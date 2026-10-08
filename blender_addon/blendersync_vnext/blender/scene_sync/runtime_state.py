from __future__ import annotations

from array import array
from dataclasses import dataclass, field


@dataclass
class LifecycleRuntimeState:
    baseline_ids: set[str] = field(default_factory=set)
    managed_ids: set[str] = field(default_factory=set)
    entries: dict[str, dict] = field(default_factory=dict)
    new_reason_by_id: dict[str, str] = field(default_factory=dict)
    delete_reason_by_id: dict[str, str] = field(default_factory=dict)

    def reset(self) -> None:
        self.baseline_ids.clear()
        self.managed_ids.clear()
        self.entries.clear()
        self.new_reason_by_id.clear()
        self.delete_reason_by_id.clear()


@dataclass
class ObjectStateRuntimeState:
    signatures_by_pair: dict[str, str] = field(default_factory=dict)
    non_transform_signatures_by_pair: dict[str, str] = field(default_factory=dict)
    dirty_queue: dict[str, dict] = field(default_factory=dict)
    motion_burst_until_by_pair: dict[str, float] = field(default_factory=dict)
    active_fallback_next_at_by_pair: dict[str, float] = field(default_factory=dict)
    non_transform_poll_cursor: int = 0

    def reset(self) -> None:
        self.signatures_by_pair.clear()
        self.non_transform_signatures_by_pair.clear()
        self.non_transform_poll_cursor = 0
        self.reset_transient()

    def reset_transient(self) -> None:
        self.dirty_queue.clear()
        self.motion_burst_until_by_pair.clear()
        self.active_fallback_next_at_by_pair.clear()

    def clear_pair(self, pair_id: str) -> None:
        self.signatures_by_pair.pop(pair_id, None)
        self.non_transform_signatures_by_pair.pop(pair_id, None)
        self.dirty_queue.pop(pair_id, None)
        self.motion_burst_until_by_pair.pop(pair_id, None)
        self.active_fallback_next_at_by_pair.pop(pair_id, None)


@dataclass
class MaterialRuntimeState:
    refs_by_pair: dict[str, tuple[str, ...]] = field(default_factory=dict)
    slots_poll_next_at_by_pair: dict[str, float] = field(default_factory=dict)
    pending_bundles_by_ref: dict[str, dict] = field(default_factory=dict)
    waiting_pair_ids_by_ref: dict[str, set[str]] = field(default_factory=dict)
    pending_resends_by_ref: dict[str, dict] = field(default_factory=dict)
    retry_after_by_ref: dict[str, float] = field(default_factory=dict)
    send_inflight: bool = False

    def reset(self) -> None:
        self.refs_by_pair.clear()
        self.slots_poll_next_at_by_pair.clear()
        self.pending_bundles_by_ref.clear()
        self.waiting_pair_ids_by_ref.clear()
        self.pending_resends_by_ref.clear()
        self.retry_after_by_ref.clear()
        self.send_inflight = False

    def clear_pair_cache(self, pair_id: str) -> None:
        self.refs_by_pair.pop(pair_id, None)
        self.slots_poll_next_at_by_pair.pop(pair_id, None)

    def clear_pending_send(self, material_ref: str) -> set[str]:
        ref = str(material_ref or "").strip()
        if not ref:
            return set()
        self.pending_bundles_by_ref.pop(ref, None)
        self.retry_after_by_ref.pop(ref, None)
        return set(self.waiting_pair_ids_by_ref.pop(ref, set()) or set())

    def remove_pair_from_pending(self, pair_id: str) -> dict[str, int]:
        waiting_refs = 0
        preserved_bundles = 0
        for material_ref in list(self.waiting_pair_ids_by_ref.keys()):
            pair_ids = self.waiting_pair_ids_by_ref.get(material_ref)
            if not isinstance(pair_ids, set) or pair_id not in pair_ids:
                continue
            pair_ids.discard(pair_id)
            waiting_refs += 1
            if pair_ids:
                continue
            self.waiting_pair_ids_by_ref.pop(material_ref, None)
            if material_ref in self.pending_bundles_by_ref:
                preserved_bundles += 1

        resend_refs = 0
        for material_ref in list(self.pending_resends_by_ref.keys()):
            entry = self.pending_resends_by_ref.get(material_ref)
            if not isinstance(entry, dict):
                continue
            pair_ids = {
                str(value).strip()
                for value in (entry.get("pairIds") or set())
                if str(value).strip()
            }
            if pair_id not in pair_ids:
                continue
            pair_ids.discard(pair_id)
            resend_refs += 1
            if pair_ids:
                entry["pairIds"] = pair_ids
            else:
                self.pending_resends_by_ref.pop(material_ref, None)

        return {
            "waitingRefs": waiting_refs,
            "preservedBundles": preserved_bundles,
            "resendRefs": resend_refs,
        }


@dataclass
class GeometryPreviewRuntimeState:
    dirty_by_pair: dict[str, dict] = field(default_factory=dict)
    inflight_by_pair: dict[str, bool] = field(default_factory=dict)
    dirty_during_inflight_by_pair: dict[str, bool] = field(default_factory=dict)

    def reset(self) -> None:
        self.dirty_by_pair.clear()
        self.inflight_by_pair.clear()
        self.dirty_during_inflight_by_pair.clear()

    def reset_transient(self) -> int:
        self.inflight_by_pair.clear()
        self.dirty_during_inflight_by_pair.clear()
        demoted = 0
        for state in self.dirty_by_pair.values():
            if state.get("geometry") and state.get("autoEligible", True):
                state["autoEligible"] = False
                demoted += 1
        return demoted

    def clear_pair(self, pair_id: str) -> None:
        self.dirty_by_pair.pop(pair_id, None)
        self.inflight_by_pair.pop(pair_id, None)
        self.dirty_during_inflight_by_pair.pop(pair_id, None)


