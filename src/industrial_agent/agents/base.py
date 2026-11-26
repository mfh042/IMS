"""阶段 5.2 子 Agent 基类与共享上下文。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from industrial_agent.agents.llm import LLMClient
from industrial_agent.audit.recorder import AuditRecorder
from industrial_agent.knowledge.store import KnowledgeStore
from industrial_agent.models.events import Incident
from industrial_agent.models.findings import AgentFinding
from industrial_agent.models.knowledge import KnowledgeHit
from industrial_agent.tools.contracts import ToolContext
from industrial_agent.tools.registry import ToolRegistry


@dataclass
class AgentContext:
    incident: Incident
    tool_context: ToolContext
    tools: ToolRegistry
    knowledge: KnowledgeStore
    llm: LLMClient | None = None
    scenario: str = "设备异常诊断"
    tool_results: dict[str, Any] = field(default_factory=dict)
    knowledge_hits: list[KnowledgeHit] = field(default_factory=list)


class BaseAgent:
    """职责单一、输出结构化的子 Agent。"""

    name: str = "base"
    domain: str = "通用"

    def __init__(self, audit: AuditRecorder | None = None) -> None:
        self._audit = audit

    def analyze(self, ctx: AgentContext) -> AgentFinding:
        finding = self._analyze(ctx)
        if self._audit is not None:
            self._audit.record(
                trace_id=ctx.tool_context.trace_id,
                incident_id=ctx.incident.incident_id,
                actor=self.name,
                action="agent:analyze",
                target=self.name,
                output_snapshot=finding.model_dump(mode="json"),
                decision="executed",
            )
        return finding

    def _analyze(self, ctx: AgentContext) -> AgentFinding:  # pragma: no cover - 抽象
        raise NotImplementedError

    def _call(self, ctx: AgentContext, tool_name: str, **args: Any) -> Any:
        result = ctx.tools.invoke(tool_name, args, context=ctx.tool_context)
        ctx.tool_results[tool_name] = result.model_dump(mode="json")
        return result

    def _llm_finding(self, ctx: AgentContext) -> AgentFinding | None:
        if ctx.llm is None:
            return None
        return ctx.llm.structured_finding(
            domain=self.domain,
            scenario=ctx.scenario,
            incident=ctx.incident.model_dump(mode="json"),
            tool_results=ctx.tool_results,
            knowledge_chunks=[h.chunk.model_dump(mode="json") for h in ctx.knowledge_hits],
        )