from __future__ import annotations

from datetime import datetime
from math import isfinite
from typing import Any, Literal
from urllib.parse import urlsplit
from uuid import UUID

from pydantic import BaseModel, Field, field_validator, model_validator

from oncall.monitoring.signals import PYTHON_GPU_SIGNALS, PYTHON_SIGNALS, SUPPORTED_SIGNALS

# Only the remote-Python contract is accepted. The monitored server exporter
# supplies host/GPU telemetry and the project supplies health/Prometheus URLs.
APP_HTTP_SIGNALS = tuple(x for x in PYTHON_SIGNALS if x.startswith('app.'))
HOST_GPU_SIGNALS = PYTHON_GPU_SIGNALS
HOST_EXPORTER_SIGNALS = tuple(x for x in PYTHON_SIGNALS if x.startswith('host.'))
PROCESS_PROM_SIGNALS = tuple(x for x in PYTHON_SIGNALS if x.startswith('process.'))
SUPPORTED_METRICS = SUPPORTED_SIGNALS
METRIC_RANGES = {
    'host.cpu.percent': (0, 100),
    'host.memory.percent': (0, 100),
    'host.disk.usage_percent': (0, 100),
    'service.reachable': (0, 1),
    'service.status_code': (0, 599),
    'app.up': (0, 1),
    'app.http.rps': (0, 100000),
    'app.http.error_rate': (0, 1),
    'app.http.p95_ms': (0, 600000),
    'app.http.p99_ms': (0, 600000),
    'app.http.availability': (0, 100),
    'host.gpu.exporter.up': (0, 1),
    'host.gpu.utilization_percent': (0, 100),
    'host.gpu.memory_percent': (0, 100),
    'host.gpu.temperature_celsius': (0, 150),
    'host.gpu.available': (0, 1),
    'host.exporter.up': (0, 1),
}


