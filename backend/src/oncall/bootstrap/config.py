from __future__ import annotations

import re
from pathlib import Path
from typing import Literal

from dotenv import dotenv_values
from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_prefix="ONCALL_", extra="ignore")

    env: str = "development"
    host: str = "127.0.0.1"
    port: int = 9900
    log_level: str = "INFO"
    log_dir: Path = Path("./logs")
    log_retention_days: int = Field(default=2, ge=1, le=30)
    database_url: str = "postgresql+asyncpg://oncall:oncall@127.0.0.1:5432/oncall"
    langgraph_database_url: str = "postgresql://oncall:oncall@127.0.0.1:5432/oncall"
    milvus_uri: str = "http://127.0.0.1:19530"
    milvus_token: str = "root:Milvus"
    data_dir: Path = Path("./data")
    secret_master_key: str = ""
    model_provider: str = "openai-compatible"
    model_display_name: str = "大语言模型"
    model_base_url: str = "https://api.siliconflow.cn/v1"
    model_api_key: str = ""
    model_name: str = "deepseek-ai/DeepSeek-V4-Flash"
    embedding_base_url: str = "https://api.siliconflow.cn/v1"
    embedding_api_key: str = ""
    embedding_model: str = "BAAI/bge-m3"
    embedding_dimension: int = 1024
    rerank_base_url: str = "https://api.siliconflow.cn/v1/rerank"
    rerank_api_key: str = ""
    rerank_model: str = "BAAI/bge-reranker-v2-m3"
    feishu_enabled: bool = False
    feishu_app_id: str = ""
    feishu_app_secret: str = ""
    feishu_default_receive_id: str = ""
    feishu_default_receive_id_type: Literal["chat_id", "open_id", "user_id", "union_id"] = "chat_id"
    feishu_ws_initial_retry_seconds: float = Field(default=2.0, ge=0.1, le=60)
    feishu_ws_max_retry_seconds: float = Field(default=120.0, ge=1, le=900)
    feishu_event_claim_seconds: int = Field(default=300, ge=10, le=86400)
    feishu_event_max_attempts: int = Field(default=5, ge=1, le=100)
    feishu_outbox_claim_seconds: int = Field(default=300, ge=10, le=86400)
    web_origin: str = "http://127.0.0.1:5173"
    knowledge_max_upload_mb: int = Field(default=50, ge=1, le=500)
    # Expand a retrieved hit with adjacent chunks before sending context to the model.
    knowledge_context_radius: int = Field(default=1, ge=0, le=3)
    knowledge_context_max_chars: int = Field(default=2400, ge=800, le=12000)
    notification_cooldown_seconds: int = Field(default=1800, ge=0)
    # Escalations are relative to the first firing: T+2h and T+5h by default.
    incident_reminder_first_seconds: int = Field(default=7200, ge=60)
    incident_reminder_final_seconds: int = Field(default=18000, ge=120)
    # A recovered alert that fires again in this window reopens the same
    # Incident/Conversation instead of creating a second conversation.
    incident_reopen_cooldown_seconds: int = Field(default=7200, ge=0)
    job_lease_seconds: int = 120
    job_poll_seconds: float = 1.0
    # Alert delivery runs in its own worker. Keep this short so a fresh alert lands
    # within a second or two of being queued, independent of Agent activity.
    notification_poll_seconds: float = 1.0
    prometheus_url: str = "http://127.0.0.1:9090"
    prometheus_reload_url: str = ""
    prometheus_scrape_base_url: str = "http://127.0.0.1:9900"
    prometheus_scrape_token: str = ""
    prometheus_proxy_application_scrapes: bool = True
    alertmanager_webhook_token: str = ""
    langgraph_strict_msgpack: bool = True

    @model_validator(mode="after")
    def validate_production_safety(self):
        if self.incident_reminder_final_seconds <= self.incident_reminder_first_seconds:
            raise ValueError("incident final reminder must be later than first reminder")
        if self.env.lower() == "production":
            if not self.secret_master_key:
                raise ValueError("ONCALL_SECRET_MASTER_KEY is required in production")
            if self.model_provider != "mock" and not self.model_api_key:
                raise ValueError("real model provider requires ONCALL_MODEL_API_KEY")
            if self.feishu_enabled and not (self.feishu_app_id and self.feishu_app_secret):
                raise ValueError("Feishu is enabled but app credentials are incomplete")
        return self

    @property
    def knowledge_dir(self) -> Path:
        return self.data_dir / "knowledge"

    @property
    def upload_dir(self) -> Path:
        return self.data_dir / "uploads"

    def ensure_dirs(self) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.knowledge_dir.mkdir(parents=True, exist_ok=True)
        self.upload_dir.mkdir(parents=True, exist_ok=True)
        self.log_dir.mkdir(parents=True, exist_ok=True)


def update_env_values(values: dict[str, str]) -> None:
    """Update selected ONCALL_* values without exposing secrets to the API response."""
    env_path = Path(".env")
    content = env_path.read_text(encoding="utf-8") if env_path.exists() else ""
    for key, value in values.items():
        pattern = rf"(?m)^\s*#?\s*{re.escape(key)}\s*=.*$"
        replacement = f"{key}={value}"
        if re.search(pattern, content):
            content = re.sub(pattern, replacement, content)
        else:
            if content and not content.endswith("\n"):
                content += "\n"
            content += replacement + "\n"
    env_path.write_text(content, encoding="utf-8", newline="\n")


_cached_settings: Settings | None = None
_cached_settings_mtime_ns: int | None = None


def get_settings() -> Settings:
    """Cache settings until the shared .env changes, including across workers.

    Docker Compose also injects ``env_file`` values into each process at
    container startup.  Normal BaseSettings precedence would keep those stale
    process values above a subsequently edited bind-mounted .env.  Explicit
    init values from the shared file deliberately win here, so a model switch
    made in the UI reaches API, Agent and RAG workers without container restarts.
    """
    global _cached_settings, _cached_settings_mtime_ns
    env_file = Path(".env")
    try:
        mtime_ns = env_file.stat().st_mtime_ns
    except OSError:
        mtime_ns = None
    if _cached_settings is None or mtime_ns != _cached_settings_mtime_ns:
        file_values = dotenv_values(env_file) if env_file.exists() else {}
        overrides = {
            field_name: file_values[env_key]
            for field_name in Settings.model_fields
            if (env_key := f"ONCALL_{field_name.upper()}") in file_values
            and file_values[env_key] is not None
        }
        settings = Settings(**overrides)
        settings.ensure_dirs()
        _cached_settings = settings
        _cached_settings_mtime_ns = mtime_ns
    return _cached_settings
