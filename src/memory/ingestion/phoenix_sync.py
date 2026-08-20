"""Phoenix 同步器（拉型采集）：定时从 docgen 的 Phoenix 库拉 span → 信封 → store_events。

决策（decisions.md 拉型采集条）：管线天级异步，不需要实时；docgen 零改动（只读账号即可）。
receiver（push 型）保留待命——将来配 collector 即启用，两条路汇于同一 store_events。

水位设计（核心规则）：**只导含 done span 的完整 trace，且按 trace_id 查全量一次性导入**。
- seq 从全量派生，一次成型，天然稳定（分轮增量导入会导致 seq 撞车 → 幂等误杀 = 静默丢数据）
- 崩溃重跑同一 trace → 全量重导 → 派生相同 seq → store_events 幂等去重（水位丢了最多重导）
"""

import json
import logging
import time

from memory.config import Config
from memory.ingestion.service import store_events
from memory.ingestion.span_map import map_spans
from memory.storage.engine import Storage

log = logging.getLogger("memory.phoenix")

# ponytail: 表/列名按 Phoenix postgres 常规假设，拿到只读账号后对照真实 schema 校正。
# 校正点：表名 spans、id 是否自增、trace_id 是否 bytes(需 hex)、attributes 是否 JSONB dict。
_COLS = "id, trace_id, span_id, parent_span_id, name, start_time, attributes"
_SQL_NEW = f"SELECT {_COLS} FROM spans WHERE id > %s ORDER BY id"
_SQL_TRACE = f"SELECT {_COLS} FROM spans WHERE trace_id = %s ORDER BY id"
_SQL_MAX_ID = "SELECT max(id) FROM spans"


class PhoenixReader:
    """Phoenix postgres 的薄读层。行 → 归一化 span dict（span_map 的输入契约）。"""

    def __init__(self, dsn: str):
        import psycopg  # 延迟导入：测试用 FakeReader，无需装驱动

        self.conn = psycopg.connect(dsn, autocommit=True)

    def _row(self, r) -> dict:
        rid, trace_id, span_id, parent_id, name, start_time, attrs = r
        if isinstance(trace_id, (bytes, memoryview)):
            trace_id = bytes(trace_id).hex()
        return {
            "id": rid,
            "trace_id": trace_id,
            "name": name,
            "span_id": span_id if isinstance(span_id, str) else bytes(span_id).hex(),
            "parent_id": (parent_id if isinstance(parent_id, str) else bytes(parent_id).hex())
            if parent_id
            else None,
            "start_ns": int(start_time.timestamp() * 1e9),
            "attrs": attrs if isinstance(attrs, dict) else {},
        }

    def new_spans(self, last_id: int) -> list[dict]:
        return [self._row(r) for r in self.conn.execute(_SQL_NEW, (last_id,)).fetchall()]

    def trace_spans(self, trace_id: str) -> list[dict]:
        return [self._row(r) for r in self.conn.execute(_SQL_TRACE, (trace_id,)).fetchall()]

    def max_id(self) -> int:
        return self.conn.execute(_SQL_MAX_ID).fetchone()[0] or 0


class PhoenixSyncer:
    def __init__(self, storage: Storage, reader: PhoenixReader, cfg: Config):
        self.storage = storage
        self.reader = reader
        self.workspace = cfg.phoenix_workspace
        self.interval_s = cfg.phoenix_interval_s
        self.state_path = cfg.data_dir / self.workspace / "phoenix_sync.json"
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        if self.state_path.exists():
            state = json.loads(self.state_path.read_text(encoding="utf-8"))
        else:
            # 首次启动：all=吃历史(0) | now=只收新的(跳到当前最大 id)
            state = {
                "last_id": 0 if cfg.phoenix_start_from == "all" else reader.max_id(),
                "incomplete": [],
            }
        self.last_id: int = state["last_id"]
        self.incomplete: set[str] = set(state["incomplete"])

    # ---------- 一轮同步 ----------

    def poll_once(self) -> tuple[int, int]:
        """返回 (本轮导入的 trace 数, 挂起的未完成 trace 数)。"""
        rows = self.reader.new_spans(self.last_id)
        groups: dict[str, list[dict]] = {}
        for r in rows:
            groups.setdefault(r["trace_id"], []).append(r)

        imported = 0
        done_tids: set[str] = set()
        for tid, spans in groups.items():
            if any(s["name"] == "done" for s in spans):
                self._import_trace(tid)  # 全量查库一次导完，seq 一次成型
                done_tids.add(tid)
                imported += 1
            else:
                self.incomplete.add(tid)  # 半截会话：留库下轮再看（done 是新 span，到时自然出现）

        self.incomplete -= done_tids
        if rows:
            self.last_id = max(r["id"] for r in rows)
        self._save()
        return imported, len(self.incomplete)

    def _import_trace(self, trace_id: str) -> None:
        events = map_spans(self.reader.trace_spans(trace_id))
        if events:
            store_events(self.storage, self.workspace, trace_id, events)
            log.info("imported trace=%s events=%d", trace_id, len(events))

    def _save(self) -> None:
        self.state_path.write_text(
            json.dumps({"last_id": self.last_id, "incomplete": sorted(self.incomplete)}),
            encoding="utf-8",
        )

    # ---------- 常驻循环 ----------

    def run(self) -> None:
        log.info(
            "phoenix syncer started, interval=%ss workspace=%s", self.interval_s, self.workspace
        )
        while True:
            try:
                self.poll_once()
            except Exception:
                log.exception("phoenix sync failed, retry next round")  # 断点续拉：源头数据一直在
            time.sleep(self.interval_s)
