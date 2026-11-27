"""阶段 2：事件治理（去重、聚合、限流、优先级）。

本文件是事件治理层的核心：EventGovernance 把上游涌入的标准事件流（StandardEvent）
治理为有序、可调度的问题实例流（Incident），解决工业现场"事件风暴"——
同一故障会从 EAM/MES/QMS 多系统反复推送、多设备同时报警，不治理会淹没下游智能体。

治理四件套（主图节点 2 依次调用）：
1. 去重 is_duplicate：同一 idempotency_key 在窗口内只保留首条，其余计数累加；
2. 聚合 aggregate：按聚合键（device/wo/batch）把相关事件合并为一个累加器 _Aggregate；
3. 限流 is_throttled：全局并发上限 + 单实体上限，超限等待不调度；
4. 优先级 priority_score + build_incident：综合评分后生成 Incident，登记开放列表。

数据来源：StandardEvent 来自归一化层（models/events.py），治理只读取不修改；
产出：Incident 交给下游 classify 节点路由子图。治理状态全部存内存，进程重启丢失。
"""

from __future__ import annotations

# ---- 标准库 ----
import uuid                       # 生成 incident_id（inc- + 10 位 hex）
from collections import defaultdict  # _entity_open / _dup_counter 的默认值容器
from dataclasses import dataclass, field  # _Aggregate 用 dataclass（可变累加器，轻量）
from datetime import datetime, timedelta  # 去重窗口、发生时间、等待时长计算

# ---- 本项目：事件数据模型（治理的输入 StandardEvent 与输出 Incident 都在这里定义）----
from industrial_agent.models.events import Incident, Severity, StandardEvent

# ==================== 模块级常量 ====================

# 严重度 → 优先级权重：critical 最高（10），用于 priority_score 的基础分
# 差距设计：critical(10) vs high(6) 差 4，high vs medium(3) 差 3，保证严重度梯度明显
SEVERITY_WEIGHT = {"low": 1.0, "medium": 3.0, "high": 6.0, "critical": 10.0}
# 严重度 → 排序序号：聚合时用 max 取最高严重度（rank 越大越严重）
SEVERITY_RANK = {"low": 1, "medium": 2, "high": 3, "critical": 4}

# event_type 前缀 → incident_type 路由映射（catalog 三级路由的第一级依据）
# 例：eam.device.temp_high 前缀 "eam" → equipment_exception
# query/esop/manual → query 子图（只读问答链路）；training/edu → standalone（独立链路）
INCIDENT_TYPE_BY_EVENT_PREFIX = {
    "mes": "production_exception",
    "wms": "material_exception",
    "qms": "quality_exception",
    "eam": "equipment_exception",
    # 阶段 6.8：工艺问答/装配履历走 query 子图，实训任务走独立链路
    "query": "query",
    "esop": "query",
    "manual": "query",
    "training": "standalone",
    "edu": "standalone",
}

# 安全关键词：命中 message/event_type 任一即标记 safety_related，优先级 +3（最高加权项）
# 体现工业场景"人身安全优先于产能"的原则
SAFETY_KEYWORDS = ("安全", "safety", "人身", "火灾", "泄漏", "stop line", "停线")
# 高影响关键词：命中即标记 high_impact，优先级 +2（影响范围广）
HIGH_IMPACT_KEYWORDS = ("停线", "批量", "整批", "全检", "召回")


