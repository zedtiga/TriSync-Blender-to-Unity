import sys
import unittest
from pathlib import Path
from types import SimpleNamespace


ADDON_ROOT = Path(__file__).resolve().parents[2] / "blender_addon" / "blendersync_vnext"
sys.path.insert(0, str(ADDON_ROOT))

from blender.common.evaluated_mesh import (
    count_evaluated_instances,
    evaluated_mesh_build_in_progress,
    evaluated_mesh_for_sync,
)


class EvaluatedMeshHelperTests(unittest.TestCase):
    def test_count_evaluated_instances_accepts_original_and_evaluated_parents(self) -> None:
        original = SimpleNamespace(name="Source")
        evaluated = SimpleNamespace(original=original, name="Source_eval")
        unrelated = SimpleNamespace(name="Other")
        depsgraph = SimpleNamespace(
            object_instances=[
                SimpleNamespace(is_instance=True, parent=original),
                SimpleNamespace(is_instance=True, parent=evaluated),
                SimpleNamespace(is_instance=True, parent=unrelated),
                SimpleNamespace(is_instance=False, parent=original),
            ]
        )

        self.assertEqual(2, count_evaluated_instances(depsgraph, original, evaluated))

    def test_count_evaluated_instances_handles_missing_depsgraph(self) -> None:
        self.assertEqual(0, count_evaluated_instances(None, object()))

    def test_non_blender_fallback_still_yields_original_mesh(self) -> None:
        mesh = SimpleNamespace(vertices=[object()])
        obj = SimpleNamespace(data=mesh)

        with evaluated_mesh_for_sync(obj) as lease:
            self.assertIs(mesh, lease.mesh)
            self.assertEqual("original_fallback", lease.source)
            self.assertFalse(lease.realized_instances)

    def test_build_scope_is_idle_after_non_blender_fallback(self) -> None:
        mesh = SimpleNamespace(vertices=[object()])
        obj = SimpleNamespace(data=mesh)

        with evaluated_mesh_for_sync(obj):
            self.assertFalse(evaluated_mesh_build_in_progress())

        self.assertFalse(evaluated_mesh_build_in_progress())


if __name__ == "__main__":
    unittest.main()
