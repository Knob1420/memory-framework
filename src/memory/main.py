"""FastAPI 组装入口，唯一的 uvicorn 目标。"""

import uvicorn
from fastapi import FastAPI

from memory.config import load_config
from memory.orchestrator.gate import workspace_gate
from memory.transport.errors import install_error_handlers


def create_app() -> FastAPI:
    cfg = load_config()
    app = FastAPI(title="memory-framework")
    app.middleware("http")(workspace_gate(cfg.workspaces))
    install_error_handlers(app)

    @app.get("/health")
    def health() -> dict:
        return {"status": "ok"}

    # TODO(P0): 注册 /events、/ingest_doc 路由（transport 层）
    # TODO(P0): OTLP receiver /otlp/v1/traces
    return app


app = create_app()

if __name__ == "__main__":
    uvicorn.run("memory.main:app", reload=True)
