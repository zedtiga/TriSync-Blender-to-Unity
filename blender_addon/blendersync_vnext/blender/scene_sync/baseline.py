from __future__ import annotations

import hashlib
import json
import time
from collections import defaultdict

from blender.common.log import trace
from blender.resource_update.fingerprint import compute_mesh_content_fingerprint

try:
    import bpy  # type: ignore
except ImportError:
    bpy = None

MODIFIER_STACK_SIGNATURE_PROP = "blendersync_baseline_modifier_stack_signature_v1"
MATERIAL_SLOTS_SIGNATURE_PROP = "blendersync_baseline_material_slots_signature_v1"
UV_CHANNELS_SIGNATURE_PROP = "blendersync_baseline_uv_channels_signature_v1"
COLOR_ATTRIBUTES_SIGNATURE_PROP = "blendersync_baseline_color_attributes_signature_v1"
MESH_CONTENT_FINGERPRINT_PROP = "blendersync_baseline_mesh_content_fingerprint_v1"
MESH_CONTENT_FINGERPRINT_NO_UV_PROP = "blendersync_baseline_mesh_content_fingerprint_no_uv_v1"
BASELINE_UPDATED_AT_PROP = "blendersync_baseline_updated_at_v1"
BASELINE_REASON_PROP = "blendersync_baseline_reason_v1"

MODIFIER_STACK_SIGNATURE_SCHEMA = "modifier_stack_signature_v2"
MODIFIER_REFERENCE_POLICY_VERSION = "builtin_modifier_policy_v1"

MAX_STRING_SIGNATURE_LENGTH = 512
MAX_SEQUENCE_SIGNATURE_ITEMS = 16
MAX_ID_PROPERTIES = 64
MAX_COLLECTION_OBJECTS = 64
MAX_NODETREE_NODES = 128
MAX_NODETREE_LINKS = 256
MAX_NODETREE_INTERFACE_ITEMS = 128
MAX_LOG_PER_KEY_SECONDS = 10.0
_MATRIX_ROUND_DIGITS = 6
_FLOAT_ROUND_DIGITS = 6

# Modifier-specific reference semantics. Keep this intentionally small and
# conservative; unknown object references fall back to a light summary instead
# of trying to serialize full datablocks.
_REFERENCE_POLICY_REGISTRY = {
    # Object transform controls
    ("MIRROR", "mirror_object"): "transform_only",
    ("ARRAY", "offset_object"): "transform_only",
    ("SIMPLE_DEFORM", "origin"): "transform_only",
    ("WARP", "object_from"): "transform_only",
    ("WARP", "object_to"): "transform_only",
    ("CAST", "object"): "transform_only",
    ("WAVE", "start_position_object"): "transform_only",
    ("HOOK", "object"): "transform_only",

    # Geometry/reference inputs
    ("BOOLEAN", "object"): "geometry_input_light",
    ("BOOLEAN", "collection"): "collection_geometry_input_light",
    ("SHRINKWRAP", "target"): "geometry_input_light",
    ("SHRINKWRAP", "auxiliary_target"): "geometry_input_light",
    ("DATA_TRANSFER", "object"): "geometry_input_light",
    ("MESH_DEFORM", "object"): "geometry_input_light",
    ("SURFACE_DEFORM", "target"): "geometry_input_light",
    ("VERTEX_WEIGHT_PROXIMITY", "target"): "geometry_input_light",

    # Deformer objects. Their object transform and data identity matter; mesh
    # fingerprint is added when the referenced object is a Mesh, otherwise the
    # light summary still captures data identity for Curve/Lattice/Armature.
    ("ARMATURE", "object"): "geometry_input_light",
    ("LATTICE", "object"): "geometry_input_light",
    ("CURVE", "object"): "geometry_input_light",
}

_UI_ONLY_PROPERTY_NAMES = {
    "rna_type",
    "name",
    "type",
    "show_render",
    "show_viewport",
    "show_expanded",
    "is_active",
}
_UI_ONLY_PROPERTY_PREFIXES = (
    "open_",
)
_UI_ONLY_PROPERTY_SUFFIXES = (
    "_panel",
)

