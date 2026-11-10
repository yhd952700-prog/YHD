"""托盘状态来源：从网关读**真实**执行态；读不到就明说"看不到"，不猜。

状态口径
--------
* ``stopped``：网关进程没在跑（由 ``app`` 判定，本模块不产出）。
* ``error``：接口读失败，或有执行个体处于 ``failed``。
* ``waiting``：有等待人工确认的高危动作（``/v1/policy/approvals/pending``）。
* ``working``：有 ``state=running`` 的 Goal。
* ``idle``：以上都没有。
优先级：``stopped > error > waiting > working > idle``。

未登录 / 令牌过期 / 读不到员工列表时，``authenticated=False``：``app`` 只显示
``idle/stopped/error``，tooltip 明确写"未登录，任务状态不可见"，绝不显示一个
猜出来的"执行中/等你确认"。

"暂停 AI 员工接活"
------------------
* 动作 = 把所有**当前账号可见**的执行个体（``/v1/employees`` 的 ``agents``）
  逐个 ``POST /v1/employees/{id}/pause``，不碰后端全局配置，也不阻止用户直接
  对话；恢复同理。
* 暂停意图持久化在 ``<DATA_DIR>/desktop/employees-paused.json``：进程重启后
  仍然是暂停态，**暂停期间新建的员工**由轮询器发现后立刻一并暂停，并在托盘
  气泡里点名提示（不会静默放过）。
"""

from __future__ import annotations

import json
import logging
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen

from .tray import STATE_ERROR, STATE_IDLE, STATE_WAITING, STATE_WORKING

logger = logging.getLogger(__name__)

PAUSED_FILE = "employees-paused.json"
#: 轮询间隔：托盘状态不需要秒级；4s 足够"看得到在跑"，又不会让网关白忙。
POLL_INTERVAL = 4.0
#: 单次接口超时（本机回环）。
HTTP_TIMEOUT = 2.0
#: 自动暂停新员工失败后，隔多久再试（避免每 4s 重复打同一个失败请求）。
RETRY_PAUSE_AFTER = 30.0
#: 执行个体的终态：不再自动暂停（暂停对它们没有意义）。
_SKIP_PAUSE = {"paused", "stopped"}


# ---------------------------------------------------------------------- #
# 暂停意图的持久化
# ---------------------------------------------------------------------- #
def _paused_path(data_dir: Path | str) -> Path:
    return Path(data_dir) / "desktop" / PAUSED_FILE


