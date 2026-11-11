"""文本提取、中文 bigram 切词与分块。

为什么不引入分词库
==================

企业知识库必须在本机断网、零额外服务的情况下可用。中文用**重叠 bigram**：

* 不依赖词典，专有名词、缩写、新词都能命中；
* 英文/数字按词保留，``SQLite FTS5`` 的 ``unicode61`` 分词器直接可用；
* 代价是索引略大、单词级精度不如词典分词 —— 单机企业文档量下可以接受，
  且排序仍由 BM25 拉开差距，不会把无关片段顶到前面。

检索是**词法**的：页面标注「词法相关度」，不冒充语义相似度。需要语义检索时，
现有 ``src/knowledge`` 的向量链路仍然可用，本模块不重复实现。
"""

from __future__ import annotations

import re
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple
from xml.etree import ElementTree

from src.knowledge.chunker import chunk_document_structured
from src.knowledge.extractors import ExtractionError, extract_document

#: 现有提取器已支持的格式；XLSX / 图片 / CSV 由本模块补齐。
DELEGATED_SUFFIXES = frozenset({".pdf", ".docx", ".html", ".htm", ".txt", ".md"})
XLSX_SUFFIXES = frozenset({".xlsx"})
CSV_SUFFIXES = frozenset({".csv"})
IMAGE_SUFFIXES = frozenset({".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp"})

SUPPORTED_SUFFIXES = DELEGATED_SUFFIXES | XLSX_SUFFIXES | CSV_SUFFIXES | IMAGE_SUFFIXES

_MIME_BY_SUFFIX = {
    ".pdf": "application/pdf",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    ".txt": "text/plain",
    ".md": "text/markdown",
    ".csv": "text/csv",
    ".html": "text/html",
    ".htm": "text/html",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".webp": "image/webp",
    ".bmp": "image/bmp",
}

_CJK_RE = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]+")
_WORD_RE = re.compile(r"[a-z0-9][a-z0-9_+#.\-]*")
_TOKEN_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_+#.\-]*|[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]+")

#: 单条查询最多展开的 bigram 数：再长的句子也不会变成几千个 OR 项。
MAX_QUERY_TERMS = 32

#: 分块大小。600 字左右在「上下文完整」与「命中精度」之间比较稳。
CHUNK_SIZE = 600
CHUNK_OVERLAP = 60

_MAX_XLSX_ROWS = 5000
_MAX_XLSX_COLS = 100
_MAX_XLSX_CHARS = 500_000


class TextExtractionError(Exception):
    """带 HTTP 语义的提取失败；路由层直接映射状态码。"""

    def __init__(self, message: str, *, code: str = "extraction_failed", status: int = 422):
        super().__init__(message)
        self.code = code
        self.status = status


@dataclass
class ExtractedContent:
    text: str
    file_type: str
    text_source: str = "extracted"
    title: Optional[str] = None
    page_count: int = 0
    metadata: Dict[str, Any] = field(default_factory=dict)


def mime_for(filename: str) -> str:
    return _MIME_BY_SUFFIX.get(Path(filename).suffix.lower(), "application/octet-stream")


def suffix_of(filename: str) -> str:
    return Path(filename).suffix.lower()


def validate_suffix(filename: str) -> None:
    suffix = suffix_of(filename)
    if suffix not in SUPPORTED_SUFFIXES:
        supported = " / ".join(sorted(SUPPORTED_SUFFIXES))
        raise TextExtractionError(
            f"不支持的文件类型：{suffix or '未知后缀'}；支持 {supported}",
            code="unsupported_file_type",
            status=415,
        )


