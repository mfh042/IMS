"""阶段 5：智能体设计与提示工程。

agents 包的入口文件：把分散在各子模块的公开类与函数 re-export 到包顶层，
让外部只需 `from industrial_agent.agents import build_agent, BaseAgent` 即可使用，
无需关心内部文件划分。

主图编排统一使用 catalog.py 的编号体系（A1–A5 / B1–B5 / C1–C3）+ factory.build_agent；
早期按 name（equipment_diagnosis 等）调度的遗留类已移除，不再保留兼容映射。
"""

# ---- 基类与上下文：AgentContext 是运行时共享工作台，BaseAgent 是所有智能体父类 ----
from industrial_agent.agents.base import AgentContext, BaseAgent
# ---- 工厂：catalog 编号 → Agent 实例的唯一装配入口 ----
from industrial_agent.agents.factory import CODE_AGENT_CLASS, build_agent
# ---- LLM：提示词构建 + 可选结构化输出客户端 ----
from industrial_agent.agents.llm import LLMClient, build_prompt
# ---- 13 类具体智能体实现（A1–A5 / B1–B5 / C1–C3，全部接入主图编号体系）----
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

# __all__ 显式声明公开 API，控制 `from industrial_agent.agents import *` 的导出范围
__all__ = [
    "AgentContext",
    "AssemblyEntryAgent",
    "BaseAgent",
    "CODE_AGENT_CLASS",
    "ControlCenterAgent",
    "IndustryRuleAgent",
    "LLMClient",
    "LineDispatchAgent",
    "LogisticsServiceAgent",
    "MaterialDeliveryAgent",
    "QualityTraceAgent",
    "QualityTraceServiceAgent",
    "StationAssemblyAgent",
    "TrainingServiceAgent",
    "TrainingTeachingAgent",
    "build_agent",
    "build_prompt",
]