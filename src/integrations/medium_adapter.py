"""
Medium Integration Adapter for LiuHao AI OS.

Provides integration with Medium API for publishing posts, retrieving user info,
managing publications, uploading images, and post formatting.
"""

from __future__ import annotations

import base64
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


class MediumPostFormat(str, Enum):
    """Post content format types."""
    HTML = "html"
    MARKDOWN = "markdown"
    PLAIN = "plain"


class MediumPublishStatus(str, Enum):
    """Post publish status."""
    DRAFT = "draft"
    UNLISTED = "unlisted"
    PUBLIC = "public"


class MediumLicense(str, Enum):
    """Post license types."""
    ALL_RIGHTS_RESERVED = "all-rights-reserved"
    CC_40_BY = "cc-40-by"
    CC_40_BY_SA = "cc-40-by-sa"
    CC_40_BY_ND = "cc-40-by-nd"
    CC_40_BY_NC = "cc-40-by-nc"
    CC_40_BY_NC_SA = "cc-40-by-nc-sa"
    CC_40_BY_NC_ND = "cc-40-by-nc-nd"
    PUBLIC_DOMAIN = "public-domain"


@dataclass
class MediumUser:
    """Represents a Medium user."""
    id: str
    username: str
    name: str
    url: str = ""
    image_url: str = ""
    extra: Dict[str, Any] = field(default_factory=dict)


@dataclass
class MediumPublication:
    """Represents a Medium publication."""
    id: str
    name: str
    description: str = ""
    url: str = ""
    image_url: str = ""
    extra: Dict[str, Any] = field(default_factory=dict)


@dataclass
class MediumPost:
    """Represents a Medium post to create or retrieve."""
    title: str
    content: str = ""
    content_format: MediumPostFormat = MediumPostFormat.MARKDOWN
    tags: List[str] = field(default_factory=list)
    canonical_url: str = ""
    publish_status: MediumPublishStatus = MediumPublishStatus.PUBLIC
    license: MediumLicense = MediumLicense.ALL_RIGHTS_RESERVED
    notify_followers: bool = True
    id: str = ""
    url: str = ""
    author_id: str = ""
    published_at: Optional[int] = None
    reading_time: float = 0.0
    word_count: int = 0
    extra: Dict[str, Any] = field(default_factory=dict)


@dataclass
class MediumUploadedImage:
    """Represents an uploaded image on Medium."""
    url: str
    md5: str = ""


