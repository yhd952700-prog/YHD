"""
Zendesk Integration Adapter for LiuHao AI OS.

Provides integration with Zendesk for tickets (create, list, update, solve),
users and organizations, comments, attachments, search,
ticket fields and forms, and satisfaction ratings.
"""

from __future__ import annotations

import base64
import json
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple

from .base_adapter import (
    AuthType,
    BaseIntegrationAdapter,
    ConnectionStatus,
    IntegrationConfig,
    IntegrationError,
    IntegrationStatus,
    RateLimitError,
)


class TicketStatus(str, Enum):
    """Zendesk ticket statuses."""
    NEW = "new"
    OPEN = "open"
    PENDING = "pending"
    HOLD = "hold"
    SOLVED = "solved"
    CLOSED = "closed"


class TicketPriority(str, Enum):
    """Zendesk ticket priorities."""
    LOW = "low"
    NORMAL = "normal"
    HIGH = "high"
    URGENT = "urgent"


class TicketType(str, Enum):
    """Zendesk ticket types."""
    PROBLEM = "problem"
    INCIDENT = "incident"
    QUESTION = "question"
    TASK = "task"


class UserRole(str, Enum):
    """Zendesk user roles."""
    END_USER = "end-user"
    AGENT = "agent"
    ADMIN = "admin"


@dataclass
class ZendeskTicket:
    """Represents a Zendesk ticket."""
    id: int
    subject: str
    description: str = ""
    status: str = "new"
    priority: str = "normal"
    ticket_type: str = ""
    requester_id: int = 0
    assignee_id: int = 0
    group_id: int = 0
    organization_id: int = 0
    tags: List[str] = field(default_factory=list)
    custom_fields: List[Dict[str, Any]] = field(default_factory=list)
    created_at: str = ""
    updated_at: str = ""
    solved_at: str = ""
    due_at: str = ""
    url: str = ""
    satisfaction_rating: Dict[str, Any] = field(default_factory=dict)
    extra: Dict[str, Any] = field(default_factory=dict)


@dataclass
class ZendeskComment:
    """Represents a ticket comment."""
    id: int
    body: str
    author_id: int = 0
    public: bool = True
    created_at: str = ""
    attachments: List[Dict[str, Any]] = field(default_factory=list)
    via: Dict[str, Any] = field(default_factory=dict)
    extra: Dict[str, Any] = field(default_factory=dict)


@dataclass
class ZendeskUser:
    """Represents a Zendesk user."""
    id: int
    name: str
    email: str = ""
    role: str = "end-user"
    active: bool = True
    verified: bool = False
    organization_id: int = 0
    phone: str = ""
    time_zone: str = ""
    locale: str = ""
    created_at: str = ""
    updated_at: str = ""
    last_login_at: str = ""
    tags: List[str] = field(default_factory=list)
    user_fields: Dict[str, Any] = field(default_factory=dict)
    extra: Dict[str, Any] = field(default_factory=dict)


@dataclass
class ZendeskOrganization:
    """Represents a Zendesk organization."""
    id: int
    name: str
    details: str = ""
    notes: str = ""
    group_id: int = 0
    shared_tickets: bool = False
    shared_comments: bool = False
    tags: List[str] = field(default_factory=list)
    organization_fields: Dict[str, Any] = field(default_factory=dict)
    created_at: str = ""
    updated_at: str = ""
    external_id: str = ""
    url: str = ""
    extra: Dict[str, Any] = field(default_factory=dict)


@dataclass
class ZendeskAttachment:
    """Represents a Zendesk attachment."""
    id: int
    file_name: str
    content_url: str = ""
    content_type: str = ""
    size: int = 0
    width: Optional[int] = None
    height: Optional[int] = None
    inline: bool = False
    created_at: str = ""
    token: str = ""
    extra: Dict[str, Any] = field(default_factory=dict)


@dataclass
class ZendeskSearchResult:
    """Result from a Zendesk search."""
    results: List[Dict[str, Any]]
    total: int
    next_page: Optional[str] = None
    previous_page: Optional[str] = None
    count: int = 0


@dataclass
class ZendeskTicketField:
    """Represents a custom ticket field."""
    id: int
    title: str
    type_: str = ""
    description: str = ""
    required: bool = False
    active: bool = True
    position: int = 0
    custom_field_options: List[Dict[str, Any]] = field(default_factory=list)
    extra: Dict[str, Any] = field(default_factory=dict)


