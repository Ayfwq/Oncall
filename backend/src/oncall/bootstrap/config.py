from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file='.env', env_prefix='ONCALL_', extra='ignore')

    env: str = 'development'
    host: str = '127.0.0.1'
    port: int = 9900
    log_level: str = 'INFO'
    database_url: str = 'postgresql+asyncpg://oncall:oncall@127.0.0.1:5432/oncall'
    langgraph_database_url: str = 'postgresql://oncall:oncall@127.0.0.1:5432/oncall'
    milvus_uri: str = 'http://127.0.0.1:19530'
    milvus_token: str = 'root:Milvus'
    data_dir: Path = Path('./data')
    secret_master_key: str = ''
    admin_username: str = 'admin'
    admin_password: str = 'change-me-now'
    model_provider: str = 'mock'
    model_base_url: str = 'https://api.openai.com/v1'
    model_api_key: str = ''
    model_name: str = 'gpt-4.1-mini'
    embedding_base_url: str = ''
    embedding_api_key: str = ''
    embedding_model: str = 'text-embedding-3-small'
    embedding_dimension: int = 1536
    knowledge_index_version: str = 'v1'
    rerank_base_url: str = ''
    rerank_api_key: str = ''
    rerank_model: str = ''
    feishu_enabled: bool = False
    feishu_app_id: str = ''
    feishu_app_secret: str = ''
    feishu_default_receive_id: str = ''
    feishu_default_receive_id_type: Literal['chat_id', 'open_id', 'user_id', 'union_id'] = 'chat_id'
    feishu_ws_initial_retry_seconds: float = Field(default=2.0, ge=0.1, le=60)
    feishu_ws_max_retry_seconds: float = Field(default=120.0, ge=1, le=900)
    feishu_event_claim_seconds: int = Field(default=300, ge=10, le=86400)
    feishu_event_max_attempts: int = Field(default=5, ge=1, le=100)
    feishu_outbox_claim_seconds: int = Field(default=300, ge=10, le=86400)
    web_origin: str = 'http://127.0.0.1:5173'
    monitor_default_interval_seconds: int = Field(default=300, ge=10)
    metric_retention_days: int = Field(default=30, ge=1)
    knowledge_max_upload_mb: int = Field(default=50, ge=1, le=500)
    incident_stale_reinvestigate_seconds: int = Field(default=3600, ge=300)
    notification_cooldown_seconds: int = Field(default=1800, ge=0)
    session_days: int = 30
    job_lease_seconds: int = 120
    job_poll_seconds: float = 1.0
    langgraph_strict_msgpack: bool = True

    @model_validator(mode='after')
    def validate_production_safety(self):
        if self.env.lower()=='production':
            if not self.secret_master_key:
                raise ValueError('ONCALL_SECRET_MASTER_KEY is required in production')
            if self.admin_password=='change-me-now':
                raise ValueError('default admin password is forbidden in production')
            if self.model_provider!='mock' and not self.model_api_key:
                raise ValueError('real model provider requires ONCALL_MODEL_API_KEY')
            if self.feishu_enabled and not (self.feishu_app_id and self.feishu_app_secret):
                raise ValueError('Feishu is enabled but app credentials are incomplete')
        return self

    @property
    def knowledge_dir(self) -> Path:
        return self.data_dir / 'knowledge'

    @property
    def upload_dir(self) -> Path:
        return self.data_dir / 'uploads'

    def ensure_dirs(self) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.knowledge_dir.mkdir(parents=True, exist_ok=True)
        self.upload_dir.mkdir(parents=True, exist_ok=True)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    settings = Settings()
    settings.ensure_dirs()
    return settings
