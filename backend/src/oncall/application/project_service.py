from __future__ import annotations

from uuid import UUID

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from oncall.application.dtos import (
    DatabaseProfileDTO,
    DockerTargetDTO,
    LogSourceDTO,
    MonitoringRuleDTO,
    ProcessTargetDTO,
    ProjectCreateDTO,
    ProjectRuntimeConfig,
    ServiceEndpointDTO,
)
from oncall.bootstrap.config import get_settings
from oncall.infrastructure.db.models import (
    MonitoringRule,
    Project,
    ProjectDatabaseProfile,
    ProjectDockerTarget,
    ProjectLogSource,
    ProjectProcessTarget,
    ProjectServiceEndpoint,
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
        # Full replacement keeps the Project API simple, but secrets must survive an
        # edit where the browser intentionally sends password=null instead of exposing
        # the stored plaintext. Preserve encrypted DB passwords by child id.
        existing_db = list((await self.session.scalars(select(ProjectDatabaseProfile).where(ProjectDatabaseProfile.project_id == project_id))).all())
        encrypted_by_id = {x.id: x.encrypted_password for x in existing_db}
        for model in (ProjectProcessTarget, ProjectLogSource, ProjectDockerTarget, ProjectDatabaseProfile, ProjectServiceEndpoint, MonitoringRule):
            await self.session.execute(delete(model).where(model.project_id == project_id))
        for x in dto.process_targets:
            self.session.add(ProjectProcessTarget(project_id=project_id, **x.model_dump(exclude={'id'})))
        for x in dto.log_sources:
            self.session.add(ProjectLogSource(project_id=project_id, **x.model_dump(exclude={'id'})))
        for x in dto.docker_targets:
            self.session.add(ProjectDockerTarget(project_id=project_id, **x.model_dump(exclude={'id'})))
        for x in dto.database_profiles:
            data=x.model_dump(exclude={'id','password'})
            if x.password:
                data['encrypted_password']=self.box.encrypt(x.password)
            elif x.id and x.id in encrypted_by_id:
                data['encrypted_password']=encrypted_by_id[x.id]
            else:
                data['encrypted_password']=None
            self.session.add(ProjectDatabaseProfile(project_id=project_id, **data))
        for x in dto.service_endpoints:
            self.session.add(ProjectServiceEndpoint(project_id=project_id, **x.model_dump(exclude={'id'})))
        for x in dto.rules:
            data=x.model_dump(exclude={'id'}); data['operator']=data.pop('operator')
            self.session.add(MonitoringRule(project_id=project_id, **data))

    async def runtime_config(self, project_id: UUID) -> ProjectRuntimeConfig:
        p = await self.session.get(Project, project_id)
        if not p:
            raise KeyError(f'project {project_id} not found')
        async def all_for(model):
            return list((await self.session.scalars(select(model).where(model.project_id == project_id, model.enabled.is_(True)))).all())
        processes = await all_for(ProjectProcessTarget)
        logs = await all_for(ProjectLogSource)
        docks = await all_for(ProjectDockerTarget)
        dbs = await all_for(ProjectDatabaseProfile)
        eps = await all_for(ProjectServiceEndpoint)
        rules = await all_for(MonitoringRule)
        return ProjectRuntimeConfig(
            id=p.id, user_id=p.user_id, name=p.name, description=p.description, enabled=p.enabled,
            timezone=p.timezone, poll_interval=p.poll_interval,
            process_targets=[ProcessTargetDTO(id=x.id,name=x.name,executable=x.executable,cmdline_filters=x.cmdline_filters,cwd=x.cwd,port=x.port,enabled=x.enabled) for x in processes],
            log_sources=[LogSourceDTO(id=x.id,path=x.path,encoding=x.encoding,parser_config=x.parser_config,enabled=x.enabled) for x in logs],
            docker_targets=[DockerTargetDTO(id=x.id,container_ref=x.container_ref,enabled=x.enabled) for x in docks],
            database_profiles=[DatabaseProfileDTO(id=x.id,type=x.type,host=x.host,port=x.port,database=x.database,username=x.username,password=self.box.decrypt(x.encrypted_password),sslmode=x.sslmode,enabled=x.enabled) for x in dbs],
            service_endpoints=[ServiceEndpointDTO(id=x.id,name=x.name,url=x.url,method=x.method,expected_status=x.expected_status,timeout_ms=x.timeout_ms,enabled=x.enabled) for x in eps],
            rules=[MonitoringRuleDTO(id=x.id,metric_key=x.metric_key,resource_key=x.resource_key,operator=x.operator,trigger_threshold=x.trigger_threshold,trigger_for=x.trigger_for,recovery_threshold=x.recovery_threshold,recovery_for=x.recovery_for,severity=x.severity,enabled=x.enabled) for x in rules],
        )
