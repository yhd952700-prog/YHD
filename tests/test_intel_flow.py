"""OSI 全流程测试：发现 → 判定 → 建议 → 报告（全部走 mock HTTP）。

真实网络默认关闭；以下所有 HTTP 都由 ``httpx.MockTransport`` 提供。
"""

from __future__ import annotations

import base64

import httpx
import pytest

from src.intel.github_client import GitHubClient
from src.intel.models import LicenseVerdict, QuotaExhausted, ScanBudget
from src.intel.proposals import DISCLAIMER
from src.intel.scanner import IntelScanner
from src.intel.store import IntelStore

MIT_TEXT = (
    "MIT License\n\n"
    "Permission is hereby granted, free of charge, to any person obtaining a copy "
    "of this software and associated documentation files (the \"Software\"), to deal "
    "in the Software without restriction.\n\n"
    "THE SOFTWARE IS PROVIDED \"AS IS\", WITHOUT WARRANTY OF ANY KIND."
)
GPL_TEXT = "GNU GENERAL PUBLIC LICENSE\nVersion 3, 29 June 2007"


def _encoded(text: str) -> dict:
    return {
        "encoding": "base64",
        "content": base64.b64encode(text.encode("utf-8")).decode("ascii"),
        "html_url": "https://github.com/alpha/agent/blob/main/LICENSE",
    }


def _repo(full_name: str, language: str, stars: int, topics: list[str], description: str) -> dict:
    return {
        "full_name": full_name,
        "language": language,
        "stargazers_count": stars,
        "pushed_at": "2026-11-01T10:00:00Z",
        "topics": topics,
        "description": description,
    }


def _commit(sha: str) -> list:
    return [{"sha": sha, "commit": {"committer": {"date": "2026-11-01T12:00:00Z"}}}]


def _make_transport() -> httpx.MockTransport:
    """三个仓库：MIT（可采用）、GPL（runtime 拒绝）、LICENSE 缺失（unknown 拒绝）。"""

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path

        if path == "/repos/alpha/agent":
            return httpx.Response(
                200,
                json=_repo(
                    "alpha/agent", "Python", 1234, ["agent", "workflow"], "Agent orchestration"
                ),
            )
        if path == "/repos/alpha/agent/commits":
            return httpx.Response(200, json=_commit("abc123"))
        if path == "/repos/alpha/agent/releases/latest":
            return httpx.Response(200, json={"tag_name": "v1.2.0"})
        if path == "/repos/alpha/agent/license":
            return httpx.Response(200, json=_encoded(MIT_TEXT))
        if path == "/repos/alpha/agent/contents/pyproject.toml":
            return httpx.Response(
                200, json=_encoded("[project]\nname = \"agent\"\ndependencies = [\"httpx\"]\n")
            )

        if path == "/repos/beta/gpl":
            return httpx.Response(
                200, json=_repo("beta/gpl", "Go", 500, ["security"], "Security scanner")
            )
        if path == "/repos/beta/gpl/commits":
            return httpx.Response(200, json=_commit("def456"))
        if path == "/repos/beta/gpl/releases/latest":
            return httpx.Response(200, json={"tag_name": "v2.0.0"})
        if path == "/repos/beta/gpl/license":
            return httpx.Response(200, json=_encoded(GPL_TEXT))
        if path == "/repos/beta/gpl/contents/package.json":
            return httpx.Response(200, json=_encoded("{\"dependencies\": {\"left-pad\": \"1.0.0\"}}"))

        if path == "/repos/gamma/unknown":
            return httpx.Response(
                200, json=_repo("gamma/unknown", "Rust", 90, ["knowledge"], "Knowledge graph")
            )
        if path == "/repos/gamma/unknown/commits":
            return httpx.Response(200, json=_commit("ghi789"))

        return httpx.Response(404, json={"message": "Not Found"})

    return httpx.MockTransport(handler)


