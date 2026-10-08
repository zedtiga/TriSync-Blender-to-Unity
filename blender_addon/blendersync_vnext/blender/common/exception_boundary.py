from __future__ import annotations

import os
import threading
import time
import traceback


TRACEBACK_ENV = "BLENDERSYNC_TRACEBACKS"
DEFAULT_TRACEBACK_INTERVAL_SECONDS = 10.0

_last_traceback_time_by_site: dict[str, float] = {}
_traceback_lock = threading.RLock()


def _tracebacks_enabled() -> bool:
    value = str(os.environ.get(TRACEBACK_ENV, "1")).strip().lower()
    return value not in {"0", "false", "no", "off"}


def report_boundary_exception(
    site: str,
    exc: BaseException,
    *,
    message: str | None = None,
    traceback_interval_seconds: float = DEFAULT_TRACEBACK_INTERVAL_SECONDS,
) -> bool:
    normalized_site = str(site or "unknown").strip() or "unknown"
    error_type = type(exc).__name__
    one_line = message or f"[vNext][BoundaryException] site={normalized_site} type={error_type} error={exc}"
    try:
        print(one_line)
    except Exception:
        pass

    if not _tracebacks_enabled():
        return False

    now = time.monotonic()
    interval = max(0.0, float(traceback_interval_seconds))
    with _traceback_lock:
        last = _last_traceback_time_by_site.get(normalized_site)
        if last is not None and (now - last) < interval:
            return False
        _last_traceback_time_by_site[normalized_site] = now

    try:
        formatted = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__)).rstrip()
        print(f"[vNext][BoundaryException] traceback site={normalized_site}\n{formatted}")
        return True
    except Exception:
        return False


def reset_boundary_exception_throttle() -> None:
    with _traceback_lock:
        _last_traceback_time_by_site.clear()
