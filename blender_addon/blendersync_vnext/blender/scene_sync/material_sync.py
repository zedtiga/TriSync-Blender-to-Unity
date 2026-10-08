from __future__ import annotations

from dataclasses import dataclass
from typing import Callable
import time

from blender.asset_registry import is_asset_known, mark_assets_known
from blender.common.log import exception as log_exception, trace, warn
from blender.identity import ensure_instance_id, ensure_unique_asset_id
from blender.material_resource.slots import collect_material_export_snapshot, collect_materials_for_export
from blender.material_resource.content_v1 import build_material_content_v1
from blender.scene_sync.baseline import (
    get_material_slots_baseline,
    material_slots_signature_from_refs,
    set_material_slots_baseline,
)
from blender.scene_sync.runtime_state import MaterialRuntimeState
from blender.resource_update.entrypoints import send_mesh_update_once
from blender.transport.entrypoints import send_selected_resources
from blender.ui.state_view import get_session

try:
    import bpy  # type: ignore
except ImportError:
    bpy = None


AUTO_SYNC_READY_KEY = "blendersync_auto_sync_ready"
MATERIAL_AUTO_SEND_MAX_PER_TICK = 1
MATERIAL_AUTO_SEND_RETRY_SECONDS = 2.0
MATERIAL_REF_RESEND_DELAYS_SECONDS = (0.35, 1.0, 2.0)
MATERIAL_REF_RESEND_MAX_PER_TICK = 4
MATERIAL_SLOTS_SIGNATURE_POLL_SECONDS = 0.25


@dataclass(frozen=True)
class MaterialSyncHooks:
    get_sync_enabled: Callable[[], bool]
    get_current_mode: Callable[[], str]
    pair_id_for_object: Callable[[object], str | None]
    find_object_by_pair_id: Callable[[str], object | None]
    send_slot_mesh_update: Callable[..., bool]
    build_mesh_context: Callable[..., dict | None]
    store_mesh_content_baseline: Callable[..., None]
    send_shape_key_weights: Callable[..., bool]
    object_uses_evaluated_materials: Callable[[object], bool] = lambda _obj: False


def remove_pair_from_runtime(runtime: MaterialRuntimeState, pair_id: str | None, reason: str = "unknown") -> dict[str, int]:
    pair_id = str(pair_id or "").strip()
    if not pair_id:
        return {"waitingRefs": 0, "preservedBundles": 0, "resendRefs": 0}
    result = runtime.remove_pair_from_pending(pair_id)
    if result["waitingRefs"] or result["resendRefs"]:
        trace(
            "MaterialSync",
            "pair_state_cleared",
            lambda: "Cleared pending material state for an object pair.",
            lambda: {"pairId": pair_id, "reason": reason, **result},
        )
    return result


def is_material_ref_known(material_ref: str) -> bool:
    ref = str(material_ref or "").strip()
    return True if not ref else is_asset_known(ref)


def ensure_material_asset_id(mat) -> str:
    materials = getattr(getattr(bpy, "data", None), "materials", None) if bpy is not None else None
    return ensure_unique_asset_id(mat, materials, label="material")


def _collect_material_refs_from_materials(materials) -> tuple[str, ...]:
    refs = []
    for mat in list(materials or []):
        if mat is None:
            refs.append("")
            continue
        try:
            refs.append(f"mat-{ensure_material_asset_id(mat)}")
        except Exception:
            refs.append("")
    return tuple(refs)


def collect_refs_for_export(obj, *, mesh=None, evaluated_mesh: bool = False) -> tuple[str, ...]:
    if evaluated_mesh:
        materials = collect_material_export_snapshot(
            obj,
            mesh,
            geometry_is_evaluated=True,
        ).materials
    else:
        materials = collect_materials_for_export(obj, mesh=mesh, evaluated_mesh=False)
    return _collect_material_refs_from_materials(
        materials
    )


