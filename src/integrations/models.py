"""Compatibility exports for the platform-integration model boundary.

The platform intelligence models live in :mod:`src.knowledge.models`, but
existing integrations and downstream callers import them from
``src.integrations.models``. Keep that boundary stable while the ownership
remains in the knowledge package.
"""

from src.knowledge.models import (
    MessageDirection,
    MessageStatus,
    PlatformAccount,
    PlatformAccountStatus,
    PlatformContact,
    PlatformMessage,
    PlatformType,
)

__all__ = [
    "MessageDirection",
    "MessageStatus",
    "PlatformAccount",
    "PlatformAccountStatus",
    "PlatformContact",
    "PlatformMessage",
    "PlatformType",
]
