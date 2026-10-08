from __future__ import annotations

import copy
import sys
import unittest
from pathlib import Path


ADDON_ROOT = Path(__file__).resolve().parents[2] / "blender_addon" / "blendersync_vnext"
sys.path.insert(0, str(ADDON_ROOT))

from blender.resource_update.fingerprint import compute_mesh_content_fingerprint


def _mesh_content() -> dict:
    return {
        "vertices": [
            0.0,
            0.0,
            0.0,
            1.0,
            0.0,
            0.0,
            0.0,
            1.0,
            0.0,
            1.0,
            1.0,
            0.0,
        ],
        "triangles": [0, 1, 2, 2, 1, 3],
        "normals": [0.0, 0.0, 1.0] * 4,
        "uv": [0.0, 0.0, 1.0, 0.0, 0.0, 1.0, 1.0, 1.0],
    }


class MeshFingerprintTests(unittest.TestCase):
    def test_identical_input_has_stable_fingerprint(self) -> None:
        mesh = _mesh_content()

        first = compute_mesh_content_fingerprint(mesh)
        second = compute_mesh_content_fingerprint(copy.deepcopy(mesh))

        self.assertEqual(first, second)
        self.assertEqual(40, len(first))

    def test_geometry_and_normal_changes_affect_fingerprint(self) -> None:
        original = _mesh_content()
        original_hash = compute_mesh_content_fingerprint(original)

        mutations = {
            "vertex": lambda mesh: mesh["vertices"].__setitem__(0, 0.25),
            "index": lambda mesh: mesh["triangles"].__setitem__(5, 2),
            "normal": lambda mesh: mesh["normals"].__setitem__(2, -1.0),
        }
        for name, mutate in mutations.items():
            with self.subTest(name=name):
                changed = copy.deepcopy(original)
                mutate(changed)
                self.assertNotEqual(
                    original_hash,
                    compute_mesh_content_fingerprint(changed),
                )

    def test_uv_change_only_affects_hash_when_uv_is_included(self) -> None:
        original = _mesh_content()
        changed = copy.deepcopy(original)
        changed["uv"][0] = 0.5

        self.assertNotEqual(
            compute_mesh_content_fingerprint(original),
            compute_mesh_content_fingerprint(changed),
        )
        self.assertEqual(
            compute_mesh_content_fingerprint(original, include_uv=False),
            compute_mesh_content_fingerprint(changed, include_uv=False),
        )

    def test_submesh_material_slot_affects_fingerprint(self) -> None:
        original = _mesh_content()
        original["subMeshes"] = [
            {
                "materialSlot": 0,
                "topology": "triangles",
                "indices": list(original["triangles"]),
            }
        ]
        changed = copy.deepcopy(original)
        changed["subMeshes"][0]["materialSlot"] = 1

        self.assertNotEqual(
            compute_mesh_content_fingerprint(original),
            compute_mesh_content_fingerprint(changed),
        )

    def test_blend_shape_asset_metadata_and_deltas_affect_fingerprint(self) -> None:
        original = _mesh_content()
        original["blendShapes"] = [
            {
                "name": "Smile",
                "frameWeight": 100.0,
                "value": 0.0,
                "sliderMin": 0.0,
                "sliderMax": 1.0,
                "deltaPositions": [0.0, 0.0, 0.1] * 4,
            }
        ]
        original_hash = compute_mesh_content_fingerprint(original)

        metadata_changed = copy.deepcopy(original)
        metadata_changed["blendShapes"][0]["name"] = "WideSmile"
        frame_weight_changed = copy.deepcopy(original)
        frame_weight_changed["blendShapes"][0]["frameWeight"] = 50.0
        delta_changed = copy.deepcopy(original)
        delta_changed["blendShapes"][0]["deltaPositions"][2] = 0.2

        self.assertNotEqual(
            original_hash,
            compute_mesh_content_fingerprint(metadata_changed),
        )
        self.assertNotEqual(
            original_hash,
            compute_mesh_content_fingerprint(frame_weight_changed),
        )
        self.assertNotEqual(
            original_hash,
            compute_mesh_content_fingerprint(delta_changed),
        )

    def test_blend_shape_current_value_and_slider_range_do_not_affect_fingerprint(self) -> None:
        original = _mesh_content()
        original["blendShapes"] = [
            {
                "name": "Smile",
                "frameWeight": 100.0,
                "value": 0.0,
                "sliderMin": 0.0,
                "sliderMax": 1.0,
                "deltaPositions": [0.0, 0.0, 0.1] * 4,
            }
        ]
        changed = copy.deepcopy(original)
        changed["blendShapes"][0]["value"] = 0.75
        changed["blendShapes"][0]["sliderMin"] = -1.0
        changed["blendShapes"][0]["sliderMax"] = 2.0

        self.assertEqual(
            compute_mesh_content_fingerprint(original),
            compute_mesh_content_fingerprint(changed),
        )

    def test_prebuilt_buffer_order_does_not_affect_fingerprint(self) -> None:
        prebuilt = {
            "vertexCount": 3,
            "indexCount": 3,
            "hashProfile": {
                "vertexSha1": "vertex-hash",
                "indexSha1": "index-hash",
                "normalSha1": "normal-hash",
                "uv0Sha1": "uv-hash",
            },
            "buffers": [
                {"semantic": "UV0", "byteLength": 24},
                {"semantic": "POSITION", "byteLength": 36},
                {"semantic": "NORMAL", "byteLength": 36},
                {"semantic": "INDEX", "byteLength": 12},
            ],
            "subMeshes": [
                {
                    "materialSlot": 0,
                    "topology": "triangles",
                    "indicesBuffer": {"count": 3, "byteLength": 12},
                    "indexSha1": "index-hash",
                }
            ],
        }
        reordered = copy.deepcopy(prebuilt)
        reordered["buffers"].reverse()

        self.assertEqual(
            compute_mesh_content_fingerprint(prebuilt_binary=prebuilt),
            compute_mesh_content_fingerprint(prebuilt_binary=reordered),
        )

    def test_prebuilt_blend_shape_current_value_and_slider_range_do_not_affect_fingerprint(self) -> None:
        prebuilt = {
            "vertexCount": 4,
            "indexCount": 6,
            "hashProfile": {
                "vertexSha1": "vertex-hash",
                "indexSha1": "index-hash",
                "blendshape_0_delta_positionsSha1": "delta-hash",
            },
            "blendShapes": [
                {
                    "name": "Smile",
                    "frameWeight": 100.0,
                    "value": 0.0,
                    "sliderMin": 0.0,
                    "sliderMax": 1.0,
                    "vertexCount": 4,
                    "deltaPositionsBuffer": {"count": 4, "byteLength": 48},
                }
            ],
        }
        changed = copy.deepcopy(prebuilt)
        changed["blendShapes"][0]["value"] = 0.75
        changed["blendShapes"][0]["sliderMin"] = -1.0
        changed["blendShapes"][0]["sliderMax"] = 2.0

        self.assertEqual(
            compute_mesh_content_fingerprint(prebuilt_binary=prebuilt),
            compute_mesh_content_fingerprint(prebuilt_binary=changed),
        )


if __name__ == "__main__":
    unittest.main()
