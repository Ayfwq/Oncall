from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    String,
    Text,
    UniqueConstraint,
    Uuid,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base


def uid() -> uuid.UUID:
    return uuid.uuid4()


def now() -> datetime:
    return datetime.now().astimezone()


class User(Base):
    __tablename__ = 'users'
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uid)
    username: Mapped[str] = mapped_column(String(120), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class Session(Base):
    __tablename__ = 'sessions'
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uid)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey('users.id', ondelete='CASCADE'), index=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class Project(Base):
    __tablename__ = 'projects'
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uid)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey('users.id', ondelete='CASCADE'), index=True)
    name: Mapped[str] = mapped_column(String(200))
    description: Mapped[str] = mapped_column(Text, default='')
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    timezone: Mapped[str] = mapped_column(String(80), default='Asia/Singapore')
    poll_interval: Mapped[int] = mapped_column(Integer, default=300)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, onupdate=now)


class ProjectProcessTarget(Base):
    __tablename__ = 'project_process_targets'
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uid)
    project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey('projects.id', ondelete='CASCADE'), index=True)
    name: Mapped[str] = mapped_column(String(200), default='target')
    executable: Mapped[str | None] = mapped_column(String(255), nullable=True)
    cmdline_filters: Mapped[list[str]] = mapped_column(JSONB, default=list)
    cwd: Mapped[str | None] = mapped_column(Text, nullable=True)
    port: Mapped[int | None] = mapped_column(Integer, nullable=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)


class ProjectLogSource(Base):
    __tablename__ = 'project_log_sources'
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uid)
    project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey('projects.id', ondelete='CASCADE'), index=True)
    path: Mapped[str] = mapped_column(Text)
    encoding: Mapped[str] = mapped_column(String(40), default='utf-8')
    parser_config: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)


class ProjectDockerTarget(Base):
    __tablename__ = 'project_docker_targets'
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uid)
    project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey('projects.id', ondelete='CASCADE'), index=True)
    container_ref: Mapped[str] = mapped_column(String(255))
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)


class ProjectDatabaseProfile(Base):
    __tablename__ = 'project_database_profiles'
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uid)
    project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey('projects.id', ondelete='CASCADE'), index=True)
    type: Mapped[str] = mapped_column(String(40), default='postgresql')
    host: Mapped[str] = mapped_column(String(255), default='127.0.0.1')
    port: Mapped[int] = mapped_column(Integer, default=5432)
    database: Mapped[str] = mapped_column(String(255))
    username: Mapped[str] = mapped_column(String(255))
    encrypted_password: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)
    sslmode: Mapped[str] = mapped_column(String(40), default='prefer')
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)


class ProjectServiceEndpoint(Base):
    __tablename__ = 'project_service_endpoints'
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uid)
    project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey('projects.id', ondelete='CASCADE'), index=True)
    name: Mapped[str] = mapped_column(String(200), default='health')
    url: Mapped[str] = mapped_column(Text)
    method: Mapped[str] = mapped_column(String(20), default='GET')
    expected_status: Mapped[int] = mapped_column(Integer, default=200)
    timeout_ms: Mapped[int] = mapped_column(Integer, default=3000)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)


class MonitoringRule(Base):
    __tablename__ = 'monitoring_rules'
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uid)
    project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey('projects.id', ondelete='CASCADE'), index=True)
    metric_key: Mapped[str] = mapped_column(String(160), index=True)
    resource_key: Mapped[str] = mapped_column(String(200), default='default')
    operator: Mapped[str] = mapped_column(String(8), default='>')
    trigger_threshold: Mapped[float] = mapped_column(Float)
    trigger_for: Mapped[int] = mapped_column(Integer, default=2)
    recovery_threshold: Mapped[float] = mapped_column(Float)
    recovery_for: Mapped[int] = mapped_column(Integer, default=2)
    severity: Mapped[str] = mapped_column(String(20), default='warning')
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)


