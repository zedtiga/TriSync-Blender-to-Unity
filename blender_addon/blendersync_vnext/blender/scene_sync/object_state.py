from __future__ import annotations

from dataclasses import dataclass
from typing import Callable
import json
import time

from blender.common.log import trace, warn
from blender.identity import ensure_instance_id, get_instance_id
from blender.scene_sync.entrypoints import sync_active_transform_once
from blender.scene_sync.runtime_state import ObjectStateRuntimeState
from blender.transport.entrypoints import send_object_state_update
from blender.ui.object_context_builders import build_object_state_payload
from blender.ui.state_view import get_session

try:
    import bpy  # type: ignore
except ImportError:
    bpy = None


AUTO_SYNC_READY_KEY = "blendersync_auto_sync_ready"
OBJECT_STATE_MOTION_BURST_SECONDS = 0.35
OBJECT_STATE_MOTION_BURST_HZ = 30.0
OBJECT_STATE_NON_TRANSFORM_POLL_MAX_PER_TICK = 32
OBJECT_STATE_ACTIVE_FALLBACK_HZ = 0.0
SUPPORTED_OBJECT_TYPES = {"MESH", "CAMERA", "LIGHT", "ARMATURE", "EMPTY"}


@dataclass(frozen=True)
class ObjectStateHooks:
    get_sync_enabled: Callable[[], bool]
    pair_id_for_object: Callable[[object], str | None]
    object_for_pair: Callable[[str], object | None]
    has_pending_geometry_dirty: Callable[[str | None], bool]


def build_object_transform_context(obj, package_id: str = "pkg-object-state-sync-auto") -> dict | None:
    if obj is None or not bool(obj.get(AUTO_SYNC_READY_KEY, False)):
        return None
    session = get_session()
    if session is None:
        return None

    matrix_world = getattr(obj, "matrix_world", None)
    world_position = matrix_world.to_translation() if matrix_world is not None else obj.location
    q = matrix_world.to_quaternion() if matrix_world is not None else (
        obj.rotation_quaternion
        if getattr(obj, "rotation_mode", None) == "QUATERNION"
        else obj.rotation_euler.to_quaternion()
    )
    pair_id = f"pair-{ensure_instance_id(obj)}"
    source_hint = "manual_sync" if "manual" in str(package_id or "") else "auto_sync"
    return {
        "session": session,
        "timestamp": int(time.time()),
        "object_scope": "selected",
        "source_hint": source_hint,
        "object_name": str(getattr(obj, "name", "") or ""),
        "pair_entry": {"pairId": pair_id, "mapped": True, "syncEnabled": True},
        "active_transform": {
            "position": [float(world_position.x), float(world_position.y), float(world_position.z)],
            "rotation": [float(q.x), float(q.y), float(q.z), float(q.w)],
            "scale": [float(obj.scale.x), float(obj.scale.y), float(obj.scale.z)],
        },
        "policy": {"scope": "selected", "transform_threshold": 0.001},
    }


def rounded_matrix_world(obj, digits: int = 6):
    try:
        matrix = getattr(obj, "matrix_world")
        return tuple(tuple(round(float(matrix[row][col]), digits) for col in range(4)) for row in range(4))
    except Exception:
        return tuple()


def object_state_signature(obj) -> str:
    if obj is None:
        return ""
    parent = getattr(obj, "parent", None)
    parent_pair = ""
    if parent is not None:
        try:
            parent_pair = f"pair-{get_instance_id(parent)}" if get_instance_id(parent) else ""
        except Exception:
            parent_pair = ""
    payload = {
        "name": str(getattr(obj, "name", "") or ""),
        "type": str(getattr(obj, "type", "") or ""),
        "matrixWorld": rounded_matrix_world(obj),
        "hideViewport": bool(getattr(obj, "hide_viewport", False)),
        "hideRender": bool(getattr(obj, "hide_render", False)),
        "parentPairId": parent_pair,
        "parentName": str(getattr(parent, "name", "") or "") if parent is not None else "",
    }
    try:
        payload["visible"] = bool(obj.visible_get())
    except Exception:
        pass
    try:
        return json.dumps(payload, sort_keys=True, ensure_ascii=False, default=str)
    except Exception:
        return repr(payload)


