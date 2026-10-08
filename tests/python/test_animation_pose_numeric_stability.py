from __future__ import annotations

import math
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock


ADDON_ROOT = Path(__file__).resolve().parents[2] / "blender_addon" / "blendersync_vnext"
sys.path.insert(0, str(ADDON_ROOT))

from blender.animation_clip import core


class _Vector3:
    def __init__(self, x: float, y: float, z: float) -> None:
        self.x = float(x)
        self.y = float(y)
        self.z = float(z)

    def __getitem__(self, index: int) -> float:
        return (self.x, self.y, self.z)[index]


class _Quaternion:
    def __init__(self) -> None:
        self.x = 0.0
        self.y = 0.0
        self.z = 0.0
        self.w = 1.0

    def normalize(self) -> None:
        pass


class _DecomposedMatrix:
    def __init__(self, position: _Vector3, scale: _Vector3) -> None:
        self._position = position
        self._scale = scale

    def decompose(self):
        return self._position, _Quaternion(), self._scale

    def copy(self):
        return self


class _RestMatrix:
    def __init__(self, product) -> None:
        self._product = product

    def inverted_safe(self):
        return self

    def __matmul__(self, _other):
        return self._product


class _Column:
    def __init__(self, length: float) -> None:
        self.length = float(length)


class _MatrixWithColumns:
    def __init__(self, lengths: tuple[float, float, float]) -> None:
        self._matrix3 = SimpleNamespace(
            col=[_Column(length) for length in lengths]
        )

    def to_3x3(self):
        return self._matrix3


class AnimationPoseNumericStabilityTests(unittest.TestCase):
    def test_snap_vector_replaces_only_finite_values_inside_noise_window(self) -> None:
        values = (0.9999997, 1.000009, 0.99998)

        self.assertEqual(
            (1.0, 1.0, 0.99998),
            core._snap_vector_to_reference(values, (1.0, 1.0, 1.0), 1e-5),
        )
        self.assertTrue(
            math.isnan(
                core._snap_scalar_to_reference(float("nan"), 1.0, 1e-5)
            )
        )

    def test_stable_scale_uses_column_lengths_before_snapping(self) -> None:
        scale = core._stable_bone_matrix_scale(
            _MatrixWithColumns((1.00000002, 0.99999998, 1.0002)),
            _Vector3(1.0000325, 0.9999675, 1.0002),
        )

        self.assertEqual(
            (1.00000002, 0.99999998, 1.0002),
            scale,
        )
        self.assertEqual(
            (1.0, 1.0, 1.0002),
            core._snap_vector_to_reference(scale, (1.0, 1.0, 1.0), 1e-5),
        )

    def test_evaluated_pose_snaps_matrix_noise_to_authored_bone_channels(self) -> None:
        basis = _DecomposedMatrix(
            _Vector3(3e-6, -4e-6, 5e-6),
            _Vector3(0.9999997, 1.000003, 0.999997),
        )
        pose_bone = SimpleNamespace(
            location=_Vector3(0.0, 0.0, 0.0),
            scale=_Vector3(1.0, 1.0, 1.0),
        )

        with mock.patch.object(
            core,
            "_get_evaluated_local_pose_matrix",
            return_value=object(),
        ), mock.patch.object(
            core,
            "_get_rest_local_matrix",
            return_value=_RestMatrix(basis),
        ), mock.patch.object(
            core,
            "_get_unity_rig_v1_rest_local_position",
            return_value=(0.0, 0.0, 1.0),
        ):
            position, _rotation, scale = core.sample_pose_bone_transform_unity_rig_v1(
                pose_bone
            )

        self.assertEqual((0.0, 0.0, 1.0), position)
        self.assertEqual((1.0, 1.0, 1.0), scale)

    def test_evaluated_pose_preserves_meaningful_constraint_output(self) -> None:
        basis = _DecomposedMatrix(
            _Vector3(2e-5, 0.0, 0.0),
            _Vector3(1.00002, 1.0, 1.0),
        )
        pose_bone = SimpleNamespace(
            location=_Vector3(0.0, 0.0, 0.0),
            scale=_Vector3(1.0, 1.0, 1.0),
        )

        with mock.patch.object(
            core,
            "_get_evaluated_local_pose_matrix",
            return_value=object(),
        ), mock.patch.object(
            core,
            "_get_rest_local_matrix",
            return_value=_RestMatrix(basis),
        ), mock.patch.object(
            core,
            "_get_unity_rig_v1_rest_local_position",
            return_value=(0.0, 0.0, 1.0),
        ):
            position, _rotation, scale = core.sample_pose_bone_transform_unity_rig_v1(
                pose_bone
            )

        self.assertEqual((2e-5, 0.0, 1.0), position)
        self.assertEqual((1.00002, 1.0, 1.0), scale)

    def test_preserve_rest_axes_pose_snaps_to_authored_local_transform(self) -> None:
        evaluated = _DecomposedMatrix(
            _Vector3(1.000004, 1.999995, 3.000003),
            _Vector3(0.9999997, 1.000003, 0.999997),
        )
        authored = _DecomposedMatrix(
            _Vector3(1.0, 2.0, 3.0),
            _Vector3(1.0, 1.0, 1.0),
        )
        authored_basis = _DecomposedMatrix(
            _Vector3(0.0, 0.0, 0.0),
            _Vector3(1.0, 1.0, 1.0),
        )
        pose_bone = SimpleNamespace(matrix_basis=authored_basis, parent=None)

        with mock.patch.object(
            core,
            "_get_evaluated_local_pose_matrix",
            return_value=object(),
        ), mock.patch.object(
            core,
            "_get_rest_local_matrix",
            return_value=_RestMatrix(object()),
        ), mock.patch.object(
            core,
            "_map_preserve_rest_bone_matrix",
            side_effect=[evaluated, authored],
        ):
            position, _rotation, scale = (
                core.sample_pose_bone_transform_preserve_rest_bone_axes(pose_bone)
            )

        self.assertEqual((1.0, 2.0, 3.0), position)
        self.assertEqual((1.0, 1.0, 1.0), scale)


if __name__ == "__main__":
    unittest.main()
