"""阶段 6：主调度与 LangGraph 编排。

本文件是整个工业多智能体系统的 **唯一装配点 + 总指挥**：
- 把下层所有子系统（归一化 / 治理 / 知识 / 工具 / 智能体 / 规划 / 执行 / 审计）组装在一起；
- 用 LangGraph 的 StateGraph 画出 14 节点的确定性流程图，事件按节点顺序流转；
- 对外提供 run（跑完整主图）/ resume（人工确认后恢复）/ replay（按 trace 回放审计）/
  run_single_agent（前端单智能体模拟）四个入口。

图结构：
START -> normalize_event -> governance_check -> classify_incident -> plan_subtasks
      -> retrieve_knowledge -> run_specialists -> merge_findings
      -> build_resolution_plan -> validate_plan -> risk_route
      -> execute_safe_actions / execute_medium_risk_actions / request_human_confirm
      -> record_result -> respond -> END

执行流程图（Mermaid，可在支持渲染的 Markdown 查看器中预览）：

```mermaid
flowchart TD
    START(["开始 run(raw_event)"]) --> N1

    subgraph S1["① 接入阶段"]
        N1["归一化事件<br/>normalize_event"]
        N2["治理 去重/聚合/限流<br/>governance_check"]
    end
    subgraph S2["② 规划阶段"]
        N3["判定事件类型<br/>classify_incident"]
        N4["路由子任务<br/>plan_subtasks"]
    end
    subgraph S3["③ 取证阶段"]
        N5["知识检索<br/>retrieve_knowledge"]
        N6["并行运行子Agent<br/>run_specialists"]
    end
    subgraph S4["④ 规划阶段"]
        N7["合并发现<br/>merge_findings"]
        N8["生成处置方案<br/>build_resolution_plan"]
        N9["方案校验与风险分级<br/>validate_plan"]
    end
    subgraph S5["⑤ 执行阶段"]
        N10A["执行低风险动作<br/>execute_safe_actions"]
        N10B["灰度执行中风险<br/>execute_medium_risk_actions"]
        N10C["人工确认 可挂起<br/>request_human_confirm"]
    end
    subgraph S6["⑥ 收尾阶段"]
        N11["记录审计结果<br/>record_result"]
        N12["生成最终结论<br/>respond"]
    end

    N1 -->|"成功 有event"| N2
    N1 -->|"失败 死信"| N12
    N2 -->|"通过 有incident"| N3
    N2 -->|"重复 / 限流"| N12
    N3 --> N4 --> N5 --> N6 --> N7 --> N8 --> N9
    N9 -->|"confirm 有高风险待确认"| N10C
    N9 -->|"safe 有低风险可执行"| N10A
    N9 -->|"medium 仅中风险可执行"| N10B
    N9 -->|"none 无可执行动作"| N11
    N10A --> N10B
    N10B --> N11
    N10C --> N11
    N11 --> N12
    N12 --> END(["结束"])

    N10C -.->|"interrupt 挂起 抛出确认请求"| WAIT(["等待外部人工决策"])
    WAIT -.->|"resume(thread_id, decision) 恢复"| N10C
```

说明：
- 节点间靠 IndustrialAgentState 传话，互不直接调用，保证流程确定、可回放；
- ⑩c 节点通过 interrupt() 挂起，由 resume() 恢复，decision 缺省 approved 一律视为拒绝（安全默认）；
- ⑩a→⑩b 始终串接：safe 路由先跑低风险，再进中风险节点由灰度策略决定是否真执行；
- run_single_agent 是独立旁路：归一化→治理→知识检索→单 Agent，不进入主图，见方法注释。
"""

from __future__ import annotations

# ---------- 标准库 ----------
from concurrent.futures import ThreadPoolExecutor  # 并行跑多个子智能体
from datetime import UTC, datetime  # 审计时间戳
from typing import Any

# ---------- LangGraph 编排底座 ----------
from langgraph.checkpoint.memory import InMemorySaver  # 内存检查点，支持 interrupt 挂起/恢复
from langgraph.graph import END, START, StateGraph  # 有向图构建
from langgraph.types import interrupt  # 人工确认挂起原语

# ---------- 智能体层 ----------
from industrial_agent.agents.base import AgentContext  # 传给子智能体的运行时上下文
from industrial_agent.agents.catalog import (  # 13 类智能体的"单一事实来源"
    agent_name_of,  # 编号 → 中文名
    is_readonly,  # 编号是否只读（A1/B1 第一版只读）
    knowledge_bases_for,  # incident_type → 应检索的知识库
    required_knowledge_for,  # incident_type → 必需的知识元数据维度
    resolve_codes,  # incident_type + 灰度范围 → 最终要跑的 agent 编号列表
)
from industrial_agent.agents.factory import build_agent  # 编号 → 智能体实例
from industrial_agent.agents.llm import LLMClient  # 可选大模型增强

# ---------- 审计 / 配置 ----------
from industrial_agent.audit.recorder import AuditRecorder
from industrial_agent.config import Settings, rollout_policy  # 全局配置 + 四级灰度策略

# ---------- 执行 / 治理 / 接入 / 知识 ----------
from industrial_agent.execution.executor import ActionExecutor, ConfirmStore  # 执行器 + 人工确认存储
from industrial_agent.governance.governor import EventGovernance  # 去重/聚合/限流/优先级
from industrial_agent.graph.state import IndustrialAgentState  # 22 字段状态契约，节点间靠它传数据
from industrial_agent.ingestion.normalizer import EventNormalizer  # 异构报文 → 标准事件
from industrial_agent.knowledge.store import KnowledgeStore  # 知识库检索

# ---------- 数据模型 ----------
from industrial_agent.models.events import Incident, StandardEvent
from industrial_agent.models.findings import AgentFinding  # 子智能体输出
from industrial_agent.models.plan import ProposedAction, ResolutionPlan

# ---------- 规划 / 工具 ----------
from industrial_agent.planning.builder import PlanBuilder, PlanValidator  # 方案生成 + 校验
from industrial_agent.tools.contracts import ToolContext  # 工具调用上下文（含 allow_write 开关）
from industrial_agent.tools.factory import build_default_registry  # 装配 17 个默认工具
from industrial_agent.tools.registry import ToolRegistry  # 工具唯一入口 + 九步检查链

# 事件类型关键词映射：当报文正文命中这些关键词时，用于辅助判定 / 修正 incident_type。
# 命中多个不同类型的关键词 → 升级为 mixed_exception（混合异常）。
KEYWORD_TYPE_MAP = {
    "equipment_exception": ("设备", "报警", "温度", "振动", "通讯", "device", "alarm", "temp", "vibration"),
    "quality_exception": ("不良", "质量", "检验", "缺陷", "defect", "quality"),
    "material_exception": ("缺料", "库存", "物料", "批次", "material", "stock"),
    "production_exception": ("工单", "阻塞", "停线", "工序", "work_order", "blocked"),
}


