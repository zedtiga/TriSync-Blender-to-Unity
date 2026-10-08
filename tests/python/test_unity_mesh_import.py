from __future__ import annotations

import json
import io
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


ADDON_ROOT = Path(__file__).resolve().parents[2] / "blender_addon" / "blendersync_vnext"
sys.path.insert(0, str(ADDON_ROOT))

from blender import unity_mesh_import
from blender.common import log as sync_log


class UnityMeshImportValidationTests(unittest.TestCase):
    def setUp(self) -> None:
        sync_log.clear()
        sync_log.set_verbose_preference(False)
        sync_log.set_verbose_override(False)

    def tearDown(self) -> None:
        sync_log.clear()
        sync_log.set_verbose_preference(False)
        sync_log.set_verbose_override(None)

    def test_vertices_require_declared_length_and_finite_values(self):
        self.assertEqual(
            unity_mesh_import._read_vertices([0, 1, 2, 3, 4, 5], 2),
            [(0.0, 1.0, 2.0), (3.0, 4.0, 5.0)],
        )
        with self.assertRaisesRegex(ValueError, "vertices_length_mismatch"):
            unity_mesh_import._read_vertices([0, 1, 2], 2)
        with self.assertRaisesRegex(ValueError, "vertices_value_not_finite"):
            unity_mesh_import._read_vertices([0, 1, float("nan")], 1)

    def test_faces_reject_truncated_or_out_of_range_indices(self):
        with self.assertRaisesRegex(ValueError, "not_triangles"):
            unity_mesh_import._read_faces(
                [{"materialSlot": 0, "indices": [0, 1, 2, 0]}],
                3,
            )
        with self.assertRaisesRegex(ValueError, "out_of_range"):
            unity_mesh_import._read_faces(
                [{"materialSlot": 0, "indices": [0, 1, 3]}],
                3,
            )

    def test_optional_attributes_and_blend_shapes_are_exact(self):
        self.assertEqual(unity_mesh_import._read_optional_float_values([], 3, "normals"), [])
        with self.assertRaisesRegex(ValueError, "colors_length_mismatch"):
            unity_mesh_import._read_optional_float_values([1, 2], 4, "colors")
        shape = unity_mesh_import._read_blend_shapes(
            [{"name": "Smile", "frameWeight": 100, "deltaPositions": [0, 0, 0] * 3}],
            3,
        )
        self.assertEqual(shape[0]["name"], "Smile")
        self.assertEqual(shape[0]["frameWeight"], 100.0)
        with self.assertRaisesRegex(ValueError, "blend_shape_Smile_length_mismatch"):
            unity_mesh_import._read_blend_shapes(
                [{"name": "Smile", "deltaPositions": [0, 0, 0]}],
                3,
            )

    def test_import_result_status_distinguishes_partial_and_failed(self):
        with mock.patch.object(unity_mesh_import, "_create_mesh_object", return_value=object()):
            result = unity_mesh_import._import_payload_now(
                {"meshes": [{}], "warnings": ["multi-frame omitted"]}
            )
        self.assertEqual(result["status"], "partial")
        self.assertEqual(result["created"], 1)
        self.assertEqual(result["warnings"], ["multi-frame omitted"])

        with mock.patch.object(
            unity_mesh_import,
            "_create_mesh_object",
            side_effect=ValueError("faces_empty"),
        ):
            result = unity_mesh_import._import_payload_now({"meshes": [{}]})
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["skipped"], 1)
        self.assertIn("faces_empty", result["warnings"][0])

    def test_successful_import_is_buffered_as_info_without_console_output(self) -> None:
        with (
            mock.patch.object(unity_mesh_import, "_create_mesh_object", return_value=object()),
            mock.patch("builtins.print") as print_mock,
        ):
            result = unity_mesh_import._import_payload_now({"meshes": [{}]})

        self.assertEqual("imported", result["status"])
        entries = sync_log.get_recent_entries()
        self.assertEqual(1, len(entries))
        self.assertEqual("INFO", entries[0]["level"])
        self.assertEqual("import_completed", entries[0]["event"])
        print_mock.assert_not_called()

    def test_attribute_degradation_is_returned_as_a_partial_result(self):
        def create_with_warning(_payload, *, result_warnings):
            result_warnings.append("NORMALS_SKIPPED mesh=Cube reason=unsupported")
            return object()

        with mock.patch.object(
            unity_mesh_import,
            "_create_mesh_object",
            side_effect=create_with_warning,
        ):
            result = unity_mesh_import._import_payload_now({"meshes": [{}]})

        self.assertEqual(result["status"], "partial")
        self.assertEqual(result["created"], 1)
        self.assertEqual(
            result["warnings"],
            ["NORMALS_SKIPPED mesh=Cube reason=unsupported"],
        )

    def test_attribute_apply_helpers_report_only_actual_degradation(self):
        class NormalMesh:
            name = "NormalMesh"
            polygons = []

            def normals_split_custom_set_from_vertices(self, _values):
                raise RuntimeError("custom_normals_unavailable")

        normal_warning = unity_mesh_import._apply_normals(
            NormalMesh(),
            [0.0, 0.0, 1.0],
            1,
        )
        self.assertEqual(
            normal_warning,
            "NORMALS_SKIPPED mesh=NormalMesh reason=custom_normals_unavailable",
        )

        class ColorMesh:
            name = "ColorMesh"

        color_warning = unity_mesh_import._apply_vertex_colors(
            ColorMesh(),
            [1.0, 1.0, 1.0, 1.0],
            1,
        )
        self.assertEqual(
            color_warning,
            "COLORS_SKIPPED mesh=ColorMesh reason=color_attributes_unavailable",
        )
        self.assertIsNone(unity_mesh_import._apply_normals(NormalMesh(), [], 1))
        self.assertIsNone(unity_mesh_import._apply_vertex_colors(ColorMesh(), [], 1))

    def test_import_result_message_is_one_shot_and_has_no_transfer_id(self):
        class FakeSession:
            def __init__(self):
                self.payload = None

            def is_feature_negotiated(self, feature):
                return feature == "unity_mesh_import_result_v1"

            def send_auto(self, payload):
                self.payload = payload
                return type("Result", (), {"ok": True, "error": None})()

        session = FakeSession()
        result = unity_mesh_import.send_import_result(
            "imported",
            "Created 1 object(s) in Blender.",
            created=1,
            session=session,
        )
        self.assertTrue(result["ok"])
        self.assertEqual(session.payload["type"], "unity_mesh.import_result_v1")
        self.assertNotIn("transferId", session.payload)
        self.assertEqual(session.payload["created"], 1)

    def test_import_result_is_skipped_without_negotiated_feature(self) -> None:
        class LegacySession:
            def __init__(self):
                self.sent = False

            def is_feature_negotiated(self, feature):
                return False

            def send_auto(self, payload):
                self.sent = True
                raise AssertionError("legacy session must not receive an import result")

        session = LegacySession()
        result = unity_mesh_import.send_import_result(
            "imported",
            "Created 1 object(s) in Blender.",
            created=1,
            session=session,
        )
        self.assertTrue(result["ok"])
        self.assertTrue(result["skipped"])
        self.assertEqual("feature_not_negotiated", result["reason"])
        self.assertFalse(session.sent)

    def test_staged_binary_checksum_and_pair_are_validated(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "Temp" / "BlenderSyncVNext" / "UnityMeshImportsV1"
            root.mkdir(parents=True)
            manifest_path = root / "import-test.manifest.json"
            binary_path = root / "import-test.bin"
            binary_path.write_bytes(b"mesh-data")
            manifest_path.write_text(json.dumps({"schema": "unity_mesh_binary_v1", "meshes": []}))

            payload = {
                "manifestPath": str(manifest_path),
                "binaryPath": str(binary_path),
                "manifestBytes": manifest_path.stat().st_size,
                "binaryBytes": binary_path.stat().st_size,
                "checksumAlgorithm": "crc32-ieee",
                "checksum": unity_mesh_import._crc32_file(str(binary_path)),
            }
            unity_mesh_import._validate_staged_binary_paths(payload)

            payload["checksum"] = "00000000"
            with self.assertRaisesRegex(ValueError, "checksum_mismatch"):
                unity_mesh_import._validate_staged_binary_paths(payload)

            payload["checksum"] = unity_mesh_import._crc32_file(str(binary_path))
            other_binary = root / "import-other.bin"
            other_binary.write_bytes(binary_path.read_bytes())
            payload["binaryPath"] = str(other_binary)
            with self.assertRaisesRegex(ValueError, "staged_file_pair_mismatch"):
                unity_mesh_import._validate_staged_binary_paths(payload)

    def test_binary_descriptor_components_are_not_silently_normalized(self):
        descriptor = {
            "semantic": "POSITION",
            "valueType": "float32",
            "components": 1,
            "count": 3,
            "offset": 0,
            "byteCount": 12,
        }
        with self.assertRaisesRegex(ValueError, "components_mismatch"):
            unity_mesh_import._read_float_buffer(
                io.BytesIO(b"\0" * 12),
                descriptor,
                12,
                required=True,
                expected_components=3,
            )

    def test_typed_empty_optional_float_buffer_is_accepted(self):
        descriptor = {
            "semantic": "NORMAL",
            "valueType": "float32",
            "components": 3,
            "count": 0,
            "offset": 0,
            "byteCount": 0,
        }
        values = unity_mesh_import._read_float_buffer(
            io.BytesIO(b""),
            descriptor,
            0,
            expected_components=3,
        )
        self.assertEqual(values, [])

    def test_queue_rejects_when_blender_timer_is_unavailable(self):
        payload = {"type": "unity_mesh.import_v1", "meshes": [{}]}
        with mock.patch.object(unity_mesh_import, "bpy", None):
            result = unity_mesh_import.import_unity_mesh_payload(payload)
        self.assertFalse(result["ok"])
        self.assertEqual(result["reason"], "import_timer_unavailable")
        with unity_mesh_import._pending_lock:
            self.assertNotIn(payload, unity_mesh_import._pending_payloads)


if __name__ == "__main__":
    unittest.main()
