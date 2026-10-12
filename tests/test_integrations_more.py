"""
Tests for LiuHao AI OS Additional Integration Adapters.

Tests cover all 15 NEW adapters beyond the original 12.
Each adapter has at least 3 test cases.
Tests verify structure, dataclasses, parsing, enums, and basic behavior
without requiring actual API connections.

Adapters tested:
1. TelegramAdapter
2. MediumAdapter
3. FigmaAdapter
4. LinearAdapter
5. ClickUpAdapter
6. AsanaAdapter
7. MondayAdapter
8. AirtableAdapter
9. StripeAdapter
10. BrevoAdapter
11. TwilioAdapter
12. DropboxAdapter
13. BoxAdapter
14. ConfluenceAdapter
15. PagerDutyAdapter
"""

import sys
import os
import unittest
from unittest.mock import patch, MagicMock

# Add src to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from integrations.base_adapter import (
    AuthType,
    BaseIntegrationAdapter,
    ConnectionStatus,
    IntegrationConfig,
    IntegrationError,
    IntegrationStatus,
    RateLimitError,
)
from integrations.telegram_adapter import (
    TelegramAdapter,
    TelegramMessage,
    TelegramChatType,
    TelegramParseMode,
    TelegramInlineKeyboardButton,
    TelegramBotCommand,
)
from integrations.medium_adapter import (
    MediumAdapter,
    MediumPost,
    MediumPostFormat,
    MediumPublishStatus,
    MediumUser,
    MediumPublication,
)
from integrations.figma_adapter import (
    FigmaAdapter,
    FigmaFile,
    FigmaImageFormat,
    FigmaWebhookEvent,
    FigmaComponent,
)
from integrations.linear_adapter import (
    LinearAdapter,
    LinearIssue,
    LinearIssueState,
    LinearIssuePriority,
    LinearTeam,
)
from integrations.clickup_adapter import (
    ClickUpAdapter,
    ClickUpTask,
    ClickUpTaskPriority,
    ClickUpList,
    ClickUpFolder,
)
from integrations.asana_adapter import (
    AsanaAdapter,
    AsanaTask,
    AsanaTaskStatus,
    AsanaResourceType,
    AsanaProject,
    AsanaWorkspace,
)
from integrations.monday_adapter import (
    MondayAdapter,
    MondayItem,
    MondayColumnType,
    MondayBoard,
    MondayGroup,
)
from integrations.airtable_adapter import (
    AirtableAdapter,
    AirtableRecord,
    AirtableFieldType,
    AirtableBase,
    AirtableView,
)
from integrations.stripe_adapter import (
    StripeAdapter,
    StripeCustomer,
    StripeCurrency,
    StripePaymentIntentStatus,
    StripeSubscriptionStatus,
    StripeInvoice,
)
from integrations.brevo_adapter import (
    BrevoAdapter,
    BrevoContact,
    BrevoEmailEventType,
    BrevoSendEmailRequest,
    BrevoEmailRecipient,
)
from integrations.twilio_adapter import (
    TwilioAdapter,
    TwilioMessage,
    TwilioMessageStatus,
    TwilioCall,
    TwilioPhoneNumber,
)
from integrations.dropbox_adapter import (
    DropboxAdapter,
    DropboxFileMetadata,
    DropboxWriteMode,
    DropboxSharedLink,
    DropboxAccessLevel,
)
from integrations.box_adapter import (
    BoxAdapter,
    BoxFile,
    BoxItemType,
    BoxFolder,
    BoxAccessLevel,
)
from integrations.confluence_adapter import (
    ConfluenceAdapter,
    ConfluencePage,
    ConfluenceContentType,
    ConfluenceContentStatus,
    ConfluenceSpace,
    ConfluenceSpaceType,
    ConfluenceSpaceStatus,
)
from integrations.pagerduty_adapter import (
    PagerDutyAdapter,
    PagerDutyIncident,
    PagerDutyIncidentStatus,
    PagerDutyService,
    PagerDutyUrgency,
    PagerDutySeverity,
    PagerDutyServiceStatus,
)


# Helper to create a minimal IntegrationConfig with base_url
def _make_config(name: str = "test", **kwargs) -> IntegrationConfig:
    defaults = {
        "base_url": "https://api.example.com",
        "auth_type": AuthType.API_KEY,
    }
    defaults.update(kwargs)
    return IntegrationConfig(name=name, **defaults)


# ======================================================================
# Telegram Adapter Tests (4 tests)
# ======================================================================

