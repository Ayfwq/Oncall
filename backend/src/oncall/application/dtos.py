from __future__ import annotations

from datetime import datetime
from typing import Any, Literal
from urllib.parse import urlsplit
from uuid import UUID

from pydantic import BaseModel, Field, field_validator, model_validator

from oncall.monitoring.signals import PYTHON_GPU_SIGNALS, PYTHON_SIGNALS

# Only the remote-Python contract is accepted. The monitored server exporters
# supply host/GPU/container telemetry and the project supplies Prometheus URLs.
APP_HTTP_SIGNALS = tuple(x for x in PYTHON_SIGNALS if x.startswith("app."))
HOST_GPU_SIGNALS = PYTHON_GPU_SIGNALS
HOST_EXPORTER_SIGNALS = tuple(x for x in PYTHON_SIGNALS if x.startswith("host."))
PROCESS_PROM_SIGNALS = tuple(x for x in PYTHON_SIGNALS if x.startswith("process."))


class MonitoredServerCreateDTO(BaseModel):
    name: str
    node_metrics_url: str
    gpu_metrics_url: str | None = None
    container_metrics_url: str
    collector_url: str | None = None
    collector_token: str | None = None
    enabled: bool = True

    @field_validator("name")
    @classmethod
    def server_name_not_blank(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("server name must not be blank")
        return value

    @field_validator("node_metrics_url", "container_metrics_url")
    @classmethod
    def required_server_metrics_url(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("node_metrics_url and container_metrics_url are required")
        parts = urlsplit(value)
        if parts.scheme not in {"http", "https"} or not parts.netloc:
            raise ValueError("server metrics URL must be an absolute http(s) URL")
        return value

    @field_validator("gpu_metrics_url", "collector_url")
    @classmethod
    def optional_server_metrics_url(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip()
        if not value:
            return None
        parts = urlsplit(value)
        if parts.scheme not in {"http", "https"} or not parts.netloc:
            raise ValueError("server metrics URL must be an absolute http(s) URL")
        return value


class MonitoredServerDTO(MonitoredServerCreateDTO):
    id: UUID
    created_at: datetime | None = None
    updated_at: datetime | None = None


class LogSourceDTO(BaseModel):
    id: UUID | None = None
    path: str = "docker://auto"
    encoding: str = "utf-8"
    parser_config: dict[str, Any] = Field(default_factory=dict)
    enabled: bool = True


class DatabaseProfileDTO(BaseModel):
    id: UUID | None = None
    type: Literal["postgresql"] = "postgresql"
    host: str
    port: int = Field(default=5432, ge=1, le=65535)
    database: str
    username: str
    password: str | None = None
    sslmode: Literal["disable", "allow", "prefer", "require", "verify-ca", "verify-full"] = "prefer"
    enabled: bool = True

    @field_validator("host", "database", "username")
    @classmethod
    def database_text_not_blank(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("database connection values must not be blank")
        return value


class MetricsSourceDTO(BaseModel):
    """A Prometheus text-format scrape target for one application (white-box metrics)."""

    id: UUID | None = None
    service_id: UUID | None = None
    name: str = "app"
    url: str
    auth_type: Literal["none", "bearer", "basic"] = "none"
    # bearer token or basic password; never serialized back on read (model stores ciphertext)
    token: str | None = None
    scrape_timeout_ms: int = Field(default=5000, ge=100, le=60000)
    # Label whose values identify individual routes for per-route drill-down.
    # FastAPI instrumentator -> "handler"; Spring Boot -> "uri"; Django -> "view".
    route_label: str = "handler"
    enabled: bool = True

    @field_validator("name", "url", "route_label")
    @classmethod
    def metrics_text_not_blank(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("metrics source values must not be blank")
        return value

    @field_validator("url")
    @classmethod
    def metrics_http_url(cls, value: str) -> str:
        parts = urlsplit(value)
        if parts.scheme not in {"http", "https"} or not parts.netloc:
            raise ValueError("metrics source url must be an absolute http(s) URL")
        return value

    @field_validator("auth_type")
    @classmethod
    def metrics_auth_allowed(cls, value: str) -> str:
        value = value.strip().lower()
        if value not in {"none", "bearer", "basic"}:
            raise ValueError("unsupported metrics source auth_type")
        return value


class MetricsDiscoverDTO(BaseModel):
    """Input for the /metrics/discover probe: where to scrape and how to auth."""

    url: str
    auth_type: Literal["none", "bearer", "basic"] = "none"
    token: str | None = None
    route_label: str = "handler"
    scrape_timeout_ms: int = Field(default=5000, ge=100, le=60000)

    @field_validator("url")
    @classmethod
    def discover_http_url(cls, value: str) -> str:
        parts = urlsplit(value.strip())
        if parts.scheme not in {"http", "https"} or not parts.netloc:
            raise ValueError("metrics source url must be an absolute http(s) URL")
        return value.strip()

    @field_validator("route_label")
    @classmethod
    def discover_route_label(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("route_label must not be blank")
        return value


class MetricsApplyDTO(BaseModel):
    """A Prometheus scrape source to persist for a project."""

    source: MetricsSourceDTO


class ProjectCreateDTO(BaseModel):
    name: str
    server_id: UUID
    description: str = ""
    environment: Literal["production"] = "production"
    enabled: bool = True
    timezone: str = "Asia/Shanghai"
    poll_interval: int = Field(default=30, ge=10, le=86400)
    metrics_sources: list[MetricsSourceDTO] = Field(default_factory=list)
    log_sources: list[LogSourceDTO] = Field(default_factory=list)
    database_profiles: list[DatabaseProfileDTO] = Field(default_factory=list)

    @field_validator("name")
    @classmethod
    def project_name_not_blank(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("project name must not be blank")
        return value

    @field_validator("timezone")
    @classmethod
    def timezone_not_blank(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("timezone must not be blank")
        return value

    @model_validator(mode="after")
    def validate_configuration(self) -> ProjectCreateDTO:
        for name, rows in {"metrics_sources": self.metrics_sources}.items():
            ids = [row.id for row in rows if row.id is not None]
            if len(set(ids)) != len(ids):
                raise ValueError(f"{name} contains duplicate ids")
        return self


class PythonProjectOnboardDTO(BaseModel):
    """Remote-only onboarding contract for a Python service.

    A saved server supplies host/GPU/container telemetry; the project metrics
    URL supplies API golden signals and optional Python process telemetry.
    """

    name: str
    server_id: UUID
    description: str = ""
    environment: Literal["production"] = "production"
    metrics_url: str
    database_url: str
    compose_project: str | None = None
    compose_services: list[str] = Field(default_factory=list, max_length=50)
    poll_interval: int = Field(default=30, ge=10, le=86400)
    enabled: bool = False

    @field_validator("name")
    @classmethod
    def onboard_name_not_blank(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("project name must not be blank")
        return value

    @field_validator("metrics_url", "database_url")
    @classmethod
    def onboard_url_trim(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("URL must not be blank")
        return value

    @model_validator(mode="after")
    def has_observation_target(self) -> PythonProjectOnboardDTO:
        # Keep an explicit model-level validation so API clients receive a
        # stable domain error even if the fields become optional in the future.
        if not self.metrics_url or not self.database_url:
            raise ValueError(
                "metrics_url and database_url are required for remote Python onboarding"
            )
        return self


class ProjectRuntimeConfig(ProjectCreateDTO):
    id: UUID
    user_id: UUID
    server: MonitoredServerDTO | None = None


class ConversationCreateDTO(BaseModel):
    title: str = "新会话"
    project_id: UUID | None = None


class ConversationPatchDTO(BaseModel):
    title: str | None = None
    archived: bool | None = None


class ChatMessageDTO(BaseModel):
    content: str = Field(min_length=1, max_length=30000)
    channel: str = "web"


class IncidentBatchDeleteDTO(BaseModel):
    ids: list[UUID] = Field(min_length=1, max_length=200)


class FeishuSettingsDTO(BaseModel):
    enabled: bool = False
    app_id: str = Field(default="", max_length=200)
    app_secret: str | None = Field(default=None, max_length=500)
    default_receive_id: str = Field(default="", max_length=255)
    default_receive_id_type: Literal["chat_id", "open_id", "user_id", "union_id"] = "chat_id"


class SnapshotDTO(BaseModel):
    project_id: UUID
    observed_at: datetime
    signals: dict[str, float | bool | str | None] = Field(default_factory=dict)
    resource_signals: dict[str, dict[str, float | bool | str | None]] = Field(default_factory=dict)
    resources: dict[str, Any] = Field(default_factory=dict)
    collector_status: dict[str, Any] = Field(default_factory=dict)
