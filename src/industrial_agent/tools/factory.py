"""阶段 4.2 工具清单装配：只读 / 低风险写 / 中风险写 / 高风险控制。

build_default_registry 把 17 个 ToolSpec 注册进 registry，并把每个工具的 handler 绑定到
IndustrialAdapter 的对应方法。这是"工具元数据"与"真实数据源实现"的装配点——
换真实 adapter 只需在调用处传入，本清单的 kind/risk/required_roles 配置保持不变。

风险配置的判定原则（与 planning/builder.py 的 RiskClassifier 口径一致）：
- 纯查询、无副作用 → read + low，可自动执行；
- 建草稿 / 发通知 / 生成建议（不直接影响生产）→ write + medium，需角色权限；
- 设备控制 / 批次冻结 / 配送路径下发（直接影响生产）→ control + high，必须人工确认。
"""

from __future__ import annotations

# ---- 本项目：工具层内部依赖 ----
from industrial_agent.tools.adapters import IndustrialAdapter   # 真实数据源实现（可注入替换）
from industrial_agent.tools.contracts import ToolSpec           # 工具元数据契约
from industrial_agent.tools.registry import ToolRegistry        # 目标注册表（可注入预填实例）


def build_default_registry(
    adapter: IndustrialAdapter | None = None,
    *,
    registry: ToolRegistry | None = None,
) -> ToolRegistry:
    """装配默认工具清单并返回 registry。

    Args:
        adapter: 工业系统适配器；不传则用内存示例 IndustrialAdapter（便于联调与测试）。
        registry: 目标注册表；不传则新建。传入预填 registry 可在默认清单上追加自定义工具。

    Returns:
        注册了全部默认工具的 ToolRegistry 实例。
    """
    # 两个参数都可注入：测试时传 mock adapter，扩展时传预填 registry
    adapter = adapter or IndustrialAdapter()
    registry = registry or ToolRegistry()

    specs = [
        # ==================== 只读 low（9 个）：无副作用，可自动执行 ====================
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
        # C3 快速换型复用模板：只读工艺参数，无副作用
        ToolSpec(
            name="reuse_process_template",
            description="按产品码复用工艺模板与参数（C3 快速换型，只读）",
            kind="read",
            risk="low",
            retryable=True,
            handler=adapter.reuse_process_template,
        ),
        # A5 / B5 实训链路独立，不入异常主链
        ToolSpec(
            name="training_score",
            description="实训评分与能力画像（A5 / B5 独立链路）",
            kind="read",
            risk="low",
            retryable=True,
            handler=adapter.training_score,
        ),
        # ==================== 写 medium（5 个）：有副作用但不直接影响生产，需角色权限 ====================
        # E-SOP 加载是记录态（记录谁在何时看了哪份工艺），不落库改数据，但需生产/维护角色
        ToolSpec(
            name="load_esop",
            description="加载 E-SOP 工艺文件与作业步骤（A1 / B1 使用，记录态）",
            kind="write",
            risk="medium",
            required_roles=("production", "maintenance", "system"),
            handler=adapter.load_esop,
        ),
        # 码绑定生成可追溯装配履历，属于生产记录写入
        ToolSpec(
            name="bind_codes",
            description="产品码/物料码/工具码绑定，生成可追溯装配履历（A1 / B1 使用）",
            kind="write",
            risk="medium",
            required_roles=("production", "quality", "system"),
            handler=adapter.bind_codes,
        ),
        # 仅生成调度建议，不下发执行
        ToolSpec(
            name="create_dispatch_suggestion",
            description="生成产线动态调度建议（仅建议，A4 使用）",
            kind="write",
            risk="medium",
            required_roles=("production", "system"),
            handler=adapter.create_dispatch_suggestion,
        ),
        # 草稿态工单，不影响生产排程
        ToolSpec(
            name="create_ticket_draft",
            description="创建工单草稿（草稿态，不影响生产）",
            kind="write",
            risk="medium",
            required_roles=("production", "maintenance", "system"),
            handler=adapter.create_ticket_draft,
        ),
        # 通知责任人：纯消息，不改生产数据
        ToolSpec(
            name="send_notification",
            description="发送通知给责任人",
            kind="write",
            risk="medium",
            required_roles=("production", "maintenance", "system"),
            handler=adapter.notify,
        ),
        # ==================== 控制 high（3 个）：直接影响生产，必须人工确认 ====================
        # auto_executable=False，registry 会在 allow_high_risk=False 时拦截，仅生成建议
        ToolSpec(
            name="adjust_delivery_route",
            description="配送路径调整（高风险，仅生成建议，确认后方可下发）",
            kind="control",
            risk="high",
            required_roles=("production_lead",),
            handler=adapter.adjust_delivery_route,
        ),
        # 设备停机/启机/改参数：最高风险，需维护主管角色 + 人工确认
        ToolSpec(
            name="control_device",
            description="设备停机/启机/参数修改（高风险，必须人工确认）",
            kind="control",
            risk="high",
            required_roles=("maintenance_lead",),
            handler=adapter.control,
        ),
        # 批次冻结：影响物料可用性，需质量主管角色 + 人工确认
        ToolSpec(
            name="freeze_batch",
            description="冻结批次（高风险，必须人工确认）",
            kind="control",
            risk="high",
            required_roles=("quality_lead",),
            handler=adapter.control,
        ),
    ]
    # 统一注册：register 对重名报错，保证清单内工具名唯一
    for spec in specs:
        registry.register(spec)
    return registry