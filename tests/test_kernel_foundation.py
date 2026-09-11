"""十源层 kernel foundation（database / health / ready）回归测试（Round 57）。

覆盖 F-04 / F-05 / F-06 三个长期未修的缺陷：

* **F-06** ``init_engine()`` 把 ``naming_convention`` 传给 ``create_engine()``。
  naming_convention 是 MetaData 的参数，SQLAlchemy 2.0 下直接 TypeError ——
  即引擎初始化从来没成功过。因为没有调用方，一直没暴露。
* **F-04** ``check_health()`` 外层无条件标 ``healthy``，忽略各 check 自己算出的
  真实结果：``check_redis`` 内部返回 ``"status": "failed"`` 仍会被报成健康。
* **F-05** 检查项实现错误：用已废弃且未安装的 ``aioredis``；对同步的
  QdrantClient ``await close()``；对根本没有 ``close()`` 的 hvac 客户端
  ``await close()``；对 gRPC 端口 4317 发 HTTP 请求要求 200。

设计原则：测试不触达真实外部服务。database 用 sqlite+aiosqlite，health 的
聚合契约通过注入假 check 验证，缺依赖的场景用 ``sys.modules`` 打桩模拟。
"""

import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LHX_DIR = os.path.join(ROOT, "LiuHao-O")
if LHX_DIR not in sys.path:
    sys.path.insert(0, LHX_DIR)

from packages.kernel import database as db  # noqa: E402
from packages.kernel import health as health_mod  # noqa: E402
from packages.kernel import ready as ready_mod  # noqa: E402


@pytest.fixture
def restore_db_state():
    """database 模块用全局单例存引擎，测试必须还原，避免污染其他用例。"""
    saved = (db.engine, db.async_engine, db.SessionLocal, db.async_session_maker)
    yield
    (db.engine, db.async_engine, db.SessionLocal, db.async_session_maker) = saved


# --------------------------------------------------------------------------
# F-06: 引擎初始化
# --------------------------------------------------------------------------


def test_naming_convention_belongs_to_metadata_not_engine():
    """naming_convention 必须挂在 MetaData 上 —— 传给 engine 会被 SQLAlchemy 拒绝。"""
    assert db.Base.metadata.naming_convention == db.NAMING_CONVENTION

    # 锁住根因：这就是原实现 init_engine() 必崩的那一行
    with pytest.raises(TypeError):
        db.create_engine("sqlite:///:memory:", naming_convention=db.NAMING_CONVENTION)


@pytest.mark.parametrize(
    "dsn,expected",
    [
        ("postgresql://u:p@host/db", "postgresql+asyncpg://u:p@host/db"),
        ("sqlite:///:memory:", "sqlite+aiosqlite:///:memory:"),
        # 已指定 driver 的 DSN 不该被改写
        ("postgresql+psycopg://host/db", "postgresql+psycopg://host/db"),
        ("sqlite+aiosqlite:///:memory:", "sqlite+aiosqlite:///:memory:"),
    ],
)
def test_to_async_dsn(dsn, expected):
    assert db.to_async_dsn(dsn) == expected


async def test_init_engine_produces_working_async_session(restore_db_state):
    """回归测试：修复前这一行会抛 TypeError，引擎从未被真正建起来。"""
    from sqlalchemy import text

    db.init_engine("sqlite:///:memory:")
    try:
        assert db.get_engine() is not None
        assert db.get_async_engine() is not None

        session_factory = db.get_async_session_local()
        assert session_factory is not None

        session = session_factory()
        assert type(session).__name__ == "AsyncSession"

        async with session as s:
            result = await s.execute(text("SELECT 1"))
        assert result.scalar() == 1
    finally:
        if db.async_engine is not None:
            await db.async_engine.dispose()
        if db.engine is not None:
            db.engine.dispose()


# --------------------------------------------------------------------------
# F-04: 健康检查状态契约
# --------------------------------------------------------------------------


