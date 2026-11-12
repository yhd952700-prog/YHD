"""版本变更测试：无变化不报、有变化必报、抓取失败不得制造变更。"""

from __future__ import annotations

import base64

import httpx

from src.intel.changes import detect_change
from src.intel.models import ScanBudget
from src.intel.scanner import IntelScanner
from src.intel.store import IntelStore


def _encoded(text: str) -> dict:
    return {
        "encoding": "base64",
        "content": base64.b64encode(text.encode("utf-8")).decode("ascii"),
        "html_url": "https://github.com/alpha/agent/blob/main/LICENSE",
    }


def test_first_capture_is_not_a_change():
    decision = detect_change(None, "v1.0.0", "a1b2c3", current_available=True)
    assert decision.first_capture is True
    assert decision.changed is False
    assert decision.to_record(
        "https://github.com/alpha/agent",
        source_url="https://api.github.com/repos/alpha/agent",
        detected_at="2026-11-06T03:00:00",
    ) is None


def test_same_version_and_commit_is_not_reported():
    previous = {"version": "v1.0.0", "commit_sha": "a1b2c3"}
    decision = detect_change(previous, "v1.0.0", "a1b2c3", current_available=True)
    assert decision.changed is False
    assert decision.history("2026-11-06T03:00:00")["reason"] == "version_unchanged"


def test_version_change_is_reported_with_from_to_and_time():
    previous = {"version": "v1.0.0", "commit_sha": "a1b2c3"}
    decision = detect_change(previous, "v1.1.0", "d4e5f6", current_available=True)
    assert decision.changed is True
    record = decision.to_record(
        "https://github.com/alpha/agent",
        source_url="https://api.github.com/repos/alpha/agent",
        detected_at="2026-11-06T03:00:00",
    )
    assert record is not None
    assert record.previous_version == "v1.0.0"
    assert record.current_version == "v1.1.0"
    assert record.detected_at == "2026-11-06T03:00:00"
    assert decision.history("2026-11-06T03:00:00")["detected_at"] == "2026-11-06T03:00:00"


def test_commit_change_without_tag_is_reported():
    previous = {"version": None, "commit_sha": "a1b2c3"}
    decision = detect_change(previous, None, "d4e5f6", current_available=True)
    assert decision.changed is True
    assert decision.to_record(
        "https://github.com/alpha/agent",
        source_url="https://api.github.com/repos/alpha/agent",
        detected_at="t",
    ).current_commit == "d4e5f6"


def test_release_failure_does_not_fake_a_tag_change():
    previous = {"version": "v1.0.0", "tag": "v1.0.0", "commit_sha": "aaa111"}
    decision = detect_change(previous, None, "aaa111", current_available=True)
    assert decision.changed is False
    assert decision.reason == "version_unchanged"


def test_failed_fetch_never_creates_or_poisons_a_change():
    previous = {"version": "v1.0.0", "commit_sha": "a1b2c3"}
    decision = detect_change(previous, None, None, current_available=False)
    assert decision.changed is False
    assert decision.reason == "current_unavailable"
    assert decision.to_record(
        "https://github.com/alpha/agent",
        source_url="https://api.github.com/repos/alpha/agent",
        detected_at="t",
    ) is None


def _scanner(tmp_path, transport, budget):
    store = IntelStore(str(tmp_path / "osi.db"))
    from src.intel.github_client import GitHubClient

    client = GitHubClient(transport=transport, allow_network=True, budget=budget)
    return IntelScanner(store, client, repo_root=tmp_path / "empty")


def test_scanner_records_change_only_when_snapshot_really_moves(tmp_path):
    state = {"tag": "v1.0.0", "sha": "aaa111", "fail": False}

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if state["fail"] and path.endswith("/repos/alpha/agent"):
            return httpx.Response(500, json={"message": "boom"})
        if path == "/repos/alpha/agent":
            return httpx.Response(200, json={"full_name": "alpha/agent", "language": "Python"})
        if path == "/repos/alpha/agent/commits":
            return httpx.Response(
                200,
                json=[{"sha": state["sha"], "commit": {"committer": {"date": "2026-11-01"}}}],
            )
        if path == "/repos/alpha/agent/releases/latest":
            return httpx.Response(200, json={"tag_name": state["tag"]})
        if path == "/repos/alpha/agent/license":
            return httpx.Response(
                200,
                json=_encoded("MIT License\nPermission is hereby granted, free of charge."),
            )
        return httpx.Response(404, json={"message": "Not Found"})

    budget = ScanBudget(max_repos=5, max_api_calls=30)
    scanner = _scanner(tmp_path, httpx.MockTransport(handler), budget)
    repo = ["https://github.com/alpha/agent"]

    first = scanner.scan(repo, budget=budget, write_report_file=False, generated_at="2026-11-01T03:00:00")
    assert len(first.changes) == 0
    assert first.provenance[0].change_history == {"first_capture": True}

    second = scanner.scan(repo, budget=budget, write_report_file=False, generated_at="2026-11-02T03:00:00")
    assert len(second.changes) == 0
    assert second.provenance[0].change_history["reason"] == "version_unchanged"
    assert "无变化不记条目" in second.report_text

    state["tag"] = "v1.1.0"
    state["sha"] = "bbb222"
    third = scanner.scan(repo, budget=budget, write_report_file=False, generated_at="2026-11-03T03:00:00")
    assert len(third.changes) == 1
    change = third.changes[0]
    assert change.previous_version == "v1.0.0"
    assert change.current_version == "v1.1.0"
    assert change.detected_at == "2026-11-03T03:00:00"
    assert "v1.0.0" in third.report_text
    assert "v1.1.0" in third.report_text

    # 断网：必须记为未获取，不能制造「v1.1.0 → None」的假变更。
    state["fail"] = True
    fourth = scanner.scan(repo, budget=budget, write_report_file=False, generated_at="2026-11-04T03:00:00")
    assert len(fourth.changes) == 0
    assert fourth.candidates[0].last_status == "not_fetched"

    store = IntelStore(str(tmp_path / "osi.db"))
    assert len(store.list_changes("https://github.com/alpha/agent")) == 1
    store.close()
