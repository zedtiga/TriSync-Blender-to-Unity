from __future__ import annotations

import ast
import importlib.util
import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
WORKFLOW_PATH = ROOT / ".github" / "workflows" / "build-native.yml"
SMOKE_PATH = ROOT / "tools" / "native_import_smoke.py"
TARGETS_PATH = ROOT / "tools" / "NativeArtifactTargets.ps1"
METADATA_PATH = ROOT / "tools" / "release_metadata.py"
NATIVE_CRATE_ROOT = (
    ROOT / "blender_addon" / "blendersync_vnext" / "native" / "blendersync_native"
)


def _load_release_metadata_tool():
    spec = importlib.util.spec_from_file_location("release_metadata", METADATA_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError("release_metadata_import_failed")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _workflow_jobs(workflow: str) -> dict[str, str]:
    jobs_text = workflow.split("jobs:\n", 1)[1]
    matches = list(re.finditer(r"(?m)^  ([a-z0-9-]+):\n", jobs_text))
    return {
        match.group(1): jobs_text[match.end() : matches[index + 1].start()]
        if index + 1 < len(matches)
        else jobs_text[match.end() :]
        for index, match in enumerate(matches)
    }


class NativeBuildWorkflowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.workflow = WORKFLOW_PATH.read_text(encoding="utf-8-sig")
        cls.smoke = SMOKE_PATH.read_text(encoding="utf-8-sig")
        cls.targets = TARGETS_PATH.read_text(encoding="utf-8-sig")
        cls.metadata = _load_release_metadata_tool().load_release_metadata(ROOT)
        cls.jobs = _workflow_jobs(cls.workflow)
        cls.cargo_toml = (NATIVE_CRATE_ROOT / "Cargo.toml").read_text(
            encoding="utf-8-sig"
        )
        cls.native_build = (NATIVE_CRATE_ROOT / "build.rs").read_text(
            encoding="utf-8-sig"
        )

    def test_workflow_is_manual_only(self) -> None:
        trigger_block = self.workflow.split("permissions:", 1)[0]
        event_names = re.findall(r"(?m)^  ([a-z][a-z0-9_-]*):\s*$", trigger_block)
        self.assertEqual(["workflow_dispatch"], event_names)

    def test_workflow_builds_every_native_artifact_target(self) -> None:
        expected = re.findall(
            r"Platform = '([^']+)'; PythonAbi = '([^']+)'; ModuleFile = '([^']+)'",
            self.targets,
        )
        self.assertEqual(4, len(expected))
        for platform_name, python_abi, module_file in expected:
            with self.subTest(platform_name=platform_name):
                self.assertIn(
                    f"native-artifact/{platform_name}/{python_abi}/{module_file}",
                    self.workflow,
                )

        self.assertIn("libblendersync_native.dylib", self.workflow)
        self.assertNotIn(
            "native-artifact/macos_arm64/cpython-311/blendersync_native.dylib",
            self.workflow,
        )
        self.assertNotIn(
            "native-artifact/macos_x86_64/cpython-311/blendersync_native.dylib",
            self.workflow,
        )

    def test_every_job_runs_import_smoke_before_upload(self) -> None:
        self.assertEqual(
            {"windows-x86-64", "linux-x86-64", "macos-universal"},
            set(self.jobs),
        )
        for job_name, job in self.jobs.items():
            with self.subTest(job_name=job_name):
                self.assertIn("python tools/native_import_smoke.py", job)
                self.assertIn("actions/upload-artifact@v4", job)
                self.assertLess(
                    job.index("python tools/native_import_smoke.py"),
                    job.index("actions/upload-artifact@v4"),
                )
                self.assertIn("path: native-artifact/", job)
                self.assertIn("if-no-files-found: error", job)
                self.assertIn("retention-days: 14", job)

        self.assertEqual(
            3,
            len(re.findall(r"(?m)^\s+- name: Import smoke$", self.workflow)),
        )
        self.assertEqual(3, self.workflow.count("python tools/native_import_smoke.py"))
        self.assertEqual(3, self.workflow.count("--module-root"))
        self.assertEqual(3, self.workflow.count("actions/upload-artifact@v4"))
        self.assertEqual(3, self.workflow.count("path: native-artifact/"))
        self.assertEqual(3, self.workflow.count("if-no-files-found: error"))
        self.assertEqual(3, self.workflow.count("retention-days: 14"))
        self.assertIn("version()", self.smoke)
        self.assertIn("capabilities()", self.smoke)
        self.assertIn("load_release_metadata", self.smoke)
        self.assertIn("module_file.parent != module_root", self.smoke)
        cargo_path = (
            ROOT
            / "blender_addon"
            / "blendersync_vnext"
            / "native"
            / "blendersync_native"
            / "Cargo.toml"
        )
        self.assertIn(
            str(self.metadata["nativeVersion"]),
            cargo_path.read_text(encoding="utf-8-sig"),
        )
        ast.parse(self.smoke, filename=str(SMOKE_PATH), feature_version=(3, 11))

    def test_macos_builds_both_architectures_but_smokes_runner_native_arm64(self) -> None:
        self.assertIn("targets: aarch64-apple-darwin,x86_64-apple-darwin", self.workflow)
        self.assertIn("--target aarch64-apple-darwin", self.workflow)
        self.assertIn("--target x86_64-apple-darwin", self.workflow)
        self.assertIn('case "$(uname -m)" in', self.workflow)

    def test_native_crate_configures_extension_module_linking(self) -> None:
        self.assertRegex(
            self.cargo_toml,
            r"(?ms)^\[build-dependencies\]\s+.*^pyo3-build-config\s*=\s*\"0\.22\"$",
        )
        self.assertIn(
            "pyo3_build_config::add_extension_module_link_args();",
            self.native_build,
        )


if __name__ == "__main__":
    unittest.main()
