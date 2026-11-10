"""
Health Check Endpoints for LiuHao AI OS Gateway

Provides:
- /health - 存活探针 (Kubernetes livenessProbe)
- /ready - 就绪探针 (Kubernetes readinessProbe)
- 依赖组件状态检查
- 详细的系统状态报告
"""

from fastapi import APIRouter, Request, status
from fastapi.responses import JSONResponse
from typing import Dict, Any
import asyncio
import logging
import threading
import time
import os
import uuid
from concurrent.futures import ThreadPoolExecutor

from ..security import get_api_key_manager, get_jwt_handler, get_rbac_manager, get_encryption_manager
from ..common.redaction import redact_log_text
from ..observability.boot_report import display_path
from ..gateway.rate_limiter import get_rate_limiter
from ..gateway.contract import API_CONTRACT_VERSION
from ..version import APP_VERSION
from .display import decorate_health_check, provider_label, status_label

logger = logging.getLogger(__name__)

# ============================================================================
# 探针隔离（D-1 / D-2）
# ============================================================================
# 这三个探针（/v1/health、/v1/ready、/v1/metrics）曾经把各自的同步 I/O
# （模型配置库读取、密钥库统计、审计统计、身份/围栏自检……）直接跑在**事件
# 循环线程**上。宿主 CPU 吃满时一次这样的读就能占住事件循环数秒，所有请求
# （包括 /v1/health）一起排队——表现就是"整站僵死、只有重启能恢复"。
#
# 现在：探针只做内存读或线程池里的有界工作。探针线程池与业务线程池
# （anyio 的 40 个 token，被慢模型调用占满）**完全分离**，业务再忙也堵不住
# 探针；真取不到数据就快速返回 503 + 中文原因，绝不把事件循环拖住。
_PROBE_TIMEOUT_SECONDS = 2.0
_PROBE_POOL = ThreadPoolExecutor(max_workers=4, thread_name_prefix="liuhao-probe")


class ProbeTimeout(RuntimeError):
    """探针在预算内拿不到结果：如实报忙，而不是无限等待。"""


async def _run_probe(fn, timeout: float = _PROBE_TIMEOUT_SECONDS):
    """在线程池里跑同步采集，带硬超时；超时立刻抛 :class:`ProbeTimeout`。

    ``shield`` 保证超时不会取消正在跑的采集（线程无法被取消），下一次调用
    仍可复用线程池；超时的那次结果被丢弃，调用方返回 503。
    """
    loop = asyncio.get_running_loop()
    future = loop.run_in_executor(_PROBE_POOL, fn)
    try:
        return await asyncio.wait_for(asyncio.shield(future), timeout)
    except asyncio.TimeoutError as exc:
        raise ProbeTimeout(f"probe exceeded {timeout}s") from exc


def warm_probe_pool(count: int = 4) -> int:
    """启动时把探针线程池的线程**全部先建好**。

    为什么：``ThreadPoolExecutor`` 的线程是"第一次 submit 时才创建"，而创建
    走的是 ``threading.Thread.start()`` —— 实测满载时这一行会在事件循环线程上
    无超时阻塞（D-1 的第二入口，见 soak 报告 §12.8）。``_run_probe`` 正是用
    ``loop.run_in_executor(_PROBE_POOL, ...)``，所以**第一个**探针请求就会在
    事件循环里建线程。预热之后，探针路径上不再有任何运行期线程创建：
    ``/v1/health`` 是最后一道可用性保障，不能把它的第一条请求赌在 Thread.start 上。

    返回**实际**预建的线程数；失败只记日志，不阻断启动。

    注意（2026-11-08 19:08 实测）：不能只连续 submit 一组空任务。空闲的 worker
    会被 ``_adjust_thread_count`` 的 ``_idle_semaphore`` 复用，线程数根本涨不到 N
    —— `rss8h4` 启动时这里只建出了 **2** 条（py-spy 里只剩 `liuhao-probe_0/1`），
    随后第三个并发探针又在事件循环里现场建线程，19:08 整站再次僵死 4 分多钟。
    这里改成"N 个任务同时卡在栅栏上"，逼迫执行器真的建出 N 条线程，再一起放行。
    """
    try:
        workers = max(1, int(count))
        workers = min(workers, int(getattr(_PROBE_POOL, "_max_workers", workers)))
        gate = threading.Barrier(workers)
        futures = [_PROBE_POOL.submit(_warm_barrier, gate) for _ in range(workers)]
        for future in futures:
            future.result(timeout=5.0)
        return len(getattr(_PROBE_POOL, "_threads", ())) or workers
    except Exception:  # pragma: no cover - 预热失败不影响服务
        logger.warning("探针线程池预热失败（不影响启动）", exc_info=True)
        return 0


def _warm_barrier(gate: threading.Barrier) -> None:
    """预热载体：等 N 条线程都到齐再一起放行。"""
    try:
        gate.wait(5.0)
    except threading.BrokenBarrierError:  # pragma: no cover - 线程被系统拒绝
        pass


def _redact_ready_details(value):
    """递归脱敏就绪检查：``technical_detail`` 在任何层级都不再出现。

    ``/v1/ready`` 是未鉴权可访问的探针，绝对路径、环境变量名、Traceback
    片段都不能下发（实测曾把 ``docker-compose sets /app/data/audit_store.db``
    这类部署指引直接暴露）。保留人话字段与状态字段，其余字符串统一过
    :func:`redact_log_text`。
    """
    if isinstance(value, str):
        return redact_log_text(value)
    if isinstance(value, dict):
        return {
            key: _redact_ready_details(item)
            for key, item in value.items()
            if key != "technical_detail"
        }
    if isinstance(value, list):
        return [_redact_ready_details(item) for item in value]
    return value


def _busy_response(kind: str, trace_id: str, detail: str = "") -> JSONResponse:
    """探针取数超时的快速失败响应：与业务 503 同一套**四字段**。

    ``code`` / ``message`` / ``correlation_id`` / ``retry_after`` + ``Retry-After``
    头。``status=busy`` 的语义保留在 ``code=PROBE_BUSY`` 与中文 ``message`` 里。
    ``detail``（``str(exc)``，可能带 "TypeName: msg" 与绝对路径）**不再下发**：
    探针未鉴权可访问，只留人话；技术原因脱敏后进日志，配合 ``correlation_id``
    排查。
    """
    correlation_id = trace_id if trace_id and trace_id != "unknown" else uuid.uuid4().hex[:16]
    if detail:
        logger.warning(
            "evt=probe.busy kind=%s correlation_id=%s cause=%s",
            kind, correlation_id, redact_log_text(str(detail))[:200],
        )
    payload = {
        "code": "PROBE_BUSY",
        "message": (
            f"{kind}暂时取不到数据：系统正在处理较慢的请求（探针繁忙），"
            "服务本身仍在运行，请 1 秒后重试。"
        ),
        "correlation_id": correlation_id,
        "retry_after": 1,
    }
    return JSONResponse(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        headers={"Retry-After": "1", "X-Correlation-ID": correlation_id},
        content=payload,
    )


# ---------------------------------------------------------------------------
# 存活探针的 provider 快照：绝不在请求路径上读模型配置库
# ---------------------------------------------------------------------------
# ``ai_provider_status()`` 会读模型配置库（SQLite）。放在事件循环上就是
# D-1 的僵死入口。这里用一个"最多 5 秒旧"的内存快照：命中最快（零 I/O），
# 过期时在探针线程池里刷新，超时就用旧值并标注 ``stale`` —— 存活探针的
# 语义是"进程还能响应"，不是"配置库还能读"。
_PROVIDER_SNAPSHOT_TTL = 5.0
_provider_snapshot: Dict[str, Any] = {}
_provider_snapshot_at: float = 0.0
_provider_snapshot_lock = threading.Lock()


