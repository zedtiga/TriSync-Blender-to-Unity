from __future__ import annotations

from dataclasses import dataclass
from typing import Callable
from array import array
import hashlib
import os
import time

from blender.common.blend_shapes import build_mesh_blend_shapes
from blender.common.evaluated_mesh import evaluated_mesh_for_sync, EvaluatedMeshLease
from blender.common.log import exception as log_exception, trace, warn
from blender.identity import ensure_instance_id, ensure_mesh_asset_id_for_object
from blender.material_resource.slots import compact_evaluated_material_slots
from blender.native.mesh_extractor import (
    NATIVE_MULTI_UV_CAPABILITY,
    get_native_status,
    native_supports,
    try_extract_mesh_arrays_native,
    try_extract_mesh_binary_native,
)
from blender.resource_update.fingerprint import compute_mesh_content_fingerprint
from blender.scene_sync.runtime_state import PreviewMappingRuntimeState
from blender.ui.state_view import get_session

try:
    import bpy  # type: ignore
except ImportError:
    bpy = None
try:
    import bmesh  # type: ignore
except ImportError:
    bmesh = None

AUTO_SYNC_READY_KEY = "blendersync_auto_sync_ready"

@dataclass(frozen=True)
class MeshContextHooks:
    get_current_mode: Callable[[], str]
    get_sync_enabled: Callable[[], bool]
    pair_id_for_object: Callable[[object], str | None]
    mesh_has_shape_keys: Callable[[object], bool]
    object_has_visible_modifiers: Callable[[object], bool]
    had_visible_modifiers: Callable[[str], bool]
    preview_buffer_dir: Callable[[], object]
    safe_preview_name: Callable[[str], str]
    write_array_buffer: Callable[..., dict]
    read_active_uv0_values: Callable[[object], object]
    log_uv_skip: Callable[[str, str], None]
    uv_verbose_enabled: Callable[[], bool]
    should_include_fingerprint: Callable[..., bool]
    collect_material_refs: Callable[[object], tuple[str, ...]]
    material_refs_for_export: Callable[..., tuple[str, ...]] | None = None
    material_contents_for_export: Callable[..., list[dict]] | None = None

def _collect_mesh_uv_layer_values_for_preview(mesh, pair_id: str | None = None) -> list[dict]:
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


def _collect_edit_bmesh_uv_layer_values_for_preview(hooks, mesh, pair_id: str | None = None) -> list[dict]:
    if bmesh is None or hooks.get_current_mode() != "EDIT_MESH":
        return []
    try:
        bm = bmesh.from_edit_mesh(mesh)
        loop_count = len(mesh.loops)
        layer_names = []
        try:
            layer_names = list(bm.loops.layers.uv.keys())
        except Exception:
            layer_names = []
        layers = []
        for index, name in enumerate(layer_names[:8]):
            try:
                layer = bm.loops.layers.uv.get(name)
            except Exception:
                layer = None
            if layer is not None:
                layers.append((index, str(name or f"UV{index}"), layer))
        if not layers:
            active = bm.loops.layers.uv.active
            if active is not None:
                layers.append((0, "UV0", active))

        channels = []
        for index, name, layer in layers:
            values = array("f", [0.0]) * (loop_count * 2)
            filled = 0
            append_values = array("f")
            for face in bm.faces:
                for loop in face.loops:
                    uv = loop[layer].uv
                    loop_index = int(getattr(loop, "index", -1))
                    if 0 <= loop_index < loop_count:
                        base = loop_index * 2
                        values[base] = float(uv.x)
                        values[base + 1] = float(uv.y)
                        filled += 1
                    else:
                        append_values.append(float(uv.x))
                        append_values.append(float(uv.y))
            if filled == loop_count and loop_count > 0:
                channels.append({"index": int(index), "name": name, "values": values})
            elif len(append_values) == loop_count * 2 and loop_count > 0:
                channels.append({"index": int(index), "name": name, "values": append_values})
        if channels and pair_id and hooks.uv_verbose_enabled():
            trace(
                "MeshContext",
                "edit_bmesh_uv_used",
                lambda: "Read UV channels from the edit-mode BMesh.",
                lambda: {"pairId": pair_id, "channelCount": len(channels), "loopCount": loop_count},
            )
        return channels
    except Exception as exc:
        if pair_id:
            hooks.log_uv_skip(pair_id, f"edit_bmesh_uv_channels_failed:{exc}")
        return []


def collect_mesh_color_attribute_values_for_preview(mesh) -> dict | None:
    try:
        attrs = list(getattr(mesh, "color_attributes", []) or [])
    except Exception:
        attrs = []
    # Phase 1: export the first supported color attribute as COLOR0.
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
            return {
                "name": str(getattr(attr, "name", "") or "Color"),
                "domain": domain,
                "dataType": data_type,
                "values": values,
            }
        except Exception:
            continue
    return None


