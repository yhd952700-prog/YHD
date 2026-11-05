"""GitHub 单源采集客户端。

铁律：

* 每个抓取结果都记录**抓取时间**与**原始 URL**；只存结论不存来源不合法。
* 硬配额：API 调用次数由 :class:`~src.intel.models.ScanBudget` 统一扣减，
  超限即停并返回 ``quota_exhausted``。
* 断网 / 超配额 / API 失败一律返回 ``not_fetched`` 并带明确原因，
  **绝不返回旧数据冒充新数据**。
* token 只用于本次请求的 ``Authorization`` 头，不落盘、不写日志、不进入任何记录。
* 真实网络默认关闭；只有 ``allow_network=True`` 或测试注入 ``transport``
  才会真正发起请求。
"""

from __future__ import annotations

import base64
import binascii
import re
from dataclasses import dataclass, replace
from typing import Any

import httpx

from src._time import utc_now
from src.intel.models import ScanBudget

_GITHUB_REPO_RE = re.compile(
    r"github\.com[/:](?P<owner>[A-Za-z0-9_.-]+)/(?P<name>[A-Za-z0-9_.-]+)"
)
_PLAIN_REPO_RE = re.compile(
    r"^(?P<owner>[A-Za-z0-9_.-]+)/(?P<name>[A-Za-z0-9_.-]+)$"
)


@dataclass
class FetchRecord:
    """一次资源抓取的完整证据。"""

    resource: str
    url: str
    attempted_at: str
    status: str
    reason: str | None = None
    http_status: int | None = None
    payload: Any = None

    @property
    def fetched(self) -> bool:
        return self.status == "fetched"

    def to_dict(self) -> dict[str, Any]:
        """返回可持久化的证据（不含响应体，避免把大块原始数据写进库）。"""
        return {
            "resource": self.resource,
            "url": self.url,
            "attempted_at": self.attempted_at,
            "status": self.status,
            "reason": self.reason,
            "http_status": self.http_status,
        }


def parse_github_repo(value: str) -> tuple[str, str]:
    """解析 ``owner/name`` / ``https://github.com/owner/name`` 两种写法。"""
    text = (value or "").strip()
    match = _GITHUB_REPO_RE.search(text)
    if match is None:
        match = _PLAIN_REPO_RE.match(text)
    if match is None:
        raise ValueError(f"无法解析 GitHub 仓库地址: {value!r}")
    owner = match.group("owner")
    name = match.group("name")
    if name.endswith(".git"):
        name = name[: -len(".git")]
    return owner, name


def canonical_repo_url(value: str) -> str:
    """规范化成 ``https://github.com/owner/name``，作为候选与来源主键。"""
    owner, name = parse_github_repo(value)
    return f"https://github.com/{owner}/{name}"


