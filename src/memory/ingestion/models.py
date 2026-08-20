"""信封与 kind 词汇表：/events 与 OTLP receiver 两个门的公共数据格式。

契约：docs/contracts/p0-contract.md §1（含透传约定）。
kind 开放枚举——未知值收下落盘（transport 层打警告），此处不做取值校验。
"""

from pydantic import BaseModel

# 核心 kind（代码分支 + 两场景公共语义）；透传事件用 "<source>.<原生事件名>"。
KNOWN_KINDS = frozenset(
    {
        "session_start",
        "llm_call",
        "tool_call",
        "file_write",
        "hitl",
        "error",
        "session_end",
    }
)


class Event(BaseModel):
    seq: int
    ts: str  # ISO8601
    kind: str
    data: dict = {}


class EventsRequest(BaseModel):
    session_id: str
    events: list[Event]
