"""阶段 2：事件治理（去重、聚合、限流、优先级）。"""

from __future__ import annotations

import uuid
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from industrial_agent.models.events import Incident, Severity, StandardEvent

SEVERITY_WEIGHT = {"low": 1.0, "medium": 3.0, "high": 6.0, "critical": 10.0}
SEVERITY_RANK = {"low": 1, "medium": 2, "high": 3, "critical": 4}

INCIDENT_TYPE_BY_EVENT_PREFIX = {
    "mes": "production_exception",
    "wms": "material_exception",
    "qms": "quality_exception",
    "eam": "equipment_exception",
}

SAFETY_KEYWORDS = ("安全", "safety", "人身", "火灾", "泄漏", "stop line", "停线")
HIGH_IMPACT_KEYWORDS = ("停线", "批量", "整批", "全检", "召回")


@dataclass
class _Aggregate:
    trace_id: str
    event_ids: list[str] = field(default_factory=list)
    first_occurred_at: datetime | None = None
    last_occurred_at: datetime | None = None
    max_severity: Severity = "low"
    incident_type: str = "mixed_exception"
    entity_refs: dict = field(default_factory=dict)
    affected_lines: set[str] = field(default_factory=set)
    safety_related: bool = False
    high_impact: bool = False
    first_seen_at: datetime | None = None