class GitHubClient:
    """只读 GitHub API 客户端；每个方法都返回 :class:`FetchRecord`。"""

    def __init__(
        self,
        *,
        transport: httpx.BaseTransport | None = None,
        allow_network: bool = False,
        token: str | None = None,
        budget: ScanBudget | None = None,
        base_url: str = "https://api.github.com",
        timeout: float = 15.0,
    ):
        self._base_url = base_url.rstrip("/")
        self._budget = budget
        # token 仅在内存中用于请求头；不参与任何持久化或日志。
        self._token = token
        self._client: httpx.Client | None = None
        if transport is not None:
            self._client = httpx.Client(
                transport=transport, timeout=timeout, follow_redirects=True
            )
        elif allow_network:
            self._client = httpx.Client(timeout=timeout, follow_redirects=True)

    @property
    def network_enabled(self) -> bool:
        return self._client is not None

    def close(self) -> None:
        if self._client is not None:
            self._client.close()
            self._client = None

    def __enter__(self) -> GitHubClient:
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()

    # ------------------------------------------------------------------ #
    # 底层请求
    # ------------------------------------------------------------------ #
    def _request(self, resource: str, path: str) -> FetchRecord:
        url = f"{self._base_url}{path}"
        attempted_at = utc_now().isoformat()

        if self._client is None:
            return FetchRecord(
                resource=resource,
                url=url,
                attempted_at=attempted_at,
                status="not_fetched",
                reason="network_disabled",
            )

        if self._budget is not None and not self._budget.reserve_api_call():
            return FetchRecord(
                resource=resource,
                url=url,
                attempted_at=attempted_at,
                status="not_fetched",
                reason="quota_exhausted",
            )

        headers = {
            "Accept": "application/vnd.github+json",
            "User-Agent": "liuhao-osi/0.1",
        }
        if self._token:
            headers["Authorization"] = f"Bearer {self._token}"

        try:
            response = self._client.get(url, headers=headers)
        except httpx.HTTPError as exc:
            return FetchRecord(
                resource=resource,
                url=url,
                attempted_at=attempted_at,
                status="not_fetched",
                reason=f"network_error:{type(exc).__name__}",
            )

        if response.status_code >= 400:
            return FetchRecord(
                resource=resource,
                url=url,
                attempted_at=attempted_at,
                status="not_fetched",
                reason=f"api_error:{response.status_code}",
                http_status=response.status_code,
            )

        try:
            payload = response.json()
        except ValueError:
            return FetchRecord(
                resource=resource,
                url=url,
                attempted_at=attempted_at,
                status="not_fetched",
                reason="response_not_json",
                http_status=response.status_code,
            )

        return FetchRecord(
            resource=resource,
            url=url,
            attempted_at=attempted_at,
            status="fetched",
            http_status=response.status_code,
            payload=payload,
        )

    # ------------------------------------------------------------------ #
    # 高层资源
    # ------------------------------------------------------------------ #
    def get_repo(self, owner: str, name: str) -> FetchRecord:
        return self._request("repo", f"/repos/{owner}/{name}")

    def get_latest_commit(self, owner: str, name: str) -> FetchRecord:
        return self._request(
            "latest_commit", f"/repos/{owner}/{name}/commits?per_page=1"
        )

    def get_latest_release(self, owner: str, name: str) -> FetchRecord:
        return self._request("latest_release", f"/repos/{owner}/{name}/releases/latest")

    def get_license(self, owner: str, name: str) -> FetchRecord:
        return self._request("license", f"/repos/{owner}/{name}/license")

    def get_text_file(self, owner: str, name: str, path: str) -> tuple[FetchRecord, str | None]:
        """读取仓库内文本文件（如 ``pyproject.toml`` / ``package.json``）。"""
        record = self._request(
            f"file:{path}", f"/repos/{owner}/{name}/contents/{path}"
        )
        if not record.fetched:
            return record, None
        payload = record.payload if isinstance(record.payload, dict) else {}
        content = payload.get("content")
        if not content or payload.get("encoding") != "base64":
            return (
                replace(record, status="not_fetched", reason="content_not_base64"),
                None,
            )
        try:
            text = base64.b64decode(content).decode("utf-8")
        except (binascii.Error, UnicodeDecodeError, ValueError):
            return (
                replace(record, status="not_fetched", reason="content_decode_error"),
                None,
            )
        return record, text

    def get_license_text(self, owner: str, name: str) -> tuple[FetchRecord, str | None]:
        """读取 LICENSE 原文。判定只以这段原文为准，不看仓库标签。"""
        record = self.get_license(owner, name)
        if not record.fetched:
            return record, None
        payload = record.payload if isinstance(record.payload, dict) else {}
        content = payload.get("content")
        if not content:
            return replace(record, status="not_fetched", reason="license_content_empty"), None
        encoding = payload.get("encoding")
        if encoding != "base64":
            return replace(record, status="not_fetched", reason="license_not_base64"), None
        try:
            text = base64.b64decode(content).decode("utf-8")
        except (binascii.Error, UnicodeDecodeError, ValueError):
            return replace(record, status="not_fetched", reason="license_decode_error"), None
        return record, text

    @staticmethod
    def license_source_url(record: FetchRecord) -> str:
        """优先返回 LICENSE 文件的人类可读地址，否则退回 API URL。"""
        payload = record.payload if isinstance(record.payload, dict) else {}
        for key in ("html_url", "download_url"):
            value = payload.get(key)
            if isinstance(value, str) and value:
                return value
        return record.url

    @staticmethod
    def advisory_url(owner: str, name: str) -> str:
        return f"https://github.com/{owner}/{name}/security/advisories"
