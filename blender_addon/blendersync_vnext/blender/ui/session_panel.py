from __future__ import annotations

import time

from blender.common.localization import format_iface, iface
from blender.common.log import get_recent_entries, latest_sequence
from blender.material_resource.extractor import find_principled
from blender.scene_sync.settings import get_addon_preferences, get_session_port
from blender.ui.state_view import (
    get_recent_operation_reports,
    get_session_view_state,
    latest_operation_sequence,
)
from blender.ui.bulk_job import get_state as get_bulk_job_state

try:
    from blender.scene_sync.controller import get_controller_state
except Exception:  # keep UI importable during partial reloads/non-Blender tests
    get_controller_state = None

try:
    import bpy  # type: ignore
except ImportError:  # non-Blender runtime fallback
    bpy = None


if bpy is not None:
    PanelBase = bpy.types.Panel
else:
    class PanelBase:  # type: ignore[no-redef]
        pass

_ui_redraw_timer_registered = False
_last_ui_state_signature = None

_VISIBLE_WORKFLOW_TABS = (
    ("objects", "Obj"),
    ("materials", "Mats"),
    ("rig_pose", "Rig"),
    ("animation", "Anim"),
    ("diagnostics", "Diag"),
)
_VISIBLE_WORKFLOW_TAB_IDS = frozenset(identifier for identifier, _label in _VISIBLE_WORKFLOW_TABS)


def _status_color_icon(number: int, *, blender_version=None) -> str:
    if blender_version is None:
        app = getattr(bpy, "app", None) if bpy is not None else None
        blender_version = getattr(app, "version", (4, 4, 0))
    prefix = "SEQUENCE_COLOR" if tuple(blender_version) < (4, 4, 0) else "STRIP_COLOR"
    return f"{prefix}_{int(number):02d}"


def _session_error(state) -> str:
    state = state or {}
    error = str(state.get("last_error") or state.get("last_ws_handoff_error") or "")
    if error == "handshake_timeout":
        return ""
    if (
        error in {"session_not_connected", "handshake_not_confirmed"}
        and not bool(state.get("connected"))
    ):
        return ""
    return error


def _session_port_editable(state) -> bool:
    state = state or {}
    return not any(
        bool(state.get(key))
        for key in ("connected", "transport_connected", "local_ready", "connect_attempted")
    )


def _connection_ui_state(state, *, blender_version=None) -> dict:
    state = state or {}
    if bool(state.get("connected")):
        return {
            "label": "Unity",
            "status_icon": _status_color_icon(4, blender_version=blender_version),
            "operator": "blendersync.session_disconnect",
            "action": "Disconnect",
            "action_icon": "UNLINKED",
        }
    if bool(state.get("transport_connected")):
        return {
            "label": "Handshaking",
            "status_icon": _status_color_icon(3, blender_version=blender_version),
            "operator": "blendersync.session_disconnect",
            "action": "Disconnect",
            "action_icon": "UNLINKED",
        }
    if bool(state.get("local_ready")) or bool(state.get("connect_attempted")):
        return {
            "label": "Waiting for Unity",
            "status_icon": _status_color_icon(3, blender_version=blender_version),
            "operator": "blendersync.session_disconnect",
            "action": "Disconnect",
            "action_icon": "UNLINKED",
        }
    return {
        "label": "Disconnected",
        "status_icon": _status_color_icon(9, blender_version=blender_version),
        "operator": "blendersync.session_connect",
        "action": "Connect Unity",
        "action_icon": "LINKED",
    }


def _connection_redraw_signature(state) -> tuple:
    state = state or {}
    return (
        bool(state.get("connected")),
        bool(state.get("transport_connected")),
        bool(state.get("local_ready")),
        bool(state.get("connect_attempted")),
        str(state.get("handshake_phase") or ""),
        _session_error(state),
        latest_sequence(),
        latest_operation_sequence(),
    )


def _normalize_ui_tab(value) -> str:
    normalized = str(value or "")
    return normalized if normalized in _VISIBLE_WORKFLOW_TAB_IDS else "objects"


def _tag_blendersync_ui_redraw() -> None:
    if bpy is None:
        return
    try:
        wm = getattr(bpy.context, "window_manager", None) if bpy.context is not None else None
        windows = list(getattr(wm, "windows", []) or []) if wm is not None else []
        screens = []
        for window in windows:
            screen = getattr(window, "screen", None)
            if screen is not None:
                screens.append(screen)
        if not screens and bpy.context is not None:
            screen = getattr(bpy.context, "screen", None)
            if screen is not None:
                screens.append(screen)
        for screen in screens:
            for area in list(getattr(screen, "areas", []) or []):
                if getattr(area, "type", "") in {"VIEW_3D", "IMAGE_EDITOR", "NODE_EDITOR"}:
                    for region in list(getattr(area, "regions", []) or []):
                        if getattr(region, "type", "") == "UI":
                            area.tag_redraw()
                            break
    except Exception:
        pass


def _ui_redraw_tick():
    global _last_ui_state_signature
    if bpy is None or bpy.context is None:
        return 1.0
    interval = 1.5
    try:
        state = get_session_view_state()
        signature = _connection_redraw_signature(state)
        if signature != _last_ui_state_signature:
            _last_ui_state_signature = signature
            _tag_blendersync_ui_redraw()
        if (
            bool(state.get("local_ready"))
            and not bool(state.get("connected"))
        ):
            interval = 0.5

        scene = getattr(bpy.context, "scene", None)
        if scene is not None and bool(getattr(scene, "blendersync_show_debug", False)):
            _tag_blendersync_ui_redraw()
            return 0.5
    except Exception:
        pass
    return interval


