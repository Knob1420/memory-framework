"""Storage L0 行为：迁移、jsonl 落盘、session 幂等、doc hash 命中、读口排序。"""

import json

import pytest

from memory.config import Config
from memory.ingestion.models import Event
from memory.storage.engine import Storage


@pytest.fixture
def store(tmp_path):
    return Storage(Config(data_dir=tmp_path))


def _ev(seq, kind="llm_call"):
    return Event(seq=seq, ts="2026-08-18T14:00:00+08:00", kind=kind, data={"n": seq})


def test_migration_creates_table(store):
    v = store.db.execute("PRAGMA user_version").fetchone()[0]
    assert v >= 1
    cols = [r[1] for r in store.db.execute("PRAGMA table_info(l0_records)")]
    assert "derived_state" in cols and "workspace" in cols


def test_put_session_appends_jsonl(store, tmp_path):
    store.put_session("codegen", "s1", [_ev(1), _ev(2)])
    store.put_session("codegen", "s1", [_ev(3)])  # 第二批 append
    lines = (tmp_path / "codegen/l0/session/s1.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(lines) == 3
    assert json.loads(lines[0])["kind"] == "llm_call"


def test_ensure_session_record_idempotent(store):
    store.ensure_session_record("codegen", "s1")
    store.ensure_session_record("codegen", "s1")  # session_end 重发
    rows = store.db.execute("SELECT derived_state FROM l0_records WHERE id='s1'").fetchall()
    assert len(rows) == 1 and rows[0][0] == "pending"


def test_pending_lists_only_pending(store):
    store.ensure_session_record("codegen", "s1")
    assert [r.id for r in store.pending()] == ["s1"]
    assert [r.id for r in store.pending(workspace="docgen")] == []


def test_put_doc_hash_hit(store):
    a = store.put_doc(b"hello", {"filename": "a.xlsx"}, "docgen")
    assert a.hash_hit is False and a.derived_state == "pending"
    b = store.put_doc(b"hello", {"filename": "别的名字.xlsx"}, "docgen")  # 同内容
    assert b.hash_hit is True and b.id == a.id
    rows = store.db.execute("SELECT COUNT(*) FROM l0_records WHERE type='doc'").fetchone()
    assert rows[0] == 1  # 只有一行，文件也只写了一份


def test_read_session_sorts_by_seq(store):
    store.put_session("codegen", "s2", [_ev(3), _ev(1)])  # 乱序写入
    assert [e.seq for e in store.read_session("codegen", "s2")] == [1, 3]
    assert store.read_session("codegen", "不存在") == []
