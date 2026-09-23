from __future__ import annotations

import logging
from urllib.parse import parse_qsl, unquote, urlsplit
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from oncall.application.dtos import (
    DatabaseProfileDTO,
    LogSourceDTO,
    MetricsSourceDTO,
    MonitoredServerDTO,
    ProjectCreateDTO,
    ProjectRuntimeConfig,
    PythonProjectOnboardDTO,
)
from oncall.bootstrap.config import get_settings
from oncall.infrastructure.db.models import (
    MonitoredServer,
    Project,
    ProjectDatabaseProfile,
    ProjectLogSource,
    ProjectMetricsSource,
)
from oncall.security.crypto import SecretBox

logger = logging.getLogger(__name__)


class ProjectService:
    """Project configuration only; alert policy lives in Prometheus."""

    def __init__(self, session: AsyncSession):
        self.session = session
        self.box = SecretBox(get_settings().secret_master_key)

    async def list(self, user_id: UUID) -> list[Project]:
        return list(
            (
                await self.session.scalars(
                    select(Project)
                    .where(Project.user_id == user_id)
                    .order_by(Project.updated_at.desc())
                )
            ).all()
        )

    async def get(self, project_id: UUID, user_id: UUID | None = None) -> Project | None:
        stmt = select(Project).where(Project.id == project_id)
        if user_id:
            stmt = stmt.where(Project.user_id == user_id)
        return await self.session.scalar(stmt)

    async def _server_for_user(self, server_id: UUID, user_id: UUID) -> MonitoredServer:
        server = await self.session.scalar(
            select(MonitoredServer).where(
                MonitoredServer.id == server_id, MonitoredServer.user_id == user_id
            )
        )
        if server is None:
            raise ValueError("selected server was not found")
        return server

    @staticmethod
    def database_profile_from_url(value: str) -> DatabaseProfileDTO:
        parsed = urlsplit(value)
        if (
            parsed.scheme not in {"postgresql", "postgres"}
            or not parsed.hostname
            or not parsed.path.strip("/")
            or not parsed.username
        ):
            raise ValueError("database_url must be a complete PostgreSQL connection URL")
        return DatabaseProfileDTO(
            host=parsed.hostname,
            port=parsed.port or 5432,
            database=unquote(parsed.path.strip("/")),
            username=unquote(parsed.username),
            password=unquote(parsed.password or ""),
            sslmode=dict(parse_qsl(parsed.query)).get("sslmode", "prefer"),
        )

    async def create(self, user_id: UUID, dto: ProjectCreateDTO) -> Project:
        dto = ProjectCreateDTO.model_validate(dto.model_dump())
        server = await self._server_for_user(dto.server_id, user_id)
        project = Project(
            user_id=user_id,
            server_id=server.id,
            name=dto.name,
            description=dto.description,
            environment=dto.environment,
            enabled=dto.enabled,
            timezone=dto.timezone,
            poll_interval=dto.poll_interval,
        )
        try:
            self.session.add(project)
            await self.session.flush()
            await self._replace_children(project.id, dto)
            await self.session.commit()
            await self._reconcile_prometheus()
            await self.session.refresh(project)
            return project
        except Exception:
            await self.session.rollback()
            raise

    async def update(
        self, project_id: UUID, user_id: UUID, dto: ProjectCreateDTO
    ) -> Project | None:
        project = await self.get(project_id, user_id)
        if not project:
            return None
        dto = ProjectCreateDTO.model_validate(dto.model_dump())
        server = await self._server_for_user(dto.server_id, user_id)
        try:
            project.name, project.description, project.environment = (
                dto.name,
                dto.description,
                dto.environment,
            )
            project.enabled, project.timezone, project.poll_interval, project.server_id = (
                dto.enabled,
                dto.timezone,
                dto.poll_interval,
                server.id,
            )
            await self._replace_children(project.id, dto)
            await self.session.commit()
            await self._reconcile_prometheus()
            await self.session.refresh(project)
            return project
        except Exception:
            await self.session.rollback()
            raise

    async def create_python(self, user_id: UUID, dto: PythonProjectOnboardDTO) -> Project:
        server = await self._server_for_user(dto.server_id, user_id)
        database = self.database_profile_from_url(dto.database_url)
        config = ProjectCreateDTO(
            name=dto.name,
            server_id=server.id,
            description=dto.description,
            environment="production",
            enabled=dto.enabled,
            poll_interval=dto.poll_interval,
            metrics_sources=[MetricsSourceDTO(name="app", url=dto.metrics_url, enabled=True)],
            log_sources=[
                LogSourceDTO(
                    path="docker://auto",
                    parser_config={
                        "target_urls": [dto.metrics_url],
                        "compose_project": dto.compose_project,
                        "services": dto.compose_services,
                    },
                    enabled=True,
                )
            ],
            database_profiles=[database],
        )
        return await self.create(user_id, config)

    async def delete(self, project_id: UUID, user_id: UUID) -> bool:
        project = await self.get(project_id, user_id)
        if not project:
            return False
        await self.session.delete(project)
        await self.session.commit()
        await self._reconcile_prometheus()
        return True

    async def _reconcile_prometheus(self) -> None:
        try:
            from oncall.integrations.prometheus_api import PrometheusProvisioner

            await PrometheusProvisioner(self.session).reconcile()
        except Exception:
            logger.exception("unable to reconcile Prometheus configuration")

    async def _replace_children(self, project_id: UUID, dto: ProjectCreateDTO) -> None:
        async def sync(model, rows, fields, identity_fields=()):
            existing = {
                x.id: x
                for x in (
                    await self.session.scalars(select(model).where(model.project_id == project_id))
                ).all()
            }
            existing_by_identity = (
                {
                    tuple(getattr(x, field) for field in identity_fields): x
                    for x in existing.values()
                }
                if identity_fields
                else {}
            )
            kept: set[UUID] = set()
            for row_dto in rows:
                row_id = getattr(row_dto, "id", None)
                row = existing.get(row_id) if row_id else None
                if row is None and identity_fields:
                    row = existing_by_identity.get(
                        tuple(getattr(row_dto, field) for field in identity_fields)
                    )
                if row is None:
                    row = model(project_id=project_id)
                    self.session.add(row)
                elif row.id:
                    kept.add(row.id)
                for field in fields:
                    setattr(row, field, getattr(row_dto, field))
            for key, row in existing.items():
                if key not in kept and not any(getattr(x, "id", None) == key for x in rows):
                    await self.session.delete(row)

        existing_sources = {
            x.id: x
            for x in (
                await self.session.scalars(
                    select(ProjectMetricsSource).where(
                        ProjectMetricsSource.project_id == project_id
                    )
                )
            ).all()
        }
        for source in dto.metrics_sources:
            row = existing_sources.get(source.id) if source.id else None
            if row is None:
                row = ProjectMetricsSource(project_id=project_id)
                self.session.add(row)
            for field in (
                "name",
                "url",
                "auth_type",
                "scrape_timeout_ms",
                "route_label",
                "enabled",
                "service_id",
            ):
                setattr(row, field, getattr(source, field))
            if source.token:
                row.encrypted_token = self.box.encrypt(source.token)
        keep_source_ids = {x.id for x in dto.metrics_sources if x.id}
        for key, row in existing_sources.items():
            if key not in keep_source_ids:
                await self.session.delete(row)

        await sync(
            ProjectLogSource, dto.log_sources, ("path", "encoding", "parser_config", "enabled")
        )
        existing_db = {
            x.id: x
            for x in (
                await self.session.scalars(
                    select(ProjectDatabaseProfile).where(
                        ProjectDatabaseProfile.project_id == project_id
                    )
                )
            ).all()
        }
        for profile in dto.database_profiles:
            row = existing_db.get(profile.id) if profile.id else None
            if row is None:
                row = ProjectDatabaseProfile(project_id=project_id)
                self.session.add(row)
            for field in ("type", "host", "port", "database", "username", "sslmode", "enabled"):
                setattr(row, field, getattr(profile, field))
            if profile.password is not None:
                row.encrypted_password = self.box.encrypt(profile.password)
        keep_db_ids = {x.id for x in dto.database_profiles if x.id}
        for key, row in existing_db.items():
            if key not in keep_db_ids:
                await self.session.delete(row)

    async def runtime_config(
        self, project_id: UUID, *, include_disabled: bool = False
    ) -> ProjectRuntimeConfig:
        project = await self.session.get(Project, project_id)
        if not project:
            raise KeyError(f"project {project_id} not found")

        async def all_for(model):
            stmt = select(model).where(model.project_id == project_id)
            if not include_disabled:
                stmt = stmt.where(model.enabled.is_(True))
            return list((await self.session.scalars(stmt)).all())

        sources = await all_for(ProjectMetricsSource)
        logs = await all_for(ProjectLogSource)
        databases = await all_for(ProjectDatabaseProfile)
        server = await self.session.get(MonitoredServer, project.server_id)
        return ProjectRuntimeConfig(
            id=project.id,
            user_id=project.user_id,
            server_id=project.server_id,
            server=MonitoredServerDTO(
                id=server.id,
                name=server.name,
                node_metrics_url=server.node_metrics_url,
                gpu_metrics_url=server.gpu_metrics_url,
                container_metrics_url=server.container_metrics_url,
                collector_url=server.collector_url,
                collector_token=self.box.decrypt(server.encrypted_collector_token),
                enabled=server.enabled,
                created_at=server.created_at,
                updated_at=server.updated_at,
            )
            if server
            else None,
            name=project.name,
            description=project.description,
            environment=project.environment,
            enabled=project.enabled,
            timezone=project.timezone,
            poll_interval=project.poll_interval,
            metrics_sources=[
                MetricsSourceDTO(
                    id=x.id,
                    service_id=x.service_id,
                    name=x.name,
                    url=x.url,
                    auth_type=x.auth_type,
                    token=self.box.decrypt(x.encrypted_token),
                    scrape_timeout_ms=x.scrape_timeout_ms,
                    route_label=x.route_label,
                    enabled=x.enabled,
                )
                for x in sources
            ],
            log_sources=[
                LogSourceDTO(
                    id=x.id,
                    path=x.path,
                    encoding=x.encoding,
                    parser_config=x.parser_config,
                    enabled=x.enabled,
                )
                for x in logs
            ],
            database_profiles=[
                DatabaseProfileDTO(
                    id=x.id,
                    type=x.type,
                    host=x.host,
                    port=x.port,
                    database=x.database,
                    username=x.username,
                    password=self.box.decrypt(x.encrypted_password),
                    sslmode=x.sslmode,
                    enabled=x.enabled,
                )
                for x in databases
            ],
        )
