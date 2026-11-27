"""阶段 9.2 审计记录：内存 + JSONL 落盘，支持按 trace_id 回放。

本文件是审计与可观测层的核心：AuditRecorder 为主图全链路的每一个关键动作留痕。

存储策略（双重存储）：
- 内存 list[AuditRecord]：进程内快速查询，replay() 直接过滤；
- JSONL 落盘：持久化，按天切分 audit-YYYYMMDD.jsonl，重启后仍可追溯（跨重启回放
  当前需直接读 JSONL 文件，replay() 本身只读内存）。

设计约束：
- record() 是唯一写入入口，所有调用方共用同一个实例（由主图创建后注入）；
- 记录一旦生成不可变，audit_id 全局唯一，created_at 用 UTC；
- 不做模块级单例——测试时可传 audit_dir=None 得到纯内存实例，不污染磁盘。
"""

from __future__ import annotations

# ---- 标准库 ----
import json                  # dump_json 用：格式化导出
import uuid                  # 生成 audit_id（aud- + 前 12 位 hex）
from datetime import UTC, datetime  # created_at 用 UTC 时间，避免时区歧义
from pathlib import Path     # 审计目录与 JSONL 文件路径处理

# ---- 本项目：审计记录的数据模型（定义在 models 层，本模块只负责写入与查询）----
from industrial_agent.models.execution import AuditRecord


class AuditRecorder:
    """审计记录器：生成 AuditRecord 并写入内存 + 可选 JSONL 落盘。

    所有调用方（orchestrator / executor / tools / agents）共享同一个实例，
    通过 record() 落库，通过 replay() / records() / dump_json() 查询。
    """

    def __init__(self, audit_dir: Path | None = None, *, echo: bool = False) -> None:
        """初始化记录器。

        Args:
            audit_dir: 落盘目录。None 表示仅内存（测试/无状态场景）；
                       传目录则自动创建并按天写 JSONL。
            echo: 是否控制台回显（当前未实现，预留参数）。
        """
        self._records: list[AuditRecord] = []  # 内存缓存：插入顺序即时间顺序，replay 直接过滤
        self._dir = audit_dir                  # 落盘目录，None=不落盘
        self._echo = echo                      # 预留：控制台回显开关
        if audit_dir is not None:
            # parents=True 逐级创建父目录，exist_ok=True 已存在不报错
            audit_dir.mkdir(parents=True, exist_ok=True)

    def record(
        self,
        *,
        trace_id: str,
        actor: str,
        action: str,
        roles: list[str] | None = None,
        incident_id: str | None = None,
        target: str | None = None,
        input_snapshot: dict | None = None,
        output_snapshot: dict | None = None,
        risk_level: str | None = None,
        decision: str = "allowed",
        reason: str | None = None,
    ) -> AuditRecord:
        """记录一条审计事件（唯一写入入口）。

        所有参数均为关键字参数（* 之后），强制调用方显式传名，避免位置错位。
        自动填充 audit_id 与 created_at，调用方无需关心。

        Args:
            trace_id: 全链路关联键，一条事件的所有记录共享同一 trace_id（回放主键）。
            actor: 执行者（智能体节点名 / 用户标识，如 "governance"、"user:张三"）。
            action: 动作名（如 "normalize"、"risk_check"、"tool_call"、"confirmed"）。
            roles: 执行者角色列表（人工确认时校验权限，如 ["工艺主管"]）。
            incident_id: 关联的事件 ID（治理层生成，可为空）。
            target: 动作目标对象（如设备 DEVICE-A-001、批次 BATCH-xxx）。
            input_snapshot: 动作前状态快照（脱敏后，用于事后排查）。
            output_snapshot: 动作后状态快照。
            risk_level: 风险等级（high/medium/low），高风险必须有人工确认记录。
            decision: 决策结果，取值见 AuditDecision
                      （allowed/denied/confirmed/rejected/executed/rolled_back）。
            reason: 决策理由（规则命中 / LLM 结论 / 人工备注）。

        Returns:
            生成的 AuditRecord 实例（已写入内存并落盘）。
        """
        entry = AuditRecord(
            audit_id=f"aud-{uuid.uuid4().hex[:12]}",  # 全局唯一审计 ID：aud- + 12 位 hex
            trace_id=trace_id,
            incident_id=incident_id,
            actor=actor,
            roles=roles or [],                       # None → 空列表，统一形态
            action=action,
            target=target,
            input_snapshot=input_snapshot or {},     # None → 空 dict
            output_snapshot=output_snapshot or {},
            risk_level=risk_level,
            decision=decision,  # type: ignore[arg-type]  # 字面量 str → AuditDecision，运行时 Pydantic 校验
            reason=reason,
            created_at=datetime.now(UTC),            # UTC 时间，避免本地时区歧义
        )
        self._records.append(entry)      # 1. 写入内存（立即可查）
        self._append_jsonl(entry)        # 2. 落盘（若配置了目录）
        return entry

    def _append_jsonl(self, entry: AuditRecord) -> None:
        """把一条记录追加到当日 JSONL 文件（私有，仅 record() 调用）。

        文件按天切分：audit-YYYYMMDD.jsonl，便于按日期归档。
        每行一条完整 JSON（model_dump_json 已序列化），UTF-8 编码保留中文。
        """
        if self._dir is None:
            return                          # 未配置目录 → 纯内存模式，不落盘
        # 按 created_at 的日期切分文件名（UTC 日期，跨时区一致）
        path = self._dir / f"audit-{entry.created_at:%Y%m%d}.jsonl"
        # "a" 追加模式：多进程/多线程并发写时理论上存在行交错风险，
        # 当前主图为单进程顺序调用，单条 write 通常原子；高并发场景需加文件锁。
        with path.open("a", encoding="utf-8") as handle:
            handle.write(entry.model_dump_json() + "\n")

    def records(self) -> list[AuditRecord]:
        """返回全部内存记录的副本（浅拷贝，避免外部修改内部列表）。"""
        return list(self._records)

    def replay(self, trace_id: str) -> list[AuditRecord]:
        """按 trace_id 回放：返回该事件的全部审计记录（保持插入/时间顺序）。

        注意：仅查内存，进程重启后内存丢失；跨重启回放需直接读 audit_dir 下的 JSONL。
        """
        return [r for r in self._records if r.trace_id == trace_id]

    def dump_json(self, trace_id: str) -> str:
        """把某条 trace 的记录导出为格式化 JSON 字符串（保留中文，缩进 2）。

        用于导出审计报告或复制给评审；model_dump(mode="json") 保证 datetime 等
        非 JSON 原生类型被转为可序列化形态。
        """
        return json.dumps(
            [r.model_dump(mode="json") for r in self.replay(trace_id)],
            ensure_ascii=False,   # 不转义中文，可读性更好
            indent=2,             # 美化缩进
        )

    def clear(self) -> None:
        """清空内存记录（主要用于测试隔离；JSONL 落盘文件不受影响）。"""
        self._records.clear()