"""每周扫描排程：复用 ``src.tasks.local_scheduler.LocalScheduler``。

约定严格按排程器自己的接口：``register(job_name, async_handler)`` 注册处理器，
``enqueue(job_name, run_at=..., job_key=...)`` 入队；``job_key`` 在 active 状态内
幂等，因此同一天重复 seed 不会产生第二条。

**单实例独占库**：本模块只注册/入队，不创建第二个运行循环。处理器运行结束后
用同一个 scheduler 实例续排下一周，绝不另起一个 scheduler 去抢同一个库。

真实网络默认关闭（与阶段一一致）；周报要真正联网，需要显式设置
``LIUHAO_INTEL_ALLOW_NETWORK=1``，否则本轮会如实记为「未获取」。
"""

from __future__ import annotations

import asyncio
import os
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from src._time import utc_now
from src.intel.github_client import GitHubClient
from src.intel.models import ScanBudget
from src.intel.osv_client import OsvClient
from src.intel.scanner import IntelScanner, ScanResult
from src.intel.store import IntelStore

WEEKLY_SCAN_JOB = "liuhao.intel.weekly_scan"
WEEKLY_JOB_KEY_PREFIX = "liuhao.intel.weekly_scan"
DEFAULT_INTERVAL_DAYS = 7
DEFAULT_MAX_REPOS = 20
DEFAULT_MAX_API_CALLS = 150
DEFAULT_MAX_TOKENS = 20000


@dataclass
class ScanContext:
    """一次周报扫描所需的依赖；测试可替换整个上下文。"""

    scanner: IntelScanner
    store: IntelStore
    close: Callable[[], None] = lambda: None


def default_scan_context(budget: ScanBudget, allow_network: bool) -> ScanContext:
    store = IntelStore(os.environ.get("OSI_DB_PATH") or None)
    client = GitHubClient(
        allow_network=allow_network,
        budget=budget,
        token=os.environ.get("GITHUB_TOKEN") or None,
    )
    osv = OsvClient(allow_network=allow_network, budget=budget)
    scanner = IntelScanner(store, client, osv_client=osv)

    def close() -> None:
        osv.close()
        client.close()
        store.close()

    return ScanContext(scanner=scanner, store=store, close=close)


def next_weekly_run(now: datetime) -> datetime:
    return now + timedelta(days=DEFAULT_INTERVAL_DAYS)


def weekly_job_key(run_at: datetime) -> str:
    """按 ISO 周取幂等键（``2026-W46``）。

    原来按 ``run_at.date()`` 取键：重启后 ``now + 7 天`` 会滑到不同日期，
    同一周能 seed 出两条扫描。ISO 周把"同一周"钉死，重启/重复 seed 都幂等。
    """
    iso = run_at.isocalendar()
    return f"{WEEKLY_JOB_KEY_PREFIX}:{iso.year}-W{iso.week:02d}"


def register_weekly_jobs(scheduler: Any) -> list[str]:
    """按排程器约定注册周报处理器；返回当前已注册的 job 名称。"""
    async def handler(**payload: Any) -> dict[str, Any]:
        # 扫描是阻塞 I/O；放到线程里，避免卡住排程器的心跳和租约刷新。
        return await asyncio.to_thread(execute_weekly_scan, scheduler, **payload)

    scheduler.register(WEEKLY_SCAN_JOB, handler)
    return scheduler.registered_jobs()


def seed_weekly_jobs(
    scheduler: Any,
    *,
    now: datetime | None = None,
) -> int:
    """预约下一周的一次扫描（不是立刻跑）；同一周重复调用幂等。"""
    run_at = next_weekly_run(now or scheduler_now(scheduler))
    return scheduler.enqueue(
        WEEKLY_SCAN_JOB,
        run_at=run_at,
        job_key=weekly_job_key(run_at),
        payload={},
    )


def scheduler_now(scheduler: Any) -> datetime:
    now_fn = getattr(scheduler, "_now", None)
    if callable(now_fn):
        return now_fn()
    return utc_now()


