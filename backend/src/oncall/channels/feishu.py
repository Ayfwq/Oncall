from __future__ import annotations

import json
from datetime import datetime, timedelta

import httpx
from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from oncall.application.ops_conversation import OpsConversationService
from oncall.bootstrap.config import get_settings
from oncall.channels.notification_policy import is_cooldown_kind, retry_delay_seconds
from oncall.infrastructure.db.models import (
    ChannelBinding,
    Conversation,
    FeishuMessageLink,
    Incident,
    Notification,
    Project,
)


class FeishuClient:
    def __init__(self):
        self.s = get_settings()
        self._token = None
        self._token_expires_at = None

    async def tenant_token(self) -> str:
        now = datetime.now().astimezone()
        if self._token and self._token_expires_at and now < self._token_expires_at:
            return self._token
        async with httpx.AsyncClient(timeout=15) as c:
            r = await c.post(
                "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal",
                json={"app_id": self.s.feishu_app_id, "app_secret": self.s.feishu_app_secret},
            )
            r.raise_for_status()
            data = r.json()
            self._token = data["tenant_access_token"]
            ttl = max(60, int(data.get("expire", 7200)) - 120)
            self._token_expires_at = now + timedelta(seconds=ttl)
            return self._token

    async def _send(
        self, receive_id: str, msg_type: str, content: dict, receive_id_type: str = "chat_id"
    ) -> str:
        token = await self.tenant_token()
        async with httpx.AsyncClient(timeout=15) as c:
            r = await c.post(
                f"https://open.feishu.cn/open-apis/im/v1/messages?receive_id_type={receive_id_type}",
                headers={"Authorization": f"Bearer {token}"},
                json={
                    "receive_id": receive_id,
                    "msg_type": msg_type,
                    "content": json.dumps(content, ensure_ascii=False),
                },
            )
            r.raise_for_status()
            body = r.json()
            if body.get("code") not in (None, 0):
                raise RuntimeError(f"Feishu API error: {body.get('code')} {body.get('msg')}")
            return body.get("data", {}).get("message_id", "")

    async def _reply(self, message_id: str, msg_type: str, content: dict) -> str:
        token = await self.tenant_token()
        async with httpx.AsyncClient(timeout=15) as c:
            r = await c.post(
                f"https://open.feishu.cn/open-apis/im/v1/messages/{message_id}/reply",
                headers={"Authorization": f"Bearer {token}"},
                json={"msg_type": msg_type, "content": json.dumps(content, ensure_ascii=False)},
            )
            r.raise_for_status()
            body = r.json()
            if body.get("code") not in (None, 0):
                raise RuntimeError(f"Feishu API error: {body.get('code')} {body.get('msg')}")
            return body.get("data", {}).get("message_id", "")

    async def send_text(self, receive_id: str, text: str, receive_id_type: str = "chat_id") -> str:
        return await self._send(receive_id, "text", {"text": str(text)[:30000]}, receive_id_type)

    async def send_incident_card(
        self,
        receive_id: str,
        text: str,
        severity: str = "warning",
        receive_id_type: str = "chat_id",
    ) -> str:
        template = (
            "red" if severity == "critical" else "orange" if severity == "warning" else "blue"
        )
        text = str(text)[:18000]
        card = {
            "config": {"wide_screen_mode": True},
            "header": {
                "template": template,
                "title": {"tag": "plain_text", "content": "PulseOps Incident 监测报告"},
            },
            "elements": [
                {"tag": "markdown", "content": text},
                {
                    "tag": "note",
                    "elements": [
                        {"tag": "plain_text", "content": "可直接回复本消息，继续围绕本次告警追问"}
                    ],
                },
            ],
        }
        return await self._send(receive_id, "interactive", card, receive_id_type)

    async def reply_incident_card(
        self, message_id: str, text: str, severity: str = "warning", title: str = "诊断报告"
    ) -> str:
        template = (
            "red" if severity == "critical" else "orange" if severity == "warning" else "blue"
        )
        card = {
            "config": {"wide_screen_mode": True},
            "header": {
                "template": template,
                "title": {"tag": "plain_text", "content": f"PulseOps Incident {title}"},
            },
            "elements": [
                {"tag": "markdown", "content": str(text)[:18000]},
                {
                    "tag": "note",
                    "elements": [
                        {"tag": "plain_text", "content": "可直接回复本消息，继续围绕本次告警追问"}
                    ],
                },
            ],
        }
        return await self._reply(message_id, "interactive", card)

    async def send_alert_card(
        self,
        receive_id: str,
        text: str,
        severity: str = "warning",
        receive_id_type: str = "chat_id",
    ) -> str:
        """First-stage alert. Deliberately short: it must land within seconds of
        detection, long before the Agent finishes its investigation."""
        template = (
            "red" if severity == "critical" else "orange" if severity == "warning" else "blue"
        )
        label = {"critical": "严重", "warning": "警告", "info": "提示"}.get(severity, severity)
        card = {
            "config": {"wide_screen_mode": True},
            "header": {
                "template": template,
                "title": {"tag": "plain_text", "content": f"🚨 PulseOps 告警 · {label}"},
            },
            "elements": [
                {"tag": "markdown", "content": str(text)[:18000]},
                {
                    "tag": "note",
                    "elements": [
                        {
                            "tag": "plain_text",
                            "content": "正在自动调查原因；可直接回复本消息继续追问",
                        }
                    ],
                },
            ],
        }
        return await self._send(receive_id, "interactive", card, receive_id_type)


