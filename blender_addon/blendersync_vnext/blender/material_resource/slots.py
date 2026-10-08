from __future__ import annotations

from dataclasses import dataclass

from blender.common.evaluated_mesh import evaluated_mesh_for_sync


@dataclass(frozen=True)
class MaterialExportSnapshot:
    materials: tuple
    triangle_material_indices: tuple[int, ...] | None
    uses_evaluated_mesh: bool
    source: str


def material_for_export(material):
    """Return the writable source datablock for an export material."""
    if material is None:
        return None
    try:
        if bool(getattr(material, "is_evaluated", False)):
            original = getattr(material, "original", None)
            if original is not None:
                return original
    except Exception:
        pass
    return material


def has_visible_geometry_nodes_modifier(obj) -> bool:
    for modifier in list(getattr(obj, "modifiers", []) or []):
        try:
            if bool(getattr(modifier, "show_viewport", True)) and str(getattr(modifier, "type", "") or "") == "NODES":
                return True
        except Exception:
            continue
    return False


def collect_materials_for_export(obj, *, mesh=None, evaluated_mesh: bool = False) -> list:
    """Collect slot-ordered materials from the mesh that owns the indices.

    Evaluated meshes carry the material table used by evaluated polygon
    ``material_index`` values.  Original meshes retain the object-slot
    semantics used by non-evaluated exports.
    """
    if evaluated_mesh:
        try:
            return [material_for_export(material) for material in list(getattr(mesh, "materials", []) or [])]
        except Exception:
            return []

    materials = []
    try:
        slots = list(getattr(obj, "material_slots", []) or [])
    except Exception:
        slots = []
    for slot in slots:
        try:
            material = getattr(slot, "material", None)
        except Exception:
            material = None
        materials.append(material_for_export(material))
    return materials


def _loop_triangles(mesh) -> list:
    if mesh is None:
        return []
    calc_loop_triangles = getattr(mesh, "calc_loop_triangles", None)
    if callable(calc_loop_triangles):
        calc_loop_triangles()
    try:
        return list(getattr(mesh, "loop_triangles", []) or [])
    except Exception:
        return []


def _triangle_topology_signature(triangle) -> tuple[tuple[int, ...], tuple[int, ...], int]:
    try:
        vertices = tuple(int(value) for value in triangle.vertices)
    except Exception:
        vertices = tuple()
    try:
        loops = tuple(int(value) for value in triangle.loops)
    except Exception:
        loops = tuple()
    try:
        polygon_index = int(getattr(triangle, "polygon_index", -1))
    except Exception:
        polygon_index = -1
    return vertices, loops, polygon_index


def material_index_topology_matches(geometry_mesh, evaluated_mesh) -> bool:
    """Return whether evaluated triangle material indices fit geometry_mesh."""
    if geometry_mesh is None or evaluated_mesh is None:
        return False
    for attr in ("vertices", "polygons", "loops"):
        try:
            if len(getattr(geometry_mesh, attr, []) or []) != len(getattr(evaluated_mesh, attr, []) or []):
                return False
        except Exception:
            return False

    geometry_triangles = _loop_triangles(geometry_mesh)
    evaluated_triangles = _loop_triangles(evaluated_mesh)
    if len(geometry_triangles) != len(evaluated_triangles):
        return False
    return all(
        _triangle_topology_signature(geometry_triangle) == _triangle_topology_signature(evaluated_triangle)
        for geometry_triangle, evaluated_triangle in zip(geometry_triangles, evaluated_triangles)
    )


def _triangle_material_indices(mesh) -> tuple[int, ...]:
    indices = []
    for triangle in _loop_triangles(mesh):
        try:
            indices.append(max(0, int(getattr(triangle, "material_index", 0) or 0)))
        except Exception:
            indices.append(0)
    return tuple(indices)


def compact_evaluated_material_slots(materials, triangle_material_indices) -> tuple[tuple, tuple[int, ...]]:
    """Drop unreferenced empty slots from an evaluated material table.

    Geometry Nodes can append a material to the evaluated mesh while leaving
    an unused object slot at the front of the evaluated material table. Unity
    material arrays are slot ordered, so remove only empty slots that no
    triangle uses and remap the triangle indices together with them. Real
    materials and used empty slots retain their existing slot semantics.
    """
    material_table = tuple(materials or ())
    try:
        indices = tuple(max(0, int(value or 0)) for value in (triangle_material_indices or ()))
    except Exception:
        return material_table, tuple(triangle_material_indices or ())
    if not material_table or not indices:
        return material_table, indices

    if any(index >= len(material_table) for index in indices):
        # Preserve the original data when Blender exposes an inconsistent
        # table; silently remapping an invalid index would hide a corruption.
        return material_table, indices

    used_slots = set(indices)
    removable_slots = {
        slot
        for slot, material in enumerate(material_table)
        if material is None and slot not in used_slots
    }
    if not removable_slots:
        return material_table, indices

    retained_slots = tuple(slot for slot in range(len(material_table)) if slot not in removable_slots)
    remap = {old_slot: new_slot for new_slot, old_slot in enumerate(retained_slots)}
    compacted_materials = tuple(material_table[old_slot] for old_slot in retained_slots)
    compacted_indices = tuple(remap[index] for index in indices)
    return compacted_materials, compacted_indices


