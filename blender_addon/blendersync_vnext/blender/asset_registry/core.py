from __future__ import annotations

import json

try:
    import bpy  # type: ignore
except ImportError:
    bpy = None


REGISTRY_SCENE_KEY = "blendersync_known_asset_registry"
REGISTRY_SCHEMA_VERSION = 1
PAIR_REGISTRY_SCENE_KEY = "blendersync_pair_registry"
PAIR_REGISTRY_SCHEMA_VERSION = 1
ASSET_FINGERPRINT_REGISTRY_SCENE_KEY = "blendersync_asset_fingerprint_registry"
ASSET_FINGERPRINT_REGISTRY_SCHEMA_VERSION = 1

_ASSET_SOURCE_FINGERPRINT_RUNTIME: dict[str, str] = {}
_ASSET_SOURCE_FINGERPRINT_RUNTIME_LOADED = False
_ASSET_SOURCE_FINGERPRINT_RUNTIME_SCENE_KEY: str | None = None


def _get_scene():
    if bpy is None or bpy.context is None:
        return None
    return getattr(bpy.context, "scene", None)


def _scene_cache_key(scene) -> str | None:
    if scene is None:
        return None
    try:
        session_uid = int(getattr(scene, "session_uid", 0) or 0)
    except Exception:
        return None
    return f"session:{session_uid}" if session_uid > 0 else None


def reset_runtime_asset_registry_cache() -> None:
    global _ASSET_SOURCE_FINGERPRINT_RUNTIME_LOADED, _ASSET_SOURCE_FINGERPRINT_RUNTIME_SCENE_KEY
    _ASSET_SOURCE_FINGERPRINT_RUNTIME.clear()
    _ASSET_SOURCE_FINGERPRINT_RUNTIME_LOADED = False
    _ASSET_SOURCE_FINGERPRINT_RUNTIME_SCENE_KEY = None


def _default_registry_payload() -> dict:
    return {
        "schemaVersion": REGISTRY_SCHEMA_VERSION,
        "knownAssetIds": [],
    }


def _default_pair_registry_payload() -> dict:
    return {
        "schemaVersion": PAIR_REGISTRY_SCHEMA_VERSION,
        "pairs": [],
    }


def _default_asset_fingerprint_registry_payload() -> dict:
    return {
        "schemaVersion": ASSET_FINGERPRINT_REGISTRY_SCHEMA_VERSION,
        "fingerprints": {},
    }


def _normalize_asset_ids(values) -> list[str]:
    if not isinstance(values, list):
        return []

    seen: set[str] = set()
    out: list[str] = []
    for value in values:
        if not isinstance(value, str):
            continue
        asset_id = value.strip()
        if not asset_id or asset_id in seen:
            continue
        seen.add(asset_id)
        out.append(asset_id)
    out.sort()
    return out


def load_known_asset_registry() -> dict:
    scene = _get_scene()
    payload = _default_registry_payload()
    if scene is None:
        payload["knownAssetIds"] = set()
        return payload

    raw = scene.get(REGISTRY_SCENE_KEY)
    if not raw:
        payload["knownAssetIds"] = set()
        return payload

    try:
        parsed = json.loads(str(raw))
    except Exception:
        payload["knownAssetIds"] = set()
        return payload

    if not isinstance(parsed, dict):
        payload["knownAssetIds"] = set()
        return payload

    schema_version = parsed.get("schemaVersion")
    if not isinstance(schema_version, int):
        schema_version = REGISTRY_SCHEMA_VERSION

    known_asset_ids = set(_normalize_asset_ids(parsed.get("knownAssetIds", [])))
    return {
        "schemaVersion": schema_version,
        "knownAssetIds": known_asset_ids,
    }


def save_known_asset_registry(payload: dict) -> None:
    scene = _get_scene()
    if scene is None:
        return

    schema_version = payload.get("schemaVersion")
    if not isinstance(schema_version, int):
        schema_version = REGISTRY_SCHEMA_VERSION

    known_asset_ids = payload.get("knownAssetIds") or []
    if isinstance(known_asset_ids, set):
        known_asset_ids = sorted(str(v).strip() for v in known_asset_ids if isinstance(v, str) and str(v).strip())
    else:
        known_asset_ids = _normalize_asset_ids(known_asset_ids)

    scene[REGISTRY_SCENE_KEY] = json.dumps(
        {
            "schemaVersion": schema_version,
            "knownAssetIds": known_asset_ids,
        },
        ensure_ascii=False,
        separators=(",", ":"),
    )


def is_asset_known(asset_id: str) -> bool:
    if not isinstance(asset_id, str) or not asset_id.strip():
        return False
    registry = load_known_asset_registry()
    return asset_id.strip() in (registry.get("knownAssetIds") or set())


def mark_assets_known(asset_ids: list[str]) -> None:
    registry = load_known_asset_registry()
    known = registry.get("knownAssetIds")
    if not isinstance(known, set):
        known = set(_normalize_asset_ids(list(known) if isinstance(known, list) else []))

    for value in asset_ids or []:
        if not isinstance(value, str):
            continue
        asset_id = value.strip()
        if not asset_id:
            continue
        known.add(asset_id)

    registry["knownAssetIds"] = known
    save_known_asset_registry(registry)


