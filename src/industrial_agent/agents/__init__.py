"""阶段 5：智能体设计与提示工程。

agents 包的入口文件：把分散在各子模块的公开类与函数 re-export 到包顶层，
让外部只需 `from industrial_agent.agents import build_agent, BaseAgent` 即可使用，
无需关心内部文件划分。同时保留旧版 AGENT_REGISTRY 兼容映射。
"""

# ---- 基类与上下文：AgentContext 是运行时共享工作台，BaseAgent 是所有智能体父类 ----
from industrial_agent.agents.base import AgentContext, BaseAgent
# ---- 工厂：catalog 编号 → Agent 实例的唯一装配入口 ----
from industrial_agent.agents.factory import CODE_AGENT_CLASS, build_agent
# ---- LLM：提示词构建 + 可选结构化输出客户端 ----
from industrial_agent.agents.llm import LLMClient, build_prompt
# ---- 13 类具体智能体实现 ----
from industrial_agent.agents.specialists import (
    AssemblyEntryAgent,
    ControlCenterAgent,
    ControlSuggestionAgent,
    EquipmentDiagnosisAgent,
    IndustryRuleAgent,
    KnowledgeRetrievalAgent,
    LineDispatchAgent,
    LogisticsServiceAgent,
    MaintenanceAdviceAgent,
    MaterialDeliveryAgent,
    MaterialInventoryAgent,
    ProductionExceptionAgent,
    QualityTraceAgent,
    QualityTraceServiceAgent,
    StationAssemblyAgent,
    TrainingServiceAgent,
    TrainingTeachingAgent,
)

# 旧版兼容映射：按 name（英文标识）→ Agent 类。
# 新代码应优先使用 catalog.py 的编号体系（A1/B3 等）+ factory.build_agent，
# 此映射仅为不破坏历史调用方而保留。
AGENT_REGISTRY = {
    "equipment_diagnosis": EquipmentDiagnosisAgent,
    "maintenance_advice": MaintenanceAdviceAgent,
    "knowledge_retrieval": KnowledgeRetrievalAgent,
    "production_exception": ProductionExceptionAgent,
    "material_inventory": MaterialInventoryAgent,
    "quality_trace": QualityTraceAgent,
    "control_suggestion": ControlSuggestionAgent,
}

# __all__ 显式声明公开 API，控制 `from industrial_agent.agents import *` 的导出范围
__all__ = [
    "AGENT_REGISTRY",
    "AgentContext",
    "AssemblyEntryAgent",
    "BaseAgent",
    "CODE_AGENT_CLASS",
    "ControlCenterAgent",
    "ControlSuggestionAgent",
    "EquipmentDiagnosisAgent",
    "IndustryRuleAgent",
    "KnowledgeRetrievalAgent",
    "LLMClient",
    "LineDispatchAgent",
    "LogisticsServiceAgent",
    "MaintenanceAdviceAgent",
    "MaterialDeliveryAgent",
    "MaterialInventoryAgent",
    "ProductionExceptionAgent",
    "QualityTraceAgent",
    "QualityTraceServiceAgent",
    "StationAssemblyAgent",
    "TrainingServiceAgent",
    "TrainingTeachingAgent",
    "build_agent",
    "build_prompt",
]