class MonitoringRuleState(Base):
    __tablename__ = 'monitoring_rule_states'
    rule_id: Mapped[uuid.UUID] = mapped_column(ForeignKey('monitoring_rules.id', ondelete='CASCADE'), primary_key=True)
    state: Mapped[str] = mapped_column(String(20), default='normal')
    abnormal_hits: Mapped[int] = mapped_column(Integer, default=0)
    recovery_hits: Mapped[int] = mapped_column(Integer, default=0)
    last_value: Mapped[float | None] = mapped_column(Float, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, onupdate=now)


class MonitoringRun(Base):
    __tablename__ = 'monitoring_runs'
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uid)
    project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey('projects.id', ondelete='CASCADE'), index=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    status: Mapped[str] = mapped_column(String(30), default='running')
    collector_status: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    snapshot: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)


class MetricSample(Base):
    __tablename__ = 'metric_samples'
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey('projects.id', ondelete='CASCADE'), index=True)
    metric_key: Mapped[str] = mapped_column(String(160), index=True)
    resource_key: Mapped[str] = mapped_column(String(200), default='default', index=True)
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, index=True)
    value: Mapped[float] = mapped_column(Float)
    __table_args__ = (Index('ix_metric_project_key_ts', 'project_id', 'metric_key', 'ts'),)


class LogCursor(Base):
    __tablename__ = 'log_cursors'
    source_id: Mapped[uuid.UUID] = mapped_column(ForeignKey('project_log_sources.id', ondelete='CASCADE'), primary_key=True)
    file_identity: Mapped[str] = mapped_column(String(255), default='')
    offset: Mapped[int] = mapped_column(Integer, default=0)
    size: Mapped[int] = mapped_column(Integer, default=0)
    mtime: Mapped[float] = mapped_column(Float, default=0)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, onupdate=now)


class AlertEvent(Base):
    __tablename__ = 'alert_events'
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uid)
    rule_id: Mapped[uuid.UUID] = mapped_column(ForeignKey('monitoring_rules.id', ondelete='CASCADE'), index=True)
    project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey('projects.id', ondelete='CASCADE'), index=True)
    resource_key: Mapped[str] = mapped_column(String(200), default='default')
    state_from: Mapped[str] = mapped_column(String(20))
    state_to: Mapped[str] = mapped_column(String(20))
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, index=True)


class Incident(Base):
    __tablename__ = 'incidents'
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uid)
    project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey('projects.id', ondelete='CASCADE'), index=True)
    fingerprint: Mapped[str] = mapped_column(String(64), index=True)
    status: Mapped[str] = mapped_column(String(30), default='open', index=True)
    severity: Mapped[str] = mapped_column(String(20), default='warning')
    anomaly_type: Mapped[str] = mapped_column(String(160))
    resource_key: Mapped[str] = mapped_column(String(200), default='default')
    summary: Mapped[str] = mapped_column(Text, default='')
    first_seen: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    last_seen: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_investigated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    __table_args__ = (Index('ix_incident_open_fingerprint', 'project_id', 'fingerprint', 'status'),)


class IncidentEvidence(Base):
    __tablename__ = 'incident_evidence'
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uid)
    incident_id: Mapped[uuid.UUID] = mapped_column(ForeignKey('incidents.id', ondelete='CASCADE'), index=True)
    type: Mapped[str] = mapped_column(String(80))
    source: Mapped[str] = mapped_column(String(160))
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    summary: Mapped[str] = mapped_column(Text)
    data: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    raw_ref: Mapped[str | None] = mapped_column(Text, nullable=True)


class Conversation(Base):
    __tablename__ = 'conversations'
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uid)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey('users.id', ondelete='CASCADE'), index=True)
    project_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey('projects.id', ondelete='SET NULL'), nullable=True, index=True)
    incident_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey('incidents.id', ondelete='SET NULL'), nullable=True, index=True)
    type: Mapped[str] = mapped_column(String(30), default='chat')
    title: Mapped[str] = mapped_column(String(240), default='新会话')
    archived: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, onupdate=now, index=True)


