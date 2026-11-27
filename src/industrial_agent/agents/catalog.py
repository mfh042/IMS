"""阶段 5.8 / 6.8 / 12.5：13 类智能体规格、三级编排路由与第一版收敛范围。

本模块是 13 类智能体的**唯一事实来源**（Single Source of Truth），其他所有模块
（factory / orchestrator / api）都从这里取数据，绝不重复硬编码编号或路由。
集中定义四类信息：
- 规格：编号、名称、框架归属、载体、输入、工具、输出、依赖知识库、人工确认边界、风险上限
- 路由：incident_type -> 子图 -> 具体智能体编号（阶段 6.8）
- 行业注入：C1–C3 以规则/模板增强方式注入通用子图
- 范围：第一版（阶段 12.5）只启用 A1 / A3 / B1 / B3 / B4，其中 A1 / B1 仅只读取证

修改发布范围只需改各 AgentSpec 的 first_release 字段，下游 FIRST_RELEASE_CODES 自动推导。
"""

from __future__ import annotations

# ---- 标准库 ----
from dataclasses import dataclass
from typing import Literal

# ---- 模型层：知识库枚举 + 风险等级枚举（契约层零内部依赖）----
from industrial_agent.models.knowledge import KnowledgeBase
from industrial_agent.models.plan import RiskLevel

# 框架归属：制造侧 A 系列 / 软件侧 B 系列 / 行业融合 C 系列
FrameworkDomain = Literal["manufacturing", "software", "industry"]
# 子图分类：决定该智能体挂到哪条取证链路，以及注入哪些行业规则
SubGraph = Literal["equipment", "quality", "production", "query", "standalone"]
# 人工确认边界：none=无需确认；dispose=处置需确认；control=控制动作需确认（最严）
ConfirmBoundary = Literal["none", "dispose", "control"]


@dataclass(frozen=True)
class AgentSpec:
    """阶段 5.8 单类智能体的可评审规格。

    frozen=True 保证规格在运行时不可被篡改，是"契约先行"的体现：
    编号、工具、风险上限等都是评审过的硬约束，任何模块只能读不能改。
    """

    code: str                              # 智能体编号（A1–A5 / B1–B5 / C1–C3），全局唯一键
    name: str                              # 英文标识，对应 specialists.py 里的类与 AgentFinding.agent_name
    title: str                             # 中文标题，用于前端展示与评审
    domain: FrameworkDomain                # 框架归属（manufacturing / software / industry）
    deployment: str                        # 部署载体说明（在哪个系统/工位落地）
    inputs: tuple[str, ...]                # 需要的输入字段（从 incident.entity_refs 取）
    tools: tuple[str, ...]                 # 允许调用的工具名白名单（超范围工具在注册表层被拒）
    outputs: tuple[str, ...]               # 产出物说明（评审用，非强制校验）
    knowledge_bases: tuple[KnowledgeBase, ...]   # 依赖的知识库（决定检索范围）
    required_knowledge: tuple[str, ...]    # 必需的知识元数据维度（缺则标记 data_gaps）
    subgraph: SubGraph                     # 归属子图，决定路由与行业注入
    confirm_boundary: ConfirmBoundary      # 人工确认边界（安全关键）
    risk_ceiling: RiskLevel                # 风险上限：该智能体能触达的最高风险等级
    first_release: bool                    # 是否纳入第一版最小可用范围
    first_release_readonly: bool = False   # 第一版中是否仅只读取证（A1/B1 为 True，写工具被禁）

    def to_dict(self) -> dict:
        """序列化为普通 dict，供 API 对外暴露 13 类智能体清单（catalog_summary 使用）。"""
        return {
            "code": self.code,
            "name": self.name,
            "title": self.title,
            "domain": self.domain,
            "deployment": self.deployment,
            "inputs": list(self.inputs),
            "tools": list(self.tools),
            "outputs": list(self.outputs),
            "knowledge_bases": list(self.knowledge_bases),
            "required_knowledge": list(self.required_knowledge),
            "subgraph": self.subgraph,
            "confirm_boundary": self.confirm_boundary,
            "risk_ceiling": self.risk_ceiling,
            "first_release": self.first_release,
            "first_release_readonly": self.first_release_readonly,
        }


