"""S2 multi-platform data models.

Lightweight, ORM-free value objects describing connected platform accounts,
their contacts, and the messages exchanged over them. These are the data
layer for the multi-platform access feature ("S2 多平台接入"); they are
deliberately dependency-free so both the in-process service layer and the
(planned) persistence adapter can share them.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, Optional


class PlatformType(str, Enum):
    """Supported external platforms for multi-platform access."""

    TELEGRAM = "telegram"
    SLACK = "slack"
    DISCORD = "discord"
    WHATSAPP = "whatsapp"
    WECHAT = "wechat"
    EMAIL = "email"
    WEB = "web"
    GENERIC = "generic"


class PlatformAccountStatus(str, Enum):
    """Lifecycle status of a connected platform account."""

    ACTIVE = "active"
    INACTIVE = "inactive"
    SUSPENDED = "suspended"
    PENDING = "pending"


class MessageDirection(str, Enum):
    """Whether a message originated from the platform or from us."""

    INBOUND = "inbound"
    OUTBOUND = "outbound"


class MessageStatus(str, Enum):
    """Delivery state of a platform message."""

    SENT = "sent"
    DELIVERED = "delivered"
    READ = "read"
    FAILED = "failed"
    RECEIVED = "received"


@dataclass
class PlatformAccount:
    """A connected account on an external platform."""

    platform: PlatformType
    account_id: str
    display_name: Optional[str] = None
    status: PlatformAccountStatus = PlatformAccountStatus.ACTIVE
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class PlatformContact:
    """A contact known to one of our platform accounts."""

    platform: PlatformType
    contact_id: str
    display_name: Optional[str] = None
    account_id: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class PlatformMessage:
    """A single message exchanged over a platform."""

    platform: PlatformType
    message_id: str
    direction: MessageDirection
    content: str
    account_id: Optional[str] = None
    contact_id: Optional[str] = None
    status: MessageStatus = MessageStatus.RECEIVED
    metadata: Dict[str, Any] = field(default_factory=dict)


__all__ = [
    "PlatformType",
    "PlatformAccountStatus",
    "MessageDirection",
    "MessageStatus",
    "PlatformAccount",
    "PlatformContact",
    "PlatformMessage",
]
