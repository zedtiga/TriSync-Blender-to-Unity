from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
UNITY_ROOT = ROOT / "unity" / "TriSync"


class GeneratedResourcePathTests(unittest.TestCase):
    def test_product_uses_unversioned_generated_resource_roots(self) -> None:
        paths = (
            UNITY_ROOT / "Scripts" / "SceneSyncCore" / "MaterialContentV1ApplyService.cs",
            UNITY_ROOT / "Scripts" / "SceneSyncCore" / "MaterialReferenceApplyService.cs",
        )
        source = "\n".join(path.read_text(encoding="utf-8-sig") for path in paths)
        self.assertIn('MaterialsRootAssetPath = "Assets/TriSync/Resources/Materials"', source)
        self.assertIn('TexturesRootAssetPath = "Assets/TriSync/Resources/Textures"', source)
        self.assertIn('DefaultMaterialAssetPath = MaterialsRootAssetPath + "/TriSyncDefault.mat"', source)
        self.assertIn('name = "TriSyncDefault"', source)
        self.assertIn("GeneratedResourcePaths.MaterialsRootAssetPath", source)
        self.assertIn("GeneratedResourcePaths.TexturesRootAssetPath", source)
        self.assertNotIn("BlenderSyncDefault", source)

    def test_legacy_generated_resource_migration_is_not_shipped(self) -> None:
        material_apply = (
            UNITY_ROOT / "Scripts" / "SceneSyncCore" / "MaterialContentV1ApplyService.cs"
        ).read_text(encoding="utf-8-sig")
        material_reference = (
            UNITY_ROOT / "Scripts" / "SceneSyncCore" / "MaterialReferenceApplyService.cs"
        ).read_text(encoding="utf-8-sig")
        product_source = "\n".join((material_apply, material_reference))
        sync_script = (ROOT / "tools" / "Sync-UnityRuntime.ps1").read_text(
            encoding="utf-8-sig"
        )
        for retired_symbol in (
            "EnsureMigrated",
            "LegacyMaterialsRootAssetPath",
            "LegacyTexturesRootAssetPath",
            "MigrateRoot",
            "RewriteDatabasePaths",
            "InitializeOnLoadMethod",
            "generated_resource_path_conflict",
        ):
            self.assertNotIn(retired_symbol, product_source)
        for retired_root in ("MaterialsV1", "TexturesV1"):
            self.assertNotIn(f"Resources\\{retired_root}\\*", sync_script)
            self.assertNotIn(f"Resources\\{retired_root}.meta", sync_script)

    def test_protocol_and_implementation_v1_names_remain_unchanged(self) -> None:
        material_service = (
            UNITY_ROOT / "Scripts" / "SceneSyncCore" / "MaterialContentV1ApplyService.cs"
        ).read_text(encoding="utf-8-sig")
        message_source = (
            UNITY_ROOT / "Scripts" / "SceneSyncCore" / "SceneSyncMessage.cs"
        ).read_text(encoding="utf-8-sig")
        self.assertIn("MaterialContentV1ApplyService", material_service)
        self.assertIn('scene_sync.material_content_v1', material_service)
        self.assertIn("MaterialContentV1Textures", message_source)


if __name__ == "__main__":
    unittest.main()
