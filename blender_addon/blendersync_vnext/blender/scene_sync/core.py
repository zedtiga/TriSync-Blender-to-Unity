from __future__ import annotations

from blender.scene_sync.policy import above_threshold, in_scope
from blender.scene_sync.types import EligibilityResult, SceneSyncState, TransformSnapshot, TransformSyncResult
from blender.session.core import BlenderSessionCore


class SceneSyncCore:
    def __init__(self) -> None:
        self._state = SceneSyncState(active=False)
        self._last_snapshot_by_pair: dict[str, TransformSnapshot] = {}
        self._last_parent_by_child: dict[str, str] = {}

    def get_state(self) -> SceneSyncState:
        return self._state

    def evaluate_eligibility(self, context: dict) -> EligibilityResult:
        session: BlenderSessionCore = context.get("session")
        pair_entry = context.get("pair_entry", {})
        policy = context.get("policy", {})

        if session is None or not session.is_alive():
            return EligibilityResult(False, "session_inactive")

        if not pair_entry.get("mapped", False):
            return EligibilityResult(False, "not_mapped")

        if not pair_entry.get("syncEnabled", False):
            return EligibilityResult(False, "sync_disabled")

        policy_scope = policy.get("scope", "selected")
        object_scope = context.get("object_scope", "selected")
        if not in_scope(policy_scope, object_scope):
            return EligibilityResult(False, "out_of_scope")

        return EligibilityResult(True, None)

    def sync_active_transform_once(self, context: dict) -> TransformSyncResult:
        eligibility = self.evaluate_eligibility(context)
        pair_entry = context.get("pair_entry", {})
        pair_id = pair_entry.get("pairId")

        if not eligibility.eligible:
            self._state.active = False
            self._state.last_skip_reason = eligibility.reason
            self._state.counters.skips += 1
            result = TransformSyncResult(ok=False, reason=eligibility.reason, pair_id=pair_id)
            self._state.last_result = result
            return result

        obj_transform = context.get("active_transform") or {}
        current = TransformSnapshot(
            position=tuple(obj_transform.get("position", (0.0, 0.0, 0.0))),
            rotation=tuple(obj_transform.get("rotation", (0.0, 0.0, 0.0, 1.0))),
            scale=tuple(obj_transform.get("scale", (1.0, 1.0, 1.0))),
        )

        threshold = float(context.get("policy", {}).get("transform_threshold", 0.001))
        previous = self._last_snapshot_by_pair.get(pair_id)
        if previous is not None and not above_threshold(previous, current, threshold):
            self._state.active = True
            self._state.last_skip_reason = "below_threshold"
            self._state.counters.skips += 1
            result = TransformSyncResult(ok=False, reason="below_threshold", pair_id=pair_id)
            self._state.last_result = result
            return result

        payload = {
            "type": "scene_sync.transform",
            "timestamp": int(context.get("timestamp", 0)),
            "pairId": pair_id,
            "sourceHint": str(context.get("source_hint") or "auto_sync"),
            "position": list(current.position),
            "rotation": list(current.rotation),
            "scale": list(current.scale),
        }

        try:
            session: BlenderSessionCore = context["session"]
            send_result = session.send_auto(payload)
            if not send_result.ok:
                self._state.active = True
                self._state.last_skip_reason = "transport_failure"
                self._state.counters.errors += 1
                result = TransformSyncResult(ok=False, reason="transport_failure", pair_id=pair_id, payload=payload)
                self._state.last_result = result
                return result

            self._last_snapshot_by_pair[pair_id] = current
            self._state.active = True
            self._state.last_skip_reason = None
            self._state.counters.sends += 1
            result = TransformSyncResult(ok=True, reason=None, pair_id=pair_id, payload=payload)
            self._state.last_result = result
            return result

        except Exception:
            self._state.active = True
            self._state.last_skip_reason = "transport_failure"
            self._state.counters.errors += 1
            result = TransformSyncResult(ok=False, reason="transport_failure", pair_id=pair_id, payload=payload)
            self._state.last_result = result
            return result

    def sync_hierarchy_once(self, context: dict) -> TransformSyncResult:
        session: BlenderSessionCore = context.get("session")
        child_entry = context.get("child_pair_entry", {})
        parent_entry = context.get("parent_pair_entry", {})

        child_pair_id = child_entry.get("pairId")
        parent_pair_id = parent_entry.get("pairId")

        if session is None or not session.is_alive():
            self._state.active = False
            self._state.last_skip_reason = "session_inactive"
            self._state.counters.skips += 1
            result = TransformSyncResult(ok=False, reason="session_inactive", pair_id=child_pair_id)
            self._state.last_result = result
            return result

        if not child_entry.get("mapped", False):
            self._state.active = True
            self._state.last_skip_reason = "child_not_mapped"
            self._state.counters.skips += 1
            result = TransformSyncResult(ok=False, reason="child_not_mapped", pair_id=child_pair_id)
            self._state.last_result = result
            return result

        if not parent_entry.get("mapped", False):
            self._state.active = True
            self._state.last_skip_reason = "parent_not_mapped"
            self._state.counters.skips += 1
            result = TransformSyncResult(ok=False, reason="parent_not_mapped", pair_id=child_pair_id)
            self._state.last_result = result
            return result

        if not child_pair_id or not parent_pair_id or child_pair_id == parent_pair_id:
            self._state.active = True
            self._state.last_skip_reason = "invalid_relation"
            self._state.counters.skips += 1
            result = TransformSyncResult(ok=False, reason="invalid_relation", pair_id=child_pair_id)
            self._state.last_result = result
            return result

        if self._last_parent_by_child.get(child_pair_id) == parent_pair_id:
            self._state.active = True
            self._state.last_skip_reason = "no_relation_change"
            self._state.counters.skips += 1
            result = TransformSyncResult(ok=False, reason="no_relation_change", pair_id=child_pair_id)
            self._state.last_result = result
            return result

        payload = {
            "type": "scene_sync.hierarchy",
            "timestamp": int(context.get("timestamp", 0)),
            "childPairId": child_pair_id,
            "parentPairId": parent_pair_id,
        }

        try:
            send_result = session.send_auto(payload)
            if not send_result.ok:
                self._state.active = True
                self._state.last_skip_reason = "transport_failure"
                self._state.counters.errors += 1
                result = TransformSyncResult(ok=False, reason="transport_failure", pair_id=child_pair_id, payload=payload)
                self._state.last_result = result
                return result

            self._last_parent_by_child[child_pair_id] = parent_pair_id
            self._state.active = True
            self._state.last_skip_reason = None
            self._state.counters.sends += 1
            result = TransformSyncResult(ok=True, reason=None, pair_id=child_pair_id, payload=payload)
            self._state.last_result = result
            return result
        except Exception:
            self._state.active = True
            self._state.last_skip_reason = "transport_failure"
            self._state.counters.errors += 1
            result = TransformSyncResult(ok=False, reason="transport_failure", pair_id=child_pair_id, payload=payload)
            self._state.last_result = result
            return result

