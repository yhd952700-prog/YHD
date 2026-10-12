"""
Caesar Cipher Plugin for LiuHao AI OS

Provides Caesar cipher encryption, decryption, brute force cracking,
ROT13, custom shifts, and frequency analysis.

Capabilities:
- encrypt: Encrypt text with Caesar cipher
- decrypt: Decrypt text with Caesar cipher
- brute_force: Try all 25 possible shifts
- rot13: ROT13 transformation
- frequency_analysis: Letter frequency analysis
- crack: Auto-crack using frequency analysis
"""

import string
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
class CaesarResult:
    """Result of a Caesar cipher operation."""
    input: str
    output: str
    shift: int
    operation: str  # encrypt, decrypt, rot13


@dataclass
class BruteForceResult:
    """Result of brute force decryption."""
    ciphertext: str
    results: List[Tuple[int, str]]  # (shift, plaintext)
    best_guess: int
    best_score: float


class CaesarCipherPlugin(BasePlugin):
    """Caesar cipher encryption, decryption, and cryptanalysis.

    Supports classic Caesar cipher with configurable shift, ROT13,
    brute force cracking, and frequency analysis for automatic
    decryption without key.

    Example:
        plugin = CaesarCipherPlugin()
        plugin.initialize()
        plugin.activate()

        encrypted = plugin.encrypt("Hello World", 3)
        print(encrypted.output)  # "Khoor Zruog"

        result = plugin.brute_force("Khoor Zruog")
        print(result.best_guess)  # 3
    """

    # Standard English letter frequencies
    ENGLISH_FREQ: Dict[str, float] = {
        'E': 12.70, 'T': 9.06, 'A': 8.17, 'O': 7.51, 'I': 6.97,
        'N': 6.75, 'S': 6.33, 'H': 6.09, 'R': 5.99, 'D': 4.25,
        'L': 4.03, 'C': 2.78, 'U': 2.76, 'M': 2.41, 'W': 2.36,
        'F': 2.23, 'G': 2.02, 'Y': 1.97, 'P': 1.93, 'B': 1.49,
        'V': 0.98, 'K': 0.77, 'J': 0.15, 'X': 0.15, 'Q': 0.10,
        'Z': 0.07,
    }

    def __init__(self) -> None:
        """Initialize the Caesar cipher plugin."""
        metadata = PluginMetadata(
            name="caesar-cipher",
            version="1.0.0",
            description="Caesar cipher encryption, decryption, brute force, and frequency analysis",
            author="LiuHao AI OS Team",
            categories=["cryptography", "cipher", "utility"],
            keywords=["caesar", "cipher", "rot13", "encryption", "decryption", "crypto"],
            requires_internet=False,
            dependencies=[],
        )
        super().__init__(metadata)

    # ==================== Lifecycle Methods ====================

    def initialize(self, config: Optional[Dict[str, Any]] = None) -> bool:
        """Initialize the Caesar cipher plugin."""
        try:
            self._set_status(PluginStatus.INITIALIZED)
            return True
        except Exception as e:
            self._set_error(f"Initialization failed: {e}")
            return False

    def activate(self) -> bool:
        """Activate the Caesar cipher plugin."""
        try:
            self._set_status(PluginStatus.ACTIVE)
            return True
        except Exception as e:
            self._set_error(f"Activation failed: {e}")
            return False

    def deactivate(self) -> bool:
        """Deactivate the Caesar cipher plugin."""
        try:
            self._set_status(PluginStatus.INACTIVE)
            return True
        except Exception as e:
            self._set_error(str(e))
            return False

    def shutdown(self) -> bool:
        """Shut down the Caesar cipher plugin."""
        try:
            self._set_status(PluginStatus.SHUTDOWN)
            return True
        except Exception as e:
            self._set_error(str(e))
            return False

    def get_capabilities(self) -> List[str]:
        """Return the list of capabilities."""
        return [
            "encrypt",
            "decrypt",
            "brute_force",
            "rot13",
            "frequency_analysis",
            "crack",
            "custom_shift",
        ]

    # ==================== Encryption / Decryption ====================

    def encrypt(self, plaintext: str, shift: int = 3) -> CaesarResult:
        """Encrypt text using Caesar cipher.

        Args:
            plaintext: The text to encrypt.
            shift: Number of positions to shift (0-25).

        Returns:
            CaesarResult with encrypted text.
        """
        self._ensure_active()
        self._increment_call_count()

        shift = shift % 26
        result = self._shift_text(plaintext, shift)

        return CaesarResult(
            input=plaintext,
            output=result,
            shift=shift,
            operation="encrypt",
        )

    def decrypt(self, ciphertext: str, shift: int = 3) -> CaesarResult:
        """Decrypt text using Caesar cipher.

        Args:
            ciphertext: The text to decrypt.
            shift: Number of positions that were shifted (0-25).

        Returns:
            CaesarResult with decrypted text.
        """
        self._ensure_active()
        self._increment_call_count()

        shift = shift % 26
        result = self._shift_text(ciphertext, -shift)

        return CaesarResult(
            input=ciphertext,
            output=result,
            shift=shift,
            operation="decrypt",
        )

    def rot13(self, text: str) -> CaesarResult:
        """Apply ROT13 transformation (Caesar cipher with shift 13).

        ROT13 is its own inverse since 26 / 2 = 13.

        Args:
            text: The text to transform.

        Returns:
            CaesarResult with ROT13'd text.
        """
        self._ensure_active()
        self._increment_call_count()

        result = self._shift_text(text, 13)

        return CaesarResult(
            input=text,
            output=result,
            shift=13,
            operation="rot13",
        )

    def _shift_text(self, text: str, shift: int) -> str:
        """Shift all letters in text by the given amount."""
        shift = shift % 26
        result: List[str] = []

        for ch in text:
            if ch.isalpha():
                base = ord('A') if ch.isupper() else ord('a')
                shifted = (ord(ch) - base + shift) % 26
                result.append(chr(base + shifted))
            else:
                result.append(ch)

        return ''.join(result)

    # ==================== Brute Force ====================

    def brute_force(self, ciphertext: str) -> BruteForceResult:
        """Try all 25 possible shifts and rank them by English frequency.

        Args:
            ciphertext: The encrypted text to crack.

        Returns:
            BruteForceResult with all 26 results and best guess.
        """
        self._ensure_active()
        self._increment_call_count()

        results: List[Tuple[int, str]] = []
        best_shift = 0
        best_score = -1.0

        for shift in range(26):
            plaintext = self._shift_text(ciphertext, -shift)
            score = self._english_score(plaintext)
            results.append((shift, plaintext))

            if score > best_score:
                best_score = score
                best_shift = shift

        return BruteForceResult(
            ciphertext=ciphertext,
            results=results,
            best_guess=best_shift,
            best_score=round(best_score, 4),
        )

    def crack(self, ciphertext: str) -> Dict[str, Any]:
        """Auto-crack Caesar cipher using frequency analysis.

        Args:
            ciphertext: The encrypted text to crack.

        Returns:
            Dict with 'shift', 'plaintext', 'confidence'.
        """
        self._ensure_active()
        self._increment_call_count()

        bf_result = self.brute_force(ciphertext)

        # Calculate confidence based on score differentiation
        scores = [self._english_score(pt) for _, pt in bf_result.results]
        scores.sort(reverse=True)
        if scores[0] > 0:
            confidence = min(1.0, (scores[0] - scores[1]) / scores[0]) if scores[1] > 0 else 0.9
        else:
            confidence = 0.0

        return {
            "shift": bf_result.best_guess,
            "plaintext": bf_result.results[bf_result.best_guess][1],
            "confidence": round(confidence, 4),
            "score": bf_result.best_score,
        }

    # ==================== Frequency Analysis ====================

    def frequency_analysis(self, text: str) -> Dict[str, Any]:
        """Perform letter frequency analysis on text.

        Args:
            text: The text to analyze.

        Returns:
            Dict with frequency table, sorted by frequency desc.
        """
        self._ensure_active()
        self._increment_call_count()

        letters = [c.upper() for c in text if c.isalpha()]
        total = len(letters)

        if total == 0:
            return {
                "total_letters": 0,
                "frequencies": {},
                "index_of_coincidence": 0.0,
                "most_common": [],
            }

        counter = Counter(letters)
        frequencies: Dict[str, float] = {}
        for letter in string.ascii_uppercase:
            count = counter.get(letter, 0)
            frequencies[letter] = round(count / total * 100, 2)

        # Index of Coincidence
        ioc = sum(c * (c - 1) for c in counter.values()) / (total * (total - 1)) if total > 1 else 0

        most_common = counter.most_common(10)

        return {
            "total_letters": total,
            "frequencies": frequencies,
            "index_of_coincidence": round(ioc, 6),
            "most_common": [(ch, cnt, round(cnt / total * 100, 2)) for ch, cnt in most_common],
            "english_ioc": 0.0667,
            "random_ioc": 0.0385,
        }

    # ==================== English Scoring ====================

    def _english_score(self, text: str) -> float:
        """Score how likely text is to be English.

        Uses chi-squared test on letter frequencies.
        Lower score = more likely to be English.
        Returns higher score for better matches (inverted chi-squared).
        """
        letters = [c.upper() for c in text if c.isalpha()]
        total = len(letters)

        if total == 0:
            return 0.0

        counter = Counter(letters)
        chi_squared = 0.0

        for letter in string.ascii_uppercase:
            observed = counter.get(letter, 0)
            expected = total * self.ENGLISH_FREQ[letter] / 100.0
            if expected > 0:
                chi_squared += (observed - expected) ** 2 / expected

        # Convert to a score where higher = more English-like
        # Use inverse with some normalization
        score = 100.0 / (1.0 + chi_squared / total) if total > 0 else 0.0

        return score

    # ==================== Statistics ====================

    def get_stats(self) -> Dict[str, Any]:
        """Get plugin statistics."""
        return {
            "alphabet_size": 26,
            "possible_shifts": 25,
            "english_freq_table_size": len(self.ENGLISH_FREQ),
            "default_shift": 3,
        }