def _register_ui_redraw_timer() -> None:
    global _ui_redraw_timer_registered, _last_ui_state_signature
    if bpy is None or _ui_redraw_timer_registered:
        return
    _last_ui_state_signature = None
    try:
        bpy.app.timers.register(_ui_redraw_tick, first_interval=0.5, persistent=True)
        _ui_redraw_timer_registered = True
    except Exception:
        _ui_redraw_timer_registered = False


def _unregister_ui_redraw_timer() -> None:
    global _ui_redraw_timer_registered, _last_ui_state_signature
    if bpy is None or not _ui_redraw_timer_registered:
        return
    try:
        bpy.app.timers.unregister(_ui_redraw_tick)
    except Exception:
        pass
    _ui_redraw_timer_registered = False
    _last_ui_state_signature = None


def _draw_disclosure(box, scene, prop_name: str, label: str, icon_open: str = "TRIA_DOWN", icon_closed: str = "TRIA_RIGHT") -> bool:
    row = box.row(align=True)
    expanded = bool(getattr(scene, prop_name, False))
    row.prop(
        scene,
        prop_name,
        text=iface(label),
        icon=icon_open if expanded else icon_closed,
        emboss=False,
        translate=False,
    )
    return expanded


def _short_text(value, max_len: int = 42) -> str:
    text = str(value or "")
    if len(text) <= max_len:
        return text
    return text[: max(0, max_len - 3)] + "..."


_LAST_OPERATION_LABELS = {
    "import_objects": "Import / Repair",
    "object_state": "Update State",
    "scene_sync_update_selected": "Update",
    "view_state": "Sync View",
    "animation_clip": "Export Animation",
    "material_update": "Sync Material",
    "rigged_pose_sync": "Sync Pose",
    "rigged_blendshape_weights_sync": "Sync Shape Keys",
    "preview_update": "Mesh Preview",
    "preview_commit": "Commit Preview",
    "auto_sync": "Auto Sync",
}


def _last_operation_label(report: dict | None) -> str:
    meta = (report or {}).get("operation_meta") or {}
    intent = str(meta.get("updateIntent") or "")
    trigger = str(meta.get("triggerType") or "")
    return _LAST_OPERATION_LABELS.get(intent) or _LAST_OPERATION_LABELS.get(trigger) or "Operation"


def _operation_activity_details(report: dict | None) -> list[tuple[str, str]]:
    report = report or {}
    meta = report.get("operation_meta") or {}
    details = []
    message = _short_text(report.get("last_send_message") or report.get("last_send_error") or "", 48)
    if message:
        details.append(("Result", message))
    if meta.get("selectedObjectCount") is not None:
        selected = int(meta.get("selectedObjectCount") or 0)
        raw_supported = meta.get("supportedSelectedObjectCount")
        supported = selected if raw_supported is None else int(raw_supported or 0)
        details.append(("Objects", f"{supported} / {selected}"))
    if meta.get("objectStateCount") is not None:
        details.append(("State Objects", str(int(meta.get("objectStateCount") or 0))))
    resources = int(report.get("last_resource_count") or 0)
    if resources:
        details.append(("Resources", str(resources)))
    payload_size = int(report.get("last_payload_size") or 0)
    if payload_size:
        details.append(("Payload", f"{payload_size} bytes"))
    package_id = report.get("last_package_id")
    if package_id:
        details.append(("Package", _short_text(package_id, 44)))
    if report.get("failure_category"):
        details.append(("Failure", str(report.get("failure_category"))))
    return details


_EXPANDED_ACTIVITY_ENTRY_IDS: set[str] = set()


def toggle_recent_activity_entry(entry_id: str) -> bool:
    entry_id = str(entry_id or "").strip()
    if not entry_id:
        return False
    if entry_id in _EXPANDED_ACTIVITY_ENTRY_IDS:
        _EXPANDED_ACTIVITY_ENTRY_IDS.remove(entry_id)
        return False
    _EXPANDED_ACTIVITY_ENTRY_IDS.add(entry_id)
    return True


def clear_recent_activity_expansion() -> None:
    _EXPANDED_ACTIVITY_ENTRY_IDS.clear()


_BULK_OPERATION_LABELS = {
    "import_selected_objects": ("Import / Repair", "Importing objects"),
    "update_selected_active": ("Update", "Updating objects"),
    "update_object_state": ("Update State", "Updating object state"),
}


def _bulk_job_presentation(state: dict | None) -> dict:
    state = state or {}
    status = str(state.get("status") or "idle")
    mode = str(state.get("mode") or "fast_path")
    operation = str(state.get("operation") or "")
    label, progress_label = _BULK_OPERATION_LABELS.get(operation, ("Operation", "Working"))

    if status == "idle" or (mode == "fast_path" and status not in {"error", "cancelled"}):
        return {"visible": False}

    if mode == "fast_path":
        cancelled = status == "cancelled"
        return {
            "visible": True,
            "kind": "result",
            "title": format_iface(
                "{operation} cancelled" if cancelled else "{operation} failed",
                operation=iface(label),
            ),
            "icon": "CANCEL" if cancelled else "ERROR",
            "summary": "",
            "detail": "" if cancelled else iface("The operation could not be completed. See Diag for details."),
            "show_dismiss": True,
        }

    total = max(0, int(state.get("totalObjects") or 0))
    processed = max(0, int(state.get("processedObjects") or 0))
    if status in {"running", "cancel_requested"}:
        return {
            "visible": True,
            "kind": "progress",
            "title": iface("Cancelling...") if status == "cancel_requested" else iface(progress_label),
            "icon": "TIME",
            "progress": min(1.0, processed / total) if total > 0 else 0.0,
            "progress_text": f"{processed} / {total}",
            "current_object": str(state.get("currentObject") or ""),
            "show_cancel": status == "running",
        }

    success = max(0, int(state.get("successCount") or 0))
    errors = max(0, int(state.get("errorCount") or 0))
    skipped = max(0, int(state.get("skippedCount") or 0))
    if status == "done" and success == 0 and errors == 0 and skipped == 0:
        success = processed
    failed = status == "error" or errors > 0
    cancelled = status == "cancelled"
    if cancelled:
        title = format_iface("{operation} cancelled", operation=iface(label))
        icon = "CANCEL"
    elif failed:
        title = format_iface("{operation} failed", operation=iface(label))
        icon = "ERROR"
    else:
        title = format_iface("{operation} complete", operation=iface(label))
        icon = "CHECKMARK"
    return {
        "visible": True,
        "kind": "result",
        "title": title,
        "icon": icon,
        "summary": format_iface(
            "Completed: {completed} | Skipped: {skipped} | Failed: {failed}",
            completed=success,
            skipped=skipped,
            failed=errors,
        ),
        "detail": iface("Some objects failed. See Diag for details.") if failed else "",
        "show_dismiss": True,
    }


