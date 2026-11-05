"""OSV 安全公告测试：查询成功/无公告/查询失败/包名无法解析必须走不同分支。"""

from __future__ import annotations

import base64
import json
import sqlite3

import httpx

from src.intel.models import AdvisoryRecord, ScanBudget
from src.intel.osv_client import (
    OSV_QUERY_URL,
    OsvClient,
    advisory_records,
    resolve_package,
    unresolved_advisory,
    version_for_osv,
)
from src.intel.scanner import IntelScanner
from src.intel.store import IntelStore


def _encoded(text: str) -> dict:
    return {
        "encoding": "base64",
        "content": base64.b64encode(text.encode("utf-8")).decode("ascii"),
        "html_url": "https://github.com/example/repo/blob/main/MANIFEST",
    }


def test_resolve_package_from_supported_manifests():
    pyproject = '[project]\nname = "My_Agent.Core"\n'
    assert resolve_package(pyproject, "pyproject.toml").ecosystem == "PyPI"
    assert resolve_package(pyproject, "pyproject.toml").name == "my-agent-core"

    package_json = '{"name": "@scope/agent", "version": "1.0.0"}'
    assert resolve_package(package_json, "package.json").name == "@scope/agent"
    assert resolve_package(package_json, "package.json").ecosystem == "npm"

    go_mod = "module github.com/example/agent\n\ngo 1.22\n"
    assert resolve_package(go_mod, "go.mod").ecosystem == "Go"
    assert resolve_package(go_mod, "go.mod").name == "github.com/example/agent"

    cargo = '[package]\nname = "agent-core"\nversion = "0.1.0"\n'
    assert resolve_package(cargo, "Cargo.toml").ecosystem == "crates.io"


def test_resolve_package_never_guesses_from_repo_name():
    assert resolve_package('{"dependencies": {"left-pad": "1.0.0"}}', "package.json") is None
    assert resolve_package("not toml at all", "pyproject.toml") is None
    assert resolve_package('{"name": "x"}', "README.md") is None


def test_version_for_osv_only_uses_real_tags():
    assert version_for_osv("v1.2.0", "abc123") == "1.2.0"
    assert version_for_osv(None, "abc123") is None


def test_no_vulns_is_fetched_with_no_advisory_note():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"vulns": []})

    budget = ScanBudget(max_api_calls=5)
    client = OsvClient(transport=httpx.MockTransport(handler), allow_network=True, budget=budget)
    package = resolve_package('[project]\nname = "agent"\n', "pyproject.toml")
    result = client.query(package)

    assert result.status == "fetched"
    assert result.vulns == []
    records = advisory_records(result, repo_url="https://github.com/alpha/agent", package=package)
    assert len(records) == 1
    assert records[0].fetch_status == "fetched"
    assert records[0].advisory_id is None
    assert "无公告记录" in records[0].note
    assert "不代表该仓库安全" in records[0].note
    assert budget.api_calls_used == 1


def test_vulns_are_written_with_range_severity_and_timestamp():
    vuln = {
        "id": "GHSA-aaaa-bbbb-cccc",
        "aliases": ["CVE-2026-0001"],
        "summary": "Remote crash",
        "database_specific": {"severity": "HIGH"},
        "affected": [
            {
                "package": {"ecosystem": "PyPI", "name": "agent"},
                "ranges": [
                    {
                        "type": "ECOSYSTEM",
                        "events": [{"introduced": "0"}, {"fixed": "1.2.1"}],
                    }
                ],
            }
        ],
    }

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"vulns": [vuln]})

    package = resolve_package('[project]\nname = "agent"\n', "pyproject.toml")
    client = OsvClient(transport=httpx.MockTransport(handler), allow_network=True)
    result = client.query(package, version="1.2.0")
    records = advisory_records(result, repo_url="https://github.com/alpha/agent", package=package)

    assert len(records) == 1
    record = records[0]
    assert record.advisory_id == "GHSA-aaaa-bbbb-cccc"
    assert record.affected_range == "PyPI:agent ECOSYSTEM(introduced:0, fixed:1.2.1)"
    assert record.severity == "HIGH"
    assert record.ecosystem == "PyPI"
    assert record.package == "agent"
    assert record.fetch_status == "fetched"
    assert record.fetched_at
    assert record.source_url == OSV_QUERY_URL


