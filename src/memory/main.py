"""FastAPI 组装入口，唯一的 uvicorn 目标。"""

import threading

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

    app.include_router(events_router)

    @app.get("/health")
    def health() -> dict:
        return {"status": "ok"}

    if cfg.phoenix_url:  # 拉型采集：配置了 url 才启动同步线程
        from memory.ingestion.phoenix_sync import PhoenixRestReader, PhoenixSyncer

        reader = PhoenixRestReader(cfg.phoenix_url, cfg.phoenix_project)
        threading.Thread(
            target=PhoenixSyncer(app.state.storage, reader, cfg).run,
            daemon=True,
            name="phoenix-sync",
        ).start()

    # 演化调度器：pending 池 → L1（derive_doc 链）
    from memory.evolution.scheduler import run as run_scheduler
    from memory.llm.client import EmbeddingClient

    app.state.embedder = EmbeddingClient(cfg)
    threading.Thread(
        target=run_scheduler,
        args=(app.state.storage, app.state.embedder, cfg.scheduler_interval_s),
        daemon=True,
        name="evolution-scheduler",
    ).start()

    return app


def __getattr__(name: str):
    """惰性创建 app：uvicorn 的 `memory.main:app` 照常工作，测试 import 不触发建库副作用。"""
    if name == "app":
        return create_app()
    raise AttributeError(name)


if __name__ == "__main__":
    uvicorn.run("memory.main:app", reload=True)