_last_log_by_key: dict[str, float] = defaultdict(float)


def _log_once(key: str, message: str) -> None:
    now = time.time()
    if now - _last_log_by_key[key] < MAX_LOG_PER_KEY_SECONDS:
        return
    _last_log_by_key[key] = now
    try:
        trace(
            "Baseline",
            "modifier_baseline",
            lambda: message,
            lambda: {"key": key},
        )
    except Exception:
        pass


def _stable_hash(value) -> str:
    try:
        raw = json.dumps(value, sort_keys=True, ensure_ascii=False, default=str)
    except Exception:
        raw = repr(value)
    return hashlib.sha1(raw.encode("utf-8", errors="replace")).hexdigest()


def _round_float(value):
    try:
        return round(float(value), _FLOAT_ROUND_DIGITS)
    except Exception:
        return value


def _id_identity(value) -> dict:
    if value is None:
        return {"kind": "null"}
    out = {
        "kind": value.__class__.__name__,
        "name": str(getattr(value, "name", "") or ""),
    }
    try:
        library = getattr(value, "library", None)
        if library is not None:
            out["library"] = str(getattr(library, "filepath", "") or getattr(library, "name", "") or "")
    except Exception:
        pass
    try:
        out["sessionUid"] = int(getattr(value, "session_uid"))
    except Exception:
        pass
    return out


def _matrix_world_summary(obj) -> dict | None:
    if obj is None:
        return None
    try:
        matrix = getattr(obj, "matrix_world")
        rows = [[round(float(matrix[row][col]), _MATRIX_ROUND_DIGITS) for col in range(4)] for row in range(4)]
        return {"rows": rows, "hash": _stable_hash(rows)}
    except Exception:
        return None


def _mesh_fingerprint(mesh) -> dict | None:
    if mesh is None:
        return None
    out = {"identity": _id_identity(mesh)}
    try:
        out["vertexCount"] = int(len(getattr(mesh, "vertices", []) or []))
    except Exception:
        pass
    try:
        out["edgeCount"] = int(len(getattr(mesh, "edges", []) or []))
    except Exception:
        pass
    try:
        out["polygonCount"] = int(len(getattr(mesh, "polygons", []) or []))
    except Exception:
        pass
    try:
        bound_box = getattr(mesh, "bound_box", None)
        if bound_box is not None:
            out["bounds"] = [[_round_float(c) for c in corner] for corner in bound_box]
    except Exception:
        pass
    try:
        # Blender Mesh.update_tag is not a value, but update_time can exist on some IDs.
        update_time = getattr(mesh, "update_time", None)
        if update_time is not None:
            out["updateTime"] = _round_float(update_time)
    except Exception:
        pass
    return out


def _object_light_summary(obj, *, include_transform: bool = True, include_geometry: bool = False) -> dict:
    if obj is None:
        return {"identity": {"kind": "null"}}
    out = {
        "identity": _id_identity(obj),
        "objectType": str(getattr(obj, "type", "") or ""),
    }
    if include_transform:
        matrix = _matrix_world_summary(obj)
        if matrix is not None:
            out["matrixWorld"] = matrix
    data = None
    try:
        data = getattr(obj, "data", None)
    except Exception:
        data = None
    if data is not None:
        out["dataIdentity"] = _id_identity(data)
    if include_geometry and str(getattr(obj, "type", "") or "") == "MESH":
        fp = _mesh_fingerprint(data)
        if fp is not None:
            out["meshFingerprint"] = fp
    return out


def _collection_summary(collection) -> dict:
    out = {"identity": _id_identity(collection), "objects": []}
    try:
        objects = list(getattr(collection, "objects", []) or [])
    except Exception:
        objects = []
    out["objectCount"] = len(objects)
    for obj in objects[:MAX_COLLECTION_OBJECTS]:
        out["objects"].append(_object_light_summary(obj, include_transform=True, include_geometry=True))
    if len(objects) > MAX_COLLECTION_OBJECTS:
        out["truncated"] = len(objects) - MAX_COLLECTION_OBJECTS
        _log_once("collection_truncated", f"collection summary truncated collection={getattr(collection, 'name', '')} count={len(objects)}")
    return out


