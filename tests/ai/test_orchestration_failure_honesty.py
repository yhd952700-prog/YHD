"""编排层失败路径的诚实性回归（2026-10-04 三个 P0 缺陷）。

这三个缺陷有一个共同形状：**失败路径本身先崩溃，于是真实失败原因被一个
与业务无关的二次异常顶替**。它们在 venv 里全部测不出来，因为

* ``end_span(span, error=e)`` 的参数不匹配只在**真的走到 except 分支**时触发，
  而既有测试（``tests/test_goal_task_graph.py``）只测 happy path；
* ``execute_graph`` 的死循环只在「依赖任务失败」这一种图结构下出现，
  而既有测试从不构造这种图；
* provider 的 ``error_types`` 导入在 venv 里永远可解析（editable ``.pth``）。

所以这里全部用**「先证明会坏，再证明修好了」**的方式写：每个用例都直接
驱动那条真实失败路径，并断言拿到的是**业务错误**而不是二次异常。
"""

from __future__ import annotations

import threading

from src.ai.goal_task_graph import (
    GoalDefinition,
    GoalStatus,
    GoalTaskGraph,
    TaskNode,
    TaskStatus,
)
from src.observability.tracing import end_span


# --------------------------------------------------------------------------- #
# 缺陷 1：end_span(span, error=e) —— 收尾时自己先崩
# --------------------------------------------------------------------------- #
class TestEndSpanErrorKwarg:
    """``end_span`` 此前只接受 1 个位置参数。

    3 处失败路径（``employee.py:125``、``goal_task_graph.py:274/535``）一直按
    ``end_span(span, error=e)`` 调用它，于是**每一条失败路径在收尾时抛**
    ``TypeError: end_span() got an unexpected keyword argument 'error'``。
    后果不是"少了一条 trace"，而是失败被二次异常顶替：调用方拿到一个与真实
    原因无关的 TypeError，真实的 provider/task 错误被彻底吞掉。
    """

    def test_end_span_accepts_error_kwarg(self):
        """正向：签名必须接受 error=（这是 3 处调用点一直在用的形态）。"""
        assert end_span(None, error=RuntimeError("boom")) is None

    def test_end_span_records_exception_on_real_span(self):
        """error 必须真的落到 span 上，而不是被静默丢弃。"""
        recorded = {}

        class FakeSpan:
            def record_exception(self, exc):
                recorded["exc"] = exc

            def set_attribute(self, k, v):
                recorded[k] = v

            def end(self):
                recorded["ended"] = True

        class Ctx:
            def __init__(self):
                self.span = FakeSpan()

            def __exit__(self, *a):
                self.span.end()

        err = RuntimeError("upstream 500")
        end_span(Ctx(), error=err)
        assert recorded.get("exc") is err, "异常没有被记录到 span"
        assert recorded.get("error.type") == "RuntimeError"
        assert recorded.get("error.message") == "upstream 500"
        assert recorded.get("ended") is True, "span 未被结束"

    def test_end_span_never_raises_even_if_recording_fails(self):
        """可观测性永远不能反过来打断业务路径。"""

        class HostileSpan:
            def record_exception(self, exc):
                raise RuntimeError("tracer exploded")

            def set_attribute(self, k, v):
                raise RuntimeError("tracer exploded")

            def end(self):
                raise RuntimeError("tracer exploded")

        class Ctx:
            span = HostileSpan()

            def __exit__(self, *a):
                self.span.end()

        # 不得抛出
        end_span(Ctx(), error=RuntimeError("business error"))

    def test_end_span_error_is_optional(self):
        """向后兼容：既有 20+ 处 ``end_span(span)`` 单参调用必须照常工作。"""
        sentinel = object()

        class Ctx:
            def __init__(self):
                self.exited = False

            def __exit__(self, *a):
                self.exited = True

        c = Ctx()
        end_span(c)
        assert c.exited is True
        assert end_span(None) is None
        assert sentinel is not None


# --------------------------------------------------------------------------- #
# 缺陷 2：execute_graph 在依赖失败时死循环挂死
# --------------------------------------------------------------------------- #
def _graph_with_chain() -> GoalTaskGraph:
    """t1 会被执行器搞失败；t2 依赖 t1 ⇒ t2 永远无法 ready。"""
    g = GoalTaskGraph(enable_observability=False)
    g.goal = GoalDefinition(id="g1", description="demo")
    t1 = TaskNode(id="t1", description="fails", depends_on=[])
    t2 = TaskNode(id="t2", description="needs t1", depends_on=["t1"])
    g.tasks["t1"] = t1
    g.tasks["t2"] = t2
    g.goal_tasks["g1"] = ["t1", "t2"]
    return g


