"""
Summarizer Plugin for LiuHao AI OS

Provides text summarization with multiple styles, configurable length,
key points extraction, and TL;DR generation.

Capabilities:
- summarize: Summarize text in various styles
- extract_key_points: Extract key points from text
- generate_tldr: Generate a TL;DR summary
- summarize_bulk: Summarize multiple documents
"""

import re
import math
from typing import Dict, Any, Optional, List, Tuple
from collections import Counter
from dataclasses import dataclass, field

from ..plugin_base import (
    BasePlugin,
    PluginMetadata,
    PluginStatus,
    PluginExecutionError,
)


@dataclass
class SummaryResult:
    """Result of a summarization operation."""
    original_text: str
    summary: str
    style: str
    length_ratio: float
    original_length: int
    summary_length: int
    key_points: List[str] = field(default_factory=list)
    sentences_used: List[int] = field(default_factory=list)


class SummarizerPlugin(BasePlugin):
    """Text summarization plugin with multiple styles and extraction methods.

    Supports bullet points, paragraph, and executive summary styles.
    Uses extractive summarization based on sentence scoring with
    word frequency and position heuristics.

    Example:
        plugin = SummarizerPlugin()
        plugin.initialize()
        plugin.activate()

        result = plugin.summarize(long_text, style="bullet", max_sentences=5)
        print(result.summary)
    """

    # Common English stop words
    STOP_WORDS = {
        "the", "a", "an", "and", "or", "but", "in", "on", "at", "to", "for",
        "of", "with", "by", "from", "as", "is", "was", "are", "were", "be",
        "been", "being", "have", "has", "had", "do", "does", "did", "will",
        "would", "could", "should", "may", "might", "can", "shall", "this",
        "that", "these", "those", "it", "its", "they", "them", "their", "we",
        "our", "you", "your", "he", "his", "she", "her", "i", "me", "my",
        "not", "no", "so", "if", "then", "than", "also", "very", "just",
        "about", "up", "out", "all", "when", "what", "which", "who", "how",
        "where", "why", "there", "here", "such", "more", "most", "other",
        "some", "any", "each", "every", "both", "few", "many", "much",
        "into", "over", "after", "before", "between", "through", "during",
    }

    def __init__(self) -> None:
        """Initialize the summarizer plugin."""
        metadata = PluginMetadata(
            name="summarizer",
            version="1.0.0",
            description="Text summarization plugin with multiple styles",
            author="LiuHao AI OS Team",
            categories=["nlp", "text", "utility"],
            keywords=["summarize", "summary", "text", "nlp", "tldr"],
            requires_internet=False,
            dependencies=[],
        )
        super().__init__(metadata)

        self._summary_count: int = 0
        self._total_chars_processed: int = 0

    # ==================== Lifecycle Methods ====================

    def initialize(self, config: Optional[Dict[str, Any]] = None) -> bool:
        """Initialize the summarizer plugin.

        Args:
            config: Optional configuration.

        Returns:
            True if initialization succeeded.
        """
        try:
            self._set_status(PluginStatus.INITIALIZED)
            return True
        except Exception as e:
            self._set_error(f"Initialization failed: {e}")
            return False

    def activate(self) -> bool:
        """Activate the summarizer plugin.

        Returns:
            True if activation succeeded.
        """
        try:
            self._set_status(PluginStatus.ACTIVE)
            return True
        except Exception as e:
            self._set_error(f"Activation failed: {e}")
            return False

    def deactivate(self) -> bool:
        """Deactivate the summarizer plugin."""
        try:
            self._set_status(PluginStatus.INACTIVE)
            return True
        except Exception as e:
            self._set_error(str(e))
            return False

    def shutdown(self) -> bool:
        """Shut down the summarizer plugin."""
        try:
            self._set_status(PluginStatus.SHUTDOWN)
            return True
        except Exception as e:
            self._set_error(str(e))
            return False

    def get_capabilities(self) -> List[str]:
        """Return the list of capabilities this plugin provides."""
        return [
            "summarize",
            "extract_key_points",
            "generate_tldr",
            "summarize_bulk",
            "get_stats",
        ]

    # ==================== Summarization Methods ====================

    def summarize(
        self,
        text: str,
        style: str = "paragraph",
        max_sentences: Optional[int] = None,
        ratio: float = 0.3,
        min_length: int = 50,
    ) -> SummaryResult:
        """Summarize text in the specified style.

        Args:
            text: The text to summarize.
            style: Summary style - 'bullet', 'paragraph', or 'executive'.
            max_sentences: Maximum number of sentences in the summary.
                If None, computed from ratio.
            ratio: Target length as a fraction of original (0.0 to 1.0).
            min_length: Minimum summary length in characters.

        Returns:
            SummaryResult with the summary and metadata.

        Raises:
            PluginExecutionError: If the plugin is not active.
            ValueError: If the style is unknown.
        """
        self._ensure_active()
        self._increment_call_count()
        self._summary_count += 1
        self._total_chars_processed += len(text)

        if not text or not text.strip():
            return SummaryResult(
                original_text=text,
                summary="",
                style=style,
                length_ratio=0.0,
                original_length=0,
                summary_length=0,
            )

        if style not in ("bullet", "paragraph", "executive"):
            raise ValueError(f"Unknown summary style: {style}")

        # Split into sentences
        sentences = self._split_sentences(text)
        if not sentences:
            return SummaryResult(
                original_text=text,
                summary=text,
                style=style,
                length_ratio=1.0,
                original_length=len(text),
                summary_length=len(text),
            )

        # Determine target sentence count
        if max_sentences is None:
            max_sentences = max(1, int(len(sentences) * ratio))
        else:
            max_sentences = max(1, min(max_sentences, len(sentences)))

        # Score and rank sentences
        scored_sentences = self._score_sentences(sentences)
        top_indices = sorted(
            [idx for idx, _ in scored_sentences[:max_sentences]]
        )

        # Extract key points
        key_points = self._extract_key_points_from_sentences(
            sentences, top_indices
        )

        # Format summary based on style
        if style == "bullet":
            summary = self._format_bullet_summary(sentences, top_indices)
        elif style == "executive":
            summary = self._format_executive_summary(
                sentences, top_indices, key_points
            )
        else:  # paragraph
            summary = self._format_paragraph_summary(sentences, top_indices)

        # Ensure minimum length
        if len(summary) < min_length and len(sentences) > max_sentences:
            # Add more sentences
            additional = min(len(sentences) - max_sentences, 3)
            for i in range(max_sentences, max_sentences + additional):
                if i < len(scored_sentences):
                    idx = scored_sentences[i][0]
                    if idx not in top_indices:
                        top_indices.append(idx)
            top_indices.sort()
            if style == "paragraph":
                summary = self._format_paragraph_summary(sentences, top_indices)

        return SummaryResult(
            original_text=text,
            summary=summary,
            style=style,
            length_ratio=round(len(summary) / max(len(text), 1), 3),
            original_length=len(text),
            summary_length=len(summary),
            key_points=key_points,
            sentences_used=top_indices,
        )

    def extract_key_points(self, text: str, max_points: int = 5) -> List[str]:
        """Extract key points from text.

        Args:
            text: The text to analyze.
            max_points: Maximum number of key points to extract.

        Returns:
            List of key point strings.
        """
        self._ensure_active()
        self._increment_call_count()

        sentences = self._split_sentences(text)
        if not sentences:
            return []

        scored = self._score_sentences(sentences)
        top_indices = sorted([idx for idx, _ in scored[:max_points]])

        return self._extract_key_points_from_sentences(sentences, top_indices)

    def generate_tldr(self, text: str, max_chars: int = 280) -> str:
        """Generate a TL;DR (Too Long; Didn't Read) summary.

        Args:
            text: The text to summarize.
            max_chars: Maximum character length of the TL;DR.

        Returns:
            TL;DR summary string.
        """
        self._ensure_active()
        self._increment_call_count()

        if not text or not text.strip():
            return ""

        sentences = self._split_sentences(text)
        if not sentences:
            return text[:max_chars]

        # Start with the most important sentence
        scored = self._score_sentences(sentences)
        tldr_parts: List[str] = []
        current_length = 0

        for idx, score in scored:
            sentence = sentences[idx].strip()
            if current_length + len(sentence) + 2 <= max_chars:
                tldr_parts.append(sentence)
                current_length += len(sentence) + 1
            else:
                break

        if not tldr_parts:
            # Take first sentence, truncated
            first = sentences[0].strip()
            if len(first) > max_chars - 3:
                return first[:max_chars - 3] + "..."
            return first

        return " ".join(tldr_parts)

    def summarize_bulk(
        self,
        documents: List[str],
        style: str = "paragraph",
        max_sentences: Optional[int] = None,
    ) -> List[SummaryResult]:
        """Summarize multiple documents.

        Args:
            documents: List of text documents to summarize.
            style: Summary style.
            max_sentences: Maximum sentences per summary.

        Returns:
            List of SummaryResult objects.
        """
        self._ensure_active()
        results: List[SummaryResult] = []
        for doc in documents:
            result = self.summarize(doc, style=style, max_sentences=max_sentences)
            results.append(result)
        return results

    def get_stats(self) -> Dict[str, Any]:
        """Get summarizer usage statistics."""
        return {
            "total_summaries": self._summary_count,
            "total_chars_processed": self._total_chars_processed,
            "call_count": self._call_count,
        }

    # ==================== Internal Helpers ====================

    def _split_sentences(self, text: str) -> List[str]:
        """Split text into sentences.

        Handles common sentence-ending punctuation with some edge cases.
        """
        # Normalize whitespace
        text = re.sub(r'\s+', ' ', text.strip())

        # Split on sentence-ending punctuation followed by space and capital letter
        # This is a simplified approach
        sentences = re.split(r'(?<=[.!?])\s+(?=[A-Z])', text)

        # Handle cases where the above regex didn't split
        if len(sentences) == 1 and len(text) > 200:
            # Fall back to splitting on all sentence endings
            sentences = re.split(r'(?<=[.!?])\s+', text)

        # Filter out very short fragments
        sentences = [s.strip() for s in sentences if len(s.strip()) > 10]

        return sentences

    def _score_sentences(self, sentences: List[str]) -> List[Tuple[int, float]]:
        """Score sentences by importance using word frequency and position.

        Returns list of (index, score) tuples sorted by score descending.
        """
        # Build word frequency table (excluding stop words)
        word_freq: Counter = Counter()
        for sent in sentences:
            words = self._tokenize(sent)
            for w in words:
                if w not in self.STOP_WORDS and len(w) > 2:
                    word_freq[w] += 1

        if not word_freq:
            return [(i, 1.0) for i in range(len(sentences))]

        max_freq = max(word_freq.values())
        normalized_freq = {w: f / max_freq for w, f in word_freq.items()}

        # Score each sentence
        scored: List[Tuple[int, float]] = []
        for i, sent in enumerate(sentences):
            words = self._tokenize(sent)
            if not words:
                scored.append((i, 0.0))
                continue

            # Content word score
            content_words = [w for w in words if w in normalized_freq]
            if content_words:
                content_score = sum(normalized_freq[w] for w in content_words) / len(words)
            else:
                content_score = 0.0

            # Position bonus (first and last sentences are more important)
            n = len(sentences)
            if n > 1:
                position_score = 1.0 - (i / (n - 1) - 0.5) ** 2  # Bell curve
                position_score = max(0.3, position_score)
            else:
                position_score = 1.0

            # Length bonus (prefer medium-length sentences)
            sent_len = len(words)
            if sent_len <= 5:
                length_score = 0.5
            elif sent_len <= 20:
                length_score = 1.0
            elif sent_len <= 40:
                length_score = 0.8
            else:
                length_score = 0.6

            # Combined score
            total_score = (content_score * 0.5 + position_score * 0.3 + length_score * 0.2)

            scored.append((i, total_score))

        # Sort by score descending
        scored.sort(key=lambda x: x[1], reverse=True)
        return scored

    def _tokenize(self, text: str) -> List[str]:
        """Simple word tokenization."""
        words = re.findall(r'\b[a-zA-Z0-9]+\b', text.lower())
        return words

    def _format_bullet_summary(
        self, sentences: List[str], indices: List[int]
    ) -> str:
        """Format summary as bullet points."""
        bullets = []
        for idx in indices:
            sent = sentences[idx].strip()
            # Remove trailing period for bullets
            if sent.endswith('.'):
                sent = sent[:-1]
            bullets.append(f"* {sent}")
        return "\n".join(bullets)

    def _format_paragraph_summary(
        self, sentences: List[str], indices: List[int]
    ) -> str:
        """Format summary as a single paragraph."""
        return " ".join(sentences[idx].strip() for idx in indices)

    def _format_executive_summary(
        self,
        sentences: List[str],
        indices: List[int],
        key_points: List[str],
    ) -> str:
        """Format an executive summary with overview + key points."""
        lines = []
        lines.append("Executive Summary")
        lines.append("=" * 30)
        lines.append("")

        # Overview paragraph
        overview = " ".join(sentences[idx].strip() for idx in indices[:3])
        lines.append("Overview:")
        lines.append(overview)
        lines.append("")

        # Key points
        lines.append("Key Points:")
        for point in key_points[:5]:
            lines.append(f"* {point}")

        return "\n".join(lines)

    def _extract_key_points_from_sentences(
        self, sentences: List[str], top_indices: List[int]
    ) -> List[str]:
        """Extract key points from top-scoring sentences."""
        key_points = []
        for idx in top_indices[:5]:
            sent = sentences[idx].strip()
            # Clean up for key point display
            if sent.endswith('.'):
                sent = sent[:-1]
            # Truncate very long points
            if len(sent) > 200:
                sent = sent[:197] + "..."
            key_points.append(sent)
        return key_points
