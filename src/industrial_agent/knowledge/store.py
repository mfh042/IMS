"""阶段 3.3 知识元数据与检索策略（阶段 3.7 增加四类知识库归属与必需元数据校验）。

本文件是知识底座与检索层的核心：KnowledgeStore 把知识分块（KnowledgeChunk）加载进内存，
按事件携带的实体维度（设备类型/报警码/工序/产品码）+ 关键词重叠做加权打分检索，
并校验必需元数据维度与文档时效性。

安全底线（阶段 3.5）：
- 元数据缺失的命中不丢弃，而是写入 missing_filters；
- KnowledgeHit.valid_for_auto_action = (未过期 且 无缺失元数据)；
- 因此"过期或缺元数据的知识只能参考，不能支撑自动动作"——下游规划/执行据此控制写动作。

检索是确定性的关键词/元数据加权打分（不依赖外部向量模型）：零依赖、可复现、可审计。
"""

from __future__ import annotations

# ---- 标准库 ----
import json                       # 加载 JSON 知识分块文件
import re                         # 中英文分词正则
from datetime import date         # 过期判定基准
from pathlib import Path          # 知识文件/目录路径处理
from typing import Iterable       # 检索参数的可迭代类型标注

# ---- 本项目：知识数据模型（定义在 models 层，本模块只负责加载与检索）----
from industrial_agent.models.knowledge import KnowledgeBase, KnowledgeChunk, KnowledgeHit

# 分词正则：\w 匹配英文/数字/下划线，\u4e00-\u9fff 匹配中文汉字，+ 表示一个或多个
# 用于把 query 与 chunk 文本切成 token 集合后求交集（关键词命中打分的依据）
_TOKEN = re.compile(r"[\w\u4e00-\u9fff]+")

# 检索维度 → KnowledgeChunk 对应字段名的映射（阶段 3.7 元数据维度定义）
# 目前主要用于文档与检索的一致性对照；具体命中逻辑在 retrieve 里直接按字段判断
METADATA_FIELDS: dict[str, str] = {
    "process": "applicable_process",
    "version": "version",
    "alarm_code": "applicable_alarm_codes",
    "device_type": "applicable_device_types",
    "product_code": "applicable_product_codes",
}


def _tokens(text: str) -> set[str]:
    """把文本切成小写 token 集合（过滤长度 ≤ 1 的碎片，如单字/单字母）。"""
    return {t.lower() for t in _TOKEN.findall(text or "") if len(t) > 1}


