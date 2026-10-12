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
"""

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
