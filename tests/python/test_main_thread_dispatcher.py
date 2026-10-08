from __future__ import annotations

import inspect
import sys
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock


ADDON_ROOT = Path(__file__).resolve().parents[2] / "blender_addon" / "blendersync_vnext"
sys.path.insert(0, str(ADDON_ROOT))

from blender.common import log as sync_log
from blender.session import main_thread_dispatcher as dispatcher
from blender.session.ws_client_adapter import MinimalWsClientHook
from blender.ui import state_view


class MainThreadDispatcherTests(unittest.TestCase):
    def setUp(self) -> None:
        sync_log.clear()
        sync_log.set_verbose_preference(False)
        sync_log.set_verbose_override(False)
        dispatcher.clear_pending_main_thread_messages()
        self.accepting_patch = mock.patch.object(dispatcher, "_accepting_messages", True)
        self.loading_patch = mock.patch.object(dispatcher, "_file_loading", False)
        self.epoch_patch = mock.patch.object(dispatcher, "_file_epoch", 0)
        self.accepting_patch.start()
        self.loading_patch.start()
        self.epoch_patch.start()

    def tearDown(self) -> None:
        dispatcher.clear_pending_main_thread_messages()
        self.epoch_patch.stop()
        self.loading_patch.stop()
        self.accepting_patch.stop()
        sync_log.clear()
        sync_log.set_verbose_preference(False)
        sync_log.set_verbose_override(None)

    def test_dispatcher_scope_excludes_handshake_messages(self) -> None:
        self.assertEqual(
            {
                "scene_sync.mesh_ref_usage_v1",
                "scene_sync.mesh_content_fingerprint_request_v1",
                "unity_mesh.import_v1",
                "unity_mesh.import_file_v1",
                "unity_mesh.import_binary_file_v1",
            },
            dispatcher.MAIN_THREAD_MESSAGE_TYPES,
        )
        self.assertFalse(dispatcher.is_main_thread_message_type("session_hello"))
        self.assertFalse(dispatcher.is_main_thread_message_type("session.handshake_ack"))

    def test_enqueue_rejects_invalid_and_unsupported_payloads(self) -> None:
        invalid = dispatcher.enqueue_main_thread_message(None)  # type: ignore[arg-type]
        unsupported = dispatcher.enqueue_main_thread_message({"type": "session_hello"})

        self.assertEqual("payload_not_dict", invalid["reason"])
        self.assertEqual("unsupported_main_thread_message_type", unsupported["reason"])
        self.assertEqual(0, dispatcher.pending_message_count())

    def test_enqueue_rejects_supported_messages_after_unregister(self) -> None:
        payload = {"type": "scene_sync.mesh_ref_usage_v1"}

        with mock.patch.object(dispatcher, "_accepting_messages", False):
            result = dispatcher.enqueue_main_thread_message(payload)

        self.assertFalse(result["ok"])
        self.assertEqual("main_thread_dispatcher_not_registered", result["reason"])
        self.assertEqual(0, dispatcher.pending_message_count())

    def test_queue_is_bounded_without_replacing_existing_messages(self) -> None:
        first = {"type": "scene_sync.mesh_ref_usage_v1", "sequence": 1}
        second = {"type": "scene_sync.mesh_ref_usage_v1", "sequence": 2}
        overflow = {"type": "scene_sync.mesh_ref_usage_v1", "sequence": 3}

        with mock.patch.object(dispatcher, "_MAX_PENDING_MESSAGES", 2):
            self.assertTrue(dispatcher.enqueue_main_thread_message(first)["ok"])
            self.assertTrue(dispatcher.enqueue_main_thread_message(second)["ok"])
            result = dispatcher.enqueue_main_thread_message(overflow)

        self.assertFalse(result["ok"])
        self.assertEqual("main_thread_queue_full", result["reason"])
        self.assertEqual(2, dispatcher.pending_message_count())

    def test_file_load_advances_epoch_clears_queue_and_rejects_messages(self) -> None:
        payload = {"type": "scene_sync.mesh_ref_usage_v1"}
        self.assertTrue(dispatcher.enqueue_main_thread_message(payload)["ok"])

        with mock.patch("builtins.print"):
            begin_result = dispatcher.begin_file_load()
        rejected = dispatcher.enqueue_main_thread_message(payload)

        self.assertEqual({"cleared": 1, "fileEpoch": 1}, begin_result)
        self.assertEqual("file_load_in_progress", rejected["reason"])
        self.assertEqual(0, dispatcher.pending_message_count())

        complete_result = dispatcher.complete_file_load()
        accepted = dispatcher.enqueue_main_thread_message(payload)
        self.assertEqual({"cleared": 0, "fileEpoch": 1}, complete_result)
        self.assertTrue(accepted["ok"])
        self.assertEqual(1, accepted["fileEpoch"])

    def test_pump_drops_an_entry_from_a_stale_file_epoch(self) -> None:
        payload = {"type": "scene_sync.mesh_ref_usage_v1"}
        with dispatcher._pending_lock:
            dispatcher._pending_messages.append((-1, payload["type"], payload))

        sync_log.set_verbose_override(True)
        with (
            mock.patch.object(dispatcher, "_dispatch_message") as dispatch_mock,
            mock.patch("builtins.print"),
        ):
            dispatcher._pump_main_thread_messages()

        dispatch_mock.assert_not_called()
        entries = sync_log.get_recent_entries()
        self.assertEqual(1, len(entries))
        self.assertEqual("stale_message_dropped", entries[0]["event"])
        self.assertEqual("stale_file_epoch", entries[0]["fields"]["reason"])

    def test_worker_handshake_timeout_uses_thread_safe_warning_facade(self) -> None:
        hook = MinimalWsClientHook("ws://127.0.0.1:8765/ws/")
        hook._running = True
        hook._first_client_observed = mock.Mock(wait=mock.Mock(return_value=False))
        session = SimpleNamespace(set_last_error=mock.Mock())

        with (
            mock.patch.object(state_view, "get_session", return_value=session),
            mock.patch.object(state_view, "set_last_ws_handoff_error"),
            mock.patch("builtins.print") as print_mock,
        ):
            worker = threading.Thread(target=hook._watch_first_client_timeout)
            worker.start()
            worker.join(timeout=2.0)

        self.assertFalse(worker.is_alive())
        entries = sync_log.get_recent_entries()
        self.assertEqual(1, len(entries))
        self.assertEqual("WARN", entries[0]["level"])
        self.assertEqual("unity_client_timeout", entries[0]["event"])
        print_mock.assert_not_called()

    def test_worker_enqueue_only_dispatches_when_main_thread_pumps(self) -> None:
        payload = {"type": "scene_sync.mesh_ref_usage_v1", "sequence": 1}
        dispatched_thread_ids: list[int] = []

        def record_dispatch(_message_type, _payload) -> None:
            dispatched_thread_ids.append(threading.get_ident())

        with mock.patch.object(dispatcher, "_dispatch_message", side_effect=record_dispatch) as dispatch_mock:
            worker = threading.Thread(target=dispatcher.enqueue_main_thread_message, args=(payload,))
            worker.start()
            worker.join(timeout=2.0)

            self.assertFalse(worker.is_alive())
            dispatch_mock.assert_not_called()
            dispatcher._pump_main_thread_messages()

        self.assertEqual([threading.get_ident()], dispatched_thread_ids)

    def test_dispatch_exception_is_reported_with_message_type_context(self) -> None:
        payload = {"type": "scene_sync.mesh_ref_usage_v1"}
        dispatcher.enqueue_main_thread_message(payload)

        with mock.patch.object(dispatcher, "_dispatch_message", side_effect=RuntimeError("dispatch failed")):
            with mock.patch.object(dispatcher, "report_boundary_exception") as report_mock:
                dispatcher._pump_main_thread_messages()

        report_mock.assert_called_once()
        self.assertEqual(
            "main_thread_dispatcher:scene_sync.mesh_ref_usage_v1",
            report_mock.call_args.args[0],
        )
        self.assertIn("[vNext][MeshRefUsage] ERROR", report_mock.call_args.kwargs["message"])

    def test_pump_preserves_fifo_order(self) -> None:
        payloads = [
            {"type": "scene_sync.mesh_ref_usage_v1", "sequence": 1},
            {"type": "scene_sync.mesh_content_fingerprint_request_v1", "sequence": 2},
            {"type": "unity_mesh.import_file_v1", "sequence": 3},
        ]
        for payload in payloads:
            dispatcher.enqueue_main_thread_message(payload)

        observed: list[int] = []
        with mock.patch.object(
            dispatcher,
            "_dispatch_message",
            side_effect=lambda _message_type, payload: observed.append(payload["sequence"]),
        ):
            for _ in payloads:
                dispatcher._pump_main_thread_messages()

        self.assertEqual([1, 2, 3], observed)
        self.assertEqual(0, dispatcher.pending_message_count())

    def test_registration_uses_a_persistent_main_thread_timer(self) -> None:
        class FakeTimers:
            def __init__(self) -> None:
                self.callback = None
                self.register_thread_id = None
                self.persistent = None

            def is_registered(self, callback) -> bool:
                return self.callback is callback

            def register(self, callback, *, first_interval, persistent) -> None:
                self.callback = callback
                self.register_thread_id = threading.get_ident()
                self.persistent = persistent

            def unregister(self, callback) -> None:
                if self.callback is callback:
                    self.callback = None

        timers = FakeTimers()
        fake_bpy = SimpleNamespace(app=SimpleNamespace(timers=timers))
        with mock.patch.object(dispatcher, "bpy", fake_bpy):
            dispatcher.register_main_thread_dispatcher()
            self.assertIs(timers.callback, dispatcher._pump_main_thread_messages)
            self.assertEqual(threading.get_ident(), timers.register_thread_id)
            self.assertTrue(timers.persistent)
            dispatcher.unregister_main_thread_dispatcher()

        self.assertIsNone(timers.callback)

    def test_ws_handler_contains_no_direct_bpy_handler_calls(self) -> None:
        source = inspect.getsource(MinimalWsClientHook._handler)

        self.assertIn("enqueue_main_thread_message", source)
        self.assertNotIn("apply_mesh_ref_usage", source)
        self.assertNotIn("apply_mesh_content_fingerprint_request", source)
        self.assertNotIn("import_unity_mesh_payload", source)
        self.assertNotIn("import_unity_mesh_file_payload", source)
        self.assertNotIn("import_unity_mesh_binary_file_payload", source)

    def test_unity_mesh_dispatch_reports_queue_result(self) -> None:
        payload = {
            "type": "unity_mesh.import_v1",
            "meshes": [{}],
            "warnings": ["multi-frame omitted"],
        }
        with mock.patch(
            "blender.unity_mesh_import.import_unity_mesh_payload",
            return_value={"ok": True, "queued": 1, "pending": 1},
        ):
            with mock.patch("blender.unity_mesh_import.send_import_result") as result_mock:
                dispatcher._dispatch_message(payload["type"], payload)

        result_mock.assert_called_once()
        self.assertEqual("queued", result_mock.call_args.args[0])
        self.assertEqual(["multi-frame omitted"], result_mock.call_args.kwargs["warnings"])


if __name__ == "__main__":
    unittest.main()
