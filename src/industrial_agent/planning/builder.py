"""阶段 7：方案生成、校验与风险分级。"""

from __future__ import annotations

import uuid

from industrial_agent.models.events import Incident
from industrial_agent.models.findings import AgentFinding
from industrial_agent.models.knowledge import KnowledgeHit
from industrial_agent.models.plan import (
    PlanValidationIssue,
    PlanValidationResult,
    ProposedAction,
    ResolutionPlan,
    RiskLevel,
)

HIGH_RISK_ACTION_TYPES = {"control_suggestion"}
CONTROL_TOOLS = {"control_device", "freeze_batch"}

PROHIBITIONS = [
    "不得直接输出设备控制指令",
    "不得在无人工确认时执行停机、启机、参数修改",
    "不得在无人工确认时冻结批次或修改工单状态",
    "不得在无知识依据时编造处置步骤",
]


def _escalate(level: RiskLevel) -> RiskLevel:
    return {"low": "medium", "medium": "high", "high": "high"}[level]


class RiskClassifier:
    """阶段 7.3 风险分级：规则优先。"""

    def classify(
        self,
        action_type: str,
        *,
        tool_name: str | None = None,
        data_quality: str = "complete",
        knowledge_expired: bool = False,
        has_conflict: bool = False,
    ) -> RiskLevel:
        if action_type in HIGH_RISK_ACTION_TYPES or (tool_name in CONTROL_TOOLS):
            base: RiskLevel = "high"
        elif action_type in ("notify_user", "create_ticket_draft", "execute_safe_function"):
            base = "medium"
        else:
            base = "low"

        if data_quality in ("partial", "suspicious") or knowledge_expired or has_conflict:
            base = _escalate(base)
        return base


