from __future__ import annotations


from blender.identity import ensure_asset_id

try:
    import bpy  # type: ignore
except ImportError:
    bpy = None


BLENDERSYNC_LIT_SHADER = "TriSync/Principled Lit URP"


def _slug(value: str) -> str:
    safe = "".join(ch.lower() if ch.isalnum() else "-" for ch in (value or "unnamed"))
    while "--" in safe:
        safe = safe.replace("--", "-")
    return safe.strip("-") or "unnamed"


def _is_node_type(node, *types: str) -> bool:
    node_type = str(getattr(node, "type", "") or "").upper()
    bl_idname = str(getattr(node, "bl_idname", "") or "")
    return any(node_type == str(item or "").upper() or bl_idname == str(item or "") for item in types)


def _iter_node_inputs(node):
    inputs = getattr(node, "inputs", None)
    if inputs is None:
        return []
    try:
        return list(inputs)
    except Exception:
        return []


def _surface_input(output_node):
    inputs = getattr(output_node, "inputs", None)
    if inputs is None:
        return None
    try:
        surface = inputs.get("Surface")
        if surface is not None:
            return surface
    except Exception:
        pass
    for socket in _iter_node_inputs(output_node):
        if str(getattr(socket, "identifier", "") or getattr(socket, "name", "") or "").lower() == "surface":
            return socket
    return None


def _material_output_node(material):
    if material is None or not getattr(material, "use_nodes", False) or not getattr(material, "node_tree", None):
        return None
    outputs = [
        node for node in list(getattr(material.node_tree, "nodes", []) or [])
        if _is_node_type(node, "OUTPUT_MATERIAL", "ShaderNodeOutputMaterial")
    ]
    active_outputs = [node for node in outputs if bool(getattr(node, "is_active_output", False))]
    return active_outputs[0] if active_outputs else (outputs[0] if outputs else None)


def _find_principled_from_socket(socket, visited=None):
    if socket is None or not getattr(socket, "is_linked", False):
        return None
    if visited is None:
        visited = set()

    for link in getattr(socket, "links", []) or []:
        node = getattr(link, "from_node", None)
        if node is None:
            continue
        node_id = id(node)
        if node_id in visited:
            continue
        visited.add(node_id)

        if _is_node_type(node, "BSDF_PRINCIPLED", "ShaderNodeBsdfPrincipled"):
            return node

        for input_socket in _iter_node_inputs(node):
            found = _find_principled_from_socket(input_socket, visited)
            if found is not None:
                return found
    return None


def find_principled(material):
    if material is None or not getattr(material, "use_nodes", False) or not getattr(material, "node_tree", None):
        return None
    output = _material_output_node(material)
    principled = _find_principled_from_socket(_surface_input(output))
    if principled is not None:
        return principled
    for node in material.node_tree.nodes:
        if _is_node_type(node, "BSDF_PRINCIPLED", "ShaderNodeBsdfPrincipled"):
            return node
    return None


def _compute_has_emission(emission_map_ref: str, emission_color) -> bool:
    return bool(emission_map_ref) or any(abs(float(v)) > 1e-6 for v in (emission_color or [0.0, 0.0, 0.0, 0.0])[:3])


def _build_snapshot_supported_slots(*, has_principled: bool, base_map_ref: str, normal_map_ref: str, metallic_map_ref: str, height_map_ref: str, occlusion_map_ref: str, emission_map_ref: str, has_emission: bool, metallic_default: float) -> list[str]:
    supported_slots = []
    if base_map_ref or has_principled:
        supported_slots.append("baseColor")
    if normal_map_ref:
        supported_slots.append("normal")
    if metallic_map_ref or metallic_default > 0.0:
        supported_slots.append("metallic")
    if height_map_ref:
        supported_slots.append("height")
    if occlusion_map_ref:
        supported_slots.append("occlusion")
    if emission_map_ref or has_emission:
        supported_slots.append("emission")
    return supported_slots


