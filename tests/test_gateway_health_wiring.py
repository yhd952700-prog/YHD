"""健康检查端点的接线与真实性。

这一组存在的理由是一个真实缺陷（Round 81 发现）：

``main.py`` 曾经自己声明 ``health_router = APIRouter(prefix="/v1")``，名字与
``health.py`` 的完全相同，并在 lifespan 里挂了两个 trivial 端点
（``{"status": "ok"}`` / ``{"status": "ready"}``）。后果有两层：

1. ``health.py`` 的 router 从未被 include —— 整份真实实现成了死代码，
   其中 ``/v1/metrics`` 的两处字段错误（``expired_count`` / ``total_usage``
   在 ``get_key_stats()`` 里并不存在）也就从未被执行而暴露；
2. 所有监控拿到的是一个无论子系统死活都回答 "ready" 的探针。

最有价值的一条断言是 :func:`test_main_reuses_the_authoritative_router`：它比较
**对象同一性**。任何"再建一个同名 router"的改法都会立刻失败，而只比较响应字段
的测试在 trivial 实现下也可能侥幸通过。
"""

from __future__ import annotations

import pathlib
import re


import pytest
from fastapi.testclient import TestClient

from src.gateway.main import get_app

REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
MAIN_PY = REPO_ROOT / "src" / "gateway" / "main.py"

#: readiness_probe 必须检查的子系统。少一个就意味着有一类故障不会被探针发现。
REQUIRED_CHECKS = {
    "api_key_manager",
    "jwt_handler",
    "rbac_manager",
    "encryption_manager",
    "master_key_acl",
    "rate_limiter",
}


@pytest.fixture
def client():
    with TestClient(get_app()) as test_client:
        yield test_client


class TestProbeTrafficIsNotUserTraffic:
    """A probe 503 must not look like a user request failure.

    The operational review saw ``/v1/ready`` 503 counted into
    ``error_rate_percent`` and a false CRITICAL (50.0 / 25.0) while the
    service was healthy. The alerting math is covered in
    ``tests/gateway/test_alerting_real.py``; this test pins the WIRING --
    the real middleware must pass ``is_probe=True`` for probe paths.
    """

    def test_ready_503_lands_in_probe_counters_not_error_rate(
        self, client, monkeypatch
    ):
        from src.gateway import health as health_module
        from src.gateway import main as main_module
        from src.observability.metrics import get_metric_collector

        # The collector is imported by the deferred warmup thread, which waits
        # up to 10s for the first screen -- a test never waits that long, so
        # the middleware would be a no-op here. Production has it live once
        # warmup finishes; trigger the same idempotent import so this test
        # exercises the real middleware wiring instead of the import timing.
        main_module._ensure_http_metrics()

        async def _timeout(*_args, **_kwargs):
            raise health_module.ProbeTimeout("probe exceeded 2.0s")

        monkeypatch.setattr(health_module, "_run_probe", _timeout)
        collector = get_metric_collector()
        before_snap = collector.collect_snapshot()
        before_probe = collector.probe_stats()

        response = client.get("/v1/ready")

        assert response.status_code == 503, response.text
        after_probe = collector.probe_stats()
        after_snap = collector.collect_snapshot()

        assert after_probe["requests"] == before_probe["requests"] + 1
        assert after_probe["five_xx"] == before_probe["five_xx"] + 1
        # Nothing leaked into the user-request counters. ``collect_snapshot``
        # exposes the alert-facing metrics only (no raw total_requests key),
        # so assert on the derived ones: neither the error rate nor the
        # latency window may move when a probe 503s.
        assert after_snap["error_rate_percent"] == before_snap["error_rate_percent"]
        assert after_snap["latency_p99_ms"] == before_snap["latency_p99_ms"]

    def test_business_paths_are_not_in_the_probe_set(self):
        from src.gateway.main import PROBE_ONLY_PATHS

        for probe in ("/v1/health", "/v1/ready", "/v1/metrics"):
            assert probe in PROBE_ONLY_PATHS
        # A user-facing route must NOT be excluded, or this fix would silence
        # real failures instead of just the probe false positive.
        assert "/v1/chat" not in PROBE_ONLY_PATHS
        assert "/v1/ready/subsystems" in PROBE_ONLY_PATHS


