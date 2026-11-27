"""阶段 1（接入标准化）与阶段 2（聚合）的数据模型。

定义事件接入流水线中两个核心对象：
- StandardEvent：上游原始报文经 EventNormalizer 标准化后的统一事件结构；
- Incident：同源同实体的多条事件聚合后形成的问题实例（主图的处理单元）。
以及承载死信分流结果的 NormalizationOutcome，和若干用 Literal 锁住合法值域的枚举。

设计原则：脏数据在模型构造时即被 Pydantic 拒绝（非法枚举值），绝不流入下游；
原始报文全量留存于 raw_payload，便于排查与重放。
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

# 来源系统白名单：ingestion 层校验，非法值进死信（INVALID_SOURCE_SYSTEM）
SourceSystem = Literal["WMS", "MES", "QMS", "EAM", "MANUAL"]
# 严重度四档：驱动优先级评分与人工确认门槛
Severity = Literal["low", "medium", "high", "critical"]
# 数据质量三档：complete（无问题）/ partial（缺关键字段）/ suspicious（时间问题，优先级最高）
DataQuality = Literal["complete", "partial", "suspicious"]


class StandardEvent(BaseModel):
    """阶段 1.4 标准事件结构：接入层产出的统一事件，贯穿后续所有阶段的基础数据单元。"""

    event_id: str                # 事件唯一标识（接入层生成，幂等去重用）
    trace_id: str                # 链路追踪 id，串联同一事件在各阶段的处理记录
    idempotency_key: str         # 幂等键（去重键）：{source}:{type}:{anchor}:{alarm_code}
    event_type: str              # 事件类型（如 eam.device.temp），catalog 据此路由子图
    source_system: SourceSystem  # 来源系统（白名单枚举，构造时校验）
    occurred_at: datetime        # 事件发生时间（标准化后带时区，无时区补 UTC）
    received_at: datetime | None = None  # 事件被系统接收的时间（用于时间偏移判定）
    severity: Severity           # 严重度（非法值在标准化时降级为 medium）
    tenant_id: str               # 租户 id（多租户隔离）
    factory_id: str | None = None  # 工厂 id
    line_id: str | None = None     # 产线 id
    work_order_id: str | None = None  # 工单 id（去重锚点优先级最高）
    material_id: str | None = None    # 物料 id
    batch_id: str | None = None       # 批次 id
    device_id: str | None = None      # 设备 id
    alarm_code: str | None = None     # 报警码（知识检索最强信号）
    message: str | None = None        # 事件描述文本
    payload: dict = Field(default_factory=dict)      # 标准化提取的结构化字段
    raw_payload: dict = Field(default_factory=dict)  # 上游原始报文全量留存（不丢失任何信息）
    data_quality: DataQuality = "complete"  # 数据质量评级（complete/partial/suspicious）
    schema_version: str = "v1"  # 模型版本号（破坏性变更时升版，ingestion 做兼容转换）


# 问题类型：catalog 据此决定子图归属、应检索的知识库、应调度的子智能体
IncidentType = Literal[
    "production_exception",  # 生产异常
    "material_exception",    # 物料异常
    "quality_exception",     # 质量异常
    "equipment_exception",   # 设备异常
    "mixed_exception",       # 混合异常（跨多类）
    "query",                 # 纯查询（无异常处理）
    "standalone",            # 独立请求（单智能体模拟旁路）
]

# 问题生命周期状态：主图节点据此流转
IncidentStatus = Literal["new", "processing", "waiting_confirm", "closed", "failed"]


class Incident(BaseModel):
    """阶段 2.4 聚合后的问题实例：同源同实体的多条事件聚合为一个问题，是主图的处理单元。"""

    incident_id: str               # 问题唯一标识
    trace_id: str                  # 链路追踪 id
    event_ids: list[str] = Field(default_factory=list)  # 聚合进来的事件 id 列表
    event_count: int = 0           # 聚合的事件数量（用于优先级与爆发判断）
    incident_type: IncidentType    # 问题类型（驱动路由）
    severity: Severity             # 严重度（取聚合事件的最高级）
    priority_score: float = 0.0    # 优先级评分（治理层据此排序调度）
    first_occurred_at: datetime    # 首次发生时间
    last_occurred_at: datetime     # 末次发生时间（用于持续时长判断）
    entity_refs: dict = Field(default_factory=dict)  # 实体引用字典（驱动知识检索与工具调用）
    status: IncidentStatus = "new"  # 生命周期状态


class NormalizationOutcome(BaseModel):
    """标准化结果：成功事件或进入死信队列的原因（分流判断的统一载体）。"""

    ok: bool                          # True=标准化成功；False=进死信
    event: StandardEvent | None = None  # ok=True 时携带标准化事件
    dead_letter_reason: str | None = None  # ok=False 时的死信原因（人类可读）
    error_code: str | None = None     # 死信错误码：EMPTY_PAYLOAD / MISSING_EVENT_TYPE /
                                      # INVALID_SOURCE_SYSTEM / INVALID_TIMESTAMP