from __future__ import annotations

import json
import math
import re
import time

from blender.common.rig_axis import (
    RIG_AXIS_MODE_BAKED_JOINT_AXES,
    RIG_AXIS_MODE_PRESERVE_REST_BONE_AXES,
    apply_output_bone_axis_correction,
    build_output_bone_axis_correction,
    normalize_bone_axis_pair,
)
from blender.identity import ensure_asset_id, ensure_instance_id, ensure_rigged_object_id
from blender.session.core import BlenderSessionCore

try:
    import bpy  # type: ignore
except ImportError:
    bpy = None

_BONE_FCURVE_RE = re.compile(r'^pose\.bones\["(?P<name>.*)"\]\.(?P<prop>location|rotation_quaternion|rotation_euler|scale)$')
_SHAPE_KEY_FCURVE_RE = re.compile(r'^key_blocks\["(?P<name>.*)"\]\.value$')
_OBJECT_PROPS = {"location", "rotation_quaternion", "rotation_euler", "scale"}
STATIC_CURVE_MODE_OFF = "off"
STATIC_CURVE_MODE_COLLAPSE_CONSTANT = "collapse_constant"
STATIC_CURVE_VALUE_EPSILON = 1e-6
STATIC_CURVE_ROTATION_EPSILON_DEGREES = 1e-4

# Evaluated pose matrices are assembled through dependency-graph constraints.
# Inverting/decomposing those matrices can introduce a few float ULPs of
# apparent translation or scale even when the authored bone channels are
# unchanged.  Keep this below the user-facing curve simplification tolerance;
# meaningful controller output remains untouched.
BONE_POSITION_NOISE_EPSILON = 1e-5
BONE_SCALE_NOISE_EPSILON = 1e-5


def send_active_object_animation_clip_unity_rig_v1_once(context, session: BlenderSessionCore | None = None) -> dict:
    return send_active_object_animation_clip_once(context, session, force_semantic="unity_rig_v1")


def send_active_object_animation_clip_once(context, session: BlenderSessionCore | None = None, force_semantic: str | None = None) -> dict:
    if bpy is None or context is None:
        return {"ok": False, "reason": "blender_context_missing"}

    session = session or BlenderSessionCore()
    if not session.is_alive():
        return {"ok": False, "reason": "handshake_not_confirmed"}

    scene = getattr(context, "scene", None)
    obj = getattr(context, "active_object", None)
    if obj is None:
        return {"ok": False, "reason": "active_object_missing"}

    obj_type = str(getattr(obj, "type", "") or "")
    semantic = str(force_semantic or ("unity_rig_v1" if obj_type == "ARMATURE" else "unity_object_v1"))
    if semantic == "unity_rig_v1" and obj_type != "ARMATURE":
        return {"ok": False, "reason": "active_object_not_armature"}
    if semantic == "unity_object_v1" and obj_type == "ARMATURE":
        return {"ok": False, "reason": "active_object_is_armature_use_unity_rig_v1"}

    source_mode = str(getattr(scene, "blendersync_animation_clip_source_mode", "combined") or "combined") if scene is not None else "combined"
    if source_mode not in {"combined", "object", "shape_keys"}:
        source_mode = "combined"
    include_object_animation = source_mode in {"combined", "object"}
    include_shape_key_animation = source_mode in {"combined", "shape_keys"}

    animation_data = getattr(obj, "animation_data", None)
    raw_object_action = getattr(animation_data, "action", None) if animation_data is not None else None
    shape_key_sources = _collect_shape_key_animation_sources(obj) if include_shape_key_animation else []
    shape_key_actions = [source.get("action") for source in shape_key_sources if source.get("action") is not None]
    raw_shape_key_action = shape_key_actions[0] if shape_key_actions else None
    object_action = raw_object_action if include_object_animation else None
    shape_key_action = raw_shape_key_action if include_shape_key_animation else None
    action = object_action or shape_key_action
    has_object_drivers = include_object_animation and _has_animation_drivers(animation_data)
    has_shape_key_drivers = include_shape_key_animation and any(
        _has_animation_drivers(source.get("animation_data"))
        for source in shape_key_sources
    )
    driver_only_armature = semantic == "unity_rig_v1" and action is None and (has_object_drivers or has_shape_key_drivers)
    driver_only_object = semantic == "unity_object_v1" and action is None and (has_object_drivers or has_shape_key_drivers)
    if action is None:
        if driver_only_armature:
            # Driver-only rigs can have no active Action at all.  Treat the
            # armature object itself as the stable clip source/asset identity;
            # evaluated sampling will bake deform bones over the scene/custom
            # frame range.
            action = obj
        elif driver_only_object:
            # Driver-only object clips also have no Action datablock.  Use the
            # object as a range/name fallback while sampling evaluated driven
            # transform and shape key values over the scene/custom frame range.
            action = obj
        else:
            return {
                "ok": False,
                "reason": "active_action_missing",
                "message": f"No animation source found for Source={source_mode}. Assign/connect a matching Action or driver before importing animation.",
            }

    range_mode = getattr(scene, "blendersync_animation_clip_range_mode", "action") if scene is not None else "action"
    driver_only_clip = driver_only_armature or driver_only_object
    effective_range_mode = "scene" if driver_only_clip and range_mode == "action" else range_mode
    sample_mode = getattr(scene, "blendersync_animation_clip_sample_mode", "scene_frames") if scene is not None else "scene_frames"
    sample_rate_mode = getattr(scene, "blendersync_animation_clip_sample_rate_mode", "scene") if scene is not None else "scene"
    loop_hint = getattr(scene, "blendersync_animation_clip_loop_hint", "none") if scene is not None else "none"
    sample_step = int(getattr(scene, "blendersync_animation_clip_sample_step", 1) or 1) if scene is not None else 1
    armature_sampling_strategy = str(getattr(scene, "blendersync_animation_clip_sampling_strategy", "auto") or "auto") if scene is not None else "auto"
    rig_axis_mode = str(getattr(scene, "blendersync_rig_axis_mode", RIG_AXIS_MODE_BAKED_JOINT_AXES) or RIG_AXIS_MODE_BAKED_JOINT_AXES) if scene is not None else RIG_AXIS_MODE_BAKED_JOINT_AXES
    if rig_axis_mode not in {RIG_AXIS_MODE_BAKED_JOINT_AXES, RIG_AXIS_MODE_PRESERVE_REST_BONE_AXES}:
        rig_axis_mode = RIG_AXIS_MODE_BAKED_JOINT_AXES
    primary_bone_axis, secondary_bone_axis = normalize_bone_axis_pair(
        getattr(scene, "blendersync_rig_primary_bone_axis", None) if scene is not None else None,
        getattr(scene, "blendersync_rig_secondary_bone_axis", None) if scene is not None else None,
    )
    bake_evaluated_pose = armature_sampling_strategy == "evaluated_deform"
    interpolation = getattr(scene, "blendersync_animation_clip_interpolation", "linear") if scene is not None else "linear"
    quaternion_continuity = bool(getattr(scene, "blendersync_animation_clip_quaternion_continuity", True)) if scene is not None else True
    simplify = getattr(scene, "blendersync_animation_clip_simplify", "off") if scene is not None else "off"
    static_curves = _normalize_static_curve_mode(
        getattr(scene, "blendersync_animation_clip_static_curves", STATIC_CURVE_MODE_OFF)
        if scene is not None else STATIC_CURVE_MODE_OFF
    )
    custom_clip_name = str(getattr(scene, "blendersync_animation_clip_name", "") or "").strip() if scene is not None else ""
    source_actions = ([object_action] if object_action is not None else []) + shape_key_actions
    start_frame, end_frame = _resolve_frame_range_for_actions(scene, source_actions, effective_range_mode)
    scene_frame_rate = _resolve_scene_frame_rate(scene)
    frame_rate = _resolve_frame_rate(scene, sample_rate_mode)
    clip_name = custom_clip_name or str(getattr(obj, "name", "") or "AnimationClip")

    if semantic == "unity_rig_v1":
        payload = _build_active_armature_clip_payload_unity_rig_v1(
            context=context,
            armature_obj=obj,
            action=action,
            clip_name=clip_name,
            start_frame=start_frame,
            end_frame=end_frame,
            frame_rate=frame_rate,
            scene_frame_rate=scene_frame_rate,
            sample_mode=sample_mode,
            sample_step=sample_step,
            loop_hint=loop_hint,
            range_mode=effective_range_mode,
            sample_rate_mode=sample_rate_mode,
            bake_evaluated_pose=bake_evaluated_pose,
            armature_sampling_strategy=armature_sampling_strategy,
            rig_axis_mode=rig_axis_mode,
            primary_bone_axis=primary_bone_axis,
            secondary_bone_axis=secondary_bone_axis,
            interpolation=interpolation,
            quaternion_continuity=quaternion_continuity,
            simplify=simplify,
            static_curves=static_curves,
            armature_action=object_action,
            shape_key_sources=shape_key_sources,
            include_object_animation=include_object_animation,
            driver_only_armature=driver_only_armature,
            source_mode=source_mode,
        )
    elif semantic == "unity_object_v1":
        payload = _build_active_object_clip_payload_unity_object_v1(
            context=context,
            obj=obj,
            action=action,
            object_action=object_action,
            shape_key_action=shape_key_action,
            clip_name=clip_name,
            start_frame=start_frame,
            end_frame=end_frame,
            frame_rate=frame_rate,
            scene_frame_rate=scene_frame_rate,
            sample_mode=sample_mode,
            sample_step=sample_step,
            loop_hint=loop_hint,
            range_mode=effective_range_mode,
            sample_rate_mode=sample_rate_mode,
            interpolation=interpolation,
            quaternion_continuity=quaternion_continuity,
            simplify=simplify,
            static_curves=static_curves,
            driver_only_object=driver_only_object,
            include_object_animation=include_object_animation,
            include_shape_key_animation=include_shape_key_animation,
            source_mode=source_mode,
        )
    else:
        return {"ok": False, "reason": f"unsupported_animation_semantic:{semantic}"}

    report = payload["clip"].get("exportReport") or {}
    if scene is not None:
        try:
            scene.blendersync_animation_clip_last_report = _format_export_report(report)
        except Exception:
            pass
    payload_size = _estimate_json_bytes(payload)
    result = session.send_auto(payload) if hasattr(session, "send_auto") else session.send(payload)
    if not result.ok:
        return {"ok": False, "reason": result.error or "animation_clip_send_failed", "exportReport": report, "reportText": _format_export_report(report), "payloadSize": payload_size}

    return {
        "ok": True,
        "reason": "animation_clip_send_ok",
        "assetId": payload["clip"].get("assetId"),
        "trackCount": len(payload["clip"].get("tracks") or []),
        "clipName": payload["clip"].get("name"),
        "channelSemantic": payload["clip"].get("channelSemantic"),
        "exportReport": report,
        "reportText": _format_export_report(report),
        "payloadSize": payload_size,
    }


