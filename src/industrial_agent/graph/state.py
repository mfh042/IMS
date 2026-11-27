"""阶段 6.4 图状态定义。

本文件定义 LangGraph 主图各节点之间传递数据的唯一契约：``IndustrialAgentState``。

设计要点：
- 采用 ``TypedDict`` 而非 dataclass：LangGraph 节点返回值是普通 dict，TypedDict 既能提供
  类型提示，又能直接被图运行时合并，无需额外序列化/反序列化开销；
- ``total=False``：所有字段都是可选的——节点只回写它负责的字段，其余字段保持原值，
  这正是"节点间靠 state 传话、互不覆盖"的根基；
- 字段值一律用可序列化的基础类型（dict/list/str/bool），不直接放模型对象，
  保证 state 可被检查点持久化、可回放、可跨进程传输。
"""

from __future__ import annotations

from typing import TypedDict


class IndustrialAgentState(TypedDict, total=False):
    """主图状态契约：14 个节点通过它传递数据，节点返回值会被自动合并进此对象。

    每个字段下方标注：由哪个节点写入 / 主要用途。
    """

    # —— 接入阶段 ——

    event: dict
    """节点 1（normalize_event）写入：归一化后的 StandardEvent（model_dump 成 dict）。
    初始值是外部传入的原始异构报文，归一化成功后被替换为标准事件；
    失败（死信）时此字段保持空，路由据此直接跳到 respond 结束。"""

    incident: dict
    """节点 2（governance_check）写入：治理聚合后构建的 Incident（序列化 dict）。
    后续几乎所有节点都依赖它取 trace_id / incident_id / entity_refs 等上下文，
    是贯穿全流程的"事件身份证"。"""

    # —— 规划/取证阶段 ——

    incident_type: str
    """节点 3（classify_incident）写入：最终判定的事件类型，如 equipment_exception /
    mixed_exception 等。节点 4 据此路由子任务、节点 5 据此限定知识库范围。"""

    subtasks: list[dict]
    """节点 4（plan_subtasks）写入：子任务清单，每项含 agent / agent_name / status。
    初始 status 统一为 pending，节点 6 据此逐个派发执行。"""

    agent_codes: list[str]
    """节点 4（plan_subtasks）写入：本事件要跑的子智能体编号列表（已按灰度发布范围裁剪）。
    是 subtasks 的精简版，方便节点 6 直接遍历。"""

    knowledge_refs: list[dict]
    """节点 5（retrieve_knowledge）写入：知识检索命中片段列表（KnowledgeHit 序列化）。
    "先知识后取证"——节点 6 跑子智能体时直接复用这些命中，避免重复查询。"""

    agent_findings: dict
    """节点 6（run_specialists）写入：各子智能体的取证结论，结构为 {agent_code: finding_dict}。
    单个 agent 失败只进 errors，不会影响此字段中其他 agent 的结论。"""

    # —— 规划阶段（信息汇聚 → 方案 → 校验）——

    merged_findings: dict
    """节点 7（merge_findings）写入：合并后的统一发现，含去重证据 evidence / 汇总数据缺口
    data_gaps / 低置信冲突 conflicts / 最低置信度 min_confidence / agent_count。"""

    resolution_plan: dict
    """节点 8（build_resolution_plan）写入、节点 9（validate_plan）回写：处置方案
    （ResolutionPlan 序列化），含问题概述、动作清单、置信度、数据缺口等，是执行阶段的输入。"""

    risk_level: str
    """节点 8（build_resolution_plan）写入：方案中所有动作的最高风险等级（low/medium/high），
    供前端展示整体风险概况（执行路由实际看三分桶，不直接用此字段）。"""

    need_human_confirm: bool
    """节点 9（validate_plan）写入、节点 11 读取：方案是否需要人工确认。
    透传给风险路由决定是否走 request_human_confirm，也决定 record_result 是否闭环事件。"""

    validation_issues: list[dict]
    """节点 9（validate_plan）写入：方案校验发现的问题清单（PlanValidationIssue 序列化），
    会被节点 12 拼进最终结论，提示人工关注。"""

    # —— 执行阶段（按风险三分桶）——

    executed_actions: list[dict]
    """节点 10a/10b/10c 累积写入：已成功执行的动作列表（ActionExecution 序列化）。
    三个执行节点都用 list() 读取已有值再 append，保证 safe→medium 串接时不丢失。"""

    deferred_actions: list[dict]
    """节点 10b/10c 累积写入：因灰度未开放或人工未确认而转为人工待办的动作列表，
    每项含 action_id / title / risk_level / 转待办原因。"""

    confirm_request: dict
    """节点 10c（request_human_confirm）写入：抛出给外部的人工确认请求（序列化），
    含 confirm_id / 风险说明 / 回滚提示 / 所需角色 / 过期时间，供前端展示。"""

    confirm_status: str
    """节点 10c 写入：人工确认的最终状态，如 approved / rejected / expired / deferred_manual，
    节点 11 汇总进审计、节点 12 拼进最终结论。"""

    # —— 收尾阶段 ——

    audit_log_id: str
    """节点 11（record_result）写入：本事件汇总审计记录的 id，用于事后追溯整条处置链路。"""

    final_answer: str
    """节点 1（死信）/ 节点 2（重复/限流）/ 节点 12（respond）写入：人类可读的最终结论。
    提前终止的节点会先写一个简短结论，节点 12 若无方案则透传它，保证调用方总能拿到文案。"""

    # —— 流程控制标记 ——

    duplicate: bool
    """节点 2（governance_check）写入：标记事件是否为窗口内重复事件，True 时路由跳到 respond。"""

    throttled: bool
    """节点 2（governance_check）写入：标记事件是否触发限流，True 时路由跳到 respond。"""

    errors: list[dict]
    """节点 1（归一化失败）/ 节点 6（子智能体异常）等累积写入：全流程错误清单，
    每项含 node / agent / message，供排查与最终结论引用。始终用 list() 读取再 append，避免覆盖。"""

    # —— 执行轨迹（前端可视化）——

    node_trace: list[dict]
    """各节点通过 _trace() 追加：节点执行轨迹，每项含 node / at / payload。
    前端编排图据此高亮当前走到哪个节点、展示各节点的关键输出，不参与业务决策。"""