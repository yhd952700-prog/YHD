"""OCR HTTP 面（0.4.6 F 组，6-3）。

* ``GET /v1/ocr/status`` — RapidOCR 装好没有。
* ``POST /v1/ocr/recognize`` — 图片 -> 识别文字（拍照识文件/发票的
  直接入口；记账拍照也走它，解析成金额在记账端做）。

识别结果永远带 ``needs_review: true`` 与中文提示\"识别结果请核对\"。
引擎没装时 503 + 安装提示。图片只在本机识别，不送上云。
"""

from __future__ import annotations

import asyncio
from typing import Any, Dict

from fastapi import APIRouter, File, HTTPException, UploadFile

from src.common.engines import EngineUnavailableError
from src.ocr import ocr_image_bytes, ocr_status

router = APIRouter(prefix="/v1/ocr", tags=["ocr"])

REVIEW_NOTE = "识别结果请核对"


@router.get("/status")
def ocr_status_endpoint() -> Dict[str, Any]:
    status = ocr_status()
    return {**status, "needs_review_note": REVIEW_NOTE}


@router.post("/recognize")
async def ocr_recognize(file: UploadFile = File(...)) -> Dict[str, Any]:
    data = await file.read()
    try:
        result = await asyncio.to_thread(
            ocr_image_bytes, data, file.filename or "image.png"
        )
    except EngineUnavailableError as exc:
        raise HTTPException(
            status_code=503,
            detail={"message": exc.detail, "install_hint": exc.install_hint},
        ) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {
        "text": result.text,
        "lines": [
            {"text": line.text, "confidence": line.confidence}
            for line in result.lines
        ],
        "engine": result.engine,
        "needs_review": True,
        "note": REVIEW_NOTE,
    }