def test_query_failure_is_not_fetched_not_no_advisory():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, json={"message": "unavailable"})

    package = resolve_package('[project]\nname = "agent"\n', "pyproject.toml")
    client = OsvClient(transport=httpx.MockTransport(handler), allow_network=True)
    result = client.query(package)
    records = advisory_records(result, repo_url="https://github.com/alpha/agent", package=package)

    assert result.status == "not_fetched"
    assert records[0].fetch_status == "not_fetched"
    assert records[0].error_reason == "api_error:503"
    assert records[0].advisory_id is None
    assert "查询失败" in records[0].note
    assert "无公告记录" not in records[0].note


def test_network_disabled_and_quota_exhausted_are_not_fetched():
    package = resolve_package('[project]\nname = "agent"\n', "pyproject.toml")
    offline = OsvClient(allow_network=False)
    assert offline.query(package).reason == "network_disabled"

    exhausted = OsvClient(
        transport=httpx.MockTransport(lambda request: httpx.Response(200, json={"vulns": []})),
        allow_network=True,
        budget=ScanBudget(max_api_calls=1, api_calls_used=1),
    )
    result = exhausted.query(package)
    assert result.status == "not_fetched"
    assert result.reason == "quota_exhausted"


def test_unresolved_package_is_explicitly_not_queried():
    record = unresolved_advisory(
        "https://github.com/gamma/unknown",
        "package_unresolved",
        fetched_at="2026-11-06T03:00:00",
    )
    assert record.fetch_status == "not_fetched"
    assert record.error_reason == "package_unresolved"
    assert "未查询" in record.note
    assert "不能视为安全" in record.note


def test_old_phase_one_database_is_migrated_without_losing_rows(tmp_path):
    db_path = tmp_path / "old.db"
    conn = sqlite3.connect(db_path)
    conn.execute(
        """
        CREATE TABLE osi_advisories (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            repo_url TEXT NOT NULL,
            source TEXT NOT NULL,
            advisory_id TEXT,
            affected_range TEXT,
            severity TEXT,
            source_url TEXT NOT NULL,
            fetch_status TEXT NOT NULL,
            fetched_at TEXT NOT NULL,
            error_reason TEXT
        )
        """
    )
    conn.execute(
        """
        INSERT INTO osi_advisories (
            repo_url, source, advisory_id, affected_range, severity,
            source_url, fetch_status, fetched_at, error_reason
        ) VALUES ('https://github.com/old/repo', 'github-advisory', NULL, NULL, NULL,
                  'https://api.github.com', 'not_fetched', '2026-11-01', 'legacy')
        """
    )
    conn.commit()
    conn.close()

    store = IntelStore(str(db_path))
    columns = {row[1] for row in store._conn.execute("PRAGMA table_info(osi_advisories)")}
    assert {"ecosystem", "package", "note"}.issubset(columns)
    assert "osi_changes" in store.table_names()

    store.insert_advisory(
        AdvisoryRecord(
            repo_url="https://github.com/old/repo",
            source="OSV",
            source_url=OSV_QUERY_URL,
            fetch_status="fetched",
            fetched_at="2026-11-06",
            ecosystem="PyPI",
            package="old",
            note="无公告记录（不代表安全）",
        )
    )
    rows = store.list_advisories("https://github.com/old/repo")
    assert len(rows) == 2
    assert rows[0]["error_reason"] == "legacy"
    assert rows[1]["ecosystem"] == "PyPI"
    assert rows[1]["note"].startswith("无公告记录")
    store.close()


