from __future__ import annotations

import hashlib
import json
import re
import tempfile
import time
from pathlib import Path
from typing import Any

from blender.common.log import trace
from blender.identity import ensure_unique_asset_id
from .extractor import find_principled

try:
    import bpy  # type: ignore
except ImportError:
    bpy = None

SCHEMA = "material_content_v1"
MESSAGE_TYPE = "scene_sync.material_content_v1"
SHADER_POLICY = "blendersync_principled_lit_urp_v1"
URP_LIT_SHADER = "Universal Render Pipeline/Lit"
BLENDERSYNC_LIT_SHADER = "TriSync/Principled Lit URP"
_TEXTURE_TEMP_TTL_SECONDS = 24 * 60 * 60
_TEXTURE_TEMP_CLEANUP_INTERVAL_SECONDS = 60 * 60
_last_texture_temp_cleanup_at = 0.0


def _ensure_material_asset_id(material) -> str:
    materials = getattr(getattr(bpy, "data", None), "materials", None) if bpy is not None else None
    return ensure_unique_asset_id(material, materials, label="material")


def get_material_content_v1_debug_path() -> Path:
    return Path(tempfile.gettempdir()) / "BlenderSync" / "last_material_content_v1.json"


def _unity_texture_export_root() -> Path | None:
    try:
        from blender.ui.state_view import get_session

        session = get_session()
        root = session.get_texture_export_root() or session.get_assets_import_root()
    except Exception:
        root = None
    if not root:
        return None
    try:
        return Path(root)
    except Exception:
        return None


def _texture_export_dir(usage: str) -> tuple[Path, str]:
    root = _unity_texture_export_root()
    safe_usage = _safe_file_stem(usage, "default")
    if root is not None:
        return root / safe_usage, "unity_project_staging"
    temp_root = Path(tempfile.gettempdir()) / "BlenderSyncVNext" / "material_textures_v1"
    _cleanup_stale_texture_temp_cache(temp_root)
    return temp_root / safe_usage, "temp_cache"


def _cleanup_stale_texture_temp_cache(root: Path) -> None:
    global _last_texture_temp_cleanup_at
    now = time.time()
    if (now - _last_texture_temp_cleanup_at) < _TEXTURE_TEMP_CLEANUP_INTERVAL_SECONDS:
        return
    _last_texture_temp_cleanup_at = now
    if not root.exists():
        return
    cutoff = now - _TEXTURE_TEMP_TTL_SECONDS
    removed = 0
    try:
        for path in root.rglob("*"):
            try:
                if path.is_file() and path.stat().st_mtime < cutoff:
                    path.unlink()
                    removed += 1
            except Exception:
                continue
    except Exception:
        return
    if removed:
        trace(
            "MaterialContent",
            "stale_textures_removed",
            lambda: "Removed stale temporary material textures.",
            lambda: {"removedCount": removed},
        )


def _socket_default(socket, fallback: Any) -> Any:
    if socket is None:
        return fallback
    try:
        value = getattr(socket, "default_value")
        if hasattr(value, "__len__") and not isinstance(value, (str, bytes)):
            return [float(v) for v in value]
        return float(value)
    except Exception:
        return fallback


def _float(value: Any, fallback: float = 0.0) -> float:
    try:
        return float(value)
    except Exception:
        return fallback


def _rgba(value: Any, fallback: list[float] | None = None) -> list[float]:
    fallback = fallback or [0.8, 0.8, 0.8, 1.0]
    try:
        items = list(value)
        if len(items) >= 4:
            return [float(items[0]), float(items[1]), float(items[2]), float(items[3])]
        if len(items) == 3:
            return [float(items[0]), float(items[1]), float(items[2]), 1.0]
    except Exception:
        pass
    return list(fallback)


def _rgb(value: Any, fallback: list[float] | None = None) -> list[float]:
    rgba = _rgba(value, (fallback or [0.0, 0.0, 0.0]) + [1.0] if fallback and len(fallback) == 3 else [0.0, 0.0, 0.0, 1.0])
    return rgba[:3]


def _get_input(node, *names: str):
    if node is None:
        return None
    inputs = getattr(node, "inputs", None)
    if inputs is None:
        return None
    aliases = {
        "Base Color": ["Base Color", "Color", "颜色", "基础色", "基础颜色"],
        "Color": ["Color", "颜色"],
        "Metallic": ["Metallic", "金属度"],
        "Roughness": ["Roughness", "粗糙度"],
        "Alpha": ["Alpha", "透明度"],
        "Emission Color": ["Emission Color", "Emission", "自发光颜色", "发射颜色"],
        "Emission": ["Emission", "Emission Color", "自发光", "发射"],
        "Normal": ["Normal", "法向", "法线"],
        "Height": ["Height", "高度"],
        "Strength": ["Strength", "强度"],
        "Distance": ["Distance", "距离"],
    }
    expanded_names = []
    for name in names:
        expanded_names.extend(aliases.get(name, [name]))
    for name in expanded_names:
        try:
            socket = inputs.get(name)
        except Exception:
            socket = None
        if socket is not None:
            return socket
    wanted = {_normalize_key(name) for name in expanded_names}
    for socket in _iter_node_inputs(node):
        for value in (getattr(socket, "identifier", ""), getattr(socket, "name", "")):
            if _normalize_key(value) in wanted:
                return socket
    return None