def _provider_snapshot_for_probe() -> Dict[str, Any]:
    """返回 provider 状态快照；过期则在**当前线程**刷新（探针池线程）。"""
    global _provider_snapshot, _provider_snapshot_at
    with _provider_snapshot_lock:
        age = time.time() - _provider_snapshot_at
        if _provider_snapshot and age <= _PROVIDER_SNAPSHOT_TTL:
            return dict(_provider_snapshot)
    try:
        from ..ai.providers import ai_provider_status

        snapshot = ai_provider_status()
    except Exception:  # pragma: no cover - 配置读失败不该让探针失败
        snapshot = {"type": "unknown", "mode": "unknown", "simulated": False}
    with _provider_snapshot_lock:
        _provider_snapshot = dict(snapshot)
        _provider_snapshot_at = time.time()
        return dict(_provider_snapshot)


def _decorate_ai_provider(ai: Dict[str, Any]) -> Dict[str, Any]:
    """给 ``ai_provider`` 补人话字段；旧字段（type/mode/source）保持不变。"""
    if not isinstance(ai, dict):
        return ai
    ai = dict(ai)
    provider = provider_label(ai.get("type"))
    mode = str(ai.get("mode") or "").strip().lower()
    if mode == "invalid":
        state = "配置有误"
    elif mode == "mock":
        state = "模拟模式（不会产生真实模型调用）"
    else:
        state = "真实模型"
    ai["display_name"] = provider
    ai["display_status"] = f"{provider} · {state}"
    ai["status_label"] = state
    return ai


health_router = APIRouter(prefix="/v1", tags=["health"])

#: 本模块被导入的时刻，用作"/metrics 的运行时长"基准。
#: 这是一个真实可得的量（进程从加载网关到现在的秒数），
#: 而不是曾经常用的 ``time.time()`` —— 那是时间戳，冒充不了运行时长。
_PROCESS_START = time.time()


@health_router.get("/health", include_in_schema=False)
async def liveness_probe(request: Request) -> JSONResponse:
    """
    Liveness probe - Kubernetes 检查容器是否存活。

    返回 200 表示容器运行正常，可以被调度器调用。

    这是**纯内存路径**：不读配置库、不碰审计库、不开连接。provider 状态来自
    探针线程池维护的 5 秒快照；即使宿主 CPU 满载，本端点也只会花几十微秒。
    取不到快照时如实标注 ``stale``，但依然返回 200 —— "进程能响应"是它唯一的
    语义，不能因为下游慢就判死。
    """
    trace_id = request.headers.get("X-Trace-ID", "unknown")

    try:
        ai_provider = await _run_probe(_provider_snapshot_for_probe, timeout=0.5)
    except ProbeTimeout:
        with _provider_snapshot_lock:
            ai_provider = (
                dict(_provider_snapshot)
                if _provider_snapshot
                else {"type": "unknown", "mode": "unknown", "simulated": False}
            )
    except Exception:  # pragma: no cover - 探针失败不改变存活判定
        ai_provider = {"type": "unknown", "mode": "unknown", "simulated": False}

    with _provider_snapshot_lock:
        snapshot_age = (
            round(time.time() - _provider_snapshot_at, 1) if _provider_snapshot_at else None
        )
    ai_provider = _decorate_ai_provider(ai_provider)
    ai_provider["snapshot_age_seconds"] = snapshot_age

    return JSONResponse(
        content={
            "status": "ok",
            "service": "liuhao-gateway",
            "timestamp": time.time(),
            "trace_id": trace_id,
            "version": APP_VERSION,
            "api_contract": API_CONTRACT_VERSION,
            "ai_provider": ai_provider,
            "status_label": status_label("ok"),
            "user_message": "服务正在运行，可以接收请求",
        },
        status_code=status.HTTP_200_OK,
    )


#: 关键内核：任何一项在启动时未到 READY 都会让对应能力缺失且难以察觉。
#: 只影响对应能力，不逐个列出成多项检查——/v1/ready 用一个聚合项 ``kernels``。
#: 非关键内核失败只在同一项里作告警，不阻断就绪。
_CRITICAL_KERNELS: Dict[str, str] = {
    "audit": "审计留痕与证据核验",
    "identity": "账号身份与登录校验",
    "security": "敏感操作的安全裁决",
    "policy": "高风险动作的策略门禁",
    "execution": "工具/任务执行与记录",
}


def _kernels_readiness(report: Any, wired: bool = True) -> tuple:
    """把启动时的内核生命周期报告翻成一个聚合检查项（不逐个撑长列表）。

    数据来源只有 ``main.py`` 在 ``initialize_all()`` 之后写入
    ``app.state.kernel_lifecycle_report`` 的同一份报告（含启动快照）。这里不
    重新驱动内核、不创建实例，只读。

    为什么用「启动快照」而不是当前的 ``snapshot()``：多数内核是惰性单例，
    在 ``initialize_all()`` 之后才由请求路径首次创建（例如审计存储由断点器
    创建），它们的 ``lifecycle`` 天然停在 ``UNINITIALIZED``。若把「现在存在但
    未 READY」一律判失败，**每次开机都会 503**。所以只对「启动时就存在、被
    驱动过」的关键内核判 READY；启动后新建的惰性实例不算故障。
    """
    if not wired:
        # 直接调用（非网关请求对象）：不臆造内核状态。
        return (
            {
                "status": "healthy",
                "display_status": "内核状态未接入",
                "user_message": "内核生命周期报告未接入就绪检查。",
                "critical_not_ready": [],
                "optional_not_ready": [],
            },
            "",
        )
    if not isinstance(report, dict):
        # 网关注入了这条检查却没拿到报告：这是接线断了，不能静默放行。
        return (
            {
                "status": "unhealthy",
                "display_status": "内核状态未接入",
                "display_name": "内核生命周期",
                "user_message": (
                    "内核生命周期报告缺失：无法确认关键内核是否就绪，"
                    "请检查网关启动装配。"
                ),
                "critical_not_ready": [],
                "optional_not_ready": [],
            },
            "Kernels: lifecycle report is missing",
        )

    boot_snapshot = report.get("snapshot") or {}
    errors = report.get("errors") or []
    error_by_name = {
        str(item.get("name") or ""): str(item.get("error") or "")
        for item in errors
        if isinstance(item, dict)
    }

    critical_not_ready: list = []
    optional_not_ready: list = []
    checked: set = set()

    for name, item in boot_snapshot.items():
        if not isinstance(item, dict) or not item.get("has_instance"):
            continue
        checked.add(name)
        lifecycle = str(item.get("lifecycle") or "").strip().lower()
        if lifecycle == "ready" and name not in error_by_name:
            continue
        entry = {
            "name": name,
            "lifecycle": lifecycle or "unknown",
            "error": error_by_name.get(name, ""),
        }
        if name in _CRITICAL_KERNELS:
            entry["impact"] = _CRITICAL_KERNELS[name]
            critical_not_ready.append(entry)
        else:
            optional_not_ready.append(entry)

    # 启动报告里有错误、但快照没收录（实例在快照之后才出现）的关键内核也要报。
    for name, detail in error_by_name.items():
        if name in _CRITICAL_KERNELS and name not in checked:
            critical_not_ready.append(
                {
                    "name": name,
                    "lifecycle": "unknown",
                    "error": detail,
                    "impact": _CRITICAL_KERNELS[name],
                }
            )

    check: Dict[str, Any] = {
        "critical_not_ready": critical_not_ready,
        "optional_not_ready": optional_not_ready,
    }
    if critical_not_ready:
        names = "、".join(item["name"] for item in critical_not_ready)
        impacts = "；".join(
            f"{item['name']}（影响{item['impact']}）" for item in critical_not_ready
        )
        check.update(
            {
                "status": "unhealthy",
                "display_status": "关键内核未就绪",
                "display_name": "内核生命周期",
                "user_message": (
                    f"关键内核未就绪：{impacts}。"
                    f"相关能力可能缺失，其余服务仍可用；请在诊断页查看 {names}。"
                ),
            }
        )
        return check, f"Kernels: {names} did not reach READY"

    if optional_not_ready:
        names = "、".join(item["name"] for item in optional_not_ready)
        check.update(
            {
                "status": "degraded",
                "advisory": True,
                "blocks_readiness": False,
                "display_status": "非关键内核未就绪",
                "display_name": "内核生命周期",
                "user_message": (
                    f"关键内核均已就绪；非关键内核未就绪（{names}），"
                    "不影响核心功能，仅在相关能力上可能缺失。"
                ),
            }
        )
        return check, ""

    check.update(
        {
            "status": "healthy",
            "display_status": "内核运行正常",
            "display_name": "内核生命周期",
            "user_message": "启动时存在的关键内核均已到达 READY。",
        }
    )
    return check, ""


