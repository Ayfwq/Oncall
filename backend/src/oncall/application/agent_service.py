from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from oncall.agent.graph import OncallGraphRuntime
from oncall.agent.model_gateway import ModelServiceError, get_model_provider
from oncall.application.conversation_service import ConversationService
from oncall.application.memory_service import ConversationMemoryService
from oncall.domain.enums import AgentMode
from oncall.infrastructure.db.models import AgentRun, Conversation, Incident, IncidentEvidence, Message


class AgentService:
    def __init__(self, session: AsyncSession, checkpointer=None):
        self.session = session
        self.checkpointer = checkpointer

    async def run(
        self,
        conversation_id: UUID,
        user_message: str,
        channel: str = "web",
        mode: AgentMode | None = None,
        emit=None,
    ) -> dict:
        conv = await self.session.get(Conversation, conversation_id)
        if not conv:
            raise KeyError(conversation_id)
        incident_id = conv.incident_id
        if mode is None:
            mode = AgentMode.FOLLOW_UP if conv.incident_id else AgentMode.CHAT
        if conv.incident_id and mode is AgentMode.INVESTIGATE:
            inc = await self.session.get(Incident, conv.incident_id)
            if inc and inc.status not in ("resolved", "failed"):
                inc.status = "investigating"
                await self.session.commit()
        # Monitor-triggered investigation is an internal event, not a fake user turn.
        # Persist real Web/Feishu messages only; the resulting diagnosis is persisted
        # as the first visible assistant message in an Incident conversation.
        if not (channel == "monitor" and mode is AgentMode.INVESTIGATE):
            await ConversationService(self.session).add_message(
                conversation_id, "user", user_message, channel=channel
            )
        run = AgentRun(
            mode=mode.value,
            conversation_id=conversation_id,
            incident_id=conv.incident_id,
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
            "incident_id": str(conv.incident_id) if conv.incident_id else None,
            "project_id": str(conv.project_id) if conv.project_id else None,
            "user_message": user_message,
            "called_tools": [],
            "evidence": [],
            "knowledge_refs": [],
        }
        config = {"configurable": {"thread_id": str(conversation_id)}}
        try:
            model = get_model_provider()
            graph = OncallGraphRuntime(self.session, model=model, emit=emit).build(self.checkpointer)
            result = await graph.ainvoke(initial, config=config)
            # Keep full raw history in PostgreSQL, but compact old turns for future
            # model context once a conversation becomes long.
            await ConversationMemoryService(self.session, model).compact_if_needed(conversation_id)
            if incident_id:
                incident = await self.session.get(Incident, incident_id)
                notice = "自动诊断失败：大模型服务异常。"
                if incident and notice in incident.summary:
                    incident.summary = incident.summary.replace("；" + notice, "").replace(notice, "").strip()
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
