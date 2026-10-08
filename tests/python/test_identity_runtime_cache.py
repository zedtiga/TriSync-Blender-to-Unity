from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock


ADDON_ROOT = Path(__file__).resolve().parents[2] / "blender_addon" / "blendersync_vnext"
sys.path.insert(0, str(ADDON_ROOT))

from blender.asset_registry import core as asset_registry
from blender.identity import core as identity


class FakeDatablock(dict):
    def __init__(self, name: str, session_uid: int, pointer: int, **values) -> None:
        super().__init__(values)
        self.name = name
        self.session_uid = session_uid
        self._pointer = pointer

    def as_pointer(self) -> int:
        return self._pointer


class IdentityRuntimeCacheTests(unittest.TestCase):
    def setUp(self) -> None:
        identity.reset_runtime_identity_cache()
        asset_registry.reset_runtime_asset_registry_cache()

    def tearDown(self) -> None:
        identity.reset_runtime_identity_cache()
        asset_registry.reset_runtime_asset_registry_cache()

    def test_reused_pointer_does_not_bypass_duplicate_instance_id_detection(self) -> None:
        shared_id = "shared-instance"
        original = FakeDatablock(
            "Original",
            session_uid=10,
            pointer=77,
            blendersync_instance_id=shared_id,
        )
        scene = SimpleNamespace(objects=[original])
        fake_bpy = SimpleNamespace(context=SimpleNamespace(scene=scene))

        with mock.patch.object(identity, "bpy", fake_bpy):
            self.assertEqual(shared_id, identity.ensure_instance_id(original))

            reused = FakeDatablock(
                "Reused",
                session_uid=30,
                pointer=77,
                blendersync_instance_id=shared_id,
            )
            duplicate = FakeDatablock(
                "Duplicate",
                session_uid=20,
                pointer=88,
                blendersync_instance_id=shared_id,
            )
            scene.objects = [reused, duplicate]
            with mock.patch.object(identity, "_new_uuid", return_value="replacement-instance"):
                self.assertEqual("replacement-instance", identity.ensure_instance_id(reused))

        self.assertEqual(shared_id, identity.get_instance_id(duplicate))
        self.assertEqual("replacement-instance", identity.get_instance_id(reused))

    def test_scene_cache_uses_session_uid_instead_of_reused_pointer(self) -> None:
        first = self._scene_with_fingerprints(101, 55, {"asset-a": "fingerprint-a"})
        second = self._scene_with_fingerprints(202, 55, {"asset-b": "fingerprint-b"})
        context = SimpleNamespace(scene=first)
        fake_bpy = SimpleNamespace(context=context)

        with mock.patch.object(asset_registry, "bpy", fake_bpy):
            self.assertEqual("fingerprint-a", asset_registry.get_asset_source_fingerprint("asset-a"))
            context.scene = second
            self.assertEqual("fingerprint-b", asset_registry.get_asset_source_fingerprint("asset-b"))
            self.assertIsNone(asset_registry.get_asset_source_fingerprint("asset-a"))

    @staticmethod
    def _scene_with_fingerprints(session_uid: int, pointer: int, fingerprints: dict[str, str]) -> FakeDatablock:
        scene = FakeDatablock("Scene", session_uid=session_uid, pointer=pointer)
        scene[asset_registry.ASSET_FINGERPRINT_REGISTRY_SCENE_KEY] = json.dumps({
            "schemaVersion": asset_registry.ASSET_FINGERPRINT_REGISTRY_SCHEMA_VERSION,
            "fingerprints": fingerprints,
        })
        return scene


if __name__ == "__main__":
    unittest.main()
