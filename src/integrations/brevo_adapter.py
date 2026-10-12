"""
Brevo (Sendinblue) Integration Adapter for LiuHao AI OS.

Provides integration with Brevo (formerly Sendinblue) for transactional email,
email campaigns, contacts management, lists and folders, SMS sending,
templates, webhooks, and statistics.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional

from .base_adapter import (
    AuthType,
    BaseIntegrationAdapter,
    ConnectionStatus,
    IntegrationConfig,
    IntegrationError,
    IntegrationStatus,
    RateLimitError,
)


class BrevoEmailEventType(str, Enum):
    """Email event types."""
    SENT = "sent"
    DELIVERED = "delivered"
    OPENED = "opened"
    CLICKED = "clicked"
    SOFT_BOUNCE = "soft_bounce"
    HARD_BOUNCE = "hard_bounce"
    COMPLAINT = "complaint"
    UNIQUE_OPEN = "unique_opened"
    UNSUBSCRIPTION = "unsubscribed"
    BLOCKED = "blocked"
    ERROR = "error"
    DEFERRED = "deferred"
    INVALID_EMAIL = "invalid_email"


class BrevoSmsEventType(str, Enum):
    """SMS event types."""
    SENT = "sent"
    DELIVERED = "delivered"
    ANSWERED = "answered"
    SOFT_BOUNCE = "soft_bounce"
    HARD_BOUNCE = "hard_bounce"
    UNSUBSCRIPTION = "unsubscribed"
    REJECTED = "rejected"
    NOT_DELIVERED = "not_delivered"


class BrevoContactStatus(str, Enum):
    """Contact subscription status."""
    SUBSCRIBED = "subscribed"
    UNSUBSCRIBED = "unsubscribed"


@dataclass
class BrevoSender:
    """Represents a Brevo sender identity."""
    name: str = ""
    email: str = ""
    id: int = 0
    extra: Dict[str, Any] = field(default_factory=dict)


@dataclass
class BrevoEmailRecipient:
    """Represents an email recipient."""
    email: str
    name: str = ""


@dataclass
class BrevoSendEmailRequest:
    """Represents a transactional email request."""
    to: List[BrevoEmailRecipient]
    sender: BrevoSender
    subject: str = ""
    html_content: str = ""
    text_content: str = ""
    cc: List[BrevoEmailRecipient] = field(default_factory=list)
    bcc: List[BrevoEmailRecipient] = field(default_factory=list)
    reply_to: Optional[BrevoEmailRecipient] = None
    attachment: List[Dict[str, Any]] = field(default_factory=list)
    headers: Dict[str, str] = field(default_factory=dict)
    params: Dict[str, Any] = field(default_factory=dict)
    template_id: int = 0
    tags: List[str] = field(default_factory=list)
    scheduled_at: str = ""
    extra: Dict[str, Any] = field(default_factory=dict)


@dataclass
class BrevoEmailResponse:
    """Represents a transactional email response."""
    message_id: str = ""
    message_ids: List[str] = field(default_factory=list)
    extra: Dict[str, Any] = field(default_factory=dict)


@dataclass
class BrevoContact:
    """Represents a Brevo contact."""
    id: int = 0
    email: str = ""
    attributes: Dict[str, Any] = field(default_factory=dict)
    email_blacklisted: bool = False
    sms_blacklisted: bool = False
    list_ids: List[int] = field(default_factory=list)
    list_unsubscribed: List[int] = field(default_factory=list)
    created_at: str = ""
    modified_at: str = ""
    statistics: Dict[str, Any] = field(default_factory=dict)
    extra: Dict[str, Any] = field(default_factory=dict)


@dataclass
class BrevoList:
    """Represents a Brevo contacts list."""
    id: int = 0
    name: str = ""
    total_blacklisted: int = 0
    total_subscribers: int = 0
    folder_id: int = 0
    created_at: str = ""
    extra: Dict[str, Any] = field(default_factory=dict)


@dataclass
class BrevoFolder:
    """Represents a Brevo folder."""
    id: int = 0
    name: str = ""
    total_blacklisted: int = 0
    total_subscribers: int = 0
    unique_subscribers: int = 0
    extra: Dict[str, Any] = field(default_factory=dict)


@dataclass
class BrevoEmailCampaign:
    """Represents an email campaign."""
    id: int = 0
    name: str = ""
    subject: str = ""
    status: str = "draft"
    sender: Dict[str, Any] = field(default_factory=dict)
    html_content: str = ""
    text_content: str = ""
    reply_to: str = ""
    to_field: str = ""
    recipients: Dict[str, Any] = field(default_factory=dict)
    scheduled_at: str = ""
    sent_at: str = ""
    stats: Dict[str, Any] = field(default_factory=dict)
    template_id: int = 0
    tag: str = ""
    created_at: str = ""
    modified_at: str = ""
    extra: Dict[str, Any] = field(default_factory=dict)


@dataclass
class BrevoTemplate:
    """Represents an email template."""
    id: int = 0
    name: str = ""
    subject: str = ""
    html_content: str = ""
    text_content: str = ""
    sender: Dict[str, Any] = field(default_factory=dict)
    reply_to: str = ""
    to_field: str = ""
    status: bool = True
    created_at: str = ""
    modified_at: str = ""
    extra: Dict[str, Any] = field(default_factory=dict)


@dataclass
class BrevoSmsMessage:
    """Represents an SMS message to send."""
    sender: str
    recipient: str
    content: str
    type: str = "transactional"
    tag: str = ""
    web_url: str = ""
    scheduled_at: str = ""


@dataclass
class BrevoSmsResponse:
    """Represents an SMS send response."""
    reference: str = ""
    message_id: int = 0
    sms_count: int = 0
    used_credits: int = 0
    remaining_credits: float = 0.0
    extra: Dict[str, Any] = field(default_factory=dict)


@dataclass
class BrevoWebhook:
    """Represents a Brevo webhook."""
    id: int = 0
    url: str = ""
    description: str = ""
    events: List[str] = field(default_factory=list)
    type: str = "transactional"
    domain: str = ""
    extra: Dict[str, Any] = field(default_factory=dict)


@dataclass
class BrevoAccountStats:
    """Represents Brevo account statistics."""
    plan: Dict[str, Any] = field(default_factory=dict)
    relay: Dict[str, Any] = field(default_factory=dict)
    marketing: Dict[str, Any] = field(default_factory=dict)
    extra: Dict[str, Any] = field(default_factory=dict)


class BrevoAdapter(BaseIntegrationAdapter):
    """
    Brevo (Sendinblue) integration adapter for LiuHao AI OS.

    Supports:
    - Send transactional email
    - Email campaigns management
    - Contacts management (CRUD)
    - Lists and folders
    - SMS sending
    - Templates
    - Webhooks
    - Statistics
    """

    def __init__(self, config: IntegrationConfig):
        if config.auth_type == AuthType.NONE:
            config.auth_type = AuthType.API_KEY
        if not config.base_url:
            config.base_url = "https://api.brevo.com/v3"
        super().__init__(config)
        self._account_first_name: Optional[str] = None
        self._account_last_name: Optional[str] = None
        self._account_email: Optional[str] = None
        self._cached_contacts: Dict[str, BrevoContact] = {}
        self._cached_lists: Dict[int, BrevoList] = {}
        self._webhooks: List[BrevoWebhook] = []

    # ------------------------------------------------------------------
    # Base interface
    # ------------------------------------------------------------------

    def connect(self) -> bool:
        self._status = ConnectionStatus.CONNECTING
        self._logger.info("Connecting to Brevo API...")

        try:
            result = self._api_call("account")
            self._account_first_name = result.get("firstName", "")
            self._account_last_name = result.get("lastName", "")
            self._account_email = result.get("email", "")
            self._status = ConnectionStatus.CONNECTED
            self._connected_at = time.time()
            self._logger.info(
                "Connected to Brevo as %s %s (%s)",
                self._account_first_name,
                self._account_last_name,
                self._account_email,
            )
            return True
        except IntegrationError:
            raise
        except Exception as e:
            self._status = ConnectionStatus.ERROR
            raise self._handle_error(f"Connection error: {e}")

    def disconnect(self) -> bool:
        self._status = ConnectionStatus.DISCONNECTED
        self._account_first_name = None
        self._account_last_name = None
        self._account_email = None
        self._cached_contacts = {}
        self._cached_lists = {}
        self._webhooks = []
        self._session = None
        self._logger.info("Disconnected from Brevo")
        return True

    def is_connected(self) -> bool:
        return self._status == ConnectionStatus.CONNECTED

    def get_status(self) -> IntegrationStatus:
        return self._build_status({
            "account_email": self._account_email,
            "account_name": f"{self._account_first_name or ''} {self._account_last_name or ''}".strip(),
            "cached_contacts": len(self._cached_contacts),
            "cached_lists": len(self._cached_lists),
            "webhooks": len(self._webhooks),
        })

    def test_connection(self) -> bool:
        try:
            if not self.is_connected():
                return False
            result = self._api_call("account")
            return "email" in result
        except Exception:
            return False

    # ------------------------------------------------------------------
    # Transactional email
    # ------------------------------------------------------------------

    def send_email(self, request: BrevoSendEmailRequest) -> BrevoEmailResponse:
        payload: Dict[str, Any] = {
            "to": [{"email": r.email, "name": r.name} for r in request.to],
            "sender": {
                "name": request.sender.name,
                "email": request.sender.email,
            },
        }

        if request.subject:
            payload["subject"] = request.subject
        if request.html_content:
            payload["htmlContent"] = request.html_content
        if request.text_content:
            payload["textContent"] = request.text_content
        if request.cc:
            payload["cc"] = [{"email": r.email, "name": r.name} for r in request.cc]
        if request.bcc:
            payload["bcc"] = [{"email": r.email, "name": r.name} for r in request.bcc]
        if request.reply_to:
            payload["replyTo"] = {
                "email": request.reply_to.email,
                "name": request.reply_to.name,
            }
        if request.attachment:
            payload["attachment"] = request.attachment
        if request.headers:
            payload["headers"] = request.headers
        if request.params:
            payload["params"] = request.params
        if request.template_id > 0:
            payload["templateId"] = request.template_id
        if request.tags:
            payload["tags"] = request.tags
        if request.scheduled_at:
            payload["scheduledAt"] = request.scheduled_at
        payload.update(request.extra)

        result = self._api_call(
            "smtp/email", data=payload, http_method="POST"
        )
        return BrevoEmailResponse(
            message_id=result.get("messageId", ""),
            message_ids=result.get("messageIds", []),
            extra=result,
        )

    def send_email_simple(self, to: str, sender_email: str,
                          subject: str, html_content: str = "",
                          text_content: str = "",
                          sender_name: str = "") -> BrevoEmailResponse:
        request = BrevoSendEmailRequest(
            to=[BrevoEmailRecipient(email=to)],
            sender=BrevoSender(email=sender_email, name=sender_name),
            subject=subject,
            html_content=html_content,
            text_content=text_content,
        )
        return self.send_email(request)

    def get_email_status(self, message_id: str) -> Dict[str, Any]:
        result = self._api_call(f"smtp/emailStatus/{message_id}")
        return result

    def get_transactional_email_events(self,
                                       limit: int = 50,
                                       offset: int = 0,
                                       email: str = "",
                                       message_id: str = "",
                                       event: Optional[BrevoEmailEventType] = None,
                                       **kwargs: Any) -> Dict[str, Any]:
        params: Dict[str, Any] = {"limit": limit, "offset": offset}
        if email:
            params["email"] = email
        if message_id:
            params["messageId"] = message_id
        if event:
            params["event"] = event.value
        params.update(kwargs)

        result = self._api_call("smtp/emailEvent", params=params)
        return result

    def get_account_stats(self) -> BrevoAccountStats:
        result = self._api_call("account")
        return BrevoAccountStats(
            plan=result.get("plan", []),
            relay=result.get("relay", {}),
            marketing=result.get("marketing", {}),
            extra=result,
        )

    # ------------------------------------------------------------------
    # Contacts
    # ------------------------------------------------------------------

    def create_contact(self, email: str,
                       attributes: Optional[Dict[str, Any]] = None,
                       list_ids: Optional[List[int]] = None,
                       update_enabled: bool = False,
                       **kwargs: Any) -> BrevoContact:
        payload: Dict[str, Any] = {"email": email}
        if attributes:
            payload["attributes"] = attributes
        if list_ids:
            payload["listIds"] = list_ids
        if update_enabled:
            payload["updateEnabled"] = True
        payload.update(kwargs)

        result = self._api_call(
            "contacts", data=payload, http_method="POST"
        )
        contact = self._parse_contact(result)
        if contact.email:
            self._cached_contacts[contact.email] = contact
        return contact

    def get_contact(self, identifier: str) -> BrevoContact:
        if identifier in self._cached_contacts:
            return self._cached_contacts[identifier]

        result = self._api_call(f"contacts/{identifier}")
        contact = self._parse_contact(result)
        if contact.email:
            self._cached_contacts[contact.email] = contact
        return contact

    def update_contact(self, identifier: str,
                       attributes: Optional[Dict[str, Any]] = None,
                       email_blacklisted: Optional[bool] = None,
                       sms_blacklisted: Optional[bool] = None,
                       list_ids: Optional[List[int]] = None,
                       unlink_list_ids: Optional[List[int]] = None,
                       **kwargs: Any) -> bool:
        payload: Dict[str, Any] = {}
        if attributes:
            payload["attributes"] = attributes
        if email_blacklisted is not None:
            payload["emailBlacklisted"] = email_blacklisted
        if sms_blacklisted is not None:
            payload["smsBlacklisted"] = sms_blacklisted
        if list_ids:
            payload["listIds"] = list_ids
        if unlink_list_ids:
            payload["unlinkListIds"] = unlink_list_ids
        payload.update(kwargs)

        self._api_call(
            f"contacts/{identifier}", data=payload, http_method="PUT"
        )
        if identifier in self._cached_contacts:
            del self._cached_contacts[identifier]
        return True

    def delete_contact(self, identifier: str) -> bool:
        self._api_call(f"contacts/{identifier}", http_method="DELETE")
        if identifier in self._cached_contacts:
            del self._cached_contacts[identifier]
        return True

    def list_contacts(self, limit: int = 50,
                      offset: int = 0,
                      sort: str = "desc",
                      **kwargs: Any) -> Dict[str, Any]:
        params: Dict[str, Any] = {"limit": limit, "offset": offset, "sort": sort}
        params.update(kwargs)

        result = self._api_call("contacts", params=params)
        contacts_data = result.get("contacts", [])
        contacts = [self._parse_contact(c) for c in contacts_data]
        for c in contacts:
            if c.email:
                self._cached_contacts[c.email] = c

        return {
            "contacts": contacts,
            "count": result.get("count", 0),
        }

    def import_contacts(self, list_ids: List[int],
                        file_body: str = "",
                        file_url: str = "",
                        **kwargs: Any) -> Dict[str, Any]:
        payload: Dict[str, Any] = {"listIds": list_ids}
        if file_body:
            payload["fileBody"] = file_body
        if file_url:
            payload["fileUrl"] = file_url
        payload.update(kwargs)

        result = self._api_call(
            "contacts/import", data=payload, http_method="POST"
        )
        return result

    def _parse_contact(self, data: Dict[str, Any]) -> BrevoContact:
        return BrevoContact(
            id=data.get("id", 0),
            email=data.get("email", ""),
            attributes=data.get("attributes", {}),
            email_blacklisted=data.get("emailBlacklisted", False),
            sms_blacklisted=data.get("smsBlacklisted", False),
            list_ids=data.get("listIds", []),
            list_unsubscribed=data.get("listUnsubscribed", []),
            created_at=data.get("createdAt", ""),
            modified_at=data.get("modifiedAt", ""),
            statistics=data.get("statistics", {}),
            extra=data,
        )

    # ------------------------------------------------------------------
    # Lists and folders
    # ------------------------------------------------------------------

    def create_list(self, name: str,
                    folder_id: int = 0) -> BrevoList:
        payload: Dict[str, Any] = {"name": name}
        if folder_id:
            payload["folderId"] = folder_id

        result = self._api_call(
            "contacts/lists", data=payload, http_method="POST"
        )
        lst = BrevoList(
            id=result.get("id", 0),
            name=name,
            folder_id=folder_id,
            extra=result,
        )
        if lst.id:
            self._cached_lists[lst.id] = lst
        return lst

    def get_list(self, list_id: int) -> BrevoList:
        if list_id in self._cached_lists:
            return self._cached_lists[list_id]

        result = self._api_call(f"contacts/lists/{list_id}")
        lst = BrevoList(
            id=result.get("id", list_id),
            name=result.get("name", ""),
            total_blacklisted=result.get("totalBlacklisted", 0),
            total_subscribers=result.get("totalSubscribers", 0),
            folder_id=result.get("folderId", 0),
            created_at=result.get("createdAt", ""),
            extra=result,
        )
        self._cached_lists[list_id] = lst
        return lst

    def update_list(self, list_id: int,
                    name: Optional[str] = None,
                    folder_id: Optional[int] = None) -> bool:
        payload: Dict[str, Any] = {}
        if name is not None:
            payload["name"] = name
        if folder_id is not None:
            payload["folderId"] = folder_id

        self._api_call(
            f"contacts/lists/{list_id}", data=payload, http_method="PUT"
        )
        if list_id in self._cached_lists:
            del self._cached_lists[list_id]
        return True

    def delete_list(self, list_id: int) -> bool:
        self._api_call(f"contacts/lists/{list_id}", http_method="DELETE")
        if list_id in self._cached_lists:
            del self._cached_lists[list_id]
        return True

    def list_lists(self, limit: int = 50,
                   offset: int = 0,
                   sort: str = "desc",
                   **kwargs: Any) -> Dict[str, Any]:
        params: Dict[str, Any] = {"limit": limit, "offset": offset, "sort": sort}
        params.update(kwargs)

        result = self._api_call("contacts/lists", params=params)
        lists_data = result.get("lists", [])
        lists = [
            BrevoList(
                id=l.get("id", 0),
                name=l.get("name", ""),
                total_blacklisted=l.get("totalBlacklisted", 0),
                total_subscribers=l.get("totalSubscribers", 0),
                folder_id=l.get("folderId", 0),
                created_at=l.get("createdAt", ""),
                extra=l,
            )
            for l in lists_data
        ]
        for lst in lists:
            if lst.id:
                self._cached_lists[lst.id] = lst

        return {
            "lists": lists,
            "count": result.get("count", 0),
        }

    def list_contacts_in_list(self, list_id: int,
                              limit: int = 50,
                              offset: int = 0,
                              **kwargs: Any) -> Dict[str, Any]:
        params: Dict[str, Any] = {"limit": limit, "offset": offset}
        params.update(kwargs)

        result = self._api_call(
            f"contacts/lists/{list_id}/contacts", params=params
        )
        contacts_data = result.get("contacts", [])
        contacts = [self._parse_contact(c) for c in contacts_data]
        return {
            "contacts": contacts,
            "count": result.get("count", 0),
        }

    def add_contact_to_list(self, list_id: int,
                            emails: List[str]) -> bool:
        self._api_call(
            f"contacts/lists/{list_id}/contacts/add",
            data={"emails": emails},
            http_method="POST",
        )
        return True

    def remove_contact_from_list(self, list_id: int,
                                 emails: List[str]) -> bool:
        self._api_call(
            f"contacts/lists/{list_id}/contacts/remove",
            data={"emails": emails},
            http_method="POST",
        )
        return True

    def create_folder(self, name: str) -> BrevoFolder:
        result = self._api_call(
            "contacts/folders", data={"name": name}, http_method="POST"
        )
        return BrevoFolder(
            id=result.get("id", 0),
            name=name,
            extra=result,
        )

    def list_folders(self, limit: int = 50,
                     offset: int = 0,
                     **kwargs: Any) -> Dict[str, Any]:
        params: Dict[str, Any] = {"limit": limit, "offset": offset}
        params.update(kwargs)
        result = self._api_call("contacts/folders", params=params)
        folders_data = result.get("folders", [])
        return {
            "folders": [
                BrevoFolder(
                    id=f.get("id", 0),
                    name=f.get("name", ""),
                    total_blacklisted=f.get("totalBlacklisted", 0),
                    total_subscribers=f.get("totalSubscribers", 0),
                    unique_subscribers=f.get("uniqueSubscribers", 0),
                    extra=f,
                )
                for f in folders_data
            ],
            "count": result.get("count", 0),
        }

    # ------------------------------------------------------------------
    # SMS
    # ------------------------------------------------------------------

    def send_sms(self, message: BrevoSmsMessage) -> BrevoSmsResponse:
        payload: Dict[str, Any] = {
            "sender": message.sender,
            "recipient": message.recipient,
            "content": message.content,
            "type": message.type,
        }
        if message.tag:
            payload["tag"] = message.tag
        if message.web_url:
            payload["webUrl"] = message.web_url
        if message.scheduled_at:
            payload["scheduledAt"] = message.scheduled_at

        result = self._api_call(
            "transactionalSMS/sms", data=payload, http_method="POST"
        )
        return BrevoSmsResponse(
            reference=result.get("reference", ""),
            message_id=result.get("messageId", 0),
            sms_count=result.get("smsCount", 0),
            used_credits=result.get("usedCredits", 0),
            remaining_credits=result.get("remainingCredits", 0.0),
            extra=result,
        )

    def send_sms_simple(self, sender: str, recipient: str,
                        content: str, tag: str = "") -> BrevoSmsResponse:
        message = BrevoSmsMessage(
            sender=sender,
            recipient=recipient,
            content=content,
            tag=tag,
        )
        return self.send_sms(message)

    def get_sms_events(self, limit: int = 50,
                       offset: int = 0,
                       phone_number: str = "",
                       event: Optional[BrevoSmsEventType] = None,
                       **kwargs: Any) -> Dict[str, Any]:
        params: Dict[str, Any] = {"limit": limit, "offset": offset}
        if phone_number:
            params["phoneNumber"] = phone_number
        if event:
            params["event"] = event.value
        params.update(kwargs)

        result = self._api_call("transactionalSMS/history", params=params)
        return result

    # ------------------------------------------------------------------
    # Templates
    # ------------------------------------------------------------------

    def get_template(self, template_id: int) -> BrevoTemplate:
        result = self._api_call(f"smtp/templates/{template_id}")
        return BrevoTemplate(
            id=result.get("id", template_id),
            name=result.get("name", ""),
            subject=result.get("subject", ""),
            html_content=result.get("htmlContent", ""),
            text_content=result.get("textContent", ""),
            sender=result.get("sender", {}),
            reply_to=result.get("replyTo", ""),
            to_field=result.get("toField", ""),
            status=result.get("isActive", True),
            created_at=result.get("createdAt", ""),
            modified_at=result.get("modifiedAt", ""),
            extra=result,
        )

    def list_templates(self, limit: int = 50,
                       offset: int = 0,
                       status: bool = True,
                       **kwargs: Any) -> Dict[str, Any]:
        params: Dict[str, Any] = {
            "limit": limit,
            "offset": offset,
            "status": "true" if status else "false",
        }
        params.update(kwargs)

        result = self._api_call("smtp/templates", params=params)
        templates_data = result.get("templates", [])
        return {
            "templates": [
                BrevoTemplate(
                    id=t.get("id", 0),
                    name=t.get("name", ""),
                    subject=t.get("subject", ""),
                    sender=t.get("sender", {}),
                    status=t.get("isActive", True),
                    created_at=t.get("createdAt", ""),
                    extra=t,
                )
                for t in templates_data
            ],
            "count": result.get("count", 0),
        }

    # ------------------------------------------------------------------
    # Email campaigns
    # ------------------------------------------------------------------

    def create_email_campaign(self, name: str,
                              subject: str,
                              sender_name: str,
                              sender_email: str,
                              html_content: str = "",
                              text_content: str = "",
                              list_ids: Optional[List[int]] = None,
                              **kwargs: Any) -> BrevoEmailCampaign:
        payload: Dict[str, Any] = {
            "name": name,
            "subject": subject,
            "sender": {"name": sender_name, "email": sender_email},
            "htmlContent": html_content,
        }
        if text_content:
            payload["textContent"] = text_content
        if list_ids:
            payload["recipients"] = {"listIds": list_ids}
        payload.update(kwargs)

        result = self._api_call(
            "emailCampaigns", data=payload, http_method="POST"
        )
        return BrevoEmailCampaign(
            id=result.get("id", 0),
            name=name,
            subject=subject,
            html_content=html_content,
            extra=result,
        )

    def get_email_campaign(self, campaign_id: int) -> BrevoEmailCampaign:
        result = self._api_call(f"emailCampaigns/{campaign_id}")
        return BrevoEmailCampaign(
            id=result.get("id", campaign_id),
            name=result.get("name", ""),
            subject=result.get("subject", ""),
            status=result.get("status", "draft"),
            sender=result.get("sender", {}),
            html_content=result.get("htmlContent", ""),
            text_content=result.get("textContent", ""),
            reply_to=result.get("replyTo", ""),
            recipients=result.get("recipients", {}),
            scheduled_at=result.get("scheduledAt", ""),
            sent_at=result.get("sentAt", ""),
            stats=result.get("statistics", {}),
            created_at=result.get("createdAt", ""),
            modified_at=result.get("modifiedAt", ""),
            extra=result,
        )

    def send_email_campaign_now(self, campaign_id: int) -> bool:
        self._api_call(
            f"emailCampaigns/{campaign_id}/sendNow", http_method="POST"
        )
        return True

    def list_email_campaigns(self, limit: int = 50,
                             offset: int = 0,
                             status: str = "",
                             **kwargs: Any) -> Dict[str, Any]:
        params: Dict[str, Any] = {"limit": limit, "offset": offset}
        if status:
            params["status"] = status
        params.update(kwargs)

        result = self._api_call("emailCampaigns", params=params)
        campaigns_data = result.get("campaigns", [])
        return {
            "campaigns": [
                BrevoEmailCampaign(
                    id=c.get("id", 0),
                    name=c.get("name", ""),
                    subject=c.get("subject", ""),
                    status=c.get("status", ""),
                    sender=c.get("sender", {}),
                    recipients=c.get("recipients", {}),
                    scheduled_at=c.get("scheduledAt", ""),
                    sent_at=c.get("sentAt", ""),
                    stats=c.get("statistics", {}),
                    created_at=c.get("createdAt", ""),
                    modified_at=c.get("modifiedAt", ""),
                    extra=c,
                )
                for c in campaigns_data
            ],
            "count": result.get("count", 0),
        }

    # ------------------------------------------------------------------
    # Webhooks
    # ------------------------------------------------------------------

    def create_webhook(self, url: str,
                       events: List[str],
                       description: str = "",
                       webhook_type: str = "transactional",
                       **kwargs: Any) -> BrevoWebhook:
        payload: Dict[str, Any] = {
            "url": url,
            "events": events,
            "description": description,
            "type": webhook_type,
        }
        payload.update(kwargs)

        result = self._api_call(
            "webhooks", data=payload, http_method="POST"
        )
        webhook = BrevoWebhook(
            id=result.get("id", 0),
            url=url,
            description=description,
            events=events,
            type=webhook_type,
            extra=result,
        )
        self._webhooks.append(webhook)
        return webhook

    def list_webhooks(self, webhook_type: str = "transactional"
                       ) -> List[BrevoWebhook]:
        params = {"type": webhook_type}
        result = self._api_call("webhooks", params=params)
        webhooks_data = result.get("webhooks", [])
        webhooks = [
            BrevoWebhook(
                id=w.get("id", 0),
                url=w.get("url", ""),
                description=w.get("description", ""),
                events=w.get("events", []),
                type=w.get("type", ""),
                domain=w.get("domain", ""),
                extra=w,
            )
            for w in webhooks_data
        ]
        return webhooks

    def delete_webhook(self, webhook_id: int) -> bool:
        self._api_call(f"webhooks/{webhook_id}", http_method="DELETE")
        self._webhooks = [w for w in self._webhooks if w.id != webhook_id]
        return True

    def verify_webhook(self, request_body: str,
                       brevo_signature: str,
                       webhook_secret: str) -> bool:
        """
        Verify a Brevo webhook signature.

        Args:
            request_body: Raw request body.
            brevo_signature: Brevo-Signature header.
            webhook_secret: Webhook secret key.

        Returns:
            True if the webhook is verified.
        """
        expected = hmac.new(
            webhook_secret.encode("utf-8"),
            request_body.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()
        return hmac.compare_digest(brevo_signature, expected)

    # ------------------------------------------------------------------
    # Statistics
    # ------------------------------------------------------------------

    def get_aggregated_report(self, start_date: str,
                              end_date: str,
                              tag: str = "",
                              **kwargs: Any) -> Dict[str, Any]:
        params: Dict[str, Any] = {
            "startDate": start_date,
            "endDate": end_date,
        }
        if tag:
            params["tag"] = tag
        params.update(kwargs)

        result = self._api_call("smtp/statistics/aggregatedReport", params=params)
        return result

    def get_list_stats(self, list_id: int) -> Dict[str, Any]:
        result = self._api_call(f"contacts/lists/{list_id}/stats")
        return result

    # ------------------------------------------------------------------
    # Internal API call helper
    # ------------------------------------------------------------------

    def _api_call(self, path: str,
                  params: Optional[Dict[str, Any]] = None,
                  data: Optional[Dict[str, Any]] = None,
                  http_method: str = "GET") -> Dict[str, Any]:
        self._check_rate_limit()
        self._mark_request()

        url = self._build_url(path)
        if params:
            from urllib.parse import urlencode
            url = f"{url}?{urlencode(params)}"

        headers = {
            "api-key": self.config.api_key or self.config.access_token or "",
            "Content-Type": "application/json",
            "Accept": "application/json",
        }

        body = json.dumps(data) if data else None
        self._log_request(http_method, url, headers, body)

        try:
            import urllib.request
            import urllib.error

            req = urllib.request.Request(
                url,
                data=body.encode("utf-8") if body else None,
                headers=headers,
                method=http_method,
            )

            with urllib.request.urlopen(
                req, timeout=self.config.timeout
            ) as response:
                response_body = response.read().decode("utf-8")
                self._log_response(
                    http_method, url, response.status, response_body
                )
                if response_body:
                    return json.loads(response_body)
                return {}

        except urllib.error.HTTPError as e:
            response_body = e.read().decode("utf-8", errors="replace")
            self._log_response(http_method, url, e.code, response_body)
            if e.code == 429:
                self._rate_limit_hits += 1
                raise RateLimitError(
                    "Rate limited by Brevo",
                    retry_after=60,
                    adapter_name=self.config.name,
                )
            if e.code in (401, 403):
                raise self._handle_error(
                    "Authentication failed: invalid Brevo API key",
                    status_code=e.code,
                    response_body=response_body,
                )
            raise self._handle_error(
                f"HTTP error {e.code}: {e.reason}",
                status_code=e.code,
                response_body=response_body,
            )
        except (IntegrationError, RateLimitError):
            raise
        except Exception as e:
            raise self._handle_error(f"Request error: {e}")

    def __repr__(self) -> str:
        return (
            f"<BrevoAdapter name={self.config.name} "
            f"status={self._status.value} "
            f"account={self._account_email or 'unknown'}>"
        )
