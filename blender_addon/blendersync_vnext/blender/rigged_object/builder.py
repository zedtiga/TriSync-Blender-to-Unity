from __future__ import annotations

from dataclasses import asdict
from array import array
import hashlib
import math
import time

from blender.common.log import trace, warn
from blender.common.rig_axis import (
    RIG_AXIS_MODE_BAKED_JOINT_AXES,
    RIG_AXIS_MODE_PRESERVE_REST_BONE_AXES,
    apply_output_bone_axis_correction,
    build_output_bone_axis_correction,
    normalize_bone_axis_pair,
)
from blender.common.types import MeshSkinPayload, RiggedMeshPartPayload, RiggedObjectPayload
from blender.identity import ensure_mesh_asset_id_for_object, ensure_rigged_object_id, ensure_unique_asset_id
from blender.material_resource.slots import collect_material_export_snapshot, collect_materials_for_export
from blender.animation_clip.core import sample_pose_bone_transform_preserve_rest_bone_axes, sample_pose_bone_transform_unity_rig_v1
from blender.native.mesh_extractor import try_extract_skin_variable_influences_native
from blender.rigged_object.export_prep import build_export_prep_context, collect_armature_mesh_parts, dispose_export_prep_context, find_common_export_root

try:
    import bpy  # type: ignore
    from mathutils import Matrix  # type: ignore
except ImportError:
    bpy = None
    Matrix = None


STATIC_MESH_FALLBACK_BONE_ID = "bone-__blendersync_static_mesh__"
_RIG_SKELETON_BONE_CACHE: dict[tuple[int, tuple[int, ...]], list] = {}
_RIG_PART_BONE_CACHE: dict[tuple[int, int], list] = {}


def _ensure_material_asset_id(mat) -> str:
    # Keep rigged materialRefs aligned with MaterialContentV1.  Update paths
    # filter material contents down to unknown refs, so a repaired/new material
    # id is sent before Unity rebuilds the rigged prefab.
    materials = getattr(getattr(bpy, "data", None), "materials", None) if bpy is not None else None
    return ensure_unique_asset_id(mat, materials, label="material")


def _object_cache_key(obj) -> int:
    ptr = getattr(obj, "as_pointer", None)
    return int(ptr()) if callable(ptr) else int(id(obj))


def _mesh_part_cache_keys(mesh_parts) -> tuple[int, ...]:
    return tuple(_object_cache_key(part) for part in list(mesh_parts or []) if part is not None)


def _invalidate_rig_bone_analysis_cache(armature_obj) -> None:
    arm_key = _object_cache_key(armature_obj)
    for key in list(_RIG_SKELETON_BONE_CACHE.keys()):
        if key and key[0] == arm_key:
            _RIG_SKELETON_BONE_CACHE.pop(key, None)
    for key in list(_RIG_PART_BONE_CACHE.keys()):
        if key and key[0] == arm_key:
            _RIG_PART_BONE_CACHE.pop(key, None)


def _collect_weighted_deform_bone_names_by_part(armature_obj, mesh_parts, deform_names: set[str]) -> tuple[dict[int, set[str]], set[str]]:
    armature_data = getattr(armature_obj, "data", None)
    if not deform_names:
        deform_names = {
            str(getattr(bone, "name", "") or "").strip()
            for bone in list(getattr(armature_data, "bones", []) or [])
            if str(getattr(bone, "name", "") or "").strip()
        }

    by_part: dict[int, set[str]] = {}
    all_weighted: set[str] = set()
    for part in list(mesh_parts or []):
        if part is None:
            continue
        group_to_name: dict[int, str] = {}
        for vg in list(getattr(part, "vertex_groups", []) or []):
            name = str(getattr(vg, "name", "") or "").strip()
            if name and name in deform_names:
                group_to_name[int(getattr(vg, "index", -1))] = name

        weighted_names: set[str] = set()
        if group_to_name:
            mesh = getattr(part, "data", None)
            for vertex in list(getattr(mesh, "vertices", []) or []):
                for group in getattr(vertex, "groups", []) or []:
                    if float(getattr(group, "weight", 0.0) or 0.0) <= 0.0:
                        continue
                    group_index = int(getattr(group, "group", -1))
                    name = group_to_name.get(group_index)
                    if name:
                        weighted_names.add(name)

        part_key = _object_cache_key(part)
        by_part[part_key] = weighted_names
        all_weighted.update(weighted_names)
    return by_part, all_weighted


def _prepare_rig_bone_analysis(armature_obj, mesh_parts, *, reset: bool = False) -> dict:
    if armature_obj is None:
        return {"skeletonBones": [], "partBones": {}}
    if reset:
        _invalidate_rig_bone_analysis_cache(armature_obj)

    arm_key = _object_cache_key(armature_obj)
    part_keys = _mesh_part_cache_keys(mesh_parts)
    skeleton_cache_key = (arm_key, part_keys)
    cached_skeleton = _RIG_SKELETON_BONE_CACHE.get(skeleton_cache_key)
    if cached_skeleton is not None:
        return {
            "skeletonBones": cached_skeleton,
            "partBones": {
                key: value
                for key, value in _RIG_PART_BONE_CACHE.items()
                if key[0] == arm_key
            },
        }

    analyze_start = time.perf_counter()
    armature_data = getattr(armature_obj, "data", None)
    bones = list(getattr(armature_data, "bones", []) or [])
    deform_names = _collect_deform_bone_names(armature_obj)
    weighted_by_part, weighted_all = _collect_weighted_deform_bone_names_by_part(armature_obj, mesh_parts, deform_names)

    # Keep the exported skeleton complete for all deform bones, even when some
    # of them are not directly weighted on any mesh part.  Controllers and
    # intermediate deform joints still need to exist on the Unity side.
    skeleton_names = deform_names or weighted_all
    skeleton_bones = _ordered_bones_with_parent_chain(bones, skeleton_names) or bones
    _RIG_SKELETON_BONE_CACHE[skeleton_cache_key] = skeleton_bones

    part_bones_by_key = {}
    for part in list(mesh_parts or []):
        if part is None:
            continue
        part_key = _object_cache_key(part)
        part_names = weighted_by_part.get(part_key) or deform_names
        part_bones = _ordered_bones_with_parent_chain(bones, part_names) or bones
        _RIG_PART_BONE_CACHE[(arm_key, part_key)] = part_bones
        part_bones_by_key[(arm_key, part_key)] = part_bones

    trace(
        "RiggedObject",
        "bone_analysis_completed",
        lambda: "Analyzed deform bones for a rigged object.",
        lambda: {
            "armatureName": getattr(armature_obj, "name", ""),
            "meshPartCount": len(part_keys),
            "boneCount": len(bones),
            "deformBoneCount": len(deform_names),
            "weightedBoneCount": len(weighted_all),
            "skeletonBoneCount": len(skeleton_bones),
            "elapsedMs": round((time.perf_counter() - analyze_start) * 1000.0, 2),
        },
    )

    return {"skeletonBones": skeleton_bones, "partBones": part_bones_by_key}


