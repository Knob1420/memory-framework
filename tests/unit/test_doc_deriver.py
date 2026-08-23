"""文档链最小闭环：put_doc → derive_doc → doc_chunks 三表齐（主表+FTS+vec）。

FakeEmbedding 提供向量（无 key 可测）；分块/清洗/提取是 rag-clean 移植件，真跑。
"""

import pytest

from memory.config import Config
from memory.evolution.doc.deriver import derive_doc
from memory.llm.client import FakeEmbedding
from memory.storage.engine import Storage

MD = (
    "# 遥测控数据表\n\n概述文字。" + "背景说明。" * 80 + "\n\n"
    "## 指令对照\n\n"
    + "| TCI | 含义 | 值 |\n|--|--|--|\n"
    + "".join(f"| TCI100{i} | 指令{i} | 8E001{i}AA |\n" for i in range(8))
)


@pytest.fixture
def store(tmp_path):
    cfg = Config(data_dir=tmp_path)
    cfg.embedding_dim = 8  # 与 FakeEmbedding 一致
    return Storage(cfg)


def test_doc_chain(store):
    rec = store.put_doc(MD.encode(), {"filename": "遥测控.md"}, "docgen")
    assert rec.hash_hit is False and rec.derived_state == "pending"

    n = derive_doc(rec, store, FakeEmbedding())
    assert n > 0
    store.mark_derived(rec.id)

    rows = store.db.execute("SELECT id,parent_id FROM doc_chunks ORDER BY id").fetchall()
    parents = [r for r in rows if r[1] is None]
    children = [r for r in rows if r[1] is not None]
    assert parents and children and len(rows) == n
    # 同一 parent 的 child 归属一致
    assert all(c[1] in {p[0] for p in parents} for c in children)
    # 三表齐：主表/FTS/vec 行数一致
    fts = store.db.execute("SELECT count(*) FROM doc_chunks_fts").fetchone()[0]
    vec = store.db.execute("SELECT count(*) FROM doc_chunks_vec").fetchone()[0]
    assert fts == vec == len(rows)
    # 状态机
    assert store.pending() == []  # 已 derived，不再 pending
    # 二次派生：整体替换，不翻倍
    derive_doc(rec, store, FakeEmbedding())
    again = store.db.execute("SELECT count(*) FROM doc_chunks").fetchone()[0]
    assert again == n
