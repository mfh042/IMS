"""阶段 5（子智能体分析）的数据模型。

定义 13 类子智能体共用的统一输出结构 AgentFinding：
不管是设备/质量/物料/哪一类智能体，分析完成后都输出同一份结构，
主图据此汇总根因、证据、置信度与数据缺口，再交给规划层生成解决方案。

统一输出结构的价值：下游规划/治理无需为每类智能体写适配，data_gaps 等字段
直接驱动治理层的降级与阻断决策（缺关键数据的发现不能支撑自动动作）。
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class AgentFinding(BaseModel):
    """阶段 5.3 子 Agent 统一输出结构：所有子智能体分析结果的标准载体。"""

    agent_name: str                          # 智能体标识（与 catalog 的 name 对应）
    summary: str                             # 分析摘要（一句话结论）
    findings: list[str] = Field(default_factory=list)      # 发现列表（具体观察到的事实）
    hypotheses: list[str] = Field(default_factory=list)    # 根因假设（推测的可能原因）
    evidence: list[dict] = Field(default_factory=list)     # 证据列表（工具调用结果等）
    knowledge_refs: list[str] = Field(default_factory=list)  # 引用的知识分块 id（追溯知识来源）
    confidence: float = Field(default=0.0, ge=0, le=1)     # 置信度（0-1，Pydantic 校验范围）
    data_gaps: list[str] = Field(default_factory=list)     # 数据缺口（缺则治理层降级/阻断自动动作）
    suggested_next_steps: list[str] = Field(default_factory=list)  # 建议的下一步动作
    risk_notes: list[str] = Field(default_factory=list)    # 风险提示（供规划层参考）