def build_material_contents_for_export(
    obj,
    *,
    mesh=None,
    evaluated_mesh: bool = False,
    unknown_only: bool = True,
) -> list[dict]:
    contents = []
    seen_refs: set[str] = set()
    if evaluated_mesh:
        materials = collect_material_export_snapshot(
            obj,
            mesh,
            geometry_is_evaluated=True,
        ).materials
    else:
        materials = collect_materials_for_export(obj, mesh=mesh, evaluated_mesh=False)
    for mat in materials:
        if mat is None:
            continue
        try:
            material_ref = f"mat-{ensure_material_asset_id(mat)}"
            if material_ref in seen_refs or (unknown_only and is_material_ref_known(material_ref)):
                continue
            seen_refs.add(material_ref)
            contents.append(build_material_content_v1(mat))
        except Exception as exc:
            log_exception(
                "MaterialSync",
                "material_content_build_exception",
                exc,
                fields={
                    "objectName": getattr(obj, "name", "<unnamed>"),
                    "materialName": getattr(mat, "name", "<unnamed>"),
                },
            )
    return contents


def queue_bundle_if_unknown(
    runtime: MaterialRuntimeState,
    material_ref: str,
    bundle: dict,
    reason: str = "auto_material_slot",
    pair_id: str | None = None,
) -> bool:
    ref = str(material_ref or "").strip()
    if not ref or is_material_ref_known(ref):
        return False
    deps = bundle.get("deps") or []
    material_contents = bundle.get("materialContents") or []
    if not deps and not material_contents:
        return False
    if pair_id:
        runtime.waiting_pair_ids_by_ref.setdefault(ref, set()).add(str(pair_id))
    existing = runtime.pending_bundles_by_ref.get(ref)
    if existing is None:
        runtime.pending_bundles_by_ref[ref] = {
            "materialRef": ref,
            "bundle": bundle,
            "reason": reason,
            "queuedAt": time.time(),
        }
        trace(
            "MaterialSync",
            "material_queued",
            lambda: "Queued an unknown material for synchronization.",
            lambda: {
                "materialRef": ref,
                "dependencyCount": len(deps),
                "materialContentCount": len(material_contents),
                "reason": reason,
            },
        )
    else:
        existing["bundle"] = bundle
        existing["reason"] = reason
    return True


def queue_unknown_contents_for_object(runtime: MaterialRuntimeState, obj, refs, pair_id: str | None, reason: str) -> int:
    if obj is None or bpy is None:
        return 0
    try:
        slots = list(getattr(obj, "material_slots", []) or [])
    except Exception:
        slots = []
    queued = 0
    for slot in slots:
        mat = getattr(slot, "material", None)
        if mat is None:
            continue
        try:
            material_ref = f"mat-{ensure_material_asset_id(mat)}"
        except Exception:
            continue
        if material_ref not in set(refs or []) or is_material_ref_known(material_ref):
            continue
        try:
            material_content = build_material_content_v1(mat)
        except Exception as exc:
            log_exception(
                "MaterialSync",
                "material_content_build_exception",
                exc,
                fields={"materialRef": material_ref, "materialName": getattr(mat, "name", "<unnamed>")},
            )
            continue
        if queue_bundle_if_unknown(
            runtime,
            material_ref,
            {"deps": [], "materialContents": [material_content]},
            reason=reason,
            pair_id=pair_id,
        ):
            queued += 1
    return queued


def queue_unknown_contents_from_context(runtime: MaterialRuntimeState, context: dict, pair_id: str | None, reason: str) -> int:
    if not isinstance(context, dict):
        return 0
    refs = {str(ref).strip() for ref in (context.get("material_refs") or []) if str(ref).strip()}
    queued = 0
    for material_content in context.get("material_contents", []) or []:
        if not isinstance(material_content, dict):
            continue
        material_ref = str(material_content.get("materialRef") or "").strip()
        if material_ref not in refs:
            continue
        if queue_bundle_if_unknown(
            runtime,
            material_ref,
            {"deps": [], "materialContents": [material_content]},
            reason=reason,
            pair_id=pair_id,
        ):
            queued += 1
    return queued


def send_mesh_update_with_materials(
    runtime: MaterialRuntimeState,
    context: dict,
    *,
    send_mesh_update: Callable[[dict], dict] | None = None,
) -> dict:
    pair_entry = (context or {}).get("pair_entry") or {}
    pair_id = str(pair_entry.get("pairId") or "").strip()
    reason = str((context or {}).get("rebuild_reason") or "mesh_update")
    queued_materials = queue_unknown_contents_from_context(runtime, context, pair_id, reason)
    if queued_materials > 0:
        refs = {str(ref).strip() for ref in (context.get("material_refs") or []) if str(ref).strip()}
        pump_pending_sends(
            runtime,
            time.time(),
            max_sends=queued_materials,
            material_refs=refs,
            schedule_resends=False,
        )
    return (send_mesh_update or send_mesh_update_once)(context)


