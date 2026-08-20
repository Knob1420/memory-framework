"""OTLP receiver（p0-contract §5）。push 型采集：收 OTel 流 → 归一化 span → 统一映射。

transport 层纪律：只做协议解析 + 翻译 + 调 ingestion，不碰 SQL、不调 LLM。
kind 映射的唯一实现在 ingestion/span_map.py（Phoenix 同步器共用）。
"""

import json
from collections import defaultdict

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import Response
from google.protobuf.json_format import ParseDict
from opentelemetry.proto.collector.trace.v1.trace_service_pb2 import (
    ExportTraceServiceRequest,
    ExportTraceServiceResponse,
)
from opentelemetry.proto.trace.v1.trace_pb2 import Span

from memory.ingestion.service import store_events
from memory.ingestion.span_map import map_spans

router = APIRouter()


def _attrs(items) -> dict:
    """OTel KeyValue 列表 → dict（只取已设置的那一个值字段）。"""
    out = {}
    for kv in items:
        which = kv.value.WhichOneof("value")
        if which:
            out[kv.key] = getattr(kv.value, which)
    return out


def _span_to_dict(sp: Span) -> dict:
    """protobuf Span → 归一化 span dict（span_map 的输入契约）。"""
    return {
        "name": sp.name,
        "span_id": sp.span_id.hex(),
        "parent_id": sp.parent_span_id.hex() or None,
        "start_ns": sp.start_time_unix_nano,
        "attrs": _attrs(sp.attributes),
    }


def _fix_ids(doc: dict) -> dict:
    """OTLP/JSON 规范：traceId/spanId 用 hex；protobuf ParseDict 却按 base64 解 bytes。
    JSON 路径先把三个 ID 字段 hex→base64，否则 ID 全部错位（测试抓到的真坑）。"""
    import base64

    for rs in doc.get("resourceSpans", []):
        for ss in rs.get("scopeSpans", []):
            for sp in ss.get("spans", []):
                for k in ("traceId", "spanId", "parentSpanId"):
                    if v := sp.get(k):
                        sp[k] = base64.b64encode(bytes.fromhex(v)).decode()
    return doc


@router.post("/otlp/v1/traces")
async def otlp_traces(request: Request) -> Response:
    body = await request.body()
    try:
        if request.headers.get("content-type", "").startswith("application/json"):
            req = ParseDict(_fix_ids(json.loads(body)), ExportTraceServiceRequest())
        else:  # protobuf（OTLP 默认）
            req = ExportTraceServiceRequest.FromString(body)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"OTLP 解析失败: {e}") from e

    workspaces = request.app.state.workspaces
    storage = request.app.state.storage

    for rs in req.resource_spans:
        ws = _attrs(rs.resource.attributes).get("service.name", "")
        if ws not in workspaces:  # receiver 自查（编排门免检，workspace 在数据体内）
            raise HTTPException(status_code=400, detail=f"未注册的 workspace: {ws!r}")

        groups: dict[str, list[dict]] = defaultdict(list)  # 一个请求可能混多个 trace
        for ss in rs.scope_spans:
            for sp in ss.spans:
                groups[sp.trace_id.hex()].append(_span_to_dict(sp))

        for session_id, spans in groups.items():
            events = map_spans(spans)
            if events:
                store_events(storage, ws, session_id, events)

    # OTLP 协议规定：成功响应是空 protobuf，不是 JSON
    return Response(
        content=ExportTraceServiceResponse().SerializeToString(),
        media_type="application/x-protobuf",
    )
