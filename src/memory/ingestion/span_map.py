"""span → 信封事件的统一映射（otel-mapping.md 的唯一实现，不允许第二份）。

输入是归一化的 span dict：{name, span_id, parent_id, start_ns, attrs}。
两个适配器喂数据：OTLP receiver（protobuf Span→dict）、Phoenix 同步器（数据库行→dict）。
"""

from datetime import UTC, datetime

from memory.ingestion.models import Event

# ponytail: stage 名单来自 exp-016 单样本，待与 docgen 确认；
# 候选方案是删掉本名单、非锚点全走透传 span.<name>（讨论中未拍板）
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
                    "stage": attrs.get("stage"),
                    "tokens_in": attrs.get("gen_ai.usage.input_tokens"),
                    "tokens_out": attrs.get("gen_ai.usage.output_tokens"),
                },
            )
        elif name == "tool":
            kind, data = "tool_call", {"name": attrs.get("tool.name")}
        elif name == "done":
            kind, data = "session_end", {}
        elif name in _KNOWN_STAGES:
            kind, data = "stage", {"name": name}
        else:
            kind, data = f"span.{name}", dict(attrs)  # 透传约定（kind 开放枚举）
        data["_span"] = {"id": sp["span_id"], "parent": sp["parent_id"]}
        events.append(Event(seq=seq, ts=_iso(sp["start_ns"]), kind=kind, data=data))
    return events
