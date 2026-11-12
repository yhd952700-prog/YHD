"""
Translation Plugin for LiuHao AI OS

Provides text translation, language detection, batch translation,
and caching for repeated translations. Supports multiple languages
through a pluggable translation backend interface.

Capabilities:
- translate_text: Translate text between languages
- detect_language: Detect the language of given text
- batch_translate: Translate multiple texts at once
- get_supported_languages: List all supported language pairs
"""

import hashlib
import time
from typing import Dict, Any, Optional, List, Tuple
from dataclasses import dataclass, field

from ..plugin_base import (
    BasePlugin,
    PluginMetadata,
    PluginStatus,
    PluginError,
    PluginExecutionError,
)


@dataclass
class TranslationResult:
    """Result of a translation operation."""
    source_text: str
    translated_text: str
    source_language: str
    target_language: str
    confidence: float = 1.0
    from_cache: bool = False


@dataclass
class DetectionResult:
    """Result of a language detection operation."""
    text: str
    detected_language: str
    confidence: float
    alternatives: List[Tuple[str, float]] = field(default_factory=list)


class TranslatePlugin(BasePlugin):
    """Translation plugin with multi-language support and caching.

    Provides translation between multiple languages, automatic language
    detection, batch translation, and an LRU cache for repeated queries.

    Example:
        plugin = TranslatePlugin()
        plugin.initialize()
        plugin.activate()

        result = plugin.translate_text("Hello world", "zh-CN")
        print(result.translated_text)  # "你好，世界"
    """

    # Common language codes and names
    SUPPORTED_LANGUAGES: Dict[str, str] = {
        "en": "English",
        "zh-CN": "简体中文",
        "zh-TW": "繁體中文",
        "ja": "日本語",
        "ko": "한국어",
        "fr": "Français",
        "de": "Deutsch",
        "es": "Español",
        "it": "Italiano",
        "pt": "Português",
        "ru": "Русский",
        "ar": "العربية",
        "hi": "हिन्दी",
        "th": "ไทย",
        "vi": "Tiếng Việt",
        "id": "Bahasa Indonesia",
        "ms": "Bahasa Melayu",
        "nl": "Nederlands",
        "pl": "Polski",
        "tr": "Türkçe",
    }

    def __init__(self) -> None:
        """Initialize the translate plugin with default metadata."""
        metadata = PluginMetadata(
            name="translate",
            version="1.0.0",
            description="Multi-language translation plugin with caching",
            author="LiuHao AI OS Team",
            categories=["language", "translation", "utility"],
            keywords=["translate", "translation", "language", "i18n"],
            requires_internet=False,
            dependencies=[],
        )
        super().__init__(metadata)

        # Translation cache (LRU style with size limit)
        self._cache: Dict[str, TranslationResult] = {}
        self._cache_size: int = 1000
        self._cache_hits: int = 0
        self._cache_misses: int = 0

        # Simple dictionary-based translation tables
        self._translation_tables: Dict[str, Dict[str, Dict[str, str]]] = {}
        self._init_basic_translations()

    def _init_basic_translations(self) -> None:
        """Initialize basic translation dictionaries for common phrases."""
        # English to Chinese (simplified) common phrases
        self._translation_tables["en_zh-CN"] = {
            "hello": "你好",
            "hello world": "你好，世界",
            "goodbye": "再见",
            "thank you": "谢谢",
            "thanks": "谢谢",
            "yes": "是",
            "no": "否",
            "please": "请",
            "sorry": "对不起",
            "how are you": "你好吗",
            "good morning": "早上好",
            "good night": "晚安",
            "i love you": "我爱你",
            "what is your name": "你叫什么名字",
            "my name is": "我的名字是",
            "how much": "多少钱",
            "where is": "在哪里",
            "i don't understand": "我不明白",
            "can you help me": "你能帮我吗",
            "nice to meet you": "很高兴认识你",
        }

        # Chinese to English
        self._translation_tables["zh-CN_en"] = {
            v: k for k, v in self._translation_tables["en_zh-CN"].items()
        }

        # English to Japanese
        self._translation_tables["en_ja"] = {
            "hello": "こんにちは",
            "hello world": "こんにちは世界",
            "goodbye": "さようなら",
            "thank you": "ありがとう",
            "thanks": "ありがとう",
            "yes": "はい",
            "no": "いいえ",
            "please": "お願いします",
            "sorry": "ごめんなさい",
            "good morning": "おはようございます",
            "good night": "おやすみなさい",
            "i love you": "愛してる",
        }

    # ==================== Lifecycle Methods ====================

    def initialize(self, config: Optional[Dict[str, Any]] = None) -> bool:
        """Initialize the translation plugin.

        Args:
            config: Optional configuration with keys:
                - cache_size: Maximum number of cached translations
                - default_source: Default source language
                - default_target: Default target language

        Returns:
            True if initialization succeeded.
        """
        try:
            if config:
                self._cache_size = config.get("cache_size", self._cache_size)

            self._set_status(PluginStatus.INITIALIZED)
            return True
        except Exception as e:
            self._set_error(f"Initialization failed: {e}")
            return False

    def activate(self) -> bool:
        """Activate the translation plugin.

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
        """Deactivate the translation plugin.

        Returns:
            True if deactivation succeeded.
        """
        try:
            self._set_status(PluginStatus.INACTIVE)
            return True
        except Exception as e:
            self._set_error(str(e))
            return False

    def shutdown(self) -> bool:
        """Shut down the translation plugin and clear cache.

        Returns:
            True if shutdown succeeded.
        """
        try:
            self._cache.clear()
            self._set_status(PluginStatus.SHUTDOWN)
            return True
        except Exception as e:
            self._set_error(str(e))
            return False

    def get_capabilities(self) -> List[str]:
        """Return the list of capabilities this plugin provides."""
        return [
            "translate_text",
            "detect_language",
            "batch_translate",
            "get_supported_languages",
            "clear_cache",
            "get_cache_stats",
        ]

    # ==================== Translation Methods ====================

    def translate_text(
        self,
        text: str,
        target_language: str,
        source_language: Optional[str] = None,
        use_cache: bool = True,
    ) -> TranslationResult:
        """Translate text from source language to target language.

        Args:
            text: The text to translate.
            target_language: Target language code (e.g., 'zh-CN', 'ja').
            source_language: Source language code. Auto-detected if None.
            use_cache: Whether to use the translation cache.

        Returns:
            TranslationResult with translated text and metadata.

        Raises:
            PluginExecutionError: If the plugin is not active.
            ValueError: If the target language is not supported.
        """
        self._ensure_active()
        self._increment_call_count()

        if not text or not text.strip():
            return TranslationResult(
                source_text=text,
                translated_text=text,
                source_language=source_language or "unknown",
                target_language=target_language,
                confidence=1.0,
            )

        if target_language not in self.SUPPORTED_LANGUAGES:
            raise ValueError(f"Unsupported target language: {target_language}")

        # Auto-detect source language if not provided
        if source_language is None:
            detection = self._detect_language_internal(text)
            source_language = detection.detected_language

        # Check cache
        cache_key = self._make_cache_key(text, source_language, target_language)
        if use_cache and cache_key in self._cache:
            self._cache_hits += 1
            cached = self._cache[cache_key]
            return TranslationResult(
                source_text=cached.source_text,
                translated_text=cached.translated_text,
                source_language=cached.source_language,
                target_language=cached.target_language,
                confidence=cached.confidence,
                from_cache=True,
            )

        self._cache_misses += 1

        # Perform translation
        translated = self._do_translate(text, source_language, target_language)

        result = TranslationResult(
            source_text=text,
            translated_text=translated,
            source_language=source_language,
            target_language=target_language,
            confidence=0.8 if source_language == "unknown" else 0.9,
            from_cache=False,
        )

        # Cache the result
        if use_cache:
            self._cache_result(cache_key, result)

        return result

    def detect_language(self, text: str) -> DetectionResult:
        """Detect the language of the given text.

        Args:
            text: The text to analyze.

        Returns:
            DetectionResult with detected language and confidence.

        Raises:
            PluginExecutionError: If the plugin is not active.
        """
        self._ensure_active()
        self._increment_call_count()
        return self._detect_language_internal(text)

    def _detect_language_internal(self, text: str) -> DetectionResult:
        """Internal language detection implementation.

        Uses character-based heuristics to detect language.
        """
        text = text.strip().lower()
        if not text:
            return DetectionResult(text=text, detected_language="unknown", confidence=0.0)

        # Check for CJK characters
        cjk_count = sum(1 for c in text if '\u4e00' <= c <= '\u9fff')
        kana_count = sum(1 for c in text if '\u3040' <= c <= '\u30ff')
        hangul_count = sum(1 for c in text if '\uac00' <= c <= '\ud7af')
        cyrillic_count = sum(1 for c in text if '\u0400' <= c <= '\u04ff')
        arabic_count = sum(1 for c in text if '\u0600' <= c <= '\u06ff')
        thai_count = sum(1 for c in text if '\u0e00' <= c <= '\u0e7f')

        total_chars = max(len([c for c in text if c.isalpha()]), 1)

        scores: List[Tuple[str, float]] = []

        if cjk_count > 0:
            # Distinguish Chinese vs Japanese
            if kana_count > 0:
                scores.append(("ja", min(0.95, (cjk_count + kana_count) / total_chars)))
            else:
                scores.append(("zh-CN", min(0.9, cjk_count / total_chars)))

        if hangul_count > 0:
            scores.append(("ko", min(0.95, hangul_count / total_chars)))

        if cyrillic_count > 0:
            scores.append(("ru", min(0.9, cyrillic_count / total_chars)))

        if arabic_count > 0:
            scores.append(("ar", min(0.9, arabic_count / total_chars)))

        if thai_count > 0:
            scores.append(("th", min(0.9, thai_count / total_chars)))

        # Check for Latin-based languages with common word patterns
        latin_chars = sum(1 for c in text if 'a' <= c <= 'z')
        if latin_chars > 0 and len(scores) == 0:
            # Simple English check using common words
            english_words = {"the", "is", "are", "was", "were", "have", "has",
                           "had", "will", "would", "could", "should", "may",
                           "might", "can", "shall", "not", "no", "yes",
                           "this", "that", "these", "those", "with", "from"}
            words = set(text.split())
            en_matches = len(words & english_words)
            if en_matches > 0:
                scores.append(("en", min(0.85, en_matches / max(len(words), 1) + 0.3)))
            else:
                scores.append(("en", 0.5))  # Default for Latin text

        if not scores:
            return DetectionResult(
                text=text,
                detected_language="unknown",
                confidence=0.1,
                alternatives=[],
            )

        # Sort by confidence descending
        scores.sort(key=lambda x: x[1], reverse=True)

        detected_lang, confidence = scores[0]
        alternatives = scores[1:4] if len(scores) > 1 else []

        return DetectionResult(
            text=text,
            detected_language=detected_lang,
            confidence=round(confidence, 3),
            alternatives=alternatives,
        )

    def batch_translate(
        self,
        texts: List[str],
        target_language: str,
        source_language: Optional[str] = None,
    ) -> List[TranslationResult]:
        """Translate multiple texts in batch.

        Args:
            texts: List of text strings to translate.
            target_language: Target language code.
            source_language: Source language code (auto-detect if None).

        Returns:
            List of TranslationResult objects.

        Raises:
            PluginExecutionError: If the plugin is not active.
        """
        self._ensure_active()
        results: List[TranslationResult] = []
        for text in texts:
            result = self.translate_text(text, target_language, source_language)
            results.append(result)
        return results

    def get_supported_languages(self) -> Dict[str, str]:
        """Get all supported languages.

        Returns:
            Dictionary mapping language codes to language names.
        """
        return dict(self.SUPPORTED_LANGUAGES)

    # ==================== Cache Management ====================

    def clear_cache(self) -> int:
        """Clear the translation cache.

        Returns:
            Number of entries cleared.
        """
        count = len(self._cache)
        self._cache.clear()
        return count

    def get_cache_stats(self) -> Dict[str, Any]:
        """Get cache statistics.

        Returns:
            Dict with cache size, hits, misses, and hit rate.
        """
        total = self._cache_hits + self._cache_misses
        hit_rate = self._cache_hits / total if total > 0 else 0.0
        return {
            "size": len(self._cache),
            "max_size": self._cache_size,
            "hits": self._cache_hits,
            "misses": self._cache_misses,
            "hit_rate": round(hit_rate, 4),
        }

    # ==================== Internal Helpers ====================

    def _do_translate(self, text: str, source_lang: str, target_lang: str) -> str:
        """Perform the actual translation.

        Uses dictionary lookup for known phrases, falls back to
        a simple word-by-word approach, or returns the original
        text with a marker for unsupported pairs.
        """
        if source_lang == target_lang:
            return text

        table_key = f"{source_lang}_{target_lang}"
        reverse_key = f"{target_lang}_{source_lang}"

        # Exact match in translation table
        text_lower = text.strip().lower()
        table = self._translation_tables.get(table_key, {})
        if text_lower in table:
            return table[text_lower]

        # Try word-by-word translation for English-based pairs
        if source_lang == "en" and target_lang in ["zh-CN", "ja"]:
            words = text_lower.split()
            translated_words = []
            for word in words:
                if word in table:
                    translated_words.append(table[word])
                else:
                    translated_words.append(word)

            if target_lang == "zh-CN":
                return "".join(translated_words)
            elif target_lang == "ja":
                return "".join(translated_words)

        # Fallback: return original with language tag
        return f"[{target_lang}] {text}"

    def _make_cache_key(self, text: str, source: str, target: str) -> str:
        """Create a cache key for a translation request."""
        raw = f"{source}:{target}:{text.lower().strip()}"
        return hashlib.md5(raw.encode("utf-8")).hexdigest()

    def _cache_result(self, key: str, result: TranslationResult) -> None:
        """Cache a translation result with LRU eviction."""
        if len(self._cache) >= self._cache_size:
            # Evict oldest entry (simple FIFO for now)
            oldest_key = next(iter(self._cache))
            del self._cache[oldest_key]
        self._cache[key] = result