def test_scanner_wires_osv_and_keeps_empty_and_failure_apart(tmp_path):
    manifests = {
        "alpha/agent": ("pyproject.toml", '[project]\nname = "agent"\n'),
        "delta/clean": ("package.json", '{"name": "delta"}'),
        "zeta/failing": ("pyproject.toml", '[project]\nname = "failpkg"\n'),
    }
    seen_queries: list[dict] = []

    def github_handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        for name in ("alpha/agent", "delta/clean", "zeta/failing"):
            if path == f"/repos/{name}":
                return httpx.Response(200, json={"full_name": name, "language": "Python"})
            if path == f"/repos/{name}/commits":
                return httpx.Response(
                    200,
                    json=[{"sha": f"{name.split('/')[0]}sha", "commit": {"committer": {"date": "2026-11-01"}}}],
                )
            if path == f"/repos/{name}/releases/latest":
                return httpx.Response(200, json={"tag_name": "v1.2.0"})
            if path == f"/repos/{name}/license":
                return httpx.Response(
                    200,
                    json=_encoded(
                        "MIT License\nPermission is hereby granted, free of charge.\n"
                        "THE SOFTWARE IS PROVIDED AS IS."
                    ),
                )
        for name, (filename, content) in manifests.items():
            if path == f"/repos/{name}/contents/{filename}":
                return httpx.Response(200, json=_encoded(content))
        return httpx.Response(404, json={"message": "Not Found"})

    vuln = {
        "id": "GHSA-test-0001",
        "database_specific": {"severity": "MODERATE"},
        "affected": [
            {
                "package": {"ecosystem": "PyPI", "name": "agent"},
                "ranges": [{"type": "ECOSYSTEM", "events": [{"fixed": "1.2.1"}]}],
            }
        ],
    }

    def osv_handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        seen_queries.append(body)
        if body["package"]["name"] == "failpkg":
            return httpx.Response(500, json={"message": "boom"})
        if body["package"]["name"] == "agent":
            return httpx.Response(200, json={"vulns": [vuln]})
        return httpx.Response(200, json={"vulns": []})

    budget = ScanBudget(max_repos=10, max_api_calls=60)
    store = IntelStore(str(tmp_path / "osi.db"))
    from src.intel.github_client import GitHubClient

    client = GitHubClient(
        transport=httpx.MockTransport(github_handler), allow_network=True, budget=budget
    )
    osv = OsvClient(transport=httpx.MockTransport(osv_handler), allow_network=True, budget=budget)
    scanner = IntelScanner(store, client, repo_root=tmp_path / "empty", osv_client=osv)
    result = scanner.scan(
        [
            "https://github.com/alpha/agent",
            "https://github.com/delta/clean",
            "https://github.com/zeta/failing",
        ],
        budget=budget,
        write_report_file=False,
    )

    by_repo: dict[str, list] = {}
    for record in result.advisories:
        by_repo.setdefault(record.repo_url, []).append(record)

    alpha = by_repo["https://github.com/alpha/agent"][0]
    assert alpha.advisory_id == "GHSA-test-0001"
    assert alpha.severity == "MODERATE"
    assert alpha.affected_range == "PyPI:agent ECOSYSTEM(fixed:1.2.1)"
    assert alpha.fetch_status == "fetched"
    assert alpha.fetched_at

    delta = by_repo["https://github.com/delta/clean"][0]
    assert delta.fetch_status == "fetched"
    assert delta.advisory_id is None
    assert "无公告记录" in delta.note

    zeta = by_repo["https://github.com/zeta/failing"][0]
    assert zeta.fetch_status == "not_fetched"
    assert zeta.error_reason == "api_error:500"
    assert "查询失败" in zeta.note
    assert zeta.advisory_id is None

    agent_query = next(item for item in seen_queries if item["package"]["name"] == "agent")
    assert agent_query["version"] == "1.2.0"

    text = result.report_text
    assert "已查询·无公告记录" in text
    assert "api_error:500" in text
    assert "不代表该仓库安全" in text
    store.close()
    osv.close()
    client.close()
