from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest import mock


ADDON_ROOT = Path(__file__).resolve().parents[2] / "blender_addon" / "blendersync_vnext"
sys.path.insert(0, str(ADDON_ROOT))

from blender.common import exception_boundary
from blender.ui import operators


class BoundaryExceptionLoggingTests(unittest.TestCase):
    def setUp(self) -> None:
        exception_boundary.reset_boundary_exception_throttle()

    def tearDown(self) -> None:
        exception_boundary.reset_boundary_exception_throttle()

    def test_single_line_is_unlimited_but_traceback_is_rate_limited_per_site(self) -> None:
        with (
            mock.patch.dict(exception_boundary.os.environ, {"BLENDERSYNC_TRACEBACKS": "1"}),
            mock.patch.object(exception_boundary.time, "monotonic", side_effect=[100.0, 105.0, 111.0]),
            mock.patch("builtins.print") as print_mock,
        ):
            results = [self._report_value_error("timer") for _ in range(3)]

        self.assertEqual([True, False, True], results)
        messages = [str(call.args[0]) for call in print_mock.call_args_list]
        self.assertEqual(3, sum("one-line" in message for message in messages))
        tracebacks = [message for message in messages if "traceback site=timer" in message]
        self.assertEqual(2, len(tracebacks))
        self.assertTrue(all("ValueError: boom" in message for message in tracebacks))

    def test_traceback_environment_switch_preserves_single_line(self) -> None:
        with (
            mock.patch.dict(exception_boundary.os.environ, {"BLENDERSYNC_TRACEBACKS": "0"}),
            mock.patch("builtins.print") as print_mock,
        ):
            result = self._report_value_error("dispatcher")

        self.assertFalse(result)
        print_mock.assert_called_once_with("one-line")

    def test_operator_value_errors_remain_user_validation_without_traceback(self) -> None:
        with mock.patch.object(operators, "report_boundary_exception") as report_mock:
            operators._report_unexpected_operator_exception("operator:test", ValueError("selection_empty"))
            operators._report_unexpected_operator_exception("operator:test", RuntimeError("unexpected"))

        report_mock.assert_called_once()
        self.assertIsInstance(report_mock.call_args.args[1], RuntimeError)

    @staticmethod
    def _report_value_error(site: str) -> bool:
        try:
            raise ValueError("boom")
        except ValueError as exc:
            return exception_boundary.report_boundary_exception(
                site,
                exc,
                message="one-line",
                traceback_interval_seconds=10.0,
            )


if __name__ == "__main__":
    unittest.main()
