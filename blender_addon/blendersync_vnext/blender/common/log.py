from __future__ import annotations

from collections import deque
from collections.abc import Callable, Mapping
from datetime import datetime, timezone
import os
import sys
import threading
import time
import traceback


MAX_ENTRIES = 500
MAX_SUMMARY_LENGTH = 2048
MAX_FIELD_COUNT = 16
MAX_FIELD_KEY_LENGTH = 80
MAX_FIELD_VALUE_LENGTH = 512
VERBOSE_ENV = "BLENDERSYNC_VERBOSE_LOGS"

_SENSITIVE_FIELD_NAMES = frozenset(
    {
        "body",
        "content",
        "json",
        "payload",
        "raw",
        "rawjson",
        "binary",
        "binarydata",
    }
)
_entries: deque[dict] = deque(maxlen=MAX_ENTRIES)
_lock = threading.RLock()
_sequence = 0
_verbose_override: bool | None = None
_verbose_preference = False
_background_verbose = bool(
    getattr(getattr(sys.modules.get("bpy"), "app", None), "background", False)
)


def _truncate(value, limit: int) -> str:
    text = "" if value is None else str(value)
    return text if len(text) <= limit else text[: max(1, limit - 3)] + "..."


def _normalize_fields(fields: Mapping | None) -> dict[str, str]:
    if not isinstance(fields, Mapping):
        return {}

    normalized: dict[str, str] = {}
    for raw_key, raw_value in fields.items():
        if len(normalized) >= MAX_FIELD_COUNT:
            break
        key = _truncate(raw_key, MAX_FIELD_KEY_LENGTH).strip()
        if not key or key.lower().replace("_", "") in _SENSITIVE_FIELD_NAMES:
            continue
        if raw_value is not None and not isinstance(raw_value, (str, int, float, bool)):
            continue
        normalized[key] = _truncate(raw_value, MAX_FIELD_VALUE_LENGTH)
    return normalized


def _environment_verbose() -> bool | None:
    if VERBOSE_ENV not in os.environ:
        return None
    return str(os.environ.get(VERBOSE_ENV, "")).strip().lower() in {"1", "true", "yes", "on"}


def verbose_enabled() -> bool:
    with _lock:
        override = _verbose_override
        preference = _verbose_preference
    if override is not None:
        return override
    environment = _environment_verbose()
    if environment is not None:
        return environment
    return _background_verbose or preference


def set_verbose_override(value: bool | None) -> None:
    global _verbose_override
    with _lock:
        _verbose_override = None if value is None else bool(value)


def set_verbose_preference(value: bool) -> None:
    global _verbose_preference
    with _lock:
        _verbose_preference = bool(value)


def clear() -> None:
    with _lock:
        _entries.clear()


def latest_sequence() -> int:
    with _lock:
        return _entries[-1]["sequence"] if _entries else 0


def get_recent_entries(limit: int | None = None) -> list[dict]:
    with _lock:
        entries = list(_entries)
    if limit is not None:
        count = max(0, int(limit))
        entries = entries[-count:] if count else []
    return [{**entry, "fields": dict(entry["fields"])} for entry in entries]


def _append(level: str, category: str, event: str, summary, fields: Mapping | None) -> dict:
    global _sequence
    with _lock:
        _sequence += 1
        entry = {
            "sequence": _sequence,
            "timestampUtc": datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"),
            "timestampUnix": time.time(),
            "level": level,
            "category": _truncate(category or "General", 80),
            "event": _truncate(event or "event", 120),
            "summary": _truncate(summary, MAX_SUMMARY_LENGTH),
            "fields": _normalize_fields(fields),
        }
        _entries.append(entry)
    return entry


def _console_line(entry: dict) -> str:
    prefix = f"[BlenderSync][{entry['level']}][{entry['category']}] {entry['event']}"
    summary = entry.get("summary") or ""
    field_text = " ".join(f"{key}={value}" for key, value in entry.get("fields", {}).items())
    return " ".join(part for part in (prefix, summary, field_text) if part)


def _write(
    level: str,
    category: str,
    event: str,
    summary=None,
    fields: Mapping | None = None,
    *,
    console: bool,
) -> bool:
    entry = _append(level, category, event, summary, fields)
    if console:
        stream = sys.stderr if level == "ERROR" else sys.stdout
        print(_console_line(entry), file=stream, flush=True)
    return True


def error(category: str, event: str, summary=None, fields: Mapping | None = None) -> bool:
    return _write("ERROR", category, event, summary, fields, console=True)


def warn(category: str, event: str, summary=None, fields: Mapping | None = None) -> bool:
    return _write("WARN", category, event, summary, fields, console=verbose_enabled())


def info(category: str, event: str, summary=None, fields: Mapping | None = None) -> bool:
    return _write("INFO", category, event, summary, fields, console=verbose_enabled())


def trace(
    category: str,
    event: str,
    summary: str | Callable[[], str] | None = None,
    fields: Mapping | Callable[[], Mapping] | None = None,
) -> bool:
    if not verbose_enabled():
        return False
    try:
        resolved_summary = summary() if callable(summary) else summary
        resolved_fields = fields() if callable(fields) else fields
    except Exception as exc:
        return exception("Logging", "trace_factory_failed", exc)
    return _write("TRACE", category, event, resolved_summary, resolved_fields, console=True)


def exception(
    category: str,
    event: str,
    exc: BaseException,
    summary: str | None = None,
    fields: Mapping | None = None,
) -> bool:
    exception_fields = dict(fields or {})
    exception_fields.setdefault("exceptionType", type(exc).__name__)
    entry = _append(
        "ERROR",
        category,
        event,
        summary or str(exc) or type(exc).__name__,
        exception_fields,
    )
    formatted = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__)).rstrip()
    print(f"{_console_line(entry)}\n{formatted}", file=sys.stderr, flush=True)
    return True
