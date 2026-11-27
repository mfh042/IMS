"""阶段 7：方案生成、校验与风险分级。

本文件是系统"规划层"的核心，承接主图节点 7（merge_findings）产出的统一发现，
负责把多智能体的取证结论汇总成一份**可执行、可审计、可风险分级**的处置方案，
并在交给执行层前做独立校验。

核心职责（对应主图节点 8 / 9）：
1. **风险分级**（RiskClassifier）：规则优先，按动作类型 + 工具名 + 数据质量给每个动作打
   low / medium / high 风险等级，数据有问题就升级一级（保守策略）；
2. **方案生成**（PlanBuilder）：汇总各 agent 的证据/假设/数据缺口，按 incident_type 生成
   标准化动作清单（复核数据 → 通知/建单 → 高风险确认请求），并算保守置信度；
3. **方案校验**（PlanValidator）：执行层之前的独立闸——查高风险动作是否漏标确认、
   可执行动作是否缺前置条件/证据、控制类工具是否被误标可执行，发现问题就地修正并降级。

设计原则：**规则优先 + 保守降级 + 证据驱动**。LLM 在取证阶段已经用过了，方案阶段回归
确定性规则，保证每次同类事件产出的方案结构一致、可回放、可审计。
"""

from __future__ import annotations

# ---- 标准库 ----
import uuid  # 生成 plan_id（取 uuid4 前 10 位，短且足够唯一）

# ---- 本项目：模型层（本模块只消费模型，不定义模型）----
from industrial_agent.models.events import Incident              # 问题实例（驱动动作模板分支）
from industrial_agent.models.findings import AgentFinding        # 子智能体发现（方案生成的输入）
from industrial_agent.models.knowledge import KnowledgeHit       # 知识命中（转轻量引用 + 过期判定）
from industrial_agent.models.plan import (                       # 规划模型（构造与返回的载体）
    PlanValidationIssue,
    PlanValidationResult,
    ProposedAction,
    ResolutionPlan,
    RiskLevel,
)

# 高风险动作类型：control_suggestion（设备控制建议）直接判 high，不可自动执行
HIGH_RISK_ACTION_TYPES = {"control_suggestion"}
# 控制类工具：涉及设备控制/批次冻结，一律 high，必须人工确认
CONTROL_TOOLS = {"control_device", "freeze_batch"}

# 禁令清单：写入方案中约束 LLM 与执行层，是系统"规则优先"的底线声明
PROHIBITIONS = [
    "不得直接输出设备控制指令",
    "不得在无人工确认时执行停机、启机、参数修改",
    "不得在无人工确认时冻结批次或修改工单状态",
    "不得在无知识依据时编造处置步骤",
]


def _escalate(level: RiskLevel) -> RiskLevel:
    """风险升级一级：low→medium→high，high 封顶（不会超过 high）。"""
    return {"low": "medium", "medium": "high", "high": "high"}[level]


class RiskClassifier:
    """阶段 7.3 风险分级：规则优先。

    给单个 ProposedAction 判定 risk_level，依据三层信息：
    - 动作类型 / 绑定工具（决定基础等级 base）；
    - 数据质量（partial/suspicious）、知识是否过期、是否存在冲突（任一触发 → 升级一级）；
    这是保守策略：只要有任何不确定性，就把风险往高了打，让执行层走更严格的路径。
    """

    def classify(
        self,
        action_type: str,
        *,
        tool_name: str | None = None,
        data_quality: str = "complete",
        knowledge_expired: bool = False,
        has_conflict: bool = False,
    ) -> RiskLevel:
        # 第一层：按动作类型/工具定基础等级
        if action_type in HIGH_RISK_ACTION_TYPES or (tool_name in CONTROL_TOOLS):
            # 控制建议 或 控制类工具 → 直接 high
            base: RiskLevel = "high"
        elif action_type in ("notify_user", "create_ticket_draft", "execute_safe_function"):
            # 通知/建草稿/安全函数 → medium（有副作用但可回退）
            base = "medium"
        else:
            # 其余（如 query_only 只读查询）→ low
            base = "low"

        # 第二层：数据/知识/冲突有问题 → 升级一级（保守降级）
        if data_quality in ("partial", "suspicious") or knowledge_expired or has_conflict:
            base = _escalate(base)
        return base


