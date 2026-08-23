"""SmartChunker：markdown → parent/child 双层 chunk。

流水线（从头设计，单元序列只解析一次）：
1. clean_text          清洗（clean.py 七步，含空表格/页眉页脚/目录行）
2. _normalize_tables   HTML 表格 → md 管道表（最终形态）；此后全链路只有一种表格语法
3. 标题骨架            H1/H2/H3 切 section；无标题文档单 section 兜底
4. _is_meta_section    目录/修订记录/免责声明整节丢弃
5. parent 尺寸收敛     <MIN 合并、>MAX 按单元序列拆（表格原子不可分）
6. _split_children     文本 ~CHILD_CHUNK_SIZE；表格级联（整表原子/行窗口+表头复读）
7. 上下文注入          [路径: H1/H2/H3] 前缀进每个 child；表格再拼前一句作语义锚
8. 低质过滤 + 短块合并 + 元数据（parent_id/doc_id/序号/path）

ID 格式：{doc_id}_p{parent_idx} / {doc_id}_c{child_idx}
"""

import logging
import re
from io import StringIO
from typing import List, Tuple

from langchain_core.documents import Document as LCDocument
from langchain_text_splitters import (
    MarkdownHeaderTextSplitter,
    RecursiveCharacterTextSplitter,
)

from memory.evolution.doc.clean import clean_text, is_low_quality_content
from memory.evolution.doc.models import ChildDocument, Document

logger = logging.getLogger(__name__)

# ── 尺寸常量（字符数）────────────────────────────────────────────────────────

MIN_PARENT_SIZE = 1000
MAX_PARENT_SIZE = 2000
CHILD_CHUNK_SIZE = 500
CHILD_CHUNK_OVERLAP = 100
MIN_CHILD_SIZE = 50  # 短块合并阈值
TABLE_CHILD_MAX = CHILD_CHUNK_SIZE * 2  # 表格切片上限（表头复读有开销，放宽一倍）

HEADERS_TO_SPLIT_ON = [("#", "H1"), ("##", "H2"), ("###", "H3")]

_TABLE_PATTERN = re.compile(r"<table[^>]*>.*?</table>", re.DOTALL | re.IGNORECASE)

Unit = Tuple[str, str]  # ("text", 段落) | ("table", md表格块)


# ═══ 1. 表格归一化：HTML 表格 → markdown（最终形态）═════════════════════════

_MD_ROW = re.compile(r"^\s*\|.+\|\s*$")
_MD_SEP = re.compile(r"^\s*\|[\s\-:|]+\|\s*$")


def _normalize_tables(text: str) -> str:
    """HTML 表格 → markdown 管道表。

    归一后全链路（标题切分/尺寸收敛/child 切分）只有 md 管道表一种语法，
    尺寸度量也在真实内容上（HTML 是渲染前的膨胀形态，度量会失真）。
    docx/xlsx（markitdown）产物本来就是管道表，无需转换。
    """
    return _TABLE_PATTERN.sub(lambda m: _html_table_to_md(m.group(0)), text)


# ═══ 2. 表格渲染：<table> → markdown ════════════════════════════════════════


def _html_table_to_md(html: str) -> str:
    """<table> → markdown 表格。

    pandas.read_html 自动展开合并单元格（rowspan/colspan 内容回填）。
    解析失败回退到正则提取（不展开但保留文本），最终失败返回 ""（调用方过滤）。
    """
    try:
        import warnings

        import pandas as pd

        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            has_th = bool(re.search(r"<th[^>]*>", html, re.IGNORECASE))
            dfs = pd.read_html(StringIO(html), header=0 if has_th else None)
        if dfs:
            df = dfs[0].fillna("")
            # 删全空列（colspan 展开后多出的列、纯空数据列）
            df = df.loc[:, (df.astype(str) != "").any()]
            if not df.empty:
                if not has_th:  # pandas 数字列名 → col1/col2/...
                    df.columns = [f"col{i+1}" for i in range(len(df.columns))]
                md = df.to_markdown(index=False)
                if len(re.sub(r"[|\- \n]", "", md)) >= 3:  # 有实质内容
                    return md
    except Exception as e:
        logger.debug(f"[chunker] pandas 表格解析失败，回退正则: {e}")
    return _html_table_to_md_simple(html)


