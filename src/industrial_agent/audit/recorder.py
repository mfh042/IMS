"""阶段 9.2 审计记录：内存 + JSONL 落盘，支持按 trace_id 回放。"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime
from pathlib import Path

from industrial_agent.models.execution import AuditRecord


class AuditRecorder:
    def __init__(self, audit_dir: Path | None = None, *, echo: bool = False) -> None:
        self._records: list[AuditRecord] = []
        self._dir = audit_dir
        self._echo = echo
        if audit_dir is not None:
            audit_dir.mkdir(parents=True, exist_ok=True)

    def record(
        self,
        *,
        trace_id: str,
        actor: str,
        action: str,
        roles: list[str] | None = None,
        incident_id: str | None = None,
        target: str | None = None,
        input_snapshot: dict | None = None,
        output_snapshot: dict | None = None,
        risk_level: str | None = None,
        decision: str = "allowed",
        reason: str | None = None,
    ) -> AuditRecord:
        entry = AuditRecord(
            audit_id=f"aud-{uuid.uuid4().hex[:12]}",
            trace_id=trace_id,
            incident_id=incident_id,
            actor=actor,
            roles=roles or [],
            action=action,
            target=target,
            input_snapshot=input_snapshot or {},
            output_snapshot=output_snapshot or {},
            risk_level=risk_level,
            decision=decision,  # type: ignore[arg-type]
            reason=reason,
            created_at=datetime.now(UTC),
        )
        self._records.append(entry)
        self._append_jsonl(entry)
        return entry

    def _append_jsonl(self, entry: AuditRecord) -> None:
        if self._dir is None:
            return
        path = self._dir / f"audit-{entry.created_at:%Y%m%d}.jsonl"
        with path.open("a", encoding="utf-8") as handle:
            handle.write(entry.model_dump_json() + "\n")

    def records(self) -> list[AuditRecord]:
        return list(self._records)

    def replay(self, trace_id: str) -> list[AuditRecord]:
        return [r for r in self._records if r.trace_id == trace_id]

    def dump_json(self, trace_id: str) -> str:
        return json.dumps(
            [r.model_dump(mode="json") for r in self.replay(trace_id)],
            ensure_ascii=False,
            indent=2,
        )

    def clear(self) -> None:
        self._records.clear()