from __future__ import annotations

import uuid

from blender.common.log import warn

try:
    import bpy  # type: ignore
except ImportError:
    bpy = None

INSTANCE_ID_KEY = "blendersync_instance_id"
ASSET_ID_KEY = "blendersync_asset_id"
MESH_ASSET_ID_KEY = "blendersync_mesh_asset_id"
MESH_REF_COUNT_KEY = "blendersync_mesh_ref_count"
MESH_REF_SHARED_KEY = "blendersync_mesh_ref_shared"
MESH_REF_USAGE_UPDATED_AT_KEY = "blendersync_mesh_ref_usage_updated_at"
_INSTANCE_OWNER_SESSION_UIDS: dict[str, int] = {}


def _new_uuid() -> str:
    return str(uuid.uuid4())


def _session_uid(datablock) -> int | None:
    if datablock is None:
        return None
    try:
        value = int(getattr(datablock, "session_uid", 0) or 0)
    except Exception:
        return None
    return value if value > 0 else None


def reset_runtime_identity_cache() -> None:
    _INSTANCE_OWNER_SESSION_UIDS.clear()


def get_instance_id(obj) -> str | None:
    if obj is None:
        return None
    try:
        value = obj.get(INSTANCE_ID_KEY)
    except Exception:
        return None
    return str(value) if value else None


def _has_instance_id_conflict(obj, instance_id: str) -> bool:
    if obj is None or not instance_id or bpy is None or bpy.context is None or bpy.context.scene is None:
        return False

    current_name = getattr(obj, "name", None)
    current_session_uid = _session_uid(obj)
    known_owner_session_uid = _INSTANCE_OWNER_SESSION_UIDS.get(instance_id)
    if (
        known_owner_session_uid is not None
        and current_session_uid is not None
        and known_owner_session_uid == current_session_uid
    ):
        return False

    for other in getattr(bpy.context.scene, "objects", []) or []:
        if other is None:
            continue
        if _same_datablock(obj, other):
            continue
        if current_session_uid is None and current_name is not None and getattr(other, "name", None) == current_name:
            continue

        other_id = get_instance_id(other)
        if other_id and other_id == instance_id:
            return True
    return False


def ensure_instance_id(obj) -> str:
    current = get_instance_id(obj)
    if current and not _has_instance_id_conflict(obj, current):
        current_session_uid = _session_uid(obj)
        if current_session_uid is not None:
            _INSTANCE_OWNER_SESSION_UIDS[current] = current_session_uid
        return current

    new_id = _new_uuid()
    obj[INSTANCE_ID_KEY] = new_id
    current_session_uid = _session_uid(obj)
    if current_session_uid is not None:
        _INSTANCE_OWNER_SESSION_UIDS[new_id] = current_session_uid

    if current and current != new_id:
        try:
            obj_name = getattr(obj, "name", "<unnamed>")
        except Exception:
            obj_name = "<name-error>"
        warn(
            "Identity",
            "instance_id_reassigned",
            "Reassigned a conflicting object identity.",
            {"objectName": obj_name, "oldInstanceId": current, "newInstanceId": new_id},
        )

    return new_id


def ensure_rigged_object_id(obj) -> str:
    return f"rigobj-{ensure_instance_id(obj)}"


def get_asset_id(datablock) -> str | None:
    if datablock is None:
        return None
    try:
        value = datablock.get(ASSET_ID_KEY)
    except Exception:
        return None
    return str(value) if value else None


def ensure_asset_id(datablock) -> str:
    current = get_asset_id(datablock)
    if current:
        return current
    new_id = _new_uuid()
    datablock[ASSET_ID_KEY] = new_id
    return new_id


def get_mesh_asset_id_for_object(obj) -> str | None:
    if obj is None:
        return None
    try:
        value = obj.get(MESH_ASSET_ID_KEY)
    except Exception:
        value = None
    if value:
        return str(value)

    # Compatibility fallback for projects created before mesh asset ownership
    # moved from Blender Mesh datablocks to Blender Objects.
    return get_asset_id(getattr(obj, "data", None))


def ensure_mesh_asset_id_for_object(obj) -> str:
    current = get_mesh_asset_id_for_object(obj)
    if current:
        try:
            obj[MESH_ASSET_ID_KEY] = current
        except Exception:
            pass
        return current

    new_id = _new_uuid()
    obj[MESH_ASSET_ID_KEY] = new_id
    return new_id


def set_mesh_asset_id_for_object(obj, asset_id: str) -> str:
    value = str(asset_id or "").strip()
    if not value:
        value = _new_uuid()
    obj[MESH_ASSET_ID_KEY] = value
    return value


def _same_datablock(a, b) -> bool:
    if a is b:
        return True
    try:
        return a is not None and b is not None and a.as_pointer() == b.as_pointer()
    except Exception:
        return False


def _find_asset_id_owner(datablock, asset_id: str, candidates) -> object | None:
    if not asset_id or candidates is None:
        return datablock

    try:
        iterable = list(candidates)
    except Exception:
        return datablock

    for other in iterable:
        if other is None:
            continue
        try:
            if get_asset_id(other) == asset_id:
                return other
        except Exception:
            continue
    return datablock


def ensure_unique_asset_id(datablock, candidates=None, *, label: str = "asset") -> str:
    current = ensure_asset_id(datablock)
    owner = _find_asset_id_owner(datablock, current, candidates)
    if owner is None or _same_datablock(owner, datablock):
        return current

    new_id = _new_uuid()
    datablock[ASSET_ID_KEY] = new_id

    try:
        name = getattr(datablock, "name", "<unnamed>")
        owner_name = getattr(owner, "name", "<unnamed>")
    except Exception:
        name = "<name-error>"
        owner_name = "<name-error>"
    warn(
        "Identity",
        "asset_id_reassigned",
        "Reassigned a conflicting asset identity.",
        {
            "assetLabel": label,
            "assetName": name,
            "ownerName": owner_name,
            "oldAssetId": current,
            "newAssetId": new_id,
        },
    )
    return new_id
