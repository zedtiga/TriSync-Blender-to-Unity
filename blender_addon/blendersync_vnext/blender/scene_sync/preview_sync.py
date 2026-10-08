from __future__ import annotations

from dataclasses import dataclass
from typing import Callable
from array import array
from pathlib import Path
import tempfile
import time

from blender.common.log import exception as log_exception, trace, warn

try:
    import bmesh  # type: ignore
except ImportError:
    bmesh = None


AUTO_SYNC_READY_KEY = "blendersync_auto_sync_ready"
_EVALUATED_CONTEXT_NOISE_SOURCES = {
    "object_transform_update_noise",
    "object_update_no_modifier_signature_change",
}


@dataclass(frozen=True)
class PreviewSyncHooks:
    get_sync_enabled: Callable[[], bool]
    get_current_mode: Callable[[], str]
    pair_id_for_object: Callable[[object], str | None]
    object_for_pair: Callable[[str | None], object | None]
    should_auto_rebuild: Callable[[object], bool]
    object_has_visible_modifiers: Callable[[object], bool]
    modifier_stack_signature: Callable[[object], str]
    get_evaluated_preview_debounce_seconds: Callable[[], float]
    get_mesh_preview_debounce_seconds: Callable[[], float]
    find_object_by_pair: Callable[[str], object | None]
    build_mesh_context: Callable[..., dict | None]
    send_mesh_update: Callable[[dict], dict]
    store_mesh_content_baseline: Callable[..., None]
    record_sent_material_refs: Callable[..., tuple]
    get_session: Callable[[], object | None]
    ensure_instance_id: Callable[[object], str]
    ensure_mesh_asset_id: Callable[[object], str]
    mesh_has_shape_keys: Callable[[object], bool]
    blendshape_weight_sync_interval_seconds: float
    get_active_object: Callable[[], object | None]
    preview_matches_mesh_content_baseline: Callable[[object, dict | None], tuple[bool, str]]
    send_deferred_object_state: Callable[..., bool]
    get_selected_objects: Callable[[], list | None]
    build_single_object_live_context: Callable[..., dict]
    send_selected_resources: Callable[[dict], object]
    build_mesh_binary_buffers: Callable[..., dict | None]
    compute_mesh_content_fingerprint: Callable[..., str]
    collect_material_refs: Callable[[object], tuple]
    preview_buffer_cleanup_interval_seconds: float
    preview_buffer_ttl_seconds: float
    uv_verbose_enabled: Callable[[], bool]


def preview_buffer_dir(uv_runtime, hooks: PreviewSyncHooks) -> Path:
    root = Path(tempfile.gettempdir()) / "BlenderSyncVNext" / "preview_positions_v1"
    root.mkdir(parents=True, exist_ok=True)
    cleanup_stale_preview_buffers(uv_runtime, hooks, root)
    return root


def cleanup_stale_preview_buffers(uv_runtime, hooks: PreviewSyncHooks, root: Path) -> None:
    now = time.time()
    if (now - uv_runtime.last_buffer_cleanup_at) < hooks.preview_buffer_cleanup_interval_seconds:
        return
    uv_runtime.last_buffer_cleanup_at = now
    cutoff = now - hooks.preview_buffer_ttl_seconds
    removed = 0
    try:
        for path in root.glob("*.bin"):
            try:
                if path.stat().st_mtime < cutoff:
                    path.unlink()
                    removed += 1
            except Exception:
                continue
    except Exception:
        return
    if removed:
        trace(
            "PreviewSync",
            "stale_buffers_removed",
            lambda: "Removed stale preview buffers.",
            lambda: {"removedCount": removed},
        )


def safe_preview_name(value: str | None) -> str:
    raw = str(value or "mesh").strip() or "mesh"
    return "".join(ch if ch.isalnum() or ch in ("-", "_", ".") else "_" for ch in raw)[:80]


def write_array_buffer(path: Path, values: array) -> int:
    with path.open("wb") as file:
        values.tofile(file)
    return path.stat().st_size


def log_uv_skip(uv_runtime, pair_id: str, reason: str) -> None:
    now = time.time()
    last = float(uv_runtime.last_skip_log_by_pair.get(pair_id, 0.0) or 0.0)
    if (now - last) >= 1.0:
        trace(
            "PreviewSync",
            "uv_preview_skipped",
            lambda: "Skipped a UV preview path.",
            lambda: {"pairId": pair_id, "reason": reason},
        )
        uv_runtime.last_skip_log_by_pair[pair_id] = now


def read_float2_attribute_data(data) -> array | None:
    if data is None or len(data) == 0:
        return None
    for prop in ("vector", "value", "uv"):
        try:
            out = array("f", [0.0]) * (len(data) * 2)
            data.foreach_get(prop, out)
            return out
        except Exception:
            continue
    try:
        out = array("f")
        for item in data:
            value = getattr(item, "vector", None)
            if value is None:
                value = getattr(item, "value", None)
            if value is None:
                value = getattr(item, "uv", None)
            if value is None:
                return None
            out.append(float(value[0]))
            out.append(float(value[1]))
        return out
    except Exception:
        return None


