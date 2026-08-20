"""StorageEngine L0 实现（签名冻结于 docs/contracts/p0-contract.md §4）。

铁律：不调 LLM、影子表（P1）对调用方不存在、所有读写隐含 workspace。
L0 本体在文件系统（jsonl/原始文件），SQLite 只存元数据。
"""

import hashlib
import json
import sqlite3
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from memory.config import Config
from memory.ingestion.models import Event

MIGRATIONS_DIR = Path(__file__).parent / "migrations"


def _vec_blob(vec: list[float]) -> bytes:
    import struct

    return struct.pack(f"{len(vec)}f", *vec)


@dataclass
class L0Record:
    id: str
    type: str
    workspace: str
    path: str
    content_hash: str | None
    derived_state: str
    hash_hit: bool = False  # 瞬态字段，不入库：put 时告知调用方是否命中缓存


@dataclass
class Chunk:
    id: str
    l0_id: str
    workspace: str
    parent_id: str | None
    seq: int
    title: str | None
    summary: str
    content: str
    embedding: list[float]


def _now() -> str:
    return datetime.now(UTC).isoformat()


class Storage:
    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.data_dir = cfg.data_dir
        self.data_dir.mkdir(parents=True, exist_ok=True)
        # ponytail: check_same_thread=False，FastAPI 线程池并发下够用；真高并发再上连接池
        self.db = sqlite3.connect(self.data_dir / "memory.db", check_same_thread=False)
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.enable_load_extension(True)
        import sqlite_vec

        sqlite_vec.load(self.db)
        self._migrate()

    # ---------- 迁移：只执行比当前库版本新的编号文件 ----------

    def _migrate(self) -> None:
        version = self.db.execute("PRAGMA user_version").fetchone()[0]
        for path in sorted(MIGRATIONS_DIR.glob("*.sql")):
            n = int(path.name[:4])
            if n > version:
                self.db.executescript(path.read_text(encoding="utf-8"))
                self.db.execute(f"PRAGMA user_version = {n}")
        # vec 影子表维度依赖配置，不走 .sql（换 embedding 模型=改维度=重建它们）
        self.db.execute(
            f"CREATE VIRTUAL TABLE IF NOT EXISTS doc_chunks_vec USING vec0("
            f"chunk_id TEXT PRIMARY KEY, embedding float[{self.cfg.embedding_dim}])"
        )
        self.db.commit()

    def _row_to_record(self, row: sqlite3.Row, hash_hit: bool = False) -> L0Record:
        return L0Record(row[0], row[1], row[2], row[3], row[4], row[6], hash_hit)

    # ---------- L0: doc ----------

    def put_doc(self, content: bytes, meta: dict, workspace: str) -> L0Record:
        content_hash = hashlib.sha256(content).hexdigest()
        row = self.db.execute(
            "SELECT id,type,workspace,path,content_hash,meta,derived_state FROM l0_records "
            "WHERE content_hash=? AND type='doc'",
            (content_hash,),
        ).fetchone()
        if row:  # hash 命中：什么都不写，返回已有记录
            return self._row_to_record(row, hash_hit=True)

        doc_id = uuid.uuid4().hex[:12]
        name = str(meta.get("filename", "file"))
        dest = self.data_dir / workspace / "l0" / "doc" / doc_id / name
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(content)  # 落盘顺序：先文件后插行（崩溃安全）
        self._insert_record(doc_id, "doc", workspace, str(dest), content_hash, meta)
        return L0Record(doc_id, "doc", workspace, str(dest), content_hash, "pending")

    def put_repo(self, repo_path: str, workspace: str) -> L0Record:
        raise NotImplementedError("P3: codegen 场景再实现")

    # ---------- L0: session ----------

    def _session_path(self, workspace: str, session_id: str) -> Path:
        return self.data_dir / workspace / "l0" / "session" / f"{session_id}.jsonl"

    def put_session(self, workspace: str, session_id: str, events: list[Event]) -> None:
        f = self._session_path(workspace, session_id)
        f.parent.mkdir(parents=True, exist_ok=True)
        with f.open("a", encoding="utf-8") as fp:  # append-only，utf-8 显式
            for e in events:
                fp.write(json.dumps(e.model_dump(), ensure_ascii=False) + "\n")

    def ensure_session_record(self, workspace: str, session_id: str) -> None:
        """session_end 到达时插 l0_records；id=session_id 使重复调用天然幂等。"""
        self.db.execute(
            "INSERT OR IGNORE INTO l0_records "
            "(id,type,workspace,path,content_hash,meta,derived_state,created_at,updated_at) "
            "VALUES (?, 'session', ?, ?, NULL, ?, 'pending', ?, ?)",
            (
                session_id,
                workspace,
                str(self._session_path(workspace, session_id)),
                json.dumps({"session_id": session_id}),
                _now(),
                _now(),
            ),
        )
        self.db.commit()

    # ---------- 读口（P1 演化引擎用，P0 先备好） ----------

    def read_session(self, workspace: str, session_id: str) -> list[Event]:
        f = self._session_path(workspace, session_id)
        if not f.exists():
            return []
        events = [Event(**json.loads(line)) for line in f.read_text(encoding="utf-8").splitlines()]
        return sorted(events, key=lambda e: e.seq)  # 到达序≠逻辑序，消费端排序

    def pending(self, workspace: str | None = None) -> list[L0Record]:
        sql = (
            "SELECT id,type,workspace,path,content_hash,meta,derived_state "
            "FROM l0_records WHERE derived_state='pending'"
        )
        args: tuple = ()
        if workspace:
            sql += " AND workspace=?"
            args = (workspace,)
        return [self._row_to_record(r) for r in self.db.execute(sql + " ORDER BY created_at", args)]

    # ---------- L1: doc_chunks（整体替换语义：重跑派生不留残渣） ----------

    def put_chunks(self, chunks: list[Chunk]) -> None:
        l0_ids = {c.l0_id for c in chunks}
        for l0_id in l0_ids:
            olds = [r[0] for r in self.db.execute(
                "SELECT id FROM doc_chunks WHERE l0_id=?", (l0_id,))]
            if olds:
                marks = ",".join("?" * len(olds))
                self.db.execute(f"DELETE FROM doc_chunks WHERE id IN ({marks})", olds)
                self.db.execute(f"DELETE FROM doc_chunks_fts WHERE chunk_id IN ({marks})", olds)
                self.db.execute(f"DELETE FROM doc_chunks_vec WHERE chunk_id IN ({marks})", olds)
        for c in chunks:
            self.db.execute(
                "INSERT INTO doc_chunks (id,l0_id,workspace,parent_id,seq,title,summary,content,"
                "created_at) VALUES (?,?,?,?,?,?,?,?,?)",
                (c.id, c.l0_id, c.workspace, c.parent_id, c.seq, c.title, c.summary,
                 c.content, _now()),
            )
            self.db.execute(
                "INSERT INTO doc_chunks_fts (content,title,chunk_id,workspace) VALUES (?,?,?,?)",
                (c.content, c.title, c.id, c.workspace),
            )
            self.db.execute(
                "INSERT INTO doc_chunks_vec (chunk_id,embedding) VALUES (?,?)",
                (c.id, _vec_blob(c.embedding)),
            )
        self.db.commit()

    # ---------- 状态机 ----------

    def mark_derived(self, l0_id: str) -> None:
        self.db.execute(
            "UPDATE l0_records SET derived_state='derived', updated_at=? WHERE id=?",
            (_now(), l0_id),
        )
        self.db.commit()

    def mark_failed(self, l0_id: str, error: str) -> None:
        self.db.execute(
            "UPDATE l0_records SET derived_state='failed', error=?, updated_at=? WHERE id=?",
            (error[:2000], _now(), l0_id),
        )
        self.db.commit()

    # ---------- 内部 ----------

    def _insert_record(
        self,
        rid: str,
        rtype: str,
        workspace: str,
        path: str,
        content_hash: str,
        meta: dict,
    ) -> None:
        self.db.execute(
            "INSERT INTO l0_records "
            "(id,type,workspace,path,content_hash,meta,derived_state,created_at,updated_at) "
            "VALUES (?,?,?,?,?,?,'pending',?,?)",
            (
                rid,
                rtype,
                workspace,
                path,
                content_hash,
                json.dumps(meta, ensure_ascii=False),
                _now(),
                _now(),
            ),
        )
        self.db.commit()
