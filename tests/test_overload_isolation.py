"""满载隔离：探针豁免、身份解析出循环、goal 线程非阻塞启动（D-1 三条修复）。

三条缺陷的现场都在 ``docs/operations/soak-report-2026-11-08.md`` §12.11：

1. ``RateLimitMiddleware`` 在事件循环线程上同步读 JWT 撤销库（``sqlite3.connect``），
   把 ``/v1/health`` 一起拖到 3 秒超时；
2. 应用自己 new 的 goal 线程走 ``threading.Thread.start()``，满载时无限等
   ``_started``；
3. 探针端点被配置进限流 paths 后仍会付身份解析的账。

这里的用例都设计成**修复回退即变红**（不是"这次没复发"）：
``test_health_stays_fast_while_identity_resolution_is_stuck`` 在身份解析被卡住
1 秒时要求健康接口 < 0.3 秒返回；``test_goal_thread_start_does_not_wait_for_bootstrap``
在 bootstrap 永远不执行的条件下要求启动调用本身立即返回。
"""

from __future__ import annotations

import asyncio
import json
import threading
import time

import pytest
from starlette.applications import Starlette
from starlette.responses import PlainTextResponse
from starlette.routing import Route
from starlette.testclient import TestClient

from src.gateway import rate_limiter as rl


# ---------------------------------------------------------------------------
# 装置
# ---------------------------------------------------------------------------
def _build_app() -> Starlette:
    async def ok(_request):
        return PlainTextResponse("ok")

    paths = (
        "/v1/health",
        "/v1/ready",
        "/v1/metrics",
        "/v1/metrics/prometheus",
        "/v1/metrics/prometheus-extra",
        "/v1/chat",
    )
    return Starlette(routes=[Route(p, ok) for p in paths])


@pytest.fixture
def small_limiter(monkeypatch):
    """把模块级单例换成一个"每次只放 1 个"的限流器，便于观察 429。"""
    limiter = rl.RateLimiter(default_capacity=1, default_rate=0.0001)
    monkeypatch.setattr(rl, "_default_limiter", limiter)
    return limiter


def _wait_lanes_idle(timeout: float = 3.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if rl._idle_lanes.qsize() >= rl._IDENTITY_LANE_COUNT:
            return True
        time.sleep(0.02)
    return rl._idle_lanes.qsize() >= rl._IDENTITY_LANE_COUNT


@pytest.fixture(autouse=True)
def _reset_identity_state():
    """每个用例后把降级状态清零，并等弃置 lane 退出、池子恢复。"""
    yield
    with rl._lane_state_lock:
        rl._identity_degraded = False
        rl._consecutive_identity_timeouts = 0
    deadline = time.monotonic() + 3.0
    while time.monotonic() < deadline:
        with rl._lane_state_lock:
            abandoned = rl._abandoned_running
        if abandoned == 0 and _wait_lanes_idle(0.2):
            break
        time.sleep(0.02)


async def _call_asgi(app, path: str):
    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "GET",
        "scheme": "http",
        "path": path,
        "raw_path": path.encode(),
        "query_string": b"",
        "headers": [],
        "client": ("127.0.0.1", 12345),
        "server": ("testserver", 80),
        "root_path": "",
    }
    messages: list = []

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message):
        messages.append(message)

    await app(scope, receive, send)
    return messages


def _status(messages) -> int:
    for message in messages:
        if message["type"] == "http.response.start":
            return int(message["status"])
    raise AssertionError("没有收到 http.response.start")


# ---------------------------------------------------------------------------
# ① 探针只豁免限流/身份解析，且无法借道绕过
# ---------------------------------------------------------------------------
def test_probe_paths_are_exempt_even_when_configured_as_limited(small_limiter):
    """配置把探针写进 paths，也必须放行 —— 限流器不能饿死监控。"""
    middleware = rl.RateLimitMiddleware(
        _build_app(), paths=("/v1/health", "/v1/chat")
    )
    with TestClient(middleware) as client:
        for _ in range(5):
            assert client.get("/v1/health").status_code == 200

        # 对照：同一个中间件对 /v1/chat 仍然生效（容量 1）。
        assert client.get("/v1/chat").status_code == 200
        assert client.get("/v1/chat").status_code == 429


