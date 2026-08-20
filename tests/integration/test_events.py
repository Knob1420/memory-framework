"""/events 与 /ingest_doc 端到端：对应 p0-contract 验收前四条中的插件侧。"""

import io

import pytest
from fastapi.testclient import TestClient

from memory.config import Config
from memory.ingestion.service import _seq_cache
from memory.main import create_app

WS = {"X-Workspace": "codegen"}


@pytest.fixture
def client(tmp_path):
    _seq_cache.clear()
    cfg = Config(data_dir=tmp_path)
    cfg.workspaces = ["codegen", "docgen"]
    return TestClient(create_app(cfg))


def _batch(sid, n, start=1, kind="llm_call"):
    return {
        "session_id": sid,
        "events": [
            {"seq": start + i, "ts": "2026-08-20T10:00:00+08:00", "kind": kind, "data": {"n": i}}
            for i in range(n)
        ],
    }


def test_events_store_and_duplicate(client):
    r = client.post("/events", json=_batch("s1", 3), headers=WS)
    assert r.json() == {"stored": 3, "duplicates": 0}
    r = client.post("/events", json=_batch("s1", 3), headers=WS)  # 整批重发
    assert r.json() == {"stored": 0, "duplicates": 3}


def test_session_end_creates_pending(client):
    batch = _batch("s1", 2)
    batch["events"].append({"seq": 3, "ts": "...", "kind": "session_end", "data": {}})
    client.post("/events", json=batch, headers=WS)
    assert [r.id for r in client.app.state.storage.pending()] == ["s1"]


def test_unknown_kind_accepted(client):
    batch = _batch("s1", 1)
    batch["events"][0]["kind"] = "opencode.message.part.updated"  # 透传
    r = client.post("/events", json=batch, headers=WS)
    assert r.status_code == 200  # 收下落盘，不拒收


def test_missing_workspace_rejected(client):
    r = client.post("/events", json=_batch("s1", 1))  # 无头 → 编排门拦截
    assert r.status_code == 400 and r.json()["error"]["code"] == "WORKSPACE_REQUIRED"


def test_ingest_doc_hash_hit(client):
    files = {"file": ("数据表.xlsx", io.BytesIO(b"hello docgen"), "application/octet-stream")}
    r1 = client.post("/ingest_doc", files=files, data={"meta": "{}"}, headers=WS)
    assert r1.json()["hash_hit"] is False

    files2 = {"file": ("改名也命中.xlsx", io.BytesIO(b"hello docgen"), "application/octet-stream")}
    r2 = client.post("/ingest_doc", files=files2, data={"meta": "{}"}, headers=WS)
    assert r2.json() == {"l0_id": r1.json()["l0_id"], "hash_hit": True}  # 同内容同记录
