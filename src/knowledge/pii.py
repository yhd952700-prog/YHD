"""Rule-based PII (Personally Identifiable Information) detector for the knowledge pipeline.

Detects common PII types -- including Chinese mobile numbers, mainland ID
numbers, emails, labelled names and mailing addresses -- without relying on
third-party services. Output masking uses a canonical ``[REDACTED]`` value.

Design notes:

* Detection returns *spans* (start/end offsets in the original text); redaction
  rewrites the original text from those spans instead of recomputing a regex
  (the previous implementation computed ``re.sub`` results and threw them
  away, so nothing was ever actually redacted).
* Name detection is deliberately conservative. Treating every capitalised
  English word as a person name redacts ordinary prose ("Python", "Monday") on
  a massive scale; missing an unlabelled name is the lesser evil, so names are
  only matched after an explicit label (``姓名:``, ``Name:``) or an honorific
  (``Mr.``/``Ms.``/``Dr.``).
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import re
from typing import Any, Dict, List, Optional, Tuple


REDACTION_TOKEN = "[REDACTED]"


class PIIType(str, Enum):
    """Enum of PII types that can be detected."""
    EMAIL = "email"
    PHONE = "phone"
    ADDRESS = "address"
    NAME = "name"
    CREDIT_CARD = "credit_card"
    ID_CARD = "id_card"


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


# --- patterns ---------------------------------------------------------------
# Basic email shape (ASCII local part / domain, mirroring the previous rule).
_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")

# Mainland China mobile numbers, optionally prefixed by +86 / 0086 / 86.
_CN_MOBILE_RE = re.compile(
    r"(?<!\d)(?:(?:\+|00)?86[- ]?)?1[3-9]\d{9}(?!\d)"
)
# Mainland landline numbers (area code 0xx + 7/8 digit local number).
_CN_LANDLINE_RE = re.compile(r"(?<!\d)0\d{2,3}[- ]?\d{7,8}(?!\d)")
# International format kept intentionally strict: leading + and separators.
_INTL_PHONE_RE = re.compile(r"(?<!\d)\+[1-9]\d{0,2}[- ]?\d{2,4}[- ]?\d{3,4}[- ]?\d{3,4}(?!\d)")

# 18-digit mainland resident ID (the shape most commonly leaked).
_CN_ID_RE = re.compile(
    r"(?<!\d)[1-9]\d{5}(?:18|19|20)\d{2}"
    r"(?:0[1-9]|1[0-2])(?:0[1-9]|[12]\d|3[01])\d{3}[\dXx](?!\d)"
)
# 16-digit card number, optionally grouped by fours.
_CREDIT_CARD_RE = re.compile(r"(?<!\d)(?:\d{4}[- ]?){3}\d{4}(?!\d)")

# English-style mailing address: number + street + suffix + (city, state) + ZIP.
_ADDRESS_RE = re.compile(
    r"\d+\s+[A-Za-z0-9.\s]+"
    r"(?:street|st|rd|road|ave|avenue|blvd|drive|lane|ct|court)\b"
    r"[A-Za-z0-9,\s]{0,40}\d{5}(?:-\d{4})?",
    re.IGNORECASE,
)

# Only extract the *name* part after an explicit label / honorific.
_CN_NAME_LABEL_RE = re.compile(
    r"(?:姓名|联系人|作者|收件人|申请人|负责人|审核人|签到人)\s*[:：]\s*"
    r"([\u4e00-\u9fa5·]{2,4})"
)
_EN_NAME_LABEL_RE = re.compile(
    r"(?i:name|contact|author|recipient)\s*[:：]\s*"
    r"([A-Z][a-z]+(?:\s+[A-Z][a-z]+){0,2})",
)
_EN_NAME_HONORIFIC_RE = re.compile(
    r"(?:Mr\.|Ms\.|Mrs\.|Dr\.)\s+([A-Z][a-z]+(?:\s+[A-Z][a-z]+){0,1})"
)


def _collect(regex: "re.Pattern[str]", text: str, include_group: Optional[int] = None) -> Tuple[List[str], List[Tuple[int, int]]]:
    """Return matched strings and spans; optionally only a capture group's span."""
    matches: List[str] = []
    positions: List[Tuple[int, int]] = []
    for match in regex.finditer(text):
        start, end = match.span(include_group if include_group is not None else 0)
        if start < 0 or end <= start:
            continue
        matches.append(text[start:end])
        positions.append((start, end))
    return matches, positions


