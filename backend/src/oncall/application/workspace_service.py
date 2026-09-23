from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from oncall.infrastructure.db.models import User


async def ensure_local_user(session: AsyncSession) -> User:
    """Return the single local workspace owner used by the no-login deployment.

    User ownership remains in the data model so existing projects and conversations
    keep their foreign keys, but there is intentionally no password or session
    authentication in this deployment mode.
    """
    user = await session.scalar(select(User).order_by(User.created_at.asc()).limit(1))
    if user:
        return user
    user = User(username="local-workspace", password_hash="auth-disabled")
    session.add(user)
    try:
        await session.commit()
        await session.refresh(user)
        return user
    except IntegrityError:
        await session.rollback()
        user = await session.scalar(select(User).order_by(User.created_at.asc()).limit(1))
        if user:
            return user
        raise
