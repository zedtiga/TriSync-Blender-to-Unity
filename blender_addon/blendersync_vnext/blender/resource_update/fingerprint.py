from __future__ import annotations

import hashlib
import json
import struct
from array import array
from typing import Any


HASH_KEYS = (
    "vertexSha1",
    "normalSha1",
    "uv0Sha1",
    "uv1Sha1",
    "uv2Sha1",
    "uv3Sha1",
    "uv4Sha1",
    "uv5Sha1",
    "uv6Sha1",
    "uv7Sha1",
    "color0Sha1",
    "indexSha1",
)
BUFFER_ORDER = {
    "POSITION": 0,
    "INDEX": 1,
    "NORMAL": 2,
    "UV0": 3,
    "UV1": 4,
    "UV2": 5,
    "UV3": 6,
    "UV4": 7,
    "UV5": 8,
    "UV6": 9,
    "UV7": 10,
    "COLOR0": 11,
    "BLENDSHAPE_DELTA_POSITION": 12,
    "SUBMESH_INDEX": 13,
}


def compute_mesh_content_fingerprint(
    mesh_content: dict[str, Any] | None = None,
    prebuilt_binary: dict[str, Any] | None = None,
    *,
    vertices=None,
    triangles=None,
    normals=None,
    uv=None,
    color0=None,
    submeshes=None,
    include_uv: bool = True,
) -> str:
    h = hashlib.sha1()
    if isinstance(prebuilt_binary, dict):
        profile = prebuilt_binary.get("profile") or {}
        hash_profile = prebuilt_binary.get("hashProfile") or profile.get("hashProfile") or {}
        if isinstance(hash_profile, dict):
            ordered_hash_keys = list(HASH_KEYS)
            ordered_hash_keys.extend(
                key
                for key in sorted(hash_profile.keys())
                if key not in HASH_KEYS
                and str(key).endswith("Sha1")
                and (str(key).startswith("uv") or str(key).startswith("blendshape_") or str(key).startswith("submesh_"))
            )
            for key in ordered_hash_keys:
                if not include_uv and str(key).startswith("uv"):
                    continue
                value = hash_profile.get(key)
                if value:
                    h.update(str(key).encode("utf-8"))
                    h.update(str(value).encode("utf-8"))
        buffers = sorted(
            [b for b in (prebuilt_binary.get("buffers") or []) if isinstance(b, dict)],
            key=lambda b: (BUFFER_ORDER.get(str(b.get("semantic") or ""), 100), str(b.get("semantic") or "")),
        )
        for b in buffers:
            semantic = str(b.get("semantic") or "")
            if not include_uv and semantic.upper().startswith("UV"):
                continue
            h.update(semantic.encode("utf-8"))
            h.update(str(b.get("byteLength") or 0).encode("utf-8"))
        h.update(str(prebuilt_binary.get("vertexCount") or 0).encode("utf-8"))
        h.update(str(prebuilt_binary.get("indexCount") or 0).encode("utf-8"))
        h.update(json.dumps(_stable_prebuilt_submeshes(prebuilt_binary.get("subMeshes") or []), sort_keys=True, separators=(",", ":"), default=str).encode("utf-8"))
        h.update(json.dumps(_stable_prebuilt_blend_shapes(prebuilt_binary.get("blendShapes") or [], hash_profile), sort_keys=True, separators=(",", ":"), default=str).encode("utf-8"))
        return h.hexdigest()

    mesh_content = mesh_content or {}
    vertex_values = vertices if vertices is not None else mesh_content.get("vertices")
    index_values = triangles if triangles is not None else (mesh_content.get("triangles") or mesh_content.get("indices"))
    normal_values = normals if normals is not None else mesh_content.get("normals")
    uv_values = uv if uv is not None else (mesh_content.get("uv") if mesh_content.get("uv") is not None else mesh_content.get("uv0"))
    color_values = color0 if color0 is not None else mesh_content.get("color0")
    submesh_values = submeshes if submeshes is not None else mesh_content.get("subMeshes")

    if _try_update_canonical_mesh_hash(
        h,
        mesh_content,
        vertex_values,
        index_values,
        normal_values,
        uv_values if include_uv else None,
        color_values,
        submesh_values,
        include_uv=include_uv,
    ):
        return h.hexdigest()

    digests: dict[str, str] = {}
    buffer_meta: list[tuple[str, int]] = []

    def add_array_digest(key: str, semantic: str, value, typecode: str, required: bool = False) -> None:
        if isinstance(value, array):
            arr = value
        else:
            arr = array(typecode)
            if value:
                if typecode == "f":
                    arr.fromlist([float(v) for v in value])
                else:
                    arr.fromlist([int(v) for v in value])
        if len(arr) == 0 and not required:
            return
        digests[key] = hashlib.sha1(arr.tobytes()).hexdigest()
        buffer_meta.append((semantic, len(arr) * arr.itemsize))

    add_array_digest("vertexSha1", "POSITION", vertex_values, "f", required=True)
    add_array_digest("indexSha1", "INDEX", index_values, "i", required=True)
    add_array_digest("normalSha1", "NORMAL", normal_values, "f")
    if include_uv:
        add_array_digest("uv0Sha1", "UV0", uv_values, "f")
        for channel in mesh_content.get("uvChannels") or []:
            if not isinstance(channel, dict):
                continue
            try:
                channel_index = int(channel.get("index") or 0)
            except Exception:
                continue
            if channel_index == 0:
                if uv_values is None:
                    add_array_digest("uv0Sha1", "UV0", channel.get("values"), "f")
                continue
            if channel_index > 7:
                continue
            add_array_digest(f"uv{channel_index}Sha1", f"UV{channel_index}", channel.get("values"), "f")
    add_array_digest("color0Sha1", "COLOR0", color_values, "f")
    for index, shape in enumerate(mesh_content.get("blendShapes") or []):
        if not isinstance(shape, dict):
            continue
        add_array_digest(f"blendshape_{index}_delta_positionsSha1", "BLENDSHAPE_DELTA_POSITION", shape.get("deltaPositions"), "f")

    ordered_digest_keys = list(HASH_KEYS)
    ordered_digest_keys.extend(
        key
        for key in sorted(digests.keys())
        if key not in HASH_KEYS
        and str(key).endswith("Sha1")
        and (str(key).startswith("uv") or str(key).startswith("blendshape_"))
    )
    for key in ordered_digest_keys:
        value = digests.get(key)
        if value:
            h.update(key.encode("utf-8"))
            h.update(value.encode("utf-8"))
    for semantic, byte_length in buffer_meta:
        h.update(str(semantic).encode("utf-8"))
        h.update(str(byte_length).encode("utf-8"))
    h.update(str(len(vertex_values or []) // 3).encode("utf-8"))
    h.update(str(len(index_values or [])).encode("utf-8"))
    h.update(json.dumps(submesh_values or [], sort_keys=True, separators=(",", ":"), default=str).encode("utf-8"))
    return h.hexdigest()


def _stable_prebuilt_submeshes(submeshes) -> list[dict[str, Any]]:
    stable = []
    for submesh in submeshes or []:
        if not isinstance(submesh, dict):
            continue
        indices_buffer = submesh.get("indicesBuffer") if isinstance(submesh.get("indicesBuffer"), dict) else {}
        indices = submesh.get("indices")
        stable.append(
            {
                "materialSlot": int(submesh.get("materialSlot") or 0),
                "topology": str(submesh.get("topology") or "triangles"),
                "indexCount": int(indices_buffer.get("count") or (len(indices) if hasattr(indices, "__len__") else 0) or 0),
                "indexByteLength": int(indices_buffer.get("byteLength") or 0),
                "indexSha1": str(submesh.get("indexSha1") or ""),
            }
        )
    return stable


def _stable_prebuilt_blend_shapes(blend_shapes, hash_profile) -> list[dict[str, Any]]:
    stable = []
    hash_profile = hash_profile if isinstance(hash_profile, dict) else {}
    for index, shape in enumerate(blend_shapes or []):
        if not isinstance(shape, dict):
            continue
        delta_buffer = shape.get("deltaPositionsBuffer") if isinstance(shape.get("deltaPositionsBuffer"), dict) else {}
        stable.append(
            {
                "index": int(index),
                "name": str(shape.get("name") or ""),
                "frameWeight": float(shape.get("frameWeight") or 0.0),
                "vertexCount": int(shape.get("vertexCount") or delta_buffer.get("count") or 0),
                "deltaByteLength": int(delta_buffer.get("byteLength") or 0),
                "deltaSha1": str(hash_profile.get(f"blendshape_{index}_delta_positionsSha1") or ""),
            }
        )
    return stable


def _try_update_canonical_mesh_hash(
    h,
    mesh_content: dict[str, Any],
    vertex_values,
    index_values,
    normal_values,
    uv_values,
    color_values,
    submesh_values,
    *,
    include_uv: bool = True,
) -> bool:
    try:
        vertices = _float_list(vertex_values)
        indices = _int_list(index_values)
        if len(vertices) == 0 or len(vertices) % 3 != 0 or len(indices) == 0 or len(indices) % 3 != 0:
            return False

        vertex_count = len(vertices) // 3
        normals = _float_list(normal_values) if normal_values is not None else []
        if len(normals) != len(vertices):
            normals = []

        uv_channels = _canonical_uv_channels(mesh_content, uv_values, vertex_count) if include_uv else {}
        colors = _float_list(color_values) if color_values is not None else []
        if len(colors) != vertex_count * 4:
            colors = []

        vertex_records = [
            _canonical_vertex_record(vertices, normals, uv_channels, colors, index)
            for index in range(vertex_count)
        ]
        triangle_records = _canonical_triangle_records(indices, submesh_values, vertex_records)
        if not triangle_records:
            return False

        h.update(b"mesh_content_canonical_v1")
        h.update(_pack_int(vertex_count))
        h.update(_pack_int(len(triangle_records)))
        h.update(_pack_int(1 if normals else 0))
        h.update(_pack_int(len(uv_channels)))
        h.update(_pack_int(1 if colors else 0))
        for channel_index in sorted(uv_channels.keys()):
            h.update(_pack_int(channel_index))
        for record in sorted(triangle_records):
            h.update(record)
        _update_blend_shape_hash(h, mesh_content, vertex_count)
        return True
    except Exception:
        return False


def _canonical_uv_channels(mesh_content: dict[str, Any], uv_values, vertex_count: int) -> dict[int, list[float]]:
    channels: dict[int, list[float]] = {}
    uv0 = _float_list(uv_values) if uv_values is not None else []
    if len(uv0) == vertex_count * 2:
        channels[0] = uv0
    for channel in mesh_content.get("uvChannels") or []:
        if not isinstance(channel, dict):
            continue
        try:
            channel_index = int(channel.get("index") or 0)
        except Exception:
            continue
        if channel_index < 0 or channel_index > 7:
            continue
        values = _float_list(channel.get("values"))
        if len(values) == vertex_count * 2:
            channels[channel_index] = values
    return channels


def _canonical_vertex_record(vertices, normals, uv_channels: dict[int, list[float]], colors, index: int) -> bytes:
    chunks = [b"P"]
    chunks.append(_pack_float(vertices[index * 3]))
    chunks.append(_pack_float(vertices[index * 3 + 1]))
    chunks.append(_pack_float(vertices[index * 3 + 2]))
    if normals:
        chunks.append(b"N")
        chunks.append(_pack_float(normals[index * 3]))
        chunks.append(_pack_float(normals[index * 3 + 1]))
        chunks.append(_pack_float(normals[index * 3 + 2]))
    for channel_index in sorted(uv_channels.keys()):
        values = uv_channels[channel_index]
        chunks.append(b"U")
        chunks.append(_pack_int(channel_index))
        chunks.append(_pack_float(values[index * 2]))
        chunks.append(_pack_float(values[index * 2 + 1]))
    if colors:
        chunks.append(b"C")
        chunks.append(_pack_float(colors[index * 4]))
        chunks.append(_pack_float(colors[index * 4 + 1]))
        chunks.append(_pack_float(colors[index * 4 + 2]))
        chunks.append(_pack_float(colors[index * 4 + 3]))
    return b"".join(chunks)


def _canonical_triangle_records(indices, submesh_values, vertex_records: list[bytes]) -> list[bytes]:
    if submesh_values:
        records = []
        for submesh in submesh_values:
            if not isinstance(submesh, dict):
                continue
            try:
                material_slot = int(submesh.get("materialSlot") or 0)
            except Exception:
                material_slot = 0
            sub_indices = _int_list(submesh.get("indices"))
            records.extend(_canonical_triangle_records_for_indices(sub_indices, vertex_records, material_slot))
        if records:
            return records
    return _canonical_triangle_records_for_indices(indices, vertex_records, 0)


def _canonical_triangle_records_for_indices(indices, vertex_records: list[bytes], material_slot: int) -> list[bytes]:
    records = []
    vertex_count = len(vertex_records)
    for offset in range(0, len(indices) - 2, 3):
        i0 = int(indices[offset])
        i1 = int(indices[offset + 1])
        i2 = int(indices[offset + 2])
        if i0 < 0 or i1 < 0 or i2 < 0 or i0 >= vertex_count or i1 >= vertex_count or i2 >= vertex_count:
            continue
        a = vertex_records[i0]
        b = vertex_records[i1]
        c = vertex_records[i2]
        rotations = ((a, b, c), (b, c, a), (c, a, b))
        va, vb, vc = min(rotations)
        records.append(b"M" + _pack_int(material_slot) + b"T" + va + b"|" + vb + b"|" + vc)
    return records


def _update_blend_shape_hash(h, mesh_content: dict[str, Any], vertex_count: int) -> None:
    shapes = list(mesh_content.get("blendShapes") or [])
    h.update(_pack_int(len(shapes)))
    for shape in shapes:
        if not isinstance(shape, dict):
            continue
        h.update(str(shape.get("name") or "").encode("utf-8"))
        h.update(_pack_float(float(shape.get("frameWeight") or 100.0)))
        deltas = _float_list(shape.get("deltaPositions"))
        if len(deltas) == vertex_count * 3:
            h.update(hashlib.sha1(array("f", deltas).tobytes()).digest())


def _float_list(values) -> list[float]:
    if values is None:
        return []
    if isinstance(values, array):
        return [float(v) for v in values]
    return [float(v) for v in values]


def _int_list(values) -> list[int]:
    if values is None:
        return []
    if isinstance(values, array):
        return [int(v) for v in values]
    return [int(v) for v in values]


def _pack_float(value: float) -> bytes:
    return struct.pack("<f", float(value))


def _pack_int(value: int) -> bytes:
    return struct.pack("<i", int(value))
