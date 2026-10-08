from __future__ import annotations

import os
import hashlib
import struct
import tempfile
import time
from array import array
from pathlib import Path
from typing import Any

from blender.common.log import trace

_STALE_BUFFER_TTL_SECONDS = 24 * 60 * 60
_STALE_BUFFER_CLEANUP_INTERVAL_SECONDS = 60 * 60
_last_stale_buffer_cleanup_at = 0.0


def _safe_name(value: str | None) -> str:
    raw = str(value or "mesh").strip() or "mesh"
    return "".join(ch if ch.isalnum() or ch in ("-", "_", ".") else "_" for ch in raw)[:80]


def _buffer_dir() -> Path:
    root = Path(tempfile.gettempdir()) / "BlenderSyncVNext" / "binary_v1"
    root.mkdir(parents=True, exist_ok=True)
    _cleanup_stale_buffers(root)
    return root


def _cleanup_stale_buffers(root: Path) -> None:
    global _last_stale_buffer_cleanup_at
    now = time.time()
    if (now - _last_stale_buffer_cleanup_at) < _STALE_BUFFER_CLEANUP_INTERVAL_SECONDS:
        return
    _last_stale_buffer_cleanup_at = now
    cutoff = now - _STALE_BUFFER_TTL_SECONDS
    removed = 0
    try:
        for path in root.glob("*.bin"):
            try:
                if path.stat().st_mtime < cutoff:
                    path.unlink()
                    removed += 1
            except Exception:
                continue
    except Exception:
        return
    if removed:
        trace(
            "MeshBinary",
            "stale_buffers_removed",
            lambda: "Removed stale mesh binary buffers.",
            lambda: {"removedCount": removed},
        )


def _write_array(path: Path, values: array) -> int:
    with path.open("wb") as f:
        values.tofile(f)
    return path.stat().st_size


def _pack_float_array(values: list | tuple | array | None) -> array:
    if isinstance(values, array) and values.typecode == "f":
        return values
    arr = array("f")
    if values:
        arr.fromlist(values if isinstance(values, list) else [float(v) for v in values])
    return arr


def _pack_int_array(values: list | tuple | array | None) -> array:
    if isinstance(values, array) and values.typecode == "i":
        return values
    arr = array("i")
    if values:
        arr.fromlist(values if isinstance(values, list) else [int(v) for v in values])
    return arr


def _is_array_like(value: Any) -> bool:
    return isinstance(value, (list, tuple, array))


