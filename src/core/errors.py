"""Shared error hierarchy for the LiuHao knowledge and identity layers.

The Memory System refactor (src/knowledge/memory_system.py) raises these so
callers can catch knowledge-specific failures distinctly from generic
exceptions.
"""

from __future__ import annotations


class KnowledgeError(Exception):
    """Base error for knowledge-layer operations."""


class NotFoundError(KnowledgeError):
    """The requested resource does not exist."""


class PermissionDeniedError(KnowledgeError):
    """The caller lacks the required permission for this operation."""


class ValidationError(KnowledgeError):
    """Input failed validation before the operation was attempted."""
