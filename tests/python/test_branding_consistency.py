from __future__ import annotations

import ast
import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
ADDON_ROOT = ROOT / "blender_addon" / "blendersync_vnext"
UNITY_ROOT = ROOT / "unity" / "TriSync"
SHADER_GUID = "e6a884a9ab26c6b44a2ac01cbc5b0497"


def _source(relative_path: str) -> str:
    return (ROOT / relative_path).read_text(encoding="utf-8-sig")


def _literal_assignment(path: Path, name: str):
    tree = ast.parse(path.read_text(encoding="utf-8-sig"), filename=str(path))
    for node in tree.body:
        if not isinstance(node, ast.Assign):
            continue
        if any(isinstance(target, ast.Name) and target.id == name for target in node.targets):
            return ast.literal_eval(node.value)
    raise AssertionError(f"assignment_missing:{path}:{name}")


class BrandingConsistencyTests(unittest.TestCase):
    def test_blender_public_brand_surfaces_use_trisync(self) -> None:
        bl_info = _literal_assignment(ADDON_ROOT / "__init__.py", "bl_info")
        self.assertEqual("TriSync", bl_info["name"])
        self.assertEqual("TriSync Team", bl_info["author"])
        self.assertEqual("View3D > Sidebar > TriSync", bl_info["location"])
        self.assertEqual(
            "TriSync live preview integration between Blender and Unity",
            bl_info["description"],
        )

        panel_source = _source(
            "blender_addon/blendersync_vnext/blender/ui/session_panel.py"
        )
        for label in ("TriSync Session", "TriSync UV", "TriSync Material"):
            self.assertIn(f'bl_label = "{label}"', panel_source)
        self.assertEqual(3, panel_source.count('bl_category = "TriSync"'))

    def test_unity_public_brand_surfaces_use_trisync(self) -> None:
        session = _source("unity/TriSync/Scripts/UI/SessionPanel.cs")
        animation = _source(
            "unity/TriSync/Scripts/RootMotion/AnimationToolsWindow.cs"
        )
        user_settings = _source(
            "unity/TriSync/Scripts/UI/BlenderSyncUserSettingsGUI.cs"
        )
        pose = _source(
            "unity/TriSync/Scripts/AssetBridgeCore/RiggedPoseApplyService.cs"
        )

        self.assertIn('MenuPath = "TriSync/Open TriSync"', session)
        self.assertIn('GetWindow<SessionPanel>(false, "TriSync", true)', session)
        self.assertIn('Tr("TriSync")', session)
        self.assertIn('MenuPath = "TriSync/Open Animation Tools"', animation)
        self.assertIn('Tr("TriSync Animation")', animation)
        self.assertIn('SettingsProvider("Preferences/TriSync"', user_settings)
        self.assertIn('label = "TriSync"', user_settings)
        self.assertIn("BlenderSyncUserSettingsGUI.DrawControls()", session)
        self.assertIn('Tr("TriSync Sync Current Pose")', pose)
        self.assertIn('Tr("TriSync Restore Static Pose")', pose)

    def test_shader_identity_and_guid_use_trisync(self) -> None:
        shader = UNITY_ROOT / "Shaders" / "TriSync_PrincipledLit_URP.shader"
        meta = shader.with_suffix(shader.suffix + ".meta")
        self.assertTrue(shader.is_file())
        self.assertTrue(meta.is_file())
        self.assertFalse(
            (UNITY_ROOT / "Shaders" / "BlenderSync_PrincipledLit_URP.shader").exists()
        )
        self.assertIn('Shader "TriSync/Principled Lit URP"', shader.read_text(encoding="utf-8-sig"))
        self.assertRegex(meta.read_text(encoding="utf-8-sig"), rf"(?m)^guid: {SHADER_GUID}$")

    def test_translation_catalog_brand_keys_use_trisync(self) -> None:
        blender_catalog = _literal_assignment(
            ADDON_ROOT / "blender" / "translations.py", "ZH_HANS"
        )
        unity_source = _source(
            "unity/TriSync/Scripts/Localization/BlenderSyncLocalization.cs"
        )
        unity_catalog = re.findall(
            r'\{\s*"((?:[^"\\]|\\.)*)"\s*,\s*"((?:[^"\\]|\\.)*)"\s*\},',
            unity_source,
        )
        self.assertTrue(unity_catalog)
        for source, translated in (*blender_catalog.items(), *unity_catalog):
            self.assertNotRegex(source, r"\bBlenderSync\b")
            self.assertNotRegex(translated, r"\bBlenderSync\b")
        self.assertIn("TriSync Session", blender_catalog)
        self.assertIn(("TriSync", "TriSync"), unity_catalog)
        self.assertIn(("TriSync Animation", "TriSync 动画"), unity_catalog)

    def test_release_artifact_templates_use_trisync(self) -> None:
        builder = _source("tools/Build-ReleasePackage.ps1")
        release_test = _source("tools/Test-ReleasePackage.ps1")
        for source in (builder, release_test):
            self.assertIn('"TriSync-', source)
            self.assertNotIn('"BlenderSyncVNext-$', source)
        self.assertIn('"TriSync-Blender-$productVersion.zip"', builder)
        self.assertIn('"TriSync-Unity-$productVersion.zip"', builder)

    def test_unity_package_and_generated_asset_roots_use_trisync(self) -> None:
        self.assertTrue(UNITY_ROOT.is_dir())
        self.assertFalse((ROOT / "unity" / "BlenderSyncVNext").exists())

        checked_paths = (
            ROOT / ".gitignore",
            ROOT / "README.md",
            ROOT / "LICENSE.md",
            ROOT / "THIRD_PARTY_NOTICES.md",
            ROOT / "tools" / "Build-ReleasePackage.ps1",
            ROOT / "tools" / "Sync-CompatibilityWorkspaces.ps1",
            ROOT / "tools" / "Sync-UnityRuntime.ps1",
            ROOT / "tools" / "Test-CompatibilityMatrix.ps1",
            ROOT / "tools" / "Test-LicenseBundle.ps1",
            ROOT / "tools" / "Test-LoggingPolicy.ps1",
            ROOT / "tools" / "Test-ProtocolTypes.ps1",
            ROOT / "tools" / "Test-UnityPlayerBoundary.ps1",
            ROOT / "tools" / "release_metadata.py",
            *(ROOT / "docs").glob("*.md"),
            *UNITY_ROOT.rglob("*.cs"),
        )
        retired_roots = (
            "unity/BlenderSyncVNext/",
            "unity\\BlenderSyncVNext\\",
            "Assets/BlenderSyncVNext/",
            "Assets\\BlenderSyncVNext\\",
        )
        violations = []
        for path in checked_paths:
            source = path.read_text(encoding="utf-8-sig")
            for retired_root in retired_roots:
                if retired_root in source:
                    violations.append(f"{path.relative_to(ROOT)}:{retired_root}")
        self.assertEqual([], violations)

        sync_script = _source("tools/Sync-UnityRuntime.ps1")
        release_builder = _source("tools/Build-ReleasePackage.ps1")
        material_source = _source(
            "unity/TriSync/Scripts/SceneSyncCore/MaterialContentV1ApplyService.cs"
        )
        material_reference = _source(
            "unity/TriSync/Scripts/SceneSyncCore/MaterialReferenceApplyService.cs"
        )
        self.assertIn(r"$RuntimeRoot = Join-Path $UnityProjectRoot 'Assets\TriSync'", sync_script)
        self.assertIn(r"unity\TriSync", sync_script)
        self.assertIn("'unity/TriSync'", release_builder)
        self.assertIn("'unity\\TriSync'", release_builder)
        self.assertIn('"Assets/TriSync/Resources/Materials"', material_source)
        self.assertIn('"Assets/TriSync/Resources/Textures"', material_source)
        self.assertIn('"/TriSyncDefault.mat"', material_source)
        self.assertIn('name = "TriSyncDefault"', material_reference)
        self.assertNotIn("BlenderSyncDefault", material_source + material_reference)

    def test_public_documentation_has_only_explicit_legacy_name_exceptions(self) -> None:
        allowed = {
            "docs/COMPATIBILITY.md": {"BlenderSync Test.cmd"},
        }
        documents = (
            ROOT / "README.md",
            ROOT / "LICENSE.md",
            ROOT / "THIRD_PARTY_NOTICES.md",
            *(ROOT / "docs").glob("*.md"),
        )
        violations = []
        for path in documents:
            relative = path.relative_to(ROOT).as_posix()
            for line_number, line in enumerate(
                path.read_text(encoding="utf-8-sig").splitlines(), 1
            ):
                if not re.search(r"\bBlenderSync\b", line):
                    continue
                if any(token in line for token in allowed.get(relative, set())):
                    continue
                violations.append(f"{relative}:{line_number}:{line.strip()}")
        self.assertEqual([], violations)

    def test_product_source_has_only_explicit_internal_brand_exceptions(self) -> None:
        allowed = {
            "blender_addon/blendersync_vnext/blender/common/log.py": {
                'prefix = f"[BlenderSync]',
            },
            "blender_addon/blendersync_vnext/blender/material_resource/content_v1.py": {
                '/ "BlenderSync" /',
            },
            "unity/TriSync/Scripts/Diagnostics/BlenderSyncLog.cs": {
                '$"[BlenderSync]',
            },
            "unity/TriSync/Scripts/RootMotion/HumanoidClipConverterView.cs": {
                '"BlenderSync Legacy Humanoid Goals"',
            },
        }
        product_files = (
            *ADDON_ROOT.rglob("*.py"),
            *UNITY_ROOT.rglob("*.cs"),
            *UNITY_ROOT.rglob("*.shader"),
            *UNITY_ROOT.rglob("*.hlsl"),
        )
        violations = []
        for path in product_files:
            relative = path.relative_to(ROOT).as_posix()
            for line_number, line in enumerate(
                path.read_text(encoding="utf-8-sig").splitlines(), 1
            ):
                if not re.search(r"\bBlenderSync\b", line):
                    continue
                if any(token in line for token in allowed.get(relative, set())):
                    continue
                violations.append(f"{relative}:{line_number}:{line.strip()}")
        self.assertEqual([], violations)

    def test_compatibility_identifiers_remain_unchanged(self) -> None:
        settings = _source(
            "blender_addon/blendersync_vnext/blender/scene_sync/settings.py"
        )
        session = _source("unity/TriSync/Scripts/UI/SessionPanel.cs")
        localization = _source(
            "unity/TriSync/Scripts/Localization/BlenderSyncLocalization.cs"
        )
        diagnostics = _source(
            "unity/TriSync/Scripts/Diagnostics/BlenderSyncLog.cs"
        )
        smoothing = _source(
            "unity/TriSync/Scripts/SceneSyncCore/TransformSmoothingPreferences.cs"
        )
        panel = _source("blender_addon/blendersync_vnext/blender/ui/session_panel.py")

        self.assertIn('ADDON_PACKAGE = "blendersync_vnext"', settings)
        self.assertIn('bpy.types.Scene.blendersync_sync_enabled', settings)
        self.assertIn('"BlenderSyncVNext.MainTab"', session)
        self.assertIn('"BlenderSyncVNext.Language"', localization)
        self.assertIn('"BlenderSyncVNext.TransformSmoothingEnabled"', smoothing)
        self.assertIn('"BlenderSyncVNext.TransformSmoothingTime"', smoothing)
        self.assertIn('"BlenderSyncVNext.TransformSmoothingCurve"', smoothing)
        self.assertIn('"[BlenderSync]', diagnostics)
        self.assertIn('bl_idname = "BS_PT_session_panel"', panel)
        self.assertIn(
            '"blendersync-diagnostics-v2"',
            _source("blender_addon/blendersync_vnext/blender/ui/state_view.py"),
        )
        self.assertIn(
            'ASSET_BRIDGE_CONTRACT_VERSION = "asset-bridge-v1"',
            _source("blender_addon/blendersync_vnext/blender/common/constants.py"),
        )
        asset_container = _source(
            "unity/TriSync/Scripts/AssetBridgeCore/AssetContainerService.cs"
        )
        material_source = _source(
            "unity/TriSync/Scripts/SceneSyncCore/MaterialContentV1ApplyService.cs"
        )
        unity_test_runner = _source("tools/Test-UnityEditMode.ps1")
        self.assertIn("namespace BlenderSyncVNext.AssetBridgeCore", asset_container)
        self.assertIn('"Temp", "BlenderSyncVNext"', material_source)
        self.assertIn(r"Assets\BlenderSyncVNextTests", unity_test_runner)
        self.assertTrue((UNITY_ROOT / "Scripts" / "BlenderSyncVNext.Editor.asmdef").is_file())
        self.assertTrue(
            (ADDON_ROOT / "native" / "artifacts" / "win_amd64" / "cpython-311" / "blendersync_native.pyd").is_file()
        )


if __name__ == "__main__":
    unittest.main()