class KnowledgeStore:
    """知识分块的内存存储与检索器。

    加载方式（构造时一次性加载，之后只读检索）：
    - chunks：调用方显式传入的分块列表（如 build_seed_knowledge() 的内置种子）；
    - path：JSON 文件或目录；目录会递归加载所有 *.json，文件加载单个。
      JSON 兼容两种格式：[...] 列表 或 {"chunks": [...]} 包裹。
    两者可叠加（先 chunks，再 extend path 加载的内容）。不传任何参数则为空库，
    调用方需自行注入分块或指向知识目录。
    """

    def __init__(self, chunks: list[KnowledgeChunk] | None = None, *, path: Path | None = None) -> None:
        self._chunks: list[KnowledgeChunk] = list(chunks or [])  # 内存分块列表（检索的数据源）
        self._path = path                                          # 记录加载路径（调试/溯源用）
        if path is not None and path.exists():
            self._chunks.extend(self._load_json(path))  # 路径存在才加载，避免启动因缺目录报错

    def _load_json(self, path: Path) -> list[KnowledgeChunk]:
        """加载 JSON 知识分块（递归支持目录，兼容列表/包裹两种格式）。

        - 目录：sorted 后逐个 glob *.json 递归加载（排序保证加载顺序确定，可复现）；
        - 文件：读 UTF-8 JSON，取列表或 raw["chunks"]，逐行用 KnowledgeChunk.model_validate 校验。
        """
        if path.is_dir():
            items: list[KnowledgeChunk] = []
            for file in sorted(path.glob("*.json")):
                items.extend(self._load_json(file))
            return items
        raw = json.loads(path.read_text(encoding="utf-8"))
        # 兼容顶层是 list，或 {"chunks": [...]} 包裹两种形态
        rows = raw if isinstance(raw, list) else raw.get("chunks", [])
        return [KnowledgeChunk.model_validate(row) for row in rows]

    def add(self, chunk: KnowledgeChunk) -> None:
        """追加一条分块（运行时增量注入；按 chunk_id 去重需调用方自行保证）。"""
        self._chunks.append(chunk)

    def all_chunks(self) -> list[KnowledgeChunk]:
        """返回全部分块的副本（浅拷贝，避免外部修改内部列表）。"""
        return list(self._chunks)

    def retrieve(
        self,
        query: str,
        *,
        device_type: str | None = None,
        alarm_code: str | None = None,
        process: str | None = None,
        product_code: str | None = None,
        knowledge_bases: Iterable[KnowledgeBase] | None = None,
        required_metadata: Iterable[str] | None = None,
        top_k: int = 5,
        today: date | None = None,
    ) -> list[KnowledgeHit]:
        """阶段 3.7：按子图限定知识库归属，并校验必需元数据维度。

        检索流程：遍历所有分块 → 知识库白名单过滤 → 多维度加权打分 →
        过期降权 → 高风险权威加权 → 必需元数据缺失标记 → 按分降序取 top_k。

        元数据缺失的命中不会直接丢弃，而是写入 ``missing_filters``：
        依据阶段 3.5，此类知识只允许作为参考，不允许支撑自动动作
        （见 ``KnowledgeHit.valid_for_auto_action``）。

        Args:
            query: 检索文本（报警码/物料/批次或 incident_type），用于关键词重叠打分。
            device_type/alarm_code/process/product_code: 四维实体过滤（命中加分，不命中不扣分）。
            knowledge_bases: 知识库白名单（catalog 按 incident_type 限定），None 表示不过滤。
            required_metadata: 必需元数据维度，缺失写入 missing_filters。
            top_k: 返回条数上限（默认 5）。
            today: 过期判定基准（缺省当天，便于测试注入固定日期）。

        Returns:
            按 score 降序的 top_k 条命中，且只保留 score > 0（无任何匹配的分块不返回）。
        """

        today = today or date.today()
        query_tokens = _tokens(query)                  # query 分词集合，用于与 chunk 求交集
        bases = set(knowledge_bases or ())             # 知识库白名单集合（空=不过滤）
        required = tuple(required_metadata or ())      # 必需元数据维度（转 tuple 便于传递）
        hits: list[KnowledgeHit] = []

        for chunk in self._chunks:
            # ① 知识库归属过滤：不在白名单内的直接跳过（catalog 按 incident_type 限定范围）
            if bases and chunk.knowledge_base not in bases:
                continue

            reasons: list[str] = []  # 命中理由（前端展示"为什么命中"，也用于审计可解释）
            score = 0.0

            # ② 四维实体匹配打分：报警码最强(+6) > 设备(+4) > 工序/产品(+2)
            if alarm_code and alarm_code in chunk.applicable_alarm_codes:
                score += 6.0
                reasons.append("报警代码命中")
            if device_type and device_type in chunk.applicable_device_types:
                score += 4.0
                reasons.append("设备类型匹配")
            if process and process in chunk.applicable_process:
                score += 2.0
                reasons.append("工序匹配")
            if product_code and product_code in chunk.applicable_product_codes:
                score += 2.0
                reasons.append("产品码匹配")

            # ③ 关键词重叠打分：query 与 chunk（标题+路径+正文）的 token 交集，每个 +0.75，封顶 8 个
            chunk_tokens = _tokens(f"{chunk.title} {chunk.section_path} {chunk.text}")
            overlap = query_tokens & chunk_tokens
            if overlap:
                score += min(len(overlap), 8) * 0.75
                reasons.append(f"关键词命中 {len(overlap)} 个")

            # 无任何匹配 → 跳过（不产出命中）
            if score <= 0:
                continue

            # ④ 过期判定：过期降权 -2 但不丢弃（仍可参考），标 expired
            expired = self._is_expired(chunk, today)
            if expired:
                score -= 2.0
                reasons.append("文档已过期，已降权")

            # ⑤ 高风险文档权威加权：+0.5（高风险 SOP 通常更权威）
            if chunk.risk_level == "high":
                score += 0.5
                reasons.append("高风险文档，权威级加权")

            # ⑥ 必需元数据校验：缺失维度记入 missing_filters（不丢弃，只标记）
            missing = self._missing_filters(chunk, required, process, alarm_code, device_type, product_code)
            if missing:
                reasons.append(f"缺少必需元数据：{','.join(missing)}")

            hits.append(
                KnowledgeHit(
                    chunk=chunk,
                    score=round(score, 2),       # 保留 2 位小数，避免浮点噪声
                    expired=expired,
                    reasons=reasons,
                    missing_filters=missing,
                )
            )

        # 按 score 降序，取 top_k，且只返回 score>0 的命中（过期降权后可能掉到 ≤0，被过滤）
        hits.sort(key=lambda h: h.score, reverse=True)
        return [h for h in hits[:top_k] if h.score > 0]

    @staticmethod
    def _missing_filters(
        chunk: KnowledgeChunk,
        required: tuple[str, ...],
        process: str | None,
        alarm_code: str | None,
        device_type: str | None,
        product_code: str | None,
    ) -> list[str]:
        """校验 chunk 是否具备所有必需元数据维度，返回缺失的维度名列表。

        判定逻辑（对每个 required 维度）：
        - 若该维度对应的 chunk 字段为空 → 缺失；
        - 若调用方传入了该维度的具体值（如 alarm_code="TEMP_HIGH"）但 chunk 不包含它 → 缺失；
        - 调用方未传值时，只校验字段非空。
        缺失的维度记入 missing_filters，使 valid_for_auto_action=False（不能支撑自动动作）。
        """
        missing: list[str] = []
        for name in required:
            if name == "process":
                if not chunk.applicable_process or (process and process not in chunk.applicable_process):
                    missing.append(name)
            elif name == "version":
                if not chunk.version:
                    missing.append(name)
            elif name == "alarm_code":
                if not chunk.applicable_alarm_codes or (
                    alarm_code and alarm_code not in chunk.applicable_alarm_codes
                ):
                    missing.append(name)
            elif name == "device_type":
                if not chunk.applicable_device_types or (
                    device_type and device_type not in chunk.applicable_device_types
                ):
                    missing.append(name)
            elif name == "product_code":
                if not chunk.applicable_product_codes or (
                    product_code and product_code not in chunk.applicable_product_codes
                ):
                    missing.append(name)
        return missing


    @staticmethod
    def _is_expired(chunk: KnowledgeChunk, today: date) -> bool:
        """判定文档是否过期：expire_date 早于 today 视为过期；无 expire_date 或解析失败视为不过期。"""
        if not chunk.expire_date:
            return False
        try:
            return date.fromisoformat(chunk.expire_date) < today
        except ValueError:
            return False  # 日期格式非法不判过期（容错，避免误标把知识全过滤掉）