def _reference_policy_for(mod_type: str, identifier: str, value) -> str | None:
    policy = _REFERENCE_POLICY_REGISTRY.get((mod_type, identifier))
    if policy:
        return policy
    if bpy is not None:
        try:
            if isinstance(value, bpy.types.Object):
                return "unknown_object_light"
        except Exception:
            pass
        try:
            if isinstance(value, bpy.types.Collection):
                return "collection_geometry_input_light"
        except Exception:
            pass
    # Fallback without bpy type checks: common Blender IDs have name and object type/data.
    if hasattr(value, "name") and hasattr(value, "matrix_world"):
        return "unknown_object_light"
    return None


def _reference_value_for_signature(mod_type: str, identifier: str, value):
    policy = _reference_policy_for(mod_type, identifier, value)
    if not policy:
        return None
    if policy == "transform_only":
        return {"policy": policy, "ref": _object_light_summary(value, include_transform=True, include_geometry=False)}
    if policy == "geometry_input_light":
        return {"policy": policy, "ref": _object_light_summary(value, include_transform=True, include_geometry=True)}
    if policy == "collection_geometry_input_light":
        return {"policy": policy, "ref": _collection_summary(value)}
    if policy == "unknown_object_light":
        _log_once(f"unknown_object:{mod_type}:{identifier}", f"using conservative object reference summary modType={mod_type} property={identifier}")
        return {"policy": policy, "ref": _object_light_summary(value, include_transform=True, include_geometry=True)}
    return {"policy": policy, "ref": _id_identity(value)}


def _is_ui_only_property(identifier: str) -> bool:
    if not identifier:
        return True
    if identifier in _UI_ONLY_PROPERTY_NAMES:
        return True
    if any(identifier.startswith(prefix) for prefix in _UI_ONLY_PROPERTY_PREFIXES):
        return True
    if any(identifier.endswith(suffix) for suffix in _UI_ONLY_PROPERTY_SUFFIXES):
        return True
    return False


def _value_for_signature(value, depth: int = 0, *, key: str = "value"):
    if depth > 3:
        _log_once(f"depth:{key}", f"skipped deep value key={key}")
        return {"truncated": "depth"}
    if value is None or isinstance(value, bool):
        return value
    if isinstance(value, int):
        return int(value)
    if isinstance(value, float):
        return _round_float(value)
    if isinstance(value, str):
        if len(value) > MAX_STRING_SIGNATURE_LENGTH:
            _log_once(f"string:{key}", f"truncated long string key={key} length={len(value)}")
            return {"stringHash": _stable_hash(value), "length": len(value), "truncated": True}
        return value
    if isinstance(value, (bytes, bytearray)):
        _log_once(f"bytes:{key}", f"summarized bytes key={key} length={len(value)}")
        return {"bytesHash": hashlib.sha1(bytes(value)).hexdigest(), "length": len(value)}
    if hasattr(value, "name"):
        return _id_identity(value)
    try:
        if isinstance(value, (list, tuple)):
            seq = list(value)
            out = [_value_for_signature(v, depth + 1, key=key) for v in seq[:MAX_SEQUENCE_SIGNATURE_ITEMS]]
            if len(seq) > MAX_SEQUENCE_SIGNATURE_ITEMS:
                out.append({"truncated": len(seq) - MAX_SEQUENCE_SIGNATURE_ITEMS})
                _log_once(f"seq:{key}", f"truncated sequence key={key} length={len(seq)}")
            return out
    except Exception:
        pass
    try:
        if hasattr(value, "__len__") and hasattr(value, "__getitem__"):
            count = min(len(value), MAX_SEQUENCE_SIGNATURE_ITEMS)
            out = [_value_for_signature(value[i], depth + 1, key=key) for i in range(count)]
            if len(value) > MAX_SEQUENCE_SIGNATURE_ITEMS:
                out.append({"truncated": len(value) - MAX_SEQUENCE_SIGNATURE_ITEMS})
                _log_once(f"seqlike:{key}", f"truncated sequence-like key={key} length={len(value)}")
            return out
    except Exception:
        pass
    text = str(value)
    if len(text) > MAX_STRING_SIGNATURE_LENGTH:
        return {"reprHash": _stable_hash(text), "length": len(text), "truncated": True}
    return text


