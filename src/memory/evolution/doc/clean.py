"""文本清洗器：原始转换文本 → 干净的可分块文本。

七步流水线（领域无关，结构性信号优先于关键词）：
1. XML/模板符号（LLM 特殊 token 泄漏降级）
2. 控制字符（含 U+FFFE 两种编码形态）
3. 多余空白
4. 图片语法 → [图片] 占位；图例保留（它是图片的语义描述）
5. 结构性空表格（HTML + markdown 双格式；空格率 ≥60% 判定）
6. 页眉页脚（纯页码行 + 跨页重复行）
7. 目录行（强特征单行删 + 弱特征成串删）
"""

import re

# ── 低质量内容判定（chunker 每片过安检用）──────────────────

_HTML_TAG_RE = re.compile(r"<[^>]+>")
_TABLE_FRAG_RE = re.compile(r"</td>\s*</tr>\s*<tr>\s*<td", re.IGNORECASE)
_TD_CLOSE_RE = re.compile(r"</td>", re.IGNORECASE)
_EFFECTIVE_WORD_RE = re.compile(r"[\u4e00-\u9fff]|[a-zA-Z]{2,}|\d+")


def is_low_quality_content(text: str) -> bool:
    """五条信号（任一命中即垃圾）：

    1. HTML 标签占比 >50% 且去标签纯文本 <20 字符
    2. 表格碎片签名（MinerU 切割残留：`</td></tr><tr><td`）
    3. ≥3 个 `</td>` 且有效词 <15
    4. HTML 占比 >15% 且有效词 <15
    5. 纯文本（无标签）且有效词 <3（如 "共3"、"64W" 碎片）
    """
    tags = _HTML_TAG_RE.findall(text)
    if not tags:
        return len(_EFFECTIVE_WORD_RE.findall(text.strip())) < 3

    tag_len = sum(len(t) for t in tags)
    html_ratio = tag_len / max(len(text), 1)
    plain = _HTML_TAG_RE.sub("", text).strip()

    if html_ratio > 0.5 and len(plain) < 20:
        return True
    word_count = len(_EFFECTIVE_WORD_RE.findall(plain))
    if _TABLE_FRAG_RE.search(text):
        return True
    if len(_TD_CLOSE_RE.findall(text)) >= 3 and word_count < 15:
        return True
    return html_ratio > 0.15 and word_count < 15


