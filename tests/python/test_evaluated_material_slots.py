from __future__ import annotations

import sys
import unittest
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from unittest import mock


ADDON_ROOT = Path(__file__).resolve().parents[2] / "blender_addon" / "blendersync_vnext"
sys.path.insert(0, str(ADDON_ROOT))

from blender.identity import ASSET_ID_KEY
from blender.material_resource import slots as material_slots
from blender.material_resource.slots import (
    MaterialExportSnapshot,
    compact_evaluated_material_slots,
    collect_material_export_snapshot,
    material_refs_from_live_context,
)
from blender.resource_update.core import MeshUpdateCore
from blender.rigged_object import builder as rigged_builder
from blender.scene_sync import controller, material_sync, mesh_context
from blender.scene_sync.baseline import set_material_slots_baseline
from blender.ui import object_context_builders


class FakeMaterial(dict):
    def __init__(self, name: str, *, is_evaluated: bool = False, original=None) -> None:
        super().__init__()
        self.name = name
        self.is_evaluated = is_evaluated
        self.original = original


def evaluated_material(name: str) -> tuple[FakeMaterial, FakeMaterial]:
    original = FakeMaterial(name)
    evaluated = FakeMaterial(f"{name}.evaluated", is_evaluated=True, original=original)
    return evaluated, original


class FakeTriangle:
    def __init__(self, vertices, loops, polygon_index: int, material_index: int = 0) -> None:
        self.vertices = tuple(vertices)
        self.loops = tuple(loops)
        self.polygon_index = polygon_index
        self.material_index = material_index


class FakeMesh:
    def __init__(self, materials, triangles, *, vertex_count: int = 3, polygon_count: int | None = None, loop_count: int | None = None) -> None:
        self.materials = list(materials)
        self.loop_triangles = list(triangles)
        self.vertices = [object()] * vertex_count
        self.polygons = [object()] * (len(triangles) if polygon_count is None else polygon_count)
        self.loops = [object()] * (len(triangles) * 3 if loop_count is None else loop_count)
        self.calc_loop_triangles_calls = 0

    def calc_loop_triangles(self) -> None:
        self.calc_loop_triangles_calls += 1


class FakeMatrix:
    def copy(self):
        return self

    def inverted_safe(self):
        return self

    def __matmul__(self, _other):
        return self

    def to_quaternion(self):
        return SimpleNamespace(x=0.0, y=0.0, z=0.0, w=1.0)

    def to_translation(self):
        return SimpleNamespace(x=0.0, y=0.0, z=0.0)

    def to_scale(self):
        return SimpleNamespace(x=1.0, y=1.0, z=1.0)

    def __iter__(self):
        return iter(((1.0, 0.0, 0.0, 0.0), (0.0, 1.0, 0.0, 0.0), (0.0, 0.0, 1.0, 0.0), (0.0, 0.0, 0.0, 1.0)))