class TestSchedulerReadiness:
    """排程器起不来时 /v1/ready 必须显性报 unhealthy。

    真实案例：旧库缺列（``no such column: concurrency_key``）导致整条排程器
    不启动，提醒/定时任务静默不响，而 `/v1/ready` 全程健康。
    """

    @staticmethod
    def _broken_host():
        class _Broken:
            running = False
            reason = ""
            start_error = (
                "本地排程器启动失败（提醒与定时任务不会触发）："
                "OperationalError: no such column: concurrency_key"
            )

        return _Broken()

    def test_scheduler_start_failure_is_unhealthy_and_named(self, client):
        client.app.state.local_scheduler_host = self._broken_host()

        response = client.get("/v1/ready")
        body = response.json()

        assert response.status_code == 503, response.text
        scheduler = body["checks"]["scheduler"]
        assert scheduler["status"] == "unhealthy"
        assert scheduler["start_error"]
        assert "提醒与定时任务不会触发" in scheduler["user_message"]
        assert any("Scheduler:" in err for err in body.get("errors", []))

    def test_scheduler_check_is_wired_into_the_real_probe(self, client):
        body = client.get("/v1/ready").json()
        scheduler = body["checks"].get("scheduler")
        assert scheduler is not None, "/v1/ready 没有排程器检查项 —— 接线丢了"
        assert scheduler["status"] == "healthy"
        # 启动宽限窗口内也不许冒充"运行正常"。
        assert scheduler.get("display_status") in {"排程器启动中", "排程器运行中", "排程器未在本进程运行"}

    def test_running_scheduler_reports_healthy(self, client):
        class _Running:
            running = True
            reason = "运行中"
            start_error = ""

        client.app.state.local_scheduler_host = _Running()
        body = client.get("/v1/ready").json()
        scheduler = body["checks"]["scheduler"]
        assert scheduler["status"] == "healthy"
        assert "提醒与定时任务会按时触发" in scheduler["user_message"]


class TestKernelReadiness:
    """关键内核未到 READY 必须进 /v1/ready；非关键失败只作告警。

    真实空档：``initialize_all()`` 的错误只进日志和 /v1/startup，单个关键内核
    （身份/审计/安全/策略）没到 READY 时 /v1/ready 全绿。用一个聚合项避免把
    检查列表逐个撑长。
    """

    def test_critical_kernel_not_ready_blocks_readiness(self, client):
        client.app.state.kernel_lifecycle_report = {
            "initialized": [],
            "skipped_not_present": [],
            "no_canonical_instance": [],
            "errors": [{"name": "identity", "error": "许可证存储不可读"}],
            "snapshot": {
                "identity": {
                    "name": "identity",
                    "has_instance": True,
                    "lifecycle": "uninitialized",
                }
            },
        }

        response = client.get("/v1/ready")
        body = response.json()

        assert response.status_code == 503, response.text
        kernels = body["checks"]["kernels"]
        assert kernels["status"] == "unhealthy"
        assert kernels["critical_not_ready"][0]["name"] == "identity"
        assert "账号身份与登录校验" in kernels["critical_not_ready"][0]["impact"]
        assert "账号身份与登录校验" in kernels["user_message"]
        assert any("Kernels:" in err for err in body.get("errors", []))

    def test_optional_kernel_failure_is_advisory_not_blocking(self, client):
        client.app.state.kernel_lifecycle_report = {
            "initialized": [],
            "skipped_not_present": [],
            "no_canonical_instance": [],
            "errors": [{"name": "memory", "error": "存储不可读"}],
            "snapshot": {
                "memory": {
                    "name": "memory",
                    "has_instance": True,
                    "lifecycle": "uninitialized",
                }
            },
        }

        response = client.get("/v1/ready")
        body = response.json()

        assert response.status_code == 200, response.text
        kernels = body["checks"]["kernels"]
        assert kernels["status"] == "degraded"
        assert kernels["blocks_readiness"] is False
        assert kernels["optional_not_ready"][0]["name"] == "memory"
        assert "kernels" in (body.get("advisories") or [])

    def test_kernel_check_is_wired_into_the_real_probe(self, client):
        assert client.app.state.kernel_lifecycle_report is not None
        body = client.get("/v1/ready").json()
        kernels = body["checks"].get("kernels")
        assert kernels is not None, "/v1/ready 没有内核聚合项 —— 接线丢了"
        assert kernels["status"] == "healthy"


