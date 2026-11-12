"""
WordPress Integration Adapter for LiuHao AI OS.

Provides integration with WordPress via REST API for posts, pages,
media upload, categories, tags, comments, and users.
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


class PostStatus(str, Enum):
    """WordPress post statuses."""
    PUBLISH = "publish"
    FUTURE = "future"
    DRAFT = "draft"
    PENDING = "pending"
    PRIVATE = "private"
    TRASH = "trash"


class PostType(str, Enum):
    """WordPress post types."""
    POST = "post"
    PAGE = "page"
    ATTACHMENT = "attachment"
    REVISION = "revision"
    NAV_MENU_ITEM = "nav_menu_item"
    CUSTOM_CSS = "custom_css"
    CUSTOMIZE_CHANGESSET = "customize_changeset"


class CommentStatus(str, Enum):
    """Comment statuses."""
    APPROVED = "approved"
    PENDING = "pending"
    SPAM = "spam"
    TRASH = "trash"


class CommentType(str, Enum):
    """Comment types."""
    COMMENT = "comment"
    PINGBACK = "pingback"
    TRACKBACK = "trackback"


@dataclass
class WPPost:
    """Represents a WordPress post."""
    id: int
    title: str
    content: str = ""
    excerpt: str = ""
    status: str = "draft"
    post_type: str = "post"
    slug: str = ""
    link: str = ""
    author_id: int = 0
    categories: List[int] = field(default_factory=list)
    tags: List[int] = field(default_factory=list)
    featured_media: int = 0
    format: str = "standard"
    comment_status: str = "open"
    ping_status: str = "open"
    date_gmt: str = ""
    modified_gmt: str = ""
    sticky: bool = False
    template: str = ""
    extra: Dict[str, Any] = field(default_factory=dict)


@dataclass
class WPPage:
    """Represents a WordPress page."""
    id: int
    title: str
    content: str = ""
    excerpt: str = ""
    status: str = "draft"
    slug: str = ""
    link: str = ""
    author_id: int = 0
    parent_id: int = 0
    featured_media: int = 0
    menu_order: int = 0
    comment_status: str = "open"
    ping_status: str = "open"
    date_gmt: str = ""
    modified_gmt: str = ""
    template: str = ""
    extra: Dict[str, Any] = field(default_factory=dict)


@dataclass
class WPMedia:
    """Represents a media attachment."""
    id: int
    title: str
    source_url: str = ""
    media_type: str = "image"
    mime_type: str = ""
    description: str = ""
    caption: str = ""
    alt_text: str = ""
    author_id: int = 0
    post_id: int = 0
    slug: str = ""
    link: str = ""
    date_gmt: str = ""
    media_details: Dict[str, Any] = field(default_factory=dict)
    extra: Dict[str, Any] = field(default_factory=dict)


@dataclass
class WPCategory:
    """Represents a category."""
    id: int
    name: str
    slug: str = ""
    description: str = ""
    parent: int = 0
    count: int = 0
    link: str = ""
    taxonomy: str = "category"
    extra: Dict[str, Any] = field(default_factory=dict)


@dataclass
class WPTag:
    """Represents a tag."""
    id: int
    name: str
    slug: str = ""
    description: str = ""
    count: int = 0
    link: str = ""
    taxonomy: str = "post_tag"
    extra: Dict[str, Any] = field(default_factory=dict)


@dataclass
class WPComment:
    """Represents a comment."""
    id: int
    content: str
    post_id: int = 0
    author_name: str = ""
    author_email: str = ""
    author_url: str = ""
    author_ip: str = ""
    status: str = "pending"
    type_: str = "comment"
    parent: int = 0
    author_avatar_urls: Dict[str, str] = field(default_factory=dict)
    date_gmt: str = ""
    link: str = ""
    extra: Dict[str, Any] = field(default_factory=dict)


@dataclass
class WPUser:
    """Represents a WordPress user."""
    id: int
    username: str
    name: str = ""
    email: str = ""
    url: str = ""
    description: str = ""
    link: str = ""
    slug: str = ""
    roles: List[str] = field(default_factory=list)
    registered_date: str = ""
    avatar_urls: Dict[str, str] = field(default_factory=dict)
    extra: Dict[str, Any] = field(default_factory=dict)


class WordPressAdapter(BaseIntegrationAdapter):
    """
    WordPress integration adapter for LiuHao AI OS.

    Supports WordPress REST API with:
    - Posts CRUD (create, list, update, delete)
    - Pages CRUD
    - Media upload and management
    - Categories and tags (taxonomies)
    - Comments management
    - Users management
    - Full REST API interface with authentication
    """

    def __init__(self, config: IntegrationConfig):
        """
        Initialize WordPress adapter.

        Uses Application Passwords or Basic Auth for authentication.

        Args:
            config: Integration config.
                    base_url: WordPress site URL (e.g., https://example.com/wp-json/)
                    username: WordPress username
                    password: Application Password or regular password
                    auth_type: BASIC_AUTH (recommended with app passwords)
        """
        if config.auth_type == AuthType.NONE:
            config.auth_type = AuthType.BASIC_AUTH
        if config.base_url and not config.base_url.endswith("/"):
            config.base_url += "/"
        super().__init__(config)
        self._site_title: Optional[str] = None
        self._site_url: Optional[str] = None
        self._current_user_id: Optional[int] = None

    # ------------------------------------------------------------------
    # Base interface
    # ------------------------------------------------------------------

    def connect(self) -> bool:
        """Connect to WordPress and validate credentials."""
        self._status = ConnectionStatus.CONNECTING
        self._logger.info("Connecting to WordPress REST API...")

        try:
            # Get site info
            try:
                site_info = self._api_call("GET", "")
                self._site_title = site_info.get("name")
                self._site_url = site_info.get("url")
            except Exception:
                pass

            # Validate credentials by fetching current user
            user_data = self._api_call("GET", "wp/v2/users/me")
            self._current_user_id = user_data.get("id")
            self._status = ConnectionStatus.CONNECTED
            self._connected_at = time.time()
            self._logger.info(
                "Connected to WordPress as %s",
                user_data.get("username", ""),
            )
            return True
        except IntegrationError:
            raise
        except Exception as e:
            self._status = ConnectionStatus.ERROR
            raise self._handle_error(f"Connection error: {e}")

    def disconnect(self) -> bool:
        """Disconnect from WordPress."""
        self._status = ConnectionStatus.DISCONNECTED
        self._site_title = None
        self._site_url = None
        self._current_user_id = None
        self._session = None
        self._logger.info("Disconnected from WordPress")
        return True

    def is_connected(self) -> bool:
        return self._status == ConnectionStatus.CONNECTED

    def get_status(self) -> IntegrationStatus:
        return self._build_status({
            "site_title": self._site_title,
            "site_url": self._site_url,
            "current_user_id": self._current_user_id,
        })

    def test_connection(self) -> bool:
        try:
            if not self.is_connected():
                return False
            result = self._api_call("GET", "wp/v2/users/me")
            return "id" in result
        except Exception:
            return False

    # ------------------------------------------------------------------
    # Posts
    # ------------------------------------------------------------------

    def list_posts(self,
                   page: int = 1,
                   per_page: int = 10,
                   search: Optional[str] = None,
                   status: Optional[PostStatus] = None,
                   categories: Optional[List[int]] = None,
                   tags: Optional[List[int]] = None,
                   author: Optional[int] = None,
                   order: str = "desc",
                   orderby: str = "date",
                   after: Optional[str] = None,
                   before: Optional[str] = None,
                   slug: Optional[str] = None,
                   ) -> Tuple[List[WPPost], int, int]:
        """
        List posts with pagination and filters.

        Returns:
            Tuple of (posts list, total_count, total_pages).
        """
        params: Dict[str, Any] = {"page": page, "per_page": per_page,
                                  "order": order, "orderby": orderby}
        if search:
            params["search"] = search
        if status:
            params["status"] = status.value
        if categories:
            params["categories"] = ",".join(str(c) for c in categories)
        if tags:
            params["tags"] = ",".join(str(t) for t in tags)
        if author:
            params["author"] = author
        if after:
            params["after"] = after
        if before:
            params["before"] = before
        if slug:
            params["slug"] = slug

        data, headers = self._api_call_with_headers("GET", "wp/v2/posts", params=params)
        posts = [self._parse_post(p) for p in data]
        total = int(headers.get("X-WP-Total", 0))
        total_pages = int(headers.get("X-WP-TotalPages", 0))
        return posts, total, total_pages

    def get_post(self, post_id: int,
                 context: str = "view") -> WPPost:
        """Get a post by ID."""
        data = self._api_call(
            "GET", f"wp/v2/posts/{post_id}",
            params={"context": context},
        )
        return self._parse_post(data)

    def get_post_by_slug(self, slug: str) -> Optional[WPPost]:
        """Get a post by slug."""
        posts, _, _ = self.list_posts(slug=slug, per_page=1)
        return posts[0] if posts else None

    def create_post(self, title: str,
                    content: str = "",
                    status: str = "draft",
                    excerpt: str = "",
                    categories: Optional[List[int]] = None,
                    tags: Optional[List[int]] = None,
                    featured_media: Optional[int] = None,
                    slug: Optional[str] = None,
                    date_gmt: Optional[str] = None,
                    format_: str = "standard",
                    comment_status: Optional[str] = None,
                    sticky: bool = False,
                    **kwargs: Any) -> WPPost:
        """Create a new post."""
        payload: Dict[str, Any] = {
            "title": title,
            "content": content,
            "status": status,
            "excerpt": excerpt,
            "format": format_,
            "sticky": sticky,
        }
        if categories:
            payload["categories"] = categories
        if tags:
            payload["tags"] = tags
        if featured_media is not None:
            payload["featured_media"] = featured_media
        if slug:
            payload["slug"] = slug
        if date_gmt:
            payload["date_gmt"] = date_gmt
        if comment_status:
            payload["comment_status"] = comment_status
        if kwargs:
            payload.update(kwargs)

        data = self._api_call("POST", "wp/v2/posts", json_body=payload)
        return self._parse_post(data)

    def update_post(self, post_id: int, **kwargs: Any) -> WPPost:
        """Update a post."""
        data = self._api_call(
            "POST", f"wp/v2/posts/{post_id}", json_body=kwargs,
        )
        return self._parse_post(data)

    def delete_post(self, post_id: int, force: bool = False) -> bool:
        """
        Delete a post.

        Args:
            post_id: Post ID.
            force: If True, permanently delete (skip trash).

        Returns:
            True on success.
        """
        try:
            params = {"force": "true"} if force else {}
            self._api_call(
                "DELETE", f"wp/v2/posts/{post_id}",
                params=params if params else None,
            )
            return True
        except Exception:
            return False

    # ------------------------------------------------------------------
    # Pages
    # ------------------------------------------------------------------

    def list_pages(self,
                   page: int = 1,
                   per_page: int = 10,
                   search: Optional[str] = None,
                   status: Optional[PostStatus] = None,
                   author: Optional[int] = None,
                   parent: Optional[int] = None,
                   order: str = "desc",
                   orderby: str = "date",
                   ) -> Tuple[List[WPPage], int, int]:
        """List pages."""
        params: Dict[str, Any] = {"page": page, "per_page": per_page,
                                  "order": order, "orderby": orderby}
        if search:
            params["search"] = search
        if status:
            params["status"] = status.value
        if author:
            params["author"] = author
        if parent is not None:
            params["parent"] = parent

        data, headers = self._api_call_with_headers(
            "GET", "wp/v2/pages", params=params,
        )
        pages = [self._parse_page(p) for p in data]
        total = int(headers.get("X-WP-Total", 0))
        total_pages = int(headers.get("X-WP-TotalPages", 0))
        return pages, total, total_pages

    def get_page(self, page_id: int,
                 context: str = "view") -> WPPage:
        """Get a page by ID."""
        data = self._api_call(
            "GET", f"wp/v2/pages/{page_id}",
            params={"context": context},
        )
        return self._parse_page(data)

    def create_page(self, title: str,
                    content: str = "",
                    status: str = "draft",
                    excerpt: str = "",
                    parent_id: int = 0,
                    featured_media: Optional[int] = None,
                    menu_order: int = 0,
                    slug: Optional[str] = None,
                    template: str = "",
                    **kwargs: Any) -> WPPage:
        """Create a new page."""
        payload: Dict[str, Any] = {
            "title": title,
            "content": content,
            "status": status,
            "excerpt": excerpt,
            "parent": parent_id,
            "menu_order": menu_order,
            "template": template,
        }
        if featured_media is not None:
            payload["featured_media"] = featured_media
        if slug:
            payload["slug"] = slug
        if kwargs:
            payload.update(kwargs)

        data = self._api_call("POST", "wp/v2/pages", json_body=payload)
        return self._parse_page(data)

    def update_page(self, page_id: int, **kwargs: Any) -> WPPage:
        """Update a page."""
        data = self._api_call(
            "POST", f"wp/v2/pages/{page_id}", json_body=kwargs,
        )
        return self._parse_page(data)

    def delete_page(self, page_id: int, force: bool = False) -> bool:
        """Delete a page. Returns True on success."""
        try:
            params = {"force": "true"} if force else {}
            self._api_call(
                "DELETE", f"wp/v2/pages/{page_id}",
                params=params if params else None,
            )
            return True
        except Exception:
            return False

    # ------------------------------------------------------------------
    # Media upload
    # ------------------------------------------------------------------

    def list_media(self,
                   page: int = 1,
                   per_page: int = 10,
                   search: Optional[str] = None,
                   media_type: Optional[str] = None,
                   mime_type: Optional[str] = None,
                   author: Optional[int] = None,
                   order: str = "desc",
                   orderby: str = "date",
                   ) -> Tuple[List[WPMedia], int, int]:
        """List media items."""
        params: Dict[str, Any] = {"page": page, "per_page": per_page,
                                  "order": order, "orderby": orderby}
        if search:
            params["search"] = search
        if media_type:
            params["media_type"] = media_type
        if mime_type:
            params["mime_type"] = mime_type
        if author:
            params["author"] = author

        data, headers = self._api_call_with_headers(
            "GET", "wp/v2/media", params=params,
        )
        media = [self._parse_media(m) for m in data]
        total = int(headers.get("X-WP-Total", 0))
        total_pages = int(headers.get("X-WP-TotalPages", 0))
        return media, total, total_pages

    def get_media(self, media_id: int,
                  context: str = "view") -> WPMedia:
        """Get a media item by ID."""
        data = self._api_call(
            "GET", f"wp/v2/media/{media_id}",
            params={"context": context},
        )
        return self._parse_media(data)

    def upload_media(self, filename: str,
                     file_content: bytes,
                     title: Optional[str] = None,
                     caption: str = "",
                     description: str = "",
                     alt_text: str = "",
                     post_id: Optional[int] = None
                     ) -> WPMedia:
        """
        Upload a media file.

        Args:
            filename: Name of the file.
            file_content: Raw file bytes.
            title: Attachment title (defaults to filename).
            caption: Media caption.
            description: Media description.
            alt_text: Alt text for images.
            post_id: Optional post ID to attach to.

        Returns:
            Created WPMedia object.
        """
        url = f"{self.config.base_url}wp/v2/media"
        if post_id:
            url += f"?post={post_id}"

        # Build multipart form data
        boundary = f"----LiuHaoBoundary{int(time.time())}"
        body_parts: List[bytes] = []

        # File part
        body_parts.append(f"--{boundary}\r\n".encode("utf-8"))
        body_parts.append(
            f'Content-Disposition: form-data; name="file"; '
            f'filename="{filename}"\r\n'.encode("utf-8")
        )
        body_parts.append(b"Content-Type: application/octet-stream\r\n\r\n")
        body_parts.append(file_content)
        body_parts.append(b"\r\n")

        # Title part
        final_title = title or filename
        body_parts.append(f"--{boundary}\r\n".encode("utf-8"))
        body_parts.append(
            b'Content-Disposition: form-data; name="title"\r\n\r\n'
        )
        body_parts.append(final_title.encode("utf-8"))
        body_parts.append(b"\r\n")

        if caption:
            body_parts.append(f"--{boundary}\r\n".encode("utf-8"))
            body_parts.append(
                b'Content-Disposition: form-data; name="caption"\r\n\r\n'
            )
            body_parts.append(caption.encode("utf-8"))
            body_parts.append(b"\r\n")

        if description:
            body_parts.append(f"--{boundary}\r\n".encode("utf-8"))
            body_parts.append(
                b'Content-Disposition: form-data; name="description"\r\n\r\n'
            )
            body_parts.append(description.encode("utf-8"))
            body_parts.append(b"\r\n")

        if alt_text:
            body_parts.append(f"--{boundary}\r\n".encode("utf-8"))
            body_parts.append(
                b'Content-Disposition: form-data; name="alt_text"\r\n\r\n'
            )
            body_parts.append(alt_text.encode("utf-8"))
            body_parts.append(b"\r\n")

        body_parts.append(f"--{boundary}--\r\n".encode("utf-8"))
        body = b"".join(body_parts)

        self._check_rate_limit()
        self._mark_request()

        token = self._get_auth_token()
        headers = {
            "Authorization": token,
            "Content-Type": f"multipart/form-data; boundary={boundary}",
            "Content-Disposition": f'form-data; filename="{filename}"',
            "Accept": "application/json",
        }

        self._log_request("POST", url, headers, f"<{len(file_content)} bytes>")

        try:
            import urllib.request
            req = urllib.request.Request(
                url, data=body, headers=headers, method="POST",
            )
            with urllib.request.urlopen(req, timeout=self.config.timeout) as resp:
                resp_body = resp.read().decode("utf-8")
                self._log_response("POST", url, resp.status, resp_body)
                data = json.loads(resp_body)
                return self._parse_media(data)
        except urllib.error.HTTPError as e:
            resp_body = e.read().decode("utf-8", errors="replace")
            raise self._handle_error(
                f"Media upload failed: {e.code}",
                status_code=e.code,
                response_body=resp_body,
            )

    def update_media(self, media_id: int, **kwargs: Any) -> WPMedia:
        """Update media metadata."""
        data = self._api_call(
            "POST", f"wp/v2/media/{media_id}", json_body=kwargs,
        )
        return self._parse_media(data)

    def delete_media(self, media_id: int, force: bool = True) -> bool:
        """Delete a media item. Returns True on success."""
        try:
            self._api_call(
                "DELETE", f"wp/v2/media/{media_id}",
                params={"force": "true"},
            )
            return True
        except Exception:
            return False

    # ------------------------------------------------------------------
    # Categories
    # ------------------------------------------------------------------

    def list_categories(self,
                        page: int = 1,
                        per_page: int = 10,
                        search: Optional[str] = None,
                        parent: Optional[int] = None,
                        order: str = "asc",
                        orderby: str = "name",
                        hide_empty: bool = False,
                        ) -> Tuple[List[WPCategory], int, int]:
        """List categories."""
        params: Dict[str, Any] = {"page": page, "per_page": per_page,
                                  "order": order, "orderby": orderby}
        if search:
            params["search"] = search
        if parent is not None:
            params["parent"] = parent
        if hide_empty:
            params["hide_empty"] = "true"

        data, headers = self._api_call_with_headers(
            "GET", "wp/v2/categories", params=params,
        )
        cats = [self._parse_category(c) for c in data]
        total = int(headers.get("X-WP-Total", 0))
        total_pages = int(headers.get("X-WP-TotalPages", 0))
        return cats, total, total_pages

    def get_category(self, category_id: int) -> WPCategory:
        """Get a category by ID."""
        data = self._api_call("GET", f"wp/v2/categories/{category_id}")
        return self._parse_category(data)

    def create_category(self, name: str,
                        description: str = "",
                        parent: int = 0,
                        slug: Optional[str] = None) -> WPCategory:
        """Create a new category."""
        payload: Dict[str, Any] = {"name": name, "description": description,
                                   "parent": parent}
        if slug:
            payload["slug"] = slug

        data = self._api_call("POST", "wp/v2/categories", json_body=payload)
        return self._parse_category(data)

    def update_category(self, category_id: int, **kwargs: Any) -> WPCategory:
        """Update a category."""
        data = self._api_call(
            "POST", f"wp/v2/categories/{category_id}", json_body=kwargs,
        )
        return self._parse_category(data)

    def delete_category(self, category_id: int, force: bool = True) -> bool:
        """Delete a category. Returns True on success."""
        try:
            self._api_call(
                "DELETE", f"wp/v2/categories/{category_id}",
                params={"force": "true"},
            )
            return True
        except Exception:
            return False

    # ------------------------------------------------------------------
    # Tags
    # ------------------------------------------------------------------

    def list_tags(self,
                  page: int = 1,
                  per_page: int = 10,
                  search: Optional[str] = None,
                  order: str = "asc",
                  orderby: str = "name",
                  hide_empty: bool = False,
                  ) -> Tuple[List[WPTag], int, int]:
        """List tags."""
        params: Dict[str, Any] = {"page": page, "per_page": per_page,
                                  "order": order, "orderby": orderby}
        if search:
            params["search"] = search
        if hide_empty:
            params["hide_empty"] = "true"

        data, headers = self._api_call_with_headers(
            "GET", "wp/v2/tags", params=params,
        )
        tags = [self._parse_tag(t) for t in data]
        total = int(headers.get("X-WP-Total", 0))
        total_pages = int(headers.get("X-WP-TotalPages", 0))
        return tags, total, total_pages

    def get_tag(self, tag_id: int) -> WPTag:
        """Get a tag by ID."""
        data = self._api_call("GET", f"wp/v2/tags/{tag_id}")
        return self._parse_tag(data)

    def create_tag(self, name: str,
                   description: str = "",
                   slug: Optional[str] = None) -> WPTag:
        """Create a new tag."""
        payload: Dict[str, Any] = {"name": name, "description": description}
        if slug:
            payload["slug"] = slug

        data = self._api_call("POST", "wp/v2/tags", json_body=payload)
        return self._parse_tag(data)

    def update_tag(self, tag_id: int, **kwargs: Any) -> WPTag:
        """Update a tag."""
        data = self._api_call(
            "POST", f"wp/v2/tags/{tag_id}", json_body=kwargs,
        )
        return self._parse_tag(data)

    def delete_tag(self, tag_id: int, force: bool = True) -> bool:
        """Delete a tag. Returns True on success."""
        try:
            self._api_call(
                "DELETE", f"wp/v2/tags/{tag_id}",
                params={"force": "true"},
            )
            return True
        except Exception:
            return False

    # ------------------------------------------------------------------
    # Comments
    # ------------------------------------------------------------------

    def list_comments(self,
                      page: int = 1,
                      per_page: int = 10,
                      search: Optional[str] = None,
                      status: Optional[CommentStatus] = None,
                      post_id: Optional[int] = None,
                      author_email: Optional[str] = None,
                      type_: Optional[CommentType] = None,
                      order: str = "desc",
                      orderby: str = "date_gmt",
                      ) -> Tuple[List[WPComment], int, int]:
        """List comments."""
        params: Dict[str, Any] = {"page": page, "per_page": per_page,
                                  "order": order, "orderby": orderby}
        if search:
            params["search"] = search
        if status:
            params["status"] = status.value
        if post_id:
            params["post"] = post_id
        if author_email:
            params["author_email"] = author_email
        if type_:
            params["type"] = type_.value

        data, headers = self._api_call_with_headers(
            "GET", "wp/v2/comments", params=params,
        )
        comments = [self._parse_comment(c) for c in data]
        total = int(headers.get("X-WP-Total", 0))
        total_pages = int(headers.get("X-WP-TotalPages", 0))
        return comments, total, total_pages

    def get_comment(self, comment_id: int) -> WPComment:
        """Get a comment by ID."""
        data = self._api_call("GET", f"wp/v2/comments/{comment_id}")
        return self._parse_comment(data)

    def create_comment(self, content: str,
                       post_id: int,
                       author_name: str = "",
                       author_email: str = "",
                       author_url: str = "",
                       parent: int = 0,
                       status: str = "pending") -> WPComment:
        """Create a new comment."""
        payload: Dict[str, Any] = {
            "content": content,
            "post": post_id,
            "parent": parent,
            "status": status,
        }
        if author_name:
            payload["author_name"] = author_name
        if author_email:
            payload["author_email"] = author_email
        if author_url:
            payload["author_url"] = author_url

        data = self._api_call("POST", "wp/v2/comments", json_body=payload)
        return self._parse_comment(data)

    def update_comment(self, comment_id: int, **kwargs: Any) -> WPComment:
        """Update a comment."""
        data = self._api_call(
            "POST", f"wp/v2/comments/{comment_id}", json_body=kwargs,
        )
        return self._parse_comment(data)

    def delete_comment(self, comment_id: int, force: bool = False) -> bool:
        """Delete a comment. Returns True on success."""
        try:
            params = {"force": "true"} if force else {}
            self._api_call(
                "DELETE", f"wp/v2/comments/{comment_id}",
                params=params if params else None,
            )
            return True
        except Exception:
            return False

    # ------------------------------------------------------------------
    # Users
    # ------------------------------------------------------------------

    def list_users(self,
                   page: int = 1,
                   per_page: int = 10,
                   search: Optional[str] = None,
                   roles: Optional[List[str]] = None,
                   order: str = "asc",
                   orderby: str = "name",
                   ) -> Tuple[List[WPUser], int, int]:
        """List users."""
        params: Dict[str, Any] = {"page": page, "per_page": per_page,
                                  "order": order, "orderby": orderby}
        if search:
            params["search"] = search
        if roles:
            params["roles"] = ",".join(roles)

        data, headers = self._api_call_with_headers(
            "GET", "wp/v2/users", params=params,
        )
        users = [self._parse_user(u) for u in data]
        total = int(headers.get("X-WP-Total", 0))
        total_pages = int(headers.get("X-WP-TotalPages", 0))
        return users, total, total_pages

    def get_user(self, user_id: int,
                 context: str = "view") -> WPUser:
        """Get a user by ID."""
        data = self._api_call(
            "GET", f"wp/v2/users/{user_id}",
            params={"context": context},
        )
        return self._parse_user(data)

    def get_current_user(self) -> WPUser:
        """Get the currently authenticated user."""
        data = self._api_call("GET", "wp/v2/users/me")
        return self._parse_user(data)

    def create_user(self, username: str,
                    email: str,
                    password: str,
                    first_name: str = "",
                    last_name: str = "",
                    nickname: str = "",
                    description: str = "",
                    roles: Optional[List[str]] = None) -> WPUser:
        """Create a new user."""
        payload: Dict[str, Any] = {
            "username": username,
            "email": email,
            "password": password,
            "first_name": first_name,
            "last_name": last_name,
            "nickname": nickname,
            "description": description,
        }
        if roles:
            payload["roles"] = roles

        data = self._api_call("POST", "wp/v2/users", json_body=payload)
        return self._parse_user(data)

    def update_user(self, user_id: int, **kwargs: Any) -> WPUser:
        """Update a user."""
        data = self._api_call(
            "POST", f"wp/v2/users/{user_id}", json_body=kwargs,
        )
        return self._parse_user(data)

    def delete_user(self, user_id: int, reassign: int = 0) -> bool:
        """Delete a user. Returns True on success."""
        try:
            self._api_call(
                "DELETE", f"wp/v2/users/{user_id}",
                params={"force": "true", "reassign": reassign},
            )
            return True
        except Exception:
            return False

    # ------------------------------------------------------------------
    # Parsing helpers
    # ------------------------------------------------------------------

    def _parse_post(self, data: Dict[str, Any]) -> WPPost:
        title = data.get("title", {})
        content = data.get("content", {})
        excerpt = data.get("excerpt", {})
        return WPPost(
            id=data.get("id", 0),
            title=title.get("rendered", title.get("raw", "")),
            content=content.get("rendered", content.get("raw", "")),
            excerpt=excerpt.get("rendered", excerpt.get("raw", "")),
            status=data.get("status", "draft"),
            post_type=data.get("type", "post"),
            slug=data.get("slug", ""),
            link=data.get("link", ""),
            author_id=data.get("author", 0),
            categories=data.get("categories", []) or [],
            tags=data.get("tags", []) or [],
            featured_media=data.get("featured_media", 0),
            format=data.get("format", "standard"),
            comment_status=data.get("comment_status", "open"),
            ping_status=data.get("ping_status", "open"),
            date_gmt=data.get("date_gmt", ""),
            modified_gmt=data.get("modified_gmt", ""),
            sticky=data.get("sticky", False),
            template=data.get("template", ""),
            extra=data,
        )

    def _parse_page(self, data: Dict[str, Any]) -> WPPage:
        title = data.get("title", {})
        content = data.get("content", {})
        excerpt = data.get("excerpt", {})
        return WPPage(
            id=data.get("id", 0),
            title=title.get("rendered", title.get("raw", "")),
            content=content.get("rendered", content.get("raw", "")),
            excerpt=excerpt.get("rendered", excerpt.get("raw", "")),
            status=data.get("status", "draft"),
            slug=data.get("slug", ""),
            link=data.get("link", ""),
            author_id=data.get("author", 0),
            parent_id=data.get("parent", 0),
            featured_media=data.get("featured_media", 0),
            menu_order=data.get("menu_order", 0),
            comment_status=data.get("comment_status", "open"),
            ping_status=data.get("ping_status", "open"),
            date_gmt=data.get("date_gmt", ""),
            modified_gmt=data.get("modified_gmt", ""),
            template=data.get("template", ""),
            extra=data,
        )

    def _parse_media(self, data: Dict[str, Any]) -> WPMedia:
        title = data.get("title", {})
        description = data.get("description", {})
        caption = data.get("caption", {})
        return WPMedia(
            id=data.get("id", 0),
            title=title.get("rendered", title.get("raw", "")),
            source_url=data.get("source_url", ""),
            media_type=data.get("media_type", ""),
            mime_type=data.get("mime_type", ""),
            description=description.get("rendered", description.get("raw", "")),
            caption=caption.get("rendered", caption.get("raw", "")),
            alt_text=data.get("alt_text", ""),
            author_id=data.get("author", 0),
            post_id=data.get("post", 0),
            slug=data.get("slug", ""),
            link=data.get("link", ""),
            date_gmt=data.get("date_gmt", ""),
            media_details=data.get("media_details", {}) or {},
            extra=data,
        )

    def _parse_category(self, data: Dict[str, Any]) -> WPCategory:
        return WPCategory(
            id=data.get("id", 0),
            name=data.get("name", ""),
            slug=data.get("slug", ""),
            description=data.get("description", ""),
            parent=data.get("parent", 0),
            count=data.get("count", 0),
            link=data.get("link", ""),
            taxonomy=data.get("taxonomy", "category"),
            extra=data,
        )

    def _parse_tag(self, data: Dict[str, Any]) -> WPTag:
        return WPTag(
            id=data.get("id", 0),
            name=data.get("name", ""),
            slug=data.get("slug", ""),
            description=data.get("description", ""),
            count=data.get("count", 0),
            link=data.get("link", ""),
            taxonomy=data.get("taxonomy", "post_tag"),
            extra=data,
        )

    def _parse_comment(self, data: Dict[str, Any]) -> WPComment:
        content = data.get("content", {})
        return WPComment(
            id=data.get("id", 0),
            content=content.get("rendered", content.get("raw", "")),
            post_id=data.get("post", 0),
            author_name=data.get("author_name", ""),
            author_email=data.get("author_email", ""),
            author_url=data.get("author_url", ""),
            author_ip=data.get("author_ip", ""),
            status=data.get("status", "pending"),
            type_=data.get("type", "comment"),
            parent=data.get("parent", 0),
            author_avatar_urls=data.get("author_avatar_urls", {}) or {},
            date_gmt=data.get("date_gmt", ""),
            link=data.get("link", ""),
            extra=data,
        )

    def _parse_user(self, data: Dict[str, Any]) -> WPUser:
        return WPUser(
            id=data.get("id", 0),
            username=data.get("username", ""),
            name=data.get("name", ""),
            email=data.get("email", ""),
            url=data.get("url", ""),
            description=data.get("description", ""),
            link=data.get("link", ""),
            slug=data.get("slug", ""),
            roles=data.get("roles", []) or [],
            registered_date=data.get("registered_date", ""),
            avatar_urls=data.get("avatar_urls", {}) or {},
            extra=data,
        )

    # ------------------------------------------------------------------
    # Internal API helpers
    # ------------------------------------------------------------------

    def _get_auth_token(self) -> str:
        """Get the Authorization header value."""
        if self.config.auth_type == AuthType.BASIC_AUTH:
            username = self.config.username or ""
            password = self.config.password or ""
            credentials = f"{username}:{password}".encode("utf-8")
            encoded = base64.b64encode(credentials).decode("utf-8")
            return f"Basic {encoded}"
        elif self.config.auth_type == AuthType.BEARER_TOKEN:
            token = self.config.access_token or self.config.api_key or ""
            return f"Bearer {token}"
        elif self.config.auth_type == AuthType.API_KEY and self.config.api_key:
            return f"Bearer {self.config.api_key}"
        return ""

    def _api_call_with_headers(self, method: str, path: str,
                               params: Optional[Dict[str, Any]] = None,
                               json_body: Optional[Dict[str, Any]] = None
                               ) -> Tuple[Any, Dict[str, str]]:
        """Make API call and return (data, headers)."""
        self._check_rate_limit()
        self._mark_request()

        url = self._build_url(path)
        headers = {
            "Authorization": self._get_auth_token(),
            "Content-Type": "application/json",
            "Accept": "application/json",
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

                resp_headers = {k: v for k, v in resp.headers.items()}
                if resp.status == 204:
                    return {}, resp_headers
                if not resp_body:
                    return {}, resp_headers
                return json.loads(resp_body), resp_headers

        except urllib.error.HTTPError as e:
            resp_body = e.read().decode("utf-8", errors="replace")
            self._log_response(method, url, e.code, resp_body)

            if e.code == 429:
                retry_after = float(e.headers.get("Retry-After", 1))
                self._rate_limit_hits += 1
                raise RateLimitError(
                    f"WordPress rate limited, retry after {retry_after}s",
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
                f"WordPress API error {e.code}: {e.reason}",
                status_code=e.code,
                response_body=resp_body,
            )

    def _api_call(self, method: str, path: str,
                  params: Optional[Dict[str, Any]] = None,
                  json_body: Optional[Dict[str, Any]] = None,
                  _retry_count: int = 0) -> Any:
        """Make a WordPress API call with retry."""
        try:
            data, _ = self._api_call_with_headers(method, path, params, json_body)
            return data
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
