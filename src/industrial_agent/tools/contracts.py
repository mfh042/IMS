"""阶段 4.2 / 4.3 工具契约与风险等级定义。

本模块只定义数据契约（不包含执行逻辑），是工具层与上下游之间的稳定接口：
- ToolSpec：工具元数据（frozen，注册后不可变，防止运行时被篡改风险等级）；
- ToolContext：调用上下文（携带 trace / 角色 / 两个许可开关，权限只收紧不放宽）；
- ToolResult：统一返回（下游异常归一化为 ok=False + degraded，不抛异常中断链路）。

设计原则：契约与实现分离。registry / factory / adapters 都依赖这里的类型，
换适配器或换注册表实现都不影响契约本身。
"""

from __future__ import annotations

# ---- 标准库 ----
from collections.abc import Callable          # ToolSpec.handler 的可调用类型
from dataclasses import dataclass, field      # ToolSpec 用 frozen dataclass
from datetime import UTC, datetime            # ToolResult.fetched_at 默认取当前 UTC 时间
from typing import Any, Literal               # ToolKind / ToolRisk 用 Literal 做精确类型约束

# ---- 第三方 ----
from pydantic import BaseModel, Field         # ToolContext / ToolResult 用 pydantic 做校验与序列化

# 工具类别：read 只读查询（无副作用）/ write 有副作用但可回退的写 / control 设备或批次控制（最高风险）
ToolKind = Literal["read", "write", "control"]
# 风险等级：low 可自动执行 / medium 需角色权限 / high 必须人工确认（auto_executable=False）
ToolRisk = Literal["low", "medium", "high"]


class ToolContext(BaseModel):
    """阶段 4.3 工具调用上下文。

    两个许可开关的默认值是"最宽松态"，由调用方按场景收紧——权限只收紧不放宽：
    - ``allow_write``：默认 True；只读取证智能体（A1 / B1）与阶段 10.4 影子模式置 False，
      统一拒绝 write / control 类工具，避免绕过风险分级直接落库。
    - ``allow_high_risk``：默认 False；只有执行层拿到人工确认后才置 True，放开 high 工具。
    """

    trace_id: str                              # 链路追踪 id，贯穿事件→方案→执行→审计全链路
    incident_id: str | None = None             # 关联的问题实例 id（工具调用可能不绑定具体事件）
    actor: str = "system"                      # 调用者标识（system / agent 名 / 用户名），写入审计
    roles: list[str] = Field(default_factory=list)  # 调用者角色，与 ToolSpec.required_roles 比对
    tenant_id: str = "factory_a"               # 租户 id（多工厂隔离用，当前单租户示例）
    allow_high_risk: bool = False              # 是否允许执行 high 风险工具（人工确认后置 True）
    allow_write: bool = True                   # 是否允许写/控制（只读取证/影子模式置 False）


class ToolResult(BaseModel):
    """阶段 4.3 工具统一返回。

    所有工具调用无论成功失败都返回此结构，不抛业务异常：下游超时/无数据/异常被
    registry 归一化为 ok=False + error_code + degraded=True，智能体据此判断数据缺口并降级。
    """

    ok: bool                                   # 调用是否成功（False 时看 error_code）
    data: dict | list | None = None            # 成功时的返回数据（dict/list），失败为 None
    error_code: str | None = None              # 失败错误码（TOOL_NOT_FOUND / HIGH_RISK_BLOCKED 等）
    error_message: str | None = None           # 失败的可读说明
    source: str = "tool"                       # 数据来源（工具名），便于多源数据溯源
    fetched_at: datetime = Field(default_factory=lambda: datetime.now(UTC))  # 取数时间（UTC）
    degraded: bool = False                     # 是否降级（下游异常/超时/无数据时为 True）


@dataclass(frozen=True)
class ToolSpec:
    """工具元数据：frozen 保证注册后不可变，防止运行时篡改风险等级绕过管控。

    三维分类决定工具能否被自动执行：
    - kind（read/write/control）决定是否受 allow_write 约束；
    - risk（low/medium/high）决定是否受 allow_high_risk 约束、是否 auto_executable；
    - required_roles 决定调用者角色是否达标。
    """

    name: str                                  # 工具唯一名（registry 的 key，重名注册报错）
    description: str                           # 工具用途说明（展示给智能体 / 前端 / 审计）
    kind: ToolKind                             # 工具类别
    risk: ToolRisk                             # 风险等级
    required_roles: tuple[str, ...] = ()       # 调用所需角色（空表示无角色门槛）
    retryable: bool = False                    # 是否可重试（仅 read 类生效，写/控制靠幂等兜底）
    timeout_seconds: float = 5.0               # 单次调用超时阈值（秒），超时返回 TOOL_TIMEOUT
    handler: Callable[..., Any] = field(default=None)  # type: ignore[assignment]  实际执行函数（adapter 方法）

    @property
    def auto_executable(self) -> bool:
        """是否可被自动执行：仅 low/medium 可自动执行，high 必须人工确认。

        此属性供 catalog() 展示与执行层判断——high 工具即使权限满足也不能自动跑，
        必须先经人工确认把 context.allow_high_risk 置 True。
        """
        return self.risk != "high"