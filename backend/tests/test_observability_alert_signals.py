from uuid import uuid4

import pytest

from oncall.application.dtos import DatabaseProfileDTO, LogSourceDTO, MonitoredServerDTO
from oncall.integrations.observability import RemoteObservabilityIntegration


def _integration() -> RemoteObservabilityIntegration:
    server = MonitoredServerDTO(
        id=uuid4(),
        name="target",
        node_metrics_url="http://target:9100/metrics",
        collector_url="http://target:9910",
        collector_token="secret",
    )
    database = DatabaseProfileDTO(host="postgres", database="app", username="monitor")
    return RemoteObservabilityIntegration(server, [LogSourceDTO()], [database])


@pytest.mark.asyncio
async def test_dead_collector_emits_alertable_log_and_database_up_signals(monkeypatch):
    integration = _integration()

    async def unavailable(path, body):
        raise RuntimeError("collector connection refused")

    monkeypatch.setattr(integration, "_post", unavailable)
    result = await integration.collect()

    assert result.ok is False
    assert result.signals["log.collector.up"] == 0.0
    assert result.signals["db.up"] == 0.0
    assert "日志" in (result.error or "")
    assert "数据库" in (result.error or "")


@pytest.mark.asyncio
async def test_log_failure_does_not_hide_healthy_database(monkeypatch):
    integration = _integration()

    async def mixed(path, body):
        if path == "/v1/logs/search":
            raise RuntimeError("docker unavailable")
        return {"ok": True, "signals": {"db.up": 1, "db.connections.utilization_percent": 20}}

    monkeypatch.setattr(integration, "_post", mixed)
    result = await integration.collect()

    assert result.signals["log.collector.up"] == 0.0
    assert result.signals["db.up"] == 1.0
    assert result.signals["db.connections.utilization_percent"] == 20.0