class MediumAdapter(BaseIntegrationAdapter):
    """
    Medium integration adapter for LiuHao AI OS.

    Supports:
    - Publishing posts to user profile or publications
    - Getting user information
    - Listing user publications
    - Getting post details
    - Uploading images
    - Post formatting (Markdown/HTML)
    - Publication management
    """

    def __init__(self, config: IntegrationConfig):
        """
        Initialize the Medium adapter.

        Args:
            config: Integration configuration. Must have access_token set
                    for the Medium integration token.
        """
        if config.auth_type == AuthType.NONE:
            config.auth_type = AuthType.BEARER_TOKEN
        if not config.base_url:
            config.base_url = "https://api.medium.com/v1"
        super().__init__(config)
        self._user: Optional[MediumUser] = None
        self._publications: List[MediumPublication] = []
        self._cached_posts: Dict[str, MediumPost] = {}

    # ------------------------------------------------------------------
    # Base interface implementation
    # ------------------------------------------------------------------

    def connect(self) -> bool:
        """
        Establish connection to Medium API and validate credentials.

        Returns:
            True if connection successful.

        Raises:
            AuthenticationError: If token is invalid.
        """
        self._status = ConnectionStatus.CONNECTING
        self._logger.info("Connecting to Medium API...")

        try:
            result = self._api_call("me")
            user_data = result.get("data", {})
            self._user = MediumUser(
                id=user_data.get("id", ""),
                username=user_data.get("username", ""),
                name=user_data.get("name", ""),
                url=user_data.get("url", ""),
                image_url=user_data.get("imageUrl", ""),
                extra=user_data,
            )
            self._status = ConnectionStatus.CONNECTED
            self._connected_at = time.time()
            self._logger.info(
                "Connected to Medium as @%s (%s)",
                self._user.username, self._user.name,
            )
            return True
        except IntegrationError:
            raise
        except Exception as e:
            self._status = ConnectionStatus.ERROR
            raise self._handle_error(f"Connection error: {e}")

    def disconnect(self) -> bool:
        """
        Disconnect from Medium (clear session state).

        Returns:
            Always returns True.
        """
        self._status = ConnectionStatus.DISCONNECTED
        self._user = None
        self._publications = []
        self._cached_posts = {}
        self._session = None
        self._logger.info("Disconnected from Medium")
        return True

    def is_connected(self) -> bool:
        """
        Check if connected to Medium API.

        Returns:
            True if status is CONNECTED.
        """
        return self._status == ConnectionStatus.CONNECTED

    def get_status(self) -> IntegrationStatus:
        """
        Get current adapter status.

        Returns:
            IntegrationStatus with Medium-specific extra info.
        """
        return self._build_status({
            "username": self._user.username if self._user else None,
            "user_id": self._user.id if self._user else None,
            "publications_count": len(self._publications),
            "cached_posts": len(self._cached_posts),
        })

    def test_connection(self) -> bool:
        """
        Test the Medium API connection.

        Returns:
            True if connection is working.
        """
        try:
            if not self.is_connected():
                return False
            result = self._api_call("me")
            return "data" in result
        except Exception:
            return False

    # ------------------------------------------------------------------
    # User info
    # ------------------------------------------------------------------

    def get_user(self) -> Optional[MediumUser]:
        """
        Get the authenticated user information.

        Returns:
            MediumUser object, or None if not connected.
        """
        return self._user

    def get_user_by_username(self, username: str) -> MediumUser:
        """
        Get user information by username.

        Args:
            username: Medium username (without @).

        Returns:
            MediumUser object.
        """
        result = self._api_call(f"users/@{username}")
        user_data = result.get("data", {})
        return MediumUser(
            id=user_data.get("id", ""),
            username=user_data.get("username", ""),
            name=user_data.get("name", ""),
            url=user_data.get("url", ""),
            image_url=user_data.get("imageUrl", ""),
            extra=user_data,
        )

    # ------------------------------------------------------------------
    # Publications
    # ------------------------------------------------------------------

    def list_user_publications(self,
                               user_id: Optional[str] = None
                               ) -> List[MediumPublication]:
        """
        List publications for a user.

        Args:
            user_id: User ID. Defaults to authenticated user.

        Returns:
            List of MediumPublication objects.
        """
        uid = user_id or (self._user.id if self._user else "")
        if not uid:
            raise self._handle_error("User ID is required")

        result = self._api_call(f"users/{uid}/publications")
        pubs_data = result.get("data", [])
        publications = [
            MediumPublication(
                id=p.get("id", ""),
                name=p.get("name", ""),
                description=p.get("description", ""),
                url=p.get("url", ""),
                image_url=p.get("imageUrl", ""),
                extra=p,
            )
            for p in pubs_data
        ]

        if user_id is None or user_id == (self._user.id if self._user else ""):
            self._publications = publications

        return publications

    def get_publication(self, publication_id: str) -> MediumPublication:
        """
        Get a specific publication by ID.

        Args:
            publication_id: Publication ID.

        Returns:
            MediumPublication object.
        """
        result = self._api_call(f"publications/{publication_id}")
        pub_data = result.get("data", {})
        return MediumPublication(
            id=pub_data.get("id", ""),
            name=pub_data.get("name", ""),
            description=pub_data.get("description", ""),
            url=pub_data.get("url", ""),
            image_url=pub_data.get("imageUrl", ""),
            extra=pub_data,
        )

    def list_publication_contributors(self, publication_id: str
                                      ) -> List[Dict[str, Any]]:
        """
        List contributors for a publication.

        Args:
            publication_id: Publication ID.

        Returns:
            List of contributor data.
        """
        result = self._api_call(
            f"publications/{publication_id}/contributors"
        )
        return result.get("data", [])

    # ------------------------------------------------------------------
    # Posts
    # ------------------------------------------------------------------

    def create_post(self, post: MediumPost,
                    user_id: Optional[str] = None) -> MediumPost:
        """
        Create a new post under a user's profile.

        Args:
            post: Post data to create.
            user_id: User ID. Defaults to authenticated user.

        Returns:
            Created MediumPost with ID and URL populated.
        """
        uid = user_id or (self._user.id if self._user else "")
        if not uid:
            raise self._handle_error("User ID is required")

        payload = self._build_post_payload(post)
        result = self._api_call(
            f"users/{uid}/posts",
            data=payload,
            http_method="POST",
        )
        post_data = result.get("data", {})
        created_post = self._parse_post_response(post_data)
        self._cached_posts[created_post.id] = created_post
        return created_post

    def create_post_in_publication(self, post: MediumPost,
                                   publication_id: str) -> MediumPost:
        """
        Create a new post under a publication.

        Args:
            post: Post data to create.
            publication_id: Publication ID.

        Returns:
            Created MediumPost with ID and URL populated.
        """
        payload = self._build_post_payload(post)
        result = self._api_call(
            f"publications/{publication_id}/posts",
            data=payload,
            http_method="POST",
        )
        post_data = result.get("data", {})
        created_post = self._parse_post_response(post_data)
        self._cached_posts[created_post.id] = created_post
        return created_post

    def get_post(self, post_id: str) -> MediumPost:
        """
        Get post details by ID.

        Args:
            post_id: Post ID.

        Returns:
            MediumPost object with details.
        """
        if post_id in self._cached_posts:
            return self._cached_posts[post_id]

        result = self._api_call(f"posts/{post_id}")
        post_data = result.get("data", {})
        post = self._parse_post_response(post_data)
        self._cached_posts[post_id] = post
        return post

    def _build_post_payload(self, post: MediumPost) -> Dict[str, Any]:
        """
        Build the payload for creating a post.

        Args:
            post: MediumPost data.

        Returns:
            Dictionary payload for the API.
        """
        payload: Dict[str, Any] = {
            "title": post.title,
            "content": post.content,
            "contentFormat": post.content_format.value,
            "publishStatus": post.publish_status.value,
            "license": post.license.value,
            "notifyFollowers": post.notify_followers,
        }

        if post.tags:
            payload["tags"] = post.tags
        if post.canonical_url:
            payload["canonicalUrl"] = post.canonical_url

        return payload

    def _parse_post_response(self, data: Dict[str, Any]) -> MediumPost:
        """
        Parse a post response into a MediumPost object.

        Args:
            data: Raw API response data.

        Returns:
            MediumPost object.
        """
        return MediumPost(
            title=data.get("title", ""),
            content=data.get("content", ""),
            content_format=MediumPostFormat(
                data.get("contentFormat", "markdown")
            ),
            tags=data.get("tags", []),
            canonical_url=data.get("canonicalUrl", ""),
            publish_status=MediumPublishStatus(
                data.get("publishStatus", "public")
            ),
            license=MediumLicense(
                data.get("license", "all-rights-reserved")
            ),
            id=data.get("id", ""),
            url=data.get("url", ""),
            author_id=data.get("authorId", ""),
            published_at=data.get("publishedAt"),
            reading_time=float(data.get("readingTime", 0)),
            word_count=int(data.get("wordCount", 0)),
            extra=data,
        )

    # ------------------------------------------------------------------
    # Image upload
    # ------------------------------------------------------------------

    def upload_image(self, image_data: bytes,
                     content_type: str = "image/png"
                     ) -> MediumUploadedImage:
        """
        Upload an image to Medium for use in posts.

        Args:
            image_data: Raw image bytes.
            content_type: MIME type of the image.

        Returns:
            MediumUploadedImage with URL and MD5.
        """
        self._check_rate_limit()
        self._mark_request()

        url = self._build_url("images")
        headers = {
            "Authorization": f"Bearer {self.config.access_token or self.config.api_key or ''}",
            "Content-Type": content_type,
        }

        self._log_request("POST", url, headers, f"<binary {len(image_data)} bytes>")

        try:
            import urllib.request

            req = urllib.request.Request(
                url,
                data=image_data,
                headers=headers,
                method="POST",
            )

            with urllib.request.urlopen(
                req, timeout=self.config.timeout
            ) as response:
                response_body = response.read().decode("utf-8")
                self._log_response(
                    "POST", url, response.status, response_body
                )
                result = json.loads(response_body)

                if "data" not in result:
                    raise self._handle_error(
                        "Image upload failed",
                        status_code=response.status,
                        response_body=response_body,
                    )

                img_data = result["data"]
                return MediumUploadedImage(
                    url=img_data.get("url", ""),
                    md5=img_data.get("md5", ""),
                )

        except IntegrationError:
            raise
        except Exception as e:
            raise self._handle_error(f"Image upload error: {e}")

    def upload_image_from_file(self, file_path: str,
                               content_type: str = "image/png"
                               ) -> MediumUploadedImage:
        """
        Upload an image from a local file path.

        Args:
            file_path: Path to the image file.
            content_type: MIME type of the image.

        Returns:
            MediumUploadedImage with URL and MD5.
        """
        with open(file_path, "rb") as f:
            image_data = f.read()
        return self.upload_image(image_data, content_type)

    # ------------------------------------------------------------------
    # Post formatting helpers
    # ------------------------------------------------------------------

    @staticmethod
    def format_markdown_post(title: str, body: str,
                             tags: Optional[List[str]] = None
                             ) -> MediumPost:
        """
        Helper to create a Markdown-formatted post.

        Args:
            title: Post title.
            body: Markdown body content.
            tags: Optional list of tags.

        Returns:
            MediumPost configured for Markdown.
        """
        return MediumPost(
            title=title,
            content=body,
            content_format=MediumPostFormat.MARKDOWN,
            tags=tags or [],
        )

    @staticmethod
    def format_html_post(title: str, body: str,
                         tags: Optional[List[str]] = None
                         ) -> MediumPost:
        """
        Helper to create an HTML-formatted post.

        Args:
            title: Post title.
            body: HTML body content.
            tags: Optional list of tags.

        Returns:
            MediumPost configured for HTML.
        """
        return MediumPost(
            title=title,
            content=body,
            content_format=MediumPostFormat.HTML,
            tags=tags or [],
        )

    # ------------------------------------------------------------------
    # Internal API call helper
    # ------------------------------------------------------------------

    def _api_call(self, path: str,
                  data: Optional[Dict[str, Any]] = None,
                  http_method: str = "GET") -> Dict[str, Any]:
        """
        Make a Medium API call.

        Args:
            path: API endpoint path.
            data: Request body data for POST/PUT.
            http_method: HTTP method to use.

        Returns:
            Parsed JSON response.

        Raises:
            IntegrationError: On API errors.
            RateLimitError: On rate limit exceeded.
        """
        self._check_rate_limit()
        self._mark_request()

        url = self._build_url(path)
        headers = {
            "Authorization": f"Bearer {self.config.access_token or self.config.api_key or ''}",
            "Content-Type": "application/json; charset=utf-8",
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
                    "Rate limited by Medium",
                    retry_after=60,
                    adapter_name=self.config.name,
                )
            if e.code == 401:
                raise self._handle_error(
                    "Authentication failed: invalid Medium token",
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
            f"<MediumAdapter name={self.config.name} "
            f"status={self._status.value} "
            f"user=@{self._user.username if self._user else 'unknown'}>"
        )
