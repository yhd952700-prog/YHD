"""任务台账（SQLite）回归：七态映射、写穿透、迁移演练、可见性与热路径。

覆盖用户明确要求的三条加严：

1. 写穿透失败**不许拖垮热路径**：台账故障只计数，目标创建仍然成功；
2. 迁移在**真实形态**数据上演练（热文件 + 全量归档分片 + ``.archive-index-*.jsonl``），
   并分别报告 goal / step / skipped（含原因）；
3. ``GET /v1/tasks`` 与 ``/v1/goals`` **同一套可见范围**：非管理员查别人的任务一律 404。

另外覆盖 ``/v1/goals`` 新增的 ``completed_at`` 与 ``visible_scope`` 语义。
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Dict, List, Optional

import pytest
from fastapi.testclient import TestClient

from src.ai.agent_runtime import AgentRunState
from src.gateway.ai_management import AIStateManager, GoalViewer
from src.gateway.main import get_app
from src.gateway.task_ledger import (
    ALL_STATUSES,
    LedgerError,
    LedgerStatus,
    TaskLedger,
    map_goal_state,
    map_step_status,
    reset_task_ledger_instances,
)


# ─── fixtures / helpers ────────────────────────────────────────

@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    monkeypatch.setenv("LIUHAO_WORKSPACE_ROOT", str(tmp_path))
    monkeypatch.delenv("LIUHAO_TASK_LEDGER_DB", raising=False)
    monkeypatch.delenv("LIUHAO_DEFAULT_ACCOUNT_ID", raising=False)
    AIStateManager._instance = None
    reset_task_ledger_instances()
    yield
    reset_task_ledger_instances()
    AIStateManager._instance = None


@pytest.fixture
def db_path(tmp_path) -> str:
    return str(tmp_path / "liuhao_tasks.db")


@pytest.fixture
def ledger(db_path) -> TaskLedger:
    return TaskLedger(db_path)


@pytest.fixture
def client() -> TestClient:
    return TestClient(get_app())


def _headers(subject: str) -> dict:
    """网关同一套 JWT 签发器：sub = 账号名。"""
    from src.security import get_jwt_handler

    token, _payload = get_jwt_handler().create_token(
        subject=subject, metadata={"kind": "human", "scope": "L0"}
    )
    return {"Authorization": f"Bearer {token}"}


def _make_tree(root: str = "boss", members=("alice", "bob")) -> None:
    from src.kernels.identity import get_identity_manager

    manager = get_identity_manager()
    manager.register_root_account(root)
    for member in members:
        manager.create_member_account(actor=root, principal=member)


def _goal(
    goal_id: str,
    state: str = "completed",
    *,
    owner: str = "alice",
    tasks: Optional[List[Dict]] = None,
    **extra,
) -> Dict:
    entry = {
        "goal_id": goal_id,
        "state": state,
        "natural_language": f"目标 {goal_id}",
        "scope": "L1",
        "created_at": 1000.0,
        "tasks": list(tasks or []),
        "artifacts": [],
        "owner_account_id": owner,
    }
    entry.update(extra)
    return entry


def _seed_and_project(manager: AIStateManager, entry: Dict) -> None:
    """模拟既有落盘目标：进内存 + 落盘 + 写穿透到台账。"""
    with manager._goal_lock:
        manager._goals[entry["goal_id"]] = entry
    manager._save_goals()
    manager._project_to_ledger(entry)


class _BrokenLedger:
    """台账故障替身：所有写都抛错，用来证明热路径 fail-soft。"""

    sync_goal_calls = 0
    note_failure_calls = 0

    def sync_goal(self, entry, *, default_owner=""):
        type(self).sync_goal_calls += 1
        raise sqlite3_operational_error()

    def note_write_failure(self, exc):
        type(self).note_failure_calls += 1


def sqlite3_operational_error() -> Exception:
    import sqlite3

    return sqlite3.OperationalError("database is locked (simulated)")


class _StubRuntime:
    def run_goal(self, **kwargs):
        goal_id = kwargs.get("goal_id") or "stub"
        return SimpleNamespace(
            goal_id=goal_id,
            state=SimpleNamespace(value="completed"),
            correlation_id=f"stub-{goal_id}",
            error=None,
            replan_count=0,
            replan_suggested=False,
            memory_keys=[],
            persist_errors=[],
            trace=None,
            context=None,
            evaluation=None,
        )


# ─── 七态映射 ──────────────────────────────────────────────────

class TestSevenStateMapping:
    def test_every_agent_run_state_maps_into_the_seven_states(self):
        assert len(AgentRunState) == 8  # created/planning/executing/waiting/verifying/...
        expected = {
            "created": LedgerStatus.PENDING_ASSIGNMENT,
            "planning": LedgerStatus.PENDING,
            "executing": LedgerStatus.RUNNING,
            "waiting": LedgerStatus.PENDING,
            "verifying": LedgerStatus.REVIEWING,
            "completed": LedgerStatus.COMPLETED,
            "failed": LedgerStatus.FAILED,
            "cancelled": LedgerStatus.CANCELLED,
        }
        for state in AgentRunState:
            assert map_goal_state(state.value) is expected[state.value], state
        # 只投影到七态，不发明第三套状态
        assert {item.value for item in LedgerStatus} == set(ALL_STATUSES)

    def test_step_mapping_shares_the_same_table(self):
        assert map_step_status("running") is LedgerStatus.RUNNING
        assert map_step_status("verifying") is LedgerStatus.REVIEWING
        # 内核里从未开始、也没人认领的步骤 = 待分配
        assert map_step_status("draft", has_assignee=False) is LedgerStatus.PENDING_ASSIGNMENT
        assert map_step_status("draft", has_assignee=True) is LedgerStatus.PENDING


# ─── 写穿透 / 幂等 / 查询 ──────────────────────────────────────

class TestLedgerProjection:
    def test_sync_goal_is_idempotent_and_records_transitions(self, ledger):
        entry = _goal(
            "G-1", "running",
            tasks=[{"id": "s1", "name": "执行", "status": "running"}],
        )
        first = ledger.sync_goal(entry)
        assert first["tasks"] == 1 and first["steps"] == 1
        runs_after_first = len(ledger.list_runs("G-1"))

        # 同一份记录再投影：不新增任务/步骤/执行记录
        again = ledger.sync_goal(entry)
        assert again == {"tasks": 0, "steps": 0, "runs": 0}
        assert len(ledger.list_runs("G-1")) == runs_after_first

        # 状态迁移才追加执行记录
        entry["state"] = "completed"
        entry["tasks"][0]["status"] = "completed"
        moved = ledger.sync_goal(entry)
        assert moved["tasks"] == 1 and moved["steps"] == 1
        events = [run["event"] for run in ledger.list_runs("G-1")]
        assert "finished" in events or "status_change" in events
        assert "created" in events

    def test_list_filters_and_pagination(self, ledger):
        ledger.sync_goal(_goal("G-A", "running", tasks=[
            {"id": "a1", "name": "a1", "status": "running", "assigned_employee": "alice"},
        ]))
        ledger.sync_goal(_goal("G-B", "failed", owner="bob",
                               assigned_employee="bob", error="boom"))
        ledger.sync_goal(_goal("G-C", "cancelled", owner="alice"))

        assert ledger.list_tasks(status="running")["total"] == 1
        assert ledger.list_tasks(assignee_name="bob")["total"] == 1
        assert ledger.list_tasks(goal_id="G-C")["total"] == 1
        assert ledger.list_tasks(owners=["bob"])["total"] == 1
        assert ledger.list_tasks(owners=["alice"])["total"] == 2

        page = ledger.list_tasks(limit=1, offset=1, sort_by="created_at", sort_order="asc")
        assert page["total"] == 3 and len(page["items"]) == 1


# ─── 迁移演练（真实形态：热文件 + 全量分片 + 索引分片）──────────

def _write_production_shaped_source(directory) -> str:
    hot = directory / "liuhao_goals.json"
    hot.write_text(json.dumps({
        "G-HOT-RUN": _goal("G-HOT-RUN", "running", tasks=[
            {"id": "hot-1", "name": "拆解", "status": "completed",
             "assigned_employee": "alice", "completed_at": 1001.0},
            {"id": "hot-2", "name": "执行", "status": "running",
             "assigned_employee": "alice", "started_at": 1002.0},
        ], started_at=1001.0),
        "G-HOT-FAIL": _goal("G-HOT-FAIL", "failed", error="写文件失败", created_at=2000.0,
                            tasks=[{"id": "hot-3", "name": "写报告", "status": "failed",
                                    "error": "写文件失败", "completed_at": 2001.0}]),
    }), encoding="utf-8")

    full = directory / "liuhao_goals.json.archive-20260101.jsonl"
    full.write_text("\n".join(json.dumps(item) for item in [
        _goal("G-ARCH-1", "completed", created_at=500.0, tasks=[
            {"id": "a1", "name": "a1", "status": "completed"},
            {"id": "a2", "name": "a2", "status": "completed"},
        ]),
        _goal("G-ARCH-2", "completed", created_at=600.0, tasks=[
            {"name": "缺少 id 的历史步骤", "status": "completed"},
            {"id": "a3", "name": "a3", "status": "completed"},
        ]),
        # 真实归档里可能出现没有 goal_id 的坏行：必须计入 skipped 原因
        {"state": "completed", "natural_language": "没有 goal_id 的坏记录"},
    ]) + "\n", encoding="utf-8")

    index = directory / "liuhao_goals.json.archive-index-20260101.jsonl"
    index.write_text("\n".join(json.dumps(item) for item in [
        {"goal_id": "G-IDX-1", "state": "completed",
         "natural_language": "归档索引目标 1", "created_at": 10.0},
        {"goal_id": "G-IDX-2", "state": "cancelled",
         "natural_language": "归档索引目标 2", "created_at": 11.0},
        # 全量分片里已有：索引不得覆盖全量（去重口径：热 > 全量 > 索引）
        {"goal_id": "G-ARCH-1", "state": "completed", "natural_language": "被去重"},
    ]) + "\n", encoding="utf-8")
    return str(hot)


class TestMigrationDrill:
    def test_migration_drill_reports_goals_steps_and_skip_reasons(self, tmp_path, ledger):
        goals_path = _write_production_shaped_source(tmp_path)
        result = ledger.migrate_from_goals(goals_path, default_owner="alice")

        assert result["status"] == "completed"
        # ── 三个必须报告的数字 ──
        assert result["entries"] == 7, "热文件 2 + 全量 3 + 索引 3，去重后 7"
        assert result["tasks_imported"] == 6
        assert result["steps_imported"] == 6
        assert result["skipped"] == 2
        assert result["skipped_reasons"] == {
            "missing_goal_id": 1,
            "step_missing_id": 1,
        }

        # 全量分片优先于索引：G-ARCH-1 来自全量（有子步骤），不是索引摘要
        assert len(ledger.list_steps("G-ARCH-1")) == 2
        # 索引独有的老目标如实导入为"没有子步骤"的任务卡（不编造步骤）
        assert ledger.get_task("G-IDX-1")["status"] == "completed"
        assert ledger.list_steps("G-IDX-1") == []

        report = ledger.consistency(goals_path)
        assert report["source_goals"] == 6
        assert report["ledger_tasks"] == 6
        assert report["missing_in_ledger"] == []
        assert report["state_mismatched"] == []
        assert report["consistent"] is True

    def test_migration_is_idempotent_and_rollback_is_repeatable(self, tmp_path, ledger):
        goals_path = _write_production_shaped_source(tmp_path)
        first = ledger.migrate_from_goals(goals_path)
        assert first["status"] == "completed"

        # 同一源指纹：第二次直接 skipped，不再解析归档
        second = ledger.migrate_from_goals(goals_path)
        assert second["status"] == "skipped"
        assert second["reason"] == "source unchanged since last completed migration"
        assert second["tasks_imported"] == first["tasks_imported"]

        # force 重跑：不覆盖活数据，只把已有条目计为 skipped 并说明原因
        forced = ledger.migrate_from_goals(goals_path, force=True)
        assert forced["status"] == "completed"
        assert forced["tasks_imported"] == 0
        assert forced["skipped_reasons"]["already_in_ledger"] == 6
        assert forced["skipped_reasons"]["missing_goal_id"] == 1

        # 回滚：只删该次导入的台账行，源文件一字不动
        source_bytes = open(goals_path, "rb").read()
        rolled = ledger.rollback_migration(first["migration_id"])
        assert rolled["tasks_deleted"] == 6
        assert ledger.summary()["counts"]["tasks"] == 0
        assert open(goals_path, "rb").read() == source_bytes

        # 回滚后可再次迁移（不是一次性单向操作）
        again = ledger.migrate_from_goals(goals_path)
        assert again["status"] == "completed"
        assert again["tasks_imported"] == 6


# ─── 人工接管 / 失败重试 ───────────────────────────────────────

class TestHumanActions:
    def test_takeover_moves_to_human_hold_and_survives_auto_projection(self, ledger):
        entry = _goal("G-T", "failed", tasks=[
            {"id": "t1", "name": "写报告", "status": "failed"},
        ])
        ledger.sync_goal(entry)
        task = ledger.takeover("G-T", principal="alice", reason="人工接手")
        assert task["status"] == "pending"
        assert task["assignee"] == {"type": "human", "id": "alice", "name": "alice"}
        assert task["human_hold"] is True

        # 自动投影不得把接管改回去（hold 生效）
        ledger.sync_goal(dict(entry))
        assert ledger.get_task("G-T")["status"] == "pending"

        # 真实执行重新开始（running）才解除保持语义
        running = dict(entry)
        running["state"] = "running"
        running["tasks"] = [{"id": "t1", "name": "写报告", "status": "running"}]
        ledger.sync_goal(running)
        assert ledger.get_task("G-T")["status"] == "running"

    def test_takeover_rejects_running_completed_and_cancelled(self, ledger):
        for goal_id, state, code in (
            ("G-RUN", "running", "task_running"),
            ("G-DONE", "completed", "task_completed"),
            ("G-CANCEL", "cancelled", "task_cancelled"),
        ):
            ledger.sync_goal(_goal(goal_id, state))
            with pytest.raises(LedgerError) as info:
                ledger.takeover(goal_id, principal="alice")
            assert info.value.status_code == 409
            assert info.value.code == code


# ─── 写穿透 fail-soft（不能拖垮热路径）─────────────────────────

class TestHotPathFailSoft:
    def test_projection_failure_is_counted_and_never_raised(self):
        manager = AIStateManager()
        manager._task_ledger_instance = _BrokenLedger()
        before = _BrokenLedger.sync_goal_calls

        manager._project_to_ledger(_goal("G-BROKEN"))

        assert _BrokenLedger.sync_goal_calls == before + 1
        assert _BrokenLedger.note_failure_calls >= 1
        assert manager._ledger_projection_failures >= 1
        assert "OperationalError" in manager._ledger_last_error

    def test_goal_creation_still_succeeds_when_ledger_is_down(self, client):
        AIStateManager._instance = None
        manager = AIStateManager()
        manager._runtime = _StubRuntime()
        manager._ensure_employee()
        manager._task_ledger_instance = _BrokenLedger()

        resp = client.post(
            "/v1/goals",
            headers=_headers("alice"),
            json={"natural_language": "台账故障也要能建目标"},
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["state"] == "completed"
        # 目标真的落盘了（台账缺一条 ≠ 目标创建失败）
        assert manager.get_goal(resp.json()["goal_id"]) is not None
        assert manager._ledger_projection_failures >= 1


# ─── HTTP：可见性与 /v1/goals 新字段 ────────────────────────────

class TestTaskApiVisibility:
    def test_non_admin_cannot_see_others_tasks(self, client, db_path):
        _make_tree()
        manager = AIStateManager()
        _seed_and_project(manager, _goal("G-ALICE", "failed", tasks=[
            {"id": "t1", "name": "写报告", "status": "failed"},
        ]))
        assert manager.task_ledger() is not None
        assert manager.task_ledger().get_task("G-ALICE") is not None

        bob = _headers("bob")
        listed = client.get("/v1/tasks", headers=bob)
        assert listed.status_code == 200
        assert listed.json()["total"] == 0
        assert listed.json()["items"] == []
        assert listed.json()["visible_scope"] == "self"

        # 别人的任务与不存在的任务：同一 404，不泄露存在性
        visible = client.get("/v1/tasks/G-ALICE", headers=bob)
        missing = client.get("/v1/tasks/G-NOBODY", headers=bob)
        assert visible.status_code == missing.status_code == 404
        assert visible.json()["code"] == "task_not_found"
        assert visible.json()["code"] == missing.json()["code"]
        assert visible.json()["message"].replace("G-ALICE", "<id>") == \
            missing.json()["message"].replace("G-NOBODY", "<id>")

        for path in (
            "/v1/tasks/G-ALICE/steps",
            "/v1/tasks/G-ALICE/runs",
        ):
            assert client.get(path, headers=bob).status_code == 404, path
        for path in (
            "/v1/tasks/G-ALICE/takeover",
            "/v1/tasks/G-ALICE/retry",
        ):
            assert client.post(path, headers=bob).status_code == 404, path

        # 自己看得见
        alice = _headers("alice")
        own = client.get("/v1/tasks", headers=alice)
        assert own.json()["total"] == 1
        assert own.json()["items"][0]["goal_id"] == "G-ALICE"
        assert client.get("/v1/tasks/G-ALICE", headers=alice).status_code == 200

        # 主账号看子树（只读汇总口径与 goals 一致）
        boss = client.get("/v1/tasks", headers=_headers("boss"))
        assert {item["goal_id"] for item in boss.json()["items"]} == {"G-ALICE"}
        assert boss.json()["visible_scope"] == "subtree"

        # 运维自检仅根账号
        assert client.get("/v1/tasks/ledger/status", headers=bob).status_code == 403
        assert client.get("/v1/tasks/ledger/status", headers=_headers("boss")).status_code == 200

    def test_takeover_then_auto_retry_is_rejected(self, client):
        _make_tree()
        manager = AIStateManager()
        _seed_and_project(manager, _goal("G-TAKE", "failed", tasks=[
            {"id": "t1", "name": "写报告", "status": "failed"},
        ]))
        alice = _headers("alice")

        taken = client.post("/v1/tasks/G-TAKE/takeover", headers=alice, json={"reason": "接手"})
        assert taken.status_code == 200
        assert taken.json()["task"]["human_hold"] is True

        retried = client.post("/v1/tasks/G-TAKE/retry", headers=alice)
        assert retried.status_code == 409
        assert retried.json()["code"] == "human_takeover_active"

    def test_goals_completed_at_and_visible_scope(self, client):
        _make_tree()
        manager = AIStateManager()
        # 老数据：终态但没有 completed_at —— 读侧必须留空，不能造时间
        legacy = _goal("G-LEGACY", "completed")
        legacy.pop("completed_at", None)
        _seed_and_project(manager, legacy)

        alice = _headers("alice")
        body = client.get("/v1/goals", headers=alice).json()
        assert body["visible_scope"] == "self"
        assert body["count"] == 1
        assert body["goals"][0]["completed_at"] is None
        # 不外泄"被隐藏的条数"
        assert "hidden" not in body and "hidden_count" not in body

        # 主账号：范围标签为 subtree，但同样不下发隐藏条数
        root_body = client.get("/v1/goals", headers=_headers("boss")).json()
        assert root_body["visible_scope"] == "subtree"
        assert set(root_body) == {"goals", "count", "visible_scope"}

        # 终态写入一次时间；重试拉回非终态时清除，再次失败重新计时
        terminal = _goal("G-STAMP", "failed")
        AIStateManager._stamp_goal_completed_at(terminal)
        first_stamp = terminal["completed_at"]
        assert isinstance(first_stamp, float) and first_stamp > 0

        retrying = _goal("G-STAMP", "running")
        retrying["completed_at"] = first_stamp
        AIStateManager._stamp_goal_completed_at(retrying)
        assert "completed_at" not in retrying

        refailed = _goal("G-STAMP", "failed")
        AIStateManager._stamp_goal_completed_at(refailed)
        assert refailed["completed_at"] >= first_stamp

        # 列表视图把 completed_at 原样透出（有值才有值）
        _seed_and_project(manager, terminal)
        listed = {
            item["goal_id"]: item
            for item in client.get("/v1/goals", headers=alice).json()["goals"]
        }
        assert listed["G-STAMP"]["completed_at"] == first_stamp


# ─── 自检：台账落后多少条 ──────────────────────────────────────

class TestLagSelfCheck:
    def test_status_reports_lag_when_write_through_missed(self, db_path):
        manager = AIStateManager()
        _seed_and_project(manager, _goal("G-SEEN", "completed"))
        status = manager.task_ledger_status()
        assert status["lag"]["lag_total"] == 0

        # 模拟"写穿透漏了一条"：目标在文件里，台账没有
        with manager._goal_lock:
            manager._goals["G-MISSED"] = _goal("G-MISSED", "completed")
        manager._save_goals()

        status = manager.task_ledger_status()
        assert status["lag"]["lag_total"] >= 1
        assert "G-MISSED" in status["lag"]["missing_in_ledger"]

        # 补偿后归零
        manager.task_ledger_repair()
        assert manager.task_ledger_status()["lag"]["lag_total"] == 0
        assert manager.task_ledger().get_task("G-MISSED") is not None
