from __future__ import annotations

import json
from datetime import datetime, timedelta

import httpx
from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from oncall.bootstrap.config import get_settings
from oncall.channels.notification_policy import is_cooldown_kind, retry_delay_seconds
from oncall.infrastructure.db.models import Conversation, FeishuMessageLink, Notification


class FeishuClient:
    def __init__(self):
        self.s=get_settings(); self._token=None; self._token_expires_at=None

    async def tenant_token(self)->str:
        now=datetime.now().astimezone()
        if self._token and self._token_expires_at and now < self._token_expires_at:
            return self._token
        async with httpx.AsyncClient(timeout=15) as c:
            r=await c.post(
                'https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal',
                json={'app_id':self.s.feishu_app_id,'app_secret':self.s.feishu_app_secret},
            )
            r.raise_for_status();data=r.json();self._token=data['tenant_access_token']
            ttl=max(60,int(data.get('expire',7200))-120)
            self._token_expires_at=now+timedelta(seconds=ttl)
            return self._token

    async def _send(self,receive_id:str,msg_type:str,content:dict,receive_id_type:str='chat_id')->str:
        token=await self.tenant_token()
        async with httpx.AsyncClient(timeout=15) as c:
            r=await c.post(
                f'https://open.feishu.cn/open-apis/im/v1/messages?receive_id_type={receive_id_type}',
                headers={'Authorization':f'Bearer {token}'},
                json={'receive_id':receive_id,'msg_type':msg_type,'content':json.dumps(content,ensure_ascii=False)},
            )
            r.raise_for_status()
            body=r.json()
            if body.get('code') not in (None,0):
                raise RuntimeError(f"Feishu API error: {body.get('code')} {body.get('msg')}")
            return body.get('data',{}).get('message_id','')

    async def send_text(self,receive_id:str,text:str,receive_id_type:str='chat_id')->str:
        return await self._send(receive_id,'text',{'text':str(text)[:30000]},receive_id_type)

    async def send_incident_card(self,receive_id:str,text:str,severity:str='warning',receive_id_type:str='chat_id')->str:
        template='red' if severity=='critical' else 'orange' if severity=='warning' else 'blue'
        text=str(text)[:18000]
        card={
            'config':{'wide_screen_mode':True},
            'header':{'template':template,'title':{'tag':'plain_text','content':'Oncall Incident 监测报告'}},
            'elements':[{'tag':'markdown','content':text}],
        }
        return await self._send(receive_id,'interactive',card,receive_id_type)


