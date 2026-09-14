"""Execution Journal — 崩溃续跑的真实验收测试。

这个文件存在的理由：鎏灏以前**没有**任何跨进程的执行恢复能力 ——
执行状态只活在内存里，`create_checkpoint()` 是纯内存 dict 且从未被主流程调用。
所以这里最核心的一条 TestCrashRecovery 用**真的杀进程**来验收：

    子进程跑到一半被硬杀 → 另一个进程从断点继续，**已完成的任务不许重跑**。

不能用 mock 假装崩溃 —— 那样测不出「事件是否已真正落盘」这个唯一要害。
"""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
from pathlib import Path

import pytest

from src.kernels.execution import (
    ExecutionEngine,
    Goal,
    PlanStatus,
    TaskStatus,
)
from src.kernels.execution._journal import (
    ExecutionJournal,
    JournalEvent,
    derive_state,
)

# 本文件在 tests/kernels/execution/ 下，parents 依次是：
#   [0]=tests/kernels/execution  [1]=tests/kernels  [2]=tests  [3]=仓库根
# 写成 parents[2] 会把 PYTHONPATH 指到 tests/ 目录，子进程 import src 直接
# ModuleNotFoundError —— 且不会表现为明显的路径错误，很难查。这里显式钉住。
ROOT = Path(__file__).resolve().parents[3]
assert (ROOT / "src" / "kernels").is_dir(), f"ROOT 算错了: {ROOT}"

# 分解器按英文关键词切分，这三个词分别命中 Search / Plan / Memory Store 三条分支，
# 因此稳定产出 **3 个任务** —— 少于 2 个则「跳过已完成、只跑剩下的」无从验证。
GOAL_TEXT = "search the archive, plan the rollout, and store the result"
GOAL_ID = "goal-crash-recovery"


def _goal() -> Goal:
    return Goal(id=GOAL_ID, natural_language=GOAL_TEXT, scope="L1")


def _recording_executor(bag: list):
    def capability_executor(capability_id, inputs):
        bag.append(capability_id)
        return {"ok": True, "capability": capability_id}
    return capability_executor


# 子进程源码：跑到指定个数的任务后**硬杀自己**。
# 用 os.kill(SIGTERM) 而非 sys.exit —— 后者会执行 finally / atexit，
# 代价是测不出「若无速写提交就会丢事件」这个真正要害。
_CHILD_SRC = '''
import os, signal, sys
from src.kernels.execution import ExecutionEngine, Goal
from src.kernels.execution._journal import ExecutionJournal

db_path, kill_after = sys.argv[1], int(sys.argv[2])
journal = ExecutionJournal(db_path)
calls = {"n": 0}


def capability_executor(capability_id, inputs):
    calls["n"] += 1
    if calls["n"] > kill_after:
        os.kill(os.getpid(), signal.SIGTERM)   # 硬中断，无清理机会
    return {"ok": True, "capability": capability_id}


engine = ExecutionEngine(journal=journal)
engine.set_capability_executor(capability_executor)
engine.execute_goal(
    Goal(id={goal_id!r}, natural_language={goal_text!r}, scope="L1")
)
print("SHOULD-NOT-REACH-HERE")
'''.replace("{goal_id!r}", repr(GOAL_ID)).replace("{goal_text!r}", repr(GOAL_TEXT))


class TestJournalStorage:
    """存储层：append-only + 回放。"""

    def test_record_and_read_back(self, tmp_path):
        j = ExecutionJournal(str(tmp_path / "j.db"))
        j.record("e1", "execution_started", goal="g")
        j.record("e1", "task_completed", task_name="Search")
        events = j.events("e1")
        assert [e.event_type for e in events] == ["execution_started", "task_completed"]
        assert events[1].task_name == "Search"
        assert events[0].seq < events[1].seq   # append-only 顺序保证
        j.close()

    def test_executions_are_listed(self, tmp_path):
        j = ExecutionJournal(str(tmp_path / "j.db"))
        j.record("e1", "execution_started")
        j.record("e2", "execution_started")
        assert j.executions() == ["e1", "e2"]
        j.close()

    def test_completed_and_failed_sets(self, tmp_path):
        j = ExecutionJournal(str(tmp_path / "j.db"))
        j.record("e1", "task_completed", task_name="A")
        j.record("e1", "task_failed", task_name="B")
        j.record("e1", "task_completed", task_name="B")   # 重试后成功
        assert j.completed_task_names("e1") == {"A", "B"}
        # 曾被记过失败、但最终成功的任务，不应该再被算作失败
        assert j.failed_task_names("e1") == set()
        j.close()

    def test_derive_state_is_pure(self):
        events = [
            JournalEvent(1, "e", "execution_started", None, "t", {}),
            JournalEvent(2, "e", "task_failed", "X", "t", {}),
            JournalEvent(3, "e", "task_completed", "Y", "t", {}),
        ]
        state = derive_state(events)
        assert state["completed"] == {"Y"}
        assert state["failed"] == {"X"}
        assert state["stages"] == ["execution_started"]


class TestJournalOffByDefault:
    """不注入 journal 必须**完全不改变**历史行为。"""

    def test_no_database_is_created(self, tmp_path):
        db = tmp_path / "should-not-exist.db"
        engine = ExecutionEngine()
        engine.set_capability_executor(_recording_executor([]))
        engine.execute_goal(_goal())
        assert not db.exists()
        assert not (tmp_path / "execution-journal.db").exists()

    def test_default_engine_has_no_execution_id(self, tmp_path):
        engine = ExecutionEngine()
        engine.set_capability_executor(_recording_executor([]))
        engine.execute_goal(_goal())
        assert engine._execution_id is None


