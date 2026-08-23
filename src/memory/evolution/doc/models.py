"""文档分块数据模型（chunker 产出，deriver 消费）。

- Document: parent chunk（content + children）
- ChildDocument: child chunk（检索主力，靠 parent_id 回溯完整上下文）

三层 ID：{l0_id}_p{i} = parent，{l0_id}_c{j} = child。
"""

from dataclasses import dataclass, field
from typing import Dict, List, Optional


@dataclass
class ChildDocument:
    """metadata 必含：chunk_id, parent_id, path, doc_title；
    透传：doc_id, H1/H2/H3（供将来检索过滤）。"""
    content: str
    metadata: Dict[str, str] = field(default_factory=dict)


@dataclass
class Document:
    """metadata 必含：chunk_id, path, doc_title；透传：doc_id, H1/H2/H3。"""
    content: str
    metadata: Dict[str, str] = field(default_factory=dict)
    children: Optional[List[ChildDocument]] = None
