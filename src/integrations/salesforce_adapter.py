"""
Salesforce Integration Adapter for LiuHao AI OS.

Provides integration with Salesforce for object CRUD (Accounts, Contacts,
Opportunities, Cases), SOQL queries, bulk operations, Chatter,
user management, and metadata interface.
"""

from __future__ import annotations

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


class SObjectType(str, Enum):
    """Common Salesforce object types."""
    ACCOUNT = "Account"
    CONTACT = "Contact"
    OPPORTUNITY = "Opportunity"
    CASE = "Case"
    LEAD = "Lead"
    TASK = "Task"
    EVENT = "Event"
    USER = "User"
    OPPORTUNITY_LINE_ITEM = "OpportunityLineItem"
    CAMPAIGN = "Campaign"


class CaseStatus(str, Enum):
    """Case status values."""
    NEW = "New"
    WORKING = "Working"
    ESCALATED = "Escalated"
    CLOSED = "Closed"


class OpportunityStage(str, Enum):
    """Common opportunity stages."""
    PROSPECTING = "Prospecting"
    QUALIFICATION = "Qualification"
    NEEDS_ANALYSIS = "Needs Analysis"
    VALUE_PROPOSITION = "Value Proposition"
    ID_DECISION_MAKERS = "Id. Decision Makers"
    PERCEPTION_ANALYSIS = "Perception Analysis"
    PROPOSAL_PRICE_QUOTE = "Proposal/Price Quote"
    NEGOTIATION_REVIEW = "Negotiation/Review"
    CLOSED_WON = "Closed Won"
    CLOSED_LOST = "Closed Lost"


@dataclass
class SObjectRecord:
    """Represents a generic Salesforce object record."""
    id: str
    object_type: str
    fields: Dict[str, Any] = field(default_factory=dict)
    attributes: Dict[str, Any] = field(default_factory=dict)

    def get(self, field: str, default: Any = None) -> Any:
        return self.fields.get(field, default)

    def __getitem__(self, field: str) -> Any:
        return self.fields[field]

    def __contains__(self, field: str) -> bool:
        return field in self.fields


@dataclass
class SalesforceUser:
    """Represents a Salesforce user."""
    id: str
    username: str
    name: str = ""
    email: str = ""
    profile_id: str = ""
    user_type: str = ""
    is_active: bool = True
    last_login_date: str = ""
    extra: Dict[str, Any] = field(default_factory=dict)


@dataclass
class SOQLQueryResult:
    """Result from a SOQL query."""
    records: List[SObjectRecord]
    total_size: int
    done: bool
    next_records_url: Optional[str] = None
    query_locator: Optional[str] = None


@dataclass
class BulkJob:
    """Represents a Bulk API job."""
    id: str
    object_type: str
    operation: str
    state: str = ""
    created_date: str = ""
    system_modstamp: str = ""
    number_records_processed: int = 0
    number_records_failed: int = 0
    number_records_successful: int = 0
    extra: Dict[str, Any] = field(default_factory=dict)


@dataclass
class ChatterFeedItem:
    """Represents a Chatter feed item (post)."""
    id: str
    feed_item_type: str
    body: str = ""
    parent_id: str = ""
    parent_type: str = ""
    created_by_id: str = ""
    created_by_name: str = ""
    created_date: str = ""
    url: str = ""
    extra: Dict[str, Any] = field(default_factory=dict)