class Message(Base):
    __tablename__ = 'messages'
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uid)
    conversation_id: Mapped[uuid.UUID] = mapped_column(ForeignKey('conversations.id', ondelete='CASCADE'), index=True)
    role: Mapped[str] = mapped_column(String(30))
    content: Mapped[str] = mapped_column(Text)
    channel: Mapped[str] = mapped_column(String(30), default='web')
    status: Mapped[str] = mapped_column(String(30), default='completed')
    metadata_json: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, index=True)


class ConversationSummary(Base):
    __tablename__ = 'conversation_summaries'
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uid)
    conversation_id: Mapped[uuid.UUID] = mapped_column(ForeignKey('conversations.id', ondelete='CASCADE'), index=True)
    through_message_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey('messages.id', ondelete='SET NULL'), nullable=True)
    summary: Mapped[str] = mapped_column(Text)
    token_estimate: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class AgentRun(Base):
    __tablename__ = 'agent_runs'
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uid)
    mode: Mapped[str] = mapped_column(String(30))
    conversation_id: Mapped[uuid.UUID] = mapped_column(ForeignKey('conversations.id', ondelete='CASCADE'), index=True)
    incident_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey('incidents.id', ondelete='SET NULL'), nullable=True, index=True)
    status: Mapped[str] = mapped_column(String(30), default='running')
    model_profile: Mapped[str] = mapped_column(String(120), default='default')
    prompt_version: Mapped[str] = mapped_column(String(80), default='v1')
    usage: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class Diagnosis(Base):
    __tablename__ = 'diagnoses'
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uid)
    incident_id: Mapped[uuid.UUID] = mapped_column(ForeignKey('incidents.id', ondelete='CASCADE'), index=True)
    agent_run_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey('agent_runs.id', ondelete='SET NULL'), nullable=True)
    structured_json: Mapped[dict[str, Any]] = mapped_column(JSONB)
    confidence: Mapped[float] = mapped_column(Float, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class ToolRun(Base):
    __tablename__ = 'tool_runs'
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uid)
    agent_run_id: Mapped[uuid.UUID] = mapped_column(ForeignKey('agent_runs.id', ondelete='CASCADE'), index=True)
    tool_name: Mapped[str] = mapped_column(String(160), index=True)
    params_hash: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(30))
    summary: Mapped[str] = mapped_column(Text, default='')
    latency_ms: Mapped[float] = mapped_column(Float, default=0)
    result_size: Mapped[int] = mapped_column(Integer, default=0)
    truncated: Mapped[bool] = mapped_column(Boolean, default=False)
    error_code: Mapped[str | None] = mapped_column(String(80), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class KnowledgeDocument(Base):
    __tablename__ = 'knowledge_documents'
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uid)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey('users.id', ondelete='CASCADE'), index=True)
    project_scope: Mapped[uuid.UUID | None] = mapped_column(ForeignKey('projects.id', ondelete='SET NULL'), nullable=True, index=True)
    title: Mapped[str] = mapped_column(String(300))
    status: Mapped[str] = mapped_column(String(30), default='uploaded')
    active_version_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, onupdate=now)


class KnowledgeDocumentVersion(Base):
    __tablename__ = 'knowledge_document_versions'
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uid)
    document_id: Mapped[uuid.UUID] = mapped_column(ForeignKey('knowledge_documents.id', ondelete='CASCADE'), index=True)
    checksum: Mapped[str] = mapped_column(String(64), index=True)
    parser_version: Mapped[str] = mapped_column(String(80), default='docling')
    original_filename: Mapped[str] = mapped_column(String(300))
    raw_path: Mapped[str] = mapped_column(Text)
    canonical_json_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    canonical_md_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(30), default='uploaded')
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    __table_args__ = (UniqueConstraint('document_id', 'checksum', name='uq_doc_checksum'),)