def _id_properties_for_signature(value) -> dict:
    out = {}
    try:
        keys = list(value.keys()) if hasattr(value, "keys") else []
    except Exception:
        keys = []
    kept = 0
    for key in keys:
        key_str = str(key)
        if key_str.startswith("_"):
            continue
        if kept >= MAX_ID_PROPERTIES:
            out["__truncated__"] = len(keys) - kept
            _log_once("idprops_truncated", f"id properties truncated owner={getattr(value, 'name', '')} count={len(keys)}")
            break
        try:
            out[key_str] = _value_for_signature(value[key], 0, key=f"idprop:{key_str}")
            kept += 1
        except Exception:
            continue
    return out


def _node_socket_default_value(socket, *, key: str):
    try:
        if not hasattr(socket, "default_value"):
            return None
        return _value_for_signature(getattr(socket, "default_value"), key=key)
    except Exception:
        return None


def _node_socket_summary(socket, *, key: str) -> dict:
    out = {
        "name": str(getattr(socket, "name", "") or ""),
        "identifier": str(getattr(socket, "identifier", "") or ""),
        "type": str(getattr(socket, "type", "") or ""),
    }
    try:
        out["enabled"] = bool(getattr(socket, "enabled", True))
    except Exception:
        pass
    try:
        out["hide"] = bool(getattr(socket, "hide", False))
    except Exception:
        pass
    default_value = _node_socket_default_value(socket, key=key)
    if default_value is not None:
        out["default"] = default_value
    return out


def _node_tree_interface_summary(node_tree) -> dict:
    out = {"items": []}
    interface = getattr(node_tree, "interface", None)
    if interface is None:
        return out
    try:
        items = list(getattr(interface, "items_tree", []) or [])
    except Exception:
        items = []
    out["itemCount"] = len(items)
    for item in items[:MAX_NODETREE_INTERFACE_ITEMS]:
        try:
            entry = {
                "name": str(getattr(item, "name", "") or ""),
                "identifier": str(getattr(item, "identifier", "") or ""),
                "itemType": str(getattr(item, "item_type", "") or ""),
                "inOut": str(getattr(item, "in_out", "") or ""),
                "socketType": str(getattr(item, "socket_type", "") or ""),
            }
            default_value = _node_socket_default_value(item, key=f"nodetree.interface.{entry['identifier'] or entry['name']}")
            if default_value is not None:
                entry["default"] = default_value
            out["items"].append(entry)
        except Exception:
            continue
    if len(items) > MAX_NODETREE_INTERFACE_ITEMS:
        out["truncated"] = len(items) - MAX_NODETREE_INTERFACE_ITEMS
        _log_once("nodetree_interface_truncated", f"node tree interface summary truncated nodeTree={getattr(node_tree, 'name', '')} count={len(items)}")
    return out


