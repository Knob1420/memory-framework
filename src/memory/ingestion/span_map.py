"""span → 信封事件的统一映射（otel-mapping.md 的唯一实现，不允许第二份）。

输入是归一化的 span dict：{name, span_id, parent_id, start_ns, attrs}。
适配器：Phoenix REST 同步器（Arrow / GraphQL 导出 JSON → dict）。
llm_call 保留完整对话（messages_in/out + thinking）——P2 经验演化的原料。
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


def _messages(attrs: dict, prefix: str) -> list[dict] | None:
    """从拍平的 dotted key 里重组消息列表（llm.input_messages.N.message.role/content）。"""
    idxs = {
        int(k.split(".")[2])
        for k in attrs
        if k.startswith(f"{prefix}.") and k.endswith((".role", ".content"))
    }
    if not idxs:
        return None
    return [
        {
            "role": attrs.get(f"{prefix}.{i}.message.role"),
            "content": attrs.get(f"{prefix}.{i}.message.content"),
        }
        for i in sorted(idxs)
    ]


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
                    "messages_in": _messages(attrs, "llm.input_messages"),
                    "messages_out": _messages(attrs, "llm.output_messages"),
                    "thinking": attrs.get("docgen.llm.thinking"),
                    "call_site": attrs.get("docgen.llm.call_site"),
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
