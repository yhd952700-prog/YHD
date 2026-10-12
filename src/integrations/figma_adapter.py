"""
Figma Integration Adapter for LiuHao AI OS.

Provides integration with Figma API for accessing file data, images, comments,
team projects, component/style libraries, and webhooks.
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


class FigmaNodeType(str, Enum):
    """Figma node types."""
    DOCUMENT = "DOCUMENT"
    CANVAS = "CANVAS"
    FRAME = "FRAME"
    GROUP = "GROUP"
    COMPONENT = "COMPONENT"
    COMPONENT_SET = "COMPONENT_SET"
    INSTANCE = "INSTANCE"
    RECTANGLE = "RECTANGLE"
    TEXT = "TEXT"
    VECTOR = "VECTOR"
    BOOLEAN_OPERATION = "BOOLEAN_OPERATION"
    STAR = "STAR"
    LINE = "LINE"
    ELLIPSE = "ELLIPSE"
    REGULAR_POLYGON = "REGULAR_POLYGON"
    SLIC = "SLICE"
    SECTION = "SECTION"


class FigmaImageFormat(str, Enum):
    """Image export formats."""
    PNG = "png"
    JPG = "jpg"
    SVG = "svg"
    PDF = "pdf"


class FigmaWebhookEvent(str, Enum):
    """Figma webhook event types."""
    FILE_UPDATE = "FILE_UPDATE"
    FILE_COMMENT = "FILE_COMMENT"
    FILE_VERSION_UPDATE = "FILE_VERSION_UPDATE"
    LIBRARY_PUBLISH = "LIBRARY_PUBLISH"


@dataclass
class FigmaFile:
    """Represents a Figma file."""
    key: str
    name: str = ""
    last_modified: str = ""
    thumbnail_url: str = ""
    nodes: Dict[str, Any] = field(default_factory=dict)
    schema_version: int = 0
    role: str = ""
    editor_type: str = ""
    link_access: str = ""
    extra: Dict[str, Any] = field(default_factory=dict)


@dataclass
class FigmaComment:
    """Represents a Figma comment."""
    id: str
    file_key: str
    body_text: str
    user_id: str = ""
    user_handle: str = ""
    user_img_url: str = ""
    created_at: str = ""
    resolved_at: str = ""
    order_id: str = ""
    parent_id: str = ""
    client_meta: Dict[str, Any] = field(default_factory=dict)
    extra: Dict[str, Any] = field(default_factory=dict)


@dataclass
class FigmaProject:
    """Represents a Figma project."""
    id: str
    name: str = ""
    extra: Dict[str, Any] = field(default_factory=dict)


@dataclass
class FigmaTeam:
    """Represents a Figma team."""
    id: str
    name: str = ""
    extra: Dict[str, Any] = field(default_factory=dict)


@dataclass
class FigmaComponent:
    """Represents a Figma component."""
    key: str
    file_key: str = ""
    node_id: str = ""
    thumbnail_url: str = ""
    name: str = ""
    description: str = ""
    created_at: str = ""
    updated_at: str = ""
    user: Dict[str, Any] = field(default_factory=dict)
    containing_frame: Dict[str, Any] = field(default_factory=dict)
    extra: Dict[str, Any] = field(default_factory=dict)


@dataclass
class FigmaStyle:
    """Represents a Figma style."""
    key: str
    file_key: str = ""
    node_id: str = ""
    style_type: str = ""
    name: str = ""
    description: str = ""
    thumbnail_url: str = ""
    user: Dict[str, Any] = field(default_factory=dict)
    updated_at: str = ""
    created_at: str = ""
    sort_position: str = ""
    extra: Dict[str, Any] = field(default_factory=dict)


@dataclass
class FigmaWebhook:
    """Represents a Figma webhook."""
    id: str
    team_id: str
    event_type: FigmaWebhookEvent
    endpoint: str
    passcode: str = ""
    status: str = ""
    description: str = ""
    extra: Dict[str, Any] = field(default_factory=dict)


class FigmaAdapter(BaseIntegrationAdapter):
    """
    Figma integration adapter for LiuHao AI OS.

    Supports:
    - Getting file data and nodes
    - Getting file images (PNG, JPG, SVG, PDF exports)
    - Getting and posting comments
    - Getting team projects
    - Getting project files
    - Component and style libraries
    - Webhook management and verification
    """

    def __init__(self, config: IntegrationConfig):
        """
        Initialize the Figma adapter.

        Args:
            config: Integration configuration. Must have access_token set
                    for the Figma personal access token.
        """
        if config.auth_type == AuthType.NONE:
            config.auth_type = AuthType.BEARER_TOKEN
        if not config.base_url:
            config.base_url = "https://api.figma.com/v1"
        super().__init__(config)
        self._user_id: Optional[str] = None
        self._handle: Optional[str] = None
        self._team_id: Optional[str] = None
        self._cached_files: Dict[str, FigmaFile] = {}
        self._webhook_endpoints: List[FigmaWebhook] = []

    # ------------------------------------------------------------------
    # Base interface implementation
    # ------------------------------------------------------------------

    def connect(self) -> bool:
        """
        Establish connection to Figma API and validate credentials.

        Returns:
            True if connection successful.

        Raises:
            AuthenticationError: If token is invalid.
        """
        self._status = ConnectionStatus.CONNECTING
        self._logger.info("Connecting to Figma API...")

        try:
            result = self._api_call("me")
            self._user_id = result.get("id", "")
            self._handle = result.get("handle", "")
            self._status = ConnectionStatus.CONNECTED
            self._connected_at = time.time()
            self._logger.info(
                "Connected to Figma as %s (id: %s)",
                self._handle, self._user_id,
            )
            return True
        except IntegrationError:
            raise
        except Exception as e:
            self._status = ConnectionStatus.ERROR
            raise self._handle_error(f"Connection error: {e}")

    def disconnect(self) -> bool:
        """
        Disconnect from Figma (clear session state).

        Returns:
            Always returns True.
        """
        self._status = ConnectionStatus.DISCONNECTED
        self._user_id = None
        self._handle = None
        self._team_id = None
        self._cached_files = {}
        self._webhook_endpoints = []
        self._session = None
        self._logger.info("Disconnected from Figma")
        return True

    def is_connected(self) -> bool:
        """
        Check if connected to Figma API.

        Returns:
            True if status is CONNECTED.
        """
        return self._status == ConnectionStatus.CONNECTED

    def get_status(self) -> IntegrationStatus:
        """
        Get current adapter status.

        Returns:
            IntegrationStatus with Figma-specific extra info.
        """
        return self._build_status({
            "user_handle": self._handle,
            "user_id": self._user_id,
            "team_id": self._team_id,
            "cached_files": len(self._cached_files),
            "webhooks": len(self._webhook_endpoints),
        })

    def test_connection(self) -> bool:
        """
        Test the Figma API connection.

        Returns:
            True if connection is working.
        """
        try:
            if not self.is_connected():
                return False
            result = self._api_call("me")
            return "id" in result
        except Exception:
            return False

    # ------------------------------------------------------------------
    # File data
    # ------------------------------------------------------------------

    def get_file(self, file_key: str,
                 version: Optional[str] = None,
                 ids: Optional[str] = None,
                 depth: Optional[int] = None,
                 geometry: str = "paths",
                 plugin_data: bool = False,
                 branch_data: bool = False) -> FigmaFile:
        """
        Get the document structure of a Figma file.

        Args:
            file_key: File key (from the URL).
            version: Specific version ID.
            ids: Comma-separated list of node IDs to include.
            depth: How many levels of children to include.
            geometry: Geometry export format (paths, outline, or paths+outline).
            plugin_data: Whether to include plugin data.
            branch_data: Whether to include branch data.

        Returns:
            FigmaFile with document structure.
        """
        params: Dict[str, Any] = {
            "geometry": geometry,
            "plugin_data": str(plugin_data).lower(),
            "branch_data": str(branch_data).lower(),
        }
        if version:
            params["version"] = version
        if ids:
            params["ids"] = ids
        if depth is not None:
            params["depth"] = depth

        result = self._api_call(f"files/{file_key}", params=params)
        fig_file = FigmaFile(
            key=file_key,
            name=result.get("name", ""),
            last_modified=result.get("lastModified", ""),
            thumbnail_url=result.get("thumbnailUrl", ""),
            nodes=result.get("nodes", {}),
            schema_version=result.get("schemaVersion", 0),
            role=result.get("role", ""),
            editor_type=result.get("editorType", ""),
            link_access=result.get("linkAccess", ""),
            extra=result,
        )
        self._cached_files[file_key] = fig_file
        return fig_file

    def get_file_nodes(self, file_key: str,
                       ids: List[str],
                       version: Optional[str] = None,
                       depth: Optional[int] = None,
                       geometry: str = "paths") -> Dict[str, Any]:
        """
        Get specific nodes from a file.

        Args:
            file_key: File key.
            ids: List of node IDs.
            version: Specific version.
            depth: How many levels of children.
            geometry: Geometry format.

        Returns:
            Dictionary of node ID to node data.
        """
        params: Dict[str, Any] = {
            "ids": ",".join(ids),
            "geometry": geometry,
        }
        if version:
            params["version"] = version
        if depth is not None:
            params["depth"] = depth

        result = self._api_call(f"files/{file_key}/nodes", params=params)
        return result.get("nodes", {})

    def get_file_versions(self, file_key: str) -> List[Dict[str, Any]]:
        """
        Get the version history of a file.

        Args:
            file_key: File key.

        Returns:
            List of version objects.
        """
        result = self._api_call(f"files/{file_key}/versions")
        return result.get("versions", [])

    # ------------------------------------------------------------------
    # File images
    # ------------------------------------------------------------------

    def get_file_images(self, file_key: str,
                        ids: List[str],
                        scale: float = 1.0,
                        format: FigmaImageFormat = FigmaImageFormat.PNG,
                        svg_include_id: bool = False,
                        svg_simplify_stroke: bool = True,
                        use_absolute_bounds: bool = False,
                        version: Optional[str] = None) -> Dict[str, str]:
        """
        Get rendered images for nodes in a file.

        Args:
            file_key: File key.
            ids: List of node IDs to render.
            scale: Image scale (1, 2, 3, or 4).
            format: Image format (png, jpg, svg, pdf).
            svg_include_id: Include node IDs in SVG.
            svg_simplify_stroke: Simplify strokes in SVG.
            use_absolute_bounds: Use absolute bounds.
            version: Specific file version.

        Returns:
            Dictionary mapping node IDs to image URLs.
        """
        params: Dict[str, Any] = {
            "ids": ",".join(ids),
            "scale": scale,
            "format": format.value,
            "svg_include_id": str(svg_include_id).lower(),
            "svg_simplify_stroke": str(svg_simplify_stroke).lower(),
            "use_absolute_bounds": str(use_absolute_bounds).lower(),
        }
        if version:
            params["version"] = version

        result = self._api_call(f"images/{file_key}", params=params)
        return result.get("images", {})

    def get_image_fills(self, file_key: str) -> Dict[str, str]:
        """
        Get URLs for all image fills in a file.

        Args:
            file_key: File key.

        Returns:
            Dictionary mapping image references to URLs.
        """
        result = self._api_call(f"files/{file_key}/images")
        return result.get("meta", {}).get("images", {})

    # ------------------------------------------------------------------
    # Comments
    # ------------------------------------------------------------------

    def get_comments(self, file_key: str,
                     as_md: bool = False) -> List[FigmaComment]:
        """
        Get all comments on a file.

        Args:
            file_key: File key.
            as_md: Return comment body as Markdown.

        Returns:
            List of FigmaComment objects.
        """
        params = {"as_md": str(as_md).lower()}
        result = self._api_call(f"files/{file_key}/comments", params=params)
        comments_data = result.get("comments", [])
        return [
            FigmaComment(
                id=c.get("id", ""),
                file_key=file_key,
                body_text=c.get("message", ""),
                user_id=c.get("user", {}).get("id", ""),
                user_handle=c.get("user", {}).get("handle", ""),
                user_img_url=c.get("user", {}).get("img_url", ""),
                created_at=c.get("created_at", ""),
                resolved_at=c.get("resolved_at", ""),
                order_id=c.get("order_id", ""),
                parent_id=c.get("parent_id", ""),
                client_meta=c.get("client_meta", {}),
                extra=c,
            )
            for c in comments_data
        ]

    def post_comment(self, file_key: str,
                     message: str,
                     client_meta: Optional[Dict[str, Any]] = None,
                     comment_id: Optional[str] = None) -> FigmaComment:
        """
        Post a comment on a file.

        Args:
            file_key: File key.
            message: Comment text.
            client_meta: Position data for the comment.
            comment_id: Parent comment ID for replies.

        Returns:
            Created FigmaComment.
        """
        payload: Dict[str, Any] = {"message": message}
        if client_meta:
            payload["client_meta"] = client_meta
        if comment_id:
            payload["comment_id"] = comment_id

        result = self._api_call(
            f"files/{file_key}/comments",
            data=payload,
            http_method="POST",
        )
        c = result
        return FigmaComment(
            id=c.get("id", ""),
            file_key=file_key,
            body_text=c.get("message", ""),
            user_id=c.get("user", {}).get("id", ""),
            user_handle=c.get("user", {}).get("handle", ""),
            user_img_url=c.get("user", {}).get("img_url", ""),
            created_at=c.get("created_at", ""),
            resolved_at=c.get("resolved_at", ""),
            order_id=c.get("order_id", ""),
            parent_id=c.get("parent_id", ""),
            client_meta=c.get("client_meta", {}),
            extra=c,
        )

    def delete_comment(self, file_key: str, comment_id: str) -> bool:
        """
        Delete a comment from a file.

        Args:
            file_key: File key.
            comment_id: Comment ID.

        Returns:
            True if deletion was successful.
        """
        result = self._api_call(
            f"files/{file_key}/comments/{comment_id}",
            http_method="DELETE",
        )
        return result.get("id") == comment_id or True

    # ------------------------------------------------------------------
    # Teams and projects
    # ------------------------------------------------------------------

    def get_team_projects(self, team_id: str) -> List[FigmaProject]:
        """
        Get all projects in a team.

        Args:
            team_id: Team ID.

        Returns:
            List of FigmaProject objects.
        """
        result = self._api_call(f"teams/{team_id}/projects")
        projects_data = result.get("projects", [])
        return [
            FigmaProject(
                id=p.get("id", ""),
                name=p.get("name", ""),
                extra=p,
            )
            for p in projects_data
        ]

    def get_project_files(self, project_id: str,
                          branch_data: bool = False
                          ) -> List[Dict[str, Any]]:
        """
        Get all files in a project.

        Args:
            project_id: Project ID.
            branch_data: Include branch information.

        Returns:
            List of file data.
        """
        params = {"branch_data": str(branch_data).lower()}
        result = self._api_call(
            f"projects/{project_id}/files", params=params
        )
        return result.get("files", [])

    def get_team_components(self, team_id: str,
                            page_size: int = 30,
                            cursor: Optional[str] = None
                            ) -> Dict[str, Any]:
        """
        Get all published components in a team library.

        Args:
            team_id: Team ID.
            page_size: Number of items per page.
            cursor: Pagination cursor.

        Returns:
            Dictionary with components and pagination info.
        """
        params: Dict[str, Any] = {"page_size": page_size}
        if cursor:
            params["cursor"] = cursor

        result = self._api_call(
            f"teams/{team_id}/components", params=params
        )
        return result

    def get_team_styles(self, team_id: str,
                        page_size: int = 30,
                        cursor: Optional[str] = None
                        ) -> Dict[str, Any]:
        """
        Get all published styles in a team library.

        Args:
            team_id: Team ID.
            page_size: Number of items per page.
            cursor: Pagination cursor.

        Returns:
            Dictionary with styles and pagination info.
        """
        params: Dict[str, Any] = {"page_size": page_size}
        if cursor:
            params["cursor"] = cursor

        result = self._api_call(
            f"teams/{team_id}/styles", params=params
        )
        return result

    # ------------------------------------------------------------------
    # Components and styles
    # ------------------------------------------------------------------

    def get_component(self, component_key: str) -> FigmaComponent:
        """
        Get metadata on a specific component.

        Args:
            component_key: Component key.

        Returns:
            FigmaComponent object.
        """
        result = self._api_call(f"components/{component_key}")
        data = result.get("meta", {})
        return FigmaComponent(
            key=data.get("key", component_key),
            file_key=data.get("file_key", ""),
            node_id=data.get("node_id", ""),
            thumbnail_url=data.get("thumbnail_url", ""),
            name=data.get("name", ""),
            description=data.get("description", ""),
            created_at=data.get("created_at", ""),
            updated_at=data.get("updated_at", ""),
            user=data.get("user", {}),
            containing_frame=data.get("containing_frame", {}),
            extra=data,
        )

    def get_style(self, style_key: str) -> FigmaStyle:
        """
        Get metadata on a specific style.

        Args:
            style_key: Style key.

        Returns:
            FigmaStyle object.
        """
        result = self._api_call(f"styles/{style_key}")
        data = result.get("meta", {})
        return FigmaStyle(
            key=data.get("key", style_key),
            file_key=data.get("file_key", ""),
            node_id=data.get("node_id", ""),
            style_type=data.get("style_type", ""),
            name=data.get("name", ""),
            description=data.get("description", ""),
            thumbnail_url=data.get("thumbnail_url", ""),
            user=data.get("user", {}),
            updated_at=data.get("updated_at", ""),
            created_at=data.get("created_at", ""),
            sort_position=data.get("sort_position", ""),
            extra=data,
        )

    # ------------------------------------------------------------------
    # Webhooks
    # ------------------------------------------------------------------

    def create_webhook(self, team_id: str,
                       event_type: FigmaWebhookEvent,
                       endpoint: str,
                       passcode: str,
                       description: str = "") -> FigmaWebhook:
        """
        Create a new webhook for a team.

        Args:
            team_id: Team ID.
            event_type: Event type to listen for.
            endpoint: Webhook endpoint URL.
            passcode: Webhook passcode for verification.
            description: Webhook description.

        Returns:
            Created FigmaWebhook.
        """
        payload: Dict[str, Any] = {
            "team_id": team_id,
            "event_type": event_type.value,
            "endpoint": endpoint,
            "passcode": passcode,
            "description": description,
        }

        result = self._api_call(
            "webhooks", data=payload, http_method="POST"
        )
        webhook = FigmaWebhook(
            id=result.get("id", ""),
            team_id=team_id,
            event_type=event_type,
            endpoint=endpoint,
            passcode=passcode,
            status=result.get("status", ""),
            description=description,
            extra=result,
        )
        self._webhook_endpoints.append(webhook)
        return webhook

    def list_webhooks(self, team_id: str) -> List[FigmaWebhook]:
        """
        List all webhooks for a team.

        Args:
            team_id: Team ID.

        Returns:
            List of FigmaWebhook objects.
        """
        result = self._api_call(f"teams/{team_id}/webhooks")
        webhooks_data = result.get("webhooks", [])
        webhooks = [
            FigmaWebhook(
                id=w.get("id", ""),
                team_id=team_id,
                event_type=FigmaWebhookEvent(w.get("event_type", "FILE_UPDATE")),
                endpoint=w.get("endpoint", ""),
                status=w.get("status", ""),
                description=w.get("description", ""),
                extra=w,
            )
            for w in webhooks_data
        ]
        return webhooks

    def delete_webhook(self, webhook_id: str) -> bool:
        """
        Delete a webhook.

        Args:
            webhook_id: Webhook ID.

        Returns:
            True if deletion was successful.
        """
        self._api_call(f"webhooks/{webhook_id}", http_method="DELETE")
        self._webhook_endpoints = [
            w for w in self._webhook_endpoints if w.id != webhook_id
        ]
        return True

    def verify_webhook(self, request_body: str,
                       x_figma_timestamp: str,
                       x_figma_signature: str,
                       passcode: str) -> bool:
        """
        Verify a Figma webhook request signature.

        Args:
            request_body: Raw request body.
            x_figma_timestamp: X-Figma-Timestamp header.
            x_figma_signature: X-Figma-Signature header.
            passcode: The webhook passcode.

        Returns:
            True if the webhook is verified.
        """
        signed_content = f"{x_figma_timestamp}\n{request_body}"
        expected = hmac.new(
            passcode.encode("utf-8"),
            signed_content.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()
        return hmac.compare_digest(x_figma_signature, expected)

    # ------------------------------------------------------------------
    # Internal API call helper
    # ------------------------------------------------------------------

    def _api_call(self, path: str,
                  params: Optional[Dict[str, Any]] = None,
                  data: Optional[Dict[str, Any]] = None,
                  http_method: str = "GET") -> Dict[str, Any]:
        """
        Make a Figma API call.

        Args:
            path: API endpoint path.
            params: Query parameters.
            data: Request body data.
            http_method: HTTP method.

        Returns:
            Parsed JSON response.

        Raises:
            IntegrationError: On API errors.
            RateLimitError: On rate limit exceeded.
        """
        self._check_rate_limit()
        self._mark_request()

        url = self._build_url(path)
        if params:
            from urllib.parse import urlencode
            url = f"{url}?{urlencode(params)}"

        headers = {
            "X-Figma-Token": self.config.access_token or self.config.api_key or "",
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
                retry_after = int(e.headers.get("Retry-After", 60))
                self._rate_limit_hits += 1
                raise RateLimitError(
                    "Rate limited by Figma",
                    retry_after=retry_after,
                    adapter_name=self.config.name,
                )
            if e.code == 401 or e.code == 403:
                raise self._handle_error(
                    "Authentication failed: invalid Figma token",
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
            f"<FigmaAdapter name={self.config.name} "
            f"status={self._status.value} "
            f"user={self._handle or 'unknown'}>"
        )