def _normalize_key(value: Any) -> str:
    return re.sub(r"[\s_\-]+", "", str(value or "")).casefold()


def _node_type_keys(node) -> set[str]:
    return {
        _normalize_key(getattr(node, "type", "")),
        _normalize_key(getattr(node, "bl_idname", "")),
        _normalize_key(type(node).__name__),
    }


def _is_node_type(node, *expected: str) -> bool:
    keys = _node_type_keys(node)
    aliases = {
        "BUMP": ["BUMP", "ShaderNodeBump"],
        "NORMAL_MAP": ["NORMAL_MAP", "ShaderNodeNormalMap"],
        "TEX_IMAGE": ["TEX_IMAGE", "ShaderNodeTexImage"],
    }
    expanded = []
    for item in expected:
        expanded.extend(aliases.get(str(item or ""), [item]))
    expected_keys = {_normalize_key(item) for item in expanded}
    return bool(keys.intersection(expected_keys))


def _node_label(node) -> str:
    if node is None:
        return "<none>"
    return str(getattr(node, "type", "") or getattr(node, "bl_idname", "") or type(node).__name__ or "<unknown>")


def _same_node(left, right) -> bool:
    if left is None or right is None:
        return left is right
    if left is right:
        return True
    for node in (left, right):
        if not hasattr(node, "as_pointer"):
            return False
    try:
        return int(left.as_pointer()) == int(right.as_pointer())
    except Exception:
        return False


def _surface_shader_kind(node) -> str | None:
    if node is None:
        return None
    if _is_node_type(node, "BSDF_DIFFUSE", "ShaderNodeBsdfDiffuse"):
        return "diffuse"
    if _is_node_type(node, "EMISSION", "ShaderNodeEmission"):
        return "emission"
    return None


def _material_output_surface_source(material):
    if material is None or not getattr(material, "use_nodes", False) or not getattr(material, "node_tree", None):
        return None
    outputs = [
        node for node in list(getattr(material.node_tree, "nodes", []) or [])
        if _is_node_type(node, "OUTPUT_MATERIAL", "ShaderNodeOutputMaterial")
    ]
    active_outputs = [node for node in outputs if bool(getattr(node, "is_active_output", False))]
    output = active_outputs[0] if active_outputs else (outputs[0] if outputs else None)
    if output is None:
        return None
    surface = _get_input(output, "Surface")
    if surface is None or not getattr(surface, "is_linked", False):
        return None
    try:
        links = list(getattr(surface, "links", []) or [])
    except Exception:
        links = []
    return getattr(links[0], "from_node", None) if links else None


def _append_shader_graph_warnings(material, principled, warnings: list[dict]) -> None:
    if material is None:
        return
    if not getattr(material, "use_nodes", False):
        warnings.append({
            "code": "material_nodes_disabled_lit_fallback",
            "message": "Blender material does not use nodes; Unity will create a TriSync Principled Lit material with fallback PBR values.",
        })
        return

    surface_node = _material_output_surface_source(material)
    if principled is None:
        kind = _surface_shader_kind(surface_node)
        if kind in {"diffuse", "emission"}:
            warnings.append({
                "code": f"{kind}_bsdf_lit_fallback",
                "message": f"Material Output Surface uses '{_node_label(surface_node)}'. TriSync will create a TriSync Principled Lit material and map the supported {kind} color/texture parameters best-effort.",
            })
            return
        warnings.append({
            "code": "unsupported_shader_graph_lit_fallback",
            "message": f"No supported Principled BSDF path was found; Unity will create a TriSync Principled Lit material with fallback PBR values. Surface source={_node_label(surface_node)}.",
        })
        return

    if surface_node is not None and not _same_node(surface_node, principled):
        warnings.append({
            "code": "complex_shader_graph_best_effort",
            "message": f"Material Output Surface is driven by '{_node_label(surface_node)}'. TriSync will use TriSync Principled Lit and extract the supported Principled/PBR subset only.",
        })


def _image_source_path(image) -> str:
    if image is None:
        return ""
    try:
        raw = image.filepath_from_user() or getattr(image, "filepath", "") or ""
    except Exception:
        raw = getattr(image, "filepath", "") or ""
    if not raw:
        return ""
    try:
        return bpy.path.abspath(raw) if bpy is not None else raw
    except Exception:
        return raw


def _path_exists(path: str) -> bool:
    if not path:
        return False
    try:
        return Path(path).exists()
    except Exception:
        return False


def _texture_ref_for_image(image) -> str:
    images = getattr(getattr(bpy, "data", None), "images", None) if bpy is not None else None
    stable_asset_id = ensure_unique_asset_id(image, images, label="texture")
    return f"tex-{stable_asset_id}"


