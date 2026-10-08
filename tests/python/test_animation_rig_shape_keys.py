from __future__ import annotations

import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock


ADDON_ROOT = Path(__file__).resolve().parents[2] / "blender_addon" / "blendersync_vnext"
sys.path.insert(0, str(ADDON_ROOT))

from blender.animation_clip import core


def _fcurve(data_path: str, *, keyframes: bool = True):
    return SimpleNamespace(
        data_path=data_path,
        array_index=0,
        keyframe_points=[SimpleNamespace()] if keyframes else [],
    )


def _shape_source(name: str, action=None, drivers=None, block_name: str = "Smile"):
    block = SimpleNamespace(name=block_name, value=0.0)
    shape_keys = SimpleNamespace(
        key_blocks=[SimpleNamespace(name="Basis", value=0.0), block],
        animation_data=SimpleNamespace(action=action, drivers=list(drivers or [])),
    )
    mesh_object = SimpleNamespace(
        name=name,
        type="MESH",
        data=SimpleNamespace(shape_keys=shape_keys),
    )
    return mesh_object, shape_keys, block


class _Session:
    def __init__(self) -> None:
        self.payload = None

    def is_alive(self) -> bool:
        return True

    def send_auto(self, payload):
        self.payload = payload
        return SimpleNamespace(ok=True, error=None)


class _Scene:
    def __init__(self, block=None) -> None:
        self.frame_current = 7
        self.frame_subframe = 0.0
        self.frame_start = 1
        self.frame_end = 20
        self.render = SimpleNamespace(fps=30.0, fps_base=1.0)
        self.blendersync_animation_clip_source_mode = "shape_keys"
        self._block = block

    def frame_set(self, frame, subframe=0.0) -> None:
        self.frame_current = int(frame)
        self.frame_subframe = float(subframe)
        if self._block is not None:
            self._block.value = float(frame) + float(subframe) - 1.0