def object_non_transform_signature(obj) -> str:
    if obj is None:
        return ""
    parent = getattr(obj, "parent", None)
    parent_pair = ""
    if parent is not None:
        try:
            parent_pair = f"pair-{get_instance_id(parent)}" if get_instance_id(parent) else ""
        except Exception:
            parent_pair = ""
    payload = {
        "name": str(getattr(obj, "name", "") or ""),
        "type": str(getattr(obj, "type", "") or ""),
        "hideViewport": bool(getattr(obj, "hide_viewport", False)),
        "hideRender": bool(getattr(obj, "hide_render", False)),
        "parentPairId": parent_pair,
        "parentName": str(getattr(parent, "name", "") or "") if parent is not None else "",
    }
    try:
        payload["visible"] = bool(obj.visible_get())
    except Exception:
        pass
    try:
        return json.dumps(payload, sort_keys=True, ensure_ascii=False, default=str)
    except Exception:
        return repr(payload)


def non_transform_state_changed(
    runtime: ObjectStateRuntimeState,
    hooks: ObjectStateHooks,
    obj,
    *,
    update_cache: bool = True,
) -> bool:
    if obj is None:
        return False
    pair_id = hooks.pair_id_for_object(obj)
    if not pair_id:
        return True
    signature = object_non_transform_signature(obj)
    previous = runtime.non_transform_signatures_by_pair.get(pair_id)
    if previous == signature:
        return False
    if update_cache:
        runtime.non_transform_signatures_by_pair[pair_id] = signature
    return True


def cache_runtime_signatures(runtime: ObjectStateRuntimeState, hooks: ObjectStateHooks, obj) -> None:
    pair_id = hooks.pair_id_for_object(obj)
    if not pair_id:
        return
    runtime.signatures_by_pair[pair_id] = object_state_signature(obj)
    runtime.non_transform_signatures_by_pair[pair_id] = object_non_transform_signature(obj)


def state_changed(
    runtime: ObjectStateRuntimeState,
    hooks: ObjectStateHooks,
    obj,
    *,
    update_cache: bool = True,
) -> bool:
    if obj is None:
        return False
    try:
        pair_id = f"pair-{ensure_instance_id(obj)}"
    except Exception:
        return True
    signature = object_state_signature(obj)
    previous = runtime.signatures_by_pair.get(pair_id)
    if previous == signature:
        return False
    if update_cache:
        runtime.signatures_by_pair[pair_id] = signature
    return True


def send_object_state_update_for_object(runtime: ObjectStateRuntimeState, hooks: ObjectStateHooks, obj, *, source: str = "dirty_queue") -> bool:
    if obj is None:
        return False
    payload = build_object_state_payload([obj], source_hint=str(source or "auto_object_state"))
    items = payload.get("objects") or []
    if not items:
        return False
    result = send_object_state_update({
        "session": get_session(),
        "triggerType": source,
        "sendMode": "object_state_only",
        "correlationId": f"auto-state-{int(time.time() * 1000)}",
        "objectStatePayload": payload,
        "objectStateCount": len(items),
    })
    ok = bool(getattr(result, "ok", False))
    if ok:
        cache_runtime_signatures(runtime, hooks, obj)
    return ok


def enter_motion_burst(runtime: ObjectStateRuntimeState, pair_id: str | None, *, now: float | None = None) -> None:
    if not pair_id:
        return
    now = time.time() if now is None else now
    until = now + OBJECT_STATE_MOTION_BURST_SECONDS
    previous = float(runtime.motion_burst_until_by_pair.get(pair_id, 0.0) or 0.0)
    runtime.motion_burst_until_by_pair[pair_id] = max(previous, until)


