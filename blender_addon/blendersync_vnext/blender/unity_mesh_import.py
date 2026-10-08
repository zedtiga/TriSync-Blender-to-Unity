from __future__ import annotations

import json
import math
import os
import sys
import threading
import time
import zlib
from array import array
from typing import Any

from blender.common.exception_boundary import report_boundary_exception
from blender.common.log import exception as log_exception, info, trace, warn
from blender.session.protocol_contract import UNITY_MESH_IMPORT_RESULT_FEATURE

try:
    import bpy  # type: ignore
except ImportError:
    bpy = None

_STAGING_ROOT_PARTS = ("Temp", "BlenderSyncVNext", "UnityMeshImportsV1")
_MAX_STAGED_BINARY_BYTES = 512 * 1024 * 1024
_MAX_STAGED_MANIFEST_BYTES = 64 * 1024 * 1024
_MAX_STAGED_JSON_BYTES = 512 * 1024 * 1024
_MAX_MESHES_PER_IMPORT = 1024
_MAX_MESH_VERTICES = 20_000_000
_MAX_MESH_INDICES = 120_000_000
_MAX_BLEND_SHAPES = 1024
_MAX_MATERIAL_SLOTS = 4096
_MAX_RESULT_WARNINGS = 128
_pending_lock = threading.RLock()
_pending_payloads: list[dict[str, Any]] = []
_timer_registered = False


