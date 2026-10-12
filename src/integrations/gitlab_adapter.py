"""
GitLab Integration Adapter for LiuHao AI OS.

Provides integration with GitLab for project management, issues,
merge requests, CI/CD pipelines, repository operations, and user/group management.
Similar structure to the GitHub adapter.
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


class GitlabIssueState(str, Enum):
    """GitLab issue states."""
    OPEN = "opened"
    CLOSED = "closed"
    ALL = "all"


class GitlabMRState(str, Enum):
    """GitLab merge request states."""
    OPEN = "opened"
    CLOSED = "closed"
    MERGED = "merged"
    ALL = "all"


class PipelineStatus(str, Enum):
    """GitLab CI pipeline statuses."""
    RUNNING = "running"
    PENDING = "pending"
    SUCCESS = "success"
    FAILED = "failed"
    CANCELED = "canceled"
    SKIPPED = "skipped"
    CREATED = "created"
    MANUAL = "manual"
    SCHEDULED = "scheduled"
    PREPARING = "preparing"
    WAITING_FOR_RESOURCE = "waiting_for_resource"


@dataclass
class GitlabProject:
    """Represents a GitLab project."""
    id: int
    name: str
    path_with_namespace: str
    description: str = ""
    web_url: str = ""
    namespace: str = ""
    visibility: str = "private"
    default_branch: str = "main"
    ssh_url_to_repo: str = ""
    http_url_to_repo: str = ""
    star_count: int = 0
    forks_count: int = 0
    open_issues_count: int = 0
    created_at: str = ""
    last_activity_at: str = ""
    extra: Dict[str, Any] = field(default_factory=dict)


@dataclass
class GitlabIssue:
    """Represents a GitLab issue."""
    iid: int
    title: str
    description: str = ""
    state: str = "opened"
    web_url: str = ""
    author_username: str = ""
    author_name: str = ""
    labels: List[str] = field(default_factory=list)
    assignees: List[str] = field(default_factory=list)
    milestone: str = ""
    created_at: str = ""
    updated_at: str = ""
    closed_at: str = ""
    user_notes_count: int = 0
    project_id: int = 0
    project_path: str = ""
    extra: Dict[str, Any] = field(default_factory=dict)


@dataclass
class GitlabMergeRequest:
    """Represents a GitLab merge request."""
    iid: int
    title: str
    description: str = ""
    state: str = "opened"
    web_url: str = ""
    author_username: str = ""
    source_branch: str = ""
    target_branch: str = ""
    sha: str = ""
    merge_status: str = ""
    merged: bool = False
    merged_by: str = ""
    labels: List[str] = field(default_factory=list)
    created_at: str = ""
    updated_at: str = ""
    merged_at: str = ""
    changes_count: str = ""
    project_id: int = 0
    project_path: str = ""
    extra: Dict[str, Any] = field(default_factory=dict)


@dataclass
class GitlabPipeline:
    """Represents a GitLab CI/CD pipeline."""
    id: int
    status: str
    ref: str = ""
    sha: str = ""
    web_url: str = ""
    source: str = ""
    created_at: str = ""
    updated_at: str = ""
    started_at: str = ""
    finished_at: str = ""
    duration: int = 0
    project_id: int = 0
    project_path: str = ""
    extra: Dict[str, Any] = field(default_factory=dict)


@dataclass
class GitlabCommit:
    """Represents a GitLab commit."""
    id: str
    short_id: str
    title: str
    message: str
    author_name: str
    author_email: str
    authored_date: str
    committed_date: str
    web_url: str = ""
    project_id: int = 0
    project_path: str = ""
    extra: Dict[str, Any] = field(default_factory=dict)


@dataclass
class GitlabUser:
    """Represents a GitLab user."""
    id: int
    username: str
    name: str = ""
    email: str = ""
    state: str = ""
    avatar_url: str = ""
    web_url: str = ""
    is_admin: bool = False
    created_at: str = ""
    extra: Dict[str, Any] = field(default_factory=dict)


@dataclass
class GitlabGroup:
    """Represents a GitLab group."""
    id: int
    name: str
    full_path: str
    description: str = ""
    web_url: str = ""
    visibility: str = "private"
    parent_id: int = 0
    project_count: int = 0
    member_count: int = 0
    extra: Dict[str, Any] = field(default_factory=dict)


class GitlabAdapter(BaseIntegrationAdapter):
    """
    GitLab integration adapter for LiuHao AI OS.

    Supports full GitLab API v4 integration with:
    - Project management (create, list, update, delete)
    - Issues and merge requests
    - CI/CD pipelines and jobs
    - Repository operations (commits, branches, tags)
    - User and group management
    - Rate limiting with automatic retry
    """

    def __init__(self, config: IntegrationConfig):
        """
        Initialize GitLab adapter.

        Args:
            config: Config with personal access token (access_token or api_key).
        """
        if config.auth_type == AuthType.NONE:
            config.auth_type = AuthType.BEARER_TOKEN
        if not config.base_url:
            config.base_url = "https://gitlab.com/api/v4/"
        super().__init__(config)
        self._user_id: Optional[int] = None
        self._username: Optional[str] = None
        self._rate_limit_remaining: int = 0
        self._rate_limit_limit: int = 0

    # ------------------------------------------------------------------
    # Base interface
    # ------------------------------------------------------------------

    def connect(self) -> bool:
        """Connect to GitLab API and validate token."""
        self._status = ConnectionStatus.CONNECTING
        self._logger.info("Connecting to GitLab API...")

        try:
            user_data = self._api_call("GET", "user")
            self._user_id = user_data.get("id")
            self._username = user_data.get("username")
            self._status = ConnectionStatus.CONNECTED
            self._connected_at = time.time()
            self._logger.info(
                "Connected to GitLab as %s (ID: %d)",
                user_data.get("username"), user_data.get("id"),
            )
            return True
        except IntegrationError:
            raise
        except Exception as e:
            self._status = ConnectionStatus.ERROR
            raise self._handle_error(f"Connection error: {e}")

    def disconnect(self) -> bool:
        """Disconnect from GitLab."""
        self._status = ConnectionStatus.DISCONNECTED
        self._user_id = None
        self._username = None
        self._session = None
        self._logger.info("Disconnected from GitLab")
        return True

    def is_connected(self) -> bool:
        return self._status == ConnectionStatus.CONNECTED

    def get_status(self) -> IntegrationStatus:
        return self._build_status({
            "user_id": self._user_id,
            "username": self._username,
            "rate_limit_remaining": self._rate_limit_remaining,
            "rate_limit_limit": self._rate_limit_limit,
        })

    def test_connection(self) -> bool:
        try:
            if not self.is_connected():
                return False
            result = self._api_call("GET", "user")
            return "id" in result
        except Exception:
            return False

    # ------------------------------------------------------------------
    # Project management
    # ------------------------------------------------------------------

    def get_project(self, project_id_or_path: str) -> GitlabProject:
        """
        Get a project by ID or path (namespace/project).

        Args:
            project_id_or_path: Numeric ID or "namespace/project" path.

        Returns:
            GitlabProject object.
        """
        data = self._api_call(
            "GET", f"projects/{self._encode_path(project_id_or_path)}",
        )
        return self._parse_project(data)

    def list_projects(self,
                      owned: bool = False,
                      membership: bool = True,
                      search: Optional[str] = None,
                      sort: str = "last_activity_at",
                      order_by: str = "desc",
                      per_page: int = 20,
                      page: int = 1,
                      visibility: Optional[str] = None) -> List[GitlabProject]:
        """
        List projects accessible to the authenticated user.

        Args:
            owned: Only show projects owned by user.
            membership: Only show projects user is a member of.
            search: Search query.
            sort: Sort field.
            order_by: Sort direction ("asc" or "desc").
            per_page: Results per page.
            page: Page number.
            visibility: "private", "internal", or "public".

        Returns:
            List of GitlabProject objects.
        """
        params: Dict[str, Any] = {
            "membership": str(membership).lower(),
            "sort": sort,
            "order_by": order_by,
            "per_page": per_page,
            "page": page,
        }
        if owned:
            params["owned"] = "true"
        if search:
            params["search"] = search
        if visibility:
            params["visibility"] = visibility

        data = self._api_call("GET", "projects", params=params)
        return [self._parse_project(p) for p in data]

    def create_project(self, name: str,
                       namespace_id: Optional[int] = None,
                       description: str = "",
                       visibility: str = "private",
                       initialize_with_readme: bool = False,
                       default_branch: str = "main") -> GitlabProject:
        """Create a new project."""
        payload: Dict[str, Any] = {
            "name": name,
            "description": description,
            "visibility": visibility,
            "initialize_with_readme": initialize_with_readme,
            "default_branch": default_branch,
        }
        if namespace_id:
            payload["namespace_id"] = namespace_id

        data = self._api_call("POST", "projects", json_body=payload)
        return self._parse_project(data)

    def delete_project(self, project_id_or_path: str) -> bool:
        """Delete a project. Returns True on success."""
        try:
            self._api_call(
                "DELETE", f"projects/{self._encode_path(project_id_or_path)}",
            )
            return True
        except Exception:
            return False

    # ------------------------------------------------------------------
    # Issues
    # ------------------------------------------------------------------

    def create_issue(self, project_id_or_path: str, title: str,
                     description: str = "",
                     labels: Optional[List[str]] = None,
                     assignee_ids: Optional[List[int]] = None,
                     milestone_id: Optional[int] = None,
                     due_date: Optional[str] = None) -> GitlabIssue:
        """Create a new issue."""
        payload: Dict[str, Any] = {"title": title, "description": description}
        if labels:
            payload["labels"] = ",".join(labels)
        if assignee_ids:
            payload["assignee_ids"] = assignee_ids
        if milestone_id is not None:
            payload["milestone_id"] = milestone_id
        if due_date:
            payload["due_date"] = due_date

        data = self._api_call(
            "POST",
            f"projects/{self._encode_path(project_id_or_path)}/issues",
            json_body=payload,
        )
        return self._parse_issue(data, project_id_or_path)

    def list_issues(self, project_id_or_path: str,
                    state: GitlabIssueState = GitlabIssueState.OPEN,
                    labels: Optional[List[str]] = None,
                    assignee_id: Optional[int] = None,
                    author_id: Optional[int] = None,
                    search: Optional[str] = None,
                    sort: str = "desc",
                    order_by: str = "created_at",
                    per_page: int = 20,
                    page: int = 1) -> List[GitlabIssue]:
        """List issues in a project."""
        params: Dict[str, Any] = {
            "state": state.value,
            "sort": sort,
            "order_by": order_by,
            "per_page": per_page,
            "page": page,
        }
        if labels:
            params["labels"] = ",".join(labels)
        if assignee_id is not None:
            params["assignee_id"] = assignee_id
        if author_id is not None:
            params["author_id"] = author_id
        if search:
            params["search"] = search

        data = self._api_call(
            "GET",
            f"projects/{self._encode_path(project_id_or_path)}/issues",
            params=params,
        )
        return [self._parse_issue(i, project_id_or_path) for i in data]

    def get_issue(self, project_id_or_path: str, issue_iid: int) -> GitlabIssue:
        """Get a specific issue."""
        data = self._api_call(
            "GET",
            f"projects/{self._encode_path(project_id_or_path)}/issues/{issue_iid}",
        )
        return self._parse_issue(data, project_id_or_path)

    def update_issue(self, project_id_or_path: str, issue_iid: int,
                     title: Optional[str] = None,
                     description: Optional[str] = None,
                     state_event: Optional[str] = None,
                     labels: Optional[List[str]] = None,
                     assignee_ids: Optional[List[int]] = None) -> GitlabIssue:
        """Update an issue."""
        payload: Dict[str, Any] = {}
        if title is not None:
            payload["title"] = title
        if description is not None:
            payload["description"] = description
        if state_event is not None:
            payload["state_event"] = state_event
        if labels is not None:
            payload["labels"] = ",".join(labels)
        if assignee_ids is not None:
            payload["assignee_ids"] = assignee_ids

        data = self._api_call(
            "PUT",
            f"projects/{self._encode_path(project_id_or_path)}/issues/{issue_iid}",
            json_body=payload,
        )
        return self._parse_issue(data, project_id_or_path)

    def close_issue(self, project_id_or_path: str, issue_iid: int) -> GitlabIssue:
        """Close an issue."""
        return self.update_issue(
            project_id_or_path, issue_iid, state_event="close",
        )

    def add_issue_note(self, project_id_or_path: str, issue_iid: int,
                       body: str, confidential: bool = False) -> Dict[str, Any]:
        """Add a comment/note to an issue or MR."""
        payload = {"body": body, "confidential": confidential}
        return self._api_call(
            "POST",
            (f"projects/{self._encode_path(project_id_or_path)}"
             f"/issues/{issue_iid}/notes"),
            json_body=payload,
        )

    # ------------------------------------------------------------------
    # Merge Requests
    # ------------------------------------------------------------------

    def create_merge_request(self, project_id_or_path: str, title: str,
                             source_branch: str, target_branch: str,
                             description: str = "",
                             remove_source_branch: bool = False,
                             squash: bool = False,
                             draft: bool = False) -> GitlabMergeRequest:
        """Create a new merge request."""
        payload = {
            "title": title,
            "source_branch": source_branch,
            "target_branch": target_branch,
            "description": description,
            "remove_source_branch": remove_source_branch,
            "squash": squash,
            "draft": draft,
        }
        data = self._api_call(
            "POST",
            f"projects/{self._encode_path(project_id_or_path)}/merge_requests",
            json_body=payload,
        )
        return self._parse_mr(data, project_id_or_path)

    def list_merge_requests(self, project_id_or_path: str,
                            state: GitlabMRState = GitlabMRState.OPEN,
                            source_branch: Optional[str] = None,
                            target_branch: Optional[str] = None,
                            author_id: Optional[int] = None,
                            labels: Optional[List[str]] = None,
                            search: Optional[str] = None,
                            sort: str = "desc",
                            order_by: str = "created_at",
                            per_page: int = 20,
                            page: int = 1) -> List[GitlabMergeRequest]:
        """List merge requests."""
        params: Dict[str, Any] = {
            "state": state.value,
            "sort": sort,
            "order_by": order_by,
            "per_page": per_page,
            "page": page,
        }
        if source_branch:
            params["source_branch"] = source_branch
        if target_branch:
            params["target_branch"] = target_branch
        if author_id is not None:
            params["author_id"] = author_id
        if labels:
            params["labels"] = ",".join(labels)
        if search:
            params["search"] = search

        data = self._api_call(
            "GET",
            f"projects/{self._encode_path(project_id_or_path)}/merge_requests",
            params=params,
        )
        return [self._parse_mr(mr, project_id_or_path) for mr in data]

    def get_merge_request(self, project_id_or_path: str,
                          mr_iid: int) -> GitlabMergeRequest:
        """Get a specific merge request."""
        data = self._api_call(
            "GET",
            (f"projects/{self._encode_path(project_id_or_path)}"
             f"/merge_requests/{mr_iid}"),
        )
        return self._parse_mr(data, project_id_or_path)

    def merge_merge_request(self, project_id_or_path: str, mr_iid: int,
                            merge_commit_message: str = "",
                            squash_commit_message: str = "",
                            should_remove_source_branch: bool = False,
                            merge_when_pipeline_succeeds: bool = False
                            ) -> GitlabMergeRequest:
        """
        Merge a merge request.

        Args:
            project_id_or_path: Project identifier.
            mr_iid: Merge request IID.
            merge_commit_message: Custom merge commit message.
            squash_commit_message: Custom squash commit message.
            should_remove_source_branch: Remove source branch after merge.
            merge_when_pipeline_succeeds: Merge when pipeline succeeds.

        Returns:
            Updated GitlabMergeRequest.
        """
        payload: Dict[str, Any] = {}
        if merge_commit_message:
            payload["merge_commit_message"] = merge_commit_message
        if squash_commit_message:
            payload["squash_commit_message"] = squash_commit_message
        if should_remove_source_branch:
            payload["should_remove_source_branch"] = True
        if merge_when_pipeline_succeeds:
            payload["merge_when_pipeline_succeeds"] = True

        data = self._api_call(
            "PUT",
            (f"projects/{self._encode_path(project_id_or_path)}"
             f"/merge_requests/{mr_iid}/merge"),
            json_body=payload,
        )
        return self._parse_mr(data, project_id_or_path)

    def create_mr_note(self, project_id_or_path: str, mr_iid: int,
                       body: str) -> Dict[str, Any]:
        """Add a comment to a merge request."""
        return self._api_call(
            "POST",
            (f"projects/{self._encode_path(project_id_or_path)}"
             f"/merge_requests/{mr_iid}/notes"),
            json_body={"body": body},
        )

    # ------------------------------------------------------------------
    # CI/CD Pipelines
    # ------------------------------------------------------------------

    def list_pipelines(self, project_id_or_path: str,
                       status: Optional[PipelineStatus] = None,
                       ref: Optional[str] = None,
                       sha: Optional[str] = None,
                       source: Optional[str] = None,
                       username: Optional[str] = None,
                       order_by: str = "id",
                       sort: str = "desc",
                       per_page: int = 20,
                       page: int = 1) -> List[GitlabPipeline]:
        """List CI/CD pipelines for a project."""
        params: Dict[str, Any] = {
            "order_by": order_by,
            "sort": sort,
            "per_page": per_page,
            "page": page,
        }
        if status:
            params["status"] = status.value
        if ref:
            params["ref"] = ref
        if sha:
            params["sha"] = sha
        if source:
            params["source"] = source
        if username:
            params["username"] = username

        data = self._api_call(
            "GET",
            f"projects/{self._encode_path(project_id_or_path)}/pipelines",
            params=params,
        )
        return [self._parse_pipeline(p, project_id_or_path) for p in data]

    def get_pipeline(self, project_id_or_path: str,
                     pipeline_id: int) -> GitlabPipeline:
        """Get a specific pipeline."""
        data = self._api_call(
            "GET",
            (f"projects/{self._encode_path(project_id_or_path)}"
             f"/pipelines/{pipeline_id}"),
        )
        return self._parse_pipeline(data, project_id_or_path)

    def trigger_pipeline(self, project_id_or_path: str,
                         ref: str = "main",
                         variables: Optional[Dict[str, str]] = None
                         ) -> GitlabPipeline:
        """
        Trigger a new pipeline.

        Args:
            project_id_or_path: Project identifier.
            ref: Branch or tag to run on.
            variables: Optional pipeline variables.

        Returns:
            Created pipeline.
        """
        payload: Dict[str, Any] = {"ref": ref}
        if variables:
            payload["variables"] = [
                {"key": k, "value": v, "variable_type": "env_var"}
                for k, v in variables.items()
            ]

        data = self._api_call(
            "POST",
            f"projects/{self._encode_path(project_id_or_path)}/pipeline",
            json_body=payload,
        )
        return self._parse_pipeline(data, project_id_or_path)

    def cancel_pipeline(self, project_id_or_path: str,
                        pipeline_id: int) -> GitlabPipeline:
        """Cancel a running pipeline."""
        data = self._api_call(
            "POST",
            (f"projects/{self._encode_path(project_id_or_path)}"
             f"/pipelines/{pipeline_id}/cancel"),
        )
        return self._parse_pipeline(data, project_id_or_path)

    def retry_pipeline(self, project_id_or_path: str,
                       pipeline_id: int) -> GitlabPipeline:
        """Retry failed jobs in a pipeline."""
        data = self._api_call(
            "POST",
            (f"projects/{self._encode_path(project_id_or_path)}"
             f"/pipelines/{pipeline_id}/retry"),
        )
        return self._parse_pipeline(data, project_id_or_path)

    def list_pipeline_jobs(self, project_id_or_path: str,
                           pipeline_id: int,
                           scope: Optional[List[str]] = None,
                           per_page: int = 20,
                           page: int = 1) -> List[Dict[str, Any]]:
        """List jobs in a pipeline."""
        params: Dict[str, Any] = {"per_page": per_page, "page": page}
        if scope:
            for i, s in enumerate(scope):
                params[f"scope[{i}]"] = s

        return self._api_call(
            "GET",
            (f"projects/{self._encode_path(project_id_or_path)}"
             f"/pipelines/{pipeline_id}/jobs"),
            params=params,
        )

    # ------------------------------------------------------------------
    # Repository operations
    # ------------------------------------------------------------------

    def list_commits(self, project_id_or_path: str,
                     ref_name: Optional[str] = None,
                     path: Optional[str] = None,
                     author: Optional[str] = None,
                     since: Optional[str] = None,
                     until: Optional[str] = None,
                     all_: bool = False,
                     per_page: int = 20,
                     page: int = 1) -> List[GitlabCommit]:
        """List commits in a project repository."""
        params: Dict[str, Any] = {"per_page": per_page, "page": page}
        if ref_name:
            params["ref_name"] = ref_name
        if path:
            params["path"] = path
        if author:
            params["author"] = author
        if since:
            params["since"] = since
        if until:
            params["until"] = until
        if all_:
            params["all"] = "true"

        data = self._api_call(
            "GET",
            f"projects/{self._encode_path(project_id_or_path)}/repository/commits",
            params=params,
        )
        return [self._parse_commit(c, project_id_or_path) for c in data]

    def get_commit(self, project_id_or_path: str, sha: str) -> GitlabCommit:
        """Get a specific commit."""
        data = self._api_call(
            "GET",
            f"projects/{self._encode_path(project_id_or_path)}/repository/commits/{sha}",
        )
        return self._parse_commit(data, project_id_or_path)

    def list_branches(self, project_id_or_path: str,
                      search: Optional[str] = None,
                      per_page: int = 20,
                      page: int = 1) -> List[Dict[str, Any]]:
        """List repository branches."""
        params: Dict[str, Any] = {"per_page": per_page, "page": page}
        if search:
            params["search"] = search
        return self._api_call(
            "GET",
            f"projects/{self._encode_path(project_id_or_path)}/repository/branches",
            params=params,
        )

    def get_file_contents(self, project_id_or_path: str,
                          file_path: str,
                          ref: str = "main") -> Dict[str, Any]:
        """Get file contents from repository."""
        from urllib.parse import quote
        encoded_path = quote(file_path, safe="")
        return self._api_call(
            "GET",
            (f"projects/{self._encode_path(project_id_or_path)}"
             f"/repository/files/{encoded_path}"),
            params={"ref": ref},
        )

    # ------------------------------------------------------------------
    # User management
    # ------------------------------------------------------------------

    def get_current_user(self) -> GitlabUser:
        """Get the authenticated user."""
        data = self._api_call("GET", "user")
        return self._parse_user(data)

    def get_user(self, user_id: int) -> GitlabUser:
        """Get a user by ID."""
        data = self._api_call("GET", f"users/{user_id}")
        return self._parse_user(data)

    def search_users(self, search: str,
                     per_page: int = 20,
                     page: int = 1) -> List[GitlabUser]:
        """Search for users."""
        data = self._api_call(
            "GET", "users",
            params={"search": search, "per_page": per_page, "page": page},
        )
        return [self._parse_user(u) for u in data]

    # ------------------------------------------------------------------
    # Group management
    # ------------------------------------------------------------------

    def list_groups(self, search: Optional[str] = None,
                    owned: bool = False,
                    min_access_level: Optional[int] = None,
                    per_page: int = 20,
                    page: int = 1) -> List[GitlabGroup]:
        """List groups."""
        params: Dict[str, Any] = {"per_page": per_page, "page": page}
        if search:
            params["search"] = search
        if owned:
            params["owned"] = "true"
        if min_access_level is not None:
            params["min_access_level"] = min_access_level

        data = self._api_call("GET", "groups", params=params)
        return [self._parse_group(g) for g in data]

    def get_group(self, group_id_or_path: str) -> GitlabGroup:
        """Get a group by ID or path."""
        data = self._api_call(
            "GET", f"groups/{self._encode_path(group_id_or_path)}",
        )
        return self._parse_group(data)

    def list_group_projects(self, group_id_or_path: str,
                            per_page: int = 20,
                            page: int = 1) -> List[GitlabProject]:
        """List projects in a group."""
        data = self._api_call(
            "GET",
            f"groups/{self._encode_path(group_id_or_path)}/projects",
            params={"per_page": per_page, "page": page},
        )
        return [self._parse_project(p) for p in data]

    # ------------------------------------------------------------------
    # Parsing helpers
    # ------------------------------------------------------------------

    def _parse_project(self, data: Dict[str, Any]) -> GitlabProject:
        return GitlabProject(
            id=data.get("id", 0),
            name=data.get("name", ""),
            path_with_namespace=data.get("path_with_namespace", ""),
            description=data.get("description", "") or "",
            web_url=data.get("web_url", ""),
            namespace=(data.get("namespace") or {}).get("full_path", ""),
            visibility=data.get("visibility", "private"),
            default_branch=data.get("default_branch", "main"),
            ssh_url_to_repo=data.get("ssh_url_to_repo", ""),
            http_url_to_repo=data.get("http_url_to_repo", ""),
            star_count=data.get("star_count", 0),
            forks_count=data.get("forks_count", 0),
            open_issues_count=data.get("open_issues_count", 0),
            created_at=data.get("created_at", ""),
            last_activity_at=data.get("last_activity_at", ""),
            extra=data,
        )

    def _parse_issue(self, data: Dict[str, Any],
                     project_path: str) -> GitlabIssue:
        return GitlabIssue(
            iid=data.get("iid", 0),
            title=data.get("title", ""),
            description=data.get("description", "") or "",
            state=data.get("state", ""),
            web_url=data.get("web_url", ""),
            author_username=data.get("author", {}).get("username", ""),
            author_name=data.get("author", {}).get("name", ""),
            labels=data.get("labels", []) or [],
            assignees=[a.get("username", "") for a in data.get("assignees", [])],
            milestone=(data.get("milestone") or {}).get("title", ""),
            created_at=data.get("created_at", ""),
            updated_at=data.get("updated_at", ""),
            closed_at=data.get("closed_at", "") or "",
            user_notes_count=data.get("user_notes_count", 0),
            project_id=data.get("project_id", 0),
            project_path=project_path,
            extra=data,
        )

    def _parse_mr(self, data: Dict[str, Any],
                  project_path: str) -> GitlabMergeRequest:
        return GitlabMergeRequest(
            iid=data.get("iid", 0),
            title=data.get("title", ""),
            description=data.get("description", "") or "",
            state=data.get("state", ""),
            web_url=data.get("web_url", ""),
            author_username=data.get("author", {}).get("username", ""),
            source_branch=data.get("source_branch", ""),
            target_branch=data.get("target_branch", ""),
            sha=data.get("sha", ""),
            merge_status=data.get("merge_status", ""),
            merged=data.get("state") == "merged",
            merged_by=(data.get("merged_by") or {}).get("username", ""),
            labels=data.get("labels", []) or [],
            created_at=data.get("created_at", ""),
            updated_at=data.get("updated_at", ""),
            merged_at=data.get("merged_at", "") or "",
            changes_count=str(data.get("changes_count", "")),
            project_id=data.get("project_id", 0),
            project_path=project_path,
            extra=data,
        )

    def _parse_pipeline(self, data: Dict[str, Any],
                        project_path: str) -> GitlabPipeline:
        return GitlabPipeline(
            id=data.get("id", 0),
            status=data.get("status", ""),
            ref=data.get("ref", ""),
            sha=data.get("sha", ""),
            web_url=data.get("web_url", ""),
            source=data.get("source", ""),
            created_at=data.get("created_at", ""),
            updated_at=data.get("updated_at", ""),
            started_at=data.get("started_at", "") or "",
            finished_at=data.get("finished_at", "") or "",
            duration=data.get("duration", 0) or 0,
            project_id=data.get("project_id", 0),
            project_path=project_path,
            extra=data,
        )

    def _parse_commit(self, data: Dict[str, Any],
                      project_path: str) -> GitlabCommit:
        return GitlabCommit(
            id=data.get("id", ""),
            short_id=data.get("short_id", ""),
            title=data.get("title", ""),
            message=data.get("message", ""),
            author_name=data.get("author_name", ""),
            author_email=data.get("author_email", ""),
            authored_date=data.get("authored_date", ""),
            committed_date=data.get("committed_date", ""),
            web_url=data.get("web_url", ""),
            project_id=data.get("project_id", 0),
            project_path=project_path,
            extra=data,
        )

    def _parse_user(self, data: Dict[str, Any]) -> GitlabUser:
        return GitlabUser(
            id=data.get("id", 0),
            username=data.get("username", ""),
            name=data.get("name", ""),
            email=data.get("email", ""),
            state=data.get("state", ""),
            avatar_url=data.get("avatar_url", ""),
            web_url=data.get("web_url", ""),
            is_admin=data.get("is_admin", False),
            created_at=data.get("created_at", ""),
            extra=data,
        )

    def _parse_group(self, data: Dict[str, Any]) -> GitlabGroup:
        return GitlabGroup(
            id=data.get("id", 0),
            name=data.get("name", ""),
            full_path=data.get("full_path", ""),
            description=data.get("description", "") or "",
            web_url=data.get("web_url", ""),
            visibility=data.get("visibility", "private"),
            parent_id=data.get("parent_id", 0) or 0,
            project_count=data.get("project_count", 0) or 0,
            member_count=data.get("member_count", 0) or 0,
            extra=data,
        )

    def _encode_path(self, path: str) -> str:
        """URL-encode a project/group path for use in API URLs."""
        from urllib.parse import quote
        # If it's purely numeric, use as-is
        if str(path).isdigit():
            return str(path)
        return quote(str(path), safe="")

    # ------------------------------------------------------------------
    # Internal API helper
    # ------------------------------------------------------------------

    def _api_call(self, method: str, path: str,
                  params: Optional[Dict[str, Any]] = None,
                  json_body: Optional[Dict[str, Any]] = None,
                  _retry_count: int = 0) -> Any:
        """
        Make a GitLab API call with retry on rate limits.
        """
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
        """Execute a single API request."""
        self._check_rate_limit()
        self._mark_request()

        url = self._build_url(path)
        token = self.config.access_token or self.config.api_key or ""
        headers = {
            "PRIVATE-TOKEN": token,
            "Content-Type": "application/json",
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

                # Track rate limit headers
                remaining = resp.headers.get("RateLimit-Remaining")
                limit = resp.headers.get("RateLimit-Limit")
                if remaining is not None:
                    self._rate_limit_remaining = int(remaining)
                if limit is not None:
                    self._rate_limit_limit = int(limit)

                if resp.status == 204:
                    return {}
                if not resp_body:
                    return {}
                return json.loads(resp_body)

        except urllib.error.HTTPError as e:
            resp_body = e.read().decode("utf-8", errors="replace")
            self._log_response(method, url, e.code, resp_body)

            if e.code == 429:
                retry_after = float(e.headers.get("Retry-After", 60))
                self._rate_limit_hits += 1
                raise RateLimitError(
                    f"GitLab rate limited, retry after {retry_after}s",
                    retry_after=retry_after,
                    adapter_name=self.config.name,
                )

            if e.code == 401:
                self._status = ConnectionStatus.ERROR
                raise self._handle_error(
                    "Authentication failed: invalid GitLab token",
                    status_code=401,
                    response_body=resp_body,
                )

            raise self._handle_error(
                f"GitLab API error {e.code}: {e.reason}",
                status_code=e.code,
                response_body=resp_body,
            )
        except (RateLimitError, IntegrationError):
            raise
        except Exception as e:
            raise self._handle_error(f"Request failed: {e}")
