"""阶段 5.2 专业子 Agent 实现（规则兜底 + 可选 LLM 结构化增强）。

本文件是 13 类智能体的"业务实现层"：每个类继承 BaseAgent，只重写 _analyze 方法，
完成各自领域的取证分析，输出统一的 AgentFinding。

通用模式（每个 _analyze 都遵循）：
1. 从 ctx.incident.entity_refs 取所需输入字段（device_id / batch_id 等），缺则返回 data_gaps；
2. 通过 self._call 调工具取数（走九步检查链），结果自动缓存进 ctx.tool_results；
3. 调 self._llm_finding 尝试 LLM 增强：有结果直接返回，无结果继续走规则；
4. 用确定性规则把取到的数据拼成 findings / hypotheses / evidence / confidence；
5. 始终声明 risk_notes（安全约束），智能体本身只输出诊断/建议，不执行控制动作。

类与 catalog 编号的对应关系见 factory.py 的 CODE_AGENT_CLASS；
EquipmentDiagnosisAgent 等 7 个" legacy "类仅在 __init__.AGENT_REGISTRY 中保留兼容，
主图编排实际走 A1–A5 / B1–B5 / C1–C3 编号体系。
"""

from __future__ import annotations

from typing import Any

# ---- 同模块：共享上下文 + 基类（提供 _call / _llm_finding / 审计）----
from industrial_agent.agents.base import AgentContext, BaseAgent
# ---- 模型层：所有智能体的统一输出结构 ----
from industrial_agent.models.findings import AgentFinding


# ==================== 兼容遗留类（仅 AGENT_REGISTRY 引用，主图不直接调度）====================


class EquipmentDiagnosisAgent(BaseAgent):
    """设备诊断：基于设备状态、报警、趋势形成故障假设与证据。"""

    name = "equipment_diagnosis"
    domain = "设备诊断"

    def _analyze(self, ctx: AgentContext) -> AgentFinding:
        refs = ctx.incident.entity_refs
        device_id = refs.get("device_id")
        data_gaps: list[str] = []
        evidence: list[dict[str, Any]] = []
        hypotheses: list[str] = []
        findings: list[str] = []

        if not device_id:
            data_gaps.append("事件未提供 device_id，无法查询设备状态")
            return AgentFinding(
                agent_name=self.name,
                summary="缺少设备标识，无法进行设备诊断",
                data_gaps=data_gaps,
                confidence=0.0,
                risk_notes=["证据不足，建议人工确认设备归属"],
            )

        status = self._call(ctx, "query_device_status", device_id=device_id)
        alarms = self._call(ctx, "query_recent_alarms", device_id=device_id, hours=24)

        status_data = status.data if status.ok and isinstance(status.data, dict) else None
        if status_data is None:
            data_gaps.append("设备状态查询失败或为空")
        else:
            evidence.append({"ref": f"device:{device_id}", "detail": status_data})
            temperature = status_data.get("temperature")
            vibration = status_data.get("vibration")
            if isinstance(temperature, int | float) and temperature > 75:
                hypotheses.append("主轴温度超过阈值，可能由冷却不足或负载过高导致")
                findings.append(f"设备温度 {temperature}℃，超过 75℃ 阈值")
            if isinstance(vibration, int | float) and vibration > 3.0:
                hypotheses.append("振动值偏高，可能存在刀具磨损或轴承问题")
                findings.append(f"设备振动 {vibration}mm/s，超过 3.0 阈值")
            if status_data.get("online") is False:
                hypotheses.append("设备离线，优先排查通讯与供电")
                findings.append("设备当前不在线")

        if alarms.ok and isinstance(alarms.data, list) and alarms.data:
            evidence.append({"ref": "alarms", "detail": alarms.data})
            codes = [a.get("alarm_code") for a in alarms.data if isinstance(a, dict)]
            findings.append(f"最近报警：{', '.join(str(c) for c in codes if c)}")
        elif not alarms.ok:
            data_gaps.append(f"报警查询失败：{alarms.error_code}")

        if not findings:
            data_gaps.append("未获取到可支撑诊断的设备数据")

        llm_finding = self._llm_finding(ctx)
        if llm_finding is not None:
            return llm_finding

        confidence = 0.0
        if evidence:
            confidence = min(0.9, 0.45 + 0.15 * len(evidence) + 0.1 * len(hypotheses))
        if data_gaps:
            confidence = max(0.0, confidence - 0.2)

        return AgentFinding(
            agent_name=self.name,
            summary="；".join(findings) if findings else "设备数据不足，未形成有效结论",
            findings=findings,
            hypotheses=hypotheses,
            evidence=evidence,
            knowledge_refs=[],
            confidence=round(confidence, 2),
            data_gaps=data_gaps,
            suggested_next_steps=["复核冷却系统与刀具状态", "查看同型号设备历史故障"],
            risk_notes=["本 Agent 仅做诊断，不输出任何控制指令"],
        )


