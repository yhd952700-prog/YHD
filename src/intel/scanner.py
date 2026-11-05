"""扫描编排：发现 → 判定 → 建议 → 报告。

安全边界：

* 只读取 GitHub 公开 API 与本地源码，不执行任何安装/合并/改码动作；
* 真实网络默认关闭，由 :class:`~src.intel.github_client.GitHubClient` 控制；
* 仓库数量与 API 调用次数有硬顶，超限即停并显式记录「配额用尽」；
* 失败一律记录「未获取」，不用旧数据冒充新数据。
"""

from __future__ import annotations

import hashlib
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from src._time import utc_now
from src.intel.capabilities import analyze_capabilities, select_capabilities
from src.intel.github_client import (
    FetchRecord,
    GitHubClient,
    canonical_repo_url,
    parse_github_repo,
)
from src.intel.licenses import (
    assess_license_text,
    decide_for_category,
    unknown_assessment,
)
from src.intel.models import (
    AdvisoryRecord,
    Candidate,
    FetchStatus,
    GapRecord,
    LicenseCategory,
    LicenseRecord,
    ProposalRecord,
    ProvenanceRecord,
    ScanBudget,
    Snapshot,
)
from src.intel.proposals import DISCLAIMER, build_proposal
from src.intel.report import default_report_path, render_report, write_report
from src.intel.store import IntelStore

_PROJECT_ROOT = Path(__file__).resolve().parents[2]


@dataclass
class ScanResult:
    """一轮扫描的完整结果（同时已写入 SQLite）。"""

    repo_urls: list[str] = field(default_factory=list)
    candidates: list[Candidate] = field(default_factory=list)
    snapshots: list[Snapshot] = field(default_factory=list)
    licenses: list[LicenseRecord] = field(default_factory=list)
    advisories: list[AdvisoryRecord] = field(default_factory=list)
    gaps: list[GapRecord] = field(default_factory=list)
    proposals: list[ProposalRecord] = field(default_factory=list)
    provenance: list[ProvenanceRecord] = field(default_factory=list)
    quota_notes: list[str] = field(default_factory=list)
    report_text: str = ""
    report_path: str | None = None

    @property
    def adoptable(self) -> list[ProposalRecord]:
        return [proposal for proposal in self.proposals if proposal.adoptable]


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


