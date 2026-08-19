"""Real-data verification of the six Monitoring Engine integration classes.

Each test confirms the integration returns either a genuine ToolResult.ok with
real resource data, or an explicit ok=False with an error code — never fake
values.  Resource-dependent tests skip only when the underlying resource is
unavailable on this machine.
"""
from __future__ import annotations

import asyncio
import os
from pathlib import Path

import pytest
from oncall.application.dtos import (
    DatabaseProfileDTO,
    DockerTargetDTO,
    LogSourceDTO,
    ProcessTargetDTO,
    ServiceEndpointDTO,
)
from oncall.integrations.database import DatabaseIntegration
from oncall.integrations.docker_integration import DockerIntegration
from oncall.integrations.host import HostIntegration
from oncall.integrations.logs import ERROR_RE, WARN_RE, LogIntegration
from oncall.integrations.process import ProcessIntegration
from oncall.integrations.service import ServiceIntegration

pytestmark = pytest.mark.local

ROOT = Path(__file__).resolve().parents[3]


async def test_host_integration_real_values():
    result = await HostIntegration().query()
    assert result.ok, result.summary
    sig = result.data["signals"]
    for key in ("host.cpu.percent", "host.memory.percent", "host.memory.available_bytes",
                "host.disk.usage_percent", "host.disk.free_bytes",
                "host.disk.read_bytes_per_sec", "host.disk.write_bytes_per_sec",
                "host.net.rx_bytes_per_sec", "host.net.tx_bytes_per_sec"):
        assert key in sig, f"{key} missing"
        assert isinstance(sig[key], int | float), f"{key} not numeric"
    assert 0 <= float(sig["host.cpu.percent"]) <= 100
    assert 0 <= float(sig["host.memory.percent"]) <= 100
    assert 0 <= float(sig["host.disk.usage_percent"]) <= 100
    # the ToolResult summary reflects the real numbers
    assert "CPU" in result.summary and "%" in result.summary
    # resources carry real counters
    counters = result.data["resources"]["counters"]
    assert "ts" in counters


async def test_process_integration_matches_current_python():
    target = ProcessTargetDTO(name="python", executable="python")
    result = await ProcessIntegration([target]).query()
    assert result.ok
    rows = result.data or []
    assert rows, "no python process matched"
    current = os.getpid()
    assert any(r["pid"] == current for r in rows), f"expected current pid {current} in matches"
    for r in rows[:1]:
        assert "python" in r["name"].lower()
        assert r["rss_bytes"] > 0
    # collect() exposes the aggregated contract signals. The exact count can drift
    # between query() and collect() as sibling processes spawn/exit, so assert the
    # invariant (a live python process exists) rather than an exact cross-call match.
    cr = await ProcessIntegration([target]).collect()
    assert cr.ok
    assert cr.signals["process.target.alive"] == 1.0
    assert cr.signals["process.target.count"] >= 1.0
    assert cr.signals["process.target.rss_bytes_sum"] > 0


def _docker_available() -> bool:
    try:
        import docker

        docker.from_env().ping()
        return True
    except Exception:
        return False


@pytest.mark.skipif(not _docker_available(), reason="Docker daemon not available")
async def test_docker_integration_real_container_state():
    result = await DockerIntegration(
        [DockerTargetDTO(container_ref="oncall-ai-sre-postgres-1")]
    ).query()
    assert result.ok, f"expected real data, got {result}"
    row = result.data[0]
    assert row["container"] == "oncall-ai-sre-postgres-1"
    assert row["running"] is True
    assert row["status"] == "running"
    assert isinstance(row["cpu_percent"], float) and isinstance(row["memory_percent"], float)
    cr = await DockerIntegration(
        [DockerTargetDTO(container_ref="oncall-ai-sre-postgres-1")]
    ).collect()
    assert cr.ok
    assert cr.signals["container.running"] == 1.0