class MonitoredServerCreateDTO(BaseModel):
    name: str
    node_metrics_url: str
    gpu_metrics_url: str | None = None
    enabled: bool = True

    @field_validator('name')
    @classmethod
    def server_name_not_blank(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError('server name must not be blank')
        return value

    @field_validator('node_metrics_url', 'gpu_metrics_url')
    @classmethod
    def server_metrics_url(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip()
        if not value:
            return None
        parts = urlsplit(value)
        if parts.scheme not in {'http', 'https'} or not parts.netloc:
            raise ValueError('server metrics URL must be an absolute http(s) URL')
        return value


class MonitoredServerDTO(MonitoredServerCreateDTO):
    id: UUID
    created_at: datetime | None = None
    updated_at: datetime | None = None


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
    detection_mode: Literal['threshold', 'baseline', 'hybrid'] = 'threshold'
    baseline_window: int = Field(default=60, ge=6, le=10000)
    baseline_min_samples: int = Field(default=12, ge=3, le=10000)
    baseline_z_score: float = Field(default=3.0, gt=0, le=20)
    baseline_recovery_z_score: float = Field(default=2.0, gt=0, le=20)
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
        if self.conditions is not None and self.detection_mode != 'threshold':
            raise ValueError('composite rules only support threshold detection')
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
        if self.detection_mode in {'baseline', 'hybrid'} and self.baseline_recovery_z_score >= self.baseline_z_score:
            raise ValueError('baseline recovery z-score must be lower than trigger z-score')
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
    server_id: UUID
    description: str = ''
    environment: Literal['production'] = 'production'
    enabled: bool = True
    timezone: str = 'Asia/Shanghai'
    poll_interval: int = Field(default=30, ge=10, le=86400)
    service_endpoints: list[ServiceEndpointDTO] = Field(default_factory=list)
    metrics_sources: list[MetricsSourceDTO] = Field(default_factory=list)
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
            'service_endpoints': self.service_endpoints,
            'metrics_sources': self.metrics_sources,
        }
        for name, rows in collections.items():
            if len({row.id for row in rows if row.id is not None}) != len([row.id for row in rows if row.id is not None]):
                errors.append(f'{name} contains duplicate ids')
        seen_rules: set[tuple[str, str]] = set()
        for index, rule in enumerate(self.rules, start=1):
            if rule.conditions is not None:
                for c in rule.conditions.get('all', []):
                    metric = str(c.get('metric_key', ''))
                    rk = str(c.get('resource_key', 'default')) or 'default'
                    if metric not in SUPPORTED_METRICS and not metric.startswith('zz.test.'):
                        errors.append(f'rule #{index} condition uses unknown metric: {metric}')
                        continue
                    if metric.startswith('zz.test.'):
                        continue
                    if rk.startswith('route:') and not metric.startswith('app.'):
                        errors.append(f'rule #{index} condition resource {rk} is only valid for app metrics')
                    elif rk != 'default':
                        source_names = {str(row.id) for row in self.metrics_sources if row.id is not None}
                        source_names.update(row.name for row in self.metrics_sources)
                        if rk not in source_names and not rk.startswith('route:'):
                            errors.append(f'rule #{index} condition resource does not match a configured metrics source')
                key = ('composite', str(index))
                if key in seen_rules:
                    errors.append(f'rule #{index} duplicates a composite rule')
                seen_rules.add(key)
                continue
            metric = rule.metric_key
            if metric not in SUPPORTED_METRICS and not metric.startswith('zz.test.'):
                errors.append(f'rule #{index} uses unknown metric: {metric}')
            key = (metric, rule.resource_key)
            if key in seen_rules:
                errors.append(f'rule #{index} duplicates metric/resource {metric}/{rule.resource_key}')
            seen_rules.add(key)
            if rule.enabled and metric.startswith(('service.', 'app.', 'process.')):
                if metric.startswith('service.') and not any(row.enabled for row in self.service_endpoints):
                    errors.append(f'rule #{index} requires an enabled service_endpoints target')
                if metric.startswith(('app.', 'process.')) and not any(row.enabled for row in self.metrics_sources):
                    errors.append(f'rule #{index} requires an enabled metrics_sources target')
            if rule.enabled and rule.resource_key != 'default' and not metric.startswith('zz.test.'):
                source_names = {str(row.id) for row in self.metrics_sources if row.id is not None}
                source_names.update(row.name for row in self.metrics_sources)
                if not (rule.metric_key.startswith('app.') and rule.resource_key.startswith('route:')) and rule.resource_key not in source_names:
                    errors.append(f'rule #{index} resource does not match a configured metrics source')
        if errors:
            raise ValueError('; '.join(errors))
        return self


class PythonProjectOnboardDTO(BaseModel):
    """Remote-only onboarding contract for a Python service.

    A saved server supplies host/GPU telemetry; the two project URLs supply
    health, API golden signals and optional Python client process telemetry.
    """
    name: str
    server_id: UUID
    description: str = ''
    environment: Literal['production'] = 'production'
    health_url: str
    metrics_url: str
    poll_interval: int = Field(default=30, ge=10, le=86400)
    enabled: bool = False

    @field_validator('name')
    @classmethod
    def onboard_name_not_blank(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError('project name must not be blank')
        return value

    @field_validator('health_url', 'metrics_url')
    @classmethod
    def onboard_url_trim(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError('URL must not be blank')
        return value

    @model_validator(mode='after')
    def has_observation_target(self) -> PythonProjectOnboardDTO:
        # Keep an explicit model-level validation so API clients receive a
        # stable domain error even if the fields become optional in the future.
        if not self.health_url or not self.metrics_url:
            raise ValueError('health_url and metrics_url are required for remote Python onboarding')
        return self

class ProjectRuntimeConfig(ProjectCreateDTO):
    id: UUID
    user_id: UUID
    server: MonitoredServerDTO | None = None


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
    new_password: str = Field(min_length=6, max_length=200)
    confirm_password: str = Field(min_length=6, max_length=200)

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
