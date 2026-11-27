"""阶段 7（规划与校验）的数据模型。

定义解决方案生成与治理校验链路的对象：
- ProposedAction：方案中的单个动作（含风险、是否需人工确认、执行工具与回滚提示）；
- ResolutionPlan：完整解决方案（根因假设、证据、动作列表、禁止项、置信度）；
- PlanValidationIssue / PlanValidationResult：治理层对方案的校验结果。

安全相关字段直接建模：risk_level / requires_human_confirm / prohibitions / rollback_hint，
让"哪些动作能自动执行、哪些必须人工确认"成为数据本身的属性。
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

# 动作风险等级：high 必须人工确认，low/medium 视策略可自动执行
RiskLevel = Literal["low", "medium", "high"]

# 动作类型六档：从纯只读到需人工确认，决定执行路径与权限
ActionType = Literal[
    "query_only",            # 仅查询（只读，无副作用，可自动执行）
    "notify_user",           # 通知用户（无副作用）
    "create_ticket_draft",   # 创建工单草稿（不直接派单，需人工确认后提交）
    "execute_safe_function", # 执行安全函数（白名单内的低风险写操作）
    "request_human_confirm", # 请求人工确认（高风险动作的前置）
    "control_suggestion",    # 控制建议（仅给出建议，不直接执行）
]


class ProposedAction(BaseModel):
    """阶段 7.2 方案动作结构：解决方案中的单个可执行动作。"""

    action_id: str                          # 动作唯一标识
    action_type: ActionType                 # 动作类型（决定执行路径）
    title: str                              # 动作标题（前端展示）
    description: str = ""                   # 动作详细描述
    risk_level: RiskLevel = "low"           # 风险等级（high → 必须人工确认）
    requires_human_confirm: bool = False    # 是否需要人工确认才能执行
    executable: bool = True                 # 是否可执行（False 表示仅建议/缺前置）
    tool_name: str | None = None            # 执行工具名（executable=True 时必填）
    tool_args: dict | None = None           # 执行工具参数
    preconditions: list[str] = Field(default_factory=list)  # 前置条件（满足才能执行）
    rollback_hint: str | None = None        # 回滚提示（失败时如何撤销）
    evidence_refs: list[str] = Field(default_factory=list)  # 支撑该动作的证据引用


class ResolutionPlan(BaseModel):
    """阶段 7.2 解决方案结构：规划层输出的完整处置方案。"""

    plan_id: str                              # 方案唯一标识
    incident_id: str                          # 关联的问题实例 id
    problem_summary: str                      # 问题摘要
    root_cause_hypotheses: list[dict] = Field(default_factory=list)  # 根因假设（带置信度）
    evidence: list[dict] = Field(default_factory=list)              # 汇总证据
    knowledge_references: list[dict] = Field(default_factory=list)  # 知识引用（来自 knowledge_hits）
    actions: list[ProposedAction] = Field(default_factory=list)     # 动作列表（方案的核心）
    user_instruction: str | None = None      # 用户附加指令（覆盖默认策略）
    prohibitions: list[str] = Field(default_factory=list)  # 禁止项（治理红线，不得执行）
    data_gaps: list[str] = Field(default_factory=list)     # 数据缺口（来自 findings 汇总）
    need_human_confirm: bool = False         # 方案整体是否需要人工确认（任一动作 high 则 True）
    confidence: float = Field(default=0.0, ge=0, le=1)  # 方案整体置信度（0-1）
    validity_note: str | None = None         # 治理校验备注（校验不通过时的说明）


class PlanValidationIssue(BaseModel):
    """规划校验发现的单个问题：治理层校验方案时产出的 issue。"""

    code: str                    # 问题码（如 MISSING_REQUIRED_METADATA / RISK_ESCALATION）
    message: str                 # 问题描述（人类可读）
    action_id: str | None = None  # 关联的动作 id（方案级问题则为 None）


class PlanValidationResult(BaseModel):
    """阶段 7.4 校验结果：治理层对解决方案的校验结论。"""

    ok: bool                                          # 是否通过校验
    issues: list[PlanValidationIssue] = Field(default_factory=list)  # 发现的问题列表
    risk_escalated: bool = False                      # 是否因风险升级（如知识过期把动作升为 high）
    confidence_adjusted: bool = False                 # 是否因数据缺口下调了置信度