class FeishuOutboxSender:
    def __init__(self, session: AsyncSession):
        self.session = session
        self.client = FeishuClient()
        self.s = get_settings()

    async def _in_cooldown(self, n: Notification, now: datetime) -> bool:
        kind = n.payload.get("kind")
        if (
            not n.incident_id
            or not is_cooldown_kind(kind)
            or n.payload.get("force_delivery")
            or self.s.notification_cooldown_seconds <= 0
        ):
            return False
        cutoff = now - timedelta(seconds=self.s.notification_cooldown_seconds)
        recent = list(
            (
                await self.session.scalars(
                    select(Notification)
                    .where(
                        Notification.incident_id == n.incident_id,
                        Notification.channel == "feishu",
                        Notification.status == "sent",
                        Notification.sent_at >= cutoff,
                        Notification.id != n.id,
                    )
                    .order_by(Notification.sent_at.desc())
                    .limit(20)
                )
            ).all()
        )
        return any(x.payload.get("kind") == kind for x in recent)

    async def _incident_thread_message_id(self, n: Notification) -> str | None:
        if not n.incident_id:
            return None
        rows = list(
            (
                await self.session.scalars(
                    select(Notification)
                    .where(
                        Notification.incident_id == n.incident_id,
                        Notification.channel == "feishu",
                        Notification.status == "sent",
                        Notification.id != n.id,
                    )
                    .order_by(Notification.created_at.asc())
                    .limit(20)
                )
            ).all()
        )
        for row in rows:
            if row.payload.get("app_id") not in (None, self.s.feishu_app_id):
                continue
            if row.payload.get("kind") in {
                "triggered",
                "escalated",
                "reopened",
            } and row.payload.get("message_id"):
                return str(row.payload["message_id"])
        return None

    async def send_pending(self, limit: int = 20) -> int:
        if not self.s.feishu_enabled:
            return 0
        now = datetime.now().astimezone()
        stale_cutoff = now - timedelta(seconds=self.s.feishu_outbox_claim_seconds)
        rows = list(
            (
                await self.session.scalars(
                    select(Notification)
                    .where(
                        Notification.channel == "feishu",
                        Notification.available_at <= now,
                        or_(
                            Notification.status == "pending",
                            and_(
                                Notification.status == "sending",
                                Notification.updated_at <= stale_cutoff,
                            ),
                        ),
                    )
                    .order_by(Notification.created_at.asc())
                    .with_for_update(skip_locked=True)
                    .limit(limit)
                )
            ).all()
        )
        if not rows:
            return 0
        # Claim with the existing status/updated_at columns. The transaction
        # is committed before network I/O, so another worker cannot send the
        # same batch while this worker is waiting on Feishu.
        for n in rows:
            n.status = "sending"
            n.attempts += 1
        await self.session.commit()
        # Resolve the "default" push target once per outbox tick. If the
        # env default is set, use it (and honour the env receive_id_type,
        # which may be 'chat_id' or 'open_id'). Otherwise fall back to the
        # first bound Feishu chat. Later questions from other chats must not
        # silently redirect every proactive alert to a different recipient.
        default_target = self.s.feishu_default_receive_id
        if default_target:
            default_type = self.s.feishu_default_receive_id_type
        else:
            default_type = "chat_id"
            binding = await self.session.scalar(
                select(ChannelBinding)
                .where(ChannelBinding.channel == "feishu", ChannelBinding.external_chat.is_not(None))
                .order_by(ChannelBinding.created_at.asc(), ChannelBinding.id.asc())
                .limit(1)
            )
            if binding:
                default_target = binding.external_chat
        sent = 0
        for n in rows:
            if n.target == "default":
                target = default_target
                rid_type = default_type
            else:
                target = n.target
                # Non-default targets (e.g. inbound replies) carry their
                # own receive_id_type in the payload; default to chat_id.
                rid_type = str(n.payload.get("receive_id_type") or "chat_id")
            if not target:
                # Keep alerts queued until the first inbound message binds a
                # chat. Missing a target is setup state, not a delivery failure.
                n.status = "pending"
                n.attempts -= 1
                n.available_at = datetime.now().astimezone() + timedelta(seconds=15)
                n.last_error = "等待飞书会话绑定"
                await self.session.commit()
                continue
            kind = n.payload.get("kind")
            if kind in {"escalated", "reopened"} and n.incident_id:
                incident = await self.session.get(Incident, n.incident_id)
                if (
                    not incident
                    or incident.status == "resolved"
                    or incident.resolved_at is not None
                ):
                    n.status = "suppressed"
                    n.last_error = "incident resolved before notification"
                    await self.session.commit()
                    continue
            if await self._in_cooldown(n, now):
                n.status = "suppressed"
                n.last_error = "notification cooldown"
                await self.session.commit()
                continue
            try:
                text = n.payload.get("text") or f"PulseOps Incident: {n.payload.get('summary', '')}"
                severity = n.payload.get("severity", "warning")
                if kind in ("diagnosis", "escalated", "reopened", "resolved"):
                    thread_message_id = await self._incident_thread_message_id(n)
                    if thread_message_id:
                        title = {
                            "diagnosis": "诊断报告",
                            "escalated": "告警升级",
                            "reopened": "告警再次触发",
                            "resolved": "恢复通知",
                        }.get(kind, "进展更新")
                        message_id = await self.client.reply_incident_card(
                            thread_message_id, text, severity, title
                        )
                        n.payload = {**n.payload, "root_id": thread_message_id}
                    else:
                        message_id = await self.client.send_incident_card(
                            target, text, severity, rid_type
                        )
                elif kind == "triggered":
                    message_id = await self.client.send_alert_card(target, text, severity, rid_type)
                else:
                    message_id = await self.client.send_text(target, text, rid_type)
                n.status = "sent"
                n.sent_at = datetime.now().astimezone()
                n.last_error = None
                n.payload = {**n.payload, "message_id": message_id, "app_id": self.s.feishu_app_id}
                sent += 1
                # Trigger, diagnosis and recovery are recorded when queued.
                # An escalation is conditional, so record it only after send.
                if n.incident_id and kind == "escalated":
                    incident = await self.session.get(Incident, n.incident_id)
                    project = (
                        await self.session.get(Project, incident.project_id) if incident else None
                    )
                    if project:
                        await OpsConversationService(self.session).record_notification(
                            project.user_id, n
                        )
                if message_id:
                    conv = None
                    conversation_id = n.payload.get("conversation_id")
                    if conversation_id:
                        from uuid import UUID

                        try:
                            conv = await self.session.get(Conversation, UUID(str(conversation_id)))
                        except (ValueError, TypeError):
                            conv = None
                    elif n.incident_id:
                        conv = await self.session.scalar(
                            select(Conversation)
                            .where(Conversation.incident_id == n.incident_id)
                            .order_by(Conversation.created_at.asc())
                            .limit(1)
                        )
                    if conv and not await self.session.get(FeishuMessageLink, message_id):
                        self.session.add(
                            FeishuMessageLink(
                                message_id=message_id,
                                root_id=n.payload.get("root_id"),
                                chat_id=target,
                                conversation_id=conv.id,
                                incident_id=n.incident_id,
                            )
                        )
            except Exception as exc:
                n.last_error = str(exc)[:4000]
                if n.attempts >= n.max_attempts:
                    n.status = "dead"
                else:
                    n.status = "pending"
                    n.available_at = datetime.now().astimezone() + timedelta(
                        seconds=retry_delay_seconds(n.attempts)
                    )
            # Commit each row independently. A process crash after an API
            # response cannot roll back already completed rows in this batch.
            await self.session.commit()
        return sent