def _safe_file_stem(value: Any, fallback: str = "texture") -> str:
    text = str(value or "").strip() or fallback
    text = re.sub(r"[^A-Za-z0-9_.-]+", "_", text).strip("._")
    return text or fallback


def _clean_image_file_stem(image_name: Any, ext: str) -> str:
    text = str(image_name or "").strip() or "texture"
    text = re.sub(r"\.\d{3,}$", "", text)
    known_suffixes = {".png", ".jpg", ".jpeg", ".tga", ".tif", ".tiff", ".bmp", ".exr", ".hdr"}
    suffix = Path(text).suffix.lower()
    if suffix in known_suffixes:
        text = text[: -len(suffix)]
    normalized_ext = (ext or "").lower()
    if normalized_ext and text.lower().endswith(normalized_ext):
        text = text[: -len(normalized_ext)]
    return _safe_file_stem(text, "texture")


def _short_texture_ref(texture_ref: str) -> str:
    text = str(texture_ref or "").strip()
    if text.startswith("tex-"):
        text = text[4:]
    text = re.sub(r"[^A-Za-z0-9]+", "", text)
    return (text[:8] or "texture").lower()


def _generated_texture_file_name(image_name: Any, texture_ref: str, ext: str) -> str:
    return f"{_clean_image_file_stem(image_name, ext)}__{_short_texture_ref(texture_ref)}{ext}"


def _image_has_packed_data(image) -> bool:
    if image is None:
        return False
    try:
        if getattr(image, "packed_file", None) is not None:
            return True
    except Exception:
        pass
    try:
        for tile in list(getattr(image, "tiles", []) or []):
            if getattr(tile, "packed_file", None) is not None:
                return True
    except Exception:
        pass
    return False


def _image_has_buffer_data(image) -> bool:
    if image is None:
        return False
    try:
        if bool(getattr(image, "has_data", False)):
            return True
    except Exception:
        pass
    try:
        pixels = getattr(image, "pixels", None)
        return pixels is not None and len(pixels) > 0
    except Exception:
        return False


def _image_export_extension(image) -> str:
    raw_path = ""
    try:
        raw_path = str(getattr(image, "filepath", "") or "")
    except Exception:
        raw_path = ""
    suffix = Path(raw_path).suffix.lower()
    if suffix in {".png", ".jpg", ".jpeg", ".tga", ".tif", ".tiff", ".bmp", ".exr", ".hdr"}:
        return ".jpg" if suffix == ".jpeg" else suffix
    return ".png"


def _save_image_copy(image, output_path: Path) -> None:
    save = getattr(image, "save", None)
    if callable(save):
        try:
            save(filepath=str(output_path), save_copy=True)
            return
        except TypeError:
            save(filepath=str(output_path))
            return
        except Exception:
            pass

    save_render = getattr(image, "save_render", None)
    if callable(save_render):
        save_render(str(output_path))
        return

    raise RuntimeError("Blender image has no supported save method")


def _export_recoverable_image(image, *, slot: str, usage: str, texture_ref: str, image_name: str, warnings: list[dict]) -> tuple[str, str] | None:
    if image is None:
        return None
    source_kind = "packed" if _image_has_packed_data(image) else ("buffer" if _image_has_buffer_data(image) else "")
    if not source_kind:
        return None

    ext = _image_export_extension(image)
    file_name = _generated_texture_file_name(image_name, texture_ref, ext)
    output_dir, export_root_kind = _texture_export_dir(usage)
    output_path = output_dir / file_name
    output_path.parent.mkdir(parents=True, exist_ok=True)

    try:
        _save_image_copy(image, output_path)
    except Exception as exc:
        warnings.append({
            "code": "image_buffer_export_failed",
            "slot": slot,
            "imageName": image_name,
            "message": f"Image '{image_name}' is {source_kind} but could not be exported for Unity import: {exc}",
        })
        return None

    if not output_path.exists():
        warnings.append({
            "code": "image_buffer_export_missing",
            "slot": slot,
            "imageName": image_name,
            "message": f"Image '{image_name}' export did not produce a readable file.",
        })
        return None

    warnings.append({
        "code": f"image_{source_kind}_exported",
        "slot": slot,
        "imageName": image_name,
        "sourcePath": str(output_path),
        "exportRoot": export_root_kind,
        "message": f"Image '{image_name}' was exported from Blender {source_kind} data for Unity import ({export_root_kind}).",
    })
    return str(output_path), source_kind



def _normalize_texture_channel(channel: Any) -> str | None:
    text = str(channel or "").strip().upper()
    if text in {"R", "RED"}:
        return "R"
    if text in {"G", "GREEN"}:
        return "G"
    if text in {"B", "BLUE"}:
        return "B"
    if text in {"A", "ALPHA"}:
        return "A"
    return None