def read_paused(data_dir: Path | str) -> bool:
    """读取"AI 员工接活已暂停"的持久化意图；读不到按未暂停处理。"""
    try:
        payload = json.loads(_paused_path(data_dir).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    return bool(payload.get("paused")) if isinstance(payload, dict) else False


def write_paused(data_dir: Path | str, paused: bool, *, employees: int | None = None) -> None:
    """写入暂停意图（含时间与当时的员工数，便于排障时解释状态）。"""
    path = _paused_path(data_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "paused": bool(paused),
        "at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "employees": employees,
    }
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    tmp.replace(path)
    logger.info("evt=employees.pause_flag paused=%s employees=%s", paused, employees)


# ---------------------------------------------------------------------- #
# HTTP
# ---------------------------------------------------------------------- #
def _request_json(
    base_url: str,
    path: str,
    token: str,
    *,
    method: str = "GET",
    timeout: float = HTTP_TIMEOUT,
) -> tuple[int, dict[str, Any] | None]:
    """发一次带 Bearer 的请求，返回 ``(状态码, JSON)``。

    只记录状态码，不记录响应体（可能含账号名）。网络失败返回 ``(0, None)``。
    """
    url = base_url.rstrip("/") + path
    request = Request(url, method=method)
    if token:
        request.add_header("Authorization", f"Bearer {token}")
    try:
        with urlopen(request, timeout=timeout) as response:  # noqa: S310 - 本机回环
            raw = response.read()
            status = int(getattr(response, "status", 200) or 200)
    except HTTPError as exc:
        return int(exc.code), None
    except (URLError, OSError):
        return 0, None
    except ValueError:
        return 0, None
    try:
        body = json.loads(raw.decode("utf-8")) if raw else {}
    except (ValueError, UnicodeDecodeError):
        return status, None
    return status, body if isinstance(body, dict) else None


def _agent_rows(body: dict[str, Any] | None) -> list[dict[str, Any]]:
    rows = body.get("agents") if isinstance(body, dict) else None
    return [row for row in rows or [] if isinstance(row, dict) and row.get("id")]


def _employee_count(body: dict[str, Any] | None) -> int | None:
    if not isinstance(body, dict):
        return None
    try:
        return int(body.get("employee_count"))
    except (TypeError, ValueError):
        employees = body.get("employees")
        return len(employees) if isinstance(employees, list) else None


def pause_all(base_url: str, token: str, *, timeout: float = HTTP_TIMEOUT) -> dict[str, Any]:
    """暂停所有可见执行个体；返回真实计数（不夸大成功数）。"""
    status, body = _request_json(base_url, "/v1/employees", token, timeout=timeout)
    if status != 200 or body is None:
        return {"ok": False, "status": status, "paused": 0, "failed": 0, "employees": None}
    agents = _agent_rows(body)
    targets = [
        row for row in agents if str(row.get("status") or "") not in _SKIP_PAUSE
    ]
    paused = failed = 0
    for row in targets:
        code, _ = _request_json(
            base_url,
            f"/v1/employees/{quote(str(row['id']), safe='')}/pause",
            token,
            method="POST",
            timeout=timeout,
        )
        if code == 200:
            paused += 1
        else:
            failed += 1
    logger.info(
        "evt=employees.paused agents=%s paused=%s failed=%s",
        len(agents),
        paused,
        failed,
    )
    return {
        "ok": failed == 0,
        "status": status,
        "paused": paused,
        "failed": failed,
        "employees": _employee_count(body),
        "agents": len(agents),
    }


def resume_all(base_url: str, token: str, *, timeout: float = HTTP_TIMEOUT) -> dict[str, Any]:
    """恢复所有处于暂停状态的执行个体；返回真实计数。"""
    status, body = _request_json(base_url, "/v1/employees", token, timeout=timeout)
    if status != 200 or body is None:
        return {"ok": False, "status": status, "resumed": 0, "failed": 0, "employees": None}
    agents = _agent_rows(body)
    targets = [row for row in agents if str(row.get("status") or "") == "paused"]
    resumed = failed = 0
    for row in targets:
        code, _ = _request_json(
            base_url,
            f"/v1/employees/{quote(str(row['id']), safe='')}/resume",
            token,
            method="POST",
            timeout=timeout,
        )
        if code == 200:
            resumed += 1
        else:
            failed += 1
    logger.info(
        "evt=employees.resumed agents=%s resumed=%s failed=%s",
        len(agents),
        resumed,
        failed,
    )
    return {
        "ok": failed == 0,
        "status": status,
        "resumed": resumed,
        "failed": failed,
        "employees": _employee_count(body),
        "agents": len(agents),
    }


def logout_token(base_url: str, token: str, *, timeout: float = HTTP_TIMEOUT) -> int:
    """撤销服务端令牌（退出登录用）；返回状态码，失败静默。"""
    status, _ = _request_json(
        base_url, "/v1/auth/logout", token, method="POST", timeout=timeout
    )
    return status


# ---------------------------------------------------------------------- #
# 快照
# ---------------------------------------------------------------------- #
def compute_state(
    *,
    pending_approvals: int | None,
    running_goals: int,
    failed_agents: int,
    api_error: bool = False,
) -> str:
    """按 ``error > waiting > working > idle`` 给出状态位。"""
    if api_error or failed_agents > 0:
        return STATE_ERROR
    if (pending_approvals or 0) > 0:
        return STATE_WAITING
    if running_goals > 0:
        return STATE_WORKING
    return STATE_IDLE


def fetch_snapshot(
    base_url: str,
    token: str,
    *,
    timeout: float = HTTP_TIMEOUT,
) -> dict[str, Any]:
    """读一次真实状态；未登录或 401 时返回 ``authenticated=False``。"""
    if not token:
        return {
            "authenticated": False,
            "auth_expired": False,
            "state": STATE_IDLE,
            "employees": None,
            "agents": 0,
            "running_goals": 0,
            "pending_approvals": None,
            "failed_agents": 0,
            "error": None,
        }
    goals_status, goals_body = _request_json(
        base_url, "/v1/goals", token, timeout=timeout
    )
    if goals_status == 401:
        return {
            "authenticated": False,
            "auth_expired": True,
            "state": STATE_IDLE,
            "employees": None,
            "agents": 0,
            "running_goals": 0,
            "pending_approvals": None,
            "failed_agents": 0,
            "error": None,
        }
    goals = (goals_body or {}).get("goals")
    goals = goals if isinstance(goals, list) else []
    running = sum(
        1 for item in goals if isinstance(item, dict) and item.get("state") == "running"
    )
    api_error = goals_status != 200

    emp_status, emp_body = _request_json(
        base_url, "/v1/employees", token, timeout=timeout
    )
    agents = _agent_rows(emp_body)
    failed_agents = sum(
        1 for row in agents if str(row.get("status") or "") == "failed"
    )
    employees = _employee_count(emp_body) if emp_status == 200 else None

    pending: int | None = None
    approval_status, approval_body = _request_json(
        base_url, "/v1/policy/approvals/pending", token, timeout=timeout
    )
    if approval_status == 200 and isinstance(approval_body, dict):
        try:
            pending = int(approval_body.get("count") or 0)
        except (TypeError, ValueError):
            pending = None
    # 403 = 不是管理员，看不到审批队列：这是"权限看不到"，不是"没有待确认"，
    # 保持 None，界面不显示等待状态。

    error_text = None
    if api_error:
        error_text = "读取任务状态失败（网关接口不可达）"

    return {
        "authenticated": True,
        "auth_expired": False,
        "state": compute_state(
            pending_approvals=pending,
            running_goals=running,
            failed_agents=failed_agents,
            api_error=api_error,
        ),
        "employees": employees,
        "agents": len(agents),
        "running_goals": running,
        "pending_approvals": pending,
        "failed_agents": failed_agents,
        "error": error_text,
    }


class TrayStatePoller:
    """后台轮询托盘状态；暂停期间负责把"新来的员工"一并暂停。"""

    def __init__(
        self,
        *,
        base_url: Callable[[], str],
        token: Callable[[], str],
        on_snapshot: Callable[[dict[str, Any]], None],
        on_auth_expired: Callable[[], None] | None = None,
        paused: Callable[[], bool] | None = None,
        interval: float = POLL_INTERVAL,
        timeout: float = HTTP_TIMEOUT,
    ) -> None:
        self._base_url = base_url
        self._token = token
        self._on_snapshot = on_snapshot
        self._on_auth_expired = on_auth_expired
        self._paused = paused or (lambda: False)
        self._interval = max(0.5, interval)
        self._timeout = timeout
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        #: 已提示过的"暂停期间新员工"，避免每次轮询重复弹气泡。
        self._announced: set[str] = set()
        #: 自动暂停失败过的 agent id → 下次重试时间（避免每轮都打）。
        self._pause_retry_at: dict[str, float] = {}

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._run, name="liuhao-tray-state", daemon=True
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        thread = self._thread
        if thread is not None and thread.is_alive():
            thread.join(timeout=2.0)
        self._thread = None

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                snapshot = self.poll_once()
                self._on_snapshot(snapshot)
            except Exception:  # noqa: BLE001 - 状态轮询不能拖垮托盘
                logger.warning("托盘状态轮询异常", exc_info=False)
            self._stop.wait(self._interval)

    def poll_once(self) -> dict[str, Any]:
        """读一次快照；暂停期间顺带补暂停新出现的执行个体。"""
        base = self._base_url() or ""
        token = self._token() or ""
        if not base:
            return {
                "authenticated": False,
                "auth_expired": False,
                "state": STATE_IDLE,
                "employees": None,
                "agents": 0,
                "running_goals": 0,
                "pending_approvals": None,
                "failed_agents": 0,
                "error": None,
            }
        snapshot = fetch_snapshot(base, token, timeout=self._timeout)
        if snapshot.get("auth_expired") and self._on_auth_expired is not None:
            self._on_auth_expired()
        if snapshot.get("authenticated"):
            snapshot["newly_paused"] = self._ensure_paused(base, token)
        return snapshot

    def _ensure_paused(self, base: str, token: str) -> list[str]:
        """暂停期间：把还没暂停的执行个体一并暂停，返回**本次新提示**的名字。

        失败按 agent 记一个重试时间，不在每一轮里反复打同一个失败请求。
        """
        if not self._paused():
            self._announced.clear()
            self._pause_retry_at.clear()
            return []
        status, body = _request_json(base, "/v1/employees", token, timeout=self._timeout)
        if status != 200 or body is None:
            return []
        now = time.monotonic()
        newly: list[str] = []
        for row in _agent_rows(body):
            agent_id = str(row["id"])
            if str(row.get("status") or "") in _SKIP_PAUSE:
                continue
            if self._pause_retry_at.get(agent_id, 0.0) > now:
                continue
            code, _ = _request_json(
                base,
                f"/v1/employees/{quote(agent_id, safe='')}/pause",
                token,
                method="POST",
                timeout=self._timeout,
            )
            if code == 200:
                self._pause_retry_at.pop(agent_id, None)
                name = str(row.get("employee_name") or row.get("name") or agent_id)
                if agent_id not in self._announced:
                    self._announced.add(agent_id)
                    newly.append(name)
                logger.info("evt=employees.paused_new agent=%s", agent_id)
            else:
                self._pause_retry_at[agent_id] = now + RETRY_PAUSE_AFTER
                logger.warning("evt=employees.pause_failed agent=%s status=%s", agent_id, code)
        return newly


__all__ = [
    "PAUSED_FILE",
    "POLL_INTERVAL",
    "TrayStatePoller",
    "compute_state",
    "fetch_snapshot",
    "logout_token",
    "pause_all",
    "read_paused",
    "resume_all",
    "write_paused",
]
