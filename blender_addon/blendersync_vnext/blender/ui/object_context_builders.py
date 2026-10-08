from __future__ import annotations

import hashlib
import json
import os
import time
import uuid
from array import array
from collections import OrderedDict
from contextlib import contextmanager

from blender.asset_registry import is_asset_known
from blender.common.blend_shapes import build_mesh_blend_shapes as _build_mesh_blend_shapes
from blender.common.evaluated_mesh import evaluated_mesh_for_sync
from blender.common.log import trace, warn
from blender.identity import ensure_instance_id, ensure_mesh_asset_id_for_object, ensure_rigged_object_id, ensure_unique_asset_id
from blender.material_resource.content_v1 import build_material_content_v1
from blender.material_resource.slots import collect_material_export_snapshot, collect_materials_for_export
from blender.object_classification import classify_scene_object, is_supported_scene_object, summarize_unsupported
from blender.native.mesh_extractor import (
    NATIVE_MULTI_UV_CAPABILITY,
    build_reference_hash_profile,
    get_native_status,
    native_supports,
    try_build_material_submeshes_native,
    try_extract_mesh_arrays_native,
    try_gather_uv0_native,
)
from blender.rigged_object import build_mesh_skin_payload, build_unity_rig_v1_payload
from blender.rigged_object.export_prep import collect_armature_mesh_parts

try:
    import bpy  # type: ignore
except ImportError:
    bpy = None

AUTO_SYNC_READY_KEY = "blendersync_auto_sync_ready"
_last_mesh_array_profile: dict = {}
_last_submesh_profile: dict = {}


def _format_mesh_detail_profile(object_name: str, loop_build_mode: str) -> str:
    mesh_array_profile = dict(_last_mesh_array_profile or {})
    submesh_profile = dict(_last_submesh_profile or {})
    return (
        "[vNext][MeshBuildDetailProfile] "
        f"object={object_name} mode={loop_build_mode} "
        f"rawMs={float(mesh_array_profile.get('rawMs') or 0.0):.2f} "
        f"nativeCallMs={float(mesh_array_profile.get('nativeCallMs') or 0.0):.2f} nativeTotalMs={mesh_array_profile.get('nativeTotalMs')} "
        f"nativeDedupeMs={mesh_array_profile.get('nativeDedupeMs')} nativeHashMs={mesh_array_profile.get('nativeHashMs')} "
        f"uvBuildMs={float(mesh_array_profile.get('uvChannelsBuildMs') or 0.0):.2f} colorBuildMs={float(mesh_array_profile.get('colorBuildMs') or 0.0):.2f} "
        f"pythonDedupeMs={float(mesh_array_profile.get('pythonDedupeMs') or 0.0):.2f} "
        f"submeshMaterialReadMs={float(submesh_profile.get('materialReadMs') or 0.0):.2f} "
        f"submeshNativeMs={float(submesh_profile.get('nativeMs') or 0.0):.2f} "
        f"submeshGroupMs={float(submesh_profile.get('groupMs') or 0.0):.2f} "
        f"submeshHashMs={float(submesh_profile.get('hashMs') or 0.0):.2f} "
        f"submeshTotalMs={float(submesh_profile.get('totalMs') or 0.0):.2f}"
    )


def _slug(value: str) -> str:
    safe = "".join(ch.lower() if ch.isalnum() else "-" for ch in (value or "unnamed"))
    while "--" in safe:
        safe = safe.replace("--", "-")
    return safe.strip("-") or "unnamed"


def _new_correlation_id(prefix: str) -> str:
    return f"{prefix}-{int(time.time() * 1000)}-{uuid.uuid4().hex[:8]}"


def _ensure_material_asset_id(mat) -> str:
    materials = getattr(getattr(bpy, "data", None), "materials", None) if bpy is not None else None
    return ensure_unique_asset_id(mat, materials, label="material")


def _build_export_material_payloads(
    obj,
    mesh,
    *,
    evaluated_mesh: bool,
    material_contents_unknown_only: bool = False,
    materials=None,
) -> tuple[list[str], list[dict]]:
    if materials is None:
        if evaluated_mesh:
            materials = collect_material_export_snapshot(
                obj,
                mesh,
                geometry_is_evaluated=True,
            ).materials
        else:
            materials = collect_materials_for_export(obj, mesh=mesh, evaluated_mesh=False)
    material_refs = []
    material_contents = []
    seen_material_refs = set()

    for mat in materials:
        if mat is None:
            material_refs.append("")
            continue
        material_refs.append(f"mat-{_ensure_material_asset_id(mat)}")

    for mat in materials:
        if mat is None:
            continue
        mat_ref = f"mat-{_ensure_material_asset_id(mat)}"
        if mat_ref in seen_material_refs:
            continue
        seen_material_refs.add(mat_ref)
        if material_contents_unknown_only and is_asset_known(mat_ref):
            continue
        try:
            material_contents.append(build_material_content_v1(mat))
        except Exception as exc:
            warn(
                "ObjectContext",
                "material_content_build_failed",
                str(exc),
                {
                    "objectName": getattr(obj, "name", "<unnamed>"),
                    "materialName": getattr(mat, "name", "<unnamed>"),
                },
            )
    return material_refs, material_contents


def _update_hash_sequence(hasher, label: str, values, typecode: str | None = None) -> None:
    """Hash large numeric payloads without per-item string formatting.

    The previous implementation converted every skin weight/index into text before
    hashing.  For large skinned meshes that made `fpMs` show up as a real cost.
    Binary array hashing keeps the same duplicate-detection role while avoiding
    Python string churn.
    """
    hasher.update(str(label).encode("utf-8"))
    hasher.update(b"|")
    if values is None:
        hasher.update(b"0|")
        return
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
    if typecode is None:
        typecode = "i" if all(isinstance(v, int) and not isinstance(v, bool) for v in seq[: min(16, len(seq))]) else "f"
    try:
        arr = array(typecode)
        arr.fromlist([int(v) if typecode in {"i", "I", "l", "L"} else float(v) for v in seq])
        hasher.update(typecode.encode("ascii", errors="ignore"))
        hasher.update(b":")
        hasher.update(arr.tobytes())
    except Exception:
        # Rare non-numeric fallback; keep deterministic but don't penalize the
        # common huge numeric arrays.
        for value in seq:
            hasher.update(str(value).encode("utf-8"))
            hasher.update(b",")


def _compute_skin_fingerprint(skin_payload: dict | None) -> str:
    if not skin_payload:
        return "noskin"
    cached = skin_payload.get("fingerprint") or skin_payload.get("_fingerprint")
    if cached:
        return str(cached)
    hasher = hashlib.sha1()
    for key in ("isSkinned", "skinEncoding", "spaceSemantic", "boneCount"):
        hasher.update(str(key).encode("utf-8"))
        hasher.update(b"=")
        hasher.update(str(skin_payload.get(key)).encode("utf-8"))
        hasher.update(b";")
    _update_hash_sequence(hasher, "bindPoses", skin_payload.get("bindPoses") or [], "f")
    _update_hash_sequence(hasher, "bonesPerVertex", skin_payload.get("bonesPerVertex") or [], "i")
    _update_hash_sequence(hasher, "boneIndices", skin_payload.get("boneIndices") or [], "i")
    _update_hash_sequence(hasher, "boneWeights", skin_payload.get("boneWeights") or [], "f")
    return hasher.hexdigest()


def _compute_uv_channels_fingerprint(uv_channels, native_hash_profile: dict | None = None) -> str:
    hasher = hashlib.sha1()
    native_hash_profile = native_hash_profile or {}
    for channel in list(uv_channels or []):
        if not isinstance(channel, dict):
            continue
        try:
            channel_index = int(channel.get("index", 0) or 0)
        except Exception:
            channel_index = 0
        hasher.update(str(channel_index).encode("utf-8"))
        hasher.update(b"|")
        hasher.update(str(channel.get("name", "")).encode("utf-8"))
        hasher.update(b"|")
        native_key = f"uv{channel_index}Sha1"
        native_digest = native_hash_profile.get(native_key)
        if native_digest:
            hasher.update(b"valuesSha1")
            hasher.update(str(native_digest).encode("utf-8"))
        else:
            _update_hash_sequence(hasher, "values", channel.get("values") or [], "f")
    return hasher.hexdigest()


def _compute_mesh_geometry_fingerprint(vertices, normals, uv0, indices, exported_vertex_source_indices, native_hash_profile: dict | None = None) -> str:
    hasher = hashlib.sha1()
    native_hash_profile = native_hash_profile or {}
    native_keys = ("vertexSha1", "normalSha1", "uv0Sha1", "indexSha1", "sourceIndexSha1")
    if all(native_hash_profile.get(key) for key in native_keys):
        for key in native_keys:
            hasher.update(key.encode("utf-8"))
            hasher.update(b"=")
            hasher.update(str(native_hash_profile.get(key)).encode("utf-8"))
            hasher.update(b";")
        return hasher.hexdigest()

    _update_hash_sequence(hasher, "vertices", vertices, "f")
    _update_hash_sequence(hasher, "normals", normals, "f")
    _update_hash_sequence(hasher, "uv0", uv0, "f")
    _update_hash_sequence(hasher, "indices", indices, "i")
    _update_hash_sequence(hasher, "sourceIndices", exported_vertex_source_indices, "i")
    return hasher.hexdigest()


def _collect_mesh_color_attribute_values(mesh) -> dict | None:
    try:
        attrs = list(getattr(mesh, "color_attributes", []) or [])
    except Exception:
        attrs = []
    for attr in attrs:
        try:
            data = getattr(attr, "data", None)
            if data is None or len(data) == 0:
                continue
            domain = str(getattr(attr, "domain", "") or "")
            data_type = str(getattr(attr, "data_type", "") or "")
            if domain not in {"CORNER", "POINT"}:
                continue
            values = array("f", [0.0]) * (len(data) * 4)
            data.foreach_get("color", values)
            return {"name": str(getattr(attr, "name", "") or "Color"), "domain": domain, "dataType": data_type, "values": values}
        except Exception:
            continue
    return None


