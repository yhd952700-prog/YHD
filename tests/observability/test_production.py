"""Phase 7a 生产化就绪层测试。

设计文档：``docs/PRODUCTION-READINESS-DESIGN.md``。

本文件里最有价值的三类断言：

1. :func:`test_probe_registry_covers_every_kernel_on_disk` —— 动态对比磁盘上的
   内核目录与探针注册表。新增一个内核却忘了加探针，会立刻失败（防"14 内核"叙事
   在代码里悄悄退化成 13）。
2. :func:`test_gateway_exposes_the_three_new_routes` —— 检查真实 FastAPI 路由表。
   这正是本仓库反复踩的坑：函数写好了但没人调用（孤儿）。只测模块函数是无法发现的。
3. :func:`test_findings_never_contain_config_values` —— 预检报告若把密钥值带出去，
   一个"配置审计端点"就变成了泄露源。
"""

from __future__ import annotations

import json
import pathlib

import pytest
from fastapi.testclient import TestClient

from src.observability import production
from src.observability.production import (
    PLACEHOLDER_SECRETS_MIRROR,
    PROMETHEUS_CONTENT_TYPE,
    STATUS_DEGRADED,
    STATUS_HEALTHY,
    STATUS_UNAVAILABLE,
    ProductionConfigError,
    check_subsystems,
    prometheus_available,
    prometheus_enabled,
    prometheus_response,
    validate_production_config,
)

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
KERNELS_DIR = REPO_ROOT / "src" / "kernels"


def _collect_route_paths(routes, out=None):
    """递归收集真实路由路径，穿透 FastAPI 0.141 的惰性 ``_IncludedRouter``。"""
    if out is None:
        out = set()
    for route in routes:
        inner = getattr(route, "original_router", None)
        if inner is not None:
            _collect_route_paths(inner.routes, out)
            continue
        path = getattr(route, "path", None)
        if path:
            out.add(path)
    return out


# ============================================================
# 1) Prometheus 暴露
# ============================================================


class TestPrometheusExposure:
    def test_export_is_available(self):
        ok, reason = prometheus_available()
        assert ok is True, reason

    def test_content_type_is_prometheus_text_format(self):
        _body, content_type = prometheus_response()
        assert content_type == PROMETHEUS_CONTENT_TYPE

    def test_export_contains_the_real_metric_families(self):
        body, _ = prometheus_response()
        text = body.decode("utf-8")
        # 取自 src/observability/metrics.py 的真实指标名，而非凭空假设。
        for family in (
            "http_requests_total",
            "provider_requests_total",
            "agent_tasks_total",
            "goal_decompositions_total",
            "task_execution_total",
            "process_cpu_seconds_total",
        ):
            assert f"# HELP {family}" in text, f"缺少指标族 {family}"
            assert f"# TYPE {family}" in text, f"缺少类型声明 {family}"

    def test_export_is_not_empty(self):
        body, _ = prometheus_response()
        assert len(body) > 500

    def test_enabled_by_default_and_respects_falsy_values(self):
        assert prometheus_enabled(env={}) is True
        assert prometheus_enabled(env={production.PROMETHEUS_ENV: "1"}) is True
        for falsy in ("0", "false", "off", "no", "disable", "DISABLED"):
            assert prometheus_enabled(env={production.PROMETHEUS_ENV: falsy}) is False, falsy

    def test_empty_export_is_rejected_rather_than_reported_as_success(self, monkeypatch):
        """空内容必须抛错 —— 静默返回空串会让监控以为"没指标"而不是"导出坏了"。"""
        monkeypatch.setattr("src.observability.metrics.generate_metrics", lambda: b"")
        with pytest.raises(production.ProductionError):
            prometheus_response()


# ============================================================
# 2) 子系统探针
# ============================================================


