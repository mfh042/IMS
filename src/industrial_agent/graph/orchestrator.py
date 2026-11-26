"""阶段 6：主调度与 LangGraph 编排。

图结构：
START -> normalize_event -> governance_check -> classify_incident -> plan_subtasks
      -> retrieve_knowledge -> run_specialists -> merge_findings
      -> build_resolution_plan -> validate_plan -> risk_route
      -> execute_safe_actions / execute_medium_risk_actions / request_human_confirm
      -> record_result -> respond -> END
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from typing import Any

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import interrupt

from industrial_agent.agents.base import AgentContext, BaseAgent
from industrial_agent.agents.llm import LLMClient
from industrial_agent.agents.specialists import (
    ControlSuggestionAgent,
    EquipmentDiagnosisAgent,
    KnowledgeRetrievalAgent,
    MaintenanceAdviceAgent,
    MaterialInventoryAgent,
    ProductionExceptionAgent,
    QualityTraceAgent,
)
from industrial_agent.audit.recorder import AuditRecorder
from industrial_agent.config import Settings
from industrial_agent.execution.executor import ActionExecutor, ConfirmStore
from industrial_agent.governance.governor import EventGovernance
from industrial_agent.graph.state import IndustrialAgentState
from industrial_agent.ingestion.normalizer import EventNormalizer
from industrial_agent.knowledge.store import KnowledgeStore
from industrial_agent.models.events import Incident, StandardEvent
from industrial_agent.models.findings import AgentFinding
from industrial_agent.models.plan import ProposedAction, ResolutionPlan
from industrial_agent.planning.builder import PlanBuilder, PlanValidator
from industrial_agent.tools.contracts import ToolContext
from industrial_agent.tools.factory import build_default_registry
from industrial_agent.tools.registry import ToolRegistry

SUBTASK_MAP: dict[str, list[str]] = {
    "equipment_exception": ["equipment_diagnosis", "knowledge_retrieval", "maintenance_advice"],
    "material_exception": ["material_inventory", "production_exception", "knowledge_retrieval"],
    "quality_exception": ["quality_trace", "knowledge_retrieval"],
    "production_exception": ["production_exception", "material_inventory", "knowledge_retrieval"],
    "mixed_exception": [
        "equipment_diagnosis",
        "production_exception",
        "material_inventory",
        "quality_trace",
        "knowledge_retrieval",
    ],
}

KEYWORD_TYPE_MAP = {
    "equipment_exception": ("设备", "报警", "温度", "振动", "通讯", "device", "alarm", "temp", "vibration"),
    "quality_exception": ("不良", "质量", "检验", "缺陷", "defect", "quality"),
    "material_exception": ("缺料", "库存", "物料", "批次", "material", "stock"),
    "production_exception": ("工单", "阻塞", "停线", "工序", "work_order", "blocked"),
}


class IndustrialAgentGraph:
    """可回放、可中断、可人工介入的确定性编排图。"""

    def __init__(
        self,
        *,
        settings: Settings | None = None,
        tools: ToolRegistry | None = None,
        knowledge: KnowledgeStore | None = None,
        governance: EventGovernance | None = None,
        audit: AuditRecorder | None = None,
        llm: LLMClient | None = None,
        confirm_ttl_seconds: int = 3600,
    ) -> None:
        self._settings = settings or Settings()
        self._audit = audit or AuditRecorder()
        self._tools = tools or build_default_registry(registry=ToolRegistry(self._audit))
        self._knowledge = knowledge or KnowledgeStore()
        self._governance = governance or EventGovernance(
            dedup_window_seconds=self._settings.dedup_window_seconds,
            max_concurrent_incidents=self._settings.max_concurrent_incidents,
            per_entity_incident_limit=self._settings.per_entity_incident_limit,
            priority_wait_weight=self._settings.priority_wait_weight,
        )
        self._llm = llm or LLMClient(self._settings)
        self._normalizer = EventNormalizer(self._settings.tenant_id)
        self._plan_builder = PlanBuilder()
        self._plan_validator = PlanValidator()
        self._confirm_store = ConfirmStore(confirm_ttl_seconds)
        self._executor = ActionExecutor(self._tools, audit=self._audit, confirms=self._confirm_store)
        self._checkpointer = InMemorySaver()
        self._graph = self._build()

    # ---------- 属性 ----------

    @property
    def governance(self) -> EventGovernance:
        return self._governance

    @property
    def audit(self) -> AuditRecorder:
        return self._audit

    @property
    def executor(self) -> ActionExecutor:
        return self._executor

    @property
    def confirms(self) -> ConfirmStore:
        return self._confirm_store

    @property
    def tools(self) -> ToolRegistry:
        return self._tools

    # ---------- 图构建 ----------

    def _build(self) -> Any:
        graph = StateGraph(IndustrialAgentState)
        graph.add_node("normalize_event", self._normalize_event)
        graph.add_node("governance_check", self._governance_check)
        graph.add_node("classify_incident", self._classify_incident)
        graph.add_node("plan_subtasks", self._plan_subtasks)
        graph.add_node("retrieve_knowledge", self._retrieve_knowledge)
        graph.add_node("run_specialists", self._run_specialists)
        graph.add_node("merge_findings", self._merge_findings)
        graph.add_node("build_resolution_plan", self._build_plan)
        graph.add_node("validate_plan", self._validate_plan)
        graph.add_node("execute_safe_actions", self._execute_safe_actions)
        graph.add_node("execute_medium_risk_actions", self._execute_medium_risk_actions)
        graph.add_node("request_human_confirm", self._request_human_confirm)
        graph.add_node("record_result", self._record_result)
        graph.add_node("respond", self._respond)

        graph.add_edge(START, "normalize_event")
        graph.add_conditional_edges(
            "normalize_event",
            self._route_after_normalize,
            {"continue": "governance_check", "respond": "respond"},
        )
        graph.add_conditional_edges(
            "governance_check",
            self._route_after_governance,
            {"continue": "classify_incident", "respond": "respond"},
        )
        graph.add_edge("classify_incident", "plan_subtasks")
        graph.add_edge("plan_subtasks", "retrieve_knowledge")
        graph.add_edge("retrieve_knowledge", "run_specialists")
        graph.add_edge("run_specialists", "merge_findings")
        graph.add_edge("merge_findings", "build_resolution_plan")
        graph.add_edge("build_resolution_plan", "validate_plan")
        graph.add_conditional_edges(
            "validate_plan",
            self._route_by_risk,
            {
                "safe": "execute_safe_actions",
                "medium": "execute_medium_risk_actions",
                "confirm": "request_human_confirm",
                "none": "record_result",
            },
        )
        graph.add_edge("execute_safe_actions", "execute_medium_risk_actions")
        graph.add_edge("execute_medium_risk_actions", "record_result")
        graph.add_edge("request_human_confirm", "record_result")
        graph.add_edge("record_result", "respond")
        graph.add_edge("respond", END)
        return graph.compile(checkpointer=self._checkpointer)

    # ---------- 节点 ----------

    def _trace(self, state: IndustrialAgentState, node: str, payload: dict[str, Any]) -> None:
        state.setdefault("node_trace", []).append(
            {"node": node, "at": datetime.now(UTC).isoformat(), "payload": payload}
        )

    def _normalize_event(self, state: IndustrialAgentState) -> dict[str, Any]:
        raw = state.get("event") or {}
        outcome = self._normalizer.normalize(raw)
        if not outcome.ok or outcome.event is None:
            errors = list(state.get("errors", []))
            errors.append(
                {
                    "node": "normalize_event",
                    "error_code": outcome.error_code,
                    "message": outcome.dead_letter_reason,
                }
            )
            self._audit.record(
                trace_id=str(raw.get("trace_id") or "unknown"),
                actor="system",
                action="event:dead_letter",
                input_snapshot=raw,
                decision="denied",
                reason=outcome.dead_letter_reason,
            )
            return {"errors": errors, "final_answer": f"事件进入死信队列：{outcome.dead_letter_reason}"}

        event = outcome.event
        self._audit.record(
            trace_id=event.trace_id,
            actor="system",
            action="event:normalized",
            target=event.event_id,
            input_snapshot=raw,
            output_snapshot=event.model_dump(mode="json"),
        )
        return {"event": event.model_dump(mode="json")}

    def _governance_check(self, state: IndustrialAgentState) -> dict[str, Any]:
        event = StandardEvent.model_validate(state["event"])

        if self._governance.is_duplicate(event):
            self._audit.record(
                trace_id=event.trace_id,
                actor="system",
                action="governance:dedup",
                target=event.idempotency_key,
                decision="allowed",
                reason="窗口内重复事件，已丢弃并计数",
            )
            return {
                "duplicate": True,
                "final_answer": (
                    f"重复事件已收敛（idempotency_key={event.idempotency_key}，"
                    f"累计重复 {self._governance.duplicate_count(event.idempotency_key)} 次）"
                ),
            }

        agg = self._governance.aggregate(event)
        throttled, reason = self._governance.is_throttled(agg)
        if throttled:
            self._audit.record(
                trace_id=event.trace_id,
                actor="system",
                action="governance:throttle",
                target=event.event_id,
                decision="denied",
                reason=reason,
            )
            return {"throttled": True, "final_answer": f"限流触发，事件进入排队：{reason}"}

        incident = self._governance.build_incident(agg, now=event.occurred_at)
        self._audit.record(
            trace_id=incident.trace_id,
            incident_id=incident.incident_id,
            actor="system",
            action="governance:aggregate",
            target=incident.incident_id,
            output_snapshot=incident.model_dump(mode="json"),
        )
        return {"incident": incident.model_dump(mode="json")}

    def _classify_incident(self, state: IndustrialAgentState) -> dict[str, Any]:
        event = StandardEvent.model_validate(state["event"])
        incident = Incident.model_validate(state["incident"])

        incident_type = incident.incident_type
        text = f"{event.event_type} {event.message or ''}".lower()
        for candidate, keywords in KEYWORD_TYPE_MAP.items():
            if any(k.lower() in text for k in keywords):
                if incident_type != candidate and incident_type != "mixed_exception":
                    incident_type = "mixed_exception"
                elif incident_type == "mixed_exception":
                    incident_type = candidate
                break

        incident.incident_type = incident_type  # type: ignore[assignment]
        self._governance.close_incident(incident.incident_id, status="processing")
        self._audit.record(
            trace_id=incident.trace_id,
            incident_id=incident.incident_id,
            actor="supervisor",
            action="classify_incident",
            output_snapshot={"incident_type": incident_type},
        )
        return {
            "incident": incident.model_dump(mode="json"),
            "incident_type": incident_type,
        }

    def _plan_subtasks(self, state: IndustrialAgentState) -> dict[str, Any]:
        incident_type = state.get("incident_type", "mixed_exception")
        names = SUBTASK_MAP.get(incident_type, SUBTASK_MAP["mixed_exception"])
        subtasks = [{"agent": name, "status": "pending"} for name in names]
        self._audit.record(
            trace_id=state["incident"]["trace_id"],
            incident_id=state["incident"]["incident_id"],
            actor="supervisor",
            action="plan_subtasks",
            output_snapshot={"subtasks": subtasks},
        )
        return {"subtasks": subtasks}

    def _retrieve_knowledge(self, state: IndustrialAgentState) -> dict[str, Any]:
        incident = Incident.model_validate(state["incident"])
        refs = incident.entity_refs
        device_type = self._resolve_device_type(refs.get("device_id"))
        query = " ".join(
            str(v) for v in (refs.get("alarm_code"), refs.get("material_id"), refs.get("batch_id")) if v
        ) or incident.incident_type

        hits = self._knowledge.retrieve(
            query,
            device_type=device_type,
            alarm_code=refs.get("alarm_code"),
            process=refs.get("process_id"),
            top_k=5,
        )
        state_hits = [h.model_dump(mode="json") for h in hits]
        self._audit.record(
            trace_id=incident.trace_id,
            incident_id=incident.incident_id,
            actor="knowledge_retrieval",
            action="knowledge:retrieve",
            target=query,
            output_snapshot={"hits": [h["chunk"]["chunk_id"] for h in state_hits]},
            decision="executed",
        )
        return {"knowledge_refs": state_hits}

    def _run_specialists(self, state: IndustrialAgentState) -> dict[str, Any]:
        incident = Incident.model_validate(state["incident"])
        subtasks = state.get("subtasks", [])
        hits = state.get("knowledge_refs", [])

        from industrial_agent.models.knowledge import KnowledgeHit

        knowledge_hits = [KnowledgeHit.model_validate(h) for h in hits]
        tool_context = ToolContext(
            trace_id=incident.trace_id,
            incident_id=incident.incident_id,
            actor="supervisor",
            roles=["system"],
            tenant_id=self._settings.tenant_id,
            allow_high_risk=False,
        )

        agent_classes: dict[str, type[BaseAgent]] = {
            "equipment_diagnosis": EquipmentDiagnosisAgent,
            "maintenance_advice": MaintenanceAdviceAgent,
            "knowledge_retrieval": KnowledgeRetrievalAgent,
            "production_exception": ProductionExceptionAgent,
            "material_inventory": MaterialInventoryAgent,
            "quality_trace": QualityTraceAgent,
            "control_suggestion": ControlSuggestionAgent,
        }

        findings: dict[str, AgentFinding] = {}
        errors = list(state.get("errors", []))
        agent_names = [t["agent"] for t in subtasks] or SUBTASK_MAP["mixed_exception"]

        def run(name: str) -> tuple[str, AgentFinding | None, str | None]:
            cls = agent_classes.get(name)
            if cls is None:
                return name, None, f"未知 Agent：{name}"
            agent = cls(self._audit)
            ctx = AgentContext(
                incident=incident,
                tool_context=tool_context,
                tools=self._tools,
                knowledge=self._knowledge,
                llm=self._llm,
                knowledge_hits=knowledge_hits,
                scenario="设备异常诊断与维护建议",
            )
            try:
                return name, agent.analyze(ctx), None
            except Exception as exc:  # noqa: BLE001
                return name, None, f"{name} 执行失败：{exc}"

        with ThreadPoolExecutor(max_workers=min(4, max(1, len(agent_names)))) as pool:
            for name, finding, error in pool.map(run, agent_names):
                if error:
                    errors.append({"node": "run_specialists", "agent": name, "message": error})
                if finding is not None:
                    findings[name] = finding

        return {
            "agent_findings": {k: v.model_dump(mode="json") for k, v in findings.items()},
            "errors": errors,
        }

    def _merge_findings(self, state: IndustrialAgentState) -> dict[str, Any]:
        raw_findings = state.get("agent_findings", {})
        findings = {k: AgentFinding.model_validate(v) for k, v in raw_findings.items()}

        evidence: list[dict] = []
        conflicts: list[str] = []
        data_gaps: list[str] = []
        for finding in findings.values():
            for item in finding.evidence:
                if item not in evidence:
                    evidence.append(item)
            data_gaps.extend(finding.data_gaps)
            if finding.hypotheses and finding.confidence < 0.4:
                conflicts.append(f"{finding.agent_name} 结论置信度偏低，需交叉验证")

        confidences = [f.confidence for f in findings.values()] or [0.0]
        merged = {
            "evidence": evidence,
            "data_gaps": sorted(set(data_gaps)),
            "conflicts": conflicts,
            "min_confidence": min(confidences),
            "agent_count": len(findings),
        }
        self._audit.record(
            trace_id=state["incident"]["trace_id"],
            incident_id=state["incident"]["incident_id"],
            actor="supervisor",
            action="merge_findings",
            output_snapshot=merged,
        )
        return {"merged_findings": merged}

    def _build_plan(self, state: IndustrialAgentState) -> dict[str, Any]:
        incident = Incident.model_validate(state["incident"])
        findings = {
            k: AgentFinding.model_validate(v) for k, v in state.get("agent_findings", {}).items()
        }
        from industrial_agent.models.knowledge import KnowledgeHit

        hits = [KnowledgeHit.model_validate(h) for h in state.get("knowledge_refs", [])]
        merged = state.get("merged_findings", {})
        event = StandardEvent.model_validate(state["event"])

        plan = self._plan_builder.build(
            incident=incident,
            findings=findings,
            knowledge_hits=hits,
            data_quality=event.data_quality,
            conflicts=list(merged.get("conflicts", [])),
        )
        self._audit.record(
            trace_id=incident.trace_id,
            incident_id=incident.incident_id,
            actor="supervisor",
            action="build_resolution_plan",
            output_snapshot={"plan_id": plan.plan_id, "confidence": plan.confidence},
        )
        return {
            "resolution_plan": plan.model_dump(mode="json"),
            "risk_level": self._highest_risk(plan),
        }

    def _validate_plan(self, state: IndustrialAgentState) -> dict[str, Any]:
        plan = ResolutionPlan.model_validate(state["resolution_plan"])
        result = self._plan_validator.validate(plan)
        self._audit.record(
            trace_id=state["incident"]["trace_id"],
            incident_id=state["incident"]["incident_id"],
            actor="supervisor",
            action="validate_plan",
            output_snapshot=result.model_dump(mode="json"),
            decision="allowed" if result.ok else "denied",
        )
        return {
            "resolution_plan": plan.model_dump(mode="json"),
            "validation_issues": [i.model_dump(mode="json") for i in result.issues],
            "need_human_confirm": plan.need_human_confirm,
        }

    def _execute_safe_actions(self, state: IndustrialAgentState) -> dict[str, Any]:
        plan = ResolutionPlan.model_validate(state["resolution_plan"])
        context = self._execution_context(state)
        executions = []
        for action in self._executor.plan_dispatch(plan)["auto"]:
            execution = self._executor.execute(plan, action, context=context)
            executions.append(execution.model_dump(mode="json"))
        return {"executed_actions": executions}

    def _execute_medium_risk_actions(self, state: IndustrialAgentState) -> dict[str, Any]:
        plan = ResolutionPlan.model_validate(state["resolution_plan"])
        context = self._execution_context(state)
        executions = list(state.get("executed_actions", []))
        for action in self._executor.plan_dispatch(plan)["todo"]:
            if not action.executable:
                continue
            execution = self._executor.execute(plan, action, context=context)
            executions.append(execution.model_dump(mode="json"))
        return {"executed_actions": executions}

    def _request_human_confirm(self, state: IndustrialAgentState) -> dict[str, Any]:
        plan = ResolutionPlan.model_validate(state["resolution_plan"])
        pending = self._executor.plan_dispatch(plan)["confirm"]
        if not pending:
            return {"need_human_confirm": False}

        action = pending[0]
        requests = self._executor.request_confirmations(
            plan,
            required_roles=self._roles_for_action(action),
        )
        request = requests[0]

        self._governance.close_incident(plan.incident_id, status="waiting_confirm")

        decision = interrupt(
            {
                "confirm_id": request.confirm_id,
                "incident_id": plan.incident_id,
                "plan_id": plan.plan_id,
                "action_id": action.action_id,
                "action_title": action.title,
                "risk_level": action.risk_level,
                "risks": request.risks,
                "rollback_hint": request.rollback_hint,
                "required_roles": request.required_roles,
                "expire_at": request.expire_at.isoformat(),
            }
        )
        approved = bool(decision.get("approved")) if isinstance(decision, dict) else False
        decision_by = (
            str(decision.get("decision_by", "unknown")) if isinstance(decision, dict) else "unknown"
        )
        roles = list(decision.get("roles", [])) if isinstance(decision, dict) else []

        ok, status = self._confirm_store.decide(
            request.confirm_id,
            approved=approved,
            decision_by=decision_by,
            roles=roles,
            note=decision.get("note") if isinstance(decision, dict) else None,
        )
        if not ok:
            return {
                "confirm_request": request.model_dump(mode="json"),
                "confirm_status": status,
                "need_human_confirm": True,
            }

        executions = list(state.get("executed_actions", []))
        if approved:
            execution = self._executor.execute(
                plan,
                action,
                context=self._execution_context(state, allow_high_risk=True, roles=roles or None),
                confirm_id=request.confirm_id,
                operator=decision_by,
            )
            executions.append(execution.model_dump(mode="json"))
        return {
            "confirm_request": request.model_dump(mode="json"),
            "confirm_status": status,
            "executed_actions": executions,
        }

    def _record_result(self, state: IndustrialAgentState) -> dict[str, Any]:
        plan_data = state.get("resolution_plan")
        if not plan_data:
            return {}
        plan = ResolutionPlan.model_validate(plan_data)
        summary = {
            "plan_id": plan.plan_id,
            "incident_id": plan.incident_id,
            "confidence": plan.confidence,
            "need_human_confirm": plan.need_human_confirm,
            "executed": len(state.get("executed_actions", [])),
            "confirm_status": state.get("confirm_status"),
        }
        audit = self._audit.record(
            trace_id=state["incident"]["trace_id"],
            incident_id=plan.incident_id,
            actor="supervisor",
            action="record_result",
            output_snapshot=summary,
        )
        if not plan.need_human_confirm:
            self._governance.close_incident(plan.incident_id, status="closed")
        return {"audit_log_id": audit.audit_id}

    def _respond(self, state: IndustrialAgentState) -> dict[str, Any]:
        plan_data = state.get("resolution_plan")
        if not plan_data:
            answer = state.get("final_answer") or "流程提前终止，未生成方案"
            return {"final_answer": answer}

        plan = ResolutionPlan.model_validate(plan_data)
        lines = [
            f"问题概述：{plan.problem_summary}",
            f"综合置信度：{plan.confidence}",
        ]
        if plan.data_gaps:
            lines.append(f"数据缺口：{'；'.join(plan.data_gaps)}")
        if plan.validity_note:
            lines.append(f"有效性提示：{plan.validity_note}")

        lines.append("处置动作：")
        for action in plan.actions:
            tag = "需人工确认" if action.requires_human_confirm else (
                "可自动执行" if action.executable else "仅提示"
            )
            lines.append(f"- [{action.risk_level}/{tag}] {action.title}")

        if state.get("confirm_status"):
            lines.append(f"人工确认状态：{state['confirm_status']}")
        if state.get("validation_issues"):
            codes = [i["code"] for i in state["validation_issues"]]
            lines.append(f"方案校验：{', '.join(codes)}")

        return {"final_answer": "\n".join(lines)}

    # ---------- 路由 ----------

    def _route_after_normalize(self, state: IndustrialAgentState) -> str:
        return "continue" if state.get("event") else "respond"

    def _route_after_governance(self, state: IndustrialAgentState) -> str:
        if state.get("duplicate") or state.get("throttled"):
            return "respond"
        return "continue" if state.get("incident") else "respond"

    def _route_by_risk(self, state: IndustrialAgentState) -> str:
        plan_data = state.get("resolution_plan")
        if not plan_data:
            return "none"
        plan = ResolutionPlan.model_validate(plan_data)
        buckets = self._executor.plan_dispatch(plan)
        if buckets["confirm"]:
            return "confirm"
        if any(a.executable for a in buckets["auto"]):
            return "safe"
        if any(a.executable for a in buckets["todo"]):
            return "medium"
        return "none"

    # ---------- 辅助 ----------

    def _execution_context(
        self,
        state: IndustrialAgentState,
        *,
        allow_high_risk: bool = False,
        roles: list[str] | None = None,
    ) -> ToolContext:
        incident = state.get("incident") or {}
        return ToolContext(
            trace_id=str(incident.get("trace_id", "unknown")),
            incident_id=incident.get("incident_id"),
            actor="system",
            roles=roles or ["system"],
            tenant_id=self._settings.tenant_id,
            allow_high_risk=allow_high_risk,
        )

    def _resolve_device_type(self, device_id: Any) -> str | None:
        if not device_id:
            return None
        spec = self._tools.get("query_device_status")
        if spec is None:
            return None
        try:
            data = spec.handler(device_id=str(device_id))  # type: ignore[misc]
        except Exception:  # noqa: BLE001
            return None
        if isinstance(data, dict):
            return data.get("device_type")
        return None

    @staticmethod
    def _highest_risk(plan: ResolutionPlan) -> str:
        order = {"low": 1, "medium": 2, "high": 3}
        return max((a.risk_level for a in plan.actions), key=lambda r: order[r], default="low")

    @staticmethod
    def _roles_for_action(action: ProposedAction) -> list[str]:
        if action.tool_name == "freeze_batch":
            return ["quality_lead"]
        if action.tool_name == "control_device":
            return ["maintenance_lead"]
        return ["maintenance_lead"]

    # ---------- 对外运行接口 ----------

    def run(self, raw_event: dict[str, Any], *, thread_id: str | None = None) -> dict[str, Any]:
        thread_id = thread_id or str(raw_event.get("trace_id") or "default")
        config = {"configurable": {"thread_id": thread_id}}
        return self._graph.invoke({"event": raw_event, "node_trace": [], "errors": []}, config)

    def resume(self, thread_id: str, decision: dict[str, Any]) -> dict[str, Any]:
        from langgraph.types import Command

        config = {"configurable": {"thread_id": thread_id}}
        return self._graph.invoke(Command(resume=decision), config)

    def replay(self, trace_id: str) -> str:
        return self._audit.dump_json(trace_id)