def test_health_flood_does_not_spend_the_chat_budget(small_limiter):
    """高频打健康接口，不许把别的受限路径的额度一起吃掉。"""
    app = _build_app()
    app.add_middleware(rl.GlobalRateLimitMiddleware, limit_per_minute=2)
    with TestClient(app) as client:
        for _ in range(20):
            assert client.get("/v1/health").status_code == 200

        assert client.get("/v1/chat").status_code == 200
        assert client.get("/v1/chat").status_code == 200
        assert client.get("/v1/chat").status_code == 429


def test_probe_exemption_is_exact_not_prefix():
    """``/v1/metrics/prometheus-extra`` 不是探针，不能被前缀法顺手放过。"""
    middleware = rl.GlobalRateLimitMiddleware(_build_app(), limit_per_minute=1)
    assert middleware._applies("/v1/health") is False
    assert middleware._applies("/v1/metrics/prometheus") is False
    assert middleware._applies("/v1/metrics/prometheus-extra") is True
    assert middleware._applies("/v1/metrics") is False


#: 动手前核对：这三个是公开探针（health.py 直接挂载、无依赖）。
#: ``/v1/metrics/prometheus`` 一直是**需要认证**的抓取端点（无凭据 403），
#: 它只在限流层被豁免，鉴权照旧。
PUBLIC_PROBES = ("/v1/health", "/v1/ready", "/v1/metrics")


def test_probe_endpoints_keep_their_public_contract():
    """认证要求不变：公开的仍公开，要认证的仍要认证。"""
    from src.gateway.main import get_app

    with TestClient(get_app()) as client:
        for path in PUBLIC_PROBES:
            response = client.get(path)
            assert response.status_code in (200, 503), (
                f"{path} 返回 {response.status_code}：探针被新增的认证挡住了"
            )
            assert response.status_code not in (401, 403)

        # 反证：豁免限流 ≠ 放宽鉴权。Prometheus 抓取没有凭据仍是 403。
        prometheus = client.get("/v1/metrics/prometheus")
        assert prometheus.status_code == 403, (
            "prometheus 抓取端点的鉴权被顺手放宽了"
        )


# ---------------------------------------------------------------------------
# ② 身份解析移出事件循环
# ---------------------------------------------------------------------------
def test_identity_resolution_runs_off_the_event_loop(monkeypatch):
    limiter = rl.RateLimiter()
    seen_thread: list[str] = []

    def slow_identity(_request, _trusted=()):
        seen_thread.append(threading.current_thread().name)
        time.sleep(0.3)
        return "ip:127.0.0.1", None

    monkeypatch.setattr(limiter, "_identity", slow_identity)
    ticks = {"n": 0}

    async def scenario():
        async def ticker():
            while True:
                await asyncio.sleep(0.01)
                ticks["n"] += 1

        task = asyncio.create_task(ticker())
        scope = {
            "type": "http",
            "path": "/v1/chat",
            "headers": [],
            "client": ("127.0.0.1", 1),
            "query_string": b"",
        }
        from fastapi import Request

        identity = await rl.resolve_identity_off_loop(limiter, Request(scope), ())
        task.cancel()
        return identity

    identity = asyncio.run(scenario())
    assert identity == ("ip:127.0.0.1", None)
    # 事件循环在 0.3 秒阻塞期间持续跑：旧实现（同步 _identity）这里只会是 0-1。
    assert ticks["n"] >= 15, f"事件循环被身份解析占住了：只走了 {ticks['n']} 个 tick"
    assert seen_thread and seen_thread[0].startswith("liuhao-identity"), (
        f"身份解析没有跑到预热池线程：{seen_thread}"
    )


