"""One Web-visible operations conversation per user and Feishu chat."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from oncall.infrastructure.db.models import (
    ChannelBinding,
    Conversation,
    Incident,
    Message,
    Notification,
    Project,
    User,
)


class OpsConversationService:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def get_or_create(self, user_id: UUID) -> Conversation:
        # A user row lock makes concurrent alert webhooks and Feishu events
        # converge on the same conversation without committing the caller's work.
        user = await self.session.scalar(select(User).where(User.id == user_id).with_for_update())
        if user is None:
            raise KeyError(user_id)
        conv = await self.session.scalar(
            select(Conversation)
            .where(Conversation.user_id == user_id, Conversation.type == "ops")
            .order_by(Conversation.created_at.asc())
            .limit(1)
        )
        if conv:
            if conv.archived:
                conv.archived = False
            return conv
        # Keep the original Feishu chat transcript when upgrading an existing
        # installation. Incident investigation threads remain stored separately.
        conv = await self.session.scalar(
            select(Conversation)
            .join(ChannelBinding, ChannelBinding.conversation_id == Conversation.id)
            .where(
                Conversation.user_id == user_id,
                Conversation.type == "chat",
                ChannelBinding.channel == "feishu",
            )
            .order_by(Conversation.created_at.asc())
            .limit(1)
        )
        if conv:
            conv.type = "ops"
            conv.title = "运维主会话 · 飞书同步"
            conv.archived = False
            await self.session.flush()
            return conv
        conv = Conversation(user_id=user_id, title="运维主会话 · 飞书同步", type="ops")
        self.session.add(conv)
        await self.session.flush()
        return conv

    async def record_notification(self, user_id: UUID, notification: Notification) -> Message:
        conv = await self.get_or_create(user_id)
        await self.session.flush()
        existing = await self.session.scalar(
            select(Message)
            .where(
                Message.conversation_id == conv.id,
                Message.metadata_json["notification_id"].astext == str(notification.id),
            )
            .limit(1)
        )
        if existing:
            return existing
        notification.payload = {**notification.payload, "conversation_id": str(conv.id)}
        message = Message(
            conversation_id=conv.id,
            role="event",
            content=str(
                notification.payload.get("text")
                or notification.payload.get("summary")
                or "告警更新"
            ),
            channel="monitor",
            metadata_json={
                "notification_id": str(notification.id),
                "incident_id": str(notification.incident_id) if notification.incident_id else None,
                "kind": notification.payload.get("kind"),
            },
        )
        self.session.add(message)
        conv.updated_at = datetime.now().astimezone()
        await self.session.flush()
        return message

    async def backfill_notifications(self, user_id: UUID) -> int:
        """Bring existing alert history into the shared conversation once on boot."""
        conv = await self.get_or_create(user_id)
        existing_ids = set(
            (
                await self.session.scalars(
                    select(Message.metadata_json["notification_id"].astext).where(
                        Message.conversation_id == conv.id, Message.role == "event"
                    )
                )
            ).all()
        )
        notices = list(
            (
                await self.session.scalars(
                    select(Notification)
                    .join(Incident, Incident.id == Notification.incident_id)
                    .join(Project, Project.id == Incident.project_id)
                    .where(Project.user_id == user_id, Notification.channel == "feishu")
                    .order_by(Notification.created_at.asc())
                )
            ).all()
        )
        count = 0
        for notice in notices:
            if str(notice.id) not in existing_ids and notice.payload.get("kind") in {
                "triggered",
                "reopened",
                "diagnosis",
                "escalated",
                "resolved",
            }:
                await self.record_notification(user_id, notice)
                count += 1
        return count