def _metrics_readiness() -> tuple:
    """指标链路的可观测性检查（告警级，不阻断就绪）。

    ``main._ensure_http_metrics()`` 是幂等导入：导入失败后 ``_metrics_import_
    attempted`` 仍为 True，但 ``_get_metric_collector`` / ``_RECORD_HTTP_REQUEST``
    永远是 None。此时 Prometheus 没有请求样本、错误率告警全盲——这是"监控失明"，
    不是"业务断服"，所以按告警处理（``blocks_readiness=False``）：总览显示、
    但不让 /v1/ready 503。

    预热开始前（``attempted=False``）是正常的启动窗口，不报问题。
    """
    try:
        from . import main as _gateway_main

        attempted = bool(getattr(_gateway_main, "_metrics_import_attempted", False))
        collector = getattr(_gateway_main, "_get_metric_collector", None)
        recorder = getattr(_gateway_main, "_RECORD_HTTP_REQUEST", None)
    except Exception:  # pragma: no cover - 装配异常不能反过来打断探针
        attempted, collector, recorder = False, None, None

    if not attempted:
        return (
            {
                "status": "healthy",
                "advisory": True,
                "blocks_readiness": False,
                "display_status": "指标链路预热中",
                "display_name": "监控链路",
                "user_message": "指标链路尚未完成预热，启动后会自动接入。",
            },
            "",
        )

    if collector is not None and recorder is not None:
        return (
            {
                "status": "healthy",
                "advisory": True,
                "blocks_readiness": False,
                "display_status": "指标链路正常",
                "display_name": "监控链路",
                "user_message": "请求指标已接入，错误率告警可正常评估。",
            },
            "",
        )

    missing = [
        label
        for label, value in (("指标采集", collector), ("Prometheus 导出", recorder))
        if value is None
    ]
    return (
        {
            "status": "degraded",
            "advisory": True,
            "blocks_readiness": False,
            "display_status": "监控链路不可用",
            "display_name": "监控链路",
            "user_message": (
                "指标链路不可用"
                f"（{'、'.join(missing)} 未加载）：Prometheus 没有请求样本，"
                "错误率告警会失明；业务请求不受影响。"
            ),
        },
        "Metrics: instrumentation unavailable -- error-rate alerting is blind",
    )


def _execution_dedupe_advisories() -> list:
    """读取执行内核的幂等去重告警（单独成函数，便于测试替换）。

    取不到时返回空列表：探针本身不能因为导入失败而误报。
    """
    try:
        from ..kernels.execution import dedupe_advisories

        return list(dedupe_advisories() or [])
    except Exception:  # pragma: no cover - 装配异常不能反过来打断探针
        return []


def _idempotency_readiness(advisories: Any) -> tuple:
    """幂等账本的可用性检查（告警级，不阻断就绪）。

    journal 未接入 / 账本写不进去时，硬副作用动作的重复投递无法被去重，
    同一动作（发消息、调外部接口、启动外部可见服务）可能产生第二次不可撤销
    后果。这件事不允许静默，但也不该让业务断服，所以按 advisory 展示
    （``blocks_readiness=False``）。
    """
    items = [item for item in (advisories or []) if isinstance(item, dict)]
    if not items:
        return (
            {
                "status": "healthy",
                "advisory": True,
                "blocks_readiness": False,
                "display_status": "幂等去重正常",
                "display_name": "幂等去重",
                "user_message": "幂等账本已接入：不可重入动作的重复投递会复用首次结果。",
            },
            "",
        )

    codes = sorted({str(item.get("code") or "") for item in items})
    # 严重度排序：硬副作用被拒绝 > 账本写失败 > 安全能力放行告警。
    priority_codes = (
        "hard_side_effect_without_ledger",
        "journal_write_failed",
        "ledger_missing_safe_replay",
    )
    lead = items[0]
    for code in priority_codes:
        matched = [item for item in items if item.get("code") == code]
        if matched:
            lead = matched[0]
            break

    user_message = " ".join(
        str(item.get("user_message") or "").strip()
        for item in items
        if str(item.get("user_message") or "").strip()
    )

    capabilities: set = set()
    blocked: set = set()
    for item in items:
        caps = item.get("capabilities")
        if isinstance(caps, list):
            capabilities.update(str(cap) for cap in caps if cap)
        if item.get("capability"):
            capabilities.add(str(item.get("capability")))
        if item.get("code") == "hard_side_effect_without_ledger" and isinstance(caps, list):
            blocked.update(str(cap) for cap in caps if cap)
    if blocked:
        user_message += (
            f"（本次被挡下的硬副作用能力：{'、'.join(sorted(blocked))}）"
        )
    if not user_message:
        user_message = "幂等去重存在告警，请检查 journal 装配与写入权限。"

    check = {
        "status": "degraded",
        "advisory": True,
        "blocks_readiness": False,
        "display_status": lead.get("display_status") or "幂等去重告警",
        "display_name": "幂等去重",
        "user_message": user_message,
        "codes": codes,
        "capabilities": sorted(capabilities),
        "occurrences": sum(int(item.get("occurrences") or 0) for item in items),
        "first_seen_at": lead.get("first_seen_at"),
        "last_seen_at": lead.get("last_seen_at"),
    }
    if "hard_side_effect_without_ledger" in codes:
        detail = (
            "Idempotency: hard side-effect capabilities were refused because no "
            "journal is wired -- attach an ExecutionJournal to run them"
        )
    elif "journal_write_failed" in codes:
        detail = (
            "Idempotency: ledger write failed -- non-reentrant actions were refused"
        )
    else:
        detail = (
            "Idempotency: ledger not wired -- read-only/reentrant actions allowed, "
            "hard side-effect actions refused"
        )
    return check, detail


