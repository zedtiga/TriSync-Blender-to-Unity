"""
Mode-aware AutoSync controller.

This module owns Blender-side timer/depsgraph driven synchronization: object
state, lifecycle add/remove, preview mesh sends,
material slot/reference updates, blendshape weights, and scene-view state.

"""

from __future__ import annotations

from collections import defaultdict
import hashlib
import time
from array import array
from pathlib import Path

from blender.asset_registry import (
    get_asset_source_fingerprint,
    is_asset_known,
    mark_asset_source_fingerprints,
    mark_assets_known,
)
from blender.common.exception_boundary import report_boundary_exception
from blender.common.evaluated_mesh import evaluated_mesh_build_in_progress
from blender.common.log import exception as log_exception, trace, warn
from blender.identity import (
    ensure_instance_id,
    ensure_mesh_asset_id_for_object,
    ensure_unique_asset_id,
    get_instance_id,
    MESH_REF_COUNT_KEY,
    MESH_REF_SHARED_KEY,
    MESH_REF_USAGE_UPDATED_AT_KEY,
    set_mesh_asset_id_for_object,
)
from blender.material_resource.slots import has_visible_geometry_nodes_modifier, material_refs_from_live_context
from blender.resource_update.binary_v1 import build_mesh_binary_buffers_from_content
from blender.resource_update.entrypoints import send_mesh_update_once
from blender.resource_update.fingerprint import compute_mesh_content_fingerprint
from blender.object_classification import summarize_unsupported
from blender.scene_sync.baseline import (
    capture_object_baselines,
    COLOR_ATTRIBUTES_SIGNATURE_PROP,
    get_mesh_content_fingerprint_baseline,
    get_mesh_content_fingerprint_no_uv_baseline,
    get_color_attributes_baseline,
    get_material_slots_baseline,
    get_modifier_stack_baseline,
    get_uv_channels_baseline,
    mesh_payload_content_fingerprints,
    material_slots_signature_from_refs,
    modifier_stack_signature,
    set_material_slots_baseline,
    set_mesh_content_fingerprint_baseline,
    set_modifier_stack_baseline,
    uv_channels_signature,
)
from blender.scene_sync import object_lifecycle
from blender.scene_sync import material_sync
from blender.scene_sync import mesh_context
from blender.scene_sync import object_state
from blender.scene_sync import preview_sync
from blender.scene_sync import structure_watch
from blender.scene_sync.settings import (
    get_evaluated_mesh_preview_debounce_ms,
    get_lifecycle_reconcile_hz,
    get_mesh_update_sync_hz,
    get_object_state_sync_hz,
    get_preview_idle_commit_seconds,
    get_view_sync_hz,
    get_view_sync_scale,
)
from blender.scene_sync.object_lifecycle import LifecycleHooks
from blender.scene_sync.object_state import ObjectStateHooks
from blender.scene_sync.material_sync import MaterialSyncHooks
from blender.scene_sync.mesh_context import MeshContextHooks
from blender.scene_sync.preview_sync import PreviewSyncHooks
from blender.scene_sync.structure_watch import StructureWatchHooks
from blender.scene_sync.runtime_state import (
    EvaluatedPreviewRuntimeState,
    GeometryPreviewRuntimeState,
    LifecycleRuntimeState,
    MaterialRuntimeState,
    MeshStructureRuntimeState,
    ObjectStateRuntimeState,
    PreviewMappingRuntimeState,
    ShapeKeyRuntimeState,
    UvPreviewRuntimeState,
)
from blender.transport.entrypoints import send_object_state_update, send_scene_view_state, send_selected_resources
from blender.ui.object_context_builders import (
    build_object_state_payload,
    build_selected_live_context,
    build_single_object_live_context,
    collect_meshes_managed_by_armatures,
    collect_supported_sync_objects,
    object_identity_key,
)
from blender.ui.view_context_builders import build_scene_view_state_context
from blender.ui.state_view import get_session

try:
    import bpy  # type: ignore
    from bpy.app.handlers import persistent  # type: ignore
except ImportError:
    bpy = None
    def persistent(fn):
        return fn

try:
    import bmesh  # type: ignore
except ImportError:
    bmesh = None

# --- minimal controller state ---

AUTO_SYNC_READY_KEY = object_lifecycle.AUTO_SYNC_READY_KEY
_last_tick_time: float = 0.0
_last_mesh_tick_time: float = 0.0
_last_lifecycle_tick_time: float = 0.0
_last_view_tick_time: float = 0.0
_last_view_fingerprint: str | None = None
_last_mode: str = "UNKNOWN"
_timer_registered: bool = False
_last_sync_enabled: bool = False
_last_auto_sync_state_signature: str | None = None


def _mesh_root_asset_id_and_fingerprint(root: dict) -> tuple[str | None, str | None]:
    if not isinstance(root, dict):
        return None, None
    metadata = root.get("metadata") or {}
    if not isinstance(metadata, dict):
        return None, None
    asset_id = str(metadata.get("assetId") or "").strip()
    fingerprint = str(metadata.get("sourceFingerprint") or "").strip()
    return asset_id or None, fingerprint or None


def _filter_rigged_update_changed_mesh_roots(context: dict) -> dict[str, int]:
    roots = list((context or {}).get("selected") or [])
    kept = []
    skipped = 0
    missing_fingerprint = 0
    changed_records: dict[str, str] = {}

    for root in roots:
        asset_id, fingerprint = _mesh_root_asset_id_and_fingerprint(root)
        if not asset_id or not fingerprint:
            kept.append(root)
            missing_fingerprint += 1
            continue
        previous = get_asset_source_fingerprint(asset_id)
        if previous == fingerprint:
            skipped += 1
            continue
        kept.append(root)
        changed_records[asset_id] = fingerprint

    context["selected"] = kept
    context["_riggedChangedMeshFingerprints"] = changed_records
    return {
        "kept": len(kept),
        "skipped": skipped,
        "missingFingerprint": missing_fingerprint,
        "total": len(roots),
    }


def _mark_sent_mesh_root_fingerprints(context: dict) -> None:
    records = dict((context or {}).get("_riggedChangedMeshFingerprints") or {})
    if not records:
        return
    mark_asset_source_fingerprints(records)


def _keep_only_unknown_material_contents_for_rigged_update(context: dict) -> None:
    material_contents = list((context or {}).get("materialContents") or [])
    kept = []
    seen_refs: set[str] = set()
    for payload in material_contents:
        if not isinstance(payload, dict):
            continue
        material_ref = str(payload.get("materialRef") or "").strip()
        if not material_ref or material_ref in seen_refs:
            continue
        seen_refs.add(material_ref)
        if is_asset_known(material_ref):
            continue
        kept.append(payload)

    context["materialContents"] = kept
    context["sendMaterialContents"] = bool(kept)
    if material_contents or kept:
        trace(
            "SceneSync",
            "rigged_materials_filtered",
            lambda: "Filtered known materials from a rigged-object update.",
            lambda: {
                "totalCount": len(material_contents),
                "unknownCount": len(kept),
                "knownCount": max(0, len(material_contents) - len(kept)),
            },
        )

LIFECYCLE_MAX_PER_TICK = object_lifecycle.LIFECYCLE_MAX_PER_TICK
LIFECYCLE_BACKOFF_SECONDS = object_lifecycle.LIFECYCLE_BACKOFF_SECONDS
LIFECYCLE_PHASE_PENDING_BOOTSTRAP = object_lifecycle.LIFECYCLE_PHASE_PENDING_BOOTSTRAP
LIFECYCLE_PHASE_ACTIVE = object_lifecycle.LIFECYCLE_PHASE_ACTIVE
LIFECYCLE_PHASE_PENDING_REMOVE = object_lifecycle.LIFECYCLE_PHASE_PENDING_REMOVE
LIFECYCLE_PHASE_REMOVED = object_lifecycle.LIFECYCLE_PHASE_REMOVED

_lifecycle_runtime = LifecycleRuntimeState()
_preview_mapping_runtime = PreviewMappingRuntimeState()
_dirty_probe_registered: bool = False
_dirty_probe_counts_by_id: dict[str, int] = defaultdict(int)
_dirty_probe_last_log_by_id: dict[str, float] = {}
_dirty_probe_last_seen_by_id: dict[str, float] = {}
_dirty_probe_last_summary_time: float = 0.0
_background_pump_error_last_at_by_signature: dict[str, float] = {}
_geometry_preview_runtime = GeometryPreviewRuntimeState()
_evaluated_preview_runtime = EvaluatedPreviewRuntimeState()
_mesh_structure_runtime = MeshStructureRuntimeState()
_shape_key_runtime = ShapeKeyRuntimeState()
_object_state_runtime = ObjectStateRuntimeState()
BLENDSHAPE_WEIGHT_SYNC_INTERVAL_SECONDS = 0.10
OBJECT_STATE_MOTION_BURST_SECONDS = object_state.OBJECT_STATE_MOTION_BURST_SECONDS
OBJECT_STATE_MOTION_BURST_HZ = object_state.OBJECT_STATE_MOTION_BURST_HZ
# Disabled by default. Keep the code path as an emergency switch for future
# depsgraph miss debugging; set to 2.0 to restore the previous safety cadence.
OBJECT_STATE_ACTIVE_FALLBACK_HZ = object_state.OBJECT_STATE_ACTIVE_FALLBACK_HZ
DIRTY_PROBE_VERBOSE = False
AUTOPREVIEW_UV_VERBOSE = False
_uv_preview_runtime = UvPreviewRuntimeState()
_material_runtime = MaterialRuntimeState()
_MESH_EDIT_FAMILY_MODES = {"EDIT_MESH", "SCULPT"}
MATERIAL_AUTO_SEND_MAX_PER_TICK = material_sync.MATERIAL_AUTO_SEND_MAX_PER_TICK
MATERIAL_AUTO_SEND_RETRY_SECONDS = material_sync.MATERIAL_AUTO_SEND_RETRY_SECONDS
MATERIAL_REF_RESEND_DELAYS_SECONDS = material_sync.MATERIAL_REF_RESEND_DELAYS_SECONDS
MATERIAL_REF_RESEND_MAX_PER_TICK = material_sync.MATERIAL_REF_RESEND_MAX_PER_TICK
PREVIEW_BUFFER_TTL_SECONDS = 24 * 60 * 60
PREVIEW_BUFFER_CLEANUP_INTERVAL_SECONDS = 60 * 60
UV_CHANNELS_SIGNATURE_POLL_SECONDS = 0.25
COLOR_ATTRIBUTES_SIGNATURE_POLL_SECONDS = 0.25
SHAPE_KEY_STRUCTURE_POLL_SECONDS = 0.25
MATERIAL_SLOTS_SIGNATURE_POLL_SECONDS = material_sync.MATERIAL_SLOTS_SIGNATURE_POLL_SECONDS