def _failing_executor(agent_id, desc):
    if desc == "fails":
        raise RuntimeError("boom")
    return "ok"


class TestExecuteGraphTerminates:
    """``execute_graph`` 此前在「无 ready 任务但仍有剩余」时执行
    ``time.sleep(0.1); continue`` —— 一个**永不终止**的忙等。

    触发条件极普通：任何「前置任务失败 + 后续任务依赖它」的图。实测挂死
    （线程 8s 后仍存活）。而这不是竞态：上一轮提交的所有 future 都在
    ``as_completed`` 里被完整收集后才回到循环顶部，所以「没有 ready 任务」
    永远不可能是「还在飞」的状态 —— 它是可证明的终态，必须诚实终止。
    """

    def test_returns_instead_of_hanging(self):
        g = _graph_with_chain()
        out = {}

        def run():
            out["r"] = g.execute_graph("g1", _failing_executor, max_parallel=2)

        th = threading.Thread(target=run, daemon=True)
        th.start()
        th.join(timeout=15)

        assert not th.is_alive(), (
            "execute_graph 挂死未返回 —— 依赖任务失败时它会无限忙等，"
            "调用方永远拿不到结果，线程池也被永久占用"
        )
        assert "r" in out, "execute_graph 未产出结果"

    def test_blocked_task_fails_with_real_reason(self):
        """被阻塞的任务必须**失败并说明真实原因**，不能悄悄消失。"""
        g = _graph_with_chain()
        r = g.execute_graph("g1", _failing_executor, max_parallel=2)

        assert r["status"] == "failed"
        assert r["completed"] == 0
        # t1 与 t2 都必须有结论：一个不多、一个不少
        assert set(r["results"]) == {"t1", "t2"}

        # t1 的真实错误必须原样透出（不被 end_span TypeError 顶替）
        assert r["results"]["t1"]["error"] == "boom"

        # t2 必须说明它为什么跑不了，且指名那个失败的前置任务
        t2 = r["results"]["t2"]
        assert t2["status"] == "failed"
        assert "t1" in t2["error"], f"未指明阻塞来源：{t2['error']!r}"
        assert g.tasks["t2"].status == TaskStatus.FAILED
        assert g.tasks["t2"].error == t2["error"]

    def test_goal_state_reflects_failure_not_success(self):
        """goal 级状态不得停留在 EXECUTING（挂死时就是这个状态）。"""
        g = _graph_with_chain()
        g.execute_graph("g1", _failing_executor, max_parallel=2)
        assert g.goal.status == GoalStatus.FAILED

    def test_circular_dependency_still_terminates(self):
        """环依赖路径不能被本次改动破坏（它此前就能终止）。"""
        g = GoalTaskGraph(enable_observability=False)
        g.goal = GoalDefinition(id="g2", description="cycle")
        a = TaskNode(id="a", description="a", depends_on=["b"])
        b = TaskNode(id="b", description="b", depends_on=["a"])
        g.tasks["a"] = a
        g.tasks["b"] = b
        g.goal_tasks["g2"] = ["a", "b"]

        out = {}
        th = threading.Thread(
            target=lambda: out.update(r=g.execute_graph("g2", _failing_executor)),
            daemon=True,
        )
        th.start()
        th.join(timeout=15)
        assert not th.is_alive(), "环依赖图挂死"
        assert out["r"]["status"] == "failed"
        assert "Circular" in out["r"]["results"]["a"]["error"]

    def test_missing_dependency_is_reported_honestly(self):
        """依赖一个不存在的任务 —— 也要终止并说清原因。"""
        g = GoalTaskGraph(enable_observability=False)
        g.goal = GoalDefinition(id="g3", description="missing dep")
        t = TaskNode(id="t1", description="orphan", depends_on=["does_not_exist"])
        g.tasks["t1"] = t
        g.goal_tasks["g3"] = ["t1"]

        out = {}
        th = threading.Thread(
            target=lambda: out.update(r=g.execute_graph("g3", _failing_executor)),
            daemon=True,
        )
        th.start()
        th.join(timeout=15)
        assert not th.is_alive(), "依赖不存在时挂死"
        r = out["r"]
        assert r["status"] == "failed"
        assert "does_not_exist" in r["results"]["t1"]["error"]


