from __future__ import annotations

import hashlib
import secrets
from datetime import datetime, timedelta

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from oncall.bootstrap.config import get_settings
from oncall.infrastructure.db.models import Session, User
from oncall.security.passwords import hash_password, verify_password


class AuthService:
    def __init__(self, session: AsyncSession):
        self.session = session
        self.settings = get_settings()

    async def ensure_admin(self) -> User:
        user = await self.session.scalar(select(User).where(User.username == self.settings.admin_username))
        if user:
            return user
        user = User(username=self.settings.admin_username, password_hash=hash_password(self.settings.admin_password))
        self.session.add(user)
        await self.session.commit()
        await self.session.refresh(user)
        return user

    async def login(self, username: str, password: str) -> tuple[User, str] | None:
        user = await self.session.scalar(select(User).where(User.username == username))
        if not user or not verify_password(user.password_hash, password):
            return None
        token = secrets.token_urlsafe(48)
        token_hash = hashlib.sha256(token.encode()).hexdigest()
        expires = datetime.now().astimezone() + timedelta(days=self.settings.session_days)
        self.session.add(Session(user_id=user.id, token_hash=token_hash, expires_at=expires))
        await self.session.commit()
        return user, token

    async def user_from_token(self, token: str | None) -> User | None:
        if not token:
            return None
        token_hash = hashlib.sha256(token.encode()).hexdigest()
        now = datetime.now().astimezone()
        stmt = select(User).join(Session, Session.user_id == User.id).where(Session.token_hash == token_hash, Session.expires_at > now)
        return await self.session.scalar(stmt)

    async def logout(self, token: str | None) -> None:
        if not token:
            return
        token_hash = hashlib.sha256(token.encode()).hexdigest()
        await self.session.execute(delete(Session).where(Session.token_hash == token_hash))
        await self.session.commit()

    async def change_password(self, user: User, current_password: str, new_password: str, current_token: str | None) -> bool:
        if not verify_password(user.password_hash, current_password):
            return False
        user.password_hash = hash_password(new_password)
        if current_token:
            current_hash = hashlib.sha256(current_token.encode()).hexdigest()
            await self.session.execute(delete(Session).where(Session.user_id == user.id, Session.token_hash != current_hash))
        else:
            await self.session.execute(delete(Session).where(Session.user_id == user.id))
        await self.session.commit()
        return True
