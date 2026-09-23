from enum import StrEnum


class IncidentStatus(StrEnum):
    OPEN = "open"
    INVESTIGATING = "investigating"
    DIAGNOSED = "diagnosed"
    RESOLVED = "resolved"
    FAILED = "failed"


class Severity(StrEnum):
    INFO = "info"
    WARNING = "warning"
    CRITICAL = "critical"


class AgentMode(StrEnum):
    CHAT = "chat"
    INVESTIGATE = "investigate"
    FOLLOW_UP = "follow_up"
