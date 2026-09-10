"""能力层可观测性基础设施的单元测试（无外部依赖 / 无网络）。"""

import logging

import pytest

from src.ai.observability import (
    TraceContext,
    get_logger,
    observe,
    recent_traces,
    _recent,
)


def setup_function(_fn):
    """每个用例前清空环形缓冲，避免相互污染。"""
    _recent.clear()
    TraceContext.reset()


def test_get_logger_injects_trace_id(caplog):
    logger = get_logger("obs_test")
    TraceContext.set(trace_id="t-1", correlation_id="c-1")
    with caplog.at_level(logging.INFO):
        logger.info("hello-world")
    recs = [r for r in caplog.records if "hello-world" in r.message]
    assert recs, "日志未被记录"
    assert recs[0].trace_id == "t-1"
    assert recs[0].correlation_id == "c-1"


def test_observe_logs_exit_and_records():
    @observe("demo.fn")
    def fn(x):
        return {"status": "completed", "x": x}

    res = fn(42)
    assert res["x"] == 42
    traces = recent_traces()
    assert len(traces) == 1
    assert traces[0].phase == "demo.fn"
    assert traces[0].outcome == "completed"
    assert traces[0].elapsed_ms >= 0


def test_observe_reraises_and_logs_error(caplog):
    @observe("demo.bad")
    def bad():
        raise ValueError("boom")

    with caplog.at_level(logging.ERROR):
        with pytest.raises(ValueError):
            bad()
    assert any(r.levelno == logging.ERROR for r in caplog.records)
    traces = recent_traces()
    assert traces and traces[0].outcome.startswith("error:")


def test_observe_preserves_return_value():
    @observe("demo.identity")
    def identity(v):
        return v

    assert identity({"a": 1}) == {"a": 1}


def test_trace_context_propagates_through_nested_observe():
    seen = {}

    @observe("outer")
    def outer():
        @observe("inner")
        def inner():
            seen["trace"] = TraceContext.current().trace_id
            return "ok"

        return inner()

    outer()
    assert seen["trace"] is not None


def test_recent_traces_length_matches_calls():
    @observe("demo.n")
    def n(_i):
        return {"status": "completed"}

    for i in range(5):
        n(i)
    assert len(recent_traces()) == 5


def test_trace_id_inherited_across_calls():
    """已存在的 trace_id 应在多次 observe 调用间被继承（同一条调用链）。"""
    TraceContext.set(trace_id="fixed-trace")

    @observe("a")
    def a():
        return {"status": "completed"}

    @observe("b")
    def b():
        return {"status": "completed"}

    a()
    b()
    traces = recent_traces()
    assert len(traces) == 2
    # 两次调用的 trace_id 应一致（observe 在已有 trace 时继承，不重新生成）。
    assert traces[0].trace_id == "fixed-trace" == traces[1].trace_id


def test_correlation_id_persists_via_logger_record(caplog):
    """手动设置的 correlation_id 应在 observe 调用期间透传到日志记录。"""
    TraceContext.set(correlation_id="cid-xyz")

    @observe("demo.cid")
    def fn():
        return {"status": "completed"}

    with caplog.at_level(logging.INFO):
        fn()
    recs = [r for r in caplog.records if r.getMessage().startswith("EXIT")]
    assert recs
    assert recs[0].correlation_id == "cid-xyz"
