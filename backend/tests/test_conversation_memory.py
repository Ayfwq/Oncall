from types import SimpleNamespace
from uuid import uuid4

import pytest
from oncall.agent import context_builder
from oncall.application import memory_service
from oncall.application.memory_policy import count_tokens, message_tokens, take_recent_whole_turns
from oncall.application.memory_service import ConversationMemoryService
from oncall.bootstrap.config import Settings


class FakeSession:
    def __init__(self):
        self.added = []
        self.commits = 0

    def add(self, value):
        self.added.append(value)

    async def scalar(self, _statement):
        return None

    async def get(self, _model, _id):
        return None

    async def commit(self):
        self.commits += 1

    async def refresh(self, _value):
        pass


class FakeModel:
    def __init__(self):
        self.prompts = []

    async def summarize(self, prompt):
        self.prompts.append(prompt)
        return "新版摘要"


def messages(turns: int, chars: int = 200):
    rows = []
    for n in range(turns):
        rows.extend(
            [
                SimpleNamespace(id=uuid4(), role="user", content=f"问题 {n} " + "甲" * chars),
                SimpleNamespace(id=uuid4(), role="assistant", content=f"回答 {n} " + "乙" * chars),
            ]
        )
    return rows


@pytest.mark.asyncio
async def test_compaction_only_at_threshold_and_keeps_recent_raw(monkeypatch):
    settings = Settings(
        memory_compact_at_tokens=4096,
        memory_recent_tokens=500,
        memory_summary_tokens=150,
    )
    monkeypatch.setattr(memory_service, "get_settings", lambda: settings)
    monkeypatch.setattr("oncall.application.memory_policy.get_settings", lambda: settings)
    rows = messages(8, 80)
    session = FakeSession()
    model = FakeModel()
    current = None

    class FakeConversationService:
        def __init__(self, _session):
            pass

        async def latest_summary(self, _conversation_id):
            return current

        async def messages_after_summary(self, _conversation_id, summary):
            if not summary:
                return rows
            index = next(i for i, m in enumerate(rows) if m.id == summary.through_message_id)
            return rows[index + 1 :]

    monkeypatch.setattr(memory_service, "ConversationService", FakeConversationService)
    service = ConversationMemoryService(session, model)
    assert await service.compact_if_needed(uuid4(), context_tokens=4095) is None
    assert model.prompts == []

    first = await service.compact_if_needed(uuid4(), context_tokens=4096)
    assert first is not None
    assert first.through_message_id in {m.id for m in rows[:-2]}
    assert model.prompts
    assert count_tokens(first.summary) <= settings.memory_summary_tokens
    current = first
    assert await service.compact_if_needed(uuid4(), context_tokens=4095) is first
    assert len(model.prompts) == 1


@pytest.mark.asyncio
async def test_manual_compaction_below_threshold_uses_same_recent_window(monkeypatch):
    settings = Settings(memory_compact_at_tokens=4096, memory_recent_tokens=500)
    monkeypatch.setattr(memory_service, "get_settings", lambda: settings)
    monkeypatch.setattr("oncall.application.memory_policy.get_settings", lambda: settings)
    rows = messages(8, 80)
    session = FakeSession()
    model = FakeModel()

    class FakeConversationService:
        def __init__(self, _session):
            pass

        async def latest_summary(self, _conversation_id):
            return None

        async def messages_after_summary(self, _conversation_id, _summary):
            return rows

    monkeypatch.setattr(memory_service, "ConversationService", FakeConversationService)
    service = ConversationMemoryService(session, model)
    usage = await service.usage(uuid4())
    assert usage["can_compact"] is True
    assert usage["context_window_tokens"] == settings.memory_context_window_tokens
    summary = await service.compact_if_needed(uuid4(), context_tokens=1, force=True)
    assert summary is not None
    assert summary.through_message_id in {m.id for m in rows[:-2]}
    assert session.commits == 1


