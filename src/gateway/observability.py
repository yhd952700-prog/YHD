"""生产化就绪端点（Phase 7a）—— Prometheus 暴露 / 子系统就绪 / 配置预检。

设计文档：``docs/PRODUCTION-READINESS-DESIGN.md``。逻辑全在
:mod:`src.observability.production`，本模块只做 HTTP 适配。

与既有 ``/v1/health``、``/v1/ready``、``/v1/metrics``（``health.py``）**并存**，
不替换、不修改 —— 那三个端点有精确的回归测试（``tests/test_gateway_health_wiring.py``），
本模块提供的是**追加能力**：

* ``/v1/metrics/prometheus`` —— 接上此前**无人调用**的 ``generate_metrics()``，
  输出 Prometheus 文本格式，使 docker-compose.observability.yml 的 Prometheus 能真正抓取。
* ``/v1/ready/subsystems`` —— 把 14 个内核纳入就绪检查（既有 ``/v1/ready`` 只看 5 个 auth 组件）。
* ``/v1/production/preflight`` —— 生产配置预检。**必须鉴权**：它会透露
  "SECRET_KEY 是占位符"这类对攻击者极具价值的信息。
"""

from __future__ import annotations

import logging
from typing import Any, Dict

from fastapi import APIRouter, Depends, HTTPException, Response, status
from fastapi.responses import JSONResponse

from ..observability.production import (
    PROMETHEUS_ENV,
    ProductionError,
    check_subsystems,
    prometheus_enabled,
    prometheus_response,
    validate_production_config,
)
from ..observability.alerts.store import AlertStore
from .policy import require_human_principal

logger = logging.getLogger("liuhao.gateway.observability")

router = APIRouter(prefix="/v1", tags=["observability"])


@router.get("/metrics/prometheus", include_in_schema=False)
async def prometheus_metrics() -> Response:
    """Prometheus 文本格式指标。

    ``LIUHAO_PROMETHEUS_METRICS=0`` 时端点关闭（404），便于运维显式关闭导出。
    导出不可用时**如实**返回 503 + 原因，绝不返回空串假装成功。
    """
    if not prometheus_enabled():
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Prometheus 导出已关闭（{PROMETHEUS_ENV}=0）",
        )

    try:
        body, content_type = prometheus_response()
    except ProductionError as exc:
        logger.warning("Prometheus 导出不可用: %s", exc)
        return JSONResponse(
            content={"error": "prometheus_unavailable", "detail": str(exc)},
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        )

    return Response(content=body, media_type=content_type)


@router.get("/ready/subsystems", include_in_schema=False)
async def subsystem_readiness() -> JSONResponse:
    """14 内核逐条就绪检查。

    ``healthy`` = 可导入 + 可实例化（+ 有 ``stats()`` 时能取到）；
    ``degraded`` = 可实例化但统计取不到（不阻断，避免误报全场 503）；
    ``unavailable`` = 导入或实例化失败 → HTTP 503。
    """
    report = check_subsystems()
    return JSONResponse(
        content=report.to_dict(),
        status_code=(
            status.HTTP_200_OK if report.ready else status.HTTP_503_SERVICE_UNAVAILABLE
        ),
    )


@router.get("/production/preflight", dependencies=[Depends(require_human_principal)])
async def production_preflight() -> JSONResponse:
    """生产配置预检（需人类主体鉴权）。

    报告**只含键名、代码与严重级别，不含任何配置值**。
    """
    report = validate_production_config()
    return JSONResponse(content=report.to_dict(), status_code=status.HTTP_200_OK)


@router.get("/alerts", dependencies=[Depends(require_human_principal)])
def list_alerts() -> Dict[str, Any]:
    """列出告警（只读）。返回 firing 告警与最近告警；库为空时 count=0，不是错误。

    这是把"只写不读"的告警存储变成操作员可见的第一步：执行失败告警（真实路径）
    此前只落日志/JSON，没有任何 API 或 UI 能读取。本端点如实返回当前库里的内容。
    """
    store = AlertStore()
    all_alerts = store.list_alerts()
    firing = store.list_firing()
    # 最近 50 条，按 fired_at 倒序。
    recent = sorted(all_alerts, key=lambda a: a.fired_at or 0, reverse=True)[:50]
    return {
        "count": len(all_alerts),
        "firing_count": len(firing),
        "alerts": [a.to_dict() for a in recent],
    }


@router.get("/alerts/rules", dependencies=[Depends(require_human_principal)])
def list_alert_rules() -> Dict[str, Any]:
    """列出已注册的告警规则及其真实 enabled 状态。

    诚实说明：install_production_rules() 注册的 6 条系统健康规则当前全部
    enabled=False（它们的指标发射器尚未接线，启用即装饰性失效）；执行失败告警
    不经规则引擎，直接经 EventBus 落库。本端点如实返回规则状态，不美化。
    """
    store = AlertStore()
    rules = store.list_rules()
    return {
        "count": len(rules),
        "rules": [
            {
                "id": r.id,
                "name": r.name,
                "enabled": r.enabled,
                "severity": r.severity.name if hasattr(r.severity, "name") else str(r.severity),
                "alert_type": r.alert_type.value if hasattr(r.alert_type, "value") else str(r.alert_type),
                "metric_name": r.metric_name,
                "description": r.description,
            }
            for r in rules
        ],
    }