def force_resend_refs_for_pairs(
    hooks: MaterialSyncHooks,
    pair_ids: set[str],
    material_ref: str,
    reason: str = "material_auto_send_ready",
) -> int:
    resent = 0
    for pair_id in sorted(pair_ids or set()):
        obj = hooks.find_object_by_pair_id(pair_id)
        if obj is not None and hooks.send_slot_mesh_update(obj, force=True, reason=reason):
            resent += 1
    if pair_ids:
        trace(
            "MaterialSync",
            "material_references_resent",
            lambda: "Resent object references after material synchronization.",
            lambda: {
                "materialRef": material_ref,
                "requestedPairCount": len(pair_ids),
                "resentCount": resent,
                "reason": reason,
            },
        )
    return resent


def schedule_ref_resends(runtime: MaterialRuntimeState, material_ref: str, pair_ids: set[str], now: float) -> None:
    ref = str(material_ref or "").strip()
    pairs = {str(pair_id).strip() for pair_id in (pair_ids or set()) if str(pair_id).strip()}
    if not ref or not pairs:
        return
    runtime.pending_resends_by_ref[ref] = {
        "materialRef": ref,
        "pairIds": pairs,
        "delays": list(MATERIAL_REF_RESEND_DELAYS_SECONDS),
        "nextAt": now + float(MATERIAL_REF_RESEND_DELAYS_SECONDS[0]),
        "attempt": 0,
    }
    trace(
        "MaterialSync",
        "reference_resends_scheduled",
        lambda: "Scheduled delayed material-reference resends.",
        lambda: {"materialRef": ref, "pairCount": len(pairs)},
    )


def pump_ref_resends(runtime: MaterialRuntimeState, hooks: MaterialSyncHooks, now: float) -> int:
    pumped = 0
    for material_ref in list(runtime.pending_resends_by_ref.keys()):
        if pumped >= MATERIAL_REF_RESEND_MAX_PER_TICK:
            break
        entry = runtime.pending_resends_by_ref.get(material_ref) or {}
        if now < float(entry.get("nextAt", 0.0) or 0.0):
            continue
        pair_ids = set(entry.get("pairIds") or set())
        attempt = int(entry.get("attempt", 0) or 0)
        force_resend_refs_for_pairs(
            hooks,
            pair_ids,
            material_ref,
            reason=f"material_auto_send_ready_delayed_{attempt + 1}",
        )
        pumped += 1
        delays = list(entry.get("delays") or [])
        next_attempt = attempt + 1
        if next_attempt >= len(delays):
            runtime.pending_resends_by_ref.pop(material_ref, None)
            continue
        entry["attempt"] = next_attempt
        entry["nextAt"] = now + float(delays[next_attempt])
        runtime.pending_resends_by_ref[material_ref] = entry
    return pumped


