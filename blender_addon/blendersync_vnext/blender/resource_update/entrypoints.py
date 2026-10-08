from __future__ import annotations

from blender.resource_update.core import MeshUpdateCore


_MESH_UPDATE_CORE = MeshUpdateCore()


def send_mesh_update_once(context: dict) -> dict:
    result = _MESH_UPDATE_CORE.send_mesh_update_once(context)
    state = _MESH_UPDATE_CORE.get_state()
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

