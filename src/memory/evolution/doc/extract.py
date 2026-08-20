"""格式提取：任意文档 → Markdown（从 rag-clean/core/ingestion/extractor.py 裁剪移植）。

裁剪：去掉 Document 构建与 MinerU 服务调用。
ponytail: PDF 暂不支持（MinerU 未迁移）——需要时从 rag-clean 搬 mineru_client +
其配置（mineru3_env/gpu/backend 等），挂进 _convert_pdf。
"""

import hashlib
import logging
import os
import re
import subprocess
import tempfile
from pathlib import Path
from typing import Optional

log = logging.getLogger(__name__)

SUPPORTED_FORMATS = {".pdf", ".doc", ".docx", ".pptx", ".ppt", ".xlsx", ".xls", ".csv", ".md"}


def detect_format(file_path: str) -> str:
    ext = Path(file_path).suffix.lower()
    return {
        ".pdf": "pdf", ".doc": "doc", ".docx": "docx", ".pptx": "pptx", ".ppt": "ppt",
        ".xlsx": "xlsx", ".xls": "xls", ".csv": "csv", ".md": "md",
    }.get(ext, "unknown")


# ── 缓存（内容 hash 为 key）──────────────────────────────


def _cache_dir() -> Path:
    return Path(os.environ.get("MEMORY_CACHE_DIR", "data/cache")) / "converters"


def _content_hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()[:16]


def _cache_path(path: Path) -> Path:
    return _cache_dir() / f"{path.stem}_{_content_hash(path)}.md"


def _load_cache(path: Path) -> Optional[str]:
    cache = _cache_path(path)
    if cache.exists():
        return cache.read_text(encoding="utf-8")
    return None


def _save_cache(path: Path, content: str) -> None:
    cache = _cache_path(path)
    cache.parent.mkdir(parents=True, exist_ok=True)
    cache.write_text(content, encoding="utf-8")


# ── 转换器 ────────────────────────────────────────────────


def _convert_with_mineru3(path: Path) -> str:
    raise NotImplementedError(
        "MinerU 未迁移到本项目，PDF 暂不支持。需要时从 rag-clean 搬 mineru_client。"
    )


def _convert_with_markitdown(path: Path) -> Optional[str]:
    """MarkItDown：DOCX(mammoth)/PPTX/XLSX/CSV → Markdown，失败返回 None 走兜底。"""
    try:
        from markitdown import MarkItDown

        result = MarkItDown(enable_plugins=False).convert(str(path))
        text = (result.text_content or "").strip()
        if not text:
            return None
        text = re.sub(r"!\[[^\]]*\]\(data:[^)]*\)", "", text)  # 去嵌入的 base64 图片
        return f"# {path.stem}\n\n{text}"
    except ImportError:
        log.warning("markitdown 未安装，跳过（pip install 'markitdown[all]'）")
        return None
    except Exception as e:
        log.warning("MarkItDown 转换失败: %s", e)
        return None


def _convert_with_libreoffice(path: Path) -> Optional[str]:
    try:
        with tempfile.TemporaryDirectory() as td:
            result = subprocess.run(
                ["libreoffice", "--headless", "--convert-to", "txt:Text (encoded)",
                 "--outdir", td, str(path)],
                capture_output=True, text=True, timeout=60,
            )
            if result.returncode != 0:
                return None
            txt = list(Path(td).glob("*.txt"))
            if not txt:
                return None
            return f"# {path.stem}\n\n" + txt[0].read_text(encoding="utf-8", errors="ignore")
    except Exception as e:
        log.warning("libreoffice 转换失败: %s", e)
        return None


def _try_antiword(path: Path) -> Optional[str]:
    try:
        result = subprocess.run(
            ["antiword", "-m", "UTF-8", str(path)],
            capture_output=True, text=True, timeout=30,
        )
        if result.returncode == 0 and result.stdout.strip():
            return f"# {path.stem}\n\n{result.stdout}"
    except Exception as e:
        log.warning("antiword 失败: %s", e)
    return None


def _normalize_docx_paths(path: Path) -> Path:
    """修复 Windows 工具（WPS）创建的 docx 内部反斜杠路径，就地原子替换。"""
    import shutil
    import zipfile

    try:
        with zipfile.ZipFile(path) as z:
            names = z.namelist()
    except zipfile.BadZipFile:
        return path
    if not any("\\" in n for n in names):
        return path

    fd, tmp = tempfile.mkstemp(suffix=".docx")
    os.close(fd)
    with zipfile.ZipFile(path) as zin, zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as zout:
        for item in zin.infolist():
            info = zipfile.ZipInfo(filename=item.filename.replace("\\", "/"),
                                   date_time=item.date_time)
            info.compress_type = item.compress_type
            zout.writestr(info, zin.read(item.filename))
    shutil.move(tmp, path)
    return path


def _convert_docx(path: Path) -> str:
    cached = _load_cache(path)
    if cached:
        return cached
    path = _normalize_docx_paths(path)
    for conv in (_convert_with_markitdown, _convert_with_libreoffice):
        if result := conv(path):
            _save_cache(path, result)
            return result
    raise RuntimeError(f"DOCX 转换失败: {path.name}")


def _convert_doc(path: Path) -> str:
    cached = _load_cache(path)
    if cached:
        return cached
    for conv in (_try_antiword, _convert_with_libreoffice):
        if result := conv(path):
            _save_cache(path, result)
            return result
    raise RuntimeError(f"DOC 转换失败: {path.name}")


def _convert_xlsx(path: Path) -> str:
    cached = _load_cache(path)
    if cached:
        return cached
    if result := _convert_with_markitdown(path):
        _save_cache(path, result)
        return result
    raise RuntimeError(f"XLSX 转换失败: {path.name}")


def _convert_csv(path: Path) -> str:
    return _convert_xlsx(path)  # 同走 MarkItDown


def _convert_pptx(path: Path) -> str:
    return _convert_docx(path)  # 同级降级链


def _convert_pdf(path: Path) -> str:
    return _convert_with_mineru3(path)


def convert_to_markdown(file_path: str) -> str:
    """统一入口：任意支持格式 → Markdown 文本。"""
    path = Path(file_path)
    fmt = detect_format(path)
    if fmt == "unknown":
        raise ValueError(f"不支持的文件格式: {path.suffix}")
    if fmt == "md":
        return path.read_text(encoding="utf-8")
    if fmt == "ppt":
        raise RuntimeError("老 PPT 格式暂不支持，请转换为 PPTX")
    return {
        "pdf": _convert_pdf, "doc": _convert_doc, "docx": _convert_docx,
        "pptx": _convert_pptx, "xlsx": _convert_xlsx, "xls": _convert_xlsx,
        "csv": _convert_csv,
    }[fmt](path)