def pump_pending_sends(
    runtime: MaterialRuntimeState,
    now: float,
    max_sends: int | None = None,
    material_refs: set[str] | None = None,
    schedule_resends: bool = True,
) -> int:
    if runtime.send_inflight or not runtime.pending_bundles_by_ref:
        return 0
    session = get_session()
    if session is None:
        return 0
    limit = MATERIAL_AUTO_SEND_MAX_PER_TICK if max_sends is None else max(0, int(max_sends))
    if limit <= 0:
        return 0
    target_refs = {str(ref).strip() for ref in (material_refs or set()) if str(ref).strip()}
    sent = 0
    for material_ref in list(runtime.pending_bundles_by_ref.keys()):
        if sent >= limit:
            break
        if target_refs and material_ref not in target_refs:
            continue
        if is_material_ref_known(material_ref):
            waiting_pairs = runtime.clear_pending_send(material_ref)
            if schedule_resends:
                schedule_ref_resends(runtime, material_ref, waiting_pairs, now)
            continue
        if now < float(runtime.retry_after_by_ref.get(material_ref, 0.0) or 0.0):
            continue
        entry = runtime.pending_bundles_by_ref.get(material_ref) or {}
        bundle = entry.get("bundle") or {}
        deps = bundle.get("deps") or []
        material_contents = bundle.get("materialContents") or []
        if not deps and not material_contents:
            runtime.clear_pending_send(material_ref)
            continue
        context = {
            "session": session,
            "packageId": f"pkg-auto-material-{int(now * 1000)}",
            "sendMode": "manual_resource_resend",
            "triggerType": "auto_material_slot",
            "correlationId": f"auto-mat-{int(now * 1000)}",
            "selectedObjectCount": 0,
            "resourceCountEstimate": len(deps) + len(material_contents),
            "selected": deps,
            "scene": [],
            "materialContents": material_contents,
        }
        try:
            runtime.send_inflight = True
            result = send_selected_resources(context)
        except Exception as exc:
            result = None
            log_exception(
                "MaterialSync",
                "material_send_exception",
                exc,
                fields={"materialRef": material_ref},
            )
        finally:
            runtime.send_inflight = False
        if result is not None and getattr(result, "ok", False):
            mark_assets_known([material_ref])
            waiting_pairs = runtime.clear_pending_send(material_ref)
            sent += 1
            trace(
                "MaterialSync",
                "material_sent",
                lambda: "Sent an automatically discovered material.",
                lambda: {
                    "materialRef": material_ref,
                    "dependencyCount": len(deps),
                    "materialContentCount": len(material_contents),
                },
            )
            if schedule_resends:
                schedule_ref_resends(runtime, material_ref, waiting_pairs, now)
        else:
            err = getattr(result, "error", None) if result is not None else "material_auto_send_failed"
            runtime.retry_after_by_ref[material_ref] = now + MATERIAL_AUTO_SEND_RETRY_SECONDS
            if result is not None:
                warn(
                    "MaterialSync",
                    "material_send_deferred",
                    err,
                    {"materialRef": material_ref, "retryAfterSeconds": MATERIAL_AUTO_SEND_RETRY_SECONDS},
                )
    return sent


def collect_refs_for_object(obj) -> tuple[str, ...]:
    if obj is None:
        return tuple()
    return collect_refs_for_export(obj)


def send_refs_if_changed(runtime: MaterialRuntimeState, hooks: MaterialSyncHooks, obj, force: bool = False, reason: str = "auto_sync") -> bool:
    if obj is None or getattr(obj, "type", None) != "MESH" or not bool(obj.get(AUTO_SYNC_READY_KEY, False)):
        return False


    session = get_session()
    if session is None:
        return False
    pair_id = f"pair-{ensure_instance_id(obj)}"
    refs = collect_refs_for_object(obj)
    queued_materials = queue_unknown_contents_for_object(runtime, obj, refs, pair_id, reason)
    if queued_materials > 0:
        pump_pending_sends(runtime, time.time(), max_sends=queued_materials, material_refs={ref for ref in refs if ref}, schedule_resends=False)
    previous = runtime.refs_by_pair.get(pair_id)
    if previous is None:
        baseline = get_material_slots_baseline(obj)
        if baseline is not None and baseline == material_slots_signature_from_refs(refs):
            runtime.refs_by_pair[pair_id] = refs
            if not force:
                return False
    if not force and previous == refs:
        return False
    payload = {
        "type": "scene_sync.reference_change",
        "timestamp": int(time.time()),
        "pairId": pair_id,
        "sourceHint": "auto_sync" if hooks.get_sync_enabled() else "manual_sync",
        "resourceKind": "material",
        "resourceRef": next((ref for ref in refs if ref), ""),
        "resourceRefs": list(refs),
        "reason": str(reason or "auto_sync"),
    }
    try:
        send_result = session.send_auto(payload)
        ok = bool(getattr(send_result, "ok", False))
        if ok:
            runtime.refs_by_pair[pair_id] = refs
            set_material_slots_baseline(obj, refs, reason=reason)
        else:
            warn(
                "MaterialSync",
                "material_references_failed",
                getattr(send_result, "error", None) or "send_failed",
                {
                    "pairId": pair_id,
                    "slotCount": len(refs),
                    "nonEmptyCount": sum(1 for ref in refs if ref),
                    "reason": reason,
                },
            )
        return ok
    except Exception as exc:
        log_exception(
            "MaterialSync",
            "material_references_exception",
            exc,
            fields={"pairId": pair_id, "reason": reason},
        )
        return False


