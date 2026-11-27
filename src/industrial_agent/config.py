from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

RolloutMode = Literal["shadow", "low_auto", "medium_auto", "high_controlled"]


@dataclass(frozen=True)
class RolloutPolicy:
    """阶段 10.3 / 10.4 灰度策略：写开关、中风险自动、高风险受控。"""

    allow_write: bool
    allow_medium_auto: bool
    allow_high_confirm: bool


ROLLOUT_POLICY: dict[str, RolloutPolicy] = {
    # 阶段 A 影子模式：生成方案但不执行任何写操作
    "shadow": RolloutPolicy(allow_write=False, allow_medium_auto=False, allow_high_confirm=False),
    # 阶段 B 低风险自动：开放查询、诊断记录、工单草稿、通知
    "low_auto": RolloutPolicy(allow_write=True, allow_medium_auto=False, allow_high_confirm=False),
    # 阶段 C 中风险闭环：待办确认流上线
    "medium_auto": RolloutPolicy(allow_write=True, allow_medium_auto=True, allow_high_confirm=False),
    # 阶段 D 高风险受控：审批后方可执行
    "high_controlled": RolloutPolicy(
        allow_write=True, allow_medium_auto=True, allow_high_confirm=True
    ),
}


def rollout_policy(mode: str) -> RolloutPolicy:
    """未知模式一律按最保守的影子模式处理。"""

    return ROLLOUT_POLICY.get(mode, ROLLOUT_POLICY["shadow"])


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
    # 阶段 12.5：first 收敛到最小可用范围（B3/A3/B4 + A1/B1 只读取证）；all 放开全量路由
    agent_release_scope: str = "first"

    audit_dir: Path = Path("var/audit")
    knowledge_path: Path = Path("var/knowledge")
    # 持久化：主图检查点 SQLite（interrupt 挂起状态跨进程可恢复）+ 人工确认请求 JSON（重启不丢待确认项）
    checkpoint_db_path: Path = Path("var/checkpoints/checkpoints.db")
    confirm_store_path: Path = Path("var/confirms/confirms.json")

    # API 鉴权：默认开启；api_keys 为空时所有受保护路径返回 401，/api/health 等探活路径豁免
    api_auth_enabled: bool = True
    api_keys: list[str] = Field(default_factory=list)

    llm_enabled: bool = False
    llm_model: str = "gpt-4o-mini"
    llm_api_key: str | None = None
    llm_base_url: str | None = None
    llm_timeout_seconds: float = Field(default=30.0, gt=0)

    def ensure_dirs(self) -> None:
        self.audit_dir.mkdir(parents=True, exist_ok=True)
        self.knowledge_path.mkdir(parents=True, exist_ok=True)
        self.checkpoint_db_path.parent.mkdir(parents=True, exist_ok=True)
        self.confirm_store_path.parent.mkdir(parents=True, exist_ok=True)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()