def _draw_bulk_job_status(layout) -> None:
    presentation = _bulk_job_presentation(get_bulk_job_state())
    if not presentation.get("visible"):
        return

    box = layout.box()
    header = box.row(align=True)
    header.label(
        text=str(presentation.get("title") or "Operation"),
        icon=str(presentation.get("icon") or "NONE"),
    )
    if presentation.get("show_dismiss"):
        header.operator(
            "blendersync.dismiss_bulk_job",
            text="",
            icon="X",
        )

    if presentation.get("kind") == "progress":
        progress = getattr(box, "progress", None)
        if callable(progress):
            progress(
                factor=float(presentation.get("progress") or 0.0),
                type="BAR",
                text=str(presentation.get("progress_text") or ""),
            )
        else:
            box.label(text=str(presentation.get("progress_text") or ""))
        current_object = str(presentation.get("current_object") or "")
        if current_object:
            box.label(
                text=format_iface("Current: {name}", name=_short_text(current_object, 42)),
                translate=False,
            )
    else:
        summary = str(presentation.get("summary") or "")
        if summary:
            box.label(text=summary)
        detail = str(presentation.get("detail") or "")
        if detail:
            box.label(text=detail)

    if presentation.get("show_cancel"):
        box.operator("blendersync.cancel_bulk_job", text=iface("Cancel"), translate=False)


def _animation_export_report_text(value) -> str:
    report_text = str(value or "").strip()
    if report_text == "No animation clip exported yet.":
        return ""
    return report_text


def _draw_animation_panel(layout, scene) -> None:
    anim_box = layout.box()
    if not _draw_disclosure(anim_box, scene, "blendersync_show_animation_panel", "Clip Export"):
        return

    anim_box.prop(scene, "blendersync_animation_clip_name", text="Clip Name")
    anim_box.prop(scene, "blendersync_animation_clip_source_mode", text="Source")
    anim_box.prop(scene, "blendersync_animation_clip_range_mode", text="Range")
    if scene.blendersync_animation_clip_range_mode == "custom":
        row = anim_box.row(align=True)
        row.prop(scene, "blendersync_animation_clip_start_frame", text="Start")
        row.prop(scene, "blendersync_animation_clip_end_frame", text="End")
    anim_box.prop(scene, "blendersync_animation_clip_sample_mode", text="Sample Mode")
    anim_box.prop(scene, "blendersync_animation_clip_sample_rate_mode", text="FPS")
    if scene.blendersync_animation_clip_sample_rate_mode == "custom":
        anim_box.prop(scene, "blendersync_animation_clip_sample_rate", text="Custom FPS")
    if scene.blendersync_animation_clip_sample_mode == "scene_frames":
        anim_box.prop(scene, "blendersync_animation_clip_sample_step", text="Frame Step")
    anim_box.prop(
        scene,
        "blendersync_animation_clip_loop",
        text="Loop Animation",
    )

    anim_box.separator()
    if _draw_disclosure(anim_box, scene, "blendersync_show_animation_quality", "Quality"):
        anim_box.prop(scene, "blendersync_animation_clip_sampling_strategy", text="Sampling")
        anim_box.prop(scene, "blendersync_animation_clip_interpolation", text="Interpolation")
        anim_box.prop(scene, "blendersync_animation_clip_simplify", text="Simplify")
        anim_box.prop(scene, "blendersync_animation_clip_static_curves", text="Static Curves")

    export_row = anim_box.row(align=True)
    export_row.scale_y = 1.2
    export_row.operator(
        "blendersync.import_animation_clip",
        text="Export to Unity",
        icon="EXPORT",
    )

    report_text = _animation_export_report_text(
        getattr(scene, "blendersync_animation_clip_last_report", "")
    )
    if report_text:
        anim_box.separator()
        if _draw_disclosure(anim_box, scene, "blendersync_show_animation_report", "Last Export"):
            for line in report_text.split("\n")[:8]:
                if line:
                    anim_box.label(text=_short_text(line, 56), translate=False)


