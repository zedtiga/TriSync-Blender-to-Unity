from __future__ import annotations

import sys
import unittest
from pathlib import Path


ADDON_ROOT = Path(__file__).resolve().parents[2] / "blender_addon" / "blendersync_vnext"
sys.path.insert(0, str(ADDON_ROOT))

from blender.scene_sync.policy import above_threshold, in_scope
from blender.scene_sync.types import TransformSnapshot


def _snapshot(
    position=(0.0, 0.0, 0.0),
    rotation=(0.0, 0.0, 0.0, 1.0),
    scale=(1.0, 1.0, 1.0),
) -> TransformSnapshot:
    return TransformSnapshot(
        position=position,
        rotation=rotation,
        scale=scale,
    )


class SceneSyncPolicyTests(unittest.TestCase):
    def test_all_scope_accepts_any_object_scope(self) -> None:
        self.assertTrue(in_scope("all", "selected"))
        self.assertTrue(in_scope("all", "visible"))

    def test_specific_scope_requires_an_exact_match(self) -> None:
        self.assertTrue(in_scope("selected", "selected"))
        self.assertFalse(in_scope("selected", "visible"))

    def test_delta_below_threshold_is_skipped(self) -> None:
        previous = _snapshot()
        current = _snapshot(position=(0.009, 0.0, 0.0))

        self.assertFalse(above_threshold(previous, current, 0.01))

    def test_delta_equal_to_threshold_is_sent(self) -> None:
        previous = _snapshot()
        current = _snapshot(position=(0.01, 0.0, 0.0))

        self.assertTrue(above_threshold(previous, current, 0.01))

    def test_largest_transform_component_controls_threshold(self) -> None:
        previous = _snapshot()
        changes = {
            "position": _snapshot(position=(0.0, -0.02, 0.0)),
            "rotation": _snapshot(rotation=(0.0, 0.02, 0.0, 1.0)),
            "scale": _snapshot(scale=(1.0, 1.0, 1.02)),
        }
        for name, current in changes.items():
            with self.subTest(name=name):
                self.assertTrue(above_threshold(previous, current, 0.01))


if __name__ == "__main__":
    unittest.main()