def read_edit_bmesh_uv0_values(uv_runtime, hooks: PreviewSyncHooks, mesh, pair_id: str | None = None) -> array | None:
    if bmesh is None or hooks.get_current_mode() != "EDIT_MESH":
        return None
    try:
        bm = bmesh.from_edit_mesh(mesh)
        uv_layer = bm.loops.layers.uv.active
        if uv_layer is None:
            return None
        values = array("f")
        face_count = loop_count = 0
        for face in bm.faces:
            face_count += 1
            for loop in face.loops:
                uv = loop[uv_layer].uv
                values.append(float(uv.x))
                values.append(float(uv.y))
                loop_count += 1
        if loop_count <= 0:
            return None
        if pair_id and hooks.uv_verbose_enabled():
            trace(
                "PreviewSync",
                "edit_bmesh_uv_used",
                lambda: "Read UV data from the edit-mode BMesh.",
                lambda: {"pairId": pair_id, "faceCount": face_count, "loopCount": loop_count},
            )
        return values
    except Exception as exc:
        if pair_id:
            log_uv_skip(uv_runtime, pair_id, f"edit_bmesh_uv_failed:{exc}")
        return None


def read_active_uv0_values(
    uv_runtime,
    hooks: PreviewSyncHooks,
    mesh,
    pair_id: str | None = None,
    *,
    allow_edit_bmesh: bool = False,
) -> array | None:
    if allow_edit_bmesh:
        edit_uv = read_edit_bmesh_uv0_values(uv_runtime, hooks, mesh, pair_id=pair_id)
        if edit_uv is not None and len(edit_uv) > 0:
            return edit_uv
    try:
        uv_layer = mesh.uv_layers.active.data if getattr(mesh.uv_layers, "active", None) else None
    except Exception:
        uv_layer = None
    if uv_layer is not None and len(uv_layer) > 0:
        uv0 = array("f", [0.0]) * (len(uv_layer) * 2)
        uv_layer.foreach_get("uv", uv0)
        return uv0
    attrs = getattr(mesh, "attributes", None)
    if attrs is None:
        return None
    try:
        active_name = getattr(getattr(mesh.uv_layers, "active", None), "name", None)
    except Exception:
        active_name = None
    candidates = []
    if active_name:
        try:
            attr = attrs.get(active_name)
            if attr is not None:
                candidates.append(attr)
        except Exception:
            pass
    try:
        candidates.extend(list(attrs))
    except Exception:
        pass
    for attr in candidates:
        try:
            if getattr(attr, "domain", None) != "CORNER" or getattr(attr, "data_type", None) != "FLOAT2":
                continue
            data = getattr(attr, "data", None)
            if data is None or len(data) == 0:
                continue
            uv0 = read_float2_attribute_data(data)
            if uv0 is None:
                continue
            if pair_id and hooks.uv_verbose_enabled():
                trace(
                    "PreviewSync",
                    "uv_attribute_used",
                    lambda: "Read UV data from a mesh attribute.",
                    lambda: {
                        "pairId": pair_id,
                        "attributeName": getattr(attr, "name", ""),
                        "valueCount": len(data),
                    },
                )
            return uv0
        except Exception:
            continue
    return None


def collect_shape_key_weights(obj) -> tuple[tuple[str, float], ...]:
    if obj is None or getattr(obj, "type", None) != "MESH":
        return tuple()
    mesh = getattr(obj, "data", None)
    shape_keys = getattr(mesh, "shape_keys", None) if mesh is not None else None
    key_blocks = getattr(shape_keys, "key_blocks", []) if shape_keys is not None else []
    items = []
    for index, key_block in enumerate(list(key_blocks or [])):
        if index == 0:
            continue
        name = str(getattr(key_block, "name", "") or "")
        if not name:
            continue
        try:
            value = float(getattr(key_block, "value", 0.0) or 0.0)
        except Exception:
            value = 0.0
        items.append((name, value))
    return tuple(items)


def send_shape_key_weights_if_changed(
    shape_key_runtime,
    hooks: PreviewSyncHooks,
    obj,
    force: bool = False,
    reason: str = "auto_object_mode",
) -> bool:
    if obj is None or getattr(obj, "type", None) != "MESH" or not hooks.mesh_has_shape_keys(obj):
        return False
    if not bool(obj.get(AUTO_SYNC_READY_KEY, False)):
        return False
    session = hooks.get_session()
    if session is None:
        return False
    pair_id = f"pair-{hooks.ensure_instance_id(obj)}"
    mesh = getattr(obj, "data", None)
    mesh_ref = f"mesh-{hooks.ensure_mesh_asset_id(obj)}" if mesh is not None else ""
    weights = collect_shape_key_weights(obj)
    previous = shape_key_runtime.last_weights_by_pair.get(pair_id)
    if not force and previous == weights:
        return False
    now = time.time()
    next_allowed = float(shape_key_runtime.next_weight_send_time_by_pair.get(pair_id, 0.0) or 0.0)
    if not force and now < next_allowed:
        return False
    payload = {
        "type": "scene_sync.blendshape_weights_v1",
        "timestamp": int(time.time()),
        "pairId": pair_id,
        "meshRef": mesh_ref,
        "sourceHint": str(reason or "auto_object_mode"),
        "weights": [
            {"name": name, "index": index + 1, "value": value, "weight": value * 100.0}
            for index, (name, value) in enumerate(weights)
        ],
    }
    try:
        result = session.send_auto(payload)
        ok = bool(getattr(result, "ok", False))
        if ok:
            shape_key_runtime.last_weights_by_pair[pair_id] = weights
            shape_key_runtime.next_weight_send_time_by_pair[pair_id] = (
                now + hooks.blendshape_weight_sync_interval_seconds
            )
            trace(
                "PreviewSync",
                "blendshape_weights_sent",
                lambda: "Sent BlendShape weights.",
                lambda: {"pairId": pair_id, "weightCount": len(weights), "reason": reason},
            )
        else:
            warn(
                "PreviewSync",
                "blendshape_weights_failed",
                getattr(result, "error", None) or "send_failed",
                {"pairId": pair_id, "weightCount": len(weights), "reason": reason},
            )
        return ok
    except Exception as exc:
        log_exception(
            "PreviewSync",
            "blendshape_weights_exception",
            exc,
            fields={"pairId": pair_id, "reason": reason},
        )
        return False


