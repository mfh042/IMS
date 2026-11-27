"""阶段 6：主调度与 LangGraph 编排。

graph 包的入口文件：把两个核心符号 re-export 到包顶层。
外部（API 层 / 测试代码）使用时只需：

    from industrial_agent.graph import IndustrialAgentGraph, IndustrialAgentState

    agent = IndustrialAgentGraph()              # 装配全部子系统 + 编译 14 节点主图
    state = agent.run(raw_event)                # 跑完整主图，返回最终状态（含 final_answer）
    answer = state["final_answer"]

- IndustrialAgentGraph：编排器主体，唯一装配点 + 总指挥（见 orchestrator.py）；
- IndustrialAgentState：节点间数据契约，22 字段 TypedDict（见 state.py）。
"""

# IndustrialAgentGraph：14 节点确定性编排图，对外暴露 run / resume / replay / run_single_agent
from industrial_agent.graph.orchestrator import IndustrialAgentGraph
# IndustrialAgentState：节点间传话的标准化信封（TypedDict, total=False，所有字段可选）
from industrial_agent.graph.state import IndustrialAgentState

# 显式声明公开 API：本包只暴露编排器与状态契约，内部节点方法不导出
__all__ = ["IndustrialAgentGraph", "IndustrialAgentState"]