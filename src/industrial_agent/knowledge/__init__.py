"""数据与知识底座。

knowledge 包的入口文件：把 store.py 里的两个核心符号 re-export 到包顶层。
外部（主图 orchestrator / 测试代码）使用时只需：

    from industrial_agent.knowledge import KnowledgeStore, build_seed_knowledge

    # 方式一：从 JSON 目录加载外部知识
    store = KnowledgeStore(path=settings.knowledge_path)
    # 方式二：无外部库时注入内置种子，跑通链路
    store = KnowledgeStore(chunks=build_seed_knowledge())

    hits = store.retrieve("TEMP_HIGH", device_type="CNC", alarm_code="TEMP_HIGH",
                          knowledge_bases=("equipment",), required_metadata=("device_type",))
"""

# KnowledgeStore：知识底座主体，加载分块 + 加权打分检索 + 元数据/过期校验
from industrial_agent.knowledge.store import KnowledgeStore
# build_seed_knowledge：7 条内置示例分块（覆盖四类知识库），供无外部库时注入
from industrial_agent.knowledge.store import build_seed_knowledge

# 显式声明公开 API：本包暴露知识存储与种子构建，内部辅助不导出
__all__ = ["KnowledgeStore", "build_seed_knowledge"]