def _scanner(tmp_path, budget: ScanBudget, *, transport=None, token=None) -> IntelScanner:
    db_path = tmp_path / "osi.db"
    store = IntelStore(str(db_path))
    client = GitHubClient(
        transport=transport,
        allow_network=transport is not None,
        token=token,
        budget=budget,
    )
    return IntelScanner(store, client, repo_root=tmp_path / "empty_repo")


def test_full_discovery_judgement_proposal_report_flow(tmp_path):
    budget = ScanBudget(max_repos=10, max_api_calls=60, max_tokens=20000)
    scanner = _scanner(tmp_path, budget, transport=_make_transport(), token="secret-token")
    report_path = tmp_path / "radar.md"

    result = scanner.scan(
        [
            "https://github.com/alpha/agent",
            "beta/gpl",
            "https://github.com/gamma/unknown",
        ],
        budget=budget,
        report_path=report_path,
    )

    # 发现：3 个候选都写入数据库，状态区分 fetched / partial。
    assert len(result.candidates) == 3
    assert len(result.snapshots) == 3
    assert len(result.licenses) == 3
    assert len(result.advisories) == 3
    assert len(result.proposals) == 3
    assert len(result.provenance) == 3
    assert result.candidates[0].last_status == "fetched"
    assert result.candidates[2].last_status == "partial"

    # 抓取记录必须带时间戳与原始 URL。
    alpha_snapshot = result.snapshots[0]
    assert alpha_snapshot.fetched_at
    assert alpha_snapshot.source_url == "https://api.github.com/repos/alpha/agent"
    license_detail = [d for d in alpha_snapshot.details if d["resource"] == "license"][0]
    assert license_detail["url"] == "https://api.github.com/repos/alpha/agent/license"
    assert license_detail["attempted_at"]

    # 判定：MIT 可采用；GPL runtime 拒绝；LICENSE 缺失 unknown 拒绝。
    licenses = {record.repo_url: record for record in result.licenses}
    assert licenses["https://github.com/alpha/agent"].allowed is True
    assert licenses["https://github.com/beta/gpl"].allowed is False
    assert licenses["https://github.com/beta/gpl"].verdict == LicenseVerdict.COPYLEFT.value
    assert licenses["https://github.com/gamma/unknown"].verdict == LicenseVerdict.UNKNOWN.value
    assert licenses["https://github.com/gamma/unknown"].allowed is False

    # 建议：只有 MIT 候选进入建议采用列表。
    proposals = {record.repo_url: record for record in result.proposals}
    assert proposals["https://github.com/alpha/agent"].adoptable is True
    assert proposals["https://github.com/beta/gpl"].adoptable is False
    assert proposals["https://github.com/gamma/unknown"].adoptable is False
    assert DISCLAIMER in proposals["https://github.com/alpha/agent"].disclaimer

    # 报告：unknown 不得出现在「建议采用」段落。
    text = report_path.read_text(encoding="utf-8")
    adopt_section = text.split("## 建议采用 / 改造")[1].split("## 不建议采用")[0]
    rejected_section = text.split("## 不建议采用")[1].split("## 本轮未获取仓库/资源及原因")[0]
    assert "https://github.com/alpha/agent" in adopt_section
    assert "https://github.com/gamma/unknown" not in adopt_section
    assert "https://github.com/gamma/unknown" in rejected_section
    assert text.count(DISCLAIMER) >= 3

    # 持久化：重新打开数据库仍然有全部记录。
    reopened = IntelStore(str(tmp_path / "osi.db"))
    assert len(reopened.list_candidates()) == 3
    assert len(reopened.list_snapshots()) == 3
    assert len(reopened.list_proposals()) == 3
    reopened.close()

    # 零敏感信息：token 不落盘、不进报告。
    assert b"secret-token" not in (tmp_path / "osi.db").read_bytes()
    assert "secret-token" not in text