def _scheduler_readiness(
    host: Any,
    boot_thread: Any,
    wired: bool,
    start_deadline: Any = None,
    start_timeout_s: Any = None,
) -> tuple:
    """把排程器托管状态翻成就绪检查项（不另建状态）。

    唯一事实来源是 ``main.py`` 启动时写入 ``app.state.local_scheduler_host`` 的
    ``HostedLocalScheduler``：``running`` 与 ``start_error`` 由
    ``src/tasks/scheduler_host.py`` 维护，这里只读。

    真实故障（``start_error`` 非空，例如旧库缺列 ``no such column``）必须
    ``unhealthy``：这是"提醒与定时任务不会触发"的静默空档，不能装饰成
    ``degraded`` 或直接放过。非故障状态（按配置不托管、另一进程持有锁）保持
    ``healthy`` 并用 ``display_status`` 说明，避免把配置选择报成故障。
    """
    if not wired:
        # 非网关进程内的直接调用（旧内部调用方/单测）：不臆造排程器状态。
        return None, ""

    start_error = str(getattr(host, "start_error", "") or "").strip()
    running = bool(getattr(host, "running", False))
    reason = str(getattr(host, "reason", "") or "").strip()
    check: Dict[str, Any] = {
        "running": running,
        "reason": reason,
        "display_name": "任务调度",
    }

    if start_error:
        message = (
            start_error
            if "提醒与定时任务不会触发" in start_error
            else f"提醒与定时任务不会触发：{start_error}"
        )
        check.update(
            {
                "status": "unhealthy",
                "start_error": start_error,
                "display_status": "排程器未启动",
                "user_message": message,
            }
        )
        return check, f"Scheduler: {start_error}"

    if running:
        check.update(
            {
                "status": "healthy",
                "display_status": "排程器运行中",
                "user_message": "排程器运行正常，提醒与定时任务会按时触发。",
            }
        )
        return check, ""

    if host is not None:
        # 按配置关闭 / 另一进程已持有实例锁：提醒仍由别处负责，不是故障。
        note = reason or "未在本进程运行"
        check.update(
            {
                "status": "healthy",
                "display_status": "排程器未在本进程运行",
                "user_message": f"排程器未在本进程运行（{note}）；这是配置/归属状态，不是故障。",
            }
        )
        return check, ""

    if bool(getattr(boot_thread, "is_alive", lambda: False)()):
        # 启动线程还活着：先给"启动中"的宽限。但线程卡在 build/导入而不抛异常时，
        # 它会永远活着——超过配置阈值必须转成故障，不能永远显示"启动中"。
        deadline = start_deadline
        overdue = False
        if isinstance(deadline, (int, float)) and deadline > 0:
            overdue = time.monotonic() > deadline
        if overdue:
            seconds = (
                f"{float(start_timeout_s):.0f}"
                if isinstance(start_timeout_s, (int, float))
                else "配置的"
            )
            detail = f"排程器启动超时（超过 {seconds} 秒），可能卡在初始化"
            check.update(
                {
                    "status": "unhealthy",
                    "start_error": detail,
                    "display_status": "排程器启动超时",
                    "user_message": (
                        f"提醒与定时任务不会触发：{detail}，"
                        "请到诊断页查看技术细节。"
                    ),
                }
            )
            return check, f"Scheduler: {detail} -- reminders will not fire"
        # 后台启动的宽限窗口内（main.py 有意延后启动）：明确说"启动中"，
        # 既不冒充运行正常，也不把每次开机的正常等待报成故障。
        check.update(
            {
                "status": "healthy",
                "starting": True,
                "display_status": "排程器启动中",
                "user_message": "排程器正在后台启动，提醒与定时任务稍后可用。",
            }
        )
        return check, ""

    detail = (
        "排程器启动线程已退出但未完成启动"
        if boot_thread is not None
        else "排程器启动状态未接入"
    )
    check.update(
        {
            "status": "unhealthy",
            "start_error": detail,
            "display_status": "排程器未启动",
            "user_message": f"提醒与定时任务不会触发：{detail}，请到诊断页查看技术细节。",
        }
    )
    return check, f"Scheduler: {detail} -- reminders will not fire"