class TestMetricsReadiness:
    """监控失明按告警处理：不阻断就绪，但总览必须看得见。"""

    def test_permanent_metrics_import_failure_is_advisory(self, client, monkeypatch):
        from src.gateway import main as main_module

        monkeypatch.setattr(main_module, "_metrics_import_attempted", True, raising=False)
        monkeypatch.setattr(main_module, "_get_metric_collector", None, raising=False)
        monkeypatch.setattr(main_module, "_RECORD_HTTP_REQUEST", None, raising=False)

        response = client.get("/v1/ready")
        body = response.json()

        assert response.status_code == 200, response.text
        assert body["status"] == "ready"
        metrics = body["checks"]["metrics"]
        assert metrics["status"] == "degraded"
        assert metrics["blocks_readiness"] is False
        assert "错误率告警会失明" in metrics["user_message"]
        assert "metrics" in (body.get("advisories") or [])

    def test_metrics_before_warmup_is_not_a_problem(self, client, monkeypatch):
        from src.gateway import main as main_module

        monkeypatch.setattr(main_module, "_metrics_import_attempted", False, raising=False)
        body = client.get("/v1/ready").json()
        metrics = body["checks"]["metrics"]
        assert metrics["status"] == "healthy"
        assert "预热" in metrics["display_status"]


class TestMasterKeyAclVisibility:
    """启动期 ACL 自检失败必须在 /v1/ready 可见（告警级，不断服）。"""

    def test_acl_failure_is_an_advisory_not_silent(self, client):
        from src.observability import boot_report

        boot_report.reset_degraded()
        boot_report.add_degraded(
            "master_key_acl",
            r"C:\Users\test\data\master.key",
            "master.key ACL 收紧后复核仍不通过（still-exposed），其他主体仍可读取",
            "degraded: 检查数据目录/文件是否只读或 ACL 不可写，以当前用户身份修复后重启",
        )
        try:
            body = client.get("/v1/ready").json()
        finally:
            boot_report.reset_degraded()

        check = body["checks"]["master_key_acl"]
        assert check["status"] == "degraded"
        assert check["blocks_readiness"] is False, "权限问题要让用户看见，但不该断服"
        assert "master_key_acl" in (body.get("advisories") or [])
        assert "ACL" in check["user_message"]

    def test_healthy_acl_has_no_advisory(self, client):
        from src.observability import boot_report

        boot_report.reset_degraded()
        body = client.get("/v1/ready").json()

        check = body["checks"]["master_key_acl"]
        assert check["status"] == "healthy"
        assert "master_key_acl" not in (body.get("advisories") or [])


class TestSchedulerStartTimeout:
    """启动线程卡在 build 而不抛异常时，宽限过后必须转 unhealthy。"""

    def test_alive_thread_past_deadline_is_unhealthy(self, client):
        import time

        class _AliveThread:
            def is_alive(self):
                return True

        client.app.state.local_scheduler_host = None
        client.app.state.scheduler_host_thread = _AliveThread()
        client.app.state.scheduler_start_timeout_s = 60.0
        client.app.state.scheduler_start_deadline = time.monotonic() - 1.0

        response = client.get("/v1/ready")
        body = response.json()

        assert response.status_code == 503, response.text
        scheduler = body["checks"]["scheduler"]
        assert scheduler["status"] == "unhealthy"
        assert scheduler["display_status"] == "排程器启动超时"
        assert "可能卡在初始化" in scheduler["user_message"]

    def test_alive_thread_within_deadline_is_starting(self, client):
        import time

        class _AliveThread:
            def is_alive(self):
                return True

        client.app.state.local_scheduler_host = None
        client.app.state.scheduler_host_thread = _AliveThread()
        client.app.state.scheduler_start_timeout_s = 60.0
        client.app.state.scheduler_start_deadline = time.monotonic() + 60.0

        response = client.get("/v1/ready")
        body = response.json()

        assert response.status_code == 200, response.text
        scheduler = body["checks"]["scheduler"]
        assert scheduler["status"] == "healthy"
        assert scheduler["display_status"] == "排程器启动中"


