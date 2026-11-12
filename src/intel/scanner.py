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

from src._time import utc_now
from src.intel.capabilities import (
    CAPABILITY_MAP_RELATIVE,
    build_gap_records,
    load_capability_map,
)
from src.intel.changes import detect_change
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
    ChangeRecord,
    FetchStatus,
    GapRecord,
    LicenseCategory,
    LicenseRecord,
    ProposalRecord,
    ProvenanceRecord,
    ScanBudget,
    Snapshot,
)
from src.intel.osv_client import (
    MANIFEST_FILENAMES,
    OsvClient,
    advisory_records,
    resolve_package,
    unresolved_advisory,
    version_for_osv,
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
    changes: list[ChangeRecord] = field(default_factory=list)
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
        osv_client: OsvClient | None = None,
    ):
        self._store = store
        self._client = client
        self._repo_root = Path(repo_root) if repo_root is not None else _PROJECT_ROOT
        self._disclaimer = disclaimer
        # 默认不联网：避免测试或误调用时打到 api.osv.dev；CLI/周报显式注入。
        self._osv = osv_client or OsvClient(allow_network=False, budget=client.budget)
        self._capability_rows_cache = None
        self._capability_map_path: Path | None = None

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
            changes=result.changes,
            budget=budget,
            category=category.value,
            repo_urls=result.repo_urls,
            quota_notes=result.quota_notes,
            capability_map_source=self._map_path_text(),
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
        manifest_filename: str | None = None
        for filename in MANIFEST_FILENAMES:
            record, text = self._client.get_text_file(owner, name, filename)
            if manifest_rec is None:
                manifest_rec = record
            if text is not None:
                manifest_rec = record
                manifest_text = text
                manifest_filename = filename
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
        previous = self._store.latest_comparable_snapshot(repo_url)
        change = detect_change(
            previous,
            tag,
            commit_sha,
            current_available=fetch_status != FetchStatus.NOT_FETCHED.value,
        )
        self._store.insert_snapshot(snapshot)
        result.snapshots.append(snapshot)
        change_record = change.to_record(
            repo_url, source_url=repo_rec.url, detected_at=timestamp
        )
        if change_record is not None:
            self._store.insert_change(change_record)
            result.changes.append(change_record)

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

        package = (
            resolve_package(manifest_text, manifest_filename)
            if manifest_text and manifest_filename
            else None
        )
        if package is None:
            advisory_batch = [
                unresolved_advisory(
                    repo_url,
                    "package_unresolved",
                    fetched_at=timestamp,
                )
            ]
        else:
            osv_result = self._osv.query(package, version=version_for_osv(tag, commit_sha))
            advisory_batch = advisory_records(
                osv_result,
                repo_url=repo_url,
                package=package,
            )
        for advisory in advisory_batch:
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

        provenance = ProvenanceRecord(
            repo_url=repo_url,
            source="github",
            source_url=repo_rec.url,
            captured_at=timestamp,
            license_id=assessment.license_id,
            version=version,
            change_history=change.history(timestamp),
        )
        self._store.insert_provenance(provenance)
        result.provenance.append(provenance)

        # 唯一对照源：docs/capability-map.md。全量落库，报告只展示相关行。
        rows = self._load_capability_rows()
        gap_records = build_gap_records(
            candidate,
            rows,
            timestamp,
            map_path=self._map_path_text(),
        )
        for gap in gap_records:
            self._store.insert_gap(gap)
            result.gaps.append(gap)

        relevant_gaps = [gap for gap in gap_records if gap.relevant]
        proposal = build_proposal(candidate, decision, relevant_gaps, timestamp)
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
            source="OSV",
            source_url=source_url or repo_url,
            fetch_status=FetchStatus.NOT_FETCHED.value,
            fetched_at=timestamp,
            error_reason=f"repo_not_fetched:{reason}",
            note="仓库未获取，未查询 OSV，不能视为安全",
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

    def _load_capability_rows(self):
        if self._capability_rows_cache is None:
            candidate_paths = [
                self._repo_root / CAPABILITY_MAP_RELATIVE,
                _PROJECT_ROOT / CAPABILITY_MAP_RELATIVE,
            ]
            map_path = next((path for path in candidate_paths if path.is_file()), candidate_paths[0])
            self._capability_rows_cache = load_capability_map(map_path)
            self._capability_map_path = map_path
        return self._capability_rows_cache

    def _map_path_text(self) -> str:
        if self._capability_map_path is None:
            self._load_capability_rows()
        path = self._capability_map_path or _PROJECT_ROOT / CAPABILITY_MAP_RELATIVE
        try:
            return path.relative_to(_PROJECT_ROOT).as_posix()
        except ValueError:
            return path.as_posix()


__all__ = ["IntelScanner", "ScanResult"]
