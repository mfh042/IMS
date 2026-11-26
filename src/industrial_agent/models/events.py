from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

SourceSystem = Literal["WMS", "MES", "QMS", "EAM", "MANUAL"]
Severity = Literal["low", "medium", "high", "critical"]
DataQuality = Literal["complete", "partial", "suspicious"]


class StandardEvent(BaseModel):
    """阶段 1.4 标准事件结构。"""

    event_id: str
    trace_id: str
    idempotency_key: str
    event_type: str
    source_system: SourceSystem
    occurred_at: datetime
    received_at: datetime | None = None
    severity: Severity
    tenant_id: str
    factory_id: str | None = None
    line_id: str | None = None
    work_order_id: str | None = None
    material_id: str | None = None
    batch_id: str | None = None
    device_id: str | None = None
    alarm_code: str | None = None
    message: str | None = None
    payload: dict = Field(default_factory=dict)
    raw_payload: dict = Field(default_factory=dict)
    data_quality: DataQuality = "complete"
    schema_version: str = "v1"


IncidentType = Literal[
    "production_exception",
    "material_exception",
    "quality_exception",
    "equipment_exception",
    "mixed_exception",
]

IncidentStatus = Literal["new", "processing", "waiting_confirm", "closed", "failed"]


class Incident(BaseModel):
    """阶段 2.4 聚合后的问题实例。"""

    incident_id: str
    trace_id: str
    event_ids: list[str] = Field(default_factory=list)
    event_count: int = 0
    incident_type: IncidentType
    severity: Severity
    priority_score: float = 0.0
    first_occurred_at: datetime
    last_occurred_at: datetime
    entity_refs: dict = Field(default_factory=dict)
    status: IncidentStatus = "new"


class NormalizationOutcome(BaseModel):
    """标准化结果：成功事件或进入死信队列的原因。"""

    ok: bool
    event: StandardEvent | None = None
    dead_letter_reason: str | None = None
    error_code: str | None = None