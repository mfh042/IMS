"""阶段 10：服务接口层。

api 包的入口文件：把 app.py 里的 FastAPI 应用实例（app）与工厂函数（create_app）
re-export 到包顶层。外部启动服务时只需：

    uvicorn industrial_agent.api:app --port 8000

或在代码中：

    from industrial_agent.api import app
"""

# 从 app.py 导入：app 是默认实例（生产直接用），create_app 是工厂（测试可注入 mock graph）
from industrial_agent.api.app import app, create_app

# 显式声明公开 API
__all__ = ["app", "create_app"]