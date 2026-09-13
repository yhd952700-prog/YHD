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
    "rate_limiter",
}


@pytest.fixture
def client():
    with TestClient(get_app()) as test_client:
        yield test_client


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
        unhealthy = [
            name
            for name, detail in body["checks"].items()
            if detail.get("status") != "healthy"
        ]
        if unhealthy:
            # 不健康时必须如实降级：503 + errors，而不是回答 "ready"。
            assert response.status_code == 503
            assert body["status"] == "degraded"
            assert body.get("errors")
        else:
            assert response.status_code == 200
            assert body["status"] == "ready"

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
