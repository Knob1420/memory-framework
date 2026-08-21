"""FastAPI 组装入口，唯一的 uvicorn 目标。"""

import uvicorn
from fastapi import FastAPI

from memory.config import Config, load_config
from memory.orchestrator.gate import workspace_gate
from memory.storage.engine import Storage
from memory.transport.errors import install_error_handlers


def create_app(cfg: Config | None = None) -> FastAPI:
    cfg = cfg or load_config()
    app = FastAPI(title="memory-framework")
    app.middleware("http")(workspace_gate(cfg.workspaces))
    install_error_handlers(app)
    app.state.storage = Storage(cfg)  # 端点经 request.app.state.storage 访问
    app.state.workspaces = cfg.workspaces  # OTLP receiver 自查注册表用

    from memory.transport.events import router as events_router
    from memory.transport.otlp_receiver import router as otlp_router

    app.include_router(events_router)
    app.include_router(otlp_router)

    @app.get("/health")
    def health() -> dict:
        return {"status": "ok"}

    if cfg.phoenix_dsn:  # 拉型采集：配置了 dsn 才启动同步线程
        import threading

        from memory.ingestion.phoenix_sync import PhoenixReader, PhoenixSyncer

        syncer = PhoenixSyncer(app.state.storage, PhoenixReader(cfg.phoenix_dsn), cfg)
        threading.Thread(target=syncer.run, daemon=True, name="phoenix-sync").start()

    return app


def __getattr__(name: str):
    """惰性创建 app：uvicorn 的 `memory.main:app` 照常工作，测试 import 不触发建库副作用。"""
    if name == "app":
        return create_app()
    raise AttributeError(name)


if __name__ == "__main__":
    uvicorn.run("memory.main:app", reload=True)
