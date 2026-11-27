"""阶段 4.4 工具执行前置检查：存在性 -> 权限 -> 风险 -> 幂等 -> 审计 -> 执行 -> 审计 -> 缓存。

本模块是所有智能体访问外部工业系统的唯一入口（工具层闸，三道闸的第一道）。
invoke() 内部是一条九步前置检查链，任一不通过立即拒绝、绝不调用真实 handler，
保证权限 / 风险 / 审计 / 幂等 / 超时全部集中管控、无法绕过。

设计原则：
- 拒绝统一走 _deny（先落审计再返回 degraded 失败结果），不漏审计；
- 下游异常不抛出，归一化为 ToolResult(ok=False, degraded=True)，让智能体降级而非崩溃；
- 幂等缓存仅覆盖写/控制且成功的结果（只读天然幂等无需缓存，失败结果不缓存以便重试）。
"""

from __future__ import annotations

# ---- 标准库 ----
import time                       # perf_counter 用于超时判定与耗时统计
from typing import Any            # handler 返回值 / catalog 字典的值类型

# ---- 本项目 ----
from industrial_agent.audit.recorder import AuditRecorder       # 调用前后落痕（全链路可追溯）
from industrial_agent.tools.contracts import ToolContext, ToolResult, ToolSpec  # 工具契约


