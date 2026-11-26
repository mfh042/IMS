"""阶段 6.4 图状态定义。"""

from __future__ import annotations

from typing import TypedDict


class IndustrialAgentState(TypedDict, total=False):
    event: dict
    incident: dict
    incident_type: str
    subtasks: list[dict]
    agent_findings: dict
    knowledge_refs: list[dict]
    merged_findings: dict
    resolution_plan: dict
    risk_level: str
    need_human_confirm: bool
    confirm_request: dict
    confirm_status: str
    executed_actions: list[dict]
    final_answer: str
    audit_log_id: str
    errors: list[dict]
    duplicate: bool
    throttled: bool
    validation_issues: list[dict]
    node_trace: list[dict]