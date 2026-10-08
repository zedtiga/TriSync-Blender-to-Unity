from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path
from unittest import mock


ADDON_ROOT = Path(__file__).resolve().parents[2] / "blender_addon" / "blendersync_vnext"
sys.path.insert(0, str(ADDON_ROOT))

from blender.common import log as sync_log


class LoggingFacadeTests(unittest.TestCase):
    def setUp(self) -> None:
        sync_log.clear()
        sync_log.set_verbose_preference(False)
        sync_log.set_verbose_override(False)

    def tearDown(self) -> None:
        sync_log.clear()
        sync_log.set_verbose_preference(False)
        sync_log.set_verbose_override(None)

    def test_default_mode_buffers_info_and_warn_without_console_and_drops_trace(self) -> None:
        trace_factory_calls = 0

        def trace_summary() -> str:
            nonlocal trace_factory_calls
            trace_factory_calls += 1
            return "expensive"

        with mock.patch("builtins.print") as print_mock:
            self.assertTrue(sync_log.info("Session", "connected", "ready"))
            self.assertTrue(sync_log.warn("Mesh", "degraded", "uv skipped"))
            self.assertFalse(sync_log.trace("Preview", "profile", trace_summary))

        self.assertEqual(0, trace_factory_calls)
        self.assertEqual(["INFO", "WARN"], [entry["level"] for entry in sync_log.get_recent_entries()])
        print_mock.assert_not_called()

    def test_error_is_buffered_and_always_written_to_stderr(self) -> None:
        with mock.patch("builtins.print") as print_mock:
            self.assertTrue(sync_log.error("Session", "failed", "connection lost"))

        entry = sync_log.get_recent_entries()[0]
        self.assertEqual("ERROR", entry["level"])
        self.assertIn("[BlenderSync][ERROR][Session] failed", print_mock.call_args.args[0])
        self.assertIs(sys.stderr, print_mock.call_args.kwargs["file"])

    def test_verbose_mode_emits_info_warn_and_lazy_trace(self) -> None:
        sync_log.set_verbose_override(True)
        with mock.patch("builtins.print") as print_mock:
            sync_log.info("Session", "connected")
            sync_log.warn("Mesh", "partial")
            self.assertTrue(sync_log.trace("Preview", "profile", lambda: "totalMs=4.2"))

        self.assertEqual(["INFO", "WARN", "TRACE"], [entry["level"] for entry in sync_log.get_recent_entries()])
        self.assertEqual(3, print_mock.call_count)

    def test_background_mode_enables_verbose_unless_explicitly_overridden(self) -> None:
        sync_log.set_verbose_override(None)
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop(sync_log.VERBOSE_ENV, None)
            with mock.patch.object(sync_log, "_background_verbose", True):
                self.assertTrue(sync_log.verbose_enabled())
                sync_log.set_verbose_override(False)
                self.assertFalse(sync_log.verbose_enabled())

    def test_ring_buffer_and_field_limits_bound_memory(self) -> None:
        for index in range(sync_log.MAX_ENTRIES + 5):
            sync_log.info("Loop", "tick", fields={"index": index})

        entries = sync_log.get_recent_entries()
        self.assertEqual(sync_log.MAX_ENTRIES, len(entries))
        self.assertEqual("5", entries[0]["fields"]["index"])

        sync_log.clear()
        sync_log.info(
            "Import",
            "bounded",
            "x" * (sync_log.MAX_SUMMARY_LENGTH + 100),
            {
                "path": "C:\\Users\\alice\\asset.fbx",
                "payload": "secret",
                "nested": {"secret": True},
                "long": "y" * (sync_log.MAX_FIELD_VALUE_LENGTH + 100),
                "zero": 0,
                "disabled": False,
            },
        )
        entry = sync_log.get_recent_entries()[0]
        self.assertLessEqual(len(entry["summary"]), sync_log.MAX_SUMMARY_LENGTH)
        self.assertEqual({"path", "long", "zero", "disabled"}, set(entry["fields"]))
        self.assertLessEqual(len(entry["fields"]["long"]), sync_log.MAX_FIELD_VALUE_LENGTH)
        self.assertEqual("0", entry["fields"]["zero"])
        self.assertEqual("False", entry["fields"]["disabled"])
        self.assertEqual([], sync_log.get_recent_entries(0))
        self.assertEqual([], sync_log.get_recent_entries(-1))

    def test_exception_preserves_stack_and_records_exception_type(self) -> None:
        try:
            raise RuntimeError("boom")
        except RuntimeError as exc:
            with mock.patch("builtins.print") as print_mock:
                sync_log.exception("Controller", "tick_failed", exc)

        entry = sync_log.get_recent_entries()[0]
        self.assertEqual("RuntimeError", entry["fields"]["exceptionType"])
        self.assertIn("RuntimeError: boom", print_mock.call_args.args[0])
        self.assertIs(sys.stderr, print_mock.call_args.kwargs["file"])


if __name__ == "__main__":
    unittest.main()