class TestWiring:
    def test_main_reuses_the_authoritative_router(self):
        """main 暴露的 health_router 必须**就是** health.py 那一个。"""
        from src.gateway import health as health_module
        from src.gateway import main as main_module

        assert main_module.health_router is health_module.health_router, (
            "main.py 又声明了自己的 health_router —— 这会再次遮蔽真实实现"
        )

    def test_main_does_not_declare_its_own_health_router(self):
        """源码级护栏：防止有人再写回 ``health_router = APIRouter(...)``。"""
        source = MAIN_PY.read_text(encoding="utf-8")
        declarations = re.findall(
            r"^\s*health_router\s*=\s*APIRouter\(", source, flags=re.MULTILINE
        )
        assert declarations == [], (
            "main.py 不应自行构造 health_router（应 import health.py 的那个）"
        )

    def test_main_does_not_redefine_health_or_ready_in_the_lifespan(self):
        """lifespan 里不应再出现覆盖真实探针的 trivial 端点。"""
        source = MAIN_PY.read_text(encoding="utf-8")
        assert 'health_router.get("/health"' not in source
        assert 'health_router.get("/ready"' not in source


class TestLiveness:
    def test_health_is_alive(self, client):
        response = client.get("/v1/health")
        assert response.status_code == 200
        body = response.json()
        assert body["status"] == "ok"
        # 来自 health.py 的 liveness 载荷（旧 trivial 版本没有 service 字段）。
        assert body["service"] == "liuhao-gateway"


class TestReadiness:
    def test_ready_reports_the_real_dependency_checks(self, client):
        response = client.get("/v1/ready")
        body = response.json()
        assert body["checks"], "就绪探针没有返回任何检查项 —— 真实实现可能又被遮蔽了"
        assert REQUIRED_CHECKS <= set(body["checks"]), "有子系统没有被探针覆盖"

    def test_status_and_http_code_agree(self, client):
        response = client.get("/v1/ready")
        body = response.json()
        # 告警级检查（blocks_readiness=false，如监控链路）不参与 503 判定。
        blocking = [
            name
            for name, detail in body["checks"].items()
            if detail.get("status") != "healthy" and detail.get("blocks_readiness", True)
        ]
        advisory = [
            name
            for name, detail in body["checks"].items()
            if detail.get("status") != "healthy" and not detail.get("blocks_readiness", True)
        ]
        if blocking:
            # 不健康时必须如实降级：503 + errors，而不是回答 "ready"。
            assert response.status_code == 503
            assert body["status"] == "degraded"
            assert body.get("errors")
        else:
            assert response.status_code == 200
            assert body["status"] == "ready"
        if advisory:
            # 告警不阻断，但也不能彻底隐身：payload 单独列出它们。
            assert set(body.get("advisories") or []) == set(advisory)

    def test_each_check_carries_a_status(self, client):
        checks = client.get("/v1/ready").json()["checks"]
        for name, detail in checks.items():
            assert "status" in detail, name


