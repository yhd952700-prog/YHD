"""ACTION-01 / TOOL-04 — 稳定幂等键与「执行是否已开始」的语义验收。

这里钉住两件事：

1. **幂等键必须真的到达执行器**。同一次执行的所有重试共享同一个 action id；
   老签名 ``executor(capability_id, inputs)`` 不因此崩。不可重入能力（发消息、
   调外部接口）在同一 action id 上只允许产生一次副作用；第二次直接返回首次
   结果并标 ``replayed_from``。
2. **TOOL-04**：``task_started``（已领取）但执行尚未开始 -> 允许重跑；
   ``task_exec_started``（带 exec_started_at）+ 非幂等 -> 转人工确认、不许
   自动重跑；``task_exec_started`` + 天然幂等 -> 允许重跑。

崩溃场景用真子进程硬杀（``SIGTERM``），因为「结果是否已落账」的唯一证据
就是磁盘 —— 同进程内模拟崩溃测不出这一点。
"""

from __future__ import annotations

import json
import os
import signal
import sqlite3
import subprocess
import sys
from pathlib import Path

from src.kernels.execution import (
    Action,
    ActionExecutor,
    ExecutionEngine,
    ExecutionJournal,
    Goal,
    HARD_SIDE_EFFECT_CAPABILITIES,
    PlanStatus,
    TaskStatus,
    capability_has_hard_side_effects,
    capability_is_reentrant,
    dedupe_advisories,
    make_action_id,
    reset_dedupe_advisories,
)

ROOT = Path(__file__).resolve().parents[3]
assert (ROOT / "src" / "kernels").is_dir(), f"ROOT 算错了: {ROOT}"

#: 引擎把 ``goal.id`` 当作 execution id（并发目标之间用它隔离事件）。
EXECUTION_ID = "goal-action-01"
ACTION_ID = make_action_id("goal-action-01", "SendOnce")


def _action(
    action_id: str = ACTION_ID,
    capability_id: str = "network_bus",
    capability_namespace: str = "kernel",
) -> Action:
    return Action(
        id=action_id,
        task_id="task-action-01",
        capability_id=capability_id,
        capability_namespace=capability_namespace,
        inputs={"payload": "hello"},
        correlation_id="corr-action-01",
        scope="L1",
        timeout_seconds=10,
        execution_id=EXECUTION_ID,
    )


def _count_effects(path: Path) -> int:
    if not path.exists():
        return 0
    return len([ln for ln in path.read_text(encoding="utf-8").splitlines() if ln.strip()])


class TestExecutorContract:
    """签名兼容 + 稳定键真的传到了执行器。"""

    def test_action_id_is_stable_across_retries(self, tmp_path):
        seen: list = []

        def executor(capability_id, inputs, *, action_id=None):
            seen.append(action_id)
            if len(seen) == 1:
                # 明确失败 -> 账本释放占坑，允许真正重试一次
                return {"success": False, "error": "flaky upstream"}
            return {"success": True, "result": "ok"}

        goal = Goal(id="goal-action-01", natural_language="send once", scope="L1")
        engine = ExecutionEngine(journal=ExecutionJournal(str(tmp_path / "j.db")))
        engine.set_capability_executor(executor)
        ctx = engine.execute_goal(
            goal,
            pinned_tasks=[{
                "name": "SendOnce",
                "capability_id": "network_bus",
                "inputs": {"payload": "hello"},
                "max_retries": 1,
            }],
        )

        assert len(seen) == 2, f"应当重试一次，实际调用 {seen}"
        assert seen == [ACTION_ID, ACTION_ID], (
            f"同一次执行的重试必须复用同一个幂等键，实际 {seen}"
        )
        assert all(t.status == TaskStatus.COMPLETED for t in ctx.plan.tasks)

    def test_legacy_two_argument_executor_still_works(self, tmp_path):
        calls: list = []

        def legacy(capability_id, inputs):   # 老签名，没有 action_id
            calls.append(capability_id)
            return {"success": True, "capability": capability_id}

        engine = ExecutionEngine(journal=ExecutionJournal(str(tmp_path / "j.db")))
        engine.set_capability_executor(legacy)
        ctx = engine.execute_goal(
            Goal(id="goal-legacy", natural_language="search archive", scope="L1"),
            pinned_tasks=[{
                "name": "Search",
                "capability_id": "execution_pipeline",
                "inputs": {"q": "archive"},
            }],
        )
        assert calls == ["execution_pipeline"]
        assert ctx.plan.status == PlanStatus.COMPLETED


