"""阶段 5.4 提示词骨架 + 可选 LLM 结构化输出（未配置时自动降级为规则模式）。

本模块是"规则优先 + LLM 可选降级"策略的落地处：
- build_prompt：把事件/取数结果/知识片段拼成符合安全约束的提示词；
- LLMClient：调用大模型做结构化输出（强制返回 AgentFinding），
  未启用或调用失败时返回 None，调用方自动回落到规则结论，系统不中断。
"""

from __future__ import annotations

import json
from typing import Any

# ---- 全局配置：判断 LLM 是否启用、读 API Key / 模型 / 超时等 ----
from industrial_agent.config import Settings
# ---- 模型层：LLM 必须强制输出的结构化结论 ----
from industrial_agent.models.findings import AgentFinding

# 提示词模板：用 {占位符} 动态填充。约束写死在模板里，防止 LLM 越界：
# 1. 只基于给定数据作答，不得编造；2. 每条结论必须给证据来源；
# 3. 区分事实与假设；4. 不输出控制指令；5. 严格按 JSON 结构输出
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
    """把结构化数据填入提示词模板，生成最终 prompt 字符串。

    所有数据用 json.dumps 序列化（ensure_ascii=False 保留中文），
    避免手写拼接导致的格式错误与注入风险。
    """
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
        # 保存配置引用，模型实例懒加载（首次调用时才创建，避免未启用时浪费资源）
        self._settings = settings
        self._model: Any | None = None

    @property
    def enabled(self) -> bool:
        """LLM 是否真正可用：需同时满足开关打开 + 配置了 API Key。"""
        return self._settings.llm_enabled and bool(self._settings.llm_api_key)

    def _get_model(self) -> Any:
        """懒加载 LangChain 模型实例（带结构化输出绑定），只创建一次。"""
        if self._model is None:
            # 延迟导入：未启用 LLM 时不加载 langchain_openai，减少冷启动开销与依赖耦合
            from langchain_openai import ChatOpenAI

            kwargs: dict[str, Any] = {
                "model": self._settings.llm_model,
                "api_key": self._settings.llm_api_key,
                "timeout": self._settings.llm_timeout_seconds,
            }
            # 兼容百炼 / OpenAI 兼容端点：配置了 base_url 才传入
            if self._settings.llm_base_url:
                kwargs["base_url"] = self._settings.llm_base_url
            # with_structured_output(AgentFinding)：强制模型按 AgentFinding schema 输出，
            # LangChain 会自动做校验与解析
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
        """调用 LLM 生成结构化结论；未启用或失败返回 None，由调用方走规则降级。"""
        # 前置守卫：未启用直接返回 None（调用方据此回落到规则结果）
        if not self.enabled:
            return None
        # 拼装提示词
        prompt = build_prompt(
            domain=domain,
            scenario=scenario,
            incident=incident,
            tool_results=tool_results,
            knowledge_chunks=knowledge_chunks,
        )
        # 最多重试 2 次：LLM 偶发超时/解析失败时重试，仍失败则降级
        for _ in range(2):
            try:
                finding = self._get_model().invoke(prompt)
                # 模型可能直接返回 AgentFinding 实例，也可能返回 dict；
                # 两种情况都规范化成 AgentFinding
                if isinstance(finding, AgentFinding):
                    return finding
                return AgentFinding.model_validate(finding)
            except Exception:  # noqa: BLE001
                # 吞掉异常继续重试，确保 LLM 故障不会中断整个取证流程
                continue
        # 两次都失败 → 返回 None，调用方回落到规则结论
        return None