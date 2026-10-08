from __future__ import annotations

import time

from blender.animation_clip import send_active_object_animation_clip_once
from blender.asset_registry import is_asset_known, mark_asset_source_fingerprints
from blender.common.exception_boundary import report_boundary_exception
from blender.common.localization import report as translate_report
from blender.common.log import trace, warn
from blender.common.types import SendResult
from blender.identity import (
    ensure_instance_id,
    ensure_mesh_asset_id_for_object,
    ensure_unique_asset_id,
    get_mesh_asset_id_for_object,
    set_mesh_asset_id_for_object,
)
from blender.object_classification import is_supported_scene_object, summarize_unsupported
from blender.scene_sync.settings import get_addon_preferences, get_session_port, reset_performance_tuning
from blender.scene_sync.controller import manual_commit_selected_previews_once, manual_preview_active_uv_once, sync_objects_once, sync_selected_objects_once
from blender.transport.entrypoints import (
    send_material_content_v1,
    send_object_state_update,
    send_rigged_blendshape_weights_sync,
    send_rigged_pose_sync,
    send_scene_view_state,
    send_selected_resources,
)
from blender.rigged_object import (
    build_unity_rig_v1_blendshape_weights_payload,
    build_unity_rig_v1_pose_sync_payload,
)
from blender.ui.bulk_job import (
    begin_job,
    cancel_job,
    cancel_requested,
    dismiss_job,
    finish_job,
    get_state as get_bulk_job_state,
    request_cancel,
    should_use_bulk_mode,
    update_job,
)
from blender.ui.object_context_builders import (
    build_objects_object_state_context,
    build_objects_hierarchy_context,
    build_single_object_live_context,
    build_selected_live_context,
    build_selected_object_state_context,
    collect_supported_sync_objects,
    collect_meshes_managed_by_armatures,
    object_identity_key,
)
from blender.ui.view_context_builders import build_scene_view_state_context
from blender.ui.state_view import (
    build_local_ws_endpoint,
    clear_diagnostics_activity,
    connect_minimal_ws_runtime,
    disconnect_minimal_ws_runtime,
    get_copy_diagnostics_json,
    get_session,
    register_pair_ids,
    set_last_operation_meta,
    set_last_send_result,
)
from blender.scene_sync.baseline import (
    capture_object_baselines,
    mesh_payload_content_fingerprints,
    set_mesh_content_fingerprint_baseline,
)
from blender.material_resource.content_v1 import build_material_content_v1, get_material_content_v1_debug_path, write_material_content_v1_debug_json
from blender.material_resource.slots import material_refs_from_live_context
from blender.ui.update_intents import (
    INTENT_ANIMATION_CLIP,
    INTENT_IMPORT_OBJECTS,
    INTENT_OBJECT_STATE,
    INTENT_STRUCTURE_ONLY,
    INTENT_VIEW_STATE,
    TRIGGER_ANIMATION_CLIP_EXPORT,
    TRIGGER_IMPORT_SELECTED_OBJECTS,
    TRIGGER_SYNC_SCENE_VIEW_MANUAL,
    TRIGGER_UPDATE_OBJECT_STATE,
    apply_update_metadata,
)
from blender.ui.update_intents import INTENT_MATERIAL_UPDATE, TRIGGER_MATERIAL_UPDATE_CONTEXTUAL


def _report_unexpected_operator_exception(site: str, exc: Exception) -> None:
    if isinstance(exc, ValueError):
        return
    report_boundary_exception(site, exc)


AUTO_SYNC_READY_KEY = "blendersync_auto_sync_ready"
_bulk_import_queue: list = []
_bulk_import_total = 0
_bulk_import_success = 0
_bulk_import_errors = 0
_bulk_import_job_id = ""
_bulk_import_structure_objects: list = []
_bulk_update_queue: list = []
_bulk_update_total = 0
_bulk_update_success = 0
_bulk_update_errors = 0
_bulk_object_state_queue: list = []
_bulk_object_state_total = 0
_bulk_object_state_success = 0
_bulk_object_state_errors = 0
_BULK_QUEUE_ITEM_INTERVAL_SECONDS = 0.05


def _any_bulk_queue_running() -> bool:
    return bool(_bulk_import_queue or _bulk_update_queue or _bulk_object_state_queue)


def _ensure_material_asset_id(mat) -> str:
    try:
        import bpy  # type: ignore
        materials = getattr(getattr(bpy, "data", None), "materials", None)
    except Exception:
        materials = None
    return ensure_unique_asset_id(mat, materials, label="material")


def _material_refs_for_baseline(obj) -> tuple[str, ...]:
    refs = []
    for slot in list(getattr(obj, "material_slots", []) or []):
        mat = getattr(slot, "material", None)
        if mat is None:
            refs.append("")
            continue
        try:
            refs.append(f"mat-{_ensure_material_asset_id(mat)}")
        except Exception:
            refs.append("")
    return tuple(refs)


def _mesh_content_fingerprints_by_ref_from_context(send_context: dict | None) -> dict[str, tuple[str | None, str | None]]:
    out: dict[str, tuple[str | None, str | None]] = {}
    if not isinstance(send_context, dict):
        return out
    for root in list(send_context.get("selected") or []):
        if not isinstance(root, dict) or root.get("type") != "mesh":
            continue
        meta = root.get("metadata") or {}
        mesh_ref = str(meta.get("assetId") or "").strip()
        if not mesh_ref:
            continue
        mesh_dict = meta.get("mesh") or {}
        fp = str(meta.get("meshContentFingerprint") or mesh_dict.get("meshContentFingerprint") or "").strip()
        fp_no_uv = str(meta.get("meshContentFingerprintNoUv") or mesh_dict.get("meshContentFingerprintNoUv") or "").strip()
        if fp or fp_no_uv:
            out[mesh_ref] = (fp or None, fp_no_uv or None)
        else:
            out[mesh_ref] = mesh_payload_content_fingerprints(mesh_dict)
    return out


def _mesh_source_fingerprints_by_ref_from_context(send_context: dict | None) -> dict[str, str]:
    out: dict[str, str] = {}
    if not isinstance(send_context, dict):
        return out
    for root in list(send_context.get("selected") or []):
        if not isinstance(root, dict) or root.get("type") != "mesh":
            continue
        meta = root.get("metadata") or {}
        asset_id = str(meta.get("assetId") or "").strip()
        fingerprint = str(meta.get("sourceFingerprint") or "").strip()
        if asset_id and fingerprint:
            out[asset_id] = fingerprint
    return out


def _capture_selected_object_baselines(objects, reason: str, send_context: dict | None = None) -> None:
    mesh_fingerprints_by_ref = _mesh_content_fingerprints_by_ref_from_context(send_context)
    for obj in list(objects or []):
        if obj is None or getattr(obj, "type", None) != "MESH":
            continue
        try:
            mesh_ref = f"mesh-{ensure_mesh_asset_id_for_object(obj)}" if getattr(obj, "data", None) is not None else ""
            pair_id = f"pair-{ensure_instance_id(obj)}"
            material_refs = material_refs_from_live_context(send_context, pair_id=pair_id, mesh_ref=mesh_ref)
            if material_refs is None:
                material_refs = _material_refs_for_baseline(obj)
            capture_object_baselines(obj, material_refs=material_refs, reason=reason)
            if mesh_ref in mesh_fingerprints_by_ref:
                fp, fp_no_uv = mesh_fingerprints_by_ref.get(mesh_ref) or (None, None)
                set_mesh_content_fingerprint_baseline(obj, fp, fp_no_uv, reason=reason)
        except Exception as exc:
            report_boundary_exception(
                "operator:capture_baseline",
                exc,
                message=f"[vNext][Baseline] capture_fail object={getattr(obj, 'name', '<unnamed>')} reason={reason} error={exc}",
            )


def _collect_context_pair_ids(send_context: dict) -> list[str]:
    pair_ids: list[str] = []
    seen: set[str] = set()

    assemblies = list(send_context.get("objectAssemblies") or [])
    single = send_context.get("objectAssembly")
    if single:
        assemblies.append(single)

    for assembly in assemblies:
        pair_id = str((assembly or {}).get("pairId") or "").strip()
        if pair_id and pair_id not in seen:
            seen.add(pair_id)
            pair_ids.append(pair_id)

    for payload in send_context.get("riggedObjects") or []:
        rig = (payload or {}).get("riggedObject") or {}
        rigged_id = str(rig.get("riggedObjectId") or "").strip()
        if not rigged_id:
            continue
        pair_id = f"rigpair-{rigged_id}"
        if pair_id not in seen:
            seen.add(pair_id)
            pair_ids.append(pair_id)

    return pair_ids


