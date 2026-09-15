"""S2 multi-platform access service ("多平台接入").

Connects to external platforms (see :class:`PlatformType`), manages connected
accounts and contacts, and sends/receives messages. The S2 refactor wires the
memory layer to this service so conversations can span multiple platforms.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from .models import (
    MessageDirection,
    PlatformAccount,
    PlatformAccountStatus,
    PlatformContact,
    PlatformMessage,
    PlatformType,
)

logger = logging.getLogger(__name__)


class PlatformService:
    """Manage multi-platform connections and messaging."""

    def __init__(self, provider_name: str = "mock") -> None:
        self.provider_name = provider_name
        self._accounts: Dict[str, PlatformAccount] = {}

    # --- platform registry -------------------------------------------------
    def list_platforms(self) -> List[str]:
        """Return the platform codes this service can connect to."""
        return [p.value for p in PlatformType]

    @staticmethod
    def _coerce_platform(platform: Any) -> PlatformType:
        """Accept a :class:`PlatformType` or a string code (e.g. ``"whatsapp"``)."""
        if isinstance(platform, PlatformType):
            return platform
        try:
            return PlatformType(platform)
        except ValueError:
            valid = [p.value for p in PlatformType]
            raise ValueError(f"Unknown platform: {platform!r}; valid codes: {valid}")

    # --- account management ------------------------------------------------
    def connect_account(
        self,
        platform: PlatformType,
        account_id: str,
        display_name: Optional[str] = None,
    ) -> PlatformAccount:
        """Register a connected account for `platform`."""
        platform = self._coerce_platform(platform)
        account = PlatformAccount(
            platform=platform,
            account_id=account_id,
            display_name=display_name,
            status=PlatformAccountStatus.ACTIVE,
        )
        self._accounts[f"{platform.value}:{account_id}"] = account
        return account

    def list_accounts(self) -> List[PlatformAccount]:
        return list(self._accounts.values())

    def get_account(
        self, platform: PlatformType, account_id: str
    ) -> Optional[PlatformAccount]:
        platform = self._coerce_platform(platform)
        return self._accounts.get(f"{platform.value}:{account_id}")

    # --- contacts ----------------------------------------------------------
    def add_contact(
        self,
        platform: PlatformType,
        contact_id: str,
        account_id: Optional[str] = None,
        display_name: Optional[str] = None,
    ) -> PlatformContact:
        platform = self._coerce_platform(platform)
        return PlatformContact(
            platform=platform,
            contact_id=contact_id,
            account_id=account_id,
            display_name=display_name,
        )

    # --- messaging ---------------------------------------------------------
    def send_message(
        self,
        platform: PlatformType,
        account_id: str,
        content: str,
        contact_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Send `content` over `platform`. Mock implementation by default."""
        platform = self._coerce_platform(platform)
        return {
            "platform": platform.value,
            "account_id": account_id,
            "contact_id": contact_id,
            "sent": True,
            "content": content,
        }

    def receive_message(
        self,
        platform: PlatformType,
        account_id: str,
        contact_id: str,
        content: str,
    ) -> PlatformMessage:
        """Record an inbound message as a :class:`PlatformMessage`."""
        platform = self._coerce_platform(platform)
        return PlatformMessage(
            platform=platform,
            message_id=f"msg-{id(content)}-{len(self._accounts)}",
            direction=MessageDirection.INBOUND,
            content=content,
            account_id=account_id,
            contact_id=contact_id,
        )

    def health(self) -> Dict[str, Any]:
        return {"provider": self.provider_name, "accounts": len(self._accounts)}