class IntelScanner:
    """把采集、判定、建议、报告串成一轮只读扫描。"""

    def __init__(
        self,
        store: IntelStore,
        client: GitHubClient,
        *,
        repo_root: Path | str | None = None,
        disclaimer: str = DISCLAIMER,
    ):
        self._store = store
        self._client = client
        self._repo_root = Path(repo_root) if repo_root is not None else _PROJECT_ROOT
        self._disclaimer = disclaimer
        self._capability_findings = None

    def scan(
        self,
        repo_urls: Sequence[str],
        *,
        category: LicenseCategory | str = LicenseCategory.RUNTIME,
        budget: ScanBudget | None = None,
        report_path: Path | str | None = None,
        write_report_file: bool = True,
        generated_at: str | None = None,
    ) -> ScanResult:
        budget = budget or ScanBudget()
        category = LicenseCategory(category)
        result = ScanResult()
        result.repo_urls = list(repo_urls)
        timestamp = generated_at or utc_now().isoformat()

        for raw in repo_urls:
            try:
                owner, name = parse_github_repo(raw)
                repo_url = canonical_repo_url(raw)
            except ValueError as exc:
                self._record_unavailable(
                    result,
                    repo_url=str(raw),
                    name=str(raw),
                    reason=f"invalid_repo_url:{exc}",
                    category=category,
                    attempted_at=timestamp,
                    timestamp=timestamp,
                )
                continue

            if not budget.reserve_repo():
                reason = "quota_exhausted"
                result.quota_notes.append(
                    f"仓库数量配额用尽（上限 {budget.max_repos}）：{repo_url} 未获取"
                )
                self._record_unavailable(
                    result,
                    repo_url=repo_url,
                    name=f"{owner}/{name}",
                    reason=reason,
                    category=category,
                    attempted_at=timestamp,
                    timestamp=timestamp,
                )
                continue

            self._scan_one(
                result,
                owner=owner,
                name=name,
                repo_url=repo_url,
                category=category,
                timestamp=timestamp,
            )

        result.report_text = render_report(
            candidates=result.candidates,
            snapshots=result.snapshots,
            licenses=result.licenses,
            advisories=result.advisories,
            gaps=result.gaps,
            proposals=result.proposals,
            provenance=result.provenance,
            budget=budget,
            category=category.value,
            repo_urls=result.repo_urls,
            quota_notes=result.quota_notes,
            generated_at=timestamp,
        )
        if write_report_file:
            destination = Path(report_path) if report_path else default_report_path(timestamp)
            result.report_path = str(write_report(result.report_text, destination))
        return result

    # ------------------------------------------------------------------ #
    # 单个仓库
    # ------------------------------------------------------------------ #
    def _scan_one(
        self,
        result: ScanResult,
        *,
        owner: str,
        name: str,
        repo_url: str,
        category: LicenseCategory,
        timestamp: str,
    ) -> None:
        repo_rec = self._client.get_repo(owner, name)
        if not repo_rec.fetched:
            self._record_unavailable(
                result,
                repo_url=repo_url,
                name=f"{owner}/{name}",
                reason=repo_rec.reason or "repo_not_fetched",
                category=category,
                attempted_at=repo_rec.attempted_at,
                timestamp=timestamp,
                source_url=repo_rec.url,
                detail=repo_rec,
            )
            return

        repo_data = repo_rec.payload if isinstance(repo_rec.payload, dict) else {}
        candidate_name = str(repo_data.get("full_name") or f"{owner}/{name}")
        language = repo_data.get("language")
        stars = repo_data.get("stargazers_count")
        description = repo_data.get("description")
        topics = repo_data.get("topics") if isinstance(repo_data.get("topics"), list) else []
        last_commit_at = repo_data.get("pushed_at")

        commit_rec = self._client.get_latest_commit(owner, name)
        commit_sha: str | None = None
        if commit_rec.fetched and isinstance(commit_rec.payload, list) and commit_rec.payload:
            first = commit_rec.payload[0] if isinstance(commit_rec.payload[0], dict) else {}
            commit_sha = first.get("sha")
            commit_block = first.get("commit") if isinstance(first.get("commit"), dict) else {}
            committer = commit_block.get("committer") if isinstance(commit_block.get("committer"), dict) else {}
            last_commit_at = committer.get("date") or last_commit_at

        release_rec = self._client.get_latest_release(owner, name)
        version: str | None = commit_sha
        tag: str | None = None
        if release_rec.fetched and isinstance(release_rec.payload, dict):
            release_tag = release_rec.payload.get("tag_name")
            if release_tag:
                tag = str(release_tag)
                version = tag

        license_rec, license_text = self._client.get_license_text(owner, name)
        if license_text is not None:
            assessment = assess_license_text(license_text)
            decision = decide_for_category(assessment, category)
            raw_sha = assessment.raw_sha256
            license_source = GitHubClient.license_source_url(license_rec)
        else:
            assessment = unknown_assessment(license_rec.reason or "license_not_fetched")
            decision = decide_for_category(assessment, category)
            raw_sha = None
            license_source = license_rec.url

        manifest_rec: FetchRecord | None = None
        manifest_text: str | None = None
        for filename in ("pyproject.toml", "package.json"):
            record, text = self._client.get_text_file(owner, name, filename)
            if manifest_rec is None:
                manifest_rec = record
            if text is not None:
                manifest_rec = record
                manifest_text = text
                break

        critical_records = [repo_rec, commit_rec, license_rec]
        optional_records = [manifest_rec] if manifest_rec is not None else []
        failures = [
            record.reason or "not_fetched"
            for record in [*critical_records, *optional_records]
            if not record.fetched
        ]
        fetch_status = FetchStatus.FETCHED.value if not failures else FetchStatus.PARTIAL.value
        details = [
            record.to_dict()
            for record in [repo_rec, commit_rec, release_rec, license_rec, manifest_rec]
            if record is not None
        ]

        snapshot = Snapshot(
            repo_url=repo_url,
            fetched_at=timestamp,
            source_url=repo_rec.url,
            fetch_status=fetch_status,
            version=version,
            tag=tag,
            commit_sha=commit_sha,
            license_raw_sha256=raw_sha,
            dependency_manifest_sha256=_sha256(manifest_text) if manifest_text else None,
            error_reason="; ".join(failures) if failures else None,
            details=details,
        )
        previous = self._store.latest_snapshot(repo_url)
        self._store.insert_snapshot(snapshot)
        result.snapshots.append(snapshot)

        license_record = LicenseRecord(
            repo_url=repo_url,
            license_id=assessment.license_id,
            source_url=license_source,
            verdict=assessment.verdict.value,
            category=category.value,
            allowed=decision.allowed,
            captured_at=timestamp,
            raw_sha256=raw_sha,
            reason=decision.reason,
            requires_note=decision.requires_note,
        )
        self._store.insert_license(license_record)
        result.licenses.append(license_record)

        advisory = AdvisoryRecord(
            repo_url=repo_url,
            source="github-advisory",
            source_url=GitHubClient.advisory_url(owner, name),
            fetch_status=FetchStatus.NOT_FETCHED.value,
            fetched_at=timestamp,
            error_reason="advisory_endpoint_not_wired_in_this_round",
        )
        self._store.insert_advisory(advisory)
        result.advisories.append(advisory)

        first_seen = self._first_seen(repo_url, timestamp)
        candidate = Candidate(
            repo_url=repo_url,
            name=candidate_name,
            first_seen_at=first_seen,
            last_assessed_at=timestamp,
            language=str(language) if language else None,
            stars=int(stars) if isinstance(stars, int) else None,
            last_commit_at=str(last_commit_at) if last_commit_at else None,
            description=str(description) if description else None,
            topics=[str(topic) for topic in topics],
            last_status=fetch_status,
            last_error=snapshot.error_reason,
        )
        self._store.upsert_candidate(candidate)
        result.candidates.append(candidate)

        change_history = self._change_history(previous, version, commit_sha)
        provenance = ProvenanceRecord(
            repo_url=repo_url,
            source="github",
            source_url=repo_rec.url,
            captured_at=timestamp,
            license_id=assessment.license_id,
            version=version,
            change_history=change_history,
        )
        self._store.insert_provenance(provenance)
        result.provenance.append(provenance)

        findings = self._findings()
        relevant = select_capabilities(candidate)
        gap_records: list[GapRecord] = []
        for finding in findings:
            if finding.capability not in {cap.key for cap in relevant}:
                continue
            gap = GapRecord(
                repo_url=repo_url,
                capability=finding.capability,
                status=finding.status.value,
                file_paths=list(finding.files),
                assessed_at=timestamp,
                note="按文件名/文本关键词粗对照，非语义判断",
            )
            self._store.insert_gap(gap)
            result.gaps.append(gap)
            gap_records.append(gap)

        proposal = build_proposal(candidate, decision, gap_records, timestamp)
        proposal.disclaimer = self._disclaimer
        self._store.insert_proposal(proposal)
        result.proposals.append(proposal)

    # ------------------------------------------------------------------ #
    # 失败路径：显式「未获取」
    # ------------------------------------------------------------------ #
    def _record_unavailable(
        self,
        result: ScanResult,
        *,
        repo_url: str,
        name: str,
        reason: str,
        category: LicenseCategory,
        attempted_at: str,
        timestamp: str,
        source_url: str | None = None,
        detail: FetchRecord | None = None,
    ) -> None:
        first_seen = self._first_seen(repo_url, timestamp)
        candidate = Candidate(
            repo_url=repo_url,
            name=name,
            first_seen_at=first_seen,
            last_assessed_at=timestamp,
            last_status=FetchStatus.NOT_FETCHED.value,
            last_error=reason,
        )
        self._store.upsert_candidate(candidate)
        result.candidates.append(candidate)

        details = [
            detail.to_dict()
            if detail is not None
            else {
                "resource": "repo",
                "url": source_url or repo_url,
                "attempted_at": attempted_at,
                "status": "not_fetched",
                "reason": reason,
                "http_status": None,
            }
        ]
        snapshot = Snapshot(
            repo_url=repo_url,
            fetched_at=timestamp,
            source_url=source_url or repo_url,
            fetch_status=FetchStatus.NOT_FETCHED.value,
            error_reason=reason,
            details=details,
        )
        self._store.insert_snapshot(snapshot)
        result.snapshots.append(snapshot)

        license_record = LicenseRecord(
            repo_url=repo_url,
            license_id="unknown",
            source_url=source_url or repo_url,
            verdict="unknown",
            category=category.value,
            allowed=False,
            captured_at=timestamp,
            raw_sha256=None,
            reason=f"未获取 LICENSE 原文（{reason}），判定 unknown，拒绝进入建议采用列表",
            requires_note=False,
        )
        self._store.insert_license(license_record)
        result.licenses.append(license_record)

        advisory = AdvisoryRecord(
            repo_url=repo_url,
            source="github-advisory",
            source_url=source_url or repo_url,
            fetch_status=FetchStatus.NOT_FETCHED.value,
            fetched_at=timestamp,
            error_reason="advisory_endpoint_not_wired_in_this_round",
        )
        self._store.insert_advisory(advisory)
        result.advisories.append(advisory)

        provenance = ProvenanceRecord(
            repo_url=repo_url,
            source="github",
            source_url=source_url or repo_url,
            captured_at=timestamp,
            license_id="unknown",
            version=None,
            change_history={"first_capture": True, "reason": reason},
        )
        self._store.insert_provenance(provenance)
        result.provenance.append(provenance)

        decision = decide_for_category(unknown_assessment(reason), category)
        proposal = build_proposal(candidate, decision, [], timestamp)
        proposal.disclaimer = self._disclaimer
        self._store.insert_proposal(proposal)
        result.proposals.append(proposal)

    # ------------------------------------------------------------------ #
    # 辅助
    # ------------------------------------------------------------------ #
    def _first_seen(self, repo_url: str, timestamp: str) -> str:
        existing = self._store.get_candidate(repo_url)
        if existing and existing.get("first_seen_at"):
            return str(existing["first_seen_at"])
        return timestamp

    def _findings(self):
        if self._capability_findings is None:
            self._capability_findings = analyze_capabilities(self._repo_root)
        return self._capability_findings

    @staticmethod
    def _change_history(
        previous: dict[str, Any] | None, version: str | None, commit_sha: str | None
    ) -> dict[str, Any]:
        if previous is None:
            return {"first_capture": True}
        previous_version = previous.get("version")
        previous_commit = previous.get("commit_sha")
        return {
            "previous_version": previous_version,
            "current_version": version,
            "previous_commit": previous_commit,
            "current_commit": commit_sha,
            "changed": previous_version != version or previous_commit != commit_sha,
        }


__all__ = ["IntelScanner", "ScanResult"]