def _get_sync_enabled() -> bool:
    """Read the scene-level sync-enabled flag."""
    if bpy is None or bpy.context is None:
        return False
    scene = bpy.context.scene
    return getattr(scene, "blendersync_sync_enabled", False)


def _get_object_sync_hz() -> float:
    return get_object_state_sync_hz()


def _get_mesh_sync_hz() -> float:
    return get_mesh_update_sync_hz()


def _get_evaluated_mesh_preview_debounce_seconds() -> float:
    return _get_mesh_preview_debounce_seconds()


def _get_mesh_preview_debounce_seconds() -> float:
    value = get_evaluated_mesh_preview_debounce_ms()
    return max(0.05, min(5.0, value / 1000.0))


def _get_preview_idle_commit_seconds() -> float:
    value = get_preview_idle_commit_seconds()
    return max(1.0, min(60.0, value))


def _get_view_sync_enabled() -> bool:
    if bpy is None or bpy.context is None:
        return False
    scene = bpy.context.scene
    return bool(getattr(scene, "blendersync_view_sync_enabled", False))


def _get_view_sync_hz() -> float:
    return get_view_sync_hz()


def _get_view_sync_scale() -> float:
    return get_view_sync_scale()


def _get_lifecycle_reconcile_hz() -> float:
    return get_lifecycle_reconcile_hz()


def _collect_shape_key_weights(obj) -> tuple[tuple[str, float], ...]:
    return preview_sync.collect_shape_key_weights(obj)


def _send_shape_key_weights_if_changed(obj, force: bool = False, reason: str = "auto_object_mode") -> bool:
    return preview_sync.send_shape_key_weights_if_changed(
        _shape_key_runtime,
        _preview_sync_hooks(),
        obj,
        force=force,
        reason=reason,
    )


def _mesh_has_shape_keys(obj) -> bool:
    mesh = getattr(obj, "data", None) if obj is not None else None
    shape_keys = getattr(mesh, "shape_keys", None) if mesh is not None else None
    key_blocks = getattr(shape_keys, "key_blocks", []) if shape_keys is not None else []
    try:
        return len(key_blocks) > 1
    except Exception:
        return False


def _get_current_mode() -> str:
    """Get the current Blender mode as a string."""
    if bpy is None or bpy.context is None:
        return "UNKNOWN"
    return str(bpy.context.mode)


def _should_tick(now: float) -> bool:
    """Check if enough time has passed since last tick for the current frequency."""
    hz = _get_object_sync_hz()
    if hz <= 0:
        return False
    interval = 1.0 / hz
    return (now - _last_tick_time) >= interval






def sync_objects_once(selected: list | None = None) -> dict:
    if bpy is None or bpy.context is None:
        return {
            "ok": False,
            "reason": "blender_context_missing",
            "mode": "UNKNOWN",
            "selectedCount": 0,
            "objectStateSynced": 0,
            "meshSynced": 0,
            "riggedSynced": 0,
            "skippedNotReady": 0,
            "skippedUnsupportedType": 0,
            "skippedNoContext": 0,
            "errors": 0,
        }

    mode = _get_current_mode()
    if mode != "OBJECT":
        return {
            "ok": False,
            "reason": "object_mode_required",
            "mode": mode,
            "selectedCount": 0,
            "objectStateSynced": 0,
            "meshSynced": 0,
            "riggedSynced": 0,
            "skippedNotReady": 0,
            "skippedUnsupportedType": 0,
            "skippedNoContext": 0,
            "errors": 0,
        }

    if selected is None:
        selected = list(getattr(bpy.context, "selected_objects", []) or [])
    else:
        selected = list(selected or [])
    if not selected:
        active = getattr(bpy.context, "object", None)
        selected = [active] if active is not None else []
    if not selected:
        return {
            "ok": False,
            "reason": "selection_empty",
            "mode": mode,
            "selectedCount": 0,
            "objectStateSynced": 0,
            "meshSynced": 0,
            "riggedSynced": 0,
            "skippedNotReady": 0,
            "skippedUnsupportedType": 0,
            "skippedNoContext": 0,
            "errors": 0,
        }

    object_state_synced = 0
    mesh_synced = 0
    rigged_synced = 0
    skipped_not_ready = 0
    skipped_no_context = 0
    errors = 0

    ready_objects = []
    skipped_unsupported_type = len(summarize_unsupported(selected))
    supported_objects = collect_supported_sync_objects(selected)
    rigged_armatures = []
    rigged_armature_keys = set()

    for obj in supported_objects:
        armature_obj = None
        if getattr(obj, "type", None) == "ARMATURE" and bool(obj.get(AUTO_SYNC_READY_KEY, False)):
            armature_obj = obj
        elif getattr(obj, "type", None) == "MESH":
            find_armature = getattr(obj, "find_armature", None)
            candidate = find_armature() if callable(find_armature) else None
            if candidate is not None and bool(candidate.get(AUTO_SYNC_READY_KEY, False)):
                armature_obj = candidate

        if armature_obj is not None:
            key = object_identity_key(armature_obj)
            if key not in rigged_armature_keys:
                rigged_armature_keys.add(key)
                rigged_armatures.append(armature_obj)

    managed_mesh_keys = collect_meshes_managed_by_armatures(rigged_armatures)

    for obj in supported_objects:
        obj_type = getattr(obj, "type", None)
        if obj_type == "MESH" and object_identity_key(obj) in managed_mesh_keys:
            continue
        if not bool(obj.get(AUTO_SYNC_READY_KEY, False)):
            skipped_not_ready += 1
            continue

        ready_objects.append(obj)

    ordinary_ready_objects = []
    for obj in ready_objects:
        obj_type = getattr(obj, "type", None)
        if obj_type == "ARMATURE" and object_identity_key(obj) in rigged_armature_keys:
            continue
        if obj_type == "MESH" and object_identity_key(obj) in managed_mesh_keys:
            continue
        ordinary_ready_objects.append(obj)

    for armature_obj in rigged_armatures:
        try:
            rigged_context = build_single_object_live_context(
                get_session(),
                armature_obj,
                package_id=f"pkg-rigged-update-{int(time.time() * 1000)}",
            )
            rigged_context["sendMode"] = "manual_update_selected_rigged"
            rigged_context["triggerType"] = "update_selected_rigged"
            rigged_context["updateIntent"] = "scene_sync_update_selected"
            _keep_only_unknown_material_contents_for_rigged_update(rigged_context)
            rigged_context["visibilityPayloads"] = []
            filter_stats = _filter_rigged_update_changed_mesh_roots(rigged_context)
            if filter_stats.get("skipped", 0) > 0:
                trace(
                    "SceneSync",
                    "rigged_mesh_parts_filtered",
                    lambda: "Filtered unchanged mesh parts from a rigged-object update.",
                    lambda: {
                        "objectName": getattr(armature_obj, "name", "<unnamed>"),
                        "totalCount": filter_stats.get("total"),
                        "keptCount": filter_stats.get("kept"),
                        "skippedCount": filter_stats.get("skipped"),
                        "missingFingerprintCount": filter_stats.get("missingFingerprint"),
                    },
                )
            rigged_result = send_selected_resources(rigged_context)
            if rigged_result.ok:
                _mark_sent_mesh_root_fingerprints(rigged_context)
                rigged_synced += 1
            else:
                errors += 1
        except Exception as exc:
            log_exception(
                "SceneSync",
                "rigged_update_exception",
                exc,
                fields={"objectName": getattr(armature_obj, "name", "<unnamed>")},
            )
            errors += 1

    if ordinary_ready_objects:
        state_payload = build_object_state_payload(ordinary_ready_objects, source_hint="manual_update_selected")
        state_items = state_payload.get("objects") or []
        if state_items:
            state_result = send_object_state_update(
                {
                    "session": get_session(),
                    "triggerType": "update_selected_object_state",
                    "sendMode": "object_state_only",
                    "correlationId": f"manual-update-state-{int(time.time() * 1000)}",
                    "objectStatePayload": state_payload,
                    "objectStateCount": len(state_items),
                }
            )
            if state_result.ok:
                object_state_synced = len(state_items)
                for obj in ordinary_ready_objects:
                    object_state.cache_runtime_signatures(_object_state_runtime, _object_state_hooks(), obj)
            else:
                errors += 1
        else:
            skipped_no_context += len(ordinary_ready_objects)

    for obj in ordinary_ready_objects:
        obj_type = getattr(obj, "type", None) if obj is not None else None
        if obj_type != "MESH" or getattr(obj, "data", None) is None:
            continue

        if material_sync.send_slot_mesh_update(
            _material_runtime,
            _material_sync_hooks(),
            obj,
            force=False,
            reason="manual_update_selected",
        ):
            mesh_synced += 1
            continue

        pair_id = _pair_id_for_object_readonly(obj)
        geometry_dirty = _consume_pair_geometry_dirty_event(pair_id, respect_due=False)
        evaluated_dirty = _consume_evaluated_mesh_dirty_event(pair_id, respect_due=False)
        if not geometry_dirty and not evaluated_dirty:
            _send_shape_key_weights_if_changed(
                obj,
                force=True,
                reason="manual_update_selected_weights_only",
            )
            continue

        dirty_state = evaluated_dirty or geometry_dirty or {}
        dirty_source = str(dirty_state.get("source") or "manual_update_selected")
        mesh_source_override = "evaluated" if evaluated_dirty or _should_auto_rebuild_evaluated_object(obj) else None
        mesh_context = _build_mesh_update_context_for_object(
            obj,
            include_extras=True,
            mesh_source_override=mesh_source_override,
            rebuild_reason=f"manual_update_selected:{dirty_source}",
        )
        if mesh_context is None:
            skipped_no_context += 1
            continue

        mesh_result = _send_mesh_update_with_materials(mesh_context)
        mesh_reason = mesh_result.get("reason")
        if mesh_result.get("ok"):
            _store_mesh_content_baseline_from_context(obj, mesh_context, reason="manual_update_selected_preview_sent")
            _record_sent_material_refs(obj, mesh_context, reason="manual_update_selected_preview_sent")
            _send_shape_key_weights_if_changed(obj, force=True, reason="manual_update_selected_after_mesh_update")
            mesh_synced += 1
        elif mesh_reason == "no_mesh_change":
            _send_shape_key_weights_if_changed(
                obj,
                force=True,
                reason="manual_update_selected_after_no_mesh_change",
            )
        else:
            errors += 1

    synced_total = object_state_synced + mesh_synced + rigged_synced
    ok = synced_total > 0 and errors == 0
    reason = "sync_selected_ok"
    if synced_total == 0 and errors == 0:
        reason = "sync_selected_noop"
    elif synced_total > 0 and (skipped_not_ready > 0 or skipped_unsupported_type > 0 or skipped_no_context > 0):
        reason = "sync_selected_partial"
    elif errors > 0:
        reason = "sync_selected_partial" if synced_total > 0 else "sync_selected_failed"

    return {
        "ok": ok,
        "reason": reason,
        "mode": mode,
        "selectedCount": len(selected),
        "objectStateSynced": object_state_synced,
        "meshSynced": mesh_synced,
        "riggedSynced": rigged_synced,
        "synced": synced_total,
        "skippedNotReady": skipped_not_ready,
        "skippedUnsupportedType": skipped_unsupported_type,
        "skippedNoContext": skipped_no_context,
        "errors": errors,
    }