def _readiness_payload_sync(
    trace_id: str,
    *,
    scheduler_host: Any = None,
    scheduler_boot_thread: Any = None,
    scheduler_wired: bool = False,
    scheduler_start_deadline: Any = None,
    scheduler_start_timeout_s: Any = None,
    kernel_report: Any = None,
    kernel_wired: bool = False,
) -> tuple:
    """
    Readiness probe - Kubernetes 检查服务是否就绪接受流量。

    检查所有核心依赖组件是否就绪：
    - API Key Manager
    - JWT Handler
    - RBAC Manager
    - Encryption Manager
    - Rate Limiter

    注意：本函数是**同步**的，只在探针线程池里执行，绝不在事件循环上跑。
    它要读密钥库、审计库、身份/围栏状态，任何一次读都可能被下游锁拖住；
    放在事件循环上就是 D-1 的"整站僵死"入口（见模块顶部说明）。
    """
    errors: list = []
    checks: Dict[str, Any] = {}

    # Check API Key Manager
    try:
        key_mgr = get_api_key_manager()
        key_stats = key_mgr.get_key_stats()
        checks["api_key_manager"] = {
            "status": "healthy",
            "total_keys": key_stats["total"],
            "active_keys": key_stats["by_status"].get("active", 0),
        }
    except Exception as e:
        errors.append(f"API Key Manager: {str(e)}")
        checks["api_key_manager"] = {"status": "unhealthy", "error": str(e)}

    # Check JWT Handler
    try:
        get_jwt_handler()
        # Quick validation test
        checks["jwt_handler"] = {"status": "healthy"}
    except Exception as e:
        errors.append(f"JWT Handler: {str(e)}")
        checks["jwt_handler"] = {"status": "unhealthy", "error": str(e)}

    # Check RBAC Manager
    try:
        rbac_mgr = get_rbac_manager()
        checks["rbac_manager"] = {"status": "healthy", "total_roles": len(rbac_mgr._roles)}
    except Exception as e:
        errors.append(f"RBAC Manager: {str(e)}")
        checks["rbac_manager"] = {"status": "unhealthy", "error": str(e)}

    # Check Encryption Manager
    try:
        get_encryption_manager()
        checks["encryption_manager"] = {"status": "healthy"}
    except Exception as e:
        errors.append(f"Encryption Manager: {str(e)}")
        checks["encryption_manager"] = {"status": "unhealthy", "error": str(e)}

    # 主密钥 ACL：启动期自检失败会登记到 boot_report。这里把它转成健康检查项，
    # 让"收紧后仍可被其他主体读取"在 /v1/ready 上看得见，而不是只留一行日志。
    # 权限问题需要人工修复，不该让整个产品断服，因此是告警级（不参与 503 判定）。
    try:
        from ..observability.boot_report import degraded_components

        acl_issues = [
            item
            for item in degraded_components()
            if item.get("subsystem") == "master_key_acl"
        ]
        if acl_issues:
            issue = acl_issues[0]
            checks["master_key_acl"] = {
                "status": "degraded",
                "blocks_readiness": False,
                "display_status": "主密钥文件权限过宽",
                "path": issue.get("path", ""),
                "user_message": issue.get("action")
                or "主密钥文件仍可被其他主体读取；按启动日志提示修复后重启。",
            }
        else:
            checks["master_key_acl"] = {
                "status": "healthy",
                "blocks_readiness": False,
            }
    except Exception as e:  # noqa: BLE001 - 健康面不能因读取自检结果而中断
        checks["master_key_acl"] = {
            "status": "degraded",
            "blocks_readiness": False,
            "display_status": "主密钥权限状态未知",
            "user_message": f"无法读取启动期 ACL 自检结果：{e}",
        }

    # Check Rate Limiter
    try:
        get_rate_limiter()
        checks["rate_limiter"] = {"status": "healthy"}
    except Exception as e:
        errors.append(f"Rate Limiter: {str(e)}")
        checks["rate_limiter"] = {"status": "unhealthy", "error": str(e)}

    # CRIT-1C / D17 (Layer 1) + C2 observability: surface audit write failures
    # AND verification coverage as observable signals.
    #
    # TWO DISTINCT QUESTIONS, TWO DISTINCT FIELDS. Conflating them is what
    # produced a self-contradictory payload: this block used to append "Audit
    # chain verification incomplete" to `errors` while leaving `status` at
    # "healthy", so the same object said the chain was broken AND fine, and the
    # console -- which counts `checks[*].status` -- reported 100% health behind
    # HTTP 200. `errors` is prose for humans; `checks` is what machines and KPIs
    # read. They must never disagree.
    #
    #   availability     -- can the store be reached and read at all?
    #   evidence_status  -- has the chain actually been re-derived?
    #
    # `evidence_status` has four honest values:
    #   "verified"    -- checkpoints cover the tail, rooted at genesis.
    #   "unverified"  -- there are events but NO checkpoint has ever been
    #                    written. This is the steady state of every deployment
    #                    today, because nothing in production calls
    #                    verify_incremental/verify_rolling (the /v1/audit/verify
    #                    endpoint runs verify_integrity(), which is read-only and
    #                    writes no checkpoint). It is an honest gap in evidence,
    #                    NOT a broken chain, and it must NOT gate readiness:
    #                    flipping /v1/ready to 503 here would make every
    #                    instance permanently un-ready and K8s would pull the
    #                    whole deployment out of rotation, with no mechanism in
    #                    place to ever advance the coverage back.
    #   "pending"     -- the only uncovered region is a CONTIGUOUS TAIL that
    #                    begins exactly at covered_through + 1. Checkpoints land
    #                    every few minutes (src/reliability/checkpointer.py,
    #                    interval=300s), so events appended since the last tick
    #                    are always uncovered. That is the normal "awaiting the
    #                    next checkpoint" lag, NOT an integrity regression --
    #                    and it must not 503 every freshly-logged-in instance
    #                    for ten minutes.
    #   "broken"      -- checkpoints EXIST but a HOLE is left in the middle of
    #                    the history (several uncovered ranges, or one that
    #                    starts before the frontier): a gap, a non-genesis start,
    #                    or a regression. That is a real integrity failure, so
    #                    it DOES degrade the gate.
    try:
        from ..kernels.audit import (
            audit_stats,
            audit_verification_coverage,
            get_audit_write_health,
        )

        astats = audit_stats()
        cov = audit_verification_coverage()
        # The store can OPEN and still fail EVERY write: policy decisions then
        # run fail-open and are "NOT RECORDED", while this block used to report
        # "available / healthy". That is the lie this field removes.
        write_health = get_audit_write_health()
        write_failing = bool(write_health.get("failing"))
        uncovered = cov.get("uncovered_events", 0) or 0
        rooted = cov.get("rooted_at_genesis", True)
        floor = cov.get("retention_floor_seq", 0) or 0
        # A retention policy deletes the oldest prefix ON PURPOSE. In that
        # case rooted_at_genesis is False by definition, but the retained
        # suffix being fully covered is the only claim the store can honestly
        # make. Treating that as "broken" would 503 every store the moment
        # retention first runs.
        rooted_suffix = cov.get("rooted_at_retention_floor", rooted)
        # `checkpoint_count` is what separates "never verified" from "coverage
        # regressed". Both look identical in uncovered_events alone.
        checkpoints = cov.get("checkpoint_count")
        incomplete = uncovered > 0 or (not rooted and not rooted_suffix)

        # Distinguish "waiting for the next checkpoint" from "the chain broke".
        # `uncovered_ranges` is the shape of the gap, and the shape is what
        # tells the two apart: a single range starting at covered_through + 1 is
        # just the un-checkpointed tail. Anything else (multiple ranges, or a
        # range that starts before the frontier) means history in the middle was
        # never covered -- that is a real break.
        ranges = cov.get("uncovered_ranges") or []
        frontier = cov.get("covered_through")
        tail_seq = cov.get("tail_seq")
        tail_only = False
        if len(ranges) == 1 and frontier is not None:
            try:
                tail_only = int(ranges[0][0]) == int(frontier) + 1
                if tail_only and tail_seq is not None:
                    # A checkpoint that RESUMES after a hole (e.g. an imported
                    # segment) also leaves one range starting at frontier + 1,
                    # but that range ends BEFORE the tail -- history in the
                    # middle was never covered, so it is not tail lag.
                    tail_only = int(ranges[0][1]) >= int(tail_seq)
            except (TypeError, ValueError, IndexError):
                tail_only = False

        if not incomplete:
            evidence_status = "verified"
        elif checkpoints is None:
            # Coverage reporter that does not publish checkpoint_count: we
            # cannot prove a regression, so do not invent one.
            evidence_status = "unverified"
        elif checkpoints > 0:
            evidence_status = "pending" if tail_only else "broken"
        else:
            # Never verified. Honest gap, not a proven break.
            evidence_status = "unverified"

        checks["audit_store"] = {
            # Availability: the store opened and answered. Reported separately
            # so a reachable-but-unverified store is never read as a verdict on
            # the evidence.
            "availability": "available",
            # Evidence integrity: the three honest values above.
            "evidence_status": evidence_status,
            # A failing write path outranks "coverage pending": the chain is
            # actively losing records, so the gate MUST say so.
            "status": (
                "unhealthy"
                if write_failing
                else ("degraded" if evidence_status == "broken" else "healthy")
            ),
            "total_events": astats.get("total_events", 0),
            "failures": astats.get("failures", 0),
            "write_failing": write_failing,
            "write_attempts": write_health.get("attempted", 0),
            "write_failures": write_health.get("failed", 0),
            "failure_streak": write_health.get("failure_streak", 0),
            "last_failure_kind": write_health.get("last_failure_kind"),
            "coverage_ratio": cov.get("coverage_ratio"),
            "uncovered_events": uncovered,
            "covered_through": cov.get("covered_through"),
            "newest_verified_at": cov.get("newest_verified_at"),
            "rooted_at_genesis": rooted,
            "rooted_at_retention_floor": rooted_suffix,
            "retention_floor_seq": floor,
        }
        if write_failing:
            # Fail-open is a deliberate availability trade-off, but it must be
            # LOUD: the user is told the audit trail stopped, in Chinese, at
            # the readiness edge -- not left reading a green "审计链正常".
            checks["audit_store"]["user_message"] = (
                "审计记录已中断，相关操作没有被记录。系统仍可继续使用，"
                "请尽快恢复审计库（AUDIT_DB_PATH 指向的数据库）。"
            )
            checks["audit_store"]["display_status"] = "审计记录已中断"
            checks["audit_store"]["fail_open"] = True
            errors.append(
                "Audit Write: "
                f"{write_health.get('failure_streak', 0)} consecutive write "
                f"failure(s) (last kind: {write_health.get('last_failure_kind') or 'unknown'}) "
                "-- policy decisions are NOT RECORDED (fail-open)."
            )
        if evidence_status == "pending":
            # Non-gating: this is the normal tail lag between checkpoints, so it
            # is phrased as a pending item -- NOT as "verification incomplete"
            # (that wording is reserved for a real regression below).
            errors.append(
                f"Audit chain: {uncovered} events awaiting the next checkpoint "
                "(normal tail lag)"
            )
        elif incomplete:
            # Kept verbatim in spirit: this line is for humans and is NOT
            # weakened. It now names which of the two cases it is.
            errors.append(
                "Audit chain verification incomplete: "
                f"{uncovered} events uncovered, "
                f"rooted_at_genesis={rooted} "
                f"(evidence_status={evidence_status}"
                + (
                    f", {checkpoints} checkpoint(s) exist so coverage regressed"
                    if checkpoints
                    else ", no checkpoint has ever been written"
                )
                + ")"
            )
    except Exception as e:
        errors.append(f"Audit Store: {str(e)}")
        checks["audit_store"] = {
            "availability": "unavailable",
            "evidence_status": "unknown",
            "status": "unhealthy",
            "error": str(e),
        }

    # G8/G9: the audit checkpointer computes an outcome for every tick
    # (ok / degraded / failed -- see CheckpointStats.record_status) but NOTHING
    # consumed it. A checkpointer failing on every tick and one succeeding on
    # every tick produced byte-identical readiness, alerts and dashboards.
    #
    # Which outcome gates readiness, and why it is `failed` and not `degraded`:
    #   * "failed"  = the tick raised, or a segment did not verify. That is a
    #     real integrity failure, so it DEGRADEDs the gate (HTTP 503).
    #   * "degraded" = coverage is incomplete. The audit_store block above
    #     deliberately does NOT take the service out of rotation for that, and
    #     a cold-start store with events but no checkpoint yet reports
    #     "degraded" on its very first tick -- gating on it would make
    #     /v1/ready permanently 503 on every fresh deployment. It is therefore
    #     surfaced here and in `errors`, but does not gate.
    #
    # A deployment with no verifier (or one that never ticked) is a
    # configuration state, not a fault, and stays healthy -- that is the
    # control case exercised by the tests.
    try:
        from ..reliability.checkpointer import get_checkpointer_status

        cp = get_checkpointer_status()
        cp_status = cp.get("last_status")
        checks["audit_checkpointer"] = {
            "status": "healthy",
            "available": bool(cp.get("available")),
            "last_status": cp_status,
            "status_counts": cp.get("status_counts") or {},
            "ticks": cp.get("ticks", 0),
        }
        if cp_status == "failed":
            detail = cp.get("last_error") or "no error text recorded"
            checks["audit_checkpointer"]["status"] = "degraded"
            checks["audit_checkpointer"]["error"] = (
                f"last checkpoint tick FAILED: {detail}"
            )
            errors.append(
                "Audit checkpointer: last tick FAILED "
                f"({detail}) -- audit verification is not running"
            )
        elif cp_status == "degraded":
            # Visible, non-gating: see the rationale above.
            checks["audit_checkpointer"]["coverage_incomplete"] = True
            errors.append(
                "Audit checkpointer: last tick degraded (verification coverage "
                "incomplete). Reported, not gating: a cold-start store reports "
                "this until its first checkpoint."
            )
        elif not cp.get("available"):
            # The verifier is not running in this process. This is NOT one
            # state -- it mirrors the boot decision in src/gateway/main.py,
            # where start_background_verifier() is called ONLY when AUDIT_DB_PATH
            # is set:
            #   * AUDIT_DB_PATH unset -> the verifier is intentionally not
            #     started. Configuration state, not a fault: keep healthy +
            #     informational note; never pull a deployment out of rotation
            #     for a deliberate choice.
            #   * AUDIT_DB_PATH set    -> the verifier WAS expected to start but
            #     did not (its start raised and was caught at main.py:339, e.g.
            #     the audit engine was unavailable at boot). This must NOT read
            #     as healthy -- it is precisely the "engine unavailable ->
            #     fake-green" trap. Degrade honestly so the instance is taken
            #     out of rotation instead of serving with an un-verified audit
            #     chain.
            if os.environ.get("AUDIT_DB_PATH"):
                checks["audit_checkpointer"]["status"] = "degraded"
                checks["audit_checkpointer"]["error"] = (
                    "AUDIT_DB_PATH is set (the audit verifier was expected to "
                    "start in this process) but no checkpointer is running -- "
                    "it failed to start (audit engine unavailable at boot?). "
                    "The audit chain is NOT being checkpointed."
                )
                errors.append(
                    "Audit checkpointer: AUDIT_DB_PATH is set but the verifier "
                    "is not running -- the audit chain is not being "
                    "checkpointed (audit engine unavailable at boot?)."
                )
            else:
                checks["audit_checkpointer"]["note"] = (
                    "AUDIT_DB_PATH is not set: this process does not start the "
                    "audit verifier (configuration state). Set AUDIT_DB_PATH to "
                    "a volume path to enable audit-chain checkpointing."
                )
    except Exception as e:  # pragma: no cover - defensive
        errors.append(f"Audit Checkpointer: {str(e)}")
        checks["audit_checkpointer"] = {"status": "unhealthy", "error": str(e)}

    # P0-3 (boss decision 2026-09-22): the human-identity registry integrity
    # posture must be OBSERVABLE at the readiness edge. When a registry is in
    # use (humans can hold sovereignty) but no integrity key is configured, the
    # posture is "degraded_unverified" -- the system must NOT present as fully
    # sovereign. Mark it "degraded" so the overall readiness degrades and a
    # deployment gate can see it, instead of a warning that disappears.
    try:
        from ..kernels.identity import get_identity_manager

        mgr = get_identity_manager()
        reg = mgr.describe_identity_namespaces().get("registry_integrity", {})
        state = reg.get("integrity_state", "unknown")
        if state == "degraded_unverified":
            checks["human_identity_registry"] = {
                "status": "degraded",
                "integrity_state": state,
                "detail": (
                    "a human registry is in use but LIUHAO_HUMAN_IDENTITIES_"
                    "INTEGRITY_KEY is unset -- rows are attacker-writable"
                ),
            }
            errors.append(
                "Human Identity Registry: degraded_unverified (no integrity key)"
            )
        else:
            checks["human_identity_registry"] = {
                "status": "healthy",
                "integrity_state": state,
            }
    except Exception as e:  # pragma: no cover - defensive
        errors.append(f"Human Identity Registry: {str(e)}")
        checks["human_identity_registry"] = {"status": "unhealthy", "error": str(e)}

    # UBX-005 observability: surface the human-sovereignty-critical executor
    # fence at the readiness edge. This is a VISIBILITY signal, not a gating
    # one -- a deployment that deliberately leaves the gate unarmed must not be
    # marked unhealthy, but operators must be able to SEE that the gate is off.
    # A genuine failure (exception) is reported as unhealthy so it is visible.
    #
    # G8/G9: "armed but the production fence failed to attach" used to be
    # INVISIBLE here -- /v1/ready stayed green while autonomous actions ran
    # UN-FENCED (the gateway lifespan logged one ERROR and kept booting). It is
    # now a real dependency check: when autonomous-action enforcement is ON
    # (``armed``) and the default fence did NOT attach, readiness is DEGRADED.
    # When the fence is legitimately not required (gate not armed), behaviour is
    # byte-for-byte what it was before: healthy + informational note.
    try:
        from ..kernels.execution.fence import (
            current_executor_fence,
            executor_fence_armed,
            get_default_fence_status,
            get_executor_fence,
            get_executor_fence_total,
        )

        fence = get_executor_fence()
        armed = executor_fence_armed()
        install = get_default_fence_status()
        installed = bool(install.get("attached"))
        proc_ctx = current_executor_fence()
        active_leases = fence.lease.count_active() if fence is not None else 0
        backend = type(fence.lease).__name__ if fence is not None else "none"
        heartbeat_failures = (
            getattr(fence.lease, "heartbeat_failures", 0) if fence is not None else 0
        )
        checks["executor_fence"] = {
            "status": "healthy",
            "armed": armed,
            "installed": installed,
            "backend": backend,
            "active_leases": active_leases,
            "epoch": proc_ctx.epoch if proc_ctx is not None else None,
            "denials_total": get_executor_fence_total(),
            "heartbeat_failures": heartbeat_failures,
        }
        if armed and not installed:
            # The one state that must NOT be green: autonomous-action
            # enforcement is on, but the fence that enforces it never attached.
            reason = install.get("error") or (
                "the default executor fence was never attached in this process"
            )
            checks["executor_fence"]["status"] = "degraded"
            checks["executor_fence"]["error"] = reason
            checks["executor_fence"]["failed_at"] = install.get("failed_at")
            checks["executor_fence"]["display_status"] = "执行隔离未生效"
            checks["executor_fence"]["user_message"] = (
                "执行隔离未生效：防护开关已打开，但拦截未挂载，自主操作可能未被拦截。"
                "请到诊断页查看技术细节。"
            )
            errors.append(
                "Executor Fence: armed (LIUHAO_EXECUTOR_FENCE=on) but the "
                f"default executor fence is NOT attached -- {reason}. "
                "Autonomous actions may be running UN-FENCED."
            )
        elif not armed:
            # Informational only -- the default policy is that the gate is
            # opt-in, so an unarmed deployment is a config state, not a fault.
            # But it must NOT read as "执行隔离正常": on every Windows install
            # the gate is never armed, so claiming normal here is a lie about
            # whether autonomous actions are fenced at all.
            checks["executor_fence"]["note"] = (
                "executor fence gate not armed (LIUHAO_EXECUTOR_FENCE != on): "
                "the default-deny autonomous-action gate is inactive"
            )
            checks["executor_fence"]["enforcement"] = "off"
            checks["executor_fence"]["display_status"] = "执行隔离未启用"
            checks["executor_fence"]["user_message"] = (
                "执行隔离未启用：当前部署不拦截自主执行"
                "（LIUHAO_EXECUTOR_FENCE 未开启）。这是配置状态，不是故障。"
            )
    except Exception as e:  # pragma: no cover - defensive
        errors.append(f"Executor Fence: {str(e)}")
        checks["executor_fence"] = {"status": "unhealthy", "error": str(e)}

    # Honest provider-mode surfacing (MOCK vs REAL). This is a *config* state,
    # not a fault, so it is reported as healthy; the mode itself ("mock"/"real")
    # tells operators whether executions are real or a dev default. Never
    # silently fakes a simulation as real.
    try:
        from ..ai.providers import ai_provider_status

        checks["ai_provider"] = {"status": "healthy", **ai_provider_status()}
    except Exception as e:  # pragma: no cover - defensive
        checks["ai_provider"] = {"status": "unhealthy", "error": str(e)}

    # 用量账本写入健康度：账本写失败此前只留一条 warning，调用方不检查，
    # 计费数据会悄悄缺行。当前仍在失败时必须让 readiness/诊断看得见。
    try:
        from .account_usage import usage_health

        usage = usage_health()
        ledger_ok = not usage.get("consecutive_failures")
        # /v1/health 与 /v1/ready 免鉴权：只给脱敏后的位置，不下发绝对路径。
        ledger = dict(usage)
        ledger["path"] = display_path(usage.get("path"))
        if ledger.get("last_error"):
            ledger["last_error"] = redact_log_text(str(ledger["last_error"]))
        checks["usage_ledger"] = {
            "status": "healthy" if ledger_ok else "degraded",
            **ledger,
        }
        if not ledger_ok:
            errors.append(
                "Usage Ledger: "
                f"{ledger.get('consecutive_failures')} consecutive write failure(s); "
                f"last error: {ledger.get('last_error')}"
            )
    except Exception as e:  # pragma: no cover - defensive
        checks["usage_ledger"] = {"status": "unhealthy", "error": str(e)}
        errors.append(f"Usage Ledger: {str(e)}")

    # C-2 arming self-check (security domain owns the DECISION, this is wiring).
    #
    # C-2 -- the blocking gate for HIGH/CRITICAL kernel actions -- is armed by a
    # single control point (LIUHAO_KERNEL_POLICY_ENFORCE; docker-compose.prod.yml
    # sets CRITICAL per D24), while production decoration points pass
    # enforce=False (C-4 single-control-point design, correct). The cost is that
    # nothing in-process asserted the gate is REALLY armed: blank the variable and
    # the defence silently degrades to record-only while every other signal stays
    # green (silent fail-open).
    #
    # We deliberately CONSUME src.security.c2_arming (the single source of truth)
    # rather than re-deriving the verdict here: two truths drift, and the stale one
    # silently yields a wrong security call. TestWiringContract in
    # tests/gateway/test_c2_arming_visibility.py enforces that.
    #
    # to_check() already carries a "status" field, so an arming mismatch
    # (UNARMED_PRODUCTION / INVALID_SPEC -> "degraded") flows into the existing
    # checks -> overall_status -> HTTP 503 path below with no extra state logic.
    # Unarmed NON-production stays healthy + a note (a deliberate config choice,
    # not a fault) so operators are not trained to ignore this check.
    try:
        from ..security.c2_arming import c2_arming_verdict_from_env

        verdict = c2_arming_verdict_from_env()
        checks["kernel_policy_enforcement"] = verdict.to_check()
        line = verdict.to_error_line()
        if line:
            errors.append(line)
    except Exception:  # pragma: no cover - defensive
        # Fail LOUD, not open: if the arming self-check cannot run we cannot
        # claim the sovereignty gate is armed.
        checks["kernel_policy_enforcement"] = {
            "status": "unhealthy",
            "error": "安全策略自检暂时无法运行（内部自检异常）。",
            "user_message": (
                "可以正常使用：登录、对话和模型调用都不受影响。"
                "这项自检本轮没有跑起来，无法确认内部防篡改是否已启用。"
                "需要动手：先重启一次程序；仍未恢复就到「设置 → 遇到问题 → "
                "导出诊断包」，把诊断包发给支持人员。"
            ),
        }
        errors.append("安全策略自检暂时无法运行（内部自检异常）。")

    # 排程器（提醒/定时任务）托管状态。历史上它起不来时这里完全看不见：
    # 旧库缺列导致整条排程器不启动，用户只会发现"提醒莫名不响了"而系统全程
    # 报健康。数据只读 main.py 已写入 app.state 的 HostedLocalScheduler。
    scheduler_check, scheduler_error = _scheduler_readiness(
        scheduler_host,
        scheduler_boot_thread,
        scheduler_wired,
        scheduler_start_deadline,
        scheduler_start_timeout_s,
    )
    if scheduler_check is not None:
        checks["scheduler"] = scheduler_check
        if scheduler_error:
            errors.append(scheduler_error)

    # 内核生命周期聚合项：关键内核启动时未到 READY 会阻断就绪；非关键内核
    # 未就绪只作告警。数据来自 main.py 写入 app.state 的同一份启动报告。
    kernels_check, kernels_error = _kernels_readiness(kernel_report, kernel_wired)
    checks["kernels"] = kernels_check
    if kernels_error:
        errors.append(kernels_error)

    # 指标链路：监控失明是告警，不是业务断服。blocks_readiness=False 使它
    # 不进阻断判定；总览仍单独展示（见 Overview.tsx）。
    metrics_check, metrics_error = _metrics_readiness()
    checks["metrics"] = metrics_check
    if metrics_error:
        errors.append(metrics_error)

    # 幂等去重（ACTION-01 后续）：账本未接入 / 不可写时，不可重入动作的重复
    # 投递无法去重。同样是告警级（不阻断就绪），但必须能在总览里看到。
    idempotency_check, idempotency_error = _idempotency_readiness(
        _execution_dedupe_advisories()
    )
    checks["idempotency_dedupe"] = idempotency_check
    if idempotency_error:
        errors.append(idempotency_error)

    # 逐项补"人话"字段；status 等机器字段原样保留（诊断页/旧调用方不受影响）。
    for name, check in checks.items():
        decorate_health_check(name, check)
    checks["ai_provider"] = _decorate_ai_provider(checks.get("ai_provider") or {})
    decorate_health_check("ai_provider", checks["ai_provider"])
    checks["ai_provider"]["user_message"] = (
        checks["ai_provider"].get("display_status")
        or checks["ai_provider"]["user_message"]
    )

    # Determine overall status.
    # ``blocks_readiness=False`` 的检查项（指标链路、非关键内核）是告警级：
    # 它们让总览看到问题，但不能让业务断服，所以不参与 503 判定。
    blocking_unhealthy = [
        k
        for k, v in checks.items()
        if v.get("status") != "healthy" and v.get("blocks_readiness", True)
    ]
    advisory_checks = [
        k
        for k, v in checks.items()
        if v.get("status") != "healthy" and not v.get("blocks_readiness", True)
    ]

    overall_status = "ready" if not blocking_unhealthy else "degraded"

    if overall_status == "ready" and advisory_checks:
        user_message = (
            f"系统就绪，阻断项全部通过；另有 {len(advisory_checks)} 项告警需要关注"
        )
    elif overall_status == "ready":
        user_message = "系统就绪，全部检查通过"
    else:
        user_message = f"系统未完全就绪：{len(blocking_unhealthy)} 项检查需要关注"

    response_data = {
        "status": overall_status,
        "status_label": status_label(overall_status),
        "user_message": user_message,
        "service": "liuhao-gateway",
        "timestamp": time.time(),
        "trace_id": trace_id,
        "checks": checks,
        "ai_provider": checks.get("ai_provider"),
    }
    if advisory_checks:
        response_data["advisories"] = advisory_checks

    # 探针未鉴权可访问：整包脱敏，并**在任何层级删掉 technical_detail**。
    # 部署细节（绝对路径 / 环境变量名 / Traceback）只留在诊断页。
    response_data["checks"] = _redact_ready_details(checks)
    response_data["ai_provider"] = response_data["checks"].get("ai_provider")

    # Add error details if degraded/unhealthy
    if errors:
        response_data["errors"] = [redact_log_text(str(err)) for err in errors]

    status_code = (
        status.HTTP_200_OK
        if overall_status == "ready"
        else status.HTTP_503_SERVICE_UNAVAILABLE
    )

    return response_data, status_code