def _operation_meta_from_context(context: dict | None) -> dict:
    context = context or {}
    return {
        "updateIntent": context.get("updateIntent"),
        "triggerType": context.get("triggerType"),
        "correlationId": context.get("correlationId"),
        "selectedObjectCount": context.get("selectedObjectCount"),
        "supportedSelectedObjectCount": context.get("supportedSelectedObjectCount"),
        "unsupportedSelectedObjectCount": context.get("unsupportedSelectedObjectCount"),
        "resourceCountEstimate": context.get("resourceCountEstimate"),
        "objectAssemblyCount": len(context.get("objectAssemblies") or []) + (1 if context.get("objectAssembly") else 0),
        "riggedObjectCount": len(context.get("riggedObjects") or []),
        "objectStateCount": context.get("objectStateCount"),
    }


def _set_last_result(result, context: dict | None = None) -> None:
    set_last_send_result(result, _operation_meta_from_context(context))


def _set_last_meta_from_context(context: dict | None = None) -> None:
    set_last_operation_meta(_operation_meta_from_context(context))


def _format_unsupported_suffix(items: list[dict], limit: int = 3) -> str:
    if not items:
        return ""
    labels = []
    for item in items[:limit]:
        name = str(item.get("objectName") or "<unnamed>")
        object_type = str(item.get("objectType") or "UNKNOWN")
        labels.append(f"{name}:{object_type}")
    extra = len(items) - len(labels)
    suffix = ", ".join(labels)
    if extra > 0:
        suffix += f", +{extra} more"
    return suffix


def _report_selection_context_error(operator, context, reason: str, empty_message: str) -> None:
    if reason == "selection_empty":
        operator.report({"WARNING"}, empty_message)
    elif reason == "selection_no_supported_objects":
        selected = list(getattr(context, "selected_objects", []) or []) if context is not None else []
        unsupported_suffix = _format_unsupported_suffix(summarize_unsupported(selected))
        operator.report({"WARNING"}, "No supported objects selected" + (f": {unsupported_suffix}" if unsupported_suffix else "."))
    elif reason == "selection_no_imported_objects":
        operator.report({"WARNING"}, "No imported objects selected. Use Import / Repair first.")
    else:
        operator.report({"ERROR"}, reason)


def _selected_count_for_bulk(context) -> int:
    selected = list(getattr(context, "selected_objects", []) or []) if context is not None else []
    if selected:
        return len(selected)
    active = getattr(context, "object", None) if context is not None else None
    return 1 if active is not None else 0


def _selected_or_active_objects(context) -> list:
    selected = list(getattr(context, "selected_objects", []) or []) if context is not None else []
    if selected:
        return [obj for obj in selected if obj is not None]
    active = getattr(context, "active_object", None) if context is not None else None
    if active is None and context is not None:
        active = getattr(context, "object", None)
    return [active] if active is not None else []


def _session_handshake_confirmed() -> bool:
    try:
        return bool(get_session().get_truth_state().get("handshake_confirmed"))
    except Exception:
        return False


def _ready_update_target(obj, *, allow_rigged_owner: bool) -> bool:
    if obj is None or not is_supported_scene_object(obj):
        return False
    if bool(obj.get(AUTO_SYNC_READY_KEY, False)):
        return True
    if not allow_rigged_owner or getattr(obj, "type", None) != "MESH":
        return False
    find_armature = getattr(obj, "find_armature", None)
    armature = find_armature() if callable(find_armature) else None
    return bool(armature is not None and armature.get(AUTO_SYNC_READY_KEY, False))


def _manual_action_unavailable_reason(
    context,
    *,
    action: str,
    require_ready: bool,
    allow_rigged_owner: bool = False,
) -> str:
    if not _session_handshake_confirmed():
        return f"Connect Unity before {action}."
    selected = _selected_or_active_objects(context)
    supported = [obj for obj in selected if is_supported_scene_object(obj)]
    if not supported:
        return "Select a supported object first."
    if require_ready and not any(
        _ready_update_target(obj, allow_rigged_owner=allow_rigged_owner) for obj in supported
    ):
        return "Select an imported object to update. Use Import / Repair first."
    return ""


def _poll_manual_action(cls, reason: str) -> bool:
    if not reason:
        return True
    setter = getattr(cls, "poll_message_set", None)
    if callable(setter):
        try:
            setter(reason)
        except Exception:
            pass
    return False


def _is_expected_precondition_failure(reason: str | None) -> bool:
    return str(reason or "") in {
        "handshake_not_confirmed",
        "session_not_connected",
        "selection_empty",
        "selection_no_supported_objects",
        "selection_no_imported_objects",
        "mesh_has_no_exportable_geometry",
    }


def _bulk_import_objects_from_context(context) -> list:
    selected = list(getattr(context, "selected_objects", []) or []) if context is not None else []
    if not selected:
        active = getattr(context, "object", None) if context is not None else None
        selected = [active] if active is not None else []
    supported = collect_supported_sync_objects(selected)
    managed_mesh_keys = collect_meshes_managed_by_armatures(supported)
    out = []
    seen = set()
    for obj in supported:
        if obj is None:
            continue
        if getattr(obj, "type", None) == "MESH" and object_identity_key(obj) in managed_mesh_keys:
            continue
        key = object_identity_key(obj)
        if key in seen:
            continue
        seen.add(key)
        out.append(obj)
    return out


def _mark_import_context_ready(send_context: dict) -> int:
    pair_ids = _collect_context_pair_ids(send_context)
    pair_records = [{"pairId": pair_id, "objectId": pair_id} for pair_id in pair_ids]
    register_pair_ids(pair_records, source_hint="bulk_import")

    selected_supported_objects = send_context.get("_supportedObjects", []) or []
    managed_mesh_keys = collect_meshes_managed_by_armatures(selected_supported_objects)
    for obj in selected_supported_objects:
        if getattr(obj, "type", None) == "MESH" and object_identity_key(obj) in managed_mesh_keys:
            continue
        obj[AUTO_SYNC_READY_KEY] = True
    _capture_selected_object_baselines(selected_supported_objects, reason="bulk_import_selected", send_context=send_context)
    mark_asset_source_fingerprints(_mesh_source_fingerprints_by_ref_from_context(send_context))
    return len(pair_ids)


def _send_bulk_import_structure_once() -> None:
    if not _bulk_import_structure_objects:
        return
    try:
        structure_context = build_objects_hierarchy_context(
            get_session(),
            list(_bulk_import_structure_objects),
            trigger_type="bulk_import_structure",
        )
        apply_update_metadata(structure_context, update_intent=INTENT_STRUCTURE_ONLY, trigger_type="bulk_import_structure")
        result = send_selected_resources(structure_context)
        _set_last_result(result, structure_context)
        if not result.ok:
            warn(
                "BulkOperation",
                "import_structure_failed",
                result.error or result.message or "send_failed",
            )
    except Exception as exc:
        report_boundary_exception(
            "bulk_import:structure",
            exc,
            message=f"[vNext][BulkImport] structure_exception error={exc}",
        )


def _bulk_import_pump():
    global _bulk_import_success, _bulk_import_errors, _bulk_import_structure_objects
    if bpy is None:
        return None
    if cancel_requested():
        remaining = len(_bulk_import_queue)
        _bulk_import_queue.clear()
        _bulk_import_structure_objects = []
        cancel_job(f"Cancelled. Remaining objects={remaining}")
        return None
    if not _bulk_import_queue:
        ok = _bulk_import_errors == 0
        _send_bulk_import_structure_once()
        finish_job(
            ok=ok,
            processed_objects=_bulk_import_success + _bulk_import_errors,
            message=f"bulk_import_done success={_bulk_import_success} errors={_bulk_import_errors}",
        )
        _bulk_import_structure_objects = []
        return None

    index = _bulk_import_success + _bulk_import_errors + 1
    obj = _bulk_import_queue.pop(0)
    obj_name = str(getattr(obj, "name", "<unnamed>") or "<unnamed>")
    last_error = ""
    update_job(processed_objects=index - 1, message=f"Importing {obj_name}", current_object=obj_name)
    try:
        package_id = f"pkg-bulk-import-{int(time.time() * 1000)}-{index}"
        send_context = build_single_object_live_context(get_session(), obj, package_id=package_id)
        send_context["sendMode"] = "manual_resource_resend"
        send_context["selectedObjectCount"] = 1
        send_context["supportedSelectedObjectCount"] = 1
        send_context["unsupportedSelectedObjectCount"] = 0
        send_context["unsupportedObjects"] = []
        send_context["_supportedObjects"] = [obj]
        apply_update_metadata(send_context, update_intent=INTENT_IMPORT_OBJECTS, trigger_type=TRIGGER_IMPORT_SELECTED_OBJECTS)
        result = send_selected_resources(send_context)
        _set_last_result(result, send_context)
        if result.ok:
            _mark_import_context_ready(send_context)
            _bulk_import_structure_objects.append(obj)
            _bulk_import_success += 1
        else:
            _bulk_import_errors += 1
            last_error = str(result.error or result.message or "send_failed")
            warn(
                "BulkOperation",
                "import_item_failed",
                last_error,
                {"objectName": obj_name},
            )
    except Exception as exc:
        _bulk_import_errors += 1
        last_error = str(exc)
        report_boundary_exception(
            "bulk_import:item",
            exc,
            message=f"[vNext][BulkImport] item_exception object={obj_name} error={last_error}",
        )

    update_job(
        processed_objects=_bulk_import_success + _bulk_import_errors,
        message=f"Bulk import progress success={_bulk_import_success} errors={_bulk_import_errors}",
        success_count=_bulk_import_success,
        error_count=_bulk_import_errors,
        last_error=last_error or None,
    )
    return _BULK_QUEUE_ITEM_INTERVAL_SECONDS if _bulk_import_queue else 0.01