def _build_color0_from_raw_for_preview(raw: dict, source_loop_indices, source_vertex_indices) -> dict | None:
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
        out.extend((
            float(values[base]),
            float(values[base + 1]),
            float(values[base + 2]),
            float(values[base + 3]),
        ))
    if len(out) != len(source_indices) * 4:
        return None
    return {
        "name": str(attr.get("name") or "Color"),
        "domain": domain,
        "dataType": str(attr.get("dataType") or ""),
        "values": out,
    }


def _build_uv_channels_from_raw_for_preview(raw: dict, source_loop_indices) -> list[dict]:
    result = []
    uv_layers = list(raw.get("uv_layers") or [])
    if not uv_layers or source_loop_indices is None:
        return result
    try:
        source_loops = [int(v) for v in source_loop_indices]
    except Exception:
        source_loops = []
    if not source_loops:
        return result
    for channel in uv_layers[:8]:
        values = channel.get("values") if isinstance(channel, dict) else None
        if values is None:
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


def _attach_color0_to_prebuilt_binary(hooks, prebuilt_binary: dict, color0: dict | None, pair_id: str) -> None:
    if not isinstance(prebuilt_binary, dict) or not isinstance(color0, dict):
        return
    values = color0.get("values")
    vertex_count = int(prebuilt_binary.get("vertexCount") or 0)
    if values is None or len(values) != vertex_count * 4:
        prebuilt_binary["color0"] = None
        prebuilt_binary.setdefault("profile", {})["colorAttributeCount"] = 0
        prebuilt_binary.setdefault("profile", {})["colorBinaryBytes"] = 0
        return
    root = hooks.preview_buffer_dir()
    base = f"{hooks.safe_preview_name(pair_id)}_color0_{int(time.time() * 1000)}_{os.getpid()}"
    path = root / f"{base}.bin"
    byte_length = hooks.write_array_buffer(path, values)
    buffer = {
        "semantic": "COLOR0",
        "format": "float32",
        "components": 4,
        "count": vertex_count,
        "path": str(path),
        "byteLength": byte_length,
    }
    prebuilt_binary.setdefault("buffers", []).append(buffer)
    prebuilt_binary["color0"] = buffer
    prebuilt_binary["colorAttributeName"] = str(color0.get("name") or "Color")
    profile = prebuilt_binary.setdefault("profile", {})
    profile["colorAttributeCount"] = 1
    profile["colorBinaryBytes"] = int(byte_length)
    hash_profile = profile.setdefault("hashProfile", {})
    hash_profile["color0Sha1"] = hashlib.sha1(values.tobytes()).hexdigest()
    prebuilt_binary["hashProfile"] = hash_profile


def _attach_blend_shapes_to_prebuilt_binary(hooks, prebuilt_binary: dict, blend_shapes: list[dict], pair_id: str) -> None:
    if not isinstance(prebuilt_binary, dict):
        return

    vertex_count = int(prebuilt_binary.get("vertexCount") or 0)
    profile = prebuilt_binary.setdefault("profile", {})
    hash_profile = profile.setdefault("hashProfile", {})
    buffers = prebuilt_binary.setdefault("buffers", [])
    binary_blend_shapes = []
    total_bytes = 0
    for index, shape in enumerate(list(blend_shapes or [])):
        if not isinstance(shape, dict):
            continue
        deltas = shape.get("deltaPositions")
        if deltas is None or vertex_count <= 0 or len(deltas) != vertex_count * 3:
            continue
        delta_array = deltas if isinstance(deltas, array) else array("f", [float(v) for v in deltas])
        base = f"{hooks.safe_preview_name(pair_id)}_blendshape_{index}_{hooks.safe_preview_name(shape.get('name'))}_{int(time.time() * 1000)}_{os.getpid()}"
        path = hooks.preview_buffer_dir() / f"{base}.bin"
        byte_length = hooks.write_array_buffer(path, delta_array)
        buffer = {
            "semantic": "BLENDSHAPE_DELTA_POSITION",
            "format": "float32",
            "components": 3,
            "count": vertex_count,
            "path": str(path),
            "byteLength": byte_length,
        }
        buffers.append(buffer)
        packed_shape = dict(shape)
        packed_shape.pop("deltaPositions", None)
        packed_shape["deltaPositionsBuffer"] = buffer
        packed_shape["vertexCount"] = vertex_count
        binary_blend_shapes.append(packed_shape)
        total_bytes += int(byte_length or 0)
        hash_profile[f"blendshape_{index}_delta_positionsSha1"] = hashlib.sha1(delta_array.tobytes()).hexdigest()

    prebuilt_binary["blendShapes"] = binary_blend_shapes
    profile["blendShapeCount"] = len(binary_blend_shapes)
    profile["blendShapeBinaryBytes"] = total_bytes
    profile["binaryBytes"] = int(profile.get("binaryBytes") or 0) + total_bytes
    prebuilt_binary["hashProfile"] = hash_profile