class MaintenanceAdviceAgent(BaseAgent):
    """维护建议：结合知识库给出维护步骤、所需资源与风险。"""

    name = "maintenance_advice"
    domain = "维护建议"

    def _analyze(self, ctx: AgentContext) -> AgentFinding:
        hits = ctx.knowledge_hits
        if not hits:
            return AgentFinding(
                agent_name=self.name,
                summary="未检索到可用维护依据",
                data_gaps=["知识库无命中"],
                confidence=0.0,
                risk_notes=["无标准依据时禁止自动执行任何维护动作"],
                suggested_next_steps=["转设备工程师人工判断"],
            )

        llm_finding = self._llm_finding(ctx)
        if llm_finding is not None:
            return llm_finding

        refs = [h.chunk.chunk_id for h in hits]
        evidence = [
            {"ref": h.chunk.chunk_id, "detail": {"source": h.chunk.source_uri, "score": h.score}}
            for h in hits
        ]
        steps = [h.chunk.text for h in hits[:2]]
        expired = [h.chunk.chunk_id for h in hits if h.expired]

        data_gaps = []
        risk_notes = ["维护动作涉及设备状态变更时，必须人工确认后执行"]
        if expired:
            data_gaps.append(f"部分依据已过期：{', '.join(expired)}")
            risk_notes.append("依据可能过期，建议人工确认")

        return AgentFinding(
            agent_name=self.name,
            summary=f"基于 {len(hits)} 条知识给出维护建议",
            findings=[h.chunk.title for h in hits],
            hypotheses=[],
            evidence=evidence,
            knowledge_refs=refs,
            confidence=round(min(0.85, 0.4 + 0.1 * len(hits) - 0.15 * len(expired)), 2),
            data_gaps=data_gaps,
            suggested_next_steps=steps,
            risk_notes=risk_notes,
        )


class KnowledgeRetrievalAgent(BaseAgent):
    """知识检索：返回带来源与版本的知识片段。"""

    name = "knowledge_retrieval"
    domain = "知识检索"

    def _analyze(self, ctx: AgentContext) -> AgentFinding:
        hits = ctx.knowledge_hits
        if not hits:
            return AgentFinding(
                agent_name=self.name,
                summary="无可靠依据",
                data_gaps=["知识库未命中相关 SOP 或案例"],
                confidence=0.0,
                risk_notes=["禁止在无依据情况下生成具体处置方案"],
            )
        return AgentFinding(
            agent_name=self.name,
            summary=f"命中 {len(hits)} 条知识片段",
            findings=[f"{h.chunk.title} / {h.chunk.section_path}" for h in hits],
            evidence=[
                {
                    "ref": h.chunk.chunk_id,
                    "detail": {
                        "version": h.chunk.version,
                        "source": h.chunk.source_uri,
                        "expired": h.expired,
                        "reasons": h.reasons,
                    },
                }
                for h in hits
            ],
            knowledge_refs=[h.chunk.chunk_id for h in hits],
            confidence=round(min(0.9, 0.35 + 0.1 * len(hits)), 2),
            data_gaps=["部分知识可能过期"] if any(h.expired for h in hits) else [],
        )


class ProductionExceptionAgent(BaseAgent):
    """生产异常：分析工单阻塞与影响范围。"""

    name = "production_exception"
    domain = "生产异常"
    domain_tools = ("query_work_order",)

    def _analyze(self, ctx: AgentContext) -> AgentFinding:
        work_order_id = ctx.incident.entity_refs.get("work_order_id")
        if not work_order_id:
            return AgentFinding(
                agent_name=self.name,
                summary="事件未提供工单号，无法分析生产影响",
                data_gaps=["缺少 work_order_id"],
                confidence=0.0,
            )

        result = self._call(ctx, "query_work_order", work_order_id=work_order_id)
        if not result.ok or not isinstance(result.data, dict):
            return AgentFinding(
                agent_name=self.name,
                summary="工单查询失败",
                data_gaps=[f"工单查询失败：{result.error_code}"],
                confidence=0.0,
                risk_notes=["数据不可用，方案需降级"],
            )

        llm_finding = self._llm_finding(ctx)
        if llm_finding is not None:
            return llm_finding

        wo = result.data
        findings = [
            f"工单 {wo.get('work_order_id')} 状态 {wo.get('status')}，阻塞原因：{wo.get('blocked_reason')}",
        ]
        planned = wo.get("planned_qty") or 0
        completed = wo.get("completed_qty") or 0
        remaining = max(int(planned) - int(completed), 0)
        findings.append(f"剩余在制 {remaining} 件，产线 {wo.get('line_id')} 工序 {wo.get('process_id')}")

        return AgentFinding(
            agent_name=self.name,
            summary=findings[0],
            findings=findings,
            evidence=[{"ref": f"wo:{work_order_id}", "detail": wo}],
            confidence=0.6 if wo.get("status") == "blocked" else 0.4,
            data_gaps=[],
            suggested_next_steps=["确认缺料或质量冻结是否为阻塞根因"],
        )


