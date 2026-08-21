from __future__ import annotations

from fastapi import Cookie, Depends, HTTPException, Request
from sqlalchemy.ext.asyncio import AsyncSession

from oncall.application.auth_service import AuthService
from oncall.infrastructure.db.session import get_session


async def current_user(oncall_session:str|None=Cookie(default=None),session:AsyncSession=Depends(get_session)):
    user=await AuthService(session).user_from_token(oncall_session)
    if not user:raise HTTPException(status_code=401,detail='not authenticated')
    return user

def get_checkpointer(request:Request):return getattr(request.app.state,'checkpointer',None)
