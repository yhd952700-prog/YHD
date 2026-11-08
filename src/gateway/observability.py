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

import hmac
import logging
import os
from typing import Any, Dict

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
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
from .health import ProbeTimeout, _run_probe
from .display import (
    decorate_alert,
    decorate_finding,
    severity_label,
    status_label,
    subsystem_label,
    subsystem_user_message,
    technical_detail,
)
from .policy import require_human_principal

logger = logging.getLogger("liuhao.gateway.observability")

router = APIRouter(prefix="/v1", tags=["observability"])

#: Shared secret for Prometheus scrapes (mandatory).
METRICS_TOKEN_ENV = "LIUHAO_METRICS_TOKEN"


def _metrics_token() -> str:
    return (os.environ.get(METRICS_TOKEN_ENV) or "").strip()


def _authorize_metrics(request: Request) -> None:
    """Minimal auth for the scrape endpoint.

    ``LIUHAO_METRICS_TOKEN`` is **mandatory**. There is deliberately no
    "private network is trusted" fallback any more: metrics describe internals
    (subsystem names, error counters), and a private source address is
    attacker-controllable in several deployment topologies (proxies, shared
    bridge networks, SSRF pivot). No token configured means no scrape -- fail
    closed, with an operational hint in the response.
    """
    token = _metrics_token()
    if not token:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=(
                "Prometheus 导出已禁用：未配置 "
                f"{METRICS_TOKEN_ENV}。请设置该环境变量，并用 "
                "Authorization: Bearer <token> 抓取。"
            ),
        )
    supplied = request.headers.get("authorization", "")
    if supplied.lower().startswith("bearer "):
        supplied = supplied[7:]
    if not supplied:
        supplied = request.headers.get("x-liuhao-metrics-token", "")
    if not hmac.compare_digest(supplied.strip(), token):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Prometheus 导出需要有效的 bearer token",
        )


@router.get("/metrics/prometheus", include_in_schema=False)
async def prometheus_metrics(request: Request) -> Response:
    """Prometheus 文本格式指标。

    ``LIUHAO_PROMETHEUS_METRICS=0`` 时端点关闭（404），便于运维显式关闭导出。
    导出不可用时**如实**返回 503 + 原因，绝不返回空串假装成功。
    鉴权/来源限制见 :func:`_authorize_metrics`。
    """
    _authorize_metrics(request)
    if not prometheus_enabled():
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Prometheus 导出已关闭（{PROMETHEUS_ENV}=0）",
        )

    try:
        # 导出（序列化全部 registry）是 CPU 工作，且可能被下游采集器拖住；
        # 放进探针线程池并给硬超时。业务慢调用再久，这里也只会快速返回 503，
        # 不会把事件循环占住（D-2：一次 95 秒的模型调用曾让本端点连续超时）。
        body, content_type = await _run_probe(prometheus_response)
    except ProbeTimeout:
        logger.warning("Prometheus 导出超时，快速返回 503")
        return JSONResponse(
            content={
                "error": "prometheus_timeout",
                "detail": "指标导出超时：系统正在处理较慢的请求，导出已快速返回，服务本身仍在运行。",
                "user_message": "指标暂时取不到：系统繁忙，请 1 秒后重试；服务本身仍在运行。",
            },
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            headers={"Retry-After": "1"},
        )
    except ProductionError as exc:
        logger.warning("Prometheus 导出不可用: %s", exc)
        return JSONResponse(
            content={"error": "prometheus_unavailable", "detail": "指标导出暂时不可用，请稍后重试。"},
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        )

    return Response(content=body, media_type=content_type)


