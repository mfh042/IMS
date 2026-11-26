"""阶段 10 服务入口：事件接入 API + 人工确认回调。"""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from industrial_agent.config import get_settings
from industrial_agent.graph.orchestrator import IndustrialAgentGraph


class EventIn(BaseModel):
    event: dict[str, Any]
    thread_id: str | None = None


class ConfirmIn(BaseModel):
    thread_id: str
    approved: bool
    decision_by: str
    roles: list[str] = Field(default_factory=list)
    note: str | None = None


def create_app(graph: IndustrialAgentGraph | None = None) -> FastAPI:
    settings = get_settings()
    settings.ensure_dirs()
    agent = graph or IndustrialAgentGraph(settings=settings)

    app = FastAPI(title="工业多智能体系统", version="0.1.0")
    app.state.agent = agent

    @app.get("/health")
    def health() -> dict[str, Any]:
        return {"status": "ok", "env": settings.env, "tenant_id": settings.tenant_id}

    @app.get("/tools")
    def tools() -> list[dict[str, Any]]:
        return agent.tools.catalog()

    @app.get("/incidents/open")
    def open_incidents() -> list[dict[str, Any]]:
        return [i.model_dump(mode="json") for i in agent.governance.open_incidents()]

    @app.get("/confirms/pending")
    def pending_confirms() -> list[dict[str, Any]]:
        return [c.model_dump(mode="json") for c in agent.confirms.list_pending()]

    @app.post("/events")
    def ingest(payload: EventIn) -> dict[str, Any]:
        result = agent.run(payload.event, thread_id=payload.thread_id)
        return {
            "final_answer": result.get("final_answer"),
            "incident": result.get("incident"),
            "plan": result.get("resolution_plan"),
            "confirm_status": result.get("confirm_status"),
            "executed_actions": result.get("executed_actions", []),
        }

    @app.post("/confirms/{thread_id}")
    def confirm(thread_id: str, payload: ConfirmIn) -> dict[str, Any]:
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
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(status_code=400, detail=f"恢复执行失败：{exc}") from exc
        return {
            "final_answer": result.get("final_answer"),
            "confirm_status": result.get("confirm_status"),
            "executed_actions": result.get("executed_actions", []),
        }

    @app.get("/audit/{trace_id}")
    def audit(trace_id: str) -> dict[str, Any]:
        records = [r.model_dump(mode="json") for r in agent.audit.replay(trace_id)]
        if not records:
            raise HTTPException(status_code=404, detail="未找到该 trace_id 的审计记录")
        return {"trace_id": trace_id, "records": records}

    return app


app = create_app()