class TestSubsystemProbes:
    def test_probe_registry_covers_every_kernel_on_disk(self):
        """防漂移：磁盘上的内核目录必须与探针注册表一一对应。"""
        on_disk = {
            d.name
            for d in KERNELS_DIR.iterdir()
            if d.is_dir() and not d.name.startswith("_") and d.name != "__pycache__"
        }
        probed = {name for name, _mod, _getter in production.SUBSYSTEM_PROBES}
        assert probed == on_disk, (
            f"探针注册表与磁盘内核不一致。缺少探针: {on_disk - probed}；"
            f"多余探针: {probed - on_disk}"
        )

    def test_registry_has_fourteen_probes(self):
        assert len(production.SUBSYSTEM_PROBES) == 14

    def test_probe_names_are_unique(self):
        names = [n for n, _m, _g in production.SUBSYSTEM_PROBES]
        assert len(names) == len(set(names))

    def test_real_kernels_are_all_healthy(self):
        report = check_subsystems()
        assert report.ready is True
        assert report.counts[STATUS_HEALTHY] == 14, report.to_dict()
        assert report.counts[STATUS_UNAVAILABLE] == 0

    def test_report_is_serialisable(self):
        payload = check_subsystems().to_dict()
        json.dumps(payload)  # 不抛即通过
        assert payload["status"] == "ready"
        assert payload["total"] == 14

    def test_broken_module_is_unavailable_and_does_not_raise(self):
        report = check_subsystems(
            probes=(("ghost", "src.kernels.does_not_exist_at_all", "get_ghost"),)
        )
        assert report.subsystems[0].status == STATUS_UNAVAILABLE
        assert report.subsystems[0].detail["stage"] == "import"
        assert report.ready is False

    def test_missing_getter_is_unavailable(self):
        report = check_subsystems(
            probes=(("memory", "src.kernels.memory", "get_no_such_thing"),)
        )
        assert report.subsystems[0].status == STATUS_UNAVAILABLE
        assert report.subsystems[0].detail["stage"] == "getter"

    def test_construct_failure_is_unavailable(self):
        class Exploding:
            def __init__(self):
                raise RuntimeError("boom")

        report = check_subsystems(probes=(("boom", __name__, "_exploding_getter"),))
        assert report.subsystems[0].status == STATUS_UNAVAILABLE
        assert report.subsystems[0].detail["stage"] == "construct"
        assert "boom" in report.subsystems[0].detail["error"]
        assert not report.ready

    def test_degraded_stats_does_not_block_readiness(self):
        report = check_subsystems(probes=(("degraded", __name__, "_bad_stats_getter"),))
        assert report.subsystems[0].status == STATUS_DEGRADED
        assert report.ready is True, "degraded 刻意不阻断就绪，否则探针会因噪音被运维关掉"

    def test_instance_without_stats_is_healthy(self):
        report = check_subsystems(probes=(("nostats", __name__, "_no_stats_getter"),))
        assert report.subsystems[0].status == STATUS_HEALTHY
        assert report.subsystems[0].detail["stats"] == "not_available"

    def test_readiness_requires_no_unavailable(self):
        mixed = check_subsystems(
            probes=(
                ("ok", "src.kernels.memory", "get_memory_kernel"),
                ("ghost", "src.kernels.nope", "get_x"),
            )
        )
        assert mixed.ready is False
        assert mixed.counts[STATUS_UNAVAILABLE] == 1


# --- 供上面探针测试使用的模块级 getter ---


def _exploding_getter():
    raise RuntimeError("boom")


class _BadStats:
    def stats(self):
        raise ValueError("stats exploded")


def _bad_stats_getter():
    return _BadStats()


class _NoStats:
    pass


def _no_stats_getter():
    return _NoStats()


# ============================================================
# 3) 生产配置预检
# ============================================================


class TestPreflight:
    def test_placeholder_mirror_matches_the_authoritative_set(self):
        """镜像常量若与 jwt_handler 漂移，本测试立刻失败（这是它存在的唯一理由）。"""
        from src.security.jwt_handler import _PLACEHOLDER_SECRETS

        assert frozenset(_PLACEHOLDER_SECRETS) == PLACEHOLDER_SECRETS_MIRROR

    def test_missing_secret_key_is_critical(self):
        report = validate_production_config(env={})
        codes = {f.code for f in report.findings}
        assert "secret_key_missing" in codes
        assert report.ok is False

    def test_placeholder_secret_key_is_critical(self):
        report = validate_production_config(env={"SECRET_KEY": "replace-me"})
        assert "secret_key_placeholder" in {f.code for f in report.findings}
        assert report.ok is False

    def test_placeholder_jwt_secret_is_critical(self):
        report = validate_production_config(
            env={"SECRET_KEY": "a-real-long-random-value",
                 "LIUHAO_JWT_SECRET": "change-me"}
        )
        assert "jwt_secret_placeholder" in {f.code for f in report.findings}
        assert report.ok is False

    def test_missing_jwt_secret_is_critical(self):
        report = validate_production_config(env={"SECRET_KEY": "a-real-long-random-value"})
        assert "jwt_secret_missing" in {f.code for f in report.findings}

    def test_valid_config_passes(self):
        report = validate_production_config(env={
            "APP_ENV": "production",
            "SECRET_KEY": "a-real-long-random-value",
            "LIUHAO_JWT_SECRET": "another-real-long-random-value",
            "DATABASE_URL": "postgresql://liuhao:pw@postgres:5432/liuhao",
            "LOG_LEVEL": "INFO",
            "LIUHAO_KERNEL_POLICY_ENFORCE": "HIGH",
        })
        assert report.ok is True
        assert report.critical_count == 0
        assert report.warning_count == 0

    def test_production_env_warnings(self):
        report = validate_production_config(env={
            "APP_ENV": "production",
            "SECRET_KEY": "a-real-long-random-value",
            "LIUHAO_JWT_SECRET": "another-real-long-random-value",
            "DATABASE_URL": "sqlite:///liuhao.db",
            "LOG_LEVEL": "DEBUG",
        })
        codes = {f.code for f in report.findings}
        assert {"sqlite_in_production", "debug_logging_in_production",
                "policy_enforcement_off"} <= codes
        assert report.ok is True, "warning 不应让预检失败"
        assert report.warning_count == 3

    def test_sqlite_only_warns_in_production(self):
        report = validate_production_config(env={
            "APP_ENV": "staging",
            "SECRET_KEY": "a-real-long-random-value",
            "LIUHAO_JWT_SECRET": "another-real-long-random-value",
            "DATABASE_URL": "sqlite:///liuhao.db",
        })
        assert "sqlite_in_production" not in {f.code for f in report.findings}

    def test_strict_raises_on_critical(self):
        with pytest.raises(ProductionConfigError) as exc:
            validate_production_config(env={}, strict=True)
        assert "secret_key_missing" in str(exc.value)

    def test_strict_passes_when_clean(self):
        report = validate_production_config(env={
            "SECRET_KEY": "a-real-long-random-value",
            "LIUHAO_JWT_SECRET": "another-real-long-random-value",
        }, strict=True)
        assert report.ok is True

    def test_app_env_is_recorded(self):
        report = validate_production_config(env={"APP_ENV": "production"})
        assert report.app_env == "production"
        assert report.to_dict()["app_env"] == "production"

    def test_findings_never_contain_config_values(self):
        """诚实性：报告只能含键名/代码/级别，绝不能把密钥值带出去。"""
        sentinel = "S3CR3T-SENTINEL-9f3a1c"
        report = validate_production_config(env={
            "APP_ENV": "production",
            "SECRET_KEY": sentinel,
            "LIUHAO_JWT_SECRET": sentinel,
            "DATABASE_URL": "sqlite:///" + sentinel,
        })
        dumped = json.dumps(report.to_dict(), ensure_ascii=False)
        assert sentinel not in dumped, "预检报告泄露了配置值"

    def test_jwt_env_aliases_are_accepted(self):
        for alias in ("JWT_SECRET_KEY", "JWT_SECRET", "LIUHAO_JWT_PRIVATE_KEY"):
            report = validate_production_config(env={
                "SECRET_KEY": "a-real-long-random-value",
                alias: "another-real-long-random-value",
            })
            assert "jwt_secret_missing" not in {f.code for f in report.findings}, alias

    def test_only_critical_counts_toward_ok(self):
        report = validate_production_config(env={
            "SECRET_KEY": "a-real-long-random-value",
            "LIUHAO_JWT_SECRET": "another-real-long-random-value",
            "APP_ENV": "production",
            "LOG_LEVEL": "DEBUG",
        })
        assert report.warning_count >= 1
        assert report.ok is True
        assert report.critical_count == 0