def _iter_skin_export_bones(armature_obj, mesh_obj):
    armature_data = getattr(armature_obj, "data", None)
    bones = list(getattr(armature_data, "bones", []) or [])
    if not bones:
        return []

    if mesh_obj is not None:
        cached = _RIG_PART_BONE_CACHE.get((_object_cache_key(armature_obj), _object_cache_key(mesh_obj)))
        if cached is not None:
            return cached

    mesh_parts = []
    if mesh_obj is None:
        mesh_parts = collect_armature_mesh_parts(armature_obj)
    if mesh_obj is not None and getattr(mesh_obj, "type", None) == "MESH" and mesh_obj not in mesh_parts:
        mesh_parts.append(mesh_obj)

    used_names = _collect_deform_bone_names(armature_obj)
    if not used_names:
        used_names = _collect_weighted_deform_bone_names(armature_obj, mesh_parts)

    return _ordered_bones_with_parent_chain(bones, used_names)


def _collect_deform_bone_names(armature_obj) -> set[str]:
    armature_data = getattr(armature_obj, "data", None)
    out = set()
    for bone in list(getattr(armature_data, "bones", []) or []):
        if bool(getattr(bone, "use_deform", True)):
            name = str(getattr(bone, "name", "") or "").strip()
            if name:
                out.add(name)
    return out


def _collect_weighted_deform_bone_names(armature_obj, mesh_parts) -> set[str]:
    deform_names = _collect_deform_bone_names(armature_obj)
    _by_part, weighted_names = _collect_weighted_deform_bone_names_by_part(armature_obj, mesh_parts, deform_names)
    return weighted_names


def _ordered_bones_with_parent_chain(bones, used_names: set[str]):
    if not bones:
        return []
    if not used_names:
        return []
    export = []
    seen = set()
    for bone in bones:
        if bone.name not in used_names:
            continue
        current = bone
        while current is not None and current.name not in seen:
            seen.add(current.name)
            export.append(current)
            current = getattr(current, "parent", None)

    if not export:
        return bones

    ordered = []
    export_names = {bone.name for bone in export}
    for bone in bones:
        if bone.name in export_names:
            ordered.append(bone)
    return ordered


def _iter_export_skeleton_bones(armature_obj, mesh_parts):
    part_keys = _mesh_part_cache_keys(mesh_parts)
    cached = _RIG_SKELETON_BONE_CACHE.get((_object_cache_key(armature_obj), part_keys))
    if cached is not None:
        return cached

    armature_data = getattr(armature_obj, "data", None)
    bones = list(getattr(armature_data, "bones", []) or [])
    if not bones:
        return []
    used_names = _collect_weighted_deform_bone_names(armature_obj, mesh_parts)
    if not used_names:
        used_names = _collect_deform_bone_names(armature_obj)
    export_bones = _ordered_bones_with_parent_chain(bones, used_names)
    return export_bones or bones


def _build_ordered_bone_ids(bones):
    return [f"bone-{_export_bone_name(bone)}" for bone in bones if _export_bone_name(bone)]


def _build_skin_ordered_bone_ids(bones):
    return _build_ordered_bone_ids(bones) + [STATIC_MESH_FALLBACK_BONE_ID]


def _export_bone_name(bone) -> str:
    return str(getattr(bone, "name", bone) or "").strip()


def _export_bone_names_from_ordered_ids(ordered_bone_ids) -> list[str]:
    names = []
    for bone_id in list(ordered_bone_ids or []):
        value = str(bone_id or "").strip()
        if not value or value == STATIC_MESH_FALLBACK_BONE_ID:
            continue
        if value.startswith("bone-"):
            value = value[len("bone-"):]
        if value:
            names.append(value)
    return names


def _find_root_bone(bones):
    if not bones:
        return None
    for bone in bones:
        if getattr(bone, "parent", None) is None:
            return bone
    return bones[0]


def _build_leaf_tail_payload(bone):
    if bpy is None or Matrix is None or bone is None:
        return False, [], []

    children = list(getattr(bone, "children", []) or [])
    if children:
        return False, [], []

    try:
        tail_armature = getattr(bone, "tail_local", None)
        bone_matrix = getattr(bone, "matrix_local", None)
        if tail_armature is None or bone_matrix is None:
            return True, [], []

        tail_matrix = bone_matrix.inverted() @ Matrix.Translation(tail_armature)
        tail_position = tail_matrix.to_translation()
        return (
            True,
            [float(tail_position.x), float(tail_position.y), float(tail_position.z)],
            [float(v) for row in tail_matrix for v in row],
        )
    except Exception:
        return True, [], []



def _map_blender_vector_to_unity_rig_basis(vec):
    if vec is None:
        return None
    return vec.__class__((float(vec.x), float(vec.z), float(vec.y)))



def _map_blender_matrix_to_unity_rig_basis(local_matrix):
    if Matrix is None or local_matrix is None:
        return local_matrix
    pos = _map_blender_vector_to_unity_rig_basis(local_matrix.to_translation())
    col_x = _map_blender_vector_to_unity_rig_basis(local_matrix.to_3x3().col[0])
    col_y = _map_blender_vector_to_unity_rig_basis(local_matrix.to_3x3().col[1])
    col_z = _map_blender_vector_to_unity_rig_basis(local_matrix.to_3x3().col[2])
    rebuilt = Matrix.Identity(4)
    rebuilt.col[0][0], rebuilt.col[0][1], rebuilt.col[0][2] = float(col_x.x), float(col_x.y), float(col_x.z)
    rebuilt.col[1][0], rebuilt.col[1][1], rebuilt.col[1][2] = float(col_y.x), float(col_y.y), float(col_y.z)
    rebuilt.col[2][0], rebuilt.col[2][1], rebuilt.col[2][2] = float(col_z.x), float(col_z.y), float(col_z.z)
    rebuilt.col[3][0], rebuilt.col[3][1], rebuilt.col[3][2], rebuilt.col[3][3] = float(pos.x), float(pos.y), float(pos.z), 1.0
    return rebuilt