def _build_color0_from_raw(raw: dict, source_loop_indices, source_vertex_indices) -> dict | None:
    attr = raw.get("color_attribute")
    if not isinstance(attr, dict):
        return None
    values = attr.get("values")
    if values is None:
        return None
    domain = str(attr.get("domain") or "")
    source = source_loop_indices if domain == "CORNER" else source_vertex_indices
    if source is None:
        return None
    try:
        source_indices = [int(v) for v in source]
    except Exception:
        source_indices = []
    if not source_indices:
        return None
    out = array("f")
    for src_idx in source_indices:
        base = src_idx * 4
        if base < 0 or base + 3 >= len(values):
            return None
        out.extend((float(values[base]), float(values[base + 1]), float(values[base + 2]), float(values[base + 3])))
    if len(out) != len(source_indices) * 4:
        return None
    return {"name": str(attr.get("name") or "Color"), "domain": domain, "dataType": str(attr.get("dataType") or ""), "values": out}


def _compute_color0_fingerprint(color0: dict | None) -> str:
    if not isinstance(color0, dict):
        return ""
    values = color0.get("values")
    if values is None:
        return ""
    hasher = hashlib.sha1()
    hasher.update(str(color0.get("name") or "").encode("utf-8"))
    hasher.update(str(color0.get("domain") or "").encode("utf-8"))
    hasher.update(str(color0.get("dataType") or "").encode("utf-8"))
    try:
        hasher.update(values.tobytes())
    except Exception:
        hasher.update(repr(list(values)).encode("utf-8"))
    return hasher.hexdigest()


def _compute_submeshes_fingerprint(submeshes: list[dict] | None, precomputed: str | None = None) -> str:
    if precomputed:
        return str(precomputed)
    if not submeshes:
        return ""
    hasher = hashlib.sha1()
    hasher.update(str(len(submeshes)).encode("utf-8"))
    for submesh in submeshes:
        if not isinstance(submesh, dict):
            continue
        hasher.update(b"|slot=")
        hasher.update(str(int(submesh.get("materialSlot", 0) or 0)).encode("utf-8"))
        hasher.update(b"|topology=")
        hasher.update(str(submesh.get("topology") or "triangles").encode("utf-8"))
        _update_hash_sequence(hasher, "indices", submesh.get("indices") or [], "i")
    return hasher.hexdigest()


def _collect_mesh_uv_layer_values(mesh) -> list[dict]:
    channels = []
    try:
        layers = list(getattr(mesh, "uv_layers", []) or [])
    except Exception:
        layers = []
    for index, layer in enumerate(layers[:8]):
        data = getattr(layer, "data", None)
        if data is None or len(data) == 0:
            continue
        try:
            values = array("f", [0.0]) * (len(data) * 2)
            data.foreach_get("uv", values)
            channels.append({"index": int(index), "name": str(getattr(layer, "name", f"UV{index}") or f"UV{index}"), "values": values})
        except Exception:
            continue
    return channels


def _collect_mesh_raw_buffers(mesh):
    vertex_count = len(mesh.vertices)
    loop_count = len(mesh.loops)
    tri_count = len(mesh.loop_triangles)
    co = array("f", [0.0]) * (vertex_count * 3)
    v_normals = array("f", [0.0]) * (vertex_count * 3)
    loop_vertex_indices = array("i", [0]) * loop_count
    loop_normals = array("f", [0.0]) * (loop_count * 3)
    tri_loops = array("i", [0]) * (tri_count * 3)
    mesh.vertices.foreach_get("co", co)
    mesh.vertices.foreach_get("normal", v_normals)
    mesh.loops.foreach_get("vertex_index", loop_vertex_indices)
    try:
        mesh.loops.foreach_get("normal", loop_normals)
        have_loop_normals = True
    except Exception:
        have_loop_normals = False
    mesh.loop_triangles.foreach_get("loops", tri_loops)

    uv_channels = _collect_mesh_uv_layer_values(mesh)
    uv_values = uv_channels[0].get("values") if uv_channels else None
    color_attribute = _collect_mesh_color_attribute_values(mesh)

    return {
        "vertex_count": vertex_count,
        "loop_count": loop_count,
        "tri_count": tri_count,
        "positions": co,
        "vertex_normals": v_normals,
        "loop_vertex_indices": loop_vertex_indices,
        "loop_normals": loop_normals,
        "tri_loops": tri_loops,
        "uv0": uv_values,
        "uv_layers": uv_channels,
        "color_attribute": color_attribute,
        "have_loop_normals": have_loop_normals,
    }


def _build_uv_channels_from_raw(raw: dict, source_loop_indices) -> list[dict]:
    result = []
    uv_layers = list(raw.get("uv_layers") or [])
    if not uv_layers or source_loop_indices is None:
        return result
    try:
        source_loop_count = len(source_loop_indices)
    except Exception:
        source_loop_count = 0
    if source_loop_count <= 0:
        return result
    try:
        source_loop_array = (
            source_loop_indices
            if isinstance(source_loop_indices, array) and source_loop_indices.typecode == "i"
            else array("i", [int(v) for v in source_loop_indices])
        )
    except Exception:
        source_loop_array = array("i")
    for channel in uv_layers[:8]:
        values = channel.get("values") if isinstance(channel, dict) else None
        if values is None:
            continue
        native_result = None
        try:
            native_result = try_gather_uv0_native(uv0=values, source_loop_indices=source_loop_array)
        except Exception:
            native_result = None
        if native_result and native_result.get("ok") and native_result.get("uv0") is not None:
            out = array("f")
            try:
                out.frombytes(bytes(native_result.get("uv0")))
            except Exception:
                out = array("f")
            if len(out) == source_loop_count * 2:
                channel_index = int(channel.get("index") or 0)
                result.append({
                    "index": channel_index,
                    "name": str(channel.get("name") or f"UV{channel_index}"),
                    "values": out,
                    "sha1": str(native_result.get("uv0Hash") or ""),
                })
                continue
        try:
            source_loops = [int(v) for v in source_loop_indices]
        except Exception:
            source_loops = []
        if not source_loops:
            continue
        out = array("f")
        ok = True
        for loop_idx in source_loops:
            base = loop_idx * 2
            if base < 0 or base + 1 >= len(values):
                ok = False
                break
            out.extend((float(values[base]), float(values[base + 1])))
        if ok and len(out) == len(source_loops) * 2:
            channel_index = int(channel.get("index") or 0)
            result.append({"index": channel_index, "name": str(channel.get("name") or f"UV{channel_index}"), "values": out})
    return result


def _raw_requires_python_multi_uv_dedupe(raw: dict, native_status: dict | None = None) -> bool:
    return (
        len(list(raw.get("uv_layers") or [])) > 1
        and not native_supports(NATIVE_MULTI_UV_CAPABILITY, native_status)
    )


def _build_mesh_arrays_from_raw(raw: dict):
    vertex_count = int(raw.get("vertex_count") or 0)
    tri_count = int(raw.get("tri_count") or 0)
    co = raw.get("positions")
    v_normals = raw.get("vertex_normals")
    loop_vertex_indices = raw.get("loop_vertex_indices")
    loop_normals = raw.get("loop_normals")
    tri_loops = raw.get("tri_loops")
    uv_values = raw.get("uv0")
    uv_layer_values = [
        channel.get("values")
        for channel in list(raw.get("uv_layers") or [])[:8]
        if isinstance(channel, dict) and channel.get("values") is not None
    ]
    have_loop_normals = bool(raw.get("have_loop_normals"))

    vertices = []
    normals = []
    uv0 = []
    indices = []
    exported_vertex_source_indices = []
    exported_vertex_source_loop_indices = []
    loop_key_to_index = {}
    next_index = 0

    for tri_i in range(tri_count):
        tri_indices = []
        base = tri_i * 3
        for k in range(3):
            loop_idx = tri_loops[base + k]
            v_idx = loop_vertex_indices[loop_idx]
            if uv_values is not None and loop_idx * 2 + 1 < len(uv_values):
                u = float(uv_values[loop_idx * 2])
                vv = float(uv_values[loop_idx * 2 + 1])
            else:
                u, vv = 0.0, 0.0
            if have_loop_normals:
                n_base = loop_idx * 3
                nx = float(loop_normals[n_base])
                ny = float(loop_normals[n_base + 1])
                nz = float(loop_normals[n_base + 2])
            else:
                n_base = v_idx * 3
                nx = float(v_normals[n_base])
                ny = float(v_normals[n_base + 1])
                nz = float(v_normals[n_base + 2])
            uv_key = []
            for channel_values in uv_layer_values:
                channel_base = loop_idx * 2
                if channel_base >= 0 and channel_base + 1 < len(channel_values):
                    uv_key.extend(
                        (
                            int(float(channel_values[channel_base]) * 1000000.0),
                            int(float(channel_values[channel_base + 1]) * 1000000.0),
                        )
                    )
                else:
                    uv_key.extend((0, 0))
            key = (
                int(v_idx),
                int(u * 1000000.0),
                int(vv * 1000000.0),
                int(nx * 1000000.0),
                int(ny * 1000000.0),
                int(nz * 1000000.0),
                *uv_key,
            )
            mapped = loop_key_to_index.get(key)
            if mapped is None:
                c_base = v_idx * 3
                vertices.extend([float(co[c_base]), float(co[c_base + 1]), float(co[c_base + 2])])
                normals.extend([nx, ny, nz])
                uv0.extend([u, vv])
                exported_vertex_source_indices.append(int(v_idx))
                exported_vertex_source_loop_indices.append(int(loop_idx))
                mapped = next_index
                loop_key_to_index[key] = mapped
                next_index += 1
            tri_indices.append(mapped)
        indices.extend(tri_indices)
    uv_channels = _build_uv_channels_from_raw(raw, exported_vertex_source_loop_indices)
    color0 = _build_color0_from_raw(raw, exported_vertex_source_loop_indices, exported_vertex_source_indices)
    return vertices, normals, uv0, indices, exported_vertex_source_indices, exported_vertex_source_loop_indices, uv_channels, color0


