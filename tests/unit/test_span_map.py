"""span_map 统一映射：分支、排序派生 seq、树保留、同批重发幂等根基。

词表已对真实 tc03 数据核对（tool:* 前缀、llm.token_count.* 键）。
"""

from memory.ingestion.span_map import map_spans


def _sp(name, span_id="01", parent=None, ns=1000, attrs=None):
    return {
        "name": name,
        "span_id": span_id,
        "parent_id": parent,
        "start_ns": ns,
        "attrs": attrs or {},
    }


def test_branches():
    events = map_spans(
        [
            _sp(
                "llm_call",
                ns=1,
                attrs={
                    "llm.token_count.prompt": 10720,
                    "docgen.llm.call_site": "agent_loop.stage2",
                    "llm.input_messages.0.message.role": "user",
                    "llm.input_messages.0.message.content": "开始",
                    "llm.output_messages.0.message.role": "assistant",
                    "llm.output_messages.0.message.content": "好的",
                    "docgen.llm.thinking": "推理过程",
                },
            ),
            _sp("tool:tool_save_plan", ns=2),
            _sp("plan", ns=3),  # stage 名单内
            _sp("weird_thing", ns=4),  # 透传
        ]
    )
    assert [e.kind for e in events] == ["llm_call", "tool_call", "stage", "span.weird_thing"]
    assert events[0].data["tokens_in"] == 10720
    assert events[0].data["call_site"] == "agent_loop.stage2"
    assert events[0].data["messages_in"] == [{"role": "user", "content": "开始"}]
    assert events[0].data["messages_out"] == [{"role": "assistant", "content": "好的"}]
    assert events[0].data["thinking"] == "推理过程"
    assert events[1].data["name"] == "tool_save_plan"
    assert events[3].data == {"_span": {"id": "01", "parent": None}}  # 透传无额外 attrs


def test_done_and_tree():
    events = map_spans([_sp("llm_call", "aa", "bb", ns=1), _sp("done", "cc", ns=9)])
    assert events[1].kind == "session_end"
    assert events[0].data["_span"] == {"id": "aa", "parent": "bb"}


def test_sort_derives_seq():
    events = map_spans([_sp("done", ns=30), _sp("llm_call", ns=10), _sp("tool:tool_x", ns=20)])
    assert [e.seq for e in events] == [1, 2, 3]  # 按 start_ns 排序，与输入顺序无关
    # 同一批重发 → 相同排序 → 相同 seq（store_events 幂等的根基）
    again = map_spans([_sp("done", ns=30), _sp("llm_call", ns=10), _sp("tool:tool_x", ns=20)])
    assert [e.seq for e in again] == [e.seq for e in events]
