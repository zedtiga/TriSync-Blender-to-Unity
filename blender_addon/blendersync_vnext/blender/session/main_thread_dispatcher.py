from __future__ import annotations

from collections import deque
import threading
from typing import Any

from blender.common.exception_boundary import report_boundary_exception
from blender.common.log import trace, warn

try:
    import bpy  # type: ignore
except ImportError:
    bpy = None


MAIN_THREAD_MESSAGE_TYPES = frozenset(
    {
        "scene_sync.mesh_ref_usage_v1",
        "scene_sync.mesh_content_fingerprint_request_v1",
        "unity_mesh.import_v1",
        "unity_mesh.import_file_v1",
        "unity_mesh.import_binary_file_v1",
    }
)

_MAX_PENDING_MESSAGES = 256
_ACTIVE_PUMP_INTERVAL_SECONDS = 0.01
_IDLE_PUMP_INTERVAL_SECONDS = 0.05

_ERROR_LOG_FORMAT_BY_TYPE = {
    "scene_sync.mesh_ref_usage_v1": "[vNext][MeshRefUsage] ERROR error={error}",
    "scene_sync.mesh_content_fingerprint_request_v1": "[vNext][MeshContentFingerprintRequest] ERROR error={error}",
    "unity_mesh.import_v1": "[vNext][UnityMeshImport] ERROR error={error}",
    "unity_mesh.import_file_v1": "[vNext][UnityMeshImport] ERROR_FILE error={error}",
    "unity_mesh.import_binary_file_v1": "[vNext][UnityMeshImport] ERROR_BINARY_FILE error={error}",
}

_pending_lock = threading.RLock()
_pending_messages: deque[tuple[int, str, dict[str, Any]]] = deque()
_accepting_messages = False
_file_loading = False
_file_epoch = 0


def is_main_thread_message_type(message_type: object) -> bool:
    return isinstance(message_type, str) and message_type in MAIN_THREAD_MESSAGE_TYPES


