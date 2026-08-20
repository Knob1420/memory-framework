"""OTLP receiver 端到端：自造 OTLP/JSON 样例 → 翻译 → jsonl + pending。

这份样例同时是对接 docgen 的物料（docs/contracts/otel-sample.json 同源）。
"""

import json

import pytest
from fastapi.testclient import TestClient

from memory.config import Config
from memory.ingestion.service import _seq_cache
from memory.main import create_app

# 三个 span：llm_call + tool + done（乱序 + 同 trace），翻译后期望 seq=1,2,3
OTLP_JSON = {
    "resourceSpans": [
        {
            "resource": {
                "attributes": [
                    {"key": "service.name", "value": {"stringValue": "docgen"}},
                ]
            },
            "scopeSpans": [
                {
                    "spans": [
                        {
                            "traceId": "9f2a7c11",
                            "spanId": "aabb",
                            "name": "done",
                            "startTimeUnixNano": "1753868201999000000",
                            "attributes": [],
                        },
                        {
                            "traceId": "9f2a7c11",
                            "spanId": "8054",
                            "parentSpanId": "864c",
                            "name": "llm_call",
                            "startTimeUnixNano": "1753868201500000000",
                            "attributes": [
                                {"key": "stage", "value": {"stringValue": "plan"}},
                                {
                                    "key": "gen_ai.usage.input_tokens",
                                    "value": {"intValue": "10720"},
                                },
                                {
                                    "key": "gen_ai.usage.output_tokens",
                                    "value": {"intValue": "7411"},
                                },
                            ],
                        },
                        {
                            "traceId": "9f2a7c11",
                            "spanId": "162a",
                            "parentSpanId": "8054",
                            "name": "tool",
                            "startTimeUnixNano": "1753868201501000000",
                            "attributes": [
                                {"key": "tool.name", "value": {"stringValue": "tool_save_plan"}},
                            ],
                        },
                    ]
                }
            ],
        }
    ]
}


@pytest.fixture
def client(tmp_path):
    _seq_cache.clear()
    cfg = Config(data_dir=tmp_path)
    cfg.workspaces = ["codegen", "docgen"]
    app = create_app(cfg)
    return TestClient(app), tmp_path


def test_translate_and_persist(client):
    c, tmp = client
    r = c.post("/otlp/v1/traces", json=OTLP_JSON, headers={"content-type": "application/json"})
    assert r.status_code == 200 and r.content == b""  # 空 protobuf

    events = sorted_events(tmp)
    assert [e["kind"] for e in events] == ["llm_call", "tool_call", "session_end"]
    assert [e["seq"] for e in events] == [1, 2, 3]  # startTime 排序派生
    llm = events[0]
    assert llm["data"]["stage"] == "plan" and llm["data"]["tokens_in"] == 10720
    assert llm["data"]["_span"]["id"] == "8054"
    # done → session_end → l0_records 出现 pending（trace_id 即主键）
    assert [rec.id for rec in app_state_pending(c)] == ["9f2a7c11"]


def test_resend_idempotent(client):
    c, tmp = client
    c.post("/otlp/v1/traces", json=OTLP_JSON, headers={"content-type": "application/json"})
    c.post("/otlp/v1/traces", json=OTLP_JSON, headers={"content-type": "application/json"})
    assert len(sorted_events(tmp)) == 3  # 同批重发派生同 seq → 幂等
    assert len(app_state_pending(c)) == 1


def test_unknown_workspace_rejected(client):
    c, _ = client
    bad = json.loads(json.dumps(OTLP_JSON))
    bad["resourceSpans"][0]["resource"]["attributes"][0]["value"]["stringValue"] = "codgen"
    r = c.post("/otlp/v1/traces", json=bad, headers={"content-type": "application/json"})
    assert r.status_code == 400 and "workspace" in r.text


def sorted_events(tmp):
    f = tmp / "docgen/l0/session/9f2a7c11.jsonl"
    return sorted(
        (json.loads(x) for x in f.read_text(encoding="utf-8").splitlines()),
        key=lambda e: e["seq"],
    )


def app_state_pending(c):
    return c.app.state.storage.pending()
