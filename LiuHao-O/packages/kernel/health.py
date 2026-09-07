"""
LIUHAO X - Health Package

Health checks for all system dependencies.
Following the LIUHAO X architectural policy: every release must pass health checks.
"""

from typing import Dict, Any, List, Optional
import asyncio

HealthStatus = Dict[str, Any]


async def check_health() -> Dict[str, HealthStatus]:
    """Run all health checks and return status."""
    checks = [
        check_database,
        check_redis,
        check_vector_db,
        check_vault,
        check_otel,
        check_prometheus,
    ]
    
    results = {}
    for check in checks:
        name = check.__name__.replace("check_", "")
        try:
            result = await check()
            results[name] = {"status": "healthy", "details": result}
        except Exception as e:
            results[name] = {"status": "unhealthy", "details": str(e)}
    
    return results


async def check_database() -> dict:
    """Check PostgreSQL database connectivity."""
    from .database import get_async_engine, get_async_session_local
    
    engine = get_async_engine()
    async with get_async_session_local()() as session:
        from sqlalchemy import text
        result = await session.execute(text("SELECT 1"))
        result.scalar()
    
    return {"database": "postgresql", "status": "connected"}


async def check_redis() -> dict:
    """Check Redis connectivity."""
    import aioredis
    
    redis = await aioredis.from_url("redis://localhost:6379")
    pong = await redis.ping()
    await redis.aclose()
    
    return {"redis": "connected", "status": "pong" if pong else "failed"}


async def check_vector_db() -> dict:
    """Check Qdrant vector database connectivity."""
    from qdrant_client import QdrantClient
    
    client = QdrantClient(host="localhost", port=6333)
    collections = client.get_collections()
    await client.close()
    
    return {"vector_db": "qdrant", "status": "connected", "collections": len(collections.collections)}


async def check_vault() -> dict:
    """Check Vault connectivity."""
    import hvac
    
    client = hvac.Client(host="localhost", port=8200)
    health = client.sys.read_health_status()
    await client.close()
    
    return {"vault": "connected" if health else "failed"}


async def check_otel() -> dict:
    """Check OpenTelemetry collector connectivity."""
    import httpx
    
    async with httpx.AsyncClient() as client:
        resp = await client.get("http://localhost:4317/v1/metrics")
        return {"otel": "connected" if resp.status_code == 200 else "failed"}


async def check_prometheus() -> dict:
    """Check Prometheus connectivity."""
    import httpx
    
    async with httpx.AsyncClient() as client:
        resp = await client.get("http://localhost:9090/metrics")
        return {"prometheus": "connected" if resp.status_code == 200 else "failed"}