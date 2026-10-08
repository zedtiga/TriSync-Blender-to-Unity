from __future__ import annotations

import sys
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock


ADDON_ROOT = Path(__file__).resolve().parents[2] / "blender_addon" / "blendersync_vnext"
sys.path.insert(0, str(ADDON_ROOT))

from blender.scene_sync import controller
from blender.scene_sync import object_lifecycle
from blender.scene_sync import material_sync
from blender.session import file_lifecycle
from blender import unity_mesh_import
from blender.common.evaluated_mesh import TEMPORARY_EVALUATED_OBJECT_KEY


FILE_SCOPED_COLLECTION_NAMES = (
    "_dirty_probe_counts_by_id",
    "_dirty_probe_last_log_by_id",
    "_dirty_probe_last_seen_by_id",
)

LIFECYCLE_COLLECTION_FIELDS = (
    "baseline_ids",
    "managed_ids",
    "entries",
    "new_reason_by_id",
    "delete_reason_by_id",
)

OBJECT_STATE_COLLECTION_FIELDS = (
    "signatures_by_pair",
    "non_transform_signatures_by_pair",
    "dirty_queue",
    "motion_burst_until_by_pair",
    "active_fallback_next_at_by_pair",
)

MATERIAL_COLLECTION_FIELDS = (
    "refs_by_pair",
    "slots_poll_next_at_by_pair",
    "pending_bundles_by_ref",
    "waiting_pair_ids_by_ref",
    "pending_resends_by_ref",
    "retry_after_by_ref",
)

GEOMETRY_PREVIEW_COLLECTION_FIELDS = (
    "dirty_by_pair",
    "inflight_by_pair",
    "dirty_during_inflight_by_pair",
)

EVALUATED_PREVIEW_COLLECTION_FIELDS = (
    "dirty_by_pair",
    "object_name_by_pair",
    "last_preview_sent_at_by_pair",
    "had_visible_modifiers_by_pair",
    "modifier_signature_by_pair",
)

MESH_STRUCTURE_COLLECTION_FIELDS = (
    "uv_channels_signature_by_pair",
    "uv_channels_poll_next_time_by_pair",
    "color_attributes_signature_by_pair",
    "color_attributes_poll_next_time_by_pair",
    "shape_key_structure_signature_by_pair",
    "shape_key_structure_poll_next_time_by_pair",
)

SHAPE_KEY_COLLECTION_FIELDS = (
    "edit_skip_logged_by_pair",
    "last_weights_by_pair",
    "next_weight_send_time_by_pair",
)

UV_PREVIEW_COLLECTION_FIELDS = (
    "last_skip_log_by_pair",
    "inflight_by_pair",
)

PREVIEW_MAPPING_COLLECTION_FIELDS = (
    "source_indices_by_pair",
    "source_loop_indices_by_pair",
    "hashes_by_pair",
)

FILE_SCOPED_SCALAR_DEFAULTS = {
    "_last_tick_time": 0.0,
    "_last_mesh_tick_time": 0.0,
    "_last_lifecycle_tick_time": 0.0,
    "_last_view_tick_time": 0.0,
    "_last_view_fingerprint": None,
    "_last_mode": "UNKNOWN",
    "_last_sync_enabled": False,
    "_last_auto_sync_state_signature": None,
    "_dirty_probe_last_summary_time": 0.0,
}


