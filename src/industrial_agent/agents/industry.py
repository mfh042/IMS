"""阶段 5.8 / 6.8：行业专用智能体（C1–C3）的规则与模板增强包。

C1–C3 不新增自由协作 Agent，而是以"规则/模板增强"方式注入通用子图：
- ``checks``：注入对应子图时必须执行的行业校验项（确定性、可审计）；
- ``confirm_notes``：行业专属的人工确认边界（哪些动作必须人工放行）；
- ``template_hint``：可复用的工艺模板编号（C3 快速换型场景）。

这种设计让行业知识以"硬规则"形式嵌入，而不是交给 LLM 自由发挥，
保证高可靠行业（轨道/航空/新能源）的校验项不会被遗漏或篡改。
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class IndustryRulePack:
    """单个行业的规则增强包（不可变，防止运行时被篡改）。"""

    code: str                              # 行业编号 C1/C2/C3
    title: str                             # 规则包标题
    checks: tuple[str, ...]                # 必须执行的行业校验项（确定性清单）
    confirm_notes: tuple[str, ...]         # 行业专属人工确认边界
    template_hint: str | None = None       # 可选：可复用工艺模板编号（C3 用）


# C1–C3 三个行业的规则包定义：校验项 + 确认边界 + 模板提示
INDUSTRY_RULE_PACKS: dict[str, IndustryRulePack] = {
    "C1": IndustryRulePack(
        code="C1",
        title="轨道装备多品种小批量装配规则",
        checks=(
            "换型前核对工艺版本与首件确认记录",
            "关键件校验产品码与物料批次绑定关系",
            "多品种小批量需核对工装夹具切换清单",
        ),
        confirm_notes=("换型与首件放行必须人工确认",),
        template_hint="PROC-TPL-001",
    ),
    "C2": IndustryRulePack(
        code="C2",
        title="航空高可靠装配与检测规则",
        checks=(
            "关键件识别与装配顺序校验必须留痕",
            "扭矩曲线需完整采集归档，不允许缺段",
            "关键工序检测记录需双人复核",
        ),
        confirm_notes=("处置结论与放行必须人工确认",),
    ),
    "C3": IndustryRulePack(
        code="C3",
        title="新产品导入快速换型规则",
        checks=(
            "优先复用同族产品工艺模板与参数",
            "换型后需首件验证并比对历史参数偏差",
            "换型下发前确认工装与程序版本一致",
        ),
        confirm_notes=("换型下发必须人工确认",),
        template_hint="PROC-TPL-001",
    ),
}


def rule_pack(code: str) -> IndustryRulePack | None:
    """返回行业专用规则包；非 C 类智能体返回 None。

    工厂（factory.py）通过本函数判断编号是否需要走行业规则注入路径。
    """
    return INDUSTRY_RULE_PACKS.get(code)