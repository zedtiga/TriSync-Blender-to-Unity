from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class MeshUpdateResult:
    ok: bool
    reason: str | None
    pair_id: str | None
    payload: dict[str, Any] | None = None


@dataclass
class MeshUpdateCounters:
    sends: int = 0
    skips: int = 0
    errors: int = 0


@dataclass
class MeshUpdateState:
    active: bool = False
    last_result: MeshUpdateResult | None = None
    last_skip_reason: str | None = None
    counters: MeshUpdateCounters = field(default_factory=MeshUpdateCounters)

