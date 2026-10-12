"""
Slack Integration Adapter for LiuHao AI OS.

Provides integration with Slack for sending/receiving messages,
interactive components, file uploads, user lookup, webhook support,
and rate limiting.
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


class SlackMessageType(str, Enum):
    """Types of Slack messages."""
    TEXT = "text"
    BLOCK_KIT = "block_kit"
    ATTACHMENT = "attachment"
    EPHEMERAL = "ephemeral"


class SlackChannelType(str, Enum):
    """Slack channel types."""
    PUBLIC = "public_channel"
    PRIVATE = "private_channel"
    DM = "im"
    GROUP_DM = "mpim"


@dataclass
class SlackMessage:
    """Represents a Slack message."""
    channel: str
    text: str = ""
    blocks: Optional[List[Dict[str, Any]]] = None
    attachments: Optional[List[Dict[str, Any]]] = None
    thread_ts: Optional[str] = None
    user: Optional[str] = None
    ts: Optional[str] = None
    message_type: SlackMessageType = SlackMessageType.TEXT
    extra: Dict[str, Any] = field(default_factory=dict)


@dataclass
class SlackUser:
    """Represents a Slack user."""
    id: str
    name: str
    real_name: str = ""
    email: str = ""
    is_bot: bool = False
    is_admin: bool = False
    avatar_url: str = ""
    team_id: str = ""
    extra: Dict[str, Any] = field(default_factory=dict)


@dataclass
class SlackInteractiveComponent:
    """Represents an interactive component action."""
    action_id: str
    block_id: str
    action_type: str
    value: str = ""
    user_id: str = ""
    channel_id: str = ""
    message_ts: str = ""
    team_id: str = ""
    trigger_id: str = ""
    response_url: str = ""
    raw: Dict[str, Any] = field(default_factory=dict)


class SlackAdapter(BaseIntegrationAdapter):
    """
    Slack integration adapter for LiuHao AI OS.

    Supports:
    - Sending messages to channels (text, blocks, attachments)
    - Receiving messages via webhook or RTM
    - Interactive components (buttons, menus, modals)
    - File uploads
    - User lookup by ID, email, or name
    - Incoming webhook support with signature verification
    - Rate limiting per Slack API guidelines
    """

    def __init__(self, config: IntegrationConfig):
        """
        Initialize the Slack adapter.

        Args:
            config: Integration configuration. Must have either a bot token
                    (access_token) or webhook URL for basic messaging.
        """
        if config.auth_type == AuthType.NONE:
            config.auth_type = AuthType.BEARER_TOKEN
        super().__init__(config)
        self._bot_user_id: Optional[str] = None
        self._team_id: Optional[str] = None
        self._webhook_urls: Dict[str, str] = {}
        self._last_cursor: Dict[str, str] = {}

    # ------------------------------------------------------------------
    # Base interface implementation
    # ------------------------------------------------------------------

    def connect(self) -> bool:
        """
        Establish connection to Slack API and validate credentials.

        Returns:
            True if connection successful.

        Raises:
            AuthenticationError: If token is invalid.
        """
        self._status = ConnectionStatus.CONNECTING
        self._logger.info("Connecting to Slack API...")

        try:
            # Use auth.test to validate token
            result = self._api_call("auth.test")
            if result.get("ok"):
                self._bot_user_id = result.get("user_id")
                self._team_id = result.get("team_id")
                self._status = ConnectionStatus.CONNECTED
                self._connected_at = time.time()
                self._logger.info(
                    "Connected to Slack as %s (team: %s)",
                    result.get("user"), result.get("team"),
                )
                return True
            else:
                error_msg = result.get("error", "Unknown error")
                self._status = ConnectionStatus.ERROR
                raise self._handle_error(
                    f"Slack auth failed: {error_msg}", status_code=401
                )
        except IntegrationError:
            raise
        except Exception as e:
            self._status = ConnectionStatus.ERROR
            raise self._handle_error(f"Connection error: {e}")

    def disconnect(self) -> bool:
        """
        Disconnect from Slack (clear session state).

        Returns:
            Always returns True.
        """
        self._status = ConnectionStatus.DISCONNECTED
        self._bot_user_id = None
        self._team_id = None
        self._session = None
        self._logger.info("Disconnected from Slack")
        return True

    def is_connected(self) -> bool:
        """
        Check if connected to Slack.

        Returns:
            True if status is CONNECTED.
        """
        return self._status == ConnectionStatus.CONNECTED

    def get_status(self) -> IntegrationStatus:
        """
        Get current adapter status.

        Returns:
            IntegrationStatus with Slack-specific extra info.
        """
        return self._build_status({
            "bot_user_id": self._bot_user_id,
            "team_id": self._team_id,
            "webhook_count": len(self._webhook_urls),
        })

    def test_connection(self) -> bool:
        """
        Test the Slack API connection.

        Returns:
            True if connection is working.
        """
        try:
            if not self.is_connected():
                return False
            result = self._api_call("auth.test")
            return result.get("ok", False)
        except Exception:
            return False

    # ------------------------------------------------------------------
    # Message sending
    # ------------------------------------------------------------------

    def send_message(self, message: SlackMessage) -> Dict[str, Any]:
        """
        Send a message to a Slack channel.

        Args:
            message: SlackMessage with channel, text, blocks, etc.

        Returns:
            API response data including message ts.

        Raises:
            IntegrationError: If message send fails.
        """
        payload: Dict[str, Any] = {
            "channel": message.channel,
            "text": message.text,
        }

        if message.blocks:
            payload["blocks"] = message.blocks
        if message.attachments:
            payload["attachments"] = message.attachments
        if message.thread_ts:
            payload["thread_ts"] = message.thread_ts
        if message.extra:
            payload.update(message.extra)

        if message.message_type == SlackMessageType.EPHEMERAL and message.user:
            payload["user"] = message.user
            return self._api_call("chat.postEphemeral", payload)

        return self._api_call("chat.postMessage", payload)

    def send_text(self, channel: str, text: str,
                  thread_ts: Optional[str] = None) -> Dict[str, Any]:
        """
        Send a simple text message.

        Args:
            channel: Channel ID or name.
            text: Message text.
            thread_ts: Optional thread timestamp for replies.

        Returns:
            API response.
        """
        msg = SlackMessage(
            channel=channel,
            text=text,
            thread_ts=thread_ts,
            message_type=SlackMessageType.TEXT,
        )
        return self.send_message(msg)

    def send_blocks(self, channel: str, blocks: List[Dict[str, Any]],
                    fallback_text: str = "",
                    thread_ts: Optional[str] = None) -> Dict[str, Any]:
        """
        Send a Block Kit message.

        Args:
            channel: Channel ID or name.
            blocks: Block Kit blocks array.
            fallback_text: Fallback text for notifications.
            thread_ts: Optional thread timestamp.

        Returns:
            API response.
        """
        msg = SlackMessage(
            channel=channel,
            text=fallback_text,
            blocks=blocks,
            thread_ts=thread_ts,
            message_type=SlackMessageType.BLOCK_KIT,
        )
        return self.send_message(msg)

    def update_message(self, channel: str, ts: str,
                       text: str = "",
                       blocks: Optional[List[Dict[str, Any]]] = None
                       ) -> Dict[str, Any]:
        """
        Update an existing message.

        Args:
            channel: Channel ID.
            ts: Message timestamp.
            text: New text content.
            blocks: New blocks (optional).

        Returns:
            API response.
        """
        payload: Dict[str, Any] = {
            "channel": channel,
            "ts": ts,
            "text": text,
        }
        if blocks:
            payload["blocks"] = blocks
        return self._api_call("chat.update", payload)

    def delete_message(self, channel: str, ts: str) -> Dict[str, Any]:
        """
        Delete a message.

        Args:
            channel: Channel ID.
            ts: Message timestamp.

        Returns:
            API response.
        """
        return self._api_call("chat.delete", {"channel": channel, "ts": ts})

    # ------------------------------------------------------------------
    # Message receiving / webhook handling
    # ------------------------------------------------------------------

    def verify_webhook_signature(self, body: str,
                                 signature: str,
                                 timestamp: str) -> bool:
        """
        Verify a Slack webhook request signature.

        Args:
            body: Raw request body string.
            signature: X-Slack-Signature header value.
            timestamp: X-Slack-Request-Timestamp header value.

        Returns:
            True if signature is valid.
        """
        if not self.config.webhook_secret:
            self._logger.warning("No webhook secret configured")
            return False

        # Prevent replay attacks - reject if older than 5 minutes
        try:
            ts_int = int(timestamp)
            if abs(time.time() - ts_int) > 60 * 5:
                self._logger.warning("Webhook timestamp too old")
                return False
        except (ValueError, TypeError):
            return False

        sig_basestring = f"v0:{timestamp}:{body}"
        computed = "v0=" + hmac.new(
            self.config.webhook_secret.encode("utf-8"),
            sig_basestring.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()

        return hmac.compare_digest(computed, signature)

    def parse_webhook_payload(self, payload: Dict[str, Any]
                              ) -> Optional[SlackMessage]:
        """
        Parse an incoming Slack webhook payload into a SlackMessage.

        Args:
            payload: Parsed JSON payload from Slack.

        Returns:
            SlackMessage if payload contains a message, else None.
        """
        event = payload.get("event", {})
        event_type = event.get("type", "")

        if event_type == "message":
            return SlackMessage(
                channel=event.get("channel", ""),
                text=event.get("text", ""),
                user=event.get("user", ""),
                ts=event.get("ts", ""),
                thread_ts=event.get("thread_ts"),
                message_type=SlackMessageType.TEXT,
                extra={"event": event},
            )
        return None

    def parse_interactive_payload(self, payload: Dict[str, Any]
                                  ) -> List[SlackInteractiveComponent]:
        """
        Parse interactive component actions from a Slack payload.

        Args:
            payload: Parsed payload from interactive action endpoint.

        Returns:
            List of SlackInteractiveComponent actions.
        """
        actions = payload.get("actions", [])
        components: List[SlackInteractiveComponent] = []

        for action in actions:
            components.append(SlackInteractiveComponent(
                action_id=action.get("action_id", ""),
                block_id=action.get("block_id", ""),
                action_type=action.get("type", ""),
                value=action.get("value", ""),
                user_id=payload.get("user", {}).get("id", ""),
                channel_id=payload.get("channel", {}).get("id", ""),
                message_ts=payload.get("message", {}).get("ts", ""),
                team_id=payload.get("team", {}).get("id", ""),
                trigger_id=payload.get("trigger_id", ""),
                response_url=payload.get("response_url", ""),
                raw=payload,
            ))

        return components

    # ------------------------------------------------------------------
    # File uploads
    # ------------------------------------------------------------------

    def upload_file(self, channels: List[str],
                    file_content: bytes,
                    filename: str,
                    title: str = "",
                    initial_comment: str = "",
                    filetype: str = "auto") -> Dict[str, Any]:
        """
        Upload a file to Slack.

        Args:
            channels: List of channel IDs to share the file to.
            file_content: Raw file bytes.
            filename: Name of the file.
            title: File title.
            initial_comment: Comment to post with the file.
            filetype: File type hint (e.g., "python", "pdf", "png").

        Returns:
            API response with file info.
        """
        payload: Dict[str, Any] = {
            "channels": ",".join(channels),
            "filename": filename,
            "title": title,
            "initial_comment": initial_comment,
            "filetype": filetype,
        }
        # In a real implementation, this would use multipart/form-data
        return self._api_call("files.upload", payload, method="POST")

    def get_file_info(self, file_id: str) -> Dict[str, Any]:
        """Get information about an uploaded file."""
        return self._api_call("files.info", {"file": file_id})

    # ------------------------------------------------------------------
    # User lookup
    # ------------------------------------------------------------------

    def get_user_info(self, user_id: str) -> SlackUser:
        """
        Look up a user by ID.

        Args:
            user_id: Slack user ID (e.g., U123456).

        Returns:
            SlackUser object.

        Raises:
            IntegrationError: If user not found.
        """
        result = self._api_call("users.info", {"user": user_id})
        user_data = result.get("user", {})
        if not user_data:
            raise self._handle_error(f"User not found: {user_id}")

        profile = user_data.get("profile", {})
        return SlackUser(
            id=user_data.get("id", user_id),
            name=user_data.get("name", ""),
            real_name=profile.get("real_name", ""),
            email=profile.get("email", ""),
            is_bot=user_data.get("is_bot", False),
            is_admin=user_data.get("is_admin", False),
            avatar_url=profile.get("image_512", ""),
            team_id=user_data.get("team_id", ""),
            extra=user_data,
        )

    def lookup_user_by_email(self, email: str) -> Optional[SlackUser]:
        """
        Look up a user by email address.

        Args:
            email: Email address to search for.

        Returns:
            SlackUser if found, None otherwise.
        """
        try:
            result = self._api_call("users.lookupByEmail", {"email": email})
            user_data = result.get("user", {})
            if user_data:
                profile = user_data.get("profile", {})
                return SlackUser(
                    id=user_data.get("id", ""),
                    name=user_data.get("name", ""),
                    real_name=profile.get("real_name", ""),
                    email=profile.get("email", email),
                    is_bot=user_data.get("is_bot", False),
                    is_admin=user_data.get("is_admin", False),
                    avatar_url=profile.get("image_512", ""),
                    team_id=user_data.get("team_id", ""),
                    extra=user_data,
                )
        except IntegrationError as e:
            if "users_not_found" in str(e):
                return None
            raise
        return None

    def list_users(self, limit: int = 100,
                   cursor: Optional[str] = None) -> Tuple[List[SlackUser], Optional[str]]:
        """
        List users in the workspace.

        Args:
            limit: Maximum number of users to return.
            cursor: Pagination cursor.

        Returns:
            Tuple of (list of SlackUser, next_cursor or None).
        """
        params: Dict[str, Any] = {"limit": limit}
        if cursor:
            params["cursor"] = cursor

        result = self._api_call("users.list", params)
        members = result.get("members", [])
        users: List[SlackUser] = []

        for member in members:
            profile = member.get("profile", {})
            users.append(SlackUser(
                id=member.get("id", ""),
                name=member.get("name", ""),
                real_name=profile.get("real_name", ""),
                email=profile.get("email", ""),
                is_bot=member.get("is_bot", False),
                is_admin=member.get("is_admin", False),
                avatar_url=profile.get("image_512", ""),
                team_id=member.get("team_id", ""),
                extra=member,
            ))

        next_cursor = result.get("response_metadata", {}).get("next_cursor") or None
        return users, next_cursor

    # ------------------------------------------------------------------
    # Channel operations
    # ------------------------------------------------------------------

    def list_channels(self, channel_type: SlackChannelType = SlackChannelType.PUBLIC,
                      limit: int = 100,
                      cursor: Optional[str] = None
                      ) -> Tuple[List[Dict[str, Any]], Optional[str]]:
        """
        List channels of a given type.

        Args:
            channel_type: Type of channels to list.
            limit: Max results per page.
            cursor: Pagination cursor.

        Returns:
            Tuple of (channels list, next_cursor).
        """
        params: Dict[str, Any] = {
            "limit": limit,
            "types": channel_type.value,
        }
        if cursor:
            params["cursor"] = cursor

        result = self._api_call("conversations.list", params)
        channels = result.get("channels", [])
        next_cursor = result.get("response_metadata", {}).get("next_cursor") or None
        return channels, next_cursor

    def get_channel_history(self, channel: str, limit: int = 100,
                            oldest: Optional[str] = None,
                            latest: Optional[str] = None
                            ) -> Tuple[List[Dict[str, Any]], Optional[str]]:
        """
        Get message history for a channel.

        Args:
            channel: Channel ID.
            limit: Number of messages to retrieve.
            oldest: Start of time range (ts).
            latest: End of time range (ts).

        Returns:
            Tuple of (messages list, next_cursor).
        """
        params: Dict[str, Any] = {"channel": channel, "limit": limit}
        if oldest:
            params["oldest"] = oldest
        if latest:
            params["latest"] = latest

        result = self._api_call("conversations.history", params)
        messages = result.get("messages", [])
        next_cursor = result.get("response_metadata", {}).get("next_cursor") or None
        return messages, next_cursor

    # ------------------------------------------------------------------
    # Webhook support (incoming webhooks)
    # ------------------------------------------------------------------

    def register_webhook(self, name: str, url: str) -> None:
        """Register a named incoming webhook URL."""
        self._webhook_urls[name] = url

    def send_via_webhook(self, webhook_name: str,
                         text: str = "",
                         blocks: Optional[List[Dict[str, Any]]] = None,
                         attachments: Optional[List[Dict[str, Any]]] = None
                         ) -> bool:
        """
        Send a message using an incoming webhook (no bot token needed).

        Args:
            webhook_name: Name of the registered webhook.
            text: Message text.
            blocks: Optional Block Kit blocks.
            attachments: Optional legacy attachments.

        Returns:
            True if send succeeded.
        """
        if webhook_name not in self._webhook_urls:
            raise self._handle_error(
                f"Unknown webhook: {webhook_name}. "
                f"Available: {list(self._webhook_urls.keys())}"
            )

        url = self._webhook_urls[webhook_name]
        payload: Dict[str, Any] = {"text": text}
        if blocks:
            payload["blocks"] = blocks
        if attachments:
            payload["attachments"] = attachments

        try:
            import urllib.request
            data = json.dumps(payload).encode("utf-8")
            req = urllib.request.Request(
                url, data=data,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=self.config.timeout) as resp:
                body = resp.read().decode("utf-8")
                success = resp.status == 200 and body == "ok"
                if not success:
                    self._logger.warning(
                        "Webhook send returned: %s", body,
                    )
                return success
        except Exception as e:
            raise self._handle_error(f"Webhook send failed: {e}")

    # ------------------------------------------------------------------
    # Internal API helpers
    # ------------------------------------------------------------------

    def _api_call(self, method: str,
                  params: Optional[Dict[str, Any]] = None,
                  http_method: str = "POST") -> Dict[str, Any]:
        """
        Make a Slack Web API call.

        Args:
            method: Slack API method (e.g., "chat.postMessage").
            params: Method parameters.
            http_method: HTTP method to use.

        Returns:
            Parsed JSON response.

        Raises:
            IntegrationError: On API errors.
            RateLimitError: On rate limit exceeded.
        """
        self._check_rate_limit()
        self._mark_request()

        url = f"https://slack.com/api/{method}"
        headers = {
            "Authorization": f"Bearer {self.config.access_token or self.config.api_key or ''}",
            "Content-Type": "application/json; charset=utf-8",
        }

        body = json.dumps(params or {})
        self._log_request(http_method, url, headers, body)

        try:
            import urllib.request
            data = body.encode("utf-8") if http_method == "POST" else None
            if http_method == "GET" and params:
                from urllib.parse import urlencode
                url = f"{url}?{urlencode(params)}"
                data = None

            req = urllib.request.Request(
                url, data=data, headers=headers, method=http_method,
            )
            with urllib.request.urlopen(req, timeout=self.config.timeout) as resp:
                resp_body = resp.read().decode("utf-8")
                self._log_response(http_method, url, resp.status, resp_body)
                result = json.loads(resp_body)

                if not result.get("ok", False):
                    error = result.get("error", "unknown_error")
                    if error == "ratelimited":
                        retry_after = float(resp.headers.get("Retry-After", 1))
                        self._rate_limit.retry_after = time.time() + retry_after
                        raise RateLimitError(
                            f"Slack rate limited: retry after {retry_after}s",
                            retry_after=retry_after,
                            adapter_name=self.config.name,
                        )
                    raise self._handle_error(
                        f"Slack API error ({method}): {error}",
                        status_code=resp.status,
                        response_body=resp_body,
                    )

                return result

        except RateLimitError:
            raise
        except IntegrationError:
            raise
        except urllib.error.HTTPError as e:
            resp_body = e.read().decode("utf-8", errors="replace")
            if e.code == 429:
                retry_after = float(e.headers.get("Retry-After", 1))
                self._rate_limit.retry_after = time.time() + retry_after
                raise RateLimitError(
                    f"Rate limited (HTTP 429): retry after {retry_after}s",
                    retry_after=retry_after,
                    adapter_name=self.config.name,
                )
            raise self._handle_error(
                f"HTTP error {e.code}: {e.reason}",
                status_code=e.code,
                response_body=resp_body,
            )
        except Exception as e:
            raise self._handle_error(f"Request failed: {e}")
