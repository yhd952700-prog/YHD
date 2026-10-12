"""
PDF Processing Plugin for LiuHao AI OS

Provides PDF text extraction, metadata extraction, page range extraction,
PDF info, and text search within PDF files.

Capabilities:
- extract_text: Extract text from PDF files
- extract_metadata: Extract PDF metadata
- get_info: Get PDF information (pages, size, author)
- search_text: Search for text within PDF
- extract_pages: Extract text from specific pages
"""

import re
import os
from typing import Dict, Any, Optional, List, Tuple
from dataclasses import dataclass, field
from pathlib import Path

from ..plugin_base import (
    BasePlugin,
    PluginMetadata,
    PluginStatus,
    PluginExecutionError,
)


@dataclass
class PDFInfo:
    """Information about a PDF file."""
    file_path: str
    file_size: int
    page_count: int
    title: str = ""
    author: str = ""
    subject: str = ""
    keywords: str = ""
    creator: str = ""
    producer: str = ""
    creation_date: str = ""
    modification_date: str = ""
    pdf_version: str = ""
    is_encrypted: bool = False


@dataclass
class TextExtractionResult:
    """Result of text extraction from PDF."""
    file_path: str
    text: str
    page_count: int
    pages_extracted: int
    character_count: int
    word_count: int
    per_page: List[str] = field(default_factory=list)


@dataclass
class SearchResult:
    """Result of a text search in PDF."""
    file_path: str
    query: str
    total_matches: int
    matches: List[Dict[str, Any]]  # {page, line, text, position}


