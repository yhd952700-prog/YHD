"""
Telegram Bot Integration Adapter for LiuHao AI OS.

Provides integration with Telegram Bot API for sending/receiving messages,
inline keyboards, chat management, file operations, and bot commands.
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


class TelegramChatType(str, Enum):
    """Telegram chat types."""
    PRIVATE = "private"
    GROUP = "group"
    SUPERGROUP = "supergroup"
    CHANNEL = "channel"


class TelegramParseMode(str, Enum):
    """Message parse modes."""
    MARKDOWN = "Markdown"
    MARKDOWN_V2 = "MarkdownV2"
    HTML = "HTML"
    NONE = ""


class TelegramUpdateType(str, Enum):
    """Types of Telegram updates."""
    MESSAGE = "message"
    EDITED_MESSAGE = "edited_message"
    CHANNEL_POST = "channel_post"
    CALLBACK_QUERY = "callback_query"
    INLINE_QUERY = "inline_query"
    WEBHOOK = "webhook"


@dataclass
class TelegramUser:
    """Represents a Telegram user."""
    id: int
    is_bot: bool = False
    first_name: str = ""
    last_name: str = ""
    username: str = ""
    language_code: str = ""
    is_premium: bool = False
    extra: Dict[str, Any] = field(default_factory=dict)


@dataclass
class TelegramChat:
    """Represents a Telegram chat."""
    id: int
    type: TelegramChatType = TelegramChatType.PRIVATE
    title: str = ""
    username: str = ""
    first_name: str = ""
    last_name: str = ""
    description: str = ""
    member_count: int = 0
    extra: Dict[str, Any] = field(default_factory=dict)


@dataclass
class TelegramMessage:
    """Represents a Telegram message to send."""
    chat_id: int
    text: str = ""
    parse_mode: TelegramParseMode = TelegramParseMode.NONE
    reply_to_message_id: Optional[int] = None
    reply_markup: Optional[Dict[str, Any]] = None
    disable_web_page_preview: bool = False
    disable_notification: bool = False
    protect_content: bool = False
    message_id: Optional[int] = None
    from_user: Optional[TelegramUser] = None
    chat: Optional[TelegramChat] = None
    date: Optional[int] = None
    extra: Dict[str, Any] = field(default_factory=dict)


@dataclass
class TelegramInlineKeyboardButton:
    """Represents an inline keyboard button."""
    text: str
    url: Optional[str] = None
    callback_data: Optional[str] = None
    switch_inline_query: Optional[str] = None
    switch_inline_query_current_chat: Optional[str] = None
    pay: bool = False


@dataclass
class TelegramFile:
    """Represents a Telegram file."""
    file_id: str
    file_unique_id: str = ""
    file_size: int = 0
    file_path: str = ""
    extra: Dict[str, Any] = field(default_factory=dict)


@dataclass
class TelegramBotCommand:
    """Represents a bot command."""
    command: str
    description: str


class TelegramAdapter(BaseIntegrationAdapter):
    """
    Telegram Bot integration adapter for LiuHao AI OS.

    Supports:
    - Sending messages (text, photo, document, audio)
    - Receiving messages via webhook or long polling
    - Inline keyboards and callback queries
    - Chat management (kick, ban, restrict, promote)
    - User info retrieval
    - File upload and download
    - Bot command management
    - Webhook setup and verification
    """

    def __init__(self, config: IntegrationConfig):
        """
        Initialize the Telegram adapter.

        Args:
            config: Integration configuration. Must have api_key set to the bot token.
        """
        if config.auth_type == AuthType.NONE:
            config.auth_type = AuthType.API_KEY
        if not config.base_url:
            config.base_url = "https://api.telegram.org"
        super().__init__(config)
        self._bot_info: Optional[TelegramUser] = None
        self._webhook_set: bool = False
        self._polling_offset: int = 0
        self._pending_updates: List[Dict[str, Any]] = []

    # ------------------------------------------------------------------
    # Base interface implementation
    # ------------------------------------------------------------------

    def connect(self) -> bool:
        """
        Establish connection to Telegram Bot API and validate credentials.

        Returns:
            True if connection successful.

        Raises:
            AuthenticationError: If bot token is invalid.
        """
        self._status = ConnectionStatus.CONNECTING
        self._logger.info("Connecting to Telegram Bot API...")

        try:
            result = self._api_call("getMe")
            if result.get("ok"):
                user_data = result.get("result", {})
                self._bot_info = TelegramUser(
                    id=user_data.get("id", 0),
                    is_bot=user_data.get("is_bot", False),
                    first_name=user_data.get("first_name", ""),
                    username=user_data.get("username", ""),
                )
                self._status = ConnectionStatus.CONNECTED
                self._connected_at = time.time()
                self._logger.info(
                    "Connected to Telegram as @%s (%s)",
                    self._bot_info.username, self._bot_info.first_name,
                )
                return True
            else:
                error_msg = result.get("description", "Unknown error")
                self._status = ConnectionStatus.ERROR
                raise self._handle_error(
                    f"Telegram auth failed: {error_msg}",
                    status_code=401,
                )
        except IntegrationError:
            raise
        except Exception as e:
            self._status = ConnectionStatus.ERROR
            raise self._handle_error(f"Connection error: {e}")

    def disconnect(self) -> bool:
        """
        Disconnect from Telegram (clear session state).

        Returns:
            Always returns True.
        """
        self._status = ConnectionStatus.DISCONNECTED
        self._bot_info = None
        self._webhook_set = False
        self._pending_updates = []
        self._session = None
        self._logger.info("Disconnected from Telegram")
        return True

    def is_connected(self) -> bool:
        """
        Check if connected to Telegram API.

        Returns:
            True if status is CONNECTED.
        """
        return self._status == ConnectionStatus.CONNECTED

    def get_status(self) -> IntegrationStatus:
        """
        Get current adapter status.

        Returns:
            IntegrationStatus with Telegram-specific extra info.
        """
        return self._build_status({
            "bot_username": self._bot_info.username if self._bot_info else None,
            "bot_id": self._bot_info.id if self._bot_info else None,
            "webhook_set": self._webhook_set,
            "pending_updates": len(self._pending_updates),
        })

    def test_connection(self) -> bool:
        """
        Test the Telegram API connection.

        Returns:
            True if connection is working.
        """
        try:
            if not self.is_connected():
                return False
            result = self._api_call("getMe")
            return result.get("ok", False)
        except Exception:
            return False

    # ------------------------------------------------------------------
    # Message sending
    # ------------------------------------------------------------------

    def send_message(self, message: TelegramMessage) -> Dict[str, Any]:
        """
        Send a text message to a Telegram chat.

        Args:
            message: TelegramMessage with chat_id, text, and optional parameters.

        Returns:
            API response data with message details.

        Raises:
            IntegrationError: If message send fails.
        """
        payload: Dict[str, Any] = {
            "chat_id": message.chat_id,
            "text": message.text,
            "disable_web_page_preview": message.disable_web_page_preview,
            "disable_notification": message.disable_notification,
            "protect_content": message.protect_content,
        }

        if message.parse_mode != TelegramParseMode.NONE:
            payload["parse_mode"] = message.parse_mode.value
        if message.reply_to_message_id is not None:
            payload["reply_to_message_id"] = message.reply_to_message_id
        if message.reply_markup:
            payload["reply_markup"] = json.dumps(message.reply_markup)
        if message.extra:
            payload.update(message.extra)

        result = self._api_call("sendMessage", payload)
        return result.get("result", {})

    def send_photo(self, chat_id: int, photo: str,
                   caption: str = "",
                   parse_mode: TelegramParseMode = TelegramParseMode.NONE,
                   **kwargs: Any) -> Dict[str, Any]:
        """
        Send a photo to a chat.

        Args:
            chat_id: Target chat ID.
            photo: Photo file_id or URL.
            caption: Photo caption.
            parse_mode: Caption parse mode.
            **kwargs: Additional parameters.

        Returns:
            API response data.
        """
        payload: Dict[str, Any] = {
            "chat_id": chat_id,
            "photo": photo,
            "caption": caption,
        }
        if parse_mode != TelegramParseMode.NONE:
            payload["parse_mode"] = parse_mode.value
        payload.update(kwargs)

        result = self._api_call("sendPhoto", payload)
        return result.get("result", {})

    def send_document(self, chat_id: int, document: str,
                      caption: str = "",
                      **kwargs: Any) -> Dict[str, Any]:
        """
        Send a document to a chat.

        Args:
            chat_id: Target chat ID.
            document: Document file_id or URL.
            caption: Document caption.
            **kwargs: Additional parameters.

        Returns:
            API response data.
        """
        payload: Dict[str, Any] = {
            "chat_id": chat_id,
            "document": document,
            "caption": caption,
        }
        payload.update(kwargs)

        result = self._api_call("sendDocument", payload)
        return result.get("result", {})

    def send_audio(self, chat_id: int, audio: str,
                   caption: str = "",
                   title: str = "",
                   performer: str = "",
                   **kwargs: Any) -> Dict[str, Any]:
        """
        Send an audio file to a chat.

        Args:
            chat_id: Target chat ID.
            audio: Audio file_id or URL.
            caption: Audio caption.
            title: Track title.
            performer: Track performer.
            **kwargs: Additional parameters.

        Returns:
            API response data.
        """
        payload: Dict[str, Any] = {
            "chat_id": chat_id,
            "audio": audio,
            "caption": caption,
            "title": title,
            "performer": performer,
        }
        payload.update(kwargs)

        result = self._api_call("sendAudio", payload)
        return result.get("result", {})

    def edit_message_text(self, chat_id: int, message_id: int,
                          text: str,
                          parse_mode: TelegramParseMode = TelegramParseMode.NONE,
                          reply_markup: Optional[Dict[str, Any]] = None,
                          **kwargs: Any) -> Dict[str, Any]:
        """
        Edit a text message.

        Args:
            chat_id: Chat ID.
            message_id: Message ID to edit.
            text: New message text.
            parse_mode: Parse mode for the text.
            reply_markup: New inline keyboard markup.
            **kwargs: Additional parameters.

        Returns:
            API response data.
        """
        payload: Dict[str, Any] = {
            "chat_id": chat_id,
            "message_id": message_id,
            "text": text,
        }
        if parse_mode != TelegramParseMode.NONE:
            payload["parse_mode"] = parse_mode.value
        if reply_markup:
            payload["reply_markup"] = json.dumps(reply_markup)
        payload.update(kwargs)

        result = self._api_call("editMessageText", payload)
        return result.get("result", {})

    def delete_message(self, chat_id: int, message_id: int) -> bool:
        """
        Delete a message.

        Args:
            chat_id: Chat ID.
            message_id: Message ID to delete.

        Returns:
            True if deletion was successful.
        """
        result = self._api_call("deleteMessage", {
            "chat_id": chat_id,
            "message_id": message_id,
        })
        return result.get("ok", False)

    # ------------------------------------------------------------------
    # Receiving messages / updates
    # ------------------------------------------------------------------

    def get_updates(self, limit: int = 100,
                    timeout: int = 0,
                    allowed_updates: Optional[List[str]] = None,
                    **kwargs: Any) -> List[Dict[str, Any]]:
        """
        Receive incoming updates via long polling.

        Args:
            limit: Maximum number of updates to receive.
            timeout: Long polling timeout in seconds.
            allowed_updates: List of update types to receive.
            **kwargs: Additional parameters.

        Returns:
            List of update objects.
        """
        payload: Dict[str, Any] = {
            "offset": self._polling_offset,
            "limit": limit,
            "timeout": timeout,
        }
        if allowed_updates:
            payload["allowed_updates"] = json.dumps(allowed_updates)
        payload.update(kwargs)

        result = self._api_call("getUpdates", payload)
        updates = result.get("result", [])

        if updates:
            self._polling_offset = updates[-1]["update_id"] + 1

        return updates

    def set_webhook(self, url: str,
                    secret_token: Optional[str] = None,
                    allowed_updates: Optional[List[str]] = None,
                    **kwargs: Any) -> bool:
        """
        Set up a webhook to receive updates.

        Args:
            url: Webhook URL.
            secret_token: Secret token for webhook verification.
            allowed_updates: List of update types to receive.
            **kwargs: Additional parameters.

        Returns:
            True if webhook was set successfully.
        """
        payload: Dict[str, Any] = {"url": url}
        if secret_token:
            payload["secret_token"] = secret_token
        if allowed_updates:
            payload["allowed_updates"] = json.dumps(allowed_updates)
        payload.update(kwargs)

        result = self._api_call("setWebhook", payload)
        success = result.get("ok", False)
        if success:
            self._webhook_set = True
        return success

    def delete_webhook(self, drop_pending_updates: bool = False) -> bool:
        """
        Remove the webhook and switch back to long polling.

        Args:
            drop_pending_updates: Whether to drop pending updates.

        Returns:
            True if webhook was deleted successfully.
        """
        result = self._api_call("deleteWebhook", {
            "drop_pending_updates": drop_pending_updates,
        })
        success = result.get("ok", False)
        if success:
            self._webhook_set = False
        return success

    def get_webhook_info(self) -> Dict[str, Any]:
        """
        Get current webhook information.

        Returns:
            Webhook info dictionary.
        """
        result = self._api_call("getWebhookInfo")
        return result.get("result", {})

    def verify_webhook_update(self, request_body: str,
                              secret_token: str,
                              headers: Dict[str, str]) -> bool:
        """
        Verify that an incoming webhook update is authentic.

        Args:
            request_body: Raw request body.
            secret_token: Expected secret token.
            headers: Request headers.

        Returns:
            True if the update is verified.
        """
        provided_token = headers.get("X-Telegram-Bot-Api-Secret-Token", "")
        return hmac.compare_digest(provided_token, secret_token)

    # ------------------------------------------------------------------
    # Inline keyboards
    # ------------------------------------------------------------------

    def build_inline_keyboard(self,
                              buttons: List[List[TelegramInlineKeyboardButton]]
                              ) -> Dict[str, Any]:
        """
        Build an inline keyboard markup from button definitions.

        Args:
            buttons: 2D list of inline keyboard buttons.

        Returns:
            Reply markup dictionary for use in messages.
        """
        keyboard = []
        for row in buttons:
            row_data = []
            for btn in row:
                btn_data: Dict[str, Any] = {"text": btn.text}
                if btn.url:
                    btn_data["url"] = btn.url
                if btn.callback_data:
                    btn_data["callback_data"] = btn.callback_data
                if btn.switch_inline_query:
                    btn_data["switch_inline_query"] = btn.switch_inline_query
                if btn.switch_inline_query_current_chat:
                    btn_data["switch_inline_query_current_chat"] = (
                        btn.switch_inline_query_current_chat
                    )
                if btn.pay:
                    btn_data["pay"] = btn.pay
                row_data.append(btn_data)
            keyboard.append(row_data)

        return {"inline_keyboard": keyboard}

    def answer_callback_query(self, callback_query_id: str,
                              text: str = "",
                              show_alert: bool = False,
                              url: str = "",
                              cache_time: int = 0) -> bool:
        """
        Answer a callback query from an inline keyboard button.

        Args:
            callback_query_id: ID of the callback query.
            text: Text to show to the user.
            show_alert: Whether to show an alert instead of a toast.
            url: URL to open.
            cache_time: Cache time in seconds.

        Returns:
            True if the answer was sent successfully.
        """
        payload: Dict[str, Any] = {
            "callback_query_id": callback_query_id,
            "text": text,
            "show_alert": show_alert,
            "cache_time": cache_time,
        }
        if url:
            payload["url"] = url

        result = self._api_call("answerCallbackQuery", payload)
        return result.get("ok", False)

    # ------------------------------------------------------------------
    # Chat management
    # ------------------------------------------------------------------

    def get_chat(self, chat_id: int) -> TelegramChat:
        """
        Get information about a chat.

        Args:
            chat_id: Chat ID.

        Returns:
            TelegramChat object with chat info.
        """
        result = self._api_call("getChat", {"chat_id": chat_id})
        chat_data = result.get("result", {})
        return TelegramChat(
            id=chat_data.get("id", 0),
            type=TelegramChatType(chat_data.get("type", "private")),
            title=chat_data.get("title", ""),
            username=chat_data.get("username", ""),
            first_name=chat_data.get("first_name", ""),
            last_name=chat_data.get("last_name", ""),
            description=chat_data.get("description", ""),
            extra=chat_data,
        )

    def get_chat_member_count(self, chat_id: int) -> int:
        """
        Get the number of members in a chat.

        Args:
            chat_id: Chat ID.

        Returns:
            Number of chat members.
        """
        result = self._api_call("getChatMemberCount", {"chat_id": chat_id})
        return result.get("result", 0)

    def ban_chat_member(self, chat_id: int, user_id: int,
                        until_date: int = 0,
                        revoke_messages: bool = False) -> bool:
        """
        Ban a user from a chat.

        Args:
            chat_id: Chat ID.
            user_id: User ID to ban.
            until_date: Date when the ban will be lifted (unix time).
            revoke_messages: Whether to revoke all messages from the user.

        Returns:
            True if ban was successful.
        """
        payload: Dict[str, Any] = {
            "chat_id": chat_id,
            "user_id": user_id,
            "revoke_messages": revoke_messages,
        }
        if until_date > 0:
            payload["until_date"] = until_date

        result = self._api_call("banChatMember", payload)
        return result.get("ok", False)

    def unban_chat_member(self, chat_id: int, user_id: int,
                          only_if_banned: bool = False) -> bool:
        """
        Unban a user from a chat.

        Args:
            chat_id: Chat ID.
            user_id: User ID to unban.
            only_if_banned: Only unban if the user is currently banned.

        Returns:
            True if unban was successful.
        """
        result = self._api_call("unbanChatMember", {
            "chat_id": chat_id,
            "user_id": user_id,
            "only_if_banned": only_if_banned,
        })
        return result.get("ok", False)

    def leave_chat(self, chat_id: int) -> bool:
        """
        Make the bot leave a chat.

        Args:
            chat_id: Chat ID.

        Returns:
            True if the bot left successfully.
        """
        result = self._api_call("leaveChat", {"chat_id": chat_id})
        return result.get("ok", False)

    # ------------------------------------------------------------------
    # User info
    # ------------------------------------------------------------------

    def get_chat_member(self, chat_id: int, user_id: int) -> Dict[str, Any]:
        """
        Get information about a chat member.

        Args:
            chat_id: Chat ID.
            user_id: User ID.

        Returns:
            Chat member data.
        """
        result = self._api_call("getChatMember", {
            "chat_id": chat_id,
            "user_id": user_id,
        })
        return result.get("result", {})

    def get_me(self) -> Optional[TelegramUser]:
        """
        Get bot user info.

        Returns:
            TelegramUser for the bot, or None if not connected.
        """
        return self._bot_info

    # ------------------------------------------------------------------
    # File operations
    # ------------------------------------------------------------------

    def get_file(self, file_id: str) -> TelegramFile:
        """
        Get basic info about a file and prepare it for download.

        Args:
            file_id: File ID to get info for.

        Returns:
            TelegramFile object with file info.
        """
        result = self._api_call("getFile", {"file_id": file_id})
        file_data = result.get("result", {})
        return TelegramFile(
            file_id=file_data.get("file_id", file_id),
            file_unique_id=file_data.get("file_unique_id", ""),
            file_size=file_data.get("file_size", 0),
            file_path=file_data.get("file_path", ""),
            extra=file_data,
        )

    def get_file_download_url(self, file_path: str) -> str:
        """
        Build the full download URL for a file.

        Args:
            file_path: File path from get_file().

        Returns:
            Full download URL.
        """
        token = self.config.api_key or self.config.access_token or ""
        return f"https://api.telegram.org/file/bot{token}/{file_path}"

    # ------------------------------------------------------------------
    # Bot commands
    # ------------------------------------------------------------------

    def set_my_commands(self, commands: List[TelegramBotCommand],
                        scope: Optional[Dict[str, Any]] = None,
                        language_code: str = "") -> bool:
        """
        Set the list of the bot's commands.

        Args:
            commands: List of bot commands.
            scope: Command scope.
            language_code: Language code.

        Returns:
            True if commands were set successfully.
        """
        cmd_list = [{"command": c.command, "description": c.description}
                    for c in commands]
        payload: Dict[str, Any] = {
            "commands": json.dumps(cmd_list),
        }
        if scope:
            payload["scope"] = json.dumps(scope)
        if language_code:
            payload["language_code"] = language_code

        result = self._api_call("setMyCommands", payload)
        return result.get("ok", False)

    def get_my_commands(self, scope: Optional[Dict[str, Any]] = None,
                        language_code: str = "") -> List[TelegramBotCommand]:
        """
        Get the current list of the bot's commands.

        Args:
            scope: Command scope.
            language_code: Language code.

        Returns:
            List of TelegramBotCommand objects.
        """
        payload: Dict[str, Any] = {}
        if scope:
            payload["scope"] = json.dumps(scope)
        if language_code:
            payload["language_code"] = language_code

        result = self._api_call("getMyCommands", payload)
        commands_data = result.get("result", [])
        return [
            TelegramBotCommand(
                command=c.get("command", ""),
                description=c.get("description", ""),
            )
            for c in commands_data
        ]

    def delete_my_commands(self, scope: Optional[Dict[str, Any]] = None,
                           language_code: str = "") -> bool:
        """
        Delete the list of the bot's commands.

        Args:
            scope: Command scope.
            language_code: Language code.

        Returns:
            True if commands were deleted successfully.
        """
        payload: Dict[str, Any] = {}
        if scope:
            payload["scope"] = json.dumps(scope)
        if language_code:
            payload["language_code"] = language_code

        result = self._api_call("deleteMyCommands", payload)
        return result.get("ok", False)

    # ------------------------------------------------------------------
    # Internal API call helper
    # ------------------------------------------------------------------

    def _api_call(self, method: str,
                  params: Optional[Dict[str, Any]] = None,
                  http_method: str = "POST") -> Dict[str, Any]:
        """
        Make a Telegram Bot API call.

        Args:
            method: Telegram API method name.
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

        token = self.config.api_key or self.config.access_token or ""
        url = f"https://api.telegram.org/bot{token}/{method}"
        headers = {
            "Content-Type": "application/json; charset=utf-8",
        }

        body = json.dumps(params or {})
        self._log_request(http_method, url, headers, body)

        try:
            import urllib.request
            import urllib.error

            req = urllib.request.Request(
                url,
                data=body.encode("utf-8") if http_method == "POST" else None,
                headers=headers,
                method=http_method,
            )

            with urllib.request.urlopen(
                req, timeout=self.config.timeout
            ) as response:
                response_body = response.read().decode("utf-8")
                self._log_response(http_method, url, response.status, response_body)
                result = json.loads(response_body)

                if not result.get("ok"):
                    error_code = result.get("error_code", 500)
                    description = result.get("description", "Unknown error")
                    if error_code == 429:
                        retry_after = result.get("parameters", {}).get(
                            "retry_after", 60
                        )
                        self._rate_limit.retry_after = time.time() + retry_after
                        self._rate_limit_hits += 1
                        raise RateLimitError(
                            f"Rate limited: {description}",
                            retry_after=retry_after,
                            adapter_name=self.config.name,
                        )
                    raise self._handle_error(
                        f"Telegram API error: {description}",
                        status_code=error_code,
                        response_body=response_body,
                    )

                return result

        except (IntegrationError, RateLimitError):
            raise
        except urllib.error.HTTPError as e:
            response_body = e.read().decode("utf-8", errors="replace")
            self._log_response(http_method, url, e.code, response_body)
            if e.code == 429:
                self._rate_limit_hits += 1
                raise RateLimitError(
                    "Rate limited by Telegram",
                    retry_after=60,
                    adapter_name=self.config.name,
                )
            raise self._handle_error(
                f"HTTP error {e.code}: {e.reason}",
                status_code=e.code,
                response_body=response_body,
            )
        except Exception as e:
            raise self._handle_error(
                f"Request error: {e}"
            )

    def __repr__(self) -> str:
        return (
            f"<TelegramAdapter name={self.config.name} "
            f"status={self._status.value} "
            f"bot=@{self._bot_info.username if self._bot_info else 'unknown'}>"
        )
