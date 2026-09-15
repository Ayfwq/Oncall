from oncall.monitoring.alert_policy import should_open_incident


def test_shared_resource_pressure_is_evidence_not_a_user_facing_incident():
    assert not should_open_incident('host.cpu.percent')
    assert not should_open_incident('host.memory.percent')
    assert not should_open_incident('process.target.cpu_percent_sum')
    assert not should_open_incident('process.target.rss_bytes_sum')


def test_service_database_log_and_capacity_failures_are_actionable():
    assert should_open_incident('service.consecutive_failures')
    assert should_open_incident('app.http.error_rate')
    assert should_open_incident('host.disk.usage_percent')
    assert should_open_incident('log.exception_count')
    assert should_open_incident('db.up')
