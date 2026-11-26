"""阶段 4.4 工具执行前置检查：存在性 -> 权限 -> 参数 -> 风险 -> 幂等 -> 超时 -> 审计。"""

from __future__ import annotations

import time
from typing import Any

from industrial_agent.audit.recorder import AuditRecorder
from industrial_agent.tools.contracts import ToolContext, ToolResult, ToolSpec


class ToolRegistry:
    """所有智能体访问外部系统的唯一入口。"""

    def __init__(self, audit: AuditRecorder | None = None) -> None:
        self._tools: dict[str, ToolSpec] = {}
        self._audit = audit or AuditRecorder()
        self._idempotency: dict[str, ToolResult] = {}

    def register(self, spec: ToolSpec) -> None:
        if spec.name in self._tools:
            raise ValueError(f"工具已存在：{spec.name}")
        self._tools[spec.name] = spec

    def get(self, name: str) -> ToolSpec | None:
        return self._tools.get(name)

    def names(self) -> list[str]:
        return sorted(self._tools)

    def catalog(self) -> list[dict[str, Any]]:
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
        args = args or {}

        spec = self._tools.get(name)
        if spec is None:
            return self._deny(context, name, args, "TOOL_NOT_FOUND", f"工具不存在或未启用：{name}")

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

        if spec.risk == "high" and not context.allow_high_risk:
            return self._deny(
                context,
                name,
                args,
                "HIGH_RISK_BLOCKED",
                "高风险动作需人工确认，已拦截",
                risk=spec.risk,
            )

        if idempotency_key and spec.kind in ("write", "control"):
            cached = self._idempotency.get(idempotency_key)
            if cached is not None:
                return cached

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

        started = time.perf_counter()
        result = self._execute(spec, args, context, started)

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

        if idempotency_key and result.ok and spec.kind in ("write", "control"):
            self._idempotency[idempotency_key] = result
        return result

    def _execute(
        self, spec: ToolSpec, args: dict[str, Any], context: ToolContext, started: float
    ) -> ToolResult:
        attempts = 2 if (spec.retryable and spec.kind == "read") else 1
        last_error: Exception | None = None
        for _ in range(attempts):
            if time.perf_counter() - started > spec.timeout_seconds:
                return ToolResult(
                    ok=False,
                    error_code="TOOL_TIMEOUT",
                    error_message=f"工具 {spec.name} 超时（>{spec.timeout_seconds}s）",
                    source=spec.name,
                )
            try:
                data = spec.handler(**args)  # type: ignore[misc]
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
                return ToolResult(
                    ok=False,
                    error_code="INVALID_ARGS",
                    error_message=f"参数校验失败：{exc}",
                    source=spec.name,
                )
            except Exception as exc:  # noqa: BLE001
                last_error = exc

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