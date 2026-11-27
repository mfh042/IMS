"""阶段 6.8：catalog 编号 -> 具体 Agent 实例的工厂。

13 类智能体的单一事实来源在 :mod:`industrial_agent.agents.catalog`，
本模块只负责把编号装配成可运行的子 Agent；C1–C3 走行业规则注入而非自由协作。

职责边界：
- 只做"编号 → 实例"的装配，不包含任何业务分析逻辑；
- 未知编号回落到 B3（主分析脑），保证编排链路不会因为缺一个 Agent 就断链；
- C1–C3 不实例化自由协作 Agent，而是包装成 IndustryRuleAgent 发射规则。
"""

from __future__ import annotations

# ---- 同模块：行业规则包（C1–C3 取规则用）----
from industrial_agent.agents import industry
# ---- 同模块：基类（类型标注用）----
from industrial_agent.agents.base import BaseAgent
# ---- 同模块：13 个具体智能体实现 ----
from industrial_agent.agents.specialists import (
    AssemblyEntryAgent,
    ControlCenterAgent,
    IndustryRuleAgent,
    LineDispatchAgent,
    LogisticsServiceAgent,
    MaterialDeliveryAgent,
    QualityTraceAgent,
    QualityTraceServiceAgent,
    StationAssemblyAgent,
    TrainingServiceAgent,
    TrainingTeachingAgent,
)
# ---- 横切：审计记录器（注入到每个 Agent 实例）----
from industrial_agent.audit.recorder import AuditRecorder

# catalog 编号 → 具体 Agent 类的映射（A1–A5 / B1–B5 共 10 个自由协作 Agent）
# C1–C3 不在此映射中，因为它们走行业规则注入，由下方 INDUSTRY_CODES 分支处理
CODE_AGENT_CLASS: dict[str, type[BaseAgent]] = {
    "A1": StationAssemblyAgent,      # 工位装配（第一版只读）
    "A2": MaterialDeliveryAgent,      # 物料配送
    "A3": QualityTraceAgent,          # 质量追溯
    "A4": LineDispatchAgent,          # 产线调度
    "A5": TrainingTeachingAgent,      # 实训教学
    "B1": AssemblyEntryAgent,         # 装配入口（第一版只读）
    "B2": LogisticsServiceAgent,      # 物流服务
    "B3": ControlCenterAgent,         # 智能管控（主分析脑，未知编号的回落目标）
    "B4": QualityTraceServiceAgent,   # 质量追溯服务
    "B5": TrainingServiceAgent,       # 实训服务
}

# C1–C3 行业专用编号集合：这些编号不走 CODE_AGENT_CLASS，而是取规则包
INDUSTRY_CODES = frozenset({"C1", "C2", "C3"})


def is_industry_code(code: str) -> bool:
    """判断编号是否为行业专用（C 类）。"""
    return code in INDUSTRY_CODES


def build_agent(code: str, audit: AuditRecorder | None = None) -> BaseAgent:
    """按 catalog 编号实例化子 Agent。

    装配优先级：
    1. C1–C3 → 取行业规则包，包装成 IndustryRuleAgent（只发射规则，不调工具）；
    2. A/B 类 → 查 CODE_AGENT_CLASS 实例化对应实现类；
    3. 未知编号 → 回落到 B3（ControlCenterAgent，主分析脑），避免编排断链。

    每个实例都会注入同一个审计记录器，保证全链路留痕一致。
    """
    # 分支 1：行业专用编号 → 规则注入
    if is_industry_code(code):
        pack = industry.rule_pack(code)
        if pack is not None:
            # IndustryRuleAgent 拿到规则包后，_analyze 只确定性发射校验项与确认边界
            return IndustryRuleAgent(pack, audit)
    # 分支 2：常规编号 → 查表实例化
    cls = CODE_AGENT_CLASS.get(code)
    # 分支 3：查不到 → 回落到主分析脑 B3，保证编排不断链
    if cls is None:
        cls = ControlCenterAgent
    return cls(audit)