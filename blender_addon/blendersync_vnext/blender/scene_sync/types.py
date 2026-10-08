from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class EligibilityResult:
    eligible: bool
    reason: str | None = None


@dataclass
class TransformSnapshot:
    position: tuple[float, float, float]
    rotation: tuple[float, float, float, float]
    scale: tuple[float, float, float]


@dataclass
class TransformSyncResult:
    ok: bool
    reason: str | None
    pair_id: str | None
    payload: dict[str, Any] | None = None


@dataclass
class TransformCounters:
    sends: int = 0
    skips: int = 0
    errors: int = 0


@dataclass
class SceneSyncState:
    active: bool = False
    last_result: TransformSyncResult | None = None
    last_skip_reason: str | None = None
    counters: TransformCounters = field(default_factory=TransformCounters)

