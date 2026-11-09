"""File import for the knowledge base: PDF / DOCX / HTML / plain text.

Why this module exists
======================
The upload surface used to accept only ``.txt`` / ``.md`` and answered PDF with
"不会假装解析". Real libraries are mostly PDF and Word, so this module turns an
uploaded byte stream into the *same* extracted text that the chunker, the
content hash and retrieval already understand.

Hard rules (stated, not hidden)
===============================
* **A scanned / image-only PDF is an error, never an empty success.** If no page
  yields a text layer the caller gets ``ScannedPdfError`` with
  ``该 PDF 无文本层，需要 OCR，暂不支持``. Returning ``""`` would silently land
  an empty document in the index and make every later search miss look like a
  relevance problem.
* **Encrypted / corrupt files get their own error**, not a generic 500.
* **Page numbers survive into chunk metadata** (``page``), so a search hit can
  say "来自第 N 页" instead of quoting an anonymous paragraph.
* **Limits are checked before the expensive work**, so an oversized upload is
  refused with a readable reason instead of failing at OOM: file size, PDF page
  count, DOCX decompressed size, extracted character count.
* **Dedup hashes the extracted text** (the service hashes the text it receives),
  so the same PDF renamed ``manual.pdf`` -> ``manual-copy.pdf`` does not create
  a second document.

Library choices (licenses verified from installed package metadata)
===================================================================
* ``pypdf`` 6.21.4 -- **BSD-3-Clause**. Chosen over ``pymupdf`` (AGPL, forbidden
  here), ``pdfminer.six`` (MIT but lower-level page plumbing) and
  ``pdfplumber`` (MIT but pulls pdfminer.six + imaging extras). pypdf is pure
  Python, has a stable per-page ``extract_text()`` API, exposes
  ``is_encrypted`` / ``decrypt`` and raises typed errors we can map to readable
  messages. No system binary (``pdftotext`` / poppler) required.
* ``python-docx`` 1.2.0 -- **MIT**. The only mainstream pure-Python OOXML
  reader that exposes paragraph **style names** (``Heading 1``...), which is
  exactly what the structure-aware chunker needs for ``heading_path``.
  ``docx2txt`` loses that hierarchy; ``mammoth`` converts to HTML and pulls
  extra dependencies for no benefit here.
* **HTML uses the standard library** (``html.parser``) -- no new dependency and
  therefore no new license surface. It is lenient with malformed markup, and
  heading / paragraph / table structure is all this importer needs.
"""

from __future__ import annotations

import io
import multiprocessing
import re
import threading
import time
import zipfile
import zlib
from dataclasses import dataclass, field
from html.parser import HTMLParser
from pathlib import Path
from queue import Empty as _QueueEmpty
from typing import Any, Callable, Dict, Iterator, List, Optional, Tuple, Union

_TEXT_SUFFIXES = frozenset({".txt", ".md"})
_PDF_SUFFIXES = frozenset({".pdf"})
_DOCX_SUFFIXES = frozenset({".docx"})
_HTML_SUFFIXES = frozenset({".html", ".htm"})

SUPPORTED_SUFFIXES = (
    _TEXT_SUFFIXES | _PDF_SUFFIXES | _DOCX_SUFFIXES | _HTML_SUFFIXES
)

#: Exact wording required by the product contract for a scanned PDF.
SCANNED_PDF_MESSAGE = "该 PDF 无文本层，需要 OCR，暂不支持"


@dataclass(frozen=True)
class ExtractionLimits:
    """Hard caps enforced **before** the expensive parse/embedding work.

    20MB compressed input is already far beyond a normal manual, and 300 pages
    keeps a full text extraction + chunking well inside a request budget. The
    uncompressed DOCX cap is a zip-bomb guard: a 20MB ``.docx`` can expand into
    gigabytes of XML.
    """

    max_file_bytes: int = 20 * 1024 * 1024
    max_pdf_pages: int = 300
    max_uncompressed_bytes: int = 150 * 1024 * 1024
    #: PDF 单条 Flate 流解压上限（pypdf 每个流各自解压，没有总量概念）。
    max_pdf_stream_uncompressed_bytes: int = 32 * 1024 * 1024
    #: PDF 全部 Flate 流累计解压上限：20MB 的输入可以塞很多条小炸弹流。
    max_pdf_total_uncompressed_bytes: int = 150 * 1024 * 1024
    #: 单页解析时间预算（秒）。PDF 的每一页在独立子进程里解析：超过预算仍在
    #: 等待回传就强杀进程并抛 ``PdfPageTimeout``；窗口内返回但耗时超预算同样
    #: 判定超时（兼容慢页刚好卡在边界的场景）。
    pdf_page_timeout_seconds: float = 10.0
    max_extracted_chars: int = 2_000_000
    #: Garbled-text gate. A handful of replacement glyphs in a short note is
    #: noise; 20+ glyphs at a 15% ratio means the text layer is unusable.
    min_garbled_chars: int = 20
    max_garbled_ratio: float = 0.15