class MaterialInventoryAgent(BaseAgent):
    """物料库存：判断缺料、可用量与替代料。"""

    name = "material_inventory"
    domain = "物料库存"

    def _analyze(self, ctx: AgentContext) -> AgentFinding:
        material_id = ctx.incident.entity_refs.get("material_id")
        batch_id = ctx.incident.entity_refs.get("batch_id")
        data_gaps: list[str] = []
        evidence: list[dict[str, Any]] = []
        findings: list[str] = []
        hypotheses: list[str] = []

        if material_id:
            stock = self._call(ctx, "query_material_stock", material_id=material_id)
            if stock.ok and isinstance(stock.data, dict):
                evidence.append({"ref": f"stock:{material_id}", "detail": stock.data})
                available = stock.data.get("available_qty", 0)
                safety = stock.data.get("safety_stock", 0)
                findings.append(f"物料 {material_id} 可用量 {available}，安全库存 {safety}")
                if available < safety:
                    hypotheses.append("可用库存低于安全库存，存在缺料风险")
            else:
                data_gaps.append(f"库存查询失败：{stock.error_code}")
        else:
            data_gaps.append("事件未提供 material_id")

        if batch_id:
            batch = self._call(ctx, "query_batch", batch_id=batch_id)
            if batch.ok and isinstance(batch.data, dict):
                evidence.append({"ref": f"batch:{batch_id}", "detail": batch.data})
                if batch.data.get("frozen"):
                    findings.append(f"批次 {batch_id} 处于冻结状态：{batch.data.get('freeze_reason')}")
                    hypotheses.append("批次冻结导致可用量归零")
            else:
                data_gaps.append(f"批次查询失败：{batch.error_code}")

        llm_finding = self._llm_finding(ctx)
        if llm_finding is not None:
            return llm_finding

        if not findings:
            return AgentFinding(
                agent_name=self.name,
                summary="缺少物料与批次信息，无法判断缺料",
                data_gaps=data_gaps,
                confidence=0.0,
            )

        confidence = min(0.85, 0.4 + 0.15 * len(evidence)) - (0.15 if data_gaps else 0.0)
        return AgentFinding(
            agent_name=self.name,
            summary="；".join(findings),
            findings=findings,
            hypotheses=hypotheses,
            evidence=evidence,
            confidence=round(max(confidence, 0.0), 2),
            data_gaps=data_gaps,
            suggested_next_steps=["确认是否存在已认证替代料", "复核冻结批次范围"],
        )


# A3（兼 legacy quality_trace）：第一版启用，按 batch_id 查检验记录与追溯链
class QualityTraceAgent(BaseAgent):
    """质量追溯：查询检验结果、不良代码与追溯链。"""

    name = "quality_trace"
    domain = "质量追溯"

    def _analyze(self, ctx: AgentContext) -> AgentFinding:
        batch_id = ctx.incident.entity_refs.get("batch_id")
        if not batch_id:
            return AgentFinding(
                agent_name=self.name,
                summary="事件未提供批次号，无法追溯",
                data_gaps=["缺少 batch_id"],
                confidence=0.0,
            )

        result = self._call(ctx, "query_quality_record", batch_id=batch_id)
        if not result.ok or not isinstance(result.data, dict):
            return AgentFinding(
                agent_name=self.name,
                summary="检验记录查询失败",
                data_gaps=[f"检验记录查询失败：{result.error_code}"],
                confidence=0.0,
            )

        llm_finding = self._llm_finding(ctx)
        if llm_finding is not None:
            return llm_finding

        record = result.data
        findings = [
            f"批次 {batch_id} 检验结果 {record.get('inspection_result')}，不良代码 {record.get('defect_code')}",
        ]
        chain = record.get("trace_chain") or []
        if chain:
            findings.append(f"追溯链工序：{' -> '.join(str(c) for c in chain)}")
        affected = record.get("affected_batches") or []
        if affected:
            findings.append(f"关联批次 {len(affected)} 个，需评估隔离范围")

        return AgentFinding(
            agent_name=self.name,
            summary=findings[0],
            findings=findings,
            evidence=[{"ref": f"qms:{batch_id}", "detail": record}],
            confidence=0.65 if record.get("inspection_result") == "NG" else 0.4,
            suggested_next_steps=["评估关联批次隔离范围", "确认冻结需质量负责人审批"],
            risk_notes=["批次冻结属高风险动作，禁止自动执行"],
        )


