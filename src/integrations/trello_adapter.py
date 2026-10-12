"""
Trello Integration Adapter for LiuHao AI OS.

Provides integration with Trello for boards, lists, cards,
card CRUD, checklists, labels, members, actions/activity, and webhooks.
"""

from __future__ import annotations

import hashlib
import hmac
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


class CardStatus(str, Enum):
    """Card status values."""
    OPEN = "open"
    CLOSED = "closed"
    ALL = "all"


class BoardFilter(str, Enum):
    """Board filter options."""
    ALL = "all"
    MEMBERS = "members"
    ORGS = "orgs"
    PUBLIC = "public"
    STARRED = "starred"
    CLOSED = "closed"
    OPEN = "open"


class ActionType(str, Enum):
    """Common Trello action types."""
    CREATE_CARD = "createCard"
    UPDATE_CARD = "updateCard"
    DELETE_CARD = "deleteCard"
    MOVE_CARD_TO_BOARD = "moveCardToBoard"
    MOVE_CARD_FROM_BOARD = "moveCardFromBoard"
    COMMENT_CARD = "commentCard"
    ADD_MEMBER_TO_CARD = "addMemberToCard"
    REMOVE_MEMBER_FROM_CARD = "removeMemberFromCard"
    ADD_ATTACHMENT_TO_CARD = "addAttachmentToCard"
    ADD_CHECKLIST_TO_CARD = "addChecklistToCard"
    UPDATE_CHECK_ITEM = "updateCheckItemStateOnCard"
    CREATE_LIST = "createList"
    UPDATE_LIST = "updateList"
    CREATE_BOARD = "createBoard"
    UPDATE_BOARD = "updateBoard"


@dataclass
class TrelloBoard:
    """Represents a Trello board."""
    id: str
    name: str
    description: str = ""
    url: str = ""
    short_url: str = ""
    closed: bool = False
    starred: bool = False
    id_organization: str = ""
    prefs: Dict[str, Any] = field(default_factory=dict)
    label_names: Dict[str, str] = field(default_factory=dict)
    extra: Dict[str, Any] = field(default_factory=dict)


@dataclass
class TrelloList:
    """Represents a Trello list (column on a board)."""
    id: str
    name: str
    id_board: str = ""
    pos: float = 0.0
    closed: bool = False
    subscribed: bool = False
    extra: Dict[str, Any] = field(default_factory=dict)


@dataclass
class TrelloCard:
    """Represents a Trello card."""
    id: str
    name: str
    description: str = ""
    id_board: str = ""
    id_list: str = ""
    url: str = ""
    short_url: str = ""
    short_link: str = ""
    pos: float = 0.0
    closed: bool = False
    due: str = ""
    due_complete: bool = False
    id_labels: List[str] = field(default_factory=list)
    id_members: List[str] = field(default_factory=list)
    id_checklists: List[str] = field(default_factory=list)
    labels: List[Dict[str, Any]] = field(default_factory=list)
    date_last_activity: str = ""
    date_last_viewed: str = ""
    created_at: str = ""
    extra: Dict[str, Any] = field(default_factory=dict)


@dataclass
class TrelloChecklist:
    """Represents a checklist on a card."""
    id: str
    name: str
    id_card: str = ""
    id_board: str = ""
    pos: int = 0
    check_items: List[Dict[str, Any]] = field(default_factory=list)
    extra: Dict[str, Any] = field(default_factory=dict)


@dataclass
class TrelloCheckItem:
    """Represents a single checklist item."""
    id: str
    name: str
    state: str = "incomplete"
    id_checklist: str = ""
    pos: int = 0
    due: str = ""
    id_member: str = ""
    extra: Dict[str, Any] = field(default_factory=dict)


@dataclass
class TrelloLabel:
    """Represents a Trello label."""
    id: str
    name: str
    color: str = ""
    id_board: str = ""
    uses: int = 0
    extra: Dict[str, Any] = field(default_factory=dict)


@dataclass
class TrelloMember:
    """Represents a Trello member (user)."""
    id: str
    username: str
    full_name: str = ""
    initials: str = ""
    avatar_url: str = ""
    bio: str = ""
    url: str = ""
    member_type: str = ""
    extra: Dict[str, Any] = field(default_factory=dict)