def test_identity_is_resolved_once_per_request(monkeypatch):
    limiter = rl.RateLimiter()
    calls = {"n": 0}

    def counting_identity(_request, _trusted=()):
        calls["n"] += 1
        return "ip:127.0.0.1", None

    monkeypatch.setattr(limiter, "_identity", counting_identity)
    scope = {"type": "http", "path": "/v1/chat", "headers": [], "client": ("127.0.0.1", 1)}
    from fastapi import Request

    request = Request(scope)

    async def scenario():
        await rl.resolve_identity_off_loop(limiter, request, ())
        await rl.resolve_identity_off_loop(limiter, request, ())

    asyncio.run(scenario())
    assert calls["n"] == 1, "同一次请求把撤销库读了不止一遍"


def test_health_stays_fast_while_identity_resolution_is_stuck(monkeypatch):
    """撤销库/API Key 读卡住 1 秒，健康接口仍必须立刻返回。"""
    limiter = rl.RateLimiter()
    monkeypatch.setattr(rl, "_default_limiter", limiter)
    monkeypatch.setattr(
        limiter,
        "_identity",
        lambda _request, _trusted=(): (time.sleep(1.0), ("ip:127.0.0.1", None))[1],
    )
    middleware = rl.RateLimitMiddleware(
        _build_app(), paths=("/v1/health", "/v1/chat")
    )

    async def scenario():
        chat = asyncio.create_task(_call_asgi(middleware, "/v1/chat"))
        await asyncio.sleep(0.15)  # 让身份解析先卡住
        started = time.monotonic()
        health = await _call_asgi(middleware, "/v1/health")
        elapsed = time.monotonic() - started
        await chat
        return elapsed, health

    elapsed, health = asyncio.run(scenario())
    assert _status(health) == 200
    assert elapsed < 0.3, (
        f"身份解析卡住时健康接口被拖了 {elapsed:.2f}s —— 探针又回到事件循环上了"
    )


# ---------------------------------------------------------------------------
# ④ 身份校验防护：lane 就绪、2 秒超时、abandon、降级自愈、503 vs 401
# ---------------------------------------------------------------------------
def test_identity_lanes_are_warm_before_first_request():
    """经**启动路径**验证：导入即预热，4 条 lane 的 Event 都已 set。

    旧用例直接调 ``warm_identity_pool()``，绕开了真正的启动路径，属于假护栏。
    """
    assert _wait_lanes_idle(2.0), "身份 lane 没有全部回到空闲 —— 预热没有经导入路径生效"
    assert rl.identity_pool_ready() is True, "就绪 Event 没有全部 set（lane 未真正跑起来）"
    assert len(rl._active_lanes) == rl._IDENTITY_LANE_COUNT
    assert rl._idle_lanes.qsize() == rl._IDENTITY_LANE_COUNT


def test_first_request_creates_no_new_threads(monkeypatch):
    """第一条请求不许在请求路径上新建线程（那正是满载时 Thread.start 的入口）。"""
    assert _wait_lanes_idle(2.0)
    limiter = rl.RateLimiter()
    monkeypatch.setattr(
        limiter, "_identity", lambda _r, _t=(): ("ip:127.0.0.1", None)
    )
    before = threading.active_count()
    lane_before = [t.name for t in threading.enumerate() if t.name.startswith("liuhao-identity")]

    scope = {"type": "http", "path": "/v1/chat", "headers": [], "client": ("127.0.0.1", 1)}
    from fastapi import Request

    async def scenario():
        return await rl.resolve_identity_off_loop(limiter, Request(scope), ())

    assert asyncio.run(scenario()) == ("ip:127.0.0.1", None)
    after = threading.active_count()
    lane_after = [t.name for t in threading.enumerate() if t.name.startswith("liuhao-identity")]
    assert after == before, f"首个请求新建了线程：{before} -> {after}"
    assert len(lane_after) == len(lane_before), "首个请求新建了身份 lane"