def _html_table_to_md_simple(html: str) -> str:
    """兜底：正则提取 <th>/<td>，不展开合并单元格。"""
    strip = lambda s: re.sub(r"<[^>]+>", "", s).strip()  # noqa: E731

    rows = re.findall(r"<tr[^>]*>(.*?)</tr>", html, re.DOTALL | re.IGNORECASE)
    if not rows:
        return ""
    headers = [
        strip(h)
        for h in re.findall(r"<th[^>]*>(.*?)</th>", html, re.DOTALL | re.IGNORECASE)
    ]
    parsed = [
        [
            strip(c)
            for c in re.findall(r"<td[^>]*>(.*?)</td>", r, re.DOTALL | re.IGNORECASE)
        ]
        for r in rows
    ]
    parsed = [r for r in parsed if any(r)]
    if not parsed:
        return ""

    if headers:
        head, data = headers, parsed
    else:
        head, data = parsed[0], parsed[1:]
    md_lines = [
        "| " + " | ".join(head) + " |",
        "| " + " | ".join(["---"] * len(head)) + " |",
    ] + ["| " + " | ".join(r) + " |" for r in data]

    if len(re.sub(r"[|\- \n]", "", "\n".join(md_lines))) < 3:
        return ""
    return "\n".join(md_lines)


def _split_large_md_table(md_table: str, max_size: int) -> List[str]:
    """大 markdown 表格按行切分，每个切片自带表头（表头复读）。

    单行超长时单行成片；<4 行或不超 max_size 原样返回。
    """
    lines = md_table.split("\n")
    while lines and not lines[-1].strip():
        lines.pop()

    if len(lines) < 4 or len(md_table) <= max_size:
        return [md_table]

    header, separator, data_rows = lines[0], lines[1], lines[2:]
    if not re.match(r"^\s*\|[\s\-:|]+\|\s*$", separator):  # 防误切非表格
        return [md_table]

    header_len = len(header) + len(separator) + 2
    pieces: List[str] = []
    current_rows: List[str] = []
    current_len = header_len

    for row in data_rows:
        if current_rows and current_len + len(row) + 1 > max_size:
            pieces.append("\n".join([header, separator] + current_rows))
            current_rows, current_len = [row], header_len + len(row) + 1
        else:
            current_rows.append(row)
            current_len += len(row) + 1

    if current_rows:
        pieces.append("\n".join([header, separator] + current_rows))
    return pieces or [md_table]


# ═══ 3. 单元序列（全链路唯一解析点）═════════════════════════════════════════


def _table_starts(lines: List[str], i: int) -> bool:
    """行 i 是否是 md 表格块的开头：管道行 + 紧跟分隔行（防 "|a|b|" 形正文误伤）。"""
    return _MD_ROW.match(lines[i]) and i + 1 < len(lines) and _MD_SEP.match(lines[i + 1])


def _parse_units(content: str) -> List[Unit]:
    """→ [("text", 段) | ("table", md表格块)]。表格永远独立成单元，不与正文混切。"""
    lines = content.split("\n")
    units: List[Unit] = []
    i = 0
    while i < len(lines):
        if _table_starts(lines, i):
            j = i
            while j < len(lines) and _MD_ROW.match(lines[j]):
                j += 1
            units.append(("table", "\n".join(lines[i:j]).strip()))
            i = j
        else:
            start = i
            i += 1
            while i < len(lines) and not _table_starts(lines, i):
                i += 1
            units.append(("text", "\n".join(lines[start:i])))
    return units


# ═══ 4. 元信息 section 判定 ═════════════════════════════════════════════════

_META_HEADER_KEYWORDS = [
    "目录",
    "table of contents",
    "index",
    "版本修订记录",
    "修订记录",
    "变更记录",
    "changelog",
    "版本历史",
    "免责声明",
    "版权声明",
    "声明",
    "注意事项",
    "重要声明",
    "preface",
    "foreword",
    "acknowledgement",
    "acknowledgments",
    "致谢",
    "abbreviation",
    "缩写",
    "glossary",
    "词汇表",
    "参考文献",
    "reference",
    "references",
]
# MinerU 把目录条目识别成标题的情况："5 单机试验要求.. 25" / "目 录"
_META_HEADER_PATTERNS = [
    re.compile(r"^\s*\d+(?:\.\d+)*\s+\S.+[\.\s]{2,}\d{1,4}\s*$"),
    re.compile(r"^目\s*录\s*$"),
]


