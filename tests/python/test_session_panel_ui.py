from __future__ import annotations

import sys
import inspect
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock


ADDON_ROOT = Path(__file__).resolve().parents[2] / "blender_addon" / "blendersync_vnext"
sys.path.insert(0, str(ADDON_ROOT))

from blender.scene_sync import settings
from blender.scene_sync import controller
from blender.common import log as sync_log
from blender.common.types import SendResult
from blender.session.ws_client_adapter import MinimalWsClientHook
from blender.ui import bulk_job
from blender.ui import operators
from blender.ui import session_panel
from blender.ui import state_view


class FakeObject(dict):
    def __init__(
        self,
        object_type: str = "MESH",
        *,
        ready: bool = False,
        has_data: bool = True,
        armature=None,
        name: str | None = None,
    ) -> None:
        super().__init__()
        self.type = object_type
        self.data = object() if has_data else None
        self.name = name or object_type.title()
        self._armature = armature
        if ready:
            self[operators.AUTO_SYNC_READY_KEY] = True

    def find_armature(self):
        return self._armature


class SessionPanelUiTests(unittest.TestCase):
    def test_status_color_icons_follow_the_blender_44_enum_rename(self) -> None:
        self.assertEqual(
            "SEQUENCE_COLOR_04",
            session_panel._status_color_icon(4, blender_version=(4, 2, 23)),
        )
        self.assertEqual(
            "SEQUENCE_COLOR_09",
            session_panel._connection_ui_state({}, blender_version=(4, 3, 2))["status_icon"],
        )
        self.assertEqual(
            "STRIP_COLOR_03",
            session_panel._status_color_icon(3, blender_version=(4, 4, 0)),
        )
        self.assertEqual(
            "STRIP_COLOR_04",
            session_panel._session_diagnostics_presentation(
                {"connected": True}, blender_version=(5, 0, 1)
            )["status_icon"],
        )

    def test_connection_state_maps_connected_waiting_handshaking_and_disconnected_rows(self) -> None:
        self.assertEqual(
            {
                "label": "Unity",
                "status_icon": "STRIP_COLOR_04",
                "operator": "blendersync.session_disconnect",
                "action": "Disconnect",
                "action_icon": "UNLINKED",
            },
            session_panel._connection_ui_state({"connected": True, "transport_connected": True}),
        )
        self.assertEqual(
            {
                "label": "Waiting for Unity",
                "status_icon": "STRIP_COLOR_03",
                "operator": "blendersync.session_disconnect",
                "action": "Disconnect",
                "action_icon": "UNLINKED",
            },
            session_panel._connection_ui_state({"local_ready": True}),
        )
        self.assertEqual(
            {
                "label": "Handshaking",
                "status_icon": "STRIP_COLOR_03",
                "operator": "blendersync.session_disconnect",
                "action": "Disconnect",
                "action_icon": "UNLINKED",
            },
            session_panel._connection_ui_state({"connected": False, "transport_connected": True}),
        )
        self.assertEqual(
            {
                "label": "Disconnected",
                "status_icon": "STRIP_COLOR_09",
                "operator": "blendersync.session_connect",
                "action": "Connect Unity",
                "action_icon": "LINKED",
            },
            session_panel._connection_ui_state({}),
        )

    def test_connection_status_and_action_use_separate_consistent_rows(self) -> None:
        source = inspect.getsource(session_panel._draw_connection)
        self.assertIn("status_row = layout.row", source)
        self.assertIn("action_row = layout.row", source)
        self.assertEqual(2, source.count("scale_y = 1.2"))
        self.assertIn('port_row.prop(preferences, "session_port", text="Port"', source)
        self.assertLess(source.index("status_row.label"), source.index("action_row.operator"))

    def test_session_port_is_editable_only_while_fully_disconnected(self) -> None:
        self.assertTrue(session_panel._session_port_editable({}))
        self.assertFalse(session_panel._session_port_editable({"connect_attempted": True}))
        self.assertFalse(session_panel._session_port_editable({"local_ready": True}))
        self.assertFalse(session_panel._session_port_editable({"transport_connected": True}))
        self.assertFalse(session_panel._session_port_editable({"connected": True}))

    def test_session_endpoint_normalizes_invalid_ports(self) -> None:
        self.assertEqual(8765, state_view.normalize_ws_port(None))
        self.assertEqual(8765, state_view.normalize_ws_port(True))
        self.assertEqual(8765, state_view.normalize_ws_port(0))
        self.assertEqual(8765, state_view.normalize_ws_port(65536))
        self.assertEqual(49152, state_view.normalize_ws_port("49152"))
        self.assertEqual("ws://127.0.0.1:49152/ws/", state_view.build_local_ws_endpoint(49152))

    def test_session_connect_uses_the_user_preference_port(self) -> None:
        operator = operators.BS_OT_SessionConnect()
        with mock.patch.object(operators, "get_session_port", return_value=49152), mock.patch.object(
            operators,
            "connect_minimal_ws_runtime",
            return_value=True,
        ) as connect:
            self.assertEqual({"FINISHED"}, operator.execute(SimpleNamespace()))
        connect.assert_called_once_with("ws://127.0.0.1:49152/ws/")

    def test_session_port_is_an_addon_preference_not_a_scene_property(self) -> None:
        preferences_annotations = getattr(settings.BlenderSyncAddonPreferences, "__annotations__", {})
        register_source = inspect.getsource(settings.register_settings)
        unregister_source = inspect.getsource(settings.unregister_settings)
        self.assertEqual("blendersync_vnext", settings.BlenderSyncAddonPreferences.bl_idname)
        if settings.bpy is not None:
            self.assertIn("session_port", preferences_annotations)
        self.assertIn("_safe_register_class(BlenderSyncAddonPreferences)", register_source)
        self.assertIn("_safe_unregister_class(BlenderSyncAddonPreferences)", unregister_source)
        self.assertNotIn("bpy.types.Scene.blendersync_session_port", register_source)

        preferences = SimpleNamespace(session_port=49152)
        context = SimpleNamespace(
            preferences=SimpleNamespace(
                addons={"blendersync_vnext": SimpleNamespace(preferences=preferences)}
            )
        )
        self.assertIs(preferences, settings.get_addon_preferences(context))
        self.assertEqual(49152, settings.get_session_port(context))

    def test_verbose_logging_is_a_user_preference_with_a_safe_default(self) -> None:
        annotations = getattr(settings.BlenderSyncAddonPreferences, "__annotations__", {})
        if settings.bpy is not None:
            self.assertIn("verbose_logging", annotations)
        context = SimpleNamespace(preferences=SimpleNamespace(addons={}))
        self.assertFalse(settings.get_verbose_logging(context))
        preferences = SimpleNamespace(verbose_logging=True)
        context.preferences.addons["blendersync_vnext"] = SimpleNamespace(preferences=preferences)
        self.assertTrue(settings.get_verbose_logging(context))
        developer_source = inspect.getsource(session_panel._draw_developer_diagnostics)
        self.assertIn('preferences, "verbose_logging"', developer_source)

    def test_performance_tuning_uses_addon_preferences_and_ignores_scene_values(self) -> None:
        preference_names = {
            "object_state_sync_hz",
            "mesh_update_sync_hz",
            "evaluated_mesh_preview_debounce_ms",
            "preview_idle_commit_seconds",
            "lifecycle_reconcile_hz",
            "view_sync_hz",
            "view_sync_scale",
        }
        annotations = getattr(settings.BlenderSyncAddonPreferences, "__annotations__", {})
        register_source = inspect.getsource(settings.register_settings)
        performance_source = inspect.getsource(session_panel._draw_sync_tuning)
        if settings.bpy is not None:
            self.assertTrue(preference_names.issubset(annotations))
        for name in preference_names:
            self.assertNotIn(f"bpy.types.Scene.blendersync_{name}", register_source)
            self.assertIn(f'preferences, "{name}"', performance_source)

        preferences = SimpleNamespace(
            object_state_sync_hz=24.0,
            mesh_update_sync_hz=12.0,
            evaluated_mesh_preview_debounce_ms=425,
            preview_idle_commit_seconds=8.5,
            lifecycle_reconcile_hz=0.75,
            view_sync_hz=7.0,
            view_sync_scale=1.4,
        )
        context = SimpleNamespace(
            preferences=SimpleNamespace(
                addons={"blendersync_vnext": SimpleNamespace(preferences=preferences)}
            ),
            scene=SimpleNamespace(
                blendersync_object_state_sync_hz=99.0,
                blendersync_mesh_update_sync_hz=99.0,
                blendersync_evaluated_mesh_preview_debounce_ms=999,
                blendersync_preview_idle_commit_seconds=59.0,
                blendersync_lifecycle_reconcile_hz=4.0,
                blendersync_view_sync_hz=29.0,
                blendersync_view_sync_scale=4.5,
            ),
        )
        self.assertEqual(24.0, settings.get_object_state_sync_hz(context))
        self.assertEqual(12.0, settings.get_mesh_update_sync_hz(context))
        self.assertEqual(425, settings.get_evaluated_mesh_preview_debounce_ms(context))
        self.assertEqual(8.5, settings.get_preview_idle_commit_seconds(context))
        self.assertEqual(0.75, settings.get_lifecycle_reconcile_hz(context))
        self.assertEqual(7.0, settings.get_view_sync_hz(context))
        self.assertEqual(1.4, settings.get_view_sync_scale(context))

    def test_performance_tuning_defaults_when_preferences_are_unavailable(self) -> None:
        context = SimpleNamespace(preferences=SimpleNamespace(addons={}))
        self.assertEqual(settings.DEFAULT_OBJECT_STATE_SYNC_HZ, settings.get_object_state_sync_hz(context))
        self.assertEqual(settings.DEFAULT_MESH_UPDATE_SYNC_HZ, settings.get_mesh_update_sync_hz(context))
        self.assertEqual(
            settings.DEFAULT_EVALUATED_MESH_PREVIEW_DEBOUNCE_MS,
            settings.get_evaluated_mesh_preview_debounce_ms(context),
        )
        self.assertEqual(
            settings.DEFAULT_PREVIEW_IDLE_COMMIT_SECONDS,
            settings.get_preview_idle_commit_seconds(context),
        )
        self.assertEqual(settings.DEFAULT_LIFECYCLE_RECONCILE_HZ, settings.get_lifecycle_reconcile_hz(context))
        self.assertEqual(settings.DEFAULT_VIEW_SYNC_HZ, settings.get_view_sync_hz(context))
        self.assertEqual(settings.DEFAULT_VIEW_SYNC_SCALE, settings.get_view_sync_scale(context))

    def test_performance_tuning_reset_restores_only_the_seven_tuning_defaults(self) -> None:
        preferences = SimpleNamespace(
            session_port=49152,
            object_state_sync_hz=24.0,
            mesh_update_sync_hz=12.0,
            evaluated_mesh_preview_debounce_ms=425,
            preview_idle_commit_seconds=8.5,
            lifecycle_reconcile_hz=0.75,
            view_sync_hz=7.0,
            view_sync_scale=1.4,
        )
        context = SimpleNamespace(
            preferences=SimpleNamespace(
                addons={"blendersync_vnext": SimpleNamespace(preferences=preferences)}
            )
        )

        self.assertTrue(operators.BS_OT_ResetPerformanceTuning.poll(context))
        self.assertEqual({"FINISHED"}, operators.BS_OT_ResetPerformanceTuning().execute(context))
        self.assertEqual(49152, preferences.session_port)
        for name, default in settings.PERFORMANCE_TUNING_DEFAULTS:
            self.assertEqual(default, getattr(preferences, name))

        unavailable = SimpleNamespace(preferences=SimpleNamespace(addons={}))
        self.assertFalse(operators.BS_OT_ResetPerformanceTuning.poll(unavailable))

    def test_performance_tuning_draws_a_reset_button(self) -> None:
        performance_source = inspect.getsource(session_panel._draw_sync_tuning)
        self.assertIn('"blendersync.reset_performance_tuning"', performance_source)
        self.assertIn('text="Reset to Defaults"', performance_source)
        self.assertIn('icon="LOOP_BACK"', performance_source)
        self.assertIn(operators.BS_OT_ResetPerformanceTuning, operators.CLASSES)

    def test_expected_connection_errors_are_hidden_but_actionable_errors_remain(self) -> None:
        self.assertEqual(
            "",
            session_panel._session_error(
                {
                    "connected": False,
                    "transport_connected": False,
                    "last_error": "session_not_connected",
                }
            ),
        )
        self.assertEqual(
            "",
            session_panel._session_error(
                {
                    "connected": False,
                    "transport_connected": True,
                    "last_error": "handshake_not_confirmed",
                }
            ),
        )
        self.assertEqual(
            "",
            session_panel._session_error(
                {
                    "connected": False,
                    "transport_connected": False,
                    "last_error": "handshake_timeout",
                }
            ),
        )
        self.assertEqual(
            "ws_server_start_failed:port_in_use",
            session_panel._session_error(
                {
                    "connected": False,
                    "transport_connected": False,
                    "last_error": "ws_server_start_failed:port_in_use",
                }
            ),
        )

    def test_diagnostics_session_summary_only_surfaces_relevant_handshake_state(self) -> None:
        self.assertEqual(
            {
                "status": "Disconnected",
                "status_icon": "STRIP_COLOR_09",
                "protocol": "-",
                "handshake": "",
                "error": "",
            },
            session_panel._session_diagnostics_presentation({}),
        )
        self.assertEqual(
            {
                "status": "Handshaking",
                "status_icon": "STRIP_COLOR_03",
                "protocol": "Negotiating",
                "handshake": "awaiting_ack",
                "error": "",
            },
            session_panel._session_diagnostics_presentation(
                {
                    "transport_connected": True,
                    "handshake_phase": "awaiting_ack",
                    "last_error": "handshake_not_confirmed",
                }
            ),
            )
        self.assertEqual(
            {
                "status": "Waiting for Unity",
                "status_icon": "STRIP_COLOR_03",
                "protocol": "-",
                "handshake": "local_ready",
                "error": "",
            },
            session_panel._session_diagnostics_presentation(
                {
                    "local_ready": True,
                    "handshake_phase": "local_ready",
                }
            ),
        )
        connected = session_panel._session_diagnostics_presentation(
            {"connected": True, "transport_connected": True, "peer_protocol_version": 1}
        )
        self.assertEqual("Connected", connected["status"])
        self.assertEqual("v1", connected["protocol"])
        self.assertEqual("", connected["handshake"])

    def test_new_connection_clears_stale_session_and_websocket_errors(self) -> None:
        session = state_view.get_session()
        previous_session_error = session.get_last_error()
        previous_ws_error = state_view._LAST_WS_HANDOFF_ERROR
        try:
            session.set_last_error("handshake_timeout")
            state_view.set_last_ws_handoff_error("handshake_timeout")
            state_view.clear_session_connection_errors()
            self.assertIsNone(session.get_last_error())
            self.assertIsNone(state_view._LAST_WS_HANDOFF_ERROR)
        finally:
            session.set_last_error(previous_session_error)
            state_view.set_last_ws_handoff_error(previous_ws_error)

    def test_new_websocket_transport_clears_the_previous_timeout(self) -> None:
        runtime = MinimalWsClientHook("ws://127.0.0.1:8765/ws/")
        runtime._last_error = "handshake_timeout"
        fake_session = mock.Mock()
        websocket = object()
        previous_ws_error = state_view._LAST_WS_HANDOFF_ERROR
        try:
            state_view.set_last_ws_handoff_error("handshake_timeout")
            with mock.patch.object(state_view, "get_session", return_value=fake_session):
                runtime._handler(websocket)

            self.assertIsNone(runtime.last_error)
            self.assertIsNone(state_view._LAST_WS_HANDOFF_ERROR)
            fake_session.set_last_error.assert_called_with(None)
            self.assertEqual(
                [mock.call(True), mock.call(False)],
                fake_session.set_transport_connected.call_args_list,
            )
        finally:
            state_view.set_last_ws_handoff_error(previous_ws_error)

    def test_active_object_diagnostics_aggregate_baselines(self) -> None:
        not_imported = session_panel._active_object_diagnostics_presentation(
            {
                "hasActiveObject": True,
                "objectName": "Cube",
                "objectType": "MESH",
                "autoSyncReady": False,
                "meshSource": "original",
                "modifierBaseline": "missing",
                "materialSlotsBaseline": "missing",
                "uvChannelsBaseline": "missing",
                "colorAttributesBaseline": "missing",
            }
        )
        self.assertEqual("Not captured", not_imported["baseline_summary"])
        self.assertEqual([], not_imported["baseline_issues"])

        imported = session_panel._active_object_diagnostics_presentation(
            {
                "hasActiveObject": True,
                "objectName": "Cube",
                "objectType": "MESH",
                "autoSyncReady": True,
                "meshSource": "evaluated",
                "modifierBaseline": "clean",
                "materialSlotsBaseline": "dirty",
                "uvChannelsBaseline": "clean",
                "colorAttributesBaseline": "missing",
            }
        )
        self.assertEqual("2 / 4 ready", imported["baseline_summary"])
        self.assertEqual(
            [("Material Slots", "dirty"), ("Color Attributes", "missing")],
            imported["baseline_issues"],
        )

    def test_diagnostics_are_progressively_disclosed_without_debug_narration(self) -> None:
        panel_source = inspect.getsource(session_panel)
        diagnostics_source = inspect.getsource(session_panel._draw_diagnostics_tab)
        performance_source = inspect.getsource(session_panel._draw_sync_tuning)
        developer_source = inspect.getsource(session_panel._draw_developer_diagnostics)
        settings_source = inspect.getsource(settings.register_settings)

        self.assertNotIn("Debug UI Refresh", panel_source)
        self.assertNotIn("Idle is signature-gated", panel_source)
        self.assertNotIn("Trigger Monitor", panel_source)
        self.assertNotIn("trigger_monitor", inspect.getsource(controller))
        self.assertIn("copy_diagnostics", diagnostics_source)
        self.assertIn('text="Edit Preview Max Hz"', performance_source)
        self.assertIn('text="Preview Send Delay ms"', performance_source)
        self.assertNotIn('text="Preview Hz"', performance_source)
        self.assertNotIn('text="Mesh Preview ms"', performance_source)
        self.assertIn("_draw_active_object_diagnostics", diagnostics_source)
        self.assertIn("_draw_runtime_diagnostics", diagnostics_source)
        self.assertIn("_draw_developer_diagnostics", diagnostics_source)
        self.assertIn("blendersync_show_diagnostics_active_object", settings_source)
        self.assertIn("blendersync_show_diagnostics_runtime", settings_source)
        self.assertIn("blendersync_show_diagnostics_developer", settings_source)
        self.assertIn("blendersync_show_diagnostics_recent_activity", settings_source)
        self.assertNotIn("blendersync_show_diagnostics_last_operation", settings_source)
        recent_activity_start = settings_source.index("blendersync_show_diagnostics_recent_activity")
        recent_activity_end = settings_source.index("blendersync_show_diagnostics_active_object")
        recent_activity_setting = settings_source[recent_activity_start:recent_activity_end]
        self.assertIn("default=False", recent_activity_setting)
        self.assertIn("_draw_recent_activity", diagnostics_source)
        self.assertNotIn("_draw_last_send_report", diagnostics_source)
        self.assertIn('text="Verbose Logging"', developer_source)
        self.assertEqual(
            "Import / Repair",
            session_panel._last_operation_label(
                {"operation_meta": {"updateIntent": "import_objects"}}
            ),
        )

    def test_recent_activity_presentation_handles_missing_time_and_error_level(self) -> None:
        presentation = session_panel._activity_entry_presentation(
            {
                "level": "error",
                "category": "Session",
                "event": "failed",
                "summary": "connection lost",
            }
        )
        self.assertEqual("--:--:--  [ERROR] Session / failed", presentation["header"])
        self.assertEqual("ERROR", presentation["icon"])
        self.assertEqual("connection lost", presentation["summary"])

    def test_recent_activity_merges_operation_reports_and_logs(self) -> None:
        state_view.clear_diagnostics_activity()
        self.addCleanup(state_view.clear_diagnostics_activity)
        with mock.patch.object(state_view.time, "time", side_effect=(10.0, 11.0)):
            state_view.set_last_send_result(
                SendResult(ok=True, message="import_selected_ok", last_resource_count=2),
                {"updateIntent": "import_objects", "selectedObjectCount": 1},
            )
            sync_log.warn("Mesh", "degraded", "secondary UV fallback")

        entries = session_panel._recent_activity_entries(5)

        self.assertEqual(["operation", "log"], [entry["kind"] for entry in entries])
        operation = entries[0]["value"]
        self.assertEqual("Import / Repair", session_panel._last_operation_label(operation))
        self.assertEqual(
            "import_selected_ok",
            session_panel._operation_activity_presentation(operation)["summary"],
        )
        draw_source = inspect.getsource(session_panel._draw_recent_activity)
        self.assertIn('text=iface("Clear")', draw_source)
        self.assertNotIn('icon="X"', draw_source)
        self.assertIn('getattr(scene, "blendersync_show_diagnostics_recent_activity", False)', draw_source)
        self.assertIn('icon="TRIA_DOWN" if expanded else "TRIA_RIGHT"', draw_source)

    def test_operation_report_history_is_bounded_and_returns_copies(self) -> None:
        state_view.clear_diagnostics_activity()
        self.addCleanup(state_view.clear_diagnostics_activity)
        for index in range(505):
            state_view.set_last_send_result(
                SendResult(ok=True, message=f"operation-{index}"),
                {"selectedObjectCount": index},
            )

        reports = state_view.get_recent_operation_reports()

        self.assertEqual(500, len(reports))
        self.assertEqual("operation-5", reports[0]["last_send_message"])
        reports[-1]["operation_meta"]["selectedObjectCount"] = -1
        self.assertEqual(
            504,
            state_view.get_recent_operation_reports(1)[0]["operation_meta"]["selectedObjectCount"],
        )

    def test_recent_activity_entries_expand_independently(self) -> None:
        session_panel.clear_recent_activity_expansion()
        self.addCleanup(session_panel.clear_recent_activity_expansion)
        toggle = operators.BS_OT_ToggleRecentActivityEntry()
        toggle.entry_id = "operation:7"

        self.assertEqual({"FINISHED"}, toggle.execute(SimpleNamespace()))
        self.assertIn("operation:7", session_panel._EXPANDED_ACTIVITY_ENTRY_IDS)
        self.assertEqual({"FINISHED"}, toggle.execute(SimpleNamespace()))
        self.assertNotIn("operation:7", session_panel._EXPANDED_ACTIVITY_ENTRY_IDS)
        self.assertIn(operators.BS_OT_ToggleRecentActivityEntry, operators.CLASSES)

    def test_copy_diagnostics_operator_writes_sanitized_snapshot_to_clipboard(self) -> None:
        self.assertEqual("Copy Diagnostics", operators.BS_OT_CopyDiagnostics.bl_label)
        context = SimpleNamespace(window_manager=SimpleNamespace(clipboard=None))
        with mock.patch.object(
            operators,
            "get_copy_diagnostics_json",
            return_value='{"session": {}}',
        ):
            self.assertEqual({"FINISHED"}, operators.BS_OT_CopyDiagnostics().execute(context))
        self.assertEqual('{"session": {}}', context.window_manager.clipboard)

    def test_copy_diagnostics_snapshot_omits_payloads_paths_and_handshake_id(self) -> None:
        sync_log.clear()
        self.addCleanup(sync_log.clear)
        sync_log.info(
            "Import",
            "failed",
            "read C:\\Users\\alice\\My Project\\mesh.json",
            {"path": "/home/alice/mesh.json", "payload": "secret"},
        )
        session_state = {
            "local_ready": True,
            "connect_attempted": True,
            "transport_connected": True,
            "counterpart_observed": True,
            "connected": True,
            "handshake_phase": "confirmed",
            "handshake_id": "hs-secret",
            "peer_protocol_version": 1,
            "legacy_protocol": False,
            "negotiated_features": ["large_payload_crc32_v1"],
            "endpoint": "ws://127.0.0.1:8765/ws/",
            "last_error": "failed C:\\Users\\alice\\My Project\\secret.json",
            "last_ws_handoff_error": None,
        }
        report = {
            "last_send_ok": False,
            "last_send_message": "payload failed",
            "last_send_error": "read /home/alice/mesh.json failed",
            "last_payload_size": 123,
            "last_resource_count": 2,
            "failure_category": "transport",
            "operation_meta": {
                "updateIntent": "import_objects",
                "selectedObjectCount": 1,
                "payload": {"vertices": [1, 2, 3]},
            },
        }
        with (
            mock.patch.object(state_view, "get_session_view_state", return_value=session_state),
            mock.patch.object(state_view, "get_asset_bridge_view_state", return_value=report),
            mock.patch.object(state_view, "bpy", SimpleNamespace(app=SimpleNamespace(version=(5, 0, 1)))),
            mock.patch.object(
                controller,
                "get_controller_state",
                return_value={
                    "timer_registered": True,
                    "sync_enabled": True,
                    "object_dirty_queue_count": 2,
                    "active_object_baseline": {
                        "hasActiveObject": True,
                        "objectName": "Cube",
                        "objectType": "MESH",
                        "autoSyncReady": True,
                        "meshSource": "original",
                        "ignoredPayload": {"vertices": [4, 5, 6]},
                    },
                },
            ),
        ):
            snapshot = state_view.get_copy_diagnostics_snapshot()
            encoded = state_view.get_copy_diagnostics_json()

        self.assertEqual("blendersync-diagnostics-v2", snapshot["schemaVersion"])
        self.assertNotIn("handshake_id", encoded)
        self.assertNotIn("hs-secret", encoded)
        self.assertNotIn("vertices", encoded)
        self.assertNotIn("C:\\Users\\alice", encoded)
        self.assertNotIn("My Project", encoded)
        self.assertNotIn("/home/alice", encoded)
        self.assertIn("<path>", encoded)
        self.assertEqual(True, snapshot["runtime"]["timer_registered"])
        self.assertEqual(2, snapshot["runtime"]["object_dirty_queue_count"])
        self.assertEqual("Cube", snapshot["activeObject"]["objectName"])
        self.assertNotIn("ignoredPayload", encoded)
        self.assertEqual(1, len(snapshot["recentLogs"]))
        self.assertEqual("Import", snapshot["recentLogs"][0]["category"])
        self.assertEqual([{"key": "path", "value": "<path>"}], snapshot["recentLogs"][0]["fields"])
        self.assertNotIn("secret", encoded)

    def test_recent_activity_clear_removes_operation_reports_and_logs(self) -> None:
        state_view.clear_diagnostics_activity()
        self.addCleanup(state_view.clear_diagnostics_activity)
        state_view.set_last_send_result(
            SendResult(ok=True, message="sync_ok"),
            {"updateIntent": "object_state"},
        )
        sync_log.info("Session", "connected")
        self.assertEqual(1, len(sync_log.get_recent_entries()))
        self.assertEqual(1, len(state_view.get_recent_operation_reports()))
        self.assertEqual({"FINISHED"}, operators.BS_OT_ClearRecentActivity().execute(SimpleNamespace()))
        self.assertEqual([], sync_log.get_recent_entries())
        self.assertEqual([], state_view.get_recent_operation_reports())
        self.assertIsNone(state_view.get_asset_bridge_view_state()["last_send_ok"])
        self.assertIn(operators.BS_OT_ClearRecentActivity, operators.CLASSES)

    def test_copy_diagnostics_limits_recent_logs_to_latest_fifty(self) -> None:
        sync_log.clear()
        self.addCleanup(sync_log.clear)
        for index in range(55):
            sync_log.info("Loop", f"event-{index}")

        logs = state_view._recent_logs_for_copy()

        self.assertEqual(50, len(logs))
        self.assertEqual("event-5", logs[0]["eventName"])
        self.assertEqual("event-54", logs[-1]["eventName"])

    def test_legacy_sync_tab_is_stably_numbered_hidden_and_normalized_to_objects(self) -> None:
        numbers = {item[0]: item[4] for item in settings.UI_TAB_ITEMS}
        self.assertEqual(
            {
                "objects": 0,
                "materials": 1,
                "rig_pose": 2,
                "animation": 3,
                "sync": 4,
                "diagnostics": 5,
            },
            numbers,
        )
        self.assertNotIn("sync", session_panel._VISIBLE_WORKFLOW_TAB_IDS)
        self.assertEqual("objects", session_panel._normalize_ui_tab("sync"))
        self.assertEqual("objects", session_panel._normalize_ui_tab("unknown"))
        self.assertEqual("diagnostics", session_panel._normalize_ui_tab("diagnostics"))

    def test_commit_preview_poll_requires_handshake_and_selected_ready_mesh(self) -> None:
        ready_mesh = FakeObject(ready=True)
        unready_mesh = FakeObject(ready=False)
        ready_curve = FakeObject("CURVE", ready=True)

        def poll(connected: bool, selected: list) -> bool:
            session = SimpleNamespace(
                get_truth_state=mock.Mock(return_value={"handshake_confirmed": connected})
            )
            context = SimpleNamespace(selected_objects=selected)
            with mock.patch.object(operators, "get_session", return_value=session):
                return operators.BS_OT_ManualCommitSelectedPreviews.poll(context)

        self.assertFalse(poll(False, [ready_mesh]))
        self.assertFalse(poll(True, []))
        self.assertFalse(poll(True, [unready_mesh, ready_curve]))
        self.assertTrue(poll(True, [ready_mesh]))

    def test_import_and_update_polls_require_connection_and_suitable_selection(self) -> None:
        importable = FakeObject(ready=False)
        imported = FakeObject(ready=True)
        unsupported = FakeObject("CURVE")
        imported_rig = FakeObject("ARMATURE", ready=True)
        rigged_part = FakeObject(ready=False, armature=imported_rig)

        def poll(operator, *, connected: bool, selected: list, mode: str = "OBJECT") -> bool:
            session = SimpleNamespace(
                get_truth_state=mock.Mock(return_value={"handshake_confirmed": connected})
            )
            active = selected[0] if selected else None
            context = SimpleNamespace(
                mode=mode,
                selected_objects=selected,
                active_object=active,
                object=active,
            )
            with mock.patch.object(operators, "get_session", return_value=session):
                return operator.poll(context)

        import_operator = operators.BS_OT_SendSelectedResources
        update_operator = operators.BS_OT_SyncSelectedObjects
        state_operator = operators.BS_OT_SyncSelectedObjectState

        self.assertFalse(poll(import_operator, connected=False, selected=[importable]))
        self.assertFalse(poll(import_operator, connected=True, selected=[]))
        self.assertFalse(poll(import_operator, connected=True, selected=[unsupported]))
        self.assertTrue(poll(import_operator, connected=True, selected=[importable]))

        self.assertFalse(poll(update_operator, connected=True, selected=[]))
        self.assertFalse(poll(update_operator, connected=True, selected=[importable]))
        self.assertTrue(poll(update_operator, connected=True, selected=[imported]))
        self.assertTrue(poll(update_operator, connected=True, selected=[rigged_part]))
        self.assertFalse(poll(update_operator, connected=True, selected=[imported], mode="EDIT_MESH"))

        self.assertFalse(poll(state_operator, connected=True, selected=[importable]))
        self.assertTrue(poll(state_operator, connected=True, selected=[imported]))
        self.assertFalse(poll(state_operator, connected=True, selected=[rigged_part]))

    def test_manual_action_preconditions_warn_without_starting_bulk_job(self) -> None:
        unimported = FakeObject(ready=False)
        context = SimpleNamespace(
            mode="OBJECT",
            selected_objects=[unimported],
            active_object=unimported,
            object=unimported,
        )
        disconnected = SimpleNamespace(
            get_truth_state=mock.Mock(return_value={"handshake_confirmed": False})
        )
        connected = SimpleNamespace(
            get_truth_state=mock.Mock(return_value={"handshake_confirmed": True})
        )

        import_operator = operators.BS_OT_SendSelectedResources()
        import_operator.report = mock.Mock()
        with (
            mock.patch.object(operators, "get_session", return_value=disconnected),
            mock.patch.object(operators, "begin_job") as begin_mock,
        ):
            self.assertEqual({"CANCELLED"}, import_operator.execute(context))
        begin_mock.assert_not_called()
        self.assertEqual({"WARNING"}, import_operator.report.call_args.args[0])

        update_operator = operators.BS_OT_SyncSelectedObjects()
        update_operator.report = mock.Mock()
        with (
            mock.patch.object(operators, "bpy", object()),
            mock.patch.object(operators, "get_session", return_value=connected),
            mock.patch.object(operators, "begin_job") as begin_mock,
        ):
            self.assertEqual({"CANCELLED"}, update_operator.execute(context))
        begin_mock.assert_not_called()
        self.assertEqual({"WARNING"}, update_operator.report.call_args.args[0])

    def test_terminal_bulk_job_can_be_dismissed_but_running_job_cannot(self) -> None:
        bulk_job.dismiss_job()
        try:
            bulk_job.begin_job("test", 2, mode="bulk_pending")
            self.assertFalse(bulk_job.dismiss_job())
            bulk_job.finish_job(ok=False, processed_objects=1, message="failed")
            self.assertTrue(operators.BS_OT_DismissBulkJob.poll(None))
            self.assertIn("blendersync.dismiss_bulk_job", inspect.getsource(session_panel._draw_bulk_job_status))
            self.assertEqual({"FINISHED"}, operators.BS_OT_DismissBulkJob().execute(None))
            self.assertEqual("idle", bulk_job.get_state()["status"])
        finally:
            if bulk_job.get_state()["status"] in {"running", "cancel_requested"}:
                bulk_job.cancel_job()
            bulk_job.dismiss_job()

    def test_bulk_job_presentation_hides_internal_status_and_operation_ids(self) -> None:
        fast_failure = session_panel._bulk_job_presentation(
            {
                "status": "error",
                "mode": "fast_path",
                "operation": "import_selected_objects",
                "message": "pipeline_failed",
                "lastError": "handshake_not_confirmed",
            }
        )
        self.assertEqual("Import / Repair failed", fast_failure["title"])
        self.assertEqual("result", fast_failure["kind"])
        self.assertNotIn("pipeline_failed", repr(fast_failure))
        self.assertNotIn("import_selected_objects", repr(fast_failure))
        self.assertNotIn("fast_path", repr(fast_failure))

        running = session_panel._bulk_job_presentation(
            {
                "status": "running",
                "mode": "bulk_pending",
                "operation": "update_selected_active",
                "totalObjects": 20,
                "processedObjects": 5,
                "currentObject": "Cube",
            }
        )
        self.assertEqual("Updating objects", running["title"])
        self.assertAlmostEqual(0.25, running["progress"])
        self.assertEqual("5 / 20", running["progress_text"])
        self.assertTrue(running["show_cancel"])

        terminal = session_panel._bulk_job_presentation(
            {
                "status": "error",
                "mode": "bulk_pending",
                "operation": "update_object_state",
                "successCount": 8,
                "skippedCount": 1,
                "errorCount": 2,
            }
        )
        self.assertEqual("Update State failed", terminal["title"])
        self.assertEqual("Completed: 8 | Skipped: 1 | Failed: 2", terminal["summary"])
        self.assertTrue(terminal["show_dismiss"])

    def test_reuse_active_mesh_poll_requires_active_and_additional_mesh_data(self) -> None:
        self.assertEqual("Reuse Active Mesh Asset", operators.BS_OT_LinkSelectedToActiveMesh.bl_label)
        active = FakeObject()
        other_mesh = FakeObject()
        curve = FakeObject("CURVE")
        missing_data = FakeObject(has_data=False)

        def poll(active_object, selected: list) -> bool:
            context = SimpleNamespace(active_object=active_object, selected_objects=selected)
            return operators.BS_OT_LinkSelectedToActiveMesh.poll(context)

        self.assertFalse(poll(None, [other_mesh]))
        self.assertFalse(poll(curve, [curve, other_mesh]))
        self.assertFalse(poll(active, [active]))
        self.assertFalse(poll(active, [active, curve, missing_data]))
        self.assertTrue(poll(active, [active, other_mesh]))

    def test_object_tab_prioritizes_actions_and_hides_legacy_mesh_controls(self) -> None:
        panel_source = inspect.getsource(session_panel._draw_objects_tab)
        settings_source = inspect.getsource(settings.register_settings)
        update_offset = panel_source.index("blendersync.sync_selected_object_state")
        reuse_offset = panel_source.index("blendersync.link_selected_to_active_mesh")
        options_offset = panel_source.index("blendersync_show_object_advanced")
        self.assertLess(update_offset, reuse_offset)
        self.assertLess(reuse_offset, options_offset)
        self.assertNotIn("Object Settings", panel_source)
        self.assertNotIn("blendersync_mesh_source", panel_source)
        self.assertNotIn("blendersync_auto_rebuild_evaluated_mesh", panel_source)
        self.assertNotIn("blendersync_auto_rebuild_evaluated_mesh", settings_source)
        self.assertEqual(2, panel_source.count("scale_y = 1.2"))

    def test_material_panel_does_not_build_payload_during_draw(self) -> None:
        panel_source = inspect.getsource(session_panel)
        self.assertNotIn("build_material_content_v1", panel_source)
        self.assertNotIn("blendersync.build_active_material_content_v1", panel_source)
        self.assertEqual({"INTERNAL"}, operators.BS_OT_BuildActiveMaterialContentV1.bl_options)

    def test_material_sync_is_not_registered_in_the_properties_editor(self) -> None:
        self.assertFalse(hasattr(session_panel, "BS_PT_MaterialPropertiesSyncPanel"))
        self.assertIn(session_panel.BS_PT_MaterialSyncPanel, session_panel.CLASSES)
        panel_source = inspect.getsource(session_panel)
        self.assertNotIn('bl_space_type = "PROPERTIES"', panel_source)
        self.assertNotIn('bl_context = "material"', panel_source)

    def test_material_summary_is_read_only_and_counts_unique_grouped_images(self) -> None:
        image_a = object()
        image_b = object()
        nested_tree = SimpleNamespace(
            nodes=[SimpleNamespace(type="TEX_IMAGE", bl_idname="", image=image_a, node_tree=None)]
        )
        root_tree = SimpleNamespace(
            nodes=[
                SimpleNamespace(type="TEX_IMAGE", bl_idname="", image=image_a, node_tree=None),
                SimpleNamespace(type="", bl_idname="ShaderNodeTexImage", image=image_b, node_tree=None),
                SimpleNamespace(type="GROUP", bl_idname="", image=None, node_tree=nested_tree),
            ]
        )
        material = SimpleNamespace(use_nodes=True, node_tree=root_tree)

        with mock.patch.object(session_panel, "find_principled", return_value=object()):
            self.assertEqual(
                {"shader": "Principled BSDF", "texture_count": 2},
                session_panel._material_panel_summary(material),
            )
        with mock.patch.object(session_panel, "find_principled", return_value=None):
            self.assertEqual("Fallback Surface", session_panel._material_panel_summary(material)["shader"])
        self.assertEqual(
            {"shader": "Nodes Disabled", "texture_count": 0},
            session_panel._material_panel_summary(SimpleNamespace(use_nodes=False, node_tree=None)),
        )

    def test_send_material_poll_requires_material_and_confirmed_handshake(self) -> None:
        self.assertEqual("Sync Material to Unity", operators.BS_OT_SendActiveMaterialContentV1.bl_label)
        material = SimpleNamespace(use_nodes=True)

        def poll(*, connected: bool, active_material) -> bool:
            session = SimpleNamespace(
                get_truth_state=mock.Mock(return_value={"handshake_confirmed": connected})
            )
            active = SimpleNamespace(active_material=active_material)
            context = SimpleNamespace(space_data=None, material=None, active_object=active)
            with mock.patch.object(operators, "get_session", return_value=session):
                return operators.BS_OT_SendActiveMaterialContentV1.poll(context)

        self.assertFalse(poll(connected=False, active_material=material))
        self.assertFalse(poll(connected=True, active_material=None))
        self.assertTrue(poll(connected=True, active_material=material))

    def test_rig_pose_polls_apply_distinct_mode_and_target_rules(self) -> None:
        armature = FakeObject("ARMATURE", ready=True, name="Rig")
        bound_mesh = FakeObject("MESH", ready=True, armature=armature)
        unimported = FakeObject("ARMATURE", ready=False)

        def poll(operator, *, connected: bool, mode: str, active) -> bool:
            session = SimpleNamespace(
                get_truth_state=mock.Mock(return_value={"handshake_confirmed": connected})
            )
            context = SimpleNamespace(mode=mode, active_object=active)
            with mock.patch.object(operators, "get_session", return_value=session):
                return operator.poll(context)

        sync = operators.BS_OT_SyncCurrentRiggedPose
        sync_shapes = operators.BS_OT_SyncRiggedBlendShapeWeights
        restore = operators.BS_OT_RestoreStaticRiggedPose
        self.assertEqual("Sync Pose to Unity", sync.bl_label)
        self.assertEqual("Sync Shape Keys to Unity", sync_shapes.bl_label)
        self.assertEqual("Restore Imported Pose", restore.bl_label)
        self.assertTrue(poll(sync, connected=True, mode="POSE", active=armature))
        self.assertFalse(poll(sync, connected=True, mode="OBJECT", active=armature))
        self.assertFalse(poll(sync, connected=True, mode="POSE", active=bound_mesh))
        self.assertFalse(poll(sync, connected=True, mode="POSE", active=unimported))
        self.assertFalse(poll(sync, connected=False, mode="POSE", active=armature))
        self.assertTrue(poll(sync_shapes, connected=True, mode="POSE", active=armature))
        self.assertTrue(poll(sync_shapes, connected=True, mode="OBJECT", active=armature))
        self.assertTrue(poll(sync_shapes, connected=True, mode="OBJECT", active=bound_mesh))
        self.assertFalse(poll(sync_shapes, connected=True, mode="EDIT_MESH", active=bound_mesh))
        self.assertFalse(poll(sync_shapes, connected=True, mode="OBJECT", active=unimported))
        self.assertFalse(poll(sync_shapes, connected=False, mode="OBJECT", active=armature))
        self.assertTrue(poll(restore, connected=True, mode="POSE", active=armature))
        self.assertTrue(poll(restore, connected=True, mode="OBJECT", active=armature))
        self.assertTrue(poll(restore, connected=True, mode="OBJECT", active=bound_mesh))
        self.assertFalse(poll(restore, connected=True, mode="EDIT_MESH", active=bound_mesh))

    def test_rig_panel_resolves_armature_or_bound_mesh_without_writing_state(self) -> None:
        armature = FakeObject("ARMATURE", ready=True, name="Rig")
        bound_mesh = FakeObject("MESH", armature=armature)
        self.assertIs(
            armature,
            session_panel._resolve_active_rig_for_panel(SimpleNamespace(active_object=armature)),
        )
        self.assertIs(
            armature,
            session_panel._resolve_active_rig_for_panel(SimpleNamespace(active_object=bound_mesh)),
        )
        self.assertIsNone(
            session_panel._resolve_active_rig_for_panel(
                SimpleNamespace(active_object=FakeObject("MESH"))
            )
        )

    def test_pose_mode_execute_builds_current_pose_from_active_armature(self) -> None:
        armature = FakeObject("ARMATURE", ready=True, name="Rig")
        context = SimpleNamespace(
            mode="POSE",
            active_object=armature,
            selected_objects=[armature],
        )
        session = SimpleNamespace(
            get_truth_state=mock.Mock(return_value={"handshake_confirmed": True})
        )
        payload = {"type": "asset_bridge.rigged_pose_v1", "bones": [{"boneId": "bone-root"}]}
        result = SimpleNamespace(ok=True, error=None)
        operator = operators.BS_OT_SyncCurrentRiggedPose()

        with (
            mock.patch.object(operators, "bpy", object()),
            mock.patch.object(operators, "get_session", return_value=session),
            mock.patch.object(
                operators,
                "build_unity_rig_v1_pose_sync_payload",
                return_value=payload,
            ) as build_payload,
            mock.patch.object(operators, "send_rigged_pose_sync", return_value=result),
            mock.patch.object(operators, "_set_last_result"),
        ):
            self.assertEqual({"FINISHED"}, operator.execute(context))

        build_payload.assert_called_once_with(context, armature, mode="current_pose")

    def test_animation_export_ui_hides_empty_report_and_uses_export_semantics(self) -> None:
        self.assertEqual("", session_panel._animation_export_report_text(""))
        self.assertEqual(
            "",
            session_panel._animation_export_report_text("No animation clip exported yet."),
        )
        self.assertEqual(
            "Clip: Walk\nTracks: 12",
            session_panel._animation_export_report_text("  Clip: Walk\nTracks: 12  "),
        )
        self.assertEqual(
            "Export Animation Clip to Unity",
            operators.BS_OT_ImportAnimationClipFbxLikeV0.bl_label,
        )
        panel_source = inspect.getsource(session_panel._draw_animation_panel)
        self.assertIn('text="Clip Name"', panel_source)
        self.assertNotIn('text="Name Override"', panel_source)
        self.assertIn('"blendersync_animation_clip_loop"', panel_source)
        self.assertNotIn('"blendersync_animation_clip_loop_hint"', panel_source)
        self.assertIn('text="Loop Animation"', panel_source)
        self.assertNotIn("toggle=True", panel_source)
        self.assertIn('text="Export to Unity"', panel_source)
        self.assertNotIn('text="Import Clip"', panel_source)
        self.assertNotIn("Raw clips. Root Motion is in Unity.", panel_source)
        self.assertNotIn("blendersync_animation_clip_quaternion_continuity", panel_source)
        self.assertIn('"blendersync_animation_clip_static_curves"', panel_source)
        settings_source = inspect.getsource(settings.register_settings)
        self.assertIn("blendersync_animation_clip_quaternion_continuity", settings_source)
        self.assertIn("default=True", settings_source)
        self.assertIn("blendersync_animation_clip_static_curves", settings_source)
        self.assertIn('default="off"', settings_source)

    def test_animation_loop_toggle_maps_legacy_hint_and_defaults_on(self) -> None:
        scene = SimpleNamespace(blendersync_animation_clip_loop_hint="loop")
        self.assertTrue(settings._get_animation_clip_loop(scene))
        settings._set_animation_clip_loop(scene, False)
        self.assertEqual("none", scene.blendersync_animation_clip_loop_hint)
        self.assertFalse(settings._get_animation_clip_loop(scene))
        settings._set_animation_clip_loop(scene, True)
        self.assertEqual("loop", scene.blendersync_animation_clip_loop_hint)

        settings_source = inspect.getsource(settings.register_settings)
        loop_hint_offset = settings_source.index("blendersync_animation_clip_loop_hint")
        loop_toggle_offset = settings_source.index("blendersync_animation_clip_loop = BoolProperty")
        self.assertLess(loop_hint_offset, loop_toggle_offset)
        self.assertIn('default="loop"', settings_source[loop_hint_offset:loop_toggle_offset])

    def test_animation_export_poll_requires_active_object_and_handshake(self) -> None:
        def poll(*, connected: bool, active) -> bool:
            session = SimpleNamespace(
                get_truth_state=mock.Mock(return_value={"handshake_confirmed": connected})
            )
            context = SimpleNamespace(active_object=active)
            with mock.patch.object(operators, "get_session", return_value=session):
                return operators.BS_OT_ImportAnimationClipFbxLikeV0.poll(context)

        self.assertFalse(poll(connected=False, active=FakeObject()))
        self.assertFalse(poll(connected=True, active=None))
        self.assertTrue(poll(connected=True, active=FakeObject()))


if __name__ == "__main__":
    unittest.main()