def _link_output_channel(link) -> str | None:
    socket = getattr(link, "from_socket", None)
    names = [
        getattr(socket, "identifier", ""),
        getattr(socket, "name", ""),
    ]
    for name in names:
        channel = _normalize_texture_channel(name)
        if channel is not None:
            return channel
    return None


def _normalize_texture_extension(value: Any) -> str | None:
    text = str(value or "").strip().upper()
    return text if text in {"REPEAT", "EXTEND", "CLIP", "MIRROR"} else None


def _normalize_texture_interpolation(value: Any) -> str | None:
    text = str(value or "").strip().upper()
    return text if text in {"LINEAR", "CLOSEST", "CUBIC", "SMART"} else None


def _normalize_texture_projection(value: Any) -> str | None:
    text = str(value or "").strip().upper()
    return text if text in {"FLAT", "BOX", "SPHERE", "TUBE"} else None


def _image_color_space_name(image) -> str:
    color_settings = getattr(image, "colorspace_settings", None)
    return str(getattr(color_settings, "name", "") or "")


def _warn_color_space_mismatch(entry: dict, usage: str, slot: str, warnings: list[dict]) -> None:
    blender_color_space = str(entry.get("blenderColorSpace") or "")
    if not blender_color_space:
        return

    normalized = blender_color_space.replace("_", "-").casefold()
    expects_srgb = usage in {"baseColor", "emission"}
    mismatch = (expects_srgb and "non-color" in normalized) or (not expects_srgb and "srgb" in normalized)
    if not mismatch:
        return

    warnings.append({
        "code": "texture_color_space_mismatch",
        "slot": slot,
        "imageName": entry.get("imageName"),
        "message": f"Blender Image Texture color space '{blender_color_space}' does not match usage '{usage}'. Unity import will use '{entry.get('colorSpace')}'.",
    })


def _append_texture_node_metadata(entry: dict, image_node, image, slot: str, warnings: list[dict]) -> None:
    blender_color_space = _image_color_space_name(image)
    if blender_color_space:
        entry["blenderColorSpace"] = blender_color_space

    if image_node is None:
        return

    extension = _normalize_texture_extension(getattr(image_node, "extension", None))
    if extension is not None:
        entry["extension"] = extension
        if extension == "CLIP":
            warnings.append({
                "code": "texture_extension_clip_approximate",
                "slot": slot,
                "imageName": entry.get("imageName"),
                "message": "Blender Image Texture extension 'Clip' has no TextureImporter equivalent; Unity will use Clamp until shader-side UV clipping is supported.",
            })

    interpolation = _normalize_texture_interpolation(getattr(image_node, "interpolation", None))
    if interpolation is not None:
        entry["interpolation"] = interpolation
        if interpolation in {"CUBIC", "SMART"}:
            warnings.append({
                "code": "texture_interpolation_approximate",
                "slot": slot,
                "imageName": entry.get("imageName"),
                "message": f"Blender Image Texture interpolation '{interpolation}' has no exact TextureImporter equivalent; Unity will use Bilinear.",
            })

    projection = _normalize_texture_projection(getattr(image_node, "projection", None))
    if projection is not None:
        entry["projection"] = projection
        if projection != "FLAT":
            warnings.append({
                "code": "texture_projection_unsupported",
                "slot": slot,
                "imageName": entry.get("imageName"),
                "message": f"Blender Image Texture projection '{projection}' is not reproduced by the current Unity shader; texture is still imported with UV sampling.",
            })


def _build_texture_entry(slot: str, usage: str, image, warnings: list[dict], *, source_channel: str | None = None, image_node=None) -> dict | None:
    if image is None:
        return None

    image_name = getattr(image, "name", None) or slot
    texture_ref = _texture_ref_for_image(image)
    source_path = _image_source_path(image)
    source_kind = "external"
    if source_path and not _path_exists(source_path):
        recovered = _export_recoverable_image(image, slot=slot, usage=usage, texture_ref=texture_ref, image_name=image_name, warnings=warnings)
        if recovered is not None:
            source_path, source_kind = recovered
        else:
            warnings.append({
                "code": "image_file_missing",
                "slot": slot,
                "imageName": image_name,
                "message": f"Image '{image_name}' points to '{source_path}', but the file is not readable on this machine.",
            })
            return None
    elif not source_path:
        recovered = _export_recoverable_image(image, slot=slot, usage=usage, texture_ref=texture_ref, image_name=image_name, warnings=warnings)
        if recovered is not None:
            source_path, source_kind = recovered
        else:
            missing_reason = "no file path and no packed/buffer data"
            if _image_has_packed_data(image):
                missing_reason = "packed image export failed"
            elif _image_has_buffer_data(image):
                missing_reason = "buffer image export failed"
            warnings.append({
                "code": "image_without_filepath",
                "slot": slot,
                "imageName": image_name,
                "message": f"Image '{image_name}' has no file path ({missing_reason}).",
            })
            return None

    color_space = "linear" if usage in {"normal", "metallic", "roughness", "occlusion", "height", "alpha"} else "sRGB"
    entry = {
        "textureRef": texture_ref,
        "sourcePath": source_path,
        "imageName": image_name,
        "fileName": Path(source_path).name,
        "colorSpace": color_space,
        "usage": usage,
        "sourceKind": source_kind,
    }
    normalized_channel = _normalize_texture_channel(source_channel)
    if normalized_channel is not None:
        entry["sourceChannel"] = normalized_channel
    _append_texture_node_metadata(entry, image_node, image, slot, warnings)
    _warn_color_space_mismatch(entry, usage, slot, warnings)
    return entry