DEFAULT_LIMITS = ExtractionLimits()


class ExtractionError(Exception):
    """Base class: the message is user-facing and maps to ``http_status``."""

    code = "extraction_failed"
    http_status = 422


class UnsupportedFileType(ExtractionError):
    code = "unsupported_file_type"
    http_status = 415


class FileTooLarge(ExtractionError):
    code = "file_too_large"
    http_status = 413


class TooManyPages(ExtractionError):
    code = "too_many_pages"
    http_status = 413


class EmptyDocument(ExtractionError):
    code = "empty_document"
    http_status = 400


class ScannedPdfError(ExtractionError):
    code = "pdf_no_text_layer"
    http_status = 422


class EncryptedPdfError(ExtractionError):
    code = "pdf_encrypted"
    http_status = 422


class CorruptedDocument(ExtractionError):
    code = "corrupted_document"
    http_status = 422


class PdfPageTimeout(ExtractionError):
    """某页解析超过 ``pdf_page_timeout_seconds``；停止后续页面。"""

    code = "pdf_page_timeout"
    http_status = 504


class ExtractionCancelled(ExtractionError):
    """Raised between pages when an async job is cancelled during extraction.

    Nothing has been written at this point, so the job is a full rollback.
    """

    code = "extraction_cancelled"
    http_status = 409

    def __init__(self, processed: int, total: int) -> None:
        super().__init__(
            f"提取已取消（已处理 {processed}/{total} 页，未写入任何数据）"
        )
        self.processed = processed
        self.total = total


class LowQualityText(ExtractionError):
    """Text extracted with too many replacement/block glyphs to be trusted."""

    code = "low_quality_extraction"
    http_status = 422

    def __init__(self, message: str, quality: "TextQuality") -> None:
        super().__init__(message)
        self.quality = quality


#: Characters that signal a broken text layer: U+FFFD replacement glyphs,
#: geometric squares/rectangles (font-missing "tofu"), and Private Use Area
#: codepoints that embedded subset fonts often leak.
_GARBLED_RE = re.compile("[\ufffd\u25a0\u25a1\u25a2\u25a3\ue000-\uf8ff]")


@dataclass(frozen=True)
class TextQuality:
    """Measured garbled-text ratio for one extracted document."""

    garbled_chars: int
    total_chars: int
    ratio: float
    low_quality: bool

    def summary(self) -> Dict[str, Any]:
        return {
            "garbled_chars": self.garbled_chars,
            "total_chars": self.total_chars,
            "garbled_ratio": round(self.ratio, 4),
            "low_quality": self.low_quality,
        }


def assess_text_quality(
    text: str, limits: Optional[ExtractionLimits] = None
) -> TextQuality:
    """Measure broken glyphs; never guesses, just counts what is there."""
    caps = limits or DEFAULT_LIMITS
    total_chars = len(re.sub(r"\s+", "", text or ""))
    garbled_chars = len(_GARBLED_RE.findall(text or ""))
    ratio = (garbled_chars / total_chars) if total_chars else 0.0
    low_quality = (
        garbled_chars >= caps.min_garbled_chars
        and ratio >= caps.max_garbled_ratio
    )
    return TextQuality(
        garbled_chars=garbled_chars,
        total_chars=total_chars,
        ratio=ratio,
        low_quality=low_quality,
    )


def _quality_or_raise(
    text: str, limits: ExtractionLimits, allow_low_quality: bool
) -> TextQuality:
    quality = assess_text_quality(text, limits)
    if quality.low_quality and not allow_low_quality:
        raise LowQualityText(
            "该文件可能是扫描件或字体嵌入异常，提取质量差"
            f"（乱码 {quality.garbled_chars} 字符，占比 {quality.ratio:.1%}）；"
            "如确认仍需入库，请重试并设置 allow_low_quality=true",
            quality,
        )
    return quality