def _start_bulk_import_job(context) -> tuple[bool, str]:
    global _bulk_import_queue, _bulk_import_total, _bulk_import_success, _bulk_import_errors, _bulk_import_job_id, _bulk_import_structure_objects
    if _any_bulk_queue_running():
        return False, "bulk_job_already_running"
    objects = _bulk_import_objects_from_context(context)
    if not objects:
        return False, "selection_no_supported_objects"

    state = begin_job("import_selected_objects", len(objects), mode="bulk_pending")
    _bulk_import_queue = list(objects)
    _bulk_import_total = len(objects)
    _bulk_import_success = 0
    _bulk_import_errors = 0
    _bulk_import_job_id = str(state.get("jobId") or "")
    _bulk_import_structure_objects = []
    update_job(processed_objects=0, message=f"Queued bulk import objects={_bulk_import_total}", success_count=0, error_count=0, skipped_count=0)
    try:
        bpy.app.timers.register(_bulk_import_pump, first_interval=0.01)
    except Exception as exc:
        _bulk_import_queue.clear()
        _bulk_import_structure_objects.clear()
        report_boundary_exception(
            "bulk_import:timer_register",
            exc,
            message=f"[vNext][BulkImport] timer_register_failed error={exc}",
        )
        finish_job(ok=False, processed_objects=0, message=f"bulk_timer_register_failed:{exc}")
        return False, "bulk_timer_register_failed"
    return True, ""


def _bulk_update_objects_from_context(context) -> list:
    selected = list(getattr(context, "selected_objects", []) or []) if context is not None else []
    if not selected:
        active = getattr(context, "object", None) if context is not None else None
        selected = [active] if active is not None else []

    supported = collect_supported_sync_objects(selected)
    managed_mesh_keys = collect_meshes_managed_by_armatures(supported)
    out = []
    seen = set()
    for obj in supported:
        if obj is None:
            continue

        sync_obj = obj
        obj_type = getattr(obj, "type", None)
        if obj_type == "MESH":
            if object_identity_key(obj) in managed_mesh_keys:
                continue
            find_armature = getattr(obj, "find_armature", None)
            armature_obj = find_armature() if callable(find_armature) else None
            if armature_obj is not None and bool(armature_obj.get(AUTO_SYNC_READY_KEY, False)):
                sync_obj = armature_obj

        if not bool(sync_obj.get(AUTO_SYNC_READY_KEY, False)):
            continue

        key = object_identity_key(sync_obj)
        if key in seen:
            continue
        seen.add(key)
        out.append(sync_obj)
    return out


def _bulk_update_pump():
    global _bulk_update_success, _bulk_update_errors
    if bpy is None:
        return None
    if cancel_requested():
        remaining = len(_bulk_update_queue)
        _bulk_update_queue.clear()
        cancel_job(f"Cancelled. Remaining objects={remaining}")
        return None
    if not _bulk_update_queue:
        ok = _bulk_update_errors == 0
        finish_job(
            ok=ok,
            processed_objects=_bulk_update_success + _bulk_update_errors,
            message=f"bulk_update_done success={_bulk_update_success} errors={_bulk_update_errors}",
        )
        return None

    index = _bulk_update_success + _bulk_update_errors + 1
    obj = _bulk_update_queue.pop(0)
    obj_name = str(getattr(obj, "name", "<unnamed>") or "<unnamed>")
    last_error = ""
    update_job(processed_objects=index - 1, message=f"Updating {obj_name}", current_object=obj_name)
    try:
        result_dict = sync_objects_once([obj])
        ok = bool(result_dict.get("ok")) or str(result_dict.get("reason") or "") == "sync_selected_noop"
        if ok:
            _bulk_update_success += 1
        else:
            _bulk_update_errors += 1
            last_error = str(result_dict.get("reason") or "sync_failed")
            warn(
                "BulkOperation",
                "update_item_failed",
                last_error,
                {"objectName": obj_name},
            )
    except Exception as exc:
        _bulk_update_errors += 1
        last_error = str(exc)
        report_boundary_exception(
            "bulk_update:item",
            exc,
            message=f"[vNext][BulkUpdate] item_exception object={obj_name} error={last_error}",
        )

    update_job(
        processed_objects=_bulk_update_success + _bulk_update_errors,
        message=f"Bulk update progress success={_bulk_update_success} errors={_bulk_update_errors}",
        success_count=_bulk_update_success,
        error_count=_bulk_update_errors,
        last_error=last_error or None,
    )
    return _BULK_QUEUE_ITEM_INTERVAL_SECONDS if _bulk_update_queue else 0.01


def _start_bulk_update_job(context) -> tuple[bool, str]:
    global _bulk_update_queue, _bulk_update_total, _bulk_update_success, _bulk_update_errors
    if _any_bulk_queue_running():
        return False, "bulk_job_already_running"
    objects = _bulk_update_objects_from_context(context)
    if not objects:
        return False, "selection_no_imported_objects"

    begin_job("update_selected_active", len(objects), mode="bulk_pending")
    _bulk_update_queue = list(objects)
    _bulk_update_total = len(objects)
    _bulk_update_success = 0
    _bulk_update_errors = 0
    update_job(processed_objects=0, message=f"Queued bulk update objects={_bulk_update_total}", success_count=0, error_count=0, skipped_count=0)
    try:
        bpy.app.timers.register(_bulk_update_pump, first_interval=0.01)
    except Exception as exc:
        _bulk_update_queue.clear()
        report_boundary_exception(
            "bulk_update:timer_register",
            exc,
            message=f"[vNext][BulkUpdate] timer_register_failed error={exc}",
        )
        finish_job(ok=False, processed_objects=0, message=f"bulk_timer_register_failed:{exc}")
        return False, "bulk_timer_register_failed"
    return True, ""


def _bulk_object_state_objects_from_context(context) -> list:
    selected = list(getattr(context, "selected_objects", []) or []) if context is not None else []
    if not selected:
        active = getattr(context, "object", None) if context is not None else None
        selected = [active] if active is not None else []

    selected_armatures = {obj for obj in selected if obj is not None and getattr(obj, "type", None) == "ARMATURE"}
    out = []
    seen = set()
    for obj in selected:
        if obj is None:
            continue
        parent = getattr(obj, "parent", None)
        skip_descendant = False
        while parent is not None:
            if parent in selected_armatures:
                skip_descendant = True
                break
            parent = getattr(parent, "parent", None)
        if skip_descendant:
            continue

        supported = collect_supported_sync_objects([obj])
        if obj not in supported:
            continue
        if not bool(obj.get(AUTO_SYNC_READY_KEY, False)):
            continue
        key = object_identity_key(obj)
        if key in seen:
            continue
        seen.add(key)
        out.append(obj)
    return out


