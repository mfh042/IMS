from __future__ import annotations

from pydantic import BaseModel, Field


class AgentFinding(BaseModel):
    """阶段 5.3 子 Agent 统一输出结构。"""

    agent_name: str
    summary: str
    findings: list[str] = Field(default_factory=list)
    hypotheses: list[str] = Field(default_factory=list)
    evidence: list[dict] = Field(default_factory=list)
    knowledge_refs: list[str] = Field(default_factory=list)
    confidence: float = Field(default=0.0, ge=0, le=1)
    data_gaps: list[str] = Field(default_factory=list)
    suggested_next_steps: list[str] = Field(default_factory=list)
    risk_notes: list[str] = Field(default_factory=list)