"""OCR 状态面专属测试（完善第二轮 A4）。

只测状态与输入校验：引擎在不在是环境事实，断言状态结构与可用性
自洽；图片校验（格式/空/超限）在引擎缺席时也必须先行拦住。
真正的识别质量要装 rapidocr 引擎后在真机验，不在这里编造。
"""

from __future__ import annotations

import pytest

from src import ocr


def test_ocr_status_shape_and_consistency():
    status = ocr.ocr_status()
    assert status["available"] == ocr.ocr_available()
    if status["available"]:
        assert status["engine"] == "rapidocr"
        assert status["install_hint"] == ""
    else:
        assert status["engine"] is None
        assert status["install_hint"]  # 没引擎时必须给安装提示，不许空白
    assert "不送上云" in status["note"]


def test_check_image_rejects_bad_suffix():
    with pytest.raises(ValueError, match="不支持的图片格式"):
        ocr._check_image(b"\x89PNG", "note.txt")


def test_check_image_rejects_empty():
    with pytest.raises(ValueError, match="空"):
        ocr._check_image(b"", "a.png")


def test_check_image_rejects_oversize(monkeypatch):
    monkeypatch.setattr(ocr, "MAX_IMAGE_BYTES", 4)
    with pytest.raises(ValueError, match="太大"):
        ocr._check_image(b"12345", "a.png")


def test_check_image_accepts_supported_suffixes():
    for name in ("a.png", "b.jpg", "c.JPEG", "d.webp", "e.bmp"):
        ocr._check_image(b"\x00\x01", name)  # 不抛即过


def test_ocr_image_bytes_validates_before_engine():
    # 输入校验先于引擎：坏格式在任何环境下都必须是 ValueError，
    # 不能被“引擎没装”掩盖成别的问题。
    with pytest.raises(ValueError):
        ocr.ocr_image_bytes(b"abc", "x.gif")
