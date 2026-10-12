"""
Palindrome Tools Plugin for LiuHao AI OS

Provides palindrome checking, generation, longest palindromic substring,
palindrome phrases, number palindromes, and palindrome permutation check.

Capabilities:
- is_palindrome: Check if text is a palindrome
- generate_palindrome: Generate a palindrome
- longest_substring: Find longest palindromic substring
- phrase_palindrome: Check/generate palindromic phrases
- number_palindrome: Number palindrome tools
- can_form_palindrome: Check if string can form a palindrome
"""

import random
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
class PalindromeResult:
    """Result of a palindrome check or generation."""
    input: str
    is_palindrome: bool
    normalized: str
    details: Dict[str, Any]


@dataclass
class PalindromeSubstring:
    """A palindromic substring found in text."""
    text: str
    start: int
    end: int
    length: int
    is_odd: bool


class PalindromePlugin(BasePlugin):
    """Palindrome tools plugin.

    Provides palindrome checking, generation, longest substring
    finding, phrase palindromes, number palindromes, and
    palindrome permutation checking.

    Example:
        plugin = PalindromePlugin()
        plugin.initialize()
        plugin.activate()

        print(plugin.is_palindrome("A man, a plan, a canal: Panama"))
        # True
    """

    # Common palindrome words
    PALINDROME_WORDS = [
        "level", "radar", "madam", "racecar", "rotor", "civic",
        "kayak", "reviver", "deified", "repaper", "deed", "peep",
        "noon", "civic", "refer", "tenet", "stats", "solos",
        "rotator", "mom", "dad", "pop", "bob", "wow", "did",
        "eye", "level", "minim", "murdrum", "pip", "tat",
        "toot", "peep", "poop", "ewe", "ere", "eve", "aha",
        "ana", "anna", "otto", "abba", "esse", "exxe",
        "immi", "itti", "otto", "maam", "teet",
    ]

    # Palindrome phrases
    PALINDROME_PHRASES = [
        "A man, a plan, a canal: Panama",
        "No lemon, no melon",
        "Was it a car or a cat I saw?",
        "Madam, in Eden, I'm Adam",
        "Never odd or even",
        "Evil is a name of a foeman, as I live",
        "A Santa lived as a devil at NASA",
        "Dennis sinned",
        "Satan, oscillate my metallic sonatas",
        "Go hang a salami, I'm a lasagna hog",
        "Step on no pets",
        "Yo, banana boy!",
        "Do geese see God?",
        "Rise to vote, sir",
        "Won't lovers revolt now?",
    ]

    def __init__(self) -> None:
        """Initialize the palindrome plugin."""
        metadata = PluginMetadata(
            name="palindrome",
            version="1.0.0",
            description="Palindrome checking, generation, and analysis tools",
            author="LiuHao AI OS Team",
            categories=["text", "word", "utility"],
            keywords=["palindrome", "reverse", "word", "phrase", "string", "puzzle"],
            requires_internet=False,
            dependencies=[],
        )
        super().__init__(metadata)

        self._rng = random.Random()

    # ==================== Lifecycle Methods ====================

    def initialize(self, config: Optional[Dict[str, Any]] = None) -> bool:
        """Initialize the palindrome plugin."""
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
        """Activate the palindrome plugin."""
        try:
            self._set_status(PluginStatus.ACTIVE)
            return True
        except Exception as e:
            self._set_error(f"Activation failed: {e}")
            return False

    def deactivate(self) -> bool:
        """Deactivate the palindrome plugin."""
        try:
            self._set_status(PluginStatus.INACTIVE)
            return True
        except Exception as e:
            self._set_error(str(e))
            return False

    def shutdown(self) -> bool:
        """Shut down the palindrome plugin."""
        try:
            self._set_status(PluginStatus.SHUTDOWN)
            return True
        except Exception as e:
            self._set_error(str(e))
            return False

    def get_capabilities(self) -> List[str]:
        """Return the list of capabilities."""
        return [
            "is_palindrome",
            "generate_palindrome",
            "longest_substring",
            "all_substrings",
            "phrase_palindrome",
            "number_palindrome",
            "can_form_palindrome",
            "make_palindrome",
        ]

    # ==================== Palindrome Checking ====================

    def is_palindrome(
        self,
        text: str,
        case_sensitive: bool = False,
        ignore_spaces: bool = True,
        ignore_punctuation: bool = True,
    ) -> PalindromeResult:
        """Check if text is a palindrome.

        Args:
            text: Text to check.
            case_sensitive: Whether to consider case.
            ignore_spaces: Whether to ignore spaces.
            ignore_punctuation: Whether to ignore punctuation.

        Returns:
            PalindromeResult with check details.
        """
        self._ensure_active()
        self._increment_call_count()

        # Normalize text
        normalized = text

        if not case_sensitive:
            normalized = normalized.lower()

        if ignore_spaces:
            normalized = normalized.replace(' ', '').replace('\t', '').replace('\n', '')

        if ignore_punctuation:
            import string
            normalized = ''.join(
                ch for ch in normalized
                if ch.isalnum()
            )

        # Check palindrome
        is_palin = normalized == normalized[::-1]

        # Find mismatches
        mismatches = []
        n = len(normalized)
        for i in range(n // 2):
            if normalized[i] != normalized[n - 1 - i]:
                mismatches.append({
                    "left_index": i,
                    "right_index": n - 1 - i,
                    "left_char": normalized[i],
                    "right_char": normalized[n - 1 - i],
                })

        return PalindromeResult(
            input=text,
            is_palindrome=is_palin,
            normalized=normalized,
            details={
                "length": len(normalized),
                "original_length": len(text),
                "mismatches": mismatches,
                "mismatch_count": len(mismatches),
                "case_sensitive": case_sensitive,
                "ignore_spaces": ignore_spaces,
                "ignore_punctuation": ignore_punctuation,
            },
        )

    # ==================== Palindrome Generation ====================

    def generate_palindrome(
        self,
        length: int = 8,
        style: str = "word",
    ) -> str:
        """Generate a palindrome.

        Args:
            length: Target length of the palindrome.
            style: Generation style ('word', 'sentence', 'number').

        Returns:
            Generated palindrome string.
        """
        self._ensure_active()
        self._increment_call_count()

        if length < 1:
            raise ValueError("Length must be at least 1")

        if style == "word":
            return self._generate_word_palindrome(length)
        elif style == "sentence":
            return self._generate_sentence_palindrome(length)
        elif style == "number":
            return self._generate_number_palindrome(length)
        else:
            raise ValueError(f"Unknown style: {style}")

    def _generate_word_palindrome(self, length: int) -> str:
        """Generate a word palindrome of given length."""
        # Build first half, mirror it
        half_len = (length + 1) // 2
        vowels = 'aeiou'
        consonants = 'bcdfghjklmnpqrstvwxyz'

        # Start with a consonant/vowel pattern
        first_half = ''
        for i in range(half_len):
            if i % 2 == 0:
                # Alternate starting with consonant
                if i == 0:
                    first_half += self._rng.choice(consonants)
                else:
                    first_half += self._rng.choice(vowels)
            else:
                first_half += self._rng.choice(vowels)

        # Build second half (mirror)
        if length % 2 == 0:
            second_half = first_half[::-1]
        else:
            second_half = first_half[-2::-1]

        return first_half + second_half

    def _generate_sentence_palindrome(self, length: int) -> str:
        """Generate a palindromic sentence/phrase."""
        # Simple approach: generate a phrase and mirror it
        words = [
            "a", "the", "man", "cat", "dog", "sun", "moon",
            "star", "bird", "fish", "tree", "lake", "hill",
            "road", "town", "city", "ship", "boat", "car",
        ]

        # Build first half of words
        half_words = []
        current_len = 0
        target_half = length // 2

        while current_len < target_half - 5:
            word = self._rng.choice(words)
            if current_len + len(word) + 1 <= target_half:
                half_words.append(word)
                current_len += len(word) + 1
            else:
                break

        first_half = ' '.join(half_words)

        # Mirror: reverse words and each word
        mirrored_words = []
        for word in reversed(half_words):
            mirrored_words.append(word[::-1])

        result = first_half + ' ' + ' '.join(mirrored_words)

        # Adjust to approximate length
        if len(result) < length:
            result = result.ljust(length, ' ')
        elif len(result) > length:
            result = result[:length]

        return result.strip()

    def _generate_number_palindrome(self, length: int) -> str:
        """Generate a numeric palindrome."""
        half_len = (length + 1) // 2
        first_half = ''
        for i in range(half_len):
            if i == 0:
                first_half += str(self._rng.randint(1, 9))
            else:
                first_half += str(self._rng.randint(0, 9))

        if length % 2 == 0:
            second_half = first_half[::-1]
        else:
            second_half = first_half[-2::-1]

        return first_half + second_half

    # ==================== Longest Palindromic Substring ====================

    def longest_substring(
        self,
        text: str,
        case_sensitive: bool = False,
    ) -> PalindromeSubstring:
        """Find the longest palindromic substring in text.

        Uses expand-around-center algorithm (O(n^2) time, O(1) space).

        Args:
            text: Text to search.
            case_sensitive: Whether to consider case.

        Returns:
            PalindromeSubstring for the longest palindrome.
        """
        self._ensure_active()
        self._increment_call_count()

        if not text:
            return PalindromeSubstring(
                text="",
                start=0,
                end=0,
                length=0,
                is_odd=True,
            )

        search_text = text if case_sensitive else text.lower()
        n = len(search_text)
        best_start = 0
        best_end = 0
        best_len = 1

        # Check for odd-length palindromes
        for i in range(n):
            left, right = i, i
            while left >= 0 and right < n and search_text[left] == search_text[right]:
                if right - left + 1 > best_len:
                    best_len = right - left + 1
                    best_start = left
                    best_end = right
                left -= 1
                right += 1

            # Check for even-length palindromes
            left, right = i, i + 1
            while left >= 0 and right < n and search_text[left] == search_text[right]:
                if right - left + 1 > best_len:
                    best_len = right - left + 1
                    best_start = left
                    best_end = right
                left -= 1
                right += 1

        return PalindromeSubstring(
            text=text[best_start:best_end + 1],
            start=best_start,
            end=best_end,
            length=best_len,
            is_odd=best_len % 2 == 1,
        )

    def all_substrings(
        self,
        text: str,
        min_length: int = 2,
        case_sensitive: bool = False,
    ) -> List[PalindromeSubstring]:
        """Find all palindromic substrings in text.

        Args:
            text: Text to search.
            min_length: Minimum palindrome length.
            case_sensitive: Whether to consider case.

        Returns:
            List of PalindromeSubstring objects, sorted by length desc.
        """
        self._ensure_active()
        self._increment_call_count()

        search_text = text if case_sensitive else text.lower()
        n = len(search_text)
        substrings: List[PalindromeSubstring] = []
        seen = set()

        # Odd length
        for i in range(n):
            left, right = i, i
            while left >= 0 and right < n and search_text[left] == search_text[right]:
                length = right - left + 1
                if length >= min_length:
                    key = (left, right)
                    if key not in seen:
                        seen.add(key)
                        substrings.append(PalindromeSubstring(
                            text=text[left:right + 1],
                            start=left,
                            end=right,
                            length=length,
                            is_odd=True,
                        ))
                left -= 1
                right += 1

        # Even length
        for i in range(n):
            left, right = i, i + 1
            while left >= 0 and right < n and search_text[left] == search_text[right]:
                length = right - left + 1
                if length >= min_length:
                    key = (left, right)
                    if key not in seen:
                        seen.add(key)
                        substrings.append(PalindromeSubstring(
                            text=text[left:right + 1],
                            start=left,
                            end=right,
                            length=length,
                            is_odd=False,
                        ))
                left -= 1
                right += 1

        # Sort by length descending
        substrings.sort(key=lambda x: (-x.length, x.start))

        return substrings

    # ==================== Phrase Palindromes ====================

    def phrase_palindrome(self, count: int = 5) -> List[str]:
        """Get classic palindrome phrases.

        Args:
            count: Number of phrases to return.

        Returns:
            List of palindrome phrases.
        """
        self._ensure_active()
        self._increment_call_count()

        if count >= len(self.PALINDROME_PHRASES):
            return list(self.PALINDROME_PHRASES)

        return self._rng.sample(self.PALINDROME_PHRASES, count)

    # ==================== Number Palindromes ====================

    def number_palindrome(self, number: int) -> Dict[str, Any]:
        """Check and analyze number palindromes.

        Args:
            number: Number to check.

        Returns:
            Dict with palindrome info.
        """
        self._ensure_active()
        self._increment_call_count()

        num_str = str(abs(number))
        is_palin = num_str == num_str[::-1]

        # Find next palindrome
        next_palin = number + 1
        while str(next_palin) != str(next_palin)[::-1]:
            next_palin += 1

        # Find previous palindrome
        prev_palin = abs(number) - 1
        while prev_palin >= 0 and str(prev_palin) != str(prev_palin)[::-1]:
            prev_palin -= 1

        return {
            "number": number,
            "is_palindrome": is_palin,
            "digits": len(num_str),
            "next_palindrome": next_palin if number >= 0 else -prev_palin,
            "previous_palindrome": prev_palin if number > 0 else -next_palin,
            "reverse": int(num_str[::-1]),
        }

    # ==================== Palindrome Permutation ====================

    def can_form_palindrome(
        self,
        text: str,
        case_sensitive: bool = False,
        ignore_spaces: bool = True,
    ) -> Tuple[bool, Dict[str, Any]]:
        """Check if a string can be rearranged to form a palindrome.

        A string can form a palindrome if at most one character
        has an odd count.

        Args:
            text: Text to check.
            case_sensitive: Whether to consider case.
            ignore_spaces: Whether to ignore spaces.

        Returns:
            Tuple of (can_form, details).
        """
        self._ensure_active()
        self._increment_call_count()

        normalized = text
        if not case_sensitive:
            normalized = normalized.lower()
        if ignore_spaces:
            normalized = normalized.replace(' ', '')

        counter = Counter(normalized)
        odd_count = sum(1 for c in counter.values() if c % 2 != 0)

        can_form = odd_count <= 1

        details = {
            "character_counts": dict(counter),
            "odd_count": odd_count,
            "odd_characters": [c for c, n in counter.items() if n % 2 != 0],
            "total_characters": len(normalized),
            "unique_characters": len(counter),
        }

        return can_form, details

    def make_palindrome(self, text: str) -> str:
        """Create a palindrome from text by appending reversed prefix.

        Finds the shortest palindrome that can be formed by
        adding characters to the front.

        Args:
            text: Input text.

        Returns:
            Shortest palindrome by adding characters to front.
        """
        self._ensure_active()
        self._increment_call_count()

        if not text:
            return ""

        # Find the longest palindromic prefix
        n = len(text)
        longest_prefix = 1

        for i in range(n, 0, -1):
            prefix = text[:i]
            if prefix == prefix[::-1]:
                longest_prefix = i
                break

        # Append reverse of remaining
        suffix = text[longest_prefix:]
        return text + suffix[::-1]

    # ==================== Statistics ====================

    def get_stats(self) -> Dict[str, Any]:
        """Get plugin statistics."""
        return {
            "palindrome_words_count": len(self.PALINDROME_WORDS),
            "palindrome_phrases_count": len(self.PALINDROME_PHRASES),
            "longest_word": max(len(w) for w in self.PALINDROME_WORDS),
            "shortest_word": min(len(w) for w in self.PALINDROME_WORDS),
        }