def _attach_uv_channels_to_prebuilt_binary(hooks, prebuilt_binary: dict, raw: dict, source_loop_indices, pair_id: str) -> None:
    if not isinstance(prebuilt_binary, dict):
        return
    uv_channels = _build_uv_channels_from_raw_for_preview(raw, source_loop_indices)
    if not uv_channels:
        prebuilt_binary["uvChannels"] = []
        prebuilt_binary.setdefault("profile", {})["uvChannelCount"] = 0
        prebuilt_binary.setdefault("profile", {})["uvBinaryBytes"] = 0
        return
    buffers = prebuilt_binary.setdefault("buffers", [])
    existing_uv_buffers = {
        int(str(buffer.get("semantic") or "UV-1")[2:]): buffer
        for buffer in buffers
        if isinstance(buffer, dict)
        and str(buffer.get("semantic") or "").upper().startswith("UV")
        and str(buffer.get("semantic") or "").upper()[2:].isdigit()
    }
    root = hooks.preview_buffer_dir()
    base = f"{hooks.safe_preview_name(pair_id)}_uvchannels_{int(time.time() * 1000)}_{os.getpid()}"
    vertex_count = int(prebuilt_binary.get("vertexCount") or 0)
    uv_meta = []
    uv_bytes = 0
    written: set[int] = set()
    for channel in uv_channels:
        channel_index = int(channel.get("index") or 0)
        values = channel.get("values")
        if channel_index in written or channel_index < 0 or channel_index > 7:
            continue
        if values is None or len(values) != vertex_count * 2:
            continue
        written.add(channel_index)
        semantic = f"UV{channel_index}"
        buffer = existing_uv_buffers.get(channel_index)
        if buffer is None:
            path = root / f"{base}_uv{channel_index}.bin"
            byte_length = hooks.write_array_buffer(path, values)
            uv_bytes += int(byte_length)
            buffer = {
                "semantic": semantic,
                "format": "float32",
                "components": 2,
                "count": vertex_count,
                "path": str(path),
                "byteLength": byte_length,
            }
            buffers.append(buffer)
        else:
            uv_bytes += int(buffer.get("byteLength") or 0)
        uv_meta.append({"index": channel_index, "name": channel.get("name") or semantic, "buffer": buffer})
    prebuilt_binary["uvChannels"] = uv_meta
    profile = prebuilt_binary.setdefault("profile", {})
    profile["uvChannelCount"] = len(uv_meta)
    profile["uvBinaryBytes"] = uv_bytes
    hash_profile = profile.setdefault("hashProfile", {})
    for channel in uv_meta:
        channel_index = int(channel.get("index") or 0)
        buffer = channel.get("buffer") or {}
        path = buffer.get("path")
        digest = None
        try:
            with open(path, "rb") as f:
                digest = hashlib.sha1(f.read()).hexdigest()
        except Exception:
            pass
        if digest:
            hash_profile[f"uv{channel_index}Sha1"] = digest
    prebuilt_binary["hashProfile"] = hash_profile


def _collect_mesh_raw_buffers_for_preview(hooks, mesh, pair_id: str | None = None, *, allow_edit_bmesh_uv: bool = False) -> dict:
    vertex_count = len(mesh.vertices)
    loop_count = len(mesh.loops)
    tri_count = len(mesh.loop_triangles)
    co = array("f", [0.0]) * (vertex_count * 3)
    v_normals = array("f", [0.0]) * (vertex_count * 3)
    loop_vertex_indices = array("i", [0]) * loop_count
    loop_normals = array("f", [0.0]) * (loop_count * 3)
    tri_loops = array("i", [0]) * (tri_count * 3)
    tri_material_indices = array("i", [0]) * tri_count
    mesh.vertices.foreach_get("co", co)
    mesh.vertices.foreach_get("normal", v_normals)
    mesh.loops.foreach_get("vertex_index", loop_vertex_indices)
    try:
        mesh.loops.foreach_get("normal", loop_normals)
        have_loop_normals = True
    except Exception:
        have_loop_normals = False
    mesh.loop_triangles.foreach_get("loops", tri_loops)
    try:
        mesh.loop_triangles.foreach_get("material_index", tri_material_indices)
    except Exception:
        try:
            for tri_i, tri in enumerate(mesh.loop_triangles):
                tri_material_indices[tri_i] = int(getattr(tri, "material_index", 0) or 0)
        except Exception:
            tri_material_indices = array("i", [0]) * tri_count

    uv_channels = _collect_edit_bmesh_uv_layer_values_for_preview(hooks, mesh, pair_id=pair_id) if allow_edit_bmesh_uv else []
    if not uv_channels:
        uv_channels = _collect_mesh_uv_layer_values_for_preview(mesh, pair_id=pair_id)
    uv_values = uv_channels[0].get("values") if uv_channels else hooks.read_active_uv0_values(mesh)
    color_attribute = collect_mesh_color_attribute_values_for_preview(mesh)

    return {
        "vertex_count": vertex_count,
        "loop_count": loop_count,
        "tri_count": tri_count,
        "positions": co,
        "vertex_normals": v_normals,
        "loop_vertex_indices": loop_vertex_indices,
        "loop_normals": loop_normals,
        "tri_loops": tri_loops,
        "tri_material_indices": tri_material_indices,
        "uv0": uv_values,
        "uv_layers": uv_channels,
        "color_attribute": color_attribute,
        "have_loop_normals": have_loop_normals,
    }


