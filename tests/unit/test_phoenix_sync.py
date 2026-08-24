"""Phoenix 同步器 REST 版：完整/半截 trace、水位跳过、崩溃重导幂等 + 真实样例回归。

FakeReader 喂合成 span 测调度逻辑；真实 tc03 arrow 样例（901 span / 16 trace）
走 parse_arrow 全链路——15 条含 done 导入、1 条单 span trace 挂起。
"""

import json
from pathlib import Path

import pytest

from memory.config import Config
from memory.ingestion.phoenix_sync import PhoenixRestReader, PhoenixSyncer
from memory.ingestion.service import _seq_cache
from memory.storage.engine import Storage

SAMPLE = Path(__file__).parents[2] / "docs/example/docgen-real-tc03-spans.arrow"
SAMPLE_V2 = Path(__file__).parents[2] / "docs/example/export-otel-v2-thinking-think-rec-1.json"


class FakeReader:
    """fetch_spans 契约的最小替身：内部一个可追加的 span 表。"""

    def __init__(self):
        self.rows: list[dict] = []

    def add(self, trace_id, name, ns):
        self.rows.append(
            {
                "trace_id": trace_id,
                "name": name,
                "span_id": f"s{len(self.rows) + 1:04x}",
                "parent_id": None,
                "start_ns": ns,
                "attrs": {},
            }
        )

    def fetch_spans(self, limit=1000):
        return list(self.rows)


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
    assert store.pending() == []

    reader.add("T1", "done", ns=2)  # 第二轮：done 到了
    imported, held = syncer.poll_once()
    assert (imported, held) == (1, 0)
    events = store.read_session("docgen", "T1")
    assert [e.kind for e in events] == ["llm_call", "session_end"]  # 全量一次导，seq 一次成型
    assert [r.id for r in store.pending()] == ["T1"]


def test_watermark_skip_and_idempotent(env):
    """已导 trace 跳过；水位丢了重导 → seq 相同 → 幂等不翻倍。"""
    store, reader, syncer, tmp = env
    reader.add("T1", "llm_call", ns=1)
    reader.add("T1", "tool:tool_x", ns=2)
    reader.add("T1", "done", ns=3)
    assert syncer.poll_once() == (1, 0)

    # 已导：每轮全量拉回但按 imported 名单跳过
    assert syncer.poll_once() == (0, 0)
    assert len(store.read_session("docgen", "T1")) == 3

    # 模拟崩溃：水位回退 → 重导 → 幂等去重
    state_path = tmp / "docgen/phoenix_sync.json"
    (tmp / "docgen").mkdir(exist_ok=True)
    state_path.write_text(json.dumps({"imported": []}), encoding="utf-8")
    PhoenixSyncer(store, reader, Config(data_dir=tmp)).poll_once()
    assert len(store.read_session("docgen", "T1")) == 3  # 没有翻倍
    assert len(store.pending()) == 1


def test_start_from_now_skips_history(tmp_path):
    _seq_cache.clear()
    store = Storage(Config(data_dir=tmp_path))
    reader = FakeReader()
    reader.add("T1", "llm_call", ns=1)
    reader.add("T1", "done", ns=2)
    cfg = Config(data_dir=tmp_path)
    cfg.phoenix_start_from = "now"
    assert PhoenixSyncer(store, reader, cfg).poll_once() == (0, 0)  # 首见即跳过
    reader.add("T2", "done", ns=3)
    assert PhoenixSyncer(store, reader, cfg).poll_once() == (1, 0)  # 新的照收


def test_real_tc03_sample(tmp_path):
    """真实样例全链路：Arrow 解码 → 分组 → 14 导入 / 2 挂起。

    挂起的 2 条：1 个单 span "run docgen" + 1 条 46 span 中途失败的运行（无 done）——
    "只导完整 trace"规则在真实数据上正确拒绝半截会话。
    """
    _seq_cache.clear()
    store = Storage(Config(data_dir=tmp_path))
    spans = PhoenixRestReader.parse_arrow(SAMPLE.read_bytes())
    assert len(spans) == 901

    reader = FakeReader()
    reader.rows = spans
    syncer = PhoenixSyncer(store, reader, Config(data_dir=tmp_path))
    imported, held = syncer.poll_once()
    assert (imported, held) == (14, 2)

    sessions = store.pending()
    assert len(sessions) == 14


def test_real_v2_export(tmp_path):
    """v2 埋点导出（含消息/thinking）全链路：解析 → 映射 → 落库。"""
    _seq_cache.clear()
    store = Storage(Config(data_dir=tmp_path))
    spans = PhoenixRestReader.parse_export(SAMPLE_V2.read_bytes())
    assert len(spans) == 94

    reader = FakeReader()
    reader.rows = spans
    syncer = PhoenixSyncer(store, reader, Config(data_dir=tmp_path))
    imported, _held = syncer.poll_once()
    assert imported == 1  # 单 trace，含 done

    events = store.read_session("docgen", store.pending()[0].id)
    llm = [e for e in events if e.kind == "llm_call"]
    assert len(llm) == 15
    # 完整对话都在：输入含 system prompt、输出、thinking
    first = llm[0].data
    assert first["messages_in"][0]["role"] == "system"
    assert len(first["messages_in"][0]["content"]) > 1000
    assert first["thinking"]
    assert any(e.kind == "tool_call" for e in events)
    kinds = {e.kind for e in events}
    assert "session_end" in kinds
    assert [e.seq for e in events] == sorted(e.seq for e in events)