def _node_tree_signature_summary(node_tree) -> dict | None:
    if node_tree is None:
        return None
    out = {
        "identity": _id_identity(node_tree),
        "treeType": str(getattr(node_tree, "type", "") or ""),
        "interface": _node_tree_interface_summary(node_tree),
        "nodes": [],
        "links": [],
    }
    try:
        out["idProperties"] = _id_properties_for_signature(node_tree)
    except Exception:
        pass
    try:
        nodes = list(getattr(node_tree, "nodes", []) or [])
    except Exception:
        nodes = []
    out["nodeCount"] = len(nodes)
    for node in nodes[:MAX_NODETREE_NODES]:
        try:
            node_name = str(getattr(node, "name", "") or "")
            entry = {
                "name": node_name,
                "label": str(getattr(node, "label", "") or ""),
                "type": str(getattr(node, "type", "") or ""),
                "blIdname": str(getattr(node, "bl_idname", "") or ""),
                "mute": bool(getattr(node, "mute", False)),
            }
            try:
                entry["operation"] = str(getattr(node, "operation", "") or "")
            except Exception:
                pass
            try:
                entry["mode"] = str(getattr(node, "mode", "") or "")
            except Exception:
                pass
            try:
                entry["dataType"] = str(getattr(node, "data_type", "") or "")
            except Exception:
                pass
            try:
                entry["domain"] = str(getattr(node, "domain", "") or "")
            except Exception:
                pass
            try:
                group_tree = getattr(node, "node_tree", None)
                if group_tree is not None and group_tree is not node_tree:
                    entry["nodeTreeIdentity"] = _id_identity(group_tree)
            except Exception:
                pass
            try:
                entry["idProperties"] = _id_properties_for_signature(node)
            except Exception:
                pass
            inputs = []
            try:
                sockets = list(getattr(node, "inputs", []) or [])
            except Exception:
                sockets = []
            for socket in sockets[:MAX_SEQUENCE_SIGNATURE_ITEMS]:
                inputs.append(_node_socket_summary(socket, key=f"nodetree.node.{node_name}.input.{getattr(socket, 'identifier', '') or getattr(socket, 'name', '')}"))
            if len(sockets) > MAX_SEQUENCE_SIGNATURE_ITEMS:
                inputs.append({"truncated": len(sockets) - MAX_SEQUENCE_SIGNATURE_ITEMS})
            entry["inputs"] = inputs
            outputs = []
            try:
                sockets = list(getattr(node, "outputs", []) or [])
            except Exception:
                sockets = []
            for socket in sockets[:MAX_SEQUENCE_SIGNATURE_ITEMS]:
                outputs.append(_node_socket_summary(socket, key=f"nodetree.node.{node_name}.output.{getattr(socket, 'identifier', '') or getattr(socket, 'name', '')}"))
            if len(sockets) > MAX_SEQUENCE_SIGNATURE_ITEMS:
                outputs.append({"truncated": len(sockets) - MAX_SEQUENCE_SIGNATURE_ITEMS})
            entry["outputs"] = outputs
            out["nodes"].append(entry)
        except Exception as exc:
            out["nodes"].append({"error": str(exc)})
    if len(nodes) > MAX_NODETREE_NODES:
        out["nodesTruncated"] = len(nodes) - MAX_NODETREE_NODES
        _log_once("nodetree_nodes_truncated", f"node tree nodes summary truncated nodeTree={getattr(node_tree, 'name', '')} count={len(nodes)}")
    try:
        links = list(getattr(node_tree, "links", []) or [])
    except Exception:
        links = []
    out["linkCount"] = len(links)
    for link in links[:MAX_NODETREE_LINKS]:
        try:
            from_node = getattr(link, "from_node", None)
            to_node = getattr(link, "to_node", None)
            from_socket = getattr(link, "from_socket", None)
            to_socket = getattr(link, "to_socket", None)
            out["links"].append({
                "fromNode": str(getattr(from_node, "name", "") or ""),
                "fromSocket": str(getattr(from_socket, "identifier", "") or getattr(from_socket, "name", "") or ""),
                "toNode": str(getattr(to_node, "name", "") or ""),
                "toSocket": str(getattr(to_socket, "identifier", "") or getattr(to_socket, "name", "") or ""),
            })
        except Exception:
            continue
    if len(links) > MAX_NODETREE_LINKS:
        out["linksTruncated"] = len(links) - MAX_NODETREE_LINKS
        _log_once("nodetree_links_truncated", f"node tree links summary truncated nodeTree={getattr(node_tree, 'name', '')} count={len(links)}")
    return out


def _blender_version_summary() -> str:
    if bpy is None:
        return "unknown"
    try:
        return ".".join(str(v) for v in bpy.app.version)
    except Exception:
        return "unknown"