def _build_mesh_arrays_fast(mesh):
    """Build Unity-ready split vertex arrays using Blender foreach_get where possible."""
    global _last_mesh_array_profile
    try:
        mesh_array_profile = {}
        raw_start = time.perf_counter()
        raw = _collect_mesh_raw_buffers(mesh)
        raw_ms = (time.perf_counter() - raw_start) * 1000.0
        mesh_array_profile["rawMs"] = raw_ms
        native_status = get_native_status()
        validate_native = str(os.environ.get("BLENDERSYNC_NATIVE_VALIDATE", "0")).strip().lower() in {"1", "true", "yes", "on"}

        if _raw_requires_python_multi_uv_dedupe(raw, native_status):
            build_start = time.perf_counter()
            result = _build_mesh_arrays_from_raw(raw)
            mesh_array_profile["pythonDedupeMs"] = (time.perf_counter() - build_start) * 1000.0
            _last_mesh_array_profile = mesh_array_profile
            return (*result, "foreach_get_multi_uv_reference", None)

        if validate_native:
            build_start = time.perf_counter()
            vertices, normals, uv0, indices, exported_vertex_source_indices, exported_vertex_source_loop_indices, uv_channels, color0 = _build_mesh_arrays_from_raw(raw)
            build_ms = (time.perf_counter() - build_start) * 1000.0
            mesh_array_profile["pythonDedupeMs"] = build_ms
            reference = build_reference_hash_profile(vertices, normals, uv0, indices, exported_vertex_source_indices)
            mesh_array_profile["referenceHashMs"] = reference.get("hashMs")
            native_call_start = time.perf_counter()
            native_result = try_extract_mesh_arrays_native(raw=raw, validate_reference=reference)
            mesh_array_profile["nativeCallMs"] = (time.perf_counter() - native_call_start) * 1000.0
            if native_result and native_result.get("ok") and native_result.get("validationOk") and native_result.get("vertices") is not None:
                native_profile = native_result.get("profile") or {}
                mesh_array_profile["nativeTotalMs"] = native_profile.get("totalMs")
                mesh_array_profile["nativeDedupeMs"] = native_profile.get("dedupeMs")
                native_hash_profile = native_result.get("hashProfile") or (native_result.get("profile") or {}).get("hashProfile") or {}
                mesh_array_profile["nativeHashMs"] = native_hash_profile.get("hashMs")
                uv_start = time.perf_counter()
                uv_channels = _build_uv_channels_from_raw(raw, native_result.get("sourceLoopIndices") or [])
                mesh_array_profile["uvChannelsBuildMs"] = (time.perf_counter() - uv_start) * 1000.0
                color_start = time.perf_counter()
                color0 = _build_color0_from_raw(raw, native_result.get("sourceLoopIndices") or [], native_result.get("sourceIndices") or [])
                mesh_array_profile["colorBuildMs"] = (time.perf_counter() - color_start) * 1000.0
                _last_mesh_array_profile = mesh_array_profile
                trace(
                    "MeshBuild",
                    "native_validation_profile",
                    lambda: (
                        "[vNext][NativeMeshExtract] "
                        f"mode=validate available={native_status.get('available')} cacheTag={native_status.get('cacheTag')} "
                        f"rawMs={raw_ms:.2f} pythonDedupeMs={build_ms:.2f} nativeTotalMs={native_profile.get('totalMs')} "
                        f"nativeDedupeMs={native_profile.get('dedupeMs')} hashMs={reference.get('hashMs')} "
                        f"validation=True exportVerts={reference.get('exportVertexCount')} indices={reference.get('indexCount')}"
                    ),
                )
                return (
                    native_result.get("vertices"),
                    native_result.get("normals"),
                    native_result.get("uv0"),
                    native_result.get("indices"),
                    native_result.get("sourceIndices") or [],
                    native_result.get("sourceLoopIndices") or [],
                    uv_channels,
                    color0,
                    "pyd_accurate_validate",
                    native_hash_profile,
                )
            trace(
                "MeshBuild",
                "native_validation_fallback_profile",
                lambda: (
                    "[vNext][NativeMeshExtract] "
                    f"mode=validate available={native_status.get('available')} cacheTag={native_status.get('cacheTag')} "
                    f"rawMs={raw_ms:.2f} pythonDedupeMs={build_ms:.2f} hashMs={reference.get('hashMs')} "
                    f"exportVerts={reference.get('exportVertexCount')} indices={reference.get('indexCount')} "
                    f"nativeOk={bool(native_result and native_result.get('ok'))} validation={native_result.get('validationOk') if isinstance(native_result, dict) else None} "
                    f"mismatch={native_result.get('validationMismatch') if isinstance(native_result, dict) else None} "
                    f"importError={native_status.get('importError')}"
                ),
            )
            _last_mesh_array_profile = mesh_array_profile
            return vertices, normals, uv0, indices, exported_vertex_source_indices, exported_vertex_source_loop_indices, uv_channels, color0, "foreach_get_reference", None

        native_call_start = time.perf_counter()
        native_result = try_extract_mesh_arrays_native(raw=raw)
        mesh_array_profile["nativeCallMs"] = (time.perf_counter() - native_call_start) * 1000.0
        if native_result and native_result.get("ok") and native_result.get("vertices") is not None:
            native_profile = native_result.get("profile") or {}
            native_hash_profile = native_result.get("hashProfile") or native_profile.get("hashProfile") or {}
            mesh_array_profile["nativeTotalMs"] = native_profile.get("totalMs")
            mesh_array_profile["nativeDedupeMs"] = native_profile.get("dedupeMs")
            mesh_array_profile["nativeHashMs"] = native_hash_profile.get("hashMs")
            export_verts = int(len(native_result.get("vertices") or []) // 3)
            index_count = int(len(native_result.get("indices") or []))
            uv_start = time.perf_counter()
            uv_channels = _build_uv_channels_from_raw(raw, native_result.get("sourceLoopIndices") or [])
            mesh_array_profile["uvChannelsBuildMs"] = (time.perf_counter() - uv_start) * 1000.0
            color_start = time.perf_counter()
            color0 = _build_color0_from_raw(raw, native_result.get("sourceLoopIndices") or [], native_result.get("sourceIndices") or [])
            mesh_array_profile["colorBuildMs"] = (time.perf_counter() - color_start) * 1000.0
            _last_mesh_array_profile = mesh_array_profile
            trace(
                "MeshBuild",
                "native_profile",
                lambda: (
                    "[vNext][NativeMeshExtract] "
                    f"mode=native available={native_status.get('available')} cacheTag={native_status.get('cacheTag')} "
                    f"rawMs={raw_ms:.2f} nativeTotalMs={native_profile.get('totalMs')} "
                    f"nativeDedupeMs={native_profile.get('dedupeMs')} nativeHashMs={native_hash_profile.get('hashMs')} "
                    f"nativeCallMs={mesh_array_profile.get('nativeCallMs'):.2f} uvBuildMs={mesh_array_profile.get('uvChannelsBuildMs'):.2f} colorBuildMs={mesh_array_profile.get('colorBuildMs'):.2f} "
                    f"exportVerts={export_verts} indices={index_count}"
                ),
            )
            return (
                native_result.get("vertices"),
                native_result.get("normals"),
                native_result.get("uv0"),
                native_result.get("indices"),
                native_result.get("sourceIndices") or [],
                native_result.get("sourceLoopIndices") or [],
                uv_channels,
                color0,
                "pyd_accurate",
                native_hash_profile,
            )

        build_start = time.perf_counter()
        vertices, normals, uv0, indices, exported_vertex_source_indices, exported_vertex_source_loop_indices, uv_channels, color0 = _build_mesh_arrays_from_raw(raw)
        build_ms = (time.perf_counter() - build_start) * 1000.0
        mesh_array_profile["pythonDedupeMs"] = build_ms
        _last_mesh_array_profile = mesh_array_profile
        trace(
            "MeshBuild",
            "python_fallback_profile",
            lambda: (
                "[vNext][NativeMeshExtract] "
                f"mode=fallback available={native_status.get('available')} cacheTag={native_status.get('cacheTag')} "
                f"rawMs={raw_ms:.2f} pythonDedupeMs={build_ms:.2f} "
                f"nativeOk={bool(native_result and native_result.get('ok'))} importError={native_status.get('importError')}"
            ),
        )
        return vertices, normals, uv0, indices, exported_vertex_source_indices, exported_vertex_source_loop_indices, uv_channels, color0, "foreach_get_reference", None
    except Exception as exc:
        warn(
            "MeshBuild",
            "foreach_get_fallback",
            str(exc),
            {"fallback": "slow_mesh_iteration"},
        )
        vertices, normals, uv0, indices, exported_vertex_source_indices = _build_mesh_arrays_slow(mesh)
        _last_mesh_array_profile = {"fallbackReason": str(exc)}
        return vertices, normals, uv0, indices, exported_vertex_source_indices, [], [], None, "fallback", None


def _build_mesh_arrays_slow(mesh):
    vertices = []
    normals = []
    uv0 = []
    indices = []
    uv_layer = mesh.uv_layers.active.data if getattr(mesh.uv_layers, "active", None) else None
    loop_key_to_index = {}
    exported_vertex_source_indices = []
    next_index = 0
    for tri in mesh.loop_triangles:
        tri_indices = []
        for loop_idx in tri.loops:
            loop = mesh.loops[loop_idx]
            v_idx = loop.vertex_index
            v = mesh.vertices[v_idx]
            if uv_layer and loop_idx < len(uv_layer):
                u = float(uv_layer[loop_idx].uv.x)
                vv = float(uv_layer[loop_idx].uv.y)
            else:
                u, vv = 0.0, 0.0
            loop_normal = getattr(loop, "normal", None)
            if loop_normal is not None:
                nx, ny, nz = float(loop_normal.x), float(loop_normal.y), float(loop_normal.z)
            else:
                nx, ny, nz = float(v.normal.x), float(v.normal.y), float(v.normal.z)
            key = (int(v_idx), int(u * 1000000.0), int(vv * 1000000.0), int(nx * 1000000.0), int(ny * 1000000.0), int(nz * 1000000.0))
            mapped = loop_key_to_index.get(key)
            if mapped is None:
                normal_source = loop_normal if loop_normal is not None else v.normal
                pos, nor = v.co.copy(), normal_source.copy()
                vertices.extend([float(pos.x), float(pos.y), float(pos.z)])
                normals.extend([float(nor.x), float(nor.y), float(nor.z)])
                uv0.extend([u, vv])
                exported_vertex_source_indices.append(int(v_idx))
                mapped = next_index
                loop_key_to_index[key] = mapped
                next_index += 1
            tri_indices.append(mapped)
        if len(tri_indices) == 3:
            indices.extend(tri_indices)
    return vertices, normals, uv0, indices, exported_vertex_source_indices


def _build_material_submeshes(
    mesh,
    indices,
    *,
    material_count: int | None = None,
    material_indices_override=None,
) -> tuple[list[dict], str]:
    global _last_submesh_profile
    profile = {"materialReadMs": 0.0, "groupMs": 0.0, "hashMs": 0.0}
    total_start = time.perf_counter()
    if mesh is None or not indices:
        _last_submesh_profile = profile
        return [], ""
    try:
        resolved_material_count = (
            int(material_count)
            if material_count is not None
            else len(getattr(mesh, "materials", []) or [])
        )
        if resolved_material_count <= 1:
            _last_submesh_profile = profile
            return [], ""
    except Exception:
        pass

    loop_triangles = getattr(mesh, "loop_triangles", None)
    try:
        tri_count = len(loop_triangles or [])
    except Exception:
        tri_count = 0
    if tri_count <= 0 or len(indices) < tri_count * 3:
        _last_submesh_profile = profile
        return [], ""

    material_indices = None
    material_start = time.perf_counter()
    if material_indices_override is not None:
        try:
            material_indices = array("i", (max(0, int(value)) for value in material_indices_override))
            if len(material_indices) != tri_count:
                material_indices = None
        except Exception:
            material_indices = None
    else:
        try:
            material_indices = array("i", [0]) * tri_count
            loop_triangles.foreach_get("material_index", material_indices)
        except Exception:
            material_indices = None
    profile["materialReadMs"] = (time.perf_counter() - material_start) * 1000.0

    if material_indices is not None:
        native_start = time.perf_counter()
        native_result = try_build_material_submeshes_native(indices=indices, material_indices=material_indices)
        profile["nativeMs"] = (time.perf_counter() - native_start) * 1000.0
        if native_result and native_result.get("ok"):
            submeshes = list(native_result.get("subMeshes") or [])
            if not submeshes:
                profile["totalMs"] = (time.perf_counter() - total_start) * 1000.0
                _last_submesh_profile = profile
                return [], ""
            if len(submeshes) == 1 and int((submeshes[0] or {}).get("materialSlot", 0) or 0) == 0:
                profile["totalMs"] = (time.perf_counter() - total_start) * 1000.0
                _last_submesh_profile = profile
                return [], ""
            native_profile = native_result.get("profile") or {}
            profile["groupMs"] = float(native_profile.get("groupMs") or 0.0)
            profile["hashMs"] = float(native_profile.get("hashMs") or 0.0)
            profile["subMeshCount"] = len(submeshes)
            profile["totalMs"] = (time.perf_counter() - total_start) * 1000.0
            _last_submesh_profile = profile
            return submeshes, str(native_result.get("fingerprint") or "")

    grouped: dict[int, list[int]] = {}
    group_start = time.perf_counter()
    if material_indices is not None:
        for tri_index, material_slot in enumerate(material_indices):
            if material_slot < 0:
                material_slot = 0
            base = tri_index * 3
            slot_indices = grouped.get(material_slot)
            if slot_indices is None:
                slot_indices = []
                grouped[material_slot] = slot_indices
            slot_indices.extend(indices[base:base + 3])
    else:
        try:
            triangles = list(loop_triangles or [])
        except Exception:
            triangles = []
        for tri_index, tri in enumerate(triangles):
            material_slot = int(getattr(tri, "material_index", 0) or 0)
            if material_slot < 0:
                material_slot = 0
            base = tri_index * 3
            slot_indices = grouped.get(material_slot)
            if slot_indices is None:
                slot_indices = []
                grouped[material_slot] = slot_indices
            slot_indices.extend(indices[base:base + 3])
    profile["groupMs"] = (time.perf_counter() - group_start) * 1000.0

    if not grouped:
        profile["totalMs"] = (time.perf_counter() - total_start) * 1000.0
        _last_submesh_profile = profile
        return [], ""
    if len(grouped) == 1 and 0 in grouped:
        profile["totalMs"] = (time.perf_counter() - total_start) * 1000.0
        _last_submesh_profile = profile
        return [], ""

    submeshes = []
    hash_start = time.perf_counter()
    fingerprint_hasher = hashlib.sha1()
    non_empty = [(slot, slot_indices) for slot, slot_indices in sorted(grouped.items()) if slot_indices]
    fingerprint_hasher.update(str(len(non_empty)).encode("utf-8"))
    for material_slot, slot_indices in non_empty:
        fingerprint_hasher.update(b"|slot=")
        fingerprint_hasher.update(str(material_slot).encode("utf-8"))
        fingerprint_hasher.update(b"|topology=triangles")
        _update_hash_sequence(fingerprint_hasher, "indices", slot_indices, "i")
        submeshes.append(
            {
                "materialSlot": material_slot,
                "topology": "triangles",
                "indices": slot_indices,
            }
        )
    profile["hashMs"] = (time.perf_counter() - hash_start) * 1000.0
    profile["subMeshCount"] = len(submeshes)
    profile["totalMs"] = (time.perf_counter() - total_start) * 1000.0
    _last_submesh_profile = profile
    return submeshes, fingerprint_hasher.hexdigest()


def _get_scene_mesh_source() -> str:
    # Auto is the product policy. The legacy Scene property remains registered
    # so older .blend files load cleanly, but hidden saved overrides must not
    # continue changing runtime behavior.
    return "auto"


def _modifier_summary(obj) -> list[str]:
    names = []
    for mod in list(getattr(obj, "modifiers", []) or []):
        try:
            if not bool(getattr(mod, "show_viewport", True)):
                continue
            mod_type = str(getattr(mod, "type", "") or "")
            mod_name = str(getattr(mod, "name", "") or mod_type or "Modifier")
            names.append(f"{mod_name}:{mod_type}" if mod_type else mod_name)
        except Exception:
            continue
    return names


def _mesh_has_shape_keys(obj) -> bool:
    mesh = getattr(obj, "data", None) if obj is not None else None
    shape_keys = getattr(mesh, "shape_keys", None) if mesh is not None else None
    key_blocks = getattr(shape_keys, "key_blocks", []) if shape_keys is not None else []
    try:
        return len(key_blocks) > 1
    except Exception:
        return False


def _resolve_mesh_source_for_object(obj, requested: str, include_rigged_payload: bool, rigged_payload=None, force_skin_mesh_source: bool = False) -> tuple[str, str, list[str]]:
    requested = requested if requested in {"auto", "original", "evaluated"} else "auto"
    modifiers = _modifier_summary(obj)
    has_shape_keys = _mesh_has_shape_keys(obj)
    has_skin_or_rigged = bool(force_skin_mesh_source or rigged_payload is not None)

    # Product policy: Shape Keys/BlendShapes and Skin/Rigged semantics require
    # stable original topology, so they stay on the Original Mesh path by
    # default. This remains true if an internal caller requests Evaluated; a
    # separate future "bake static evaluated mesh" workflow can opt out of
    # these semantics explicitly.
    if has_skin_or_rigged:
        return "original", "skin_or_rigged_original", modifiers
    if has_shape_keys:
        return "original", "shape_keys_original", modifiers
    if requested == "original":
        return "original", "forced_original", modifiers
    if requested == "evaluated":
        return "evaluated", "forced_evaluated", modifiers
    if modifiers:
        return "evaluated", "auto_active_modifiers", modifiers
    return "original", "auto_no_active_modifiers", modifiers


@contextmanager
def _mesh_for_sync(obj, mesh_source: str):
    original_mesh = getattr(obj, "data", None)
    if mesh_source != "evaluated" or bpy is None:
        yield obj, original_mesh, False
        return

    with evaluated_mesh_for_sync(obj) as lease:
        if lease.mesh is None:
            raise ValueError("mesh_has_no_exportable_geometry")
        yield lease.owner or obj, lease.mesh, True


def _build_mesh_object_live_context(
    session,
    obj,
    package_id: str,
    include_rigged_payload: bool = True,
    force_skin_mesh_source: bool = False,
    skin_ordered_bone_ids_override: list[str] | None = None,
    material_contents_unknown_only: bool = False,
    material_snapshot_override=None,
) -> dict:
    profile_start = time.perf_counter()
    original_mesh = obj.data
    requested_mesh_source = _get_scene_mesh_source()
    rigged_material_snapshots_by_ref = {}
    rigged_payload_precheck = (
        build_unity_rig_v1_payload(
            obj,
            material_snapshots_by_mesh_ref=rigged_material_snapshots_by_ref,
        )
        if include_rigged_payload
        else None
    )
    mesh_source, mesh_source_reason, modifier_names = _resolve_mesh_source_for_object(
        obj,
        requested_mesh_source,
        include_rigged_payload,
        rigged_payload=rigged_payload_precheck,
        force_skin_mesh_source=force_skin_mesh_source,
    )
    if mesh_source == "original" and requested_mesh_source == "evaluated" and mesh_source_reason in {"skin_or_rigged_original", "shape_keys_original"}:
        trace(
            "MeshBuild",
            "evaluated_source_skipped",
            lambda: f"[vNext][MeshBuildProfile] object={obj.name} evaluated_mesh_source_skipped reason={mesh_source_reason}",
        )

    try:
        obj.update_from_editmode()
    except Exception:
        pass
    update_ms = (time.perf_counter() - profile_start) * 1000.0

    with _mesh_for_sync(obj, mesh_source) as (_mesh_owner, mesh, evaluated_mesh):
        if mesh is None:
            raise ValueError("mesh_has_no_exportable_geometry")
        tri_start = time.perf_counter()
        mesh.calc_loop_triangles()
        tri_ms = (time.perf_counter() - tri_start) * 1000.0

        loop_build_start = time.perf_counter()
        vertices, normals, uv0, indices, exported_vertex_source_indices, exported_vertex_source_loop_indices, uv_channels, color0, loop_build_mode, native_hash_profile = _build_mesh_arrays_fast(mesh)
        loop_build_ms = (time.perf_counter() - loop_build_start) * 1000.0
        if len(vertices) < 3 or not indices:
            raise ValueError("mesh_has_no_exportable_geometry")

        obj_slug = _slug(obj.name)
        pair_id = f"pair-{ensure_instance_id(obj)}"
        mesh_asset_id = ensure_mesh_asset_id_for_object(obj)
        mesh_ref = f"mesh-{mesh_asset_id}"
        effective_skin_ordered_bone_ids = skin_ordered_bone_ids_override
        if effective_skin_ordered_bone_ids is None and isinstance(rigged_payload_precheck, dict):
            effective_skin_ordered_bone_ids = dict(rigged_payload_precheck.get("_meshPartOrderedBoneIdsByRef") or {}).get(mesh_ref)

        material_snapshot = material_snapshot_override or rigged_material_snapshots_by_ref.get(mesh_ref)
        if material_snapshot is None:
            material_snapshot = collect_material_export_snapshot(
                obj,
                mesh,
                geometry_is_evaluated=evaluated_mesh,
            )
        material_refs, material_contents = _build_export_material_payloads(
            obj,
            mesh,
            evaluated_mesh=evaluated_mesh,
            material_contents_unknown_only=material_contents_unknown_only,
            materials=material_snapshot.materials,
        )

        material_dep = []

        skip_skin_reason = "evaluated_mesh_source" if evaluated_mesh else ""
        skin_payload = None
        skin_ms = 0.0
        if not evaluated_mesh:
            skin_start = time.perf_counter()
            skin_payload = build_mesh_skin_payload(obj, exported_vertex_source_indices, ordered_bone_ids_override=effective_skin_ordered_bone_ids)
            skin_ms = (time.perf_counter() - skin_start) * 1000.0
        fp_start = time.perf_counter()
        skin_fp_start = time.perf_counter()
        skin_fp = _compute_skin_fingerprint(skin_payload)
        skin_fp_ms = (time.perf_counter() - skin_fp_start) * 1000.0
        geom_fp_start = time.perf_counter()
        geom_fp = _compute_mesh_geometry_fingerprint(vertices, normals, uv0, indices, exported_vertex_source_indices, native_hash_profile)
        uv_channels_fp = _compute_uv_channels_fingerprint(uv_channels, native_hash_profile)
        geom_hash_ms = (time.perf_counter() - geom_fp_start) * 1000.0
        submesh_build_start = time.perf_counter()
        submeshes, submesh_precomputed_fp = _build_material_submeshes(
            mesh,
            indices,
            material_count=len(material_snapshot.materials),
            material_indices_override=material_snapshot.triangle_material_indices,
        )
        submesh_build_ms = (time.perf_counter() - submesh_build_start) * 1000.0
        submesh_fp_start = time.perf_counter()
        submesh_fp = _compute_submeshes_fingerprint(submeshes, submesh_precomputed_fp)
        submesh_fp_ms = (time.perf_counter() - submesh_fp_start) * 1000.0
        geom_fp_ms = (time.perf_counter() - geom_fp_start) * 1000.0
        blend_shape_build_start = time.perf_counter()
        blend_shapes = [] if evaluated_mesh else _build_mesh_blend_shapes(mesh, exported_vertex_source_indices)
        blend_shape_build_ms = (time.perf_counter() - blend_shape_build_start) * 1000.0
        blend_shape_skip_reason = "evaluated_mesh_source" if evaluated_mesh else ""
        blend_shape_fp_start = time.perf_counter()
        blend_shape_hasher = hashlib.sha1()
        blend_shape_hasher.update(str(len(blend_shapes)).encode("utf-8"))
        for shape in blend_shapes:
            blend_shape_hasher.update(str(shape.get("name", "")).encode("utf-8"))
            blend_shape_hasher.update(b"|")
            blend_shape_hasher.update(str(shape.get("frameWeight", 100.0)).encode("utf-8"))
            blend_shape_hasher.update(b"|")
            blend_shape_hasher.update(str(shape.get("value", 0.0)).encode("utf-8"))
            blend_shape_hasher.update(b"|")
            blend_shape_hasher.update(str(shape.get("sliderMin", 0.0)).encode("utf-8"))
            blend_shape_hasher.update(b"|")
            blend_shape_hasher.update(str(shape.get("sliderMax", 1.0)).encode("utf-8"))
            blend_shape_hasher.update(b"|")
            _update_hash_sequence(blend_shape_hasher, "deltaPositions", shape.get("deltaPositions") or [], "f")
        blend_shape_fp = blend_shape_hasher.hexdigest()
        blend_shape_fp_ms = (time.perf_counter() - blend_shape_fp_start) * 1000.0
        mesh_fp = hashlib.sha1(
            (
                f"{mesh_asset_id}|meshSourceRequested={requested_mesh_source}|meshSourceResolved={mesh_source}|meshSourceReason={mesh_source_reason}|evaluated={evaluated_mesh}|mods={modifier_names}|"
                f"orig={len(original_mesh.vertices)}:{len(original_mesh.edges)}:{len(original_mesh.polygons)}:{len(original_mesh.loops)}|"
                f"eval={len(mesh.vertices)}:{len(mesh.edges)}:{len(mesh.polygons)}:{len(mesh.loops)}:{len(mesh.loop_triangles)}|"
                f"export={len(vertices)}:{len(indices)}:{len(normals)}:{len(uv0)}|geom={geom_fp}|uvChannels={uv_channels_fp}|subMeshes={submesh_fp}|color0={_compute_color0_fingerprint(color0)}|skin={skin_fp}|blendShapes={blend_shape_fp}"
            ).encode("utf-8")
        ).hexdigest()
        fp_ms = (time.perf_counter() - fp_start) * 1000.0
        mesh_payload = {
            "topology": "triangles",
            "spaceSemantic": "blender_evaluated_mesh_v1" if evaluated_mesh else "blender_live_v0",
            "vertexCount": len(mesh.vertices),
            "vertices": vertices,
            "indices": indices,
            "subMeshes": submeshes,
            "normals": normals,
            "uv0": uv0,
            "uvChannels": uv_channels,
            "uvChannelCount": len(uv_channels),
            "uvChannelNames": [str(ch.get("name") or f"UV{int(ch.get('index') or 0)}") for ch in uv_channels if isinstance(ch, dict)],
            "color0": color0.get("values") if isinstance(color0, dict) else [],
            "colorAttributeName": color0.get("name") if isinstance(color0, dict) else "",
            "colorAttributeCount": 1 if isinstance(color0, dict) else 0,
            "_binaryHashProfile": dict(native_hash_profile or {}),
            "meshSource": "evaluated" if evaluated_mesh else "original",
            "meshSourceRequested": requested_mesh_source,
            "meshSourceResolved": "evaluated" if evaluated_mesh else "original",
            "meshSourceReason": mesh_source_reason,
            "sourceVertexCount": len(original_mesh.vertices),
            "sourcePolygonCount": len(original_mesh.polygons),
            "evaluatedVertexCount": len(mesh.vertices),
            "evaluatedPolygonCount": len(mesh.polygons),
            "evaluatedLoopCount": len(mesh.loops),
            "evaluatedTriangleCount": len(mesh.loop_triangles),
            "modifierSummary": modifier_names,
        }
        if blend_shape_skip_reason:
            mesh_payload["blendShapeSkipReason"] = blend_shape_skip_reason
        if skip_skin_reason:
            mesh_payload["skinSkipReason"] = skip_skin_reason
        if blend_shapes:
            mesh_payload["blendShapes"] = blend_shapes
        if skin_payload:
            skin_payload.pop("_fingerprint", None)
            mesh_payload["skin"] = skin_payload

        total_profile_ms = (time.perf_counter() - profile_start) * 1000.0
        trace(
            "MeshBuild",
            "object_profile",
            lambda: (
                "[vNext][MeshBuildProfile] "
                f"object={obj.name} meshSource={mesh_payload.get('meshSource')} modifiers={modifier_names} "
                f"sourceVerts={len(original_mesh.vertices)} evaluatedVerts={len(mesh.vertices)} exportVerts={len(vertices)//3} indices={len(indices)} "
                f"subMeshes={len(submeshes)} uvLayers={len(list(getattr(mesh, 'uv_layers', []) or []))} uvChannels={len(uv_channels)} uvNames={mesh_payload.get('uvChannelNames')} colorAttributes={mesh_payload.get('colorAttributeCount')} colorName={mesh_payload.get('colorAttributeName')} "
                f"mode={loop_build_mode} updateMs={update_ms:.2f} triMs={tri_ms:.2f} "
                f"loopBuildMs={loop_build_ms:.2f} fpMs={fp_ms:.2f} skinFpMs={skin_fp_ms:.2f} geomFpMs={geom_fp_ms:.2f} geomHashMs={geom_hash_ms:.2f} submeshBuildMs={submesh_build_ms:.2f} submeshFpMs={submesh_fp_ms:.2f} "
                f"blendShapeCount={len(blend_shapes)} blendShapeSkip={blend_shape_skip_reason or 'none'} blendShapeBuildMs={blend_shape_build_ms:.2f} blendShapeFpMs={blend_shape_fp_ms:.2f} "
                f"skinMs={skin_ms:.2f} skinSkip={skip_skin_reason or 'none'} totalMs={total_profile_ms:.2f}"
            ),
        )
        trace(
            "MeshBuild",
            "object_detail_profile",
            lambda: _format_mesh_detail_profile(obj.name, loop_build_mode),
        )

        selected = [{
            "key": f"obj-{obj_slug}",
            "type": "mesh",
            "source_uri": f"blender://object/{obj.name}",
            "metadata": {
                "assetId": mesh_ref,
                "sourceFingerprint": mesh_fp,
                "mesh": mesh_payload,
            },
            "deps": material_dep,
        }]

    q = obj.rotation_quaternion if obj.rotation_mode == "QUATERNION" else obj.matrix_world.to_quaternion()
    rigged_payload = rigged_payload_precheck
    object_assembly = None
    if rigged_payload is None:
        object_assembly = {
            "type": "scene_sync.object_assembly",
            "timestamp": int(__import__("time").time()),
            "pairId": pair_id,
            "objectName": obj.name,
            "objectType": "mesh",
            "meshRef": mesh_ref,
            "materialRefs": material_refs,
            "position": [float(obj.location.x), float(obj.location.y), float(obj.location.z)],
            "rotation": [float(q.x), float(q.y), float(q.z), float(q.w)],
            "scale": [float(obj.scale.x), float(obj.scale.y), float(obj.scale.z)],
        }
    return {
        "session": session,
        "packageId": package_id,
        "selected": selected,
        "scene": [],
        "objectAssembly": object_assembly,
        "materialContents": material_contents,
        "riggedObjects": [rigged_payload] if rigged_payload else [],
    }


def _build_camera_object_live_context(session, obj, package_id: str) -> dict:
    camera = obj.data
    q = obj.rotation_quaternion if obj.rotation_mode == "QUATERNION" else obj.matrix_world.to_quaternion()
    return {
        "session": session,
        "packageId": package_id,
        "selected": [],
        "scene": [],
        "objectAssembly": {
            "type": "scene_sync.object_assembly",
            "timestamp": int(__import__("time").time()),
            "pairId": f"pair-{ensure_instance_id(obj)}",
            "objectName": obj.name,
            "objectType": "camera",
            "active": _get_object_visible_in_current_view(obj),
            "camera": {
                "fov": float(getattr(camera, "angle", 0.0) or 0.0),
                "near": float(getattr(camera, "clip_start", 0.0) or 0.0),
                "far": float(getattr(camera, "clip_end", 0.0) or 0.0),
            },
            "meshRef": "",
            "materialRefs": [],
            "position": [float(obj.location.x), float(obj.location.y), float(obj.location.z)],
            "rotation": [float(q.x), float(q.y), float(q.z), float(q.w)],
            "scale": [float(obj.scale.x), float(obj.scale.y), float(obj.scale.z)],
        },
    }


def _build_light_object_live_context(session, obj, package_id: str) -> dict:
    light = obj.data
    q = obj.rotation_quaternion if obj.rotation_mode == "QUATERNION" else obj.matrix_world.to_quaternion()
    color = getattr(light, "color", None)
    return {
        "session": session,
        "packageId": package_id,
        "selected": [],
        "scene": [],
        "objectAssembly": {
            "type": "scene_sync.object_assembly",
            "timestamp": int(__import__("time").time()),
            "pairId": f"pair-{ensure_instance_id(obj)}",
            "objectName": obj.name,
            "objectType": "light",
            "active": _get_object_visible_in_current_view(obj),
            "light": {
                "lightType": str(getattr(light, "type", "POINT") or "POINT"),
                "color": [
                    float(color[0]) if color is not None and len(color) > 0 else 1.0,
                    float(color[1]) if color is not None and len(color) > 1 else 1.0,
                    float(color[2]) if color is not None and len(color) > 2 else 1.0,
                ],
                "intensity": float(getattr(light, "energy", 0.0) or 0.0),
                "range": float(getattr(light, "cutoff_distance", 0.0) or 0.0),
                "spotAngle": float(getattr(light, "spot_size", 0.0) or 0.0),
            },
            "meshRef": "",
            "materialRefs": [],
            "position": [float(obj.location.x), float(obj.location.y), float(obj.location.z)],
            "rotation": [float(q.x), float(q.y), float(q.z), float(q.w)],
            "scale": [float(obj.scale.x), float(obj.scale.y), float(obj.scale.z)],
        },
    }


def _build_empty_object_live_context(session, obj, package_id: str) -> dict:
    q = obj.rotation_quaternion if obj.rotation_mode == "QUATERNION" else obj.matrix_world.to_quaternion()
    return {
        "session": session,
        "packageId": package_id,
        "selected": [],
        "scene": [],
        "objectAssembly": {
            "type": "scene_sync.object_assembly",
            "timestamp": int(__import__("time").time()),
            "pairId": f"pair-{ensure_instance_id(obj)}",
            "objectName": obj.name,
            "objectType": "empty",
            "active": _get_object_visible_in_current_view(obj),
            "meshRef": "",
            "materialRefs": [],
            "position": [float(obj.location.x), float(obj.location.y), float(obj.location.z)],
            "rotation": [float(q.x), float(q.y), float(q.z), float(q.w)],
            "scale": [float(obj.scale.x), float(obj.scale.y), float(obj.scale.z)],
        },
    }


def _build_armature_object_live_context(session, obj, package_id: str) -> dict:
    material_snapshots_by_mesh_ref = {}
    rigged_payload = build_unity_rig_v1_payload(
        obj,
        material_snapshots_by_mesh_ref=material_snapshots_by_mesh_ref,
    )
    if not rigged_payload:
        raise ValueError("armature_no_rigged_mesh_parts")

    # Phase 1 rigged mesh native/binary reuse: when importing an Armature
    # directly, also enqueue its skinned mesh resources through the same mesh
    # resource builder used by plain Mesh objects.  That builder already uses
    # _build_mesh_arrays_fast(), so geometry extraction follows the unified
    # native -> foreach_get -> fallback path.  Skin influence extraction remains
    # the existing Python path keyed by exported sourceIndices.
    selected = []
    material_contents = []
    seen_asset_ids = set()
    seen_material_refs = set()
    mesh_parts = collect_armature_mesh_parts(obj)
    rigged_part_bones_by_ref = dict((rigged_payload or {}).get("_meshPartOrderedBoneIdsByRef") or {})
    for mesh_obj in mesh_parts:
        if mesh_obj is None or getattr(mesh_obj, "type", None) != "MESH" or getattr(mesh_obj, "data", None) is None:
            continue
        mesh_ref = f"mesh-{ensure_mesh_asset_id_for_object(mesh_obj)}"
        mesh_context = _build_mesh_object_live_context(
            session,
            mesh_obj,
            package_id,
            include_rigged_payload=False,
            force_skin_mesh_source=True,
            skin_ordered_bone_ids_override=rigged_part_bones_by_ref.get(mesh_ref),
            material_contents_unknown_only=True,
            material_snapshot_override=material_snapshots_by_mesh_ref.get(mesh_ref),
        )
        for node in mesh_context.get("selected", []) or []:
            asset_id = str(((node.get("metadata") or {}).get("assetId")) or "").strip()
            if asset_id and asset_id in seen_asset_ids:
                continue
            if asset_id:
                seen_asset_ids.add(asset_id)
            selected.append(node)
        for material_content in mesh_context.get("materialContents", []) or []:
            material_ref = str((material_content or {}).get("materialRef") or "").strip()
            dedupe_key = material_ref or str((material_content or {}).get("materialName") or "")
            if dedupe_key and dedupe_key in seen_material_refs:
                continue
            if dedupe_key:
                seen_material_refs.add(dedupe_key)
            material_contents.append(material_content)

    trace(
        "MeshBuild",
        "rigged_resource_reuse",
        lambda: (
            "[vNext][RiggedMeshResourceReuse] "
            f"armature={obj.name} meshParts={len(mesh_parts)} selectedResources={len(selected)} materialContents={len(material_contents)} mode=native_mesh_artifact"
        ),
    )

    return {
        "session": session,
        "packageId": package_id,
        "selected": selected,
        "scene": [],
        "objectAssembly": None,
        "materialContents": material_contents,
        "riggedObjects": [rigged_payload],
    }


def _build_object_live_context(session, obj, package_id: str) -> dict:
    if obj is None:
        raise ValueError("object_missing")

    info = classify_scene_object(obj)
    if not info.get("supported"):
        raise ValueError(str(info.get("reason") or "object_not_supported"))

    object_type = getattr(obj, "type", None)
    if object_type == "MESH" and getattr(obj, "data", None) is not None:
        return _build_mesh_object_live_context(session, obj, package_id)
    if object_type == "ARMATURE" and getattr(obj, "data", None) is not None:
        return _build_armature_object_live_context(session, obj, package_id)
    if object_type == "CAMERA" and getattr(obj, "data", None) is not None:
        return _build_camera_object_live_context(session, obj, package_id)
    if object_type == "LIGHT" and getattr(obj, "data", None) is not None:
        return _build_light_object_live_context(session, obj, package_id)
    if object_type == "EMPTY":
        return _build_empty_object_live_context(session, obj, package_id)

    raise ValueError("object_not_supported")


def build_single_object_live_context(session, obj, package_id: str | None = None) -> dict:
    context = _build_object_live_context(
        session,
        obj,
        package_id or f"pkg-object-auto-{int(time.time() * 1000)}",
    )
    context["sendMode"] = "full_send"
    context["visibilityPayloads"] = _build_selection_visibility_payloads([obj])
    return context


def _build_name_payloads(objects: list) -> list[dict]:
    objects = _filter_lightweight_state_objects(objects)
    payloads: list[dict] = []
    seen: set[str] = set()
    now = int(time.time())

    for obj in objects or []:
        if obj is None:
            continue
        pair_id = _object_state_pair_id(obj, rigged_armature=True)
        if not pair_id or pair_id in seen:
            continue
        seen.add(pair_id)
        payloads.append(
            {
                "type": "scene_sync.object_name",
                "timestamp": now,
                "pairId": pair_id,
                "objectName": getattr(obj, "name", pair_id),
            }
        )

    return payloads


def _get_object_visible_in_current_view(obj) -> bool:
    if obj is None:
        return True

    try:
        if bpy is not None and bpy.context is not None:
            view_layer = getattr(bpy.context, "view_layer", None)
            return not bool(obj.hide_get(view_layer=view_layer))
    except Exception:
        pass

    return not bool(getattr(obj, "hide_viewport", False))


def _matrix_to_row_major_values(matrix) -> list[float]:
    if matrix is None:
        return []
    try:
        return [float(v) for row in matrix for v in row]
    except Exception:
        return []


def _object_rotation_quaternion_values(obj) -> list[float]:
    try:
        q = obj.rotation_quaternion if obj.rotation_mode == "QUATERNION" else obj.rotation_euler.to_quaternion()
        return [float(q.x), float(q.y), float(q.z), float(q.w)]
    except Exception:
        return [0.0, 0.0, 0.0, 1.0]


def _object_state_pair_id(obj, *, rigged_armature: bool = False) -> str:
    if rigged_armature and getattr(obj, "type", None) == "ARMATURE":
        return f"rigpair-{ensure_rigged_object_id(obj)}"
    return f"pair-{ensure_instance_id(obj)}"


def _is_managed_mesh_part(obj, managed_mesh_keys: set | None = None) -> bool:
    if obj is None or getattr(obj, "type", None) != "MESH":
        return False
    key = object_identity_key(obj)
    if managed_mesh_keys and key in managed_mesh_keys:
        return True

    find_armature = getattr(obj, "find_armature", None)
    armature = find_armature() if callable(find_armature) else None
    if armature is None or getattr(armature, "type", None) != "ARMATURE":
        return False

    try:
        for mesh_obj in collect_armature_mesh_parts(armature):
            if mesh_obj is not None and object_identity_key(mesh_obj) == key:
                return True
    except Exception:
        return False
    return False


def _filter_lightweight_state_objects(objects: list) -> list:
    object_list = [obj for obj in (objects or []) if obj is not None]
    managed_mesh_keys = collect_meshes_managed_by_armatures(object_list)
    return [
        obj
        for obj in object_list
        if not _is_managed_mesh_part(obj, managed_mesh_keys)
    ]


def _build_object_state_items(objects: list, *, rigged_armatures: bool = False) -> list[dict]:
    items: list[dict] = []
    seen: set[str] = set()
    object_set = {obj for obj in (objects or []) if obj is not None}

    for obj in objects or []:
        if obj is None:
            continue
        pair_id = _object_state_pair_id(obj, rigged_armature=rigged_armatures)
        if not pair_id or pair_id in seen:
            continue
        seen.add(pair_id)

        parent = getattr(obj, "parent", None)
        parent_pair_id = _object_state_pair_id(parent, rigged_armature=rigged_armatures) if parent is not None else ""
        matrix_local = getattr(obj, "matrix_local", None)
        matrix_world = getattr(obj, "matrix_world", None)
        try:
            local_t, local_q, local_s = matrix_local.decompose() if matrix_local is not None else (None, None, None)
        except Exception:
            local_t, local_q, local_s = None, None, None
        if local_t is None:
            local_t = getattr(obj, "location", None)
        if local_q is None:
            try:
                local_q = obj.rotation_quaternion if obj.rotation_mode == "QUATERNION" else obj.rotation_euler.to_quaternion()
            except Exception:
                local_q = None
        if local_s is None:
            local_s = getattr(obj, "scale", None)
        q = [
            float(getattr(local_q, "x", 0.0)),
            float(getattr(local_q, "y", 0.0)),
            float(getattr(local_q, "z", 0.0)),
            float(getattr(local_q, "w", 1.0)),
        ]
        origin_world = getattr(matrix_world, "translation", None) if matrix_world is not None else None
        try:
            world_q = matrix_world.to_quaternion() if matrix_world is not None else None
            world_s = matrix_world.to_scale() if matrix_world is not None else None
        except Exception:
            world_q = None
            world_s = None

        items.append(
            {
                "pairId": pair_id,
                "objectName": getattr(obj, "name", pair_id),
                "objectType": str(getattr(obj, "type", "UNKNOWN") or "UNKNOWN"),
                "visible": _get_object_visible_in_current_view(obj),
                "parentPairId": parent_pair_id,
                "clearParent": parent is None,
                "parentKnownInBatch": bool(parent is not None and parent in object_set),
                "position": [
                    float(getattr(local_t, "x", 0.0)),
                    float(getattr(local_t, "y", 0.0)),
                    float(getattr(local_t, "z", 0.0)),
                ],
                "rotation": q,
                "scale": [
                    float(getattr(local_s, "x", 1.0)),
                    float(getattr(local_s, "y", 1.0)),
                    float(getattr(local_s, "z", 1.0)),
                ],
                "matrixLocal": _matrix_to_row_major_values(matrix_local),
                "matrixWorld": _matrix_to_row_major_values(matrix_world),
                "originLocal": [
                    float(getattr(local_t, "x", 0.0)),
                    float(getattr(local_t, "y", 0.0)),
                    float(getattr(local_t, "z", 0.0)),
                ],
                "originWorld": [
                    float(getattr(origin_world, "x", 0.0)),
                    float(getattr(origin_world, "y", 0.0)),
                    float(getattr(origin_world, "z", 0.0)),
                ],
                "worldPosition": [
                    float(getattr(origin_world, "x", 0.0)),
                    float(getattr(origin_world, "y", 0.0)),
                    float(getattr(origin_world, "z", 0.0)),
                ],
                "worldRotation": [
                    float(getattr(world_q, "x", q[0])),
                    float(getattr(world_q, "y", q[1])),
                    float(getattr(world_q, "z", q[2])),
                    float(getattr(world_q, "w", q[3])),
                ],
                "worldScale": [
                    float(getattr(world_s, "x", obj.scale.x)),
                    float(getattr(world_s, "y", obj.scale.y)),
                    float(getattr(world_s, "z", obj.scale.z)),
                ],
            }
        )

    return items


def build_object_state_payload(objects: list, *, source_hint: str = "manual_state_update", rigged_armatures: bool = False) -> dict:
    return {
        "type": "scene_sync.object_state_update_v1",
        "timestamp": int(time.time()),
        "sourceHint": str(source_hint or "manual_state_update"),
        "objects": _build_object_state_items(objects, rigged_armatures=rigged_armatures),
    }


def build_objects_object_state_context(session, objects: list, *, selected_object_count: int | None = None) -> dict:
    selected_objects = list(objects or [])
    if not selected_objects:
        raise ValueError("selection_empty")

    supported_objects = collect_supported_sync_objects(selected_objects)
    if not supported_objects:
        raise ValueError("selection_no_supported_objects")

    state_supported_objects = _filter_lightweight_state_objects(supported_objects)
    ready_objects = [obj for obj in state_supported_objects if bool(obj.get(AUTO_SYNC_READY_KEY, False))]
    if not ready_objects:
        raise ValueError("selection_no_imported_objects")

    payload = build_object_state_payload(ready_objects, source_hint="manual_state_update", rigged_armatures=True)
    items = payload.get("objects") or []
    if not items:
        raise ValueError("selection_no_state_items")

    return {
        "session": session,
        "triggerType": "object_state",
        "sendMode": "object_state_only",
        "correlationId": _new_correlation_id("state"),
        "selectedObjectCount": int(selected_object_count if selected_object_count is not None else len(selected_objects)),
        "supportedSelectedObjectCount": len(ready_objects),
        "unsupportedSelectedObjectCount": len(summarize_unsupported(selected_objects)),
        "unsupportedObjects": summarize_unsupported(selected_objects),
        "skippedNotReadyObjectCount": len(state_supported_objects) - len(ready_objects),
        "selected": [],
        "objectStatePayload": payload,
        "objectStateCount": len(items),
        "_supportedObjects": ready_objects,
    }


def build_selected_object_state_context(session) -> dict:
    if bpy is None or bpy.context is None:
        raise ValueError("blender_context_missing")

    selected_objects = list(getattr(bpy.context, "selected_objects", []) or [])
    if not selected_objects:
        active = getattr(bpy.context, "object", None)
        selected_objects = [active] if active is not None else []
    return build_objects_object_state_context(session, selected_objects)


def _build_selection_visibility_payloads(objects: list) -> list[dict]:
    objects = _filter_lightweight_state_objects(objects)
    payloads: list[dict] = []
    seen: set[str] = set()
    now = int(time.time())

    for obj in objects or []:
        if obj is None:
            continue

        pair_id = _object_state_pair_id(obj, rigged_armature=True)
        if not pair_id or pair_id in seen:
            continue
        seen.add(pair_id)

        payloads.append(
            {
                "type": "scene_sync.visibility",
                "timestamp": now,
                "pairId": pair_id,
                "visible": _get_object_visible_in_current_view(obj),
            }
        )

    return payloads


def _build_selection_hierarchy_payloads(objects: list) -> list[dict]:
    if bpy is None:
        return []

    objects = _filter_lightweight_state_objects(objects)
    object_set = {obj for obj in (objects or []) if obj is not None}
    payloads: list[dict] = []
    seen_children: set[str] = set()
    now = int(time.time())

    for obj in objects or []:
        if obj is None:
            continue

        child_pair_id = _object_state_pair_id(obj, rigged_armature=True)
        if not child_pair_id or child_pair_id in seen_children:
            continue

        parent = getattr(obj, "parent", None)
        if parent is None:
            seen_children.add(child_pair_id)
            payloads.append(
                {
                    "type": "scene_sync.hierarchy",
                    "timestamp": now,
                    "childPairId": child_pair_id,
                    "parentPairId": "",
                    "clearParent": True,
                }
            )
            continue

        if parent not in object_set:
            continue

        parent_pair_id = _object_state_pair_id(parent, rigged_armature=True)
        if not parent_pair_id or child_pair_id == parent_pair_id:
            continue

        seen_children.add(child_pair_id)
        payloads.append(
            {
                "type": "scene_sync.hierarchy",
                "timestamp": now,
                "childPairId": child_pair_id,
                "parentPairId": parent_pair_id,
                "clearParent": False,
            }
        )

    return payloads


def _iter_hierarchy_descendants(root) -> list:
    if root is None:
        return []

    result = []
    stack = list(getattr(root, "children", []) or [])
    seen = set()
    while stack:
        current = stack.pop(0)
        if current is None:
            continue
        ptr = getattr(current, "as_pointer", None)
        key = ptr() if callable(ptr) else id(current)
        if key in seen:
            continue
        seen.add(key)
        result.append(current)
        stack.extend(list(getattr(current, "children", []) or []))
    return result


def _expand_selected_send_objects(objects: list) -> list:
    expanded = []
    seen = set()

    def _append(obj):
        if obj is None or not is_supported_scene_object(obj):
            return
        ptr = getattr(obj, "as_pointer", None)
        key = ptr() if callable(ptr) else id(obj)
        if key in seen:
            return
        seen.add(key)
        expanded.append(obj)

    for obj in objects or []:
        if obj is None:
            continue
        object_type = getattr(obj, "type", None)
        if object_type in {"MESH", "CAMERA", "LIGHT", "EMPTY"}:
            _append(obj)
            continue
        if object_type == "ARMATURE":
            _append(obj)
            for child in _iter_hierarchy_descendants(obj):
                if is_supported_scene_object(child) and getattr(child, "type", None) in {"MESH", "EMPTY"}:
                    _append(child)

    return expanded


def collect_supported_sync_objects(objects: list) -> list:
    return _expand_selected_send_objects(objects)


def build_objects_hierarchy_context(session, objects: list, *, trigger_type: str = "object_structure") -> dict:
    supported_objects = collect_supported_sync_objects(objects or [])
    if not supported_objects:
        raise ValueError("selection_no_supported_objects")

    payloads = _build_selection_hierarchy_payloads(supported_objects)
    return {
        "session": session,
        "triggerType": trigger_type,
        "correlationId": _new_correlation_id("hier"),
        "selectedObjectCount": len(supported_objects),
        "selected": [],
        "namePayloads": _build_name_payloads(supported_objects),
        "visibilityPayloads": _build_selection_visibility_payloads(supported_objects),
        "hierarchyPayloads": payloads,
    }


def object_identity_key(obj):
    ptr = getattr(obj, "as_pointer", None)
    return ptr() if callable(ptr) else id(obj)


def collect_meshes_managed_by_armatures(objects) -> set:
    managed = set()
    for obj in list(objects or []):
        if obj is None or getattr(obj, "type", None) != "ARMATURE":
            continue
        try:
            for mesh_obj in collect_armature_mesh_parts(obj):
                if mesh_obj is not None:
                    managed.add(object_identity_key(mesh_obj))
        except Exception as exc:
            warn(
                "ObjectContext",
                "rigged_mesh_collection_failed",
                str(exc),
                {"armatureName": getattr(obj, "name", None)},
            )
    return managed


def build_selected_live_context(session, package_id: str = "pkg-selected-manual") -> dict:
    if bpy is None or bpy.context is None:
        raise ValueError("blender_context_missing")

    selected_objects = list(getattr(bpy.context, "selected_objects", []) or [])
    supported_objects = collect_supported_sync_objects(selected_objects)

    if not supported_objects:
        raise ValueError("selection_no_supported_objects")

    selected_by_resource_key = {}
    all_object_assemblies = []
    all_material_contents = []
    seen_material_content_refs = set()
    rigged_objects = []
    seen_rigged_ids = set()
    managed_mesh_keys = collect_meshes_managed_by_armatures(supported_objects)
    skipped_managed_meshes = 0
    selected_armatures = []
    seen_armatures = set()
    for original in selected_objects:
        if original is None or getattr(original, "type", None) != "ARMATURE":
            continue
        ptr = getattr(original, "as_pointer", None)
        key = ptr() if callable(ptr) else id(original)
        if key in seen_armatures:
            continue
        seen_armatures.add(key)
        selected_armatures.append(original)

    selected_armature_keys = {object_identity_key(obj) for obj in selected_armatures}
    ordinary_scene_objects = [
        obj
        for obj in supported_objects
        if not (getattr(obj, "type", None) == "ARMATURE" and object_identity_key(obj) in selected_armature_keys)
        and not (getattr(obj, "type", None) == "MESH" and object_identity_key(obj) in managed_mesh_keys)
    ]

    single_contexts = []
    for obj in supported_objects:
        if getattr(obj, "type", None) == "MESH" and object_identity_key(obj) in managed_mesh_keys:
            skipped_managed_meshes += 1
            continue
        single = _build_object_live_context(session, obj, package_id)
        single_contexts.append(single)
        for root in single.get("selected", []) or []:
            meta = root.get("metadata", {}) or {}
            asset_id = meta.get("assetId")
            source_fingerprint = meta.get("sourceFingerprint")
            root_type = root.get("type")
            if root_type == "mesh":
                dedupe_key = (root_type, asset_id)
            else:
                dedupe_key = (root_type, asset_id, source_fingerprint)
            if dedupe_key not in selected_by_resource_key:
                selected_by_resource_key[dedupe_key] = root
        assembly = single.get("objectAssembly")
        if assembly:
            all_object_assemblies.append(assembly)
        for material_content in single.get("materialContents", []) or []:
            material_ref = str((material_content or {}).get("materialRef") or "").strip()
            if material_ref and material_ref in seen_material_content_refs:
                continue
            if material_ref:
                seen_material_content_refs.add(material_ref)
            all_material_contents.append(material_content)

    for single in single_contexts:
        for rigged_payload in single.get("riggedObjects", []) or []:
            if not rigged_payload:
                continue
            rig = rigged_payload.get("riggedObject") or {}
            rig_id = str(rig.get("riggedObjectId") or "").strip()
            if rig_id and rig_id not in seen_rigged_ids:
                seen_rigged_ids.add(rig_id)
                rigged_objects.append(rigged_payload)

    for armature in selected_armatures:
        rigged_id = ensure_rigged_object_id(armature)
        if rigged_id in seen_rigged_ids:
            continue
        rigged_payload = build_unity_rig_v1_payload(armature)
        if rigged_payload:
            rig = rigged_payload.get("riggedObject") or {}
            rig_id = str(rig.get("riggedObjectId") or "").strip()
            if rig_id and rig_id not in seen_rigged_ids:
                seen_rigged_ids.add(rig_id)
                rigged_objects.append(rigged_payload)

    if skipped_managed_meshes:
        trace(
            "ObjectContext",
            "managed_rig_meshes_skipped",
            lambda: (
                f"[vNext][RiggedMeshDedup] skip_direct_mesh_build count={skipped_managed_meshes} "
                "reason=managed_by_selected_armature"
            ),
        )

    trace(
        "ObjectContext",
        "selection_built",
        lambda: "Built the selected-object synchronization context.",
        lambda: {
            "selectedResources": len(selected_by_resource_key),
            "objectAssemblies": len(all_object_assemblies),
            "materialContents": len(all_material_contents),
            "riggedObjects": len(rigged_objects),
        },
    )

    return {
        "session": session,
        "packageId": package_id,
        "sendMode": "full_send",
        "triggerType": "selection",
        "correlationId": _new_correlation_id("sel"),
        "selectedObjectCount": len(selected_objects),
        "supportedSelectedObjectCount": len(supported_objects),
        "unsupportedSelectedObjectCount": len(summarize_unsupported(selected_objects)),
        "unsupportedObjects": summarize_unsupported(selected_objects),
        "resourceCountEstimate": len(selected_by_resource_key),
        "selected": list(selected_by_resource_key.values()),
        "scene": [],
        "objectAssemblies": all_object_assemblies,
        "materialContents": all_material_contents,
        "riggedObjects": rigged_objects,
        "visibilityPayloads": _build_selection_visibility_payloads(ordinary_scene_objects),
        "hierarchyPayloads": _build_selection_hierarchy_payloads(ordinary_scene_objects),
        "_supportedObjects": supported_objects,
    }


