"""阶段 5.4 提示词骨架 + 可选 LLM 结构化输出（未配置时自动降级为规则模式）。"""

from __future__ import annotations

import json
from typing import Any

from industrial_agent.config import Settings
from industrial_agent.models.findings import AgentFinding

PROMPT_TEMPLATE = """角色：你是{domain}分析助手，服务于工业现场{scenario}。

输入：
- 事件与实体信息：{incident}
- 已查询数据：{tool_results}
- 知识依据：{knowledge_chunks}

要求：
1. 只基于给定数据与知识作答，不得编造设备状态、参数、文档内容。
2. 每条结论必须给出对应证据来源；证据不足时明确写入 data_gaps。
3. 区分"事实"与"假设"，假设必须标注置信度。
4. 不输出任何直接控制设备的指令；如需控制，仅输出建议与风险。
5. 严格按给定 JSON 结构输出，不添加额外字段。

输出：AgentFinding 结构
"""


def build_prompt(
    *,
    domain: str,
    scenario: str,
    incident: dict[str, Any],
    tool_results: dict[str, Any],
    knowledge_chunks: list[dict[str, Any]],
) -> str:
    return PROMPT_TEMPLATE.format(
        domain=domain,
        scenario=scenario,
        incident=json.dumps(incident, ensure_ascii=False, default=str),
        tool_results=json.dumps(tool_results, ensure_ascii=False, default=str),
        knowledge_chunks=json.dumps(knowledge_chunks, ensure_ascii=False, default=str),
    )


class LLMClient:
    """基于 LangChain 的结构化输出客户端；未启用或失败时返回 None 以走规则降级。"""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._model: Any | None = None

    @property
    def enabled(self) -> bool:
        return self._settings.llm_enabled and bool(self._settings.llm_api_key)

    def _get_model(self) -> Any:
        if self._model is None:
            from langchain_openai import ChatOpenAI

            kwargs: dict[str, Any] = {
                "model": self._settings.llm_model,
                "api_key": self._settings.llm_api_key,
                "timeout": self._settings.llm_timeout_seconds,
            }
            if self._settings.llm_base_url:
                kwargs["base_url"] = self._settings.llm_base_url
            self._model = ChatOpenAI(**kwargs).with_structured_output(AgentFinding)
        return self._model

    def structured_finding(
        self,
        *,
        domain: str,
        scenario: str,
        incident: dict[str, Any],
        tool_results: dict[str, Any],
        knowledge_chunks: list[dict[str, Any]],
    ) -> AgentFinding | None:
        if not self.enabled:
            return None
        prompt = build_prompt(
            domain=domain,
            scenario=scenario,
            incident=incident,
            tool_results=tool_results,
            knowledge_chunks=knowledge_chunks,
        )
        for _ in range(2):
            try:
                finding = self._get_model().invoke(prompt)
                if isinstance(finding, AgentFinding):
                    return finding
                return AgentFinding.model_validate(finding)
            except Exception:  # noqa: BLE001
                continue
        return None