class ControllerFileRuntimeStateTests(unittest.TestCase):
    def test_lifecycle_reconcile_ignores_temporary_realize_objects(self) -> None:
        class FakeObject(dict):
            def __init__(self, name: str, temporary: bool = False) -> None:
                super().__init__()
                self.name = name
                self.type = "MESH"
                self.data = object()
                if temporary:
                    self[TEMPORARY_EVALUATED_OBJECT_KEY] = True

        temporary = FakeObject("__BlenderSyncRealize_probe", temporary=True)
        regular = FakeObject("Cube")
        fake_bpy = SimpleNamespace(
            context=SimpleNamespace(scene=SimpleNamespace(objects=[temporary, regular]))
        )

        with mock.patch.object(object_lifecycle, "bpy", fake_bpy):
            current = object_lifecycle.collect_scene_instance_ids(controller._lifecycle_runtime)

        self.assertNotIn("blendersync_instance_id", temporary)
        self.assertEqual([regular], list(current.values()))

    def test_file_reset_clears_all_file_scoped_state(self) -> None:
        lifecycle_collection_ids = {}
        for name in LIFECYCLE_COLLECTION_FIELDS:
            collection = getattr(controller._lifecycle_runtime, name)
            lifecycle_collection_ids[name] = id(collection)
            if isinstance(collection, set):
                collection.add("test")
            else:
                collection["test"] = 1
        object_state_collection_ids = {}
        for name in OBJECT_STATE_COLLECTION_FIELDS:
            collection = getattr(controller._object_state_runtime, name)
            object_state_collection_ids[name] = id(collection)
            collection["test"] = 1
        material_collection_ids = {}
        for name in MATERIAL_COLLECTION_FIELDS:
            collection = getattr(controller._material_runtime, name)
            material_collection_ids[name] = id(collection)
            collection["test"] = 1
        controller._material_runtime.send_inflight = True
        geometry_preview_collection_ids = {}
        for name in GEOMETRY_PREVIEW_COLLECTION_FIELDS:
            collection = getattr(controller._geometry_preview_runtime, name)
            geometry_preview_collection_ids[name] = id(collection)
            collection["test"] = 1
        evaluated_preview_collection_ids = {}
        for name in EVALUATED_PREVIEW_COLLECTION_FIELDS:
            collection = getattr(controller._evaluated_preview_runtime, name)
            evaluated_preview_collection_ids[name] = id(collection)
            collection["test"] = 1
        mesh_structure_collection_ids = self._populate_runtime_collections(
            controller._mesh_structure_runtime,
            MESH_STRUCTURE_COLLECTION_FIELDS,
        )
        shape_key_collection_ids = self._populate_runtime_collections(
            controller._shape_key_runtime,
            SHAPE_KEY_COLLECTION_FIELDS,
        )
        uv_preview_collection_ids = self._populate_runtime_collections(
            controller._uv_preview_runtime,
            UV_PREVIEW_COLLECTION_FIELDS,
        )
        preview_mapping_collection_ids = self._populate_runtime_collections(
            controller._preview_mapping_runtime,
            PREVIEW_MAPPING_COLLECTION_FIELDS,
        )
        for name in FILE_SCOPED_COLLECTION_NAMES:
            collection = getattr(controller, name)
            if isinstance(collection, set):
                collection.add("test")
            else:
                collection["test"] = 1
        for name, default in FILE_SCOPED_SCALAR_DEFAULTS.items():
            setattr(controller, name, "changed" if default is None else object())
        controller._object_state_runtime.non_transform_poll_cursor = 7
        controller._preview_mapping_runtime.last_mesh_diag_time = 7.0
        controller._uv_preview_runtime.last_buffer_cleanup_at = 7.0

        with mock.patch("builtins.print"):
            controller.reset_file_runtime_state(reason="test")

        for name in LIFECYCLE_COLLECTION_FIELDS:
            collection = getattr(controller._lifecycle_runtime, name)
            self.assertEqual(0, len(collection), name)
            self.assertEqual(lifecycle_collection_ids[name], id(collection), name)
        for name in OBJECT_STATE_COLLECTION_FIELDS:
            collection = getattr(controller._object_state_runtime, name)
            self.assertEqual(0, len(collection), name)
            self.assertEqual(object_state_collection_ids[name], id(collection), name)
        for name in MATERIAL_COLLECTION_FIELDS:
            collection = getattr(controller._material_runtime, name)
            self.assertEqual(0, len(collection), name)
            self.assertEqual(material_collection_ids[name], id(collection), name)
        self.assertFalse(controller._material_runtime.send_inflight)
        for name in GEOMETRY_PREVIEW_COLLECTION_FIELDS:
            collection = getattr(controller._geometry_preview_runtime, name)
            self.assertEqual(0, len(collection), name)
            self.assertEqual(geometry_preview_collection_ids[name], id(collection), name)
        for name in EVALUATED_PREVIEW_COLLECTION_FIELDS:
            collection = getattr(controller._evaluated_preview_runtime, name)
            self.assertEqual(0, len(collection), name)
            self.assertEqual(evaluated_preview_collection_ids[name], id(collection), name)
        self._assert_runtime_collections_reset(
            controller._mesh_structure_runtime,
            mesh_structure_collection_ids,
        )
        self._assert_runtime_collections_reset(
            controller._shape_key_runtime,
            shape_key_collection_ids,
        )
        self._assert_runtime_collections_reset(
            controller._uv_preview_runtime,
            uv_preview_collection_ids,
        )
        self._assert_runtime_collections_reset(
            controller._preview_mapping_runtime,
            preview_mapping_collection_ids,
        )
        for name in FILE_SCOPED_COLLECTION_NAMES:
            self.assertEqual(0, len(getattr(controller, name)), name)
        for name, default in FILE_SCOPED_SCALAR_DEFAULTS.items():
            self.assertEqual(default, getattr(controller, name), name)
        self.assertEqual(0, controller._object_state_runtime.non_transform_poll_cursor)
        self.assertEqual(0.0, controller._preview_mapping_runtime.last_mesh_diag_time)
        self.assertEqual(0.0, controller._uv_preview_runtime.last_buffer_cleanup_at)

    @staticmethod
    def _populate_runtime_collections(runtime, fields) -> dict[str, int]:
        collection_ids = {}
        for name in fields:
            collection = getattr(runtime, name)
            collection_ids[name] = id(collection)
            if isinstance(collection, set):
                collection.add("test")
            else:
                collection["test"] = 1
        return collection_ids

    def _assert_runtime_collections_reset(self, runtime, collection_ids: dict[str, int]) -> None:
        for name, collection_id in collection_ids.items():
            collection = getattr(runtime, name)
            self.assertEqual(0, len(collection), name)
            self.assertEqual(collection_id, id(collection), name)

    def test_controller_uses_actual_timer_registration_state(self) -> None:
        class FakeTimers:
            def __init__(self) -> None:
                self.registered = set()
                self.register_thread_id = None
                self.persistent = None

            def is_registered(self, callback) -> bool:
                return callback in self.registered

            def register(self, callback, *, persistent) -> None:
                self.registered.add(callback)
                self.register_thread_id = threading.get_ident()
                self.persistent = persistent

            def unregister(self, callback) -> None:
                self.registered.discard(callback)

        timers = FakeTimers()
        handlers = SimpleNamespace(depsgraph_update_post=[])
        fake_bpy = SimpleNamespace(app=SimpleNamespace(timers=timers, handlers=handlers))
        previous_timer_flag = controller._timer_registered
        previous_probe_flag = controller._dirty_probe_registered
        try:
            controller._timer_registered = True
            controller._dirty_probe_registered = False
            with mock.patch.object(controller, "bpy", fake_bpy):
                with mock.patch("builtins.print"):
                    controller.register_controller()
                self.assertTrue(controller.is_controller_timer_registered())
                self.assertEqual(threading.get_ident(), timers.register_thread_id)
                self.assertFalse(timers.persistent)
                self.assertIn(controller._dirty_probe_depsgraph_update, handlers.depsgraph_update_post)
                self.assertTrue(controller.is_dirty_probe_registered())
                controller.unregister_controller()
                self.assertFalse(controller.is_controller_timer_registered())
                self.assertFalse(controller.is_dirty_probe_registered())
        finally:
            controller._timer_registered = previous_timer_flag
            controller._dirty_probe_registered = previous_probe_flag

    def test_removed_pair_is_detached_from_material_waiters_and_resends(self) -> None:
        removed_pair = "pair-removed"
        kept_pair = "pair-kept"
        controller._material_runtime.waiting_pair_ids_by_ref.update(
            {
                "mat-shared": {removed_pair, kept_pair},
                "mat-orphaned": {removed_pair},
            }
        )
        controller._material_runtime.pending_bundles_by_ref.update(
            {
                "mat-shared": {"bundle": "shared"},
                "mat-orphaned": {"bundle": "orphaned"},
            }
        )
        controller._material_runtime.retry_after_by_ref.update(
            {"mat-shared": 1.0, "mat-orphaned": 2.0}
        )
        controller._material_runtime.pending_resends_by_ref.update(
            {
                "mat-resend-shared": {"pairIds": {removed_pair, kept_pair}, "attempt": 0},
                "mat-resend-orphaned": {"pairIds": {removed_pair}, "attempt": 0},
            }
        )
        try:
            with mock.patch("builtins.print"):
                result = material_sync.remove_pair_from_runtime(
                    controller._material_runtime,
                    removed_pair,
                    reason="test",
                )

            self.assertEqual({"waitingRefs": 2, "preservedBundles": 1, "resendRefs": 2}, result)
            self.assertEqual({kept_pair}, controller._material_runtime.waiting_pair_ids_by_ref["mat-shared"])
            self.assertNotIn("mat-orphaned", controller._material_runtime.waiting_pair_ids_by_ref)
            self.assertIn("mat-shared", controller._material_runtime.pending_bundles_by_ref)
            self.assertIn("mat-orphaned", controller._material_runtime.pending_bundles_by_ref)
            self.assertIn("mat-shared", controller._material_runtime.retry_after_by_ref)
            self.assertIn("mat-orphaned", controller._material_runtime.retry_after_by_ref)
            self.assertEqual(
                {kept_pair},
                controller._material_runtime.pending_resends_by_ref["mat-resend-shared"]["pairIds"],
            )
            self.assertNotIn("mat-resend-orphaned", controller._material_runtime.pending_resends_by_ref)
        finally:
            with mock.patch("builtins.print"):
                controller.reset_file_runtime_state(reason="test_cleanup")

    def test_empty_material_bundle_does_not_create_a_waiting_pair(self) -> None:
        with mock.patch.object(material_sync, "is_material_ref_known", return_value=False):
            queued = material_sync.queue_bundle_if_unknown(
                controller._material_runtime,
                "mat-empty",
                {"deps": [], "materialContents": []},
                pair_id="pair-test",
            )

        self.assertFalse(queued)
        self.assertNotIn("mat-empty", controller._material_runtime.waiting_pair_ids_by_ref)

    def test_preview_cache_clear_preserves_material_waiters_and_resends(self) -> None:
        pair_id = "pair-preview"
        controller._material_runtime.waiting_pair_ids_by_ref["mat-waiting"] = {pair_id}
        controller._material_runtime.pending_bundles_by_ref["mat-waiting"] = {"bundle": "pending"}
        controller._material_runtime.pending_resends_by_ref["mat-resend"] = {"pairIds": {pair_id}}
        try:
            controller._clear_pair_runtime_state(pair_id, reason="preview_test")

            self.assertEqual({pair_id}, controller._material_runtime.waiting_pair_ids_by_ref["mat-waiting"])
            self.assertIn("mat-waiting", controller._material_runtime.pending_bundles_by_ref)
            self.assertEqual({pair_id}, controller._material_runtime.pending_resends_by_ref["mat-resend"]["pairIds"])
        finally:
            with mock.patch("builtins.print"):
                controller.reset_file_runtime_state(reason="test_cleanup")


