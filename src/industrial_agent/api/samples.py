"""前端可视化用的示例事件报文。

覆盖 7 种 incident_type（equipment / quality / material / production /
query / standalone / mixed），共 8 条样本，便于在前端「单 Agent 模拟」与
「端到端编排」两个视图中一键加载测试。

注意：这里的 event 字段是**上游异构原始报文**（WMS/MES/QMS/EAM/MANUAL 格式），
不是归一化后的 StandardEvent；归一化由 ingestion/normalizer.py 在主图节点 1 完成。
前端拿到样本后原样 POST 给 /api/events 或 /api/agents/{code}/run 即可。
"""

from __future__ import annotations

from typing import Any

# 示例事件列表：每条含 key（唯一标识，前端路由用）/ label（下拉框显示）/
# incident_type（决定路由到哪条子图）/ event（原始报文，异构格式）
SAMPLE_EVENTS: list[dict[str, Any]] = [
    # 1. 设备温度过高 → equipment_exception，路由 B3(主脑) + A1(工位) + A3(追溯)
    {
        "key": "equipment_temp",
        "label": "设备温度过高（equipment_exception）",
        "incident_type": "equipment_exception",
        "event": {
            "event_id": "evt-eq-temp-001",          # 上游事件唯一 ID
            "event_type": "eam.device.temperature_high",  # 源系统事件类型码
            "source_system": "EAM",                 # 来源系统（EAM=设备资产管理）
            "occurred_at": "2026-11-26T10:20:00+08:00",  # 发生时间（带时区）
            "severity": "high",                     # 严重等级
            "tenant_id": "factory_a",               # 租户隔离
            "trace_id": "trace-eq-temp-001",        # 全链路追踪 ID（审计用）
            "payload": {                            # 业务载荷，normalizer 从这里提取实体
                "device_id": "DEVICE-A-001",
                "line_id": "LINE-03",
                "work_order_id": "WO20261126001",
                "alarm_code": "TEMP_HIGH",
                "message": "主轴温度超过阈值 75℃",
            },
        },
    },
    # 2. 设备振动报警 → equipment_exception（同子图，不同实体/报警码）
    {
        "key": "equipment_vibration",
        "label": "设备振动报警（equipment_exception）",
        "incident_type": "equipment_exception",
        "event": {
            "event_id": "evt-eq-vib-002",
            "event_type": "eam.device.vibration_alarm",
            "source_system": "EAM",
            "occurred_at": "2026-11-26T11:05:00+08:00",
            "severity": "critical",
            "tenant_id": "factory_a",
            "trace_id": "trace-eq-vib-002",
            "payload": {
                "device_id": "DEVICE-B-007",
                "line_id": "LINE-02",
                "work_order_id": "WO20261126018",
                "alarm_code": "VIBRATION_HIGH",
                "message": "设备振动幅值超出上限 8.2mm/s",
            },
        },
    },
    # 3. 质量不良/CAPA → quality_exception，路由 A3 + B4 + B3
    {
        "key": "quality_defect",
        "label": "质量不良/CAPA（quality_exception）",
        "incident_type": "quality_exception",
        "event": {
            "event_id": "evt-ql-defect-003",
            "event_type": "qms.quality.defect",
            "source_system": "QMS",                 # 来源系统（QMS=质量管理）
            "occurred_at": "2026-11-26T09:40:00+08:00",
            "severity": "high",
            "tenant_id": "factory_a",
            "trace_id": "trace-ql-defect-003",
            "payload": {
                "product_code": "PRD-X100",
                "batch_id": "BATCH-20261125-008",
                "work_order_id": "WO20261125009",
                "defect_code": "TORQUE_LOW",        # 不良代码（质量追溯主键之一）
                "message": "终检发现扭矩不合格，疑似装配工序异常",
            },
        },
    },
    # 4. 缺料/工单阻塞 → material_exception，路由 A2 + B2 + A4 + A1
    {
        "key": "material_shortage",
        "label": "缺料/工单阻塞（material_exception）",
        "incident_type": "material_exception",
        "event": {
            "event_id": "evt-mt-short-004",
            "event_type": "wms.material.stock_low",
            "source_system": "WMS",                 # 来源系统（WMS=仓储管理）
            "occurred_at": "2026-11-26T08:15:00+08:00",
            "severity": "medium",
            "tenant_id": "factory_a",
            "trace_id": "trace-mt-short-004",
            "payload": {
                "material_id": "MAT-BOLT-M8",
                "batch_id": "BATCH-20261120-003",
                "work_order_id": "WO20261126002",
                "line_id": "LINE-01",
                "message": "线边库存低于安全库存，缺料导致工单阻塞",
            },
        },
    },
    # 5. 生产异常停线 → production_exception，路由 A4 + A2 + B2 + A1
    {
        "key": "production_blocked",
        "label": "生产异常停线（production_exception）",
        "incident_type": "production_exception",
        "event": {
            "event_id": "evt-pr-block-005",
            "event_type": "mes.work_order.blocked",
            "source_system": "MES",                 # 来源系统（MES=制造执行）
            "occurred_at": "2026-11-26T13:30:00+08:00",
            "severity": "high",
            "tenant_id": "factory_a",
            "trace_id": "trace-pr-block-005",
            "payload": {
                "work_order_id": "WO20261126010",
                "line_id": "LINE-04",
                "device_id": "DEVICE-A-001",
                "message": "工序节拍异常导致产线阻塞停线",
            },
        },
    },
    # 6. 装配履历/工艺问答 → query，路由 B1 + A1（只读查询链路）
    {
        "key": "query_process",
        "label": "装配履历/工艺问答（query）",
        "incident_type": "query",
        "event": {
            "event_id": "evt-qy-proc-006",
            "event_type": "manual.query.process",
            "source_system": "MANUAL",              # 来源系统（人工发起，非上游自动推送）
            "occurred_at": "2026-11-26T14:00:00+08:00",
            "severity": "low",
            "tenant_id": "factory_a",
            "trace_id": "trace-qy-proc-006",
            "payload": {
                "work_order_id": "WO20261126005",
                "product_code": "PRD-X100",
                "message": "查询该工单的装配工艺步骤与历史装配履历",
            },
        },
    },
    # 7. 实训任务 → standalone，路由 A5 + B5（独立链路，不入异常主链）
    {
        "key": "standalone_training",
        "label": "实训任务（standalone）",
        "incident_type": "standalone",
        "event": {
            "event_id": "evt-sd-train-007",
            "event_type": "manual.training.task",
            "source_system": "MANUAL",
            "occurred_at": "2026-11-26T15:00:00+08:00",
            "severity": "low",
            "tenant_id": "factory_a",
            "trace_id": "trace-sd-train-007",
            "payload": {
                "trainee_id": "TRAINEE-2026-017",
                "task_id": "TASK-VISION-03",
                "message": "发起视觉检测实训任务并请求过程指导",
            },
        },
    },
    # 8. 设备+物料混合异常 → mixed_exception，路由全量 B3/A2/B2/A4/A3/A1（最复杂链路）
    {
        "key": "mixed_alarm",
        "label": "设备+物料混合异常（mixed_exception）",
        "incident_type": "mixed_exception",
        "event": {
            "event_id": "evt-mx-alarm-008",
            "event_type": "eam.device.alarm_with_material",
            "source_system": "EAM",
            "occurred_at": "2026-11-26T16:10:00+08:00",
            "severity": "critical",
            "tenant_id": "factory_a",
            "trace_id": "trace-mx-alarm-008",
            "payload": {
                "device_id": "DEVICE-C-002",
                "material_id": "MAT-SENSOR-05",
                "batch_id": "BATCH-20261126-011",
                "work_order_id": "WO20261126021",
                "alarm_code": "DEVICE_STOP",
                "message": "设备停机并伴随关键物料批次质量报警",
            },
        },
    },
]