# --------------------------------------------------------------------------- #
# 缺陷 3：partial 被记成 COMPLETED
# --------------------------------------------------------------------------- #
class TestPartialIsNotCompleted:
    """部分成功此前被写成 ``GoalStatus.COMPLETED``。

    ``GoalStatus`` 是 ``save_state`` 持久化、``get_graph_summary`` 汇报的东西，
    所以把 partial 折叠成 COMPLETED 会让「部分失败」在进程重启后与「干净成功」
    无法区分 —— 这正是「谎报成功」。
    """

    def test_partial_success_is_not_recorded_as_completed(self):
        g = GoalTaskGraph(enable_observability=False)
        g.goal = GoalDefinition(id="g4", description="partial")
        ok = TaskNode(id="ok", description="ok", depends_on=[])
        bad = TaskNode(id="bad", description="bad", depends_on=[])
        g.tasks["ok"] = ok
        g.tasks["bad"] = bad
        g.goal_tasks["g4"] = ["ok", "bad"]

        def executor(agent_id, desc):
            if desc == "bad":
                raise RuntimeError("nope")
            return "fine"

        r = g.execute_graph("g4", executor, max_parallel=2)
        assert r["status"] == "partial"
        assert r["completed"] == 1 and r["failed"] == 1
        assert g.goal.status == GoalStatus.PARTIAL, (
            f"部分成功被记成 {g.goal.status}，重启后与干净成功不可区分"
        )
        assert g.goal.status != GoalStatus.COMPLETED

    def test_partial_survives_save_load_roundtrip(self):
        """持久化往返后仍须是 partial（这是谎报成功真正会伤到的地方）。"""
        import json

        g = GoalTaskGraph(enable_observability=False)
        g.goal = GoalDefinition(id="g5", description="partial persist")
        ok = TaskNode(id="ok", description="ok", depends_on=[])
        bad = TaskNode(id="bad", description="bad", depends_on=[])
        g.tasks["ok"] = ok
        g.tasks["bad"] = bad
        g.goal_tasks["g5"] = ["ok", "bad"]

        def executor(agent_id, desc):
            if desc == "bad":
                raise RuntimeError("nope")
            return "fine"

        g.execute_graph("g5", executor, max_parallel=2)

        dumped = json.loads(json.dumps({
            "status": g.goal.status.value,
            "tasks": [{"id": t.id, "status": t.status.value} for t in g.tasks.values()],
        }))
        assert dumped["status"] == "partial"
        assert GoalStatus(dumped["status"]) is GoalStatus.PARTIAL


# --------------------------------------------------------------------------- #
# 回归：happy path 不得被修复破坏
# --------------------------------------------------------------------------- #
class TestHappyPathUnaffected:
    def test_all_success_still_completes(self):
        g = GoalTaskGraph(enable_observability=False)
        g.goal = GoalDefinition(id="g6", description="all ok")
        for i in ("a", "b", "c"):
            g.tasks[i] = TaskNode(id=i, description=i, depends_on=[])
        g.goal_tasks["g6"] = ["a", "b", "c"]
        r = g.execute_graph("g6", lambda a, d: f"done:{d}", max_parallel=3)
        assert r["status"] == "completed"
        assert r["completed"] == 3 and r["failed"] == 0
        assert g.goal.status == GoalStatus.COMPLETED

    def test_dependency_order_still_respected(self):
        """t2 真的在 t1 之后跑（依赖语义没被本次终止逻辑破坏）。"""
        order = []

        g = GoalTaskGraph(enable_observability=False)
        g.goal = GoalDefinition(id="g7", description="ordered")
        t1 = TaskNode(id="t1", description="first", depends_on=[])
        t2 = TaskNode(id="t2", description="second", depends_on=["t1"])
        g.tasks["t1"] = t1
        g.tasks["t2"] = t2
        g.goal_tasks["g7"] = ["t1", "t2"]

        def executor(agent_id, desc):
            order.append(desc)
            return "ok"

        r = g.execute_graph("g7", executor, max_parallel=2)
        assert r["status"] == "completed"
        assert order == ["first", "second"]