def _draw_connection(layout, state, context=None) -> None:
    display = _connection_ui_state(state)
    status_row = layout.row(align=True)
    status_row.scale_y = 1.2
    status_row.label(text=iface(display["label"]), icon=display["status_icon"], translate=False)
    preferences = get_addon_preferences(context)
    if preferences is not None:
        port_row = status_row.row(align=True)
        port_row.enabled = _session_port_editable(state)
        port_row.prop(preferences, "session_port", text="Port")
    else:
        status_row.label(
            text=format_iface("Port {port}", port=get_session_port(context)),
            translate=False,
        )

    action_row = layout.row(align=True)
    action_row.scale_y = 1.2
    action_row.operator(
        display["operator"],
        text=iface(display["action"]),
        icon=display["action_icon"],
        translate=False,
    )

    last_error = _session_error(state)
    if last_error:
        error_row = layout.row()
        error_row.alert = True
        error_row.label(
            text=format_iface("Last error: {error}", error=_short_text(last_error, 48)),
            icon="ERROR",
            translate=False,
        )


def _draw_primary_controls(layout, scene) -> None:
    view_row = layout.row(align=True)
    view_row.scale_y = 1.2
    view_row.prop(
        scene,
        "blendersync_view_sync_enabled",
        text="Auto View",
        icon="PAUSE" if bool(scene.blendersync_view_sync_enabled) else "PLAY",
        toggle=True,
    )
    view_row.operator(
        "blendersync.sync_scene_view_to_unity",
        text="Sync View",
        icon="VIEW_CAMERA",
    )

    auto_sync_row = layout.row(align=True)
    auto_sync_row.scale_y = 1.2
    auto_sync_row.prop(
        scene,
        "blendersync_sync_enabled",
        text="Auto Sync",
        icon="PAUSE" if bool(scene.blendersync_sync_enabled) else "PLAY",
        toggle=True,
    )

    import_row = layout.row(align=True)
    import_row.scale_y = 1.2
    import_row.operator(
        "blendersync.send_selected_resources",
        text="Import / Repair",
        icon="IMPORT",
    )

    update_row = layout.row(align=True)
    update_row.scale_y = 1.2
    update_row.operator(
        "blendersync.sync_selected_objects",
        text="Update",
        icon="FILE_REFRESH",
    )

    commit_row = layout.row(align=True)
    commit_row.scale_y = 1.2
    commit_row.operator(
        "blendersync.manual_commit_selected_previews",
        text="Commit Preview",
        icon="CHECKMARK",
    )
    _draw_bulk_job_status(layout)


def _draw_tab_bar(layout, scene) -> str:
    current = str(getattr(scene, "blendersync_ui_tab", "objects") or "objects")
    tab = _normalize_ui_tab(current)
    if current != tab:
        try:
            scene.blendersync_ui_tab = tab
        except Exception:
            pass
    row = layout.row(align=True)
    for identifier, label in _VISIBLE_WORKFLOW_TABS:
        row.prop_enum(
            scene,
            "blendersync_ui_tab",
            identifier,
            text=label,
        )
    return tab


def _draw_objects_tab(layout, scene) -> None:
    main_box = layout.box()
    update_row = main_box.row(align=True)
    update_row.scale_y = 1.2
    update_row.operator(
        "blendersync.sync_selected_object_state",
        text="Update State",
        icon="FILE_REFRESH",
    )
    reuse_row = main_box.row(align=True)
    reuse_row.scale_y = 1.2
    reuse_row.operator(
        "blendersync.link_selected_to_active_mesh",
        text="Reuse Active Mesh Asset",
        icon="LINKED",
    )

    options_box = layout.box()
    if _draw_disclosure(options_box, scene, "blendersync_show_object_advanced", "Object Options"):
        options_box.label(text="Rig Import")
        options_box.prop(scene, "blendersync_rig_axis_mode", text="", translate=False)
        if scene.blendersync_rig_axis_mode == "preserve_rest_bone_axes":
            axis_row = options_box.row(align=True)
            axis_row.prop(scene, "blendersync_rig_primary_bone_axis", text="Primary")
            axis_row.prop(scene, "blendersync_rig_secondary_bone_axis", text="Secondary")
        options_box.prop(
            scene,
            "blendersync_rig_export_leaf_bones",
            text="Export Leaf Bones",
        )


def _draw_materials_tab(layout, context) -> None:
    material = _resolve_active_material_for_panel(context)
    box = layout.box()
    name_row = box.row()
    if material is None:
        name_row.active = False
        name_row.label(text="No Active Material", icon="MATERIAL")
    else:
        material_name = str(getattr(material, "name", "") or "<unnamed>")
        name_row.label(text=_short_text(material_name, 42), icon="MATERIAL", translate=False)
        summary = _material_panel_summary(material)
        details_row = box.row(align=True)
        details_row.active = False
        details_row.label(text=iface(summary["shader"]), translate=False)
        texture_count = summary["texture_count"]
        texture_label = iface("texture" if texture_count == 1 else "textures")
        details_row.label(text=f"{texture_count} {texture_label}", translate=False)

    send_row = box.row(align=True)
    send_row.scale_y = 1.2
    send_row.operator(
        "blendersync.send_active_material_content_v1",
        text="Sync to Unity",
        icon="EXPORT",
    )


def _draw_rig_pose_tab(layout, context) -> None:
    armature = _resolve_active_rig_for_panel(context)
    pose_box = layout.box()
    name_row = pose_box.row()
    if armature is None:
        name_row.active = False
        name_row.label(text="No Active Armature", icon="ARMATURE_DATA")
    else:
        armature_name = str(getattr(armature, "name", "") or "<unnamed>")
        name_row.label(text=_short_text(armature_name, 42), icon="ARMATURE_DATA", translate=False)

    sync_row = pose_box.row(align=True)
    sync_row.scale_y = 1.2
    sync_row.operator(
        "blendersync.sync_current_rigged_pose",
        text="Sync Pose to Unity",
        icon="EXPORT",
    )

    shape_row = pose_box.row(align=True)
    shape_row.operator(
        "blendersync.sync_rigged_blendshape_weights",
        text="Sync Shape Keys to Unity",
        icon="SHAPEKEY_DATA",
    )

    restore_row = pose_box.row(align=True)
    restore_row.operator(
        "blendersync.restore_static_rigged_pose",
        text="Restore Imported Pose",
        icon="LOOP_BACK",
    )


