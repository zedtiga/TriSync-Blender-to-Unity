from __future__ import annotations

import hashlib
from array import array
import os
import platform
import sys
import time
from pathlib import Path
from typing import Any

from blender.common.log import warn

_NATIVE_MODULE = None
_NATIVE_IMPORT_ERROR: str | None = None
_NATIVE_IMPORT_ATTEMPTED = False
_ABI3_MINIMUM_CPYTHON_TAG = "cpython-311"
NATIVE_MULTI_UV_CAPABILITY = "multi_uv_v1"
_NATIVE_FALLBACK_WARNED_EVENTS: set[str] = set()


def _warn_native_fallback(event: str, exc: Exception) -> None:
    if event in _NATIVE_FALLBACK_WARNED_EVENTS:
        return
    _NATIVE_FALLBACK_WARNED_EVENTS.add(event)
    warn(
        "NativeMesh",
        event,
        "Native mesh processing failed; TriSync used the Python fallback.",
        {"exceptionType": type(exc).__name__, "reason": str(exc)},
    )


def _addon_root() -> Path:
    # .../blendersync_vnext/blender/native/mesh_extractor.py -> .../blendersync_vnext
    return Path(__file__).resolve().parents[2]


def _native_search_tags(
    *,
    implementation_name: str | None = None,
    cache_tag: str | None = None,
    version_info: tuple[int, int] | None = None,
) -> list[str]:
    name = implementation_name
    if name is None:
        name = str(getattr(sys.implementation, "name", "") or "")
    tag = cache_tag
    if tag is None:
        tag = str(getattr(sys.implementation, "cache_tag", "") or "unknown")
    version = version_info
    if version is None:
        version = (int(sys.version_info[0]), int(sys.version_info[1]))

    tags = [tag]
    if (
        name == "cpython"
        and version >= (3, 11)
        and _ABI3_MINIMUM_CPYTHON_TAG not in tags
    ):
        tags.append(_ABI3_MINIMUM_CPYTHON_TAG)
    return tags


def _native_platform_tag(
    *,
    platform_name: str | None = None,
    machine: str | None = None,
) -> str | None:
    system = str(sys.platform if platform_name is None else platform_name).strip().lower()
    architecture = str(platform.machine() if machine is None else machine).strip().lower().replace("-", "_")

    if system == "win32" and architecture in {"amd64", "x86_64"}:
        return "win_amd64"
    if system.startswith("linux") and architecture in {"amd64", "x86_64"}:
        return "linux_x86_64"
    if system == "darwin" and architecture in {"arm64", "aarch64"}:
        return "macos_arm64"
    if system == "darwin" and architecture in {"amd64", "x86_64"}:
        return "macos_x86_64"
    return None


def _native_search_dirs(
    *,
    addon_root: Path | None = None,
    platform_name: str | None = None,
    machine: str | None = None,
    implementation_name: str | None = None,
    cache_tag: str | None = None,
    version_info: tuple[int, int] | None = None,
) -> list[Path]:
    root = _addon_root() if addon_root is None else Path(addon_root)
    artifacts_root = root / "native" / "artifacts"
    platform_tag = _native_platform_tag(platform_name=platform_name, machine=machine)
    search_dirs = []
    if platform_tag is not None:
        platform_root = artifacts_root / platform_tag
        search_dirs.extend(
            platform_root / tag
            for tag in _native_search_tags(
                implementation_name=implementation_name,
                cache_tag=cache_tag,
                version_info=version_info,
            )
        )
        search_dirs.append(platform_root)
    search_dirs.append(artifacts_root)

    unique_dirs: list[Path] = []
    seen: set[str] = set()
    for path in search_dirs:
        key = os.path.normcase(str(path.resolve()))
        if key in seen:
            continue
        seen.add(key)
        unique_dirs.append(path)
    return unique_dirs


