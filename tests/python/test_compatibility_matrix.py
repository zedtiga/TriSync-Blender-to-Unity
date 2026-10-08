from __future__ import annotations

import ast
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
MATRIX_PATH = ROOT / "tools" / "compatibility-matrix.json"
BLENDER_SMOKE_PATH = ROOT / "tests" / "blender" / "compatibility_smoke.py"
MATRIX_RUNNER_PATH = ROOT / "tools" / "Test-CompatibilityMatrix.ps1"
PROJECT_RUNNER_PATH = ROOT / "tools" / "Test-Project.ps1"
UNITY_RUNNER_PATH = ROOT / "tools" / "Test-UnityEditMode.ps1"
UNITY_SYNC_PATH = ROOT / "tools" / "Sync-UnityRuntime.ps1"
PROCESS_HELPERS_PATH = ROOT / "tools" / "ProcessHelpers.ps1"
PROCESS_HELPERS_TEST_PATH = ROOT / "tools" / "Test-ProcessHelpers.ps1"
LOGGING_POLICY_PATH = ROOT / "tools" / "Test-LoggingPolicy.ps1"
WORKSPACE_SYNC_PATH = ROOT / "tools" / "Sync-CompatibilityWorkspaces.ps1"
BLENDER_NORMAL_ADDON_SETUP_PATH = ROOT / "tools" / "blender_normal_addon_setup.py"
ADDON_INIT_PATH = ROOT / "blender_addon" / "blendersync_vnext" / "__init__.py"
PUBLIC_SUPPORT_DOC_PATHS = (
    ROOT / "README.md",
    ROOT / "docs" / "README.md",
    ROOT / "docs" / "QUICK_START.md",
    ROOT / "docs" / "COMPATIBILITY.md",
    ROOT / "docs" / "TESTING.md",
    ROOT / "docs" / "RELEASE.md",
)

BLENDER_CHECKS = [
    "addon-register",
    "mesh-api",
    "geometry-nodes-api",
    "evaluated-mesh-api",
    "evaluated-material-api",
    "animation-action-api",
    "bone-pose-numeric-stability",
    "rig-shape-key-animation-api",
    "rigged-blendshape-weight-api",
    "ui-icon-api",
    "localization-facade",
    "logging-facade",
    "native-probe",
    "addon-unregister",
]


class CompatibilityMatrixDefinitionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.matrix = json.loads(MATRIX_PATH.read_text(encoding="utf-8"))
        self.profiles = {profile["id"]: profile for profile in self.matrix["profiles"]}
        self.interop_pairs = {pair["id"]: pair for pair in self.matrix["interopPairs"]}

    def test_matrix_defines_every_planned_minor_profile(self) -> None:
        self.assertEqual(1, self.matrix["schemaVersion"])
        self.assertEqual(
            {
                "blender-4.2",
                "blender-4.3",
                "blender-4.4",
                "blender-4.5",
                "blender-5.0",
                "blender-5.1",
                "blender-5.2",
                "unity-6000.0",
                "unity-6000.1",
                "unity-6000.2",
                "unity-6000.3",
                "unity-6000.4",
                "unity-6000.5",
            },
            set(self.profiles),
        )
        self.assertEqual("current-baseline", self.profiles["blender-5.0"]["verificationStatus"])
        self.assertEqual("current-baseline", self.profiles["unity-6000.3"]["verificationStatus"])
        for profile_id in {
            "blender-4.2",
            "blender-4.3",
            "blender-4.4",
            "blender-4.5",
            "blender-5.1",
            "blender-5.2",
            "unity-6000.0",
            "unity-6000.1",
            "unity-6000.2",
            "unity-6000.4",
            "unity-6000.5",
        }:
            self.assertEqual("verified", self.profiles[profile_id]["verificationStatus"])
        self.assertEqual("17.0", self.profiles["unity-6000.0"]["urpVersion"])
        self.assertEqual("17.1", self.profiles["unity-6000.1"]["urpVersion"])
        self.assertEqual("17.2", self.profiles["unity-6000.2"]["urpVersion"])
        self.assertEqual("17.3", self.profiles["unity-6000.3"]["urpVersion"])
        self.assertEqual("17.4", self.profiles["unity-6000.4"]["urpVersion"])
        self.assertEqual("17.5", self.profiles["unity-6000.5"]["urpVersion"])

    def test_matrix_defines_endpoint_coverage_interop_pairs(self) -> None:
        expected_pairs = {
            "floor-floor": ("blender-4.2", "unity-6000.0", "verified"),
            "floor-unity-6000.1": ("blender-4.2", "unity-6000.1", "verified"),
            "floor-unity-6000.2": ("blender-4.2", "unity-6000.2", "verified"),
            "floor-current": ("blender-4.2", "unity-6000.3", "verified"),
            "floor-unity-6000.4": ("blender-4.2", "unity-6000.4", "verified"),
            "floor-ceiling": ("blender-4.2", "unity-6000.5", "verified"),
            "blender-4.3-floor": ("blender-4.3", "unity-6000.0", "verified"),
            "blender-4.3-ceiling": ("blender-4.3", "unity-6000.5", "verified"),
            "blender-4.4-floor": ("blender-4.4", "unity-6000.0", "verified"),
            "blender-4.4-ceiling": ("blender-4.4", "unity-6000.5", "verified"),
            "blender-4.5-floor": ("blender-4.5", "unity-6000.0", "verified"),
            "blender-4.5-ceiling": ("blender-4.5", "unity-6000.5", "verified"),
            "current-floor": ("blender-5.0", "unity-6000.0", "verified"),
            "current-current": ("blender-5.0", "unity-6000.3", "current-baseline"),
            "current-ceiling": ("blender-5.0", "unity-6000.5", "verified"),
            "blender-5.1-floor": ("blender-5.1", "unity-6000.0", "verified"),
            "blender-5.1-ceiling": ("blender-5.1", "unity-6000.5", "verified"),
            "ceiling-floor": ("blender-5.2", "unity-6000.0", "verified"),
            "ceiling-unity-6000.1": ("blender-5.2", "unity-6000.1", "verified"),
            "ceiling-unity-6000.2": ("blender-5.2", "unity-6000.2", "verified"),
            "ceiling-current": ("blender-5.2", "unity-6000.3", "verified"),
            "ceiling-unity-6000.4": ("blender-5.2", "unity-6000.4", "verified"),
            "ceiling-ceiling": ("blender-5.2", "unity-6000.5", "verified"),
        }
        self.assertEqual(set(expected_pairs), set(self.interop_pairs))
        self.assertEqual(23, len(self.interop_pairs))
        for pair in self.interop_pairs.values():
            self.assertEqual("blender", self.profiles[pair["blenderProfile"]]["product"])
            self.assertEqual("unity", self.profiles[pair["unityProfile"]]["product"])
            self.assertEqual("level-1-smoke", pair["check"])
        for pair_id, (blender_id, unity_id, status) in expected_pairs.items():
            pair = self.interop_pairs[pair_id]
            self.assertEqual(blender_id, pair["blenderProfile"])
            self.assertEqual(unity_id, pair["unityProfile"])
            self.assertEqual(status, pair["verificationStatus"])

        perimeter_endpoints = {
            (blender_id, unity_id)
            for blender_id, blender in self.profiles.items()
            for unity_id, unity in self.profiles.items()
            if blender["product"] == "blender"
            and unity["product"] == "unity"
            and (
                blender_id in {"blender-4.2", "blender-5.2"}
                or unity_id in {"unity-6000.0", "unity-6000.5"}
            )
        }
        expected_endpoints = perimeter_endpoints | {("blender-5.0", "unity-6000.3")}
        actual_endpoints = {
            (pair["blenderProfile"], pair["unityProfile"])
            for pair in self.interop_pairs.values()
        }
        self.assertEqual(expected_endpoints, actual_endpoints)

    def test_profiles_use_environment_configuration_and_required_checks(self) -> None:
        environment_variables: set[str] = set()
        for profile in self.profiles.values():
            self.assertIn(
                profile["verificationStatus"],
                {"candidate", "verified", "current-baseline"},
            )
            if profile["product"] == "blender":
                variables = [profile["executableEnvironmentVariable"]]
                self.assertEqual(BLENDER_CHECKS, profile["checks"])
            else:
                variables = [
                    profile["editorEnvironmentVariable"],
                    profile["projectEnvironmentVariable"],
                ]
                self.assertEqual(["editor-compile", "editmode"], profile["checks"])
            for variable in variables:
                self.assertTrue(variable.startswith("BLENDERSYNC_"))
                self.assertNotIn(variable, environment_variables)
                environment_variables.add(variable)

    def test_public_support_statement_matches_the_verified_range(self) -> None:
        addon_tree = ast.parse(
            ADDON_INIT_PATH.read_text(encoding="utf-8-sig"),
            filename=str(ADDON_INIT_PATH),
        )
        bl_info = None
        for node in addon_tree.body:
            if not isinstance(node, ast.Assign):
                continue
            if any(isinstance(target, ast.Name) and target.id == "bl_info" for target in node.targets):
                bl_info = ast.literal_eval(node.value)
                break
        self.assertIsNotNone(bl_info)
        self.assertEqual((4, 2, 0), bl_info["blender"])

        expected_ranges = (
            ("Blender", "`4.2.x` through `5.2.x`"),
            ("Unity", "`6000.0.x` through `6000.5.x`"),
            ("Universal Render Pipeline", "`17.0.x` through `17.5.x`"),
        )
        for path in PUBLIC_SUPPORT_DOC_PATHS:
            source = path.read_text(encoding="utf-8-sig")
            for product, version_range in expected_ranges:
                self.assertIn(product, source, f"{path} is missing support product: {product}")
                self.assertIn(
                    version_range,
                    source,
                    f"{path} is missing support range: {version_range}",
                )

    @unittest.skipUnless(os.name == "nt", "PowerShell fixture requires Windows")
    def test_unity_sync_repairs_stale_generated_project_references(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source = root / "source"
            scripts = source / "Scripts"
            runtime_scripts = scripts / "Runtime"
            urp_scripts = scripts / "Editor"
            project = root / "project"
            runtime_root = project / "Assets" / "TriSync"
            runtime_scripts.mkdir(parents=True)
            urp_scripts.mkdir(parents=True)
            project.mkdir(parents=True)

            runtime_guid = "11111111111111111111111111111111"
            (runtime_scripts / "BlenderSyncVNext.Runtime.asmdef").write_text(
                json.dumps(
                    {
                        "name": "BlenderSyncVNext.Runtime",
                        "references": [],
                    }
                ),
                encoding="utf-8",
            )
            (runtime_scripts / "BlenderSyncVNext.Runtime.asmdef.meta").write_text(
                f"fileFormatVersion: 2\nguid: {runtime_guid}\n",
                encoding="utf-8",
            )
            (runtime_scripts / "RuntimeMarker.cs").write_text(
                "internal sealed class RuntimeMarker {}\n", encoding="utf-8"
            )
            (scripts / "BlenderSyncVNext.Editor.asmdef").write_text(
                json.dumps(
                    {
                        "name": "BlenderSyncVNext.Editor",
                        "references": [f"GUID:{runtime_guid}"],
                    }
                ),
                encoding="utf-8",
            )
            (scripts / "EditorMarker.cs").write_text(
                "internal sealed class EditorMarker {}\n", encoding="utf-8"
            )
            (urp_scripts / "BlenderSyncVNext.URP.Editor.asmdef").write_text(
                json.dumps(
                    {
                        "name": "BlenderSyncVNext.URP.Editor",
                        "references": ["BlenderSyncVNext.Editor"],
                    }
                ),
                encoding="utf-8",
            )
            (urp_scripts / "UrpMarker.cs").write_text(
                "internal sealed class UrpMarker {}\n", encoding="utf-8"
            )

            (project / "Assembly-CSharp.csproj").write_text(
                '<Project Sdk="Microsoft.NET.Sdk">\n'
                "  <ItemGroup>\n"
                '    <Compile Include="Assets\\DeletedMarker.cs" />\n'
                '    <None Include="Assets\\BlenderSyncVNext\\Retired.asset" />\n'
                '    <ProjectReference Include="BlenderSyncVNext.Obsolete.csproj">\n'
                '      <Name>BlenderSyncVNext.Obsolete</Name>\n'
                '    </ProjectReference>\n'
                "  </ItemGroup>\n"
                "</Project>\n",
                encoding="utf-8",
            )
            (project / "Assembly-CSharp-Editor.csproj").write_text(
                '<Project Sdk="Microsoft.NET.Sdk"></Project>\n', encoding="utf-8"
            )
            (project / "BlenderSyncVNext.Runtime.csproj").write_text(
                '<Project Sdk="Microsoft.NET.Sdk">\n'
                "  <ItemGroup>\n"
                '    <Compile Include="Assets\\TriSync\\Scripts\\Runtime\\RuntimeMarker.cs" />\n'
                "  </ItemGroup>\n"
                "</Project>\n",
                encoding="utf-8",
            )
            (project / "BlenderSyncVNext.Editor.csproj").write_text(
                '<Project Sdk="Microsoft.NET.Sdk">\n'
                "  <ItemGroup>\n"
                '    <Compile Include="Assets\\TriSync\\Scripts\\EditorMarker.cs" />\n'
                '    <ProjectReference Include="BlenderSyncVNext.Obsolete.csproj">\n'
                '      <Name>BlenderSyncVNext.Obsolete</Name>\n'
                '    </ProjectReference>\n'
                "  </ItemGroup>\n"
                "</Project>\n",
                encoding="utf-8",
            )
            (project / "BlenderSyncVNext.URP.Editor.csproj").write_text(
                '<Project Sdk="Microsoft.NET.Sdk">\n'
                "  <ItemGroup>\n"
                '    <Compile Include="Assets\\TriSync\\Scripts\\Editor\\UrpMarker.cs" />\n'
                "  </ItemGroup>\n"
                "  <ItemGroup>\n"
                '    <ProjectReference Include="BlenderSyncVNext.Runtime.csproj">\n'
                '      <Name>BlenderSyncVNext.Runtime</Name>\n'
                '    </ProjectReference>\n'
                "  </ItemGroup>\n"
                "</Project>\n",
                encoding="utf-8",
            )

            completed = subprocess.run(
                [
                    "powershell",
                    "-NoProfile",
                    "-ExecutionPolicy",
                    "Bypass",
                    "-File",
                    str(UNITY_SYNC_PATH),
                    "-SourceRoot",
                    str(source),
                    "-RuntimeRoot",
                    str(runtime_root),
                    "-UnityProjectRoot",
                    str(project),
                    "-RemoveStaleFiles",
                    "-SkipBuild",
                ],
                cwd=ROOT,
                capture_output=True,
                timeout=60,
            )
            output = (completed.stdout + completed.stderr).decode(errors="replace")
            self.assertEqual(0, completed.returncode, output)
            self.assertIn("missing_asset_compile_items_removed=1", output)
            self.assertIn(
                "missing_asset_items project=Assembly-CSharp.csproj removed=1",
                output,
            )
            self.assertIn("retired_product_project_references_removed=2", output)
            self.assertIn("asmdef_project_references_removed=1", output)
            self.assertIn("asmdef_project_references_added=2", output)

            assembly = (project / "Assembly-CSharp.csproj").read_text(encoding="utf-8")
            editor = (project / "BlenderSyncVNext.Editor.csproj").read_text(
                encoding="utf-8"
            )
            urp = (project / "BlenderSyncVNext.URP.Editor.csproj").read_text(
                encoding="utf-8"
            )
            self.assertNotIn("DeletedMarker.cs", assembly)
            self.assertNotIn("Assets\\BlenderSyncVNext\\Retired.asset", assembly)
            self.assertNotIn("BlenderSyncVNext.Obsolete.csproj", assembly)
            self.assertIn(
                '<ProjectReference Include="BlenderSyncVNext.Runtime.csproj" />',
                editor,
            )
            self.assertNotIn("BlenderSyncVNext.Obsolete.csproj", editor)
            self.assertIn(
                '<ProjectReference Include="BlenderSyncVNext.Editor.csproj" />',
                urp,
            )
            self.assertNotIn(
                '<ProjectReference Include="BlenderSyncVNext.Runtime.csproj" />',
                urp,
            )

    def test_smoke_and_runner_entrypoints_are_syntax_checked_or_wired(self) -> None:
        ast.parse(BLENDER_SMOKE_PATH.read_text(encoding="utf-8"), filename=str(BLENDER_SMOKE_PATH))
        matrix_runner = MATRIX_RUNNER_PATH.read_text(encoding="utf-8-sig")
        project_runner = PROJECT_RUNNER_PATH.read_text(encoding="utf-8-sig")
        unity_runner = UNITY_RUNNER_PATH.read_text(encoding="utf-8-sig")
        unity_sync = UNITY_SYNC_PATH.read_text(encoding="utf-8-sig")
        process_helpers = PROCESS_HELPERS_PATH.read_text(encoding="utf-8-sig")
        process_helpers_test = PROCESS_HELPERS_TEST_PATH.read_text(encoding="utf-8-sig")
        logging_policy = LOGGING_POLICY_PATH.read_text(encoding="utf-8-sig")
        workspace_sync = WORKSPACE_SYNC_PATH.read_text(encoding="utf-8-sig")
        normal_addon_setup = BLENDER_NORMAL_ADDON_SETUP_PATH.read_text(encoding="utf-8")
        self.assertIn("[CmdletBinding()]", matrix_runner)
        self.assertIn("Test-CompatibilityMatrix.ps1') -ValidateOnly", project_runner)
        self.assertIn("Test-ProcessHelpers.ps1", project_runner)
        self.assertIn("Test-LoggingPolicy.ps1", project_runner)
        self.assertIn("Test-ReleasePackage.ps1", project_runner)
        self.assertIn("[string]$ResultDirectory", unity_runner)
        self.assertIn("[int]$TimeoutSeconds = 600", unity_runner)
        self.assertIn("Wait-ChildProcess", unity_runner)
        self.assertIn("function Test-ProjectHasCompileItems", unity_sync)
        self.assertIn("dotnet build $buildProject --nologo --verbosity minimal --warnaserror", unity_sync)
        self.assertIn("dotnet_build_skip_empty", unity_sync)
        self.assertIn("function Get-LocalAsmdefProjectMap", unity_sync)
        self.assertIn("function Get-ExpectedProjectReferencesForAsmdef", unity_sync)
        self.assertIn("function Remove-MissingAssetCompileItems", unity_sync)
        self.assertIn("function Remove-MissingAssetItems", unity_sync)
        self.assertIn("function Remove-StaleProjectReferencesByExpected", unity_sync)
        self.assertIn("function Add-MissingProjectReferencesByExpected", unity_sync)
        self.assertIn("missing_asset_compile_items_removed", unity_sync)
        self.assertIn("asmdef_project_references_added", unity_sync)
        self.assertIn("Assert-ChecksMatch", matrix_runner)
        self.assertIn("Read-ReleaseMetadata", matrix_runner)
        self.assertIn("expectedNativeVersion", matrix_runner)
        self.assertIn("Blender native capabilities", matrix_runner)
        self.assertIn("platformTag=$nativePlatformTag", matrix_runner)
        self.assertIn("[int]$BlenderTimeoutSeconds = 300", matrix_runner)
        self.assertIn("[int]$UnityTimeoutSeconds = 600", matrix_runner)
        self.assertIn("function Convert-ToProcessArgument", process_helpers)
        self.assertIn("function Start-RedirectedChildProcess", process_helpers)
        self.assertIn("function Complete-RedirectedChildProcess", process_helpers)
        self.assertIn("function Stop-ChildProcessTree", process_helpers)
        self.assertIn('ArgumentList "/PID $processId /T /F"', process_helpers)
        self.assertIn("function Wait-ChildProcess", process_helpers)
        self.assertIn("trailing slash path", process_helpers_test)
        self.assertIn("Process helper timeout probe", process_helpers_test)
        self.assertIn("logging_debt_increased", logging_policy)
        self.assertIn("logging_debt_reduced_update_baseline", logging_policy)
        self.assertIn("terminate its process tree", process_helpers_test)
        self.assertIn("Start-RedirectedChildProcess", matrix_runner)
        self.assertIn("Complete-RedirectedChildProcess", matrix_runner)
        self.assertIn("blender-smoke.stdout.log", matrix_runner)
        self.assertIn("blender-smoke.stderr.log", matrix_runner)
        self.assertIn("scripts\\addons\\blendersync_vnext", workspace_sync)
        self.assertIn("Set-BlenderNormalAddonLink", workspace_sync)
        self.assertIn("New-Item -ItemType Junction", workspace_sync)
        self.assertIn("blendersync-managed-backups", workspace_sync)
        self.assertIn("Enable-BlenderAddonForNormalLaunch", workspace_sync)
        ast.parse(normal_addon_setup, filename=str(BLENDER_NORMAL_ADDON_SETUP_PATH))
        self.assertIn('addon_utils.enable("blendersync_vnext", default_set=True)', normal_addon_setup)
        self.assertIn("bpy.ops.wm.save_userpref()", normal_addon_setup)
        self.assertIn("BLENDERSYNC_NORMAL_RESTART_OK", normal_addon_setup)
        self.assertIn("BlenderSync Test.cmd", workspace_sync)
        self.assertIn("PreferProcessEnvironment", workspace_sync)
        self.assertIn("Assert-VersionPrefix", workspace_sync)
        self.assertIn("Get-UnityUrpVersion", workspace_sync)
        self.assertIn("Sync-BlenderAddon.ps1", workspace_sync)
        self.assertIn("Sync-UnityRuntime.ps1", workspace_sync)
        self.assertIn("-SkipProjectPatch", workspace_sync)
        self.assertIn("-SkipBuild", workspace_sync)

        blender_python_runner = (ROOT / "tools" / "Test-BlenderPython.ps1").read_text(
            encoding="utf-8-sig"
        )
        self.assertIn('encoding="utf-8-sig"', blender_python_runner)
        self.assertIn("feature_version=(3, 11)", blender_python_runner)

        smoke_source = BLENDER_SMOKE_PATH.read_text(encoding="utf-8")
        self.assertIn('hasattr(bpy.types, "BS_PT_session_panel")', smoke_source)
        self.assertNotIn('hasattr(bpy.types, "BS_PT_SessionPanel")', smoke_source)
        self.assertIn("from blender.native.mesh_extractor import get_native_status", smoke_source)
        self.assertIn('"platformTag":', smoke_source)
        self.assertIn('"capabilities":', smoke_source)
        for check in BLENDER_CHECKS:
            self.assertIn(f'append("{check}")', smoke_source)


if __name__ == "__main__":
    unittest.main()