@dataclass
class ExtractedDocument:
    """Extracted text plus the provenance needed by chunk metadata.

    ``pages`` is non-empty only for paged formats (PDF); each item is
    ``(page_number, page_text)`` with 1-based numbers. ``text`` is the full
    extracted body and is what the service hashes for deduplication.
    """

    text: str
    file_type: str
    pages: List[Tuple[int, str]] = field(default_factory=list)
    title: Optional[str] = None
    page_count: int = 0
    metadata: Dict[str, Any] = field(default_factory=dict)
    quality: Optional[TextQuality] = None

    def summary(self) -> Dict[str, Any]:
        """Small, JSON-safe description for the upload / job response."""
        return {
            "file_type": self.file_type,
            "page_count": self.page_count,
            "text_chars": len(self.text),
            "title": self.title,
            "paged": bool(self.pages),
            "quality": self.quality.summary() if self.quality else None,
        }


#: 提取输入：内存字节，或磁盘上的临时文件路径。上传接口先把文件分块落盘，
#: 再把路径交给解析器，这样解析期间进程里不会多留一份完整文件副本
#: （``pypdf`` / ``python-docx`` / ``zipfile`` 都支持按需读盘）。
ExtractionSource = Union[bytes, bytearray, Path, str]


def _suffix_of(filename: str) -> str:
    return Path(filename).suffix.lower()


def _source_size(source: ExtractionSource) -> int:
    if isinstance(source, (bytes, bytearray)):
        return len(source)
    return Path(source).stat().st_size


def _read_source_bytes(source: ExtractionSource) -> bytes:
    """只有文本类格式需要整份字节（解码后就是要入库的正文）。"""
    if isinstance(source, (bytes, bytearray)):
        return bytes(source)
    return Path(source).read_bytes()


def _parser_source(source: ExtractionSource):
    """给 pypdf / python-docx / zipfile 的输入：路径或二进制流。"""
    if isinstance(source, (bytes, bytearray)):
        return io.BytesIO(source)
    return str(Path(source))


def _check_size(size: int, limits: ExtractionLimits) -> None:
    if size <= 0:
        raise EmptyDocument("文件为空")
    if size > limits.max_file_bytes:
        raise FileTooLarge(
            f"文件过大：{size // (1024 * 1024)}MB，上限 "
            f"{limits.max_file_bytes // (1024 * 1024)}MB；请压缩或拆分后再上传"
        )


def _check_chars(text: str, limits: ExtractionLimits) -> None:
    if len(text) > limits.max_extracted_chars:
        raise FileTooLarge(
            f"提取出的正文过长：{len(text)} 字符，上限 "
            f"{limits.max_extracted_chars} 字符；请拆分后再上传"
        )


def extract_document(
    source: ExtractionSource,
    filename: str,
    *,
    limits: Optional[ExtractionLimits] = None,
    allow_low_quality: bool = False,
    on_page: Optional[Callable[[int, int], None]] = None,
    cancel_event: Optional[threading.Event] = None,
) -> ExtractedDocument:
    """Extract ``source`` according to the filename suffix.

    ``source`` is either raw ``bytes`` or a filesystem path to a temporary
    upload; a path is preferred by the HTTP layer so the whole file is never
    copied into the process heap. ``allow_low_quality=False`` (default) refuses
    a text layer full of replacement/block glyphs with a readable 422; passing
    ``True`` is the user explicitly saying "I know it is bad, store it anyway".
    ``on_page`` and ``cancel_event`` exist for async jobs: progress is reported
    per extracted page and cancellation is checked at the same boundary, before
    anything is written.

    Raises :class:`ExtractionError` subclasses with a readable message; the
    caller maps ``http_status`` directly onto its response. Never returns an
    empty document for a format that had content but no readable text.
    """
    caps = limits or DEFAULT_LIMITS
    suffix = _suffix_of(filename)
    _check_size(_source_size(source), caps)

    if suffix in _TEXT_SUFFIXES:
        return _extract_plain_text(source, suffix, caps, allow_low_quality)
    if suffix in _PDF_SUFFIXES:
        return _extract_pdf(
            source,
            caps,
            allow_low_quality=allow_low_quality,
            on_page=on_page,
            cancel_event=cancel_event,
        )
    if suffix in _DOCX_SUFFIXES:
        return _extract_docx(source, caps, allow_low_quality)
    if suffix in _HTML_SUFFIXES:
        return _extract_html(source, caps, allow_low_quality)

    raise UnsupportedFileType(
        f"不支持的文件类型：{suffix or '未知后缀'}；"
        "支持 .txt / .md / .pdf / .docx / .html / .htm"
    )


