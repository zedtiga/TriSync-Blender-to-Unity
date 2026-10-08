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
from blender.scene_sync import object_state
from blender.scene_sync import preview_sync


class FakeMeshObject(dict):
    def __init__(self) -> None:
        super().__init__()
        self.name = "Cube"
        self.type = "MESH"
        self.data = object()
        self[controller.AUTO_SYNC_READY_KEY] = True


class ControllerGeometryPreviewCharacterizationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.print_patch = mock.patch("builtins.print")
        self.print_patch.start()
        controller.reset_file_runtime_state(reason="geometry_preview_setup")
        self.obj = FakeMeshObject()
        self.pair_id = "pair-object"

    def tearDown(self) -> None:
        controller.reset_file_runtime_state(reason="geometry_preview_teardown")
        self.print_patch.stop()

    def preview_stack(self, dirty_state: dict | None) -> ExitStack:
        stack = ExitStack()
        fake_bpy = SimpleNamespace(context=SimpleNamespace(active_object=self.obj))
        stack.enter_context(mock.patch.object(controller, "bpy", fake_bpy))
        stack.enter_context(mock.patch.object(controller, "get_session", return_value=object()))
        stack.enter_context(mock.patch.object(controller, "ensure_instance_id", return_value="object"))
        stack.enter_context(
            mock.patch.object(
                preview_sync,
                "consume_geometry_dirty_event",
                return_value=dirty_state,
            )
        )
        stack.enter_context(mock.patch.object(controller, "_object_has_visible_modifiers", return_value=False))
        return stack

    def test_existing_inflight_preview_requeues_dirty_without_consuming(self) -> None:
        controller._geometry_preview_runtime.inflight_by_pair[self.pair_id] = True
        with self.preview_stack({"source": "unused"}) as stack:
            consume_mock = preview_sync.consume_geometry_dirty_event
            mark_mock = stack.enter_context(mock.patch.object(preview_sync, "mark_pair_dirty"))
            result = controller._send_active_mesh_full_preview_if_dirty()

        self.assertFalse(result)
        consume_mock.assert_not_called()
        mark_mock.assert_called_once()
        self.assertEqual(self.pair_id, mark_mock.call_args.args[2])
        self.assertEqual("full_preview_inflight_skip", mark_mock.call_args.kwargs["source"])

    def test_matching_baseline_skips_send_and_flushes_deferred_object_state(self) -> None:
        dirty = {"source": "depsgraph", "count": 2, "autoEligible": True}
        context = {"mesh_source": "original"}
        with self.preview_stack(dirty) as stack:
            stack.enter_context(
                mock.patch.object(controller, "_build_mesh_update_context_for_object", return_value=context)
            )
            stack.enter_context(
                mock.patch.object(controller, "_preview_matches_mesh_content_baseline", return_value=(True, "full"))
            )
            store_mock = stack.enter_context(
                mock.patch.object(controller, "_store_mesh_content_baseline_from_context")
            )
            deferred_mock = stack.enter_context(
                mock.patch.object(object_state, "send_deferred_after_mesh_preview")
            )
            send_mock = stack.enter_context(mock.patch.object(controller, "send_mesh_update_once"))
            result = controller._send_active_mesh_full_preview_if_dirty()

        self.assertFalse(result)
        send_mock.assert_not_called()
        store_mock.assert_called_once_with(self.obj, context, reason="auto_preview_no_mesh_change")
        self.assertEqual(self.obj, deferred_mock.call_args.args[2])
        self.assertEqual(self.pair_id, deferred_mock.call_args.args[3])
        self.assertEqual("after_mesh_preview:depsgraph", deferred_mock.call_args.kwargs["source"])
        self.assertNotIn(self.pair_id, controller._geometry_preview_runtime.inflight_by_pair)

    def test_successful_preview_updates_baseline_weights_and_deferred_state(self) -> None:
        dirty = {"source": "geometry", "count": 1, "autoEligible": True}
        context = {"mesh_source": "original"}
        with self.preview_stack(dirty) as stack:
            stack.enter_context(
                mock.patch.object(controller, "_build_mesh_update_context_for_object", return_value=context)
            )
            stack.enter_context(
                mock.patch.object(controller, "_preview_matches_mesh_content_baseline", return_value=(False, "full"))
            )
            stack.enter_context(
                mock.patch.object(controller, "send_mesh_update_once", return_value={"ok": True, "reason": "sent"})
            )
            store_mock = stack.enter_context(
                mock.patch.object(controller, "_store_mesh_content_baseline_from_context")
            )
            weights_mock = stack.enter_context(
                mock.patch.object(preview_sync, "send_shape_key_weights_if_changed")
            )
            deferred_mock = stack.enter_context(
                mock.patch.object(object_state, "send_deferred_after_mesh_preview")
            )
            result = controller._send_active_mesh_full_preview_if_dirty()

        self.assertTrue(result)
        store_mock.assert_called_once_with(self.obj, context, reason="auto_preview_sent")
        self.assertEqual(self.obj, weights_mock.call_args.args[2])
        self.assertEqual(
            {
                "force": True,
                "reason": "auto_preview_after_mesh_update:geometry",
            },
            weights_mock.call_args.kwargs,
        )
        self.assertEqual(self.obj, deferred_mock.call_args.args[2])
        self.assertEqual(self.pair_id, deferred_mock.call_args.args[3])
        self.assertEqual("after_mesh_preview:geometry", deferred_mock.call_args.kwargs["source"])
        self.assertNotIn(self.pair_id, controller._geometry_preview_runtime.inflight_by_pair)

    def test_no_mesh_change_result_flushes_deferred_state_without_retry(self) -> None:
        dirty = {"source": "geometry", "count": 1, "autoEligible": True}
        with self.preview_stack(dirty) as stack:
            stack.enter_context(
                mock.patch.object(
                    controller,
                    "_build_mesh_update_context_for_object",
                    return_value={"mesh_source": "original"},
                )
            )
            stack.enter_context(
                mock.patch.object(controller, "_preview_matches_mesh_content_baseline", return_value=(False, "full"))
            )
            stack.enter_context(
                mock.patch.object(
                    controller,
                    "send_mesh_update_once",
                    return_value={"ok": False, "reason": "no_mesh_change"},
                )
            )
            deferred_mock = stack.enter_context(
                mock.patch.object(object_state, "send_deferred_after_mesh_preview")
            )
            mark_mock = stack.enter_context(mock.patch.object(preview_sync, "mark_pair_dirty"))
            result = controller._send_active_mesh_full_preview_if_dirty()

        self.assertFalse(result)
        deferred_mock.assert_called_once()
        mark_mock.assert_not_called()
        self.assertNotIn(self.pair_id, controller._geometry_preview_runtime.inflight_by_pair)

    def test_failed_preview_requeues_geometry_and_clears_inflight(self) -> None:
        dirty = {"source": "geometry", "count": 1, "autoEligible": True}
        with self.preview_stack(dirty) as stack:
            stack.enter_context(
                mock.patch.object(
                    controller,
                    "_build_mesh_update_context_for_object",
                    return_value={"mesh_source": "original"},
                )
            )
            stack.enter_context(
                mock.patch.object(controller, "_preview_matches_mesh_content_baseline", return_value=(False, "full"))
            )
            stack.enter_context(
                mock.patch.object(
                    controller,
                    "send_mesh_update_once",
                    return_value={"ok": False, "reason": "transport"},
                )
            )
            mark_mock = stack.enter_context(mock.patch.object(preview_sync, "mark_pair_dirty"))
            result = controller._send_active_mesh_full_preview_if_dirty()

        self.assertFalse(result)
        self.assertEqual("full_preview_retry:geometry", mark_mock.call_args.kwargs["source"])
        self.assertNotIn(self.pair_id, controller._geometry_preview_runtime.inflight_by_pair)

    def test_dirty_during_send_is_requeued_after_current_preview(self) -> None:
        dirty = {"source": "geometry", "count": 1, "autoEligible": True}

        def send_with_new_dirty(_context):
            controller._geometry_preview_runtime.dirty_during_inflight_by_pair[self.pair_id] = True
            return {"ok": True, "reason": "sent"}

        with self.preview_stack(dirty) as stack:
            stack.enter_context(
                mock.patch.object(
                    controller,
                    "_build_mesh_update_context_for_object",
                    return_value={"mesh_source": "original"},
                )
            )
            stack.enter_context(
                mock.patch.object(controller, "_preview_matches_mesh_content_baseline", return_value=(False, "full"))
            )
            stack.enter_context(mock.patch.object(controller, "send_mesh_update_once", side_effect=send_with_new_dirty))
            mark_mock = stack.enter_context(mock.patch.object(preview_sync, "mark_pair_dirty"))
            stack.enter_context(mock.patch.object(controller, "_store_mesh_content_baseline_from_context"))
            stack.enter_context(mock.patch.object(preview_sync, "send_shape_key_weights_if_changed"))
            stack.enter_context(mock.patch.object(object_state, "send_deferred_after_mesh_preview"))
            result = controller._send_active_mesh_full_preview_if_dirty()

        self.assertTrue(result)
        self.assertEqual("during_full_preview", mark_mock.call_args.kwargs["source"])
        self.assertNotIn(self.pair_id, controller._geometry_preview_runtime.dirty_during_inflight_by_pair)
        self.assertNotIn(self.pair_id, controller._geometry_preview_runtime.inflight_by_pair)

    def test_preview_exception_requeues_geometry_and_clears_inflight(self) -> None:
        dirty = {"source": "geometry", "count": 1, "autoEligible": True}
        with self.preview_stack(dirty) as stack:
            stack.enter_context(
                mock.patch.object(
                    controller,
                    "_build_mesh_update_context_for_object",
                    return_value={"mesh_source": "original"},
                )
            )
            stack.enter_context(
                mock.patch.object(controller, "_preview_matches_mesh_content_baseline", return_value=(False, "full"))
            )
            stack.enter_context(
                mock.patch.object(controller, "send_mesh_update_once", side_effect=RuntimeError("send failed"))
            )
            mark_mock = stack.enter_context(mock.patch.object(preview_sync, "mark_pair_dirty"))
            result = controller._send_active_mesh_full_preview_if_dirty()

        self.assertFalse(result)
        self.assertEqual("full_preview_exception:geometry", mark_mock.call_args.kwargs["source"])
        self.assertNotIn(self.pair_id, controller._geometry_preview_runtime.inflight_by_pair)


if __name__ == "__main__":
    unittest.main()