class PlanBuilder:
    """把多 Agent 结论汇总为可执行、可审计、可分级的方案。

    主图节点 8（build_resolution_plan）调用 build()，产出 ResolutionPlan。
    方案结构固定：先汇总证据/假设/知识引用/数据缺口 → 算置信度 → 按 incident_type
    生成动作清单 → 拼装最终方案。动作清单是"模板化"的：每类事件都有标准动作序列，
    保证同类事件产出一致、可回放。
    """

    def __init__(self, classifier: RiskClassifier | None = None) -> None:
        # 注入风险分级器，方便测试替换；默认用规则版
        self._classifier = classifier or RiskClassifier()

    def build(
        self,
        *,
        incident: Incident,
        findings: dict[str, AgentFinding],
        knowledge_hits: list[KnowledgeHit],
        data_quality: str = "complete",
        conflicts: list[str] | None = None,
    ) -> ResolutionPlan:
        """汇总各 agent 发现，生成处置方案。

        输入：事件 + 各 agent 结论 + 知识命中 + 数据质量 + 冲突列表。
        输出：ResolutionPlan，含问题概述、根因假设、证据、知识引用、动作清单、禁令、
              数据缺口、置信度等。
        """
        conflicts = conflicts or []
        # 只要有一条知识命中过期，整个方案的知识依据就标记为过期（保守）
        knowledge_expired = any(h.expired for h in knowledge_hits)

        evidence: list[dict] = []          # 汇总所有 agent 的证据
        knowledge_refs: list[dict] = []    # 知识命中引用（供方案溯源）
        data_gaps: list[str] = []          # 汇总所有 agent 的数据缺口
        hypotheses: list[dict] = []        # 汇总各 agent 的根因假设（带来源 agent 与置信度）

        # 遍历各 agent 结论，合并证据/数据缺口/假设
        for finding in findings.values():
            evidence.extend(finding.evidence)
            data_gaps.extend(finding.data_gaps)
            for hypothesis in finding.hypotheses:
                hypotheses.append(
                    {"hypothesis": hypothesis, "confidence": finding.confidence, "agent": finding.agent_name}
                )

        # 把知识命中转成轻量引用（只留溯源所需字段，不存全文）
        for hit in knowledge_hits:
            knowledge_refs.append(
                {
                    "chunk_id": hit.chunk.chunk_id,
                    "title": hit.chunk.title,
                    "version": hit.chunk.version,
                    "source_uri": hit.chunk.source_uri,
                    "expired": hit.expired,
                }
            )

        # 算保守置信度（取各维度的最小值再扣缺口惩罚）
        confidence = self._conservative_confidence(findings, evidence, knowledge_hits, data_gaps)

        # 按 incident_type 生成动作清单
        actions = self._build_actions(
            incident=incident,
            findings=findings,
            knowledge_hits=knowledge_hits,
            data_quality=data_quality,
            knowledge_expired=knowledge_expired,
            has_conflict=bool(conflicts),
        )

        summary = self._summarize(incident, findings)
        # 任一动作需要确认 → 整个方案标记为需确认（主图据此走人工确认路径）
        need_confirm = any(a.requires_human_confirm for a in actions)
        # 知识过期则给方案加有效性提示，提醒人工结论仅供参考
        validity_note = "存在过期知识依据，结论仅供参考" if knowledge_expired else None

        return ResolutionPlan(
            plan_id=f"plan-{uuid.uuid4().hex[:10]}",
            incident_id=incident.incident_id,
            problem_summary=summary,
            root_cause_hypotheses=hypotheses,
            evidence=evidence,
            knowledge_references=knowledge_refs,
            actions=actions,
            prohibitions=PROHIBITIONS,
            data_gaps=sorted(set(data_gaps)),  # 去重排序，避免同一缺口被多 agent 重复报
            need_human_confirm=need_confirm,
            confidence=confidence,
            validity_note=validity_note,
        )

    @staticmethod
    def _conservative_confidence(
        findings: dict[str, AgentFinding],
        evidence: list[dict],
        knowledge_hits: list[KnowledgeHit],
        data_gaps: list[str],
    ) -> float:
        """保守置信度：取多个维度的最小值，再扣数据缺口惩罚。

        维度：
        - agent_floor：所有 agent 置信度的最小值（木桶效应，最低的那个决定上限）；
        - evidence_score：证据充分度，4 条证据封顶 1.0；
        - knowledge_score：有效（非过期）知识命中数，2 条封顶 1.0；
        - gap_penalty：每个不同的数据缺口扣 0.1；
        最终取 min(agent_floor, evidence_score, knowledge_score) - gap_penalty，
        并 clamp 到 [0, 1]。无任何 agent 结论时直接返回 0.0。
        """
        if not findings:
            return 0.0
        agent_floor = min(f.confidence for f in findings.values())
        evidence_score = min(1.0, len(evidence) / 4.0)
        valid_knowledge = [h for h in knowledge_hits if not h.expired]
        knowledge_score = min(1.0, len(valid_knowledge) / 2.0)
        gap_penalty = 0.1 * len(set(data_gaps))
        value = min(agent_floor, evidence_score, knowledge_score) - gap_penalty
        return round(max(0.0, min(1.0, value)), 2)

    def _build_actions(
        self,
        *,
        incident: Incident,
        findings: dict[str, AgentFinding],
        knowledge_hits: list[KnowledgeHit],
        data_quality: str,
        knowledge_expired: bool,
        has_conflict: bool,
    ) -> list[ProposedAction]:
        """按 incident_type 生成模板化动作清单。

        通用动作（所有事件都有）：
        - act-1：复核关键状态数据（query_only，low，必可执行）——先确认数据时效性；
        然后按事件类型追加：
        - equipment_exception：建维护工单草稿 + 通知设备工程师 + 高风险确认（降速/停机）；
        - material/production_exception：通知计划员物料员 + 高风险确认（切换替代批次）；
        - quality_exception：通知质量工程师 + 高风险确认（冻结批次）；
        若没有匹配到任何类型（证据不足），追加一个"补充查询后重新评估"的低风险动作，
        保证方案至少有一个动作、不会空跑。
        """
        actions: list[ProposedAction] = []
        # 汇总所有 agent 的证据引用（供动作溯源），取前 5 条避免过长
        evidence_refs = [e.get("ref") for e in findings_evidence(findings) if e.get("ref")]

        # 通用动作 1：复核关键状态数据（只读，低风险，所有事件都先做这步）
        actions.append(
            ProposedAction(
                action_id="act-1",
                action_type="query_only",
                title="复核关键状态数据",
                description="重新查询设备状态、批次冻结状态与库存可用量，确认结论时效性",
                risk_level="low",
                requires_human_confirm=False,
                executable=True,
                evidence_refs=evidence_refs[:5],
                preconditions=["相关系统接口可用"],
                rollback_hint="只读操作，无需回滚",
            )
        )

        # 传给风险分级器的公共参数：数据质量差/知识过期/有冲突都会触发升级
        risk_common = {
            "data_quality": data_quality,
            "knowledge_expired": knowledge_expired,
            "has_conflict": has_conflict,
        }

        # —— 设备异常：建单 + 通知 + 高风险确认（降速/停机）——
        if incident.incident_type == "equipment_exception":
            actions.append(
                ProposedAction(
                    action_id="act-2",
                    action_type="create_ticket_draft",
                    title="创建设备维护工单草稿",
                    description="依据诊断结论与维护建议生成草稿，由设备工程师审核后转正式工单",
                    risk_level=self._classifier.classify("create_ticket_draft", **risk_common),
                    requires_human_confirm=False,
                    executable=bool(knowledge_hits),  # 有知识依据才可执行（无依据不编造）
                    tool_name="create_ticket_draft",
                    tool_args={
                        "incident_id": incident.incident_id,
                        "device_id": incident.entity_refs.get("device_id"),
                        "title": "设备异常维护",
                    },
                    preconditions=["存在可用知识依据", "设备标识存在"],
                    rollback_hint="草稿可删除，不影响生产工单",
                    evidence_refs=evidence_refs[:5],
                )
            )
            actions.append(
                ProposedAction(
                    action_id="act-3",
                    action_type="notify_user",
                    title="通知设备工程师",
                    description="将诊断结论与维护建议推送给当班设备工程师",
                    risk_level=self._classifier.classify("notify_user", **risk_common),
                    requires_human_confirm=False,
                    executable=True,
                    tool_name="send_notification",
                    tool_args={
                        "incident_id": incident.incident_id,
                        "role": "maintenance",
                        "message": "设备异常诊断结论已生成，请复核",
                    },
                    preconditions=["通知渠道可用"],
                    rollback_hint="通知类动作无需回滚",
                    evidence_refs=evidence_refs[:3],
                )
            )
            # 高风险动作：降速/停机 —— requires_human_confirm=True, executable=False
            # 意味着系统只生成确认请求，绝不自动执行，必须人工批准
            actions.append(
                ProposedAction(
                    action_id="act-4",
                    action_type="request_human_confirm",
                    title="确认是否降速或停机检查",
                    description="若温度或振动继续上升，需设备主管确认后执行降速/停机",
                    risk_level="high",
                    requires_human_confirm=True,
                    executable=False,
                    tool_name="control_device",
                    tool_args={
                        "device_id": incident.entity_refs.get("device_id"),
                        "command": "reduce_speed_or_stop",
                    },
                    preconditions=["设备主管在线", "现场安全确认"],
                    rollback_hint="停机后需按 SOP 重新升温启动",
                    evidence_refs=evidence_refs[:5],
                )
            )

        # —— 物料/生产异常：通知 + 高风险确认（切换替代批次）——
        if incident.incident_type in ("material_exception", "production_exception"):
            actions.append(
                ProposedAction(
                    action_id="act-2",
                    action_type="notify_user",
                    title="通知计划员与物料员",
                    description="推送缺料判断与影响范围，触发补料或替代料评估",
                    risk_level=self._classifier.classify("notify_user", **risk_common),
                    requires_human_confirm=False,
                    executable=True,
                    tool_name="send_notification",
                    tool_args={
                        "incident_id": incident.incident_id,
                        "role": "planner",
                        "message": "工单疑似缺料，请确认补料或替代料",
                    },
                    preconditions=["通知渠道可用"],
                    rollback_hint="通知类动作无需回滚",
                    evidence_refs=evidence_refs[:3],
                )
            )
            actions.append(
                ProposedAction(
                    action_id="act-3",
                    action_type="request_human_confirm",
                    title="确认是否切换替代批次",
                    description="替代批次或替代料需工艺与质量双确认，系统不自动执行",
                    risk_level="high",
                    requires_human_confirm=True,
                    executable=False,
                    tool_name="freeze_batch",
                    tool_args={"batch_id": incident.entity_refs.get("batch_id")},
                    preconditions=["工艺员与质量员确认"],
                    rollback_hint="切换后需重新首件确认",
                    evidence_refs=evidence_refs[:5],
                )
            )

        # —— 质量异常：通知 + 高风险确认（冻结批次）——
        if incident.incident_type == "quality_exception":
            actions.append(
                ProposedAction(
                    action_id="act-2",
                    action_type="notify_user",
                    title="通知质量工程师",
                    description="推送不良与追溯链结论，启动隔离评估",
                    risk_level=self._classifier.classify("notify_user", **risk_common),
                    requires_human_confirm=False,
                    executable=True,
                    tool_name="send_notification",
                    tool_args={
                        "incident_id": incident.incident_id,
                        "role": "quality",
                        "message": "批次不良已追溯，请评估隔离范围",
                    },
                    preconditions=["通知渠道可用"],
                    rollback_hint="通知类动作无需回滚",
                    evidence_refs=evidence_refs[:3],
                )
            )
            actions.append(
                ProposedAction(
                    action_id="act-3",
                    action_type="request_human_confirm",
                    title="确认是否冻结批次",
                    description="批次冻结必须由质量负责人审批，系统仅生成确认请求",
                    risk_level="high",
                    requires_human_confirm=True,
                    executable=False,
                    tool_name="freeze_batch",
                    tool_args={"batch_id": incident.entity_refs.get("batch_id")},
                    preconditions=["质量负责人审批", "隔离标识已挂"],
                    rollback_hint="解冻需质量负责人二次审批",
                    evidence_refs=evidence_refs[:5],
                )
            )

        # 兜底：没有匹配到任何事件类型时，追加一个"补充查询"动作，保证方案非空
        if not actions:
            actions.append(
                ProposedAction(
                    action_id="act-1",
                    action_type="query_only",
                    title="补充查询后重新评估",
                    description="当前证据不足，先补齐关键数据再生成处置方案",
                    risk_level="low",
                    requires_human_confirm=False,
                    executable=True,
                    evidence_refs=[],
                    preconditions=[],
                    rollback_hint="只读操作，无需回滚",
                )
            )
        return actions

    @staticmethod
    def _summarize(incident: Incident, findings: dict[str, AgentFinding]) -> str:
        """生成问题概述：取主结论 agent（排除知识检索/控制建议）的 summary 作为概述。

        若没有主结论 agent（全是知识检索或控制建议），返回"证据不足"提示。
        前缀带 [incident_type] 便于一眼识别事件类别。
        """
        primary = next(
            (f for f in findings.values() if f.agent_name not in ("knowledge_retrieval", "control_suggestion")),
            None,
        )
        prefix = f"[{incident.incident_type}] "
        if primary is None:
            return prefix + "证据不足，未形成明确结论"
        return prefix + primary.summary


