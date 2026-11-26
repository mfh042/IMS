"""阶段 12 第一版最小可用范围的命令行入口。"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from industrial_agent.config import get_settings
from industrial_agent.graph.orchestrator import IndustrialAgentGraph


def load_event(path: str | None) -> dict[str, Any]:
    if path:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    return {
        "event_id": "evt-20261126-000123",
        "event_type": "eam.device.temperature_high",
        "source_system": "EAM",
        "occurred_at": "2026-11-26T10:20:00+08:00",
        "severity": "high",
        "tenant_id": "factory_a",
        "trace_id": "trace-demo-001",
        "payload": {
            "device_id": "DEVICE-A-001",
            "line_id": "LINE-03",
            "work_order_id": "WO20261126001",
            "alarm_code": "TEMP_HIGH",
            "message": "主轴温度超过阈值 75℃",
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="工业多智能体系统 CLI")
    parser.add_argument("--event", help="事件 JSON 文件路径，缺省使用内置示例")
    parser.add_argument("--thread-id", help="LangGraph 线程 ID，用于人工确认恢复")
    parser.add_argument("--approve", action="store_true", help="恢复并批准待确认动作")
    parser.add_argument("--reject", action="store_true", help="恢复并拒绝待确认动作")
    parser.add_argument("--decision-by", default="maintenance_lead", help="确认人")
    parser.add_argument("--role", action="append", help="确认人角色，可多次传入")
    parser.add_argument("--audit", action="store_true", help="输出审计回放")
    args = parser.parse_args()

    settings = get_settings()
    settings.ensure_dirs()
    agent = IndustrialAgentGraph(settings=settings)

    if args.approve or args.reject:
        if not args.thread_id:
            parser.error("恢复确认必须提供 --thread-id")
        decision = {
            "approved": bool(args.approve),
            "decision_by": args.decision_by,
            "roles": args.role or ["maintenance_lead"],
            "note": "cli decision",
        }
        result = agent.resume(args.thread_id, decision)
    else:
        result = agent.run(load_event(args.event), thread_id=args.thread_id)

    print(result.get("final_answer", "无输出"))
    if args.audit:
        trace_id = (result.get("incident") or {}).get("trace_id", "unknown")
        print("\n--- 审计回放 ---")
        print(agent.replay(trace_id))


if __name__ == "__main__":
    main()