class EvaluatedMaterialSlotTests(unittest.TestCase):
    def test_unused_leading_evaluated_slot_is_compacted_and_indices_are_remapped(self) -> None:
        material = FakeMaterial("GN Material")

        compacted_materials, compacted_indices = compact_evaluated_material_slots(
            (None, material),
            (1, 1),
        )

        self.assertEqual((material,), compacted_materials)
        self.assertEqual((0, 0), compacted_indices)

    def test_evaluated_slot_compaction_keeps_used_empty_and_multiple_material_slots(self) -> None:
        first = FakeMaterial("First")
        third = FakeMaterial("Third")

        compacted_materials, compacted_indices = compact_evaluated_material_slots(
            (None, first, None, third),
            (1, 3, 1),
        )

        self.assertEqual((first, third), compacted_materials)
        self.assertEqual((0, 1, 0), compacted_indices)

        used_empty, used_empty_indices = compact_evaluated_material_slots(
            (None, first),
            (0, 1),
        )
        self.assertEqual((None, first), used_empty)
        self.assertEqual((0, 1), used_empty_indices)

        unused = FakeMaterial("Unused")
        retained_materials, retained_indices = compact_evaluated_material_slots(
            (unused, None, first),
            (2,),
        )
        self.assertEqual((unused, first), retained_materials)
        self.assertEqual((1,), retained_indices)

    def test_evaluated_snapshot_compacts_unused_geometry_nodes_material_slot(self) -> None:
        material = FakeMaterial("GN Material")
        triangles = [FakeTriangle((0, 1, 2), (0, 1, 2), 0, material_index=1)]
        mesh = FakeMesh([None, material], triangles)
        obj = SimpleNamespace(material_slots=[])

        snapshot = collect_material_export_snapshot(
            obj,
            mesh,
            geometry_is_evaluated=True,
        )

        self.assertEqual((material,), snapshot.materials)
        self.assertEqual((0,), snapshot.triangle_material_indices)

    def test_evaluated_export_refs_compact_unused_empty_slot(self) -> None:
        material = FakeMaterial("GN Material")
        mesh = FakeMesh(
            [None, material],
            [FakeTriangle((0, 1, 2), (0, 1, 2), 0, material_index=1)],
        )
        obj = SimpleNamespace(material_slots=[])

        with mock.patch.object(material_sync, "ensure_material_asset_id", return_value="gn"):
            refs = material_sync.collect_refs_for_export(
                obj,
                mesh=mesh,
                evaluated_mesh=True,
            )

        self.assertEqual(("mat-gn",), refs)

    def test_preview_submeshes_use_compacted_evaluated_material_indices(self) -> None:
        raw = {
            "tri_count": 1,
            "tri_material_indices": (1,),
        }
        materials = (None, FakeMaterial("GN Material"))
        _compacted, compacted_indices = compact_evaluated_material_slots(
            materials,
            raw["tri_material_indices"],
        )
        raw["tri_material_indices"] = compacted_indices

        submeshes = mesh_context._build_material_submeshes_from_raw_for_preview(
            raw,
            [0, 1, 2],
        )

        self.assertEqual([], submeshes)

    def test_shape_key_geometry_nodes_mesh_keeps_original_material_watcher(self) -> None:
        obj = SimpleNamespace(
            data=SimpleNamespace(
                shape_keys=SimpleNamespace(key_blocks=[object(), object()]),
            ),
            modifiers=[SimpleNamespace(type="NODES", show_viewport=True)],
        )

        self.assertFalse(controller._object_uses_evaluated_materials(obj))

        obj.data.shape_keys = None
        self.assertTrue(controller._object_uses_evaluated_materials(obj))

    def test_topology_compatible_snapshot_uses_evaluated_material_table_and_indices(self) -> None:
        evaluated_and_original = [evaluated_material(name) for name in ("First", "Second", "Third")]
        evaluated_materials = [item[0] for item in evaluated_and_original]
        original_materials = [item[1] for item in evaluated_and_original]
        triangles = [
            FakeTriangle((0, 1, 2), (0, 1, 2), 0, material_index=2),
            FakeTriangle((0, 2, 1), (3, 4, 5), 1, material_index=0),
        ]
        original_mesh = FakeMesh([FakeMaterial("ObjectSlot")], triangles)
        evaluated_mesh = FakeMesh(evaluated_materials, triangles)
        obj = SimpleNamespace(
            material_slots=[SimpleNamespace(material=FakeMaterial("ObjectSlot"))],
            modifiers=[SimpleNamespace(type="NODES", show_viewport=True)],
        )

        @contextmanager
        def lease_context(_obj):
            yield SimpleNamespace(mesh=evaluated_mesh, source="depsgraph")

        with mock.patch.object(material_slots, "evaluated_mesh_for_sync", side_effect=lease_context):
            snapshot = collect_material_export_snapshot(
                obj,
                original_mesh,
                allow_topology_compatible_evaluated=True,
            )

        self.assertTrue(snapshot.uses_evaluated_mesh)
        self.assertEqual("depsgraph", snapshot.source)
        self.assertEqual(tuple(original_materials), snapshot.materials)
        self.assertEqual((2, 0), snapshot.triangle_material_indices)

    def test_non_evaluated_export_keeps_object_slots_without_evaluating_geometry_nodes(self) -> None:
        object_material = FakeMaterial("ObjectSlot")
        obj = SimpleNamespace(
            material_slots=[SimpleNamespace(material=object_material), SimpleNamespace(material=None)],
            modifiers=[SimpleNamespace(type="NODES", show_viewport=True)],
        )
        original_mesh = FakeMesh([FakeMaterial("MeshTableMaterial")], [])

        with mock.patch.object(material_slots, "evaluated_mesh_for_sync") as evaluate_mock:
            snapshot = collect_material_export_snapshot(obj, original_mesh)

        evaluate_mock.assert_not_called()
        self.assertFalse(snapshot.uses_evaluated_mesh)
        self.assertEqual("object_slots", snapshot.source)
        self.assertEqual((object_material, None), snapshot.materials)
        self.assertIsNone(snapshot.triangle_material_indices)

    def test_topology_mismatch_falls_back_to_object_slots(self) -> None:
        object_material = FakeMaterial("ObjectSlot")
        evaluated, _original = evaluated_material("EvaluatedOnly")
        original_triangles = [FakeTriangle((0, 1, 2), (0, 1, 2), 0)]
        mismatched_triangles = [FakeTriangle((0, 2, 1), (0, 1, 2), 0, material_index=1)]
        original_mesh = FakeMesh([object_material], original_triangles)
        evaluated_mesh = FakeMesh([evaluated], mismatched_triangles)
        obj = SimpleNamespace(
            material_slots=[SimpleNamespace(material=object_material)],
            modifiers=[SimpleNamespace(type="NODES", show_viewport=True)],
        )

        @contextmanager
        def lease_context(_obj):
            yield SimpleNamespace(mesh=evaluated_mesh, source="depsgraph")

        with mock.patch.object(material_slots, "evaluated_mesh_for_sync", side_effect=lease_context):
            snapshot = collect_material_export_snapshot(
                obj,
                original_mesh,
                allow_topology_compatible_evaluated=True,
            )

        self.assertFalse(snapshot.uses_evaluated_mesh)
        self.assertEqual("object_slots", snapshot.source)
        self.assertEqual((object_material,), snapshot.materials)
        self.assertIsNone(snapshot.triangle_material_indices)

    def test_evaluated_triangle_material_indices_drive_submesh_grouping(self) -> None:
        triangles = [
            FakeTriangle((0, 1, 2), (0, 1, 2), 0),
            FakeTriangle((3, 4, 5), (3, 4, 5), 1),
        ]
        mesh = FakeMesh([FakeMaterial("A"), FakeMaterial("B"), FakeMaterial("C")], triangles)

        with mock.patch.object(object_context_builders, "try_build_material_submeshes_native", return_value=None):
            submeshes, _fingerprint = object_context_builders._build_material_submeshes(
                mesh,
                [0, 1, 2, 3, 4, 5],
                material_count=3,
                material_indices_override=(2, 0),
            )

        self.assertEqual(
            [
                {"materialSlot": 0, "topology": "triangles", "indices": [3, 4, 5]},
                {"materialSlot": 2, "topology": "triangles", "indices": [0, 1, 2]},
            ],
            submeshes,
        )

    def test_mesh_payload_uses_all_evaluated_materials_when_object_slots_are_empty(self) -> None:
        evaluated = [evaluated_material(name)[0] for name in ("Red", "Green", "Blue")]
        obj = SimpleNamespace(name="GN Object", material_slots=[])
        mesh = SimpleNamespace(materials=evaluated)

        def asset_id(material) -> str:
            return material.name.lower()

        with mock.patch.object(object_context_builders, "_ensure_material_asset_id", side_effect=asset_id):
            with mock.patch.object(
                object_context_builders,
                "build_material_content_v1",
                side_effect=lambda material: {"materialRef": f"mat-{asset_id(material)}"},
            ):
                refs, contents = object_context_builders._build_export_material_payloads(
                    obj,
                    mesh,
                    evaluated_mesh=True,
                )

        self.assertEqual(["mat-red", "mat-green", "mat-blue"], refs)
        self.assertEqual(refs, [item["materialRef"] for item in contents])

    def test_evaluated_material_table_replaces_shorter_object_slot_table(self) -> None:
        object_material = FakeMaterial("ObjectSlot")
        evaluated = [evaluated_material(name)[0] for name in ("First", "Second", "Third")]
        obj = SimpleNamespace(
            name="GN Object",
            material_slots=[SimpleNamespace(material=object_material)],
        )
        mesh = SimpleNamespace(materials=evaluated)

        with mock.patch.object(
            object_context_builders,
            "_ensure_material_asset_id",
            side_effect=lambda material: material.name.lower(),
        ):
            with mock.patch.object(
                object_context_builders,
                "build_material_content_v1",
                side_effect=lambda material: {"materialRef": f"mat-{material.name.lower()}"},
            ):
                refs, _contents = object_context_builders._build_export_material_payloads(
                    obj,
                    mesh,
                    evaluated_mesh=True,
                )

        self.assertEqual(["mat-first", "mat-second", "mat-third"], refs)
        self.assertNotIn("mat-objectslot", refs)

    def test_evaluated_material_identity_and_content_are_written_to_original(self) -> None:
        evaluated, original = evaluated_material("Source")
        obj = SimpleNamespace(name="GN Object", material_slots=[])
        mesh = SimpleNamespace(materials=[evaluated])

        refs = material_sync.collect_refs_for_export(obj, mesh=mesh, evaluated_mesh=True)
        self.assertEqual(1, len(refs))
        self.assertTrue(refs[0].startswith("mat-"))
        self.assertIn(ASSET_ID_KEY, original)
        self.assertNotIn(ASSET_ID_KEY, evaluated)

        with mock.patch.object(
            material_sync,
            "build_material_content_v1",
            return_value={"materialRef": refs[0]},
        ) as build_mock:
            contents = material_sync.build_material_contents_for_export(
                obj,
                mesh=mesh,
                evaluated_mesh=True,
                unknown_only=False,
            )

        self.assertEqual([{"materialRef": refs[0]}], contents)
        build_mock.assert_called_once_with(original)

    def test_material_ref_change_invalidates_mesh_baseline_match(self) -> None:
        obj = {}
        set_material_slots_baseline(obj, ("mat-old",), reason="test")
        context = {"material_refs": ["mat-new"]}

        with mock.patch.object(
            controller,
            "_mesh_content_fingerprints_from_update_context",
            return_value=("same", "same-no-uv", "full"),
        ):
            with mock.patch.object(controller, "get_mesh_content_fingerprint_baseline", return_value="same"):
                matched, scope = controller._preview_matches_mesh_content_baseline(obj, context)

        self.assertFalse(matched)
        self.assertEqual("material_refs", scope)

    def test_missing_material_baseline_requires_first_reference_send(self) -> None:
        context = {"material_refs": ["mat-new"]}

        with mock.patch.object(
            controller,
            "_mesh_content_fingerprints_from_update_context",
            return_value=("same", "same-no-uv", "full"),
        ), mock.patch.object(controller, "get_mesh_content_fingerprint_baseline", return_value="same"), mock.patch.object(
            controller, "get_material_slots_baseline", return_value=None
        ):
            matched, scope = controller._preview_matches_mesh_content_baseline({}, context)

        self.assertFalse(matched)
        self.assertEqual("material_refs", scope)

    def test_mesh_update_core_sends_same_geometry_when_material_refs_change(self) -> None:
        class Session:
            def __init__(self) -> None:
                self.payloads = []

            def is_alive(self) -> bool:
                return True

            def send_auto(self, payload):
                self.payloads.append(payload)
                return SimpleNamespace(ok=True)

        session = Session()
        core = MeshUpdateCore()

        def context(material_ref: str) -> dict:
            return {
                "session": session,
                "timestamp": 1,
                "pair_entry": {"pairId": "pair-gn", "mapped": True, "syncEnabled": True},
                "mesh_ref": "mesh-gn",
                "material_refs": [material_ref],
                "mesh_content": {
                    "vertices": [0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0, 0.0],
                    "triangles": [0, 1, 2],
                },
            }

        with mock.patch("blender.resource_update.core.build_mesh_update_binary_payload", return_value=None):
            first = core.send_mesh_update_once(context("mat-first"))
            second = core.send_mesh_update_once(context("mat-second"))
            third = core.send_mesh_update_once(context("mat-second"))

        self.assertTrue(first.ok)
        self.assertTrue(second.ok)
        self.assertFalse(third.ok)
        self.assertEqual("no_mesh_change", third.reason)
        self.assertEqual(["mat-first", "mat-second"], [payload["materialRefs"][0] for payload in session.payloads])

    def test_rigged_refs_use_evaluated_table_instead_of_empty_or_short_object_slots(self) -> None:
        object_material = FakeMaterial("ObjectSlot")
        evaluated = [evaluated_material(name)[0] for name in ("First", "Second", "Third")]
        mesh = SimpleNamespace(materials=evaluated)

        with mock.patch.object(
            rigged_builder,
            "_ensure_material_asset_id",
            side_effect=lambda material: material.name.lower(),
        ):
            empty_refs = rigged_builder._collect_material_refs(
                SimpleNamespace(material_slots=[]),
                mesh=mesh,
                evaluated_mesh=True,
            )
            short_refs = rigged_builder._collect_material_refs(
                SimpleNamespace(material_slots=[SimpleNamespace(material=object_material)]),
                mesh=mesh,
                evaluated_mesh=True,
            )

        expected = ["mat-first", "mat-second", "mat-third"]
        self.assertEqual(expected, empty_refs)
        self.assertEqual(expected, short_refs)

    def test_rigged_evaluated_material_maps_to_original(self) -> None:
        evaluated, original = evaluated_material("RigMaterial")
        mesh = SimpleNamespace(materials=[evaluated])
        obj = SimpleNamespace(material_slots=[])

        refs = rigged_builder._collect_material_refs(obj, mesh=mesh, evaluated_mesh=True)

        self.assertEqual(1, len(refs))
        self.assertIn(ASSET_ID_KEY, original)
        self.assertNotIn(ASSET_ID_KEY, evaluated)

    def test_build_unity_rig_payload_uses_the_evaluated_material_snapshot(self) -> None:
        material = FakeMaterial("RigMaterial")
        snapshot = MaterialExportSnapshot(
            materials=(material,),
            triangle_material_indices=(0,),
            uses_evaluated_mesh=True,
            source="depsgraph",
        )
        bone = SimpleNamespace(name="Root", parent=None)
        mesh_data = SimpleNamespace()
        mesh_obj = SimpleNamespace(
            name="Body",
            type="MESH",
            data=mesh_data,
            matrix_world=FakeMatrix(),
        )
        armature = SimpleNamespace(
            name="Armature",
            type="ARMATURE",
            data=SimpleNamespace(bones=[bone]),
            matrix_world=FakeMatrix(),
        )
        prep = {
            "armature": armature,
            "meshParts": [mesh_obj],
            "exportRoot": armature,
            "preparedArmature": armature,
            "preparedMeshParts": [mesh_obj],
            "preparedRoot": armature,
        }
        scene = SimpleNamespace()
        fake_bpy = SimpleNamespace(context=SimpleNamespace(scene=scene))

        with mock.patch.object(rigged_builder, "bpy", fake_bpy), mock.patch.object(
            rigged_builder, "build_export_prep_context", return_value=prep
        ), mock.patch.object(rigged_builder, "dispose_export_prep_context"), mock.patch.object(
            rigged_builder, "_prepare_rig_bone_analysis"
        ), mock.patch.object(rigged_builder, "_iter_export_skeleton_bones", return_value=[bone]), mock.patch.object(
            rigged_builder, "_iter_skin_export_bones", return_value=[bone]
        ), mock.patch.object(rigged_builder, "_find_root_bone", return_value=bone), mock.patch.object(
            rigged_builder, "ensure_rigged_object_id", return_value="rig-id"
        ), mock.patch.object(rigged_builder, "ensure_mesh_asset_id_for_object", return_value="mesh-id"), mock.patch.object(
            rigged_builder, "collect_material_export_snapshot", return_value=snapshot
        ) as snapshot_mock, mock.patch.object(
            rigged_builder, "_collect_material_refs", side_effect=lambda _obj, **kwargs: [
                f"mat-{item.name.lower()}" for item in kwargs["materials"]
            ]
        ), mock.patch.object(
            rigged_builder, "_build_unity_rig_v1_bone_local_matrix", return_value=FakeMatrix()
        ), mock.patch.object(
            rigged_builder, "_build_unity_rig_v1_tail_local_position", return_value=(False, [], [])
        ), mock.patch.object(
            rigged_builder, "_unity_rig_v1_map_quaternion", return_value=[0.0, 0.0, 0.0, 1.0]
        ):
            result = rigged_builder.build_unity_rig_v1_payload(armature)

        self.assertIsNotNone(result)
        rigged = result["riggedObject"]
        self.assertEqual(["mat-rigmaterial"], rigged["materialRefs"])
        self.assertEqual(["mat-rigmaterial"], rigged["meshParts"][0]["materialRefs"])
        snapshot_mock.assert_called_once_with(
            mesh_obj,
            mesh_data,
            geometry_is_evaluated=False,
            allow_topology_compatible_evaluated=True,
        )

    def test_armature_mesh_context_reuses_rigged_material_snapshot(self) -> None:
        snapshot = object()
        armature = SimpleNamespace(name="Armature", type="ARMATURE")
        mesh_obj = SimpleNamespace(name="Body", type="MESH", data=object())
        rigged_payload = {
            "riggedObject": {"riggedObjectId": "rig-id"},
            "_meshPartOrderedBoneIdsByRef": {"mesh-mesh-id": ["bone-Root"]},
        }
        captured = {}

        def build_rig(_obj, *, material_snapshots_by_mesh_ref=None):
            material_snapshots_by_mesh_ref["mesh-mesh-id"] = snapshot
            return rigged_payload

        def build_mesh(*_args, **kwargs):
            captured["snapshot"] = kwargs.get("material_snapshot_override")
            return {"selected": [], "materialContents": []}

        with mock.patch.object(object_context_builders, "build_unity_rig_v1_payload", side_effect=build_rig), mock.patch.object(
            object_context_builders, "collect_armature_mesh_parts", return_value=[mesh_obj]
        ), mock.patch.object(
            object_context_builders, "ensure_mesh_asset_id_for_object", return_value="mesh-id"
        ), mock.patch.object(
            object_context_builders, "_build_mesh_object_live_context", side_effect=build_mesh
        ):
            object_context_builders._build_armature_object_live_context(object(), armature, "pkg-test")

        self.assertIs(snapshot, captured["snapshot"])

    def test_unknown_material_content_is_sent_before_mesh_references(self) -> None:
        from blender.scene_sync.runtime_state import MaterialRuntimeState

        runtime = MaterialRuntimeState()
        events = []
        context = {
            "session": object(),
            "pair_entry": {"pairId": "pair-gn"},
            "rebuild_reason": "geometry_nodes_changed",
            "material_refs": ["mat-gn"],
            "material_contents": [{"materialRef": "mat-gn", "source": {"name": "GN"}}],
        }

        with mock.patch.object(material_sync, "get_session", return_value=object()), mock.patch.object(
            material_sync, "is_material_ref_known", return_value=False
        ), mock.patch.object(
            material_sync, "send_selected_resources", side_effect=lambda _context: events.append("material") or SimpleNamespace(ok=True)
        ), mock.patch.object(material_sync, "mark_assets_known"):
            result = material_sync.send_mesh_update_with_materials(
                runtime,
                context,
                send_mesh_update=lambda _context: events.append("mesh") or {"ok": True},
            )

        self.assertEqual({"ok": True}, result)
        self.assertEqual(["material", "mesh"], events)

    def test_geometry_nodes_context_failure_does_not_fall_back_to_object_slots(self) -> None:
        from blender.scene_sync.runtime_state import MaterialRuntimeState

        obj_state = {material_sync.AUTO_SYNC_READY_KEY: True}
        obj = SimpleNamespace(
            type="MESH",
            material_slots=[],
            get=obj_state.get,
        )
        hooks = material_sync.MaterialSyncHooks(
            get_sync_enabled=lambda: True,
            get_current_mode=lambda: "OBJECT",
            pair_id_for_object=lambda _obj: "pair-gn",
            find_object_by_pair_id=lambda _pair_id: obj,
            send_slot_mesh_update=lambda *_args, **_kwargs: False,
            build_mesh_context=lambda *_args, **_kwargs: None,
            store_mesh_content_baseline=lambda *_args, **_kwargs: None,
            send_shape_key_weights=lambda *_args, **_kwargs: False,
            object_uses_evaluated_materials=lambda _obj: True,
        )

        with mock.patch.object(material_sync, "ensure_instance_id", return_value="gn"), mock.patch.object(
            material_sync, "send_refs_if_changed"
        ) as fallback_mock:
            sent = material_sync.send_slot_mesh_update(
                MaterialRuntimeState(),
                hooks,
                obj,
                force=True,
                reason="manual_update_selected",
            )

        self.assertFalse(sent)
        fallback_mock.assert_not_called()

    def test_live_context_material_refs_are_available_for_object_and_rigged_baselines(self) -> None:
        object_context = {
            "objectAssemblies": [
                {"pairId": "pair-object", "meshRef": "mesh-object", "materialRefs": ["mat-a", "", "mat-c"]}
            ]
        }
        rigged_context = {
            "riggedObjects": [
                {
                    "riggedObject": {
                        "meshRef": "mesh-rig",
                        "materialRefs": ["mat-root"],
                        "meshParts": [
                            {"meshRef": "mesh-part", "materialRefs": ["mat-a", "mat-b"]}
                        ],
                    }
                }
            ]
        }

        self.assertEqual(
            ("mat-a", "", "mat-c"),
            material_refs_from_live_context(object_context, pair_id="pair-object", mesh_ref="mesh-object"),
        )
        self.assertEqual(
            ("mat-a", "mat-b"),
            material_refs_from_live_context(rigged_context, mesh_ref="mesh-part"),
        )


if __name__ == "__main__":
    unittest.main()
