"""拍照识字（OCR）：RapidOCR 首选，PaddleOCR 只作备选说明。

RapidOCR 3.10.0 / rapidocr-onnxruntime（Apache-2.0，代码与模型同许可）：
把 PaddleOCR 的模型转成 ONNX，用 ONNX Runtime 跑，**不用装 PaddlePaddle
大框架**，CPU 就能跑，中英文默认支持。模型第一次识别时自动下载到
``~/.cache/rapidocr`` 一类缓存目录（之后离线）；模型文件不在代码仓里。

备选 PaddleOCR（Apache-2.0，功能更全：表格、整页转 Markdown、印章/
手写）只写在文档里，不接线：它要装 PaddlePaddle 框架，体积大、Windows
安装麻烦，RapidOCR 够用时不引。

识别结果永远带 ``needs_review=True``：前端与知识库里都标明
\"识别结果请核对\"，OCR 错字不能悄悄变成\"事实\"。
"""

from __future__ import annotations

import io
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional

from src.common.engines import EngineUnavailableError

#: 单张图片上限 20MB；拍照原图一般几 MB，够用。
MAX_IMAGE_BYTES = 20 * 1024 * 1024

#: 接受的图片后缀（知识库上传与记账拍照共用）。
IMAGE_SUFFIXES = frozenset({".png", ".jpg", ".jpeg", ".webp", ".bmp"})

_RAPIDOCR_HINT = (
    "拍照识字还没装好：先装 rapidocr-onnxruntime"
    "（pip install rapidocr-onnxruntime），首次识别会自动下载 OCR 模型"
    "（需联网一次，之后离线可用）。"
)


@dataclass(frozen=True)
class OcrLine:
    text: str
    confidence: float


@dataclass(frozen=True)
class OcrResult:
    """一次识别的结果：``text`` 是各行按阅读顺序拼起来的全文。"""

    text: str
    lines: List[OcrLine] = field(default_factory=list)
    engine: str = "rapidocr"
    needs_review: bool = True  # 永远 True：识别结果请核对。


def ocr_available() -> bool:
    try:
        import rapidocr_onnxruntime  # noqa: F401
    except Exception:
        return False
    return True


def ocr_status() -> dict:
    return {
        "available": ocr_available(),
        "engine": "rapidocr" if ocr_available() else None,
        "install_hint": "" if ocr_available() else _RAPIDOCR_HINT,
        "note": "离线本机识别，图片不送上云；识别结果请核对。",
    }


def _check_image(data: bytes, filename: str) -> None:
    suffix = Path(filename or "").suffix.lower()
    if suffix and suffix not in IMAGE_SUFFIXES:
        raise ValueError(
            f"不支持的图片格式：{suffix}；支持 " + " / ".join(sorted(IMAGE_SUFFIXES))
        )
    if len(data) == 0:
        raise ValueError("图片是空的。")
    if len(data) > MAX_IMAGE_BYTES:
        raise ValueError(f"图片太大（{len(data) // (1024 * 1024)}MB），单张上限 20MB。")


def ocr_image_bytes(data: bytes, filename: str = "image.png") -> OcrResult:
    """图片字节 -> 识别文字。引擎缺失时抛 :class:`EngineUnavailableError`。"""
    _check_image(data, filename)
    if not ocr_available():
        raise EngineUnavailableError("拍照识字还没装好，无法识别图片里的文字。", _RAPIDOCR_HINT)
    from rapidocr_onnxruntime import RapidOCR

    import numpy as np
    from PIL import Image

    try:
        image = Image.open(io.BytesIO(data)).convert("RGB")
    except Exception as exc:
        raise ValueError("图片打不开，可能是损坏的文件。") from exc
    engine = RapidOCR()
    result, _elapse = engine(np.asarray(image))
    lines: List[OcrLine] = []
    if result:
        for _box, text, score in result:
            text = (text or "").strip()
            if text:
                lines.append(OcrLine(text=text, confidence=float(score or 0.0)))
    return OcrResult(
        text="\n".join(line.text for line in lines),
        lines=lines,
        engine="rapidocr",
    )