def _iter_node_inputs(node):
    inputs = getattr(node, "inputs", None)
    if inputs is None:
        return []
    try:
        return list(inputs)
    except Exception:
        return []


def _is_multiply_node(node) -> bool:
    if node is None:
        return False
    node_type = str(getattr(node, "type", "") or "").upper()
    blend_type = str(getattr(node, "blend_type", "") or "").upper()
    operation = str(getattr(node, "operation", "") or "").upper()
    return (node_type in {"MIX", "MIX_RGB"} and blend_type == "MULTIPLY") or (node_type == "MATH" and operation == "MULTIPLY")


def _is_occlusion_labeled(node, image) -> bool:
    return _occlusion_label_score(node, image) > 0


def _occlusion_label_score(node, image) -> int:
    strong_labels = [
        getattr(node, "name", ""),
        getattr(node, "label", ""),
        getattr(image, "name", ""),
    ]
    weak_labels = [Path(str(getattr(image, "filepath", "") or "")).stem]
    text = " ".join(str(item or "") for item in strong_labels).lower()
    weak_text = " ".join(str(item or "") for item in weak_labels).lower()
    score = 0
    if "ambient occlusion" in text or "occlusion" in text:
        score = max(score, 4)
    tokens = [token for token in re.split(r"[^a-z0-9]+", text) if token]
    if "ao" in tokens:
        score = max(score, 3)
    if "ambient occlusion" in weak_text or "occlusion" in weak_text:
        score = max(score, 2)
    weak_tokens = [token for token in re.split(r"[^a-z0-9]+", weak_text) if token]
    if "ao" in weak_tokens:
        score = max(score, 1)
    return score


def _collect_linked_image_candidates(socket, visited=None, source_channel: str | None = None) -> list[tuple[Any, Any, str | None]]:
    if socket is None or not getattr(socket, "is_linked", False):
        return []
    if visited is None:
        visited = set()

    candidates: list[tuple[Any, Any, str | None]] = []
    for link in getattr(socket, "links", []) or []:
        node = getattr(link, "from_node", None)
        if node is None:
            continue
        link_channel = _link_output_channel(link) or source_channel
        node_id = id(node)
        if node_id in visited:
            continue
        visited.add(node_id)

        if _is_node_type(node, "TEX_IMAGE", "ShaderNodeTexImage"):
            image = getattr(node, "image", None)
            if image is not None:
                candidates.append((node, image, _normalize_texture_channel(link_channel)))
            continue

        for input_socket in _iter_node_inputs(node):
            candidates.extend(_collect_linked_image_candidates(input_socket, visited, link_channel))
    return candidates


def _find_socket_image_candidate(socket) -> tuple[Any | None, str | None, Any | None]:
    candidates = _collect_linked_image_candidates(socket)
    if not candidates:
        return None, None, None
    image_node, image, source_channel = candidates[0]
    return image, source_channel, image_node


def _find_socket_image_with_channel(principled, *socket_names: str) -> tuple[Any | None, str | None, Any | None]:
    socket = _get_input(principled, *socket_names)
    return _find_socket_image_candidate(socket)


def _extract_fallback_surface_values(surface_node, warnings: list[dict]) -> dict[str, Any]:
    kind = _surface_shader_kind(surface_node)
    if kind is None:
        return {}

    color_socket = _get_input(surface_node, "Color")
    color = _rgba(_socket_default(color_socket, [0.8, 0.8, 0.8, 1.0]))
    color_image, color_channel, color_node = _find_socket_image_candidate(color_socket)

    if kind == "diffuse":
        roughness = _float(_socket_default(_get_input(surface_node, "Roughness"), 0.5), 0.5)
        return {
            "kind": kind,
            "baseColor": color,
            "roughness": max(0.0, min(1.0, roughness)),
            "baseColorImage": color_image,
            "baseColorChannel": color_channel,
            "baseColorNode": color_node,
        }

    if kind == "emission":
        strength = _float(_socket_default(_get_input(surface_node, "Strength"), 1.0), 1.0)
        strength = max(0.0, strength)
        return {
            "kind": kind,
            "baseColor": color,
            "emissionColor": color[:3],
            "emissionStrength": strength,
            "emissionImage": color_image,
            "emissionChannel": color_channel,
            "emissionNode": color_node,
        }

    return {}


def _find_node_from_socket(socket, node_types: set[str], visited=None):
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

        if any(_is_node_type(node, node_type) for node_type in node_types):
            return node

        for input_socket in _iter_node_inputs(node):
            found = _find_node_from_socket(input_socket, node_types, visited)
            if found is not None:
                return found
    return None