@dataclass
class TrelloAction:
    """Represents a Trello action/activity entry."""
    id: str
    type: str
    data: Dict[str, Any] = field(default_factory=dict)
    id_member_creator: str = ""
    member_creator: Dict[str, Any] = field(default_factory=dict)
    date: str = ""
    date_iso: str = ""
    extra: Dict[str, Any] = field(default_factory=dict)


@dataclass
class TrelloWebhook:
    """Represents a Trello webhook."""
    id: str
    description: str
    callback_url: str
    id_model: str = ""
    active: bool = True
    consecutive_failures: int = 0
    first_consecutive_fail_date: str = ""
    extra: Dict[str, Any] = field(default_factory=dict)


class TrelloAdapter(BaseIntegrationAdapter):
    """
    Trello integration adapter for LiuHao AI OS.

    Supports Trello REST API v1 with:
    - Boards, lists, and cards management
    - Card CRUD operations
    - Checklists and checklist items
    - Labels (create, update, delete, assign)
    - Members (users) lookup and management
    - Actions and activity history
    - Webhook registration and verification
    """

    def __init__(self, config: IntegrationConfig):
        """
        Initialize Trello adapter.

        Uses API key + token authentication.

        Args:
            config: Integration config.
                    api_key: Trello API key
                    access_token: Trello token
                    base_url: https://api.trello.com/1/
        """
        if config.auth_type == AuthType.NONE:
            config.auth_type = AuthType.API_KEY
        if not config.base_url:
            config.base_url = "https://api.trello.com/1/"
        super().__init__(config)
        self._member_id: Optional[str] = None
        self._username: Optional[str] = None
        self._webhooks: Dict[str, str] = {}

    def _get_auth_params(self) -> Dict[str, str]:
        """Get auth query parameters for Trello API."""
        params: Dict[str, str] = {}
        if self.config.api_key:
            params["key"] = self.config.api_key
        if self.config.access_token:
            params["token"] = self.config.access_token
        return params

    # ------------------------------------------------------------------
    # Base interface
    # ------------------------------------------------------------------

    def connect(self) -> bool:
        """Connect to Trello and validate credentials."""
        self._status = ConnectionStatus.CONNECTING
        self._logger.info("Connecting to Trello API...")

        try:
            member_data = self._api_call("GET", "members/me")
            self._member_id = member_data.get("id")
            self._username = member_data.get("username")
            self._status = ConnectionStatus.CONNECTED
            self._connected_at = time.time()
            self._logger.info(
                "Connected to Trello as %s",
                member_data.get("fullName", member_data.get("username", "")),
            )
            return True
        except IntegrationError:
            raise
        except Exception as e:
            self._status = ConnectionStatus.ERROR
            raise self._handle_error(f"Connection error: {e}")

    def disconnect(self) -> bool:
        """Disconnect from Trello."""
        self._status = ConnectionStatus.DISCONNECTED
        self._member_id = None
        self._username = None
        self._session = None
        self._logger.info("Disconnected from Trello")
        return True

    def is_connected(self) -> bool:
        return self._status == ConnectionStatus.CONNECTED

    def get_status(self) -> IntegrationStatus:
        return self._build_status({
            "member_id": self._member_id,
            "username": self._username,
        })

    def test_connection(self) -> bool:
        try:
            if not self.is_connected():
                return False
            result = self._api_call("GET", "members/me")
            return "id" in result
        except Exception:
            return False

    # ------------------------------------------------------------------
    # Boards
    # ------------------------------------------------------------------

    def list_boards(self,
                    filter_: BoardFilter = BoardFilter.ALL,
                    fields: Optional[List[str]] = None
                    ) -> List[TrelloBoard]:
        """
        List boards for the authenticated user.

        Args:
            filter_: Filter boards by status.
            fields: List of fields to return.

        Returns:
            List of TrelloBoard objects.
        """
        params: Dict[str, Any] = {"filter": filter_.value}
        if fields:
            params["fields"] = ",".join(fields)

        data = self._api_call("GET", "members/me/boards", params=params)
        return [self._parse_board(b) for b in data]

    def get_board(self, board_id: str,
                  fields: Optional[List[str]] = None) -> TrelloBoard:
        """Get a board by ID."""
        params: Dict[str, Any] = {}
        if fields:
            params["fields"] = ",".join(fields)

        data = self._api_call("GET", f"boards/{board_id}", params=params)
        return self._parse_board(data)

    def create_board(self, name: str,
                     description: str = "",
                     default_lists: bool = True,
                     id_organization: str = "",
                     id_board_source: str = "",
                     permission_level: str = "private",
                     **kwargs: Any) -> TrelloBoard:
        """Create a new board."""
        params: Dict[str, Any] = {
            "name": name,
            "desc": description,
            "defaultLists": str(default_lists).lower(),
            "prefs_permissionLevel": permission_level,
        }
        if id_organization:
            params["idOrganization"] = id_organization
        if id_board_source:
            params["idBoardSource"] = id_board_source
        if kwargs:
            params.update(kwargs)

        data = self._api_call("POST", "boards", params=params)
        return self._parse_board(data)

    def update_board(self, board_id: str, **kwargs: Any) -> TrelloBoard:
        """Update a board."""
        data = self._api_call("PUT", f"boards/{board_id}", params=kwargs)
        return self._parse_board(data)

    def close_board(self, board_id: str) -> TrelloBoard:
        """Close (archive) a board."""
        return self.update_board(board_id, closed="true")

    def reopen_board(self, board_id: str) -> TrelloBoard:
        """Reopen a closed board."""
        return self.update_board(board_id, closed="false")

    def delete_board(self, board_id: str) -> bool:
        """Delete a board. Returns True on success."""
        try:
            self._api_call("DELETE", f"boards/{board_id}")
            return True
        except Exception:
            return False

    # ------------------------------------------------------------------
    # Lists
    # ------------------------------------------------------------------

    def list_lists(self, board_id: str,
                   cards: str = "none",
                   filter_: str = "open") -> List[TrelloList]:
        """
        Get all lists on a board.

        Args:
            board_id: Board ID.
            cards: "none", "open", "closed", "all".
            filter_: "all", "closed", "open".

        Returns:
            List of TrelloList objects.
        """
        params = {"cards": cards, "filter": filter_}
        data = self._api_call(
            "GET", f"boards/{board_id}/lists", params=params,
        )
        return [self._parse_list(l) for l in data]

    def get_list(self, list_id: str) -> TrelloList:
        """Get a list by ID."""
        data = self._api_call("GET", f"lists/{list_id}")
        return self._parse_list(data)

    def create_list(self, board_id: str, name: str,
                    pos: str = "bottom") -> TrelloList:
        """Create a new list on a board."""
        params = {"name": name, "idBoard": board_id, "pos": pos}
        data = self._api_call("POST", "lists", params=params)
        return self._parse_list(data)

    def update_list(self, list_id: str, **kwargs: Any) -> TrelloList:
        """Update a list."""
        data = self._api_call("PUT", f"lists/{list_id}", params=kwargs)
        return self._parse_list(data)

    def archive_list(self, list_id: str) -> TrelloList:
        """Archive a list."""
        return self.update_list(list_id, closed="true")

    def move_list_to_board(self, list_id: str,
                           board_id: str) -> TrelloList:
        """Move a list to a different board."""
        data = self._api_call(
            "PUT", f"lists/{list_id}/idBoard",
            params={"value": board_id},
        )
        return self._parse_list(data)

    # ------------------------------------------------------------------
    # Cards
    # ------------------------------------------------------------------

    def list_cards(self, board_id: Optional[str] = None,
                   list_id: Optional[str] = None,
                   filter_: CardStatus = CardStatus.OPEN,
                   fields: Optional[List[str]] = None,
                   limit: int = 50
                   ) -> List[TrelloCard]:
        """
        List cards on a board or in a list.

        Args:
            board_id: Board ID (mutually exclusive with list_id).
            list_id: List ID (mutually exclusive with board_id).
            filter_: Card status filter.
            fields: Fields to return.
            limit: Max number of cards.

        Returns:
            List of TrelloCard objects.
        """
        params: Dict[str, Any] = {"filter": filter_.value, "limit": limit}
        if fields:
            params["fields"] = ",".join(fields)

        if list_id:
            data = self._api_call(
                "GET", f"lists/{list_id}/cards", params=params,
            )
        elif board_id:
            data = self._api_call(
                "GET", f"boards/{board_id}/cards", params=params,
            )
        else:
            raise ValueError("Either board_id or list_id must be provided")

        return [self._parse_card(c) for c in data]

    def get_card(self, card_id: str,
                 fields: Optional[List[str]] = None) -> TrelloCard:
        """Get a card by ID."""
        params: Dict[str, Any] = {}
        if fields:
            params["fields"] = ",".join(fields)

        data = self._api_call("GET", f"cards/{card_id}", params=params)
        return self._parse_card(data)

    def create_card(self, list_id: str,
                    name: str,
                    description: str = "",
                    pos: str = "bottom",
                    due: Optional[str] = None,
                    id_members: Optional[List[str]] = None,
                    id_labels: Optional[List[str]] = None,
                    id_card_source: str = "",
                    url_source: str = "",
                    **kwargs: Any) -> TrelloCard:
        """
        Create a new card.

        Args:
            list_id: List ID to create the card in.
            name: Card name/title.
            description: Card description.
            pos: Position ("top", "bottom", or numeric).
            due: Due date (ISO 8601).
            id_members: List of member IDs to assign.
            id_labels: List of label IDs to add.
            id_card_source: Card ID to copy from.
            url_source: URL attachment source.

        Returns:
            Created TrelloCard.
        """
        params: Dict[str, Any] = {
            "idList": list_id,
            "name": name,
            "desc": description,
            "pos": pos,
        }
        if due:
            params["due"] = due
        if id_members:
            params["idMembers"] = ",".join(id_members)
        if id_labels:
            params["idLabels"] = ",".join(id_labels)
        if id_card_source:
            params["idCardSource"] = id_card_source
        if url_source:
            params["urlSource"] = url_source
        if kwargs:
            params.update(kwargs)

        data = self._api_call("POST", "cards", params=params)
        return self._parse_card(data)

    def update_card(self, card_id: str, **kwargs: Any) -> TrelloCard:
        """Update a card."""
        data = self._api_call("PUT", f"cards/{card_id}", params=kwargs)
        return self._parse_card(data)

    def move_card(self, card_id: str,
                   list_id: Optional[str] = None,
                   board_id: Optional[str] = None,
                   pos: Optional[str] = None) -> TrelloCard:
        """Move a card to another list/board."""
        params: Dict[str, Any] = {}
        if list_id:
            params["idList"] = list_id
        if board_id:
            params["idBoard"] = board_id
        if pos:
            params["pos"] = pos
        return self.update_card(card_id, **params)

    def archive_card(self, card_id: str) -> TrelloCard:
        """Archive a card."""
        return self.update_card(card_id, closed="true")

    def unarchive_card(self, card_id: str) -> TrelloCard:
        """Unarchive a card."""
        return self.update_card(card_id, closed="false")

    def delete_card(self, card_id: str) -> bool:
        """Delete a card. Returns True on success."""
        try:
            self._api_call("DELETE", f"cards/{card_id}")
            return True
        except Exception:
            return False

    def add_card_comment(self, card_id: str, text: str) -> Dict[str, Any]:
        """Add a comment to a card."""
        return self._api_call(
            "POST", f"cards/{card_id}/actions/comments",
            params={"text": text},
        )

    def add_card_attachment(self, card_id: str,
                            url: str = "",
                            name: str = "",
                            file_content: Optional[bytes] = None,
                            mime_type: str = "") -> Dict[str, Any]:
        """Add an attachment to a card."""
        params: Dict[str, Any] = {}
        if url:
            params["url"] = url
        if name:
            params["name"] = name

        # Note: file uploads would need multipart handling
        return self._api_call(
            "POST", f"cards/{card_id}/attachments",
            params=params if params else None,
        )

    def list_card_attachments(self, card_id: str) -> List[Dict[str, Any]]:
        """List attachments on a card."""
        return self._api_call("GET", f"cards/{card_id}/attachments")

    # ------------------------------------------------------------------
    # Checklists
    # ------------------------------------------------------------------

    def list_checklists(self, card_id: str) -> List[TrelloChecklist]:
        """List checklists on a card."""
        data = self._api_call("GET", f"cards/{card_id}/checklists")
        return [self._parse_checklist(cl) for cl in data]

    def get_checklist(self, checklist_id: str) -> TrelloChecklist:
        """Get a checklist by ID."""
        data = self._api_call("GET", f"checklists/{checklist_id}")
        return self._parse_checklist(data)

    def create_checklist(self, card_id: str, name: str,
                         id_checklist_source: str = "",
                         pos: str = "bottom") -> TrelloChecklist:
        """Create a new checklist on a card."""
        params: Dict[str, Any] = {
            "idCard": card_id,
            "name": name,
            "pos": pos,
        }
        if id_checklist_source:
            params["idChecklistSource"] = id_checklist_source

        data = self._api_call("POST", "checklists", params=params)
        return self._parse_checklist(data)

    def update_checklist(self, checklist_id: str,
                         **kwargs: Any) -> TrelloChecklist:
        """Update a checklist."""
        data = self._api_call(
            "PUT", f"checklists/{checklist_id}", params=kwargs,
        )
        return self._parse_checklist(data)

    def delete_checklist(self, checklist_id: str) -> bool:
        """Delete a checklist. Returns True on success."""
        try:
            self._api_call("DELETE", f"checklists/{checklist_id}")
            return True
        except Exception:
            return False

    def add_check_item(self, checklist_id: str, name: str,
                       pos: str = "bottom",
                       checked: bool = False,
                       due: Optional[str] = None) -> Dict[str, Any]:
        """Add an item to a checklist."""
        params: Dict[str, Any] = {
            "name": name,
            "pos": pos,
            "checked": str(checked).lower(),
        }
        if due:
            params["due"] = due

        return self._api_call(
            "POST", f"checklists/{checklist_id}/checkItems",
            params=params,
        )

    def update_check_item(self, card_id: str,
                          check_item_id: str,
                          **kwargs: Any) -> Dict[str, Any]:
        """Update a checklist item on a card."""
        return self._api_call(
            "PUT",
            f"cards/{card_id}/checkItem/{check_item_id}",
            params=kwargs,
        )

    def set_check_item_state(self, card_id: str,
                             check_item_id: str,
                             complete: bool = True) -> Dict[str, Any]:
        """Set a checklist item as complete or incomplete."""
        state = "complete" if complete else "incomplete"
        return self.update_check_item(card_id, check_item_id, state=state)

    def delete_check_item(self, checklist_id: str,
                          check_item_id: str) -> bool:
        """Delete a checklist item. Returns True on success."""
        try:
            self._api_call(
                "DELETE",
                f"checklists/{checklist_id}/checkItems/{check_item_id}",
            )
            return True
        except Exception:
            return False

    # ------------------------------------------------------------------
    # Labels
    # ------------------------------------------------------------------

    def list_labels(self, board_id: str) -> List[TrelloLabel]:
        """List all labels on a board."""
        data = self._api_call("GET", f"boards/{board_id}/labels")
        return [self._parse_label(l) for l in data]

    def get_label(self, label_id: str) -> TrelloLabel:
        """Get a label by ID."""
        data = self._api_call("GET", f"labels/{label_id}")
        return self._parse_label(data)

    def create_label(self, board_id: str, name: str,
                     color: str = "") -> TrelloLabel:
        """Create a new label on a board."""
        params = {"idBoard": board_id, "name": name, "color": color}
        data = self._api_call("POST", "labels", params=params)
        return self._parse_label(data)

    def update_label(self, label_id: str, **kwargs: Any) -> TrelloLabel:
        """Update a label."""
        data = self._api_call("PUT", f"labels/{label_id}", params=kwargs)
        return self._parse_label(data)

    def delete_label(self, label_id: str) -> bool:
        """Delete a label. Returns True on success."""
        try:
            self._api_call("DELETE", f"labels/{label_id}")
            return True
        except Exception:
            return False

    def add_label_to_card(self, card_id: str, label_id: str) -> bool:
        """Add a label to a card. Returns True on success."""
        try:
            self._api_call(
                "POST", f"cards/{card_id}/idLabels",
                params={"value": label_id},
            )
            return True
        except Exception:
            return False

    def remove_label_from_card(self, card_id: str, label_id: str) -> bool:
        """Remove a label from a card. Returns True on success."""
        try:
            self._api_call(
                "DELETE", f"cards/{card_id}/idLabels/{label_id}",
            )
            return True
        except Exception:
            return False

    # ------------------------------------------------------------------
    # Members
    # ------------------------------------------------------------------

    def get_member(self, member_id: str,
                   fields: Optional[List[str]] = None) -> TrelloMember:
        """Get a member by ID or username."""
        params: Dict[str, Any] = {}
        if fields:
            params["fields"] = ",".join(fields)

        data = self._api_call("GET", f"members/{member_id}", params=params)
        return self._parse_member(data)

    def get_current_member(self) -> TrelloMember:
        """Get the authenticated member."""
        data = self._api_call("GET", "members/me")
        return self._parse_member(data)

    def list_board_members(self, board_id: str) -> List[TrelloMember]:
        """List members of a board."""
        data = self._api_call("GET", f"boards/{board_id}/members")
        return [self._parse_member(m) for m in data]

    def list_card_members(self, card_id: str) -> List[TrelloMember]:
        """List members assigned to a card."""
        data = self._api_call("GET", f"cards/{card_id}/members")
        return [self._parse_member(m) for m in data]

    def add_member_to_board(self, board_id: str,
                            member_id: str,
                            type_: str = "normal") -> Dict[str, Any]:
        """Add a member to a board."""
        return self._api_call(
            "PUT", f"boards/{board_id}/members/{member_id}",
            params={"type": type_},
        )

    def remove_member_from_board(self, board_id: str,
                                 member_id: str) -> bool:
        """Remove a member from a board. Returns True on success."""
        try:
            self._api_call(
                "DELETE", f"boards/{board_id}/members/{member_id}",
            )
            return True
        except Exception:
            return False

    def add_member_to_card(self, card_id: str, member_id: str) -> bool:
        """Add a member to a card. Returns True on success."""
        try:
            self._api_call(
                "POST", f"cards/{card_id}/idMembers",
                params={"value": member_id},
            )
            return True
        except Exception:
            return False

    def remove_member_from_card(self, card_id: str,
                                member_id: str) -> bool:
        """Remove a member from a card. Returns True on success."""
        try:
            self._api_call(
                "DELETE", f"cards/{card_id}/idMembers/{member_id}",
            )
            return True
        except Exception:
            return False

    # ------------------------------------------------------------------
    # Actions / Activity
    # ------------------------------------------------------------------

    def list_actions(self, board_id: Optional[str] = None,
                     card_id: Optional[str] = None,
                     list_id: Optional[str] = None,
                     member_id: Optional[str] = None,
                     filter_types: Optional[List[str]] = None,
                     limit: int = 50,
                     before: Optional[str] = None,
                     since: Optional[str] = None,
                     ) -> List[TrelloAction]:
        """
        List actions (activity history).

        Args:
            board_id: Filter by board.
            card_id: Filter by card.
            list_id: Filter by list.
            member_id: Filter by member.
            filter_types: List of action types to include.
            limit: Max results.
            before: Only actions before this date.
            since: Only actions after this date.

        Returns:
            List of TrelloAction objects.
        """
        params: Dict[str, Any] = {"limit": limit}
        if filter_types:
            params["filter"] = ",".join(filter_types)
        if before:
            params["before"] = before
        if since:
            params["since"] = since

        if card_id:
            data = self._api_call(
                "GET", f"cards/{card_id}/actions", params=params,
            )
        elif list_id:
            data = self._api_call(
                "GET", f"lists/{list_id}/actions", params=params,
            )
        elif board_id:
            data = self._api_call(
                "GET", f"boards/{board_id}/actions", params=params,
            )
        elif member_id:
            data = self._api_call(
                "GET", f"members/{member_id}/actions", params=params,
            )
        else:
            raise ValueError(
                "One of board_id, card_id, list_id, or member_id required"
            )

        return [self._parse_action(a) for a in data]

    def get_action(self, action_id: str) -> TrelloAction:
        """Get a specific action by ID."""
        data = self._api_call("GET", f"actions/{action_id}")
        return self._parse_action(data)

    # ------------------------------------------------------------------
    # Webhooks
    # ------------------------------------------------------------------

    def list_webhooks(self) -> List[TrelloWebhook]:
        """List all webhooks for the token."""
        data = self._api_call("GET", f"tokens/{self.config.access_token}/webhooks")
        return [self._parse_webhook(w) for w in data]

    def create_webhook(self, callback_url: str,
                       id_model: str,
                       description: str = "") -> TrelloWebhook:
        """
        Create a new webhook.

        Args:
            callback_url: URL that Trello will call.
            id_model: ID of the model to watch (board, card, etc.).
            description: Description of the webhook.

        Returns:
            Created TrelloWebhook.
        """
        params = {
            "callbackURL": callback_url,
            "idModel": id_model,
            "description": description,
        }
        data = self._api_call("POST", "webhooks", params=params)
        return self._parse_webhook(data)

    def get_webhook(self, webhook_id: str) -> TrelloWebhook:
        """Get a webhook by ID."""
        data = self._api_call("GET", f"webhooks/{webhook_id}")
        return self._parse_webhook(data)

    def update_webhook(self, webhook_id: str, **kwargs: Any) -> TrelloWebhook:
        """Update a webhook."""
        data = self._api_call("PUT", f"webhooks/{webhook_id}", params=kwargs)
        return self._parse_webhook(data)

    def delete_webhook(self, webhook_id: str) -> bool:
        """Delete a webhook. Returns True on success."""
        try:
            self._api_call("DELETE", f"webhooks/{webhook_id}")
            return True
        except Exception:
            return False

    def verify_webhook_callback(self, callback_url: str) -> bool:
        """
        Verify a webhook callback.

        Trello sends a HEAD request to the callback URL during creation.
        The endpoint must return a 200 response.

        Returns:
            True if verification would succeed (placeholder).
        """
        return True

    def verify_webhook_signature(self, body: str,
                                 signature: str,
                                 callback_url: str) -> bool:
        """
        Verify a webhook request signature.

        Trello generates HMAC-SHA1 of the content + callback URL,
        using the app secret as the key.

        Args:
            body: Raw request body.
            signature: X-Trello-Webhook-Signature header (base64).
            callback_url: The full callback URL.

        Returns:
            True if signature is valid.
        """
        secret = self.config.webhook_secret or self.config.api_key or ""
        if not secret:
            return False

        import base64
        content = body + callback_url
        computed = hmac.new(
            secret.encode("utf-8"),
            content.encode("utf-8"),
            hashlib.sha1,
        ).digest()
        computed_b64 = base64.b64encode(computed).decode("utf-8")
        return hmac.compare_digest(computed_b64, signature)

    # ------------------------------------------------------------------
    # Parsing helpers
    # ------------------------------------------------------------------

    def _parse_board(self, data: Dict[str, Any]) -> TrelloBoard:
        return TrelloBoard(
            id=data.get("id", ""),
            name=data.get("name", ""),
            description=data.get("desc", "") or "",
            url=data.get("url", ""),
            short_url=data.get("shortUrl", ""),
            closed=data.get("closed", False),
            starred=data.get("starred", False),
            id_organization=data.get("idOrganization", "") or "",
            prefs=data.get("prefs", {}) or {},
            label_names=data.get("labelNames", {}) or {},
            extra=data,
        )

    def _parse_list(self, data: Dict[str, Any]) -> TrelloList:
        return TrelloList(
            id=data.get("id", ""),
            name=data.get("name", ""),
            id_board=data.get("idBoard", "") or "",
            pos=float(data.get("pos", 0)),
            closed=data.get("closed", False),
            subscribed=data.get("subscribed", False),
            extra=data,
        )

    def _parse_card(self, data: Dict[str, Any]) -> TrelloCard:
        return TrelloCard(
            id=data.get("id", ""),
            name=data.get("name", ""),
            description=data.get("desc", "") or "",
            id_board=data.get("idBoard", "") or "",
            id_list=data.get("idList", "") or "",
            url=data.get("url", ""),
            short_url=data.get("shortUrl", ""),
            short_link=data.get("shortLink", ""),
            pos=float(data.get("pos", 0)),
            closed=data.get("closed", False),
            due=data.get("due", "") or "",
            due_complete=data.get("dueComplete", False),
            id_labels=data.get("idLabels", []) or [],
            id_members=data.get("idMembers", []) or [],
            id_checklists=data.get("idChecklists", []) or [],
            labels=data.get("labels", []) or [],
            date_last_activity=data.get("dateLastActivity", "") or "",
            date_last_viewed=data.get("dateLastView", "") or "",
            extra=data,
        )

    def _parse_checklist(self, data: Dict[str, Any]) -> TrelloChecklist:
        return TrelloChecklist(
            id=data.get("id", ""),
            name=data.get("name", ""),
            id_card=data.get("idCard", "") or "",
            id_board=data.get("idBoard", "") or "",
            pos=int(data.get("pos", 0)),
            check_items=data.get("checkItems", []) or [],
            extra=data,
        )

    def _parse_label(self, data: Dict[str, Any]) -> TrelloLabel:
        return TrelloLabel(
            id=data.get("id", ""),
            name=data.get("name", ""),
            color=data.get("color", ""),
            id_board=data.get("idBoard", "") or "",
            uses=data.get("uses", 0) or 0,
            extra=data,
        )

    def _parse_member(self, data: Dict[str, Any]) -> TrelloMember:
        return TrelloMember(
            id=data.get("id", ""),
            username=data.get("username", ""),
            full_name=data.get("fullName", "") or "",
            initials=data.get("initials", ""),
            avatar_url=data.get("avatarUrl", "") or "",
            bio=data.get("bio", "") or "",
            url=data.get("url", ""),
            member_type=data.get("memberType", "") or "",
            extra=data,
        )

    def _parse_action(self, data: Dict[str, Any]) -> TrelloAction:
        return TrelloAction(
            id=data.get("id", ""),
            type=data.get("type", ""),
            data=data.get("data", {}) or {},
            id_member_creator=data.get("idMemberCreator", "") or "",
            member_creator=data.get("memberCreator", {}) or {},
            date=data.get("date", ""),
            date_iso=data.get("date", ""),
            extra=data,
        )

    def _parse_webhook(self, data: Dict[str, Any]) -> TrelloWebhook:
        return TrelloWebhook(
            id=data.get("id", ""),
            description=data.get("description", ""),
            callback_url=data.get("callbackURL", ""),
            id_model=data.get("idModel", "") or "",
            active=data.get("active", False),
            consecutive_failures=data.get("consecutiveFailures", 0) or 0,
            first_consecutive_fail_date=(
                data.get("firstConsecutiveFailDate", "") or ""
            ),
            extra=data,
        )

    # ------------------------------------------------------------------
    # Internal API helper
    # ------------------------------------------------------------------

    def _api_call(self, method: str, path: str,
                  params: Optional[Dict[str, Any]] = None,
                  json_body: Optional[Dict[str, Any]] = None,
                  _retry_count: int = 0) -> Any:
        """Make a Trello API call with retry."""
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
        """Execute a single Trello API request."""
        self._check_rate_limit()
        self._mark_request()

        # Merge auth params with request params
        all_params = self._get_auth_params()
        if params:
            all_params.update({k: str(v) if not isinstance(v, str) else v
                               for k, v in params.items()})

        url = self._build_url(path)
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": "LiuHao-AI-OS",
        }

        body_str = json.dumps(json_body) if json_body else ""
        self._log_request(method, url, headers, body_str or all_params)

        try:
            import urllib.request
            from urllib.parse import urlencode

            full_url = f"{url}?{urlencode(all_params)}"

            data = body_str.encode("utf-8") if body_str and method != "GET" else None
            req = urllib.request.Request(
                full_url, data=data, headers=headers, method=method,
            )

            with urllib.request.urlopen(req, timeout=self.config.timeout) as resp:
                resp_body = resp.read().decode("utf-8")
                self._log_response(method, url, resp.status, resp_body)

                if resp.status == 204:
                    return {}
                if not resp_body:
                    return {}
                return json.loads(resp_body)

        except urllib.error.HTTPError as e:
            resp_body = e.read().decode("utf-8", errors="replace")
            self._log_response(method, url, e.code, resp_body)

            if e.code == 429:
                retry_after = float(e.headers.get("Retry-After", 10))
                self._rate_limit_hits += 1
                raise RateLimitError(
                    f"Trello rate limited, retry after {retry_after}s",
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
                f"Trello API error {e.code}: {e.reason}",
                status_code=e.code,
                response_body=resp_body,
            )
        except (RateLimitError, IntegrationError):
            raise
        except Exception as e:
            raise self._handle_error(f"Request failed: {e}")