class ControlSuggestionAgent(BaseAgent):
    """控制建议：只输出建议与风险，永不执行控制。"""

    name = "control_suggestion"
    domain = "控制建议"

    def _analyze(self, ctx: AgentContext) -> AgentFinding:
        hypotheses = ctx.incident.entity_refs
        suggestions: list[str] = []
        if ctx.incident.incident_type == "equipment_exception":
            suggestions = ["若温度持续上升，建议降速运行并申请停机检查"]
        elif ctx.incident.incident_type == "quality_exception":
            suggestions = ["建议隔离当前批次并提交质量负责人确认冻结范围"]
        elif ctx.incident.incident_type == "material_exception":
            suggestions = ["建议核对替代料认证状态，由工艺员确认后再切换"]

        return AgentFinding(
            agent_name=self.name,
            summary="仅输出控制建议，不执行任何控制动作",
            findings=[f"事件类型 {ctx.incident.incident_type}"] if hypotheses else [],
            hypotheses=suggestions,
            confidence=0.3,
            risk_notes=[
                "本 Agent 无控制工具权限",
                "任何停机、改参数、冻结批次动作均需人工确认",
            ],
        )


# ==================== catalog 主图调度类（A1–A5 / B1–B5，factory.CODE_AGENT_CLASS 注册）====================


# B3：第一版主分析脑，跨设备/工单/物料/批次/质量取全局证据；factory 未知编号的回落目标
class ControlCenterAgent(BaseAgent):
    """B3 智能管控大模型服务：主分析脑，跨设备/工单/物料/批次/质量取证据。"""

    name = "control_center"
    domain = "智能管控"

    def _analyze(self, ctx: AgentContext) -> AgentFinding:
        refs = ctx.incident.entity_refs
        evidence: list[dict[str, Any]] = []
        findings: list[str] = []
        hypotheses: list[str] = []
        data_gaps: list[str] = []

        device_id = refs.get("device_id")
        if device_id:
            status = self._call(ctx, "query_device_status", device_id=device_id)
            if status.ok and isinstance(status.data, dict):
                evidence.append({"ref": f"device:{device_id}", "detail": status.data})
                findings.append(f"设备 {device_id} 状态 {status.data.get('status')}")
            else:
                data_gaps.append(f"设备状态查询失败：{status.error_code}")
            alarms = self._call(ctx, "query_recent_alarms", device_id=device_id, hours=24)
            if alarms.ok and isinstance(alarms.data, list) and alarms.data:
                evidence.append({"ref": "alarms", "detail": alarms.data})
                codes = [a.get("alarm_code") for a in alarms.data if isinstance(a, dict)]
                findings.append(f"近 24h 报警：{', '.join(str(c) for c in codes if c) or '无'}")

        work_order_id = refs.get("work_order_id")
        if work_order_id:
            wo = self._call(ctx, "query_work_order", work_order_id=work_order_id)
            if wo.ok and isinstance(wo.data, dict):
                evidence.append({"ref": f"wo:{work_order_id}", "detail": wo.data})
                findings.append(
                    f"工单 {work_order_id} 状态 {wo.data.get('status')}，"
                    f"阻塞原因 {wo.data.get('blocked_reason')}"
                )
                if wo.data.get("status") == "blocked":
                    hypotheses.append("工单阻塞可能由缺料、质量冻结或设备故障导致")
            else:
                data_gaps.append(f"工单查询失败：{wo.error_code}")

        material_id = refs.get("material_id")
        if material_id:
            stock = self._call(ctx, "query_material_stock", material_id=material_id)
            if stock.ok and isinstance(stock.data, dict):
                evidence.append({"ref": f"stock:{material_id}", "detail": stock.data})
                available = stock.data.get("available_qty", 0)
                safety = stock.data.get("safety_stock", 0)
                if available < safety:
                    hypotheses.append("物料可用量低于安全库存，疑似缺料")
                    findings.append(f"物料 {material_id} 可用量 {available} 低于安全库存 {safety}")

        batch_id = refs.get("batch_id")
        if batch_id:
            batch = self._call(ctx, "query_batch", batch_id=batch_id)
            if batch.ok and isinstance(batch.data, dict):
                evidence.append({"ref": f"batch:{batch_id}", "detail": batch.data})
                if batch.data.get("frozen"):
                    hypotheses.append("批次冻结可能是阻塞或不良的根因")
                    findings.append(f"批次 {batch_id} 已冻结：{batch.data.get('freeze_reason')}")
            quality = self._call(ctx, "query_quality_record", batch_id=batch_id)
            if quality.ok and isinstance(quality.data, dict):
                evidence.append({"ref": f"qms:{batch_id}", "detail": quality.data})
                if quality.data.get("inspection_result") == "NG":
                    hypotheses.append("存在不良记录，需追溯关联批次")

        llm_finding = self._llm_finding(ctx)
        if llm_finding is not None:
            return llm_finding

        if not evidence:
            data_gaps.append("未获取到任何业务系统证据，结论仅供参考")

        confidence = 0.0
        if evidence:
            confidence = min(0.9, 0.4 + 0.1 * len(evidence) + 0.1 * len(hypotheses))
        if data_gaps:
            confidence = max(0.0, confidence - 0.15 * len(data_gaps))

        return AgentFinding(
            agent_name=self.name,
            summary="；".join(findings) if findings else "证据不足，未形成管控结论",
            findings=findings,
            hypotheses=hypotheses,
            evidence=evidence,
            confidence=round(confidence, 2),
            data_gaps=data_gaps,
            suggested_next_steps=["补齐关键字段后重跑", "交叉验证设备与质量侧结论"],
            risk_notes=["B3 仅做分析，不直接下发控制指令"],
        )