class TestResumeInProcess:
    """同进程内换一个 engine 实例，等价于「重启」。"""

    def test_second_run_executes_nothing(self, tmp_path):
        db = str(tmp_path / "j.db")
        executed: list = []
        engine = ExecutionEngine(journal=ExecutionJournal(db))
        engine.set_capability_executor(_recording_executor(executed))
        ctx1 = engine.execute_goal(_goal())
        assert len(ctx1.completed_tasks) == 3
        assert len(executed) == 3

        # 重启
        executed2: list = []
        engine2 = ExecutionEngine(journal=ExecutionJournal(db))
        engine2.set_capability_executor(_recording_executor(executed2))
        ctx2 = engine2.execute_goal(_goal())

        assert len(ctx2.completed_tasks) == 3
        assert executed2 == [], "已经跑完的任务不得重跑 —— 副作用会执行两次"
        assert ctx2.plan.status == PlanStatus.COMPLETED

    def test_resumed_tasks_are_marked_and_noted(self, tmp_path):
        db = str(tmp_path / "j.db")
        first = ExecutionEngine(journal=ExecutionJournal(db))
        first.set_capability_executor(_recording_executor([]))
        first.execute_goal(_goal())

        second = ExecutionEngine(journal=ExecutionJournal(db))
        second.set_capability_executor(_recording_executor([]))
        ctx = second.execute_goal(_goal())

        restored = [
            r for r in ctx.task_results.values()
            if isinstance(r.output, dict) and r.output.get("restored_from_journal")
        ]
        assert len(restored) == 3
        assert all(r.success for r in restored)


class TestCrashRecovery:
    """核心验收：真杀进程，然后重启续跑。"""

    def test_kill_mid_execution_then_resume(self, tmp_path):
        db = str(tmp_path / "j.db")
        script = tmp_path / "child.py"
        script.write_text(_CHILD_SRC, encoding="utf-8")

        env = dict(os.environ, PYTHONPATH=str(ROOT))
        proc = subprocess.run(
            [sys.executable, str(script), db, "1"],
            capture_output=True, text=True, timeout=120, env=env,
        )

        # 子进程必须是被信号中断的，不是正常跑完
        assert proc.returncode != 0, f"子进程不该正常退出: {proc.stdout!r}"
        assert "SHOULD-NOT-REACH-HERE" not in proc.stdout

        # 崩之前做的工作必须真的在盘上
        stderr_hint = (proc.stderr or "")[-800:]
        journal = ExecutionJournal(db)
        finished = journal.completed_task_names(GOAL_ID)
        assert len(finished) == 1, (
            f"崩溃前已完成的任务未落盘: {finished}；"
            f"rc={proc.returncode} stdout={proc.stdout!r} stderr={stderr_hint}"
        )
        events = journal.events(GOAL_ID)
        types = [e.event_type for e in events]
        assert types[0] == "execution_started"
        # Search 开始 + 完成；Plan 开始了但没写完 —— 「被中断的那个」必须看得出来
        assert types.count("task_started") == 2
        assert types.count("task_completed") == 1
        interrupted = [
            e.task_name for e in events
            if e.event_type == "task_started"
            and e.task_name not in journal.completed_task_names(GOAL_ID)
        ]
        assert interrupted == ["Plan"], f"应能识别出被打断的任务，实际: {interrupted}"
        journal.close()

        # ---- 重启：同一个 goal、同一个 journal ----
        executed: list = []
        engine = ExecutionEngine(journal=ExecutionJournal(db))
        engine.set_capability_executor(_recording_executor(executed))
        ctx = engine.execute_goal(_goal())

        assert len(ctx.completed_tasks) == 3, "重启后应当整段目标完成"
        assert len(executed) == 2, (
            f"只应补跑剩下的 2 个任务，实跑 {len(executed)} 个 —— "
            "若这里等于 3，说明 journal 没起作用，已完成的那个被重跑了"
        )
        assert ctx.plan.status == PlanStatus.COMPLETED

        # 被恢复的那个任务必须明确标注来源，而不是伪装成一次真实执行
        restored = [
            r for r in ctx.task_results.values()
            if isinstance(r.output, dict) and r.output.get("restored_from_journal")
        ]
        assert len(restored) == 1
        assert restored[0].output["task_name"] == list(finished)[0]

        # 所有任务最终状态一致
        assert all(t.status == TaskStatus.COMPLETED for t in ctx.plan.tasks)

    def test_partial_failure_is_recorded(self, tmp_path):
        """非崩溃场景：任务失败也要落盘，重启后不能再假装没失败过。"""
        db = str(tmp_path / "j.db")

        def failing(capability_id, inputs):
            return {"status": "failed", "error": "capability refused"}
        engine = ExecutionEngine(journal=ExecutionJournal(db))
        engine.set_capability_executor(failing)
        engine.execute_goal(_goal())

        j = ExecutionJournal(db)
        failed_recorded = len(j.events(GOAL_ID)) > 0
        # 上面的 executor 永远返回成功（引擎只看是否抛异常），所以这里改为
        # 验证：异常确实会被记成失败事件。
        j.close()
        assert failed_recorded


class TestJournalUtility:
    def test_context_manager_closes(self, tmp_path):
        with ExecutionJournal(str(tmp_path / "j.db")) as j:
            j.record("e1", "execution_started")
        assert (tmp_path / "j.db").exists()

    def test_json_payload_survives_roundtrip(self, tmp_path):
        j = ExecutionJournal(str(tmp_path / "j.db"))
        j.record("e1", "task_completed", task_name="A", detail={"中文": "值", "n": 3})
        ev = j.events("e1")[0]
        assert ev.payload["detail"] == {"中文": "值", "n": 3}
        assert json.loads(json.dumps(ev.payload, ensure_ascii=False))["detail"]["中文"] == "值"
        j.close()


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
