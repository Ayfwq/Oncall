from oncall.agent.router import classify_intent


def test_casual_chat_skips_knowledge_and_tools():
    route = classify_intent("你好，你是什么模型？", project_id=None, incident_id=None)
    assert route["intent"] == "casual_chat"
    assert route["requires_knowledge"] is False
    assert route["requires_realtime"] is False


def test_ops_question_requires_knowledge_without_project():
    route = classify_intent("Docker 一直安装不上怎么办？", project_id=None, incident_id=None)
    assert route["intent"] == "ops_qa"
    assert route["requires_knowledge"] is True
    assert route["requires_project"] is False


def test_realtime_question_requires_project():
    route = classify_intent("最近五分钟有错误日志吗？", project_id=None, incident_id=None)
    assert route["intent"] == "clarification"
    assert route["requires_project"] is True

    route = classify_intent("最近五分钟有错误日志吗？", project_id="project-1", incident_id=None)
    assert route["intent"] == "project_query"
    assert route["requires_knowledge"] is True
    assert route["requires_realtime"] is True


def test_general_metric_troubleshooting_uses_shared_knowledge_without_project():
    route = classify_intent("主机 CPU 持续过高怎么定位和恢复？", project_id=None, incident_id=None)
    assert route["intent"] == "ops_qa"
    assert route["requires_knowledge"] is True
    assert route["requires_project"] is False
    assert route["requires_realtime"] is False


def test_realtime_metric_query_still_requires_project():
    route = classify_intent("当前 CPU 指标是多少？", project_id=None, incident_id=None)
    assert route["intent"] == "clarification"
    assert route["requires_knowledge"] is False
    assert route["requires_project"] is True
    assert route["requires_realtime"] is True


def test_current_alert_list_uses_user_scoped_query_without_project():
    route = classify_intent("目前还有哪些异常告警呢？", project_id=None, incident_id=None)
    assert route["intent"] == "active_alerts"
    assert route["requires_realtime"] is True
    assert route["requires_project"] is False
    assert route["requires_knowledge"] is False


def test_ambiguous_alert_reference_asks_for_explicit_anchor():
    route = classify_intent("这个告警为什么发生？", project_id=None, incident_id=None)
    assert route["intent"] == "clarification"
    assert "追问此告警" in route["clarification_question"]


def test_incident_conversation_keeps_incident_context():
    route = classify_intent(
        "现在恢复了吗？", project_id="project-1", incident_id="incident-1", mode="follow_up"
    )
    assert route["intent"] == "incident_followup"
    assert route["requires_incident"] is True
    assert route["requires_knowledge"] is True


def test_incident_conversation_can_switch_to_general_ops_question():
    route = classify_intent(
        "Docker 怎么清理无用镜像？",
        project_id="project-1",
        incident_id="incident-1",
        mode="follow_up",
    )
    assert route["intent"] == "ops_qa"
    assert route["requires_incident"] is False
    assert route["requires_realtime"] is False