# A1：第一版只读（catalog.first_release_readonly=True），写工具 bind_codes/load_esop 被注册表拦截
class StationAssemblyAgent(BaseAgent):
    """A1 工位装配智能体：第一版仅只读取证，不触发装配履历落库。"""

    name = "station_assembly"
    domain = "工位装配"

    def _analyze(self, ctx: AgentContext) -> AgentFinding:
        refs = ctx.incident.entity_refs
        evidence: list[dict[str, Any]] = []
        findings: list[str] = []
        data_gaps: list[str] = []

        work_order_id = refs.get("work_order_id")
        if work_order_id:
            wo = self._call(ctx, "query_work_order", work_order_id=work_order_id)
            if wo.ok and isinstance(wo.data, dict):
                evidence.append({"ref": f"wo:{work_order_id}", "detail": wo.data})
                findings.append(f"工位工单 {work_order_id} 工序 {wo.data.get('process_id')}")
            else:
                data_gaps.append(f"工位工单查询失败：{wo.error_code}")

        device_id = refs.get("device_id")
        if device_id:
            status = self._call(ctx, "query_device_status", device_id=device_id)
            if status.ok and isinstance(status.data, dict):
                evidence.append({"ref": f"device:{device_id}", "detail": status.data})

        for key in ("material_id", "batch_id"):
            value = refs.get(key)
            if not value:
                continue
            result = self._call(ctx, "query_material_stock" if key == "material_id" else "query_batch", **{key: value})
            if result.ok and isinstance(result.data, dict):
                evidence.append({"ref": f"{key}:{value}", "detail": result.data})

        llm_finding = self._llm_finding(ctx)
        if llm_finding is not None:
            return llm_finding

        return AgentFinding(
            agent_name=self.name,
            summary="；".join(findings) if findings else "工位装配侧仅完成取证",
            findings=findings,
            evidence=evidence,
            confidence=round(min(0.8, 0.3 + 0.1 * len(evidence)), 2),
            data_gaps=data_gaps,
            suggested_next_steps=["核对 E-SOP 工艺版本", "确认码批绑定关系"],
            risk_notes=["A1 第一版为只读取证，E-SOP 加载与码绑定写入已被禁用"],
        )