def sync_selected_objects_once() -> dict:
    return sync_objects_once(None)


def _pair_id_for_object_readonly(obj) -> str | None:
    instance_id = get_instance_id(obj)
    return f"pair-{instance_id}" if instance_id else None


def _data_ids_match(candidate, data_id) -> bool:
    if candidate is None or data_id is None:
        return False
    if candidate is data_id:
        return True
    try:
        candidate_original = getattr(candidate, "original", None) or candidate
    except Exception:
        candidate_original = candidate
    try:
        data_original = getattr(data_id, "original", None) or data_id
    except Exception:
        data_original = data_id
    return candidate_original is data_original


def _object_pair_id_for_data_id(data_id) -> str | None:
    if bpy is None or bpy.context is None or bpy.context.scene is None or data_id is None:
        return None
    for obj in getattr(bpy.context.scene, "objects", []) or []:
        if obj is None:
            continue
        if _data_ids_match(getattr(obj, "data", None), data_id):
            return _pair_id_for_object_readonly(obj)
    return None


def _ready_object_pair_id_for_data_id(data_id, preferred_obj=None) -> str | None:
    if bpy is None or bpy.context is None or bpy.context.scene is None or data_id is None:
        return None
    if (
        preferred_obj is not None
        and _data_ids_match(getattr(preferred_obj, "data", None), data_id)
        and bool(preferred_obj.get(AUTO_SYNC_READY_KEY, False))
    ):
        return _pair_id_for_object_readonly(preferred_obj)
    for obj in getattr(bpy.context.scene, "objects", []) or []:
        if obj is None or not _data_ids_match(getattr(obj, "data", None), data_id):
            continue
        if bool(obj.get(AUTO_SYNC_READY_KEY, False)):
            return _pair_id_for_object_readonly(obj)
    return None


def _object_for_pair_id(pair_id: str | None):
    if bpy is None or bpy.context is None or bpy.context.scene is None or not pair_id:
        return None
    wanted = str(pair_id)
    for obj in getattr(bpy.context.scene, "objects", []) or []:
        if obj is None:
            continue
        if _pair_id_for_object_readonly(obj) == wanted:
            return obj
    name = _evaluated_preview_runtime.object_name_by_pair.get(wanted)
    return bpy.data.objects.get(name) if name else None


def _pair_has_pending_geometry_dirty(pair_id: str | None) -> bool:
    return preview_sync.has_pending_geometry_dirty(_geometry_preview_runtime, pair_id)


def _object_state_hooks() -> ObjectStateHooks:
    return ObjectStateHooks(
        get_sync_enabled=_get_sync_enabled,
        pair_id_for_object=_pair_id_for_object_readonly,
        object_for_pair=_object_for_pair_id,
        has_pending_geometry_dirty=_pair_has_pending_geometry_dirty,
    )


def _material_sync_hooks() -> MaterialSyncHooks:
    return MaterialSyncHooks(
        get_sync_enabled=_get_sync_enabled,
        get_current_mode=_get_current_mode,
        pair_id_for_object=_pair_id_for_object_readonly,
        find_object_by_pair_id=_find_object_by_pair_id,
        send_slot_mesh_update=lambda obj, force=False, reason="auto_sync": material_sync.send_slot_mesh_update(
            _material_runtime,
            _material_sync_hooks(),
            obj,
            force=force,
            reason=reason,
        ),
        build_mesh_context=_build_mesh_update_context_for_object,
        store_mesh_content_baseline=_store_mesh_content_baseline_from_context,
        send_shape_key_weights=_send_shape_key_weights_if_changed,
        object_uses_evaluated_materials=_object_uses_evaluated_materials,
    )


def _mesh_context_hooks() -> MeshContextHooks:
    return MeshContextHooks(
        get_current_mode=_get_current_mode,
        get_sync_enabled=_get_sync_enabled,
        pair_id_for_object=_pair_id_for_object_readonly,
        mesh_has_shape_keys=_mesh_has_shape_keys,
        object_has_visible_modifiers=_object_has_visible_modifiers,
        had_visible_modifiers=lambda pair_id: bool(
            _evaluated_preview_runtime.had_visible_modifiers_by_pair.get(pair_id)
        ),
        preview_buffer_dir=_preview_buffer_dir,
        safe_preview_name=_safe_preview_name,
        write_array_buffer=_write_array_buffer,
        read_active_uv0_values=_read_active_uv0_values,
        log_uv_skip=_log_uv_skip,
        uv_verbose_enabled=lambda: AUTOPREVIEW_UV_VERBOSE,
        should_include_fingerprint=_should_include_unity_mesh_content_fingerprint,
        collect_material_refs=material_sync.collect_refs_for_object,
        material_refs_for_export=material_sync.collect_refs_for_export,
        material_contents_for_export=material_sync.build_material_contents_for_export,
    )


def _structure_watch_hooks() -> StructureWatchHooks:
    return StructureWatchHooks(
        get_sync_enabled=_get_sync_enabled,
        get_current_mode=_get_current_mode,
        mesh_has_shape_keys=_mesh_has_shape_keys,
        pair_id_for_object=_pair_id_for_object_readonly,
        mark_evaluated_dirty=_mark_evaluated_mesh_dirty,
        mark_geometry_dirty=_mark_pair_dirty,
    )


def _selected_preview_objects() -> list | None:
    if bpy is None:
        return None
    context = getattr(bpy, "context", None)
    selected = list(getattr(context, "selected_objects", []) or []) if context is not None else []
    active = getattr(context, "active_object", None) if context is not None else None
    if active is not None and active not in selected:
        selected.append(active)
    return selected


def _preview_sync_hooks() -> PreviewSyncHooks:
    return PreviewSyncHooks(
        get_sync_enabled=_get_sync_enabled,
        get_current_mode=_get_current_mode,
        pair_id_for_object=_pair_id_for_object_readonly,
        object_for_pair=_object_for_pair_id,
        should_auto_rebuild=_should_auto_rebuild_evaluated_object,
        object_has_visible_modifiers=_object_has_visible_modifiers,
        modifier_stack_signature=modifier_stack_signature,
        get_evaluated_preview_debounce_seconds=_get_evaluated_mesh_preview_debounce_seconds,
        get_mesh_preview_debounce_seconds=_get_mesh_preview_debounce_seconds,
        find_object_by_pair=_object_for_pair_id,
        build_mesh_context=_build_mesh_update_context_for_object,
        send_mesh_update=_send_mesh_update_with_materials,
        store_mesh_content_baseline=_store_mesh_content_baseline_from_context,
        record_sent_material_refs=_record_sent_material_refs,
        get_session=get_session,
        ensure_instance_id=ensure_instance_id,
        ensure_mesh_asset_id=ensure_mesh_asset_id_for_object,
        mesh_has_shape_keys=_mesh_has_shape_keys,
        blendshape_weight_sync_interval_seconds=BLENDSHAPE_WEIGHT_SYNC_INTERVAL_SECONDS,
        get_active_object=lambda: (
            getattr(getattr(bpy, "context", None), "active_object", None) if bpy is not None else None
        ),
        preview_matches_mesh_content_baseline=_preview_matches_mesh_content_baseline,
        send_deferred_object_state=lambda obj, pair_id, source: object_state.send_deferred_after_mesh_preview(
            _object_state_runtime,
            _object_state_hooks(),
            obj,
            pair_id,
            source=source,
        ),
        get_selected_objects=_selected_preview_objects,
        build_single_object_live_context=build_single_object_live_context,
        send_selected_resources=send_selected_resources,
        build_mesh_binary_buffers=build_mesh_binary_buffers_from_content,
        compute_mesh_content_fingerprint=compute_mesh_content_fingerprint,
        collect_material_refs=material_sync.collect_refs_for_object,
        preview_buffer_cleanup_interval_seconds=PREVIEW_BUFFER_CLEANUP_INTERVAL_SECONDS,
        preview_buffer_ttl_seconds=PREVIEW_BUFFER_TTL_SECONDS,
        uv_verbose_enabled=lambda: AUTOPREVIEW_UV_VERBOSE,
    )


def _object_has_visible_modifiers(obj) -> bool:
    for mod in list(getattr(obj, "modifiers", []) or []):
        try:
            if bool(getattr(mod, "show_viewport", True)):
                return True
        except Exception:
            continue
    return False


def _object_uses_evaluated_materials(obj) -> bool:
    # Shape-key meshes are intentionally exported from original topology, even
    # when a Geometry Nodes modifier is visible. Keep their object-slot watcher
    # active so the material source follows the same mesh-source policy.
    return has_visible_geometry_nodes_modifier(obj) and not _mesh_has_shape_keys(obj)


def _classify_object_update_for_evaluated(obj, pair_id: str | None) -> tuple[str, str, str, str]:
    signature = modifier_stack_signature(obj)
    previous = _evaluated_preview_runtime.modifier_signature_by_pair.get(pair_id or "")
    if previous is None:
        previous = get_modifier_stack_baseline(obj)
        if previous is not None:
            _evaluated_preview_runtime.modifier_signature_by_pair[pair_id or ""] = previous
    if previous is None:
        _evaluated_preview_runtime.modifier_signature_by_pair[pair_id or ""] = signature
        if _object_has_visible_modifiers(obj):
            return "content", "modifier_signature_initialized_with_modifiers", signature, ""
        return "context_noise", "modifier_signature_initialized_no_modifiers", signature, ""
    if previous != signature:
        _evaluated_preview_runtime.modifier_signature_by_pair[pair_id or ""] = signature
        return "content", "modifier_signature_changed", signature, previous
    return "context_noise", "object_update_no_modifier_signature_change", signature, previous


def _object_state_changed_since_runtime_baseline(obj, pair_id: str | None) -> bool:
    pair_key = str(pair_id or "")
    previous = _object_state_runtime.signatures_by_pair.get(pair_key)
    if previous is None:
        return False
    return object_state.object_state_signature(obj) != previous


