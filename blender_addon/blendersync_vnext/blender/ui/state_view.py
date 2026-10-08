from __future__ import annotations

from collections import deque
import json
import re
import time

from blender.asset_registry import load_pair_registry, upsert_pairs
from blender.common.constants import DEFAULT_WS_PORT
from blender.common.log import clear as clear_log_entries, get_recent_entries
from blender.common.types import SendResult
from blender.session.core import BlenderSessionCore
from blender.session.ws_client_adapter import MinimalWsClientHook

try:
    import bpy  # type: ignore
except ImportError:
    bpy = None


def _blender_application_version() -> str | None:
    if bpy is None:
        return None
    try:
        return ".".join(str(part) for part in bpy.app.version)
    except Exception:
        return None


def normalize_ws_port(value) -> int:
    if isinstance(value, bool):
        return DEFAULT_WS_PORT
    try:
        port = int(value)
    except (TypeError, ValueError, OverflowError):
        return DEFAULT_WS_PORT
    return port if 1 <= port <= 65535 else DEFAULT_WS_PORT


def build_local_ws_endpoint(port=DEFAULT_WS_PORT) -> str:
    return f"ws://127.0.0.1:{normalize_ws_port(port)}/ws/"


_DEFAULT_WS_ENDPOINT = build_local_ws_endpoint()
_SESSION = BlenderSessionCore(application_version=_blender_application_version())
_LAST_SEND_RESULT: SendResult | None = None
_LAST_OPERATION_META: dict | None = None
_LAST_OPERATION_SEQUENCE = 0
_LAST_OPERATION_TIMESTAMP_UNIX = 0.0
_OPERATION_SEQUENCE = 0
_RECENT_OPERATION_REPORTS: deque[dict] = deque(maxlen=500)
_WS_RUNTIME: MinimalWsClientHook | None = None
_LAST_WS_HANDOFF_ERROR: str | None = None
AUTO_SYNC_READY_KEY = "blendersync_auto_sync_ready"
_DIAGNOSTICS_SCHEMA_VERSION = "blendersync-diagnostics-v2"
_MAX_DIAGNOSTIC_LOG_ENTRIES = 50
_DIAGNOSTICS_PATH_RE = re.compile(
    r"(?:\"(?:[A-Za-z]:[\\/]|\\\\|/(?:Users|home|tmp|var|private|mnt|opt)/)[^\"]*\"|"
    r"'(?:[A-Za-z]:[\\/]|\\\\|/(?:Users|home|tmp|var|private|mnt|opt)/)[^']*'|"
    r"(?:[A-Za-z]:[\\/]|\\\\|/(?:Users|home|tmp|var|private|mnt|opt)/)[^,;\r\n]+)"
)


def get_session() -> BlenderSessionCore:
    return _SESSION


def set_last_send_result(result: SendResult | None, meta: dict | None = None) -> None:
    global _LAST_SEND_RESULT, _LAST_OPERATION_META
    global _LAST_OPERATION_SEQUENCE, _LAST_OPERATION_TIMESTAMP_UNIX, _OPERATION_SEQUENCE
    _LAST_SEND_RESULT = result
    if meta is not None:
        _LAST_OPERATION_META = dict(meta)
    if result is None:
        _LAST_OPERATION_SEQUENCE = 0
        _LAST_OPERATION_TIMESTAMP_UNIX = 0.0
        return

    _OPERATION_SEQUENCE += 1
    _LAST_OPERATION_SEQUENCE = _OPERATION_SEQUENCE
    _LAST_OPERATION_TIMESTAMP_UNIX = time.time()
    _RECENT_OPERATION_REPORTS.append(get_asset_bridge_view_state())


def set_last_operation_meta(meta: dict | None) -> None:
    global _LAST_OPERATION_META
    _LAST_OPERATION_META = dict(meta or {}) if meta is not None else None


def latest_operation_sequence() -> int:
    return int(_RECENT_OPERATION_REPORTS[-1]["operation_sequence"]) if _RECENT_OPERATION_REPORTS else 0


def get_recent_operation_reports(limit: int | None = None) -> list[dict]:
    reports = list(_RECENT_OPERATION_REPORTS)
    if limit is not None:
        count = max(0, int(limit))
        reports = reports[-count:] if count else []
    return [
        {
            **report,
            "operation_meta": dict(report.get("operation_meta") or {}),
        }
        for report in reports
    ]


def clear_diagnostics_activity() -> None:
    global _LAST_SEND_RESULT, _LAST_OPERATION_META
    global _LAST_OPERATION_SEQUENCE, _LAST_OPERATION_TIMESTAMP_UNIX
    clear_log_entries()
    _RECENT_OPERATION_REPORTS.clear()
    _LAST_SEND_RESULT = None
    _LAST_OPERATION_META = None
    _LAST_OPERATION_SEQUENCE = 0
    _LAST_OPERATION_TIMESTAMP_UNIX = 0.0


def set_last_ws_handoff_error(error: str | None) -> None:
    global _LAST_WS_HANDOFF_ERROR
    _LAST_WS_HANDOFF_ERROR = error


