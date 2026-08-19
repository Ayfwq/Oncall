from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any


@dataclass(slots=True)
class FeishuInboundMessage:
    event_key: str
    message_id: str
    chat_id: str
    sender_id: str | None
    text: str
    root_id: str | None = None
    parent_id: str | None = None


def _get(obj: Any, *path: str, default: Any = None) -> Any:
    cur = obj
    for key in path:
        if cur is None:
            return default
        if isinstance(cur, dict):
            cur = cur.get(key)
        else:
            cur = getattr(cur, key, None)
    return default if cur is None else cur


def parse_lark_message(event: Any) -> FeishuInboundMessage | None:
    """Convert a lark-oapi event object/dict into a stable channel DTO."""
    message = _get(event, "event", "message") or _get(event, "message")
    sender = _get(event, "event", "sender") or _get(event, "sender")
    if not message:
        return None
    message_id = _get(message, "message_id")
    chat_id = _get(message, "chat_id")
    if not message_id or not chat_id:
        return None
    raw_content = _get(message, "content", default="")
    try:
        payload = json.loads(raw_content) if isinstance(raw_content, str) else (raw_content or {})
        text = str(payload.get("text", "")).strip()
    except Exception:
        text = str(raw_content or "").strip()
    if not text:
        return None
    sender_id = (
        _get(sender, "sender_id", "open_id")
        or _get(sender, "sender_id", "user_id")
        or _get(sender, "sender_id", "union_id")
    )
    event_key = _get(event, "header", "event_id") or _get(event, "event_id") or message_id
    return FeishuInboundMessage(
        event_key=str(event_key),
        message_id=str(message_id),
        chat_id=str(chat_id),
        sender_id=str(sender_id) if sender_id else None,
        text=text,
        root_id=_get(message, "root_id"),
        parent_id=_get(message, "parent_id"),
    )