def _is_meta_section(sec) -> bool:
    """目录/修订记录/免责声明等整节无检索价值，按标题判定丢弃。"""
    for key in ("H1", "H2", "H3"):
        header = sec.metadata.get(key, "")
        if not header:
            continue
        hl = header.lower()
        if any(kw.lower() in hl for kw in _META_HEADER_KEYWORDS):
            return True
        if any(pat.search(header) for pat in _META_HEADER_PATTERNS):
            return True
    return False


# ═══ 分块器 ═════════════════════════════════════════════════════════════════


class SmartChunker:
    """父子双层分块器。parent 给 LLM（完整上下文），child 给检索（嵌入粒度）。"""

    def __init__(self):
        self._parent_splitter = MarkdownHeaderTextSplitter(
            headers_to_split_on=HEADERS_TO_SPLIT_ON,
            strip_headers=False,
        )
        self._child_splitter = RecursiveCharacterTextSplitter(
            chunk_size=CHILD_CHUNK_SIZE,
            chunk_overlap=CHILD_CHUNK_OVERLAP,
        )

    def chunk(self, markdown: str, title: str, doc_id: str) -> List[Document]:
        """markdown → List[Document]（每 parent 一个，含 children）。"""
        md = _normalize_tables(clean_text(markdown))

        # 标题骨架 + 无标题兜底 + 元信息节剔除
        sections = self._parent_splitter.split_text(md)
        if not sections:
            sections = [LCDocument(page_content=md, metadata={})]
        sections = [s for s in sections if not _is_meta_section(s)]

        # parent 尺寸收敛：先拆大的（会余下碎片），再合并小的（碎片顺势收编）
        sections = self._split_large_parents(sections)
        sections = self._merge_small_parents(sections)

        documents: List[Document] = []
        child_idx = 0
        for p_idx, sec in enumerate(sections):
            parent_id = f"{doc_id}_p{p_idx}"
            path = " / ".join(
                sec.metadata.get(k, "")
                for k in ("H1", "H2", "H3")
                if sec.metadata.get(k)
            )
            common_meta = {
                "doc_id": doc_id,
                "doc_title": title,
                "path": path,
                "H1": sec.metadata.get("H1", ""),
                "H2": sec.metadata.get("H2", ""),
                "H3": sec.metadata.get("H3", ""),
            }

            children, child_idx = self._split_children(
                sec.page_content, doc_id, parent_id, child_idx, path, common_meta
            )

            # parent 内容已是全 md（归一化时表格已渲染），只加路径前缀
            prefix = f"[路径: {path}]\n\n" if path else ""
            parent_content = prefix + sec.page_content
            documents.append(
                Document(
                    content=parent_content,
                    metadata={**common_meta, "chunk_id": parent_id},
                    children=children or None,
                )
            )

        logger.info(
            f"[Chunker] 分块完成: {len(documents)} parents (children={child_idx})"
        )
        return documents

    # ── child 切分 ─────────────────────────────────────────────────────

    def _split_children(
        self,
        content: str,
        doc_id: str,
        parent_id: str,
        start_idx: int,
        path: str,
        common_meta: dict,
    ) -> Tuple[List[ChildDocument], int]:
        """parent 内容 → children。

        文本：buffer 累加，超长 RecursiveCharacterTextSplitter；
        表格：整表原子（≤TABLE_CHILD_MAX）/ 行窗口+表头复读；
        上下文注入：[路径] 前缀进每个 child，表格再拼前一句正文作锚（"表 3.5"的语义来源）。
        """
        prefix = f"[路径: {path}]\n" if path else ""
        pieces: List[Tuple[str, bool]] = []  # (content, is_table)
        buf = ""
        anchor = ""  # 表格前的最后一句正文

        def flush_text(b: str) -> List[Tuple[str, bool]]:
            b = b.strip()
            if not b or is_low_quality_content(b):
                return []
            if len(b) <= CHILD_CHUNK_SIZE:
                return [(prefix + b, False)]
            return [
                (prefix + s.strip(), False)
                for s in self._child_splitter.split_text(b)
                if s.strip() and not is_low_quality_content(s)
            ]

        for typ, unit in _parse_units(content):
            if typ == "table":
                if buf:
                    pieces.extend(flush_text(buf))
                    buf = ""
                ctx = prefix + (f"表格（前文: {anchor[-80:]}）\n" if anchor else "")
                if len(unit) > TABLE_CHILD_MAX:
                    pieces.extend(
                        (ctx + p, True)
                        for p in _split_large_md_table(unit, TABLE_CHILD_MAX)
                    )
                else:
                    pieces.append((ctx + unit, True))
                anchor = ""
            else:
                for ln in unit.splitlines():  # 记录紧邻表格的最后一句
                    if ln.strip():
                        anchor = ln.strip()
                buf += unit
        if buf:
            pieces.extend(flush_text(buf))

        # 短块合并：文本碎块并入前一个（不超限）。表格自身不拆不并，
        # 但表格后的短注（"以上为标称值"）跟随表格——它就是表格的语义尾巴
        merged: List[Tuple[str, bool]] = []
        for text, is_table in pieces:
            if (
                not is_table
                and len(text) < MIN_CHILD_SIZE + len(prefix)
                and merged
                and len(merged[-1][0]) + len(text) < CHILD_CHUNK_SIZE * 1.5
            ):
                merged[-1] = (merged[-1][0] + "\n\n" + text, merged[-1][1])
            else:
                merged.append((text, is_table))

        children = [
            ChildDocument(
                content=text,
                metadata={
                    **common_meta,
                    "chunk_id": f"{doc_id}_c{start_idx + i}",
                    "parent_id": parent_id,
                },
            )
            for i, (text, _is_table) in enumerate(merged)
        ]
        return children, start_idx + len(children)

    # ── parent 尺寸收敛 ────────────────────────────────────────────────

    def _merge_small_parents(self, sections: list) -> list:
        """连续小 section（< MIN_PARENT_SIZE）合并，MAX_PARENT_SIZE 上限保护。"""
        if not sections:
            return []
        merged, current = [], None
        for sec in sections:
            if current is None:
                current = sec
            else:
                if (
                    len(current.page_content) + len(sec.page_content) + 2
                    > MAX_PARENT_SIZE
                ):
                    merged.append(current)
                    current = sec
                    continue
                current.page_content += "\n\n" + sec.page_content
                self._merge_metadata(current, sec)
            if len(current.page_content) >= MIN_PARENT_SIZE:
                merged.append(current)
                current = None
        if current:
            if (
                merged
                and len(merged[-1].page_content) + len(current.page_content) + 2
                <= MAX_PARENT_SIZE
            ):
                merged[-1].page_content += "\n\n" + current.page_content
                self._merge_metadata(merged[-1], current)
            else:
                merged.append(current)
        return merged

    def _split_large_parents(self, sections: list) -> list:
        """超 MAX_PARENT_SIZE 的 section 按单元序列拆，表格原子（单独超限也保完整）。"""
        result = []
        for sec in sections:
            if len(sec.page_content) <= MAX_PARENT_SIZE:
                result.append(sec)
                continue
            chunks, current = [], ""
            for _typ, unit in _parse_units(sec.page_content):
                if current and len(current) + len(unit) > MAX_PARENT_SIZE:
                    chunks.append(current)
                    current = unit
                else:
                    # 单元间必须补 \n：MinerU 的说明行常紧贴表格，
                    # 无换行会把表格首行的 | 粘进句子，表格从此认不出来
                    current = current + ("\n" if current else "") + unit
            if current:
                chunks.append(current)
            result.extend(
                LCDocument(page_content=ch, metadata=dict(sec.metadata))
                for ch in chunks
            )
        return result

    @staticmethod
    def _merge_metadata(target, source):
        """合并 section metadata；值不同时拼 "原值 -> 新值"（记录合并路径）。"""
        for k, v in source.metadata.items():
            if k in target.metadata:
                if target.metadata[k] != v:
                    target.metadata[k] = f"{target.metadata[k]} -> {v}"
            else:
                target.metadata[k] = v
