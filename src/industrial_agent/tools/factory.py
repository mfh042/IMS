"""阶段 4.2 工具清单装配：只读 / 低风险写 / 中风险写 / 高风险控制。"""

from __future__ import annotations

from industrial_agent.tools.adapters import IndustrialAdapter
from industrial_agent.tools.contracts import ToolSpec
from industrial_agent.tools.registry import ToolRegistry


def build_default_registry(
    adapter: IndustrialAdapter | None = None,
    *,
    registry: ToolRegistry | None = None,
) -> ToolRegistry:
    adapter = adapter or IndustrialAdapter()
    registry = registry or ToolRegistry()

    specs = [
        ToolSpec(
            name="query_device_status",
            description="查询设备实时状态：运行状态、关键测点、采集时间、数据质量",
            kind="read",
            risk="low",
            retryable=True,
            handler=adapter.device_status,
        ),
        ToolSpec(
            name="query_recent_alarms",
            description="查询设备最近报警记录",
            kind="read",
            risk="low",
            retryable=True,
            handler=adapter.recent_alarms,
        ),
        ToolSpec(
            name="query_device_trend",
            description="查询设备测点趋势",
            kind="read",
            risk="low",
            retryable=True,
            handler=adapter.device_trend,
        ),
        ToolSpec(
            name="query_work_order",
            description="查询工单状态、工序、计划与完工数量",
            kind="read",
            risk="low",
            retryable=True,
            handler=adapter.work_order,
        ),
        ToolSpec(
            name="query_material_stock",
            description="查询物料可用量、在途量与安全库存",
            kind="read",
            risk="low",
            retryable=True,
            handler=adapter.stock,
        ),
        ToolSpec(
            name="query_batch",
            description="查询批次冻结状态与库位信息",
            kind="read",
            risk="low",
            retryable=True,
            handler=adapter.batch,
        ),
        ToolSpec(
            name="query_quality_record",
            description="查询检验结果、不良代码与追溯链",
            kind="read",
            risk="low",
            retryable=True,
            handler=adapter.quality,
        ),
        ToolSpec(
            name="create_ticket_draft",
            description="创建工单草稿（草稿态，不影响生产）",
            kind="write",
            risk="medium",
            required_roles=("production", "maintenance", "system"),
            handler=adapter.create_ticket_draft,
        ),
        ToolSpec(
            name="send_notification",
            description="发送通知给责任人",
            kind="write",
            risk="medium",
            required_roles=("production", "maintenance", "system"),
            handler=adapter.notify,
        ),
        ToolSpec(
            name="control_device",
            description="设备停机/启机/参数修改（高风险，必须人工确认）",
            kind="control",
            risk="high",
            required_roles=("maintenance_lead",),
            handler=adapter.control,
        ),
        ToolSpec(
            name="freeze_batch",
            description="冻结批次（高风险，必须人工确认）",
            kind="control",
            risk="high",
            required_roles=("quality_lead",),
            handler=adapter.control,
        ),
    ]
    for spec in specs:
        registry.register(spec)
    return registry