# ---------------------------------------------------------------------------
# Plain text
# ---------------------------------------------------------------------------


def _extract_plain_text(
    source: ExtractionSource,
    suffix: str,
    limits: ExtractionLimits,
    allow_low_quality: bool,
) -> ExtractedDocument:
    try:
        text = _read_source_bytes(source).decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise EmptyDocument(
            f"文件不是 UTF-8 编码：{exc}；请另存为 UTF-8 后重新上传"
        )
    if not text.strip():
        raise EmptyDocument("文件未包含任何正文文本")
    _check_chars(text, limits)
    quality = _quality_or_raise(text, limits, allow_low_quality)
    return ExtractedDocument(
        text=text,
        file_type=suffix.lstrip("."),
        metadata={},
        quality=quality,
    )


# ---------------------------------------------------------------------------
# PDF
# ---------------------------------------------------------------------------


_PDF_STREAM_START_RE = re.compile(rb"stream\r?\n")


def _guard_pdf_decompression(
    source: ExtractionSource, limits: ExtractionLimits
) -> None:
    """在 pypdf 解析前，对每条 ``/FlateDecode`` 流做有界解压。

    pypdf 单个流有自己的输出上限，但没有“全文件累计”上限；一个 20MB 的
    PDF 可以塞上千条小流，每条膨胀几十 MB，最终把内存打爆。这里按
    ``max_pdf_stream_uncompressed_bytes`` / ``max_pdf_total_uncompressed_bytes``
    逐条有界解压并累计，超限即 ``FileTooLarge``，且不会把完整解压结果留在内存。

    只处理 Flate（PDF 解压炸弹的主流编码）；LZW/ASCII85 等仍由 pypdf 自身上限
    约束。这里只做边界检查，不修改源文件。
    """
    raw = _read_source_bytes(source)
    per_stream = limits.max_pdf_stream_uncompressed_bytes
    total_limit = limits.max_pdf_total_uncompressed_bytes
    total = 0
    for match in _PDF_STREAM_START_RE.finditer(raw):
        start = match.end()
        end = raw.find(b"endstream", start)
        if end <= start:
            continue
        header = raw[max(0, match.start() - 512) : match.start()]
        if b"/FlateDecode" not in header:
            continue
        data = raw[start:end]
        decompressor = zlib.decompressobj()
        emitted = 0
        chunk = data
        try:
            while chunk:
                # 多留 1 字节，能拿到就说明已超过本流上限。
                piece = decompressor.decompress(chunk, per_stream - emitted + 1)
                emitted += len(piece)
                total += len(piece)
                if emitted > per_stream:
                    raise FileTooLarge(
                        f"PDF 单条流解压后超过上限 {per_stream // (1024 * 1024)}MB，"
                        "疑似解压炸弹，已拒绝解析"
                    )
                if total > total_limit:
                    raise FileTooLarge(
                        f"PDF 解压后累计体积超过上限 "
                        f"{total_limit // (1024 * 1024)}MB，疑似解压炸弹，已拒绝解析"
                    )
                if decompressor.unconsumed_tail:
                    chunk = decompressor.unconsumed_tail
                else:
                    break
        except zlib.error:
            # 不是有效 Flate 流：交给 pypdf 报它自己的损坏错误。
            continue


#: 子进程启动方式。Windows / macOS 只有 ``spawn`` 可用；``fork`` 会复制父进程的
#: 线程与锁状态，对 FastAPI 工作线程不安全。
_PDF_WORKER_START_METHOD = "spawn"

#: 子进程只回传错误类名与文案，父进程按名字重建异常，避免跨进程 pickle 自定义
#: 异常对象时的兼容问题。这里列出子进程可能抛出的全部提取错误。
_PDF_WORKER_ERRORS = {
    cls.__name__: cls
    for cls in (
        UnsupportedFileType,
        FileTooLarge,
        TooManyPages,
        EmptyDocument,
        ScannedPdfError,
        EncryptedPdfError,
        CorruptedDocument,
        PdfPageTimeout,
    )
}


