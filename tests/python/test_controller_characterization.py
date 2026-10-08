from __future__ import annotations

import sys
import unittest
from contextlib import ExitStack
from pathlib import Path
from types import SimpleNamespace
from unittest import mock


ADDON_ROOT = Path(__file__).resolve().parents[2] / "blender_addon" / "blendersync_vnext"
sys.path.insert(0, str(ADDON_ROOT))

from blender.scene_sync import controller
from blender.scene_sync import object_lifecycle
from blender.scene_sync import object_state
from blender.scene_sync import material_sync
from blender.scene_sync import structure_watch


class FakeObject(dict):
    def __init__(self, name: str = "Cube", *, ready: bool = True) -> None:
        super().__init__()
        self.name = name
        self.type = "MESH"
        self.modifiers = []
        if ready:
            self[controller.AUTO_SYNC_READY_KEY] = True


class ControllerCharacterizationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.print_patch = mock.patch("builtins.print")
        self.print_patch.start()
        controller.reset_file_runtime_state(reason="characterization_setup")

    def tearDown(self) -> None:
        controller.reset_file_runtime_state(reason="characterization_teardown")
        self.print_patch.stop()

    def test_geometry_dirty_requires_a_ready_managed_object(self) -> None:
        pair_id = "pair-object"
        with mock.patch.object(controller, "_object_for_pair_id", return_value=None):
            controller._mark_pair_dirty(pair_id, now=1.0)
        self.assertNotIn(pair_id, controller._geometry_preview_runtime.dirty_by_pair)

        with mock.patch.object(controller, "_object_for_pair_id", return_value=FakeObject(ready=False)):
            controller._mark_pair_dirty(pair_id, now=2.0)
        self.assertNotIn(pair_id, controller._geometry_preview_runtime.dirty_by_pair)

    def test_geometry_dirty_merges_latest_event_and_tracks_inflight_changes(self) -> None:
        pair_id = "pair-object"
        obj = FakeObject()
        with ExitStack() as stack:
            stack.enter_context(mock.patch.object(controller, "_object_for_pair_id", return_value=obj))
            stack.enter_context(mock.patch.object(controller, "_get_sync_enabled", return_value=True))
            stack.enter_context(mock.patch.object(controller, "_get_mesh_preview_debounce_seconds", return_value=0.25))
            controller._mark_pair_dirty(pair_id, source="first", now=10.0)
            controller._geometry_preview_runtime.inflight_by_pair[pair_id] = True
            controller._mark_pair_dirty(
                pair_id,
                source="second",
                now=10.1,
                force_mesh_send=True,
            )

        state = controller._geometry_preview_runtime.dirty_by_pair[pair_id]
        self.assertTrue(state["geometry"])
        self.assertEqual("second", state["source"])
        self.assertEqual(2, state["count"])
        self.assertAlmostEqual(10.35, state["previewDueAt"])
        self.assertTrue(state["autoEligible"])
        self.assertTrue(state["forceMeshSend"])
        self.assertTrue(controller._geometry_preview_runtime.dirty_during_inflight_by_pair[pair_id])

    def test_geometry_dirty_consume_respects_due_time_and_auto_eligibility(self) -> None:
        pair_id = "pair-object"
        controller._geometry_preview_runtime.dirty_by_pair[pair_id] = {
            "geometry": True,
            "previewDueAt": 20.0,
            "autoEligible": False,
            "forceMeshSend": True,
        }

        with mock.patch.object(controller.time, "time", return_value=21.0):
            self.assertIsNone(
                controller._consume_pair_geometry_dirty_event(pair_id, require_auto_eligible=True)
            )

        controller._geometry_preview_runtime.dirty_by_pair[pair_id]["autoEligible"] = True
        with mock.patch.object(controller.time, "time", return_value=19.0):
            self.assertIsNone(controller._consume_pair_geometry_dirty_event(pair_id))
            consumed = controller._consume_pair_geometry_dirty_event(pair_id, respect_due=False)

        self.assertTrue(consumed["forceMeshSend"])
        self.assertNotIn(pair_id, controller._geometry_preview_runtime.dirty_by_pair)

    def test_auto_sync_transient_reset_demotes_dirty_without_deleting_it(self) -> None:
        controller._geometry_preview_runtime.dirty_by_pair["pair-geometry"] = {
            "geometry": True,
            "autoEligible": True,
            "forceMeshSend": True,
        }
        controller._evaluated_preview_runtime.dirty_by_pair["pair-content"] = {
            "dirtyClass": "content",
            "autoEligible": True,
        }
        controller._evaluated_preview_runtime.dirty_by_pair["pair-noise"] = {
            "dirtyClass": "context_noise",
            "autoEligible": True,
        }
        controller._object_state_runtime.dirty_queue["pair-object"] = {"source": "test"}
        controller._geometry_preview_runtime.inflight_by_pair["pair-geometry"] = True

        controller._reset_auto_sync_transient_dirty(reason="test")

        self.assertFalse(controller._geometry_preview_runtime.dirty_by_pair["pair-geometry"]["autoEligible"])
        self.assertTrue(controller._geometry_preview_runtime.dirty_by_pair["pair-geometry"]["forceMeshSend"])
        self.assertFalse(controller._evaluated_preview_runtime.dirty_by_pair["pair-content"]["autoEligible"])
        self.assertTrue(controller._evaluated_preview_runtime.dirty_by_pair["pair-noise"]["autoEligible"])
        self.assertEqual({}, controller._object_state_runtime.dirty_queue)
        self.assertEqual({}, controller._geometry_preview_runtime.inflight_by_pair)

    def test_evaluated_context_noise_does_not_move_content_preview_due_time(self) -> None:
        pair_id = "pair-evaluated"
        obj = FakeObject("Evaluated")
        obj.modifiers = [SimpleNamespace(show_viewport=True)]
        with ExitStack() as stack:
            stack.enter_context(mock.patch.object(controller, "_should_auto_rebuild_evaluated_object", return_value=True))
            stack.enter_context(mock.patch.object(controller, "_pair_id_for_object_readonly", return_value=pair_id))
            stack.enter_context(mock.patch.object(controller, "_get_sync_enabled", return_value=True))
            stack.enter_context(mock.patch.object(controller, "_get_evaluated_mesh_preview_debounce_seconds", return_value=0.5))
            stack.enter_context(mock.patch.object(controller, "modifier_stack_signature", return_value="sig-1"))
            controller._mark_evaluated_mesh_dirty(obj, source="content-change", dirty_class="content", now=5.0)
            controller._mark_evaluated_mesh_dirty(obj, source="selection-noise", dirty_class="context_noise", now=6.0)

        state = controller._evaluated_preview_runtime.dirty_by_pair[pair_id]
        self.assertEqual("content", state["dirtyClass"])
        self.assertEqual("content-change", state["source"])
        self.assertEqual("selection-noise", state["lastNoiseSource"])
        self.assertAlmostEqual(5.5, state["previewDueAt"])
        self.assertEqual(2, state["count"])

    def test_recent_transform_noise_suppresses_node_tree_fallback(self) -> None:
        pair_id = "pair-evaluated"
        obj = FakeObject("Evaluated")
        obj.modifiers = [SimpleNamespace(show_viewport=True)]
        controller._evaluated_preview_runtime.modifier_signature_by_pair[pair_id] = "sig-1"
        with ExitStack() as stack:
            stack.enter_context(mock.patch.object(controller, "_should_auto_rebuild_evaluated_object", return_value=True))
            stack.enter_context(mock.patch.object(controller, "_pair_id_for_object_readonly", return_value=pair_id))
            stack.enter_context(mock.patch.object(controller, "_get_sync_enabled", return_value=True))
            stack.enter_context(mock.patch.object(controller, "_get_evaluated_mesh_preview_debounce_seconds", return_value=0.5))
            stack.enter_context(mock.patch.object(controller, "modifier_stack_signature", return_value="sig-1"))
            controller._mark_evaluated_mesh_dirty(
                obj,
                source="object_transform_update_noise",
                dirty_class="context_noise",
                now=5.0,
            )
            controller._mark_evaluated_mesh_dirty(
                obj,
                source="depsgraph_GeometryNodeTree",
                dirty_class="content",
                now=5.1,
            )

        state = controller._evaluated_preview_runtime.dirty_by_pair[pair_id]
        self.assertEqual("context_noise", state["dirtyClass"])
        self.assertEqual("object_transform_update_noise", state["source"])
        self.assertEqual(0.0, state["previewDueAt"])

    def test_node_tree_signature_change_is_not_suppressed_by_recent_transform(self) -> None:
        pair_id = "pair-evaluated"
        obj = FakeObject("Evaluated")
        obj.modifiers = [SimpleNamespace(show_viewport=True)]
        controller._evaluated_preview_runtime.modifier_signature_by_pair[pair_id] = "sig-old"
        with ExitStack() as stack:
            stack.enter_context(mock.patch.object(controller, "_should_auto_rebuild_evaluated_object", return_value=True))
            stack.enter_context(mock.patch.object(controller, "_pair_id_for_object_readonly", return_value=pair_id))
            stack.enter_context(mock.patch.object(controller, "_get_sync_enabled", return_value=True))
            stack.enter_context(mock.patch.object(controller, "_get_evaluated_mesh_preview_debounce_seconds", return_value=0.5))
            stack.enter_context(mock.patch.object(controller, "modifier_stack_signature", return_value="sig-new"))
            controller._mark_evaluated_mesh_dirty(
                obj,
                source="object_update_no_modifier_signature_change",
                dirty_class="context_noise",
                now=5.0,
            )
            controller._mark_evaluated_mesh_dirty(
                obj,
                source="depsgraph_GeometryNodeTree",
                dirty_class="content",
                now=5.1,
            )

        state = controller._evaluated_preview_runtime.dirty_by_pair[pair_id]
        self.assertEqual("content", state["dirtyClass"])
        self.assertEqual("depsgraph_GeometryNodeTree", state["source"])
        self.assertEqual("sig-new", state["pendingModifierSignature"])

    def test_evaluated_preview_success_uses_auto_sync_route_and_clears_dirty(self) -> None:
        pair_id = "pair-evaluated"
        obj = FakeObject("Evaluated")
        context = {"source_hint": "wrong"}
        controller._evaluated_preview_runtime.dirty_by_pair[pair_id] = {
            "dirtyClass": "content",
            "source": "modifier_signature_changed",
            "previewDueAt": 5.0,
            "autoEligible": True,
            "pendingModifierSignature": "sig-new",
        }
        with ExitStack() as stack:
            stack.enter_context(mock.patch.object(controller, "_get_sync_enabled", return_value=True))
            stack.enter_context(mock.patch.object(controller, "_get_current_mode", return_value="OBJECT"))
            stack.enter_context(mock.patch.object(controller, "_object_for_pair_id", return_value=obj))
            stack.enter_context(mock.patch.object(controller, "_should_auto_rebuild_evaluated_object", return_value=True))
            stack.enter_context(mock.patch.object(controller, "_object_has_visible_modifiers", return_value=True))
            stack.enter_context(mock.patch.object(controller, "_build_mesh_update_context_for_object", return_value=context))
            send_mock = stack.enter_context(mock.patch.object(controller, "send_mesh_update_once", return_value={"ok": True, "reason": "sent"}))
            stack.enter_context(mock.patch.object(controller, "_store_mesh_content_baseline_from_context"))
            controller._pump_evaluated_mesh_rebuilds(now=10.0)

        self.assertEqual("auto_sync", send_mock.call_args.args[0]["source_hint"])
        self.assertEqual("sig-new", controller._evaluated_preview_runtime.modifier_signature_by_pair[pair_id])
        self.assertNotIn(pair_id, controller._evaluated_preview_runtime.dirty_by_pair)

    def test_evaluated_preview_failure_keeps_dirty_and_reschedules(self) -> None:
        pair_id = "pair-evaluated"
        obj = FakeObject("Evaluated")
        controller._evaluated_preview_runtime.dirty_by_pair[pair_id] = {
            "dirtyClass": "content",
            "source": "modifier_signature_changed",
            "previewDueAt": 5.0,
            "autoEligible": True,
        }
        with ExitStack() as stack:
            stack.enter_context(mock.patch.object(controller, "_get_sync_enabled", return_value=True))
            stack.enter_context(mock.patch.object(controller, "_get_current_mode", return_value="OBJECT"))
            stack.enter_context(mock.patch.object(controller, "_get_evaluated_mesh_preview_debounce_seconds", return_value=0.4))
            stack.enter_context(mock.patch.object(controller, "_object_for_pair_id", return_value=obj))
            stack.enter_context(mock.patch.object(controller, "_should_auto_rebuild_evaluated_object", return_value=True))
            stack.enter_context(mock.patch.object(controller, "_object_has_visible_modifiers", return_value=True))
            stack.enter_context(mock.patch.object(controller, "_build_mesh_update_context_for_object", return_value={}))
            stack.enter_context(mock.patch.object(controller, "send_mesh_update_once", return_value={"ok": False, "reason": "transport"}))
            controller._pump_evaluated_mesh_rebuilds(now=10.0)

        self.assertIn(pair_id, controller._evaluated_preview_runtime.dirty_by_pair)
        self.assertAlmostEqual(10.4, controller._evaluated_preview_runtime.dirty_by_pair[pair_id]["previewDueAt"])

    def test_lifecycle_retry_backoff_saturates_at_ten_seconds(self) -> None:
        entry = {"retryCount": 0, "nextRetryAt": 0.0, "lastError": None}
        expected_delays = [1.0, 2.0, 5.0, 10.0, 10.0]
        now = 100.0
        for attempt, expected_delay in enumerate(expected_delays, start=1):
            object_lifecycle.schedule_retry(entry, now, f"error-{attempt}")
            self.assertEqual(attempt, entry["retryCount"])
            self.assertAlmostEqual(now + expected_delay, entry["nextRetryAt"])
            self.assertEqual(f"error-{attempt}", entry["lastError"])
            now = entry["nextRetryAt"]

    def test_lifecycle_bootstrap_success_marks_object_managed_and_clears_dirty(self) -> None:
        instance_id = "object-1"
        pair_id = f"pair-{instance_id}"
        obj = FakeObject("Bootstrap", ready=False)
        controller._geometry_preview_runtime.dirty_by_pair[pair_id] = {"geometry": True}
        controller._evaluated_preview_runtime.dirty_by_pair[pair_id] = {"dirtyClass": "content"}
        controller._lifecycle_runtime.new_reason_by_id[instance_id] = "depsgraph_new"
        send_result = SimpleNamespace(ok=True, error=None, message="sent")

        with ExitStack() as stack:
            stack.enter_context(mock.patch.object(object_lifecycle, "get_session", return_value=object()))
            build_mock = stack.enter_context(
                mock.patch.object(object_lifecycle, "build_single_object_live_context", return_value={"selected": []})
            )
            stack.enter_context(mock.patch.object(object_lifecycle, "send_selected_resources", return_value=send_result))
            stack.enter_context(mock.patch.object(material_sync, "collect_refs_for_object", return_value=("mat-1",)))
            stack.enter_context(mock.patch.object(structure_watch, "color_attributes_signature", return_value="color-sig"))
            stack.enter_context(mock.patch.object(controller, "capture_object_baselines"))
            stack.enter_context(mock.patch.object(controller, "_store_mesh_content_baseline_from_live_context"))
            stack.enter_context(mock.patch.object(controller, "modifier_stack_signature", return_value="modifier-sig"))
            stack.enter_context(mock.patch.object(controller, "uv_channels_signature", return_value="uv-sig"))
            stack.enter_context(mock.patch.object(object_state, "cache_runtime_signatures"))
            result = object_lifecycle.process_bootstrap(
                controller._lifecycle_runtime,
                controller._lifecycle_hooks(),
                instance_id,
                obj,
                now=10.0,
            )

        self.assertTrue(result)
        self.assertTrue(obj[controller.AUTO_SYNC_READY_KEY])
        self.assertEqual(
            controller.LIFECYCLE_PHASE_ACTIVE,
            controller._lifecycle_runtime.entries[instance_id]["phase"],
        )
        self.assertIn(instance_id, controller._lifecycle_runtime.managed_ids)
        self.assertNotIn(pair_id, controller._geometry_preview_runtime.dirty_by_pair)
        self.assertNotIn(pair_id, controller._evaluated_preview_runtime.dirty_by_pair)
        self.assertNotIn(instance_id, controller._lifecycle_runtime.new_reason_by_id)
        self.assertIn("pkg-object-auto-bootstrap-10000", build_mock.call_args.kwargs["package_id"])

    def test_lifecycle_bootstrap_success_writes_complete_cross_domain_baseline_set(self) -> None:
        pair_id = "pair-bootstrap"
        obj = FakeObject("Bootstrap")
        controller._geometry_preview_runtime.dirty_by_pair[pair_id] = {"geometry": True}
        controller._evaluated_preview_runtime.dirty_by_pair[pair_id] = {"dirtyClass": "content"}

        with ExitStack() as stack:
            stack.enter_context(mock.patch.object(material_sync, "collect_refs_for_object", return_value=("mat-1", "mat-2")))
            stack.enter_context(mock.patch.object(structure_watch, "color_attributes_signature", return_value="color-sig"))
            capture_mock = stack.enter_context(mock.patch.object(controller, "capture_object_baselines"))
            mesh_baseline_mock = stack.enter_context(
                mock.patch.object(controller, "_store_mesh_content_baseline_from_live_context")
            )
            stack.enter_context(mock.patch.object(controller, "modifier_stack_signature", return_value="modifier-sig"))
            stack.enter_context(mock.patch.object(controller, "uv_channels_signature", return_value="uv-sig"))
            stack.enter_context(mock.patch.object(controller, "_pair_id_for_object_readonly", return_value=pair_id))
            stack.enter_context(mock.patch.object(object_state, "object_state_signature", return_value="object-sig"))
            stack.enter_context(
                mock.patch.object(object_state, "object_non_transform_signature", return_value="non-transform-sig")
            )
            controller._on_lifecycle_bootstrap_success(obj, {"selected": []}, pair_id)

        self.assertNotIn(pair_id, controller._geometry_preview_runtime.dirty_by_pair)
        self.assertNotIn(pair_id, controller._evaluated_preview_runtime.dirty_by_pair)
        self.assertEqual(("mat-1", "mat-2"), controller._material_runtime.refs_by_pair[pair_id])
        self.assertEqual("modifier-sig", controller._evaluated_preview_runtime.modifier_signature_by_pair[pair_id])
        self.assertEqual("uv-sig", controller._mesh_structure_runtime.uv_channels_signature_by_pair[pair_id])
        self.assertEqual("color-sig", controller._mesh_structure_runtime.color_attributes_signature_by_pair[pair_id])
        self.assertEqual("object-sig", controller._object_state_runtime.signatures_by_pair[pair_id])
        self.assertEqual(
            "non-transform-sig",
            controller._object_state_runtime.non_transform_signatures_by_pair[pair_id],
        )
        capture_mock.assert_called_once_with(
            obj,
            material_refs=("mat-1", "mat-2"),
            color_attributes_signature="color-sig",
            reason="lifecycle_bootstrap",
        )
        mesh_baseline_mock.assert_called_once_with(obj, {"selected": []}, reason="lifecycle_bootstrap")

    def test_lifecycle_missing_object_retries_only_after_backoff(self) -> None:
        instance_id = "missing"
        hooks = controller._lifecycle_hooks()
        self.assertFalse(object_lifecycle.process_bootstrap(controller._lifecycle_runtime, hooks, instance_id, None, now=10.0))
        entry = controller._lifecycle_runtime.entries[instance_id]
        self.assertEqual(1, entry["retryCount"])
        self.assertAlmostEqual(11.0, entry["nextRetryAt"])

        self.assertFalse(object_lifecycle.process_bootstrap(controller._lifecycle_runtime, hooks, instance_id, None, now=10.5))
        self.assertEqual(1, entry["retryCount"])

        self.assertFalse(object_lifecycle.process_bootstrap(controller._lifecycle_runtime, hooks, instance_id, None, now=11.0))
        self.assertEqual(2, entry["retryCount"])
        self.assertAlmostEqual(13.0, entry["nextRetryAt"])

    def test_lifecycle_never_managed_remove_skips_send_and_marks_removed(self) -> None:
        instance_id = "never-managed"
        controller._lifecycle_runtime.delete_reason_by_id[instance_id] = "depsgraph_deleted"
        with ExitStack() as stack:
            send_mock = stack.enter_context(mock.patch.object(object_lifecycle, "send_object_remove_once"))
            clear_mock = stack.enter_context(mock.patch.object(controller, "_clear_removed_pair_runtime_state"))
            result = object_lifecycle.process_remove(
                controller._lifecycle_runtime,
                controller._lifecycle_hooks(),
                instance_id,
                now=10.0,
            )

        self.assertFalse(result)
        send_mock.assert_not_called()
        clear_mock.assert_called_once_with(f"pair-{instance_id}", reason="lifecycle_remove_skip")
        self.assertEqual(
            controller.LIFECYCLE_PHASE_REMOVED,
            controller._lifecycle_runtime.entries[instance_id]["phase"],
        )
        self.assertNotIn(instance_id, controller._lifecycle_runtime.delete_reason_by_id)

    def test_lifecycle_remove_failure_retries_then_successfully_removes(self) -> None:
        instance_id = "managed"
        controller._lifecycle_runtime.managed_ids.add(instance_id)
        controller._lifecycle_runtime.entries[instance_id] = {
            "phase": controller.LIFECYCLE_PHASE_ACTIVE,
            "retryCount": 0,
            "nextRetryAt": 0.0,
            "lastError": None,
        }
        controller._lifecycle_runtime.delete_reason_by_id[instance_id] = "depsgraph_deleted"

        with ExitStack() as stack:
            stack.enter_context(mock.patch.object(object_lifecycle, "get_session", return_value=object()))
            send_mock = stack.enter_context(
                mock.patch.object(
                    object_lifecycle,
                    "send_object_remove_once",
                    side_effect=[{"ok": False, "reason": "transport"}, {"ok": True}],
                )
            )
            clear_mock = stack.enter_context(mock.patch.object(controller, "_clear_removed_pair_runtime_state"))
            hooks = controller._lifecycle_hooks()
            self.assertFalse(object_lifecycle.process_remove(controller._lifecycle_runtime, hooks, instance_id, now=10.0))
            self.assertFalse(object_lifecycle.process_remove(controller._lifecycle_runtime, hooks, instance_id, now=10.5))
            self.assertTrue(object_lifecycle.process_remove(controller._lifecycle_runtime, hooks, instance_id, now=11.0))

        self.assertEqual(2, send_mock.call_count)
        clear_mock.assert_called_once_with(f"pair-{instance_id}", reason="lifecycle_remove_ok")
        self.assertEqual(
            controller.LIFECYCLE_PHASE_REMOVED,
            controller._lifecycle_runtime.entries[instance_id]["phase"],
        )
        self.assertNotIn(instance_id, controller._lifecycle_runtime.managed_ids)
        self.assertNotIn(instance_id, controller._lifecycle_runtime.delete_reason_by_id)

    def test_auto_sync_state_send_is_signature_deduplicated(self) -> None:
        sent = []

        class FakeSession:
            def send_auto(self, payload):
                sent.append(dict(payload))
                return SimpleNamespace(ok=True, error=None)

        with ExitStack() as stack:
            stack.enter_context(mock.patch.object(controller, "get_session", return_value=FakeSession()))
            idle_mock = stack.enter_context(
                mock.patch.object(controller, "_get_preview_idle_commit_seconds", return_value=0.35)
            )
            controller._emit_auto_sync_state_if_changed(True)
            controller._emit_auto_sync_state_if_changed(True)
            idle_mock.return_value = 0.5
            controller._emit_auto_sync_state_if_changed(True)

        self.assertEqual(2, len(sent))
        self.assertEqual([0.35, 0.5], [payload["previewIdleCommitSeconds"] for payload in sent])
        self.assertTrue(all(payload["enabled"] for payload in sent))


if __name__ == "__main__":
    unittest.main()
