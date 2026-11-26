"""阶段 4.2 / 4.3 工具契约与风险等级定义。"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

ToolKind = Literal["read", "write", "control"]
ToolRisk = Literal["low", "medium", "high"]


class ToolContext(BaseModel):
    """阶段 4.3 工具调用上下文。"""

    trace_id: str
    incident_id: str | None = None
    actor: str = "system"
    roles: list[str] = Field(default_factory=list)
    tenant_id: str = "factory_a"
    allow_high_risk: bool = False


class ToolResult(BaseModel):
    """阶段 4.3 工具统一返回。"""

    ok: bool
    data: dict | list | None = None
    error_code: str | None = None
    error_message: str | None = None
    source: str = "tool"
    fetched_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    degraded: bool = False


@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    kind: ToolKind
    risk: ToolRisk
    required_roles: tuple[str, ...] = ()
    retryable: bool = False
    timeout_seconds: float = 5.0
    handler: Callable[..., Any] = field(default=None)  # type: ignore[assignment]

    @property
    def auto_executable(self) -> bool:
        return self.risk != "high"