class PlanBuilder:
    """把多 Agent 结论汇总为可执行、可审计、可分级的方案。"""

    def __init__(self, classifier: RiskClassifier | None = None) -> None:
        self._classifier = classifier or RiskClassifier()

    def build(
        self,
        *,
        incident: Incident,
        findings: dict[str, AgentFinding],
        knowledge_hits: list[KnowledgeHit],
        data_quality: str = "complete",
        conflicts: list[str] | None = None,
    ) -> ResolutionPlan:
        conflicts = conflicts or []
        knowledge_expired = any(h.expired for h in knowledge_hits)

        evidence: list[dict] = []
        knowledge_refs: list[dict] = []
        data_gaps: list[str] = []
        hypotheses: list[dict] = []

        for finding in findings.values():
            evidence.extend(finding.evidence)
            data_gaps.extend(finding.data_gaps)
            for hypothesis in finding.hypotheses:
                hypotheses.append(
                    {"hypothesis": hypothesis, "confidence": finding.confidence, "agent": finding.agent_name}
                )

        for hit in knowledge_hits:
            knowledge_refs.append(
                {
                    "chunk_id": hit.chunk.chunk_id,
                    "title": hit.chunk.title,
                    "version": hit.chunk.version,
                    "source_uri": hit.chunk.source_uri,
                    "expired": hit.expired,
                }
            )

        confidence = self._conservative_confidence(findings, evidence, knowledge_hits, data_gaps)

        actions = self._build_actions(
            incident=incident,
            findings=findings,
            knowledge_hits=knowledge_hits,
            data_quality=data_quality,
            knowledge_expired=knowledge_expired,
            has_conflict=bool(conflicts),
        )

        summary = self._summarize(incident, findings)
        need_confirm = any(a.requires_human_confirm for a in actions)
        validity_note = "存在过期知识依据，结论仅供参考" if knowledge_expired else None

        return ResolutionPlan(
            plan_id=f"plan-{uuid.uuid4().hex[:10]}",
            incident_id=incident.incident_id,
            problem_summary=summary,
            root_cause_hypotheses=hypotheses,
            evidence=evidence,
            knowledge_references=knowledge_refs,
            actions=actions,
            prohibitions=PROHIBITIONS,
            data_gaps=sorted(set(data_gaps)),
            need_human_confirm=need_confirm,
            confidence=confidence,
            validity_note=validity_note,
        )

    @staticmethod
    def _conservative_confidence(
        findings: dict[str, AgentFinding],
        evidence: list[dict],
        knowledge_hits: list[KnowledgeHit],
        data_gaps: list[str],
    ) -> float:
        if not findings:
            return 0.0
        agent_floor = min(f.confidence for f in findings.values())
        evidence_score = min(1.0, len(evidence) / 4.0)
        valid_knowledge = [h for h in knowledge_hits if not h.expired]
        knowledge_score = min(1.0, len(valid_knowledge) / 2.0)
        gap_penalty = 0.1 * len(set(data_gaps))
        value = min(agent_floor, evidence_score, knowledge_score) - gap_penalty
        return round(max(0.0, min(1.0, value)), 2)

    def _build_actions(
        self,
        *,
        incident: Incident,
        findings: dict[str, AgentFinding],
        knowledge_hits: list[KnowledgeHit],
        data_quality: str,
        knowledge_expired: bool,
        has_conflict: bool,
    ) -> list[ProposedAction]:
        actions: list[ProposedAction] = []
        evidence_refs = [e.get("ref") for e in findings_evidence(findings) if e.get("ref")]

        actions.append(
            ProposedAction(
                action_id="act-1",
                action_type="query_only",
                title="复核关键状态数据",
                description="重新查询设备状态、批次冻结状态与库存可用量，确认结论时效性",
                risk_level="low",
                requires_human_confirm=False,
                executable=True,
                evidence_refs=evidence_refs[:5],
                preconditions=["相关系统接口可用"],
                rollback_hint="只读操作，无需回滚",
            )
        )

        risk_common = {
            "data_quality": data_quality,
            "knowledge_expired": knowledge_expired,
            "has_conflict": has_conflict,
        }

        if incident.incident_type == "equipment_exception":
            actions.append(
                ProposedAction(
                    action_id="act-2",
                    action_type="create_ticket_draft",
                    title="创建设备维护工单草稿",
                    description="依据诊断结论与维护建议生成草稿，由设备工程师审核后转正式工单",
                    risk_level=self._classifier.classify("create_ticket_draft", **risk_common),
                    requires_human_confirm=False,
                    executable=bool(knowledge_hits),
                    tool_name="create_ticket_draft",
                    tool_args={
                        "incident_id": incident.incident_id,
                        "device_id": incident.entity_refs.get("device_id"),
                        "title": "设备异常维护",
                    },
                    preconditions=["存在可用知识依据", "设备标识存在"],
                    rollback_hint="草稿可删除，不影响生产工单",
                    evidence_refs=evidence_refs[:5],
                )
            )
            actions.append(
                ProposedAction(
                    action_id="act-3",
                    action_type="notify_user",
                    title="通知设备工程师",
                    description="将诊断结论与维护建议推送给当班设备工程师",
                    risk_level=self._classifier.classify("notify_user", **risk_common),
                    requires_human_confirm=False,
                    executable=True,
                    tool_name="send_notification",
                    tool_args={
                        "incident_id": incident.incident_id,
                        "role": "maintenance",
                        "message": "设备异常诊断结论已生成，请复核",
                    },
                    preconditions=["通知渠道可用"],
                    rollback_hint="通知类动作无需回滚",
                    evidence_refs=evidence_refs[:3],
                )
            )
            actions.append(
                ProposedAction(
                    action_id="act-4",
                    action_type="request_human_confirm",
                    title="确认是否降速或停机检查",
                    description="若温度或振动继续上升，需设备主管确认后执行降速/停机",
                    risk_level="high",
                    requires_human_confirm=True,
                    executable=False,
                    tool_name="control_device",
                    tool_args={
                        "device_id": incident.entity_refs.get("device_id"),
                        "command": "reduce_speed_or_stop",
                    },
                    preconditions=["设备主管在线", "现场安全确认"],
                    rollback_hint="停机后需按 SOP 重新升温启动",
                    evidence_refs=evidence_refs[:5],
                )
            )

        if incident.incident_type in ("material_exception", "production_exception"):
            actions.append(
                ProposedAction(
                    action_id="act-2",
                    action_type="notify_user",
                    title="通知计划员与物料员",
                    description="推送缺料判断与影响范围，触发补料或替代料评估",
                    risk_level=self._classifier.classify("notify_user", **risk_common),
                    requires_human_confirm=False,
                    executable=True,
                    tool_name="send_notification",
                    tool_args={
                        "incident_id": incident.incident_id,
                        "role": "planner",
                        "message": "工单疑似缺料，请确认补料或替代料",
                    },
                    preconditions=["通知渠道可用"],
                    rollback_hint="通知类动作无需回滚",
                    evidence_refs=evidence_refs[:3],
                )
            )
            actions.append(
                ProposedAction(
                    action_id="act-3",
                    action_type="request_human_confirm",
                    title="确认是否切换替代批次",
                    description="替代批次或替代料需工艺与质量双确认，系统不自动执行",
                    risk_level="high",
                    requires_human_confirm=True,
                    executable=False,
                    tool_name="freeze_batch",
                    tool_args={"batch_id": incident.entity_refs.get("batch_id")},
                    preconditions=["工艺员与质量员确认"],
                    rollback_hint="切换后需重新首件确认",
                    evidence_refs=evidence_refs[:5],
                )
            )

        if incident.incident_type == "quality_exception":
            actions.append(
                ProposedAction(
                    action_id="act-2",
                    action_type="notify_user",
                    title="通知质量工程师",
                    description="推送不良与追溯链结论，启动隔离评估",
                    risk_level=self._classifier.classify("notify_user", **risk_common),
                    requires_human_confirm=False,
                    executable=True,
                    tool_name="send_notification",
                    tool_args={
                        "incident_id": incident.incident_id,
                        "role": "quality",
                        "message": "批次不良已追溯，请评估隔离范围",
                    },
                    preconditions=["通知渠道可用"],
                    rollback_hint="通知类动作无需回滚",
                    evidence_refs=evidence_refs[:3],
                )
            )
            actions.append(
                ProposedAction(
                    action_id="act-3",
                    action_type="request_human_confirm",
                    title="确认是否冻结批次",
                    description="批次冻结必须由质量负责人审批，系统仅生成确认请求",
                    risk_level="high",
                    requires_human_confirm=True,
                    executable=False,
                    tool_name="freeze_batch",
                    tool_args={"batch_id": incident.entity_refs.get("batch_id")},
                    preconditions=["质量负责人审批", "隔离标识已挂"],
                    rollback_hint="解冻需质量负责人二次审批",
                    evidence_refs=evidence_refs[:5],
                )
            )

        if not actions:
            actions.append(
                ProposedAction(
                    action_id="act-1",
                    action_type="query_only",
                    title="补充查询后重新评估",
                    description="当前证据不足，先补齐关键数据再生成处置方案",
                    risk_level="low",
                    requires_human_confirm=False,
                    executable=True,
                    evidence_refs=[],
                    preconditions=[],
                    rollback_hint="只读操作，无需回滚",
                )
            )
        return actions

    @staticmethod
    def _summarize(incident: Incident, findings: dict[str, AgentFinding]) -> str:
        primary = next(
            (f for f in findings.values() if f.agent_name not in ("knowledge_retrieval", "control_suggestion")),
            None,
        )
        prefix = f"[{incident.incident_type}] "
        if primary is None:
            return prefix + "证据不足，未形成明确结论"
        return prefix + primary.summary