class TestMetrics:
    def test_metrics_responds(self, client):
        response = client.get("/v1/metrics")
        assert response.status_code == 200, response.text

    def test_metrics_only_reports_fields_that_exist(self, client):
        components = client.get("/v1/metrics").json()["components"]
        api_keys = components["api_key_manager"]
        # 真实字段（get_key_stats 的返回值）都在……
        for field in ("total_keys", "by_status", "by_scope", "expired_count"):
            assert field in api_keys, field
        # ……而不存在的字段不该被凭空填进来。
        assert "total_usage" not in api_keys

    def test_expired_count_is_derived_from_the_status_map(self, client):
        api_keys = client.get("/v1/metrics").json()["components"]["api_key_manager"]
        assert api_keys["expired_count"] == int(api_keys["by_status"].get("expired", 0))

    def test_uptime_is_a_duration_not_an_epoch_timestamp(self, client):
        uptime = client.get("/v1/metrics").json()["uptime_seconds"]
        # 一个把 time.time() 当运行时长填进去的实现会在这里失败：
        # 1.7 亿秒 ≈ 5.7 年，而测试进程才刚起来。
        assert 0 <= uptime < 86_400


class TestProbeIsolation:
    """D-1 / D-2 回归：探针不得跑在事件循环上，也不得无限等待。

    这两条缺陷的运行时表现都是"业务一慢，整站（含 /v1/health）一起不响应"。
    下面的测试不去复现满载，而是把**结构性保证**钉死：
      1. 采集慢于预算时，端点必须在预算内返回 503 + 中文原因（不是挂住）；
      2. 一次慢采集之后，存活探针仍然立即 200（证明不在同一条临界区）；
      3. 存活探针读不到模型配置库时仍返回 200（它只承诺"进程能响应"）。
    """

    def test_metrics_fails_fast_with_chinese_reason(self, client, monkeypatch):
        import time

        from src.gateway import health as health_module

        budget = health_module._PROBE_TIMEOUT_SECONDS

        def slow_payload(_trace_id):
            time.sleep(budget + 0.6)
            return {"components": {}}

        monkeypatch.setattr(health_module, "_metrics_payload_sync", slow_payload)
        started = time.perf_counter()
        response = client.get("/v1/metrics")
        elapsed = time.perf_counter() - started

        assert response.status_code == 503, response.text
        body = response.json()
        # 与业务 503 同一套四字段；`status=busy` 语义落在 `code` 与 `message` 里。
        assert body["code"] == "PROBE_BUSY"
        assert body["retry_after"] == 1
        assert body["correlation_id"]
        assert "慢" in body["message"] or "处理较慢" in body["message"]
        assert set(body) == {"code", "message", "correlation_id", "retry_after"}
        assert response.headers.get("Retry-After") == "1"
        assert response.headers.get("X-Correlation-ID") == body["correlation_id"]
        # 耗时应当≈预算，而不是≈采集时长：说明采集不在请求/事件循环线程上。
        assert elapsed < budget + 0.5, (
            f"指标端点等了 {elapsed:.2f}s，超过探针预算 {budget:.2f}s —— "
            "同步采集又回到事件循环上了"
        )

    def test_liveness_survives_a_slow_metrics_collection(self, client, monkeypatch):
        import time

        from src.gateway import health as health_module

        def slow_payload(_trace_id):
            time.sleep(health_module._PROBE_TIMEOUT_SECONDS + 0.6)
            return {"components": {}}

        monkeypatch.setattr(health_module, "_metrics_payload_sync", slow_payload)
        assert client.get("/v1/metrics").status_code == 503

        started = time.perf_counter()
        health = client.get("/v1/health")
        elapsed = time.perf_counter() - started
        assert health.status_code == 200, health.text
        assert health.json()["status"] == "ok"
        # 存活探针必须是纯内存路径：几十毫秒，而不是等到采集结束。
        assert elapsed < 0.5, f"/v1/health 被指标采集拖住了 {elapsed:.2f}s"

    def test_liveness_reports_unknown_when_provider_config_is_unreadable(
        self, client, monkeypatch
    ):
        from src.gateway import health as health_module

        # 清掉 5 秒快照，强制它去读（失败路径）。
        monkeypatch.setattr(health_module, "_provider_snapshot", {})
        monkeypatch.setattr(health_module, "_provider_snapshot_at", 0.0)

        import src.ai.providers as providers

        def boom():
            raise RuntimeError("模型配置库暂时读不到")

        monkeypatch.setattr(providers, "ai_provider_status", boom)
        response = client.get("/v1/health")
        # 存活语义是"进程还能响应"，不是"配置库还能读"。
        assert response.status_code == 200, response.text
        assert response.json()["ai_provider"]["type"] == "unknown"

    def test_liveness_source_has_no_synchronous_provider_read(self):
        """源码级护栏：liveness 里不能直接出现 ``ai_provider_status()``。"""
        source = (REPO_ROOT / "src" / "gateway" / "health.py").read_text(
            encoding="utf-8")
        start = source.index("async def liveness_probe")
        end = source.index("def _readiness_payload_sync")
        body = source[start:end]
        assert "ai_provider_status()" not in body, (
            "liveness 又在事件循环线程上直接读模型配置库了（D-1 的僵死入口）"
        )


