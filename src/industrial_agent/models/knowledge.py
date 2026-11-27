"""阶段 3（知识底座）的数据模型。

定义知识检索链路的两个核心对象：
- KnowledgeChunk：知识分块（知识管理系统导入的最小检索单元，KnowledgeStore 的数据源）；
- KnowledgeHit：检索命中（KnowledgeStore.retrieve 的返回项，含能否支撑自动动作的判定）。
以及四类知识库归属 KnowledgeBase 与风险等级 RiskLevel（Literal 锁住合法值域）。

安全底线：KnowledgeHit.valid_for_auto_action = (未过期 且 无缺失元数据)，
过期或缺元数据的知识只能参考，不能支撑自动动作——下游规划/执行据此控制写动作。
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

# 四类知识库归属：catalog 按 incident_type 决定本事件应检索哪几类（阶段 3.7）
KnowledgeBase = Literal["process", "quality", "equipment", "trace"]
# 知识风险等级：high 文档在检索时权威加权 +0.5（通常更权威的 SOP）
RiskLevel = Literal["low", "medium", "high"]


class KnowledgeChunk(BaseModel):
    """阶段 3.3 知识元数据结构（阶段 3.7 增加四类知识库归属）。

    知识管理系统导出的最小检索单元，由 KnowledgeStore 加载进内存后参与打分检索。
    四维适用元数据（设备/报警码/工序/产品码）是检索匹配与必需校验的依据。
    """

    chunk_id: str          # 分块唯一标识
    doc_id: str            # 所属文档 id
    title: str             # 分块标题（参与关键词检索）
    section_path: str      # 章节路径（参与关键词检索，也用于定位原文）
    version: str           # 文档版本（required_metadata 可校验其非空）
    effective_date: str    # 生效日期
    expire_date: str | None = None  # 过期日期（早于 today 则降权并标 expired）
    knowledge_base: KnowledgeBase = "process"  # 知识库归属（catalog 白名单过滤依据）
    applicable_device_types: list[str] = Field(default_factory=list)   # 适用设备类型（+4 分）
    applicable_alarm_codes: list[str] = Field(default_factory=list)    # 适用报警码（+6 分，最强信号）
    applicable_process: list[str] = Field(default_factory=list)        # 适用工序（+2 分）
    applicable_product_codes: list[str] = Field(default_factory=list)  # 适用产品码（+2 分）
    risk_level: RiskLevel = "low"  # 风险等级（high 权威加权 +0.5）
    owner: str              # 知识归属部门（如 设备工程部/质量管理部）
    text: str               # 分块正文（参与关键词检索，是知识的实际内容）
    source_uri: str         # 原文来源 URI（前端可跳转定位）


class KnowledgeHit(BaseModel):
    """检索命中：KnowledgeStore.retrieve 的返回项，携带打分与命中理由。"""

    chunk: KnowledgeChunk                        # 命中的知识分块
    score: float                                 # 加权打分（保留 2 位小数）
    expired: bool = False                        # 是否过期（过期降权但不丢弃）
    reasons: list[str] = Field(default_factory=list)          # 命中理由列表（可解释/审计）
    missing_filters: list[str] = Field(default_factory=list)  # 缺失的必需元数据维度

    @property
    def valid_for_auto_action(self) -> bool:
        """知识能否支撑自动动作的判定闸门：未过期 且 无缺失元数据。

        过期或缺元数据的知识只允许作为参考，不允许支撑自动动作（阶段 3.5 安全底线）。
        """
        return not self.expired and not self.missing_filters