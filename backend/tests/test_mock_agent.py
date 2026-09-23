import pytest
from oncall.agent.model_gateway import MockProvider


@pytest.mark.asyncio
async def test_mock_chat_uses_rag_first():
    d = await MockProvider().decide(
        {
            "mode": "chat",
            "user_message": "PostgreSQL 备份怎么做",
            "called_tools": [],
            "project_id": None,
        }
    )
    assert d.action == "tool" and d.tool_name == "search_knowledge"


@pytest.mark.asyncio
async def test_mock_chat_without_project_is_general_qa():
    d = await MockProvider().decide(
        {
            "mode": "chat",
            "user_message": "PostgreSQL 备份怎么做",
            "called_tools": ["search_knowledge"],
            "project_id": None,
            "evidence": [{"summary": "备份应定期验证恢复"}],
        }
    )
    assert d.action == "final"
    assert "通用运维问答模式" in d.answer
    assert "绑定该会话" not in d.answer


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("message", "tool"),
    [
        ("查看最近错误日志", "search_logs"),
        ("PostgreSQL 数据库现在有锁吗", "query_database_health"),
        ("Docker 容器有没有 OOM", "query_runtime_resources"),
        ("API 指标正常吗", "query_current_metrics"),
        ("当前 CPU 是多少", "query_current_metrics"),
    ],
)
async def test_mock_chat_routes_realtime_question_to_specialized_tool(message, tool):
    decision = await MockProvider().decide(
        {"mode": "chat", "user_message": message, "called_tools": [], "project_id": "project"}
    )
    assert decision.action == "tool"
    assert decision.tool_name == tool