def _session_diagnostics_presentation(state: dict | None, *, blender_version=None) -> dict:
    state = state or {}
    connected = bool(state.get("connected"))
    transport_connected = bool(state.get("transport_connected"))
    if connected:
        status_label = "Connected"
        status_icon = _status_color_icon(4, blender_version=blender_version)
        peer_version = state.get("peer_protocol_version")
        protocol_label = "Legacy" if state.get("legacy_protocol") else f"v{peer_version or '?'}"
    elif transport_connected:
        status_label = "Handshaking"
        status_icon = _status_color_icon(3, blender_version=blender_version)
        protocol_label = "Negotiating"
    elif bool(state.get("local_ready")) or bool(state.get("connect_attempted")):
        status_label = "Waiting for Unity"
        status_icon = _status_color_icon(3, blender_version=blender_version)
        protocol_label = "-"
    else:
        status_label = "Disconnected"
        status_icon = _status_color_icon(9, blender_version=blender_version)
        protocol_label = "-"
    return {
        "status": status_label,
        "status_icon": status_icon,
        "protocol": protocol_label,
        "handshake": str(state.get("handshake_phase") or "")
        if (transport_connected or bool(state.get("local_ready"))) and not connected
        else "",
        "error": _session_error(state),
    }


def _draw_session_diagnostics(layout, state) -> None:
    presentation = _session_diagnostics_presentation(state)
    session_box = layout.box()
    session_box.label(text="Session")
    session_box.label(
        text=iface(presentation["status"]),
        icon=presentation["status_icon"],
        translate=False,
    )
    session_box.label(
        text=format_iface("Protocol: {protocol}", protocol=iface(presentation["protocol"])),
        translate=False,
    )
    if presentation["handshake"]:
        session_box.label(
            text=format_iface("Handshake: {phase}", phase=presentation["handshake"]),
            translate=False,
        )
    if presentation["error"]:
        session_box.label(
            text=format_iface("Last Error: {error}", error=_short_text(presentation["error"], 48)),
            icon="ERROR",
            translate=False,
        )


def _draw_sync_tuning(layout, scene, context=None) -> None:
    tuning_box = layout.box()
    if not _draw_disclosure(tuning_box, scene, "blendersync_show_auto_sync_settings", "Performance"):
        return

    preferences = get_addon_preferences(context)
    if preferences is None:
        tuning_box.label(text="Preferences unavailable", icon="ERROR")
        return
    tuning_box.prop(preferences, "object_state_sync_hz", text="Object Hz")
    tuning_box.prop(preferences, "mesh_update_sync_hz", text="Edit Preview Max Hz")
    tuning_box.prop(preferences, "evaluated_mesh_preview_debounce_ms", text="Preview Send Delay ms")
    tuning_box.prop(preferences, "preview_idle_commit_seconds", text="Idle Commit s")
    tuning_box.prop(preferences, "lifecycle_reconcile_hz", text="Reconcile Hz")
    tuning_box.prop(preferences, "view_sync_hz", text="View Hz")
    tuning_box.prop(preferences, "view_sync_scale", text="View Scale")
    reset_row = tuning_box.row(align=True)
    reset_row.operator(
        "blendersync.reset_performance_tuning",
        text="Reset to Defaults",
        icon="LOOP_BACK",
    )


def _active_object_diagnostics_presentation(active_baseline: dict | None) -> dict:
    active_baseline = active_baseline or {}
    if not active_baseline.get("hasActiveObject"):
        return {"has_object": False}

    object_type = str(active_baseline.get("objectType") or "")
    ready = bool(active_baseline.get("autoSyncReady"))
    fields = (
        ("Modifier", str(active_baseline.get("modifierBaseline") or "n/a")),
        ("Material Slots", str(active_baseline.get("materialSlotsBaseline") or "n/a")),
        ("UV Channels", str(active_baseline.get("uvChannelsBaseline") or "n/a")),
        ("Color Attributes", str(active_baseline.get("colorAttributesBaseline") or "n/a")),
    )
    issues = [(label, value) for label, value in fields if value != "clean"] if object_type == "MESH" else []
    if object_type != "MESH":
        baseline_summary = "Not applicable"
    elif not ready and all(value in {"missing", "n/a"} for _label, value in fields):
        baseline_summary = "Not captured"
        issues = []
    elif not issues:
        baseline_summary = "Ready"
    else:
        baseline_summary = f"{len(fields) - len(issues)} / {len(fields)} ready"
    source = str(active_baseline.get("meshSource") or "n/a")
    source_label = "N/A" if source == "n/a" else source.replace("_", " ").title()
    return {
        "has_object": True,
        "name": str(active_baseline.get("objectName") or "<unnamed>"),
        "type": object_type,
        "ready": ready,
        "mesh_source": source_label,
        "baseline_summary": baseline_summary,
        "baseline_issues": issues,
        "uv_count": int(active_baseline.get("uvChannelCount") or 0),
        "uv_names": list(active_baseline.get("uvChannelNames") or []),
        "color_count": int(active_baseline.get("colorAttributeCount") or 0),
        "color_names": list(active_baseline.get("colorAttributeNames") or []),
        "color_export": str(active_baseline.get("colorAttributeExportName") or "None"),
    }


