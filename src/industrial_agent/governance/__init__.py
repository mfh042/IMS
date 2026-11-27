"""事件治理层。

governance 包的入口文件：把 governor.py 里的 EventGovernance 类 re-export 到包顶层。
外部（主图 orchestrator / 测试代码）使用时只需：

    from industrial_agent.governance import EventGovernance

    governance = EventGovernance(
        dedup_window_seconds=300,
        max_concurrent_incidents=8,
        per_entity_incident_limit=2,
        priority_wait_weight=0.5,
    )
    # 去重 → 聚合 → 限流 → 优先级评分 → 生成/关闭 Incident，全部由 EventGovernance 收口
"""

# EventGovernance：事件治理层唯一入口，承接主图节点 2 的治理调用
from industrial_agent.governance.governor import EventGovernance

# 显式声明公开 API：本包只暴露治理器，内部累加器 _Aggregate 不导出
__all__ = ["EventGovernance"]