def _find_height_image_with_channel(principled) -> tuple[Any | None, str | None, Any | None]:
    direct_image, direct_channel, direct_node = _find_socket_image_with_channel(principled, "Height")
    if direct_image is not None:
        return direct_image, direct_channel, direct_node

    bump_node = _find_node_from_socket(_get_input(principled, "Normal"), {"BUMP"})
    if bump_node is None:
        return None, None, None
    return _find_socket_image_candidate(_get_input(bump_node, "Height"))


def _height_map_metadata(principled) -> dict[str, Any]:
    bump_node = _find_node_from_socket(_get_input(principled, "Normal"), {"BUMP"})
    if bump_node is None:
        return {}

    strength = max(0.0, _float(_socket_default(_get_input(bump_node, "Strength"), 1.0), 1.0))
    distance = max(0.0, _float(_socket_default(_get_input(bump_node, "Distance"), 0.005), 0.005))
    return {
        "heightScale": max(0.005, min(0.08, strength * distance)),
    }


def _find_multiplied_occlusion_images(socket, warnings: list[dict], visited=None) -> tuple[Any | None, Any | None, Any | None, Any | None, str | None]:
    if socket is None or not getattr(socket, "is_linked", False):
        return None, None, None, None, None
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

        if _is_multiply_node(node):
            image_candidates: list[tuple[Any, Any, str | None]] = []
            for input_socket in _iter_node_inputs(node):
                image_candidates.extend(_collect_linked_image_candidates(input_socket, set(visited)))

            occlusion_candidates = sorted([
                (_occlusion_label_score(image_node, image), image_node, image, source_channel)
                for image_node, image, source_channel in image_candidates
                if _occlusion_label_score(image_node, image) > 0
            ], key=lambda item: item[0], reverse=True)
            if occlusion_candidates:
                occlusion_score = occlusion_candidates[0][0]
                occlusion_image = occlusion_candidates[0][2]
                occlusion_channel = occlusion_candidates[0][3]
                base_candidate = next(
                    ((image_node, image) for image_node, image, _ in image_candidates if image is not occlusion_image and _occlusion_label_score(image_node, image) < occlusion_score),
                    None,
                )
                base_node = base_candidate[0] if base_candidate is not None else None
                base_image = base_candidate[1] if base_candidate is not None else None
                occlusion_node = occlusion_candidates[0][1]
                return base_image, base_node, occlusion_image, occlusion_node, occlusion_channel

            if len({id(image) for _, image, _ in image_candidates}) >= 2:
                warnings.append({
                    "code": "ao_multiply_candidate_ambiguous",
                    "slot": "occlusion",
                    "message": "Multiply node feeding Base Color has multiple image inputs but no AO/occlusion-labeled texture; occlusion was not extracted.",
                })

        for input_socket in _iter_node_inputs(node):
            base_image, base_node, occlusion_image, occlusion_node, occlusion_channel = _find_multiplied_occlusion_images(input_socket, warnings, visited)
            if occlusion_image is not None:
                return base_image, base_node, occlusion_image, occlusion_node, occlusion_channel

    return None, None, None, None, None


def _find_base_color_and_occlusion_images(principled, warnings: list[dict]) -> tuple[Any | None, Any | None, Any | None, Any | None, str | None]:
    base_color_socket = _get_input(principled, "Base Color")
    base_color_image, _, base_color_node = _find_socket_image_candidate(base_color_socket)
    occlusion_image, occlusion_channel, occlusion_node = _find_socket_image_with_channel(principled, "Occlusion", "Ambient Occlusion", "AO")

    multiplied_base_image, multiplied_base_node, multiplied_occlusion_image, multiplied_occlusion_node, multiplied_occlusion_channel = _find_multiplied_occlusion_images(base_color_socket, warnings)
    if multiplied_base_image is not None:
        base_color_image = multiplied_base_image
        base_color_node = multiplied_base_node
    if occlusion_image is None:
        occlusion_image = multiplied_occlusion_image
        occlusion_node = multiplied_occlusion_node
        occlusion_channel = multiplied_occlusion_channel

    return base_color_image, base_color_node, occlusion_image, occlusion_node, occlusion_channel


def _find_normal_map_node_from_socket(socket, visited=None):
    if socket is None or not getattr(socket, "is_linked", False):
        return None
    if visited is None:
        visited = set()

    for link in getattr(socket, "links", []) or []:
        node = getattr(link, "from_node", None)
        if node is None:
            continue
        node_key = id(node)
        if node_key in visited:
            continue
        visited.add(node_key)

        if _is_node_type(node, "NORMAL_MAP", "ShaderNodeNormalMap"):
            return node

        priority_inputs = []
        if _is_node_type(node, "BUMP", "ShaderNodeBump"):
            priority_inputs.append(_get_input(node, "Normal"))
        priority_inputs.extend(_iter_node_inputs(node))

        seen_sockets = set()
        for input_socket in priority_inputs:
            if input_socket is None:
                continue
            socket_id = id(input_socket)
            if socket_id in seen_sockets:
                continue
            seen_sockets.add(socket_id)
            found = _find_normal_map_node_from_socket(input_socket, visited)
            if found is not None:
                return found
    return None


