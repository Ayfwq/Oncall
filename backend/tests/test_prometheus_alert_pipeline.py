from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest
from oncall.integrations.prometheus_api import (
    PrometheusProvisioner,
    _project_rules,
    _target_from_url,
    project_metric_promql,
)


def _project():
    return SimpleNamespace(id=uuid4(), name="股票预测服务")


def test_prometheus_targets_keep_project_and_component_identity():
    address, path, scheme = _target_from_url("https://metrics.example.test/custom/metrics")

    assert (address, path, scheme) == (
        "metrics.example.test",
        "/custom/metrics",
        "https",
    )


def test_first_version_rules_are_conservative_and_alertmanager_ready():
    rules = "\n".join(
        _project_rules(
            _project(),
            has_container_metrics=True,
            has_database_metrics=True,
            compose_project="tradingagents",
            compose_services=["api", "worker"],
        )
    )

    assert "OncallApplicationMetricsDown" in rules
    assert "OncallApplicationErrorRateHigh" in rules
    assert "OncallDatabaseDown" in rules
    assert "oncall_postgres_connections_utilization_percent" in rules
    assert "OncallContainerCpuHigh" in rules
    assert "for: 2m" in rules
    assert "for: 10m" in rules
    assert "project_id:" in rules
    assert "category:" in rules
    assert "severity:" in rules
    assert 'container_label_com_docker_compose_project="tradingagents"' in rules
    assert 'container_label_com_docker_compose_service=~"api|worker"' in rules


def test_container_rules_are_not_rendered_without_container_metrics_endpoint():
    rules = "\n".join(_project_rules(_project(), has_container_metrics=False))

    assert "OncallContainerCpuHigh" not in rules
    assert "OncallContainerMemoryHigh" not in rules


def test_database_rules_are_not_rendered_without_database_metrics_target():
    rules = "\n".join(_project_rules(_project(), has_container_metrics=False))

    assert "OncallDatabaseDown" not in rules
    assert "oncall_postgres_up" not in rules


def test_database_dashboard_and_agent_share_canonical_promql():
    project_id = uuid4()

    assert project_metric_promql("db.up", project_id) == (
        f'min(oncall_postgres_up{{project_id="{project_id}",component="database"}})'
    )
    assert "oncall_postgres_long_transactions" in str(
        project_metric_promql("db.long_transactions", project_id)
    )


@pytest.mark.asyncio
async def test_prometheus_provisioning_files_match_prometheus_mount_paths(tmp_path, monkeypatch):
    class EmptyScalars:
        @staticmethod
        def all():
            return []

    class EmptySession:
        @staticmethod
        async def scalars(_statement):
            return EmptyScalars()

    class NoopHttpClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def post(self, _url):
            return SimpleNamespace(status_code=200)

    settings = SimpleNamespace(
        data_dir=tmp_path,
        prometheus_url="http://prometheus:9090",
        prometheus_reload_url="http://prometheus:9090/-/reload",
        prometheus_scrape_base_url="http://api:9900",
        prometheus_scrape_token="",
        prometheus_proxy_application_scrapes=True,
    )
    monkeypatch.setattr(
        "oncall.integrations.prometheus_api.get_settings", lambda: settings
    )
    monkeypatch.setattr(
        "oncall.integrations.prometheus_api.httpx.AsyncClient",
        lambda **_kwargs: NoopHttpClient(),
    )

    result = await PrometheusProvisioner(EmptySession()).reconcile()

    targets_file = tmp_path / "prometheus" / "targets" / "oncall.json"
    rules_file = tmp_path / "prometheus" / "rules" / "oncall.yml"
    repository_root = Path(__file__).resolve().parents[2]
    prometheus_config = (
        repository_root / "deploy" / "prometheus" / "prometheus.yml"
    ).read_text(encoding="utf-8")
    local_compose = (repository_root / "compose.local.monitoring.yaml").read_text(
        encoding="utf-8"
    )
    server_compose = (repository_root / "compose.server.yaml").read_text(
        encoding="utf-8"
    )
    assert result == {"projects": 0, "targets": 0, "rules": 0}
    assert json.loads(targets_file.read_text(encoding="utf-8")) == []
    assert rules_file.is_file()
    assert "/prometheus-data/prometheus/targets/*.json" in prometheus_config
    assert "/prometheus-data/prometheus/rules/*.yml" in prometheus_config
    assert "./data:/prometheus-data:ro" in local_compose
    assert "oncall_data:/prometheus-data:ro" in server_compose