def mark_dirty(runtime: ObjectStateRuntimeState, hooks: ObjectStateHooks, obj, *, source: str = "depsgraph", now: float | None = None) -> None:
    if obj is None or not hooks.get_sync_enabled():
        return
    if getattr(obj, "type", None) not in SUPPORTED_OBJECT_TYPES or not bool(obj.get(AUTO_SYNC_READY_KEY, False)):
        return
    pair_id = hooks.pair_id_for_object(obj)
    if not pair_id:
        return
    now = time.time() if now is None else now
    state = runtime.dirty_queue.setdefault(pair_id, {})
    state["objectName"] = str(getattr(obj, "name", "") or "")
    state["source"] = source
    state["lastDirtyAt"] = now
    state["count"] = int(state.get("count") or 0) + 1
    enter_motion_burst(runtime, pair_id, now=now)


def send_runtime_state_for_object(runtime: ObjectStateRuntimeState, hooks: ObjectStateHooks, obj, *, source: str = "dirty_queue") -> bool:
    if obj is None:
        return False
    if non_transform_state_changed(runtime, hooks, obj, update_cache=False):
        return send_object_state_update_for_object(runtime, hooks, obj, source=source)
    if not state_changed(runtime, hooks, obj):
        return False
    context = build_object_transform_context(obj)
    if context is None:
        return False
    result = sync_active_transform_once(context)
    ok = bool(result.get("ok")) if isinstance(result, dict) else True
    fields = {
        "pairId": context.get("pair_id"),
        "objectName": context.get("object_name"),
        "source": source,
        "reason": result.get("reason") if isinstance(result, dict) else None,
    }
    if ok:
        trace(
            "ObjectState",
            "transform_sent",
            lambda: "Sent an object transform update.",
            lambda: fields,
        )
    elif fields["reason"] == "transport_failure":
        warn("ObjectState", "transform_send_failed", fields["reason"] or "send_failed", fields)
    else:
        trace(
            "ObjectState",
            "transform_skipped",
            lambda: "Skipped an object transform update.",
            lambda: fields,
        )
    return ok


def pump_motion_bursts(runtime: ObjectStateRuntimeState, hooks: ObjectStateHooks, now: float | None = None, *, max_items: int = 32) -> int:
    if not runtime.motion_burst_until_by_pair:
        return 0
    now = time.time() if now is None else now
    sampled = 0
    for pair_id, until in list(runtime.motion_burst_until_by_pair.items())[:max_items]:
        if now > float(until or 0.0):
            runtime.motion_burst_until_by_pair.pop(pair_id, None)
            continue
        obj = hooks.object_for_pair(pair_id)
        if obj is None or not bool(obj.get(AUTO_SYNC_READY_KEY, False)):
            runtime.motion_burst_until_by_pair.pop(pair_id, None)
            continue
        if hooks.has_pending_geometry_dirty(pair_id):
            continue
        sampled += 1
        sent = send_runtime_state_for_object(runtime, hooks, obj, source="motion_burst")
        if sent:
            enter_motion_burst(runtime, pair_id, now=now)
    return sampled


def should_run_active_fallback(runtime: ObjectStateRuntimeState, hooks: ObjectStateHooks, obj, now: float | None = None) -> bool:
    if obj is None or not bool(obj.get(AUTO_SYNC_READY_KEY, False)):
        return False
    pair_id = hooks.pair_id_for_object(obj)
    if not pair_id or pair_id in runtime.dirty_queue or pair_id in runtime.motion_burst_until_by_pair:
        return False
    now = time.time() if now is None else now
    next_time = float(runtime.active_fallback_next_at_by_pair.get(pair_id, 0.0) or 0.0)
    if now < next_time or OBJECT_STATE_ACTIVE_FALLBACK_HZ <= 0:
        return False
    interval = 1.0 / max(0.1, OBJECT_STATE_ACTIVE_FALLBACK_HZ)
    runtime.active_fallback_next_at_by_pair[pair_id] = now + interval
    return True


