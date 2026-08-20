from __future__ import annotations

from typing import Any, TypedDict


class OncallState(TypedDict, total=False):
    run_id: str
    mode: str
    conversation_id: str
    incident_id: str | None
    project_id: str | None
    user_message: str
    channel: str
    intent: str
    route_confidence: float
    route_reason: str
    is_ops_related: bool
    requires_knowledge: bool
    requires_project: bool
    requires_incident: bool
    requires_realtime: bool
    clarification_question: str | None
    working_messages: list[dict[str, str]]
    conversation_summary: str | None
    project_context: dict[str, Any] | None
    previous_diagnosis: dict[str, Any] | None
    incident_context: dict[str, Any] | None
    evidence: list[dict[str, Any]]
    hypotheses: list[str]
    tool_calls_used: int
    tool_budget: int
    reason_loops: int
    reason_loop_budget: int
    called_tools: list[str]
    decision: dict[str, Any]
    pending_tool: dict[str, Any] | None
    current_tool_result: dict[str, Any] | None
    knowledge_refs: list[dict[str, Any]]
    knowledge_query: str | None
    knowledge_status: str
    knowledge_hits: list[dict[str, Any]]
    allowed_tools: list[str]
    tool_plan: list[dict[str, Any]]
    answer_sources: list[dict[str, Any]]
    diagnosis: dict[str, Any] | None
    final_response: str | None
    exhausted: bool