class TestSideEffectDeduplication:
    """不可重入工具：同一 action id 只允许一次副作用。"""

    def test_replay_returns_first_result_without_second_execution(self, tmp_path):
        db = str(tmp_path / "j.db")
        effects = tmp_path / "effects.log"

        def executor(capability_id, inputs, *, action_id=None):
            with effects.open("a", encoding="utf-8") as fh:
                fh.write(f"{action_id}\n")
            return {"success": True, "message": "sent-once", "action_id": action_id}

        action = _action()
        first = ActionExecutor(executor, journal=ExecutionJournal(db)).execute(action)
        assert first.success is True
        assert _count_effects(effects) == 1

        # 等价于「结果已落账、但任务没记完成就重启」：新连接、新执行器实例，
        # 同一个 action id 必须命中账本，绝不再次调用工具。
        second = ActionExecutor(executor, journal=ExecutionJournal(db)).execute(action)
        assert second.success is True
        assert second.replayed is True
        assert second.output["replayed_from"] == ACTION_ID
        assert second.output["message"] == "sent-once"
        assert _count_effects(effects) == 1, "副作用被重复执行了"

    def test_different_action_ids_are_not_deduplicated(self, tmp_path):
        db = str(tmp_path / "j.db")
        effects = tmp_path / "effects.log"

        def executor(capability_id, inputs, *, action_id=None):
            with effects.open("a", encoding="utf-8") as fh:
                fh.write(f"{action_id}\n")
            return {"success": True, "action_id": action_id}

        journal = ExecutionJournal(db)
        action_a = _action("act-aaaaaaaaaaaa-0")
        action_b = _action("act-bbbbbbbbbbbb-0")

        ActionExecutor(executor, journal=journal).execute(action_a)
        ActionExecutor(executor, journal=journal).execute(action_b)

        assert _count_effects(effects) == 2, "不同 action id 不得被误当成重复而吞掉"

    def test_reentrant_capability_is_not_deduped(self, tmp_path):
        db = str(tmp_path / "j.db")
        effects = tmp_path / "effects.log"

        def executor(capability_id, inputs, *, action_id=None):
            with effects.open("a", encoding="utf-8") as fh:
                fh.write("write\n")
            return {"success": True, "path": "out.txt"}

        assert capability_is_reentrant("file_write") is True
        journal = ExecutionJournal(db)
        action = _action(capability_id="file_write")
        ActionExecutor(executor, journal=journal).execute(action)
        ActionExecutor(executor, journal=journal).execute(action)

        assert _count_effects(effects) == 2, "天然幂等能力不应被账本拦截"


_CHILD_SUCCESS_THEN_CRASH = '''
import os, signal, sys
from pathlib import Path
from src.kernels.execution import Action, ActionExecutor, ExecutionJournal

db, effects, action_id = sys.argv[1], Path(sys.argv[2]), sys.argv[3]


def executor(capability_id, inputs, *, action_id=None):
    with effects.open("a", encoding="utf-8") as fh:
        fh.write(f"{action_id}\\n")          # 副作用已经发生
    return {"success": True, "message": "sent-once", "action_id": action_id}


action = Action(
    id=action_id, task_id="t", capability_id="network_bus",
    capability_namespace="kernel", inputs={"payload": "hello"},
    correlation_id="c", scope="L1", timeout_seconds=10,
    execution_id="exec-action-01",
)
ActionExecutor(executor, journal=ExecutionJournal(db)).execute(action)
# 结果已写入幂等账本，但任务级 task_completed 还没落盘 —— 硬中断
os.kill(os.getpid(), signal.SIGTERM)
print("SHOULD-NOT-REACH-HERE")
'''