def _bulk_object_state_pump():
    global _bulk_object_state_success, _bulk_object_state_errors
    if bpy is None:
        return None
    if cancel_requested():
        remaining = len(_bulk_object_state_queue)
        _bulk_object_state_queue.clear()
        cancel_job(f"Cancelled. Remaining objects={remaining}")
        return None
    if not _bulk_object_state_queue:
        ok = _bulk_object_state_errors == 0
        finish_job(
            ok=ok,
            processed_objects=_bulk_object_state_success + _bulk_object_state_errors,
            message=f"bulk_object_state_done success={_bulk_object_state_success} errors={_bulk_object_state_errors}",
        )
        return None

    index = _bulk_object_state_success + _bulk_object_state_errors + 1
    obj = _bulk_object_state_queue.pop(0)
    obj_name = str(getattr(obj, "name", "<unnamed>") or "<unnamed>")
    last_error = ""
    update_job(processed_objects=index - 1, message=f"Updating state {obj_name}", current_object=obj_name)
    try:
        send_context = build_objects_object_state_context(get_session(), [obj], selected_object_count=1)
        apply_update_metadata(send_context, update_intent=INTENT_OBJECT_STATE, trigger_type=TRIGGER_UPDATE_OBJECT_STATE)
        result = send_object_state_update(send_context)
        _set_last_result(result, send_context)
        if result.ok:
            _bulk_object_state_success += 1
        else:
            _bulk_object_state_errors += 1
            last_error = str(result.error or result.message or "send_failed")
            warn(
                "BulkOperation",
                "object_state_item_failed",
                last_error,
                {"objectName": obj_name},
            )
    except Exception as exc:
        _bulk_object_state_errors += 1
        last_error = str(exc)
        report_boundary_exception(
            "bulk_object_state:item",
            exc,
            message=f"[vNext][BulkObjectState] item_exception object={obj_name} error={last_error}",
        )

    update_job(
        processed_objects=_bulk_object_state_success + _bulk_object_state_errors,
        message=f"Bulk object state progress success={_bulk_object_state_success} errors={_bulk_object_state_errors}",
        success_count=_bulk_object_state_success,
        error_count=_bulk_object_state_errors,
        last_error=last_error or None,
    )
    return _BULK_QUEUE_ITEM_INTERVAL_SECONDS if _bulk_object_state_queue else 0.01


def _start_bulk_object_state_job(context) -> tuple[bool, str]:
    global _bulk_object_state_queue, _bulk_object_state_total, _bulk_object_state_success, _bulk_object_state_errors
    if _any_bulk_queue_running():
        return False, "bulk_job_already_running"
    objects = _bulk_object_state_objects_from_context(context)
    if not objects:
        return False, "selection_no_imported_objects"

    begin_job("update_object_state", len(objects), mode="bulk_pending")
    _bulk_object_state_queue = list(objects)
    _bulk_object_state_total = len(objects)
    _bulk_object_state_success = 0
    _bulk_object_state_errors = 0
    update_job(processed_objects=0, message=f"Queued bulk object state objects={_bulk_object_state_total}", success_count=0, error_count=0, skipped_count=0)
    try:
        bpy.app.timers.register(_bulk_object_state_pump, first_interval=0.01)
    except Exception as exc:
        _bulk_object_state_queue.clear()
        report_boundary_exception(
            "bulk_object_state:timer_register",
            exc,
            message=f"[vNext][BulkObjectState] timer_register_failed error={exc}",
        )
        finish_job(ok=False, processed_objects=0, message=f"bulk_timer_register_failed:{exc}")
        return False, "bulk_timer_register_failed"
    return True, ""

try:
    import bpy  # type: ignore
except ImportError:  # non-Blender runtime fallback
    bpy = None


if bpy is not None:
    class OperatorBase(bpy.types.Operator):
        def report(self, levels, message):
            return super().report(levels, translate_report(str(message or "")))
else:
    class OperatorBase:  # type: ignore[no-redef]
        bl_idname = ""
        bl_label = ""

        def report(self, _levels, _message: str) -> None:
            pass


class BS_OT_SendSelectedResources(OperatorBase):
    bl_idname = "blendersync.send_selected_resources"
    bl_label = "Import / Repair Selected Objects"
    bl_description = (
        "Import or repair supported selected objects in Unity using the automatic mesh source policy; material node/property "
        "edits should be sent from the Mats tab."
    )

    @classmethod
    def poll(cls, context):
        reason = _manual_action_unavailable_reason(
            context,
            action="importing or repairing",
            require_ready=False,
        )
        return _poll_manual_action(cls, reason)

    def execute(self, context):
        unavailable = _manual_action_unavailable_reason(
            context,
            action="importing or repairing",
            require_ready=False,
        )
        if unavailable:
            self.report({"WARNING"}, unavailable)
            return {"CANCELLED"}

        selected_count = _selected_count_for_bulk(context)
        if should_use_bulk_mode(selected_count):
            ok, reason = _start_bulk_import_job(context)
            if ok:
                self.report({"INFO"}, f"bulk_import_started objects={selected_count}")
                return {"FINISHED"}
            level = {"WARNING"} if _is_expected_precondition_failure(reason) else {"ERROR"}
            self.report(level, reason or "bulk_import_start_failed")
            return {"CANCELLED"}

        bulk_state = begin_job("import_selected_objects", selected_count)
        send_context = None
        try:
            send_context = build_selected_live_context(get_session(), package_id=f"pkg-selected-manual-{int(time.time() * 1000)}")
            send_context["sendMode"] = "manual_resource_resend"
            apply_update_metadata(send_context, update_intent=INTENT_IMPORT_OBJECTS, trigger_type=TRIGGER_IMPORT_SELECTED_OBJECTS)
        except Exception as exc:
            reason = str(exc) or "send_selected_context_failed"
            expected = _is_expected_precondition_failure(reason)
            if not expected:
                _report_unexpected_operator_exception("operator:import_selected", exc)
            finish_job(ok=expected, processed_objects=0, message=reason)
            self.report({"WARNING"} if expected else {"ERROR"}, reason)
            return {"CANCELLED"}

        result = send_selected_resources(send_context)
        if result.ok and bpy is not None and context is not None:
            pair_ids = _collect_context_pair_ids(send_context)
            pair_records = [{"pairId": pair_id, "objectId": pair_id} for pair_id in pair_ids]
            register_pair_ids(pair_records, source_hint="send_selected")

            selected_supported_objects = send_context.get("_supportedObjects", []) or []
            managed_mesh_keys = collect_meshes_managed_by_armatures(selected_supported_objects)
            for obj in selected_supported_objects:
                if getattr(obj, "type", None) == "MESH" and object_identity_key(obj) in managed_mesh_keys:
                    continue
                obj[AUTO_SYNC_READY_KEY] = True
            _capture_selected_object_baselines(selected_supported_objects, reason="import_selected", send_context=send_context)
            mark_asset_source_fingerprints(_mesh_source_fingerprints_by_ref_from_context(send_context))

            supported = int(send_context.get("supportedSelectedObjectCount", len(selected_supported_objects)) or 0)
            unsupported = int(send_context.get("unsupportedSelectedObjectCount", 0) or 0)
            resources = int(send_context.get("resourceCountEstimate", 0) or 0)
            unsupported_suffix = _format_unsupported_suffix(send_context.get("unsupportedObjects") or [])
            if unsupported > 0:
                self.report(
                    {"WARNING"},
                    (
                        f"import_selected_partial mapped={len(pair_ids)} supported={supported} "
                        f"resources={resources} skippedUnsupported={unsupported}"
                        + (f" [{unsupported_suffix}]" if unsupported_suffix else "")
                    ),
                )
            elif pair_ids:
                self.report({"INFO"}, f"import_selected_ok mapped={len(pair_ids)} resources={resources}")
            else:
                self.report({"INFO"}, "import_selected_ok")

        result_reason = str(result.error or result.message or "")
        expected = not result.ok and _is_expected_precondition_failure(result_reason)
        if not expected:
            _set_last_result(result, send_context)
        processed = int((send_context or {}).get("supportedSelectedObjectCount", 0) or bulk_state.get("totalObjects") or 0)
        finish_job(ok=bool(result.ok) or expected, processed_objects=processed, message=result.message or result.error or "")
        if result.ok:
            return {"FINISHED"}

        self.report({"WARNING"} if expected else {"ERROR"}, result.error or "send_selected_failed")
        return {"CANCELLED"}