# ============================================================
# 4) HTTP 端点
# ============================================================


@pytest.fixture
def client():
    from src.gateway.main import get_app

    with TestClient(get_app()) as test_client:
        yield test_client


class TestHttpEndpoints:
    def test_gateway_exposes_the_three_new_routes(self, client):
        """防孤儿：函数写好了不代表被接线。检查真实路由表。

        坑（FastAPI 0.141）：``include_router`` 是**惰性**的 —— 被包含的 router 在
        ``app.routes`` 里表现为 ``_IncludedRouter``（``path is None``），真实路径
        藏在 ``.original_router.routes`` 里。直接读 ``app.routes`` 的 ``.path``
        只能看到 /docs 等 4 条默认路由，会得出"路由没挂上"的错误结论。
        """
        paths = _collect_route_paths(client.app.routes)
        assert "/v1/metrics/prometheus" in paths, sorted(paths)
        assert "/v1/ready/subsystems" in paths
        assert "/v1/production/preflight" in paths
        # 既有端点仍在（新增是追加，不是替换）
        assert "/v1/health" in paths
        assert "/v1/metrics" in paths

    def test_prometheus_endpoint_serves_text_format(self, client):
        response = client.get("/v1/metrics/prometheus")
        assert response.status_code == 200, response.text
        assert "text/plain" in response.headers["content-type"]
        assert "version=0.0.4" in response.headers["content-type"]
        assert "# HELP http_requests_total" in response.text

    def test_prometheus_endpoint_can_be_disabled(self, client, monkeypatch):
        monkeypatch.setenv(production.PROMETHEUS_ENV, "0")
        assert client.get("/v1/metrics/prometheus").status_code == 404

    def test_subsystem_readiness_reports_all_kernels(self, client):
        response = client.get("/v1/ready/subsystems")
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["total"] == 14
        assert body["counts"]["healthy"] == 14
        assert body["status"] == "ready"

    def test_subsystem_readiness_503_when_a_kernel_is_unavailable(self, client, monkeypatch):
        from src.observability import production as mod

        monkeypatch.setattr(
            mod, "SUBSYSTEM_PROBES",
            (("ghost", "src.kernels.nope", "get_x"),),
        )
        response = client.get("/v1/ready/subsystems")
        assert response.status_code == 503
        assert response.json()["status"] == "not_ready"

    def test_preflight_requires_a_human_principal(self, client):
        response = client.get("/v1/production/preflight")
        assert response.status_code == 401, response.text

    def test_legacy_metrics_and_ready_are_untouched(self, client):
        """新增端点不得影响既有 health.py 三个端点。"""
        assert client.get("/v1/health").status_code == 200
        assert client.get("/v1/ready").status_code in (200, 503)
        assert client.get("/v1/metrics").status_code == 200
