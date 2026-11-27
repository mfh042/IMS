"""阶段 5.2 子 Agent 基类与共享上下文。

本模块提供两个核心抽象：
- AgentContext：智能体运行时的"共享工作台"，主图节点在调用智能体前构造并传入；
- BaseAgent：所有 13 类子智能体的基类，用模板方法模式封装"取证分析 → 审计留痕"的固定流程，
  子类只需实现 _analyze 这一处业务逻辑。
"""

from __future__ import annotations

# ---- 标准库 ----
from dataclasses import dataclass, field
from typing import Any

# ---- 同模块：LLM 客户端（可选增强）----
from industrial_agent.agents.llm import LLMClient
# ---- 横切：审计记录器（留痕用，可空）----
from industrial_agent.audit.recorder import AuditRecorder
# ---- 能力层：知识库检索 ----
from industrial_agent.knowledge.store import KnowledgeStore
# ---- 模型层：事件 / 结论 / 知识命中的数据契约 ----
from industrial_agent.models.events import Incident
from industrial_agent.models.findings import AgentFinding
from industrial_agent.models.knowledge import KnowledgeHit
# ---- 工具层：工具调用上下文 + 工具注册表（九步检查链入口）----
from industrial_agent.tools.contracts import ToolContext
from industrial_agent.tools.registry import ToolRegistry


@dataclass
class AgentContext:
    """子 Agent 运行时共享上下文。

    主图节点（dispatch_agents）在并发调用各智能体之前构造本对象并传入，
    保证每个智能体都能拿到同一份事件、工具、知识库视图，同时把各自的取数结果
    累积到 tool_results / knowledge_hits，供 LLM 增强阶段统一使用。
    """

    # 当前正在处置的事件（已归一化 + 治理后的 Incident）
    incident: Incident
    # 工具调用上下文：携带 trace_id / operator 等审计字段，贯穿整条链路
    tool_context: ToolContext
    # 工具注册表：所有工具调用都走它的九步检查链，智能体不直接碰底层实现
    tools: ToolRegistry
    # 知识库：智能体可按需检索（主图通常已预检索并填入 knowledge_hits）
    knowledge: KnowledgeStore
    # LLM 客户端：未配置时为 None，智能体自动走规则降级，不影响运行
    llm: LLMClient | None = None
    # 场景描述，用于拼装 LLM 提示词（如"设备异常诊断"）
    scenario: str = "设备异常诊断"
    # 本次运行累积的工具调用结果：{工具名: 结果快照}，供 LLM 增强与审计使用
    tool_results: dict[str, Any] = field(default_factory=dict)
    # 本次运行命中的知识片段：主图预检索后填入，智能体直接复用
    knowledge_hits: list[KnowledgeHit] = field(default_factory=list)


class BaseAgent:
    """职责单一、输出结构化的子 Agent 基类。

    设计为模板方法：analyze() 是固定骨架（调用子类分析 + 写审计），
    _analyze() 是子类必须实现的变化点。这样所有智能体的审计行为保持一致，
    子类只需关注自己的取证分析逻辑。
    """

    # 智能体英文标识，写入审计 actor 与 AgentFinding.agent_name
    name: str = "base"
    # 领域名称，用于拼装 LLM 提示词（如"设备诊断""质量追溯"）
    domain: str = "通用"

    def __init__(self, audit: AuditRecorder | None = None) -> None:
        # 审计记录器可选：单测或离线模拟时不传，生产环境由工厂注入
        self._audit = audit

    def analyze(self, ctx: AgentContext) -> AgentFinding:
        """模板方法：子类分析 → 自动审计留痕 → 返回结论。

        所有智能体对外只暴露这一个入口，保证审计不会被子类绕过。
        """
        # 1. 调子类实现的具体取证分析，得到结构化结论
        finding = self._analyze(ctx)
        # 2. 若注入了审计器，把本次分析的完整输出快照落痕（decision=executed）
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
        """子类必须实现的取证分析逻辑，返回 AgentFinding。"""
        raise NotImplementedError

    def _call(self, ctx: AgentContext, tool_name: str, **args: Any) -> Any:
        """统一工具调用入口：走 ToolRegistry 九步检查链，并缓存结果。

        子类不直接调 ctx.tools.invoke，而是通过本方法，确保：
        1. 所有调用都经过权限/风险/幂等/审计等九步检查；
        2. 结果自动累积到 ctx.tool_results，供后续 LLM 增强使用。
        """
        # 委托给工具注册表执行（内部走九步链），context 携带 trace_id 等审计字段
        result = ctx.tools.invoke(tool_name, args, context=ctx.tool_context)
        # 缓存结果快照（JSON 模式），LLM 增强时会把这些数据拼进提示词
        ctx.tool_results[tool_name] = result.model_dump(mode="json")
        return result

    def _llm_finding(self, ctx: AgentContext) -> AgentFinding | None:
        """可选 LLM 结构化增强：未配置 LLM 时返回 None，由子类回落到规则结果。

        这是"规则优先 + LLM 可选降级"策略的关键：LLM 只是增强项，
        返回 None 不代表失败，而是告诉子类"用你自己的规则结论即可"。
        """
        # 上下文未注入 LLM 客户端（未配置或被禁用）→ 直接降级
        if ctx.llm is None:
            return None
        # 把领域、场景、事件、已取数结果、知识片段一并交给 LLM 做结构化输出
        return ctx.llm.structured_finding(
            domain=self.domain,
            scenario=ctx.scenario,
            incident=ctx.incident.model_dump(mode="json"),
            tool_results=ctx.tool_results,
            knowledge_chunks=[h.chunk.model_dump(mode="json") for h in ctx.knowledge_hits],
        )