from __future__ import annotations

from dataclasses import dataclass
from typing import Callable
from array import array
import json

from blender.scene_sync.baseline import get_color_attributes_baseline, get_uv_channels_baseline, uv_channels_signature

try:
    import bpy  # type: ignore
except ImportError:
    bpy = None

AUTO_SYNC_READY_KEY = "blendersync_auto_sync_ready"
UV_CHANNELS_SIGNATURE_POLL_SECONDS = 0.25
COLOR_ATTRIBUTES_SIGNATURE_POLL_SECONDS = 0.25
SHAPE_KEY_STRUCTURE_POLL_SECONDS = 0.25

@dataclass(frozen=True)
class StructureWatchHooks:
    get_sync_enabled: Callable[[], bool]
    get_current_mode: Callable[[], str]
    mesh_has_shape_keys: Callable[[object], bool]
    pair_id_for_object: Callable[[object], str | None]
    mark_evaluated_dirty: Callable[..., None]
    mark_geometry_dirty: Callable[..., None]


def color_attributes_signature(obj) -> str:
    mesh = getattr(obj, "data", None) if obj is not None else None
    items = []
    try:
        attrs = list(getattr(mesh, "color_attributes", []) or []) if mesh is not None else []
    except Exception:
        attrs = []
    for index, attr in enumerate(attrs):
        try:
            data = getattr(attr, "data", None)
            count = len(data) if data is not None else 0
            sample = []
            digest = ""
            if data is not None and count > 0:
                max_samples = min(count, 8)
                for i in range(max_samples):
                    col = getattr(data[i], "color", None)
                    if col is not None:
                        sample.append(tuple(round(float(col[j]), 4) for j in range(min(4, len(col)))))
                try:
                    import hashlib
                    values = array("f", [0.0]) * (count * 4)
                    data.foreach_get("color", values)
                    digest = hashlib.sha1(values.tobytes()).hexdigest()
                except Exception:
                    digest = ""
            items.append({
                "index": index,
                "name": str(getattr(attr, "name", "") or ""),
                "domain": str(getattr(attr, "domain", "") or ""),
                "dataType": str(getattr(attr, "data_type", "") or ""),
                "count": count,
                "sample": sample,
                "sha1": digest,
            })
        except Exception:
            items.append({"index": index, "error": "color_attribute_summary_failed"})
    try:
        return json.dumps({"schema": "color_attributes_signature_v1", "attributes": items}, sort_keys=True, ensure_ascii=False, default=str)
    except Exception:
        return repr(items)


def poll_active_color_attributes_signature(structure_runtime, hooks, now: float) -> None:
    if not hooks.get_sync_enabled() or hooks.get_current_mode() != "OBJECT":
        return
    if bpy is None or bpy.context is None:
        return
    obj = getattr(bpy.context, "active_object", None)
    if obj is None or getattr(obj, "type", None) != "MESH":
        return
    if not bool(obj.get(AUTO_SYNC_READY_KEY, False)):
        return
    if hooks.mesh_has_shape_keys(obj):
        return
    pair_id = hooks.pair_id_for_object(obj)
    if not pair_id:
        return
    next_time = float(structure_runtime.color_attributes_poll_next_time_by_pair.get(pair_id, 0.0) or 0.0)
    if now < next_time:
        return
    structure_runtime.color_attributes_poll_next_time_by_pair[pair_id] = now + COLOR_ATTRIBUTES_SIGNATURE_POLL_SECONDS
    signature = color_attributes_signature(obj)
    previous = structure_runtime.color_attributes_signature_by_pair.get(pair_id)
    if previous is None:
        previous = get_color_attributes_baseline(obj)
        if previous is not None:
            structure_runtime.color_attributes_signature_by_pair[pair_id] = previous
    if previous is None:
        structure_runtime.color_attributes_signature_by_pair[pair_id] = signature
        return
    if previous != signature:
        structure_runtime.color_attributes_signature_by_pair[pair_id] = signature
        hooks.mark_evaluated_dirty(obj, source="color_attributes_signature_changed", dirty_class="content", now=now)


