"""编排门行为：三分支（缺头/未注册/放行挂上下文）+ 错误格式。"""

from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from memory.orchestrator.gate import workspace_gate


def _app():
    app = FastAPI()
    app.middleware("http")(workspace_gate(["codegen", "docgen"]))

    @app.post("/events")
    def events(request: Request):
        return {"ws": request.state.workspace}

    return TestClient(app)


def test_missing_header():
    r = _app().post("/events", json={})
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "WORKSPACE_REQUIRED"


def test_unknown_workspace():
    r = _app().post("/events", json={}, headers={"X-Workspace": "codgen"})  # 拼错
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "BAD_REQUEST"


def test_pass_through():
    r = _app().post("/events", json={}, headers={"X-Workspace": "codegen"})
    assert r.status_code == 200
    assert r.json() == {"ws": "codegen"}
