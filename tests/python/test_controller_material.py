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
from blender.scene_sync import material_sync


class ControllerMaterialCharacterizationTests(unittest.TestCase):
    def _hooks(self):
        return controller._material_sync_hooks()

    def setUp(self) -> None:
        self.print_patch = mock.patch("builtins.print")
        self.print_patch.start()
        controller.reset_file_runtime_state(reason="material_setup")

    def tearDown(self) -> None:
        controller.reset_file_runtime_state(reason="material_teardown")
        self.print_patch.stop()

    def test_unknown_material_queue_deduplicates_bundle_and_collects_waiting_pairs(self) -> None:
        first_bundle = {"deps": [], "materialContents": [{"name": "first"}]}
        second_bundle = {"deps": [{"type": "texture"}], "materialContents": [{"name": "second"}]}
        with ExitStack() as stack:
            stack.enter_context(mock.patch.object(material_sync, "is_material_ref_known", return_value=False))
            stack.enter_context(mock.patch.object(material_sync.time, "time", return_value=10.0))
            self.assertTrue(
                material_sync.queue_bundle_if_unknown(
                    controller._material_runtime,
                    "mat-test",
                    first_bundle,
                    reason="first",
                    pair_id="pair-a",
                )
            )
            self.assertTrue(
                material_sync.queue_bundle_if_unknown(
                    controller._material_runtime,
                    "mat-test",
                    second_bundle,
                    reason="second",
                    pair_id="pair-b",
                )
            )

        self.assertEqual({"pair-a", "pair-b"}, controller._material_runtime.waiting_pair_ids_by_ref["mat-test"])
        entry = controller._material_runtime.pending_bundles_by_ref["mat-test"]
        self.assertIs(second_bundle, entry["bundle"])
        self.assertEqual("second", entry["reason"])
        self.assertEqual(10.0, entry["queuedAt"])

    def test_known_material_is_not_queued(self) -> None:
        with mock.patch.object(material_sync, "is_material_ref_known", return_value=True):
            queued = material_sync.queue_bundle_if_unknown(
                controller._material_runtime,
                "mat-known",
                {"deps": [], "materialContents": [{"name": "known"}]},
                pair_id="pair-a",
            )

        self.assertFalse(queued)
        self.assertNotIn("mat-known", controller._material_runtime.pending_bundles_by_ref)
        self.assertNotIn("mat-known", controller._material_runtime.waiting_pair_ids_by_ref)

    def test_pending_material_that_became_known_clears_state_and_schedules_resend(self) -> None:
        controller._material_runtime.pending_bundles_by_ref["mat-known"] = {
            "bundle": {"deps": [], "materialContents": [{"name": "known"}]}
        }
        controller._material_runtime.waiting_pair_ids_by_ref["mat-known"] = {"pair-a", "pair-b"}
        controller._material_runtime.retry_after_by_ref["mat-known"] = 99.0
        with ExitStack() as stack:
            stack.enter_context(mock.patch.object(material_sync, "get_session", return_value=object()))
            stack.enter_context(mock.patch.object(material_sync, "is_material_ref_known", return_value=True))
            schedule_mock = stack.enter_context(mock.patch.object(material_sync, "schedule_ref_resends"))
            sent = material_sync.pump_pending_sends(controller._material_runtime, now=10.0)

        self.assertEqual(0, sent)
        self.assertNotIn("mat-known", controller._material_runtime.pending_bundles_by_ref)
        self.assertNotIn("mat-known", controller._material_runtime.waiting_pair_ids_by_ref)
        self.assertNotIn("mat-known", controller._material_runtime.retry_after_by_ref)
        schedule_mock.assert_called_once_with(controller._material_runtime, "mat-known", {"pair-a", "pair-b"}, 10.0)

    def test_empty_pending_bundle_clears_waiting_and_retry_state(self) -> None:
        controller._material_runtime.pending_bundles_by_ref["mat-empty"] = {
            "bundle": {"deps": [], "materialContents": []}
        }
        controller._material_runtime.waiting_pair_ids_by_ref["mat-empty"] = {"pair-a"}
        controller._material_runtime.retry_after_by_ref["mat-empty"] = 1.0
        with ExitStack() as stack:
            stack.enter_context(mock.patch.object(material_sync, "get_session", return_value=object()))
            stack.enter_context(mock.patch.object(material_sync, "is_material_ref_known", return_value=False))
            schedule_mock = stack.enter_context(mock.patch.object(material_sync, "schedule_ref_resends"))
            sent = material_sync.pump_pending_sends(controller._material_runtime, now=10.0)

        self.assertEqual(0, sent)
        self.assertNotIn("mat-empty", controller._material_runtime.pending_bundles_by_ref)
        self.assertNotIn("mat-empty", controller._material_runtime.waiting_pair_ids_by_ref)
        self.assertNotIn("mat-empty", controller._material_runtime.retry_after_by_ref)
        schedule_mock.assert_not_called()

    def test_material_send_success_marks_known_clears_pending_and_schedules_resend(self) -> None:
        controller._material_runtime.pending_bundles_by_ref["mat-new"] = {
            "bundle": {
                "deps": [{"type": "texture"}],
                "materialContents": [{"name": "new"}],
            }
        }
        controller._material_runtime.waiting_pair_ids_by_ref["mat-new"] = {"pair-a"}
        controller._material_runtime.retry_after_by_ref["mat-new"] = 1.0
        result = SimpleNamespace(ok=True, error=None)
        with ExitStack() as stack:
            stack.enter_context(mock.patch.object(material_sync, "get_session", return_value=object()))
            stack.enter_context(mock.patch.object(material_sync, "is_material_ref_known", return_value=False))
            send_mock = stack.enter_context(mock.patch.object(material_sync, "send_selected_resources", return_value=result))
            known_mock = stack.enter_context(mock.patch.object(material_sync, "mark_assets_known"))
            schedule_mock = stack.enter_context(mock.patch.object(material_sync, "schedule_ref_resends"))
            sent = material_sync.pump_pending_sends(controller._material_runtime, now=10.0)

        self.assertEqual(1, sent)
        context = send_mock.call_args.args[0]
        self.assertEqual("manual_resource_resend", context["sendMode"])
        self.assertEqual(2, context["resourceCountEstimate"])
        known_mock.assert_called_once_with(["mat-new"])
        schedule_mock.assert_called_once_with(controller._material_runtime, "mat-new", {"pair-a"}, 10.0)
        self.assertNotIn("mat-new", controller._material_runtime.pending_bundles_by_ref)
        self.assertNotIn("mat-new", controller._material_runtime.waiting_pair_ids_by_ref)
        self.assertNotIn("mat-new", controller._material_runtime.retry_after_by_ref)
        self.assertFalse(controller._material_runtime.send_inflight)

    def test_material_send_failure_sets_retry_gate_and_keeps_pending(self) -> None:
        controller._material_runtime.pending_bundles_by_ref["mat-retry"] = {
            "bundle": {"deps": [], "materialContents": [{"name": "retry"}]}
        }
        result = SimpleNamespace(ok=False, error="transport")
        with ExitStack() as stack:
            stack.enter_context(mock.patch.object(material_sync, "get_session", return_value=object()))
            stack.enter_context(mock.patch.object(material_sync, "is_material_ref_known", return_value=False))
            send_mock = stack.enter_context(mock.patch.object(material_sync, "send_selected_resources", return_value=result))
            self.assertEqual(0, material_sync.pump_pending_sends(controller._material_runtime, now=10.0))
            self.assertEqual(0, material_sync.pump_pending_sends(controller._material_runtime, now=11.0))

        self.assertEqual(1, send_mock.call_count)
        self.assertIn("mat-retry", controller._material_runtime.pending_bundles_by_ref)
        self.assertAlmostEqual(12.0, controller._material_runtime.retry_after_by_ref["mat-retry"])
        self.assertFalse(controller._material_runtime.send_inflight)

    def test_removed_last_waiter_keeps_bundle_until_reconnect_send_succeeds(self) -> None:
        material_ref = "mat-shared"
        removed_pair = "pair-removed"
        controller._material_runtime.pending_bundles_by_ref[material_ref] = {
            "bundle": {"deps": [], "materialContents": [{"name": "shared"}]}
        }
        controller._material_runtime.waiting_pair_ids_by_ref[material_ref] = {removed_pair}
        controller._material_runtime.retry_after_by_ref[material_ref] = 2.0

        remove_result = material_sync.remove_pair_from_runtime(
            controller._material_runtime,
            removed_pair,
            reason="test_disconnect_delete",
        )

        self.assertEqual(
            {"waitingRefs": 1, "preservedBundles": 1, "resendRefs": 0},
            remove_result,
        )
        self.assertNotIn(material_ref, controller._material_runtime.waiting_pair_ids_by_ref)
        self.assertIn(material_ref, controller._material_runtime.pending_bundles_by_ref)
        self.assertEqual(2.0, controller._material_runtime.retry_after_by_ref[material_ref])

        send_result = SimpleNamespace(ok=True, error=None)
        with ExitStack() as stack:
            stack.enter_context(mock.patch.object(material_sync, "get_session", return_value=object()))
            stack.enter_context(mock.patch.object(material_sync, "is_material_ref_known", return_value=False))
            send_mock = stack.enter_context(
                mock.patch.object(material_sync, "send_selected_resources", return_value=send_result)
            )
            stack.enter_context(mock.patch.object(material_sync, "mark_assets_known"))
            sent = material_sync.pump_pending_sends(controller._material_runtime, now=10.0)

        self.assertEqual(1, sent)
        send_mock.assert_called_once()
        self.assertNotIn(material_ref, controller._material_runtime.pending_bundles_by_ref)
        self.assertNotIn(material_ref, controller._material_runtime.retry_after_by_ref)
        self.assertNotIn(material_ref, controller._material_runtime.pending_resends_by_ref)

    def test_material_ref_resend_runs_three_delayed_attempts_then_clears(self) -> None:
        material_sync.schedule_ref_resends(controller._material_runtime, "mat-test", {"pair-a"}, now=10.0)
        with mock.patch.object(material_sync, "force_resend_refs_for_pairs", return_value=1) as resend_mock:
            self.assertEqual(0, material_sync.pump_ref_resends(controller._material_runtime, self._hooks(), now=10.34))
            self.assertEqual(1, material_sync.pump_ref_resends(controller._material_runtime, self._hooks(), now=10.35))
            self.assertEqual(1, material_sync.pump_ref_resends(controller._material_runtime, self._hooks(), now=11.35))
            self.assertEqual(1, material_sync.pump_ref_resends(controller._material_runtime, self._hooks(), now=13.35))

        self.assertEqual(
            [
                "material_auto_send_ready_delayed_1",
                "material_auto_send_ready_delayed_2",
                "material_auto_send_ready_delayed_3",
            ],
            [call.kwargs["reason"] for call in resend_mock.call_args_list],
        )
        self.assertNotIn("mat-test", controller._material_runtime.pending_resends_by_ref)

    def test_force_resend_skips_missing_pairs_and_counts_successes(self) -> None:
        existing = object()

        def find_object(pair_id: str):
            return existing if pair_id == "pair-a" else None

        with ExitStack() as stack:
            stack.enter_context(mock.patch.object(controller, "_find_object_by_pair_id", side_effect=find_object))
            send_mock = stack.enter_context(
                mock.patch.object(material_sync, "send_slot_mesh_update", return_value=True)
            )
            resent = material_sync.force_resend_refs_for_pairs(
                self._hooks(),
                {"pair-a", "pair-missing"},
                "mat-test",
                reason="test",
            )

        self.assertEqual(1, resent)
        self.assertIs(existing, send_mock.call_args.args[2])
        self.assertTrue(send_mock.call_args.kwargs["force"])
        self.assertEqual("test", send_mock.call_args.kwargs["reason"])

    def test_slot_signature_poll_routes_change_through_mesh_update_hook(self) -> None:
        pair_id = "pair-material"
        obj = {controller.AUTO_SYNC_READY_KEY: True}
        obj = SimpleNamespace(
            type="MESH",
            name="Material Object",
            material_slots=[],
            get=obj.get,
        )
        controller._material_runtime.refs_by_pair[pair_id] = ("mat-old",)
        send_mock = mock.Mock(return_value=True)
        hooks = material_sync.MaterialSyncHooks(
            get_sync_enabled=lambda: True,
            get_current_mode=lambda: "OBJECT",
            pair_id_for_object=lambda _obj: pair_id,
            find_object_by_pair_id=lambda _pair_id: None,
            send_slot_mesh_update=send_mock,
            build_mesh_context=lambda *_args, **_kwargs: None,
            store_mesh_content_baseline=lambda *_args, **_kwargs: None,
            send_shape_key_weights=lambda *_args, **_kwargs: False,
        )
        fake_bpy = SimpleNamespace(context=SimpleNamespace(active_object=obj))

        with ExitStack() as stack:
            stack.enter_context(mock.patch.object(material_sync, "bpy", fake_bpy))
            stack.enter_context(mock.patch.object(material_sync, "collect_refs_for_object", return_value=("mat-new",)))
            stack.enter_context(mock.patch.object(material_sync, "get_material_slots_baseline", return_value="old-signature"))
            stack.enter_context(
                mock.patch.object(
                    material_sync,
                    "material_slots_signature_from_refs",
                    side_effect=lambda refs: "new-signature" if refs == ("mat-new",) else "old-signature",
                )
            )
            material_sync.poll_active_slots_signature(controller._material_runtime, hooks, now=10.0)

        self.assertAlmostEqual(10.25, controller._material_runtime.slots_poll_next_at_by_pair[pair_id])
        self.assertEqual(("mat-old",), controller._material_runtime.refs_by_pair[pair_id])
        send_mock.assert_called_once_with(obj, force=True, reason="material_slots_signature_changed")

    def test_slot_mesh_update_success_writes_all_material_and_mesh_baselines(self) -> None:
        pair_id = "pair-object"
        ready = {controller.AUTO_SYNC_READY_KEY: True}
        obj = SimpleNamespace(type="MESH", name="Mesh", material_slots=[], get=ready.get)
        context = {"mesh_content": {}}
        store_mesh_mock = mock.Mock()
        weights_mock = mock.Mock(return_value=True)
        hooks = material_sync.MaterialSyncHooks(
            get_sync_enabled=lambda: True,
            get_current_mode=lambda: "OBJECT",
            pair_id_for_object=lambda _obj: pair_id,
            find_object_by_pair_id=lambda _pair_id: obj,
            send_slot_mesh_update=lambda *_args, **_kwargs: False,
            build_mesh_context=lambda *_args, **_kwargs: context,
            store_mesh_content_baseline=store_mesh_mock,
            send_shape_key_weights=weights_mock,
        )

        with ExitStack() as stack:
            stack.enter_context(mock.patch.object(material_sync, "ensure_instance_id", return_value="object"))
            stack.enter_context(mock.patch.object(material_sync, "collect_refs_for_object", return_value=("mat-a",)))
            stack.enter_context(mock.patch.object(material_sync, "queue_unknown_contents_for_object", return_value=0))
            stack.enter_context(mock.patch.object(material_sync, "get_material_slots_baseline", return_value=None))
            baseline_mock = stack.enter_context(mock.patch.object(material_sync, "set_material_slots_baseline"))
            stack.enter_context(mock.patch.object(material_sync, "send_mesh_update_once", return_value={"ok": True}))
            result = material_sync.send_slot_mesh_update(
                controller._material_runtime,
                hooks,
                obj,
                force=True,
                reason="test",
            )

        self.assertTrue(result)
        self.assertEqual(("mat-a",), controller._material_runtime.refs_by_pair[pair_id])
        baseline_mock.assert_called_once_with(obj, ("mat-a",), reason="test")
        store_mesh_mock.assert_called_once_with(obj, context, reason="test")
        weights_mock.assert_called_once_with(obj, force=True, reason="test_after_mesh_update")


if __name__ == "__main__":
    unittest.main()
