from uuid import uuid4

import pytest
from oncall.agent.context_builder import ContextBuilder
from oncall.agent.tool_registry import ToolExecutionContext, ToolRegistry
from oncall.application.conversation_service import ConversationService
from oncall.application.ops_conversation import OpsConversationService
from oncall.channels.feishu_events import FeishuInboundMessage
from oncall.channels.feishu_gateway import FeishuGateway
from oncall.infrastructure.db.models import (
    AgentRun,
    FeishuMessageLink,
    Incident,
    Notification,
    Project,
    User,
)
from oncall.infrastructure.db.session import SessionFactory
from sqlalchemy import delete


@pytest.mark.integration
@pytest.mark.asyncio
async def test_alerts_share_one_visible_chat_and_followups_stay_scoped():
    user_id = uuid4()
    async with SessionFactory() as db:
        db.add(User(id=user_id, username=f"ops-test-{user_id}"))
        await db.commit()
        try:
            project = Project(user_id=user_id, name=f"project-{user_id}")
            db.add(project)
            await db.flush()
            incidents = [
                Incident(project_id=project.id, fingerprint=str(uuid4()), anomaly_type="cpu"),
                Incident(project_id=project.id, fingerprint=str(uuid4()), anomaly_type="memory"),
            ]
            db.add_all(incidents)
            await db.flush()
            ops_service = OpsConversationService(db)
            notices = []
            for index, incident in enumerate(incidents):
                notice = Notification(
                    incident_id=incident.id,
                    channel="feishu",
                    target="default",
                    payload={"kind": "triggered", "text": f"告警 {index}"},
                    dedupe_key=f"ops-test-{uuid4()}",
                )
                db.add(notice)
                notices.append(notice)
                await ops_service.record_notification(user_id, notice)
            await ops_service.record_notification(user_id, notices[0])
            await db.commit()
            assert await ops_service.backfill_notifications(user_id) == 0

            visible = await ConversationService(db).list(user_id)
            assert len(visible) == 1 and visible[0].type == "ops"
            ops = visible[0]
            assert (
                len(
                    [m for m in await ConversationService(db).messages(ops.id) if m.role == "event"]
                )
                == 2
            )
            assert all(n.payload["conversation_id"] == str(ops.id) for n in notices)

            chat = ConversationService(db)
            await chat.add_message(ops.id, "user", "普通问题")
            await chat.add_message(
                ops.id, "user", "CPU 怎么了", metadata={"incident_id": str(incidents[0].id)}
            )
            await chat.add_message(
                ops.id, "assistant", "CPU 分析", metadata={"incident_id": str(incidents[0].id)}
            )
            await chat.add_message(
                ops.id, "user", "内存怎么了", metadata={"incident_id": str(incidents[1].id)}
            )

            builder = ContextBuilder(db)
            general, _ = await builder._load_messages(ops.id, None)
            cpu, _ = await builder._load_messages(ops.id, incidents[0].id)
            assert [m.content for m in general] == ["普通问题"]
            assert [m.content for m in cpu] == ["CPU 怎么了", "CPU 分析"]

            db.add(
                FeishuMessageLink(
                    message_id="om-ops-test",
                    chat_id="oc-ops-test",
                    conversation_id=ops.id,
                    incident_id=incidents[0].id,
                )
            )
            await db.commit()
            inbound = FeishuInboundMessage(
                event_key="event-ops-test",
                message_id="om-reply-ops-test",
                chat_id="oc-ops-test",
                sender_id="ou-ops-test",
                text="这个告警为什么发生？",
                parent_id="om-ops-test",
            )
            assert (
                await FeishuGateway()._incident_for_message(db, inbound, user_id) == incidents[0].id
            )
            with pytest.raises(ValueError, match="不能删除"):
                await chat.delete(ops.id, user_id)
        finally:
            await db.rollback()
            await db.execute(delete(User).where(User.id == user_id))
            await db.commit()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_active_alert_query_is_scoped_to_current_user():
    own_id, other_id = uuid4(), uuid4()
    async with SessionFactory() as db:
        db.add_all([
            User(id=own_id, username=f"alerts-own-{own_id}"),
            User(id=other_id, username=f"alerts-other-{other_id}"),
        ])
        await db.flush()
        try:
            own_project = Project(user_id=own_id, name="own-project")
            other_project = Project(user_id=other_id, name="other-project")
            db.add_all([own_project, other_project])
            await db.flush()
            db.add_all([
                Incident(project_id=own_project.id, fingerprint=str(uuid4()), anomaly_type="own-open"),
                Incident(project_id=own_project.id, fingerprint=str(uuid4()), anomaly_type="own-resolved", status="resolved"),
                Incident(project_id=other_project.id, fingerprint=str(uuid4()), anomaly_type="other-open"),
            ])
            conv = await ConversationService(db).create(own_id, "运维主会话")
            run = AgentRun(mode="chat", conversation_id=conv.id)
            db.add(run)
            await db.commit()

            result = await ToolRegistry(db).execute(
                "query_active_alerts", {},
                ToolExecutionContext(project_id=None, incident_id=None, agent_run_id=run.id),
            )
            assert result.ok
            assert [row["alertname"] for row in result.data["alerts"]] == ["own-open"]
            assert result.data["alerts"][0]["severity"] == "警告"
            assert result.data["alerts"][0]["status"] == "待调查"
        finally:
            await db.rollback()
            await db.execute(delete(User).where(User.id.in_([own_id, other_id])))
            await db.commit()
