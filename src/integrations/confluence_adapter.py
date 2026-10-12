"""
Confluence Integration Adapter for LiuHao AI OS.

Provides integration with Confluence wiki for pages (CRUD), spaces management,
content search, attachments, comments, labels, templates, and users/groups.
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


class ConfluenceContentStatus(str, Enum):
    """Content status values."""
    CURRENT = "current"
    DRAFT = "draft"
    HISTORICAL = "historical"
    TRASHED = "trashed"


class ConfluenceContentType(str, Enum):
    """Content types."""
    PAGE = "page"
    BLOGPOST = "blogpost"
    COMMENT = "comment"
    ATTACHMENT = "attachment"


class ConfluenceSpaceStatus(str, Enum):
    """Space status values."""
    CURRENT = "current"
    ARCHIVED = "archived"


class ConfluenceSpaceType(str, Enum):
    """Space types."""
    GLOBAL = "global"
    PERSONAL = "personal"


class ConfluenceSearchType(str, Enum):
    """Search result types."""
    CONTENT = "content"
    SPACE = "space"
    PERSON = "person"
    ATTACHMENT = "attachment"
    CUSTOM_CONTENT = "custom_content"


@dataclass
class ConfluencePage:
    """Represents a Confluence page."""
    id: str = ""
    type: ConfluenceContentType = ConfluenceContentType.PAGE
    title: str = ""
    status: ConfluenceContentStatus = ConfluenceContentStatus.CURRENT
    space_key: str = ""
    space_name: str = ""
    version_number: int = 0
    version_message: str = ""
    version_when: str = ""
    version_by: Dict[str, Any] = field(default_factory=dict)
    body_storage: str = ""
    body_view: str = ""
    body_editor: str = ""
    parent_id: str = ""
    parent_title: str = ""
    created_at: str = ""
    created_by: Dict[str, Any] = field(default_factory=dict)
    history_latest: bool = True
    labels: List[str] = field(default_factory=list)
    webui_url: str = ""
    tinyui_url: str = ""
    _links: Dict[str, Any] = field(default_factory=dict)
    extra: Dict[str, Any] = field(default_factory=dict)


@dataclass
class ConfluenceSpace:
    """Represents a Confluence space."""
    id: int = 0
    key: str = ""
    name: str = ""
    type: ConfluenceSpaceType = ConfluenceSpaceType.GLOBAL
    status: ConfluenceSpaceStatus = ConfluenceSpaceStatus.CURRENT
    description: Dict[str, Any] = field(default_factory=dict)
    icon: Dict[str, Any] = field(default_factory=dict)
    homepage_id: str = ""
    created_at: str = ""
    author: Dict[str, Any] = field(default_factory=dict)
    _links: Dict[str, Any] = field(default_factory=dict)
    extra: Dict[str, Any] = field(default_factory=dict)


@dataclass
class ConfluenceAttachment:
    """Represents a Confluence attachment."""
    id: str = ""
    type: ConfluenceContentType = ConfluenceContentType.ATTACHMENT
    title: str = ""
    status: ConfluenceContentStatus = ConfluenceContentStatus.CURRENT
    media_type: str = ""
    file_size: int = 0
    version_number: int = 0
    comment: str = ""
    download_url: str = ""
    webui_url: str = ""
    created_at: str = ""
    created_by: Dict[str, Any] = field(default_factory=dict)
    extra: Dict[str, Any] = field(default_factory=dict)


@dataclass
class ConfluenceComment:
    """Represents a Confluence comment."""
    id: str = ""
    type: ConfluenceContentType = ConfluenceContentType.COMMENT
    title: str = ""
    body: str = ""
    status: ConfluenceContentStatus = ConfluenceContentStatus.CURRENT
    container_id: str = ""
    container_type: str = ""
    created_at: str = ""
    created_by: Dict[str, Any] = field(default_factory=dict)
    version_number: int = 0
    extra: Dict[str, Any] = field(default_factory=dict)


@dataclass
class ConfluenceLabel:
    """Represents a Confluence label."""
    label: str = ""
    name: str = ""
    prefix: str = "global"
    id: str = ""
    extra: Dict[str, Any] = field(default_factory=dict)


@dataclass
class ConfluenceTemplate:
    """Represents a Confluence template."""
    template_id: str = ""
    name: str = ""
    description: str = ""
    body: Dict[str, Any] = field(default_factory=dict)
    labels: List[str] = field(default_factory=list)
    space_key: str = ""
    extra: Dict[str, Any] = field(default_factory=dict)


@dataclass
class ConfluenceUser:
    """Represents a Confluence user."""
    account_id: str = ""
    account_type: str = "atlassian"
    email: str = ""
    display_name: str = ""
    nickname: str = ""
    profile_picture: Dict[str, Any] = field(default_factory=dict)
    public_name: str = ""
    extra: Dict[str, Any] = field(default_factory=dict)


@dataclass
class ConfluenceGroup:
    """Represents a Confluence group."""
    name: str = ""
    type: str = "group"
    extra: Dict[str, Any] = field(default_factory=dict)


@dataclass
class ConfluenceSearchResult:
    """Represents a Confluence search result entry."""
    content: Optional[ConfluencePage] = None
    space: Optional[ConfluenceSpace] = None
    title: str = ""
    excerpt: str = ""
    url: str = ""
    result_type: str = ""
    result_global_container_title: str = ""
    extra: Dict[str, Any] = field(default_factory=dict)


class ConfluenceAdapter(BaseIntegrationAdapter):
    """
    Confluence integration adapter for LiuHao AI OS.

    Supports:
    - Pages (create, read, update, delete)
    - Spaces management
    - Content search (CQL)
    - Attachments
    - Comments
    - Labels
    - Templates
    - Users and groups
    """

    def __init__(self, config: IntegrationConfig):
        if config.auth_type == AuthType.NONE:
            config.auth_type = AuthType.BASIC_AUTH
        if not config.base_url:
            config.base_url = "https://your-domain.atlassian.net/wiki/rest/api"
        super().__init__(config)
        self._user_account_id: Optional[str] = None
        self._user_email: Optional[str] = None
        self._user_display_name: Optional[str] = None
        self._cached_pages: Dict[str, ConfluencePage] = {}
        self._cached_spaces: Dict[str, ConfluenceSpace] = {}
        self._base_url = config.base_url

    # ------------------------------------------------------------------
    # Base interface
    # ------------------------------------------------------------------

    def connect(self) -> bool:
        self._status = ConnectionStatus.CONNECTING
        self._logger.info("Connecting to Confluence API...")

        try:
            result = self._api_call("user/current")
            self._user_account_id = result.get("accountId", "")
            self._user_email = result.get("email", "")
            self._user_display_name = result.get("displayName", "")
            self._status = ConnectionStatus.CONNECTED
            self._connected_at = time.time()
            self._logger.info(
                "Connected to Confluence as %s (%s)",
                self._user_display_name, self._user_email,
            )
            return True
        except IntegrationError:
            raise
        except Exception as e:
            self._status = ConnectionStatus.ERROR
            raise self._handle_error(f"Connection error: {e}")

    def disconnect(self) -> bool:
        self._status = ConnectionStatus.DISCONNECTED
        self._user_account_id = None
        self._user_email = None
        self._user_display_name = None
        self._cached_pages = {}
        self._cached_spaces = {}
        self._session = None
        self._logger.info("Disconnected from Confluence")
        return True

    def is_connected(self) -> bool:
        return self._status == ConnectionStatus.CONNECTED

    def get_status(self) -> IntegrationStatus:
        return self._build_status({
            "user_display_name": self._user_display_name,
            "user_email": self._user_email,
            "user_account_id": self._user_account_id,
            "cached_pages": len(self._cached_pages),
            "cached_spaces": len(self._cached_spaces),
        })

    def test_connection(self) -> bool:
        try:
            if not self.is_connected():
                return False
            result = self._api_call("user/current")
            return "accountId" in result
        except Exception:
            return False

    # ------------------------------------------------------------------
    # Pages
    # ------------------------------------------------------------------

    def create_page(self, title: str,
                    space_key: str,
                    body_storage: str = "",
                    parent_id: str = "",
                    status: ConfluenceContentStatus = ConfluenceContentStatus.CURRENT,
                    **kwargs: Any) -> ConfluencePage:
        data: Dict[str, Any] = {
            "type": "page",
            "title": title,
            "space": {"key": space_key},
            "status": status.value,
        }
        if body_storage:
            data["body"] = {
                "storage": {
                    "value": body_storage,
                    "representation": "storage",
                }
            }
        if parent_id:
            data["ancestors"] = [{"id": parent_id}]
        data.update(kwargs)

        result = self._api_call("content", data=data, http_method="POST")
        page = self._parse_page(result)
        if page.id:
            self._cached_pages[page.id] = page
        return page

    def get_page(self, page_id: str,
                 expand: str = "body.storage,version,space,history",
                 status: str = "current") -> ConfluencePage:
        if page_id in self._cached_pages:
            return self._cached_pages[page_id]

        params = {"expand": expand, "status": status}
        result = self._api_call(f"content/{page_id}", params=params)
        page = self._parse_page(result)
        if page.id:
            self._cached_pages[page.id] = page
        return page

    def update_page(self, page_id: str,
                    title: str,
                    version_number: int,
                    body_storage: str = "",
                    status: ConfluenceContentStatus = ConfluenceContentStatus.CURRENT,
                    version_message: str = "",
                    **kwargs: Any) -> ConfluencePage:
        # First get the current page to know space key
        current = self.get_page(page_id)
        data: Dict[str, Any] = {
            "id": page_id,
            "type": "page",
            "title": title,
            "status": status.value,
            "version": {
                "number": version_number,
                "message": version_message,
            },
            "space": {"key": current.space_key},
        }
        if body_storage:
            data["body"] = {
                "storage": {
                    "value": body_storage,
                    "representation": "storage",
                }
            }
        data.update(kwargs)

        result = self._api_call(
            f"content/{page_id}", data=data, http_method="PUT"
        )
        page = self._parse_page(result)
        if page.id:
            self._cached_pages[page.id] = page
        return page

    def delete_page(self, page_id: str) -> bool:
        self._api_call(f"content/{page_id}", http_method="DELETE")
        if page_id in self._cached_pages:
            del self._cached_pages[page_id]
        return True

    def list_pages(self, space_key: str = "",
                   title: str = "",
                   type: ConfluenceContentType = ConfluenceContentType.PAGE,
                   status: str = "current",
                   limit: int = 25,
                   start: int = 0,
                   expand: str = "version,space",
                   **kwargs: Any) -> Dict[str, Any]:
        params: Dict[str, Any] = {
            "type": type.value,
            "status": status,
            "limit": limit,
            "start": start,
            "expand": expand,
        }
        if space_key:
            params["spaceKey"] = space_key
        if title:
            params["title"] = title
        params.update(kwargs)

        result = self._api_call("content", params=params)
        pages_data = result.get("results", [])
        pages = [self._parse_page(p) for p in pages_data]
        for page in pages:
            if page.id:
                self._cached_pages[page.id] = page

        return {
            "pages": pages,
            "size": result.get("size", 0),
            "limit": result.get("limit", 0),
            "start": result.get("start", 0),
            "_links": result.get("_links", {}),
        }

    def _parse_page(self, data: Dict[str, Any]) -> ConfluencePage:
        space = data.get("space", {})
        version = data.get("version", {})
        body = data.get("body", {})
        ancestors = data.get("ancestors", [])
        history = data.get("history", {})
        links = data.get("_links", {})

        storage = body.get("storage", {})
        view = body.get("view", {})
        editor = body.get("editor", {})

        parent = ancestors[-1] if ancestors else {}

        return ConfluencePage(
            id=data.get("id", ""),
            type=ConfluenceContentType(data.get("type", "page")),
            title=data.get("title", ""),
            status=ConfluenceContentStatus(data.get("status", "current")),
            space_key=space.get("key", "") if space else "",
            space_name=space.get("name", "") if space else "",
            version_number=version.get("number", 0),
            version_message=version.get("message", ""),
            version_when=version.get("when", ""),
            version_by=version.get("by", {}),
            body_storage=storage.get("value", ""),
            body_view=view.get("value", ""),
            body_editor=editor.get("value", ""),
            parent_id=parent.get("id", "") if parent else "",
            parent_title=parent.get("title", "") if parent else "",
            created_at=history.get("createdDate", ""),
            created_by=history.get("createdBy", {}),
            history_latest=history.get("latest", True),
            webui_url=links.get("webui", ""),
            tinyui_url=links.get("tinyui", ""),
            _links=links,
            extra=data,
        )

    # ------------------------------------------------------------------
    # Spaces
    # ------------------------------------------------------------------

    def create_space(self, key: str,
                     name: str,
                     description: str = "",
                     space_type: ConfluenceSpaceType = ConfluenceSpaceType.GLOBAL,
                     **kwargs: Any) -> ConfluenceSpace:
        data: Dict[str, Any] = {
            "key": key,
            "name": name,
            "type": space_type.value,
        }
        if description:
            data["description"] = {
                "plain": {
                    "value": description,
                    "representation": "plain",
                }
            }
        data.update(kwargs)

        result = self._api_call("space", data=data, http_method="POST")
        space = self._parse_space(result)
        if space.key:
            self._cached_spaces[space.key] = space
        return space

    def get_space(self, space_key: str,
                  expand: str = "description,icon,homepage") -> ConfluenceSpace:
        if space_key in self._cached_spaces:
            return self._cached_spaces[space_key]

        params = {"expand": expand}
        result = self._api_call(f"space/{space_key}", params=params)
        space = self._parse_space(result)
        if space.key:
            self._cached_spaces[space.key] = space
        return space

    def update_space(self, space_key: str,
                     name: Optional[str] = None,
                     description: Optional[str] = None,
                     status: Optional[ConfluenceSpaceStatus] = None,
                     **kwargs: Any) -> ConfluenceSpace:
        data: Dict[str, Any] = {}
        if name is not None:
            data["name"] = name
        if description is not None:
            data["description"] = {
                "plain": {
                    "value": description,
                    "representation": "plain",
                }
            }
        if status is not None:
            data["status"] = status.value
        data.update(kwargs)

        result = self._api_call(
            f"space/{space_key}", data=data, http_method="PUT"
        )
        space = self._parse_space(result)
        if space.key:
            self._cached_spaces[space.key] = space
        return space

    def delete_space(self, space_key: str) -> bool:
        self._api_call(f"space/{space_key}", http_method="DELETE")
        if space_key in self._cached_spaces:
            del self._cached_spaces[space_key]
        return True

    def list_spaces(self, space_type: str = "global",
                    status: str = "current",
                    limit: int = 25,
                    start: int = 0,
                    **kwargs: Any) -> Dict[str, Any]:
        params: Dict[str, Any] = {
            "type": space_type,
            "status": status,
            "limit": limit,
            "start": start,
        }
        params.update(kwargs)

        result = self._api_call("space", params=params)
        spaces_data = result.get("results", [])
        spaces = [self._parse_space(s) for s in spaces_data]
        for space in spaces:
            if space.key:
                self._cached_spaces[space.key] = space

        return {
            "spaces": spaces,
            "size": result.get("size", 0),
            "limit": result.get("limit", 0),
            "start": result.get("start", 0),
        }

    def _parse_space(self, data: Dict[str, Any]) -> ConfluenceSpace:
        description = data.get("description", {})
        homepage = data.get("homepage", {})
        links = data.get("_links", {})
        return ConfluenceSpace(
            id=data.get("id", 0),
            key=data.get("key", ""),
            name=data.get("name", ""),
            type=ConfluenceSpaceType(data.get("type", "global")),
            status=ConfluenceSpaceStatus(data.get("status", "current")),
            description=description,
            icon=data.get("icon", {}),
            homepage_id=homepage.get("id", "") if homepage else "",
            created_at=data.get("createdAt", ""),
            author=data.get("author", {}),
            _links=links,
            extra=data,
        )

    # ------------------------------------------------------------------
    # Content search
    # ------------------------------------------------------------------

    def search(self, cql: str,
               limit: int = 25,
               start: int = 0,
               expand: str = "version,space",
               **kwargs: Any) -> Dict[str, Any]:
        params: Dict[str, Any] = {
            "cql": cql,
            "limit": limit,
            "start": start,
            "expand": expand,
        }
        params.update(kwargs)

        result = self._api_call("content/search", params=params)
        results_data = result.get("results", [])
        search_results = []
        for r in results_data:
            content = r.get("content", r)
            if content.get("type") in ("page", "blogpost"):
                page = self._parse_page(content)
                search_results.append(ConfluenceSearchResult(
                    content=page,
                    title=r.get("title", content.get("title", "")),
                    excerpt=r.get("excerpt", ""),
                    url=r.get("url", ""),
                    result_type=r.get("resultType", "content"),
                    result_global_container_title=r.get(
                        "resultGlobalContainerTitle", ""
                    ),
                    extra=r,
                ))

        return {
            "results": search_results,
            "size": result.get("size", 0),
            "limit": result.get("limit", 0),
            "start": result.get("start", 0),
            "total_size": result.get("totalSize", 0),
        }

    def search_by_title(self, title: str,
                        space_key: str = "",
                        type: str = "page",
                        **kwargs: Any) -> Dict[str, Any]:
        cql = f'title="{title}"'
        if space_key:
            cql += f' and space="{space_key}"'
        cql += f' and type={type}'
        return self.search(cql, **kwargs)

    # ------------------------------------------------------------------
    # Attachments
    # ------------------------------------------------------------------

    def create_attachment(self, container_id: str,
                          filename: str,
                          file_data: bytes,
                          content_type: str = "application/octet-stream",
                          comment: str = "") -> ConfluenceAttachment:
        self._check_rate_limit()
        self._mark_request()

        url = self._build_url(f"content/{container_id}/child/attachment")
        boundary = "----LiuHaoConfluenceBoundary7MA4YWxkTrZu0gW"

        # Build multipart body
        parts = []
        parts.append(f"--{boundary}")
        parts.append(
            f'Content-Disposition: form-data; name="file"; filename="{filename}"'
        )
        parts.append(f"Content-Type: {content_type}")
        parts.append("")

        body = "\r\n".join(parts).encode("utf-8")
        body += file_data
        body += f"\r\n--{boundary}--\r\n".encode("utf-8")

        import base64
        username = self.config.username or ""
        password = self.config.password or self.config.api_key or ""
        credentials = f"{username}:{password}".encode("utf-8")
        encoded = base64.b64encode(credentials).decode("utf-8")

        headers = {
            "Authorization": f"Basic {encoded}",
            "X-Atlassian-Token": "no-check",
            "Content-Type": f"multipart/form-data; boundary={boundary}",
        }
        if comment:
            headers["X-Comment"] = comment

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
                results = result.get("results", [])
                if results:
                    return self._parse_attachment(results[0])
                raise self._handle_error("Upload failed: no results")
        except Exception as e:
            raise self._handle_error(f"Attachment upload error: {e}")

    def list_attachments(self, container_id: str,
                         filename: str = "",
                         media_type: str = "",
                         limit: int = 25,
                         start: int = 0,
                         expand: str = "version",
                         **kwargs: Any) -> Dict[str, Any]:
        params: Dict[str, Any] = {
            "limit": limit,
            "start": start,
            "expand": expand,
        }
        if filename:
            params["filename"] = filename
        if media_type:
            params["mediaType"] = media_type
        params.update(kwargs)

        result = self._api_call(
            f"content/{container_id}/child/attachment", params=params
        )
        attachments_data = result.get("results", [])
        return {
            "attachments": [
                self._parse_attachment(a) for a in attachments_data
            ],
            "size": result.get("size", 0),
            "limit": result.get("limit", 0),
            "start": result.get("start", 0),
        }

    def _parse_attachment(self, data: Dict[str, Any]) -> ConfluenceAttachment:
        version = data.get("version", {})
        links = data.get("_links", {})
        metadata = data.get("metadata", {})
        return ConfluenceAttachment(
            id=data.get("id", ""),
            type=ConfluenceContentType.ATTACHMENT,
            title=data.get("title", ""),
            status=ConfluenceContentStatus(data.get("status", "current")),
            media_type=metadata.get("mediaType", ""),
            file_size=int(metadata.get("size", 0)) if metadata.get("size") else 0,
            version_number=version.get("number", 0),
            comment=version.get("message", ""),
            download_url=links.get("download", ""),
            webui_url=links.get("webui", ""),
            created_at=data.get("history", {}).get("createdDate", ""),
            created_by=data.get("history", {}).get("createdBy", {}),
            extra=data,
        )

    # ------------------------------------------------------------------
    # Comments
    # ------------------------------------------------------------------

    def create_comment(self, container_id: str,
                       body_storage: str,
                       parent_comment_id: str = "") -> ConfluenceComment:
        data: Dict[str, Any] = {
            "type": "comment",
            "container": {"id": container_id, "type": "page"},
            "body": {
                "storage": {
                    "value": body_storage,
                    "representation": "storage",
                }
            },
        }
        if parent_comment_id:
            data["ancestors"] = [{"id": parent_comment_id}]

        result = self._api_call("content", data=data, http_method="POST")
        return ConfluenceComment(
            id=result.get("id", ""),
            title=result.get("title", ""),
            status=ConfluenceContentStatus(result.get("status", "current")),
            container_id=container_id,
            created_at=result.get("history", {}).get("createdDate", ""),
            created_by=result.get("history", {}).get("createdBy", {}),
            version_number=result.get("version", {}).get("number", 0),
            extra=result,
        )

    def list_comments(self, container_id: str,
                      limit: int = 25,
                      start: int = 0,
                      expand: str = "body.storage",
                      **kwargs: Any) -> Dict[str, Any]:
        params: Dict[str, Any] = {
            "limit": limit,
            "start": start,
            "expand": expand,
        }
        params.update(kwargs)

        result = self._api_call(
            f"content/{container_id}/child/comment", params=params
        )
        comments_data = result.get("results", [])
        return {
            "comments": [
                ConfluenceComment(
                    id=c.get("id", ""),
                    title=c.get("title", ""),
                    body=c.get("body", {}).get("storage", {}).get("value", ""),
                    status=ConfluenceContentStatus(c.get("status", "current")),
                    container_id=container_id,
                    created_at=c.get("history", {}).get("createdDate", ""),
                    created_by=c.get("history", {}).get("createdBy", {}),
                    version_number=c.get("version", {}).get("number", 0),
                    extra=c,
                )
                for c in comments_data
            ],
            "size": result.get("size", 0),
            "limit": result.get("limit", 0),
            "start": result.get("start", 0),
        }

    def delete_comment(self, comment_id: str) -> bool:
        self._api_call(f"content/{comment_id}", http_method="DELETE")
        return True

    # ------------------------------------------------------------------
    # Labels
    # ------------------------------------------------------------------

    def add_labels(self, content_id: str,
                   labels: List[str],
                   prefix: str = "global") -> List[ConfluenceLabel]:
        data = [{"prefix": prefix, "name": label} for label in labels]
        result = self._api_call(
            f"content/{content_id}/label",
            data=data,
            http_method="POST",
        )
        results_data = result.get("results", [])
        return [
            ConfluenceLabel(
                label=l.get("label", ""),
                name=l.get("name", ""),
                prefix=l.get("prefix", "global"),
                id=l.get("id", ""),
                extra=l,
            )
            for l in results_data
        ]

    def list_labels(self, content_id: str,
                    prefix: str = "global",
                    limit: int = 200,
                    start: int = 0) -> Dict[str, Any]:
        params = {"prefix": prefix, "limit": limit, "start": start}
        result = self._api_call(
            f"content/{content_id}/label", params=params
        )
        labels_data = result.get("results", [])
        return {
            "labels": [
                ConfluenceLabel(
                    label=l.get("label", ""),
                    name=l.get("name", ""),
                    prefix=l.get("prefix", "global"),
                    id=l.get("id", ""),
                    extra=l,
                )
                for l in labels_data
            ],
            "size": result.get("size", 0),
            "limit": result.get("limit", 0),
            "start": result.get("start", 0),
        }

    def remove_label(self, content_id: str, label_name: str) -> bool:
        self._api_call(
            f"content/{content_id}/label/{label_name}",
            http_method="DELETE",
        )
        return True

    # ------------------------------------------------------------------
    # Templates
    # ------------------------------------------------------------------

    def list_content_templates(self, space_key: str = "",
                               limit: int = 25,
                               start: int = 0,
                               **kwargs: Any) -> Dict[str, Any]:
        params: Dict[str, Any] = {"limit": limit, "start": start}
        if space_key:
            params["spaceKey"] = space_key
        params.update(kwargs)

        result = self._api_call("template/page", params=params)
        templates_data = result.get("results", [])
        return {
            "templates": [
                ConfluenceTemplate(
                    template_id=t.get("templateId", ""),
                    name=t.get("name", ""),
                    description=t.get("description", ""),
                    body=t.get("body", {}),
                    labels=t.get("labels", []),
                    space_key=t.get("space", {}).get("key", ""),
                    extra=t,
                )
                for t in templates_data
            ],
            "size": result.get("size", 0),
            "limit": result.get("limit", 0),
            "start": result.get("start", 0),
        }

    # ------------------------------------------------------------------
    # Users and groups
    # ------------------------------------------------------------------

    def get_current_user(self) -> ConfluenceUser:
        result = self._api_call("user/current")
        return self._parse_user(result)

    def get_user(self, account_id: str = "",
                 key: str = "",
                 username: str = "") -> ConfluenceUser:
        params: Dict[str, Any] = {}
        if account_id:
            params["accountId"] = account_id
        if key:
            params["key"] = key
        if username:
            params["username"] = username

        result = self._api_call("user", params=params)
        return self._parse_user(result)

    def search_users(self, cql: str = "",
                     limit: int = 25,
                     start: int = 0,
                     **kwargs: Any) -> Dict[str, Any]:
        params: Dict[str, Any] = {"limit": limit, "start": start}
        if cql:
            params["cql"] = cql
        params.update(kwargs)

        result = self._api_call("search/user", params=params)
        users_data = result.get("results", [])
        return {
            "users": [self._parse_user(u.get("user", u)) for u in users_data],
            "size": result.get("size", 0),
            "limit": result.get("limit", 0),
            "start": result.get("start", 0),
            "total_size": result.get("totalSize", 0),
        }

    def _parse_user(self, data: Dict[str, Any]) -> ConfluenceUser:
        return ConfluenceUser(
            account_id=data.get("accountId", ""),
            account_type=data.get("accountType", "atlassian"),
            email=data.get("email", ""),
            display_name=data.get("displayName", ""),
            nickname=data.get("nickname", ""),
            profile_picture=data.get("profilePicture", {}),
            public_name=data.get("publicName", ""),
            extra=data,
        )

    def list_groups(self, limit: int = 25,
                    start: int = 0,
                    access_type: str = "",
                    **kwargs: Any) -> Dict[str, Any]:
        params: Dict[str, Any] = {"limit": limit, "start": start}
        if access_type:
            params["accessType"] = access_type
        params.update(kwargs)

        result = self._api_call("group", params=params)
        groups_data = result.get("results", [])
        return {
            "groups": [
                ConfluenceGroup(
                    name=g.get("name", ""),
                    type=g.get("type", "group"),
                    extra=g,
                )
                for g in groups_data
            ],
            "size": result.get("size", 0),
            "limit": result.get("limit", 0),
            "start": result.get("start", 0),
        }

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

        import base64
        username = self.config.username or ""
        password = self.config.password or self.config.api_key or ""
        credentials = f"{username}:{password}".encode("utf-8")
        encoded = base64.b64encode(credentials).decode("utf-8")

        headers = {
            "Authorization": f"Basic {encoded}",
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
                if response_body:
                    return json.loads(response_body)
                return {}

        except urllib.error.HTTPError as e:
            response_body = e.read().decode("utf-8", errors="replace")
            self._log_response(http_method, url, e.code, response_body)
            if e.code == 429:
                self._rate_limit_hits += 1
                raise RateLimitError(
                    "Rate limited by Confluence",
                    retry_after=60,
                    adapter_name=self.config.name,
                )
            if e.code in (401, 403):
                raise self._handle_error(
                    "Authentication failed: invalid Confluence credentials",
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
            f"<ConfluenceAdapter name={self.config.name} "
            f"status={self._status.value} "
            f"user={self._user_display_name or 'unknown'}>"
        )