class BS_OT_SyncSelectedObjectState(OperatorBase):
    bl_idname = "blendersync.sync_selected_object_state"
    bl_label = "Update Selected Object State"
    bl_description = (
        "Update lightweight state for the current Blender selection only: transform, name, visibility, and hierarchy. "
        "Objects hidden and removed from selection are ignored; update them separately."
    )

    @classmethod
    def poll(cls, context):
        if getattr(context, "mode", "OBJECT") != "OBJECT":
            return _poll_manual_action(cls, "Object Mode is required to update object state.")
        reason = _manual_action_unavailable_reason(
            context,
            action="updating object state",
            require_ready=True,
        )
        return _poll_manual_action(cls, reason)

    def execute(self, context):
        if bpy is None or context is None:
            self.report({"ERROR"}, "blender_context_missing")
            return {"CANCELLED"}
        if getattr(context, "mode", "OBJECT") != "OBJECT":
            self.report({"WARNING"}, "Object Mode is required to update object state.")
            return {"CANCELLED"}

        unavailable = _manual_action_unavailable_reason(
            context,
            action="updating object state",
            require_ready=True,
        )
        if unavailable:
            self.report({"WARNING"}, unavailable)
            return {"CANCELLED"}

        selected_count = _selected_count_for_bulk(context)
        if should_use_bulk_mode(selected_count):
            ok, reason = _start_bulk_object_state_job(context)
            if ok:
                self.report({"INFO"}, f"bulk_object_state_started objects={selected_count}")
                return {"FINISHED"}
            level = {"WARNING"} if _is_expected_precondition_failure(reason) else {"ERROR"}
            self.report(level, reason or "bulk_object_state_start_failed")
            return {"CANCELLED"}

        try:
            send_context = build_selected_object_state_context(get_session())
            apply_update_metadata(send_context, update_intent=INTENT_OBJECT_STATE, trigger_type=TRIGGER_UPDATE_OBJECT_STATE)
        except Exception as exc:
            reason = str(exc) or "object_state_context_failed"
            if not _is_expected_precondition_failure(reason):
                _report_unexpected_operator_exception("operator:update_object_state", exc)
            _report_selection_context_error(self, context, reason, "No selected or active object.")
            return {"CANCELLED"}

        result = send_object_state_update(send_context)
        result_reason = str(result.error or result.message or "")
        expected = not result.ok and _is_expected_precondition_failure(result_reason)
        if not expected:
            _set_last_result(result, send_context)

        count = int(send_context.get("objectStateCount", 0) or 0)
        unsupported = int(send_context.get("unsupportedSelectedObjectCount", 0) or 0)
        skipped_not_ready = int(send_context.get("skippedNotReadyObjectCount", 0) or 0)
        if result.ok:
            suffix = ""
            if skipped_not_ready > 0:
                suffix += f" skippedNotReady={skipped_not_ready}"
            if unsupported > 0:
                suffix += f" skippedUnsupported={unsupported}"
            level = {"WARNING"} if skipped_not_ready > 0 or unsupported > 0 else {"INFO"}
            self.report(level, f"update_state_ok objects={count}{suffix}")
            return {"FINISHED"}

        self.report({"WARNING"} if expected else {"ERROR"}, result.error or "update_state_failed")
        return {"CANCELLED"}


class BS_OT_SyncSceneViewToUnity(OperatorBase):
    bl_idname = "blendersync.sync_scene_view_to_unity"
    bl_label = "Sync Scene View to Unity"

    def execute(self, context):
        try:
            send_context = build_scene_view_state_context(get_session(), context)
            apply_update_metadata(send_context, update_intent=INTENT_VIEW_STATE, trigger_type=TRIGGER_SYNC_SCENE_VIEW_MANUAL)
        except Exception as exc:
            _report_unexpected_operator_exception("operator:sync_scene_view", exc)
            self.report({"ERROR"}, str(exc) or "view_state_context_failed")
            return {"CANCELLED"}

        result = send_scene_view_state(send_context)
        _set_last_result(result, send_context)
        if result.ok:
            mode = (send_context.get("viewStatePayload") or {}).get("viewMode") or "UNKNOWN"
            self.report({"INFO"}, f"sync_scene_view_ok mode={mode}")
            return {"FINISHED"}

        self.report({"ERROR"}, result.error or "sync_scene_view_failed")
        return {"CANCELLED"}


class BS_OT_SyncSelectedObjects(OperatorBase):
    bl_idname = "blendersync.sync_selected_objects"
    bl_label = "Update Selected / Active"
    bl_description = (
        "Update imported resources and object state for the current Blender selection or active object. "
        "Objects hidden and removed from selection are ignored; update them separately."
    )

    @classmethod
    def poll(cls, context):
        if getattr(context, "mode", "OBJECT") != "OBJECT":
            return _poll_manual_action(cls, "Object Mode is required to update selected objects.")
        reason = _manual_action_unavailable_reason(
            context,
            action="updating selected objects",
            require_ready=True,
            allow_rigged_owner=True,
        )
        return _poll_manual_action(cls, reason)

    def execute(self, context):
        if bpy is None or context is None:
            self.report({"ERROR"}, "blender_context_missing")
            return {"CANCELLED"}
        if getattr(context, "mode", "OBJECT") != "OBJECT":
            self.report({"WARNING"}, "Object Mode is required to update selected objects.")
            return {"CANCELLED"}

        unavailable = _manual_action_unavailable_reason(
            context,
            action="updating selected objects",
            require_ready=True,
            allow_rigged_owner=True,
        )
        if unavailable:
            self.report({"WARNING"}, unavailable)
            return {"CANCELLED"}

        selected_count = _selected_count_for_bulk(context)
        if should_use_bulk_mode(selected_count):
            ok, reason = _start_bulk_update_job(context)
            if ok:
                self.report({"INFO"}, f"bulk_update_started objects={selected_count}")
                return {"FINISHED"}
            level = {"WARNING"} if _is_expected_precondition_failure(reason) else {"ERROR"}
            self.report(level, reason or "bulk_update_start_failed")
            return {"CANCELLED"}

        bulk_state = begin_job("update_selected_active", selected_count)
        result_dict = sync_selected_objects_once()
        ok = bool(result_dict.get("ok"))
        reason = str(result_dict.get("reason") or ("update_selected_ok" if ok else "update_selected_failed"))
        selected = int(result_dict.get("selectedCount", 0) or 0)
        synced = int(result_dict.get("synced", 0) or 0)
        object_state_synced = int(result_dict.get("objectStateSynced", 0) or 0)
        mesh_synced = int(result_dict.get("meshSynced", 0) or 0)
        rigged_synced = int(result_dict.get("riggedSynced", 0) or 0)
        skipped_not_ready = int(result_dict.get("skippedNotReady", 0) or 0)
        skipped_unsupported = int(result_dict.get("skippedUnsupportedType", 0) or 0)
        skipped_no_context = int(result_dict.get("skippedNoContext", 0) or 0)
        errors = int(result_dict.get("errors", 0) or 0)
        expected_noop = reason == "sync_selected_noop" and errors == 0
        display_ok = ok or expected_noop
        finish_job(ok=display_ok, processed_objects=selected or int(bulk_state.get("totalObjects") or 0), message=reason)

        set_last_send_result(
            SendResult(
                ok=display_ok,
                message=reason,
                error=None if display_ok else reason,
                payload_size=0,
                last_package_id=None,
                last_resource_count=0,
                failure_category=None if display_ok else "scene_sync_update_selected",
            ),
            {
                "updateIntent": "scene_sync_update_selected",
                "triggerType": "update_selected_preview_commit",
                "selectedObjectCount": selected,
                "objectStateCount": object_state_synced,
                "meshPreviewCount": mesh_synced,
                "riggedUpdateCount": rigged_synced,
                "skippedNotReady": skipped_not_ready,
                "skippedUnsupportedType": skipped_unsupported,
                "skippedNoContext": skipped_no_context,
                "errors": errors,
            },
        )

        if ok:
            level = {"WARNING"} if (
                skipped_not_ready > 0 or skipped_unsupported > 0 or skipped_no_context > 0
            ) else {"INFO"}
            self.report(
                level,
                (
                    f"update_selected_ok selected={selected} synced={synced} "
                    f"objectState={object_state_synced} meshPreviews={mesh_synced} rigged={rigged_synced}"
                ),
            )
            return {"FINISHED"}

        if expected_noop:
            self.report(
                {"WARNING"},
                (
                    f"update_selected_noop selected={selected} "
                    f"skippedNotReady={skipped_not_ready} skippedUnsupported={skipped_unsupported}"
                ),
            )
            return {"FINISHED"}

        self.report(
            {"ERROR"},
            (
                f"{reason} selected={selected} synced={synced} "
                f"skippedNotReady={skipped_not_ready} skippedUnsupported={skipped_unsupported} "
                f"skippedNoContext={skipped_no_context} errors={errors}"
            ),
        )
        return {"CANCELLED"}


class BS_OT_CancelBulkJob(OperatorBase):
    bl_idname = "blendersync.cancel_bulk_job"
    bl_label = "Cancel Bulk Job"
    bl_description = "Request cancellation for the active bulk job. Current synchronous operations finish before cancellation can take effect."

    def execute(self, context):
        state = request_cancel()
        self.report({"INFO"}, str(state.get("message") or "cancel_requested"))
        return {"FINISHED"}


