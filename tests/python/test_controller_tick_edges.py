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
from blender.scene_sync import structure_watch


class FakeMeshObject(dict):
    def __init__(self) -> None:
        super().__init__()
        self.name = "Cube"
        self.type = "MESH"
        self.data = object()
        self[controller.AUTO_SYNC_READY_KEY] = True


class ControllerTickEdgeCharacterizationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.print_patch = mock.patch("builtins.print")
        self.print_patch.start()
        controller.reset_file_runtime_state(reason="tick_edge_setup")
        self.obj = FakeMeshObject()

    def tearDown(self) -> None:
        controller.reset_file_runtime_state(reason="tick_edge_teardown")
        self.print_patch.stop()

    def test_sync_enable_edge_runs_reset_baseline_and_publish_in_order(self) -> None:
        controller._last_mode = "OBJECT"
        controller._last_sync_enabled = False
        calls: list[str] = []

        with self._tick_stack(mode="OBJECT", sync_enabled=True) as stack:
            stack.enter_context(
                mock.patch.object(
                    controller,
                    "_reset_auto_sync_transient_dirty",
                    side_effect=lambda reason: calls.append("transient_" + reason),
                )
            )
            stack.enter_context(
                mock.patch.object(
                    controller,
                    "_reset_evaluated_runtime_state",
                    side_effect=lambda reason: calls.append("evaluated_" + reason),
                )
            )
            stack.enter_context(
                mock.patch.object(
                    controller,
                    "_capture_evaluated_modifier_signature_baseline",
                    side_effect=lambda reason: calls.append("capture_" + reason),
                )
            )
            stack.enter_context(
                mock.patch.object(controller, "_init_lifecycle_baseline", side_effect=lambda: calls.append("lifecycle_init"))
            )
            stack.enter_context(
                mock.patch.object(
                    controller,
                    "_emit_auto_sync_state_if_changed",
                    side_effect=lambda enabled: calls.append(f"emit_{enabled}"),
                )
            )
            controller._tick_impl()

        self.assertEqual(
            [
                "transient_sync_enabled",
                "evaluated_sync_enabled",
                "capture_sync_enabled",
                "lifecycle_init",
                "emit_True",
                "emit_True",
            ],
            calls,
        )
        self.assertTrue(controller._last_sync_enabled)

    def test_sync_disable_edge_runs_all_resets_and_publishes_disabled(self) -> None:
        controller._last_mode = "OBJECT"
        controller._last_sync_enabled = True
        calls: list[str] = []

        with self._tick_stack(mode="OBJECT", sync_enabled=False) as stack:
            stack.enter_context(
                mock.patch.object(
                    controller,
                    "_reset_auto_sync_transient_dirty",
                    side_effect=lambda reason: calls.append("transient_" + reason),
                )
            )
            stack.enter_context(
                mock.patch.object(controller, "_reset_lifecycle_runtime_state", side_effect=lambda: calls.append("lifecycle_reset"))
            )
            stack.enter_context(
                mock.patch.object(
                    controller,
                    "_reset_evaluated_runtime_state",
                    side_effect=lambda reason: calls.append("evaluated_" + reason),
                )
            )
            stack.enter_context(
                mock.patch.object(
                    controller,
                    "_emit_auto_sync_state_if_changed",
                    side_effect=lambda enabled: calls.append(f"emit_{enabled}"),
                )
            )
            controller._tick_impl()

        self.assertEqual(
            [
                "transient_sync_disabled",
                "lifecycle_reset",
                "evaluated_sync_disabled",
                "emit_False",
            ],
            calls,
        )
        self.assertFalse(controller._last_sync_enabled)

    def test_mode_enter_clears_preview_cache_once(self) -> None:
        controller._last_mode = "OBJECT"
        controller._last_sync_enabled = True

        with self._tick_stack(mode="EDIT_MESH", sync_enabled=True) as stack:
            clear_mock = stack.enter_context(mock.patch.object(controller, "_clear_preview_fast_cache_for_object"))
            controller._tick_impl()

        clear_mock.assert_called_once_with(self.obj, reason="mode_enter")
        self.assertEqual("EDIT_MESH", controller._last_mode)

    def test_mode_exit_ordinary_mesh_sends_uv_preview_then_clears_cache(self) -> None:
        controller._last_mode = "EDIT_MESH"
        controller._last_sync_enabled = True
        calls: list[str] = []

        with self._tick_stack(mode="OBJECT", sync_enabled=True) as stack:
            stack.enter_context(mock.patch.object(controller, "_mesh_has_shape_keys", return_value=False))
            preview_mock = stack.enter_context(
                mock.patch.object(
                    controller,
                    "_send_mesh_preview_with_live_uv",
                    side_effect=lambda *_args, **kwargs: calls.append("preview_" + kwargs["reason"])
                    or {"ok": True, "reason": "sent"},
                )
            )
            clear_mock = stack.enter_context(
                mock.patch.object(
                    controller,
                    "_clear_preview_fast_cache_for_object",
                    side_effect=lambda *_args, **kwargs: calls.append("clear_" + kwargs["reason"]),
                )
            )
            weights_mock = stack.enter_context(mock.patch.object(controller, "_send_shape_key_weights_if_changed"))
            controller._tick_impl()

        self.assertEqual(["preview_mode_exit_uv_preview", "clear_mode_exit_commit"], calls)
        preview_mock.assert_called_once_with(self.obj, reason="mode_exit_uv_preview", require_uv=False)
        clear_mock.assert_called_once_with(self.obj, reason="mode_exit_commit")
        weights_mock.assert_not_called()
        self.assertEqual("OBJECT", controller._last_mode)

    def test_mode_exit_shape_key_mesh_sends_preview_and_forced_weights(self) -> None:
        controller._last_mode = "EDIT_MESH"
        controller._last_sync_enabled = True

        with self._tick_stack(mode="OBJECT", sync_enabled=True) as stack:
            stack.enter_context(mock.patch.object(controller, "_mesh_has_shape_keys", return_value=True))
            preview_mock = stack.enter_context(
                mock.patch.object(
                    controller,
                    "_send_mesh_preview_with_live_uv",
                    return_value={"ok": True, "reason": "sent"},
                )
            )
            weights_mock = stack.enter_context(
                mock.patch.object(controller, "_send_shape_key_weights_if_changed")
            )
            stack.enter_context(mock.patch.object(controller, "_clear_preview_fast_cache_for_object"))
            controller._tick_impl()

        preview_mock.assert_called_once_with(
            self.obj,
            reason="mode_exit_shape_key_preview",
            require_uv=False,
        )
        weights_mock.assert_any_call(
            self.obj,
            force=True,
            reason="mode_exit_shape_key_preview_after_mesh_update",
        )

    def test_mode_exit_preview_exception_is_not_replayed_on_next_tick(self) -> None:
        controller._last_mode = "EDIT_MESH"
        controller._last_sync_enabled = True

        with self._tick_stack(mode="OBJECT", sync_enabled=True) as stack:
            report_mock = stack.enter_context(
                mock.patch.object(controller, "report_boundary_exception")
            )
            stack.enter_context(mock.patch.object(controller, "_mesh_has_shape_keys", return_value=False))
            preview_mock = stack.enter_context(
                mock.patch.object(
                    controller,
                    "_send_mesh_preview_with_live_uv",
                    side_effect=RuntimeError("preview failed"),
                )
            )
            clear_mock = stack.enter_context(
                mock.patch.object(controller, "_clear_preview_fast_cache_for_object")
            )
            first_delay = controller._tick()
            second_delay = controller._tick()

        self.assertEqual(0.5, first_delay)
        self.assertEqual(0.05, second_delay)
        self.assertEqual(1, preview_mock.call_count)
        report_mock.assert_called_once()
        self.assertEqual("auto_sync_timer", report_mock.call_args.args[0])
        clear_mock.assert_called_once_with(self.obj, reason="mode_exit_commit")
        self.assertEqual("OBJECT", controller._last_mode)

    def _tick_stack(self, *, mode: str, sync_enabled: bool) -> ExitStack:
        stack = ExitStack()
        fake_bpy = SimpleNamespace(context=SimpleNamespace(active_object=self.obj))
        stack.enter_context(mock.patch.object(controller, "bpy", fake_bpy))
        stack.enter_context(mock.patch.object(controller.time, "time", return_value=10.0))
        stack.enter_context(mock.patch.object(controller, "_try_auto_sync_view_state"))
        stack.enter_context(mock.patch.object(controller, "_get_current_mode", return_value=mode))
        stack.enter_context(mock.patch.object(controller, "_get_sync_enabled", return_value=sync_enabled))
        stack.enter_context(mock.patch.object(controller, "_emit_auto_sync_state_if_changed"))
        stack.enter_context(mock.patch.object(controller, "ensure_instance_id", return_value="object"))
        stack.enter_context(mock.patch.object(controller, "_get_lifecycle_reconcile_hz", return_value=0.0))
        stack.enter_context(mock.patch.object(structure_watch, "poll_active_uv_channels_signature"))
        stack.enter_context(mock.patch.object(structure_watch, "poll_active_color_attributes_signature"))
        stack.enter_context(mock.patch.object(structure_watch, "poll_active_shape_key_structure_signature"))
        stack.enter_context(mock.patch.object(material_sync, "poll_active_slots_signature"))
        stack.enter_context(mock.patch.object(controller, "_pump_evaluated_mesh_rebuilds"))
        stack.enter_context(mock.patch.object(material_sync, "pump_pending_sends"))
        stack.enter_context(mock.patch.object(material_sync, "pump_ref_resends"))
        stack.enter_context(mock.patch.object(controller, "_should_tick", return_value=False))
        stack.enter_context(mock.patch.object(controller, "_get_mesh_sync_hz", return_value=0.0))
        return stack


if __name__ == "__main__":
    unittest.main()