def _build_active_armature_clip_payload_unity_rig_v1(context, armature_obj, action, clip_name: str, start_frame: int, end_frame: int, frame_rate: float, scene_frame_rate: float, sample_mode: str, sample_step: int, loop_hint: str, range_mode: str, sample_rate_mode: str, bake_evaluated_pose: bool, armature_sampling_strategy: str, rig_axis_mode: str = RIG_AXIS_MODE_BAKED_JOINT_AXES, primary_bone_axis: str = "Z", secondary_bone_axis: str = "X", interpolation: str = "linear", quaternion_continuity: bool = True, simplify: str = "off", static_curves: str = STATIC_CURVE_MODE_OFF, armature_action=None, shape_key_sources: list[dict] | None = None, include_object_animation: bool = True, driver_only_armature: bool = False, source_mode: str = "object") -> dict:
    armature_action_for_tracks = armature_action if include_object_animation else None
    channel_map = _collect_action_channel_map(armature_action_for_tracks, armature_obj)
    if include_object_animation:
        armature_sampling_mode, armature_sampling_reasons = _resolve_armature_sampling_strategy(
            armature_obj,
            armature_action_for_tracks,
            channel_map,
            armature_sampling_strategy,
        )
    else:
        armature_sampling_mode = "raw_fcurve"
        armature_sampling_reasons = ["Shape Keys Only: Armature channels excluded"]
    if armature_sampling_mode == "evaluated_deform":
        channel_map = _expand_channel_map_for_evaluated_pose(armature_obj, channel_map)
    shape_key_sources = list(shape_key_sources or [])
    sample_actions = ([armature_action_for_tracks] if armature_action_for_tracks is not None else []) + [
        source.get("action")
        for source in shape_key_sources
        if source.get("action") is not None
    ]
    sample_times = _build_sample_times_for_actions(sample_actions, start_frame, end_frame, frame_rate, scene_frame_rate, sample_mode, sample_step)
    tracks = _sample_active_armature_tracks_unity_rig_v1(
        context,
        armature_obj,
        channel_map,
        start_frame,
        sample_times,
        interpolation,
        armature_sampling_mode == "evaluated_deform",
        rig_axis_mode,
        primary_bone_axis,
        secondary_bone_axis,
        shape_key_sources=shape_key_sources,
    )
    key_count_before = _count_track_keys(tracks)
    if quaternion_continuity:
        _ensure_quaternion_continuity(tracks)
    if simplify and simplify != "off":
        _simplify_tracks(tracks, simplify)
    key_count_after_simplify = _count_track_keys(tracks)
    collapsed_static_track_count = _collapse_constant_transform_tracks(tracks, static_curves)
    key_count_after = _count_track_keys(tracks)
    clip_asset_prefix = "animunityrigv1"
    if rig_axis_mode == RIG_AXIS_MODE_PRESERVE_REST_BONE_AXES:
        clip_asset_prefix = "animunityrigv1-preserveaxes"
    rigged_object_id = ensure_rigged_object_id(armature_obj)
    clip_asset_id = _build_current_unity_rig_clip_asset_id(clip_asset_prefix, armature_obj, action, driver_only_armature)
    export_report = _build_export_report(
        clip_name=clip_name,
        sample_mode=sample_mode,
        sample_rate_mode=sample_rate_mode,
        frame_rate=frame_rate,
        scene_frame_rate=scene_frame_rate,
        start_frame=start_frame,
        end_frame=end_frame,
        sample_count=len(sample_times),
        tracks=tracks,
        key_count_before=key_count_before,
        key_count_after_simplify=key_count_after_simplify,
        key_count_after=key_count_after,
        bake_evaluated_pose=armature_sampling_mode == "evaluated_deform",
        interpolation=interpolation,
        quaternion_continuity=quaternion_continuity,
        simplify=simplify,
        static_curves=static_curves,
        collapsed_static_track_count=collapsed_static_track_count,
        loop_hint=loop_hint,
        armature_sampling_requested=armature_sampling_strategy,
        armature_sampling_mode=armature_sampling_mode,
        armature_sampling_reasons=armature_sampling_reasons,
    )
    return {
        "type": "asset_bridge.animation_clip",
        "kind": "animation_clip",
        "version": 1,
        "timestamp": int(time.time()),
        "clip": {
            "assetId": clip_asset_id,
            "name": _build_clip_name(clip_name, "unity_rig_v1"),
            "sourceActionName": str(getattr(action, "name", "") or ""),
            "frameRate": float(frame_rate),
            "startFrame": int(start_frame),
            "endFrame": int(end_frame),
            "wrapModeHint": str(loop_hint or "none"),
            "bindingSpace": "unity_rig_v1",
            "channelSemantic": "unity_rig_v1",
            "exportSettings": {
                "rangeMode": str(range_mode or "action"),
                "sampleMode": str(sample_mode or "scene_frames"),
                "sampleRateMode": str(sample_rate_mode or "scene"),
                "sampleStep": int(sample_step),
                "bakeEvaluatedPose": armature_sampling_mode == "evaluated_deform",
                "rigAxisMode": rig_axis_mode,
                "primaryBoneAxis": primary_bone_axis,
                "secondaryBoneAxis": secondary_bone_axis,
                "armatureSamplingRequested": str(armature_sampling_strategy or "auto"),
                "armatureSamplingMode": armature_sampling_mode,
                "armatureSamplingReasons": armature_sampling_reasons,
                "onlyDeformBones": True,
                "interpolation": str(interpolation or "linear"),
                "quaternionContinuity": bool(quaternion_continuity),
                "simplify": str(simplify or "off"),
                "staticCurves": str(static_curves or STATIC_CURVE_MODE_OFF),
                "sampleCount": int(len(sample_times)),
                "animationSource": str(source_mode or "object"),
            },
            "exportContext": {
                "sourceObjectName": str(getattr(armature_obj, "name", "") or ""),
                "sourceArmatureName": str(getattr(armature_obj, "name", "") or ""),
                "sourcePairId": f"rigpair-{rigged_object_id}",
                "sourceRiggedObjectId": rigged_object_id,
                "targetKind": "rigged_object",
            },
            "exportReport": export_report,
            "tracks": tracks,
        },
    }