class TestIdempotencyDedupeAdvisory:
    """ACTION-01 后续：幂等账本缺失/不可写不允许静默。

    账本缺席时硬副作用能力会被 fail-closed 拒绝，安全能力放行；两种情况都在
    健康面留下告警级检查（``blocks_readiness=False``），并在 ``advisories`` 里列出。
    """

    def test_hard_side_effect_refusal_is_visible_but_not_blocking(
        self, client, monkeypatch
    ):
        from src.gateway import health as health_module

        monkeypatch.setattr(
            health_module,
            "_execution_dedupe_advisories",
            lambda: [
                {
                    "code": "hard_side_effect_without_ledger",
                    "status": "degraded",
                    "advisory": True,
                    "blocks_readiness": False,
                    "display_status": "硬副作用已拒绝（账本未接入）",
                    "display_name": "幂等去重",
                    "user_message": (
                        "执行引擎没有接入幂等账本（journal），已拒绝执行硬副作用能力"
                        "（network_bus、personal.write…）。解决方式：注入 "
                        "ExecutionJournal，或通过网关部署运行（网关默认已接入账本）。"
                    ),
                    "capability": "network_bus",
                    "capabilities": ["network_bus"],
                    "occurrences": 3,
                    "first_seen_at": "2026-11-08T10:00:00+00:00",
                    "last_seen_at": "2026-11-08T10:00:03+00:00",
                }
            ],
        )

        response = client.get("/v1/ready")
        assert response.status_code == 200, "告警不得把就绪探针打成 503"
        body = response.json()
        check = body["checks"]["idempotency_dedupe"]
        assert check["status"] == "degraded"
        assert check["blocks_readiness"] is False
        assert check["display_status"] == "硬副作用已拒绝（账本未接入）"
        # 文案要让人看懂「哪些能力被挡了、怎么解」，不是一行术语。
        assert "ExecutionJournal" in check["user_message"]
        assert "网关默认已接入账本" in check["user_message"]
        assert "本次被挡下的硬副作用能力：network_bus" in check["user_message"]
        assert check["codes"] == ["hard_side_effect_without_ledger"]
        assert check["capabilities"] == ["network_bus"]
        assert check["occurrences"] == 3
        assert "idempotency_dedupe" in (body.get("advisories") or [])

    def test_safe_capability_advisory_names_blocked_class(self, client, monkeypatch):
        from src.gateway import health as health_module

        monkeypatch.setattr(
            health_module,
            "_execution_dedupe_advisories",
            lambda: [
                {
                    "code": "ledger_missing_safe_replay",
                    "status": "degraded",
                    "advisory": True,
                    "blocks_readiness": False,
                    "display_status": "幂等账本未接入（安全能力已放行）",
                    "display_name": "幂等去重",
                    "user_message": (
                        "执行引擎没有接入幂等账本（journal）：只读/可安全重入能力已放行；"
                        "硬副作用能力会被拒绝执行。解决方式：注入 ExecutionJournal。"
                    ),
                    "capability": "file.read",
                    "capabilities": ["file.read"],
                    "occurrences": 1,
                    "first_seen_at": "2026-11-08T10:00:00+00:00",
                    "last_seen_at": "2026-11-08T10:00:00+00:00",
                }
            ],
        )

        response = client.get("/v1/ready")
        assert response.status_code == 200
        check = response.json()["checks"]["idempotency_dedupe"]
        assert check["status"] == "degraded"
        assert check["display_status"] == "幂等账本未接入（安全能力已放行）"
        assert "硬副作用能力会被拒绝" in check["user_message"]
        assert check["capabilities"] == ["file.read"]

    def test_healthy_ledger_has_no_advisory(self, client, monkeypatch):
        from src.gateway import health as health_module

        monkeypatch.setattr(health_module, "_execution_dedupe_advisories", lambda: [])
        response = client.get("/v1/ready")
        assert response.status_code == 200
        body = response.json()
        check = body["checks"]["idempotency_dedupe"]
        assert check["status"] == "healthy"
        assert check["blocks_readiness"] is False
        assert "idempotency_dedupe" not in (body.get("advisories") or [])



