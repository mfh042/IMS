"""工业系统 Adapter 示例实现：真实项目替换为 WMS/MES/QMS/EAM 接口。

IndustrialAdapter 是"真实数据源"的内存示例实现，目的是让框架在无外部依赖时可联调、
可跑测试。它分两类方法：
- 只读查询：从 __init__ 预置的 fixtures（dict）返回，模拟 EAM/MES/WMS/QMS 的查询接口；
- 写/控制：把入参追加到对应 list 并返回带 id 的记录，模拟"落库成功"。

真实项目替换原则：实现相同的方法签名（参数名/返回结构保持 dict|list|None），
registry 会用 ToolResult 包装——None → NO_DATA 降级，抛异常 → DOWNSTREAM_UNAVAILABLE 降级，
因此真实 adapter 不应抛业务异常，应自行捕获并返回 None 或带错误字段的 dict。
"""

from __future__ import annotations

# ---- 标准库 ----
from datetime import UTC, datetime   # 写操作生成时间戳（UTC，与 ToolResult.fetched_at 一致）
from typing import Any               # fixtures 与返回值为异构 dict/list


class IndustrialAdapter:
    """只读数据源适配器集合（示例内存实现，便于框架联调）。

    __init__ 预置三类数据：
    - 查询侧 fixtures：devices / alarms / work_orders / inventory / batches / quality_records / templates；
    - 写侧记录 list：ticket_drafts / notifications / control_calls / esop_sessions /
      code_bindings / dispatch_suggestions / training_scores（写操作往里追加）。
    真实项目：这些字段替换为对外部系统的 HTTP/消息客户端，方法实现改为远程调用。
    """

    def __init__(self) -> None:
        # ---- 查询侧 fixtures（模拟 EAM/MES/WMS/QMS 返回）----
        # 设备实时状态：DEVICE-A-001 在线超温（正向案例），DEVICE-B-002 离线通讯中断（数据缺口案例）
        self.devices: dict[str, dict[str, Any]] = {
            "DEVICE-A-001": {
                "device_id": "DEVICE-A-001",
                "device_type": "CNC",
                "online": True,
                "status": "running",
                "temperature": 82.4,
                "vibration": 3.6,
                "spindle_load": 0.78,
                "data_quality": "good",
                "collected_at": "2026-11-26T10:00:00+08:00",
            },
            "DEVICE-B-002": {
                "device_id": "DEVICE-B-002",
                "device_type": "ROBOT",
                "online": False,
                "status": "offline",
                "temperature": None,       # 离线导致测点缺失：驱动 data_quality=poor 与缺口判定
                "vibration": None,
                "spindle_load": None,
                "data_quality": "poor",
                "collected_at": "2026-11-26T09:10:00+08:00",
            },
        }
        # 设备报警历史：与 devices 关联，供取证智能体交叉验证
        self.alarms: dict[str, list[dict[str, Any]]] = {
            "DEVICE-A-001": [
                {
                    "alarm_code": "TEMP_HIGH",
                    "level": "warning",
                    "message": "主轴温度超过阈值 75℃",
                    "created_at": "2026-11-26T09:30:00+08:00",
                },
                {
                    "alarm_code": "VIBRATION_HIGH",
                    "level": "warning",
                    "message": "振动值持续偏高",
                    "created_at": "2026-11-26T09:50:00+08:00",
                },
            ],
            "DEVICE-B-002": [
                {
                    "alarm_code": "COMM_LOST",
                    "level": "critical",
                    "message": "与 PLC 通讯中断",
                    "created_at": "2026-11-26T09:05:00+08:00",
                }
            ],
        }
        # 工单：模拟 MES 返回，含缺料阻塞场景（blocked + 物料低于安全库存）
        self.work_orders: dict[str, dict[str, Any]] = {
            "WO20261126001": {
                "work_order_id": "WO20261126001",
                "line_id": "LINE-03",
                "process_id": "OP-20",
                "status": "blocked",
                "planned_qty": 500,
                "completed_qty": 120,
                "material_id": "M-10023",
                "batch_id": "B20261126-08",
                "blocked_reason": "疑似缺料",
            }
        }
        # 库存：M-10023 可用量 80 < 安全库存 120，与工单缺料场景呼应
        self.inventory: dict[str, dict[str, Any]] = {
            "M-10023": {
                "material_id": "M-10023",
                "available_qty": 80,
                "in_transit_qty": 0,
                "safety_stock": 120,
                "uom": "PCS",
            }
        }
        # 批次：B20261126-08 已冻结（质量不合格），与下面 quality_records 形成证据链
        self.batches: dict[str, dict[str, Any]] = {
            "B20261126-08": {
                "batch_id": "B20261126-08",
                "material_id": "M-10023",
                "frozen": True,
                "freeze_reason": "过程检验不合格",
                "location": "WH-A-03",
                "quantity": 200,
            }
        }
        # 质量记录：与 batches 关联，NG + 缺陷码 + 追溯链
        self.quality_records: dict[str, dict[str, Any]] = {
            "B20261126-08": {
                "batch_id": "B20261126-08",
                "inspection_result": "NG",
                "defect_code": "DIM_OUT_OF_TOL",
                "affected_batches": ["B20261126-08"],
                "trace_chain": ["OP-10", "OP-20"],
            }
        }

        # ---- 写侧记录 list（模拟落库，写操作往里追加并返回带 id 的记录）----
        self.ticket_drafts: list[dict[str, Any]] = []          # create_ticket_draft 写入
        self.notifications: list[dict[str, Any]] = []          # notify 写入
        self.control_calls: list[dict[str, Any]] = []          # control 写入（设备/批次控制）
        self.esop_sessions: list[dict[str, Any]] = []          # load_esop 写入（记录态）
        self.code_bindings: list[dict[str, Any]] = []          # bind_codes 写入
        self.dispatch_suggestions: list[dict[str, Any]] = []   # adjust_delivery_route / create_dispatch_suggestion 写入
        self.training_scores: list[dict[str, Any]] = []        # training_score 写入
        # 工艺模板 fixtures（只读）：按 product_code 复用，供 C3 快速换型
        self.templates: dict[str, dict[str, Any]] = {
            "PROC-TPL-001": {
                "template_id": "PROC-TPL-001",
                "product_code": "P-2001",
                "process_id": "OP-20",
                "version": "v2.1",
                "parameters": {"torque": 42, "speed": 1800, "cycle_time": 96},
            }
        }

    # ==================== 只读查询（返回 dict|None，None 会被 registry 转 NO_DATA 降级）====================

    def device_status(self, device_id: str) -> dict[str, Any] | None:
        """查询单台设备实时状态（EAM）。找不到返回 None（→ NO_DATA 降级）。"""
        return self.devices.get(device_id)

    def recent_alarms(self, device_id: str, hours: int = 24) -> list[dict[str, Any]]:
        """查询设备最近报警（EAM）。hours 参数示例保留，内存实现返回全量历史。"""
        return list(self.alarms.get(device_id, []))

    def device_trend(self, device_id: str, metric: str, hours: int = 8) -> dict[str, Any]:
        """查询设备测点趋势（EAM/时序库）。内存实现用当前值 × 系数模拟三个历史点；
        设备不存在或测点为 None（离线）返回空 dict（由 registry 视为 NO_DATA 降级）。"""
        device = self.devices.get(device_id)
        if device is None:
            return {}
        value = device.get(metric)
        if value is None:
            return {}
        return {
            "device_id": device_id,
            "metric": metric,
            "points": [round(float(value) * f, 2) for f in (0.92, 0.96, 1.0)],   # 模拟上升趋势
            "hours": hours,
        }

    def work_order(self, work_order_id: str) -> dict[str, Any] | None:
        """查询工单状态（MES）。找不到返回 None（→ NO_DATA 降级）。"""
        return self.work_orders.get(work_order_id)

    def stock(self, material_id: str) -> dict[str, Any] | None:
        """查询物料库存（WMS）。找不到返回 None（→ NO_DATA 降级）。"""
        return self.inventory.get(material_id)

    def batch(self, batch_id: str) -> dict[str, Any] | None:
        """查询批次冻结状态（WMS/QMS）。找不到返回 None（→ NO_DATA 降级）。"""
        return self.batches.get(batch_id)

    def quality(self, batch_id: str) -> dict[str, Any] | None:
        """查询质量检验记录（QMS）。找不到返回 None（→ NO_DATA 降级）。"""
        return self.quality_records.get(batch_id)

    # ==================== 写/控制（模拟落库：入参 + 自增 id + 时间戳追加到 list 并返回）====================

    def create_ticket_draft(self, **payload: Any) -> dict[str, Any]:
        """创建工单草稿（MES，草稿态不影响生产）。payload 透传，返回带 tkt-xxxx id 的草稿。"""
        draft = {
            "ticket_draft_id": f"tkt-{len(self.ticket_drafts) + 1:04d}",
            "status": "draft",
            "created_at": datetime.now(UTC).isoformat(),
            **payload,
        }
        self.ticket_drafts.append(draft)
        return draft

    def notify(self, **payload: Any) -> dict[str, Any]:
        """发送通知给责任人（消息系统）。payload 透传，返回带 ntf-xxxx id 的消息记录。"""
        message = {
            "notification_id": f"ntf-{len(self.notifications) + 1:04d}",
            "sent_at": datetime.now(UTC).isoformat(),
            **payload,
        }
        self.notifications.append(message)
        return message

    def control(self, **payload: Any) -> dict[str, Any]:
        """设备/批次控制的统一落库入口（control_device 与 freeze_batch 共用）。

        注意：此方法只有在 registry 放行（allow_high_risk=True 且角色达标）后才会被调用；
        高风险工具的实际执行受人工确认管控，这里仅模拟"确认后下发"的落痕。
        """
        call = {
            "control_id": f"ctl-{len(self.control_calls) + 1:04d}",
            "executed_at": datetime.now(UTC).isoformat(),
            **payload,
        }
        self.control_calls.append(call)
        return call

    # ---------- 阶段 4.7：13 类智能体共用工具域 ----------

    def load_esop(self, **payload: Any) -> dict[str, Any]:
        """E-SOP 自动加载：返回工艺文件与步骤，供 A1 / B1 使用（记录态，不落库改数据）。"""
        session = {
            "esop_id": f"esop-{len(self.esop_sessions) + 1:04d}",
            "loaded_at": datetime.now(UTC).isoformat(),
            "steps": ["扫码确认物料", "按顺序装配", "扭矩采集并记录", "自检并上报"],
            **payload,
        }
        self.esop_sessions.append(session)
        return session

    def bind_codes(self, **payload: Any) -> dict[str, Any]:
        """产品码/物料码/工具码绑定，生成可追溯装配履历（MES 记录写入）。"""
        binding = {
            "binding_id": f"bind-{len(self.code_bindings) + 1:04d}",
            "bound_at": datetime.now(UTC).isoformat(),
            **payload,
        }
        self.code_bindings.append(binding)
        return binding

    def adjust_delivery_route(self, **payload: Any) -> dict[str, Any]:
        """配送路径调整建议（高风险，仅生成建议 pending_confirm，需人工确认后才下发）。"""
        suggestion = {
            "route_suggestion_id": f"rt-{len(self.dispatch_suggestions) + 1:04d}",
            "created_at": datetime.now(UTC).isoformat(),
            "status": "pending_confirm",
            **payload,
        }
        self.dispatch_suggestions.append(suggestion)
        return suggestion

    def create_dispatch_suggestion(self, **payload: Any) -> dict[str, Any]:
        """产线动态调度建议（高风险，仅建议，状态 pending_confirm）。"""
        suggestion = {
            "dispatch_suggestion_id": f"dsp-{len(self.dispatch_suggestions) + 1:04d}",
            "created_at": datetime.now(UTC).isoformat(),
            "status": "pending_confirm",
            **payload,
        }
        self.dispatch_suggestions.append(suggestion)
        return suggestion

    def reuse_process_template(self, product_code: str, **payload: Any) -> dict[str, Any] | None:
        """按产品码复用工艺模板与参数，供 C3 快速换型使用（只读，无副作用）。

        遍历 templates 找匹配 product_code 的模板，找不到返回 None（→ NO_DATA 降级）。
        """
        for template in self.templates.values():
            if template["product_code"] == product_code:
                return dict(template)     # 返回副本，避免外部篡改 fixtures
        return None

    def training_score(self, **payload: Any) -> dict[str, Any]:
        """实训评分与能力画像（A5 / B5 独立链路，不入异常主链）。"""
        score = {
            "score_id": f"trn-{len(self.training_scores) + 1:04d}",
            "scored_at": datetime.now(UTC).isoformat(),
            "score": 88.5,
            "dimensions": {"规范度": 90, "效率": 82, "质量": 93},
            **payload,
        }
        self.training_scores.append(score)
        return score