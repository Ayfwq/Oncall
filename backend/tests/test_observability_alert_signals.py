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


@pytest.mark.asyncio
async def test_diagnostic_tools_forward_narrow_scope_and_group_log_signatures(monkeypatch):
    integration = _integration()
    integration.logs[0].parser_config = {"compose_project": "demo", "services": ["api", "worker"]}
    requests = []

    async def fake_post(path, body):
        requests.append((path, body))
        if path == "/v1/logs/search":
            return {"ok": True, "containers": ["api-1"], "lines": [
                {"container": "api-1", "line": "ERROR request 123 failed"},
                {"container": "api-1", "line": "ERROR request 456 failed"},
            ]}
        if path == "/v1/database/diagnose":
            return {"ok": True, "signals": {"db.lock_waits": 2}}
        return {"ok": True, "containers": [{"name": "api-1", "cpu_percent": 12.0}]}

    monkeypatch.setattr(integration, "_post", fake_post)
    logs = await integration.search_logs(level="ERROR", services=["api"])
    database = await integration.query_database(["lock_waits", "blocking_chain"], 7)
    runtime = await integration.query_runtime_resources(["api"], ["cpu", "oom"], 3)

    assert logs.ok and logs.data["signatures"][0]["count"] == 2
    assert database.ok and runtime.ok
    assert requests[0][1]["services"] == ["api"]
    assert requests[1][1]["checks"] == ["lock_waits", "blocking_chain"]
    assert requests[1][1]["slow_query_limit"] == 7
    assert requests[2][1]["checks"] == ["cpu", "oom"]
    assert requests[2][1]["top_n"] == 3


@pytest.mark.asyncio
async def test_diagnostic_tools_reject_service_outside_configured_scope(monkeypatch):
    integration = _integration()
    integration.logs[0].parser_config = {"services": ["api"]}

    async def must_not_call(path, body):
        raise AssertionError("collector must not be called")

    monkeypatch.setattr(integration, "_post", must_not_call)
    logs = await integration.search_logs(services=["database"])
    runtime = await integration.query_runtime_resources(["database"])
    assert logs.error_code == "SERVICE_NOT_ALLOWED"
    assert runtime.error_code == "SERVICE_NOT_ALLOWED"
