"""阶段 1：事件接入与标准化。

本文件是事件接入层的核心：EventNormalizer 把上游 WMS / MES / QMS / EAM / 人工填报的
异构原始报文，转换为系统内部统一的 StandardEvent，是整条流水线的"入口海关"。

设计原则：脏数据绝不流入下游。
- 关键字段（event_type / source_system / 时间）非法 → 进死信队列（ok=False），主图据此提前结束；
- 非关键字段（severity / 实体字段）缺失或非法 → 自动降级/默认值，并标记 data_quality，
  让下游能感知数据可信度，而不是直接拒收。

处理五步流水线（normalize 内部顺序）：
① 非空校验 → ② event_type/source_system 白名单校验 → ③ 时间统一 → ④ 标识生成 → ⑤ 质量标记。
原始报文通过 raw_payload 全量留存，不丢失任何信息，便于事后排查与重放。
"""

from __future__ import annotations

# ---- 标准库 ----
import uuid                       # 生成 trace_id / event_id / idempotency_key 缺省值
from datetime import UTC, datetime, timedelta  # 时间统一与偏移阈值计算
from typing import Any            # 原始报文是任意 JSON 值，用 Any 标注

# ---- 本项目：标准化的输入输出模型（定义在 models 层，本模块只负责转换）----
from industrial_agent.models.events import NormalizationOutcome, StandardEvent

# ==================== 模块级常量 ====================

# 来源系统白名单：上游 source_system 必须在此集合内（大小写不敏感，比较时 upper()），
# 不在此列的报文直接进死信，防止未知来源的数据污染下游
SOURCE_SYSTEMS = {"WMS", "MES", "QMS", "EAM", "MANUAL"}
# 严重度合法值：非法 severity 会被降级为 medium 而非拒收（非关键字段容错）
SEVERITIES = {"low", "medium", "high", "critical"}

# 关键字段清单：这些字段缺失会影响 data_quality 评级（但不一定拒收，除非是阻断路由的字段）
CRITICAL_FIELDS = ("event_type", "source_system", "occurred_at", "severity", "tenant_id")

# 实体字段清单：从 raw + payload 双来源提取，既用于构造 StandardEvent，也用于生成去重键
ENTITY_FIELDS = (
    "factory_id",
    "line_id",
    "work_order_id",
    "material_id",
    "batch_id",
    "device_id",
    "alarm_code",
)

# 未来时间容忍阈值：occurred_at 超过"接收时间 + 5 分钟"视为设备时钟漂移/误填，降级用接收时间
MAX_FUTURE_SKEW = timedelta(minutes=5)
# 历史时间偏移阈值：occurred_at 早于"接收时间 - 7 天"视为过期数据，降级用接收时间
MAX_HISTORY_SKEW = timedelta(days=7)