# 13 类智能体规格总表：编号 → AgentSpec。
# 按 A（制造装备侧）/ B（软件服务侧）/ C（行业融合）三组组织，每组内按编号升序。
# 注意：first_release_readonly=True 的 A1/B1 在第一版只允许读取证，其写工具
# （bind_codes / load_esop 等）会被工具注册表拦截，不会真正执行写入。
AGENT_CATALOG: dict[str, AgentSpec] = {
    # ---------- 人工智能+制造：装备侧 A1–A5 ----------
    "A1": AgentSpec(
        code="A1",
        name="station_assembly",
        title="工位装配智能体",
        domain="manufacturing",
        deployment="智能装配工作台、人机协作产线",
        inputs=("work_order_id", "device_id", "material_id", "batch_id"),
        tools=(
            "query_work_order",
            "query_device_status",
            "query_material_stock",
            "query_batch",
            "load_esop",
            "bind_codes",
        ),
        outputs=("装配引导", "扭矩/检测采集", "装配履历", "异常上报"),
        knowledge_bases=("process", "equipment"),
        required_knowledge=("process", "version"),
        subgraph="production",
        confirm_boundary="control",
        risk_ceiling="high",
        first_release=True,
        first_release_readonly=True,
    ),
    "A2": AgentSpec(
        code="A2",
        name="material_delivery",
        title="物料配送智能体",
        domain="manufacturing",
        deployment="CTU 库房、AGV 系统、线边库",
        inputs=("work_order_id", "material_id", "batch_id"),
        tools=("query_work_order", "query_material_stock", "query_batch", "adjust_delivery_route"),
        outputs=("备料/补料/配送建议", "路径调整建议", "异常上报"),
        knowledge_bases=("process", "trace"),
        required_knowledge=("process",),
        subgraph="production",
        confirm_boundary="control",
        risk_ceiling="high",
        first_release=False,
    ),
    "A3": AgentSpec(
        code="A3",
        name="quality_trace_station",
        title="质量追溯智能体",
        domain="manufacturing",
        deployment="检测工位、MES/MOM 追溯系统",
        inputs=("product_code", "batch_id", "defect_code"),
        tools=("query_quality_record", "query_batch"),
        outputs=("码批绑定记录", "工步记录", "追溯链", "异常批次定位"),
        knowledge_bases=("quality", "trace"),
        required_knowledge=("alarm_code",),
        subgraph="quality",
        confirm_boundary="dispose",
        risk_ceiling="high",
        first_release=True,
    ),
    "A4": AgentSpec(
        code="A4",
        name="line_dispatch",
        title="产线调度智能体",
        domain="manufacturing",
        deployment="生产管控平台、人机协作产线",
        inputs=("line_id", "work_order_id", "device_id"),
        tools=("query_work_order", "query_device_status", "create_dispatch_suggestion"),
        outputs=("瓶颈与异常节点识别", "动态调度建议"),
        knowledge_bases=("equipment", "process"),
        required_knowledge=("device_type",),
        subgraph="production",
        confirm_boundary="control",
        risk_ceiling="high",
        first_release=False,
    ),
    "A5": AgentSpec(
        code="A5",
        name="training_teaching",
        title="实训教学智能体",
        domain="manufacturing",
        deployment="柔性装配/视觉检测/AGV-CTU/PLC/机器人实训装备",
        inputs=("trainee_id", "task_id"),
        tools=("training_score",),
        outputs=("任务发布", "过程指导", "故障模拟", "自动评分", "能力画像"),
        knowledge_bases=("process",),
        required_knowledge=("version",),
        subgraph="standalone",
        confirm_boundary="none",
        risk_ceiling="low",
        first_release=False,
    ),
    # ---------- 人工智能+软件：软件侧 B1–B5 ----------
    "B1": AgentSpec(
        code="B1",
        name="assembly_entry",
        title="装配智能体入口",
        domain="software",
        deployment="工位软件 / E-SOP 系统",
        inputs=("work_order_id", "product_code"),
        tools=("query_work_order", "load_esop", "bind_codes"),
        outputs=("工艺问答", "E-SOP 自动加载", "可追溯装配履历"),
        knowledge_bases=("process",),
        required_knowledge=("process", "version"),
        subgraph="query",
        confirm_boundary="none",
        risk_ceiling="medium",
        first_release=True,
        first_release_readonly=True,
    ),
    "B2": AgentSpec(
        code="B2",
        name="logistics_service",
        title="物流智能体服务",
        domain="software",
        deployment="WMS / WCS",
        inputs=("work_order_id", "material_id", "batch_id"),
        tools=("query_work_order", "query_material_stock", "query_batch", "adjust_delivery_route"),
        outputs=("自动备料", "缺料补料", "路径调整", "与 MES 闭环"),
        knowledge_bases=("trace", "process"),
        required_knowledge=("process",),
        subgraph="production",
        confirm_boundary="control",
        risk_ceiling="high",
        first_release=False,
    ),
    "B3": AgentSpec(
        code="B3",
        name="control_center_service",
        title="智能管控大模型服务",
        domain="software",
        deployment="MES / MOM 生产管控平台",
        inputs=("event", "work_order_id", "device_id", "batch_id", "material_id"),
        tools=(
            "query_device_status",
            "query_recent_alarms",
            "query_work_order",
            "query_material_stock",
            "query_batch",
            "query_quality_record",
        ),
        outputs=("工艺查询", "异常诊断", "报表生成", "追溯分析"),
        knowledge_bases=("process", "quality", "equipment", "trace"),
        required_knowledge=("alarm_code",),
        subgraph="equipment",
        confirm_boundary="control",
        risk_ceiling="high",
        first_release=True,
    ),
    "B4": AgentSpec(
        code="B4",
        name="quality_trace_service",
        title="质量追溯智能体服务",
        domain="software",
        deployment="质量追溯系统 / MES",
        inputs=("product_code", "batch_id", "defect_code"),
        tools=("query_quality_record", "query_batch"),
        outputs=("异常批次/工序定位", "追溯报告", "根因分析辅助"),
        knowledge_bases=("quality", "trace"),
        required_knowledge=("alarm_code",),
        subgraph="quality",
        confirm_boundary="dispose",
        risk_ceiling="high",
        first_release=True,
    ),
    "B5": AgentSpec(
        code="B5",
        name="training_service",
        title="实训智能体服务",
        domain="software",
        deployment="教育科研实训平台 / 数字孪生平台",
        inputs=("trainee_id", "task_id"),
        tools=("training_score",),
        outputs=("任务发布", "过程指导", "故障模拟", "评分与能力画像"),
        knowledge_bases=("process",),
        required_knowledge=("version",),
        subgraph="standalone",
        confirm_boundary="none",
        risk_ceiling="low",
        first_release=False,
    ),
    # ---------- 行业专用（两框架融合）C1–C3 ----------
    "C1": AgentSpec(
        code="C1",
        name="rail_assembly",
        title="轨道装备装配智能体",
        domain="industry",
        deployment="转向架/车体/内饰/制动控制器多品种小批量装配",
        inputs=("work_order_id", "process_id", "material_id", "batch_id", "device_id"),
        tools=("query_work_order", "query_material_stock", "query_quality_record"),
        outputs=("工艺校验", "物料齐套", "质量回溯", "远程运维经验固化"),
        knowledge_bases=("process", "quality", "equipment", "trace"),
        required_knowledge=("process", "version"),
        subgraph="quality",
        confirm_boundary="dispose",
        risk_ceiling="high",
        first_release=False,
    ),
    "C2": AgentSpec(
        code="C2",
        name="aviation_assembly",
        title="航空装配智能体",
        domain="industry",
        deployment="高可靠装配与检测（关键件识别、顺序校验、扭矩曲线、检测留痕）",
        inputs=("work_order_id", "process_id", "device_id", "batch_id", "product_code"),
        tools=("query_work_order", "query_device_status", "query_quality_record"),
        outputs=("指导书检索", "记录归集", "问题定位", "审计追溯"),
        knowledge_bases=("process", "quality", "equipment"),
        required_knowledge=("process", "version"),
        subgraph="query",
        confirm_boundary="dispose",
        risk_ceiling="high",
        first_release=False,
    ),
    "C3": AgentSpec(
        code="C3",
        name="fast_changeover",
        title="快速换型智能体",
        domain="industry",
        deployment="新能源/能源电力/石油工程装备产线新产品导入",
        inputs=("work_order_id", "product_code"),
        tools=("query_work_order", "reuse_process_template"),
        outputs=("模板与参数复用", "缩短新产品导入周期"),
        knowledge_bases=("process",),
        required_knowledge=("process", "version"),
        subgraph="production",
        confirm_boundary="control",
        risk_ceiling="high",
        first_release=False,
    ),
}

