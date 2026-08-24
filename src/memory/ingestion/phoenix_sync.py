"""Phoenix 同步器（拉型采集，REST 版）：定时从 Phoenix /v1/spans 拉 span → 信封 → store_events。

docgen 侧零改动零账号：span 本来就落 Phoenix，我们只读它的 REST 接口
（POST /v1/spans，返回 Arrow IPC——Phoenix 官方格式，pyarrow 解码即 DataFrame）。

核心规则（沿用 psycopg 版）：**只导含 done span 的完整 trace，按 trace 一次成型**。
- seq 从 trace 全量派生，一次成型（分轮增量导入会 seq 撞车 → 幂等误杀 = 静默丢数据）
- 崩溃重跑同一 trace → 全量重导 → 相同 seq → store_events 幂等去重（水位丢了最多重导）
- 水位 = 已导 trace_id 名单（JSON 文件，人可改，删掉即重拉）

ponytail: REST 无增量游标，每轮全量拉 limit=1000 再按 imported 名单跳过——
tc03 量级（901 span）绰绰有余；量级上来后换服务端时间过滤（queries.filter 语法待验证）。
"""

import io
import json
import logging
import time

from memory.config import Config
from memory.ingestion.service import store_events
from memory.ingestion.span_map import map_spans
from memory.storage.engine import Storage

log = logging.getLogger("memory.phoenix")


def _plain(v):
    """numpy 标量/数组 → python 原生（arrow 读出的嵌套 dict 里也藏着 ndarray，须递归洗）。"""
    import numpy as np

    if isinstance(v, np.generic):
        return v.item()
    if isinstance(v, np.ndarray):
        return [_plain(x) for x in v.tolist()]
    if isinstance(v, dict):
        return {k: _plain(x) for k, x in v.items()}
    if isinstance(v, (list, tuple)):
        return [_plain(x) for x in v]
    return v


class PhoenixRestReader:
    """Phoenix REST /v1/spans 薄读层：POST → Arrow IPC → 归一化 span dict（span_map 输入契约）。"""

    def __init__(self, url: str, project: str):
        self.url = url.rstrip("/") + "/v1/spans"
        self.project = project

    def fetch_spans(self, limit: int = 1000) -> list[dict]:
        import httpx

        resp = httpx.post(
            self.url,
            params={"project_name": self.project},
            json={"queries": [{}], "limit": limit},
            timeout=60,
        )
        resp.raise_for_status()
        return self.parse_arrow(resp.content)

    @staticmethod
    def parse_arrow(content: bytes) -> list[dict]:
        """Arrow IPC 字节 → span dict 列表。行结构见 docs/example/ 真实样例。"""
        import pyarrow as pa

        df = pa.ipc.open_stream(io.BytesIO(content)).read_pandas()
        spans = []
        for i in range(len(df)):
            row = df.iloc[i]
            attrs = {}
            for col in df.columns:
                if col.startswith("attributes."):
                    v = row[col]
                    if v is None or v != v:  # noqa: PLR0124 NaN 自比较判 NaN
                        continue
                    attrs[col[len("attributes.") :]] = _plain(v)
            pid = row["parent_id"]
            spans.append(
                {
                    "trace_id": row["context.trace_id"],
                    "name": row["name"],
                    "span_id": row["context.span_id"],
                    "parent_id": pid if isinstance(pid, str) else None,
                    "start_ns": int(row["start_time"].value),  # Timestamp.value = 纳秒
                    "attrs": attrs,
                }
            )
        return spans

    @staticmethod
    def parse_export(content: bytes) -> list[dict]:
        """Phoenix GraphQL 导出 JSON（v2 埋点，含消息/thinking）→ span dict 列表。

        差异于 REST Arrow：attributes 是拍平的 dotted key（llm.input_messages.0...）、
        时间戳是 ISO 字符串、文件头带 session_id/span_count。
        样例：docs/example/export-otel-v2-thinking-think-rec-1.json
        """
        from datetime import datetime

        d = json.loads(content)
        spans = []
        for s in d["spans"]:
            ts = datetime.fromisoformat(s["start_time"])
            spans.append(
                {
                    "trace_id": s["trace_id"],
                    "name": s["name"],
                    "span_id": s["span_id"],
                    "parent_id": s.get("parent_id") or None,
                    "start_ns": int(ts.timestamp() * 1e9),
                    "attrs": {k: v for k, v in s.get("attributes", {}).items() if v is not None},
                }
            )
        return spans


class PhoenixSyncer:
    def __init__(self, storage: Storage, reader: PhoenixRestReader, cfg: Config):
        self.storage = storage
        self.reader = reader
        self.workspace = cfg.phoenix_workspace
        self.interval_s = cfg.phoenix_interval_s
        self.state_path = cfg.data_dir / self.workspace / "phoenix_sync.json"
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        if self.state_path.exists():
            state = json.loads(self.state_path.read_text(encoding="utf-8"))
        else:
            state = {"imported": []}
        self.imported: set[str] = set(state["imported"])
        # now=只收新的：仅对"从未跑过"的实例生效（水位非空说明历史已处理过）
        self._skip_history = cfg.phoenix_start_from == "now" and not self.imported

    # ---------- 一轮同步 ----------

    def poll_once(self) -> tuple[int, int]:
        """返回 (本轮导入的 trace 数, 见到但未完成的 trace 数)。"""
        spans = self.reader.fetch_spans()
        groups: dict[str, list[dict]] = {}
        for s in spans:
            groups.setdefault(s["trace_id"], []).append(s)

        if self._skip_history:
            self.imported |= set(groups)  # 只收新的：首见即跳过
            self._skip_history = False

        imported = 0
        held = 0
        for tid, group in groups.items():
            if tid in self.imported:
                continue
            if not any(s["name"] == "done" for s in group):
                held += 1  # 半截 trace：下轮再见（done 到时自然导入）
                continue
            events = map_spans(group)
            if events:
                store_events(self.storage, self.workspace, tid, events)
                log.info("imported trace=%s events=%d", tid, len(events))
                imported += 1
            self.imported.add(tid)

        self._save()
        return imported, held

    def _save(self) -> None:
        self.state_path.write_text(
            json.dumps({"imported": sorted(self.imported)}), encoding="utf-8"
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
