from __future__ import annotations

SUPPORTED_OBJECT_TYPES = {"MESH", "ARMATURE", "CAMERA", "LIGHT", "EMPTY"}
UNSUPPORTED_OBJECT_TYPES = {"CURVE", "FONT", "SURFACE", "META", "VOLUME", "POINTCLOUD", "GPENCIL", "GREASEPENCIL", "LATTICE", "SPEAKER"}


def get_object_type(obj) -> str:
    return str(getattr(obj, "type", "") or "").upper()


def is_empty(obj) -> bool:
    return get_object_type(obj) == "EMPTY"


def is_supported_scene_object(obj) -> bool:
    object_type = get_object_type(obj)
    if object_type == "MESH":
        return getattr(obj, "data", None) is not None
    if object_type in {"CAMERA", "LIGHT"}:
        return getattr(obj, "data", None) is not None
    return object_type in {"ARMATURE", "EMPTY"}


def classify_scene_object(obj) -> dict:
    name = str(getattr(obj, "name", "") or "") if obj is not None else ""
    object_type = get_object_type(obj) if obj is not None else "NONE"

    if obj is None:
        return {
            "supported": False,
            "kind": "none",
            "objectType": object_type,
            "objectName": name,
            "reason": "object_missing",
        }

    if object_type == "MESH":
        if getattr(obj, "data", None) is None:
            return {"supported": False, "kind": "mesh", "objectType": object_type, "objectName": name, "reason": "mesh_data_missing"}
        return {"supported": True, "kind": "mesh", "objectType": object_type, "objectName": name, "reason": None}

    if object_type == "ARMATURE":
        return {"supported": True, "kind": "armature", "objectType": object_type, "objectName": name, "reason": None}

    if object_type == "CAMERA":
        if getattr(obj, "data", None) is None:
            return {"supported": False, "kind": "camera", "objectType": object_type, "objectName": name, "reason": "camera_data_missing"}
        return {"supported": True, "kind": "camera", "objectType": object_type, "objectName": name, "reason": None}

    if object_type == "LIGHT":
        if getattr(obj, "data", None) is None:
            return {"supported": False, "kind": "light", "objectType": object_type, "objectName": name, "reason": "light_data_missing"}
        return {"supported": True, "kind": "light", "objectType": object_type, "objectName": name, "reason": None}

    if object_type == "EMPTY":
        return {"supported": True, "kind": "empty", "objectType": object_type, "objectName": name, "reason": None}

    if object_type in UNSUPPORTED_OBJECT_TYPES:
        return {
            "supported": False,
            "kind": "unsupported",
            "objectType": object_type,
            "objectName": name,
            "reason": "unsupported_object_type",
        }

    return {
        "supported": False,
        "kind": "unsupported",
        "objectType": object_type,
        "objectName": name,
        "reason": "unsupported_object_type",
    }


def summarize_unsupported(objects: list) -> list[dict]:
    out = []
    for obj in objects or []:
        info = classify_scene_object(obj)
        if not info.get("supported"):
            out.append(info)
    return out
