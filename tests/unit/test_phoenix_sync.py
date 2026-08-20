"""Phoenix 同步器（FakeReader，不碰真库）：完整/半截会话、水位、崩溃重导幂等。

真库的行归一化（PhoenixReader._row）依赖真实 schema，拿到只读账号后另行验证——
这里测的是同步器的调度逻辑（分组/水位/全量导入）。
"""

import json

import pytest

from memory.config import Config
from memory.ingestion.phoenix_sync import PhoenixSyncer
from memory.ingestion.service import _seq_cache
from memory.storage.engine import Storage


class FakeReader:
    """模拟 Phoenix 库：内部一个自增 id 的行表。"""

    def __init__(self):
        self.rows: list[dict] = []
        self._next = 1

    def add(self, trace_id, name, ns):
        self.rows.append(
            {
                "id": self._next,
                "trace_id": trace_id,
                "name": name,
                "span_id": f"s{self._next:04x}",
                "parent_id": None,
                "start_ns": ns,
                "attrs": {},
            }
        )
        self._next += 1

    def new_spans(self, last_id):
        return [r for r in self.rows if r["id"] > last_id]

    def trace_spans(self, trace_id):
        return [r for r in self.rows if r["trace_id"] == trace_id]

    def max_id(self):
        return self._next - 1


@pytest.fixture
def env(tmp_path):
    _seq_cache.clear()
    store = Storage(Config(data_dir=tmp_path))
    reader = FakeReader()
    cfg = Config(data_dir=tmp_path)
    cfg.phoenix_start_from = "all"
    syncer = PhoenixSyncer(store, reader, cfg)
    return store, reader, syncer, tmp_path


def test_incomplete_then_complete(env):
    store, reader, syncer, _tmp = env
    reader.add("T1", "llm_call", ns=1)  # 第一轮：半截，无 done
    imported, held = syncer.poll_once()
    assert (imported, held) == (0, 1)
    assert store.pending() == []  # 未导：jsonl 无、表无行

    reader.add("T1", "done", ns=2)  # 第二轮：done 到了
    imported, held = syncer.poll_once()
    assert (imported, held) == (1, 0)
    events = store.read_session("docgen", "T1")
    assert [e.kind for e in events] == ["llm_call", "session_end"]  # 全量一次导，seq 一次成型
    assert [r.id for r in store.pending()] == ["T1"]


def test_watermark_no_rescan(env):
    store, reader, syncer, _tmp = env
    reader.add("T1", "llm_call", ns=1)
    reader.add("T1", "done", ns=2)
    syncer.poll_once()
    before = len(store.read_session("docgen", "T1"))
    assert syncer.poll_once() == (0, 0)  # 无新 span：不重导、不重查
    assert len(store.read_session("docgen", "T1")) == before


def test_crash_reimport_idempotent(env):
    """崩溃在导入后、存水位前 → 重跑同轮 → 全量重导 → seq 相同 → 幂等去重。"""
    store, reader, syncer, tmp = env
    reader.add("T1", "llm_call", ns=1)
    reader.add("T1", "tool", ns=2)
    reader.add("T1", "done", ns=3)
    syncer.poll_once()
    # 模拟崩溃：水位回退到导入前
    state = json.loads((tmp / "docgen/phoenix_sync.json").read_text(encoding="utf-8"))
    state["last_id"] = 0
    (tmp / "docgen/phoenix_sync.json").write_text(json.dumps(state), encoding="utf-8")
    syncer2 = PhoenixSyncer(store, reader, Config(data_dir=tmp))
    syncer2.poll_once()
    assert len(store.read_session("docgen", "T1")) == 3  # 没有翻倍
    assert len(store.pending()) == 1


def test_state_file_shape(env):
    _, reader, syncer, tmp = env
    reader.add("T1", "llm_call", ns=1)
    syncer.poll_once()
    state = json.loads((tmp / "docgen/phoenix_sync.json").read_text(encoding="utf-8"))
    assert state == {"last_id": 1, "incomplete": ["T1"]}  # 人可读可手改（重置重拉=编辑它）
