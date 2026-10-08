from __future__ import annotations

import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock


ADDON_ROOT = Path(__file__).resolve().parents[2] / "blender_addon" / "blendersync_vnext"
sys.path.insert(0, str(ADDON_ROOT))

from blender.rigged_object import builder
from blender.ui import operators


class _FakeObject(dict):
    def __init__(self, object_type: str, name: str, *, ready: bool = False, armature=None):
        super().__init__()
        self.type = object_type
        self.name = name
        self.data = SimpleNamespace()
        self._armature = armature
        if ready:
            self[operators.AUTO_SYNC_READY_KEY] = True

    def find_armature(self):
        return self._armature


def _mesh_part(name: str, values: list[tuple[str, float]]):
    blocks = [SimpleNamespace(name="Basis", value=0.0)]
    blocks.extend(SimpleNamespace(name=shape_name, value=value) for shape_name, value in values)
    mesh = SimpleNamespace(shape_keys=SimpleNamespace(key_blocks=blocks))
    obj = SimpleNamespace(name=name, type="MESH", data=mesh)
    return obj


class RiggedBlendShapeWeightsTests(unittest.TestCase):
    def test_builder_collects_all_bound_parts_without_mesh_payload(self) -> None:
        face = _mesh_part("Face", [("Smile", 0.75), ("Blink", 0.0)])
        body = _mesh_part("Body", [("Breath", 0.2)])
        armature = SimpleNamespace(type="ARMATURE", name="Rig")
        update = mock.Mock()
        context = SimpleNamespace(
            evaluated_depsgraph_get=lambda: SimpleNamespace(update=update),
        )

        with (
            mock.patch.object(builder, "bpy", object()),
            mock.patch.object(builder, "collect_armature_mesh_parts", return_value=[face, body]),
            mock.patch.object(builder, "ensure_mesh_asset_id_for_object", side_effect=["face-id", "body-id"]),
            mock.patch.object(builder, "ensure_rigged_object_id", return_value="rig-id"),
        ):
            payload = builder.build_unity_rig_v1_blendshape_weights_payload(context, armature)

        update.assert_called_once_with()
        self.assertEqual("asset_bridge.rigged_blendshape_weights_v1", payload["type"])
        self.assertEqual("rig-id", payload["riggedObjectId"])
        self.assertEqual(
            ["Face", "Body"],
            [part["objectName"] for part in payload["parts"]],
        )
        self.assertEqual("mesh-face-id", payload["parts"][0]["meshRef"])
        self.assertEqual(
            [("Smile", 1, 0.75, 75.0), ("Blink", 2, 0.0, 0.0)],
            [
                (item["name"], item["index"], item["value"], item["weight"])
                for item in payload["parts"][0]["weights"]
            ],
        )
        self.assertEqual("Breath", payload["parts"][1]["weights"][0]["name"])
        self.assertNotIn("vertices", payload)
        self.assertNotIn("mesh", payload)

    def test_builder_returns_none_when_bound_rig_has_no_shape_keys(self) -> None:
        mesh = SimpleNamespace(
            name="Body",
            type="MESH",
            data=SimpleNamespace(shape_keys=None),
        )
        armature = SimpleNamespace(type="ARMATURE", name="Rig")
        context = SimpleNamespace(evaluated_depsgraph_get=lambda: SimpleNamespace(update=lambda: None))

        with (
            mock.patch.object(builder, "bpy", object()),
            mock.patch.object(builder, "collect_armature_mesh_parts", return_value=[mesh]),
        ):
            self.assertIsNone(
                builder.build_unity_rig_v1_blendshape_weights_payload(context, armature)
            )

    def test_operator_sends_shape_key_snapshot_from_object_or_pose_mode(self) -> None:
        armature = _FakeObject("ARMATURE", "Rig", ready=True)
        payload = {
            "type": "asset_bridge.rigged_blendshape_weights_v1",
            "riggedObjectId": "rig-id",
            "parts": [{"objectName": "Face", "weights": [{"name": "Smile"}]}],
        }
        result = SimpleNamespace(ok=True, error=None)
        session = SimpleNamespace(
            get_truth_state=mock.Mock(return_value={"handshake_confirmed": True})
        )
        context = SimpleNamespace(
            mode="OBJECT",
            active_object=armature,
            selected_objects=[armature],
        )
        operator = operators.BS_OT_SyncRiggedBlendShapeWeights()

        with (
            mock.patch.object(operators, "bpy", object()),
            mock.patch.object(operators, "get_session", return_value=session),
            mock.patch.object(
                operators,
                "build_unity_rig_v1_blendshape_weights_payload",
                return_value=payload,
            ) as build_payload,
            mock.patch.object(
                operators,
                "send_rigged_blendshape_weights_sync",
                return_value=result,
            ) as send_payload,
            mock.patch.object(operators, "_set_last_result"),
        ):
            self.assertTrue(operator.poll(context))
            self.assertEqual({"FINISHED"}, operator.execute(context))

        build_payload.assert_called_once_with(context, armature)
        send_context = send_payload.call_args.args[0]
        self.assertIs(payload, send_context["riggedBlendShapeWeightsPayload"])
        self.assertEqual("rigged_blendshape_weights_sync", send_context["updateIntent"])


if __name__ == "__main__":
    unittest.main()