def extract_content(
    path: Path,
    filename: str,
    *,
    description: str = "",
    allow_low_quality: bool = False,
) -> ExtractedContent:
    """按后缀提取可检索文本。图片没有 OCR，只索引文件名/尺寸/人工说明。"""
    suffix = suffix_of(filename)
    validate_suffix(filename)
    if suffix in DELEGATED_SUFFIXES:
        try:
            extracted = extract_document(
                path, filename, allow_low_quality=allow_low_quality
            )
        except ExtractionError as exc:
            raise TextExtractionError(
                str(exc), code=getattr(exc, "code", "extraction_failed"),
                status=getattr(exc, "http_status", 422),
            ) from exc
        return ExtractedContent(
            text=extracted.text,
            file_type=extracted.file_type,
            title=extracted.title,
            page_count=extracted.page_count,
            metadata=dict(extracted.metadata or {}),
        )
    if suffix in XLSX_SUFFIXES:
        text, sheet_count, row_count = _extract_xlsx(path)
        return ExtractedContent(
            text=text,
            file_type="xlsx",
            metadata={"sheets": sheet_count, "rows": row_count},
        )
    if suffix in CSV_SUFFIXES:
        return ExtractedContent(text=_extract_csv(path), file_type="csv")
    if suffix in IMAGE_SUFFIXES:
        data = path.read_bytes()
        width, height = _image_size(data)
        lines = [f"图片文件：{filename}"]
        if width and height:
            lines.append(f"尺寸：{width}×{height}")
        if description.strip():
            lines.append(f"说明：{description.strip()}")
        else:
            lines.append("说明：未填写。图片不做 OCR，只有文件名、尺寸与人工说明可被检索。")
        return ExtractedContent(
            text="\n".join(lines),
            file_type=suffix.lstrip("."),
            text_source="image_metadata",
            metadata={"width": width, "height": height, "ocr": False},
        )
    # validate_suffix 已覆盖全部后缀，这里只是防御性兜底。
    raise TextExtractionError("不支持的文件类型", code="unsupported_file_type", status=415)


# ---------------------------------------------------------------------------
# 中文 bigram / 查询词
# ---------------------------------------------------------------------------


def segment_text(text: str) -> str:
    """把正文切成检索用 token 串：中文 bigram + 英文/数字原词。"""
    if not text:
        return ""
    lowered = text.lower()
    tokens: List[str] = []
    for chunk in re.split(r"(\s+)", lowered):
        if not chunk.strip():
            continue
        cjk = _CJK_RE.search(chunk)
        if cjk:
            for run in _CJK_RE.findall(chunk):
                if len(run) == 1:
                    tokens.append(run)
                else:
                    tokens.extend(run[i : i + 2] for i in range(len(run) - 1))
            # 中文串之间夹带的英文/数字也保留下来。
            for word in _WORD_RE.findall(chunk):
                tokens.append(word)
        else:
            tokens.extend(_WORD_RE.findall(chunk))
    return " ".join(tokens)


def query_terms(query: str) -> List[str]:
    """把用户输入展开成去重、有上限的检索词。"""
    terms: List[str] = []
    seen = set()
    for run in _TOKEN_RE.findall(query or ""):
        lowered = run.lower()
        candidates: Iterable[str]
        if _CJK_RE.fullmatch(run):
            candidates = [lowered] if len(lowered) == 1 else [
                lowered[i : i + 2] for i in range(len(lowered) - 1)
            ]
        else:
            candidates = [lowered]
        for term in candidates:
            if term and term not in seen:
                seen.add(term)
                terms.append(term)
                if len(terms) >= MAX_QUERY_TERMS:
                    return terms
    return terms


def fts_query(query: str) -> Optional[str]:
    """FTS5 MATCH 表达式：每个 bigram/单词用 OR 连接，命中越多 BM25 越高。"""
    terms = query_terms(query)
    if not terms:
        return None
    quoted = []
    for term in terms:
        safe = term.replace('"', '""')
        quoted.append(f'"{safe}"')
    return " OR ".join(quoted)