def _classify_transform_update_for_evaluated(obj, pair_id: str | None) -> tuple[str, str, str, str]:
    pair_key = pair_id or ""
    previous = _evaluated_preview_runtime.modifier_signature_by_pair.get(pair_key)
    if previous is None:
        previous = get_modifier_stack_baseline(obj)
        if previous is not None:
            _evaluated_preview_runtime.modifier_signature_by_pair[pair_key] = previous
    signature = modifier_stack_signature(obj)
    if _object_state_changed_since_runtime_baseline(obj, pair_id):
        # Blender 4.2 can tag modifier edits as transform updates, so transform
        # flags alone cannot distinguish a parameter edit from object motion.
        # A changed object-state signature means this event is actual object
        # motion; rebase any incidental modifier-signature drift and let the
        # object-state path send only the transform.
        _evaluated_preview_runtime.modifier_signature_by_pair[pair_key] = signature
        return "context_noise", "object_transform_update_noise", signature, signature
    if previous is None:
        _evaluated_preview_runtime.modifier_signature_by_pair[pair_key] = signature
        return "context_noise", "object_transform_update_noise", signature, signature
    if previous != signature:
        _evaluated_preview_runtime.modifier_signature_by_pair[pair_key] = signature
        return "content", "modifier_signature_changed", signature, previous
    return "context_noise", "object_transform_update_noise", signature, previous


def _should_auto_rebuild_evaluated_object(obj) -> bool:
    if obj is None or getattr(obj, "type", None) != "MESH":
        return False
    if not bool(obj.get(AUTO_SYNC_READY_KEY, False)):
        return False
    if _mesh_has_shape_keys(obj):
        return False
    pair_id = _pair_id_for_object_readonly(obj)
    has_visible_modifiers = _object_has_visible_modifiers(obj)
    # Important edge: deleting the last modifier leaves the object with no
    # visible modifiers, but Unity still needs one more mesh update to revert
    # the preview from the previously baked evaluated mesh back to original.
    had_visible_modifiers = bool(_evaluated_preview_runtime.had_visible_modifiers_by_pair.get(pair_id or ""))
    return has_visible_modifiers or had_visible_modifiers


def _mark_evaluated_mesh_dirty(
    obj,
    *,
    source: str = "depsgraph",
    dirty_class: str = "content",
    now: float | None = None,
) -> None:
    preview_sync.mark_evaluated_mesh_dirty(
        _evaluated_preview_runtime,
        _preview_sync_hooks(),
        obj,
        source=source,
        dirty_class=dirty_class,
        now=now,
    )


def _mark_pair_dirty(
    pair_id: str | None,
    *,
    channel: str = "geometry",
    source: str = "unknown",
    now: float | None = None,
    force_mesh_send: bool = False,
) -> None:
    preview_sync.mark_pair_dirty(
        _geometry_preview_runtime,
        _preview_sync_hooks(),
        pair_id,
        channel=channel,
        source=source,
        now=now,
        force_mesh_send=force_mesh_send,
    )


def _consume_pair_geometry_dirty_event(
    pair_id: str | None,
    *,
    respect_due: bool = True,
    require_auto_eligible: bool = False,
) -> dict | None:
    """Consume only explicit geometry dirty events.

    Full preview sends are expensive enough that the old safety-poll path must
    not trigger them. Safety polling was useful for no-change incremental probes;
    the default mesh path is now event-driven and full-preview based.
    """
    return preview_sync.consume_geometry_dirty_event(
        _geometry_preview_runtime,
        pair_id,
        respect_due=respect_due,
        require_auto_eligible=require_auto_eligible,
    )


def _consume_evaluated_mesh_dirty_event(
    pair_id: str | None,
    *,
    respect_due: bool = True,
    require_auto_eligible: bool = False,
) -> dict | None:
    return preview_sync.consume_evaluated_dirty_event(
        _evaluated_preview_runtime,
        pair_id,
        respect_due=respect_due,
        require_auto_eligible=require_auto_eligible,
    )


@persistent
def _dirty_probe_depsgraph_update(scene, depsgraph) -> None:
    global _dirty_probe_last_summary_time
    if bpy is None or depsgraph is None:
        return
    if evaluated_mesh_build_in_progress():
        return
    now = time.time()
    mode = _get_current_mode()
    active = getattr(bpy.context, "active_object", None) if bpy.context is not None else None
    active_pair = None
    if active is not None:
        active_pair = _pair_id_for_object_readonly(active)

    mesh_updates = 0
    object_updates = 0
    other_updates = 0
    logged_any = False
    try:
        updates = list(getattr(depsgraph, "updates", []) or [])
    except Exception:
        return

    object_update_classifications: dict[int, tuple[str, str, str, str]] = {}
    transform_noise_pairs: set[str] = set()
    for update in updates:
        data_id = getattr(update, "id", None)
        try:
            is_object_update = bpy is not None and isinstance(data_id, bpy.types.Object)
        except Exception:
            is_object_update = False
        if not is_object_update:
            continue
        pair_id = _pair_id_for_object_readonly(data_id)
        if not pair_id or not bool(data_id.get(AUTO_SYNC_READY_KEY, False)):
            continue
        is_transform_update = bool(getattr(update, "is_updated_transform", False))
        if is_transform_update:
            classification = _classify_transform_update_for_evaluated(data_id, pair_id)
        else:
            classification = _classify_object_update_for_evaluated(data_id, pair_id)
        object_update_classifications[id(update)] = classification
        if is_transform_update and classification[0] == "context_noise":
            transform_noise_pairs.add(pair_id)

    for update in updates:
        data_id = getattr(update, "id", None)
        id_type = type(data_id).__name__ if data_id is not None else "None"
        name = getattr(data_id, "name", "<unnamed>") if data_id is not None else "<none>"
        is_geometry_update = bool(getattr(update, "is_updated_geometry", False))
        is_transform_update = bool(getattr(update, "is_updated_transform", False))
        pair_id = None
        if bpy is not None and data_id is not None:
            try:
                if isinstance(data_id, bpy.types.Mesh):
                    mesh_updates += 1
                    pair_id = _ready_object_pair_id_for_data_id(data_id, active)
                elif isinstance(data_id, bpy.types.Object):
                    object_updates += 1
                    pair_id = _pair_id_for_object_readonly(data_id)
                else:
                    other_updates += 1
            except Exception:
                other_updates += 1
        if pair_id and mode in _MESH_EDIT_FAMILY_MODES:
            if is_geometry_update:
                _mark_pair_dirty(pair_id, channel="geometry", source=f"depsgraph_{id_type}_geometry", now=now)
        elif mode == "OBJECT":
            if pair_id and isinstance(data_id, bpy.types.Object):
                if bool(data_id.get(AUTO_SYNC_READY_KEY, False)):
                    object_state.mark_dirty(
                        _object_state_runtime,
                        _object_state_hooks(),
                        data_id,
                        source="depsgraph_object",
                        now=now,
                    )
                    classification = object_update_classifications.get(id(update))
                    if classification is None:
                        if is_transform_update:
                            classification = _classify_transform_update_for_evaluated(data_id, pair_id)
                        else:
                            classification = _classify_object_update_for_evaluated(data_id, pair_id)
                    dirty_class, dirty_source, _, _ = classification
                    transform_noise = bool(is_transform_update and dirty_class == "context_noise")
                    if getattr(data_id, "type", None) == "MESH" and is_geometry_update and not transform_noise:
                        _mark_pair_dirty(pair_id, channel="geometry", source="depsgraph_object_geometry_object_mode", now=now)
                    _mark_evaluated_mesh_dirty(data_id, source=dirty_source, dirty_class=dirty_class, now=now)
            elif pair_id and isinstance(data_id, bpy.types.Mesh):
                obj_for_pair = _object_for_pair_id(pair_id)
                _mark_pair_dirty(pair_id, channel="geometry", source="depsgraph_mesh_object_mode", now=now)
                _mark_evaluated_mesh_dirty(obj_for_pair, source="depsgraph_mesh", dirty_class="content", now=now)
            elif active is not None and _should_auto_rebuild_evaluated_object(active):
                if id_type in {"NodeTree", "GeometryNodeTree"}:
                    dirty_class = (
                        "context_noise"
                        if active_pair in transform_noise_pairs
                        else "content"
                    )
                else:
                    dirty_class = "context_noise"
                # Some modifier/GN edits surface as NodeTree/generic depsgraph
                # updates rather than Object/Mesh.  NodeTree can represent real
                # GN content changes, while other fallback updates are treated as
                # context noise: selection/active-object changes may refresh
                # preview checks, but must not schedule mesh previews.
                _mark_evaluated_mesh_dirty(active, source=f"depsgraph_{id_type}", dirty_class=dirty_class, now=now)
        if DIRTY_PROBE_VERBOSE:
            key = pair_id or f"{id_type}:{name}"
            _dirty_probe_counts_by_id[key] += 1
            _dirty_probe_last_seen_by_id[key] = now
            last_log = float(_dirty_probe_last_log_by_id.get(key, 0.0) or 0.0)
            if pair_id == active_pair and (now - last_log) >= 1.0:
                trace(
                    "SceneSync",
                    "dirty_probe_update",
                    lambda: "Observed a depsgraph update for the active synchronized object.",
                    lambda: {
                        "mode": mode,
                        "pairId": pair_id,
                        "idType": id_type,
                        "idName": name,
                        "count": _dirty_probe_counts_by_id[key],
                    },
                )
                _dirty_probe_last_log_by_id[key] = now
                logged_any = True

    if DIRTY_PROBE_VERBOSE and (mesh_updates or object_updates or other_updates) and (now - _dirty_probe_last_summary_time) >= 5.0 and not logged_any:
        trace(
            "SceneSync",
            "dirty_probe_summary",
            lambda: "Aggregated recent depsgraph updates.",
            lambda: {
                "mode": mode,
                "meshUpdates": mesh_updates,
                "objectUpdates": object_updates,
                "otherUpdates": other_updates,
            },
        )
        _dirty_probe_last_summary_time = now


def _register_dirty_probe() -> None:
    global _dirty_probe_registered
    if bpy is None:
        return
    handlers = bpy.app.handlers.depsgraph_update_post
    if _dirty_probe_depsgraph_update not in handlers:
        handlers.append(_dirty_probe_depsgraph_update)
        trace(
            "SceneSync",
            "dirty_probe_registered",
            lambda: "Registered the depsgraph dirty probe.",
        )
    _dirty_probe_registered = _dirty_probe_depsgraph_update in handlers


def _unregister_dirty_probe() -> None:
    global _dirty_probe_registered
    if bpy is None:
        return
    handlers = bpy.app.handlers.depsgraph_update_post
    try:
        if _dirty_probe_depsgraph_update in handlers:
            handlers.remove(_dirty_probe_depsgraph_update)
    except Exception:
        pass
    _dirty_probe_registered = False


def _reset_lifecycle_runtime_state() -> None:
    _lifecycle_runtime.reset()


