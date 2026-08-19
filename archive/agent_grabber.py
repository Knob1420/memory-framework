"""
LangGraph/LangChain → LoongSuite Pilot GenAI event schema 抓取器。

参考: https://github.com/alibaba/loongsuite-pilot/blob/main/docs/output-event-schema.md

用法（任选一种）:
    export LANGCHAIN_CALLBACKS_PATH=/abs/path/to/agent_grabber.py
    # 或代码注入:
    from agent_grabber import grabber
    graph = graph.with_config(callbacks=[grabber])

可调 env:
    AGENT_GRABBER_LOG          JSONL 输出路径 (默认 /tmp/agent_messages.jsonl)
    AGENT_GRABBER_AGENT_TYPE   如 langgraph (默认 langgraph)
    AGENT_GRABBER_AGENT_NAME   人读名字
    AGENT_GRABBER_USER_ID      用户标识 (默认 $USER)
    AGENT_GRABBER_INCLUDE_MESSAGES  0/1 是否带敏感 messages 字段 (默认 1)
"""

import hashlib
import json
import os
import socket
import time
from typing import Any

from langchain_core.callbacks import BaseCallbackHandler

LOG_PATH = os.environ.get("AGENT_GRABBER_LOG", "/tmp/agent_messages.jsonl")
AGENT_TYPE = os.environ.get("AGENT_GRABBER_AGENT_TYPE", "langgraph")
AGENT_NAME = os.environ.get("AGENT_GRABBER_AGENT_NAME", "")
USER_ID = os.environ.get("AGENT_GRABBER_USER_ID", os.environ.get("USER", "unknown"))
HOST_NAME = socket.gethostname()
INCLUDE_MESSAGES = os.environ.get("AGENT_GRABBER_INCLUDE_MESSAGES", "1") != "0"

# ponytail: 内存里的小账本 —— trace_id 继承 + tool 计时 + provider 缓存
_trace_root: dict[str, str] = {}     # run_id -> trace_id
_tool_start_ts: dict[str, float] = {}  # tool run_id -> start ts
_model_meta: dict[str, tuple[str, str]] = {}  # run_id -> (provider, request_model)


# ---------- helpers ----------

def _msg_to_dict(m: Any) -> Any:
    if hasattr(m, "model_dump"):
        return m.model_dump()
    if hasattr(m, "dict"):
        return m.dict()
    return {"type": type(m).__name__, "content": str(m)}


def _infer_provider(serialized: dict, name: str = "") -> str:
    """从 langchain 类名/序列化 id 猜 provider。"""
    cls = ""
    ids = serialized.get("id") or serialized.get("name") or []
    if isinstance(ids, list):
        cls = ids[-1] if ids else ""
    else:
        cls = str(ids)
    cls = (cls or name).lower()
    table = [
        ("azurechatopenai", "azure.ai.openai"),
        ("chatopenai", "openai"),
        ("chatanthropic", "anthropic"),
        ("chatbedrock", "aws.bedrock"),
        ("chatvertexai", "gcp.vertex_ai"),
        ("chatgooglegenerativeai", "gcp.gemini"),
        ("chatdeepseek", "deepseek"),
        ("chattongyi", "qwen"),
        ("chatqwen", "qwen"),
        ("chatzhipuai", "zhipu.chatglm"),
        ("chatgroq", "groq"),
        ("chatmistral", "mistral_ai"),
        ("chatcohere", "cohere"),
    ]
    for key, prov in table:
        if key in cls:
            return prov
    return "unknown"


def _hash_messages(messages: list) -> str:
    try:
        blob = json.dumps(messages, ensure_ascii=False, default=str, sort_keys=True)
        return hashlib.sha256(blob.encode()).hexdigest()
    except Exception:
        return ""


def _resolve_trace(run_id: str, parent_run_id: str | None) -> tuple[str, str, str]:
    """trace_id 继承: parent 是根 → parent==trace_id; 否则继承 parent 的 trace_id。"""
    if not parent_run_id or parent_run_id == "None":
        trace = run_id
        _trace_root[run_id] = trace
        return trace, run_id, ""
    trace = _trace_root.get(parent_run_id, parent_run_id)
    _trace_root[run_id] = trace
    return trace, run_id, parent_run_id


def _session_turn(metadata: dict, run_id: str, trace_id: str) -> tuple[str, str, str]:
    """session/turn/step 三级: thread_id → session, trace_id → turn, run_id → step。"""
    md = metadata or {}
    session = md.get("thread_id") or md.get("session_id") or trace_id
    turn = trace_id
    step = run_id
    return session, turn, step


def _emit(event: dict) -> None:
    event.setdefault("time_unix_nano", int(time.time() * 1e9))
    event.setdefault("observed_time_unix_nano", int(time.time() * 1e9))
    event.setdefault("user.id", USER_ID)
    event.setdefault("host.name", HOST_NAME)
    event.setdefault("gen_ai.agent.type", AGENT_TYPE)
    if AGENT_NAME:
        event.setdefault("gen_ai.agent.name", AGENT_NAME)
    with open(LOG_PATH, "a", encoding="utf-8") as f:
        f.write(json.dumps(event, ensure_ascii=False, default=str) + "\n")