class SalesforceAdapter(BaseIntegrationAdapter):
    """
    Salesforce integration adapter for LiuHao AI OS.

    Supports Salesforce REST API v59.0 with:
    - Standard object CRUD (Accounts, Contacts, Opportunities, Cases, etc.)
    - SOQL query execution with automatic pagination
    - Bulk API 2.0 for large data operations
    - Chatter feeds and posts
    - User management and querying
    - Metadata API (describe objects, fields)
    - OAuth token-based authentication
    """

    def __init__(self, config: IntegrationConfig):
        """
        Initialize Salesforce adapter.

        Args:
            config: Integration config.
                    base_url: Salesforce instance URL
                              (e.g., https://na1.salesforce.com/)
                    access_token: OAuth access token
                    api_version: Salesforce API version (default v59.0)
        """
        if config.auth_type == AuthType.NONE:
            config.auth_type = AuthType.BEARER_TOKEN
        super().__init__(config)
        self._api_version: str = config.extra.get("api_version", "59.0")
        self._instance_url: str = ""
        self._user_id: Optional[str] = None
        self._username: Optional[str] = None
        self._org_id: Optional[str] = None

    @property
    def _api_base(self) -> str:
        """Base URL for REST API calls."""
        base = self._instance_url or self.config.base_url
        return f"{base.rstrip('/')}/services/data/v{self._api_version}/"

    # ------------------------------------------------------------------
    # Base interface
    # ------------------------------------------------------------------

    def connect(self) -> bool:
        """Connect to Salesforce and validate token."""
        self._status = ConnectionStatus.CONNECTING
        self._logger.info("Connecting to Salesforce API...")

        try:
            # Use the identity endpoint to validate token
            # First, try to get limits info (requires valid token)
            limits = self._api_call("GET", "limits")
            # Get user info via SOQL
            result = self.query(
                "SELECT Id, Username, Name, Email FROM User WHERE IsActive = true LIMIT 1"
            )
            if result.records:
                # This isn't the current user, just a test
                pass

            # Use identity URL from response headers if available
            # For now, set a basic connected state
            self._instance_url = self.config.base_url.rstrip("/")
            self._status = ConnectionStatus.CONNECTED
            self._connected_at = time.time()
            self._logger.info(
                "Connected to Salesforce (API v%s)", self._api_version,
            )
            return True
        except IntegrationError:
            raise
        except Exception as e:
            self._status = ConnectionStatus.ERROR
            raise self._handle_error(f"Connection error: {e}")

    def disconnect(self) -> bool:
        """Disconnect from Salesforce."""
        self._status = ConnectionStatus.DISCONNECTED
        self._user_id = None
        self._username = None
        self._org_id = None
        self._session = None
        self._logger.info("Disconnected from Salesforce")
        return True

    def is_connected(self) -> bool:
        return self._status == ConnectionStatus.CONNECTED

    def get_status(self) -> IntegrationStatus:
        return self._build_status({
            "api_version": self._api_version,
            "instance_url": self._instance_url,
            "user_id": self._user_id,
            "org_id": self._org_id,
        })

    def test_connection(self) -> bool:
        try:
            if not self.is_connected():
                return False
            result = self._api_call("GET", "limits")
            return "ConcurrentAsyncGetReportInstances" in result
        except Exception:
            return False

    # ------------------------------------------------------------------
    # Object CRUD
    # ------------------------------------------------------------------

    def get_record(self, object_type: str, record_id: str,
                   fields: Optional[List[str]] = None) -> SObjectRecord:
        """
        Get a record by ID.

        Args:
            object_type: Object type (e.g., "Account", "Contact").
            record_id: Salesforce record ID.
            fields: List of fields to retrieve (None = all).

        Returns:
            SObjectRecord with requested fields.
        """
        params: Dict[str, Any] = {}
        if fields:
            params["fields"] = ",".join(fields)

        data = self._api_call(
            "GET", f"sobjects/{object_type}/{record_id}", params=params,
        )
        return self._parse_sobject(data, object_type)

    def create_record(self, object_type: str,
                      fields: Dict[str, Any]) -> str:
        """
        Create a new record.

        Args:
            object_type: Object type name.
            fields: Field name -> value mapping.

        Returns:
            ID of the created record.
        """
        data = self._api_call(
            "POST", f"sobjects/{object_type}", json_body=fields,
        )
        if not data.get("success", False):
            errors = data.get("errors", [])
            raise self._handle_error(
                f"Failed to create {object_type}: {errors}",
            )
        return data.get("id", "")

    def update_record(self, object_type: str, record_id: str,
                      fields: Dict[str, Any]) -> bool:
        """
        Update a record.

        Args:
            object_type: Object type name.
            record_id: Record ID to update.
            fields: Fields to update.

        Returns:
            True on success.
        """
        try:
            self._api_call(
                "PATCH", f"sobjects/{object_type}/{record_id}",
                json_body=fields,
            )
            return True
        except Exception:
            return False

    def delete_record(self, object_type: str, record_id: str) -> bool:
        """Delete a record. Returns True on success."""
        try:
            self._api_call("DELETE", f"sobjects/{object_type}/{record_id}")
            return True
        except Exception:
            return False

    def upsert_record(self, object_type: str,
                      external_id_field: str,
                      external_id_value: str,
                      fields: Dict[str, Any]) -> Tuple[str, bool]:
        """
        Upsert a record using an external ID field.

        Args:
            object_type: Object type name.
            external_id_field: Name of the external ID field.
            external_id_value: Value of the external ID.
            fields: Fields to set.

        Returns:
            Tuple of (record_id, created_flag).
        """
        path = f"sobjects/{object_type}/{external_id_field}/{external_id_value}"
        data = self._api_call("PATCH", path, json_body=fields)
        return data.get("id", ""), data.get("created", False)

    # ------------------------------------------------------------------
    # Convenience methods for common objects
    # ------------------------------------------------------------------

    def create_account(self, name: str,
                       **kwargs: Any) -> str:
        """Create an Account record."""
        fields = {"Name": name, **kwargs}
        return self.create_record("Account", fields)

    def get_account(self, account_id: str,
                    fields: Optional[List[str]] = None) -> SObjectRecord:
        """Get an Account by ID."""
        return self.get_record("Account", account_id, fields)

    def update_account(self, account_id: str,
                       fields: Dict[str, Any]) -> bool:
        """Update an Account."""
        return self.update_record("Account", account_id, fields)

    def create_contact(self, last_name: str,
                       account_id: Optional[str] = None,
                       **kwargs: Any) -> str:
        """Create a Contact record."""
        fields = {"LastName": last_name, **kwargs}
        if account_id:
            fields["AccountId"] = account_id
        return self.create_record("Contact", fields)

    def create_opportunity(self, name: str,
                           close_date: str,
                           stage_name: str = OpportunityStage.PROSPECTING.value,
                           amount: Optional[float] = None,
                           account_id: Optional[str] = None,
                           **kwargs: Any) -> str:
        """Create an Opportunity record."""
        fields = {
            "Name": name,
            "CloseDate": close_date,
            "StageName": stage_name,
            **kwargs,
        }
        if amount is not None:
            fields["Amount"] = amount
        if account_id:
            fields["AccountId"] = account_id
        return self.create_record("Opportunity", fields)

    def create_case(self, subject: str,
                    status: str = CaseStatus.NEW.value,
                    contact_id: Optional[str] = None,
                    account_id: Optional[str] = None,
                    priority: str = "Medium",
                    description: str = "",
                    **kwargs: Any) -> str:
        """Create a Case record."""
        fields = {
            "Subject": subject,
            "Status": status,
            "Priority": priority,
            "Description": description,
            **kwargs,
        }
        if contact_id:
            fields["ContactId"] = contact_id
        if account_id:
            fields["AccountId"] = account_id
        return self.create_record("Case", fields)

    # ------------------------------------------------------------------
    # SOQL Query
    # ------------------------------------------------------------------

    def query(self, soql: str,
              include_all: bool = False) -> SOQLQueryResult:
        """
        Execute a SOQL query.

        Args:
            soql: SOQL query string.
            include_all: If True, include deleted/archived records.

        Returns:
            SOQLQueryResult with records and pagination info.
        """
        path = "queryAll" if include_all else "query"
        data = self._api_call("GET", path, params={"q": soql})
        return self._parse_query_result(data)

    def query_more(self, next_records_url: str) -> SOQLQueryResult:
        """
        Get next page of query results.

        Args:
            next_records_url: The nextRecordsUrl from a previous query.

        Returns:
            Next page of results.
        """
        # next_records_url is a full path like /services/data/vXX.X/query/01g...
        if next_records_url.startswith("/services/data/"):
            # Extract relative path
            data = self._api_call("GET", "", full_path=next_records_url)
        else:
            data = self._api_call("GET", next_records_url)
        return self._parse_query_result(data)

    def query_all(self, soql: str,
                  max_pages: int = 100) -> List[SObjectRecord]:
        """
        Execute a query and fetch all pages of results.

        Args:
            soql: SOQL query string.
            max_pages: Maximum pages to fetch.

        Returns:
            List of all matching records.
        """
        all_records: List[SObjectRecord] = []
        result = self.query(soql)
        all_records.extend(result.records)
        pages = 1

        while not result.done and result.next_records_url and pages < max_pages:
            result = self.query_more(result.next_records_url)
            all_records.extend(result.records)
            pages += 1

        return all_records

    def count_query(self, soql: str) -> int:
        """
        Get the count of records matching a query.

        Args:
            soql: Query string (should use COUNT()).

        Returns:
            Total count.
        """
        result = self.query(soql)
        if result.records:
            return result.records[0].fields.get("expr0", 0)
        return 0

    # ------------------------------------------------------------------
    # Bulk operations
    # ------------------------------------------------------------------

    def create_bulk_job(self, object_type: str,
                        operation: str,
                        external_id_field: Optional[str] = None,
                        content_type: str = "JSON") -> BulkJob:
        """
        Create a Bulk API 2.0 job.

        Args:
            object_type: Object type.
            operation: "insert", "update", "upsert", "delete", "hardDelete".
            external_id_field: Required for upsert operations.
            content_type: "JSON" or "CSV".

        Returns:
            BulkJob object.
        """
        payload: Dict[str, Any] = {
            "object": object_type,
            "operation": operation,
            "contentType": content_type,
        }
        if external_id_field:
            payload["externalIdFieldName"] = external_id_field

        data = self._api_call(
            "POST", "jobs/ingest", json_body=payload,
        )
        return self._parse_bulk_job(data)

    def upload_bulk_data(self, job_id: str, records: List[Dict[str, Any]]) -> bool:
        """
        Upload data to a bulk job.

        Args:
            job_id: Bulk job ID.
            records: List of record dicts.

        Returns:
            True on success.
        """
        # Each record on its own line (NDJSON format for JSON content type)
        body_lines = [json.dumps(r) for r in records]
        body = "\n".join(body_lines)

        try:
            self._api_call(
                "PUT", f"jobs/ingest/{job_id}/batches",
                raw_body=body,
                content_type="application/json",
            )
            return True
        except Exception:
            return False

    def close_bulk_job(self, job_id: str,
                       state: str = "UploadComplete") -> BulkJob:
        """
        Close/start a bulk job by setting its state.

        Args:
            job_id: Job ID.
            state: New state ("UploadComplete" to start processing).

        Returns:
            Updated BulkJob.
        """
        data = self._api_call(
            "PATCH", f"jobs/ingest/{job_id}",
            json_body={"state": state},
        )
        return self._parse_bulk_job(data)

    def get_bulk_job(self, job_id: str) -> BulkJob:
        """Get status of a bulk job."""
        data = self._api_call("GET", f"jobs/ingest/{job_id}")
        return self._parse_bulk_job(data)

    def get_bulk_job_results(self, job_id: str) -> Dict[str, Any]:
        """Get successful/failed results of a completed bulk job."""
        return self._api_call("GET", f"jobs/ingest/{job_id}/successfulResults")

    def bulk_insert(self, object_type: str,
                    records: List[Dict[str, Any]],
                    wait_for_completion: bool = True,
                    max_wait_seconds: int = 300) -> BulkJob:
        """
        Perform a bulk insert operation.

        Args:
            object_type: Object type name.
            records: List of record field dicts.
            wait_for_completion: Whether to wait for job to finish.
            max_wait_seconds: Max time to wait.

        Returns:
            Final BulkJob state.
        """
        job = self.create_bulk_job(object_type, "insert")
        self.upload_bulk_data(job.id, records)
        job = self.close_bulk_job(job.id)

        if wait_for_completion:
            start = time.time()
            while job.state not in ("JobComplete", "Failed"):
                if time.time() - start > max_wait_seconds:
                    break
                time.sleep(5)
                job = self.get_bulk_job(job.id)

        return job

    # ------------------------------------------------------------------
    # Chatter (Feeds/Posts)
    # ------------------------------------------------------------------

    def get_feed_items(self, feed_type: str = "news",
                       page_size: int = 20,
                       sort: str = "LastModifiedDateDesc") -> List[ChatterFeedItem]:
        """
        Get Chatter feed items.

        Args:
            feed_type: Feed type ("news", "record", "user", etc.).
            page_size: Number of items.
            sort: Sort order.

        Returns:
            List of ChatterFeedItem objects.
        """
        params = {"pageSize": page_size, "sort": sort}
        data = self._api_call(
            "GET", f"chatter/feed-elements/{feed_type}", params=params,
        )
        items = []
        for element in data.get("elements", []):
            items.append(self._parse_feed_item(element))
        return items

    def post_to_feed(self, parent_id: str,
                     text: str,
                     feed_type: str = "record") -> ChatterFeedItem:
        """
        Post a message to a record/user feed.

        Args:
            parent_id: ID of the record or user to post to.
            text: Post text body.
            feed_type: Type of feed ("record", "user", "group").

        Returns:
            Created feed item.
        """
        payload = {
            "body": {
                "messageSegments": [{"type": "Text", "text": text}],
            },
            "feedElementType": "FeedItem",
            "subjectId": parent_id,
        }

        data = self._api_call(
            "POST", "chatter/feed-elements", json_body=payload,
        )
        return self._parse_feed_item(data)

    def post_to_record_feed(self, record_id: str, text: str) -> ChatterFeedItem:
        """Post to a record's Chatter feed."""
        return self.post_to_feed(record_id, text, feed_type="record")

    def get_record_feed(self, record_id: str,
                        page_size: int = 20) -> List[ChatterFeedItem]:
        """Get Chatter feed for a specific record."""
        data = self._api_call(
            "GET", f"chatter/feed-elements/record/{record_id}",
            params={"pageSize": page_size},
        )
        return [self._parse_feed_item(e) for e in data.get("elements", [])]

    # ------------------------------------------------------------------
    # User management
    # ------------------------------------------------------------------

    def get_user(self, user_id: str,
                 fields: Optional[List[str]] = None) -> SalesforceUser:
        """Get a user by ID."""
        record = self.get_record("User", user_id, fields)
        return self._parse_user(record)

    def query_users(self, query_filter: str = "",
                    fields: Optional[List[str]] = None) -> List[SalesforceUser]:
        """
        Query users with optional filter.

        Args:
            query_filter: WHERE clause (without "WHERE").
            fields: Fields to retrieve.

        Returns:
            List of SalesforceUser objects.
        """
        field_list = ",".join(fields) if fields else (
            "Id, Username, Name, Email, ProfileId, UserType, "
            "IsActive, LastLoginDate"
        )
        soql = f"SELECT {field_list} FROM User"
        if query_filter:
            soql += f" WHERE {query_filter}"

        records = self.query_all(soql)
        return [self._parse_user(r) for r in records]

    def search_users(self, search_term: str,
                     fields: Optional[List[str]] = None) -> List[SalesforceUser]:
        """Search users by name or email."""
        qfilter = (f"Name LIKE '%{search_term}%' "
                   f"OR Username LIKE '%{search_term}%' "
                   f"OR Email LIKE '%{search_term}%'")
        return self.query_users(qfilter, fields)

    # ------------------------------------------------------------------
    # Metadata interface
    # ------------------------------------------------------------------

    def describe_global(self) -> List[Dict[str, Any]]:
        """List all available sObjects."""
        data = self._api_call("GET", "sobjects")
        return data.get("sobjects", [])

    def describe_object(self, object_type: str) -> Dict[str, Any]:
        """
        Get metadata for an sObject (fields, relationships, etc.).

        Args:
            object_type: Object type name.

        Returns:
            Full describe result with fields and metadata.
        """
        return self._api_call("GET", f"sobjects/{object_type}/describe")

    def get_object_fields(self, object_type: str) -> List[Dict[str, Any]]:
        """Get list of fields for an sObject."""
        data = self.describe_object(object_type)
        return data.get("fields", [])

    def get_picklist_values(self, object_type: str,
                            field_name: str) -> List[Dict[str, Any]]:
        """Get picklist options for a field."""
        fields = self.get_object_fields(object_type)
        for f in fields:
            if f.get("name") == field_name:
                return f.get("picklistValues", [])
        return []

    # ------------------------------------------------------------------
    # Parsing helpers
    # ------------------------------------------------------------------

    def _parse_sobject(self, data: Dict[str, Any],
                       object_type: str) -> SObjectRecord:
        fields = {k: v for k, v in data.items()
                  if k not in ("attributes", "Id")}
        return SObjectRecord(
            id=data.get("Id", data.get("id", "")),
            object_type=object_type,
            fields=fields,
            attributes=data.get("attributes", {}),
        )

    def _parse_query_result(self, data: Dict[str, Any]) -> SOQLQueryResult:
        records = []
        for rec in data.get("records", []):
            obj_type = rec.get("attributes", {}).get("type", "")
            records.append(self._parse_sobject(rec, obj_type))

        next_url = data.get("nextRecordsUrl")
        return SOQLQueryResult(
            records=records,
            total_size=data.get("totalSize", 0),
            done=data.get("done", True),
            next_records_url=next_url,
            query_locator=data.get("queryLocator"),
        )

    def _parse_bulk_job(self, data: Dict[str, Any]) -> BulkJob:
        return BulkJob(
            id=data.get("id", ""),
            object_type=data.get("object", ""),
            operation=data.get("operation", ""),
            state=data.get("state", ""),
            created_date=data.get("createdDate", ""),
            system_modstamp=data.get("systemModstamp", ""),
            number_records_processed=data.get("numberRecordsProcessed", 0),
            number_records_failed=data.get("numberRecordsFailed", 0),
            number_records_successful=data.get("numberRecordsSuccessful", 0),
            extra=data,
        )

    def _parse_feed_item(self, data: Dict[str, Any]) -> ChatterFeedItem:
        body_segments = data.get("body", {}).get("messageSegments", [])
        text = "".join(
            s.get("text", "") for s in body_segments
            if s.get("type") == "Text"
        )
        parent = data.get("parent", {})
        created_by = data.get("createdBy", {})
        return ChatterFeedItem(
            id=data.get("id", ""),
            feed_item_type=data.get("type", ""),
            body=text,
            parent_id=parent.get("id", ""),
            parent_type=parent.get("type", ""),
            created_by_id=created_by.get("id", ""),
            created_by_name=created_by.get("name", ""),
            created_date=data.get("createdDate", ""),
            url=data.get("url", ""),
            extra=data,
        )

    def _parse_user(self, record: SObjectRecord) -> SalesforceUser:
        return SalesforceUser(
            id=record.id,
            username=record.get("Username", ""),
            name=record.get("Name", ""),
            email=record.get("Email", ""),
            profile_id=record.get("ProfileId", ""),
            user_type=record.get("UserType", ""),
            is_active=record.get("IsActive", True),
            last_login_date=record.get("LastLoginDate", "") or "",
            extra=record.fields,
        )

    # ------------------------------------------------------------------
    # Internal API helper
    # ------------------------------------------------------------------

    def _api_call(self, method: str, path: str,
                  params: Optional[Dict[str, Any]] = None,
                  json_body: Optional[Dict[str, Any]] = None,
                  raw_body: Optional[str] = None,
                  content_type: str = "application/json",
                  full_path: Optional[str] = None,
                  _retry_count: int = 0) -> Any:
        """Make a Salesforce API call with retry."""
        try:
            return self._make_request(
                method, path, params, json_body, raw_body,
                content_type, full_path,
            )
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
                    method, path, params, json_body, raw_body,
                    content_type, full_path, _retry_count + 1,
                )
            raise

    def _make_request(self, method: str, path: str,
                      params: Optional[Dict[str, Any]],
                      json_body: Optional[Dict[str, Any]],
                      raw_body: Optional[str],
                      content_type: str,
                      full_path: Optional[str]) -> Any:
        """Execute a single Salesforce API request."""
        self._check_rate_limit()
        self._mark_request()

        if full_path:
            # Use the full path directly (for query_more etc.)
            if full_path.startswith("/services/data/"):
                base = self._instance_url or self.config.base_url
                url = f"{base.rstrip('/')}{full_path}"
            else:
                url = full_path
        else:
            url = f"{self._api_base}{path}"

        token = self.config.access_token or self.config.api_key or ""
        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": content_type,
            "Accept": "application/json",
            "User-Agent": "LiuHao-AI-OS",
        }

        if raw_body:
            body = raw_body
        elif json_body:
            body = json.dumps(json_body)
        else:
            body = ""

        self._log_request(method, url, headers, body or params)

        try:
            import urllib.request
            from urllib.parse import urlencode

            full_url = url
            if params and method == "GET":
                full_url = f"{url}?{urlencode(params)}"

            data = body.encode("utf-8") if body and method != "GET" else None
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
                    f"Salesforce rate limited, retry after {retry_after}s",
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

            # Parse Salesforce error response
            error_msg = f"Salesforce API error {e.code}"
            try:
                error_data = json.loads(resp_body)
                if isinstance(error_data, list) and error_data:
                    error_msg = error_data[0].get("message", error_msg)
                elif isinstance(error_data, dict):
                    error_msg = error_data.get("message", error_msg)
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
