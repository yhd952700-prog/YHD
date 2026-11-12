"""
Pronounceable Word Generator Plugin for LiuHao AI OS

Provides pronounceable word generation with custom length,
multiple languages, syllable count control, word list filtering,
and naming options.

Capabilities:
- generate_word: Generate a pronounceable word
- generate_words: Generate multiple pronounceable words
- generate_username: Generate a pronounceable username
- generate_product_name: Generate a product-style name
- syllable_count: Count/control syllables
- filter_wordlist: Filter words by criteria
"""

import random
import re
from typing import Dict, Any, Optional, List, Tuple
from dataclasses import dataclass, field

from ..plugin_base import (
    BasePlugin,
    PluginMetadata,
    PluginStatus,
    PluginExecutionError,
)


@dataclass
class GeneratedWord:
    """A generated pronounceable word."""
    word: str
    syllables: int
    length: int
    pattern: str  # V/C pattern
    language: str


class PronounceablePlugin(BasePlugin):
    """Pronounceable word generator plugin.

    Generates pronounceable words using phonotactic rules
    (syllable structure patterns), with support for multiple
    languages, syllable control, and naming variants.

    Example:
        plugin = PronounceablePlugin()
        plugin.initialize()
        plugin.activate()

        word = plugin.generate_word(length=8)
        print(word.word)
    """

    # Vowels and consonants for English
    ENGLISH_VOWELS = ['a', 'e', 'i', 'o', 'u', 'y']
    ENGLISH_CONSONANTS = [
        'b', 'c', 'd', 'f', 'g', 'h', 'j', 'k', 'l', 'm',
        'n', 'p', 'q', 'r', 's', 't', 'v', 'w', 'x', 'z',
    ]

    # Common consonant clusters (onsets)
    ENGLISH_ONSETS = [
        '', 'b', 'c', 'd', 'f', 'g', 'h', 'j', 'k', 'l', 'm',
        'n', 'p', 'r', 's', 't', 'v', 'w', 'y', 'z',
        'bl', 'br', 'ch', 'cl', 'cr', 'dr', 'fl', 'fr', 'gl',
        'gr', 'pl', 'pr', 'sc', 'sh', 'sk', 'sl', 'sm', 'sn',
        'sp', 'st', 'sw', 'th', 'tr', 'tw', 'wh', 'wr',
        'sch', 'scr', 'shr', 'spl', 'spr', 'squ', 'str', 'thr',
    ]

    # Common vowel nuclei
    ENGLISH_NUCLEI = [
        'a', 'e', 'i', 'o', 'u', 'y',
        'ai', 'ay', 'ea', 'ee', 'ei', 'ey', 'ie', 'oa', 'oe',
        'oo', 'ou', 'ow', 'oy', 'au', 'aw', 'eu', 'ue', 'ui',
        'ia', 'io', 'ua', 'uo',
    ]

    # Common codas (ending consonant clusters)
    ENGLISH_CODAS = [
        '', 'b', 'd', 'f', 'g', 'l', 'm', 'n', 'p', 'r',
        's', 't', 'v', 'w', 'x', 'z',
        'ct', 'ft', 'gh', 'ld', 'lf', 'lk', 'll', 'lm', 'ln',
        'lt', 'mp', 'nd', 'ng', 'nk', 'nt', 'pt', 'rb', 'rc',
        'rd', 'rf', 'rg', 'rk', 'rl', 'rm', 'rn', 'rp', 'rs',
        'rt', 'rv', 'rz', 'sc', 'sk', 'sp', 'ss', 'st', 'th',
        'xt',
        'ght', 'lch', 'ldr', 'ndr', 'ngth', 'nth', 'rch', 'rth',
        'sts', 'tch', 'xth',
    ]

    # Syllable patterns: onset + nucleus + coda
    # C = consonant cluster, V = vowel cluster
    SYLLABLE_PATTERNS = [
        'CV',     # open syllable
        'CVC',    # closed syllable
        'CVCC',   # complex coda
        'CCV',    # complex onset
        'CCVC',   # complex onset + coda
        'CCVCC',  # complex onset + coda
        'V',      # vowel-only (rare)
        'VC',     # vowel + coda
    ]

    def __init__(self) -> None:
        """Initialize the pronounceable word plugin."""
        metadata = PluginMetadata(
            name="pronounceable",
            version="1.0.0",
            description="Pronounceable word generator with syllable control and naming options",
            author="LiuHao AI OS Team",
            categories=["text", "utility", "generator"],
            keywords=["pronounceable", "word", "generator", "syllable", "username", "naming"],
            requires_internet=False,
            dependencies=[],
        )
        super().__init__(metadata)

        self._rng = random.Random()

    # ==================== Lifecycle Methods ====================

    def initialize(self, config: Optional[Dict[str, Any]] = None) -> bool:
        """Initialize the pronounceable word plugin."""
        try:
            if config:
                seed = config.get("seed")
                if seed is not None:
                    self._rng.seed(seed)

            self._set_status(PluginStatus.INITIALIZED)
            return True
        except Exception as e:
            self._set_error(f"Initialization failed: {e}")
            return False

    def activate(self) -> bool:
        """Activate the pronounceable word plugin."""
        try:
            self._set_status(PluginStatus.ACTIVE)
            return True
        except Exception as e:
            self._set_error(f"Activation failed: {e}")
            return False

    def deactivate(self) -> bool:
        """Deactivate the pronounceable word plugin."""
        try:
            self._set_status(PluginStatus.INACTIVE)
            return True
        except Exception as e:
            self._set_error(str(e))
            return False

    def shutdown(self) -> bool:
        """Shut down the pronounceable word plugin."""
        try:
            self._set_status(PluginStatus.SHUTDOWN)
            return True
        except Exception as e:
            self._set_error(str(e))
            return False

    def get_capabilities(self) -> List[str]:
        """Return the list of capabilities."""
        return [
            "generate_word",
            "generate_words",
            "generate_username",
            "generate_product_name",
            "syllable_count",
            "is_pronounceable",
            "get_syllable_patterns",
        ]

    # ==================== Word Generation ====================

    def generate_word(
        self,
        length: Optional[int] = None,
        syllables: Optional[int] = None,
        language: str = "english",
        capitalize: bool = False,
    ) -> GeneratedWord:
        """Generate a pronounceable word.

        Args:
            length: Target word length. If None, determined by syllables.
            syllables: Number of syllables. If None, random (2-3).
            language: Language (currently 'english').
            capitalize: Capitalize first letter.

        Returns:
            GeneratedWord object.
        """
        self._ensure_active()
        self._increment_call_count()

        if syllables is None:
            syllables = self._rng.randint(2, 3)

        if syllables < 1:
            raise ValueError("Syllable count must be at least 1")

        word_parts: List[str] = []
        pattern_parts: List[str] = []

        for i in range(syllables):
            # Choose pattern based on position
            if i == 0 and syllables > 1:
                # First syllable can be simpler
                patterns = ['CV', 'CVC', 'CCV', 'CCVC', 'V', 'VC']
                weights = [25, 20, 15, 10, 5, 5]
            elif i == syllables - 1:
                # Last syllable
                patterns = ['CV', 'CVC', 'CVCC', 'CCV', 'CCVC', 'CCVCC']
                weights = [20, 25, 15, 10, 15, 10]
            else:
                # Middle syllables
                patterns = ['CV', 'CVC', 'CCV', 'CCVC']
                weights = [30, 25, 20, 20]

            pattern = self._weighted_choice(patterns, weights)
            syllable = self._build_syllable(pattern)
            word_parts.append(syllable)
            pattern_parts.append(pattern)

        word = ''.join(word_parts)

        # Adjust length if specified
        if length is not None:
            word = self._adjust_length(word, length)

        if capitalize:
            word = word.capitalize()

        actual_syllables = self.count_syllables(word)

        return GeneratedWord(
            word=word,
            syllables=actual_syllables,
            length=len(word),
            pattern='.'.join(pattern_parts),
            language=language,
        )

    def generate_words(
        self,
        count: int = 10,
        length: Optional[int] = None,
        syllables: Optional[int] = None,
        language: str = "english",
        capitalize: bool = False,
    ) -> List[GeneratedWord]:
        """Generate multiple pronounceable words.

        Args:
            count: Number of words to generate.
            length: Target word length.
            syllables: Number of syllables per word.
            language: Language.
            capitalize: Capitalize first letter.

        Returns:
            List of GeneratedWord objects.
        """
        self._ensure_active()
        self._increment_call_count()

        words: List[GeneratedWord] = []
        seen = set()

        max_attempts = count * 10
        attempts = 0

        while len(words) < count and attempts < max_attempts:
            word = self.generate_word(
                length=length,
                syllables=syllables,
                language=language,
                capitalize=capitalize,
            )
            attempts += 1

            if word.word not in seen:
                seen.add(word.word)
                words.append(word)

        return words

    # ==================== Naming ====================

    def generate_username(
        self,
        style: str = "random",
        add_number: bool = True,
        separator: str = "",
    ) -> str:
        """Generate a pronounceable username.

        Args:
            style: Style ('random', 'cool', 'cute', 'tech', 'fantasy').
            add_number: Add a random number at the end.
            separator: Separator between word and number.

        Returns:
            Username string.
        """
        self._ensure_active()
        self._increment_call_count()

        # Generate base word
        if style == "cool":
            word = self.generate_word(length=self._rng.randint(5, 8), syllables=2).word
        elif style == "cute":
            word = self.generate_word(length=self._rng.randint(4, 6), syllables=2).word
        elif style == "tech":
            word = self.generate_word(length=self._rng.randint(4, 7), syllables=2).word
        elif style == "fantasy":
            word = self.generate_word(length=self._rng.randint(6, 10), syllables=3).word
        else:
            word = self.generate_word(syllables=2).word

        username = word

        if add_number:
            number = self._rng.randint(1, 9999)
            username = f"{word}{separator}{number}"

        return username

    def generate_product_name(
        self,
        style: str = "modern",
        word_count: int = 2,
    ) -> str:
        """Generate a product-style name.

        Args:
            style: Style ('modern', 'classic', 'tech', 'luxury').
            word_count: Number of words in the name.

        Returns:
            Product name string.
        """
        self._ensure_active()
        self._increment_call_count()

        words: List[str] = []

        for i in range(word_count):
            if style == "modern":
                length = self._rng.randint(4, 7)
                syllables = 2
            elif style == "classic":
                length = self._rng.randint(5, 8)
                syllables = self._rng.randint(2, 3)
            elif style == "tech":
                length = self._rng.randint(3, 6)
                syllables = 2
            elif style == "luxury":
                length = self._rng.randint(6, 10)
                syllables = 3
            else:
                length = None
                syllables = 2

            word = self.generate_word(
                length=length,
                syllables=syllables,
                capitalize=True,
            ).word
            words.append(word)

        return " ".join(words)

    # ==================== Syllable Counting ====================

    def count_syllables(self, word: str) -> int:
        """Count syllables in a word (approximate).

        Uses vowel counting heuristic.

        Args:
            word: Word to count syllables in.

        Returns:
            Estimated syllable count.
        """
        self._ensure_active()
        self._increment_call_count()

        word = word.lower().strip()
        if not word:
            return 0

        # Count vowel groups
        vowels = set('aeiouy')
        count = 0
        prev_vowel = False

        for ch in word:
            is_vowel = ch in vowels
            if is_vowel and not prev_vowel:
                count += 1
            prev_vowel = is_vowel

        # Silent 'e' at end
        if word.endswith('e') and len(word) > 1:
            count -= 1

        # Words ending in 'le' preceded by consonant
        if len(word) > 2 and word.endswith('le') and word[-3] not in vowels:
            count += 1

        # Minimum 1 syllable
        return max(1, count)

    def syllable_count(self, text: str) -> Dict[str, Any]:
        """Count syllables in a text.

        Args:
            text: Text to analyze.

        Returns:
            Dict with syllable counts.
        """
        self._ensure_active()
        self._increment_call_count()

        words = text.split()
        total_syllables = 0
        word_syllables: Dict[str, int] = {}

        for word in words:
            clean_word = re.sub(r'[^a-zA-Z]', '', word)
            if clean_word:
                syl = self.count_syllables(clean_word)
                total_syllables += syl
                word_syllables[clean_word] = syl

        return {
            "total_words": len(words),
            "total_syllables": total_syllables,
            "avg_syllables_per_word": round(total_syllables / len(words), 2)
            if words else 0,
            "word_syllables": word_syllables,
        }

    # ==================== Pronounceability Check ====================

    def is_pronounceable(self, word: str) -> Tuple[bool, List[str]]:
        """Check if a word is pronounceable (basic check).

        Args:
            word: Word to check.

        Returns:
            Tuple of (is_pronounceable, list_of_issues).
        """
        self._ensure_active()
        self._increment_call_count()

        issues: List[str] = []
        word_lower = word.lower()

        # Must have at least one vowel
        if not any(c in 'aeiouy' for c in word_lower):
            issues.append("No vowels found")

        # Check for impossible consonant clusters (more than 4 in a row)
        consonant_run = 0
        max_run = 0
        for ch in word_lower:
            if ch in 'bcdfghjklmnpqrstvwxz':
                consonant_run += 1
                max_run = max(max_run, consonant_run)
            else:
                consonant_run = 0

        if max_run > 5:
            issues.append(f"Too many consecutive consonants ({max_run})")

        # Check length
        if len(word) < 2:
            issues.append("Word too short")

        return len(issues) == 0, issues

    # ==================== Internal Helpers ====================

    def _build_syllable(self, pattern: str) -> str:
        """Build a syllable from a C/V pattern."""
        syllable = ''

        for ch in pattern:
            if ch == 'C':
                # Consonant cluster
                cluster = self._rng.choice(self.ENGLISH_ONSETS)
                if not cluster:
                    cluster = self._rng.choice(self.ENGLISH_CONSONANTS)
                syllable += cluster
            elif ch == 'V':
                # Vowel nucleus
                nucleus = self._rng.choice(self.ENGLISH_NUCLEI)
                syllable += nucleus

        # For CVC and longer, add coda
        if 'C' in pattern and pattern.index('C') < pattern.rindex('C'):
            # Has final consonant(s)
            coda = self._rng.choice(self.ENGLISH_CODAS)
            # Replace the last C with coda
            # Actually, let's handle this differently
            pass

        # Simpler approach: build onset + nucleus + coda based on pattern
        if pattern == 'CV':
            onset = self._pick_onset()
            nucleus = self._pick_nucleus()
            return onset + nucleus
        elif pattern == 'CVC':
            onset = self._pick_onset()
            nucleus = self._pick_nucleus()
            coda = self._pick_coda()
            return onset + nucleus + coda
        elif pattern == 'CVCC':
            onset = self._pick_onset()
            nucleus = self._pick_nucleus()
            coda = self._pick_coda(complex=True)
            return onset + nucleus + coda
        elif pattern == 'CCV':
            onset = self._pick_onset(complex=True)
            nucleus = self._pick_nucleus()
            return onset + nucleus
        elif pattern == 'CCVC':
            onset = self._pick_onset(complex=True)
            nucleus = self._pick_nucleus()
            coda = self._pick_coda()
            return onset + nucleus + coda
        elif pattern == 'CCVCC':
            onset = self._pick_onset(complex=True)
            nucleus = self._pick_nucleus()
            coda = self._pick_coda(complex=True)
            return onset + nucleus + coda
        elif pattern == 'V':
            return self._pick_nucleus()
        elif pattern == 'VC':
            nucleus = self._pick_nucleus()
            coda = self._pick_coda()
            return nucleus + coda

        return syllable

    def _pick_onset(self, complex: bool = False) -> str:
        """Pick a syllable onset."""
        if complex:
            # Pick from multi-consonant onsets
            complex_onsets = [o for o in self.ENGLISH_ONSETS if len(o) >= 2]
            return self._rng.choice(complex_onsets)
        else:
            # Pick from single consonants
            simple = [o for o in self.ENGLISH_ONSETS if len(o) == 1 and o]
            return self._rng.choice(simple)

    def _pick_nucleus(self) -> str:
        """Pick a syllable nucleus (vowel sound)."""
        # Weight toward simple vowels
        simple_vowels = ['a', 'e', 'i', 'o', 'u']
        if self._rng.random() < 0.6:
            return self._rng.choice(simple_vowels)
        else:
            return self._rng.choice(self.ENGLISH_NUCLEI)

    def _pick_coda(self, complex: bool = False) -> str:
        """Pick a syllable coda."""
        if complex:
            complex_codas = [c for c in self.ENGLISH_CODAS if len(c) >= 2]
            if complex_codas:
                return self._rng.choice(complex_codas)
        # Single consonant coda or short cluster
        simple = [c for c in self.ENGLISH_CODAS if len(c) <= 2]
        return self._rng.choice(simple)

    def _weighted_choice(self, items: List[str], weights: List[int]) -> str:
        """Choose from a list with weighted probabilities."""
        total = sum(weights)
        r = self._rng.randint(1, total)
        cumulative = 0
        for item, weight in zip(items, weights):
            cumulative += weight
            if r <= cumulative:
                return item
        return items[-1]

    def _adjust_length(self, word: str, target: int) -> str:
        """Adjust word to target length."""
        if len(word) == target:
            return word
        elif len(word) < target:
            # Add characters
            add = target - len(word)
            vowels = 'aeiou'
            consonants = 'bcdfghjklmnpqrstvwxyz'
            for _ in range(add):
                if word[-1] in vowels:
                    word += self._rng.choice(consonants)
                else:
                    word += self._rng.choice(vowels)
            return word
        else:
            # Truncate
            return word[:target]

    def get_syllable_patterns(self) -> List[str]:
        """Get available syllable patterns."""
        self._ensure_active()
        self._increment_call_count()
        return list(self.SYLLABLE_PATTERNS)

    # ==================== Statistics ====================

    def get_stats(self) -> Dict[str, Any]:
        """Get plugin statistics."""
        return {
            "languages": ["english"],
            "onsets": len(self.ENGLISH_ONSETS),
            "nuclei": len(self.ENGLISH_NUCLEI),
            "codas": len(self.ENGLISH_CODAS),
            "syllable_patterns": len(self.SYLLABLE_PATTERNS),
            "consonants": len(self.ENGLISH_CONSONANTS),
            "vowels": len(self.ENGLISH_VOWELS),
        }
