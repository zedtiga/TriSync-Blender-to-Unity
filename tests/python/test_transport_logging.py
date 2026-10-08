from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest import mock


ADDON_ROOT = Path(__file__).resolve().parents[2] / "blender_addon" / "blendersync_vnext"
sys.path.insert(0, str(ADDON_ROOT))

from blender.common import log as sync_log
from blender.common.types import SendResult
from blender.transport import entrypoints


class _Session:
    def __init__(self, result=None, failure: Exception | None = None) -> None:
        self.result = result or SendResult(ok=True, message="sent")
        self.failure = failure

    def send_auto(self, _payload):
        if self.failure is not None:
            raise self.failure
        return self.result


class TransportLoggingTests(unittest.TestCase):
    def setUp(self) -> None:
        sync_log.clear()
        sync_log.set_verbose_preference(False)
        sync_log.set_verbose_override(False)

    def tearDown(self) -> None:
        sync_log.clear()
        sync_log.set_verbose_preference(False)
        sync_log.set_verbose_override(None)

    def test_successful_high_frequency_send_is_silent_by_default(self) -> None:
        with mock.patch("builtins.print") as print_mock:
            result = entrypoints.send_scene_view_state(
                {
                    "session": _Session(),
                    "viewStatePayload": {
                        "type": "scene_sync.view_state_v1",
                        "viewMode": "SOLID",
                        "distance": 4.0,
                    },
                }
            )

        self.assertTrue(result.ok)
        self.assertEqual([], sync_log.get_recent_entries())
        print_mock.assert_not_called()

    def test_expected_session_failure_is_buffered_as_warning(self) -> None:
        with mock.patch("builtins.print") as print_mock:
            result = entrypoints.send_scene_view_state(
                {"session": None, "viewStatePayload": {"type": "scene_sync.view_state_v1"}}
            )

        self.assertFalse(result.ok)
        entries = sync_log.get_recent_entries()
        self.assertEqual(1, len(entries))
        self.assertEqual("WARN", entries[0]["level"])
        self.assertEqual("view_state_failed", entries[0]["event"])
        print_mock.assert_not_called()

    def test_unexpected_transport_exception_remains_error_with_exception_type(self) -> None:
        with mock.patch("builtins.print") as print_mock:
            result = entrypoints.send_scene_view_state(
                {
                    "session": _Session(failure=RuntimeError("unexpected send failure")),
                    "viewStatePayload": {"type": "scene_sync.view_state_v1"},
                }
            )

        self.assertFalse(result.ok)
        entries = sync_log.get_recent_entries()
        self.assertEqual(1, len(entries))
        self.assertEqual("ERROR", entries[0]["level"])
        self.assertEqual("view_state_failed", entries[0]["event"])
        self.assertEqual("RuntimeError", entries[0]["fields"]["exceptionType"])
        self.assertIs(sys.stderr, print_mock.call_args.kwargs["file"])


if __name__ == "__main__":
    unittest.main()
