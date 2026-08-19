from enum import StrEnum


class ConversationType(StrEnum):
    CHAT = 'chat'
    INCIDENT = 'incident'


class IncidentStatus(StrEnum):
    OPEN = 'open'
    INVESTIGATING = 'investigating'
    DIAGNOSED = 'diagnosed'
    RESOLVED = 'resolved'
    FAILED = 'failed'


class Severity(StrEnum):
    INFO = 'info'
    WARNING = 'warning'
    CRITICAL = 'critical'


class RuleState(StrEnum):
    NORMAL = 'normal'
    PENDING = 'pending'
    FIRING = 'firing'
    RECOVERING = 'recovering'


class AgentMode(StrEnum):
    CHAT = 'chat'
    INVESTIGATE = 'investigate'
    FOLLOW_UP = 'follow_up'
    DEEP = 'deep'


class JobStatus(StrEnum):
    PENDING = 'pending'
    RUNNING = 'running'
    DONE = 'done'
    FAILED = 'failed'
    DEAD = 'dead'


class DocumentStatus(StrEnum):
    UPLOADED = 'uploaded'
    PROCESSING = 'processing'
    READY = 'ready'
    FAILED = 'failed'


class ToolRisk(StrEnum):
    READ = 'read'
    WRITE = 'write'
    DANGEROUS = 'dangerous'
