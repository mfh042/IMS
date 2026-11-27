"""阶段 7：方案生成与风险分级。

planning 包的入口文件：把 builder.py 里的四个核心符号 re-export 到包顶层。
外部（主图 orchestrator / 测试代码）使用时只需：

    from industrial_agent.planning import PlanBuilder, PlanValidator, RiskClassifier

    builder = PlanBuilder()
    plan = builder.build(incident=incident, findings=findings,
                         knowledge_hits=hits, data_quality=dq)
    result = PlanValidator().validate(plan)   # 就地修正 plan，返回校验结果

本模块是"三道闸纵深防御"的方案层闸：PlanBuilder 生成方案，PlanValidator 在交给
执行层前做最后一道独立校验（就地修正可执行性与置信度）。
"""

# 方案生成主体：把多智能体发现汇总为可执行、可审计、可分级的 ResolutionPlan
from industrial_agent.planning.builder import PlanBuilder
# 方案校验：执行层之前的独立闸，7 项校验，发现问题就地修正
from industrial_agent.planning.builder import PlanValidator
# 风险分级器：规则优先，数据/知识/冲突有问题时保守升级一级
from industrial_agent.planning.builder import RiskClassifier
# 模块级工具：汇总所有 agent 的证据列表（供动作取 evidence_refs）
from industrial_agent.planning.builder import findings_evidence

# 显式声明公开 API：包的稳定导出契约，新增符号须在此登记
__all__ = ["PlanBuilder", "PlanValidator", "RiskClassifier", "findings_evidence"]