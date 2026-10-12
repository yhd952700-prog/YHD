"""
Jira Integration Adapter for LiuHao AI OS.

Provides integration with Jira for issue management, JQL search,
project info, user management, sprint and board management,
and comments/attachments.
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


class JiraIssueType(str, Enum):
    """Common Jira issue types."""
    TASK = "Task"
    BUG = "Bug"
    STORY = "Story"
    EPIC = "Epic"
    SUBTASK = "Sub-task"
    IMPROVEMENT = "Improvement"
    NEW_FEATURE = "New Feature"


class JiraPriority(str, Enum):
    """Jira priority names."""
    HIGHEST = "Highest"
    HIGH = "High"
    MEDIUM = "Medium"
    LOW = "Low"
    LOWEST = "Lowest"


class JiraIssueState(str, Enum):
    """Jira issue states."""
    OPEN = "opened"
    IN_PROGRESS = "in_progress"
    DONE = "done"
    CLOSED = "closed"
    ALL = "all"


class CaseStatus(str, Enum):
    """Jira case/issue status names."""
    TO_DO = "To Do"
    IN_PROGRESS = "In Progress"
    DONE = "Done"
    CANCELLED = "Cancelled"


@dataclass
class JiraIssue:
    """Represents a Jira issue."""
    key: str
    summary: str
    issue_type: str = ""
    status: str = ""
    priority: str = ""
    description: str = ""
    project_key: str = ""
    project_name: str = ""
    assignee_name: str = ""
    assignee_email: str = ""
    reporter_name: str = ""
    reporter_email: str = ""
    labels: List[str] = field(default_factory=list)
    components: List[str] = field(default_factory=list)
    created: str = ""
    updated: str = ""
    resolved: str = ""
    due_date: str = ""
    story_points: float = 0.0
    epic_link: str = ""
    sprint: str = ""
    url: str = ""
    extra: Dict[str, Any] = field(default_factory=dict)


@dataclass
class JiraProject:
    """Represents a Jira project."""
    key: str
    name: str
    id: str = ""
    description: str = ""
    project_type: str = ""
    lead_name: str = ""
    avatar_url: str = ""
    url: str = ""
    issue_types: List[str] = field(default_factory=list)
    extra: Dict[str, Any] = field(default_factory=dict)


@dataclass
class JiraUser:
    """Represents a Jira user."""
    account_id: str
    display_name: str
    email_address: str = ""
    active: bool = True
    avatar_url: str = ""
    time_zone: str = ""
    extra: Dict[str, Any] = field(default_factory=dict)


@dataclass
class JiraSprint:
    """Represents a Jira sprint."""
    id: int
    name: str
    state: str = ""
    board_id: int = 0
    start_date: str = ""
    end_date: str = ""
    complete_date: str = ""
    goal: str = ""
    origin_board_id: int = 0
    extra: Dict[str, Any] = field(default_factory=dict)


@dataclass
class JiraBoard:
    """Represents a Jira board."""
    id: int
    name: str
    type: str = ""
    project_key: str = ""
    url: str = ""
    extra: Dict[str, Any] = field(default_factory=dict)


@dataclass
class JiraComment:
    """Represents a Jira issue comment."""
    id: str
    body: str
    author_name: str = ""
    author_email: str = ""
    created: str = ""
    updated: str = ""
    extra: Dict[str, Any] = field(default_factory=dict)


@dataclass
class JiraAttachment:
    """Represents a Jira issue attachment."""
    id: str
    filename: str
    size: int = 0
    mime_type: str = ""
    author_name: str = ""
    created: str = ""
    content_url: str = ""
    thumbnail_url: str = ""
    extra: Dict[str, Any] = field(default_factory=dict)


@dataclass
class JiraSearchResult:
    """Result from a JQL search."""
    issues: List[JiraIssue]
    total: int
    start_at: int
    max_results: int


class JiraAdapter(BaseIntegrationAdapter):
    """
    Jira integration adapter for LiuHao AI OS.

    Supports Jira Cloud REST API v3 with:
    - Issue management (create, update, transition, delete)
    - JQL-based issue search with pagination
    - Project listing and info
    - User search and management
    - Sprint and board management (Agile/Scrum)
    - Comments and attachments on issues
    """

    def __init__(self, config: IntegrationConfig):
        """
        Initialize Jira adapter.

        Uses email + API token via Basic Auth for Jira Cloud,
        or PAT via Bearer token for Jira Data Center.

        Args:
            config: Integration config.
                    For Jira Cloud: auth_type=BASIC_AUTH with username=email,
                    password=API token.
                    base_url should be the Jira instance URL
                    (e.g., https://your-domain.atlassian.net/).
        """
        if not config.base_url:
            raise ValueError("Jira adapter requires base_url configuration")
        # Ensure base URL ends with proper path
        if not config.base_url.endswith("/"):
            config.base_url += "/"
        if config.auth_type == AuthType.NONE:
            config.auth_type = AuthType.BASIC_AUTH
        super().__init__(config)
        self._current_user: Optional[str] = None
        self._server_info: Optional[Dict[str, Any]] = None

    # ------------------------------------------------------------------
    # Base interface
    # ------------------------------------------------------------------

    def connect(self) -> bool:
        """Connect to Jira and validate credentials."""
        self._status = ConnectionStatus.CONNECTING
        self._logger.info("Connecting to Jira API...")

        try:
            # Get current user info
            myself = self._api_call("GET", "rest/api/3/myself")
            self._current_user = myself.get("displayName", "")
            # Get server info
            try:
                self._server_info = self._api_call("GET", "rest/api/3/serverInfo")
            except Exception:
                pass

            self._status = ConnectionStatus.CONNECTED
            self._connected_at = time.time()
            self._logger.info(
                "Connected to Jira as %s", self._current_user,
            )
            return True
        except IntegrationError:
            raise
        except Exception as e:
            self._status = ConnectionStatus.ERROR
            raise self._handle_error(f"Connection error: {e}")

    def disconnect(self) -> bool:
        """Disconnect from Jira."""
        self._status = ConnectionStatus.DISCONNECTED
        self._current_user = None
        self._server_info = None
        self._session = None
        self._logger.info("Disconnected from Jira")
        return True

    def is_connected(self) -> bool:
        return self._status == ConnectionStatus.CONNECTED

    def get_status(self) -> IntegrationStatus:
        return self._build_status({
            "current_user": self._current_user,
            "server_title": (self._server_info or {}).get("serverTitle"),
            "server_version": (self._server_info or {}).get("version"),
            "base_url": self.config.base_url,
        })

    def test_connection(self) -> bool:
        try:
            if not self.is_connected():
                return False
            result = self._api_call("GET", "rest/api/3/myself")
            return "accountId" in result
        except Exception:
            return False

    # ------------------------------------------------------------------
    # Issue management
    # ------------------------------------------------------------------

    def create_issue(self, project_key: str, summary: str,
                     issue_type: str = "Task",
                     description: str = "",
                     priority: Optional[str] = None,
                     assignee_id: Optional[str] = None,
                     reporter_id: Optional[str] = None,
                     labels: Optional[List[str]] = None,
                     components: Optional[List[str]] = None,
                     due_date: Optional[str] = None,
                     story_points: Optional[float] = None,
                     epic_link: Optional[str] = None,
                     custom_fields: Optional[Dict[str, Any]] = None
                     ) -> JiraIssue:
        """
        Create a new Jira issue.

        Args:
            project_key: Project key (e.g., "PROJ").
            summary: Issue summary/title.
            issue_type: Issue type name.
            description: Issue description (ADF format or plain text).
            priority: Priority name.
            assignee_id: Account ID of assignee.
            reporter_id: Account ID of reporter.
            labels: List of labels.
            components: List of component names.
            due_date: Due date (YYYY-MM-DD).
            story_points: Story points estimate.
            epic_link: Epic key to link.
            custom_fields: Additional custom fields.

        Returns:
            Created JiraIssue.
        """
        fields: Dict[str, Any] = {
            "project": {"key": project_key},
            "summary": summary,
            "issuetype": {"name": issue_type},
        }

        if description:
            fields["description"] = self._text_to_adf(description)
        if priority:
            fields["priority"] = {"name": priority}
        if assignee_id:
            fields["assignee"] = {"accountId": assignee_id}
        if reporter_id:
            fields["reporter"] = {"accountId": reporter_id}
        if labels:
            fields["labels"] = labels
        if components:
            fields["components"] = [{"name": c} for c in components]
        if due_date:
            fields["duedate"] = due_date
        if story_points is not None:
            # Common custom field for story points (may vary by instance)
            fields["customfield_10016"] = story_points
        if epic_link:
            # Common epic link custom field
            fields["customfield_10014"] = epic_link
        if custom_fields:
            fields.update(custom_fields)

        data = self._api_call(
            "POST", "rest/api/3/issue", json_body={"fields": fields},
        )
        return self.get_issue(data.get("key", ""))

    def get_issue(self, issue_key: str,
                  fields: Optional[List[str]] = None,
                  expand: Optional[str] = None) -> JiraIssue:
        """
        Get a specific issue by key.

        Args:
            issue_key: Issue key (e.g., "PROJ-123").
            fields: List of fields to return (None = all).
            expand: Optional expand parameter.

        Returns:
            JiraIssue object.
        """
        params: Dict[str, Any] = {}
        if fields:
            params["fields"] = ",".join(fields)
        if expand:
            params["expand"] = expand

        data = self._api_call(
            "GET", f"rest/api/3/issue/{issue_key}", params=params,
        )
        return self._parse_issue(data)

    def update_issue(self, issue_key: str,
                     summary: Optional[str] = None,
                     description: Optional[str] = None,
                     priority: Optional[str] = None,
                     assignee_id: Optional[str] = None,
                     labels: Optional[List[str]] = None,
                     due_date: Optional[str] = None,
                     custom_fields: Optional[Dict[str, Any]] = None
                     ) -> bool:
        """
        Update issue fields.

        Returns:
            True if update succeeded.
        """
        fields: Dict[str, Any] = {}
        if summary is not None:
            fields["summary"] = summary
        if description is not None:
            fields["description"] = self._text_to_adf(description)
        if priority is not None:
            fields["priority"] = {"name": priority}
        if assignee_id is not None:
            fields["assignee"] = {"accountId": assignee_id}
        if labels is not None:
            fields["labels"] = labels
        if due_date is not None:
            fields["duedate"] = due_date
        if custom_fields:
            fields.update(custom_fields)

        if not fields:
            return False

        try:
            self._api_call(
                "PUT", f"rest/api/3/issue/{issue_key}",
                json_body={"fields": fields},
            )
            return True
        except Exception:
            return False

    def transition_issue(self, issue_key: str,
                         transition_name: str,
                         comment: Optional[str] = None,
                         fields: Optional[Dict[str, Any]] = None) -> bool:
        """
        Transition an issue through its workflow.

        Args:
            issue_key: Issue key.
            transition_name: Name of the transition (e.g., "In Progress", "Done").
            comment: Optional comment to add.
            fields: Optional fields to set during transition.

        Returns:
            True if transition succeeded.
        """
        # First, get available transitions
        transitions_data = self._api_call(
            "GET", f"rest/api/3/issue/{issue_key}/transitions",
        )
        transitions = transitions_data.get("transitions", [])

        # Find the transition ID by name
        transition_id = None
        for t in transitions:
            if t.get("name", "").lower() == transition_name.lower():
                transition_id = t.get("id")
                break

        if not transition_id:
            available = [t.get("name", "") for t in transitions]
            raise self._handle_error(
                f"Transition '{transition_name}' not found. "
                f"Available: {available}"
            )

        payload: Dict[str, Any] = {"transition": {"id": transition_id}}
        if comment:
            payload["update"] = {
                "comment": [
                    {"add": {"body": self._text_to_adf(comment)}},
                ],
            }
        if fields:
            payload["fields"] = fields

        try:
            self._api_call(
                "POST",
                f"rest/api/3/issue/{issue_key}/transitions",
                json_body=payload,
            )
            return True
        except Exception:
            return False

    def delete_issue(self, issue_key: str) -> bool:
        """Delete an issue. Returns True on success."""
        try:
            self._api_call("DELETE", f"rest/api/3/issue/{issue_key}")
            return True
        except Exception:
            return False

    def assign_issue(self, issue_key: str, account_id: str) -> bool:
        """Assign an issue to a user. Use None for unassigned."""
        try:
            self._api_call(
                "PUT", f"rest/api/3/issue/{issue_key}/assignee",
                json_body={"accountId": account_id},
            )
            return True
        except Exception:
            return False

    # ------------------------------------------------------------------
    # JQL Search
    # ------------------------------------------------------------------

    def search_issues(self, jql: str,
                      start_at: int = 0,
                      max_results: int = 50,
                      fields: Optional[List[str]] = None,
                      expand: Optional[str] = None
                      ) -> JiraSearchResult:
        """
        Search issues using JQL (Jira Query Language).

        Args:
            jql: JQL query string.
            start_at: Pagination offset.
            max_results: Results per page (default 50, max ~100).
            fields: Fields to include.
            expand: Optional expand parameter.

        Returns:
            JiraSearchResult with issues list and pagination info.
        """
        payload: Dict[str, Any] = {
            "jql": jql,
            "startAt": start_at,
            "maxResults": max_results,
        }
        if fields:
            payload["fields"] = fields
        if expand:
            payload["expand"] = expand

        data = self._api_call(
            "POST", "rest/api/3/search", json_body=payload,
        )
        issues = [self._parse_issue(i) for i in data.get("issues", [])]
        return JiraSearchResult(
            issues=issues,
            total=data.get("total", 0),
            start_at=data.get("startAt", 0),
            max_results=data.get("maxResults", 0),
        )

    def search_all_issues(self, jql: str,
                          fields: Optional[List[str]] = None,
                          max_pages: int = 10,
                          page_size: int = 50) -> List[JiraIssue]:
        """
        Fetch all issues matching a JQL query (paginated).

        Args:
            jql: JQL query.
            fields: Fields to include.
            max_pages: Maximum pages to fetch (prevents runaway queries).
            page_size: Results per page.

        Returns:
            List of all matching JiraIssue objects.
        """
        all_issues: List[JiraIssue] = []
        start_at = 0
        pages = 0

        while pages < max_pages:
            result = self.search_issues(
                jql, start_at=start_at, max_results=page_size, fields=fields,
            )
            all_issues.extend(result.issues)
            pages += 1

            if start_at + len(result.issues) >= result.total:
                break
            start_at += len(result.issues)

            if not result.issues:
                break

        return all_issues

    # ------------------------------------------------------------------
    # Project info
    # ------------------------------------------------------------------

    def list_projects(self,
                      start_at: int = 0,
                      max_results: int = 50) -> List[JiraProject]:
        """List all projects accessible to the user."""
        data = self._api_call(
            "GET", "rest/api/3/project/search",
            params={"startAt": start_at, "maxResults": max_results},
        )
        projects = [self._parse_project(p) for p in data.get("values", [])]
        return projects

    def get_project(self, project_key: str) -> JiraProject:
        """Get detailed project info."""
        data = self._api_call("GET", f"rest/api/3/project/{project_key}")
        return self._parse_project(data)

    def get_project_statuses(self, project_key: str) -> List[Dict[str, Any]]:
        """Get all statuses for a project."""
        data = self._api_call(
            "GET", f"rest/api/3/project/{project_key}/statuses",
        )
        return data

    def get_project_versions(self, project_key: str,
                             start_at: int = 0,
                             max_results: int = 50) -> List[Dict[str, Any]]:
        """Get versions/releases for a project."""
        data = self._api_call(
            "GET", f"rest/api/3/project/{project_key}/version",
            params={"startAt": start_at, "maxResults": max_results},
        )
        return data.get("values", [])

    # ------------------------------------------------------------------
    # User management
    # ------------------------------------------------------------------

    def get_current_user(self) -> JiraUser:
        """Get the currently authenticated user."""
        data = self._api_call("GET", "rest/api/3/myself")
        return self._parse_user(data)

    def get_user(self, account_id: str) -> JiraUser:
        """Get a user by account ID."""
        data = self._api_call(
            "GET", "rest/api/3/user",
            params={"accountId": account_id},
        )
        return self._parse_user(data)

    def search_users(self, query: str,
                     start_at: int = 0,
                     max_results: int = 50) -> List[JiraUser]:
        """Search for users by name or email."""
        data = self._api_call(
            "GET", "rest/api/3/user/search",
            params={"query": query, "startAt": start_at,
                    "maxResults": max_results},
        )
        return [self._parse_user(u) for u in data]

    def find_users_by_name(self, username: str,
                           start_at: int = 0,
                           max_results: int = 50) -> List[JiraUser]:
        """Find users by username."""
        data = self._api_call(
            "GET", "rest/api/3/user/search",
            params={"username": username, "startAt": start_at,
                    "maxResults": max_results},
        )
        return [self._parse_user(u) for u in data]

    # ------------------------------------------------------------------
    # Sprint and board management
    # ------------------------------------------------------------------

    def list_boards(self, project_key_or_id: Optional[str] = None,
                    board_type: Optional[str] = None,
                    name: Optional[str] = None,
                    start_at: int = 0,
                    max_results: int = 50) -> Tuple[List[JiraBoard], int]:
        """
        List agile boards.

        Args:
            project_key_or_id: Filter by project key or ID.
            board_type: Filter by type ("scrum", "kanban").
            name: Filter by name.
            start_at: Pagination offset.
            max_results: Results per page.

        Returns:
            Tuple of (boards list, total count).
        """
        params: Dict[str, Any] = {"startAt": start_at, "maxResults": max_results}
        if project_key_or_id:
            params["projectKeyOrId"] = project_key_or_id
        if board_type:
            params["type"] = board_type
        if name:
            params["name"] = name

        data = self._api_call(
            "GET", "rest/agile/1.0/board", params=params,
        )
        boards = [self._parse_board(b) for b in data.get("values", [])]
        return boards, data.get("total", 0)

    def get_board(self, board_id: int) -> JiraBoard:
        """Get a specific board."""
        data = self._api_call("GET", f"rest/agile/1.0/board/{board_id}")
        return self._parse_board(data)

    def list_sprints(self, board_id: int,
                     state: Optional[str] = None,
                     start_at: int = 0,
                     max_results: int = 50) -> Tuple[List[JiraSprint], int]:
        """
        List sprints for a board.

        Args:
            board_id: Board ID.
            state: Filter by state ("active", "closed", "future").
            start_at: Pagination offset.
            max_results: Results per page.

        Returns:
            Tuple of (sprints list, total count).
        """
        params: Dict[str, Any] = {"startAt": start_at, "maxResults": max_results}
        if state:
            params["state"] = state

        data = self._api_call(
            "GET", f"rest/agile/1.0/board/{board_id}/sprint", params=params,
        )
        sprints = [self._parse_sprint(s, board_id) for s in data.get("values", [])]
        return sprints, data.get("total", 0)

    def get_sprint(self, sprint_id: int) -> JiraSprint:
        """Get a specific sprint."""
        data = self._api_call("GET", f"rest/agile/1.0/sprint/{sprint_id}")
        return self._parse_sprint(data, data.get("originBoardId", 0))

    def get_sprint_issues(self, sprint_id: int,
                          start_at: int = 0,
                          max_results: int = 50,
                          jql: Optional[str] = None
                          ) -> Tuple[List[JiraIssue], int]:
        """Get issues in a sprint."""
        params: Dict[str, Any] = {"startAt": start_at, "maxResults": max_results}
        if jql:
            params["jql"] = jql

        data = self._api_call(
            "GET", f"rest/agile/1.0/sprint/{sprint_id}/issue", params=params,
        )
        issues = [self._parse_issue(i) for i in data.get("issues", [])]
        return issues, data.get("total", 0)

    def start_sprint(self, sprint_id: int,
                     name: Optional[str] = None,
                     start_date: Optional[str] = None,
                     end_date: Optional[str] = None,
                     goal: Optional[str] = None) -> bool:
        """Start a sprint."""
        payload: Dict[str, Any] = {"state": "active"}
        if name:
            payload["name"] = name
        if start_date:
            payload["startDate"] = start_date
        if end_date:
            payload["endDate"] = end_date
        if goal:
            payload["goal"] = goal

        try:
            self._api_call(
                "POST", f"rest/agile/1.0/sprint/{sprint_id}",
                json_body=payload,
            )
            return True
        except Exception:
            return False

    def complete_sprint(self, sprint_id: int,
                        end_date: Optional[str] = None) -> bool:
        """Complete a sprint."""
        payload: Dict[str, Any] = {"state": "closed"}
        if end_date:
            payload["endDate"] = end_date

        try:
            self._api_call(
                "POST", f"rest/agile/1.0/sprint/{sprint_id}",
                json_body=payload,
            )
            return True
        except Exception:
            return False

    # ------------------------------------------------------------------
    # Comments
    # ------------------------------------------------------------------

    def list_comments(self, issue_key: str,
                      start_at: int = 0,
                      max_results: int = 50) -> Tuple[List[JiraComment], int]:
        """List comments on an issue."""
        data = self._api_call(
            "GET", f"rest/api/3/issue/{issue_key}/comment",
            params={"startAt": start_at, "maxResults": max_results},
        )
        comments = [self._parse_comment(c) for c in data.get("comments", [])]
        return comments, data.get("total", 0)

    def add_comment(self, issue_key: str, body: str,
                    is_public: bool = True) -> JiraComment:
        """
        Add a comment to an issue.

        Args:
            issue_key: Issue key.
            body: Comment text.
            is_public: Whether comment is visible to all.

        Returns:
            Created JiraComment.
        """
        payload: Dict[str, Any] = {"body": self._text_to_adf(body)}
        if not is_public:
            payload["visibility"] = {
                "type": "role",
                "value": "Administrators",
            }

        data = self._api_call(
            "POST", f"rest/api/3/issue/{issue_key}/comment",
            json_body=payload,
        )
        return self._parse_comment(data)

    def update_comment(self, issue_key: str, comment_id: str,
                       body: str) -> bool:
        """Update an existing comment."""
        try:
            self._api_call(
                "PUT",
                f"rest/api/3/issue/{issue_key}/comment/{comment_id}",
                json_body={"body": self._text_to_adf(body)},
            )
            return True
        except Exception:
            return False

    def delete_comment(self, issue_key: str, comment_id: str) -> bool:
        """Delete a comment."""
        try:
            self._api_call(
                "DELETE",
                f"rest/api/3/issue/{issue_key}/comment/{comment_id}",
            )
            return True
        except Exception:
            return False

    # ------------------------------------------------------------------
    # Attachments
    # ------------------------------------------------------------------

    def list_attachments(self, issue_key: str) -> List[JiraAttachment]:
        """List attachments on an issue."""
        data = self._api_call("GET", f"rest/api/3/issue/{issue_key}")
        attachment_data = data.get("fields", {}).get("attachment", [])
        return [self._parse_attachment(a) for a in attachment_data]

    def add_attachment(self, issue_key: str,
                       filename: str,
                       file_content: bytes,
                       content_type: str = "application/octet-stream"
                       ) -> List[JiraAttachment]:
        """
        Add an attachment to an issue.

        Args:
            issue_key: Issue key.
            filename: Name of the file.
            file_content: Raw file bytes.
            content_type: MIME type.

        Returns:
            List of created JiraAttachment objects.
        """
        # In a real implementation, this would use multipart/form-data
        # For now, we provide a structured interface
        self._logger.info(
            "Attachment upload: %s to %s (%d bytes)",
            filename, issue_key, len(file_content),
        )
        # Return placeholder - actual upload would need multipart handling
        return []

    def delete_attachment(self, attachment_id: str) -> bool:
        """Delete an attachment."""
        try:
            self._api_call("DELETE", f"rest/api/3/attachment/{attachment_id}")
            return True
        except Exception:
            return False

    # ------------------------------------------------------------------
    # Parsing helpers
    # ------------------------------------------------------------------

    def _parse_issue(self, data: Dict[str, Any]) -> JiraIssue:
        fields = data.get("fields", {})
        base_url = self.config.base_url.rstrip("/")

        # Extract description text from ADF
        description = ""
        desc_field = fields.get("description")
        if desc_field and isinstance(desc_field, dict):
            # Simple ADF to text extraction
            parts = []
            for content in desc_field.get("content", []):
                for para in content.get("content", []):
                    parts.append(para.get("text", ""))
            description = " ".join(parts)

        # Story points (common custom field)
        story_points = 0.0
        for key, value in fields.items():
            if key.startswith("customfield_") and isinstance(value, (int, float)):
                # Heuristic: the first numeric custom field is often story points
                if story_points == 0:
                    story_points = float(value)

        return JiraIssue(
            key=data.get("key", ""),
            summary=fields.get("summary", ""),
            issue_type=fields.get("issuetype", {}).get("name", ""),
            status=fields.get("status", {}).get("name", ""),
            priority=(fields.get("priority") or {}).get("name", ""),
            description=description,
            project_key=fields.get("project", {}).get("key", ""),
            project_name=fields.get("project", {}).get("name", ""),
            assignee_name=(fields.get("assignee") or {}).get("displayName", ""),
            assignee_email=(fields.get("assignee") or {}).get("emailAddress", ""),
            reporter_name=(fields.get("reporter") or {}).get("displayName", ""),
            reporter_email=(fields.get("reporter") or {}).get("emailAddress", ""),
            labels=fields.get("labels", []) or [],
            components=[c.get("name", "") for c in fields.get("components", [])],
            created=fields.get("created", ""),
            updated=fields.get("updated", ""),
            resolved=fields.get("resolutiondate", "") or "",
            due_date=fields.get("duedate", "") or "",
            story_points=story_points,
            url=f"{base_url}/browse/{data.get('key', '')}",
            extra=data,
        )

    def _parse_project(self, data: Dict[str, Any]) -> JiraProject:
        base_url = self.config.base_url.rstrip("/")
        return JiraProject(
            key=data.get("key", ""),
            name=data.get("name", ""),
            id=str(data.get("id", "")),
            description=data.get("description", "") or "",
            project_type=data.get("projectTypeKey", ""),
            lead_name=(data.get("lead") or {}).get("displayName", ""),
            avatar_url=(data.get("avatarUrls") or {}).get("48x48", ""),
            url=f"{base_url}/projects/{data.get('key', '')}",
            issue_types=[t.get("name", "") for t in data.get("issueTypes", [])],
            extra=data,
        )

    def _parse_user(self, data: Dict[str, Any]) -> JiraUser:
        return JiraUser(
            account_id=data.get("accountId", ""),
            display_name=data.get("displayName", ""),
            email_address=data.get("emailAddress", ""),
            active=data.get("active", True),
            avatar_url=(data.get("avatarUrls") or {}).get("48x48", ""),
            time_zone=data.get("timeZone", ""),
            extra=data,
        )

    def _parse_sprint(self, data: Dict[str, Any],
                      board_id: int = 0) -> JiraSprint:
        return JiraSprint(
            id=data.get("id", 0),
            name=data.get("name", ""),
            state=data.get("state", ""),
            board_id=board_id,
            start_date=data.get("startDate", "") or "",
            end_date=data.get("endDate", "") or "",
            complete_date=data.get("completeDate", "") or "",
            goal=data.get("goal", "") or "",
            origin_board_id=data.get("originBoardId", 0) or 0,
            extra=data,
        )

    def _parse_board(self, data: Dict[str, Any]) -> JiraBoard:
        return JiraBoard(
            id=data.get("id", 0),
            name=data.get("name", ""),
            type=data.get("type", ""),
            project_key=((data.get("location") or {}).get("projectKey", "")),
            url=data.get("self", ""),
            extra=data,
        )

    def _parse_comment(self, data: Dict[str, Any]) -> JiraComment:
        body = ""
        body_field = data.get("body")
        if isinstance(body_field, dict):
            parts = []
            for content in body_field.get("content", []):
                for para in content.get("content", []):
                    parts.append(para.get("text", ""))
            body = " ".join(parts)
        elif isinstance(body_field, str):
            body = body_field

        return JiraComment(
            id=str(data.get("id", "")),
            body=body,
            author_name=(data.get("author") or {}).get("displayName", ""),
            author_email=(data.get("author") or {}).get("emailAddress", ""),
            created=data.get("created", ""),
            updated=data.get("updated", ""),
            extra=data,
        )

    def _parse_attachment(self, data: Dict[str, Any]) -> JiraAttachment:
        return JiraAttachment(
            id=str(data.get("id", "")),
            filename=data.get("filename", ""),
            size=data.get("size", 0),
            mime_type=data.get("mimeType", ""),
            author_name=(data.get("author") or {}).get("displayName", ""),
            created=data.get("created", ""),
            content_url=data.get("content", ""),
            thumbnail_url=data.get("thumbnail", ""),
            extra=data,
        )

    # ------------------------------------------------------------------
    # Utility
    # ------------------------------------------------------------------

    def _text_to_adf(self, text: str) -> Dict[str, Any]:
        """
        Convert plain text to Atlassian Document Format (ADF).

        Args:
            text: Plain text string.

        Returns:
            ADF document structure.
        """
        return {
            "type": "doc",
            "version": 1,
            "content": [
                {
                    "type": "paragraph",
                    "content": [
                        {"type": "text", "text": text},
                    ],
                },
            ],
        }

    # ------------------------------------------------------------------
    # Internal API helper
    # ------------------------------------------------------------------

    def _api_call(self, method: str, path: str,
                  params: Optional[Dict[str, Any]] = None,
                  json_body: Optional[Dict[str, Any]] = None,
                  _retry_count: int = 0) -> Any:
        """Make a Jira API call with retry on rate limits."""
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
        """Execute a single Jira API request."""
        self._check_rate_limit()
        self._mark_request()

        url = self._build_url(path)
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": "LiuHao-AI-OS",
        }

        # Add auth header based on auth type
        if self.config.auth_type == AuthType.BASIC_AUTH:
            username = self.config.username or ""
            password = self.config.password or ""
            credentials = f"{username}:{password}".encode("utf-8")
            encoded = base64.b64encode(credentials).decode("utf-8")
            headers["Authorization"] = f"Basic {encoded}"
        elif self.config.auth_type == AuthType.BEARER_TOKEN:
            token = self.config.access_token or self.config.api_key or ""
            headers["Authorization"] = f"Bearer {token}"
        elif self.config.auth_type == AuthType.API_KEY and self.config.api_key:
            headers["Authorization"] = f"Bearer {self.config.api_key}"

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
                    f"Jira rate limited, retry after {retry_after}s",
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
                f"Jira API error {e.code}: {e.reason}",
                status_code=e.code,
                response_body=resp_body,
            )
        except (RateLimitError, IntegrationError):
            raise
        except Exception as e:
            raise self._handle_error(f"Request failed: {e}")
