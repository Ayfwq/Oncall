from __future__ import annotations

import asyncio

from sqlalchemy import or_, select
from sqlalchemy.exc import IntegrityError

from oncall.application.agent_service import AgentService
from oncall.application.conversation_service import ConversationService
from oncall.channels.feishu_events import FeishuInboundMessage, parse_lark_message
from oncall.infrastructure.db.models import (
    ChannelBinding,
    Conversation,
    FeishuMessageLink,
    Notification,
    ProcessedChannelEvent,
    User,
)
from oncall.infrastructure.db.session import SessionFactory


class FeishuGateway:
    def __init__(self, checkpointer=None):
        self.checkpointer = checkpointer

    async def _first_user(self, db) -> User:
        user = await db.scalar(select(User).order_by(User.created_at.asc()).limit(1))
        if not user:
            raise RuntimeError("Oncall has no local user. Run oncall-init-admin first.")
        return user

    async def _conversation_for_message(self, db, msg: FeishuInboundMessage) -> Conversation:
        anchors = [x for x in (msg.root_id, msg.parent_id) if x]
        if anchors:
            link = await db.scalar(
                select(FeishuMessageLink)
                .where(or_(FeishuMessageLink.message_id.in_(anchors),FeishuMessageLink.root_id.in_(anchors)))
                .order_by(FeishuMessageLink.created_at.desc()).limit(1)
            )
            if link:
                conv = await db.get(Conversation, link.conversation_id)
                if conv:return conv
        binding = await db.scalar(select(ChannelBinding).where(
            ChannelBinding.channel == "feishu",
            ChannelBinding.external_chat == msg.chat_id,
            ChannelBinding.external_user == msg.sender_id,
        ).limit(1))
        if binding and binding.conversation_id:
            conv=await db.get(Conversation,binding.conversation_id)
            if conv:return conv
        user=await self._first_user(db)
        conv=await ConversationService(db).create(user.id,title="飞书运维会话",type_="chat")
        if binding:binding.conversation_id=conv.id
        else:db.add(ChannelBinding(channel="feishu",external_user=msg.sender_id,external_chat=msg.chat_id,conversation_id=conv.id))
        await db.commit();return conv

    async def _new_conversation(self,db,msg:FeishuInboundMessage)->Conversation:
        user=await self._first_user(db)
        conv=await ConversationService(db).create(user.id,title="飞书新会话",type_="chat")
        binding=await db.scalar(select(ChannelBinding).where(
            ChannelBinding.channel=="feishu",ChannelBinding.external_chat==msg.chat_id,
            ChannelBinding.external_user==msg.sender_id,
        ))
        if binding:binding.conversation_id=conv.id
        else:db.add(ChannelBinding(channel="feishu",external_user=msg.sender_id,external_chat=msg.chat_id,conversation_id=conv.id))
        await db.commit();return conv

    async def handle(self,msg:FeishuInboundMessage)->None:
        async with SessionFactory() as db:
            event=await db.get(ProcessedChannelEvent,msg.event_key)
            if event and event.status=='processed':return
            if not event:
                event=ProcessedChannelEvent(event_key=msg.event_key,channel='feishu',status='processing',attempts=1)
                db.add(event)
                try:
                    # Claim the event before running the model. A WebSocket may
                    # deliver the same event concurrently; the unique key makes
                    # only one callback the owner of the work.
                    await db.commit()
                except IntegrityError:
                    await db.rollback()
                    existing=await db.get(ProcessedChannelEvent,msg.event_key)
                    if existing and existing.status in {'processing','processed'}:
                        return
                    raise
            else:
                event.status='processing';event.attempts+=1;event.last_error=None
                await db.commit()
            try:
                if msg.text.strip().lower() in {"/new","新会话","新建会话"}:
                    conv=await self._new_conversation(db,msg);reply=f"已创建新会话：{conv.title}"
                elif msg.text.strip().lower() in {"/help","帮助"}:
                    conv=await self._conversation_for_message(db,msg)
                    reply="可以直接询问运维问题；回复 Incident 告警可继续追问。发送 /new 可创建新会话。"
                else:
                    conv=await self._conversation_for_message(db,msg)
                    state=await AgentService(db,self.checkpointer).run(conv.id,msg.text,channel='feishu')
                    reply=state.get('final_response') or 'Oncall 未生成有效回复。'

                if msg.message_id and not await db.get(FeishuMessageLink,msg.message_id):
                    db.add(FeishuMessageLink(
                        message_id=msg.message_id,root_id=msg.root_id or msg.parent_id,
                        chat_id=msg.chat_id,conversation_id=conv.id,incident_id=conv.incident_id,
                    ))
                # Do not perform Feishu network I/O inside event processing. Durable outbox
                # makes retries safe and prevents a transient send error from re-running Agent.
                db.add(Notification(
                    incident_id=conv.incident_id,channel='feishu',target=msg.chat_id,status='pending',
                    payload={
                        'kind':'reply','text':reply,'conversation_id':str(conv.id),
                        'root_id':msg.root_id or msg.message_id,'receive_id_type':'chat_id',
                    },
                    dedupe_key=f'feishu-event:{msg.event_key}:reply',
                ))
                event.status='processed';event.last_error=None
                await db.commit()
            except Exception as exc:
                await db.rollback()
                event=await db.get(ProcessedChannelEvent,msg.event_key)
                if event:
                    event.status='failed';event.last_error=str(exc)[:4000]
                # Best-effort apology in the Outbox so the user gets feedback
                # in Feishu instead of a silent drop. We surface the exception
                # class name (short, not the full traceback) to help the
                # operator triage at a glance. If resolving the conversation
                # or staging the notification itself fails, the event is
                # still marked failed and the original error is re-raised.
                conv = None
                try:
                    conv = await self._conversation_for_message(db, msg)
                except Exception:
                    pass
                try:
                    db.add(Notification(
                        incident_id=(conv.incident_id if conv else None),
                        channel='feishu',
                        target=msg.chat_id,
                        status='pending',
                        payload={
                            'kind': 'reply',
                            'text': f"抱歉，本次请求处理失败（{type(exc).__name__}），请稍后再试或联系管理员。",
                            'conversation_id': (str(conv.id) if conv else None),
                            'root_id': msg.root_id or msg.message_id,
                            'receive_id_type': 'chat_id',
                        },
                        dedupe_key=f'feishu-event:{msg.event_key}:apology',
                    ))
                except Exception:
                    pass
                await db.commit()
                raise


def build_lark_callback(loop:asyncio.AbstractEventLoop,gateway:FeishuGateway):
    """Acknowledge the SDK callback quickly and move durable work to the API event loop."""
    def callback(event):
        parsed=parse_lark_message(event)
        if parsed:
            future=asyncio.run_coroutine_threadsafe(gateway.handle(parsed),loop)
            # The SDK callback must return quickly, but silently discarded
            # coroutine failures make Feishu look like it stopped responding.
            # Consume the exception so it is visible to the process logger.
            def report_done(done):
                try:
                    done.result()
                except Exception as exc:
                    import logging
                    logging.getLogger(__name__).exception('Feishu event handling failed: %s', exc)
            future.add_done_callback(report_done)
        return None
    return callback
