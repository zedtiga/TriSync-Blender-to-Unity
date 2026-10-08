from __future__ import annotations

import sys
import unittest
from array import array
from contextlib import ExitStack
from pathlib import Path
from types import SimpleNamespace
from unittest import mock


ADDON_ROOT = Path(__file__).resolve().parents[2] / "blender_addon" / "blendersync_vnext"
sys.path.insert(0, str(ADDON_ROOT))

from blender.scene_sync import controller
from blender.scene_sync import material_sync
from blender.scene_sync import mesh_context
from blender.common.evaluated_mesh import EvaluatedMeshLease


class FakeMesh:
    def __init__(self) -> None:
        self.vertices = [object(), object()]
        self.loops = [object(), object(), object()]
        self.loop_triangles = [object()]
        self.shape_keys = None

    def calc_loop_triangles(self) -> None:
        return None


class FakeMeshObject(dict):
    def __init__(self) -> None:
        super().__init__()
        self.name = "Cube"
        self.type = "MESH"
        self.data = FakeMesh()
        self.modifiers = []
        self[controller.AUTO_SYNC_READY_KEY] = True


class ControllerPreviewCacheCharacterizationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.print_patch = mock.patch("builtins.print")
        self.print_patch.start()
        controller.reset_file_runtime_state(reason="preview_cache_setup")
        self.obj = FakeMeshObject()
        self.pair_id = "pair-object"

    def tearDown(self) -> None:
        controller.reset_file_runtime_state(reason="preview_cache_teardown")
        self.print_patch.stop()

    def test_native_binary_build_caches_source_mappings_and_hash_profile(self) -> None:
        native_result = self._native_binary_result(
            source_indices=array("i", [0, 1]),
            source_loop_indices=array("i", [2, 3]),
        )

        with self._build_stack(native_result) as stack:
            attach_uv_mock = stack.enter_context(
                mock.patch.object(mesh_context, "_attach_uv_channels_to_prebuilt_binary")
            )
            context = controller._build_mesh_update_context_for_object(self.obj, include_extras=True)

        self.assertIsNotNone(context)
        self.assertEqual([0, 1], list(controller._preview_mapping_runtime.source_indices_by_pair[self.pair_id]))
        self.assertEqual([2, 3], list(controller._preview_mapping_runtime.source_loop_indices_by_pair[self.pair_id]))
        attach_uv_mock.assert_called_once()
        hashes = controller._preview_mapping_runtime.hashes_by_pair[self.pair_id]
        self.assertEqual("vertex-hash", hashes["vertices"])
        self.assertEqual("normal-hash", hashes["normals"])
        self.assertEqual("index-hash", hashes["indices"])
        self.assertEqual("source-index-hash", hashes["sourceIndices"])
        self.assertEqual("source-loop-hash", hashes["sourceLoopIndices"])
        self.assertEqual("topology-hash", hashes["topology"])
        self.assertNotIn("uv0", hashes)

    def test_mismatched_source_loop_mapping_clears_stale_loop_cache_and_uv_channels(self) -> None:
        controller._preview_mapping_runtime.source_loop_indices_by_pair[self.pair_id] = array("i", [9, 9])
        native_result = self._native_binary_result(
            source_indices=array("i", [0, 1]),
            source_loop_indices=array("i", [2]),
        )

        with self._build_stack(native_result) as stack:
            attach_uv_mock = stack.enter_context(
                mock.patch.object(mesh_context, "_attach_uv_channels_to_prebuilt_binary")
            )
            context = controller._build_mesh_update_context_for_object(self.obj, include_extras=True)

        self.assertIsNotNone(context)
        self.assertNotIn(self.pair_id, controller._preview_mapping_runtime.source_loop_indices_by_pair)
        self.assertEqual([], context["prebuilt_binary"]["uvChannels"])
        attach_uv_mock.assert_not_called()

    def test_pair_cleanup_removes_source_mappings_and_all_preview_hashes(self) -> None:
        controller._preview_mapping_runtime.source_indices_by_pair[self.pair_id] = array("i", [0])
        controller._preview_mapping_runtime.source_loop_indices_by_pair[self.pair_id] = array("i", [1])
        controller._preview_mapping_runtime.hashes_by_pair[self.pair_id] = {
            "vertices": "vertex-hash",
            "meshContent": "content-hash",
        }

        controller._clear_pair_runtime_state(self.pair_id, reason="test")

        self.assertNotIn(self.pair_id, controller._preview_mapping_runtime.source_indices_by_pair)
        self.assertNotIn(self.pair_id, controller._preview_mapping_runtime.source_loop_indices_by_pair)
        self.assertNotIn(self.pair_id, controller._preview_mapping_runtime.hashes_by_pair)

    def test_evaluated_runtime_has_no_periodic_mesh_poll_state(self) -> None:
        runtime = controller._evaluated_preview_runtime
        self.assertFalse(hasattr(runtime, "modifier_poll_next_time_by_pair"))
        self.assertFalse(hasattr(runtime, "mesh_content_poll_next_time_by_pair"))

    def test_evaluated_mesh_lease_is_used_for_realized_geometry(self) -> None:
        native_result = self._native_binary_result(
            source_indices=array("i", [0, 1]),
            source_loop_indices=array("i", [2, 3]),
        )
        lease = EvaluatedMeshLease(
            owner=self.obj,
            mesh=self.obj.data,
            source="evaluated_realized_instances",
            instance_count=10,
            realized_instances=True,
        )
        lease_context = mock.MagicMock()
        lease_context.__enter__.return_value = lease
        lease_context.__exit__.return_value = False

        with self._build_stack(native_result) as stack:
            stack.enter_context(
                mock.patch.object(
                    mesh_context,
                    "resolve_auto_preview_mesh_source",
                    return_value=("evaluated", "forced_evaluated"),
                )
            )
            evaluated_mesh_mock = stack.enter_context(
                mock.patch.object(
                    mesh_context,
                    "evaluated_mesh_for_sync",
                    return_value=lease_context,
                )
            )
            context = controller._build_mesh_update_context_for_object(self.obj, include_extras=True)

        self.assertIsNotNone(context)
        evaluated_mesh_mock.assert_called_once_with(self.obj)
        self.assertEqual("evaluated_realized_instances", context["evaluated_mesh_source"])
        self.assertEqual(10, context["evaluated_instance_count"])
        self.assertTrue(context["evaluated_instances_realized"])
        self.assertTrue(context["mesh_content_fingerprint_debug"]["evaluatedInstancesRealized"])

    def test_empty_mesh_export_is_rejected_before_context_is_returned(self) -> None:
        native_result = self._native_binary_result(
            source_indices=array("i"),
            source_loop_indices=array("i"),
        )
        native_result["vertexCount"] = 0
        native_result["indexCount"] = 0

        with self._build_stack(native_result):
            with self.assertRaisesRegex(ValueError, "^mesh_has_no_exportable_geometry$"):
                controller._build_mesh_update_context_for_object(self.obj, include_extras=True)

        self.assertNotIn(self.pair_id, controller._preview_mapping_runtime.source_indices_by_pair)
        self.assertNotIn(self.pair_id, controller._preview_mapping_runtime.hashes_by_pair)

    def test_python_fallback_preserves_uv_channels_and_blend_shape_source_mapping(self) -> None:
        source_indices = [0, 1, 0]
        source_loop_indices = [2, 3, 4]
        uv_channels = [
            {"index": 0, "name": "UV_Main", "values": array("f", [0.0, 0.0, 1.0, 0.0, 0.0, 1.0])},
            {"index": 1, "name": "UV_Second", "values": array("f", [0.2, 0.3, 0.4, 0.5, 0.6, 0.7])},
        ]
        built_shape = {"name": "Smile", "deltaPositions": array("f", [0.0] * 9)}

        with self._build_stack(None) as stack:
            stack.enter_context(mock.patch.object(mesh_context, "try_extract_mesh_arrays_native", return_value=None))
            stack.enter_context(
                mock.patch.object(
                    mesh_context,
                    "_build_mesh_arrays_from_raw_for_preview_detailed",
                    return_value=(
                        [0.0] * 9,
                        [0.0, 0.0, 1.0] * 3,
                        [0.0, 0.0, 1.0, 0.0, 0.0, 1.0],
                        [0, 1, 2],
                        source_indices,
                        source_loop_indices,
                    ),
                )
            )
            stack.enter_context(
                mock.patch.object(
                    mesh_context,
                    "_build_uv_channels_from_raw_for_preview",
                    return_value=uv_channels,
                )
            )
            stack.enter_context(mock.patch.object(mesh_context, "_build_color0_from_raw_for_preview", return_value=None))
            blend_shape_mock = stack.enter_context(
                mock.patch.object(mesh_context, "build_mesh_blend_shapes", return_value=[built_shape])
            )
            context = controller._build_mesh_update_context_for_object(self.obj, include_extras=True)

        self.assertIsNotNone(context)
        self.assertEqual(source_indices, list(controller._preview_mapping_runtime.source_indices_by_pair[self.pair_id]))
        self.assertEqual(source_loop_indices, list(controller._preview_mapping_runtime.source_loop_indices_by_pair[self.pair_id]))
        self.assertEqual(uv_channels, context["mesh_content"]["uvChannels"])
        self.assertEqual([built_shape], context["mesh_content"]["blendShapes"])
        blend_shape_mock.assert_called_once_with(self.obj.data, source_indices)

    def test_multi_uv_preview_falls_back_when_native_lacks_capability(self) -> None:
        raw = {
            "uv_layers": [
                {"index": 0, "name": "UV_Main", "values": array("f", [0.0, 0.0])},
                {"index": 1, "name": "UV_Second", "values": array("f", [0.5, 0.5])},
            ]
        }
        native_result = self._native_binary_result(
            source_indices=array("i", [0, 1]),
            source_loop_indices=array("i", [2, 3]),
        )
        source_indices = [0, 1, 0]
        source_loop_indices = [0, 1, 2]

        with self._build_stack(native_result) as stack:
            stack.enter_context(mock.patch.object(mesh_context, "_collect_mesh_raw_buffers_for_preview", return_value=raw))
            native_binary_mock = stack.enter_context(mock.patch.object(mesh_context, "try_extract_mesh_binary_native"))
            native_array_mock = stack.enter_context(mock.patch.object(mesh_context, "try_extract_mesh_arrays_native"))
            stack.enter_context(
                mock.patch.object(
                    mesh_context,
                    "_build_mesh_arrays_from_raw_for_preview_detailed",
                    return_value=(
                        [0.0] * 9,
                        [0.0, 0.0, 1.0] * 3,
                        [0.0] * 6,
                        [0, 1, 2],
                        source_indices,
                        source_loop_indices,
                    ),
                )
            )
            stack.enter_context(mock.patch.object(mesh_context, "_build_uv_channels_from_raw_for_preview", return_value=[]))
            context = controller._build_mesh_update_context_for_object(self.obj, include_extras=True)

        self.assertIsNotNone(context)
        self.assertEqual("foreach_get_python_multi_uv", context["mesh_content_fingerprint_debug"]["buildMode"])
        native_binary_mock.assert_not_called()
        native_array_mock.assert_not_called()

    def test_multi_uv_preview_uses_native_binary_when_capability_is_present(self) -> None:
        raw = {
            "uv_layers": [
                {"index": 0, "name": "UV_Main", "values": array("f", [0.0, 0.0])},
                {"index": 7, "name": "UV_Aux", "values": array("f", [0.5, 0.5])},
            ]
        }
        native_result = self._native_binary_result(
            source_indices=array("i", [0, 1]),
            source_loop_indices=array("i", [0, 1]),
        )

        with self._build_stack(native_result) as stack:
            stack.enter_context(mock.patch.object(mesh_context, "_collect_mesh_raw_buffers_for_preview", return_value=raw))
            stack.enter_context(
                mock.patch.object(
                    mesh_context,
                    "get_native_status",
                    return_value={
                        "available": True,
                        "version": "0.2.0",
                        "capabilities": [mesh_context.NATIVE_MULTI_UV_CAPABILITY],
                    },
                )
            )
            attach_uv_mock = stack.enter_context(
                mock.patch.object(mesh_context, "_attach_uv_channels_to_prebuilt_binary")
            )
            context = controller._build_mesh_update_context_for_object(self.obj, include_extras=True)

        self.assertIsNotNone(context)
        self.assertEqual("pyd_binary_v1", context["mesh_content_fingerprint_debug"]["buildMode"])
        attach_uv_mock.assert_called_once()

    def test_python_fallback_deduplication_keeps_secondary_uv_seams_distinct(self) -> None:
        raw = {
            "tri_count": 2,
            "positions": array("f", [0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0, 0.0]),
            "vertex_normals": array("f", [0.0, 0.0, 1.0] * 3),
            "loop_vertex_indices": array("i", [0, 1, 2, 0, 2, 1]),
            "loop_normals": array("f", [0.0, 0.0, 1.0] * 6),
            "tri_loops": array("i", [0, 1, 2, 3, 4, 5]),
            "uv0": array("f", [0.0, 0.0, 1.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0, 1.0, 0.0]),
            "uv_layers": [
                {"index": 0, "name": "UV_Main", "values": array("f", [0.0, 0.0, 1.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0, 1.0, 0.0])},
                {"index": 1, "name": "UV_Second", "values": array("f", [0.0, 0.0, 0.5, 0.0, 0.0, 0.5, 0.75, 0.75, 0.0, 0.5, 0.5, 0.0])},
            ],
            "have_loop_normals": True,
        }

        result = mesh_context._build_mesh_arrays_from_raw_for_preview_detailed(raw)

        self.assertEqual(4, len(result[0]) // 3)
        self.assertEqual([0, 1, 2, 3, 2, 1], result[3])
        self.assertEqual([0, 1, 2, 0], result[4])
        self.assertEqual([0, 1, 2, 3], result[5])

    def _build_stack(self, native_result: dict | None) -> ExitStack:
        stack = ExitStack()
        stack.enter_context(mock.patch.object(mesh_context, "get_session", return_value=object()))
        stack.enter_context(mock.patch.object(mesh_context, "ensure_instance_id", return_value="object"))
        stack.enter_context(mock.patch.object(mesh_context, "ensure_mesh_asset_id_for_object", return_value="mesh-id"))
        stack.enter_context(mock.patch.object(controller, "_get_current_mode", return_value="OBJECT"))
        stack.enter_context(mock.patch.object(controller, "_get_sync_enabled", return_value=True))
        stack.enter_context(mock.patch.object(controller, "_resolve_auto_preview_mesh_source", return_value=("original", "test")))
        stack.enter_context(mock.patch.object(mesh_context, "_collect_mesh_raw_buffers_for_preview", return_value={"raw": True}))
        stack.enter_context(mock.patch.object(controller, "_preview_buffer_dir", return_value=Path(".")))
        stack.enter_context(mock.patch.object(mesh_context, "try_extract_mesh_binary_native", return_value=native_result))
        stack.enter_context(mock.patch.object(mesh_context, "try_extract_mesh_arrays_native"))
        stack.enter_context(mock.patch.object(mesh_context, "get_native_status", return_value={"available": True, "version": "test"}))
        stack.enter_context(mock.patch.object(mesh_context, "_raw_preview_needs_submeshes", return_value=False))
        stack.enter_context(mock.patch.object(mesh_context, "_build_color0_from_raw_for_preview", return_value=None))
        stack.enter_context(mock.patch.object(mesh_context, "_attach_color0_to_prebuilt_binary"))
        stack.enter_context(mock.patch.object(mesh_context, "build_mesh_blend_shapes", return_value=[]))
        stack.enter_context(mock.patch.object(mesh_context, "_attach_blend_shapes_to_prebuilt_binary"))
        stack.enter_context(mock.patch.object(material_sync, "collect_refs_for_object", return_value=()))
        stack.enter_context(mock.patch.object(controller, "_should_include_unity_mesh_content_fingerprint", return_value=False))
        stack.enter_context(mock.patch.object(mesh_context, "compute_mesh_content_fingerprint", return_value="fingerprint"))
        return stack

    @staticmethod
    def _native_binary_result(*, source_indices: array, source_loop_indices: array) -> dict:
        return {
            "ok": True,
            "buffers": [{"semantic": "POSITION", "path": "unused.bin"}],
            "vertexCount": len(source_indices),
            "indexCount": 3,
            "sourceIndices": source_indices,
            "sourceLoopIndices": source_loop_indices,
            "hashProfile": {
                "vertexSha1": "vertex-hash",
                "normalSha1": "normal-hash",
                "uv0Sha1": "uv-hash",
                "indexSha1": "index-hash",
                "sourceIndexSha1": "source-index-hash",
                "sourceLoopIndexSha1": "source-loop-hash",
                "topologySha1": "topology-hash",
            },
            "profile": {},
        }


if __name__ == "__main__":
    unittest.main()
