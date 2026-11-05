"""OSV 安全公告查询：按仓库的生态与包名查 ``api.osv.dev``。

三条必须分开的结论，任何一条都不允许被写成「安全」：

1. ``fetched`` + 有公告 → 写入每一条公告（编号、影响区间、严重级别、抓取时间）；
2. ``fetched`` + ``vulns`` 为空 → 明确记「无公告记录（不代表安全）」；
3. ``not_fetched`` → 查询失败/网络关闭/配额用尽/包名无法解析，**不能**降级成
   「无公告」或「安全」。

真实网络默认关闭；只有 ``allow_network=True`` 或测试注入 ``transport`` 才会请求。
"""

from __future__ import annotations

import json
import re
import tomllib
from dataclasses import dataclass, field
from typing import Any

import httpx

from src._time import utc_now
from src.intel.models import AdvisoryRecord, FetchStatus, ScanBudget

OSV_QUERY_URL = "https://api.osv.dev/v1/query"
OSV_ADVISORY_URL = "https://osv.dev/vulnerability/{advisory_id}"
SOURCE = "OSV"

_NO_ADVISORY_NOTE = "OSV 查询成功，无公告记录（vulns 为空，不代表该仓库安全）"
_QUERY_FAILED_NOTE = "OSV 查询失败，不能据此判断该仓库安全"
_UNRESOLVED_NOTE = "未能从依赖清单解析生态与包名，未查询 OSV，不能视为安全"

#: 只从仓库清单推断生态；不从仓库 README 或仓库名猜包名。
_MANIFEST_ECOSYSTEMS = {
    "pyproject.toml": "PyPI",
    "package.json": "npm",
    "go.mod": "Go",
    "Cargo.toml": "crates.io",
}

#: 允许 scanner 依次尝试的清单文件（顺序固定，便于配额可预期）。
MANIFEST_FILENAMES = tuple(_MANIFEST_ECOSYSTEMS)

_PYPI_NORMALIZE_RE = re.compile(r"[-_.]+")


@dataclass(frozen=True)
class PackageRef:
    """OSV 查询所需的「生态 + 包名」。"""

    ecosystem: str
    name: str
    manifest: str


@dataclass
class OsvQueryResult:
    """一次 OSV 查询的完整证据。"""

    status: str
    url: str
    attempted_at: str
    reason: str | None = None
    http_status: int | None = None
    vulns: list[dict[str, Any]] = field(default_factory=list)
    note: str | None = None

    @property
    def fetched(self) -> bool:
        return self.status == FetchStatus.FETCHED.value


def _parse_pyproject(text: str) -> str | None:
    data = tomllib.loads(text)
    project = data.get("project")
    if isinstance(project, dict) and isinstance(project.get("name"), str):
        return str(project["name"])
    poetry = data.get("tool", {}).get("poetry") if isinstance(data.get("tool"), dict) else None
    if isinstance(poetry, dict) and isinstance(poetry.get("name"), str):
        return str(poetry["name"])
    return None


def _parse_package_json(text: str) -> str | None:
    data = json.loads(text)
    name = data.get("name") if isinstance(data, dict) else None
    return str(name) if isinstance(name, str) and name.strip() else None


def _parse_cargo(text: str) -> str | None:
    data = tomllib.loads(text)
    package = data.get("package")
    if isinstance(package, dict) and isinstance(package.get("name"), str):
        return str(package["name"])
    return None


def _parse_go_mod(text: str) -> str | None:
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("module "):
            value = stripped[len("module ") :].strip().strip('"')
            return value or None
    return None


_PARSERS = {
    "pyproject.toml": _parse_pyproject,
    "package.json": _parse_package_json,
    "go.mod": _parse_go_mod,
    "Cargo.toml": _parse_cargo,
}


def resolve_package(manifest_text: str, manifest_filename: str) -> PackageRef | None:
    """从清单原文解析包名；解析不到就返回 ``None``，绝不拿仓库名硬猜。"""
    ecosystem = _MANIFEST_ECOSYSTEMS.get(manifest_filename)
    parser = _PARSERS.get(manifest_filename)
    if ecosystem is None or parser is None:
        return None
    try:
        name = parser(manifest_text)
    except (json.JSONDecodeError, tomllib.TOMLDecodeError, ValueError, TypeError):
        return None
    if not name:
        return None
    name = name.strip()
    if ecosystem == "PyPI":
        name = _PYPI_NORMALIZE_RE.sub("-", name).lower()
    return PackageRef(ecosystem=ecosystem, name=name, manifest=manifest_filename)