# B1：第一版只读，不自动加载 E-SOP、不写入装配履历，仅做工艺问答依据检索
class AssemblyEntryAgent(BaseAgent):
    """B1 装配智能体入口：工艺问答与 E-SOP 检索，第一版只读。"""

    name = "assembly_entry"
    domain = "装配入口"

    def _analyze(self, ctx: AgentContext) -> AgentFinding:
        refs = ctx.incident.entity_refs
        work_order_id = refs.get("work_order_id")
        evidence: list[dict[str, Any]] = []
        findings: list[str] = []
        data_gaps: list[str] = []

        if work_order_id:
            wo = self._call(ctx, "query_work_order", work_order_id=work_order_id)
            if wo.ok and isinstance(wo.data, dict):
                evidence.append({"ref": f"wo:{work_order_id}", "detail": wo.data})
                findings.append(f"产品 {wo.data.get('product_code')} 工艺 {wo.data.get('process_id')}")
            else:
                data_gaps.append(f"工单查询失败：{wo.error_code}")

        if ctx.knowledge_hits:
            refs_k = [h.chunk.chunk_id for h in ctx.knowledge_hits]
            evidence.append({"ref": "esop_knowledge", "detail": {"chunks": refs_k}})
            findings.append(f"命中工艺知识 {len(ctx.knowledge_hits)} 条，可用于工艺问答")

        llm_finding = self._llm_finding(ctx)
        if llm_finding is not None:
            return llm_finding

        return AgentFinding(
            agent_name=self.name,
            summary="；".join(findings) if findings else "装配入口侧未检索到工艺依据",
            findings=findings,
            evidence=evidence,
            knowledge_refs=[h.chunk.chunk_id for h in ctx.knowledge_hits],
            confidence=round(min(0.85, 0.35 + 0.1 * len(ctx.knowledge_hits)), 2),
            data_gaps=data_gaps or (["未命中工艺知识"] if not ctx.knowledge_hits else []),
            suggested_next_steps=["核对工艺版本有效性", "由工艺员确认后加载 E-SOP"],
            risk_notes=["B1 第一版为只读取证，不自动加载 E-SOP、不写入装配履历"],
        )


# B4：第一版启用，服务级追溯报告，评估关联批次隔离范围（冻结属高风险，仅建议）
class QualityTraceServiceAgent(BaseAgent):
    """B4 质量追溯智能体服务：服务级追溯报告与根因辅助。"""

    name = "quality_trace_service"
    domain = "质量追溯服务"

    def _analyze(self, ctx: AgentContext) -> AgentFinding:
        refs = ctx.incident.entity_refs
        batch_id = refs.get("batch_id") or refs.get("product_code")
        if not batch_id:
            return AgentFinding(
                agent_name=self.name,
                summary="缺少批次/产品标识，无法生成追溯报告",
                data_gaps=["缺少 batch_id 或 product_code"],
                confidence=0.0,
                risk_notes=["无追溯主键时禁止生成处置结论"],
            )

        evidence: list[dict[str, Any]] = []
        findings: list[str] = []
        hypotheses: list[str] = []
        data_gaps: list[str] = []

        quality = self._call(ctx, "query_quality_record", batch_id=batch_id)
        if quality.ok and isinstance(quality.data, dict):
            evidence.append({"ref": f"qms:{batch_id}", "detail": quality.data})
            findings.append(
                f"批次 {batch_id} 检验 {quality.data.get('inspection_result')}，"
                f"不良代码 {quality.data.get('defect_code')}"
            )
            chain = quality.data.get("trace_chain") or []
            if chain:
                findings.append(f"追溯链：{' -> '.join(str(c) for c in chain)}")
            affected = quality.data.get("affected_batches") or []
            if affected:
                hypotheses.append(f"关联批次 {len(affected)} 个存在扩散风险")
        else:
            data_gaps.append(f"质量记录查询失败：{quality.error_code}")

        batch = self._call(ctx, "query_batch", batch_id=batch_id)
        if batch.ok and isinstance(batch.data, dict):
            evidence.append({"ref": f"batch:{batch_id}", "detail": batch.data})
            if batch.data.get("frozen"):
                findings.append(f"批次 {batch_id} 当前已冻结")

        llm_finding = self._llm_finding(ctx)
        if llm_finding is not None:
            return llm_finding

        return AgentFinding(
            agent_name=self.name,
            summary="；".join(findings) if findings else "未取到质量追溯数据",
            findings=findings,
            hypotheses=hypotheses,
            evidence=evidence,
            confidence=round(min(0.85, 0.45 + 0.1 * len(evidence)), 2),
            data_gaps=data_gaps,
            suggested_next_steps=["评估关联批次隔离范围", "冻结动作须质量负责人审批"],
            risk_notes=["批次冻结属高风险，禁止自动执行"],
        )