_CHILD_CRASH_BEFORE_RESULT = '''
import os, signal, sys
from pathlib import Path
from src.kernels.execution import Action, ActionExecutor, ExecutionJournal

db, effects, action_id = sys.argv[1], Path(sys.argv[2]), sys.argv[3]


def executor(capability_id, inputs, *, action_id=None):
    with effects.open("a", encoding="utf-8") as fh:
        fh.write(f"{action_id}\\n")          # 副作用已经发生
    os.kill(os.getpid(), signal.SIGTERM)     # 结果还来不及落账


action = Action(
    id=action_id, task_id="t", capability_id="network_bus",
    capability_namespace="kernel", inputs={"payload": "hello"},
    correlation_id="c", scope="L1", timeout_seconds=10,
    execution_id="exec-action-01",
)
ActionExecutor(executor, journal=ExecutionJournal(db)).execute(action)
'''


def _run_child(tmp_path: Path, source: str, db: str, effects: Path) -> subprocess.CompletedProcess:
    script = tmp_path / f"child_{abs(hash(source)) % 10**8}.py"
    script.write_text(source, encoding="utf-8")
    env = dict(os.environ, PYTHONPATH=str(ROOT))
    return subprocess.run(
        [sys.executable, str(script), db, str(effects), ACTION_ID],
        capture_output=True, text=True, timeout=120, env=env,
    )


class TestCrashReplay:
    """真崩溃场景：副作用只发生一次。"""

    def test_crash_before_task_completed_returns_first_result(self, tmp_path):
        db = str(tmp_path / "j.db")
        effects = tmp_path / "effects.log"

        proc = _run_child(tmp_path, _CHILD_SUCCESS_THEN_CRASH, db, effects)
        assert proc.returncode != 0, f"子进程应当被硬中断: {proc.stdout!r}"
        assert "SHOULD-NOT-REACH-HERE" not in proc.stdout
        assert _count_effects(effects) == 1

        # 重启：同一个 action id 命中幂等账本
        def executor(capability_id, inputs, *, action_id=None):
            raise AssertionError("重启后不得再次执行副作用")

        replayed = ActionExecutor(executor, journal=ExecutionJournal(db)).execute(_action())
        assert replayed.success is True
        assert replayed.replayed is True
        assert replayed.output["replayed_from"] == ACTION_ID
        assert replayed.output["message"] == "sent-once"
        assert _count_effects(effects) == 1

    def test_crash_without_landed_result_is_not_reexecuted(self, tmp_path):
        db = str(tmp_path / "j.db")
        effects = tmp_path / "effects.log"

        proc = _run_child(tmp_path, _CHILD_CRASH_BEFORE_RESULT, db, effects)
        assert proc.returncode != 0, f"子进程应当被硬中断: {proc.stdout!r}"
        assert _count_effects(effects) == 1

        def executor(capability_id, inputs, *, action_id=None):
            raise AssertionError("结果未知的副作用不得自动重放")

        replayed = ActionExecutor(executor, journal=ExecutionJournal(db)).execute(_action())
        assert replayed.success is False
        assert replayed.replayed is True
        assert replayed.output["status"] == "replayed_unknown"
        assert replayed.output["replayed_from"] == ACTION_ID
        assert "人工确认" in (replayed.error or "")
        assert _count_effects(effects) == 1


def _engine_with_journal(db: str) -> ExecutionEngine:
    engine = ExecutionEngine(journal=ExecutionJournal(db))
    return engine


