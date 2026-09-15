"""Knowledge-base security policy used by the RAG pipeline.

Provides retrieval validation, PII filtering, and security-event auditing so
the RAG flow can refuse disallowed retrievals and redact sensitive content
before it reaches the LLM or the caller.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

POLICY_VERSION = "2026.09.S2"


class KnowledgeSecurityPolicy:
    """Access-control / PII policy for knowledge-base retrieval and answers."""

    def __init__(
        self,
        allowed_operations: Optional[List[str]] = None,
        redact_pii: bool = True,
        allowed_users: Optional[List[str]] = None,
    ) -> None:
        self.policy_version = POLICY_VERSION
        self.allowed_operations = allowed_operations or ["read", "search", "query"]
        self.redact_pii = redact_pii
        self.allowed_users = allowed_users or []

    def validate_retrieval(self, user: Any, docs: List[Any]) -> Dict[str, Any]:
        """Decide whether `user` may retrieve `docs`.

        Returns a dict with ``allowed`` (bool) and ``reason`` (str | None).
        """
        user_id = getattr(user, "id", user)
        if self.allowed_users and user_id not in self.allowed_users:
            return {"allowed": False, "reason": "user not permitted to retrieve"}
        return {"allowed": True, "reason": None}

    def filter_content(self, text: str) -> str:
        """Redact PII from `text`; returns the text unchanged when disabled."""
        if not self.redact_pii or not text:
            return text
        try:
            from .pii import redact_text

            return redact_text(text)
        except Exception:  # pragma: no cover - defensive
            return text

    def audit_security_event(self, event: Dict[str, Any]) -> None:
        """Record a security-relevant event (retrieval / filter / deny)."""
        logger.info(
            "knowledge_security_event policy_version=%s %s",
            self.policy_version,
            event,
        )
