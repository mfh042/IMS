"""阶段 8：执行与人工确认闭环。

execution 包的入口文件：把 executor.py 里的两个核心类 re-export 到包顶层。
外部（主图 orchestrator / 测试代码）使用时只需：

    from industrial_agent.execution import ActionExecutor, ConfirmStore

    executor = ActionExecutor(tools, audit=audit, confirms=confirms)
    # 三分桶分流 + 五道闸执行 + 人工确认 + 失败回滚，全部由 ActionExecutor 统一收口
"""

# ActionExecutor：执行层唯一入口，承接主图节点 10 的低/中/高风险执行调用
# ConfirmStore：人工确认请求存储，带 TTL 超时与 RBAC 角色校验，严禁默认同意
from industrial_agent.execution.executor import ActionExecutor, ConfirmStore

# 显式声明公开 API：本包只暴露执行器与确认存储，内部辅助不导出
__all__ = ["ActionExecutor", "ConfirmStore"]