# 全部编号的有序元组（按字典插入顺序 = A1..C3），供前端列表与遍历使用
AGENT_CODES: tuple[str, ...] = tuple(AGENT_CATALOG)


def get_spec(code: str) -> AgentSpec:
    """按编号取规格；编号不存在时抛 KeyError（由调用方保证传入合法编号）。"""
    return AGENT_CATALOG[code]


# ---------- 阶段 6.8：incident_type -> 子图 -> 智能体编号 ----------
# 三级路由的第一级：事件类型 → 归属子图。子图决定后续注入哪些行业规则、检索哪些知识库。
INCIDENT_SUBGRAPH: dict[str, SubGraph] = {
    "equipment_exception": "equipment",
    "quality_exception": "quality",
    "material_exception": "production",
    "production_exception": "production",
    "mixed_exception": "production",
    "query": "query",
    "standalone": "standalone",
}

# 三级路由的第二级：事件类型 → 实际调用的智能体编号序列（按取证顺序）。
# 顺序即主图并发调度的顺序：通常主分析脑（B3）在前取全局证据，专员在后补领域证据。
ROUTE_AGENT_MAP: dict[str, tuple[str, ...]] = {
    # 设备/工位报警：诊断主脑 B3 -> A1/A3 取证 -> C1/C2 行业规则
    "equipment_exception": ("B3", "A1", "A3"),
    # 工单阻塞 / 缺料：A2 物料配送 + B2 物流 + A4 调度 + A1 工位状态
    "material_exception": ("A2", "B2", "A4", "A1"),
    "production_exception": ("A4", "A2", "B2", "A1"),
    # 质量不良 / CAPA：A3 追溯 + B4 追溯服务 + B3 根因辅助
    "quality_exception": ("A3", "B4", "B3"),
    # 装配履历 / 工艺问答：B1 入口 + A1 + 工艺知识库
    "query": ("B1", "A1"),
    # 实训任务：独立链路，不入异常主链
    "standalone": ("A5", "B5"),
    "mixed_exception": ("B3", "A2", "B2", "A4", "A3", "A1"),
}

