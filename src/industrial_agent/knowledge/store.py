"""阶段 3.3 知识元数据与检索策略。"""

from __future__ import annotations

import json
import re
from datetime import date
from pathlib import Path

from industrial_agent.models.knowledge import KnowledgeChunk, KnowledgeHit

_TOKEN = re.compile(r"[\w\u4e00-\u9fff]+")


def _tokens(text: str) -> set[str]:
    return {t.lower() for t in _TOKEN.findall(text or "") if len(t) > 1}


class KnowledgeStore:
    """加载知识分块：优先读取 knowledge_path 下的 JSON，缺失时使用内置种子知识。"""

    def __init__(self, chunks: list[KnowledgeChunk] | None = None, *, path: Path | None = None) -> None:
        self._chunks: list[KnowledgeChunk] = list(chunks or [])
        self._path = path
        if path is not None and path.exists():
            self._chunks.extend(self._load_json(path))

    def _load_json(self, path: Path) -> list[KnowledgeChunk]:
        if path.is_dir():
            items: list[KnowledgeChunk] = []
            for file in sorted(path.glob("*.json")):
                items.extend(self._load_json(file))
            return items
        raw = json.loads(path.read_text(encoding="utf-8"))
        rows = raw if isinstance(raw, list) else raw.get("chunks", [])
        return [KnowledgeChunk.model_validate(row) for row in rows]

    def add(self, chunk: KnowledgeChunk) -> None:
        self._chunks.append(chunk)

    def all_chunks(self) -> list[KnowledgeChunk]:
        return list(self._chunks)

    def retrieve(
        self,
        query: str,
        *,
        device_type: str | None = None,
        alarm_code: str | None = None,
        process: str | None = None,
        top_k: int = 5,
        today: date | None = None,
    ) -> list[KnowledgeHit]:
        today = today or date.today()
        query_tokens = _tokens(query)
        hits: list[KnowledgeHit] = []

        for chunk in self._chunks:
            reasons: list[str] = []
            score = 0.0

            if alarm_code and alarm_code in chunk.applicable_alarm_codes:
                score += 6.0
                reasons.append("报警代码命中")
            if device_type and device_type in chunk.applicable_device_types:
                score += 4.0
                reasons.append("设备类型匹配")
            if process and process in chunk.applicable_process:
                score += 2.0
                reasons.append("工序匹配")

            chunk_tokens = _tokens(f"{chunk.title} {chunk.section_path} {chunk.text}")
            overlap = query_tokens & chunk_tokens
            if overlap:
                score += min(len(overlap), 8) * 0.75
                reasons.append(f"关键词命中 {len(overlap)} 个")

            if score <= 0:
                continue

            expired = self._is_expired(chunk, today)
            if expired:
                score -= 2.0
                reasons.append("文档已过期，已降权")

            if chunk.risk_level == "high":
                score += 0.5
                reasons.append("高风险文档，权威级加权")

            hits.append(KnowledgeHit(chunk=chunk, score=round(score, 2), expired=expired, reasons=reasons))

        hits.sort(key=lambda h: h.score, reverse=True)
        return [h for h in hits[:top_k] if h.score > 0]

    @staticmethod
    def _is_expired(chunk: KnowledgeChunk, today: date) -> bool:
        if not chunk.expire_date:
            return False
        try:
            return date.fromisoformat(chunk.expire_date) < today
        except ValueError:
            return False


def build_seed_knowledge() -> list[KnowledgeChunk]:
    """内置示例知识，保证框架在无外部知识库时可跑通检索与引用链路。"""

    def chunk(
        idx: int,
        doc_id: str,
        title: str,
        section: str,
        device_types: list[str],
        alarm_codes: list[str],
        processes: list[str],
        text: str,
        risk: str = "medium",
        expire: str | None = None,
    ) -> KnowledgeChunk:
        return KnowledgeChunk(
            chunk_id=f"kc-{idx:03d}",
            doc_id=doc_id,
            title=title,
            section_path=section,
            version="v1.2",
            effective_date="2026-01-01",
            expire_date=expire,
            applicable_device_types=device_types,
            applicable_alarm_codes=alarm_codes,
            applicable_process=processes,
            risk_level=risk,  # type: ignore[arg-type]
            owner="设备工程部",
            text=text,
            source_uri=f"kb://{doc_id}#{section}",
        )

    return [
        chunk(
            1,
            "SOP-EQ-001",
            "主轴温度异常处置规程",
            "第3章 温升异常/3.1 排查步骤",
            ["CNC", "MACHINE_CENTER"],
            ["TEMP_HIGH", "SPINDLE_OVERHEAT"],
            ["OP-10", "OP-20"],
            "1) 确认冷却液流量与压力是否正常；2) 检查主轴冷却风扇与过滤网是否堵塞；"
            "3) 读取主轴负载趋势，判断是否超负荷加工；4) 若温升持续超过 75℃ 应立即降速并停机检查。",
            risk="high",
        ),
        chunk(
            2,
            "SOP-EQ-002",
            "振动超标诊断指引",
            "第2章 振动异常/2.2 常见原因",
            ["CNC", "MACHINE_CENTER", "PUMP"],
            ["VIBRATION_HIGH"],
            ["OP-10", "OP-20"],
            "振动超标常见原因：刀具磨损或动平衡失效、主轴轴承磨损、地脚螺栓松动、进给参数不合理。"
            "建议先检查刀具寿命与夹持状态，再检查轴承温度与噪声。",
        ),
        chunk(
            3,
            "SOP-QM-001",
            "批次不良追溯与冻结流程",
            "第4章 批次处置/4.1 冻结与隔离",
            [],
            ["QUALITY_DEFECT"],
            ["OP-30", "OP-40"],
            "发现批量不良时：1) 立即隔离当前批次并挂标识；2) 追溯同批次上下游工序记录；"
            "3) 由质量工程师确认冻结范围；4) 冻结需质量负责人审批，系统不得自动执行。",
            risk="high",
        ),
        chunk(
            4,
            "SOP-WM-001",
            "缺料与替代料处理规则",
            "第2章 物料短缺/2.3 替代料启用",
            [],
            ["MATERIAL_SHORTAGE", "STOCK_LOW"],
            ["OP-20", "OP-30"],
            "缺料处置：1) 核对库存可用量与在途量；2) 检查批次是否被质量冻结；"
            "3) 若存在已认证替代料，可由工艺员确认后切换；4) 替代料切换需工艺与质量双确认。",
        ),
        chunk(
            5,
            "SOP-EQ-003",
            "通讯中断排查清单",
            "第5章 通讯异常/5.1 排查顺序",
            ["PLC", "ROBOT"],
            ["COMM_LOST", "HEARTBEAT_LOST"],
            [],
            "通讯中断排查：1) 检查交换机端口与网线；2) 检查 PLC 心跳与 IP 配置；"
            "3) 检查网关日志；4) 恢复后需确认设备状态再恢复生产。",
            expire="2025-12-31",
        ),
    ]