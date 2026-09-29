from __future__ import annotations

import re
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from oncall.agent.model_gateway import ModelProvider
from oncall.agent.prompts import DECISION_SCHEMA, STREAM_ANSWER_PROMPT, SYSTEM_PROMPT
from oncall.application.conversation_service import ConversationService
from oncall.application.memory_policy import (
    count_tokens,
    message_tokens,
    take_recent_whole_turns,
    trim_to_tokens,
)
from oncall.bootstrap.config import get_settings
from oncall.infrastructure.db.models import Conversation, ConversationSummary, Message


class ConversationMemoryService:
    """Maintain one rolling summary and an uncompressed recent tail.

    All raw messages remain in PostgreSQL. Only the context sent to the model is
    compacted, and each summary records the last original message it covers.
    """

    def __init__(self, session: AsyncSession, model: ModelProvider):
        self.session = session
        self.model = model

    async def usage(self, conversation_id: UUID) -> dict:
        """Estimate the persistent chat context using the same scope as compaction."""
        settings = get_settings()
        cs = ConversationService(self.session)
        latest = await cs.latest_summary(conversation_id)
        conv = await self.session.get(Conversation, conversation_id)
        rows = [
            m for m in await cs.messages_after_summary(conversation_id, latest)
            if m.role != "event"
            and not (conv and conv.type == "ops" and (m.metadata_json or {}).get("incident_id"))
        ]
        old, _recent = take_recent_whole_turns(rows, settings.memory_recent_tokens)
        raw_tokens = sum(message_tokens(m.role, m.content) for m in rows)
        used = (
            raw_tokens
            + count_tokens(latest.summary if latest else "")
            + max(count_tokens(SYSTEM_PROMPT + DECISION_SCHEMA), count_tokens(STREAM_ANSWER_PROMPT))
        )
        return {
            "estimated_tokens": used,
            "context_window_tokens": settings.memory_context_window_tokens,
            "compact_at_tokens": settings.memory_compact_at_tokens,
            # A manual request may also compact a short tail. The normal 4K
            # recent window only applies to automatic compaction.
            "can_compact": bool(old) or raw_tokens >= 128,
            "scope": "主会话记忆" if conv and conv.type == "ops" else "会话记忆",
        }

    async def _summarize(
        self, previous: str, messages: list[Message], *, max_tokens: int | None = None
    ) -> str:
        s = get_settings()
        limit = max_tokens or s.memory_summary_tokens
        concise = limit < 256
        transcript = "\n".join(f"[{m.id}] {m.role}: {m.content}" for m in messages)
        # Fold long histories in consecutive batches; no old text is silently
        # discarded before the model sees it.
        while transcript:
            batch = trim_to_tokens(transcript, 10000)
            transcript = transcript[len(batch) :]
            style = (
                "只写完整的简短中文句子，不要消息 ID、前言或 Markdown，不要编造。"
                if concise
                else "区分已证实与推测，保留关键消息 ID，不要编造。"
            )
            prompt = (
                "请将旧摘要和这批原始对话合并为一份新的运维会话摘要。"
                "只保留用户目标、明确事实、项目/服务、已执行检查及结果、结论和未解决问题。"
                f"{style}"
                f"摘要不超过 {limit} token。\n"
                f"旧摘要：\n{previous or '无'}\n\n新增原文：\n{batch}"
            )
            previous = await self.model.summarize(prompt)
            if not previous.strip():
                raise ValueError("模型返回空摘要，保留原始对话并稍后重试")
            if count_tokens(previous) > limit:
                previous = await self.model.summarize(
                    f"将以下摘要压缩到 {limit} token 内，"
                    + ("只写完整句子，不要消息 ID：\n" if concise else "保留事实与消息 ID：\n")
                    + previous
                )
                if not previous.strip():
                    raise ValueError("模型返回空摘要，保留原始对话并稍后重试")
            if count_tokens(previous) > limit:
                trimmed = trim_to_tokens(previous, limit)
                ends = list(re.finditer(r"[。！？.!?；;]", trimmed))
                if not ends or ends[-1].end() < len(trimmed) // 3:
                    raise ValueError("模型未能生成预算内的完整摘要，原始对话已保留")
                previous = trimmed[: ends[-1].end()].strip()
        return previous

    async def compact_if_needed(
        self, conversation_id: UUID, *, context_tokens: int | None = None, force: bool = False
    ) -> ConversationSummary | None:
        s = get_settings()
        cs = ConversationService(self.session)
        latest = await cs.latest_summary(conversation_id)
        conv = await self.session.get(Conversation, conversation_id)
        rows = [
            m
            for m in await cs.messages_after_summary(conversation_id, latest)
            if m.role != "event"
            and not (conv and conv.type == "ops" and m.metadata_json.get("incident_id"))
        ]
        tail_tokens = sum(message_tokens(m.role, m.content) for m in rows)
        # The full model context is preferred. Tail length is a fallback for
        # direct callers that do not provide the assembled prompt size.
        estimated_context = (
            context_tokens
            if context_tokens is not None
            else (
                tail_tokens
                + count_tokens(latest.summary if latest else "")
                + max(
                    count_tokens(SYSTEM_PROMPT + DECISION_SCHEMA),
                    count_tokens(STREAM_ANSWER_PROMPT),
                )
            )
        )
        if not force and estimated_context < s.memory_compact_at_tokens:
            return latest

        old, _recent = take_recent_whole_turns(rows, s.memory_recent_tokens)
        short_tail = force and not old and tail_tokens >= 128
        if short_tail:
            # The user explicitly requested compaction before the recent
            # window filled. Summarize that short tail instead of silently
            # reporting success without changing anything.
            old = rows
        if not old:
            return latest

        previous_text = latest.summary if latest else ""
        summary_limit = s.memory_summary_tokens
        if short_tail:
            old_tokens = sum(message_tokens(m.role, m.content) for m in old)
            summary_limit = min(
                summary_limit,
                max(count_tokens(previous_text), old_tokens // 2, 64),
            )
        summary_text = await self._summarize(previous_text, old, max_tokens=summary_limit)
        # Serialize the boundary update per conversation. Another worker may
        # have compacted the same history while this model call was running.
        await self.session.scalar(
            select(Conversation.id).where(Conversation.id == conversation_id).with_for_update()
        )
        current = await cs.latest_summary(conversation_id)
        if (current.id if current else None) != (latest.id if latest else None):
            return current
        summary = ConversationSummary(
            conversation_id=conversation_id,
            through_message_id=old[-1].id,
            summary=summary_text,
            token_estimate=count_tokens(summary_text),
        )
        self.session.add(summary)
        await self.session.commit()
        await self.session.refresh(summary)
        return summary
