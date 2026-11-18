"""邮件规则接线专属测试（完善第二轮 A4 裁决：接线，不删除）。

覆盖 poll_gmail_once 的三态（开关关 / Google 没接通 / 接通且命中）、
seen 去重、mailbox_status 的诚实口径、排程器 handler 注册与种子。
Gmail 走 httpx MockTransport 假传输，不碰真网、不碰真 token。
"""

from __future__ import annotations

import json

import httpx
import pytest

from src.ai import email_rules


class FakePlanStore:
    def __init__(self):
        self.plans = []

    def create_plan(self, *, title, goal, created_by, owner_account_id, steps):
        plan = {"plan_id": f"plan-{len(self.plans) + 1}", "title": title,
                "goal": goal, "created_by": created_by,
                "owner_account_id": owner_account_id, "steps": steps}
        self.plans.append(plan)
        return plan


def _gmail_transport():
    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path.endswith("/messages") :
            return httpx.Response(200, json={"messages": [{"id": "m1"}, {"id": "m2"}]})
        if path.endswith("/messages/m1"):
            return httpx.Response(200, json={
                "id": "m1", "snippet": "请处理这份报价单",
                "payload": {"headers": [
                    {"name": "From", "value": "Vendor <v@example.com>"},
                    {"name": "Subject", "value": "请处理：11 月报价单"},
                    {"name": "Date", "value": "Tue, 17 Nov 2026 09:00:00 +0000"},
                ]},
            })
        if path.endswith("/messages/m2"):
            return httpx.Response(200, json={
                "id": "m2", "snippet": "周末快乐",
                "payload": {"headers": [
                    {"name": "From", "value": "Friend <f@example.com>"},
                    {"name": "Subject", "value": "周末聚餐"},
                    {"name": "Date", "value": "Tue, 17 Nov 2026 10:00:00 +0000"},
                ]},
            })
        return httpx.Response(404, json={})

    return httpx.MockTransport(handler)


@pytest.fixture()
def wired(tmp_path, monkeypatch):
    monkeypatch.setenv("LIUHAO_DATA_DIR", str(tmp_path))
    rules_file = tmp_path / "rules.json"
    rules_file.write_text(json.dumps([
        {"id": "r1", "name": "请处理类", "subject_contains": "请处理",
         "action": "create_task", "enabled": True},
    ], ensure_ascii=False), encoding="utf-8")
    monkeypatch.setenv(email_rules.RULES_FILE_ENV, str(rules_file))
    monkeypatch.setenv(email_rules.SEEN_FILE_ENV, str(tmp_path / "seen.json"))
    return tmp_path


def test_poll_skips_when_disabled(wired, monkeypatch):
    monkeypatch.delenv("LIUHAO_EMAIL_RULES_ENABLED", raising=False)
    monkeypatch.setenv("LIUHAO_GOOGLE_ACCESS_TOKEN", "fake-token")
    out = email_rules.poll_gmail_once(transport=_gmail_transport(), plan_store=FakePlanStore())
    assert out["ran"] is False and out["reason"] == "disabled"


def test_poll_skips_when_google_not_configured(wired, monkeypatch):
    monkeypatch.setenv("LIUHAO_EMAIL_RULES_ENABLED", "1")
    monkeypatch.delenv("LIUHAO_GOOGLE_ACCESS_TOKEN", raising=False)
    out = email_rules.poll_gmail_once(transport=_gmail_transport(), plan_store=FakePlanStore())
    assert out["ran"] is False and out["reason"] == "not_configured"


def test_poll_processes_matches_and_dedupes(wired, monkeypatch):
    monkeypatch.setenv("LIUHAO_EMAIL_RULES_ENABLED", "1")
    monkeypatch.setenv("LIUHAO_GOOGLE_ACCESS_TOKEN", "fake-token")
    store = FakePlanStore()
    out = email_rules.poll_gmail_once(transport=_gmail_transport(), plan_store=store)
    assert out["ran"] is True
    assert out["fetched"] == 2 and out["processed"] == 2
    assert out["matched"] == 1  # 只有 m1 命中「请处理」
    assert len(store.plans) == 1
    assert store.plans[0]["created_by"] == "email_rule:r1"
    # 第二轮：两封都已 seen，不重复处理、不重复建计划
    out2 = email_rules.poll_gmail_once(transport=_gmail_transport(), plan_store=store)
    assert out2["processed"] == 0 and len(store.plans) == 1


def test_mailbox_status_honest_when_not_configured(wired, monkeypatch):
    monkeypatch.delenv("LIUHAO_GOOGLE_ACCESS_TOKEN", raising=False)
    status = email_rules.mailbox_status()
    assert status["connected"] is False and status["provider"] is None


def test_mailbox_status_connected_but_rules_off(wired, monkeypatch):
    monkeypatch.setenv("LIUHAO_GOOGLE_ACCESS_TOKEN", "fake-token")
    monkeypatch.delenv("LIUHAO_EMAIL_RULES_ENABLED", raising=False)
    status = email_rules.mailbox_status()
    assert status["connected"] is True and status["provider"] == "gmail"
    assert status["rules_enabled"] is False
    assert "没开" in status["note"]


def test_scheduler_registers_and_seeds_email_poll(tmp_path, monkeypatch):
    monkeypatch.setenv("LIUHAO_DATA_DIR", str(tmp_path))
    from src.tasks.local_handlers import EMAIL_POLL_JOB, build_local_scheduler

    scheduler = build_local_scheduler()
    assert EMAIL_POLL_JOB in scheduler.registered_jobs()
