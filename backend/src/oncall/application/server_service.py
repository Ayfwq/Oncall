from __future__ import annotations

from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from oncall.application.dtos import MonitoredServerCreateDTO, MonitoredServerDTO
from oncall.infrastructure.db.models import MonitoredServer, Project


def to_dto(row: MonitoredServer) -> MonitoredServerDTO:
    return MonitoredServerDTO(
        id=row.id,
        name=row.name,
        node_metrics_url=row.node_metrics_url,
        gpu_metrics_url=row.gpu_metrics_url,
        enabled=row.enabled,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


class MonitoredServerService:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def list(self, user_id: UUID) -> list[tuple[MonitoredServer, int]]:
        stmt = (
            select(MonitoredServer, func.count(Project.id))
            .outerjoin(Project, Project.server_id == MonitoredServer.id)
            .where(MonitoredServer.user_id == user_id)
            .group_by(MonitoredServer.id)
            .order_by(MonitoredServer.updated_at.desc())
        )
        return list((await self.session.execute(stmt)).all())

    async def get(self, server_id: UUID, user_id: UUID | None = None) -> MonitoredServer | None:
        stmt = select(MonitoredServer).where(MonitoredServer.id == server_id)
        if user_id is not None:
            stmt = stmt.where(MonitoredServer.user_id == user_id)
        return await self.session.scalar(stmt)

    async def create(self, user_id: UUID, dto: MonitoredServerCreateDTO) -> MonitoredServer:
        if await self.session.scalar(select(MonitoredServer.id).where(MonitoredServer.user_id == user_id, MonitoredServer.name == dto.name)):
            raise ValueError('server name already exists')
        row = MonitoredServer(user_id=user_id, **dto.model_dump())
        self.session.add(row)
        await self.session.commit()
        await self.session.refresh(row)
        return row

    async def update(self, server_id: UUID, user_id: UUID, dto: MonitoredServerCreateDTO) -> MonitoredServer | None:
        row = await self.get(server_id, user_id)
        if row is None:
            return None
        row.name = dto.name
        row.node_metrics_url = dto.node_metrics_url
        row.gpu_metrics_url = dto.gpu_metrics_url
        row.enabled = dto.enabled
        await self.session.commit()
        await self.session.refresh(row)
        return row

    async def delete(self, server_id: UUID, user_id: UUID) -> bool:
        row = await self.get(server_id, user_id)
        if row is None:
            return False
        linked = await self.session.scalar(select(func.count(Project.id)).where(Project.server_id == row.id))
        if linked:
            raise ValueError('server still has linked projects')
        await self.session.delete(row)
        await self.session.commit()
        return True
