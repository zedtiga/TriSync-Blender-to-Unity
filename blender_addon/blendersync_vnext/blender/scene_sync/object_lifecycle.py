from __future__ import annotations

from dataclasses import dataclass
from typing import Callable
import uuid

from blender.asset_registry import mark_assets_known
from blender.common.log import exception as log_exception, trace, warn
from blender.common.evaluated_mesh import TEMPORARY_EVALUATED_OBJECT_KEY
from blender.identity import (
    ensure_instance_id,
    get_instance_id,
    get_mesh_asset_id_for_object,
    set_mesh_asset_id_for_object,
)
from blender.object_classification import is_supported_scene_object
from blender.scene_sync.entrypoints import send_object_remove_once
from blender.scene_sync.runtime_state import LifecycleRuntimeState
from blender.transport.entrypoints import send_selected_resources
from blender.ui.object_context_builders import build_single_object_live_context
from blender.ui.state_view import get_session

try:
    import bpy  # type: ignore
except ImportError:
    bpy = None


AUTO_SYNC_READY_KEY = "blendersync_auto_sync_ready"
LIFECYCLE_MAX_PER_TICK = 5
LIFECYCLE_BACKOFF_SECONDS = (1.0, 2.0, 5.0, 10.0)
LIFECYCLE_PHASE_PENDING_BOOTSTRAP = "pending_bootstrap"
LIFECYCLE_PHASE_ACTIVE = "active"
LIFECYCLE_PHASE_PENDING_REMOVE = "pending_remove"
LIFECYCLE_PHASE_REMOVED = "removed"


@dataclass(frozen=True)
class LifecycleHooks:
    on_bootstrap_success: Callable[[object, dict, str], None]
    clear_removed_pair: Callable[[str, str], object]
    poll_non_transform: Callable[[dict[str, object], float], int]
    cache_ready_object: Callable[[object], None]


def reassign_duplicate_instance_id(
    runtime: LifecycleRuntimeState,
    obj,
    existing_obj,
    old_instance_id: str,
) -> str:
    instance_id = str(uuid.uuid4())
    obj["blendersync_instance_id"] = instance_id
    instance_id = ensure_instance_id(obj)
    source_mesh_asset_id = get_mesh_asset_id_for_object(existing_obj)
    if source_mesh_asset_id:
        set_mesh_asset_id_for_object(obj, source_mesh_asset_id)
        try:
            if bool(existing_obj.get(AUTO_SYNC_READY_KEY, False)):
                mark_assets_known([f"mesh-{source_mesh_asset_id}"])
        except Exception:
            pass
    runtime.new_reason_by_id[instance_id] = "duplicate_reassigned"
    warn(
        "Lifecycle",
        "duplicate_instance_id_reassigned",
        "Reassigned a duplicate object identity.",
        {
            "keptObjectName": getattr(existing_obj, "name", "<unnamed>"),
            "reassignedObjectName": getattr(obj, "name", "<unnamed>"),
            "oldInstanceId": old_instance_id,
            "newInstanceId": instance_id,
            "meshAssetId": source_mesh_asset_id or "",
        },
    )
    return instance_id


def collect_scene_instance_ids(runtime: LifecycleRuntimeState) -> dict[str, object]:
    if bpy is None or bpy.context is None or bpy.context.scene is None:
        return {}

    out = {}
    for obj in getattr(bpy.context.scene, "objects", []) or []:
        if obj is None or not is_supported_scene_object(obj):
            continue
        try:
            if bool(obj.get(TEMPORARY_EVALUATED_OBJECT_KEY, False)):
                continue
        except Exception:
            pass
        try:
            copied_instance_id = get_instance_id(obj)
            if copied_instance_id and copied_instance_id in out and out[copied_instance_id] is not obj:
                instance_id = reassign_duplicate_instance_id(runtime, obj, out[copied_instance_id], copied_instance_id)
            else:
                instance_id = ensure_instance_id(obj)
            if instance_id in out and out[instance_id] is not obj:
                instance_id = reassign_duplicate_instance_id(runtime, obj, out[instance_id], instance_id)
        except Exception:
            continue
        if instance_id:
            out[instance_id] = obj
    return out