def has_pending_geometry_dirty(geometry_runtime, pair_id: str | None) -> bool:
    if not pair_id:
        return False
    state = geometry_runtime.dirty_by_pair.get(pair_id)
    return bool(state and state.get("geometry") and state.get("autoEligible", True))


def mark_evaluated_mesh_dirty(
    evaluated_runtime,
    hooks: PreviewSyncHooks,
    obj,
    *,
    source: str = "depsgraph",
    dirty_class: str = "content",
    now: float | None = None,
) -> None:
    if obj is None:
        return
    allow_mesh_structure_dirty = str(source or "") in {"uv_channels_signature_changed", "color_attributes_signature_changed"}
    if not allow_mesh_structure_dirty and not hooks.should_auto_rebuild(obj):
        return
    pair_id = hooks.pair_id_for_object(obj)
    if not pair_id:
        return
    now = time.time() if now is None else now
    state = evaluated_runtime.dirty_by_pair.setdefault(pair_id, {})
    dirty_class = dirty_class if dirty_class in {"content", "structure", "context_noise"} else "content"
    if dirty_class == "content" and str(source or "") in {"depsgraph_NodeTree", "depsgraph_GeometryNodeTree"}:
        existing_dirty_class = str(state.get("dirtyClass") or "")
        last_noise_source = str(state.get("lastNoiseSource") or "")
        last_noise_at = float(state.get("lastNoiseAt") or 0.0)
        noise_window = max(0.35, hooks.get_evaluated_preview_debounce_seconds())
        baseline_signature = evaluated_runtime.modifier_signature_by_pair.get(pair_id)
        signature_changed = False
        if baseline_signature:
            try:
                signature_changed = hooks.modifier_stack_signature(obj) != baseline_signature
            except Exception:
                signature_changed = False
        if (
            existing_dirty_class != "content"
            and not signature_changed
            and last_noise_source in _EVALUATED_CONTEXT_NOISE_SOURCES
            and (now - last_noise_at) <= noise_window
        ):
            dirty_class = "context_noise"
    has_visible_modifiers = hooks.object_has_visible_modifiers(obj)
    previous_preview_due = float(state.get("previewDueAt") or 0.0)
    state["lastDirtyAt"] = now
    if dirty_class != "context_noise":
        state["previewDueAt"] = now + hooks.get_evaluated_preview_debounce_seconds()
        state["dirtyClass"] = dirty_class
        state["source"] = source
        state["pendingModifierSignature"] = hooks.modifier_stack_signature(obj)
    else:
        state["previewDueAt"] = previous_preview_due
        state["lastNoiseSource"] = source
        state["lastNoiseAt"] = now
        if not state.get("dirtyClass"):
            state["dirtyClass"] = dirty_class
            state["source"] = source
    state["count"] = int(state.get("count") or 0) + 1
    state["hasVisibleModifiers"] = has_visible_modifiers
    state["autoEligible"] = bool(state.get("autoEligible", False)) or hooks.get_sync_enabled()
    evaluated_runtime.object_name_by_pair[pair_id] = str(getattr(obj, "name", "") or "")
    if has_visible_modifiers:
        evaluated_runtime.had_visible_modifiers_by_pair[pair_id] = True


def mark_pair_dirty(
    geometry_runtime,
    hooks: PreviewSyncHooks,
    pair_id: str | None,
    *,
    channel: str = "geometry",
    source: str = "unknown",
    now: float | None = None,
    force_mesh_send: bool = False,
) -> None:
    if not pair_id:
        return
    if channel == "geometry":
        obj = hooks.object_for_pair(pair_id)
        if obj is None or not bool(obj.get(AUTO_SYNC_READY_KEY, False)):
            return
    now = time.time() if now is None else now
    state = geometry_runtime.dirty_by_pair.setdefault(pair_id, {})
    state[channel] = True
    state["lastDirtyAt"] = now
    state["source"] = source
    state["count"] = int(state.get("count") or 0) + 1
    if force_mesh_send:
        state["forceMeshSend"] = True
    if channel == "geometry":
        state["previewDueAt"] = now + hooks.get_mesh_preview_debounce_seconds()
        state["autoEligible"] = bool(state.get("autoEligible", False)) or hooks.get_sync_enabled()
    if geometry_runtime.inflight_by_pair.get(pair_id):
        geometry_runtime.dirty_during_inflight_by_pair[pair_id] = True