class PDFPlugin(BasePlugin):
    """PDF processing plugin for text extraction and analysis.

    Supports text extraction from PDF files, metadata extraction,
    page range extraction, and text search.

    Example:
        plugin = PDFPlugin()
        plugin.initialize()
        plugin.activate()

        result = plugin.extract_text("document.pdf")
        print(result.text)
    """

    def __init__(self) -> None:
        """Initialize the PDF plugin."""
        metadata = PluginMetadata(
            name="pdf",
            version="1.0.0",
            description="PDF processing and text extraction plugin",
            author="LiuHao AI OS Team",
            categories=["document", "pdf", "utility"],
            keywords=["pdf", "extract", "text", "document", "search"],
            requires_internet=False,
            dependencies=[],
        )
        super().__init__(metadata)

        self._has_pdf_reader: bool = False
        self._pdf_reader_module = None

    # ==================== Lifecycle Methods ====================

    def initialize(self, config: Optional[Dict[str, Any]] = None) -> bool:
        """Initialize the PDF plugin.

        Tries to import PyPDF2 or pdfminer as backend.
        Falls back to a basic text extraction if neither is available.
        """
        try:
            # Try to import PyPDF2
            try:
                import PyPDF2
                self._pdf_reader_module = PyPDF2
                self._has_pdf_reader = True
            except ImportError:
                try:
                    from pdfminer.high_level import extract_text
                    self._pdf_reader_module = extract_text
                    self._has_pdf_reader = True
                except ImportError:
                    self._has_pdf_reader = False

            self._set_status(PluginStatus.INITIALIZED)
            return True
        except Exception as e:
            self._set_error(f"Initialization failed: {e}")
            return False

    def activate(self) -> bool:
        """Activate the PDF plugin."""
        try:
            self._set_status(PluginStatus.ACTIVE)
            return True
        except Exception as e:
            self._set_error(f"Activation failed: {e}")
            return False

    def deactivate(self) -> bool:
        """Deactivate the PDF plugin."""
        try:
            self._set_status(PluginStatus.INACTIVE)
            return True
        except Exception as e:
            self._set_error(str(e))
            return False

    def shutdown(self) -> bool:
        """Shut down the PDF plugin."""
        try:
            self._pdf_reader_module = None
            self._set_status(PluginStatus.SHUTDOWN)
            return True
        except Exception as e:
            self._set_error(str(e))
            return False

    def get_capabilities(self) -> List[str]:
        """Return the list of capabilities."""
        return [
            "extract_text",
            "extract_metadata",
            "get_info",
            "search_text",
            "extract_pages",
            "is_available",
        ]

    # ==================== PDF Processing Methods ====================

    def is_available(self) -> bool:
        """Check if PDF processing is available (has backend library)."""
        return self._has_pdf_reader

    def extract_text(
        self,
        pdf_path: str,
        start_page: Optional[int] = None,
        end_page: Optional[int] = None,
    ) -> TextExtractionResult:
        """Extract text from a PDF file.

        Args:
            pdf_path: Path to the PDF file.
            start_page: First page to extract (1-indexed). Default: first page.
            end_page: Last page to extract (1-indexed). Default: last page.

        Returns:
            TextExtractionResult with extracted text and metadata.

        Raises:
            PluginExecutionError: If the plugin is not active.
            FileNotFoundError: If the PDF file does not exist.
        """
        self._ensure_active()
        self._increment_call_count()

        path = Path(pdf_path)
        if not path.exists():
            raise FileNotFoundError(f"PDF file not found: {pdf_path}")

        file_size = path.stat().st_size

        if self._has_pdf_reader and self._pdf_reader_module is not None:
            return self._extract_with_library(path, start_page, end_page)
        else:
            return self._extract_basic(path, start_page, end_page)

    def extract_metadata(self, pdf_path: str) -> Dict[str, Any]:
        """Extract metadata from a PDF file.

        Args:
            pdf_path: Path to the PDF file.

        Returns:
            Dictionary with metadata fields.
        """
        self._ensure_active()
        self._increment_call_count()

        info = self.get_info(pdf_path)
        return {
            "title": info.title,
            "author": info.author,
            "subject": info.subject,
            "keywords": info.keywords,
            "creator": info.creator,
            "producer": info.producer,
            "creation_date": info.creation_date,
            "modification_date": info.modification_date,
            "pdf_version": info.pdf_version,
        }

    def get_info(self, pdf_path: str) -> PDFInfo:
        """Get detailed information about a PDF file.

        Args:
            pdf_path: Path to the PDF file.

        Returns:
            PDFInfo with file information.
        """
        self._ensure_active()
        self._increment_call_count()

        path = Path(pdf_path)
        if not path.exists():
            raise FileNotFoundError(f"PDF file not found: {pdf_path}")

        file_size = path.stat().st_size

        if self._has_pdf_reader and self._pdf_reader_module is not None:
            return self._get_info_with_library(path)
        else:
            return self._get_info_basic(path)

    def search_text(
        self,
        pdf_path: str,
        query: str,
        case_sensitive: bool = False,
        max_matches: int = 100,
    ) -> SearchResult:
        """Search for text within a PDF file.

        Args:
            pdf_path: Path to the PDF file.
            query: Text to search for.
            case_sensitive: Whether the search is case-sensitive.
            max_matches: Maximum number of matches to return.

        Returns:
            SearchResult with matching locations and context.
        """
        self._ensure_active()
        self._increment_call_count()

        if not query:
            return SearchResult(
                file_path=pdf_path,
                query=query,
                total_matches=0,
                matches=[],
            )

        # Extract full text
        extraction = self.extract_text(pdf_path)
        matches: List[Dict[str, Any]] = []

        # Search in each page
        for page_num, page_text in enumerate(extraction.per_page, 1):
            if not case_sensitive:
                search_text = page_text.lower()
                search_query = query.lower()
            else:
                search_text = page_text
                search_query = query

            # Find all occurrences
            start = 0
            while True:
                pos = search_text.find(search_query, start)
                if pos == -1 or len(matches) >= max_matches:
                    break

                # Get surrounding context
                context_start = max(0, pos - 40)
                context_end = min(len(page_text), pos + len(query) + 40)
                context = page_text[context_start:context_end]
                if context_start > 0:
                    context = "..." + context
                if context_end < len(page_text):
                    context = context + "..."

                # Find line number
                line_num = page_text[:pos].count("\n") + 1

                matches.append({
                    "page": page_num,
                    "line": line_num,
                    "position": pos,
                    "context": context.strip(),
                })

                start = pos + 1

        return SearchResult(
            file_path=pdf_path,
            query=query,
            total_matches=len(matches),
            matches=matches,
        )

    def extract_pages(
        self,
        pdf_path: str,
        pages: List[int],
    ) -> TextExtractionResult:
        """Extract text from specific pages.

        Args:
            pdf_path: Path to the PDF file.
            pages: List of page numbers (1-indexed).

        Returns:
            TextExtractionResult with extracted text.
        """
        self._ensure_active()

        if not pages:
            return TextExtractionResult(
                file_path=pdf_path,
                text="",
                page_count=0,
                pages_extracted=0,
                character_count=0,
                word_count=0,
                per_page=[],
            )

        # Sort pages
        pages_sorted = sorted(pages)
        start_page = pages_sorted[0]
        end_page = pages_sorted[-1]

        # Extract range
        result = self.extract_text(pdf_path, start_page, end_page)

        # Filter to only requested pages
        requested_set = set(pages_sorted)
        filtered_per_page = []
        filtered_text_parts = []

        for i, page_text in enumerate(result.per_page):
            actual_page = start_page + i
            if actual_page in requested_set:
                filtered_per_page.append(page_text)
                filtered_text_parts.append(page_text)

        filtered_text = "\n\n".join(filtered_text_parts)

        return TextExtractionResult(
            file_path=pdf_path,
            text=filtered_text,
            page_count=result.page_count,
            pages_extracted=len(filtered_per_page),
            character_count=len(filtered_text),
            word_count=len(filtered_text.split()),
            per_page=filtered_per_page,
        )

    # ==================== Library-based Extraction ====================

    def _extract_with_library(
        self,
        path: Path,
        start_page: Optional[int],
        end_page: Optional[int],
    ) -> TextExtractionResult:
        """Extract text using PyPDF2 or pdfminer."""
        try:
            import PyPDF2

            with open(path, "rb") as f:
                reader = PyPDF2.PdfReader(f)
                total_pages = len(reader.pages)

                start = max(0, (start_page or 1) - 1)
                end = min(total_pages, end_page or total_pages)

                per_page: List[str] = []
                all_text_parts: List[str] = []

                for i in range(start, end):
                    page = reader.pages[i]
                    try:
                        page_text = page.extract_text() or ""
                    except Exception:
                        page_text = ""
                    per_page.append(page_text)
                    all_text_parts.append(page_text)

                full_text = "\n\n".join(all_text_parts)

                return TextExtractionResult(
                    file_path=str(path),
                    text=full_text,
                    page_count=total_pages,
                    pages_extracted=end - start,
                    character_count=len(full_text),
                    word_count=len(full_text.split()),
                    per_page=per_page,
                )
        except ImportError:
            pass

        try:
            from pdfminer.high_level import extract_text

            full_text = extract_text(str(path))
            total_pages = self._estimate_page_count(full_text)

            per_page = full_text.split("\f") if "\f" in full_text else [full_text]

            return TextExtractionResult(
                file_path=str(path),
                text=full_text,
                page_count=total_pages,
                pages_extracted=total_pages,
                character_count=len(full_text),
                word_count=len(full_text.split()),
                per_page=per_page,
            )
        except Exception:
            return self._extract_basic(path, start_page, end_page)

    def _get_info_with_library(self, path: Path) -> PDFInfo:
        """Get PDF info using PyPDF2."""
        try:
            import PyPDF2

            with open(path, "rb") as f:
                reader = PyPDF2.PdfReader(f)
                total_pages = len(reader.pages)

                metadata = reader.metadata or {}

                return PDFInfo(
                    file_path=str(path),
                    file_size=path.stat().st_size,
                    page_count=total_pages,
                    title=metadata.get("/Title", "") or "",
                    author=metadata.get("/Author", "") or "",
                    subject=metadata.get("/Subject", "") or "",
                    keywords=metadata.get("/Keywords", "") or "",
                    creator=metadata.get("/Creator", "") or "",
                    producer=metadata.get("/Producer", "") or "",
                    creation_date=str(metadata.get("/CreationDate", "") or ""),
                    modification_date=str(metadata.get("/ModDate", "") or ""),
                    pdf_version=getattr(reader, "pdf_version", "1.4"),
                    is_encrypted=reader.is_encrypted if hasattr(reader, "is_encrypted") else False,
                )
        except ImportError:
            return self._get_info_basic(path)

    # ==================== Basic (No Library) Extraction ====================

    def _extract_basic(
        self,
        path: Path,
        start_page: Optional[int],
        end_page: Optional[int],
    ) -> TextExtractionResult:
        """Basic text extraction by parsing raw PDF bytes.

        This is a fallback that attempts to extract text from PDF streams
        without external libraries. Quality may be limited.
        """
        try:
            with open(path, "rb") as f:
                content = f.read()

            # Try to find text between BT (Begin Text) and ET (End Text) markers
            text_parts: List[str] = []

            # Find text objects
            bt_pattern = re.compile(rb'BT\s*(.*?)\s*ET', re.DOTALL)
            for match in bt_pattern.finditer(content):
                text_obj = match.group(1)
                # Extract text from Tj and TJ operators
                tj_matches = re.findall(rb'\(([^)]*)\)\s*Tj', text_obj)
                for tj in tj_matches:
                    try:
                        decoded = tj.decode("latin-1", errors="replace")
                        # Clean up common PDF escape sequences
                        decoded = decoded.replace("\\(", "(").replace("\\)", ")")
                        decoded = decoded.replace("\\n", "\n").replace("\\r", "")
                        text_parts.append(decoded)
                    except Exception:
                        pass

                # TJ array
                tj_array = re.findall(rb'\[(.*?)\]\s*TJ', text_obj, re.DOTALL)
                for arr in tj_array:
                    str_matches = re.findall(rb'\(([^)]*)\)', arr)
                    for s in str_matches:
                        try:
                            decoded = s.decode("latin-1", errors="replace")
                            text_parts.append(decoded)
                        except Exception:
                            pass

            full_text = " ".join(text_parts)
            # Clean up excessive whitespace
            full_text = re.sub(r'\s+', ' ', full_text).strip()

            page_count = self._count_pages_from_raw(content)

            return TextExtractionResult(
                file_path=str(path),
                text=full_text,
                page_count=page_count,
                pages_extracted=page_count,
                character_count=len(full_text),
                word_count=len(full_text.split()),
                per_page=[full_text],  # Can't easily separate pages
            )
        except Exception as e:
            return TextExtractionResult(
                file_path=str(path),
                text=f"[Extraction failed: {e}]",
                page_count=0,
                pages_extracted=0,
                character_count=0,
                word_count=0,
                per_page=[],
            )

    def _get_info_basic(self, path: Path) -> PDFInfo:
        """Get basic PDF info from raw bytes."""
        file_size = path.stat().st_size
        page_count = 0
        pdf_version = "1.0"
        title = ""
        author = ""

        try:
            with open(path, "rb") as f:
                header = f.read(100)

                # Parse version from header
                version_match = re.search(rb'%PDF-(\d+\.\d+)', header)
                if version_match:
                    pdf_version = version_match.group(1).decode("ascii")

                # Count page objects
                f.seek(0)
                content = f.read()
                page_count = self._count_pages_from_raw(content)

                # Try to extract metadata
                info_match = re.search(rb'/Title\s*\(([^)]*)\)', content)
                if info_match:
                    try:
                        title = info_match.group(1).decode("latin-1", errors="replace")
                    except Exception:
                        pass

                author_match = re.search(rb'/Author\s*\(([^)]*)\)', content)
                if author_match:
                    try:
                        author = author_match.group(1).decode("latin-1", errors="replace")
                    except Exception:
                        pass
        except Exception:
            pass

        return PDFInfo(
            file_path=str(path),
            file_size=file_size,
            page_count=page_count,
            title=title,
            author=author,
            pdf_version=pdf_version,
        )

    def _count_pages_from_raw(self, content: bytes) -> int:
        """Estimate page count from raw PDF bytes."""
        # Count /Type /Page entries (not /Pages)
        page_count = len(re.findall(rb'/Type\s*/Page\b', content))
        # Subtract /Pages entries
        pages_count = len(re.findall(rb'/Type\s*/Pages\b', content))
        return max(1, page_count - pages_count)

    def _estimate_page_count(self, text: str) -> int:
        """Estimate page count from form feed characters."""
        form_feeds = text.count("\f")
        return max(1, form_feeds + 1 if form_feeds > 0 else 1)
