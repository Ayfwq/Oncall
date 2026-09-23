from __future__ import annotations

from types import SimpleNamespace
from uuid import uuid4

from oncall.integrations.prometheus_api import (
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
