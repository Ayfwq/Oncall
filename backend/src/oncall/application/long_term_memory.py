"""Small, source-backed long-term facts shared across Web conversations."""

from __future__ import annotations

import re
from uuid import UUID

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from oncall.application.memory_policy import count_tokens
from oncall.bootstrap.config import get_settings
from oncall.infrastructure.db.models import MemoryFact
from oncall.security.redact import redact_text

_REMEMBER = re.compile(r"^\s*(?:请)?记住\s*[：:,，]?\s*(.+)$", re.S)


def explicit_fact(text: str) -> str | None:
    match = _REMEMBER.match(text)
    if not match:
        return None
    content = match.group(1).strip()
    if not content or count_tokens(content) > 500 or redact_text(content) != content:
        return None
    return content


class LongTermMemoryService:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def remember(
        self,
        user_id: UUID,
        content: str,
        *,
        project_id: UUID | None = None,
        source_message_id: UUID | None = None,
    ) -> MemoryFact:
        content = content.strip()
        if not content or count_tokens(content) > 500 or redact_text(content) != content:
            raise ValueError("记忆内容为空、过长或包含敏感凭据")
        existing = await self.session.scalar(
            select(MemoryFact).where(
                MemoryFact.user_id == user_id,
                MemoryFact.project_id == project_id if project_id else MemoryFact.project_id.is_(None),
                MemoryFact.content == content,
                MemoryFact.active.is_(True),
            )
        )
        if existing:
            return existing
        fact = MemoryFact(
            user_id=user_id,
            project_id=project_id,
            source_message_id=source_message_id,
            content=content,
        )
        self.session.add(fact)
        await self.session.commit()
        await self.session.refresh(fact)
        return fact

    async def list(
        self, user_id: UUID, project_id: UUID | None = None, *, all_projects: bool = False
    ) -> list[MemoryFact]:
        stmt = select(MemoryFact).where(MemoryFact.user_id == user_id, MemoryFact.active.is_(True))
        if not all_projects:
            scope = [MemoryFact.project_id.is_(None)]
            if project_id:
                scope.append(MemoryFact.project_id == project_id)
            stmt = stmt.where(or_(*scope))
        return list(
            (
                await self.session.scalars(
                    stmt.order_by(MemoryFact.created_at.desc(), MemoryFact.id.desc())
                )
            ).all()
        )

    async def relevant(self, user_id: UUID, project_id: UUID | None, query: str) -> list[dict]:
        rows = await self.list(user_id, project_id)
        terms = set(re.findall(r"[\u4e00-\u9fff]{2}|[a-zA-Z0-9_]+", query.lower()))

        def score(row: MemoryFact) -> int:
            body = row.content.lower()
            return sum(1 for term in terms if term in body)

        ranked = sorted(rows, key=score, reverse=True)
        selected: list[dict] = []
        used = 0
        for row in ranked:
            if score(row) == 0 and not any(x in row.content for x in ("我偏好", "回答时", "回复时")):
                continue
            cost = count_tokens(row.content) + 20
            if used + cost > get_settings().memory_facts_tokens:
                continue
            selected.append(
                {
                    "id": str(row.id),
                    "content": row.content,
                    "scope": "project" if row.project_id else "user",
                    "source_message_id": str(row.source_message_id) if row.source_message_id else None,
                }
            )
            used += cost
            if len(selected) == 8:
                break
        return selected

    async def forget(self, user_id: UUID, fact_id: UUID) -> bool:
        fact = await self.session.get(MemoryFact, fact_id)
        if not fact or fact.user_id != user_id or not fact.active:
            return False
        fact.active = False
        await self.session.commit()
        return True
