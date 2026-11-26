from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

RiskLevel = Literal["low", "medium", "high"]

ActionType = Literal[
    "query_only",
    "notify_user",
    "create_ticket_draft",
    "execute_safe_function",
    "request_human_confirm",
    "control_suggestion",
]


class ProposedAction(BaseModel):
    """阶段 7.2 方案动作结构。"""

    action_id: str
    action_type: ActionType
    title: str
    description: str = ""
    risk_level: RiskLevel = "low"
    requires_human_confirm: bool = False
    executable: bool = True
    tool_name: str | None = None
    tool_args: dict | None = None
    preconditions: list[str] = Field(default_factory=list)
    rollback_hint: str | None = None
    evidence_refs: list[str] = Field(default_factory=list)


class ResolutionPlan(BaseModel):
    """阶段 7.2 解决方案结构。"""

    plan_id: str
    incident_id: str
    problem_summary: str
    root_cause_hypotheses: list[dict] = Field(default_factory=list)
    evidence: list[dict] = Field(default_factory=list)
    knowledge_references: list[dict] = Field(default_factory=list)
    actions: list[ProposedAction] = Field(default_factory=list)
    user_instruction: str | None = None
    prohibitions: list[str] = Field(default_factory=list)
    data_gaps: list[str] = Field(default_factory=list)
    need_human_confirm: bool = False
    confidence: float = Field(default=0.0, ge=0, le=1)
    validity_note: str | None = None


class PlanValidationIssue(BaseModel):
    code: str
    message: str
    action_id: str | None = None


class PlanValidationResult(BaseModel):
    """阶段 7.4 校验结果。"""

    ok: bool
    issues: list[PlanValidationIssue] = Field(default_factory=list)
    risk_escalated: bool = False
    confidence_adjusted: bool = False