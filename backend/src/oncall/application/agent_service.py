from __future__ import annotations

import json
import logging
from datetime import datetime
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from oncall.agent.graph import OncallGraphRuntime
from oncall.agent.model_gateway import ModelProvider, ModelServiceError, get_model_provider
from oncall.agent.prompts import DECISION_SCHEMA, STREAM_ANSWER_PROMPT, SYSTEM_PROMPT
from oncall.application.conversation_service import ConversationService
from oncall.application.long_term_memory import LongTermMemoryService, explicit_fact
from oncall.application.memory_policy import count_tokens, message_tokens
from oncall.bootstrap.config import get_settings
from oncall.domain.enums import AgentMode
from oncall.infrastructure.db.models import (
    AgentRun,
    Conversation,
    Incident,
    IncidentEvidence,
    Message,
    Project,
)
from oncall.jobs.queue import JobQueue

logger = logging.getLogger(__name__)


class AgentService:
    def __init__(self, session: AsyncSession, checkpointer=None, *, model: ModelProvider | None = None):
        self.session = session
        self.checkpointer = checkpointer
        self.model = model

    async def run(
        self,
        conversation_id: UUID,
        user_message: str,
        channel: str = "web",
        mode: AgentMode | None = None,
        emit=None,
        incident_id_override: UUID | None = None,
        force_notification: bool = False,
    ) -> dict:
        conv = await self.session.get(Conversation, conversation_id)
        if not conv:
            raise KeyError(conversation_id)
        incident_id = incident_id_override or conv.incident_id
        incident_project_id = conv.project_id
        if incident_id_override:
            incident = await self.session.get(Incident, incident_id_override)
            project = await self.session.get(Project, incident.project_id) if incident else None
            if not project or project.user_id != conv.user_id:
                raise KeyError(incident_id_override)
            incident_project_id = project.id
        if mode is None:
            mode = AgentMode.FOLLOW_UP if incident_id else AgentMode.CHAT
        if incident_id and mode is AgentMode.INVESTIGATE:
            inc = await self.session.get(Incident, incident_id)
            if inc and inc.status not in ("resolved", "failed"):
                inc.status = "investigating"
                await self.session.commit()
        # Monitor-triggered investigation is an internal event, not a fake user turn.
        # Persist real Web/Feishu messages only; the resulting diagnosis is persisted
        # as the first visible assistant message in an Incident conversation.
        if not (channel == "monitor" and mode is AgentMode.INVESTIGATE):
            message = await ConversationService(self.session).add_message(
                conversation_id,
                "user",
                user_message,
                channel=channel,
                metadata={"incident_id": str(incident_id)} if incident_id else None,
            )
            fact = explicit_fact(user_message) if channel == "web" else None
            if channel == "web" and user_message.lstrip().startswith(("记住", "请记住")):
                if fact:
                    saved = await LongTermMemoryService(self.session).remember(
                        conv.user_id,
                        fact,
                        project_id=conv.project_id
                        if any(x in fact for x in ("本项目", "这个项目"))
                        else None,
                        source_message_id=message.id,
                    )
                    reply = f"已保存长期记忆：{saved.content}"
                else:
                    reply = "这条记忆为空、过长或包含敏感凭据，未保存。请改写后重试。"
                await ConversationService(self.session).add_message(
                    conversation_id, "assistant", reply, channel=channel
                )
                return {"final_response": reply}
        run = AgentRun(
            mode=mode.value,
            conversation_id=conversation_id,
            incident_id=incident_id,
            status="running",
            model_profile="default",
            prompt_version="current",
        )
        self.session.add(run)
        await self.session.commit()
        await self.session.refresh(run)
        initial = {
            "run_id": str(run.id),
            "mode": mode.value,
            "channel": channel,
            "conversation_id": str(conversation_id),
            "incident_id": str(incident_id) if incident_id else None,
            "project_id": str(incident_project_id) if incident_project_id else None,
            "user_message": user_message,
            "called_tools": [],
            "evidence": [],
            "knowledge_refs": [],
            "force_notification": force_notification,
        }
        thread_id = (
            f"{conversation_id}:incident:{incident_id}"
            if conv.type == "ops" and incident_id
            else str(conversation_id)
        )
        config = {"configurable": {"thread_id": thread_id}}
        try:
            model = self.model or get_model_provider()
            runtime = OncallGraphRuntime(self.session, model=model, emit=emit)
            graph = runtime.build(self.checkpointer)
            result = await graph.ainvoke(initial, config=config)
            # Queue compaction durably. The model call happens in the worker,
            # after this answer has already been persisted and returned.
            try:
                model_context = runtime._context(result)
                context_tokens = (
                    count_tokens(json.dumps(model_context, ensure_ascii=False, default=str))
                    + max(
                        count_tokens(SYSTEM_PROMPT + DECISION_SCHEMA),
                        count_tokens(STREAM_ANSWER_PROMPT),
                    )
                    + message_tokens("assistant", result.get("final_response") or "")
                )
                if context_tokens >= get_settings().memory_compact_at_tokens:
                    await JobQueue(self.session).enqueue(
                        "memory_compact",
                        {"conversation_id": str(conversation_id)},
                        idempotency_key=f"memory-compact:{run.id}",
                        priority=200,
                    )
            except Exception:
                await self.session.rollback()
                logger.exception(
                    "conversation compaction enqueue failed",
                    extra={"conversation_id": str(conversation_id)},
                )
            if incident_id:
                incident = await self.session.get(Incident, incident_id)
                notice = "自动诊断失败：大模型服务异常。"
                if incident and notice in incident.summary:
                    incident.summary = (
                        incident.summary.replace("；" + notice, "").replace(notice, "").strip()
                    )
                    await self.session.commit()
            return result
        except Exception as exc:
            await self.session.rollback()
            try:
                run = await self.session.get(AgentRun, run.id)
                if run:
                    run.status = "failed"
                    run.finished_at = datetime.now().astimezone()
                if isinstance(exc, ModelServiceError):
                    self.session.add(
                        Message(
                            conversation_id=conversation_id,
                            role="assistant",
                            content=str(exc),
                            channel=channel,
                            status="failed",
                        )
                    )
                if isinstance(exc, ModelServiceError) and incident_id:
                    incident = await self.session.get(Incident, incident_id)
                    if incident and incident.status not in {"resolved", "failed"}:
                        notice = "自动诊断失败：大模型服务异常。"
                        if notice not in incident.summary:
                            incident.summary = f"{incident.summary.rstrip('；。')}；{notice}"
                        if incident.status == "investigating":
                            incident.status = "open"
                        self.session.add(
                            IncidentEvidence(
                                incident_id=incident.id,
                                type="model_service_error",
                                source="llm",
                                summary=str(exc),
                                data={"category": exc.category},
                            )
                        )
                await self.session.commit()
            except Exception:
                await self.session.rollback()
            raise