def version_for_osv(tag: str | None, commit_sha: str | None = None) -> str | None:
    """只在有明确 tag 时带版本查询；commit sha 不是 OSV 可识别的发布版本。"""
    if tag:
        return tag.strip().lstrip("vV") or None
    return None


class OsvClient:
    """只读 OSV 查询客户端；每次查询都消耗一次 API 配额。"""

    def __init__(
        self,
        *,
        transport: httpx.BaseTransport | None = None,
        allow_network: bool = False,
        budget: ScanBudget | None = None,
        base_url: str = OSV_QUERY_URL,
        timeout: float = 15.0,
    ) -> None:
        self._base_url = base_url
        self._budget = budget
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

    def query(self, package: PackageRef, *, version: str | None = None) -> OsvQueryResult:
        attempted_at = utc_now().isoformat()
        if self._client is None:
            return OsvQueryResult(
                status=FetchStatus.NOT_FETCHED.value,
                url=self._base_url,
                attempted_at=attempted_at,
                reason="network_disabled",
            )
        if self._budget is not None and not self._budget.reserve_api_call():
            return OsvQueryResult(
                status=FetchStatus.NOT_FETCHED.value,
                url=self._base_url,
                attempted_at=attempted_at,
                reason="quota_exhausted",
            )

        body: dict[str, Any] = {
            "package": {"name": package.name, "ecosystem": package.ecosystem}
        }
        if version:
            body["version"] = version
        try:
            response = self._client.post(
                self._base_url,
                json=body,
                headers={"User-Agent": "liuhao-osi/0.1", "Accept": "application/json"},
            )
        except httpx.HTTPError as exc:
            return OsvQueryResult(
                status=FetchStatus.NOT_FETCHED.value,
                url=self._base_url,
                attempted_at=attempted_at,
                reason=f"network_error:{type(exc).__name__}",
            )

        if response.status_code >= 400:
            return OsvQueryResult(
                status=FetchStatus.NOT_FETCHED.value,
                url=self._base_url,
                attempted_at=attempted_at,
                reason=f"api_error:{response.status_code}",
                http_status=response.status_code,
            )
        try:
            payload = response.json()
        except ValueError:
            return OsvQueryResult(
                status=FetchStatus.NOT_FETCHED.value,
                url=self._base_url,
                attempted_at=attempted_at,
                reason="response_not_json",
                http_status=response.status_code,
            )
        vulns = payload.get("vulns") if isinstance(payload, dict) else None
        if vulns is None:
            vulns = []
        if not isinstance(vulns, list):
            return OsvQueryResult(
                status=FetchStatus.NOT_FETCHED.value,
                url=self._base_url,
                attempted_at=attempted_at,
                reason="malformed_response",
                http_status=response.status_code,
            )
        note = None
        if isinstance(payload, dict) and payload.get("next_page_token"):
            note = "OSV 返回分页令牌，本模块只取第一页；结果可能不完整，需人工复核"
        return OsvQueryResult(
            status=FetchStatus.FETCHED.value,
            url=self._base_url,
            attempted_at=attempted_at,
            http_status=response.status_code,
            vulns=[vuln for vuln in vulns if isinstance(vuln, dict)],
            note=note,
        )


