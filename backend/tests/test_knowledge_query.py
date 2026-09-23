from oncall.agent.graph import build_knowledge_query


def test_incident_query_uses_alert_identity_and_cpu_terms():
    query = build_knowledge_query(
        {
            "incident_id": "incident-1",
            "user_message": "请基于当前 Incident 主动调查并生成完整故障报告。",
            "incident_context": {
                "anomaly_type": "OncallHostCpuHigh",
                "summary": "服务器 CPU 连续超过 95%",
            },
        }
    )

    assert "OncallHostCpuHigh" in query
    assert "服务器 CPU 连续超过 95%" in query
    assert "CPU 使用率过高" in query
    assert "故障排查" in query
    assert "请基于当前 Incident 主动调查" not in query


def test_incident_query_uses_database_terms_and_keeps_follow_up_context():
    query = build_knowledge_query(
        {
            "incident_id": "incident-2",
            "user_message": "这个长事务应该怎么处理？",
            "incident_context": {
                "anomaly_type": "OncallDatabaseLongTransaction",
                "summary": "数据库存在长事务",
            },
        }
    )

    assert "OncallDatabaseLongTransaction" in query
    assert "PostgreSQL 数据库长事务 pg_stat_activity 排查处理方案" in query
    assert "这个长事务应该怎么处理？" in query


def test_collector_down_has_a_collector_query_not_a_container_cpu_query():
    query = build_knowledge_query(
        {
            "incident_id": "incident-3",
            "user_message": "请基于当前 Incident 主动调查并生成完整故障报告。",
            "incident_context": {
                "anomaly_type": "OncallContainerMetricsDown",
                "summary": "Docker 容器指标采集器不可用",
            },
        }
    )

    assert "cAdvisor Docker 容器指标采集器不可用" in query
    assert "CPU 使用率过高" not in query