class EventNormalizer:
    """把 WMS / MES / QMS / EAM 异构报文转换为 StandardEvent。

    无状态、线程安全：构造器只存默认租户 ID，normalize 可被主图并发复用。
    处理顺序对应阶段 1.3：字段映射 -> 时间统一 -> 标识生成 -> 质量标记。
    """

    def __init__(self, tenant_id: str = "factory_a") -> None:
        """初始化归一化器。

        Args:
            tenant_id: 默认租户 ID，当报文未携带 tenant_id 时使用（多租户隔离用）。
        """
        self._tenant_id = tenant_id

    def normalize(
        self,
        raw: dict[str, Any],
        *,
        source_system: str | None = None,
        received_at: datetime | None = None,
    ) -> NormalizationOutcome:
        """把一条原始报文归一化为 StandardEvent（五步流水线）。

        Args:
            raw: 上游原始报文（dict）。约定可带顶层字段，也可把业务字段嵌套在 "payload" 子对象里。
            source_system: 调用方显式指定来源系统（优先级高于报文自带），如消息队列已标注来源时传入。
            received_at: 报文接收时间（缺省取当前 UTC）；用于时间偏移判定与缺省 occurred_at。

        Returns:
            NormalizationOutcome：
            - ok=True → event 为标准化后的 StandardEvent；
            - ok=False → event 为 None，dead_letter_reason / error_code 说明死信原因，
              主图节点 1 据此写审计并路由到 respond 提前结束。
        """
        # 接收时间基准：缺省取当前 UTC，所有时间判定以此为参照
        received_at = received_at or datetime.now(UTC)

        # ① 非空校验：空报文或非 dict → 死信，不让脏数据进入后续
        if not isinstance(raw, dict) or not raw:
            return NormalizationOutcome(
                ok=False,
                dead_letter_reason="报文为空或不是 JSON 对象",
                error_code="EMPTY_PAYLOAD",
            )

        # payload 子对象：兼容"业务字段嵌套在 payload 里"的上游格式；非 dict 则视为空
        payload = raw.get("payload") if isinstance(raw.get("payload"), dict) else {}

        # ② event_type 校验：缺失则无法路由（catalog 三级路由的第一级依据），直接死信
        event_type = raw.get("event_type") or payload.get("event_type")
        if not isinstance(event_type, str) or not event_type.strip():
            return NormalizationOutcome(
                ok=False,
                dead_letter_reason="缺少 event_type，无法路由",
                error_code="MISSING_EVENT_TYPE",
            )

        # source_system 白名单校验：显式传入 > 报文自带；不在 SOURCE_SYSTEMS → 死信
        resolved_source = source_system or raw.get("source_system")
        if not isinstance(resolved_source, str) or resolved_source.upper() not in SOURCE_SYSTEMS:
            return NormalizationOutcome(
                ok=False,
                dead_letter_reason=f"source_system 非法或缺失：{resolved_source!r}",
                error_code="INVALID_SOURCE_SYSTEM",
            )

        # ③ 时间统一：解析 occurred_at，处理时区/未来/偏移，返回 (时间, 问题说明)
        occurred_at, time_issue = self._parse_time(raw.get("occurred_at"), received_at)
        if occurred_at is None:
            # 完全无法解析为时间 → 死信（时间是治理聚合与优先级的关键基准，不能瞎猜）
            return NormalizationOutcome(
                ok=False,
                dead_letter_reason="occurred_at 无法解析为 ISO8601 时间",
                error_code="INVALID_TIMESTAMP",
            )

        # severity 容错：非法值降级 medium 而非拒收，并记录为时间问题之外的质量提示
        severity = raw.get("severity") or payload.get("severity") or "medium"
        severity = str(severity).lower()
        if severity not in SEVERITIES:
            severity = "medium"
            time_issue = time_issue or "severity 非法，已降级为 medium"

        # 关键字段缺失检查：同时看 raw 顶层和 payload 子对象（双来源）
        missing_critical = [
            name for name in CRITICAL_FIELDS if raw.get(name) in (None, "") and payload.get(name) in (None, "")
        ]

        # ④ 实体字段提取：从 raw 优先取，取不到再取 payload；收集进 entities 供构造事件 + 生成去重键
        entities: dict[str, Any] = {}
        for field in ENTITY_FIELDS:
            value = raw.get(field) if raw.get(field) is not None else payload.get(field)
            if value not in (None, ""):
                entities[field] = value

        # ⑤ 数据质量评级：时间问题（suspicious）优先级最高，其次是缺关键字段（partial），都无则 complete
        if missing_critical or time_issue:
            data_quality = "suspicious" if time_issue and "时间" in time_issue else "partial"
            if time_issue and missing_critical:
                data_quality = "suspicious"
        else:
            data_quality = "complete"

        # 标识生成：trace_id（全链路追踪）/ idempotency_key（去重键）/ event_id（事件唯一 ID）
        # 缺省值保证即便上游不传，下游治理/审计也有稳定标识可用
        trace_id = raw.get("trace_id") or f"trace-{uuid.uuid4().hex[:12]}"
        idempotency_key = raw.get("idempotency_key") or self._build_idempotency_key(
            resolved_source.upper(), event_type, entities
        )

        # 构造标准事件：同时保留 payload（业务载荷）与 raw_payload（完整原始报文），不丢信息
        event = StandardEvent(
            event_id=raw.get("event_id") or f"evt-{uuid.uuid4().hex[:16]}",
            trace_id=trace_id,
            idempotency_key=idempotency_key,
            event_type=event_type,
            source_system=resolved_source.upper(),  # 统一大写，便于下游匹配
            occurred_at=occurred_at,
            received_at=received_at,
            severity=severity,  # type: ignore[arg-type]  # 已校验在 SEVERITIES 内
            tenant_id=raw.get("tenant_id") or self._tenant_id,  # 报文优先，否则用默认租户
            factory_id=entities.get("factory_id"),
            line_id=entities.get("line_id"),
            work_order_id=entities.get("work_order_id"),
            material_id=entities.get("material_id"),
            batch_id=entities.get("batch_id"),
            device_id=entities.get("device_id"),
            alarm_code=entities.get("alarm_code"),
            message=raw.get("message") or payload.get("message"),
            payload=payload,
            raw_payload=raw,  # 全量留存原始报文，便于排查与重放
            data_quality=data_quality,  # type: ignore[arg-type]
            schema_version=raw.get("schema_version") or "v1",
        )
        return NormalizationOutcome(ok=True, event=event)

    def _parse_time(
        self, value: Any, received_at: datetime
    ) -> tuple[datetime | None, str | None]:
        """解析事件发生时间，统一为时区感知的 UTC，并做偏移校验。

        Args:
            value: 原始 occurred_at（可能是 str / datetime / None）。
            received_at: 接收时间基准，用于未来/历史偏移判定。

        Returns:
            (parsed_datetime, issue_note)：
            - parsed_datetime 为 None 表示完全无法解析 → 调用方进死信 INVALID_TIMESTAMP；
            - issue_note 为 None 表示时间正常；非 None 说明有降级处理（缺时区/未来/偏移过大），
              调用方据此把 data_quality 评为 suspicious。
            - 降级时 parsed_datetime 回退为 received_at（用接收时间兜底）。
        """
        if value in (None, ""):
            # 缺失 → 用接收时间兜底，并标记质量问题（下游会降低置信度）
            return received_at, "occurred_at 缺失，按接收时间处理"
        if isinstance(value, datetime):
            parsed = value
        else:
            try:
                # 兼容 ISO8601 末尾的 "Z"（Python fromisoformat 3.11+ 才原生支持 Z）
                parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
            except ValueError:
                # 完全无法解析 → 返回 None 让上层进死信（不瞎猜时间）
                return None, None
        # 无时区信息 → 按 UTC 处理（工业现场多为本地时间但未带时区，统一按 UTC 避免歧义）
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=UTC)
            note = "occurred_at 无时区，按 UTC 处理"
        else:
            note = None
        # 未来时间（设备时钟漂移/误填）→ 降级用接收时间
        if parsed > received_at + MAX_FUTURE_SKEW:
            return received_at, "occurred_at 为未来时间，按接收时间处理"
        # 历史偏移过大（过期数据）→ 降级用接收时间
        if parsed < received_at - MAX_HISTORY_SKEW:
            return received_at, "occurred_at 偏移过大，按接收时间处理"
        return parsed, note

    @staticmethod
    def _build_idempotency_key(
        source_system: str, event_type: str, entities: dict[str, Any]
    ) -> str:
        """生成缺省幂等键：同一物理故障的重复上报产生相同 key，供治理层去重。

        格式："{source_system}:{event_type}:{anchor}:{alarm_code}"。
        锚点优先级：work_order_id > batch_id > device_id > material_id > "unknown"，
        体现"工单/批次比设备更能标识一次业务事件"的业务语义；末尾空段用 rstrip(":") 清理。
        """
        anchor = (
            entities.get("work_order_id")
            or entities.get("batch_id")
            or entities.get("device_id")
            or entities.get("material_id")
            or "unknown"
        )
        code = entities.get("alarm_code") or ""
        return f"{source_system}:{event_type}:{anchor}:{code}".rstrip(":")