def consume_pair_dirty(geometry_runtime, pair_id: str | None, *, channel: str = "geometry") -> dict | None:
    if not pair_id:
        return None
    state = geometry_runtime.dirty_by_pair.get(pair_id)
    if not state:
        return None
    out = dict(state)
    state[channel] = False
    ignored = ("lastDirtyAt", "source", "count", "previewDueAt", "autoEligible", "forceMeshSend")
    if not any(bool(value) for key, value in state.items() if key not in ignored):
        geometry_runtime.dirty_by_pair.pop(pair_id, None)
    return out


def consume_geometry_dirty_event(
    geometry_runtime,
    pair_id: str | None,
    *,
    respect_due: bool = True,
    require_auto_eligible: bool = False,
) -> dict | None:
    if not pair_id:
        return None
    state = geometry_runtime.dirty_by_pair.get(pair_id)
    if not state or not state.get("geometry"):
        return None
    if require_auto_eligible and not bool(state.get("autoEligible", True)):
        return None
    preview_due_at = float(state.get("previewDueAt") or 0.0)
    if respect_due and preview_due_at > 0.0 and time.time() < preview_due_at:
        return None
    return consume_pair_dirty(geometry_runtime, pair_id, channel="geometry")


def consume_evaluated_dirty_event(
    evaluated_runtime,
    pair_id: str | None,
    *,
    respect_due: bool = True,
    require_auto_eligible: bool = False,
) -> dict | None:
    if not pair_id:
        return None
    state = evaluated_runtime.dirty_by_pair.get(pair_id)
    if not state or str(state.get("dirtyClass") or "") == "context_noise":
        return None
    if require_auto_eligible and not bool(state.get("autoEligible", True)):
        return None
    preview_due_at = float(state.get("previewDueAt") or 0.0)
    if respect_due and preview_due_at > 0.0 and time.time() < preview_due_at:
        return None
    return dict(evaluated_runtime.dirty_by_pair.pop(pair_id, {}) or {})


def pump_evaluated_mesh_rebuilds(evaluated_runtime, hooks: PreviewSyncHooks, now: float) -> None:
    if not hooks.get_sync_enabled() or hooks.get_current_mode() != "OBJECT":
        return
    preview_delay = hooks.get_evaluated_preview_debounce_seconds()
    for pair_id, state in list(evaluated_runtime.dirty_by_pair.items()):
        obj = hooks.find_object_by_pair(pair_id)
        if obj is None:
            evaluated_runtime.dirty_by_pair.pop(pair_id, None)
            continue
        if str(state.get("dirtyClass") or "") == "context_noise":
            evaluated_runtime.dirty_by_pair.pop(pair_id, None)
            evaluated_runtime.last_preview_sent_at_by_pair.pop(pair_id, None)
            continue
        if not bool(state.get("autoEligible", True)):
            continue
        allow_structure_dirty = str(state.get("source") or "") in {"uv_channels_signature_changed", "color_attributes_signature_changed"}
        if not allow_structure_dirty and not hooks.should_auto_rebuild(obj):
            evaluated_runtime.dirty_by_pair.pop(pair_id, None)
            continue
        had_modifiers = bool(evaluated_runtime.had_visible_modifiers_by_pair.get(pair_id))
        has_modifiers = hooks.object_has_visible_modifiers(obj)
        preview_due_at = float(state.get("previewDueAt") or 0.0)
        last_preview_at = float(evaluated_runtime.last_preview_sent_at_by_pair.get(pair_id, 0.0) or 0.0)
        if now < preview_due_at:
            continue
        if last_preview_at >= preview_due_at:
            evaluated_runtime.dirty_by_pair.pop(pair_id, None)
            evaluated_runtime.last_preview_sent_at_by_pair.pop(pair_id, None)
            continue
        try:
            context = hooks.build_mesh_context(
                obj,
                include_extras=True,
                mesh_source_override="evaluated",
                rebuild_reason=str(state.get("source") or "evaluated_dirty"),
            )
        except ValueError as exc:
            build_reason = str(exc) or "mesh_context_failed"
            if build_reason != "mesh_has_no_exportable_geometry":
                raise
            evaluated_runtime.dirty_by_pair.pop(pair_id, None)
            evaluated_runtime.last_preview_sent_at_by_pair.pop(pair_id, None)
            warn(
                "PreviewSync",
                "evaluated_preview_geometry_missing",
                build_reason,
                {"pairId": pair_id, "dirtyReason": state.get("source")},
            )
            continue
        if context is None:
            evaluated_runtime.dirty_by_pair.pop(pair_id, None)
            evaluated_runtime.last_preview_sent_at_by_pair.pop(pair_id, None)
            continue
        context["source_hint"] = "auto_sync"
        result = hooks.send_mesh_update(context)
        ok = bool(result.get("ok"))
        reason = result.get("reason")
        if ok or reason == "no_mesh_change":
            if ok:
                hooks.store_mesh_content_baseline(obj, context, reason="evaluated_preview_sent")
                hooks.record_sent_material_refs(obj, context, reason="evaluated_preview_sent")
            evaluated_runtime.last_preview_sent_at_by_pair[pair_id] = now
            pending_signature = state.get("pendingModifierSignature")
            if pending_signature:
                evaluated_runtime.modifier_signature_by_pair[pair_id] = str(pending_signature)
            if had_modifiers and not has_modifiers:
                evaluated_runtime.had_visible_modifiers_by_pair.pop(pair_id, None)
            evaluated_runtime.dirty_by_pair.pop(pair_id, None)
            evaluated_runtime.last_preview_sent_at_by_pair.pop(pair_id, None)
        else:
            state["previewDueAt"] = now + preview_delay
        fields = {
            "pairId": pair_id,
            "reason": reason,
            "dirtyClass": state.get("dirtyClass"),
            "dirtyReason": state.get("source"),
            "dirtyCount": state.get("count"),
        }
        if ok or reason == "no_mesh_change":
            trace(
                "PreviewSync",
                "evaluated_preview_completed",
                lambda: "Completed an evaluated-mesh preview cycle.",
                lambda: fields,
            )
        else:
            warn("PreviewSync", "evaluated_preview_failed", reason or "send_failed", fields)