class TestTool04ClaimVsExecution:
    """「已领取」与「执行已开始」必须区分对待。"""

    def test_claimed_but_not_executed_is_rerun(self, tmp_path):
        db = str(tmp_path / "j.db")
        journal = ExecutionJournal(db)
        journal.record(EXECUTION_ID, "execution_started", goal="g")
        # 只有 task_started：任务被领取，但执行器从未被调用 -> 没有副作用
        journal.record(EXECUTION_ID, "task_started", task_name="SendOnce")
        journal.close()

        effects: list = []
        engine = _engine_with_journal(db)
        engine.set_capability_executor(
            lambda capability_id, inputs, **kw: effects.append(capability_id)
            or {"success": True}
        )
        ctx = engine.execute_goal(
            Goal(id="goal-action-01", natural_language="send once", scope="L1"),
            pinned_tasks=[{
                "name": "SendOnce",
                "capability_id": "network_bus",
                "inputs": {"payload": "hello"},
            }],
        )

        assert effects == ["network_bus"], "没有执行开始的领取记录必须允许重跑"
        assert ctx.plan.status == PlanStatus.COMPLETED

    def test_running_without_any_start_marker_is_rerun(self, tmp_path):
        """持久化快照里是 running、但 journal 里连 task_started 都没有 -> 重跑。

        执行器从未被调用过，也就没有任何副作用；「running」只说明调度层把它
        放进了队列，不能当成「已开始执行」。
        """
        db = str(tmp_path / "j.db")
        journal = ExecutionJournal(db)
        journal.record(EXECUTION_ID, "execution_started", goal="g")
        journal.close()

        effects: list = []
        engine = _engine_with_journal(db)
        engine.set_capability_executor(
            lambda capability_id, inputs, **kw: effects.append(capability_id)
            or {"success": True}
        )
        ctx = engine.execute_goal(
            Goal(id="goal-action-01", natural_language="send once", scope="L1"),
            pinned_tasks=[{
                "name": "SendOnce",
                "capability_id": "network_bus",
                "inputs": {"payload": "hello"},
            }],
        )

        assert effects == ["network_bus"], "没有开始标记的任务必须允许重跑"
        assert ctx.plan.status == PlanStatus.COMPLETED

    def test_exec_started_non_reentrant_needs_human(self, tmp_path):
        db = str(tmp_path / "j.db")
        journal = ExecutionJournal(db)
        journal.record(EXECUTION_ID, "execution_started", goal="g")
        journal.record(EXECUTION_ID, "task_started", task_name="SendOnce")
        journal.record(
            EXECUTION_ID, "task_exec_started", task_name="SendOnce",
            exec_started_at="2026-11-08T10:00:00+00:00",
            capability_id="network_bus",
        )
        journal.close()

        effects: list = []
        engine = _engine_with_journal(db)
        engine.set_capability_executor(
            lambda capability_id, inputs, **kw: effects.append(capability_id)
            or {"success": True}
        )
        ctx = engine.execute_goal(
            Goal(id="goal-action-01", natural_language="send once", scope="L1"),
            pinned_tasks=[{
                "name": "SendOnce",
                "capability_id": "network_bus",
                "inputs": {"payload": "hello"},
            }],
        )

        assert effects == [], "非幂等能力执行已开始，不许自动重跑"
        task = ctx.plan.tasks[0]
        assert task.status == TaskStatus.FAILED
        assert "人工确认" in (task.error or "")
        assert ctx.plan.status in (PlanStatus.FAILED, PlanStatus.PARTIAL)

    def test_exec_started_reentrant_is_rerun(self, tmp_path):
        db = str(tmp_path / "j.db")
        journal = ExecutionJournal(db)
        journal.record(EXECUTION_ID, "execution_started", goal="g")
        journal.record(EXECUTION_ID, "task_started", task_name="WriteFile")
        journal.record(
            EXECUTION_ID, "task_exec_started", task_name="WriteFile",
            exec_started_at="2026-11-08T10:00:00+00:00",
            capability_id="file_write",
        )
        journal.close()

        effects: list = []
        engine = _engine_with_journal(db)
        engine.set_capability_executor(
            lambda capability_id, inputs, **kw: effects.append(capability_id)
            or {"success": True}
        )
        ctx = engine.execute_goal(
            Goal(id="goal-action-01", natural_language="write file", scope="L1"),
            pinned_tasks=[{
                "name": "WriteFile",
                "capability_id": "file_write",
                "inputs": {"path": "out.txt", "content": "x"},
            }],
        )

        assert effects == ["file_write"], "天然幂等能力执行已开始也可安全重跑"
        assert ctx.plan.status == PlanStatus.COMPLETED


