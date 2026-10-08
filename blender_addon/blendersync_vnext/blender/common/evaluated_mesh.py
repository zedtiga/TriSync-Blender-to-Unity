from __future__ import annotations

import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Iterator

try:
    import bpy  # type: ignore
except ImportError:
    bpy = None


TEMPORARY_EVALUATED_OBJECT_KEY = "blendersync_temporary_evaluated_mesh"
_evaluated_mesh_build_depth = 0


def evaluated_mesh_build_in_progress() -> bool:
    return _evaluated_mesh_build_depth > 0


@dataclass
class EvaluatedMeshLease:
    owner: object | None
    mesh: object | None
    source: str
    instance_count: int = 0
    realized_instances: bool = False


def _is_owned_by_object(candidate, original, evaluated) -> bool:
    if candidate is original or candidate is evaluated:
        return True
    try:
        candidate_original = getattr(candidate, "original", None)
    except Exception:
        candidate_original = None
    return candidate_original is original or candidate_original is evaluated


def count_evaluated_instances(depsgraph, original, evaluated=None) -> int:
    """Count generated instances whose parent is the requested object."""
    if depsgraph is None or original is None:
        return 0
    count = 0
    try:
        for item in depsgraph.object_instances:
            try:
                if not bool(getattr(item, "is_instance", False)):
                    continue
                parent = getattr(item, "parent", None)
                if _is_owned_by_object(parent, original, evaluated):
                    count += 1
            except (ReferenceError, RuntimeError):
                continue
    except Exception:
        return count
    return count


def _build_realize_instances_group():
    if bpy is None:
        raise RuntimeError("blender_api_unavailable")

    tree = bpy.data.node_groups.new(
        name=f"__BlenderSyncRealizeInstances_{uuid.uuid4().hex}",
        type="GeometryNodeTree",
    )
    tree.interface.new_socket(
        name="Geometry",
        in_out="INPUT",
        socket_type="NodeSocketGeometry",
    )
    tree.interface.new_socket(
        name="Geometry",
        in_out="OUTPUT",
        socket_type="NodeSocketGeometry",
    )

    nodes = tree.nodes
    links = tree.links
    group_input = nodes.new("NodeGroupInput")
    realize = nodes.new("GeometryNodeRealizeInstances")
    group_output = nodes.new("NodeGroupOutput")
    links.new(group_input.outputs["Geometry"], realize.inputs["Geometry"])
    links.new(realize.outputs["Geometry"], group_output.inputs["Geometry"])
    return tree


@contextmanager
def _realized_mesh_for_object(obj, instance_count: int) -> Iterator[EvaluatedMeshLease]:
    if bpy is None or bpy.context is None:
        raise RuntimeError("blender_context_missing")

    temporary_object = None
    temporary_tree = None
    evaluated = None
    mesh = None
    try:
        temporary_object = obj.copy()
        temporary_object.name = (
            f"__BlenderSyncRealize_{getattr(obj, 'name', 'Object')}_"
            f"{uuid.uuid4().hex[:8]}"
        )
        # Prevent the short-lived probe object from being treated as a managed
        # TriSync-managed object by lifecycle or depsgraph handlers.
        for key in list(temporary_object.keys()):
            if str(key).startswith("blendersync_"):
                del temporary_object[key]
        temporary_object[TEMPORARY_EVALUATED_OBJECT_KEY] = True
        try:
            temporary_object.hide_viewport = False
            temporary_object.hide_render = False
            temporary_object.hide_set(False)
        except Exception:
            pass

        scene = getattr(bpy.context, "scene", None)
        collection = getattr(scene, "collection", None) if scene is not None else None
        if collection is None:
            raise RuntimeError("temporary_realize_collection_missing")
        collection.objects.link(temporary_object)

        temporary_tree = _build_realize_instances_group()
        modifier = temporary_object.modifiers.new(
            name="__BlenderSyncRealizeInstances",
            type="NODES",
        )
        modifier.node_group = temporary_tree
        modifier.show_viewport = True

        depsgraph = bpy.context.evaluated_depsgraph_get()
        depsgraph.update()
        evaluated = temporary_object.evaluated_get(depsgraph)
        mesh = evaluated.to_mesh(
            preserve_all_data_layers=True,
            depsgraph=depsgraph,
        )
        if mesh is None:
            raise RuntimeError("evaluated_instances_realize_failed")
        yield EvaluatedMeshLease(
            owner=evaluated,
            mesh=mesh,
            source="evaluated_realized_instances",
            instance_count=instance_count,
            realized_instances=True,
        )
    finally:
        if evaluated is not None and mesh is not None:
            try:
                evaluated.to_mesh_clear()
            except Exception:
                pass
        if temporary_object is not None:
            try:
                bpy.data.objects.remove(temporary_object, do_unlink=True)
            except Exception:
                pass
        if temporary_tree is not None:
            try:
                bpy.data.node_groups.remove(temporary_tree)
            except Exception:
                pass


@contextmanager
def evaluated_mesh_for_sync(obj, *, depsgraph=None) -> Iterator[EvaluatedMeshLease]:
    """Acquire an evaluated mesh, realizing Geometry Nodes instances when needed."""
    global _evaluated_mesh_build_depth
    if obj is None or getattr(obj, "data", None) is None:
        yield EvaluatedMeshLease(owner=obj, mesh=None, source="missing_object")
        return
    if bpy is None or bpy.context is None:
        yield EvaluatedMeshLease(
            owner=obj,
            mesh=getattr(obj, "data", None),
            source="original_fallback",
        )
        return

    _evaluated_mesh_build_depth += 1
    drain_depsgraph = False
    try:
        depsgraph = depsgraph or bpy.context.evaluated_depsgraph_get()
        evaluated = obj.evaluated_get(depsgraph)
        instance_count = count_evaluated_instances(depsgraph, obj, evaluated)
        if instance_count > 0:
            drain_depsgraph = True
            with _realized_mesh_for_object(obj, instance_count) as lease:
                yield lease
            return

        mesh = None
        try:
            mesh = evaluated.to_mesh(
                preserve_all_data_layers=True,
                depsgraph=depsgraph,
            )
            yield EvaluatedMeshLease(
                owner=evaluated,
                mesh=mesh,
                source="evaluated",
                instance_count=0,
                realized_instances=False,
            )
        finally:
            if mesh is not None:
                try:
                    evaluated.to_mesh_clear()
                except Exception:
                    pass
    finally:
        # Removing the temporary object/tree can leave deferred depsgraph tags.
        # Drain them while callers can still identify this as an internal build.
        if drain_depsgraph:
            try:
                view_layer = getattr(bpy.context, "view_layer", None)
                if view_layer is not None:
                    view_layer.update()
            except Exception:
                pass
        _evaluated_mesh_build_depth = max(0, _evaluated_mesh_build_depth - 1)
