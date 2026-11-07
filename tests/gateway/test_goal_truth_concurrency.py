"""目标链路缺陷回归：持久化失败必须可见、序列化不毁数据、并发目标不串状态。

覆盖：
- P0-2  POST /v1/goals 在 goals 文件不可写时必须返回明确错误，不能 200 running
- P1-13 不可序列化的内存条目不能覆盖磁盘原值
- P1-14 并发目标中先结束者不得把另一个目标的 agent 标记为空闲；异常路径不泄漏线程记录
- P1-11 _ensure_runtime 并发首用只创建一个 runtime
"""

from __future__ import annotations

import json
import threading
import time
import types

import pytest
from fastapi.testclient import TestClient

from src.gateway import ai_management
from src.gateway.ai_management import AIStateManager, GoalPersistError
from src.gateway.main import get_app


@pytest.fixture
def manager(tmp_path, monkeypatch):
    monkeypatch.setenv("LIUHAO_WORKSPACE_ROOT", str(tmp_path))
    AIStateManager._instance = None
    mgr = AIStateManager()
    yield mgr
    AIStateManager._instance = None


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("LIUHAO_WORKSPACE_ROOT", str(tmp_path))
    AIStateManager._instance = None
    yield TestClient(get_app())
    AIStateManager._instance = None


def _fake_result(goal_id: str):
    return types.SimpleNamespace(
        goal_id=goal_id,
        state=types.SimpleNamespace(value="completed"),
        correlation_id="corr",
        error=None,
        replan_count=0,
        replan_suggested=False,
        memory_keys=[],
        persist_errors=[],
        trace=None,
        context=None,
        evaluation=None,
    )


class _FakeEmployee:
    def __init__(self) -> None:
        self.name = "fake-employee"
        self.agents = []
        self.idle_calls = 0
        self.busy_calls = 0
        self.finished: list = []

    def record_goal_submitted(self, goal_id: str) -> None:
        pass

    def mark_agents_busy(self) -> None:
        self.busy_calls += 1

    def mark_agents_idle(self) -> None:
        self.idle_calls += 1

    def record_goal_finished(self, goal_id: str, state: str) -> None:
        self.finished.append((goal_id, state))


class _GatedRuntime:
    """C 目标先在任务里停住，D 目标完整跑完，然后 C 才继续。

    目标 id 是管理器生成的 uuid，不能用 id 做门控；按目标文本区分即可。
    """

    def __init__(self) -> None:
        self.started = {"goal-c": threading.Event(), "goal-d": threading.Event()}
        self.release_c = threading.Event()
        self.release_d = threading.Event()

    def run_goal(self, goal_text: str, goal_id: str, **kwargs) -> object:
        if goal_text == "search the archive":
            self.started["goal-c"].set()
            assert self.release_c.wait(10), "test gate never released"
        else:
            self.started["goal-d"].set()
            assert self.release_d.wait(10), "test gate never released"
        return _fake_result(goal_id)


def test_p0_2_goal_persist_failure_is_visible(manager, tmp_path, monkeypatch):
    """goals 路径变成目录后，POST /v1/goals 必须报错而不是 200 running。"""
    # 让 goals 路径不可写：同名目录会让临时文件 os.replace 失败。
    (tmp_path / "liuhao_goals.json").mkdir()

    fake_employee = _FakeEmployee()
    monkeypatch.setattr(manager, "_ensure_employee", lambda: fake_employee)
    monkeypatch.setattr(manager, "_ensure_runtime", lambda: None)
    monkeypatch.setattr(manager, "_persist_employee", lambda: None)

    with pytest.raises(GoalPersistError) as raised:
        manager.create_and_execute_goal("search the archive", background=True)

    assert raised.value.status_code == 507
    assert raised.value.code == "goal_persist_failed"
    assert "目标未能启动" in raised.value.detail
    # 没有开始执行：内存里不能留下 running 记录，active/线程登记也必须清空。
    assert manager._goals == {}
    assert manager._active_goals == set()
    assert manager._goal_threads == {}
    # agent 被标过 BUSY 后必须回滚成 IDLE，不能留下假忙碌。
    assert fake_employee.busy_calls == 1
    assert fake_employee.idle_calls == 1


def test_p0_2_http_endpoint_does_not_report_running(client, tmp_path, auth_headers):
    """真实 HTTP：持久化失败时端点必须给出非 2xx 与明确 code。"""
    manager = AIStateManager()
    (tmp_path / "liuhao_goals.json").mkdir()

    resp = client.post(
        "/v1/goals",
        json={"natural_language": "search the archive", "background": True},
        headers=auth_headers,
    )

    assert resp.status_code == 507, resp.text
    body = resp.json()
    detail = body.get("detail") if isinstance(body.get("detail"), dict) else body
    assert detail.get("code") == "goal_persist_failed"
    assert "目标未能启动" in (detail.get("message") or "")
    assert "running" not in resp.text.lower()