def _merge_spans(spans: List[Tuple[int, int]]) -> List[Tuple[int, int]]:
    """Merge overlapping/adjacent spans so one token replaces one region."""
    if not spans:
        return []
    ordered = sorted(spans)
    merged: List[Tuple[int, int]] = [ordered[0]]
    for start, end in ordered[1:]:
        last_start, last_end = merged[-1]
        if start <= last_end:
            merged[-1] = (last_start, max(last_end, end))
        else:
            merged.append((start, end))
    return merged


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
        return result.redacted_text

    def detect_pii(self, text: str) -> PIIResult:
        """Detect PII in the given text and return a PIIResult."""
        matches: Dict[PIIType, PIIMatch] = {}
        all_pii_types: List[PIIType] = []

        for pii_type, detector in (
            (PIIType.EMAIL, self._detect_email),
            (PIIType.PHONE, self._detect_phone),
            (PIIType.ID_CARD, self._detect_id_card),
            (PIIType.CREDIT_CARD, self._detect_credit_card),
            (PIIType.ADDRESS, self._detect_address),
            (PIIType.NAME, self._detect_name),
        ):
            found = detector(text)
            if found.matches:
                matches[pii_type] = found
                all_pii_types.append(pii_type)

        redacted = self._apply_redaction(text, matches)

        return PIIResult(
            detected=len(all_pii_types) > 0,
            pii_types=all_pii_types,
            matches=matches,
            redacted_text=redacted,
        )

    def _detect_email(self, text: str) -> PIIMatch:
        """Detect email addresses in text."""
        matches, positions = _collect(_EMAIL_RE, text)
        return PIIMatch(
            pii_type=PIIType.EMAIL,
            detected=bool(matches),
            matches=matches,
            positions=positions,
        )

    def _detect_phone(self, text: str) -> PIIMatch:
        """Detect Chinese mobile/landline and international phone numbers."""
        combined: List[Tuple[int, int, str]] = []
        for regex in (_CN_MOBILE_RE, _CN_LANDLINE_RE, _INTL_PHONE_RE):
            for match in regex.finditer(text):
                combined.append((match.start(), match.end(), match.group(0)))
        # Deduplicate spans that several patterns matched at the same place.
        combined.sort(key=lambda item: (item[0], -(item[1] - item[0])))
        unique: List[Tuple[int, int, str]] = []
        for start, end, value in combined:
            if unique and start <= unique[-1][1]:
                continue
            unique.append((start, end, value))
        return PIIMatch(
            pii_type=PIIType.PHONE,
            detected=bool(unique),
            matches=[value for _, _, value in unique],
            positions=[(start, end) for start, end, _ in unique],
        )

    def _detect_id_card(self, text: str) -> PIIMatch:
        """Detect 18-digit mainland resident ID numbers."""
        matches, positions = _collect(_CN_ID_RE, text)
        return PIIMatch(
            pii_type=PIIType.ID_CARD,
            detected=bool(matches),
            matches=matches,
            positions=positions,
        )

    def _detect_credit_card(self, text: str) -> PIIMatch:
        """Detect 16-digit card numbers (grouped or plain)."""
        matches, positions = _collect(_CREDIT_CARD_RE, text)
        return PIIMatch(
            pii_type=PIIType.CREDIT_CARD,
            detected=bool(matches),
            matches=matches,
            positions=positions,
        )

    def _detect_address(self, text: str) -> PIIMatch:
        """Detect English-style mailing addresses."""
        matches, positions = _collect(_ADDRESS_RE, text)
        return PIIMatch(
            pii_type=PIIType.ADDRESS,
            detected=bool(matches),
            matches=matches,
            positions=positions,
        )

    def _detect_name(self, text: str) -> PIIMatch:
        """Detect names only when explicitly labelled or honorific-prefixed.

        Generic capitalised words are *not* names -- redacting every ``The``/
        ``Python``/``Monday`` destroys ordinary text while still missing most
        real names.
        """
        matches: List[str] = []
        positions: List[Tuple[int, int]] = []
        for regex in (_CN_NAME_LABEL_RE, _EN_NAME_LABEL_RE, _EN_NAME_HONORIFIC_RE):
            found, spans = _collect(regex, text, include_group=1)
            matches.extend(found)
            positions.extend(spans)
        return PIIMatch(
            pii_type=PIIType.NAME,
            detected=bool(matches),
            matches=matches,
            positions=positions,
        )

    def _apply_redaction(self, text: str, matches: Dict[PIIType, PIIMatch]) -> str:
        """Rewrite the original text from collected spans (never from scratch).

        Spans are merged and replaced right-to-left so earlier offsets stay
        valid; this is what makes detection results actually reach the output.
        """
        spans: List[Tuple[int, int]] = []
        for match in matches.values():
            spans.extend(match.positions)
        if not spans:
            return text

        redacted = text
        for start, end in reversed(_merge_spans(spans)):
            redacted = f"{redacted[:start]}{REDACTION_TOKEN}{redacted[end:]}"
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


def detect_pii(text: str) -> Dict[str, Any]:
    """Detect PII in text using the default policy.

    Returns a dict with ``detected`` (bool) and ``types`` (list[str]) so the
    RAG pipeline can subscript it directly (``result["detected"]``,
    ``result["types"]``). The underlying :class:`PIIResult` is converted here.
    """
    result = get_knowledge_security_policy().detect_pii(text)
    return {
        "detected": result.detected,
        "types": [t.value for t in result.pii_types],
    }


__all__ = [
    "KnowledgeSecurityPolicy",
    "PIIType",
    "PIIMatch",
    "PIIResult",
    "REDACTION_TOKEN",
    "get_knowledge_security_policy",
    "set_knowledge_security_policy",
    "redact_text",
    "detect_pii",
]
