from oncall.monitoring.alert_policy import should_open_incident


def test_shared_resource_pressure_is_evidence_not_a_user_facing_incident():
    assert not should_open_incident('host.cpu.percent')
    assert not should_open_incident('host.memory.percent')
    assert not should_open_incident('process.target.cpu_percent_sum')
    assert not should_open_incident('process.target.rss_bytes_sum')
    assert not should_open_incident('host.gpu.memory_percent')


def test_service_database_log_and_capacity_failures_are_actionable():
    assert should_open_incident('service.consecutive_failures')
    assert should_open_incident('app.http.error_rate')
    assert should_open_incident('host.disk.usage_percent')
    assert should_open_incident('log.exception_count')
    assert should_open_incident('db.up')


def test_long_transactions_need_a_second_impact_signal():
    healthy_context = {
        'db.long_transactions': 4,
        'db.lock_waits': 0,
        'db.connections.utilization_percent': 12,
        'db.slow_queries': 0,
        'service.consecutive_failures': 0,
        'app.http.error_rate': 0,
        'app.http.availability': 100,
    }
    assert not should_open_incident('db.long_transactions', healthy_context)
    assert should_open_incident('db.long_transactions', {**healthy_context, 'db.lock_waits': 1})
    assert should_open_incident('db.long_transactions', {**healthy_context, 'db.connections.utilization_percent': 72})
    assert should_open_incident('db.long_transactions', {**healthy_context, 'app.http.error_rate': 0.08})
