from __future__ import annotations

import time

from blender.scene_sync.settings import get_view_sync_scale

try:
    import bpy  # type: ignore
except ImportError:
    bpy = None


def _find_view3d_region_data(context):
    if bpy is None or context is None:
        return None, None

    space = getattr(context, "space_data", None)
    if space is not None and getattr(space, "type", None) == "VIEW_3D":
        rv3d = getattr(space, "region_3d", None)
        if rv3d is not None:
            return space, rv3d

    area = getattr(context, "area", None)
    if area is not None and getattr(area, "type", None) == "VIEW_3D":
        for candidate in getattr(area, "spaces", []) or []:
            if getattr(candidate, "type", None) == "VIEW_3D":
                rv3d = getattr(candidate, "region_3d", None)
                if rv3d is not None:
                    return candidate, rv3d

    screen = getattr(context, "screen", None)
    for area in getattr(screen, "areas", []) or []:
        if getattr(area, "type", None) != "VIEW_3D":
            continue
        for candidate in getattr(area, "spaces", []) or []:
            if getattr(candidate, "type", None) == "VIEW_3D":
                rv3d = getattr(candidate, "region_3d", None)
                if rv3d is not None:
                    return candidate, rv3d

    return None, None


def _vector3_values(value) -> list[float]:
    return [
        float(getattr(value, "x", 0.0)),
        float(getattr(value, "y", 0.0)),
        float(getattr(value, "z", 0.0)),
    ]


def _quaternion_values(value) -> list[float]:
    return [
        float(getattr(value, "x", 0.0)),
        float(getattr(value, "y", 0.0)),
        float(getattr(value, "z", 0.0)),
        float(getattr(value, "w", 1.0)),
    ]


def build_scene_view_state_context(session, context) -> dict:
    if bpy is None or context is None:
        raise ValueError("blender_context_missing")

    space, rv3d = _find_view3d_region_data(context)
    if rv3d is None:
        raise ValueError("view3d_region_missing")

    view_location = getattr(rv3d, "view_location", None)
    view_rotation = getattr(rv3d, "view_rotation", None)
    if view_location is None or view_rotation is None:
        raise ValueError("view_state_missing")

    try:
        # RegionView3D.view_rotation rotates canonical view axes into Blender world.
        # Derive explicit camera forward/up vectors and let Unity reconstruct the view
        # via LookRotation; this avoids ambiguous object-vs-view quaternion semantics.
        view_forward = view_rotation @ __import__("mathutils").Vector((0.0, 0.0, -1.0))
        view_up = view_rotation @ __import__("mathutils").Vector((0.0, 1.0, 0.0))
    except Exception:
        view_forward = None
        view_up = None

    view_perspective = str(getattr(rv3d, "view_perspective", "PERSP") or "PERSP")
    lens = float(getattr(space, "lens", 50.0) or 50.0) if space is not None else 50.0
    clip_start = float(getattr(space, "clip_start", 0.01) or 0.01) if space is not None else 0.01
    clip_end = float(getattr(space, "clip_end", 1000.0) or 1000.0) if space is not None else 1000.0
    distance = float(getattr(rv3d, "view_distance", 0.0) or 0.0)
    view_scale = max(0.1, min(5.0, float(get_view_sync_scale(context))))

    return {
        "session": session,
        "triggerType": "scene_view_state",
        "viewStatePayload": {
            "type": "scene_sync.view_state_v1",
            "timestamp": int(time.time()),
            "sourceHint": "manual_view_sync",
            "viewMode": view_perspective,
            "isOrthographic": view_perspective == "ORTHO",
            "pivot": _vector3_values(view_location),
            "rotation": _quaternion_values(view_rotation),
            "forward": _vector3_values(view_forward) if view_forward is not None else [],
            "up": _vector3_values(view_up) if view_up is not None else [],
            "distance": distance,
            "lens": lens,
            "orthographicScale": distance,
            "viewScale": view_scale,
            "clipStart": clip_start,
            "clipEnd": clip_end,
        },
    }
