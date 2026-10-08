from __future__ import annotations

import inspect
import sys
import unittest
from contextlib import ExitStack
from pathlib import Path
from types import SimpleNamespace
from unittest import mock


ADDON_ROOT = Path(__file__).resolve().parents[2] / "blender_addon" / "blendersync_vnext"
sys.path.insert(0, str(ADDON_ROOT))

from blender.scene_sync import controller


class FakeMeshObject(dict):
    def __init__(self, name: str = "Cube", *, modifiers: bool = True) -> None:
        super().__init__()
        self.name = name
        self.type = "MESH"
        self.data = SimpleNamespace(shape_keys=None)
        self.modifiers = [SimpleNamespace(show_viewport=True)] if modifiers else []
        self[controller.AUTO_SYNC_READY_KEY] = True


class ControllerEvaluatedPreviewCharacterizationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.print_patch = mock.patch("builtins.print")
        self.print_patch.start()
        controller.reset_file_runtime_state(reason="evaluated_preview_setup")
        self.obj = FakeMeshObject()
        self.pair_id = "pair-object"

    def tearDown(self) -> None:
        controller.reset_file_runtime_state(reason="evaluated_preview_teardown")
        self.print_patch.stop()

    def test_modifier_classification_uses_persistent_baseline_then_advances_cache(self) -> None:
        with ExitStack() as stack:
            stack.enter_context(mock.patch.object(controller, "modifier_stack_signature", return_value="sig-new"))
            stack.enter_context(mock.patch.object(controller, "get_modifier_stack_baseline", return_value="sig-old"))
            result = controller._classify_object_update_for_evaluated(self.obj, self.pair_id)

        self.assertEqual(("content", "modifier_signature_changed", "sig-new", "sig-old"), result)
        self.assertEqual("sig-new", controller._evaluated_preview_runtime.modifier_signature_by_pair[self.pair_id])

    def test_transform_flag_does_not_hide_modifier_signature_change(self) -> None:
        with ExitStack() as stack:
            stack.enter_context(
                mock.patch.object(
                    controller,
                    "_object_state_changed_since_runtime_baseline",
                    return_value=False,
                )
            )
            stack.enter_context(mock.patch.object(controller, "modifier_stack_signature", return_value="sig-new"))
            stack.enter_context(mock.patch.object(controller, "get_modifier_stack_baseline", return_value="sig-old"))
            result = controller._classify_transform_update_for_evaluated(self.obj, self.pair_id)

        self.assertEqual(("content", "modifier_signature_changed", "sig-new", "sig-old"), result)
        self.assertEqual("sig-new", controller._evaluated_preview_runtime.modifier_signature_by_pair[self.pair_id])

    def test_object_motion_rebases_incidental_modifier_signature_drift_as_noise(self) -> None:
        controller._evaluated_preview_runtime.modifier_signature_by_pair[self.pair_id] = "sig-before-move"
        with (
            mock.patch.object(
                controller,
                "_object_state_changed_since_runtime_baseline",
                return_value=True,
            ),
            mock.patch.object(controller, "modifier_stack_signature", return_value="sig-during-move"),
        ):
            result = controller._classify_transform_update_for_evaluated(self.obj, self.pair_id)

        self.assertEqual(
            ("context_noise", "object_transform_update_noise", "sig-during-move", "sig-during-move"),
            result,
        )
        self.assertEqual(
            "sig-during-move",
            controller._evaluated_preview_runtime.modifier_signature_by_pair[self.pair_id],
        )

    def test_transform_update_with_stable_modifier_signature_remains_noise(self) -> None:
        controller._evaluated_preview_runtime.modifier_signature_by_pair[self.pair_id] = "sig-stable"
        with mock.patch.object(controller, "modifier_stack_signature", return_value="sig-stable"):
            result = controller._classify_transform_update_for_evaluated(self.obj, self.pair_id)

        self.assertEqual(
            ("context_noise", "object_transform_update_noise", "sig-stable", "sig-stable"),
            result,
        )

    def test_sync_enable_captures_live_signature_instead_of_stale_import_baseline(self) -> None:
        fake_bpy = SimpleNamespace(
            context=SimpleNamespace(
                scene=SimpleNamespace(objects=[self.obj]),
            )
        )
        with (
            mock.patch.object(controller, "bpy", fake_bpy),
            mock.patch.object(controller, "_pair_id_for_object_readonly", return_value=self.pair_id),
            mock.patch.object(controller, "get_modifier_stack_baseline", return_value="sig-import"),
            mock.patch.object(controller, "modifier_stack_signature", return_value="sig-live"),
            mock.patch.object(controller.structure_watch, "shape_key_structure_signature", return_value="shape-sig"),
        ):
            controller._capture_evaluated_modifier_signature_baseline(reason="sync_enabled")

        self.assertEqual(
            "sig-live",
            controller._evaluated_preview_runtime.modifier_signature_by_pair[self.pair_id],
        )

    def test_removed_last_modifier_still_requires_one_original_mesh_rebuild(self) -> None:
        obj = FakeMeshObject(modifiers=False)
        controller._evaluated_preview_runtime.had_visible_modifiers_by_pair[self.pair_id] = True

        with ExitStack() as stack:
            stack.enter_context(mock.patch.object(controller, "_pair_id_for_object_readonly", return_value=self.pair_id))
            self.assertTrue(controller._should_auto_rebuild_evaluated_object(obj))
            self.assertEqual(
                ("original", "modifier_stack_cleared_revert_original"),
                controller._resolve_auto_preview_mesh_source(obj, "evaluated"),
            )

    def test_shape_keys_exclude_mesh_from_evaluated_rebuild(self) -> None:
        self.obj.data.shape_keys = object()
        with ExitStack() as stack:
            stack.enter_context(mock.patch.object(controller, "_mesh_has_shape_keys", return_value=True))
            self.assertFalse(controller._should_auto_rebuild_evaluated_object(self.obj))

    def test_consume_ignores_noise_due_time_and_ineligible_content(self) -> None:
        controller._evaluated_preview_runtime.dirty_by_pair[self.pair_id] = {
            "dirtyClass": "context_noise",
            "previewDueAt": 1.0,
            "autoEligible": True,
        }
        self.assertIsNone(controller._consume_evaluated_mesh_dirty_event(self.pair_id, respect_due=False))

        controller._evaluated_preview_runtime.dirty_by_pair[self.pair_id] = {
            "dirtyClass": "content",
            "previewDueAt": 20.0,
            "autoEligible": False,
        }
        with mock.patch.object(controller.time, "time", return_value=10.0):
            self.assertIsNone(controller._consume_evaluated_mesh_dirty_event(self.pair_id))
            self.assertIsNone(
                controller._consume_evaluated_mesh_dirty_event(
                    self.pair_id,
                    respect_due=False,
                    require_auto_eligible=True,
                )
            )
            consumed = controller._consume_evaluated_mesh_dirty_event(self.pair_id, respect_due=False)

        self.assertEqual("content", consumed["dirtyClass"])
        self.assertNotIn(self.pair_id, controller._evaluated_preview_runtime.dirty_by_pair)

    def test_context_noise_is_discarded_without_building_or_sending_preview(self) -> None:
        controller._evaluated_preview_runtime.dirty_by_pair[self.pair_id] = {
            "dirtyClass": "context_noise",
            "source": "selection_noise",
            "previewDueAt": 1.0,
            "autoEligible": True,
        }
        controller._evaluated_preview_runtime.last_preview_sent_at_by_pair[self.pair_id] = 0.5

        with self._pump_stack(self.obj) as stack:
            build_mock = stack.enter_context(mock.patch.object(controller, "_build_mesh_update_context_for_object"))
            send_mock = stack.enter_context(mock.patch.object(controller, "send_mesh_update_once"))
            controller._pump_evaluated_mesh_rebuilds(now=10.0)

        build_mock.assert_not_called()
        send_mock.assert_not_called()
        self.assertNotIn(self.pair_id, controller._evaluated_preview_runtime.dirty_by_pair)
        self.assertNotIn(self.pair_id, controller._evaluated_preview_runtime.last_preview_sent_at_by_pair)

    def test_no_mesh_change_completes_last_modifier_revert_and_clears_marker(self) -> None:
        obj = FakeMeshObject(modifiers=False)
        controller._evaluated_preview_runtime.had_visible_modifiers_by_pair[self.pair_id] = True
        controller._evaluated_preview_runtime.dirty_by_pair[self.pair_id] = {
            "dirtyClass": "content",
            "source": "modifier_signature_changed",
            "previewDueAt": 5.0,
            "autoEligible": True,
            "pendingModifierSignature": "sig-empty",
        }

        with self._pump_stack(obj) as stack:
            stack.enter_context(mock.patch.object(controller, "_should_auto_rebuild_evaluated_object", return_value=True))
            stack.enter_context(mock.patch.object(controller, "_object_has_visible_modifiers", return_value=False))
            build_mock = stack.enter_context(
                mock.patch.object(controller, "_build_mesh_update_context_for_object", return_value={})
            )
            stack.enter_context(
                mock.patch.object(
                    controller,
                    "send_mesh_update_once",
                    return_value={"ok": False, "reason": "no_mesh_change"},
                )
            )
            baseline_mock = stack.enter_context(
                mock.patch.object(controller, "_store_mesh_content_baseline_from_context")
            )
            controller._pump_evaluated_mesh_rebuilds(now=10.0)

        self.assertEqual("evaluated", build_mock.call_args.kwargs["mesh_source_override"])
        baseline_mock.assert_not_called()
        self.assertEqual("sig-empty", controller._evaluated_preview_runtime.modifier_signature_by_pair[self.pair_id])
        self.assertNotIn(self.pair_id, controller._evaluated_preview_runtime.had_visible_modifiers_by_pair)
        self.assertNotIn(self.pair_id, controller._evaluated_preview_runtime.dirty_by_pair)

    def test_empty_evaluated_geometry_is_consumed_without_send_or_retry(self) -> None:
        controller._evaluated_preview_runtime.dirty_by_pair[self.pair_id] = {
            "dirtyClass": "content",
            "source": "geometry_nodes_changed",
            "previewDueAt": 5.0,
            "autoEligible": True,
            "count": 1,
        }

        with self._pump_stack(self.obj) as stack:
            build_mock = stack.enter_context(
                mock.patch.object(
                    controller,
                    "_build_mesh_update_context_for_object",
                    side_effect=ValueError("mesh_has_no_exportable_geometry"),
                )
            )
            send_mock = stack.enter_context(mock.patch.object(controller, "send_mesh_update_once"))
            controller._pump_evaluated_mesh_rebuilds(now=10.0)

        build_mock.assert_called_once()
        send_mock.assert_not_called()
        self.assertNotIn(self.pair_id, controller._evaluated_preview_runtime.dirty_by_pair)

    def test_enabled_background_pump_has_no_idle_evaluated_mesh_poll(self) -> None:
        source = inspect.getsource(controller._run_enabled_background_pumps)
        self.assertNotIn("poll_active_evaluated_modifier_signature", source)
        self.assertNotIn("poll_active_evaluated_mesh_content", source)
        self.assertNotIn("_build_mesh_update_context_for_object", source)

    def _pump_stack(self, obj: FakeMeshObject) -> ExitStack:
        stack = ExitStack()
        stack.enter_context(mock.patch.object(controller, "_get_sync_enabled", return_value=True))
        stack.enter_context(mock.patch.object(controller, "_get_current_mode", return_value="OBJECT"))
        stack.enter_context(mock.patch.object(controller, "_object_for_pair_id", return_value=obj))
        return stack


if __name__ == "__main__":
    unittest.main()
