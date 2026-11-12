"""
PagerDuty Integration Adapter for LiuHao AI OS.

Provides integration with PagerDuty incident management for incidents,
services, escalation policies, schedules, on-call, users and teams,
webhooks, and incident notes.
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


class PagerDutyIncidentStatus(str, Enum):
    """Incident status values."""
    TRIGGERED = "triggered"
    ACKNOWLEDGED = "acknowledged"
    RESOLVED = "resolved"


class PagerDutyUrgency(str, Enum):
    """Incident urgency levels."""
    HIGH = "high"
    LOW = "low"


class PagerDutySeverity(str, Enum):
    """Alert severity levels."""
    CRITICAL = "critical"
    ERROR = "error"
    WARNING = "warning"
    INFO = "info"


class PagerDutyIncidentType(str, Enum):
    """Incident type values."""
    INCIDENT = "incident"
    ALERT = "alert"


class PagerDutyServiceStatus(str, Enum):
    """Service status values."""
    ACTIVE = "active"
    WARNING = "warning"
    CRITICAL = "critical"
    MAINTENANCE = "maintenance"
    DISABLED = "disabled"


@dataclass
class PagerDutyIncident:
    """Represents a PagerDuty incident."""
    id: str = ""
    type: PagerDutyIncidentType = PagerDutyIncidentType.INCIDENT
    summary: str = ""
    self_url: str = ""
    html_url: str = ""
    incident_number: int = 0
    title: str = ""
    description: str = ""
    status: PagerDutyIncidentStatus = PagerDutyIncidentStatus.TRIGGERED
    urgency: PagerDutyUrgency = PagerDutyUrgency.HIGH
    created_at: str = ""
    updated_at: str = ""
    resolved_at: str = ""
    first_trigger_log_entry: Dict[str, Any] = field(default_factory=dict)
    alert_counts: Dict[str, Any] = field(default_factory=dict)
    service: Dict[str, Any] = field(default_factory=dict)
    escalation_policy: Dict[str, Any] = field(default_factory=dict)
    assignments: List[Dict[str, Any]] = field(default_factory=list)
    acknowledged_via: str = ""
    last_status_change_at: str = ""
    last_status_change_by: Dict[str, Any] = field(default_factory=dict)
    priority: Dict[str, Any] = field(default_factory=dict)
    teams: List[Dict[str, Any]] = field(default_factory=list)
    extra: Dict[str, Any] = field(default_factory=dict)


@dataclass
class PagerDutyService:
    """Represents a PagerDuty service."""
    id: str = ""
    name: str = ""
    description: str = ""
    status: PagerDutyServiceStatus = PagerDutyServiceStatus.ACTIVE
    created_at: str = ""
    updated_at: str = ""
    self_url: str = ""
    html_url: str = ""
    escalation_policy: Dict[str, Any] = field(default_factory=dict)
    teams: List[Dict[str, Any]] = field(default_factory=list)
    integrations: List[Dict[str, Any]] = field(default_factory=list)
    incident_urgency_rule: Dict[str, Any] = field(default_factory=dict)
    support_hours: Dict[str, Any] = field(default_factory=dict)
    scheduled_maintenances: List[Dict[str, Any]] = field(default_factory=list)
    alert_creation: str = "create_alerts_and_incidents"
    extra: Dict[str, Any] = field(default_factory=dict)


@dataclass
class PagerDutyEscalationPolicy:
    """Represents a PagerDuty escalation policy."""
    id: str = ""
    name: str = ""
    description: str = ""
    escalation_rules: List[Dict[str, Any]] = field(default_factory=list)
    services: List[Dict[str, Any]] = field(default_factory=dict)
    teams: List[Dict[str, Any]] = field(default_factory=list)
    num_loops: int = 0
    on_call_handoff_notifications: str = ""
    self_url: str = ""
    html_url: str = ""
    summary: str = ""
    extra: Dict[str, Any] = field(default_factory=dict)


@dataclass
class PagerDutySchedule:
    """Represents a PagerDuty schedule."""
    id: str = ""
    name: str = ""
    description: str = ""
    time_zone: str = "UTC"
    schedule_layers: List[Dict[str, Any]] = field(default_factory=list)
    teams: List[Dict[str, Any]] = field(default_factory=list)
    escalation_policies: List[Dict[str, Any]] = field(default_factory=list)
    users: List[Dict[str, Any]] = field(default_factory=list)
    self_url: str = ""
    html_url: str = ""
    summary: str = ""
    extra: Dict[str, Any] = field(default_factory=dict)


@dataclass
class PagerDutyOnCall:
    """Represents a PagerDuty on-call entry."""
    user: Dict[str, Any] = field(default_factory=dict)
    schedule: Dict[str, Any] = field(default_factory=dict)
    escalation_policy: Dict[str, Any] = field(default_factory=dict)
    escalation_level: int = 0
    start: str = ""
    end: str = ""
    extra: Dict[str, Any] = field(default_factory=dict)


@dataclass
class PagerDutyUser:
    """Represents a PagerDuty user."""
    id: str = ""
    name: str = ""
    email: str = ""
    time_zone: str = "UTC"
    color: str = ""
    avatar_url: str = ""
    billed: bool = True
    role: str = "user"
    description: str = ""
    job_title: str = ""
    teams: List[Dict[str, Any]] = field(default_factory=list)
    contact_methods: List[Dict[str, Any]] = field(default_factory=list)
    notification_rules: List[Dict[str, Any]] = field(default_factory=list)
    self_url: str = ""
    html_url: str = ""
    summary: str = ""
    extra: Dict[str, Any] = field(default_factory=dict)


@dataclass
class PagerDutyTeam:
    """Represents a PagerDuty team."""
    id: str = ""
    name: str = ""
    description: str = ""
    default_role: str = "manager"
    self_url: str = ""
    html_url: str = ""
    summary: str = ""
    extra: Dict[str, Any] = field(default_factory=dict)


@dataclass
class PagerDutyNote:
    """Represents a PagerDuty incident note."""
    id: str = ""
    content: str = ""
    user: Dict[str, Any] = field(default_factory=dict)
    created_at: str = ""
    incident_id: str = ""
    extra: Dict[str, Any] = field(default_factory=dict)


@dataclass
class PagerDutyWebhook:
    """Represents a PagerDuty webhook subscription."""
    id: str = ""
    type: str = "webhook_subscription"
    active: bool = True
    delivery_method: Dict[str, Any] = field(default_factory=dict)
    description: str = ""
    events: List[str] = field(default_factory=list)
    filter: Dict[str, Any] = field(default_factory=dict)
    extra: Dict[str, Any] = field(default_factory=dict)


class PagerDutyAdapter(BaseIntegrationAdapter):
    """
    PagerDuty integration adapter for LiuHao AI OS.

    Supports:
    - Incidents (trigger, acknowledge, resolve, list)
    - Services management
    - Escalation policies
    - Schedules
    - On-call information
    - Users and teams
    - Webhook subscriptions
    - Incident notes
    """

    def __init__(self, config: IntegrationConfig):
        if config.auth_type == AuthType.NONE:
            config.auth_type = AuthType.API_KEY
        if not config.base_url:
            config.base_url = "https://api.pagerduty.com"
        super().__init__(config)
        self._user_id: Optional[str] = None
        self._user_name: Optional[str] = None
        self._user_email: Optional[str] = None
        self._cached_incidents: Dict[str, PagerDutyIncident] = {}
        self._cached_services: Dict[str, PagerDutyService] = {}
        self._webhook_secret: str = config.webhook_secret or ""

    # ------------------------------------------------------------------
    # Base interface
    # ------------------------------------------------------------------

    def connect(self) -> bool:
        self._status = ConnectionStatus.CONNECTING
        self._logger.info("Connecting to PagerDuty API...")

        try:
            result = self._api_call("users/me")
            user_data = result.get("user", {})
            self._user_id = user_data.get("id", "")
            self._user_name = user_data.get("name", "")
            self._user_email = user_data.get("email", "")
            self._status = ConnectionStatus.CONNECTED
            self._connected_at = time.time()
            self._logger.info(
                "Connected to PagerDuty as %s (%s)",
                self._user_name, self._user_email,
            )
            return True
        except IntegrationError:
            raise
        except Exception as e:
            self._status = ConnectionStatus.ERROR
            raise self._handle_error(f"Connection error: {e}")

    def disconnect(self) -> bool:
        self._status = ConnectionStatus.DISCONNECTED
        self._user_id = None
        self._user_name = None
        self._user_email = None
        self._cached_incidents = {}
        self._cached_services = {}
        self._session = None
        self._logger.info("Disconnected from PagerDuty")
        return True

    def is_connected(self) -> bool:
        return self._status == ConnectionStatus.CONNECTED

    def get_status(self) -> IntegrationStatus:
        return self._build_status({
            "user_name": self._user_name,
            "user_id": self._user_id,
            "user_email": self._user_email,
            "cached_incidents": len(self._cached_incidents),
            "cached_services": len(self._cached_services),
        })

    def test_connection(self) -> bool:
        try:
            if not self.is_connected():
                return False
            result = self._api_call("users/me")
            return "user" in result
        except Exception:
            return False

    # ------------------------------------------------------------------
    # Incidents
    # ------------------------------------------------------------------

    def trigger_incident(self, title: str,
                         service_id: str,
                         urgency: PagerDutyUrgency = PagerDutyUrgency.HIGH,
                         body: Dict[str, Any] = None,
                         incident_key: str = "",
                         priority_id: str = "",
                         escalation_policy_id: str = "",
                         assignments: Optional[List[Dict[str, Any]]] = None,
                         **kwargs: Any) -> PagerDutyIncident:
        incident_data: Dict[str, Any] = {
            "type": "incident",
            "title": title,
            "service": {
                "id": service_id,
                "type": "service_reference",
            },
            "urgency": urgency.value,
        }
        if body:
            incident_data["body"] = body
        if incident_key:
            incident_data["incident_key"] = incident_key
        if priority_id:
            incident_data["priority"] = {
                "id": priority_id,
                "type": "priority_reference",
            }
        if escalation_policy_id:
            incident_data["escalation_policy"] = {
                "id": escalation_policy_id,
                "type": "escalation_policy_reference",
            }
        if assignments:
            incident_data["assignments"] = assignments
        incident_data.update(kwargs)

        payload = {"incident": incident_data}
        result = self._api_call(
            "incidents", data=payload, http_method="POST"
        )
        incident = self._parse_incident(result.get("incident", {}))
        if incident.id:
            self._cached_incidents[incident.id] = incident
        return incident

    def acknowledge_incident(self, incident_id: str,
                             from_email: str,
                             resolution: str = "") -> PagerDutyIncident:
        payload = {
            "incident": {
                "type": "incident_reference",
                "status": "acknowledged",
            }
        }
        if resolution:
            payload["incident"]["resolution"] = resolution

        result = self._api_call(
            f"incidents/{incident_id}",
            data=payload,
            http_method="PUT",
            from_email=from_email,
        )
        incident = self._parse_incident(result.get("incident", {}))
        if incident.id:
            self._cached_incidents[incident.id] = incident
        return incident

    def resolve_incident(self, incident_id: str,
                         from_email: str,
                         resolution: str = "Resolved") -> PagerDutyIncident:
        payload = {
            "incident": {
                "type": "incident_reference",
                "status": "resolved",
                "resolution": resolution,
            }
        }
        result = self._api_call(
            f"incidents/{incident_id}",
            data=payload,
            http_method="PUT",
            from_email=from_email,
        )
        incident = self._parse_incident(result.get("incident", {}))
        if incident.id:
            self._cached_incidents[incident.id] = incident
        return incident

    def get_incident(self, incident_id: str,
                     include: List[str] = None) -> PagerDutyIncident:
        if incident_id in self._cached_incidents:
            return self._cached_incidents[incident_id]

        params = {}
        if include:
            params["include[]"] = include

        result = self._api_call(f"incidents/{incident_id}", params=params)
        incident = self._parse_incident(result.get("incident", {}))
        if incident.id:
            self._cached_incidents[incident.id] = incident
        return incident

    def list_incidents(self, statuses: Optional[List[str]] = None,
                       urgencies: Optional[List[str]] = None,
                       service_ids: Optional[List[str]] = None,
                       team_ids: Optional[List[str]] = None,
                       since: str = "",
                       until: str = "",
                       limit: int = 25,
                       offset: int = 0,
                       sort_by: str = "created_at:DESC",
                       **kwargs: Any) -> Dict[str, Any]:
        params: Dict[str, Any] = {
            "limit": limit,
            "offset": offset,
            "sort_by": sort_by,
        }
        if statuses:
            for i, s in enumerate(statuses):
                params[f"statuses[{i}]"] = s
        if urgencies:
            for i, u in enumerate(urgencies):
                params[f"urgencies[{i}]"] = u
        if service_ids:
            for i, sid in enumerate(service_ids):
                params[f"service_ids[{i}]"] = sid
        if team_ids:
            for i, tid in enumerate(team_ids):
                params[f"team_ids[{i}]"] = tid
        if since:
            params["since"] = since
        if until:
            params["until"] = until
        params.update(kwargs)

        result = self._api_call("incidents", params=params)
        incidents_data = result.get("incidents", [])
        incidents = [self._parse_incident(i) for i in incidents_data]
        for incident in incidents:
            if incident.id:
                self._cached_incidents[incident.id] = incident

        return {
            "incidents": incidents,
            "limit": result.get("limit", 0),
            "offset": result.get("offset", 0),
            "total": result.get("total", 0),
            "more": result.get("more", False),
        }

    def _parse_incident(self, data: Dict[str, Any]) -> PagerDutyIncident:
        return PagerDutyIncident(
            id=data.get("id", ""),
            type=PagerDutyIncidentType(data.get("type", "incident")),
            summary=data.get("summary", ""),
            self_url=data.get("self", ""),
            html_url=data.get("html_url", ""),
            incident_number=data.get("incident_number", 0),
            title=data.get("title", ""),
            description=data.get("description", ""),
            status=PagerDutyIncidentStatus(data.get("status", "triggered")),
            urgency=PagerDutyUrgency(data.get("urgency", "high")),
            created_at=data.get("created_at", ""),
            updated_at=data.get("updated_at", ""),
            resolved_at=data.get("resolved_at", ""),
            first_trigger_log_entry=data.get("first_trigger_log_entry", {}),
            alert_counts=data.get("alert_counts", {}),
            service=data.get("service", {}),
            escalation_policy=data.get("escalation_policy", {}),
            assignments=data.get("assignments", []),
            acknowledged_via=data.get("acknowledged_via", ""),
            last_status_change_at=data.get("last_status_change_at", ""),
            last_status_change_by=data.get("last_status_change_by", {}),
            priority=data.get("priority", {}),
            teams=data.get("teams", []),
            extra=data,
        )

    # ------------------------------------------------------------------
    # Services
    # ------------------------------------------------------------------

    def create_service(self, name: str,
                       escalation_policy_id: str,
                       description: str = "",
                       status: PagerDutyServiceStatus = PagerDutyServiceStatus.ACTIVE,
                       alert_creation: str = "create_alerts_and_incidents",
                       **kwargs: Any) -> PagerDutyService:
        service_data: Dict[str, Any] = {
            "type": "service",
            "name": name,
            "description": description,
            "status": status.value,
            "escalation_policy": {
                "id": escalation_policy_id,
                "type": "escalation_policy_reference",
            },
            "alert_creation": alert_creation,
        }
        service_data.update(kwargs)

        payload = {"service": service_data}
        result = self._api_call(
            "services", data=payload, http_method="POST"
        )
        service = self._parse_service(result.get("service", {}))
        if service.id:
            self._cached_services[service.id] = service
        return service

    def get_service(self, service_id: str) -> PagerDutyService:
        if service_id in self._cached_services:
            return self._cached_services[service_id]

        result = self._api_call(f"services/{service_id}")
        service = self._parse_service(result.get("service", {}))
        if service.id:
            self._cached_services[service.id] = service
        return service

    def update_service(self, service_id: str,
                       name: Optional[str] = None,
                       description: Optional[str] = None,
                       status: Optional[PagerDutyServiceStatus] = None,
                       **kwargs: Any) -> PagerDutyService:
        service_data: Dict[str, Any] = {"type": "service"}
        if name is not None:
            service_data["name"] = name
        if description is not None:
            service_data["description"] = description
        if status is not None:
            service_data["status"] = status.value
        service_data.update(kwargs)

        payload = {"service": service_data}
        result = self._api_call(
            f"services/{service_id}",
            data=payload,
            http_method="PUT",
        )
        service = self._parse_service(result.get("service", {}))
        if service.id:
            self._cached_services[service.id] = service
        return service

    def delete_service(self, service_id: str) -> bool:
        self._api_call(f"services/{service_id}", http_method="DELETE")
        if service_id in self._cached_services:
            del self._cached_services[service_id]
        return True

    def list_services(self, team_ids: Optional[List[str]] = None,
                      query: str = "",
                      limit: int = 25,
                      offset: int = 0,
                      statuses: Optional[List[str]] = None,
                      **kwargs: Any) -> Dict[str, Any]:
        params: Dict[str, Any] = {"limit": limit, "offset": offset}
        if team_ids:
            for i, tid in enumerate(team_ids):
                params[f"team_ids[{i}]"] = tid
        if query:
            params["query"] = query
        if statuses:
            for i, s in enumerate(statuses):
                params[f"statuses[{i}]"] = s
        params.update(kwargs)

        result = self._api_call("services", params=params)
        services_data = result.get("services", [])
        services = [self._parse_service(s) for s in services_data]
        for service in services:
            if service.id:
                self._cached_services[service.id] = service

        return {
            "services": services,
            "limit": result.get("limit", 0),
            "offset": result.get("offset", 0),
            "total": result.get("total", 0),
            "more": result.get("more", False),
        }

    def _parse_service(self, data: Dict[str, Any]) -> PagerDutyService:
        return PagerDutyService(
            id=data.get("id", ""),
            name=data.get("name", ""),
            description=data.get("description", ""),
            status=PagerDutyServiceStatus(data.get("status", "active")),
            created_at=data.get("created_at", ""),
            updated_at=data.get("updated_at", ""),
            self_url=data.get("self", ""),
            html_url=data.get("html_url", ""),
            escalation_policy=data.get("escalation_policy", {}),
            teams=data.get("teams", []),
            integrations=data.get("integrations", []),
            incident_urgency_rule=data.get("incident_urgency_rule", {}),
            support_hours=data.get("support_hours", {}),
            scheduled_maintenances=data.get("scheduled_maintenances", []),
            alert_creation=data.get("alert_creation", "create_alerts_and_incidents"),
            extra=data,
        )

    # ------------------------------------------------------------------
    # Escalation policies
    # ------------------------------------------------------------------

    def create_escalation_policy(self, name: str,
                                 escalation_rules: List[Dict[str, Any]],
                                 num_loops: int = 0,
                                 description: str = "",
                                 teams: Optional[List[Dict[str, Any]]] = None,
                                 **kwargs: Any) -> PagerDutyEscalationPolicy:
        ep_data: Dict[str, Any] = {
            "type": "escalation_policy",
            "name": name,
            "escalation_rules": escalation_rules,
            "num_loops": num_loops,
            "description": description,
        }
        if teams:
            ep_data["teams"] = teams
        ep_data.update(kwargs)

        payload = {"escalation_policy": ep_data}
        result = self._api_call(
            "escalation_policies", data=payload, http_method="POST"
        )
        return self._parse_escalation_policy(
            result.get("escalation_policy", {})
        )

    def get_escalation_policy(self, ep_id: str) -> PagerDutyEscalationPolicy:
        result = self._api_call(f"escalation_policies/{ep_id}")
        return self._parse_escalation_policy(
            result.get("escalation_policy", {})
        )

    def list_escalation_policies(self, user_ids: Optional[List[str]] = None,
                                 team_ids: Optional[List[str]] = None,
                                 query: str = "",
                                 limit: int = 25,
                                 offset: int = 0,
                                 **kwargs: Any) -> Dict[str, Any]:
        params: Dict[str, Any] = {"limit": limit, "offset": offset}
        if user_ids:
            for i, uid in enumerate(user_ids):
                params[f"user_ids[{i}]"] = uid
        if team_ids:
            for i, tid in enumerate(team_ids):
                params[f"team_ids[{i}]"] = tid
        if query:
            params["query"] = query
        params.update(kwargs)

        result = self._api_call("escalation_policies", params=params)
        eps_data = result.get("escalation_policies", [])
        return {
            "escalation_policies": [
                self._parse_escalation_policy(ep) for ep in eps_data
            ],
            "limit": result.get("limit", 0),
            "offset": result.get("offset", 0),
            "total": result.get("total", 0),
            "more": result.get("more", False),
        }

    def _parse_escalation_policy(self, data: Dict[str, Any]
                                 ) -> PagerDutyEscalationPolicy:
        return PagerDutyEscalationPolicy(
            id=data.get("id", ""),
            name=data.get("name", ""),
            description=data.get("description", ""),
            escalation_rules=data.get("escalation_rules", []),
            services=data.get("services", []),
            teams=data.get("teams", []),
            num_loops=data.get("num_loops", 0),
            on_call_handoff_notifications=data.get(
                "on_call_handoff_notifications", ""
            ),
            self_url=data.get("self", ""),
            html_url=data.get("html_url", ""),
            summary=data.get("summary", ""),
            extra=data,
        )

    # ------------------------------------------------------------------
    # Schedules
    # ------------------------------------------------------------------

    def create_schedule(self, name: str,
                        time_zone: str,
                        schedule_layers: List[Dict[str, Any]],
                        description: str = "",
                        **kwargs: Any) -> PagerDutySchedule:
        schedule_data: Dict[str, Any] = {
            "type": "schedule",
            "name": name,
            "time_zone": time_zone,
            "schedule_layers": schedule_layers,
            "description": description,
        }
        schedule_data.update(kwargs)

        payload = {"schedule": schedule_data}
        result = self._api_call(
            "schedules", data=payload, http_method="POST"
        )
        return self._parse_schedule(result.get("schedule", {}))

    def get_schedule(self, schedule_id: str,
                     since: str = "",
                     until: str = "",
                     **kwargs: Any) -> PagerDutySchedule:
        params: Dict[str, Any] = {}
        if since:
            params["since"] = since
        if until:
            params["until"] = until
        params.update(kwargs)

        result = self._api_call(
            f"schedules/{schedule_id}", params=params
        )
        return self._parse_schedule(result.get("schedule", {}))

    def list_schedules(self, query: str = "",
                       limit: int = 25,
                       offset: int = 0,
                       **kwargs: Any) -> Dict[str, Any]:
        params: Dict[str, Any] = {"limit": limit, "offset": offset}
        if query:
            params["query"] = query
        params.update(kwargs)

        result = self._api_call("schedules", params=params)
        schedules_data = result.get("schedules", [])
        return {
            "schedules": [
                self._parse_schedule(s) for s in schedules_data
            ],
            "limit": result.get("limit", 0),
            "offset": result.get("offset", 0),
            "total": result.get("total", 0),
            "more": result.get("more", False),
        }

    def _parse_schedule(self, data: Dict[str, Any]) -> PagerDutySchedule:
        return PagerDutySchedule(
            id=data.get("id", ""),
            name=data.get("name", ""),
            description=data.get("description", ""),
            time_zone=data.get("time_zone", "UTC"),
            schedule_layers=data.get("schedule_layers", []),
            teams=data.get("teams", []),
            escalation_policies=data.get("escalation_policies", []),
            users=data.get("users", []),
            self_url=data.get("self", ""),
            html_url=data.get("html_url", ""),
            summary=data.get("summary", ""),
            extra=data,
        )

    # ------------------------------------------------------------------
    # On-call
    # ------------------------------------------------------------------

    def list_on_calls(self, time_zone: str = "UTC",
                      since: str = "",
                      until: str = "",
                      schedule_ids: Optional[List[str]] = None,
                      escalation_policy_ids: Optional[List[str]] = None,
                      user_ids: Optional[List[str]] = None,
                      team_ids: Optional[List[str]] = None,
                      limit: int = 25,
                      offset: int = 0,
                      **kwargs: Any) -> Dict[str, Any]:
        params: Dict[str, Any] = {
            "time_zone": time_zone,
            "limit": limit,
            "offset": offset,
        }
        if since:
            params["since"] = since
        if until:
            params["until"] = until
        if schedule_ids:
            for i, sid in enumerate(schedule_ids):
                params[f"schedule_ids[{i}]"] = sid
        if escalation_policy_ids:
            for i, epid in enumerate(escalation_policy_ids):
                params[f"escalation_policy_ids[{i}]"] = epid
        if user_ids:
            for i, uid in enumerate(user_ids):
                params[f"user_ids[{i}]"] = uid
        if team_ids:
            for i, tid in enumerate(team_ids):
                params[f"team_ids[{i}]"] = tid
        params.update(kwargs)

        result = self._api_call("oncalls", params=params)
        oncalls_data = result.get("oncalls", [])
        return {
            "oncalls": [
                PagerDutyOnCall(
                    user=oc.get("user", {}),
                    schedule=oc.get("schedule", {}),
                    escalation_policy=oc.get("escalation_policy", {}),
                    escalation_level=oc.get("escalation_level", 0),
                    start=oc.get("start", ""),
                    end=oc.get("end", ""),
                    extra=oc,
                )
                for oc in oncalls_data
            ],
            "limit": result.get("limit", 0),
            "offset": result.get("offset", 0),
            "total": result.get("total", 0),
            "more": result.get("more", False),
        }

    # ------------------------------------------------------------------
    # Users and teams
    # ------------------------------------------------------------------

    def get_current_user(self) -> PagerDutyUser:
        result = self._api_call("users/me")
        return self._parse_user(result.get("user", {}))

    def get_user(self, user_id: str) -> PagerDutyUser:
        result = self._api_call(f"users/{user_id}")
        return self._parse_user(result.get("user", {}))

    def list_users(self, query: str = "",
                   team_ids: Optional[List[str]] = None,
                   limit: int = 25,
                   offset: int = 0,
                   **kwargs: Any) -> Dict[str, Any]:
        params: Dict[str, Any] = {"limit": limit, "offset": offset}
        if query:
            params["query"] = query
        if team_ids:
            for i, tid in enumerate(team_ids):
                params[f"team_ids[{i}]"] = tid
        params.update(kwargs)

        result = self._api_call("users", params=params)
        users_data = result.get("users", [])
        return {
            "users": [self._parse_user(u) for u in users_data],
            "limit": result.get("limit", 0),
            "offset": result.get("offset", 0),
            "total": result.get("total", 0),
            "more": result.get("more", False),
        }

    def _parse_user(self, data: Dict[str, Any]) -> PagerDutyUser:
        return PagerDutyUser(
            id=data.get("id", ""),
            name=data.get("name", ""),
            email=data.get("email", ""),
            time_zone=data.get("time_zone", "UTC"),
            color=data.get("color", ""),
            avatar_url=data.get("avatar_url", ""),
            billed=data.get("billed", True),
            role=data.get("role", "user"),
            description=data.get("description", ""),
            job_title=data.get("job_title", ""),
            teams=data.get("teams", []),
            contact_methods=data.get("contact_methods", []),
            notification_rules=data.get("notification_rules", []),
            self_url=data.get("self", ""),
            html_url=data.get("html_url", ""),
            summary=data.get("summary", ""),
            extra=data,
        )

    def list_teams(self, query: str = "",
                   limit: int = 25,
                   offset: int = 0,
                   **kwargs: Any) -> Dict[str, Any]:
        params: Dict[str, Any] = {"limit": limit, "offset": offset}
        if query:
            params["query"] = query
        params.update(kwargs)

        result = self._api_call("teams", params=params)
        teams_data = result.get("teams", [])
        return {
            "teams": [
                PagerDutyTeam(
                    id=t.get("id", ""),
                    name=t.get("name", ""),
                    description=t.get("description", ""),
                    default_role=t.get("default_role", "manager"),
                    self_url=t.get("self", ""),
                    html_url=t.get("html_url", ""),
                    summary=t.get("summary", ""),
                    extra=t,
                )
                for t in teams_data
            ],
            "limit": result.get("limit", 0),
            "offset": result.get("offset", 0),
            "total": result.get("total", 0),
            "more": result.get("more", False),
        }

    def get_team(self, team_id: str) -> PagerDutyTeam:
        result = self._api_call(f"teams/{team_id}")
        t = result.get("team", {})
        return PagerDutyTeam(
            id=t.get("id", ""),
            name=t.get("name", ""),
            description=t.get("description", ""),
            default_role=t.get("default_role", "manager"),
            self_url=t.get("self", ""),
            html_url=t.get("html_url", ""),
            summary=t.get("summary", ""),
            extra=t,
        )

    # ------------------------------------------------------------------
    # Incident notes
    # ------------------------------------------------------------------

    def create_note(self, incident_id: str,
                    content: str,
                    from_email: str = "") -> PagerDutyNote:
        payload = {"note": {"content": content}}
        result = self._api_call(
            f"incidents/{incident_id}/notes",
            data=payload,
            http_method="POST",
            from_email=from_email,
        )
        note = result.get("note", {})
        return PagerDutyNote(
            id=note.get("id", ""),
            content=note.get("content", ""),
            user=note.get("user", {}),
            created_at=note.get("created_at", ""),
            incident_id=incident_id,
            extra=note,
        )

    def list_notes(self, incident_id: str,
                   limit: int = 25,
                   offset: int = 0) -> Dict[str, Any]:
        params = {"limit": limit, "offset": offset}
        result = self._api_call(
            f"incidents/{incident_id}/notes", params=params
        )
        notes_data = result.get("notes", [])
        return {
            "notes": [
                PagerDutyNote(
                    id=n.get("id", ""),
                    content=n.get("content", ""),
                    user=n.get("user", {}),
                    created_at=n.get("created_at", ""),
                    incident_id=incident_id,
                    extra=n,
                )
                for n in notes_data
            ],
            "limit": result.get("limit", 0),
            "offset": result.get("offset", 0),
        }

    # ------------------------------------------------------------------
    # Webhooks
    # ------------------------------------------------------------------

    def create_webhook(self, endpoint_url: str,
                       event_types: List[str],
                       description: str = "",
                       filter_type: str = "account",
                       filter_id: str = "",
                       **kwargs: Any) -> PagerDutyWebhook:
        webhook_data: Dict[str, Any] = {
            "type": "webhook_subscription",
            "delivery_method": {
                "type": "http_delivery_method",
                "url": endpoint_url,
            },
            "description": description,
            "events": event_types,
            "filter": {
                "type": filter_type,
            },
        }
        if filter_id:
            webhook_data["filter"]["id"] = filter_id
        webhook_data.update(kwargs)

        payload = {"webhook_subscription": webhook_data}
        result = self._api_call(
            "webhook_subscriptions", data=payload, http_method="POST"
        )
        wh = result.get("webhook_subscription", {})
        return PagerDutyWebhook(
            id=wh.get("id", ""),
            type=wh.get("type", "webhook_subscription"),
            active=wh.get("active", True),
            delivery_method=wh.get("delivery_method", {}),
            description=wh.get("description", ""),
            events=wh.get("events", []),
            filter=wh.get("filter", {}),
            extra=wh,
        )

    def list_webhooks(self, limit: int = 25,
                      offset: int = 0,
                      **kwargs: Any) -> Dict[str, Any]:
        params: Dict[str, Any] = {"limit": limit, "offset": offset}
        params.update(kwargs)
        result = self._api_call("webhook_subscriptions", params=params)
        webhooks_data = result.get("webhook_subscriptions", [])
        return {
            "webhooks": [
                PagerDutyWebhook(
                    id=w.get("id", ""),
                    type=w.get("type", ""),
                    active=w.get("active", True),
                    delivery_method=w.get("delivery_method", {}),
                    description=w.get("description", ""),
                    events=w.get("events", []),
                    filter=w.get("filter", {}),
                    extra=w,
                )
                for w in webhooks_data
            ],
            "limit": result.get("limit", 0),
            "offset": result.get("offset", 0),
            "total": result.get("total", 0),
            "more": result.get("more", False),
        }

    def delete_webhook(self, webhook_id: str) -> bool:
        self._api_call(
            f"webhook_subscriptions/{webhook_id}", http_method="DELETE"
        )
        return True

    def verify_webhook(self, request_body: str,
                       signature: str,
                       secret: str = "") -> bool:
        """
        Verify a PagerDuty webhook signature.

        Args:
            request_body: Raw request body.
            signature: X-PagerDuty-Signature header value.
            secret: Webhook signing secret.

        Returns:
            True if the webhook is verified.
        """
        signing_secret = secret or self._webhook_secret
        if not signing_secret:
            return False

        # Extract the v1 signature from the header
        expected = hmac.new(
            signing_secret.encode("utf-8"),
            request_body.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()

        # Header format: "v1=signature"
        sig_value = signature.split("=", 1)[-1] if "=" in signature else signature
        return hmac.compare_digest(sig_value, expected)

    # ------------------------------------------------------------------
    # Internal API call helper
    # ------------------------------------------------------------------

    def _api_call(self, path: str,
                  params: Optional[Dict[str, Any]] = None,
                  data: Optional[Dict[str, Any]] = None,
                  http_method: str = "GET",
                  from_email: str = "") -> Dict[str, Any]:
        self._check_rate_limit()
        self._mark_request()

        url = self._build_url(path)
        if params:
            from urllib.parse import urlencode
            url = f"{url}?{urlencode(params)}"

        headers = {
            "Authorization": f"Token token={self.config.api_key or self.config.access_token or ''}",
            "Content-Type": "application/json",
            "Accept": "application/vnd.pagerduty+json;version=2",
        }
        if from_email:
            headers["From"] = from_email

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
                return json.loads(response_body)

        except urllib.error.HTTPError as e:
            response_body = e.read().decode("utf-8", errors="replace")
            self._log_response(http_method, url, e.code, response_body)
            if e.code == 429:
                self._rate_limit_hits += 1
                raise RateLimitError(
                    "Rate limited by PagerDuty",
                    retry_after=60,
                    adapter_name=self.config.name,
                )
            if e.code in (401, 403):
                raise self._handle_error(
                    "Authentication failed: invalid PagerDuty API key",
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
            f"<PagerDutyAdapter name={self.config.name} "
            f"status={self._status.value} "
            f"user={self._user_name or 'unknown'}>"
        )