def send_active_mesh_full_preview_if_dirty(
    geometry_runtime,
    shape_key_runtime,
    hooks: PreviewSyncHooks,
) -> bool:
    obj = hooks.get_active_object()
    if obj is None or getattr(obj, "type", None) != "MESH" or getattr(obj, "data", None) is None:
        return False
    if not bool(obj.get(AUTO_SYNC_READY_KEY, False)) or hooks.get_session() is None:
        return False
    pair_id = f"pair-{hooks.ensure_instance_id(obj)}"
    if geometry_runtime.inflight_by_pair.get(pair_id):
        mark_pair_dirty(
            geometry_runtime,
            hooks,
            pair_id,
            channel="geometry",
            source="full_preview_inflight_skip",
            now=time.time(),
        )
        return False
    dirty_state = consume_geometry_dirty_event(
        geometry_runtime,
        pair_id,
        require_auto_eligible=True,
    )
    if not dirty_state:
        return False
    source = str(dirty_state.get("source") or "mesh_dirty")
    force_mesh_send = bool(dirty_state.get("forceMeshSend")) or source == "shape_key_structure_signature_changed"
    mesh_source = "evaluated" if hooks.object_has_visible_modifiers(obj) else "original"
    try:
        context = hooks.build_mesh_context(
            obj,
            include_extras=True,
            mesh_source_override=mesh_source,
            rebuild_reason=f"mesh_dirty_full_preview:{source}",
        )
    except ValueError as exc:
        build_reason = str(exc) or "mesh_context_failed"
        if build_reason != "mesh_has_no_exportable_geometry":
            raise
        warn(
            "PreviewSync",
            "full_preview_geometry_missing",
            build_reason,
            {"pairId": pair_id, "dirtySource": source},
        )
        return False
    if force_mesh_send and context is not None:
        context["force_send"] = True
    if context is None:
        trace(
            "PreviewSync",
            "full_preview_context_missing",
            lambda: "Deferred a full preview because its mesh context was unavailable.",
            lambda: {"pairId": pair_id, "dirtySource": source},
        )
        mark_pair_dirty(
            geometry_runtime,
            hooks,
            pair_id,
            channel="geometry",
            source=f"full_preview_context_missing:{source}",
            now=time.time(),
        )
        return False
    matches_baseline, baseline_scope = hooks.preview_matches_mesh_content_baseline(obj, context)
    if matches_baseline and not force_mesh_send:
        hooks.store_mesh_content_baseline(obj, context, reason="auto_preview_no_mesh_change")
        trace(
            "PreviewSync",
            "full_preview_unchanged",
            lambda: "Skipped a full preview because the mesh matched its baseline.",
            lambda: {
                "pairId": pair_id,
                "dirtySource": source,
                "dirtyCount": dirty_state.get("count"),
                "baselineScope": baseline_scope,
            },
        )
        hooks.send_deferred_object_state(obj, pair_id, source=f"after_mesh_preview:{source}")
        return False
    geometry_runtime.inflight_by_pair[pair_id] = True
    try:
        result = hooks.send_mesh_update(context)
        ok = bool(result.get("ok"))
        reason = result.get("reason")
        dirty_during = bool(geometry_runtime.dirty_during_inflight_by_pair.pop(pair_id, False))
        if dirty_during:
            mark_pair_dirty(
                geometry_runtime,
                hooks,
                pair_id,
                channel="geometry",
                source="during_full_preview",
                now=time.time(),
            )
        fields = {
            "pairId": pair_id,
            "reason": reason,
            "dirtySource": source,
            "dirtyCount": dirty_state.get("count"),
            "meshSource": context.get("mesh_source"),
        }
        if ok or reason == "no_mesh_change":
            trace(
                "PreviewSync",
                "full_preview_completed",
                lambda: "Completed a full mesh preview cycle.",
                lambda: fields,
            )
        else:
            warn("PreviewSync", "full_preview_failed", reason or "send_failed", fields)
        if ok:
            hooks.store_mesh_content_baseline(obj, context, reason="auto_preview_sent")
            hooks.record_sent_material_refs(obj, context, reason="auto_preview_sent")
            send_shape_key_weights_if_changed(
                shape_key_runtime,
                hooks,
                obj,
                force=True,
                reason=f"auto_preview_after_mesh_update:{source}",
            )
            hooks.send_deferred_object_state(obj, pair_id, source=f"after_mesh_preview:{source}")
        elif reason == "no_mesh_change":
            hooks.send_deferred_object_state(obj, pair_id, source=f"after_mesh_preview:{source}")
        else:
            mark_pair_dirty(
                geometry_runtime,
                hooks,
                pair_id,
                channel="geometry",
                source=f"full_preview_retry:{source}",
                now=time.time(),
            )
        return ok
    except Exception as exc:
        log_exception(
            "PreviewSync",
            "full_preview_exception",
            exc,
            fields={"pairId": pair_id, "dirtySource": source},
        )
        mark_pair_dirty(
            geometry_runtime,
            hooks,
            pair_id,
            channel="geometry",
            source=f"full_preview_exception:{source}",
            now=time.time(),
        )
        return False
    finally:
        geometry_runtime.inflight_by_pair.pop(pair_id, None)