def clear_session_connection_errors() -> None:
    get_session().set_last_error(None)
    set_last_ws_handoff_error(None)


def connect_minimal_ws_runtime(endpoint: str = _DEFAULT_WS_ENDPOINT) -> bool:
    global _WS_RUNTIME
    session = get_session()
    if _WS_RUNTIME is not None and _WS_RUNTIME.connected and _WS_RUNTIME.endpoint == endpoint:
        _WS_RUNTIME.install_outbound_handoff()
        session.set_endpoint(endpoint)
        clear_session_connection_errors()
        return True

    if _WS_RUNTIME is not None:
        try:
            _WS_RUNTIME.clear_outbound_handoff()
            _WS_RUNTIME.disconnect()
        finally:
            _WS_RUNTIME = None

    if not session.connect():
        return False
    set_last_ws_handoff_error(None)

    ws = MinimalWsClientHook(endpoint)
    if not ws.connect():
        session.disconnect()
        session.set_endpoint(endpoint)
        session.set_last_error(ws.last_error)
        set_last_ws_handoff_error(ws.last_error)
        return False

    ws.install_outbound_handoff()
    session.set_endpoint(endpoint)
    set_last_ws_handoff_error(None)
    _WS_RUNTIME = ws
    return True


def disconnect_minimal_ws_runtime() -> None:
    global _WS_RUNTIME
    if _WS_RUNTIME is not None:
        _WS_RUNTIME.clear_outbound_handoff()
        _WS_RUNTIME.disconnect()
        _WS_RUNTIME = None
    get_session().disconnect()
    set_last_ws_handoff_error(None)


def register_pair_ids(pair_records: list[dict], source_hint: str = "manual_send") -> None:
    now = int(time.time())
    normalized = []
    for record in pair_records or []:
        if not isinstance(record, dict):
            continue
        pair_id = str(record.get("pairId") or "").strip()
        if not pair_id:
            continue
        normalized.append(
            {
                "pairId": pair_id,
                "objectId": str(record.get("objectId") or "").strip() or None,
                "lastSentAt": int(record.get("lastSentAt") or now),
                "sourceHint": source_hint,
            }
        )
    if normalized:
        upsert_pairs(normalized)


def get_asset_bridge_view_state() -> dict:

    if _LAST_SEND_RESULT is None:
        return {
            "operation_sequence": 0,
            "operation_timestamp_unix": 0.0,
            "operation_timestamp_utc": None,
            "last_send_ok": None,
            "last_send_message": "not_sent_yet",
            "last_send_error": None,
            "last_payload_size": 0,
            "last_package_id": None,
            "last_resource_count": 0,
            "failure_category": None,
            "operation_meta": dict(_LAST_OPERATION_META or {}),
        }

    return {
        "operation_sequence": _LAST_OPERATION_SEQUENCE,
        "operation_timestamp_unix": _LAST_OPERATION_TIMESTAMP_UNIX,
        "operation_timestamp_utc": time.strftime(
            "%Y-%m-%dT%H:%M:%SZ",
            time.gmtime(_LAST_OPERATION_TIMESTAMP_UNIX),
        ),
        "last_send_ok": _LAST_SEND_RESULT.ok,
        "last_send_message": _LAST_SEND_RESULT.message,
        "last_send_error": _LAST_SEND_RESULT.error,
        "last_payload_size": _LAST_SEND_RESULT.payload_size,
        "last_package_id": _LAST_SEND_RESULT.last_package_id,
        "last_resource_count": _LAST_SEND_RESULT.last_resource_count,
        "failure_category": _LAST_SEND_RESULT.failure_category,
        "operation_meta": dict(_LAST_OPERATION_META or {}),
    }


def get_observability_snapshot() -> dict:
    return {
        "meta": {
            "schema_version": "smoke-v1",
        },
        "session": get_session_view_state(),
        "asset_bridge": get_asset_bridge_view_state(),
    }


def _redact_diagnostics_text(value) -> str | None:
    if value is None:
        return None
    return _DIAGNOSTICS_PATH_RE.sub("<path>", str(value))


def _blender_version_for_diagnostics() -> str:
    return _blender_application_version() or "unknown"


def _controller_diagnostics_for_copy() -> tuple[dict, dict]:
    if bpy is None:
        return {}, {}
    try:
        from blender.scene_sync.controller import get_controller_state

        raw = get_controller_state(include_active_object_baseline=True) or {}
    except Exception:
        return {}, {}

    runtime_keys = (
        "timer_registered",
        "sync_enabled",
        "object_sync_hz",
        "mesh_sync_hz",
        "view_sync_enabled",
        "view_sync_hz",
        "view_sync_scale",
        "current_mode",
        "lifecycle_reconcile_hz",
        "object_dirty_queue_count",
        "object_motion_burst_pair_count",
        "object_active_fallback_tracked_count",
    )
    runtime = {key: raw[key] for key in runtime_keys if key in raw}

    baseline = raw.get("active_object_baseline") or {}
    active = {}
    for key in (
        "hasActiveObject",
        "objectName",
        "objectType",
        "autoSyncReady",
        "meshSource",
        "modifierBaseline",
        "materialSlotsBaseline",
        "uvChannelsBaseline",
        "uvChannelCount",
        "colorAttributesBaseline",
        "colorAttributeCount",
    ):
        if key in baseline:
            value = baseline[key]
            active[key] = _redact_diagnostics_text(value) if isinstance(value, str) else value
    return runtime, active


