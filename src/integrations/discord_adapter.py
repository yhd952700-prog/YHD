"""
Discord Integration Adapter for LiuHao AI OS.

Provides integration with Discord for sending/receiving messages,
embeds and rich content, slash commands, server/user management,
and webhook support.
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


class DiscordEmbedType(str, Enum):
    """Types of Discord embeds."""
    RICH = "rich"
    IMAGE = "image"
    VIDEO = "video"
    LINK = "link"
    ARTICLE = "article"


class DiscordChannelType(int, Enum):
    """Discord channel type codes."""
    GUILD_TEXT = 0
    DM = 1
    GUILD_VOICE = 2
    GROUP_DM = 3
    GUILD_CATEGORY = 4
    GUILD_ANNOUNCEMENT = 5
    ANNOUNCEMENT_THREAD = 10
    PUBLIC_THREAD = 11
    PRIVATE_THREAD = 12
    GUILD_STAGE_VOICE = 13
    GUILD_DIRECTORY = 14
    GUILD_FORUM = 15


@dataclass
class DiscordEmbedFooter:
    """Footer section of a Discord embed."""
    text: str
    icon_url: str = ""
    proxy_icon_url: str = ""


@dataclass
class DiscordEmbedImage:
    """Image section of a Discord embed."""
    url: str
    proxy_url: str = ""
    height: int = 0
    width: int = 0


@dataclass
class DiscordEmbedField:
    """A field in a Discord embed."""
    name: str
    value: str
    inline: bool = False


@dataclass
class DiscordEmbed:
    """Represents a Discord rich embed."""
    title: str = ""
    description: str = ""
    url: str = ""
    color: int = 0
    timestamp: str = ""
    footer: Optional[DiscordEmbedFooter] = None
    image: Optional[DiscordEmbedImage] = None
    thumbnail: Optional[DiscordEmbedImage] = None
    fields: List[DiscordEmbedField] = field(default_factory=list)
    embed_type: DiscordEmbedType = DiscordEmbedType.RICH

    def to_dict(self) -> Dict[str, Any]:
        """Convert embed to API-compatible dict."""
        data: Dict[str, Any] = {"type": self.embed_type.value}
        if self.title:
            data["title"] = self.title
        if self.description:
            data["description"] = self.description
        if self.url:
            data["url"] = self.url
        if self.color:
            data["color"] = self.color
        if self.timestamp:
            data["timestamp"] = self.timestamp
        if self.footer:
            footer_data: Dict[str, str] = {"text": self.footer.text}
            if self.footer.icon_url:
                footer_data["icon_url"] = self.footer.icon_url
            data["footer"] = footer_data
        if self.image:
            data["image"] = {"url": self.image.url}
        if self.thumbnail:
            data["thumbnail"] = {"url": self.thumbnail.url}
        if self.fields:
            data["fields"] = [
                {"name": f.name, "value": f.value, "inline": f.inline}
                for f in self.fields
            ]
        return data


@dataclass
class DiscordMessage:
    """Represents a Discord message."""
    channel_id: str
    content: str = ""
    embeds: List[DiscordEmbed] = field(default_factory=list)
    message_id: str = ""
    author_id: str = ""
    author_username: str = ""
    guild_id: str = ""
    timestamp: str = ""
    mention_everyone: bool = False
    mentions: List[str] = field(default_factory=list)
    extra: Dict[str, Any] = field(default_factory=dict)


@dataclass
class DiscordSlashCommand:
    """Represents a Discord slash command registration."""
    name: str
    description: str
    options: List[Dict[str, Any]] = field(default_factory=list)
    guild_id: Optional[str] = None
    application_id: str = ""
    command_id: str = ""
    default_permission: bool = True


@dataclass
class DiscordInteraction:
    """Represents a received Discord interaction."""
    id: str
    type: int
    token: str
    guild_id: str = ""
    channel_id: str = ""
    user_id: str = ""
    command_name: str = ""
    options: Dict[str, Any] = field(default_factory=dict)
    raw: Dict[str, Any] = field(default_factory=dict)


@dataclass
class DiscordGuild:
    """Represents a Discord server (guild)."""
    id: str
    name: str
    icon: str = ""
    owner_id: str = ""
    member_count: int = 0
    approximate_member_count: int = 0
    extra: Dict[str, Any] = field(default_factory=dict)


class DiscordAdapter(BaseIntegrationAdapter):
    """
    Discord integration adapter for LiuHao AI OS.

    Supports:
    - Sending messages to channels and DMs
    - Receiving messages via webhook gateway
    - Rich embeds and attachments
    - Slash command creation and handling
    - Server (guild) and user management
    - Webhook-based messaging
    """

    def __init__(self, config: IntegrationConfig):
        """
        Initialize the Discord adapter.

        Args:
            config: Integration config with bot token (access_token).
        """
        if config.auth_type == AuthType.NONE:
            config.auth_type = AuthType.BEARER_TOKEN
        if not config.base_url:
            config.base_url = "https://discord.com/api/v10/"
        super().__init__(config)
        self._application_id: Optional[str] = None
        self._bot_user_id: Optional[str] = None
        self._bot_username: Optional[str] = None
        self._webhooks: Dict[str, str] = {}
        self._global_rate_limit: float = 0.0

    # ------------------------------------------------------------------
    # Base interface
    # ------------------------------------------------------------------

    def connect(self) -> bool:
        """
        Connect to Discord API and validate bot token.

        Returns:
            True on success.
        """
        self._status = ConnectionStatus.CONNECTING
        self._logger.info("Connecting to Discord API...")

        try:
            user_data = self._api_call("GET", "users/@me")
            self._bot_user_id = user_data.get("id")
            self._bot_username = user_data.get("username")
            self._application_id = user_data.get("id")  # Bot user = app for bots

            # Fetch actual application info
            try:
                app_data = self._api_call("GET", "oauth2/applications/@me")
                self._application_id = app_data.get("id", self._application_id)
            except Exception:
                pass  # Not all tokens have this scope

            self._status = ConnectionStatus.CONNECTED
            self._connected_at = time.time()
            self._logger.info(
                "Connected to Discord as %s#%s (ID: %s)",
                user_data.get("username"),
                user_data.get("discriminator", ""),
                user_data.get("id"),
            )
            return True
        except IntegrationError:
            raise
        except Exception as e:
            self._status = ConnectionStatus.ERROR
            raise self._handle_error(f"Connection error: {e}")

    def disconnect(self) -> bool:
        """Disconnect from Discord."""
        self._status = ConnectionStatus.DISCONNECTED
        self._bot_user_id = None
        self._bot_username = None
        self._application_id = None
        self._session = None
        self._logger.info("Disconnected from Discord")
        return True

    def is_connected(self) -> bool:
        """Check if connected to Discord."""
        return self._status == ConnectionStatus.CONNECTED

    def get_status(self) -> IntegrationStatus:
        """Get current adapter status."""
        return self._build_status({
            "bot_user_id": self._bot_user_id,
            "bot_username": self._bot_username,
            "application_id": self._application_id,
            "webhook_count": len(self._webhooks),
        })

    def test_connection(self) -> bool:
        """Test Discord API connection."""
        try:
            if not self.is_connected():
                return False
            result = self._api_call("GET", "users/@me")
            return "id" in result
        except Exception:
            return False

    # ------------------------------------------------------------------
    # Message sending
    # ------------------------------------------------------------------

    def send_message(self, message: DiscordMessage) -> Dict[str, Any]:
        """
        Send a message to a Discord channel or DM.

        Args:
            message: DiscordMessage with channel_id, content, embeds, etc.

        Returns:
            Created message data.
        """
        payload: Dict[str, Any] = {"content": message.content}

        if message.embeds:
            payload["embeds"] = [e.to_dict() for e in message.embeds]
        if message.mention_everyone:
            payload["allowed_mentions"] = {"parse": ["everyone"]}
        if message.extra:
            payload.update(message.extra)

        return self._api_call(
            "POST",
            f"channels/{message.channel_id}/messages",
            json_body=payload,
        )

    def send_text(self, channel_id: str, content: str) -> Dict[str, Any]:
        """Send a simple text message."""
        msg = DiscordMessage(channel_id=channel_id, content=content)
        return self.send_message(msg)

    def send_embed(self, channel_id: str, embed: DiscordEmbed,
                   content: str = "") -> Dict[str, Any]:
        """
        Send a message with a rich embed.

        Args:
            channel_id: Target channel ID.
            embed: DiscordEmbed to send.
            content: Optional plain text content.

        Returns:
            Created message data.
        """
        msg = DiscordMessage(
            channel_id=channel_id,
            content=content,
            embeds=[embed],
        )
        return self.send_message(msg)

    def send_embeds(self, channel_id: str,
                    embeds: List[DiscordEmbed],
                    content: str = "") -> Dict[str, Any]:
        """Send a message with multiple embeds (up to 10)."""
        msg = DiscordMessage(
            channel_id=channel_id,
            content=content,
            embeds=embeds[:10],
        )
        return self.send_message(msg)

    def edit_message(self, channel_id: str, message_id: str,
                     content: str = "",
                     embeds: Optional[List[DiscordEmbed]] = None
                     ) -> Dict[str, Any]:
        """Edit an existing message."""
        payload: Dict[str, Any] = {"content": content}
        if embeds is not None:
            payload["embeds"] = [e.to_dict() for e in embeds]
        return self._api_call(
            "PATCH",
            f"channels/{channel_id}/messages/{message_id}",
            json_body=payload,
        )

    def delete_message(self, channel_id: str, message_id: str) -> bool:
        """Delete a message. Returns True on success."""
        try:
            self._api_call(
                "DELETE",
                f"channels/{channel_id}/messages/{message_id}",
            )
            return True
        except Exception:
            return False

    def get_channel_messages(self, channel_id: str, limit: int = 50,
                             around: Optional[str] = None,
                             before: Optional[str] = None,
                             after: Optional[str] = None
                             ) -> List[DiscordMessage]:
        """
        Get messages from a channel.

        Args:
            channel_id: Channel ID.
            limit: Max messages (1-100).
            around: Get messages around this snowflake.
            before: Get messages before this snowflake.
            after: Get messages after this snowflake.

        Returns:
            List of DiscordMessage objects.
        """
        params: Dict[str, Any] = {"limit": min(limit, 100)}
        if around:
            params["around"] = around
        if before:
            params["before"] = before
        if after:
            params["after"] = after

        data = self._api_call(
            "GET", f"channels/{channel_id}/messages", params=params,
        )
        messages: List[DiscordMessage] = []
        for msg_data in data:
            author = msg_data.get("author", {})
            messages.append(DiscordMessage(
                channel_id=msg_data.get("channel_id", channel_id),
                content=msg_data.get("content", ""),
                message_id=msg_data.get("id", ""),
                author_id=author.get("id", ""),
                author_username=author.get("username", ""),
                guild_id=msg_data.get("guild_id", ""),
                timestamp=msg_data.get("timestamp", ""),
                mention_everyone=msg_data.get("mention_everyone", False),
                mentions=[m.get("id", "") for m in msg_data.get("mentions", [])],
                extra=msg_data,
            ))
        return messages

    # ------------------------------------------------------------------
    # DM support
    # ------------------------------------------------------------------

    def create_dm(self, user_id: str) -> str:
        """
        Create or get a DM channel with a user.

        Args:
            user_id: User ID to DM.

        Returns:
            DM channel ID.
        """
        data = self._api_call(
            "POST", "users/@me/channels",
            json_body={"recipient_id": user_id},
        )
        return data.get("id", "")

    def send_dm(self, user_id: str, content: str = "",
                embed: Optional[DiscordEmbed] = None) -> Dict[str, Any]:
        """
        Send a direct message to a user.

        Args:
            user_id: Target user ID.
            content: Message text.
            embed: Optional rich embed.

        Returns:
            Created message data.
        """
        channel_id = self.create_dm(user_id)
        msg = DiscordMessage(channel_id=channel_id, content=content)
        if embed:
            msg.embeds = [embed]
        return self.send_message(msg)

    # ------------------------------------------------------------------
    # Slash commands
    # ------------------------------------------------------------------

    def register_slash_command(self, command: DiscordSlashCommand) -> Dict[str, Any]:
        """
        Register a global or guild slash command.

        Args:
            command: Command definition.

        Returns:
            Created command data.
        """
        if not self._application_id:
            raise self._handle_error("Not connected: no application ID")

        payload: Dict[str, Any] = {
            "name": command.name,
            "description": command.description,
        }
        if command.options:
            payload["options"] = command.options
        if not command.default_permission:
            payload["default_permission"] = False

        if command.guild_id:
            path = (f"applications/{self._application_id}/"
                    f"guilds/{command.guild_id}/commands")
        else:
            path = f"applications/{self._application_id}/commands"

        return self._api_call("POST", path, json_body=payload)

    def list_slash_commands(self, guild_id: Optional[str] = None
                            ) -> List[Dict[str, Any]]:
        """List all registered slash commands."""
        if not self._application_id:
            raise self._handle_error("Not connected: no application ID")

        if guild_id:
            path = (f"applications/{self._application_id}/"
                    f"guilds/{guild_id}/commands")
        else:
            path = f"applications/{self._application_id}/commands"

        return self._api_call("GET", path)

    def delete_slash_command(self, command_id: str,
                             guild_id: Optional[str] = None) -> bool:
        """Delete a slash command. Returns True on success."""
        if not self._application_id:
            raise self._handle_error("Not connected: no application ID")

        if guild_id:
            path = (f"applications/{self._application_id}/"
                    f"guilds/{guild_id}/commands/{command_id}")
        else:
            path = f"applications/{self._application_id}/commands/{command_id}"

        try:
            self._api_call("DELETE", path)
            return True
        except Exception:
            return False

    def parse_interaction(self, payload: Dict[str, Any]) -> DiscordInteraction:
        """
        Parse an incoming interaction payload.

        Args:
            payload: Raw JSON payload from Discord.

        Returns:
            DiscordInteraction object.
        """
        data = payload.get("data", {})
        member = payload.get("member", {})
        user = member.get("user", payload.get("user", {}))

        options: Dict[str, Any] = {}
        for opt in data.get("options", []):
            options[opt["name"]] = opt.get("value")

        return DiscordInteraction(
            id=payload.get("id", ""),
            type=payload.get("type", 0),
            token=payload.get("token", ""),
            guild_id=payload.get("guild_id", ""),
            channel_id=payload.get("channel_id", ""),
            user_id=user.get("id", ""),
            command_name=data.get("name", ""),
            options=options,
            raw=payload,
        )

    def respond_to_interaction(self, interaction: DiscordInteraction,
                               content: str = "",
                               embeds: Optional[List[DiscordEmbed]] = None,
                               ephemeral: bool = False) -> bool:
        """
        Send a response to an interaction (within 3 seconds).

        Args:
            interaction: The interaction to respond to.
            content: Response text.
            embeds: Optional rich embeds.
            ephemeral: Whether response is only visible to the user.

        Returns:
            True on success.
        """
        data: Dict[str, Any] = {"content": content}
        if embeds:
            data["embeds"] = [e.to_dict() for e in embeds]
        if ephemeral:
            data["flags"] = 64  # EPHEMERAL flag

        payload = {
            "type": 4,  # CHANNEL_MESSAGE_WITH_SOURCE
            "data": data,
        }

        try:
            self._api_call(
                "POST",
                f"interactions/{interaction.id}/{interaction.token}/callback",
                json_body=payload,
            )
            return True
        except Exception:
            return False

    # ------------------------------------------------------------------
    # Server / guild management
    # ------------------------------------------------------------------

    def list_guilds(self, limit: int = 200,
                    before: Optional[str] = None,
                    after: Optional[str] = None) -> List[DiscordGuild]:
        """
        List guilds the bot is a member of.

        Args:
            limit: Max number of guilds (1-200).
            before: Get guilds before this snowflake.
            after: Get guilds after this snowflake.

        Returns:
            List of DiscordGuild objects.
        """
        params: Dict[str, Any] = {"limit": min(limit, 200)}
        if before:
            params["before"] = before
        if after:
            params["after"] = after

        data = self._api_call("GET", "users/@me/guilds", params=params)
        guilds: List[DiscordGuild] = []
        for g in data:
            guilds.append(DiscordGuild(
                id=g.get("id", ""),
                name=g.get("name", ""),
                icon=g.get("icon", ""),
                owner_id=g.get("owner_id", ""),
                approximate_member_count=g.get("approximate_member_count", 0),
                extra=g,
            ))
        return guilds

    def get_guild(self, guild_id: str,
                  with_counts: bool = False) -> DiscordGuild:
        """Get details for a specific guild."""
        params = {"with_counts": "true"} if with_counts else {}
        g = self._api_call("GET", f"guilds/{guild_id}", params=params)
        return DiscordGuild(
            id=g.get("id", guild_id),
            name=g.get("name", ""),
            icon=g.get("icon", ""),
            owner_id=g.get("owner_id", ""),
            member_count=g.get("member_count", 0),
            approximate_member_count=g.get("approximate_member_count", 0),
            extra=g,
        )

    def list_guild_channels(self, guild_id: str) -> List[Dict[str, Any]]:
        """List all channels in a guild."""
        return self._api_call("GET", f"guilds/{guild_id}/channels")

    # ------------------------------------------------------------------
    # User management
    # ------------------------------------------------------------------

    def get_user(self, user_id: str) -> Dict[str, Any]:
        """Get user info by ID."""
        return self._api_call("GET", f"users/{user_id}")

    def get_guild_member(self, guild_id: str,
                         user_id: str) -> Dict[str, Any]:
        """Get guild member info."""
        return self._api_call("GET", f"guilds/{guild_id}/members/{user_id}")

    def list_guild_members(self, guild_id: str, limit: int = 100,
                           after: Optional[str] = None
                           ) -> List[Dict[str, Any]]:
        """List members of a guild."""
        params: Dict[str, Any] = {"limit": min(limit, 1000)}
        if after:
            params["after"] = after
        return self._api_call(
            "GET", f"guilds/{guild_id}/members", params=params,
        )

    # ------------------------------------------------------------------
    # Webhook support
    # ------------------------------------------------------------------

    def register_webhook(self, name: str, webhook_url: str) -> None:
        """Register a named webhook URL for sending messages."""
        self._webhooks[name] = webhook_url

    def send_via_webhook(self, webhook_name: str,
                         content: str = "",
                         embeds: Optional[List[DiscordEmbed]] = None,
                         username: str = "",
                         avatar_url: str = "") -> Dict[str, Any]:
        """
        Send a message via a registered webhook (no bot token needed).

        Args:
            webhook_name: Name of registered webhook.
            content: Message text.
            embeds: Optional rich embeds.
            username: Override webhook username.
            avatar_url: Override webhook avatar.

        Returns:
            Response data.
        """
        if webhook_name not in self._webhooks:
            raise self._handle_error(
                f"Unknown webhook: {webhook_name}"
            )

        url = self._webhooks[webhook_name]
        payload: Dict[str, Any] = {"content": content}
        if embeds:
            payload["embeds"] = [e.to_dict() for e in embeds]
        if username:
            payload["username"] = username
        if avatar_url:
            payload["avatar_url"] = avatar_url

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
                if resp.status in (200, 204):
                    return json.loads(body) if body else {"ok": True}
                raise self._handle_error(
                    f"Webhook failed with {resp.status}",
                    status_code=resp.status,
                    response_body=body,
                )
        except IntegrationError:
            raise
        except Exception as e:
            raise self._handle_error(f"Webhook error: {e}")

    # ------------------------------------------------------------------
    # Internal API helper
    # ------------------------------------------------------------------

    def _api_call(self, method: str, path: str,
                  params: Optional[Dict[str, Any]] = None,
                  json_body: Optional[Dict[str, Any]] = None) -> Any:
        """
        Make a Discord API call.

        Handles rate limiting per Discord's global and per-route limits.
        """
        self._check_rate_limit()

        # Global rate limit check
        now = time.time()
        if self._global_rate_limit > now:
            wait = self._global_rate_limit - now
            if wait > 5:
                raise RateLimitError(
                    f"Global rate limit active, retry after {wait:.1f}s",
                    retry_after=wait,
                    adapter_name=self.config.name,
                )
            time.sleep(wait)

        self._mark_request()

        url = self._build_url(path)
        token = self.config.access_token or self.config.api_key or ""
        headers = {
            "Authorization": f"Bot {token}",
            "Content-Type": "application/json",
            "User-Agent": "LiuHao-AI-OS (https://liuhao.ai, 1.0)",
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

                # Check remaining rate limit
                remaining = resp.headers.get("X-RateLimit-Remaining")
                reset_after = resp.headers.get("X-RateLimit-Reset-After")
                if remaining == "0" and reset_after:
                    self._rate_limit.retry_after = now + float(reset_after)

                if resp.status == 204:
                    return {}
                return json.loads(resp_body)

        except urllib.error.HTTPError as e:
            resp_body = e.read().decode("utf-8", errors="replace")
            self._log_response(method, url, e.code, resp_body)

            if e.code == 429:
                try:
                    error_data = json.loads(resp_body)
                    retry_after = float(error_data.get("retry_after", 1))
                    if error_data.get("global"):
                        self._global_rate_limit = time.time() + retry_after
                    else:
                        self._rate_limit.retry_after = time.time() + retry_after
                    self._rate_limit_hits += 1
                    raise RateLimitError(
                        f"Discord rate limited: retry after {retry_after}s",
                        retry_after=retry_after,
                        adapter_name=self.config.name,
                    )
                except (json.JSONDecodeError, ValueError):
                    raise RateLimitError(
                        "Discord rate limited (HTTP 429)",
                        adapter_name=self.config.name,
                    )

            if e.code == 401:
                self._status = ConnectionStatus.ERROR
                raise self._handle_error(
                    "Authentication failed: invalid bot token",
                    status_code=401,
                    response_body=resp_body,
                )

            raise self._handle_error(
                f"Discord API error {e.code}: {e.reason}",
                status_code=e.code,
                response_body=resp_body,
            )
        except (RateLimitError, IntegrationError):
            raise
        except Exception as e:
            raise self._handle_error(f"Request failed: {e}")