@dataclass
class _Aggregate:
    """聚合过程中的内部累加器（不对外暴露）。

    多条语义相关的 StandardEvent 经 aggregate() 合并进同一个 _Aggregate，
    聚合完成后由 build_incident() 转成对外的 Incident。
    下划线前缀表示私有，外部（主图）不应直接构造或访问。
    """

    trace_id: str                              # 全链路追踪 ID，取首条事件的 trace_id
    event_ids: list[str] = field(default_factory=list)  # 聚合进来的所有事件 ID（去重前的原事件）
    first_occurred_at: datetime | None = None  # 最早发生时间（用于 Incident.first_occurred_at）
    last_occurred_at: datetime | None = None   # 最晚发生时间（用于判断是否已平息/自动关闭）
    max_severity: Severity = "low"             # 聚合内最高严重度（取 rank 最大者）
    incident_type: str = "mixed_exception"     # 聚合后的事件类型；类型不一致时降级为 mixed
    entity_refs: dict = field(default_factory=dict)    # 去重后的实体引用（device/wo/batch 等）
    affected_lines: set[str] = field(default_factory=set)  # 受影响产线集合（优先级因子之一）
    safety_related: bool = False               # 是否涉及安全（命中 SAFETY_KEYWORDS）
    high_impact: bool = False                  # 是否高影响（命中 HIGH_IMPACT_KEYWORDS）
    first_seen_at: datetime | None = None      # 首次被治理层接收时间（优先级等待时长的起点）


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
        """初始化治理器，参数全部可配置（主图从 settings 注入，测试可自定义）。

        Args:
            dedup_window_seconds: 去重窗口（秒），同 idempotency_key 在此窗口内重复则丢弃。
            max_concurrent_incidents: 全局未关闭 Incident 并发上限，超限的新 Incident 等待。
            per_entity_incident_limit: 单实体（同一 device/wo/batch）未关闭 Incident 上限，
                                       防止同一设备的多个故障同时打满下游智能体。
            priority_wait_weight: 优先级中等待时长的权重，防饥饿（每分钟 +0.5×该值）。
        """
        self._dedup_window = timedelta(seconds=dedup_window_seconds)  # 去重窗口（时间差对象）
        self._max_concurrent = max_concurrent_incidents              # 全局并发上限
        self._per_entity_limit = per_entity_incident_limit           # 单实体并发上限
        self._wait_weight = priority_wait_weight                     # 等待时长权重

        # ---- 治理状态（全部内存，进程重启丢失）----
        self._aggregates: dict[str, _Aggregate] = {}          # 聚合键 → 累加器（尚未生成 Incident 的）
        self._last_seen: dict[str, datetime] = {}             # idempotency_key → 上次接收时间（去重用）
        self._dup_counter: dict[str, int] = defaultdict(int)  # idempotency_key → 累计被丢弃的重复次数
        self._open_incidents: dict[str, Incident] = {}        # incident_id → 未关闭的 Incident（限流统计 + 查询）
        self._entity_open: dict[str, set[str]] = defaultdict(set)  # 实体 → 其未关闭 incident_id 集合（单实体限流用）

    # ---------- 去重 ----------

    def is_duplicate(self, event: StandardEvent) -> bool:
        """判断事件是否为去重窗口内的重复事件。

        以 idempotency_key 为去重键（由归一化层从上游报文稳定生成）：
        - 窗口内见过 → 累加 dup_counter 并返回 True（调用方据此丢弃，不进下游）；
        - 没见过 / 已超出窗口 → 更新 last_seen 为当前时间，返回 False（放行）。
        时间基准优先用 received_at（治理接收时间），其次 occurred_at。
        """
        key = event.idempotency_key
        now = event.received_at or event.occurred_at
        last = self._last_seen.get(key)
        if last is not None and now - last <= self._dedup_window:
            self._dup_counter[key] += 1
            return True
        self._last_seen[key] = now
        return False

    def duplicate_count(self, idempotency_key: str) -> int:
        """查询某个 idempotency_key 累计被去重丢弃的次数（用于审计/最终结论提示）。"""
        return self._dup_counter.get(idempotency_key, 0)

    # ---------- 聚合 ----------

    @staticmethod
    def aggregate_key(event: StandardEvent) -> str:
        """生成聚合键：把语义相关的事件归到一起。

        优先级（从最精确到兜底）：
        1. device_id + alarm_code：同设备同报警（最常见的设备故障聚合）；
        2. work_order_id + process_id：同工单同工序（生产异常聚合）；
        3. batch_id + alarm_code/event_type：同批次（质量/物料异常聚合）；
        4. misc:idempotency_key：无明确实体时兜底，单事件独立成 Incident。
        选择逻辑体现"同一物理故障应聚到一起"，避免下游重复取证。
        """
        if event.device_id and event.alarm_code:
            return f"device:{event.device_id}:{event.alarm_code}"
        if event.work_order_id:
            return f"wo:{event.work_order_id}:{event.payload.get('process_id', '')}"
        if event.batch_id:
            return f"batch:{event.batch_id}:{event.alarm_code or event.event_type}"
        return f"misc:{event.idempotency_key}"

    def aggregate(self, event: StandardEvent) -> _Aggregate:
        """把一条事件聚合进对应累加器（不存在则新建），返回更新后的 _Aggregate。

        更新项：event_ids 累加、首末发生时间取 min/max、严重度取 max、
        实体引用合并去重、受影响产线并集、安全/高影响关键词检测、incident_type 一致性校验。
        若聚合内出现不同 incident_type，降级为 mixed_exception（触发最完整取证链路）。
        """
        key = self.aggregate_key(event)
        agg = self._aggregates.get(key)
        now = event.occurred_at

        if agg is None:
            # 首次见到该聚合键：新建累加器，incident_type 从事件推导
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
        # 严重度取最高（rank 越大越严重）
        if SEVERITY_RANK[event.severity] > SEVERITY_RANK[agg.max_severity]:
            agg.max_severity = event.severity
        # 同一聚合内事件类型不一致 → 降级为 mixed（下游走最完整链路）
        if agg.incident_type != self._incident_type(event):
            agg.incident_type = "mixed_exception"

        # 合并实体引用：只在该字段首次出现时记录（保留最先报告的值，避免覆盖）
        refs = agg.entity_refs
        for name in ("device_id", "work_order_id", "material_id", "batch_id", "line_id", "alarm_code"):
            value = getattr(event, name, None)
            if value and name not in refs:
                refs[name] = value
        if event.line_id:
            agg.affected_lines.add(event.line_id)

        # 关键词检测：在 message + event_type 的小写文本里匹配
        text = f"{event.message or ''} {event.event_type}".lower()
        if any(k in text for k in SAFETY_KEYWORDS):
            agg.safety_related = True
        if any(k in text for k in HIGH_IMPACT_KEYWORDS):
            agg.high_impact = True
        return agg

    @staticmethod
    def _incident_type(event: StandardEvent) -> str:
        """从事件推导 incident_type（catalog 子图路由的第一级依据）。

        取 event_type 的第一个 "." 前缀（如 eam.device.x → "eam"）查映射表；
        前缀无法识别时回落到 source_system（如 MANUAL → query）；
        都无法识别兜底 mixed_exception。
        """
        prefix = event.event_type.split(".")[0].lower()
        return INCIDENT_TYPE_BY_EVENT_PREFIX.get(
            prefix, INCIDENT_TYPE_BY_EVENT_PREFIX.get(event.source_system.lower(), "mixed_exception")
        )

    # ---------- 限流 ----------

    def is_throttled(self, agg: _Aggregate) -> tuple[bool, str | None]:
        """判断聚合是否应被限流（暂缓调度）。返回 (是否限流, 原因)。

        两道闸，任一触发即限流：
        1. 全局：未关闭 Incident 数 ≥ max_concurrent → 防止整体过载；
        2. 单实体：该聚合锚点实体（device/wo/batch）的未关闭 Incident 数 ≥ per_entity_limit
           → 防止同一设备的多故障打满下游智能体，让其他实体的事件也能被处理。
        """
        if len(self._open_incidents) >= self._max_concurrent:
            return True, "全局并发达到上限"
        # 锚点实体：device 优先，其次 wo，其次 batch（与 build_incident 的 anchor 一致）
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
        """计算聚合的优先级评分（越高越优先调度），保留 2 位小数。

        评分因子（可比分值，全部换算为同一量纲）：
        - 严重度权重（基础分，critical=10）；
        - 事件数：每多 1 条 +0.5，封顶 5 条（反映故障持续/反复）；
        - 受影响产线数：每多 1 条 +1，封顶 3（反映影响范围广度）；
        - 高影响：+2（停线/批量/召回）；
        - 安全：+3（人身/火灾/泄漏，最高单项加权，体现安全优先）；
        - 等待时长：每分钟 +wait_weight，封顶 10 分钟（防止低优事件饥饿）。
        """
        score = SEVERITY_WEIGHT[agg.max_severity]
        score += min(len(agg.event_ids), 5) * 0.5
        # affected_lines 非空才计分，且首条产线不计分（从 0 开始，-1 后封顶 3）
        score += min(len(agg.affected_lines) - 1, 3) * 1.0 if agg.affected_lines else 0.0
        if agg.high_impact:
            score += 2.0
        if agg.safety_related:
            score += 3.0
        # 等待时长：从首次被治理层接收算起，每分钟 +wait_weight，封顶 10 分钟
        if agg.first_seen_at is not None:
            waited = max((now - agg.first_seen_at).total_seconds(), 0.0)
            score += min(waited / 60.0, 10.0) * self._wait_weight
        return round(score, 2)

    # ---------- 生成 Incident ----------

    def build_incident(self, agg: _Aggregate, *, now: datetime) -> Incident:
        """把聚合累加器转为对外的 Incident，登记进开放列表（用于限流统计与查询）。

        生成 incident_id（inc- + 10 位 hex），计算 priority_score，status 置 new。
        同时把 incident_id 登记到锚点实体的 _entity_open 集合，单实体限流据此统计。
        """
        # 锚点实体：与 is_throttled 一致，device 优先
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
            entity_refs=dict(agg.entity_refs),  # 拷贝，避免外部修改影响内部累加器
            status="new",
        )
        self._open_incidents[incident.incident_id] = incident
        self._entity_open[anchor].add(incident.incident_id)
        return incident

    def close_incident(self, incident_id: str, status: str = "closed") -> Incident | None:
        """关闭一个 Incident：更新状态并从限流统计中移除（释放并发额度）。

        主图在事件处理完成（或失败）后调用。关闭后该 Incident 不再计入
        _open_incidents 与各实体的 _entity_open，为新事件腾出限流额度。
        不存在则返回 None。
        """
        incident = self._open_incidents.get(incident_id)
        if incident is None:
            return None
        incident.status = status  # type: ignore[assignment]
        # 从所有实体的开放集合中移除（一个 incident 可能关联多个实体引用）
        for entity_set in self._entity_open.values():
            entity_set.discard(incident_id)
        return incident

    def open_incidents(self) -> list[Incident]:
        """返回所有未关闭的 Incident（供前端 /api/incidents/open 查询，返回副本避免外部修改）。"""
        return list(self._open_incidents.values())

    def rank_open_incidents(self, *, now: datetime) -> list[Incident]:
        """按 priority_score 降序返回未关闭 Incident（调度器从队首取，决定先处理谁）。

        注意：这里直接读 Incident 上已存的 priority_score（build_incident 时算的快照），
        不会随等待时长动态重算；若需动态老化，调用方可重新计算后排序。
        """
        return sorted(
            self._open_incidents.values(),
            key=lambda inc: inc.priority_score,
            reverse=True,
        )

    def reset(self) -> None:
        """清空全部治理状态（主要用于测试隔离；生产环境不应调用）。"""
        self._aggregates.clear()
        self._last_seen.clear()
        self._dup_counter.clear()
        self._open_incidents.clear()
        self._entity_open.clear()