from __future__ import annotations

import asyncio
from datetime import datetime

from oncall.application.dtos import SnapshotDTO
from oncall.integrations.base import CollectResult
from oncall.integrations.observability import RemoteObservabilityIntegration
from oncall.integrations.prometheus import PrometheusIntegration
from oncall.integrations.server_exporters import ServerExportersIntegration


class ProjectProbe:
    """One-off configuration test; it never decides or opens an alert."""

    async def run(self, project_id, config) -> SnapshotDTO:
        if config.server is None:
            raise ValueError("remote Python monitoring requires a configured server")
        integrations = [
            ServerExportersIntegration(config.server),
            PrometheusIntegration(None, project_id, config.metrics_sources, persist_state=False),
            RemoteObservabilityIntegration(
                config.server, config.log_sources, config.database_profiles
            ),
        ]

        async def collect(integration) -> CollectResult:
            try:
                return await asyncio.wait_for(integration.collect(), timeout=8)
            except Exception as exc:
                return CollectResult(name=integration.name, ok=False, error=str(exc))

        results = await asyncio.gather(*(collect(x) for x in integrations))
        signals: dict[str, float] = {}
        resource_signals: dict[str, dict[str, float]] = {}
        resources: dict = {}
        status: dict = {}
        for result in results:
            signals.update(result.signals)
            resources[result.name] = result.resources
            status[result.name] = {"ok": result.ok, "error": result.error}
            for resource, values in result.resource_signals.items():
                resource_signals.setdefault(str(resource), {}).update(values)
        return SnapshotDTO(
            project_id=project_id,
            observed_at=datetime.now().astimezone(),
            signals=signals,
            resource_signals=resource_signals,
            resources=resources,
            collector_status=status,
        )