@dataclass
class EvaluatedPreviewRuntimeState:
    dirty_by_pair: dict[str, dict] = field(default_factory=dict)
    object_name_by_pair: dict[str, str] = field(default_factory=dict)
    last_preview_sent_at_by_pair: dict[str, float] = field(default_factory=dict)
    had_visible_modifiers_by_pair: dict[str, bool] = field(default_factory=dict)
    modifier_signature_by_pair: dict[str, str] = field(default_factory=dict)

    def reset(self) -> None:
        self.dirty_by_pair.clear()
        self.object_name_by_pair.clear()
        self.last_preview_sent_at_by_pair.clear()
        self.had_visible_modifiers_by_pair.clear()
        self.modifier_signature_by_pair.clear()

    def reset_transient(self) -> int:
        demoted = 0
        for state in self.dirty_by_pair.values():
            if str(state.get("dirtyClass") or "") != "context_noise" and state.get("autoEligible", True):
                state["autoEligible"] = False
                demoted += 1
        return demoted

    def clear_pair(self, pair_id: str) -> None:
        self.dirty_by_pair.pop(pair_id, None)
        self.object_name_by_pair.pop(pair_id, None)
        self.last_preview_sent_at_by_pair.pop(pair_id, None)
        self.had_visible_modifiers_by_pair.pop(pair_id, None)
        self.modifier_signature_by_pair.pop(pair_id, None)


@dataclass
class MeshStructureRuntimeState:
    uv_channels_signature_by_pair: dict[str, str] = field(default_factory=dict)
    uv_channels_poll_next_time_by_pair: dict[str, float] = field(default_factory=dict)
    color_attributes_signature_by_pair: dict[str, str] = field(default_factory=dict)
    color_attributes_poll_next_time_by_pair: dict[str, float] = field(default_factory=dict)
    shape_key_structure_signature_by_pair: dict[str, str] = field(default_factory=dict)
    shape_key_structure_poll_next_time_by_pair: dict[str, float] = field(default_factory=dict)

    def reset(self) -> None:
        self.uv_channels_signature_by_pair.clear()
        self.uv_channels_poll_next_time_by_pair.clear()
        self.color_attributes_signature_by_pair.clear()
        self.color_attributes_poll_next_time_by_pair.clear()
        self.shape_key_structure_signature_by_pair.clear()
        self.shape_key_structure_poll_next_time_by_pair.clear()

    def clear_pair(self, pair_id: str) -> None:
        self.uv_channels_signature_by_pair.pop(pair_id, None)
        self.uv_channels_poll_next_time_by_pair.pop(pair_id, None)
        self.color_attributes_signature_by_pair.pop(pair_id, None)
        self.color_attributes_poll_next_time_by_pair.pop(pair_id, None)
        self.shape_key_structure_signature_by_pair.pop(pair_id, None)
        self.shape_key_structure_poll_next_time_by_pair.pop(pair_id, None)


@dataclass
class ShapeKeyRuntimeState:
    edit_skip_logged_by_pair: set[str] = field(default_factory=set)
    last_weights_by_pair: dict[str, tuple[tuple[str, float], ...]] = field(default_factory=dict)
    next_weight_send_time_by_pair: dict[str, float] = field(default_factory=dict)

    def reset(self) -> None:
        self.edit_skip_logged_by_pair.clear()
        self.last_weights_by_pair.clear()
        self.next_weight_send_time_by_pair.clear()

    def clear_pair(self, pair_id: str) -> None:
        self.edit_skip_logged_by_pair.discard(pair_id)
        self.last_weights_by_pair.pop(pair_id, None)
        self.next_weight_send_time_by_pair.pop(pair_id, None)


@dataclass
class UvPreviewRuntimeState:
    last_skip_log_by_pair: dict[str, float] = field(default_factory=dict)
    inflight_by_pair: dict[str, bool] = field(default_factory=dict)
    last_buffer_cleanup_at: float = 0.0

    def reset(self) -> None:
        self.last_skip_log_by_pair.clear()
        self.inflight_by_pair.clear()
        self.last_buffer_cleanup_at = 0.0

    def clear_pair(self, pair_id: str) -> None:
        self.last_skip_log_by_pair.pop(pair_id, None)
        self.inflight_by_pair.pop(pair_id, None)


@dataclass
class PreviewMappingRuntimeState:
    source_indices_by_pair: dict[str, array] = field(default_factory=dict)
    source_loop_indices_by_pair: dict[str, array] = field(default_factory=dict)
    hashes_by_pair: dict[str, dict[str, str]] = field(default_factory=dict)
    last_mesh_diag_time: float = 0.0

    def reset(self) -> None:
        self.source_indices_by_pair.clear()
        self.source_loop_indices_by_pair.clear()
        self.hashes_by_pair.clear()
        self.last_mesh_diag_time = 0.0

    def clear_pair(self, pair_id: str) -> tuple[bool, bool]:
        had_indices = pair_id in self.source_indices_by_pair
        had_loop_indices = pair_id in self.source_loop_indices_by_pair
        self.source_indices_by_pair.pop(pair_id, None)
        self.source_loop_indices_by_pair.pop(pair_id, None)
        self.hashes_by_pair.pop(pair_id, None)
        return had_indices, had_loop_indices