def _infer_surface_path_kind(*, has_principled: bool, has_emission: bool, emission_map_ref: str) -> str:
    if not has_principled:
        return "unknown"
    if emission_map_ref or has_emission:
        return "principled_with_emission"
    return "direct_principled"


def _build_snapshot_warnings(*, has_principled: bool, occlusion_map_ref: str) -> list[str]:
    warnings = []
    if occlusion_map_ref:
        warnings.append("occlusion_is_best_effort")
    return warnings


def _infer_alpha_mode_hint(material) -> str:
    blend_method = (getattr(material, "blend_method", None) or "OPAQUE").upper() if material is not None else "OPAQUE"
    if blend_method == "BLEND":
        return "Fade"
    if blend_method == "HASHED":
        return "Transparent"
    if blend_method == "CLIP":
        return "Cutout"
    return "Opaque"


def _infer_double_sided_hint(material) -> bool:
    if material is None:
        return False
    return not bool(getattr(material, "use_backface_culling", False))


def build_material_target_snapshot(*, material, principled, base_map_ref: str, normal_map_ref: str, metallic_map_ref: str, height_map_ref: str, occlusion_map_ref: str, emission_map_ref: str, emission_color) -> dict:
    has_principled = principled is not None
    has_emission = _compute_has_emission(emission_map_ref, emission_color)

    if has_principled:
        try:
            metallic_default = float(principled.inputs["Metallic"].default_value)
        except Exception:
            metallic_default = 0.0
        try:
            roughness_default = float(principled.inputs["Roughness"].default_value)
        except Exception:
            roughness_default = 0.5
    else:
        metallic_default = 0.0
        roughness_default = 0.5

    supported_slots = _build_snapshot_supported_slots(
        has_principled=has_principled,
        base_map_ref=base_map_ref,
        normal_map_ref=normal_map_ref,
        metallic_map_ref=metallic_map_ref,
        height_map_ref=height_map_ref,
        occlusion_map_ref=occlusion_map_ref,
        emission_map_ref=emission_map_ref,
        has_emission=has_emission,
        metallic_default=metallic_default,
    )
    surface_path_kind = _infer_surface_path_kind(
        has_principled=has_principled,
        has_emission=has_emission,
        emission_map_ref=emission_map_ref,
    )
    warnings = _build_snapshot_warnings(
        has_principled=has_principled,
        occlusion_map_ref=occlusion_map_ref,
    )
    alpha_mode_hint = _infer_alpha_mode_hint(material)
    double_sided_hint = _infer_double_sided_hint(material)

    return {
        "shaderFamily": "principled" if has_principled else None,
        "surfacePathKind": surface_path_kind,
        "supportedSlots": supported_slots,
        "warnings": warnings,
        "alphaModeHint": alpha_mode_hint,
        "doubleSidedHint": double_sided_hint,
        "hasPrincipled": has_principled,
        "hasBaseMap": bool(base_map_ref),
        "hasNormalMap": bool(normal_map_ref),
        "hasMetallicMap": bool(metallic_map_ref),
        "hasHeightMap": bool(height_map_ref),
        "hasOcclusionMap": bool(occlusion_map_ref),
        "hasEmissionMap": bool(emission_map_ref),
        "hasEmission": has_emission,
        "metallicDefault": metallic_default,
        "roughnessDefault": roughness_default,
    }


def _build_reason_details(snapshot: dict) -> dict:
    return {
        "hasPrincipled": bool(snapshot.get("hasPrincipled")),
        "hasBaseMap": bool(snapshot.get("hasBaseMap")),
        "hasNormalMap": bool(snapshot.get("hasNormalMap")),
        "hasMetallicMap": bool(snapshot.get("hasMetallicMap")),
        "hasHeightMap": bool(snapshot.get("hasHeightMap")),
        "hasOcclusionMap": bool(snapshot.get("hasOcclusionMap")),
        "hasEmission": bool(snapshot.get("hasEmission")),
        "metallicDefault": float(snapshot.get("metallicDefault", 0.0)),
        "roughnessDefault": float(snapshot.get("roughnessDefault", 0.5)),
        "surfacePathKind": snapshot.get("surfacePathKind"),
    }