@health_router.get("/ready", include_in_schema=False)
async def readiness_probe(request: Request) -> JSONResponse:
    """就绪探针：采集在探针线程池里跑，超时快速返回 503（中文原因）。"""
    trace_id = request.headers.get("X-Trace-ID", "unknown")

    # 排程器状态只从 main.py 启动时写入的 app.state 读取（同一份 start_error /
    # running），不在探针里另建一套状态。拿不到 app（直接调用/单测）时不臆造。
    scheduler_host = None
    scheduler_boot_thread = None
    scheduler_wired = False
    scheduler_start_deadline = None
    scheduler_start_timeout_s = None
    kernel_report = None
    kernel_wired = False
    try:
        state = request.app.state
        scheduler_wired = True
        kernel_wired = True
        scheduler_host = getattr(state, "local_scheduler_host", None)
        scheduler_boot_thread = getattr(state, "scheduler_host_thread", None)
        scheduler_start_deadline = getattr(state, "scheduler_start_deadline", None)
        scheduler_start_timeout_s = getattr(state, "scheduler_start_timeout_s", None)
        kernel_report = getattr(state, "kernel_lifecycle_report", None)
    except Exception:  # pragma: no cover - 非网关请求对象（直接调用）
        scheduler_wired = False

    try:
        response_data, status_code = await _run_probe(
            lambda: _readiness_payload_sync(
                trace_id,
                scheduler_host=scheduler_host,
                scheduler_boot_thread=scheduler_boot_thread,
                scheduler_wired=scheduler_wired,
                scheduler_start_deadline=scheduler_start_deadline,
                scheduler_start_timeout_s=scheduler_start_timeout_s,
                kernel_report=kernel_report,
                kernel_wired=kernel_wired,
            )
        )
    except ProbeTimeout as exc:
        return _busy_response("就绪检查", trace_id, str(exc))
    except Exception as exc:  # pragma: no cover - 采集自身出错也必须如实报
        return _busy_response("就绪检查", trace_id, f"{type(exc).__name__}: {exc}")
    return JSONResponse(content=response_data, status_code=status_code)


