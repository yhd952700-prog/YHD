"""
Dropbox Integration Adapter for LiuHao AI OS.

Provides integration with Dropbox API for file upload/download, file/folder
listing, file metadata, sharing, search, revisions, users and teams,
and Paper docs.
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


class DropboxWriteMode(str, Enum):
    """File write modes."""
    ADD = "add"
    OVERWRITE = "overwrite"
    UPDATE = "update"


class DropboxFileStatus(str, Enum):
    """File status values."""
    ACTIVE = "active"
    DELETED = "deleted"


class DropboxAccessLevel(str, Enum):
    """Shared link access levels."""
    VIEWER = "viewer"
    EDITOR = "editor"
    OWNER = "owner"
    VIEWER_NO_COMMENT = "viewer_no_comment"


class DropboxRequestedVisibility(str, Enum):
    """Shared link visibility types."""
    PUBLIC = "public"
    TEAM_ONLY = "team_only"
    PASSWORD = "password"
    TEAM_AND_PASSWORD = "team_and_password"
    SHARED_FOLDER_ONLY = "shared_folder_only"


@dataclass
class DropboxFileMetadata:
    """Represents a Dropbox file or folder metadata."""
    id: str = ""
    name: str = ""
    path_lower: str = ""
    path_display: str = ""
    size: int = 0
    server_modified: str = ""
    client_modified: str = ""
    rev: str = ""
    content_hash: str = ""
    is_downloadable: bool = True
    has_explicit_shared_members: bool = False
    media_info: Optional[Dict[str, Any]] = None
    sharing_info: Optional[Dict[str, Any]] = None
    is_deleted: bool = False
    is_folder: bool = False
    is_file: bool = True
    extra: Dict[str, Any] = field(default_factory=dict)


@dataclass
class DropboxSharedLink:
    """Represents a Dropbox shared link."""
    url: str = ""
    name: str = ""
    path_lower: str = ""
    link_permissions: Dict[str, Any] = field(default_factory=dict)
    client_modified: str = ""
    server_modified: str = ""
    size: int = 0
    expires: str = ""
    id: str = ""
    extra: Dict[str, Any] = field(default_factory=dict)


@dataclass
class DropboxFileRevision:
    """Represents a Dropbox file revision."""
    id: str = ""
    name: str = ""
    size: int = 0
    server_modified: str = ""
    client_modified: str = ""
    rev: str = ""
    is_downloadable: bool = True
    extra: Dict[str, Any] = field(default_factory=dict)


@dataclass
class DropboxUser:
    """Represents a Dropbox user."""
    account_id: str = ""
    name: Dict[str, Any] = field(default_factory=dict)
    email: str = ""
    email_verified: bool = False
    profile_photo_url: str = ""
    country: str = ""
    locale: str = ""
    team_member_id: str = ""
    is_paired: bool = False
    account_type: Dict[str, Any] = field(default_factory=dict)
    extra: Dict[str, Any] = field(default_factory=dict)


@dataclass
class DropboxTeam:
    """Represents a Dropbox team."""
    id: str = ""
    name: str = ""
    num_teams: int = 0
    num_users: int = 0
    extra: Dict[str, Any] = field(default_factory=dict)


@dataclass
class DropboxPaperDoc:
    """Represents a Dropbox Paper doc."""
    doc_id: str = ""
    owner: str = ""
    title: str = ""
    created_date: str = ""
    status: str = ""
    folder_id: str = ""
    revision: int = 0
    last_editor: str = ""
    last_modified_date: str = ""
    extra: Dict[str, Any] = field(default_factory=dict)


@dataclass
class DropboxSearchResult:
    """Represents a Dropbox search result entry."""
    metadata: DropboxFileMetadata
    match_type: str = ""
    highlights: List[Dict[str, Any]] = field(default_factory=list)


class DropboxAdapter(BaseIntegrationAdapter):
    """
    Dropbox integration adapter for LiuHao AI OS.

    Supports:
    - File upload and download
    - File/folder listing
    - File metadata
    - Sharing (shared links, shared folders)
    - Search
    - Revisions
    - Users and teams
    - Paper docs
    """

    def __init__(self, config: IntegrationConfig):
        if config.auth_type == AuthType.NONE:
            config.auth_type = AuthType.BEARER_TOKEN
        if not config.base_url:
            config.base_url = "https://api.dropboxapi.com/2"
        super().__init__(config)
        self._user_account_id: Optional[str] = None
        self._user_email: Optional[str] = None
        self._user_name: Optional[str] = None
        self._team_id: Optional[str] = None
        self._team_name: Optional[str] = None
        self._content_url = "https://content.dropboxapi.com/2"
        self._cached_metadata: Dict[str, DropboxFileMetadata] = {}

    # ------------------------------------------------------------------
    # Base interface
    # ------------------------------------------------------------------

    def connect(self) -> bool:
        self._status = ConnectionStatus.CONNECTING
        self._logger.info("Connecting to Dropbox API...")

        try:
            result = self._api_call("users/get_current_account")
            self._user_account_id = result.get("account_id", "")
            self._user_email = result.get("email", "")
            name = result.get("name", {})
            self._user_name = name.get("display_name", "")

            # Check for team info
            team = result.get("team")
            if team:
                self._team_id = team.get("id", "")
                self._team_name = team.get("name", "")

            self._status = ConnectionStatus.CONNECTED
            self._connected_at = time.time()
            self._logger.info(
                "Connected to Dropbox as %s (%s)",
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
        self._user_account_id = None
        self._user_email = None
        self._user_name = None
        self._team_id = None
        self._team_name = None
        self._cached_metadata = {}
        self._session = None
        self._logger.info("Disconnected from Dropbox")
        return True

    def is_connected(self) -> bool:
        return self._status == ConnectionStatus.CONNECTED

    def get_status(self) -> IntegrationStatus:
        return self._build_status({
            "user_name": self._user_name,
            "user_email": self._user_email,
            "user_account_id": self._user_account_id,
            "team_id": self._team_id,
            "team_name": self._team_name,
            "cached_metadata": len(self._cached_metadata),
        })

    def test_connection(self) -> bool:
        try:
            if not self.is_connected():
                return False
            result = self._api_call("users/get_current_account")
            return "account_id" in result
        except Exception:
            return False

    # ------------------------------------------------------------------
    # File metadata
    # ------------------------------------------------------------------

    def get_metadata(self, path: str,
                     include_media_info: bool = False,
                     include_deleted: bool = False,
                     include_has_explicit_shared_members: bool = False
                     ) -> DropboxFileMetadata:
        data: Dict[str, Any] = {
            "path": path,
            "include_media_info": include_media_info,
            "include_deleted": include_deleted,
            "include_has_explicit_shared_members": include_has_explicit_shared_members,
        }
        result = self._api_call(
            "files/get_metadata", data=data, http_method="POST"
        )
        metadata = self._parse_metadata(result)
        if metadata.id:
            self._cached_metadata[path] = metadata
        return metadata

    def _parse_metadata(self, data: Dict[str, Any]) -> DropboxFileMetadata:
        is_folder = data.get(".tag", "") == "folder"
        is_file = data.get(".tag", "") == "file"
        return DropboxFileMetadata(
            id=data.get("id", ""),
            name=data.get("name", ""),
            path_lower=data.get("path_lower", ""),
            path_display=data.get("path_display", ""),
            size=data.get("size", 0),
            server_modified=data.get("server_modified", ""),
            client_modified=data.get("client_modified", ""),
            rev=data.get("rev", ""),
            content_hash=data.get("content_hash", ""),
            is_downloadable=data.get("is_downloadable", True),
            has_explicit_shared_members=data.get(
                "has_explicit_shared_members", False
            ),
            media_info=data.get("media_info"),
            sharing_info=data.get("sharing_info"),
            is_deleted=data.get(".tag", "") == "deleted",
            is_folder=is_folder,
            is_file=is_file,
            extra=data,
        )

    # ------------------------------------------------------------------
    # File upload/download
    # ------------------------------------------------------------------

    def upload_file(self, path: str,
                    file_data: bytes,
                    mode: DropboxWriteMode = DropboxWriteMode.ADD,
                    autorename: bool = True,
                    mute: bool = False,
                    strict_conflict: bool = False) -> DropboxFileMetadata:
        self._check_rate_limit()
        self._mark_request()

        url = f"{self._content_url}/files/upload"
        api_args = {
            "path": path,
            "mode": mode.value,
            "autorename": autorename,
            "mute": mute,
            "strict_conflict": strict_conflict,
        }
        headers = {
            "Authorization": f"Bearer {self.config.access_token or self.config.api_key or ''}",
            "Content-Type": "application/octet-stream",
            "Dropbox-API-Arg": json.dumps(api_args),
        }

        self._log_request(
            "POST", url, headers,
            f"<binary {len(file_data)} bytes>",
        )

        try:
            import urllib.request
            import urllib.error

            req = urllib.request.Request(
                url, data=file_data, headers=headers, method="POST"
            )
            with urllib.request.urlopen(req, timeout=self.config.timeout) as r:
                response_body = r.read().decode("utf-8")
                self._log_response("POST", url, r.status, response_body)
                result = json.loads(response_body)
                metadata = self._parse_metadata(result)
                if metadata.id:
                    self._cached_metadata[path] = metadata
                return metadata

        except urllib.error.HTTPError as e:
            response_body = e.read().decode("utf-8", errors="replace")
            self._log_response("POST", url, e.code, response_body)
            if e.code == 429:
                self._rate_limit_hits += 1
                raise RateLimitError(
                    "Rate limited by Dropbox",
                    retry_after=60,
                    adapter_name=self.config.name,
                )
            raise self._handle_error(
                f"HTTP error {e.code}: {e.reason}",
                status_code=e.code,
                response_body=response_body,
            )
        except Exception as e:
            raise self._handle_error(f"Upload error: {e}")

    def upload_file_from_path(self, local_path: str,
                              dropbox_path: str,
                              **kwargs: Any) -> DropboxFileMetadata:
        with open(local_path, "rb") as f:
            file_data = f.read()
        return self.upload_file(dropbox_path, file_data, **kwargs)

    def download_file(self, path: str,
                      rev: str = "") -> bytes:
        self._check_rate_limit()
        self._mark_request()

        url = f"{self._content_url}/files/download"
        api_args: Dict[str, Any] = {"path": path}
        if rev:
            api_args["rev"] = rev

        headers = {
            "Authorization": f"Bearer {self.config.access_token or self.config.api_key or ''}",
            "Dropbox-API-Arg": json.dumps(api_args),
        }

        self._log_request("POST", url, headers, "<binary download>")

        try:
            import urllib.request
            import urllib.error

            req = urllib.request.Request(
                url, data=b"", headers=headers, method="POST"
            )
            with urllib.request.urlopen(req, timeout=self.config.timeout) as r:
                data = r.read()
                # Metadata is in the response header
                meta_header = r.headers.get("Dropbox-API-Result", "{}")
                metadata = json.loads(meta_header)
                self._log_response(
                    "POST", url, r.status,
                    f"<binary {len(data)} bytes>"
                )
                if metadata.get("name"):
                    meta = self._parse_metadata(metadata)
                    if meta.id:
                        self._cached_metadata[path] = meta
                return data

        except urllib.error.HTTPError as e:
            response_body = e.read().decode("utf-8", errors="replace")
            self._log_response("POST", url, e.code, response_body)
            if e.code == 429:
                self._rate_limit_hits += 1
                raise RateLimitError(
                    "Rate limited by Dropbox",
                    retry_after=60,
                    adapter_name=self.config.name,
                )
            raise self._handle_error(
                f"HTTP error {e.code}: {e.reason}",
                status_code=e.code,
                response_body=response_body,
            )
        except Exception as e:
            raise self._handle_error(f"Download error: {e}")

    def download_file_to_path(self, dropbox_path: str,
                              local_path: str,
                              **kwargs: Any) -> DropboxFileMetadata:
        data = self.download_file(dropbox_path, **kwargs)
        with open(local_path, "wb") as f:
            f.write(data)
        return self.get_metadata(dropbox_path)

    # ------------------------------------------------------------------
    # File/folder listing
    # ------------------------------------------------------------------

    def list_folder(self, path: str = "",
                    recursive: bool = False,
                    include_media_info: bool = False,
                    include_deleted: bool = False,
                    include_has_explicit_shared_members: bool = False,
                    include_mounted_folders: bool = True,
                    limit: int = 2000,
                    **kwargs: Any) -> Dict[str, Any]:
        data: Dict[str, Any] = {
            "path": path,
            "recursive": recursive,
            "include_media_info": include_media_info,
            "include_deleted": include_deleted,
            "include_has_explicit_shared_members": include_has_explicit_shared_members,
            "include_mounted_folders": include_mounted_folders,
            "limit": limit,
        }
        data.update(kwargs)

        result = self._api_call(
            "files/list_folder", data=data, http_method="POST"
        )
        entries_data = result.get("entries", [])
        entries = [self._parse_metadata(e) for e in entries_data]
        for entry in entries:
            if entry.path_lower:
                self._cached_metadata[entry.path_lower] = entry

        return {
            "entries": entries,
            "cursor": result.get("cursor", ""),
            "has_more": result.get("has_more", False),
        }

    def list_folder_continue(self, cursor: str) -> Dict[str, Any]:
        result = self._api_call(
            "files/list_folder/continue",
            data={"cursor": cursor},
            http_method="POST",
        )
        entries_data = result.get("entries", [])
        entries = [self._parse_metadata(e) for e in entries_data]
        return {
            "entries": entries,
            "cursor": result.get("cursor", ""),
            "has_more": result.get("has_more", False),
        }

    def list_folder_longpoll(self, cursor: str,
                             timeout: int = 30) -> Dict[str, Any]:
        result = self._api_call(
            "files/list_folder/longpoll",
            data={"cursor": cursor, "timeout": timeout},
            http_method="POST",
        )
        return result

    # ------------------------------------------------------------------
    # Sharing
    # ------------------------------------------------------------------

    def create_shared_link(self, path: str,
                           settings: Optional[Dict[str, Any]] = None
                           ) -> DropboxSharedLink:
        data: Dict[str, Any] = {"path": path}
        if settings:
            data["settings"] = settings

        result = self._api_call(
            "sharing/create_shared_link_with_settings",
            data=data,
            http_method="POST",
        )
        return self._parse_shared_link(result)

    def get_shared_links(self, path: str = "",
                         direct_only: bool = False
                         ) -> List[DropboxSharedLink]:
        data: Dict[str, Any] = {
            "path": path,
            "direct_only": direct_only,
        }
        result = self._api_call(
            "sharing/get_shared_links", data=data, http_method="POST"
        )
        links_data = result.get("links", [])
        return [self._parse_shared_link(link) for link in links_data]

    def modify_shared_link(self, url: str,
                           settings: Dict[str, Any]) -> DropboxSharedLink:
        data: Dict[str, Any] = {
            "url": url,
            "settings": settings,
        }
        result = self._api_call(
            "sharing/modify_shared_link_settings",
            data=data,
            http_method="POST",
        )
        return self._parse_shared_link(result)

    def revoke_shared_link(self, url: str) -> bool:
        self._api_call(
            "sharing/revoke_shared_link",
            data={"url": url},
            http_method="POST",
        )
        return True

    def _parse_shared_link(self, data: Dict[str, Any]) -> DropboxSharedLink:
        permissions = data.get("link_permissions", {})
        return DropboxSharedLink(
            url=data.get("url", ""),
            name=data.get("name", ""),
            path_lower=data.get("path_lower", ""),
            link_permissions=permissions if permissions else {},
            client_modified=data.get("client_modified", ""),
            server_modified=data.get("server_modified", ""),
            size=data.get("size", 0),
            expires=data.get("expires", ""),
            id=data.get("id", ""),
            extra=data,
        )

    def share_folder(self, path: str,
                     force_async: bool = False,
                     **kwargs: Any) -> Dict[str, Any]:
        data: Dict[str, Any] = {"path": path, "force_async": force_async}
        data.update(kwargs)
        return self._api_call(
            "sharing/share_folder", data=data, http_method="POST"
        )

    def list_shared_folders(self, limit: int = 1000,
                            **kwargs: Any) -> Dict[str, Any]:
        data: Dict[str, Any] = {"limit": limit}
        data.update(kwargs)
        return self._api_call(
            "sharing/list_folders", data=data, http_method="POST"
        )

    # ------------------------------------------------------------------
    # Search
    # ------------------------------------------------------------------

    def search(self, query: str,
               path: str = "",
               max_results: int = 100,
               file_status: DropboxFileStatus = DropboxFileStatus.ACTIVE,
               **kwargs: Any) -> Dict[str, Any]:
        data: Dict[str, Any] = {
            "query": query,
            "max_results": max_results,
        }
        options: Dict[str, Any] = {}
        if path:
            options["path"] = path
        options["file_status"] = file_status.value
        if options:
            data["options"] = options
        data.update(kwargs)

        result = self._api_call(
            "files/search_v2", data=data, http_method="POST"
        )
        matches = result.get("matches", [])
        search_results = []
        for m in matches:
            meta_data = m.get("metadata", {})
            file_meta = meta_data.get("metadata", meta_data)
            metadata = self._parse_metadata(file_meta)
            search_results.append(DropboxSearchResult(
                metadata=metadata,
                match_type=m.get(".tag", ""),
                highlights=m.get("highlights", []),
            ))

        return {
            "matches": search_results,
            "has_more": result.get("has_more", False),
            "cursor": result.get("cursor"),
        }

    # ------------------------------------------------------------------
    # Revisions
    # ------------------------------------------------------------------

    def list_revisions(self, path: str,
                       limit: int = 10,
                       mode: str = "path") -> Dict[str, Any]:
        data: Dict[str, Any] = {
            "path": path,
            "limit": limit,
            "mode": mode,
        }
        result = self._api_call(
            "files/list_revisions", data=data, http_method="POST"
        )
        revisions_data = result.get("entries", [])
        revisions = [
            DropboxFileRevision(
                id=r.get("id", ""),
                name=r.get("name", ""),
                size=r.get("size", 0),
                server_modified=r.get("server_modified", ""),
                client_modified=r.get("client_modified", ""),
                rev=r.get("rev", ""),
                is_downloadable=r.get("is_downloadable", True),
                extra=r,
            )
            for r in revisions_data
        ]
        return {
            "revisions": revisions,
            "is_deleted": result.get("is_deleted", False),
            "server_deleted": result.get("server_deleted"),
        }

    def restore_file(self, path: str, rev: str) -> DropboxFileMetadata:
        result = self._api_call(
            "files/restore",
            data={"path": path, "rev": rev},
            http_method="POST",
        )
        return self._parse_metadata(result)

    # ------------------------------------------------------------------
    # Users and teams
    # ------------------------------------------------------------------

    def get_current_user(self) -> DropboxUser:
        result = self._api_call("users/get_current_account")
        return self._parse_user(result)

    def get_user(self, account_id: str) -> DropboxUser:
        result = self._api_call(
            "users/get_account",
            data={"account_id": account_id},
            http_method="POST",
        )
        return self._parse_user(result)

    def get_users_batch(self, account_ids: List[str]) -> List[DropboxUser]:
        result = self._api_call(
            "users/get_account_batch",
            data={"account_ids": account_ids},
            http_method="POST",
        )
        return [self._parse_user(u) for u in result]

    def get_space_usage(self) -> Dict[str, Any]:
        return self._api_call("users/get_space_usage", http_method="POST")

    def _parse_user(self, data: Dict[str, Any]) -> DropboxUser:
        return DropboxUser(
            account_id=data.get("account_id", ""),
            name=data.get("name", {}),
            email=data.get("email", ""),
            email_verified=data.get("email_verified", False),
            profile_photo_url=data.get("profile_photo_url", ""),
            country=data.get("country", ""),
            locale=data.get("locale", ""),
            team_member_id=data.get("team_member_id", ""),
            is_paired=data.get("is_paired", False),
            account_type=data.get("account_type", {}),
            extra=data,
        )

    def get_team_info(self) -> DropboxTeam:
        result = self._api_call("users/get_current_account")
        team = result.get("team", {})
        return DropboxTeam(
            id=team.get("id", ""),
            name=team.get("name", ""),
            num_teams=team.get("num_teams", 0),
            num_users=team.get("num_users", 0),
            extra=team,
        )

    # ------------------------------------------------------------------
    # Paper docs
    # ------------------------------------------------------------------

    def list_paper_docs(self, filter_by: str = "",
                        sort_by: str = "modified",
                        sort_order: str = "descending",
                        limit: int = 1000,
                        **kwargs: Any) -> Dict[str, Any]:
        data: Dict[str, Any] = {
            "filter_by": filter_by or {".tag": "docs_created"},
            "sort_by": sort_by,
            "sort_order": sort_order,
            "limit": limit,
        }
        data.update(kwargs)

        result = self._api_call(
            "paper/docs/list", data=data, http_method="POST"
        )
        doc_ids = result.get("doc_ids", [])
        return {
            "doc_ids": doc_ids,
            "cursor": result.get("cursor"),
            "has_more": result.get("has_more", False),
        }

    def get_paper_doc(self, doc_id: str,
                      export_format: str = "markdown") -> Dict[str, Any]:
        self._check_rate_limit()
        self._mark_request()

        url = f"{self._content_url}/paper/docs/download"
        api_args = {"doc_id": doc_id, "export_format": export_format}
        headers = {
            "Authorization": f"Bearer {self.config.access_token or self.config.api_key or ''}",
            "Dropbox-API-Arg": json.dumps(api_args),
        }

        self._log_request("POST", url, headers, f"Paper doc: {doc_id}")

        try:
            import urllib.request
            req = urllib.request.Request(
                url, data=b"", headers=headers, method="POST"
            )
            with urllib.request.urlopen(req, timeout=self.config.timeout) as r:
                content = r.read().decode("utf-8")
                self._log_response(
                    "POST", url, r.status,
                    f"<text {len(content)} chars>"
                )
                return {
                    "doc_id": doc_id,
                    "content": content,
                    "format": export_format,
                    "content_length": len(content),
                }
        except Exception as e:
            raise self._handle_error(f"Paper download error: {e}")

    def create_paper_doc(self, title: str,
                         content: str = "",
                         parent_folder_id: str = "") -> DropboxPaperDoc:
        self._check_rate_limit()
        self._mark_request()

        url = f"{self._content_url}/paper/docs/create"
        api_args: Dict[str, Any] = {"title": title}
        if parent_folder_id:
            api_args["parent_folder_id"] = parent_folder_id

        headers = {
            "Authorization": f"Bearer {self.config.access_token or self.config.api_key or ''}",
            "Content-Type": "application/octet-stream",
            "Dropbox-API-Arg": json.dumps(api_args),
        }

        body = content.encode("utf-8")
        self._log_request("POST", url, headers, f"Paper doc create: {title}")

        try:
            import urllib.request
            req = urllib.request.Request(
                url, data=body, headers=headers, method="POST"
            )
            with urllib.request.urlopen(req, timeout=self.config.timeout) as r:
                response_body = r.read().decode("utf-8")
                self._log_response("POST", url, r.status, response_body)
                result = json.loads(response_body)
                return DropboxPaperDoc(
                    doc_id=result.get("doc_id", ""),
                    title=result.get("title", title),
                    revision=result.get("revision", 0),
                    extra=result,
                )
        except Exception as e:
            raise self._handle_error(f"Paper create error: {e}")

    # ------------------------------------------------------------------
    # Webhook verification
    # ------------------------------------------------------------------

    def verify_webhook(self, request_body: str,
                       signature: str,
                       app_secret: str) -> bool:
        """
        Verify a Dropbox webhook signature.

        Args:
            request_body: Raw request body.
            signature: X-Dropbox-Signature header value.
            app_secret: App secret key.

        Returns:
            True if the webhook is verified.
        """
        expected = hmac.new(
            app_secret.encode("utf-8"),
            request_body.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()
        return hmac.compare_digest(signature, expected)

    # ------------------------------------------------------------------
    # Internal API call helper
    # ------------------------------------------------------------------

    def _api_call(self, path: str,
                  data: Optional[Dict[str, Any]] = None,
                  http_method: str = "POST") -> Dict[str, Any]:
        self._check_rate_limit()
        self._mark_request()

        url = self._build_url(path)
        headers = {
            "Authorization": f"Bearer {self.config.access_token or self.config.api_key or ''}",
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
                self._rate_limit_hits += 1
                raise RateLimitError(
                    "Rate limited by Dropbox",
                    retry_after=60,
                    adapter_name=self.config.name,
                )
            if e.code in (401, 403):
                raise self._handle_error(
                    "Authentication failed: invalid Dropbox token",
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
            f"<DropboxAdapter name={self.config.name} "
            f"status={self._status.value} "
            f"user={self._user_name or 'unknown'}>"
        )