def test_stuck_identity_store_returns_structured_503(monkeypatch):
    """撤销库挂住 -> 2 秒级超时 -> 结构化 503（带 Retry-After / correlation_id）。"""
    monkeypatch.setenv("LIUHAO_IDENTITY_TIMEOUT_SECONDS", "0.2")
    limiter = rl.RateLimiter()
    monkeypatch.setattr(rl, "_default_limiter", limiter)
    monkeypatch.setattr(
        limiter,
        "_identity",
        lambda _r, _t=(): (time.sleep(1.0), ("ip:127.0.0.1", None))[1],
    )
    middleware = rl.RateLimitMiddleware(_build_app(), paths=("/v1/chat",))

    messages = asyncio.run(_call_asgi(middleware, "/v1/chat"))
    assert _status(messages) == 503

    start = next(m for m in messages if m["type"] == "http.response.start")
    headers = {k.decode().lower(): v.decode() for k, v in start["headers"]}
    body = json.loads(
        next(m for m in messages if m["type"] == "http.response.body")["body"]
    )
    assert set(body) == {"code", "message", "correlation_id", "retry_after"}
    assert body["code"] == "IDENTITY_UNAVAILABLE"
    assert body["retry_after"] == 30
    assert "不是你密码的问题" in body["message"], "503 文案没有把责任从用户身上移开"
    assert headers["retry-after"] == "30"
    assert headers["x-correlation-id"] == body["correlation_id"]


def test_identity_pool_full_fails_fast(monkeypatch):
    """4 条 lane 都在忙时第 5 个请求立即 503，不排队。"""
    monkeypatch.setenv("LIUHAO_IDENTITY_TIMEOUT_SECONDS", "5.0")
    limiter = rl.RateLimiter()
    monkeypatch.setattr(rl, "_default_limiter", limiter)
    monkeypatch.setattr(
        limiter,
        "_identity",
        lambda _r, _t=(): (time.sleep(0.4), ("ip:127.0.0.1", None))[1],
    )
    middleware = rl.RateLimitMiddleware(_build_app(), paths=("/v1/chat",))

    async def scenario():
        jobs = [asyncio.create_task(_call_asgi(middleware, "/v1/chat")) for _ in range(4)]
        await asyncio.sleep(0.1)  # 让 4 条 lane 先占满
        started = time.monotonic()
        fifth = await _call_asgi(middleware, "/v1/chat")
        elapsed = time.monotonic() - started
        await asyncio.gather(*jobs)
        return elapsed, fifth

    elapsed, fifth = asyncio.run(scenario())
    assert _status(fifth) == 503
    assert elapsed < 0.5, f"池满没有快速失败：第 5 个请求等了 {elapsed:.2f}s"


def test_degraded_state_self_heals_without_restart(monkeypatch):
    """连续 3 次超时 -> degraded -> 探针成功即恢复（不靠重启）。"""
    monkeypatch.setattr(rl, "_IDENTITY_PROBE_INTERVAL_SECONDS", 0.05)
    monkeypatch.setattr(rl, "_probe_identity_store", lambda: True)

    for _ in range(3):
        rl._record_identity_timeout()
    assert rl._identity_degraded is True
    with pytest.raises(rl.IdentityCheckDegraded):
        rl._submit_identity_job(lambda: None)

    deadline = time.monotonic() + 3.0
    while time.monotonic() < deadline and rl._identity_degraded:
        time.sleep(0.02)
    assert rl._identity_degraded is False, "30 秒探针没有把服务自愈回来"

    _lane, future = rl._submit_identity_job(lambda: ("ip:127.0.0.1", None))
    assert future.result(timeout=2.0) == ("ip:127.0.0.1", None)


def test_identity_store_unavailable_maps_to_503_not_401(monkeypatch):
    """读不出 -> 503（IDENTITY_UNAVAILABLE），绝不伪装成 401 密码问题。"""
    import src.gateway.policy as policy
    from fastapi import HTTPException
    from src.security.jwt_handler import IdentityStoreUnavailable

    class _BrokenHandler:
        def validate_access_token(self, _token):
            raise IdentityStoreUnavailable("db hung")

    monkeypatch.setattr("src.security.get_jwt_handler", lambda: _BrokenHandler())
    with pytest.raises(HTTPException) as info:
        policy.require_bearer_payload(x_liuhao_token="Bearer abc", authorization=None)
    assert info.value.status_code == 503
    assert info.value.detail["code"] == "IDENTITY_UNAVAILABLE"
    assert "不是你密码的问题" in info.value.detail["message"]


