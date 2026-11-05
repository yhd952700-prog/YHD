"""每周排程测试：按 LocalScheduler 的注册/入队约定，幂等续排，配额耗尽不编造。"""

from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path

from src.intel.github_client import GitHubClient
from src.intel.models import Candidate, ScanBudget
from src.intel.osv_client import OsvClient
from src.intel.scanner import IntelScanner, ScanResult
from src.intel.scheduling import (
    WEEKLY_SCAN_JOB,
    ScanContext,
    execute_weekly_scan,
    register_weekly_jobs,
    seed_weekly_jobs,
    weekly_job_key,
)
from src.intel.store import IntelStore
from src.tasks.local_scheduler import LocalScheduler

_NOW = datetime(2026, 11, 6, 3, 0, 0)


def _scheduler(tmp_path) -> LocalScheduler:
    return LocalScheduler(
        str(tmp_path / "queue" / "local_scheduler.db"),
        now_fn=lambda: _NOW,
        audit_sink=lambda *args: None,
    )


def _tracked_candidate() -> Candidate:
    return Candidate(
        repo_url="https://github.com/alpha/agent",
        name="alpha/agent",
        first_seen_at="2026-11-01T03:00:00",
        last_assessed_at="2026-11-01T03:00:00",
        last_status="fetched",
    )


def _seed_store(tmp_path) -> IntelStore:
    store = IntelStore(str(tmp_path / "osi.db"))
    store.upsert_candidate(_tracked_candidate())
    return store


def test_register_and_seed_follow_local_scheduler_conventions(tmp_path):
    scheduler = _scheduler(tmp_path)
    registered = register_weekly_jobs(scheduler)
    assert WEEKLY_SCAN_JOB in registered
    assert WEEKLY_SCAN_JOB in scheduler.registered_jobs()

    first_id = seed_weekly_jobs(scheduler, now=_NOW)
    job = scheduler.get_job(first_id)
    assert job["job_name"] == WEEKLY_SCAN_JOB
    assert job["status"] == "pending"
    assert datetime.fromisoformat(job["run_at"]) == _NOW + timedelta(days=7)
    assert job["job_key"] == weekly_job_key(_NOW + timedelta(days=7))

    # job_key 在 active 状态内幂等：同一天重复 seed 不会产生第二条。
    second_id = seed_weekly_jobs(scheduler, now=_NOW)
    assert second_id == first_id
    assert len(scheduler.list_jobs()) == 1
    scheduler.close()


def test_weekly_scan_runs_and_chains_next_week(tmp_path):
    scheduler = _scheduler(tmp_path)
    register_weekly_jobs(scheduler)
    store = _seed_store(tmp_path)
    report_dir = tmp_path / "reports"

    class FakeScanner:
        def __init__(self) -> None:
            self.calls = 0

        def scan(self, repo_urls, *, budget, report_path=None, generated_at=None):
            self.calls += 1
            candidate = _tracked_candidate()
            return ScanResult(
                repo_urls=list(repo_urls),
                candidates=[candidate],
                report_text="fake-report",
                report_path=str(report_path) if report_path else None,
            )

    fake = FakeScanner()

    def factory(budget: ScanBudget, allow_network: bool) -> ScanContext:
        return ScanContext(scanner=fake, store=store, close=lambda: None)

    summary = execute_weekly_scan(
        scheduler,
        context_factory=factory,
        report_dir=str(report_dir),
        allow_network=False,
        now=_NOW,
    )

    assert fake.calls == 1
    assert summary["repos_requested"] == 1
    assert summary["repos_fetched"] == 1
    assert summary["tokens_used"] == 0
    assert summary["allow_network"] is False
    assert summary["next_run_at"] == (_NOW + timedelta(days=7)).isoformat()

    next_job = scheduler.get_job(summary["next_job_id"])
    assert next_job["status"] == "pending"
    assert datetime.fromisoformat(next_job["run_at"]) == _NOW + timedelta(days=7)
    # 同一周内再次执行不会叠加第二条 next job。
    again = execute_weekly_scan(
        scheduler,
        context_factory=factory,
        report_dir=str(report_dir),
        allow_network=False,
        now=_NOW + timedelta(seconds=30),
    )
    assert again["next_job_id"] == summary["next_job_id"]
    assert len(scheduler.list_jobs()) == 1

    store.close()
    scheduler.close()


def test_weekly_scan_skips_when_no_tracked_repos_but_still_schedules(tmp_path):
    scheduler = _scheduler(tmp_path)
    register_weekly_jobs(scheduler)
    store = IntelStore(str(tmp_path / "osi.db"))

    class NeverCalled:
        def scan(self, *args, **kwargs):
            raise AssertionError("没有跟踪仓库时不应发起扫描")

    def factory(budget: ScanBudget, allow_network: bool) -> ScanContext:
        return ScanContext(scanner=NeverCalled(), store=store, close=lambda: None)

    summary = execute_weekly_scan(scheduler, context_factory=factory, now=_NOW)
    assert summary["skipped"] == "no_tracked_repos"
    assert summary["repos_requested"] == 0
    assert summary["tokens_used"] == 0
    assert scheduler.get_job(summary["next_job_id"])["status"] == "pending"

    store.close()
    scheduler.close()


def test_quota_exhausted_weekly_report_does_not_fabricate_entries(tmp_path):
    scheduler = _scheduler(tmp_path)
    register_weekly_jobs(scheduler)
    store = _seed_store(tmp_path)
    report_dir = tmp_path / "reports"

    def factory(budget: ScanBudget, _allow_network: bool) -> ScanContext:
        client = GitHubClient(allow_network=False, budget=budget)
        osv = OsvClient(allow_network=False, budget=budget)
        scanner = IntelScanner(
            store, client, repo_root=tmp_path / "empty", osv_client=osv
        )
        return ScanContext(
            scanner=scanner,
            store=store,
            close=lambda: (client.close(), osv.close()),
        )

    summary = execute_weekly_scan(
        scheduler,
        context_factory=factory,
        report_dir=str(report_dir),
        allow_network=False,
        max_repos=0,
        max_api_calls=5,
        now=_NOW,
    )

    assert summary["repos_requested"] == 1
    assert summary["repos_reserved"] == 0
    assert summary["repos_fetched"] == 0
    assert summary["adoptable_proposals"] == 0
    assert summary["tokens_used"] == 0
    assert summary["repos_not_fetched"][0]["reason"] == "quota_exhausted"

    report = Path(summary["report_path"]).read_text(encoding="utf-8")
    assert "仓库数量配额用尽" in report
    assert "配额耗尽后本轮不再追加任何候选、公告或建议条目" in report
    adopt_section = report.split("## 建议采用 / 改造")[1].split("## 不建议采用")[0]
    assert "- **" not in adopt_section
    assert "由 AI 生成，未经人工复核" in report

    store.close()
    scheduler.close()
