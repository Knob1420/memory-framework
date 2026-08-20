"""store_events 行为（p0-contract §3）：幂等、session_end 触发、崩溃重启兜底。"""

import pytest

from memory.config import Config
from memory.ingestion.models import Event
from memory.ingestion.service import _seq_cache, store_events
from memory.storage.engine import Storage


@pytest.fixture
def store(tmp_path):
    _seq_cache.clear()  # 隔离模块级缓存
    return Storage(Config(data_dir=tmp_path))


def _ev(seq, kind="llm_call"):
    return Event(seq=seq, ts="2026-08-18T14:00:00+08:00", kind=kind, data={})


def test_store_and_duplicate(store):
    batch = [_ev(1), _ev(2), _ev(3)]
    assert store_events(store, "codegen", "s1", batch) == (3, 0)
    assert store_events(store, "codegen", "s1", batch) == (0, 3)  # 整批重发
    assert len(store.read_session("codegen", "s1")) == 3


def test_session_end_triggers_pending(store):
    store_events(store, "codegen", "s1", [_ev(1), _ev(2, kind="session_end")])
    assert [r.id for r in store.pending()] == ["s1"]
    store_events(store, "codegen", "s1", [_ev(2, kind="session_end")])  # 重发 end
    assert len(store.pending()) == 1  # 仍一行


def test_no_session_end_no_record(store):
    store_events(store, "codegen", "s2", [_ev(1), _ev(2)])
    assert store.pending() == []  # 无行=会话未结束，演化引擎看不见（设计如此）


def test_cache_rebuild_after_restart(store, tmp_path):
    store_events(store, "codegen", "s3", [_ev(1), _ev(2)])
    _seq_cache.clear()  # 模拟进程重启：缓存丢失
    assert store_events(store, "codegen", "s3", [_ev(2), _ev(3)]) == (1, 1)  # jsonl 兜底重建
    assert [e.seq for e in store.read_session("codegen", "s3")] == [1, 2, 3]
