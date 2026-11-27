"""事件接入层。

ingestion 包的入口文件：把 normalizer.py 里的 EventNormalizer 类 re-export 到包顶层。
外部（主图 orchestrator / 测试代码）使用时只需：

    from industrial_agent.ingestion import EventNormalizer

    normalizer = EventNormalizer(tenant_id="factory_a")
    outcome = normalizer.normalize(raw_event_dict)   # 异构报文 → StandardEvent 或死信
"""

# EventNormalizer：接入层唯一入口，承接主图节点 1 的异构报文标准化调用
from industrial_agent.ingestion.normalizer import EventNormalizer

# 显式声明公开 API：本包只暴露归一化器，内部辅助不导出
__all__ = ["EventNormalizer"]