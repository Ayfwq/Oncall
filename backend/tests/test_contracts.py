from oncall.agent.tool_contracts import ALLOWED_TOOLS
from oncall.channels.feishu_events import parse_lark_message
from oncall.monitoring.signals import PYTHON_GPU_SIGNALS, PYTHON_SIGNALS, SUPPORTED_SIGNALS


def test_has_single_remote_python_signal_contract():
    assert len(PYTHON_SIGNALS) == 24
    assert len(PYTHON_GPU_SIGNALS) == 6
    assert len(SUPPORTED_SIGNALS) == 43
    assert len(set(PYTHON_SIGNALS)) == 24


def test_has_only_remote_read_tools():
    assert ALLOWED_TOOLS == {
        "query_incident_context",
        "query_current_metrics",
        "query_metric_history",
        "search_knowledge",
        "search_logs",
        "query_database_health",
        "query_runtime_resources",
    }


def test_feishu_message_parser_preserves_incident_thread_anchor():
    event = {
        "header": {"event_id": "evt-1"},
        "event": {
            "sender": {"sender_id": {"open_id": "ou-user"}},
            "message": {
                "message_id": "om-reply",
                "chat_id": "oc-chat",
                "root_id": "om-report",
                "content": '{"text":"现在 CPU 多少？"}',
            },
        },
    }
    parsed = parse_lark_message(event)
    assert parsed is not None
    assert parsed.root_id == "om-report"
    assert parsed.text == "现在 CPU 多少？"
    assert parsed.sender_id == "ou-user"
