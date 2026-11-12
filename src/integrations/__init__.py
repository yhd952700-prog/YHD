"""
LiuHao AI OS Integration Adapters.

This package provides integration adapters for various external services,
all extending the BaseIntegrationAdapter abstract base class.

Available adapters:
- SlackAdapter
- DiscordAdapter
- GitHubAdapter
- GitLabAdapter
- NotionAdapter
- JiraAdapter
- SalesforceAdapter
- ShopifyAdapter
- WordPressAdapter
- TrelloAdapter
- ZendeskAdapter
- HubSpotAdapter

Lazy loading
------------
导入本包**不再**一次性拉起全部 28 个适配器（实测约 0.5–0.8s）。任何一个
``from src.integrations import XxxAdapter`` 仍然可用：属性访问会经
``__getattr__`` 按需导入对应子模块并缓存。这样 ``src/knowledge/db.py`` 里
``from src.integrations.orm_models import Base`` 触发的包初始化只剩一个字典，
不会把 Stripe/Asana/Twilio 等 SaaS SDK 拖进启动关键路径。
"""

from __future__ import annotations

from importlib import import_module
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:  # 仅类型检查期导入，运行期由 __getattr__ 按需加载
    from .base_adapter import (
        AuthType,
        BaseIntegrationAdapter,
        ConnectionStatus,
        IntegrationConfig,
        IntegrationError,
        IntegrationStatus,
        RateLimitError,
    )
    from .registry import (
        AdapterEntry,
        IntegrationRegistry,
        RegistryHealthReport,
        get_global_registry,
    )
    from .slack_adapter import SlackAdapter
    from .discord_adapter import DiscordAdapter
    from .github_adapter import GitHubAdapter
    from .gitlab_adapter import GitlabAdapter
    from .notion_adapter import NotionAdapter
    from .jira_adapter import JiraAdapter
    from .salesforce_adapter import SalesforceAdapter
    from .shopify_adapter import ShopifyAdapter
    from .wordpress_adapter import WordPressAdapter
    from .trello_adapter import TrelloAdapter
    from .zendesk_adapter import ZendeskAdapter
    from .hubspot_adapter import HubSpotAdapter
    from .telegram_adapter import TelegramAdapter
    from .medium_adapter import MediumAdapter
    from .figma_adapter import FigmaAdapter
    from .linear_adapter import LinearAdapter
    from .clickup_adapter import ClickUpAdapter
    from .asana_adapter import AsanaAdapter
    from .monday_adapter import MondayAdapter
    from .airtable_adapter import AirtableAdapter
    from .stripe_adapter import StripeAdapter
    from .brevo_adapter import BrevoAdapter
    from .twilio_adapter import TwilioAdapter
    from .dropbox_adapter import DropboxAdapter
    from .box_adapter import BoxAdapter
    from .confluence_adapter import ConfluenceAdapter
    from .pagerduty_adapter import PagerDutyAdapter

#: 公开名 -> 定义它的子模块。属性访问时才导入该子模块。
_LAZY_ATTRS: dict[str, str] = {
    # Base
    "AuthType": ".base_adapter",
    "BaseIntegrationAdapter": ".base_adapter",
    "ConnectionStatus": ".base_adapter",
    "IntegrationConfig": ".base_adapter",
    "IntegrationError": ".base_adapter",
    "IntegrationStatus": ".base_adapter",
    "RateLimitError": ".base_adapter",
    # Registry
    "AdapterEntry": ".registry",
    "IntegrationRegistry": ".registry",
    "RegistryHealthReport": ".registry",
    "get_global_registry": ".registry",
    # Adapters
    "SlackAdapter": ".slack_adapter",
    "DiscordAdapter": ".discord_adapter",
    "GitHubAdapter": ".github_adapter",
    "GitlabAdapter": ".gitlab_adapter",
    "NotionAdapter": ".notion_adapter",
    "JiraAdapter": ".jira_adapter",
    "SalesforceAdapter": ".salesforce_adapter",
    "ShopifyAdapter": ".shopify_adapter",
    "WordPressAdapter": ".wordpress_adapter",
    "TrelloAdapter": ".trello_adapter",
    "ZendeskAdapter": ".zendesk_adapter",
    "HubSpotAdapter": ".hubspot_adapter",
    "TelegramAdapter": ".telegram_adapter",
    "MediumAdapter": ".medium_adapter",
    "FigmaAdapter": ".figma_adapter",
    "LinearAdapter": ".linear_adapter",
    "ClickUpAdapter": ".clickup_adapter",
    "AsanaAdapter": ".asana_adapter",
    "MondayAdapter": ".monday_adapter",
    "AirtableAdapter": ".airtable_adapter",
    "StripeAdapter": ".stripe_adapter",
    "BrevoAdapter": ".brevo_adapter",
    "TwilioAdapter": ".twilio_adapter",
    "DropboxAdapter": ".dropbox_adapter",
    "BoxAdapter": ".box_adapter",
    "ConfluenceAdapter": ".confluence_adapter",
    "PagerDutyAdapter": ".pagerduty_adapter",
}

__all__ = [
    # Base
    "BaseIntegrationAdapter",
    "IntegrationConfig",
    "IntegrationStatus",
    "ConnectionStatus",
    "AuthType",
    "IntegrationError",
    "RateLimitError",
    # Registry
    "IntegrationRegistry",
    "AdapterEntry",
    "RegistryHealthReport",
    "get_global_registry",
    # Adapters
    "SlackAdapter",
    "DiscordAdapter",
    "GitHubAdapter",
    "GitlabAdapter",
    "NotionAdapter",
    "JiraAdapter",
    "SalesforceAdapter",
    "ShopifyAdapter",
    "WordPressAdapter",
    "TrelloAdapter",
    "ZendeskAdapter",
    "HubSpotAdapter",
    "TelegramAdapter",
    "MediumAdapter",
    "FigmaAdapter",
    "LinearAdapter",
    "ClickUpAdapter",
    "AsanaAdapter",
    "MondayAdapter",
    "AirtableAdapter",
    "StripeAdapter",
    "BrevoAdapter",
    "TwilioAdapter",
    "DropboxAdapter",
    "BoxAdapter",
    "ConfluenceAdapter",
    "PagerDutyAdapter",
]

__version__ = "1.0.0"


def __getattr__(name: str) -> Any:
    """按需导入公开名（PEP 562），导入后缓存进模块全局。"""
    module_name = _LAZY_ATTRS.get(name)
    if module_name is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    value = getattr(import_module(module_name, __name__), name)
    globals()[name] = value
    return value


def __dir__() -> list[str]:
    return sorted(set(globals()) | set(_LAZY_ATTRS))
