from __future__ import annotations

import copy
import math
import sys
import unittest
from pathlib import Path


ADDON_ROOT = Path(__file__).resolve().parents[2] / "blender_addon" / "blendersync_vnext"
sys.path.insert(0, str(ADDON_ROOT))

from blender.animation_clip import core


def _track(path: str, prop: str, component: str, values, times=None, target_type: str = "bone") -> dict:
    times = list(times or [0.0, 0.5, 1.0])
    return {
        "path": path,
        "targetType": target_type,
        "property": prop,
        "component": component,
        "keys": [
            {"time": float(time), "value": float(value)}
            for time, value in zip(times, values)
        ],
    }


class AnimationStaticCurveTests(unittest.TestCase):
    def test_off_preserves_constant_transform_tracks(self) -> None:
        tracks = [
            _track("Arm/hand", "localPosition", axis, [1.0, 1.0, 1.0])
            for axis in ("x", "y", "z")
        ]
        original = copy.deepcopy(tracks)

        collapsed = core._collapse_constant_transform_tracks(
            tracks,
            core.STATIC_CURVE_MODE_OFF,
        )

        self.assertEqual(0, collapsed)
        self.assertEqual(original, tracks)

    def test_collapse_constant_vector_keeps_all_bindings_and_first_key(self) -> None:
        tracks = [
            _track("Arm/hand", "localPosition", "x", [1.0, 1.0 + 2e-7, 1.0]),
            _track("Arm/hand", "localPosition", "y", [2.0, 2.0, 2.0]),
            _track("Arm/hand", "localPosition", "z", [3.0, 3.0, 3.0]),
        ]

        collapsed = core._collapse_constant_transform_tracks(
            tracks,
            core.STATIC_CURVE_MODE_COLLAPSE_CONSTANT,
        )

        self.assertEqual(3, collapsed)
        self.assertEqual(3, len(tracks))
        self.assertEqual(
            [(0.0, 1.0), (0.0, 2.0), (0.0, 3.0)],
            [(track["keys"][0]["time"], track["keys"][0]["value"]) for track in tracks],
        )
        self.assertEqual(["x", "y", "z"], [track["component"] for track in tracks])

    def test_collapse_constant_quaternion_handles_sign_equivalent_keys(self) -> None:
        quaternion_values = {
            "x": [0.0, -0.0, 0.0],
            "y": [0.0, -0.0, 0.0],
            "z": [0.0, -0.0, 0.0],
            "w": [1.0, -1.0, 1.0],
        }
        tracks = [
            _track("Arm/hand", "localRotation", axis, values)
            for axis, values in quaternion_values.items()
        ]

        collapsed = core._collapse_constant_transform_tracks(
            tracks,
            core.STATIC_CURVE_MODE_COLLAPSE_CONSTANT,
        )

        self.assertEqual(4, collapsed)
        self.assertTrue(all(len(track["keys"]) == 1 for track in tracks))
        self.assertEqual([0.0, 0.0, 0.0, 1.0], [track["keys"][0]["value"] for track in tracks])

    def test_nonconstant_or_non_transform_tracks_are_untouched(self) -> None:
        tracks = [
            _track("Arm/hand", "localScale", axis, [1.0, 1.0, 1.0])
            for axis in ("x", "y", "z")
        ]
        tracks.append(_track("Mesh", "blendShapeWeight", "Smile", [0.0, 0.0, 0.0], target_type="blend_shape"))
        tracks[0]["keys"][1]["value"] = 1.01
        original_blend_shape = copy.deepcopy(tracks[-1])

        collapsed = core._collapse_constant_transform_tracks(
            tracks,
            core.STATIC_CURVE_MODE_COLLAPSE_CONSTANT,
        )

        self.assertEqual(0, collapsed)
        self.assertEqual(3, len(tracks[0]["keys"]))
        self.assertEqual(original_blend_shape, tracks[-1])

    def test_invalid_quaternion_values_are_not_collapsed(self) -> None:
        for first_value in (0.0, math.nan):
            values = {
                "x": [first_value, 0.0, 0.0],
                "y": [0.0, 0.0, 0.0],
                "z": [0.0, 0.0, 0.0],
                "w": [0.0 if first_value == 0.0 else 1.0, 1.0, 1.0],
            }
            tracks = [
                _track("Arm/hand", "localRotation", axis, component_values)
                for axis, component_values in values.items()
            ]

            collapsed = core._collapse_constant_transform_tracks(
                tracks,
                core.STATIC_CURVE_MODE_COLLAPSE_CONSTANT,
            )

            self.assertEqual(0, collapsed)
            self.assertTrue(all(len(track["keys"]) == 3 for track in tracks))

    def test_invalid_mode_falls_back_to_off(self) -> None:
        self.assertEqual(
            core.STATIC_CURVE_MODE_OFF,
            core._normalize_static_curve_mode("remove_static_pose"),
        )


if __name__ == "__main__":
    unittest.main()