def _collect_pdf_pages(
    source: ExtractionSource,
    limits: ExtractionLimits,
    *,
    on_meta: Callable[[int], None],
    on_page: Callable[[int, int, str], None],
) -> None:
    """真正解析 PDF 的部分；只在子进程里跑（父进程才能强杀它）。

    ``on_meta`` 先报总页数，``on_page`` 每解析完一页回传一页正文。异常原样抛出，
    由子进程入口序列化回父进程——父进程不做任何解析，只计时与收页。
    """
    # Imported lazily so the knowledge module still imports (and reports a
    # clear error) in an environment where the optional parser is missing.
    try:
        from pypdf import PdfReader
    except ImportError as exc:  # pragma: no cover - declaration is mandatory
        raise CorruptedDocument(
            f"PDF 解析器未安装（pypdf）：{exc}；请执行 pip install pypdf"
        ) from exc

    # 必须在 PdfReader 读取 xref/内容流之前拦截，否则炸弹已经膨胀完了。
    _guard_pdf_decompression(source, limits)

    try:
        reader = PdfReader(_parser_source(source), strict=False)
    except Exception as exc:
        raise CorruptedDocument(f"PDF 文件损坏或格式不合法，无法解析：{exc}") from exc

    if reader.is_encrypted:
        # Owner-password-only PDFs (very common) open with the empty password;
        # a real user password returns 0 and must be reported, not guessed.
        try:
            unlocked = reader.decrypt("")
        except Exception:
            unlocked = 0
        if not unlocked:
            raise EncryptedPdfError(
                "PDF 已加密，无法读取文本层；请上传未加密的 PDF 或先解除加密"
            )

    try:
        page_count = len(reader.pages)
    except Exception as exc:
        raise CorruptedDocument(f"PDF 页目录损坏，无法读取：{exc}") from exc

    if page_count == 0:
        raise EmptyDocument("PDF 没有任何页面")
    if page_count > limits.max_pdf_pages:
        raise TooManyPages(
            f"PDF 页数超过上限：{page_count} 页，最多 {limits.max_pdf_pages} 页；"
            "请拆分后再上传"
        )

    # 先把总页数交给父进程：取消请求与进度条都要在解析第一页之前拿到分母。
    on_meta(page_count)
    for index in range(page_count):
        try:
            page_text = reader.pages[index].extract_text() or ""
        except Exception as exc:
            raise CorruptedDocument(
                f"PDF 第 {index + 1} 页解析失败：{exc}"
            ) from exc
        on_page(index + 1, page_count, page_text)


def _pdf_worker(
    source: ExtractionSource, limits: ExtractionLimits, sink: Any
) -> None:
    """子进程入口：只解析、只回传，不做超时判断。

    ``pypdf`` 是纯 Python，卡在某页的 ``extract_text()`` 里没有任何协作式中断点；
    Windows 上唯一能真正停下它的手段是 ``Process.kill()``（``TerminateProcess``）。
    因此超时判定必须留在父进程。
    """
    try:
        _collect_pdf_pages(
            source,
            limits,
            on_meta=lambda total: sink.put(("meta", total)),
            on_page=lambda index, total, text: sink.put(
                ("page", index, total, text)
            ),
        )
    except ExtractionError as exc:
        sink.put(("error", type(exc).__name__, str(exc)))
    except BaseException as exc:  # noqa: BLE001 - 子进程必须把失败交回父进程
        sink.put(("crash", f"{type(exc).__name__}: {exc}"))
    else:
        sink.put(("done",))


def _release_pdf_worker(process: Any, sink: Any) -> None:
    """超时、取消、异常、成功四条路径都必须回收进程与队列句柄。"""
    try:
        if process.is_alive():
            process.kill()
    except Exception:  # noqa: BLE001 - 进程可能尚未启动或已退出
        pass
    try:
        process.join(timeout=3)
    except Exception:  # noqa: BLE001
        pass
    if process.is_alive():
        try:
            process.terminate()
            process.join(timeout=2)
        except Exception:  # noqa: BLE001
            pass
    try:
        sink.close()
    except Exception:  # noqa: BLE001
        pass
    try:
        # 父进程侧没有 feeder 线程需要 join，明确取消可避免退出时卡住。
        sink.cancel_join_thread()
    except Exception:  # noqa: BLE001
        pass
    try:
        if not process.is_alive():
            # 释放 Windows 进程句柄；进程还活着时 close() 会报错，故先判活。
            process.close()
    except Exception:  # noqa: BLE001
        pass


