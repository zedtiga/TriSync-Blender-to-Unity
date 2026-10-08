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
from blender.scene_sync import mesh_context
from blender.scene_sync import structure_watch


class FakeMeshObject(dict):
    def __init__(self, *, shape_keys: bool = False) -> None:
        super().__init__()
        self.name = "Cube"
        self.type = "MESH"
        key_blocks = []
        if shape_keys:
            key_blocks = [
                SimpleNamespace(name="Basis", value=0.0),
                SimpleNamespace(name="Smile", value=0.25),
            ]
        self.data = SimpleNamespace(
            shape_keys=SimpleNamespace(key_blocks=key_blocks) if shape_keys else None,
        )
        self.modifiers = []
        self[controller.AUTO_SYNC_READY_KEY] = True


class ControllerMeshFeatureCharacterizationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.print_patch = mock.patch("builtins.print")
        self.print_patch.start()
        controller.reset_file_runtime_state(reason="mesh_feature_setup")
        self.pair_id = "pair-object"

    def tearDown(self) -> None:
        controller.reset_file_runtime_state(reason="mesh_feature_teardown")
        self.print_patch.stop()

    def test_active_object_baseline_reports_exported_color_attribute_name(self) -> None:
        obj = FakeMeshObject()
        obj.data.uv_layers = []
        obj.data.color_attributes = [SimpleNamespace(name="Color")]
        fake_bpy = SimpleNamespace(context=SimpleNamespace(active_object=obj))

        with (
            mock.patch.object(controller, "bpy", fake_bpy),
            mock.patch.object(
                mesh_context,
                "collect_mesh_color_attribute_values_for_preview",
                return_value={"name": "Color"},
            ) as collect_mock,
        ):
            state = controller._active_object_baseline_state()

        collect_mock.assert_called_once_with(obj.data)
        self.assertEqual("Color", state["colorAttributeExportName"])

    def test_runtime_only_controller_state_skips_active_object_baseline_collection(self) -> None:
        with mock.patch.object(controller, "_active_object_baseline_state") as baseline_mock:
            state = controller.get_controller_state(include_active_object_baseline=False)

        baseline_mock.assert_not_called()
        self.assertNotIn("active_object_baseline", state)

    def test_shape_key_weight_send_converts_to_unity_percent_and_updates_cache(self) -> None:
        obj = FakeMeshObject(shape_keys=True)
        session = SimpleNamespace(send_auto=mock.Mock(return_value=SimpleNamespace(ok=True)))

        with ExitStack() as stack:
            stack.enter_context(mock.patch.object(controller, "get_session", return_value=session))
            stack.enter_context(mock.patch.object(controller, "ensure_instance_id", return_value="object"))
            stack.enter_context(mock.patch.object(controller, "ensure_mesh_asset_id_for_object", return_value="mesh-id"))
            stack.enter_context(mock.patch.object(controller.time, "time", return_value=10.0))
            result = controller._send_shape_key_weights_if_changed(obj, reason="test")

        self.assertTrue(result)
        payload = session.send_auto.call_args.args[0]
        self.assertEqual("scene_sync.blendshape_weights_v1", payload["type"])
        self.assertEqual("mesh-mesh-id", payload["meshRef"])
        self.assertEqual(
            [{"name": "Smile", "index": 1, "value": 0.25, "weight": 25.0}],
            payload["weights"],
        )
        self.assertEqual((("Smile", 0.25),), controller._shape_key_runtime.last_weights_by_pair[self.pair_id])
        self.assertAlmostEqual(
            10.0 + controller.BLENDSHAPE_WEIGHT_SYNC_INTERVAL_SECONDS,
            controller._shape_key_runtime.next_weight_send_time_by_pair[self.pair_id],
        )

    def test_shape_key_weight_send_skips_unchanged_and_throttled_values(self) -> None:
        obj = FakeMeshObject(shape_keys=True)
        session = SimpleNamespace(send_auto=mock.Mock(return_value=SimpleNamespace(ok=True)))
        controller._shape_key_runtime.last_weights_by_pair[self.pair_id] = (("Smile", 0.25),)

        with self._shape_weight_stack(session):
            self.assertFalse(controller._send_shape_key_weights_if_changed(obj))

        controller._shape_key_runtime.last_weights_by_pair[self.pair_id] = (("Smile", 0.1),)
        controller._shape_key_runtime.next_weight_send_time_by_pair[self.pair_id] = 11.0
        with self._shape_weight_stack(session), mock.patch.object(controller.time, "time", return_value=10.0):
            self.assertFalse(controller._send_shape_key_weights_if_changed(obj))

        session.send_auto.assert_not_called()

    def test_failed_shape_key_weight_send_does_not_advance_cache_or_throttle(self) -> None:
        obj = FakeMeshObject(shape_keys=True)
        session = SimpleNamespace(send_auto=mock.Mock(return_value=SimpleNamespace(ok=False)))

        with self._shape_weight_stack(session), mock.patch.object(controller.time, "time", return_value=10.0):
            self.assertFalse(controller._send_shape_key_weights_if_changed(obj))

        self.assertNotIn(self.pair_id, controller._shape_key_runtime.last_weights_by_pair)
        self.assertNotIn(self.pair_id, controller._shape_key_runtime.next_weight_send_time_by_pair)

    def test_update_selected_with_auto_sync_disabled_sends_weights_without_mesh_preview(self) -> None:
        obj = FakeMeshObject(shape_keys=True)
        fake_bpy = SimpleNamespace(
            context=SimpleNamespace(
                mode="OBJECT",
                object=obj,
                selected_objects=[obj],
                scene=SimpleNamespace(blendersync_auto_sync_enabled=False),
            )
        )

        with ExitStack() as stack:
            stack.enter_context(mock.patch.object(controller, "bpy", fake_bpy))
            stack.enter_context(mock.patch.object(controller, "_get_current_mode", return_value="OBJECT"))
            stack.enter_context(mock.patch.object(controller, "summarize_unsupported", return_value=[]))
            stack.enter_context(mock.patch.object(controller, "collect_supported_sync_objects", return_value=[obj]))
            stack.enter_context(mock.patch.object(controller, "collect_meshes_managed_by_armatures", return_value=set()))
            stack.enter_context(mock.patch.object(controller, "object_identity_key", return_value="mesh-object"))
            stack.enter_context(
                mock.patch.object(
                    controller,
                    "build_object_state_payload",
                    return_value={"objects": [{"pairId": self.pair_id}]},
                )
            )
            stack.enter_context(mock.patch.object(controller, "get_session", return_value=object()))
            stack.enter_context(
                mock.patch.object(
                    controller,
                    "send_object_state_update",
                    return_value=SimpleNamespace(ok=True),
                )
            )
            stack.enter_context(mock.patch.object(controller.object_state, "cache_runtime_signatures"))
            stack.enter_context(mock.patch.object(controller.material_sync, "send_slot_mesh_update", return_value=False))
            stack.enter_context(mock.patch.object(controller, "_pair_id_for_object_readonly", return_value=self.pair_id))
            stack.enter_context(mock.patch.object(controller, "_consume_pair_geometry_dirty_event", return_value=None))
            stack.enter_context(mock.patch.object(controller, "_consume_evaluated_mesh_dirty_event", return_value=None))
            weights_mock = stack.enter_context(
                mock.patch.object(controller, "_send_shape_key_weights_if_changed", return_value=True)
            )
            build_mesh_mock = stack.enter_context(
                mock.patch.object(controller, "_build_mesh_update_context_for_object")
            )
            send_mesh_mock = stack.enter_context(mock.patch.object(controller, "send_mesh_update_once"))

            result = controller.sync_objects_once([obj])

        self.assertTrue(result["ok"])
        self.assertEqual(1, result["objectStateSynced"])
        self.assertEqual(0, result["meshSynced"])
        weights_mock.assert_called_once_with(
            obj,
            force=True,
            reason="manual_update_selected_weights_only",
        )
        build_mesh_mock.assert_not_called()
        send_mesh_mock.assert_not_called()

    def test_uv_channel_poll_marks_mesh_structure_dirty_after_signature_change(self) -> None:
        obj = FakeMeshObject()
        fake_bpy = SimpleNamespace(context=SimpleNamespace(active_object=obj))

        with self._poll_stack(fake_bpy, obj) as stack:
            stack.enter_context(mock.patch.object(structure_watch, "uv_channels_signature", return_value="uv-new"))
            stack.enter_context(mock.patch.object(structure_watch, "get_uv_channels_baseline", return_value="uv-old"))
            mark_mock = stack.enter_context(mock.patch.object(controller, "_mark_evaluated_mesh_dirty"))
            structure_watch.poll_active_uv_channels_signature(controller._mesh_structure_runtime, controller._structure_watch_hooks(), now=10.0)

        self.assertEqual("uv-new", controller._mesh_structure_runtime.uv_channels_signature_by_pair[self.pair_id])
        self.assertAlmostEqual(
            10.0 + controller.UV_CHANNELS_SIGNATURE_POLL_SECONDS,
            controller._mesh_structure_runtime.uv_channels_poll_next_time_by_pair[self.pair_id],
        )
        mark_mock.assert_called_once_with(
            obj,
            source="uv_channels_signature_changed",
            dirty_class="content",
            now=10.0,
        )

    def test_color_attribute_poll_skips_shape_key_meshes(self) -> None:
        obj = FakeMeshObject(shape_keys=True)
        fake_bpy = SimpleNamespace(context=SimpleNamespace(active_object=obj))

        with self._poll_stack(fake_bpy, obj) as stack:
            stack.enter_context(mock.patch.object(controller, "_mesh_has_shape_keys", return_value=True))
            signature_mock = stack.enter_context(mock.patch.object(structure_watch, "color_attributes_signature"))
            mark_mock = stack.enter_context(mock.patch.object(controller, "_mark_evaluated_mesh_dirty"))
            structure_watch.poll_active_color_attributes_signature(controller._mesh_structure_runtime, controller._structure_watch_hooks(), now=10.0)

        signature_mock.assert_not_called()
        mark_mock.assert_not_called()
        self.assertNotIn(self.pair_id, controller._mesh_structure_runtime.color_attributes_poll_next_time_by_pair)

    def test_color_attribute_poll_marks_dirty_after_signature_change(self) -> None:
        obj = FakeMeshObject()
        fake_bpy = SimpleNamespace(context=SimpleNamespace(active_object=obj))

        with self._poll_stack(fake_bpy, obj) as stack:
            stack.enter_context(mock.patch.object(controller, "_mesh_has_shape_keys", return_value=False))
            stack.enter_context(mock.patch.object(structure_watch, "color_attributes_signature", return_value="color-new"))
            stack.enter_context(mock.patch.object(structure_watch, "get_color_attributes_baseline", return_value="color-old"))
            mark_mock = stack.enter_context(mock.patch.object(controller, "_mark_evaluated_mesh_dirty"))
            structure_watch.poll_active_color_attributes_signature(controller._mesh_structure_runtime, controller._structure_watch_hooks(), now=10.0)

        self.assertEqual("color-new", controller._mesh_structure_runtime.color_attributes_signature_by_pair[self.pair_id])
        mark_mock.assert_called_once_with(
            obj,
            source="color_attributes_signature_changed",
            dirty_class="content",
            now=10.0,
        )

    def test_shape_key_structure_poll_initializes_then_forces_full_mesh_send_on_change(self) -> None:
        obj = FakeMeshObject(shape_keys=True)
        fake_bpy = SimpleNamespace(context=SimpleNamespace(active_object=obj))
        signatures = iter(("shape-old", "shape-new"))

        with self._poll_stack(fake_bpy, obj) as stack:
            stack.enter_context(mock.patch.object(structure_watch, "shape_key_structure_signature", side_effect=signatures))
            mark_mock = stack.enter_context(mock.patch.object(controller, "_mark_pair_dirty"))
            structure_watch.poll_active_shape_key_structure_signature(controller._mesh_structure_runtime, controller._structure_watch_hooks(), now=10.0)
            structure_watch.poll_active_shape_key_structure_signature(
                controller._mesh_structure_runtime,
                controller._structure_watch_hooks(),
                now=10.0 + controller.SHAPE_KEY_STRUCTURE_POLL_SECONDS,
            )

        self.assertEqual("shape-new", controller._mesh_structure_runtime.shape_key_structure_signature_by_pair[self.pair_id])
        mark_mock.assert_called_once_with(
            self.pair_id,
            channel="geometry",
            source="shape_key_structure_signature_changed",
            now=10.0 + controller.SHAPE_KEY_STRUCTURE_POLL_SECONDS,
            force_mesh_send=True,
        )

    def test_uv_preview_inflight_guard_and_finally_cleanup_are_stable(self) -> None:
        obj = FakeMeshObject()
        controller._uv_preview_runtime.inflight_by_pair[self.pair_id] = True
        with self._uv_preview_stack(obj) as stack:
            build_mock = stack.enter_context(mock.patch.object(controller, "_build_mesh_update_context_for_object"))
            result = controller._send_mesh_preview_with_live_uv(obj, reason="test")

        self.assertEqual("uv_preview_inflight", result["reason"])
        build_mock.assert_not_called()

        controller._uv_preview_runtime.inflight_by_pair.clear()
        with self._uv_preview_stack(obj) as stack:
            stack.enter_context(mock.patch.object(controller, "_build_mesh_update_context_for_object", return_value=None))
            result = controller._send_mesh_preview_with_live_uv(obj, reason="test")

        self.assertEqual("mesh_context_missing", result["reason"])
        self.assertNotIn(self.pair_id, controller._uv_preview_runtime.inflight_by_pair)

    def test_successful_uv_preview_records_the_sent_material_references(self) -> None:
        obj = FakeMeshObject()
        context = {
            "mesh_content": {
                "vertices": [0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0, 0.0],
                "uv": [0.0, 0.0, 1.0, 0.0, 0.0, 1.0],
            },
            "material_refs": ["mat-gn"],
        }

        with self._uv_preview_stack(obj) as stack:
            stack.enter_context(
                mock.patch.object(controller, "_build_mesh_update_context_for_object", return_value=context)
            )
            stack.enter_context(
                mock.patch.object(controller, "_send_mesh_update_with_materials", return_value={"ok": True})
            )
            record_mock = stack.enter_context(mock.patch.object(controller, "_record_sent_material_refs"))
            result = controller._send_mesh_preview_with_live_uv(obj, reason="test")

        self.assertTrue(result["ok"])
        record_mock.assert_called_once_with(obj, context, reason="test")

    def _shape_weight_stack(self, session) -> ExitStack:
        stack = ExitStack()
        stack.enter_context(mock.patch.object(controller, "get_session", return_value=session))
        stack.enter_context(mock.patch.object(controller, "ensure_instance_id", return_value="object"))
        stack.enter_context(mock.patch.object(controller, "ensure_mesh_asset_id_for_object", return_value="mesh-id"))
        return stack

    def _poll_stack(self, fake_bpy, obj) -> ExitStack:
        stack = ExitStack()
        stack.enter_context(mock.patch.object(structure_watch, "bpy", fake_bpy))
        stack.enter_context(mock.patch.object(controller, "_get_sync_enabled", return_value=True))
        stack.enter_context(mock.patch.object(controller, "_get_current_mode", return_value="OBJECT"))
        stack.enter_context(mock.patch.object(controller, "_pair_id_for_object_readonly", return_value=self.pair_id))
        return stack

    def _uv_preview_stack(self, obj) -> ExitStack:
        stack = ExitStack()
        stack.enter_context(mock.patch.object(controller, "get_session", return_value=object()))
        stack.enter_context(mock.patch.object(controller, "ensure_instance_id", return_value="object"))
        return stack


if __name__ == "__main__":
    unittest.main()