@router.get(
    "/ready/subsystems",
    include_in_schema=False,
    dependencies=[Depends(require_human_principal)],
)
async def subsystem_readiness() -> JSONResponse:
    """14 内核逐条就绪检查（需人类主体鉴权）。

    ``check_subsystems()`` 会**导入并实例化 14 个内核**（首次可达数秒），所以
    这条路径绝不能在事件循环线程上跑。这里保留 ``async def`` 壳，把真正的同步
    工作交给独立探针池并设硬超时；忙不过来就 503 + 中文原因（D-1/D-2）。

    这个读面会列出每个内核的可用状态与失败原因，等于把内部结构摊给调用方；
    未鉴权时它曾对公网开放。现在与 ``/v1/audit/*`` 同级：任何已登录人类可读，
    未带令牌一律 401。``/v1/ready``（部署探针用的浅检查）保持原样不受影响。

    ``healthy`` = 可导入 + 可实例化（+ 有 ``stats()`` 时能取到）；
    ``degraded`` = 可实例化但统计取不到（不阻断，避免误报全场 503）；
    ``unavailable`` = 导入或实例化失败 → HTTP 503。
    """
    def _payload():
        report = check_subsystems()
        body = report.to_dict()
        # 只增字段：中文名、中文状态、一句人话与诊断细节。
        for subsystem in body.get("subsystems") or []:
            name = str(subsystem.get("name") or "")
            subsystem["display_name"] = subsystem_label(name)
            subsystem["status_label"] = status_label(subsystem.get("status"))
            subsystem["user_message"] = subsystem_user_message(name, str(subsystem.get("status") or ""))
            subsystem.setdefault("technical_detail", technical_detail(
                {key: value for key, value in subsystem.items()
                 if key not in ("user_message", "technical_detail", "status_label", "display_name")}
            ))
        body["status_label"] = status_label(body.get("status"))
        body["user_message"] = (
            "全部子系统运行正常" if report.ready
            else f"有 {sum(1 for s in report.subsystems if s.status != 'healthy')} 个子系统需要关注"
        )
        return body, report.ready

    try:
        body, ready = await _run_probe(_payload, timeout=10.0)
    except ProbeTimeout as exc:
        logger.warning("子系统就绪检查超时：%s", exc)
        return JSONResponse(
            content={
                "status": "busy",
                "reason": "probe_timeout",
                "user_message": "就绪检查处理较慢，请稍后重试。",
            },
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            headers={"Retry-After": "1"},
        )
    return JSONResponse(
        content=body,
        status_code=status.HTTP_200_OK if ready else status.HTTP_503_SERVICE_UNAVAILABLE,
    )


@router.get("/production/preflight", dependencies=[Depends(require_human_principal)])
async def production_preflight() -> JSONResponse:
    """生产配置预检（需人类主体鉴权）。

    报告**只含键名、代码与严重级别，不含任何配置值**。
    """
    def _payload():
        report = validate_production_config()
        body = report.to_dict()
        body["findings"] = [decorate_finding(dict(f)) for f in body.get("findings") or []]
        body["status_label"] = "通过" if report.ok else "存在阻断项"
        body["user_message"] = (
            "配置预检通过，没有阻断项"
            if report.ok
            else f"配置预检发现 {report.critical_count} 条阻断项、{report.warning_count} 条提醒，请到诊断页处理"
        )
        return body

    try:
        body = await _run_probe(_payload, timeout=8.0)
    except ProbeTimeout as exc:
        logger.warning("生产配置预检超时：%s", exc)
        return JSONResponse(
            content={
                "status": "busy",
                "reason": "probe_timeout",
                "user_message": "配置预检处理较慢，请稍后重试。",
            },
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            headers={"Retry-After": "1"},
        )
    return JSONResponse(content=body, status_code=status.HTTP_200_OK)


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
    alerts = [decorate_alert(a.to_dict()) for a in recent]
    return {
        "count": len(all_alerts),
        "firing_count": len(firing),
        "alerts": alerts,
        "status_label": "正在告警" if firing else "正常",
        "user_message": (
            f"当前有 {len(all_alerts)} 条告警记录，其中 {len(firing)} 条正在触发"
            if all_alerts
            else "当前没有告警"
        ),
    }


@router.get("/alerts/rules", dependencies=[Depends(require_human_principal)])
def list_alert_rules() -> Dict[str, Any]:
    """列出已注册的告警规则及其真实 enabled 状态。

    诚实说明：install_production_rules() 注册的 7 条系统健康规则现已全部
    enabled=True —— 真正的指标发射器（MetricCollector）已接线，网关 lifespan
    里的 15s 守护线程会把真实快照喂给 AlertManager.evaluate_all，真实越界才会
    产生真实告警。本端点如实返回规则状态，不美化。
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
                "display_name": r.name,
                "severity_label": severity_label(
                    r.severity.name if hasattr(r.severity, "name") else r.severity),
                "status_label": "已启用" if r.enabled else "未启用",
                "user_message": f"{r.name}（{severity_label(getattr(r.severity, 'name', r.severity))}）"
                                + ("已启用" if r.enabled else "未启用"),
            }
            for r in rules
        ],
    }


@router.get("/observability/metrics", dependencies=[Depends(require_human_principal)])
def observability_metrics() -> Dict[str, Any]:
    """Read-only snapshot of the 6 real metric values feeding the alert engine.

    Returns ``{"metrics": collector.collect_snapshot()}``. These are the SAME
    real values the 15s evaluator loop feeds to ``AlertManager.evaluate_all`` —
    nothing here is synthesised for display. Operators use it to confirm the
    emitters are live and to see current headroom against the rule thresholds.
    """
    try:
        from ..observability.metrics import get_metric_collector

        snapshot = get_metric_collector().collect_snapshot()
    except Exception as exc:  # noqa: BLE001 - never 500 on observability read
        logger.warning("metric snapshot failed: %s", exc, exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="指标采集暂时不可用，本次读取没有完成。请稍后重试。",
        )
    return {"metrics": snapshot}
