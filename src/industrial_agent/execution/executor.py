"""阶段 8：执行分流、前置再校验、人工确认闭环、回滚。

本文件是系统"执行层"的唯一入口，承接主图节点 10（execute_safe_actions /
execute_medium_risk_actions / request_human_confirm）的调用，负责把规划好的处置方案
真正落到工具执行上，并保证全过程安全可控。

核心职责：
1. **三分桶分流**（plan_dispatch）：按风险把方案动作拆成 auto（低风险自动）/ todo（中风险待办）/
   confirm（高风险待确认）三个桶，主图的风险路由据此选择执行路径；
2. **前置再校验**（execute）：执行前再过五道闸——幂等、高风险确认、可执行性、工具绑定、工具调用；
   这是"执行层"的独立闸，与工具层（ToolRegistry 九步链）和方案层（PlanValidator）共同构成三道闸；
3. **人工确认闭环**（ConfirmStore）：高风险动作必须经人工批准，带 TTL 超时与角色校验，
   严禁默认同意；confirm_id 是连接"请求 → 批准 → 执行"的纽带；
4. **回滚**（rollback）：执行失败后按 rollback_hint 尝试回滚，无回滚方案则升级人工紧急处理；
5. **审计**：每一步意图与结果都落 AuditRecorder，保证可追溯。
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

from industrial_agent.audit.recorder import AuditRecorder
from industrial_agent.models.execution import ActionExecution, ConfirmRequest
from industrial_agent.models.plan import ProposedAction, ResolutionPlan
from industrial_agent.tools.contracts import ToolContext, ToolResult
from industrial_agent.tools.registry import ToolRegistry


class ConfirmStore:
    """人工确认请求存储；超时置 expired，严禁默认同意。

    设计要点：
    - 内存存储（dict），ConfirmRequest 的完整生命周期 pending → approved / rejected / expired
      都在这里流转；
    - TTL（默认 3600s）：超时自动置 expired，避免"挂起的确认请求被无限期持有后被误批准"；
    - 角色校验：批准人必须具备动作要求的全部角色（RBAC 最小化授权），缺一不可；
    - 安全默认：decide() 返回 (ok, status)，ok=False 表示确认无效（不存在/已决策/超时/缺角色），
      调用方据此拒绝执行，绝不存在"默认同意"的分支。
    """

    def __init__(self, ttl_seconds: int = 3600) -> None:
        # confirm_id → ConfirmRequest，所有待处理/已处理的确认请求都在这
        self._requests: dict[str, ConfirmRequest] = {}
        # 确认请求的存活时长，超过 expire_at 即视为过期
        self._ttl = timedelta(seconds=ttl_seconds)

    def create(
        self,
        *,
        plan: ResolutionPlan,
        action: ProposedAction,
        required_roles: list[str],
        now: datetime | None = None,
    ) -> ConfirmRequest:
        """为一个高风险动作创建确认请求，写入存储并返回（status=pending）。

        created_at = now，expire_at = now + ttl；调用方（主图节点 10c）拿到 request 后
        通过 interrupt() 把它抛给前端展示，等待人工决策。
        """
        now = now or datetime.now(UTC)
        request = ConfirmRequest(
            confirm_id=f"cfm-{uuid.uuid4().hex[:10]}",  # 短 id，便于人眼识别与日志检索
            incident_id=plan.incident_id,
            plan_id=plan.plan_id,
            action_id=action.action_id,
            action_title=action.title,
            action_detail=action.description,
            tool_name=action.tool_name or "unknown",
            tool_args=action.tool_args or {},
            risk_level=action.risk_level,
            # 有 evidence_refs 说明动作有证据支撑，提示"需现场安全确认"；否则只标"高风险动作"
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
        """按 confirm_id 取确认请求，不存在返回 None（调用方据此判断确认是否有效）。"""
        return self._requests.get(confirm_id)

    def list_pending(self) -> list[ConfirmRequest]:
        """列出所有仍在等待人工决策的确认请求（供前端展示待办列表）。"""
        return [r for r in self._requests.values() if r.status == "pending"]

    def expire_overdue(self, now: datetime | None = None) -> list[ConfirmRequest]:
        """扫描并把超时的 pending 请求置为 expired，返回被过期的列表。

        可由定时任务或主流程周期性调用，清理挂起过久的确认请求，防止资源泄漏与误批准。
        """
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
        """对确认请求做出决策（批准/拒绝），返回 (是否有效, 最终状态)。

        校验顺序（任一不通过即返回 ok=False，调用方必须拒绝执行）：
        1. 请求存在 → 否则 "确认请求不存在"；
        2. 状态必须是 pending → 否则不可重复决策（防止同一请求被批准两次）；
        3. 未超时 → 否则置 expired；
        4. 批准人具备全部 required_roles → 否则缺角色拒绝；
        通过后置 approved / rejected，记录决策人 / 时间 / 备注。
        """
        request = self._requests.get(confirm_id)
        if request is None:
            return False, "确认请求不存在"
        if request.status != "pending":
            # 已 approved / rejected / expired 的请求不允许再次决策
            return False, f"确认请求状态为 {request.status}，不可再次决策"
        now = now or datetime.now(UTC)
        if now >= request.expire_at:
            request.status = "expired"
            return False, "确认请求已超时，需重新发起"

        # 角色校验：required_roles 是动作要求的批准角色，批准人的 roles 必须全部覆盖
        missing = [r for r in request.required_roles if r not in roles]
        if missing:
            return False, f"缺少确认角色：{','.join(missing)}"

        request.status = "approved" if approved else "rejected"
        request.decision_by = decision_by
        request.decision_at = now
        request.decision_note = note
        return True, request.status


class ActionExecutor:
    """阶段 8.2 执行分流 + 8.3 前置再校验 + 8.7 回滚。

    执行层的唯一入口：主图三个执行节点（低/中/高风险）都调它。
    - plan_dispatch：三分桶，决定动作走哪条执行路径；
    - execute：执行单个动作，前置五道闸校验；
    - rollback：执行失败后回滚；
    内部维护已执行幂等键集合 _executed_keys，防止同一 incident+action 重复执行。
    """

    def __init__(
        self,
        tools: ToolRegistry,
        *,
        audit: AuditRecorder | None = None,
        confirms: ConfirmStore | None = None,
    ) -> None:
        self._tools = tools  # 工具注册表：真正调工具的入口，内部带九步检查链
        self._audit = audit or AuditRecorder()  # 审计记录器，默认内存 + JSONL 落盘
        self._confirms = confirms or ConfirmStore()  # 人工确认存储，默认 TTL 3600s
        self._executions: list[ActionExecution] = []  # 所有执行记录（成功/失败/回滚）
        # 已执行的幂等键集合：incident_id:action_id，防止重复执行
        self._executed_keys: set[str] = set()

    @property
    def confirms(self) -> ConfirmStore:
        """暴露 ConfirmStore，供主图节点 10c 直接操作确认请求（如取状态、手动过期）。"""
        return self._confirms

    def executions(self) -> list[ActionExecution]:
        """返回所有执行记录的副本（外部不应直接修改内部列表）。"""
        return list(self._executions)

    def plan_dispatch(self, plan: ResolutionPlan) -> dict[str, list[ProposedAction]]:
        """按风险分流：低自动、中自动+待办、高转确认。

        返回三个桶：auto（低风险，可自动执行）/ todo（中风险，灰度决定是否自动或转待办）/
        confirm（高风险或显式 requires_human_confirm，必须人工确认）。
        主图的 _route_by_risk 就是按这三个桶的内容选择执行节点。
        """
        buckets: dict[str, list[ProposedAction]] = {"auto": [], "todo": [], "confirm": []}
        for action in plan.actions:
            if action.requires_human_confirm or action.risk_level == "high":
                # 显式要求确认 或 风险等级 high → 进确认桶
                buckets["confirm"].append(action)
            elif action.risk_level == "low":
                # 低风险 → 自动执行桶
                buckets["auto"].append(action)
            else:
                # medium 及其他 → 待办桶（中风险，灰度控制）
                buckets["todo"].append(action)
        return buckets

    def request_confirmations(
        self, plan: ResolutionPlan, *, required_roles: list[str] | None = None
    ) -> list[ConfirmRequest]:
        """为方案中所有高风险（confirm 桶）动作批量创建确认请求，并落审计。

        主图节点 10c 调用此方法生成确认请求，再通过 interrupt() 抛给前端。
        required_roles 默认 ["maintenance_lead"]，但主图会按工具名传入更精确的角色
        （如 freeze_batch → quality_lead，见 orchestrator._roles_for_action）。
        """
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
        """执行单个动作：前置五道闸校验 → 调工具 → 落执行记录与审计。

        这是"执行层独立闸"，五道闸任一不通过都直接返回 failed，绝不调工具：
        1. 幂等校验：同一 incident+action 已执行过 → DUPLICATE_EXECUTION；
        2. 高风险确认：risk_level=high 必须有有效且 approved 的 confirm_id →
           CONFIRM_REQUIRED / CONFIRM_INVALID；
        3. 可执行性：action.executable=False → NOT_EXECUTABLE；
        4. 工具绑定：action.tool_name 为空 → NO_TOOL_BOUND；
        5. 工具调用：通过后调 tools.invoke（工具层九步链在此生效），结果落记录。

        注意：高风险动作即便主图节点 10c 已批准，这里也会再次校验 confirm_id 状态，
        防止"批准后确认被过期/撤销，仍被执行"的竞态。
        """
        # 幂等键：incident_id + action_id，唯一标识一次执行意图
        key = f"{plan.incident_id}:{action.action_id}"
        execution_id = f"exe-{uuid.uuid4().hex[:10]}"
        started_at = datetime.now(UTC)

        # 闸 1：幂等校验——同一动作已执行过则拒绝重复执行
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

        # 闸 2：高风险动作必须有人工确认
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
            # 确认请求不存在 或 状态不是 approved（可能已过期/被拒）→ 拒绝
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

        # 闸 3：动作被标记为不可执行（仅作人工提示）→ 不执行
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

        # 闸 4：动作未绑定工具 → 无法执行
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

        # 落"执行意图"审计（调工具前）：记录谁、以什么角色、打算对哪个工具做什么
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

        # 闸 5：调工具（工具层九步检查链 + 幂等缓存在此生效），传入幂等键防止工具层重复执行
        result: ToolResult = self._tools.invoke(
            action.tool_name,
            action.tool_args or {},
            context=context,
            idempotency_key=key,
        )
        # 工具调用返回后才把幂等键加入集合（即便失败也算"执行过"，避免重试无限循环）
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
        """统一收尾：构造 ActionExecution、追加到执行列表、落"执行结果"审计。

        execute 的所有分支（成功/各道闸失败）最终都走这里，保证每条执行都有完整记录。
        """
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
        """回滚：按动作的 rollback_hint 尝试回滚，无回滚方案则升级人工紧急处理。

        当前实现：若 rollback_hint 存在且非"无回滚方案"，标记为 rolled_back 并记录提示；
        否则标记 failed 带 NO_ROLLBACK_PLAN，提示人工介入。真正的反向工具调用可在此扩展。
        """
        execution_id = f"exe-{uuid.uuid4().hex[:10]}"
        started_at = datetime.now(UTC)
        # 是否有可用回滚方案：rollback_hint 存在且不是占位文案
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