def _allow_network(explicit: bool | None) -> bool:
    if explicit is not None:
        return explicit
    return os.environ.get("LIUHAO_INTEL_ALLOW_NETWORK", "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def execute_weekly_scan(
    scheduler: Any,
    *,
    max_repos: int = DEFAULT_MAX_REPOS,
    max_api_calls: int = DEFAULT_MAX_API_CALLS,
    max_tokens: int = DEFAULT_MAX_TOKENS,
    allow_network: bool | None = None,
    context_factory: Callable[[ScanBudget, bool], ScanContext] | None = None,
    report_dir: str | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """执行一次周报扫描、续排下一周，并返回可审计的摘要。"""
    allow = _allow_network(allow_network)
    budget = ScanBudget(
        max_repos=max_repos, max_api_calls=max_api_calls, max_tokens=max_tokens
    )
    context = (context_factory or default_scan_context)(budget, allow)
    run_now = now or scheduler_now(scheduler)
    timestamp = run_now.isoformat()
    summary: dict[str, Any] = {
        "job": WEEKLY_SCAN_JOB,
        "started_at": timestamp,
        "allow_network": allow,
        "max_repos": max_repos,
        "max_api_calls": max_api_calls,
        "max_tokens": max_tokens,
    }

    try:
        tracked = [str(row["repo_url"]) for row in context.store.list_candidates()]
        if not tracked:
            summary.update(
                {
                    "skipped": "no_tracked_repos",
                    "repos_requested": 0,
                    "repos_reserved": 0,
                    "tokens_used": 0,
                    "report_path": None,
                }
            )
        else:
            report_path = None
            if report_dir:
                stamp = timestamp.replace(":", "").replace("-", "")[:15]
                report_path = os.path.join(report_dir, f"osi-weekly-{stamp}.md")
            result: ScanResult = context.scanner.scan(
                tracked,
                budget=budget,
                report_path=report_path,
                generated_at=timestamp,
            )
            proposals = result.proposals
            summary.update(
                {
                    "skipped": None,
                    "repos_requested": len(tracked),
                    "repos_reserved": budget.repos_reserved,
                    "repos_fetched": sum(
                        1 for candidate in result.candidates if candidate.last_status == "fetched"
                    ),
                    "repos_not_fetched": [
                        {
                            "repo_url": candidate.repo_url,
                            "reason": candidate.last_error or "not_fetched",
                        }
                        for candidate in result.candidates
                        if candidate.last_status == "not_fetched"
                    ],
                    "advisories": {
                        "records": len(result.advisories),
                        "no_advisory": sum(
                            1
                            for record in result.advisories
                            if record.fetch_status == "fetched" and not record.advisory_id
                        ),
                        "not_fetched": sum(
                            1 for record in result.advisories if record.fetch_status != "fetched"
                        ),
                    },
                    "changes": len(result.changes),
                    "adoptable_proposals": sum(1 for proposal in proposals if proposal.adoptable),
                    "tokens_used": budget.tokens_used,
                    "api_calls_used": budget.api_calls_used,
                    "report_path": result.report_path,
                    "quota_notes": list(result.quota_notes),
                }
            )
    finally:
        context.close()

    # 只有成功跑完才续排下一周；失败交给排程器自己的退避重试，不重复排期。
    next_run = next_weekly_run(run_now)
    job_id = scheduler.enqueue(
        WEEKLY_SCAN_JOB,
        run_at=next_run,
        job_key=weekly_job_key(next_run),
        payload={},
    )
    summary["next_job_id"] = job_id
    summary["next_run_at"] = next_run.isoformat()
    scheduler.audit_event("intel.weekly_scan", "completed", summary)
    return summary


__all__ = [
    "DEFAULT_INTERVAL_DAYS",
    "ScanContext",
    "WEEKLY_SCAN_JOB",
    "default_scan_context",
    "execute_weekly_scan",
    "next_weekly_run",
    "register_weekly_jobs",
    "seed_weekly_jobs",
    "weekly_job_key",
]