# 三级路由的第三级：按子图注入行业专用规则（C1–C3）。
# 行业规则不新增自由协作 Agent，而是以规则包形式挂到对应子图，由 IndustryRuleAgent 发射。
INDUSTRY_INJECTION: dict[SubGraph, tuple[str, ...]] = {
    "equipment": ("C1",),
    "quality": ("C1", "C2"),
    "production": ("C3",),
    "query": ("C2", "C3"),
    "standalone": (),
}

# ---------- 阶段 3.7：按子图确定知识库归属与必需元数据 ----------
# 子图 → 应检索的知识库范围（主图知识检索节点据此构造检索请求）
SUBGRAPH_KNOWLEDGE_BASES: dict[SubGraph, tuple[KnowledgeBase, ...]] = {
    "equipment": ("equipment", "process"),
    "quality": ("quality", "trace"),
    "production": ("process", "trace"),
    "query": ("process",),
    "standalone": ("process",),
}

SUBGRAPH_REQUIRED_KNOWLEDGE: dict[SubGraph, tuple[str, ...]] = {
    "equipment": ("alarm_code", "device_type"),
    "quality": ("alarm_code",),
    "production": ("process",),
    "query": ("process", "version"),
    "standalone": ("version",),
}

# ---------- 阶段 12.5：第一版最小可用范围 ----------
# 从 AGENT_CATALOG 自动推导：first_release=True 的编号纳入第一版，其余暂不启用。
# 好处是改范围只需动规格表里的布尔字段，这里自动同步，不会出现两处不一致。
FIRST_RELEASE_CODES: frozenset[str] = frozenset(
    code for code, spec in AGENT_CATALOG.items() if spec.first_release
)