def _build_active_object_clip_payload_unity_object_v1(context, obj, action, object_action=None, shape_key_action=None, clip_name: str = "AnimationClip", start_frame: int = 1, end_frame: int = 1, frame_rate: float = 30.0, scene_frame_rate: float = 30.0, sample_mode: str = "scene_frames", sample_step: int = 1, loop_hint: str = "none", range_mode: str = "action", sample_rate_mode: str = "scene", interpolation: str = "linear", quaternion_continuity: bool = True, simplify: str = "off", static_curves: str = STATIC_CURVE_MODE_OFF, driver_only_object: bool = False, include_object_animation: bool = True, include_shape_key_animation: bool = True, source_mode: str = "combined") -> dict:
    shape_keys = _get_object_shape_keys(obj)
    object_action_for_tracks = object_action
    shape_key_action_for_tracks = shape_key_action
    channel_map = _collect_action_channel_map(object_action_for_tracks, obj)
    shape_key_channel_map = _collect_shape_key_channel_map(shape_key_action_for_tracks, shape_keys)
    animation_data = getattr(obj, "animation_data", None)
    shape_key_animation_data = getattr(shape_keys, "animation_data", None) if shape_keys is not None else None
    if include_object_animation:
        _merge_object_channel_map(channel_map.setdefault("object", {}), _collect_object_driver_channel_map(animation_data))
    if include_shape_key_animation:
        shape_key_channel_map.update(_collect_shape_key_driver_channel_map(shape_key_animation_data, shape_keys))
    sample_times = _build_sample_times_for_actions([object_action_for_tracks, shape_key_action_for_tracks], start_frame, end_frame, frame_rate, scene_frame_rate, sample_mode, sample_step)
    tracks = _sample_active_object_tracks_unity_object_v1(context, obj, channel_map.get("object") or {}, shape_key_channel_map, sample_times, interpolation)
    key_count_before = _count_track_keys(tracks)
    if quaternion_continuity:
        _ensure_quaternion_continuity(tracks)
    if simplify and simplify != "off":
        _simplify_tracks(tracks, simplify)
    key_count_after_simplify = _count_track_keys(tracks)
    collapsed_static_track_count = _collapse_constant_transform_tracks(tracks, static_curves)
    key_count_after = _count_track_keys(tracks)
    object_instance_id = ensure_instance_id(obj)
    object_name = str(getattr(obj, "name", "") or "")
    object_type = str(getattr(obj, "type", "") or "")
    export_report = _build_export_report(
        clip_name=clip_name,
        sample_mode=sample_mode,
        sample_rate_mode=sample_rate_mode,
        frame_rate=frame_rate,
        scene_frame_rate=scene_frame_rate,
        start_frame=start_frame,
        end_frame=end_frame,
        sample_count=len(sample_times),
        tracks=tracks,
        key_count_before=key_count_before,
        key_count_after_simplify=key_count_after_simplify,
        key_count_after=key_count_after,
        bake_evaluated_pose=False,
        interpolation=interpolation,
        quaternion_continuity=quaternion_continuity,
        simplify=simplify,
        static_curves=static_curves,
        collapsed_static_track_count=collapsed_static_track_count,
        loop_hint=loop_hint,
    )
    return {
        "type": "asset_bridge.animation_clip",
        "kind": "animation_clip",
        "version": 1,
        "timestamp": int(time.time()),
        "clip": {
            "assetId": _build_current_unity_object_clip_asset_id(object_instance_id, object_action_for_tracks, shape_key_action_for_tracks, action, driver_only_object),
            "name": _build_clip_name(clip_name, "unity_object_v1"),
            "sourceActionName": str(getattr(action, "name", "") or ""),
            "frameRate": float(frame_rate),
            "startFrame": int(start_frame),
            "endFrame": int(end_frame),
            "wrapModeHint": str(loop_hint or "none"),
            "bindingSpace": "unity_object_v1",
            "channelSemantic": "unity_object_v1",
            "exportSettings": {
                "rangeMode": str(range_mode or "action"),
                "sampleMode": str(sample_mode or "scene_frames"),
                "sampleRateMode": str(sample_rate_mode or "scene"),
                "sampleStep": int(sample_step),
                "bakeEvaluatedPose": False,
                "interpolation": str(interpolation or "linear"),
                "quaternionContinuity": bool(quaternion_continuity),
                "simplify": str(simplify or "off"),
                "staticCurves": str(static_curves or STATIC_CURVE_MODE_OFF),
                "sampleCount": int(len(sample_times)),
                "driverOnly": bool(driver_only_object),
                "animationSource": str(source_mode or "combined"),
            },
            "exportContext": {
                "sourceObjectName": object_name,
                "sourceObjectType": object_type,
                "sourcePairId": f"pair-{object_instance_id}",
                "targetKind": "scene_object",
            },
            "exportReport": export_report,
            "tracks": tracks,
        },
    }


def _get_object_shape_keys(obj):
    mesh = getattr(obj, "data", None) if obj is not None else None
    return getattr(mesh, "shape_keys", None) if mesh is not None else None


def _collect_shape_key_animation_sources(obj) -> list[dict]:
    if obj is None:
        return []

    if str(getattr(obj, "type", "") or "") == "ARMATURE":
        mesh_objects = _collect_armature_animation_mesh_parts(obj)
    else:
        mesh_objects = [obj]

    sources = []
    for mesh_obj in mesh_objects:
        shape_keys = _get_object_shape_keys(mesh_obj)
        if shape_keys is None:
            continue
        animation_data = getattr(shape_keys, "animation_data", None)
        action = getattr(animation_data, "action", None) if animation_data is not None else None
        channels = _collect_shape_key_channel_map(action, shape_keys)
        channels.update(_collect_shape_key_driver_channel_map(animation_data, shape_keys))
        sources.append(
            {
                "object": mesh_obj,
                "shape_keys": shape_keys,
                "animation_data": animation_data,
                "action": action,
                "channels": channels,
            }
        )
    return sources


def _collect_armature_animation_mesh_parts(armature_obj):
    # rigged_object.builder imports pose-sampling helpers from this module, so
    # resolve its canonical mesh-part collector only after core is initialized.
    from blender.rigged_object.export_prep import collect_armature_mesh_parts

    return collect_armature_mesh_parts(armature_obj)


def _has_animation_drivers(animation_data) -> bool:
    if animation_data is None:
        return False
    return bool(list(getattr(animation_data, "drivers", []) or []))


def _resolve_armature_sampling_strategy(armature_obj, action, channel_map, requested: str) -> tuple[str, list[str]]:
    requested = requested if requested in {"auto", "raw_fcurve", "evaluated_deform"} else "auto"
    reasons = _detect_evaluated_deform_bake_reasons(armature_obj, action, channel_map)
    if requested == "evaluated_deform":
        return "evaluated_deform", ["Forced by Armature Sampling setting: Evaluated Deform Bones"] + reasons
    if requested == "raw_fcurve":
        return "raw_fcurve", ["Forced by Armature Sampling setting: Raw F-Curve"] + reasons
    if reasons:
        return "evaluated_deform", reasons
    return "raw_fcurve", ["Auto: no animated non-deform bones, constraints, or drivers detected"]


def _detect_evaluated_deform_bake_reasons(armature_obj, action, channel_map) -> list[str]:
    reasons: list[str] = []
    data_bones = getattr(getattr(armature_obj, "data", None), "bones", {}) or {}
    pose_bones = getattr(getattr(armature_obj, "pose", None), "bones", {}) or {}

    for bone_name in sorted((channel_map.get("bones") or {}).keys()):
        bone = data_bones.get(bone_name) if hasattr(data_bones, "get") else None
        if bone is not None and not bool(getattr(bone, "use_deform", False)):
            reasons.append(f"Animated non-deform/controller bone: {bone_name}")

    for pose_bone in pose_bones:
        bone = data_bones.get(pose_bone.name) if hasattr(data_bones, "get") else None
        if bone is None or not bool(getattr(bone, "use_deform", False)):
            continue
        for constraint in getattr(pose_bone, "constraints", []) or []:
            if getattr(constraint, "mute", False):
                continue
            influence = float(getattr(constraint, "influence", 1.0) or 0.0)
            if influence <= 0.0:
                continue
            reasons.append(f"Constraint on deform bone {pose_bone.name}: {getattr(constraint, 'type', 'UNKNOWN')}")
            break

    animation_data = getattr(armature_obj, "animation_data", None)
    drivers = list(getattr(animation_data, "drivers", []) or []) if animation_data is not None else []
    if drivers:
        reasons.append(f"Armature has drivers: {len(drivers)}")

    return reasons

def _collect_action_channel_map(action, animated_id=None) -> dict:
    channels = {"object": {}, "bones": {}}
    if action is None:
        return channels

    for fc in _iter_action_fcurves(action):
        if not _fcurve_has_keyframes(fc):
            continue
        data_path = str(getattr(fc, "data_path", "") or "")
        array_index = int(getattr(fc, "array_index", 0) or 0)
        match = _BONE_FCURVE_RE.match(data_path)
        if match:
            bone_name = match.group("name")
            prop = match.group("prop")
            bone_channels = channels["bones"].setdefault(bone_name, {})
            bone_channels.setdefault(prop, set()).add(array_index)
            continue
        if data_path in _OBJECT_PROPS:
            channels["object"].setdefault(data_path, set()).add(array_index)
    return channels


def _collect_shape_key_channel_map(action, shape_keys=None) -> dict:
    channels: dict[str, dict] = {}
    if action is None or shape_keys is None:
        return channels

    key_blocks = getattr(shape_keys, "key_blocks", []) or []
    valid_names = {str(getattr(block, "name", "") or "") for block in key_blocks}
    for fc in _iter_action_fcurves(action):
        if not _fcurve_has_keyframes(fc):
            continue
        data_path = str(getattr(fc, "data_path", "") or "")
        match = _SHAPE_KEY_FCURVE_RE.match(data_path)
        if not match:
            continue
        name = match.group("name")
        if name and name in valid_names:
            channels[name] = {"value": True}
    return channels


def _collect_object_driver_channel_map(animation_data) -> dict:
    channels: dict[str, set[int]] = {}
    drivers = list(getattr(animation_data, "drivers", []) or []) if animation_data is not None else []
    for fc in drivers:
        data_path = str(getattr(fc, "data_path", "") or "")
        if data_path not in _OBJECT_PROPS:
            continue
        array_index = int(getattr(fc, "array_index", 0) or 0)
        channels.setdefault(data_path, set()).add(array_index)
    return channels


def _merge_object_channel_map(target: dict, source: dict) -> None:
    for data_path, indices in (source or {}).items():
        target.setdefault(data_path, set()).update(indices or set())


def _collect_shape_key_driver_channel_map(animation_data, shape_keys=None) -> dict:
    channels: dict[str, dict] = {}
    if animation_data is None or shape_keys is None:
        return channels

    key_blocks = getattr(shape_keys, "key_blocks", []) or []
    valid_names = {str(getattr(block, "name", "") or "") for block in key_blocks}
    for fc in list(getattr(animation_data, "drivers", []) or []):
        data_path = str(getattr(fc, "data_path", "") or "")
        match = _SHAPE_KEY_FCURVE_RE.match(data_path)
        if not match:
            continue
        name = match.group("name")
        if name and name in valid_names:
            channels[name] = {"value": True}
    return channels


def _expand_channel_map_for_evaluated_pose(armature_obj, channel_map: dict) -> dict:
    expanded = {
        "object": {key: set(value or set()) for key, value in (channel_map.get("object") or {}).items()},
        "bones": {},
    }
    for bone_name, props in (channel_map.get("bones") or {}).items():
        expanded["bones"][bone_name] = {key: set(value or set()) for key, value in (props or {}).items()}

    pose_bones = list(getattr(getattr(armature_obj, "pose", None), "bones", []) or [])
    data_bones = getattr(getattr(armature_obj, "data", None), "bones", {}) or {}
    for pose_bone in pose_bones:
        name = str(getattr(pose_bone, "name", "") or "")
        if not name:
            continue
        data_bone = data_bones.get(name) if hasattr(data_bones, "get") else None
        if data_bone is not None and not bool(getattr(data_bone, "use_deform", False)):
            continue
        props = expanded["bones"].setdefault(name, {})
        props.setdefault("location", set()).update({0, 1, 2})
        props.setdefault("rotation_quaternion", set()).update({0, 1, 2, 3})
        props.setdefault("scale", set()).update({0, 1, 2})
    return expanded