def init_lifecycle_baseline(runtime: LifecycleRuntimeState, hooks: LifecycleHooks) -> None:
    current = collect_scene_instance_ids(runtime)
    runtime.baseline_ids.clear()
    runtime.baseline_ids.update(current.keys())
    runtime.managed_ids.clear()
    runtime.managed_ids.update({
        instance_id
        for instance_id, obj in current.items()
        if obj is not None and bool(obj.get(AUTO_SYNC_READY_KEY, False))
    })
    for obj in current.values():
        if obj is None or not bool(obj.get(AUTO_SYNC_READY_KEY, False)):
            continue
        hooks.cache_ready_object(obj)
    trace(
        "Lifecycle",
        "baseline_initialized",
        lambda: "Initialized the scene-object lifecycle baseline.",
        lambda: {"objectCount": len(runtime.baseline_ids), "managedCount": len(runtime.managed_ids)},
    )


def diff_lifecycle(runtime: LifecycleRuntimeState, current_ids: set[str]) -> tuple[set[str], set[str]]:
    return current_ids - runtime.baseline_ids, runtime.baseline_ids - current_ids


def ensure_lifecycle_entry(runtime: LifecycleRuntimeState, instance_id: str, phase: str) -> dict:
    entry = runtime.entries.get(instance_id)
    if entry is None:
        entry = {
            "phase": phase,
            "retryCount": 0,
            "nextRetryAt": 0.0,
            "lastError": None,
        }
        runtime.entries[instance_id] = entry
    else:
        entry["phase"] = phase
    return entry


def can_retry(entry: dict, now: float) -> bool:
    return now >= float(entry.get("nextRetryAt", 0.0) or 0.0)


def schedule_retry(entry: dict, now: float, error: str) -> None:
    retry_count = int(entry.get("retryCount", 0)) + 1
    entry["retryCount"] = retry_count
    backoff_index = min(retry_count - 1, len(LIFECYCLE_BACKOFF_SECONDS) - 1)
    delay = LIFECYCLE_BACKOFF_SECONDS[backoff_index]
    entry["nextRetryAt"] = now + delay
    entry["lastError"] = error


def mark_success(runtime: LifecycleRuntimeState, instance_id: str, phase: str) -> None:
    runtime.entries[instance_id] = {
        "phase": phase,
        "retryCount": 0,
        "nextRetryAt": 0.0,
        "lastError": None,
    }


def process_bootstrap(
    runtime: LifecycleRuntimeState,
    hooks: LifecycleHooks,
    instance_id: str,
    obj,
    now: float,
) -> bool:
    entry = ensure_lifecycle_entry(runtime, instance_id, LIFECYCLE_PHASE_PENDING_BOOTSTRAP)
    reason = runtime.new_reason_by_id.get(instance_id) or "baseline_new"
    if not can_retry(entry, now):
        return False

    if obj is None:
        schedule_retry(entry, now, "object_missing")
        return False

    try:
        send_context = build_single_object_live_context(
            get_session(),
            obj,
            package_id=f"pkg-object-auto-bootstrap-{int(now * 1000)}",
        )
        result = send_selected_resources(send_context)
    except Exception as exc:
        schedule_retry(entry, now, str(exc) or "bootstrap_context_failed")
        log_exception(
            "Lifecycle",
            "bootstrap_exception",
            exc,
            fields={"pairId": instance_id, "retryCount": entry.get("retryCount")},
        )
        return False

    if not result.ok:
        schedule_retry(entry, now, result.error or result.message or "bootstrap_send_failed")
        warn(
            "Lifecycle",
            "bootstrap_failed",
            entry.get("lastError") or "bootstrap_send_failed",
            {"pairId": instance_id, "retryCount": entry.get("retryCount")},
        )
        return False

    try:
        obj[AUTO_SYNC_READY_KEY] = True
    except Exception:
        pass

    pair_id = f"pair-{instance_id}"
    try:
        hooks.on_bootstrap_success(obj, send_context, pair_id)
    except Exception as exc:
        log_exception(
            "Lifecycle",
            "bootstrap_baseline_exception",
            exc,
            fields={"pairId": instance_id},
        )

    mark_success(runtime, instance_id, LIFECYCLE_PHASE_ACTIVE)
    runtime.managed_ids.add(instance_id)
    trace(
        "Lifecycle",
        "bootstrap_completed",
        lambda: "Completed object lifecycle bootstrap.",
        lambda: {"pairId": instance_id, "reason": reason},
    )
    runtime.new_reason_by_id.pop(instance_id, None)
    return True