def _build_mesh_arrays_from_raw_for_preview_detailed(raw: dict):
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
            indices.append(mapped)
    return vertices, normals, uv0, indices, exported_vertex_source_indices, exported_vertex_source_loop_indices


def _build_mesh_arrays_from_raw_for_preview(raw: dict):
    vertices, normals, uv0, indices, _, _ = _build_mesh_arrays_from_raw_for_preview_detailed(raw)
    return vertices, normals, uv0, indices


def _sha1_int_sequence(values) -> str:
    if values is None:
        return ""
    if isinstance(values, array) and values.typecode == "i":
        arr = values
    else:
        arr = array("i")
        try:
            arr.fromlist([int(v) for v in values])
        except Exception:
            return ""
    return hashlib.sha1(arr.tobytes()).hexdigest()


def _build_material_submeshes_from_raw_for_preview(raw: dict, indices) -> list[dict]:
    if not isinstance(raw, dict) or not indices:
        return []
    tri_count = int(raw.get("tri_count") or 0)
    tri_material_indices = raw.get("tri_material_indices")
    if tri_count <= 0 or tri_material_indices is None or len(indices) < tri_count * 3:
        return []

    grouped: dict[int, list[int]] = {}
    for tri_i in range(tri_count):
        try:
            material_slot = int(tri_material_indices[tri_i])
        except Exception:
            material_slot = 0
        if material_slot < 0:
            material_slot = 0
        base = tri_i * 3
        grouped.setdefault(material_slot, []).extend([int(indices[base]), int(indices[base + 1]), int(indices[base + 2])])

    if not grouped:
        return []
    if len(grouped) == 1 and 0 in grouped:
        return []

    return [
        {
            "materialSlot": material_slot,
            "topology": "triangles",
            "indices": slot_indices,
        }
        for material_slot, slot_indices in sorted(grouped.items())
        if slot_indices
    ]


def _raw_preview_needs_submeshes(raw: dict) -> bool:
    if not isinstance(raw, dict):
        return False
    values = raw.get("tri_material_indices")
    if values is None:
        return False
    try:
        return any(int(v) != 0 for v in values)
    except Exception:
        return False


def resolve_auto_preview_mesh_source(runtime, hooks, obj, requested: str | None = None) -> tuple[str, str]:
    requested = str(requested or "original")
    if requested == "evaluated":
        if hooks.mesh_has_shape_keys(obj):
            return "original", "shape_keys_original"
        pair_id = hooks.pair_id_for_object(obj)
        if hooks.object_has_visible_modifiers(obj):
            return "evaluated", "forced_evaluated"
        if pair_id and hooks.had_visible_modifiers(pair_id):
            return "original", "modifier_stack_cleared_revert_original"
        return "original", "evaluated_requested_no_modifiers"
    return "original", "original"


