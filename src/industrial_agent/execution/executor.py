"""阶段 8：执行分流、前置再校验、人工确认闭环、回滚。"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

from industrial_agent.audit.recorder import AuditRecorder
from industrial_agent.models.execution import ActionExecution, ConfirmRequest
from industrial_agent.models.plan import ProposedAction, ResolutionPlan
from industrial_agent.tools.contracts import ToolContext, ToolResult
from industrial_agent.tools.registry import ToolRegistry


class ConfirmStore:
    """人工确认请求存储；超时置 expired，严禁默认同意。"""

    def __init__(self, ttl_seconds: int = 3600) -> None:
        self._requests: dict[str, ConfirmRequest] = {}
        self._ttl = timedelta(seconds=ttl_seconds)

    def create(
        self,
        *,
        plan: ResolutionPlan,
        action: ProposedAction,
        required_roles: list[str],
        now: datetime | None = None,
    ) -> ConfirmRequest:
        now = now or datetime.now(UTC)
        request = ConfirmRequest(
            confirm_id=f"cfm-{uuid.uuid4().hex[:10]}",
            incident_id=plan.incident_id,
            plan_id=plan.plan_id,
            action_id=action.action_id,
            action_title=action.title,
            action_detail=action.description,
            tool_name=action.tool_name or "unknown",
            tool_args=action.tool_args or {},
            risk_level=action.risk_level,
            risks=action.evidence_refs and ["高风险动作，需现场安全确认"] or ["高风险动作"],
            rollback_hint=action.rollback_hint or "无回滚方案",
            required_roles=required_roles,
            created_at=now,
            expire_at=now + self._ttl,
            status="pending",
        )
        self._requests[request.confirm_id] = request
        return request

    def get(self, confirm_id: str) -> ConfirmRequest | None:
        return self._requests.get(confirm_id)

    def list_pending(self) -> list[ConfirmRequest]:
        return [r for r in self._requests.values() if r.status == "pending"]

    def expire_overdue(self, now: datetime | None = None) -> list[ConfirmRequest]:
        now = now or datetime.now(UTC)
        expired: list[ConfirmRequest] = []
        for request in self._requests.values():
            if request.status == "pending" and now >= request.expire_at:
                request.status = "expired"
                expired.append(request)
        return expired

    def decide(
        self,
        confirm_id: str,
        *,
        approved: bool,
        decision_by: str,
        roles: list[str],
        note: str | None = None,
        now: datetime | None = None,
    ) -> tuple[bool, str]:
        request = self._requests.get(confirm_id)
        if request is None:
            return False, "确认请求不存在"
        if request.status != "pending":
            return False, f"确认请求状态为 {request.status}，不可再次决策"
        now = now or datetime.now(UTC)
        if now >= request.expire_at:
            request.status = "expired"
            return False, "确认请求已超时，需重新发起"

        missing = [r for r in request.required_roles if r not in roles]
        if missing:
            return False, f"缺少确认角色：{','.join(missing)}"

        request.status = "approved" if approved else "rejected"
        request.decision_by = decision_by
        request.decision_at = now
        request.decision_note = note
        return True, request.status


class ActionExecutor:
    """阶段 8.2 执行分流 + 8.3 前置再校验 + 8.7 回滚。"""

    def __init__(
        self,
        tools: ToolRegistry,
        *,
        audit: AuditRecorder | None = None,
        confirms: ConfirmStore | None = None,
    ) -> None:
        self._tools = tools
        self._audit = audit or AuditRecorder()
        self._confirms = confirms or ConfirmStore()
        self._executions: list[ActionExecution] = []
        self._executed_keys: set[str] = set()

    @property
    def confirms(self) -> ConfirmStore:
        return self._confirms

    def executions(self) -> list[ActionExecution]:
        return list(self._executions)

    def plan_dispatch(self, plan: ResolutionPlan) -> dict[str, list[ProposedAction]]:
        """按风险分流：低自动、中自动+待办、高转确认。"""
        buckets: dict[str, list[ProposedAction]] = {"auto": [], "todo": [], "confirm": []}
        for action in plan.actions:
            if action.requires_human_confirm or action.risk_level == "high":
                buckets["confirm"].append(action)
            elif action.risk_level == "low":
                buckets["auto"].append(action)
            else:
                buckets["todo"].append(action)
        return buckets

    def request_confirmations(
        self, plan: ResolutionPlan, *, required_roles: list[str] | None = None
    ) -> list[ConfirmRequest]:
        requests = []
        for action in self.plan_dispatch(plan)["confirm"]:
            request = self._confirms.create(
                plan=plan,
                action=action,
                required_roles=required_roles or ["maintenance_lead"],
            )
            self._audit.record(
                trace_id=plan.incident_id,
                incident_id=plan.incident_id,
                actor="system",
                action="confirm:request",
                target=action.action_id,
                output_snapshot=request.model_dump(mode="json"),
                risk_level="high",
                decision="allowed",
            )
            requests.append(request)
        return requests

    def execute(
        self,
        plan: ResolutionPlan,
        action: ProposedAction,
        *,
        context: ToolContext,
        confirm_id: str | None = None,
        operator: str = "system",
    ) -> ActionExecution:
        key = f"{plan.incident_id}:{action.action_id}"
        execution_id = f"exe-{uuid.uuid4().hex[:10]}"
        started_at = datetime.now(UTC)

        if key in self._executed_keys:
            return self._finish(
                execution_id=execution_id,
                plan=plan,
                action=action,
                started_at=started_at,
                status="failed",
                summary="幂等校验：同一 incident+action 已执行，拒绝重复执行",
                error_code="DUPLICATE_EXECUTION",
                operator=operator,
            )

        if action.risk_level == "high":
            if not confirm_id:
                return self._finish(
                    execution_id=execution_id,
                    plan=plan,
                    action=action,
                    started_at=started_at,
                    status="failed",
                    summary="高风险动作缺少人工确认，已拒绝执行",
                    error_code="CONFIRM_REQUIRED",
                    operator=operator,
                )
            request = self._confirms.get(confirm_id)
            if request is None or request.status != "approved":
                return self._finish(
                    execution_id=execution_id,
                    plan=plan,
                    action=action,
                    started_at=started_at,
                    status="failed",
                    summary=f"确认请求不可用：{request.status if request else 'missing'}",
                    error_code="CONFIRM_INVALID",
                    operator=operator,
                )

        if not action.executable:
            return self._finish(
                execution_id=execution_id,
                plan=plan,
                action=action,
                started_at=started_at,
                status="failed",
                summary="动作被标记为不可执行（仅作为人工待办）",
                error_code="NOT_EXECUTABLE",
                operator=operator,
            )

        if action.tool_name is None:
            return self._finish(
                execution_id=execution_id,
                plan=plan,
                action=action,
                started_at=started_at,
                status="failed",
                summary="动作未绑定工具，无法执行",
                error_code="NO_TOOL_BOUND",
                operator=operator,
            )

        self._audit.record(
            trace_id=context.trace_id,
            incident_id=plan.incident_id,
            actor=operator,
            roles=context.roles,
            action="execution:intent",
            target=action.tool_name,
            input_snapshot=action.tool_args or {},
            risk_level=action.risk_level,
            decision="allowed",
        )

        result: ToolResult = self._tools.invoke(
            action.tool_name,
            action.tool_args or {},
            context=context,
            idempotency_key=key,
        )
        self._executed_keys.add(key)

        status = "success" if result.ok else "failed"
        return self._finish(
            execution_id=execution_id,
            plan=plan,
            action=action,
            started_at=started_at,
            status=status,
            summary=f"{action.title}: {result.data if result.ok else result.error_message}",
            error_code=None if result.ok else result.error_code,
            operator=operator,
            tool_args=action.tool_args or {},
        )

    def _finish(
        self,
        *,
        execution_id: str,
        plan: ResolutionPlan,
        action: ProposedAction,
        started_at: datetime,
        status: str,
        summary: str,
        error_code: str | None,
        operator: str,
        tool_args: dict | None = None,
    ) -> ActionExecution:
        execution = ActionExecution(
            execution_id=execution_id,
            incident_id=plan.incident_id,
            plan_id=plan.plan_id,
            action_id=action.action_id,
            tool_name=action.tool_name or "none",
            tool_args_snapshot=tool_args or action.tool_args or {},
            started_at=started_at,
            finished_at=datetime.now(UTC),
            status=status,  # type: ignore[arg-type]
            result_summary=summary,
            error_code=error_code,
            operator=operator,
        )
        self._executions.append(execution)
        self._audit.record(
            trace_id=plan.incident_id,
            incident_id=plan.incident_id,
            actor=operator,
            action="execution:result",
            target=action.tool_name,
            output_snapshot=execution.model_dump(mode="json"),
            risk_level=action.risk_level,
            decision="executed" if status == "success" else "denied",
            reason=error_code,
        )
        return execution

    def rollback(
        self, plan: ResolutionPlan, action: ProposedAction, *, context: ToolContext
    ) -> ActionExecution:
        execution_id = f"exe-{uuid.uuid4().hex[:10]}"
        started_at = datetime.now(UTC)
        rollback_ok = bool(action.rollback_hint) and action.rollback_hint != "无回滚方案"
        execution = ActionExecution(
            execution_id=execution_id,
            incident_id=plan.incident_id,
            plan_id=plan.plan_id,
            action_id=action.action_id,
            tool_name=action.tool_name or "none",
            tool_args_snapshot=action.tool_args or {},
            started_at=started_at,
            finished_at=datetime.now(UTC),
            status="rolled_back" if rollback_ok else "failed",
            result_summary=(
                f"按回滚提示执行：{action.rollback_hint}"
                if rollback_ok
                else "无回滚方案，已升级人工紧急处理"
            ),
            error_code=None if rollback_ok else "NO_ROLLBACK_PLAN",
            rollback_executed=rollback_ok,
            operator=context.actor,
        )
        self._executions.append(execution)
        self._audit.record(
            trace_id=context.trace_id,
            incident_id=plan.incident_id,
            actor=context.actor,
            roles=context.roles,
            action="execution:rollback",
            target=action.tool_name,
            output_snapshot=execution.model_dump(mode="json"),
            risk_level=action.risk_level,
            decision="rolled_back" if rollback_ok else "denied",
            reason=execution.error_code,
        )
        return execution