# A2：第一版暂未启用（first_release=False）；缺料判断 + 配送路径建议，路径调整属高风险
class MaterialDeliveryAgent(BaseAgent):
    """A2 物料配送智能体：缺料判断与配送建议。"""

    name = "material_delivery"
    domain = "物料配送"

    def _analyze(self, ctx: AgentContext) -> AgentFinding:
        refs = ctx.incident.entity_refs
        material_id = refs.get("material_id")
        evidence: list[dict[str, Any]] = []
        findings: list[str] = []
        hypotheses: list[str] = []
        data_gaps: list[str] = []

        if material_id:
            stock = self._call(ctx, "query_material_stock", material_id=material_id)
            if stock.ok and isinstance(stock.data, dict):
                evidence.append({"ref": f"stock:{material_id}", "detail": stock.data})
                available = stock.data.get("available_qty", 0)
                safety = stock.data.get("safety_stock", 0)
                findings.append(f"物料 {material_id} 可用 {available} / 安全库存 {safety}")
                if available < safety:
                    hypotheses.append("触发补料配送")
            else:
                data_gaps.append(f"库存查询失败：{stock.error_code}")
        else:
            data_gaps.append("缺少 material_id")

        llm_finding = self._llm_finding(ctx)
        if llm_finding is not None:
            return llm_finding

        return AgentFinding(
            agent_name=self.name,
            summary="；".join(findings) if findings else "物料配送侧证据不足",
            findings=findings,
            hypotheses=hypotheses,
            evidence=evidence,
            confidence=round(max(0.0, min(0.8, 0.4 + 0.15 * len(evidence)) - 0.15 * len(data_gaps)), 2),
            data_gaps=data_gaps,
            suggested_next_steps=["核对替代料认证状态", "配送路径调整须人工确认"],
            risk_notes=["配送路径调整属高风险，仅生成建议不自动下发"],
        )


# B2：第一版暂未启用；库存与配送路径建议，路径调整禁止自动执行
class LogisticsServiceAgent(BaseAgent):
    """B2 物流智能体服务：库存与配送路径建议。"""

    name = "logistics_service"
    domain = "物流服务"

    def _analyze(self, ctx: AgentContext) -> AgentFinding:
        refs = ctx.incident.entity_refs
        evidence: list[dict[str, Any]] = []
        findings: list[str] = []
        data_gaps: list[str] = []

        for key, tool in (("material_id", "query_material_stock"), ("batch_id", "query_batch")):
            value = refs.get(key)
            if not value:
                continue
            result = self._call(ctx, tool, **{key: value})
            if result.ok and isinstance(result.data, dict):
                evidence.append({"ref": f"{key}:{value}", "detail": result.data})
                findings.append(f"{key} {value} 已取数")
            else:
                data_gaps.append(f"{key} 查询失败：{result.error_code}")

        llm_finding = self._llm_finding(ctx)
        if llm_finding is not None:
            return llm_finding

        return AgentFinding(
            agent_name=self.name,
            summary="；".join(findings) if findings else "物流侧数据不足",
            findings=findings,
            evidence=evidence,
            confidence=round(max(0.0, min(0.75, 0.35 + 0.1 * len(evidence))), 2),
            data_gaps=data_gaps,
            suggested_next_steps=["配送路径调整由生产主管确认后下发"],
            risk_notes=["路径调整属高风险控制动作，禁止自动执行"],
        )


# A4：第一版暂未启用；瓶颈识别 + 调度建议草稿，create_dispatch_suggestion 写操作受灰度/确认约束
class LineDispatchAgent(BaseAgent):
    """A4 产线调度智能体：瓶颈识别与动态调度建议。"""

    name = "line_dispatch"
    domain = "产线调度"

    def _analyze(self, ctx: AgentContext) -> AgentFinding:
        refs = ctx.incident.entity_refs
        work_order_id = refs.get("work_order_id")
        evidence: list[dict[str, Any]] = []
        findings: list[str] = []
        hypotheses: list[str] = []
        data_gaps: list[str] = []

        if work_order_id:
            wo = self._call(ctx, "query_work_order", work_order_id=work_order_id)
            if wo.ok and isinstance(wo.data, dict):
                evidence.append({"ref": f"wo:{work_order_id}", "detail": wo.data})
                findings.append(f"产线 {wo.data.get('line_id')} 工单 {work_order_id} 状态 {wo.data.get('status')}")
                if wo.data.get("status") == "blocked":
                    hypotheses.append("当前工序为瓶颈节点，建议动态调度")
            else:
                data_gaps.append(f"工单查询失败：{wo.error_code}")
        else:
            data_gaps.append("缺少 work_order_id")

        suggestion = self._call(
            ctx,
            "create_dispatch_suggestion",
            line_id=refs.get("line_id"),
            work_order_id=work_order_id,
        )
        if suggestion.ok and isinstance(suggestion.data, dict):
            evidence.append({"ref": "dispatch_suggestion", "detail": suggestion.data})
            findings.append("已生成调度建议草稿，待人工确认")
        elif suggestion.error_code not in (None, "WRITE_BLOCKED"):
            data_gaps.append(f"调度建议生成失败：{suggestion.error_code}")

        llm_finding = self._llm_finding(ctx)
        if llm_finding is not None:
            return llm_finding

        return AgentFinding(
            agent_name=self.name,
            summary="；".join(findings) if findings else "产线调度侧证据不足",
            findings=findings,
            hypotheses=hypotheses,
            evidence=evidence,
            confidence=round(max(0.0, min(0.75, 0.35 + 0.1 * len(evidence))), 2),
            data_gaps=data_gaps,
            suggested_next_steps=["调度建议下发须生产主管确认"],
            risk_notes=["调度下发属高风险，仅生成建议不自动执行"],
        )