def findings_evidence(findings: dict[str, AgentFinding]) -> list[dict]:
    """汇总所有 agent 的证据列表（模块级工具函数，供 _build_actions 取证据引用）。"""
    items: list[dict] = []
    for finding in findings.values():
        items.extend(finding.evidence)
    return items


class PlanValidator:
    """阶段 7.4 方案校验清单。

    主图节点 9（validate_plan）调用 validate()，是"方案层的独立闸"——在方案交给执行层
    之前，做最后一道确定性校验，发现问题就地修正（标记 requires_human_confirm / 置
    executable=False / 下调置信度），并把问题写进 validation_issues 供最终结论引用。

    校验项：
    1. 综合置信度 < 0.3 → LOW_CONFIDENCE（提示转人工，但不阻断）；
    2. high 风险动作没标 requires_human_confirm → 自动补上标记并置不可执行；
    3. 可执行动作缺前置条件 → 置不可执行；
    4. control_suggestion 被标可执行 → 强制置不可执行（控制建议只能提示，不能执行）；
    5. 控制类工具（control_device/freeze_batch）被标可执行 → 强制置不可执行；
    6. 非只读动作缺证据引用 → NO_EVIDENCE（提示，不阻断）；
    7. 有数据缺口但置信度 > 0.6 → 下调到 0.6 并标记风险升级。

    其中 2 和 5 是硬伤（ok=False），其余是告警（ok 仍为 True 但 issues 非空）。
    """

    def validate(self, plan: ResolutionPlan) -> PlanValidationResult:
        issues: list[PlanValidationIssue] = []
        risk_escalated = False  # 是否触发了风险升级（如置信度下调）

        # 校验 1：综合置信度过低 → 建议转人工（不阻断执行，只告警）
        if plan.confidence < 0.3:
            issues.append(
                PlanValidationIssue(code="LOW_CONFIDENCE", message="综合置信度过低，建议转人工")
            )

        # 遍历每个动作做校验
        for action in plan.actions:
            # 校验 2：高风险动作必须有人工确认标记，缺了就补上并置不可执行（硬伤）
            if action.risk_level == "high" and not action.requires_human_confirm:
                issues.append(
                    PlanValidationIssue(
                        code="HIGH_RISK_NO_CONFIRM",
                        message="高风险动作缺少人工确认标记",
                        action_id=action.action_id,
                    )
                )
                action.requires_human_confirm = True
                action.executable = False

            # 校验 3：可执行的非只读动作必须有前置条件，缺了就置不可执行
            if action.executable and not action.preconditions and action.action_type != "query_only":
                issues.append(
                    PlanValidationIssue(
                        code="MISSING_PRECONDITION",
                        message="可执行动作缺少前置条件",
                        action_id=action.action_id,
                    )
                )
                action.executable = False

            # 校验 4：control_suggestion（控制建议）绝不能标记为可执行
            if action.action_type == "control_suggestion" and action.executable:
                issues.append(
                    PlanValidationIssue(
                        code="CONTROL_EXECUTABLE",
                        message="控制建议不得标记为可执行",
                        action_id=action.action_id,
                    )
                )
                action.executable = False

            # 校验 5：控制类工具（control_device/freeze_batch）绝不能直接执行（硬伤）
            if action.tool_name in CONTROL_TOOLS and action.executable:
                issues.append(
                    PlanValidationIssue(
                        code="CONTROL_TOOL_EXECUTABLE",
                        message="高风险工具不得直接执行",
                        action_id=action.action_id,
                    )
                )
                action.executable = False

            # 校验 6：非只读动作必须有证据引用（告警，不阻断）
            if not action.evidence_refs and action.action_type != "query_only":
                issues.append(
                    PlanValidationIssue(
                        code="NO_EVIDENCE",
                        message="动作缺少证据引用",
                        action_id=action.action_id,
                    )
                )

        # 校验 7：有数据缺口但置信度偏高 → 下调置信度并标记风险升级
        if plan.data_gaps and plan.confidence > 0.6:
            issues.append(
                PlanValidationIssue(
                    code="CONFIDENCE_MISMATCH",
                    message="存在数据缺口但置信度偏高，已下调",
                )
            )
            plan.confidence = 0.6
            risk_escalated = True

        # 重新计算方案的 need_human_confirm（校验可能新增了确认标记）
        plan.need_human_confirm = any(a.requires_human_confirm for a in plan.actions)

        # ok 判定：出现 HIGH_RISK_NO_CONFIRM 或 CONTROL_TOOL_EXECUTABLE 这两类硬伤则 ok=False，
        # 其余告警不影响 ok（方案仍可执行低/中风险动作，但 issues 会展示给人工）
        return PlanValidationResult(
            ok=not any(i.code in ("HIGH_RISK_NO_CONFIRM", "CONTROL_TOOL_EXECUTABLE") for i in issues),
            issues=issues,
            risk_escalated=risk_escalated,
            confidence_adjusted=risk_escalated,
        )