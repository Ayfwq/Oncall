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


class MonitoringIntegration(Protocol):
    name: str
    async def collect(self) -> CollectResult: ...
