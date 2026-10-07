from datetime import UTC, datetime
from uuid import uuid4

from oncall.application.incident_service import _notification_text


def test_alert_notification_shows_alertmanager_start_time_in_beijing_time():
    text = _notification_text(
        "测试项目",
        {
            "labels": {"alertname": "DiskHigh", "severity": "warning"},
            "annotations": {"summary": "磁盘空间不足"},
            "startsAt": "2026-10-01T04:30:00Z",
        },
        uuid4(),
    )

    assert "**发生时间**：2026-10-01 12:30:00（北京时间）" in text


def test_alert_notification_marks_fallback_as_system_record_time():
    text = _notification_text(
        "测试项目",
        {"labels": {"alertname": "DiskHigh"}},
        uuid4(),
        datetime(2026, 10, 1, 4, 30, tzinfo=UTC),
    )

    assert "**系统记录时间**：2026-10-01 12:30:00（北京时间）" in text
