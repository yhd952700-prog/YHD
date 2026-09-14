"""生产化就绪层（Phase 7a）—— 指标暴露 / 子系统探针 / 配置预检。

设计文档：``docs/PRODUCTION-READINESS-DESIGN.md``。

本模块是**纯逻辑**：不依赖 FastAPI、不发起网络、不写库、不在 import 期拉起
任何内核。HTTP 层在 :mod:`src.gateway.observability`。

三条诚实性原则：

1. **不静默降级** —— Prometheus 导出不可用时抛 :class:`ProductionError`，
   由调用方如实返回 503，而不是返回一段空文本假装成功。
2. **探针永不抛** —— :func:`check_subsystems` 把每个内核的失败就地收敛成
   ``unavailable`` 状态；一个内核挂掉不该让整份就绪报告消失。
3. **预检不泄密** —— :func:`validate_production_config` 只返回**键名 + 代码 +
   严重级别**，永不返回配置值本身。"SECRET_KEY 是占位符"已足够作为攻击线索，
   因此其 HTTP 端点必须鉴权（见 router）。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Mapping, Optional, Tuple

logger = logging.getLogger("liuhao.observability.production")

#: Prometheus 文本格式的 Content-Type（0.0.4 是 prometheus_client 现行版本）。
PROMETHEUS_CONTENT_TYPE = "text/plain; version=0.0.4; charset=utf-8"

#: 端点开关：``LIUHAO_PROMETHEUS_METRICS=0/false/off/no`` 时关闭暴露。
PROMETHEUS_ENV = "LIUHAO_PROMETHEUS_METRICS"
_FALSY = frozenset({"0", "false", "off", "no", "disable", "disabled"})


# ============================================================
# 异常
# ============================================================


class ProductionError(RuntimeError):
    """生产能力无法诚实提供时抛出（调用方应转成 503，而不是假装成功）。"""


class ProductionConfigError(ProductionError):
    """严格模式下，生产配置预检发现 critical 级问题时抛出。"""


# ============================================================
# 1) Prometheus 指标暴露 —— 接线既有孤儿 generate_metrics()
# ============================================================


def prometheus_enabled(env: Optional[Mapping[str, str]] = None) -> bool:
    """端点是否启用。默认**启用**（导出是只读、可安全被抓取的操作）。"""
    source = env if env is not None else _os_environ()
    raw = (source.get(PROMETHEUS_ENV) or "").strip().lower()
    return raw not in _FALSY


def prometheus_available() -> Tuple[bool, str]:
    """检查 Prometheus 导出是否真的可用。返回 ``(可用, 原因)``，不抛异常。"""
    try:
        from .metrics import generate_metrics  # noqa: F401
    except Exception as exc:  # pragma: no cover - 环境相关
        return False, f"prometheus client 不可用: {exc}"
    try:
        body = generate_metrics()
    except Exception as exc:  # pragma: no cover - 环境相关
        return False, f"指标导出失败: {exc}"
    if not body:
        return False, "指标导出返回了空内容"
    return True, "ok"


def prometheus_response() -> Tuple[bytes, str]:
    """返回 ``(Prometheus 文本, Content-Type)``。

    直接复用 :func:`src.observability.metrics.generate_metrics` —— 那个函数在
    Phase 7a 之前是**全仓库无人调用**的孤儿（详见设计文档 §1 第 2 条）。
    """
    try:
        from .metrics import generate_metrics
    except Exception as exc:
        raise ProductionError(f"prometheus client 不可用: {exc}") from exc
    try:
        body = generate_metrics()
    except Exception as exc:
        raise ProductionError(f"指标导出失败: {exc}") from exc
    if not body:
        raise ProductionError("指标导出返回了空内容，拒绝以成功响应返回")
    return body, PROMETHEUS_CONTENT_TYPE


# ============================================================
# 2) 子系统探针 —— 把 14 内核纳入就绪检查
# ============================================================

#: ``(子系统名, 模块路径, getter 名)``。getter 均在对应内核的 ``__init__`` 中定义。
SUBSYSTEM_PROBES: Tuple[Tuple[str, str, str], ...] = (
    ("identity", "src.kernels.identity", "get_identity_manager"),
    ("memory", "src.kernels.memory", "get_memory_kernel"),
    # context 与 execution 没有 get_* 单例 getter，只有工厂函数 —— 实测确认，
    # 勿凭"应该有个 getter"猜测（本仓库同名/近名方法很多，唯代码是证据）。
    ("context", "src.kernels.context", "create_context_kernel"),
    ("capability", "src.kernels.capability", "get_capability_registry"),
    ("policy", "src.kernels.policy", "get_policy_engine"),
    ("security", "src.kernels.security", "get_security_engine"),
    ("execution", "src.kernels.execution", "create_execution_engine"),
    ("evaluation", "src.kernels.evaluation", "get_evaluator"),
    ("resource", "src.kernels.resource", "get_resource_manager"),
    ("event", "src.kernels.event", "get_event_bus"),
    ("network", "src.kernels.network", "get_network_bus"),
    ("audit", "src.kernels.audit", "get_audit_store"),
    ("trust", "src.kernels.trust", "get_trust_manager"),
    ("plugin", "src.kernels.plugin", "get_plugin_registry"),
)

STATUS_HEALTHY = "healthy"
STATUS_DEGRADED = "degraded"
STATUS_UNAVAILABLE = "unavailable"

#: 会阻断就绪的状态。``degraded`` 刻意**不算**——见设计文档 §2.1 探针语义。
_BLOCKING_STATUSES = frozenset({STATUS_UNAVAILABLE})


@dataclass
class SubsystemStatus:
    """单个内核的就绪状态。"""

    name: str
    status: str
    detail: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {"name": self.name, "status": self.status, "detail": dict(self.detail)}


@dataclass
class SubsystemReport:
    """全部内核的就绪汇总。"""

    subsystems: List[SubsystemStatus] = field(default_factory=list)

    @property
    def ready(self) -> bool:
        """只要没有 ``unavailable`` 即视为就绪（``degraded`` 不阻断）。"""
        return not any(s.status in _BLOCKING_STATUSES for s in self.subsystems)

    @property
    def counts(self) -> Dict[str, int]:
        out = {STATUS_HEALTHY: 0, STATUS_DEGRADED: 0, STATUS_UNAVAILABLE: 0}
        for s in self.subsystems:
            out[s.status] = out.get(s.status, 0) + 1
        return out

    def to_dict(self) -> Dict[str, Any]:
        return {
            "status": "ready" if self.ready else "not_ready",
            "total": len(self.subsystems),
            "counts": self.counts,
            "subsystems": [s.to_dict() for s in self.subsystems],
        }


def _probe_one(name: str, module_path: str, getter_name: str) -> SubsystemStatus:
    """探测单个子系统。**任何异常都在此收敛**，绝不外抛。"""
    try:
        import importlib

        module = importlib.import_module(module_path)
    except Exception as exc:
        return SubsystemStatus(name, STATUS_UNAVAILABLE, {"stage": "import", "error": str(exc)})

    getter: Optional[Callable[..., Any]] = getattr(module, getter_name, None)
    if getter is None:
        return SubsystemStatus(
            name, STATUS_UNAVAILABLE,
            {"stage": "getter", "error": f"{module_path}.{getter_name} 不存在"},
        )

    try:
        instance = getter()
    except Exception as exc:
        return SubsystemStatus(
            name, STATUS_UNAVAILABLE,
            {"stage": "construct", "error": str(exc)},
        )

    detail: Dict[str, Any] = {"type": type(instance).__name__}

    stats_fn = getattr(instance, "stats", None)
    if not callable(stats_fn):
        # 可实例化但没有 stats() —— 视为健康，只是无可观测计数。
        detail["stats"] = "not_available"
        return SubsystemStatus(name, STATUS_HEALTHY, detail)

    try:
        stats = stats_fn()
        detail["stats_keys"] = sorted(stats.keys()) if isinstance(stats, Mapping) else []
        return SubsystemStatus(name, STATUS_HEALTHY, detail)
    except Exception as exc:
        # 能构造但取不到统计 —— 核心仍可服务，如实标 degraded 而不阻断。
        detail["stats"] = "error"
        detail["error"] = str(exc)
        return SubsystemStatus(name, STATUS_DEGRADED, detail)


def check_subsystems(
    probes: Optional[Tuple[Tuple[str, str, str], ...]] = None,
) -> SubsystemReport:
    """逐个探测内核子系统，返回汇总报告。**永不抛异常。**"""
    chosen = probes if probes is not None else SUBSYSTEM_PROBES
    return SubsystemReport([_probe_one(*p) for p in chosen])


# ============================================================
# 3) 生产配置预检 —— 复用既有占位密钥语义
# ============================================================

SEVERITY_CRITICAL = "critical"
SEVERITY_WARNING = "warning"

#: 与 :data:`src.security.jwt_handler._PLACEHOLDER_SECRETS` 的镜像。
#: 之所以在此保留一份：预检端点不应因为 jwt_handler 的 import 失败而 500。
#: ``tests/observability/test_production.py`` 断言两份**必须相等**，防漂移。
PLACEHOLDER_SECRETS_MIRROR = frozenset({
    "replace-me", "change-me", "changeme", "your-secret-key",
    "secret", "***", "xxx", "todo", "password",
})

_PRODUCTION_ENVS = frozenset({"production", "prod"})

JWT_SECRET_ENVS: Tuple[str, ...] = (
    "LIUHAO_JWT_SECRET",
    "JWT_SECRET_KEY",
    "JWT_SECRET",
    "LIUHAO_JWT_PRIVATE_KEY",
)

SECRET_KEY_ENV = "SECRET_KEY"
APP_ENV_ENV = "APP_ENV"
DATABASE_URL_ENV = "DATABASE_URL"
LOG_LEVEL_ENV = "LOG_LEVEL"
POLICY_ENFORCE_ENV = "LIUHAO_KERNEL_POLICY_ENFORCE"


@dataclass
class ConfigFinding:
    """一条配置预检结论。**只含键名与代码，不含配置值。**"""

    key: str
    severity: str
    code: str
    message: str

    def to_dict(self) -> Dict[str, str]:
        return {
            "key": self.key,
            "severity": self.severity,
            "code": self.code,
            "message": self.message,
        }


@dataclass
class PreflightReport:
    """生产配置预检报告。"""

    findings: List[ConfigFinding] = field(default_factory=list)
    app_env: str = ""

    @property
    def critical_count(self) -> int:
        return sum(1 for f in self.findings if f.severity == SEVERITY_CRITICAL)

    @property
    def warning_count(self) -> int:
        return sum(1 for f in self.findings if f.severity == SEVERITY_WARNING)

    @property
    def ok(self) -> bool:
        """无 critical 即通过。"""
        return self.critical_count == 0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "ok": self.ok,
            "app_env": self.app_env,
            "critical_count": self.critical_count,
            "warning_count": self.warning_count,
            "findings": [f.to_dict() for f in self.findings],
        }


def _os_environ() -> Mapping[str, str]:
    import os

    return os.environ


def _placeholder_set() -> frozenset:
    """优先用 jwt_handler 的权威集合；不可用时退回镜像常量。"""
    try:
        from src.security.jwt_handler import _PLACEHOLDER_SECRETS

        return frozenset(_PLACEHOLDER_SECRETS)
    except Exception:  # pragma: no cover - 环境相关
        return PLACEHOLDER_SECRETS_MIRROR


def _is_placeholder(value: str, placeholders: frozenset) -> bool:
    return value.strip().lower() in placeholders


def validate_production_config(
    env: Optional[Mapping[str, str]] = None,
    strict: bool = False,
) -> PreflightReport:
    """生产配置预检。

    :param env: 配置来源，默认 ``os.environ``。显式传入便于测试。
    :param strict: ``True`` 且存在 critical 时抛 :class:`ProductionConfigError`。
    :returns: :class:`PreflightReport`
    """
    source = env if env is not None else _os_environ()
    placeholders = _placeholder_set()

    app_env = (source.get(APP_ENV_ENV) or "").strip()
    is_prod = app_env.lower() in _PRODUCTION_ENVS

    findings: List[ConfigFinding] = []

    # --- SECRET_KEY ---
    secret_key = source.get(SECRET_KEY_ENV, "") or ""
    if not secret_key.strip():
        findings.append(ConfigFinding(
            SECRET_KEY_ENV, SEVERITY_CRITICAL, "secret_key_missing",
            "SECRET_KEY 未设置：会话/加密派生密钥为空。",
        ))
    elif _is_placeholder(secret_key, placeholders):
        findings.append(ConfigFinding(
            SECRET_KEY_ENV, SEVERITY_CRITICAL, "secret_key_placeholder",
            "SECRET_KEY 是已知占位串，等同于公开密钥。",
        ))

    # --- JWT 签名密钥 ---
    usable_jwt = False
    placeholder_jwt: List[str] = []
    for name in JWT_SECRET_ENVS:
        raw = (source.get(name) or "").strip()
        if not raw:
            continue
        if _is_placeholder(raw, placeholders):
            placeholder_jwt.append(name)
            continue
        usable_jwt = True
        break
    if not usable_jwt:
        if placeholder_jwt:
            findings.append(ConfigFinding(
                placeholder_jwt[0], SEVERITY_CRITICAL, "jwt_secret_placeholder",
                "JWT 签名密钥是已知占位串，会被拒绝当作未配置。",
            ))
        else:
            findings.append(ConfigFinding(
                "LIUHAO_JWT_SECRET", SEVERITY_CRITICAL, "jwt_secret_missing",
                "未配置持久 JWT 签名密钥：进程重启即全端登出，多 worker 令牌互斥。",
            ))

    # --- 以下仅在 production 环境以下才有意义 ---
    if is_prod:
        db_url = (source.get(DATABASE_URL_ENV) or "").strip()
        if not db_url or db_url.lower().startswith("sqlite"):
            findings.append(ConfigFinding(
                DATABASE_URL_ENV, SEVERITY_WARNING, "sqlite_in_production",
                "生产环境使用 SQLite（或未声明 DATABASE_URL）：不适合多实例并发写入。",
            ))

        if (source.get(LOG_LEVEL_ENV) or "").strip().upper() == "DEBUG":
            findings.append(ConfigFinding(
                LOG_LEVEL_ENV, SEVERITY_WARNING, "debug_logging_in_production",
                "生产环境开启 DEBUG 日志：可能泄露内部路径与载荷。",
            ))

        enforce = (source.get(POLICY_ENFORCE_ENV) or "").strip().upper()
        if enforce not in {"LOW", "MEDIUM", "HIGH", "CRITICAL"}:
            findings.append(ConfigFinding(
                POLICY_ENFORCE_ENV, SEVERITY_WARNING, "policy_enforcement_off",
                "生产环境内核策略强制未开启（非 LOW/MEDIUM/HIGH/CRITICAL）。",
            ))

    report = PreflightReport(findings=findings, app_env=app_env)

    if strict and not report.ok:
        codes = ", ".join(f.code for f in findings if f.severity == SEVERITY_CRITICAL)
        raise ProductionConfigError(f"生产配置预检失败，critical 项: {codes}")

    return report
