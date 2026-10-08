from __future__ import annotations

import importlib.util
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace


ROOT = Path(__file__).resolve().parents[2]
METADATA_TOOL_PATH = ROOT / "tools" / "release_metadata.py"
ADDON_ROOT = ROOT / "blender_addon" / "blendersync_vnext"
if str(ADDON_ROOT) not in sys.path:
    sys.path.insert(0, str(ADDON_ROOT))

from blender.package.builder import PackageBuilder


def _load_tool():
    spec = importlib.util.spec_from_file_location("release_metadata", METADATA_TOOL_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError("release_metadata_import_failed")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class ReleaseMetadataTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.tool = _load_tool()

    def test_repository_release_metadata_is_consistent(self) -> None:
        metadata = self.tool.load_release_metadata(ROOT)

        self.assertEqual("1.0.0", metadata["productVersion"])
        self.assertEqual("1.0.0", metadata["blenderAddonVersion"])
        self.assertEqual("0.2.0", metadata["nativeVersion"])
        self.assertEqual(1, metadata["sessionProtocolVersion"])
        self.assertEqual("asset-bridge-v1", metadata["assetBridgeContractVersion"])
        self.assertIn("accurate_v1", metadata["nativeCapabilities"])
        self.assertIn("submesh_v1", metadata["nativeCapabilities"])
        self.assertIn("multi_uv_v1", metadata["nativeCapabilities"])

    def test_product_version_rejects_ambiguous_or_zero_iteration_labels(self) -> None:
        invalid = (
            "0.9",
            "v0.9.0-preview.13",
            "0.9.0-preview",
            "0.9.0-preview.0",
            "0.9.0 preview.13",
        )

        for value in invalid:
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    self.tool._product_version_parts(value)

    def test_product_version_core_matches_blender_tuple_shape(self) -> None:
        self.assertEqual((1, 0, 0), self.tool._product_version_parts("1.0.0"))
        self.assertEqual((0, 9, 0), self.tool._product_version_parts("0.9.0-preview.13"))
        self.assertEqual((1, 2, 3), self.tool._product_version_parts("1.2.3"))

    def test_asset_bridge_envelope_uses_stable_contract_identifier(self) -> None:
        envelope = PackageBuilder().build(SimpleNamespace(nodes=[]), {})

        self.assertEqual("asset-bridge-v1", envelope.contractVersion)


if __name__ == "__main__":
    unittest.main()
