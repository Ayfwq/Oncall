from uuid import uuid4

import pytest
from oncall.application import memory_service
from oncall.application.conversation_service import ConversationService
from oncall.application.long_term_memory import LongTermMemoryService
from oncall.application.memory_service import ConversationMemoryService
from oncall.bootstrap.config import Settings
from oncall.infrastructure.db.models import ConversationSummary, User
from oncall.infrastructure.db.session import SessionFactory
from sqlalchemy import delete


@pytest.mark.integration
@pytest.mark.asyncio
async def test_fact_survives_new_conversation_and_can_be_forgotten():
    user_id = uuid4()
    async with SessionFactory() as db:
        db.add(User(id=user_id, username=f"memory-test-{user_id}"))
        await db.commit()
        try:
            conversations = ConversationService(db)
            first = await conversations.create(user_id, title="第一条对话")
            second = await conversations.create(user_id, title="第二条对话")
            source = await conversations.add_message(first.id, "user", "请记住：回答时先给排查步骤")
            memory = LongTermMemoryService(db)
            fact = await memory.remember(
                user_id, "回答时先给排查步骤", source_message_id=source.id
            )
            assert fact.source_message_id == source.id
            assert [x["id"] for x in await memory.relevant(user_id, None, "如何排查？")] == [
                str(fact.id)
            ]
            assert (await conversations.get(second.id, user_id)) is not None
            assert await memory.forget(user_id, fact.id)
            assert await memory.relevant(user_id, None, "如何排查？") == []
        finally:
            await db.execute(delete(User).where(User.id == user_id))
            await db.commit()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_compaction_persists_boundary_and_preserves_raw_history(monkeypatch):
    settings = Settings(memory_compact_at_tokens=4096, memory_recent_tokens=500)
    monkeypatch.setattr(memory_service, "get_settings", lambda: settings)
    monkeypatch.setattr("oncall.application.memory_policy.get_settings", lambda: settings)

    class SummaryModel:
        async def summarize(self, _prompt):
            return "确认的事实：用户正在排查 CPU 问题。"

    user_id = uuid4()
    async with SessionFactory() as db:
        db.add(User(id=user_id, username=f"compact-test-{user_id}"))
        await db.commit()
        try:
            conversations = ConversationService(db)
            conv = await conversations.create(user_id)
            for index in range(8):
                await conversations.add_message(conv.id, "user", f"排查问题 {index} " + "甲" * 150)
                await conversations.add_message(conv.id, "assistant", f"排查回答 {index} " + "乙" * 150)
            all_messages = await conversations.messages(conv.id)
            summary = await ConversationMemoryService(db, SummaryModel()).compact_if_needed(
                conv.id, context_tokens=4096
            )
            assert summary is not None
            assert summary.through_message_id in {m.id for m in all_messages[:-2]}
            tail = await conversations.messages_after_summary(conv.id, summary)
            assert tail and tail[-1].id == all_messages[-1].id
            assert len(await conversations.messages(conv.id)) == len(all_messages)
        finally:
            await db.execute(delete(User).where(User.id == user_id))
            await db.commit()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_summary_boundary_keeps_only_uncompressed_messages():
    user_id = uuid4()
    async with SessionFactory() as db:
        db.add(User(id=user_id, username=f"summary-test-{user_id}"))
        await db.commit()
        try:
            conversations = ConversationService(db)
            conv = await conversations.create(user_id)
            first = await conversations.add_message(conv.id, "user", "旧问题")
            second = await conversations.add_message(conv.id, "assistant", "旧回答")
            await conversations.add_message(conv.id, "user", "新问题")
            await conversations.add_message(conv.id, "assistant", "新回答")
            summary = ConversationSummary(
                conversation_id=conv.id,
                through_message_id=second.id,
                summary="旧问题与旧回答的摘要",
                token_estimate=20,
            )
            db.add(summary)
            await db.commit()
            assert [m.content for m in await conversations.messages_after_summary(conv.id, summary)] == [
                "新问题",
                "新回答",
            ]
            assert first.id != summary.through_message_id
        finally:
            await db.execute(delete(User).where(User.id == user_id))
            await db.commit()
