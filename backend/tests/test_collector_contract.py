import pytest
from oncall.collector.app import DatabaseRequest, RuntimeQuery, app
from pydantic import ValidationError


def test_collector_exposes_all_read_only_diagnostic_routes():
    paths = set(app.openapi()["paths"])
    assert {
        "/health",
        "/v1/docker/discover",
        "/v1/logs/search",
        "/v1/database/diagnose",
        "/v1/runtime/diagnose",
    } <= paths


def test_collector_rejects_unknown_diagnostic_checks():
    with pytest.raises(ValidationError):
        DatabaseRequest(dsn="postgresql://example", checks=["drop_database"])
    with pytest.raises(ValidationError):
        RuntimeQuery(checks=["kill_process"])