def _draw_active_object_diagnostics(layout, scene, controller_state) -> None:
    box = layout.box()
    if not _draw_disclosure(
        box,
        scene,
        "blendersync_show_diagnostics_active_object",
        "Active Object",
    ):
        return
    presentation = _active_object_diagnostics_presentation(
        (controller_state or {}).get("active_object_baseline") or {}
    )
    if not presentation.get("has_object"):
        box.label(text="No Active Object", icon="OBJECT_DATA")
        return

    box.label(
        text=f"{_short_text(presentation['name'], 34)} ({presentation['type']})",
        icon="OBJECT_DATA",
        translate=False,
    )
    box.label(
        text=iface("Imported" if presentation["ready"] else "Not Imported"),
        icon="CHECKMARK" if presentation["ready"] else "INFO",
        translate=False,
    )
    if presentation["type"] == "MESH":
        box.label(
            text=format_iface("Mesh Source: {source}", source=iface(presentation["mesh_source"])),
            translate=False,
        )
        box.label(
            text=format_iface("Baselines: {summary}", summary=iface(presentation["baseline_summary"])),
            translate=False,
        )
        for label, value in presentation["baseline_issues"]:
            box.label(text=f"{iface(label)}: {iface(value)}", translate=False)
        uv_names = ", ".join(presentation["uv_names"])
        box.label(
            text=format_iface("UV Channels: {count}", count=presentation["uv_count"])
            + (f" [{_short_text(uv_names, 30)}]" if uv_names else ""),
            translate=False,
        )
        color_names = ", ".join(presentation["color_names"])
        box.label(
            text=format_iface("Color Attributes: {count}", count=presentation["color_count"])
            + (f" [{_short_text(color_names, 26)}]" if color_names else ""),
            translate=False,
        )
        if presentation["color_count"]:
            box.label(
                text=format_iface("COLOR0 Export: {status}", status=presentation["color_export"]),
                translate=False,
            )


def _draw_runtime_diagnostics(layout, scene, controller_state) -> None:
    box = layout.box()
    if not _draw_disclosure(
        box,
        scene,
        "blendersync_show_diagnostics_runtime",
        "Runtime",
    ):
        return
    controller_state = controller_state or {}
    timer_running = bool(controller_state.get("timer_registered"))
    box.label(
        text=iface("Controller Running" if timer_running else "Controller Stopped"),
        icon="CHECKMARK" if timer_running else "ERROR",
        translate=False,
    )
    box.label(
        text=format_iface("Dirty Queue: {count}", count=int(controller_state.get("object_dirty_queue_count") or 0)),
        translate=False,
    )
    box.label(
        text=format_iface("Motion Burst Pairs: {count}", count=int(controller_state.get("object_motion_burst_pair_count") or 0)),
        translate=False,
    )
    box.label(
        text=format_iface("Fallback Tracked: {count}", count=int(controller_state.get("object_active_fallback_tracked_count") or 0)),
        translate=False,
    )


def _activity_entry_presentation(entry: dict | None) -> dict:
    entry = entry or {}
    level = str(entry.get("level") or "INFO").upper()
    try:
        timestamp = float(entry.get("timestampUnix") or 0.0)
        if timestamp <= 0.0:
            raise ValueError("timestamp_missing")
        local_time = time.strftime("%H:%M:%S", time.localtime(timestamp))
    except (TypeError, ValueError, OverflowError):
        local_time = "--:--:--"
    return {
        "header": (
            f"{local_time}  [{level}] "
            f"{str(entry.get('category') or 'General')} / {str(entry.get('event') or 'event')}"
        ),
        "summary": _short_text(entry.get("summary") or "", 56),
        "icon": "ERROR" if level == "ERROR" else "INFO",
    }


def _operation_activity_presentation(report: dict | None) -> dict:
    report = report or {}
    ok = bool(report.get("last_send_ok"))
    try:
        timestamp = float(report.get("operation_timestamp_unix") or 0.0)
        if timestamp <= 0.0:
            raise ValueError("timestamp_missing")
        local_time = time.strftime("%H:%M:%S", time.localtime(timestamp))
    except (TypeError, ValueError, OverflowError):
        local_time = "--:--:--"
    return {
        "header": f"{local_time}  [{'OK' if ok else 'FAILED'}] {_last_operation_label(report)}",
        "summary": _short_text(
            report.get("last_send_message") or report.get("last_send_error") or "",
            56,
        ),
        "icon": "CHECKMARK" if ok else "ERROR",
    }


def _recent_activity_entries(limit: int = 5) -> list[dict]:
    entries = []
    for entry in get_recent_entries():
        entries.append(
            {
                "entry_id": f"log:{int(entry.get('sequence') or 0)}",
                "kind": "log",
                "timestamp": float(entry.get("timestampUnix") or 0.0),
                "value": entry,
            }
        )
    for report in get_recent_operation_reports():
        entries.append(
            {
                "entry_id": f"operation:{int(report.get('operation_sequence') or 0)}",
                "kind": "operation",
                "timestamp": float(report.get("operation_timestamp_unix") or 0.0),
                "value": report,
            }
        )
    entries.sort(key=lambda entry: (entry["timestamp"], entry["entry_id"]))
    count = max(0, int(limit))
    return entries[-count:] if count else []


def _activity_entry_details(item: dict) -> list[tuple[str, str]]:
    value = item.get("value") or {}
    if item.get("kind") == "operation":
        return _operation_activity_details(value)

    details = []
    if value.get("summary"):
        details.append(("Summary", str(value.get("summary"))))
    details.extend(
        (str(key), str(field_value))
        for key, field_value in (value.get("fields") or {}).items()
    )
    return details


