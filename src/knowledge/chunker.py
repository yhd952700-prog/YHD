"""Text chunker for the embedding pipeline.

``chunk_text`` / ``chunk_by_sentences`` are the legacy fixed-window splitters kept
for compatibility. ``chunk_document_structured`` is the retrieval-oriented
splitter used by the knowledge ingest path:

* headings (Markdown ``#``, ``第X章/节``, ``一、``) form a heading stack, and
  every chunk records its heading path;
* paragraphs stay intact and are grouped up to ``chunk_size``;
* tables (``|`` rows) and fenced code blocks are never split;
* only overlong paragraphs degrade to a hard cut with overlap.
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
    return chunks


def chunk_paged_document(
    pages: List[Tuple[int, str]],
    chunk_size: int = 512,
    chunk_overlap: int = 50,
) -> List[TextChunk]:
    """Chunk a paged document one page at a time, tagging every chunk's page.

    Chunking per page (rather than joining pages and splitting the blob) keeps
    the page number exact: a chunk never straddles two pages and never needs a
    guessed "which page did this come from" heuristic. The trade-off is that a
    paragraph continued across a page break becomes two chunks -- an acceptable
    loss for the provenance gain, and the page number makes it obvious.
    """
    chunks: List[TextChunk] = []
    for page_number, page_text in pages:
        page_chunks = chunk_document_structured(
            page_text, chunk_size=chunk_size, chunk_overlap=chunk_overlap
        )
        for chunk in page_chunks:
            chunks.append(
                TextChunk(
                    chunk.text,
                    chunk.heading_path,
                    len(chunks),
                    page=page_number,
                )
            )
    return chunks