class TestTelegramAdapter(unittest.TestCase):
    """Tests for TelegramAdapter."""

    def setUp(self):
        self.config = _make_config(
            name="test_telegram",
            base_url="https://api.telegram.org",
            auth_type=AuthType.API_KEY,
            api_key="test_token_12345",
        )

    def test_adapter_instantiation(self):
        """Test that adapter can be instantiated with correct defaults."""
        adapter = TelegramAdapter(self.config)
        self.assertIsInstance(adapter, BaseIntegrationAdapter)
        self.assertEqual(adapter.config.name, "test_telegram")
        self.assertEqual(adapter.config.auth_type, AuthType.API_KEY)
        self.assertEqual(adapter.config.api_key, "test_token_12345")
        self.assertFalse(adapter.is_connected())
        self.assertEqual(adapter._status, ConnectionStatus.DISCONNECTED)

    def test_message_dataclass(self):
        """Test TelegramMessage dataclass creation and fields."""
        msg = TelegramMessage(
            chat_id=123456789,
            text="Hello world",
            parse_mode=TelegramParseMode.MARKDOWN,
        )
        self.assertEqual(msg.chat_id, 123456789)
        self.assertEqual(msg.text, "Hello world")
        self.assertEqual(msg.parse_mode, TelegramParseMode.MARKDOWN)
        self.assertFalse(msg.disable_web_page_preview)
        self.assertIsNone(msg.reply_to_message_id)

    def test_inline_keyboard_builder(self):
        """Test inline keyboard building."""
        adapter = TelegramAdapter(self.config)
        buttons = [
            [
                TelegramInlineKeyboardButton(text="Yes", callback_data="yes"),
                TelegramInlineKeyboardButton(text="No", callback_data="no"),
            ]
        ]
        keyboard = adapter.build_inline_keyboard(buttons)
        self.assertIn("inline_keyboard", keyboard)
        self.assertEqual(len(keyboard["inline_keyboard"]), 1)
        self.assertEqual(len(keyboard["inline_keyboard"][0]), 2)
        self.assertEqual(keyboard["inline_keyboard"][0][0]["text"], "Yes")
        self.assertEqual(keyboard["inline_keyboard"][0][0]["callback_data"], "yes")

    def test_enum_values(self):
        """Test that enums have correct values."""
        self.assertEqual(TelegramChatType.PRIVATE.value, "private")
        self.assertEqual(TelegramChatType.CHANNEL.value, "channel")
        self.assertEqual(TelegramParseMode.HTML.value, "HTML")
        self.assertEqual(TelegramParseMode.MARKDOWN_V2.value, "MarkdownV2")


# ======================================================================
# Medium Adapter Tests (3 tests)
# ======================================================================

class TestMediumAdapter(unittest.TestCase):
    """Tests for MediumAdapter."""

    def setUp(self):
        self.config = _make_config(
            name="test_medium",
            base_url="https://api.medium.com",
            auth_type=AuthType.BEARER_TOKEN,
            access_token="test_medium_token",
        )

    def test_adapter_instantiation(self):
        adapter = MediumAdapter(self.config)
        self.assertIsInstance(adapter, BaseIntegrationAdapter)
        self.assertEqual(adapter.config.auth_type, AuthType.BEARER_TOKEN)
        self.assertFalse(adapter.is_connected())

    def test_post_dataclass(self):
        post = MediumPost(
            title="Test Post",
            content="# Hello",
            content_format=MediumPostFormat.MARKDOWN,
            tags=["test", "python"],
        )
        self.assertEqual(post.title, "Test Post")
        self.assertEqual(post.content, "# Hello")
        self.assertEqual(post.content_format, MediumPostFormat.MARKDOWN)
        self.assertEqual(len(post.tags), 2)
        self.assertEqual(post.publish_status, MediumPublishStatus.PUBLIC)

    def test_format_helpers(self):
        md_post = MediumAdapter.format_markdown_post(
            "Title", "# Body", ["tag1"]
        )
        self.assertEqual(md_post.title, "Title")
        self.assertEqual(md_post.content_format, MediumPostFormat.MARKDOWN)
        self.assertIn("tag1", md_post.tags)

        html_post = MediumAdapter.format_html_post("T", "<p>body</p>")
        self.assertEqual(html_post.content_format, MediumPostFormat.HTML)


# ======================================================================
# Figma Adapter Tests (3 tests)
# ======================================================================