def _fcurve_has_keyframes(fc) -> bool:
    return fc is not None and len(list(getattr(fc, "keyframe_points", []) or [])) > 0


def _iter_action_fcurves(action):
    if action is None:
        return []

    found = []
    seen = set()

    def add_curves(curves):
        for fc in list(curves or []):
            if fc is None:
                continue
            key = getattr(fc, "as_pointer", lambda: id(fc))()
            if key in seen:
                continue
            seen.add(key)
            found.append(fc)

    # Legacy actions expose action.fcurves. Layered actions store curves under
    # action.layers[].strips[].channelbags[].fcurves.
    add_curves(getattr(action, "fcurves", None))
    for layer in list(getattr(action, "layers", []) or []):
        for strip in list(getattr(layer, "strips", []) or []):
            for bag in list(getattr(strip, "channelbags", []) or []):
                add_curves(getattr(bag, "fcurves", None))

    return found


def _sample_active_armature_tracks_unity_rig_v1(context, armature_obj, channel_map: dict, start_frame: int, sample_times: list[tuple[float, float]], interpolation: str, bake_evaluated_pose: bool = False, rig_axis_mode: str = RIG_AXIS_MODE_BAKED_JOINT_AXES, primary_bone_axis: str = "Z", secondary_bone_axis: str = "X", shape_key_sources: list[dict] | None = None) -> list[dict]:
    depsgraph = context.evaluated_depsgraph_get()
    original_frame = context.scene.frame_current
    original_subframe = float(getattr(context.scene, "frame_subframe", 0.0) or 0.0)
    tracks = _TrackWriter(interpolation=interpolation)
    armature_path = _sanitize_binding_segment(str(getattr(armature_obj, "name", "") or ""))
    shape_key_sources = list(shape_key_sources or [])
    axis_correction, axis_correction_inv = build_output_bone_axis_correction(primary_bone_axis, secondary_bone_axis)

    try:
        for source_frame, clip_time in sample_times:
            frame_int = int(source_frame)
            subframe = float(source_frame) - float(frame_int)
            context.scene.frame_set(frame_int, subframe=subframe)
            depsgraph.update()
            eval_armature = armature_obj.evaluated_get(depsgraph)
            pose = getattr(eval_armature, "pose", None)

            _sample_object_channels(tracks, armature_path, eval_armature, channel_map.get("object") or {}, clip_time)
            for source in shape_key_sources:
                mesh_obj = source.get("object")
                mesh_path = _sanitize_binding_segment(str(getattr(mesh_obj, "name", "") or ""))
                _sample_shape_key_channels(
                    tracks,
                    mesh_path,
                    mesh_obj,
                    source.get("channels") or {},
                    clip_time,
                )

            if pose is None:
                continue
            data_bones = getattr(getattr(armature_obj, "data", None), "bones", {}) or {}
            for pose_bone in getattr(pose, "bones", []) or []:
                bone_name = str(getattr(pose_bone, "name", "") or "")
                data_bone = data_bones.get(bone_name) if hasattr(data_bones, "get") else None
                if data_bone is not None and not bool(getattr(data_bone, "use_deform", False)):
                    continue
                bone_channels = (channel_map.get("bones") or {}).get(bone_name)
                if not bone_channels:
                    continue
                bone_path = f"{armature_path}/{_get_bone_binding_path(pose_bone)}" if armature_path else _get_bone_binding_path(pose_bone)
                if rig_axis_mode == RIG_AXIS_MODE_PRESERVE_REST_BONE_AXES:
                    _sample_preserve_rest_bone_axes_channels(tracks, bone_path, pose_bone, bone_channels, clip_time, axis_correction, axis_correction_inv)
                elif bake_evaluated_pose:
                    _sample_evaluated_bone_channels(tracks, bone_path, pose_bone, bone_channels, clip_time)
                else:
                    _sample_bone_channels(tracks, bone_path, pose_bone, bone_channels, clip_time)
    finally:
        context.scene.frame_set(original_frame, subframe=original_subframe)

    return tracks.to_list()


def _sample_active_object_tracks_unity_object_v1(context, obj, channels: dict, shape_key_channels: dict | None, sample_times: list[tuple[float, float]], interpolation: str) -> list[dict]:
    if not channels and not shape_key_channels:
        return []
    depsgraph = context.evaluated_depsgraph_get()
    original_frame = context.scene.frame_current
    original_subframe = float(getattr(context.scene, "frame_subframe", 0.0) or 0.0)
    tracks = _TrackWriter(interpolation=interpolation)
    # unity_object_v1 clips are auto-bound to the synced object itself. In Unity,
    # an empty binding path drives the Animator root Transform; using the object
    # name here would instead target a child named like the object and the root
    # would not move.
    object_path = ""
    object_type = str(getattr(obj, "type", "") or "")

    try:
        for source_frame, clip_time in sample_times:
            frame_int = int(source_frame)
            subframe = float(source_frame) - float(frame_int)
            context.scene.frame_set(frame_int, subframe=subframe)
            depsgraph.update()
            eval_obj = obj.evaluated_get(depsgraph)
            _sample_object_channels(tracks, object_path, eval_obj, channels, clip_time, object_type=object_type)
            _sample_shape_key_channels(tracks, object_path, obj, shape_key_channels or {}, clip_time)
    finally:
        context.scene.frame_set(original_frame, subframe=original_subframe)

    return tracks.to_list()


def _sample_shape_key_channels(tracks, path: str, obj, channels: dict, clip_time: float) -> None:
    if not channels:
        return
    shape_keys = _get_object_shape_keys(obj)
    if shape_keys is None:
        return
    key_blocks = getattr(shape_keys, "key_blocks", []) or []
    for block in key_blocks:
        name = str(getattr(block, "name", "") or "")
        if not name or name not in channels:
            continue
        # Blender shape key values are authored in the key block slider domain
        # (default 0..1). Unity blend shape weights use percentage-like values,
        # so value 1.0 maps to 100.  Preserve non-default slider ranges by using
        # the raw keyed value rather than normalizing between min/max.
        unity_weight = float(getattr(block, "value", 0.0) or 0.0) * 100.0
        tracks.append(path, "blend_shape", "blendShapeWeight", _sanitize_binding_segment(name), clip_time, unity_weight)


def _sample_object_channels(tracks, path: str, obj, channels: dict, clip_time: float, object_type: str | None = None) -> None:
    if not channels or obj is None:
        return
    if "location" in channels:
        pos = getattr(obj, "location", None)
        if pos is not None:
            mapped = _map_position(pos)
            _append_mapped_vector_components(tracks, path, "object", "localPosition", mapped, channels.get("location") or set(), clip_time)
    if "scale" in channels:
        scl = getattr(obj, "scale", None)
        if scl is not None:
            mapped = _map_scale(scl)
            _append_mapped_vector_components(tracks, path, "object", "localScale", mapped, channels.get("scale") or set(), clip_time)
    if "rotation_quaternion" in channels or "rotation_euler" in channels:
        rot = obj.rotation_quaternion if getattr(obj, "rotation_mode", "") == "QUATERNION" else obj.rotation_euler.to_quaternion()
        rot.normalize()
        mapped_rot = _map_quaternion_by_matrix(rot)
        if _object_type_needs_axis_offset(object_type):
            mapped_rot = _mul_quaternion(mapped_rot, _axis_angle_quaternion((1.0, 0.0, 0.0), math.radians(90.0)))
        _append_quaternion(tracks, path, "object", mapped_rot, clip_time)


def _sample_bone_channels(tracks, path: str, pose_bone, channels: dict, clip_time: float) -> None:
    # unity_rig_v1 static bones store joint offsets in localPosition and keep
    # Transform.localRotation as identity.  Therefore animation must export the
    # pose delta relative to the Blender rest bone, not the full parent-relative
    # pose matrix that contains rest bone orientation.  matrix_basis is exactly
    # the keyed local delta in Blender pose space.
    basis = pose_bone.matrix_basis.copy()
    pos, rot, scl = basis.decompose()
    scl = _stable_bone_matrix_scale(basis, scl)
    rot.normalize()

    mapped_basis_pos, mapped_basis_scl = _map_and_canonicalize_bone_basis_components(
        pos,
        scl,
        pose_bone,
    )

    if "location" in channels:
        # Pose-bone location is authored as a delta in the bone's rest-local
        # axes, but unity_rig_v1 static bones store joint offsets directly in
        # Transform.localPosition and keep rotations identity.  Therefore the
        # animation curve must write the absolute Unity localPosition:
        #   mapped rest joint offset + mapped/rest-oriented pose delta.
        # Export all three components whenever any location F-Curve exists so
        # Unity does not overwrite only one component with a delta and leave the
        # rest pose half-applied (the "origin jumps to Hips" symptom).
        mapped_pos = _add_vec3(_get_unity_rig_v1_rest_local_position(pose_bone), mapped_basis_pos)
        _append_vector_components(tracks, path, "bone", "localPosition", mapped_pos, {0, 1, 2}, clip_time)
    if "rotation_quaternion" in channels or "rotation_euler" in channels:
        mapped_rot = _map_bone_basis_quaternion(rot, pose_bone)
        _append_quaternion(tracks, path, "bone", mapped_rot, clip_time)
    if "scale" in channels:
        _append_mapped_vector_components(tracks, path, "bone", "localScale", mapped_basis_scl, channels.get("scale") or set(), clip_time)