class ToolRegistry:
    """所有智能体访问外部系统的唯一入口。

    持有三个内部状态：
    - _tools：name -> ToolSpec 的注册表；
    - _audit：审计记录器（调用前后都写）；
    - _idempotency：写/控制工具的成功结果缓存（按 idempotency_key 去重）。
    """

    def __init__(self, audit: AuditRecorder | None = None) -> None:
        # 注入审计器便于测试替换；默认构造一个
        self._tools: dict[str, ToolSpec] = {}
        self._audit = audit or AuditRecorder()
        # 幂等缓存：key -> ToolResult，仅存写/控制的成功结果
        self._idempotency: dict[str, ToolResult] = {}

    def register(self, spec: ToolSpec) -> None:
        """注册一个工具；重名直接报错（防止后注册的覆盖已生效的风险配置）。"""
        if spec.name in self._tools:
            raise ValueError(f"工具已存在：{spec.name}")
        self._tools[spec.name] = spec

    def get(self, name: str) -> ToolSpec | None:
        """按名取工具 spec（不存在返回 None，不抛异常）。"""
        return self._tools.get(name)

    def names(self) -> list[str]:
        """返回已注册工具名（排序后），便于前端/API 列举。"""
        return sorted(self._tools)

    def catalog(self) -> list[dict[str, Any]]:
        """返回工具清单摘要（不含 handler），供前端展示可用工具及风险/权限。"""
        return [
            {
                "name": s.name,
                "kind": s.kind,
                "risk": s.risk,
                "auto_executable": s.auto_executable,
                "required_roles": list(s.required_roles),
            }
            for s in sorted(self._tools.values(), key=lambda s: s.name)
        ]

    def invoke(
        self,
        name: str,
        args: dict[str, Any] | None = None,
        *,
        context: ToolContext,
        idempotency_key: str | None = None,
    ) -> ToolResult:
        """九步前置检查链主入口：任一检查不通过立即拒绝，绝不调用真实 handler。

        Args:
            name: 工具名（须已注册）。
            args: 传给 handler 的关键字参数。
            context: 调用上下文（角色 + 两个许可开关），关键字参数强制显式传递。
            idempotency_key: 幂等键；写/控制工具传入后，同 key 直接返回上次成功结果。

        Returns:
            ToolResult：成功返回 data；失败返回 ok=False + error_code + degraded。
        """
        args = args or {}

        # ---- 步骤 1：存在性 —— 工具未注册/未启用 ----
        spec = self._tools.get(name)
        if spec is None:
            return self._deny(context, name, args, "TOOL_NOT_FOUND", f"工具不存在或未启用：{name}")

        # ---- 步骤 2：权限 —— 调用者角色必须覆盖工具要求的全部角色 ----
        missing_roles = [r for r in spec.required_roles if r not in context.roles]
        if missing_roles:
            return self._deny(
                context,
                name,
                args,
                "PERMISSION_DENIED",
                f"缺少角色权限：{','.join(missing_roles)}",
                risk=spec.risk,
            )

        # ---- 步骤 3：高风险许可 —— high 工具必须人工确认后才放开 ----
        if spec.risk == "high" and not context.allow_high_risk:
            return self._deny(
                context,
                name,
                args,
                "HIGH_RISK_BLOCKED",
                "高风险动作需人工确认，已拦截",
                risk=spec.risk,
            )

        # ---- 步骤 4：写许可 —— 只读取证 / 影子模式下拦截一切写/控制 ----
        if spec.kind in ("write", "control") and not context.allow_write:
            return self._deny(
                context,
                name,
                args,
                "WRITE_BLOCKED",
                "当前上下文为只读取证/影子模式，写操作已拦截",
                risk=spec.risk,
            )

        # ---- 步骤 5：幂等去重 —— 同 key 的写/控制直接返回上次成功结果，不重复执行 ----
        if idempotency_key and spec.kind in ("write", "control"):
            cached = self._idempotency.get(idempotency_key)
            if cached is not None:
                return cached

        # ---- 步骤 6：调用前审计 —— 决策 allowed，记录输入快照 ----
        self._audit.record(
            trace_id=context.trace_id,
            incident_id=context.incident_id,
            actor=context.actor,
            roles=context.roles,
            action=f"tool:{name}",
            target=name,
            input_snapshot=args,
            risk_level=spec.risk,
            decision="allowed",
        )

        # ---- 步骤 7：执行 —— 超时判定 + 只读可重试 + 异常归一化 ----
        started = time.perf_counter()
        result = self._execute(spec, args, context, started)

        # ---- 步骤 8：调用后审计 —— 决策 executed/denied，记录输出快照与失败原因 ----
        self._audit.record(
            trace_id=context.trace_id,
            incident_id=context.incident_id,
            actor=context.actor,
            roles=context.roles,
            action=f"tool:{name}:result",
            target=name,
            output_snapshot=result.model_dump(mode="json"),
            risk_level=spec.risk,
            decision="executed" if result.ok else "denied",
            reason=result.error_code,
        )

        # ---- 步骤 9：幂等缓存 —— 仅成功的写/控制结果入缓存（失败不缓存，便于下次重试）----
        if idempotency_key and result.ok and spec.kind in ("write", "control"):
            self._idempotency[idempotency_key] = result
        return result

    def _execute(
        self, spec: ToolSpec, args: dict[str, Any], context: ToolContext, started: float
    ) -> ToolResult:
        """执行 handler：超时 / 只读重试 / 异常归一化（内部方法，由 invoke 步骤 7 调用）。

        容错策略：
        - 超过 spec.timeout_seconds → TOOL_TIMEOUT；
        - 只读且 retryable → 最多尝试 2 次；
        - TypeError（参数不匹配）→ INVALID_ARGS（立即终止，重试无意义）；
        - 其他异常 → 记 last_error，耗尽重试后 DOWNSTREAM_UNAVAILABLE（degraded=True）；
        - handler 返回 None → NO_DATA（下游无数据，degraded=True）。
        """
        # 写/控制不重试（避免重复副作用），靠幂等键兜底；只读可重试 1 次
        attempts = 2 if (spec.retryable and spec.kind == "read") else 1
        last_error: Exception | None = None
        for _ in range(attempts):
            # 每次尝试前先判超时，防止 handler 卡死后重试又超时
            if time.perf_counter() - started > spec.timeout_seconds:
                return ToolResult(
                    ok=False,
                    error_code="TOOL_TIMEOUT",
                    error_message=f"工具 {spec.name} 超时（>{spec.timeout_seconds}s）",
                    source=spec.name,
                )
            try:
                data = spec.handler(**args)  # type: ignore[misc]
                # None 视为下游无数据：降级而非成功（避免智能体把空当有）
                if data is None:
                    return ToolResult(
                        ok=False,
                        error_code="NO_DATA",
                        error_message="下游未返回数据",
                        source=spec.name,
                        degraded=True,
                    )
                return ToolResult(ok=True, data=data, source=spec.name)
            except TypeError as exc:
                # 参数错误（缺参/多参/类型错）立即返回，重试不会改变结果
                return ToolResult(
                    ok=False,
                    error_code="INVALID_ARGS",
                    error_message=f"参数校验失败：{exc}",
                    source=spec.name,
                )
            except Exception as exc:  # noqa: BLE001
                # 下游其他异常：记录后重试（仅 read 会到第二次）
                last_error = exc

        # 耗尽重试仍失败：下游不可用，标记 degraded 让智能体降级
        return ToolResult(
            ok=False,
            error_code="DOWNSTREAM_UNAVAILABLE",
            error_message=f"下游不可用：{last_error}",
            source=spec.name,
            degraded=True,
        )

    def _deny(
        self,
        context: ToolContext,
        name: str,
        args: dict[str, Any],
        code: str,
        message: str,
        *,
        risk: str | None = None,
    ) -> ToolResult:
        """统一拒绝出口：先落审计（decision=denied），再返回 degraded 失败结果。

        所有前置检查拒绝都走这里，保证"被拒绝"也有审计痕迹，不会出现调用无记录的盲区。
        """
        self._audit.record(
            trace_id=context.trace_id,
            incident_id=context.incident_id,
            actor=context.actor,
            roles=context.roles,
            action=f"tool:{name}",
            target=name,
            input_snapshot=args,
            risk_level=risk,
            decision="denied",
            reason=f"{code}: {message}",
        )
        return ToolResult(
            ok=False,
            error_code=code,
            error_message=message,
            source=name,
            degraded=True,
        )