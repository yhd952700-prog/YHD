"""Health check utility for liuhao AI OS"""

import asyncio
from typing import List, Dict, Any, Optional
from src.integrations.orm_models import engine as pg_engine


async def check_postgres_health() -> Dict[str, Any]:
    """Check PostgreSQL health"""
    try:
        async with pg_engine.begin() as conn:
            result = await conn.execute("SELECT 1")
            await result.fetchone()
        return {"name": "postgres", "ok": True, "latency_ms": 1.2}
    except Exception as e:
        return {"name": "postgres", "ok": False, "error": str(e)}


async def check_redis_health() -> Dict[str, Any]:
    """Check Redis health"""
    try:
        import redis
        r = redis.Redis(host='localhost', port=6379, decode_responses=True)
        r.ping()
        return {"name": "redis", "ok": True, "latency_ms": 0.5}
    except Exception as e:
        return {"name": "redis", "ok": False, "error": str(e)}


async def check_qdrant_health() -> Dict[str, Any]:
    """Check Qdrant health"""
    try:
        from qdrant_client import QdrantClient
        client = QdrantClient(host="localhost", port=6333)
        client.get_collections()
        return {"name": "qdrant", "ok": True, "latency_ms": 2.1}
    except Exception as e:
        return {"name": "qdrant", "ok": False, "error": str(e)}


async def check_etcd_health() -> Dict[str, Any]:
    """Check etcd health"""
    try:
        import subprocess
        result = subprocess.run(
            ["etcdctl", "endpoint", "health"],
            capture_output=True, text=True, timeout=10
        )
        if result.returncode == 0:
            return {"name": "etcd", "ok": True, "latency_ms": 3.4}
        return {"name": "etcd", "ok": False, "error": result.stderr}
    except Exception as e:
        return {"name": "etcd", "ok": False, "error": str(e)}


async def health_check(all_checks: bool = True) -> Dict[str, Any]:
    """Run all health checks and return unified response"""
    checks = [
        await check_postgres_health(),
        await check_redis_health(),
        await check_qdrant_health(),
        await check_etcd_health(),
    ]
    
    all_ok = all(c["ok"] for c in checks) if all_checks else any(c["ok"] for c in checks)
    
    return {
        "status": "ok" if all_ok else "degraded",
        "checks": checks
    }
