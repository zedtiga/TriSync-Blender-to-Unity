from __future__ import annotations

import sys
import tempfile
import unittest
from array import array
from pathlib import Path
from unittest import mock


ADDON_ROOT = Path(__file__).resolve().parents[2] / "blender_addon" / "blendersync_vnext"
sys.path.insert(0, str(ADDON_ROOT))

from blender.ui import object_context_builders
from blender.native import mesh_extractor
from blender.scene_sync import mesh_context


def multi_uv_raw() -> dict:
    uv0 = array(
        "f",
        [
            0.0, 0.0,
            1.0, 0.0,
            0.0, 1.0,
            0.0, 0.0,
            0.0, 1.0,
            1.0, 0.0,
        ],
    )
    return {
        "vertex_count": 3,
        "tri_count": 2,
        "positions": array("f", [0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 1.0, 1.0, 0.0]),
        "vertex_normals": array("f", [0.0, 0.0, 1.0] * 3),
        "loop_vertex_indices": array("i", [0, 1, 2, 0, 2, 1]),
        "loop_normals": array("f", [0.0, 0.0, 1.0] * 6),
        "tri_loops": array("i", [0, 1, 2, 3, 4, 5]),
        "uv0": uv0,
        "uv_layers": [
            {"index": 0, "name": "UV_Main", "values": uv0},
            {
                "index": 3,
                "name": "UV_Detail",
                "values": array(
                    "f",
                    [
                        0.0, 0.0,
                        0.5, 0.0,
                        0.0, 0.5,
                        0.0, 0.0,
                        0.0, 0.5,
                        0.5, 0.0,
                    ],
                ),
            },
            {
                "index": 7,
                "name": "UV_Aux",
                "values": array(
                    "f",
                    [
                        0.0, 0.0,
                        0.25, 0.0,
                        0.0, 0.25,
                        0.75, 0.75,
                        0.0, 0.25,
                        0.25, 0.0,
                    ],
                ),
            },
        ],
        "have_loop_normals": True,
        "color_attribute": None,
    }


class MultiUvMeshBuildTests(unittest.TestCase):
    def test_initial_import_deduplication_keeps_secondary_uv_seams_distinct(self) -> None:
        result = object_context_builders._build_mesh_arrays_from_raw(multi_uv_raw())

        self.assertEqual(4, len(result[0]) // 3)
        self.assertEqual([0, 1, 2, 3, 2, 1], result[3])
        self.assertEqual([0, 1, 2, 0], result[4])
        self.assertEqual([0, 1, 2, 3], result[5])
        self.assertEqual([0, 3, 7], [channel["index"] for channel in result[6]])
        self.assertTrue(all(len(channel["values"]) == 8 for channel in result[6]))

    def test_initial_import_multi_uv_path_falls_back_when_native_lacks_capability(self) -> None:
        expected = object_context_builders._build_mesh_arrays_from_raw(multi_uv_raw())

        with (
            mock.patch.object(object_context_builders, "_collect_mesh_raw_buffers", return_value=multi_uv_raw()),
            mock.patch.object(object_context_builders, "_build_mesh_arrays_from_raw", return_value=expected),
            mock.patch.object(
                object_context_builders,
                "get_native_status",
                return_value={"available": True, "capabilities": []},
            ),
            mock.patch.object(object_context_builders, "try_extract_mesh_arrays_native") as native_mock,
        ):
            result = object_context_builders._build_mesh_arrays_fast(object())

        self.assertEqual("foreach_get_multi_uv_reference", result[8])
        native_mock.assert_not_called()

    def test_multi_uv_gate_uses_capability_instead_of_native_version(self) -> None:
        raw = multi_uv_raw()
        legacy_status = {"available": True, "version": "99.0.0", "capabilities": []}
        capable_status = {
            "available": True,
            "version": "0.2.0",
            "capabilities": [mesh_extractor.NATIVE_MULTI_UV_CAPABILITY],
        }

        self.assertTrue(object_context_builders._raw_requires_python_multi_uv_dedupe(raw, legacy_status))
        self.assertTrue(mesh_context._raw_requires_python_multi_uv_dedupe(raw, legacy_status))
        self.assertFalse(object_context_builders._raw_requires_python_multi_uv_dedupe(raw, capable_status))
        self.assertFalse(mesh_context._raw_requires_python_multi_uv_dedupe(raw, capable_status))
        self.assertEqual(2, len(mesh_extractor._native_raw_payload(raw)["secondary_uv_layers"]))

    @unittest.skipUnless(sys.platform == "win32", "tracked native artifact is currently Windows-only")
    def test_native_and_python_multi_uv_deduplication_match(self) -> None:
        raw = multi_uv_raw()
        status = mesh_extractor.get_native_status()
        self.assertTrue(status.get("available"), status.get("importError"))
        self.assertIn(mesh_extractor.NATIVE_MULTI_UV_CAPABILITY, status.get("capabilities") or [])

        native = mesh_extractor.try_extract_mesh_arrays_native(raw=raw)
        self.assertIsNotNone(native)
        self.assertTrue(native.get("ok"))
        reference = mesh_context._build_mesh_arrays_from_raw_for_preview_detailed(raw)

        for key, expected in zip(
            ("vertices", "normals", "uv0", "indices", "sourceIndices", "sourceLoopIndices"),
            reference,
        ):
            with self.subTest(key=key):
                self.assertEqual(list(expected), list(native.get(key) or []))

        native_channels = mesh_context._build_uv_channels_from_raw_for_preview(
            raw,
            native.get("sourceLoopIndices") or [],
        )
        reference_channels = mesh_context._build_uv_channels_from_raw_for_preview(raw, reference[5])
        self.assertEqual(
            [
                (channel["index"], channel["name"], list(channel["values"]))
                for channel in reference_channels
            ],
            [
                (channel["index"], channel["name"], list(channel["values"]))
                for channel in native_channels
            ],
        )

        with tempfile.TemporaryDirectory() as output_dir:
            binary = mesh_extractor.try_extract_mesh_binary_native(
                raw=raw,
                output_dir=output_dir,
                prefix="multi_uv",
            )
            self.assertIsNotNone(binary)
            self.assertTrue(binary.get("ok"))
            buffers = {
                str(buffer.get("semantic")): buffer
                for buffer in binary.get("buffers") or []
                if isinstance(buffer, dict)
            }

            def read_buffer(semantic: str, typecode: str) -> list:
                values = array(typecode)
                values.frombytes(Path(buffers[semantic]["path"]).read_bytes())
                return list(values)

            self.assertEqual(list(reference[0]), read_buffer("POSITION", "f"))
            self.assertEqual(list(reference[1]), read_buffer("NORMAL", "f"))
            self.assertEqual(list(reference[2]), read_buffer("UV0", "f"))
            self.assertEqual(list(reference[3]), read_buffer("INDEX", "i"))
            self.assertEqual(list(reference[4]), list(binary.get("sourceIndices") or []))
            self.assertEqual(list(reference[5]), list(binary.get("sourceLoopIndices") or []))


if __name__ == "__main__":
    unittest.main()
