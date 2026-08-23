"""extract：格式检测分支 + md 双存。重转换(markitdown/libreoffice)不进 CI。"""

import pytest

from memory.evolution.doc.extract import convert_to_markdown_file, detect_format


def test_detect_format_branches():
    assert detect_format("a.md") == "md"
    assert detect_format("a.xlsx") == "xlsx"
    assert detect_format("a.jpg") == "image"
    assert detect_format("a.exe") == "unknown"


def test_pdf_image_disabled_policy(tmp_path, monkeypatch):
    """mineru.enabled=false 时 pdf/image 报可读错误（队友无环境的默认体验）。
    monkeypatch memory.config.load_config——_run_mineru 是函数内 import，必须 patch 源头。"""
    from memory import config as cfg_mod
    from memory.evolution.doc.extract import convert_to_markdown

    orig = cfg_mod.load_config()
    orig.mineru_enabled = False
    monkeypatch.setattr(cfg_mod, "load_config", lambda: orig)

    for name, blob in (("x.pdf", b"%PDF"), ("x.png", b"\x89PNG")):
        p = tmp_path / name
        p.write_bytes(blob)
        with pytest.raises(RuntimeError, match="mineru.enabled"):
            convert_to_markdown(str(p))


def test_md_persistence(tmp_path):
    """md 已在原始文件旁则复用（重派生免转换）；源是 md 时不重复写。"""
    src = tmp_path / "报表.xlsx"
    src.write_bytes(b"fake")
    (tmp_path / "报表.md").write_text("# 转好的内容\n表格...", encoding="utf-8")
    dest = convert_to_markdown_file(src)
    assert dest == tmp_path / "报表.md"
    assert dest.read_text(encoding="utf-8").startswith("# 转好的内容")
    assert src.exists()  # 原始文件保留

    md_src = tmp_path / "笔记.md"
    md_src.write_text("# 原生md", encoding="utf-8")
    assert convert_to_markdown_file(md_src) == md_src  # md 本身就是产物


def test_unified_layout_with_images(tmp_path):
    """统一布局：docx → md 落旁边 + 内嵌图进 images/。手工构造含 media 的 docx zip。"""
    import zipfile

    src = tmp_path / "带图.docx"
    with zipfile.ZipFile(src, "w") as z:
        z.writestr("[Content_Types].xml", "<Types/>")
        z.writestr("word/document.xml", "<w:document/>")
        z.writestr("word/media/image1.png", b"\x89PNG-fake")
        z.writestr("word/media/image2.png", b"\x89PNG-fake2")

    (tmp_path / "带图.md").write_text("# 带图\n[图片] 签名处", encoding="utf-8")
    from memory.evolution.doc.extract import convert_to_markdown_file as ctmf

    dest = ctmf(src)
    assert dest == tmp_path / "带图.md"
    # md 已存在 → 早返回；但内嵌图提取独立验证：
    from memory.evolution.doc.extract import _extract_zip_media

    _extract_zip_media(src)
    assert (tmp_path / "images/image1.png").read_bytes() == b"\x89PNG-fake"
    assert (tmp_path / "images/image2.png").exists()
    assert src.exists()  # 原始保留