def _sample_evaluated_bone_channels(tracks, path: str, pose_bone, channels: dict, clip_time: float) -> None:
    mapped_pos, mapped_rot, mapped_scl = sample_pose_bone_transform_unity_rig_v1(pose_bone)

    if "location" in channels:
        _append_vector_components(tracks, path, "bone", "localPosition", mapped_pos, {0, 1, 2}, clip_time)
    if "rotation_quaternion" in channels or "rotation_euler" in channels:
        _append_quaternion(tracks, path, "bone", mapped_rot, clip_time)
    if "scale" in channels:
        _append_mapped_vector_components(tracks, path, "bone", "localScale", mapped_scl, channels.get("scale") or set(), clip_time)


def _sample_preserve_rest_bone_axes_channels(tracks, path: str, pose_bone, channels: dict, clip_time: float, axis_correction=None, axis_correction_inv=None) -> None:
    mapped_pos, mapped_rot, mapped_scl = sample_pose_bone_transform_preserve_rest_bone_axes(pose_bone, axis_correction, axis_correction_inv)

    if "location" in channels or "rotation_quaternion" in channels or "rotation_euler" in channels or "scale" in channels:
        _append_vector_components(tracks, path, "bone", "localPosition", mapped_pos, {0, 1, 2}, clip_time)
        _append_quaternion(tracks, path, "bone", mapped_rot, clip_time)
        _append_vector_components(tracks, path, "bone", "localScale", mapped_scl, {0, 1, 2}, clip_time)


def sample_pose_bone_transform_unity_rig_v1(pose_bone) -> tuple[tuple[float, float, float], object, tuple[float, float, float]]:
    local_pose = _get_evaluated_local_pose_matrix(pose_bone)
    rest_local = _get_rest_local_matrix(pose_bone)
    basis = rest_local.inverted_safe() @ local_pose
    pos, rot, scl = basis.decompose()
    # Scale belongs to the evaluated parent-relative pose matrix.  Reading it
    # from `basis` would also divide by scale/shear embedded in imported rest
    # bone matrices, which is not part of the Unity joint Transform contract.
    scl = _stable_bone_matrix_scale(local_pose, scl)
    rot.normalize()

    mapped_basis_pos, mapped_scl = _map_and_canonicalize_bone_basis_components(
        pos,
        scl,
        pose_bone,
    )
    mapped_pos = _add_vec3(_get_unity_rig_v1_rest_local_position(pose_bone), mapped_basis_pos)
    mapped_rot = _map_bone_basis_quaternion(rot, pose_bone)
    return mapped_pos, mapped_rot, mapped_scl


def sample_pose_bone_transform_preserve_rest_bone_axes(pose_bone, axis_correction=None, axis_correction_inv=None) -> tuple[tuple[float, float, float], object, tuple[float, float, float]]:
    local_pose = _get_evaluated_local_pose_matrix(pose_bone)
    mapped_matrix = _map_preserve_rest_bone_matrix(pose_bone, local_pose, axis_correction, axis_correction_inv)
    pos, rot, scl = mapped_matrix.decompose()
    scl = _stable_bone_matrix_scale(mapped_matrix, scl)
    rot.normalize()

    # Compare the evaluated result with the same bone's authored local basis.
    # This removes matrix-inversion noise while preserving a real constraint
    # result once it exceeds the small numerical-noise window.
    authored_basis = getattr(pose_bone, "matrix_basis", None)
    if authored_basis is not None:
        authored_local = _get_rest_local_matrix(pose_bone) @ authored_basis.copy()
        authored_matrix = _map_preserve_rest_bone_matrix(
            pose_bone,
            authored_local,
            axis_correction,
            axis_correction_inv,
        )
        authored_pos, _, authored_scl = authored_matrix.decompose()
        authored_scl = _stable_bone_matrix_scale(authored_matrix, authored_scl)
        mapped_pos = _snap_vector_to_reference(
            _float_vec3(pos),
            _float_vec3(authored_pos),
            BONE_POSITION_NOISE_EPSILON,
        )
        mapped_scl = _snap_vector_to_reference(
            _float_vec3(scl),
            _float_vec3(authored_scl),
            BONE_SCALE_NOISE_EPSILON,
        )
    else:
        mapped_pos = _float_vec3(pos)
        mapped_scl = _snap_vector_to_reference(
            _float_vec3(scl),
            (1.0, 1.0, 1.0),
            BONE_SCALE_NOISE_EPSILON,
        )
    return mapped_pos, rot, mapped_scl


def _map_preserve_rest_bone_matrix(pose_bone, matrix, axis_correction=None, axis_correction_inv=None):
    mapped_matrix = _map_full_matrix_by_basis(matrix)
    return apply_output_bone_axis_correction(
        mapped_matrix,
        has_parent=getattr(pose_bone, "parent", None) is not None,
        correction=axis_correction,
        correction_inv=axis_correction_inv,
    )


def _get_evaluated_local_pose_matrix(pose_bone):
    parent = getattr(pose_bone, "parent", None)
    matrix = pose_bone.matrix.copy()
    if parent is None:
        return matrix
    return parent.matrix.inverted_safe() @ matrix


def _get_rest_local_matrix(pose_bone):
    bone = getattr(pose_bone, "bone", None)
    if bone is None:
        from mathutils import Matrix  # type: ignore
        return Matrix.Identity(4)
    parent = getattr(bone, "parent", None)
    if parent is None:
        return bone.matrix_local.copy()
    return parent.matrix_local.inverted_safe() @ bone.matrix_local


def _map_position(vec):
    return (
        float(_vector_component(vec, 0)),
        float(_vector_component(vec, 2)),
        float(_vector_component(vec, 1)),
    )


def _map_scale(vec):
    return (
        float(_vector_component(vec, 0)),
        float(_vector_component(vec, 2)),
        float(_vector_component(vec, 1)),
    )


def _vector_component(vec, index: int):
    names = ("x", "y", "z")
    try:
        return getattr(vec, names[index])
    except AttributeError:
        return vec[index]


def _float_vec3(vec) -> tuple[float, float, float]:
    return tuple(float(_vector_component(vec, index)) for index in range(3))


def _snap_scalar_to_reference(value: float, reference: float, epsilon: float) -> float:
    value = float(value)
    reference = float(reference)
    if math.isfinite(value) and math.isfinite(reference) and abs(value - reference) <= float(epsilon):
        return reference
    return value


def _snap_vector_to_reference(values, reference, epsilon: float) -> tuple[float, float, float]:
    return tuple(
        _snap_scalar_to_reference(values[index], reference[index], epsilon)
        for index in range(3)
    )


def _stable_bone_matrix_scale(matrix, decomposed_scale):
    """Read TRS scale from column lengths before applying noise snapping.

    Blender's shear-aware decomposition can amplify tiny non-orthogonality in
    evaluated constraint matrices (for example, 1.00000002 becoming 1.0000325).
    Column lengths preserve actual stretch while avoiding that amplification.
    Keep the decomposition signs for mirrored bones and fall back when a test
    double or an older API does not expose the 3x3 columns.
    """
    try:
        matrix3 = matrix.to_3x3()
        lengths = [float(matrix3.col[index].length) for index in range(3)]
        return tuple(
            math.copysign(lengths[index], float(decomposed_scale[index]))
            for index in range(3)
        )
    except Exception:
        return (
            float(decomposed_scale.x),
            float(decomposed_scale.y),
            float(decomposed_scale.z),
        )


def _map_and_canonicalize_bone_basis_components(pos, scl, pose_bone):
    mapped_pos = _map_bone_basis_position(pos, pose_bone)
    authored_pos = getattr(pose_bone, "location", None)
    if authored_pos is None:
        authored_pos = pos
    mapped_authored_pos = _map_bone_basis_position(authored_pos, pose_bone)
    mapped_pos = _snap_vector_to_reference(
        mapped_pos,
        mapped_authored_pos,
        BONE_POSITION_NOISE_EPSILON,
    )

    mapped_scl = _map_scale(scl)
    authored_scl = getattr(pose_bone, "scale", None)
    if authored_scl is None:
        authored_scl = scl
    mapped_authored_scl = _map_scale(authored_scl)
    mapped_scl = _snap_vector_to_reference(
        mapped_scl,
        mapped_authored_scl,
        BONE_SCALE_NOISE_EPSILON,
    )
    return mapped_pos, mapped_scl



def _map_quaternion_by_matrix(quat):
    # Convert a Blender-space rotation into the same Y/Z-swapped basis used by
    # scene sync matrices: M_u = C * M_b * C^-1.  Decompose back to quaternion.
    if bpy is None:
        return quat
    from mathutils import Matrix  # type: ignore
    basis = _unity_rig_v1_basis_matrix(Matrix)
    matrix = basis @ quat.to_matrix() @ basis.inverted_safe()
    mapped = matrix.to_quaternion()
    mapped.normalize()
    return mapped


def _map_full_matrix_by_basis(matrix):
    if bpy is None:
        return matrix
    from mathutils import Matrix  # type: ignore
    basis = _unity_rig_v1_basis_matrix(Matrix).to_4x4()
    return basis @ matrix @ basis.inverted_safe()


def _object_type_needs_axis_offset(object_type: str | None) -> bool:
    value = str(object_type or "").strip().lower()
    return value in {"camera", "light"}


def _axis_angle_quaternion(axis, angle_radians: float):
    from mathutils import Quaternion  # type: ignore
    return Quaternion(axis, angle_radians)