def send_mesh_preview_with_live_uv(
    uv_runtime,
    hooks: PreviewSyncHooks,
    obj,
    *,
    reason: str,
    require_uv: bool = True,
) -> dict:
    if getattr(obj, "type", None) != "MESH" or getattr(obj, "data", None) is None:
        return {"ok": False, "reason": "active_mesh_required"}
    if not bool(obj.get(AUTO_SYNC_READY_KEY, False)):
        return {"ok": False, "reason": "active_mesh_not_imported_or_auto_sync_not_ready"}
    if hooks.get_session() is None:
        return {"ok": False, "reason": "session_missing"}
    pair_id = f"pair-{hooks.ensure_instance_id(obj)}"
    if uv_runtime.inflight_by_pair.get(pair_id):
        return {"ok": False, "reason": "uv_preview_inflight", "pairId": pair_id}
    uv_runtime.inflight_by_pair[pair_id] = True
    started_at = time.perf_counter()
    send_ms = 0.0
    try:
        context = hooks.build_mesh_context(
            obj,
            include_extras=True,
            mesh_source_override="evaluated" if hooks.object_has_visible_modifiers(obj) else "original",
            rebuild_reason=reason,
            allow_edit_bmesh_uv=True,
        )
        if context is None:
            return {"ok": False, "reason": "mesh_context_missing", "pairId": pair_id}
        context["source_hint"] = "manual_sync"
        context["force_send"] = True
        context["triggerType"] = f"{reason}_full_mesh"
        prebuilt = context.get("prebuilt_binary") if isinstance(context.get("prebuilt_binary"), dict) else None
        mesh_content = context.get("mesh_content") or {}
        export_vertex_count = int(prebuilt.get("vertexCount") or 0) if isinstance(prebuilt, dict) else len(mesh_content.get("vertices") or []) // 3
        uv_channel_count = len(prebuilt.get("uvChannels") or []) if isinstance(prebuilt, dict) else len(mesh_content.get("uvChannels") or [])
        uv_buffer_count = 0
        if isinstance(prebuilt, dict):
            for buffer in list(prebuilt.get("buffers") or []):
                if isinstance(buffer, dict) and str(buffer.get("semantic") or "").upper().startswith("UV"):
                    uv_buffer_count += 1
        if export_vertex_count <= 0:
            return {"ok": False, "reason": "mesh_data_empty", "pairId": pair_id}
        has_uv = uv_channel_count > 0 or uv_buffer_count > 0
        if not has_uv:
            uv = mesh_content.get("uv") if mesh_content.get("uv") is not None else mesh_content.get("uv0")
            has_uv = uv is not None and len(uv) >= export_vertex_count * 2
        if require_uv and not has_uv:
            return {"ok": False, "reason": "uv_data_empty", "pairId": pair_id}
        send_started_at = time.perf_counter()
        result = hooks.send_mesh_update(context)
        send_ms = (time.perf_counter() - send_started_at) * 1000.0
        ok = bool(result.get("ok"))
        result_reason = str(result.get("reason") or ("sent" if ok else "send_failed"))
        fields = {
            "pairId": pair_id,
            "exportVertexCount": export_vertex_count,
            "uvChannelCount": uv_channel_count,
            "uvBufferCount": uv_buffer_count,
            "trigger": context.get("triggerType"),
            "reason": result_reason,
            "buildMs": round((time.perf_counter() - started_at) * 1000.0, 2),
            "sendMs": round(send_ms, 2),
        }
        if ok:
            hooks.record_sent_material_refs(obj, context, reason=reason)
            trace(
                "PreviewSync",
                "manual_uv_preview_sent",
                lambda: "Sent a manual UV preview.",
                lambda: fields,
            )
        else:
            warn("PreviewSync", "manual_uv_preview_failed", result_reason, fields)
        return {
            "ok": ok,
            "changed": True,
            "pairId": pair_id,
            "exportVertexCount": export_vertex_count,
            "uvChannelCount": uv_channel_count,
            "reason": result_reason,
        }
    except Exception as exc:
        log_exception(
            "PreviewSync",
            "manual_uv_preview_exception",
            exc,
            fields={
                "pairId": pair_id,
                "buildMs": round((time.perf_counter() - started_at) * 1000.0, 2),
                "sendMs": round(send_ms, 2),
            },
        )
        return {"ok": False, "reason": str(exc) or "manual_uv_preview_failed", "pairId": pair_id}
    finally:
        uv_runtime.inflight_by_pair.pop(pair_id, None)