@pytest.mark.asyncio
async def test_manual_compaction_of_short_conversation_is_real(monkeypatch):
    settings = Settings(memory_compact_at_tokens=4096, memory_recent_tokens=4000)
    monkeypatch.setattr(memory_service, "get_settings", lambda: settings)
    monkeypatch.setattr("oncall.application.memory_policy.get_settings", lambda: settings)
    rows = messages(1, 200)
    session = FakeSession()
    model = FakeModel()

    class FakeConversationService:
        def __init__(self, _session):
            pass

        async def latest_summary(self, _conversation_id):
            return None

        async def messages_after_summary(self, _conversation_id, _summary):
            return rows

    monkeypatch.setattr(memory_service, "ConversationService", FakeConversationService)
    service = ConversationMemoryService(session, model)
    assert (await service.usage(uuid4()))["can_compact"] is True
    summary = await service.compact_if_needed(uuid4(), context_tokens=100, force=True)
    assert summary is not None
    assert summary.through_message_id == rows[-1].id
    assert summary.token_estimate <= sum(message_tokens(m.role, m.content) for m in rows) // 2
    assert model.prompts and session.commits == 1


def test_recent_window_uses_tokens_and_complete_turns(monkeypatch):
    settings = Settings()
    monkeypatch.setattr("oncall.application.memory_policy.get_settings", lambda: settings)
    rows = messages(5, 20)
    one_turn_tokens = sum(count_tokens(m.content) + count_tokens(m.role) + 8 for m in rows[-2:])
    old, recent = take_recent_whole_turns(rows, one_turn_tokens * 2)
    assert len(recent) == 4
    assert len(old) == 6
    assert recent[0].role == "user" and recent[-1].role == "assistant"


def test_bundled_mimo_tokenizer_used_when_data_volume_is_empty(monkeypatch):
    settings = Settings(
        model_name="mimo-v2.6-flash",
        memory_tokenizer_path="data/tokenizers/does-not-exist.json",
    )
    monkeypatch.setattr("oncall.application.memory_policy.get_settings", lambda: settings)
    assert count_tokens("请简洁回答：你好。" * 120) == 720


@pytest.mark.asyncio
async def test_empty_summary_does_not_advance_boundary(monkeypatch):
    settings = Settings(memory_compact_at_tokens=4096, memory_recent_tokens=500)
    monkeypatch.setattr(memory_service, "get_settings", lambda: settings)
    monkeypatch.setattr("oncall.application.memory_policy.get_settings", lambda: settings)
    rows = messages(8, 80)
    session = FakeSession()

    class EmptyModel:
        async def summarize(self, _prompt):
            return ""

    class FakeConversationService:
        def __init__(self, _session):
            pass

        async def latest_summary(self, _conversation_id):
            return None

        async def messages_after_summary(self, _conversation_id, _summary):
            return rows

    monkeypatch.setattr(memory_service, "ConversationService", FakeConversationService)
    with pytest.raises(ValueError, match="空摘要"):
        await ConversationMemoryService(session, EmptyModel()).compact_if_needed(
            uuid4(), context_tokens=4096
        )
    assert session.commits == 0


@pytest.mark.asyncio
async def test_context_reads_every_uncompressed_message(monkeypatch):
    rows = messages(20, 5)
    summary = SimpleNamespace(through_message_id=rows[1].id, summary="已压缩")

    class FakeConversationService:
        def __init__(self, _session):
            pass

        async def latest_summary(self, _conversation_id):
            return summary

        async def messages_after_summary(self, _conversation_id, value):
            assert value is summary
            return rows[2:]

    class Factory:
        async def __aenter__(self):
            return FakeSession()

        async def __aexit__(self, *_args):
            pass

    monkeypatch.setattr(context_builder, "ConversationService", FakeConversationService)
    builder = context_builder.ContextBuilder(object(), session_factory=Factory)
    recent, text = await builder._load_messages(uuid4(), None)
    assert len(recent) == 38  # no arbitrary 30-message cap
    assert recent[0].id == rows[2].id
    assert text == "已压缩"