class TestLedgerAvailability:
    """journal 缺席 / 写失败都必须「说出来」，写失败还必须 fail-closed。

    历史缺陷：``journal=None`` 时去重被静默跳过 —— 用户看到一切正常，
    但同一动作在崩溃重试后可能产生第二次外部副作用。
    """

    def setup_method(self):
        reset_dedupe_advisories()

    def teardown_method(self):
        reset_dedupe_advisories()

    def test_hard_side_effect_list_is_explicit(self):
        """硬副作用清单必须显式列出仓库里真实存在的能力 id，而不是靠命名猜。"""
        assert HARD_SIDE_EFFECT_CAPABILITIES == frozenset({
            "network_bus",
            "personal.write",
            "multi_tier_memory",
            "preview_start",
            "execution_pipeline",
        })
        for cap in HARD_SIDE_EFFECT_CAPABILITIES:
            assert capability_has_hard_side_effects(cap) is True
        # 只读 / 天然幂等 / 未知能力都不在硬副作用清单里。
        for cap in ("file.read", "file.list", "file_write", "python_compute",
                    "memory.search", "system.status", "brand_new_tool"):
            assert capability_has_hard_side_effects(cap) is False

    def _run_without_journal(self, tmp_path, capability_id, name):
        effects = tmp_path / "effects.log"

        def executor(capability_id, inputs, *, action_id=None):
            with effects.open("a", encoding="utf-8") as fh:
                fh.write(f"{action_id}\n")
            return {"success": True, "result": "done"}

        engine = ExecutionEngine()
        engine.set_capability_executor(executor)
        ctx = engine.execute_goal(
            Goal(id="goal-no-ledger", natural_language="run once", scope="L1"),
            pinned_tasks=[{
                "name": name,
                "capability_id": capability_id,
                "inputs": {"payload": "hello"},
            }],
        )
        return ctx, effects

    def test_hard_side_effect_without_journal_is_refused(self, tmp_path, caplog):
        """账本缺席时硬副作用必须 fail-closed：一行告警挡不住已发出的消息。"""
        ctx, effects = self._run_without_journal(tmp_path, "network_bus", "SendOnce")

        assert _count_effects(effects) == 0, "硬副作用不得在账本缺席时执行"
        assert ctx.plan.status == PlanStatus.FAILED
        task = ctx.plan.tasks[0]
        assert task.status == TaskStatus.FAILED
        assert task.error and "幂等账本" in task.error
        assert "network_bus" in task.error, "错误信息要指出被拒的能力"
        assert "ExecutionJournal" in task.error, "错误信息要给出解决办法"
        assert "网关默认已接入账本" in task.error, "要说明正常部署无需额外操作"

        advisories = {item["code"]: item for item in dedupe_advisories()}
        item = advisories["hard_side_effect_without_ledger"]
        assert item["status"] == "degraded"
        assert item["blocks_readiness"] is False
        assert item["capabilities"] == ["network_bus"]
        assert "已拒绝执行" in item["user_message"]
        assert "ExecutionJournal" in item["user_message"]
        assert any(
            "idempotency_hard_side_effect_refused" in record.getMessage()
            for record in caplog.records
        )

    def _run_direct_without_journal(self, tmp_path, capability_id):
        """直接过 ActionExecutor，验证「账本缺席 + 非硬副作用」这一档。

        ``system.status`` 这类只读能力只在工具注册表里、内核 capability registry
        未登记，引擎一进来就会被 scope check 拒掉；因此测试把同样的能力 id 注册到
        一个独立 namespace（``test``）下，既走真实的 scope check，也不污染
        ``kernel`` 命名空间。
        """
        from src.kernels.capability import CapabilityScope, register_capability

        namespace = "test"
        register_capability(
            id=capability_id,
            version="1.0.0",
            namespace=namespace,
            name=capability_id,
            description="test capability",
            scope=CapabilityScope.L1,
            owner="tests",
        )
        effects = tmp_path / "effects.log"

        def executor(capability_id, inputs, *, action_id=None):
            with effects.open("a", encoding="utf-8") as fh:
                fh.write(f"{action_id}\n")
            return {"success": True, "result": "ok"}

        action_executor = ActionExecutor(capability_executor=executor)
        result = action_executor.execute(
            _action(capability_id=capability_id, capability_namespace=namespace)
        )
        return result, effects

    def test_read_only_without_journal_runs_with_safe_advisory(self, tmp_path):
        result, effects = self._run_direct_without_journal(tmp_path, "system.status")
        assert result.success is True
        assert _count_effects(effects) == 1, "只读能力不阻断"
        advisories = {item["code"]: item for item in dedupe_advisories()}
        item = advisories["ledger_missing_safe_replay"]
        assert item["blocks_readiness"] is False
        assert item["capabilities"] == ["system.status"]
        assert "硬副作用能力" in item["user_message"], "要说明哪些能力会被挡"
        assert "ExecutionJournal" in item["user_message"], "要给出解决办法"

    def test_reentrant_without_journal_runs_with_safe_advisory(self, tmp_path):
        ctx, effects = self._run_without_journal(tmp_path, "file_write", "WriteFile")
        assert _count_effects(effects) == 1, "天然幂等能力不阻断"
        assert ctx.plan.status == PlanStatus.COMPLETED
        advisories = {item["code"]: item for item in dedupe_advisories()}
        item = advisories["ledger_missing_safe_replay"]
        assert item["capabilities"] == ["file_write"]

    def test_capability_outside_hard_list_defaults_to_safe(self, tmp_path):
        """硬副作用白名单之外默认按可安全重入处理（显式选定的口径）。

        用 ``brand_new_tool`` 模拟「已注册但未分类」的新能力；它不在硬副作用
        清单里，因此账本缺席时也放行（并留下 advisory）。
        """
        result, effects = self._run_direct_without_journal(tmp_path, "brand_new_tool")
        assert result.success is True
        assert _count_effects(effects) == 1
        advisories = {item["code"]: item for item in dedupe_advisories()}
        assert advisories["ledger_missing_safe_replay"]["capabilities"] == ["brand_new_tool"]

    def test_ledger_write_failure_fails_closed(self, tmp_path):
        db = str(tmp_path / "j.db")
        effects = tmp_path / "effects.log"
        journal = ExecutionJournal(db)

        def broken_claim(key, execution_id, task_name=None):
            raise sqlite3.OperationalError("attempt to write a readonly database")

        journal.claim_idempotency = broken_claim  # type: ignore[method-assign]

        def executor(capability_id, inputs, *, action_id=None):
            with effects.open("a", encoding="utf-8") as fh:
                fh.write(f"{action_id}\n")
            return {"success": True}

        executor_impl = ActionExecutor(executor, journal=journal)
        first = executor_impl.execute(_action())
        second = executor_impl.execute(_action())

        assert _count_effects(effects) == 0, "账本写不进去时不得执行不可重入动作"
        for result in (first, second):
            assert result.success is False
            assert result.output["status"] == "idempotency_ledger_unavailable"
            assert result.output["executed"] is False
            assert "幂等账本不可写" in (result.error or ""), "失败原因必须中文可读"
            assert "journal 写入权限" in (result.error or "")
            assert "readonly database" not in (result.error or ""), "原始异常不该抛给用户"

        advisories = {item["code"]: item for item in dedupe_advisories()}
        assert "journal_write_failed" in advisories
        assert advisories["journal_write_failed"]["blocks_readiness"] is False

    def test_remember_failure_does_not_reexecute(self, tmp_path):
        """占坑成功、结果落账失败：第二次投递只能回放「结果未知」，不得重跑。"""
        db = str(tmp_path / "j.db")
        effects = tmp_path / "effects.log"
        journal = ExecutionJournal(db)

        def broken_remember(key, **payload):
            raise sqlite3.OperationalError("attempt to write a readonly database")

        journal.remember_idempotent_result = broken_remember  # type: ignore[method-assign]

        def executor(capability_id, inputs, *, action_id=None):
            with effects.open("a", encoding="utf-8") as fh:
                fh.write(f"{action_id}\n")
            return {"success": True, "message": "sent-once"}

        first = ActionExecutor(executor, journal=journal).execute(_action())
        assert first.success is True
        assert _count_effects(effects) == 1

        second = ActionExecutor(executor, journal=journal).execute(_action())
        assert second.success is False
        assert second.output["status"] == "replayed_unknown"
        assert "人工确认" in (second.error or "")
        assert _count_effects(effects) == 1, "不得因为结果没落账就再执行一次"