def test_genuine_invalid_token_keeps_401_contract():
    """真无效的令牌仍是 401 + 原文案（两个码、两套话互不串味）。"""
    import src.gateway.policy as policy
    from fastapi import HTTPException

    with pytest.raises(HTTPException) as info:
        policy.require_bearer_payload(
            x_liuhao_token="Bearer not-a-jwt", authorization=None
        )
    assert info.value.status_code == 401
    assert info.value.detail["code"] == "AUTH_TOKEN_INVALID"
    assert info.value.detail["message"] == "访问令牌无效或已过期，请重新登录"


def test_stuck_threads_stay_within_cap_after_repeated_hangs(monkeypatch):
    """连续 20 次卡死/弃置后，线程总数仍 ≤ 4 + 4。"""
    monkeypatch.setenv("LIUHAO_IDENTITY_TIMEOUT_SECONDS", "0.05")
    limiter = rl.RateLimiter()
    monkeypatch.setattr(
        limiter,
        "_identity",
        lambda _r, _t=(): (time.sleep(0.4), ("ip:127.0.0.1", None))[1],
    )
    peak = 0
    for _ in range(20):
        with rl._lane_state_lock:  # 专测上限：每轮清掉降级，让它继续弃置
            rl._identity_degraded = False
            rl._consecutive_identity_timeouts = 0
        scope = {"type": "http", "path": "/v1/chat", "headers": [], "client": ("127.0.0.1", 1)}
        from fastapi import Request

        async def scenario():
            await rl.resolve_identity_off_loop(limiter, Request(scope), ())

        with pytest.raises((rl.IdentityCheckTimeout, rl.IdentityPoolFull)):
            asyncio.run(scenario())
        with rl._lane_state_lock:
            peak = max(peak, len(rl._active_lanes) + rl._abandoned_running)

    assert peak <= rl._IDENTITY_LANE_COUNT + rl._IDENTITY_MAX_ABANDONED
    lane_threads = [t for t in threading.enumerate() if t.name.startswith("liuhao-identity")]
    assert len(lane_threads) <= rl._IDENTITY_LANE_COUNT + rl._IDENTITY_MAX_ABANDONED


def test_metrics_expose_identity_check_counters():
    """两个计数指标在 /v1/metrics 的 payload 里可见（重启归零）。"""
    from src.gateway.health import _metrics_payload_sync

    payload = _metrics_payload_sync("test")
    component = payload["components"]["identity_check"]
    assert "identity_check_degraded_total" in component
    assert "identity_check_rejected_total" in component
    assert component["ready"] is True


# ---------------------------------------------------------------------------
# ③ goal 线程：启动不等待 bootstrap
# ---------------------------------------------------------------------------
def test_goal_thread_start_does_not_wait_for_bootstrap(monkeypatch):
    """新线程永远跑不到 bootstrap 时，启动调用也不许卡住。

    这就是 19:44 现场的另一半：``Thread.start`` 里 ``_started.wait()`` 无超时。
    回退成 ``thread.start()`` 时，下面这个 worker 会永远停在 start 里，
    ``join(0.5)`` 超时、断言变红。
    """
    from src.gateway import ai_management as am

    monkeypatch.setattr(am, "_start_new_thread", lambda *_a, **_k: None)
    thread = threading.Thread(target=lambda: None, daemon=True)

    finished = {"ok": False}

    def starter():
        am._start_goal_thread(thread)
        finished["ok"] = True

    worker = threading.Thread(target=starter, daemon=True)
    worker.start()
    worker.join(0.5)
    assert finished["ok"] is True, "启动 goal 线程时又去等 _started 了（D-1 回退）"
