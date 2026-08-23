"""格式提取：任意文档 → Markdown（从 rag-clean 裁剪移植 + MinerU 接入）。

PDF/图片 → MinerU（独立 conda env，子进程调用，config.yaml mineru 节控制）；
其余 → markitdown / libreoffice。统一布局落盘见 convert_to_markdown_file。
"""

import logging
import os
import re
import subprocess
import tempfile
from pathlib import Path
from typing import Optional

log = logging.getLogger(__name__)

SUPPORTED_FORMATS = {".pdf", ".doc", ".docx", ".pptx", ".ppt", ".xlsx", ".xls", ".csv", ".md"}
IMAGE_FORMATS = {".jpg", ".jpeg", ".png", ".gif", ".bmp", ".webp", ".tiff"}


def detect_format(file_path: str) -> str:
    ext = Path(file_path).suffix.lower()
    return {
        ".pdf": "pdf",
        ".doc": "doc",
        ".docx": "docx",
        ".pptx": "pptx",
        ".ppt": "ppt",
        ".xlsx": "xlsx",
        ".xls": "xls",
        ".csv": "csv",
        ".md": "md",
        **{e: "image" for e in IMAGE_FORMATS},
    }.get(ext, "unknown")


# ── MinerU（PDF/图片 OCR，子进程调独立 conda env）─────────


def _run_mineru(path: Path) -> str:
    """MinerU CLI：hybrid-engine（文字直提+OCR+跨页表格合并），产出 md+images/。

    输出落统一布局：md 内容由调用方写目标文件，images/ 移到原始文件旁。
    """
    import shutil

    from memory.config import load_config

    cfg = load_config()
    if not cfg.mineru_enabled:
        raise RuntimeError(
            "pdf/image 需要 MinerU：config.yaml 里 mineru.enabled 未开"
            "（环境安装见 docs/environments.md）"
        )

    with tempfile.TemporaryDirectory() as out_dir:
        cmd = [
            "conda",
            "run",
            "--no-capture-output",
            "-n",
            cfg.mineru_conda_env,
            "env",
            "CUDA_DEVICE_ORDER=PCI_BUS_ID",
            f"CUDA_VISIBLE_DEVICES={cfg.mineru_gpu}",
            "mineru",
            "-p",
            str(path),
            "-o",
            out_dir,
            "-b",
            cfg.mineru_backend,
            "--effort",
            cfg.mineru_effort,
            "-l",
            cfg.mineru_lang,
        ]
        # ponytail: 109页实测 4m22s(~2.4s/页)，上限 30min；更大文档再调
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=1800)
        if r.returncode != 0:
            tail = (r.stderr or r.stdout)[-800:]
            raise RuntimeError(f"mineru 失败 rc={r.returncode}: {path.name}\n{tail}")

        md_files = list(Path(out_dir).rglob("*.md"))
        if not md_files:
            raise RuntimeError(f"mineru 未生成 markdown: {path.name}")
        md = md_files[0].read_text(encoding="utf-8")

        # images/ 移到原始文件旁（统一布局；md 里相对路径引用不断链）
        for img_dir in Path(out_dir).rglob("images"):
            if img_dir.is_dir():
                dest = path.parent / "images"
                dest.mkdir(exist_ok=True)
                for img in img_dir.iterdir():
                    shutil.move(str(img), dest / img.name)
                break
    return md


# ── 转换器 ────────────────────────────────────────────────


def _convert_with_markitdown(path: Path) -> Optional[str]:
    """MarkItDown：DOCX(mammoth)/PPTX/XLSX/CSV → Markdown，失败返回 None 走兜底。"""
    try:
        from markitdown import MarkItDown

        result = MarkItDown(enable_plugins=False).convert(str(path))
        text = (result.text_content or "").strip()
        if not text:
            return None
        # 内嵌图片是 base64 内联的，不落盘；留占位（真源在 L0 原始文件的 zip 里）
        text = re.sub(r"!\[([^\]]*)\]\(data:[^)]*\)", r"[图片]", text)
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
                [
                    "libreoffice",
                    "--headless",
                    "--convert-to",
                    "txt:Text (encoded)",
                    "--outdir",
                    td,
                    str(path),
                ],
                capture_output=True,
                text=True,
                timeout=60,
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
            info = zipfile.ZipInfo(
                filename=item.filename.replace("\\", "/"), date_time=item.date_time
            )
            info.compress_type = item.compress_type
            zout.writestr(info, zin.read(item.filename))
    shutil.move(tmp, path)
    return path


def _convert_docx(path: Path) -> str:
    path = _normalize_docx_paths(path)
    for conv in (_convert_with_markitdown, _convert_with_libreoffice):
        if result := conv(path):
            return result  # 缓存由入口统一存
    raise RuntimeError(f"DOCX 转换失败: {path.name}")


def _convert_doc(path: Path) -> str:
    # ponytail: antiword 已删（真实数据无 .doc，libreoffice 能转）；.doc 多了再加回
    if result := _convert_with_libreoffice(path):
        return result
    raise RuntimeError(f"DOC 转换失败: {path.name}")


def _convert_xlsx(path: Path) -> str:
    if result := _convert_with_markitdown(path):
        return result
    raise RuntimeError(f"XLSX 转换失败: {path.name}")


def _convert_csv(path: Path) -> str:
    return _convert_xlsx(path)  # 同走 MarkItDown


def _convert_pptx(path: Path) -> str:
    return _convert_docx(path)  # 同级降级链


def _extract_zip_media(path: Path) -> None:
    """docx/pptx 内嵌图提取到同目录 images/（统一布局；md 里留 [图片] 占位）。"""
    import shutil
    import zipfile

    try:
        with zipfile.ZipFile(path) as z:
            media = [n for n in z.namelist() if "/media/" in n]
            if not media:
                return
            dest = path.parent / "images"
            dest.mkdir(exist_ok=True)
            for name in media:
                with z.open(name) as src, open(dest / Path(name).name, "wb") as out:
                    shutil.copyfileobj(src, out)
    except zipfile.BadZipFile:
        pass  # 非 zip（如 xls），让下游报错


def convert_to_markdown_file(path) -> "Path":
    """统一布局落盘：md 写到原始文件旁边，docx/pptx 的内嵌图提取到 images/。"""
    path = Path(path)
    if detect_format(str(path)) == "md":
        return path
    dest = path.with_suffix(".md")
    if dest.exists():
        return dest  # 重派生：md 已在原始文件旁，免转换
    md = convert_to_markdown(str(path))
    dest.write_text(md, encoding="utf-8")
    fmt = detect_format(str(path))
    if fmt in ("docx", "pptx"):
        _extract_zip_media(path)
    return dest


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
    if fmt in ("pdf", "image"):
        return _run_mineru(path)  # 同一条 MinerU 路（文字直提+OCR）
    return {
        "doc": _convert_doc,
        "docx": _convert_docx,
        "pptx": _convert_pptx,
        "xlsx": _convert_xlsx,
        "xls": _convert_xlsx,
        "csv": _convert_csv,
    }[fmt](path)