def excerpt(text: str, query: str, limit: int = 180) -> str:
    """从原文里截一段命中窗口。只返回纯文本，前端自行安全地做高亮。"""
    body = (text or "").strip()
    if len(body) <= limit:
        return body
    lowered = body.lower()
    positions: List[int] = []
    for token in _TOKEN_RE.findall(query or ""):
        token = token.lower()
        if len(token) < 1:
            continue
        found = lowered.find(token)
        if found >= 0:
            positions.append(found)
    if not positions:
        return body[:limit].rstrip() + "…"
    start = max(0, min(positions) - limit // 3)
    end = min(len(body), start + limit)
    prefix = "…" if start > 0 else ""
    suffix = "…" if end < len(body) else ""
    return f"{prefix}{body[start:end].strip()}{suffix}"


def split_chunks(text: str) -> List[Dict[str, Any]]:
    """结构感知分块：保留标题路径，表格/代码块不拆。"""
    chunks = chunk_document_structured(
        text, chunk_size=CHUNK_SIZE, chunk_overlap=CHUNK_OVERLAP
    )
    result: List[Dict[str, Any]] = []
    for chunk in chunks:
        body = chunk.text.strip()
        if not body:
            continue
        result.append(
            {
                "index": len(result),
                "text": body,
                "heading_path": " / ".join(chunk.heading_path),
                "page": chunk.page,
            }
        )
    return result


# ---------------------------------------------------------------------------
# XLSX（标准库 zipfile + ElementTree；本机没有 openpyxl，也不为此加依赖）
# ---------------------------------------------------------------------------


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _xlsx_shared_strings(archive: zipfile.ZipFile) -> List[str]:
    if "xl/sharedStrings.xml" not in archive.namelist():
        return []
    root = ElementTree.fromstring(archive.read("xl/sharedStrings.xml"))
    values: List[str] = []
    for item in root:
        if _local(item.tag) != "si":
            continue
        parts = [
            node.text or ""
            for node in item.iter()
            if _local(node.tag) == "t"
        ]
        values.append("".join(parts))
    return values


def _xlsx_sheet_order(archive: zipfile.ZipFile) -> List[Tuple[str, str]]:
    """返回 [(sheet 名, zip 内路径)]，按工作簿顺序；失败时按文件名排序。"""
    names = set(archive.namelist())
    fallback = sorted(
        name
        for name in names
        if name.startswith("xl/worksheets/sheet") and name.endswith(".xml")
    )
    if "xl/workbook.xml" not in names:
        return [(Path(path).stem, path) for path in fallback]

    rels: Dict[str, str] = {}
    if "xl/_rels/workbook.xml.rels" in names:
        rel_root = ElementTree.fromstring(archive.read("xl/_rels/workbook.xml.rels"))
        for rel in rel_root:
            rel_id = rel.attrib.get("Id")
            target = rel.attrib.get("Target", "")
            if rel_id and target:
                cleaned = target.lstrip("/")
                rels[rel_id] = cleaned if cleaned.startswith("xl/") else f"xl/{cleaned}"

    ordered: List[Tuple[str, str]] = []
    root = ElementTree.fromstring(archive.read("xl/workbook.xml"))
    for node in root.iter():
        if _local(node.tag) != "sheet":
            continue
        label = node.attrib.get("name", "")
        rel_id = next(
            (value for key, value in node.attrib.items() if _local(key) == "id"), ""
        )
        target = rels.get(rel_id, "")
        if target in names:
            ordered.append((label or Path(target).stem, target))
    return ordered or [(Path(path).stem, path) for path in fallback]


def _column_index(reference: str) -> int:
    letters = re.match(r"([A-Za-z]+)", reference or "")
    if not letters:
        return 0
    value = 0
    for char in letters.group(1).upper():
        value = value * 26 + (ord(char) - 64)
    return max(0, value - 1)


def _extract_xlsx(path: Path) -> Tuple[str, int, int]:
    try:
        archive = zipfile.ZipFile(path)
    except zipfile.BadZipFile as exc:
        raise TextExtractionError(
            "不是有效的 .xlsx 文件；旧版 .xls 请先另存为 .xlsx",
            code="corrupted_document",
        ) from exc

    with archive:
        shared = _xlsx_shared_strings(archive)
        sheets = _xlsx_sheet_order(archive)
        lines: List[str] = []
        total_rows = 0
        total_chars = 0
        for sheet_name, sheet_path in sheets:
            try:
                root = ElementTree.fromstring(archive.read(sheet_path))
            except (KeyError, ElementTree.ParseError):
                continue
            lines.append(f"# 工作表：{sheet_name}")
            for row in root.iter():
                if _local(row.tag) != "row":
                    continue
                total_rows += 1
                if total_rows > _MAX_XLSX_ROWS:
                    lines.append("…（行数超过上限，后续行未索引）")
                    break
                cells: Dict[int, str] = {}
                for cell in row:
                    if _local(cell.tag) != "c":
                        continue
                    index = _column_index(cell.attrib.get("r", ""))
                    if index >= _MAX_XLSX_COLS:
                        continue
                    kind = cell.attrib.get("t", "")
                    value = ""
                    if kind == "inlineStr":
                        value = "".join(
                            node.text or ""
                            for node in cell.iter()
                            if _local(node.tag) == "t"
                        )
                    else:
                        node = next(
                            (child for child in cell if _local(child.tag) == "v"), None
                        )
                        raw = node.text if node is not None else ""
                        if kind == "s" and raw:
                            try:
                                value = shared[int(raw)]
                            except (ValueError, IndexError):
                                value = ""
                        else:
                            value = raw or ""
                    if value.strip():
                        cells[index] = value.strip()
                if not cells:
                    continue
                width = max(cells) + 1
                merged = " | ".join(cells.get(i, "") for i in range(width)).strip()
                if not merged:
                    continue
                line = merged
                total_chars += len(line)
                if total_chars > _MAX_XLSX_CHARS:
                    lines.append("…（内容超过上限，后续内容未索引）")
                    break
                lines.append(line)
            if total_chars > _MAX_XLSX_CHARS:
                break

    text = "\n".join(lines).strip()
    if not text:
        raise TextExtractionError("表格中没有可索引的文本内容", code="empty_document")
    return text, len(sheets), total_rows


def _extract_csv(path: Path) -> str:
    raw = path.read_bytes()
    for encoding in ("utf-8-sig", "utf-8", "gb18030"):
        try:
            text = raw.decode(encoding)
            break
        except UnicodeDecodeError:
            continue
    else:
        raise TextExtractionError(
            "CSV 编码无法识别；请另存为 UTF-8 后重新上传", code="empty_document"
        )
    if not text.strip():
        raise TextExtractionError("文件未包含任何正文文本", code="empty_document")
    return text


# ---------------------------------------------------------------------------
# 图片尺寸（只读文件头，不依赖 Pillow）
# ---------------------------------------------------------------------------


def _image_size(data: bytes) -> Tuple[Optional[int], Optional[int]]:
    try:
        if data[:8] == b"\x89PNG\r\n\x1a\n" and len(data) >= 24:
            return int.from_bytes(data[16:20], "big"), int.from_bytes(data[20:24], "big")
        if data[:6] in (b"GIF87a", b"GIF89a") and len(data) >= 10:
            return (
                int.from_bytes(data[6:8], "little"),
                int.from_bytes(data[8:10], "little"),
            )
        if data[:2] == b"BM" and len(data) >= 26:
            return (
                abs(int.from_bytes(data[18:22], "little", signed=True)),
                abs(int.from_bytes(data[22:26], "little", signed=True)),
            )
        if data[:4] == b"RIFF" and data[8:12] == b"WEBP" and len(data) >= 30:
            chunk = data[12:16]
            if chunk == b"VP8X":
                width = 1 + int.from_bytes(data[24:27], "little")
                height = 1 + int.from_bytes(data[27:30], "little")
                return width, height
            if chunk == b"VP8 " and len(data) >= 30:
                return (
                    int.from_bytes(data[26:28], "little") & 0x3FFF,
                    int.from_bytes(data[28:30], "little") & 0x3FFF,
                )
        if data[:2] == b"\xff\xd8":
            index = 2
            while index + 9 < len(data):
                if data[index] != 0xFF:
                    index += 1
                    continue
                marker = data[index + 1]
                if marker in (0xD8, 0xD9):
                    index += 2
                    continue
                length = int.from_bytes(data[index + 2 : index + 4], "big")
                if marker in (0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA):
                    height = int.from_bytes(data[index + 5 : index + 7], "big")
                    width = int.from_bytes(data[index + 7 : index + 9], "big")
                    return width, height
                index += 2 + max(length, 2)
    except (IndexError, ValueError):
        return None, None
    return None, None