def _extract_pdf(
    source: ExtractionSource,
    limits: ExtractionLimits,
    *,
    allow_low_quality: bool = False,
    on_page: Optional[Callable[[int, int], None]] = None,
    cancel_event: Optional[threading.Event] = None,
) -> ExtractedDocument:
    pages, page_count = _extract_pdf_isolated(
        source,
        limits,
        on_page=on_page,
        cancel_event=cancel_event,
    )

    text = "\n\n".join(page_text for _, page_text in pages).strip()
    if not text:
        # This is the line that keeps "success with an empty string" impossible.
        raise ScannedPdfError(SCANNED_PDF_MESSAGE)
    _check_chars(text, limits)
    quality = _quality_or_raise(text, limits, allow_low_quality)

    text_pages = sum(1 for _, page_text in pages if page_text.strip())
    return ExtractedDocument(
        text=text,
        file_type="pdf",
        pages=pages,
        page_count=page_count,
        metadata={
            "page_count": page_count,
            "pages_with_text": text_pages,
            "parser": "pypdf",
        },
        quality=quality,
    )


def _extract_pdf_isolated(
    source: ExtractionSource,
    limits: ExtractionLimits,
    *,
    on_page: Optional[Callable[[int, int], None]],
    cancel_event: Optional[threading.Event],
) -> Tuple[List[Tuple[int, str]], int]:
    """在可强杀的子进程里解析 PDF；超时/取消即杀进程并返回中文错误。

    为什么必须是进程而不是线程：Windows 不能中断一个正在运行的 Python 线程，
    而 ``pypdf`` 的 ``extract_text()`` 是纯 Python 死循环级别的 CPU 占用时，线程
    只能等它自己跑完。子进程用 ``spawn`` 启动，父进程在 ``sink.get`` 上计时，
    超过 ``pdf_page_timeout_seconds`` 就 ``kill()``，进程、队列、临时状态在
    ``finally`` 里全部释放；父进程本身只做等待与组装，不会被拖住。
    """
    context = multiprocessing.get_context(_PDF_WORKER_START_METHOD)
    sink = context.Queue()
    process = context.Process(
        target=_pdf_worker,
        args=(source, limits, sink),
        daemon=True,
    )
    pages: List[Tuple[int, str]] = []
    page_count = 0
    budget = float(limits.pdf_page_timeout_seconds)

    try:
        process.start()
    except Exception as exc:  # noqa: BLE001 - 启动失败要给出可读原因
        _release_pdf_worker(process, sink)
        raise CorruptedDocument(f"PDF 解析子进程启动失败：{exc}") from exc

    try:
        # 第一页的计时从“启动 + 打开 + 解析”整体算起：xref 畸形时卡在打开阶段
        # 同样要能被掐掉。之后每页独立计时。
        page_started = time.monotonic()
        while True:
            try:
                item = sink.get(timeout=budget)
            except _QueueEmpty:
                raise PdfPageTimeout(
                    f"PDF 第 {len(pages) + 1} 页解析超过 {budget:.0f}s 上限，"
                    "解析进程已被强制终止；该页可能已损坏或异常复杂，"
                    "请拆分后再上传"
                ) from None
            except (EOFError, OSError) as exc:
                # 子进程在回传前异常死亡（管道已关闭）：同样按损坏处理，
                # 不能把裸 EOFError 抛到接口层。
                raise CorruptedDocument(f"PDF 解析进程异常退出：{exc}") from exc

            kind = item[0]
            if kind == "done":
                return pages, page_count or len(pages)
            if kind == "error":
                _, error_name, message = item
                raise _PDF_WORKER_ERRORS.get(error_name, CorruptedDocument)(
                    message
                )
            if kind == "crash":
                raise CorruptedDocument(f"PDF 解析进程异常退出：{item[1]}")

            if kind == "meta":
                page_count = int(item[1])
                # 拿到总页数后立即响应取消：此时一页都还没读，处理数为 0。
                if cancel_event is not None and cancel_event.is_set():
                    raise ExtractionCancelled(0, page_count)
                continue

            _, index, total, page_text = item
            page_count = int(total)
            elapsed = time.monotonic() - page_started
            if elapsed > budget:
                # 窗口内返回但耗时超预算：与硬超时同样拒绝，文案保留真实耗时。
                raise PdfPageTimeout(
                    f"PDF 第 {index} 页解析耗时 {elapsed:.1f}s，超过 "
                    f"{budget:.0f}s 上限；解析进程已被强制终止，请拆分后再上传"
                )
            pages.append((int(index), page_text))
            if on_page is not None:
                # Progress is counted in real pages, blank ones included: they
                # were genuinely parsed and skipping them fakes the percentage.
                on_page(int(index), page_count)
            if cancel_event is not None and cancel_event.is_set():
                raise ExtractionCancelled(len(pages), page_count)
            page_started = time.monotonic()
    finally:
        _release_pdf_worker(process, sink)


