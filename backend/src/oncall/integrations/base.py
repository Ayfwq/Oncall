from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Protocol


@dataclass
class CollectResult:
    name: str
    ok: bool
    signals: dict[str, float | bool | str | None] = field(default_factory=dict)
    # Per-resource signals keep the legacy project-level ``signals`` contract while
    # allowing rules to target an individual process, endpoint, container, database,
    # or log source. Keys are stable resource identifiers.
    resource_signals: dict[str, dict[str, float | bool | str | None]] = field(default_factory=dict)
    resources: dict[str, Any] = field(default_factory=dict)
    error: str | None = None
    observed_at: datetime = field(default_factory=lambda: datetime.now().astimezone())
    # Prometheus counters are cumulative, so rate-style metrics (rps, error rate)
    # need the previous sample to difference against. An integration reports the
    # values to remember; the engine persists them into metric_cursors.
    # Key format: f'{resource_key}||{metric_key}'
    cursor_updates: dict[str, float] = field(default_factory=dict)


class MonitoringIntegration(Protocol):
    name: str
    async def collect(self) -> CollectResult: ...
