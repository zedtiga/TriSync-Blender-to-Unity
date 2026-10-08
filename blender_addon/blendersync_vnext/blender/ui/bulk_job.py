from __future__ import annotations

import time
import uuid

BULK_OBJECT_THRESHOLD = 10

def _idle_state() -> dict:
    return {
        "jobId": "",
        "mode": "fast_path",
        "status": "idle",
        "operation": "",
        "totalObjects": 0,
        "processedObjects": 0,
        "message": "",
        "currentObject": "",
        "successCount": 0,
        "errorCount": 0,
        "skippedCount": 0,
        "lastError": "",
        "startedAt": 0.0,
        "endedAt": 0.0,
        "cancelRequested": False,
    }


_state = _idle_state()


def should_use_bulk_mode(object_count: int) -> bool:
    return int(object_count or 0) > BULK_OBJECT_THRESHOLD


def begin_job(operation: str, total_objects: int, *, mode: str | None = None) -> dict:
    total = int(total_objects or 0)
    job_mode = str(mode or ("bulk_pending" if should_use_bulk_mode(total) else "fast_path"))
    _state.update(
        {
            "jobId": f"bulk-{int(time.time() * 1000)}-{uuid.uuid4().hex[:8]}",
            "mode": job_mode,
            "status": "running",
            "operation": str(operation or "sync"),
            "totalObjects": total,
            "processedObjects": 0,
            "message": "Bulk infrastructure active; current operation is queued on the synchronous pump."
            if job_mode == "bulk_pending"
            else "Fast path",
            "currentObject": "",
            "successCount": 0,
            "errorCount": 0,
            "skippedCount": 0,
            "lastError": "",
            "startedAt": time.time(),
            "endedAt": 0.0,
            "cancelRequested": False,
        }
    )
    return get_state()


def finish_job(*, ok: bool, processed_objects: int | None = None, message: str = "") -> dict:
    ended_at = time.time()
    _state.update(
        {
            "status": "done" if ok else "error",
            "processedObjects": int(processed_objects if processed_objects is not None else _state.get("totalObjects") or 0),
            "message": str(message or ("Completed" if ok else "Failed")),
            "currentObject": "",
            "endedAt": ended_at,
        }
    )
    return get_state()


def update_job(
    *,
    processed_objects: int | None = None,
    message: str = "",
    current_object: str | None = None,
    success_count: int | None = None,
    error_count: int | None = None,
    skipped_count: int | None = None,
    last_error: str | None = None,
) -> dict:
    if processed_objects is not None:
        _state["processedObjects"] = int(processed_objects)
    if message:
        _state["message"] = str(message)
    if current_object is not None:
        _state["currentObject"] = str(current_object)
    if success_count is not None:
        _state["successCount"] = int(success_count)
    if error_count is not None:
        _state["errorCount"] = int(error_count)
    if skipped_count is not None:
        _state["skippedCount"] = int(skipped_count)
    if last_error is not None:
        _state["lastError"] = str(last_error)
    return get_state()


def request_cancel() -> dict:
    if _state.get("status") == "running":
        _state["cancelRequested"] = True
        _state["status"] = "cancel_requested"
        _state["message"] = "Cancel requested. Current synchronous operation cannot be interrupted until the next bulk queue phase."
    return get_state()


def cancel_job(message: str = "Cancelled") -> dict:
    _state.update(
        {
            "status": "cancelled",
            "message": str(message or "Cancelled"),
            "currentObject": "",
            "endedAt": time.time(),
        }
    )
    return get_state()


def cancel_requested() -> bool:
    return bool(_state.get("cancelRequested"))


def dismiss_job() -> bool:
    if str(_state.get("status") or "idle") in {"running", "cancel_requested"}:
        return False
    _state.clear()
    _state.update(_idle_state())
    return True


def get_state() -> dict:
    return dict(_state)
