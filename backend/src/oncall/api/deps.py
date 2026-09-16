from __future__ import annotations

from fastapi import Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from oncall.application.workspace_service import ensure_local_user
from oncall.infrastructure.db.models import User
from oncall.infrastructure.db.session import get_session


async def current_user(session: AsyncSession = Depends(get_session)) -> User:
    """Compatibility dependency for single-workspace API handlers.

    The name is retained to avoid changing every route signature; it no longer
    reads cookies or performs authentication.
    """
    return await ensure_local_user(session)

def get_checkpointer(request:Request):return getattr(request.app.state,'checkpointer',None)