def manual_preview_active_uv_once(uv_runtime, hooks: PreviewSyncHooks) -> dict:
    obj = hooks.get_active_object()
    if obj is None:
        return {"ok": False, "reason": "blender_context_missing"}
    return send_mesh_preview_with_live_uv(
        uv_runtime,
        hooks,
        obj,
        reason="manual_uv_preview",
        require_uv=True,
    )


def send_preview_commit_for_object(hooks: PreviewSyncHooks, obj, reason: str = "mode_exit") -> bool:
    if obj is None or getattr(obj, "type", None) != "MESH":
        return False
    session = hooks.get_session()
    if session is None or getattr(obj, "data", None) is None:
        return False
    pair_id = f"pair-{hooks.ensure_instance_id(obj)}"
    mesh_ref = f"mesh-{hooks.ensure_mesh_asset_id(obj)}"
    payload = {
        "type": "scene_sync.preview_commit",
        "timestamp": int(time.time()),
        "pairId": pair_id,
        "meshRef": mesh_ref,
        "reason": str(reason or "mode_exit"),
    }
    try:
        result = session.send_auto(payload)
        ok = bool(getattr(result, "ok", False))
        fields = {"pairId": pair_id, "meshRef": mesh_ref, "reason": reason}
        if ok:
            trace(
                "PreviewSync",
                "preview_commit_sent",
                lambda: "Sent a preview commit.",
                lambda: fields,
            )
        else:
            warn(
                "PreviewSync",
                "preview_commit_failed",
                getattr(result, "error", None) or "send_failed",
                fields,
            )
        return ok
    except Exception as exc:
        log_exception(
            "PreviewSync",
            "preview_commit_exception",
            exc,
            fields={"pairId": pair_id, "meshRef": mesh_ref, "reason": reason},
        )
        return False


def manual_commit_selected_previews_once(hooks: PreviewSyncHooks) -> dict:
    selected = hooks.get_selected_objects()
    if selected is None:
        return {"ok": False, "reason": "blender_context_missing", "selectedCount": 0, "sent": 0}
    sent = skipped_not_mesh = skipped_not_ready = failed = 0
    for obj in selected:
        if obj is None or getattr(obj, "type", None) != "MESH":
            skipped_not_mesh += 1
            continue
        if not bool(obj.get(AUTO_SYNC_READY_KEY, False)):
            skipped_not_ready += 1
            continue
        if send_preview_commit_for_object(hooks, obj, reason="manual_commit"):
            sent += 1
        else:
            failed += 1
    ok = failed == 0 and sent > 0
    reason = "manual_preview_commit_sent" if ok else ("manual_preview_commit_no_ready_mesh" if sent == 0 else "manual_preview_commit_partial_failed")
    return {"ok": ok, "reason": reason, "selectedCount": len(selected), "sent": sent, "failed": failed, "skippedNotMesh": skipped_not_mesh, "skippedNotReady": skipped_not_ready}


def send_shape_key_mesh_full_update_on_mode_exit(
    hooks: PreviewSyncHooks,
    obj,
    reason: str = "mode_exit_shape_key_full_update",
) -> bool:
    if obj is None or getattr(obj, "type", None) != "MESH":
        return False
    session = hooks.get_session()
    if session is None:
        return False
    try:
        context = hooks.build_single_object_live_context(
            session,
            obj,
            package_id=f"pkg-shapekey-mode-exit-{int(time.time() * 1000)}",
        )
        context["sendMode"] = "manual_resource_resend"
        context["triggerType"] = "shape_key_mode_exit_full_update"
        context["correlationId"] = f"shape-key-exit-{int(time.time() * 1000)}"
        result = hooks.send_selected_resources(context)
        ok = bool(getattr(result, "ok", False))
        fields = {
            "pairId": f"pair-{hooks.ensure_instance_id(obj)}",
            "reason": reason,
            "resourceCount": getattr(result, "last_resource_count", 0),
        }
        if ok:
            trace(
                "PreviewSync",
                "shape_key_mode_exit_update_sent",
                lambda: "Sent the shape-key mesh update after leaving edit mode.",
                lambda: fields,
            )
        else:
            warn(
                "PreviewSync",
                "shape_key_mode_exit_update_failed",
                getattr(result, "error", None) or "send_failed",
                fields,
            )
        return ok
    except Exception as exc:
        log_exception(
            "PreviewSync",
            "shape_key_mode_exit_update_exception",
            exc,
            fields={"objectName": getattr(obj, "name", "<unnamed>"), "reason": reason},
        )
        return False