class UnityMeshImportFileRuntimeStateTests(unittest.TestCase):
    def test_import_pump_reports_unexpected_boundary_exception(self) -> None:
        payload = {"type": "unity_mesh.import_v1", "meshes": []}
        with unity_mesh_import._pending_lock:
            unity_mesh_import._pending_payloads.append(payload)
        try:
            with mock.patch.object(
                unity_mesh_import,
                "_load_pending_payload",
                side_effect=RuntimeError("load failed"),
            ):
                with mock.patch.object(unity_mesh_import, "_cleanup_pending_file") as cleanup_mock:
                    with mock.patch.object(unity_mesh_import, "report_boundary_exception") as report_mock:
                        with mock.patch.object(unity_mesh_import, "send_import_result") as result_mock:
                            self.assertIsNone(unity_mesh_import._pump_pending_imports())

            report_mock.assert_called_once()
            self.assertEqual("unity_mesh_import_pump", report_mock.call_args.args[0])
            cleanup_mock.assert_called_once_with(payload)
            result_mock.assert_called_once()
            self.assertEqual("failed", result_mock.call_args.args[0])
            self.assertEqual("load failed", result_mock.call_args.kwargs["error"])
        finally:
            with unity_mesh_import._pending_lock:
                unity_mesh_import._pending_payloads.clear()

    def test_file_reset_clears_queue_unregisters_timer_and_deletes_staging_files(self) -> None:
        class FakeTimers:
            def __init__(self) -> None:
                self.registered = set()

            def is_registered(self, callback) -> bool:
                return callback in self.registered

            def register(self, callback, **_kwargs) -> None:
                self.registered.add(callback)

            def unregister(self, callback) -> None:
                self.registered.discard(callback)

        import tempfile

        timers = FakeTimers()
        fake_bpy = SimpleNamespace(app=SimpleNamespace(timers=timers))
        with tempfile.TemporaryDirectory() as temp_dir:
            staging_root = Path(temp_dir) / "Temp" / "BlenderSyncVNext" / "UnityMeshImportsV1"
            staging_root.mkdir(parents=True)
            delete_path = staging_root / "import-delete.json"
            keep_path = staging_root / "import-keep.json"
            delete_path.write_text("{}", encoding="utf-8")
            keep_path.write_text("{}", encoding="utf-8")
            payloads = [
                {
                    "type": "unity_mesh.import_file_v1",
                    "payloadPath": str(delete_path),
                    "deleteAfterImport": True,
                },
                {
                    "type": "unity_mesh.import_file_v1",
                    "payloadPath": str(keep_path),
                    "deleteAfterImport": False,
                },
            ]
            previous_flag = unity_mesh_import._timer_registered
            try:
                with mock.patch.object(unity_mesh_import, "bpy", fake_bpy):
                    with unity_mesh_import._pending_lock:
                        unity_mesh_import._pending_payloads.extend(payloads)
                    timers.registered.add(unity_mesh_import._pump_pending_imports)
                    unity_mesh_import._timer_registered = True
                    with mock.patch("builtins.print"):
                        result = unity_mesh_import.reset_file_runtime_state(reason="test")

                    self.assertEqual(2, result["cleared"])
                    self.assertTrue(result["timerWasRegistered"])
                    self.assertFalse(result["timerRegistered"])
                    self.assertEqual([], unity_mesh_import._pending_payloads)
                    self.assertFalse(delete_path.exists())
                    self.assertTrue(keep_path.exists())
            finally:
                with unity_mesh_import._pending_lock:
                    unity_mesh_import._pending_payloads.clear()
                unity_mesh_import._timer_registered = previous_flag


