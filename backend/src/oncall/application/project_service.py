from __future__ import annotations

from urllib.parse import parse_qsl, unquote, urlsplit
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from oncall.application.dtos import (
    DatabaseProfileDTO,
    LogSourceDTO,
    MetricsSourceDTO,
    MonitoredServerDTO,
    MonitoringRuleDTO,
    ProjectCreateDTO,
    ProjectRuntimeConfig,
    PythonProjectOnboardDTO,
    ServiceEndpointDTO,
)
from oncall.bootstrap.config import get_settings
from oncall.infrastructure.db.models import (
    MonitoredServer,
    MonitoringRule,
    Project,
    ProjectDatabaseProfile,
    ProjectLogSource,
    ProjectMetricsSource,
    ProjectServiceEndpoint,
)
from oncall.security.crypto import SecretBox


def default_remote_python_rules(*, has_gpu: bool, has_health: bool, has_metrics: bool, has_logs: bool = True, has_database: bool = True) -> list[MonitoringRuleDTO]:
    """Build the only supported starter rule set from available data sources."""
    rules = [
        MonitoringRuleDTO(metric_key='host.exporter.up', operator='==', trigger_threshold=0, recovery_threshold=0, trigger_for=3, recovery_for=2, severity='critical'),
        MonitoringRuleDTO(metric_key='host.cpu.percent', operator='>', trigger_threshold=95, recovery_threshold=85, trigger_for=5, recovery_for=3, severity='warning'),
        MonitoringRuleDTO(metric_key='host.memory.percent', operator='>', trigger_threshold=95, recovery_threshold=85, trigger_for=5, recovery_for=3, severity='warning'),
        MonitoringRuleDTO(metric_key='host.disk.usage_percent', operator='>', trigger_threshold=90, recovery_threshold=85, trigger_for=3, recovery_for=3, severity='warning'),
    ]
    if has_gpu:
        rules.extend([
            MonitoringRuleDTO(metric_key='host.gpu.exporter.up', operator='==', trigger_threshold=0, recovery_threshold=0, trigger_for=3, recovery_for=2, severity='warning'),
            MonitoringRuleDTO(metric_key='host.gpu.memory_percent', operator='>', trigger_threshold=95, recovery_threshold=85, trigger_for=5, recovery_for=3, severity='warning'),
            MonitoringRuleDTO(metric_key='host.gpu.temperature_celsius', operator='>', trigger_threshold=90, recovery_threshold=80, trigger_for=3, recovery_for=3, severity='critical'),
        ])
    if has_health:
        rules.extend([
            MonitoringRuleDTO(metric_key='service.consecutive_failures', operator='>=', trigger_threshold=3, recovery_threshold=1, trigger_for=1, recovery_for=2, severity='critical'),
            MonitoringRuleDTO(metric_key='service.latency_ms', operator='>', trigger_threshold=1500, recovery_threshold=800, trigger_for=4, recovery_for=3, severity='warning', detection_mode='hybrid'),
        ])
    if has_metrics:
        rules.extend([
            MonitoringRuleDTO(metric_key='app.up', operator='==', trigger_threshold=0, recovery_threshold=0, trigger_for=3, recovery_for=2, severity='critical'),
            MonitoringRuleDTO(metric_key='app.http.error_rate', operator='>', trigger_threshold=0.1, recovery_threshold=0.05, trigger_for=3, recovery_for=2, severity='warning', conditions={'all': [
                {'metric_key': 'app.http.rps', 'resource_key': 'default', 'operator': '>', 'threshold': 5.0},
                {'metric_key': 'app.http.error_rate', 'resource_key': 'default', 'operator': '>', 'threshold': 0.1},
            ]}),
            MonitoringRuleDTO(metric_key='app.http.p95_ms', operator='>', trigger_threshold=1500, recovery_threshold=800, trigger_for=3, recovery_for=3, severity='warning', detection_mode='hybrid'),
            MonitoringRuleDTO(metric_key='app.http.p99_ms', operator='>', trigger_threshold=3000, recovery_threshold=1500, trigger_for=3, recovery_for=3, severity='warning', detection_mode='hybrid'),
            MonitoringRuleDTO(metric_key='app.http.availability', operator='<', trigger_threshold=95, recovery_threshold=99, trigger_for=3, recovery_for=2, severity='warning'),
            MonitoringRuleDTO(metric_key='process.target.cpu_percent_sum', operator='>', trigger_threshold=10000, recovery_threshold=9000, trigger_for=5, recovery_for=3, severity='warning', detection_mode='baseline'),
            MonitoringRuleDTO(metric_key='process.target.rss_bytes_sum', operator='>', trigger_threshold=1e15, recovery_threshold=9e14, trigger_for=5, recovery_for=3, severity='warning', detection_mode='baseline'),
        ])
    if has_logs:
        rules.extend([
            MonitoringRuleDTO(metric_key='log.collector.up', operator='==', trigger_threshold=0, recovery_threshold=0, trigger_for=3, recovery_for=2, severity='warning'),
            # One exception is evidence, not an incident. Require a burst that
            # persists across two windows before notifying the operator.
            MonitoringRuleDTO(metric_key='log.exception_count', operator='>=', trigger_threshold=10, recovery_threshold=1, trigger_for=2, recovery_for=2, severity='warning'),
        ])
    if has_database:
        rules.extend([
            MonitoringRuleDTO(metric_key='db.up', operator='==', trigger_threshold=0, recovery_threshold=0, trigger_for=2, recovery_for=2, severity='critical'),
            MonitoringRuleDTO(metric_key='db.connections.utilization_percent', operator='>', trigger_threshold=90, recovery_threshold=75, trigger_for=4, recovery_for=3, severity='warning'),
            # A single transaction can legitimately stay open in a worker for
            # more than five minutes. Alert only when several remain open for
            # multiple collection cycles, while keeping the signal available
            # for diagnosis and the snapshot.
            MonitoringRuleDTO(metric_key='db.long_transactions', operator='>=', trigger_threshold=3, recovery_threshold=1, trigger_for=3, recovery_for=2, severity='warning'),
            MonitoringRuleDTO(metric_key='db.lock_waits', operator='>=', trigger_threshold=1, recovery_threshold=0.5, trigger_for=3, recovery_for=2, severity='warning'),
            MonitoringRuleDTO(metric_key='db.replication_lag_seconds', operator='>', trigger_threshold=60, recovery_threshold=20, trigger_for=3, recovery_for=2, severity='critical'),
        ])
    return rules


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

    async def _server_for_user(self, server_id: UUID, user_id: UUID) -> MonitoredServer:
        server = await self.session.scalar(select(MonitoredServer).where(MonitoredServer.id == server_id, MonitoredServer.user_id == user_id))
        if server is None:
            raise ValueError('selected server was not found')
        return server

    @staticmethod
    def database_profile_from_url(value: str) -> DatabaseProfileDTO:
        parsed=urlsplit(value)
        if parsed.scheme not in {'postgresql','postgres'} or not parsed.hostname or not parsed.path.strip('/') or not parsed.username:
            raise ValueError('database_url must be a complete PostgreSQL connection URL')
        return DatabaseProfileDTO(
            host=parsed.hostname,port=parsed.port or 5432,database=unquote(parsed.path.strip('/')),
            username=unquote(parsed.username),password=unquote(parsed.password or ''),
            sslmode=dict(parse_qsl(parsed.query)).get('sslmode','prefer'),
        )

    async def create(self, user_id: UUID, dto: ProjectCreateDTO) -> Project:
        dto = ProjectCreateDTO.model_validate(dto.model_dump())
        server = await self._server_for_user(dto.server_id, user_id)
        self._validate_gpu_rules(dto, server)
        p = Project(user_id=user_id, server_id=server.id, name=dto.name, description=dto.description, environment=dto.environment, enabled=dto.enabled, timezone=dto.timezone, poll_interval=dto.poll_interval)
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
        server = await self._server_for_user(dto.server_id, user_id)
        self._validate_gpu_rules(dto, server)
        try:
            p.name, p.description, p.environment, p.enabled, p.timezone, p.poll_interval, p.server_id = dto.name, dto.description, dto.environment, dto.enabled, dto.timezone, dto.poll_interval, server.id
            await self._replace_children(p.id, dto)
            await self.session.commit()
            await self.session.refresh(p)
            return p
        except Exception:
            await self.session.rollback()
            raise

    @staticmethod
    def _validate_gpu_rules(dto: ProjectCreateDTO, server: MonitoredServer) -> None:
        if server.gpu_metrics_url:
            return
        if any(rule.enabled and rule.metric_key.startswith('host.gpu.') for rule in dto.rules):
            raise ValueError('GPU rules require a configured GPU metrics URL on the selected server')

    async def create_python(self, user_id: UUID, dto: PythonProjectOnboardDTO) -> Project:
        server = await self._server_for_user(dto.server_id, user_id)
        db=self.database_profile_from_url(dto.database_url)
        config = ProjectCreateDTO(
            name=dto.name,
            server_id=server.id,
            description=dto.description,
            environment='production',
            enabled=dto.enabled,
            poll_interval=dto.poll_interval,
            service_endpoints=[ServiceEndpointDTO(name=f'{dto.name} 健康检查', url=dto.health_url, enabled=True)],
            metrics_sources=[MetricsSourceDTO(name='app', url=dto.metrics_url, enabled=True)],
            log_sources=[LogSourceDTO(path='docker://auto',parser_config={
                'target_urls':[dto.health_url,dto.metrics_url],
                'compose_project': dto.compose_project,
                'services': dto.compose_services,
            },enabled=True)],
            database_profiles=[db],
            rules=default_remote_python_rules(has_gpu=bool(server.gpu_metrics_url), has_health=True, has_metrics=True, has_logs=True, has_database=True),
        )
        return await self.create(user_id, config)

    async def delete(self, project_id: UUID, user_id: UUID) -> bool:
        p = await self.get(project_id, user_id)
        if not p:
            return False
        await self.session.delete(p)
        await self.session.commit()
        return True

    async def _replace_children(self, project_id: UUID, dto: ProjectCreateDTO) -> None:
        async def sync(model, rows, fields, identity_fields=()):
            existing = {x.id: x for x in (await self.session.scalars(select(model).where(model.project_id == project_id))).all()}
            existing_by_identity = {
                tuple(getattr(x, field) for field in identity_fields): x
                for x in existing.values()
            } if identity_fields else {}
            kept_existing_ids = set()
            for dto_row in rows:
                row_id = getattr(dto_row, 'id', None)
                obj = existing.get(row_id) if row_id else None
                if obj is None and identity_fields:
                    obj = existing_by_identity.get(tuple(getattr(dto_row, field) for field in identity_fields))
                if obj is None:
                    obj = model(project_id=project_id)
                    self.session.add(obj)
                elif obj.id is not None:
                    kept_existing_ids.add(obj.id)
                for field in fields:
                    setattr(obj, field, getattr(dto_row, field))
            for key, obj in existing.items():
                if key not in kept_existing_ids:
                    await self.session.delete(obj)

        existing_ms = {x.id: x for x in (await self.session.scalars(select(ProjectMetricsSource).where(ProjectMetricsSource.project_id == project_id))).all()}
        for source in dto.metrics_sources:
            obj = existing_ms.get(source.id) if source.id else None
            if obj is None:
                obj = ProjectMetricsSource(project_id=project_id)
                self.session.add(obj)
            for field in ('name', 'url', 'auth_type', 'scrape_timeout_ms', 'route_label', 'enabled', 'service_id'):
                setattr(obj, field, getattr(source, field))
            if source.token:
                obj.encrypted_token = self.box.encrypt(source.token)
        for key, obj in existing_ms.items():
            if key not in {source.id for source in dto.metrics_sources if source.id}:
                await self.session.delete(obj)

        await sync(ProjectServiceEndpoint, dto.service_endpoints, ('name', 'url', 'method', 'expected_status', 'timeout_ms', 'enabled', 'service_id'))
        await sync(ProjectLogSource, dto.log_sources, ('path', 'encoding', 'parser_config', 'enabled'))
        existing_db = {x.id: x for x in (await self.session.scalars(select(ProjectDatabaseProfile).where(ProjectDatabaseProfile.project_id == project_id))).all()}
        for profile in dto.database_profiles:
            obj=existing_db.get(profile.id) if profile.id else None
            if obj is None:
                obj=ProjectDatabaseProfile(project_id=project_id);self.session.add(obj)
            for field in ('type','host','port','database','username','sslmode','enabled'):
                setattr(obj,field,getattr(profile,field))
            if profile.password is not None:
                obj.encrypted_password=self.box.encrypt(profile.password)
        keep={x.id for x in dto.database_profiles if x.id}
        for key,obj in existing_db.items():
            if key not in keep:await self.session.delete(obj)
        await sync(MonitoringRule, dto.rules, ('metric_key', 'resource_key', 'operator', 'trigger_threshold', 'trigger_for', 'recovery_threshold', 'recovery_for', 'severity', 'enabled', 'conditions', 'detection_mode', 'baseline_window', 'baseline_min_samples', 'baseline_z_score', 'baseline_recovery_z_score'), identity_fields=('metric_key', 'resource_key'))

    async def runtime_config(self, project_id: UUID, *, include_disabled: bool = False) -> ProjectRuntimeConfig:
        p = await self.session.get(Project, project_id)
        if not p:
            raise KeyError(f'project {project_id} not found')

        async def all_for(model):
            stmt = select(model).where(model.project_id == project_id)
            if not include_disabled:
                stmt = stmt.where(model.enabled.is_(True))
            return list((await self.session.scalars(stmt)).all())

        endpoints = await all_for(ProjectServiceEndpoint)
        sources = await all_for(ProjectMetricsSource)
        logs = await all_for(ProjectLogSource)
        databases = await all_for(ProjectDatabaseProfile)
        rules = await all_for(MonitoringRule)
        server = await self.session.get(MonitoredServer, p.server_id)
        return ProjectRuntimeConfig(
            id=p.id,
            user_id=p.user_id,
            server_id=p.server_id,
            server=MonitoredServerDTO(id=server.id, name=server.name, node_metrics_url=server.node_metrics_url, gpu_metrics_url=server.gpu_metrics_url, collector_url=server.collector_url, collector_token=self.box.decrypt(server.encrypted_collector_token), enabled=server.enabled, created_at=server.created_at, updated_at=server.updated_at) if server else None,
            name=p.name,
            description=p.description,
            environment=p.environment,
            enabled=p.enabled,
            timezone=p.timezone,
            poll_interval=p.poll_interval,
            service_endpoints=[ServiceEndpointDTO(id=x.id, service_id=x.service_id, name=x.name, url=x.url, method=x.method, expected_status=x.expected_status, timeout_ms=x.timeout_ms, enabled=x.enabled) for x in endpoints],
            metrics_sources=[MetricsSourceDTO(id=x.id, service_id=x.service_id, name=x.name, url=x.url, auth_type=x.auth_type, token=self.box.decrypt(x.encrypted_token), scrape_timeout_ms=x.scrape_timeout_ms, route_label=x.route_label, enabled=x.enabled) for x in sources],
            log_sources=[LogSourceDTO(id=x.id,path=x.path,encoding=x.encoding,parser_config=x.parser_config,enabled=x.enabled) for x in logs],
            database_profiles=[DatabaseProfileDTO(id=x.id,type=x.type,host=x.host,port=x.port,database=x.database,username=x.username,password=self.box.decrypt(x.encrypted_password),sslmode=x.sslmode,enabled=x.enabled) for x in databases],
            rules=[MonitoringRuleDTO(id=x.id, metric_key=x.metric_key, resource_key=x.resource_key, operator=x.operator, trigger_threshold=x.trigger_threshold, trigger_for=x.trigger_for, recovery_threshold=x.recovery_threshold, recovery_for=x.recovery_for, severity=x.severity, enabled=x.enabled, conditions=x.conditions, detection_mode=x.detection_mode, baseline_window=x.baseline_window, baseline_min_samples=x.baseline_min_samples, baseline_z_score=x.baseline_z_score, baseline_recovery_z_score=x.baseline_recovery_z_score) for x in rules],
        )