class FeishuWsListener:
    """Reconnectable Feishu listener that can be stopped when credentials change."""

    def __init__(self, on_message):
        import threading

        self.on_message = on_message
        self.stop_event = threading.Event()
        self.connected = False
        self.error: str | None = None
        self.thread = threading.Thread(target=self._run, name="feishu-ws", daemon=True)

    def start(self):
        self.thread.start()
        return self

    def stop(self) -> bool:
        self.stop_event.set()
        self.thread.join(timeout=8)
        return not self.thread.is_alive()

    def _run(self):
        import asyncio

        import lark_oapi as lark

        settings = get_settings()
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        # The SDK stores its loop at module level; only one listener may run
        # in this API process. Stop the old listener before starting another.
        lark.ws.client.loop = loop
        dispatcher = (
            lark.EventDispatcherHandler.builder("", "")
            .register_p2_im_message_receive_v1(self.on_message)
            .build()
        )
        client = lark.ws.Client(
            settings.feishu_app_id,
            settings.feishu_app_secret,
            event_handler=dispatcher,
            # The SDK's INFO connection line contains a signed WS URL.
            log_level=lark.LogLevel.WARNING,
            auto_reconnect=False,
        )

        async def serve():
            while not self.stop_event.is_set():
                ping = None
                try:
                    await client._connect()
                    self.connected = True
                    self.error = None
                    ping = asyncio.create_task(client._ping_loop())
                    while not self.stop_event.is_set() and client._conn is not None:
                        await asyncio.sleep(0.5)
                except Exception as exc:
                    self.error = str(exc)[:500]
                finally:
                    self.connected = False
                    if ping:
                        ping.cancel()
                        await asyncio.gather(ping, return_exceptions=True)
                    await client._disconnect()
                if not self.stop_event.is_set():
                    await asyncio.sleep(3)

        try:
            loop.run_until_complete(serve())
        except Exception as exc:
            self.error = str(exc)[:500]
        finally:
            pending = asyncio.all_tasks(loop)
            for task in pending:
                task.cancel()
            if pending:
                loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
            loop.close()


def start_ws_listener(on_message):
    if not get_settings().feishu_enabled:
        return None
    return FeishuWsListener(on_message).start()
