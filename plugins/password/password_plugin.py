"""
Password/Secret Management Plugin for LiuHao AI OS

Provides password generation, strength evaluation, secure storage
interface (hashed), and password policy checking.

Capabilities:
- generate_password: Generate a random password
- evaluate_strength: Evaluate password strength
- check_policy: Check password against policy
- hash_password: Hash a password for storage
- verify_password: Verify password against hash
- generate_passphrase: Generate a memorable passphrase
"""

import hashlib
import hmac
import os
import secrets
import string
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
class PasswordStrength:
    """Result of a password strength evaluation."""
    password: str
    score: int  # 0-100
    strength: str  # very_weak, weak, medium, strong, very_strong
    length: int
    has_lowercase: bool
    has_uppercase: bool
    has_digits: bool
    has_symbols: bool
    unique_chars: int
    entropy_bits: float
    issues: List[str]
    suggestions: List[str]


@dataclass
class PasswordPolicy:
    """Password policy configuration."""
    min_length: int = 8
    max_length: int = 128
    require_lowercase: bool = True
    require_uppercase: bool = True
    require_digits: bool = True
    require_symbols: bool = True
    min_unique_chars: int = 5
    forbid_common_passwords: bool = True
    min_score: int = 50


class PasswordPlugin(BasePlugin):
    """Password and secret management plugin.

    Supports password generation with configurable complexity,
    strength evaluation, policy checking, and secure hashing.

    Example:
        plugin = PasswordPlugin()
        plugin.initialize()
        plugin.activate()

        password = plugin.generate_password(length=16)
        strength = plugin.evaluate_strength(password)
        print(strength.score)
    """

    # Common weak passwords (subset)
    COMMON_PASSWORDS = {
        "password", "123456", "12345678", "qwerty", "abc123",
        "monkey", "1234567", "letmein", "trustno1", "dragon",
        "baseball", "iloveyou", "master", "sunshine", "ashley",
        "passw0rd", "shadow", "123123", "654321", "superman",
        "qazwsx", "michael", "football", "welcome", "jesus",
        "ninja", "mustang", "password1", "hello", "charlie",
        "donald", "1234567890", "batman", "access", "flower",
        "admin", "qwerty123", "555555", "login", "welcome1",
    }

    # Word list for passphrase generation
    WORD_LIST = [
        "apple", "banana", "cherry", "dragon", "eagle", "forest",
        "garden", "harbor", "island", "jungle", "kitten", "lemon",
        "mountain", "nebula", "ocean", "panda", "queen", "river",
        "sunset", "tiger", "umbrella", "violet", "willow", "xenon",
        "yellow", "zebra", "anchor", "bridge", "castle", "dolphin",
        "emerald", "falcon", "galaxy", "horizon", "ivory", "jewel",
        "kingdom", "lantern", "meadow", "night", "orchid", "pearl",
        "quartz", "rainbow", "sparrow", "thunder", "unity", "voyage",
        "wanderer", "zenith", "azure", "blossom", "crimson", "dusk",
        "ember", "frost", "glacier", "haven", "indigo", "jade",
    ]

    def __init__(self) -> None:
        """Initialize the password plugin."""
        metadata = PluginMetadata(
            name="password",
            version="1.0.0",
            description="Password generation and security evaluation",
            author="LiuHao AI OS Team",
            categories=["security", "utility", "passwords"],
            keywords=["password", "security", "hash", "generator", "strength"],
            requires_internet=False,
            dependencies=[],
        )
        super().__init__(metadata)

        self._default_policy = PasswordPolicy()

    # ==================== Lifecycle Methods ====================

    def initialize(self, config: Optional[Dict[str, Any]] = None) -> bool:
        """Initialize the password plugin."""
        try:
            if config:
                self._default_policy = PasswordPolicy(**{
                    k: v for k, v in config.items()
                    if k in PasswordPolicy.__dataclass_fields__
                })

            self._set_status(PluginStatus.INITIALIZED)
            return True
        except Exception as e:
            self._set_error(f"Initialization failed: {e}")
            return False

    def activate(self) -> bool:
        """Activate the password plugin."""
        try:
            self._set_status(PluginStatus.ACTIVE)
            return True
        except Exception as e:
            self._set_error(f"Activation failed: {e}")
            return False

    def deactivate(self) -> bool:
        """Deactivate the password plugin."""
        try:
            self._set_status(PluginStatus.INACTIVE)
            return True
        except Exception as e:
            self._set_error(str(e))
            return False

    def shutdown(self) -> bool:
        """Shut down the password plugin."""
        try:
            self._set_status(PluginStatus.SHUTDOWN)
            return True
        except Exception as e:
            self._set_error(str(e))
            return False

    def get_capabilities(self) -> List[str]:
        """Return the list of capabilities."""
        return [
            "generate_password",
            "generate_passphrase",
            "evaluate_strength",
            "check_policy",
            "hash_password",
            "verify_password",
            "generate_pin",
            "get_default_policy",
        ]

    # ==================== Password Generation ====================

    def generate_password(
        self,
        length: int = 16,
        include_lowercase: bool = True,
        include_uppercase: bool = True,
        include_digits: bool = True,
        include_symbols: bool = True,
        custom_symbols: str = "!@#$%^&*()_+-=[]{}|;:,.<>?",
        exclude_ambiguous: bool = False,
    ) -> str:
        """Generate a random password.

        Args:
            length: Password length.
            include_lowercase: Include lowercase letters.
            include_uppercase: Include uppercase letters.
            include_digits: Include digits.
            include_symbols: Include special symbols.
            custom_symbols: Custom symbol set to use.
            exclude_ambiguous: Exclude ambiguous characters (0, O, l, 1, I).

        Returns:
            Generated password string.

        Raises:
            ValueError: If no character types are selected.
        """
        self._ensure_active()
        self._increment_call_count()

        if length < 4:
            raise ValueError("Password length must be at least 4")

        charset = ""
        if include_lowercase:
            chars = string.ascii_lowercase
            if exclude_ambiguous:
                chars = chars.replace("l", "").replace("o", "")
            charset += chars
        if include_uppercase:
            chars = string.ascii_uppercase
            if exclude_ambiguous:
                chars = chars.replace("I", "").replace("O", "")
            charset += chars
        if include_digits:
            digits = string.digits
            if exclude_ambiguous:
                digits = digits.replace("0", "").replace("1", "")
            charset += digits
        if include_symbols:
            charset += custom_symbols

        if not charset:
            raise ValueError("At least one character type must be selected")

        # Ensure at least one of each selected type
        password_chars: List[str] = []

        if include_lowercase:
            lower_chars = string.ascii_lowercase
            if exclude_ambiguous:
                lower_chars = lower_chars.replace("l", "").replace("o", "")
            password_chars.append(secrets.choice(lower_chars))

        if include_uppercase:
            upper_chars = string.ascii_uppercase
            if exclude_ambiguous:
                upper_chars = upper_chars.replace("I", "").replace("O", "")
            password_chars.append(secrets.choice(upper_chars))

        if include_digits:
            digits = string.digits
            if exclude_ambiguous:
                digits = digits.replace("0", "").replace("1", "")
            password_chars.append(secrets.choice(digits))

        if include_symbols:
            password_chars.append(secrets.choice(custom_symbols))

        # Fill remaining length
        remaining = length - len(password_chars)
        for _ in range(remaining):
            password_chars.append(secrets.choice(charset))

        # Shuffle the password
        secrets.SystemRandom().shuffle(password_chars)

        return "".join(password_chars)

    def generate_passphrase(
        self,
        num_words: int = 4,
        separator: str = "-",
        capitalize: bool = True,
        add_number: bool = True,
    ) -> str:
        """Generate a memorable passphrase using word list.

        Args:
            num_words: Number of words in the passphrase.
            separator: Word separator character.
            capitalize: Capitalize each word.
            add_number: Add a random number at the end.

        Returns:
            Generated passphrase string.
        """
        self._ensure_active()
        self._increment_call_count()

        if num_words < 2:
            raise ValueError("Passphrase must have at least 2 words")

        words = []
        for _ in range(num_words):
            word = secrets.choice(self.WORD_LIST)
            if capitalize:
                word = word.capitalize()
            words.append(word)

        passphrase = separator.join(words)

        if add_number:
            number = secrets.randbelow(100)
            passphrase += separator + str(number)

        return passphrase

    def generate_pin(self, length: int = 6) -> str:
        """Generate a numeric PIN.

        Args:
            length: PIN length.

        Returns:
            Numeric PIN string.
        """
        self._ensure_active()
        self._increment_call_count()

        if length < 4:
            raise ValueError("PIN length must be at least 4")

        return "".join(secrets.choice(string.digits) for _ in range(length))

    # ==================== Strength Evaluation ====================

    def evaluate_strength(self, password: str) -> PasswordStrength:
        """Evaluate the strength of a password.

        Args:
            password: The password to evaluate.

        Returns:
            PasswordStrength with score and details.
        """
        self._ensure_active()
        self._increment_call_count()

        if not password:
            return PasswordStrength(
                password="",
                score=0,
                strength="very_weak",
                length=0,
                has_lowercase=False,
                has_uppercase=False,
                has_digits=False,
                has_symbols=False,
                unique_chars=0,
                entropy_bits=0.0,
                issues=["Password is empty"],
                suggestions=["Enter a password to evaluate"],
            )

        length = len(password)
        has_lower = bool(re.search(r"[a-z]", password))
        has_upper = bool(re.search(r"[A-Z]", password))
        has_digits = bool(re.search(r"\d", password))
        has_symbols = bool(re.search(r"[^a-zA-Z0-9]", password))
        unique_chars = len(set(password))

        # Calculate entropy
        charset_size = 0
        if has_lower:
            charset_size += 26
        if has_upper:
            charset_size += 26
        if has_digits:
            charset_size += 10
        if has_symbols:
            charset_size += 32

        entropy = length * (charset_size.bit_length() - 1) if charset_size > 0 else 0

        # Score calculation
        score = 0
        issues: List[str] = []
        suggestions: List[str] = []

        # Length score
        if length >= 16:
            score += 30
        elif length >= 12:
            score += 25
        elif length >= 10:
            score += 20
        elif length >= 8:
            score += 15
        elif length >= 6:
            score += 8
        else:
            score += 2
            issues.append("Password is too short")
            suggestions.append("Use at least 8 characters, preferably 12+")

        # Character type score
        type_count = sum([has_lower, has_upper, has_digits, has_symbols])
        score += type_count * 10

        if not has_lower:
            issues.append("No lowercase letters")
            suggestions.append("Add lowercase letters")
        if not has_upper:
            issues.append("No uppercase letters")
            suggestions.append("Add uppercase letters")
        if not has_digits:
            issues.append("No digits")
            suggestions.append("Add numbers")
        if not has_symbols:
            issues.append("No special symbols")
            suggestions.append("Add special characters like !@#$%")

        # Uniqueness score
        uniqueness_ratio = unique_chars / length
        if uniqueness_ratio >= 0.8:
            score += 15
        elif uniqueness_ratio >= 0.6:
            score += 10
        elif uniqueness_ratio >= 0.4:
            score += 5
        else:
            score += 0
            issues.append("Too many repeated characters")
            suggestions.append("Use more unique characters")

        # Common password check
        if password.lower() in self.COMMON_PASSWORDS:
            score = max(0, score - 40)
            issues.append("Password is in common password list")
            suggestions.append("Avoid common passwords like 'password' or '123456'")

        # Pattern detection
        if re.search(r"(.)\1{3,}", password):
            score -= 5
            issues.append("Contains repeated character sequences")
            suggestions.append("Avoid repeated characters like 'aaaa'")

        if re.search(r"0123|1234|2345|3456|4567|5678|6789|abcd|qwer|asdf|zxcv", password.lower()):
            score -= 5
            issues.append("Contains common keyboard patterns")
            suggestions.append("Avoid keyboard patterns like 'qwerty' or '1234'")

        # Cap score at 100, floor at 0
        score = max(0, min(100, score))

        # Determine strength label
        if score >= 80:
            strength = "very_strong"
        elif score >= 60:
            strength = "strong"
        elif score >= 40:
            strength = "medium"
        elif score >= 20:
            strength = "weak"
        else:
            strength = "very_weak"

        return PasswordStrength(
            password=password,
            score=score,
            strength=strength,
            length=length,
            has_lowercase=has_lower,
            has_uppercase=has_upper,
            has_digits=has_digits,
            has_symbols=has_symbols,
            unique_chars=unique_chars,
            entropy_bits=round(entropy, 1),
            issues=issues,
            suggestions=suggestions,
        )

    # ==================== Policy Checking ====================

    def check_policy(
        self,
        password: str,
        policy: Optional[PasswordPolicy] = None,
    ) -> Tuple[bool, List[str]]:
        """Check if a password meets the policy requirements.

        Args:
            password: The password to check.
            policy: Password policy. Uses default if None.

        Returns:
            Tuple of (is_valid, list_of_failures).
        """
        self._ensure_active()
        self._increment_call_count()

        policy = policy or self._default_policy
        failures: List[str] = []

        if len(password) < policy.min_length:
            failures.append(
                f"Password too short: {len(password)} < {policy.min_length}"
            )

        if len(password) > policy.max_length:
            failures.append(
                f"Password too long: {len(password)} > {policy.max_length}"
            )

        if policy.require_lowercase and not re.search(r"[a-z]", password):
            failures.append("Must contain at least one lowercase letter")

        if policy.require_uppercase and not re.search(r"[A-Z]", password):
            failures.append("Must contain at least one uppercase letter")

        if policy.require_digits and not re.search(r"\d", password):
            failures.append("Must contain at least one digit")

        if policy.require_symbols and not re.search(r"[^a-zA-Z0-9]", password):
            failures.append("Must contain at least one special symbol")

        if policy.min_unique_chars > 0:
            unique = len(set(password))
            if unique < policy.min_unique_chars:
                failures.append(
                    f"Must have at least {policy.min_unique_chars} unique characters"
                    f" (has {unique})"
                )

        if policy.forbid_common_passwords:
            if password.lower() in self.COMMON_PASSWORDS:
                failures.append("Password is too common")

        return len(failures) == 0, failures

    def get_default_policy(self) -> Dict[str, Any]:
        """Get the default password policy."""
        return {
            "min_length": self._default_policy.min_length,
            "max_length": self._default_policy.max_length,
            "require_lowercase": self._default_policy.require_lowercase,
            "require_uppercase": self._default_policy.require_uppercase,
            "require_digits": self._default_policy.require_digits,
            "require_symbols": self._default_policy.require_symbols,
            "min_unique_chars": self._default_policy.min_unique_chars,
            "forbid_common_passwords": self._default_policy.forbid_common_passwords,
            "min_score": self._default_policy.min_score,
        }

    # ==================== Hashing & Verification ====================

    def hash_password(
        self,
        password: str,
        algorithm: str = "sha256",
        salt: Optional[str] = None,
        iterations: int = 100000,
    ) -> Dict[str, str]:
        """Hash a password for secure storage.

        Uses PBKDF2 with the specified algorithm.

        Args:
            password: The password to hash.
            algorithm: Hash algorithm (sha256, sha512).
            salt: Optional salt. Generated if not provided.
            iterations: Number of PBKDF2 iterations.

        Returns:
            Dict with 'hash', 'salt', 'algorithm', 'iterations'.
        """
        self._ensure_active()
        self._increment_call_count()

        if salt is None:
            salt = secrets.token_hex(16)

        salt_bytes = salt.encode("utf-8")
        password_bytes = password.encode("utf-8")

        dk = hashlib.pbkdf2_hmac(
            algorithm,
            password_bytes,
            salt_bytes,
            iterations,
        )

        return {
            "hash": dk.hex(),
            "salt": salt,
            "algorithm": algorithm,
            "iterations": str(iterations),
        }

    def verify_password(
        self,
        password: str,
        stored_hash: str,
        salt: str,
        algorithm: str = "sha256",
        iterations: int = 100000,
    ) -> bool:
        """Verify a password against a stored hash.

        Args:
            password: The password to verify.
            stored_hash: The stored hash (hex string).
            salt: The salt used for hashing.
            algorithm: Hash algorithm used.
            iterations: Number of PBKDF2 iterations.

        Returns:
            True if password matches.
        """
        self._ensure_active()
        self._increment_call_count()

        salt_bytes = salt.encode("utf-8")
        password_bytes = password.encode("utf-8")

        dk = hashlib.pbkdf2_hmac(
            algorithm,
            password_bytes,
            salt_bytes,
            iterations,
        )

        return hmac.compare_digest(dk.hex(), stored_hash)

    # ==================== Stats ====================

    def get_stats(self) -> Dict[str, Any]:
        """Get plugin statistics."""
        return {
            "common_passwords_count": len(self.COMMON_PASSWORDS),
            "passphrase_words": len(self.WORD_LIST),
            "default_policy": self.get_default_policy(),
        }