def select_target_material_decision(snapshot: dict) -> dict:
    reason_details = _build_reason_details(snapshot)

    if not snapshot.get("hasPrincipled"):
        return {
            "targetModel": "URP_LIT",
            "shaderName": BLENDERSYNC_LIT_SHADER,
            "confidence": "low",
            "reason": "no_principled_node_lit_fallback",
            "reasonDetails": reason_details,
        }

    has_lit_features = any([
        snapshot.get("hasNormalMap"),
        snapshot.get("hasMetallicMap"),
        snapshot.get("hasHeightMap"),
        snapshot.get("hasOcclusionMap"),
    ])
    if has_lit_features:
        return {
            "targetModel": "URP_LIT",
            "shaderName": BLENDERSYNC_LIT_SHADER,
            "confidence": "high",
            "reason": "lit_texture_features_present",
            "reasonDetails": reason_details,
        }

    has_nontrivial_pbr_defaults = float(snapshot.get("metallicDefault", 0.0)) > 0.0 or abs(float(snapshot.get("roughnessDefault", 0.5)) - 0.5) > 1e-6
    has_only_color_emission = bool(snapshot.get("hasBaseMap")) or bool(snapshot.get("hasEmission"))

    if has_nontrivial_pbr_defaults:
        return {
            "targetModel": "URP_LIT",
            "shaderName": BLENDERSYNC_LIT_SHADER,
            "confidence": "medium",
            "reason": "pbr_defaults_present",
            "reasonDetails": reason_details,
        }
    if has_only_color_emission:
        return {
            "targetModel": "URP_LIT",
            "shaderName": BLENDERSYNC_LIT_SHADER,
            "confidence": "medium",
            "reason": "color_or_emission_lit_fallback",
            "reasonDetails": reason_details,
        }
    return {
        "targetModel": "URP_LIT",
        "shaderName": BLENDERSYNC_LIT_SHADER,
        "confidence": "low",
        "reason": "fallback_lit_default",
        "reasonDetails": reason_details,
    }


def select_target_material_shader(*, material, principled, base_map_ref: str, normal_map_ref: str, metallic_map_ref: str, height_map_ref: str, occlusion_map_ref: str, emission_map_ref: str, emission_color) -> str:
    snapshot = build_material_target_snapshot(
        material=material,
        principled=principled,
        base_map_ref=base_map_ref,
        normal_map_ref=normal_map_ref,
        metallic_map_ref=metallic_map_ref,
        height_map_ref=height_map_ref,
        occlusion_map_ref=occlusion_map_ref,
        emission_map_ref=emission_map_ref,
        emission_color=emission_color,
    )
    decision = select_target_material_decision(snapshot)
    return decision["shaderName"]


def find_linked_image_from_socket(socket, visited=None):
    if socket is None or not getattr(socket, "is_linked", False):
        return None
    if visited is None:
        visited = set()

    for link in getattr(socket, "links", []) or []:
        node = getattr(link, "from_node", None)
        if node is None:
            continue
        node_id = id(node)
        if node_id in visited:
            continue
        visited.add(node_id)

        node_type = getattr(node, "type", "")
        if node_type == "TEX_IMAGE":
            image = getattr(node, "image", None)
            if image is not None:
                return image

        if node_type == "NORMAL_MAP":
            image = find_linked_image_from_socket(node.inputs.get("Color"), visited)
            if image is not None:
                return image

        if node_type == "BUMP":
            image = find_linked_image_from_socket(node.inputs.get("Height"), visited)
            if image is not None:
                return image

        for input_socket in getattr(node, "inputs", []) or []:
            image = find_linked_image_from_socket(input_socket, visited)
            if image is not None:
                return image
    return None