# ---------------------------------------------------------------------------
# DOCX
# ---------------------------------------------------------------------------


def _heading_level(style_name: str) -> Optional[int]:
    """Map a Word style name to a markdown heading level (1-6)."""
    name = (style_name or "").strip().lower()
    match = re.match(r"(?:heading|标题)\s*([1-6])", name)
    if match:
        return int(match.group(1))
    if name in {"title", "标题"}:
        return 1
    return None


def _zip_is_safe(source: ExtractionSource, limits: ExtractionLimits) -> None:
    """Refuse a zip bomb before python-docx expands it into memory."""
    try:
        with zipfile.ZipFile(_parser_source(source)) as archive:
            total = sum(info.file_size for info in archive.infolist())
    except zipfile.BadZipFile as exc:
        raise CorruptedDocument(f"DOCX 文件损坏（不是有效的 zip 包）：{exc}")
    if total > limits.max_uncompressed_bytes:
        raise FileTooLarge(
            f"DOCX 解压后体积过大：{total // (1024 * 1024)}MB，上限 "
            f"{limits.max_uncompressed_bytes // (1024 * 1024)}MB"
        )


def _iter_docx_blocks(document) -> Iterator[Any]:
    """Yield paragraphs and tables in document order (python-docx API)."""
    from docx.table import Table
    from docx.text.paragraph import Paragraph

    body = document.element.body
    for child in body.iterchildren():
        if child.tag.endswith("}p"):
            yield Paragraph(child, document)
        elif child.tag.endswith("}tbl"):
            yield Table(child, document)


def _extract_docx(
    source: ExtractionSource, limits: ExtractionLimits, allow_low_quality: bool = False
) -> ExtractedDocument:
    _zip_is_safe(source, limits)
    try:
        from docx import Document
    except ImportError as exc:  # pragma: no cover - declaration is mandatory
        raise CorruptedDocument(
            f"DOCX 解析器未安装（python-docx）：{exc}；请执行 pip install python-docx"
        )

    try:
        document = Document(_parser_source(source))
    except Exception as exc:
        raise CorruptedDocument(f"DOCX 文件损坏或格式不合法，无法解析：{exc}")

    from docx.table import Table

    blocks: List[str] = []
    title: Optional[str] = None
    for block in _iter_docx_blocks(document):
        if isinstance(block, Table):
            rows: List[str] = []
            for row in block.rows:
                cells = [
                    " ".join(cell.text.split()) for cell in row.cells
                ]
                if any(cells):
                    rows.append("| " + " | ".join(cells) + " |")
            if rows:
                blocks.append("\n".join(rows))
            continue

        text = " ".join(block.text.split())
        if not text:
            continue
        level = _heading_level(getattr(getattr(block, "style", None), "name", ""))
        if level is not None:
            blocks.append(f"{'#' * level} {text}")
            if title is None:
                title = text
        else:
            blocks.append(text)

    body = "\n\n".join(blocks).strip()
    if not body:
        raise EmptyDocument("DOCX 文件未提取到任何正文文本")
    _check_chars(body, limits)
    quality = _quality_or_raise(body, limits, allow_low_quality)

    core_title = getattr(document.core_properties, "title", None)
    if not (core_title or "").strip():
        core_title = None
    return ExtractedDocument(
        text=body,
        file_type="docx",
        title=core_title or title,
        metadata={"parser": "python-docx"},
        quality=quality,
    )


# ---------------------------------------------------------------------------
# HTML
# ---------------------------------------------------------------------------

_HTML_SKIP_TAGS = frozenset({"script", "style", "noscript", "template"})
_HTML_BLOCK_TAGS = frozenset(
    {
        "p", "div", "section", "article", "header", "footer", "main",
        "aside", "ul", "ol", "blockquote", "pre", "figure", "hr",
    }
)
_HTML_HEADING_TAGS = frozenset({f"h{level}" for level in range(1, 7)})