class IndustrialAgentGraph:
    """可回放、可中断、可人工介入的确定性编排图。

    设计要点：
    - 所有子系统在 __init__ 里装配一次，之后只读使用，保证状态可预测；
    - 构造参数全部可注入（settings/tools/knowledge/governance/audit/llm），便于单元测试替换；
    - 流程确定性：14 节点顺序固定，子系统之间零横向调用，全由节点方法串联。
    """

    def __init__(
        self,
        *,
        settings: Settings | None = None,
        tools: ToolRegistry | None = None,
        knowledge: KnowledgeStore | None = None,
        governance: EventGovernance | None = None,
        audit: AuditRecorder | None = None,
        llm: LLMClient | None = None,
        confirm_ttl_seconds: int = 3600,
    ) -> None:
        # 全局配置（读 .env / 环境变量 IAMS_* 前缀），单例缓存
        self._settings = settings or Settings()
        # 四级灰度策略：shadow（默认只看不做）/ low_auto / medium_auto / high_controlled
        self._rollout = rollout_policy(self._settings.rollout_mode)
        # 审计记录器：内存列表 + 按天 JSONL 落盘到 var/audit
        self._audit = audit or AuditRecorder(audit_dir=self._settings.audit_dir)
        # 工具注册表：17 个默认工具的唯一入口，内部带九步前置检查链
        self._tools = tools or build_default_registry(registry=ToolRegistry(self._audit))
        # 知识库：启动加载 var/knowledge，目录空则回退内置种子知识
        self._knowledge = knowledge or KnowledgeStore()
        # 事件治理：去重窗口 / 并发上限 / 单实体上限 / 优先级等待权重，全来自配置
        self._governance = governance or EventGovernance(
            dedup_window_seconds=self._settings.dedup_window_seconds,
            max_concurrent_incidents=self._settings.max_concurrent_incidents,
            per_entity_incident_limit=self._settings.per_entity_incident_limit,
            priority_wait_weight=self._settings.priority_wait_weight,
        )
        # 大模型客户端：可选增强，未配 key 时 enabled() 返回 False，自动降级走规则
        self._llm = llm or LLMClient(self._settings)
        # 报文归一化：WMS/MES/QMS/EAM 等异构报文 → 统一 StandardEvent
        self._normalizer = EventNormalizer(self._settings.tenant_id)
        # 规划：PlanBuilder 汇总多智能体结论生成方案；PlanValidator 校验并修正可执行性/置信度
        self._plan_builder = PlanBuilder()
        self._plan_validator = PlanValidator()
        # 人工确认存储：TTL 默认 3600s，超时置 expired 且严禁默认同意
        self._confirm_store = ConfirmStore(confirm_ttl_seconds)
        # 动作执行器：三分桶（auto/todo/confirm）+ 调工具执行 + 失败回滚 + 写审计
        self._executor = ActionExecutor(self._tools, audit=self._audit, confirms=self._confirm_store)
        # LangGraph 内存检查点：让图支持 interrupt() 挂起，等待外部 resume 恢复
        self._checkpointer = InMemorySaver()
        # 构建并编译 14 节点 StateGraph（见 _build）
        self._graph = self._build()

    # ---------- 属性 ----------
    # 以下只读属性向外部（API 层 / 测试）暴露内部子系统，外部不应修改这些实例。

    @property
    def governance(self) -> EventGovernance:
        return self._governance

    @property
    def audit(self) -> AuditRecorder:
        return self._audit

    @property
    def executor(self) -> ActionExecutor:
        return self._executor

    @property
    def confirms(self) -> ConfirmStore:
        return self._confirm_store

    @property
    def tools(self) -> ToolRegistry:
        return self._tools

    @property
    def graph_nodes(self) -> list[dict[str, Any]]:
        """主图 14 节点的执行顺序清单（前端绘制编排图用）。

        phase 把节点归到五个阶段：接入 / 规划 / 取证 / 执行 / 收尾，
        与 _build() 中 add_node 的注册顺序严格一致，前端据此画流程图。
        """

        return [
            {"id": "normalize_event", "name": "归一化事件", "phase": "接入"},
            {"id": "governance_check", "name": "治理（去重/聚合/限流）", "phase": "接入"},
            {"id": "classify_incident", "name": "判定事件类型", "phase": "规划"},
            {"id": "plan_subtasks", "name": "路由子任务", "phase": "规划"},
            {"id": "retrieve_knowledge", "name": "知识检索", "phase": "取证"},
            {"id": "run_specialists", "name": "并行运行子 Agent", "phase": "取证"},
            {"id": "merge_findings", "name": "合并发现", "phase": "规划"},
            {"id": "build_resolution_plan", "name": "生成处置方案", "phase": "规划"},
            {"id": "validate_plan", "name": "方案校验与风险分级", "phase": "规划"},
            {"id": "execute_safe_actions", "name": "执行低风险动作", "phase": "执行"},
            {"id": "execute_medium_risk_actions", "name": "灰度执行中风险", "phase": "执行"},
            {"id": "request_human_confirm", "name": "人工确认（可挂起）", "phase": "执行"},
            {"id": "record_result", "name": "记录审计结果", "phase": "收尾"},
            {"id": "respond", "name": "生成最终结论", "phase": "收尾"},
        ]

    # ---------- 图构建 ----------

    def _build(self) -> Any:
        """构建并编译 14 节点 LangGraph 流程图。

        节点返回值会被 LangGraph 自动合并进 IndustrialAgentState（更新对应字段）；
        节点之间通过 state 传话，不直接互调——这就是"流程确定、可回放"的根基。
        """
        graph = StateGraph(IndustrialAgentState)  # 状态对象定义节点间的数据契约

        # ---- 注册 14 个节点（id → 节点处理函数）----
        graph.add_node("normalize_event", self._normalize_event)        # 1. 异构报文归一化
        graph.add_node("governance_check", self._governance_check)      # 2. 去重/聚合/限流/优先级
        graph.add_node("classify_incident", self._classify_incident)    # 3. 判定 incident_type
        graph.add_node("plan_subtasks", self._plan_subtasks)            # 4. 路由：决定跑哪几个 agent
        graph.add_node("retrieve_knowledge", self._retrieve_knowledge)  # 5. ★ 先取知识，再取证
        graph.add_node("run_specialists", self._run_specialists)        # 6. 并行跑子智能体
        graph.add_node("merge_findings", self._merge_findings)          # 7. 合并多 agent 发现
        graph.add_node("build_resolution_plan", self._build_plan)       # 8. 生成处置方案
        graph.add_node("validate_plan", self._validate_plan)            # 9. 方案校验 + 风险分级
        graph.add_node("execute_safe_actions", self._execute_safe_actions)         # 10a. 低风险自动执行
        graph.add_node("execute_medium_risk_actions", self._execute_medium_risk_actions)  # 10b. 中风险灰度执行
        graph.add_node("request_human_confirm", self._request_human_confirm)      # 10c. 高风险人工确认
        graph.add_node("record_result", self._record_result)            # 11. 写审计、归集结果
        graph.add_node("respond", self._respond)                        # 12. 生成 final_answer

        # ---- 连线 ----
        graph.add_edge(START, "normalize_event")

        # 归一化后：成功 → 治理；失败(死信) → 直接 respond 结束
        graph.add_conditional_edges(
            "normalize_event",
            self._route_after_normalize,
            {"continue": "governance_check", "respond": "respond"},
        )
        # 治理后：重复/限流 → respond 结束；否则 → 判定类型
        graph.add_conditional_edges(
            "governance_check",
            self._route_after_governance,
            {"continue": "classify_incident", "respond": "respond"},
        )
        # 规划/取证主干：线性串接
        graph.add_edge("classify_incident", "plan_subtasks")
        graph.add_edge("plan_subtasks", "retrieve_knowledge")
        graph.add_edge("retrieve_knowledge", "run_specialists")
        graph.add_edge("run_specialists", "merge_findings")
        graph.add_edge("merge_findings", "build_resolution_plan")
        graph.add_edge("build_resolution_plan", "validate_plan")

        # 风险路由：按三分桶结果选择执行路径
        # - confirm（有高风险待确认动作）→ 人工确认挂起
        # - safe（有可执行的低风险动作）→ 自动执行 low，再串到中风险节点
        # - medium（只有中风险可执行）→ 直接从中风险开始
        # - none（无可执行动作）→ 跳过执行，直接记录结果
        graph.add_conditional_edges(
            "validate_plan",
            self._route_by_risk,
            {
                "safe": "execute_safe_actions",
                "medium": "execute_medium_risk_actions",
                "confirm": "request_human_confirm",
                "none": "record_result",
            },
        )
        # safe 路径：先执行完 low 动作，再串接中风险节点（中风险节点内部会按灰度决定是否真执行）
        graph.add_edge("execute_safe_actions", "execute_medium_risk_actions")
        # 中风险 / 人工确认 之后统一汇聚到记录结果
        graph.add_edge("execute_medium_risk_actions", "record_result")
        graph.add_edge("request_human_confirm", "record_result")
        graph.add_edge("record_result", "respond")
        graph.add_edge("respond", END)

        # compile 时挂检查点：使 interrupt() 能暂停图执行，等外部 resume 恢复
        return graph.compile(checkpointer=self._checkpointer)

    # ---------- 节点 ----------

    def _trace(self, state: IndustrialAgentState, node: str, payload: dict[str, Any]) -> None:
        """往 state 的 node_trace 追加一条节点执行记录（前端展示执行轨迹用）。"""
        state.setdefault("node_trace", []).append(
            {"node": node, "at": datetime.now(UTC).isoformat(), "payload": payload}
        )

    def _normalize_event(self, state: IndustrialAgentState) -> dict[str, Any]:
        """节点 1：把外部原始报文归一化成统一的 StandardEvent。

        写入状态字段：event / errors / final_answer。
        失败（缺字段 / 非法报文）→ 写死信审计，返回 final_answer，路由直接跳到 respond 结束，
        不让脏数据流入后续节点。
        """
        raw = state.get("event") or {}  # 外部传入的原始异构报文
        outcome = self._normalizer.normalize(raw)
        # 归一化失败：进入死信队列，流程提前终止
        if not outcome.ok or outcome.event is None:
            errors = list(state.get("errors", []))
            errors.append(
                {
                    "node": "normalize_event",
                    "error_code": outcome.error_code,
                    "message": outcome.dead_letter_reason,
                }
            )
            self._audit.record(
                trace_id=str(raw.get("trace_id") or "unknown"),
                actor="system",
                action="event:dead_letter",
                input_snapshot=raw,
                decision="denied",
                reason=outcome.dead_letter_reason,
            )
            return {"errors": errors, "final_answer": f"事件进入死信队列：{outcome.dead_letter_reason}"}

        # 归一化成功：把 StandardEvent 写回 state（model_dump 转成可序列化 dict）
        event = outcome.event
        self._audit.record(
            trace_id=event.trace_id,
            actor="system",
            action="event:normalized",
            target=event.event_id,
            input_snapshot=raw,
            output_snapshot=event.model_dump(mode="json"),
        )
        return {"event": event.model_dump(mode="json")}

    def _governance_check(self, state: IndustrialAgentState) -> dict[str, Any]:
        """节点 2：事件治理——去重 / 聚合 / 限流 / 构建 Incident。

        写入状态字段：incident / duplicate / throttled / final_answer。
        重复或被限流 → 直接结束（不往下派单）；通过 → 生成 Incident 进入下一环。
        """
        event = StandardEvent.model_validate(state["event"])  # 从 state 反序列化出标准事件

        # ① 去重：同一 idempotency_key 在窗口内重复到达，丢弃并计数
        if self._governance.is_duplicate(event):
            self._audit.record(
                trace_id=event.trace_id,
                actor="system",
                action="governance:dedup",
                target=event.idempotency_key,
                decision="allowed",
                reason="窗口内重复事件，已丢弃并计数",
            )
            return {
                "duplicate": True,
                "final_answer": (
                    f"重复事件已收敛（idempotency_key={event.idempotency_key}，"
                    f"累计重复 {self._governance.duplicate_count(event.idempotency_key)} 次）"
                ),
            }

        # ② 聚合：按 device/work_order/batch 等键把相关事件聚到一个 Incident
        agg = self._governance.aggregate(event)
        # ③ 限流：全局并发上限 + 单实体上限，超限则排队
        throttled, reason = self._governance.is_throttled(agg)
        if throttled:
            self._audit.record(
                trace_id=event.trace_id,
                actor="system",
                action="governance:throttle",
                target=event.event_id,
                decision="denied",
                reason=reason,
            )
            return {"throttled": True, "final_answer": f"限流触发，事件进入排队：{reason}"}

        # ④ 通过治理：构建 Incident（含聚合键、优先级、生命周期状态）
        incident = self._governance.build_incident(agg, now=event.occurred_at)
        self._audit.record(
            trace_id=incident.trace_id,
            incident_id=incident.incident_id,
            actor="system",
            action="governance:aggregate",
            target=incident.incident_id,
            output_snapshot=incident.model_dump(mode="json"),
        )
        return {"incident": incident.model_dump(mode="json")}

    def _classify_incident(self, state: IndustrialAgentState) -> dict[str, Any]:
        """节点 3：判定事件类型 incident_type（基于治理初判 + 关键词命中修正）。

        写入状态字段：incident / incident_type。
        规则：治理层已有初判；再扫描报文正文关键词，若命中另一类关键词则升级为 mixed_exception，
        命中同类则归回该类。最后把 Incident 生命周期推进到 processing。
        """
        event = StandardEvent.model_validate(state["event"])
        incident = Incident.model_validate(state["incident"])

        incident_type = incident.incident_type  # 治理层的初判类型
        text = f"{event.event_type} {event.message or ''}".lower()  # 用于关键词扫描的文本
        # 关键词命中修正：命中不同类 → 升级 mixed_exception；命中同类 → 归回该类
        for candidate, keywords in KEYWORD_TYPE_MAP.items():
            if any(k.lower() in text for k in keywords):
                if incident_type != candidate and incident_type != "mixed_exception":
                    incident_type = "mixed_exception"
                elif incident_type == "mixed_exception":
                    incident_type = candidate
                break

        incident.incident_type = incident_type  # type: ignore[assignment]
        # 推进 Incident 生命周期：治理阶段已建立（open）→ 进入处理（processing）
        self._governance.close_incident(incident.incident_id, status="processing")
        self._audit.record(
            trace_id=incident.trace_id,
            incident_id=incident.incident_id,
            actor="supervisor",
            action="classify_incident",
            output_snapshot={"incident_type": incident_type},
        )
        return {
            "incident": incident.model_dump(mode="json"),
            "incident_type": incident_type,
        }

    def _plan_subtasks(self, state: IndustrialAgentState) -> dict[str, Any]:
        """节点 4：路由子任务——根据 incident_type + 灰度发布范围，确定要跑哪几个子智能体。

        写入状态字段：subtasks / agent_codes。
        resolve_codes 是 13 类智能体的"单一事实来源"：不同 incident_type 对应不同的 agent 组合，
        同时受 agent_release_scope 灰度配置裁剪（未发布的 agent 不会被派单）。
        """
        incident_type = state.get("incident_type", "mixed_exception")
        # 查表得到本事件要执行的 agent 编号列表（已按发布范围裁剪）
        codes = resolve_codes(
            incident_type,
            release_scope=self._settings.agent_release_scope,
        )
        # 生成子任务清单，状态统一 pending，后续 run_specialists 节点逐个执行
        subtasks = [
            {"agent": code, "agent_name": agent_name_of(code), "status": "pending"}
            for code in codes
        ]
        self._audit.record(
            trace_id=state["incident"]["trace_id"],
            incident_id=state["incident"]["incident_id"],
            actor="supervisor",
            action="plan_subtasks",
            output_snapshot={
                "incident_type": incident_type,
                "release_scope": self._settings.agent_release_scope,
                "rollout_mode": self._settings.rollout_mode,
                "subtasks": subtasks,
            },
        )
        return {"subtasks": subtasks, "agent_codes": codes}

    def _retrieve_knowledge(self, state: IndustrialAgentState) -> dict[str, Any]:
        """节点 5：知识检索——在跑子智能体之前先把相关知识捞出来。

        写入状态字段：knowledge_refs。
        设计要点："先知识后取证"——子智能体能拿到命中的知识片段，避免重复查询；
        检索范围（哪些知识库 / 必需哪些元数据维度）由 catalog 按 incident_type 决定，
        保证检索精度与审计完整性（缺哪些元数据维度会记录在 missing_metadata）。
        """
        incident = Incident.model_validate(state["incident"])
        refs = incident.entity_refs  # 事件携带的实体引用（device/material/batch/process 等）
        # 通过设备工具反查 device_type，作为知识检索的过滤维度
        device_type = self._resolve_device_type(refs.get("device_id"))
        # 拼装检索 query：优先用报警码/物料/批次，退化为 incident_type
        query = " ".join(
            str(v) for v in (refs.get("alarm_code"), refs.get("material_id"), refs.get("batch_id")) if v
        ) or incident.incident_type

        # 检索：多维度过滤 + 限定知识库 + 校验必需元数据，取 top_k=5
        hits = self._knowledge.retrieve(
            query,
            device_type=device_type,
            alarm_code=refs.get("alarm_code"),
            process=refs.get("process_id"),
            knowledge_bases=knowledge_bases_for(incident.incident_type),
            required_metadata=required_knowledge_for(incident.incident_type),
            top_k=5,
        )
        state_hits = [h.model_dump(mode="json") for h in hits]
        # 汇总所有命中片段缺失的过滤维度（用于提示数据完整性问题）
        missing_meta = sorted({m for h in hits for m in h.missing_filters})
        self._audit.record(
            trace_id=incident.trace_id,
            incident_id=incident.incident_id,
            actor="knowledge_retrieval",
            action="knowledge:retrieve",
            target=query,
            output_snapshot={
                "knowledge_bases": list(knowledge_bases_for(incident.incident_type)),
                "hits": [h["chunk"]["chunk_id"] for h in state_hits],
                "missing_metadata": missing_meta,
            },
            decision="executed",
        )
        return {"knowledge_refs": state_hits}

    def _run_specialists(self, state: IndustrialAgentState) -> dict[str, Any]:
        """节点 6：并行运行子智能体——把 subtasks 里的每个 agent 并发跑起来，收集 AgentFinding。

        写入状态字段：agent_findings / errors。
        安全要点：
        - 高风险工具（allow_high_risk）在取证阶段一律关闭；
        - 写权限受灰度策略控制，且 A1/B1 等只读智能体即便灰度放开也禁止写；
        - 单个智能体抛异常不会中断整体，错误收集进 errors，其余智能体继续跑完。
        """
        incident = Incident.model_validate(state["incident"])
        subtasks = state.get("subtasks", [])
        hits = state.get("knowledge_refs", [])

        # 延迟导入，避免模块加载期的循环依赖
        from industrial_agent.models.knowledge import KnowledgeHit

        # 把 state 里序列化的知识命中还原成模型对象，传给子智能体
        knowledge_hits = [KnowledgeHit.model_validate(h) for h in hits]

        # 基础工具上下文：高风险关闭，写权限跟随灰度策略；每个 agent 会再据此拷贝一份
        base_context = ToolContext(
            trace_id=incident.trace_id,
            incident_id=incident.incident_id,
            actor="supervisor",
            roles=["system"],
            tenant_id=self._settings.tenant_id,
            allow_high_risk=False,
            allow_write=self._rollout.allow_write,
        )

        # 要跑的 agent 编号列表；兜底 ["B3"] 保证至少有一个取证结果
        codes = [t["agent"] for t in subtasks if t.get("agent")] or ["B3"]
        findings: dict[str, AgentFinding] = {}  # code → AgentFinding
        errors = list(state.get("errors", []))  # 累积已有错误，避免覆盖

        def run(code: str) -> tuple[str, AgentFinding | None, str | None]:
            """跑单个智能体：返回 (编号, 结论, 错误信息)，错误为 None 表示成功。"""
            agent = build_agent(code, self._audit)
            # A1 / B1 第一版只读取证：即便灰度放开写也禁止其写工具；其余随灰度策略
            allow_write = self._rollout.allow_write and not is_readonly(code)
            ctx_tool = base_context.model_copy(update={"allow_write": allow_write})
            # 组装子智能体运行时上下文：事件/工具/知识/LLM 全靠它注入
            ctx = AgentContext(
                incident=incident,
                tool_context=ctx_tool,
                tools=self._tools,
                knowledge=self._knowledge,
                llm=self._llm,
                knowledge_hits=knowledge_hits,
                scenario=f"{incident.incident_type}->{code}",
            )
            try:
                return code, agent.analyze(ctx), None
            except Exception as exc:  # noqa: BLE001
                # 单个 agent 失败不影响其他：捕获后返回错误字符串，由外层收集
                return code, None, f"{code} 执行失败：{exc}"

        # 线程池并发：最多 4 个 worker，取 agent 数量与 4 的较小值
        with ThreadPoolExecutor(max_workers=min(4, max(1, len(codes)))) as pool:
            for code, finding, error in pool.map(run, codes):
                if error:
                    errors.append({"node": "run_specialists", "agent": code, "message": error})
                if finding is not None:
                    findings[code] = finding

        return {
            "agent_findings": {k: v.model_dump(mode="json") for k, v in findings.items()},
            "errors": errors,
        }

    def _merge_findings(self, state: IndustrialAgentState) -> dict[str, Any]:
        """节点 7：合并多智能体发现——去重证据、汇总数据缺口、标记低置信冲突。

        写入状态字段：merged_findings。
        这是规划前的"信息汇聚层"：多个子智能体可能报同一条证据（去重），
        各自的数据缺口要合并，并把置信度 < 0.4 的结论标记为冲突，供方案生成时参考。
        """
        raw_findings = state.get("agent_findings", {})
        findings = {k: AgentFinding.model_validate(v) for k, v in raw_findings.items()}

        evidence: list[dict] = []  # 去重后的统一证据列表
        conflicts: list[str] = []  # 需要交叉验证的低置信结论
        data_gaps: list[str] = []  # 汇总所有智能体报的数据缺口
        for finding in findings.values():
            for item in finding.evidence:
                if item not in evidence:  # 同一证据多个 agent 都报，只保留一份
                    evidence.append(item)
            data_gaps.extend(finding.data_gaps)
            # 有假设但置信度过低 → 标记冲突，提醒人工或后续交叉验证
            if finding.hypotheses and finding.confidence < 0.4:
                conflicts.append(f"{finding.agent_name} 结论置信度偏低，需交叉验证")

        confidences = [f.confidence for f in findings.values()] or [0.0]
        merged = {
            "evidence": evidence,
            "data_gaps": sorted(set(data_gaps)),  # 去重排序
            "conflicts": conflicts,
            "min_confidence": min(confidences),  # 取最低置信度，反映整体可信度短板
            "agent_count": len(findings),
        }
        self._audit.record(
            trace_id=state["incident"]["trace_id"],
            incident_id=state["incident"]["incident_id"],
            actor="supervisor",
            action="merge_findings",
            output_snapshot=merged,
        )
        return {"merged_findings": merged}

    def _build_plan(self, state: IndustrialAgentState) -> dict[str, Any]:
        """节点 8：生成处置方案——把多智能体结论 + 知识 + 数据质量 + 冲突汇总成 ResolutionPlan。

        写入状态字段：resolution_plan / risk_level。
        PlanBuilder 内部会为每个动作打上 risk_level 和 requires_human_confirm，
        风险等级由本节点顺带算出（取所有动作中的最高风险），供后续路由使用。
        """
        incident = Incident.model_validate(state["incident"])
        findings = {
            k: AgentFinding.model_validate(v) for k, v in state.get("agent_findings", {}).items()
        }
        from industrial_agent.models.knowledge import KnowledgeHit  # 延迟导入，避免循环依赖

        hits = [KnowledgeHit.model_validate(h) for h in state.get("knowledge_refs", [])]
        merged = state.get("merged_findings", {})
        event = StandardEvent.model_validate(state["event"])

        # 方案生成：综合事件 + 各 agent 结论 + 知识命中 + 数据质量 + 冲突清单
        plan = self._plan_builder.build(
            incident=incident,
            findings=findings,
            knowledge_hits=hits,
            data_quality=event.data_quality,
            conflicts=list(merged.get("conflicts", [])),
        )
        self._audit.record(
            trace_id=incident.trace_id,
            incident_id=incident.incident_id,
            actor="supervisor",
            action="build_resolution_plan",
            output_snapshot={"plan_id": plan.plan_id, "confidence": plan.confidence},
        )
        return {
            "resolution_plan": plan.model_dump(mode="json"),
            "risk_level": self._highest_risk(plan),  # 方案中动作的最高风险等级
        }

    def _validate_plan(self, state: IndustrialAgentState) -> dict[str, Any]:
        """节点 9：方案校验与风险分级——PlanValidator 检查可执行性、置信度、风险一致性。

        写入状态字段：resolution_plan / validation_issues / need_human_confirm。
        这是"方案层"独立校验闸：即便智能体生成了方案，也要过一遍校验，
        发现的问题写入 validation_issues，校验不通过会影响后续执行决策。
        """
        plan = ResolutionPlan.model_validate(state["resolution_plan"])
        result = self._plan_validator.validate(plan)
        self._audit.record(
            trace_id=state["incident"]["trace_id"],
            incident_id=state["incident"]["incident_id"],
            actor="supervisor",
            action="validate_plan",
            output_snapshot=result.model_dump(mode="json"),
            decision="allowed" if result.ok else "denied",
        )
        return {
            "resolution_plan": plan.model_dump(mode="json"),
            "validation_issues": [i.model_dump(mode="json") for i in result.issues],
            "need_human_confirm": plan.need_human_confirm,  # 透传给路由，决定是否走人工确认
        }

    def _execute_safe_actions(self, state: IndustrialAgentState) -> dict[str, Any]:
        """节点 10a：执行低风险（safe/auto）动作——无需人工确认，直接自动执行。

        写入状态字段：executed_actions。
        plan_dispatch 把方案动作三分桶：auto（低风险自动）/ todo（中风险待办）/ confirm（高风险待确认），
        本节点只处理 auto 桶，逐个调工具执行（执行器内部带九步检查 + 失败回滚 + 审计）。
        """
        plan = ResolutionPlan.model_validate(state["resolution_plan"])
        context = self._execution_context(state)  # 默认 allow_high_risk=False
        executions = []
        # auto 桶 = 风险低、可自动执行的动作
        for action in self._executor.plan_dispatch(plan)["auto"]:
            execution = self._executor.execute(plan, action, context=context)
            executions.append(execution.model_dump(mode="json"))
        return {"executed_actions": executions}

    def _execute_medium_risk_actions(self, state: IndustrialAgentState) -> dict[str, Any]:
        """节点 10b：灰度执行中风险（medium/todo）动作——是否真执行取决于灰度策略。

        写入状态字段：executed_actions / deferred_actions。
        设计：低风险动作执行完后会串到本节点（即使是 safe 路由也会经过），
        所以要先累积已执行的 executions，再决定中风险动作是执行还是转人工待办。
        - 灰度未开放中风险自动（allow_medium_auto=False）→ 全部进 deferred，转人工待办；
        - 已开放 → 逐个执行可执行的中风险动作。
        """
        plan = ResolutionPlan.model_validate(state["resolution_plan"])
        context = self._execution_context(state)
        executions = list(state.get("executed_actions", []))  # 累积 low 阶段已执行的
        deferred = list(state.get("deferred_actions", []))  # 累积已转待办的
        todo_actions = self._executor.plan_dispatch(plan)["todo"]  # 中风险动作桶

        # 阶段 10.3：未进入中风险闭环前，中风险动作只留待办，不自动执行
        if not self._rollout.allow_medium_auto:
            for action in todo_actions:
                if action.executable:
                    deferred.append(
                        {
                            "action_id": action.action_id,
                            "title": action.title,
                            "risk_level": action.risk_level,
                            "reason": (
                                f"灰度模式 {self._settings.rollout_mode}"
                                " 未开放中风险自动执行"
                            ),
                        }
                    )
            return {"executed_actions": executions, "deferred_actions": deferred}

        # 灰度已开放：执行中风险动作（不可执行的跳过）
        for action in todo_actions:
            if not action.executable:
                continue
            execution = self._executor.execute(plan, action, context=context)
            executions.append(execution.model_dump(mode="json"))
        return {"executed_actions": executions, "deferred_actions": deferred}

    def _request_human_confirm(self, state: IndustrialAgentState) -> dict[str, Any]:
        """节点 10c：人工确认（可挂起）——高风险动作必须经人工批准才能执行。

        写入状态字段：confirm_request / confirm_status / executed_actions / deferred_actions。
        核心机制：interrupt() 是 LangGraph 的挂起原语——图执行到这里会暂停，
        把确认请求 payload 返回给调用方（前端展示），等待外部调用 resume(thread_id, decision) 恢复；
        恢复后从 interrupt() 返回处继续往下执行批准/拒绝逻辑。
        安全默认：decision 非 dict 或 approved 缺省 → 一律视为未批准（严禁默认同意）；
        即便人工同意，若灰度未开放高风险受控执行（allow_high_confirm=False），也只转人工待办不自动执行。
        """
        plan = ResolutionPlan.model_validate(state["resolution_plan"])
        pending = self._executor.plan_dispatch(plan)["confirm"]  # 高风险待确认动作桶
        # 没有待确认动作 → 直接结束（理论上路由不会进此节点，兜底）
        if not pending:
            return {"need_human_confirm": False}

        action = pending[0]  # 一次只处理一个高风险动作（串行确认，避免并发风险）
        # 生成确认请求：含风险说明、回滚提示、所需角色、过期时间（TTL）
        requests = self._executor.request_confirmations(
            plan,
            required_roles=self._roles_for_action(action),
        )
        request = requests[0]

        # 推进 Incident 生命周期：processing → waiting_confirm（等人工）
        self._governance.close_incident(plan.incident_id, status="waiting_confirm")

        # ★ 挂起点：把确认信息抛出，图暂停；恢复后 decision = 外部传入的批准决定
        decision = interrupt(
            {
                "confirm_id": request.confirm_id,
                "incident_id": plan.incident_id,
                "plan_id": plan.plan_id,
                "action_id": action.action_id,
                "action_title": action.title,
                "risk_level": action.risk_level,
                "risks": request.risks,
                "rollback_hint": request.rollback_hint,
                "required_roles": request.required_roles,
                "expire_at": request.expire_at.isoformat(),
            }
        )
        # 安全默认：不是 dict / 缺 approved → False；缺 decision_by → "unknown"
        approved = bool(decision.get("approved")) if isinstance(decision, dict) else False
        decision_by = (
            str(decision.get("decision_by", "unknown")) if isinstance(decision, dict) else "unknown"
        )
        roles = list(decision.get("roles", [])) if isinstance(decision, dict) else []

        # 落确认存储（校验 TTL 是否过期、角色是否匹配）；ok=False 表示确认已失效（超时等）
        ok, status = self._confirm_store.decide(
            request.confirm_id,
            approved=approved,
            decision_by=decision_by,
            roles=roles,
            note=decision.get("note") if isinstance(decision, dict) else None,
        )
        if not ok:
            # 确认已过期/失效：不执行，标记仍需确认
            return {
                "confirm_request": request.model_dump(mode="json"),
                "confirm_status": status,
                "need_human_confirm": True,
            }

        executions = list(state.get("executed_actions", []))
        deferred = list(state.get("deferred_actions", []))
        if approved:
            if self._rollout.allow_high_confirm:
                # 灰度已开放高风险受控：带 confirm_id + operator 执行，上下文打开 allow_high_risk
                execution = self._executor.execute(
                    plan,
                    action,
                    context=self._execution_context(state, allow_high_risk=True, roles=roles or None),
                    confirm_id=request.confirm_id,
                    operator=decision_by,
                )
                executions.append(execution.model_dump(mode="json"))
            else:
                # 阶段 10.3：未进入高风险受控阶段，即便人工同意也不自动落执行，仅转为人工待办
                deferred.append(
                    {
                        "action_id": action.action_id,
                        "title": action.title,
                        "risk_level": action.risk_level,
                        "reason": f"灰度模式 {self._settings.rollout_mode} 未开放高风险受控执行",
                    }
                )
                status = "deferred_manual"
        return {
            "confirm_request": request.model_dump(mode="json"),
            "confirm_status": status,
            "executed_actions": executions,
            "deferred_actions": deferred,
        }

    def _record_result(self, state: IndustrialAgentState) -> dict[str, Any]:
        """节点 11：记录审计结果——汇总方案执行概况，落审计，并关闭 Incident 生命周期。

        写入状态字段：audit_log_id。
        注意：若方案仍需人工确认（need_human_confirm=True），Incident 保持 waiting_confirm，
        不在这里关闭（避免把等确认的事件误标为已闭环）；否则直接 closed。
        提前终止的流程（无 resolution_plan，如死信/重复/限流）会跳过本节点的汇总，直接返回空。
        """
        plan_data = state.get("resolution_plan")
        if not plan_data:
            return {}  # 流程提前终止，无方案可记录
        plan = ResolutionPlan.model_validate(plan_data)
        # 执行概况摘要：方案 id / 置信度 / 是否需确认 / 已执行动作数 / 确认状态
        summary = {
            "plan_id": plan.plan_id,
            "incident_id": plan.incident_id,
            "confidence": plan.confidence,
            "need_human_confirm": plan.need_human_confirm,
            "executed": len(state.get("executed_actions", [])),
            "confirm_status": state.get("confirm_status"),
        }
        audit = self._audit.record(
            trace_id=state["incident"]["trace_id"],
            incident_id=plan.incident_id,
            actor="supervisor",
            action="record_result",
            output_snapshot=summary,
        )
        # 无需人工确认的事件在此闭环；等确认的保持 waiting_confirm
        if not plan.need_human_confirm:
            self._governance.close_incident(plan.incident_id, status="closed")
        return {"audit_log_id": audit.audit_id}

    def _respond(self, state: IndustrialAgentState) -> dict[str, Any]:
        """节点 12：生成最终结论——把 state 里的信息拼装成人类可读的 final_answer。

        写入状态字段：final_answer。
        两种情况：
        - 无方案（提前终止）→ 透传之前节点写的 final_answer，或给兜底文案；
        - 有方案 → 输出问题概述、置信度、数据缺口、有效性提示、处置动作清单、确认/校验状态。
        """
        plan_data = state.get("resolution_plan")
        if not plan_data:
            # 提前终止：复用前面节点（死信/重复/限流）写的结论，没有则给兜底文案
            answer = state.get("final_answer") or "流程提前终止，未生成方案"
            return {"final_answer": answer}

        plan = ResolutionPlan.model_validate(plan_data)
        lines = [
            f"问题概述：{plan.problem_summary}",
            f"综合置信度：{plan.confidence}",
        ]
        if plan.data_gaps:
            lines.append(f"数据缺口：{'；'.join(plan.data_gaps)}")
        if plan.validity_note:
            lines.append(f"有效性提示：{plan.validity_note}")

        # 处置动作清单：标注风险等级 + 执行方式标签
        lines.append("处置动作：")
        for action in plan.actions:
            tag = "需人工确认" if action.requires_human_confirm else (
                "可自动执行" if action.executable else "仅提示"
            )
            lines.append(f"- [{action.risk_level}/{tag}] {action.title}")

        if state.get("confirm_status"):
            lines.append(f"人工确认状态：{state['confirm_status']}")
        if state.get("validation_issues"):
            codes = [i["code"] for i in state["validation_issues"]]
            lines.append(f"方案校验：{', '.join(codes)}")

        return {"final_answer": "\n".join(lines)}

    # ---------- 路由 ----------
    # 路由函数只看 state 决定下一个节点 id，返回值与 _build 里 conditional_edges 的映射表对应。

    def _route_after_normalize(self, state: IndustrialAgentState) -> str:
        """归一化后路由：有 event（成功）→ 治理；无 event（死信）→ respond 结束。"""
        return "continue" if state.get("event") else "respond"

    def _route_after_governance(self, state: IndustrialAgentState) -> str:
        """治理后路由：重复或限流 → respond 结束；有 incident → 继续判定类型。"""
        if state.get("duplicate") or state.get("throttled"):
            return "respond"
        return "continue" if state.get("incident") else "respond"

    def _route_by_risk(self, state: IndustrialAgentState) -> str:
        """风险路由：按三分桶结果选择执行路径，优先级 confirm > safe > medium > none。

        - confirm：有高风险待确认动作 → 人工确认挂起；
        - safe：有可执行的低风险动作 → 自动执行 low（随后串到中风险节点）；
        - medium：只有中风险可执行 → 直接进中风险节点；
        - none：无可执行动作 → 跳过执行，直接记录结果。
        """
        plan_data = state.get("resolution_plan")
        if not plan_data:
            return "none"
        plan = ResolutionPlan.model_validate(plan_data)
        buckets = self._executor.plan_dispatch(plan)  # 三分桶：auto / todo / confirm
        if buckets["confirm"]:
            return "confirm"
        if any(a.executable for a in buckets["auto"]):
            return "safe"
        if any(a.executable for a in buckets["todo"]):
            return "medium"
        return "none"

    # ---------- 辅助 ----------

    def _execution_context(
        self,
        state: IndustrialAgentState,
        *,
        allow_high_risk: bool = False,
        roles: list[str] | None = None,
    ) -> ToolContext:
        """从 state 构造工具调用上下文 ToolContext（执行节点共用）。

        默认关闭高风险（allow_high_risk=False）；仅人工确认节点执行高风险动作时显式打开，
        并带上批准人角色 roles。写权限统一跟随灰度策略 allow_write。
        """
        incident = state.get("incident") or {}
        return ToolContext(
            trace_id=str(incident.get("trace_id", "unknown")),
            incident_id=incident.get("incident_id"),
            actor="system",
            roles=roles or ["system"],
            tenant_id=self._settings.tenant_id,
            allow_high_risk=allow_high_risk,
            allow_write=self._rollout.allow_write,
        )

    def _resolve_device_type(self, device_id: Any) -> str | None:
        """通过 query_device_status 工具反查设备类型，失败/无工具一律返回 None（不阻断流程）。"""
        if not device_id:
            return None
        spec = self._tools.get("query_device_status")
        if spec is None:
            return None
        try:
            data = spec.handler(device_id=str(device_id))  # type: ignore[misc]
        except Exception:  # noqa: BLE001
            return None  # 工具异常不影响主流程，知识检索退化为无 device_type 过滤
        if isinstance(data, dict):
            return data.get("device_type")
        return None

    @staticmethod
    def _highest_risk(plan: ResolutionPlan) -> str:
        """取方案中所有动作的最高风险等级（low < medium < high），无动作默认 low。"""
        order = {"low": 1, "medium": 2, "high": 3}
        return max((a.risk_level for a in plan.actions), key=lambda r: order[r], default="low")

    @staticmethod
    def _roles_for_action(action: ProposedAction) -> list[str]:
        """按动作的工具名决定人工确认所需的批准角色（RBAC 的最小化授权）。"""
        if action.tool_name == "freeze_batch":
            return ["quality_lead"]  # 冻结批次需质量主管
        if action.tool_name == "control_device":
            return ["maintenance_lead"]  # 设备控制需设备主管
        return ["maintenance_lead"]  # 其余默认设备主管

    # ---------- 对外运行接口 ----------
    # 这四个方法是外部（API 层 / 测试 / 前端）与编排图交互的唯一入口。

    def run(self, raw_event: dict[str, Any], *, thread_id: str | None = None) -> dict[str, Any]:
        """跑完整主图：传入原始事件，返回最终 state（含 final_answer / 执行轨迹等）。

        thread_id 用于 LangGraph 检查点隔离——同一 thread_id 的多次 invoke/resume 共享状态，
        是人工确认挂起/恢复的纽带。默认用事件 trace_id，没有则用 "default"。
        """
        thread_id = thread_id or str(raw_event.get("trace_id") or "default")
        config = {"configurable": {"thread_id": thread_id}}
        # 初始 state：只放原始 event + 空的 node_trace/errors，其余字段由节点逐步填充
        return self._graph.invoke({"event": raw_event, "node_trace": [], "errors": []}, config)

    def resume(self, thread_id: str, decision: dict[str, Any]) -> dict[str, Any]:
        """恢复被 interrupt 挂起的图：传入人工确认决定，从挂起点继续执行。

        decision 必须包含 approved（bool），可选 decision_by / roles / note。
        Command(resume=...) 是 LangGraph 恢复挂起图的标准方式。
        """
        from langgraph.types import Command  # 延迟导入

        config = {"configurable": {"thread_id": thread_id}}
        return self._graph.invoke(Command(resume=decision), config)

    def replay(self, trace_id: str) -> str:
        """按 trace_id 回放审计轨迹：返回该事件全流程的审计 JSON 字符串（可追溯）。"""
        return self._audit.dump_json(trace_id)

    # ---------- 单 Agent 模拟（前端可视化用） ----------

    def run_single_agent(
        self,
        code: str,
        raw_event: dict[str, Any],
        *,
        incident_type: str | None = None,
    ) -> dict[str, Any]:
        """单独模拟某个子智能体：归一化 → 治理 → 知识检索 → 运行指定 Agent。

        与主图 ``run`` 的区别：只执行指定编号的单个 Agent，跳过路由收敛与方案/执行阶段，
        便于前端逐个观察每个智能体的取证结论、工具调用与知识命中。
        """

        # ① 归一化（与主图节点 1 同逻辑）：失败直接返回，不往下走
        outcome = self._normalizer.normalize(raw_event)
        if not outcome.ok or outcome.event is None:
            return {
                "ok": False,
                "stage": "normalize",
                "error_code": outcome.error_code,
                "reason": outcome.dead_letter_reason,
            }
        event = outcome.event

        # ② 治理聚合构建 Incident（与主图节点 2 同逻辑，但跳过去重/限流的提前终止分支）
        agg = self._governance.aggregate(event)
        incident = self._governance.build_incident(agg, now=event.occurred_at)

        # ③ 判定 incident_type：调用方显式指定则用指定的；否则走与主图节点 3 一致的关键词修正逻辑
        if incident_type:
            incident.incident_type = incident_type  # type: ignore[assignment]
        else:
            detected = incident.incident_type
            text = f"{event.event_type} {event.message or ''}".lower()
            for candidate, keywords in KEYWORD_TYPE_MAP.items():
                if any(k.lower() in text for k in keywords):
                    if detected != candidate and detected != "mixed_exception":
                        detected = "mixed_exception"
                    elif detected == "mixed_exception":
                        detected = candidate
                    break
            incident.incident_type = detected  # type: ignore[assignment]

        # ④ 知识检索（与主图节点 5 同逻辑）：保证单 Agent 模拟拿到的知识与主图一致
        refs = incident.entity_refs
        device_type = self._resolve_device_type(refs.get("device_id"))
        query = " ".join(
            str(v) for v in (refs.get("alarm_code"), refs.get("material_id"), refs.get("batch_id")) if v
        ) or incident.incident_type
        hits = self._knowledge.retrieve(
            query,
            device_type=device_type,
            alarm_code=refs.get("alarm_code"),
            process=refs.get("process_id"),
            knowledge_bases=knowledge_bases_for(incident.incident_type),
            required_metadata=required_knowledge_for(incident.incident_type),
            top_k=5,
        )

        # ⑤ 构造工具上下文：actor 标记为 simulator（与主图的 supervisor 区分，便于审计溯源）
        allow_write = self._rollout.allow_write and not is_readonly(code)
        ctx_tool = ToolContext(
            trace_id=incident.trace_id,
            incident_id=incident.incident_id,
            actor="simulator",
            roles=["system"],
            tenant_id=self._settings.tenant_id,
            allow_high_risk=False,  # 模拟阶段一律关闭高风险
            allow_write=allow_write,
        )
        agent = build_agent(code, self._audit)
        ctx = AgentContext(
            incident=incident,
            tool_context=ctx_tool,
            tools=self._tools,
            knowledge=self._knowledge,
            llm=self._llm,
            knowledge_hits=hits,
            scenario=f"simulate->{code}",
        )
        # ⑥ 运行指定的单个 Agent（与主图节点 6 中 run(code) 同逻辑，但只跑一个）
        finding = agent.analyze(ctx)

        # 返回完整上下文供前端展示：结论 + 工具调用结果 + 知识命中 + 是否只读
        return {
            "ok": True,
            "agent_code": code,
            "agent_name": agent.name,
            "incident": incident.model_dump(mode="json"),
            "incident_type": incident.incident_type,
            "finding": finding.model_dump(mode="json"),
            "tool_results": ctx.tool_results,
            "knowledge_hits": [h.model_dump(mode="json") for h in hits],
            "readonly": is_readonly(code),
        }