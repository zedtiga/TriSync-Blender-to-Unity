from __future__ import annotations

try:
    from mathutils import Matrix  # type: ignore
    from bpy_extras.io_utils import axis_conversion  # type: ignore
except ImportError:
    Matrix = None
    axis_conversion = None


RIG_AXIS_MODE_BAKED_JOINT_AXES = "baked_joint_axes"
RIG_AXIS_MODE_PRESERVE_REST_BONE_AXES = "preserve_rest_bone_axes"
DEFAULT_PRIMARY_BONE_AXIS = "Z"
DEFAULT_SECONDARY_BONE_AXIS = "X"
VALID_AXES = {"X", "Y", "Z", "-X", "-Y", "-Z"}


def normalize_bone_axis_pair(primary_axis: str | None, secondary_axis: str | None) -> tuple[str, str]:
    primary = str(primary_axis or DEFAULT_PRIMARY_BONE_AXIS).strip().upper()
    secondary = str(secondary_axis or DEFAULT_SECONDARY_BONE_AXIS).strip().upper()
    if primary not in VALID_AXES:
        primary = DEFAULT_PRIMARY_BONE_AXIS
    if secondary not in VALID_AXES:
        secondary = DEFAULT_SECONDARY_BONE_AXIS
    if _axis_family(primary) == _axis_family(secondary):
        return DEFAULT_PRIMARY_BONE_AXIS, DEFAULT_SECONDARY_BONE_AXIS
    return primary, secondary


def build_output_bone_axis_correction(primary_axis: str | None, secondary_axis: str | None):
    if Matrix is None or axis_conversion is None:
        return None, None

    primary, secondary = normalize_bone_axis_pair(primary_axis, secondary_axis)
    if (primary, secondary) == (DEFAULT_PRIMARY_BONE_AXIS, DEFAULT_SECONDARY_BONE_AXIS):
        return Matrix.Identity(4), Matrix.Identity(4)

    correction = axis_conversion(
        from_forward=secondary,
        from_up=primary,
        to_forward=DEFAULT_SECONDARY_BONE_AXIS,
        to_up=DEFAULT_PRIMARY_BONE_AXIS,
    ).to_4x4()
    return correction, correction.inverted_safe()


def apply_output_bone_axis_correction(local_matrix, *, has_parent: bool, correction, correction_inv):
    if local_matrix is None or correction is None or correction_inv is None:
        return local_matrix
    if has_parent:
        return correction_inv @ local_matrix @ correction
    return local_matrix @ correction


def _axis_family(axis: str) -> str:
    return str(axis or "").replace("-", "")