# A5：第一版暂未启用；standalone 子图独立链路，不入异常主链，不触发任何生产控制
class TrainingTeachingAgent(BaseAgent):
    """A5 实训教学智能体：独立链路，评分与能力画像。"""

    name = "training_teaching"
    domain = "实训教学"

    def _analyze(self, ctx: AgentContext) -> AgentFinding:
        refs = ctx.incident.entity_refs
        trainee_id = refs.get("trainee_id") or refs.get("operator_id")
        result = self._call(ctx, "training_score", trainee_id=trainee_id, task_id=refs.get("task_id"))
        if not result.ok or not isinstance(result.data, dict):
            return AgentFinding(
                agent_name=self.name,
                summary="实训评分不可用",
                data_gaps=[f"实训评分查询失败：{result.error_code}"],
                confidence=0.0,
            )
        return AgentFinding(
            agent_name=self.name,
            summary=f"学员 {trainee_id} 得分 {result.data.get('score')}",
            findings=[f"能力画像：{result.data.get('profile')}"],
            evidence=[{"ref": f"training:{trainee_id}", "detail": result.data}],
            confidence=0.6,
            risk_notes=["实训链路不入异常主链，不触发任何生产控制"],
        )


# B5：第一版暂未启用；standalone 子图，服务化评分与画像，不入异常主链
class TrainingServiceAgent(BaseAgent):
    """B5 实训智能体服务：服务化评分与画像。"""

    name = "training_service"
    domain = "实训服务"

    def _analyze(self, ctx: AgentContext) -> AgentFinding:
        result = self._call(
            ctx,
            "training_score",
            trainee_id=ctx.incident.entity_refs.get("trainee_id"),
            task_id=ctx.incident.entity_refs.get("task_id"),
        )
        if not result.ok or not isinstance(result.data, dict):
            return AgentFinding(
                agent_name=self.name,
                summary="实训服务评分不可用",
                data_gaps=[f"评分查询失败：{result.error_code}"],
                confidence=0.0,
            )
        return AgentFinding(
            agent_name=self.name,
            summary=f"实训服务评分 {result.data.get('score')}",
            evidence=[{"ref": "training_service", "detail": result.data}],
            confidence=0.6,
            risk_notes=["实训链路不入异常主链"],
        )


# ==================== C 系列行业规则注入类（C1–C3，factory 走 industry.rule_pack 装配）====================


# C1–C3：不调工具、不自由协作，仅确定性发射行业校验项 + 确认边界 + 模板提示
class IndustryRuleAgent(BaseAgent):
    """C1–C3 行业专用规则注入：确定性发射校验项与确认边界，不调用工具、不自由协作。"""

    name = "industry_rule"
    domain = "行业规则"

    def __init__(self, pack: Any, audit: "AuditRecorder | None" = None) -> None:  # type: ignore[name-defined]
        super().__init__(audit)
        self._pack = pack

    def _analyze(self, ctx: AgentContext) -> AgentFinding:
        pack = self._pack
        checks = list(pack.checks)
        confirm_notes = list(pack.confirm_notes)
        template = getattr(pack, "template_hint", None)
        findings = [f"[{pack.code}] {c}" for c in checks]
        hypotheses = []
        if template:
            hypotheses.append(f"建议复用工艺模板：{template}")
        return AgentFinding(
            agent_name=f"{self.name}_{pack.code}",
            summary=f"{pack.title}：需执行 {len(checks)} 项行业校验",
            findings=findings,
            hypotheses=hypotheses,
            confidence=0.5,
            risk_notes=confirm_notes or ["行业关键动作须人工确认"],
        )