def build_mesh_update_context_for_object(
    runtime,
    hooks,
    obj,
    include_extras: bool = False,
    mesh_source_override: str | None = None,
    rebuild_reason: str | None = None,
    force_mesh_content_fingerprint: bool = False,
    allow_edit_bmesh_uv: bool = False,
) -> dict | None:
    if (
        obj is not None
        and getattr(obj, "type", None) == "MESH"
        and getattr(obj, "data", None) is not None
        and bool(obj.get(AUTO_SYNC_READY_KEY, False))
    ):
        mesh_source, _reason = resolve_auto_preview_mesh_source(
            runtime,
            hooks,
            obj,
            mesh_source_override,
        )
        if mesh_source == "evaluated":
            with evaluated_mesh_for_sync(obj) as lease:
                return _build_mesh_update_context_for_object_impl(
                    runtime,
                    hooks,
                    obj,
                    include_extras=include_extras,
                    mesh_source_override=mesh_source_override,
                    rebuild_reason=rebuild_reason,
                    force_mesh_content_fingerprint=force_mesh_content_fingerprint,
                    allow_edit_bmesh_uv=allow_edit_bmesh_uv,
                    _evaluated_lease=lease,
                    _resolved_mesh_source=(mesh_source, _reason),
                )
    return _build_mesh_update_context_for_object_impl(
        runtime,
        hooks,
        obj,
        include_extras=include_extras,
        mesh_source_override=mesh_source_override,
        rebuild_reason=rebuild_reason,
        force_mesh_content_fingerprint=force_mesh_content_fingerprint,
        allow_edit_bmesh_uv=allow_edit_bmesh_uv,
    )