def process_remove(
    runtime: LifecycleRuntimeState,
    hooks: LifecycleHooks,
    instance_id: str,
    now: float,
) -> bool:
    entry = ensure_lifecycle_entry(runtime, instance_id, LIFECYCLE_PHASE_PENDING_REMOVE)
    reason = runtime.delete_reason_by_id.get(instance_id) or "baseline_deleted"
    pair_id = f"pair-{instance_id}"
    if not can_retry(entry, now):
        return False

    prev_phase = entry.get("phase")
    if prev_phase not in {LIFECYCLE_PHASE_ACTIVE, LIFECYCLE_PHASE_PENDING_REMOVE} or instance_id not in runtime.managed_ids:
        mark_success(runtime, instance_id, LIFECYCLE_PHASE_REMOVED)
        hooks.clear_removed_pair(pair_id, "lifecycle_remove_skip")
        trace(
            "Lifecycle",
            "remove_skipped",
            lambda: "Skipped removal publication for an object that was never managed.",
            lambda: {"pairId": instance_id, "deleteReason": reason},
        )
        runtime.delete_reason_by_id.pop(instance_id, None)
        runtime.managed_ids.discard(instance_id)
        return False

    try:
        result = send_object_remove_once({
            "session": get_session(),
            "timestamp": int(now),
            "removed_pair_ids": [pair_id],
        })
    except Exception as exc:
        schedule_retry(entry, now, str(exc) or "remove_context_failed")
        log_exception(
            "Lifecycle",
            "remove_exception",
            exc,
            fields={"pairId": instance_id, "reason": reason, "retryCount": entry.get("retryCount")},
        )
        return False

    if not result.get("ok"):
        schedule_retry(entry, now, result.get("reason") or "object_remove_send_failed")
        warn(
            "Lifecycle",
            "remove_failed",
            entry.get("lastError") or "object_remove_send_failed",
            {"pairId": instance_id, "reason": reason, "retryCount": entry.get("retryCount")},
        )
        return False

    mark_success(runtime, instance_id, LIFECYCLE_PHASE_REMOVED)
    hooks.clear_removed_pair(pair_id, "lifecycle_remove_ok")
    runtime.managed_ids.discard(instance_id)
    trace(
        "Lifecycle",
        "remove_completed",
        lambda: "Published an object lifecycle removal.",
        lambda: {"pairId": instance_id, "reason": reason},
    )
    runtime.delete_reason_by_id.pop(instance_id, None)
    return True


def run_lifecycle_tick(
    runtime: LifecycleRuntimeState,
    hooks: LifecycleHooks,
    now: float,
    do_full_diff: bool = True,
    candidate_ids: set[str] | None = None,
) -> None:
    current = collect_scene_instance_ids(runtime)
    current_ids = set(current.keys())

    if do_full_diff:
        new_ids, deleted_ids = diff_lifecycle(runtime, current_ids)
        managed_deleted_ids = deleted_ids & runtime.managed_ids

        for instance_id in sorted(new_ids):
            reason = runtime.new_reason_by_id.get(instance_id) or "baseline_new"
            if instance_id not in runtime.entries:
                trace(
                    "Lifecycle",
                    "new_object_detected",
                    lambda: "Detected a new scene object.",
                    lambda: {"pairId": instance_id, "reason": reason},
                )
            ensure_lifecycle_entry(runtime, instance_id, LIFECYCLE_PHASE_PENDING_BOOTSTRAP)

        for instance_id in sorted(managed_deleted_ids):
            reason = runtime.delete_reason_by_id.get(instance_id) or "baseline_deleted"
            runtime.delete_reason_by_id[instance_id] = reason
            if instance_id not in runtime.entries:
                trace(
                    "Lifecycle",
                    "deleted_object_detected",
                    lambda: "Detected a removed scene object.",
                    lambda: {"pairId": instance_id, "reason": reason},
                )
            ensure_lifecycle_entry(runtime, instance_id, LIFECYCLE_PHASE_PENDING_REMOVE)

    processed = 0
    process_keys = sorted(candidate_ids) if candidate_ids is not None else sorted(list(runtime.entries.keys()))

    for instance_id in process_keys:
        if processed >= LIFECYCLE_MAX_PER_TICK:
            break

        entry = runtime.entries.get(instance_id) or {}
        phase = entry.get("phase")

        if phase == LIFECYCLE_PHASE_PENDING_BOOTSTRAP:
            if instance_id not in current:
                continue
            if process_bootstrap(runtime, hooks, instance_id, current.get(instance_id), now):
                processed += 1
        elif phase == LIFECYCLE_PHASE_PENDING_REMOVE:
            if instance_id in current_ids or not do_full_diff:
                continue
            if process_remove(runtime, hooks, instance_id, now):
                processed += 1

    if do_full_diff:
        runtime.baseline_ids.clear()
        runtime.baseline_ids.update(current_ids)
        for instance_id, obj in current.items():
            if obj is not None and bool(obj.get(AUTO_SYNC_READY_KEY, False)):
                runtime.managed_ids.add(instance_id)
        hooks.poll_non_transform(current, now)
