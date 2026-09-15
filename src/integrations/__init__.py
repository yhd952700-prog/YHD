"""
S2 多平台接入（Platform Intelligence）

统一管理 WhatsApp / Facebook / LinkedIn / 企业微信 4 大外贸触达渠道，
提供账号绑定、消息收发、联系人管理与多语言自动翻译。

The actual S2 implementation lives in ``src/knowledge`` (models / service /
translation); this package is a thin facade that re-exports those symbols so
callers can import the platform-intelligence types from ``src.integrations``.
The legacy ORM/cloud integration layer (cloud_models) was superseded by this
refactor and deleted on 2026-09-15; the decision is recorded as a history note
in orphan-registry.yaml. The live SQLAlchemy Base/SessionManager stays in the
sibling orm_models.py module.
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
from src.knowledge.service import PlatformService
from src.knowledge.translation import LANGUAGE_LIST, SUPPORTED_LANGUAGES

__all__ = [
    "LANGUAGE_LIST",
    "MessageDirection",
    "MessageStatus",
    "PlatformAccount",
    "PlatformAccountStatus",
    "PlatformContact",
    "PlatformMessage",
    "PlatformService",
    "PlatformType",
    "SUPPORTED_LANGUAGES",
]
