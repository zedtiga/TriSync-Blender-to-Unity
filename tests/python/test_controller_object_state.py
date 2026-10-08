from __future__ import annotations

import sys
import unittest
from contextlib import ExitStack
from pathlib import Path
from unittest import mock


ADDON_ROOT = Path(__file__).resolve().parents[2] / "blender_addon" / "blendersync_vnext"
sys.path.insert(0, str(ADDON_ROOT))

from blender.scene_sync import controller
from blender.scene_sync import object_state


class FakeObject(dict):
    def __init__(self, name: str = "Cube", *, object_type: str = "MESH", ready: bool = True) -> None:
        super().__init__()
        self.name = name
        self.type = object_type
        if ready:
            self[controller.AUTO_SYNC_READY_KEY] = True


class ControllerObjectStateCharacterizationTests(unittest.TestCase):
    def _hooks(self):
        return controller._object_state_hooks()

    def setUp(self) -> None:
        self.print_patch = mock.patch("builtins.print")
        self.print_patch.start()
        controller.reset_file_runtime_state(reason="object_state_setup")

    def tearDown(self) -> None:
        controller.reset_file_runtime_state(reason="object_state_teardown")
        self.print_patch.stop()

    def test_mark_dirty_requires_sync_supported_type_ready_and_pair_id(self) -> None:
        obj = FakeObject()
        with mock.patch.object(controller, "_get_sync_enabled", return_value=False):
            object_state.mark_dirty(controller._object_state_runtime, self._hooks(), obj, now=1.0)
        self.assertEqual({}, controller._object_state_runtime.dirty_queue)

        with mock.patch.object(controller, "_get_sync_enabled", return_value=True):
            object_state.mark_dirty(controller._object_state_runtime, self._hooks(), FakeObject(object_type="CURVE"), now=2.0)
            object_state.mark_dirty(controller._object_state_runtime, self._hooks(), FakeObject(ready=False), now=3.0)
        self.assertEqual({}, controller._object_state_runtime.dirty_queue)

        with ExitStack() as stack:
            stack.enter_context(mock.patch.object(controller, "_get_sync_enabled", return_value=True))
            stack.enter_context(mock.patch.object(controller, "_pair_id_for_object_readonly", return_value=None))
            object_state.mark_dirty(controller._object_state_runtime, self._hooks(), obj, now=4.0)
        self.assertEqual({}, controller._object_state_runtime.dirty_queue)

    def test_mark_dirty_merges_latest_source_and_extends_motion_burst(self) -> None:
        pair_id = "pair-object"
        obj = FakeObject("Moving")
        with ExitStack() as stack:
            stack.enter_context(mock.patch.object(controller, "_get_sync_enabled", return_value=True))
            stack.enter_context(mock.patch.object(controller, "_pair_id_for_object_readonly", return_value=pair_id))
            object_state.mark_dirty(controller._object_state_runtime, self._hooks(), obj, source="first", now=10.0)
            object_state.mark_dirty(controller._object_state_runtime, self._hooks(), obj, source="second", now=10.2)

        state = controller._object_state_runtime.dirty_queue[pair_id]
        self.assertEqual("Moving", state["objectName"])
        self.assertEqual("second", state["source"])
        self.assertAlmostEqual(10.2, state["lastDirtyAt"])
        self.assertEqual(2, state["count"])
        self.assertAlmostEqual(
            10.2 + controller.OBJECT_STATE_MOTION_BURST_SECONDS,
            controller._object_state_runtime.motion_burst_until_by_pair[pair_id],
        )

    def test_runtime_state_non_transform_change_uses_full_object_state_channel(self) -> None:
        obj = FakeObject()
        with ExitStack() as stack:
            stack.enter_context(mock.patch.object(object_state, "non_transform_state_changed", return_value=True))
            state_send = stack.enter_context(
                mock.patch.object(object_state, "send_object_state_update_for_object", return_value=True)
            )
            transform_send = stack.enter_context(mock.patch.object(object_state, "sync_active_transform_once"))
            result = object_state.send_runtime_state_for_object(controller._object_state_runtime, self._hooks(), obj, source="test")

        self.assertTrue(result)
        state_send.assert_called_once_with(controller._object_state_runtime, self._hooks(), obj, source="test")
        transform_send.assert_not_called()

    def test_runtime_state_transform_only_change_uses_transform_channel(self) -> None:
        obj = FakeObject()
        context = {"pair_id": "pair-object", "object_name": "Cube"}
        with ExitStack() as stack:
            stack.enter_context(mock.patch.object(object_state, "non_transform_state_changed", return_value=False))
            stack.enter_context(mock.patch.object(object_state, "state_changed", return_value=True))
            stack.enter_context(
                mock.patch.object(object_state, "build_object_transform_context", return_value=context)
            )
            transform_send = stack.enter_context(
                mock.patch.object(object_state, "sync_active_transform_once", return_value={"ok": True, "reason": "sent"})
            )
            state_send = stack.enter_context(mock.patch.object(object_state, "send_object_state_update_for_object"))
            result = object_state.send_runtime_state_for_object(controller._object_state_runtime, self._hooks(), obj, source="test")

        self.assertTrue(result)
        transform_send.assert_called_once_with(context)
        state_send.assert_not_called()

    def test_runtime_state_unchanged_sends_nothing(self) -> None:
        obj = FakeObject()
        with ExitStack() as stack:
            stack.enter_context(mock.patch.object(object_state, "non_transform_state_changed", return_value=False))
            stack.enter_context(mock.patch.object(object_state, "state_changed", return_value=False))
            transform_send = stack.enter_context(mock.patch.object(object_state, "sync_active_transform_once"))
            state_send = stack.enter_context(mock.patch.object(object_state, "send_object_state_update_for_object"))
            result = object_state.send_runtime_state_for_object(controller._object_state_runtime, self._hooks(), obj, source="test")

        self.assertFalse(result)
        transform_send.assert_not_called()
        state_send.assert_not_called()

    def test_dirty_queue_defers_while_geometry_preview_is_pending(self) -> None:
        pair_id = "pair-object"
        obj = FakeObject()
        controller._object_state_runtime.dirty_queue[pair_id] = {"source": "depsgraph", "count": 1}
        with ExitStack() as stack:
            stack.enter_context(mock.patch.object(controller, "_object_for_pair_id", return_value=obj))
            stack.enter_context(mock.patch.object(controller, "_pair_has_pending_geometry_dirty", return_value=True))
            state_send = stack.enter_context(mock.patch.object(object_state, "send_object_state_update_for_object"))
            transform_send = stack.enter_context(mock.patch.object(object_state, "sync_active_transform_once"))
            sent = object_state.pump_dirty_queue(controller._object_state_runtime, self._hooks(), now=10.0)

        self.assertEqual(0, sent)
        self.assertIn(pair_id, controller._object_state_runtime.dirty_queue)
        state_send.assert_not_called()
        transform_send.assert_not_called()

    def test_dirty_queue_full_state_success_clears_queue_and_motion_burst(self) -> None:
        pair_id = "pair-object"
        obj = FakeObject()
        controller._object_state_runtime.dirty_queue[pair_id] = {"source": "visibility", "count": 2}
        controller._object_state_runtime.motion_burst_until_by_pair[pair_id] = 20.0
        with ExitStack() as stack:
            stack.enter_context(mock.patch.object(controller, "_object_for_pair_id", return_value=obj))
            stack.enter_context(mock.patch.object(controller, "_pair_has_pending_geometry_dirty", return_value=False))
            stack.enter_context(mock.patch.object(object_state, "non_transform_state_changed", return_value=True))
            state_send = stack.enter_context(
                mock.patch.object(object_state, "send_object_state_update_for_object", return_value=True)
            )
            sent = object_state.pump_dirty_queue(controller._object_state_runtime, self._hooks(), now=10.0)

        self.assertEqual(1, sent)
        self.assertEqual(obj, state_send.call_args.args[2])
        self.assertEqual("visibility", state_send.call_args.kwargs["source"])
        self.assertNotIn(pair_id, controller._object_state_runtime.dirty_queue)
        self.assertNotIn(pair_id, controller._object_state_runtime.motion_burst_until_by_pair)

    def test_dirty_queue_transform_failure_keeps_dirty_for_retry(self) -> None:
        pair_id = "pair-object"
        obj = FakeObject()
        context = {"pair_id": pair_id, "object_name": "Cube"}
        controller._object_state_runtime.dirty_queue[pair_id] = {"source": "transform", "count": 1}
        with ExitStack() as stack:
            stack.enter_context(mock.patch.object(controller, "_object_for_pair_id", return_value=obj))
            stack.enter_context(mock.patch.object(controller, "_pair_has_pending_geometry_dirty", return_value=False))
            stack.enter_context(mock.patch.object(object_state, "non_transform_state_changed", return_value=False))
            stack.enter_context(mock.patch.object(object_state, "state_changed", return_value=True))
            stack.enter_context(
                mock.patch.object(object_state, "build_object_transform_context", return_value=context)
            )
            stack.enter_context(
                mock.patch.object(object_state, "sync_active_transform_once", return_value={"ok": False, "reason": "transport"})
            )
            sent = object_state.pump_dirty_queue(controller._object_state_runtime, self._hooks(), now=10.0)

        self.assertEqual(0, sent)
        self.assertIn(pair_id, controller._object_state_runtime.dirty_queue)
        self.assertNotIn(pair_id, controller._object_state_runtime.motion_burst_until_by_pair)

    def test_mesh_preview_success_flushes_deferred_object_state(self) -> None:
        pair_id = "pair-object"
        obj = FakeObject()
        controller._object_state_runtime.dirty_queue[pair_id] = {"source": "depsgraph", "count": 3}
        controller._object_state_runtime.motion_burst_until_by_pair[pair_id] = 20.0
        with mock.patch.object(
            object_state,
            "send_object_state_update_for_object",
            return_value=True,
        ) as send_mock:
            result = object_state.send_deferred_after_mesh_preview(
                controller._object_state_runtime,
                self._hooks(),
                obj,
                pair_id,
                source="after_preview",
            )

        self.assertTrue(result)
        self.assertEqual(obj, send_mock.call_args.args[2])
        self.assertEqual("after_preview", send_mock.call_args.kwargs["source"])
        self.assertNotIn(pair_id, controller._object_state_runtime.dirty_queue)
        self.assertNotIn(pair_id, controller._object_state_runtime.motion_burst_until_by_pair)

    def test_motion_burst_defers_geometry_then_samples_and_extends_on_send(self) -> None:
        pair_id = "pair-object"
        obj = FakeObject("Burst")
        controller._object_state_runtime.motion_burst_until_by_pair[pair_id] = 10.35
        with ExitStack() as stack:
            stack.enter_context(mock.patch.object(controller, "_object_for_pair_id", return_value=obj))
            geometry_mock = stack.enter_context(
                mock.patch.object(controller, "_pair_has_pending_geometry_dirty", side_effect=[True, False])
            )
            send_mock = stack.enter_context(
                mock.patch.object(object_state, "send_runtime_state_for_object", return_value=True)
            )
            self.assertEqual(0, object_state.pump_motion_bursts(controller._object_state_runtime, self._hooks(), now=10.0))
            self.assertEqual(1, object_state.pump_motion_bursts(controller._object_state_runtime, self._hooks(), now=10.2))

        self.assertEqual(2, geometry_mock.call_count)
        self.assertEqual(obj, send_mock.call_args.args[2])
        self.assertEqual("motion_burst", send_mock.call_args.kwargs["source"])
        self.assertAlmostEqual(
            10.2 + controller.OBJECT_STATE_MOTION_BURST_SECONDS,
            controller._object_state_runtime.motion_burst_until_by_pair[pair_id],
        )

    def test_motion_burst_expiry_removes_pair_without_sampling(self) -> None:
        pair_id = "pair-object"
        controller._object_state_runtime.motion_burst_until_by_pair[pair_id] = 10.0
        with mock.patch.object(object_state, "send_runtime_state_for_object") as send_mock:
            sampled = object_state.pump_motion_bursts(controller._object_state_runtime, self._hooks(), now=10.1)

        self.assertEqual(0, sampled)
        self.assertNotIn(pair_id, controller._object_state_runtime.motion_burst_until_by_pair)
        send_mock.assert_not_called()

    def test_non_transform_poll_preserves_batch_limit_and_rotating_cursor(self) -> None:
        current = {
            f"object-{index:02d}": FakeObject(f"Object {index:02d}")
            for index in range(33)
        }
        controller._object_state_runtime.non_transform_poll_cursor = 1
        with ExitStack() as stack:
            changed_mock = stack.enter_context(
                mock.patch.object(object_state, "non_transform_state_changed", return_value=False)
            )
            send_mock = stack.enter_context(
                mock.patch.object(object_state, "send_object_state_update_for_object")
            )
            sent = object_state.poll_scene_non_transform(
                controller._object_state_runtime,
                self._hooks(),
                current,
                now=10.0,
            )

        self.assertEqual(0, sent)
        self.assertEqual(32, changed_mock.call_count)
        self.assertIs(current["object-01"], changed_mock.call_args_list[0].args[2])
        self.assertEqual(0, controller._object_state_runtime.non_transform_poll_cursor)
        send_mock.assert_not_called()


if __name__ == "__main__":
    unittest.main()
