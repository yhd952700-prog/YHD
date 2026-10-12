"""
HubSpot Integration Adapter for LiuHao AI OS.

Provides integration with HubSpot for contacts, companies, deals,
CRM objects, deals pipeline, email campaigns, tickets, analytics, and forms.
"""

from __future__ import annotations

import hashlib
import hmac
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


class DealStage(str, Enum):
    """Common HubSpot deal pipeline stages."""
    APPOINTMENT_SCHEDULED = "appointmentscheduled"
    QUALIFIED_TO_BUY = "qualifiedtobuy"
    PRESENTATION_SCHEDULED = "presentationscheduled"
    DECISION_MAKER_BOUGHT_IN = "decisionmakerboughtin"
    CONTRACT_SENT = "contractsent"
    CLOSED_WON = "closedwon"
    CLOSED_LOST = "closedlost"


class ObjectType(str, Enum):
    """HubSpot CRM object types."""
    CONTACT = "contact"
    COMPANY = "company"
    DEAL = "deal"
    TICKET = "ticket"
    CALL = "call"
    EMAIL = "email"
    MEETING = "meeting"
    NOTE = "note"
    TASK = "task"


class TicketStage(str, Enum):
    """Common HubSpot ticket stages."""
    SUBMITTED = "submitted"
    IN_PROGRESS = "in_progress"
    WAITING_ON_CUSTOMER = "waiting_on_customer"
    ESCALATED = "escalated"
    CLOSED = "closed"


@dataclass
class HubSpotContact:
    """Represents a HubSpot contact."""
    id: str
    email: str = ""
    first_name: str = ""
    last_name: str = ""
    phone: str = ""
    company: str = ""
    website: str = ""
    city: str = ""
    state: str = ""
    country: str = ""
    zip: str = ""
    job_title: str = ""
    hs_object_id: str = ""
    created_at: str = ""
    updated_at: str = ""
    archived: bool = False
    properties: Dict[str, Any] = field(default_factory=dict)
    extra: Dict[str, Any] = field(default_factory=dict)


@dataclass
class HubSpotCompany:
    """Represents a HubSpot company."""
    id: str
    name: str = ""
    domain: str = ""
    description: str = ""
    industry: str = ""
    city: str = ""
    state: str = ""
    country: str = ""
    zip: str = ""
    phone: str = ""
    website: str = ""
    numberofemployees: int = 0
    revenue: float = 0.0
    hs_object_id: str = ""
    created_at: str = ""
    updated_at: str = ""
    archived: bool = False
    properties: Dict[str, Any] = field(default_factory=dict)
    extra: Dict[str, Any] = field(default_factory=dict)


@dataclass
class HubSpotDeal:
    """Represents a HubSpot deal."""
    id: str
    dealname: str = ""
    amount: str = ""
    dealstage: str = ""
    pipeline: str = "default"
    closedate: str = ""
    description: str = ""
    priority: str = ""
    hs_object_id: str = ""
    created_at: str = ""
    updated_at: str = ""
    archived: bool = False
    properties: Dict[str, Any] = field(default_factory=dict)
    extra: Dict[str, Any] = field(default_factory=dict)


@dataclass
class HubSpotTicket:
    """Represents a HubSpot ticket."""
    id: str
    subject: str = ""
    content: str = ""
    hs_pipeline: str = "0"
    hs_pipeline_stage: str = ""
    hs_ticket_priority: str = "MEDIUM"
    hs_object_id: str = ""
    created_at: str = ""
    updated_at: str = ""
    archived: bool = False
    properties: Dict[str, Any] = field(default_factory=dict)
    extra: Dict[str, Any] = field(default_factory=dict)


@dataclass
class HubSpotEmail:
    """Represents a HubSpot marketing email."""
    id: int
    name: str = ""
    subject: str = ""
    type_: str = ""
    state: str = ""
    created: int = 0
    updated: int = 0
    publish_date: int = 0
    stats: Dict[str, Any] = field(default_factory=dict)
    extra: Dict[str, Any] = field(default_factory=dict)


@dataclass
class HubSpotForm:
    """Represents a HubSpot form."""
    id: str
    name: str = ""
    action: str = ""
    method: str = ""
    css_class: str = ""
    redirect: str = ""
    submit_text: str = ""
    form_field_groups: List[Dict[str, Any]] = field(default_factory=list)
    created_at: str = ""
    updated_at: str = ""
    extra: Dict[str, Any] = field(default_factory=dict)


@dataclass
class HubSpotPipeline:
    """Represents a HubSpot deal or ticket pipeline."""
    id: str
    label: str
    stages: List[Dict[str, Any]] = field(default_factory=list)
    archived: bool = False
    created_at: str = ""
    updated_at: str = ""
    extra: Dict[str, Any] = field(default_factory=dict)


