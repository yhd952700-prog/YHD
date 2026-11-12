"""
Asana Integration Adapter for LiuHao AI OS.

Provides integration with Asana project management for tasks, projects,
sections, workspaces, teams, users, attachments, comments, tags,
due dates, and dependencies.
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


class AsanaTaskStatus(str, Enum):
    """Task completion status."""
    INCOMPLETE = "incomplete"
    COMPLETED = "completed"


class AsanaResourceType(str, Enum):
    """Asana resource types."""
    TASK = "task"
    PROJECT = "project"
    SECTION = "section"
    WORKSPACE = "workspace"
    TEAM = "team"
    USER = "user"
    TAG = "tag"
    STORY = "story"
    ATTACHMENT = "attachment"


class AsanaEventType(str, Enum):
    """Asana webhook event types."""
    ADDED = "added"
    CHANGED = "changed"
    REMOVED = "removed"
    DELETED = "deleted"
    UNDELETED = "undeleted"


@dataclass
class AsanaTask:
    """Represents an Asana task."""
    gid: str = ""
    name: str = ""
    notes: str = ""
    completed: bool = False
    completed_at: Optional[str] = None
    created_at: str = ""
    modified_at: str = ""
    due_on: Optional[str] = None
    due_at: Optional[str] = None
    start_on: Optional[str] = None
    start_at: Optional[str] = None
    assignee_gid: str = ""
    assignee_name: str = ""
    project_gids: List[str] = field(default_factory=list)
    memberships: List[Dict[str, Any]] = field(default_factory=list)
    tags: List[Dict[str, Any]] = field(default_factory=list)
    workspace_gid: str = ""
    parent_gid: str = ""
    num_subtasks: int = 0
    permalink_url: str = ""
    resource_type: AsanaResourceType = AsanaResourceType.TASK
    custom_fields: List[Dict[str, Any]] = field(default_factory=list)
    extra: Dict[str, Any] = field(default_factory=dict)


@dataclass
class AsanaProject:
    """Represents an Asana project."""
    gid: str
    name: str = ""
    description: str = ""
    notes: str = ""
    color: str = ""
    archived: bool = False
    public: bool = False
    workspace_gid: str = ""
    team_gid: str = ""
    owner_gid: str = ""
    default_view: str = "list"
    created_at: str = ""
    modified_at: str = ""
    due_on: Optional[str] = None
    start_on: Optional[str] = None
    current_status: Optional[Dict[str, Any]] = None
    permalink_url: str = ""
    extra: Dict[str, Any] = field(default_factory=dict)


@dataclass
class AsanaSection:
    """Represents an Asana section (column in board view)."""
    gid: str
    name: str = ""
    project_gid: str = ""
    created_at: str = ""
    extra: Dict[str, Any] = field(default_factory=dict)


@dataclass
class AsanaWorkspace:
    """Represents an Asana workspace."""
    gid: str
    name: str = ""
    is_organization: bool = False
    email_domains: List[str] = field(default_factory=list)
    extra: Dict[str, Any] = field(default_factory=dict)


@dataclass
class AsanaTeam:
    """Represents an Asana team."""
    gid: str
    name: str = ""
    description: str = ""
    workspace_gid: str = ""
    organization_gid: str = ""
    permalink_url: str = ""
    extra: Dict[str, Any] = field(default_factory=dict)


@dataclass
class AsanaUser:
    """Represents an Asana user."""
    gid: str
    name: str = ""
    email: str = ""
    workspaces: List[Dict[str, Any]] = field(default_factory=list)
    photo: Optional[Dict[str, Any]] = None
    extra: Dict[str, Any] = field(default_factory=dict)


@dataclass
class AsanaAttachment:
    """Represents an Asana attachment."""
    gid: str
    name: str = ""
    resource_type: str = ""
    resource_subtype: str = ""
    parent_gid: str = ""
    parent_name: str = ""
    download_url: str = ""
    view_url: str = ""
    permanent_url: str = ""
    size: int = 0
    created_at: str = ""
    created_by: Dict[str, Any] = field(default_factory=dict)
    extra: Dict[str, Any] = field(default_factory=dict)


@dataclass
class AsanaStory:
    """Represents an Asana story (comment/activity)."""
    gid: str
    resource_type: str = "story"
    resource_subtype: str = "comment_added"
    created_at: str = ""
    created_by: Dict[str, Any] = field(default_factory=dict)
    text: str = ""
    html_text: str = ""
    is_edited: bool = False
    is_pinned: bool = False
    target_gid: str = ""
    target_name: str = ""
    extra: Dict[str, Any] = field(default_factory=dict)


@dataclass
class AsanaTag:
    """Represents an Asana tag."""
    gid: str
    name: str = ""
    color: str = ""
    notes: str = ""
    workspace_gid: str = ""
    permalink_url: str = ""
    extra: Dict[str, Any] = field(default_factory=dict)


class AsanaAdapter(BaseIntegrationAdapter):
    """
    Asana integration adapter for LiuHao AI OS.

    Supports:
    - Tasks (CRUD)
    - Projects and sections
    - Workspaces and teams
    - Users
    - Attachments and comments (stories)
    - Tags
    - Due dates and dependencies
    - Webhook verification
    """

    def __init__(self, config: IntegrationConfig):
        if config.auth_type == AuthType.NONE:
            config.auth_type = AuthType.BEARER_TOKEN
        if not config.base_url:
            config.base_url = "https://app.asana.com/api/1.0"
        super().__init__(config)
        self._user_gid: Optional[str] = None
        self._user_name: Optional[str] = None
        self._user_email: Optional[str] = None
        self._workspace_gid: Optional[str] = None
        self._cached_tasks: Dict[str, AsanaTask] = {}
        self._cached_projects: Dict[str, AsanaProject] = {}

    # ------------------------------------------------------------------
    # Base interface
    # ------------------------------------------------------------------

    def connect(self) -> bool:
        self._status = ConnectionStatus.CONNECTING
        self._logger.info("Connecting to Asana API...")

        try:
            result = self._api_call("users/me")
            user_data = result.get("data", {})
            self._user_gid = user_data.get("gid", "")
            self._user_name = user_data.get("name", "")
            self._user_email = user_data.get("email", "")
            workspaces = user_data.get("workspaces", [])
            if workspaces:
                self._workspace_gid = workspaces[0].get("gid", "")
            self._status = ConnectionStatus.CONNECTED
            self._connected_at = time.time()
            self._logger.info(
                "Connected to Asana as %s (%s)",
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
        self._user_gid = None
        self._user_name = None
        self._user_email = None
        self._workspace_gid = None
        self._cached_tasks = {}
        self._cached_projects = {}
        self._session = None
        self._logger.info("Disconnected from Asana")
        return True

    def is_connected(self) -> bool:
        return self._status == ConnectionStatus.CONNECTED

    def get_status(self) -> IntegrationStatus:
        return self._build_status({
            "user_name": self._user_name,
            "user_gid": self._user_gid,
            "user_email": self._user_email,
            "workspace_gid": self._workspace_gid,
            "cached_tasks": len(self._cached_tasks),
            "cached_projects": len(self._cached_projects),
        })

    def test_connection(self) -> bool:
        try:
            if not self.is_connected():
                return False
            result = self._api_call("users/me")
            return "data" in result and "gid" in result["data"]
        except Exception:
            return False

    # ------------------------------------------------------------------
    # Tasks
    # ------------------------------------------------------------------

    def create_task(self, name: str,
                    workspace_gid: Optional[str] = None,
                    project_gid: Optional[str] = None,
                    section_gid: Optional[str] = None,
                    notes: str = "",
                    assignee_gid: str = "",
                    due_on: Optional[str] = None,
                    due_at: Optional[str] = None,
                    start_on: Optional[str] = None,
                    tags: Optional[List[str]] = None,
                    **kwargs: Any) -> AsanaTask:
        ws_gid = workspace_gid or self._workspace_gid
        if not ws_gid:
            raise self._handle_error("Workspace GID is required")

        payload: Dict[str, Any] = {
            "data": {
                "name": name,
                "workspace": ws_gid,
            }
        }
        if project_gid:
            payload["data"]["projects"] = [project_gid]
        if section_gid:
            payload["data"]["memberships"] = [{"section": section_gid}]
        if notes:
            payload["data"]["notes"] = notes
        if assignee_gid:
            payload["data"]["assignee"] = assignee_gid
        if due_on:
            payload["data"]["due_on"] = due_on
        if due_at:
            payload["data"]["due_at"] = due_at
        if start_on:
            payload["data"]["start_on"] = start_on
        if tags:
            payload["data"]["tags"] = tags
        payload["data"].update(kwargs)

        result = self._api_call("tasks", data=payload, http_method="POST")
        task = self._parse_task(result.get("data", {}))
        if task.gid:
            self._cached_tasks[task.gid] = task
        return task

    def get_task(self, task_gid: str,
                 opt_fields: Optional[str] = None) -> AsanaTask:
        params = {}
        if opt_fields:
            params["opt_fields"] = opt_fields
        result = self._api_call(f"tasks/{task_gid}", params=params)
        task = self._parse_task(result.get("data", {}))
        if task.gid:
            self._cached_tasks[task.gid] = task
        return task

    def update_task(self, task_gid: str,
                    name: Optional[str] = None,
                    notes: Optional[str] = None,
                    completed: Optional[bool] = None,
                    assignee_gid: Optional[str] = None,
                    due_on: Optional[str] = None,
                    **kwargs: Any) -> AsanaTask:
        payload: Dict[str, Any] = {"data": {}}
        if name is not None:
            payload["data"]["name"] = name
        if notes is not None:
            payload["data"]["notes"] = notes
        if completed is not None:
            payload["data"]["completed"] = completed
        if assignee_gid is not None:
            payload["data"]["assignee"] = assignee_gid
        if due_on is not None:
            payload["data"]["due_on"] = due_on
        payload["data"].update(kwargs)

        result = self._api_call(
            f"tasks/{task_gid}", data=payload, http_method="PUT"
        )
        task = self._parse_task(result.get("data", {}))
        if task.gid:
            self._cached_tasks[task.gid] = task
        return task

    def delete_task(self, task_gid: str) -> bool:
        self._api_call(f"tasks/{task_gid}", http_method="DELETE")
        if task_gid in self._cached_tasks:
            del self._cached_tasks[task_gid]
        return True

    def list_tasks(self, project_gid: Optional[str] = None,
                   section_gid: Optional[str] = None,
                   workspace_gid: Optional[str] = None,
                   assignee_gid: Optional[str] = None,
                   completed_since: Optional[str] = None,
                   modified_since: Optional[str] = None,
                   limit: int = 100,
                   **kwargs: Any) -> Dict[str, Any]:
        params: Dict[str, Any] = {"limit": limit}
        if completed_since:
            params["completed_since"] = completed_since
        if modified_since:
            params["modified_since"] = modified_since
        if assignee_gid:
            params["assignee"] = assignee_gid
        params.update(kwargs)

        if project_gid:
            path = f"projects/{project_gid}/tasks"
        elif section_gid:
            path = f"sections/{section_gid}/tasks"
        elif workspace_gid:
            path = f"workspaces/{workspace_gid}/tasks"
        else:
            raise self._handle_error(
                "project_gid, section_gid, or workspace_gid is required"
            )

        result = self._api_call(path, params=params)
        tasks_data = result.get("data", [])
        tasks = [self._parse_task(t) for t in tasks_data]
        for task in tasks:
            if task.gid:
                self._cached_tasks[task.gid] = task

        return {
            "tasks": tasks,
            "next_page": result.get("next_page"),
        }

    def _parse_task(self, data: Dict[str, Any]) -> AsanaTask:
        projects = data.get("projects", [])
        assignee = data.get("assignee")
        return AsanaTask(
            gid=data.get("gid", ""),
            name=data.get("name", ""),
            notes=data.get("notes", ""),
            completed=data.get("completed", False),
            completed_at=data.get("completed_at"),
            created_at=data.get("created_at", ""),
            modified_at=data.get("modified_at", ""),
            due_on=data.get("due_on"),
            due_at=data.get("due_at"),
            start_on=data.get("start_on"),
            start_at=data.get("start_at"),
            assignee_gid=assignee.get("gid", "") if assignee else "",
            assignee_name=assignee.get("name", "") if assignee else "",
            project_gids=[p.get("gid", "") for p in projects],
            memberships=data.get("memberships", []),
            tags=data.get("tags", []),
            workspace_gid=data.get("workspace", {}).get("gid", ""),
            parent_gid=data.get("parent", {}).get("gid", "") if data.get("parent") else "",
            num_subtasks=data.get("num_subtasks", 0),
            permalink_url=data.get("permalink_url", ""),
            custom_fields=data.get("custom_fields", []),
            extra=data,
        )

    # ------------------------------------------------------------------
    # Projects
    # ------------------------------------------------------------------

    def create_project(self, name: str,
                       workspace_gid: Optional[str] = None,
                       team_gid: str = "",
                       description: str = "",
                       public: bool = False,
                       **kwargs: Any) -> AsanaProject:
        ws_gid = workspace_gid or self._workspace_gid
        if not ws_gid:
            raise self._handle_error("Workspace GID is required")

        payload: Dict[str, Any] = {
            "data": {
                "name": name,
                "workspace": ws_gid,
                "public": public,
            }
        }
        if team_gid:
            payload["data"]["team"] = team_gid
        if description:
            payload["data"]["notes"] = description
        payload["data"].update(kwargs)

        result = self._api_call("projects", data=payload, http_method="POST")
        project = self._parse_project(result.get("data", {}))
        if project.gid:
            self._cached_projects[project.gid] = project
        return project

    def get_project(self, project_gid: str) -> AsanaProject:
        result = self._api_call(f"projects/{project_gid}")
        project = self._parse_project(result.get("data", {}))
        if project.gid:
            self._cached_projects[project.gid] = project
        return project

    def update_project(self, project_gid: str,
                       name: Optional[str] = None,
                       notes: Optional[str] = None,
                       **kwargs: Any) -> AsanaProject:
        payload: Dict[str, Any] = {"data": {}}
        if name is not None:
            payload["data"]["name"] = name
        if notes is not None:
            payload["data"]["notes"] = notes
        payload["data"].update(kwargs)

        result = self._api_call(
            f"projects/{project_gid}", data=payload, http_method="PUT"
        )
        project = self._parse_project(result.get("data", {}))
        if project.gid:
            self._cached_projects[project.gid] = project
        return project

    def delete_project(self, project_gid: str) -> bool:
        self._api_call(f"projects/{project_gid}", http_method="DELETE")
        if project_gid in self._cached_projects:
            del self._cached_projects[project_gid]
        return True

    def list_projects(self, workspace_gid: Optional[str] = None,
                      team_gid: Optional[str] = None,
                      archived: bool = False,
                      limit: int = 100) -> List[AsanaProject]:
        params: Dict[str, Any] = {"limit": limit, "archived": str(archived).lower()}

        if team_gid:
            path = f"teams/{team_gid}/projects"
        elif workspace_gid or self._workspace_gid:
            ws_gid = workspace_gid or self._workspace_gid
            path = f"workspaces/{ws_gid}/projects"
        else:
            raise self._handle_error("workspace_gid or team_gid is required")

        result = self._api_call(path, params=params)
        projects_data = result.get("data", [])
        return [self._parse_project(p) for p in projects_data]

    def _parse_project(self, data: Dict[str, Any]) -> AsanaProject:
        workspace = data.get("workspace", {})
        team = data.get("team", {})
        owner = data.get("owner", {})
        return AsanaProject(
            gid=data.get("gid", ""),
            name=data.get("name", ""),
            description=data.get("description", ""),
            notes=data.get("notes", ""),
            color=data.get("color", ""),
            archived=data.get("archived", False),
            public=data.get("public", False),
            workspace_gid=workspace.get("gid", "") if workspace else "",
            team_gid=team.get("gid", "") if team else "",
            owner_gid=owner.get("gid", "") if owner else "",
            default_view=data.get("default_view", "list"),
            created_at=data.get("created_at", ""),
            modified_at=data.get("modified_at", ""),
            due_on=data.get("due_on"),
            start_on=data.get("start_on"),
            current_status=data.get("current_status"),
            permalink_url=data.get("permalink_url", ""),
            extra=data,
        )

    # ------------------------------------------------------------------
    # Sections
    # ------------------------------------------------------------------

    def create_section(self, project_gid: str,
                       name: str,
                       insert_before: str = "",
                       insert_after: str = "") -> AsanaSection:
        payload: Dict[str, Any] = {"data": {"name": name}}
        if insert_before:
            payload["data"]["insert_before"] = insert_before
        if insert_after:
            payload["data"]["insert_after"] = insert_after

        result = self._api_call(
            f"projects/{project_gid}/sections",
            data=payload,
            http_method="POST",
        )
        data = result.get("data", {})
        return AsanaSection(
            gid=data.get("gid", ""),
            name=data.get("name", ""),
            project_gid=project_gid,
            created_at=data.get("created_at", ""),
            extra=data,
        )

    def list_sections(self, project_gid: str) -> List[AsanaSection]:
        result = self._api_call(f"projects/{project_gid}/sections")
        sections_data = result.get("data", [])
        return [
            AsanaSection(
                gid=s.get("gid", ""),
                name=s.get("name", ""),
                project_gid=project_gid,
                created_at=s.get("created_at", ""),
                extra=s,
            )
            for s in sections_data
        ]

    def delete_section(self, section_gid: str) -> bool:
        self._api_call(f"sections/{section_gid}", http_method="DELETE")
        return True

    # ------------------------------------------------------------------
    # Workspaces and teams
    # ------------------------------------------------------------------

    def list_workspaces(self) -> List[AsanaWorkspace]:
        result = self._api_call("workspaces")
        workspaces_data = result.get("data", [])
        return [
            AsanaWorkspace(
                gid=w.get("gid", ""),
                name=w.get("name", ""),
                is_organization=w.get("is_organization", False),
                email_domains=w.get("email_domains", []),
                extra=w,
            )
            for w in workspaces_data
        ]

    def list_teams(self, workspace_gid: str,
                   user_gid: Optional[str] = None) -> List[AsanaTeam]:
        if user_gid:
            path = f"users/{user_gid}/teams"
            params = {"organization": workspace_gid}
        else:
            path = f"organizations/{workspace_gid}/teams"
            params = {}

        result = self._api_call(path, params=params)
        teams_data = result.get("data", [])
        return [
            AsanaTeam(
                gid=t.get("gid", ""),
                name=t.get("name", ""),
                description=t.get("description", ""),
                workspace_gid=workspace_gid,
                organization_gid=workspace_gid,
                permalink_url=t.get("permalink_url", ""),
                extra=t,
            )
            for t in teams_data
        ]

    # ------------------------------------------------------------------
    # Users
    # ------------------------------------------------------------------

    def get_user(self, user_gid: str = "me") -> AsanaUser:
        result = self._api_call(f"users/{user_gid}")
        data = result.get("data", {})
        return AsanaUser(
            gid=data.get("gid", ""),
            name=data.get("name", ""),
            email=data.get("email", ""),
            workspaces=data.get("workspaces", []),
            photo=data.get("photo"),
            extra=data,
        )

    def list_users(self, workspace_gid: Optional[str] = None,
                   limit: int = 100) -> List[AsanaUser]:
        params: Dict[str, Any] = {"limit": limit}
        ws_gid = workspace_gid or self._workspace_gid
        if ws_gid:
            params["workspace"] = ws_gid

        result = self._api_call("users", params=params)
        users_data = result.get("data", [])
        return [
            AsanaUser(
                gid=u.get("gid", ""),
                name=u.get("name", ""),
                email=u.get("email", ""),
                photo=u.get("photo"),
                extra=u,
            )
            for u in users_data
        ]

    # ------------------------------------------------------------------
    # Attachments and comments (stories)
    # ------------------------------------------------------------------

    def list_attachments(self, parent_gid: str) -> List[AsanaAttachment]:
        result = self._api_call(f"tasks/{parent_gid}/attachments")
        attachments_data = result.get("data", [])
        return [
            AsanaAttachment(
                gid=a.get("gid", ""),
                name=a.get("name", ""),
                resource_type=a.get("resource_type", ""),
                resource_subtype=a.get("resource_subtype", ""),
                parent_gid=parent_gid,
                download_url=a.get("download_url", ""),
                view_url=a.get("view_url", ""),
                permanent_url=a.get("permanent_url", ""),
                size=a.get("size", 0),
                created_at=a.get("created_at", ""),
                created_by=a.get("created_by", {}),
                extra=a,
            )
            for a in attachments_data
        ]

    def get_attachment(self, attachment_gid: str) -> AsanaAttachment:
        result = self._api_call(f"attachments/{attachment_gid}")
        data = result.get("data", {})
        parent = data.get("parent", {})
        return AsanaAttachment(
            gid=data.get("gid", ""),
            name=data.get("name", ""),
            resource_type=data.get("resource_type", ""),
            resource_subtype=data.get("resource_subtype", ""),
            parent_gid=parent.get("gid", "") if parent else "",
            parent_name=parent.get("name", "") if parent else "",
            download_url=data.get("download_url", ""),
            view_url=data.get("view_url", ""),
            permanent_url=data.get("permanent_url", ""),
            size=data.get("size", 0),
            created_at=data.get("created_at", ""),
            created_by=data.get("created_by", {}),
            extra=data,
        )

    def create_comment(self, task_gid: str,
                       text: str,
                       is_pinned: bool = False,
                       **kwargs: Any) -> AsanaStory:
        payload: Dict[str, Any] = {
            "data": {
                "text": text,
                "is_pinned": is_pinned,
            }
        }
        payload["data"].update(kwargs)

        result = self._api_call(
            f"tasks/{task_gid}/stories",
            data=payload,
            http_method="POST",
        )
        data = result.get("data", {})
        target = data.get("target", {})
        created_by = data.get("created_by", {})
        return AsanaStory(
            gid=data.get("gid", ""),
            resource_type=data.get("resource_type", "story"),
            resource_subtype=data.get("resource_subtype", "comment_added"),
            created_at=data.get("created_at", ""),
            created_by=created_by,
            text=data.get("text", ""),
            html_text=data.get("html_text", ""),
            is_edited=data.get("is_edited", False),
            is_pinned=data.get("is_pinned", False),
            target_gid=target.get("gid", task_gid) if target else task_gid,
            target_name=target.get("name", "") if target else "",
            extra=data,
        )

    def list_stories(self, task_gid: str,
                     limit: int = 100) -> List[AsanaStory]:
        params = {"limit": limit}
        result = self._api_call(f"tasks/{task_gid}/stories", params=params)
        stories_data = result.get("data", [])
        return [
            AsanaStory(
                gid=s.get("gid", ""),
                resource_type=s.get("resource_type", "story"),
                resource_subtype=s.get("resource_subtype", ""),
                created_at=s.get("created_at", ""),
                created_by=s.get("created_by", {}),
                text=s.get("text", ""),
                html_text=s.get("html_text", ""),
                is_edited=s.get("is_edited", False),
                is_pinned=s.get("is_pinned", False),
                target_gid=task_gid,
                extra=s,
            )
            for s in stories_data
        ]

    # ------------------------------------------------------------------
    # Tags
    # ------------------------------------------------------------------

    def create_tag(self, name: str,
                   workspace_gid: Optional[str] = None,
                   color: str = "",
                   notes: str = "") -> AsanaTag:
        ws_gid = workspace_gid or self._workspace_gid
        if not ws_gid:
            raise self._handle_error("Workspace GID is required")

        payload: Dict[str, Any] = {
            "data": {
                "name": name,
                "workspace": ws_gid,
            }
        }
        if color:
            payload["data"]["color"] = color
        if notes:
            payload["data"]["notes"] = notes

        result = self._api_call("tags", data=payload, http_method="POST")
        data = result.get("data", {})
        return AsanaTag(
            gid=data.get("gid", ""),
            name=data.get("name", ""),
            color=data.get("color", ""),
            notes=data.get("notes", ""),
            workspace_gid=ws_gid,
            permalink_url=data.get("permalink_url", ""),
            extra=data,
        )

    def list_tags(self, workspace_gid: Optional[str] = None) -> List[AsanaTag]:
        ws_gid = workspace_gid or self._workspace_gid
        if not ws_gid:
            raise self._handle_error("Workspace GID is required")

        result = self._api_call(f"workspaces/{ws_gid}/tags")
        tags_data = result.get("data", [])
        return [
            AsanaTag(
                gid=t.get("gid", ""),
                name=t.get("name", ""),
                color=t.get("color", ""),
                notes=t.get("notes", ""),
                workspace_gid=ws_gid,
                permalink_url=t.get("permalink_url", ""),
                extra=t,
            )
            for t in tags_data
        ]

    def add_tag_to_task(self, task_gid: str, tag_gid: str) -> bool:
        self._api_call(
            f"tasks/{task_gid}/addTag",
            data={"data": {"tag": tag_gid}},
            http_method="POST",
        )
        return True

    def remove_tag_from_task(self, task_gid: str, tag_gid: str) -> bool:
        self._api_call(
            f"tasks/{task_gid}/removeTag",
            data={"data": {"tag": tag_gid}},
            http_method="POST",
        )
        return True

    # ------------------------------------------------------------------
    # Dependencies
    # ------------------------------------------------------------------

    def add_dependency(self, task_gid: str,
                       depends_on_gid: str) -> bool:
        """
        Make a task depend on another task.

        Args:
            task_gid: The task that depends on another.
            depends_on_gid: The task that must be completed first.

        Returns:
            True if dependency was added.
        """
        self._api_call(
            f"tasks/{task_gid}/addDependencies",
            data={"data": {"depends_on": [depends_on_gid]}},
            http_method="POST",
        )
        return True

    def remove_dependency(self, task_gid: str,
                          depends_on_gid: str) -> bool:
        """
        Remove a task dependency.

        Args:
            task_gid: The task that depends on another.
            depends_on_gid: The task dependency to remove.

        Returns:
            True if dependency was removed.
        """
        self._api_call(
            f"tasks/{task_gid}/removeDependencies",
            data={"data": {"depends_on": [depends_on_gid]}},
            http_method="POST",
        )
        return True

    # ------------------------------------------------------------------
    # Webhook verification
    # ------------------------------------------------------------------

    def verify_webhook(self, request_body: str,
                       x_hook_signature: str,
                       client_secret: str) -> bool:
        """
        Verify an Asana webhook signature.

        Args:
            request_body: Raw request body.
            x_hook_signature: X-Hook-Signature header value.
            client_secret: Webhook client secret.

        Returns:
            True if the webhook is verified.
        """
        expected = hmac.new(
            client_secret.encode("utf-8"),
            request_body.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()
        return hmac.compare_digest(x_hook_signature, expected)

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
            "Accept": "application/json",
            "Content-Type": "application/json",
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
                    "Rate limited by Asana",
                    retry_after=60,
                    adapter_name=self.config.name,
                )
            if e.code in (401, 403):
                raise self._handle_error(
                    "Authentication failed: invalid Asana token",
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
            f"<AsanaAdapter name={self.config.name} "
            f"status={self._status.value} "
            f"user={self._user_name or 'unknown'}>"
        )
