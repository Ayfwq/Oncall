from oncall.agent.tool_contracts import ALLOWED_TOOLS
from oncall.channels.feishu_events import parse_lark_message
from oncall.monitoring.signals import BASELINE_SIGNALS


def test_v1_has_exactly_32_baseline_signals():
    assert len(BASELINE_SIGNALS) == 32
    assert len(set(BASELINE_SIGNALS)) == 32


def test_v1_has_exactly_8_read_tools():
    assert ALLOWED_TOOLS == {
        'query_host_metrics','query_metric_history','query_processes','query_logs',
        'query_containers','query_database','query_service_health','search_knowledge'
    }


def test_feishu_message_parser_preserves_incident_thread_anchor():
    event = {
        'header': {'event_id': 'evt-1'},
        'event': {
            'sender': {'sender_id': {'open_id': 'ou-user'}},
            'message': {
                'message_id': 'om-reply',
                'chat_id': 'oc-chat',
                'root_id': 'om-report',
                'content': '{"text":"现在 CPU 多少？"}',
            },
        },
    }
    parsed = parse_lark_message(event)
    assert parsed is not None
    assert parsed.root_id == 'om-report'
    assert parsed.text == '现在 CPU 多少？'
    assert parsed.sender_id == 'ou-user'
