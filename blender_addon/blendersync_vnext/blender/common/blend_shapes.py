from __future__ import annotations

from array import array


def read_shape_key_coords_fast(key_block, vertex_count: int) -> array:
    coords = array("f", [0.0]) * max(0, int(vertex_count) * 3)
    data = getattr(key_block, "data", None)
    if data is None or vertex_count <= 0:
        return coords
    try:
        data.foreach_get("co", coords)
        return coords
    except Exception:
        # Fallback for unusual Blender/Python collection behavior.
        for i in range(min(vertex_count, len(data))):
            co = data[i].co
            j = i * 3
            coords[j] = float(co.x)
            coords[j + 1] = float(co.y)
            coords[j + 2] = float(co.z)
        return coords


def build_mesh_blend_shapes(mesh, exported_vertex_source_indices) -> list[dict]:
    shape_keys = getattr(mesh, "shape_keys", None)
    key_blocks = list(getattr(shape_keys, "key_blocks", []) or []) if shape_keys is not None else []
    if len(key_blocks) <= 1:
        return []
    basis = key_blocks[0]
    basis_data = getattr(basis, "data", None)
    if basis_data is None:
        return []

    vertex_count = len(getattr(mesh, "vertices", []) or [])
    source_indices = [int(i) for i in (exported_vertex_source_indices or [])]
    if vertex_count <= 0 or not source_indices:
        return []

    basis_coords = read_shape_key_coords_fast(basis, vertex_count)
    blend_shapes: list[dict] = []
    for key in key_blocks[1:]:
        key_data = getattr(key, "data", None)
        if key_data is None:
            continue
        key_coords = read_shape_key_coords_fast(key, vertex_count)
        delta_positions = array("f")
        non_zero = False
        for source_index in source_indices:
            j = source_index * 3
            if j < 0 or j + 2 >= len(basis_coords) or j + 2 >= len(key_coords):
                dx = dy = dz = 0.0
            else:
                dx = float(key_coords[j] - basis_coords[j])
                dy = float(key_coords[j + 1] - basis_coords[j + 1])
                dz = float(key_coords[j + 2] - basis_coords[j + 2])
            if abs(dx) > 1e-8 or abs(dy) > 1e-8 or abs(dz) > 1e-8:
                non_zero = True
            delta_positions.extend((dx, dy, dz))
        if not delta_positions or not non_zero:
            continue
        blend_shapes.append(
            {
                "name": str(getattr(key, "name", "ShapeKey") or "ShapeKey"),
                "frameWeight": 100.0,
                "value": float(getattr(key, "value", 0.0) or 0.0),
                "sliderMin": float(getattr(key, "slider_min", 0.0) or 0.0),
                "sliderMax": float(getattr(key, "slider_max", 1.0) or 1.0),
                "deltaPositions": delta_positions,
            }
        )
    return blend_shapes
