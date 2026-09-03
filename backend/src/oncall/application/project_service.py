from __future__ import annotations

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from oncall.application.dtos import (
    DatabaseProfileDTO,
    DockerTargetDTO,
    LogSourceDTO,
    MetricsSourceDTO,
    MonitoringRuleDTO,
    ProcessTargetDTO,
    ProjectCreateDTO,
    ProjectRuntimeConfig,
    ServiceCreateDTO,
    ServiceDTO,
    ServiceEndpointDTO,
)
from oncall.bootstrap.config import get_settings
from oncall.infrastructure.db.models import (
    MonitoringRule,
    Project,
    ProjectDatabaseProfile,
    ProjectDockerTarget,
    ProjectLogSource,
    ProjectMetricsSource,
    ProjectProcessTarget,
    ProjectServiceEndpoint,
    Service,
)
from oncall.security.crypto import SecretBox


class ProjectService:
    def __init__(self, session: AsyncSession):
        self.session = session
        self.box = SecretBox(get_settings().secret_master_key)

    async def list(self, user_id: UUID) -> list[Project]:
        return list((await self.session.scalars(select(Project).where(Project.user_id == user_id).order_by(Project.updated_at.desc()))).all())

    async def get(self, project_id: UUID, user_id: UUID | None = None) -> Project | None:
        stmt = select(Project).where(Project.id == project_id)
        if user_id:
            stmt = stmt.where(Project.user_id == user_id)
        return await self.session.scalar(stmt)

    async def create(self, user_id: UUID, dto: ProjectCreateDTO) -> Project:
        dto = ProjectCreateDTO.model_validate(dto.model_dump())
        p = Project(user_id=user_id, name=dto.name, description=dto.description, enabled=dto.enabled, timezone=dto.timezone, poll_interval=dto.poll_interval)
        try:
            self.session.add(p)
            await self.session.flush()
            await self._replace_children(p.id, dto)
            await self.session.commit()
            await self.session.refresh(p)
            return p
        except Exception:
            await self.session.rollback()
            raise

    async def update(self, project_id: UUID, user_id: UUID, dto: ProjectCreateDTO) -> Project | None:
        p = await self.get(project_id, user_id)
        if not p:
            return None
        dto = ProjectCreateDTO.model_validate(dto.model_dump())
        try:
            p.name, p.description, p.enabled, p.timezone, p.poll_interval = dto.name, dto.description, dto.enabled, dto.timezone, dto.poll_interval
            await self._replace_children(p.id, dto)
            await self.session.commit()
            await self.session.refresh(p)
            return p
        except Exception:
            await self.session.rollback()
            raise

    async def delete(self, project_id: UUID, user_id: UUID) -> bool:
        p = await self.get(project_id, user_id)
        if not p:
            return False
        await self.session.delete(p)
        await self.session.commit()
        return True

    async def _replace_children(self, project_id: UUID, dto: ProjectCreateDTO) -> None:
        # Secrets must survive an edit where the browser intentionally sends
        # password=null instead of exposing the stored plaintext.
        # Update rows in place. Recreating every child on each save used to reset
        # detector hysteresis and change Incident fingerprints even for a harmless
        # project-name edit. Stable IDs are part of the monitoring contract.
        async def sync(model, rows, fields):
            existing = {x.id: x for x in (await self.session.scalars(select(model).where(model.project_id == project_id))).all()}
            for dto_row in rows:
                row_id = getattr(dto_row, 'id', None)
                obj = existing.get(row_id) if row_id else None
                if obj is None:
                    obj = model(project_id=project_id)
                    self.session.add(obj)
                for field in fields:
                    setattr(obj, field, getattr(dto_row, field))
            row_ids = {getattr(x, 'id', None) for x in rows if getattr(x, 'id', None)}
            for key, obj in existing.items():
                if key not in row_ids:
                    await self.session.delete(obj)

        await sync(ProjectProcessTarget, dto.process_targets, ('name','executable','cmdline_filters','cwd','port','enabled','service_id'))
        await sync(ProjectLogSource, dto.log_sources, ('path','encoding','parser_config','enabled','service_id'))
        await sync(ProjectDockerTarget, dto.docker_targets, ('container_ref','enabled','service_id'))

        existing_db = {x.id: x for x in (await self.session.scalars(select(ProjectDatabaseProfile).where(ProjectDatabaseProfile.project_id == project_id))).all()}
        for x in dto.database_profiles:
            obj = existing_db.get(x.id) if x.id else None
            if obj is None:
                obj = ProjectDatabaseProfile(project_id=project_id)
                self.session.add(obj)
            for field in ('type','host','port','database','username','sslmode','enabled','service_id'):
                setattr(obj, field, getattr(x, field))
            if x.password:
                obj.encrypted_password = self.box.encrypt(x.password)
        for key, obj in existing_db.items():
            if key not in {x.id for x in dto.database_profiles if x.id}:
                await self.session.delete(obj)

        existing_ms = {x.id: x for x in (await self.session.scalars(select(ProjectMetricsSource).where(ProjectMetricsSource.project_id == project_id))).all()}
        for x in dto.metrics_sources:
            obj = existing_ms.get(x.id) if x.id else None
            if obj is None:
                obj = ProjectMetricsSource(project_id=project_id)
                self.session.add(obj)
            for field in ('name', 'url', 'auth_type', 'scrape_timeout_ms', 'route_label', 'enabled', 'service_id'):
                setattr(obj, field, getattr(x, field))
            # Secrets must survive an edit where the browser sends token=null.
            if x.token:
                obj.encrypted_token = self.box.encrypt(x.token)
        for key, obj in existing_ms.items():
            if key not in {x.id for x in dto.metrics_sources if x.id}:
                await self.session.delete(obj)

        await sync(ProjectServiceEndpoint, dto.service_endpoints, ('name','url','method','expected_status','timeout_ms','enabled','service_id'))
        await sync(Service, dto.services, ('name','description','enabled'))
        await sync(MonitoringRule, dto.rules, ('metric_key','resource_key','operator','trigger_threshold','trigger_for','recovery_threshold','recovery_for','severity','enabled','conditions'))

    async def runtime_config(self, project_id: UUID, *, include_disabled: bool = False) -> ProjectRuntimeConfig:
        p = await self.session.get(Project, project_id)
        if not p:
            raise KeyError(f'project {project_id} not found')
        async def all_for(model):
            stmt = select(model).where(model.project_id == project_id)
            if not include_disabled:
                stmt = stmt.where(model.enabled.is_(True))
            return list((await self.session.scalars(stmt)).all())
        processes = await all_for(ProjectProcessTarget)
        logs = await all_for(ProjectLogSource)
        docks = await all_for(ProjectDockerTarget)
        dbs = await all_for(ProjectDatabaseProfile)
        eps = await all_for(ProjectServiceEndpoint)
        mss = await all_for(ProjectMetricsSource)
        rules = await all_for(MonitoringRule)
        svc = await all_for(Service)
        return ProjectRuntimeConfig(
            id=p.id, user_id=p.user_id, name=p.name, description=p.description, enabled=p.enabled,
            timezone=p.timezone, poll_interval=p.poll_interval,
            process_targets=[ProcessTargetDTO(id=x.id,service_id=x.service_id,name=x.name,executable=x.executable,cmdline_filters=x.cmdline_filters,cwd=x.cwd,port=x.port,enabled=x.enabled) for x in processes],
            log_sources=[LogSourceDTO(id=x.id,service_id=x.service_id,path=x.path,encoding=x.encoding,parser_config=x.parser_config,enabled=x.enabled) for x in logs],
            docker_targets=[DockerTargetDTO(id=x.id,service_id=x.service_id,container_ref=x.container_ref,enabled=x.enabled) for x in docks],
            database_profiles=[DatabaseProfileDTO(id=x.id,service_id=x.service_id,type=x.type,host=x.host,port=x.port,database=x.database,username=x.username,password=self.box.decrypt(x.encrypted_password),sslmode=x.sslmode,enabled=x.enabled) for x in dbs],
            service_endpoints=[ServiceEndpointDTO(id=x.id,service_id=x.service_id,name=x.name,url=x.url,method=x.method,expected_status=x.expected_status,timeout_ms=x.timeout_ms,enabled=x.enabled) for x in eps],
            metrics_sources=[MetricsSourceDTO(id=x.id,service_id=x.service_id,name=x.name,url=x.url,auth_type=x.auth_type,scrape_timeout_ms=x.scrape_timeout_ms,route_label=x.route_label,enabled=x.enabled) for x in mss],
            services=[ServiceDTO(id=x.id,name=x.name,description=x.description,enabled=x.enabled) for x in svc],
            rules=[MonitoringRuleDTO(id=x.id,metric_key=x.metric_key,resource_key=x.resource_key,operator=x.operator,trigger_threshold=x.trigger_threshold,trigger_for=x.trigger_for,recovery_threshold=x.recovery_threshold,recovery_for=x.recovery_for,severity=x.severity,enabled=x.enabled,conditions=x.conditions) for x in rules],
        )
