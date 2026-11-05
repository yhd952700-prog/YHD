"""数据模型：OSI 开源技术情报中心。

本模块只定义结构化记录与配额对象，不承担任何网络或磁盘副作用。
所有「抓取时间」「来源 URL」都是必填字段：只存结论、不存来源的记录一律不合法。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any


class LicenseCategory(StrEnum):
    """许可证落点：必须在哪种使用场景下判定。"""

    RUNTIME = "runtime"
    BUILD = "build"
    TOOL = "tool"


class LicenseVerdict(StrEnum):
    """许可证判定结果（以 LICENSE 原文为准，不靠仓库标签猜）。"""

    PERMISSIVE = "permissive"
    COPYLEFT = "copyleft"
    NON_OSI = "non-osi"
    UNKNOWN = "unknown"


class FetchStatus(StrEnum):
    """单轮抓取状态。``not_fetched`` 必须带明确原因，不允许用旧数据冒充新数据。"""

    FETCHED = "fetched"
    PARTIAL = "partial"
    NOT_FETCHED = "not_fetched"


class GapStatus(StrEnum):
    """候选仓库能力 vs 鎏灏现有能力。"""

    EXISTING = "existing"
    PARTIAL = "partial"
    MISSING = "missing"


class ProposalStrategy(StrEnum):
    """集成方案策略。只建议，不自动动手。"""

    ADOPT = "采用"
    ADAPT = "改造"
    BORROW = "仅借鉴设计"


class QuotaExhausted(RuntimeError):
    """硬配额用尽。调用方必须停下并显式报告，不得降级为伪造数据。"""


@dataclass
class ScanBudget:
    """单轮扫描硬顶：仓库数量、API 调用次数、token 消耗。

    token 配额供未来 LLM 摘要阶段使用；当前阶段不调用 LLM，``tokens_used``
    保持 0，报告会如实写明，而不是编造一个消耗数字。
    """

    max_repos: int = 10
    max_api_calls: int = 60
    max_tokens: int = 20000
    repos_reserved: int = 0
    api_calls_used: int = 0
    tokens_used: int = 0

    def reserve_repo(self) -> bool:
        """占用一个仓库名额；超出上限返回 ``False``（调用方须停止并记录配额用尽）。"""
        if self.repos_reserved >= self.max_repos:
            return False
        self.repos_reserved += 1
        return True

    def reserve_api_call(self) -> bool:
        """占用一次 API 调用名额；超出上限返回 ``False``。"""
        if self.api_calls_used >= self.max_api_calls:
            return False
        self.api_calls_used += 1
        return True

    def consume_tokens(self, tokens: int) -> None:
        """消耗 token 配额；超限抛 :class:`QuotaExhausted`。"""
        if tokens < 0:
            raise ValueError("tokens must be non-negative")
        if self.tokens_used + tokens > self.max_tokens:
            raise QuotaExhausted("token 配额用尽")
        self.tokens_used += tokens

    def as_dict(self) -> dict[str, Any]:
        return {
            "max_repos": self.max_repos,
            "repos_reserved": self.repos_reserved,
            "max_api_calls": self.max_api_calls,
            "api_calls_used": self.api_calls_used,
            "max_tokens": self.max_tokens,
            "tokens_used": self.tokens_used,
        }


@dataclass
class Candidate:
    """osi_candidates：候选仓库。"""

    repo_url: str
    name: str
    first_seen_at: str
    last_assessed_at: str
    language: str | None = None
    stars: int | None = None
    last_commit_at: str | None = None
    description: str | None = None
    topics: list[str] = field(default_factory=list)
    last_status: str = FetchStatus.NOT_FETCHED.value
    last_error: str | None = None


@dataclass
class Snapshot:
    """osi_snapshots：一次抓取的版本/标签/提交与 LICENSE、依赖清单哈希。"""

    repo_url: str
    fetched_at: str
    source_url: str
    fetch_status: str
    version: str | None = None
    tag: str | None = None
    commit_sha: str | None = None
    license_raw_sha256: str | None = None
    dependency_manifest_sha256: str | None = None
    error_reason: str | None = None
    details: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class LicenseRecord:
    """osi_licenses：许可证原文位置与判定结果。"""

    repo_url: str
    license_id: str
    source_url: str
    verdict: str
    category: str
    allowed: bool
    captured_at: str
    raw_sha256: str | None = None
    reason: str = ""
    requires_note: bool = False


@dataclass
class AdvisoryRecord:
    """osi_advisories：安全公告（来源、编号、影响区间、严重级别、抓取时间）。"""

    repo_url: str
    source: str
    source_url: str
    fetch_status: str
    fetched_at: str
    advisory_id: str | None = None
    affected_range: str | None = None
    severity: str | None = None
    error_reason: str | None = None


@dataclass
class GapRecord:
    """osi_gap_analysis：候选 → 鎏灏现有能力对照（附真实文件路径）。"""

    repo_url: str
    capability: str
    status: str
    file_paths: list[str]
    assessed_at: str
    note: str = ""


@dataclass
class ProposalRecord:
    """osi_proposals：集成方案（采用 / 改造 / 仅借鉴设计）。"""

    repo_url: str
    strategy: str
    effort: str
    risk: str
    license_conclusion: str
    adoptable: bool
    summary: str
    created_at: str
    disclaimer: str = ""


@dataclass
class ProvenanceRecord:
    """osi_provenance：来源、许可证、版本与修改历史。"""

    repo_url: str
    source: str
    source_url: str
    captured_at: str
    license_id: str | None = None
    version: str | None = None
    change_history: dict[str, Any] = field(default_factory=dict)