def _map_blender_bone_local_to_unity_rig_basis(local_matrix):
    if Matrix is None or local_matrix is None:
        return local_matrix
    return _map_blender_matrix_to_unity_rig_basis(local_matrix)



def _map_blender_armature_local_to_unity_rig_basis(local_matrix):
    if Matrix is None or local_matrix is None:
        return local_matrix
    return _map_blender_matrix_to_unity_rig_basis(local_matrix)


def _unity_rig_v1_map_position(vec):
    if vec is None:
        return [0.0, 0.0, 0.0]
    return [float(vec.x), float(vec.z), float(vec.y)]


def _unity_rig_v1_map_scale(scale):
    if scale is None:
        return [1.0, 1.0, 1.0]
    return [float(scale.x), float(scale.z), float(scale.y)]


def _unity_rig_v1_basis_matrix():
    if Matrix is None:
        return None
    return Matrix(((1.0, 0.0, 0.0), (0.0, 0.0, 1.0), (0.0, 1.0, 0.0)))


def _unity_rig_v1_map_full_matrix(local_matrix):
    if Matrix is None or local_matrix is None:
        return local_matrix
    basis3 = _unity_rig_v1_basis_matrix()
    basis = basis3.to_4x4() if hasattr(basis3, "to_4x4") else Matrix((
        (1.0, 0.0, 0.0, 0.0),
        (0.0, 0.0, 1.0, 0.0),
        (0.0, 1.0, 0.0, 0.0),
        (0.0, 0.0, 0.0, 1.0),
    ))
    return basis @ local_matrix @ basis.inverted_safe()


def _unity_rig_v1_map_quaternion(quat):
    if quat is None or Matrix is None:
        return [0.0, 0.0, 0.0, 1.0]
    basis = _unity_rig_v1_basis_matrix()
    mapped_matrix = basis @ quat.to_matrix() @ basis.inverted_safe()
    mapped = mapped_matrix.to_quaternion()
    mapped.normalize()
    return [float(mapped.x), float(mapped.y), float(mapped.z), float(mapped.w)]


def _unity_rig_v1_invert_quaternion_array(values):
    if Matrix is None or not values or len(values) < 4:
        return [0.0, 0.0, 0.0, 1.0]
    from mathutils import Quaternion  # type: ignore
    quat = Quaternion((float(values[3]), float(values[0]), float(values[1]), float(values[2])))
    quat.invert()
    quat.normalize()
    return [float(quat.x), float(quat.y), float(quat.z), float(quat.w)]



def _build_unity_rig_v1_bone_local_matrix(bone):
    if bone is None or Matrix is None:
        return None

    # unity_rig_v1 is a joint-object hierarchy contract.  Bone Transform
    # rotations stay identity; local positions are armature-space joint offsets
    # mapped by Unity from Blender axes.  Keep this simple delta form: it is the
    # tested path for correct standing pose and skin weights.
    parent_bone = getattr(bone, "parent", None)
    head_local = getattr(bone, "head_local", None)
    if head_local is None:
        return Matrix.Identity(4)

    if parent_bone is not None and getattr(parent_bone, "head_local", None) is not None:
        offset = head_local - parent_bone.head_local
    else:
        offset = head_local.copy()

    return Matrix.Translation(offset)


def _build_unity_rig_v1_preserve_rest_bone_axes_local_matrix(bone):
    if bone is None or Matrix is None:
        return None

    matrix = getattr(bone, "matrix_local", None)
    if matrix is None:
        return Matrix.Identity(4)

    parent_bone = getattr(bone, "parent", None)
    if parent_bone is not None and getattr(parent_bone, "matrix_local", None) is not None:
        return parent_bone.matrix_local.inverted_safe() @ matrix
    return matrix.copy()



def _build_unity_rig_v1_tail_local_position(bone):
    if bone is None:
        return False, [], []
    children = list(getattr(bone, "children", []) or [])
    if children:
        return False, [], []
    head_local = getattr(bone, "head_local", None)
    tail_local = getattr(bone, "tail_local", None)
    if head_local is None or tail_local is None or Matrix is None:
        return True, [], []

    tail_vec = tail_local - head_local
    tail_matrix = Matrix.Translation(tail_vec)
    return (
        True,
        [float(tail_vec.x), float(tail_vec.y), float(tail_vec.z)],
        [float(v) for row in tail_matrix for v in row],
    )


def _build_unity_rig_v1_preserve_rest_bone_axes_tail_local_position(bone):
    if bone is None:
        return False, [], []
    children = list(getattr(bone, "children", []) or [])
    if children:
        return False, [], []
    tail_local = getattr(bone, "tail_local", None)
    matrix = getattr(bone, "matrix_local", None)
    if tail_local is None or matrix is None or Matrix is None:
        return True, [], []

    tail_matrix = matrix.inverted_safe() @ Matrix.Translation(tail_local)
    tail_position = tail_matrix.to_translation()
    return (
        True,
        [float(tail_position.x), float(tail_position.y), float(tail_position.z)],
        [float(v) for row in tail_matrix for v in row],
    )