def modifier_stack_signature(obj) -> str:
    if obj is None:
        return ""
    items = []
    for index, mod in enumerate(list(getattr(obj, "modifiers", []) or [])):
        try:
            mod_type = str(getattr(mod, "type", "") or "")
            mod_items = {
                "index": index,
                "name": str(getattr(mod, "name", "") or ""),
                "type": mod_type,
                "show_viewport": bool(getattr(mod, "show_viewport", True)),
                "show_render": bool(getattr(mod, "show_render", True)),
            }
            props = {}
            refs = {}
            bl_rna = getattr(mod, "bl_rna", None)
            for prop in list(getattr(bl_rna, "properties", []) or []):
                identifier = str(getattr(prop, "identifier", "") or "")
                if _is_ui_only_property(identifier):
                    continue
                if getattr(prop, "is_readonly", False):
                    continue
                try:
                    value = getattr(mod, identifier)
                except Exception:
                    continue
                ref_value = _reference_value_for_signature(mod_type, identifier, value)
                if ref_value is not None:
                    refs[identifier] = ref_value
                    continue
                try:
                    props[identifier] = _value_for_signature(value, key=f"{mod_type}.{identifier}")
                except Exception:
                    continue
            id_props = _id_properties_for_signature(mod)
            if id_props:
                mod_items["id_properties"] = id_props
            if mod_type == "NODES":
                try:
                    node_tree_summary = _node_tree_signature_summary(getattr(mod, "node_group", None))
                    if node_tree_summary is not None:
                        mod_items["node_tree"] = node_tree_summary
                except Exception as exc:
                    mod_items["node_tree"] = {"error": str(exc)}
            if refs:
                mod_items["refs"] = refs
            mod_items["props"] = props
            items.append(mod_items)
        except Exception as exc:
            items.append({"index": index, "error": str(exc)})
    payload = {
        "schema": MODIFIER_STACK_SIGNATURE_SCHEMA,
        "blenderVersion": _blender_version_summary(),
        "policyVersion": MODIFIER_REFERENCE_POLICY_VERSION,
        "modifiers": items,
    }
    try:
        return json.dumps(payload, sort_keys=True, ensure_ascii=False, default=str)
    except Exception:
        return repr(payload)


def material_slots_signature_from_refs(refs) -> str:
    try:
        return json.dumps(list(refs or []), ensure_ascii=False)
    except Exception:
        return repr(tuple(refs or []))


def uv_channels_signature(obj) -> str:
    mesh = getattr(obj, "data", None) if obj is not None else None
    items = []
    try:
        layers = list(getattr(mesh, "uv_layers", []) or []) if mesh is not None else []
    except Exception:
        layers = []
    try:
        active_index = int(getattr(getattr(mesh, "uv_layers", None), "active_index", -1)) if mesh is not None else -1
    except Exception:
        active_index = -1
    for index, layer in enumerate(layers):
        try:
            items.append({
                "index": index,
                "name": str(getattr(layer, "name", "") or ""),
                "active": index == active_index,
            })
        except Exception:
            items.append({"index": index, "error": "uv_layer_summary_failed"})
    payload = {
        "schema": "uv_channels_signature_v1",
        "layerCount": len(items),
        "activeIndex": active_index,
        "layers": items,
    }
    try:
        return json.dumps(payload, sort_keys=True, ensure_ascii=False, default=str)
    except Exception:
        return repr(payload)


def get_modifier_stack_baseline(obj) -> str | None:
    if obj is None:
        return None
    value = obj.get(MODIFIER_STACK_SIGNATURE_PROP)
    return str(value) if value is not None else None


def set_modifier_stack_baseline(obj, signature: str | None = None, *, reason: str = "unknown") -> str:
    sig = modifier_stack_signature(obj) if signature is None else str(signature)
    obj[MODIFIER_STACK_SIGNATURE_PROP] = sig
    obj[BASELINE_UPDATED_AT_PROP] = float(time.time())
    obj[BASELINE_REASON_PROP] = str(reason or "unknown")
    return sig


def get_material_slots_baseline(obj) -> str | None:
    if obj is None:
        return None
    value = obj.get(MATERIAL_SLOTS_SIGNATURE_PROP)
    return str(value) if value is not None else None


def set_material_slots_baseline(obj, refs, *, reason: str = "unknown") -> str:
    sig = material_slots_signature_from_refs(refs)
    obj[MATERIAL_SLOTS_SIGNATURE_PROP] = sig
    obj[BASELINE_UPDATED_AT_PROP] = float(time.time())
    obj[BASELINE_REASON_PROP] = str(reason or "unknown")
    return sig


def get_uv_channels_baseline(obj) -> str | None:
    if obj is None:
        return None
    value = obj.get(UV_CHANNELS_SIGNATURE_PROP)
    return str(value) if value is not None else None


