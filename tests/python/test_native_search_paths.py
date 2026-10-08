from __future__ import annotations

import sys
import unittest
from pathlib import Path


ADDON_ROOT = Path(__file__).resolve().parents[2] / "blender_addon" / "blendersync_vnext"
REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ADDON_ROOT))

from blender.native import mesh_extractor


class NativeSearchTagTests(unittest.TestCase):
    def test_platform_tags_cover_the_four_tracked_artifact_targets(self) -> None:
        cases = (
            ("win32", "AMD64", "win_amd64"),
            ("linux", "x86_64", "linux_x86_64"),
            ("darwin", "arm64", "macos_arm64"),
            ("darwin", "x86_64", "macos_x86_64"),
        )

        for platform_name, machine, expected in cases:
            with self.subTest(platform_name=platform_name, machine=machine):
                self.assertEqual(
                    expected,
                    mesh_extractor._native_platform_tag(
                        platform_name=platform_name,
                        machine=machine,
                    ),
                )

    def test_unsupported_platform_or_architecture_does_not_guess(self) -> None:
        self.assertIsNone(
            mesh_extractor._native_platform_tag(platform_name="linux", machine="aarch64")
        )
        self.assertIsNone(
            mesh_extractor._native_platform_tag(platform_name="freebsd", machine="x86_64")
        )

    def test_platform_search_dirs_preserve_current_then_abi3_fallback_order(self) -> None:
        root = Path("addon-root")
        cases = (
            ("win32", "AMD64", "win_amd64"),
            ("linux", "x86_64", "linux_x86_64"),
            ("darwin", "arm64", "macos_arm64"),
            ("darwin", "x86_64", "macos_x86_64"),
        )

        for platform_name, machine, platform_tag in cases:
            with self.subTest(platform_name=platform_name, machine=machine):
                platform_root = root / "native" / "artifacts" / platform_tag
                self.assertEqual(
                    [
                        platform_root / "cpython-313",
                        platform_root / "cpython-311",
                        platform_root,
                        root / "native" / "artifacts",
                    ],
                    mesh_extractor._native_search_dirs(
                        addon_root=root,
                        platform_name=platform_name,
                        machine=machine,
                        implementation_name="cpython",
                        cache_tag="cpython-313",
                        version_info=(3, 13),
                    ),
                )

    def test_unsupported_platform_searches_only_the_generic_artifact_root(self) -> None:
        root = Path("addon-root")
        self.assertEqual(
            [root / "native" / "artifacts"],
            mesh_extractor._native_search_dirs(
                addon_root=root,
                platform_name="linux",
                machine="aarch64",
                implementation_name="cpython",
                cache_tag="cpython-313",
                version_info=(3, 13),
            ),
        )

    def test_native_binary_extensions_are_protected_from_text_conversion(self) -> None:
        attributes = (REPO_ROOT / ".gitattributes").read_text(encoding="utf-8-sig")
        self.assertIn("*.pyd binary", attributes)
        self.assertIn("*.so binary", attributes)
        self.assertIn("*.dylib binary", attributes)

    def test_cpython_311_uses_single_matching_tag(self) -> None:
        self.assertEqual(
            ["cpython-311"],
            mesh_extractor._native_search_tags(
                implementation_name="cpython",
                cache_tag="cpython-311",
                version_info=(3, 11),
            ),
        )

    def test_newer_cpython_prefers_current_tag_then_abi3_floor(self) -> None:
        self.assertEqual(
            ["cpython-313", "cpython-311"],
            mesh_extractor._native_search_tags(
                implementation_name="cpython",
                cache_tag="cpython-313",
                version_info=(3, 13),
            ),
        )

    def test_older_cpython_does_not_use_abi3_floor(self) -> None:
        self.assertEqual(
            ["cpython-310"],
            mesh_extractor._native_search_tags(
                implementation_name="cpython",
                cache_tag="cpython-310",
                version_info=(3, 10),
            ),
        )

    def test_non_cpython_does_not_use_cpython_abi3_floor(self) -> None:
        self.assertEqual(
            ["pypy313-pp73"],
            mesh_extractor._native_search_tags(
                implementation_name="pypy",
                cache_tag="pypy313-pp73",
                version_info=(3, 13),
            ),
        )


if __name__ == "__main__":
    unittest.main()