class TextCleaner:
    """文本清洗器"""

    _CONTROL_CHARS = re.compile(r"[\x00-\x08\x0B\x0C\x0E-\x1F\x7F\xEF\xBF\xBE]")
    _FFFE = re.compile("\ufffe")
    _MULTIPLE_NEWLINES = re.compile(r"\n{3,}")
    _MULTIPLE_SPACES = re.compile(
        r"[\t\f\r\x20\u00a0\u1680\u180e\u2000-\u200a\u202f\u205f\u3000]{2,}"
    )

    @classmethod
    def clean(cls, text: str) -> str:
        """清洗文本。步骤见模块 docstring。"""
        if not text:
            return text
        text = cls._clean_xml_symbols(text)
        text = cls._clean_control_chars(text)
        text = cls._clean_extra_spaces(text)
        text = cls._remove_images(text)
        text = cls._remove_meta_html_tables(text)
        text = cls._remove_empty_md_tables(text)
        text = cls._clean_headers_footers(text)
        text = cls._clean_toc_pages(text)
        return text.strip()

    @staticmethod
    def _clean_xml_symbols(text: str) -> str:
        """LLM 特殊 token 泄漏降级：`<|end|>` → `<end>`（无害化，后续正则可收割）"""
        text = re.sub(r"<\|", "<", text)
        text = re.sub(r"\|>", ">", text)
        return text

    @classmethod
    def _clean_control_chars(cls, text: str) -> str:
        """控制字符；CR(\x0d) 故意保留给空白步处理，\xef\xbf\xbe 是 U+FFFE 的乱码形态"""
        text = cls._CONTROL_CHARS.sub("", text)
        return cls._FFFE.sub("", text)

    @classmethod
    def _clean_extra_spaces(cls, text: str) -> str:
        """3+换行→2；连续空白→1（含全角/不间断等 Unicode 空白，单空白不碰）"""
        text = cls._MULTIPLE_NEWLINES.sub("\n\n", text)
        return cls._MULTIPLE_SPACES.sub(" ", text)

    # ── 4. 图片 ──

    _MD_IMG_LINE = re.compile(r"!\[[^\]]*\]\([^)]*\)(?:\s*\"[^\"]*\")?")
    _HTML_IMG_LINE = re.compile(r"<img\s[^>]*/?>", re.IGNORECASE)

    @classmethod
    def _remove_images(cls, text: str) -> str:
        """图片语法 → [图片] 占位；图例（"图 3 xxx"）保留"""
        text = cls._MD_IMG_LINE.sub("[图片]", text)
        return cls._HTML_IMG_LINE.sub("[图片]", text)

    # ── 5. 结构性空表格 ──

    @classmethod
    def _remove_meta_html_tables(cls, text: str) -> str:
        """HTML 表格（pdf/MinerU 路径）：单元格 ≥60% 空（[图片] 占位算空）→ 删。

        签字/审批/会签区的本质是"空格子等人填"，结构判定领域无关。
        闸门：行数 >20 不删（大表保护）、格子 <4 不判。
        MinerU 会把大表拆成碎片 <table>——间隔纯空白的连续表分组聚合判定。
        """
        html_table_pattern = re.compile(r"<table[^>]*>.*?</table>", re.DOTALL | re.IGNORECASE)
        tables_info = [
            {"start": m.start(), "end": m.end(), "content": m.group(0)}
            for m in html_table_pattern.finditer(text)
        ]
        if not tables_info:
            return text

        groups: list[list[dict]] = []
        current_group = [tables_info[0]]
        for i in range(1, len(tables_info)):
            gap = text[tables_info[i - 1]["end"] : tables_info[i]["start"]].strip()
            if not gap:
                current_group.append(tables_info[i])
            else:
                groups.append(current_group)
                current_group = [tables_info[i]]
        groups.append(current_group)

        to_delete: list[tuple[int, int]] = []
        for group in groups:
            combined = " ".join(t["content"] for t in group)
            if len(re.findall(r"<tr", combined, re.IGNORECASE)) > 20:
                continue
            cells = re.findall(r"<t[dh][^>]*>(.*?)</t[dh]>", combined, re.DOTALL | re.IGNORECASE)
            if len(cells) < 4:
                continue
            empty = sum(1 for c in cells if len(re.sub(r"<[^>]+>|\s|\[图片\]", "", c)) < 2)
            if empty / len(cells) >= 0.6:
                to_delete.append((group[0]["start"], group[-1]["end"]))

        for start, end in reversed(to_delete):  # 从后往前删避免偏移
            text = text[:start] + text[end:]
        return text

    @classmethod
    def _remove_empty_md_tables(cls, text: str) -> str:
        """markdown 表格（docx/xlsx 路径）：与 HTML 版同一判定。
        ponytail: markitdown 不拆碎表格，无需分组。"""
        lines = text.split("\n")
        result: list[str] = []
        i = 0
        while i < len(lines):
            if lines[i].strip().startswith("|"):
                j = i
                while j < len(lines) and lines[j].strip().startswith("|"):
                    j += 1
                block = lines[i:j]
                rows = [ln for ln in block if not re.match(r"^\s*\|[\s\-|:]+\|?\s*$", ln)]
                cells = [c for ln in rows for c in ln.strip().strip("|").split("|")]
                if len(rows) <= 20 and len(cells) >= 4:
                    empty = sum(1 for c in cells if len(re.sub(r"\s|\[图片\]", "", c)) < 2)
                    if empty / len(cells) >= 0.6:
                        i = j
                        continue
                result.extend(block)
                i = j
            else:
                result.append(lines[i])
                i += 1
        return "\n".join(result)

    # ── 7. 页眉页脚 ──

    # 纯页码行字符集：整行只由数字+页码装饰符构成（"3", "- 3 -", "1/3", "第 1 页", "Page 3"）
    _PAGE_NUM_CHARS = set("0123456789第页頁共ofOF.-–—/ \t·") | {"Page", "page"}
    _WORD_TOKEN = re.compile(r"[A-Za-z]+|\S|\s")

    @classmethod
    def _is_page_num_line(cls, line: str) -> bool:
        stripped = line.strip()
        if not stripped or not any(c.isdigit() for c in stripped):
            return False
        return all(
            t in cls._PAGE_NUM_CHARS or t in ("Page", "page")
            for t in cls._WORD_TOKEN.findall(stripped)
        )

    @classmethod
    def _clean_headers_footers(cls, text: str) -> str:
        """通用页眉页脚清洗：本质是跨页重复，不是关键词。

        1. 纯页码行删除（页码数字每页不同，靠字符集形态判定）
        2. 归一化后出现 ≥3 次的普通文本行删除
        保护带：标题(#)/表格(| <)/空行/短行(<6字符)不参与规则2——
        表头行跨 sheet 重复是合法结构，正文句子不会逐字重复 3 次。
        """
        lines = text.split("\n")
        counts: dict[str, int] = {}
        for line in lines:
            norm = " ".join(line.split())
            if norm and len(norm) >= 6 and not norm.startswith(("#", "|", "<")):
                counts[norm] = counts.get(norm, 0) + 1

        result = []
        for line in lines:
            norm = " ".join(line.split())
            if cls._is_page_num_line(line):
                continue
            if norm and len(norm) >= 6 and counts.get(norm, 0) >= 3:
                continue
            result.append(line)
        return "\n".join(result)

    # ── 7. 目录行 ──

    # 强特征（有引导点，单行无歧义）："4 力学环境试验条件.. 6" / "6.2.2 标准 …… 56"
    # 末尾页码是防误伤锚（'见表 3.5' 这类正文引用不整行匹配）
    # ponytail: 汉字章节号（"第四章"）不覆盖——OCR 输出数字形式，出现汉字号再扩
    _TOC_LINE_PATTERN = re.compile(
        r"^\s*\d+(?:\.\d+)*\s+\S[^\n]*?[\.\s…]{2,}\d{1,4}\s*$", re.MULTILINE
    )
    # 弱特征（无引导符，"标题 6"/"标题6"）：单行与正文歧义，成串 ≥3 才删
    _TOC_LOOSE_LINE = re.compile(r"^\s*(?:\d+(?:\.\d+)*[\.、\s]+)?\S[^\n]*?\d{1,4}\s*$")

    @classmethod
    def _clean_toc_pages(cls, text: str) -> str:
        text = cls._TOC_LINE_PATTERN.sub("", text)

        lines = text.split("\n")
        result: list[str] = []
        run: list[int] = []
        blanks_in_run = 0

        def flush() -> None:
            nonlocal run, blanks_in_run
            if sum(1 for i in run if lines[i].strip()) >= 3:
                pass  # 整串丢弃
            else:
                result.extend(lines[i] for i in run)
            run.clear()
            blanks_in_run = 0

        for i, line in enumerate(lines):
            if not line.strip():
                if run:
                    blanks_in_run += 1
                    run.append(i)  # 串内空行不断串
                    if blanks_in_run > 2:
                        flush()
                else:
                    result.append(line)
            elif cls._TOC_LOOSE_LINE.match(line):
                run.append(i)
            else:
                if run:
                    flush()
                result.append(line)
        if run:
            flush()

        return cls._clean_extra_spaces("\n".join(result))


def clean_text(text: str) -> str:
    """便捷入口：TextCleaner.clean"""
    return TextCleaner.clean(text)