class TestFigmaAdapter(unittest.TestCase):
    """Tests for FigmaAdapter."""

    def setUp(self):
        self.config = _make_config(
            name="test_figma",
            base_url="https://api.figma.com",
            auth_type=AuthType.BEARER_TOKEN,
            access_token="figma_token",
        )

    def test_adapter_instantiation(self):
        adapter = FigmaAdapter(self.config)
        self.assertIsInstance(adapter, BaseIntegrationAdapter)
        self.assertFalse(adapter.is_connected())

    def test_file_dataclass(self):
        f = FigmaFile(key="abc123", name="Design File")
        self.assertEqual(f.key, "abc123")
        self.assertEqual(f.name, "Design File")
        self.assertEqual(f.schema_version, 0)

    def test_webhook_verification(self):
        adapter = FigmaAdapter(self.config)
        body = '{"event":"file_update"}'
        timestamp = "1234567890"
        passcode = "secret123"

        import hmac
        import hashlib
        signed_content = f"{timestamp}\n{body}"
        expected = hmac.new(
            passcode.encode("utf-8"),
            signed_content.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()

        self.assertTrue(adapter.verify_webhook(body, timestamp, expected, passcode))
        self.assertFalse(adapter.verify_webhook(body, timestamp, "wrong_sig", passcode))


# ======================================================================
# Linear Adapter Tests (3 tests)
# ======================================================================

class TestLinearAdapter(unittest.TestCase):
    """Tests for LinearAdapter."""

    def setUp(self):
        self.config = _make_config(
            name="test_linear",
            base_url="https://api.linear.app",
            auth_type=AuthType.API_KEY,
            api_key="lin_api_123",
        )

    def test_adapter_instantiation(self):
        adapter = LinearAdapter(self.config)
        self.assertIsInstance(adapter, BaseIntegrationAdapter)
        self.assertEqual(adapter.config.api_key, "lin_api_123")

    def test_issue_dataclass_and_enum(self):
        issue = LinearIssue(
            identifier="ENG-42",
            title="Fix bug",
            priority=LinearIssuePriority.HIGH,
            state_type=LinearIssueState.STARTED,
        )
        self.assertEqual(issue.identifier, "ENG-42")
        self.assertEqual(issue.title, "Fix bug")
        self.assertEqual(issue.priority, LinearIssuePriority.HIGH)
        self.assertEqual(issue.state_type, LinearIssueState.STARTED)
        self.assertEqual(issue.priority.value, 2)

    def test_team_dataclass(self):
        team = LinearTeam(id="team123", name="Engineering", key="ENG")
        self.assertEqual(team.id, "team123")
        self.assertEqual(team.name, "Engineering")
        self.assertEqual(team.key, "ENG")


# ======================================================================
# ClickUp Adapter Tests (3 tests)
# ======================================================================

class TestClickUpAdapter(unittest.TestCase):
    """Tests for ClickUpAdapter."""

    def setUp(self):
        self.config = _make_config(
            name="test_clickup",
            base_url="https://api.clickup.com",
            auth_type=AuthType.API_KEY,
            api_key="clickup_token",
        )

    def test_adapter_instantiation(self):
        adapter = ClickUpAdapter(self.config)
        self.assertIsInstance(adapter, BaseIntegrationAdapter)
        self.assertFalse(adapter.is_connected())

    def test_task_dataclass(self):
        task = ClickUpTask(
            id="task123",
            name="Test Task",
            priority=ClickUpTaskPriority.URGENT,
        )
        self.assertEqual(task.id, "task123")
        self.assertEqual(task.name, "Test Task")
        self.assertEqual(task.priority, ClickUpTaskPriority.URGENT)

    def test_list_and_folder_dataclasses(self):
        lst = ClickUpList(id="list1", name="To Do")
        self.assertEqual(lst.id, "list1")
        self.assertEqual(lst.name, "To Do")

        folder = ClickUpFolder(id="folder1", name="Project X")
        self.assertEqual(folder.id, "folder1")
        self.assertEqual(folder.name, "Project X")


# ======================================================================
# Asana Adapter Tests (3 tests)
# ======================================================================

class TestAsanaAdapter(unittest.TestCase):
    """Tests for AsanaAdapter."""

    def setUp(self):
        self.config = _make_config(
            name="test_asana",
            base_url="https://app.asana.com",
            auth_type=AuthType.BEARER_TOKEN,
            access_token="asana_token",
        )

    def test_adapter_instantiation(self):
        adapter = AsanaAdapter(self.config)
        self.assertIsInstance(adapter, BaseIntegrationAdapter)
        self.assertFalse(adapter.is_connected())

    def test_task_dataclass(self):
        task = AsanaTask(
            gid="12345",
            name="Complete project",
            completed=False,
        )
        self.assertEqual(task.gid, "12345")
        self.assertEqual(task.name, "Complete project")
        self.assertFalse(task.completed)
        self.assertEqual(task.resource_type, AsanaResourceType.TASK)
        self.assertEqual(task.num_subtasks, 0)

    def test_task_completed_status_and_enum(self):
        task_incomplete = AsanaTask(gid="1", name="Todo", completed=False)
        self.assertFalse(task_incomplete.completed)

        task_complete = AsanaTask(gid="2", name="Done", completed=True)
        self.assertTrue(task_complete.completed)

        self.assertEqual(AsanaTaskStatus.COMPLETED.value, "completed")
        self.assertEqual(AsanaTaskStatus.INCOMPLETE.value, "incomplete")
        self.assertEqual(AsanaResourceType.TASK.value, "task")
        self.assertEqual(AsanaResourceType.PROJECT.value, "project")


# ======================================================================
# Monday Adapter Tests (3 tests)
# ======================================================================

class TestMondayAdapter(unittest.TestCase):
    """Tests for MondayAdapter."""

    def setUp(self):
        self.config = _make_config(
            name="test_monday",
            base_url="https://api.monday.com",
            auth_type=AuthType.API_KEY,
            api_key="monday_token",
        )

    def test_adapter_instantiation(self):
        adapter = MondayAdapter(self.config)
        self.assertIsInstance(adapter, BaseIntegrationAdapter)
        self.assertFalse(adapter.is_connected())

    def test_item_dataclass(self):
        item = MondayItem(id="item1", name="New Task", group_id="group1")
        self.assertEqual(item.id, "item1")
        self.assertEqual(item.name, "New Task")
        self.assertEqual(item.group_id, "group1")

    def test_column_type_enum(self):
        self.assertEqual(MondayColumnType.STATUS.value, "status")
        self.assertEqual(MondayColumnType.TEXT.value, "text")
        self.assertEqual(MondayColumnType.NUMERIC.value, "numeric")
        self.assertEqual(MondayColumnType.DATE.value, "date")
        self.assertEqual(MondayColumnType.PEOPLE.value, "people")


# ======================================================================
# Airtable Adapter Tests (3 tests)
# ======================================================================

class TestAirtableAdapter(unittest.TestCase):
    """Tests for AirtableAdapter."""

    def setUp(self):
        self.config = _make_config(
            name="test_airtable",
            base_url="https://api.airtable.com",
            auth_type=AuthType.BEARER_TOKEN,
            access_token="airtable_token",
        )

    def test_adapter_instantiation(self):
        adapter = AirtableAdapter(self.config)
        self.assertIsInstance(adapter, BaseIntegrationAdapter)
        self.assertFalse(adapter.is_connected())

    def test_record_dataclass(self):
        record = AirtableRecord(
            id="rec123",
            fields={"Name": "Test", "Count": 42},
        )
        self.assertEqual(record.id, "rec123")
        self.assertEqual(record.fields["Name"], "Test")
        self.assertEqual(record.fields["Count"], 42)

    def test_formula_builders(self):
        f1 = AirtableAdapter.build_formula_equals("Status", "Done")
        self.assertIn("{Status}", f1)
        self.assertIn("Done", f1)

        f2 = AirtableAdapter.build_formula_and(
            AirtableAdapter.build_formula_equals("A", "1"),
            AirtableAdapter.build_formula_is_blank("B"),
        )
        self.assertTrue(f2.startswith("AND("))

        f3 = AirtableAdapter.build_formula_or(
            AirtableAdapter.build_formula_contains("Name", "x"),
            AirtableAdapter.build_formula_contains("Desc", "y"),
        )
        self.assertTrue(f3.startswith("OR("))


# ======================================================================
# Stripe Adapter Tests (3 tests)
# ======================================================================

class TestStripeAdapter(unittest.TestCase):
    """Tests for StripeAdapter."""

    def setUp(self):
        self.config = _make_config(
            name="test_stripe",
            base_url="https://api.stripe.com",
            auth_type=AuthType.BEARER_TOKEN,
            access_token="sk_test_123",
            webhook_secret="whsec_test",
        )

    def test_adapter_instantiation(self):
        adapter = StripeAdapter(self.config)
        self.assertIsInstance(adapter, BaseIntegrationAdapter)
        self.assertFalse(adapter.is_connected())

    def test_customer_dataclass_and_currency(self):
        customer = StripeCustomer(
            id="cus_123",
            email="test@example.com",
            name="Test User",
        )
        self.assertEqual(customer.id, "cus_123")
        self.assertEqual(customer.email, "test@example.com")
        self.assertEqual(StripeCurrency.USD.value, "usd")
        self.assertEqual(StripeCurrency.EUR.value, "eur")

    def test_webhook_verification(self):
        import time
        import hmac
        import hashlib

        adapter = StripeAdapter(self.config)
        secret = "whsec_test_secret"
        payload = '{"id":"evt_test"}'
        ts = str(int(time.time()))
        signed_payload = f"{ts}.{payload}"
        sig = hmac.new(
            secret.encode("utf-8"),
            signed_payload.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()
        signature = f"t={ts},v1={sig}"

        self.assertTrue(adapter.verify_webhook(payload, signature, secret))
        self.assertFalse(adapter.verify_webhook(payload, "t=0,v1=bad", secret))


# ======================================================================
# Brevo Adapter Tests (3 tests)
# ======================================================================

class TestBrevoAdapter(unittest.TestCase):
    """Tests for BrevoAdapter."""

    def setUp(self):
        self.config = _make_config(
            name="test_brevo",
            base_url="https://api.brevo.com",
            auth_type=AuthType.API_KEY,
            api_key="brevo_key",
        )

    def test_adapter_instantiation(self):
        adapter = BrevoAdapter(self.config)
        self.assertIsInstance(adapter, BaseIntegrationAdapter)
        self.assertFalse(adapter.is_connected())

    def test_contact_dataclass(self):
        contact = BrevoContact(
            id=1,
            email="user@example.com",
            list_ids=[1, 2, 3],
        )
        self.assertEqual(contact.id, 1)
        self.assertEqual(contact.email, "user@example.com")
        self.assertEqual(len(contact.list_ids), 3)

    def test_email_request_builder(self):
        req = BrevoSendEmailRequest(
            to=[BrevoEmailRecipient(email="a@b.com", name="A")],
            sender=BrevoEmailRecipient(email="sender@b.com", name="Sender"),
            subject="Hello",
            html_content="<p>Hi</p>",
        )
        self.assertEqual(len(req.to), 1)
        self.assertEqual(req.to[0].email, "a@b.com")
        self.assertEqual(req.subject, "Hello")


# ======================================================================
# Twilio Adapter Tests (3 tests)
# ======================================================================

class TestTwilioAdapter(unittest.TestCase):
    """Tests for TwilioAdapter."""

    def setUp(self):
        self.config = _make_config(
            name="test_twilio",
            base_url="https://api.twilio.com",
            auth_type=AuthType.BASIC_AUTH,
            username="AC123",
            password="auth_token",
        )

    def test_adapter_instantiation(self):
        adapter = TwilioAdapter(self.config)
        self.assertIsInstance(adapter, BaseIntegrationAdapter)
        self.assertEqual(adapter.config.username, "AC123")
        self.assertFalse(adapter.is_connected())

    def test_message_dataclass_and_status(self):
        msg = TwilioMessage(
            sid="SM123",
            from_number="+1234567890",
            to="+1987654321",
            body="Hello",
            status=TwilioMessageStatus.DELIVERED,
        )
        self.assertEqual(msg.sid, "SM123")
        self.assertEqual(msg.status, TwilioMessageStatus.DELIVERED)
        self.assertEqual(msg.body, "Hello")

    def test_phone_number_dataclass(self):
        num = TwilioPhoneNumber(
            sid="PN123",
            phone_number="+1234567890",
            friendly_name="My Number",
        )
        self.assertEqual(num.sid, "PN123")
        self.assertEqual(num.phone_number, "+1234567890")


# ======================================================================
# Dropbox Adapter Tests (3 tests)
# ======================================================================

class TestDropboxAdapter(unittest.TestCase):
    """Tests for DropboxAdapter."""

    def setUp(self):
        self.config = _make_config(
            name="test_dropbox",
            base_url="https://api.dropboxapi.com",
            auth_type=AuthType.BEARER_TOKEN,
            access_token="dropbox_token",
        )

    def test_adapter_instantiation(self):
        adapter = DropboxAdapter(self.config)
        self.assertIsInstance(adapter, BaseIntegrationAdapter)
        self.assertFalse(adapter.is_connected())

    def test_file_metadata_dataclass(self):
        meta = DropboxFileMetadata(
            id="id:abc123",
            name="document.pdf",
            path_lower="/docs/document.pdf",
            size=1024,
            is_file=True,
        )
        self.assertEqual(meta.id, "id:abc123")
        self.assertEqual(meta.name, "document.pdf")
        self.assertTrue(meta.is_file)
        self.assertFalse(meta.is_folder)

    def test_write_mode_enum(self):
        self.assertEqual(DropboxWriteMode.ADD.value, "add")
        self.assertEqual(DropboxWriteMode.OVERWRITE.value, "overwrite")
        self.assertEqual(DropboxWriteMode.UPDATE.value, "update")


# ======================================================================
# Box Adapter Tests (3 tests)
# ======================================================================

class TestBoxAdapter(unittest.TestCase):
    """Tests for BoxAdapter."""

    def setUp(self):
        self.config = _make_config(
            name="test_box",
            base_url="https://api.box.com",
            auth_type=AuthType.BEARER_TOKEN,
            access_token="box_token",
        )

    def test_adapter_instantiation(self):
        adapter = BoxAdapter(self.config)
        self.assertIsInstance(adapter, BaseIntegrationAdapter)
        self.assertFalse(adapter.is_connected())

    def test_file_and_folder_dataclasses(self):
        f = BoxFile(id="12345", name="file.txt", size=1000)
        self.assertEqual(f.id, "12345")
        self.assertEqual(f.name, "file.txt")
        self.assertEqual(f.type, BoxItemType.FILE)

        folder = BoxFolder(id="67890", name="Documents")
        self.assertEqual(folder.id, "67890")
        self.assertEqual(folder.type, BoxItemType.FOLDER)

    def test_access_level_enum(self):
        self.assertEqual(BoxAccessLevel.OPEN.value, "open")
        self.assertEqual(BoxAccessLevel.COMPANY.value, "company")
        self.assertEqual(BoxAccessLevel.COLLABORATORS.value, "collaborators")


# ======================================================================
# Confluence Adapter Tests (3 tests)
# ======================================================================

class TestConfluenceAdapter(unittest.TestCase):
    """Tests for ConfluenceAdapter."""

    def setUp(self):
        self.config = _make_config(
            name="test_confluence",
            base_url="https://example.atlassian.net",
            auth_type=AuthType.BASIC_AUTH,
            username="user@example.com",
            password="api_token",
        )

    def test_adapter_instantiation(self):
        adapter = ConfluenceAdapter(self.config)
        self.assertIsInstance(adapter, BaseIntegrationAdapter)
        self.assertFalse(adapter.is_connected())

    def test_page_dataclass(self):
        page = ConfluencePage(
            id="12345",
            title="My Page",
            space_key="DOC",
            body_storage="<p>Hello</p>",
        )
        self.assertEqual(page.id, "12345")
        self.assertEqual(page.title, "My Page")
        self.assertEqual(page.space_key, "DOC")
        self.assertEqual(page.type, ConfluenceContentType.PAGE)
        self.assertEqual(page.status, ConfluenceContentStatus.CURRENT)

    def test_space_dataclass_and_enum(self):
        space = ConfluenceSpace(
            key="TEST",
            name="Test Space",
            type=ConfluenceSpaceType.GLOBAL,
        )
        self.assertEqual(space.key, "TEST")
        self.assertEqual(space.name, "Test Space")
        self.assertEqual(space.type, ConfluenceSpaceType.GLOBAL)
        self.assertEqual(ConfluenceSpaceStatus.CURRENT.value, "current")
        self.assertEqual(ConfluenceSpaceStatus.ARCHIVED.value, "archived")


# ======================================================================
# PagerDuty Adapter Tests (4 tests)
# ======================================================================

class TestPagerDutyAdapter(unittest.TestCase):
    """Tests for PagerDutyAdapter."""

    def setUp(self):
        self.config = _make_config(
            name="test_pagerduty",
            base_url="https://api.pagerduty.com",
            auth_type=AuthType.API_KEY,
            api_key="pd_token",
            webhook_secret="pd_secret",
        )

    def test_adapter_instantiation(self):
        adapter = PagerDutyAdapter(self.config)
        self.assertIsInstance(adapter, BaseIntegrationAdapter)
        self.assertEqual(adapter.config.api_key, "pd_token")
        self.assertFalse(adapter.is_connected())

    def test_incident_dataclass_and_status(self):
        incident = PagerDutyIncident(
            id="P12345",
            title="Server down",
            status=PagerDutyIncidentStatus.TRIGGERED,
            urgency=PagerDutyUrgency.HIGH,
            incident_number=42,
        )
        self.assertEqual(incident.id, "P12345")
        self.assertEqual(incident.title, "Server down")
        self.assertEqual(incident.status, PagerDutyIncidentStatus.TRIGGERED)
        self.assertEqual(incident.urgency, PagerDutyUrgency.HIGH)
        self.assertEqual(incident.incident_number, 42)

    def test_service_and_severity_enums(self):
        service = PagerDutyService(id="S123", name="API Service")
        self.assertEqual(service.id, "S123")
        self.assertEqual(service.name, "API Service")
        self.assertEqual(service.status, PagerDutyServiceStatus.ACTIVE)
        self.assertEqual(PagerDutySeverity.CRITICAL.value, "critical")
        self.assertEqual(PagerDutySeverity.INFO.value, "info")

    def test_webhook_verification(self):
        import hmac
        import hashlib

        adapter = PagerDutyAdapter(self.config)
        body = '{"event":"incident.triggered"}'
        secret = "test_secret"
        expected = hmac.new(
            secret.encode("utf-8"),
            body.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()

        self.assertTrue(adapter.verify_webhook(body, f"v1={expected}", secret))
        self.assertFalse(adapter.verify_webhook(body, "v1=invalid", secret))


# ======================================================================
# Common base behavior tests for all 15 adapters
# ======================================================================

class TestAllAdaptersBaseBehavior(unittest.TestCase):
    """Common base behavior tests across all 15 new adapters."""

    def test_all_adapters_extend_base(self):
        """Verify all 15 adapters extend BaseIntegrationAdapter."""
        adapters = [
            TelegramAdapter,
            MediumAdapter,
            FigmaAdapter,
            LinearAdapter,
            ClickUpAdapter,
            AsanaAdapter,
            MondayAdapter,
            AirtableAdapter,
            StripeAdapter,
            BrevoAdapter,
            TwilioAdapter,
            DropboxAdapter,
            BoxAdapter,
            ConfluenceAdapter,
            PagerDutyAdapter,
        ]
        for adapter_cls in adapters:
            self.assertTrue(
                issubclass(adapter_cls, BaseIntegrationAdapter),
                f"{adapter_cls.__name__} does not extend BaseIntegrationAdapter"
            )

    def test_all_adapters_disconnect_sets_status(self):
        """Verify disconnect sets status to DISCONNECTED for all adapters."""
        configs = [
            (TelegramAdapter, _make_config(name="t", base_url="https://a.com", api_key="k")),
            (MediumAdapter, _make_config(name="m", base_url="https://a.com", auth_type=AuthType.BEARER_TOKEN, access_token="k")),
            (FigmaAdapter, _make_config(name="f", base_url="https://a.com", auth_type=AuthType.BEARER_TOKEN, access_token="k")),
            (LinearAdapter, _make_config(name="l", base_url="https://a.com", api_key="k")),
            (ClickUpAdapter, _make_config(name="c", base_url="https://a.com", api_key="k")),
            (AsanaAdapter, _make_config(name="a", base_url="https://a.com", auth_type=AuthType.BEARER_TOKEN, access_token="k")),
            (MondayAdapter, _make_config(name="m", base_url="https://a.com", api_key="k")),
            (AirtableAdapter, _make_config(name="a", base_url="https://a.com", auth_type=AuthType.BEARER_TOKEN, access_token="k")),
            (StripeAdapter, _make_config(name="s", base_url="https://a.com", auth_type=AuthType.BEARER_TOKEN, access_token="k")),
            (BrevoAdapter, _make_config(name="b", base_url="https://a.com", api_key="k")),
            (TwilioAdapter, _make_config(name="t", base_url="https://a.com", auth_type=AuthType.BASIC_AUTH, username="u", password="p")),
            (DropboxAdapter, _make_config(name="d", base_url="https://a.com", auth_type=AuthType.BEARER_TOKEN, access_token="k")),
            (BoxAdapter, _make_config(name="b", base_url="https://a.com", auth_type=AuthType.BEARER_TOKEN, access_token="k")),
            (ConfluenceAdapter, _make_config(name="c", base_url="https://a.com", auth_type=AuthType.BASIC_AUTH, username="u", password="p")),
            (PagerDutyAdapter, _make_config(name="p", base_url="https://a.com", api_key="k")),
        ]
        for adapter_cls, config in configs:
            adapter = adapter_cls(config)
            result = adapter.disconnect()
            self.assertTrue(result, f"{adapter_cls.__name__}.disconnect() returned False")
            self.assertEqual(
                adapter._status,
                ConnectionStatus.DISCONNECTED,
                f"{adapter_cls.__name__} status not DISCONNECTED after disconnect()",
            )
            self.assertFalse(adapter.is_connected())

    def test_all_adapters_get_status_returns_integration_status(self):
        """Verify get_status returns IntegrationStatus for all adapters."""
        configs = [
            (TelegramAdapter, _make_config(name="t", base_url="https://a.com", api_key="k")),
            (MediumAdapter, _make_config(name="m", base_url="https://a.com", auth_type=AuthType.BEARER_TOKEN, access_token="k")),
            (FigmaAdapter, _make_config(name="f", base_url="https://a.com", auth_type=AuthType.BEARER_TOKEN, access_token="k")),
            (LinearAdapter, _make_config(name="l", base_url="https://a.com", api_key="k")),
            (ClickUpAdapter, _make_config(name="c", base_url="https://a.com", api_key="k")),
            (AsanaAdapter, _make_config(name="a", base_url="https://a.com", auth_type=AuthType.BEARER_TOKEN, access_token="k")),
            (MondayAdapter, _make_config(name="m", base_url="https://a.com", api_key="k")),
            (AirtableAdapter, _make_config(name="a", base_url="https://a.com", auth_type=AuthType.BEARER_TOKEN, access_token="k")),
            (StripeAdapter, _make_config(name="s", base_url="https://a.com", auth_type=AuthType.BEARER_TOKEN, access_token="k")),
            (BrevoAdapter, _make_config(name="b", base_url="https://a.com", api_key="k")),
            (TwilioAdapter, _make_config(name="t", base_url="https://a.com", auth_type=AuthType.BASIC_AUTH, username="u", password="p")),
            (DropboxAdapter, _make_config(name="d", base_url="https://a.com", auth_type=AuthType.BEARER_TOKEN, access_token="k")),
            (BoxAdapter, _make_config(name="b", base_url="https://a.com", auth_type=AuthType.BEARER_TOKEN, access_token="k")),
            (ConfluenceAdapter, _make_config(name="c", base_url="https://a.com", auth_type=AuthType.BASIC_AUTH, username="u", password="p")),
            (PagerDutyAdapter, _make_config(name="p", base_url="https://a.com", api_key="k")),
        ]
        for adapter_cls, config in configs:
            adapter = adapter_cls(config)
            status = adapter.get_status()
            self.assertIsInstance(
                status,
                IntegrationStatus,
                f"{adapter_cls.__name__}.get_status() did not return IntegrationStatus",
            )
            self.assertEqual(status.name, config.name)
            self.assertFalse(status.is_connected)

    def test_all_adapters_test_connection_returns_false_when_disconnected(self):
        """Verify test_connection returns False when not connected."""
        configs = [
            (TelegramAdapter, _make_config(name="t", base_url="https://a.com", api_key="k")),
            (MediumAdapter, _make_config(name="m", base_url="https://a.com", auth_type=AuthType.BEARER_TOKEN, access_token="k")),
            (FigmaAdapter, _make_config(name="f", base_url="https://a.com", auth_type=AuthType.BEARER_TOKEN, access_token="k")),
            (LinearAdapter, _make_config(name="l", base_url="https://a.com", api_key="k")),
            (ClickUpAdapter, _make_config(name="c", base_url="https://a.com", api_key="k")),
            (AsanaAdapter, _make_config(name="a", base_url="https://a.com", auth_type=AuthType.BEARER_TOKEN, access_token="k")),
            (MondayAdapter, _make_config(name="m", base_url="https://a.com", api_key="k")),
            (AirtableAdapter, _make_config(name="a", base_url="https://a.com", auth_type=AuthType.BEARER_TOKEN, access_token="k")),
            (StripeAdapter, _make_config(name="s", base_url="https://a.com", auth_type=AuthType.BEARER_TOKEN, access_token="k")),
            (BrevoAdapter, _make_config(name="b", base_url="https://a.com", api_key="k")),
            (TwilioAdapter, _make_config(name="t", base_url="https://a.com", auth_type=AuthType.BASIC_AUTH, username="u", password="p")),
            (DropboxAdapter, _make_config(name="d", base_url="https://a.com", auth_type=AuthType.BEARER_TOKEN, access_token="k")),
            (BoxAdapter, _make_config(name="b", base_url="https://a.com", auth_type=AuthType.BEARER_TOKEN, access_token="k")),
            (ConfluenceAdapter, _make_config(name="c", base_url="https://a.com", auth_type=AuthType.BASIC_AUTH, username="u", password="p")),
            (PagerDutyAdapter, _make_config(name="p", base_url="https://a.com", api_key="k")),
        ]
        for adapter_cls, config in configs:
            adapter = adapter_cls(config)
            self.assertFalse(
                adapter.test_connection(),
                f"{adapter_cls.__name__}.test_connection() should return False when disconnected",
            )

    def test_all_adapters_repr(self):
        """Verify __repr__ returns a string containing class name."""
        configs = [
            (TelegramAdapter, _make_config(name="t", base_url="https://a.com", api_key="k")),
            (MediumAdapter, _make_config(name="m", base_url="https://a.com", auth_type=AuthType.BEARER_TOKEN, access_token="k")),
            (FigmaAdapter, _make_config(name="f", base_url="https://a.com", auth_type=AuthType.BEARER_TOKEN, access_token="k")),
            (LinearAdapter, _make_config(name="l", base_url="https://a.com", api_key="k")),
            (ClickUpAdapter, _make_config(name="c", base_url="https://a.com", api_key="k")),
            (AsanaAdapter, _make_config(name="a", base_url="https://a.com", auth_type=AuthType.BEARER_TOKEN, access_token="k")),
            (MondayAdapter, _make_config(name="m", base_url="https://a.com", api_key="k")),
            (AirtableAdapter, _make_config(name="a", base_url="https://a.com", auth_type=AuthType.BEARER_TOKEN, access_token="k")),
            (StripeAdapter, _make_config(name="s", base_url="https://a.com", auth_type=AuthType.BEARER_TOKEN, access_token="k")),
            (BrevoAdapter, _make_config(name="b", base_url="https://a.com", api_key="k")),
            (TwilioAdapter, _make_config(name="t", base_url="https://a.com", auth_type=AuthType.BASIC_AUTH, username="u", password="p")),
            (DropboxAdapter, _make_config(name="d", base_url="https://a.com", auth_type=AuthType.BEARER_TOKEN, access_token="k")),
            (BoxAdapter, _make_config(name="b", base_url="https://a.com", auth_type=AuthType.BEARER_TOKEN, access_token="k")),
            (ConfluenceAdapter, _make_config(name="c", base_url="https://a.com", auth_type=AuthType.BASIC_AUTH, username="u", password="p")),
            (PagerDutyAdapter, _make_config(name="p", base_url="https://a.com", api_key="k")),
        ]
        for adapter_cls, config in configs:
            adapter = adapter_cls(config)
            repr_str = repr(adapter)
            self.assertIsInstance(repr_str, str)
            self.assertIn(
                adapter_cls.__name__,
                repr_str,
                f"{adapter_cls.__name__} __repr__ missing class name",
            )


if __name__ == "__main__":
    unittest.main()
