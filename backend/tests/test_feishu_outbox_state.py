from __future__ import annotations

from types import SimpleNamespace
from uuid import uuid4

import pytest
from oncall.channels.feishu import FeishuOutboxSender
from oncall.infrastructure.db.models import Notification


class Result:
    def __init__(self, rows):
        self.rows = rows

    def all(self):
        return self.rows


class FakeSession:
    def __init__(self, rows):
        self.rows = rows
        self.commits = 0

    async def scalars(self, statement):
        return Result(self.rows)

    async def scalar(self, statement):
        return None

    async def commit(self):
        self.commits += 1


def settings(default_target=""):
    return SimpleNamespace(
        feishu_enabled=True,
        feishu_outbox_claim_seconds=60,
        feishu_default_receive_id=default_target,
        feishu_default_receive_id_type="chat_id",
        notification_cooldown_seconds=300,
    )


@pytest.mark.asyncio
async def test_missing_target_is_committed_as_dead():
    note = Notification(
        channel="feishu",
        target="default",
        status="pending",
        attempts=0,
        payload={"kind": "triggered"},
        dedupe_key="dead-test",
    )
    session = FakeSession([note])
    sender = FeishuOutboxSender(session)
    sender.s = settings()

    assert await sender.send_pending() == 0
    assert note.status == "dead"
    assert session.commits == 2


@pytest.mark.asyncio
async def test_cooldown_is_committed_as_suppressed(monkeypatch):
    note = Notification(
        incident_id=uuid4(),
        channel="feishu",
        target="chat-1",
        status="pending",
        attempts=0,
        payload={"kind": "diagnosis"},
        dedupe_key="suppressed-test",
    )
    session = FakeSession([note])
    sender = FeishuOutboxSender(session)
    sender.s = settings("chat-1")

    async def in_cooldown(*args):
        return True

    monkeypatch.setattr(sender, "_in_cooldown", in_cooldown)
    assert await sender.send_pending() == 0
    assert note.status == "suppressed"
    assert session.commits == 2


@pytest.mark.asyncio
async def test_recovery_is_replied_in_the_original_incident_thread(monkeypatch):
    note = Notification(
        incident_id=uuid4(),
        channel="feishu",
        target="default",
        status="pending",
        attempts=0,
        payload={"kind": "resolved", "severity": "info", "text": "recovered"},
        dedupe_key="resolved-thread-test",
    )
    session = FakeSession([note])
    sender = FeishuOutboxSender(session)
    sender.s = settings("chat-1")
    calls = []

    async def not_cooling_down(*args):
        return False

    async def thread_id(*args):
        return "om-initial-alert"

    async def reply(message_id, text, severity, title):
        calls.append((message_id, text, severity, title))
        return "om-recovery"

    monkeypatch.setattr(sender, "_in_cooldown", not_cooling_down)
    monkeypatch.setattr(sender, "_incident_thread_message_id", thread_id)
    sender.client = SimpleNamespace(reply_incident_card=reply)

    assert await sender.send_pending() == 1
    assert calls == [("om-initial-alert", "recovered", "info", "恢复通知")]
    assert note.payload["root_id"] == "om-initial-alert"
