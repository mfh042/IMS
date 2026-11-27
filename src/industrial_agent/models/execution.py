"""阶段 8（执行）与阶段 9（审计）的数据模型。

定义动作执行与全链路留痕的对象：
- ConfirmRequest：高风险动作执行前的人工确认请求（审批流载体）；
- ActionExecution：动作执行结果记录（成功/失败/回滚）；
- AuditRecord：全链路每一步决策的审计留痕（谁、在何时、对什么、做了什么决定）。

这三者是工业场景"可追溯、可问责"的核心数据：任何自动动作都必须有对应的
执行记录与审计记录，高风险动作还必须有审批通过的确认请求，缺一不可。
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

# 人工确认状态：pending 待审批 → approved/rejected/expired → executed/failed
ConfirmStatus = Literal["pending", "approved", "rejected", "expired", "executed", "failed"]
# 执行结果状态：success 成功 / failed 失败 / partial 部分成功 / rolled_back 已回滚
ExecutionStatus = Literal["success", "failed", "partial", "rolled_back"]
# 审计决策：治理层对每一步动作的判定结论
AuditDecision = Literal["allowed", "denied", "confirmed", "rejected", "executed", "rolled_back"]


class ConfirmRequest(BaseModel):
    """阶段 8.4 人工确认请求：高风险动作执行前生成，等待具备 required_roles 的人审批。"""

    confirm_id: str                    # 确认请求唯一标识
    incident_id: str                   # 关联问题实例 id
    plan_id: str                       # 关联方案 id
    action_id: str                     # 关联动作 id
    action_title: str                  # 动作标题（审批人快速判断）
    action_detail: str = ""            # 动作详情
    tool_name: str                     # 将调用的工具名
    tool_args: dict = Field(default_factory=dict)  # 将调用的工具参数（审批人可见）
    risk_level: str = "high"           # 风险等级（通常 high 才需要确认）
    risks: list[str] = Field(default_factory=list)  # 风险说明列表
    rollback_hint: str = ""            # 回滚提示（审批失败时如何撤销）
    required_roles: list[str] = Field(default_factory=list)  # 所需审批角色（如 质量负责人）
    assignee: str | None = None        # 指定审批人
    created_at: datetime               # 创建时间
    expire_at: datetime                # 过期时间（超时未审批 → expired）
    status: ConfirmStatus = "pending"  # 当前状态
    decision_by: str | None = None     # 决策人
    decision_at: datetime | None = None  # 决策时间
    decision_note: str | None = None   # 决策备注（批准/驳回理由）


class ActionExecution(BaseModel):
    """阶段 8.6 执行结果记录：每个动作执行后的留痕（含参数快照，便于复现与审计）。"""

    execution_id: str                       # 执行记录唯一标识
    incident_id: str                        # 关联问题实例 id
    plan_id: str                            # 关联方案 id
    action_id: str                          # 关联动作 id
    tool_name: str                          # 实际调用的工具名
    tool_args_snapshot: dict = Field(default_factory=dict)  # 参数快照（执行时的实际入参）
    started_at: datetime                    # 开始执行时间
    finished_at: datetime | None = None     # 完成时间（未完成则 None）
    status: ExecutionStatus = "success"     # 执行结果状态
    result_summary: str = ""                # 结果摘要
    error_code: str | None = None           # 失败时的错误码
    rollback_executed: bool = False         # 是否已执行回滚
    operator: str = "system"                # 操作者（system=自动，或人工用户名）


class AuditRecord(BaseModel):
    """阶段 9.2 审计记录：全链路每一步决策的留痕，是可追溯/可问责的核心。"""

    audit_id: str                        # 审计记录唯一标识
    trace_id: str                        # 链路追踪 id（串联同一次处理的所有审计）
    incident_id: str | None = None       # 关联问题实例 id（非问题级操作则 None）
    actor: str                           # 执行者（system 或用户名）
    roles: list[str] = Field(default_factory=list)  # 执行者角色（用于权限审计）
    action: str                          # 执行的动作/操作名
    target: str | None = None            # 操作对象（如工单 id、设备 id）
    input_snapshot: dict = Field(default_factory=dict)   # 输入快照（操作前状态）
    output_snapshot: dict = Field(default_factory=dict)  # 输出快照（操作后结果）
    risk_level: str | None = None        # 该操作的风险等级
    decision: AuditDecision = "allowed"  # 治理决策（allowed/denied/confirmed/...）
    reason: str | None = None            # 决策理由（denied 时说明阻断原因）
    created_at: datetime                 # 记录时间