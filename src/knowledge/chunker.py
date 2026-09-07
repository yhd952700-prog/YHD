"""Text chunker for the embedding pipeline.

Provides configurable chunking with chunk_size and overlap parameters,
supporting recursive character-based splitting for general text documents.
"""

from __future__ import annotations

from typing import List


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
