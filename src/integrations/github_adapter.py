"""
GitHub Integration Adapter for LiuHao AI OS.

Provides integration with GitHub for repository management, issues,
pull requests, code search, Actions workflows, commits, branches,
releases, and rate limiting with automatic retry.
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


class PullRequestState(str, Enum):
    """Pull request states."""
    OPEN = "open"
    CLOSED = "closed"
    ALL = "all"


class IssueState(str, Enum):
    """Issue states."""
    OPEN = "open"
    CLOSED = "closed"
    ALL = "all"


class IssueSort(str, Enum):
    """Issue sort options."""
    CREATED = "created"
    UPDATED = "updated"
    COMMENTS = "comments"


@dataclass
class GitHubIssue:
    """Represents a GitHub issue."""
    number: int
    title: str
    body: str = ""
    state: str = "open"
    html_url: str = ""
    user_login: str = ""
    labels: List[str] = field(default_factory=list)
    assignees: List[str] = field(default_factory=list)
    milestone: str = ""
    created_at: str = ""
    updated_at: str = ""
    closed_at: str = ""
    comments: int = 0
    repo: str = ""
    extra: Dict[str, Any] = field(default_factory=dict)


@dataclass
class GitHubPullRequest:
    """Represents a GitHub pull request."""
    number: int
    title: str
    body: str = ""
    state: str = "open"
    html_url: str = ""
    user_login: str = ""
    base_branch: str = ""
    head_branch: str = ""
    head_sha: str = ""
    mergeable: Optional[bool] = None
    merged: bool = False
    merged_by: str = ""
    labels: List[str] = field(default_factory=list)
    created_at: str = ""
    updated_at: str = ""
    closed_at: str = ""
    merged_at: str = ""
    additions: int = 0
    deletions: int = 0
    changed_files: int = 0
    repo: str = ""
    extra: Dict[str, Any] = field(default_factory=dict)


@dataclass
class GitHubRepository:
    """Represents a GitHub repository."""
    name: str
    full_name: str
    description: str = ""
    html_url: str = ""
    owner: str = ""
    is_private: bool = False
    is_fork: bool = False
    default_branch: str = "main"
    language: str = ""
    stargazers_count: int = 0
    forks_count: int = 0
    open_issues_count: int = 0
    created_at: str = ""
    updated_at: str = ""
    extra: Dict[str, Any] = field(default_factory=dict)


@dataclass
class GitHubWorkflowRun:
    """Represents a GitHub Actions workflow run."""
    id: int
    name: str
    status: str
    conclusion: str = ""
    html_url: str = ""
    head_branch: str = ""
    head_sha: str = ""
    event: str = ""
    created_at: str = ""
    updated_at: str = ""
    run_number: int = 0
    repo: str = ""
    extra: Dict[str, Any] = field(default_factory=dict)


@dataclass
class GitHubCommit:
    """Represents a Git commit."""
    sha: str
    message: str
    author_name: str
    author_email: str
    date: str
    html_url: str = ""
    repo: str = ""
    extra: Dict[str, Any] = field(default_factory=dict)


@dataclass
class GitHubRelease:
    """Represents a GitHub release."""
    id: int
    tag_name: str
    name: str = ""
    body: str = ""
    draft: bool = False
    prerelease: bool = False
    html_url: str = ""
    author: str = ""
    created_at: str = ""
    published_at: str = ""
    repo: str = ""
    extra: Dict[str, Any] = field(default_factory=dict)


class GitHubAdapter(BaseIntegrationAdapter):
    """
    GitHub integration adapter for LiuHao AI OS.

    Supports full GitHub API v3 integration with:
    - Repository management (create, list, delete, update)
    - Issues (create, list, update, close, comment)
    - Pull requests (create, list, merge, review, comments)
    - Code search with pagination
    - GitHub Actions (trigger workflow, check status, list runs)
    - Commits, branches, tags, releases
    - Smart rate limiting with retry and backoff
    """

    def __init__(self, config: IntegrationConfig):
        """
        Initialize GitHub adapter.

        Args:
            config: Config with API token (access_token or api_key).
        """
        if config.auth_type == AuthType.NONE:
            config.auth_type = AuthType.BEARER_TOKEN
        if not config.base_url:
            config.base_url = "https://api.github.com/"
        # GitHub default rate limits: 5000/hour authenticated, 60/hour unauthenticated
        if config.rate_limit_per_hour == 1000:
            config.rate_limit_per_hour = 5000
        if config.rate_limit_per_minute == 60:
            config.rate_limit_per_minute = 0  # We use per-hour limits primarily
        super().__init__(config)
        self._username: Optional[str] = None
        self._rate_limit_remaining: int = 0
        self._rate_limit_limit: int = 0
        self._rate_limit_reset: int = 0

    # ------------------------------------------------------------------
    # Base interface
    # ------------------------------------------------------------------

    def connect(self) -> bool:
        """Connect to GitHub API and validate token."""
        self._status = ConnectionStatus.CONNECTING
        self._logger.info("Connecting to GitHub API...")

        try:
            user_data = self._api_call("GET", "user")
            self._username = user_data.get("login")
            self._status = ConnectionStatus.CONNECTED
            self._connected_at = time.time()
            self._logger.info(
                "Connected to GitHub as %s", user_data.get("login"),
            )
            return True
        except IntegrationError:
            raise
        except Exception as e:
            self._status = ConnectionStatus.ERROR
            raise self._handle_error(f"Connection error: {e}")

    def disconnect(self) -> bool:
        """Disconnect from GitHub."""
        self._status = ConnectionStatus.DISCONNECTED
        self._username = None
        self._session = None
        self._logger.info("Disconnected from GitHub")
        return True

    def is_connected(self) -> bool:
        return self._status == ConnectionStatus.CONNECTED

    def get_status(self) -> IntegrationStatus:
        return self._build_status({
            "username": self._username,
            "rate_limit_remaining": self._rate_limit_remaining,
            "rate_limit_limit": self._rate_limit_limit,
            "rate_limit_reset_timestamp": self._rate_limit_reset,
        })

    def test_connection(self) -> bool:
        try:
            if not self.is_connected():
                return False
            result = self._api_call("GET", "user")
            return "login" in result
        except Exception:
            return False

    # ------------------------------------------------------------------
    # Repository management
    # ------------------------------------------------------------------

    def get_repository(self, repo: str) -> GitHubRepository:
        """
        Get a repository by full name (owner/name).

        Args:
            repo: Full repo name, e.g. "owner/repo".

        Returns:
            GitHubRepository object.
        """
        data = self._api_call("GET", f"repos/{repo}")
        return self._parse_repo(data)

    def list_repositories(self, user: Optional[str] = None,
                          visibility: str = "all",
                          sort: str = "updated",
                          per_page: int = 30,
                          page: int = 1) -> List[GitHubRepository]:
        """
        List repositories for the authenticated user or a specific user/org.

        Args:
            user: Username or org (None = authenticated user).
            visibility: "all", "public", "private".
            sort: "created", "updated", "pushed", "full_name".
            per_page: Results per page.
            page: Page number.

        Returns:
            List of GitHubRepository objects.
        """
        params = {"visibility": visibility, "sort": sort,
                  "per_page": per_page, "page": page}
        if user:
            path = f"users/{user}/repos"
        else:
            path = "user/repos"

        data = self._api_call("GET", path, params=params)
        return [self._parse_repo(r) for r in data]

    def create_repository(self, name: str,
                          description: str = "",
                          private: bool = True,
                          auto_init: bool = False,
                          gitignore_template: str = "",
                          org: Optional[str] = None) -> GitHubRepository:
        """Create a new repository."""
        payload: Dict[str, Any] = {
            "name": name,
            "description": description,
            "private": private,
            "auto_init": auto_init,
        }
        if gitignore_template:
            payload["gitignore_template"] = gitignore_template

        path = f"orgs/{org}/repos" if org else "user/repos"
        data = self._api_call("POST", path, json_body=payload)
        return self._parse_repo(data)

    def delete_repository(self, repo: str) -> bool:
        """Delete a repository. Returns True on success."""
        try:
            self._api_call("DELETE", f"repos/{repo}")
            return True
        except Exception:
            return False

    # ------------------------------------------------------------------
    # Issues
    # ------------------------------------------------------------------

    def create_issue(self, repo: str, title: str, body: str = "",
                     labels: Optional[List[str]] = None,
                     assignees: Optional[List[str]] = None,
                     milestone: Optional[int] = None) -> GitHubIssue:
        """Create a new issue."""
        payload: Dict[str, Any] = {"title": title, "body": body}
        if labels:
            payload["labels"] = labels
        if assignees:
            payload["assignees"] = assignees
        if milestone is not None:
            payload["milestone"] = milestone

        data = self._api_call("POST", f"repos/{repo}/issues", json_body=payload)
        return self._parse_issue(data, repo)

    def list_issues(self, repo: str,
                    state: IssueState = IssueState.OPEN,
                    labels: Optional[List[str]] = None,
                    sort: IssueSort = IssueSort.CREATED,
                    direction: str = "desc",
                    per_page: int = 30,
                    page: int = 1,
                    assignee: Optional[str] = None,
                    creator: Optional[str] = None) -> List[GitHubIssue]:
        """List issues in a repository."""
        params: Dict[str, Any] = {
            "state": state.value,
            "sort": sort.value,
            "direction": direction,
            "per_page": per_page,
            "page": page,
        }
        if labels:
            params["labels"] = ",".join(labels)
        if assignee:
            params["assignee"] = assignee
        if creator:
            params["creator"] = creator

        data = self._api_call("GET", f"repos/{repo}/issues", params=params)
        # Filter out pull requests (GitHub API includes PRs in issues endpoint)
        issues = [i for i in data if "pull_request" not in i]
        return [self._parse_issue(i, repo) for i in issues]

    def get_issue(self, repo: str, issue_number: int) -> GitHubIssue:
        """Get a specific issue."""
        data = self._api_call("GET", f"repos/{repo}/issues/{issue_number}")
        return self._parse_issue(data, repo)

    def update_issue(self, repo: str, issue_number: int,
                     title: Optional[str] = None,
                     body: Optional[str] = None,
                     state: Optional[str] = None,
                     labels: Optional[List[str]] = None,
                     assignees: Optional[List[str]] = None) -> GitHubIssue:
        """Update an issue."""
        payload: Dict[str, Any] = {}
        if title is not None:
            payload["title"] = title
        if body is not None:
            payload["body"] = body
        if state is not None:
            payload["state"] = state
        if labels is not None:
            payload["labels"] = labels
        if assignees is not None:
            payload["assignees"] = assignees

        data = self._api_call(
            "PATCH", f"repos/{repo}/issues/{issue_number}", json_body=payload,
        )
        return self._parse_issue(data, repo)

    def close_issue(self, repo: str, issue_number: int) -> GitHubIssue:
        """Close an issue."""
        return self.update_issue(repo, issue_number, state="closed")

    def add_issue_comment(self, repo: str, issue_number: int,
                          body: str) -> Dict[str, Any]:
        """Add a comment to an issue or PR."""
        return self._api_call(
            "POST",
            f"repos/{repo}/issues/{issue_number}/comments",
            json_body={"body": body},
        )

    def search_issues(self, query: str,
                      sort: str = "created",
                      order: str = "desc",
                      per_page: int = 30,
                      page: int = 1) -> Tuple[List[GitHubIssue], int]:
        """
        Search issues across GitHub.

        Args:
            query: GitHub search query string.
            sort: Sort field.
            order: "asc" or "desc".
            per_page: Results per page.
            page: Page number.

        Returns:
            Tuple of (issues list, total count).
        """
        params = {"q": query, "sort": sort, "order": order,
                  "per_page": per_page, "page": page}
        data = self._api_call("GET", "search/issues", params=params)
        issues = [
            self._parse_issue(i, i.get("repository_url", "").replace(
                "https://api.github.com/repos/", ""))
            for i in data.get("items", [])
        ]
        return issues, data.get("total_count", 0)

    # ------------------------------------------------------------------
    # Pull requests
    # ------------------------------------------------------------------

    def create_pull_request(self, repo: str, title: str,
                            head: str, base: str,
                            body: str = "",
                            draft: bool = False) -> GitHubPullRequest:
        """
        Create a new pull request.

        Args:
            repo: Repository name (owner/repo).
            title: PR title.
            head: Branch name with changes.
            base: Target branch.
            body: PR description.
            draft: Whether to create as draft.

        Returns:
            GitHubPullRequest object.
        """
        payload = {
            "title": title,
            "head": head,
            "base": base,
            "body": body,
            "draft": draft,
        }
        data = self._api_call("POST", f"repos/{repo}/pulls", json_body=payload)
        return self._parse_pr(data, repo)

    def list_pull_requests(self, repo: str,
                           state: PullRequestState = PullRequestState.OPEN,
                           head: Optional[str] = None,
                           base: Optional[str] = None,
                           sort: str = "created",
                           direction: str = "desc",
                           per_page: int = 30,
                           page: int = 1) -> List[GitHubPullRequest]:
        """List pull requests."""
        params: Dict[str, Any] = {
            "state": state.value,
            "sort": sort,
            "direction": direction,
            "per_page": per_page,
            "page": page,
        }
        if head:
            params["head"] = head
        if base:
            params["base"] = base

        data = self._api_call("GET", f"repos/{repo}/pulls", params=params)
        return [self._parse_pr(pr, repo) for pr in data]

    def get_pull_request(self, repo: str, pr_number: int) -> GitHubPullRequest:
        """Get a specific pull request."""
        data = self._api_call("GET", f"repos/{repo}/pulls/{pr_number}")
        return self._parse_pr(data, repo)

    def merge_pull_request(self, repo: str, pr_number: int,
                           merge_method: str = "merge",
                           commit_title: str = "",
                           commit_message: str = "") -> Dict[str, Any]:
        """
        Merge a pull request.

        Args:
            repo: Repository name.
            pr_number: PR number.
            merge_method: "merge", "squash", or "rebase".
            commit_title: Title for the merge commit.
            commit_message: Message for the merge commit.

        Returns:
            Merge result data.
        """
        payload: Dict[str, Any] = {"merge_method": merge_method}
        if commit_title:
            payload["commit_title"] = commit_title
        if commit_message:
            payload["commit_message"] = commit_message

        return self._api_call(
            "PUT", f"repos/{repo}/pulls/{pr_number}/merge", json_body=payload,
        )

    def create_pr_review(self, repo: str, pr_number: int,
                         event: str = "COMMENT",
                         body: str = "",
                         comments: Optional[List[Dict[str, Any]]] = None
                         ) -> Dict[str, Any]:
        """
        Create a review on a pull request.

        Args:
            repo: Repository name.
            pr_number: PR number.
            event: "APPROVE", "REQUEST_CHANGES", "COMMENT", or "PENDING".
            body: Review body text.
            comments: Optional list of line comments.

        Returns:
            Review data.
        """
        payload: Dict[str, Any] = {"event": event, "body": body}
        if comments:
            payload["comments"] = comments
        return self._api_call(
            "POST", f"repos/{repo}/pulls/{pr_number}/reviews", json_body=payload,
        )

    # ------------------------------------------------------------------
    # Code search
    # ------------------------------------------------------------------

    def search_code(self, query: str,
                    per_page: int = 30,
                    page: int = 1,
                    sort: str = "indexed",
                    order: str = "desc") -> Tuple[List[Dict[str, Any]], int]:
        """
        Search code on GitHub.

        Args:
            query: Code search query.
            per_page: Results per page.
            page: Page number.
            sort: Sort field.
            order: "asc" or "desc".

        Returns:
            Tuple of (results list, total count).
        """
        params = {"q": query, "per_page": per_page, "page": page,
                  "sort": sort, "order": order}
        data = self._api_call("GET", "search/code", params=params)
        return data.get("items", []), data.get("total_count", 0)

    # ------------------------------------------------------------------
    # GitHub Actions
    # ------------------------------------------------------------------

    def trigger_workflow(self, repo: str, workflow_id: str,
                         ref: str = "main",
                         inputs: Optional[Dict[str, str]] = None) -> bool:
        """
        Trigger a GitHub Actions workflow run.

        Args:
            repo: Repository name.
            workflow_id: Workflow filename or ID.
            ref: Branch or tag to run on.
            inputs: Optional workflow inputs.

        Returns:
            True if workflow was triggered successfully.
        """
        payload: Dict[str, Any] = {"ref": ref}
        if inputs:
            payload["inputs"] = inputs

        try:
            self._api_call(
                "POST",
                f"repos/{repo}/actions/workflows/{workflow_id}/dispatches",
                json_body=payload,
            )
            return True
        except Exception:
            return False

    def list_workflow_runs(self, repo: str,
                           status: Optional[str] = None,
                           branch: Optional[str] = None,
                           per_page: int = 30,
                           page: int = 1) -> Tuple[List[GitHubWorkflowRun], int]:
        """List workflow runs for a repository."""
        params: Dict[str, Any] = {"per_page": per_page, "page": page}
        if status:
            params["status"] = status
        if branch:
            params["branch"] = branch

        data = self._api_call(
            "GET", f"repos/{repo}/actions/runs", params=params,
        )
        runs = [self._parse_workflow_run(r, repo) for r in data.get("workflow_runs", [])]
        return runs, data.get("total_count", 0)

    def get_workflow_run(self, repo: str, run_id: int) -> GitHubWorkflowRun:
        """Get a specific workflow run."""
        data = self._api_call("GET", f"repos/{repo}/actions/runs/{run_id}")
        return self._parse_workflow_run(data, repo)

    def cancel_workflow_run(self, repo: str, run_id: int) -> bool:
        """Cancel a workflow run. Returns True on success."""
        try:
            self._api_call("POST", f"repos/{repo}/actions/runs/{run_id}/cancel")
            return True
        except Exception:
            return False

    # ------------------------------------------------------------------
    # Commits, branches, releases
    # ------------------------------------------------------------------

    def list_commits(self, repo: str,
                     branch: Optional[str] = None,
                     path: Optional[str] = None,
                     author: Optional[str] = None,
                     since: Optional[str] = None,
                     until: Optional[str] = None,
                     per_page: int = 30,
                     page: int = 1) -> List[GitHubCommit]:
        """List commits in a repository."""
        params: Dict[str, Any] = {"per_page": per_page, "page": page}
        if branch:
            params["sha"] = branch
        if path:
            params["path"] = path
        if author:
            params["author"] = author
        if since:
            params["since"] = since
        if until:
            params["until"] = until

        data = self._api_call("GET", f"repos/{repo}/commits", params=params)
        return [self._parse_commit(c, repo) for c in data]

    def get_commit(self, repo: str, sha: str) -> GitHubCommit:
        """Get a specific commit."""
        data = self._api_call("GET", f"repos/{repo}/commits/{sha}")
        return self._parse_commit(data, repo)

    def list_branches(self, repo: str,
                      per_page: int = 30,
                      page: int = 1) -> List[Dict[str, Any]]:
        """List branches in a repository."""
        return self._api_call(
            "GET", f"repos/{repo}/branches",
            params={"per_page": per_page, "page": page},
        )

    def get_branch(self, repo: str, branch: str) -> Dict[str, Any]:
        """Get a specific branch."""
        return self._api_call("GET", f"repos/{repo}/branches/{branch}")

    def list_releases(self, repo: str,
                      per_page: int = 30,
                      page: int = 1) -> List[GitHubRelease]:
        """List releases for a repository."""
        data = self._api_call(
            "GET", f"repos/{repo}/releases",
            params={"per_page": per_page, "page": page},
        )
        return [self._parse_release(r, repo) for r in data]

    def create_release(self, repo: str, tag_name: str,
                       name: str = "",
                       body: str = "",
                       target_commitish: str = "main",
                       draft: bool = False,
                       prerelease: bool = False) -> GitHubRelease:
        """Create a new release."""
        payload = {
            "tag_name": tag_name,
            "name": name,
            "body": body,
            "target_commitish": target_commitish,
            "draft": draft,
            "prerelease": prerelease,
        }
        data = self._api_call("POST", f"repos/{repo}/releases", json_body=payload)
        return self._parse_release(data, repo)

    # ------------------------------------------------------------------
    # Parsing helpers
    # ------------------------------------------------------------------

    def _parse_repo(self, data: Dict[str, Any]) -> GitHubRepository:
        return GitHubRepository(
            name=data.get("name", ""),
            full_name=data.get("full_name", ""),
            description=data.get("description", "") or "",
            html_url=data.get("html_url", ""),
            owner=data.get("owner", {}).get("login", ""),
            is_private=data.get("private", False),
            is_fork=data.get("fork", False),
            default_branch=data.get("default_branch", "main"),
            language=data.get("language", "") or "",
            stargazers_count=data.get("stargazers_count", 0),
            forks_count=data.get("forks_count", 0),
            open_issues_count=data.get("open_issues_count", 0),
            created_at=data.get("created_at", ""),
            updated_at=data.get("updated_at", ""),
            extra=data,
        )

    def _parse_issue(self, data: Dict[str, Any], repo: str) -> GitHubIssue:
        return GitHubIssue(
            number=data.get("number", 0),
            title=data.get("title", ""),
            body=data.get("body", "") or "",
            state=data.get("state", ""),
            html_url=data.get("html_url", ""),
            user_login=data.get("user", {}).get("login", ""),
            labels=[l.get("name", "") for l in data.get("labels", [])],
            assignees=[a.get("login", "") for a in data.get("assignees", [])],
            milestone=(data.get("milestone") or {}).get("title", ""),
            created_at=data.get("created_at", ""),
            updated_at=data.get("updated_at", ""),
            closed_at=data.get("closed_at", "") or "",
            comments=data.get("comments", 0),
            repo=repo,
            extra=data,
        )

    def _parse_pr(self, data: Dict[str, Any], repo: str) -> GitHubPullRequest:
        return GitHubPullRequest(
            number=data.get("number", 0),
            title=data.get("title", ""),
            body=data.get("body", "") or "",
            state=data.get("state", ""),
            html_url=data.get("html_url", ""),
            user_login=data.get("user", {}).get("login", ""),
            base_branch=data.get("base", {}).get("ref", ""),
            head_branch=data.get("head", {}).get("ref", ""),
            head_sha=data.get("head", {}).get("sha", ""),
            mergeable=data.get("mergeable"),
            merged=data.get("merged", False),
            merged_by=(data.get("merged_by") or {}).get("login", ""),
            labels=[l.get("name", "") for l in data.get("labels", [])],
            created_at=data.get("created_at", ""),
            updated_at=data.get("updated_at", ""),
            closed_at=data.get("closed_at", "") or "",
            merged_at=data.get("merged_at", "") or "",
            additions=data.get("additions", 0),
            deletions=data.get("deletions", 0),
            changed_files=data.get("changed_files", 0),
            repo=repo,
            extra=data,
        )

    def _parse_workflow_run(self, data: Dict[str, Any], repo: str) -> GitHubWorkflowRun:
        return GitHubWorkflowRun(
            id=data.get("id", 0),
            name=data.get("name", ""),
            status=data.get("status", ""),
            conclusion=data.get("conclusion", "") or "",
            html_url=data.get("html_url", ""),
            head_branch=data.get("head_branch", ""),
            head_sha=data.get("head_sha", ""),
            event=data.get("event", ""),
            created_at=data.get("created_at", ""),
            updated_at=data.get("updated_at", ""),
            run_number=data.get("run_number", 0),
            repo=repo,
            extra=data,
        )

    def _parse_commit(self, data: Dict[str, Any], repo: str) -> GitHubCommit:
        commit_info = data.get("commit", {})
        author_info = commit_info.get("author", {})
        return GitHubCommit(
            sha=data.get("sha", ""),
            message=commit_info.get("message", ""),
            author_name=author_info.get("name", ""),
            author_email=author_info.get("email", ""),
            date=author_info.get("date", ""),
            html_url=data.get("html_url", ""),
            repo=repo,
            extra=data,
        )

    def _parse_release(self, data: Dict[str, Any], repo: str) -> GitHubRelease:
        return GitHubRelease(
            id=data.get("id", 0),
            tag_name=data.get("tag_name", ""),
            name=data.get("name", "") or "",
            body=data.get("body", "") or "",
            draft=data.get("draft", False),
            prerelease=data.get("prerelease", False),
            html_url=data.get("html_url", ""),
            author=data.get("author", {}).get("login", ""),
            created_at=data.get("created_at", ""),
            published_at=data.get("published_at", "") or "",
            repo=repo,
            extra=data,
        )

    # ------------------------------------------------------------------
    # Internal API helper with retry logic
    # ------------------------------------------------------------------

    def _api_call(self, method: str, path: str,
                  params: Optional[Dict[str, Any]] = None,
                  json_body: Optional[Dict[str, Any]] = None,
                  _retry_count: int = 0) -> Any:
        """
        Make a GitHub API call with automatic retry on rate limits.

        Implements exponential backoff for rate limit errors.
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
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github.v3+json",
            "Content-Type": "application/json",
            "User-Agent": "LiuHao-AI-OS",
            "X-GitHub-Api-Version": "2022-11-28",
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
                remaining = resp.headers.get("X-RateLimit-Remaining")
                limit = resp.headers.get("X-RateLimit-Limit")
                reset = resp.headers.get("X-RateLimit-Reset")
                if remaining is not None:
                    self._rate_limit_remaining = int(remaining)
                if limit is not None:
                    self._rate_limit_limit = int(limit)
                if reset is not None:
                    self._rate_limit_reset = int(reset)

                if resp.status == 204:
                    return {}
                return json.loads(resp_body)

        except urllib.error.HTTPError as e:
            resp_body = e.read().decode("utf-8", errors="replace")
            self._log_response(method, url, e.code, resp_body)

            # Update rate limit info from error response
            remaining = e.headers.get("X-RateLimit-Remaining")
            reset = e.headers.get("X-RateLimit-Reset")
            if remaining is not None:
                self._rate_limit_remaining = int(remaining)
            if reset is not None:
                self._rate_limit_reset = int(reset)

            if e.code == 403 and remaining == "0":
                reset_time = int(reset) if reset else time.time() + 60
                retry_after = max(reset_time - time.time(), 1)
                self._rate_limit_hits += 1
                raise RateLimitError(
                    f"GitHub rate limit exceeded, resets in {retry_after:.0f}s",
                    retry_after=retry_after,
                    adapter_name=self.config.name,
                )

            if e.code == 429:
                retry_after = float(e.headers.get("Retry-After", 60))
                self._rate_limit_hits += 1
                raise RateLimitError(
                    f"GitHub secondary rate limit, retry after {retry_after}s",
                    retry_after=retry_after,
                    adapter_name=self.config.name,
                )

            if e.code == 401:
                self._status = ConnectionStatus.ERROR
                raise self._handle_error(
                    "Authentication failed: invalid GitHub token",
                    status_code=401,
                    response_body=resp_body,
                )

            raise self._handle_error(
                f"GitHub API error {e.code}: {e.reason}",
                status_code=e.code,
                response_body=resp_body,
            )
        except (RateLimitError, IntegrationError):
            raise
        except Exception as e:
            raise self._handle_error(f"Request failed: {e}")