def findings_evidence(findings: dict[str, AgentFinding]) -> list[dict]:
    items: list[dict] = []
    for finding in findings.values():
        items.extend(finding.evidence)
    return items


class PlanValidator:
    """阶段 7.4 方案校验清单。"""

    def validate(self, plan: ResolutionPlan) -> PlanValidationResult:
        issues: list[PlanValidationIssue] = []
        risk_escalated = False

        if plan.confidence < 0.3:
            issues.append(
                PlanValidationIssue(code="LOW_CONFIDENCE", message="综合置信度过低，建议转人工")
            )

        for action in plan.actions:
            if action.risk_level == "high" and not action.requires_human_confirm:
                issues.append(
                    PlanValidationIssue(
                        code="HIGH_RISK_NO_CONFIRM",
                        message="高风险动作缺少人工确认标记",
                        action_id=action.action_id,
                    )
                )
                action.requires_human_confirm = True
                action.executable = False

            if action.executable and not action.preconditions and action.action_type != "query_only":
                issues.append(
                    PlanValidationIssue(
                        code="MISSING_PRECONDITION",
                        message="可执行动作缺少前置条件",
                        action_id=action.action_id,
                    )
                )
                action.executable = False

            if action.action_type == "control_suggestion" and action.executable:
                issues.append(
                    PlanValidationIssue(
                        code="CONTROL_EXECUTABLE",
                        message="控制建议不得标记为可执行",
                        action_id=action.action_id,
                    )
                )
                action.executable = False

            if action.tool_name in CONTROL_TOOLS and action.executable:
                issues.append(
                    PlanValidationIssue(
                        code="CONTROL_TOOL_EXECUTABLE",
                        message="高风险工具不得直接执行",
                        action_id=action.action_id,
                    )
                )
                action.executable = False

            if not action.evidence_refs and action.action_type != "query_only":
                issues.append(
                    PlanValidationIssue(
                        code="NO_EVIDENCE",
                        message="动作缺少证据引用",
                        action_id=action.action_id,
                    )
                )

        if plan.data_gaps and plan.confidence > 0.6:
            issues.append(
                PlanValidationIssue(
                    code="CONFIDENCE_MISMATCH",
                    message="存在数据缺口但置信度偏高，已下调",
                )
            )
            plan.confidence = 0.6
            risk_escalated = True

        plan.need_human_confirm = any(a.requires_human_confirm for a in plan.actions)
        return PlanValidationResult(
            ok=not any(i.code in ("HIGH_RISK_NO_CONFIRM", "CONTROL_TOOL_EXECUTABLE") for i in issues),
            issues=issues,
            risk_escalated=risk_escalated,
            confidence_adjusted=risk_escalated,
        )