def _affected_range(vuln: dict[str, Any]) -> str | None:
    parts: list[str] = []
    affected_entries = vuln.get("affected")
    if not isinstance(affected_entries, list):
        return None
    for affected in affected_entries:
        if not isinstance(affected, dict):
            continue
        package = affected.get("package") if isinstance(affected.get("package"), dict) else {}
        prefix = ""
        if package.get("ecosystem") and package.get("name"):
            prefix = f"{package['ecosystem']}:{package['name']} "
        ranges = affected.get("ranges")
        if isinstance(ranges, list):
            for item in ranges:
                if not isinstance(item, dict):
                    continue
                events = item.get("events")
                if not isinstance(events, list):
                    continue
                chunks = [
                    f"{key}:{event[key]}"
                    for event in events
                    if isinstance(event, dict)
                    for key in ("introduced", "fixed", "last_affected", "limit")
                    if key in event
                ]
                if chunks:
                    kind = item.get("type") or "range"
                    parts.append(f"{prefix}{kind}({', '.join(chunks)})")
        versions = affected.get("versions")
        if not ranges and isinstance(versions, list) and versions:
            shown = ", ".join(str(item) for item in versions[:3])
            suffix = " ..." if len(versions) > 3 else ""
            parts.append(f"{prefix}versions({shown}{suffix})")
    return "; ".join(parts) or None


def _severity(vuln: dict[str, Any]) -> str:
    database_specific = vuln.get("database_specific")
    if isinstance(database_specific, dict):
        severity = database_specific.get("severity")
        if isinstance(severity, str) and severity.strip():
            return severity.strip()
    severity_entries = vuln.get("severity")
    if isinstance(severity_entries, list) and severity_entries:
        first = severity_entries[0]
        if isinstance(first, dict):
            score = first.get("score")
            if isinstance(score, str) and score.strip():
                return score.strip()
            kind = first.get("type")
            if isinstance(kind, str) and kind.strip():
                return kind.strip()
    return "unknown"


def advisory_records(
    result: OsvQueryResult,
    *,
    repo_url: str,
    package: PackageRef | None,
) -> list[AdvisoryRecord]:
    """把一次 OSV 结果转成可持久化的公告记录（四类结果走不同分支）。"""
    common = {
        "repo_url": repo_url,
        "source": SOURCE,
        "source_url": result.url,
        "fetched_at": result.attempted_at,
        "ecosystem": package.ecosystem if package else None,
        "package": package.name if package else None,
    }

    if not result.fetched:
        return [
            AdvisoryRecord(
                **common,
                fetch_status=FetchStatus.NOT_FETCHED.value,
                error_reason=result.reason or "osv_not_fetched",
                note=_QUERY_FAILED_NOTE,
            )
        ]

    if not result.vulns:
        return [
            AdvisoryRecord(
                **common,
                fetch_status=FetchStatus.FETCHED.value,
                note=result.note or _NO_ADVISORY_NOTE,
            )
        ]

    records: list[AdvisoryRecord] = []
    for vuln in result.vulns:
        advisory_id = vuln.get("id")
        advisory_id = str(advisory_id) if advisory_id else "unknown"
        aliases = vuln.get("aliases") if isinstance(vuln.get("aliases"), list) else []
        note_parts = [OSV_ADVISORY_URL.format(advisory_id=advisory_id)]
        if aliases:
            note_parts.append(f"别名：{', '.join(str(item) for item in aliases[:5])}")
        if result.note:
            note_parts.append(result.note)
        summary = vuln.get("summary")
        if isinstance(summary, str) and summary.strip():
            note_parts.append(summary.strip())
        records.append(
            AdvisoryRecord(
                **common,
                advisory_id=advisory_id,
                affected_range=_affected_range(vuln),
                severity=_severity(vuln),
                fetch_status=FetchStatus.FETCHED.value,
                note="；".join(note_parts),
            )
        )
    return records


def unresolved_advisory(
    repo_url: str, reason: str, fetched_at: str | None = None
) -> AdvisoryRecord:
    """包名无法解析：明确记「未查询」，不能写成「无公告」。"""
    return AdvisoryRecord(
        repo_url=repo_url,
        source=SOURCE,
        source_url=OSV_QUERY_URL,
        fetch_status=FetchStatus.NOT_FETCHED.value,
        fetched_at=fetched_at or utc_now().isoformat(),
        error_reason=reason,
        note=_UNRESOLVED_NOTE,
    )


__all__ = [
    "MANIFEST_FILENAMES",
    "OSV_QUERY_URL",
    "OsvClient",
    "OsvQueryResult",
    "PackageRef",
    "advisory_records",
    "resolve_package",
    "unresolved_advisory",
    "version_for_osv",
]
