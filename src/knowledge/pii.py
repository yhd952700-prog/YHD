"""Rule-based PII (Personally Identifiable Information) detector for the knowledge pipeline.

Detects common PII types including emails, phone numbers, and general identity/address markers
without relying on third-party services. Output masking uses a canonical [REDACTED] value.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import re
from typing import Dict, List, Optional, Tuple


class PIIType(str, Enum):
    """Enum of PII types that can be detected."""
    EMAIL = "email"
    PHONE = "phone"
    ADDRESS = "address"
    NAME = "name"
    CREDIT_CARD = "credit_card"


@dataclass
class PIIMatch:
    """Represents a single PII match result."""
    pii_type: PIIType
    detected: bool
    matches: List[str]  # List of matched substrings
    positions: List[Tuple[int, int]]  # List of (start, end) position tuples in original text


class PIIResult:
    """Container for PII detection results."""

    def __init__(self, detected: bool, pii_types: List[PIIType], matches: Dict[PIIType, PIIMatch], redacted_text: str):
        self.detected = detected
        self.pii_types = pii_types
        self.matches = matches
        self.redacted_text = redacted_text


class KnowledgeSecurityPolicy:
    """Security policy for knowledge base operations.

    Defines access control, PII handling, and redaction rules for knowledge base interactions.
    """

    def __init__(self,
                 allowed_operations: Optional[List[str]] = None,
                 redact_pii: bool = True,
                 allowed_users: Optional[List[str]] = None,
                 ) -> None:
        self.allowed_operations = allowed_operations or ["read", "search", "query"]
        self.redact_pii = redact_pii
        self.allowed_users = allowed_users or []

    def check_operation(self, operation: str, user: str) -> bool:
        """Check if a user is allowed to perform an operation."""
        if operation not in self.allowed_operations:
            return False
        if self.allowed_users and user not in self.allowed_users:
            return False
        return True

    def redact_text(self, text: str) -> str:
        """Redact PII from text using canonical [REDACTED] placeholder."""
        if not self.redact_pii:
            return text

        result = self.detect_pii(text)
        redacted = result.redacted_text
        return redacted

    def detect_pii(self, text: str) -> PIIResult:
        """Detect PII in the given text and return a PIIResult."""
        matches: Dict[PIIType, PIIMatch] = {}
        all_pii_types: List[PIIType] = []

        # Detect emails
        email_matches = self._detect_email(text)
        if email_matches.matches:
            matches[PIIType.EMAIL] = email_matches
            all_pii_types.append(PIIType.EMAIL)

        # Detect phone numbers
        phone_matches = self._detect_phone(text)
        if phone_matches.matches:
            matches[PIIType.PHONE] = phone_matches
            all_pii_types.append(PIIType.PHONE)

        # Detect addresses (simple pattern)
        address_matches = self._detect_address(text)
        if address_matches.matches:
            matches[PIIType.ADDRESS] = address_matches
            all_pii_types.append(PIIType.ADDRESS)

        # Detect names (simple pattern - capitalized words)
        name_matches = self._detect_name(text)
        if name_matches.matches:
            matches[PIIType.NAME] = name_matches
            all_pii_types.append(PIIType.NAME)

        # Build redacted text
        redacted = self._apply_redaction(text, matches)

        result = PIIResult(
            detected=len(all_pii_types) > 0,
            pii_types=all_pii_types,
            matches=matches,
            redacted_text=redacted,
        )
        return result

    def _detect_email(self, text: str) -> PIIMatch:
        """Detect email addresses in text."""
        email_pattern = r'\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b'
        matches = re.findall(email_pattern, text)
        positions = [(m.start(), m.end()) for m in re.finditer(email_pattern, text)]

        # Redact emails in text for the return
        redacted = re.sub(email_pattern, '[REDACTED]', text)

        return PIIMatch(
            pii_type=PIIType.EMAIL,
            detected=len(matches) > 0,
            matches={PIIType.EMAIL: matches},
            positions=positions,
        )

    def _detect_phone(self, text: str) -> PIIMatch:
        """Detect phone numbers in text."""
        phone_pattern = r'(\+\d{1,3}[- ]?)?\d{3,4}[- ]?\d{3,4}[- ]?\d{3,4}'
        clean_matches = []
        for m in re.finditer(phone_pattern, text):
            clean_matches.append(m.group(0))
        positions = [(m.start(), m.end()) for m in re.finditer(phone_pattern, text)]

        redacted = re.sub(phone_pattern, '[REDACTED]', text)

        return PIIMatch(
            pii_type=PIIType.PHONE,
            detected=len(clean_matches) > 0,
            matches={PIIType.PHONE: clean_matches},
            positions=positions,
        )

    def _detect_address(self, text: str) -> PIIMatch:
        """Detect address patterns in text."""
        # Simple address pattern - street number + street name + city/state/zip
        address_pattern = r'\d+\s+[A-Za-z0-9\s]+(?:street|street|rd|ave|blvd|drive|lane|ct)\s+[A-Za-z\s]+(?:,\s*[A-Z]{2})\s+\d{5}(?:-\d{4})?'
        matches_iter = re.finditer(address_pattern, text, re.IGNORECASE)
        matches = list(matches_iter)
        positions = [(m.start(), m.end()) for m in matches]

        redacted = re.sub(address_pattern, '[REDACTED]', text, flags=re.IGNORECASE)

        return PIIMatch(
            pii_type=PIIType.ADDRESS,
            detected=len(matches) > 0,
            matches={PIIType.ADDRESS: list(matches)},
            positions=positions,
        )

    def _detect_name(self, text: str) -> PIIMatch:
        """Detect personal names (simple heuristic: capitalized words)."""
        # Simple heuristic - words that start with uppercase and are followed by lowercase
        name_pattern = r'\b[A-Z][a-z]+(?:\s+[A-Z][a-z]+)*\b'
        matches = re.findall(name_pattern, text)
        positions = [(m.start(), m.end()) for m in re.finditer(name_pattern, text)]

        # Filter out common words that aren't names
        common_words = {'The', 'This', 'That', 'With', 'From', 'Have', 'Each', 'Which', 'Would', 'Could', 'Should'}
        filtered_matches = [m for m in matches if m not in common_words]

        redacted = re.sub(name_pattern, '[REDACTED]', text)

        return PIIMatch(
            pii_type=PIIType.NAME,
            detected=len(filtered_matches) > 0,
            matches={PIIType.NAME: filtered_matches},
            positions=positions,
        )

    def _apply_redaction(self, text: str, matches: Dict[PIIType, PIIMatch]) -> str:
        """Apply redaction to text based on matches."""
        redacted = text
        for pii_type, match in matches.items():
            if match.matches:
                for match_str in match.matches:
                    pattern_map = {
                        PIIType.EMAIL: r'\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b',
                        PIIType.PHONE: r'(\+\d{1,3}[- ]?)?\d{3,4}[- ]?\d{3,4}[- ]?\d{3,4}',
                        PIIType.ADDRESS: r'\d+\s+[A-Za-z0-9\s]+(?:street|street|rd|ave|blvd|drive|lane|ct)\s+[A-Za-z\s]+(?:,\s*[A-Z]{2})\s+\d{5}(?:-\d{4})?',
                        PIIType.NAME: r'\b[A-Z][a-z]+(?:\s+[A-Z][a-z]+)*\b',
                    }
                    pattern = pattern_map.get(pii_type, r'[REDACTED]')
                    redacted = re.sub(pattern, '[REDACTED]', redacted)
        return redacted


# Module-level convenience functions
_default_policy: Optional[KnowledgeSecurityPolicy] = None


def get_knowledge_security_policy() -> KnowledgeSecurityPolicy:
    """Get the default knowledge security policy instance."""
    global _default_policy
    if _default_policy is None:
        _default_policy = KnowledgeSecurityPolicy()
    return _default_policy


def set_knowledge_security_policy(policy: KnowledgeSecurityPolicy) -> None:
    """Set the default knowledge security policy instance."""
    global _default_policy
    _default_policy = policy


def redact_text(text: str) -> str:
    """Redact PII from text using the default policy."""
    return get_knowledge_security_policy().redact_text(text)


def detect_pii(text: str) -> PIIResult:
    """Detect PII in text using the default policy."""
    return get_knowledge_security_policy().detect_pii(text)


__all__ = [
    "KnowledgeSecurityPolicy",
    "PIIType",
    "PIIMatch",
    "PIIResult",
    "get_knowledge_security_policy",
    "set_knowledge_security_policy",
    "redact_text",
    "detect_pii",
]