"""
Twilio Integration Adapter for LiuHao AI OS.

Provides integration with Twilio for SMS/MMS sending, WhatsApp messages,
voice calls, phone numbers management, account info, messaging services,
webhooks, and call recordings.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional
from urllib.parse import urlencode, unquote

from .base_adapter import (
    AuthType,
    BaseIntegrationAdapter,
    ConnectionStatus,
    IntegrationConfig,
    IntegrationError,
    IntegrationStatus,
    RateLimitError,
)


class TwilioMessageStatus(str, Enum):
    """Message status values."""
    ACCEPTED = "accepted"
    QUEUED = "queued"
    SENDING = "sending"
    SENT = "sent"
    FAILED = "failed"
    DELIVERED = "delivered"
    UNDELIVERED = "undelivered"
    RECEIVING = "receiving"
    RECEIVED = "received"
    READ = "read"


class TwilioMessageDirection(str, Enum):
    """Message direction types."""
    INBOUND = "inbound"
    OUTBOUND_API = "outbound-api"
    OUTBOUND_CALL = "outbound-call"
    OUTBOUND_REPLY = "outbound-reply"


class TwilioCallStatus(str, Enum):
    """Call status values."""
    QUEUED = "queued"
    RINGING = "ringing"
    IN_PROGRESS = "in-progress"
    COMPLETED = "completed"
    BUSY = "busy"
    FAILED = "failed"
    NO_ANSWER = "no-answer"
    CANCELED = "canceled"


class TwilioPhoneNumberType(str, Enum):
    """Phone number types."""
    LOCAL = "local"
    MOBILE = "mobile"
    TOLL_FREE = "toll-free"
    NATIONAL = "national"


@dataclass
class TwilioMessage:
    """Represents a Twilio SMS/MMS message."""
    sid: str = ""
    account_sid: str = ""
    from_number: str = ""
    to: str = ""
    body: str = ""
    status: TwilioMessageStatus = TwilioMessageStatus.ACCEPTED
    direction: TwilioMessageDirection = TwilioMessageDirection.OUTBOUND_API
    num_segments: int = 0
    num_media: int = 0
    date_created: str = ""
    date_updated: str = ""
    date_sent: str = ""
    error_code: Optional[int] = None
    error_message: str = ""
    price: str = ""
    price_unit: str = ""
    messaging_service_sid: str = ""
    media_url: List[str] = field(default_factory=list)
    uri: str = ""
    extra: Dict[str, Any] = field(default_factory=dict)


@dataclass
class TwilioCall:
    """Represents a Twilio voice call."""
    sid: str = ""
    account_sid: str = ""
    from_number: str = ""
    to: str = ""
    status: TwilioCallStatus = TwilioCallStatus.QUEUED
    direction: str = ""
    parent_call_sid: str = ""
    date_created: str = ""
    date_updated: str = ""
    date_started: str = ""
    date_ended: str = ""
    duration: str = ""
    price: str = ""
    price_unit: str = ""
    caller_name: str = ""
    answered_by: str = ""
    twiml: str = ""
    url: str = ""
    uri: str = ""
    extra: Dict[str, Any] = field(default_factory=dict)


@dataclass
class TwilioPhoneNumber:
    """Represents an incoming phone number."""
    sid: str = ""
    account_sid: str = ""
    friendly_name: str = ""
    phone_number: str = ""
    voice_url: str = ""
    voice_method: str = "POST"
    sms_url: str = ""
    sms_method: str = "POST"
    status_callback: str = ""
    status_callback_method: str = "POST"
    capabilities: Dict[str, bool] = field(default_factory=dict)
    date_created: str = ""
    date_updated: str = ""
    type: TwilioPhoneNumberType = TwilioPhoneNumberType.LOCAL
    extra: Dict[str, Any] = field(default_factory=dict)


@dataclass
class TwilioAccount:
    """Represents a Twilio account."""
    sid: str = ""
    friendly_name: str = ""
    status: str = ""
    owner_account_sid: str = ""
    auth_token: str = ""
    date_created: str = ""
    date_updated: str = ""
    type: str = ""
    balance: str = ""
    currency: str = ""
    extra: Dict[str, Any] = field(default_factory=dict)


@dataclass
class TwilioMessagingService:
    """Represents a Twilio messaging service."""
    sid: str = ""
    friendly_name: str = ""
    date_created: str = ""
    date_updated: str = ""
    status: str = ""
    sticky_sender: bool = False
    mms_converter: bool = False
    smart_encoding: bool = False
    scan_message_content: str = ""
    fallback_method: str = "POST"
    fallback_to_long_code: bool = False
    area_code_geomatch: bool = False
    synchronous_validation: bool = False
    validity_period: int = 0
    extra: Dict[str, Any] = field(default_factory=dict)


@dataclass
class TwilioRecording:
    """Represents a Twilio call recording."""
    sid: str = ""
    account_sid: str = ""
    call_sid: str = ""
    conference_sid: str = ""
    duration: str = ""
    date_created: str = ""
    date_updated: str = ""
    status: str = ""
    price: str = ""
    price_unit: str = ""
    channels: int = 1
    source: str = ""
    error_code: Optional[int] = None
    uri: str = ""
    media_url: str = ""
    extra: Dict[str, Any] = field(default_factory=dict)


class TwilioAdapter(BaseIntegrationAdapter):
    """
    Twilio integration adapter for LiuHao AI OS.

    Supports:
    - Send SMS/MMS messages
    - Send WhatsApp messages
    - Make voice calls
    - Phone numbers management
    - Account info and balance
    - Messaging services
    - Webhook request validation
    - Call recordings
    """

    def __init__(self, config: IntegrationConfig):
        if config.auth_type == AuthType.NONE:
            config.auth_type = AuthType.BASIC_AUTH
        if not config.base_url:
            config.base_url = "https://api.twilio.com/2010-04-01"
        super().__init__(config)
        self._account_sid: Optional[str] = None
        self._account_friendly_name: Optional[str] = None
        self._cached_messages: Dict[str, TwilioMessage] = {}
        self._cached_calls: Dict[str, TwilioCall] = {}
        self._cached_phone_numbers: Dict[str, TwilioPhoneNumber] = {}

    # ------------------------------------------------------------------
    # Base interface
    # ------------------------------------------------------------------

    def connect(self) -> bool:
        self._status = ConnectionStatus.CONNECTING
        self._logger.info("Connecting to Twilio API...")

        try:
            # Account SID is the username for basic auth
            account_sid = self.config.username or ""
            result = self._api_call(f"Accounts/{account_sid}.json")
            self._account_sid = result.get("sid", "")
            self._account_friendly_name = result.get("friendly_name", "")
            self._status = ConnectionStatus.CONNECTED
            self._connected_at = time.time()
            self._logger.info(
                "Connected to Twilio as %s (sid: %s)",
                self._account_friendly_name, self._account_sid,
            )
            return True
        except IntegrationError:
            raise
        except Exception as e:
            self._status = ConnectionStatus.ERROR
            raise self._handle_error(f"Connection error: {e}")

    def disconnect(self) -> bool:
        self._status = ConnectionStatus.DISCONNECTED
        self._account_sid = None
        self._account_friendly_name = None
        self._cached_messages = {}
        self._cached_calls = {}
        self._cached_phone_numbers = {}
        self._session = None
        self._logger.info("Disconnected from Twilio")
        return True

    def is_connected(self) -> bool:
        return self._status == ConnectionStatus.CONNECTED

    def get_status(self) -> IntegrationStatus:
        return self._build_status({
            "account_sid": self._account_sid,
            "account_name": self._account_friendly_name,
            "cached_messages": len(self._cached_messages),
            "cached_calls": len(self._cached_calls),
            "cached_phone_numbers": len(self._cached_phone_numbers),
        })

    def test_connection(self) -> bool:
        try:
            if not self.is_connected():
                return False
            account_sid = self.config.username or self._account_sid or ""
            result = self._api_call(f"Accounts/{account_sid}.json")
            return "sid" in result
        except Exception:
            return False

    # ------------------------------------------------------------------
    # SMS/MMS messages
    # ------------------------------------------------------------------

    def send_message(self, to: str,
                     from_number: str,
                     body: str = "",
                     media_url: Optional[List[str]] = None,
                     messaging_service_sid: str = "",
                     status_callback: str = "",
                     **kwargs: Any) -> TwilioMessage:
        data: Dict[str, Any] = {
            "To": to,
        }
        if messaging_service_sid:
            data["MessagingServiceSid"] = messaging_service_sid
        else:
            data["From"] = from_number
        if body:
            data["Body"] = body
        if media_url:
            for i, url in enumerate(media_url):
                data[f"MediaUrl[{i}]"] = url
        if status_callback:
            data["StatusCallback"] = status_callback
        data.update(kwargs)

        account_sid = self._account_sid or self.config.username or ""
        result = self._api_call(
            f"Accounts/{account_sid}/Messages.json",
            data=data,
            http_method="POST",
        )
        message = self._parse_message(result)
        if message.sid:
            self._cached_messages[message.sid] = message
        return message

    def send_sms(self, to: str, from_number: str,
                 body: str, **kwargs: Any) -> TwilioMessage:
        return self.send_message(to, from_number, body=body, **kwargs)

    def send_mms(self, to: str, from_number: str,
                 body: str = "",
                 media_url: List[str] = None,
                 **kwargs: Any) -> TwilioMessage:
        return self.send_message(
            to, from_number, body=body, media_url=media_url or [], **kwargs
        )

    def get_message(self, message_sid: str) -> TwilioMessage:
        if message_sid in self._cached_messages:
            return self._cached_messages[message_sid]

        account_sid = self._account_sid or self.config.username or ""
        result = self._api_call(
            f"Accounts/{account_sid}/Messages/{message_sid}.json"
        )
        message = self._parse_message(result)
        if message.sid:
            self._cached_messages[message.sid] = message
        return message

    def list_messages(self, to: str = "",
                      from_number: str = "",
                      date_sent: str = "",
                      page_size: int = 50,
                      **kwargs: Any) -> Dict[str, Any]:
        account_sid = self._account_sid or self.config.username or ""
        params: Dict[str, Any] = {"PageSize": page_size}
        if to:
            params["To"] = to
        if from_number:
            params["From"] = from_number
        if date_sent:
            params["DateSent"] = date_sent
        params.update(kwargs)

        result = self._api_call(
            f"Accounts/{account_sid}/Messages.json", params=params
        )
        messages_data = result.get("messages", [])
        messages = [self._parse_message(m) for m in messages_data]
        for msg in messages:
            if msg.sid:
                self._cached_messages[msg.sid] = msg

        return {
            "messages": messages,
            "next_page_uri": result.get("next_page_uri", ""),
            "previous_page_uri": result.get("previous_page_uri", ""),
            "page": result.get("page", 0),
            "page_size": result.get("page_size", 0),
            "end": result.get("end", 0),
        }

    def delete_message(self, message_sid: str) -> bool:
        account_sid = self._account_sid or self.config.username or ""
        self._api_call(
            f"Accounts/{account_sid}/Messages/{message_sid}.json",
            http_method="DELETE",
        )
        if message_sid in self._cached_messages:
            del self._cached_messages[message_sid]
        return True

    def _parse_message(self, data: Dict[str, Any]) -> TwilioMessage:
        media_list = data.get("subresource_uris", {}).get("media", "")
        return TwilioMessage(
            sid=data.get("sid", ""),
            account_sid=data.get("account_sid", ""),
            from_number=data.get("from", ""),
            to=data.get("to", ""),
            body=data.get("body", ""),
            status=TwilioMessageStatus(data.get("status", "accepted")),
            direction=TwilioMessageDirection(data.get("direction", "outbound-api")),
            num_segments=int(data.get("num_segments", 0)) if data.get("num_segments") else 0,
            num_media=int(data.get("num_media", 0)) if data.get("num_media") else 0,
            date_created=data.get("date_created", ""),
            date_updated=data.get("date_updated", ""),
            date_sent=data.get("date_sent", ""),
            error_code=data.get("error_code"),
            error_message=data.get("error_message", ""),
            price=data.get("price", ""),
            price_unit=data.get("price_unit", ""),
            messaging_service_sid=data.get("messaging_service_sid", ""),
            uri=data.get("uri", ""),
            media_url=[media_list] if media_list else [],
            extra=data,
        )

    # ------------------------------------------------------------------
    # WhatsApp messages
    # ------------------------------------------------------------------

    def send_whatsapp_message(self, to: str,
                              from_number: str,
                              body: str = "",
                              media_url: Optional[List[str]] = None,
                              **kwargs: Any) -> TwilioMessage:
        """
        Send a WhatsApp message via Twilio.

        Args:
            to: Recipient WhatsApp number (e.g., whatsapp:+1234567890).
            from_number: Sender WhatsApp number (e.g., whatsapp:+1234567890).
            body: Message text.
            media_url: List of media URLs for MMS-style attachments.
            **kwargs: Additional parameters.

        Returns:
            TwilioMessage with message details.
        """
        if not to.startswith("whatsapp:"):
            to = f"whatsapp:{to}"
        if not from_number.startswith("whatsapp:"):
            from_number = f"whatsapp:{from_number}"

        return self.send_message(
            to, from_number, body=body, media_url=media_url, **kwargs
        )

    # ------------------------------------------------------------------
    # Voice calls
    # ------------------------------------------------------------------

    def make_call(self, to: str,
                  from_number: str,
                  url: str = "",
                  twiml: str = "",
                  method: str = "POST",
                  status_callback: str = "",
                  status_callback_method: str = "POST",
                  timeout: int = 60,
                  **kwargs: Any) -> TwilioCall:
        data: Dict[str, Any] = {
            "To": to,
            "From": from_number,
            "Timeout": timeout,
        }
        if url:
            data["Url"] = url
            data["Method"] = method
        if twiml:
            data["Twiml"] = twiml
        if status_callback:
            data["StatusCallback"] = status_callback
            data["StatusCallbackMethod"] = status_callback_method
        data.update(kwargs)

        account_sid = self._account_sid or self.config.username or ""
        result = self._api_call(
            f"Accounts/{account_sid}/Calls.json",
            data=data,
            http_method="POST",
        )
        call = self._parse_call(result)
        if call.sid:
            self._cached_calls[call.sid] = call
        return call

    def get_call(self, call_sid: str) -> TwilioCall:
        if call_sid in self._cached_calls:
            return self._cached_calls[call_sid]

        account_sid = self._account_sid or self.config.username or ""
        result = self._api_call(
            f"Accounts/{account_sid}/Calls/{call_sid}.json"
        )
        call = self._parse_call(result)
        if call.sid:
            self._cached_calls[call.sid] = call
        return call

    def list_calls(self, to: str = "",
                   from_number: str = "",
                   status: str = "",
                   page_size: int = 50,
                   **kwargs: Any) -> Dict[str, Any]:
        account_sid = self._account_sid or self.config.username or ""
        params: Dict[str, Any] = {"PageSize": page_size}
        if to:
            params["To"] = to
        if from_number:
            params["From"] = from_number
        if status:
            params["Status"] = status
        params.update(kwargs)

        result = self._api_call(
            f"Accounts/{account_sid}/Calls.json", params=params
        )
        calls_data = result.get("calls", [])
        calls = [self._parse_call(c) for c in calls_data]
        for call in calls:
            if call.sid:
                self._cached_calls[call.sid] = call

        return {
            "calls": calls,
            "next_page_uri": result.get("next_page_uri", ""),
            "previous_page_uri": result.get("previous_page_uri", ""),
            "page": result.get("page", 0),
            "page_size": result.get("page_size", 0),
            "end": result.get("end", 0),
        }

    def end_call(self, call_sid: str) -> TwilioCall:
        account_sid = self._account_sid or self.config.username or ""
        result = self._api_call(
            f"Accounts/{account_sid}/Calls/{call_sid}.json",
            data={"Status": "completed"},
            http_method="POST",
        )
        call = self._parse_call(result)
        if call.sid:
            self._cached_calls[call.sid] = call
        return call

    def _parse_call(self, data: Dict[str, Any]) -> TwilioCall:
        return TwilioCall(
            sid=data.get("sid", ""),
            account_sid=data.get("account_sid", ""),
            from_number=data.get("from", ""),
            to=data.get("to", ""),
            status=TwilioCallStatus(data.get("status", "queued")),
            direction=data.get("direction", ""),
            parent_call_sid=data.get("parent_call_sid", ""),
            date_created=data.get("date_created", ""),
            date_updated=data.get("date_updated", ""),
            date_started=data.get("start_time", ""),
            date_ended=data.get("end_time", ""),
            duration=data.get("duration", ""),
            price=data.get("price", ""),
            price_unit=data.get("price_unit", ""),
            caller_name=data.get("caller_name", ""),
            answered_by=data.get("answered_by", ""),
            uri=data.get("uri", ""),
            extra=data,
        )

    # ------------------------------------------------------------------
    # Phone numbers
    # ------------------------------------------------------------------

    def list_incoming_phone_numbers(self, friendly_name: str = "",
                                    phone_number: str = "",
                                    page_size: int = 50,
                                    **kwargs: Any) -> Dict[str, Any]:
        account_sid = self._account_sid or self.config.username or ""
        params: Dict[str, Any] = {"PageSize": page_size}
        if friendly_name:
            params["FriendlyName"] = friendly_name
        if phone_number:
            params["PhoneNumber"] = phone_number
        params.update(kwargs)

        result = self._api_call(
            f"Accounts/{account_sid}/IncomingPhoneNumbers.json",
            params=params,
        )
        numbers_data = result.get("incoming_phone_numbers", [])
        numbers = [self._parse_phone_number(n) for n in numbers_data]
        for num in numbers:
            if num.sid:
                self._cached_phone_numbers[num.sid] = num

        return {
            "phone_numbers": numbers,
            "next_page_uri": result.get("next_page_uri", ""),
            "page": result.get("page", 0),
            "page_size": result.get("page_size", 0),
        }

    def get_incoming_phone_number(self, sid: str) -> TwilioPhoneNumber:
        if sid in self._cached_phone_numbers:
            return self._cached_phone_numbers[sid]

        account_sid = self._account_sid or self.config.username or ""
        result = self._api_call(
            f"Accounts/{account_sid}/IncomingPhoneNumbers/{sid}.json"
        )
        number = self._parse_phone_number(result)
        if number.sid:
            self._cached_phone_numbers[number.sid] = number
        return number

    def update_incoming_phone_number(self, sid: str,
                                     friendly_name: Optional[str] = None,
                                     voice_url: Optional[str] = None,
                                     sms_url: Optional[str] = None,
                                     status_callback: Optional[str] = None,
                                     **kwargs: Any) -> TwilioPhoneNumber:
        data: Dict[str, Any] = {}
        if friendly_name is not None:
            data["FriendlyName"] = friendly_name
        if voice_url is not None:
            data["VoiceUrl"] = voice_url
        if sms_url is not None:
            data["SmsUrl"] = sms_url
        if status_callback is not None:
            data["StatusCallback"] = status_callback
        data.update(kwargs)

        account_sid = self._account_sid or self.config.username or ""
        result = self._api_call(
            f"Accounts/{account_sid}/IncomingPhoneNumbers/{sid}.json",
            data=data,
            http_method="POST",
        )
        number = self._parse_phone_number(result)
        if number.sid:
            self._cached_phone_numbers[number.sid] = number
        return number

    def _parse_phone_number(self, data: Dict[str, Any]) -> TwilioPhoneNumber:
        cap = data.get("capabilities", {})
        return TwilioPhoneNumber(
            sid=data.get("sid", ""),
            account_sid=data.get("account_sid", ""),
            friendly_name=data.get("friendly_name", ""),
            phone_number=data.get("phone_number", ""),
            voice_url=data.get("voice_url", ""),
            voice_method=data.get("voice_method", "POST"),
            sms_url=data.get("sms_url", ""),
            sms_method=data.get("sms_method", "POST"),
            status_callback=data.get("status_callback", ""),
            capabilities={
                "voice": cap.get("voice", False),
                "sms": cap.get("SMS", False),
                "mms": cap.get("MMS", False),
                "fax": cap.get("fax", False),
            },
            date_created=data.get("date_created", ""),
            date_updated=data.get("date_updated", ""),
            type=TwilioPhoneNumberType(data.get("type", "local")),
            extra=data,
        )

    def search_available_phone_numbers(self, country_code: str,
                                       area_code: str = "",
                                       contains: str = "",
                                       sms_enabled: bool = False,
                                       voice_enabled: bool = False,
                                       mms_enabled: bool = False,
                                       page_size: int = 30,
                                       **kwargs: Any) -> List[Dict[str, Any]]:
        account_sid = self._account_sid or self.config.username or ""
        params: Dict[str, Any] = {"PageSize": page_size}
        if area_code:
            params["AreaCode"] = area_code
        if contains:
            params["Contains"] = contains
        if sms_enabled:
            params["SmsEnabled"] = "true"
        if voice_enabled:
            params["VoiceEnabled"] = "true"
        if mms_enabled:
            params["MmsEnabled"] = "true"
        params.update(kwargs)

        result = self._api_call(
            f"Accounts/{account_sid}/AvailablePhoneNumbers/"
            f"{country_code}/Local.json",
            params=params,
        )
        return result.get("available_phone_numbers", [])

    # ------------------------------------------------------------------
    # Account info
    # ------------------------------------------------------------------

    def get_account(self) -> TwilioAccount:
        account_sid = self._account_sid or self.config.username or ""
        result = self._api_call(f"Accounts/{account_sid}.json")
        return TwilioAccount(
            sid=result.get("sid", ""),
            friendly_name=result.get("friendly_name", ""),
            status=result.get("status", ""),
            owner_account_sid=result.get("owner_account_sid", ""),
            date_created=result.get("date_created", ""),
            date_updated=result.get("date_updated", ""),
            type=result.get("type", ""),
            extra=result,
        )

    def get_balance(self) -> Dict[str, Any]:
        account_sid = self._account_sid or self.config.username or ""
        result = self._api_call(f"Accounts/{account_sid}/Balance.json")
        return result

    # ------------------------------------------------------------------
    # Messaging services
    # ------------------------------------------------------------------

    def list_messaging_services(self, page_size: int = 50,
                                **kwargs: Any) -> Dict[str, Any]:
        account_sid = self._account_sid or self.config.username or ""
        params: Dict[str, Any] = {"PageSize": page_size}
        params.update(kwargs)

        result = self._api_call(
            f"Accounts/{account_sid}/Services.json", params=params
        )
        services_data = result.get("services", [])
        return {
            "services": [
                TwilioMessagingService(
                    sid=s.get("sid", ""),
                    friendly_name=s.get("friendly_name", ""),
                    date_created=s.get("date_created", ""),
                    date_updated=s.get("date_updated", ""),
                    status=s.get("status", ""),
                    sticky_sender=s.get("sticky_sender", False),
                    mms_converter=s.get("mms_converter", False),
                    smart_encoding=s.get("smart_encoding", False),
                    validity_period=s.get("validity_period", 0),
                    extra=s,
                )
                for s in services_data
            ],
            "next_page_uri": result.get("next_page_uri", ""),
            "page": result.get("page", 0),
            "page_size": result.get("page_size", 0),
        }

    def create_messaging_service(self, friendly_name: str,
                                 **kwargs: Any) -> TwilioMessagingService:
        account_sid = self._account_sid or self.config.username or ""
        data: Dict[str, Any] = {"FriendlyName": friendly_name}
        data.update(kwargs)

        result = self._api_call(
            f"Accounts/{account_sid}/Services.json",
            data=data,
            http_method="POST",
        )
        return TwilioMessagingService(
            sid=result.get("sid", ""),
            friendly_name=result.get("friendly_name", ""),
            date_created=result.get("date_created", ""),
            date_updated=result.get("date_updated", ""),
            status=result.get("status", ""),
            extra=result,
        )

    # ------------------------------------------------------------------
    # Call recordings
    # ------------------------------------------------------------------

    def list_recordings(self, call_sid: str = "",
                        page_size: int = 50,
                        **kwargs: Any) -> Dict[str, Any]:
        account_sid = self._account_sid or self.config.username or ""
        params: Dict[str, Any] = {"PageSize": page_size}
        params.update(kwargs)

        if call_sid:
            path = (
                f"Accounts/{account_sid}/Calls/{call_sid}/Recordings.json"
            )
        else:
            path = f"Accounts/{account_sid}/Recordings.json"

        result = self._api_call(path, params=params)
        recordings_data = result.get("recordings", [])
        return {
            "recordings": [
                TwilioRecording(
                    sid=r.get("sid", ""),
                    account_sid=r.get("account_sid", ""),
                    call_sid=r.get("call_sid", ""),
                    conference_sid=r.get("conference_sid", ""),
                    duration=r.get("duration", ""),
                    date_created=r.get("date_created", ""),
                    date_updated=r.get("date_updated", ""),
                    status=r.get("status", ""),
                    price=r.get("price", ""),
                    price_unit=r.get("price_unit", ""),
                    channels=r.get("channels", 1),
                    source=r.get("source", ""),
                    error_code=r.get("error_code"),
                    uri=r.get("uri", ""),
                    media_url=(
                        f"https://api.twilio.com/2010-04-01/Accounts/"
                        f"{account_sid}/Recordings/{r.get('sid', '')}.mp3"
                    ),
                    extra=r,
                )
                for r in recordings_data
            ],
            "next_page_uri": result.get("next_page_uri", ""),
            "page": result.get("page", 0),
            "page_size": result.get("page_size", 0),
        }

    def delete_recording(self, recording_sid: str) -> bool:
        account_sid = self._account_sid or self.config.username or ""
        self._api_call(
            f"Accounts/{account_sid}/Recordings/{recording_sid}.json",
            http_method="DELETE",
        )
        return True

    # ------------------------------------------------------------------
    # Webhook verification
    # ------------------------------------------------------------------

    def verify_webhook(self, request_url: str,
                       request_params: Dict[str, str],
                       x_twilio_signature: str,
                       auth_token: str = "") -> bool:
        """
        Verify a Twilio webhook request signature.

        Args:
            request_url: Full request URL.
            request_params: Dictionary of POST parameters.
            x_twilio_signature: X-Twilio-Signature header value.
            auth_token: Twilio auth token.

        Returns:
            True if the webhook is verified.
        """
        token = auth_token or self.config.password or ""
        if not token:
            return False

        # Build the validation string: URL + sorted params concatenated
        sorted_params = sorted(request_params.items())
        validation_string = request_url
        for key, value in sorted_params:
            validation_string += key + value

        expected = base64.b64encode(
            hmac.new(
                token.encode("utf-8"),
                validation_string.encode("utf-8"),
                hashlib.sha1,
            ).digest()
        ).decode("utf-8")

        return hmac.compare_digest(x_twilio_signature, expected)

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
            url = f"{url}?{urlencode(params)}"

        # Twilio uses basic auth: Account SID as username, Auth Token as password
        username = self.config.username or ""
        password = self.config.password or self.config.api_key or ""
        credentials = f"{username}:{password}".encode("utf-8")
        encoded = base64.b64encode(credentials).decode("utf-8")

        headers = {
            "Authorization": f"Basic {encoded}",
            "Accept": "application/json",
        }

        body = None
        if data:
            body = urlencode(data).encode("utf-8")
            headers["Content-Type"] = "application/x-www-form-urlencoded"

        self._log_request(http_method, url, headers, body.decode("utf-8") if body else None)

        try:
            import urllib.request
            import urllib.error

            req = urllib.request.Request(
                url,
                data=body,
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
                    "Rate limited by Twilio",
                    retry_after=60,
                    adapter_name=self.config.name,
                )
            if e.code in (401, 403):
                raise self._handle_error(
                    "Authentication failed: invalid Twilio credentials",
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
            f"<TwilioAdapter name={self.config.name} "
            f"status={self._status.value} "
            f"account={self._account_friendly_name or 'unknown'}>"
        )
