"""调度器：pending 池 → run_once → derived/failed 状态机。

FakeEmbedding 提供向量；成功路（md 文档）+ 失败路（未知格式）互不影响。
"""

from memory.config import Config
from memory.evolution.scheduler import run_once
from memory.llm.client import FakeEmbedding
from memory.storage.engine import Storage

MD = "# 标题\n\n" + "正文内容。" * 100


def make_store(tmp_path):
    cfg = Config(data_dir=tmp_path)
    cfg.embedding_dim = 8
    return Storage(cfg)


def test_run_once_derives_and_marks(tmp_path):
    store = make_store(tmp_path)
    rec = store.put_doc(MD.encode(), {"filename": "a.md"}, "docgen")

    n = run_once(store, FakeEmbedding())
    assert n == 1
    assert store.pending() == []  # 不再 pending
    state = store.db.execute(
        "SELECT derived_state FROM l0_records WHERE id=?", (rec.id,)
    ).fetchone()[0]
    assert state == "derived"
    chunks = store.db.execute("SELECT count(*) FROM doc_chunks").fetchone()[0]
    assert chunks > 0


def test_run_once_failure_isolated(tmp_path):
    store = make_store(tmp_path)
    good = store.put_doc(MD.encode(), {"filename": "good.md"}, "docgen")
    bad = store.put_doc(b"\x00\x01", {"filename": "bad.xyz"}, "docgen")

    n = run_once(store, FakeEmbedding())
    assert n == 1  # 坏文档失败，好文档照常
    states = dict(store.db.execute("SELECT id, derived_state FROM l0_records").fetchall())
    assert states[good.id] == "derived"
    assert states[bad.id] == "failed"
    err = store.db.execute("SELECT error FROM l0_records WHERE id=?", (bad.id,)).fetchone()[0]
    assert err  # 错误留痕
    # failed 不回 pending，下一轮不再捞
    assert run_once(store, FakeEmbedding()) == 0