def send_deferred_after_mesh_preview(runtime: ObjectStateRuntimeState, hooks: ObjectStateHooks, obj, pair_id: str | None, *, source: str = "after_mesh_preview") -> bool:
    if obj is None or not pair_id:
        return False
    queued_state = runtime.dirty_queue.get(pair_id)
    if not queued_state:
        return False
    send_source = str(source or queued_state.get("source") or "after_mesh_preview")
    ok = send_object_state_update_for_object(runtime, hooks, obj, source=send_source)
    if ok:
        runtime.dirty_queue.pop(pair_id, None)
        runtime.motion_burst_until_by_pair.pop(pair_id, None)
    return bool(ok)


def pump_dirty_queue(runtime: ObjectStateRuntimeState, hooks: ObjectStateHooks, now: float | None = None, *, max_items: int = 32) -> int:
    if not runtime.dirty_queue:
        return 0
    now = time.time() if now is None else now
    sent = 0
    for pair_id, state in list(runtime.dirty_queue.items())[:max_items]:
        obj = hooks.object_for_pair(pair_id)
        if obj is None or not bool(obj.get(AUTO_SYNC_READY_KEY, False)):
            runtime.dirty_queue.pop(pair_id, None)
            continue
        if hooks.has_pending_geometry_dirty(pair_id):
            continue
        source = str(state.get("source") or "dirty_queue")
        if non_transform_state_changed(runtime, hooks, obj, update_cache=False):
            ok = send_object_state_update_for_object(runtime, hooks, obj, source=source)
            if ok:
                runtime.dirty_queue.pop(pair_id, None)
                runtime.motion_burst_until_by_pair.pop(pair_id, None)
                sent += 1
            continue
        if not state_changed(runtime, hooks, obj):
            runtime.dirty_queue.pop(pair_id, None)
            continue
        context = build_object_transform_context(obj)
        if context is None:
            runtime.dirty_queue.pop(pair_id, None)
            continue
        result = sync_active_transform_once(context)
        ok = bool(result.get("ok")) if isinstance(result, dict) else True
        if ok:
            runtime.dirty_queue.pop(pair_id, None)
            sent += 1
            enter_motion_burst(runtime, pair_id, now=now)
        fields = {
            "pairId": context.get("pair_id"),
            "objectName": context.get("object_name"),
            "source": source,
            "dirtyCount": state.get("count"),
            "reason": result.get("reason") if isinstance(result, dict) else None,
        }
        if ok:
            trace(
                "ObjectState",
                "dirty_transform_sent",
                lambda: "Sent a queued object transform update.",
                lambda: fields,
            )
        elif fields["reason"] == "transport_failure":
            warn("ObjectState", "dirty_transform_send_failed", fields["reason"] or "send_failed", fields)
        else:
            trace(
                "ObjectState",
                "dirty_transform_skipped",
                lambda: "Skipped a queued object transform update.",
                lambda: fields,
            )
    return sent


def poll_scene_non_transform(runtime: ObjectStateRuntimeState, hooks: ObjectStateHooks, current: dict[str, object], now: float) -> int:
    if not current:
        return 0
    ready_items = [
        (instance_id, obj)
        for instance_id, obj in sorted(current.items())
        if obj is not None and getattr(obj, "type", None) in SUPPORTED_OBJECT_TYPES and bool(obj.get(AUTO_SYNC_READY_KEY, False))
    ]
    if not ready_items:
        runtime.non_transform_poll_cursor = 0
        return 0
    checked = 0
    sent = 0
    count = len(ready_items)
    start = runtime.non_transform_poll_cursor % count
    limit = min(OBJECT_STATE_NON_TRANSFORM_POLL_MAX_PER_TICK, count)
    for offset in range(limit):
        instance_id, obj = ready_items[(start + offset) % count]
        checked += 1
        pair_id = f"pair-{instance_id}"
        if not non_transform_state_changed(runtime, hooks, obj, update_cache=False):
            continue
        ok = send_object_state_update_for_object(runtime, hooks, obj, source="non_transform_state_poll")
        if ok:
            sent += 1
    runtime.non_transform_poll_cursor = (start + checked) % count
    return sent