def _normal_map_metadata_from_node(normal_node, warnings: list[dict]) -> dict[str, Any]:
    if normal_node is None:
        return {}

    strength = _float(_socket_default(_get_input(normal_node, "Strength"), 1.0), 1.0)
    if strength < 0.0:
        strength = 0.0
    space = str(getattr(normal_node, "space", "") or "TANGENT").upper()
    if space not in {"TANGENT", "TANGENT_SPACE"}:
        warnings.append({
            "code": "normal_space_unsupported",
            "slot": "normal",
            "message": f"Normal Map space '{space}' is not supported in Phase 1; Unity URP Lit expects tangent-space normal maps.",
        })
    return {
        "normalStrength": strength,
        "normalSpace": space,
    }


def _find_normal_texture(principled, warnings: list[dict]) -> dict | None:
    normal_socket = _get_input(principled, "Normal")
    normal_node = _find_normal_map_node_from_socket(normal_socket)
    if normal_node is not None:
        normal_image, _, normal_image_node = _find_socket_image_candidate(_get_input(normal_node, "Color"))
        normal_texture = _build_texture_entry("normal", "normal", normal_image, warnings, image_node=normal_image_node)
        if normal_texture is not None:
            normal_texture.update(_normal_map_metadata_from_node(normal_node, warnings))
        return normal_texture

    direct_image = None
    direct_node = None
    if normal_socket is not None and getattr(normal_socket, "is_linked", False):
        for link in getattr(normal_socket, "links", []) or []:
            node = getattr(link, "from_node", None)
            if _is_node_type(node, "TEX_IMAGE", "ShaderNodeTexImage"):
                direct_image = getattr(node, "image", None)
                direct_node = node
                break
    return _build_texture_entry("normal", "normal", direct_image, warnings, image_node=direct_node)


def _infer_alpha_mode_hint(*, has_alpha_texture: bool, alpha: float) -> str:
    if has_alpha_texture:
        return "Cutout"
    if alpha < 0.999:
        return "Fade"
    return "Opaque"


def _alpha_cutoff(material) -> float:
    value = getattr(material, "alpha_threshold", 0.5) if material is not None else 0.5
    return max(0.0, min(1.0, _float(value, 0.5)))


def _compute_hash(value: Any) -> str:
    encoded = json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    return hashlib.sha1(encoded).hexdigest()