class EventGovernance:
    """把标准事件流治理为 Incident 流。

    - 去重：同一 idempotency_key 在窗口内只保留首条，其余计数累加
    - 聚合：按聚合键合并为 incident
    - 限流：全局并发上限 + 单实体上限，超限进入等待队列
    - 优先级：严重度 + 影响范围 + 安全加权 + 等待时长
    """

    def __init__(
        self,
        *,
        dedup_window_seconds: int = 300,
        max_concurrent_incidents: int = 8,
        per_entity_incident_limit: int = 2,
        priority_wait_weight: float = 0.5,
    ) -> None:
        self._dedup_window = timedelta(seconds=dedup_window_seconds)
        self._max_concurrent = max_concurrent_incidents
        self._per_entity_limit = per_entity_incident_limit
        self._wait_weight = priority_wait_weight

        self._aggregates: dict[str, _Aggregate] = {}
        self._last_seen: dict[str, datetime] = {}
        self._dup_counter: dict[str, int] = defaultdict(int)
        self._open_incidents: dict[str, Incident] = {}
        self._entity_open: dict[str, set[str]] = defaultdict(set)

    # ---------- 去重 ----------

    def is_duplicate(self, event: StandardEvent) -> bool:
        key = event.idempotency_key
        now = event.received_at or event.occurred_at
        last = self._last_seen.get(key)
        if last is not None and now - last <= self._dedup_window:
            self._dup_counter[key] += 1
            return True
        self._last_seen[key] = now
        return False

    def duplicate_count(self, idempotency_key: str) -> int:
        return self._dup_counter.get(idempotency_key, 0)

    # ---------- 聚合 ----------

    @staticmethod
    def aggregate_key(event: StandardEvent) -> str:
        if event.device_id and event.alarm_code:
            return f"device:{event.device_id}:{event.alarm_code}"
        if event.work_order_id:
            return f"wo:{event.work_order_id}:{event.payload.get('process_id', '')}"
        if event.batch_id:
            return f"batch:{event.batch_id}:{event.alarm_code or event.event_type}"
        return f"misc:{event.idempotency_key}"

    def aggregate(self, event: StandardEvent) -> _Aggregate:
        key = self.aggregate_key(event)
        agg = self._aggregates.get(key)
        now = event.occurred_at

        if agg is None:
            agg = _Aggregate(
                trace_id=event.trace_id,
                first_occurred_at=now,
                last_occurred_at=now,
                max_severity=event.severity,
                incident_type=self._incident_type(event),
                first_seen_at=event.received_at or now,
            )
            self._aggregates[key] = agg

        agg.event_ids.append(event.event_id)
        agg.last_occurred_at = max(agg.last_occurred_at or now, now)
        agg.first_occurred_at = min(agg.first_occurred_at or now, now)
        if SEVERITY_RANK[event.severity] > SEVERITY_RANK[agg.max_severity]:
            agg.max_severity = event.severity
        if agg.incident_type != self._incident_type(event):
            agg.incident_type = "mixed_exception"

        refs = agg.entity_refs
        for name in ("device_id", "work_order_id", "material_id", "batch_id", "line_id", "alarm_code"):
            value = getattr(event, name, None)
            if value and name not in refs:
                refs[name] = value
        if event.line_id:
            agg.affected_lines.add(event.line_id)

        text = f"{event.message or ''} {event.event_type}".lower()
        if any(k in text for k in SAFETY_KEYWORDS):
            agg.safety_related = True
        if any(k in text for k in HIGH_IMPACT_KEYWORDS):
            agg.high_impact = True
        return agg

    @staticmethod
    def _incident_type(event: StandardEvent) -> str:
        prefix = event.event_type.split(".")[0].lower()
        return INCIDENT_TYPE_BY_EVENT_PREFIX.get(
            prefix, INCIDENT_TYPE_BY_EVENT_PREFIX.get(event.source_system.lower(), "mixed_exception")
        )

    # ---------- 限流 ----------

    def is_throttled(self, agg: _Aggregate) -> tuple[bool, str | None]:
        if len(self._open_incidents) >= self._max_concurrent:
            return True, "全局并发达到上限"
        anchor = (
            agg.entity_refs.get("device_id")
            or agg.entity_refs.get("work_order_id")
            or agg.entity_refs.get("batch_id")
        )
        if anchor and len(self._entity_open.get(anchor, set())) >= self._per_entity_limit:
            return True, f"实体 {anchor} 并发达到上限"
        return False, None

    # ---------- 优先级 ----------

    def priority_score(self, agg: _Aggregate, now: datetime) -> float:
        score = SEVERITY_WEIGHT[agg.max_severity]
        score += min(len(agg.event_ids), 5) * 0.5
        score += min(len(agg.affected_lines) - 1, 3) * 1.0 if agg.affected_lines else 0.0
        if agg.high_impact:
            score += 2.0
        if agg.safety_related:
            score += 3.0
        if agg.first_seen_at is not None:
            waited = max((now - agg.first_seen_at).total_seconds(), 0.0)
            score += min(waited / 60.0, 10.0) * self._wait_weight
        return round(score, 2)

    # ---------- 生成 Incident ----------

    def build_incident(self, agg: _Aggregate, *, now: datetime) -> Incident:
        anchor = (
            agg.entity_refs.get("device_id")
            or agg.entity_refs.get("work_order_id")
            or agg.entity_refs.get("batch_id")
            or "unknown"
        )
        incident = Incident(
            incident_id=f"inc-{uuid.uuid4().hex[:10]}",
            trace_id=agg.trace_id,
            event_ids=list(agg.event_ids),
            event_count=len(agg.event_ids),
            incident_type=agg.incident_type,  # type: ignore[arg-type]
            severity=agg.max_severity,
            priority_score=self.priority_score(agg, now),
            first_occurred_at=agg.first_occurred_at or now,
            last_occurred_at=agg.last_occurred_at or now,
            entity_refs=dict(agg.entity_refs),
            status="new",
        )
        self._open_incidents[incident.incident_id] = incident
        self._entity_open[anchor].add(incident.incident_id)
        return incident

    def close_incident(self, incident_id: str, status: str = "closed") -> Incident | None:
        incident = self._open_incidents.get(incident_id)
        if incident is None:
            return None
        incident.status = status  # type: ignore[assignment]
        for entity_set in self._entity_open.values():
            entity_set.discard(incident_id)
        return incident

    def open_incidents(self) -> list[Incident]:
        return list(self._open_incidents.values())

    def rank_open_incidents(self, *, now: datetime) -> list[Incident]:
        return sorted(
            self._open_incidents.values(),
            key=lambda inc: inc.priority_score,
            reverse=True,
        )

    def reset(self) -> None:
        self._aggregates.clear()
        self._last_seen.clear()
        self._dup_counter.clear()
        self._open_incidents.clear()
        self._entity_open.clear()