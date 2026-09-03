from __future__ import annotations

from datetime import datetime
from math import isfinite
from typing import Any, Literal
from urllib.parse import urlsplit
from uuid import UUID

from pydantic import BaseModel, Field, field_validator, model_validator

from oncall.monitoring.signals import BASELINE_SIGNALS

SUPPORTED_ENCODINGS = {'utf-8', 'gbk', 'gb2312', 'utf-16', 'latin-1'}
SUPPORTED_SSL_MODES = {'disable', 'prefer', 'require', 'verify-ca', 'verify-full'}
# White-box application metrics scraped from a Prometheus text-format /metrics
# endpoint (e.g. a FastAPI app instrumented with prometheus-fastapi-instrumentator).
# Kept separate from BASELINE_SIGNALS so the 32-signal contract tests stay stable.
APP_HTTP_SIGNALS = (
    'app.up',
    'app.http.rps',
    'app.http.error_rate',
    'app.http.p95_ms',
    'app.http.p99_ms',
    'app.http.availability',
)
SUPPORTED_METRICS = frozenset([*BASELINE_SIGNALS, *APP_HTTP_SIGNALS])
METRIC_FAMILY_TARGETS = {
    'process.': 'process_targets',
    'log.': 'log_sources',
    'container.': 'docker_targets',
    'db.': 'database_profiles',
    'service.': 'service_endpoints',
    'app.': 'metrics_sources',
}
METRIC_RANGES = {
    'host.cpu.percent': (0, 100),
    'host.memory.percent': (0, 100),
    'host.disk.usage_percent': (0, 100),
    'process.target.alive': (0, 1),
    'container.running': (0, 1),
    'container.health': (-1, 1),
    'db.reachable': (0, 1),
    'service.reachable': (0, 1),
    'service.status_code': (0, 599),
    'app.up': (0, 1),
    'app.http.rps': (0, 100000),
    'app.http.error_rate': (0, 1),
    'app.http.p95_ms': (0, 600000),
    'app.http.p99_ms': (0, 600000),
    'app.http.availability': (0, 100),
}