def shape_key_structure_signature(obj) -> str:
    mesh = getattr(obj, "data", None) if obj is not None else None
    shape_keys = getattr(mesh, "shape_keys", None) if mesh is not None else None
    key_blocks = list(getattr(shape_keys, "key_blocks", []) or []) if shape_keys is not None else []
    items = []
    for index, key in enumerate(key_blocks):
        try:
            data = getattr(key, "data", None)
            relative_key = getattr(key, "relative_key", None)
            items.append(
                {
                    "index": index,
                    "name": str(getattr(key, "name", "") or ""),
                    "dataCount": len(data) if data is not None else 0,
                    "relative": str(getattr(relative_key, "name", "") or "") if relative_key is not None else "",
                    "sliderMin": float(getattr(key, "slider_min", 0.0) or 0.0) if index > 0 else 0.0,
                    "sliderMax": float(getattr(key, "slider_max", 1.0) or 1.0) if index > 0 else 1.0,
                }
            )
        except Exception:
            items.append({"index": index, "error": "shape_key_summary_failed"})
    payload = {
        "schema": "shape_key_structure_signature_v1",
        "shapeKeysName": str(getattr(shape_keys, "name", "") or "") if shape_keys is not None else "",
        "keyCount": len(key_blocks),
        "keys": items,
    }
    try:
        return json.dumps(payload, sort_keys=True, ensure_ascii=False, default=str)
    except Exception:
        return repr(payload)


def poll_active_shape_key_structure_signature(structure_runtime, hooks, now: float) -> None:
    if not hooks.get_sync_enabled() or hooks.get_current_mode() != "OBJECT":
        return
    if bpy is None or bpy.context is None:
        return
    obj = getattr(bpy.context, "active_object", None)
    if obj is None or getattr(obj, "type", None) != "MESH":
        return
    if not bool(obj.get(AUTO_SYNC_READY_KEY, False)):
        return
    pair_id = hooks.pair_id_for_object(obj)
    if not pair_id:
        return
    next_time = float(structure_runtime.shape_key_structure_poll_next_time_by_pair.get(pair_id, 0.0) or 0.0)
    if now < next_time:
        return
    structure_runtime.shape_key_structure_poll_next_time_by_pair[pair_id] = now + SHAPE_KEY_STRUCTURE_POLL_SECONDS
    signature = shape_key_structure_signature(obj)
    previous = structure_runtime.shape_key_structure_signature_by_pair.get(pair_id)
    if previous is None:
        structure_runtime.shape_key_structure_signature_by_pair[pair_id] = signature
        return
    if previous != signature:
        structure_runtime.shape_key_structure_signature_by_pair[pair_id] = signature
        hooks.mark_geometry_dirty(
            pair_id,
            channel="geometry",
            source="shape_key_structure_signature_changed",
            now=now,
            force_mesh_send=True,
        )


def poll_active_uv_channels_signature(structure_runtime, hooks, now: float) -> None:
    if not hooks.get_sync_enabled() or hooks.get_current_mode() != "OBJECT":
        return
    if bpy is None or bpy.context is None:
        return
    obj = getattr(bpy.context, "active_object", None)
    if obj is None or getattr(obj, "type", None) != "MESH":
        return
    if not bool(obj.get(AUTO_SYNC_READY_KEY, False)):
        return
    pair_id = hooks.pair_id_for_object(obj)
    if not pair_id:
        return
    next_time = float(structure_runtime.uv_channels_poll_next_time_by_pair.get(pair_id, 0.0) or 0.0)
    if now < next_time:
        return
    structure_runtime.uv_channels_poll_next_time_by_pair[pair_id] = now + UV_CHANNELS_SIGNATURE_POLL_SECONDS
    signature = uv_channels_signature(obj)
    previous = structure_runtime.uv_channels_signature_by_pair.get(pair_id)
    if previous is None:
        previous = get_uv_channels_baseline(obj)
        if previous is not None:
            structure_runtime.uv_channels_signature_by_pair[pair_id] = previous
    if previous is None:
        structure_runtime.uv_channels_signature_by_pair[pair_id] = signature
        return
    if previous != signature:
        structure_runtime.uv_channels_signature_by_pair[pair_id] = signature
        # UV layer structure is part of mesh resource structure.  It must trigger
        # the same preview path even when the object has no modifiers.
        hooks.mark_evaluated_dirty(obj, source="uv_channels_signature_changed", dirty_class="content", now=now)
