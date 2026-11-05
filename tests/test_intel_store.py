"""SQLite 数据模型测试：7 张 osi_* 表、跨实例持久化、来源字段。"""

from __future__ import annotations

import sqlite3

from src.intel.models import (
    AdvisoryRecord,
    Candidate,
    GapRecord,
    LicenseRecord,
    ProposalRecord,
    ProvenanceRecord,
    Snapshot,
)
from src.intel.store import IntelStore

EXPECTED_TABLES = {
    "osi_candidates",
    "osi_snapshots",
    "osi_licenses",
    "osi_advisories",
    "osi_gap_analysis",
    "osi_proposals",
    "osi_provenance",
}


def test_all_seven_osi_tables_are_created(tmp_path):
    store = IntelStore(str(tmp_path / "osi.db"))
    tables = set(store.table_names())
    assert EXPECTED_TABLES.issubset(tables)
    store.close()


def test_rows_persist_across_store_instances(tmp_path):
    db_path = str(tmp_path / "osi.db")
    store = IntelStore(db_path)
    store.upsert_candidate(
        Candidate(
            repo_url="https://github.com/alpha/agent",
            name="alpha/agent",
            first_seen_at="2026-11-06T00:00:00",
            last_assessed_at="2026-11-06T00:00:00",
            language="Python",
            stars=12,
            last_status="fetched",
        )
    )
    store.insert_snapshot(
        Snapshot(
            repo_url="https://github.com/alpha/agent",
            fetched_at="2026-11-06T00:00:00",
            source_url="https://api.github.com/repos/alpha/agent",
            fetch_status="fetched",
            version="v1.0.0",
            commit_sha="abc123",
            license_raw_sha256="a" * 64,
            dependency_manifest_sha256="b" * 64,
            details=[
                {
                    "resource": "license",
                    "url": "https://api.github.com/repos/alpha/agent/license",
                    "attempted_at": "2026-11-06T00:00:00",
                    "status": "fetched",
                    "reason": None,
                    "http_status": 200,
                }
            ],
        )
    )
    store.insert_license(
        LicenseRecord(
            repo_url="https://github.com/alpha/agent",
            license_id="MIT",
            source_url="https://github.com/alpha/agent/blob/main/LICENSE",
            verdict="permissive",
            category="runtime",
            allowed=True,
            captured_at="2026-11-06T00:00:00",
            raw_sha256="c" * 64,
            reason="MIT 为宽松许可证，可用于 runtime",
        )
    )
    store.insert_advisory(
        AdvisoryRecord(
            repo_url="https://github.com/alpha/agent",
            source="github-advisory",
            source_url="https://github.com/alpha/agent/security/advisories",
            fetch_status="not_fetched",
            fetched_at="2026-11-06T00:00:00",
            error_reason="advisory_endpoint_not_wired_in_this_round",
        )
    )
    store.insert_gap(
        GapRecord(
            repo_url="https://github.com/alpha/agent",
            capability="agent-orchestration",
            status="missing",
            file_paths=[],
            assessed_at="2026-11-06T00:00:00",
            note="粗对照",
        )
    )
    store.insert_proposal(
        ProposalRecord(
            repo_url="https://github.com/alpha/agent",
            strategy="采用",
            effort="高",
            risk="低",
            license_conclusion="MIT / permissive / runtime",
            adoptable=True,
            summary="建议采用",
            created_at="2026-11-06T00:00:00",
            disclaimer="由 AI 生成，未经人工复核",
        )
    )
    store.insert_provenance(
        ProvenanceRecord(
            repo_url="https://github.com/alpha/agent",
            source="github",
            source_url="https://api.github.com/repos/alpha/agent",
            captured_at="2026-11-06T00:00:00",
            license_id="MIT",
            version="v1.0.0",
            change_history={"first_capture": True},
        )
    )
    store.close()

    reopened = IntelStore(db_path)
    assert len(reopened.list_candidates()) == 1
    assert len(reopened.list_snapshots()) == 1
    assert len(reopened.list_licenses()) == 1
    assert len(reopened.list_advisories()) == 1
    assert len(reopened.list_gaps()) == 1
    assert len(reopened.list_proposals()) == 1
    assert len(reopened.list_provenance()) == 1
    reopened.close()


def test_timestamp_and_source_url_are_stored_verbatim(tmp_path):
    db_path = str(tmp_path / "osi.db")
    store = IntelStore(db_path)
    store.insert_snapshot(
        Snapshot(
            repo_url="https://github.com/alpha/agent",
            fetched_at="2026-11-06T01:02:03",
            source_url="https://api.github.com/repos/alpha/agent/license",
            fetch_status="not_fetched",
            error_reason="network_disabled",
        )
    )
    store.close()

    with sqlite3.connect(db_path) as conn:
        row = conn.execute(
            "SELECT fetched_at, source_url, fetch_status FROM osi_snapshots"
        ).fetchone()
    assert row == (
        "2026-11-06T01:02:03",
        "https://api.github.com/repos/alpha/agent/license",
        "not_fetched",
    )
