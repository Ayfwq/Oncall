from __future__ import annotations

from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, Field

from .enums import AgentMode, IncidentStatus, Severity


class ToolResult(BaseModel):
    ok: bool
    summary: str
    data: Any = None
    observed_at: datetime = Field(default_factory=lambda: datetime.now().astimezone())
    truncated: bool = False
    error_code: str | None = None
    source_ref: str | None = None


class EvidenceItem(BaseModel):
    type: str
    source_tool: str
    observed_at: datetime
    summary: str
    data: Any = None
    source_ref: str | None = None


class CitationRef(BaseModel):
    document_id: str
    version_id: str | None = None
    chunk_id: str | None = None
    title: str | None = None
    page_range: str | None = None
    score: float | None = None


class DiagnosisReport(BaseModel):
    summary: str
    severity: Severity = Severity.WARNING
    affected_service: str | None = None
    symptoms: list[str] = Field(default_factory=list)
    evidence: list[str] = Field(default_factory=list)
    root_cause: str
    confidence: float = Field(ge=0, le=1)
    remediation: list[str] = Field(default_factory=list)
    verification: list[str] = Field(default_factory=list)
    risks: list[str] = Field(default_factory=list)
    knowledge_refs: list[CitationRef] = Field(default_factory=list)
    unknowns: list[str] = Field(default_factory=list)
    status: IncidentStatus = IncidentStatus.DIAGNOSED


class AgentDecision(BaseModel):
    action: Literal['tool', 'final']
    rationale: str = ''
    tool_name: str | None = None
    tool_args: dict[str, Any] = Field(default_factory=dict)
    answer: str | None = None
    diagnosis: DiagnosisReport | None = None


class AgentRunRequest(BaseModel):
    conversation_id: UUID
    project_id: UUID | None = None
    incident_id: UUID | None = None
    mode: AgentMode
    user_message: str
