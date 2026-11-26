"""阶段 5：智能体设计与提示工程。"""

from industrial_agent.agents.base import AgentContext, BaseAgent
from industrial_agent.agents.llm import LLMClient, build_prompt
from industrial_agent.agents.specialists import (
    ControlSuggestionAgent,
    EquipmentDiagnosisAgent,
    KnowledgeRetrievalAgent,
    MaintenanceAdviceAgent,
    MaterialInventoryAgent,
    ProductionExceptionAgent,
    QualityTraceAgent,
)

AGENT_REGISTRY = {
    "equipment_diagnosis": EquipmentDiagnosisAgent,
    "maintenance_advice": MaintenanceAdviceAgent,
    "knowledge_retrieval": KnowledgeRetrievalAgent,
    "production_exception": ProductionExceptionAgent,
    "material_inventory": MaterialInventoryAgent,
    "quality_trace": QualityTraceAgent,
    "control_suggestion": ControlSuggestionAgent,
}

__all__ = [
    "AGENT_REGISTRY",
    "AgentContext",
    "BaseAgent",
    "ControlSuggestionAgent",
    "EquipmentDiagnosisAgent",
    "KnowledgeRetrievalAgent",
    "LLMClient",
    "MaintenanceAdviceAgent",
    "MaterialInventoryAgent",
    "ProductionExceptionAgent",
    "QualityTraceAgent",
    "build_prompt",
]