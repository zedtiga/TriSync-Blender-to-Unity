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


class FakeMesh:
    pass


class FakeObject(dict):
    pass


class FakeData:
    def __init__(self, name: str) -> None:
        self.name = name


class GeometryNodeTree:
    def __init__(self, name: str) -> None:
        self.name = name


class ControllerDirtyProbeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.print_patch = mock.patch("builtins.print")
        self.print_patch.start()
        controller.reset_file_runtime_state(reason="dirty_probe_setup")

    def tearDown(self) -> None:
        controller.reset_file_runtime_state(reason="dirty_probe_teardown")
        self.print_patch.stop()

    def test_verbose_disabled_does_not_accumulate_unused_diagnostic_state(self) -> None:
        with self._probe_stack(verbose=False):
            controller._dirty_probe_depsgraph_update(None, self._depsgraph())

        self.assertEqual({}, controller._dirty_probe_counts_by_id)
        self.assertEqual({}, controller._dirty_probe_last_seen_by_id)
        self.assertEqual({}, controller._dirty_probe_last_log_by_id)

    def test_verbose_enabled_preserves_existing_count_and_log_throttle_state(self) -> None:
        with self._probe_stack(verbose=True):
            controller._dirty_probe_depsgraph_update(None, self._depsgraph())

        key = "FakeData:Node"
        self.assertEqual(1, controller._dirty_probe_counts_by_id[key])
        self.assertEqual(10.0, controller._dirty_probe_last_seen_by_id[key])
        self.assertEqual(10.0, controller._dirty_probe_last_log_by_id[key])

    def test_internal_evaluated_build_does_not_process_depsgraph_updates(self) -> None:
        with self._probe_stack(verbose=True) as stack:
            stack.enter_context(mock.patch.object(controller, "evaluated_mesh_build_in_progress", return_value=True))
            controller._dirty_probe_depsgraph_update(None, self._depsgraph())

        self.assertEqual({}, controller._dirty_probe_counts_by_id)

    def test_node_tree_update_is_noise_when_target_only_moves(self) -> None:
        class ReadyObject(FakeObject):
            def __init__(self) -> None:
                super().__init__()
                self.name = "Target"
                self.type = "MESH"
                self.data = FakeMesh()
                self.modifiers = []
                self[controller.AUTO_SYNC_READY_KEY] = True

        obj = ReadyObject()
        tree = GeometryNodeTree("GeometryNodes")
        updates = [
            SimpleNamespace(
                id=obj,
                is_updated_geometry=True,
                is_updated_transform=True,
                is_updated_shading=False,
            ),
            SimpleNamespace(
                id=tree,
                is_updated_geometry=False,
                # Blender can emit this companion NodeTree update without a
                # transform flag even though the Object update is a pure move.
                is_updated_transform=False,
                is_updated_shading=False,
            ),
        ]
        fake_bpy = SimpleNamespace(
            context=SimpleNamespace(active_object=obj),
            types=SimpleNamespace(Mesh=FakeMesh, Object=FakeObject),
        )
        with (
            mock.patch.object(controller, "bpy", fake_bpy),
            mock.patch.object(controller, "_get_current_mode", return_value="OBJECT"),
            mock.patch.object(controller, "_get_sync_enabled", return_value=True),
            mock.patch.object(controller, "_pair_id_for_object_readonly", return_value="pair-target"),
            mock.patch.object(controller, "_object_for_pair_id", return_value=obj),
            mock.patch.object(controller, "_should_auto_rebuild_evaluated_object", return_value=True),
            mock.patch.object(controller, "_object_has_visible_modifiers", return_value=True),
            mock.patch.object(controller, "get_modifier_stack_baseline", return_value="same"),
            mock.patch.object(controller, "modifier_stack_signature", return_value="same"),
            mock.patch.object(controller, "_classify_object_update_for_evaluated") as classify_mock,
            mock.patch.object(controller, "_mark_evaluated_mesh_dirty") as mark_mock,
            mock.patch.object(controller, "_mark_pair_dirty") as mark_pair_mock,
        ):
            controller._dirty_probe_depsgraph_update(None, SimpleNamespace(updates=updates))

        evaluated_calls = [call for call in mark_mock.call_args_list if call.args]
        self.assertEqual(2, len(evaluated_calls))
        self.assertTrue(all(call.kwargs["dirty_class"] == "context_noise" for call in evaluated_calls))
        classify_mock.assert_not_called()
        self.assertEqual("object_transform_update_noise", evaluated_calls[0].kwargs["source"])
        mark_pair_mock.assert_not_called()

    def test_non_transform_object_update_classifies_signature_once(self) -> None:
        obj = FakeObject()
        obj.name = "Target"
        obj.type = "MESH"
        obj.data = FakeMesh()
        obj.modifiers = []
        obj[controller.AUTO_SYNC_READY_KEY] = True
        update = SimpleNamespace(
            id=obj,
            is_updated_geometry=True,
            is_updated_transform=False,
            is_updated_shading=False,
        )
        fake_bpy = SimpleNamespace(
            context=SimpleNamespace(active_object=obj),
            types=SimpleNamespace(Mesh=FakeMesh, Object=FakeObject),
        )
        with (
            mock.patch.object(controller, "bpy", fake_bpy),
            mock.patch.object(controller, "_get_current_mode", return_value="OBJECT"),
            mock.patch.object(controller, "_pair_id_for_object_readonly", return_value="pair-target"),
            mock.patch.object(
                controller,
                "_classify_object_update_for_evaluated",
                return_value=("content", "modifier_signature_changed", "new", "old"),
            ) as classify_mock,
            mock.patch.object(controller.object_state, "mark_dirty"),
            mock.patch.object(controller, "_mark_pair_dirty"),
            mock.patch.object(controller, "_mark_evaluated_mesh_dirty"),
        ):
            controller._dirty_probe_depsgraph_update(None, SimpleNamespace(updates=[update]))

        classify_mock.assert_called_once_with(obj, "pair-target")

    def test_transform_flagged_modifier_change_marks_evaluated_content(self) -> None:
        obj = FakeObject()
        obj.name = "Target"
        obj.type = "MESH"
        obj.data = FakeMesh()
        obj.modifiers = [SimpleNamespace(show_viewport=True)]
        obj[controller.AUTO_SYNC_READY_KEY] = True
        update = SimpleNamespace(
            id=obj,
            is_updated_geometry=True,
            is_updated_transform=True,
            is_updated_shading=False,
        )
        fake_bpy = SimpleNamespace(
            context=SimpleNamespace(active_object=obj),
            types=SimpleNamespace(Mesh=FakeMesh, Object=FakeObject),
        )
        with (
            mock.patch.object(controller, "bpy", fake_bpy),
            mock.patch.object(controller, "_get_current_mode", return_value="OBJECT"),
            mock.patch.object(controller, "_pair_id_for_object_readonly", return_value="pair-target"),
            mock.patch.object(
                controller,
                "_classify_transform_update_for_evaluated",
                return_value=("content", "modifier_signature_changed", "new", "old"),
            ) as classify_mock,
            mock.patch.object(controller.object_state, "mark_dirty"),
            mock.patch.object(controller, "_mark_pair_dirty") as mark_pair_mock,
            mock.patch.object(controller, "_mark_evaluated_mesh_dirty") as mark_evaluated_mock,
            mock.patch.object(controller.time, "time", return_value=10.0),
        ):
            controller._dirty_probe_depsgraph_update(None, SimpleNamespace(updates=[update]))

        classify_mock.assert_called_once_with(obj, "pair-target")
        mark_pair_mock.assert_called_once()
        mark_evaluated_mock.assert_called_once_with(
            obj,
            source="modifier_signature_changed",
            dirty_class="content",
            now=10.0,
        )

    def test_evaluated_mesh_update_maps_back_to_ready_original_object(self) -> None:
        source_mesh = FakeMesh()
        evaluated_mesh = FakeMesh()
        evaluated_mesh.original = source_mesh
        obj = FakeObject()
        obj.name = "Target"
        obj.type = "MESH"
        obj.data = source_mesh
        obj.modifiers = []
        obj[controller.AUTO_SYNC_READY_KEY] = True
        fake_bpy = SimpleNamespace(
            context=SimpleNamespace(
                active_object=obj,
                scene=SimpleNamespace(objects=[obj]),
            ),
            types=SimpleNamespace(Mesh=FakeMesh, Object=FakeObject),
        )
        update = SimpleNamespace(
            id=evaluated_mesh,
            is_updated_geometry=True,
            is_updated_transform=True,
            is_updated_shading=True,
        )

        with (
            mock.patch.object(controller, "bpy", fake_bpy),
            mock.patch.object(controller, "_get_current_mode", return_value="OBJECT"),
            mock.patch.object(controller, "_pair_id_for_object_readonly", return_value="pair-target"),
            mock.patch.object(controller, "_object_for_pair_id", return_value=obj),
            mock.patch.object(controller, "_mark_pair_dirty") as mark_pair_mock,
            mock.patch.object(controller, "_mark_evaluated_mesh_dirty") as mark_evaluated_mock,
            mock.patch.object(controller.time, "time", return_value=10.0),
        ):
            controller._dirty_probe_depsgraph_update(None, SimpleNamespace(updates=[update]))

        mark_pair_mock.assert_called_once_with(
            "pair-target",
            channel="geometry",
            source="depsgraph_mesh_object_mode",
            now=10.0,
        )
        mark_evaluated_mock.assert_called_once_with(
            obj,
            source="depsgraph_mesh",
            dirty_class="content",
            now=10.0,
        )

    def test_node_tree_update_without_transform_remains_content(self) -> None:
        obj = FakeObject()
        obj.name = "Target"
        obj.type = "MESH"
        obj.data = FakeMesh()
        obj.modifiers = []
        obj[controller.AUTO_SYNC_READY_KEY] = True
        tree = GeometryNodeTree("GeometryNodes")
        fake_bpy = SimpleNamespace(
            context=SimpleNamespace(active_object=obj),
            types=SimpleNamespace(Mesh=FakeMesh, Object=FakeObject),
        )
        with (
            mock.patch.object(controller, "bpy", fake_bpy),
            mock.patch.object(controller, "_get_current_mode", return_value="OBJECT"),
            mock.patch.object(controller, "_get_sync_enabled", return_value=True),
            mock.patch.object(controller, "_pair_id_for_object_readonly", return_value="pair-target"),
            mock.patch.object(controller, "_object_for_pair_id", return_value=obj),
            mock.patch.object(controller, "_should_auto_rebuild_evaluated_object", return_value=True),
            mock.patch.object(controller, "_object_has_visible_modifiers", return_value=True),
            mock.patch.object(controller, "modifier_stack_signature", return_value="same"),
            mock.patch.object(controller, "_mark_evaluated_mesh_dirty") as mark_mock,
        ):
            controller._dirty_probe_depsgraph_update(
                None,
                SimpleNamespace(
                    updates=[
                        SimpleNamespace(
                            id=tree,
                            is_updated_geometry=False,
                            is_updated_transform=False,
                            is_updated_shading=False,
                        )
                    ]
                ),
            )

        mark_mock.assert_called_once()
        self.assertEqual("content", mark_mock.call_args.kwargs["dirty_class"])

    @staticmethod
    def _depsgraph():
        update = SimpleNamespace(
            id=FakeData("Node"),
            is_updated_geometry=False,
            is_updated_transform=False,
            is_updated_shading=True,
        )
        return SimpleNamespace(updates=[update])

    @staticmethod
    def _probe_stack(*, verbose: bool) -> ExitStack:
        fake_bpy = SimpleNamespace(
            context=SimpleNamespace(active_object=None),
            types=SimpleNamespace(Mesh=FakeMesh, Object=FakeObject),
        )
        stack = ExitStack()
        stack.enter_context(mock.patch.object(controller, "bpy", fake_bpy))
        stack.enter_context(mock.patch.object(controller, "DIRTY_PROBE_VERBOSE", verbose))
        stack.enter_context(mock.patch.object(controller, "_get_current_mode", return_value="OBJECT"))
        stack.enter_context(mock.patch.object(controller.time, "time", return_value=10.0))
        return stack


if __name__ == "__main__":
    unittest.main()
