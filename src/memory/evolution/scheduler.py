"""演化调度器：L0 状态机驱动（pending → derived/failed）。

逐条派生，单条失败只 mark_failed 留痕，不影响其他记录。
ponytail: 单线程轮询；真有积压（MinerU 慢文档排队）再上并发。
session 等 P2 类型暂不处理，留在 pending 池等 TraceDeriver。
"""

import logging
import time

from memory.evolution.doc.deriver import derive_doc
from memory.storage.engine import Storage

log = logging.getLogger(__name__)


def run_once(storage: Storage, embedder) -> int:
    """捞一轮 pending 文档派生，返回成功条数。"""
    n = 0
    for l0 in storage.pending():
        if l0.type != "doc":
            continue
        try:
            chunks = derive_doc(l0, storage, embedder)
            storage.mark_derived(l0.id)
            n += 1
            log.info(f"[scheduler] {l0.id} derived: {chunks} chunks")
        except Exception as e:  # noqa: BLE001 故意兜一切：单条失败必须隔离，不能杀掉整轮
            storage.mark_failed(l0.id, repr(e))
            log.warning(f"[scheduler] {l0.id} failed: {e}")
    return n


def run(storage: Storage, embedder, interval_s: int = 10) -> None:
    """daemon 线程体：轮询 + 睡眠。"""
    while True:
        run_once(storage, embedder)
        time.sleep(interval_s)
