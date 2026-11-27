"""审计与可观测层。

audit 包的入口文件：把 recorder.py 里的 AuditRecorder 类 re-export 到包顶层。
外部（主图 / 执行器 / 工具注册表 / Agent 基类）创建审计记录器时只需：

    from industrial_agent.audit import AuditRecorder
    recorder = AuditRecorder(audit_dir=settings.audit_dir)
"""

# 审计记录器：唯一公开类，负责记录生成 + 内存缓存 + JSONL 落盘 + 按 trace_id 回放
from industrial_agent.audit.recorder import AuditRecorder

# 显式声明公开 API（本包只暴露记录器，不暴露内部辅助）
__all__ = ["AuditRecorder"]