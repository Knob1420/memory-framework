"""DocChunkDeriver：L0 doc → 提取/清洗/分块（移植自 rag-clean）→ 向量化 → doc_chunks。

LLM 边界：embedding 经注入的 llm 客户端（测试用 FakeLLM）。
ponytail: summary 用内容截断而非 LLM 摘要——search 只需索引卡判断要不要 get，
LLM 摘要等链 B 的 llm/schema 补全后再挂。
"""

import logging
from pathlib import Path

from memory.evolution.doc.chunker import SmartChunker
from memory.evolution.doc.extract import convert_to_markdown
from memory.llm.client import LLMClient
from memory.storage.engine import Chunk, L0Record, Storage

log = logging.getLogger(__name__)

SUMMARY_CHARS = 100


def derive_doc(l0: L0Record, storage: Storage, llm: LLMClient) -> int:
    """返回写入的 chunk 数。异常由调度器捕获置 failed。"""
    md = convert_to_markdown(l0.path)  # md 直读；docx/xlsx 走转换链
    parents = SmartChunker().chunk(md, Path(l0.path).stem, l0.id)

    rows: list[Chunk] = []
    for p in parents:
        pid = p.metadata.get("chunk_id", f"{l0.id}_p?")
        title = p.metadata.get("path", "") or p.metadata.get("doc_title")
        rows.append(Chunk(pid, l0.id, l0.workspace, None, 0, title,
                          p.content[:SUMMARY_CHARS], p.content, []))
        for j, child in enumerate(p.children, start=1):
            rows.append(Chunk(child.metadata.get("chunk_id", f"{pid}_c{j}"), l0.id,
                              l0.workspace, pid, j, title,
                              child.content[:SUMMARY_CHARS], child.content, []))

    vecs = llm.embed([r.content for r in rows])  # 铁律：storage 不调 LLM，调用方算好传入
    for r, v in zip(rows, vecs):
        r.embedding = v
    storage.put_chunks(rows)
    return len(rows)
