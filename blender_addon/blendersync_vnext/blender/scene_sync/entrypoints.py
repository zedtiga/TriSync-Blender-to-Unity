from __future__ import annotations

import time

from blender.scene_sync.core import SceneSyncCore


_SCENE_SYNC_CORE = SceneSyncCore()


def validate_active_eligibility_once(context: dict) -> dict:
    result = _SCENE_SYNC_CORE.evaluate_eligibility(context)
    return {
        "eligible": result.eligible,
        "reason": result.reason,
    }


def _to_entrypoint_result(result, state) -> dict:
    return {
        "ok": result.ok,
        "reason": result.reason,
        "pair_id": result.pair_id,
        "payload": result.payload,
        "latest_result": {
            "ok": state.last_result.ok if state.last_result else None,
            "reason": state.last_result.reason if state.last_result else None,
            "pair_id": state.last_result.pair_id if state.last_result else None,
        },
        "latest_skip_reason": state.last_skip_reason,
        "counters": {
            "sends": state.counters.sends,
            "skips": state.counters.skips,
            "errors": state.counters.errors,
        },
    }


def sync_active_transform_once(context: dict) -> dict:
    result = _SCENE_SYNC_CORE.sync_active_transform_once(context)
    state = _SCENE_SYNC_CORE.get_state()
    return _to_entrypoint_result(result, state)


def sync_hierarchy_once(context: dict) -> dict:
    result = _SCENE_SYNC_CORE.sync_hierarchy_once(context)
    state = _SCENE_SYNC_CORE.get_state()
    return _to_entrypoint_result(result, state)


def send_object_remove_once(context: dict) -> dict:
    session = context.get("session")
    removed_pair_ids = context.get("removed_pair_ids") or []
    timestamp = int(context.get("timestamp") or time.time())

    if session is None:
        return {
            "ok": False,
            "reason": "session_missing",
            "pair_ids": removed_pair_ids,
            "payload": None,
        }

    if not removed_pair_ids:
        return {
            "ok": False,
            "reason": "removed_pair_ids_empty",
            "pair_ids": [],
            "payload": None,
        }

    payload = {
        "type": "scene_sync.object_remove",
        "timestamp": timestamp,
        "removedPairIds": list(removed_pair_ids),
    }
    result = session.send_auto(payload)
    return {
        "ok": result.ok,
        "reason": result.message if result.ok else (result.error or "object_remove_send_failed"),
        "pair_ids": list(removed_pair_ids),
        "payload": payload,
    }

