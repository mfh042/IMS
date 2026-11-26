"""阶段 5.2 专业子 Agent 实现（规则兜底 + 可选 LLM 结构化增强）。"""

from __future__ import annotations

from typing import Any

from industrial_agent.agents.base import AgentContext, BaseAgent
from industrial_agent.models.findings import AgentFinding


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