def reset_file_runtime_state(reason: str = "file_load") -> None:
    global _last_tick_time, _last_mesh_tick_time
    global _last_lifecycle_tick_time, _last_view_tick_time, _last_view_fingerprint, _last_mode
    global _last_sync_enabled, _last_auto_sync_state_signature
    global _dirty_probe_last_summary_time

    lifecycle_collections = (
        _lifecycle_runtime.baseline_ids,
        _lifecycle_runtime.managed_ids,
        _lifecycle_runtime.entries,
        _lifecycle_runtime.new_reason_by_id,
        _lifecycle_runtime.delete_reason_by_id,
    )
    object_state_collections = (
        _object_state_runtime.signatures_by_pair,
        _object_state_runtime.non_transform_signatures_by_pair,
        _object_state_runtime.dirty_queue,
        _object_state_runtime.motion_burst_until_by_pair,
        _object_state_runtime.active_fallback_next_at_by_pair,
    )
    material_collections = (
        _material_runtime.refs_by_pair,
        _material_runtime.slots_poll_next_at_by_pair,
        _material_runtime.pending_bundles_by_ref,
        _material_runtime.waiting_pair_ids_by_ref,
        _material_runtime.pending_resends_by_ref,
        _material_runtime.retry_after_by_ref,
    )
    geometry_preview_collections = (
        _geometry_preview_runtime.dirty_by_pair,
        _geometry_preview_runtime.inflight_by_pair,
        _geometry_preview_runtime.dirty_during_inflight_by_pair,
    )
    evaluated_preview_collections = (
        _evaluated_preview_runtime.dirty_by_pair,
        _evaluated_preview_runtime.object_name_by_pair,
        _evaluated_preview_runtime.last_preview_sent_at_by_pair,
        _evaluated_preview_runtime.had_visible_modifiers_by_pair,
        _evaluated_preview_runtime.modifier_signature_by_pair,
    )
    mesh_structure_collections = (
        _mesh_structure_runtime.uv_channels_signature_by_pair,
        _mesh_structure_runtime.uv_channels_poll_next_time_by_pair,
        _mesh_structure_runtime.color_attributes_signature_by_pair,
        _mesh_structure_runtime.color_attributes_poll_next_time_by_pair,
        _mesh_structure_runtime.shape_key_structure_signature_by_pair,
        _mesh_structure_runtime.shape_key_structure_poll_next_time_by_pair,
    )
    shape_key_collections = (
        _shape_key_runtime.edit_skip_logged_by_pair,
        _shape_key_runtime.last_weights_by_pair,
        _shape_key_runtime.next_weight_send_time_by_pair,
    )
    uv_preview_collections = (
        _uv_preview_runtime.last_skip_log_by_pair,
        _uv_preview_runtime.inflight_by_pair,
    )
    preview_mapping_collections = (
        _preview_mapping_runtime.source_indices_by_pair,
        _preview_mapping_runtime.source_loop_indices_by_pair,
        _preview_mapping_runtime.hashes_by_pair,
    )
    collections = (
        _dirty_probe_counts_by_id,
        _dirty_probe_last_log_by_id,
        _dirty_probe_last_seen_by_id,
        _background_pump_error_last_at_by_signature,
    )
    cleared_entries = sum(
        len(collection)
        for collection in (
            lifecycle_collections
            + object_state_collections
            + material_collections
            + geometry_preview_collections
            + evaluated_preview_collections
            + mesh_structure_collections
            + shape_key_collections
            + uv_preview_collections
            + preview_mapping_collections
            + collections
        )
    )
    _lifecycle_runtime.reset()
    _object_state_runtime.reset()
    _material_runtime.reset()
    _geometry_preview_runtime.reset()
    _evaluated_preview_runtime.reset()
    _mesh_structure_runtime.reset()
    _shape_key_runtime.reset()
    _uv_preview_runtime.reset()
    _preview_mapping_runtime.reset()
    for collection in collections:
        collection.clear()

    _last_tick_time = 0.0
    _last_mesh_tick_time = 0.0
    _last_lifecycle_tick_time = 0.0
    _last_view_tick_time = 0.0
    _last_view_fingerprint = None
    _last_mode = "UNKNOWN"
    _last_sync_enabled = False
    _last_auto_sync_state_signature = None
    _dirty_probe_last_summary_time = 0.0
    trace(
        "SceneSync",
        "controller_state_reset",
        lambda: "Reset file-scoped synchronization state.",
        lambda: {"reason": reason, "clearedEntryCount": cleared_entries},
    )


def _reset_evaluated_runtime_state(reason: str = "unknown") -> None:
    _evaluated_preview_runtime.reset()
    _mesh_structure_runtime.reset()
    trace(
        "SceneSync",
        "evaluated_preview_state_reset",
        lambda: "Reset evaluated-preview runtime state.",
        lambda: {"reason": reason},
    )


def _reset_auto_sync_transient_dirty(reason: str = "unknown") -> None:
    _object_state_runtime.reset_transient()
    _geometry_preview_runtime.reset_transient()
    _evaluated_preview_runtime.reset_transient()


def _capture_evaluated_modifier_signature_baseline(reason: str = "unknown") -> None:
    if bpy is None or bpy.context is None or bpy.context.scene is None:
        return
    count = 0
    for obj in list(getattr(bpy.context.scene, "objects", []) or []):
        if obj is None or getattr(obj, "type", None) != "MESH":
            continue
        if not bool(obj.get(AUTO_SYNC_READY_KEY, False)):
            continue
        pair_id = _pair_id_for_object_readonly(obj)
        if not pair_id:
            continue
        signature = modifier_stack_signature(obj)
        if get_modifier_stack_baseline(obj) is None:
            set_modifier_stack_baseline(obj, signature, reason=reason)
        _evaluated_preview_runtime.modifier_signature_by_pair[pair_id] = signature
        _mesh_structure_runtime.shape_key_structure_signature_by_pair[pair_id] = structure_watch.shape_key_structure_signature(obj)
        if _object_has_visible_modifiers(obj):
            _evaluated_preview_runtime.had_visible_modifiers_by_pair[pair_id] = True
        count += 1
    trace(
        "SceneSync",
        "modifier_baseline_captured",
        lambda: "Captured evaluated-preview modifier baselines.",
        lambda: {"objectCount": count, "reason": reason},
    )


def _store_mesh_content_baseline_from_live_context(obj, send_context: dict | None, *, reason: str) -> None:
    if obj is None or getattr(obj, "type", None) != "MESH" or not isinstance(send_context, dict):
        return
    try:
        mesh_ref = f"mesh-{ensure_mesh_asset_id_for_object(obj)}"
    except Exception:
        return
    for root in list(send_context.get("selected") or []):
        if not isinstance(root, dict) or root.get("type") != "mesh":
            continue
        meta = root.get("metadata") or {}
        if str(meta.get("assetId") or "").strip() != mesh_ref:
            continue
        fp, fp_no_uv = mesh_payload_content_fingerprints(meta.get("mesh") or {})
        if fp or fp_no_uv:
            set_mesh_content_fingerprint_baseline(obj, fp, fp_no_uv, reason=reason)
        return


def _on_lifecycle_bootstrap_success(obj, send_context: dict, pair_id: str) -> None:
    _geometry_preview_runtime.dirty_by_pair.pop(pair_id, None)
    _evaluated_preview_runtime.dirty_by_pair.pop(pair_id, None)
    mesh_ref = f"mesh-{ensure_mesh_asset_id_for_object(obj)}" if getattr(obj, "data", None) is not None else ""
    refs = material_refs_from_live_context(send_context, pair_id=pair_id, mesh_ref=mesh_ref)
    if refs is None:
        refs = tuple(material_sync.collect_refs_for_object(obj))
    color_signature = structure_watch.color_attributes_signature(obj)
    capture_object_baselines(
        obj,
        material_refs=refs,
        color_attributes_signature=color_signature,
        reason="lifecycle_bootstrap",
    )
    _store_mesh_content_baseline_from_live_context(obj, send_context, reason="lifecycle_bootstrap")
    _material_runtime.refs_by_pair[pair_id] = refs
    _evaluated_preview_runtime.modifier_signature_by_pair[pair_id] = modifier_stack_signature(obj)
    _mesh_structure_runtime.uv_channels_signature_by_pair[pair_id] = uv_channels_signature(obj)
    _mesh_structure_runtime.color_attributes_signature_by_pair[pair_id] = color_signature
    object_state.cache_runtime_signatures(_object_state_runtime, _object_state_hooks(), obj)


def _lifecycle_hooks() -> LifecycleHooks:
    return LifecycleHooks(
        on_bootstrap_success=_on_lifecycle_bootstrap_success,
        clear_removed_pair=lambda pair_id, reason: _clear_removed_pair_runtime_state(pair_id, reason=reason),
        poll_non_transform=lambda current, now: object_state.poll_scene_non_transform(
            _object_state_runtime,
            _object_state_hooks(),
            current,
            now,
        ),
        cache_ready_object=lambda obj: object_state.cache_runtime_signatures(
            _object_state_runtime,
            _object_state_hooks(),
            obj,
        ),
    )


def _init_lifecycle_baseline() -> None:
    object_lifecycle.init_lifecycle_baseline(_lifecycle_runtime, _lifecycle_hooks())




def _run_lifecycle_tick(now: float, do_full_diff: bool = True, candidate_ids: set[str] | None = None) -> None:
    object_lifecycle.run_lifecycle_tick(
        _lifecycle_runtime,
        _lifecycle_hooks(),
        now,
        do_full_diff=do_full_diff,
        candidate_ids=candidate_ids,
    )


def _clear_pair_runtime_state(pair_id: str | None, reason: str = "unknown") -> tuple[bool, bool]:
    pair_id = str(pair_id or "").strip()
    if not pair_id:
        return False, False
    had_indices, had_loop_indices = _preview_mapping_runtime.clear_pair(pair_id)
    _geometry_preview_runtime.clear_pair(pair_id)
    _evaluated_preview_runtime.clear_pair(pair_id)
    _mesh_structure_runtime.clear_pair(pair_id)
    _shape_key_runtime.clear_pair(pair_id)
    _uv_preview_runtime.clear_pair(pair_id)
    _object_state_runtime.clear_pair(pair_id)
    _material_runtime.clear_pair_cache(pair_id)
    if had_indices or had_loop_indices:
        trace(
            "SceneSync",
            "preview_cache_cleared",
            lambda: "Cleared cached preview mappings.",
            lambda: {
                "pairId": pair_id,
                "reason": reason,
                "hadSourceIndices": had_indices,
                "hadSourceLoopIndices": had_loop_indices,
            },
        )
    return had_indices, had_loop_indices


def _clear_removed_pair_runtime_state(pair_id: str | None, reason: str = "unknown") -> tuple[bool, bool]:
    result = _clear_pair_runtime_state(pair_id, reason=reason)
    material_sync.remove_pair_from_runtime(_material_runtime, pair_id, reason=reason)
    return result


def _clear_preview_fast_cache_for_object(obj, reason: str = "unknown") -> None:
    if obj is None:
        return
    try:
        pair_id = f"pair-{ensure_instance_id(obj)}"
    except Exception:
        return
    _clear_pair_runtime_state(pair_id, reason=reason)