def enqueue_main_thread_message(payload: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(payload, dict):
        return {"ok": False, "reason": "payload_not_dict", "pending": pending_message_count()}

    message_type = payload.get("type")
    if not is_main_thread_message_type(message_type):
        return {
            "ok": False,
            "reason": "unsupported_main_thread_message_type",
            "type": message_type,
            "pending": pending_message_count(),
        }

    with _pending_lock:
        if not _accepting_messages:
            return {
                "ok": False,
                "reason": "main_thread_dispatcher_not_registered",
                "type": message_type,
                "pending": len(_pending_messages),
                "capacity": _MAX_PENDING_MESSAGES,
            }
        if _file_loading:
            return {
                "ok": False,
                "reason": "file_load_in_progress",
                "type": message_type,
                "pending": len(_pending_messages),
                "capacity": _MAX_PENDING_MESSAGES,
                "fileEpoch": _file_epoch,
            }
        pending = len(_pending_messages)
        if pending >= _MAX_PENDING_MESSAGES:
            return {
                "ok": False,
                "reason": "main_thread_queue_full",
                "type": message_type,
                "pending": pending,
                "capacity": _MAX_PENDING_MESSAGES,
            }
        epoch = _file_epoch
        _pending_messages.append((epoch, message_type, payload))
        pending = len(_pending_messages)

    return {"ok": True, "type": message_type, "pending": pending, "fileEpoch": epoch}


def pending_message_count() -> int:
    with _pending_lock:
        return len(_pending_messages)


def clear_pending_main_thread_messages() -> int:
    with _pending_lock:
        cleared = len(_pending_messages)
        _pending_messages.clear()
    return cleared


def current_file_epoch() -> int:
    with _pending_lock:
        return _file_epoch


def begin_file_load() -> dict[str, int]:
    global _file_epoch, _file_loading

    with _pending_lock:
        _file_loading = True
        _file_epoch += 1
        cleared = len(_pending_messages)
        _pending_messages.clear()
        epoch = _file_epoch
    if cleared:
        trace(
            "MainThreadDispatcher",
            "queue_cleared_before_file_load",
            lambda: "Cleared inbound messages before loading a file.",
            lambda: {"clearedCount": cleared, "fileEpoch": epoch},
        )
    return {"cleared": cleared, "fileEpoch": epoch}


def complete_file_load() -> dict[str, int]:
    global _file_loading

    with _pending_lock:
        cleared = len(_pending_messages)
        _pending_messages.clear()
        _file_loading = False
        epoch = _file_epoch
    if cleared:
        trace(
            "MainThreadDispatcher",
            "queue_cleared_after_file_load",
            lambda: "Cleared inbound messages after loading a file.",
            lambda: {"clearedCount": cleared, "fileEpoch": epoch},
        )
    return {"cleared": cleared, "fileEpoch": epoch}


def register_main_thread_dispatcher() -> None:
    global _accepting_messages, _file_loading

    if bpy is None:
        return

    timers = bpy.app.timers
    try:
        if timers.is_registered(_pump_main_thread_messages):
            with _pending_lock:
                _accepting_messages = True
                _file_loading = False
            return
    except Exception:
        pass

    timers.register(
        _pump_main_thread_messages,
        first_interval=_IDLE_PUMP_INTERVAL_SECONDS,
        persistent=True,
    )
    with _pending_lock:
        _accepting_messages = True
        _file_loading = False


def unregister_main_thread_dispatcher() -> None:
    global _accepting_messages, _file_loading

    with _pending_lock:
        _accepting_messages = False
        _file_loading = False
        _pending_messages.clear()
    if bpy is None:
        return

    timers = bpy.app.timers
    try:
        if timers.is_registered(_pump_main_thread_messages):
            timers.unregister(_pump_main_thread_messages)
    except Exception:
        pass


def _pump_main_thread_messages() -> float:
    with _pending_lock:
        entry = _pending_messages.popleft() if _pending_messages else None

    if entry is None:
        return _IDLE_PUMP_INTERVAL_SECONDS

    entry_epoch, message_type, payload = entry
    with _pending_lock:
        current_epoch = _file_epoch
        file_loading = _file_loading
    if file_loading or entry_epoch != current_epoch:
        reason = "file_load_in_progress" if file_loading else "stale_file_epoch"
        trace(
            "MainThreadDispatcher",
            "stale_message_dropped",
            lambda: "Dropped an inbound message from a stale file epoch.",
            lambda: {
                "messageType": message_type,
                "reason": reason,
                "entryEpoch": entry_epoch,
                "fileEpoch": current_epoch,
            },
        )
        return _ACTIVE_PUMP_INTERVAL_SECONDS if pending_message_count() else _IDLE_PUMP_INTERVAL_SECONDS

    try:
        _dispatch_message(message_type, payload)
    except Exception as exc:
        error_format = _ERROR_LOG_FORMAT_BY_TYPE.get(
            message_type,
            "[vNext][MainThreadInbound] ERROR type=" + message_type + " error={error}",
        )
        report_boundary_exception(
            f"main_thread_dispatcher:{message_type}",
            exc,
            message=error_format.format(error=exc),
        )

    return _ACTIVE_PUMP_INTERVAL_SECONDS if pending_message_count() else _IDLE_PUMP_INTERVAL_SECONDS


def _dispatch_message(message_type: str, payload: dict[str, Any]) -> None:
    if message_type == "scene_sync.mesh_ref_usage_v1":
        from blender.scene_sync.controller import apply_mesh_ref_usage

        result = apply_mesh_ref_usage(payload)
        if not result.get("ok"):
            warn(
                "MainThreadDispatcher",
                "mesh_reference_usage_rejected",
                result.get("reason") or "request_rejected",
            )
        return

    if message_type == "scene_sync.mesh_content_fingerprint_request_v1":
        from blender.scene_sync.controller import apply_mesh_content_fingerprint_request

        result = apply_mesh_content_fingerprint_request(payload)
        if not result.get("ok"):
            warn(
                "MainThreadDispatcher",
                "mesh_fingerprint_request_rejected",
                result.get("reason") or "request_rejected",
            )
        return

    if message_type == "unity_mesh.import_v1":
        from blender.unity_mesh_import import import_unity_mesh_payload, send_import_result

        result = import_unity_mesh_payload(payload)
        if not result.get("ok"):
            warn(
                "MainThreadDispatcher",
                "mesh_import_rejected",
                result.get("reason") or "import_rejected",
            )
            send_import_result(
                "failed",
                "Blender rejected the mesh creation request.",
                error=str(result.get("reason") or "import_rejected"),
                warnings=payload.get("warnings"),
            )
        else:
            trace(
                "MainThreadDispatcher",
                "mesh_import_queued",
                lambda: "Queued a Unity mesh import on Blender's main thread.",
                lambda: {"meshCount": result.get("queued"), "pendingCount": result.get("pending")},
            )
            send_import_result(
                "queued",
                f"Queued {result.get('queued', 0)} mesh object(s) in Blender.",
                warnings=payload.get("warnings"),
            )
        return

    if message_type == "unity_mesh.import_file_v1":
        from blender.unity_mesh_import import import_unity_mesh_file_payload, send_import_result

        result = import_unity_mesh_file_payload(payload)
        if not result.get("ok"):
            warn(
                "MainThreadDispatcher",
                "staged_mesh_import_rejected",
                result.get("reason") or "file_import_rejected",
            )
            send_import_result(
                "failed",
                "Blender rejected the staged mesh creation request.",
                error=str(result.get("reason") or "file_import_rejected"),
                warnings=payload.get("warnings"),
            )
        else:
            trace(
                "MainThreadDispatcher",
                "staged_mesh_import_queued",
                lambda: "Queued a staged Unity mesh import.",
                lambda: {"byteCount": result.get("bytes"), "pendingCount": result.get("pending")},
            )
            send_import_result(
                "queued",
                "Queued the staged mesh creation request in Blender.",
                warnings=payload.get("warnings"),
            )
        return

    if message_type == "unity_mesh.import_binary_file_v1":
        from blender.unity_mesh_import import import_unity_mesh_binary_file_payload, send_import_result

        result = import_unity_mesh_binary_file_payload(payload)
        if not result.get("ok"):
            warn(
                "MainThreadDispatcher",
                "binary_mesh_import_rejected",
                result.get("reason") or "binary_import_rejected",
            )
            send_import_result(
                "failed",
                "Blender rejected the staged mesh creation request.",
                error=str(result.get("reason") or "binary_import_rejected"),
                warnings=payload.get("warnings"),
            )
        else:
            trace(
                "MainThreadDispatcher",
                "binary_mesh_import_queued",
                lambda: "Queued a staged binary Unity mesh import.",
                lambda: {"byteCount": result.get("bytes"), "pendingCount": result.get("pending")},
            )
            send_import_result(
                "queued",
                "Queued the staged mesh creation request in Blender.",
                warnings=payload.get("warnings"),
            )
        return

    raise ValueError(f"unsupported_main_thread_message_type:{message_type}")
