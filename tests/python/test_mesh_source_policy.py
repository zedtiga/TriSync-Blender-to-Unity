from __future__ import annotations

import sys
import unittest
from pathlib import Path
from types import SimpleNamespace


ADDON_ROOT = Path(__file__).resolve().parents[2] / "blender_addon" / "blendersync_vnext"
sys.path.insert(0, str(ADDON_ROOT))

from blender.ui import object_context_builders


class FakeMeshObject:
    def __init__(self, *, modifiers=None, shape_keys: bool = False) -> None:
        key_blocks = [object(), object()] if shape_keys else []
        self.data = SimpleNamespace(
            shape_keys=SimpleNamespace(key_blocks=key_blocks) if shape_keys else None,
        )
        self.modifiers = list(modifiers or [])


def visible_modifier(name: str = "Subdivision"):
    return SimpleNamespace(name=name, type="SUBSURF", show_viewport=True)


class MeshSourcePolicyTests(unittest.TestCase):
    def test_legacy_scene_override_is_ignored(self) -> None:
        self.assertEqual("auto", object_context_builders._get_scene_mesh_source())

    def test_auto_uses_original_without_visible_modifiers(self) -> None:
        source, reason, modifiers = object_context_builders._resolve_mesh_source_for_object(
            FakeMeshObject(),
            "auto",
            include_rigged_payload=False,
        )
        self.assertEqual(("original", "auto_no_active_modifiers", []), (source, reason, modifiers))

    def test_auto_uses_evaluated_with_visible_modifiers(self) -> None:
        source, reason, modifiers = object_context_builders._resolve_mesh_source_for_object(
            FakeMeshObject(modifiers=[visible_modifier()]),
            "auto",
            include_rigged_payload=False,
        )
        self.assertEqual("evaluated", source)
        self.assertEqual("auto_active_modifiers", reason)
        self.assertEqual(["Subdivision:SUBSURF"], modifiers)

    def test_shape_keys_keep_original_topology(self) -> None:
        source, reason, _modifiers = object_context_builders._resolve_mesh_source_for_object(
            FakeMeshObject(modifiers=[visible_modifier()], shape_keys=True),
            "auto",
            include_rigged_payload=False,
        )
        self.assertEqual(("original", "shape_keys_original"), (source, reason))

    def test_skin_and_rigged_payloads_keep_original_topology(self) -> None:
        source, reason, _modifiers = object_context_builders._resolve_mesh_source_for_object(
            FakeMeshObject(modifiers=[visible_modifier()]),
            "auto",
            include_rigged_payload=True,
            rigged_payload={},
        )
        self.assertEqual(("original", "skin_or_rigged_original"), (source, reason))


if __name__ == "__main__":
    unittest.main()
