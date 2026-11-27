"""服务入口：事件接入 API + 人工确认回调 + 前端可视化接口。

所有接口统一挂载在 ``/api`` 前缀下；生产环境下前端构建产物（``frontend/dist``）
由本应用同源托管，SPA 路由走 ``/{full_path:path}`` 回退到 ``index.html``。
开发环境下前端由 Vite 单独启动（默认 5173 端口），通过代理转发 ``/api`` 到本服务。

本文件的职责边界：
- 只做 HTTP 层的事：参数校验（Pydantic 模型）、异常转 HTTP 状态码、响应序列化；
- 业务逻辑全部委托给 ``IndustrialAgentGraph``（主图实例），api 层不写业务规则；
- 路由按功能分组（系统信息 / 智能体目录 / 工具样本 / 端到端编排 / 静态托管），
  每组用注释分隔，便于维护。
"""

from __future__ import annotations

# ---- 标准库：路径处理 + 类型标注 ----
from pathlib import Path
from typing import Any

# ---- 第三方：FastAPI Web 框架 ----
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, Field

# ---- 本项目：智能体目录（规格查询 + 只读判定）----
from industrial_agent.agents.catalog import (
    AGENT_CODES,
    get_spec,
    is_readonly,
)
# ---- 本项目：示例事件（前端样本下拉框）----
from industrial_agent.api.samples import SAMPLE_EVENTS
# ---- 本项目：全局配置（环境 / 灰度 / 发布范围 / LLM 开关）----
from industrial_agent.config import get_settings
# ---- 本项目：主图编排器（14 节点 LangGraph 实例）----
from industrial_agent.graph.orchestrator import IndustrialAgentGraph


# ==================== 请求模型（Pydantic，自动校验入参）====================


class EventIn(BaseModel):
    """端到端编排的入参：原始异构事件报文 + 可选 thread_id（恢复中断用）。"""

    event: dict[str, Any]                 # 上游原始报文（WMS/MES/QMS/EAM/MANUAL），由主图节点 1 归一化
    thread_id: str | None = None          # 传则复用已有线程状态（断点续跑），不传则新建线程


class AgentRunIn(BaseModel):
    """单 Agent 模拟的入参：原始报文 + 可选 incident_type（强制指定路由子图）。"""

    event: dict[str, Any]                 # 原始异构报文
    incident_type: str | None = None      # 不传则由正常化器自动判定，传则强制覆盖（便于前端测试非默认路由）


class ConfirmIn(BaseModel):
    """人工确认回调的入参：批准与否 + 决策人 + 角色 + 备注。"""

    thread_id: str                        # 被 interrupt 挂起的线程 ID（与 /api/events 返回的 thread_id 对应）
    approved: bool                        # True=批准执行高风险动作，False=驳回
    decision_by: str                      # 决策人标识（审计追溯用）
    roles: list[str] = Field(default_factory=list)  # 决策人角色（校验是否具备该动作的确认权限）
    note: str | None = None               # 决策备注（可选）


# ==================== 模块级常量 ====================

# 项目根目录：api/app.py 位于 src/industrial_agent/api/，向上 3 层即项目根
PROJECT_ROOT = Path(__file__).resolve().parents[3]
# 前端构建产物目录（生产模式同源托管的静态资源根）
FRONTEND_DIST = PROJECT_ROOT / "frontend" / "dist"

# CORS 白名单：仅放行本地开发端口（Vite 5173 + uvicorn 8000）
# 生产部署需替换为实际前端域名，避免开放任意来源造成跨域风险
ALLOWED_ORIGINS = [
    "http://localhost:5173",
    "http://127.0.0.1:5173",
    "http://localhost:8000",
    "http://127.0.0.1:8000",
]


# ==================== 辅助函数（非路由）====================


def _sanitize_state(result: dict[str, Any]) -> dict[str, Any]:
    """把 LangGraph 返回的状态转为可 JSON 序列化的形态（处理 __interrupt__）。

    LangGraph 在节点调用 interrupt() 挂起时，状态字典里会带一个 ``__interrupt__``
    字段，其值是 ``Interrupt`` 对象（或对象列表），不能直接 JSON 序列化。
    这里取出其 ``.value``（业务真正关心的待确认内容）再塞回字典。
    """

    out = dict(result)
    interrupt = out.pop("__interrupt__", None)
    if interrupt is not None:
        value: Any = interrupt
        # LangGraph 可能返回列表（多个 interrupt），取第一个
        if isinstance(value, list):
            value = value[0] if value else None
        # Interrupt 对象的 .value 才是实际待确认的数据
        if hasattr(value, "value"):
            value = value.value
        out["__interrupt__"] = value
    return out