async def test_health_respects_check_status_instead_of_always_healthy():
    """核心回归：check 自己报 unhealthy 时，聚合器不许改写成 healthy。"""

    async def check_ok():
        return {"status": "healthy"}

    async def check_ping_failed():
        # 旧实现里这个返回值会被外层无条件标成 healthy
        return {"status": "unhealthy", "detail": "ping returned no pong"}

    result = await health_mod.check_health([check_ok, check_ping_failed])

    assert result["ok"]["status"] == "healthy"
    assert result["ping_failed"]["status"] == "unhealthy"
    assert result["overall"]["status"] == "unhealthy"


async def test_health_marks_exception_as_unhealthy():
    async def check_boom():
        raise RuntimeError("connection refused")

    result = await health_mod.check_health([check_boom])

    assert result["boom"]["status"] == "unhealthy"
    assert "RuntimeError" in result["boom"]["error"]


async def test_health_fails_closed_when_status_missing():
    """缺 status 字段时宁可误报故障，也不谎报健康。"""

    async def check_no_status():
        return {"database": "postgresql"}

    result = await health_mod.check_health([check_no_status])

    assert result["no_status"]["status"] == "unhealthy"
    assert result["overall"]["status"] == "unhealthy"


async def test_health_overall_healthy_only_when_all_healthy():
    async def check_a():
        return {"status": "healthy"}

    async def check_b():
        return {"status": "healthy"}

    result = await health_mod.check_health([check_a, check_b])
    assert result["overall"]["status"] == "healthy"


# --------------------------------------------------------------------------
# F-05: 各检查项的实现错误
# --------------------------------------------------------------------------


def test_redact_dsn_strips_credentials():
    """健康检查结果可能进日志或接口，不能泄露密码。"""
    redacted = health_mod.redact_dsn("redis://user:supersecret@host:6379/0")
    assert "supersecret" not in redacted
    assert "***" in redacted


async def test_check_database_unhealthy_when_engine_not_initialized(restore_db_state):
    """未初始化时明确上报，而不是抛 TypeError 或假装健康。"""
    db.engine = db.async_engine = db.SessionLocal = db.async_session_maker = None

    result = await health_mod.check_database()

    assert result["status"] == "unhealthy"
    assert "not initialized" in result["error"]


async def test_check_redis_reports_missing_dependency(monkeypatch):
    """aioredis 已废弃且未随项目安装：缺依赖要明确说，不能伪装成服务故障。"""
    monkeypatch.setitem(sys.modules, "redis", None)

    result = await health_mod.check_redis()

    assert result["status"] == "unhealthy"
    assert "not installed" in result["error"]


async def test_check_vector_db_unhealthy_when_not_configured(monkeypatch):
    """vector_dsn 为空时直接判定未配置，不发起连接。"""
    monkeypatch.setattr(
        health_mod, "_setting", lambda name, default: "" if name == "vector_dsn" else default
    )

    result = await health_mod.check_vector_db()

    assert result["status"] == "unhealthy"
    assert "not configured" in result["error"]


async def test_http_reachable_treats_http_error_status_as_reachable(monkeypatch):
    """OTLP/Prometheus 对 GET 返回 404/405 是正常的（端点只接受 POST）。"""
    import httpx

    class _FakeResponse:
        status_code = 404

    class _FakeClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def get(self, url):
            return _FakeResponse()

    monkeypatch.setattr(httpx, "AsyncClient", _FakeClient)

    result = await health_mod._http_reachable("http://localhost:4318/")

    assert result["status"] == "healthy"
    assert result["http_status"] == 404


async def test_http_unreachable_when_connection_fails(monkeypatch):
    import httpx

    class _FakeClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def get(self, url):
            raise httpx.ConnectError("connection refused")

    monkeypatch.setattr(httpx, "AsyncClient", _FakeClient)

    result = await health_mod._http_reachable("http://localhost:1/")

    assert result["status"] == "unhealthy"
    assert "ConnectError" in result["error"]


# --------------------------------------------------------------------------
# ready: 无外部服务时必须如实报 not_ready，而不是崩溃
# --------------------------------------------------------------------------


async def test_check_ready_not_ready_without_services(restore_db_state):
    db.engine = db.async_engine = db.SessionLocal = db.async_session_maker = None

    result = await ready_mod.check_ready()

    assert result["overall"]["status"] == "not_ready"
    assert result["database"]["status"] == "not_ready"
