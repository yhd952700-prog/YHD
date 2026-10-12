"""
Slug Generation Plugin for LiuHao AI OS

Provides text to URL slug conversion with multi-language support,
custom separators, case options, stop word removal, and max length control.

Capabilities:
- generate: Generate URL slug from text
- generate_batch: Generate slugs for multiple texts
- with_custom_separator: Slug with custom separator
- with_case: Slug with specific case
- remove_stop_words: Slug with stop words removed
- with_max_length: Slug truncated to max length
- validate: Validate if a string is a valid slug
- get_stop_words: Get stop words for a language
- add_stop_words: Add custom stop words
"""

import re
import unicodedata
from typing import Dict, Any, Optional, List, Set
from dataclasses import dataclass, field

from ..plugin_base import (
    BasePlugin,
    PluginMetadata,
    PluginStatus,
    PluginExecutionError,
)


@dataclass
class SlugResult:
    """Result of slug generation."""
    original: str
    slug: str
    length: int
    words_removed: int
    options: Dict[str, Any]


class SlugPlugin(BasePlugin):
    """Slug generation plugin.

    Converts text to URL-friendly slugs with multi-language support,
    stop word removal, custom separators, and length control.

    Example:
        plugin = SlugPlugin()
        plugin.initialize()
        plugin.activate()

        result = plugin.generate("Hello World! This is a Test")
        print(result.slug)  # hello-world-this-is-a-test
    """

    # Stop words by language
    STOP_WORDS: Dict[str, Set[str]] = {
        "en": {
            "a", "an", "the", "and", "or", "but", "in", "on", "at", "to",
            "for", "of", "with", "by", "from", "as", "is", "are", "was",
            "were", "be", "been", "being", "have", "has", "had", "do",
            "does", "did", "will", "would", "could", "should", "may",
            "might", "must", "shall", "can", "this", "that", "these",
            "those", "it", "its", "i", "you", "he", "she", "we", "they",
            "them", "their", "what", "which", "who", "whom", "when",
            "where", "why", "how", "all", "each", "every", "both",
            "few", "more", "most", "other", "some", "such", "no",
            "nor", "not", "only", "own", "same", "so", "than", "too",
            "very", "just", "also", "now", "here", "there", "then",
            "once", "if", "because", "until", "while", "about", "into",
            "through", "during", "before", "after", "above", "below",
            "up", "down", "out", "off", "over", "under", "again",
        },
        "zh": {
            "的", "了", "在", "是", "我", "有", "和", "就", "不", "人",
            "都", "一", "一个", "上", "也", "很", "到", "说", "要", "去",
            "你", "会", "着", "没有", "看", "好", "自己", "这", "那",
            "这个", "那个", "什么", "怎么", "为什么", "吗", "呢", "吧",
            "啊", "哦", "嗯", "了", "之", "其", "或", "而", "等",
        },
        "es": {
            "el", "la", "los", "las", "un", "una", "unos", "unas",
            "y", "o", "pero", "ni", "que", "de", "del", "a", "al",
            "en", "con", "sin", "por", "para", "como", "es", "son",
            "fue", "ser", "estar", "ha", "han", "he", "hemos",
            "este", "esta", "estos", "estas", "ese", "esa", "esos",
            "esas", "aquel", "aquella", "me", "te", "se", "nos",
            "os", "lo", "le", "les", "mi", "tu", "su", "muy",
            "mas", "menos", "tan", "tanto", "ya", "todavia",
        },
        "fr": {
            "le", "la", "les", "un", "une", "des", "de", "du",
            "et", "ou", "mais", "ni", "que", "qui", "quoi", "dont",
            "ou", "a", "au", "aux", "en", "dans", "sur", "sous",
            "pour", "par", "avec", "sans", "comme", "est", "sont",
            "etait", "etaient", "etre", "avoir", "fait", "faire",
            "ce", "cette", "ces", "cet", "il", "elle", "ils",
            "elles", "je", "tu", "nous", "vous", "on", "mon",
            "ton", "son", "ma", "ta", "sa", "mes", "tes", "ses",
            "tres", "plus", "moins", "trop", "peu", "bien",
        },
        "de": {
            "der", "die", "das", "den", "dem", "des", "ein", "eine",
            "einer", "eines", "einem", "einen", "und", "oder", "aber",
            "wenn", "weil", "dass", "ob", "wie", "was", "wer",
            "wo", "wann", "warum", "zu", "zur", "zum", "in", "im",
            "an", "am", "auf", "fur", "mit", "ohne", "uber", "unter",
            "vor", "nach", "bei", "von", "bis", "durch", "gegen",
            "ist", "sind", "war", "waren", "sein", "haben", "hat",
            "hatte", "werden", "wird", "wurde", "ich", "du", "er",
            "sie", "es", "wir", "ihr", "sie", "sich", "mich", "dich",
            "sehr", "mehr", "weniger", "nicht", "kein", "keine",
        },
        "pt": {
            "o", "a", "os", "as", "um", "uma", "uns", "umas",
            "e", "ou", "mas", "nem", "que", "quem", "qual",
            "de", "do", "da", "dos", "das", "em", "no", "na",
            "nos", "nas", "por", "para", "com", "sem", "como",
            "e", "sao", "era", "eram", "ser", "ter", "tem",
            "tinha", "tinham", "este", "esta", "estes", "estas",
            "esse", "essa", "esses", "essas", "aquele", "aquela",
            "eu", "tu", "ele", "ela", "nos", "vos", "eles", "elas",
            "muito", "mais", "menos", "bem", "mal", "nao",
        },
        "it": {
            "il", "lo", "la", "i", "gli", "le", "un", "uno", "una",
            "e", "o", "ma", "che", "chi", "cosa", "di", "del", "della",
            "dei", "delle", "a", "al", "alla", "ai", "alle", "in", "nel",
            "nella", "nei", "nelle", "con", "senza", "per", "da", "su",
            "tra", "fra", "come", "e", "sono", "era", "erano", "essere",
            "avere", "ha", "hanno", "aveva", "avevano", "questo", "questa",
            "questi", "queste", "quello", "quella", "quelli", "quelle",
            "io", "tu", "lui", "lei", "noi", "voi", "loro", "molto",
            "piu", "meno", "troppo", "bene", "male", "non",
        },
    }

    # Unicode character replacements for common accented characters
    CHAR_REPLACEMENTS: Dict[str, str] = {
        # Latin
        "ä": "ae", "ö": "oe", "ü": "ue", "ß": "ss",
        "Ä": "AE", "Ö": "OE", "Ü": "UE",
        "æ": "ae", "Æ": "AE", "ø": "o", "Ø": "O",
        "å": "a", "Å": "A", "ð": "d", "Ð": "D",
        "þ": "th", "Þ": "Th",
        # Cyrillic to Latin (transliteration)
        "а": "a", "б": "b", "в": "v", "г": "g", "д": "d",
        "е": "e", "ё": "yo", "ж": "zh", "з": "z", "и": "i",
        "й": "y", "к": "k", "л": "l", "м": "m", "н": "n",
        "о": "o", "п": "p", "р": "r", "с": "s", "т": "t",
        "у": "u", "ф": "f", "х": "kh", "ц": "ts", "ч": "ch",
        "ш": "sh", "щ": "shch", "ъ": "", "ы": "y", "ь": "",
        "э": "e", "ю": "yu", "я": "ya",
        "А": "A", "Б": "B", "В": "V", "Г": "G", "Д": "D",
        "Е": "E", "Ё": "Yo", "Ж": "Zh", "З": "Z", "И": "I",
        "Й": "Y", "К": "K", "Л": "L", "М": "M", "Н": "N",
        "О": "O", "П": "P", "Р": "R", "С": "S", "Т": "T",
        "У": "U", "Ф": "F", "Х": "Kh", "Ц": "Ts", "Ч": "Ch",
        "Ш": "Sh", "Щ": "Shch", "Ъ": "", "Ы": "Y", "Ь": "",
        "Э": "E", "Ю": "Yu", "Я": "Ya",
        # Greek
        "α": "a", "β": "b", "γ": "g", "δ": "d", "ε": "e",
        "ζ": "z", "η": "h", "θ": "th", "ι": "i", "κ": "k",
        "λ": "l", "μ": "m", "ν": "n", "ξ": "x", "ο": "o",
        "π": "p", "ρ": "r", "σ": "s", "τ": "t", "υ": "u",
        "φ": "ph", "χ": "ch", "ψ": "ps", "ω": "o",
    }

    def __init__(self) -> None:
        """Initialize the slug plugin."""
        metadata = PluginMetadata(
            name="slug",
            version="1.0.0",
            description="URL slug generation with multi-language support",
            author="LiuHao AI OS Team",
            categories=["text", "utility", "web"],
            keywords=["slug", "url", "permalink", "seo", "text"],
            requires_internet=False,
            dependencies=[],
        )
        super().__init__(metadata)

        self._default_separator: str = "-"
        self._default_case: str = "lower"
        self._default_language: str = "en"
        self._custom_stop_words: Set[str] = set()

    # ==================== Lifecycle Methods ====================

    def initialize(self, config: Optional[Dict[str, Any]] = None) -> bool:
        """Initialize the slug plugin."""
        try:
            if config:
                self._default_separator = config.get("separator", "-")
                self._default_case = config.get("case", "lower")
                self._default_language = config.get("language", "en")
                if "custom_stop_words" in config:
                    self._custom_stop_words = set(config["custom_stop_words"])

            self._set_status(PluginStatus.INITIALIZED)
            return True
        except Exception as e:
            self._set_error(f"Initialization failed: {e}")
            return False

    def activate(self) -> bool:
        """Activate the slug plugin."""
        try:
            self._set_status(PluginStatus.ACTIVE)
            return True
        except Exception as e:
            self._set_error(f"Activation failed: {e}")
            return False

    def deactivate(self) -> bool:
        """Deactivate the slug plugin."""
        try:
            self._set_status(PluginStatus.INACTIVE)
            return True
        except Exception as e:
            self._set_error(str(e))
            return False

    def shutdown(self) -> bool:
        """Shut down the slug plugin."""
        try:
            self._custom_stop_words.clear()
            self._set_status(PluginStatus.SHUTDOWN)
            return True
        except Exception as e:
            self._set_error(str(e))
            return False

    def get_capabilities(self) -> List[str]:
        """Return the list of capabilities."""
        return [
            "generate",
            "generate_batch",
            "validate",
            "get_stop_words",
            "add_stop_words",
            "remove_stop_words",
            "languages",
            "customize",
        ]

    # ==================== Slug Generation ====================

    def generate(
        self,
        text: str,
        *,
        separator: Optional[str] = None,
        case: Optional[str] = None,
        language: Optional[str] = None,
        remove_stop_words: bool = False,
        max_length: Optional[int] = None,
        preserve_unicode: bool = False,
        lowercase: Optional[bool] = None,
    ) -> SlugResult:
        """Generate a URL slug from text.

        Args:
            text: Input text to slugify.
            separator: Word separator character.
            case: 'lower', 'upper', 'title', or None (lowercase by default).
            language: Language for stop words.
            remove_stop_words: Whether to remove stop words.
            max_length: Maximum slug length.
            preserve_unicode: Keep non-ASCII characters.
            lowercase: Deprecated alias for case='lower'.

        Returns:
            SlugResult with generated slug.
        """
        self._ensure_active()
        self._increment_call_count()

        sep = separator or self._default_separator
        lang = language or self._default_language
        case_style = case or self._default_case

        # Handle deprecated lowercase parameter
        if lowercase is not None and lowercase:
            case_style = "lower"

        original_text = text
        words_removed = 0

        # Step 1: Apply character replacements
        text = self._apply_char_replacements(text)

        # Step 2: Unicode normalization (NFKD decomposes accented chars)
        if not preserve_unicode:
            text = unicodedata.normalize("NFKD", text)
            text = text.encode("ascii", "ignore").decode("ascii")

        # Step 3: Convert to target case
        if case_style == "lower":
            text = text.lower()
        elif case_style == "upper":
            text = text.upper()
        elif case_style == "title":
            text = text.title()

        # Step 4: Replace non-alphanumeric characters with separator
        # Keep unicode letters if preserve_unicode is True
        if preserve_unicode:
            text = re.sub(r"[^\w\s-]", " ", text, flags=re.UNICODE)
        else:
            text = re.sub(r"[^a-zA-Z0-9\s]", " ", text)

        # Step 5: Split into words
        words = text.split()

        # Step 6: Remove stop words if requested
        if remove_stop_words:
            stop_words = self._get_stop_word_set(lang)
            original_count = len(words)
            words = [w for w in words if w.lower() not in stop_words]
            words_removed = original_count - len(words)

        # Step 7: Join with separator
        slug = sep.join(words)

        # Step 8: Clean up multiple separators
        sep_pattern = re.escape(sep)
        slug = re.sub(f"{sep_pattern}+", sep, slug)

        # Step 9: Trim leading/trailing separators
        slug = slug.strip(sep)

        # Step 10: Apply max length
        if max_length and max_length > 0 and len(slug) > max_length:
            slug = self._truncate_slug(slug, max_length, sep)

        # Handle case for title case after truncation
        if case_style == "title" and slug:
            parts = slug.split(sep)
            parts = [p.capitalize() for p in parts]
            slug = sep.join(parts)

        options = {
            "separator": sep,
            "case": case_style,
            "language": lang,
            "remove_stop_words": remove_stop_words,
            "max_length": max_length,
            "preserve_unicode": preserve_unicode,
        }

        return SlugResult(
            original=original_text,
            slug=slug,
            length=len(slug),
            words_removed=words_removed,
            options=options,
        )

    def _apply_char_replacements(self, text: str) -> str:
        """Apply custom character replacements."""
        for char, replacement in self.CHAR_REPLACEMENTS.items():
            text = text.replace(char, replacement)
        return text

    def _truncate_slug(self, slug: str, max_length: int, separator: str) -> str:
        """Truncate slug to max length, avoiding word breaks."""
        if len(slug) <= max_length:
            return slug

        truncated = slug[:max_length]

        # Try to cut at a separator boundary
        last_sep = truncated.rfind(separator)
        if last_sep > max_length // 2:  # Only if we keep at least half
            truncated = truncated[:last_sep]

        return truncated.rstrip(separator)

    def _get_stop_word_set(self, language: str) -> Set[str]:
        """Get the set of stop words for a language."""
        stop_words = set()
        lang = language.lower()

        if lang in self.STOP_WORDS:
            stop_words.update(self.STOP_WORDS[lang])
        elif lang == "all":
            for words in self.STOP_WORDS.values():
                stop_words.update(words)

        # Add custom stop words
        stop_words.update(self._custom_stop_words)

        return stop_words

    # ==================== Batch Generation ====================

    def generate_batch(
        self,
        texts: List[str],
        *,
        ensure_unique: bool = True,
        **kwargs: Any,
    ) -> List[SlugResult]:
        """Generate slugs for multiple texts.

        Args:
            texts: List of input texts.
            ensure_unique: Ensure all slugs are unique (add numbers if needed).
            **kwargs: Same options as generate().

        Returns:
            List of SlugResult objects.
        """
        self._ensure_active()
        self._increment_call_count()

        results: List[SlugResult] = []
        seen_slugs: Dict[str, int] = {}

        for text in texts:
            result = self.generate(text, **kwargs)
            slug = result.slug

            if ensure_unique and slug:
                if slug in seen_slugs:
                    seen_slugs[slug] += 1
                    sep = result.options.get("separator", "-")
                    unique_slug = f"{slug}{sep}{seen_slugs[slug]}"
                    # Update result
                    result.slug = unique_slug
                    result.length = len(unique_slug)
                else:
                    seen_slugs[slug] = 0

            results.append(result)

        return results

    # ==================== Validation ====================

    def validate(
        self,
        slug: str,
        *,
        separator: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Validate if a string is a valid slug.

        Args:
            slug: The string to validate.
            separator: Expected separator (auto-detected if None).

        Returns:
            Dict with validation info.
        """
        self._ensure_active()
        self._increment_call_count()

        issues: List[str] = []

        if not slug:
            return {
                "is_valid": False,
                "issues": ["Slug is empty"],
                "separator": None,
            }

        # Detect separator
        if separator is None:
            # Find the most common non-alphanumeric character
            non_alnum = re.findall(r"[^a-zA-Z0-9]", slug)
            if non_alnum:
                from collections import Counter
                separator = Counter(non_alnum).most_common(1)[0][0]
            else:
                separator = ""

        # Check for spaces
        if " " in slug:
            issues.append("Contains spaces")

        # Check for uppercase
        if any(c.isupper() for c in slug):
            issues.append("Contains uppercase letters")

        # Check for special characters
        if separator:
            pattern = f"^[a-z0-9{re.escape(separator)}]+$"
        else:
            pattern = r"^[a-z0-9]+$"

        if not re.match(pattern, slug):
            issues.append("Contains invalid characters")

        # Check leading/trailing separator
        if separator:
            if slug.startswith(separator):
                issues.append("Starts with separator")
            if slug.endswith(separator):
                issues.append("Ends with separator")

        # Check multiple separators
        if separator and separator * 2 in slug:
            issues.append("Contains multiple consecutive separators")

        return {
            "is_valid": len(issues) == 0,
            "issues": issues,
            "separator": separator,
            "length": len(slug),
            "word_count": len(slug.split(separator)) if separator else 1,
        }

    # ==================== Stop Words Management ====================

    def get_stop_words(self, language: str = "en") -> List[str]:
        """Get stop words for a language.

        Args:
            language: Language code (en, zh, es, fr, de, pt, it, all).

        Returns:
            List of stop words.
        """
        self._ensure_active()
        self._increment_call_count()

        return sorted(self._get_stop_word_set(language))

    def add_stop_words(self, words: List[str]) -> None:
        """Add custom stop words.

        Args:
            words: List of words to add.
        """
        self._ensure_active()
        self._custom_stop_words.update(word.lower() for word in words)

    def remove_custom_stop_words(self, words: List[str]) -> None:
        """Remove custom stop words.

        Args:
            words: List of words to remove.
        """
        self._ensure_active()
        for word in words:
            self._custom_stop_words.discard(word.lower())

    def get_languages(self) -> List[Dict[str, Any]]:
        """Get list of supported languages.

        Returns:
            List of language info dicts.
        """
        languages = []
        language_names = {
            "en": "English",
            "zh": "Chinese",
            "es": "Spanish",
            "fr": "French",
            "de": "German",
            "pt": "Portuguese",
            "it": "Italian",
        }
        for lang, words in self.STOP_WORDS.items():
            languages.append({
                "code": lang,
                "name": language_names.get(lang, lang),
                "stop_word_count": len(words),
            })
        return languages

    # ==================== Utility Methods ====================

    def with_custom_separator(
        self,
        text: str,
        separator: str,
        **kwargs: Any,
    ) -> SlugResult:
        """Generate slug with custom separator (convenience method).

        Args:
            text: Input text.
            separator: Custom separator string.
            **kwargs: Other options.

        Returns:
            SlugResult.
        """
        return self.generate(text, separator=separator, **kwargs)

    def with_case(
        self,
        text: str,
        case: str,
        **kwargs: Any,
    ) -> SlugResult:
        """Generate slug with specific case (convenience method).

        Args:
            text: Input text.
            case: Case style ('lower', 'upper', 'title').
            **kwargs: Other options.

        Returns:
            SlugResult.
        """
        return self.generate(text, case=case, **kwargs)

    def with_max_length(
        self,
        text: str,
        max_length: int,
        **kwargs: Any,
    ) -> SlugResult:
        """Generate slug with max length (convenience method).

        Args:
            text: Input text.
            max_length: Maximum length.
            **kwargs: Other options.

        Returns:
            SlugResult.
        """
        return self.generate(text, max_length=max_length, **kwargs)

    def without_stop_words(
        self,
        text: str,
        language: str = "en",
        **kwargs: Any,
    ) -> SlugResult:
        """Generate slug with stop words removed (convenience method).

        Args:
            text: Input text.
            language: Language for stop words.
            **kwargs: Other options.

        Returns:
            SlugResult.
        """
        return self.generate(text, remove_stop_words=True, language=language, **kwargs)

    # ==================== Statistics ====================

    def get_stats(self) -> Dict[str, Any]:
        """Get plugin statistics."""
        total_stop_words = sum(len(words) for words in self.STOP_WORDS.values())
        return {
            "default_separator": self._default_separator,
            "default_case": self._default_case,
            "default_language": self._default_language,
            "supported_languages": len(self.STOP_WORDS),
            "total_stop_words": total_stop_words,
            "custom_stop_words": len(self._custom_stop_words),
            "char_replacements": len(self.CHAR_REPLACEMENTS),
        }