class FeishuOutboxSender:
    def __init__(self,session:AsyncSession):
        self.session=session; self.client=FeishuClient(); self.s=get_settings()

    async def _in_cooldown(self,n:Notification,now:datetime)->bool:
        kind=n.payload.get('kind')
        if not n.incident_id or not is_cooldown_kind(kind) or self.s.notification_cooldown_seconds<=0:
            return False
        cutoff=now-timedelta(seconds=self.s.notification_cooldown_seconds)
        recent=list((await self.session.scalars(
            select(Notification).where(
                Notification.incident_id==n.incident_id,
                Notification.channel=='feishu',
                Notification.status=='sent',
                Notification.sent_at>=cutoff,
                Notification.id!=n.id,
            ).order_by(Notification.sent_at.desc()).limit(20)
        )).all())
        return any(x.payload.get('kind')==kind for x in recent)

    async def send_pending(self,limit:int=20)->int:
        if not self.s.feishu_enabled:return 0
        now=datetime.now().astimezone()
        stale_cutoff=now-timedelta(seconds=self.s.feishu_outbox_claim_seconds)
        rows=list((await self.session.scalars(
            select(Notification).where(
                Notification.channel=='feishu',
                Notification.available_at<=now,
                or_(
                    Notification.status=='pending',
                    and_(Notification.status=='sending',Notification.updated_at<=stale_cutoff),
                ),
            ).order_by(Notification.created_at.asc()).with_for_update(skip_locked=True).limit(limit)
        )).all())
        if not rows:return 0
        # Claim with the existing status/updated_at columns. The transaction
        # is committed before network I/O, so another worker cannot send the
        # same batch while this worker is waiting on Feishu.
        for n in rows:
            n.status='sending';n.attempts+=1
        await self.session.commit()
        # Resolve the "default" push target once per outbox tick. If the
        # env default is set, use it (and honour the env receive_id_type,
        # which may be 'chat_id' or 'open_id'). Otherwise fall back to the
        # chat of the most recent Feishu message so a brand-new user only
        # needs to message the bot once for active push to start working
        # (no chat_id hunting). The fallback is always a chat_id.
        default_target = self.s.feishu_default_receive_id
        if default_target:
            default_type = self.s.feishu_default_receive_id_type
        else:
            default_type = 'chat_id'
            fb = await self.session.scalar(
                select(FeishuMessageLink).order_by(FeishuMessageLink.created_at.desc()).limit(1)
            )
            if fb:
                default_target = fb.chat_id
        sent=0
        for n in rows:
            if n.target == 'default':
                target = default_target
                rid_type = default_type
            else:
                target = n.target
                # Non-default targets (e.g. inbound replies) carry their
                # own receive_id_type in the payload; default to chat_id.
                rid_type = str(n.payload.get('receive_id_type') or 'chat_id')
            if not target:
                n.status='dead';n.last_error='Feishu receive_id is not configured (message the bot once to auto-bind, or set ONCALL_FEISHU_DEFAULT_RECEIVE_ID)';continue
            if await self._in_cooldown(n,now):
                n.status='suppressed';n.last_error='notification cooldown';continue
            try:
                text=n.payload.get('text') or f"Oncall Incident: {n.payload.get('summary','')}"
                if n.payload.get('kind')=='diagnosis':
                    message_id=await self.client.send_incident_card(target,text,n.payload.get('severity','warning'),rid_type)
                else:
                    message_id=await self.client.send_text(target,text,rid_type)
                n.status='sent';n.sent_at=datetime.now().astimezone();n.last_error=None;n.payload={**n.payload,'message_id':message_id};sent+=1
                if message_id:
                    conv=None
                    conversation_id=n.payload.get('conversation_id')
                    if conversation_id:
                        from uuid import UUID
                        try:conv=await self.session.get(Conversation,UUID(str(conversation_id)))
                        except (ValueError,TypeError):conv=None
                    elif n.incident_id:
                        conv=await self.session.scalar(select(Conversation).where(Conversation.incident_id==n.incident_id).order_by(Conversation.created_at.asc()).limit(1))
                    if conv and not await self.session.get(FeishuMessageLink,message_id):
                        self.session.add(FeishuMessageLink(
                            message_id=message_id,root_id=n.payload.get('root_id'),chat_id=target,
                            conversation_id=conv.id,incident_id=conv.incident_id,
                        ))
            except Exception as exc:
                n.last_error=str(exc)[:4000]
                if n.attempts>=n.max_attempts:
                    n.status='dead'
                else:
                    n.status='pending'
                    n.available_at=datetime.now().astimezone()+timedelta(seconds=retry_delay_seconds(n.attempts))
            # Commit each row independently. A process crash after an API
            # response cannot roll back already completed rows in this batch.
            await self.session.commit()
        return sent


def start_ws_listener(on_message):
    """Starts official lark-oapi websocket client in a daemon thread.

    The callback acknowledges quickly; application work is scheduled on the API loop.

    Implementation note: lark_oapi.ws.client binds ``loop = asyncio.get_event_loop()``
    at *import time* as a module-level global, and ``Client.__init__`` also creates
    an ``asyncio.Lock()`` bound to the current loop. If lark_oapi is imported in
    the main uvicorn thread (which already runs an asyncio loop), that module
    global is the running main loop and ``client.start()`` raises
    "This event loop is already running". We therefore do the lark_oapi import,
    dispatcher build and Client construction *inside* the daemon thread so the
    SDK binds to a fresh, non-running loop owned by that thread. Inbound
    callbacks still cross-thread into the main API loop via
    ``asyncio.run_coroutine_threadsafe`` (see ``build_lark_callback``).
    """
    s=get_settings()
    if not s.feishu_enabled:return None
    import asyncio
    import threading
    def _run():
        import lark_oapi as lark
        new_loop = asyncio.new_event_loop()
        asyncio.set_event_loop(new_loop)
        # Defensive: rebind the module-level loop in case lark_oapi was
        # ever imported elsewhere in this process on a different loop.
        lark.ws.client.loop = new_loop
        dispatcher = lark.EventDispatcherHandler.builder('', '').register_p2_im_message_receive_v1(on_message).build()
        client = lark.ws.Client(s.feishu_app_id, s.feishu_app_secret, event_handler=dispatcher, log_level=lark.LogLevel.INFO)
        client.start()
    t = threading.Thread(target=_run, name='feishu-ws', daemon=True)
    t.start()
    return t
