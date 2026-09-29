"""Chinese display labels for alert fields; stored status codes stay unchanged."""

from __future__ import annotations

import re

SEVERITY_LABELS = {
    "critical": "严重",
    "warning": "警告",
    "info": "提示",
}

STATUS_LABELS = {
    "open": "待调查",
    "investigating": "调查中",
    "diagnosed": "已诊断",
    "resolved": "已恢复",
    "failed": "调查失败",
    "firing": "触发中",
    "pending": "待处理",
}

_DISPLAY_LABELS = {**SEVERITY_LABELS, **STATUS_LABELS}
_DISPLAY_WORDS = re.compile(
    r"(?<![\w])(?:critical|warning|info|open|investigating|diagnosed|resolved|failed|firing|pending)(?![\w])",
    re.IGNORECASE,
)


def severity_label(value: str) -> str:
    return SEVERITY_LABELS.get(value.lower(), value)


def status_label(value: str) -> str:
    return STATUS_LABELS.get(value.lower(), value)


def localize_alert_answer(text: str) -> str:
    """Normalize model-written display values without changing alert identifiers."""
    return _DISPLAY_WORDS.sub(lambda match: _DISPLAY_LABELS[match.group().lower()], text)
