"""
Lorem Ipsum Generator Plugin for LiuHao AI OS

Provides lorem ipsum text generation with words, sentences,
paragraphs, multiple languages, HTML output, and word count control.

Capabilities:
- generate_words: Generate random words
- generate_sentences: Generate random sentences
- generate_paragraphs: Generate random paragraphs
- generate_html: Generate HTML-formatted lorem ipsum
- get_word_list: Get available word lists
- word_count: Count words in generated text
"""

import random
from typing import Dict, Any, Optional, List
from dataclasses import dataclass, field

from ..plugin_base import (
    BasePlugin,
    PluginMetadata,
    PluginStatus,
    PluginExecutionError,
)


@dataclass
class LoremResult:
    """Result of lorem ipsum generation."""
    text: str
    word_count: int
    sentence_count: int
    paragraph_count: int
    language: str
    start_with_lorem: bool


class LoremIpsumPlugin(BasePlugin):
    """Lorem ipsum text generator plugin.

    Generates placeholder text with configurable length,
    multiple language support, and HTML output options.

    Example:
        plugin = LoremIpsumPlugin()
        plugin.initialize()
        plugin.activate()

        text = plugin.generate_paragraphs(3)
        print(text)
    """

    # Latin lorem ipsum words (classic)
    LATIN_WORDS = [
        "lorem", "ipsum", "dolor", "sit", "amet", "consectetur",
        "adipiscing", "elit", "sed", "do", "eiusmod", "tempor",
        "incididunt", "ut", "labore", "et", "dolore", "magna",
        "aliqua", "enim", "ad", "minim", "veniam", "quis",
        "nostrud", "exercitation", "ullamco", "laboris", "nisi",
        "aliquip", "ex", "ea", "commodo", "consequat", "duis",
        "aute", "irure", "in", "reprehenderit", "voluptate",
        "velit", "esse", "cillum", "fugiat", "nulla", "pariatur",
        "excepteur", "sint", "occaecat", "cupidatat", "non",
        "proident", "sunt", "culpa", "qui", "officia", "deserunt",
        "mollit", "anim", "id", "est", "laborum",
        # Additional common Latin words
        "ac", "accumsan", "ante", "aptent", "arcu", "auctor",
        "augue", "bibendum", "blandit", "class", "commodo",
        "condimentum", "congue", "conubia", "convallis", "cras",
        "cubilia", "curabitur", "curae", "cursus", "dapibus",
        "dictum", "dictumst", "dignissim", "dis", "donec", "dui",
        "duis", "egestas", "eget", "eleifend", "elementum", "elit",
        "enim", "erat", "eros", "est", "etiam", "eu", "euismod",
        "facilisis", "fames", "faucibus", "felis", "fermentum",
        "feugiat", "fringilla", "fusce", "gravida", "habitant",
        "habitasse", "hac", "hendrerit", "himenaeos", "iaculis",
        "imperdiet", "inceptos", "integer", "interdum", "justo",
        "lacinia", "lacus", "laoreet", "lectus", "leo", "libero",
        "ligula", "litora", "lobortis", "luctus", "maecenas",
        "magna", "magnis", "malesuada", "massa", "mattis", "mauris",
        "metus", "mi", "molestie", "mollis", "montes", "morbi",
        "mus", "nam", "nascetur", "natoque", "nec", "neque",
        "netus", "nibh", "nisl", "nonummy", "nunc", "odio",
        "orci", "ornare", "pellentesque", "penatibus", "per",
        "phasellus", "placerat", "platea", "porta", "porttitor",
        "posuere", "potenti", "praesent", "pretium", "primis",
        "proin", "pulvinar", "purus", "quam", "quisque", "rhoncus",
        "ridiculus", "risus", "rutrum", "sagittis", "sapien",
        "scelerisque", "sed", "sem", "semper", "senectus", "sociis",
        "sodales", "sollicitudin", "suscipit", "suspendisse",
        "taciti", "tellus", "tempor", "tempus", "tincidunt",
        "torquent", "tortor", "tristique", "turpis", "ullamcorper",
        "ultrices", "ultricies", "urna", "varius", "vehicula",
        "vel", "vestibulum", "viverra", "vivamus", "vulputate",
    ]

    # English filler words
    ENGLISH_WORDS = [
        "the", "be", "to", "of", "and", "a", "in", "that", "have",
        "i", "it", "for", "not", "on", "with", "he", "as", "you",
        "do", "at", "this", "but", "his", "by", "from", "they",
        "we", "say", "her", "she", "or", "an", "will", "my",
        "one", "all", "would", "there", "their", "what", "so",
        "up", "out", "if", "about", "who", "get", "which", "go",
        "me", "when", "make", "can", "like", "time", "no", "just",
        "him", "know", "take", "people", "into", "year", "your",
        "good", "some", "could", "them", "see", "other", "than",
        "then", "now", "look", "only", "come", "its", "over",
        "think", "also", "back", "after", "use", "two", "how",
        "our", "work", "first", "well", "way", "even", "new",
        "want", "because", "any", "these", "give", "day", "most",
        "us", "great", "very", "just", "world", "still", "should",
    ]

    # Common English words for sentences
    SENTENCE_STARTERS = [
        "Lorem ipsum", "Sed ut", "At vero", "Duis aute",
        "Excepteur sint", "Ut enim", "Neque porro", "quisquam est",
        "Nemo enim", "Itaque earum", "Temporibus autem",
    ]

    LANGUAGES = ["latin", "english"]

    def __init__(self) -> None:
        """Initialize the lorem ipsum plugin."""
        metadata = PluginMetadata(
            name="lorem-ipsum",
            version="1.0.0",
            description="Lorem ipsum text generator with multiple languages and formats",
            author="LiuHao AI OS Team",
            categories=["text", "utility", "content"],
            keywords=["lorem", "ipsum", "placeholder", "text", "generator", "filler"],
            requires_internet=False,
            dependencies=[],
        )
        super().__init__(metadata)

        self._rng = random.Random()
        self._default_language = "latin"

    # ==================== Lifecycle Methods ====================

    def initialize(self, config: Optional[Dict[str, Any]] = None) -> bool:
        """Initialize the lorem ipsum plugin."""
        try:
            if config:
                seed = config.get("seed")
                if seed is not None:
                    self._rng.seed(seed)
                self._default_language = config.get("default_language", "latin")

            self._set_status(PluginStatus.INITIALIZED)
            return True
        except Exception as e:
            self._set_error(f"Initialization failed: {e}")
            return False

    def activate(self) -> bool:
        """Activate the lorem ipsum plugin."""
        try:
            self._set_status(PluginStatus.ACTIVE)
            return True
        except Exception as e:
            self._set_error(f"Activation failed: {e}")
            return False

    def deactivate(self) -> bool:
        """Deactivate the lorem ipsum plugin."""
        try:
            self._set_status(PluginStatus.INACTIVE)
            return True
        except Exception as e:
            self._set_error(str(e))
            return False

    def shutdown(self) -> bool:
        """Shut down the lorem ipsum plugin."""
        try:
            self._set_status(PluginStatus.SHUTDOWN)
            return True
        except Exception as e:
            self._set_error(str(e))
            return False

    def get_capabilities(self) -> List[str]:
        """Return the list of capabilities."""
        return [
            "generate_words",
            "generate_sentences",
            "generate_paragraphs",
            "generate_html",
            "get_word_list",
            "word_count",
            "get_languages",
        ]

    # ==================== Word Generation ====================

    def generate_words(
        self,
        count: int = 10,
        language: Optional[str] = None,
        start_with_lorem: bool = True,
        capitalize: bool = False,
    ) -> str:
        """Generate random words.

        Args:
            count: Number of words to generate.
            language: Language ('latin' or 'english').
            start_with_lorem: Start with "Lorem ipsum dolor sit amet".
            capitalize: Capitalize first letter.

        Returns:
            Space-separated words string.
        """
        self._ensure_active()
        self._increment_call_count()

        if count < 1:
            raise ValueError("Word count must be at least 1")

        lang = (language or self._default_language).lower()
        words = self._get_words(lang)

        result_words: List[str] = []

        if start_with_lorem and lang == "latin" and count >= 5:
            result_words = ["lorem", "ipsum", "dolor", "sit", "amet"]
            remaining = count - 5
        else:
            remaining = count

        for _ in range(remaining):
            word = self._rng.choice(words)
            # Avoid immediate repeats
            while result_words and word == result_words[-1]:
                word = self._rng.choice(words)
            result_words.append(word)

        result = " ".join(result_words[:count])

        if capitalize:
            result = result.capitalize()

        return result

    # ==================== Sentence Generation ====================

    def generate_sentences(
        self,
        count: int = 5,
        language: Optional[str] = None,
        start_with_lorem: bool = True,
    ) -> str:
        """Generate random sentences.

        Args:
            count: Number of sentences to generate.
            language: Language.
            start_with_lorem: Start with "Lorem ipsum dolor sit amet, ..."

        Returns:
            Sentences joined by spaces.
        """
        self._ensure_active()
        self._increment_call_count()

        if count < 1:
            raise ValueError("Sentence count must be at least 1")

        lang = (language or self._default_language).lower()
        sentences: List[str] = []

        for i in range(count):
            first = (i == 0)
            sentence = self._generate_sentence(
                lang, start_with_lorem and first
            )
            sentences.append(sentence)

        return " ".join(sentences)

    def _generate_sentence(
        self,
        language: str,
        start_with_lorem: bool = False,
    ) -> str:
        """Generate a single sentence."""
        words = self._get_words(language)

        # Sentence length: 5-20 words
        word_count = self._rng.randint(5, 20)

        if start_with_lorem and language == "latin":
            sentence_words = ["Lorem", "ipsum", "dolor", "sit", "amet"]
            remaining = word_count - 5
        else:
            sentence_words = []
            remaining = word_count

        for _ in range(remaining):
            word = self._rng.choice(words)
            # Avoid immediate repeats
            while sentence_words and word.lower() == sentence_words[-1].lower():
                word = self._rng.choice(words)
            sentence_words.append(word)

        # Capitalize first word
        if sentence_words:
            sentence_words[0] = sentence_words[0].capitalize()

        # Add punctuation at end
        sentence = " ".join(sentence_words) + "."

        # Occasionally add a comma
        if len(sentence_words) > 8:
            comma_pos = self._rng.randint(3, len(sentence_words) - 3)
            sentence_words_list = sentence[:-1].split()
            sentence_words_list[comma_pos] += ","
            sentence = " ".join(sentence_words_list) + "."

        return sentence

    # ==================== Paragraph Generation ====================

    def generate_paragraphs(
        self,
        count: int = 3,
        language: Optional[str] = None,
        start_with_lorem: bool = True,
        sentences_per_paragraph: Optional[int] = None,
    ) -> str:
        """Generate random paragraphs.

        Args:
            count: Number of paragraphs to generate.
            language: Language.
            start_with_lorem: Start first paragraph with lorem ipsum.
            sentences_per_paragraph: Fixed sentence count. Random if None.

        Returns:
            Paragraphs joined by newlines.
        """
        self._ensure_active()
        self._increment_call_count()

        if count < 1:
            raise ValueError("Paragraph count must be at least 1")

        lang = (language or self._default_language).lower()
        paragraphs: List[str] = []

        for i in range(count):
            first = (i == 0)
            if sentences_per_paragraph:
                sent_count = sentences_per_paragraph
            else:
                sent_count = self._rng.randint(3, 8)

            sentences: List[str] = []
            for j in range(sent_count):
                first_sent = first and j == 0
                sentence = self._generate_sentence(
                    lang, start_with_lorem and first_sent
                )
                sentences.append(sentence)

            paragraphs.append(" ".join(sentences))

        return "\n\n".join(paragraphs)

    # ==================== HTML Generation ====================

    def generate_html(
        self,
        paragraphs: int = 3,
        language: Optional[str] = None,
        start_with_lorem: bool = True,
        wrap_div: bool = False,
        heading: Optional[str] = None,
    ) -> str:
        """Generate HTML-formatted lorem ipsum.

        Args:
            paragraphs: Number of paragraphs.
            language: Language.
            start_with_lorem: Start with lorem ipsum.
            wrap_div: Wrap in a <div> element.
            heading: Optional heading text.

        Returns:
            HTML string.
        """
        self._ensure_active()
        self._increment_call_count()

        text = self.generate_paragraphs(
            count=paragraphs,
            language=language,
            start_with_lorem=start_with_lorem,
        )

        html_parts: List[str] = []

        if wrap_div:
            html_parts.append('<div class="lorem-ipsum">')

        if heading:
            html_parts.append(f"<h1>{heading}</h1>")

        for para in text.split("\n\n"):
            html_parts.append(f"<p>{para}</p>")

        if wrap_div:
            html_parts.append("</div>")

        return "\n".join(html_parts)

    # ==================== Word List ====================

    def get_word_list(self, language: str = "latin") -> List[str]:
        """Get the word list for a language.

        Args:
            language: Language name.

        Returns:
            List of words.
        """
        self._ensure_active()
        self._increment_call_count()

        return list(self._get_words(language))

    def get_languages(self) -> List[Dict[str, Any]]:
        """Get available languages.

        Returns:
            List of language info dicts.
        """
        self._ensure_active()
        self._increment_call_count()

        return [
            {
                "code": lang,
                "name": lang.capitalize(),
                "word_count": len(self._get_words(lang)),
            }
            for lang in self.LANGUAGES
        ]

    # ==================== Word Count ====================

    def word_count(self, text: str) -> Dict[str, Any]:
        """Count words, sentences, and paragraphs in text.

        Args:
            text: Text to analyze.

        Returns:
            Dict with counts.
        """
        self._ensure_active()
        self._increment_call_count()

        words = text.split()
        word_count = len(words)

        # Count sentences (rough estimate by period/exclamation/question)
        sentence_count = text.count('.') + text.count('!') + text.count('?')

        # Count paragraphs
        paragraphs = [p for p in text.split('\n\n') if p.strip()]
        paragraph_count = len(paragraphs)

        # Character count
        char_count = len(text)
        char_no_space = len(text.replace(' ', '').replace('\n', ''))

        # Average word length
        avg_word_length = (
            round(sum(len(w) for w in words) / word_count, 2)
            if word_count > 0 else 0
        )

        return {
            "words": word_count,
            "sentences": sentence_count,
            "paragraphs": paragraph_count,
            "characters": char_count,
            "characters_no_space": char_no_space,
            "avg_word_length": avg_word_length,
            "words_per_sentence": round(word_count / sentence_count, 2)
            if sentence_count > 0 else 0,
            "sentences_per_paragraph": round(sentence_count / paragraph_count, 2)
            if paragraph_count > 0 else 0,
        }

    # ==================== Internal Helpers ====================

    def _get_words(self, language: str) -> List[str]:
        """Get the word list for a language."""
        language = language.lower()
        if language == "latin":
            return self.LATIN_WORDS
        elif language == "english":
            return self.ENGLISH_WORDS
        else:
            raise ValueError(f"Unsupported language: {language}")

    # ==================== Statistics ====================

    def get_stats(self) -> Dict[str, Any]:
        """Get plugin statistics."""
        return {
            "languages": self.LANGUAGES,
            "latin_word_count": len(self.LATIN_WORDS),
            "english_word_count": len(self.ENGLISH_WORDS),
            "default_language": self._default_language,
            "min_sentence_words": 5,
            "max_sentence_words": 20,
            "min_paragraph_sentences": 3,
            "max_paragraph_sentences": 8,
        }