def _agent_info(agent: IndustrialAgentGraph, code: str) -> dict[str, Any]:
    """组装单个智能体的前端展示信息：catalog 规格 + 只读标记 + 工具详情。

    ``/api/agents`` 列表接口与 ``/api/agents/{code}`` 详情接口共用此函数，
    保证两处返回结构一致。
    """
    spec = get_spec(code)                  # 从全局唯一 catalog 取该编号的规格定义
    info = spec.to_dict()                  # AgentSpec → dict（name/title/子图/职责/工具列表等）
    info["readonly"] = is_readonly(code)   # 第一版是否只读（A1/B1 为 True，前端据此禁用写操作按钮）
    # 工具详情：把 catalog 里声明的工具名映射到工具注册表的元数据（描述/参数/风险级别）
    tool_catalog = {t["name"]: t for t in agent.tools.catalog()}
    info["tool_details"] = [
        {"name": t, **tool_catalog[t]} if t in tool_catalog else {"name": t}
        for t in spec.tools
    ]
    return info


# ==================== 应用工厂 ====================


def create_app(graph: IndustrialAgentGraph | None = None) -> FastAPI:
    """创建并配置 FastAPI 应用实例。

    Args:
        graph: 主图实例。传 None 时内部用 get_settings() 新建一个（生产默认）；
               传入实例用于测试注入（可替换为 mock graph，不启动真实主图）。

    Returns:
        配置好路由、中间件、应用状态的 FastAPI 实例。
    """
    settings = get_settings()              # 加载全局配置（.env / 环境变量）
    settings.ensure_dirs()                 # 确保数据/日志等目录存在
    agent = graph or IndustrialAgentGraph(settings=settings)  # 主图实例：不传则新建

    app = FastAPI(title="工业多智能体系统", version="0.1.0")
    # CORS 中间件：允许白名单来源跨域访问（开发期 Vite 前端 → 本服务必需）
    app.add_middleware(
        CORSMiddleware,
        allow_origins=ALLOWED_ORIGINS,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    # 把主图实例和配置挂到 app.state，各路由通过闭包直接访问
    app.state.agent = agent
    app.state.settings = settings

    # ---------- 系统信息 ----------

    @app.get("/api/health")
    def health() -> dict[str, Any]:
        """健康检查：供负载均衡 / 容器编排探活。"""
        return {"status": "ok", "env": settings.env, "tenant_id": settings.tenant_id}

    @app.get("/api/settings")
    def runtime_settings() -> dict[str, Any]:
        """运行时配置：前端据此渲染灰度开关、发布范围、LLM 状态等。"""
        return {
            "rollout_mode": settings.rollout_mode,           # 灰度模式（shadow/read_only/enforce）
            "agent_release_scope": settings.agent_release_scope,  # 智能体发布范围（first/all）
            "tenant_id": settings.tenant_id,
            "env": settings.env,
            "llm_enabled": settings.llm_enabled,             # LLM 是否启用（关则全规则兜底）
        }

    @app.get("/api/graph")
    def graph_info() -> dict[str, Any]:
        """主图节点清单：供前端绘制编排流程图（节点名 + 顺序）。"""
        return {"nodes": agent.graph_nodes}

    # ---------- 智能体目录与单 Agent 模拟 ----------

    @app.get("/api/agents")
    def list_agents() -> list[dict[str, Any]]:
        """13 类智能体清单：前端智能体列表页数据源。"""
        return [_agent_info(agent, code) for code in AGENT_CODES]

    @app.get("/api/agents/{code}")
    def get_agent(code: str) -> dict[str, Any]:
        """单个智能体详情：前端智能体详情页数据源。"""
        if code not in AGENT_CODES:
            raise HTTPException(status_code=404, detail=f"未知智能体编号：{code}")
        return _agent_info(agent, code)

    @app.post("/api/agents/{code}/run")
    def run_agent(code: str, payload: AgentRunIn) -> dict[str, Any]:
        """单 Agent 模拟：只跑指定智能体（归一化+治理+知识检索+该 Agent 取证），
        返回完整上下文（conclusion/evidence/findings/knowledge/tool_calls）。

        与 /api/events 的区别：不跑规划/执行/记录节点，便于前端逐个观察单个智能体的行为。
        """
        if code not in AGENT_CODES:
            raise HTTPException(status_code=404, detail=f"未知智能体编号：{code}")
        try:
            result = agent.run_single_agent(
                code,
                payload.event,
                incident_type=payload.incident_type,
            )
        except Exception as exc:  # noqa: BLE001 —— 兜底转 500，避免栈信息泄露给前端
            raise HTTPException(status_code=500, detail=f"智能体执行失败：{exc}") from exc
        # run_single_agent 返回 ok=False 表示归一化失败（报文缺关键字段），转 400
        if not result.get("ok"):
            raise HTTPException(
                status_code=400,
                detail=result.get("reason") or "事件归一化失败",
            )
        return result

    # ---------- 工具 / 样本 ----------

    @app.get("/api/tools")
    def tools() -> list[dict[str, Any]]:
        """工具目录：17 个默认工具的元数据（名称/描述/参数/风险级别），前端工具调试页用。"""
        return agent.tools.catalog()

    @app.get("/api/samples")
    def samples() -> list[dict[str, Any]]:
        """示例事件列表（仅 key/label/incident_type，不含完整报文）：前端样本下拉框。"""
        return [
            {"key": s["key"], "label": s["label"], "incident_type": s["incident_type"]}
            for s in SAMPLE_EVENTS
        ]

    @app.get("/api/samples/{key}")
    def sample_event(key: str) -> dict[str, Any]:
        """单个示例事件的完整原始报文：前端点选样本后加载，填入请求体。"""
        for s in SAMPLE_EVENTS:
            if s["key"] == key:
                return s
        raise HTTPException(status_code=404, detail=f"未知样本：{key}")

    # ---------- 端到端编排 ----------

    @app.post("/api/events")
    def ingest(payload: EventIn) -> dict[str, Any]:
        """事件接入主入口：跑完整 14 节点主图。

        若主图在高风险动作处 interrupt 挂起，返回的状态里会带 __interrupt__（待确认项），
        前端据此弹出确认对话框，用户确认后调 /api/confirms/{thread_id} 恢复执行。
        """
        result = agent.run(payload.event, thread_id=payload.thread_id)
        return _sanitize_state(result)

    @app.post("/api/confirms/{thread_id}")
    def confirm(thread_id: str, payload: ConfirmIn) -> dict[str, Any]:
        """人工确认回调：把审批结果注入被挂起的线程，恢复执行剩余节点。

        安全要点：确认结果由 executor.ConfirmStore 管理，TTL 3600s 过期即失效，
        严禁默认同意——超时未确认的高风险动作必须重新走审批。
        """
        try:
            result = agent.resume(
                thread_id,
                {
                    "approved": payload.approved,
                    "decision_by": payload.decision_by,
                    "roles": payload.roles,
                    "note": payload.note,
                },
            )
        except Exception as exc:  # noqa: BLE001 —— resume 失败通常是线程不存在/已过期，转 400
            raise HTTPException(status_code=400, detail=f"恢复执行失败：{exc}") from exc
        return _sanitize_state(result)

    @app.get("/api/confirms/pending")
    def pending_confirms() -> list[dict[str, Any]]:
        """当前待确认项列表：前端确认中心轮询数据源。"""
        return [c.model_dump(mode="json") for c in agent.confirms.list_pending()]

    @app.get("/api/incidents/open")
    def open_incidents() -> list[dict[str, Any]]:
        """当前未关闭的事件列表：治理层 incident_registry 的开放事件。"""
        return [i.model_dump(mode="json") for i in agent.governance.open_incidents()]

    @app.get("/api/audit/{trace_id}")
    def audit(trace_id: str) -> dict[str, Any]:
        """按 trace_id 回放整条审计链路：前端审计详情页数据源。

        trace_id 来自事件报文（归一化时透传），整条主图的所有节点动作都会带它写入审计表。
        """
        records = [r.model_dump(mode="json") for r in agent.audit.replay(trace_id)]
        if not records:
            raise HTTPException(status_code=404, detail="未找到该 trace_id 的审计记录")
        return {"trace_id": trace_id, "records": records}

    # ---------- 前端静态托管（生产） ----------

    @app.get("/{full_path:path}")
    def spa(full_path: str) -> Any:
        """SPA 回退路由：生产模式同源托管前端构建产物。

        规则：
        1. 若 frontend/dist 不存在 → 返回 404 提示（需先 npm run build）；
        2. 若请求路径对应一个真实静态文件 → 返回该文件；
        3. 否则（SPA 前端路由，如 /agents/B3）→ 返回 index.html 交给前端路由处理。
        注意：此路由必须放在所有 /api 路由之后注册，否则会拦截 API 请求。
        """
        if not FRONTEND_DIST.exists():
            return JSONResponse(
                status_code=404,
                content={
                    "detail": "前端构建产物不存在。请运行 `npm run build`（在 frontend/ 目录），"
                    "或在开发模式下启动 Vite：`npm run dev`。"
                },
            )
        target = FRONTEND_DIST / full_path
        # 请求的是真实静态文件（js/css/图片等），直接返回
        if full_path and target.is_file():
            return FileResponse(target)
        # SPA 路由：回退到 index.html，由前端路由接管
        index = FRONTEND_DIST / "index.html"
        if index.is_file():
            return FileResponse(index)
        raise HTTPException(status_code=404, detail="前端入口文件缺失")

    return app


# 模块级默认实例：uvicorn industrial_agent.api:app 直接启动此实例
app = create_app()