class BS_OT_DismissBulkJob(OperatorBase):
    bl_idname = "blendersync.dismiss_bulk_job"
    bl_label = "Dismiss Bulk Job"
    bl_description = "Hide the completed, cancelled, or failed bulk job summary."

    @classmethod
    def poll(cls, context):
        status = str(get_bulk_job_state().get("status") or "idle")
        return status not in {"running", "cancel_requested", "idle"}

    def execute(self, context):
        return {"FINISHED"} if dismiss_job() else {"CANCELLED"}


class BS_OT_SessionConnect(OperatorBase):
    bl_idname = "blendersync.session_connect"
    bl_label = "Connect Session"

    def execute(self, context):
        endpoint = build_local_ws_endpoint(get_session_port(context))
        ok = connect_minimal_ws_runtime(endpoint)
        self.report({"INFO" if ok else "ERROR"}, "waiting_for_unity" if ok else "connect_failed")
        return {"FINISHED"} if ok else {"CANCELLED"}


class BS_OT_SessionDisconnect(OperatorBase):
    bl_idname = "blendersync.session_disconnect"
    bl_label = "Disconnect Session"

    def execute(self, context):
        disconnect_minimal_ws_runtime()
        self.report({"INFO"}, "disconnected")
        return {"FINISHED"}


class BS_OT_CopyDiagnostics(OperatorBase):
    bl_idname = "blendersync.copy_diagnostics"
    bl_label = "Copy Diagnostics"
    bl_description = "Copy a sanitized TriSync session snapshot to the clipboard."

    def execute(self, context):
        window_manager = getattr(context, "window_manager", None) if context is not None else None
        if window_manager is None:
            self.report({"WARNING"}, "Window manager unavailable")
            return {"CANCELLED"}
        try:
            window_manager.clipboard = get_copy_diagnostics_json()
        except Exception as exc:
            _report_unexpected_operator_exception("operator:copy_diagnostics", exc)
            self.report({"ERROR"}, "Could not copy diagnostics")
            return {"CANCELLED"}
        self.report({"INFO"}, "Diagnostics copied")
        return {"FINISHED"}


class BS_OT_ClearRecentActivity(OperatorBase):
    bl_idname = "blendersync.clear_recent_activity"
    bl_label = "Clear Recent Activity"
    bl_description = "Clear recent TriSync operation reports and diagnostic logs"

    def execute(self, context):
        clear_diagnostics_activity()
        from blender.ui.session_panel import clear_recent_activity_expansion

        clear_recent_activity_expansion()
        return {"FINISHED"}


class BS_OT_ToggleRecentActivityEntry(OperatorBase):
    bl_idname = "blendersync.toggle_recent_activity_entry"
    bl_label = "Toggle Recent Activity Entry"
    bl_options = {"INTERNAL"}

    if bpy is not None:
        __annotations__ = {
            "entry_id": bpy.props.StringProperty(options={"HIDDEN"}),
        }
    else:
        entry_id = ""

    def execute(self, context):
        from blender.ui.session_panel import toggle_recent_activity_entry

        toggle_recent_activity_entry(self.entry_id)
        return {"FINISHED"}


class BS_OT_ResetPerformanceTuning(OperatorBase):
    bl_idname = "blendersync.reset_performance_tuning"
    bl_label = "Reset Performance Settings"
    bl_description = "Restore all seven TriSync performance tuning values to their defaults"

    @classmethod
    def poll(cls, context):
        return get_addon_preferences(context) is not None

    def execute(self, context):
        if not reset_performance_tuning(context):
            self.report({"WARNING"}, "TriSync preferences unavailable")
            return {"CANCELLED"}
        self.report({"INFO"}, "Performance settings reset")
        return {"FINISHED"}


def _is_mesh_asset_known(raw_asset_id: str | None) -> bool:
    if not isinstance(raw_asset_id, str) or not raw_asset_id.strip():
        return False
    raw_asset_id = raw_asset_id.strip()
    return is_asset_known(raw_asset_id) or is_asset_known(f"mesh-{raw_asset_id}")


def _can_link_selected_to_active_mesh(context) -> bool:
    if context is None:
        return False
    active = getattr(context, "active_object", None)
    if (
        active is None
        or getattr(active, "type", None) != "MESH"
        or getattr(active, "data", None) is None
    ):
        return False
    selected = list(getattr(context, "selected_objects", []) or [])
    return any(
        obj is not None
        and obj is not active
        and getattr(obj, "type", None) == "MESH"
        and getattr(obj, "data", None) is not None
        for obj in selected
    )


class BS_OT_LinkSelectedToActiveMesh(OperatorBase):
    bl_idname = "blendersync.link_selected_to_active_mesh"
    bl_label = "Reuse Active Mesh Asset"
    bl_description = (
        "Pre-import utility: selected unsent Mesh objects will reuse the active object's Mesh assetId "
        "when imported to Unity. This does not merge Unity assets or share Blender mesh datablocks."
    )

    @classmethod
    def poll(cls, context):
        return _can_link_selected_to_active_mesh(context)

    def execute(self, context):
        if bpy is None or context is None:
            self.report({"ERROR"}, "blender_context_missing")
            return {"CANCELLED"}

        active = getattr(context, "active_object", None)
        if active is None or getattr(active, "type", None) != "MESH" or getattr(active, "data", None) is None:
            self.report({"ERROR"}, "active_object_not_mesh")
            return {"CANCELLED"}

        selected_objects = list(getattr(context, "selected_objects", []) or [])
        mesh_objects = []
        skipped_non_mesh = 0
        for obj in selected_objects:
            if obj is None:
                continue
            if getattr(obj, "type", None) != "MESH" or getattr(obj, "data", None) is None:
                skipped_non_mesh += 1
                continue
            mesh_objects.append(obj)

        if not mesh_objects:
            self.report({"ERROR"}, "no_mesh_objects_selected")
            return {"CANCELLED"}

        active_existing_asset_id = get_mesh_asset_id_for_object(active)
        active_asset_id = ensure_mesh_asset_id_for_object(active)
        active_sent = _is_mesh_asset_known(active_asset_id)

        sent_objects = []
        unsent_objects = []
        sent_asset_ids = set()
        for obj in mesh_objects:
            asset_id = get_mesh_asset_id_for_object(obj)
            sent = _is_mesh_asset_known(asset_id)
            if sent:
                sent_objects.append(obj)
                if asset_id:
                    sent_asset_ids.add(str(asset_id).strip())
            else:
                unsent_objects.append(obj)

        if sent_objects and not unsent_objects:
            self.report(
                {"INFO"},
                (
                    "mesh_reuse_noop all_selected_meshes_already_imported; "
                    "this tool does not merge existing Unity mesh assets"
                ),
            )
            return {"FINISHED"}

        if sent_objects and not active_sent:
            self.report(
                {"WARNING"},
                (
                    "mesh_reuse_noop mixed_import_state; make the already imported source mesh object active "
                    "before reusing its Mesh resource ID"
                ),
            )
            return {"CANCELLED"}

        changed = 0
        already_matching = 0
        for obj in unsent_objects:
            mesh = getattr(obj, "data", None)
            if mesh is None:
                continue
            current_asset_id = get_mesh_asset_id_for_object(obj)
            if str(current_asset_id or "") == str(active_asset_id):
                already_matching += 1
                continue
            set_mesh_asset_id_for_object(obj, active_asset_id)
            changed += 1

        if changed > 0:
            mode = "reuse_sent_active" if active_sent else "preimport_shared"
            self.report(
                {"INFO"},
                (
                    f"mesh_reuse_ok mode={mode} changed_unsent={changed} "
                    f"already_matching={already_matching} skipped_sent={len(sent_objects)} "
                    f"skipped_non_mesh={skipped_non_mesh}"
                ),
            )
            return {"FINISHED"}

        created_active_id = active_existing_asset_id is None and bool(active_asset_id)
        self.report(
            {"INFO"},
            (
                f"mesh_reuse_noop changed_unsent=0 already_matching={already_matching} "
                f"skipped_sent={len(sent_objects)} skipped_non_mesh={skipped_non_mesh} "
                f"active_id_created={created_active_id}"
            ),
        )
        return {"FINISHED"}