# ---------- handler ----------

class Grabber(BaseCallbackHandler):
    """发 LoongSuite 兼容事件: llm.request / llm.response / tool.call / tool.result / other。"""

    # ---- LLM ----
    def on_chat_model_start(self, serialized, messages, *, run_id, parent_run_id,
                            tags, metadata, name, **kw):
        trace_id, span_id, parent_span = _resolve_trace(str(run_id), str(parent_run_id) if parent_run_id else None)
        session, turn, step = _session_turn(metadata or {}, str(run_id), trace_id)
        provider = _infer_provider(serialized or {}, name or "")
        # 请求模型名: langchain 序列化里 kwargs.model_name 或 id 末尾
        req_model = ((serialized or {}).get("kwargs", {}) or {}).get("model_name") \
            or ((serialized or {}).get("kwargs", {}) or {}).get("model") or name or ""
        _model_meta[str(run_id)] = (provider, req_model)

        flat = [_msg_to_dict(m) for batch in messages for m in (batch if isinstance(batch, list) else [batch])]

        event = {
            "event.id": str(run_id),
            "event.name": "llm.request",
            "trace_id": trace_id,
            "span_id": span_id,
            "parent_span_id": parent_span,
            "gen_ai.session.id": session,
            "gen_ai.turn.id": turn,
            "gen_ai.step.id": step,
            "gen_ai.provider.name": provider,
            "gen_ai.request.model": req_model,
            "agent.channel": metadata.get("agent_channel", "api") if metadata else "api",
        }
        if INCLUDE_MESSAGES:
            event["gen_ai.input.messages"] = flat
        event["gen_ai.input.messages_hash"] = _hash_messages(flat)
        _emit(event)

    on_llm_start = on_chat_model_start

    def on_chat_model_end(self, output, *, run_id, parent_run_id, **kw):
        run_id = str(run_id)
        provider, req_model = _model_meta.pop(run_id, ("unknown", ""))
        out_msg = _msg_to_dict(output)
        content = out_msg.get("content", "") if isinstance(out_msg, dict) else str(output)
        # tool_calls / finish_reason 尽力抽
        tool_calls = out_msg.get("tool_calls", []) if isinstance(out_msg, dict) else []
        finish = []
        if tool_calls:
            finish.append("tool_calls")
        elif output is not None and getattr(output, "stop_reason", None):
            finish.append(str(getattr(output, "stop_reason")))
        # llm_output 通常带 token usage
        llm_out = getattr(output, "llm_output", None) or {}
        usage = (llm_out or {}).get("token_usage") or (llm_out or {}).get("usage") or {}

        event = {
            "event.id": run_id,
            "event.name": "llm.response",
            "trace_id": _trace_root.get(run_id, run_id),
            "span_id": run_id,
            "parent_span_id": str(parent_run_id) if parent_run_id else "",
            "gen_ai.provider.name": provider,
            "gen_ai.request.model": req_model,
            "gen_ai.response.model": req_model,
            "gen_ai.response.finish_reasons": finish or ["stop"],
        }
        if usage:
            event.update({
                "gen_ai.usage.input_tokens": usage.get("prompt_tokens") or usage.get("input_tokens", 0),
                "gen_ai.usage.output_tokens": usage.get("completion_tokens") or usage.get("output_tokens", 0),
                "gen_ai.usage.total_tokens": usage.get("total_tokens", 0),
            })
        if INCLUDE_MESSAGES:
            event["gen_ai.output.messages"] = [out_msg]
        _emit(event)

    on_llm_end = on_chat_model_end

    # ---- Tool ----
    def on_tool_start(self, serialized, input_str, *, run_id, parent_run_id,
                      tags, metadata, name, **kw):
        run_id = str(run_id)
        _tool_start_ts[run_id] = time.time()
        trace_id, span_id, parent_span = _resolve_trace(run_id, str(parent_run_id) if parent_run_id else None)
        session, turn, step = _session_turn(metadata or {}, run_id, trace_id)
        event = {
            "event.id": run_id,
            "event.name": "tool.call",
            "trace_id": trace_id,
            "span_id": span_id,
            "parent_span_id": parent_span,
            "gen_ai.session.id": session,
            "gen_ai.turn.id": turn,
            "gen_ai.step.id": step,
            "gen_ai.tool.name": name or (serialized or {}).get("name", "unknown"),
            "gen_ai.tool.call.id": run_id,
        }
        if INCLUDE_MESSAGES:
            event["gen_ai.tool.call.arguments"] = input_str
        _emit(event)

    def on_tool_end(self, output, *, run_id, **kw):
        run_id = str(run_id)
        start = _tool_start_ts.pop(run_id, None)
        duration = int((time.time() - start) * 1000) if start else 0
        event = {
            "event.id": run_id + "-result",
            "event.name": "tool.result",
            "trace_id": _trace_root.get(run_id, run_id),
            "span_id": run_id + "-result",
            "parent_span_id": run_id,
            "gen_ai.tool.call.id": run_id,
            "gen_ai.tool.call.exec.id": run_id,
            "gen_ai.tool.call.duration": duration,
        }
        if INCLUDE_MESSAGES:
            event["gen_ai.tool.call.result"] = str(output)
        _emit(event)

    # ---- Graph 节点 → other (节点名进 agent.*) ----
    def on_chain_start(self, serialized, inputs, *, run_id, parent_run_id,
                       tags, metadata, name, **kw):
        run_id = str(run_id)
        trace_id, span_id, parent_span = _resolve_trace(run_id, str(parent_run_id) if parent_run_id else None)
        session, turn, step = _session_turn(metadata or {}, run_id, trace_id)
        event = {
            "event.id": run_id,
            "event.name": "other",
            "trace_id": trace_id,
            "span_id": span_id,
            "parent_span_id": parent_span,
            "gen_ai.session.id": session,
            "gen_ai.turn.id": turn,
            "gen_ai.step.id": step,
            "agent.node_name": name or (serialized or {}).get("name", ""),
            "agent.node_direction": "start",
        }
        if INCLUDE_MESSAGES and inputs is not None:
            event["agent.node_input"] = _msg_to_dict(inputs) if not isinstance(inputs, (str, list, dict)) else inputs
        _emit(event)

    def on_chain_end(self, outputs, *, run_id, name, **kw):
        run_id = str(run_id)
        event = {
            "event.id": run_id + "-end",
            "event.name": "other",
            "trace_id": _trace_root.get(run_id, run_id),
            "span_id": run_id + "-end",
            "parent_span_id": run_id,
            "agent.node_name": name or "",
            "agent.node_direction": "end",
        }
        if INCLUDE_MESSAGES and outputs is not None:
            event["agent.node_output"] = _msg_to_dict(outputs) if not isinstance(outputs, (str, list, dict)) else outputs
        _emit(event)

    # ---- 错误 ----
    def _error(self, event_name, error, run_id):
        event = {
            "event.id": str(run_id) + "-err",
            "event.name": event_name,
            "trace_id": _trace_root.get(str(run_id), str(run_id)),
            "span_id": str(run_id) + "-err",
            "parent_span_id": str(run_id),
            "error.type": type(error).__name__,
            "error.message": str(error),
        }
        _emit(event)

    def on_llm_error(self, error, *, run_id, **kw): self._error("llm.response", error, run_id)
    def on_tool_error(self, error, *, run_id, **kw): self._error("tool.result", error, run_id)
    def on_chain_error(self, error, *, run_id, **kw): self._error("other", error, run_id)