class _HtmlTextParser(HTMLParser):
    """Lenient HTML -> structure-preserving text.

    Headings become markdown ``#`` lines so the existing chunker builds the
    same ``heading_path`` it does for Markdown. Script/style bodies are dropped.
    """

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.blocks: List[str] = []
        self.title: Optional[str] = None
        self._buffer: List[str] = []
        self._skip_depth = 0
        self._in_title = False
        self._heading_level = 0
        self._row_cells: List[str] = []
        self._cell_buffer: List[str] = []
        self._in_cell = False

    # -- buffer helpers ----------------------------------------------------
    def _take(self) -> str:
        text = " ".join("".join(self._buffer).split())
        self._buffer = []
        return text

    def _flush(self, prefix: str = "") -> None:
        text = self._take()
        if text:
            self.blocks.append(f"{prefix}{text}")

    def _finish_cell(self) -> None:
        if not self._in_cell:
            return
        text = " ".join("".join(self._cell_buffer).split())
        self._cell_buffer = []
        self._in_cell = False
        self._row_cells.append(text)

    def _flush_row(self) -> None:
        self._finish_cell()
        if self._row_cells:
            cells = [cell for cell in self._row_cells if cell]
            if cells:
                self.blocks.append("| " + " | ".join(cells) + " |")
        self._row_cells = []

    # -- parser callbacks --------------------------------------------------
    def handle_starttag(self, tag: str, attrs) -> None:
        tag = tag.lower()
        if tag in _HTML_SKIP_TAGS:
            self._skip_depth += 1
            return
        if self._skip_depth:
            return
        if tag == "title":
            self._in_title = True
            self._flush()
            return
        if tag in _HTML_HEADING_TAGS:
            self._flush()
            self._heading_level = int(tag[1])
            return
        if tag == "tr":
            self._flush()
            self._flush_row()
            return
        if tag in {"td", "th"}:
            self._finish_cell()
            self._in_cell = True
            return
        if tag == "br":
            self._buffer.append("\n")
            return
        if tag == "li":
            self._flush()
            self._buffer.append("- ")
            return
        if tag in _HTML_BLOCK_TAGS:
            self._flush()

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if tag in _HTML_SKIP_TAGS:
            self._skip_depth = max(0, self._skip_depth - 1)
            return
        if self._skip_depth:
            return
        if tag == "title":
            self._in_title = False
            self.title = self._take() or None
            return
        if tag in _HTML_HEADING_TAGS:
            prefix = "#" * (self._heading_level or int(tag[1])) + " "
            self._flush(prefix)
            self._heading_level = 0
            return
        if tag in {"td", "th"}:
            self._finish_cell()
            return
        if tag == "tr":
            self._flush_row()
            return
        if tag in _HTML_BLOCK_TAGS:
            self._flush()

    def handle_data(self, data: str) -> None:
        if self._skip_depth:
            return
        if self._in_title:
            self._buffer.append(data)
        elif self._in_cell:
            self._cell_buffer.append(data)
        else:
            self._buffer.append(data)

    def result(self) -> str:
        self._flush()
        self._flush_row()
        return "\n\n".join(self.blocks).strip()


def _extract_html(
    source: ExtractionSource, limits: ExtractionLimits, allow_low_quality: bool = False
) -> ExtractedDocument:
    raw_bytes = _read_source_bytes(source)
    try:
        raw = raw_bytes.decode("utf-8-sig")
    except UnicodeDecodeError:
        try:
            raw = raw_bytes.decode("gb18030")
        except UnicodeDecodeError as exc:
            raise EmptyDocument(
                f"HTML 文件编码无法识别（既不是 UTF-8 也不是 GB18030）：{exc}"
            )

    parser = _HtmlTextParser()
    try:
        parser.feed(raw)
        parser.close()
    except Exception as exc:  # HTMLParser is lenient; this is a safety net
        raise CorruptedDocument(f"HTML 文件解析失败：{exc}")

    body = parser.result()
    if not body:
        raise EmptyDocument("HTML 文件未提取到任何正文文本")
    _check_chars(body, limits)
    quality = _quality_or_raise(body, limits, allow_low_quality)
    return ExtractedDocument(
        text=body,
        file_type="html",
        title=parser.title,
        metadata={"parser": "stdlib-html.parser"},
        quality=quality,
    )