def _load_native_module():
    global _NATIVE_MODULE, _NATIVE_IMPORT_ERROR, _NATIVE_IMPORT_ATTEMPTED
    if _NATIVE_IMPORT_ATTEMPTED:
        return _NATIVE_MODULE
    _NATIVE_IMPORT_ATTEMPTED = True
    try:
        existing_search_dirs = [path for path in _native_search_dirs() if path.exists()]
        for path in reversed(existing_search_dirs):
            path_str = str(path)
            while path_str in sys.path:
                sys.path.remove(path_str)
            sys.path.insert(0, path_str)
        import blendersync_native  # type: ignore

        _NATIVE_MODULE = blendersync_native
        _NATIVE_IMPORT_ERROR = None
    except Exception as exc:
        _NATIVE_MODULE = None
        _NATIVE_IMPORT_ERROR = str(exc)
    return _NATIVE_MODULE


def get_native_status() -> dict[str, Any]:
    module = _load_native_module()
    version = None
    capabilities: list[str] = []
    if module is not None:
        try:
            version = module.version()
        except Exception:
            version = "unknown"
        try:
            capabilities = [str(value) for value in module.capabilities()]
        except Exception:
            capabilities = []
    return {
        "available": module is not None,
        "version": version,
        "capabilities": capabilities,
        "importError": _NATIVE_IMPORT_ERROR,
        "cacheTag": getattr(sys.implementation, "cache_tag", "unknown"),
        "platformTag": _native_platform_tag(),
        "searchDirs": [str(p) for p in _native_search_dirs()],
    }


def native_supports(capability: str, status: dict[str, Any] | None = None) -> bool:
    resolved = get_native_status() if status is None else status
    return bool(resolved.get("available")) and str(capability) in {
        str(value) for value in resolved.get("capabilities") or []
    }


def _sha1_array(values: list | tuple | array | None) -> str:
    if values is None:
        return ""
    if isinstance(values, array):
        return hashlib.sha1(values.tobytes()).hexdigest()
    typecode = "f"
    if values and all(isinstance(v, int) for v in values[: min(16, len(values))]):
        typecode = "i"
    arr = array(typecode)
    if values:
        arr.fromlist(values if isinstance(values, list) else list(values))
    return hashlib.sha1(arr.tobytes()).hexdigest()


