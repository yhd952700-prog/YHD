"""
Airtable Integration Adapter for LiuHao AI OS.

Provides integration with Airtable API for bases and tables, records (CRUD),
field types, views, formulas, attachments, and webhooks.
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


class AirtableFieldType(str, Enum):
    """Airtable field types."""
    SINGLE_LINE_TEXT = "singleLineText"
    MULTILINE_TEXT = "multilineText"
    RICH_TEXT = "richText"
    NUMBER = "number"
    CURRENCY = "currency"
    PERCENT = "percent"
    DURATION = "duration"
    SINGLE_SELECT = "singleSelect"
    MULTIPLE_SELECTS = "multipleSelects"
    SINGLE_COLLABORATOR = "singleCollaborator"
    MULTIPLE_COLLABORATORS = "multipleCollaborators"
    DATE = "date"
    DATE_TIME = "dateTime"
    CHECKBOX = "checkbox"
    EMAIL = "email"
    URL = "url"
    PHONE_NUMBER = "phoneNumber"
    MULTIPLE_ATTACHMENTS = "multipleAttachments"
    LINK_TO_ANOTHER_RECORD = "multipleRecordLinks"
    ROLLUP = "rollup"
    COUNT = "count"
    LOOKUP = "multipleLookupValues"
    FORMULA = "formula"
    CREATED_TIME = "createdTime"
    LAST_MODIFIED_TIME = "lastModifiedTime"
    CREATED_BY = "createdBy"
    LAST_MODIFIED_BY = "lastModifiedBy"
    BARCODE = "barcode"
    BUTTON = "button"
    RATING = "rating"
    AUTO_NUMBER = "autoNumber"
    AI_TEXT = "aiText"


class AirtableSortDirection(str, Enum):
    """Sort directions."""
    ASC = "asc"
    DESC = "desc"


class AirtableWebhookAction(str, Enum):
    """Webhook action types."""
    CREATE = "create"
    UPDATE = "update"
    DELETE = "delete"


@dataclass
class AirtableBase:
    """Represents an Airtable base."""
    id: str
    name: str = ""
    permission_level: str = ""
    tables: List[Dict[str, Any]] = field(default_factory=list)
    extra: Dict[str, Any] = field(default_factory=dict)


@dataclass
class AirtableTable:
    """Represents an Airtable table schema."""
    id: str
    name: str = ""
    primary_field_id: str = ""
    fields: List[Dict[str, Any]] = field(default_factory=list)
    views: List[Dict[str, Any]] = field(default_factory=list)
    base_id: str = ""
    description: str = ""
    extra: Dict[str, Any] = field(default_factory=dict)


@dataclass
class AirtableRecord:
    """Represents an Airtable record."""
    id: str = ""
    fields: Dict[str, Any] = field(default_factory=dict)
    created_time: str = ""
    table_id: str = ""
    base_id: str = ""
    view_id: str = ""
    comment_count: int = 0
    extra: Dict[str, Any] = field(default_factory=dict)


@dataclass
class AirtableView:
    """Represents an Airtable view."""
    id: str
    name: str = ""
    type: str = ""
    table_id: str = ""
    visible_field_ids: List[str] = field(default_factory=list)
    extra: Dict[str, Any] = field(default_factory=dict)


@dataclass
class AirtableAttachment:
    """Represents an Airtable attachment."""
    id: str
    url: str = ""
    filename: str = ""
    size: int = 0
    type: str = ""
    width: int = 0
    height: int = 0
    thumbnails: Dict[str, Any] = field(default_factory=dict)
    extra: Dict[str, Any] = field(default_factory=dict)


@dataclass
class AirtableWebhook:
    """Represents an Airtable webhook."""
    id: str
    base_id: str = ""
    notification_url: str = ""
    specification: Dict[str, Any] = field(default_factory=dict)
    are_notifications_enabled: bool = True
    cursor_for_next_payload: int = 1
    expiration_time: str = ""
    last_successful_notification_time: str = ""
    extra: Dict[str, Any] = field(default_factory=dict)


@dataclass
class AirtableSort:
    """Represents a sort specification for list records."""
    field: str
    direction: AirtableSortDirection = AirtableSortDirection.ASC


class AirtableAdapter(BaseIntegrationAdapter):
    """
    Airtable integration adapter for LiuHao AI OS.

    Supports:
    - Bases and tables management
    - Records (list, create, update, delete)
    - Field types and schema
    - Views
    - Formulas and filtering
    - Attachments
    - Webhooks
    """

    def __init__(self, config: IntegrationConfig):
        if config.auth_type == AuthType.NONE:
            config.auth_type = AuthType.BEARER_TOKEN
        if not config.base_url:
            config.base_url = "https://api.airtable.com/v0"
        super().__init__(config)
        self._user_id: Optional[str] = None
        self._user_email: Optional[str] = None
        self._cached_bases: Dict[str, AirtableBase] = {}
        self._cached_tables: Dict[str, AirtableTable] = {}
        self._webhooks: List[AirtableWebhook] = []

    # ------------------------------------------------------------------
    # Base interface
    # ------------------------------------------------------------------

    def connect(self) -> bool:
        self._status = ConnectionStatus.CONNECTING
        self._logger.info("Connecting to Airtable API...")

        try:
            result = self._api_call("meta/whoami")
            self._user_id = result.get("id", "")
            self._user_email = result.get("email", "")
            self._status = ConnectionStatus.CONNECTED
            self._connected_at = time.time()
            self._logger.info(
                "Connected to Airtable as %s",
                self._user_email or self._user_id,
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
        self._user_email = None
        self._cached_bases = {}
        self._cached_tables = {}
        self._webhooks = []
        self._session = None
        self._logger.info("Disconnected from Airtable")
        return True

    def is_connected(self) -> bool:
        return self._status == ConnectionStatus.CONNECTED

    def get_status(self) -> IntegrationStatus:
        return self._build_status({
            "user_email": self._user_email,
            "user_id": self._user_id,
            "cached_bases": len(self._cached_bases),
            "cached_tables": len(self._cached_tables),
            "webhooks": len(self._webhooks),
        })

    def test_connection(self) -> bool:
        try:
            if not self.is_connected():
                return False
            result = self._api_call("meta/whoami")
            return "id" in result
        except Exception:
            return False

    # ------------------------------------------------------------------
    # Bases and tables
    # ------------------------------------------------------------------

    def list_bases(self, offset: str = "",
                   limit: int = 100) -> Dict[str, Any]:
        params: Dict[str, Any] = {"limit": limit}
        if offset:
            params["offset"] = offset

        result = self._api_call("meta/bases", params=params)
        bases_data = result.get("bases", [])
        bases = [
            AirtableBase(
                id=b.get("id", ""),
                name=b.get("name", ""),
                permission_level=b.get("permissionLevel", ""),
                extra=b,
            )
            for b in bases_data
        ]
        for base in bases:
            self._cached_bases[base.id] = base

        return {
            "bases": bases,
            "offset": result.get("offset", ""),
        }

    def get_base_schema(self, base_id: str) -> AirtableBase:
        result = self._api_call(f"meta/bases/{base_id}/tables")
        tables_data = result.get("tables", [])
        base = AirtableBase(
            id=base_id,
            tables=tables_data,
            extra=result,
        )
        self._cached_bases[base_id] = base

        for t in tables_data:
            table = AirtableTable(
                id=t.get("id", ""),
                name=t.get("name", ""),
                primary_field_id=t.get("primaryFieldId", ""),
                fields=t.get("fields", []),
                views=t.get("views", []),
                base_id=base_id,
                description=t.get("description", ""),
                extra=t,
            )
            self._cached_tables[f"{base_id}/{table.id}"] = table

        return base

    def create_table(self, base_id: str,
                     name: str,
                     fields: List[Dict[str, Any]],
                     description: str = "") -> AirtableTable:
        payload: Dict[str, Any] = {
            "name": name,
            "fields": fields,
        }
        if description:
            payload["description"] = description

        result = self._api_call(
            f"meta/bases/{base_id}/tables",
            data=payload,
            http_method="POST",
        )
        table = AirtableTable(
            id=result.get("id", ""),
            name=result.get("name", ""),
            primary_field_id=result.get("primaryFieldId", ""),
            fields=result.get("fields", []),
            views=result.get("views", []),
            base_id=base_id,
            description=result.get("description", ""),
            extra=result,
        )
        self._cached_tables[f"{base_id}/{table.id}"] = table
        return table

    def update_table(self, base_id: str,
                     table_id: str,
                     name: Optional[str] = None,
                     description: Optional[str] = None,
                     fields: Optional[List[Dict[str, Any]]] = None
                     ) -> AirtableTable:
        payload: Dict[str, Any] = {}
        if name is not None:
            payload["name"] = name
        if description is not None:
            payload["description"] = description
        if fields is not None:
            payload["fields"] = fields

        result = self._api_call(
            f"meta/bases/{base_id}/tables/{table_id}",
            data=payload,
            http_method="PATCH",
        )
        table = AirtableTable(
            id=result.get("id", table_id),
            name=result.get("name", ""),
            primary_field_id=result.get("primaryFieldId", ""),
            fields=result.get("fields", []),
            views=result.get("views", []),
            base_id=base_id,
            description=result.get("description", ""),
            extra=result,
        )
        self._cached_tables[f"{base_id}/{table.id}"] = table
        return table

    def list_fields(self, base_id: str,
                    table_id: str) -> List[Dict[str, Any]]:
        key = f"{base_id}/{table_id}"
        if key in self._cached_tables:
            return self._cached_tables[key].fields

        schema = self.get_base_schema(base_id)
        for t in schema.tables:
            if t.get("id") == table_id:
                return t.get("fields", [])
        return []

    # ------------------------------------------------------------------
    # Records
    # ------------------------------------------------------------------

    def list_records(self, base_id: str,
                     table_id: str,
                     view: str = "",
                     fields: Optional[List[str]] = None,
                     filter_by_formula: str = "",
                     max_records: int = 100,
                     offset: str = "",
                     sort: Optional[List[AirtableSort]] = None,
                     cell_format: str = "json",
                     time_zone: str = "",
                     user_locale: str = "",
                     return_fields_by_field_id: bool = False,
                     **kwargs: Any) -> Dict[str, Any]:
        params: Dict[str, Any] = {
            "maxRecords": max_records,
            "cellFormat": cell_format,
            "returnFieldsByFieldId": str(return_fields_by_field_id).lower(),
        }
        if view:
            params["view"] = view
        if fields:
            for i, f in enumerate(fields):
                params[f"fields[{i}]"] = f
        if filter_by_formula:
            params["filterByFormula"] = filter_by_formula
        if offset:
            params["offset"] = offset
        if sort:
            for i, s in enumerate(sort):
                params[f"sort[{i}][field]"] = s.field
                params[f"sort[{i}][direction]"] = s.direction.value
        if time_zone:
            params["timeZone"] = time_zone
        if user_locale:
            params["userLocale"] = user_locale
        params.update(kwargs)

        result = self._api_call(f"{base_id}/{table_id}", params=params)
        records_data = result.get("records", [])
        records = [
            self._parse_record(r, base_id, table_id, view)
            for r in records_data
        ]

        return {
            "records": records,
            "offset": result.get("offset", ""),
        }

    def get_record(self, base_id: str,
                   table_id: str,
                   record_id: str,
                   cell_format: str = "json",
                   **kwargs: Any) -> AirtableRecord:
        params: Dict[str, Any] = {"cellFormat": cell_format}
        params.update(kwargs)

        result = self._api_call(
            f"{base_id}/{table_id}/{record_id}", params=params
        )
        return self._parse_record(result, base_id, table_id)

    def create_record(self, base_id: str,
                      table_id: str,
                      fields: Dict[str, Any],
                      typecast: bool = False,
                      **kwargs: Any) -> AirtableRecord:
        payload: Dict[str, Any] = {
            "fields": fields,
            "typecast": typecast,
        }
        payload.update(kwargs)

        result = self._api_call(
            f"{base_id}/{table_id}",
            data=payload,
            http_method="POST",
        )
        return self._parse_record(result, base_id, table_id)

    def create_records(self, base_id: str,
                       table_id: str,
                       records: List[Dict[str, Any]],
                       typecast: bool = False,
                       **kwargs: Any) -> List[AirtableRecord]:
        payload: Dict[str, Any] = {
            "records": records,
            "typecast": typecast,
        }
        payload.update(kwargs)

        result = self._api_call(
            f"{base_id}/{table_id}",
            data=payload,
            http_method="POST",
        )
        records_data = result.get("records", [])
        return [
            self._parse_record(r, base_id, table_id)
            for r in records_data
        ]

    def update_record(self, base_id: str,
                      table_id: str,
                      record_id: str,
                      fields: Dict[str, Any],
                      typecast: bool = False,
                      destructive: bool = False) -> AirtableRecord:
        payload: Dict[str, Any] = {
            "fields": fields,
            "typecast": typecast,
        }
        method = "PUT" if destructive else "PATCH"

        result = self._api_call(
            f"{base_id}/{table_id}/{record_id}",
            data=payload,
            http_method=method,
        )
        return self._parse_record(result, base_id, table_id)

    def update_records(self, base_id: str,
                       table_id: str,
                       records: List[Dict[str, Any]],
                       typecast: bool = False,
                       destructive: bool = False) -> List[AirtableRecord]:
        payload: Dict[str, Any] = {
            "records": records,
            "typecast": typecast,
        }
        method = "PUT" if destructive else "PATCH"

        result = self._api_call(
            f"{base_id}/{table_id}",
            data=payload,
            http_method=method,
        )
        records_data = result.get("records", [])
        return [
            self._parse_record(r, base_id, table_id)
            for r in records_data
        ]

    def delete_record(self, base_id: str,
                      table_id: str,
                      record_id: str) -> bool:
        result = self._api_call(
            f"{base_id}/{table_id}/{record_id}",
            http_method="DELETE",
        )
        return result.get("deleted", False)

    def delete_records(self, base_id: str,
                       table_id: str,
                       record_ids: List[str]) -> Dict[str, Any]:
        params = {}
        for i, rid in enumerate(record_ids):
            params[f"records[{i}]"] = rid

        result = self._api_call(
            f"{base_id}/{table_id}",
            params=params,
            http_method="DELETE",
        )
        return result

    def _parse_record(self, data: Dict[str, Any],
                      base_id: str,
                      table_id: str,
                      view_id: str = "") -> AirtableRecord:
        return AirtableRecord(
            id=data.get("id", ""),
            fields=data.get("fields", {}),
            created_time=data.get("createdTime", ""),
            table_id=table_id,
            base_id=base_id,
            view_id=view_id,
            comment_count=data.get("commentCount", 0),
            extra=data,
        )

    # ------------------------------------------------------------------
    # Views
    # ------------------------------------------------------------------

    def list_views(self, base_id: str,
                   table_id: str) -> List[AirtableView]:
        result = self._api_call(f"meta/bases/{base_id}/tables/{table_id}/views")
        views_data = result.get("views", [])
        return [
            AirtableView(
                id=v.get("id", ""),
                name=v.get("name", ""),
                type=v.get("type", ""),
                table_id=table_id,
                visible_field_ids=v.get("visibleFieldIds", []),
                extra=v,
            )
            for v in views_data
        ]

    def get_view(self, base_id: str,
                 table_id: str,
                 view_id: str) -> AirtableView:
        result = self._api_call(
            f"meta/bases/{base_id}/tables/{table_id}/views/{view_id}"
        )
        return AirtableView(
            id=result.get("id", view_id),
            name=result.get("name", ""),
            type=result.get("type", ""),
            table_id=table_id,
            visible_field_ids=result.get("visibleFieldIds", []),
            extra=result,
        )

    # ------------------------------------------------------------------
    # Formulas
    # ------------------------------------------------------------------

    @staticmethod
    def build_formula_and(*formulas: str) -> str:
        """Build an AND formula from multiple conditions."""
        if len(formulas) == 0:
            return ""
        if len(formulas) == 1:
            return formulas[0]
        return f"AND({', '.join(formulas)})"

    @staticmethod
    def build_formula_or(*formulas: str) -> str:
        """Build an OR formula from multiple conditions."""
        if len(formulas) == 0:
            return ""
        if len(formulas) == 1:
            return formulas[0]
        return f"OR({', '.join(formulas)})"

    @staticmethod
    def build_formula_equals(field: str, value: str) -> str:
        """Build an equals comparison formula."""
        escaped_value = value.replace('"', '\\"')
        return '{{{field}}}="{value}"'.format(
            field=field, value=escaped_value
        )

    @staticmethod
    def build_formula_contains(field: str, value: str) -> str:
        """Build a FIND formula for substring matching."""
        escaped_value = value.replace("'", "\\'")
        return "FIND('{value}', {{{field}}}) > 0".format(
            value=escaped_value, field=field
        )

    @staticmethod
    def build_formula_is_blank(field: str) -> str:
        """Build a BLANK check formula."""
        return f"BLANK()={{{field}}}"

    # ------------------------------------------------------------------
    # Attachments
    # ------------------------------------------------------------------

    @staticmethod
    def build_attachment_upload(url: str,
                                filename: str = "") -> Dict[str, Any]:
        """
        Build an attachment field value from a URL.

        Args:
            url: URL of the file to attach.
            filename: Optional filename override.

        Returns:
            Dictionary suitable for attachment field value.
        """
        att: Dict[str, Any] = {"url": url}
        if filename:
            att["filename"] = filename
        return [att]

    @staticmethod
    def parse_attachments(field_value: Any) -> List[AirtableAttachment]:
        """
        Parse attachment field value into attachment objects.

        Args:
            field_value: Raw attachment field value (list of dicts).

        Returns:
            List of AirtableAttachment objects.
        """
        if not field_value or not isinstance(field_value, list):
            return []
        return [
            AirtableAttachment(
                id=a.get("id", ""),
                url=a.get("url", ""),
                filename=a.get("filename", ""),
                size=a.get("size", 0),
                type=a.get("type", ""),
                width=a.get("width", 0),
                height=a.get("height", 0),
                thumbnails=a.get("thumbnails", {}),
                extra=a,
            )
            for a in field_value
        ]

    # ------------------------------------------------------------------
    # Webhooks
    # ------------------------------------------------------------------

    def create_webhook(self, base_id: str,
                       notification_url: str,
                       specification: Dict[str, Any],
                       **kwargs: Any) -> AirtableWebhook:
        payload: Dict[str, Any] = {
            "notificationUrl": notification_url,
            "specification": specification,
        }
        payload.update(kwargs)

        result = self._api_call(
            f"bases/{base_id}/webhooks",
            data=payload,
            http_method="POST",
        )
        webhook = AirtableWebhook(
            id=result.get("id", ""),
            base_id=base_id,
            notification_url=notification_url,
            specification=specification,
            expiration_time=result.get("expirationTime", ""),
            extra=result,
        )
        self._webhooks.append(webhook)
        return webhook

    def list_webhooks(self, base_id: str) -> List[AirtableWebhook]:
        result = self._api_call(f"bases/{base_id}/webhooks")
        webhooks_data = result.get("webhooks", [])
        webhooks = [
            AirtableWebhook(
                id=w.get("id", ""),
                base_id=base_id,
                notification_url=w.get("notificationUrl", ""),
                specification=w.get("specification", {}),
                are_notifications_enabled=w.get(
                    "areNotificationsEnabled", True
                ),
                cursor_for_next_payload=w.get("cursorForNextPayload", 1),
                expiration_time=w.get("expirationTime", ""),
                last_successful_notification_time=w.get(
                    "lastSuccessfulNotificationTime", ""
                ),
                extra=w,
            )
            for w in webhooks_data
        ]
        return webhooks

    def delete_webhook(self, base_id: str, webhook_id: str) -> bool:
        self._api_call(
            f"bases/{base_id}/webhooks/{webhook_id}",
            http_method="DELETE",
        )
        self._webhooks = [
            w for w in self._webhooks if w.id != webhook_id
        ]
        return True

    def get_webhook_payloads(self, base_id: str,
                             webhook_id: str,
                             cursor: int = 1,
                             limit: int = 50) -> Dict[str, Any]:
        params = {"cursor": cursor, "limit": limit}
        result = self._api_call(
            f"bases/{base_id}/webhooks/{webhook_id}/payloads",
            params=params,
        )
        return result

    def refresh_webhook(self, base_id: str,
                        webhook_id: str) -> AirtableWebhook:
        result = self._api_call(
            f"bases/{base_id}/webhooks/{webhook_id}/refresh",
            http_method="POST",
        )
        return AirtableWebhook(
            id=webhook_id,
            base_id=base_id,
            expiration_time=result.get("expirationTime", ""),
            extra=result,
        )

    def verify_webhook(self, request_body: str,
                       x_airtable_content_hash: str,
                       webhook_secret: str) -> bool:
        """
        Verify an Airtable webhook signature.

        Args:
            request_body: Raw request body.
            x_airtable_content_hash: X-Airtable-Content-Hash header.
            webhook_secret: Webhook signing secret.

        Returns:
            True if the webhook is verified.
        """
        expected = hmac.new(
            webhook_secret.encode("utf-8"),
            request_body.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()
        return hmac.compare_digest(x_airtable_content_hash, expected)

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
            "Authorization": f"Bearer {self.config.access_token or self.config.api_key or ''}",
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
                return json.loads(response_body)

        except urllib.error.HTTPError as e:
            response_body = e.read().decode("utf-8", errors="replace")
            self._log_response(http_method, url, e.code, response_body)
            if e.code == 429:
                self._rate_limit_hits += 1
                raise RateLimitError(
                    "Rate limited by Airtable",
                    retry_after=30,
                    adapter_name=self.config.name,
                )
            if e.code in (401, 403):
                raise self._handle_error(
                    "Authentication failed: invalid Airtable token",
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
            f"<AirtableAdapter name={self.config.name} "
            f"status={self._status.value} "
            f"user={self._user_email or 'unknown'}>"
        )