def import_unity_mesh_payload(payload: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(payload, dict):
        return {"ok": False, "reason": "payload_not_dict"}
    meshes = payload.get("meshes")
    if not isinstance(meshes, list) or not meshes:
        return {"ok": False, "reason": "meshes_empty"}
    if len(meshes) > _MAX_MESHES_PER_IMPORT:
        return {"ok": False, "reason": f"meshes_too_many count={len(meshes)}"}

    with _pending_lock:
        _pending_payloads.append(payload)
        pending = len(_pending_payloads)

    if not _ensure_timer():
        _remove_pending_payload(payload)
        return {"ok": False, "reason": "import_timer_unavailable", "pending": pending_message_count()}
    return {"ok": True, "queued": len(meshes), "pending": pending}


def import_unity_mesh_file_payload(payload: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(payload, dict):
        return {"ok": False, "reason": "payload_not_dict"}
    path = str(payload.get("payloadPath") or "").strip()
    if not path:
        return {"ok": False, "reason": "payload_path_empty"}

    with _pending_lock:
        _pending_payloads.append(payload)
        pending = len(_pending_payloads)

    if not _ensure_timer():
        _remove_pending_payload(payload)
        return {"ok": False, "reason": "import_timer_unavailable", "pending": pending_message_count()}
    return {"ok": True, "queued": "file", "pending": pending, "bytes": payload.get("payloadBytes")}


def import_unity_mesh_binary_file_payload(payload: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(payload, dict):
        return {"ok": False, "reason": "payload_not_dict"}
    manifest_path = str(payload.get("manifestPath") or "").strip()
    binary_path = str(payload.get("binaryPath") or "").strip()
    if not manifest_path:
        return {"ok": False, "reason": "manifest_path_empty"}
    if not binary_path:
        return {"ok": False, "reason": "binary_path_empty"}

    with _pending_lock:
        _pending_payloads.append(payload)
        pending = len(_pending_payloads)

    if not _ensure_timer():
        _remove_pending_payload(payload)
        return {"ok": False, "reason": "import_timer_unavailable", "pending": pending_message_count()}
    return {"ok": True, "queued": "binary_file", "pending": pending, "bytes": payload.get("binaryBytes")}


def send_import_result(
    status: str,
    message: str,
    *,
    created: int = 0,
    skipped: int = 0,
    warnings: list[str] | None = None,
    error: str | None = None,
    session=None,
) -> dict[str, Any]:
    """Send the terminal/queue state for the one-shot Unity mesh creation flow."""
    payload = {
        "type": "unity_mesh.import_result_v1",
        "status": str(status or "failed"),
        "message": str(message or ""),
        "error": str(error) if error else None,
        "created": max(0, int(created or 0)),
        "skipped": max(0, int(skipped or 0)),
        "warnings": _normalize_warnings(warnings),
        "timestamp": int(time.time()),
    }
    try:
        if session is None:
            from blender.ui.state_view import get_session

            session = get_session()
        if not session.is_feature_negotiated(UNITY_MESH_IMPORT_RESULT_FEATURE):
            trace(
                "UnityMeshImport",
                "result_not_negotiated",
                lambda: "Skipped the mesh-import result because the peer does not support it.",
                lambda: {"feature": UNITY_MESH_IMPORT_RESULT_FEATURE},
            )
            return {
                "ok": True,
                "skipped": True,
                "reason": "feature_not_negotiated",
                "error": None,
                "payload": payload,
            }
        result = session.send_auto(payload)
        fields = {"status": payload["status"]}
        if result.ok:
            trace(
                "UnityMeshImport",
                "result_sent",
                lambda: "Sent the mesh-import result to Unity.",
                lambda: fields,
            )
        else:
            warn(
                "UnityMeshImport",
                "result_send_failed",
                result.error or "send_failed",
                fields,
            )
        return {"ok": bool(result.ok), "error": result.error, "payload": payload}
    except Exception as exc:
        log_exception(
            "UnityMeshImport",
            "result_send_exception",
            exc,
            fields={"status": payload["status"]},
        )
        return {"ok": False, "error": str(exc), "payload": payload}


def _ensure_timer() -> bool:
    global _timer_registered
    if bpy is None:
        _timer_registered = False
        return False
    if is_import_timer_registered():
        _timer_registered = True
        return True
    try:
        bpy.app.timers.register(_pump_pending_imports, first_interval=0.01, persistent=False)
        _timer_registered = is_import_timer_registered()
    except Exception as exc:
        _timer_registered = False
        log_exception("UnityMeshImport", "timer_register_exception", exc)
    return _timer_registered


def pending_message_count() -> int:
    with _pending_lock:
        return len(_pending_payloads)


def _remove_pending_payload(payload: dict[str, Any]) -> None:
    with _pending_lock:
        for index in range(len(_pending_payloads) - 1, -1, -1):
            if _pending_payloads[index] is payload:
                del _pending_payloads[index]
                break


def is_import_timer_registered() -> bool:
    if bpy is None:
        return False
    try:
        return bool(bpy.app.timers.is_registered(_pump_pending_imports))
    except Exception:
        return False


def reset_file_runtime_state(reason: str = "file_load") -> dict[str, Any]:
    global _timer_registered

    timer_was_registered = is_import_timer_registered()
    if timer_was_registered:
        try:
            bpy.app.timers.unregister(_pump_pending_imports)
        except Exception as exc:
            log_exception(
                "UnityMeshImport",
                "timer_unregister_exception",
                exc,
                fields={"reason": reason},
            )

    with _pending_lock:
        pending = list(_pending_payloads)
        _pending_payloads.clear()
    _timer_registered = is_import_timer_registered()

    for payload in pending:
        _cleanup_pending_file(payload)

    if pending or timer_was_registered:
        trace(
            "UnityMeshImport",
            "runtime_reset",
            lambda: "Reset pending Unity mesh imports.",
            lambda: {
                "reason": reason,
                "clearedCount": len(pending),
                "timerWasRegistered": timer_was_registered,
            },
        )
    return {
        "cleared": len(pending),
        "timerWasRegistered": timer_was_registered,
        "timerRegistered": _timer_registered,
    }


def _pump_pending_imports():
    global _timer_registered
    with _pending_lock:
        payload = _pending_payloads.pop(0) if _pending_payloads else None

    if payload is None:
        _timer_registered = False
        return None

    try:
        import_payload = _load_pending_payload(payload)
        result = _import_payload_now(import_payload)
        send_import_result(
            result["status"],
            result["message"],
            created=result["created"],
            skipped=result["skipped"],
            warnings=result["warnings"],
            error=result.get("error"),
        )
    except Exception as exc:
        report_boundary_exception(
            "unity_mesh_import_pump",
            exc,
            message=f"[vNext][UnityMeshImport] ERROR error={exc}",
        )
        send_import_result(
            "failed",
            "Blender could not read or create the selected mesh.",
            error=str(exc),
            warnings=_normalize_warnings(payload.get("warnings") if isinstance(payload, dict) else None),
        )
    finally:
        _cleanup_pending_file(payload)

    with _pending_lock:
        has_more = bool(_pending_payloads)
    if has_more:
        return 0.01

    _timer_registered = False
    return None


def _import_payload_now(payload: dict[str, Any]) -> dict[str, Any]:
    meshes = payload.get("meshes") if isinstance(payload, dict) else []
    if not isinstance(meshes, list):
        meshes = []

    created = 0
    skipped = 0
    warnings = _normalize_warnings(payload.get("warnings") if isinstance(payload, dict) else None)
    started = time.perf_counter()
    for mesh_payload in meshes:
        if not isinstance(mesh_payload, dict):
            skipped += 1
            continue
        try:
            obj = _create_mesh_object(mesh_payload, result_warnings=warnings)
            if obj is None:
                skipped += 1
            else:
                created += 1
        except Exception as exc:
            skipped += 1
            name = mesh_payload.get("objectName") or mesh_payload.get("meshName") or "(unnamed)"
            warnings.append(f"SKIP mesh={_safe_name(name, '(unnamed)')} reason={exc}")
            trace(
                "UnityMeshImport",
                "mesh_skipped",
                lambda: "Skipped one invalid mesh from an import request.",
                lambda: {"objectName": name, "reason": str(exc)},
            )

    elapsed_ms = (time.perf_counter() - started) * 1000.0
    warnings = _normalize_warnings(warnings)
    result_fields = {
        "createdCount": created,
        "skippedCount": skipped,
        "warningCount": len(warnings),
        "elapsedMs": round(elapsed_ms, 2),
    }
    if created <= 0:
        warn(
            "UnityMeshImport",
            "import_failed",
            warnings[0] if warnings else "Blender did not create any mesh objects.",
            result_fields,
        )
    elif skipped or warnings:
        warn(
            "UnityMeshImport",
            "import_completed_with_warnings",
            warnings[0] if warnings else "Created Unity meshes with partial results.",
            result_fields,
        )
    else:
        info(
            "UnityMeshImport",
            "import_completed",
            "Created Unity meshes in Blender.",
            result_fields,
        )
    if created <= 0:
        return {
            "status": "failed",
            "message": "Blender did not create any mesh objects.",
            "error": "no_mesh_objects_created",
            "created": 0,
            "skipped": skipped,
            "warnings": warnings,
        }
    if skipped > 0 or warnings:
        return {
            "status": "partial",
            "message": f"Created {created} object(s) in Blender with {len(warnings)} warning(s).",
            "error": None,
            "created": created,
            "skipped": skipped,
            "warnings": warnings,
        }
    return {
        "status": "imported",
        "message": f"Created {created} object(s) in Blender.",
        "error": None,
        "created": created,
        "skipped": 0,
        "warnings": [],
    }


def _load_pending_payload(payload: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise ValueError("payload_not_dict")
    if payload.get("type") != "unity_mesh.import_file_v1":
        if payload.get("type") == "unity_mesh.import_binary_file_v1":
            return _load_binary_payload(payload)
        return payload

    path = str(payload.get("payloadPath") or "").strip()
    if not path:
        raise ValueError("payload_path_empty")
    _validate_staged_file(
        path,
        ".json",
        _MAX_STAGED_JSON_BYTES,
        "payload",
        payload.get("payloadBytes"),
    )
    with open(path, "r", encoding="utf-8") as handle:
        loaded = json.load(handle)
    if not isinstance(loaded, dict):
        raise ValueError("staged_payload_not_dict")
    if payload.get("warnings"):
        loaded["warnings"] = _normalize_warnings(payload.get("warnings"))
    trace(
        "UnityMeshImport",
        "staged_payload_read",
        lambda: "Read a staged Unity mesh payload.",
        lambda: {"byteCount": payload.get("payloadBytes"), "meshCount": payload.get("meshCount")},
    )
    return loaded


def _load_binary_payload(payload: dict[str, Any]) -> dict[str, Any]:
    manifest_path = str(payload.get("manifestPath") or "").strip()
    binary_path = str(payload.get("binaryPath") or "").strip()
    if not manifest_path:
        raise ValueError("manifest_path_empty")
    if not binary_path:
        raise ValueError("binary_path_empty")

    _validate_staged_binary_paths(payload)

    with open(manifest_path, "r", encoding="utf-8") as handle:
        manifest = json.load(handle)
    if not isinstance(manifest, dict):
        raise ValueError("binary_manifest_not_dict")
    if str(manifest.get("schema") or "") != "unity_mesh_binary_v1":
        raise ValueError("binary_manifest_schema_unsupported")

    meshes = manifest.get("meshes")
    if not isinstance(meshes, list):
        raise ValueError("binary_manifest_meshes_missing")
    if len(meshes) > _MAX_MESHES_PER_IMPORT:
        raise ValueError(f"binary_manifest_meshes_too_many count={len(meshes)}")

    materialized = []
    binary_size = os.path.getsize(binary_path)
    if binary_size > _MAX_STAGED_BINARY_BYTES:
        raise ValueError(f"binary_file_too_large bytes={binary_size}")
    with open(binary_path, "rb") as binary:
        for mesh in meshes:
            if not isinstance(mesh, dict):
                continue
            materialized.append(_materialize_binary_mesh(binary, mesh, binary_size))

    expected_mesh_count = payload.get("meshCount")
    if expected_mesh_count is not None:
        expected_mesh_count = _read_bounded_int(
            expected_mesh_count,
            "binary_mesh_count",
            minimum=0,
            maximum=_MAX_MESHES_PER_IMPORT,
        )
        if expected_mesh_count != len(materialized):
            raise ValueError(
                f"binary_mesh_count_mismatch expected={expected_mesh_count} actual={len(materialized)}"
            )

    trace(
        "UnityMeshImport",
        "staged_binary_read",
        lambda: "Read a staged binary Unity mesh payload.",
        lambda: {"byteCount": payload.get("binaryBytes"), "meshCount": len(materialized)},
    )
    return {
        "type": "unity_mesh.import_v1",
        "source": "unity_editor_binary_staging",
        "meshes": materialized,
        "warnings": _normalize_warnings(payload.get("warnings")),
    }


def _materialize_binary_mesh(binary, mesh: dict[str, Any], binary_size: int) -> dict[str, Any]:
    result = {
        "meshName": mesh.get("meshName"),
        "objectName": mesh.get("objectName"),
        "sourceKind": mesh.get("sourceKind"),
        "sourceObjectPath": mesh.get("sourceObjectPath"),
        "assetPath": mesh.get("assetPath"),
        "assetGuid": mesh.get("assetGuid"),
        "assetLocalId": mesh.get("assetLocalId"),
        "vertexCount": mesh.get("vertexCount"),
        "indexCount": mesh.get("indexCount"),
        "materialNames": mesh.get("materialNames") if isinstance(mesh.get("materialNames"), list) else [],
        "vertices": _read_float_buffer(
            binary, mesh.get("vertices"), binary_size, required=True, expected_components=3
        ),
        "normals": _read_float_buffer(
            binary, mesh.get("normals"), binary_size, expected_components=3
        ),
        "colors": _read_float_buffer(
            binary, mesh.get("colors"), binary_size, expected_components=4
        ),
        "uvChannels": [],
        "subMeshes": [],
        "blendShapes": [],
    }

    uv_channels = mesh.get("uvChannels")
    if isinstance(uv_channels, list):
        for channel in uv_channels:
            if not isinstance(channel, dict):
                continue
            result["uvChannels"].append(
                {
                    "index": channel.get("index"),
                    "name": str(channel.get("name") or ""),
                    "values": _read_float_buffer(
                        binary,
                        channel.get("buffer"),
                        binary_size,
                        required=True,
                        expected_components=2,
                    ),
                }
            )

    submeshes = mesh.get("subMeshes")
    if isinstance(submeshes, list):
        for submesh in submeshes:
            if not isinstance(submesh, dict):
                continue
            result["subMeshes"].append(
                {
                    "materialSlot": submesh.get("materialSlot"),
                    "materialName": str(submesh.get("materialName") or ""),
                    "topology": str(submesh.get("topology") or "triangles"),
                    "indices": _read_int_buffer(
                        binary,
                        submesh.get("indices"),
                        binary_size,
                        required=True,
                        expected_components=1,
                    ),
                }
            )

    blend_shapes = mesh.get("blendShapes")
    if isinstance(blend_shapes, list):
        if len(blend_shapes) > _MAX_BLEND_SHAPES:
            raise ValueError("blend_shapes_too_many")
        for shape in blend_shapes:
            if not isinstance(shape, dict):
                raise ValueError("blend_shape_not_dict")
            result["blendShapes"].append(
                {
                    "name": str(shape.get("name") or ""),
                    "frameWeight": shape.get("frameWeight", 0.0),
                    "deltaPositions": _read_float_buffer(
                        binary,
                        shape.get("deltaPositions"),
                        binary_size,
                        required=True,
                        expected_components=3,
                    ),
                }
            )

    return result


def _read_float_buffer(
    binary,
    descriptor,
    binary_size: int,
    *,
    required: bool = False,
    expected_components: int | None = None,
) -> list[float]:
    values = _read_array_buffer(
        binary,
        descriptor,
        "float32",
        "f",
        4,
        binary_size,
        required=required,
        expected_components=expected_components,
    )
    return values.tolist() if values is not None else []


def _read_int_buffer(
    binary,
    descriptor,
    binary_size: int,
    *,
    required: bool = False,
    expected_components: int | None = None,
) -> list[int]:
    values = _read_array_buffer(
        binary,
        descriptor,
        "int32",
        "i",
        4,
        binary_size,
        required=required,
        expected_components=expected_components,
    )
    return values.tolist() if values is not None else []


def _read_array_buffer(
    binary,
    descriptor,
    expected_type: str,
    typecode: str,
    item_size: int,
    binary_size: int,
    *,
    required: bool = False,
    expected_components: int | None = None,
):
    if not isinstance(descriptor, dict):
        if required:
            raise ValueError(f"binary_buffer_missing expectedType={expected_type}")
        return None
    if str(descriptor.get("valueType") or "") != expected_type:
        raise ValueError(
            f"binary_buffer_type_mismatch semantic={descriptor.get('semantic')} "
            f"expected={expected_type} actual={descriptor.get('valueType')}"
        )
    offset = _read_bounded_int(
        descriptor.get("offset"), "binary_buffer_offset", minimum=0, maximum=binary_size
    )
    byte_count = _read_bounded_int(
        descriptor.get("byteCount"), "binary_buffer_bytes", minimum=0, maximum=binary_size
    )
    components = _read_bounded_int(
        descriptor.get("components"), "binary_buffer_components", minimum=1, maximum=16
    )
    count = _read_bounded_int(
        descriptor.get("count"), "binary_buffer_count", minimum=0, maximum=_MAX_MESH_INDICES
    )
    semantic = descriptor.get("semantic")
    if expected_components is not None and components != expected_components:
        raise ValueError(
            f"binary_buffer_components_mismatch semantic={semantic} "
            f"expected={expected_components} actual={components}"
        )
    if byte_count % item_size != 0:
        raise ValueError(f"binary_buffer_unaligned semantic={semantic} bytes={byte_count} itemSize={item_size}")
    if byte_count != count * components * item_size:
        raise ValueError(
            f"binary_buffer_size_mismatch semantic={semantic} "
            f"count={count} components={components} bytes={byte_count}"
        )
    if offset + byte_count > binary_size:
        raise ValueError(
            f"binary_buffer_out_of_range semantic={semantic} "
            f"offset={offset} bytes={byte_count} fileBytes={binary_size}"
        )
    if byte_count <= 0:
        return array(typecode)
    binary.seek(offset)
    data = binary.read(byte_count)
    if len(data) != byte_count:
        raise ValueError(f"binary_buffer_short semantic={semantic} expected={byte_count} actual={len(data)}")
    values = array(typecode)
    values.frombytes(data)
    if sys.byteorder != "little":
        values.byteswap()
    return values


def _validate_staged_binary_paths(payload: dict[str, Any]) -> None:
    manifest_path = str(payload.get("manifestPath") or "").strip()
    binary_path = str(payload.get("binaryPath") or "").strip()
    _validate_staged_file(
        manifest_path,
        ".manifest.json",
        _MAX_STAGED_MANIFEST_BYTES,
        "manifest",
        payload.get("manifestBytes"),
    )
    _validate_staged_file(
        binary_path,
        ".bin",
        _MAX_STAGED_BINARY_BYTES,
        "binary",
        payload.get("binaryBytes"),
    )

    manifest_real = os.path.normcase(os.path.realpath(manifest_path))
    binary_real = os.path.normcase(os.path.realpath(binary_path))
    manifest_name = os.path.basename(manifest_real)
    binary_name = os.path.basename(binary_real)
    manifest_stem = manifest_name[: -len(".manifest.json")]
    binary_stem = binary_name[: -len(".bin")]
    if os.path.dirname(manifest_real) != os.path.dirname(binary_real) or manifest_stem != binary_stem:
        raise ValueError("staged_file_pair_mismatch")

    algorithm = str(payload.get("checksumAlgorithm") or "").strip().lower()
    checksum = str(payload.get("checksum") or "").strip()
    if algorithm != "crc32-ieee":
        raise ValueError(f"binary_checksum_algorithm_unsupported algorithm={algorithm or '(missing)'}")
    if len(checksum) != 8 or any(ch not in "0123456789abcdef" for ch in checksum):
        raise ValueError("binary_checksum_invalid")
    actual = _crc32_file(binary_path)
    if actual != checksum:
        raise ValueError(f"binary_checksum_mismatch expected={checksum} actual={actual}")


def _validate_staged_file(
    path: str,
    suffix: str,
    max_bytes: int,
    label: str,
    declared_bytes=None,
) -> int:
    if not _is_safe_staging_path(path):
        raise ValueError(f"{label}_path_outside_staging_root")
    normalized = os.path.realpath(path)
    if not normalized.lower().endswith(suffix.lower()):
        raise ValueError(f"{label}_path_suffix_invalid")
    if not os.path.isfile(normalized):
        raise ValueError(f"{label}_file_missing")
    size = os.path.getsize(normalized)
    if size <= 0:
        raise ValueError(f"{label}_file_empty")
    if size > max_bytes:
        raise ValueError(f"{label}_file_too_large bytes={size}")
    if declared_bytes is not None:
        if isinstance(declared_bytes, bool) or not isinstance(declared_bytes, int):
            raise ValueError(f"{label}_declared_bytes_invalid")
        expected = declared_bytes
        if expected != size:
            raise ValueError(f"{label}_size_mismatch expected={expected} actual={size}")
    return size


def _crc32_file(path: str) -> str:
    crc = 0
    with open(path, "rb") as handle:
        while True:
            chunk = handle.read(64 * 1024)
            if not chunk:
                break
            crc = zlib.crc32(chunk, crc)
    return f"{crc & 0xFFFFFFFF:08x}"


def _cleanup_pending_file(payload: dict[str, Any]) -> None:
    if not isinstance(payload, dict):
        return
    payload_type = payload.get("type")
    if payload_type not in {"unity_mesh.import_file_v1", "unity_mesh.import_binary_file_v1"}:
        return
    if not bool(payload.get("deleteAfterImport", True)):
        return
    paths = []
    if payload_type == "unity_mesh.import_file_v1":
        paths.append(str(payload.get("payloadPath") or "").strip())
    else:
        paths.append(str(payload.get("manifestPath") or "").strip())
        paths.append(str(payload.get("binaryPath") or "").strip())

    for path in paths:
        if not path:
            continue
        if not _is_safe_staging_path(path):
            warn(
                "UnityMeshImport",
                "staged_file_cleanup_rejected",
                "Refused to delete a staged file outside the staging root.",
            )
            continue
        try:
            if os.path.exists(path):
                os.remove(path)
                trace(
                    "UnityMeshImport",
                    "staged_file_deleted",
                    lambda: "Deleted a consumed staged mesh file.",
                )
        except Exception as exc:
            log_exception("UnityMeshImport", "staged_file_cleanup_exception", exc)


def _is_safe_staging_path(path: str) -> bool:
    try:
        normalized = os.path.normcase(os.path.normpath(os.path.realpath(path)))
    except Exception:
        return False
    parts = normalized.split(os.sep)
    expected_parts = tuple(os.path.normcase(part) for part in _STAGING_ROOT_PARTS)
    for index in range(0, len(parts) - len(_STAGING_ROOT_PARTS) + 1):
        if tuple(parts[index : index + len(_STAGING_ROOT_PARTS)]) == expected_parts:
            return os.path.basename(normalized).startswith("import-")
    return False


def _create_mesh_object(payload: dict[str, Any], *, result_warnings: list[str] | None = None):
    if not isinstance(payload, dict):
        raise ValueError("mesh_payload_not_dict")
    declared_vertex_count = _read_bounded_int(
        payload.get("vertexCount"),
        "vertex_count",
        minimum=1,
        maximum=_MAX_MESH_VERTICES,
    )
    vertices = _read_vertices(payload.get("vertices"), declared_vertex_count)

    submeshes = payload.get("subMeshes")
    faces, material_indices = _read_faces(submeshes, len(vertices))
    if not faces:
        raise ValueError("faces_empty")
    declared_index_count = _read_bounded_int(
        payload.get("indexCount"),
        "index_count",
        minimum=3,
        maximum=_MAX_MESH_INDICES,
    )
    actual_index_count = len(faces) * 3
    if declared_index_count != actual_index_count:
        raise ValueError(
            f"index_count_mismatch expected={declared_index_count} actual={actual_index_count}"
        )

    normals = _read_optional_float_values(payload.get("normals"), len(vertices) * 3, "normals")
    colors = _read_optional_float_values(payload.get("colors"), len(vertices) * 4, "colors")
    material_names = _read_material_names(payload.get("materialNames"))
    uv_channels = _read_uv_channels(payload.get("uvChannels"), len(vertices))
    blend_shapes = _read_blend_shapes(payload.get("blendShapes"), len(vertices))

    object_name = _safe_name(payload.get("objectName") or payload.get("meshName"), "UnityMesh")
    mesh_name = _safe_name(payload.get("meshName") or object_name, object_name)

    mesh = None
    obj = None
    created_materials = []
    attribute_warnings = []
    try:
        mesh = bpy.data.meshes.new(mesh_name)
        mesh.from_pydata(vertices, [], faces)
        mesh.update(calc_edges=True)

        obj = bpy.data.objects.new(object_name, mesh)
        collection = getattr(bpy.context, "collection", None) or bpy.context.scene.collection
        collection.objects.link(obj)

        created_materials = _append_materials(mesh, material_names, submeshes)
        _assign_material_indices(mesh, material_indices)
        _apply_uv_channels(mesh, uv_channels, len(vertices))
        normal_warning = _apply_normals(mesh, normals, len(vertices))
        if normal_warning:
            attribute_warnings.append(normal_warning)
        color_warning = _apply_vertex_colors(mesh, colors, len(vertices))
        if color_warning:
            attribute_warnings.append(color_warning)
        _apply_blend_shapes(obj, vertices, blend_shapes)
        _set_provenance(obj, payload)
    except Exception:
        _remove_failed_mesh_object(obj, mesh, created_materials)
        raise

    try:
        bpy.ops.object.select_all(action="DESELECT")
        obj.select_set(True)
        bpy.context.view_layer.objects.active = obj
    except Exception:
        pass

    if result_warnings is not None:
        result_warnings.extend(attribute_warnings)

    trace(
        "UnityMeshImport",
        "mesh_created",
        lambda: "Created a mesh object from Unity data.",
        lambda: {
            "objectName": obj.name,
            "meshName": mesh.name,
            "vertexCount": len(vertices),
            "faceCount": len(faces),
            "subMeshCount": len(submeshes) if isinstance(submeshes, list) else 0,
        },
    )
    return obj


def _read_vertices(values, expected_count: int) -> list[tuple[float, float, float]]:
    expected_length = expected_count * 3
    flat = _read_float_values(values, expected_length, "vertices")
    return [
        (flat[i * 3], flat[i * 3 + 1], flat[i * 3 + 2])
        for i in range(expected_count)
    ]


def _read_faces(submeshes, vertex_count: int) -> tuple[list[tuple[int, int, int]], list[int]]:
    faces: list[tuple[int, int, int]] = []
    material_indices: list[int] = []
    if not isinstance(submeshes, list) or not submeshes:
        raise ValueError("submeshes_empty")

    for submesh_index, submesh in enumerate(submeshes):
        if not isinstance(submesh, dict):
            raise ValueError(f"submesh_not_dict slot={submesh_index}")
        if str(submesh.get("topology") or "triangles").lower() != "triangles":
            raise ValueError(f"submesh_topology_unsupported slot={submesh_index}")
        material_slot = _read_bounded_int(
            submesh.get("materialSlot", submesh_index),
            f"material_slot_{submesh_index}",
            minimum=0,
            maximum=_MAX_MATERIAL_SLOTS - 1,
        )
        indices = submesh.get("indices")
        if not isinstance(indices, list) or not indices:
            raise ValueError(f"submesh_indices_empty slot={submesh_index}")
        if len(indices) % 3 != 0:
            raise ValueError(f"submesh_indices_not_triangles slot={submesh_index} count={len(indices)}")
        if len(faces) * 3 + len(indices) > _MAX_MESH_INDICES:
            raise ValueError("indices_too_many")
        for i in range(0, len(indices), 3):
            a = _read_index(indices[i], vertex_count, submesh_index)
            b = _read_index(indices[i + 1], vertex_count, submesh_index)
            c = _read_index(indices[i + 2], vertex_count, submesh_index)
            faces.append((a, b, c))
            material_indices.append(material_slot)
    return faces, material_indices


def _append_materials(mesh, material_names, submeshes) -> list[Any]:
    names: list[str] = []
    if isinstance(material_names, list):
        names = [str(n or "").strip() for n in material_names]

    max_slot = -1
    if isinstance(submeshes, list):
        for submesh in submeshes:
            if not isinstance(submesh, dict):
                continue
            try:
                max_slot = max(max_slot, int(submesh.get("materialSlot", 0) or 0))
            except Exception:
                pass
            material_name = str(submesh.get("materialName") or "").strip()
            slot = int(submesh.get("materialSlot", 0) or 0) if str(submesh.get("materialSlot", "")).strip() else -1
            if material_name and slot >= 0:
                while len(names) <= slot:
                    names.append("")
                if not names[slot]:
                    names[slot] = material_name

    slot_count = max(max_slot + 1, len(names), 1)
    created = []
    for slot in range(slot_count):
        name = names[slot] if slot < len(names) and names[slot] else f"Unity Material {slot}"
        mat = bpy.data.materials.new(_safe_name(name, f"Unity Material {slot}"))
        mesh.materials.append(mat)
        created.append(mat)
    return created


def _assign_material_indices(mesh, material_indices: list[int]) -> None:
    material_count = max(1, len(mesh.materials))
    for poly, material_index in zip(mesh.polygons, material_indices):
        poly.material_index = max(0, min(int(material_index), material_count - 1))


def _apply_uv_channels(mesh, uv_channels, vertex_count: int) -> None:
    for channel in uv_channels:
        values = channel.get("values")
        if len(values) != vertex_count * 2:
            raise ValueError(f"uv_values_length_mismatch channel={channel.get('index')}")
        name = _safe_name(channel.get("name"), "UVMap")
        layer = mesh.uv_layers.new(name=name)
        for loop in mesh.loops:
            base = int(loop.vertex_index) * 2
            layer.data[loop.index].uv = (float(values[base]), float(values[base + 1]))


def _apply_normals(mesh, normals, vertex_count: int) -> str | None:
    if not normals:
        return None
    try:
        for poly in mesh.polygons:
            poly.use_smooth = True
        values = [
            (normals[i * 3], normals[i * 3 + 1], normals[i * 3 + 2])
            for i in range(vertex_count)
        ]
        mesh.normals_split_custom_set_from_vertices(values)
    except Exception as exc:
        mesh_name = _safe_name(getattr(mesh, "name", None), "(unnamed)")
        warning = f"NORMALS_SKIPPED mesh={mesh_name} reason={exc}"
        trace(
            "UnityMeshImport",
            "normals_skipped",
            lambda: warning,
            lambda: {"meshName": mesh_name},
        )
        return warning
    return None


def _apply_vertex_colors(mesh, colors, vertex_count: int) -> str | None:
    if not colors:
        return None
    try:
        if not hasattr(mesh, "color_attributes"):
            mesh_name = _safe_name(getattr(mesh, "name", None), "(unnamed)")
            warning = f"COLORS_SKIPPED mesh={mesh_name} reason=color_attributes_unavailable"
            trace(
                "UnityMeshImport",
                "colors_skipped",
                lambda: warning,
                lambda: {"meshName": mesh_name},
            )
            return warning
        attr = mesh.color_attributes.new(name="Color", type="FLOAT_COLOR", domain="POINT")
        for i in range(vertex_count):
            base = i * 4
            attr.data[i].color = (
                colors[base],
                colors[base + 1],
                colors[base + 2],
                colors[base + 3],
            )
    except Exception as exc:
        mesh_name = _safe_name(getattr(mesh, "name", None), "(unnamed)")
        warning = f"COLORS_SKIPPED mesh={mesh_name} reason={exc}"
        trace(
            "UnityMeshImport",
            "colors_skipped",
            lambda: warning,
            lambda: {"meshName": mesh_name},
        )
        return warning
    return None


def _apply_blend_shapes(obj, vertices, blend_shapes) -> None:
    if not blend_shapes:
        return
    obj.shape_key_add(name="Basis")
    for shape in blend_shapes:
        key = obj.shape_key_add(name=_safe_name(shape["name"], "Shape"))
        deltas = shape["deltaPositions"]
        coordinates = array("f", deltas)
        for index, vertex in enumerate(vertices):
            base = index * 3
            coordinates[base] += vertex[0]
            coordinates[base + 1] += vertex[1]
            coordinates[base + 2] += vertex[2]
        if hasattr(key.data, "foreach_set"):
            key.data.foreach_set("co", coordinates)
        else:
            for index in range(len(vertices)):
                base = index * 3
                key.data[index].co = coordinates[base : base + 3]
        try:
            key.slider_min = 0.0
            key.slider_max = 1.0
        except Exception:
            pass


def _remove_failed_mesh_object(obj, mesh, created_materials) -> None:
    try:
        if obj is not None:
            bpy.data.objects.remove(obj, do_unlink=True)
    except Exception:
        pass
    try:
        if mesh is not None:
            bpy.data.meshes.remove(mesh)
    except Exception:
        pass
    for material in created_materials or []:
        try:
            if material.users == 0:
                bpy.data.materials.remove(material)
        except Exception:
            pass


def _read_bounded_int(value, label: str, *, minimum: int, maximum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{label}_invalid")
    if value < minimum or value > maximum:
        raise ValueError(f"{label}_out_of_range value={value}")
    return value


def _read_index(value, vertex_count: int, submesh_index: int) -> int:
    index = _read_bounded_int(
        value,
        f"index_submesh_{submesh_index}",
        minimum=0,
        maximum=vertex_count - 1,
    )
    return index


def _read_float_values(values, expected_length: int, label: str) -> list[float]:
    if not isinstance(values, list):
        raise ValueError(f"{label}_missing")
    if len(values) != expected_length:
        raise ValueError(f"{label}_length_mismatch expected={expected_length} actual={len(values)}")
    result = []
    for value in values:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError(f"{label}_value_invalid")
        number = float(value)
        if not math.isfinite(number):
            raise ValueError(f"{label}_value_not_finite")
        result.append(number)
    return result


def _read_optional_float_values(values, expected_length: int, label: str) -> list[float]:
    if values is None or values == []:
        return []
    return _read_float_values(values, expected_length, label)


def _read_material_names(values) -> list[str]:
    if values is None or values == []:
        return []
    if not isinstance(values, list) or len(values) > _MAX_MATERIAL_SLOTS:
        raise ValueError("material_names_invalid")
    return [str(value or "").strip()[:256] for value in values]


def _read_uv_channels(channels, vertex_count: int) -> list[dict[str, Any]]:
    if channels is None or channels == []:
        return []
    if not isinstance(channels, list) or len(channels) > 8:
        raise ValueError("uv_channels_invalid")
    result = []
    seen = set()
    for channel in channels:
        if not isinstance(channel, dict):
            raise ValueError("uv_channel_not_dict")
        index = _read_bounded_int(channel.get("index"), "uv_channel_index", minimum=0, maximum=7)
        if index in seen:
            raise ValueError(f"uv_channel_duplicate index={index}")
        seen.add(index)
        values = _read_float_values(channel.get("values"), vertex_count * 2, f"uv_channel_{index}")
        result.append({"index": index, "name": str(channel.get("name") or "UV" + str(index)), "values": values})
    return result


def _read_blend_shapes(shapes, vertex_count: int) -> list[dict[str, Any]]:
    if shapes is None or shapes == []:
        return []
    if not isinstance(shapes, list) or len(shapes) > _MAX_BLEND_SHAPES:
        raise ValueError("blend_shapes_invalid")
    result = []
    for shape in shapes:
        if not isinstance(shape, dict):
            raise ValueError("blend_shape_not_dict")
        name = str(shape.get("name") or "").strip()
        if not name:
            raise ValueError("blend_shape_name_empty")
        frame_weight = shape.get("frameWeight", 0.0)
        if isinstance(frame_weight, bool) or not isinstance(frame_weight, (int, float)):
            raise ValueError("blend_shape_frame_weight_invalid")
        frame_weight = float(frame_weight)
        if not math.isfinite(frame_weight):
            raise ValueError("blend_shape_frame_weight_not_finite")
        result.append(
            {
                "name": name,
                "frameWeight": frame_weight,
                "deltaPositions": _read_float_values(
                    shape.get("deltaPositions"),
                    vertex_count * 3,
                    f"blend_shape_{name}",
                ),
            }
        )
    return result


def _normalize_warnings(values) -> list[str]:
    if not isinstance(values, list):
        return []
    result = []
    for value in values:
        text = str(value or "").strip()
        if text:
            result.append(text[:512])
        if len(result) >= _MAX_RESULT_WARNINGS:
            break
    return result


def _set_provenance(obj, payload: dict[str, Any]) -> None:
    obj["blendersync_source"] = "unity_mesh_import"
    obj["unity_mesh_name"] = str(payload.get("meshName") or "")
    obj["unity_source_kind"] = str(payload.get("sourceKind") or "")
    obj["unity_source_object_path"] = str(payload.get("sourceObjectPath") or "")
    obj["unity_asset_path"] = str(payload.get("assetPath") or "")
    obj["unity_asset_guid"] = str(payload.get("assetGuid") or "")
    obj["unity_asset_local_id"] = str(payload.get("assetLocalId") or "")
    obj["unity_imported_at"] = int(time.time())


def _safe_name(value, fallback: str) -> str:
    text = str(value or "").strip()
    if not text:
        text = fallback
    for ch in "\0\r\n\t":
        text = text.replace(ch, "_")
    return text[:63] if len(text) > 63 else text
