from __future__ import annotations

import ast
import unittest
from pathlib import Path


SCENE_SYNC_ROOT = (
    Path(__file__).resolve().parents[2]
    / "blender_addon"
    / "blendersync_vnext"
    / "blender"
    / "scene_sync"
)
DOMAIN_MODULES = {
    "object_lifecycle",
    "object_state",
    "material_sync",
    "structure_watch",
    "preview_sync",
}
SHARED_MODULES = {"mesh_context"}


class SceneSyncDomainDependencyTests(unittest.TestCase):
    def test_domain_modules_do_not_import_controller_or_each_other(self) -> None:
        violations: list[str] = []
        for domain_name in sorted(DOMAIN_MODULES | SHARED_MODULES):
            path = SCENE_SYNC_ROOT / f"{domain_name}.py"
            if not path.exists():
                continue
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for node in ast.walk(tree):
                imported_modules: list[str] = []
                if isinstance(node, ast.Import):
                    imported_modules.extend(alias.name for alias in node.names)
                elif isinstance(node, ast.ImportFrom):
                    module = node.module or ""
                    imported_modules.append(module)
                    if module == "blender.scene_sync":
                        imported_modules.extend(f"{module}.{alias.name}" for alias in node.names)
                for imported in imported_modules:
                    leaf = imported.rsplit(".", 1)[-1]
                    if leaf == "controller" or (leaf in DOMAIN_MODULES and leaf != domain_name):
                        violations.append(f"{path.name}:{node.lineno} imports {imported}")

        self.assertEqual([], violations)


if __name__ == "__main__":
    unittest.main()