def _metrics_payload_sync(trace_id: str) -> Dict[str, Any]:
    """
    Metrics endpoint - 返回详细的系统指标。

    包含：
    - 密钥统计
    - 角色统计
    - 速率限制计数
    - 服务运行时长

    ⚠️ 诚实性说明
    -------------
    本端点长期不可达（main.py 曾经自建同名 health_router 并覆盖了这里的实现），
    因此它的两处字段错误一直没被发现：``expired_count`` / ``total_usage`` 在
    ``APIKeyManager.get_key_stats()`` 里**并不存在**（该函数只返回
    ``total`` / ``by_status`` / ``by_scope``），一调用就 KeyError 500；
    ``uptime_seconds`` 也曾直接填 ``time.time()``，是时间戳而不是运行时长。
    现在按真实可得的数据计算：过期的密钥数从 ``by_status`` 里数出来，
    运行时长用本模块加载时刻起算。

    与就绪探针同理：本函数只在探针线程池执行。``get_key_stats()`` 要读密钥库，
    放在事件循环上等于把指标端点变成第二个僵死入口（D-2）。
    """
    # Gather metrics from all components
    key_mgr = get_api_key_manager()
    key_stats = key_mgr.get_key_stats()

    rbac_mgr = get_rbac_manager()

    limiter = get_rate_limiter()

    by_status = key_stats.get("by_status") or {}
    metrics_data = {
        "service": "liuhao-gateway",
        "version": APP_VERSION,
        "timestamp": time.time(),
        "trace_id": trace_id,
        "uptime_seconds": round(time.time() - _PROCESS_START, 1),
        "components": {
            "api_key_manager": {
                "total_keys": key_stats.get("total", 0),
                "by_status": by_status,
                "by_scope": key_stats.get("by_scope") or {},
                # 从真实的状态分布里数出来，而不是假设存在一个预先算好的字段。
                "expired_count": int(by_status.get("expired", 0)),
            },
            "rbac_manager": {
                "total_roles": len(rbac_mgr._roles),
            },
            "rate_limiter": {
                "active_buckets": len(limiter._buckets) if hasattr(limiter, "_buckets") else 0,
            },
        },
    }
    # 身份校验降级的可观测口径（④）：两个进程内计数，重启归零。
    # 这里只读内存、不进请求路径，不会把 /v1/metrics 变成新的僵死入口。
    try:
        from .rate_limiter import identity_check_metrics

        metrics_data["components"]["identity_check"] = identity_check_metrics()
    except Exception:  # pragma: no cover - 指标缺失不能让端点失败
        pass

    return metrics_data


@health_router.get("/metrics", include_in_schema=False)
async def metrics_endpoint(request: Request) -> JSONResponse:
    """指标端点：采集在探针线程池里跑，超时快速返回 503（中文原因）。"""
    trace_id = request.headers.get("X-Trace-ID", "unknown")
    try:
        metrics_data = await _run_probe(lambda: _metrics_payload_sync(trace_id))
    except ProbeTimeout as exc:
        return _busy_response("指标采集", trace_id, str(exc))
    except Exception as exc:  # pragma: no cover - 采集自身出错也必须如实报
        return _busy_response("指标采集", trace_id, f"{type(exc).__name__}: {exc}")
    return JSONResponse(content=metrics_data, status_code=status.HTTP_200_OK)