def _draw_activity_entry(box, item: dict) -> None:
    entry_id = str(item.get("entry_id") or "")
    expanded = entry_id in _EXPANDED_ACTIVITY_ENTRY_IDS
    value = item.get("value") or {}
    presentation = (
        _operation_activity_presentation(value)
        if item.get("kind") == "operation"
        else _activity_entry_presentation(value)
    )
    row = box.row(align=True)
    toggle = row.operator(
        "blendersync.toggle_recent_activity_entry",
        text=_short_text(presentation["header"], 62),
        icon="TRIA_DOWN" if expanded else "TRIA_RIGHT",
        emboss=False,
        translate=False,
    )
    toggle.entry_id = entry_id
    if not expanded and presentation["summary"]:
        detail = box.row()
        detail.active = False
        detail.label(text=presentation["summary"], translate=False)
        return
    if not expanded:
        return
    for label, detail_value in _activity_entry_details(item):
        detail = box.row()
        detail.active = False
        detail.label(
            text=f"{_short_text(label, 20)}: {_short_text(detail_value, 44)}",
            translate=False,
        )


def _draw_recent_activity(layout, scene) -> None:
    entries = _recent_activity_entries(5)
    _EXPANDED_ACTIVITY_ENTRY_IDS.intersection_update(
        str(entry.get("entry_id") or "") for entry in entries
    )
    box = layout.box()
    header = box.row(align=True)
    expanded = bool(getattr(scene, "blendersync_show_diagnostics_recent_activity", False))
    header.prop(
        scene,
        "blendersync_show_diagnostics_recent_activity",
        text=iface("Recent Activity"),
        icon="TRIA_DOWN" if expanded else "TRIA_RIGHT",
        emboss=False,
        translate=False,
    )
    if entries:
        header.operator(
            "blendersync.clear_recent_activity",
            text=iface("Clear"),
            translate=False,
        )
    if not expanded:
        return
    if not entries:
        box.label(text="No activity recorded")
        return
    for entry in reversed(entries):
        _draw_activity_entry(box, entry)


def _draw_developer_diagnostics(layout, scene, state, context=None) -> None:
    box = layout.box()
    if not _draw_disclosure(
        box,
        scene,
        "blendersync_show_diagnostics_developer",
        "Developer",
    ):
        return

    preferences = get_addon_preferences(context)
    if preferences is not None:
        box.prop(preferences, "verbose_logging", text="Verbose Logging", toggle=True)
    box.prop(scene, "blendersync_show_debug", text="Detailed Runtime State", toggle=True)

    phase_labels = {
        "confirmed": "Confirmed",
        "counterpart_observed": "Awaiting confirmation",
        "transport_connected": "Transport connected",
        "local_ready": "Waiting for Unity",
        "disconnected": "Disconnected",
    }
    phase = phase_labels.get(str(state.get("handshake_phase") or ""), "Unknown")
    peer_version = state.get("peer_protocol_version")
    if state.get("legacy_protocol"):
        protocol = "Legacy"
    elif peer_version is not None:
        protocol = f"v{peer_version}"
    else:
        protocol = "Negotiating" if state.get("transport_connected") else "-"

    status_row = box.row(align=True)
    status_row.label(text=format_iface("Phase: {phase}", phase=iface(phase)), translate=False)
    status_row.label(text=format_iface("Protocol: {protocol}", protocol=iface(protocol)), translate=False)

    endpoint = _short_text(state.get("endpoint") or "None", 44)
    box.label(text=format_iface("Endpoint: {endpoint}", endpoint=endpoint), translate=False)
    features = ", ".join(state.get("negotiated_features") or [])
    if features:
        box.label(
            text=format_iface("Features: {features}", features=_short_text(features, 44)),
            translate=False,
        )
    if state.get("last_ws_handoff_error"):
        box.label(
            text=format_iface(
                "WS Error: {error}",
                error=_short_text(state.get("last_ws_handoff_error"), 42),
            ),
            icon="ERROR",
            translate=False,
        )


def _draw_diagnostics_tab(layout, scene, state, context=None) -> None:
    _draw_session_diagnostics(layout, state)
    _draw_recent_activity(layout, scene)

    needs_controller_state = bool(
        getattr(scene, "blendersync_show_diagnostics_active_object", False)
        or getattr(scene, "blendersync_show_diagnostics_runtime", False)
    )
    controller_state = {}
    if needs_controller_state and get_controller_state is not None:
        try:
            controller_state = get_controller_state(
                include_active_object_baseline=bool(
                    getattr(scene, "blendersync_show_diagnostics_active_object", False)
                )
            ) or {}
        except Exception:
            controller_state = {}

    _draw_active_object_diagnostics(layout, scene, controller_state)
    _draw_runtime_diagnostics(layout, scene, controller_state)
    _draw_sync_tuning(layout, scene, context)
    _draw_developer_diagnostics(layout, scene, state, context)

    copy_row = layout.row(align=True)
    copy_row.scale_y = 1.1
    copy_row.operator(
        "blendersync.copy_diagnostics",
        text="Copy Diagnostics",
        icon="COPY_ID",
    )

class BS_PT_SessionPanel(PanelBase):
    bl_label = "TriSync Session"
    bl_idname = "BS_PT_session_panel"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "TriSync"

    def draw(self, context):
        if bpy is None:
            return

        layout = self.layout
        scene = context.scene
        state = get_session_view_state()

        _draw_connection(layout, state, context)
        _draw_primary_controls(layout, scene)
        tab = _draw_tab_bar(layout, scene)
        if tab == "objects":
            _draw_objects_tab(layout, scene)
        elif tab == "materials":
            _draw_materials_tab(layout, context)
        elif tab == "rig_pose":
            _draw_rig_pose_tab(layout, context)
        elif tab == "animation":
            _draw_animation_panel(layout, scene)
        elif tab == "diagnostics":
            _draw_diagnostics_tab(layout, scene, state, context)