def _object_for_pair_id_any(pair_id: str | None):
    normalized = str(pair_id or "").strip()
    if not normalized or bpy is None or bpy.context is None or bpy.context.scene is None:
        return None
    if normalized.startswith("rigpair-rigobj-"):
        normalized = normalized[len("rigpair-rigobj-") :]
    elif normalized.startswith("rigpair-"):
        normalized = normalized[len("rigpair-") :]
    if normalized.startswith("rigobj-"):
        normalized = normalized[len("rigobj-") :]
    if normalized.startswith("pair-"):
        normalized = normalized[len("pair-") :]
    for obj in getattr(bpy.context.scene, "objects", []) or []:
        try:
            if get_instance_id(obj) == normalized:
                return obj
        except Exception:
            continue
    return None


def _mesh_asset_id_from_ref(mesh_ref: str | None) -> str:
    value = str(mesh_ref or "").strip()
    if value.startswith("mesh-"):
        value = value[len("mesh-") :]
    return value


def _should_include_unity_mesh_content_fingerprint(obj, force: bool = False) -> bool:
    if force:
        return True
    if obj is None:
        return False
    try:
        return bool(obj.get(MESH_REF_SHARED_KEY, False))
    except Exception:
        return False


def apply_mesh_ref_usage(payload: dict) -> dict:
    if not isinstance(payload, dict):
        return {"ok": False, "reason": "invalid_payload"}
    mesh_ref = str(payload.get("meshRef") or "").strip()
    pairs = payload.get("pairs") or []
    if not mesh_ref or not isinstance(pairs, list):
        return {"ok": False, "reason": "invalid_mesh_ref_usage"}
    try:
        ref_count = int(payload.get("refCount") or len(pairs))
    except Exception:
        ref_count = len(pairs)
    is_shared = bool(payload.get("isShared")) or ref_count > 1
    timestamp = int(payload.get("timestamp") or time.time())
    mesh_asset_id = _mesh_asset_id_from_ref(mesh_ref)
    mark_assets_known([mesh_ref])

    applied = 0
    missing = 0
    for pair_id in pairs:
        obj = _object_for_pair_id_any(str(pair_id or ""))
        if obj is None:
            missing += 1
            continue
        try:
            if mesh_asset_id:
                set_mesh_asset_id_for_object(obj, mesh_asset_id)
            obj[MESH_REF_COUNT_KEY] = ref_count
            obj[MESH_REF_SHARED_KEY] = is_shared
            obj[MESH_REF_USAGE_UPDATED_AT_KEY] = timestamp
            applied += 1
        except Exception:
            missing += 1
    if applied or missing:
        trace(
            "SceneSync",
            "mesh_reference_usage_applied",
            lambda: "Applied mesh-reference usage metadata.",
            lambda: {
                "meshRef": mesh_ref,
                "referenceCount": ref_count,
                "shared": is_shared,
                "appliedCount": applied,
                "missingCount": missing,
            },
        )
    return {"ok": True, "meshRef": mesh_ref, "refCount": ref_count, "applied": applied, "missing": missing}


def apply_mesh_content_fingerprint_request(payload: dict) -> dict:
    if not isinstance(payload, dict):
        return {"ok": False, "reason": "invalid_payload"}
    pair_id = str(payload.get("pairId") or "").strip()
    if not pair_id:
        return {"ok": False, "reason": "pair_id_missing"}
    obj = _object_for_pair_id(pair_id)
    if obj is None:
        return {"ok": False, "reason": "object_missing", "pairId": pair_id}
    context = _build_mesh_update_context_for_object(
        obj,
        include_extras=True,
        mesh_source_override="evaluated" if _object_has_visible_modifiers(obj) else "original",
        rebuild_reason=f"mesh_content_fingerprint_request:{payload.get('reason') or 'unknown'}",
        force_mesh_content_fingerprint=True,
    )
    if context is None:
        return {"ok": False, "reason": "mesh_context_missing", "pairId": pair_id}
    context["send_mesh_content_fingerprint"] = True
    context["force_send"] = True
    result = _send_mesh_update_with_materials(context)
    ok = bool(result.get("ok"))
    if ok:
        _store_mesh_content_baseline_from_context(obj, context, reason="mesh_content_fingerprint_request")
        _record_sent_material_refs(obj, context, reason="mesh_content_fingerprint_request")
        trace(
            "SceneSync",
            "mesh_fingerprint_response_sent",
            lambda: "Sent a mesh-content fingerprint response.",
            lambda: {
                "pairId": pair_id,
                "reason": result.get("reason"),
                "meshRef": context.get("mesh_ref"),
            },
        )
    else:
        warn(
            "SceneSync",
            "mesh_fingerprint_response_failed",
            result.get("reason") or "send_failed",
            {"pairId": pair_id, "meshRef": context.get("mesh_ref")},
        )
    return {
        "ok": ok,
        "reason": result.get("reason"),
        "pairId": pair_id,
        "meshRef": context.get("mesh_ref"),
    }


def _preview_buffer_dir() -> Path:
    return preview_sync.preview_buffer_dir(_uv_preview_runtime, _preview_sync_hooks())
def _cleanup_stale_preview_buffers(root: Path) -> None:
    preview_sync.cleanup_stale_preview_buffers(_uv_preview_runtime, _preview_sync_hooks(), root)
def _safe_preview_name(value: str | None) -> str:
    return preview_sync.safe_preview_name(value)
def _write_array_buffer(path: Path, values: array) -> int:
    return preview_sync.write_array_buffer(path, values)
def _log_uv_skip(pair_id: str, reason: str) -> None:
    preview_sync.log_uv_skip(_uv_preview_runtime, pair_id, reason)
def _read_float2_attribute_data(data) -> array | None:
    return preview_sync.read_float2_attribute_data(data)
def _read_edit_bmesh_uv0_values(mesh, pair_id: str | None = None) -> array | None:
    return preview_sync.read_edit_bmesh_uv0_values(_uv_preview_runtime, _preview_sync_hooks(), mesh, pair_id=pair_id)
def _read_active_uv0_values(mesh, pair_id: str | None = None, *, allow_edit_bmesh: bool = False) -> array | None:
    return preview_sync.read_active_uv0_values(
        _uv_preview_runtime,
        _preview_sync_hooks(),
        mesh,
        pair_id=pair_id,
        allow_edit_bmesh=allow_edit_bmesh,
    )
def _send_mesh_preview_with_live_uv(obj, *, reason: str, require_uv: bool = True) -> dict:
    return preview_sync.send_mesh_preview_with_live_uv(
        _uv_preview_runtime,
        _preview_sync_hooks(),
        obj,
        reason=reason,
        require_uv=require_uv,
    )
def manual_preview_active_uv_once() -> dict:
    return preview_sync.manual_preview_active_uv_once(_uv_preview_runtime, _preview_sync_hooks())
def _send_active_mesh_full_preview_if_dirty() -> bool:
    return preview_sync.send_active_mesh_full_preview_if_dirty(
        _geometry_preview_runtime,
        _shape_key_runtime,
        _preview_sync_hooks(),
    )
def _find_object_by_pair_id(pair_id: str):
    if bpy is None or bpy.context is None or bpy.context.scene is None:
        return None
    wanted = str(pair_id or "").strip()
    if not wanted:
        return None
    for obj in getattr(bpy.context.scene, "objects", []) or []:
        if obj is None:
            continue
        try:
            current_pair_id = f"pair-{ensure_instance_id(obj)}"
        except Exception:
            continue
        if current_pair_id == wanted:
            return obj
    return None








def _send_preview_commit_for_object(obj, reason: str = "mode_exit") -> bool:
    return preview_sync.send_preview_commit_for_object(_preview_sync_hooks(), obj, reason=reason)
def manual_commit_selected_previews_once() -> dict:
    return preview_sync.manual_commit_selected_previews_once(_preview_sync_hooks())
def _send_shape_key_mesh_full_update_on_mode_exit(obj, reason: str = "mode_exit_shape_key_full_update") -> bool:
    return preview_sync.send_shape_key_mesh_full_update_on_mode_exit(_preview_sync_hooks(), obj, reason=reason)
def _send_preview_commit_mesh_for_object(obj, reason: str = "mode_exit", mesh_source_override: str | None = None, rebuild_reason: str | None = None) -> bool:
    return preview_sync.send_preview_commit_mesh_for_object(
        _preview_sync_hooks(),
        obj,
        reason=reason,
        mesh_source_override=mesh_source_override,
        rebuild_reason=rebuild_reason,
    )
def _resolve_auto_preview_mesh_source(obj, requested: str | None = None) -> tuple[str, str]:
    return mesh_context.resolve_auto_preview_mesh_source(_preview_mapping_runtime, _mesh_context_hooks(), obj, requested)


def _build_mesh_update_context_for_object(obj, include_extras: bool = False, mesh_source_override: str | None = None, rebuild_reason: str | None = None, force_mesh_content_fingerprint: bool = False, allow_edit_bmesh_uv: bool = False) -> dict | None:
    return mesh_context.build_mesh_update_context_for_object(_preview_mapping_runtime, _mesh_context_hooks(), obj, include_extras=include_extras, mesh_source_override=mesh_source_override, rebuild_reason=rebuild_reason, force_mesh_content_fingerprint=force_mesh_content_fingerprint, allow_edit_bmesh_uv=allow_edit_bmesh_uv)



def _mesh_content_fingerprints_from_update_context(context: dict | None) -> tuple[str | None, str | None, str]:
    if not isinstance(context, dict):
        return None, None, ""
    local_full = str(context.get("local_mesh_content_fingerprint") or "").strip()
    local_no_uv = str(context.get("local_mesh_content_fingerprint_no_uv") or "").strip()
    scope = str(context.get("local_mesh_content_fingerprint_scope") or "").strip()
    if local_full or local_no_uv:
        return local_full or None, local_no_uv or None, scope

    full = str(context.get("mesh_content_fingerprint") or context.get("meshContentFingerprint") or "").strip()
    no_uv = str(context.get("mesh_content_fingerprint_no_uv") or context.get("meshContentFingerprintNoUv") or "").strip()
    scope = str(context.get("mesh_content_fingerprint_scope") or context.get("meshContentFingerprintScope") or "").strip()
    if full or no_uv:
        return full or None, no_uv or None, scope

    mesh_content = context.get("mesh_content") or {}
    prebuilt_binary = context.get("prebuilt_binary") if isinstance(context.get("prebuilt_binary"), dict) else None
    try:
        vertices = mesh_content.get("vertices")
        triangles = mesh_content.get("triangles") or mesh_content.get("indices")
        normals = mesh_content.get("normals")
        uv = mesh_content.get("uv") if mesh_content.get("uv") is not None else mesh_content.get("uv0")
        color0 = mesh_content.get("color0")
        submeshes = mesh_content.get("subMeshes") or []
        full = compute_mesh_content_fingerprint(
            mesh_content,
            prebuilt_binary,
            vertices=vertices,
            triangles=triangles,
            normals=normals,
            uv=uv,
            color0=color0,
            submeshes=submeshes,
        )
        no_uv = compute_mesh_content_fingerprint(
            mesh_content,
            prebuilt_binary,
            vertices=vertices,
            triangles=triangles,
            normals=normals,
            color0=color0,
            submeshes=submeshes,
            include_uv=False,
        )
        return full or None, no_uv or None, scope
    except Exception:
        return None, None, scope


