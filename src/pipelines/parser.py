"""
Document Parser for LiuHao AI OS

Provides multi-format document parsing with:
- Support for PDF, DOCX, HTML, TXT, and other formats
- Intelligent chunking strategies
- Language detection
- Metadata extraction
"""

import logging
from abc import ABC, abstractmethod
from typing import Dict, Any, List, Optional
from pathlib import Path
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


@dataclass
class ParseResult:
    """Result of document parsing."""
    text: str
    metadata: Dict[str, Any]
    page_count: int = 0
    language: str = "en"
    chunks: List[str] = field(default_factory=list)
    entities: List[str] = field(default_factory=list)


@dataclass
class ChunkingConfig:
    """Configuration for document chunking."""
    chunk_size: int = 1000
    chunk_overlap: int = 200
    min_chunk_size: int = 100
    strategy: str = "fixed"  # fixed, recursive, semantic


class DocumentParser(ABC):
    """Abstract base class for document parsers."""

    @abstractmethod
    def can_handle(self, file_path: str) -> bool:
        """Check if this parser can handle the given file."""
        pass

    @abstractmethod
    def parse(self, file_path: str, config: ChunkingConfig = None) -> ParseResult:
        """Parse a document and return result."""
        pass

    def chunk_text(self, text: str, config: ChunkingConfig = None) -> List[str]:
        """Chunk text using the specified strategy."""
        config = config or ChunkingConfig()

        if config.strategy == "fixed":
            return self._chunk_fixed(text, config)
        elif config.strategy == "recursive":
            return self._chunk_recursive(text, config)
        else:
            return self._chunk_fixed(text, config)

    def _chunk_fixed(self, text: str, config: ChunkingConfig) -> List[str]:
        """Fixed-size chunking with overlap."""
        chunks = []
        start = 0

        while start < len(text):
            end = start + config.chunk_size
            if end > len(text):
                end = len(text)

            chunk = text[start:end]
            chunks.append(chunk)

            # Move start position with overlap
            start = end - config.chunk_overlap if end < len(text) else len(text)

            # Stop if chunk is too small
            if len(chunk) < config.min_chunk_size and start >= len(text):
                break

        return chunks

    def _chunk_recursive(self, text: str, config: ChunkingConfig) -> List[str]:
        """Recursive chunking based on sentence boundaries."""
        # Simple implementation - split by sentences then group
        sentences = text.split(". ")
        chunks = []
        current_chunk = []
        current_size = 0

        for sentence in sentences:
            sentence_size = len(sentence.split())
            if current_size + sentence_size > config.chunk_size and current_chunk:
                # Save current chunk
                chunks.append(". ".join(current_chunk))
                # Start new chunk with overlap
                overlap_size = max(1, config.chunk_overlap // 100 * len(current_chunk) // config.chunk_size)
                current_chunk = current_chunk[-overlap_size:] if overlap_size > 0 else []
                current_size = sum(len(s.split()) for s in current_chunk)

            current_chunk.append(sentence)
            current_size += sentence_size

        # Don't forget the last chunk
        if current_chunk:
            chunks.append(". ".join(current_chunk))

        return chunks


class MultiFormatDocumentParser:
    """Registry-based document parser that supports multiple formats."""

    def __init__(self):
        self.parsers: List[DocumentParser] = []

    def register_parser(self, parser: DocumentParser) -> None:
        """Register a document parser."""
        self.parsers.append(parser)
        logger.info(f"Registered document parser: {parser.__class__.__name__}")

    def parse(self, file_path: str, config: ChunkingConfig = None) -> Optional[ParseResult]:
        """Parse a document using the appropriate parser."""
        file_path = str(file_path)
        file_ext = Path(file_path).suffix.lower()

        # Find appropriate parser
        for parser in self.parsers:
            if parser.can_handle(file_path):
                try:
                    result = parser.parse(file_path, config)
                    logger.info(f"Parsed document: {file_path} ({result.page_count} pages)")
                    return result
                except Exception as e:
                    logger.error(f"Failed to parse {file_path}: {e}")
                    continue

        logger.warning(f"No parser found for file: {file_path} (ext: {file_ext})")
        return None


# Convenience function
def create_parser_registry() -> MultiFormatDocumentParser:
    """Create a document parser registry with built-in parsers."""
    registry = MultiFormatDocumentParser()
    # Built-in parsers would be registered here
    return registry