def _mul_quaternion(a, b):
    result = a @ b
    result.normalize()
    return result


def _unity_rig_v1_basis_matrix(matrix_cls):
    return matrix_cls(((1.0, 0.0, 0.0), (0.0, 0.0, 1.0), (0.0, 1.0, 0.0)))


def _add_vec3(a, b):
    return (float(a[0]) + float(b[0]), float(a[1]) + float(b[1]), float(a[2]) + float(b[2]))


def _get_unity_rig_v1_rest_local_position(pose_bone):
    if pose_bone is None:
        return (0.0, 0.0, 0.0)
    rest_bone = getattr(pose_bone, "bone", None)
    head_local = getattr(rest_bone, "head_local", None)
    if head_local is None:
        return (0.0, 0.0, 0.0)
    parent = getattr(rest_bone, "parent", None)
    parent_head = getattr(parent, "head_local", None) if parent is not None else None
    if parent_head is not None:
        offset = head_local - parent_head
    else:
        offset = head_local.copy()
    return _map_position(offset)


def _map_bone_basis_position(vec, pose_bone):
    if bpy is None or pose_bone is None or vec is None:
        return _map_position(vec)
    from mathutils import Matrix  # type: ignore
    rest_bone = getattr(pose_bone, "bone", None)
    rest_matrix = getattr(rest_bone, "matrix_local", None)
    if rest_matrix is None:
        return _map_position(vec)

    # matrix_basis translation is keyed in the Blender bone's rest-local axes.
    # Move that vector into armature/joint axes with the same rest-orientation
    # idea used for rotation, then apply the unity_rig_v1 basis map.
    basis = _unity_rig_v1_basis_matrix(Matrix)
    rest_rot = rest_matrix.to_quaternion().to_matrix()
    delta_armature = rest_rot @ vec
    mapped = basis @ delta_armature
    return (float(mapped.x), float(mapped.y), float(mapped.z))


def _map_bone_basis_quaternion(quat, pose_bone):
    # pose_bone.matrix_basis rotation is keyed in the Blender bone's own rest
    # local axes.  unity_rig_v1 static bones intentionally keep
    # Transform.localRotation = identity and represent rest pose with joint
    # offsets, so their local axes are the parent/joint axes instead of Blender's
    # rolled bone axes.  Conjugate the keyed delta by the rest bone orientation
    # first, then apply Blender->Unity basis conversion.
    if bpy is None or pose_bone is None:
        return _map_quaternion_by_matrix(quat)
    from mathutils import Matrix  # type: ignore
    rest_bone = getattr(pose_bone, "bone", None)
    rest_matrix = getattr(rest_bone, "matrix_local", None)
    if rest_matrix is None:
        return _map_quaternion_by_matrix(quat)

    basis = _unity_rig_v1_basis_matrix(Matrix)
    rest_rot = rest_matrix.to_quaternion().to_matrix()
    delta_armature = rest_rot @ quat.to_matrix() @ rest_rot.inverted_safe()
    mapped_matrix = basis @ delta_armature @ basis.inverted_safe()
    mapped = mapped_matrix.to_quaternion()
    mapped.normalize()
    return mapped


def _append_vector_components(tracks, path: str, target_type: str, property_name: str, values, components: set[int], time_value: float) -> None:
    component_names = ("x", "y", "z")
    for i in sorted(int(v) for v in components):
        if 0 <= i < 3:
            tracks.append(path, target_type, property_name, component_names[i], time_value, float(values[i]))


def _append_mapped_vector_components(tracks, path: str, target_type: str, property_name: str, values, components: set[int], time_value: float) -> None:
    # F-Curve array indices are Blender source axes, but `values` has already
    # been remapped into unity_rig_v1 axes as (x, z, y).  Remap sparse/partial
    # vector channels together with their component names:
    #   Blender X -> Unity X, Blender Y -> Unity Z, Blender Z -> Unity Y.
    source_to_unity_component = {
        0: ("x", 0),
        1: ("z", 2),
        2: ("y", 1),
    }
    for i in sorted(int(v) for v in components):
        mapped = source_to_unity_component.get(i)
        if mapped is not None:
            component_name, value_index = mapped
            tracks.append(path, target_type, property_name, component_name, time_value, float(values[value_index]))


def _append_quaternion(tracks, path: str, target_type: str, quat, time_value: float) -> None:
    # Rotation components are not independent.  If any Blender rotation F-Curve
    # exists, export a complete Unity quaternion.
    tracks.append(path, target_type, "localRotation", "x", time_value, float(quat.x))
    tracks.append(path, target_type, "localRotation", "y", time_value, float(quat.y))
    tracks.append(path, target_type, "localRotation", "z", time_value, float(quat.z))
    tracks.append(path, target_type, "localRotation", "w", time_value, float(quat.w))


class _TrackWriter:
    def __init__(self, interpolation: str = "linear"):
        self._tracks = {}
        self._interpolation = str(interpolation or "linear")

    def append(self, path: str, target_type: str, property_name: str, component: str, time_value: float, value: float) -> None:
        key = (path, target_type, property_name, component)
        track = self._tracks.get(key)
        if track is None:
            track = {
                "path": path,
                "targetType": target_type,
                "property": property_name,
                "component": component,
                "interpolation": self._interpolation,
                "keys": [],
            }
            self._tracks[key] = track
        track["keys"].append({"time": float(time_value), "value": float(value)})

    def to_list(self):
        return list(self._tracks.values())


def _resolve_frame_range_for_actions(scene, actions, range_mode: str) -> tuple[int, int]:
    actions = [action for action in list(actions or []) if action is not None]
    primary = actions[0] if actions else None
    if range_mode != "action" or not actions:
        return _resolve_frame_range(scene, primary, range_mode)

    ranges = [getattr(action, "frame_range", (1.0, 1.0)) for action in actions]
    start_frame = int(min(float(frame_range[0]) for frame_range in ranges))
    end_frame = int(max(float(frame_range[1]) for frame_range in ranges))
    return start_frame, max(start_frame, end_frame)


def _resolve_frame_range(scene, action, range_mode: str) -> tuple[int, int]:
    action_start, action_end = getattr(action, "frame_range", (1.0, 1.0))
    if range_mode == "scene" and scene is not None:
        return int(scene.frame_start), int(scene.frame_end)
    if range_mode == "custom" and scene is not None:
        start_frame = int(getattr(scene, "blendersync_animation_clip_start_frame", int(action_start)) or int(action_start))
        end_frame = int(getattr(scene, "blendersync_animation_clip_end_frame", int(action_end)) or int(action_end))
        return start_frame, max(start_frame, end_frame)
    return int(action_start), max(int(action_start), int(action_end))


def _resolve_scene_frame_rate(scene) -> float:
    if scene is None:
        return 30.0
    render = getattr(scene, "render", None)
    fps = float(getattr(render, "fps", 30.0) or 30.0)
    fps_base = float(getattr(render, "fps_base", 1.0) or 1.0)
    if fps_base == 0.0:
        fps_base = 1.0
    return max(1.0, fps / fps_base)


def _resolve_frame_rate(scene, sample_rate_mode: str) -> float:
    if scene is None:
        return 30.0
    fixed = {
        "fps_30": 30.0,
        "fps_60": 60.0,
        "fps_120": 120.0,
    }
    if sample_rate_mode in fixed:
        return fixed[sample_rate_mode]
    if sample_rate_mode == "custom":
        value = float(getattr(scene, "blendersync_animation_clip_sample_rate", 30.0) or 30.0)
        return max(1.0, value)
    return _resolve_scene_frame_rate(scene)


def _build_sample_times_for_actions(actions, start_frame: int, end_frame: int, frame_rate: float, scene_frame_rate: float, sample_mode: str, sample_step: int) -> list[tuple[float, float]]:
    actions = [a for a in list(actions or []) if a is not None]
    primary = actions[0] if actions else None
    start = float(start_frame)
    end = float(max(start_frame, end_frame))
    export_fps = max(float(frame_rate), 1.0)
    scene_fps = max(float(scene_frame_rate), 1.0)
    mode = str(sample_mode or "scene_frames")
    if mode != "keyframes":
        return _build_sample_times(primary, start_frame, end_frame, frame_rate, scene_frame_rate, sample_mode, sample_step)

    values = {start, end}
    for action in actions:
        for fc in _iter_action_fcurves(action):
            if not _fcurve_has_keyframes(fc):
                continue
            for kp in list(getattr(fc, "keyframe_points", []) or []):
                co = getattr(kp, "co", None)
                if co is None:
                    continue
                frame = float(co.x)
                if start <= frame <= end:
                    values.add(frame)
    return [(frame, _frame_to_clip_time(frame, start, scene_fps)) for frame in _dedupe_sorted_frames(sorted(values))]


def _build_sample_times(action, start_frame: int, end_frame: int, frame_rate: float, scene_frame_rate: float, sample_mode: str, sample_step: int) -> list[tuple[float, float]]:
    start = float(start_frame)
    end = float(max(start_frame, end_frame))
    export_fps = max(float(frame_rate), 1.0)
    scene_fps = max(float(scene_frame_rate), 1.0)
    mode = str(sample_mode or "scene_frames")
    frames: list[float] = []

    if mode == "fixed_fps":
        # Source frames live in Blender scene-frame units.  Clip time lives in
        # seconds.  Changing export FPS should change sample density, not the
        # real animation duration.
        duration_seconds = max(0.0, (end - start) / scene_fps)
        count = int(duration_seconds * export_fps + 0.5)
        source_step = scene_fps / export_fps
        frames = [start + i * source_step for i in range(count + 1)]
        if not frames or abs(frames[-1] - end) > 1e-5:
            frames.append(end)
    elif mode == "keyframes":
        values = {start, end}
        for fc in _iter_action_fcurves(action):
            if not _fcurve_has_keyframes(fc):
                continue
            for kp in list(getattr(fc, "keyframe_points", []) or []):
                co = getattr(kp, "co", None)
                if co is None:
                    continue
                frame = float(co.x)
                if start <= frame <= end:
                    values.add(frame)
        frames = sorted(values)
    else:
        step = max(1, int(sample_step or 1))
        current = int(start_frame)
        while current <= int(end):
            frames.append(float(current))
            current += step
        if end >= start and (not frames or abs(frames[-1] - end) > 1e-5):
            frames.append(end)

    return [(frame, _frame_to_clip_time(frame, start, scene_fps)) for frame in _dedupe_sorted_frames(frames)]