class FileLifecycleHandlerTests(unittest.TestCase):
    def test_load_pre_clears_runtime_identity_caches_before_file_state(self) -> None:
        calls: list[str] = []
        with mock.patch.object(
            file_lifecycle.main_thread_dispatcher,
            "begin_file_load",
            side_effect=lambda: calls.append("dispatcher_begin"),
        ):
            with mock.patch.object(
                file_lifecycle,
                "reset_runtime_identity_cache",
                side_effect=lambda: calls.append("identity_reset"),
            ):
                with mock.patch.object(
                    file_lifecycle,
                    "reset_runtime_asset_registry_cache",
                    side_effect=lambda: calls.append("asset_registry_reset"),
                ):
                    with mock.patch.object(
                        file_lifecycle.unity_mesh_import,
                        "reset_file_runtime_state",
                        side_effect=lambda reason: calls.append("unity_mesh_reset_" + reason),
                    ):
                        with mock.patch.object(
                            file_lifecycle.controller,
                            "reset_file_runtime_state",
                            side_effect=lambda reason: calls.append("controller_reset_" + reason),
                        ):
                            file_lifecycle._on_load_pre(None)

        self.assertEqual(
            [
                "dispatcher_begin",
                "identity_reset",
                "asset_registry_reset",
                "unity_mesh_reset_load_pre",
                "controller_reset_load_pre",
            ],
            calls,
        )

    def test_handlers_are_registered_once_and_removed(self) -> None:
        handlers = SimpleNamespace(load_pre=[], load_post=[], load_post_fail=[])
        fake_bpy = SimpleNamespace(app=SimpleNamespace(handlers=handlers))

        with mock.patch.object(file_lifecycle, "bpy", fake_bpy):
            file_lifecycle.register_file_lifecycle()
            file_lifecycle.register_file_lifecycle()
            self.assertEqual([file_lifecycle._on_load_pre], handlers.load_pre)
            self.assertEqual([file_lifecycle._on_load_post], handlers.load_post)
            self.assertEqual([file_lifecycle._on_load_post_fail], handlers.load_post_fail)
            file_lifecycle.unregister_file_lifecycle()

        self.assertEqual([], handlers.load_pre)
        self.assertEqual([], handlers.load_post)
        self.assertEqual([], handlers.load_post_fail)

    def test_load_handlers_preserve_the_required_order(self) -> None:
        calls: list[str] = []
        with mock.patch.object(
            file_lifecycle.main_thread_dispatcher,
            "begin_file_load",
            side_effect=lambda: calls.append("dispatcher_begin"),
        ):
            with mock.patch.object(
                file_lifecycle.unity_mesh_import,
                "reset_file_runtime_state",
                side_effect=lambda reason: calls.append("unity_mesh_reset_" + reason),
            ):
                with mock.patch.object(
                    file_lifecycle.controller,
                    "reset_file_runtime_state",
                    side_effect=lambda reason: calls.append("reset_" + reason),
                ):
                    with mock.patch.object(
                        file_lifecycle.controller,
                        "register_controller",
                        side_effect=lambda: calls.append("controller_register"),
                    ):
                        with mock.patch.object(
                            file_lifecycle.controller,
                            "publish_current_auto_sync_state",
                            side_effect=lambda: calls.append("publish_auto_sync_state"),
                        ):
                            with mock.patch.object(
                                file_lifecycle.main_thread_dispatcher,
                                "complete_file_load",
                                side_effect=lambda: calls.append("dispatcher_complete"),
                            ):
                                file_lifecycle._on_load_pre(None)
                                file_lifecycle._on_load_post(None)

        self.assertEqual(
            [
                "dispatcher_begin",
                "unity_mesh_reset_load_pre",
                "reset_load_pre",
                "reset_load_post",
                "controller_register",
                "publish_auto_sync_state",
                "dispatcher_complete",
            ],
            calls,
        )

    def test_failed_file_load_uses_the_same_recovery_path(self) -> None:
        calls: list[str] = []
        with mock.patch.object(
            file_lifecycle.controller,
            "reset_file_runtime_state",
            side_effect=lambda reason: calls.append("reset_" + reason),
        ):
            with mock.patch.object(
                file_lifecycle.controller,
                "register_controller",
                side_effect=lambda: calls.append("controller_register"),
            ):
                with mock.patch.object(
                    file_lifecycle.controller,
                    "publish_current_auto_sync_state",
                    side_effect=lambda: calls.append("publish_auto_sync_state"),
                ):
                    with mock.patch.object(
                        file_lifecycle.main_thread_dispatcher,
                        "complete_file_load",
                        side_effect=lambda: calls.append("dispatcher_complete"),
                    ):
                        file_lifecycle._on_load_post_fail(None)

        self.assertEqual(
            ["reset_load_post_fail", "controller_register", "publish_auto_sync_state", "dispatcher_complete"],
            calls,
        )

    def test_each_file_recovery_step_failure_does_not_skip_later_steps(self) -> None:
        step_names = (
            "controller_reset",
            "controller_register",
            "publish_auto_sync_state",
        )
        for failing_step in step_names:
            with self.subTest(failing_step=failing_step):
                calls: list[str] = []

                def run_step(name: str):
                    calls.append(name)
                    if name == failing_step:
                        raise RuntimeError(name + " failed")

                with mock.patch.object(
                    file_lifecycle.controller,
                    "reset_file_runtime_state",
                    side_effect=lambda reason: run_step("controller_reset"),
                ):
                    with mock.patch.object(
                        file_lifecycle.controller,
                        "register_controller",
                        side_effect=lambda: run_step("controller_register"),
                    ):
                        with mock.patch.object(
                            file_lifecycle.controller,
                            "publish_current_auto_sync_state",
                            side_effect=lambda: run_step("publish_auto_sync_state"),
                        ):
                            with mock.patch.object(
                                file_lifecycle.main_thread_dispatcher,
                                "complete_file_load",
                                side_effect=lambda: run_step("dispatcher_complete"),
                            ):
                                with mock.patch("builtins.print") as print_mock:
                                    file_lifecycle._finish_file_load("test")

                self.assertEqual(
                    [
                        "controller_reset",
                        "controller_register",
                        "publish_auto_sync_state",
                        "dispatcher_complete",
                    ],
                    calls,
                )
                self.assertTrue(
                    any(f"step={failing_step}" in str(call) for call in print_mock.call_args_list)
                )


if __name__ == "__main__":
    unittest.main()
