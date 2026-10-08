from __future__ import annotations

from blender.scene_sync.types import TransformSnapshot


def in_scope(policy_scope: str, object_scope: str) -> bool:
    if policy_scope == "all":
        return True
    return policy_scope == object_scope


def above_threshold(
    previous: TransformSnapshot,
    current: TransformSnapshot,
    threshold: float,
) -> bool:
    def max_axis_delta(a: tuple[float, ...], b: tuple[float, ...]) -> float:
        size = max(len(a), len(b))
        max_delta = 0.0
        for i in range(size):
            av = a[i] if i < len(a) else 0.0
            bv = b[i] if i < len(b) else 0.0
            max_delta = max(max_delta, abs(av - bv))
        return max_delta

    pos_delta = max_axis_delta(previous.position, current.position)
    rot_delta = max_axis_delta(previous.rotation, current.rotation)
    scale_delta = max_axis_delta(previous.scale, current.scale)
    return max(pos_delta, rot_delta, scale_delta) >= threshold

