"""
Box Integration Adapter for LiuHao AI OS.

Provides integration with Box API for file upload/download, folder management,
file versioning, sharing and collaboration, comments, tasks, search,
and users and groups.
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


class BoxItemType(str, Enum):
    """Box item types."""
    FILE = "file"
    FOLDER = "folder"
    WEB_LINK = "web_link"


class BoxItemStatus(str, Enum):
    """Item status values."""
    ACTIVE = "active"
    TRASHED = "trashed"
    DELETED = "deleted"


class BoxAccessLevel(str, Enum):
    """Shared link access levels."""
    OPEN = "open"
    COMPANY = "company"
    COLLABORATORS = "collaborators"


class BoxPermission(str, Enum):
    """Collaboration permission levels."""
    EDITOR = "editor"
    VIEWER = "viewer"
    PREVIEWER = "previewer"
    UPLOADER = "uploader"
    PREVIEWER_UPLOADER = "previewer uploader"
    VIEWER_UPLOADER = "viewer uploader"
    CO_OWNER = "co-owner"
    OWNER = "owner"


class BoxTaskResolutionState(str, Enum):
    """Task resolution states."""
    COMPLETED = "completed"
    INCOMPLETE = "incomplete"
    APPROVED = "approved"
    REJECTED = "rejected"


@dataclass
class BoxFile:
    """Represents a Box file."""
    id: str = ""
    type: BoxItemType = BoxItemType.FILE
    name: str = ""
    description: str = ""
    size: int = 0
    parent_id: str = ""
    path: List[Dict[str, Any]] = field(default_factory=list)
    created_at: str = ""
    modified_at: str = ""
    created_by: Dict[str, Any] = field(default_factory=dict)
    modified_by: Dict[str, Any] = field(default_factory=dict)
    owned_by: Dict[str, Any] = field(default_factory=dict)
    status: BoxItemStatus = BoxItemStatus.ACTIVE
    sha1: str = ""
    etag: str = ""
    version_number: str = ""
    url: str = ""
    content_created_at: str = ""
    content_modified_at: str = ""
    shared_link: Optional[Dict[str, Any]] = None
    is_externally_owned: bool = False
    extra: Dict[str, Any] = field(default_factory=dict)


@dataclass
class BoxFolder:
    """Represents a Box folder."""
    id: str = ""
    type: BoxItemType = BoxItemType.FOLDER
    name: str = ""
    description: str = ""
    size: int = 0
    parent_id: str = ""
    path: List[Dict[str, Any]] = field(default_factory=list)
    created_at: str = ""
    modified_at: str = ""
    created_by: Dict[str, Any] = field(default_factory=dict)
    modified_by: Dict[str, Any] = field(default_factory=dict)
    owned_by: Dict[str, Any] = field(default_factory=dict)
    status: BoxItemStatus = BoxItemStatus.ACTIVE
    etag: str = ""
    item_status: str = "active"
    item_collection: Dict[str, Any] = field(default_factory=dict)
    shared_link: Optional[Dict[str, Any]] = None
    has_collaborations: bool = False
    is_externally_owned: bool = False
    extra: Dict[str, Any] = field(default_factory=dict)


@dataclass
class BoxFileVersion:
    """Represents a Box file version."""
    id: str = ""
    type: str = "file_version"
    name: str = ""
    size: int = 0
    created_at: str = ""
    modified_at: str = ""
    modified_by: Dict[str, Any] = field(default_factory=dict)
    version_number: str = ""
    sha1: str = ""
    trashed_at: str = ""
    purged_at: str = ""
    extra: Dict[str, Any] = field(default_factory=dict)


@dataclass
class BoxSharedLink:
    """Represents a Box shared link."""
    url: str = ""
    download_url: str = ""
    vanity_url: str = ""
    access: BoxAccessLevel = BoxAccessLevel.OPEN
    effective_access: str = ""
    is_password_enabled: bool = False
    is_download_preview_allowed: bool = True
    unshared_at: str = ""
    download_count: int = 0
    preview_count: int = 0
    permissions: Dict[str, Any] = field(default_factory=dict)
    extra: Dict[str, Any] = field(default_factory=dict)


@dataclass
class BoxCollaboration:
    """Represents a Box collaboration."""
    id: str = ""
    type: str = "collaboration"
    role: BoxPermission = BoxPermission.VIEWER
    created_by: Dict[str, Any] = field(default_factory=dict)
    created_at: str = ""
    modified_at: str = ""
    expires_at: str = ""
    status: str = "accepted"
    accessible_by: Dict[str, Any] = field(default_factory=dict)
    item: Dict[str, Any] = field(default_factory=dict)
    acknowledged_at: str = ""
    extra: Dict[str, Any] = field(default_factory=dict)


@dataclass
class BoxComment:
    """Represents a Box comment."""
    id: str = ""
    type: str = "comment"
    message: str = ""
    is_reply_comment: bool = False
    created_by: Dict[str, Any] = field(default_factory=dict)
    created_at: str = ""
    modified_at: str = ""
    item: Dict[str, Any] = field(default_factory=dict)
    tagged_message: str = ""
    extra: Dict[str, Any] = field(default_factory=dict)


@dataclass
class BoxTask:
    """Represents a Box task."""
    id: str = ""
    type: str = "task"
    item: Dict[str, Any] = field(default_factory=dict)
    due_at: str = ""
    action: str = "review"
    message: str = ""
    task_assignment_collection: Dict[str, Any] = field(default_factory=dict)
    created_by: Dict[str, Any] = field(default_factory=dict)
    created_at: str = ""
    is_completed: bool = False
    rule: str = "all_assignees"
    extra: Dict[str, Any] = field(default_factory=dict)


@dataclass
class BoxUser:
    """Represents a Box user."""
    id: str = ""
    type: str = "user"
    name: str = ""
    login: str = ""
    created_at: str = ""
    modified_at: str = ""
    language: str = "en"
    space_amount: int = 0
    space_used: int = 0
    max_upload_size: int = 0
    status: str = "active"
    job_title: str = ""
    phone: str = ""
    address: str = ""
    avatar_url: str = ""
    timezone: str = ""
    role: str = "user"
    extra: Dict[str, Any] = field(default_factory=dict)


@dataclass
class BoxGroup:
    """Represents a Box group."""
    id: str = ""
    type: str = "group"
    name: str = ""
    description: str = ""
    provenance: str = ""
    external_sync_identifier: str = ""
    created_at: str = ""
    modified_at: str = ""
    memberships: List[Dict[str, Any]] = field(default_factory=list)
    extra: Dict[str, Any] = field(default_factory=dict)


class BoxAdapter(BaseIntegrationAdapter):
    """
    Box integration adapter for LiuHao AI OS.

    Supports:
    - File upload and download
    - Folder management
    - File versioning
    - Sharing and collaboration
    - Comments
    - Tasks
    - Search
    - Users and groups
    """

    def __init__(self, config: IntegrationConfig):
        if config.auth_type == AuthType.NONE:
            config.auth_type = AuthType.BEARER_TOKEN
        if not config.base_url:
            config.base_url = "https://api.box.com/2.0"
        super().__init__(config)
        self._user_id: Optional[str] = None
        self._user_name: Optional[str] = None
        self._user_login: Optional[str] = None
        self._enterprise_id: Optional[str] = None
        self._cached_files: Dict[str, BoxFile] = {}
        self._cached_folders: Dict[str, BoxFolder] = {}
        self._upload_url = "https://upload.box.com/api/2.0"

    # ------------------------------------------------------------------
    # Base interface
    # ------------------------------------------------------------------

    def connect(self) -> bool:
        self._status = ConnectionStatus.CONNECTING
        self._logger.info("Connecting to Box API...")

        try:
            result = self._api_call("users/me")
            self._user_id = result.get("id", "")
            self._user_name = result.get("name", "")
            self._user_login = result.get("login", "")
            self._enterprise_id = result.get("enterprise", {}).get("id", "")
            self._status = ConnectionStatus.CONNECTED
            self._connected_at = time.time()
            self._logger.info(
                "Connected to Box as %s (%s)",
                self._user_name, self._user_login,
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
        self._user_login = None
        self._enterprise_id = None
        self._cached_files = {}
        self._cached_folders = {}
        self._session = None
        self._logger.info("Disconnected from Box")
        return True

    def is_connected(self) -> bool:
        return self._status == ConnectionStatus.CONNECTED

    def get_status(self) -> IntegrationStatus:
        return self._build_status({
            "user_name": self._user_name,
            "user_id": self._user_id,
            "user_login": self._user_login,
            "enterprise_id": self._enterprise_id,
            "cached_files": len(self._cached_files),
            "cached_folders": len(self._cached_folders),
        })

    def test_connection(self) -> bool:
        try:
            if not self.is_connected():
                return False
            result = self._api_call("users/me")
            return "id" in result
        except Exception:
            return False

    # ------------------------------------------------------------------
    # File upload/download
    # ------------------------------------------------------------------

    def upload_file(self, parent_id: str,
                    name: str,
                    file_data: bytes,
                    content_md5: str = "",
                    description: str = "",
                    preflight_check: bool = False) -> BoxFile:
        self._check_rate_limit()
        self._mark_request()

        url = f"{self._upload_url}/files/content"
        boundary = "----LiuHaoBoxBoundary7MA4YWxkTrZu0gW"

        # Build multipart form body
        parts = []
        # attributes part
        attributes = json.dumps({
            "name": name,
            "parent": {"id": parent_id},
            "description": description,
        })
        parts.append(f"--{boundary}")
        parts.append('Content-Disposition: form-data; name="attributes"')
        parts.append("Content-Type: application/json")
        parts.append("")
        parts.append(attributes)

        # file part
        parts.append(f"--{boundary}")
        parts.append(
            f'Content-Disposition: form-data; name="file"; filename="{name}"'
        )
        parts.append("Content-Type: application/octet-stream")
        parts.append("")

        body = "\r\n".join(parts).encode("utf-8")
        body += file_data
        body += f"\r\n--{boundary}--\r\n".encode("utf-8")

        headers = {
            "Authorization": f"Bearer {self.config.access_token or self.config.api_key or ''}",
            "Content-Type": f"multipart/form-data; boundary={boundary}",
        }
        if content_md5:
            headers["Content-MD5"] = content_md5

        self._log_request("POST", url, headers, f"<binary {len(file_data)} bytes>")

        try:
            import urllib.request
            req = urllib.request.Request(
                url, data=body, headers=headers, method="POST"
            )
            with urllib.request.urlopen(req, timeout=self.config.timeout) as r:
                response_body = r.read().decode("utf-8")
                self._log_response("POST", url, r.status, response_body)
                result = json.loads(response_body)
                entries = result.get("entries", [])
                if entries:
                    file_data_obj = entries[0]
                    box_file = self._parse_file(file_data_obj)
                    if box_file.id:
                        self._cached_files[box_file.id] = box_file
                    return box_file
                raise self._handle_error("Upload failed: no entries returned")
        except (IntegrationError, RateLimitError):
            raise
        except Exception as e:
            raise self._handle_error(f"Upload error: {e}")

    def upload_new_version(self, file_id: str,
                           file_data: bytes,
                           name: str = "",
                           content_md5: str = "") -> BoxFile:
        self._check_rate_limit()
        self._mark_request()

        url = f"{self._upload_url}/files/{file_id}/content"
        boundary = "----LiuHaoBoxBoundary7MA4YWxkTrZu0gW"

        parts = []
        if name:
            attributes = json.dumps({"name": name})
            parts.append(f"--{boundary}")
            parts.append('Content-Disposition: form-data; name="attributes"')
            parts.append("Content-Type: application/json")
            parts.append("")
            parts.append(attributes)

        parts.append(f"--{boundary}")
        parts.append('Content-Disposition: form-data; name="file"; filename="file"')
        parts.append("Content-Type: application/octet-stream")
        parts.append("")

        body = "\r\n".join(parts).encode("utf-8")
        body += file_data
        body += f"\r\n--{boundary}--\r\n".encode("utf-8")

        headers = {
            "Authorization": f"Bearer {self.config.access_token or self.config.api_key or ''}",
            "Content-Type": f"multipart/form-data; boundary={boundary}",
        }
        if content_md5:
            headers["Content-MD5"] = content_md5

        self._log_request("POST", url, headers, f"<binary {len(file_data)} bytes>")

        try:
            import urllib.request
            req = urllib.request.Request(
                url, data=body, headers=headers, method="POST"
            )
            with urllib.request.urlopen(req, timeout=self.config.timeout) as r:
                response_body = r.read().decode("utf-8")
                self._log_response("POST", url, r.status, response_body)
                result = json.loads(response_body)
                entries = result.get("entries", [])
                if entries:
                    box_file = self._parse_file(entries[0])
                    if box_file.id:
                        self._cached_files[box_file.id] = box_file
                    return box_file
                raise self._handle_error("Upload failed: no entries returned")
        except Exception as e:
            raise self._handle_error(f"Upload error: {e}")

    def download_file(self, file_id: str,
                      version_id: str = "") -> bytes:
        self._check_rate_limit()
        self._mark_request()

        url = self._build_url(f"files/{file_id}/content")
        headers = {
            "Authorization": f"Bearer {self.config.access_token or self.config.api_key or ''}",
        }
        if version_id:
            url += f"?version={version_id}"

        self._log_request("GET", url, headers, "<binary download>")

        try:
            import urllib.request
            req = urllib.request.Request(url, headers=headers, method="GET")
            with urllib.request.urlopen(req, timeout=self.config.timeout) as r:
                data = r.read()
                self._log_response(
                    "GET", url, r.status, f"<binary {len(data)} bytes>"
                )
                return data
        except Exception as e:
            raise self._handle_error(f"Download error: {e}")

    # ------------------------------------------------------------------
    # File metadata
    # ------------------------------------------------------------------

    def get_file(self, file_id: str,
                 fields: str = "") -> BoxFile:
        if file_id in self._cached_files:
            return self._cached_files[file_id]

        params = {}
        if fields:
            params["fields"] = fields

        result = self._api_call(f"files/{file_id}", params=params)
        box_file = self._parse_file(result)
        if box_file.id:
            self._cached_files[box_file.id] = box_file
        return box_file

    def _parse_file(self, data: Dict[str, Any]) -> BoxFile:
        parent = data.get("parent", {})
        path_entries = data.get("path_collection", {}).get("entries", [])
        return BoxFile(
            id=data.get("id", ""),
            type=BoxItemType(data.get("type", "file")),
            name=data.get("name", ""),
            description=data.get("description", ""),
            size=data.get("size", 0),
            parent_id=parent.get("id", "") if parent else "",
            path=path_entries,
            created_at=data.get("created_at", ""),
            modified_at=data.get("modified_at", ""),
            created_by=data.get("created_by", {}),
            modified_by=data.get("modified_by", {}),
            owned_by=data.get("owned_by", {}),
            status=BoxItemStatus(data.get("item_status", "active")),
            sha1=data.get("sha1", ""),
            etag=data.get("etag", ""),
            version_number=data.get("version_number", ""),
            url=data.get("url", ""),
            content_created_at=data.get("content_created_at", ""),
            content_modified_at=data.get("content_modified_at", ""),
            shared_link=data.get("shared_link"),
            is_externally_owned=data.get("is_externally_owned", False),
            extra=data,
        )

    # ------------------------------------------------------------------
    # Folder management
    # ------------------------------------------------------------------

    def create_folder(self, name: str,
                      parent_id: str = "0") -> BoxFolder:
        data = {"name": name, "parent": {"id": parent_id}}
        result = self._api_call(
            "folders", data=data, http_method="POST"
        )
        folder = self._parse_folder(result)
        if folder.id:
            self._cached_folders[folder.id] = folder
        return folder

    def get_folder(self, folder_id: str = "0",
                   fields: str = "") -> BoxFolder:
        if folder_id in self._cached_folders:
            return self._cached_folders[folder_id]

        params = {}
        if fields:
            params["fields"] = fields

        result = self._api_call(f"folders/{folder_id}", params=params)
        folder = self._parse_folder(result)
        if folder.id:
            self._cached_folders[folder.id] = folder
        return folder

    def update_folder(self, folder_id: str,
                      name: Optional[str] = None,
                      description: Optional[str] = None,
                      **kwargs: Any) -> BoxFolder:
        data: Dict[str, Any] = {}
        if name is not None:
            data["name"] = name
        if description is not None:
            data["description"] = description
        data.update(kwargs)

        result = self._api_call(
            f"folders/{folder_id}", data=data, http_method="PUT"
        )
        folder = self._parse_folder(result)
        if folder.id:
            self._cached_folders[folder.id] = folder
        return folder

    def delete_folder(self, folder_id: str,
                      recursive: bool = False) -> bool:
        params = {"recursive": str(recursive).lower()}
        self._api_call(
            f"folders/{folder_id}", params=params, http_method="DELETE"
        )
        if folder_id in self._cached_folders:
            del self._cached_folders[folder_id]
        return True

    def list_folder_items(self, folder_id: str = "0",
                          limit: int = 100,
                          offset: int = 0,
                          fields: str = "",
                          **kwargs: Any) -> Dict[str, Any]:
        params: Dict[str, Any] = {"limit": limit, "offset": offset}
        if fields:
            params["fields"] = fields
        params.update(kwargs)

        result = self._api_call(
            f"folders/{folder_id}/items", params=params
        )
        entries = result.get("entries", [])
        items = []
        for entry in entries:
            entry_type = entry.get("type", "")
            if entry_type == "folder":
                items.append(self._parse_folder(entry))
            elif entry_type == "file":
                items.append(self._parse_file(entry))
            else:
                items.append(entry)

        return {
            "items": items,
            "total_count": result.get("total_count", 0),
            "limit": result.get("limit", 0),
            "offset": result.get("offset", 0),
        }

    def _parse_folder(self, data: Dict[str, Any]) -> BoxFolder:
        parent = data.get("parent", {})
        path_entries = data.get("path_collection", {}).get("entries", [])
        return BoxFolder(
            id=data.get("id", ""),
            type=BoxItemType(data.get("type", "folder")),
            name=data.get("name", ""),
            description=data.get("description", ""),
            size=data.get("size", 0),
            parent_id=parent.get("id", "") if parent else "",
            path=path_entries,
            created_at=data.get("created_at", ""),
            modified_at=data.get("modified_at", ""),
            created_by=data.get("created_by", {}),
            modified_by=data.get("modified_by", {}),
            owned_by=data.get("owned_by", {}),
            status=BoxItemStatus(data.get("item_status", "active")),
            etag=data.get("etag", ""),
            item_collection=data.get("item_collection", {}),
            shared_link=data.get("shared_link"),
            has_collaborations=data.get("has_collaborations", False),
            is_externally_owned=data.get("is_externally_owned", False),
            extra=data,
        )

    # ------------------------------------------------------------------
    # File versioning
    # ------------------------------------------------------------------

    def list_file_versions(self, file_id: str,
                           limit: int = 100,
                           offset: int = 0) -> Dict[str, Any]:
        params = {"limit": limit, "offset": offset}
        result = self._api_call(
            f"files/{file_id}/versions", params=params
        )
        versions_data = result.get("entries", [])
        versions = [
            BoxFileVersion(
                id=v.get("id", ""),
                type=v.get("type", "file_version"),
                name=v.get("name", ""),
                size=v.get("size", 0),
                created_at=v.get("created_at", ""),
                modified_at=v.get("modified_at", ""),
                modified_by=v.get("modified_by", {}),
                version_number=v.get("version_number", ""),
                sha1=v.get("sha1", ""),
                trashed_at=v.get("trashed_at", ""),
                purged_at=v.get("purged_at", ""),
                extra=v,
            )
            for v in versions_data
        ]
        return {
            "versions": versions,
            "total_count": result.get("total_count", 0),
            "limit": result.get("limit", 0),
            "offset": result.get("offset", 0),
        }

    def get_file_version(self, file_id: str,
                         version_id: str) -> BoxFileVersion:
        result = self._api_call(f"files/{file_id}/versions/{version_id}")
        return BoxFileVersion(
            id=result.get("id", ""),
            type=result.get("type", "file_version"),
            name=result.get("name", ""),
            size=result.get("size", 0),
            created_at=result.get("created_at", ""),
            modified_at=result.get("modified_at", ""),
            modified_by=result.get("modified_by", {}),
            version_number=result.get("version_number", ""),
            sha1=result.get("sha1", ""),
            extra=result,
        )

    def promote_version(self, file_id: str,
                        version_id: str) -> BoxFile:
        data = {"type": "file_version", "id": version_id}
        result = self._api_call(
            f"files/{file_id}/versions/current",
            data=data,
            http_method="POST",
        )
        box_file = self._parse_file(result)
        if box_file.id:
            self._cached_files[box_file.id] = box_file
        return box_file

    # ------------------------------------------------------------------
    # Sharing and collaboration
    # ------------------------------------------------------------------

    def create_shared_link(self, item_type: str,
                           item_id: str,
                           access: BoxAccessLevel = BoxAccessLevel.OPEN,
                           password: str = "",
                           unshared_at: str = "",
                           **kwargs: Any) -> BoxSharedLink:
        data: Dict[str, Any] = {"access": access.value}
        if password:
            data["password"] = password
        if unshared_at:
            data["unshared_at"] = unshared_at
        data.update(kwargs)

        result = self._api_call(
            f"{item_type}s/{item_id}/add_shared_link",
            data=data,
            http_method="PUT",
        )
        link_data = result.get("shared_link", {})
        return BoxSharedLink(
            url=link_data.get("url", ""),
            download_url=link_data.get("download_url", ""),
            vanity_url=link_data.get("vanity_url", ""),
            access=BoxAccessLevel(link_data.get("access", "open")),
            effective_access=link_data.get("effective_access", ""),
            is_password_enabled=link_data.get("is_password_enabled", False),
            is_download_preview_allowed=link_data.get(
                "is_download_preview_allowed", True
            ),
            unshared_at=link_data.get("unshared_at", ""),
            download_count=link_data.get("download_count", 0),
            preview_count=link_data.get("preview_count", 0),
            permissions=link_data.get("permissions", {}),
            extra=link_data,
        )

    def delete_shared_link(self, item_type: str, item_id: str) -> bool:
        self._api_call(
            f"{item_type}s/{item_id}/remove_shared_link",
            http_method="PUT",
        )
        return True

    def create_collaboration(self, item_type: str,
                             item_id: str,
                             accessible_by_id: str,
                             accessible_by_type: str = "user",
                             role: BoxPermission = BoxPermission.VIEWER,
                             can_view_path: bool = False,
                             **kwargs: Any) -> BoxCollaboration:
        data: Dict[str, Any] = {
            "item": {"type": item_type, "id": item_id},
            "accessible_by": {
                "type": accessible_by_type,
                "id": accessible_by_id,
            },
            "role": role.value,
            "can_view_path": can_view_path,
        }
        data.update(kwargs)

        result = self._api_call(
            "collaborations", data=data, http_method="POST"
        )
        return BoxCollaboration(
            id=result.get("id", ""),
            type=result.get("type", "collaboration"),
            role=BoxPermission(result.get("role", "viewer")),
            created_by=result.get("created_by", {}),
            created_at=result.get("created_at", ""),
            modified_at=result.get("modified_at", ""),
            expires_at=result.get("expires_at", ""),
            status=result.get("status", "accepted"),
            accessible_by=result.get("accessible_by", {}),
            item=result.get("item", {}),
            acknowledged_at=result.get("acknowledged_at", ""),
            extra=result,
        )

    def list_collaborations(self, item_type: str,
                            item_id: str,
                            limit: int = 100,
                            offset: int = 0) -> Dict[str, Any]:
        params = {"limit": limit, "offset": offset}
        result = self._api_call(
            f"{item_type}s/{item_id}/collaborations", params=params
        )
        collabs_data = result.get("entries", [])
        return {
            "collaborations": [
                BoxCollaboration(
                    id=c.get("id", ""),
                    role=BoxPermission(c.get("role", "viewer")),
                    created_by=c.get("created_by", {}),
                    created_at=c.get("created_at", ""),
                    status=c.get("status", ""),
                    accessible_by=c.get("accessible_by", {}),
                    item=c.get("item", {}),
                    extra=c,
                )
                for c in collabs_data
            ],
            "total_count": result.get("total_count", 0),
            "limit": result.get("limit", 0),
            "offset": result.get("offset", 0),
        }

    def delete_collaboration(self, collaboration_id: str) -> bool:
        self._api_call(
            f"collaborations/{collaboration_id}", http_method="DELETE"
        )
        return True

    # ------------------------------------------------------------------
    # Comments
    # ------------------------------------------------------------------

    def create_comment(self, item_type: str,
                       item_id: str,
                       message: str,
                       tagged_message: str = "") -> BoxComment:
        data: Dict[str, Any] = {"message": message}
        if tagged_message:
            data["tagged_message"] = tagged_message

        result = self._api_call(
            f"{item_type}s/{item_id}/comments",
            data=data,
            http_method="POST",
        )
        return BoxComment(
            id=result.get("id", ""),
            message=result.get("message", ""),
            is_reply_comment=result.get("is_reply_comment", False),
            created_by=result.get("created_by", {}),
            created_at=result.get("created_at", ""),
            modified_at=result.get("modified_at", ""),
            item=result.get("item", {}),
            tagged_message=result.get("tagged_message", ""),
            extra=result,
        )

    def list_comments(self, item_type: str,
                      item_id: str,
                      limit: int = 100,
                      offset: int = 0) -> Dict[str, Any]:
        params = {"limit": limit, "offset": offset}
        result = self._api_call(
            f"{item_type}s/{item_id}/comments", params=params
        )
        comments_data = result.get("entries", [])
        return {
            "comments": [
                BoxComment(
                    id=c.get("id", ""),
                    message=c.get("message", ""),
                    is_reply_comment=c.get("is_reply_comment", False),
                    created_by=c.get("created_by", {}),
                    created_at=c.get("created_at", ""),
                    modified_at=c.get("modified_at", ""),
                    tagged_message=c.get("tagged_message", ""),
                    extra=c,
                )
                for c in comments_data
            ],
            "total_count": result.get("total_count", 0),
        }

    def delete_comment(self, comment_id: str) -> bool:
        self._api_call(f"comments/{comment_id}", http_method="DELETE")
        return True

    # ------------------------------------------------------------------
    # Tasks
    # ------------------------------------------------------------------

    def create_task(self, file_id: str,
                    action: str = "review",
                    message: str = "",
                    due_at: str = "",
                    rule: str = "all_assignees",
                    **kwargs: Any) -> BoxTask:
        item = {"type": "file", "id": file_id}
        data: Dict[str, Any] = {
            "item": item,
            "action": action,
            "message": message,
            "rule": rule,
        }
        if due_at:
            data["due_at"] = due_at
        data.update(kwargs)

        result = self._api_call("tasks", data=data, http_method="POST")
        return BoxTask(
            id=result.get("id", ""),
            item=result.get("item", {}),
            due_at=result.get("due_at", ""),
            action=result.get("action", "review"),
            message=result.get("message", ""),
            task_assignment_collection=result.get(
                "task_assignment_collection", {}
            ),
            created_by=result.get("created_by", {}),
            created_at=result.get("created_at", ""),
            is_completed=result.get("is_completed", False),
            rule=result.get("rule", "all_assignees"),
            extra=result,
        )

    def get_task(self, task_id: str) -> BoxTask:
        result = self._api_call(f"tasks/{task_id}")
        return BoxTask(
            id=result.get("id", ""),
            item=result.get("item", {}),
            due_at=result.get("due_at", ""),
            action=result.get("action", ""),
            message=result.get("message", ""),
            task_assignment_collection=result.get(
                "task_assignment_collection", {}
            ),
            created_by=result.get("created_by", {}),
            created_at=result.get("created_at", ""),
            is_completed=result.get("is_completed", False),
            rule=result.get("rule", ""),
            extra=result,
        )

    def list_tasks(self, item_type: str,
                   item_id: str,
                   limit: int = 100,
                   offset: int = 0) -> Dict[str, Any]:
        params = {"limit": limit, "offset": offset}
        result = self._api_call(
            f"{item_type}s/{item_id}/tasks", params=params
        )
        tasks_data = result.get("entries", [])
        return {
            "tasks": [
                BoxTask(
                    id=t.get("id", ""),
                    item=t.get("item", {}),
                    due_at=t.get("due_at", ""),
                    action=t.get("action", ""),
                    message=t.get("message", ""),
                    created_at=t.get("created_at", ""),
                    is_completed=t.get("is_completed", False),
                    rule=t.get("rule", ""),
                    extra=t,
                )
                for t in tasks_data
            ],
            "total_count": result.get("total_count", 0),
        }

    def delete_task(self, task_id: str) -> bool:
        self._api_call(f"tasks/{task_id}", http_method="DELETE")
        return True

    # ------------------------------------------------------------------
    # Search
    # ------------------------------------------------------------------

    def search(self, query: str,
               limit: int = 30,
               offset: int = 0,
               type: str = "",
               content_types: Optional[List[str]] = None,
               ancestor_folder_ids: Optional[List[str]] = None,
               file_extensions: Optional[List[str]] = None,
               **kwargs: Any) -> Dict[str, Any]:
        data: Dict[str, Any] = {
            "query": query,
            "limit": limit,
            "offset": offset,
        }
        if type:
            data["type"] = type
        if content_types:
            data["content_types"] = content_types
        if ancestor_folder_ids:
            data["ancestor_folder_ids"] = ancestor_folder_ids
        if file_extensions:
            data["file_extensions"] = file_extensions
        data.update(kwargs)

        result = self._api_call(
            "search", data=data, http_method="POST"
        )
        entries = result.get("entries", [])
        items = []
        for entry in entries:
            item_data = entry
            item_type = item_data.get("type", "")
            if item_type == "file":
                items.append(self._parse_file(item_data))
            elif item_type == "folder":
                items.append(self._parse_folder(item_data))
            else:
                items.append(item_data)

        return {
            "entries": items,
            "total_count": result.get("total_count", 0),
            "limit": result.get("limit", 0),
            "offset": result.get("offset", 0),
        }

    # ------------------------------------------------------------------
    # Users and groups
    # ------------------------------------------------------------------

    def get_current_user(self) -> BoxUser:
        result = self._api_call("users/me")
        return self._parse_user(result)

    def get_user(self, user_id: str) -> BoxUser:
        result = self._api_call(f"users/{user_id}")
        return self._parse_user(result)

    def list_users(self, filter_term: str = "",
                   limit: int = 100,
                   offset: int = 0,
                   **kwargs: Any) -> Dict[str, Any]:
        params: Dict[str, Any] = {"limit": limit, "offset": offset}
        if filter_term:
            params["filter_term"] = filter_term
        params.update(kwargs)

        result = self._api_call("users", params=params)
        users_data = result.get("entries", [])
        return {
            "users": [self._parse_user(u) for u in users_data],
            "total_count": result.get("total_count", 0),
            "limit": result.get("limit", 0),
            "offset": result.get("offset", 0),
        }

    def _parse_user(self, data: Dict[str, Any]) -> BoxUser:
        return BoxUser(
            id=data.get("id", ""),
            type=data.get("type", "user"),
            name=data.get("name", ""),
            login=data.get("login", ""),
            created_at=data.get("created_at", ""),
            modified_at=data.get("modified_at", ""),
            language=data.get("language", "en"),
            space_amount=data.get("space_amount", 0),
            space_used=data.get("space_used", 0),
            max_upload_size=data.get("max_upload_size", 0),
            status=data.get("status", "active"),
            job_title=data.get("job_title", ""),
            phone=data.get("phone", ""),
            address=data.get("address", ""),
            avatar_url=data.get("avatar_url", ""),
            timezone=data.get("timezone", ""),
            role=data.get("role", "user"),
            extra=data,
        )

    def list_groups(self, limit: int = 100,
                    offset: int = 0,
                    filter_term: str = "",
                    **kwargs: Any) -> Dict[str, Any]:
        params: Dict[str, Any] = {"limit": limit, "offset": offset}
        if filter_term:
            params["filter_term"] = filter_term
        params.update(kwargs)

        result = self._api_call("groups", params=params)
        groups_data = result.get("entries", [])
        return {
            "groups": [
                BoxGroup(
                    id=g.get("id", ""),
                    name=g.get("name", ""),
                    description=g.get("description", ""),
                    provenance=g.get("provenance", ""),
                    created_at=g.get("created_at", ""),
                    modified_at=g.get("modified_at", ""),
                    extra=g,
                )
                for g in groups_data
            ],
            "total_count": result.get("total_count", 0),
            "limit": result.get("limit", 0),
            "offset": result.get("offset", 0),
        }

    def create_group(self, name: str,
                     description: str = "",
                     provenance: str = "",
                     **kwargs: Any) -> BoxGroup:
        data: Dict[str, Any] = {"name": name}
        if description:
            data["description"] = description
        if provenance:
            data["provenance"] = provenance
        data.update(kwargs)

        result = self._api_call("groups", data=data, http_method="POST")
        return BoxGroup(
            id=result.get("id", ""),
            name=result.get("name", ""),
            description=result.get("description", ""),
            provenance=result.get("provenance", ""),
            created_at=result.get("created_at", ""),
            modified_at=result.get("modified_at", ""),
            extra=result,
        )

    def delete_group(self, group_id: str) -> bool:
        self._api_call(f"groups/{group_id}", http_method="DELETE")
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
            "Authorization": f"Bearer {self.config.access_token or self.config.api_key or ''}",
            "Content-Type": "application/json",
            "Accept": "application/json",
            "BoxApi": "",
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
                if response_body:
                    return json.loads(response_body)
                return {}

        except urllib.error.HTTPError as e:
            response_body = e.read().decode("utf-8", errors="replace")
            self._log_response(http_method, url, e.code, response_body)
            if e.code == 429:
                self._rate_limit_hits += 1
                raise RateLimitError(
                    "Rate limited by Box",
                    retry_after=60,
                    adapter_name=self.config.name,
                )
            if e.code in (401, 403):
                raise self._handle_error(
                    "Authentication failed: invalid Box token",
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
            f"<BoxAdapter name={self.config.name} "
            f"status={self._status.value} "
            f"user={self._user_name or 'unknown'}>"
        )
