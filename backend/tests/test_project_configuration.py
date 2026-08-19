"""Fast, dependency-light validation coverage for Project configuration."""

import pytest
from pydantic import ValidationError

from oncall.application.dtos import (
    MonitoringRuleDTO,
    ProcessTargetDTO,
    ProjectCreateDTO,
    ServiceEndpointDTO,
)


def test_project_accepts_host_rule_and_reserved_test_metric():
    ProjectCreateDTO(
        name="local",
        rules=[MonitoringRuleDTO(metric_key="host.cpu.percent", trigger_threshold=85, recovery_threshold=70)],
    )
    ProjectCreateDTO(
        name="detector-test",
        rules=[MonitoringRuleDTO(metric_key="zz.test.synthetic", trigger_threshold=1, recovery_threshold=0)],
    )


def test_unknown_metric_and_missing_target_are_rejected():
    with pytest.raises(ValidationError, match="unknown metric"):
        ProjectCreateDTO(
            name="bad",
            rules=[MonitoringRuleDTO(metric_key="not.registered", trigger_threshold=1, recovery_threshold=0)],
        )
    with pytest.raises(ValidationError, match="requires an enabled process_targets"):
        ProjectCreateDTO(
            name="bad-target",
            rules=[MonitoringRuleDTO(metric_key="process.target.count", trigger_threshold=1, recovery_threshold=0)],
        )


def test_rule_direction_and_target_shape_are_rejected():
    with pytest.raises(ValidationError, match="recovery threshold"):
        MonitoringRuleDTO(metric_key="host.cpu.percent", trigger_threshold=70, recovery_threshold=85)
    with pytest.raises(ValidationError, match="process target needs"):
        ProcessTargetDTO(name="empty")
    with pytest.raises(ValidationError, match="absolute http"):
        ServiceEndpointDTO(url="localhost:9900/health")
