"""阶段 4：统一受控工具层。

tools 包的入口文件：把工具契约、注册表、适配器、装配函数 re-export 到包顶层。
外部（子智能体 / 主图节点 / 执行层 / API / 测试）使用时只需：

    from industrial_agent.tools import ToolRegistry, ToolContext, build_default_registry

    registry = build_default_registry()                 # 装配 17 个默认工具
    ctx = ToolContext(trace_id="t1", roles=["system"])  # 调用上下文（默认收紧高风险）
    result = registry.invoke("query_device_status",
                             {"device_id": "DEVICE-A-001"}, context=ctx)

本模块是"三道闸纵深防御"的工具层闸：所有外部系统访问必须经 ToolRegistry 的九步
前置检查链，无法绕过权限 / 风险 / 审计 / 幂等管控。
"""

# 工具元数据 / 调用上下文 / 统一返回 —— 工具层的契约定义
from industrial_agent.tools.contracts import ToolContext, ToolResult, ToolSpec
# 工具注册表与九步执行链 —— 所有外部访问的唯一入口
from industrial_agent.tools.registry import ToolRegistry
# 工业系统适配器（内存示例，真实项目替换为 WMS/MES/QMS/EAM 接口）
from industrial_agent.tools.adapters import IndustrialAdapter
# 默认工具清单装配：把 17 个 ToolSpec 注册进 registry 并绑定 adapter handler
from industrial_agent.tools.factory import build_default_registry

# 显式声明公开 API：包的稳定导出契约，新增符号须在此登记
__all__ = [
    "IndustrialAdapter",
    "ToolContext",
    "ToolRegistry",
    "ToolResult",
    "ToolSpec",
    "build_default_registry",
]