def send_slot_mesh_update(
    runtime: MaterialRuntimeState,
    hooks: MaterialSyncHooks,
    obj,
    force: bool = False,
    reason: str = "auto_sync",
) -> bool:
    if obj is None or getattr(obj, "type", None) != "MESH":
        return False
    if not bool(obj.get(AUTO_SYNC_READY_KEY, False)):
        return False

    uses_evaluated_materials = bool(hooks.object_uses_evaluated_materials(obj))
    if uses_evaluated_materials and str(reason or "").startswith((
        "auto_",
        "material_auto_",
        "material_slots_",
    )):
        return False

    pair_id = f"pair-{ensure_instance_id(obj)}"
    refs = collect_refs_for_object(obj)
    if not uses_evaluated_materials:
        queued_materials = queue_unknown_contents_for_object(runtime, obj, refs, pair_id, reason)
        if queued_materials > 0:
            pump_pending_sends(
                runtime,
                time.time(),
                max_sends=queued_materials,
                material_refs={ref for ref in refs if ref},
                schedule_resends=False,
            )
    previous = runtime.refs_by_pair.get(pair_id)
    if not uses_evaluated_materials and previous is None:
        baseline = get_material_slots_baseline(obj)
        if baseline is not None:
            current_signature = material_slots_signature_from_refs(refs)
            if baseline == current_signature:
                runtime.refs_by_pair[pair_id] = refs
                if not force:
                    return False
    if not uses_evaluated_materials and not force and previous == refs:
        return False

    context = hooks.build_mesh_context(
        obj,
        include_extras=True,
        mesh_source_override="evaluated" if uses_evaluated_materials else None,
        rebuild_reason=reason,
    )
    if context is None:
        if uses_evaluated_materials:
            return False
        return send_refs_if_changed(
            runtime,
            hooks,
            obj,
            force=force,
            reason=f"{reason}_mesh_context_missing",
        )
    refs = tuple(context.get("material_refs") or refs)
    context["force_send"] = True
    context["material_refs"] = list(refs)
    try:
        result = send_mesh_update_with_materials(runtime, context)
        ok = bool(result.get("ok"))
        if ok:
            runtime.refs_by_pair[pair_id] = refs
            set_material_slots_baseline(obj, refs, reason=reason)
            hooks.store_mesh_content_baseline(obj, context, reason=reason)
            hooks.send_shape_key_weights(obj, force=True, reason=f"{reason}_after_mesh_update")
        else:
            warn(
                "MaterialSync",
                "material_mesh_update_failed",
                result.get("reason") or "send_failed",
                {
                    "pairId": pair_id,
                    "slotCount": len(refs),
                    "nonEmptyCount": sum(1 for ref in refs if ref),
                    "reason": reason,
                },
            )
        return ok
    except Exception as exc:
        log_exception(
            "MaterialSync",
            "material_mesh_update_exception",
            exc,
            fields={"pairId": pair_id, "reason": reason},
        )
        return False


def poll_active_slots_signature(runtime: MaterialRuntimeState, hooks: MaterialSyncHooks, now: float) -> None:
    if not hooks.get_sync_enabled() or hooks.get_current_mode() != "OBJECT" or bpy is None or bpy.context is None:
        return
    obj = getattr(bpy.context, "active_object", None)
    if obj is None or getattr(obj, "type", None) != "MESH" or not bool(obj.get(AUTO_SYNC_READY_KEY, False)):
        return
    if hooks.object_uses_evaluated_materials(obj):
        return
    pair_id = hooks.pair_id_for_object(obj)
    if not pair_id:
        return
    next_time = float(runtime.slots_poll_next_at_by_pair.get(pair_id, 0.0) or 0.0)
    if now < next_time:
        return
    runtime.slots_poll_next_at_by_pair[pair_id] = now + MATERIAL_SLOTS_SIGNATURE_POLL_SECONDS
    refs = collect_refs_for_object(obj)
    signature = material_slots_signature_from_refs(refs)
    baseline = get_material_slots_baseline(obj)
    previous_refs = runtime.refs_by_pair.get(pair_id)
    previous_signature = material_slots_signature_from_refs(previous_refs) if previous_refs is not None else baseline
    if previous_signature == signature:
        if previous_refs is None:
            runtime.refs_by_pair[pair_id] = refs
        return
    runtime.refs_by_pair[pair_id] = tuple() if previous_refs is None else previous_refs
    hooks.send_slot_mesh_update(obj, force=True, reason="material_slots_signature_changed")
