"""
Monday.com Integration Adapter for LiuHao AI OS.

Provides integration with Monday.com for boards management, items (CRUD),
columns and column values, groups, workspaces, users, updates/comments,
and board views.
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


class MondayColumnType(str, Enum):
    """Monday.com column types."""
    NAME = "name"
    TEXT = "text"
    NUMERIC = "numeric"
    STATUS = "status"
    DATE = "date"
    PEOPLE = "people"
    DROPDOWN = "dropdown"
    CHECKBOX = "checkbox"
    EMAIL = "email"
    PHONE = "phone"
    LINK = "link"
    FILE = "file"
    RATING = "rating"
    TIMELINE = "timeline"
    DEPENDENCY = "dependency"
    SUBITEMS = "subtasks"
    FORMULA = "formula"
    COLOR_PICKER = "color_picker"


class MondayBoardState(str, Enum):
    """Board state values."""
    ACTIVE = "active"
    ARCHIVED = "archived"
    DELETED = "deleted"
    ALL = "all"


class MondayUserRole(str, Enum):
    """User role types."""
    ADMIN = "admin"
    MEMBER = "member"
    VIEWER = "viewer"
    GUEST = "guest"


@dataclass
class MondayBoard:
    """Represents a Monday.com board."""
    id: str = ""
    name: str = ""
    description: str = ""
    state: MondayBoardState = MondayBoardState.ACTIVE
    board_kind: str = "public"
    workspace_id: int = 0
    folder_id: int = 0
    owner: str = ""
    url: str = ""
    groups_count: int = 0
    items_count: int = 0
    columns: List[Dict[str, Any]] = field(default_factory=list)
    groups: List[Dict[str, Any]] = field(default_factory=list)
    permissions: str = ""
    extra: Dict[str, Any] = field(default_factory=dict)


@dataclass
class MondayItem:
    """Represents a Monday.com item (row)."""
    id: str = ""
    name: str = ""
    board_id: str = ""
    group_id: str = ""
    group_title: str = ""
    state: str = "active"
    created_at: str = ""
    updated_at: str = ""
    column_values: List[Dict[str, Any]] = field(default_factory=list)
    creator_id: str = ""
    url: str = ""
    subscribers: List[Dict[str, Any]] = field(default_factory=list)
    extra: Dict[str, Any] = field(default_factory=dict)


@dataclass
class MondayGroup:
    """Represents a Monday.com group."""
    id: str
    title: str = ""
    board_id: str = ""
    color: str = ""
    archived: bool = False
    deleted: bool = False
    items_count: int = 0
    position: str = ""
    extra: Dict[str, Any] = field(default_factory=dict)


@dataclass
class MondayColumn:
    """Represents a Monday.com column definition."""
    id: str
    title: str = ""
    type: MondayColumnType = MondayColumnType.TEXT
    board_id: str = ""
    settings_str: str = ""
    archived: bool = False
    extra: Dict[str, Any] = field(default_factory=dict)


@dataclass
class MondayColumnValue:
    """Represents a column value on an item."""
    column_id: str
    value: str = ""
    text: str = ""
    type: str = ""
    extra: Dict[str, Any] = field(default_factory=dict)


@dataclass
class MondayWorkspace:
    """Represents a Monday.com workspace."""
    id: str = ""
    name: str = ""
    kind: str = "open"
    description: str = ""
    team_id: int = 0
    extra: Dict[str, Any] = field(default_factory=dict)


@dataclass
class MondayUser:
    """Represents a Monday.com user."""
    id: str = ""
    name: str = ""
    email: str = ""
    title: str = ""
    url: str = ""
    role: MondayUserRole = MondayUserRole.MEMBER
    enabled: bool = True
    created_at: str = ""
    photo_original: str = ""
    time_zone_identifier: str = ""
    teams: List[Dict[str, Any]] = field(default_factory=list)
    extra: Dict[str, Any] = field(default_factory=dict)


@dataclass
class MondayUpdate:
    """Represents a Monday.com update (comment)."""
    id: str = ""
    body: str = ""
    item_id: str = ""
    creator_id: str = ""
    creator_name: str = ""
    created_at: str = ""
    updated_at: str = ""
    replies_count: int = 0
    likes_count: int = 0
    text_body: str = ""
    extra: Dict[str, Any] = field(default_factory=dict)


@dataclass
class MondayBoardView:
    """Represents a Monday.com board view."""
    id: str
    name: str = ""
    type: str = ""
    board_id: str = ""
    settings_str: str = ""
    extra: Dict[str, Any] = field(default_factory=dict)


class MondayAdapter(BaseIntegrationAdapter):
    """
    Monday.com integration adapter for LiuHao AI OS.

    Supports:
    - Boards management (create, list, get, delete)
    - Items (CRUD)
    - Columns and column values
    - Groups
    - Workspaces
    - Users
    - Updates/comments
    - Board views
    - GraphQL API access
    """

    def __init__(self, config: IntegrationConfig):
        if config.auth_type == AuthType.NONE:
            config.auth_type = AuthType.API_KEY
        if not config.base_url:
            config.base_url = "https://api.monday.com/v2"
        super().__init__(config)
        self._user_id: Optional[str] = None
        self._user_name: Optional[str] = None
        self._user_email: Optional[str] = None
        self._account_id: Optional[str] = None
        self._cached_boards: Dict[str, MondayBoard] = {}
        self._cached_items: Dict[str, MondayItem] = {}

    # ------------------------------------------------------------------
    # Base interface
    # ------------------------------------------------------------------

    def connect(self) -> bool:
        self._status = ConnectionStatus.CONNECTING
        self._logger.info("Connecting to Monday.com API...")

        try:
            query = """
            query {
              me {
                id
                name
                email
                account {
                  id
                  name
                }
              }
            }
            """
            result = self._graphql(query)
            me = result.get("data", {}).get("me", {})
            self._user_id = me.get("id", "")
            self._user_name = me.get("name", "")
            self._user_email = me.get("email", "")
            account = me.get("account", {})
            self._account_id = str(account.get("id", "")) if account else ""
            self._status = ConnectionStatus.CONNECTED
            self._connected_at = time.time()
            self._logger.info(
                "Connected to Monday.com as %s (%s)",
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
        self._user_id = None
        self._user_name = None
        self._user_email = None
        self._account_id = None
        self._cached_boards = {}
        self._cached_items = {}
        self._session = None
        self._logger.info("Disconnected from Monday.com")
        return True

    def is_connected(self) -> bool:
        return self._status == ConnectionStatus.CONNECTED

    def get_status(self) -> IntegrationStatus:
        return self._build_status({
            "user_name": self._user_name,
            "user_id": self._user_id,
            "user_email": self._user_email,
            "account_id": self._account_id,
            "cached_boards": len(self._cached_boards),
            "cached_items": len(self._cached_items),
        })

    def test_connection(self) -> bool:
        try:
            if not self.is_connected():
                return False
            query = "query { me { id } }"
            result = self._graphql(query)
            me = result.get("data", {}).get("me", {})
            return "id" in me
        except Exception:
            return False

    # ------------------------------------------------------------------
    # Boards
    # ------------------------------------------------------------------

    def create_board(self, name: str,
                     board_kind: str = "public",
                     workspace_id: Optional[int] = None,
                     description: str = "",
                     **kwargs: Any) -> MondayBoard:
        mutation = """
        mutation CreateBoard($name: String!, $boardKind: BoardKind!,
                             $workspaceId: Int, $description: String) {
          create_board(board_name: $name, board_kind: $boardKind,
                       workspace_id: $workspaceId, description: $description) {
            id
            name
            board_kind
            description
            url
            workspace_id
            owner
            state
          }
        }
        """
        variables: Dict[str, Any] = {
            "name": name,
            "boardKind": board_kind,
        }
        if workspace_id is not None:
            variables["workspaceId"] = workspace_id
        if description:
            variables["description"] = description
        variables.update(kwargs)

        result = self._graphql(mutation, variables)
        board_data = result.get("data", {}).get("create_board", {})
        board = self._parse_board(board_data)
        if board.id:
            self._cached_boards[board.id] = board
        return board

    def get_board(self, board_id: str,
                  columns: bool = True,
                  groups: bool = True,
                  items_page: bool = False) -> MondayBoard:
        col_field = "columns { id title type settings_str archived }" if columns else ""
        group_field = "groups { id title color archived deleted position }" if groups else ""
        items_field = "items_page { items { id name } }" if items_page else ""

        query = f"""
        query Board($id: [ID!]!) {{
          boards(ids: $id) {{
            id
            name
            description
            state
            board_kind
            workspace_id
            owner
            url
            groups_count
            items_count
            permissions
            {col_field}
            {group_field}
            {items_field}
          }}
        }}
        """
        result = self._graphql(query, {"id": board_id})
        boards = result.get("data", {}).get("boards", [])
        if not boards:
            raise self._handle_error(f"Board {board_id} not found")
        board = self._parse_board(boards[0])
        if board.id:
            self._cached_boards[board.id] = board
        return board

    def list_boards(self, limit: int = 25,
                    page: int = 1,
                    state: MondayBoardState = MondayBoardState.ACTIVE,
                    workspace_id: Optional[int] = None,
                    **kwargs: Any) -> Dict[str, Any]:
        query = """
        query Boards($limit: Int, $page: Int, $state: State, $workspaceIds: [Int]) {
          boards(limit: $limit, page: $page, state: $state, workspace_ids: $workspaceIds) {
            id
            name
            description
            state
            board_kind
            workspace_id
            url
            owner
            groups_count
            items_count
          }
        }
        """
        variables: Dict[str, Any] = {
            "limit": limit,
            "page": page,
            "state": state.value,
        }
        if workspace_id is not None:
            variables["workspaceIds"] = [workspace_id]
        variables.update(kwargs)

        result = self._graphql(query, variables)
        boards_data = result.get("data", {}).get("boards", [])
        boards = [self._parse_board(b) for b in boards_data]
        for board in boards:
            if board.id:
                self._cached_boards[board.id] = board

        return {
            "boards": boards,
            "page": page,
            "limit": limit,
        }

    def archive_board(self, board_id: str) -> bool:
        mutation = """
        mutation ArchiveBoard($id: ID!) {
          archive_board(board_id: $id) {
            id
            state
          }
        }
        """
        result = self._graphql(mutation, {"id": board_id})
        data = result.get("data", {}).get("archive_board", {})
        return data.get("state") == "archived"

    def delete_board(self, board_id: str) -> bool:
        mutation = """
        mutation DeleteBoard($id: ID!) {
          delete_board(board_id: $id) {
            id
          }
        }
        """
        result = self._graphql(mutation, {"id": board_id})
        success = result.get("data", {}).get("delete_board") is not None
        if success and board_id in self._cached_boards:
            del self._cached_boards[board_id]
        return success

    def _parse_board(self, data: Dict[str, Any]) -> MondayBoard:
        return MondayBoard(
            id=str(data.get("id", "")),
            name=data.get("name", ""),
            description=data.get("description", ""),
            state=MondayBoardState(data.get("state", "active")),
            board_kind=data.get("board_kind", "public"),
            workspace_id=int(data.get("workspace_id", 0)) if data.get("workspace_id") else 0,
            owner=data.get("owner", ""),
            url=data.get("url", ""),
            groups_count=data.get("groups_count", 0),
            items_count=data.get("items_count", 0),
            columns=data.get("columns", []),
            groups=data.get("groups", []),
            permissions=data.get("permissions", ""),
            extra=data,
        )

    # ------------------------------------------------------------------
    # Items
    # ------------------------------------------------------------------

    def create_item(self, board_id: str,
                    item_name: str,
                    group_id: str = "",
                    column_values: Optional[Dict[str, Any]] = None,
                    **kwargs: Any) -> MondayItem:
        mutation = """
        mutation CreateItem($boardId: ID!, $itemName: String!,
                            $groupId: String, $columnValues: JSON) {
          create_item(board_id: $boardId, item_name: $itemName,
                      group_id: $groupId, column_values: $columnValues) {
            id
            name
            board { id }
            group { id title }
            state
            created_at
            updated_at
            creator { id }
            url
            column_values {
              id
              text
              value
              type
            }
          }
        }
        """
        variables: Dict[str, Any] = {
            "boardId": board_id,
            "itemName": item_name,
        }
        if group_id:
            variables["groupId"] = group_id
        if column_values:
            variables["columnValues"] = json.dumps(column_values)
        variables.update(kwargs)

        result = self._graphql(mutation, variables)
        item_data = result.get("data", {}).get("create_item", {})
        item = self._parse_item(item_data)
        if item.id:
            self._cached_items[item.id] = item
        return item

    def get_item(self, item_id: str,
                 column_values: bool = True,
                 group: bool = True) -> MondayItem:
        col_field = "column_values { id text value type }" if column_values else ""
        group_field = "group { id title }" if group else ""

        query = f"""
        query Item($id: [ID!]!) {{
          items(ids: $id) {{
            id
            name
            state
            created_at
            updated_at
            creator {{ id name }}
            url
            board {{ id }}
            {group_field}
            {col_field}
          }}
        }}
        """
        result = self._graphql(query, {"id": item_id})
        items = result.get("data", {}).get("items", [])
        if not items:
            raise self._handle_error(f"Item {item_id} not found")
        item = self._parse_item(items[0])
        if item.id:
            self._cached_items[item.id] = item
        return item

    def update_item(self, item_id: str,
                    board_id: str,
                    column_values: Dict[str, Any]) -> MondayItem:
        mutation = """
        mutation ChangeColumnValues($itemId: ID!, $boardId: ID!,
                                    $columnValues: JSON!) {
          change_multiple_column_values(item_id: $itemId, board_id: $boardId,
                                        column_values: $columnValues) {
            id
            name
            column_values {
              id
              text
              value
              type
            }
            updated_at
          }
        }
        """
        result = self._graphql(mutation, {
            "itemId": item_id,
            "boardId": board_id,
            "columnValues": json.dumps(column_values),
        })
        item_data = result.get("data", {}).get("change_multiple_column_values", {})
        item = self._parse_item(item_data)
        if item.id:
            self._cached_items[item.id] = item
        return item

    def update_item_name(self, item_id: str, new_name: str) -> MondayItem:
        mutation = """
        mutation UpdateItemName($itemId: ID!, $name: String!) {
          change_item_name(item_id: $itemId, item_name: $name) {
            id
            name
            updated_at
          }
        }
        """
        result = self._graphql(mutation, {"itemId": item_id, "name": new_name})
        item_data = result.get("data", {}).get("change_item_name", {})
        item = self._parse_item(item_data)
        if item.id:
            self._cached_items[item.id] = item
        return item

    def archive_item(self, item_id: str) -> bool:
        mutation = """
        mutation ArchiveItem($id: ID!) {
          archive_item(item_id: $id) {
            id
            state
          }
        }
        """
        result = self._graphql(mutation, {"id": item_id})
        data = result.get("data", {}).get("archive_item", {})
        return data.get("state") == "archived"

    def delete_item(self, item_id: str) -> bool:
        mutation = """
        mutation DeleteItem($id: ID!) {
          delete_item(item_id: $id) {
            id
          }
        }
        """
        result = self._graphql(mutation, {"id": item_id})
        success = result.get("data", {}).get("delete_item") is not None
        if success and item_id in self._cached_items:
            del self._cached_items[item_id]
        return success

    def list_items(self, board_id: str,
                   limit: int = 25,
                   **kwargs: Any) -> Dict[str, Any]:
        query = """
        query BoardItems($boardId: ID!, $limit: Int) {
          boards(ids: [$boardId]) {
            items_page(limit: $limit) {
              items {
                id
                name
                state
                created_at
                updated_at
                url
                group { id title }
                column_values {
                  id
                  text
                  value
                  type
                }
              }
              cursor
            }
          }
        }
        """
        result = self._graphql(query, {"boardId": board_id, "limit": limit})
        boards = result.get("data", {}).get("boards", [])
        if not boards:
            return {"items": [], "cursor": None}
        items_page = boards[0].get("items_page", {})
        items_data = items_page.get("items", [])
        items = [self._parse_item(i) for i in items_data]
        for item in items:
            if item.id:
                self._cached_items[item.id] = item

        return {
            "items": items,
            "cursor": items_page.get("cursor"),
        }

    def _parse_item(self, data: Dict[str, Any]) -> MondayItem:
        board = data.get("board", {})
        group = data.get("group", {})
        creator = data.get("creator", {})
        return MondayItem(
            id=str(data.get("id", "")),
            name=data.get("name", ""),
            board_id=str(board.get("id", "")) if board else "",
            group_id=group.get("id", "") if group else "",
            group_title=group.get("title", "") if group else "",
            state=data.get("state", "active"),
            created_at=data.get("created_at", ""),
            updated_at=data.get("updated_at", ""),
            column_values=data.get("column_values", []),
            creator_id=str(creator.get("id", "")) if creator else "",
            url=data.get("url", ""),
            subscribers=data.get("subscribers", []),
            extra=data,
        )

    # ------------------------------------------------------------------
    # Columns and column values
    # ------------------------------------------------------------------

    def list_columns(self, board_id: str) -> List[MondayColumn]:
        query = """
        query BoardColumns($boardId: [ID!]!) {
          boards(ids: $boardId) {
            columns {
              id
              title
              type
              settings_str
              archived
            }
          }
        }
        """
        result = self._graphql(query, {"boardId": board_id})
        boards = result.get("data", {}).get("boards", [])
        if not boards:
            return []
        columns_data = boards[0].get("columns", [])
        return [
            MondayColumn(
                id=c.get("id", ""),
                title=c.get("title", ""),
                type=MondayColumnType(c.get("type", "text")),
                board_id=board_id,
                settings_str=c.get("settings_str", ""),
                archived=c.get("archived", False),
                extra=c,
            )
            for c in columns_data
        ]

    def create_column(self, board_id: str,
                      title: str,
                      column_type: MondayColumnType = MondayColumnType.TEXT,
                      defaults: Optional[Dict[str, Any]] = None,
                      **kwargs: Any) -> MondayColumn:
        mutation = """
        mutation CreateColumn($boardId: ID!, $title: String!,
                              $columnType: ColumnsType!, $defaults: JSON) {
          create_column(board_id: $boardId, title: $title,
                        column_type: $columnType, defaults: $defaults) {
            id
            title
            type
            settings_str
          }
        }
        """
        variables: Dict[str, Any] = {
            "boardId": board_id,
            "title": title,
            "columnType": column_type.value.upper(),
        }
        if defaults:
            variables["defaults"] = json.dumps(defaults)
        variables.update(kwargs)

        result = self._graphql(mutation, variables)
        col_data = result.get("data", {}).get("create_column", {})
        return MondayColumn(
            id=col_data.get("id", ""),
            title=col_data.get("title", ""),
            type=MondayColumnType(col_data.get("type", "text")),
            board_id=board_id,
            settings_str=col_data.get("settings_str", ""),
            extra=col_data,
        )

    def get_column_value(self, item_id: str,
                         column_id: str) -> MondayColumnValue:
        query = """
        query ItemColumnValue($itemId: [ID!]!, $columnId: [String!]) {
          items(ids: $itemId) {
            column_values(ids: $columnId) {
              id
              text
              value
              type
            }
          }
        }
        """
        result = self._graphql(query, {
            "itemId": item_id,
            "columnId": column_id,
        })
        items = result.get("data", {}).get("items", [])
        if not items:
            raise self._handle_error(f"Item {item_id} not found")
        values = items[0].get("column_values", [])
        if not values:
            raise self._handle_error(f"Column {column_id} not found on item")
        val = values[0]
        return MondayColumnValue(
            column_id=val.get("id", column_id),
            value=val.get("value", ""),
            text=val.get("text", ""),
            type=val.get("type", ""),
            extra=val,
        )

    # ------------------------------------------------------------------
    # Groups
    # ------------------------------------------------------------------

    def create_group(self, board_id: str,
                     group_name: str,
                     **kwargs: Any) -> MondayGroup:
        mutation = """
        mutation CreateGroup($boardId: ID!, $groupName: String!) {
          create_group(board_id: $boardId, group_name: $groupName) {
            id
            title
            color
            archived
            position
          }
        }
        """
        result = self._graphql(mutation, {"boardId": board_id, "groupName": group_name})
        group_data = result.get("data", {}).get("create_group", {})
        return MondayGroup(
            id=group_data.get("id", ""),
            title=group_data.get("title", ""),
            board_id=board_id,
            color=group_data.get("color", ""),
            archived=group_data.get("archived", False),
            position=group_data.get("position", ""),
            extra=group_data,
        )

    def list_groups(self, board_id: str) -> List[MondayGroup]:
        query = """
        query BoardGroups($boardId: [ID!]!) {
          boards(ids: $boardId) {
            groups {
              id
              title
              color
              archived
              deleted
              position
              items_count
            }
          }
        }
        """
        result = self._graphql(query, {"boardId": board_id})
        boards = result.get("data", {}).get("boards", [])
        if not boards:
            return []
        groups_data = boards[0].get("groups", [])
        return [
            MondayGroup(
                id=g.get("id", ""),
                title=g.get("title", ""),
                board_id=board_id,
                color=g.get("color", ""),
                archived=g.get("archived", False),
                deleted=g.get("deleted", False),
                items_count=g.get("items_count", 0),
                position=g.get("position", ""),
                extra=g,
            )
            for g in groups_data
        ]

    def delete_group(self, board_id: str, group_id: str) -> bool:
        mutation = """
        mutation DeleteGroup($boardId: ID!, $groupId: String!) {
          delete_group(board_id: $boardId, group_id: $groupId) {
            id
            deleted
          }
        }
        """
        result = self._graphql(mutation, {"boardId": board_id, "groupId": group_id})
        data = result.get("data", {}).get("delete_group", {})
        return data.get("deleted", False)

    # ------------------------------------------------------------------
    # Workspaces
    # ------------------------------------------------------------------

    def list_workspaces(self) -> List[MondayWorkspace]:
        query = """
        query {
          workspaces {
            id
            name
            kind
            description
          }
        }
        """
        result = self._graphql(query)
        workspaces_data = result.get("data", {}).get("workspaces", [])
        return [
            MondayWorkspace(
                id=str(w.get("id", "")),
                name=w.get("name", ""),
                kind=w.get("kind", "open"),
                description=w.get("description", ""),
                extra=w,
            )
            for w in workspaces_data
        ]

    # ------------------------------------------------------------------
    # Users
    # ------------------------------------------------------------------

    def list_users(self, limit: int = 25,
                   page: int = 1,
                   **kwargs: Any) -> Dict[str, Any]:
        query = """
        query Users($limit: Int, $page: Int) {
          users(limit: $limit, page: $page) {
            id
            name
            email
            title
            url
            enabled
            created_at
            photo_original
            time_zone_identifier
          }
        }
        """
        variables: Dict[str, Any] = {"limit": limit, "page": page}
        variables.update(kwargs)

        result = self._graphql(query, variables)
        users_data = result.get("data", {}).get("users", [])
        users = [
            MondayUser(
                id=str(u.get("id", "")),
                name=u.get("name", ""),
                email=u.get("email", ""),
                title=u.get("title", ""),
                url=u.get("url", ""),
                enabled=u.get("enabled", True),
                created_at=u.get("created_at", ""),
                photo_original=u.get("photo_original", ""),
                time_zone_identifier=u.get("time_zone_identifier", ""),
                extra=u,
            )
            for u in users_data
        ]
        return {"users": users, "page": page, "limit": limit}

    def get_user(self, user_id: str) -> MondayUser:
        query = """
        query User($id: [ID!]!) {
          users(ids: $id) {
            id
            name
            email
            title
            url
            enabled
            created_at
            photo_original
            time_zone_identifier
            teams {
              id
              name
            }
          }
        }
        """
        result = self._graphql(query, {"id": user_id})
        users = result.get("data", {}).get("users", [])
        if not users:
            raise self._handle_error(f"User {user_id} not found")
        u = users[0]
        return MondayUser(
            id=str(u.get("id", "")),
            name=u.get("name", ""),
            email=u.get("email", ""),
            title=u.get("title", ""),
            url=u.get("url", ""),
            enabled=u.get("enabled", True),
            created_at=u.get("created_at", ""),
            photo_original=u.get("photo_original", ""),
            time_zone_identifier=u.get("time_zone_identifier", ""),
            teams=u.get("teams", []),
            extra=u,
        )

    # ------------------------------------------------------------------
    # Updates (comments)
    # ------------------------------------------------------------------

    def create_update(self, item_id: str,
                      body: str,
                      **kwargs: Any) -> MondayUpdate:
        mutation = """
        mutation CreateUpdate($itemId: ID!, $body: String!) {
          create_update(item_id: $itemId, body: $body) {
            id
            body
            text_body
            creator { id name }
            created_at
            updated_at
            replies_count
            likes_count
          }
        }
        """
        result = self._graphql(mutation, {"itemId": item_id, "body": body})
        update_data = result.get("data", {}).get("create_update", {})
        creator = update_data.get("creator", {})
        return MondayUpdate(
            id=update_data.get("id", ""),
            body=update_data.get("body", ""),
            item_id=item_id,
            creator_id=str(creator.get("id", "")) if creator else "",
            creator_name=creator.get("name", "") if creator else "",
            created_at=update_data.get("created_at", ""),
            updated_at=update_data.get("updated_at", ""),
            replies_count=update_data.get("replies_count", 0),
            likes_count=update_data.get("likes_count", 0),
            text_body=update_data.get("text_body", ""),
            extra=update_data,
        )

    def list_updates(self, item_id: str,
                     limit: int = 25,
                     **kwargs: Any) -> List[MondayUpdate]:
        query = """
        query ItemUpdates($itemId: [ID!]!, $limit: Int) {
          items(ids: $itemId) {
            updates(limit: $limit) {
              id
              body
              text_body
              creator { id name }
              created_at
              updated_at
              replies_count
              likes_count
            }
          }
        }
        """
        result = self._graphql(query, {"itemId": item_id, "limit": limit})
        items = result.get("data", {}).get("items", [])
        if not items:
            return []
        updates_data = items[0].get("updates", [])
        return [
            MondayUpdate(
                id=u.get("id", ""),
                body=u.get("body", ""),
                item_id=item_id,
                creator_id=str(u.get("creator", {}).get("id", "")) if u.get("creator") else "",
                creator_name=u.get("creator", {}).get("name", "") if u.get("creator") else "",
                created_at=u.get("created_at", ""),
                updated_at=u.get("updated_at", ""),
                replies_count=u.get("replies_count", 0),
                likes_count=u.get("likes_count", 0),
                text_body=u.get("text_body", ""),
                extra=u,
            )
            for u in updates_data
        ]

    # ------------------------------------------------------------------
    # Board views
    # ------------------------------------------------------------------

    def list_board_views(self, board_id: str) -> List[MondayBoardView]:
        query = """
        query BoardViews($boardId: [ID!]!) {
          boards(ids: $boardId) {
            views {
              id
              name
              type
              settings_str
            }
          }
        }
        """
        result = self._graphql(query, {"boardId": board_id})
        boards = result.get("data", {}).get("boards", [])
        if not boards:
            return []
        views_data = boards[0].get("views", [])
        return [
            MondayBoardView(
                id=v.get("id", ""),
                name=v.get("name", ""),
                type=v.get("type", ""),
                board_id=board_id,
                settings_str=v.get("settings_str", ""),
                extra=v,
            )
            for v in views_data
        ]

    # ------------------------------------------------------------------
    # Internal GraphQL helper
    # ------------------------------------------------------------------

    def _graphql(self, query: str,
                 variables: Optional[Dict[str, Any]] = None
                 ) -> Dict[str, Any]:
        self._check_rate_limit()
        self._mark_request()

        url = self.config.base_url
        headers = {
            "Authorization": self.config.api_key or self.config.access_token or "",
            "Content-Type": "application/json",
            "API-Version": "2024-04",
        }

        payload = {"query": query, "variables": variables or {}}
        body = json.dumps(payload)
        self._log_request("POST", url, headers, body[:500])

        try:
            import urllib.request
            import urllib.error

            req = urllib.request.Request(
                url,
                data=body.encode("utf-8"),
                headers=headers,
                method="POST",
            )

            with urllib.request.urlopen(
                req, timeout=self.config.timeout
            ) as response:
                response_body = response.read().decode("utf-8")
                self._log_response("POST", url, response.status, response_body[:500])
                result = json.loads(response_body)

                if "errors" in result:
                    error_msgs = [
                        e.get("message", "Unknown error")
                        for e in result["errors"]
                    ]
                    raise self._handle_error(
                        f"GraphQL error: {'; '.join(error_msgs)}",
                        status_code=400,
                        response_body=response_body,
                    )

                return result

        except urllib.error.HTTPError as e:
            response_body = e.read().decode("utf-8", errors="replace")
            self._log_response("POST", url, e.code, response_body)
            if e.code == 429:
                self._rate_limit_hits += 1
                raise RateLimitError(
                    "Rate limited by Monday.com",
                    retry_after=60,
                    adapter_name=self.config.name,
                )
            if e.code == 401:
                raise self._handle_error(
                    "Authentication failed: invalid Monday.com API key",
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
            f"<MondayAdapter name={self.config.name} "
            f"status={self._status.value} "
            f"user={self._user_name or 'unknown'}>"
        )
