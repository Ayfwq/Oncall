from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace
from uuid import uuid4

import pytest

import oncall.application.incident_service as incident_module
from oncall.application.incident_service import IncidentService, _escalation_text
from oncall.infrastructure.db.models import Conversation, Incident, Notification


class CaptureSession:
    def __init__(self):
        self.added = []

    def add(self, value):
        self.added.append(value)


@pytest.mark.asyncio
async def test_incident_escalation_is_scheduled_at_two_and_five_hours(monkeypatch):
    monkeypatch.setattr(
        incident_module,
        "get_settings",
        lambda: SimpleNamespace(
            incident_reminder_first_seconds=7200,
            incident_reminder_final_seconds=18000,
        ),
    )
    session = CaptureSession()
    incident = Incident(
        id=uuid4(),
        severity="critical",
        anomaly_type="CpuHigh",
        summary="CPU 持续过高",
        occurrence_count=1,
    )
    conversation = Conversation(id=uuid4(), incident_id=incident.id)
    started = datetime.now().astimezone()

    await IncidentService(session)._schedule_escalations(
        incident, conversation, "测试项目", generation=1
    )

    rows = [x for x in session.added if isinstance(x, Notification)]
    assert [x.payload["stage"] for x in rows] == ["reminder_2h", "final_5h"]
    assert [x.dedupe_key for x in rows] == [
        f"incident:{incident.id}:escalated:1:reminder_2h",
        f"incident:{incident.id}:escalated:1:final_5h",
    ]
    assert 7100 <= (rows[0].available_at - started).total_seconds() <= 7300
    assert 17900 <= (rows[1].available_at - started).total_seconds() <= 18100


def test_final_escalation_message_is_explicit():
    incident = Incident(
        id=uuid4(),
        severity="warning",
        anomaly_type="DatabaseLongTransaction",
        summary="数据库存在长事务",
        occurrence_count=3,
    )

    text = _escalation_text("测试项目", incident, "final_5h")

    assert "最后升级提醒" in text
    assert "最后一次自动推送" in text
    assert "本轮发生次数**：3" in text