def _store_mesh_content_baseline_from_context(obj, context: dict | None, *, reason: str) -> None:
    if obj is None:
        return
    full, no_uv, _ = _mesh_content_fingerprints_from_update_context(context)
    if full or no_uv:
        try:
            set_mesh_content_fingerprint_baseline(obj, full, no_uv, reason=reason)
        except Exception as exc:
            log_exception(
                "SceneSync",
                "mesh_content_baseline_exception",
                exc,
                fields={"pairId": _pair_id_for_object_readonly(obj), "reason": reason},
            )


def _record_sent_material_refs(obj, context: dict | None, *, reason: str) -> tuple[str, ...]:
    refs = tuple((context or {}).get("material_refs") or [])
    if obj is None or not isinstance(context, dict):
        return refs
    try:
        pair_id = _pair_id_for_object_readonly(obj)
        if pair_id:
            _material_runtime.refs_by_pair[pair_id] = refs
        set_material_slots_baseline(obj, refs, reason=reason)
    except Exception as exc:
        log_exception(
            "SceneSync",
            "material_baseline_exception",
            exc,
            fields={"pairId": _pair_id_for_object_readonly(obj), "reason": reason},
        )
    return refs


def _send_mesh_update_with_materials(context: dict) -> dict:
    return material_sync.send_mesh_update_with_materials(
        _material_runtime,
        context,
        send_mesh_update=send_mesh_update_once,
    )


def _preview_matches_mesh_content_baseline(obj, context: dict | None) -> tuple[bool, str]:
    if isinstance(context, dict) and "material_refs" in context:
        current_material_signature = material_slots_signature_from_refs(context.get("material_refs") or [])
        material_baseline = get_material_slots_baseline(obj)
        if material_baseline != current_material_signature:
            return False, "material_refs"
    full, no_uv, scope = _mesh_content_fingerprints_from_update_context(context)
    baseline_full = get_mesh_content_fingerprint_baseline(obj)
    baseline_no_uv = get_mesh_content_fingerprint_no_uv_baseline(obj)
    if scope == "no_uv":
        if no_uv and baseline_no_uv and no_uv == baseline_no_uv:
            return True, "no_uv"
        return False, "no_uv"
    if full and baseline_full and full == baseline_full:
        return True, "full"
    return False, scope or "full"


def _pump_evaluated_mesh_rebuilds(now: float) -> None:
    preview_sync.pump_evaluated_mesh_rebuilds(_evaluated_preview_runtime, _preview_sync_hooks(), now)
def _emit_auto_sync_state(enabled: bool) -> None:
    global _last_auto_sync_state_signature

    session = get_session()
    if session is None:
        return

    preview_idle_commit_seconds = round(_get_preview_idle_commit_seconds(), 3)
    signature = f"{bool(enabled)}|{preview_idle_commit_seconds:.3f}"
    try:
        result = session.send_auto({
            "type": "scene_sync.auto_sync_state",
            "timestamp": int(time.time()),
            "enabled": bool(enabled),
            "previewIdleCommitSeconds": preview_idle_commit_seconds,
        })
        if result.ok:
            _last_auto_sync_state_signature = signature
        else:
            error = str(getattr(result, "error", None) or "send_failed")
            if error in {"session_not_connected", "handshake_not_confirmed"}:
                trace(
                    "SceneSync",
                    "auto_sync_state_deferred",
                    lambda: "Deferred auto-sync state publication until the session is ready.",
                    lambda: {"reason": error, "enabled": bool(enabled)},
                )
            else:
                warn(
                    "SceneSync",
                    "auto_sync_state_failed",
                    error,
                    {"enabled": bool(enabled)},
                )
    except Exception as exc:
        if str(exc) in {"session_not_connected", "handshake_not_confirmed"}:
            trace(
                "SceneSync",
                "auto_sync_state_deferred",
                lambda: "Deferred auto-sync state publication until the session is ready.",
                lambda: {"reason": str(exc), "enabled": bool(enabled)},
            )
        else:
            log_exception(
                "SceneSync",
                "auto_sync_state_exception",
                exc,
                fields={"enabled": bool(enabled)},
            )


def _emit_auto_sync_state_if_changed(enabled: bool) -> None:
    preview_idle_commit_seconds = round(_get_preview_idle_commit_seconds(), 3)
    signature = f"{bool(enabled)}|{preview_idle_commit_seconds:.3f}"
    if signature != _last_auto_sync_state_signature:
        _emit_auto_sync_state(enabled)


def publish_current_auto_sync_state() -> None:
    _emit_auto_sync_state_if_changed(_get_sync_enabled())



def _fingerprint_view_payload(payload: dict) -> str:
    values = []
    for key in ("pivot", "forward", "up"):
        values.extend(round(float(v), 4) for v in (payload.get(key) or []))
    values.append(round(float(payload.get("distance") or 0.0), 4))
    values.append(round(float(payload.get("lens") or 0.0), 4))
    values.append(round(float(payload.get("viewScale") or 1.0), 4))
    values.append(1 if payload.get("isOrthographic") else 0)
    values.append(str(payload.get("viewMode") or ""))
    return "|".join(str(v) for v in values)


def _try_auto_sync_view_state(now: float) -> None:
    global _last_view_tick_time, _last_view_fingerprint
    if not _get_view_sync_enabled():
        _last_view_fingerprint = None
        return
    hz = _get_view_sync_hz()
    if hz <= 0:
        return
    interval = 1.0 / hz
    if (now - _last_view_tick_time) < interval:
        return
    _last_view_tick_time = now
    try:
        context = build_scene_view_state_context(get_session(), bpy.context)
        payload = context.get("viewStatePayload") or {}
        fingerprint = _fingerprint_view_payload(payload)
        if fingerprint == _last_view_fingerprint:
            return
        result = send_scene_view_state(context)
        if result.ok:
            _last_view_fingerprint = fingerprint
    except ValueError as exc:
        trace(
            "SceneSync",
            "view_state_unavailable",
            lambda: "Skipped view synchronization because no usable 3D view was available.",
            lambda: {"reason": str(exc)},
        )
    except Exception as exc:
        log_exception("SceneSync", "view_state_exception", exc)


def _handle_mode_edge(mode: str, sync_enabled: bool, active_obj, was_mesh_edit_family: bool) -> None:
    in_mesh_edit_family = mode in _MESH_EDIT_FAMILY_MODES
    if sync_enabled and in_mesh_edit_family and not was_mesh_edit_family:
        _clear_preview_fast_cache_for_object(active_obj, reason="mode_enter")
        pair_id = f"pair-{ensure_instance_id(active_obj)}" if active_obj is not None else None
        trace(
            "SceneSync",
            "mesh_edit_mode_entered",
            lambda: "Entered mesh edit mode without treating the mode change as content.",
            lambda: {"pairId": pair_id},
        )
        return
    if not (sync_enabled and mode == "OBJECT" and was_mesh_edit_family):
        return
    pair_id = f"pair-{ensure_instance_id(active_obj)}" if active_obj is not None else None
    preview_result = None
    try:
        if (
            active_obj is not None
            and getattr(active_obj, "type", None) == "MESH"
            and bool(active_obj.get(AUTO_SYNC_READY_KEY, False))
        ):
            active_has_shape_keys = _mesh_has_shape_keys(active_obj)
            preview_reason = "mode_exit_shape_key_preview" if active_has_shape_keys else "mode_exit_uv_preview"
            preview_result = _send_mesh_preview_with_live_uv(active_obj, reason=preview_reason, require_uv=False)
            if active_has_shape_keys and bool(preview_result.get("ok") if isinstance(preview_result, dict) else False):
                _send_shape_key_weights_if_changed(active_obj, force=True, reason="mode_exit_shape_key_preview_after_mesh_update")
        trace(
            "SceneSync",
            "mesh_edit_mode_exit_processed",
            lambda: "Processed the mesh preview after leaving edit mode.",
            lambda: {
                "pairId": pair_id,
                "ok": preview_result.get("ok") if isinstance(preview_result, dict) else False,
                "reason": preview_result.get("reason") if isinstance(preview_result, dict) else "not_sent",
            },
        )
    finally:
        _clear_preview_fast_cache_for_object(active_obj, reason="mode_exit_commit")


def _handle_sync_edge(sync_enabled: bool) -> None:
    if sync_enabled and not _last_sync_enabled:
        _reset_auto_sync_transient_dirty(reason="sync_enabled")
        _reset_evaluated_runtime_state(reason="sync_enabled")
        _capture_evaluated_modifier_signature_baseline(reason="sync_enabled")
        _init_lifecycle_baseline()
        _emit_auto_sync_state_if_changed(True)
    elif not sync_enabled and _last_sync_enabled:
        _reset_auto_sync_transient_dirty(reason="sync_disabled")
        _reset_lifecycle_runtime_state()
        _reset_evaluated_runtime_state(reason="sync_disabled")
        _emit_auto_sync_state_if_changed(False)
        trace(
            "SceneSync",
            "lifecycle_baseline_reset",
            lambda: "Reset lifecycle baselines after auto sync was disabled.",
        )


def _run_enabled_background_pumps(now: float) -> None:
    global _last_lifecycle_tick_time
    _emit_auto_sync_state_if_changed(True)
    try:
        structure_watch.poll_active_uv_channels_signature(_mesh_structure_runtime, _structure_watch_hooks(), now)
        structure_watch.poll_active_color_attributes_signature(_mesh_structure_runtime, _structure_watch_hooks(), now)
        structure_watch.poll_active_shape_key_structure_signature(_mesh_structure_runtime, _structure_watch_hooks(), now)
        material_sync.poll_active_slots_signature(_material_runtime, _material_sync_hooks(), now)
        _pump_evaluated_mesh_rebuilds(now)
    except Exception as exc:
        signature = f"{type(exc).__name__}:{exc}"
        last_logged_at = _background_pump_error_last_at_by_signature.get(signature)
        if last_logged_at is None or (now - float(last_logged_at)) >= 10.0:
            _background_pump_error_last_at_by_signature[signature] = now
            log_exception("SceneSync", "background_pump_exception", exc)
    material_sync.pump_pending_sends(_material_runtime, now)
    material_sync.pump_ref_resends(_material_runtime, _material_sync_hooks(), now)
    reconcile_hz = _get_lifecycle_reconcile_hz()
    if reconcile_hz > 0:
        reconcile_interval = 1.0 / reconcile_hz
        if (now - _last_lifecycle_tick_time) >= reconcile_interval:
            _run_lifecycle_tick(now, do_full_diff=True)
            _last_lifecycle_tick_time = now


