from __future__ import annotations

import sys
import unittest
from pathlib import Path
from types import SimpleNamespace


ADDON_ROOT = Path(__file__).resolve().parents[2] / "blender_addon" / "blendersync_vnext"
sys.path.insert(0, str(ADDON_ROOT))

from blender.animation_clip import core


class AnimationActionFcurveTests(unittest.TestCase):
    def test_legacy_action_reads_direct_fcurves(self) -> None:
        first = object()
        second = object()
        action = SimpleNamespace(fcurves=[first, second])

        self.assertEqual([first, second], core._iter_action_fcurves(action))

    def test_layered_action_reads_channelbags_and_deduplicates_fcurves(self) -> None:
        first = object()
        shared = object()
        last = object()
        action = SimpleNamespace(
            layers=[
                SimpleNamespace(
                    strips=[
                        SimpleNamespace(
                            channelbags=[
                                SimpleNamespace(fcurves=[first, shared]),
                                SimpleNamespace(fcurves=[shared, last]),
                            ]
                        )
                    ]
                )
            ]
        )

        self.assertEqual([first, shared, last], core._iter_action_fcurves(action))


if __name__ == "__main__":
    unittest.main()
