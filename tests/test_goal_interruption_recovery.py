"""进程中断与恢复回归（杀在任务前/中/后 + 重启不重复执行）。

覆盖用户真实会碰到的场景：
1. 进程被杀后重启，``running`` 目标必须收敛为可解释状态（``interrupted``），
   绝不永久挂在 running，也绝不自动重跑；
2. 重启后能对受影响的子任务**单独重试**，并"接着完成"（重启后拆解器可能
   给出不同任务名，所以重试走原计划快照）；
3. 中断时正在写的文件不能留下半截被当成成功产物（写入是原子发布 + 产物
   只认已完成任务）；
4. 同一个 goal 不因重启被重复执行（幂等）。

``TestBootReconciliation`` 用"落盘快照 + 重启"做确定性回归；
``TestRealKillRecovery`` 用真实子进程 + ``Popen.kill()`` 覆盖三个杀点。
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path
from string import Template

import pytest

from src.gateway import ai_management
from src.gateway.ai_management import AIStateManager, GoalWorkError
from src.kernels.execution import Task

REPO_ROOT = Path(__file__).resolve().parents[1]

INTERRUPT_REASON_HINT = "进程中断"


# ── 公共夹具/工具 ──────────────────────────────────────────────

@pytest.fixture
def ws(tmp_path, monkeypatch):
    """隔离工作区（goals/journal/产物都落在临时目录）。"""
    root = tmp_path / "ws"
    (root / "notes").mkdir(parents=True)
    monkeypatch.setenv("LIUHAO_WORKSPACE_ROOT", str(root))
    monkeypatch.setenv("LIUHAO_BUTLER_MODEL", "0")
    monkeypatch.setenv("AUDIT_DB_PATH", str(tmp_path / "audit.db"))
    ai_management.AIStateManager._instance = None
    yield root
    ai_management.AIStateManager._instance = None


def _seed(ws: Path, entry: dict) -> None:
    (ws / "liuhao_goals.json").write_text(
        json.dumps({entry["goal_id"]: entry}, ensure_ascii=False),
        encoding="utf-8",
    )


def _boot(ws: Path) -> AIStateManager:
    """模拟一次进程重启（全新单例，从落盘状态恢复）。"""
    ai_management.AIStateManager._instance = None
    return AIStateManager()


def _task(task_id: str, name: str, status: str, **extra) -> dict:
    base = {"id": task_id, "name": name, "status": status}
    base.update(extra)
    return base


def _entry(state: str, tasks: list, **extra) -> dict:
    base = {
        "goal_id": "g-crash",
        "state": state,
        "natural_language": "调研并整理三条要点",
        "scope": "L1",
        "error": None,
        "tasks": tasks,
    }
    base.update(extra)
    return base


class _StubResult:
    def __init__(self, tasks: list):
        self.tasks_payload = tasks
        self.replan_suggested = False


class _StubRuntime:
    def __init__(self, result):
        self._result = result
        self.calls = []

    def run_goal(self, **kwargs):
        self.calls.append(kwargs)
        return self._result


# ── 1. 重启收敛（三个杀点的确定性回归）─────────────────────────

class TestBootReconciliation:
    def test_kill_before_first_task_leaves_goal_interrupted_not_running(self, ws):
        """杀在任务前：目标不能永久 running，排队任务原样可解释。"""
        _seed(ws, _entry("running", [
            _task("t1", "第一步", "pending"),
            _task("t2", "第二步", "pending"),
        ]))
        mgr = _boot(ws)

        entry = mgr._goals["g-crash"]
        assert entry["state"] == "interrupted"
        assert INTERRUPT_REASON_HINT in entry["error"]
        assert "未自动重跑" in entry["error"]
        assert [t["status"] for t in entry["tasks"]] == ["pending", "pending"]
        # 重启只做状态收敛，不会拉起任何执行线程
        assert mgr._goal_threads == {}

    def test_kill_mid_task_marks_running_task_interrupted(self, ws):
        """杀在任务中：running 任务收敛为 interrupted，已完成任务不受影响。"""
        _seed(ws, _entry("running", [
            _task("t1", "第一步", "completed", result="已完成，保持原样"),
            _task("t2", "第二步", "running"),
        ]))
        mgr = _boot(ws)

        entry = mgr._goals["g-crash"]
        assert entry["state"] == "interrupted"
        t1, t2 = entry["tasks"]
        assert t1["status"] == "completed"
        assert t1["result"] == "已完成，保持原样"
        assert t2["status"] == "interrupted"
        assert "结果未知" in t2["error"] and "单独重试" in t2["error"]
        assert all(t["status"] != "running" for t in entry["tasks"])

    def test_kill_after_task_keeps_artifacts_and_does_not_rerun(self, ws):
        """杀在任务后：已完成任务的产物保留，重启不会重写文件。"""
        produced = ws / "notes" / "kept.txt"
        produced.write_text("完整内容", encoding="utf-8")
        _seed(ws, _entry("running", [
            _task("t1", "写文件", "completed", artifacts=["notes/kept.txt"],
                  capability_id="file_write", inputs={"path": "notes/kept.txt"}),
        ], artifacts=["notes/kept.txt"]))
        before = produced.read_text(encoding="utf-8")

        mgr = _boot(ws)

        entry = mgr._goals["g-crash"]
        assert entry["state"] == "interrupted"
        assert entry["tasks"][0]["status"] == "completed"
        assert entry["artifacts"] == ["notes/kept.txt"]
        assert produced.read_text(encoding="utf-8") == before

    def test_restart_twice_is_idempotent(self, ws):
        """同一 goal 重启两次：状态稳定、任务不重复、文件不再被改写。"""
        _seed(ws, _entry("running", [
            _task("t1", "第一步", "completed"),
            _task("t2", "第二步", "running"),
        ]))
        _boot(ws)
        goals_path = ws / "liuhao_goals.json"
        first_bytes = goals_path.read_bytes()
        first_entry = json.loads(first_bytes)["g-crash"]
        assert first_entry["state"] == "interrupted"

        mgr = _boot(ws)
        second_bytes = goals_path.read_bytes()
        second_entry = mgr._goals["g-crash"]

        assert mgr._goal_threads == {}
        assert second_entry["state"] == "interrupted"
        assert second_entry["tasks"] == first_entry["tasks"]
        assert second_bytes == first_bytes, "第二次重启不应再次改写状态"


# ── 2. 重启后的单任务重试 ─────────────────────────────────────

class TestRetryAfterRestart:
    def _seed_interrupted(self, ws):
        _seed(ws, _entry("interrupted", [
            _task(
                "t1", "调研 AI 智能体开源项目", "interrupted",
                capability_id="network_bus",
                inputs={"goal": "调研 AI 智能体开源项目"},
                dependencies=[], max_retries=3, timeout_seconds=300,
            ),
            _task("t2", "整理要点", "completed", result="已完成",
                  capability_id="execution_pipeline", inputs={}),
        ], error="目标在进程中断前仍未结束"))

    def test_retry_interrupted_task_uses_pinned_plan_snapshot(self, ws, monkeypatch):
        """重启后拆解器可能换任务名，重试必须按原计划快照执行。"""
        self._seed_interrupted(ws)
        mgr = _boot(ws)
        stub = _StubRuntime(_StubResult([{
            "id": "t1", "name": "调研 AI 智能体开源项目", "status": "completed",
            "result": "已补跑", "error": None,
        }]))
        monkeypatch.setattr(AIStateManager, "_ensure_runtime", lambda self: stub)
        monkeypatch.setattr(
            AIStateManager, "_result_to_dict",
            staticmethod(lambda result, nl, scope: {"tasks": result.tasks_payload}),
        )

        entry = mgr.retry_task("g-crash", "t1")

        call = stub.calls[0]
        assert call["only_task_name"] == "调研 AI 智能体开源项目"
        assert call["pinned_tasks"] is not None, "应携带原计划快照，避免按名找不到"
        assert call["pinned_tasks"][0]["inputs"] == {"goal": "调研 AI 智能体开源项目"}
        assert entry["tasks"][0]["status"] == "completed"
        assert entry["tasks"][0]["result"] == "已补跑"
        assert entry["tasks"][1]["status"] == "completed"  # 其余任务不受影响
        assert entry["state"] == "completed"

    def test_retry_queued_task_of_interrupted_goal_is_allowed(self, ws, monkeypatch):
        """杀在任务前：排队任务也能单独补跑（但绝不整目标重跑）。"""
        _seed(ws, _entry("interrupted", [
            _task("t1", "第一步", "pending", capability_id="file_write",
                  inputs={"path": "notes/a.txt", "content": "x"}),
        ]))
        mgr = _boot(ws)
        stub = _StubRuntime(_StubResult([{
            "id": "t1", "name": "第一步", "status": "completed", "result": "ok",
        }]))
        monkeypatch.setattr(AIStateManager, "_ensure_runtime", lambda self: stub)
        monkeypatch.setattr(
            AIStateManager, "_result_to_dict",
            staticmethod(lambda result, nl, scope: {"tasks": result.tasks_payload}),
        )

        entry = mgr.retry_task("g-crash", "t1")
        assert entry["state"] == "completed"
        assert len(stub.calls) == 1

    def test_completed_task_still_not_retryable(self, ws):
        _seed(ws, _entry("failed", [_task("t1", "已完成", "completed")]))
        mgr = _boot(ws)
        with pytest.raises(GoalWorkError) as exc:
            mgr.retry_task("g-crash", "t1")
        assert exc.value.code == "task_not_retryable"


# ── 3. 半截文件不得冒充成功产物 ────────────────────────────────

class TestPartialFileSafety:
    def test_interrupted_file_write_is_not_an_artifact(self, ws):
        """任务未完成时，磁盘上的半截文件不会被列为产物。"""
        half = ws / "notes" / "half.txt"
        half.write_text("只有一半", encoding="utf-8")
        task = Task(
            id="t1", goal_id="g", name="写文件", description="写一个文件",
            capability_id="file_write", inputs={"path": "notes/half.txt"},
        )
        task.status = "interrupted"
        task.result = {"path": str(half), "written": str(half)}

        assert AIStateManager._collect_artifacts([task]) == []

    def test_filesystem_write_is_published_atomically(self, ws, monkeypatch):
        """原子发布：替换失败时目标不存在，且不残留临时文件。"""
        from src.ai.world_interface import FilesystemAdapter, WorldRequest
        adapter = FilesystemAdapter(root=str(ws))

        failed = {"raise": RuntimeError("模拟中断")}

        def broken_replace(src, dst):
            raise failed["raise"]

        monkeypatch.setattr("src.ai.world_interface.os.replace", broken_replace)
        req = WorldRequest(
            adapter="filesystem", action="write",
            params={"path": "notes/atomic.txt", "content": "完整内容"},
        )
        with pytest.raises(RuntimeError):
            adapter.execute(req)

        assert not (ws / "notes" / "atomic.txt").exists()
        assert list((ws / "notes").glob("atomic.txt.*.tmp")) == []


# ── 4. 真实子进程被杀：前/中/后 + 重启两次 ─────────────────────

CHILD_TEMPLATE = Template(r'''
import json, os, sys, time
sys.path.insert(0, $REPO_PATH)
MODE = os.environ["CRASH_MODE"]
WS = os.environ["LIUHAO_WORKSPACE_ROOT"]
GOALS = {
    "before": "write a file notes/crash-before.txt containing 'before content'",
    "mid": "write a file notes/crash-mid.txt containing '$MID_CONTENT'",
    "after": "write a file notes/crash-after.txt containing 'after content'",
}

if MODE == "before":
    # 卡在拆解阶段：进程被杀时任务还不存在（"任务前"）。
    from src.kernels.execution import GoalDecomposer
    def _slow_decompose(self, goal):
        while True:
            time.sleep(0.2)
    GoalDecomposer.decompose = _slow_decompose
elif MODE == "mid":
    # 模拟"正在写"的窗口：内容已落临时文件，但还没原子发布到目标路径。
    import src.ai.world_interface as wi
    _orig_exec = wi.FilesystemAdapter.execute
    def _slow_exec(self, request):
        if request.action == "write":
            path = self._resolve(request.params.get("path"))
            if str(path).endswith("crash-mid.txt"):
                os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
                with open(str(path) + ".partial.tmp", "w", encoding="utf-8") as f:
                    f.write(request.params.get("content", ""))
                while True:
                    time.sleep(0.2)
        return _orig_exec(self, request)
    wi.FilesystemAdapter.execute = _slow_exec
elif MODE == "after":
    # 主文件已完成，卡住校验和任务：杀在"任务后"。
    import src.ai.world_interface as wi
    _orig_exec = wi.FilesystemAdapter.execute
    def _slow_exec(self, request):
        if request.action == "write":
            path = self._resolve(request.params.get("path"))
            if str(path).endswith(".sha256"):
                while True:
                    time.sleep(0.2)
        return _orig_exec(self, request)
    wi.FilesystemAdapter.execute = _slow_exec

from src.gateway.ai_management import AIStateManager
mgr = AIStateManager()
created = mgr.create_and_execute_goal(
    GOALS[MODE], scope="L1", plan_mode="auto", background=True,
)
print("GOAL=" + str(created.get("goal_id")), flush=True)
while True:
    time.sleep(0.2)
''')

MID_CONTENT = "中" * 60
MID_PATH = "notes/crash-mid.txt"
AFTER_PATH = "notes/crash-after.txt"

#: 子进程从启动到把目标状态落盘的等待上限。条件是轮询判定的（满足即返回），
#: 这里只是防止真挂死时无限等；并行负载下解释器启动+全量导入可能超过 20s。
CHILD_READY_TIMEOUT_SECONDS = 90.0


def _spawn_crash_child(ws: Path, mode: str) -> subprocess.Popen:
    code = CHILD_TEMPLATE.substitute(
        REPO_PATH=repr(str(REPO_ROOT)),
        MID_CONTENT=MID_CONTENT,
    )
    env = dict(os.environ)
    env.update({
        "LIUHAO_WORKSPACE_ROOT": str(ws),
        "LIUHAO_BUTLER_MODEL": "0",
        "AUDIT_DB_PATH": str(ws.parent / "audit.db"),
        "CRASH_MODE": mode,
        "PYTHONPATH": str(REPO_ROOT),
    })
    # 输出必须重定向到文件而不是 PIPE：子进程启动时会写大量日志，管道
    # 不读的一端写满后子进程会阻塞在写日志上（永远走不到建 goal），
    # 表现为"目标文件一直不出现"的假超时。
    log_path = ws.parent / f"crash-child-{mode}.log"
    log_fh = open(log_path, "w", encoding="utf-8")
    proc = subprocess.Popen(
        [sys.executable, "-c", code],
        cwd=str(REPO_ROOT), env=env,
        stdout=log_fh, stderr=subprocess.STDOUT, text=True,
    )
    proc._log_fh = log_fh  # type: ignore[attr-defined]
    proc._log_path = log_path  # type: ignore[attr-defined]
    return proc


def _child_output(proc: subprocess.Popen) -> str:
    log_path = getattr(proc, "_log_path", None)
    if not log_path:
        return ""
    try:
        return Path(log_path).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def _read_goals(ws: Path) -> dict:
    path = ws / "liuhao_goals.json"
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _wait_for(proc: subprocess.Popen, ws: Path, predicate, timeout: float = CHILD_READY_TIMEOUT_SECONDS) -> dict:
    """轮询到条件满足为止（不睡固定长秒数），只在**上限**内放弃。

    上限从 20s 提到 90s：本机 CI/并行负载下实测出现过 5+ 个 pytest 进程
    同时跑全量、子进程仅解释器启动+导入就要十几秒，20s 会在条件马上要满足时
    误判超时。这里仍然是"每 50ms 读一次落盘状态、满足即返回"，上限只影响
    真挂死时要等多久；超时路径照旧先杀子进程、再报错，不留后台进程。
    """
    started = time.time()
    deadline = started + timeout
    while time.time() < deadline:
        data = _read_goals(ws)
        if data and predicate(data):
            return data
        if proc.poll() is not None:
            raise AssertionError(f"子进程提前退出：{_child_output(proc)}")
        time.sleep(0.05)
    proc.kill()
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        pass
    raise AssertionError(
        f"等待条件超时（{time.time() - started:.1f}s，上限 {timeout:.0f}s）："
        f"{_child_output(proc)}"
    )


def _close_log(proc: subprocess.Popen) -> None:
    log_fh = getattr(proc, "_log_fh", None)
    if log_fh:
        try:
            log_fh.close()
        except OSError:
            pass
        proc._log_fh = None  # type: ignore[attr-defined]


def _kill(proc: subprocess.Popen) -> None:
    proc.kill()
    proc.wait(timeout=10)
    _close_log(proc)


def _shutdown(proc: subprocess.Popen) -> None:
    """测试收尾：杀掉仍在运行的子进程，并关闭日志句柄（Windows 会锁文件）。"""
    if proc.poll() is None:
        _kill(proc)
    else:
        _close_log(proc)


def _single_entry(data: dict) -> dict:
    assert len(data) == 1, data
    return next(iter(data.values()))


class TestRealKillRecovery:
    def test_real_kill_before_first_task(self, ws):
        """真实杀在任务前：重启后目标 interrupted、没有任务、没有产物。"""
        proc = _spawn_crash_child(ws, "before")
        try:
            _wait_for(proc, ws, lambda d: (
                _single_entry(d)["state"] == "running"
                and _single_entry(d).get("tasks") == []
            ))
            _kill(proc)
        finally:
            _shutdown(proc)

        mgr = _boot(ws)
        entry = mgr._goals["g-crash"] if "g-crash" in mgr._goals else next(iter(mgr._goals.values()))
        assert entry["state"] == "interrupted"
        assert INTERRUPT_REASON_HINT in entry["error"]
        assert entry["tasks"] == []
        assert not (ws / "notes" / "crash-before.txt").exists()
        assert mgr._goal_threads == {}

    def test_real_kill_mid_write_then_retry_completes(self, ws):
        """真实杀在写入窗口：目标文件未发布、不算产物；重试后完整落盘。"""
        proc = _spawn_crash_child(ws, "mid")
        try:
            _wait_for(proc, ws, lambda d: (
                _single_entry(d)["state"] == "running"
                and any(t.get("status") == "running"
                        for t in _single_entry(d).get("tasks", []))
                and (ws / (MID_PATH + ".partial.tmp")).exists()
            ))
            _kill(proc)
        finally:
            _shutdown(proc)

        mgr = _boot(ws)
        entry = _single_entry(mgr._goals)
        goal_id = entry["goal_id"]
        target = ws / MID_PATH
        task = next(t for t in entry["tasks"] if t["status"] == "interrupted")

        # 中断时：目标未发布，半截临时文件存在但不算产物
        assert entry["state"] == "interrupted"
        assert not target.exists()
        assert entry.get("artifacts") == []
        assert (ws / (MID_PATH + ".partial.tmp")).exists()

        # 重试受影响任务：按原计划快照接着完成（含依赖它的校验和任务）
        retried = mgr.retry_task(goal_id, task["id"])
        assert retried["state"] == "completed"
        assert MID_PATH in retried["artifacts"]
        assert (MID_PATH + ".sha256") in retried["artifacts"]
        assert target.read_text(encoding="utf-8") == MID_CONTENT
        assert not (ws / (MID_PATH + ".partial.tmp")).exists()

    def test_real_kill_after_completed_task_preserves_artifact(self, ws):
        """真实杀在任务后：已完成产物保留，第二个任务中断，重试可补完。"""
        proc = _spawn_crash_child(ws, "after")
        try:
            # 必须等第二个任务真的进入 running 再杀：只在「第一个任务已完成」
            # 时就 kill，会把第二个任务留在 pending（它还没开始），
            # 而 pending 按语义只是排队、不算「中断」，测试的前提就不成立了。
            _wait_for(proc, ws, lambda d: (
                _single_entry(d)["state"] == "running"
                and (ws / AFTER_PATH).exists()
                and any(t.get("status") == "completed"
                        for t in _single_entry(d).get("tasks", []))
                and any(t.get("status") == "running"
                        for t in _single_entry(d).get("tasks", []))
            ))
            _kill(proc)
        finally:
            _shutdown(proc)

        mgr = _boot(ws)
        entry = _single_entry(mgr._goals)
        goal_id = entry["goal_id"]
        target = ws / AFTER_PATH
        completed_task = next(t for t in entry["tasks"] if t["status"] == "completed")
        interrupted = next(t for t in entry["tasks"] if t["status"] == "interrupted")

        assert entry["state"] == "interrupted"
        assert entry["artifacts"] == [AFTER_PATH]
        assert target.read_text(encoding="utf-8") == "after content"
        assert completed_task["status"] == "completed"

        retried = mgr.retry_task(goal_id, interrupted["id"])
        assert retried["state"] == "completed"
        sidecar = ws / (AFTER_PATH + ".sha256")
        assert sidecar.exists()
        assert AFTER_PATH in retried["artifacts"]
        assert (AFTER_PATH + ".sha256") in retried["artifacts"]

    def test_restart_twice_does_not_duplicate_execution(self, ws):
        """真实杀进程后连续重启两次：状态只收敛一次、不重复执行。"""
        proc = _spawn_crash_child(ws, "mid")
        try:
            _wait_for(proc, ws, lambda d: (
                _single_entry(d)["state"] == "running"
                and any(t.get("status") == "running"
                        for t in _single_entry(d).get("tasks", []))
                and (ws / (MID_PATH + ".partial.tmp")).exists()
            ))
            _kill(proc)
        finally:
            _shutdown(proc)

        _boot(ws)
        goals_path = ws / "liuhao_goals.json"
        first_bytes = goals_path.read_bytes()
        first_entry = _single_entry(json.loads(first_bytes))
        assert first_entry["state"] == "interrupted"

        mgr = _boot(ws)
        second_bytes = goals_path.read_bytes()
        second_entry = _single_entry(mgr._goals)

        assert mgr._goal_threads == {}
        assert second_entry["state"] == "interrupted"
        assert second_entry["tasks"] == first_entry["tasks"]
        assert second_bytes == first_bytes
        # 两次重启都没有偷偷重跑任务：任务数不变、目标文件仍不存在
        assert len(mgr._goals) == 1
        assert not (ws / MID_PATH).exists()