# 第一版排除的编号（用于提示"该能力尚未上线"，而非报错）
FIRST_RELEASE_EXCLUDED: frozenset[str] = frozenset(
    code for code, spec in AGENT_CATALOG.items() if not spec.first_release
)


def resolve_codes(
    incident_type: str,
    *,
    release_scope: str = "first",
    include_industry: bool = True,
) -> list[str]:
    """按阶段 6.8 路由 + 阶段 12.5 范围，返回本次实际调用的智能体编号。

    - ``release_scope="first"``：仅保留第一版最小可用范围内的编号（12.5）
    - ``release_scope="all"``：保留路由给出的全部编号
    - ``include_industry=True``：按子图注入 C1–C3 行业专用规则增强（6.8 附录 D）
    """

    codes = list(ROUTE_AGENT_MAP.get(incident_type, ROUTE_AGENT_MAP["mixed_exception"]))

    if include_industry:
        subgraph = INCIDENT_SUBGRAPH.get(incident_type, "production")
        for code in INDUSTRY_INJECTION.get(subgraph, ()):
            if code not in codes:
                codes.append(code)

    if release_scope == "first":
        codes = [code for code in codes if code in FIRST_RELEASE_CODES]
        # 第一版主入口是 B3：若收敛后没有分析主脑，统一回落到 B3 只读诊断
        if not set(codes) & {"B3", "A3", "B4"}:
            codes = ["B3"]
    return codes


def is_readonly(code: str) -> bool:
    """第一版中仅只读取证的智能体（A1 / B1），禁止触发其写动作。"""

    spec = AGENT_CATALOG.get(code)
    return bool(spec and spec.first_release_readonly)


def subgraph_of(incident_type: str) -> SubGraph:
    """事件类型 → 子图；未知类型默认归到 production（最通用的取证链路）。"""
    return INCIDENT_SUBGRAPH.get(incident_type, "production")


def knowledge_bases_for(incident_type: str) -> tuple[KnowledgeBase, ...]:
    """事件类型 → 应检索的知识库列表（供主图知识检索节点使用）。"""
    return SUBGRAPH_KNOWLEDGE_BASES.get(subgraph_of(incident_type), ("process",))


def required_knowledge_for(incident_type: str) -> tuple[str, ...]:
    """事件类型 → 必需的知识元数据维度（缺则在 finding 里标记 data_gaps）。"""
    return SUBGRAPH_REQUIRED_KNOWLEDGE.get(subgraph_of(incident_type), ("process",))


def agent_name_of(code: str) -> str:
    """编号 → 英文 name（与 specialists.py 的类名、AgentFinding.agent_name 对应）。"""
    return AGENT_CATALOG[code].name


def codes_by_subgraph(subgraph: SubGraph) -> tuple[str, ...]:
    """按子图反查归属该子图的全部智能体编号（前端分组展示用）。"""
    return tuple(code for code, spec in AGENT_CATALOG.items() if spec.subgraph == subgraph)


def catalog_summary() -> list[dict]:
    """对外暴露的 13 类智能体清单（评审与 API 使用）。"""

    return [spec.to_dict() for spec in AGENT_CATALOG.values()]