async def test_docker_unavailable_returns_explicit_error_not_fake_data(monkeypatch):
    """When the daemon/client is unavailable, query() must fail with the
    DOCKER_UNAVAILABLE error code and collect() must carry an ok=False
    CollectResult — never fabricated container rows."""

    def boom():
        raise RuntimeError("cannot connect to the Docker daemon")

    monkeypatch.setattr(DockerIntegration, "_client", boom)
    result = await DockerIntegration([DockerTargetDTO(container_ref="x")]).query()
    assert not result.ok
    assert result.error_code == "DOCKER_UNAVAILABLE"
    cr = await DockerIntegration([DockerTargetDTO(container_ref="x")]).collect()
    assert not cr.ok and cr.error


async def test_database_integration_real_pg(pg_ready):
    if not pg_ready:
        pytest.skip("PostgreSQL unreachable")
    profile = DatabaseProfileDTO(
        host="127.0.0.1", port=5432, database="oncall",
        username="oncall", password="oncall",
    )
    result = await DatabaseIntegration([profile]).query()
    assert result.ok, result.summary
    row = result.data[0]
    assert row["reachable"] is True
    assert row["connections"] >= 1
    assert row["max_connections"] >= row["connections"]
    for key in ("connections_usage_percent", "long_query_count", "lock_wait_count", "deadlocks"):
        assert isinstance(row[key], int | float)
    cr = await DatabaseIntegration([profile]).collect()
    assert cr.ok
    assert cr.signals["db.reachable"] == 1.0
    assert 0 <= float(cr.signals["db.connections.usage_percent"]) <= 100


async def test_service_integration_real_http_health():
    endpoint = ServiceEndpointDTO(url="http://127.0.0.1:9900/api/health")
    result = await ServiceIntegration([endpoint]).query()
    # tolerate transient HTTP hiccups (the API is shared with the live worker)
    for _ in range(2):
        if result.ok:
            break
        await asyncio.sleep(0.5)
        result = await ServiceIntegration([endpoint]).query()
    if not result.ok and result.data and result.data[0].get("error"):
        pytest.skip(f"local API not reachable: {result.data[0]['error']}")
    assert result.ok, f"service probe failed: {result.summary} {result.data}"
    row = result.data[0]
    assert row["reachable"] is True
    assert row["status_code"] == 200
    assert row["ok"] is True
    assert row["latency_ms"] >= 0
    cr = await ServiceIntegration([endpoint]).collect()
    assert cr.ok
    assert cr.signals["service.reachable"] == 1.0
    assert cr.signals["service.status_code"] == 200.0


async def test_log_integration_tail_and_stats():
    path = ROOT / "VALIDATION.md"  # small real text file in the project root
    assert path.is_file(), "VALIDATION.md missing from project root"
    src = LogSourceDTO(path=str(path))
    result = await LogIntegration([src]).query()
    assert result.ok
    assert result.data, "tail returned no lines"
    cr = await LogIntegration([src]).collect()
    assert cr.ok
    raw = path.read_text(encoding="utf-8", errors="replace").splitlines()[-3000:]
    expected_errors = sum(1 for line in raw if ERROR_RE.search(line))
    expected_warnings = sum(1 for line in raw if WARN_RE.search(line))
    assert cr.signals["log.error.count_window"] == float(expected_errors)
    assert cr.signals["log.warning.count_window"] == float(expected_warnings)
    # error/warning counts agree with a direct regex scan (no fabricated stats)
    assert (expected_errors, expected_warnings) == (
        int(cr.signals["log.error.count_window"]),
        int(cr.signals["log.warning.count_window"]),
    )


async def test_log_integration_missing_file_reports_error():
    src = LogSourceDTO(path=str(ROOT / "definitely-not-a-real-file-xyz.log"))
    cr = await LogIntegration([src]).collect()
    assert not cr.ok  # explicit failure, not fake zero-count data
    sources = cr.resources.get("sources", {})
    assert sources and not any(v.get("ok", True) for v in sources.values())
