from __future__ import annotations

from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from oncall.application.dtos import MonitoredServerCreateDTO, MonitoredServerDTO
from oncall.bootstrap.config import get_settings
from oncall.infrastructure.db.models import MonitoredServer, Project
from oncall.security.crypto import SecretBox


def to_dto(row: MonitoredServer) -> MonitoredServerDTO:
    return MonitoredServerDTO(
        id=row.id,
        name=row.name,
        node_metrics_url=row.node_metrics_url,
        gpu_metrics_url=row.gpu_metrics_url,
        container_metrics_url=row.container_metrics_url,
        collector_url=row.collector_url,
        collector_token=None,
        enabled=row.enabled,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


class MonitoredServerService:
    def __init__(self, session: AsyncSession):
        self.session = session
        self.box = SecretBox(get_settings().secret_master_key)

    def collector_token(self, row: MonitoredServer) -> str:
        return self.box.decrypt(row.encrypted_collector_token)

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
        if await self.session.scalar(
            select(MonitoredServer.id).where(
                MonitoredServer.user_id == user_id, MonitoredServer.name == dto.name
            )
        ):
            raise ValueError("server name already exists")
        data = dto.model_dump(exclude={"collector_token"})
        row = MonitoredServer(user_id=user_id, **data)
        if dto.collector_token:
            row.encrypted_collector_token = self.box.encrypt(dto.collector_token)
        self.session.add(row)
        await self.session.commit()
        await self._reconcile_prometheus()
        await self.session.refresh(row)
        return row

    async def update(
        self, server_id: UUID, user_id: UUID, dto: MonitoredServerCreateDTO
    ) -> MonitoredServer | None:
        row = await self.get(server_id, user_id)
        if row is None:
            return None
        row.name = dto.name
        row.node_metrics_url = dto.node_metrics_url
        row.gpu_metrics_url = dto.gpu_metrics_url
        row.container_metrics_url = dto.container_metrics_url
        row.collector_url = dto.collector_url
        if dto.collector_token:
            row.encrypted_collector_token = self.box.encrypt(dto.collector_token)
        row.enabled = dto.enabled
        await self.session.commit()
        await self._reconcile_prometheus()
        await self.session.refresh(row)
        return row

    async def delete(self, server_id: UUID, user_id: UUID) -> bool:
        row = await self.get(server_id, user_id)
        if row is None:
            return False
        linked = await self.session.scalar(
            select(func.count(Project.id)).where(Project.server_id == row.id)
        )
        if linked:
            raise ValueError("server still has linked projects")
        await self.session.delete(row)
        await self.session.commit()
        await self._reconcile_prometheus()
        return True

    async def _reconcile_prometheus(self) -> None:
        try:
            from oncall.integrations.prometheus_api import PrometheusProvisioner

            await PrometheusProvisioner(self.session).reconcile()
        except Exception:
            import logging

            logging.getLogger(__name__).exception(
                "unable to reconcile Prometheus after server change"
            )
