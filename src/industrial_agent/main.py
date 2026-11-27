"""阶段 12 第一版最小可用范围的命令行入口。

本模块是工业多智能体系统的本地调试入口（生产入口为 api/app.py）。
提供两类能力：
  1. 事件接入：从 JSON 文件或内置示例读取事件，驱动主图 run 一次完整处置流程；
  2. 人工确认恢复：当主图因高风险动作调用 interrupt() 挂起时，用 --approve / --reject
     携带决策值 resume 指定线程，使图从挂起节点继续执行。

依赖：
  - industrial_agent.config.get_settings：加载配置（含 LLM、灰度、审计/知识目录）
  - industrial_agent.graph.orchestrator.IndustrialAgentGraph：14 节点 LangGraph 主图
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from industrial_agent.config import get_settings
from industrial_agent.graph.orchestrator import IndustrialAgentGraph


def load_event(path: str | None) -> dict[str, Any]:
    """加载待处置的原始事件报文。

    参数:
        path: 事件 JSON 文件路径；为 None 时返回内置示例（设备温度过高）。

    返回:
        原始事件 dict，字段为上游系统（WMS/MES/QMS/EAM/MANUAL）的异构报文，
        后续会由 EventNormalizer 归一化为 StandardEvent。
    """
    if path:
        # 从指定文件读取并解析 JSON 报文
        return json.loads(Path(path).read_text(encoding="utf-8"))
    # 缺省示例：EAM 上报的主轴温度过高告警，用于快速演示/冒烟测试
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
    """CLI 主函数：解析参数 → 构造主图 → run 或 resume → 输出结果/审计。"""
    # ---------- 1. 命令行参数定义 ----------
    parser = argparse.ArgumentParser(description="工业多智能体系统 CLI")
    # --event: 事件 JSON 文件路径，不传则用 load_event 的内置示例
    parser.add_argument("--event", help="事件 JSON 文件路径，缺省使用内置示例")
    # --thread-id: LangGraph 线程 ID。run 时缺省用 trace_id；resume 时必须显式提供
    parser.add_argument("--thread-id", help="LangGraph 线程 ID，用于人工确认恢复")
    # --approve / --reject: 互斥语义的恢复动作，携带布尔决策值 resume 图
    parser.add_argument("--approve", action="store_true", help="恢复并批准待确认动作")
    parser.add_argument("--reject", action="store_true", help="恢复并拒绝待确认动作")
    # --decision-by: 确认人标识，写入审计与 ConfirmRequest.decision_by
    parser.add_argument("--decision-by", default="maintenance_lead", help="确认人")
    # --role: 确认人角色，可多次传入累积为列表；缺省为 maintenance_lead
    parser.add_argument("--role", action="append", help="确认人角色，可多次传入")
    # --audit: 流程结束后按 trace_id 回放审计记录（JSON 输出）
    parser.add_argument("--audit", action="store_true", help="输出审计回放")
    args = parser.parse_args()

    # ---------- 2. 构造配置与主图 ----------
    settings = get_settings()          # 单例配置（含 .env / IAMS_ 环境变量）
    settings.ensure_dirs()             # 创建 var/audit、var/knowledge 等运行时目录
    agent = IndustrialAgentGraph(settings=settings)  # 装配全部子系统并编译 14 节点主图

    # ---------- 3. 加载事件、确定线程 ID ----------
    raw_event = load_event(args.event)
    # 线程 ID 优先用显式传入；否则取事件 trace_id；兜底 "default"
    # 注意：resume 必须用与 run 相同的 thread_id 才能命中同一检查点
    thread_id = args.thread_id or str(raw_event.get("trace_id") or "default")

    # ---------- 4. 执行：恢复模式 or 全新运行 ----------
    if args.approve or args.reject:
        # 恢复模式：必须显式指定 thread_id，否则无法定位挂起的检查点
        if not args.thread_id:
            parser.error("恢复确认必须提供 --thread-id")
        # 构造决策值，作为 Command(resume=...) 的值传回被 interrupt 的节点
        # approved=True/False 由 --approve/--reject 决定；decision_by/roles 用于审计与权限校验
        decision = {
            "approved": bool(args.approve),
            "decision_by": args.decision_by,
            "roles": args.role or ["maintenance_lead"],
            "note": "cli decision",
        }
        result = agent.resume(args.thread_id, decision)
    else:
        # 全新运行：从入口节点 normalize_event 开始跑完整主图
        result = agent.run(raw_event, thread_id=thread_id)

    # ---------- 5. 结果输出 ----------
    # LangGraph 在节点内调用 interrupt() 后，invoke 返回的 state 中会带 __interrupt__
    # 这表示图在 _request_human_confirm 节点挂起，等待人工决策（高风险 control 动作）
    interrupt_payload = result.get("__interrupt__")
    if interrupt_payload:
        # 兼容 LangGraph 不同返回形态：可能是列表，也可能是 Interrupt 对象（带 .value）
        value: Any = interrupt_payload
        if isinstance(value, list):
            value = value[0] if value else None
        if hasattr(value, "value"):
            value = value.value
        # 打印挂起提示，告诉用户有待确认动作以及如何恢复
        print("⚠ 流程已挂起：存在待人工确认的高风险动作")
        if isinstance(value, dict):
            # value 为 ConfirmRequest 的字典形态，打印关键字段供人工判断
            print(f"  确认单号：{value.get('confirm_id')}")
            print(f"  事件号：{value.get('incident_id')}")
            print(f"  动作：{value.get('action_title')}（风险等级：{value.get('risk_level')}）")
            if value.get("risks"):
                print(f"  风险提示：{'; '.join(value['risks'])}")
            print(f"  要求角色：{', '.join(value.get('required_roles', []))}")
            print(f"  过期时间：{value.get('expire_at')}")
        else:
            # 兜底：非字典形态直接打印原始值
            print(f"  {value}")
        print()
        # 给出可直接复制粘贴的恢复命令（批准 / 拒绝）
        print(f"批准并恢复：uv run python -m industrial_agent.main --thread-id {thread_id} --approve")
        print(f"拒绝并恢复：uv run python -m industrial_agent.main --thread-id {thread_id} --reject")
    else:
        # 正常结束：打印 _respond 节点生成的 final_answer（人类可读处置结论）
        print(result.get("final_answer", "无输出"))

    # ---------- 6. 审计回放（可选） ----------
    if args.audit:
        # 从结果 incident 中取 trace_id 回放该次处置的全链路审计记录
        trace_id = (result.get("incident") or {}).get("trace_id", "unknown")
        print("\n--- 审计回放 ---")
        print(agent.replay(trace_id))


if __name__ == "__main__":
    main()