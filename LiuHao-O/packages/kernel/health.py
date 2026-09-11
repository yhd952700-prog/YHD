"""
LIUHAO X - Health Package

Health checks for all system dependencies.
Following the LIUHAO X architectural policy: every release must pass health checks.

状态契约（F-04 修复）:
    每个 check 返回 dict，其中 ``status`` 必须是 ``healthy`` 或 ``unhealthy``。
    聚合器 **尊重** 该字段，而不是无条件标 healthy —— 原实现里即便
    ``check_redis`` 内部算出 ``"status": "failed"``，外层仍然报告 healthy，
    健康检查等于说谎。
    check 抛异常 -> unhealthy（附带异常类型）；返回体缺 status -> unhealthy
    （fail-closed，宁可误报故障也不谎报健康）。
"""

import asyncio
from typing import Any, Awaitable, Callable, Dict, List, Optional
from urllib.parse import urlsplit, urlunsplit

HEALTHY = "healthy"
UNHEALTHY = "unhealthy"

# 所有外部探测都必须有超时，否则健康检查本身会把调用方挂住。
TIMEOUT_SECONDS = 3.0

HealthStatus = Dict[str, Any]

# 返回 HealthStatus 的协程函数
HealthCheck = Callable[[], Awaitable[Any]]


def redact_dsn(dsn: str) -> str:
    """抹掉 DSN 里的密码 —— 健康检查结果可能进日志/接口，不能泄露凭据。"""
    try:
        parts = urlsplit(dsn)
    except ValueError:
        return dsn
    if not parts.password:
        return dsn
    host = parts.hostname or ""
    if parts.port:
        host = f"{host}:{parts.port}"
    netloc = f"{parts.username or ''}:***@{host}"
    return urlunsplit((parts.scheme, netloc, parts.path, "", ""))


def _setting(name: str, default: Any) -> Any:
    """读配置；配置未加载/缺项时退回默认值，不把「没配环境变量」报成「服务挂了」。"""
    try:
        from .config import get_settings

        value = getattr(get_settings(), name, None)
    except Exception:
        return default
    return value if value else default


async def _run_checks(checks: List[HealthCheck]) -> Dict[str, HealthStatus]:
    """按统一契约执行检查并聚合（与具体依赖无关，便于测试）。"""
    results: Dict[str, HealthStatus] = {}
    overall = HEALTHY

    for check in checks:
        name = getattr(check, "__name__", str(check)).replace("check_", "")
        try:
            result = await check()
        except Exception as exc:  # noqa: BLE001 - 健康检查要吞掉一切并如实上报
            result = {"status": UNHEALTHY, "error": f"{type(exc).__name__}: {exc}"}

        if not isinstance(result, dict):
            # 兼容返回 bool 的旧式 check
            result = {"status": HEALTHY if result else UNHEALTHY, "details": result}

        result = dict(result)
        # fail-closed：没有 status 就当作不健康
        status = result.get("status")
        if status not in (HEALTHY, UNHEALTHY):
            status = UNHEALTHY
            result.setdefault("error", f"check returned unusable status: {status!r}")
        result["status"] = status

        if status != HEALTHY:
            overall = UNHEALTHY
        results[name] = result

    results["overall"] = {"status": overall}
    return results


async def check_health(
    checks: Optional[List[HealthCheck]] = None,
) -> Dict[str, HealthStatus]:
    """Run all health checks and return status.

    ``checks`` 可注入，便于测试聚合逻辑而不触达真实外部服务。
    """
    if checks is None:
        checks = [
            check_database,
            check_redis,
            check_vector_db,
            check_vault,
            check_otel,
            check_prometheus,
        ]
    return await _run_checks(checks)


async def check_database() -> dict:
    """Check database connectivity via the async engine."""
    from sqlalchemy import text

    from .database import get_async_engine, get_async_session_local

    engine = get_async_engine()
    if engine is None:
        return {"status": UNHEALTHY, "error": "async engine not initialized (call init_engine)"}

    session_factory = get_async_session_local()
    if session_factory is None:
        return {"status": UNHEALTHY, "error": "async session factory not initialized"}

    async with session_factory() as session:
        await session.execute(text("SELECT 1"))

    return {"status": HEALTHY, "database": "sqlalchemy-async"}


async def check_redis() -> dict:
    """Check Redis connectivity."""
    redis_dsn = _setting("redis_dsn", "redis://localhost:6379")
    try:
        from redis.asyncio import Redis
    except ImportError:
        # aioredis 已废弃并合入 redis.asyncio；缺依赖要明确说，而不是伪装成服务故障
        return {
            "status": UNHEALTHY,
            "dsn": redact_dsn(redis_dsn),
            "error": "dependency 'redis' is not installed",
        }

    client = Redis.from_url(redis_dsn, socket_connect_timeout=TIMEOUT_SECONDS)
    try:
        pong = await client.ping()
    finally:
        await client.aclose()

    return {
        "status": HEALTHY if pong else UNHEALTHY,
        "dsn": redact_dsn(redis_dsn),
        "ping": bool(pong),
    }


async def check_vector_db() -> dict:
    """Check Qdrant vector database connectivity."""
    vector_dsn = _setting("vector_dsn", "")
    if not vector_dsn:
        return {"status": UNHEALTHY, "error": "vector_dsn is not configured"}

    from qdrant_client import AsyncQdrantClient

    # QdrantClient（同步版）的 close()/get_collections() 不是协程，
    # 原实现 `await client.close()` 必然 TypeError。这里用官方异步客户端。
    client = AsyncQdrantClient(url=vector_dsn, timeout=TIMEOUT_SECONDS)
    try:
        collections = await client.get_collections()
        count = len(collections.collections)
    finally:
        await client.close()

    return {"status": HEALTHY, "vector_db": "qdrant", "collections": count}


async def check_vault() -> dict:
    """Check Vault connectivity."""
    address = _setting("vault_address", "http://localhost:8200")
    import hvac

    # hvac 是同步客户端，且 hvac.Client 根本没有 close()（原实现会 AttributeError）。
    client = hvac.Client(url=address, timeout=TIMEOUT_SECONDS)
    health = await asyncio.to_thread(client.sys.read_health_status)

    return {"status": HEALTHY, "vault": address, "initialized": bool(health)}


async def check_otel() -> dict:
    """Check OpenTelemetry collector reachability (OTLP/HTTP)."""
    # 4317 是 OTLP gRPC 端口，对它发 HTTP GET 永远得不到 200 —— 原实现的判定方式
    # 从一开始就错了。默认端点已在 config 里改为 OTLP/HTTP 的 4318。
    endpoint = _setting("otel_endpoint", "http://localhost:4318")
    return await _http_reachable(endpoint)


async def check_prometheus() -> dict:
    """Check Prometheus reachability."""
    port = _setting("prometheus_port", 9090)
    url = f"http://localhost:{port}/metrics"
    return await _http_reachable(url)


async def _http_reachable(url: str) -> dict:
    """能收到任何 HTTP 响应即视为可达。

    OTLP/Prometheus 对 GET 返回 404/405 是正常的（端点只接受 POST），
    只有连接失败才算故障 —— 按 status_code == 200 判定会把在线服务误报为故障。
    """
    import httpx

    try:
        async with httpx.AsyncClient(timeout=TIMEOUT_SECONDS) as client:
            response = await client.get(url)
    except Exception as exc:  # noqa: BLE001 - 连通性探测：任何异常都是不可达
        return {"status": UNHEALTHY, "url": url, "error": f"{type(exc).__name__}: {exc}"}

    return {"status": HEALTHY, "url": url, "http_status": response.status_code}