def _default_executor_threads(loop) -> int:
    """默认执行器当前线程数；尚未创建执行器时按 0 计（不抛 AttributeError）。"""
    executor = getattr(loop, "_default_executor", None)
    return len(executor._threads) if executor is not None else 0


def _assert_default_executor_warmed(before: int, after: int, workers: int) -> None:
    """验收不变量：默认线程池必须在启动时建满，且运行期不再新建线程。"""
    assert before == workers, (
        "默认线程池没有被预热：运行期第一次 submit 会在事件循环里建线程（D-1）"
    )
    assert after == workers, "运行期又新建了线程：满载时这一行会卡住事件循环（D-1）"


class TestThreadPoolStartDoesNotStallLoop:
    """D-1 的第二个入口：anyio 在**事件循环线程上**创建线程池工作线程。

    实测（2026-11-07，宿主 CPU 100%，``rss8h``）：事件循环永久停在
    ``threading.Thread.start()`` 的 ``self._started.wait()``（无超时），
    ``/v1/health``、``/v1/metrics``、``/v1/ready`` 一起停摆 967 秒以上，
    看门狗每 15 秒告警但无法自愈。同一时刻 ``liuhao-diag`` 线程也停在同一个
    ``Thread.start``（``ThreadingMixIn`` 每连接一个线程），证明这是进程级的
    "新线程跑不起来"，不是某一把业务锁。

    ``install_nonblocking_worker_start()`` 把这一步从"等新线程真正开始执行"
    降级为"发出去就不再等"，于是线程创建停滞最多拖住**一个请求**，永远拖不住
    事件循环。下面的测试用"线程永不 bootstrap"复现那个卡点。
    """

    def test_worker_thread_start_returns_without_waiting_for_bootstrap(
        self, monkeypatch
    ):
        import threading
        import time
        from collections import deque

        from anyio._backends import _asyncio as anyio_asyncio

        from src.gateway import main as main_module

        # 模拟"线程创建被接受但 bootstrap 永远不执行"（实测卡点的最小复现）。
        # 这一步必须在 install 之前 patch：install 只改 WorkerThread.start 的
        # 实现，真正的启动动作在调用时查模块全局。
        monkeypatch.setattr(
            main_module, "_start_new_thread", lambda fn, args: 1, raising=True
        )
        # WorkerThread 构造里会读"当前事件循环时间"；本测试刻意在循环外做，
        # 所以把它钉成常数（被测的只有 start 是否阻塞）。
        monkeypatch.setattr(
            anyio_asyncio.AsyncIOBackend, "current_time", staticmethod(lambda: 0.0)
        )
        assert main_module.install_nonblocking_worker_start() is True

        class _DummyRootTask:
            _loop = None

            def add_done_callback(self, *args, **kwargs):
                return None

            def remove_done_callback(self, *args, **kwargs):
                return None

        worker = anyio_asyncio.WorkerThread(
            _DummyRootTask(), set(), deque()
        )
        outcome = {}

        def _start_it():
            began = time.monotonic()
            worker.start()
            outcome["seconds"] = time.monotonic() - began

        helper = threading.Thread(target=_start_it, daemon=True)
        helper.start()
        helper.join(2.0)
        assert not helper.is_alive(), (
            "WorkerThread.start 又变成无限等待了：满载时事件循环会被这一行冻住（D-1）"
        )
        assert outcome["seconds"] < 0.5

    def test_install_is_idempotent(self):
        from src.gateway import main as main_module

        assert main_module.install_nonblocking_worker_start() is True
        assert main_module.install_nonblocking_worker_start() is True

    def test_probe_pool_threads_are_created_before_the_first_probe(self, monkeypatch):
        """探针线程池必须**真的**建满，且 4 条探针能同时跑。

        为什么用独立的池：``_PROBE_POOL`` 是模块级全局，前面的 TestClient 用例已经
        用过 /v1/health，会顺手把线程建出来 —— 在共享池上断言 ``== 4`` 会被前面的
        测试"喂饱"而假绿（`rss8h4` 的现场就是只建出 2 条，见 §12.11）。
        这里换一个新池，并要求 4 个任务能**同时**站在栅栏上（少一条线程就超时失败）。
        """
        import threading
        from concurrent.futures import ThreadPoolExecutor

        from src.gateway import health

        fresh = ThreadPoolExecutor(max_workers=4, thread_name_prefix="liuhao-probe-test")
        monkeypatch.setattr(health, "_PROBE_POOL", fresh)
        try:
            assert health.warm_probe_pool() == 4
            assert len(fresh._threads) == 4, "预热没有真的建出 4 条线程"

            gate = threading.Barrier(4)

            def _stand(_item=0, gate=gate):
                try:
                    gate.wait(1.0)
                    return True
                except threading.BrokenBarrierError:
                    return False

            outcomes = list(fresh.map(_stand, range(4)))
            assert outcomes == [True, True, True, True], (
                "4 个探针任务没能同时开跑：线程数不足，满载时会现场建线程（D-1）"
            )
            assert len(fresh._threads) == 4, "运行期又新建了线程"
        finally:
            fresh.shutdown(wait=False)

    def test_default_executor_threads_are_created_before_first_to_thread(self):
        """``asyncio.to_thread`` 用的默认线程池也必须在启动时建满。

        这条路径与 anyio 的卡点是同一个机制：线程第一次 submit 才创建，
        创建发生在事件循环线程上。知识库文档解析就在异步端点里用它。
        """
        import asyncio

        from src.gateway import main as main_module

        async def scenario():
            workers = await main_module.warm_default_executor(4)
            loop = asyncio.get_running_loop()
            before = _default_executor_threads(loop)
            await asyncio.to_thread(lambda: None)
            after = _default_executor_threads(loop)
            return workers, before, after

        workers, before, after = asyncio.run(scenario())
        assert workers == 4
        _assert_default_executor_warmed(before, after, 4)

    def test_the_warm_guard_goes_red_without_prewarming(self):
        """反证：不预热时，上面那条不变量**必须**失败。

        不预热，"首次 submit 才建线程"的旧行为就回来了（before=0、after=1）。
        这里用**同一条**断言跑这个旧行为，并断言它确实抛 ``AssertionError`` ——
        证明护栏不是空转。若有人把 lifespan 里的 ``await warm_default_executor()``
        删掉（或让 ``warm_default_executor()`` 变成空操作），正例
        ``test_default_executor_threads_are_created_before_first_to_thread``
        就会以完全相同的方式变红。
        """
        import asyncio

        async def scenario():
            loop = asyncio.get_running_loop()
            before = _default_executor_threads(loop)
            await asyncio.to_thread(lambda: None)
            after = _default_executor_threads(loop)
            return before, after

        before, after = asyncio.run(scenario())
        assert before == 0, "未预热时默认线程池不应已有线程"
        assert after == 1, "未预热时应由第一次 submit 现场建线程（旧行为）"
        with pytest.raises(AssertionError, match="没有被预热"):
            _assert_default_executor_warmed(before, after, 4)