class BS_OT_ManualPreviewActiveUv(OperatorBase):
    bl_idname = "blendersync.manual_preview_active_uv"
    bl_label = "Preview UV In Unity"
    bl_description = "Manually snapshot active edit-mode UVs once and preview them in Unity. Does not run in background."

    def execute(self, context):
        result = manual_preview_active_uv_once()
        ok = bool(result.get("ok"))
        changed = result.get("changed")
        reason = str(result.get("reason") or ("sent" if ok else "manual_uv_preview_failed"))
        if ok and changed is False:
            self.report({"INFO"}, "uv_preview_no_change")
            return {"FINISHED"}
        if ok:
            self.report({"INFO"}, f"uv_preview_sent verts={int(result.get('exportVertexCount') or 0)}")
            return {"FINISHED"}
        self.report({"ERROR"}, reason)
        return {"CANCELLED"}


def _can_commit_selected_previews(context) -> bool:
    if context is None:
        return False
    try:
        truth = get_session().get_truth_state()
        if not bool(truth.get("handshake_confirmed")):
            return False
    except Exception:
        return False

    selected = list(getattr(context, "selected_objects", []) or [])
    return any(
        obj is not None
        and getattr(obj, "type", None) == "MESH"
        and bool(obj.get(AUTO_SYNC_READY_KEY, False))
        for obj in selected
    )


class BS_OT_ManualCommitSelectedPreviews(OperatorBase):
    bl_idname = "blendersync.manual_commit_selected_previews"
    bl_label = "Commit Preview Now"
    bl_description = "Ask Unity to immediately commit preview meshes for selected synced mesh objects."

    @classmethod
    def poll(cls, context):
        return _can_commit_selected_previews(context)

    def execute(self, context):
        result = manual_commit_selected_previews_once()
        ok = bool(result.get("ok"))
        sent = int(result.get("sent") or 0)
        failed = int(result.get("failed") or 0)
        skipped_not_ready = int(result.get("skippedNotReady") or 0)
        skipped_not_mesh = int(result.get("skippedNotMesh") or 0)
        reason = str(result.get("reason") or ("manual_preview_commit_sent" if ok else "manual_preview_commit_failed"))
        if ok:
            self.report({"INFO"}, f"preview_commit_sent objects={sent}")
            return {"FINISHED"}
        if sent > 0:
            self.report({"WARNING"}, f"preview_commit_partial sent={sent} failed={failed}")
            return {"FINISHED"}
        self.report(
            {"INFO"},
            f"{reason} skippedNotReady={skipped_not_ready} skippedNotMesh={skipped_not_mesh}",
        )
        return {"FINISHED"}


def _can_export_animation_clip(context) -> bool:
    if context is None or getattr(context, "active_object", None) is None:
        return False
    try:
        return bool(get_session().get_truth_state().get("handshake_confirmed"))
    except Exception:
        return False


class BS_OT_ImportAnimationClipFbxLikeV0(OperatorBase):
    bl_idname = "blendersync.import_animation_clip"
    bl_label = "Export Animation Clip to Unity"
    bl_description = "Export the active object's animation as a Unity AnimationClip."

    @classmethod
    def poll(cls, context):
        return _can_export_animation_clip(context)

    def execute(self, context):
        result = send_active_object_animation_clip_once(context, get_session())
        if result.get("ok"):
            report = result.get("exportReport") or {}
            self.report(
                {"INFO"},
                (
                    f"animation_clip_send_ok clip={result.get('clipName')} "
                    f"tracks={int(result.get('trackCount', 0) or 0)} "
                    f"keys={int(report.get('keyCount', 0) or 0)} "
                    f"samples={int(report.get('sampleCount', 0) or 0)} "
                    f"fps={float(report.get('frameRate', 0.0) or 0.0):.2f} "
                    f"semantic={result.get('channelSemantic') or 'auto'}"
                ),
            )
            return {"FINISHED"}

        self.report({"ERROR"}, result.get("message") or result.get("reason") or "animation_clip_send_failed")
        return {"CANCELLED"}


def _resolve_active_rigged_pose_armature(context, *, require_active_armature: bool):
    active = getattr(context, "active_object", None) if context is not None else None
    if active is None:
        return None
    if getattr(active, "type", None) == "ARMATURE" and getattr(active, "data", None) is not None:
        return active
    if require_active_armature:
        return None
    find_armature = getattr(active, "find_armature", None)
    try:
        armature = find_armature() if callable(find_armature) else None
    except Exception:
        armature = None
    if armature is None or getattr(armature, "type", None) != "ARMATURE":
        return None
    return armature if getattr(armature, "data", None) is not None else None


def _rigged_pose_handshake_confirmed() -> bool:
    try:
        return bool(get_session().get_truth_state().get("handshake_confirmed"))
    except Exception:
        return False


def _can_run_rigged_pose_operator(
    context,
    *,
    allowed_modes: frozenset[str],
    require_active_armature: bool,
) -> bool:
    if context is None or str(getattr(context, "mode", "") or "") not in allowed_modes:
        return False
    armature = _resolve_active_rigged_pose_armature(
        context,
        require_active_armature=require_active_armature,
    )
    if armature is None or not bool(armature.get(AUTO_SYNC_READY_KEY, False)):
        return False
    return _rigged_pose_handshake_confirmed()


class _RiggedPoseSyncMixin:
    pose_mode = "current_pose"
    success_label = "rigged_pose_sync_ok"
    allowed_context_modes = frozenset({"OBJECT"})
    require_active_armature = False
    mode_error = "Rigged pose operation requires Object Mode."

    @classmethod
    def poll(cls, context):
        return _can_run_rigged_pose_operator(
            context,
            allowed_modes=cls.allowed_context_modes,
            require_active_armature=cls.require_active_armature,
        )

    def execute(self, context):
        if bpy is None or context is None:
            self.report({"ERROR"}, "blender_context_missing")
            return {"CANCELLED"}
        if str(getattr(context, "mode", "") or "") not in self.allowed_context_modes:
            self.report({"ERROR"}, self.mode_error)
            return {"CANCELLED"}

        armature = _resolve_active_rigged_pose_armature(
            context,
            require_active_armature=self.require_active_armature,
        )
        if armature is None:
            self.report({"ERROR"}, "active_rig_missing")
            return {"CANCELLED"}
        if not bool(armature.get(AUTO_SYNC_READY_KEY, False)):
            self.report({"ERROR"}, "active_rig_not_imported")
            return {"CANCELLED"}
        if not _rigged_pose_handshake_confirmed():
            self.report({"ERROR"}, "session_not_connected")
            return {"CANCELLED"}

        try:
            payload = build_unity_rig_v1_pose_sync_payload(context, armature, mode=self.pose_mode)
            if not payload:
                self.report({"ERROR"}, "rigged_pose_payload_empty")
                return {"CANCELLED"}
            send_context = {
                "session": get_session(),
                "riggedPosePayload": payload,
                "updateIntent": "rigged_pose_sync",
                "triggerType": f"manual_{self.pose_mode}",
                "selectedObjectCount": len(list(getattr(context, "selected_objects", []) or [])),
                "supportedSelectedObjectCount": 1,
                "unsupportedSelectedObjectCount": 0,
            }
        except Exception as exc:
            _report_unexpected_operator_exception("operator:sync_rigged_pose", exc)
            self.report({"ERROR"}, str(exc) or "rigged_pose_context_failed")
            return {"CANCELLED"}

        result = send_rigged_pose_sync(send_context)
        _set_last_result(result, send_context)
        if result.ok:
            self.report({"INFO"}, f"{self.success_label} bones={len(payload.get('bones') or [])}")
            return {"FINISHED"}

        self.report({"ERROR"}, result.error or "rigged_pose_sync_failed")
        return {"CANCELLED"}


class BS_OT_SyncCurrentRiggedPose(_RiggedPoseSyncMixin, OperatorBase):
    bl_idname = "blendersync.sync_current_rigged_pose"
    bl_label = "Sync Pose to Unity"
    bl_description = "Sync the active Armature's evaluated Pose Mode transforms to Unity. Unity Animation recording can key the resulting changes."
    pose_mode = "current_pose"
    success_label = "rigged_pose_sync_ok"
    allowed_context_modes = frozenset({"POSE"})
    require_active_armature = True
    mode_error = "Current pose sync requires Pose Mode."


