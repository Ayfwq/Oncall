from __future__ import annotations

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from oncall.agent.model_gateway import ModelProvider
from oncall.infrastructure.db.models import ConversationSummary, Message


class ConversationMemoryService:
    """Compact old conversation turns while keeping PostgreSQL as full history.

    Raw messages are never deleted. A rolling summary only controls what is sent to
    the LLM, so shutdown/restart and audit history remain lossless.
    """
    def __init__(self, session: AsyncSession, model: ModelProvider):
        self.session=session;self.model=model

    async def compact_if_needed(self,conversation_id:UUID,threshold:int=40,keep_recent:int=20)->ConversationSummary|None:
        rows=list((await self.session.scalars(select(Message).where(Message.conversation_id==conversation_id).order_by(Message.created_at.asc()))).all())
        if len(rows)<=threshold:return None
        latest=await self.session.scalar(select(ConversationSummary).where(ConversationSummary.conversation_id==conversation_id).order_by(ConversationSummary.created_at.desc()).limit(1))
        already_through=None
        if latest and latest.through_message_id:
            for i,m in enumerate(rows):
                if m.id==latest.through_message_id:already_through=i;break
        cutoff=max(0,len(rows)-keep_recent)
        start=(already_through+1) if already_through is not None else 0
        if cutoff<=start:return latest
        old=rows[start:cutoff]
        transcript='\n'.join(f'{m.role}: {m.content}' for m in old)
        seed=(latest.summary+'\n\n') if latest else ''
        prompt=(
            '请把以下 Oncall 运维会话压缩成可供后续 Agent 使用的事实摘要。保留：用户目标、项目/服务名称、'
            '已经确认的故障事实、执行过的检查、结论、未解决问题；不要编造。\n\n'+seed+transcript
        )
        summary_text=await self.model.summarize(prompt)
        through=old[-1]
        summary=ConversationSummary(conversation_id=conversation_id,through_message_id=through.id,summary=summary_text,token_estimate=max(1,len(summary_text)//4))
        self.session.add(summary);await self.session.commit();await self.session.refresh(summary);return summary
