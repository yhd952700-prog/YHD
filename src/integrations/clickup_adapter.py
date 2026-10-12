"""
ClickUp Integration Adapter for LiuHao AI OS.

Provides integration with ClickUp task management for tasks, lists, folders,
spaces, workspaces, comments, attachments, time tracking, checklists,
and custom fields.
"""

from __future__ import annotations

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


class ClickUpTaskPriority(int, Enum):
    """Task priority levels."""
    URGENT = 1
    HIGH = 2
    NORMAL = 3
    LOW = 4


class ClickUpTaskStatus(str, Enum):
    """Common task status types."""
    OPEN = "open"
    IN_PROGRESS = "in progress"
    REVIEW = "review"
    DONE = "done"
    CLOSED = "closed"


class ClickUpDependencyType(str, Enum):
    """Task dependency types."""
    WAITING_ON = "waiting_on"
    BLOCKING = "blocking"


@dataclass
class ClickUpTask:
    """Represents a ClickUp task."""
    id: str = ""
    name: str = ""
    description: str = ""
    text_content: str = ""
    status: str = ""
    status_color: str = ""
    priority: Optional[ClickUpTaskPriority] = None
    list_id: str = ""
    list_name: str = ""
    folder_id: str = ""
    folder_name: str = ""
    space_id: str = ""
    assignees: List[Dict[str, Any]] = field(default_factory=list)
    tags: List[str] = field(default_factory=list)
    due_date: Optional[int] = None
    start_date: Optional[int] = None
    date_created: Optional[int] = None
    date_updated: Optional[int] = None
    date_closed: Optional[int] = None
    creator_id: int = 0
    url: str = ""
    time_estimate: int = 0
    time_spent: int = 0
    custom_fields: List[Dict[str, Any]] = field(default_factory=list)
    extra: Dict[str, Any] = field(default_factory=dict)


@dataclass
class ClickUpList:
    """Represents a ClickUp list."""
    id: str
    name: str = ""
    folder_id: str = ""
    folder_name: str = ""
    space_id: str = ""
    orderindex: int = 0
    statuses: List[Dict[str, Any]] = field(default_factory=list)
    content: str = ""
    priority: Optional[str] = None
    extra: Dict[str, Any] = field(default_factory=dict)


@dataclass
class ClickUpFolder:
    """Represents a ClickUp folder."""
    id: str
    name: str = ""
    space_id: str = ""
    orderindex: int = 0
    override_statuses: bool = False
    statuses: List[Dict[str, Any]] = field(default_factory=list)
    lists: List[ClickUpList] = field(default_factory=list)
    extra: Dict[str, Any] = field(default_factory=dict)


@dataclass
class ClickUpSpace:
    """Represents a ClickUp space."""
    id: str
    name: str = ""
    color: str = ""
    avatar: str = ""
    admin: bool = False
    archived: bool = False
    statuses: List[Dict[str, Any]] = field(default_factory=list)
    features: Dict[str, Any] = field(default_factory=dict)
    extra: Dict[str, Any] = field(default_factory=dict)


@dataclass
class ClickUpWorkspace:
    """Represents a ClickUp workspace (team)."""
    id: str
    name: str = ""
    color: str = ""
    avatar: str = ""
    members: List[Dict[str, Any]] = field(default_factory=list)
    roles: List[Dict[str, Any]] = field(default_factory=list)
    extra: Dict[str, Any] = field(default_factory=dict)


@dataclass
class ClickUpComment:
    """Represents a ClickUp comment."""
    id: str
    comment_text: str = ""
    task_id: str = ""
    user_id: int = 0
    user_name: str = ""
    user_email: str = ""
    date: Optional[int] = None
    reactions: List[Dict[str, Any]] = field(default_factory=list)
    extra: Dict[str, Any] = field(default_factory=dict)


@dataclass
class ClickUpTimeEntry:
    """Represents a ClickUp time entry."""
    id: str
    task_id: str = ""
    user_id: int = 0
    billable: bool = False
    start: Optional[int] = None
    end: Optional[int] = None
    duration: int = 0
    description: str = ""
    tags: List[str] = field(default_factory=list)
    extra: Dict[str, Any] = field(default_factory=dict)