grabber = Grabber()


# ---- 自检 ----
if __name__ == "__main__":
    # 模拟一次完整 trajectory: llm.request → llm.response(tool_calls) → tool.call → tool.result
    rid = "test-run-001"
    grabber.on_chat_model_start(
        serialized={"id": ["langchain", "chat_openai", "ChatOpenAI"], "kwargs": {"model_name": "gpt-4o"}},
        messages=[[type("M", (), {"model_dump": lambda self: {"role": "user", "content": "hi"}})()]],
        run_id=rid, parent_run_id=None, tags=[], metadata={"thread_id": "sess-1"}, name="ChatOpenAI",
    )
    grabber.on_chat_model_end(
        output=type("O", (), {
            "model_dump": lambda self: {"content": "calling tool", "tool_calls": [{"name": "search"}]},
            "llm_output": {"token_usage": {"prompt_tokens": 5, "completion_tokens": 3, "total_tokens": 8}},
            "stop_reason": None,
        })(),
        run_id=rid, parent_run_id=None,
    )
    grabber.on_tool_start(
        serialized={"name": "search"}, input_str='{"q":"x"}',
        run_id="tool-001", parent_run_id=rid, tags=[], metadata={"thread_id": "sess-1"}, name="search",
    )
    grabber.on_tool_end(output="result-here", run_id="tool-001")

    # 校验
    import pathlib
    lines = pathlib.Path(LOG_PATH).read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 4, f"应有 4 条事件, 实际 {len(lines)}"
    ev_names = [json.loads(l)["event.name"] for l in lines]
    assert ev_names == ["llm.request", "llm.response", "tool.call", "tool.result"], ev_names
    # trace 一致性
    traces = {json.loads(l)["trace_id"] for l in lines}
    assert len(traces) == 1, f"所有事件应共享 trace_id, 实际 {traces}"
    # token
    resp = json.loads(lines[1])
    assert resp["gen_ai.usage.total_tokens"] == 8, resp
    print(f"OK -> {LOG_PATH}  (4 events, trace={list(traces)[0][:8]})")
