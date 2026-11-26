from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

KnowledgeBase = Literal["process", "quality", "equipment", "trace"]
RiskLevel = Literal["low", "medium", "high"]


class KnowledgeChunk(BaseModel):
    """阶段 3.3 知识元数据结构（阶段 3.7 增加四类知识库归属）。"""

    chunk_id: str
    doc_id: str
    title: str
    section_path: str
    version: str
    effective_date: str
    expire_date: str | None = None
    knowledge_base: KnowledgeBase = "process"
    applicable_device_types: list[str] = Field(default_factory=list)
    applicable_alarm_codes: list[str] = Field(default_factory=list)
    applicable_process: list[str] = Field(default_factory=list)
    applicable_product_codes: list[str] = Field(default_factory=list)
    risk_level: RiskLevel = "low"
    owner: str
    text: str
    source_uri: str


class KnowledgeHit(BaseModel):
    chunk: KnowledgeChunk
    score: float
    expired: bool = False
    reasons: list[str] = Field(default_factory=list)
    missing_filters: list[str] = Field(default_factory=list)

    @property
    def valid_for_auto_action(self) -> bool:
        return not self.expired and not self.missing_filters