class AnimationRigShapeKeyTests(unittest.TestCase):
    def test_armature_discovers_actions_and_drivers_from_every_bound_mesh(self) -> None:
        action = SimpleNamespace(
            frame_range=(1.0, 20.0),
            fcurves=[_fcurve('key_blocks["Smile"].value')],
        )
        face, _face_keys, _face_block = _shape_source("Face", action=action)
        driver = _fcurve('key_blocks["Blink"].value', keyframes=False)
        eyes, _eye_keys, _eye_block = _shape_source(
            "Eyes",
            drivers=[driver],
            block_name="Blink",
        )
        armature = SimpleNamespace(type="ARMATURE")

        with mock.patch.object(
            core,
            "_collect_armature_animation_mesh_parts",
            return_value=[face, eyes],
        ):
            sources = core._collect_shape_key_animation_sources(armature)

        self.assertEqual(["Face", "Eyes"], [source["object"].name for source in sources])
        self.assertIs(action, sources[0]["action"])
        self.assertEqual({"Smile"}, set(sources[0]["channels"]))
        self.assertIsNone(sources[1]["action"])
        self.assertEqual({"Blink"}, set(sources[1]["channels"]))

    def test_shape_keys_only_armature_passes_source_validation_without_armature_action(self) -> None:
        action = SimpleNamespace(frame_range=(3.0, 18.0))
        face, shape_keys, _block = _shape_source("Face", action=action)
        source = {
            "object": face,
            "shape_keys": shape_keys,
            "animation_data": shape_keys.animation_data,
            "action": action,
            "channels": {"Smile": {"value": True}},
        }
        armature = SimpleNamespace(
            name="Rig",
            type="ARMATURE",
            animation_data=SimpleNamespace(action=None, drivers=[]),
        )
        scene = _Scene()
        context = SimpleNamespace(scene=scene, active_object=armature)
        payload = {
            "clip": {
                "assetId": "anim-rig-shapes",
                "name": "Rig_unity_rig_v1",
                "channelSemantic": "unity_rig_v1",
                "exportReport": {},
                "tracks": [{"targetType": "blend_shape"}],
            }
        }
        session = _Session()

        with mock.patch.object(core, "bpy", object()), mock.patch.object(
            core,
            "_collect_shape_key_animation_sources",
            return_value=[source],
        ), mock.patch.object(
            core,
            "_build_active_armature_clip_payload_unity_rig_v1",
            return_value=payload,
        ) as build:
            result = core.send_active_object_animation_clip_once(context, session)

        self.assertTrue(result["ok"])
        self.assertIs(payload, session.payload)
        kwargs = build.call_args.kwargs
        self.assertIsNone(kwargs["armature_action"])
        self.assertFalse(kwargs["include_object_animation"])
        self.assertEqual([source], kwargs["shape_key_sources"])
        self.assertEqual((3, 18), (kwargs["start_frame"], kwargs["end_frame"]))

    def test_combined_armature_passes_bone_and_shape_sources_with_union_range(self) -> None:
        armature_action = SimpleNamespace(frame_range=(5.0, 20.0), name="RigAction")
        shape_action = SimpleNamespace(frame_range=(1.0, 42.0), name="FaceAction")
        face, shape_keys, _block = _shape_source("Face", action=shape_action)
        source = {
            "object": face,
            "shape_keys": shape_keys,
            "animation_data": shape_keys.animation_data,
            "action": shape_action,
            "channels": {"Smile": {"value": True}},
        }
        armature = SimpleNamespace(
            name="Rig",
            type="ARMATURE",
            animation_data=SimpleNamespace(action=armature_action, drivers=[]),
        )
        scene = _Scene()
        scene.blendersync_animation_clip_source_mode = "combined"
        context = SimpleNamespace(scene=scene, active_object=armature)
        payload = {
            "clip": {
                "assetId": "anim-rig-combined",
                "name": "Rig_unity_rig_v1",
                "channelSemantic": "unity_rig_v1",
                "exportReport": {},
                "tracks": [
                    {"targetType": "bone"},
                    {"targetType": "blend_shape"},
                ],
            }
        }

        with mock.patch.object(core, "bpy", object()), mock.patch.object(
            core,
            "_collect_shape_key_animation_sources",
            return_value=[source],
        ), mock.patch.object(
            core,
            "_build_active_armature_clip_payload_unity_rig_v1",
            return_value=payload,
        ) as build:
            result = core.send_active_object_animation_clip_once(context, _Session())

        self.assertTrue(result["ok"])
        kwargs = build.call_args.kwargs
        self.assertIs(armature_action, kwargs["armature_action"])
        self.assertTrue(kwargs["include_object_animation"])
        self.assertEqual([source], kwargs["shape_key_sources"])
        self.assertEqual((1, 42), (kwargs["start_frame"], kwargs["end_frame"]))

    def test_armature_sampler_writes_bound_mesh_blend_shape_path(self) -> None:
        face, shape_keys, block = _shape_source("Face/Mesh")
        source = {
            "object": face,
            "shape_keys": shape_keys,
            "animation_data": shape_keys.animation_data,
            "action": None,
            "channels": {"Smile": {"value": True}},
        }
        scene = _Scene(block)
        depsgraph = SimpleNamespace(update=lambda: None)
        context = SimpleNamespace(
            scene=scene,
            evaluated_depsgraph_get=lambda: depsgraph,
        )
        armature = SimpleNamespace(
            name="Rig",
            data=SimpleNamespace(bones={}),
            evaluated_get=lambda _depsgraph: SimpleNamespace(pose=SimpleNamespace(bones=[])),
        )

        with mock.patch.object(
            core,
            "build_output_bone_axis_correction",
            return_value=(None, None),
        ):
            tracks = core._sample_active_armature_tracks_unity_rig_v1(
                context,
                armature,
                {"object": {}, "bones": {}},
                1,
                [(1.0, 0.0), (2.0, 1.0 / 30.0)],
                "linear",
                shape_key_sources=[source],
            )

        self.assertEqual(1, len(tracks))
        self.assertEqual("Face_Mesh", tracks[0]["path"])
        self.assertEqual("blend_shape", tracks[0]["targetType"])
        self.assertEqual("blendShapeWeight", tracks[0]["property"])
        self.assertEqual("Smile", tracks[0]["component"])
        self.assertEqual([0.0, 100.0], [key["value"] for key in tracks[0]["keys"]])
        self.assertEqual((7, 0.0), (scene.frame_current, scene.frame_subframe))

    def test_shape_keys_only_forced_evaluated_mode_does_not_add_bone_tracks(self) -> None:
        action = SimpleNamespace(frame_range=(1.0, 2.0), name="FaceAction")
        face, shape_keys, _block = _shape_source("Face", action=action)
        source = {
            "object": face,
            "shape_keys": shape_keys,
            "animation_data": shape_keys.animation_data,
            "action": action,
            "channels": {"Smile": {"value": True}},
        }
        armature = SimpleNamespace(name="Rig")
        shape_track = {
            "path": "Face",
            "targetType": "blend_shape",
            "property": "blendShapeWeight",
            "component": "Smile",
            "keys": [{"time": 0.0, "value": 0.0}, {"time": 1.0 / 30.0, "value": 100.0}],
        }

        with mock.patch.object(
            core,
            "_sample_active_armature_tracks_unity_rig_v1",
            return_value=[shape_track],
        ) as sample, mock.patch.object(
            core,
            "ensure_rigged_object_id",
            return_value="rig-id",
        ), mock.patch.object(
            core,
            "_build_current_unity_rig_clip_asset_id",
            return_value="clip-id",
        ):
            payload = core._build_active_armature_clip_payload_unity_rig_v1(
                context=object(),
                armature_obj=armature,
                action=action,
                clip_name="Rig",
                start_frame=1,
                end_frame=2,
                frame_rate=30.0,
                scene_frame_rate=30.0,
                sample_mode="scene_frames",
                sample_step=1,
                loop_hint="none",
                range_mode="action",
                sample_rate_mode="scene",
                bake_evaluated_pose=True,
                armature_sampling_strategy="evaluated_deform",
                armature_action=None,
                shape_key_sources=[source],
                include_object_animation=False,
                source_mode="shape_keys",
            )

        self.assertFalse(sample.call_args.args[6])
        self.assertEqual({"object": {}, "bones": {}}, sample.call_args.args[2])
        clip = payload["clip"]
        self.assertEqual([shape_track], clip["tracks"])
        self.assertFalse(clip["exportSettings"]["bakeEvaluatedPose"])
        self.assertEqual("raw_fcurve", clip["exportSettings"]["armatureSamplingMode"])

    def test_action_range_spans_armature_and_all_shape_key_actions(self) -> None:
        actions = [
            SimpleNamespace(frame_range=(5.0, 20.0)),
            SimpleNamespace(frame_range=(1.0, 42.0)),
            SimpleNamespace(frame_range=(8.0, 30.0)),
        ]

        self.assertEqual(
            (1, 42),
            core._resolve_frame_range_for_actions(None, actions, "action"),
        )


if __name__ == "__main__":
    unittest.main()