def _build_skin_gather_arrays(mesh_obj, export_bones, exported_vertex_source_indices):
    gather_start = time.perf_counter()
    mesh = getattr(mesh_obj, "data", None)
    vertices = getattr(mesh, "vertices", []) or []
    ordered_bone_ids = _build_skin_ordered_bone_ids(export_bones)
    bone_index_by_name = {name: i for i, name in enumerate(_export_bone_name(bone) for bone in export_bones) if name}
    group_index_to_bone_index = {}
    for vg in getattr(mesh_obj, "vertex_groups", []) or []:
        bone_index = bone_index_by_name.get(getattr(vg, "name", None))
        if bone_index is not None:
            group_index_to_bone_index[int(vg.index)] = int(bone_index)

    index_start = time.perf_counter()
    needed_vertex_indices = sorted({int(v) for v in (exported_vertex_source_indices or [])})
    index_ms = (time.perf_counter() - index_start) * 1000.0
    vertex_count = len(vertices)
    gathered_source_indices = array("i")
    source_vertex_offsets = array("i", [0])
    source_group_indices = array("i")
    source_group_weights = array("f")

    vertex_group_read_start = time.perf_counter()
    gather_source = "api"
    for vertex_index in needed_vertex_indices:
        if vertex_index < 0 or vertex_index >= vertex_count:
            continue
        vertex = vertices[vertex_index]
        gathered_source_indices.append(vertex_index)
        for group in getattr(vertex, "groups", []) or []:
            group_index = int(group.group)
            # Filter non-bone groups here.  This reduces Python->Rust marshal size
            # and avoids native-side work on irrelevant vertex groups.
            if group_index not in group_index_to_bone_index:
                continue
            weight = float(group.weight)
            if weight <= 0.0:
                continue
            source_group_indices.append(group_index)
            source_group_weights.append(weight)
        source_vertex_offsets.append(len(source_group_indices))
    vertex_group_read_ms = (time.perf_counter() - vertex_group_read_start) * 1000.0

    exported_pack_start = time.perf_counter()
    exported_source_indices = array("i", [int(v) for v in (exported_vertex_source_indices or [])])
    exported_pack_ms = (time.perf_counter() - exported_pack_start) * 1000.0

    gather_ms = (time.perf_counter() - gather_start) * 1000.0
    return {
        "orderedBoneIds": ordered_bone_ids,
        "fallbackBoneIndex": len(ordered_bone_ids) - 1,
        "groupIndexToBoneIndex": group_index_to_bone_index,
        "neededSourceVertexIndices": gathered_source_indices,
        "sourceVertexOffsets": source_vertex_offsets,
        "sourceGroupIndices": source_group_indices,
        "sourceGroupWeights": source_group_weights,
        "exportedVertexSourceIndices": exported_source_indices,
        "gatherMs": gather_ms,
        "gatherSource": gather_source,
        "gatherIndexMs": index_ms,
        "gatherVertexGroupReadMs": vertex_group_read_ms,
        "gatherExportedPackMs": exported_pack_ms,
    }


def _build_skin_arrays_python_from_gather(gather: dict):
    emit_start = time.perf_counter()
    ordered_bone_ids = gather.get("orderedBoneIds") or []
    group_index_to_bone_index = gather.get("groupIndexToBoneIndex") or {}
    needed_source_indices = gather.get("neededSourceVertexIndices") or []
    offsets = gather.get("sourceVertexOffsets") or []
    group_indices = gather.get("sourceGroupIndices") or []
    group_weights = gather.get("sourceGroupWeights") or []
    exported = gather.get("exportedVertexSourceIndices") or []

    fallback_bone_index = int(gather.get("fallbackBoneIndex", -1))
    default_influences = ((fallback_bone_index, 1.0),) if fallback_bone_index >= 0 else ()
    influences_by_vertex_index = {}
    for row, vertex_index in enumerate(needed_source_indices):
        start = int(offsets[row])
        end = int(offsets[row + 1])
        raw_influences = []
        weight_sum = 0.0
        for i in range(start, end):
            bone_index = group_index_to_bone_index.get(int(group_indices[i]))
            if bone_index is None:
                continue
            weight = float(group_weights[i])
            if weight <= 0.0:
                continue
            raw_influences.append((int(bone_index), weight))
            weight_sum += weight
        if not raw_influences:
            influences_by_vertex_index[int(vertex_index)] = default_influences
            continue
        raw_influences.sort(key=lambda item: item[1], reverse=True)
        inv_weight_sum = 1.0 / (weight_sum or 1.0)
        influences_by_vertex_index[int(vertex_index)] = tuple((bone_index, float(weight * inv_weight_sum)) for bone_index, weight in raw_influences)

    bones_per_vertex = []
    bone_indices = []
    bone_weights = []
    for source_vertex_index in exported:
        influences = influences_by_vertex_index.get(int(source_vertex_index), default_influences)
        bones_per_vertex.append(len(influences))
        for bone_index, weight in influences:
            bone_indices.append(bone_index)
            bone_weights.append(weight)

    emit_ms = (time.perf_counter() - emit_start) * 1000.0
    return bones_per_vertex, bone_indices, bone_weights, emit_ms


def _weights_close(a, b, tolerance: float = 1e-5) -> bool:
    if len(a) != len(b):
        return False
    for av, bv in zip(a, b):
        if abs(float(av) - float(bv)) > tolerance:
            return False
    return True

def _update_skin_fingerprint_sequence(hasher, label: str, values, typecode: str) -> None:
    hasher.update(label.encode("utf-8"))
    hasher.update(b"|")
    if isinstance(values, array):
        hasher.update(str(len(values)).encode("utf-8"))
        hasher.update(b"|")
        hasher.update(values.typecode.encode("ascii", errors="ignore"))
        hasher.update(b":")
        hasher.update(values.tobytes())
        return
    seq = values if isinstance(values, list) else list(values or [])
    hasher.update(str(len(seq)).encode("utf-8"))
    hasher.update(b"|")
    if not seq:
        return
    arr = array(typecode)
    if typecode == "f":
        arr.fromlist([float(v) for v in seq])
    else:
        arr.fromlist([int(v) for v in seq])
    hasher.update(typecode.encode("ascii", errors="ignore"))
    hasher.update(b":")
    hasher.update(arr.tobytes())


def _compute_skin_payload_fingerprint(
    *,
    bone_count: int,
    bind_poses=None,
    bones_per_vertex=None,
    bone_indices=None,
    bone_weights=None,
    ordered_bone_ids=None,
    source_fingerprint: str | None = None,
) -> str:
    hasher = hashlib.sha1()
    for key, value in (
        ("isSkinned", True),
        ("skinEncoding", "variable"),
        ("spaceSemantic", "unity_rig_v1"),
        ("boneCount", int(bone_count)),
    ):
        hasher.update(str(key).encode("utf-8"))
        hasher.update(b"=")
        hasher.update(str(value).encode("utf-8"))
        hasher.update(b";")
    for bone_id in list(ordered_bone_ids or []):
        hasher.update(b"orderedBoneId|")
        hasher.update(str(bone_id or "").encode("utf-8"))
        hasher.update(b";")
    if source_fingerprint:
        hasher.update(b"nativeSourceFingerprint=")
        hasher.update(str(source_fingerprint).encode("utf-8"))
        hasher.update(b";")
    else:
        _update_skin_fingerprint_sequence(hasher, "bindPoses", bind_poses or [], "f")
        _update_skin_fingerprint_sequence(hasher, "bonesPerVertex", bones_per_vertex or [], "i")
        _update_skin_fingerprint_sequence(hasher, "boneIndices", bone_indices or [], "i")
        _update_skin_fingerprint_sequence(hasher, "boneWeights", bone_weights or [], "f")
    return hasher.hexdigest()