def _recent_logs_for_copy() -> list[dict]:
    logs = []
    for entry in get_recent_entries(_MAX_DIAGNOSTIC_LOG_ENTRIES):
        fields = [
            {
                "key": _redact_diagnostics_text(key),
                "value": _redact_diagnostics_text(value),
            }
            for key, value in (entry.get("fields") or {}).items()
        ]
        logs.append(
            {
                "timestampUtc": str(entry.get("timestampUtc") or ""),
                "level": str(entry.get("level") or "INFO"),
                "category": _redact_diagnostics_text(entry.get("category")),
                "eventName": _redact_diagnostics_text(entry.get("event")),
                "summary": _redact_diagnostics_text(entry.get("summary")),
                "fields": fields,
            }
        )
    return logs


def get_copy_diagnostics_snapshot() -> dict:
    """Return a compact, shareable snapshot without payloads or local paths."""
    state = get_session_view_state()
    report = get_asset_bridge_view_state()
    meta = report.get("operation_meta") or {}
    runtime, active_object = _controller_diagnostics_for_copy()

    safe_meta = {}
    for key in (
        "updateIntent",
        "triggerType",
        "selectedObjectCount",
        "supportedSelectedObjectCount",
        "unsupportedSelectedObjectCount",
        "resourceCountEstimate",
        "objectAssemblyCount",
        "riggedObjectCount",
        "objectStateCount",
    ):
        if key in meta and meta.get(key) is not None:
            value = meta.get(key)
            safe_meta[key] = _redact_diagnostics_text(value) if isinstance(value, str) else value

    return {
        "schemaVersion": _DIAGNOSTICS_SCHEMA_VERSION,
        "timestampUtc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "blenderVersion": _blender_version_for_diagnostics(),
        "session": {
            "localReady": bool(state.get("local_ready")),
            "connectAttempted": bool(state.get("connect_attempted")),
            "transportConnected": bool(state.get("transport_connected")),
            "counterpartObserved": bool(state.get("counterpart_observed")),
            "handshakeConfirmed": bool(state.get("connected")),
            "handshakePhase": str(state.get("handshake_phase") or "disconnected"),
            "protocolVersion": state.get("peer_protocol_version"),
            "legacyProtocol": bool(state.get("legacy_protocol")),
            "negotiatedFeatures": list(state.get("negotiated_features") or []),
            "endpoint": _redact_diagnostics_text(state.get("endpoint")),
            "lastError": _redact_diagnostics_text(state.get("last_error")),
            "lastWsError": _redact_diagnostics_text(state.get("last_ws_handoff_error")),
        },
        "runtime": runtime,
        "activeObject": active_object,
        "lastOperation": {
            "available": report.get("last_send_ok") is not None,
            "timestampUtc": report.get("operation_timestamp_utc"),
            "ok": report.get("last_send_ok"),
            "message": _redact_diagnostics_text(report.get("last_send_message")),
            "error": _redact_diagnostics_text(report.get("last_send_error")),
            "payloadBytes": int(report.get("last_payload_size") or 0),
            "resourceCount": int(report.get("last_resource_count") or 0),
            "failureCategory": _redact_diagnostics_text(report.get("failure_category")),
            "metadata": safe_meta,
        },
        "recentLogs": _recent_logs_for_copy(),
    }


def get_copy_diagnostics_json() -> str:
    return json.dumps(
        get_copy_diagnostics_snapshot(),
        ensure_ascii=False,
        indent=2,
        sort_keys=True,
    )


def get_session_view_state() -> dict:
    endpoint = getattr(getattr(_SESSION, "_state", None), "endpoint", None)
    truth = _SESSION.get_truth_state()
    return {
        "local_ready": truth["local_ready"],
        "connect_attempted": truth["connect_attempted"],
        "transport_connected": truth["transport_connected"],
        "counterpart_observed": truth["counterpart_observed"],
        "connected": truth["handshake_confirmed"],
        "handshake_phase": truth.get("handshake_phase"),
        "handshake_id": truth.get("current_handshake_id"),
        "peer_protocol_version": truth.get("peer_protocol_version"),
        "negotiated_features": truth.get("negotiated_features") or [],
        "legacy_protocol": bool(truth.get("legacy_protocol")),
        "endpoint": endpoint,
        "assets_import_root": truth.get("assets_import_root"),
        "texture_export_root": truth.get("texture_export_root"),
        "last_error": _SESSION.get_last_error(),
        "last_ws_handoff_error": _LAST_WS_HANDOFF_ERROR,
    }
