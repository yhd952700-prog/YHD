"""
Linear Integration Adapter for LiuHao AI OS.

Provides integration with Linear issue tracking for managing issues, projects,
teams, cycles, milestones, comments, attachments, workflow states, and search.
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


class LinearIssueState(str, Enum):
    """Issue state types."""
    BACKLOG = "backlog"
    STARTED = "started"
    UNSTARTED = "unstarted"
    COMPLETED = "completed"
    CANCELED = "canceled"


class LinearIssuePriority(int, Enum):
    """Issue priority levels."""
    URGENT = 1
    HIGH = 2
    NORMAL = 3
    LOW = 4


class LinearPaginationOrder(str, Enum):
    """Sort order for paginated queries."""
    ASCENDING = "ascending"
    DESCENDING = "descending"


@dataclass
class LinearIssue:
    """Represents a Linear issue."""
    id: str = ""
    identifier: str = ""
    title: str = ""
    description: str = ""
    state: str = ""
    state_type: LinearIssueState = LinearIssueState.BACKLOG
    priority: LinearIssuePriority = LinearIssuePriority.NORMAL
    project_id: str = ""
    project_name: str = ""
    team_id: str = ""
    team_key: str = ""
    assignee_id: str = ""
    assignee_name: str = ""
    creator_id: str = ""
    labels: List[str] = field(default_factory=list)
    estimate: int = 0
    due_date: str = ""
    created_at: str = ""
    updated_at: str = ""
    url: str = ""
    branch_name: str = ""
    cycle_id: str = ""
    extra: Dict[str, Any] = field(default_factory=dict)


@dataclass
class LinearProject:
    """Represents a Linear project."""
    id: str
    name: str = ""
    description: str = ""
    state: str = ""
    team_id: str = ""
    progress: float = 0.0
    start_date: str = ""
    target_date: str = ""
    lead_id: str = ""
    member_ids: List[str] = field(default_factory=list)
    issue_count: int = 0
    completed_issue_count: int = 0
    extra: Dict[str, Any] = field(default_factory=dict)


@dataclass
class LinearTeam:
    """Represents a Linear team."""
    id: str
    name: str = ""
    key: str = ""
    description: str = ""
    icon: str = ""
    color: str = ""
    private: bool = False
    issue_count: int = 0
    member_ids: List[str] = field(default_factory=list)
    extra: Dict[str, Any] = field(default_factory=dict)


@dataclass
class LinearCycle:
    """Represents a Linear cycle (sprint)."""
    id: str
    name: str = ""
    number: int = 0
    team_id: str = ""
    starts_at: str = ""
    ends_at: str = ""
    progress: float = 0.0
    issue_count: int = 0
    completed_issue_count: int = 0
    extra: Dict[str, Any] = field(default_factory=dict)


@dataclass
class LinearMilestone:
    """Represents a Linear milestone."""
    id: str
    name: str = ""
    description: str = ""
    target_date: str = ""
    project_id: str = ""
    state: str = ""
    extra: Dict[str, Any] = field(default_factory=dict)


@dataclass
class LinearComment:
    """Represents a Linear comment."""
    id: str
    body: str = ""
    issue_id: str = ""
    user_id: str = ""
    user_name: str = ""
    created_at: str = ""
    updated_at: str = ""
    reaction_data: List[Dict[str, Any]] = field(default_factory=list)
    extra: Dict[str, Any] = field(default_factory=dict)


@dataclass
class LinearAttachment:
    """Represents a Linear attachment."""
    id: str
    title: str = ""
    subtitle: str = ""
    url: str = ""
    issue_id: str = ""
    source_type: str = ""
    created_at: str = ""
    group_title: str = ""
    group_icon_url: str = ""
    extra: Dict[str, Any] = field(default_factory=dict)


@dataclass
class LinearWorkflowState:
    """Represents a Linear workflow state."""
    id: str
    name: str = ""
    type: str = ""
    color: str = ""
    position: int = 0
    team_id: str = ""
    extra: Dict[str, Any] = field(default_factory=dict)


class LinearAdapter(BaseIntegrationAdapter):
    """
    Linear integration adapter for LiuHao AI OS.

    Supports:
    - Issues (create, list, update, delete)
    - Projects and teams
    - Cycles and milestones
    - Comments and attachments
    - Workflow states
    - Search
    - GraphQL API access
    """

    def __init__(self, config: IntegrationConfig):
        """
        Initialize the Linear adapter.

        Args:
            config: Integration configuration. Must have api_key set
                    for the Linear personal API key.
        """
        if config.auth_type == AuthType.NONE:
            config.auth_type = AuthType.API_KEY
        if not config.base_url:
            config.base_url = "https://api.linear.app/graphql"
        super().__init__(config)
        self._user_id: Optional[str] = None
        self._user_name: Optional[str] = None
        self._org_id: Optional[str] = None
        self._org_name: Optional[str] = None
        self._cached_issues: Dict[str, LinearIssue] = {}
        self._cached_teams: Dict[str, LinearTeam] = {}

    # ------------------------------------------------------------------
    # Base interface implementation
    # ------------------------------------------------------------------

    def connect(self) -> bool:
        """
        Establish connection to Linear API and validate credentials.

        Returns:
            True if connection successful.

        Raises:
            AuthenticationError: If API key is invalid.
        """
        self._status = ConnectionStatus.CONNECTING
        self._logger.info("Connecting to Linear API...")

        try:
            query = """
            query {
              viewer {
                id
                name
                email
                organization {
                  id
                  name
                  urlKey
                }
              }
            }
            """
            result = self._graphql(query)
            viewer = result.get("data", {}).get("viewer", {})
            self._user_id = viewer.get("id", "")
            self._user_name = viewer.get("name", "")
            org = viewer.get("organization", {})
            self._org_id = org.get("id", "")
            self._org_name = org.get("name", "")
            self._status = ConnectionStatus.CONNECTED
            self._connected_at = time.time()
            self._logger.info(
                "Connected to Linear as %s (org: %s)",
                self._user_name, self._org_name,
            )
            return True
        except IntegrationError:
            raise
        except Exception as e:
            self._status = ConnectionStatus.ERROR
            raise self._handle_error(f"Connection error: {e}")

    def disconnect(self) -> bool:
        """
        Disconnect from Linear (clear session state).

        Returns:
            Always returns True.
        """
        self._status = ConnectionStatus.DISCONNECTED
        self._user_id = None
        self._user_name = None
        self._org_id = None
        self._org_name = None
        self._cached_issues = {}
        self._cached_teams = {}
        self._session = None
        self._logger.info("Disconnected from Linear")
        return True

    def is_connected(self) -> bool:
        """
        Check if connected to Linear API.

        Returns:
            True if status is CONNECTED.
        """
        return self._status == ConnectionStatus.CONNECTED

    def get_status(self) -> IntegrationStatus:
        """
        Get current adapter status.

        Returns:
            IntegrationStatus with Linear-specific extra info.
        """
        return self._build_status({
            "user_name": self._user_name,
            "user_id": self._user_id,
            "org_name": self._org_name,
            "org_id": self._org_id,
            "cached_issues": len(self._cached_issues),
            "cached_teams": len(self._cached_teams),
        })

    def test_connection(self) -> bool:
        """
        Test the Linear API connection.

        Returns:
            True if connection is working.
        """
        try:
            if not self.is_connected():
                return False
            query = "query { viewer { id } }"
            result = self._graphql(query)
            viewer = result.get("data", {}).get("viewer", {})
            return "id" in viewer
        except Exception:
            return False

    # ------------------------------------------------------------------
    # Issues
    # ------------------------------------------------------------------

    def create_issue(self, title: str,
                     team_id: str,
                     description: str = "",
                     state_id: Optional[str] = None,
                     priority: LinearIssuePriority = LinearIssuePriority.NORMAL,
                     project_id: str = "",
                     assignee_id: str = "",
                     labels: Optional[List[str]] = None,
                     estimate: int = 0,
                     due_date: str = "",
                     cycle_id: str = "",
                     **kwargs: Any) -> LinearIssue:
        """
        Create a new issue.

        Args:
            title: Issue title.
            team_id: Team ID.
            description: Issue description.
            state_id: Workflow state ID.
            priority: Issue priority.
            project_id: Project ID.
            assignee_id: Assignee user ID.
            labels: List of label IDs.
            estimate: Estimate value.
            due_date: Due date (ISO format).
            cycle_id: Cycle ID.
            **kwargs: Additional fields.

        Returns:
            Created LinearIssue.
        """
        input_data: Dict[str, Any] = {
            "title": title,
            "teamId": team_id,
            "priority": priority.value,
        }
        if description:
            input_data["description"] = description
        if state_id:
            input_data["stateId"] = state_id
        if project_id:
            input_data["projectId"] = project_id
        if assignee_id:
            input_data["assigneeId"] = assignee_id
        if labels:
            input_data["labelIds"] = labels
        if estimate > 0:
            input_data["estimate"] = estimate
        if due_date:
            input_data["dueDate"] = due_date
        if cycle_id:
            input_data["cycleId"] = cycle_id
        input_data.update(kwargs)

        mutation = """
        mutation IssueCreate($input: IssueCreateInput!) {
          issueCreate(input: $input) {
            success
            issue {
              id
              identifier
              title
              description
              state { id name type }
              priority
              team { id key name }
              project { id name }
              assignee { id name }
              url
              createdAt
              updatedAt
            }
          }
        }
        """
        result = self._graphql(mutation, {"input": input_data})
        issue_data = result.get("data", {}).get("issueCreate", {}).get("issue", {})
        issue = self._parse_issue(issue_data)
        if issue.id:
            self._cached_issues[issue.id] = issue
        return issue

    def list_issues(self, team_id: Optional[str] = None,
                    state: Optional[LinearIssueState] = None,
                    priority: Optional[LinearIssuePriority] = None,
                    project_id: str = "",
                    assignee_id: str = "",
                    first: int = 50,
                    after: str = "",
                    order_by: str = "createdAt",
                    order: LinearPaginationOrder = LinearPaginationOrder.DESCENDING,
                    **kwargs: Any) -> Dict[str, Any]:
        """
        List issues with filtering.

        Args:
            team_id: Filter by team.
            state: Filter by state type.
            priority: Filter by priority.
            project_id: Filter by project.
            assignee_id: Filter by assignee.
            first: Page size.
            after: Pagination cursor.
            order_by: Field to sort by.
            order: Sort order.
            **kwargs: Additional filter parameters.

        Returns:
            Dictionary with issues list and pagination info.
        """
        filters: Dict[str, Any] = {}
        if team_id:
            filters["team"] = {"id": {"eq": team_id}}
        if state:
            filters["state"] = {"type": {"eq": state.value}}
        if priority:
            filters["priority"] = {"eq": priority.value}
        if project_id:
            filters["project"] = {"id": {"eq": project_id}}
        if assignee_id:
            filters["assignee"] = {"id": {"eq": assignee_id}}
        filters.update(kwargs)

        query = """
        query Issues($filter: IssueFilter, $first: Int, $after: String,
                     $orderBy: IssueOrderBy, $order: PaginationOrder) {
          issues(filter: $filter, first: $first, after: $after,
                 orderBy: $orderBy, order: $order) {
            nodes {
              id
              identifier
              title
              description
              state { id name type }
              priority
              team { id key name }
              project { id name }
              assignee { id name }
              url
              createdAt
              updatedAt
            }
            pageInfo {
              endCursor
              hasNextPage
            }
            totalCount
          }
        }
        """
        variables: Dict[str, Any] = {
            "first": first,
            "orderBy": order_by,
            "order": order.value,
        }
        if filters:
            variables["filter"] = filters
        if after:
            variables["after"] = after

        result = self._graphql(query, variables)
        issues_result = result.get("data", {}).get("issues", {})
        nodes = issues_result.get("nodes", [])
        issues = [self._parse_issue(n) for n in nodes]
        for issue in issues:
            if issue.id:
                self._cached_issues[issue.id] = issue

        return {
            "issues": issues,
            "page_info": issues_result.get("pageInfo", {}),
            "total_count": issues_result.get("totalCount", 0),
        }

    def get_issue(self, issue_id: str) -> LinearIssue:
        """
        Get an issue by ID.

        Args:
            issue_id: Issue ID.

        Returns:
            LinearIssue object.
        """
        if issue_id in self._cached_issues:
            return self._cached_issues[issue_id]

        query = """
        query Issue($id: String!) {
          issue(id: $id) {
            id
            identifier
            title
            description
            state { id name type }
            priority
            team { id key name }
            project { id name }
            assignee { id name }
            labels { nodes { id name } }
            estimate
            dueDate
            url
            branchName
            cycle { id name }
            createdAt
            updatedAt
          }
        }
        """
        result = self._graphql(query, {"id": issue_id})
        issue_data = result.get("data", {}).get("issue", {})
        issue = self._parse_issue(issue_data)
        if issue.id:
            self._cached_issues[issue.id] = issue
        return issue

    def update_issue(self, issue_id: str,
                     title: Optional[str] = None,
                     description: Optional[str] = None,
                     state_id: Optional[str] = None,
                     priority: Optional[LinearIssuePriority] = None,
                     **kwargs: Any) -> LinearIssue:
        """
        Update an issue.

        Args:
            issue_id: Issue ID to update.
            title: New title.
            description: New description.
            state_id: New state ID.
            priority: New priority.
            **kwargs: Additional fields to update.

        Returns:
            Updated LinearIssue.
        """
        input_data: Dict[str, Any] = {}
        if title is not None:
            input_data["title"] = title
        if description is not None:
            input_data["description"] = description
        if state_id is not None:
            input_data["stateId"] = state_id
        if priority is not None:
            input_data["priority"] = priority.value
        input_data.update(kwargs)

        mutation = """
        mutation IssueUpdate($id: String!, $input: IssueUpdateInput!) {
          issueUpdate(id: $id, input: $input) {
            success
            issue {
              id
              identifier
              title
              description
              state { id name type }
              priority
              team { id key name }
              url
              updatedAt
            }
          }
        }
        """
        result = self._graphql(mutation, {"id": issue_id, "input": input_data})
        issue_data = result.get("data", {}).get("issueUpdate", {}).get("issue", {})
        issue = self._parse_issue(issue_data)
        if issue.id:
            self._cached_issues[issue.id] = issue
        return issue

    def delete_issue(self, issue_id: str) -> bool:
        """
        Delete an issue.

        Args:
            issue_id: Issue ID to delete.

        Returns:
            True if deletion was successful.
        """
        mutation = """
        mutation IssueDelete($id: String!) {
          issueDelete(id: $id) {
            success
          }
        }
        """
        result = self._graphql(mutation, {"id": issue_id})
        success = result.get("data", {}).get("issueDelete", {}).get("success", False)
        if success and issue_id in self._cached_issues:
            del self._cached_issues[issue_id]
        return success

    def _parse_issue(self, data: Dict[str, Any]) -> LinearIssue:
        """Parse issue data into LinearIssue object."""
        state_data = data.get("state", {})
        team_data = data.get("team", {})
        project_data = data.get("project", {})
        assignee_data = data.get("assignee", {})
        cycle_data = data.get("cycle", {})
        labels_data = data.get("labels", {}).get("nodes", [])

        return LinearIssue(
            id=data.get("id", ""),
            identifier=data.get("identifier", ""),
            title=data.get("title", ""),
            description=data.get("description", ""),
            state=state_data.get("name", ""),
            state_type=LinearIssueState(state_data.get("type", "backlog")),
            priority=LinearIssuePriority(data.get("priority", 3)),
            project_id=project_data.get("id", ""),
            project_name=project_data.get("name", ""),
            team_id=team_data.get("id", ""),
            team_key=team_data.get("key", ""),
            assignee_id=assignee_data.get("id", ""),
            assignee_name=assignee_data.get("name", ""),
            labels=[l.get("name", "") for l in labels_data],
            estimate=data.get("estimate", 0) or 0,
            due_date=data.get("dueDate", "") or "",
            created_at=data.get("createdAt", ""),
            updated_at=data.get("updatedAt", ""),
            url=data.get("url", ""),
            branch_name=data.get("branchName", "") or "",
            cycle_id=cycle_data.get("id", "") if cycle_data else "",
            extra=data,
        )

    # ------------------------------------------------------------------
    # Projects and teams
    # ------------------------------------------------------------------

    def list_teams(self) -> List[LinearTeam]:
        """
        List all teams in the organization.

        Returns:
            List of LinearTeam objects.
        """
        query = """
        query {
          teams {
            nodes {
              id
              name
              key
              description
              icon
              color
              private
            }
          }
        }
        """
        result = self._graphql(query)
        teams_data = result.get("data", {}).get("teams", {}).get("nodes", [])
        teams = [
            LinearTeam(
                id=t.get("id", ""),
                name=t.get("name", ""),
                key=t.get("key", ""),
                description=t.get("description", ""),
                icon=t.get("icon", ""),
                color=t.get("color", ""),
                private=t.get("private", False),
                extra=t,
            )
            for t in teams_data
        ]
        for team in teams:
            self._cached_teams[team.id] = team
        return teams

    def list_projects(self, team_id: Optional[str] = None,
                      first: int = 50) -> List[LinearProject]:
        """
        List projects.

        Args:
            team_id: Optional team ID filter.
            first: Maximum number of projects.

        Returns:
            List of LinearProject objects.
        """
        query = """
        query Projects($filter: ProjectFilter, $first: Int) {
          projects(filter: $filter, first: $first) {
            nodes {
              id
              name
              description
              state
              team { id name }
              progress
              startDate
              targetDate
              lead { id name }
              issueCount
            }
          }
        }
        """
        filters: Dict[str, Any] = {}
        if team_id:
            filters["team"] = {"id": {"eq": team_id}}

        variables: Dict[str, Any] = {"first": first}
        if filters:
            variables["filter"] = filters

        result = self._graphql(query, variables)
        projects_data = result.get("data", {}).get("projects", {}).get("nodes", [])
        return [
            LinearProject(
                id=p.get("id", ""),
                name=p.get("name", ""),
                description=p.get("description", ""),
                state=p.get("state", ""),
                team_id=p.get("team", {}).get("id", ""),
                progress=p.get("progress", 0.0),
                start_date=p.get("startDate", "") or "",
                target_date=p.get("targetDate", "") or "",
                lead_id=p.get("lead", {}).get("id", "") if p.get("lead") else "",
                issue_count=p.get("issueCount", 0),
                extra=p,
            )
            for p in projects_data
        ]

    # ------------------------------------------------------------------
    # Cycles and milestones
    # ------------------------------------------------------------------

    def list_cycles(self, team_id: str,
                    first: int = 50,
                    include_archived: bool = False
                    ) -> List[LinearCycle]:
        """
        List cycles for a team.

        Args:
            team_id: Team ID.
            first: Maximum number of cycles.
            include_archived: Include archived cycles.

        Returns:
            List of LinearCycle objects.
        """
        query = """
        query TeamCycles($teamId: String!, $first: Int) {
          team(id: $teamId) {
            cycles(first: $first) {
              nodes {
                id
                name
                number
                team { id name }
                startsAt
                endsAt
                progress
                issueCount
                completedIssueCount
              }
            }
          }
        }
        """
        result = self._graphql(query, {"teamId": team_id, "first": first})
        cycles_data = (
            result.get("data", {}).get("team", {}).get("cycles", {})
            .get("nodes", [])
        )
        return [
            LinearCycle(
                id=c.get("id", ""),
                name=c.get("name", ""),
                number=c.get("number", 0),
                team_id=team_id,
                starts_at=c.get("startsAt", ""),
                ends_at=c.get("endsAt", ""),
                progress=c.get("progress", 0.0),
                issue_count=c.get("issueCount", 0),
                completed_issue_count=c.get("completedIssueCount", 0),
                extra=c,
            )
            for c in cycles_data
        ]

    def list_milestones(self, project_id: str) -> List[LinearMilestone]:
        """
        List milestones for a project.

        Args:
            project_id: Project ID.

        Returns:
            List of LinearMilestone objects.
        """
        query = """
        query ProjectMilestones($projectId: String!) {
          project(id: $projectId) {
            milestones {
              nodes {
                id
                name
                description
                targetDate
                state
              }
            }
          }
        }
        """
        result = self._graphql(query, {"projectId": project_id})
        milestones_data = (
            result.get("data", {}).get("project", {}).get("milestones", {})
            .get("nodes", [])
        )
        return [
            LinearMilestone(
                id=m.get("id", ""),
                name=m.get("name", ""),
                description=m.get("description", ""),
                target_date=m.get("targetDate", "") or "",
                project_id=project_id,
                state=m.get("state", ""),
                extra=m,
            )
            for m in milestones_data
        ]

    # ------------------------------------------------------------------
    # Comments
    # ------------------------------------------------------------------

    def create_comment(self, issue_id: str, body: str) -> LinearComment:
        """
        Add a comment to an issue.

        Args:
            issue_id: Issue ID.
            body: Comment body.

        Returns:
            Created LinearComment.
        """
        mutation = """
        mutation CommentCreate($input: CommentCreateInput!) {
          commentCreate(input: $input) {
            success
            comment {
              id
              body
              issue { id identifier }
              user { id name }
              createdAt
              updatedAt
            }
          }
        }
        """
        result = self._graphql(
            mutation, {"input": {"issueId": issue_id, "body": body}}
        )
        comment_data = (
            result.get("data", {}).get("commentCreate", {}).get("comment", {})
        )
        return LinearComment(
            id=comment_data.get("id", ""),
            body=comment_data.get("body", ""),
            issue_id=comment_data.get("issue", {}).get("id", issue_id),
            user_id=comment_data.get("user", {}).get("id", ""),
            user_name=comment_data.get("user", {}).get("name", ""),
            created_at=comment_data.get("createdAt", ""),
            updated_at=comment_data.get("updatedAt", ""),
            extra=comment_data,
        )

    def list_comments(self, issue_id: str,
                      first: int = 50) -> List[LinearComment]:
        """
        List comments on an issue.

        Args:
            issue_id: Issue ID.
            first: Maximum number of comments.

        Returns:
            List of LinearComment objects.
        """
        query = """
        query IssueComments($issueId: String!, $first: Int) {
          issue(id: $issueId) {
            comments(first: $first) {
              nodes {
                id
                body
                user { id name }
                createdAt
                updatedAt
              }
            }
          }
        }
        """
        result = self._graphql(query, {"issueId": issue_id, "first": first})
        comments_data = (
            result.get("data", {}).get("issue", {}).get("comments", {})
            .get("nodes", [])
        )
        return [
            LinearComment(
                id=c.get("id", ""),
                body=c.get("body", ""),
                issue_id=issue_id,
                user_id=c.get("user", {}).get("id", ""),
                user_name=c.get("user", {}).get("name", ""),
                created_at=c.get("createdAt", ""),
                updated_at=c.get("updatedAt", ""),
                extra=c,
            )
            for c in comments_data
        ]

    # ------------------------------------------------------------------
    # Attachments
    # ------------------------------------------------------------------

    def create_attachment(self, issue_id: str,
                          title: str,
                          url: str,
                          subtitle: str = "",
                          **kwargs: Any) -> LinearAttachment:
        """
        Create an attachment on an issue.

        Args:
            issue_id: Issue ID.
            title: Attachment title.
            url: Attachment URL.
            subtitle: Attachment subtitle.
            **kwargs: Additional fields.

        Returns:
            Created LinearAttachment.
        """
        input_data: Dict[str, Any] = {
            "issueId": issue_id,
            "title": title,
            "url": url,
            "subtitle": subtitle,
        }
        input_data.update(kwargs)

        mutation = """
        mutation AttachmentCreate($input: AttachmentCreateInput!) {
          attachmentCreate(input: $input) {
            success
            attachment {
              id
              title
              subtitle
              url
              sourceType
              createdAt
            }
          }
        }
        """
        result = self._graphql(mutation, {"input": input_data})
        att_data = (
            result.get("data", {}).get("attachmentCreate", {})
            .get("attachment", {})
        )
        return LinearAttachment(
            id=att_data.get("id", ""),
            title=att_data.get("title", ""),
            subtitle=att_data.get("subtitle", ""),
            url=att_data.get("url", ""),
            issue_id=issue_id,
            source_type=att_data.get("sourceType", ""),
            created_at=att_data.get("createdAt", ""),
            extra=att_data,
        )

    # ------------------------------------------------------------------
    # Workflow states
    # ------------------------------------------------------------------

    def list_workflow_states(self, team_id: str) -> List[LinearWorkflowState]:
        """
        List workflow states for a team.

        Args:
            team_id: Team ID.

        Returns:
            List of LinearWorkflowState objects.
        """
        query = """
        query TeamStates($teamId: String!) {
          team(id: $teamId) {
            states {
              nodes {
                id
                name
                type
                color
                position
              }
            }
          }
        }
        """
        result = self._graphql(query, {"teamId": team_id})
        states_data = (
            result.get("data", {}).get("team", {}).get("states", {})
            .get("nodes", [])
        )
        return [
            LinearWorkflowState(
                id=s.get("id", ""),
                name=s.get("name", ""),
                type=s.get("type", ""),
                color=s.get("color", ""),
                position=s.get("position", 0),
                team_id=team_id,
                extra=s,
            )
            for s in states_data
        ]

    # ------------------------------------------------------------------
    # Search
    # ------------------------------------------------------------------

    def search_issues(self, query: str,
                      first: int = 50,
                      team_id: Optional[str] = None
                      ) -> Dict[str, Any]:
        """
        Search for issues.

        Args:
            query: Search query string.
            first: Maximum number of results.
            team_id: Optional team filter.

        Returns:
            Dictionary with search results and pagination.
        """
        gql_query = """
        query SearchIssues($query: String!, $first: Int, $teamId: String) {
          searchIssues(query: $query, first: $first, teamId: $teamId) {
            nodes {
              id
              identifier
              title
              description
              state { name type }
              priority
              team { key name }
              url
            }
            pageInfo {
              endCursor
              hasNextPage
            }
          }
        }
        """
        variables: Dict[str, Any] = {"query": query, "first": first}
        if team_id:
            variables["teamId"] = team_id

        result = self._graphql(gql_query, variables)
        search_result = result.get("data", {}).get("searchIssues", {})
        nodes = search_result.get("nodes", [])
        issues = [self._parse_issue(n) for n in nodes]

        return {
            "issues": issues,
            "page_info": search_result.get("pageInfo", {}),
        }

    # ------------------------------------------------------------------
    # Webhook verification
    # ------------------------------------------------------------------

    def verify_webhook(self, request_body: str,
                       signature: str,
                       secret: str) -> bool:
        """
        Verify a Linear webhook signature.

        Args:
            request_body: Raw request body.
            signature: X-Linear-Signature header value.
            secret: Webhook signing secret.

        Returns:
            True if the webhook is verified.
        """
        expected = hmac.new(
            secret.encode("utf-8"),
            request_body.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()
        return hmac.compare_digest(signature, expected)

    # ------------------------------------------------------------------
    # Internal GraphQL helper
    # ------------------------------------------------------------------

    def _graphql(self, query: str,
                 variables: Optional[Dict[str, Any]] = None
                 ) -> Dict[str, Any]:
        """
        Execute a GraphQL query against the Linear API.

        Args:
            query: GraphQL query or mutation string.
            variables: Query variables.

        Returns:
            Parsed JSON response.

        Raises:
            IntegrationError: On API errors.
            RateLimitError: On rate limit exceeded.
        """
        self._check_rate_limit()
        self._mark_request()

        url = self.config.base_url
        headers = {
            "Authorization": f"{self.config.api_key or self.config.access_token or ''}",
            "Content-Type": "application/json",
            "Linear-API-Version": "2024-04-20",
        }

        payload = {"query": query, "variables": variables or {}}
        body = json.dumps(payload)
        self._log_request("POST", url, headers, body[:500])

        try:
            import urllib.request
            import urllib.error

            req = urllib.request.Request(
                url,
                data=body.encode("utf-8"),
                headers=headers,
                method="POST",
            )

            with urllib.request.urlopen(
                req, timeout=self.config.timeout
            ) as response:
                response_body = response.read().decode("utf-8")
                self._log_response("POST", url, response.status, response_body[:500])
                result = json.loads(response_body)

                if "errors" in result:
                    error_msgs = [
                        e.get("message", "Unknown error")
                        for e in result["errors"]
                    ]
                    raise self._handle_error(
                        f"GraphQL error: {'; '.join(error_msgs)}",
                        status_code=400,
                        response_body=response_body,
                    )

                return result

        except urllib.error.HTTPError as e:
            response_body = e.read().decode("utf-8", errors="replace")
            self._log_response("POST", url, e.code, response_body)
            if e.code == 429:
                self._rate_limit_hits += 1
                raise RateLimitError(
                    "Rate limited by Linear",
                    retry_after=60,
                    adapter_name=self.config.name,
                )
            if e.code == 401:
                raise self._handle_error(
                    "Authentication failed: invalid Linear API key",
                    status_code=401,
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
            f"<LinearAdapter name={self.config.name} "
            f"status={self._status.value} "
            f"user={self._user_name or 'unknown'} "
            f"org={self._org_name or 'unknown'}>"
        )