def set_uv_channels_baseline(obj, signature: str | None = None, *, reason: str = "unknown") -> str:
    sig = uv_channels_signature(obj) if signature is None else str(signature)
    obj[UV_CHANNELS_SIGNATURE_PROP] = sig
    obj[BASELINE_UPDATED_AT_PROP] = float(time.time())
    obj[BASELINE_REASON_PROP] = str(reason or "unknown")
    return sig


def get_color_attributes_baseline(obj) -> str | None:
    if obj is None:
        return None
    value = obj.get(COLOR_ATTRIBUTES_SIGNATURE_PROP)
    return str(value) if value is not None else None


def set_color_attributes_baseline(obj, signature: str, *, reason: str = "unknown") -> str:
    sig = str(signature)
    obj[COLOR_ATTRIBUTES_SIGNATURE_PROP] = sig
    obj[BASELINE_UPDATED_AT_PROP] = float(time.time())
    obj[BASELINE_REASON_PROP] = str(reason or "unknown")
    return sig


def get_mesh_content_fingerprint_baseline(obj) -> str | None:
    try:
        value = str(obj.get(MESH_CONTENT_FINGERPRINT_PROP, "") or "").strip()
    except Exception:
        return None
    return value or None


def get_mesh_content_fingerprint_no_uv_baseline(obj) -> str | None:
    try:
        value = str(obj.get(MESH_CONTENT_FINGERPRINT_NO_UV_PROP, "") or "").strip()
    except Exception:
        return None
    return value or None


def set_mesh_content_fingerprint_baseline(
    obj,
    fingerprint: str | None,
    fingerprint_no_uv: str | None = None,
    *,
    reason: str = "unknown",
) -> dict:
    out = {}
    fp = str(fingerprint or "").strip()
    fp_no_uv = str(fingerprint_no_uv or "").strip()
    if fp:
        obj[MESH_CONTENT_FINGERPRINT_PROP] = fp
        out["meshContentFingerprint"] = fp
    if fp_no_uv:
        obj[MESH_CONTENT_FINGERPRINT_NO_UV_PROP] = fp_no_uv
        out["meshContentFingerprintNoUv"] = fp_no_uv
    if fp or fp_no_uv:
        obj[BASELINE_UPDATED_AT_PROP] = float(time.time())
        obj[BASELINE_REASON_PROP] = str(reason or "unknown")
    return out


def mesh_payload_content_fingerprints(mesh_dict: dict | None) -> tuple[str | None, str | None]:
    if not isinstance(mesh_dict, dict):
        return None, None
    vertices = mesh_dict.get("vertices")
    triangles = mesh_dict.get("indices") or mesh_dict.get("triangles")
    normals = mesh_dict.get("normals")
    uv = mesh_dict.get("uv0") if mesh_dict.get("uv0") is not None else mesh_dict.get("uv")
    color0 = mesh_dict.get("color0")
    submeshes = mesh_dict.get("subMeshes") or []
    try:
        full = compute_mesh_content_fingerprint(
            mesh_dict,
            None,
            vertices=vertices,
            triangles=triangles,
            normals=normals,
            uv=uv,
            color0=color0,
            submeshes=submeshes,
        )
        no_uv = compute_mesh_content_fingerprint(
            mesh_dict,
            None,
            vertices=vertices,
            triangles=triangles,
            normals=normals,
            color0=color0,
            submeshes=submeshes,
            include_uv=False,
        )
        return full or None, no_uv or None
    except Exception:
        return None, None


def capture_object_baselines(obj, *, material_refs=None, color_attributes_signature: str | None = None, reason: str = "unknown") -> dict:
    out = {
        "modifierStackSignature": set_modifier_stack_baseline(obj, reason=reason),
        "uvChannelsSignature": set_uv_channels_baseline(obj, reason=reason),
    }
    if color_attributes_signature is not None:
        out["colorAttributesSignature"] = set_color_attributes_baseline(obj, color_attributes_signature, reason=reason)
    if material_refs is not None:
        out["materialSlotsSignature"] = set_material_slots_baseline(obj, material_refs, reason=reason)
    return out
