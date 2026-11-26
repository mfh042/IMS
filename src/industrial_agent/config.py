from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

RolloutMode = Literal["shadow", "low_auto", "medium_auto", "high_controlled"]


class Settings(BaseSettings):
    """全局配置，全部可通过环境变量（前缀 IAMS_）覆盖。"""

    model_config = SettingsConfigDict(env_prefix="IAMS_", env_file=".env", extra="ignore")

    tenant_id: str = "factory_a"
    env: str = "dev"

    dedup_window_seconds: int = Field(default=300, ge=1)
    max_concurrent_incidents: int = Field(default=8, ge=1)
    per_entity_incident_limit: int = Field(default=2, ge=1)
    priority_wait_weight: float = Field(default=0.5, ge=0)
    priority_queue_ttl_seconds: int = Field(default=900, ge=1)

    tool_timeout_seconds: float = Field(default=5.0, gt=0)
    confirm_ttl_seconds: int = Field(default=3600, ge=1)
    confirm_escalate_ratio: float = Field(default=0.5, ge=0, le=1)

    rollout_mode: RolloutMode = "shadow"
    knowledge_strict_metadata: bool = True

    audit_dir: Path = Path("var/audit")
    knowledge_path: Path = Path("var/knowledge")

    llm_enabled: bool = False
    llm_model: str = "gpt-4o-mini"
    llm_api_key: str | None = None
    llm_base_url: str | None = None
    llm_timeout_seconds: float = Field(default=30.0, gt=0)

    def ensure_dirs(self) -> None:
        self.audit_dir.mkdir(parents=True, exist_ok=True)
        self.knowledge_path.mkdir(parents=True, exist_ok=True)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()