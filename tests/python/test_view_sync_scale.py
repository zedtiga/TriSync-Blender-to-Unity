from __future__ import annotations

import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock


ROOT = Path(__file__).resolve().parents[2]
ADDON_ROOT = ROOT / "blender_addon" / "blendersync_vnext"
if str(ADDON_ROOT) not in sys.path:
    sys.path.insert(0, str(ADDON_ROOT))

from blender.scene_sync import controller
from blender.ui import view_context_builders


class _Vector:
    def __init__(self, values):
        self.x, self.y, self.z = values


class _Quaternion:
    x = 0.0
    y = 0.0
    z = 0.0
    w = 1.0

    def __matmul__(self, vector):
        return vector


class ViewSyncScaleTests(unittest.TestCase):
    def test_view_context_carries_user_scale_without_modifying_raw_distance(self) -> None:
        region_3d = SimpleNamespace(
            view_location=_Vector((1.0, 2.0, 3.0)),
            view_rotation=_Quaternion(),
            view_perspective="PERSP",
            view_distance=8.0,
        )
        space = SimpleNamespace(
            type="VIEW_3D",
            region_3d=region_3d,
            lens=50.0,
            clip_start=0.01,
            clip_end=1000.0,
        )
        context = SimpleNamespace(space_data=space, area=None, screen=None)
        mathutils = SimpleNamespace(Vector=_Vector)

        with (
            mock.patch.object(view_context_builders, "bpy", object()),
            mock.patch.object(view_context_builders, "get_view_sync_scale", return_value=1.35),
            mock.patch.dict(sys.modules, {"mathutils": mathutils}),
        ):
            payload = view_context_builders.build_scene_view_state_context("session", context)[
                "viewStatePayload"
            ]

        self.assertEqual(8.0, payload["distance"])
        self.assertEqual(8.0, payload["orthographicScale"])
        self.assertEqual(1.35, payload["viewScale"])

    def test_view_context_clamps_scale_to_the_published_range(self) -> None:
        region_3d = SimpleNamespace(
            view_location=_Vector((0.0, 0.0, 0.0)),
            view_rotation=_Quaternion(),
            view_perspective="ORTHO",
            view_distance=4.0,
        )
        space = SimpleNamespace(
            type="VIEW_3D",
            region_3d=region_3d,
            lens=50.0,
            clip_start=0.01,
            clip_end=1000.0,
        )
        context = SimpleNamespace(space_data=space, area=None, screen=None)
        mathutils = SimpleNamespace(Vector=_Vector)

        with (
            mock.patch.object(view_context_builders, "bpy", object()),
            mock.patch.dict(sys.modules, {"mathutils": mathutils}),
        ):
            with mock.patch.object(view_context_builders, "get_view_sync_scale", return_value=0.01):
                near_payload = view_context_builders.build_scene_view_state_context(None, context)["viewStatePayload"]
            with mock.patch.object(view_context_builders, "get_view_sync_scale", return_value=9.0):
                far_payload = view_context_builders.build_scene_view_state_context(None, context)["viewStatePayload"]

        self.assertEqual(0.1, near_payload["viewScale"])
        self.assertEqual(5.0, far_payload["viewScale"])

    def test_auto_sync_fingerprint_tracks_scale_changes(self) -> None:
        payload = {
            "pivot": [1.0, 2.0, 3.0],
            "forward": [0.0, 0.0, -1.0],
            "up": [0.0, 1.0, 0.0],
            "distance": 8.0,
            "lens": 50.0,
            "viewScale": 1.0,
            "isOrthographic": False,
            "viewMode": "PERSP",
        }
        baseline = controller._fingerprint_view_payload(payload)

        payload["viewScale"] = 1.25

        self.assertNotEqual(baseline, controller._fingerprint_view_payload(payload))


if __name__ == "__main__":
    unittest.main()
