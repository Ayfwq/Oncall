"""Validation coverage for the single remote-Python project contract."""

from uuid import uuid4

import pytest
from oncall.application.dtos import MetricsSourceDTO, MonitoringRuleDTO, ProjectCreateDTO, PythonProjectOnboardDTO, ServiceEndpointDTO
from pydantic import ValidationError


def base_project(**kwargs):
    return ProjectCreateDTO(name='stock-api', server_id=uuid4(), **kwargs)


def test_project_accepts_supported_host_and_reserved_test_rule():
    base_project(rules=[MonitoringRuleDTO(metric_key='host.cpu.percent', trigger_threshold=85, recovery_threshold=70)])
    base_project(rules=[MonitoringRuleDTO(metric_key='zz.test.synthetic', trigger_threshold=1, recovery_threshold=0)])


def test_unknown_metric_and_missing_remote_target_are_rejected():
    with pytest.raises(ValidationError, match='unknown metric'):
        base_project(rules=[MonitoringRuleDTO(metric_key='not.registered', trigger_threshold=1, recovery_threshold=0)])
    with pytest.raises(ValidationError, match='metrics_sources'):
        base_project(rules=[MonitoringRuleDTO(metric_key='process.target.count', trigger_threshold=1, recovery_threshold=0)])


def test_prometheus_source_can_supply_process_rules():
    base_project(
        metrics_sources=[MetricsSourceDTO(url='http://python.example:8000/metrics')],
        rules=[MonitoringRuleDTO(metric_key='process.target.cpu_percent_sum', trigger_threshold=10000, recovery_threshold=9000, detection_mode='baseline')],
    )


def test_remote_only_fields_and_rule_direction_are_validated():
    with pytest.raises(ValidationError, match='recovery threshold'):
        MonitoringRuleDTO(metric_key='host.cpu.percent', trigger_threshold=70, recovery_threshold=85)
    with pytest.raises(ValidationError, match='absolute http'):
        ServiceEndpointDTO(url='localhost:9900/health')
    with pytest.raises(ValidationError, match='server_id'):
        ProjectCreateDTO(name='missing-server')


def test_python_onboarding_requires_health_and_metrics_urls():
    dto = PythonProjectOnboardDTO(name='stock-api', server_id=uuid4(), health_url='http://127.0.0.1:8000/health', metrics_url='http://127.0.0.1:8000/metrics')
    assert dto.poll_interval == 30
    assert dto.environment == 'production'
    with pytest.raises(ValidationError, match='health_url'):
        PythonProjectOnboardDTO(name='empty', server_id=uuid4(), health_url='', metrics_url='')