def _extract_variable_influences(mesh_obj, export_bones, exported_vertex_source_indices):
    total_start = time.perf_counter()
    gather = _build_skin_gather_arrays(mesh_obj, export_bones, exported_vertex_source_indices)
    validate_native = str(__import__("os").environ.get("BLENDERSYNC_NATIVE_SKIN_VALIDATE", "0")).strip().lower() in {"1", "true", "yes", "on"}

    native_start = time.perf_counter()
    native_result = try_extract_skin_variable_influences_native(gather=gather)
    native_call_ms = (time.perf_counter() - native_start) * 1000.0

    if native_result is not None and not validate_native:
        bones_per_vertex = [int(v) for v in (native_result.get("bonesPerVertex") or [])]
        bone_indices = [int(v) for v in (native_result.get("boneIndices") or [])]
        bone_weights = [float(v) for v in (native_result.get("boneWeights") or [])]
        profile = native_result.get("profile") or {}
        total_ms = (time.perf_counter() - total_start) * 1000.0
        trace(
            "RiggedObject",
            "skin_built_native",
            lambda: "Built skin weights with the native extractor.",
            lambda: {
                "objectName": getattr(mesh_obj, "name", ""),
                "neededVertexCount": len(gather.get("neededSourceVertexIndices") or []),
                "exportedVertexCount": len(gather.get("exportedVertexSourceIndices") or []),
                "groupCount": len(gather.get("sourceGroupIndices") or []),
                "gatherSource": gather.get("gatherSource") or "api",
                "gatherMs": round(float(gather.get("gatherMs") or 0.0), 2),
                "nativeCallMs": round(native_call_ms, 2),
                "nativeTotalMs": round(float(profile.get("totalMs") or 0.0), 2),
                "sourceFingerprintMs": round(float(native_result.get("sourceFingerprintMs") or 0.0), 2),
                "totalMs": round(total_ms, 2),
            },
        )
        return bones_per_vertex, bone_indices, bone_weights, native_result.get("sourceFingerprint")

    bones_per_vertex, bone_indices, bone_weights, emit_ms = _build_skin_arrays_python_from_gather(gather)

    if native_result is not None and validate_native:
        native_bpv = [int(v) for v in (native_result.get("bonesPerVertex") or [])]
        native_bi = [int(v) for v in (native_result.get("boneIndices") or [])]
        native_bw = [float(v) for v in (native_result.get("boneWeights") or [])]
        validation_ok = native_bpv == bones_per_vertex and native_bi == bone_indices and _weights_close(native_bw, bone_weights)
        profile = native_result.get("profile") or {}
        validation_fields = {
            "validationOk": validation_ok,
            "nativeTotalMs": round(float(profile.get("totalMs") or 0.0), 2),
            "nativeCallMs": round(native_call_ms, 2),
            "bonesPerVertexCount": len(bones_per_vertex),
            "weightCount": len(bone_weights),
        }
        if validation_ok:
            trace(
                "RiggedObject",
                "native_skin_validation_completed",
                lambda: "Validated native skin extraction against the Python result.",
                lambda: validation_fields,
            )
        else:
            warn(
                "RiggedObject",
                "native_skin_validation_failed",
                "Native skin extraction differed from the Python reference.",
                validation_fields,
            )

    total_ms = (time.perf_counter() - total_start) * 1000.0
    trace(
        "RiggedObject",
        "skin_built_python",
        lambda: "Built skin weights with the Python extractor.",
        lambda: {
            "objectName": getattr(mesh_obj, "name", ""),
            "neededVertexCount": len(gather.get("neededSourceVertexIndices") or []),
            "exportedVertexCount": len(gather.get("exportedVertexSourceIndices") or []),
            "groupCount": len(gather.get("sourceGroupIndices") or []),
            "gatherSource": gather.get("gatherSource") or "api",
            "gatherMs": round(float(gather.get("gatherMs") or 0.0), 2),
            "emitMs": round(emit_ms, 2),
            "totalMs": round(total_ms, 2),
        },
    )
    return bones_per_vertex, bone_indices, bone_weights, None


def _collect_material_refs(obj, *, mesh=None, evaluated_mesh: bool = False, materials=None) -> list[str]:
    material_refs = []
    if materials is None:
        materials = collect_materials_for_export(obj, mesh=mesh, evaluated_mesh=evaluated_mesh)
    for mat in materials:
        material_refs.append(f"mat-{_ensure_material_asset_id(mat)}" if mat is not None else "")
    return material_refs



def _visibility_state_for_object(obj) -> str:
    if obj is None:
        return "visible"
    try:
        visible_get = getattr(obj, "visible_get", None)
        if callable(visible_get):
            return "visible" if bool(visible_get()) else "hidden"
    except Exception:
        pass
    try:
        if bool(getattr(obj, "hide_viewport", False)) or bool(getattr(obj, "hide_render", False)):
            return "hidden"
    except Exception:
        pass
    return "visible"