class ServiceDTO(BaseModel):
    """A logical application/component that groups related monitoring targets."""
    id: UUID | None = None
    name: str = 'service'
    description: str = ''
    enabled: bool = True

    @field_validator('name')
    @classmethod
    def service_name_not_blank(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError('service name must not be blank')
        return value


class ServiceCreateDTO(BaseModel):
    # Optional id lets an update match an existing service in place. Without it,
    # every save would recreate services and cascade SET NULL onto bound targets.
    id: UUID | None = None
    name: str = 'service'
    description: str = ''
    enabled: bool = True

    @field_validator('name')
    @classmethod
    def service_create_name(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError('service name must not be blank')
        return value


class ProcessTargetDTO(BaseModel):
    id: UUID | None = None
    service_id: UUID | None = None
    name: str = 'target'
    executable: str | None = None
    cmdline_filters: list[str] = Field(default_factory=list)
    cwd: str | None = None
    port: int | None = Field(default=None, ge=1, le=65535)
    enabled: bool = True

    @field_validator('name')
    @classmethod
    def name_not_blank(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError('process target name must not be blank')
        return value

    @field_validator('executable', 'cwd')
    @classmethod
    def optional_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip()
        return value or None

    @field_validator('cmdline_filters')
    @classmethod
    def normalize_filters(cls, value: list[str]) -> list[str]:
        return [item.strip() for item in value if item and item.strip()]

    @model_validator(mode='after')
    def has_process_selector(self) -> ProcessTargetDTO:
        if not (self.executable or self.cmdline_filters or self.cwd):
            raise ValueError('process target needs executable, cmdline_filters, or cwd')
        return self


class LogSourceDTO(BaseModel):
    id: UUID | None = None
    service_id: UUID | None = None
    path: str
    encoding: str = 'utf-8'
    parser_config: dict[str, Any] = Field(default_factory=dict)
    enabled: bool = True

    @field_validator('path', 'encoding')
    @classmethod
    def text_not_blank(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError('log source value must not be blank')
        return value

    @field_validator('encoding')
    @classmethod
    def encoding_allowed(cls, value: str) -> str:
        value = value.lower()
        if value not in SUPPORTED_ENCODINGS:
            raise ValueError(f'unsupported log encoding: {value}')
        return value


class DockerTargetDTO(BaseModel):
    id: UUID | None = None
    service_id: UUID | None = None
    container_ref: str
    enabled: bool = True

    @field_validator('container_ref')
    @classmethod
    def container_ref_not_blank(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError('container_ref must not be blank')
        return value


class DatabaseProfileDTO(BaseModel):
    id: UUID | None = None
    service_id: UUID | None = None
    type: str = 'postgresql'
    host: str = '127.0.0.1'
    port: int = Field(default=5432, ge=1, le=65535)
    database: str
    username: str
    password: str | None = None
    sslmode: str = 'prefer'
    enabled: bool = True

    @field_validator('type', 'host', 'database', 'username', 'sslmode')
    @classmethod
    def database_text_not_blank(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError('database profile values must not be blank')
        return value

    @field_validator('type')
    @classmethod
    def database_type_supported(cls, value: str) -> str:
        if value.lower() != 'postgresql':
            raise ValueError('only postgresql database profiles are supported')
        return 'postgresql'

    @field_validator('sslmode')
    @classmethod
    def sslmode_supported(cls, value: str) -> str:
        value = value.lower()
        if value not in SUPPORTED_SSL_MODES:
            raise ValueError(f'unsupported database sslmode: {value}')
        return value


class ServiceEndpointDTO(BaseModel):
    id: UUID | None = None
    service_id: UUID | None = None
    name: str = 'health'
    url: str
    method: str = 'GET'
    expected_status: int = Field(default=200, ge=100, le=599)
    timeout_ms: int = Field(default=3000, ge=100, le=60000)
    enabled: bool = True

    @field_validator('name', 'url')
    @classmethod
    def endpoint_text_not_blank(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError('service endpoint values must not be blank')
        return value

    @field_validator('url')
    @classmethod
    def http_url_supported(cls, value: str) -> str:
        parts = urlsplit(value)
        if parts.scheme not in {'http', 'https'} or not parts.netloc:
            raise ValueError('service endpoint url must be an absolute http(s) URL')
        return value

    @field_validator('method')
    @classmethod
    def method_upper(cls, value: str) -> str:
        value = value.strip().upper()
        if value not in {'GET', 'HEAD', 'POST', 'PUT'}:
            raise ValueError('unsupported service endpoint method')
        return value


class MetricsSourceDTO(BaseModel):
    """A Prometheus text-format scrape target for one application (white-box metrics)."""
    id: UUID | None = None
    service_id: UUID | None = None
    name: str = 'app'
    url: str
    auth_type: Literal['none', 'bearer', 'basic'] = 'none'
    # bearer token or basic password; never serialized back on read (model stores ciphertext)
    token: str | None = None
    scrape_timeout_ms: int = Field(default=5000, ge=100, le=60000)
    # Label whose values identify individual routes for per-route drill-down.
    # FastAPI instrumentator -> "handler"; Spring Boot -> "uri"; Django -> "view".
    route_label: str = 'handler'
    enabled: bool = True

    @field_validator('name', 'url', 'route_label')
    @classmethod
    def metrics_text_not_blank(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError('metrics source values must not be blank')
        return value

    @field_validator('url')
    @classmethod
    def metrics_http_url(cls, value: str) -> str:
        parts = urlsplit(value)
        if parts.scheme not in {'http', 'https'} or not parts.netloc:
            raise ValueError('metrics source url must be an absolute http(s) URL')
        return value

    @field_validator('auth_type')
    @classmethod
    def metrics_auth_allowed(cls, value: str) -> str:
        value = value.strip().lower()
        if value not in {'none', 'bearer', 'basic'}:
            raise ValueError('unsupported metrics source auth_type')
        return value


class MetricsDiscoverDTO(BaseModel):
    """Input for the /metrics/discover probe: where to scrape and how to auth."""
    url: str
    auth_type: Literal['none', 'bearer', 'basic'] = 'none'
    token: str | None = None
    route_label: str = 'handler'
    scrape_timeout_ms: int = Field(default=5000, ge=100, le=60000)

    @field_validator('url')
    @classmethod
    def discover_http_url(cls, value: str) -> str:
        parts = urlsplit(value.strip())
        if parts.scheme not in {'http', 'https'} or not parts.netloc:
            raise ValueError('metrics source url must be an absolute http(s) URL')
        return value.strip()

    @field_validator('route_label')
    @classmethod
    def discover_route_label(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError('route_label must not be blank')
        return value


class MetricsApplyDTO(BaseModel):
    """A discovered metrics source plus the rules to persist for a project."""
    source: MetricsSourceDTO
    rules: list[MonitoringRuleDTO] = Field(default_factory=list)


class MonitoringRuleDTO(BaseModel):
    id: UUID | None = None
    metric_key: str
    resource_key: str = 'default'
    operator: str = '>'
    trigger_threshold: float
    trigger_for: int = Field(default=2, ge=1, le=100)
    recovery_threshold: float
    recovery_for: int = Field(default=2, ge=1, le=100)
    severity: str = 'warning'
    enabled: bool = True
    # Composite rule (P4). When set, the scalar metric_key/operator/threshold
    # fields above are ignored and firing requires ALL conditions to hold.
    conditions: dict | None = None

    @field_validator('metric_key', 'resource_key')
    @classmethod
    def rule_text_not_blank(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError('rule metric/resource must not be blank')
        return value

    @field_validator('operator')
    @classmethod
    def operator_allowed(cls, value: str) -> str:
        value = value.strip()
        if value not in {'>', '<', '>=', '<=', '==', '!='}:
            raise ValueError('unsupported monitoring rule operator')
        return value

    @field_validator('severity')
    @classmethod
    def severity_allowed(cls, value: str) -> str:
        value = value.strip().lower()
        if value not in {'info', 'warning', 'critical'}:
            raise ValueError('unsupported monitoring rule severity')
        return value

    @field_validator('trigger_threshold', 'recovery_threshold')
    @classmethod
    def threshold_finite(cls, value: float) -> float:
        if not isfinite(value):
            raise ValueError('rule thresholds must be finite numbers')
        return value

    @model_validator(mode='after')
    def validate_conditions(self) -> MonitoringRuleDTO:
        if self.conditions is None:
            return self
        conds = self.conditions
        if not isinstance(conds, dict):
            raise ValueError('conditions must be an object')
        group = conds.get('all')
        if not isinstance(group, list) or not group:
            raise ValueError("conditions.all must be a non-empty list of condition objects")
        seen: set[tuple[str, str]] = set()
        for idx, c in enumerate(group):
            if not isinstance(c, dict):
                raise ValueError(f'conditions.all[{idx}] must be an object')
            mk = str(c.get('metric_key', '')).strip()
            if mk not in SUPPORTED_METRICS and not mk.startswith('zz.test.'):
                raise ValueError(f'conditions.all[{idx}] uses unknown metric: {mk}')
            rk = str(c.get('resource_key', 'default')).strip() or 'default'
            if (mk, rk) in seen:
                raise ValueError(f'conditions.all[{idx}] duplicates metric/resource {mk}/{rk}')
            seen.add((mk, rk))
            op = str(c.get('operator', '')).strip()
            if op not in {'>', '<', '>=', '<=', '==', '!='}:
                raise ValueError(f'conditions.all[{idx}] has unsupported operator: {op}')
            try:
                thr = float(c.get('threshold'))
            except (TypeError, ValueError):
                raise ValueError(f'conditions.all[{idx}] threshold must be a number')
            if not isfinite(thr):
                raise ValueError(f'conditions.all[{idx}] threshold must be finite')
        esc = conds.get('escalate_at')
        if esc is not None:
            if not isinstance(esc, dict):
                raise ValueError('conditions.escalate_at must be an object')
            emk = str(esc.get('metric_key', '')).strip()
            if emk not in SUPPORTED_METRICS and not emk.startswith('zz.test.'):
                raise ValueError(f'conditions.escalate_at uses unknown metric: {emk}')
            eop = str(esc.get('operator', '')).strip()
            if eop not in {'>', '<', '>=', '<=', '==', '!='}:
                raise ValueError('conditions.escalate_at has unsupported operator')
            try:
                float(esc.get('threshold'))
            except (TypeError, ValueError):
                raise ValueError('conditions.escalate_at threshold must be a number')
            esev = str(esc.get('escalate_severity', 'critical')).strip().lower()
            if esev not in {'info', 'warning', 'critical'}:
                raise ValueError('conditions.escalate_at.escalate_severity must be info/warning/critical')
        return self

    @model_validator(mode='after')
    def thresholds_are_consistent(self) -> MonitoringRuleDTO:
        if self.conditions is not None:
            return self  # composite rules ignore the scalar threshold fields
        bounds = METRIC_RANGES.get(self.metric_key)
        if bounds:
            low, high = bounds
            if not low <= self.trigger_threshold <= high:
                raise ValueError(f'trigger threshold for {self.metric_key} must be between {low} and {high}')
            if not low <= self.recovery_threshold <= high:
                raise ValueError(f'recovery threshold for {self.metric_key} must be between {low} and {high}')
        if self.operator in {'>', '>='} and self.recovery_threshold >= self.trigger_threshold:
            raise ValueError('recovery threshold must be lower than trigger threshold for a high-water rule')
        if self.operator in {'<', '<='} and self.recovery_threshold <= self.trigger_threshold:
            raise ValueError('recovery threshold must be higher than trigger threshold for a low-water rule')
        if self.operator == '==' and self.recovery_threshold != self.trigger_threshold:
            raise ValueError('equal rules must use the same trigger and recovery threshold')
        return self


class ProjectCreateDTO(BaseModel):
    name: str
    description: str = ''
    enabled: bool = True
    timezone: str = 'Asia/Shanghai'
    poll_interval: int = Field(default=300, ge=10, le=86400)
    process_targets: list[ProcessTargetDTO] = Field(default_factory=list)
    log_sources: list[LogSourceDTO] = Field(default_factory=list)
    docker_targets: list[DockerTargetDTO] = Field(default_factory=list)
    database_profiles: list[DatabaseProfileDTO] = Field(default_factory=list)
    service_endpoints: list[ServiceEndpointDTO] = Field(default_factory=list)
    metrics_sources: list[MetricsSourceDTO] = Field(default_factory=list)
    services: list[ServiceCreateDTO] = Field(default_factory=list)
    rules: list[MonitoringRuleDTO] = Field(default_factory=list)

    @field_validator('name')
    @classmethod
    def project_name_not_blank(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError('project name must not be blank')
        return value

    @field_validator('timezone')
    @classmethod
    def timezone_not_blank(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError('timezone must not be blank')
        return value

    @model_validator(mode='after')
    def validate_configuration(self) -> ProjectCreateDTO:
        errors: list[str] = []
        collections = {
            'process_targets': self.process_targets,
            'log_sources': self.log_sources,
            'docker_targets': self.docker_targets,
            'database_profiles': self.database_profiles,
            'service_endpoints': self.service_endpoints,
            'metrics_sources': self.metrics_sources,
        }
        for name, rows in collections.items():
            if len({row.id for row in rows if row.id is not None}) != len([row.id for row in rows if row.id is not None]):
                errors.append(f'{name} contains duplicate ids')

        if len({s.name for s in self.services}) != len([s.name for s in self.services]):
            errors.append('services contains duplicate names')

        seen_rules: set[tuple[str, str]] = set()
        for index, rule in enumerate(self.rules, start=1):
            # Composite rules (P4): validate each condition instead of the scalar fields.
            if rule.conditions is not None:
                for c in rule.conditions.get('all', []):
                    metric = str(c.get('metric_key', ''))
                    rk = str(c.get('resource_key', 'default')) or 'default'
                    if metric not in SUPPORTED_METRICS and not metric.startswith('zz.test.'):
                        errors.append(f'rule #{index} condition uses unknown metric: {metric}')
                        continue
                    for prefix, target_field in METRIC_FAMILY_TARGETS.items():
                        if not metric.startswith(prefix):
                            continue
                        if rk == 'default':
                            if not any(row.enabled for row in getattr(self, target_field)):
                                errors.append(f'rule #{index} condition requires an enabled {target_field} target')
                        elif rk.startswith('route:'):
                            if target_field == 'metrics_sources':
                                break
                            errors.append(f'rule #{index} condition resource {rk} is only valid for metrics_sources')
                        else:
                            targets = getattr(self, target_field)
                            keys = {str(row.id) for row in targets if row.id is not None}
                            keys.update(str(getattr(row, 'name', '') or getattr(row, 'container_ref', '') or getattr(row, 'database', '') or getattr(row, 'path', '') or getattr(row, 'url', '')) for row in targets)
                            if rk not in keys:
                                errors.append(f'rule #{index} condition resource does not match a configured {target_field} target')
                        break
                key = ('composite', str(index))
                if key in seen_rules:
                    errors.append(f'rule #{index} duplicates a composite rule')
                seen_rules.add(key)
                continue

            metric = rule.metric_key
            # zz.test.* is reserved for the detector's synthetic integration tests.
            if metric not in SUPPORTED_METRICS and not metric.startswith('zz.test.'):
                errors.append(f'rule #{index} uses unknown metric: {metric}')
            key = (metric, rule.resource_key)
            if key in seen_rules:
                errors.append(f'rule #{index} duplicates metric/resource {metric}/{rule.resource_key}')
            seen_rules.add(key)
            for prefix, target_field in METRIC_FAMILY_TARGETS.items():
                if metric.startswith(prefix) and rule.enabled and not any(row.enabled for row in getattr(self, target_field)):
                    errors.append(f'rule #{index} requires an enabled {target_field} target')
                    break
                if metric.startswith(prefix) and rule.enabled and rule.resource_key != 'default':
                    # Per-route white-box metrics carry a dynamic resource_key such as
                    # "route:/health" that is not a configured target id/name.
                    if target_field == 'metrics_sources' and rule.resource_key.startswith('route:'):
                        break
                    targets = getattr(self, target_field)
                    keys = {str(row.id) for row in targets if row.id is not None}
                    keys.update(str(getattr(row, 'name', '') or getattr(row, 'container_ref', '') or getattr(row, 'database', '') or getattr(row, 'path', '') or getattr(row, 'url', '')) for row in targets)
                    if rule.resource_key not in keys:
                        errors.append(f'rule #{index} resource does not match a configured {target_field} target')
                    break

        if errors:
            raise ValueError('; '.join(errors))
        return self


class ProjectRuntimeConfig(ProjectCreateDTO):
    id: UUID
    user_id: UUID
    # Read-side: services carry generated ids (ProjectCreateDTO uses ServiceCreateDTO).
    services: list[ServiceDTO] = Field(default_factory=list)


class ConversationCreateDTO(BaseModel):
    title: str = '新会话'
    project_id: UUID | None = None


class ConversationPatchDTO(BaseModel):
    title: str | None = None
    archived: bool | None = None


class ChatMessageDTO(BaseModel):
    content: str = Field(min_length=1, max_length=30000)
    channel: str = 'web'


class PasswordChangeDTO(BaseModel):
    current_password: str = Field(min_length=1, max_length=200)
    new_password: str = Field(min_length=8, max_length=200)
    confirm_password: str = Field(min_length=8, max_length=200)

    @model_validator(mode='after')
    def passwords_match(self) -> PasswordChangeDTO:
        if self.new_password != self.confirm_password:
            raise ValueError('new password and confirmation do not match')
        return self


class FeishuSettingsDTO(BaseModel):
    enabled: bool = False
    app_id: str = Field(default='', max_length=200)
    app_secret: str | None = Field(default=None, max_length=500)
    default_receive_id: str = Field(default='', max_length=255)
    default_receive_id_type: Literal['chat_id', 'open_id', 'user_id', 'union_id'] = 'chat_id'


class SnapshotDTO(BaseModel):
    project_id: UUID
    observed_at: datetime
    signals: dict[str, float | bool | str | None] = Field(default_factory=dict)
    resource_signals: dict[str, dict[str, float | bool | str | None]] = Field(default_factory=dict)
    resources: dict[str, Any] = Field(default_factory=dict)
    collector_status: dict[str, Any] = Field(default_factory=dict)