def send_preview_commit_mesh_for_object(hooks: PreviewSyncHooks, obj, reason: str = "mode_exit", mesh_source_override: str | None = None, rebuild_reason: str | None = None) -> bool:
    if obj is None or getattr(obj, "type", None) != "MESH":
        return False
    session = hooks.get_session()
    if session is None:
        return False

    context = hooks.build_mesh_context(
        obj,
        include_extras=True,
        mesh_source_override=mesh_source_override,
        rebuild_reason=rebuild_reason,
    )
    if context is None:
        warn(
            "PreviewSync",
            "preview_commit_mesh_context_missing",
            "mesh_context_missing",
            {"objectName": getattr(obj, "name", "<unnamed>"), "reason": reason},
        )
        return False

    pair_entry = context.get("pair_entry") or {}
    pair_id = str(pair_entry.get("pairId") or "").strip()
    mesh_ref = str(context.get("mesh_ref") or "").strip()
    mesh_content = context.get("mesh_content") or {}
    prebuilt_binary = context.get("prebuilt_binary") if isinstance(context.get("prebuilt_binary"), dict) else None
    if prebuilt_binary is not None and prebuilt_binary.get("buffers"):
        binary = {
            "meshRef": mesh_ref or prebuilt_binary.get("meshRef"),
            "vertexCount": int(prebuilt_binary.get("vertexCount") or 0),
            "indexCount": int(prebuilt_binary.get("indexCount") or 0),
            "buffers": prebuilt_binary.get("buffers") or [],
            "subMeshes": prebuilt_binary.get("subMeshes") or [],
            "blendShapes": prebuilt_binary.get("blendShapes") or [],
            "uvChannels": prebuilt_binary.get("uvChannels") or [],
            "color0": prebuilt_binary.get("color0"),
            "colorAttributeName": prebuilt_binary.get("colorAttributeName") or "",
            "profile": dict(prebuilt_binary.get("profile") or {}),
        }
        binary.setdefault("profile", {})["prebuilt"] = True
    else:
        binary = hooks.build_mesh_binary_buffers(
            mesh_content,
            pair_id=pair_id,
            mesh_ref=mesh_ref,
            fallback_json_bytes=0,
        )
    if binary is None:
        vertices = mesh_content.get("vertices")
        triangles = mesh_content.get("triangles") or mesh_content.get("indices")
        normals = mesh_content.get("normals")
        uv = mesh_content.get("uv") if mesh_content.get("uv") is not None else mesh_content.get("uv0")
        warn(
            "PreviewSync",
            "preview_commit_binary_fallback",
            "binary_build_failed",
            {
                "pairId": pair_id,
                "hasPrebuilt": prebuilt_binary is not None,
                "vertexValueCount": len(vertices) if hasattr(vertices, "__len__") else None,
                "indexCount": len(triangles) if hasattr(triangles, "__len__") else None,
                "normalValueCount": len(normals) if hasattr(normals, "__len__") else None,
                "uvValueCount": len(uv) if hasattr(uv, "__len__") else None,
            },
        )
        return send_preview_commit_for_object(hooks, obj, reason=f"{reason}_fallback")

    payload = {
        "type": "scene_sync.preview_commit_mesh_v1",
        "timestamp": int(time.time()),
        "pairId": pair_id,
        "meshRef": binary.get("meshRef"),
        "reason": str(reason or "mode_exit"),
        "materialRefs": list(context.get("material_refs") or hooks.collect_material_refs(obj)),
        "vertexCount": binary.get("vertexCount"),
        "indexCount": binary.get("indexCount"),
        "buffers": binary.get("buffers") or [],
        "subMeshes": binary.get("subMeshes") or [],
        "blendShapes": binary.get("blendShapes") or [],
        "uvChannels": binary.get("uvChannels") or [],
        "uvChannelCount": len(binary.get("uvChannels") or []),
        "uvChannelNames": [str(ch.get("name") or f"UV{ch.get('index')}") for ch in (binary.get("uvChannels") or []) if isinstance(ch, dict)],
        "color0": binary.get("color0"),
        "colorAttributeName": binary.get("colorAttributeName") or "",
        "profile": binary.get("profile") or {},
    }
    if bool(context.get("send_mesh_content_fingerprint")):
        payload["meshContentFingerprint"] = (
            str(context.get("mesh_content_fingerprint") or context.get("meshContentFingerprint") or "").strip()
            or hooks.compute_mesh_content_fingerprint(mesh_content, prebuilt_binary if prebuilt_binary is not None else binary)
        )
        payload["meshContentFingerprintNoUv"] = (
            str(context.get("mesh_content_fingerprint_no_uv") or context.get("meshContentFingerprintNoUv") or "").strip()
            or hooks.compute_mesh_content_fingerprint(mesh_content, prebuilt_binary if prebuilt_binary is not None else binary, include_uv=False)
        )
        scope = str(context.get("mesh_content_fingerprint_scope") or context.get("meshContentFingerprintScope") or "").strip()
        if scope:
            payload["meshContentFingerprintScope"] = scope
    try:
        send_result = session.send_auto(payload)
        ok = bool(getattr(send_result, "ok", False))
        if ok:
            trace(
                "PreviewSync",
                "preview_commit_mesh_sent",
                lambda: "Sent a full preview mesh commit.",
                lambda: {"pairId": pair_id, "meshRef": binary.get("meshRef"), "reason": reason},
            )
            return True
        warn(
            "PreviewSync",
            "preview_commit_mesh_failed",
            getattr(send_result, "error", None) or "send_failed",
            {"pairId": pair_id, "meshRef": binary.get("meshRef"), "reason": reason},
        )
    except Exception as exc:
        log_exception(
            "PreviewSync",
            "preview_commit_mesh_exception",
            exc,
            fields={"pairId": pair_id, "meshRef": binary.get("meshRef"), "reason": reason},
        )

    return send_preview_commit_for_object(hooks, obj, reason=f"{reason}_fallback")