class BS_PT_UvSyncPanel(PanelBase):
    bl_label = "TriSync UV"
    bl_idname = "BS_PT_uv_sync_panel"
    bl_space_type = "IMAGE_EDITOR"
    bl_region_type = "UI"
    bl_category = "TriSync"

    @classmethod
    def poll(cls, context):
        if bpy is None or context is None:
            return False
        space = getattr(context, "space_data", None)
        return bool(space is not None and getattr(space, "type", "") == "IMAGE_EDITOR")

    def draw(self, context):
        if bpy is None:
            return
        layout = self.layout
        col = layout.column(align=True)
        col.operator("blendersync.manual_preview_active_uv", text="Preview UV")
        col.label(text="Use after UV edits.")
        col.label(text="No background UV polling.")
        col.label(text="Preview commits after idle time.")


def _resolve_active_material_for_panel(context):
    if context is None:
        return None
    space = getattr(context, "space_data", None)
    if space is not None:
        pinned = getattr(space, "pin_id", None)
        if pinned is not None and hasattr(pinned, "use_nodes"):
            return pinned
        pinned = getattr(space, "id", None)
        if pinned is not None and hasattr(pinned, "use_nodes"):
            return pinned
    material = getattr(context, "material", None)
    if material is not None and hasattr(material, "use_nodes"):
        return material
    active = getattr(context, "active_object", None)
    return getattr(active, "active_material", None) if active is not None else None


def _resolve_active_rig_for_panel(context):
    active = getattr(context, "active_object", None) if context is not None else None
    if active is None:
        return None
    if getattr(active, "type", None) == "ARMATURE" and getattr(active, "data", None) is not None:
        return active
    find_armature = getattr(active, "find_armature", None)
    try:
        armature = find_armature() if callable(find_armature) else None
    except Exception:
        armature = None
    if armature is None or getattr(armature, "type", None) != "ARMATURE":
        return None
    return armature if getattr(armature, "data", None) is not None else None


def _material_image_texture_count(material) -> int:
    node_tree = getattr(material, "node_tree", None) if material is not None else None
    if node_tree is None:
        return 0

    def identity(value) -> tuple[str, int]:
        try:
            session_uid = int(getattr(value, "session_uid", 0) or 0)
            if session_uid > 0:
                return ("session_uid", session_uid)
        except Exception:
            pass
        try:
            pointer = int(value.as_pointer())
            if pointer > 0:
                return ("pointer", pointer)
        except Exception:
            pass
        return ("python", id(value))

    visited_trees: set[tuple[str, int]] = set()
    image_ids: set[tuple[str, int]] = set()

    def visit(tree) -> None:
        tree_key = identity(tree)
        if tree_key in visited_trees:
            return
        visited_trees.add(tree_key)
        for node in list(getattr(tree, "nodes", []) or []):
            node_type = str(getattr(node, "type", "") or "")
            node_idname = str(getattr(node, "bl_idname", "") or "")
            if node_type == "TEX_IMAGE" or node_idname == "ShaderNodeTexImage":
                image = getattr(node, "image", None)
                if image is not None:
                    image_ids.add(identity(image))
            nested_tree = getattr(node, "node_tree", None)
            if nested_tree is not None:
                visit(nested_tree)

    visit(node_tree)
    return len(image_ids)


def _material_panel_summary(material) -> dict:
    use_nodes = bool(getattr(material, "use_nodes", False)) if material is not None else False
    node_tree = getattr(material, "node_tree", None) if material is not None else None
    if not use_nodes or node_tree is None:
        shader = "Nodes Disabled"
    else:
        try:
            shader = "Principled BSDF" if find_principled(material) is not None else "Fallback Surface"
        except Exception:
            shader = "Fallback Surface"
    return {
        "shader": shader,
        "texture_count": _material_image_texture_count(material),
    }


class BS_PT_MaterialSyncPanel(PanelBase):
    bl_label = "TriSync Material"
    bl_idname = "BS_PT_material_sync_panel"
    bl_space_type = "NODE_EDITOR"
    bl_region_type = "UI"
    bl_category = "TriSync"

    @classmethod
    def poll(cls, context):
        if bpy is None or context is None:
            return False
        space = getattr(context, "space_data", None)
        return bool(space is not None and getattr(space, "tree_type", "") == "ShaderNodeTree")

    def draw(self, context):
        if bpy is None:
            return
        _draw_materials_tab(self.layout, context)


CLASSES = (
    BS_PT_SessionPanel,
    BS_PT_UvSyncPanel,
    BS_PT_MaterialSyncPanel,
)


def _safe_unregister_class(cls) -> None:
    if bpy is None:
        return
    candidates = [cls]
    existing = getattr(bpy.types, getattr(cls, "__name__", ""), None)
    if existing is not None and existing is not cls:
        candidates.insert(0, existing)
    for candidate in candidates:
        try:
            bpy.utils.unregister_class(candidate)
            return
        except Exception:
            continue


def _safe_register_class(cls) -> None:
    if bpy is None:
        return
    try:
        bpy.utils.register_class(cls)
        return
    except Exception as exc:
        if "already registered" not in str(exc):
            raise
    _safe_unregister_class(cls)
    bpy.utils.register_class(cls)


def register() -> None:
    if bpy is None:
        return
    for cls in CLASSES:
        _safe_register_class(cls)
    _register_ui_redraw_timer()


def unregister() -> None:
    if bpy is None:
        return
    _unregister_ui_redraw_timer()
    for cls in reversed(CLASSES):
        _safe_unregister_class(cls)
