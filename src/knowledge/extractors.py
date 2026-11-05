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
import re
import threading
import zipfile
from dataclasses import dataclass, field
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Callable, Dict, Iterator, List, Optional, Tuple

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


def _suffix_of(filename: str) -> str:
    return Path(filename).suffix.lower()


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
    data: bytes,
    filename: str,
    *,
    limits: Optional[ExtractionLimits] = None,
    allow_low_quality: bool = False,
    on_page: Optional[Callable[[int, int], None]] = None,
    cancel_event: Optional[threading.Event] = None,
) -> ExtractedDocument:
    """Extract ``data`` according to the filename suffix.

    ``allow_low_quality=False`` (default) refuses a text layer full of
    replacement/block glyphs with a readable 422; passing ``True`` is the user
    explicitly saying "I know it is bad, store it anyway". ``on_page`` and
    ``cancel_event`` exist for async jobs: progress is reported per extracted
    page and cancellation is checked at the same boundary, before anything is
    written.

    Raises :class:`ExtractionError` subclasses with a readable message; the
    caller maps ``http_status`` directly onto its response. Never returns an
    empty document for a format that had content but no readable text.
    """
    caps = limits or DEFAULT_LIMITS
    suffix = _suffix_of(filename)
    _check_size(len(data), caps)

    if suffix in _TEXT_SUFFIXES:
        return _extract_plain_text(data, suffix, caps, allow_low_quality)
    if suffix in _PDF_SUFFIXES:
        return _extract_pdf(
            data,
            caps,
            allow_low_quality=allow_low_quality,
            on_page=on_page,
            cancel_event=cancel_event,
        )
    if suffix in _DOCX_SUFFIXES:
        return _extract_docx(data, caps, allow_low_quality)
    if suffix in _HTML_SUFFIXES:
        return _extract_html(data, caps, allow_low_quality)

    raise UnsupportedFileType(
        f"不支持的文件类型：{suffix or '未知后缀'}；"
        "支持 .txt / .md / .pdf / .docx / .html / .htm"
    )


# ---------------------------------------------------------------------------
# Plain text
# ---------------------------------------------------------------------------


def _extract_plain_text(
    data: bytes,
    suffix: str,
    limits: ExtractionLimits,
    allow_low_quality: bool,
) -> ExtractedDocument:
    try:
        text = data.decode("utf-8-sig")
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


def _extract_pdf(
    data: bytes,
    limits: ExtractionLimits,
    *,
    allow_low_quality: bool = False,
    on_page: Optional[Callable[[int, int], None]] = None,
    cancel_event: Optional[threading.Event] = None,
) -> ExtractedDocument:
    # Imported lazily so the knowledge module still imports (and reports a
    # clear error) in an environment where the optional parser is missing.
    try:
        from pypdf import PdfReader
    except ImportError as exc:  # pragma: no cover - declaration is mandatory
        raise CorruptedDocument(
            f"PDF 解析器未安装（pypdf）：{exc}；请执行 pip install pypdf"
        )

    try:
        reader = PdfReader(io.BytesIO(data), strict=False)
    except Exception as exc:
        raise CorruptedDocument(f"PDF 文件损坏或格式不合法，无法解析：{exc}")

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
        raise CorruptedDocument(f"PDF 页目录损坏，无法读取：{exc}")

    if page_count == 0:
        raise EmptyDocument("PDF 没有任何页面")
    if page_count > limits.max_pdf_pages:
        raise TooManyPages(
            f"PDF 页数超过上限：{page_count} 页，最多 {limits.max_pdf_pages} 页；"
            "请拆分后再上传"
        )

    pages: List[Tuple[int, str]] = []
    for index in range(page_count):
        # Cancellation during extraction is a full rollback too: nothing has
        # been written yet, so the job simply reports how many pages it read.
        if cancel_event is not None and cancel_event.is_set():
            raise ExtractionCancelled(index, page_count)
        try:
            page_text = reader.pages[index].extract_text() or ""
        except Exception as exc:
            raise CorruptedDocument(
                f"PDF 第 {index + 1} 页解析失败：{exc}"
            )
        pages.append((index + 1, page_text))
        if on_page is not None:
            # Progress is counted in real pages, blank ones included: they were
            # genuinely parsed and skipping them would fake the percentage.
            on_page(index + 1, page_count)

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


def _zip_is_safe(data: bytes, limits: ExtractionLimits) -> None:
    """Refuse a zip bomb before python-docx expands it into memory."""
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
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
    data: bytes, limits: ExtractionLimits, allow_low_quality: bool = False
) -> ExtractedDocument:
    _zip_is_safe(data, limits)
    try:
        from docx import Document
    except ImportError as exc:  # pragma: no cover - declaration is mandatory
        raise CorruptedDocument(
            f"DOCX 解析器未安装（python-docx）：{exc}；请执行 pip install python-docx"
        )

    try:
        document = Document(io.BytesIO(data))
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
    data: bytes, limits: ExtractionLimits, allow_low_quality: bool = False
) -> ExtractedDocument:
    try:
        raw = data.decode("utf-8-sig")
    except UnicodeDecodeError:
        try:
            raw = data.decode("gb18030")
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
