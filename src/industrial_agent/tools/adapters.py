"""工业系统 Adapter 示例实现：真实项目替换为 WMS/MES/QMS/EAM 接口。"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any


class IndustrialAdapter:
    """只读数据源适配器集合（示例内存实现，便于框架联调）。"""

    def __init__(self) -> None:
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
                "temperature": None,
                "vibration": None,
                "spindle_load": None,
                "data_quality": "poor",
                "collected_at": "2026-11-26T09:10:00+08:00",
            },
        }
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
        self.inventory: dict[str, dict[str, Any]] = {
            "M-10023": {
                "material_id": "M-10023",
                "available_qty": 80,
                "in_transit_qty": 0,
                "safety_stock": 120,
                "uom": "PCS",
            }
        }
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
        self.quality_records: dict[str, dict[str, Any]] = {
            "B20261126-08": {
                "batch_id": "B20261126-08",
                "inspection_result": "NG",
                "defect_code": "DIM_OUT_OF_TOL",
                "affected_batches": ["B20261126-08"],
                "trace_chain": ["OP-10", "OP-20"],
            }
        }

        self.ticket_drafts: list[dict[str, Any]] = []
        self.notifications: list[dict[str, Any]] = []
        self.control_calls: list[dict[str, Any]] = []

    def device_status(self, device_id: str) -> dict[str, Any] | None:
        return self.devices.get(device_id)

    def recent_alarms(self, device_id: str, hours: int = 24) -> list[dict[str, Any]]:
        return list(self.alarms.get(device_id, []))

    def device_trend(self, device_id: str, metric: str, hours: int = 8) -> dict[str, Any]:
        device = self.devices.get(device_id)
        if device is None:
            return {}
        value = device.get(metric)
        if value is None:
            return {}
        return {
            "device_id": device_id,
            "metric": metric,
            "points": [round(float(value) * f, 2) for f in (0.92, 0.96, 1.0)],
            "hours": hours,
        }

    def work_order(self, work_order_id: str) -> dict[str, Any] | None:
        return self.work_orders.get(work_order_id)

    def stock(self, material_id: str) -> dict[str, Any] | None:
        return self.inventory.get(material_id)

    def batch(self, batch_id: str) -> dict[str, Any] | None:
        return self.batches.get(batch_id)

    def quality(self, batch_id: str) -> dict[str, Any] | None:
        return self.quality_records.get(batch_id)

    def create_ticket_draft(self, **payload: Any) -> dict[str, Any]:
        draft = {
            "ticket_draft_id": f"tkt-{len(self.ticket_drafts) + 1:04d}",
            "status": "draft",
            "created_at": datetime.now(UTC).isoformat(),
            **payload,
        }
        self.ticket_drafts.append(draft)
        return draft

    def notify(self, **payload: Any) -> dict[str, Any]:
        message = {
            "notification_id": f"ntf-{len(self.notifications) + 1:04d}",
            "sent_at": datetime.now(UTC).isoformat(),
            **payload,
        }
        self.notifications.append(message)
        return message

    def control(self, **payload: Any) -> dict[str, Any]:
        call = {
            "control_id": f"ctl-{len(self.control_calls) + 1:04d}",
            "executed_at": datetime.now(UTC).isoformat(),
            **payload,
        }
        self.control_calls.append(call)
        return call