"""
Vigenere Cipher Plugin for LiuHao AI OS

Provides Vigenere cipher encryption, decryption, key generation,
Kasiski examination interface, frequency analysis, and autokey variant.

Capabilities:
- encrypt: Encrypt text with Vigenere cipher
- decrypt: Decrypt text with Vigenere cipher
- generate_key: Generate a random key
- autokey_encrypt: Encrypt using autokey variant
- autokey_decrypt: Decrypt using autokey variant
- frequency_analysis: Frequency analysis per position
- kasiski_interface: Kasiski examination parameters
"""

import secrets
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
class VigenereResult:
    """Result of a Vigenere cipher operation."""
    input: str
    output: str
    key: str
    operation: str  # encrypt, decrypt, autokey_encrypt, autokey_decrypt


@dataclass
class FrequencyAnalysis:
    """Frequency analysis result for Vigenere cracking."""
    key_length: int
    positions: List[Dict[str, Any]]  # Per-position frequency analysis
    probable_key: str
    confidence: float


class VigenerePlugin(BasePlugin):
    """Vigenere cipher encryption, decryption, and cryptanalysis.

    Supports classic Vigenere cipher with keyword key, autokey variant,
    key generation, and frequency analysis for key-length determination.

    Example:
        plugin = VigenerePlugin()
        plugin.initialize()
        plugin.activate()

        encrypted = plugin.encrypt("Attack at dawn", "LEMON")
        print(encrypted.output)  # "Lxfopv ef rnhr"
    """

    ENGLISH_FREQ: Dict[str, float] = {
        'E': 12.70, 'T': 9.06, 'A': 8.17, 'O': 7.51, 'I': 6.97,
        'N': 6.75, 'S': 6.33, 'H': 6.09, 'R': 5.99, 'D': 4.25,
        'L': 4.03, 'C': 2.78, 'U': 2.76, 'M': 2.41, 'W': 2.36,
        'F': 2.23, 'G': 2.02, 'Y': 1.97, 'P': 1.93, 'B': 1.49,
        'V': 0.98, 'K': 0.77, 'J': 0.15, 'X': 0.15, 'Q': 0.10,
        'Z': 0.07,
    }

    def __init__(self) -> None:
        """Initialize the Vigenere cipher plugin."""
        metadata = PluginMetadata(
            name="vigenere",
            version="1.0.0",
            description="Vigenere cipher encryption, decryption, key generation, and frequency analysis",
            author="LiuHao AI OS Team",
            categories=["cryptography", "cipher", "utility"],
            keywords=["vigenere", "cipher", "polyalphabetic", "encryption", "decryption", "crypto"],
            requires_internet=False,
            dependencies=[],
        )
        super().__init__(metadata)

    # ==================== Lifecycle Methods ====================

    def initialize(self, config: Optional[Dict[str, Any]] = None) -> bool:
        """Initialize the Vigenere cipher plugin."""
        try:
            self._set_status(PluginStatus.INITIALIZED)
            return True
        except Exception as e:
            self._set_error(f"Initialization failed: {e}")
            return False

    def activate(self) -> bool:
        """Activate the Vigenere cipher plugin."""
        try:
            self._set_status(PluginStatus.ACTIVE)
            return True
        except Exception as e:
            self._set_error(f"Activation failed: {e}")
            return False

    def deactivate(self) -> bool:
        """Deactivate the Vigenere cipher plugin."""
        try:
            self._set_status(PluginStatus.INACTIVE)
            return True
        except Exception as e:
            self._set_error(str(e))
            return False

    def shutdown(self) -> bool:
        """Shut down the Vigenere cipher plugin."""
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
            "generate_key",
            "autokey_encrypt",
            "autokey_decrypt",
            "frequency_analysis",
            "kasiski_examination",
            "index_of_coincidence",
        ]

    # ==================== Encryption / Decryption ====================

    def encrypt(self, plaintext: str, key: str) -> VigenereResult:
        """Encrypt text using Vigenere cipher.

        Args:
            plaintext: The text to encrypt.
            key: The encryption key (letters only).

        Returns:
            VigenereResult with encrypted text.

        Raises:
            ValueError: If key is empty or contains non-letter characters.
        """
        self._ensure_active()
        self._increment_call_count()

        key = self._validate_key(key)
        result = self._vigenere_transform(plaintext, key, encrypt=True)

        return VigenereResult(
            input=plaintext,
            output=result,
            key=key,
            operation="encrypt",
        )

    def decrypt(self, ciphertext: str, key: str) -> VigenereResult:
        """Decrypt text using Vigenere cipher.

        Args:
            ciphertext: The text to decrypt.
            key: The decryption key (letters only).

        Returns:
            VigenereResult with decrypted text.

        Raises:
            ValueError: If key is empty or contains non-letter characters.
        """
        self._ensure_active()
        self._increment_call_count()

        key = self._validate_key(key)
        result = self._vigenere_transform(ciphertext, key, encrypt=False)

        return VigenereResult(
            input=ciphertext,
            output=result,
            key=key,
            operation="decrypt",
        )

    def _vigenere_transform(self, text: str, key: str, encrypt: bool) -> str:
        """Apply Vigenere cipher transformation."""
        result: List[str] = []
        key_idx = 0
        key_len = len(key)

        for ch in text:
            if ch.isalpha():
                base = ord('A') if ch.isupper() else ord('a')
                key_char = key[key_idx % key_len].upper()
                shift = ord(key_char) - ord('A')

                if not encrypt:
                    shift = -shift

                shifted = (ord(ch.upper()) - ord('A') + shift) % 26
                result_char = chr(base + shifted)
                if ch.islower():
                    result_char = result_char.lower()
                result.append(result_char)
                key_idx += 1
            else:
                result.append(ch)

        return ''.join(result)

    def _validate_key(self, key: str) -> str:
        """Validate and normalize the key."""
        key_letters = ''.join(c for c in key if c.isalpha())
        if not key_letters:
            raise ValueError("Key must contain at least one letter")
        return key_letters.upper()

    # ==================== Autokey Variant ====================

    def autokey_encrypt(self, plaintext: str, primer: str) -> VigenereResult:
        """Encrypt using autokey Vigenere variant.

        In autokey cipher, the key starts with a primer word, then
        uses the plaintext itself as the remaining key.

        Args:
            plaintext: The text to encrypt.
            primer: The primer key (prepended to plaintext for key stream).

        Returns:
            VigenereResult with encrypted text.
        """
        self._ensure_active()
        self._increment_call_count()

        primer = self._validate_key(primer)

        # Build full key: primer + plaintext letters
        plain_letters = ''.join(c.upper() for c in plaintext if c.isalpha())
        full_key = primer + plain_letters

        result = self._vigenere_transform(plaintext, full_key, encrypt=True)

        return VigenereResult(
            input=plaintext,
            output=result,
            key=primer,
            operation="autokey_encrypt",
        )

    def autokey_decrypt(self, ciphertext: str, primer: str) -> VigenereResult:
        """Decrypt using autokey Vigenere variant.

        Args:
            ciphertext: The text to decrypt.
            primer: The primer key.

        Returns:
            VigenereResult with decrypted text.
        """
        self._ensure_active()
        self._increment_call_count()

        primer = self._validate_key(primer)

        # Decrypt character by character, building key as we go
        result: List[str] = []
        key_stream = list(primer)
        key_idx = 0

        for ch in ciphertext:
            if ch.isalpha():
                base = ord('A') if ch.isupper() else ord('a')
                key_char = key_stream[key_idx].upper()
                shift = ord(key_char) - ord('A')

                shifted = (ord(ch.upper()) - ord('A') - shift) % 26
                plain_char = chr(ord('A') + shifted)

                # Add plaintext char to key stream
                key_stream.append(plain_char)

                result_char = plain_char if ch.isupper() else plain_char.lower()
                result.append(result_char)
                key_idx += 1
            else:
                result.append(ch)

        return VigenereResult(
            input=ciphertext,
            output=''.join(result),
            key=primer,
            operation="autokey_decrypt",
        )

    # ==================== Key Generation ====================

    def generate_key(self, length: int = 8) -> str:
        """Generate a random key of specified length.

        Args:
            length: Length of the key to generate.

        Returns:
            Random uppercase key string.
        """
        self._ensure_active()
        self._increment_call_count()

        if length < 1:
            raise ValueError("Key length must be at least 1")

        return ''.join(secrets.choice(string.ascii_uppercase) for _ in range(length))

    # ==================== Frequency Analysis ====================

    def frequency_analysis(self, ciphertext: str, key_length: int) -> FrequencyAnalysis:
        """Perform frequency analysis for each key position.

        Groups ciphertext letters by position modulo key_length and
        performs frequency analysis on each group to help determine
        the key used.

        Args:
            ciphertext: The encrypted text.
            key_length: Assumed key length.

        Returns:
            FrequencyAnalysis with per-position analysis and probable key.
        """
        self._ensure_active()
        self._increment_call_count()

        letters = [c.upper() for c in ciphertext if c.isalpha()]
        positions: List[Dict[str, Any]] = []
        probable_key_chars: List[str] = []
        total_confidence = 0.0

        for pos in range(key_length):
            # Get letters at this position
            pos_letters = letters[pos::key_length]
            total = len(pos_letters)

            if total == 0:
                positions.append({
                    "position": pos,
                    "total": 0,
                    "frequencies": {},
                    "ioc": 0.0,
                    "best_shift": 0,
                    "best_char": '?',
                    "shift_score": 0.0,
                })
                probable_key_chars.append('?')
                continue

            counter = Counter(pos_letters)
            freqs = {}
            for letter in string.ascii_uppercase:
                freqs[letter] = round(counter.get(letter, 0) / total * 100, 2)

            # Index of coincidence
            ioc = (
                sum(c * (c - 1) for c in counter.values())
                / (total * (total - 1))
                if total > 1 else 0.0
            )

            # Find best shift using chi-squared
            best_shift = 0
            best_chi = float('inf')

            for shift in range(26):
                chi = 0.0
                for letter in string.ascii_uppercase:
                    shifted_letter = chr(
                        (ord(letter) - ord('A') + shift) % 26 + ord('A')
                    )
                    observed = counter.get(shifted_letter, 0)
                    expected = total * self.ENGLISH_FREQ[letter] / 100.0
                    if expected > 0:
                        chi += (observed - expected) ** 2 / expected

                if chi < best_chi:
                    best_chi = chi
                    best_shift = shift

            best_char = chr(ord('A') + best_shift)
            probable_key_chars.append(best_char)

            # Confidence based on IOC (English ~0.0667, random ~0.0385)
            pos_confidence = min(1.0, max(0.0, (ioc - 0.0385) / 0.0282))
            total_confidence += pos_confidence

            positions.append({
                "position": pos,
                "total": total,
                "frequencies": freqs,
                "ioc": round(ioc, 6),
                "best_shift": best_shift,
                "best_char": best_char,
                "chi_squared": round(best_chi, 4),
                "confidence": round(pos_confidence, 4),
            })

        avg_confidence = total_confidence / key_length if key_length > 0 else 0.0

        return FrequencyAnalysis(
            key_length=key_length,
            positions=positions,
            probable_key=''.join(probable_key_chars),
            confidence=round(avg_confidence, 4),
        )

    # ==================== Index of Coincidence ====================

    def index_of_coincidence(self, text: str) -> Dict[str, Any]:
        """Calculate Index of Coincidence (IC) and estimate key length.

        Args:
            text: The text to analyze.

        Returns:
            Dict with IC value and key length estimates.
        """
        self._ensure_active()
        self._increment_call_count()

        letters = [c.upper() for c in text if c.isalpha()]
        total = len(letters)

        if total < 2:
            return {
                "total_letters": total,
                "ic": 0.0,
                "key_length_estimate": 0,
                "ic_by_period": {},
            }

        counter = Counter(letters)
        ic = sum(c * (c - 1) for c in counter.values()) / (total * (total - 1))

        # Estimate key length using IC for different periods
        ic_by_period: Dict[int, float] = {}
        best_period = 1
        best_ic_diff = float('inf')

        for period in range(1, min(21, total // 4)):
            ics = []
            for pos in range(period):
                pos_letters = letters[pos::period]
                pos_total = len(pos_letters)
                if pos_total > 1:
                    pos_counter = Counter(pos_letters)
                    pos_ic = sum(
                        c * (c - 1) for c in pos_counter.values()
                    ) / (pos_total * (pos_total - 1))
                    ics.append(pos_ic)

            if ics:
                avg_ic = sum(ics) / len(ics)
                ic_by_period[period] = round(avg_ic, 6)
                # Closer to English IC (0.0667) = better
                diff = abs(avg_ic - 0.0667)
                if diff < best_ic_diff:
                    best_ic_diff = diff
                    best_period = period

        return {
            "total_letters": total,
            "ic": round(ic, 6),
            "english_ic": 0.0667,
            "random_ic": 0.0385,
            "key_length_estimate": best_period,
            "ic_by_period": ic_by_period,
        }

    # ==================== Kasiski Examination ====================

    def kasiski_examination(self, ciphertext: str) -> Dict[str, Any]:
        """Perform Kasiski examination to estimate key length.

        Finds repeated sequences and calculates the GCD of their
        distances to estimate the key length.

        Args:
            ciphertext: The encrypted text.

        Returns:
            Dict with repeated sequences and key length estimates.
        """
        self._ensure_active()
        self._increment_call_count()

        letters = [c.upper() for c in ciphertext if c.isalpha()]
        text = ''.join(letters)

        if len(text) < 10:
            return {
                "total_letters": len(text),
                "repeated_sequences": [],
                "key_length_candidates": [],
                "best_estimate": 0,
            }

        # Find repeated sequences of length 3+
        sequences: Dict[str, List[int]] = {}

        for seq_len in range(3, 7):
            for i in range(len(text) - seq_len + 1):
                seq = text[i:i + seq_len]
                if seq not in sequences:
                    sequences[seq] = []
                sequences[seq].append(i)

        # Filter to sequences that appear multiple times
        repeated = {
            seq: positions for seq, positions in sequences.items()
            if len(positions) >= 2
        }

        # Calculate distances between occurrences
        from math import gcd
        from functools import reduce

        all_distances: List[int] = []
        sequence_details: List[Dict[str, Any]] = []

        for seq, positions in sorted(
            repeated.items(),
            key=lambda x: -len(x[1])
        )[:20]:
            distances = [
                positions[i + 1] - positions[i]
                for i in range(len(positions) - 1)
            ]
            all_distances.extend(distances)

            seq_gcd = reduce(gcd, distances) if distances else 0
            sequence_details.append({
                "sequence": seq,
                "count": len(positions),
                "positions": positions,
                "distances": distances,
                "gcd": seq_gcd,
            })

        # Find common divisors
        divisor_counts: Dict[int, int] = {}
        for d in all_distances:
            for i in range(2, min(d + 1, 21)):
                if d % i == 0:
                    divisor_counts[i] = divisor_counts.get(i, 0) + 1

        candidates = sorted(
            divisor_counts.items(),
            key=lambda x: -x[1]
        )[:10]

        best_estimate = candidates[0][0] if candidates else 0

        return {
            "total_letters": len(text),
            "repeated_sequences": sequence_details[:10],
            "key_length_candidates": [
                {"length": l, "score": s} for l, s in candidates
            ],
            "best_estimate": best_estimate,
        }

    # ==================== Statistics ====================

    def get_stats(self) -> Dict[str, Any]:
        """Get plugin statistics."""
        return {
            "alphabet_size": 26,
            "english_freq_table_size": len(self.ENGLISH_FREQ),
            "max_key_length_analysis": 20,
            "supports_autokey": True,
        }