def build_unity_rig_v1_payload(obj, *, material_snapshots_by_mesh_ref: dict | None = None) -> dict | None:
    if bpy is None or obj is None:
        return None

    scene = getattr(bpy.context, "scene", None) if getattr(bpy, "context", None) is not None else None
    export_leaf_bones = bool(getattr(scene, "blendersync_rig_export_leaf_bones", True))
    rig_axis_mode = str(getattr(scene, "blendersync_rig_axis_mode", RIG_AXIS_MODE_BAKED_JOINT_AXES) or RIG_AXIS_MODE_BAKED_JOINT_AXES)
    if rig_axis_mode not in {RIG_AXIS_MODE_BAKED_JOINT_AXES, RIG_AXIS_MODE_PRESERVE_REST_BONE_AXES}:
        rig_axis_mode = RIG_AXIS_MODE_BAKED_JOINT_AXES
    primary_bone_axis, secondary_bone_axis = normalize_bone_axis_pair(
        getattr(scene, "blendersync_rig_primary_bone_axis", None),
        getattr(scene, "blendersync_rig_secondary_bone_axis", None),
    )
    axis_correction, axis_correction_inv = build_output_bone_axis_correction(primary_bone_axis, secondary_bone_axis)

    prep = build_export_prep_context(obj)
    if not prep:
        return None

    try:
        source_armature = prep["armature"]
        source_mesh_parts = list(prep.get("meshParts") or [])
        source_export_root = prep.get("exportRoot") or source_armature
        prepared_armature = prep.get("preparedArmature") or source_armature
        prepared_mesh_parts = list(prep.get("preparedMeshParts") or []) or source_mesh_parts
        prepared_export_root = prep.get("preparedRoot") or source_export_root
        if not prepared_mesh_parts:
            return None

        _prepare_rig_bone_analysis(prepared_armature, prepared_mesh_parts, reset=True)
        bones = _iter_export_skeleton_bones(prepared_armature, prepared_mesh_parts)
        if not bones:
            return None

        root_bone = _find_root_bone(bones)
        ordered_bone_ids = _build_ordered_bone_ids(bones)
        root_object_name = getattr(source_armature, "name", None) or getattr(source_mesh_parts[0], "name", None) or "rig"
        material_refs_by_part_key = {}
        material_snapshots_by_mesh_ref = material_snapshots_by_mesh_ref if material_snapshots_by_mesh_ref is not None else {}
        for source_mesh_obj in source_mesh_parts:
            mesh_ref = f"mesh-{ensure_mesh_asset_id_for_object(source_mesh_obj)}"
            snapshot = material_snapshots_by_mesh_ref.get(mesh_ref)
            if snapshot is None:
                snapshot = collect_material_export_snapshot(
                    source_mesh_obj,
                    getattr(source_mesh_obj, "data", None),
                    geometry_is_evaluated=False,
                    allow_topology_compatible_evaluated=True,
                )
                material_snapshots_by_mesh_ref[mesh_ref] = snapshot
            material_refs_by_part_key[_object_cache_key(source_mesh_obj)] = _collect_material_refs(
                source_mesh_obj,
                materials=snapshot.materials,
            )
        primary_material_refs = material_refs_by_part_key.get(_object_cache_key(source_mesh_parts[0]), [])
        payload = RiggedObjectPayload(
            riggedObjectId=ensure_rigged_object_id(source_armature),
            objectName=root_object_name,
            spaceSemantic="unity_rig_v1",
            rigAxisMode=rig_axis_mode,
            primaryBoneAxis=primary_bone_axis,
            secondaryBoneAxis=secondary_bone_axis,
            sourceObjectPath=f"Object/{root_object_name}",
            sourceArmatureObjectPath=f"Object/{source_armature.name}",
            meshRef=f"mesh-{ensure_mesh_asset_id_for_object(source_mesh_parts[0])}",
            visibilityState=_visibility_state_for_object(source_export_root),
            materialRefs=list(primary_material_refs),
            rootBoneId=f"bone-{root_bone.name}" if root_bone is not None else "",
            orderedBoneIds=ordered_bone_ids,
        )

        source_root_world = source_export_root.matrix_world.copy() if getattr(source_export_root, "matrix_world", None) is not None else source_armature.matrix_world.copy()
        source_armature_world = source_armature.matrix_world.copy()
        root_world_inv = source_root_world.inverted_safe()
        armature_local_matrix = root_world_inv @ source_armature_world

        mapped_root_rotation = _unity_rig_v1_map_quaternion(source_root_world.to_quaternion())
        payload.rigRootLocalPosition = _unity_rig_v1_map_position(source_root_world.to_translation())
        payload.rigRootLocalRotation = list(mapped_root_rotation)
        payload.rigRootLocalScale = _unity_rig_v1_map_scale(source_root_world.to_scale())

        # Keep the mapped Blender->Unity object rotation on the visible rig root
        # instead of zeroing it out.  Bones remain a joint-offset hierarchy under
        # the armature node; do not add inverse child compensation here, because
        # that would hide the mapped object rotation and make the exported rig's
        # visual facing diverge from Blender.
        payload.armatureLocalPosition = _unity_rig_v1_map_position(armature_local_matrix.to_translation())
        payload.armatureLocalRotation = [0.0, 0.0, 0.0, 1.0]
        payload.armatureLocalScale = _unity_rig_v1_map_scale(armature_local_matrix.to_scale())

        payload.meshParts = []
        mesh_part_ordered_bone_ids_by_ref = {}
        for source_mesh_obj, prepared_mesh_obj in zip(source_mesh_parts, prepared_mesh_parts):
            # Must match build_mesh_skin_payload() / _extract_variable_influences()
            # exactly.  Vertex boneIndex values are encoded in this per-mesh
            # skin-export-bone order, not necessarily in full skeleton order.
            part_bones = _iter_skin_export_bones(prepared_armature, prepared_mesh_obj)
            part_ordered_bone_ids = _build_skin_ordered_bone_ids(part_bones)
            mesh_local_matrix = source_armature_world.inverted_safe() @ source_mesh_obj.matrix_world.copy()
            mesh_ref = f"mesh-{ensure_mesh_asset_id_for_object(source_mesh_obj)}"
            mesh_part_ordered_bone_ids_by_ref[mesh_ref] = list(part_ordered_bone_ids)
            payload.meshParts.append(RiggedMeshPartPayload(
                objectName=source_mesh_obj.name,
                sourceObjectPath=f"Object/{source_mesh_obj.name}",
                meshRef=mesh_ref,
                visibilityState=_visibility_state_for_object(source_mesh_obj),
                materialRefs=list(material_refs_by_part_key.get(_object_cache_key(source_mesh_obj), [])),
                orderedBoneIds=part_ordered_bone_ids,
                localPosition=_unity_rig_v1_map_position(mesh_local_matrix.to_translation()),
                localRotation=[0.0, 0.0, 0.0, 1.0],
                localScale=_unity_rig_v1_map_scale(mesh_local_matrix.to_scale()),
            ))

        payload.skeleton.bones = []
        for bone in bones:
            bone_id = f"bone-{bone.name}"
            parent_bone = getattr(bone, "parent", None)
            parent_bone_id = f"bone-{parent_bone.name}" if parent_bone is not None else None
            if rig_axis_mode == RIG_AXIS_MODE_PRESERVE_REST_BONE_AXES:
                local_matrix = _unity_rig_v1_map_full_matrix(_build_unity_rig_v1_preserve_rest_bone_axes_local_matrix(bone))
                local_matrix = apply_output_bone_axis_correction(
                    local_matrix,
                    has_parent=parent_bone is not None,
                    correction=axis_correction,
                    correction_inv=axis_correction_inv,
                )
            else:
                local_matrix = _build_unity_rig_v1_bone_local_matrix(bone)
            local_position = [float(local_matrix.to_translation().x), float(local_matrix.to_translation().y), float(local_matrix.to_translation().z)]
            local_rotation = [float(local_matrix.to_quaternion().x), float(local_matrix.to_quaternion().y), float(local_matrix.to_quaternion().z), float(local_matrix.to_quaternion().w)]
            local_scale = [float(local_matrix.to_scale().x), float(local_matrix.to_scale().y), float(local_matrix.to_scale().z)]
            local_matrix_values = [float(v) for row in local_matrix for v in row]
            if rig_axis_mode == RIG_AXIS_MODE_PRESERVE_REST_BONE_AXES:
                is_leaf, tail_local_position, tail_local_matrix = _build_unity_rig_v1_preserve_rest_bone_axes_tail_local_position(bone)
                if tail_local_matrix and len(tail_local_matrix) >= 16 and Matrix is not None:
                    tail_matrix_unity = _unity_rig_v1_map_full_matrix(Matrix((
                        tail_local_matrix[0:4],
                        tail_local_matrix[4:8],
                        tail_local_matrix[8:12],
                        tail_local_matrix[12:16],
                    )))
                    if axis_correction_inv is not None:
                        tail_matrix_unity = axis_correction_inv @ tail_matrix_unity
                    tail_position_unity = tail_matrix_unity.to_translation()
                    tail_local_position = [float(tail_position_unity.x), float(tail_position_unity.y), float(tail_position_unity.z)]
                    tail_local_matrix = [float(v) for row in tail_matrix_unity for v in row]
            else:
                is_leaf, tail_local_position, tail_local_matrix = _build_unity_rig_v1_tail_local_position(bone)
            is_leaf = bool(is_leaf and export_leaf_bones)
            payload.skeleton.bones.append(
                {
                    "boneId": bone_id,
                    "name": bone.name,
                    "parentBoneId": parent_bone_id,
                    "localPosition": list(local_position),
                    "localRotation": list(local_rotation),
                    "localScale": list(local_scale),
                    "localMatrix": list(local_matrix_values),
                    "restLocalPosition": list(local_position),
                    "restLocalRotation": list(local_rotation),
                    "restLocalScale": list(local_scale),
                    "restLocalMatrix": list(local_matrix_values),
                    "isLeaf": bool(is_leaf),
                    "tailLocalPosition": list(tail_local_position),
                    "tailLocalMatrix": list(tail_local_matrix),
                }
            )

        return {
            "type": "unity_rig_v1",
            "timestamp": 0,
            "riggedObject": asdict(payload),
            "_meshPartOrderedBoneIdsByRef": mesh_part_ordered_bone_ids_by_ref,
        }
    finally:
        dispose_export_prep_context(prep)


