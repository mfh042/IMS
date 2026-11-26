"""阶段 1：事件接入与标准化。"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from industrial_agent.models.events import NormalizationOutcome, StandardEvent

SOURCE_SYSTEMS = {"WMS", "MES", "QMS", "EAM", "MANUAL"}
SEVERITIES = {"low", "medium", "high", "critical"}

CRITICAL_FIELDS = ("event_type", "source_system", "occurred_at", "severity", "tenant_id")

ENTITY_FIELDS = (
    "factory_id",
    "line_id",
    "work_order_id",
    "material_id",
    "batch_id",
    "device_id",
    "alarm_code",
)

MAX_FUTURE_SKEW = timedelta(minutes=5)
MAX_HISTORY_SKEW = timedelta(days=7)


class EventNormalizer:
    """把 WMS / MES / QMS / EAM 异构报文转换为 StandardEvent。

    处理顺序对应阶段 1.3：字段映射 -> 时间统一 -> 标识生成 -> 质量标记。
    """

    def __init__(self, tenant_id: str = "factory_a") -> None:
        self._tenant_id = tenant_id

    def normalize(
        self,
        raw: dict[str, Any],
        *,
        source_system: str | None = None,
        received_at: datetime | None = None,
    ) -> NormalizationOutcome:
        received_at = received_at or datetime.now(UTC)

        if not isinstance(raw, dict) or not raw:
            return NormalizationOutcome(
                ok=False,
                dead_letter_reason="报文为空或不是 JSON 对象",
                error_code="EMPTY_PAYLOAD",
            )

        payload = raw.get("payload") if isinstance(raw.get("payload"), dict) else {}

        event_type = raw.get("event_type") or payload.get("event_type")
        if not isinstance(event_type, str) or not event_type.strip():
            return NormalizationOutcome(
                ok=False,
                dead_letter_reason="缺少 event_type，无法路由",
                error_code="MISSING_EVENT_TYPE",
            )

        resolved_source = source_system or raw.get("source_system")
        if not isinstance(resolved_source, str) or resolved_source.upper() not in SOURCE_SYSTEMS:
            return NormalizationOutcome(
                ok=False,
                dead_letter_reason=f"source_system 非法或缺失：{resolved_source!r}",
                error_code="INVALID_SOURCE_SYSTEM",
            )

        occurred_at, time_issue = self._parse_time(raw.get("occurred_at"), received_at)
        if occurred_at is None:
            return NormalizationOutcome(
                ok=False,
                dead_letter_reason="occurred_at 无法解析为 ISO8601 时间",
                error_code="INVALID_TIMESTAMP",
            )

        severity = raw.get("severity") or payload.get("severity") or "medium"
        severity = str(severity).lower()
        if severity not in SEVERITIES:
            severity = "medium"
            time_issue = time_issue or "severity 非法，已降级为 medium"

        missing_critical = [
            name for name in CRITICAL_FIELDS if raw.get(name) in (None, "") and payload.get(name) in (None, "")
        ]

        entities: dict[str, Any] = {}
        for field in ENTITY_FIELDS:
            value = raw.get(field) if raw.get(field) is not None else payload.get(field)
            if value not in (None, ""):
                entities[field] = value

        if missing_critical or time_issue:
            data_quality = "suspicious" if time_issue and "时间" in time_issue else "partial"
            if time_issue and missing_critical:
                data_quality = "suspicious"
        else:
            data_quality = "complete"

        trace_id = raw.get("trace_id") or f"trace-{uuid.uuid4().hex[:12]}"
        idempotency_key = raw.get("idempotency_key") or self._build_idempotency_key(
            resolved_source.upper(), event_type, entities
        )

        event = StandardEvent(
            event_id=raw.get("event_id") or f"evt-{uuid.uuid4().hex[:16]}",
            trace_id=trace_id,
            idempotency_key=idempotency_key,
            event_type=event_type,
            source_system=resolved_source.upper(),
            occurred_at=occurred_at,
            received_at=received_at,
            severity=severity,
            tenant_id=raw.get("tenant_id") or self._tenant_id,
            factory_id=entities.get("factory_id"),
            line_id=entities.get("line_id"),
            work_order_id=entities.get("work_order_id"),
            material_id=entities.get("material_id"),
            batch_id=entities.get("batch_id"),
            device_id=entities.get("device_id"),
            alarm_code=entities.get("alarm_code"),
            message=raw.get("message") or payload.get("message"),
            payload=payload,
            raw_payload=raw,
            data_quality=data_quality,
            schema_version=raw.get("schema_version") or "v1",
        )
        return NormalizationOutcome(ok=True, event=event)

    def _parse_time(
        self, value: Any, received_at: datetime
    ) -> tuple[datetime | None, str | None]:
        if value in (None, ""):
            return received_at, "occurred_at 缺失，按接收时间处理"
        if isinstance(value, datetime):
            parsed = value
        else:
            try:
                parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
            except ValueError:
                return None, None
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=UTC)
            note = "occurred_at 无时区，按 UTC 处理"
        else:
            note = None
        if parsed > received_at + MAX_FUTURE_SKEW:
            return received_at, "occurred_at 为未来时间，按接收时间处理"
        if parsed < received_at - MAX_HISTORY_SKEW:
            return received_at, "occurred_at 偏移过大，按接收时间处理"
        return parsed, note

    @staticmethod
    def _build_idempotency_key(
        source_system: str, event_type: str, entities: dict[str, Any]
    ) -> str:
        anchor = (
            entities.get("work_order_id")
            or entities.get("batch_id")
            or entities.get("device_id")
            or entities.get("material_id")
            or "unknown"
        )
        code = entities.get("alarm_code") or ""
        return f"{source_system}:{event_type}:{anchor}:{code}".rstrip(":")