def build_seed_knowledge() -> list[KnowledgeChunk]:
    """内置示例知识，覆盖阶段 3.7 的四类知识库归属，保证无外部知识库时可跑通链路。

    7 条分块覆盖：equipment（设备，含高风险 + 过期演示）/ quality（质量）/
    trace（追溯）/ process（工艺）。含报警码、设备类型、工序、产品码、风险等级、
    过期日期等元数据，可完整演示 retrieve 的打分、过期降权、权威加权与元数据校验。
    """

    def chunk(
        idx: int,
        doc_id: str,
        title: str,
        section: str,
        device_types: list[str],
        alarm_codes: list[str],
        processes: list[str],
        text: str,
        *,
        knowledge_base: KnowledgeBase = "equipment",
        products: list[str] | None = None,
        risk: str = "medium",
        owner: str = "设备工程部",
        expire: str | None = None,
    ) -> KnowledgeChunk:
        """构造一条种子分块的内部辅助：统一填充版本/生效日期/来源 URI 等公共字段。"""
        return KnowledgeChunk(
            chunk_id=f"kc-{idx:03d}",
            doc_id=doc_id,
            title=title,
            section_path=section,
            version="v1.2",
            effective_date="2026-01-01",
            expire_date=expire,
            knowledge_base=knowledge_base,
            applicable_device_types=device_types,
            applicable_alarm_codes=alarm_codes,
            applicable_process=processes,
            applicable_product_codes=products or [],
            risk_level=risk,  # type: ignore[arg-type]  # 字面量 str → RiskLevel，Pydantic 运行时校验
            owner=owner,
            text=text,
            source_uri=f"kb://{doc_id}#{section}",
        )

    return [
        # 1. 设备类 · 高风险 SOP：报警码 TEMP_HIGH/SPINDLE_OVERHEAT，设备 CNC/MACHINE_CENTER
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
            knowledge_base="equipment",
            risk="high",
        ),
        # 2. 设备类 · 振动诊断：多设备类型覆盖（CNC/MACHINE_CENTER/PUMP）
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
            knowledge_base="equipment",
        ),
        # 3. 质量类 · 高风险：批次冻结需质量负责人审批，系统不得自动执行（演示高风险+人工约束）
        chunk(
            3,
            "SOP-QM-001",
            "批次不良追溯与冻结流程",
            "第4章 批次处置/4.1 冻结与隔离",
            [],
            ["QUALITY_DEFECT", "DIM_OUT_OF_TOL"],
            ["OP-30", "OP-40"],
            "发现批量不良时：1) 立即隔离当前批次并挂标识；2) 追溯同批次上下游工序记录；"
            "3) 由质量工程师确认冻结范围；4) 冻结需质量负责人审批，系统不得自动执行。",
            knowledge_base="quality",
            risk="high",
            owner="质量管理部",
        ),
        # 4. 追溯类 · 物料缺料与替代料：替代料切换需工艺与质量双确认
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
            knowledge_base="trace",
            owner="物流部",
        ),
        # 5. 设备类 · 已过期文档：expire=2025-12-31，演示过期降权（today 晚于该日期时 score-2）
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
            knowledge_base="equipment",
            expire="2025-12-31",
        ),
        # 6. 工艺类 · 装配作业指导书：绑定产品码 P-2001/P-3002，演示产品码匹配打分
        chunk(
            6,
            "ESOP-ASM-001",
            "工位装配作业指导书",
            "第2章 装配步骤/2.1 扭矩与顺序",
            [],
            [],
            ["OP-20", "OP-30"],
            "装配顺序：1) 扫码确认物料批次与工单匹配；2) 按 E-SOP 步骤逐序装配；"
            "3) 扭矩按工艺模板设定值采集并记录；4) 完成后自检并生成可追溯装配履历。",
            knowledge_base="process",
            products=["P-2001", "P-3002"],
            owner="工艺技术部",
        ),
        # 7. 追溯类 · 追溯链说明：演示工序范围跨多工序 + 产品码绑定
        chunk(
            7,
            "TRC-QM-001",
            "产品码-物料批次追溯链说明",
            "第3章 追溯链/3.2 码批绑定",
            [],
            ["DIM_OUT_OF_TOL", "QUALITY_DEFECT"],
            ["OP-10", "OP-20", "OP-30"],
            "追溯链由产品码、物料批次、工步记录与检测结果四段构成；"
            "出现不良时按追溯链向上游定位首道异常工序，并评估关联批次隔离范围。",
            knowledge_base="trace",
            products=["P-2001"],
            owner="质量管理部",
        ),
    ]