def build_reference_hash_profile(vertices, normals, uv0, indices, source_indices=None) -> dict[str, Any]:
    t0 = time.perf_counter()
    profile = {
        "vertexSha1": _sha1_array(vertices),
        "normalSha1": _sha1_array(normals),
        "uv0Sha1": _sha1_array(uv0),
        "indexSha1": _sha1_array(indices),
        "sourceIndexSha1": _sha1_array(source_indices),
        "exportVertexCount": int((len(vertices) if vertices is not None else 0) // 3),
        "indexCount": int(len(indices) if indices is not None else 0),
    }
    profile["hashMs"] = round((time.perf_counter() - t0) * 1000.0, 3)
    return profile


def _native_raw_payload(raw: dict[str, Any]) -> dict[str, Any]:
    secondary_uv_layers = [
        channel.get("values").tobytes()
        for channel in list(raw.get("uv_layers") or [])[1:8]
        if isinstance(channel, dict) and channel.get("values") is not None
    ]
    return {
        "vertex_count": int(raw.get("vertex_count") or 0),
        "loop_count": int(raw.get("loop_count") or 0),
        "tri_count": int(raw.get("tri_count") or 0),
        "positions": raw.get("positions").tobytes() if raw.get("positions") is not None else None,
        "vertex_normals": raw.get("vertex_normals").tobytes() if raw.get("vertex_normals") is not None else None,
        "loop_vertex_indices": raw.get("loop_vertex_indices").tobytes() if raw.get("loop_vertex_indices") is not None else None,
        "loop_normals": raw.get("loop_normals").tobytes() if raw.get("loop_normals") is not None else None,
        "tri_loops": raw.get("tri_loops").tobytes() if raw.get("tri_loops") is not None else None,
        "uv0": raw.get("uv0").tobytes() if raw.get("uv0") is not None else None,
        "secondary_uv_layers": secondary_uv_layers,
        "have_loop_normals": bool(raw.get("have_loop_normals")),
        "quantize_scale": 1000000.0,
    }


def _array_from_bytes(typecode: str, value) -> array:
    arr = array(typecode)
    if value:
        arr.frombytes(bytes(value))
    return arr


def try_extract_mesh_binary_native(*, raw: dict[str, Any], output_dir: str, prefix: str) -> dict[str, Any] | None:
    module = _load_native_module()
    if module is None:
        return None
    if not hasattr(module, "extract_mesh_binary_v1"):
        return None
    try:
        result = module.extract_mesh_binary_v1(_native_raw_payload(raw), output_dir, prefix)
        if isinstance(result, dict) and result.get("ok") and result.get("sourceIndices") is not None:
            result["sourceIndices"] = _array_from_bytes("i", result.get("sourceIndices"))
            if result.get("sourceLoopIndices") is not None:
                result["sourceLoopIndices"] = _array_from_bytes("i", result.get("sourceLoopIndices"))
            hash_profile = result.get("hashProfile")
            if isinstance(hash_profile, dict):
                profile = dict(result.get("profile") or {})
                profile["hashProfile"] = hash_profile
                result["profile"] = profile
        return result if isinstance(result, dict) else {"ok": True, "result": result}
    except Exception as exc:
        _warn_native_fallback("binary_fallback", exc)
        return None


def try_gather_uv0_native(*, uv0: array, source_loop_indices: array, output_path: str | None = None, previous_hash: str | None = None) -> dict[str, Any] | None:
    module = _load_native_module()
    if module is None:
        return None
    if not hasattr(module, "gather_uv0_v1"):
        return None
    try:
        result = module.gather_uv0_v1(uv0.tobytes(), source_loop_indices.tobytes(), output_path, previous_hash)
        return result if isinstance(result, dict) else {"ok": True, "result": result}
    except Exception as exc:
        _warn_native_fallback("uv_fallback", exc)
        return None


def try_build_material_submeshes_native(*, indices: array, material_indices: array) -> dict[str, Any] | None:
    module = _load_native_module()
    if module is None:
        return None
    if not hasattr(module, "build_material_submeshes_v1"):
        return None
    try:
        result = module.build_material_submeshes_v1(indices.tobytes(), material_indices.tobytes())
        if isinstance(result, dict) and result.get("ok"):
            submeshes = []
            for submesh in result.get("subMeshes") or []:
                if not isinstance(submesh, dict):
                    continue
                slot_indices = _array_from_bytes("i", submesh.get("indices"))
                submeshes.append({
                    "materialSlot": int(submesh.get("materialSlot", 0) or 0),
                    "topology": str(submesh.get("topology") or "triangles"),
                    "indices": slot_indices,
                    "indexSha1": str(submesh.get("indexSha1") or ""),
                })
            result["subMeshes"] = submeshes
        return result if isinstance(result, dict) else {"ok": True, "result": result}
    except Exception as exc:
        _warn_native_fallback("submesh_fallback", exc)
        return None


def try_gather_positions_native(*, positions: array, source_indices: array, output_path: str | None = None, previous_hash: str | None = None) -> dict[str, Any] | None:
    module = _load_native_module()
    if module is None:
        return None
    if not hasattr(module, "gather_positions_v1"):
        return None
    try:
        result = module.gather_positions_v1(positions.tobytes(), source_indices.tobytes(), output_path, previous_hash)
        return result if isinstance(result, dict) else {"ok": True, "result": result}
    except Exception as exc:
        _warn_native_fallback("positions_fallback", exc)
        return None


def try_extract_mesh_arrays_native(*, raw: dict[str, Any], output_path: str | None = None, validate_reference: dict[str, Any] | None = None) -> dict[str, Any] | None:
    """Call the optional Rust/PyO3 native extractor when available.

    This is intentionally a narrow pure-buffer boundary. The native module must not
    touch bpy or Unity state; it receives raw foreach_get buffers and returns a
    profile/output buffers or writes binary_v1-compatible files in later phases.
    """
    module = _load_native_module()
    if module is None:
        return None
    if not hasattr(module, "extract_mesh_accurate_v1"):
        return None
    try:
        result = module.extract_mesh_accurate_v1(_native_raw_payload(raw), output_path)
        if isinstance(result, dict) and result.get("ok"):
            if result.get("vertices") is not None:
                result["vertices"] = _array_from_bytes("f", result.get("vertices"))
            if result.get("normals") is not None:
                result["normals"] = _array_from_bytes("f", result.get("normals"))
            if result.get("uv0") is not None:
                result["uv0"] = _array_from_bytes("f", result.get("uv0"))
            if result.get("indices") is not None:
                result["indices"] = _array_from_bytes("i", result.get("indices"))
            if result.get("sourceIndices") is not None:
                result["sourceIndices"] = _array_from_bytes("i", result.get("sourceIndices"))
            if result.get("sourceLoopIndices") is not None:
                result["sourceLoopIndices"] = _array_from_bytes("i", result.get("sourceLoopIndices"))
        if validate_reference and isinstance(result, dict):
            native_hash = result.get("hashProfile") or {}
            mismatch = []
            for key in ("vertexSha1", "normalSha1", "uv0Sha1", "indexSha1", "sourceIndexSha1"):
                if native_hash.get(key) and validate_reference.get(key) and native_hash.get(key) != validate_reference.get(key):
                    mismatch.append(key)
            if mismatch:
                result["validationOk"] = False
                result["validationMismatch"] = mismatch
            else:
                result["validationOk"] = True
        return result if isinstance(result, dict) else {"ok": True, "result": result}
    except Exception as exc:
        _warn_native_fallback("mesh_extract_fallback", exc)
        return None



def try_extract_skin_variable_influences_native(*, gather: dict[str, Any]) -> dict[str, Any] | None:
    module = _load_native_module()
    if module is None:
        return None
    if not hasattr(module, "extract_skin_variable_influences_v1"):
        return None
    try:
        group_map = gather.get("groupIndexToBoneIndex") or {}

        def i32_bytes(values):
            if isinstance(values, array) and values.typecode == "i":
                return values.tobytes()
            arr = array("i", [int(v) for v in (values or [])])
            return arr.tobytes()

        def f32_bytes(values):
            if isinstance(values, array) and values.typecode == "f":
                return values.tobytes()
            arr = array("f", [float(v) for v in (values or [])])
            return arr.tobytes()

        raw = {
            "neededSourceVertexIndices": i32_bytes(gather.get("neededSourceVertexIndices") or []),
            "sourceVertexOffsets": i32_bytes(gather.get("sourceVertexOffsets") or []),
            "sourceGroupIndices": i32_bytes(gather.get("sourceGroupIndices") or []),
            "sourceGroupWeights": f32_bytes(gather.get("sourceGroupWeights") or []),
            "groupToBoneKeys": i32_bytes(group_map.keys()),
            "groupToBoneValues": i32_bytes(group_map.values()),
            "exportedVertexSourceIndices": i32_bytes(gather.get("exportedVertexSourceIndices") or []),
            "fallbackBoneIndex": int(gather.get("fallbackBoneIndex", -1)),
        }
        fp_start = time.perf_counter()
        fp_hasher = hashlib.sha1()
        for key in (
            "neededSourceVertexIndices",
            "sourceVertexOffsets",
            "sourceGroupIndices",
            "sourceGroupWeights",
            "groupToBoneKeys",
            "groupToBoneValues",
            "exportedVertexSourceIndices",
        ):
            data = raw.get(key) or b""
            fp_hasher.update(key.encode("utf-8"))
            fp_hasher.update(b"|")
            fp_hasher.update(str(len(data)).encode("utf-8"))
            fp_hasher.update(b"|")
            fp_hasher.update(data)
            fp_hasher.update(b";")
        fp_hasher.update(b"fallbackBoneIndex=")
        fp_hasher.update(str(raw.get("fallbackBoneIndex")).encode("utf-8"))
        native_skin_source_fingerprint = fp_hasher.hexdigest()
        native_skin_fingerprint_ms = round((time.perf_counter() - fp_start) * 1000.0, 3)

        result = module.extract_skin_variable_influences_v1(raw)
        if isinstance(result, dict):
            result["sourceFingerprint"] = native_skin_source_fingerprint
            result["sourceFingerprintMs"] = native_skin_fingerprint_ms
            return result
        return {"ok": True, "result": result, "sourceFingerprint": native_skin_source_fingerprint, "sourceFingerprintMs": native_skin_fingerprint_ms}
    except Exception as exc:
        _warn_native_fallback("skin_extract_fallback", exc)
        return None
