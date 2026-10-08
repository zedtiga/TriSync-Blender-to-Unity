from __future__ import annotations

import argparse
import importlib
import json
import platform
import sys
import traceback
from pathlib import Path

import bpy  # type: ignore


def _arguments() -> argparse.Namespace:
    values = sys.argv[sys.argv.index("--") + 1 :] if "--" in sys.argv else []
    parser = argparse.ArgumentParser()
    parser.add_argument("--addon-parent", required=True)
    parser.add_argument("--expected-version", required=True)
    parser.add_argument("--result", required=True)
    return parser.parse_args(values)


def _write_result(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError(message)


def _probe_mesh_api(checks: list[str]) -> tuple[object, object]:
    mesh = bpy.data.meshes.new("__BlenderSyncCompatibilityMesh")
    mesh.from_pydata(
        [(0.0, 0.0, 0.0), (1.0, 0.0, 0.0), (0.0, 1.0, 0.0)],
        [],
        [(0, 1, 2)],
    )
    mesh.update()
    mesh.uv_layers.new(name="UVMap")
    _require(hasattr(mesh, "color_attributes"), "mesh_color_attributes_missing")
    mesh.color_attributes.new(name="Color", type="FLOAT_COLOR", domain="POINT")

    obj = bpy.data.objects.new("__BlenderSyncCompatibilityObject", mesh)
    bpy.context.scene.collection.objects.link(obj)
    checks.append("mesh-api")
    return obj, mesh


def _probe_geometry_nodes_api(obj, checks: list[str]):
    tree = bpy.data.node_groups.new(
        name="__BlenderSyncCompatibilityGeometryNodes",
        type="GeometryNodeTree",
    )
    tree.interface.new_socket(
        name="Geometry",
        in_out="INPUT",
        socket_type="NodeSocketGeometry",
    )
    tree.interface.new_socket(
        name="Geometry",
        in_out="OUTPUT",
        socket_type="NodeSocketGeometry",
    )
    group_input = tree.nodes.new("NodeGroupInput")
    tree.nodes.new("GeometryNodeRealizeInstances")
    set_material = tree.nodes.new("GeometryNodeSetMaterial")
    group_output = tree.nodes.new("NodeGroupOutput")
    material = bpy.data.materials.new("__BlenderSyncCompatibilityMaterial")
    # Mirror a GN-only material assignment on a mesh that still carries an
    # empty legacy slot. The exported snapshot must compact this unused hole.
    obj.data.materials.append(None)
    set_material.inputs["Material"].default_value = material
    tree.links.new(group_input.outputs["Geometry"], set_material.inputs["Geometry"])
    tree.links.new(set_material.outputs["Geometry"], group_output.inputs["Geometry"])
    modifier = obj.modifiers.new("__BlenderSyncCompatibilityGeometryNodes", "NODES")
    modifier.node_group = tree
    bpy.context.view_layer.update()
    checks.append("geometry-nodes-api")
    return tree, material


def _probe_evaluated_mesh_api(obj, checks: list[str]) -> None:
    from blender.common.evaluated_mesh import evaluated_mesh_for_sync

    depsgraph = bpy.context.evaluated_depsgraph_get()
    with evaluated_mesh_for_sync(obj, depsgraph=depsgraph) as lease:
        _require(lease.mesh is not None, "evaluated_mesh_missing")
        _require(len(lease.mesh.vertices) == 3, "evaluated_mesh_vertex_count_mismatch")
    checks.append("evaluated-mesh-api")


def _probe_evaluated_material_api(obj, expected_material, checks: list[str]) -> None:
    from blender.common.evaluated_mesh import evaluated_mesh_for_sync
    from blender.material_resource.slots import collect_material_export_snapshot

    _require(len(obj.material_slots) == 1, "geometry_nodes_empty_slot_missing")
    _require(obj.material_slots[0].material is None, "geometry_nodes_object_slot_not_empty")
    depsgraph = bpy.context.evaluated_depsgraph_get()
    with evaluated_mesh_for_sync(obj, depsgraph=depsgraph) as lease:
        _require(lease.mesh is not None, "evaluated_material_mesh_missing")
        snapshot = collect_material_export_snapshot(
            obj,
            lease.mesh,
            geometry_is_evaluated=True,
        )
    _require(len(snapshot.materials) == 1, "evaluated_material_slot_count_mismatch")
    _require(
        snapshot.materials[0].as_pointer() == expected_material.as_pointer(),
        "evaluated_material_original_identity_mismatch",
    )
    _require(
        snapshot.triangle_material_indices == (0,),
        "evaluated_material_triangle_index_mismatch",
    )
    checks.append("evaluated-material-api")


def _probe_animation_action_api(action, checks: list[str]) -> str:
    from blender.animation_clip.core import _iter_action_fcurves

    if bpy.app.version < (4, 4, 0):
        _require(hasattr(action, "fcurves"), "legacy_action_fcurves_missing")
        fcurve = action.fcurves.new(data_path="location", index=0)
        storage = "legacy"
    else:
        _require(hasattr(action, "slots"), "layered_action_slots_missing")
        _require(hasattr(action, "layers"), "layered_action_layers_missing")
        slot = action.slots.new("OBJECT", "OBBlenderSyncCompatibility")
        layer = action.layers.new("BlenderSync Compatibility")
        strip = layer.strips.new(type="KEYFRAME")
        channelbag = strip.channelbag(slot, ensure=True)
        fcurve = channelbag.fcurves.new(data_path="location", index=0)
        storage = "layered"

    found = _iter_action_fcurves(action)
    _require(len(found) == 1, f"animation_fcurve_count_mismatch:{len(found)}")
    _require(
        found[0].as_pointer() == fcurve.as_pointer(),
        "animation_fcurve_identity_mismatch",
    )
    checks.append("animation-action-api")
    return storage


def _probe_bone_pose_numeric_stability(checks: list[str]):
    from blender.animation_clip.core import sample_pose_bone_transform_unity_rig_v1

    armature_data = bpy.data.armatures.new("__BlenderSyncCompatibilityPoseArmature")
    armature = bpy.data.objects.new("__BlenderSyncCompatibilityPoseObject", armature_data)
    bpy.context.scene.collection.objects.link(armature)
    bpy.context.view_layer.objects.active = armature
    armature.select_set(True)
    bpy.ops.object.mode_set(mode="EDIT")
    root = armature_data.edit_bones.new("root")
    root.head = (0.0, 0.0, 0.0)
    root.tail = (0.0, 1.0, 0.0)
    child = armature_data.edit_bones.new("child")
    child.head = (0.0, 1.0, 0.0)
    child.tail = (0.0, 2.0, 0.0)
    child.parent = root
    child.use_connect = True
    bpy.ops.object.mode_set(mode="POSE")
    armature.pose.bones["root"].rotation_mode = "XYZ"
    armature.pose.bones["root"].rotation_euler[2] = 0.3
    bpy.context.view_layer.update()

    depsgraph = bpy.context.evaluated_depsgraph_get()
    evaluated = armature.evaluated_get(depsgraph)
    pose_bone = evaluated.pose.bones["child"]
    position, _rotation, scale = sample_pose_bone_transform_unity_rig_v1(pose_bone)
    _require(position == (0.0, 0.0, 1.0), f"bone_pose_rest_position_not_canonical:{position}")
    _require(scale == (1.0, 1.0, 1.0), f"bone_pose_identity_scale_not_canonical:{scale}")

    armature.pose.bones["root"].location.x = 0.001
    armature.pose.bones["child"].scale.x = 1.1
    bpy.context.view_layer.update()
    evaluated = armature.evaluated_get(depsgraph)
    root_position, _root_rotation, _root_scale = sample_pose_bone_transform_unity_rig_v1(
        evaluated.pose.bones["root"]
    )
    _child_position, _child_rotation, child_scale = sample_pose_bone_transform_unity_rig_v1(
        evaluated.pose.bones["child"]
    )
    _require(abs(root_position[0] - 0.001) <= 1e-7, f"bone_pose_position_lost:{root_position}")
    _require(abs(child_scale[0] - 1.1) <= 1e-6, f"bone_pose_scale_lost:{child_scale}")
    checks.append("bone-pose-numeric-stability")
    return armature, armature_data


def _probe_rig_shape_key_animation_api(armature, checks: list[str]):
    from blender.animation_clip.core import (
        _collect_shape_key_animation_sources,
        _sample_active_armature_tracks_unity_rig_v1,
    )

    mesh = bpy.data.meshes.new("__BlenderSyncCompatibilityShapeMesh")
    mesh.from_pydata(
        [(0.0, 0.0, 0.0), (1.0, 0.0, 0.0), (0.0, 1.0, 0.0)],
        [],
        [(0, 1, 2)],
    )
    mesh.update()
    mesh_obj = bpy.data.objects.new("__BlenderSyncCompatibilityShapeObject", mesh)
    bpy.context.scene.collection.objects.link(mesh_obj)
    modifier = mesh_obj.modifiers.new("__BlenderSyncCompatibilityArmature", "ARMATURE")
    modifier.object = armature
    mesh_obj.shape_key_add(name="Basis")
    smile = mesh_obj.shape_key_add(name="Smile")
    smile.value = 0.0
    smile.keyframe_insert(data_path="value", frame=1.0)
    smile.value = 1.0
    smile.keyframe_insert(data_path="value", frame=2.0)
    bpy.context.view_layer.update()

    sources = _collect_shape_key_animation_sources(armature)
    matching = [source for source in sources if source.get("object") == mesh_obj]
    _require(len(matching) == 1, f"rig_shape_source_count_mismatch:{len(matching)}")
    source = matching[0]
    _require(source.get("action") is not None, "rig_shape_action_missing")
    _require(set(source.get("channels") or {}) == {"Smile"}, "rig_shape_channels_mismatch")

    tracks = _sample_active_armature_tracks_unity_rig_v1(
        bpy.context,
        armature,
        {"object": {}, "bones": {}},
        1,
        [(1.0, 0.0), (2.0, 1.0 / 30.0)],
        "linear",
        shape_key_sources=[source],
    )
    shape_tracks = [
        track
        for track in tracks
        if track.get("targetType") == "blend_shape"
        and track.get("component") == "Smile"
    ]
    _require(len(shape_tracks) == 1, f"rig_shape_track_count_mismatch:{len(shape_tracks)}")
    track = shape_tracks[0]
    _require(track.get("path") == mesh_obj.name, "rig_shape_binding_path_mismatch")
    values = [float(key.get("value", 0.0)) for key in track.get("keys") or []]
    _require(len(values) == 2, f"rig_shape_key_count_mismatch:{len(values)}")
    _require(abs(values[0]) <= 1e-5, f"rig_shape_first_value_mismatch:{values[0]}")
    _require(abs(values[1] - 100.0) <= 1e-4, f"rig_shape_last_value_mismatch:{values[1]}")
    checks.append("rig-shape-key-animation-api")

    from blender.rigged_object.builder import build_unity_rig_v1_blendshape_weights_payload

    bpy.context.scene.frame_set(2)
    bpy.context.view_layer.update()
    weight_payload = build_unity_rig_v1_blendshape_weights_payload(bpy.context, armature)
    _require(weight_payload is not None, "rig_blendshape_weight_payload_missing")
    _require(
        weight_payload.get("type") == "asset_bridge.rigged_blendshape_weights_v1",
        "rig_blendshape_weight_payload_type_mismatch",
    )
    weight_parts = weight_payload.get("parts") or []
    _require(len(weight_parts) == 1, f"rig_blendshape_weight_part_count_mismatch:{len(weight_parts)}")
    _require(weight_parts[0].get("objectName") == mesh_obj.name, "rig_blendshape_weight_object_mismatch")
    weight_items = weight_parts[0].get("weights") or []
    _require(len(weight_items) == 1, f"rig_blendshape_weight_count_mismatch:{len(weight_items)}")
    _require(weight_items[0].get("name") == "Smile", "rig_blendshape_weight_name_mismatch")
    _require(abs(float(weight_items[0].get("weight", 0.0)) - 100.0) <= 1e-4, "rig_blendshape_weight_value_mismatch")
    checks.append("rigged-blendshape-weight-api")
    return mesh_obj, mesh, source.get("action")


def _probe_ui_icon_api(checks: list[str]) -> None:
    from blender.ui.session_panel import (
        _connection_ui_state,
        _session_diagnostics_presentation,
    )

    icon_parameter = bpy.types.UILayout.bl_rna.functions["label"].parameters["icon"]
    available_icons = {item.identifier for item in icon_parameter.enum_items}
    status_icons = {
        _connection_ui_state({"connected": True})["status_icon"],
        _connection_ui_state({"transport_connected": True})["status_icon"],
        _connection_ui_state({})["status_icon"],
        _session_diagnostics_presentation({"connected": True})["status_icon"],
        _session_diagnostics_presentation({})["status_icon"],
    }
    missing_icons = sorted(status_icons - available_icons)
    _require(not missing_icons, f"ui_status_icons_missing:{','.join(missing_icons)}")
    checks.append("ui-icon-api")


def _probe_localization_facade(checks: list[str]) -> None:
    from blender.common import localization
    from blender.translations import blender_translation_dictionary

    catalog = blender_translation_dictionary()["zh_HANS"]
    view = bpy.context.preferences.view
    original_language = view.language
    original_translate_interface = view.use_translate_interface
    try:
        view.use_translate_interface = True
        view.language = "zh_HANS"
        _require(
            localization.iface("Session") == catalog[("*", "Session")],
            "localization_iface_translation_mismatch",
        )
        _require(
            localization.format_iface("Port {port}", port=8765)
            == catalog[("*", "Port {port}")].format(port=8765),
            "localization_formatted_translation_mismatch",
        )
        _require(
            localization.report("Connect Session", "Operator")
            == catalog[("Operator", "Connect Session")],
            "localization_report_translation_mismatch",
        )
    finally:
        view.language = original_language
        view.use_translate_interface = original_translate_interface
    checks.append("localization-facade")


def _probe_logging_facade(checks: list[str]) -> None:
    from blender.common import log as sync_log

    sync_log.clear()
    _require(sync_log.info("Compatibility", "info_probe", "buffered"), "logging_info_probe_failed")
    _require(
        sync_log.trace("Compatibility", "trace_probe", lambda: "verbose"),
        "logging_trace_probe_failed",
    )
    entries = sync_log.get_recent_entries()
    _require(
        [entry.get("level") for entry in entries] == ["INFO", "TRACE"],
        "logging_facade_levels_mismatch",
    )
    sync_log.clear()
    checks.append("logging-facade")


def _native_summary() -> dict:
    from blender.native.mesh_extractor import get_native_status

    status = get_native_status()
    return {
        "available": bool(status.get("available")),
        "cacheTag": str(status.get("cacheTag") or "unknown"),
        "platformTag": str(status.get("platformTag") or "unknown"),
        "importError": str(status.get("importError") or ""),
        "searchDirs": [str(path) for path in status.get("searchDirs") or []],
        "version": str(status.get("version") or ""),
        "capabilities": [str(value) for value in status.get("capabilities") or []],
    }


def main() -> int:
    args = _arguments()
    result_path = Path(args.result)
    result = {
        "status": "failed",
        "expectedVersion": args.expected_version,
        "blenderVersion": ".".join(str(part) for part in bpy.app.version),
        "pythonVersion": platform.python_version(),
        "animationActionStorage": "",
        "checks": [],
        "native": {},
    }
    addon = None
    registered = False
    obj = None
    mesh = None
    tree = None
    material = None
    action = None
    pose_armature = None
    pose_armature_data = None
    shape_mesh_obj = None
    shape_mesh = None
    shape_action = None
    sync_log = None

    try:
        actual_major_minor = f"{bpy.app.version[0]}.{bpy.app.version[1]}"
        _require(
            actual_major_minor == args.expected_version,
            f"blender_version_mismatch:expected={args.expected_version}:actual={actual_major_minor}",
        )

        addon_parent = str(Path(args.addon_parent).resolve())
        if addon_parent not in sys.path:
            sys.path.insert(0, addon_parent)
        addon = importlib.import_module("blendersync_vnext")
        from blender.common import log as sync_log_module

        sync_log = sync_log_module
        sync_log.set_verbose_override(True)
        addon.register()
        registered = True
        _require(
            hasattr(bpy.types.Scene, "blendersync_sync_enabled"),
            "addon_scene_property_not_registered",
        )
        from blender.scene_sync import settings as addon_settings
        from blender.ui import operators as addon_operators

        preferences_type = addon_settings.BlenderSyncAddonPreferences
        performance_reset_operator_type = addon_operators.BS_OT_ResetPerformanceTuning
        _require(
            bool(getattr(preferences_type, "is_registered", False)),
            "addon_preferences_not_registered",
        )
        port_property = preferences_type.bl_rna.properties["session_port"]
        _require(
            int(port_property.default) == 8765,
            "addon_preferences_default_port_mismatch",
        )
        verbose_property = preferences_type.bl_rna.properties["verbose_logging"]
        _require(
            bool(verbose_property.default) is False,
            "addon_preferences_default_verbose_logging_mismatch",
        )
        tuning_defaults = {
            "object_state_sync_hz": 30.0,
            "mesh_update_sync_hz": 30.0,
            "evaluated_mesh_preview_debounce_ms": 250,
            "preview_idle_commit_seconds": 10.0,
            "view_sync_hz": 10.0,
            "view_sync_scale": 0.8,
            "lifecycle_reconcile_hz": 1.5,
        }
        for property_name, expected_default in tuning_defaults.items():
            property_definition = preferences_type.bl_rna.properties[property_name]
            _require(
                abs(float(property_definition.default) - float(expected_default)) <= 1e-6,
                f"addon_preferences_default_mismatch:{property_name}",
            )
            _require(
                not hasattr(bpy.types.Scene, f"blendersync_{property_name}"),
                f"performance_tuning_scene_property_still_registered:{property_name}",
            )
        _require(
            not hasattr(bpy.types.Scene, "blendersync_auto_rebuild_evaluated_mesh"),
            "legacy_evaluated_mesh_watch_still_registered",
        )
        _require(
            not hasattr(bpy.types.Scene, "blendersync_auto_sync_trigger_monitor"),
            "trigger_monitor_still_registered",
        )
        _require(
            not hasattr(bpy.types.Scene, "blendersync_auto_sync_trigger_monitor_path"),
            "trigger_monitor_path_still_registered",
        )
        _require(
            hasattr(bpy.types, "BS_PT_session_panel"),
            "addon_session_panel_not_registered",
        )
        _require(
            bool(getattr(performance_reset_operator_type, "is_registered", False)),
            "performance_reset_operator_not_registered",
        )
        result["checks"].append("addon-register")

        obj, mesh = _probe_mesh_api(result["checks"])
        tree, material = _probe_geometry_nodes_api(obj, result["checks"])
        _probe_evaluated_mesh_api(obj, result["checks"])
        _probe_evaluated_material_api(obj, material, result["checks"])
        action = bpy.data.actions.new("__BlenderSyncCompatibilityAction")
        result["animationActionStorage"] = _probe_animation_action_api(
            action,
            result["checks"],
        )
        pose_armature, pose_armature_data = _probe_bone_pose_numeric_stability(result["checks"])
        shape_mesh_obj, shape_mesh, shape_action = _probe_rig_shape_key_animation_api(
            pose_armature,
            result["checks"],
        )
        _probe_ui_icon_api(result["checks"])
        _probe_localization_facade(result["checks"])
        _probe_logging_facade(result["checks"])
        result["native"] = _native_summary()
        result["checks"].append("native-probe")

        addon.unregister()
        registered = False
        _require(
            not hasattr(bpy.types.Scene, "blendersync_sync_enabled"),
            "addon_scene_property_not_unregistered",
        )
        _require(
            not bool(getattr(preferences_type, "is_registered", False)),
            "addon_preferences_not_unregistered",
        )
        _require(
            not hasattr(bpy.types, "BS_PT_session_panel"),
            "addon_session_panel_not_unregistered",
        )
        _require(
            not bool(getattr(performance_reset_operator_type, "is_registered", False)),
            "performance_reset_operator_not_unregistered",
        )
        result["checks"].append("addon-unregister")
        result["status"] = "passed"
        return 0
    except Exception as exc:
        result["error"] = f"{type(exc).__name__}: {exc}"
        result["traceback"] = traceback.format_exc()
        return 1
    finally:
        if registered and addon is not None:
            try:
                addon.unregister()
            except Exception as exc:
                result.setdefault("cleanupErrors", []).append(f"addon_unregister:{exc}")
                result["status"] = "failed"
        if obj is not None:
            try:
                bpy.data.objects.remove(obj, do_unlink=True)
            except Exception as exc:
                result.setdefault("cleanupErrors", []).append(f"object_remove:{exc}")
                result["status"] = "failed"
        if mesh is not None:
            try:
                bpy.data.meshes.remove(mesh)
            except Exception as exc:
                result.setdefault("cleanupErrors", []).append(f"mesh_remove:{exc}")
                result["status"] = "failed"
        if tree is not None:
            try:
                bpy.data.node_groups.remove(tree)
            except Exception as exc:
                result.setdefault("cleanupErrors", []).append(f"node_group_remove:{exc}")
                result["status"] = "failed"
        if material is not None:
            try:
                bpy.data.materials.remove(material)
            except Exception as exc:
                result.setdefault("cleanupErrors", []).append(f"material_remove:{exc}")
                result["status"] = "failed"
        if action is not None:
            try:
                bpy.data.actions.remove(action)
            except Exception as exc:
                result.setdefault("cleanupErrors", []).append(f"action_remove:{exc}")
                result["status"] = "failed"
        if pose_armature is not None:
            try:
                bpy.data.objects.remove(pose_armature, do_unlink=True)
            except Exception as exc:
                result.setdefault("cleanupErrors", []).append(f"pose_armature_remove:{exc}")
                result["status"] = "failed"
        if pose_armature_data is not None:
            try:
                bpy.data.armatures.remove(pose_armature_data)
            except Exception as exc:
                result.setdefault("cleanupErrors", []).append(f"pose_armature_data_remove:{exc}")
                result["status"] = "failed"
        if shape_mesh_obj is not None:
            try:
                bpy.data.objects.remove(shape_mesh_obj, do_unlink=True)
            except Exception as exc:
                result.setdefault("cleanupErrors", []).append(f"shape_mesh_object_remove:{exc}")
                result["status"] = "failed"
        if shape_mesh is not None:
            try:
                bpy.data.meshes.remove(shape_mesh)
            except Exception as exc:
                result.setdefault("cleanupErrors", []).append(f"shape_mesh_remove:{exc}")
                result["status"] = "failed"
        if shape_action is not None:
            try:
                bpy.data.actions.remove(shape_action)
            except Exception as exc:
                result.setdefault("cleanupErrors", []).append(f"shape_action_remove:{exc}")
                result["status"] = "failed"
        if sync_log is not None:
            sync_log.clear()
            sync_log.set_verbose_override(None)
        _write_result(result_path, result)


if __name__ == "__main__":
    raise SystemExit(main())