def _run_object_mode_tick(now: float, sync_enabled: bool, active_obj) -> float:
    global _last_tick_time
    if not sync_enabled:
        return 0.2
    if not _should_tick(now):
        return 0.05
    object_state.pump_dirty_queue(_object_state_runtime, _object_state_hooks(), now)
    object_state.pump_motion_bursts(_object_state_runtime, _object_state_hooks(), now)
    if object_state.should_run_active_fallback(_object_state_runtime, _object_state_hooks(), active_obj, now):
        object_state.send_runtime_state_for_object(
            _object_state_runtime,
            _object_state_hooks(),
            active_obj,
            source="active_fallback_safety",
        )
    _last_tick_time = now
    if active_obj is not None and getattr(active_obj, "type", None) == "MESH":
        material_sync.send_slot_mesh_update(
            _material_runtime,
            _material_sync_hooks(),
            active_obj,
            force=False,
            reason="auto_object_mode",
        )
        _send_shape_key_weights_if_changed(active_obj, force=False, reason="auto_object_mode")
        _send_active_mesh_full_preview_if_dirty()
    hz = _get_object_sync_hz()
    if hz <= 0:
        return 0.5
    interval = 1.0 / hz
    if _object_state_runtime.dirty_queue or _object_state_runtime.motion_burst_until_by_pair:
        burst_interval = 1.0 / max(1.0, OBJECT_STATE_MOTION_BURST_HZ)
        return min(interval, burst_interval)
    return interval


def _run_mesh_edit_mode_tick(now: float, sync_enabled: bool, active_obj) -> float:
    global _last_mesh_tick_time
    if not sync_enabled:
        return 0.2
    mesh_hz = _get_mesh_sync_hz()
    if mesh_hz <= 0:
        return 0.5
    interval = 1.0 / mesh_hz
    active_has_shape_keys = active_obj is not None and getattr(active_obj, "type", None) == "MESH" and _mesh_has_shape_keys(active_obj)
    if active_has_shape_keys:
        try:
            pair_id = f"pair-{ensure_instance_id(active_obj)}"
        except Exception:
            pair_id = ""
        if pair_id and pair_id not in _shape_key_runtime.edit_skip_logged_by_pair:
            _shape_key_runtime.edit_skip_logged_by_pair.add(pair_id)
            trace(
                "SceneSync",
                "shape_key_edit_preview_skipped",
                lambda: "Skipped edit-mode preview for a shape-key mesh.",
                lambda: {"pairId": pair_id},
            )
        return max(0.1, interval)
    if active_obj is not None and getattr(active_obj, "type", None) == "MESH":
        try:
            mesh_ref = f"mesh-{ensure_mesh_asset_id_for_object(active_obj)}" if getattr(active_obj, "data", None) is not None else None
            if mesh_ref:
                material_sync.send_slot_mesh_update(
                    _material_runtime,
                    _material_sync_hooks(),
                    active_obj,
                    force=False,
                    reason="auto_edit_mode",
                )
        except Exception:
            pass
    if (now - _last_mesh_tick_time) < interval:
        return 0.05
    _send_active_mesh_full_preview_if_dirty()
    _last_mesh_tick_time = now
    return interval


def _tick_impl() -> float:
    """Timer callback implementation. Returns the next call delay."""
    global _last_mode, _last_sync_enabled

    if bpy is None or bpy.context is None:
        return 0.5

    now = time.time()
    _try_auto_sync_view_state(now)
    mode = _get_current_mode()
    sync_enabled = _get_sync_enabled()

    active_obj = getattr(bpy.context, "active_object", None)
    was_mesh_edit_family = _last_mode in _MESH_EDIT_FAMILY_MODES
    # Consume the mode edge before running preview side effects. If an
    # unexpected exception escapes an edge handler, the next timer tick must
    # not replay the same enter/exit operation.
    _last_mode = mode
    _handle_mode_edge(mode, sync_enabled, active_obj, was_mesh_edit_family)
    _handle_sync_edge(sync_enabled)
    if sync_enabled:
        _run_enabled_background_pumps(now)
    _last_sync_enabled = sync_enabled
    if mode == "OBJECT":
        return _run_object_mode_tick(now, sync_enabled, active_obj)
    if mode in _MESH_EDIT_FAMILY_MODES:
        return _run_mesh_edit_mode_tick(now, sync_enabled, active_obj)
    return 0.2


def _tick() -> float:
    try:
        delay = _tick_impl()
        return 0.5 if delay is None else delay
    except Exception as exc:
        report_boundary_exception(
            "auto_sync_timer",
            exc,
            message=f"[vNext][AutoSyncTimer] tick_error error={exc}",
        )
        return 0.5


def register_controller() -> None:
    """Register the sync controller timer. Called from addon register()."""
    global _timer_registered

    if bpy is None:
        return

    if not is_controller_timer_registered():
        bpy.app.timers.register(_tick, persistent=False)
    _timer_registered = is_controller_timer_registered()
    _register_dirty_probe()


def unregister_controller() -> None:
    """Unregister the sync controller timer. Called from addon unregister()."""
    global _timer_registered

    if bpy is None:
        return

    if is_controller_timer_registered():
        try:
            bpy.app.timers.unregister(_tick)
        except Exception:
            pass
        _timer_registered = False
    _unregister_dirty_probe()


def is_controller_timer_registered() -> bool:
    if bpy is None:
        return False
    try:
        return bool(bpy.app.timers.is_registered(_tick))
    except Exception:
        return False


def is_dirty_probe_registered() -> bool:
    if bpy is None:
        return False
    try:
        return _dirty_probe_depsgraph_update in bpy.app.handlers.depsgraph_update_post
    except Exception:
        return False


def _active_object_baseline_state() -> dict:
    out = {
        "hasActiveObject": False,
        "objectName": "",
        "objectType": "",
        "autoSyncReady": False,
        "meshSource": "n/a",
        "meshSourceReason": "n/a",
        "modifierBaseline": "n/a",
        "materialSlotsBaseline": "n/a",
        "uvChannelsBaseline": "n/a",
        "uvChannelCount": 0,
        "uvChannelNames": [],
        "colorAttributesBaseline": "n/a",
        "colorAttributeCount": 0,
        "colorAttributeNames": [],
        "colorAttributeExportName": "",
    }
    if bpy is None or bpy.context is None:
        return out
    obj = getattr(bpy.context, "active_object", None)
    if obj is None:
        return out
    out["hasActiveObject"] = True
    out["objectName"] = str(getattr(obj, "name", "") or "")
    out["objectType"] = str(getattr(obj, "type", "") or "")
    out["autoSyncReady"] = bool(obj.get(AUTO_SYNC_READY_KEY, False))
    if getattr(obj, "type", None) != "MESH":
        return out
    try:
        mesh_source, mesh_source_reason = _resolve_auto_preview_mesh_source(obj, "evaluated" if _object_has_visible_modifiers(obj) else "original")
        out["meshSource"] = mesh_source
        out["meshSourceReason"] = mesh_source_reason
    except Exception as exc:
        out["meshSource"] = "error"
        out["meshSourceReason"] = str(exc)
    try:
        baseline = get_modifier_stack_baseline(obj)
        current = modifier_stack_signature(obj)
        if baseline is None:
            out["modifierBaseline"] = "missing"
        else:
            out["modifierBaseline"] = "clean" if baseline == current else "dirty"
    except Exception as exc:
        out["modifierBaseline"] = f"error:{exc}"
    try:
        refs = material_sync.collect_refs_for_object(obj)
        baseline = get_material_slots_baseline(obj)
        current = material_slots_signature_from_refs(refs)
        if baseline is None:
            out["materialSlotsBaseline"] = "missing"
        else:
            out["materialSlotsBaseline"] = "clean" if baseline == current else "dirty"
    except Exception as exc:
        out["materialSlotsBaseline"] = f"error:{exc}"
    try:
        baseline = get_uv_channels_baseline(obj)
        current = uv_channels_signature(obj)
        if baseline is None:
            out["uvChannelsBaseline"] = "missing"
        else:
            out["uvChannelsBaseline"] = "clean" if baseline == current else "dirty"
    except Exception as exc:
        out["uvChannelsBaseline"] = f"error:{exc}"
    try:
        mesh = getattr(obj, "data", None)
        layers = list(getattr(mesh, "uv_layers", []) or []) if mesh is not None else []
        out["uvChannelCount"] = len(layers)
        out["uvChannelNames"] = [str(getattr(layer, "name", "") or "") for layer in layers[:8]]
    except Exception:
        pass
    try:
        baseline = get_color_attributes_baseline(obj)
        current = structure_watch.color_attributes_signature(obj)
        if baseline is None:
            out["colorAttributesBaseline"] = "missing"
        else:
            out["colorAttributesBaseline"] = "clean" if baseline == current else "dirty"
    except Exception as exc:
        out["colorAttributesBaseline"] = f"error:{exc}"
    try:
        mesh = getattr(obj, "data", None)
        attrs = list(getattr(mesh, "color_attributes", []) or []) if mesh is not None else []
        out["colorAttributeCount"] = len(attrs)
        out["colorAttributeNames"] = [str(getattr(attr, "name", "") or "") for attr in attrs[:8]]
        exported = mesh_context.collect_mesh_color_attribute_values_for_preview(mesh) if mesh is not None else None
        if isinstance(exported, dict):
            out["colorAttributeExportName"] = str(exported.get("name") or "")
    except Exception:
        pass
    return out


def get_controller_state(*, include_active_object_baseline: bool = True) -> dict:
    """Return a minimal controller state snapshot for UI observability."""
    state = {
        "timer_registered": is_controller_timer_registered(),
        "dirty_probe_registered": is_dirty_probe_registered(),
        "sync_enabled": _get_sync_enabled(),
        "object_sync_hz": _get_object_sync_hz(),
        "mesh_sync_hz": _get_mesh_sync_hz(),
        "view_sync_enabled": _get_view_sync_enabled(),
        "view_sync_hz": _get_view_sync_hz(),
        "view_sync_scale": _get_view_sync_scale(),
        "last_view_tick_time": _last_view_tick_time,
        "current_mode": _get_current_mode(),
        "last_tick_time": _last_tick_time,
        "last_mesh_tick_time": _last_mesh_tick_time,
        "lifecycle_reconcile_hz": _get_lifecycle_reconcile_hz(),
        "last_lifecycle_tick_time": _last_lifecycle_tick_time,
        "object_dirty_queue_count": len(_object_state_runtime.dirty_queue),
        "object_motion_burst_pair_count": len(_object_state_runtime.motion_burst_until_by_pair),
        "object_motion_burst_hz": OBJECT_STATE_MOTION_BURST_HZ,
        "object_motion_burst_seconds": OBJECT_STATE_MOTION_BURST_SECONDS,
        "object_active_fallback_hz": OBJECT_STATE_ACTIVE_FALLBACK_HZ,
        "object_active_fallback_tracked_count": len(_object_state_runtime.active_fallback_next_at_by_pair),
    }
    if include_active_object_baseline:
        state["active_object_baseline"] = _active_object_baseline_state()
    return state
