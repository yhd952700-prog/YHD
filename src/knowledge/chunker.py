"""Text chunker for the embedding pipeline.

``chunk_text`` / ``chunk_by_sentences`` are the legacy fixed-window splitters kept
for compatibility. ``chunk_document_structured`` is the retrieval-oriented
splitter used by the knowledge ingest path:

* headings (Markdown ``#``, ``第X章/节``, ``一、``) form a heading stack, and
  every chunk records its heading path;
* paragraphs stay intact and are grouped up to ``chunk_size``;
* tables (``|`` rows) and fenced code blocks are never split;
* only overlong paragraphs degrade to a hard cut with overlap.

``chunk_paged_document`` adds the paged rule for PDF: a page whose last chunk
does not end with sentence-final punctuation absorbs the next page's opening
chunk (``page`` = start page, ``pages`` = the span), so a sentence is never
retrievable as only its first half.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import List, Optional, Tuple


def chunk_text(
    text: str,
    chunk_size: int = 512,
    chunk_overlap: int = 50,
    separator: str = "\n",
) -> List[str]:
    """Split text into overlapping chunks.

    Args:
        text: The input text to chunk.
        chunk_size: Maximum characters per chunk.
        chunk_overlap: Number of overlapping characters between adjacent chunks.
        separator: Sentence/line separator for recursive splitting.

    Returns:
        A list of text chunks.
    """
    if not text:
        return []

    if chunk_overlap >= chunk_size:
        chunk_overlap = chunk_size // 2

    chunks: List[str] = []
    start = 0

    while start < len(text):
        end = start + chunk_size
        chunk = text[start:end]
        chunks.append(chunk)

        # Move start position, accounting for overlap
        start += chunk_size - chunk_overlap

    return chunks


def chunk_by_sentences(
    text: str,
    chunk_size: int = 512,
    chunk_overlap: int = 50,
) -> List[str]:
    """Chunk text by sentences, preserving sentence boundaries.

    Args:
        text: The input text to chunk.
        chunk_size: Maximum characters per chunk.
        chunk_overlap: Number of overlapping characters between adjacent chunks.

    Returns:
        A list of text chunks split at sentence boundaries.
    """
    import re

    # Handle empty text
    if not text:
        return []

    # Strip and check if nothing remains
    stripped = text.strip()
    if not stripped:
        return []

    # Simple sentence tokenizer
    sentences = re.split(r"(?<=[.!?])\s+", stripped)

    # Handle case where split resulted in empty list
    if not sentences:
        return []

    # Filter out any empty sentences
    sentences = [s for s in sentences if s.strip()]

    # Handle case where all sentences were empty
    if not sentences:
        return []

    chunks: List[str] = []
    current_chunk: List[str] = []
    current_length = 0

    for sentence in sentences:
        sentence_len = len(sentence) + 1  # +1 for the space

        if current_length + sentence_len > chunk_size and current_chunk:
            # Save current chunk with overlap
            overlap_start = max(0, len(" ".join(current_chunk)) - chunk_overlap)
            overlap_text = " ".join(current_chunk)[overlap_start:]
            chunks.append(overlap_text)

            # Start new chunk with current sentence
            current_chunk = [sentence]
            current_length = len(sentence) + 1
        else:
            current_chunk.append(sentence)
            current_length += sentence_len

    # Don't forget the last chunk
    if current_chunk:
        chunks.append(" ".join(current_chunk))

    return chunks


# ---------------------------------------------------------------------------
# Structure-aware chunking
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class TextChunk:
    """A chunk plus the heading path it belongs to.

    ``text`` is the body only; ``context_header`` is the breadcrumb that makes a
    fragment such as "如下所示：" meaningful once it leaves its section.
    ``page`` is the 1-based source page for paged formats (PDF); ``None`` for
    formats without pages. It is stored in chunk metadata so a search hit can
    say "来自第 N 页".
    """

    text: str
    heading_path: Tuple[str, ...] = ()
    index: int = 0
    page: Optional[int] = None
    #: Page range covered by this chunk, e.g. ``(2, 3)`` for a chunk that
    #: starts on page 2 and continues onto page 3. Single-page chunks carry
    #: ``(page,)``; non-paged formats carry ``()``.
    pages: Tuple[int, ...] = ()

    @property
    def context_header(self) -> str:
        return " / ".join(self.heading_path)

    @property
    def embed_text(self) -> str:
        """Text handed to the embedder and shown to the model on a hit."""
        header = self.context_header
        return f"[{header}]\n{self.text}" if header else self.text


_MD_HEADING_RE = re.compile(r"^(#{1,6})\s+(.+?)\s*$")
_CHAPTER_RE = re.compile(
    r"^(第[0-9０-９一二三四五六七八九十百千零两]+)([章节篇部])"
    r"(?:\s*[:：、]?\s*[^\n]{0,60})?$"
)
_CN_ITEM_RE = re.compile(r"^[一二三四五六七八九十]+、\s*[^\n]{0,60}$")

_HEADING_MAX_LEN = 80


def _detect_heading(line: str) -> "Tuple[int, str] | None":
    """Return ``(level, title)`` when ``line`` is a heading, else ``None``."""
    if not line or len(line) > _HEADING_MAX_LEN:
        return None

    match = _MD_HEADING_RE.match(line)
    if match:
        return len(match.group(1)), match.group(2).strip()

    chapter = _CHAPTER_RE.match(line)
    if chapter:
        # 章/篇/部 -> level 1; 节 -> level 2
        level = 1 if chapter.group(2) in "章篇部" else 2
        return level, line.strip()

    if _CN_ITEM_RE.match(line):
        return 3, line.strip()

    return None


def _is_table_line(line: str) -> bool:
    return line.strip().startswith("|")


def _parse_blocks(text: str) -> List[Tuple[str, object]]:
    """Split raw text into heading / paragraph / table / code blocks."""
    blocks: List[Tuple[str, object]] = []
    paragraph: List[str] = []
    lines = text.splitlines()

    def flush_paragraph() -> None:
        if paragraph:
            body = "\n".join(paragraph).strip()
            if body:
                blocks.append(("text", body))
            paragraph.clear()

    index = 0
    while index < len(lines):
        raw = lines[index]
        stripped = raw.strip()

        if stripped.startswith("```") or stripped.startswith("~~~"):
            flush_paragraph()
            fence = stripped[:3]
            code_lines = [raw]
            index += 1
            while index < len(lines):
                code_lines.append(lines[index])
                if lines[index].strip().startswith(fence):
                    index += 1
                    break
                index += 1
            blocks.append(("code", "\n".join(code_lines).strip("\n")))
            continue

        if _is_table_line(raw):
            flush_paragraph()
            table_lines = []
            while index < len(lines) and _is_table_line(lines[index]):
                table_lines.append(lines[index])
                index += 1
            blocks.append(("table", "\n".join(table_lines).strip()))
            continue

        heading = _detect_heading(stripped)
        if heading is not None:
            flush_paragraph()
            blocks.append(("heading", heading))
            index += 1
            continue

        if not stripped:
            flush_paragraph()
            index += 1
            continue

        paragraph.append(raw)
        index += 1

    flush_paragraph()
    return blocks


def chunk_document_structured(
    text: str,
    chunk_size: int = 512,
    chunk_overlap: int = 50,
) -> List[TextChunk]:
    """Chunk ``text`` by heading hierarchy, paragraphs, tables and code blocks.

    Backward compatibility: legacy callers keep using ``chunk_text``; this is a
    new, additive API. Chunks store their heading path so retrieval can prepend
    a breadcrumb even when the body starts with a context-dependent sentence.
    """
    if not text or not text.strip():
        return []
    if chunk_size <= 0:
        raise ValueError("chunk_size must be positive")
    if chunk_overlap >= chunk_size:
        chunk_overlap = chunk_size // 2
    if chunk_overlap < 0:
        chunk_overlap = 0

    chunks: List[TextChunk] = []
    heading_stack: List[Tuple[int, str]] = []
    current_path: Tuple[str, ...] = ()
    buffer: List[str] = []
    buffer_len = 0

    def flush_buffer() -> None:
        nonlocal buffer, buffer_len
        body = "\n\n".join(buffer).strip()
        if body:
            chunks.append(TextChunk(body, current_path, len(chunks)))
        buffer = []
        buffer_len = 0

    for kind, payload in _parse_blocks(text):
        if kind == "heading":
            flush_buffer()
            level, title = payload  # type: ignore[misc]
            while heading_stack and heading_stack[-1][0] >= level:
                heading_stack.pop()
            heading_stack.append((int(level), str(title)))
            current_path = tuple(item[1] for item in heading_stack)
            continue

        body = str(payload).strip()
        if not body:
            continue

        if kind in ("code", "table"):
            # Integrity beats size: a split table or code block is useless.
            flush_buffer()
            chunks.append(TextChunk(body, current_path, len(chunks)))
            continue

        if len(body) > chunk_size:
            flush_buffer()
            for piece in chunk_text(
                body, chunk_size=chunk_size, chunk_overlap=chunk_overlap
            ):
                if piece.strip():
                    chunks.append(
                        TextChunk(piece.strip(), current_path, len(chunks))
                    )
            continue

        projected = buffer_len + (2 if buffer else 0) + len(body)
        if buffer and projected > chunk_size:
            flush_buffer()
            projected = len(body)
        buffer.append(body)
        buffer_len = projected

    flush_buffer()
    return _merge_short_parent_bodies(chunks, chunk_size)


_SENTENCE_END = frozenset("。！？.!?…")
_CLOSING_MARKS = "\"'”’）)】]」』》"

#: 短于该长度的“父级正文”（标题下的一句导语）不单独成块：它与标题高度重合，
#: 检索时会把真正的答案片段挤到第 2/3 位，引用就指向了没有答案的摘要块。
#: 仅当它后面紧跟更深的子小节时才并入子块，同级/表格/代码不受影响。
_MIN_STANDALONE_BODY = 40


def _is_non_prose(text: str) -> bool:
    """表格与代码块即使很短也不能并块（完整性优先）。"""
    stripped = text.lstrip()
    return stripped.startswith("|") or stripped.startswith("```") or stripped.startswith("~~~")


def _merge_short_parent_bodies(
    chunks: List[TextChunk], chunk_size: int
) -> List[TextChunk]:
    """把标题下过短的导语并入紧随其后的子小节，并重排 chunk index。"""
    merged: List[TextChunk] = []
    for chunk in chunks:
        if (
            merged
            and len(merged[-1].text) < _MIN_STANDALONE_BODY
            and not _is_non_prose(merged[-1].text)
            and not _is_non_prose(chunk.text)
            and len(chunk.heading_path) > len(merged[-1].heading_path)
            and chunk.heading_path[: len(merged[-1].heading_path)]
            == merged[-1].heading_path
            and len(merged[-1].text) + 2 + len(chunk.text) <= chunk_size
        ):
            previous = merged.pop()
            merged.append(
                TextChunk(
                    f"{previous.text}\n\n{chunk.text}",
                    chunk.heading_path,
                    0,
                )
            )
            continue
        merged.append(chunk)
    return [
        TextChunk(chunk.text, chunk.heading_path, index, page=chunk.page, pages=chunk.pages)
        for index, chunk in enumerate(merged)
    ]


def _ends_with_sentence(text: str) -> bool:
    """True when the text ends with sentence-final punctuation (ignoring closers).

    A page that ends mid-sentence must not become its own chunk: the next page
    opening is the second half of the same sentence, and a search hit that
    stops halfway is worse than a slightly larger chunk.
    """
    stripped = text.rstrip()
    while stripped and stripped[-1] in _CLOSING_MARKS:
        stripped = stripped[:-1].rstrip()
    return bool(stripped) and stripped[-1] in _SENTENCE_END


def _is_mergeable(text: str) -> bool:
    """Tables and code blocks keep their integrity; never merge across pages."""
    stripped = text.strip()
    if not stripped:
        return False
    if stripped.startswith("```") or stripped.endswith("```"):
        return False
    lines = [line for line in stripped.splitlines() if line.strip()]
    return not all(_is_table_line(line) for line in lines)


def chunk_paged_document(
    pages: List[Tuple[int, str]],
    chunk_size: int = 512,
    chunk_overlap: int = 50,
) -> List[TextChunk]:
    """Chunk a paged document and stitch sentences that cross a page boundary.

    Rule (documented so the next maintainer does not have to reverse-engineer it):

    * Each page is chunked independently first, keeping an exact page number.
    * If a page's **last** chunk does not end with sentence-final punctuation
      (``。！？.!?…``), the **first** chunk of the next page is appended to it.
      The merged chunk's ``page`` is the start page and ``pages`` records the
      range (``(2, 3)``), so retrieval can show "第 2-3 页".
    * Tables and code blocks are never merged; they must stay whole.
    * Merging stops at ``2 * chunk_size`` characters, so a pathological PDF
      without punctuation cannot produce one giant embedding input.
    * The merged text is the actual continuation (no duplicated overlap); the
      next page's opening is included in full within the size cap, which is
      what "带 overlap" means here: the reader sees the boundary sentence end.
    """
    flat: List[Tuple[int, TextChunk]] = []
    for page_number, page_text in pages:
        for chunk in chunk_document_structured(
            page_text, chunk_size=chunk_size, chunk_overlap=chunk_overlap
        ):
            flat.append((page_number, chunk))

    chunks: List[TextChunk] = []
    max_merged_chars = max(chunk_size * 2, chunk_size)
    cursor = 0
    while cursor < len(flat):
        start_page, start_chunk = flat[cursor]
        text = start_chunk.text
        page_numbers: List[int] = [start_page]
        last_page = start_page
        mergeable_tail = _is_mergeable(start_chunk.text)

        while (
            mergeable_tail
            and not _ends_with_sentence(text)
            and cursor + 1 < len(flat)
            and flat[cursor + 1][0] != last_page
        ):
            next_page, next_chunk = flat[cursor + 1]
            if not _is_mergeable(next_chunk.text):
                break
            if len(text) + 2 + len(next_chunk.text) > max_merged_chars:
                break
            text = text.rstrip() + "\n\n" + next_chunk.text.lstrip()
            for skipped in range(last_page + 1, next_page + 1):
                if skipped not in page_numbers:
                    page_numbers.append(skipped)
            last_page = next_page
            cursor += 1
            # Only keep stitching when the absorbed chunk was the page's last
            # chunk; otherwise we already took the page opening we needed.
            if cursor + 1 < len(flat) and flat[cursor + 1][0] == last_page:
                break

        chunks.append(
            TextChunk(
                text.strip(),
                start_chunk.heading_path,
                len(chunks),
                page=start_page,
                pages=tuple(page_numbers),
            )
        )
        cursor += 1

    return chunks
