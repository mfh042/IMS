"""全系统数据模型层的包入口。

把 5 个子模块（events/knowledge/findings/plan/execution）里的公共模型 re-export 到包顶层，
让外部（主图 / API / 测试代码）统一从这里导入，无需关心模型散落在哪个文件：

    from industrial_agent.models import Incident, StandardEvent, AgentFinding, ResolutionPlan

模型层只定义结构（Pydantic BaseModel + Literal 枚举），不含业务逻辑；
业务逻辑分布在 ingestion/agents/graph/governance/execution 等层。
"""

# 阶段 1-2：事件接入标准化与聚合
from industrial_agent.models.events import (
    Incident,              # 聚合后的问题实例
    IncidentStatus,        # 问题生命周期状态
    IncidentType,          # 问题类型（驱动子图/知识库/智能体路由）
    NormalizationOutcome,  # 标准化结果（成功事件 or 死信原因）
    Severity,              # 严重度枚举
    SourceSystem,          # 来源系统白名单枚举
    StandardEvent,         # 标准化后的统一事件结构
)
# 阶段 8-9：执行与审计
from industrial_agent.models.execution import (
    ActionExecution,   # 动作执行结果记录
    AuditRecord,       # 审计留痕记录
    ConfirmRequest,    # 人工确认请求
    ConfirmStatus,     # 确认状态枚举
    ExecutionStatus,   # 执行状态枚举
)
# 阶段 5：子智能体统一输出
from industrial_agent.models.findings import AgentFinding
# 阶段 3：知识分块（KnowledgeHit 留在 knowledge 模块内使用，包级只导出分块）
from industrial_agent.models.knowledge import KnowledgeChunk
# 阶段 7：规划与校验（RiskLevel 从 plan 导出，作为包级风险等级）
from industrial_agent.models.plan import ProposedAction, ResolutionPlan, RiskLevel

# 显式声明包的公开 API：import * 与外部依赖的稳定契约，新增模型须在此登记
__all__ = [
    "ActionExecution",
    "AgentFinding",
    "AuditRecord",
    "ConfirmRequest",
    "ConfirmStatus",
    "ExecutionStatus",
    "Incident",
    "IncidentStatus",
    "IncidentType",
    "KnowledgeChunk",
    "NormalizationOutcome",
    "ProposedAction",
    "ResolutionPlan",
    "RiskLevel",
    "Severity",
    "SourceSystem",
    "StandardEvent",
]