def build_mesh_binary_buffers_from_content(mesh_content: dict[str, Any], *, pair_id: str, mesh_ref: str | None = None, fallback_json_bytes: int | None = None) -> dict[str, Any] | None:
    vertices = mesh_content.get("vertices")
    triangles = mesh_content.get("triangles") or mesh_content.get("indices")
    if not pair_id or not _is_array_like(vertices) or not _is_array_like(triangles):
        return None
    if len(vertices) % 3 != 0 or len(triangles) % 3 != 0:
        return None

    normals = mesh_content.get("normals") if _is_array_like(mesh_content.get("normals")) else None
    uv = mesh_content.get("uv") if _is_array_like(mesh_content.get("uv")) else None
    if uv is None:
        uv = mesh_content.get("uv0") if _is_array_like(mesh_content.get("uv0")) else None
    if normals is not None and len(normals) != len(vertices):
        normals = None
    if uv is not None and len(uv) != (len(vertices) // 3) * 2:
        uv = None

    stamp = int(time.time() * 1000)
    base = f"{_safe_name(pair_id)}_{stamp}_{os.getpid()}"
    root = _buffer_dir()

    t0 = time.perf_counter()
    vertex_arr = _pack_float_array(vertices)
    triangle_arr = _pack_int_array(triangles)
    normal_arr = _pack_float_array(normals) if normals is not None else None
    uv_arr = _pack_float_array(uv) if uv is not None else None
    color0 = mesh_content.get("color0") if _is_array_like(mesh_content.get("color0")) else None
    if color0 is not None and len(color0) != (len(vertices) // 3) * 4:
        color0 = None

    uv_channel_arrays: list[dict[str, Any]] = []
    for channel in list(mesh_content.get("uvChannels") or [])[:8]:
        if not isinstance(channel, dict):
            continue
        values = channel.get("values")
        if not _is_array_like(values) or len(values) != (len(vertices) // 3) * 2:
            continue
        channel_index = int(channel.get("index") or 0)
        if channel_index < 0 or channel_index > 7:
            continue
        uv_channel_arrays.append({
            "index": channel_index,
            "name": str(channel.get("name") or f"UV{channel_index}"),
            "values": _pack_float_array(values),
            "sha1": str(channel.get("sha1") or ""),
        })
    if uv_arr is not None and not any(int(ch.get("index") or 0) == 0 for ch in uv_channel_arrays):
        uv_channel_arrays.insert(0, {"index": 0, "name": "UV0", "values": uv_arr})
    pack_ms = (time.perf_counter() - t0) * 1000.0

    buffers: list[dict[str, Any]] = []
    total_bytes = 0
    hash_profile: dict[str, str] = {}
    precomputed_hash_profile = mesh_content.get("_binaryHashProfile") if isinstance(mesh_content.get("_binaryHashProfile"), dict) else {}
    write_detail: dict[str, float | int] = {}
    hash_reused = 0
    hash_computed = 0

    def record_buffer_profile(category: str, byte_length: int, write_ms: float, hash_ms: float) -> None:
        prefix = str(category or "other")
        write_detail[f"{prefix}WriteMs"] = round(float(write_detail.get(f"{prefix}WriteMs", 0.0) or 0.0) + write_ms, 3)
        write_detail[f"{prefix}HashMs"] = round(float(write_detail.get(f"{prefix}HashMs", 0.0) or 0.0) + hash_ms, 3)
        write_detail[f"{prefix}Bytes"] = int(write_detail.get(f"{prefix}Bytes", 0) or 0) + int(byte_length or 0)

    def buffer_category(semantic: str) -> str:
        value = str(semantic or "").upper()
        if value == "POSITION":
            return "position"
        if value == "INDEX":
            return "index"
        if value == "NORMAL":
            return "normal"
        if value == "SUBMESH_INDEX":
            return "submesh"
        if value.startswith("UV"):
            return "uv"
        if value == "COLOR0":
            return "color"
        if value.startswith("BLENDSHAPE_"):
            return "blendShape"
        return "other"

    def hash_key_for_buffer(name: str, semantic: str) -> str | None:
        value = str(semantic or "").upper()
        if value == "POSITION":
            return "vertexSha1"
        if value == "NORMAL":
            return "normalSha1"
        if value == "INDEX":
            return "indexSha1"
        if value == "UV0" or str(name or "").lower() == "uv0":
            return "uv0Sha1"
        return None

    def add_buffer(name: str, semantic: str, fmt: str, components: int, count: int, arr: array, digest_override: str | None = None) -> dict[str, Any]:
        nonlocal total_bytes, hash_reused, hash_computed
        path = root / f"{base}_{name}.bin"
        write_start = time.perf_counter()
        byte_length = _write_array(path, arr)
        write_segment_ms = (time.perf_counter() - write_start) * 1000.0
        total_bytes += byte_length
        hash_segment_ms = 0.0
        digest_key = hash_key_for_buffer(name, semantic)
        digest = str(digest_override or "").strip()
        if not digest and digest_key:
            digest = str(precomputed_hash_profile.get(digest_key) or "").strip()
        if digest:
            hash_reused += 1
        else:
            hash_start = time.perf_counter()
            digest = hashlib.sha1(arr.tobytes()).hexdigest()
            hash_segment_ms = (time.perf_counter() - hash_start) * 1000.0
            hash_computed += 1
        record_buffer_profile(buffer_category(semantic), byte_length, write_segment_ms, hash_segment_ms)
        if semantic == "POSITION":
            hash_profile["vertexSha1"] = digest
        elif semantic == "NORMAL":
            hash_profile["normalSha1"] = digest
        elif semantic == "INDEX":
            hash_profile["indexSha1"] = digest
        elif semantic == "SUBMESH_INDEX":
            hash_profile[f"{name}Sha1"] = digest
        elif semantic.startswith("UV"):
            hash_profile[f"{semantic.lower()}Sha1"] = digest
        elif semantic == "COLOR0":
            hash_profile["color0Sha1"] = digest
        elif semantic == "BLENDSHAPE_DELTA_POSITION":
            parts = str(name or "").split("_", 2)
            key = f"blendshape_{parts[1]}_delta_positionsSha1" if len(parts) >= 2 and parts[0] == "blendshape" else f"{name}Sha1"
            hash_profile[key] = digest
        buffer = {
            "semantic": semantic,
            "format": fmt,
            "components": components,
            "count": count,
            "path": str(path),
            "byteLength": byte_length,
        }
        buffers.append(buffer)
        return buffer

    t1 = time.perf_counter()
    v_count = len(vertices) // 3
    add_buffer("positions", "POSITION", "float32", 3, v_count, vertex_arr)
    add_buffer("indices", "INDEX", "int32", 1, len(triangles), triangle_arr)
    if normal_arr is not None:
        add_buffer("normals", "NORMAL", "float32", 3, v_count, normal_arr)
    uv_binary_bytes = 0
    uv_channel_meta: list[dict[str, Any]] = []
    written_uv_indices: set[int] = set()
    for channel in uv_channel_arrays:
        channel_index = int(channel.get("index") or 0)
        if channel_index in written_uv_indices:
            continue
        written_uv_indices.add(channel_index)
        semantic = f"UV{channel_index}"
        uv_buffer = add_buffer(f"uv{channel_index}", semantic, "float32", 2, v_count, channel.get("values"), str(channel.get("sha1") or ""))
        uv_binary_bytes += int(uv_buffer.get("byteLength") or 0)
        uv_channel_meta.append({"index": channel_index, "name": channel.get("name") or semantic, "buffer": uv_buffer})

    color0_buffer = None
    color_binary_bytes = 0
    if color0 is not None:
        color0_arr = _pack_float_array(color0)
        color0_buffer = add_buffer("color0", "COLOR0", "float32", 4, v_count, color0_arr)
        color_binary_bytes = int(color0_buffer.get("byteLength") or 0)

    binary_submeshes: list[dict[str, Any]] = []
    submesh_binary_bytes = 0
    for idx, submesh in enumerate(mesh_content.get("subMeshes") or []):
        if not isinstance(submesh, dict):
            continue
        submesh_indices = submesh.get("indices")
        if not _is_array_like(submesh_indices) or len(submesh_indices) == 0:
            continue
        index_arr = _pack_int_array(submesh_indices)
        index_sha1 = str(submesh.get("indexSha1") or "").strip()
        index_buffer = add_buffer(
            f"submesh_{idx}_indices",
            "SUBMESH_INDEX",
            "int32",
            1,
            len(index_arr),
            index_arr,
            index_sha1,
        )
        if not index_sha1:
            index_sha1 = str(hash_profile.get(f"submesh_{idx}_indicesSha1") or "")
        submesh_binary_bytes += int(index_buffer.get("byteLength") or 0)
        packed_submesh = dict(submesh)
        packed_submesh["indicesBuffer"] = index_buffer
        packed_submesh["indices"] = []
        packed_submesh["indexSha1"] = index_sha1
        binary_submeshes.append(packed_submesh)

    blend_shape_pack_start = time.perf_counter()
    binary_blend_shapes: list[dict[str, Any]] = []
    blend_shape_binary_bytes = 0
    for idx, shape in enumerate(mesh_content.get("blendShapes") or []):
        if not isinstance(shape, dict):
            continue
        deltas = shape.get("deltaPositions")
        if not _is_array_like(deltas) or len(deltas) != v_count * 3:
            continue
        delta_arr = _pack_float_array(deltas)
        delta_buffer = add_buffer(
            f"blendshape_{idx}_{_safe_name(shape.get('name'))}_delta_positions",
            "BLENDSHAPE_DELTA_POSITION",
            "float32",
            3,
            v_count,
            delta_arr,
        )
        blend_shape_binary_bytes += int(delta_buffer.get("byteLength") or 0)
        packed_shape = dict(shape)
        packed_shape.pop("deltaPositions", None)
        packed_shape["deltaPositionsBuffer"] = delta_buffer
        packed_shape["vertexCount"] = v_count
        binary_blend_shapes.append(packed_shape)
    blend_shape_pack_ms = (time.perf_counter() - blend_shape_pack_start) * 1000.0
    write_ms = (time.perf_counter() - t1) * 1000.0
    write_detail["hashReused"] = hash_reused
    write_detail["hashComputed"] = hash_computed

    return {
        "meshRef": str(mesh_ref or "").strip() or None,
        "vertexCount": v_count,
        "indexCount": len(triangles),
        "buffers": buffers,
        "subMeshes": binary_submeshes if binary_submeshes else list(mesh_content.get("subMeshes") or []),
        "blendShapes": binary_blend_shapes,
        "uvChannels": uv_channel_meta,
        "color0": color0_buffer,
        "colorAttributeName": str(mesh_content.get("colorAttributeName") or "") if color0_buffer is not None else "",
        "profile": {
            "packMs": round(pack_ms, 3),
            "writeMs": round(write_ms, 3),
            "writeDetail": write_detail,
            "binaryBytes": total_bytes,
            "fallbackJsonBytes": int(fallback_json_bytes or 0),
            "blendShapeCount": len(binary_blend_shapes),
            "blendShapePackMs": round(blend_shape_pack_ms, 3),
            "blendShapeBinaryBytes": blend_shape_binary_bytes,
            "uvChannelCount": len(uv_channel_meta),
            "uvBinaryBytes": uv_binary_bytes,
            "colorAttributeCount": 1 if color0_buffer is not None else 0,
            "colorBinaryBytes": color_binary_bytes,
            "subMeshCount": len(binary_submeshes) if binary_submeshes else len(mesh_content.get("subMeshes") or []),
            "subMeshBinaryBytes": submesh_binary_bytes,
            "hashProfile": hash_profile,
        },
        "hashProfile": hash_profile,
    }


def build_mesh_update_binary_payload(context: dict[str, Any], *, fallback_json_bytes: int | None = None) -> dict[str, Any] | None:
    """Build a file-backed binary mesh update manifest.

    First iteration intentionally consumes the existing mesh_content lists so we can
    measure transport/Unity decode improvement without changing mesh extraction yet.
    Later this should move upstream to foreach_get / direct binary generation.
    """
    pair_entry = context.get("pair_entry") or {}
    pair_id = pair_entry.get("pairId")
    mesh_content = context.get("mesh_content") or {}
    binary = build_mesh_binary_buffers_from_content(
        mesh_content,
        pair_id=pair_id,
        mesh_ref=str(context.get("mesh_ref") or "").strip() or None,
        fallback_json_bytes=fallback_json_bytes,
    )
    if binary is None:
        return None

    payload = {
        "type": "scene_sync.mesh_update_binary_v1",
        "timestamp": int(context.get("timestamp", 0) or time.time()),
        "pairId": pair_id,
        "sourceHint": str(context.get("source_hint") or "").strip() or None,
        "meshRef": binary.get("meshRef"),
        "meshContentFingerprint": str(context.get("mesh_content_fingerprint") or context.get("meshContentFingerprint") or "").strip() or None,
        "meshContentFingerprintNoUv": str(context.get("mesh_content_fingerprint_no_uv") or context.get("meshContentFingerprintNoUv") or "").strip() or None,
        "meshContentFingerprintScope": str(context.get("mesh_content_fingerprint_scope") or context.get("meshContentFingerprintScope") or "").strip() or None,
        "materialRefs": list(context.get("material_refs") or []),
        "vertexCount": binary.get("vertexCount"),
        "indexCount": binary.get("indexCount"),
        "buffers": binary.get("buffers") or [],
        "subMeshes": binary.get("subMeshes") or [],
        "blendShapes": binary.get("blendShapes") or [],
        "uvChannels": binary.get("uvChannels") or [],
        "uvChannelCount": len(binary.get("uvChannels") or []),
        "uvChannelNames": [str(ch.get("name") or f"UV{ch.get('index')}") for ch in (binary.get("uvChannels") or []) if isinstance(ch, dict)],
        "color0": binary.get("color0"),
        "colorAttributeName": binary.get("colorAttributeName") or "",
        "profile": binary.get("profile") or {},
    }
    return payload