class KnowledgeChunk(Base):
    __tablename__ = 'knowledge_chunks'
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uid)
    version_id: Mapped[uuid.UUID] = mapped_column(ForeignKey('knowledge_document_versions.id', ondelete='CASCADE'), index=True)
    chunk_index: Mapped[int] = mapped_column(Integer)
    heading_path: Mapped[list[str]] = mapped_column(JSONB, default=list)
    page_range: Mapped[str | None] = mapped_column(String(80), nullable=True)
    content: Mapped[str] = mapped_column(Text)
    metadata_json: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    __table_args__ = (UniqueConstraint('version_id', 'chunk_index', name='uq_version_chunk_index'),)


class BackgroundJob(Base):
    __tablename__ = 'background_jobs'
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uid)
    type: Mapped[str] = mapped_column(String(100), index=True)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    status: Mapped[str] = mapped_column(String(30), default='pending', index=True)
    priority: Mapped[int] = mapped_column(Integer, default=100)
    available_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, index=True)
    lease_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, index=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, default=5)
    idempotency_key: Mapped[str | None] = mapped_column(String(255), nullable=True, unique=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, onupdate=now)


class Notification(Base):
    __tablename__ = 'notifications'
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uid)
    incident_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey('incidents.id', ondelete='CASCADE'), nullable=True, index=True)
    channel: Mapped[str] = mapped_column(String(40))
    target: Mapped[str] = mapped_column(String(255))
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    status: Mapped[str] = mapped_column(String(30), default='pending')
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, default=5)
    available_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, index=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    dedupe_key: Mapped[str] = mapped_column(String(255), unique=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, onupdate=now)


class ChannelBinding(Base):
    __tablename__ = 'channel_bindings'
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uid)
    channel: Mapped[str] = mapped_column(String(40))
    external_user: Mapped[str | None] = mapped_column(String(255), nullable=True)
    external_chat: Mapped[str | None] = mapped_column(String(255), nullable=True)
    conversation_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey('conversations.id', ondelete='SET NULL'), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    __table_args__ = (UniqueConstraint('channel', 'external_user', 'external_chat', name='uq_channel_binding'),)


class FeishuMessageLink(Base):
    __tablename__ = 'feishu_message_links'
    message_id: Mapped[str] = mapped_column(String(255), primary_key=True)
    root_id: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    chat_id: Mapped[str] = mapped_column(String(255), index=True)
    conversation_id: Mapped[uuid.UUID] = mapped_column(ForeignKey('conversations.id', ondelete='CASCADE'), index=True)
    incident_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey('incidents.id', ondelete='SET NULL'), nullable=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class ProcessedChannelEvent(Base):
    __tablename__ = 'processed_channel_events'
    event_key: Mapped[str] = mapped_column(String(255), primary_key=True)
    channel: Mapped[str] = mapped_column(String(40), default='feishu')
    status: Mapped[str] = mapped_column(String(30), default='processing', index=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, onupdate=now)


class RetrievalTrace(Base):
    __tablename__ = 'retrieval_traces'
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uid)
    agent_run_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey('agent_runs.id', ondelete='SET NULL'), nullable=True, index=True)
    project_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey('projects.id', ondelete='SET NULL'), nullable=True, index=True)
    query: Mapped[str] = mapped_column(Text)
    hit_count: Mapped[int] = mapped_column(Integer, default=0)
    refs: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, default=list)
    latency_ms: Mapped[float] = mapped_column(Float, default=0)
    status: Mapped[str] = mapped_column(String(30), default='ok')
    error_code: Mapped[str | None] = mapped_column(String(80), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, index=True)


class ModelProfile(Base):
    __tablename__ = 'model_profiles'
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uid)
    kind: Mapped[str] = mapped_column(String(40), index=True)
    name: Mapped[str] = mapped_column(String(120))
    provider: Mapped[str] = mapped_column(String(80))
    base_url: Mapped[str] = mapped_column(Text, default='')
    model: Mapped[str] = mapped_column(String(200))
    capabilities: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    encrypted_api_key: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