def remove_assets_known(asset_ids: list[str]) -> None:
    remove_set = {str(v).strip() for v in (asset_ids or []) if isinstance(v, str) and str(v).strip()}
    if not remove_set:
        return

    registry = load_known_asset_registry()
    known = registry.get("knownAssetIds")
    if not isinstance(known, set):
        known = set(_normalize_asset_ids(list(known) if isinstance(known, list) else []))
    known.difference_update(remove_set)
    registry["knownAssetIds"] = known
    save_known_asset_registry(registry)


def _get_root_asset_id(root: dict) -> str | None:
    if not isinstance(root, dict):
        return None
    metadata = root.get("metadata") or {}
    if not isinstance(metadata, dict):
        return None
    asset_id = metadata.get("assetId")
    if not isinstance(asset_id, str):
        return None
    asset_id = asset_id.strip()
    return asset_id or None


def filter_unknown_bootstrap_roots(selected_roots: list[dict]) -> tuple[list[dict], list[str]]:
    registry = load_known_asset_registry()
    known_asset_ids = registry.get("knownAssetIds") or set()

    kept: list[dict] = []
    suppressed: list[str] = []
    for root in selected_roots or []:
        asset_id = _get_root_asset_id(root)
        if asset_id and asset_id in known_asset_ids:
            suppressed.append(asset_id)
            continue
        kept.append(root)

    return kept, suppressed


def _collect_asset_ids_recursive(node: dict, out: set[str]) -> None:
    if not isinstance(node, dict):
        return

    asset_id = _get_root_asset_id(node)
    if asset_id:
        out.add(asset_id)

    deps = node.get("deps") or []
    if not isinstance(deps, list):
        return
    for dep in deps:
        _collect_asset_ids_recursive(dep, out)


def collect_asset_ids_from_selected_roots(selected_roots: list[dict]) -> list[str]:
    out: set[str] = set()
    for root in selected_roots or []:
        _collect_asset_ids_recursive(root, out)
    return sorted(out)


def _normalize_fingerprint_records(values) -> dict[str, str]:
    if not isinstance(values, dict):
        return {}
    out: dict[str, str] = {}
    for key, value in values.items():
        asset_id = str(key or "").strip()
        fingerprint = str(value or "").strip()
        if asset_id and fingerprint:
            out[asset_id] = fingerprint
    return out


def load_asset_fingerprint_registry() -> dict:
    scene = _get_scene()
    payload = _default_asset_fingerprint_registry_payload()
    if scene is None:
        return payload

    raw = scene.get(ASSET_FINGERPRINT_REGISTRY_SCENE_KEY)
    if not raw:
        return payload

    try:
        parsed = json.loads(str(raw))
    except Exception:
        return payload

    if not isinstance(parsed, dict):
        return payload

    schema_version = parsed.get("schemaVersion")
    if not isinstance(schema_version, int):
        schema_version = ASSET_FINGERPRINT_REGISTRY_SCHEMA_VERSION

    return {
        "schemaVersion": schema_version,
        "fingerprints": _normalize_fingerprint_records(parsed.get("fingerprints") or {}),
    }


def _ensure_asset_source_fingerprint_runtime_loaded() -> None:
    global _ASSET_SOURCE_FINGERPRINT_RUNTIME_LOADED, _ASSET_SOURCE_FINGERPRINT_RUNTIME_SCENE_KEY
    scene_key = _scene_cache_key(_get_scene())
    if (
        scene_key is not None
        and _ASSET_SOURCE_FINGERPRINT_RUNTIME_LOADED
        and _ASSET_SOURCE_FINGERPRINT_RUNTIME_SCENE_KEY == scene_key
    ):
        return
    _ASSET_SOURCE_FINGERPRINT_RUNTIME.clear()
    registry = load_asset_fingerprint_registry()
    _ASSET_SOURCE_FINGERPRINT_RUNTIME.update(_normalize_fingerprint_records(registry.get("fingerprints") or {}))
    _ASSET_SOURCE_FINGERPRINT_RUNTIME_LOADED = True
    _ASSET_SOURCE_FINGERPRINT_RUNTIME_SCENE_KEY = scene_key


def save_asset_fingerprint_registry(payload: dict) -> None:
    scene = _get_scene()
    if scene is None:
        return

    schema_version = payload.get("schemaVersion")
    if not isinstance(schema_version, int):
        schema_version = ASSET_FINGERPRINT_REGISTRY_SCHEMA_VERSION

    scene[ASSET_FINGERPRINT_REGISTRY_SCENE_KEY] = json.dumps(
        {
            "schemaVersion": schema_version,
            "fingerprints": _normalize_fingerprint_records(payload.get("fingerprints") or {}),
        },
        ensure_ascii=False,
        separators=(",", ":"),
    )


