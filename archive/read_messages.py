"""
读 LoongSuite schema 格式的 JSONL（agent_grabber.py 产出）。

用法:
    python read_messages.py agent_messages.jsonl
    python read_messages.py agent_messages.jsonl --trace <trace_id>
    python read_messages.py agent_messages.jsonl --transcript out.txt
    python read_messages.py agent_messages.jsonl --stats
"""

import argparse
import collections
import json
import sys
from pathlib import Path

EVENT_ORDER = {"llm.request": 0, "llm.response": 1, "tool.call": 2, "tool.result": 3, "skill.use": 4, "tool.approve": 5, "other": 6}


def load(path: Path):
    with path.open(encoding="utf-8") as f:
        for ln, line in enumerate(f, 1):
            line = line.strip()
            if not line:
                continue
            try:
                yield json.loads(line)
            except json.JSONDecodeError as e:
                print(f"[warn] 第 {ln} 行解析失败: {e}", file=sys.stderr)


def by_key(events, key, value):
    return [e for e in events if e.get(key) == value]


def show_tree(events):
    """按 trace_id 分组, 每个 trace 按 span 父子嵌套打印。"""
    by_trace = collections.defaultdict(list)
    for e in events:
        by_trace[e.get("trace_id", "?")].append(e)

    for tid, evs in by_trace.items():
        print(f"\n=== trace {tid[:16]} ({len(evs)} events) ===")
        children = collections.defaultdict(list)
        for e in evs:
            children[e.get("parent_span_id", "")].append(e)
        # 根 = parent_span_id 为空
        roots = children.pop("", [])
        if not roots:
            roots = evs
        for r in sorted(roots, key=lambda x: x.get("time_unix_nano", 0)):
            _print(r, children, 0)


def _print(node, children, depth):
    pad = "  " * depth
    ev = node.get("event.name", "?")
    span = str(node.get("span_id", ""))[:8]
    name = (node.get("gen_ai.tool.name") or
            node.get("agent.node_name") or
            node.get("gen_ai.skill.name") or "")
    extra = ""
    if ev == "llm.request":
        msgs = node.get("gen_ai.input.messages") or []
        extra = f"in={len(msgs)}msgs model={node.get('gen_ai.request.model','?')}"
    elif ev == "llm.response":
        out = node.get("gen_ai.output.messages") or [{}]
        c = (out[0].get("content", "") if isinstance(out[0], dict) else str(out[0]))[:60]
        tk = node.get("gen_ai.usage.total_tokens")
        extra = f"-> {c!r}" + (f" tok={tk}" if tk else "")
    elif ev == "tool.call":
        args = str(node.get("gen_ai.tool.call.arguments", ""))[:60]
        extra = f"args={args!r}"
    elif ev == "tool.result":
        dur = node.get("gen_ai.tool.call.duration", 0)
        res = str(node.get("gen_ai.tool.call.result", ""))[:60]
        extra = f"-> {res!r} ({dur}ms)"
    elif ev == "other":
        extra = node.get("agent.node_direction", "")
    err = ""
    if node.get("error.type"):
        err = f"  ERR: {node['error.type']} {node.get('error.message','')[:60]}"

    print(f"{pad}[{span}] {ev:14s} {name:15s} {extra}{err}")
    for c in sorted(children.get(node.get("span_id", ""), []), key=lambda x: x.get("time_unix_nano", 0)):
        _print(c, children, depth + 1)


def dump_transcript(events, out_path):
    """拍平成对话流: 一个 trace 一段, 按 step 顺序。"""
    by_trace = collections.defaultdict(list)
    for e in events:
        by_trace[e.get("trace_id", "?")].append(e)

    with out_path.open("w", encoding="utf-8") as f:
        for tid, evs in sorted(by_trace.items()):
            evs.sort(key=lambda x: x.get("time_unix_nano", 0))
            session = next((e.get("gen_ai.session.id") for e in evs if e.get("gen_ai.session.id")), "?")
            f.write(f"\n# trace {tid}  session={session}\n")
            for e in evs:
                ev = e.get("event.name")
                if ev == "llm.request":
                    for m in e.get("gen_ai.input.messages") or []:
                        role = m.get("role", m.get("type", "?")) if isinstance(m, dict) else "?"
                        content = m.get("content", "") if isinstance(m, dict) else str(m)
                        f.write(f"[{role}] {content}\n")
                elif ev == "llm.response":
                    for m in e.get("gen_ai.output.messages") or []:
                        content = m.get("content", "") if isinstance(m, dict) else str(m)
                        f.write(f"[ai] {content}\n")
                elif ev == "tool.call":
                    f.write(f"[tool:{e.get('gen_ai.tool.name','')}] {e.get('gen_ai.tool.call.arguments','')}\n")
                elif ev == "tool.result":
                    f.write(f"[tool_result] {e.get('gen_ai.tool.call.result','')}\n")
    print(f"对话记录 -> {out_path}")


def stats(events):
    print("== 事件计数 ==")
    for k, v in collections.Counter(e["event.name"] for e in events).most_common():
        print(f"  {k:15s} {v}")
    print("\n== Provider 分布 ==")
    for k, v in collections.Counter(e.get("gen_ai.provider.name", "?") for e in events if e["event.name"].startswith("llm")).most_common():
        print(f"  {k:20s} {v}")
    print("\n== Token 汇总 ==")
    in_t = sum(e.get("gen_ai.usage.input_tokens", 0) for e in events)
    out_t = sum(e.get("gen_ai.usage.output_tokens", 0) for e in events)
    print(f"  input={in_t}  output={out_t}  total={in_t+out_t}")
    print(f"\n== trace 数: {len({e.get('trace_id') for e in events})}  session 数: {len({e.get('gen_ai.session.id') for e in events})} ==")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("jsonl")
    ap.add_argument("--trace", help="只看某 trace_id")
    ap.add_argument("--session", help="只看某 session")
    ap.add_argument("--transcript", help="对话流 dump 到此文件")
    ap.add_argument("--stats", action="store_true", help="只看汇总")
    args = ap.parse_args()

    events = list(load(Path(args.jsonl)))
    print(f"共 {len(events)} 条事件\n")

    if args.trace:
        events = by_key(events, "trace_id", args.trace)
    if args.session:
        events = by_key(events, "gen_ai.session.id", args.session)

    if args.stats:
        stats(events)
        return
    if args.transcript:
        dump_transcript(events, Path(args.transcript))
        return
    show_tree(events)


if __name__ == "__main__":
    main()