def _build_mesh_update_context_for_object_impl(
    runtime,
    hooks,
    obj,
    include_extras: bool = False,
    mesh_source_override: str | None = None,
    rebuild_reason: str | None = None,
    force_mesh_content_fingerprint: bool = False,
    allow_edit_bmesh_uv: bool = False,
    _evaluated_lease: EvaluatedMeshLease | None = None,
    _resolved_mesh_source: tuple[str, str] | None = None,
) -> dict | None:
    if obj is None:
        return None
    if getattr(obj, "type", None) != "MESH" or getattr(obj, "data", None) is None:
        return None

    if not bool(obj.get(AUTO_SYNC_READY_KEY, False)):
        return None

    session = get_session()
    pair_id = f"pair-{ensure_instance_id(obj)}"
    if session is None or not pair_id:
        return None

    original_mesh = obj.data
    mesh_ref = f"mesh-{ensure_mesh_asset_id_for_object(obj)}"
    profile_start = time.perf_counter()
    update_ms = 0.0
    if hooks.get_current_mode() == "EDIT_MESH":
        update_start = time.perf_counter()
        try:
            obj.update_from_editmode()
        except Exception:
            pass
        update_ms = (time.perf_counter() - update_start) * 1000.0

    if _resolved_mesh_source is None:
        mesh_source, mesh_source_reason = resolve_auto_preview_mesh_source(runtime, hooks, obj, mesh_source_override)
    else:
        mesh_source, mesh_source_reason = _resolved_mesh_source
    evaluated_mesh_source = ""
    evaluated_instance_count = 0
    evaluated_instances_realized = False
    mesh = original_mesh
    if mesh_source == "evaluated":
        if _evaluated_lease is None:
            raise RuntimeError("evaluated_mesh_lease_missing")
        mesh = _evaluated_lease.mesh
        evaluated_mesh_source = str(_evaluated_lease.source or "evaluated")
        evaluated_instance_count = int(_evaluated_lease.instance_count or 0)
        evaluated_instances_realized = bool(_evaluated_lease.realized_instances)
        if mesh is None:
            raise ValueError("mesh_has_no_exportable_geometry")
    tri_ms = 0.0
    raw_ms = 0.0
    native_ms = 0.0
    fallback_ms = 0.0
    build_mode = "unknown"

    tri_start = time.perf_counter()
    mesh.calc_loop_triangles()
    tri_ms = (time.perf_counter() - tri_start) * 1000.0

    raw_start = time.perf_counter()
    try:
        raw = _collect_mesh_raw_buffers_for_preview(hooks, mesh, pair_id=pair_id, allow_edit_bmesh_uv=allow_edit_bmesh_uv)
    except Exception as exc:
        warn(
            "MeshContext",
            "raw_buffer_collection_failed",
            str(exc) or "raw_collection_failed",
            {"pairId": pair_id},
        )
        raw = None
    raw_ms = (time.perf_counter() - raw_start) * 1000.0

    if raw is not None and mesh_source == "evaluated":
        _, compacted_indices = compact_evaluated_material_slots(
            getattr(mesh, "materials", []) or [],
            raw.get("tri_material_indices"),
        )
        raw = dict(raw)
        raw["tri_material_indices"] = compacted_indices

    vertices = []
    triangles = []
    normals = []
    uv = []
    prebuilt_binary = None
    uv_channels = []
    color0 = None
    blend_shapes = []
    blend_shape_source_indices = None
    submeshes = []
    native_status = get_native_status()
    if raw is not None:
        if _raw_requires_python_multi_uv_dedupe(raw, native_status):
            fallback_start = time.perf_counter()
            (
                vertices,
                normals,
                uv,
                triangles,
                source_indices,
                source_loop_indices,
            ) = _build_mesh_arrays_from_raw_for_preview_detailed(raw)
            blend_shape_source_indices = source_indices
            uv_channels = _build_uv_channels_from_raw_for_preview(raw, source_loop_indices)
            color0 = _build_color0_from_raw_for_preview(raw, source_loop_indices, source_indices)
            submeshes = _build_material_submeshes_from_raw_for_preview(raw, triangles)
            runtime.source_indices_by_pair[pair_id] = array("i", source_indices)
            runtime.source_loop_indices_by_pair[pair_id] = array("i", source_loop_indices)
            runtime.hashes_by_pair.setdefault(pair_id, {}).pop("uv0", None)
            fallback_ms = (time.perf_counter() - fallback_start) * 1000.0
            build_mode = "foreach_get_python_multi_uv"
        else:
            binary_prefix = f"{hooks.safe_preview_name(pair_id)}_{int(time.time() * 1000)}_{os.getpid()}"
            native_start = time.perf_counter()
            native_binary_result = try_extract_mesh_binary_native(
                raw=raw,
                output_dir=str(hooks.preview_buffer_dir()),
                prefix=binary_prefix,
            )
            native_ms = (time.perf_counter() - native_start) * 1000.0
        if build_mode == "unknown" and native_binary_result and native_binary_result.get("ok") and native_binary_result.get("buffers"):
            source_indices = native_binary_result.get("sourceIndices") or array("i")
            source_loop_indices = native_binary_result.get("sourceLoopIndices") or array("i")
            blend_shape_source_indices = source_indices
            if _raw_preview_needs_submeshes(raw):
                _, _, _, submesh_indices = _build_mesh_arrays_from_raw_for_preview(raw)
                index_sha1 = _sha1_int_sequence(submesh_indices)
                native_hash_profile = native_binary_result.get("hashProfile") or (native_binary_result.get("profile") or {}).get("hashProfile") or {}
                native_index_sha1 = str(native_hash_profile.get("indexSha1") or "")
                if not native_index_sha1 or native_index_sha1 == index_sha1:
                    submeshes = _build_material_submeshes_from_raw_for_preview(raw, submesh_indices)
                    if submeshes:
                        native_binary_result["subMeshes"] = submeshes
                else:
                    warn(
                        "MeshContext",
                        "native_submesh_hash_mismatch",
                        "Native index data did not match the preview topology.",
                        {"pairId": pair_id},
                    )
            runtime.source_indices_by_pair[pair_id] = source_indices
            if len(source_loop_indices) == len(source_indices):
                runtime.source_loop_indices_by_pair[pair_id] = source_loop_indices
                _attach_uv_channels_to_prebuilt_binary(hooks, native_binary_result, raw, source_loop_indices, pair_id)
                color0 = _build_color0_from_raw_for_preview(raw, source_loop_indices, source_indices)
                _attach_color0_to_prebuilt_binary(hooks, native_binary_result, color0, pair_id)
            else:
                runtime.source_loop_indices_by_pair.pop(pair_id, None)
                native_binary_result["uvChannels"] = []
            prebuilt_binary = native_binary_result
            binary_profile = native_binary_result.get("profile") or {}
            hash_profile = native_binary_result.get("hashProfile") or binary_profile.get("hashProfile") or {}
            if isinstance(hash_profile, dict):
                hashes = runtime.hashes_by_pair.setdefault(pair_id, {})
                for src_key, dst_key in (
                    ("vertexSha1", "vertices"),
                    ("normalSha1", "normals"),
                    ("uv0Sha1", "uv0"),
                    ("indexSha1", "indices"),
                    ("sourceIndexSha1", "sourceIndices"),
                    ("sourceLoopIndexSha1", "sourceLoopIndices"),
                    ("topologySha1", "topology"),
                ):
                    if hash_profile.get(src_key):
                        hashes[dst_key] = str(hash_profile.get(src_key))
            else:
                source_hash = binary_profile.get("sourceIndexSha1")
                if source_hash:
                    runtime.hashes_by_pair.setdefault(pair_id, {})["sourceIndices"] = str(source_hash)
            runtime.hashes_by_pair.setdefault(pair_id, {}).pop("uv0", None)
            build_mode = "pyd_binary_v1"
        elif build_mode == "unknown":
            native_start = time.perf_counter()
            native_result = try_extract_mesh_arrays_native(raw=raw)
            native_ms = (time.perf_counter() - native_start) * 1000.0
            if native_result and native_result.get("ok") and native_result.get("vertices") is not None:
                vertices = native_result.get("vertices") or []
                triangles = native_result.get("indices") or []
                normals = native_result.get("normals") or []
                uv = native_result.get("uv0") or []
                source_indices = native_result.get("sourceIndices") or array("i")
                source_loop_indices = native_result.get("sourceLoopIndices") or array("i")
                blend_shape_source_indices = source_indices
                uv_channels = _build_uv_channels_from_raw_for_preview(raw, source_loop_indices)
                color0 = _build_color0_from_raw_for_preview(raw, source_loop_indices, source_indices)
                submeshes = _build_material_submeshes_from_raw_for_preview(raw, triangles)
                runtime.source_indices_by_pair[pair_id] = source_indices
                if len(source_loop_indices) == len(source_indices):
                    runtime.source_loop_indices_by_pair[pair_id] = source_loop_indices
                else:
                    runtime.source_loop_indices_by_pair.pop(pair_id, None)
                runtime.hashes_by_pair.setdefault(pair_id, {}).pop("uv0", None)
                build_mode = "pyd_accurate"
            else:
                fallback_start = time.perf_counter()
                (
                    vertices,
                    normals,
                    uv,
                    triangles,
                    source_indices,
                    source_loop_indices,
                ) = _build_mesh_arrays_from_raw_for_preview_detailed(raw)
                blend_shape_source_indices = source_indices
                uv_channels = _build_uv_channels_from_raw_for_preview(raw, source_loop_indices)
                color0 = _build_color0_from_raw_for_preview(raw, source_loop_indices, source_indices)
                submeshes = _build_material_submeshes_from_raw_for_preview(raw, triangles)
                runtime.source_indices_by_pair[pair_id] = array("i", source_indices)
                runtime.source_loop_indices_by_pair[pair_id] = array("i", source_loop_indices)
                runtime.hashes_by_pair.setdefault(pair_id, {}).pop("uv0", None)
                fallback_ms = (time.perf_counter() - fallback_start) * 1000.0
                build_mode = "foreach_get_python_reference"
    else:
        build_mode = "failed"

    now = time.time()
    vertex_count = int(prebuilt_binary.get("vertexCount") or 0) if isinstance(prebuilt_binary, dict) else len(vertices) // 3
    triangle_index_count = int(prebuilt_binary.get("indexCount") or 0) if isinstance(prebuilt_binary, dict) else len(triangles)
    if vertex_count <= 0 or triangle_index_count <= 0:
        runtime.source_indices_by_pair.pop(pair_id, None)
        runtime.source_loop_indices_by_pair.pop(pair_id, None)
        runtime.hashes_by_pair.pop(pair_id, None)
        raise ValueError("mesh_has_no_exportable_geometry")
    if mesh_source != "evaluated" and blend_shape_source_indices is not None:
        try:
            blend_shapes = build_mesh_blend_shapes(mesh, blend_shape_source_indices)
        except Exception as exc:
            log_exception(
                "MeshContext",
                "blend_shape_build_exception",
                exc,
                fields={"pairId": pair_id},
            )
            blend_shapes = []
    if isinstance(prebuilt_binary, dict):
        _attach_blend_shapes_to_prebuilt_binary(hooks, prebuilt_binary, blend_shapes, pair_id)
    if (now - runtime.last_mesh_diag_time) >= 1.0:
        native_profile = prebuilt_binary.get("profile") if isinstance(prebuilt_binary, dict) else None
        if native_profile is None:
            native_profile = native_result.get("profile") if isinstance(locals().get("native_result"), dict) else None
        trace(
            "MeshContext",
            "preview_mesh_built",
            lambda: "Built mesh data for an automatic preview.",
            lambda: {
                "mode": hooks.get_current_mode(),
                "pairId": pair_id,
                "buildMode": build_mode,
                "evaluatedSource": evaluated_mesh_source or "none",
                "realizedInstances": evaluated_instances_realized,
                "instanceCount": evaluated_instance_count,
                "sourceVertexCount": len(mesh.vertices),
                "loopCount": len(mesh.loops),
                "triangleCount": len(mesh.loop_triangles),
                "exportVertexCount": vertex_count,
                "indexCount": triangle_index_count,
                "blendShapeCount": (native_profile or {}).get("blendShapeCount", len(blend_shapes)),
                "nativeCallMs": round(native_ms, 2),
                "fallbackMs": round(fallback_ms, 2),
                "totalMs": round((time.perf_counter() - profile_start) * 1000.0, 2),
                "nativeAvailable": native_status.get("available"),
            },
        )
        runtime.last_mesh_diag_time = now

    mesh_content = {
        "vertices": vertices,
        "triangles": triangles,
    }
    mesh_content["blendShapes"] = blend_shapes
    if include_extras:
        mesh_content["normals"] = normals
        mesh_content["uv"] = uv
        if uv_channels:
            mesh_content["uvChannels"] = uv_channels
        if color0:
            mesh_content["color0"] = color0.get("values")
            mesh_content["colorAttributeName"] = color0.get("name") or "Color"
    if submeshes:
        mesh_content["subMeshes"] = submeshes
    send_unity_mesh_content_fingerprint = hooks.should_include_fingerprint(
        obj,
        force=force_mesh_content_fingerprint,
    )
    mesh_content_fingerprint = None
    mesh_content_fingerprint_no_uv = None
    local_mesh_content_fingerprint = None
    local_mesh_content_fingerprint_no_uv = None
    uv_unstable_for_fingerprint = (
        hooks.get_current_mode() == "EDIT_MESH"
        and isinstance(prebuilt_binary, dict)
        and int(prebuilt_binary.get("vertexCount") or 0) > 0
        and not bool(prebuilt_binary.get("uvChannels") or [])
    )
    if raw is not None and isinstance(prebuilt_binary, dict):
        local_mesh_content_fingerprint = compute_mesh_content_fingerprint(
            mesh_content,
            prebuilt_binary,
            include_uv=not uv_unstable_for_fingerprint,
        )
        local_mesh_content_fingerprint_no_uv = compute_mesh_content_fingerprint(
            mesh_content,
            prebuilt_binary,
            include_uv=False,
        )
        if send_unity_mesh_content_fingerprint:
            mesh_content_fingerprint = local_mesh_content_fingerprint
            mesh_content_fingerprint_no_uv = local_mesh_content_fingerprint_no_uv

    collect_refs = hooks.material_refs_for_export or hooks.collect_material_refs
    material_refs = collect_refs(
        obj,
        mesh=mesh,
        evaluated_mesh=mesh_source == "evaluated",
    ) if hooks.material_refs_for_export else collect_refs(obj)
    material_contents = []
    if hooks.material_contents_for_export:
        material_contents = hooks.material_contents_for_export(
            obj,
            mesh=mesh,
            evaluated_mesh=mesh_source == "evaluated",
            unknown_only=True,
        )
    context = {
        "session": session,
        "timestamp": int(time.time()),
        "source_hint": "auto_sync" if hooks.get_sync_enabled() else "manual_sync",
        "pair_entry": {
            "pairId": pair_id,
            "mapped": True,
            "syncEnabled": True,
        },
        "mesh_ref": mesh_ref,
        "material_refs": list(material_refs),
        "material_contents": material_contents,
        "mesh_content": mesh_content,
    }
    context["mesh_source"] = mesh_source
    context["mesh_source_reason"] = mesh_source_reason
    context["evaluated_mesh_source"] = evaluated_mesh_source
    context["evaluated_instance_count"] = evaluated_instance_count
    context["evaluated_instances_realized"] = evaluated_instances_realized
    context["send_mesh_content_fingerprint"] = send_unity_mesh_content_fingerprint
    if mesh_content_fingerprint:
        context["mesh_content_fingerprint"] = mesh_content_fingerprint
    if mesh_content_fingerprint_no_uv:
        context["mesh_content_fingerprint_no_uv"] = mesh_content_fingerprint_no_uv
    if uv_unstable_for_fingerprint:
        context["mesh_content_fingerprint"] = mesh_content_fingerprint_no_uv
        context["mesh_content_fingerprint_scope"] = "no_uv"
    if local_mesh_content_fingerprint:
        context["local_mesh_content_fingerprint"] = local_mesh_content_fingerprint
    if local_mesh_content_fingerprint_no_uv:
        context["local_mesh_content_fingerprint_no_uv"] = local_mesh_content_fingerprint_no_uv
    if uv_unstable_for_fingerprint:
        context["local_mesh_content_fingerprint_scope"] = "no_uv"
    context["mesh_content_fingerprint_debug"] = {
        "buildMode": build_mode,
        "evaluatedMeshSource": evaluated_mesh_source,
        "evaluatedInstanceCount": evaluated_instance_count,
        "evaluatedInstancesRealized": evaluated_instances_realized,
        "fingerprintSkipped": "edit_mesh_uv_unstable" if uv_unstable_for_fingerprint else "",
        "referenceFingerprint": mesh_content_fingerprint,
        "sourceVertexCount": len(mesh.vertices),
        "loopCount": len(mesh.loops),
        "triangleCount": len(mesh.loop_triangles),
        "exportVertexCount": vertex_count,
        "indexCount": triangle_index_count,
        "uvChannelCount": len(prebuilt_binary.get("uvChannels") or []) if isinstance(prebuilt_binary, dict) else len(uv_channels),
        "subMeshCount": len(prebuilt_binary.get("subMeshes") or []) if isinstance(prebuilt_binary, dict) else len(submeshes),
    }
    if rebuild_reason:
        context["rebuild_reason"] = str(rebuild_reason)
    if isinstance(prebuilt_binary, dict):
        context["prebuilt_binary"] = prebuilt_binary
    return context
