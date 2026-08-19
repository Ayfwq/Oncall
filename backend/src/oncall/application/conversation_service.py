from __future__ import annotations

from datetime import datetime
from uuid import UUID
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from oncall.infrastructure.db.models import Conversation, ConversationSummary, Message


class ConversationService:
    def __init__(self, session: AsyncSession): self.session=session

    async def create(self, user_id: UUID, title: str='新会话', project_id: UUID|None=None, incident_id: UUID|None=None, type_: str='chat') -> Conversation:
        c=Conversation(user_id=user_id,title=title,project_id=project_id,incident_id=incident_id,type=type_)
        self.session.add(c); await self.session.commit(); await self.session.refresh(c); return c

    async def list(self, user_id: UUID, include_archived: bool=False, query: str|None=None) -> list[Conversation]:
        stmt=select(Conversation).where(Conversation.user_id==user_id)
        if not include_archived: stmt=stmt.where(Conversation.archived.is_(False))
        if query and query.strip(): stmt=stmt.where(Conversation.title.ilike(f'%{query.strip()}%'))
        return list((await self.session.scalars(stmt.order_by(Conversation.updated_at.desc()))).all())

    async def get(self, conversation_id: UUID, user_id: UUID|None=None) -> Conversation|None:
        stmt=select(Conversation).where(Conversation.id==conversation_id)
        if user_id: stmt=stmt.where(Conversation.user_id==user_id)
        return await self.session.scalar(stmt)

    async def messages(self, conversation_id: UUID, limit: int=500) -> list[Message]:
        stmt=select(Message).where(Message.conversation_id==conversation_id).order_by(Message.created_at.asc()).limit(limit)
        return list((await self.session.scalars(stmt)).all())

    async def recent_messages(self, conversation_id: UUID, limit: int=30) -> list[Message]:
        stmt=select(Message).where(Message.conversation_id==conversation_id).order_by(Message.created_at.desc()).limit(limit)
        rows=list((await self.session.scalars(stmt)).all()); rows.reverse(); return rows

    async def latest_summary(self, conversation_id: UUID) -> ConversationSummary|None:
        stmt=select(ConversationSummary).where(ConversationSummary.conversation_id==conversation_id).order_by(ConversationSummary.created_at.desc()).limit(1)
        return await self.session.scalar(stmt)

    async def add_message(self, conversation_id: UUID, role: str, content: str, channel: str='web', metadata: dict|None=None) -> Message:
        m=Message(conversation_id=conversation_id,role=role,content=content,channel=channel,metadata_json=metadata or {})
        self.session.add(m)
        c=await self.session.get(Conversation,conversation_id)
        if c: c.updated_at=datetime.now().astimezone()
        await self.session.commit(); await self.session.refresh(m); return m

    async def patch(self, conversation_id: UUID, user_id: UUID, title: str|None=None, archived: bool|None=None) -> Conversation|None:
        c=await self.get(conversation_id,user_id)
        if not c:return None
        if title is not None:c.title=title
        if archived is not None:c.archived=archived
        await self.session.commit();await self.session.refresh(c);return c

    async def delete(self, conversation_id: UUID, user_id: UUID) -> bool:
        c=await self.get(conversation_id,user_id)
        if not c:return False
        await self.session.delete(c);await self.session.commit();return True
