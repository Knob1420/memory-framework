"""span → 信封事件的统一映射（otel-mapping.md 的唯一实现，不允许第二份）。

输入是归一化的 span dict：{name, span_id, parent_id, start_ns, attrs}。
适配器：Phoenix REST 同步器（Arrow 行→dict）。词表已对真实 tc03 数据核对。
"""

from datetime import UTC, datetime

from memory.ingestion.models import Event

# 11 个 stage 名全部出现在 tc03 真实数据中（2026-08-24 核对）
_KNOWN_STAGES = {
    "parse_template",
    "wait_parse_review",
    "extract_reference",
    "wait_extraction_rules",
    "summary",
    "plan",
    "stage2_loop",
    "partitioned_stage2",
    "wait_match_review",
    "generate_document",
    "done",
}


def _iso(ns: int) -> str:
    return datetime.fromtimestamp(ns / 1e9, tz=UTC).isoformat()


def map_spans(spans: list[dict]) -> list[Event]:
    """按 start_ns 排序派生 seq + kind 映射。同批 span 全量重发 → 派生相同 seq（幂等根基）。"""
    events = []
    for seq, sp in enumerate(sorted(spans, key=lambda s: s["start_ns"]), start=1):
        name, attrs = sp["name"], sp["attrs"]
        if name == "llm_call":
            kind, data = (
                "llm_call",
                {
                    "tokens_in": attrs.get("llm.token_count.prompt"),
                    "tokens_out": attrs.get("llm.token_count.completion"),
                    "docgen": attrs.get("docgen"),  # call_site/轮次/重放摘要等领域负载
                },
            )
        elif name.startswith("tool:"):
            kind, data = "tool_call", {"name": name[5:]}
        elif name == "done":
            kind, data = "session_end", {}
        elif name in _KNOWN_STAGES:
            kind, data = "stage", {"name": name}
        else:
            kind, data = f"span.{name}", dict(attrs)  # 透传约定（kind 开放枚举）
        data["_span"] = {"id": sp["span_id"], "parent": sp["parent_id"]}
        events.append(Event(seq=seq, ts=_iso(sp["start_ns"]), kind=kind, data=data))
    return events
