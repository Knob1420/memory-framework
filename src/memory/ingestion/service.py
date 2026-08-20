"""ingestion 服务：store_events（契约 p0-contract §3 六步）。

不做的事：不解析 kind 语义、不排序（消费端按 seq 排）、不派生（演化引擎异步消费 pending）。
"""

from memory.ingestion.models import Event
from memory.storage.engine import Storage

# ponytail: seq 集合内存缓存（进程重启丢失由首包重读 jsonl 兜底），量大了再持久化
_seq_cache: dict[str, set[int]] = {}


def store_events(
    storage: Storage, workspace: str, session_id: str, events: list[Event]
) -> tuple[int, int]:
    """幂等落盘。返回 (stored, duplicates)；session_end 到达时补 l0_records 行。"""
    seen = _seq_cache.get(session_id)
    if seen is None:  # 首次见到该 session：从 jsonl 重建已见 seq（进程重启的兜底也是它）
        seen = {e.seq for e in storage.read_session(workspace, session_id)}
        _seq_cache[session_id] = seen

    new: list[Event] = []
    duplicates = 0
    for e in events:
        if e.seq in seen:
            duplicates += 1
        else:
            seen.add(e.seq)
            new.append(e)

    if new:
        storage.put_session(workspace, session_id, new)

    if any(e.kind == "session_end" for e in events):
        storage.ensure_session_record(workspace, session_id)  # INSERT OR IGNORE，重发幂等

    return len(new), duplicates
