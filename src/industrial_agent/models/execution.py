from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

ConfirmStatus = Literal["pending", "approved", "rejected", "expired", "executed", "failed"]
ExecutionStatus = Literal["success", "failed", "partial", "rolled_back"]


class ConfirmRequest(BaseModel):
    """阶段 8.4 人工确认请求。"""

    confirm_id: str
    incident_id: str
    plan_id: str
    action_id: str
    action_title: str
    action_detail: str = ""
    tool_name: str
    tool_args: dict = Field(default_factory=dict)
    risk_level: str = "high"
    risks: list[str] = Field(default_factory=list)
    rollback_hint: str = ""
    required_roles: list[str] = Field(default_factory=list)
    assignee: str | None = None
    created_at: datetime
    expire_at: datetime
    status: ConfirmStatus = "pending"
    decision_by: str | None = None
    decision_at: datetime | None = None
    decision_note: str | None = None


class ActionExecution(BaseModel):
    """阶段 8.6 执行结果记录。"""

    execution_id: str
    incident_id: str
    plan_id: str
    action_id: str
    tool_name: str
    tool_args_snapshot: dict = Field(default_factory=dict)
    started_at: datetime
    finished_at: datetime | None = None
    status: ExecutionStatus = "success"
    result_summary: str = ""
    error_code: str | None = None
    rollback_executed: bool = False
    operator: str = "system"


AuditDecision = Literal["allowed", "denied", "confirmed", "rejected", "executed", "rolled_back"]


class AuditRecord(BaseModel):
    """阶段 9.2 审计记录。"""

    audit_id: str
    trace_id: str
    incident_id: str | None = None
    actor: str
    roles: list[str] = Field(default_factory=list)
    action: str
    target: str | None = None
    input_snapshot: dict = Field(default_factory=dict)
    output_snapshot: dict = Field(default_factory=dict)
    risk_level: str | None = None
    decision: AuditDecision = "allowed"
    reason: str | None = None
    created_at: datetime