@dataclass
class ZendeskTicketForm:
    """Represents a ticket form."""
    id: int
    name: str
    display_name: str = ""
    description: str = ""
    active: bool = True
    position: int = 0
    ticket_field_ids: List[int] = field(default_factory=list)
    extra: Dict[str, Any] = field(default_factory=dict)


@dataclass
class ZendeskSatisfactionRating:
    """Represents a satisfaction rating."""
    id: int
    score: str = ""
    comment: str = ""
    ticket_id: int = 0
    assignee_id: int = 0
    group_id: int = 0
    requester_id: int = 0
    created_at: str = ""
    updated_at: str = ""
    reason: str = ""
    reason_id: int = 0
    extra: Dict[str, Any] = field(default_factory=dict)


class ZendeskAdapter(BaseIntegrationAdapter):
    """
    Zendesk integration adapter for LiuHao AI OS.

    Supports Zendesk REST API v2 with:
    - Ticket management (create, list, update, solve, delete)
    - Comments (public and internal)
    - Attachments upload
    - Users and organizations management
    - Full-text search across tickets, users, orgs
    - Custom ticket fields and forms
    - Satisfaction ratings (CSAT)
    """

    def __init__(self, config: IntegrationConfig):
        """
        Initialize Zendesk adapter.

        Uses API token authentication via Basic Auth.

        Args:
            config: Integration config.
                    base_url: https://{subdomain}.zendesk.com/api/v2/
                    username: Agent email address
                    api_key: Zendesk API token (append /token to email)
                    auth_type: BASIC_AUTH (email/token format)
        """
        if config.auth_type == AuthType.NONE:
            config.auth_type = AuthType.BASIC_AUTH
        if config.base_url and not config.base_url.endswith("/"):
            config.base_url += "/"
        super().__init__(config)
        self._current_user_id: Optional[int] = None
        self._subdomain: Optional[str] = None

    # ------------------------------------------------------------------
    # Base interface
    # ------------------------------------------------------------------

    def connect(self) -> bool:
        """Connect to Zendesk and validate credentials."""
        self._status = ConnectionStatus.CONNECTING
        self._logger.info("Connecting to Zendesk API...")

        try:
            data = self._api_call("GET", "users/me")
            user = data.get("user", {})
            self._current_user_id = user.get("id")
            self._status = ConnectionStatus.CONNECTED
            self._connected_at = time.time()
            self._logger.info(
                "Connected to Zendesk as %s", user.get("name", ""),
            )
            return True
        except IntegrationError:
            raise
        except Exception as e:
            self._status = ConnectionStatus.ERROR
            raise self._handle_error(f"Connection error: {e}")

    def disconnect(self) -> bool:
        """Disconnect from Zendesk."""
        self._status = ConnectionStatus.DISCONNECTED
        self._current_user_id = None
        self._session = None
        self._logger.info("Disconnected from Zendesk")
        return True

    def is_connected(self) -> bool:
        return self._status == ConnectionStatus.CONNECTED

    def get_status(self) -> IntegrationStatus:
        return self._build_status({
            "current_user_id": self._current_user_id,
            "subdomain": self._subdomain,
        })

    def test_connection(self) -> bool:
        try:
            if not self.is_connected():
                return False
            result = self._api_call("GET", "users/me")
            return "user" in result
        except Exception:
            return False

    # ------------------------------------------------------------------
    # Tickets
    # ------------------------------------------------------------------

    def list_tickets(self,
                     status: Optional[TicketStatus] = None,
                     priority: Optional[TicketPriority] = None,
                     assignee_id: Optional[int] = None,
                     requester_id: Optional[int] = None,
                     organization_id: Optional[int] = None,
                     group_id: Optional[int] = None,
                     sort_by: str = "created_at",
                     sort_order: str = "desc",
                     page_size: int = 100,
                     page: Optional[int] = None,
                     ) -> Tuple[List[ZendeskTicket], int, int]:
        """
        List tickets with filters.

        Returns:
            Tuple of (tickets list, total_count, total_pages).
        """
        params: Dict[str, Any] = {
            "sort_by": sort_by,
            "sort_order": sort_order,
            "per_page": page_size,
        }
        if status:
            params["status"] = status.value
        if priority:
            params["priority"] = priority.value
        if assignee_id:
            params["assignee_id"] = assignee_id
        if requester_id:
            params["requester_id"] = requester_id
        if organization_id:
            params["organization_id"] = organization_id
        if group_id:
            params["group_id"] = group_id
        if page:
            params["page"] = page

        data = self._api_call("GET", "tickets", params=params)
        tickets = [self._parse_ticket(t) for t in data.get("tickets", [])]
        count = data.get("count", 0)
        total_pages = -(-count // page_size) if count else 0
        return tickets, count, total_pages

    def get_ticket(self, ticket_id: int) -> ZendeskTicket:
        """Get a ticket by ID."""
        data = self._api_call("GET", f"tickets/{ticket_id}")
        return self._parse_ticket(data.get("ticket", {}))

    def create_ticket(self, subject: str,
                      comment_body: str = "",
                      requester_name: str = "",
                      requester_email: str = "",
                      requester_id: Optional[int] = None,
                      priority: str = "normal",
                      status: str = "new",
                      ticket_type: str = "",
                      tags: Optional[List[str]] = None,
                      custom_fields: Optional[List[Dict[str, Any]]] = None,
                      assignee_id: Optional[int] = None,
                      group_id: Optional[int] = None,
                      organization_id: Optional[int] = None,
                      **kwargs: Any) -> ZendeskTicket:
        """
        Create a new ticket.

        Args:
            subject: Ticket subject/title.
            comment_body: Initial comment body.
            requester_name: Requester name (for new users).
            requester_email: Requester email (for new users).
            requester_id: Existing requester user ID.
            priority: Ticket priority.
            status: Initial status.
            ticket_type: Type of ticket.
            tags: List of tags.
            custom_fields: Custom fields array.
            assignee_id: Agent ID to assign.
            group_id: Group ID.
            organization_id: Organization ID.

        Returns:
            Created ZendeskTicket.
        """
        ticket: Dict[str, Any] = {
            "subject": subject,
            "priority": priority,
            "status": status,
        }
        if ticket_type:
            ticket["type"] = ticket_type
        if tags:
            ticket["tags"] = tags
        if custom_fields:
            ticket["custom_fields"] = custom_fields
        if assignee_id is not None:
            ticket["assignee_id"] = assignee_id
        if group_id is not None:
            ticket["group_id"] = group_id
        if organization_id is not None:
            ticket["organization_id"] = organization_id
        if requester_id is not None:
            ticket["requester_id"] = requester_id
        if kwargs:
            ticket.update(kwargs)

        # Build comment
        comment: Dict[str, Any] = {"body": comment_body or subject}
        if requester_name and requester_email and not requester_id:
            ticket["requester"] = {
                "name": requester_name,
                "email": requester_email,
            }

        ticket["comment"] = comment

        data = self._api_call(
            "POST", "tickets", json_body={"ticket": ticket},
        )
        return self._parse_ticket(data.get("ticket", {}))

    def update_ticket(self, ticket_id: int,
                      comment_body: str = "",
                      comment_public: bool = True,
                      **kwargs: Any) -> ZendeskTicket:
        """
        Update a ticket.

        Args:
            ticket_id: Ticket ID.
            comment_body: Comment to add (if any).
            comment_public: Whether comment is public.
            **kwargs: Ticket fields to update.

        Returns:
            Updated ZendeskTicket.
        """
        ticket: Dict[str, Any] = {**kwargs}
        if comment_body:
            ticket["comment"] = {
                "body": comment_body,
                "public": comment_public,
            }

        data = self._api_call(
            "PUT", f"tickets/{ticket_id}",
            json_body={"ticket": ticket},
        )
        return self._parse_ticket(data.get("ticket", {}))

    def solve_ticket(self, ticket_id: int,
                     comment_body: str = "",
                     comment_public: bool = True) -> ZendeskTicket:
        """Mark a ticket as solved."""
        return self.update_ticket(
            ticket_id,
            comment_body=comment_body,
            comment_public=comment_public,
            status="solved",
        )

    def close_ticket(self, ticket_id: int) -> ZendeskTicket:
        """Close a ticket (must be solved first)."""
        return self.update_ticket(ticket_id, status="closed")

    def assign_ticket(self, ticket_id: int,
                      assignee_id: int,
                      comment_body: str = "") -> ZendeskTicket:
        """Assign a ticket to an agent."""
        return self.update_ticket(
            ticket_id,
            comment_body=comment_body,
            assignee_id=assignee_id,
        )

    def delete_ticket(self, ticket_id: int) -> bool:
        """Delete a ticket. Returns True on success."""
        try:
            self._api_call("DELETE", f"tickets/{ticket_id}")
            return True
        except Exception:
            return False

    def get_ticket_count(self, status: Optional[TicketStatus] = None,
                         view_id: Optional[int] = None) -> int:
        """Get count of tickets matching criteria."""
        if view_id:
            data = self._api_call("GET", f"views/{view_id}/count")
            return data.get("view_count", {}).get("value", 0)
        else:
            _, count, _ = self.list_tickets(status=status, page_size=1)
            return count

    # ------------------------------------------------------------------
    # Comments
    # ------------------------------------------------------------------

    def list_comments(self, ticket_id: int,
                      sort_order: str = "asc",
                      include_inline_images: bool = False,
                      page_size: int = 100,
                      ) -> Tuple[List[ZendeskComment], int]:
        """
        List comments on a ticket.

        Returns:
            Tuple of (comments list, total_count).
        """
        params: Dict[str, Any] = {
            "sort_order": sort_order,
            "per_page": page_size,
        }
        if include_inline_images:
            params["include_inline_images"] = "true"

        data = self._api_call(
            "GET", f"tickets/{ticket_id}/comments", params=params,
        )
        comments = [self._parse_comment(c) for c in data.get("comments", [])]
        count = data.get("count", 0)
        return comments, count

    def add_comment(self, ticket_id: int, body: str,
                    public: bool = True,
                    author_id: Optional[int] = None,
                    uploads: Optional[List[str]] = None
                    ) -> ZendeskComment:
        """
        Add a comment to a ticket.

        Args:
            ticket_id: Ticket ID.
            body: Comment text body.
            public: Whether comment is visible to end user.
            author_id: Author user ID (defaults to authenticated user).
            uploads: List of attachment tokens.

        Returns:
            Created comment (via updated ticket).
        """
        ticket_data: Dict[str, Any] = {
            "comment": {
                "body": body,
                "public": public,
            },
        }
        if author_id:
            ticket_data["comment"]["author_id"] = author_id
        if uploads:
            ticket_data["comment"]["uploads"] = uploads

        data = self._api_call(
            "PUT", f"tickets/{ticket_id}",
            json_body={"ticket": ticket_data},
        )
        # Return the last comment from the updated ticket
        # (Zendesk returns the full ticket with audit)
        ticket = data.get("ticket", {})
        # We need to get the actual comment from audits, but for simplicity
        # return a constructed comment object
        return ZendeskComment(
            id=0,
            body=body,
            public=public,
            author_id=author_id or 0,
            extra={"ticket_updated": ticket},
        )

    def get_comment(self, ticket_id: int, comment_id: int) -> ZendeskComment:
        """Get a specific comment."""
        data = self._api_call(
            "GET", f"tickets/{ticket_id}/comments/{comment_id}",
        )
        return self._parse_comment(data.get("comment", {}))

    # ------------------------------------------------------------------
    # Attachments
    # ------------------------------------------------------------------

    def upload_attachment(self, filename: str,
                          file_content: bytes,
                          content_type: str = "application/octet-stream",
                          token: str = "") -> ZendeskAttachment:
        """
        Upload an attachment.

        Args:
            filename: Name of the file.
            file_content: Raw file bytes.
            content_type: MIME type.
            token: Optional token for chunked uploads.

        Returns:
            ZendeskAttachment with upload token.
        """
        url = self._build_url(f"uploads?filename={filename}")
        if token:
            url += f"&token={token}"

        self._check_rate_limit()
        self._mark_request()

        auth_header = self._get_auth_header()
        headers = {
            "Authorization": auth_header,
            "Content-Type": content_type,
            "Accept": "application/json",
        }

        self._log_request("POST", url, headers, f"<{len(file_content)} bytes>")

        try:
            import urllib.request
            req = urllib.request.Request(
                url, data=file_content, headers=headers, method="POST",
            )
            with urllib.request.urlopen(req, timeout=self.config.timeout) as resp:
                resp_body = resp.read().decode("utf-8")
                self._log_response("POST", url, resp.status, resp_body)
                data = json.loads(resp_body)
                upload = data.get("upload", {})
                return self._parse_attachment(upload)
        except urllib.error.HTTPError as e:
            resp_body = e.read().decode("utf-8", errors="replace")
            raise self._handle_error(
                f"Upload failed: {e.code}",
                status_code=e.code,
                response_body=resp_body,
            )

    def get_attachment(self, attachment_id: int) -> ZendeskAttachment:
        """Get attachment info by ID."""
        data = self._api_call("GET", f"attachments/{attachment_id}")
        return self._parse_attachment(data.get("attachment", {}))

    def delete_attachment(self, attachment_id: int) -> bool:
        """Delete an attachment. Returns True on success."""
        try:
            self._api_call("DELETE", f"attachments/{attachment_id}")
            return True
        except Exception:
            return False

    # ------------------------------------------------------------------
    # Users
    # ------------------------------------------------------------------

    def list_users(self,
                   role: Optional[UserRole] = None,
                   organization_id: Optional[int] = None,
                   page_size: int = 100,
                   page: Optional[int] = None,
                   ) -> Tuple[List[ZendeskUser], int]:
        """List users."""
        params: Dict[str, Any] = {"per_page": page_size}
        if role:
            params["role"] = role.value
        if organization_id:
            params["organization_id"] = organization_id
        if page:
            params["page"] = page

        data = self._api_call("GET", "users", params=params)
        users = [self._parse_user(u) for u in data.get("users", [])]
        count = data.get("count", 0)
        return users, count

    def get_user(self, user_id: int) -> ZendeskUser:
        """Get a user by ID."""
        data = self._api_call("GET", f"users/{user_id}")
        return self._parse_user(data.get("user", {}))

    def get_current_user(self) -> ZendeskUser:
        """Get the currently authenticated user."""
        data = self._api_call("GET", "users/me")
        return self._parse_user(data.get("user", {}))

    def create_user(self, name: str,
                    email: str = "",
                    role: str = "end-user",
                    organization_id: Optional[int] = None,
                    phone: str = "",
                    verified: bool = False,
                    tags: Optional[List[str]] = None,
                    **kwargs: Any) -> ZendeskUser:
        """Create a new user."""
        user: Dict[str, Any] = {
            "name": name,
            "email": email,
            "role": role,
            "verified": verified,
        }
        if organization_id:
            user["organization_id"] = organization_id
        if phone:
            user["phone"] = phone
        if tags:
            user["tags"] = tags
        if kwargs:
            user.update(kwargs)

        data = self._api_call(
            "POST", "users", json_body={"user": user},
        )
        return self._parse_user(data.get("user", {}))

    def update_user(self, user_id: int, **kwargs: Any) -> ZendeskUser:
        """Update a user."""
        data = self._api_call(
            "PUT", f"users/{user_id}", json_body={"user": kwargs},
        )
        return self._parse_user(data.get("user", {}))

    def delete_user(self, user_id: int) -> bool:
        """Delete a user. Returns True on success."""
        try:
            self._api_call("DELETE", f"users/{user_id}")
            return True
        except Exception:
            return False

    def search_users(self, query: str,
                     role: Optional[UserRole] = None,
                     page_size: int = 100) -> List[ZendeskUser]:
        """Search users by name, email, etc."""
        params: Dict[str, Any] = {"query": query, "per_page": page_size}
        if role:
            params["role"] = role.value

        data = self._api_call("GET", "users/search", params=params)
        return [self._parse_user(u) for u in data.get("users", [])]

    # ------------------------------------------------------------------
    # Organizations
    # ------------------------------------------------------------------

    def list_organizations(self,
                           page_size: int = 100,
                           page: Optional[int] = None,
                           ) -> Tuple[List[ZendeskOrganization], int]:
        """List organizations."""
        params: Dict[str, Any] = {"per_page": page_size}
        if page:
            params["page"] = page

        data = self._api_call("GET", "organizations", params=params)
        orgs = [self._parse_organization(o) for o in data.get("organizations", [])]
        count = data.get("count", 0)
        return orgs, count

    def get_organization(self, org_id: int) -> ZendeskOrganization:
        """Get an organization by ID."""
        data = self._api_call("GET", f"organizations/{org_id}")
        return self._parse_organization(data.get("organization", {}))

    def create_organization(self, name: str,
                            details: str = "",
                            notes: str = "",
                            group_id: Optional[int] = None,
                            shared_tickets: bool = False,
                            tags: Optional[List[str]] = None,
                            **kwargs: Any) -> ZendeskOrganization:
        """Create a new organization."""
        org: Dict[str, Any] = {
            "name": name,
            "details": details,
            "notes": notes,
            "shared_tickets": shared_tickets,
        }
        if group_id:
            org["group_id"] = group_id
        if tags:
            org["tags"] = tags
        if kwargs:
            org.update(kwargs)

        data = self._api_call(
            "POST", "organizations", json_body={"organization": org},
        )
        return self._parse_organization(data.get("organization", {}))

    def update_organization(self, org_id: int, **kwargs: Any) -> ZendeskOrganization:
        """Update an organization."""
        data = self._api_call(
            "PUT", f"organizations/{org_id}",
            json_body={"organization": kwargs},
        )
        return self._parse_organization(data.get("organization", {}))

    def delete_organization(self, org_id: int) -> bool:
        """Delete an organization. Returns True on success."""
        try:
            self._api_call("DELETE", f"organizations/{org_id}")
            return True
        except Exception:
            return False

    # ------------------------------------------------------------------
    # Search
    # ------------------------------------------------------------------

    def search(self, query: str,
               search_type: str = "ticket",
               sort_by: str = "created_at",
               sort_order: str = "desc",
               page_size: int = 100,
               page: Optional[int] = None,
               ) -> ZendeskSearchResult:
        """
        Search across Zendesk.

        Args:
            query: Search query string.
            search_type: "ticket", "user", "organization", "article", etc.
            sort_by: Sort field.
            sort_order: "asc" or "desc".
            page_size: Results per page.
            page: Page number.

        Returns:
            ZendeskSearchResult with results and pagination.
        """
        params: Dict[str, Any] = {
            "query": query,
            "sort_by": sort_by,
            "sort_order": sort_order,
            "per_page": page_size,
        }
        if search_type:
            params["filter[type]"] = search_type
        if page:
            params["page"] = page

        data = self._api_call("GET", "search", params=params)
        return ZendeskSearchResult(
            results=data.get("results", []),
            total=data.get("count", 0),
            next_page=data.get("next_page"),
            previous_page=data.get("previous_page"),
            count=data.get("count", 0),
        )

    def search_tickets(self, query: str, **kwargs: Any) -> List[ZendeskTicket]:
        """Search for tickets only."""
        result = self.search(query, search_type="ticket", **kwargs)
        return [self._parse_ticket(t) for t in result.results]

    # ------------------------------------------------------------------
    # Ticket fields
    # ------------------------------------------------------------------

    def list_ticket_fields(self,
                           active_only: bool = False
                           ) -> List[ZendeskTicketField]:
        """List all ticket fields."""
        data = self._api_call("GET", "ticket_fields")
        fields = [self._parse_ticket_field(f)
                  for f in data.get("ticket_fields", [])]
        if active_only:
            fields = [f for f in fields if f.active]
        return fields

    def get_ticket_field(self, field_id: int) -> ZendeskTicketField:
        """Get a specific ticket field."""
        data = self._api_call("GET", f"ticket_fields/{field_id}")
        return self._parse_ticket_field(data.get("ticket_field", {}))

    def create_ticket_field(self, title: str,
                            type_: str = "text",
                            description: str = "",
                            required: bool = False,
                            active: bool = True,
                            **kwargs: Any) -> ZendeskTicketField:
        """Create a new ticket field."""
        field: Dict[str, Any] = {
            "title": title,
            "type": type_,
            "description": description,
            "required": required,
            "active": active,
        }
        field.update(kwargs)

        data = self._api_call(
            "POST", "ticket_fields", json_body={"ticket_field": field},
        )
        return self._parse_ticket_field(data.get("ticket_field", {}))

    # ------------------------------------------------------------------
    # Ticket forms
    # ------------------------------------------------------------------

    def list_ticket_forms(self,
                          active_only: bool = False
                          ) -> List[ZendeskTicketForm]:
        """List all ticket forms."""
        data = self._api_call("GET", "ticket_forms")
        forms = [self._parse_ticket_form(f)
                 for f in data.get("ticket_forms", [])]
        if active_only:
            forms = [f for f in forms if f.active]
        return forms

    def get_ticket_form(self, form_id: int) -> ZendeskTicketForm:
        """Get a specific ticket form."""
        data = self._api_call("GET", f"ticket_forms/{form_id}")
        return self._parse_ticket_form(data.get("ticket_form", {}))

    # ------------------------------------------------------------------
    # Satisfaction ratings
    # ------------------------------------------------------------------

    def list_satisfaction_ratings(self,
                                  score: Optional[str] = None,
                                  start_time: Optional[str] = None,
                                  end_time: Optional[str] = None,
                                  page_size: int = 100,
                                  ) -> Tuple[List[ZendeskSatisfactionRating], int]:
        """List satisfaction ratings."""
        params: Dict[str, Any] = {"per_page": page_size}
        if score:
            params["score"] = score
        if start_time:
            params["start_time"] = start_time
        if end_time:
            params["end_time"] = end_time

        data = self._api_call(
            "GET", "satisfaction_ratings", params=params,
        )
        ratings = [self._parse_satisfaction_rating(r)
                   for r in data.get("satisfaction_ratings", [])]
        count = data.get("count", 0)
        return ratings, count

    def get_satisfaction_rating(self, rating_id: int) -> ZendeskSatisfactionRating:
        """Get a specific satisfaction rating."""
        data = self._api_call("GET", f"satisfaction_ratings/{rating_id}")
        return self._parse_satisfaction_rating(
            data.get("satisfaction_rating", {})
        )

    # ------------------------------------------------------------------
    # Parsing helpers
    # ------------------------------------------------------------------

    def _parse_ticket(self, data: Dict[str, Any]) -> ZendeskTicket:
        return ZendeskTicket(
            id=data.get("id", 0),
            subject=data.get("subject", ""),
            description=data.get("description", "") or "",
            status=data.get("status", "new"),
            priority=data.get("priority", "normal"),
            ticket_type=data.get("type", "") or "",
            requester_id=data.get("requester_id", 0),
            assignee_id=data.get("assignee_id", 0),
            group_id=data.get("group_id", 0),
            organization_id=data.get("organization_id", 0),
            tags=data.get("tags", []) or [],
            custom_fields=data.get("custom_fields", []) or [],
            created_at=data.get("created_at", ""),
            updated_at=data.get("updated_at", ""),
            solved_at=data.get("solved_at", "") or "",
            due_at=data.get("due_at", "") or "",
            url=data.get("url", ""),
            satisfaction_rating=data.get("satisfaction_rating", {}) or {},
            extra=data,
        )

    def _parse_comment(self, data: Dict[str, Any]) -> ZendeskComment:
        return ZendeskComment(
            id=data.get("id", 0),
            body=data.get("body", ""),
            author_id=data.get("author_id", 0),
            public=data.get("public", True),
            created_at=data.get("created_at", ""),
            attachments=data.get("attachments", []) or [],
            via=data.get("via", {}) or {},
            extra=data,
        )

    def _parse_user(self, data: Dict[str, Any]) -> ZendeskUser:
        return ZendeskUser(
            id=data.get("id", 0),
            name=data.get("name", ""),
            email=data.get("email", "") or "",
            role=data.get("role", "end-user"),
            active=data.get("active", True),
            verified=data.get("verified", False),
            organization_id=data.get("organization_id", 0),
            phone=data.get("phone", "") or "",
            time_zone=data.get("time_zone", ""),
            locale=data.get("locale", ""),
            created_at=data.get("created_at", ""),
            updated_at=data.get("updated_at", ""),
            last_login_at=data.get("last_login_at", "") or "",
            tags=data.get("tags", []) or [],
            user_fields=data.get("user_fields", {}) or {},
            extra=data,
        )

    def _parse_organization(self, data: Dict[str, Any]) -> ZendeskOrganization:
        return ZendeskOrganization(
            id=data.get("id", 0),
            name=data.get("name", ""),
            details=data.get("details", "") or "",
            notes=data.get("notes", "") or "",
            group_id=data.get("group_id", 0),
            shared_tickets=data.get("shared_tickets", False),
            shared_comments=data.get("shared_comments", False),
            tags=data.get("tags", []) or [],
            organization_fields=data.get("organization_fields", {}) or {},
            created_at=data.get("created_at", ""),
            updated_at=data.get("updated_at", ""),
            external_id=data.get("external_id", "") or "",
            url=data.get("url", ""),
            extra=data,
        )

    def _parse_attachment(self, data: Dict[str, Any]) -> ZendeskAttachment:
        return ZendeskAttachment(
            id=data.get("id", 0),
            file_name=data.get("file_name", ""),
            content_url=data.get("content_url", ""),
            content_type=data.get("content_type", ""),
            size=data.get("size", 0),
            width=data.get("width"),
            height=data.get("height"),
            inline=data.get("inline", False),
            created_at=data.get("created_at", ""),
            token=data.get("token", ""),
            extra=data,
        )

    def _parse_ticket_field(self, data: Dict[str, Any]) -> ZendeskTicketField:
        return ZendeskTicketField(
            id=data.get("id", 0),
            title=data.get("title", ""),
            type_=data.get("type", ""),
            description=data.get("description", "") or "",
            required=data.get("required", False),
            active=data.get("active", True),
            position=data.get("position", 0),
            custom_field_options=data.get("custom_field_options", []) or [],
            extra=data,
        )

    def _parse_ticket_form(self, data: Dict[str, Any]) -> ZendeskTicketForm:
        return ZendeskTicketForm(
            id=data.get("id", 0),
            name=data.get("name", ""),
            display_name=data.get("display_name", "") or "",
            description=data.get("description", "") or "",
            active=data.get("active", True),
            position=data.get("position", 0),
            ticket_field_ids=data.get("ticket_field_ids", []) or [],
            extra=data,
        )

    def _parse_satisfaction_rating(self, data: Dict[str, Any]
                                    ) -> ZendeskSatisfactionRating:
        return ZendeskSatisfactionRating(
            id=data.get("id", 0),
            score=data.get("score", ""),
            comment=data.get("comment", "") or "",
            ticket_id=data.get("ticket_id", 0),
            assignee_id=data.get("assignee_id", 0),
            group_id=data.get("group_id", 0),
            requester_id=data.get("requester_id", 0),
            created_at=data.get("created_at", ""),
            updated_at=data.get("updated_at", ""),
            reason=data.get("reason", "") or "",
            reason_id=data.get("reason_id", 0),
            extra=data,
        )

    # ------------------------------------------------------------------
    # Internal API helpers
    # ------------------------------------------------------------------

    def _get_auth_header(self) -> str:
        """Get the Authorization header value."""
        username = self.config.username or ""
        password = self.config.api_key or self.config.password or ""
        credentials = f"{username}:{password}".encode("utf-8")
        encoded = base64.b64encode(credentials).decode("utf-8")
        return f"Basic {encoded}"

    def _api_call(self, method: str, path: str,
                  params: Optional[Dict[str, Any]] = None,
                  json_body: Optional[Dict[str, Any]] = None,
                  _retry_count: int = 0) -> Any:
        """Make a Zendesk API call with retry."""
        try:
            return self._make_request(method, path, params, json_body)
        except RateLimitError as e:
            if _retry_count < self.config.max_retries:
                wait_time = min(e.retry_after, 60)
                if wait_time <= 0:
                    wait_time = 2 ** _retry_count
                self._logger.info(
                    "Rate limited, retrying in %.1fs (attempt %d/%d)",
                    wait_time, _retry_count + 1, self.config.max_retries,
                )
                time.sleep(wait_time)
                return self._api_call(
                    method, path, params, json_body,
                    _retry_count=_retry_count + 1,
                )
            raise

    def _make_request(self, method: str, path: str,
                      params: Optional[Dict[str, Any]],
                      json_body: Optional[Dict[str, Any]]) -> Any:
        """Execute a single Zendesk API request."""
        self._check_rate_limit()
        self._mark_request()

        url = self._build_url(path)
        headers = {
            "Authorization": self._get_auth_header(),
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": "LiuHao-AI-OS",
        }

        body_str = json.dumps(json_body) if json_body else ""
        self._log_request(method, url, headers, body_str or params)

        try:
            import urllib.request
            from urllib.parse import urlencode

            full_url = url
            if params and method == "GET":
                full_url = f"{url}?{urlencode(params)}"

            data = body_str.encode("utf-8") if body_str and method != "GET" else None
            req = urllib.request.Request(
                full_url, data=data, headers=headers, method=method,
            )

            with urllib.request.urlopen(req, timeout=self.config.timeout) as resp:
                resp_body = resp.read().decode("utf-8")
                self._log_response(method, url, resp.status, resp_body)

                if resp.status == 204:
                    return {}
                if not resp_body:
                    return {}
                return json.loads(resp_body)

        except urllib.error.HTTPError as e:
            resp_body = e.read().decode("utf-8", errors="replace")
            self._log_response(method, url, e.code, resp_body)

            if e.code == 429:
                retry_after = float(e.headers.get("Retry-After", 10))
                self._rate_limit_hits += 1
                raise RateLimitError(
                    f"Zendesk rate limited, retry after {retry_after}s",
                    retry_after=retry_after,
                    adapter_name=self.config.name,
                )

            if e.code in (401, 403):
                self._status = ConnectionStatus.ERROR
                raise self._handle_error(
                    f"Authentication failed (HTTP {e.code})",
                    status_code=e.code,
                    response_body=resp_body,
                )

            raise self._handle_error(
                f"Zendesk API error {e.code}: {e.reason}",
                status_code=e.code,
                response_body=resp_body,
            )
        except (RateLimitError, IntegrationError):
            raise
        except Exception as e:
            raise self._handle_error(f"Request failed: {e}")