def build_unity_rig_v1_pose_sync_payload(context, obj, mode: str = "current_pose") -> dict | None:
    if bpy is None or obj is None:
        return None

    mode = str(mode or "current_pose").strip() or "current_pose"
    if mode not in {"current_pose", "restore_static_pose"}:
        raise ValueError(f"unsupported_rigged_pose_mode:{mode}")

    scene = getattr(bpy.context, "scene", None) if getattr(bpy, "context", None) is not None else None
    rig_axis_mode = str(getattr(scene, "blendersync_rig_axis_mode", RIG_AXIS_MODE_BAKED_JOINT_AXES) or RIG_AXIS_MODE_BAKED_JOINT_AXES)
    if rig_axis_mode not in {RIG_AXIS_MODE_BAKED_JOINT_AXES, RIG_AXIS_MODE_PRESERVE_REST_BONE_AXES}:
        rig_axis_mode = RIG_AXIS_MODE_BAKED_JOINT_AXES
    primary_bone_axis, secondary_bone_axis = normalize_bone_axis_pair(
        getattr(scene, "blendersync_rig_primary_bone_axis", None),
        getattr(scene, "blendersync_rig_secondary_bone_axis", None),
    )
    axis_correction, axis_correction_inv = build_output_bone_axis_correction(primary_bone_axis, secondary_bone_axis)

    prep = build_export_prep_context(obj)
    if not prep:
        return None

    try:
        source_armature = prep["armature"]
        source_mesh_parts = list(prep.get("meshParts") or [])
        if not source_mesh_parts:
            return None

        bones = []
        ordered_bone_ids = []
        if mode == "current_pose":
            _prepare_rig_bone_analysis(source_armature, source_mesh_parts, reset=True)
            export_bones = _iter_export_skeleton_bones(source_armature, source_mesh_parts)
            ordered_bone_ids = _build_ordered_bone_ids(export_bones)
            if not ordered_bone_ids:
                return None

            depsgraph = context.evaluated_depsgraph_get() if context is not None else bpy.context.evaluated_depsgraph_get()
            depsgraph.update()
            eval_armature = source_armature.evaluated_get(depsgraph)
            pose_bones = getattr(getattr(eval_armature, "pose", None), "bones", None)
            if pose_bones is None:
                return None

            for source_bone in export_bones:
                bone_name = _export_bone_name(source_bone)
                if not bone_name:
                    continue
                pose_bone = pose_bones.get(bone_name) if hasattr(pose_bones, "get") else None
                if pose_bone is None:
                    continue
                if rig_axis_mode == RIG_AXIS_MODE_PRESERVE_REST_BONE_AXES:
                    local_position, local_rotation, local_scale = sample_pose_bone_transform_preserve_rest_bone_axes(pose_bone, axis_correction, axis_correction_inv)
                else:
                    local_position, local_rotation, local_scale = sample_pose_bone_transform_unity_rig_v1(pose_bone)
                bones.append({
                    "boneId": f"bone-{bone_name}",
                    "name": bone_name,
                    "localPosition": [float(local_position[0]), float(local_position[1]), float(local_position[2])],
                    "localRotation": [
                        float(local_rotation.x),
                        float(local_rotation.y),
                        float(local_rotation.z),
                        float(local_rotation.w),
                    ],
                    "localScale": [float(local_scale[0]), float(local_scale[1]), float(local_scale[2])],
                })

        return {
            "type": "asset_bridge.rigged_pose_v1",
            "version": 1,
            "timestamp": int(time.time()),
            "mode": mode,
            "riggedObjectId": ensure_rigged_object_id(source_armature),
            "objectName": str(getattr(source_armature, "name", "") or ""),
            "spaceSemantic": "unity_rig_v1",
            "rigAxisMode": rig_axis_mode,
            "primaryBoneAxis": primary_bone_axis,
            "secondaryBoneAxis": secondary_bone_axis,
            "orderedBoneIds": ordered_bone_ids,
            "bones": bones,
        }
    finally:
        dispose_export_prep_context(prep)


