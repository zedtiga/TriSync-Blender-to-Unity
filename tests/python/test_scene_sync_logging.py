from __future__ import annotations

import sys
import unittest
from contextlib import ExitStack
from pathlib import Path
from types import SimpleNamespace
from unittest import mock


ADDON_ROOT = Path(__file__).resolve().parents[2] / "blender_addon" / "blendersync_vnext"
sys.path.insert(0, str(ADDON_ROOT))

from blender.common import log as sync_log
from blender.scene_sync import controller, material_sync, preview_sync, structure_watch
from blender.scene_sync.runtime_state import ShapeKeyRuntimeState


class _ShapeKeyObject(dict):
    def __init__(self) -> None:
        super().__init__()
        self.type = "MESH"
        self.data = SimpleNamespace(
            shape_keys=SimpleNamespace(
                key_blocks=[
                    SimpleNamespace(name="Basis", value=0.0),
                    SimpleNamespace(name="Smile", value=0.25),
                ]
            )
        )
        self[preview_sync.AUTO_SYNC_READY_KEY] = True


class SceneSyncLoggingTests(unittest.TestCase):
    def setUp(self) -> None:
        sync_log.clear()
        sync_log.set_verbose_preference(False)
        sync_log.set_verbose_override(False)
        controller._background_pump_error_last_at_by_signature.clear()

    def tearDown(self) -> None:
        controller._background_pump_error_last_at_by_signature.clear()
        sync_log.clear()
        sync_log.set_verbose_preference(False)
        sync_log.set_verbose_override(None)

    def test_successful_blendshape_weight_send_is_silent_by_default(self) -> None:
        session = SimpleNamespace(send_auto=mock.Mock(return_value=SimpleNamespace(ok=True)))

        with mock.patch("builtins.print") as print_mock:
            sent = preview_sync.send_shape_key_weights_if_changed(
                ShapeKeyRuntimeState(),
                self._shape_key_hooks(session),
                _ShapeKeyObject(),
                reason="test",
            )

        self.assertTrue(sent)
        self.assertEqual([], sync_log.get_recent_entries())
        print_mock.assert_not_called()

    def test_rejected_blendshape_weight_send_is_buffered_as_warning(self) -> None:
        session = SimpleNamespace(
            send_auto=mock.Mock(return_value=SimpleNamespace(ok=False, error="transport_down"))
        )

        with mock.patch("builtins.print") as print_mock:
            sent = preview_sync.send_shape_key_weights_if_changed(
                ShapeKeyRuntimeState(),
                self._shape_key_hooks(session),
                _ShapeKeyObject(),
                reason="test",
            )

        self.assertFalse(sent)
        entries = sync_log.get_recent_entries()
        self.assertEqual(1, len(entries))
        self.assertEqual("WARN", entries[0]["level"])
        self.assertEqual("blendshape_weights_failed", entries[0]["event"])
        print_mock.assert_not_called()

    def test_blendshape_weight_exception_remains_error_with_traceback(self) -> None:
        session = SimpleNamespace(send_auto=mock.Mock(side_effect=RuntimeError("send exploded")))

        with mock.patch("builtins.print") as print_mock:
            sent = preview_sync.send_shape_key_weights_if_changed(
                ShapeKeyRuntimeState(),
                self._shape_key_hooks(session),
                _ShapeKeyObject(),
                reason="test",
            )

        self.assertFalse(sent)
        entries = sync_log.get_recent_entries()
        self.assertEqual(1, len(entries))
        self.assertEqual("ERROR", entries[0]["level"])
        self.assertEqual("blendshape_weights_exception", entries[0]["event"])
        self.assertEqual("RuntimeError", entries[0]["fields"]["exceptionType"])
        self.assertIs(sys.stderr, print_mock.call_args.kwargs["file"])

    def test_disconnected_auto_sync_state_publication_is_silent(self) -> None:
        session = SimpleNamespace(
            send_auto=mock.Mock(
                return_value=SimpleNamespace(ok=False, error="session_not_connected")
            )
        )

        with (
            mock.patch.object(controller, "get_session", return_value=session),
            mock.patch.object(controller, "_get_preview_idle_commit_seconds", return_value=1.0),
            mock.patch("builtins.print") as print_mock,
        ):
            controller._emit_auto_sync_state(True)

        self.assertEqual([], sync_log.get_recent_entries())
        print_mock.assert_not_called()

    def test_background_pump_exception_is_rate_limited_by_signature(self) -> None:
        with ExitStack() as stack:
            stack.enter_context(mock.patch.object(controller, "_emit_auto_sync_state_if_changed"))
            stack.enter_context(
                mock.patch.object(
                    structure_watch,
                    "poll_active_uv_channels_signature",
                    side_effect=RuntimeError("poll failed"),
                )
            )
            stack.enter_context(mock.patch.object(material_sync, "pump_pending_sends"))
            stack.enter_context(mock.patch.object(material_sync, "pump_ref_resends"))
            stack.enter_context(mock.patch.object(controller, "_get_lifecycle_reconcile_hz", return_value=0.0))
            print_mock = stack.enter_context(mock.patch("builtins.print"))

            controller._run_enabled_background_pumps(1.0)
            controller._run_enabled_background_pumps(2.0)
            controller._run_enabled_background_pumps(11.0)

        entries = sync_log.get_recent_entries()
        self.assertEqual(2, len(entries))
        self.assertTrue(all(entry["level"] == "ERROR" for entry in entries))
        self.assertTrue(all(entry["event"] == "background_pump_exception" for entry in entries))
        self.assertEqual(2, print_mock.call_count)

    @staticmethod
    def _shape_key_hooks(session):
        return SimpleNamespace(
            mesh_has_shape_keys=lambda _obj: True,
            get_session=lambda: session,
            ensure_instance_id=lambda _obj: "object",
            ensure_mesh_asset_id=lambda _obj: "mesh",
            blendshape_weight_sync_interval_seconds=0.1,
        )


if __name__ == "__main__":
    unittest.main()
