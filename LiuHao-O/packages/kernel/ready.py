"""
LIUHAO X - Ready Package

Ready checks for the system - whether all components are ready to accept requests.
Following the LIUHAO X policy: Release must pass Test, must pass Security Gate, must pass Evaluation.
"""

from typing import Dict, Any

ReadyStatus = Dict[str, Any]


async def check_ready() -> Dict[str, ReadyStatus]:
    """Run all ready checks and return overall readiness."""
    checks = [
        ready_database,
        ready_services,
        ready_observability,
        ready_security,
    ]
    
    results = {}
    all_ready = True
    
    for check in checks:
        name = check.__name__.replace("ready_", "")
        try:
            result = await check()
            results[name] = {"status": "ready" if result else "not_ready", "details": result}
            if not result:
                all_ready = False
        except Exception as e:
            results[name] = {"status": "error", "details": str(e)}
            all_ready = False
    
    results["overall"] = {
        "status": "ready" if all_ready else "not_ready",
        "details": "System " + ("ready" if all_ready else "not ready to accept requests")
    }
    
    return results


async def ready_database() -> bool:
    """Check if database is ready."""
    from .database import get_async_engine, get_async_session_local
    
    try:
        get_async_engine()  # 未初始化时为 None，随后的调用会抛异常并被下面捕获
        async with get_async_session_local()() as session:
            from sqlalchemy import text
            result = await session.execute(text("SELECT 1"))
            result.scalar()
        return True
    except Exception:
        return False


async def ready_services() -> bool:
    """Check if core services are ready."""
    # Check that we can import all packages and config validates
    try:
        from .config import get_settings

        from .database import get_async_engine, get_engine

        # 强制校验配置（fail-fast），并确认 database/health 模块可导入
        get_settings()
        assert get_engine is not None and get_async_engine is not None
        return True
    except Exception:
        return False


async def ready_observability() -> bool:
    """Check if observability is ready."""
    # Check that OTel, Prometheus config is in place
    try:
        from .config import get_settings
        s = get_settings()
        return bool(s.otel_endpoint) and s.prometheus_port > 0
    except Exception:
        return False


async def ready_security() -> bool:
    """Check if security infrastructure is ready."""
    try:
        from .config import get_settings
        s = get_settings()
        return bool(s.secret_key)
    except Exception:
        return False