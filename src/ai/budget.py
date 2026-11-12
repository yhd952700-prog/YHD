"""员工级预算：调用次数 / token / 费用上限，按天、周、月结算。

设计约束（为什么这样做）
------------------------
* **上限必须真的拦住**：派活前在这里做判定，超限由调用方转成 402，
  流程在第一个模型调用之前就停下；只在界面上"提示"不算实现。
* **费用只算算得出来的**：单价沿用 ``ModelRegistry.estimated_cost_per_1k``，
  经 ``BillingEngine`` 计算；没有价格的模型**不按 0 元入账**，而是标记
  ``cost_complete=False``——费用上限因此在存在未定价模型时"无法完全核算"，
  接口必须如实说出来，不能假装拦住了。
* **用量以真实快照为准**：优先用 provider 上报的 calls/tokens；若 provider
  没上报（例如 Mock），完成一次目标仍按 1 次调用计（派发本身），否则上限
  会永远不触发。快照里 ``last_source`` 记录这次用的是哪种口径。
* 存储沿用 ndjson/json + 原子写 + 进程内写锁的既有约定。
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
import threading
from datetime import datetime, timedelta
from typing import Any, Dict, Iterable, List, Optional, Tuple

from src.common.data_paths import resolve_store_path

logger = logging.getLogger(__name__)

_FILENAME = "liuhao_employee_budgets.json"
_VERSION = 1
_PROCESS_WRITE_LOCK = threading.Lock()

PERIODS = ("day", "week", "month")
PERIOD_LABELS = {"day": "每天", "week": "每周", "month": "每月"}

#: 单个员工名字的合理长度上限（与员工名一致）。
_NAME_LIMIT = 128


class BudgetError(ValueError):
    """预算参数本身不合法：调用方应按 400 处理。"""


class BudgetStoreError(RuntimeError):
    """预算表读写失败：调用方必须当成"本次操作没有完成"。"""


class BudgetExceeded(RuntimeError):
    """本期额度已用完：派活必须在这一步被拦下，而不是先跑再记账。"""

    def __init__(self, employee: str, violations: List[Dict[str, Any]], view: Dict[str, Any]):
        self.employee = employee
        self.violations = violations
        self.view = view
        message = "；".join(str(item.get("message") or "") for item in violations)
        super().__init__(message or f"员工「{employee}」本期额度已用完，已拦下本次派活。")


def default_path() -> str:
    repo_root = os.path.dirname(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    )
    return resolve_store_path(
        _FILENAME,
        os.path.join(repo_root, f".{_FILENAME}"),
        workspace=True,
    )


# ─── 周期 ──────────────────────────────────────────────────────


def _window_key(period: str, now: Optional[datetime] = None) -> str:
    """当前周期的稳定键：天=YYYY-MM-DD，周=YYYY-Www，月=YYYY-MM。"""
    moment = now or datetime.now()
    if period == "week":
        year, week, _ = moment.isocalendar()
        return f"{year}-W{week:02d}"
    if period == "month":
        return moment.strftime("%Y-%m")
    return moment.strftime("%Y-%m-%d")


def _period_bounds(
    period: str, now: Optional[datetime] = None
) -> Tuple[datetime, datetime]:
    """返回当前周期的 [起点, 下次重置) 两个本地时间点。"""
    moment = now or datetime.now()
    if period == "week":
        start = (moment - timedelta(days=moment.weekday())).replace(
            hour=0, minute=0, second=0, microsecond=0
        )
        return start, start + timedelta(days=7)
    if period == "month":
        start = moment.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        if start.month == 12:
            end = start.replace(year=start.year + 1, month=1)
        else:
            end = start.replace(month=start.month + 1)
        return start, end
    start = moment.replace(hour=0, minute=0, second=0, microsecond=0)
    return start, start + timedelta(days=1)


def _iso(moment: datetime) -> str:
    return moment.isoformat(timespec="seconds")


# ─── 参数归一化 ────────────────────────────────────────────────


def _clean_name(name: str) -> str:
    text = str(name or "").strip()
    if not text:
        raise BudgetError("员工名不能为空。")
    if len(text) > _NAME_LIMIT:
        raise BudgetError(f"员工名最多 {_NAME_LIMIT} 个字符。")
    return text


def _optional_int(value: Any, label: str, *, maximum: int) -> Optional[int]:
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        raise BudgetError(f"{label}必须是整数，收到布尔值。")
    try:
        number = int(value)
    except (TypeError, ValueError) as exc:
        raise BudgetError(f"{label}必须是整数，收到：{value!r}") from exc
    if number < 0:
        raise BudgetError(f"{label}不能为负数。")
    if number > maximum:
        raise BudgetError(f"{label}过大（上限 {maximum}）。")
    return number


def _optional_cost(value: Any) -> Optional[float]:
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        raise BudgetError("费用上限必须是数字，收到布尔值。")
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise BudgetError(f"费用上限必须是数字，收到：{value!r}") from exc
    if number < 0:
        raise BudgetError("费用上限不能为负数。")
    if number > 1e9:
        raise BudgetError("费用上限过大。")
    return round(number, 6)


def _clean_period(value: Any) -> str:
    period = str(value or "day").strip().lower()
    if period not in PERIODS:
        raise BudgetError("重置周期只能是 day / week / month。")
    return period


def _empty_policy() -> Dict[str, Any]:
    return {
        "enabled": True,
        "calls_limit": None,
        "tokens_limit": None,
        "cost_limit": None,
        "period": "day",
    }


def _safe_policy(raw: Any) -> Optional[Dict[str, Any]]:
    """磁盘上的策略可能被手改坏：坏值退回默认，不因此整表不可用。"""
    if not isinstance(raw, dict):
        return None
    try:
        return _normalise_policy({}, base=raw)
    except BudgetError:
        logger.warning("employee budget: invalid stored policy ignored: %r", raw)
        return _empty_policy()


def _normalise_policy(raw: Dict[str, Any], base: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    policy = dict(base or _empty_policy())
    if "enabled" in raw and raw["enabled"] is not None:
        policy["enabled"] = bool(raw["enabled"])
    if "calls_limit" in raw:
        policy["calls_limit"] = _optional_int(
            raw["calls_limit"], "调用次数上限", maximum=1_000_000_000
        )
    if "tokens_limit" in raw:
        policy["tokens_limit"] = _optional_int(
            raw["tokens_limit"], "token 上限", maximum=1_000_000_000_000
        )
    if "cost_limit" in raw:
        policy["cost_limit"] = _optional_cost(raw["cost_limit"])
    if "period" in raw and raw["period"] not in (None, ""):
        policy["period"] = _clean_period(raw["period"])
    return policy


def _empty_usage() -> Dict[str, Any]:
    return {
        "calls": 0,
        "tokens": 0,
        "cost": 0.0,
        "cost_complete": True,
        "cost_unknown_calls": 0,
        "last_source": "",
        "updated_at": "",
    }


def _normalise_usage(raw: Any) -> Dict[str, Any]:
    usage = _empty_usage()
    if not isinstance(raw, dict):
        return usage
    try:
        usage["calls"] = max(0, int(raw.get("calls") or 0))
        usage["tokens"] = max(0, int(raw.get("tokens") or 0))
        usage["cost"] = round(max(0.0, float(raw.get("cost") or 0.0)), 6)
    except (TypeError, ValueError):
        return _empty_usage()
    usage["cost_complete"] = bool(raw.get("cost_complete", True))
    try:
        usage["cost_unknown_calls"] = max(0, int(raw.get("cost_unknown_calls") or 0))
    except (TypeError, ValueError):
        usage["cost_unknown_calls"] = 0
    usage["last_source"] = str(raw.get("last_source") or "")
    usage["updated_at"] = str(raw.get("updated_at") or "")
    return usage


# ─── IO ────────────────────────────────────────────────────────


def _empty_document() -> Dict[str, Any]:
    return {"version": _VERSION, "employees": {}}


class _Store:
    """预算表的读写（实例很轻，每次操作 new 一个即可）。"""

    def __init__(self, path: Optional[str] = None) -> None:
        self.path = path or default_path()
        self._lock = threading.RLock()

    def _load(self) -> Dict[str, Any]:
        if not os.path.exists(self.path):
            return _empty_document()
        try:
            with open(self.path, "r", encoding="utf-8") as handle:
                raw = json.load(handle)
        except (OSError, json.JSONDecodeError) as exc:
            raise BudgetStoreError(
                f"员工预算表读取失败：{self.path}（{exc}）。"
                "为避免在无法确认额度的情况下继续派活，本次派活会被拦下。"
            ) from exc
        if not isinstance(raw, dict):
            raise BudgetStoreError(f"员工预算表格式不可识别：{self.path}。")
        employees = raw.get("employees")
        if employees is None:
            raw["employees"] = {}
        elif not isinstance(employees, dict):
            raise BudgetStoreError(f"员工预算表格式不可识别：{self.path}（employees 不是对象）。")
        raw["version"] = _VERSION
        return raw

    def _write(self, document: Dict[str, Any]) -> None:
        directory = os.path.dirname(os.path.abspath(self.path))
        os.makedirs(directory, exist_ok=True)
        document["version"] = _VERSION
        document["updated_at"] = _iso(datetime.now())
        payload = json.dumps(document, ensure_ascii=False, indent=2)
        handle = None
        tmp_path = ""
        try:
            fd, tmp_path = tempfile.mkstemp(
                prefix=".employee-budgets-", suffix=".tmp", dir=directory
            )
            handle = os.fdopen(fd, "w", encoding="utf-8")
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
            handle.close()
            handle = None
            os.replace(tmp_path, self.path)
            tmp_path = ""
        except OSError as exc:
            raise BudgetStoreError(
                f"员工预算表写入失败：{self.path}（{exc}）。本次修改没有落盘。"
            ) from exc
        finally:
            if handle is not None:
                try:
                    handle.close()
                except OSError:  # pragma: no cover
                    pass
            if tmp_path and os.path.exists(tmp_path):
                try:
                    os.unlink(tmp_path)
                except OSError:  # pragma: no cover
                    pass

    # -- 公共读写 --------------------------------------------------
    def read_entry(self, name: str) -> Dict[str, Any]:
        record = self._load()["employees"].get(name) or {}
        if not isinstance(record, dict):
            record = {}
        return record

    def write_entry(self, name: str, record: Dict[str, Any]) -> None:
        with _PROCESS_WRITE_LOCK:
            with self._lock:
                document = self._load()
                document["employees"][name] = record
                self._write(document)

    def delete_entry(self, name: str) -> bool:
        with _PROCESS_WRITE_LOCK:
            with self._lock:
                document = self._load()
                if name not in document["employees"]:
                    return False
                del document["employees"][name]
                self._write(document)
                return True


# ─── 定价 ──────────────────────────────────────────────────────


def _price_table(models: Iterable[str]) -> Dict[str, float]:
    """按模型注册表取每 1K token 价格；没有价格的不进表（→ 成本未知）。"""
    wanted = [str(model or "").strip() for model in models]
    wanted = [model for model in wanted if model]
    table: Dict[str, float] = {}
    if not wanted:
        return table
    try:
        from src.models.registry import get_model_registry

        registry = get_model_registry()
    except Exception:  # noqa: BLE001 - 注册表不可用 → 全部按未定价处理
        logger.warning("employee budget: model registry unavailable", exc_info=True)
        return table
    for model in dict.fromkeys(wanted):
        metadata = registry.get_model(model)
        if metadata is None:
            continue
        try:
            table[model] = float(metadata.estimated_cost_per_1k)
        except (TypeError, ValueError):
            continue
    return table


def _cost_for_usage(usage: Dict[str, Any]) -> Tuple[float, bool, int]:
    """从真实 usage 快照算费用：返回 (费用, 是否完整, 未定价调用数)。"""
    from .economy import BillingEngine

    buckets = usage.get("by_label") if isinstance(usage, dict) else None
    rows: List[Tuple[str, int, int, int]] = []
    if isinstance(buckets, dict) and buckets:
        for bucket in buckets.values():
            if not isinstance(bucket, dict):
                continue
            rows.append(
                (
                    str(bucket.get("model") or usage.get("model") or ""),
                    int(bucket.get("tokens_in") or 0),
                    int(bucket.get("tokens_out") or 0),
                    max(1, int(bucket.get("calls") or 0)),
                )
            )
    elif int(usage.get("calls") or 0) > 0:
        rows.append(
            (
                str(usage.get("model") or ""),
                int(usage.get("tokens_in") or 0),
                int(usage.get("tokens_out") or 0),
                int(usage.get("calls") or 0),
            )
        )
    if not rows:
        return 0.0, True, 0
    billing = BillingEngine(_price_table(model for model, _, _, _ in rows))
    unknown_calls = 0
    for model, tokens_in, tokens_out, calls in rows:
        billing.record_usage(model, tokens_in, tokens_out)
        if not billing.has_price(model):
            unknown_calls += calls
    report = billing.usage_report()
    return float(report["total_cost"]), bool(report["total_cost_complete"]), unknown_calls


# ─── 判定 ──────────────────────────────────────────────────────


def _evaluate(employee: str, policy: Dict[str, Any], usage: Dict[str, Any]) -> Dict[str, Any]:
    """按上限逐项判定，返回可直接上屏的预算视图。"""
    violations: List[Dict[str, Any]] = []
    remaining: Dict[str, Optional[float]] = {"calls": None, "tokens": None, "cost": None}
    notes: List[str] = []

    calls_limit = policy.get("calls_limit")
    if calls_limit is not None:
        used_calls = int(usage.get("calls") or 0)
        remaining["calls"] = max(0, int(calls_limit) - used_calls)
        if used_calls >= int(calls_limit):
            violations.append(
                {
                    "kind": "calls",
                    "limit": int(calls_limit),
                    "used": used_calls,
                    "message": (
                        f"「{employee}」本期调用次数已用完"
                        f"（{used_calls}/{int(calls_limit)} 次），本次派活已拦下。"
                    ),
                }
            )

    tokens_limit = policy.get("tokens_limit")
    if tokens_limit is not None:
        used_tokens = int(usage.get("tokens") or 0)
        remaining["tokens"] = max(0, int(tokens_limit) - used_tokens)
        if used_tokens >= int(tokens_limit):
            violations.append(
                {
                    "kind": "tokens",
                    "limit": int(tokens_limit),
                    "used": used_tokens,
                    "message": (
                        f"「{employee}」本期 token 已用完"
                        f"（{used_tokens}/{int(tokens_limit)}），本次派活已拦下。"
                    ),
                }
            )

    cost_limit = policy.get("cost_limit")
    cost_complete = bool(usage.get("cost_complete", True))
    if cost_limit is not None:
        used_cost = float(usage.get("cost") or 0.0)
        remaining["cost"] = round(max(0.0, float(cost_limit) - used_cost), 6)
        if used_cost >= float(cost_limit):
            violations.append(
                {
                    "kind": "cost",
                    "limit": float(cost_limit),
                    "used": round(used_cost, 6),
                    "message": (
                        f"「{employee}」本期费用已超出上限"
                        f"（{round(used_cost, 6)}/{float(cost_limit)}，按模型登记价格换算），"
                        "本次派活已拦下。"
                    ),
                }
            )
        if not cost_complete:
            notes.append(
                "费用上限无法完全核算：本期有用量的模型暂无价格，"
                "这部分没有计入费用（调用次数与 token 上限不受影响）。"
            )

    enabled = bool(policy.get("enabled", True))
    has_limits = any(
        policy.get(key) is not None
        for key in ("calls_limit", "tokens_limit", "cost_limit")
    )
    exceeds = enabled and bool(violations)

    period = str(policy.get("period") or "day")
    now = datetime.now()
    start, end = _period_bounds(period, now)
    return {
        "employee": employee,
        "policy": policy,
        "period_label": PERIOD_LABELS.get(period, period),
        "window": _window_key(period, now),
        "period_start": _iso(start),
        "next_reset_at": _iso(end),
        "usage": usage,
        "remaining": remaining,
        "exceeded": exceeds,
        "enforced": enabled and has_limits,
        "violations": violations if enabled else [],
        "notes": notes,
        "store_error": "",
    }


def _view_for(name: str, store: Optional[_Store] = None) -> Dict[str, Any]:
    """读取某员工的预算视图；未配置过预算时返回 ``configured=False``。"""
    store = store or _Store()
    record = store.read_entry(name)
    policy = _safe_policy(record.get("policy"))
    if policy is None:
        now = datetime.now()
        return {
            "employee": name,
            "configured": False,
            "policy": _empty_policy(),
            "period_label": PERIOD_LABELS["day"],
            "window": _window_key("day", now),
            "period_start": _iso(_period_bounds("day", now)[0]),
            "next_reset_at": _iso(_period_bounds("day", now)[1]),
            "usage": _empty_usage(),
            "remaining": {"calls": None, "tokens": None, "cost": None},
            "exceeded": False,
            "enforced": False,
            "violations": [],
            "notes": [],
            "store_error": "",
        }
    policy = _normalise_policy({}, base=policy)
    usage = _normalise_usage(record.get("usage"))
    period = str(policy["period"])
    current = _window_key(period)
    if str(record.get("window") or "") != current:
        # 跨周期：读取时按 0 展示，下一次写入时把新周期落盘。
        usage = _empty_usage()
    view = _evaluate(name, policy, usage)
    view["configured"] = True
    view["window"] = current
    return view


# ─── 对外 API ──────────────────────────────────────────────────


def get_employee_budget(name: str) -> Dict[str, Any]:
    """读取某员工的预算（含当期消耗、剩余额度、是否已超限）。"""
    return _view_for(_clean_name(name))


def set_employee_budget(name: str, patch: Dict[str, Any]) -> Dict[str, Any]:
    """设置/修改预算；``reset_usage=True`` 时把当期用量清零。"""
    employee = _clean_name(name)
    if not isinstance(patch, dict):
        raise BudgetError("预算参数必须是一个对象。")
    store = _Store()
    record = store.read_entry(employee)
    base = _safe_policy(record.get("policy"))
    previous_period = str((base or {}).get("period") or "day")
    policy = _normalise_policy(patch, base=base)
    usage = _normalise_usage(record.get("usage"))
    current = _window_key(policy["period"])
    if (
        bool(patch.get("reset_usage"))
        or str(record.get("window") or "") != current
        or previous_period != policy["period"]
    ):
        usage = _empty_usage()
    entry = {
        "policy": policy,
        "window": current,
        "usage": usage,
        "updated_at": _iso(datetime.now()),
    }
    store.write_entry(employee, entry)
    return _view_for(employee, store)


def delete_employee_budget(name: str) -> bool:
    """移除某员工的预算（回到"不限"）。"""
    return _Store().delete_entry(_clean_name(name))


def forget_employee_budget(name: str) -> bool:
    """员工被删除时清掉预算，避免同名新员工继承旧额度。"""
    try:
        return delete_employee_budget(name)
    except (BudgetError, BudgetStoreError) as exc:  # 清理失败不影响删除本身
        logger.warning("employee budget cleanup failed for %r: %s", name, exc)
        return False


def require_employee_budget(name: str) -> Dict[str, Any]:
    """派活前的硬闸：超限抛 ``BudgetExceeded``，否则返回当前视图。

    预算表读不出来时**按"无法确认额度"拦下**（fail-closed），
    而不是放行——放行等于预算从来没生效。
    """
    employee = _clean_name(name)
    try:
        view = _view_for(employee)
    except BudgetStoreError as exc:
        view = {
            "employee": employee,
            "configured": True,
            "policy": _empty_policy(),
            "usage": _empty_usage(),
            "remaining": {"calls": None, "tokens": None, "cost": None},
            "exceeded": True,
            "enforced": True,
            "violations": [
                {
                    "kind": "store_unreadable",
                    "limit": None,
                    "used": None,
                    "message": f"{exc}",
                }
            ],
            "notes": [],
            "store_error": str(exc),
        }
        raise BudgetExceeded(employee, view["violations"], view) from exc
    if view.get("exceeded"):
        raise BudgetExceeded(employee, list(view["violations"]), view)
    return view


def record_goal_usage(name: str, usage: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """目标执行结束后结算用量；未配置预算的员工不产生任何记录。

    返回结算后的视图；未配置预算或结算失败返回 ``None``（调用方不打断业务）。
    """
    employee = str(name or "").strip()
    if not employee:
        return None
    store = _Store()
    try:
        record = store.read_entry(employee)
    except BudgetStoreError as exc:
        logger.error("evt=employee_budget_settle_failed employee=%s error=%s", employee, exc)
        return None
    policy = _safe_policy(record.get("policy"))
    if policy is None:
        return None
    period = str(policy["period"])
    current = _window_key(period)
    usage_state = _normalise_usage(record.get("usage"))
    if str(record.get("window") or "") != current:
        usage_state = _empty_usage()

    snapshot = usage if isinstance(usage, dict) else {}
    raw_calls = int(snapshot.get("calls") or 0)
    tokens = int(snapshot.get("total_tokens") or 0)
    cost, complete, unknown_calls = _cost_for_usage(snapshot)
    source = "provider"
    calls = raw_calls
    if calls <= 0:
        # provider 没上报用量（Mock/异常路径）时，按"派发即 1 次调用"兜底。
        # 这是保守估计，不是真实 token 数，也不冒充真实值：
        # 只为保证次数上限在没有上报的链路上仍然会被触发。
        calls = 1
        source = "dispatch"

    merged_complete = bool(usage_state.get("cost_complete", True)) and complete
    scored = _empty_usage()
    scored["calls"] = int(usage_state.get("calls") or 0) + max(0, calls)
    scored["tokens"] = int(usage_state.get("tokens") or 0) + max(0, tokens)
    scored["cost"] = round(float(usage_state.get("cost") or 0.0) + cost, 6)
    scored["cost_complete"] = merged_complete
    scored["cost_unknown_calls"] = int(usage_state.get("cost_unknown_calls") or 0) + unknown_calls
    scored["last_source"] = source
    scored["updated_at"] = _iso(datetime.now())

    entry = {
        "policy": policy,
        "window": current,
        "usage": scored,
        "updated_at": _iso(datetime.now()),
    }
    try:
        store.write_entry(employee, entry)
    except BudgetStoreError as exc:
        # 结算失败必须显式告警：这段用量没有计入，上限会偏松。
        logger.error("evt=employee_budget_settle_failed employee=%s error=%s", employee, exc)
        return None
    return _evaluate(employee, policy, scored) | {"configured": True}


__all__ = [
    "BudgetError",
    "BudgetExceeded",
    "BudgetStoreError",
    "PERIODS",
    "PERIOD_LABELS",
    "default_path",
    "delete_employee_budget",
    "forget_employee_budget",
    "get_employee_budget",
    "record_goal_usage",
    "require_employee_budget",
    "set_employee_budget",
]