def test_p1_13_unserializable_entry_keeps_disk_copy(manager, tmp_path):
    """内存里有不可序列化条目时，磁盘上原目标必须原样保留。"""
    path = tmp_path / "liuhao_goals.json"
    original = {
        "goal_id": "g1",
        "state": "completed",
        "natural_language": "original",
        "tasks": [],
    }
    path.write_text(json.dumps({"g1": original}, ensure_ascii=False), encoding="utf-8")
    # 重新加载，让 _goals 与磁盘一致。
    manager._goals = manager._read_goals_file(str(path))
    assert manager._goals["g1"]["natural_language"] == "original"

    # set 不是 JSON 可序列化类型；旧实现会用 {"__unserializable__": str(...)}
    # 覆盖磁盘原值。
    manager._goals["bad"] = {"goal_id": "bad", "payload": {1, 2}, "state": "completed"}
    manager._save_goals()

    on_disk = json.loads(path.read_text(encoding="utf-8"))
    assert on_disk["g1"]["natural_language"] == "original"
    assert "bad" not in on_disk
    assert "__unserializable__" not in path.read_text(encoding="utf-8")


def test_p1_14_parallel_goals_do_not_share_idle_state(manager, monkeypatch):
    """C 先结束而 D 仍在跑时，不能把 agent 标成 idle；D 结束后才 idle。"""
    fake_employee = _FakeEmployee()
    gate = _GatedRuntime()
    monkeypatch.setattr(manager, "_ensure_employee", lambda: fake_employee)
    monkeypatch.setattr(manager, "_ensure_runtime", lambda: gate)
    monkeypatch.setattr(manager, "_save_goals", lambda: None)
    monkeypatch.setattr(manager, "_agent_employee_map", lambda: {})
    monkeypatch.setattr(manager, "_persist_employee", lambda: None)

    entry_c = manager.create_and_execute_goal("search the archive", background=True)
    entry_d = manager.create_and_execute_goal("plan the rollout", background=True)
    goal_c, goal_d = entry_c["goal_id"], entry_d["goal_id"]
    assert gate.started["goal-c"].wait(5)
    assert gate.started["goal-d"].wait(5)

    # 让 C 先结束：只应留下 C 自己的计数，不能把 D 的忙碌状态清掉。
    gate.release_c.set()
    deadline = time.time() + 5
    while goal_c in manager._active_goals and time.time() < deadline:
        time.sleep(0.01)
    assert goal_c not in manager._active_goals
    assert goal_d in manager._active_goals
    assert fake_employee.idle_calls == 0, "先结束的目标不得把仍在跑的目标标 idle"

    # 放开 D，让并发目标全部收尾；只有此刻才允许 idle 一次。
    gate.release_d.set()
    deadline = time.time() + 5
    while manager._active_goals and time.time() < deadline:
        time.sleep(0.01)
    assert manager._active_goals == set()
    assert all(state == "completed" for _, state in fake_employee.finished)
    assert fake_employee.idle_calls == 1
    assert manager._goal_threads == {}


def test_p1_14_abnormal_exit_releases_thread_and_pool(manager, monkeypatch):
    """异常路径（已取得员工之后）也必须清空线程记录并释放 agent 池。"""
    fake_employee = _FakeEmployee()
    manager._goals = {}
    manager._active_goals = set()
    manager._goal_threads = {}

    class ExplodingResult:
        @property
        def state(self):
            raise RuntimeError("boom")

    class BrokenRuntime:
        def run_goal(self, goal_text, goal_id, **kwargs):
            return ExplodingResult()

    monkeypatch.setattr(manager, "_ensure_employee", lambda: fake_employee)
    monkeypatch.setattr(manager, "_ensure_runtime", lambda: BrokenRuntime())
    monkeypatch.setattr(manager, "_agent_employee_map", lambda: {})
    monkeypatch.setattr(manager, "_persist_employee", lambda: None)
    monkeypatch.setattr(manager, "_save_goals", lambda: None)

    # 这条异常是测试故意制造的；吞掉预期内的线程异常，避免污染 pytest 警告。
    seen: list = []
    old_hook = threading.excepthook
    threading.excepthook = lambda args: seen.append(args.exc_value)
    try:
        manager.create_and_execute_goal("search the archive", background=True)
        deadline = time.time() + 5
        while (
            manager._goal_threads or manager._active_goals
        ) and time.time() < deadline:
            time.sleep(0.01)
    finally:
        threading.excepthook = old_hook

    assert seen and isinstance(seen[0], RuntimeError)
    assert manager._goal_threads == {}
    assert manager._active_goals == set()
    assert fake_employee.idle_calls == 1


def test_p1_11_runtime_lazy_init_is_single(manager, monkeypatch):
    """并发首次调用只能构造一个 runtime（否则会出现两份 journal 句柄）。"""
    constructed: list = []
    lock = threading.Lock()

    class SlowRuntime:
        def __init__(self, *args, **kwargs):
            with lock:
                constructed.append(self)
            time.sleep(0.05)
            self.journal = object()
            self.artifact_index = types.SimpleNamespace()

        def set_agent_resolver(self, *args, **kwargs) -> None:
            pass

    monkeypatch.setattr("src.ai.agent_runtime.AgentRuntime", SlowRuntime)
    monkeypatch.setattr(
        "src.ai.lcore.LCore",
        lambda *a, **k: types.SimpleNamespace(
            capability_executor=lambda: None,
            executable_capabilities=lambda: [],
        ),
    )
    monkeypatch.setattr(
        manager,
        "_ensure_employee",
        lambda: types.SimpleNamespace(name="fake", agents=[]),
    )

    results: list = []

    def ensure():
        results.append(manager._ensure_runtime())

    threads = [threading.Thread(target=ensure) for _ in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert len(constructed) == 1, f"runtime 被构造了 {len(constructed)} 次"
    assert all(item is results[0] for item in results)
