from __future__ import annotations

try:
    import bpy  # type: ignore
except ImportError:
    bpy = None


def _iter_ancestors_inclusive(obj):
    current = obj
    while current is not None:
        yield current
        current = getattr(current, "parent", None)



def find_common_export_root(objects):
    candidates = [obj for obj in list(objects or []) if obj is not None]
    if not candidates:
        return None

    common = None
    for obj in candidates:
        chain = list(_iter_ancestors_inclusive(obj))
        chain.reverse()
        if common is None:
            common = chain
            continue

        next_common = []
        for left, right in zip(common, chain):
            left_ptr = getattr(left, "as_pointer", None)
            right_ptr = getattr(right, "as_pointer", None)
            left_key = left_ptr() if callable(left_ptr) else id(left)
            right_key = right_ptr() if callable(right_ptr) else id(right)
            if left_key != right_key:
                break
            next_common.append(left)
        common = next_common
        if not common:
            break

    if not common:
        return candidates[0]
    return common[-1]



def collect_armature_mesh_parts(armature_obj):
    if bpy is None or armature_obj is None or bpy.context is None or bpy.context.scene is None:
        return []

    parts = []
    armature_pointer = getattr(armature_obj, "as_pointer", None)
    armature_key = armature_pointer() if callable(armature_pointer) else id(armature_obj)
    for obj in list(getattr(bpy.context.scene, "objects", []) or []):
        if obj is None or getattr(obj, "type", None) != "MESH" or getattr(obj, "data", None) is None:
            continue
        find_armature = getattr(obj, "find_armature", None)
        bound_armature = find_armature() if callable(find_armature) else None
        if bound_armature is None:
            continue
        ptr = getattr(bound_armature, "as_pointer", None)
        bound_key = ptr() if callable(ptr) else id(bound_armature)
        if bound_key == armature_key:
            parts.append(obj)
    return parts



def dispose_export_prep_context(prep):
    if bpy is None or not prep:
        return

    temp_collection = prep.get("preparedCollection")
    clone_by_source_key = dict(prep.get("cloneBySourceKey") or {})
    for clone in reversed(list(clone_by_source_key.values())):
        if clone is None:
            continue
        try:
            if getattr(clone, "users_collection", None):
                for collection in list(clone.users_collection):
                    try:
                        collection.objects.unlink(clone)
                    except Exception:
                        pass
            bpy.data.objects.remove(clone, do_unlink=True)
        except Exception:
            pass

    if temp_collection is not None:
        try:
            if getattr(temp_collection, "users_scene", None):
                for scene in list(temp_collection.users_scene):
                    try:
                        scene.collection.children.unlink(temp_collection)
                    except Exception:
                        pass
            bpy.data.collections.remove(temp_collection)
        except Exception:
            pass


def build_export_prep_context(obj):
    if bpy is None or obj is None:
        return None

    armature = obj if getattr(obj, "type", None) == "ARMATURE" else getattr(obj, "find_armature", lambda: None)()
    if armature is None or getattr(armature, "data", None) is None:
        return None

    mesh_parts = collect_armature_mesh_parts(armature)
    if getattr(obj, "type", None) == "MESH" and getattr(obj, "data", None) is not None and obj not in mesh_parts:
        mesh_parts.append(obj)
    if not mesh_parts:
        return None

    export_root = find_common_export_root([armature] + list(mesh_parts)) or armature
    return {
        "armature": armature,
        "meshParts": mesh_parts,
        "exportRoot": export_root,
        "prepared": False,
        "preparedRoot": None,
        "preparedArmature": None,
        "preparedMeshParts": [],
        "preparedCollection": None,
        "cloneBySourceKey": {},
    }