def get_asset_source_fingerprint(asset_id: str) -> str | None:
    asset_id = str(asset_id or "").strip()
    if not asset_id:
        return None
    _ensure_asset_source_fingerprint_runtime_loaded()
    value = _ASSET_SOURCE_FINGERPRINT_RUNTIME.get(asset_id)
    value = str(value or "").strip()
    return value or None


def mark_asset_source_fingerprints(records: dict[str, str]) -> None:
    normalized = _normalize_fingerprint_records(records)
    if not normalized:
        return
    _ensure_asset_source_fingerprint_runtime_loaded()
    _ASSET_SOURCE_FINGERPRINT_RUNTIME.update(normalized)
    registry = load_asset_fingerprint_registry()
    fingerprints = registry.get("fingerprints")
    if not isinstance(fingerprints, dict):
        fingerprints = {}
    fingerprints.update(_ASSET_SOURCE_FINGERPRINT_RUNTIME)
    registry["fingerprints"] = fingerprints
    save_asset_fingerprint_registry(registry)


def remove_asset_source_fingerprints(asset_ids: list[str]) -> None:
    remove_set = {str(v).strip() for v in (asset_ids or []) if isinstance(v, str) and str(v).strip()}
    if not remove_set:
        return
    _ensure_asset_source_fingerprint_runtime_loaded()
    for asset_id in remove_set:
        _ASSET_SOURCE_FINGERPRINT_RUNTIME.pop(asset_id, None)
    registry = load_asset_fingerprint_registry()
    fingerprints = registry.get("fingerprints")
    if not isinstance(fingerprints, dict):
        fingerprints = {}
    for asset_id in remove_set:
        fingerprints.pop(asset_id, None)
    registry["fingerprints"] = fingerprints
    save_asset_fingerprint_registry(registry)


def _normalize_pair_records(values) -> list[dict]:
    if not isinstance(values, list):
        return []

    out: list[dict] = []
    seen: set[str] = set()
    for value in values:
        if not isinstance(value, dict):
            continue
        pair_id = str(value.get("pairId") or "").strip()
        if not pair_id or pair_id in seen:
            continue
        seen.add(pair_id)

        object_id = value.get("objectId")
        object_id = str(object_id).strip() if isinstance(object_id, str) else None

        last_sent_at = value.get("lastSentAt")
        if not isinstance(last_sent_at, int):
            try:
                last_sent_at = int(last_sent_at)
            except Exception:
                last_sent_at = 0

        source_hint = value.get("sourceHint")
        source_hint = str(source_hint).strip() if isinstance(source_hint, str) else "manual_send"
        if not source_hint:
            source_hint = "manual_send"

        out.append(
            {
                "pairId": pair_id,
                "objectId": object_id,
                "lastSentAt": last_sent_at,
                "sourceHint": source_hint,
            }
        )

    return out


def load_pair_registry() -> dict:
    scene = _get_scene()
    payload = _default_pair_registry_payload()
    if scene is None:
        return payload

    raw = scene.get(PAIR_REGISTRY_SCENE_KEY)
    if not raw:
        return payload

    try:
        parsed = json.loads(str(raw))
    except Exception:
        return payload

    if not isinstance(parsed, dict):
        return payload

    schema_version = parsed.get("schemaVersion")
    if not isinstance(schema_version, int):
        schema_version = PAIR_REGISTRY_SCHEMA_VERSION

    pairs = _normalize_pair_records(parsed.get("pairs", []))
    return {
        "schemaVersion": schema_version,
        "pairs": pairs,
    }


def save_pair_registry(payload: dict) -> None:
    scene = _get_scene()
    if scene is None:
        return

    schema_version = payload.get("schemaVersion")
    if not isinstance(schema_version, int):
        schema_version = PAIR_REGISTRY_SCHEMA_VERSION

    pairs = _normalize_pair_records(payload.get("pairs") or [])

    scene[PAIR_REGISTRY_SCENE_KEY] = json.dumps(
        {
            "schemaVersion": schema_version,
            "pairs": pairs,
        },
        ensure_ascii=False,
        separators=(",", ":"),
    )


def upsert_pairs(records: list[dict]) -> None:
    registry = load_pair_registry()
    existing = registry.get("pairs") or []
    merged = {str(item.get("pairId") or "").strip(): item for item in _normalize_pair_records(existing)}

    for record in _normalize_pair_records(records or []):
        pair_id = record.get("pairId")
        if pair_id:
            merged[pair_id] = record

    registry["pairs"] = sorted(merged.values(), key=lambda x: x.get("pairId") or "")
    save_pair_registry(registry)


def remove_pairs(pair_ids: list[str]) -> None:
    remove_set = {str(v).strip() for v in (pair_ids or []) if isinstance(v, str) and str(v).strip()}
    if not remove_set:
        return

    registry = load_pair_registry()
    existing = _normalize_pair_records(registry.get("pairs") or [])
    registry["pairs"] = [item for item in existing if (item.get("pairId") or "") not in remove_set]
    save_pair_registry(registry)