def _dedupe_sorted_frames(frames: list[float]) -> list[float]:
    result = []
    for frame in sorted(float(v) for v in frames):
        if not result or abs(result[-1] - frame) > 1e-5:
            result.append(frame)
    return result


def _frame_to_clip_time(frame: float, start_frame: float, frame_rate: float) -> float:
    return float(frame - start_frame) / max(float(frame_rate), 1.0)


def _estimate_json_bytes(payload: dict) -> int:
    try:
        return len(json.dumps(payload, ensure_ascii=False).encode("utf-8"))
    except Exception:
        return -1


def _count_track_keys(tracks: list[dict]) -> int:
    return sum(len(track.get("keys") or []) for track in tracks or [])


def _build_export_report(clip_name: str, sample_mode: str, sample_rate_mode: str, frame_rate: float, scene_frame_rate: float, start_frame: int, end_frame: int, sample_count: int, tracks: list[dict], key_count_before: int, key_count_after_simplify: int, key_count_after: int, bake_evaluated_pose: bool, interpolation: str, quaternion_continuity: bool, simplify: str, static_curves: str, collapsed_static_track_count: int, loop_hint: str, armature_sampling_requested: str = "auto", armature_sampling_mode: str = "raw_fcurve", armature_sampling_reasons: list[str] | None = None) -> dict:
    property_counts: dict[str, int] = {}
    target_paths = set()
    for track in tracks or []:
        prop = str(track.get("property") or "unknown")
        property_counts[prop] = property_counts.get(prop, 0) + 1
        target_paths.add(str(track.get("path") or ""))
    return {
        "clipName": str(clip_name or "AnimationClip"),
        "sampleMode": str(sample_mode or "scene_frames"),
        "sampleRateMode": str(sample_rate_mode or "scene"),
        "frameRate": float(frame_rate),
        "sceneFrameRate": float(scene_frame_rate),
        "startFrame": int(start_frame),
        "endFrame": int(end_frame),
        "durationSeconds": float(max(0.0, (float(end_frame) - float(start_frame)) / max(float(scene_frame_rate), 1.0))),
        "sampleCount": int(sample_count),
        "trackCount": int(len(tracks or [])),
        "keyCountBeforeSimplify": int(key_count_before),
        "keyCountAfterSimplify": int(key_count_after_simplify),
        "keyCount": int(key_count_after),
        "removedKeyCount": int(max(0, key_count_before - key_count_after)),
        "simplifyRemovedKeyCount": int(max(0, key_count_before - key_count_after_simplify)),
        "staticCurveRemovedKeyCount": int(max(0, key_count_after_simplify - key_count_after)),
        "collapsedStaticTrackCount": int(max(0, collapsed_static_track_count)),
        "targetCount": int(len(target_paths)),
        "propertyTrackCounts": property_counts,
        "bakeEvaluatedPose": bool(bake_evaluated_pose),
        "armatureSamplingRequested": str(armature_sampling_requested or "auto"),
        "armatureSamplingMode": str(armature_sampling_mode or "raw_fcurve"),
        "armatureSamplingReasons": list(armature_sampling_reasons or []),
        "onlyDeformBones": True,
        "interpolation": str(interpolation or "linear"),
        "quaternionContinuity": bool(quaternion_continuity),
        "simplify": str(simplify or "off"),
        "staticCurves": str(static_curves or STATIC_CURVE_MODE_OFF),
        "loop": str(loop_hint or "none"),
    }


def _format_export_report(report: dict) -> str:
    if not report:
        return "No animation clip exported yet."
    props = report.get("propertyTrackCounts") or {}
    prop_text = ", ".join(f"{k}:{v}" for k, v in sorted(props.items())) if props else "none"
    reasons = [str(v) for v in list(report.get("armatureSamplingReasons") or []) if str(v)]
    lines = [
        f"Clip: {report.get('clipName', 'AnimationClip')}",
        f"Mode/FPS: {report.get('sampleMode')} @ {float(report.get('frameRate', 0.0)):.2f} fps (scene {float(report.get('sceneFrameRate', 0.0)):.2f})",
        f"Range: {report.get('startFrame')} - {report.get('endFrame')} ({float(report.get('durationSeconds', 0.0)):.3f}s), Samples: {report.get('sampleCount')}",
        f"Tracks: {report.get('trackCount')}, Targets: {report.get('targetCount')}, Keys: {report.get('keyCount')}",
        f"Simplify: {report.get('simplify')} removed {report.get('simplifyRemovedKeyCount', report.get('removedKeyCount', 0))} / {report.get('keyCountBeforeSimplify', report.get('keyCount', 0))}",
        f"Static Curves: {report.get('staticCurves', STATIC_CURVE_MODE_OFF)} collapsed {report.get('collapsedStaticTrackCount', 0)} tracks, removed {report.get('staticCurveRemovedKeyCount', 0)} keys",
        f"Bake: {report.get('bakeEvaluatedPose')}, Armature Sampling: {report.get('armatureSamplingMode')} ({report.get('armatureSamplingRequested')}), Interp: {report.get('interpolation')}, QContinuity: {report.get('quaternionContinuity')}",
    ]
    if reasons:
        lines.append("Sampling Reason: " + "; ".join(reasons[:4]))
        if len(reasons) > 4:
            lines.append(f"Sampling Reason: ... +{len(reasons) - 4} more")
    lines.append(f"Properties: {prop_text}")
    return "\n".join(lines)


def _normalize_static_curve_mode(mode: str) -> str:
    value = str(mode or STATIC_CURVE_MODE_OFF)
    if value in {STATIC_CURVE_MODE_OFF, STATIC_CURVE_MODE_COLLAPSE_CONSTANT}:
        return value
    return STATIC_CURVE_MODE_OFF


def _collapse_constant_transform_tracks(tracks: list[dict], mode: str) -> int:
    if _normalize_static_curve_mode(mode) != STATIC_CURVE_MODE_COLLAPSE_CONSTANT:
        return 0

    grouped: dict[tuple[str, str, str], dict[str, dict]] = {}
    for track in tracks or []:
        if str(track.get("property") or "") not in {"localPosition", "localRotation", "localScale"}:
            continue
        key = (
            str(track.get("path") or ""),
            str(track.get("targetType") or ""),
            str(track.get("property") or ""),
        )
        grouped.setdefault(key, {})[str(track.get("component") or "")] = track

    collapsed = 0
    for (_path, _target_type, prop), components in grouped.items():
        names = ("x", "y", "z", "w") if prop == "localRotation" else ("x", "y", "z")
        if not all(name in components for name in names):
            continue
        group = [components[name] for name in names]
        if not _animation_tracks_have_aligned_keys(group):
            continue
        keys = [list(track.get("keys") or []) for track in group]
        if len(keys[0]) <= 1:
            continue
        if prop == "localRotation":
            is_constant = _constant_quaternion_keys(keys)
        else:
            is_constant = _constant_vector_keys(keys)
        if not is_constant:
            continue
        for track in group:
            track["keys"] = [dict(track["keys"][0])]
        collapsed += len(group)
    return collapsed


def _animation_tracks_have_aligned_keys(group: list[dict]) -> bool:
    if not group:
        return False
    reference = list(group[0].get("keys") or [])
    if not reference:
        return False
    reference_times = [float(key.get("time", 0.0)) for key in reference]
    if not all(math.isfinite(value) for value in reference_times):
        return False
    for track in group[1:]:
        keys = list(track.get("keys") or [])
        if len(keys) != len(reference):
            return False
        candidate_times = [float(key.get("time", 0.0)) for key in keys]
        if not all(math.isfinite(value) for value in candidate_times):
            return False
        for left, right in zip(reference_times, candidate_times):
            if abs(left - right) > 1e-8:
                return False
    return True


def _constant_vector_keys(keys: list[list[dict]]) -> bool:
    if not keys or not keys[0]:
        return False
    first = tuple(float(component[0].get("value", 0.0)) for component in keys)
    if not all(math.isfinite(value) for value in first):
        return False
    for index in range(1, len(keys[0])):
        current = tuple(float(component[index].get("value", 0.0)) for component in keys)
        if not all(math.isfinite(value) for value in current):
            return False
        distance = math.sqrt(sum((current[i] - first[i]) ** 2 for i in range(len(first))))
        if distance > STATIC_CURVE_VALUE_EPSILON:
            return False
    return True


def _constant_quaternion_keys(keys: list[list[dict]]) -> bool:
    if not keys or not keys[0]:
        return False
    first_raw = tuple(float(component[0].get("value", 0.0)) for component in keys)
    if not _valid_static_quaternion(first_raw):
        return False
    first = _quat_normalize(first_raw)
    for index in range(1, len(keys[0])):
        current_raw = tuple(float(component[index].get("value", 0.0)) for component in keys)
        if not _valid_static_quaternion(current_raw):
            return False
        current = _quat_normalize(current_raw)
        if _quat_angle_degrees(first, current) > STATIC_CURVE_ROTATION_EPSILON_DEGREES:
            return False
    return True