@dataclass
class HubSpotAnalyticsReport:
    """Represents an analytics report result."""
    report_type: str
    total: int = 0
    breakdowns: Dict[str, Any] = field(default_factory=dict)
    data: List[Dict[str, Any]] = field(default_factory=list)
    extra: Dict[str, Any] = field(default_factory=dict)


@dataclass
class HubSpotCRMObject:
    """Generic HubSpot CRM object."""
    id: str
    object_type: str
    properties: Dict[str, Any] = field(default_factory=dict)
    created_at: str = ""
    updated_at: str = ""
    archived: bool = False
    extra: Dict[str, Any] = field(default_factory=dict)


class HubSpotAdapter(BaseIntegrationAdapter):
    """
    HubSpot integration adapter for LiuHao AI OS.

    Supports HubSpot API v3 / CRM API with:
    - Contacts, companies, deals management
    - Generic CRM objects (tickets, calls, notes, tasks, etc.)
    - Deal pipelines and stages
    - Email marketing campaigns
    - Tickets (service hub)
    - Analytics and reporting
    - Forms
    """

    def __init__(self, config: IntegrationConfig):
        """
        Initialize HubSpot adapter.

        Uses OAuth access token or private app token.

        Args:
            config: Integration config.
                    access_token: HubSpot access token (OAuth or private app)
                    base_url: https://api.hubapi.com/
                    auth_type: BEARER_TOKEN
        """
        if config.auth_type == AuthType.NONE:
            config.auth_type = AuthType.BEARER_TOKEN
        if not config.base_url:
            config.base_url = "https://api.hubapi.com/"
        super().__init__(config)
        self._hub_id: Optional[int] = None
        self._app_id: Optional[int] = None
        self._token_info: Optional[Dict[str, Any]] = None

    # ------------------------------------------------------------------
    # Base interface
    # ------------------------------------------------------------------

    def connect(self) -> bool:
        """Connect to HubSpot and validate token."""
        self._status = ConnectionStatus.CONNECTING
        self._logger.info("Connecting to HubSpot API...")

        try:
            # Validate token using the access tokens info endpoint
            data = self._api_call("GET", "oauth/v1/access-tokens/info")
            self._hub_id = data.get("hub_id")
            self._app_id = data.get("app_id")
            self._token_info = data
            self._status = ConnectionStatus.CONNECTED
            self._connected_at = time.time()
            self._logger.info(
                "Connected to HubSpot (Hub ID: %s)", self._hub_id,
            )
            return True
        except Exception as e:
            # Fallback: try CRM API to validate
            try:
                self._api_call(
                    "GET", "crm/v3/objects/contacts",
                    params={"limit": 1},
                )
                self._status = ConnectionStatus.CONNECTED
                self._connected_at = time.time()
                self._logger.info("Connected to HubSpot (via CRM API)")
                return True
            except Exception:
                self._status = ConnectionStatus.ERROR
                raise self._handle_error(f"Connection error: {e}")

    def disconnect(self) -> bool:
        """Disconnect from HubSpot."""
        self._status = ConnectionStatus.DISCONNECTED
        self._hub_id = None
        self._app_id = None
        self._token_info = None
        self._session = None
        self._logger.info("Disconnected from HubSpot")
        return True

    def is_connected(self) -> bool:
        return self._status == ConnectionStatus.CONNECTED

    def get_status(self) -> IntegrationStatus:
        return self._build_status({
            "hub_id": self._hub_id,
            "app_id": self._app_id,
        })

    def test_connection(self) -> bool:
        try:
            if not self.is_connected():
                return False
            result = self._api_call(
                "GET", "crm/v3/objects/contacts",
                params={"limit": 1},
            )
            return "results" in result
        except Exception:
            return False

    # ------------------------------------------------------------------
    # Contacts
    # ------------------------------------------------------------------

    def list_contacts(self,
                      limit: int = 100,
                      after: Optional[str] = None,
                      properties: Optional[List[str]] = None,
                      properties_with_history: bool = False,
                      archived: bool = False,
                      ) -> Tuple[List[HubSpotContact], Optional[str], bool]:
        """
        List contacts.

        Returns:
            Tuple of (contacts list, next_cursor, has_more).
        """
        params: Dict[str, Any] = {"limit": min(limit, 100)}
        if after:
            params["after"] = after
        if properties:
            params["properties"] = ",".join(properties)
        if properties_with_history:
            params["propertiesWithHistory"] = "true"
        if archived:
            params["archived"] = "true"

        data = self._api_call(
            "GET", "crm/v3/objects/contacts", params=params,
        )
        contacts = [self._parse_contact(c) for c in data.get("results", [])]
        paging = data.get("paging", {})
        next_cursor = paging.get("next", {}).get("after")
        has_more = bool(paging.get("next"))
        return contacts, next_cursor, has_more

    def get_contact(self, contact_id: str,
                    properties: Optional[List[str]] = None,
                    id_property: str = "id",
                    archived: bool = False) -> HubSpotContact:
        """Get a contact by ID."""
        params: Dict[str, Any] = {}
        if properties:
            params["properties"] = ",".join(properties)
        if archived:
            params["archived"] = "true"
        if id_property != "id":
            params["idProperty"] = id_property

        path = f"crm/v3/objects/contacts/{contact_id}"
        data = self._api_call("GET", path, params=params)
        return self._parse_contact(data)

    def get_contact_by_email(self, email: str,
                             properties: Optional[List[str]] = None
                             ) -> Optional[HubSpotContact]:
        """Get a contact by email address."""
        try:
            return self.get_contact(
                email, properties=properties, id_property="email",
            )
        except IntegrationError as e:
            if "404" in str(e) or "not found" in str(e).lower():
                return None
            raise

    def create_contact(self, email: str,
                       first_name: str = "",
                       last_name: str = "",
                       phone: str = "",
                       company: str = "",
                       **kwargs: Any) -> HubSpotContact:
        """Create a new contact."""
        properties: Dict[str, Any] = {
            "email": email,
            "firstname": first_name,
            "lastname": last_name,
            "phone": phone,
            "company": company,
        }
        properties.update(kwargs)
        properties = {k: v for k, v in properties.items() if v}

        data = self._api_call(
            "POST", "crm/v3/objects/contacts",
            json_body={"properties": properties},
        )
        return self._parse_contact(data)

    def update_contact(self, contact_id: str,
                       properties: Dict[str, Any]) -> HubSpotContact:
        """Update a contact."""
        data = self._api_call(
            "PATCH", f"crm/v3/objects/contacts/{contact_id}",
            json_body={"properties": properties},
        )
        return self._parse_contact(data)

    def delete_contact(self, contact_id: str) -> bool:
        """Archive (soft-delete) a contact. Returns True on success."""
        try:
            self._api_call("DELETE", f"crm/v3/objects/contacts/{contact_id}")
            return True
        except Exception:
            return False

    def search_contacts(self, query: str,
                        properties: Optional[List[str]] = None,
                        limit: int = 100
                        ) -> List[HubSpotContact]:
        """Search for contacts by name, email, etc."""
        filter_groups = [{
            "filters": [{
                "propertyName": "email",
                "operator": "CONTAINS_TOKEN",
                "value": query,
            }],
        }]

        payload: Dict[str, Any] = {
            "filterGroups": filter_groups,
            "limit": min(limit, 100),
        }
        if properties:
            payload["properties"] = properties

        data = self._api_call(
            "POST", "crm/v3/objects/contacts/search",
            json_body=payload,
        )
        return [self._parse_contact(c) for c in data.get("results", [])]

    # ------------------------------------------------------------------
    # Companies
    # ------------------------------------------------------------------

    def list_companies(self,
                       limit: int = 100,
                       after: Optional[str] = None,
                       properties: Optional[List[str]] = None,
                       archived: bool = False,
                       ) -> Tuple[List[HubSpotCompany], Optional[str], bool]:
        """List companies."""
        params: Dict[str, Any] = {"limit": min(limit, 100)}
        if after:
            params["after"] = after
        if properties:
            params["properties"] = ",".join(properties)
        if archived:
            params["archived"] = "true"

        data = self._api_call(
            "GET", "crm/v3/objects/companies", params=params,
        )
        companies = [self._parse_company(c) for c in data.get("results", [])]
        paging = data.get("paging", {})
        next_cursor = paging.get("next", {}).get("after")
        has_more = bool(paging.get("next"))
        return companies, next_cursor, has_more

    def get_company(self, company_id: str,
                    properties: Optional[List[str]] = None,
                    archived: bool = False) -> HubSpotCompany:
        """Get a company by ID."""
        params: Dict[str, Any] = {}
        if properties:
            params["properties"] = ",".join(properties)
        if archived:
            params["archived"] = "true"

        data = self._api_call(
            "GET", f"crm/v3/objects/companies/{company_id}",
            params=params,
        )
        return self._parse_company(data)

    def create_company(self, name: str,
                       domain: str = "",
                       description: str = "",
                       industry: str = "",
                       phone: str = "",
                       website: str = "",
                       **kwargs: Any) -> HubSpotCompany:
        """Create a new company."""
        properties: Dict[str, Any] = {
            "name": name,
            "domain": domain,
            "description": description,
            "industry": industry,
            "phone": phone,
            "website": website,
        }
        properties.update(kwargs)
        properties = {k: v for k, v in properties.items() if v}

        data = self._api_call(
            "POST", "crm/v3/objects/companies",
            json_body={"properties": properties},
        )
        return self._parse_company(data)

    def update_company(self, company_id: str,
                       properties: Dict[str, Any]) -> HubSpotCompany:
        """Update a company."""
        data = self._api_call(
            "PATCH", f"crm/v3/objects/companies/{company_id}",
            json_body={"properties": properties},
        )
        return self._parse_company(data)

    def delete_company(self, company_id: str) -> bool:
        """Archive a company. Returns True on success."""
        try:
            self._api_call("DELETE", f"crm/v3/objects/companies/{company_id}")
            return True
        except Exception:
            return False

    # ------------------------------------------------------------------
    # Deals
    # ------------------------------------------------------------------

    def list_deals(self,
                   limit: int = 100,
                   after: Optional[str] = None,
                   properties: Optional[List[str]] = None,
                   archived: bool = False,
                   ) -> Tuple[List[HubSpotDeal], Optional[str], bool]:
        """List deals."""
        params: Dict[str, Any] = {"limit": min(limit, 100)}
        if after:
            params["after"] = after
        if properties:
            params["properties"] = ",".join(properties)
        if archived:
            params["archived"] = "true"

        data = self._api_call(
            "GET", "crm/v3/objects/deals", params=params,
        )
        deals = [self._parse_deal(d) for d in data.get("results", [])]
        paging = data.get("paging", {})
        next_cursor = paging.get("next", {}).get("after")
        has_more = bool(paging.get("next"))
        return deals, next_cursor, has_more

    def get_deal(self, deal_id: str,
                 properties: Optional[List[str]] = None,
                 archived: bool = False) -> HubSpotDeal:
        """Get a deal by ID."""
        params: Dict[str, Any] = {}
        if properties:
            params["properties"] = ",".join(properties)
        if archived:
            params["archived"] = "true"

        data = self._api_call(
            "GET", f"crm/v3/objects/deals/{deal_id}", params=params,
        )
        return self._parse_deal(data)

    def create_deal(self, dealname: str,
                    amount: str = "",
                    dealstage: str = DealStage.APPOINTMENT_SCHEDULED.value,
                    pipeline: str = "default",
                    closedate: str = "",
                    description: str = "",
                    **kwargs: Any) -> HubSpotDeal:
        """Create a new deal."""
        properties: Dict[str, Any] = {
            "dealname": dealname,
            "amount": amount,
            "dealstage": dealstage,
            "pipeline": pipeline,
            "description": description,
        }
        if closedate:
            properties["closedate"] = closedate
        properties.update(kwargs)
        properties = {k: v for k, v in properties.items() if v}

        data = self._api_call(
            "POST", "crm/v3/objects/deals",
            json_body={"properties": properties},
        )
        return self._parse_deal(data)

    def update_deal(self, deal_id: str,
                    properties: Dict[str, Any]) -> HubSpotDeal:
        """Update a deal."""
        data = self._api_call(
            "PATCH", f"crm/v3/objects/deals/{deal_id}",
            json_body={"properties": properties},
        )
        return self._parse_deal(data)

    def delete_deal(self, deal_id: str) -> bool:
        """Archive a deal. Returns True on success."""
        try:
            self._api_call("DELETE", f"crm/v3/objects/deals/{deal_id}")
            return True
        except Exception:
            return False

    def move_deal_stage(self, deal_id: str,
                        new_stage: str) -> HubSpotDeal:
        """Move a deal to a new pipeline stage."""
        return self.update_deal(deal_id, {"dealstage": new_stage})

    # ------------------------------------------------------------------
    # Deal pipelines
    # ------------------------------------------------------------------

    def list_deal_pipelines(self,
                            archived: bool = False
                            ) -> List[HubSpotPipeline]:
        """List all deal pipelines."""
        params = {"archived": "true"} if archived else {}
        data = self._api_call(
            "GET", "crm/v3/pipelines/deals",
            params=params if params else None,
        )
        return [self._parse_pipeline(p) for p in data.get("results", [])]

    def get_deal_pipeline(self, pipeline_id: str) -> HubSpotPipeline:
        """Get a specific deal pipeline."""
        data = self._api_call(
            "GET", f"crm/v3/pipelines/deals/{pipeline_id}",
        )
        return self._parse_pipeline(data)

    def get_deal_stages(self, pipeline_id: str = "default"
                        ) -> List[Dict[str, Any]]:
        """Get all stages in a deal pipeline."""
        pipeline = self.get_deal_pipeline(pipeline_id)
        return pipeline.stages

    # ------------------------------------------------------------------
    # Generic CRM objects
    # ------------------------------------------------------------------

    def list_objects(self, object_type: str,
                     limit: int = 100,
                     after: Optional[str] = None,
                     properties: Optional[List[str]] = None,
                     archived: bool = False,
                     ) -> Tuple[List[HubSpotCRMObject], Optional[str], bool]:
        """List objects of a given CRM type."""
        params: Dict[str, Any] = {"limit": min(limit, 100)}
        if after:
            params["after"] = after
        if properties:
            params["properties"] = ",".join(properties)
        if archived:
            params["archived"] = "true"

        data = self._api_call(
            "GET", f"crm/v3/objects/{object_type}", params=params,
        )
        objects = [self._parse_crm_object(o, object_type)
                   for o in data.get("results", [])]
        paging = data.get("paging", {})
        next_cursor = paging.get("next", {}).get("after")
        has_more = bool(paging.get("next"))
        return objects, next_cursor, has_more

    def get_object(self, object_type: str, object_id: str,
                   properties: Optional[List[str]] = None
                   ) -> HubSpotCRMObject:
        """Get a specific CRM object."""
        params: Dict[str, Any] = {}
        if properties:
            params["properties"] = ",".join(properties)

        data = self._api_call(
            "GET", f"crm/v3/objects/{object_type}/{object_id}",
            params=params if params else None,
        )
        return self._parse_crm_object(data, object_type)

    def create_object(self, object_type: str,
                      properties: Dict[str, Any]) -> HubSpotCRMObject:
        """Create a new CRM object."""
        data = self._api_call(
            "POST", f"crm/v3/objects/{object_type}",
            json_body={"properties": properties},
        )
        return self._parse_crm_object(data, object_type)

    def update_object(self, object_type: str, object_id: str,
                      properties: Dict[str, Any]) -> HubSpotCRMObject:
        """Update a CRM object."""
        data = self._api_call(
            "PATCH", f"crm/v3/objects/{object_type}/{object_id}",
            json_body={"properties": properties},
        )
        return self._parse_crm_object(data, object_type)

    def delete_object(self, object_type: str, object_id: str) -> bool:
        """Archive a CRM object. Returns True on success."""
        try:
            self._api_call("DELETE", f"crm/v3/objects/{object_type}/{object_id}")
            return True
        except Exception:
            return False

    def search_objects(self, object_type: str,
                       filter_groups: List[Dict[str, Any]],
                       properties: Optional[List[str]] = None,
                       limit: int = 100,
                       sorts: Optional[List[Dict[str, Any]]] = None
                       ) -> List[HubSpotCRMObject]:
        """Search CRM objects with filter groups."""
        payload: Dict[str, Any] = {
            "filterGroups": filter_groups,
            "limit": min(limit, 100),
        }
        if properties:
            payload["properties"] = properties
        if sorts:
            payload["sorts"] = sorts

        data = self._api_call(
            "POST", f"crm/v3/objects/{object_type}/search",
            json_body=payload,
        )
        return [self._parse_crm_object(o, object_type)
                for o in data.get("results", [])]

    # ------------------------------------------------------------------
    # Tickets
    # ------------------------------------------------------------------

    def list_tickets(self,
                     limit: int = 100,
                     after: Optional[str] = None,
                     properties: Optional[List[str]] = None,
                     archived: bool = False,
                     ) -> Tuple[List[HubSpotTicket], Optional[str], bool]:
        """List tickets."""
        params: Dict[str, Any] = {"limit": min(limit, 100)}
        if after:
            params["after"] = after
        if properties:
            params["properties"] = ",".join(properties)
        if archived:
            params["archived"] = "true"

        data = self._api_call(
            "GET", "crm/v3/objects/tickets", params=params,
        )
        tickets = [self._parse_ticket(t) for t in data.get("results", [])]
        paging = data.get("paging", {})
        next_cursor = paging.get("next", {}).get("after")
        has_more = bool(paging.get("next"))
        return tickets, next_cursor, has_more

    def get_ticket(self, ticket_id: str,
                   properties: Optional[List[str]] = None
                   ) -> HubSpotTicket:
        """Get a ticket by ID."""
        params: Dict[str, Any] = {}
        if properties:
            params["properties"] = ",".join(properties)

        data = self._api_call(
            "GET", f"crm/v3/objects/tickets/{ticket_id}",
            params=params if params else None,
        )
        return self._parse_ticket(data)

    def create_ticket(self, subject: str,
                      content: str = "",
                      pipeline: str = "0",
                      stage: str = TicketStage.SUBMITTED.value,
                      priority: str = "MEDIUM",
                      **kwargs: Any) -> HubSpotTicket:
        """Create a new ticket."""
        properties: Dict[str, Any] = {
            "subject": subject,
            "content": content,
            "hs_pipeline": pipeline,
            "hs_pipeline_stage": stage,
            "hs_ticket_priority": priority,
        }
        properties.update(kwargs)
        properties = {k: v for k, v in properties.items() if v}

        data = self._api_call(
            "POST", "crm/v3/objects/tickets",
            json_body={"properties": properties},
        )
        return self._parse_ticket(data)

    def update_ticket(self, ticket_id: str,
                      properties: Dict[str, Any]) -> HubSpotTicket:
        """Update a ticket."""
        data = self._api_call(
            "PATCH", f"crm/v3/objects/tickets/{ticket_id}",
            json_body={"properties": properties},
        )
        return self._parse_ticket(data)

    def close_ticket(self, ticket_id: str) -> HubSpotTicket:
        """Close a ticket."""
        return self.update_ticket(
            ticket_id, {"hs_pipeline_stage": TicketStage.CLOSED.value},
        )

    def list_ticket_pipelines(self) -> List[HubSpotPipeline]:
        """List all ticket pipelines."""
        data = self._api_call("GET", "crm/v3/pipelines/tickets")
        return [self._parse_pipeline(p) for p in data.get("results", [])]

    # ------------------------------------------------------------------
    # Email campaigns
    # ------------------------------------------------------------------

    def list_emails(self, limit: int = 20,
                    offset: int = 0,
                    campaign_type: Optional[str] = None
                    ) -> Tuple[List[HubSpotEmail], int]:
        """List marketing email campaigns."""
        params: Dict[str, Any] = {"limit": min(limit, 250), "offset": offset}
        if campaign_type:
            params["campaignType"] = campaign_type

        data = self._api_call(
            "GET", "marketing/v3/emails", params=params,
        )
        emails = [self._parse_email(e) for e in data.get("emails", [])]
        total = data.get("total", 0)
        return emails, total

    def get_email(self, email_id: int) -> HubSpotEmail:
        """Get a specific email."""
        data = self._api_call("GET", f"marketing/v3/emails/{email_id}")
        return self._parse_email(data)

    def get_email_stats(self, email_id: int) -> Dict[str, Any]:
        """Get statistics for an email campaign."""
        data = self._api_call(
            "GET", f"marketing/v3/emails/{email_id}/stats",
        )
        return data

    # ------------------------------------------------------------------
    # Analytics
    # ------------------------------------------------------------------

    def get_analytics_report(self, report_type: str,
                             start_date: str,
                             end_date: str,
                             metrics: Optional[List[str]] = None,
                             dimensions: Optional[List[str]] = None,
                             filters: Optional[List[Dict[str, Any]]] = None,
                             limit: int = 100
                             ) -> HubSpotAnalyticsReport:
        """
        Get an analytics report.

        Args:
            report_type: Report type (e.g., "sessions", "contacts").
            start_date: Start date (YYYY-MM-DD).
            end_date: End date (YYYY-MM-DD).
            metrics: List of metrics to include.
            dimensions: List of dimensions to break down by.
            filters: List of filter objects.
            limit: Max results.

        Returns:
            HubSpotAnalyticsReport with data.
        """
        payload: Dict[str, Any] = {
            "startDate": start_date,
            "endDate": end_date,
            "limit": min(limit, 1000),
        }
        if metrics:
            payload["metrics"] = metrics
        if dimensions:
            payload["dimensions"] = dimensions
        if filters:
            payload["filters"] = filters

        try:
            data = self._api_call(
                "POST",
                f"analytics/v3/reports/{report_type}",
                json_body=payload,
            )
            return HubSpotAnalyticsReport(
                report_type=report_type,
                total=len(data.get("results", [])),
                data=data.get("results", []),
                extra=data,
            )
        except Exception:
            # Fallback: try alternative endpoint
            return HubSpotAnalyticsReport(
                report_type=report_type,
                total=0,
                data=[],
                extra={"error": "analytics unavailable"},
            )

    # ------------------------------------------------------------------
    # Forms
    # ------------------------------------------------------------------

    def list_forms(self,
                   limit: int = 20,
                   after: Optional[str] = None,
                   form_types: Optional[List[str]] = None
                   ) -> Tuple[List[HubSpotForm], Optional[str], bool]:
        """List all forms."""
        params: Dict[str, Any] = {"limit": min(limit, 200)}
        if after:
            params["after"] = after
        if form_types:
            params["formTypes"] = ",".join(form_types)

        data = self._api_call(
            "GET", "marketing/v3/forms", params=params,
        )
        forms = [self._parse_form(f) for f in data.get("results", [])]
        paging = data.get("paging", {})
        next_cursor = paging.get("next", {}).get("after")
        has_more = bool(paging.get("next"))
        return forms, next_cursor, has_more

    def get_form(self, form_id: str) -> HubSpotForm:
        """Get a specific form."""
        data = self._api_call("GET", f"marketing/v3/forms/{form_id}")
        return self._parse_form(data)

    def create_form(self, name: str,
                    fields: List[Dict[str, Any]],
                    action: str = "",
                    submit_text: str = "Submit",
                    redirect: str = "",
                    **kwargs: Any) -> HubSpotForm:
        """Create a new form."""
        form: Dict[str, Any] = {
            "name": name,
            "formFieldGroups": [
                {"fields": [f]} for f in fields
            ],
            "submitText": submit_text,
        }
        if action:
            form["action"] = action
        if redirect:
            form["redirect"] = redirect
        form.update(kwargs)

        data = self._api_call(
            "POST", "marketing/v3/forms", json_body=form,
        )
        return self._parse_form(data)

    def delete_form(self, form_id: str) -> bool:
        """Delete a form. Returns True on success."""
        try:
            self._api_call("DELETE", f"marketing/v3/forms/{form_id}")
            return True
        except Exception:
            return False

    def get_form_submissions(self, form_id: str,
                             limit: int = 20,
                             after: Optional[str] = None
                             ) -> Dict[str, Any]:
        """Get submissions for a form."""
        params: Dict[str, Any] = {"limit": min(limit, 50)}
        if after:
            params["after"] = after

        return self._api_call(
            "GET", f"marketing/v3/forms/{form_id}/submissions",
            params=params,
        )

    # ------------------------------------------------------------------
    # Webhook verification
    # ------------------------------------------------------------------

    def verify_webhook_signature(self, body: str,
                                 signature: str,
                                 request_timestamp: str) -> bool:
        """
        Verify HubSpot webhook signature.

        HubSpot uses HMAC-SHA256 with the app secret,
        combining timestamp + request body.

        Args:
            body: Raw request body.
            signature: X-HubSpot-Signature-v3 header.
            request_timestamp: X-HubSpot-Request-Timestamp header.

        Returns:
            True if signature is valid.
        """
        secret = self.config.webhook_secret or ""
        if not secret:
            return False

        # Replay protection - reject if older than 5 minutes
        try:
            ts = float(request_timestamp)
            if abs(time.time() - ts) > 300:
                return False
        except (ValueError, TypeError):
            pass

        source_string = f"{request_timestamp}{body}"
        computed = hmac.new(
            secret.encode("utf-8"),
            source_string.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()

        return hmac.compare_digest(computed, signature)

    # ------------------------------------------------------------------
    # Parsing helpers
    # ------------------------------------------------------------------

    def _parse_contact(self, data: Dict[str, Any]) -> HubSpotContact:
        props = data.get("properties", {})
        return HubSpotContact(
            id=data.get("id", ""),
            email=props.get("email", "") or "",
            first_name=props.get("firstname", "") or "",
            last_name=props.get("lastname", "") or "",
            phone=props.get("phone", "") or "",
            company=props.get("company", "") or "",
            website=props.get("website", "") or "",
            city=props.get("city", "") or "",
            state=props.get("state", "") or "",
            country=props.get("country", "") or "",
            zip=props.get("zip", "") or "",
            job_title=props.get("jobtitle", "") or "",
            hs_object_id=props.get("hs_object_id", "") or "",
            created_at=data.get("createdAt", ""),
            updated_at=data.get("updatedAt", ""),
            archived=data.get("archived", False),
            properties=props,
            extra=data,
        )

    def _parse_company(self, data: Dict[str, Any]) -> HubSpotCompany:
        props = data.get("properties", {})
        return HubSpotCompany(
            id=data.get("id", ""),
            name=props.get("name", "") or "",
            domain=props.get("domain", "") or "",
            description=props.get("description", "") or "",
            industry=props.get("industry", "") or "",
            city=props.get("city", "") or "",
            state=props.get("state", "") or "",
            country=props.get("country", "") or "",
            zip=props.get("zip", "") or "",
            phone=props.get("phone", "") or "",
            website=props.get("website", "") or "",
            numberofemployees=int(props.get("numberofemployees", 0) or 0),
            revenue=float(props.get("revenue", 0) or 0),
            hs_object_id=props.get("hs_object_id", "") or "",
            created_at=data.get("createdAt", ""),
            updated_at=data.get("updatedAt", ""),
            archived=data.get("archived", False),
            properties=props,
            extra=data,
        )

    def _parse_deal(self, data: Dict[str, Any]) -> HubSpotDeal:
        props = data.get("properties", {})
        return HubSpotDeal(
            id=data.get("id", ""),
            dealname=props.get("dealname", "") or "",
            amount=props.get("amount", "") or "",
            dealstage=props.get("dealstage", "") or "",
            pipeline=props.get("pipeline", "default"),
            closedate=props.get("closedate", "") or "",
            description=props.get("description", "") or "",
            priority=props.get("priority", "") or "",
            hs_object_id=props.get("hs_object_id", "") or "",
            created_at=data.get("createdAt", ""),
            updated_at=data.get("updatedAt", ""),
            archived=data.get("archived", False),
            properties=props,
            extra=data,
        )

    def _parse_ticket(self, data: Dict[str, Any]) -> HubSpotTicket:
        props = data.get("properties", {})
        return HubSpotTicket(
            id=data.get("id", ""),
            subject=props.get("subject", "") or "",
            content=props.get("content", "") or "",
            hs_pipeline=props.get("hs_pipeline", "0") or "0",
            hs_pipeline_stage=props.get("hs_pipeline_stage", "") or "",
            hs_ticket_priority=props.get("hs_ticket_priority", "MEDIUM") or "MEDIUM",
            hs_object_id=props.get("hs_object_id", "") or "",
            created_at=data.get("createdAt", ""),
            updated_at=data.get("updatedAt", ""),
            archived=data.get("archived", False),
            properties=props,
            extra=data,
        )

    def _parse_email(self, data: Dict[str, Any]) -> HubSpotEmail:
        return HubSpotEmail(
            id=data.get("id", 0),
            name=data.get("name", ""),
            subject=data.get("subject", ""),
            type_=data.get("type", ""),
            state=data.get("state", ""),
            created=data.get("created", 0),
            updated=data.get("updated", 0),
            publish_date=data.get("publishDate", 0),
            stats=data.get("stats", {}) or {},
            extra=data,
        )

    def _parse_form(self, data: Dict[str, Any]) -> HubSpotForm:
        return HubSpotForm(
            id=data.get("id", ""),
            name=data.get("name", ""),
            action=data.get("action", ""),
            method=data.get("method", "POST"),
            css_class=data.get("cssClass", ""),
            redirect=data.get("redirect", ""),
            submit_text=data.get("submitText", ""),
            form_field_groups=data.get("formFieldGroups", []) or [],
            created_at=data.get("createdAt", ""),
            updated_at=data.get("updatedAt", ""),
            extra=data,
        )

    def _parse_pipeline(self, data: Dict[str, Any]) -> HubSpotPipeline:
        return HubSpotPipeline(
            id=data.get("id", ""),
            label=data.get("label", ""),
            stages=data.get("stages", []) or [],
            archived=data.get("archived", False),
            created_at=data.get("createdAt", ""),
            updated_at=data.get("updatedAt", ""),
            extra=data,
        )

    def _parse_crm_object(self, data: Dict[str, Any],
                          object_type: str) -> HubSpotCRMObject:
        return HubSpotCRMObject(
            id=data.get("id", ""),
            object_type=object_type,
            properties=data.get("properties", {}) or {},
            created_at=data.get("createdAt", ""),
            updated_at=data.get("updatedAt", ""),
            archived=data.get("archived", False),
            extra=data,
        )

    # ------------------------------------------------------------------
    # Internal API helper
    # ------------------------------------------------------------------

    def _api_call(self, method: str, path: str,
                  params: Optional[Dict[str, Any]] = None,
                  json_body: Optional[Dict[str, Any]] = None,
                  _retry_count: int = 0) -> Any:
        """Make a HubSpot API call with retry on rate limits."""
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
        """Execute a single HubSpot API request."""
        self._check_rate_limit()
        self._mark_request()

        url = self._build_url(path)
        token = self.config.access_token or self.config.api_key or ""
        headers = {
            "Authorization": f"Bearer {token}",
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
                    f"HubSpot rate limited, retry after {retry_after}s",
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

            # Parse HubSpot error message
            error_msg = f"HubSpot API error {e.code}"
            try:
                error_data = json.loads(resp_body)
                if "message" in error_data:
                    error_msg = error_data["message"]
                elif "errors" in error_data:
                    errors = error_data["errors"]
                    if errors:
                        error_msg = errors[0].get("message", error_msg)
            except Exception:
                pass

            raise self._handle_error(
                error_msg,
                status_code=e.code,
                response_body=resp_body,
            )
        except (RateLimitError, IntegrationError):
            raise
        except Exception as e:
            raise self._handle_error(f"Request failed: {e}")
