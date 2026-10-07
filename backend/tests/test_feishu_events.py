from oncall.channels.feishu_events import parse_lark_message


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