def collect_material_export_snapshot(
    obj,
    geometry_mesh,
    *,
    geometry_is_evaluated: bool = False,
    allow_topology_compatible_evaluated: bool = False,
) -> MaterialExportSnapshot:
    """Capture the material table and indices used by an exported mesh.

    Rigged and shape-key exports retain original vertex topology. When Geometry
    Nodes only changes material assignment, a topology-compatible evaluated
    mesh can still provide its material table and per-triangle indices without
    invalidating skin or shape-key source indices.
    """
    if geometry_is_evaluated:
        materials = collect_materials_for_export(obj, mesh=geometry_mesh, evaluated_mesh=True)
        triangle_material_indices = _triangle_material_indices(geometry_mesh)
        materials, triangle_material_indices = compact_evaluated_material_slots(
            materials,
            triangle_material_indices,
        )
        return MaterialExportSnapshot(
            materials=materials,
            triangle_material_indices=triangle_material_indices,
            uses_evaluated_mesh=True,
            source="evaluated_geometry",
        )

    fallback = MaterialExportSnapshot(
        materials=tuple(collect_materials_for_export(obj)),
        triangle_material_indices=None,
        uses_evaluated_mesh=False,
        source="object_slots",
    )
    if (
        geometry_mesh is None
        or not allow_topology_compatible_evaluated
        or not has_visible_geometry_nodes_modifier(obj)
    ):
        return fallback

    try:
        with evaluated_mesh_for_sync(obj) as lease:
            evaluated_mesh = getattr(lease, "mesh", None)
            if not material_index_topology_matches(geometry_mesh, evaluated_mesh):
                return fallback
            materials = collect_materials_for_export(obj, mesh=evaluated_mesh, evaluated_mesh=True)
            triangle_material_indices = _triangle_material_indices(evaluated_mesh)
            materials, triangle_material_indices = compact_evaluated_material_slots(
                materials,
                triangle_material_indices,
            )
            return MaterialExportSnapshot(
                materials=materials,
                triangle_material_indices=triangle_material_indices,
                uses_evaluated_mesh=True,
                source=str(getattr(lease, "source", None) or "evaluated_materials"),
            )
    except Exception:
        return fallback


def material_refs_from_live_context(context, *, pair_id: str | None = None, mesh_ref: str | None = None) -> tuple[str, ...] | None:
    """Read slot-ordered refs back from an already-built live context."""
    if not isinstance(context, dict):
        return None
    wanted_pair = str(pair_id or "").strip()
    wanted_mesh = str(mesh_ref or "").strip()
    assemblies = list(context.get("objectAssemblies") or [])
    single = context.get("objectAssembly")
    if isinstance(single, dict):
        assemblies.append(single)
    for assembly in assemblies:
        if not isinstance(assembly, dict):
            continue
        assembly_pair = str(assembly.get("pairId") or "").strip()
        assembly_mesh = str(assembly.get("meshRef") or "").strip()
        if wanted_pair and assembly_pair != wanted_pair:
            continue
        if wanted_mesh and assembly_mesh != wanted_mesh:
            continue
        if "materialRefs" in assembly:
            return tuple(assembly.get("materialRefs") or [])

    for payload in list(context.get("riggedObjects") or []):
        if not isinstance(payload, dict):
            continue
        rig = payload.get("riggedObject") or {}
        if isinstance(rig, dict):
            if (not wanted_mesh or str(rig.get("meshRef") or "").strip() == wanted_mesh) and "materialRefs" in rig:
                return tuple(rig.get("materialRefs") or [])
            for part in list(rig.get("meshParts") or []):
                if not isinstance(part, dict):
                    continue
                if (not wanted_mesh or str(part.get("meshRef") or "").strip() == wanted_mesh) and "materialRefs" in part:
                    return tuple(part.get("materialRefs") or [])
    return None