def _valid_static_quaternion(value) -> bool:
    return (
        all(math.isfinite(component) for component in value)
        and sum(component * component for component in value) > 1e-24
    )


def _ensure_quaternion_continuity(tracks: list[dict]) -> None:
    grouped: dict[tuple[str, str], dict[str, dict]] = {}
    for track in tracks or []:
        if track.get("property") != "localRotation":
            continue
        key = (track.get("path") or "", track.get("targetType") or "")
        grouped.setdefault(key, {})[track.get("component") or ""] = track

    for components in grouped.values():
        if not all(c in components for c in ("x", "y", "z", "w")):
            continue
        key_count = min(len(components[c].get("keys") or []) for c in ("x", "y", "z", "w"))
        prev = None
        for i in range(key_count):
            quat = [float(components[c]["keys"][i].get("value", 0.0)) for c in ("x", "y", "z", "w")]
            if prev is not None:
                dot = sum(prev[j] * quat[j] for j in range(4))
                if dot < 0.0:
                    quat = [-v for v in quat]
                    for c, value in zip(("x", "y", "z", "w"), quat):
                        components[c]["keys"][i]["value"] = float(value)
            prev = quat


def _simplify_tracks(tracks: list[dict], mode: str) -> None:
    tolerances = _simplify_tolerances(mode)
    if tolerances is None:
        return

    grouped: dict[tuple[str, str, str], dict[str, dict]] = {}
    passthrough = []
    for track in tracks or []:
        prop = str(track.get("property") or "")
        if prop in {"localPosition", "localRotation", "localScale"}:
            key = (str(track.get("path") or ""), str(track.get("targetType") or ""), prop)
            grouped.setdefault(key, {})[str(track.get("component") or "")] = track
        elif prop == "blendShapeWeight":
            _simplify_scalar_track(track, tolerances["blendShapeWeight"])
        else:
            passthrough.append(track)

    for (path, target_type, prop), components in grouped.items():
        if prop == "localRotation":
            _simplify_quaternion_group(components, tolerances["rotationDegrees"])
        elif prop == "localPosition":
            _simplify_vector_group(components, ("x", "y", "z"), tolerances["position"])
        elif prop == "localScale":
            _simplify_vector_group(components, ("x", "y", "z"), tolerances["scale"])


def _simplify_tolerances(mode: str):
    presets = {
        "light": {"position": 0.0001, "scale": 0.0001, "rotationDegrees": 0.05, "blendShapeWeight": 0.01},
        "medium": {"position": 0.001, "scale": 0.001, "rotationDegrees": 0.25, "blendShapeWeight": 0.1},
        "aggressive": {"position": 0.01, "scale": 0.01, "rotationDegrees": 1.0, "blendShapeWeight": 0.5},
    }
    return presets.get(str(mode or "off"))


def _simplify_vector_group(components: dict[str, dict], component_names: tuple[str, ...], tolerance: float) -> None:
    if not all(name in components for name in component_names):
        for track in components.values():
            _simplify_scalar_track(track, tolerance)
        return
    key_count = min(len(components[name].get("keys") or []) for name in component_names)
    if key_count <= 2:
        return
    keep = [False] * key_count
    keep[0] = True
    keep[-1] = True
    kept_prev = 0
    for i in range(1, key_count - 1):
        next_index = i + 1
        t0 = float(components[component_names[0]]["keys"][kept_prev].get("time", 0.0))
        t1 = float(components[component_names[0]]["keys"][i].get("time", 0.0))
        t2 = float(components[component_names[0]]["keys"][next_index].get("time", 0.0))
        if abs(t2 - t0) <= 1e-8:
            keep[i] = True
            kept_prev = i
            continue
        alpha = (t1 - t0) / (t2 - t0)
        error_sq = 0.0
        for name in component_names:
            keys = components[name]["keys"]
            v0 = float(keys[kept_prev].get("value", 0.0))
            v1 = float(keys[i].get("value", 0.0))
            v2 = float(keys[next_index].get("value", 0.0))
            expected = v0 + (v2 - v0) * alpha
            error_sq += (v1 - expected) * (v1 - expected)
        if math.sqrt(error_sq) > tolerance:
            keep[i] = True
            kept_prev = i
    _apply_keep_mask(components.values(), keep)


def _simplify_quaternion_group(components: dict[str, dict], tolerance_degrees: float) -> None:
    names = ("x", "y", "z", "w")
    if not all(name in components for name in names):
        return
    key_count = min(len(components[name].get("keys") or []) for name in names)
    if key_count <= 2:
        return
    keep = [False] * key_count
    keep[0] = True
    keep[-1] = True
    kept_prev = 0
    for i in range(1, key_count - 1):
        next_index = i + 1
        t0 = float(components["x"]["keys"][kept_prev].get("time", 0.0))
        t1 = float(components["x"]["keys"][i].get("time", 0.0))
        t2 = float(components["x"]["keys"][next_index].get("time", 0.0))
        if abs(t2 - t0) <= 1e-8:
            keep[i] = True
            kept_prev = i
            continue
        alpha = (t1 - t0) / (t2 - t0)
        q0 = _read_quat(components, kept_prev)
        q1 = _read_quat(components, i)
        q2 = _read_quat(components, next_index)
        expected = _quat_normalize(tuple(q0[j] + (q2[j] - q0[j]) * alpha for j in range(4)))
        error = _quat_angle_degrees(expected, q1)
        if error > tolerance_degrees:
            keep[i] = True
            kept_prev = i
    _apply_keep_mask(components.values(), keep)


def _simplify_scalar_track(track: dict, tolerance: float) -> None:
    keys = list(track.get("keys") or [])
    if len(keys) <= 2:
        return
    keep = [False] * len(keys)
    keep[0] = True
    keep[-1] = True
    kept_prev = 0
    for i in range(1, len(keys) - 1):
        next_index = i + 1
        t0 = float(keys[kept_prev].get("time", 0.0))
        t1 = float(keys[i].get("time", 0.0))
        t2 = float(keys[next_index].get("time", 0.0))
        if abs(t2 - t0) <= 1e-8:
            keep[i] = True
            kept_prev = i
            continue
        alpha = (t1 - t0) / (t2 - t0)
        v0 = float(keys[kept_prev].get("value", 0.0))
        v1 = float(keys[i].get("value", 0.0))
        v2 = float(keys[next_index].get("value", 0.0))
        expected = v0 + (v2 - v0) * alpha
        if abs(v1 - expected) > tolerance:
            keep[i] = True
            kept_prev = i
    track["keys"] = [key for key, should_keep in zip(keys, keep) if should_keep]


def _apply_keep_mask(track_iterable, keep: list[bool]) -> None:
    for track in track_iterable:
        keys = list(track.get("keys") or [])
        track["keys"] = [key for key, should_keep in zip(keys, keep) if should_keep]


def _read_quat(components: dict[str, dict], index: int) -> tuple[float, float, float, float]:
    return _quat_normalize(tuple(float(components[name]["keys"][index].get("value", 0.0)) for name in ("x", "y", "z", "w")))


def _quat_normalize(q) -> tuple[float, float, float, float]:
    length = math.sqrt(sum(float(v) * float(v) for v in q))
    if length <= 1e-12:
        return (0.0, 0.0, 0.0, 1.0)
    return tuple(float(v) / length for v in q)


def _quat_angle_degrees(a, b) -> float:
    dot = abs(sum(float(a[i]) * float(b[i]) for i in range(4)))
    dot = max(-1.0, min(1.0, dot))
    return math.degrees(2.0 * math.acos(dot))


def _get_bone_binding_path(pose_bone) -> str:
    segments = []
    current = pose_bone
    while current is not None:
        segments.append(_sanitize_binding_segment(str(getattr(current, "name", "") or "")))
        current = getattr(current, "parent", None)
    segments.reverse()
    return "/".join(segment for segment in segments if segment)


def _sanitize_binding_segment(value: str) -> str:
    text = str(value or "").strip()
    if not text:
        return "unnamed"
    return text.replace("/", "_").replace("\\", "_")



def _build_current_unity_object_clip_asset_id(object_instance_id: str, object_action, shape_key_action, fallback_action, driver_only_object: bool = False) -> str:
    if driver_only_object:
        return f"animobjectv1-{object_instance_id}-drivers"
    # Keep the Unity clip identity stable for the current Blender animation.
    # Blender users define the exported channel set by connecting/disconnecting
    # the Object action and Shape Key action.  If both slots currently point to
    # the same Action datablock, disconnecting one slot should update the same
    # Unity .anim instead of creating a second asset.
    if object_action is not None and shape_key_action is not None and object_action == shape_key_action:
        return f"animobjectv1-{object_instance_id}-{ensure_asset_id(object_action)}"
    if object_action is not None:
        return f"animobjectv1-{object_instance_id}-{ensure_asset_id(object_action)}"
    if shape_key_action is not None:
        return f"animobjectv1-{object_instance_id}-{ensure_asset_id(shape_key_action)}"
    return f"animobjectv1-{object_instance_id}-{ensure_asset_id(fallback_action)}"


def _build_current_unity_rig_clip_asset_id(clip_asset_prefix: str, armature_obj, action, driver_only_armature: bool = False) -> str:
    if not driver_only_armature and action is not None:
        return f"{clip_asset_prefix}-{ensure_asset_id(action)}"
    return f"{clip_asset_prefix}-{ensure_instance_id(armature_obj)}-drivers"


def _build_clip_name(clip_name: str, suffix: str) -> str:
    base = str(clip_name or "AnimationClip").strip() or "AnimationClip"
    if base.endswith(f"_{suffix}"):
        return base
    return f"{base}_{suffix}"