@dataclass
class ClickUpChecklist:
    """Represents a ClickUp checklist."""
    id: str
    name: str = ""
    task_id: str = ""
    orderindex: int = 0
    items: List[Dict[str, Any]] = field(default_factory=list)
    resolved: int = 0
    unresolved: int = 0
    extra: Dict[str, Any] = field(default_factory=dict)


@dataclass
class ClickUpCustomField:
    """Represents a ClickUp custom field definition."""
    id: str
    name: str = ""
    type: str = ""
    type_config: Dict[str, Any] = field(default_factory=dict)
    date_created: Optional[int] = None
    hide_from_guests: bool = False
    required: bool = False
    extra: Dict[str, Any] = field(default_factory=dict)


class ClickUpAdapter(BaseIntegrationAdapter):
    """
    ClickUp integration adapter for LiuHao AI OS.

    Supports:
    - Tasks (CRUD)
    - Lists and folders
    - Spaces and workspaces
    - Comments and attachments
    - Time tracking
    - Checklists
    - Custom fields
    """

    def __init__(self, config: IntegrationConfig):
        """
        Initialize the ClickUp adapter.

        Args:
            config: Integration configuration. Must have api_key set
                    for the ClickUp personal API token.
        """
        if config.auth_type == AuthType.NONE:
            config.auth_type = AuthType.API_KEY
        if not config.base_url:
            config.base_url = "https://api.clickup.com/api/v2"
        super().__init__(config)
        self._user_id: Optional[int] = None
        self._username: Optional[str] = None
        self._workspace_id: Optional[str] = None
        self._cached_tasks: Dict[str, ClickUpTask] = {}
        self._cached_lists: Dict[str, ClickUpList] = {}

    # ------------------------------------------------------------------
    # Base interface implementation
    # ------------------------------------------------------------------

    def connect(self) -> bool:
        self._status = ConnectionStatus.CONNECTING
        self._logger.info("Connecting to ClickUp API...")

        try:
            result = self._api_call("user")
            user_data = result.get("user", {})
            self._user_id = user_data.get("id")
            self._username = user_data.get("username", "")

            teams = result.get("teams", [])
            if teams:
                self._workspace_id = teams[0].get("id", "")

            self._status = ConnectionStatus.CONNECTED
            self._connected_at = time.time()
            self._logger.info(
                "Connected to ClickUp as %s (workspace: %s)",
                self._username, self._workspace_id,
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
        self._username = None
        self._workspace_id = None
        self._cached_tasks = {}
        self._cached_lists = {}
        self._session = None
        self._logger.info("Disconnected from ClickUp")
        return True

    def is_connected(self) -> bool:
        return self._status == ConnectionStatus.CONNECTED

    def get_status(self) -> IntegrationStatus:
        return self._build_status({
            "username": self._username,
            "user_id": self._user_id,
            "workspace_id": self._workspace_id,
            "cached_tasks": len(self._cached_tasks),
            "cached_lists": len(self._cached_lists),
        })

    def test_connection(self) -> bool:
        try:
            if not self.is_connected():
                return False
            result = self._api_call("user")
            return "user" in result
        except Exception:
            return False

    # ------------------------------------------------------------------
    # Tasks (CRUD)
    # ------------------------------------------------------------------

    def create_task(self, list_id: str,
                    name: str,
                    description: str = "",
                    priority: Optional[ClickUpTaskPriority] = None,
                    assignees: Optional[List[int]] = None,
                    tags: Optional[List[str]] = None,
                    due_date: Optional[int] = None,
                    start_date: Optional[int] = None,
                    status: str = "",
                    **kwargs: Any) -> ClickUpTask:
        payload: Dict[str, Any] = {"name": name}
        if description:
            payload["description"] = description
        if priority is not None:
            payload["priority"] = priority.value
        if assignees:
            payload["assignees"] = assignees
        if tags:
            payload["tags"] = tags
        if due_date is not None:
            payload["due_date"] = due_date
        if start_date is not None:
            payload["start_date"] = start_date
        if status:
            payload["status"] = status
        payload.update(kwargs)

        result = self._api_call(
            f"list/{list_id}/task", data=payload, http_method="POST"
        )
        task = self._parse_task(result)
        if task.id:
            self._cached_tasks[task.id] = task
        return task

    def get_task(self, task_id: str,
                 include_subtasks: bool = False,
                 include_markdown_description: bool = False
                 ) -> ClickUpTask:
        params = {
            "include_subtasks": str(include_subtasks).lower(),
            "include_markdown_description": str(include_markdown_description).lower(),
        }
        result = self._api_call(f"task/{task_id}", params=params)
        task = self._parse_task(result)
        if task.id:
            self._cached_tasks[task.id] = task
        return task

    def update_task(self, task_id: str,
                    name: Optional[str] = None,
                    description: Optional[str] = None,
                    status: Optional[str] = None,
                    priority: Optional[ClickUpTaskPriority] = None,
                    **kwargs: Any) -> ClickUpTask:
        payload: Dict[str, Any] = {}
        if name is not None:
            payload["name"] = name
        if description is not None:
            payload["description"] = description
        if status is not None:
            payload["status"] = status
        if priority is not None:
            payload["priority"] = priority.value
        payload.update(kwargs)

        result = self._api_call(
            f"task/{task_id}", data=payload, http_method="PUT"
        )
        task = self._parse_task(result)
        if task.id:
            self._cached_tasks[task.id] = task
        return task

    def delete_task(self, task_id: str) -> bool:
        self._api_call(f"task/{task_id}", http_method="DELETE")
        if task_id in self._cached_tasks:
            del self._cached_tasks[task_id]
        return True

    def list_tasks(self, list_id: str,
                   archived: bool = False,
                   page: int = 0,
                   order_by: str = "created",
                   reverse: bool = True,
                   subtasks: bool = False,
                   statuses: Optional[List[str]] = None,
                   **kwargs: Any) -> Dict[str, Any]:
        params: Dict[str, Any] = {
            "archived": str(archived).lower(),
            "page": page,
            "order_by": order_by,
            "reverse": str(reverse).lower(),
            "subtasks": str(subtasks).lower(),
        }
        if statuses:
            for i, s in enumerate(statuses):
                params[f"statuses[{i}]"] = s
        params.update(kwargs)

        result = self._api_call(f"list/{list_id}/task", params=params)
        tasks_data = result.get("tasks", [])
        tasks = [self._parse_task(t) for t in tasks_data]
        for task in tasks:
            if task.id:
                self._cached_tasks[task.id] = task

        return {
            "tasks": tasks,
            "last_page": result.get("last_page", True),
            "page": result.get("page", 0),
        }

    def _parse_task(self, data: Dict[str, Any]) -> ClickUpTask:
        status_data = data.get("status", {})
        list_data = data.get("list", {})
        folder_data = data.get("folder", {})
        space_data = data.get("space", {})
        priority_data = data.get("priority")
        priority = None
        if priority_data:
            try:
                priority = ClickUpTaskPriority(int(priority_data.get("id", 3)))
            except (ValueError, TypeError):
                priority = ClickUpTaskPriority.NORMAL

        return ClickUpTask(
            id=data.get("id", ""),
            name=data.get("name", ""),
            description=data.get("description", ""),
            text_content=data.get("text_content", ""),
            status=status_data.get("status", ""),
            status_color=status_data.get("color", ""),
            priority=priority,
            list_id=list_data.get("id", ""),
            list_name=list_data.get("name", ""),
            folder_id=folder_data.get("id", ""),
            folder_name=folder_data.get("name", ""),
            space_id=space_data.get("id", ""),
            assignees=data.get("assignees", []),
            tags=[t.get("name", "") for t in data.get("tags", [])],
            due_date=int(data.get("due_date")) if data.get("due_date") else None,
            start_date=int(data.get("start_date")) if data.get("start_date") else None,
            date_created=int(data.get("date_created")) if data.get("date_created") else None,
            date_updated=int(data.get("date_updated")) if data.get("date_updated") else None,
            date_closed=int(data.get("date_closed")) if data.get("date_closed") else None,
            creator_id=data.get("creator", {}).get("id", 0) if data.get("creator") else 0,
            url=data.get("url", ""),
            time_estimate=data.get("time_estimate", 0) or 0,
            time_spent=data.get("time_spent", 0) or 0,
            custom_fields=data.get("custom_fields", []),
            extra=data,
        )

    # ------------------------------------------------------------------
    # Lists and folders
    # ------------------------------------------------------------------

    def create_list(self, folder_id: str,
                    name: str,
                    content: str = "",
                    **kwargs: Any) -> ClickUpList:
        payload: Dict[str, Any] = {"name": name}
        if content:
            payload["content"] = content
        payload.update(kwargs)

        result = self._api_call(
            f"folder/{folder_id}/list", data=payload, http_method="POST"
        )
        lst = self._parse_list(result)
        if lst.id:
            self._cached_lists[lst.id] = lst
        return lst

    def get_list(self, list_id: str) -> ClickUpList:
        result = self._api_call(f"list/{list_id}")
        lst = self._parse_list(result)
        if lst.id:
            self._cached_lists[lst.id] = lst
        return lst

    def update_list(self, list_id: str,
                    name: Optional[str] = None,
                    **kwargs: Any) -> ClickUpList:
        payload: Dict[str, Any] = {}
        if name is not None:
            payload["name"] = name
        payload.update(kwargs)

        result = self._api_call(
            f"list/{list_id}", data=payload, http_method="PUT"
        )
        lst = self._parse_list(result)
        if lst.id:
            self._cached_lists[lst.id] = lst
        return lst

    def delete_list(self, list_id: str) -> bool:
        self._api_call(f"list/{list_id}", http_method="DELETE")
        if list_id in self._cached_lists:
            del self._cached_lists[list_id]
        return True

    def _parse_list(self, data: Dict[str, Any]) -> ClickUpList:
        return ClickUpList(
            id=data.get("id", ""),
            name=data.get("name", ""),
            folder_id=data.get("folder", {}).get("id", ""),
            folder_name=data.get("folder", {}).get("name", ""),
            space_id=data.get("space", {}).get("id", ""),
            orderindex=int(data.get("orderindex", 0)) if data.get("orderindex") else 0,
            statuses=data.get("statuses", []),
            content=data.get("content", ""),
            extra=data,
        )

    def create_folder(self, space_id: str,
                      name: str,
                      **kwargs: Any) -> ClickUpFolder:
        payload: Dict[str, Any] = {"name": name}
        payload.update(kwargs)

        result = self._api_call(
            f"space/{space_id}/folder", data=payload, http_method="POST"
        )
        return self._parse_folder(result)

    def get_folder(self, folder_id: str) -> ClickUpFolder:
        result = self._api_call(f"folder/{folder_id}")
        return self._parse_folder(result)

    def update_folder(self, folder_id: str,
                      name: Optional[str] = None,
                      **kwargs: Any) -> ClickUpFolder:
        payload: Dict[str, Any] = {}
        if name is not None:
            payload["name"] = name
        payload.update(kwargs)

        result = self._api_call(
            f"folder/{folder_id}", data=payload, http_method="PUT"
        )
        return self._parse_folder(result)

    def delete_folder(self, folder_id: str) -> bool:
        self._api_call(f"folder/{folder_id}", http_method="DELETE")
        return True

    def list_folders(self, space_id: str,
                     archived: bool = False) -> List[ClickUpFolder]:
        params = {"archived": str(archived).lower()}
        result = self._api_call(f"space/{space_id}/folder", params=params)
        folders_data = result.get("folders", [])
        return [self._parse_folder(f) for f in folders_data]

    def _parse_folder(self, data: Dict[str, Any]) -> ClickUpFolder:
        lists_data = data.get("lists", [])
        return ClickUpFolder(
            id=data.get("id", ""),
            name=data.get("name", ""),
            space_id=data.get("space", {}).get("id", ""),
            orderindex=int(data.get("orderindex", 0)) if data.get("orderindex") else 0,
            override_statuses=data.get("override_statuses", False),
            statuses=data.get("statuses", []),
            lists=[self._parse_list(l) for l in lists_data],
            extra=data,
        )

    # ------------------------------------------------------------------
    # Spaces and workspaces
    # ------------------------------------------------------------------

    def create_space(self, workspace_id: str,
                     name: str,
                     multiple_assignees: bool = True,
                     features: Optional[Dict[str, Any]] = None,
                     **kwargs: Any) -> ClickUpSpace:
        payload: Dict[str, Any] = {
            "name": name,
            "multiple_assignees": multiple_assignees,
        }
        if features:
            payload["features"] = features
        payload.update(kwargs)

        result = self._api_call(
            f"team/{workspace_id}/space", data=payload, http_method="POST"
        )
        return self._parse_space(result)

    def get_space(self, space_id: str) -> ClickUpSpace:
        result = self._api_call(f"space/{space_id}")
        return self._parse_space(result)

    def update_space(self, space_id: str,
                     name: Optional[str] = None,
                     **kwargs: Any) -> ClickUpSpace:
        payload: Dict[str, Any] = {}
        if name is not None:
            payload["name"] = name
        payload.update(kwargs)

        result = self._api_call(
            f"space/{space_id}", data=payload, http_method="PUT"
        )
        return self._parse_space(result)

    def delete_space(self, space_id: str) -> bool:
        self._api_call(f"space/{space_id}", http_method="DELETE")
        return True

    def list_spaces(self, workspace_id: str,
                    archived: bool = False) -> List[ClickUpSpace]:
        params = {"archived": str(archived).lower()}
        result = self._api_call(f"team/{workspace_id}/space", params=params)
        spaces_data = result.get("spaces", [])
        return [self._parse_space(s) for s in spaces_data]

    def _parse_space(self, data: Dict[str, Any]) -> ClickUpSpace:
        return ClickUpSpace(
            id=data.get("id", ""),
            name=data.get("name", ""),
            color=data.get("color", ""),
            avatar=data.get("avatar", ""),
            admin=data.get("admin", False),
            archived=data.get("archived", False),
            statuses=data.get("statuses", []),
            features=data.get("features", {}),
            extra=data,
        )

    def list_workspaces(self) -> List[ClickUpWorkspace]:
        result = self._api_call("team")
        teams_data = result.get("teams", [])
        return [
            ClickUpWorkspace(
                id=t.get("id", ""),
                name=t.get("name", ""),
                color=t.get("color", ""),
                avatar=t.get("avatar", ""),
                members=t.get("members", []),
                roles=t.get("roles", []),
                extra=t,
            )
            for t in teams_data
        ]

    # ------------------------------------------------------------------
    # Comments and attachments
    # ------------------------------------------------------------------

    def create_comment(self, task_id: str,
                       comment_text: str,
                       assignee: Optional[int] = None,
                       notify_all: bool = False,
                       **kwargs: Any) -> ClickUpComment:
        payload: Dict[str, Any] = {"comment_text": comment_text}
        if assignee is not None:
            payload["assignee"] = assignee
        if notify_all:
            payload["notify_all"] = True
        payload.update(kwargs)

        result = self._api_call(
            f"task/{task_id}/comment", data=payload, http_method="POST"
        )
        return ClickUpComment(
            id=result.get("id", ""),
            comment_text=comment_text,
            task_id=task_id,
            user_id=result.get("user", {}).get("id", 0) if result.get("user") else 0,
            date=result.get("date"),
            extra=result,
        )

    def list_comments(self, task_id: str,
                      start: Optional[int] = None,
                      start_id: str = "") -> List[ClickUpComment]:
        params: Dict[str, Any] = {}
        if start is not None:
            params["start"] = start
        if start_id:
            params["start_id"] = start_id

        result = self._api_call(f"task/{task_id}/comment", params=params)
        comments_data = result.get("comments", [])
        return [
            ClickUpComment(
                id=c.get("id", ""),
                comment_text=c.get("comment_text", ""),
                task_id=task_id,
                user_id=c.get("user", {}).get("id", 0) if c.get("user") else 0,
                user_name=c.get("user", {}).get("username", ""),
                user_email=c.get("user", {}).get("email", ""),
                date=c.get("date"),
                reactions=c.get("reactions", []),
                extra=c,
            )
            for c in comments_data
        ]

    def delete_comment(self, task_id: str, comment_id: str) -> bool:
        self._api_call(f"task/{task_id}/comment/{comment_id}", http_method="DELETE")
        return True

    def create_attachment(self, task_id: str,
                          filename: str,
                          file_data: bytes,
                          content_type: str = "application/octet-stream"
                          ) -> Dict[str, Any]:
        """
        Upload an attachment to a task.

        Args:
            task_id: Task ID.
            filename: File name.
            file_data: File bytes.
            content_type: MIME type.

        Returns:
            Attachment response data.
        """
        self._check_rate_limit()
        self._mark_request()

        url = self._build_url(f"task/{task_id}/attachment")
        boundary = "----LiuHaoBoundary7MA4YWxkTrZu0gW"
        body_lines = []
        body_lines.append(f"--{boundary}")
        body_lines.append(
            f'Content-Disposition: form-data; name="attachment"; filename="{filename}"'
        )
        body_lines.append(f"Content-Type: {content_type}")
        body_lines.append("")
        body_bytes = "\r\n".join(body_lines).encode("utf-8")
        body_bytes += file_data
        body_bytes += f"\r\n--{boundary}--\r\n".encode("utf-8")

        headers = {
            "Authorization": self.config.api_key or self.config.access_token or "",
            "Content-Type": f"multipart/form-data; boundary={boundary}",
        }

        self._log_request("POST", url, headers, f"<binary {len(file_data)} bytes>")

        try:
            import urllib.request
            req = urllib.request.Request(
                url, data=body_bytes, headers=headers, method="POST"
            )
            with urllib.request.urlopen(req, timeout=self.config.timeout) as r:
                response_body = r.read().decode("utf-8")
                self._log_response("POST", url, r.status, response_body)
                return json.loads(response_body)
        except Exception as e:
            raise self._handle_error(f"Attachment upload error: {e}")

    # ------------------------------------------------------------------
    # Time tracking
    # ------------------------------------------------------------------

    def create_time_entry(self, workspace_id: str,
                          task_id: str,
                          start_time: int,
                          end_time: int,
                          description: str = "",
                          billable: bool = False,
                          **kwargs: Any) -> ClickUpTimeEntry:
        payload: Dict[str, Any] = {
            "start": start_time,
            "end": end_time,
            "duration": end_time - start_time,
            "description": description,
            "billable": billable,
            "tid": task_id,
        }
        payload.update(kwargs)

        result = self._api_call(
            f"team/{workspace_id}/time_entries",
            data=payload,
            http_method="POST",
        )
        data = result.get("data", {})
        return ClickUpTimeEntry(
            id=data.get("id", ""),
            task_id=data.get("task", {}).get("id", task_id) if data.get("task") else task_id,
            user_id=data.get("user", {}).get("id", 0) if data.get("user") else 0,
            billable=data.get("billable", False),
            start=data.get("start"),
            end=data.get("end"),
            duration=data.get("duration", 0),
            description=data.get("description", ""),
            tags=[t.get("name", "") for t in data.get("tags", [])],
            extra=data,
        )

    def get_time_entries(self, workspace_id: str,
                         task_id: Optional[str] = None,
                         start_date: Optional[int] = None,
                         end_date: Optional[int] = None,
                         **kwargs: Any) -> List[ClickUpTimeEntry]:
        params: Dict[str, Any] = {}
        if task_id:
            params["task_id"] = task_id
        if start_date is not None:
            params["start_date"] = start_date
        if end_date is not None:
            params["end_date"] = end_date
        params.update(kwargs)

        result = self._api_call(
            f"team/{workspace_id}/time_entries", params=params
        )
        entries_data = result.get("data", [])
        return [
            ClickUpTimeEntry(
                id=e.get("id", ""),
                task_id=e.get("task", {}).get("id", "") if e.get("task") else "",
                user_id=e.get("user", {}).get("id", 0) if e.get("user") else 0,
                billable=e.get("billable", False),
                start=e.get("start"),
                end=e.get("end"),
                duration=e.get("duration", 0),
                description=e.get("description", ""),
                tags=[t.get("name", "") for t in e.get("tags", [])],
                extra=e,
            )
            for e in entries_data
        ]

    # ------------------------------------------------------------------
    # Checklists
    # ------------------------------------------------------------------

    def create_checklist(self, task_id: str,
                         name: str,
                         **kwargs: Any) -> ClickUpChecklist:
        payload: Dict[str, Any] = {"name": name}
        payload.update(kwargs)

        result = self._api_call(
            f"task/{task_id}/checklist", data=payload, http_method="POST"
        )
        data = result.get("checklist", result)
        return ClickUpChecklist(
            id=data.get("id", ""),
            name=data.get("name", ""),
            task_id=task_id,
            orderindex=data.get("orderindex", 0),
            items=data.get("items", []),
            resolved=data.get("resolved", 0),
            unresolved=data.get("unresolved", 0),
            extra=data,
        )

    def list_checklists(self, task_id: str) -> List[ClickUpChecklist]:
        result = self._api_call(f"task/{task_id}/checklist")
        checklists_data = result.get("checklists", [])
        return [
            ClickUpChecklist(
                id=c.get("id", ""),
                name=c.get("name", ""),
                task_id=task_id,
                orderindex=c.get("orderindex", 0),
                items=c.get("items", []),
                resolved=c.get("resolved", 0),
                unresolved=c.get("unresolved", 0),
                extra=c,
            )
            for c in checklists_data
        ]

    def delete_checklist(self, task_id: str, checklist_id: str) -> bool:
        self._api_call(
            f"task/{task_id}/checklist/{checklist_id}", http_method="DELETE"
        )
        return True

    def create_checklist_item(self, task_id: str,
                              checklist_id: str,
                              name: str,
                              parent: Optional[str] = None,
                              **kwargs: Any) -> Dict[str, Any]:
        payload: Dict[str, Any] = {"name": name}
        if parent:
            payload["parent"] = parent
        payload.update(kwargs)

        result = self._api_call(
            f"task/{task_id}/checklist/{checklist_id}/checklist_item",
            data=payload,
            http_method="POST",
        )
        return result

    # ------------------------------------------------------------------
    # Custom fields
    # ------------------------------------------------------------------

    def list_custom_fields(self, list_id: str) -> List[ClickUpCustomField]:
        result = self._api_call(f"list/{list_id}/field")
        fields_data = result.get("fields", [])
        return [
            ClickUpCustomField(
                id=f.get("id", ""),
                name=f.get("name", ""),
                type=f.get("type", ""),
                type_config=f.get("type_config", {}),
                date_created=f.get("date_created"),
                hide_from_guests=f.get("hide_from_guests", False),
                required=f.get("required", False),
                extra=f,
            )
            for f in fields_data
        ]

    def set_custom_field_value(self, task_id: str,
                               field_id: str,
                               value: Any) -> bool:
        self._api_call(
            f"task/{task_id}/field/{field_id}",
            data={"value": value},
            http_method="POST",
        )
        return True

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
            "Authorization": self.config.api_key or self.config.access_token or "",
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
                    "Rate limited by ClickUp",
                    retry_after=60,
                    adapter_name=self.config.name,
                )
            if e.code in (401, 403):
                raise self._handle_error(
                    "Authentication failed: invalid ClickUp API key",
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
            f"<ClickUpAdapter name={self.config.name} "
            f"status={self._status.value} "
            f"user={self._username or 'unknown'}>"
        )