class BS_OT_SyncRiggedBlendShapeWeights(OperatorBase):
    bl_idname = "blendersync.sync_rigged_blendshape_weights"
    bl_label = "Sync Shape Keys to Unity"
    bl_description = (
        "Send the active rig's current bound skin Shape Key weights to Unity without rebuilding meshes. "
        "Unity Animation recording can key the resulting BlendShape changes."
    )

    @classmethod
    def poll(cls, context):
        return _can_run_rigged_pose_operator(
            context,
            allowed_modes=frozenset({"OBJECT", "POSE"}),
            require_active_armature=False,
        )

    def execute(self, context):
        if bpy is None or context is None:
            self.report({"ERROR"}, "blender_context_missing")
            return {"CANCELLED"}
        if str(getattr(context, "mode", "") or "") not in {"OBJECT", "POSE"}:
            self.report({"ERROR"}, "Shape Key sync requires Object or Pose Mode.")
            return {"CANCELLED"}

        armature = _resolve_active_rigged_pose_armature(context, require_active_armature=False)
        if armature is None:
            self.report({"ERROR"}, "active_rig_missing")
            return {"CANCELLED"}
        if not bool(armature.get(AUTO_SYNC_READY_KEY, False)):
            self.report({"ERROR"}, "active_rig_not_imported")
            return {"CANCELLED"}
        if not _rigged_pose_handshake_confirmed():
            self.report({"ERROR"}, "session_not_connected")
            return {"CANCELLED"}

        try:
            payload = build_unity_rig_v1_blendshape_weights_payload(context, armature)
            if not payload:
                self.report({"ERROR"}, "rigged_shape_keys_missing")
                return {"CANCELLED"}
            send_context = {
                "session": get_session(),
                "riggedBlendShapeWeightsPayload": payload,
                "updateIntent": "rigged_blendshape_weights_sync",
                "triggerType": "manual_rigged_blendshape_weights",
                "selectedObjectCount": len(list(getattr(context, "selected_objects", []) or [])),
                "supportedSelectedObjectCount": 1,
                "unsupportedSelectedObjectCount": 0,
            }
        except Exception as exc:
            _report_unexpected_operator_exception("operator:sync_rigged_blendshape_weights", exc)
            self.report({"ERROR"}, str(exc) or "rigged_shape_keys_context_failed")
            return {"CANCELLED"}

        result = send_rigged_blendshape_weights_sync(send_context)
        _set_last_result(result, send_context)
        if result.ok:
            parts = payload.get("parts") or []
            weights = sum(len(part.get("weights") or []) for part in parts)
            self.report({"INFO"}, f"rigged_shape_keys_sync_ok parts={len(parts)} weights={weights}")
            return {"FINISHED"}

        self.report({"ERROR"}, result.error or "rigged_shape_keys_sync_failed")
        return {"CANCELLED"}


class BS_OT_RestoreStaticRiggedPose(_RiggedPoseSyncMixin, OperatorBase):
    bl_idname = "blendersync.restore_static_rigged_pose"
    bl_label = "Restore Imported Pose"
    bl_description = "Restore the active rig's Unity instance to its imported pose without changing Blender."
    pose_mode = "restore_static_pose"
    success_label = "rigged_pose_restore_ok"
    allowed_context_modes = frozenset({"OBJECT", "POSE"})
    require_active_armature = False
    mode_error = "Imported pose restore requires Object or Pose Mode."


def _resolve_active_material(context):
    if context is None:
        return None

    space = getattr(context, "space_data", None)
    if (
        space is not None
        and getattr(space, "type", "") == "NODE_EDITOR"
        and getattr(space, "tree_type", "") == "ShaderNodeTree"
    ):
        for attr in ("pin_id", "id"):
            pinned = getattr(space, attr, None)
            if pinned is not None and hasattr(pinned, "use_nodes"):
                return pinned

    material = getattr(context, "material", None)
    if material is not None and hasattr(material, "use_nodes"):
        return material

    active = getattr(context, "active_object", None)
    return getattr(active, "active_material", None) if active is not None else None


def _can_send_active_material(context) -> bool:
    if _resolve_active_material(context) is None:
        return False
    try:
        return bool(get_session().get_truth_state().get("handshake_confirmed"))
    except Exception:
        return False


class _MaterialOperatorMixin:
    def _resolve_material(self, context):
        if bpy is None:
            return None
        return _resolve_active_material(context)


def _material_content_v1_warning_summary(payload: dict, max_items: int = 2) -> str:
    warnings = payload.get("warnings") or []
    if not warnings:
        return ""
    items = []
    for warning in warnings[:max_items]:
        code = str(warning.get("code") or "warning")
        slot = warning.get("slot")
        image_name = warning.get("imageName")
        label = code
        if slot:
            label += f"/{slot}"
        if image_name:
            label += f":{image_name}"
        items.append(label)
    remaining = len(warnings) - len(items)
    if remaining > 0:
        items.append(f"+{remaining} more")
    return "; ".join(items)


class BS_OT_BuildActiveMaterialContentV1(_MaterialOperatorMixin, OperatorBase):
    bl_idname = "blendersync.build_active_material_content_v1"
    bl_label = "Build Active Material Content V1"
    bl_options = {"INTERNAL"}

    def execute(self, context):
        material = self._resolve_material(context)
        if material is None:
            self.report({"ERROR"}, "material_missing")
            return {"CANCELLED"}

        try:
            payload = write_material_content_v1_debug_json(material)
        except Exception as exc:
            _report_unexpected_operator_exception("operator:preview_material_content", exc)
            self.report({"ERROR"}, str(exc) or "material_content_v1_build_failed")
            return {"CANCELLED"}

        textures = payload.get("textures") or {}
        texture_count = sum(1 for value in textures.values() if value)
        warning_count = len(payload.get("warnings") or [])
        trace(
            "MaterialContent",
            "debug_payload_built",
            lambda: "Built a material debug payload.",
            lambda: {
                "materialName": payload.get("source", {}).get("name"),
                "textureCount": texture_count,
                "warningCount": warning_count,
            },
        )
        debug_path = get_material_content_v1_debug_path()
        self.report(
            {"INFO"},
            f"material_content_v1_built material={payload.get('source', {}).get('name')} textures={texture_count} warnings={warning_count} path={debug_path}",
        )
        return {"FINISHED"}


class BS_OT_SendActiveMaterialContentV1(_MaterialOperatorMixin, OperatorBase):
    bl_idname = "blendersync.send_active_material_content_v1"
    bl_label = "Sync Material to Unity"
    bl_description = "Sync the active material's current properties and textures to Unity."

    @classmethod
    def poll(cls, context):
        return _can_send_active_material(context)

    def execute(self, context):
        material = self._resolve_material(context)
        if material is None:
            self.report({"ERROR"}, "material_missing")
            return {"CANCELLED"}

        try:
            payload = build_material_content_v1(material)
            send_context = {
                "session": get_session(),
                "materialContentV1Payload": payload,
                "updateIntent": INTENT_MATERIAL_UPDATE,
                "triggerType": "manual_material_content_v1",
                "resourceCountEstimate": 1,
            }
        except Exception as exc:
            _report_unexpected_operator_exception("operator:send_material_content", exc)
            self.report({"ERROR"}, str(exc) or "material_content_v1_context_failed")
            return {"CANCELLED"}

        result = send_material_content_v1(send_context)
        _set_last_result(result, send_context)
        textures = payload.get("textures") or {}
        texture_count = sum(1 for value in textures.values() if value)
        warning_count = len(payload.get("warnings") or [])
        if result.ok:
            warning_summary = _material_content_v1_warning_summary(payload)
            summary = f"material_content_v1_send_ok textures={texture_count} warnings={warning_count}"
            if warning_summary:
                summary += f" warning={warning_summary}"
            self.report({"WARNING" if warning_count else "INFO"}, summary)
        else:
            self.report({"ERROR"}, result.error or "material_content_v1_send_failed")
        return {"FINISHED"} if result.ok else {"CANCELLED"}


CLASSES = (
    BS_OT_SendSelectedResources,
    BS_OT_SyncSelectedObjectState,
    BS_OT_SyncSceneViewToUnity,
    BS_OT_SyncSelectedObjects,
    BS_OT_CancelBulkJob,
    BS_OT_DismissBulkJob,
    BS_OT_SessionConnect,
    BS_OT_SessionDisconnect,
    BS_OT_CopyDiagnostics,
    BS_OT_ClearRecentActivity,
    BS_OT_ToggleRecentActivityEntry,
    BS_OT_ResetPerformanceTuning,
    BS_OT_LinkSelectedToActiveMesh,
    BS_OT_ManualPreviewActiveUv,
    BS_OT_ManualCommitSelectedPreviews,
    BS_OT_ImportAnimationClipFbxLikeV0,
    BS_OT_SyncCurrentRiggedPose,
    BS_OT_SyncRiggedBlendShapeWeights,
    BS_OT_RestoreStaticRiggedPose,
    BS_OT_BuildActiveMaterialContentV1,
    BS_OT_SendActiveMaterialContentV1,
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


def unregister() -> None:
    if bpy is None:
        return
    for cls in reversed(CLASSES):
        _safe_unregister_class(cls)