def test_real_network_is_disabled_by_default(tmp_path):
    budget = ScanBudget(max_repos=5, max_api_calls=10)
    scanner = _scanner(tmp_path, budget, transport=None)
    result = scanner.scan(
        ["https://github.com/alpha/agent"],
        budget=budget,
        write_report_file=False,
    )

    assert GitHubClient(allow_network=False, budget=budget).network_enabled is False
    assert len(result.candidates) == 1
    assert result.candidates[0].last_status == "not_fetched"
    assert result.candidates[0].last_error == "network_disabled"
    assert result.candidates[0].stars is None

    snapshot = result.snapshots[0]
    assert snapshot.fetch_status == "not_fetched"
    assert snapshot.error_reason == "network_disabled"
    assert snapshot.details[0]["reason"] == "network_disabled"
    assert snapshot.details[0]["attempted_at"]

    assert "未获取" in result.report_text
    assert "network_disabled" in result.report_text
    assert result.adoptable == []


def test_api_call_quota_stops_and_records_not_fetched(tmp_path):
    budget = ScanBudget(max_repos=5, max_api_calls=2)
    scanner = _scanner(tmp_path, budget, transport=_make_transport())
    result = scanner.scan(
        ["https://github.com/alpha/agent"],
        budget=budget,
        write_report_file=False,
    )

    assert budget.api_calls_used == 2
    reasons = {
        detail["reason"]
        for snapshot in result.snapshots
        for detail in snapshot.details
        if detail["status"] != "fetched"
    }
    assert "quota_exhausted" in reasons
    assert result.snapshots[0].fetch_status == "partial"
    assert any(record.verdict == "unknown" for record in result.licenses)
    assert "quota_exhausted" in result.report_text


def test_repository_quota_stops_and_reports_exhausted(tmp_path):
    budget = ScanBudget(max_repos=1, max_api_calls=20)
    scanner = _scanner(tmp_path, budget, transport=_make_transport())
    result = scanner.scan(
        ["https://github.com/alpha/agent", "https://github.com/beta/gpl"],
        budget=budget,
        write_report_file=False,
    )

    assert budget.repos_reserved == 1
    assert len(result.candidates) == 2
    assert result.candidates[1].last_status == "not_fetched"
    assert result.candidates[1].last_error == "quota_exhausted"
    assert any("仓库数量配额用尽" in note for note in result.quota_notes)
    assert "quota_exhausted" in result.report_text
    assert result.candidates[1].stars is None


def test_token_budget_hard_cap_is_enforced():
    budget = ScanBudget(max_tokens=10)
    budget.consume_tokens(10)
    with pytest.raises(QuotaExhausted):
        budget.consume_tokens(1)


def test_invalid_repo_url_is_recorded_as_not_fetched(tmp_path):
    budget = ScanBudget(max_repos=3, max_api_calls=10)
    scanner = _scanner(tmp_path, budget, transport=_make_transport())
    result = scanner.scan(["not-a-github-repo"], budget=budget, write_report_file=False)

    assert result.candidates[0].last_status == "not_fetched"
    assert "invalid_repo_url" in (result.candidates[0].last_error or "")
    assert result.snapshots[0].fetch_status == "not_fetched"
    assert result.adoptable == []


def test_report_is_written_to_explicit_local_path(tmp_path):
    budget = ScanBudget(max_repos=5, max_api_calls=30)
    scanner = _scanner(tmp_path, budget, transport=_make_transport())
    report_path = tmp_path / "nested" / "radar.md"
    result = scanner.scan(
        ["https://github.com/alpha/agent"],
        budget=budget,
        report_path=report_path,
    )

    assert report_path.exists()
    assert result.report_path == str(report_path)
    content = report_path.read_text(encoding="utf-8")
    assert "鎏灏 · 开源技术雷达周报" in content
    assert DISCLAIMER in content
    assert "不扫描全量开源" in content