def build_material_content_v1(material) -> dict:
    """Build the Phase 1 canonical manual URP Lit material content payload.

    This function intentionally does not send anything. It is safe to use from
    debug/selfcheck code and is the stable input for later Unity apply work.
    """
    warnings: list[dict] = []

    if material is None:
        warnings.append({"code": "material_missing", "message": "No material was provided."})
        material_name = "Material"
        material_ref = ""
        principled = None
    else:
        material_name = getattr(material, "name", None) or "Material"
        material_ref = f"mat-{_ensure_material_asset_id(material)}"
        principled = find_principled(material)
    surface_node = _material_output_surface_source(material)
    _append_shader_graph_warnings(material, principled, warnings)

    if principled is None:
        warnings.append({
            "code": "principled_not_found",
            "message": "No Principled BSDF node found; using TriSync Principled Lit with safe fallback PBR values.",
        })

    fallback_surface = _extract_fallback_surface_values(surface_node, warnings) if principled is None else {}

    base_color = _rgba(_socket_default(_get_input(principled, "Base Color"), [0.8, 0.8, 0.8, 1.0]))
    if fallback_surface.get("baseColor") is not None:
        base_color = _rgba(fallback_surface.get("baseColor"))
    metallic = _float(_socket_default(_get_input(principled, "Metallic"), 0.0), 0.0)
    roughness = _float(_socket_default(_get_input(principled, "Roughness"), 0.5), 0.5)
    if fallback_surface.get("roughness") is not None:
        roughness = _float(fallback_surface.get("roughness"), roughness)
    roughness = max(0.0, min(1.0, roughness))
    smoothness = 1.0 - roughness

    alpha_socket = _get_input(principled, "Alpha")
    alpha = _float(_socket_default(alpha_socket, base_color[3]), base_color[3])
    base_color[3] = alpha

    emission_color_socket = _get_input(principled, "Emission Color", "Emission")
    emission_color = _rgb(_socket_default(emission_color_socket, [0.0, 0.0, 0.0, 1.0]))
    emission_strength = _float(_socket_default(_get_input(principled, "Emission Strength"), 0.0), 0.0)
    if fallback_surface.get("emissionColor") is not None:
        emission_color = _rgb(fallback_surface.get("emissionColor"))
    if fallback_surface.get("emissionStrength") is not None:
        emission_strength = _float(fallback_surface.get("emissionStrength"), emission_strength)

    normal_texture = _find_normal_texture(principled, warnings)

    base_color_image, base_color_node, occlusion_image, occlusion_node, occlusion_channel = _find_base_color_and_occlusion_images(principled, warnings)
    if fallback_surface.get("baseColorImage") is not None:
        base_color_image = fallback_surface.get("baseColorImage")
        base_color_node = fallback_surface.get("baseColorNode")
    metallic_image, metallic_channel, metallic_node = _find_socket_image_with_channel(principled, "Metallic")
    roughness_image, roughness_channel, roughness_node = _find_socket_image_with_channel(principled, "Roughness")
    height_image, height_channel, height_node = _find_height_image_with_channel(principled)
    height_texture = _build_texture_entry("height", "height", height_image, warnings, source_channel=height_channel or "G", image_node=height_node)
    if height_texture is not None:
        height_texture.update(_height_map_metadata(principled))
        if normal_texture is None and _find_node_from_socket(_get_input(principled, "Normal"), {"BUMP"}) is not None:
            warnings.append({
                "code": "bump_height_parallax_only",
                "slot": "height",
                "imageName": height_texture.get("imageName"),
                "message": "Blender Bump height was exported as a Unity parallax height map. This does not fully reproduce Blender's bump normal perturbation unless a normal map is also present.",
            })
    base_color_texture = _build_texture_entry("baseColor", "baseColor", base_color_image, warnings, image_node=base_color_node)
    alpha_image, _, alpha_node = _find_socket_image_with_channel(principled, "Alpha")
    alpha_texture = _build_texture_entry("alpha", "alpha", alpha_image, warnings, image_node=alpha_node)
    emission_image, _, emission_node = _find_socket_image_with_channel(principled, "Emission Color", "Emission")
    if fallback_surface.get("emissionImage") is not None:
        emission_image = fallback_surface.get("emissionImage")
        emission_node = fallback_surface.get("emissionNode")
    if alpha_texture is not None and base_color_texture is not None and alpha_texture.get("textureRef") != base_color_texture.get("textureRef"):
        warnings.append({
            "code": "separate_alpha_texture_not_combined",
            "slot": "alpha",
            "imageName": alpha_texture.get("imageName"),
            "message": "Separate Alpha texture detected. TriSync does not combine it into Unity BaseMap. Please provide a Base Color/BaseMap texture with an alpha channel.",
        })

    textures = {
        "baseColor": base_color_texture,
        "normal": normal_texture,
        "metallic": _build_texture_entry("metallic", "metallic", metallic_image, warnings, source_channel=metallic_channel or "R", image_node=metallic_node),
        "roughness": _build_texture_entry("roughness", "roughness", roughness_image, warnings, source_channel=roughness_channel or "R", image_node=roughness_node),
        "occlusion": _build_texture_entry("occlusion", "occlusion", occlusion_image, warnings, source_channel=occlusion_channel or "G", image_node=occlusion_node),
        "height": height_texture,
        "alpha": alpha_texture,
        "emission": _build_texture_entry("emission", "emission", emission_image, warnings, image_node=emission_node),
    }

    alpha_mode_hint = _infer_alpha_mode_hint(
        has_alpha_texture=textures.get("alpha") is not None,
        alpha=alpha,
    )

    texture_fingerprint_source = {
        key: value for key, value in textures.items() if value is not None
    }
    alpha_cutoff = _alpha_cutoff(material)

    property_fingerprint_source = {
        "baseColor": base_color,
        "metallic": metallic,
        "roughness": roughness,
        "smoothness": smoothness,
        "alpha": alpha,
        "alphaModeHint": alpha_mode_hint,
        "alphaCutoff": alpha_cutoff,
        "emissionColor": emission_color,
        "emissionStrength": emission_strength,
    }
    texture_dependency_hash = _compute_hash(texture_fingerprint_source)
    content_hash = _compute_hash({
        "schema": SCHEMA,
        "materialRef": material_ref,
        "shaderPolicy": SHADER_POLICY,
        "properties": property_fingerprint_source,
        "textures": texture_fingerprint_source,
        "warningCodes": [item.get("code") for item in warnings],
    })

    return {
        "type": MESSAGE_TYPE,
        "schema": SCHEMA,
        "materialRef": material_ref,
        "source": {
            "name": material_name,
            "blenderMaterialName": material_name,
        },
        "shader": {
            "policy": SHADER_POLICY,
            "target": BLENDERSYNC_LIT_SHADER,
            "fallback": URP_LIT_SHADER,
        },
        "properties": property_fingerprint_source,
        "textures": textures,
        "fingerprint": {
            "contentHash": content_hash,
            "textureDependencyHash": texture_dependency_hash,
        },
        "warnings": warnings,
    }


def write_material_content_v1_debug_json(material, path: str | Path | None = None) -> dict:
    payload = build_material_content_v1(material)
    output_path = Path(path) if path is not None else get_material_content_v1_debug_path()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    return payload