def build_unity_rig_v1_blendshape_weights_payload(context, obj) -> dict | None:
    """Build a lightweight current-weight snapshot for every bound skin part."""
    if bpy is None or context is None or obj is None or getattr(obj, "type", None) != "ARMATURE":
        return None

    depsgraph_getter = getattr(context, "evaluated_depsgraph_get", None)
    if callable(depsgraph_getter):
        depsgraph = depsgraph_getter()
        updater = getattr(depsgraph, "update", None)
        if callable(updater):
            updater()

    parts = []
    for mesh_obj in collect_armature_mesh_parts(obj):
        mesh = getattr(mesh_obj, "data", None)
        shape_keys = getattr(mesh, "shape_keys", None) if mesh is not None else None
        key_blocks = list(getattr(shape_keys, "key_blocks", []) or []) if shape_keys is not None else []
        if len(key_blocks) <= 1:
            continue

        weights = []
        for index, block in enumerate(key_blocks):
            if index == 0:
                continue
            name = str(getattr(block, "name", "") or "").strip()
            if not name:
                continue
            value = float(getattr(block, "value", 0.0) or 0.0)
            if not math.isfinite(value):
                raise ValueError(f"shape_key_weight_not_finite:{mesh_obj.name}:{name}")
            weights.append(
                {
                    "name": name,
                    # Keep Blender's key-block index; Unity subtracts Basis.
                    "index": int(index),
                    "value": value,
                    "weight": value * 100.0,
                }
            )
        if not weights:
            continue
        parts.append(
            {
                "objectName": str(getattr(mesh_obj, "name", "") or ""),
                "meshRef": f"mesh-{ensure_mesh_asset_id_for_object(mesh_obj)}",
                "weights": weights,
            }
        )

    if not parts:
        return None
    return {
        "type": "asset_bridge.rigged_blendshape_weights_v1",
        "version": 1,
        "timestamp": int(time.time()),
        "riggedObjectId": ensure_rigged_object_id(obj),
        "objectName": str(getattr(obj, "name", "") or ""),
        "spaceSemantic": "unity_rig_v1",
        "parts": parts,
    }



def build_mesh_skin_payload(obj, exported_vertex_source_indices: list[int], ordered_bone_ids_override: list[str] | None = None) -> dict | None:
    total_start = time.perf_counter()
    if bpy is None or obj is None or getattr(obj, "type", None) != "MESH" or getattr(obj, "data", None) is None:
        return None

    armature_start = time.perf_counter()
    armature = getattr(obj, "find_armature", lambda: None)()
    armature_ms = (time.perf_counter() - armature_start) * 1000.0
    if armature is None:
        return None

    bones_start = time.perf_counter()
    export_bones = _export_bone_names_from_ordered_ids(ordered_bone_ids_override) if ordered_bone_ids_override else _iter_skin_export_bones(armature, obj)
    bones_ms = (time.perf_counter() - bones_start) * 1000.0
    if not export_bones:
        return None

    influences_start = time.perf_counter()
    bones_per_vertex, bone_indices, bone_weights, native_skin_source_fingerprint = _extract_variable_influences(obj, export_bones, exported_vertex_source_indices)
    influences_ms = (time.perf_counter() - influences_start) * 1000.0
    # unity_rig_v1 uses current Unity bone transforms to rebuild bindposes in
    # RiggedPrefabBuilder.  Skin payload only needs bone count and per-vertex
    # influence arrays; keep legacy exporter-like matrix slots empty so no old
    # cluster/bind-pose path can take over again.
    bindpose_start = time.perf_counter()
    identity_bindposes = []
    skin_bone_count = len(export_bones) + 1
    for _ in range(skin_bone_count):
        identity_bindposes.extend([
            1.0, 0.0, 0.0, 0.0,
            0.0, 1.0, 0.0, 0.0,
            0.0, 0.0, 1.0, 0.0,
            0.0, 0.0, 0.0, 1.0,
        ])
    bindpose_ms = (time.perf_counter() - bindpose_start) * 1000.0

    fingerprint_start = time.perf_counter()
    ordered_bone_ids = _build_skin_ordered_bone_ids(export_bones)
    skin_fingerprint = _compute_skin_payload_fingerprint(
        bone_count=skin_bone_count,
        bind_poses=identity_bindposes,
        bones_per_vertex=bones_per_vertex,
        bone_indices=bone_indices,
        bone_weights=bone_weights,
        ordered_bone_ids=ordered_bone_ids,
        source_fingerprint=native_skin_source_fingerprint,
    )
    fingerprint_ms = (time.perf_counter() - fingerprint_start) * 1000.0

    asdict_start = time.perf_counter()
    # Avoid dataclasses.asdict() here: it deep-copies large influence arrays and
    # was measured at ~140ms for Alpha_Surface.  The payload is already plain
    # JSON-compatible lists, so a direct dict preserves semantics without copy.
    result = {
        "isSkinned": True,
        "skinEncoding": "variable",
        "spaceSemantic": "unity_rig_v1",
        "boneCount": skin_bone_count,
        "bindPoses": identity_bindposes,
        "bonesPerVertex": bones_per_vertex,
        "boneIndices": bone_indices,
        "boneWeights": bone_weights,
        "meshWorldMatrix": [],
        "armatureWorldMatrix": [],
        "clusterTransforms": [],
        "clusterTransformLinks": [],
        "clusterAssociateModel": [],
        "_fingerprint": skin_fingerprint,
    }
    asdict_ms = (time.perf_counter() - asdict_start) * 1000.0
    total_ms = (time.perf_counter() - total_start) * 1000.0
    trace(
        "RiggedObject",
        "skin_payload_built",
        lambda: "Built a rigged-object skin payload.",
        lambda: {
            "objectName": getattr(obj, "name", ""),
            "boneCount": len(export_bones),
            "exportedVertexCount": len(exported_vertex_source_indices or []),
            "armatureMs": round(armature_ms, 2),
            "bonesMs": round(bones_ms, 2),
            "influencesMs": round(influences_ms, 2),
            "bindPoseMs": round(bindpose_ms, 2),
            "fingerprintMs": round(fingerprint_ms, 2),
            "payloadMs": round(asdict_ms, 2),
            "totalMs": round(total_ms, 2),
        },
    )
    return result
