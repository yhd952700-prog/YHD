"""
Notion Integration Adapter for LiuHao AI OS.

Provides integration with Notion for database query and manipulation,
page creation and editing, block operations, search, users and workspaces,
and page property management.
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


class NotionObjectType(str, Enum):
    """Notion object types."""
    DATABASE = "database"
    PAGE = "page"
    BLOCK = "block"
    USER = "user"
    COMMENT = "comment"


class BlockType(str, Enum):
    """Notion block types."""
    PARAGRAPH = "paragraph"
    HEADING_1 = "heading_1"
    HEADING_2 = "heading_2"
    HEADING_3 = "heading_3"
    BULLETED_LIST_ITEM = "bulleted_list_item"
    NUMBERED_LIST_ITEM = "numbered_list_item"
    TO_DO = "to_do"
    QUOTE = "quote"
    CALLOUT = "callout"
    CODE = "code"
    DIVIDER = "divider"
    IMAGE = "image"
    TABLE = "table"
    BOOKMARK = "bookmark"


@dataclass
class RichText:
    """Notion rich text object."""
    text: str
    bold: bool = False
    italic: bool = False
    strikethrough: bool = False
    underline: bool = False
    code: bool = False
    color: str = "default"
    href: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        annotations: Dict[str, Any] = {
            "bold": self.bold,
            "italic": self.italic,
            "strikethrough": self.strikethrough,
            "underline": self.underline,
            "code": self.code,
            "color": self.color,
        }
        content: Dict[str, Any] = {"content": self.text}
        if self.href:
            content["link"] = {"url": self.href}
        return {
            "type": "text",
            "text": content,
            "annotations": annotations,
        }

    @classmethod
    def from_str(cls, text: str) -> "RichText":
        return cls(text=text)

    @classmethod
    def list_from_str(cls, text: str) -> List["RichText"]:
        return [cls(text=text)]


@dataclass
class NotionBlock:
    """Represents a Notion block."""
    id: str
    block_type: BlockType
    rich_text: List[RichText] = field(default_factory=list)
    children: List["NotionBlock"] = field(default_factory=list)
    has_children: bool = False
    parent_type: str = ""
    parent_id: str = ""
    created_time: str = ""
    last_edited_time: str = ""
    extra: Dict[str, Any] = field(default_factory=dict)


@dataclass
class NotionPage:
    """Represents a Notion page."""
    id: str
    url: str = ""
    title: str = ""
    properties: Dict[str, Any] = field(default_factory=dict)
    parent_type: str = ""
    parent_id: str = ""
    created_time: str = ""
    last_edited_time: str = ""
    archived: bool = False
    icon: Optional[Dict[str, Any]] = None
    cover: Optional[Dict[str, Any]] = None
    extra: Dict[str, Any] = field(default_factory=dict)


@dataclass
class NotionDatabase:
    """Represents a Notion database."""
    id: str
    url: str = ""
    title: str = ""
    properties: Dict[str, Any] = field(default_factory=dict)
    parent_type: str = ""
    parent_id: str = ""
    created_time: str = ""
    last_edited_time: str = ""
    archived: bool = False
    extra: Dict[str, Any] = field(default_factory=dict)


@dataclass
class NotionUser:
    """Represents a Notion user."""
    id: str
    name: str = ""
    user_type: str = "person"
    email: str = ""
    avatar_url: str = ""
    extra: Dict[str, Any] = field(default_factory=dict)


@dataclass
class DatabaseQueryFilter:
    """Filter for database queries."""
    property_name: str
    filter_type: str
    condition: str
    value: Any = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "property": self.property_name,
            self.filter_type: {
                self.condition: self.value,
            },
        }


class NotionAdapter(BaseIntegrationAdapter):
    """
    Notion integration adapter for LiuHao AI OS.

    Supports:
    - Database querying with filters, sorts, and pagination
    - Database property schema inspection and manipulation
    - Page creation within databases or as child pages
    - Page editing (properties, content blocks)
    - Block operations (create, read, update, delete, append children)
    - Search across workspace (pages, databases, blocks)
    - User listing and lookup
    - Workspace info
    """

    def __init__(self, config: IntegrationConfig):
        """
        Initialize Notion adapter.

        Args:
            config: Config with Notion integration token (access_token).
        """
        if config.auth_type == AuthType.NONE:
            config.auth_type = AuthType.BEARER_TOKEN
        if not config.base_url:
            config.base_url = "https://api.notion.com/v1/"
        # Notion rate limit: 3 requests per second, roughly 180/min
        if config.rate_limit_per_minute == 60:
            config.rate_limit_per_minute = 180
        super().__init__(config)
        self._workspace_name: Optional[str] = None
        self._workspace_id: Optional[str] = None
        self._bot_id: Optional[str] = None
        self._notion_version: str = "2022-06-28"

    # ------------------------------------------------------------------
    # Base interface
    # ------------------------------------------------------------------

    def connect(self) -> bool:
        """Connect to Notion API and validate token."""
        self._status = ConnectionStatus.CONNECTING
        self._logger.info("Connecting to Notion API...")

        try:
            data = self._api_call("GET", "users/me")
            self._bot_id = data.get("id")
            self._workspace_name = data.get("bot", {}).get("workspace_name")
            self._workspace_id = data.get("bot", {}).get("workspace_id")
            self._status = ConnectionStatus.CONNECTED
            self._connected_at = time.time()
            self._logger.info(
                "Connected to Notion workspace: %s",
                self._workspace_name,
            )
            return True
        except IntegrationError:
            raise
        except Exception as e:
            self._status = ConnectionStatus.ERROR
            raise self._handle_error(f"Connection error: {e}")

    def disconnect(self) -> bool:
        """Disconnect from Notion."""
        self._status = ConnectionStatus.DISCONNECTED
        self._workspace_name = None
        self._workspace_id = None
        self._bot_id = None
        self._session = None
        self._logger.info("Disconnected from Notion")
        return True

    def is_connected(self) -> bool:
        return self._status == ConnectionStatus.CONNECTED

    def get_status(self) -> IntegrationStatus:
        return self._build_status({
            "workspace_name": self._workspace_name,
            "workspace_id": self._workspace_id,
            "bot_id": self._bot_id,
            "notion_version": self._notion_version,
        })

    def test_connection(self) -> bool:
        try:
            if not self.is_connected():
                return False
            result = self._api_call("GET", "users/me")
            return "id" in result
        except Exception:
            return False

    # ------------------------------------------------------------------
    # Database operations
    # ------------------------------------------------------------------

    def get_database(self, database_id: str) -> NotionDatabase:
        """
        Get a database by ID.

        Args:
            database_id: Notion database ID.

        Returns:
            NotionDatabase object.
        """
        data = self._api_call("GET", f"databases/{database_id}")
        return self._parse_database(data)

    def query_database(self, database_id: str,
                       filter_obj: Optional[Dict[str, Any]] = None,
                       sorts: Optional[List[Dict[str, Any]]] = None,
                       page_size: int = 100,
                       start_cursor: Optional[str] = None
                       ) -> Tuple[List[NotionPage], Optional[str], bool]:
        """
        Query a database with optional filters and sorting.

        Args:
            database_id: Database ID.
            filter_obj: Notion filter object (dict).
            sorts: List of sort objects.
            page_size: Results per page (max 100).
            start_cursor: Pagination cursor.

        Returns:
            Tuple of (pages list, next_cursor or None, has_more).
        """
        payload: Dict[str, Any] = {"page_size": min(page_size, 100)}
        if filter_obj:
            payload["filter"] = filter_obj
        if sorts:
            payload["sorts"] = sorts
        if start_cursor:
            payload["start_cursor"] = start_cursor

        data = self._api_call(
            "POST", f"databases/{database_id}/query", json_body=payload,
        )
        pages = [self._parse_page(p) for p in data.get("results", [])]
        next_cursor = data.get("next_cursor")
        has_more = data.get("has_more", False)
        return pages, next_cursor, has_more

    def create_database(self, parent_page_id: str,
                        title: str,
                        properties: Dict[str, Any],
                        icon: Optional[Dict[str, Any]] = None,
                        cover: Optional[Dict[str, Any]] = None
                        ) -> NotionDatabase:
        """
        Create a new database as a child of a page.

        Args:
            parent_page_id: Parent page ID.
            title: Database title.
            properties: Property schema definition.
            icon: Optional icon definition.
            cover: Optional cover image.

        Returns:
            Created NotionDatabase.
        """
        payload: Dict[str, Any] = {
            "parent": {"type": "page_id", "page_id": parent_page_id},
            "title": [RichText(title).to_dict()],
            "properties": properties,
        }
        if icon:
            payload["icon"] = icon
        if cover:
            payload["cover"] = cover

        data = self._api_call("POST", "databases", json_body=payload)
        return self._parse_database(data)

    def update_database(self, database_id: str,
                        title: Optional[str] = None,
                        properties: Optional[Dict[str, Any]] = None,
                        icon: Optional[Dict[str, Any]] = None,
                        cover: Optional[Dict[str, Any]] = None
                        ) -> NotionDatabase:
        """Update an existing database."""
        payload: Dict[str, Any] = {}
        if title is not None:
            payload["title"] = [RichText(title).to_dict()]
        if properties is not None:
            payload["properties"] = properties
        if icon is not None:
            payload["icon"] = icon
        if cover is not None:
            payload["cover"] = cover

        data = self._api_call(
            "PATCH", f"databases/{database_id}", json_body=payload,
        )
        return self._parse_database(data)

    def list_databases(self, page_size: int = 100,
                       start_cursor: Optional[str] = None
                       ) -> Tuple[List[NotionDatabase], Optional[str], bool]:
        """List databases shared with the integration."""
        params: Dict[str, Any] = {"page_size": min(page_size, 100)}
        if start_cursor:
            params["start_cursor"] = start_cursor

        data = self._api_call("GET", "databases", params=params)
        databases = [self._parse_database(d) for d in data.get("results", [])]
        return databases, data.get("next_cursor"), data.get("has_more", False)

    # ------------------------------------------------------------------
    # Page operations
    # ------------------------------------------------------------------

    def get_page(self, page_id: str) -> NotionPage:
        """Get a page by ID."""
        data = self._api_call("GET", f"pages/{page_id}")
        return self._parse_page(data)

    def create_page_in_database(self, database_id: str,
                                properties: Dict[str, Any],
                                children: Optional[List[Dict[str, Any]]] = None,
                                icon: Optional[Dict[str, Any]] = None,
                                cover: Optional[Dict[str, Any]] = None
                                ) -> NotionPage:
        """
        Create a new page inside a database.

        Args:
            database_id: Parent database ID.
            properties: Page properties (must match database schema).
            children: Optional initial content blocks.
            icon: Optional page icon.
            cover: Optional page cover.

        Returns:
            Created NotionPage.
        """
        payload: Dict[str, Any] = {
            "parent": {"database_id": database_id},
            "properties": properties,
        }
        if children:
            payload["children"] = children
        if icon:
            payload["icon"] = icon
        if cover:
            payload["cover"] = cover

        data = self._api_call("POST", "pages", json_body=payload)
        return self._parse_page(data)

    def create_page_as_child(self, parent_page_id: str,
                             title: str,
                             children: Optional[List[Dict[str, Any]]] = None,
                             icon: Optional[Dict[str, Any]] = None,
                             cover: Optional[Dict[str, Any]] = None
                             ) -> NotionPage:
        """
        Create a new page as a child of another page.

        Args:
            parent_page_id: Parent page ID.
            title: Page title.
            children: Optional initial content blocks.
            icon: Optional page icon.
            cover: Optional page cover.

        Returns:
            Created NotionPage.
        """
        payload: Dict[str, Any] = {
            "parent": {"page_id": parent_page_id},
            "properties": {
                "title": [RichText(title).to_dict()],
            },
        }
        if children:
            payload["children"] = children
        if icon:
            payload["icon"] = icon
        if cover:
            payload["cover"] = cover

        data = self._api_call("POST", "pages", json_body=payload)
        return self._parse_page(data)

    def update_page_properties(self, page_id: str,
                               properties: Dict[str, Any],
                               archived: Optional[bool] = None,
                               icon: Optional[Dict[str, Any]] = None,
                               cover: Optional[Dict[str, Any]] = None
                               ) -> NotionPage:
        """
        Update page properties.

        Args:
            page_id: Page ID.
            properties: Updated properties dict.
            archived: Whether to archive (delete) the page.
            icon: Optional new icon.
            cover: Optional new cover.

        Returns:
            Updated NotionPage.
        """
        payload: Dict[str, Any] = {"properties": properties}
        if archived is not None:
            payload["archived"] = archived
        if icon is not None:
            payload["icon"] = icon
        if cover is not None:
            payload["cover"] = cover

        data = self._api_call("PATCH", f"pages/{page_id}", json_body=payload)
        return self._parse_page(data)

    def archive_page(self, page_id: str) -> NotionPage:
        """Archive (soft-delete) a page."""
        return self.update_page_properties(page_id, {}, archived=True)

    # ------------------------------------------------------------------
    # Block operations
    # ------------------------------------------------------------------

    def get_block_children(self, block_id: str,
                           page_size: int = 100,
                           start_cursor: Optional[str] = None
                           ) -> Tuple[List[NotionBlock], Optional[str], bool]:
        """
        Get child blocks of a page or block.

        Args:
            block_id: Block or page ID.
            page_size: Results per page (max 100).
            start_cursor: Pagination cursor.

        Returns:
            Tuple of (blocks list, next_cursor, has_more).
        """
        params: Dict[str, Any] = {"page_size": min(page_size, 100)}
        if start_cursor:
            params["start_cursor"] = start_cursor

        data = self._api_call(
            "GET", f"blocks/{block_id}/children", params=params,
        )
        blocks = [self._parse_block(b) for b in data.get("results", [])]
        return blocks, data.get("next_cursor"), data.get("has_more", False)

    def get_block(self, block_id: str) -> NotionBlock:
        """Get a specific block by ID."""
        data = self._api_call("GET", f"blocks/{block_id}")
        return self._parse_block(data)

    def append_block_children(self, block_id: str,
                              children: List[Dict[str, Any]],
                              after: Optional[str] = None
                              ) -> List[NotionBlock]:
        """
        Append child blocks to a parent block/page.

        Args:
            block_id: Parent block or page ID.
            children: List of block objects to append.
            after: Optional block ID to insert after.

        Returns:
            List of created NotionBlock objects.
        """
        payload: Dict[str, Any] = {"children": children}
        if after:
            payload["after"] = after

        data = self._api_call(
            "PATCH", f"blocks/{block_id}/children", json_body=payload,
        )
        return [self._parse_block(b) for b in data.get("results", [])]

    def update_block(self, block_id: str,
                     block_type: Optional[str] = None,
                     content: Optional[Dict[str, Any]] = None,
                     archived: Optional[bool] = None) -> NotionBlock:
        """
        Update a block's content.

        Args:
            block_id: Block ID.
            block_type: Block type (e.g., "paragraph", "heading_1").
            content: Updated content dict for the block type.
            archived: Whether to archive the block.

        Returns:
            Updated NotionBlock.
        """
        payload: Dict[str, Any] = {}
        if block_type and content:
            payload[block_type] = content
        if archived is not None:
            payload["archived"] = archived

        data = self._api_call("PATCH", f"blocks/{block_id}", json_body=payload)
        return self._parse_block(data)

    def delete_block(self, block_id: str) -> bool:
        """Delete (archive) a block. Returns True on success."""
        try:
            self._api_call("DELETE", f"blocks/{block_id}")
            return True
        except Exception:
            return False

    def append_paragraph(self, page_id: str, text: str,
                         color: str = "default") -> List[NotionBlock]:
        """Append a simple paragraph block to a page."""
        block = {
            "object": "block",
            "type": "paragraph",
            "paragraph": {
                "rich_text": [
                    RichText(text=text, color=color).to_dict(),
                ],
            },
        }
        return self.append_block_children(page_id, [block])

    def append_heading(self, page_id: str, text: str,
                       level: int = 1) -> List[NotionBlock]:
        """Append a heading block to a page."""
        heading_type = f"heading_{min(max(level, 1), 3)}"
        block = {
            "object": "block",
            "type": heading_type,
            heading_type: {
                "rich_text": [RichText(text=text).to_dict()],
            },
        }
        return self.append_block_children(page_id, [block])

    def append_bulleted_list(self, page_id: str,
                             items: List[str]) -> List[NotionBlock]:
        """Append bulleted list items to a page."""
        blocks = [
            {
                "object": "block",
                "type": "bulleted_list_item",
                "bulleted_list_item": {
                    "rich_text": [RichText(text=item).to_dict()],
                },
            }
            for item in items
        ]
        return self.append_block_children(page_id, blocks)

    def append_code_block(self, page_id: str, code: str,
                          language: str = "python") -> List[NotionBlock]:
        """Append a code block to a page."""
        block = {
            "object": "block",
            "type": "code",
            "code": {
                "rich_text": [RichText(text=code).to_dict()],
                "language": language,
            },
        }
        return self.append_block_children(page_id, [block])

    # ------------------------------------------------------------------
    # Search
    # ------------------------------------------------------------------

    def search(self, query: str,
               object_type: Optional[NotionObjectType] = None,
               page_size: int = 100,
               start_cursor: Optional[str] = None,
               sort_direction: str = "descending",
               sort_timestamp: str = "last_edited_time"
               ) -> Tuple[List[Dict[str, Any]], Optional[str], bool]:
        """
        Search across Notion workspace.

        Args:
            query: Search query text.
            object_type: Filter by object type (page, database, etc.).
            page_size: Results per page (max 100).
            start_cursor: Pagination cursor.
            sort_direction: "ascending" or "descending".
            sort_timestamp: "last_edited_time" or "created_time".

        Returns:
            Tuple of (results list, next_cursor, has_more).
        """
        payload: Dict[str, Any] = {
            "query": query,
            "page_size": min(page_size, 100),
            "sort": {
                "direction": sort_direction,
                "timestamp": sort_timestamp,
            },
        }
        if object_type:
            payload["filter"] = {
                "value": object_type.value,
                "property": "object",
            }
        if start_cursor:
            payload["start_cursor"] = start_cursor

        data = self._api_call("POST", "search", json_body=payload)
        return (
            data.get("results", []),
            data.get("next_cursor"),
            data.get("has_more", False),
        )

    def search_pages(self, query: str,
                     page_size: int = 100,
                     start_cursor: Optional[str] = None
                     ) -> Tuple[List[NotionPage], Optional[str], bool]:
        """Search for pages only."""
        results, cursor, has_more = self.search(
            query, object_type=NotionObjectType.PAGE,
            page_size=page_size, start_cursor=start_cursor,
        )
        pages = [self._parse_page(r) for r in results]
        return pages, cursor, has_more

    def search_databases(self, query: str,
                         page_size: int = 100,
                         start_cursor: Optional[str] = None
                         ) -> Tuple[List[NotionDatabase], Optional[str], bool]:
        """Search for databases only."""
        results, cursor, has_more = self.search(
            query, object_type=NotionObjectType.DATABASE,
            page_size=page_size, start_cursor=start_cursor,
        )
        databases = [self._parse_database(r) for r in results]
        return databases, cursor, has_more

    # ------------------------------------------------------------------
    # Users and workspace
    # ------------------------------------------------------------------

    def list_users(self, page_size: int = 100,
                   start_cursor: Optional[str] = None
                   ) -> Tuple[List[NotionUser], Optional[str], bool]:
        """List all users in the workspace."""
        params: Dict[str, Any] = {"page_size": min(page_size, 100)}
        if start_cursor:
            params["start_cursor"] = start_cursor

        data = self._api_call("GET", "users", params=params)
        users = [self._parse_user(u) for u in data.get("results", [])]
        return users, data.get("next_cursor"), data.get("has_more", False)

    def get_user(self, user_id: str) -> NotionUser:
        """Get a specific user by ID."""
        data = self._api_call("GET", f"users/{user_id}")
        return self._parse_user(data)

    def get_bot_user(self) -> NotionUser:
        """Get the bot/integration user."""
        data = self._api_call("GET", "users/me")
        return self._parse_user(data)

    def get_workspace_info(self) -> Dict[str, Any]:
        """
        Get workspace information.

        Note: Notion API doesn't have a direct workspace endpoint,
        so we derive info from the bot user endpoint.
        """
        data = self._api_call("GET", "users/me")
        bot = data.get("bot", {})
        return {
            "workspace_name": bot.get("workspace_name", ""),
            "workspace_id": bot.get("workspace_id", ""),
            "bot_id": data.get("id", ""),
            "bot_name": data.get("name", ""),
        }

    # ------------------------------------------------------------------
    # Page property helpers
    # ------------------------------------------------------------------

    def get_page_property(self, page_id: str, property_id: str,
                          page_size: int = 100,
                          start_cursor: Optional[str] = None
                          ) -> Dict[str, Any]:
        """
        Get a specific property value for a page.

        Useful for rich_text, title, relation, and people properties
        that may be paginated.
        """
        params: Dict[str, Any] = {"page_size": min(page_size, 100)}
        if start_cursor:
            params["start_cursor"] = start_cursor

        return self._api_call(
            "GET",
            f"pages/{page_id}/properties/{property_id}",
            params=params,
        )

    def extract_title_from_page(self, page: NotionPage) -> str:
        """Extract the title text from a page's properties."""
        if page.title:
            return page.title
        # Find the title property
        for prop_name, prop_value in page.properties.items():
            if prop_value.get("type") == "title":
                title_items = prop_value.get("title", [])
                if title_items:
                    return "".join(
                        t.get("plain_text", "") for t in title_items
                    )
        return ""

    # ------------------------------------------------------------------
    # Parsing helpers
    # ------------------------------------------------------------------

    def _parse_database(self, data: Dict[str, Any]) -> NotionDatabase:
        title_items = data.get("title", [])
        title = "".join(t.get("plain_text", "") for t in title_items)
        parent = data.get("parent", {})
        return NotionDatabase(
            id=data.get("id", ""),
            url=data.get("url", ""),
            title=title,
            properties=data.get("properties", {}),
            parent_type=parent.get("type", ""),
            parent_id=(parent.get("page_id")
                       or parent.get("workspace")
                       or parent.get("database_id", "")),
            created_time=data.get("created_time", ""),
            last_edited_time=data.get("last_edited_time", ""),
            archived=data.get("archived", False),
            extra=data,
        )

    def _parse_page(self, data: Dict[str, Any]) -> NotionPage:
        parent = data.get("parent", {})
        properties = data.get("properties", {})

        # Extract title
        title = ""
        for prop_name, prop_value in properties.items():
            if prop_value.get("type") == "title":
                title_items = prop_value.get("title", [])
                title = "".join(
                    t.get("plain_text", "") for t in title_items
                )
                break

        return NotionPage(
            id=data.get("id", ""),
            url=data.get("url", ""),
            title=title,
            properties=properties,
            parent_type=parent.get("type", ""),
            parent_id=(parent.get("database_id")
                       or parent.get("page_id")
                       or parent.get("workspace", "")),
            created_time=data.get("created_time", ""),
            last_edited_time=data.get("last_edited_time", ""),
            archived=data.get("archived", False),
            icon=data.get("icon"),
            cover=data.get("cover"),
            extra=data,
        )

    def _parse_block(self, data: Dict[str, Any]) -> NotionBlock:
        block_type_str = data.get("type", "paragraph")
        try:
            block_type = BlockType(block_type_str)
        except ValueError:
            block_type = BlockType.PARAGRAPH

        # Extract rich text from the block type data
        block_data = data.get(block_type_str, {})
        rich_text_data = block_data.get("rich_text", [])
        rich_text = [
            RichText(
                text=rt.get("plain_text", ""),
                bold=rt.get("annotations", {}).get("bold", False),
                italic=rt.get("annotations", {}).get("italic", False),
                color=rt.get("annotations", {}).get("color", "default"),
            )
            for rt in rich_text_data
        ]

        parent = data.get("parent", {})
        return NotionBlock(
            id=data.get("id", ""),
            block_type=block_type,
            rich_text=rich_text,
            has_children=data.get("has_children", False),
            parent_type=parent.get("type", ""),
            parent_id=(parent.get("page_id")
                       or parent.get("block_id", "")),
            created_time=data.get("created_time", ""),
            last_edited_time=data.get("last_edited_time", ""),
            extra=data,
        )

    def _parse_user(self, data: Dict[str, Any]) -> NotionUser:
        return NotionUser(
            id=data.get("id", ""),
            name=data.get("name", ""),
            user_type=data.get("type", "person"),
            email=data.get("person", {}).get("email", ""),
            avatar_url=data.get("avatar_url", ""),
            extra=data,
        )

    # ------------------------------------------------------------------
    # Internal API helper
    # ------------------------------------------------------------------

    def _api_call(self, method: str, path: str,
                  params: Optional[Dict[str, Any]] = None,
                  json_body: Optional[Dict[str, Any]] = None,
                  _retry_count: int = 0) -> Any:
        """Make a Notion API call with retry on rate limits."""
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
        """Execute a single Notion API request."""
        self._check_rate_limit()
        self._mark_request()

        url = self._build_url(path)
        token = self.config.access_token or self.config.api_key or ""
        headers = {
            "Authorization": f"Bearer {token}",
            "Notion-Version": self._notion_version,
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

                if resp.status == 204:
                    return {}
                return json.loads(resp_body)

        except urllib.error.HTTPError as e:
            resp_body = e.read().decode("utf-8", errors="replace")
            self._log_response(method, url, e.code, resp_body)

            if e.code == 429:
                retry_after = float(e.headers.get("Retry-After", 10))
                self._rate_limit_hits += 1
                raise RateLimitError(
                    f"Notion rate limited, retry after {retry_after}s",
                    retry_after=retry_after,
                    adapter_name=self.config.name,
                )

            if e.code == 401:
                self._status = ConnectionStatus.ERROR
                raise self._handle_error(
                    "Authentication failed: invalid Notion token",
                    status_code=401,
                    response_body=resp_body,
                )

            raise self._handle_error(
                f"Notion API error {e.code}: {e.reason}",
                status_code=e.code,
                response_body=resp_body,
            )
        except (RateLimitError, IntegrationError):
            raise
        except Exception as e:
            raise self._handle_error(f"Request failed: {e}")
