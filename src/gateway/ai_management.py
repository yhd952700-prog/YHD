"""AI Employee / Goal / Workflow 管理 REST 端点。

提供真实可操作的 Goal 创建+执行、AI Employee 状态查看与生命周期控制。

路由前缀 ``/v1``，与 dashboard / chat / roster 等同层。
所有端点走 ``require_human_principal`` 鉴权闸门（在 main.py 挂载时声明）。

设计原则：
- **NO-FAKE**：所有状态来自真实的 AgentRuntime / Employee 实例，不造数据；
- **幂等**：GET 无副作用，POST 创建用 UUID 去重；
- **诚实失败**：异常不吞，返回 503 + 真实错误信息。
"""

from __future__ import annotations

import copy
import gzip
import json
import logging
import os
import shutil
import threading
import time
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Callable, Dict, Iterator, List, Optional, Set, Tuple

if os.name == "nt":  # pragma: no cover - platform split
    import msvcrt
else:  # pragma: no cover - platform split
    import fcntl

if TYPE_CHECKING:  # pragma: no cover - import-time only, avoids any gateway<->ai cycle
    # 员工模型已搬到 src/ai（曾经在 src/gateway），类型块里的旧路径是
    # "改了名字/搬了位置但导入没跟着改"的隐患，已对齐真实模块。
    from ..ai.employee import Agent, Employee
    from ..ai.employee_store import EmployeeStore

from fastapi import APIRouter, Depends, Header, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from ..ai.usage_meter import PLANNING_LABEL, meter_scope, snapshot
from .account_usage import record_usage

# Used to turn a produced file's absolute path into a workspace-relative,
# honest artifact reference (never inventing a path that isn't real).
from src.ai.workspace import workspace_root
# 真实信任决议：雇佣前先查信任内核的撤销注册表。``src.kernels.trust`` 不反向
# import ``src.gateway``，故此处取顶层 import 无循环风险（已验证）。
from src.kernels.trust import get_trust_manager

logger = logging.getLogger(__name__)


def _butler_model_planning_enabled() -> bool:
    """Whether the butler path may call the configured default model.

    Product default is ON. Tests/offline environments set
    ``LIUHAO_BUTLER_MODEL=0`` so the deterministic keyword planner and
    deterministic summaries are used without any network dependency.
    """
    raw = os.environ.get("LIUHAO_BUTLER_MODEL", "1").strip().lower()
    return raw not in ("0", "false", "off", "no", "disabled")


def _outcome_label(value: str) -> str:
    """Serialize an outcome as its stable wire value (``"success"``/``"partial"``).

    ``str(EvaluationOutcome.PARTIAL)`` yields ``"EvaluationOutcome.PARTIAL"`` on
    Python 3.11+, so the UI never matched ``"partial"`` and could not tell a
    partial delivery apart from success. The enum value is the contract.
    """
    try:
        from src.kernels.evaluation import EvaluationOutcome

        return EvaluationOutcome(value).value
    except Exception:  # noqa: BLE001 - keep a usable string either way
        return value


router = APIRouter(prefix="/v1", tags=["ai-management"])


# ─── 请求/响应模型 ───────────────────────────────────────────

class GoalCreateRequest(BaseModel):
    """创建+执行 Goal 的请求体。"""
    natural_language: str = Field(
        ..., min_length=1, max_length=2000,
        description="自然语言目标描述",
    )
    scope: str = Field("L1", description="执行范围 L0-L7")
    plan_mode: str = Field("auto", description="计划模式: auto/manual")
    verification_criteria: Optional[Dict[str, Any]] = Field(
        None, description="可选验证标准"
    )
    # 把目标挂到某个真实存在的项目之下（Projects 面，Top-3 #1 缺口之一）。
    # None 表示独立目标，不属于任何项目。传入不存在的 project_id 会被拒绝。
    project_id: Optional[str] = Field(
        None, description="可选：归属的项目 id（必须已存在，否则 404）"
    )
    # Why this exists: ``create_and_execute_goal`` could always run a goal on a
    # background thread, but nothing over HTTP could ask for it, so the goal
    # always finished inside the POST request. That makes the whole
    # cancellation path unreachable from the product surface: by the time any
    # client could call ``POST /v1/goals/{id}/stop`` the goal was already in a
    # terminal state, and "stop" could only ever be a post-hoc state edit --
    # not the cooperative cancellation the code claims to provide. Default
    # ``False`` keeps the existing synchronous contract untouched.
    background: bool = Field(
        False,
        description=(
            "true: return immediately with state='running' and execute on a "
            "background thread, so GET /v1/goals/{id} can be polled and "
            "POST /v1/goals/{id}/stop can really cancel the run. "
            "false (default): execute synchronously and return the terminal state."
        ),
    )
    # 指名派活：把整个目标钉给某一位真实员工（而不是让执行层自动编号）。
    # 只能指名**自己可见且在职**的员工；不存在/不可见 → 404，停用 → 409。
    employee_name: Optional[str] = Field(
        None, max_length=128,
        description="可选：把这次目标指名交给某位员工执行（其全部子任务都归该员工）",
    )


class GoalSummary(BaseModel):
    """Goal 列表摘要。"""
    goal_id: str
    state: str
    natural_language: str
    scope: str
    created_at: Optional[str] = None
    error: Optional[str] = None
    replan_suggested: bool = False
    task_count: int = 0
    completed_tasks: int = 0
    failed_tasks: int = 0


class GoalDetail(BaseModel):
    """Goal 详情。"""
    goal_id: str
    state: str
    natural_language: str
    scope: str
    correlation_id: str
    error: Optional[str] = None
    replan_count: int = 0
    replan_suggested: bool = False
    trace: List[Dict[str, Any]] = Field(default_factory=list)
    tasks: List[Dict[str, Any]] = Field(default_factory=list)
    # Real workspace files the goal actually produced (workspace-relative paths).
    # Empty list = the goal wrote nothing into the workspace. Fail-closed: a path
    # is recorded only when a file_write task completed and its output path was
    # proven to land inside the workspace root.
    artifacts: List[str] = Field(default_factory=list)
    evaluation: Optional[Dict[str, Any]] = None


class AgentSummary(BaseModel):
    """AI Employee 摘要。"""
    id: str
    agent_type: str
    name: str
    status: str
    current_task: Optional[str] = None
    completed_tasks: int = 0
    failed_tasks: int = 0
    total_latency_ms: float = 0.0


class AgentDetail(BaseModel):
    """AI Employee 详情。"""
    id: str
    agent_type: str
    name: str
    status: str
    current_task: Optional[str] = None
    completed_tasks: int = 0
    failed_tasks: int = 0
    total_latency_ms: float = 0.0
    metadata: Dict[str, Any] = Field(default_factory=dict)
    checkpoint_state: Optional[Dict[str, Any]] = None


class HireEmployeeRequest(BaseModel):
    """按名雇佣一个新 AI Employee 的请求体。"""
    name: str = Field(..., min_length=1, max_length=128,
                      description="员工名（唯一键，全局不可重复）")
    agent_count: int = Field(3, ge=1, le=64, description="agent 数量")
    agent_types: List[str] = Field(
        default_factory=list,
        description="agent 类型列表（长度不足时按末位补齐）",
    )
    role_description: str = Field(
        "", max_length=2000, description="职责描述（这位员工负责什么）"
    )
    default_capabilities: List[str] = Field(
        default_factory=list,
        description="默认能力 id 列表（必须是已注册执行器的 capability）",
    )


class BatchHireEmployeesRequest(BaseModel):
    """批量雇佣：逐条创建，任一条失败不影响其它条目。"""
    employees: List[HireEmployeeRequest] = Field(
        ..., min_length=1, max_length=50,
        description="员工创建条目列表（每条形如单条雇佣请求）",
    )


class AdoptEmployeeRequest(BaseModel):
    """根的"取用"：把子账号员工/技能复制到根自己的空间。"""
    name: str = Field(..., min_length=1, max_length=128, description="要取用的员工名")
    owner: str = Field("", max_length=128, description="来源账号（可选，用于消歧）")
    as_name: str = Field(
        "", max_length=128, description="复制到根空间后的名字（省略则自动生成）"
    )


class EmployeeUpdateRequest(BaseModel):
    """改名 / 改职责 / 停用或启用 / 改默认能力（只作用于自己的员工）。"""
    name: Optional[str] = Field(None, min_length=1, max_length=128,
                                description="新名字（改名时必填）")
    role_description: Optional[str] = Field(None, max_length=2000)
    default_capabilities: Optional[List[str]] = None
    employment_status: Optional[str] = Field(
        None, description="active（在职）/ disabled（停用）"
    )


class AssignTaskRequest(BaseModel):
    """把一件事**直接指派**给某位指定员工（与"一句话自动拆解"分开的入口）。

    执行主体二选一：``agent_id``（agent 级）或 ``employee_name``（员工级，取其
    第一个可用 agent）。两者都不给或都给出 → 400，绝不静默换成别的员工。
    """
    agent_id: Optional[str] = Field(
        None, max_length=128,
        description="执行主体：真实存在的 agent id（与 employee_name 二选一）",
    )
    employee_name: Optional[str] = Field(
        None, max_length=128,
        description="执行主体：员工名（与 agent_id 二选一，取其第一个可用 agent）",
    )
    instruction: str = Field(..., min_length=1, max_length=2000,
                             description="要交给这位员工执行的一句话")
    scope: str = Field("L1", description="执行范围 L0-L7")
    project_id: Optional[str] = Field(
        None, description="可选：归属的项目 id（必须已存在，否则 404）"
    )
    background: bool = Field(
        True,
        description="true: 立即返回 running 并可轮询逐任务进度；false: 同步执行到终态",
    )


class GoalWorkError(Exception):
    """Goal 子操作的可读错误：带 HTTP 状态码与明确错误码。

    ``code`` 是给程序判断的稳定标识（``agent_not_found`` /
    ``agent_unavailable`` / ``task_not_retryable`` ...），``detail`` 是给人看的
    中文说明——两者都不允许笼统成"操作失败"。
    """

    def __init__(self, status_code: int, code: str, detail: str) -> None:
        super().__init__(detail)
        self.status_code = status_code
        self.code = code
        self.detail = detail


class GoalPersistError(GoalWorkError):
    """目标记录**未能写入磁盘**：调用方必须看到失败，绝不能当成成功。"""

    def __init__(self, detail: str) -> None:
        super().__init__(507, "goal_persist_failed", detail)


def _http_from_goal_error(exc: GoalWorkError) -> HTTPException:
    """把 ``GoalWorkError`` 转成稳定的线路格式（code + message）。"""
    return HTTPException(
        status_code=exc.status_code,
        detail={"code": exc.code, "message": exc.detail},
    )


def _verified_principal(
    x_liuhao_token: Optional[str] = Header(None),
    authorization: Optional[str] = Header(None),
) -> str:
    """已校验令牌的 ``sub``（审计与归属用），复用策略模块那**同一道**校验。

    刻意在函数体内导入：``policy`` 会牵连 gateway 包初始化，而本模块需要能被
    独立加载（回归测试就是这么直接加载它的）。依赖声明只需要函数对象，不需要
    执行体，因此延迟导入既安全又保证只有一套令牌校验实现。
    """
    from src.gateway.policy import _human_claims, require_bearer_payload

    payload = require_bearer_payload(x_liuhao_token, authorization)
    subject, _metadata = _human_claims(payload)
    return subject


# ─── 账号归属（任务 / 子任务 / 产物 / 审计） ─────────────────────
#
# Why this exists
# --------------
# 多账号共用一台本机网关时，goal 若是"全局可见"，子账号之间就能互相看到
# 对方的目标、产物与重试结果 —— 这是最容易泄的地方。归属只有**一个**来源：
#
#   1. 账号上下文（``AccountContext.principal``，账号树里的位置）；
#   2. 取不到上下文时退回已核验令牌的 ``sub``；
#   3. 两者都拿不到（内部调用 / 老数据）时落到"本机默认账号"。
#
# 绝不信请求体：谁能决定 owner，就等于谁能把任务塞进别人的列表。
#
# 可见性：不在自己子树内一律 **404**（不是 403）—— 403 等于告诉探测者
# "这个 id 存在"。主账号（树的根）可以**读**整棵树，但写操作仍只作用于
# 自己的目标（只读汇总），所以根账号也不是"随便替人重试"。

#: 显式覆盖本机默认账号名的环境变量（单机部署可用）。
DEFAULT_ACCOUNT_ENV = "LIUHAO_DEFAULT_ACCOUNT_ID"

#: 取不到任何账号信息时的兜底归属（不是提权，只是"本机这份数据归本机"）。
_FALLBACK_ACCOUNT_ID = "local-default"


def default_account_id() -> str:
    """返回"本机默认账号"：显式配置 > 账号树的根 > 固定兜底。

    只在**归属解析**时使用，绝不用于认证：认证永远只来自令牌。
    账号树尚未建立（老部署）时也能返回一个稳定值，使老数据可读、可迁移。
    """
    explicit = (os.environ.get(DEFAULT_ACCOUNT_ENV) or "").strip()
    if explicit:
        return explicit
    try:
        from src.kernels.identity import get_identity_manager

        root = get_identity_manager().account_root()
        if root is not None and root.principal:
            return str(root.principal)
    except Exception:  # noqa: BLE001 - 账号内核不可用不影响本机默认归属
        pass
    return _FALLBACK_ACCOUNT_ID


def owner_of_entry(entry: Optional[Dict[str, Any]]) -> str:
    """取一条 goal/task 记录的 owner；缺失字段按本机默认账号处理，不报错。"""
    if not isinstance(entry, dict):
        return default_account_id()
    raw = str(entry.get("owner_account_id") or "").strip()
    return raw or default_account_id()


def normalise_owner(owner: Optional[str], fallback: Optional[str] = None) -> str:
    """把调用方给的 owner 收敛成一个非空、可持久化的账号名。"""
    candidate = str(owner or "").strip()
    if candidate:
        return candidate
    fallback_value = str(fallback or "").strip()
    if fallback_value:
        return fallback_value
    return default_account_id()


@dataclass(frozen=True)
class GoalViewer:
    """一次请求对 goal 的可见/可写范围（不可变）。"""

    principal: str
    subtree: Tuple[str, ...]
    is_root: bool = False
    standalone: bool = False
    source: str = "context"

    def sees(self, entry: Optional[Dict[str, Any]]) -> bool:
        """读：根看全树；成员只看自己子树；独立令牌只多认"本机默认"老数据。"""
        if not isinstance(entry, dict):
            return False
        owner = owner_of_entry(entry)
        if owner == self.principal:
            return True
        if self.is_root:
            return True
        if owner in self.subtree:
            return True
        # 单机老部署（令牌没有账号节点）读取"没有归属字段"的历史数据：
        # 它按本机默认账号归属，而当时本机只有这一个使用者。账号树一旦建立，
        # 这个分支不再生效（standalone=False），子账号之间仍然互不可见。
        return self.standalone and owner == default_account_id()

    def owns(self, entry: Optional[Dict[str, Any]]) -> bool:
        """写：只允许作用于**自己的**目标（根账号也不例外，只读汇总）。"""
        if not isinstance(entry, dict):
            return False
        owner = owner_of_entry(entry)
        if owner == self.principal:
            return True
        return self.standalone and owner == default_account_id()

    def sees_any_owner(self, owners: Set[str]) -> bool:
        """产物下载：根看全部；其余只看自己（老数据归本机默认账号）。"""
        if not owners:
            return True  # 没有归属记录的文件不是"别人的产物"
        if self.is_root:
            return True
        if self.principal in owners:
            return True
        return self.standalone and any(o == default_account_id() for o in owners)


def viewer_from_payload(payload) -> GoalViewer:
    """从**已核验**令牌构造视角；账号上下文取不到时退回令牌主体本身。

    退回路径只收窄、不放宽：``subtree=(自己,)``、``is_root=False``。
    """
    from src.gateway.policy import _human_claims

    subject, _metadata = _human_claims(payload)
    try:
        from src.gateway.account_context import require_account_principal

        context = require_account_principal(payload)
        return GoalViewer(
            principal=context.principal,
            subtree=tuple(context.subtree) or (subject,),
            # 没有账号节点的老部署被 ``AccountContext`` 视作独立根；对归属而言
            # 它不是"主账号"，不能看到别人的数据。
            is_root=bool(context.is_root and not context.standalone),
            standalone=bool(context.standalone),
            source="account-context",
        )
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001 - 账号内核不可用时退回令牌主体
        logger.warning(
            "goal viewer: account context unavailable, falling back to token subject: %s",
            exc,
        )
        return GoalViewer(
            principal=subject,
            subtree=(subject,),
            is_root=False,
            standalone=True,
            source="token-fallback",
        )


def _goal_viewer(
    x_liuhao_token: Optional[str] = Header(None),
    authorization: Optional[str] = Header(None),
) -> GoalViewer:
    """FastAPI 依赖：经核验令牌 -> 账号视角（懒导入，保持本模块可独立加载）。"""
    from src.gateway.policy import require_bearer_payload

    return viewer_from_payload(require_bearer_payload(x_liuhao_token, authorization))


def _as_viewer(value: Any) -> Optional[GoalViewer]:
    """直接调用端点函数（不经 FastAPI）时，``Depends`` 标记不是真实视角。

    这种调用只出现在内部/回归测试里，没有可用的请求上下文 —— 按"取不到就
    落默认、不做可见性过滤"的老行为处理；HTTP 请求永远拿到真实 ``GoalViewer``。
    """
    return value if isinstance(value, GoalViewer) else None


def _audit_employee_action(
    action: str,
    viewer: Optional[GoalViewer],
    target: str,
    *,
    details: Optional[Dict[str, Any]] = None,
) -> None:
    """员工增删改写入哈希链审计；审计故障不改变业务已生效的事实。"""
    actor = viewer.principal if viewer is not None else ""
    try:
        from src.kernels.identity import get_identity_manager

        get_identity_manager().record_event(
            action=action,
            actor=actor,
            target=target,
            outcome="allowed",
            details=details or None,
        )
    except Exception:  # noqa: BLE001 - 审计不可用不能反向让操作失败
        logger.exception("could not audit %s for employee %r", action, target)


# ─── 跨进程写入锁 ──────────────────────────────────────────────
#
# 为什么需要：``_save_goals`` 是"整份快照写盘"。同进程多线程靠 ``_persist_lock``
# 串行；多 worker / 多进程同时写同一个 goals 文件时，后写者会把先写者的
# 新目标整份覆盖掉（经典 lost update）。这里在**读-改-写**期间持有操作系统
# 级排他锁（Windows ``msvcrt`` / POSIX ``fcntl``），并在写之前把磁盘上已有、
# 内存里没有的条目合并进来 —— 谁的写入都不会消失。

_GOALS_LOCK_TIMEOUT_SECONDS = 15.0

#: 进程级共享锁：覆盖**所有** ``AIStateManager`` 实例（单例被测试重置、
#: 或同进程存在多个历史实例时仍只有一个写者）。Windows 的 ``msvcrt``
#: region lock 是进程级的，同进程内二次加锁会失败，所以进程内必须先串行。
_GOALS_PERSIST_LOCK = threading.Lock()


@contextmanager
def _exclusive_file_lock(
    lock_path: str, timeout: float = _GOALS_LOCK_TIMEOUT_SECONDS
) -> Iterator[None]:
    """对 ``lock_path`` 持有排他锁；超时抛 ``TimeoutError``（不静默续写）。"""
    directory = os.path.dirname(os.path.abspath(lock_path))
    if directory:
        os.makedirs(directory, exist_ok=True)
    handle = open(lock_path, "a+b")
    acquired = False
    try:
        # msvcrt 的 region lock 需要一个可锁的字节；空文件先写一个占位字节。
        handle.seek(0)
        if handle.read(1) == b"":
            handle.write(b"0")
            handle.flush()
        deadline = time.time() + timeout
        while True:
            try:
                handle.seek(0)
                if os.name == "nt":
                    msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                else:  # pragma: no cover - POSIX path
                    fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                acquired = True
                break
            except OSError:
                if time.time() >= deadline:
                    raise TimeoutError(
                        f"goals 文件锁等待超时（{timeout:.0f}s）：{lock_path}"
                    )
                time.sleep(0.02)
        yield
    finally:
        if acquired:
            try:
                handle.seek(0)
                if os.name == "nt":
                    msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
                else:  # pragma: no cover - POSIX path
                    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
            except OSError:
                pass
        handle.close()


class EmployeeSummary(BaseModel):
    """按名列出员工时的单条摘要（真实的员工实体，不只是 agent 编号）。"""
    name: str
    agent_count: int
    agents: List[Dict[str, Any]] = Field(default_factory=list)
    stats: Dict[str, Any] = Field(default_factory=dict)
    # 员工实体字段（本阶段新增：可见的"员工"而不只是任务执行单元）
    role_description: str = ""
    default_capabilities: List[str] = Field(default_factory=list)
    owner_account_id: str = ""
    employment_status: str = "active"
    read_only: bool = False


# ─── 状态管理器（单例） ──────────────────────────────────────

class AIStateManager:
    """Goal / Employee 状态管理单例。

    线程安全，供 FastAPI 端点调用。
    - ``_runtime`` 是真实的 AgentRuntime，调用 ``run_goal`` 走完整链；
    - ``_employee`` 是真实的 Employee 实例，agent pool 可查可操作；
    - ``_goals`` 存储已执行的 AgentRunResult，供 GET 查询。
    """

    _instance: Optional["AIStateManager"] = None
    _lock = threading.Lock()

    def __new__(cls) -> "AIStateManager":
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
                    cls._instance._initialized = False
        return cls._instance

    def __init__(self) -> None:
        if self._initialized:
            return
        self._initialized = True
        self._goals: Dict[str, Dict[str, Any]] = {}
        self._goal_threads: Dict[str, threading.Thread] = {}
        self._runtime = None
        self._employee = None
        self._employee_store = None
        # 已注册执行器的能力目录（懒加载）；以及 agent id -> 员工名 的映射缓存，
        # 供"结果里能看见哪个员工做了什么"使用（员工增删改时失效）。
        self._capability_catalog_cache: Optional[List[Dict[str, Any]]] = None
        self._agent_map_cache: Optional[Dict[str, str]] = None
        self._employee_pool_lock = threading.RLock()
        self._shared_capability_executor = None
        self._goal_lock = threading.Lock()
        # 并发目标：记录仍在执行的 goal id。只有最后一个结束的目标才能把
        # 员工 agent 标回 idle，否则一个目标收尾会把另一个目标的 agent 说成空闲。
        self._active_goals: Set[str] = set()
        # 员工级在跑目标：employee stable id -> {goal_id}。
        # 指名派活（pinned）是**独占**的：同一员工已有在跑目标时，第二次
        # 派活直接 409 employee_busy —— 行为固定为"拒绝"，不会两个目标
        # 同时写同一员工的状态与结果（那正是丢任务/串结果的来源）。
        self._employee_active: Dict[str, Set[str]] = {}
        # 注意：这里不能叫 ``_goal_employee``，否则会遮蔽同名方法。
        self._goal_employee_map: Dict[str, str] = {}
        self._employee_runs_lock = threading.Lock()
        # ``_ensure_runtime`` / ``_ensure_employee`` 是懒初始化；并发首次目标
        # 若同时穿过 ``is None`` 会各自建一份 runtime（两份 journal 句柄）。
        self._runtime_lock = threading.RLock()
        # Serialises whole-file goals writes inside this process; the OS file
        # lock inside ``_save_goals`` covers other processes/workers.
        self._persist_lock = threading.Lock()
        # 固定本实例的持久化路径：工作区根在这次进程生命周期内不应该"漂移"，
        # 否则旧实例会把旧工作区的目标写进新工作区（测试里切换环境变量时
        # 会真实发生这种串写）。
        self._goals_path = self._default_goals_path()
        # Terminal goals move out of the hot file into archived shards. The
        # active map is capped so both the write cost and the resident memory of
        # the goal store stay flat however long the process runs (soak: 0.17 ->
        # 4.96 MB in 2h, +2.42 MB/h, and every save rewrote the whole file).
        self._archived_summaries: Dict[str, Dict[str, Any]] = {}
        self._archived_summaries_loaded = False
        # Recover any goals persisted across a previous process restart.
        self._load_goals()
        if len(self._goals) > self._goals_active_limit():
            # Upgrade path: an instance that ran before archiving existed reloads
            # a huge file. Compact it once so resident memory starts bounded.
            try:
                self._save_goals()
            except Exception as exc:  # noqa: BLE001 - startup must still proceed
                logger.warning(
                    "AIStateManager: startup goal compaction skipped: %s", exc
                )

    def _ensure_runtime(self):
        """懒初始化 AgentRuntime（延迟到首次使用，避免 import 时副作用）。

        加锁的原因：两个目标几乎同时到达时都会看到 ``_runtime is None``，
        各自构造一份 runtime，于是同一进程里出现两份 journal 句柄、两套
        agent pool。锁保证**只建一个**，后来者复用同一个实例。
        """
        with self._runtime_lock:
            return self._ensure_runtime_locked()

    def _ensure_runtime_locked(self):
        if self._runtime is None:
            try:
                from src.ai.agent_runtime import AgentRuntime
                from src.ai.lcore import LCore
                from src.kernels.execution._journal import ExecutionJournal
                # Wire the REAL local-tool executor (RestrictedPython compute +
                # workspace-contained file writes) so goals actually execute
                # instead of taking the simulated path. Fail-closed: with no
                # executor the Execution Kernel refuses (never reports success
                # for work it did not do).
                _lcore = LCore(scope="L1", register_local_tools=True)
                # Crash recovery: attach a durable, append-only journal so a
                # goal mid-flight when the process died can resume already-
                # completed tasks on restart (journal keyed by goal id). Path is
                # REDIR-able via LIUHAO_WORKSPACE_ROOT so isolation verifiers
                # never touch the real workspace.
                root = os.environ.get("LIUHAO_WORKSPACE_ROOT")
                if root:
                    journal_path = os.path.join(root, "liuhao_execution.journal")
                else:
                    journal_path = os.path.join(
                        os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
                        ".liuhao_execution.journal",
                    )
                journal = ExecutionJournal(journal_path)
                # 产物索引（旁路观测）：构造失败不阻塞执行链，退回原行为。
                try:
                    from src.artifacts.index import ArtifactIndex, default_index_path

                    artifact_index = ArtifactIndex(default_index_path())
                except Exception as exc:  # noqa: BLE001
                    logger.warning("AIStateManager: artifact index disabled: %s", exc)
                    artifact_index = None
                self._runtime = AgentRuntime(
                    scope="L1",
                    capability_executor=_lcore.capability_executor(),
                    executable_capabilities=_lcore.executable_capabilities(),
                    journal=journal,
                    artifact_index=artifact_index,
                    # Model-first decomposition + summary synthesis. Disable via
                    # LIUHAO_BUTLER_MODEL=0 for hermetic tests/offline runs.
                    enable_model_planning=_butler_model_planning_enabled(),
                )
                logger.info("AIStateManager: AgentRuntime initialized (real executor wired, journal=%s)", journal_path)
                # Inject the real Employee's agent ids so the deterministic
                # decomposer assigns a goal's tasks to distinct REAL agents
                # (multi-agent observability for P3). Fail-soft: if the employee
                # is not yet available the decomposer falls back to synthetic ids.
                try:
                    self._ensure_employee()
                    # ARM1: injecting an explicit pool is a CLAIM about who
                    # performs the work, so it must be substantiable. Without
                    # a resolver the executor marks every action
                    # ``agent_attribution == "unattributed"`` (task status
                    # ``completed_unattributed``) even though the work really
                    # ran -- a permanent false attribution failure. Every
                    # active employee's agents get a resolver bound to the
                    # local capability registry, so a NAMED employee's tasks
                    # are genuinely credited to it. Ids outside the pool stay
                    # unresolvable (fail-closed).
                    self._shared_capability_executor = _lcore.capability_executor()
                    self._refresh_agent_pool()
                except Exception as _exc:  # pragma: no cover - best effort
                    logger.debug("AIStateManager: agent id injection skipped: %s", _exc)
            except Exception as exc:
                logger.error("AIStateManager: failed to init AgentRuntime: %s", exc)
                raise
        return self._runtime

    # ── Employee persistence (restart recovery) ──

    def _ensure_employee(self):
        """懒初始化 Employee（真实 agent pool）。

        首次运行（store 为空）按默认种子配置创建一个 Employee 并落盘；后续运行
        从 EmployeeStore 加载已持久化的 Employee，使 agent/task 状态在进程重启后
        仍可恢复（此前全部为内存态，重启即丢）。
        """
        with self._runtime_lock:
            return self._ensure_employee_locked()

    def _ensure_employee_locked(self):
        if self._employee is None:
            try:
                from src.ai.employee_store import EmployeeStore
                self._employee_store = EmployeeStore()
                self._employee = self._employee_store.create_employee(
                    name=self._SEED_DEFAULT_NAME,
                    agent_count=3,
                    agent_types=["planner", "executor", "critic"],
                    role_description="本机默认管家员工：拆解目标并调度本地能力",
                    owner_account_id=default_account_id(),
                )
                logger.info("AIStateManager: Employee ready with %d agents (store-backed)",
                            len(self._employee.agents))
            except Exception as exc:
                logger.error("AIStateManager: failed to init Employee: %s", exc)
                raise
        return self._employee

    def _persist_employee(self) -> None:
        """把当前 Employee（agent/task 状态）落盘；失败**向上抛**。

        以前这里吞掉异常只打 warning：雇佣/暂停/目标计数明明没落盘，接口
        却照样返回成功，重启后状态凭空消失。现在失败如实上报，调用方要么
        让请求失败，要么把错误写进响应，不允许静默成功。
        """
        if self._employee_store is not None and self._employee is not None:
            try:
                self._employee_store.save(self._employee)
            except Exception as exc:
                logger.error(
                    "AIStateManager: employee persist failed: %s", exc
                )
                raise

    def _save_employee(self, employee: Optional["Employee"]) -> None:
        """把**任意**员工（默认或按名）落盘；失败向上抛。

        指名派活时被更新的不是 ``self._employee``（默认管家），而是被点名
        的那位员工；以前这条路径只更新内存对象、从不落盘，重启后它的
        KPI/状态全部归零。这里统一走 store 的跨进程安全写入。
        """
        if employee is None:
            return
        self._store().save(employee)

    def _employee_for_agent(self, agent_id: Optional[str]) -> Optional["Employee"]:
        """agent id → 所属员工（默认员工优先用内存实例，其余按名从 store 加载）。"""
        if not agent_id:
            return None
        employee, agent = self._find_employee_with_agent(str(agent_id))
        if agent is None:
            return None
        return employee

    def _goal_employee(self, pinned_agent: Optional[str]) -> "Employee":
        """本次目标实际归属/驱动的员工：有指名就用被点名的，否则默认管家。"""
        target = self._employee_for_agent(pinned_agent)
        return target if target is not None else self._ensure_employee()

    def _claim_employee_goal(
        self, employee_key: str, goal_id: str, *, exclusive: bool
    ) -> None:
        """登记"某员工正在跑某个目标"；独占模式下已有目标就拒绝。

        行为固定为**拒绝**（不是排队）：同一员工同时只接受一个点名派活，
        第二次请求拿到 409 ``employee_busy``，不会出现两个执行体同时改同一份
        员工状态/结果。
        """
        with self._employee_runs_lock:
            active = self._employee_active.setdefault(employee_key, set())
            if exclusive and active:
                raise GoalWorkError(
                    409,
                    "employee_busy",
                    f"员工「{employee_key}」正在执行另一项任务，"
                    "请等它完成后再派；本次没有排队，也没有执行。",
                )
            active.add(goal_id)
            self._goal_employee_map[goal_id] = employee_key

    def _release_employee_goal(self, goal_id: str) -> bool:
        """释放目标占用的员工；返回该员工是否已无在跑目标。"""
        with self._employee_runs_lock:
            key = self._goal_employee_map.pop(goal_id, None)
            if key is None:
                return False
            active = self._employee_active.get(key)
            if active is not None:
                active.discard(goal_id)
                if not active:
                    self._employee_active.pop(key, None)
            return not active

    def _employee_memory_note(
        self, employee: Optional["Employee"], entry: Dict[str, Any]
    ) -> None:
        """把"员工完成/失败了一次目标"记进**员工所属账号**的记忆。

        归属口径：``owner`` = 员工所属账号（不是发起人）。主账号可见子树全部，
        子账号只能看到自己名下员工产生的记忆。记忆是旁路记录，失败不拖垮目标。
        """
        if employee is None or not isinstance(entry, dict):
            return
        try:
            from .account_memory import employee_tag, record_entry_for_owner

            state = str(entry.get("state") or "").lower()
            tasks = [t for t in (entry.get("tasks") or []) if isinstance(t, dict)]
            completed = sum(1 for task in tasks if task.get("success"))
            failed = sum(
                1
                for task in tasks
                if str(task.get("status") or "").lower() in ("failed", "interrupted")
            )
            description = str(entry.get("natural_language") or "").strip()[:300]
            headline = "已完成" if state == "completed" else "执行未成功"
            text = f"{headline}：{description}" if description else f"{headline}一次任务"
            if tasks:
                text += f"（任务 {len(tasks)} 项，成功 {completed}，失败 {failed}）"
            record_entry_for_owner(
                self._employee_owner(employee),
                text,
                source="employee",
                tags=[
                    "employee",
                    employee_tag(employee.stable_id()),
                    f"goal:{entry.get('goal_id')}",
                    f"status:{state or 'unknown'}",
                ],
            )
        except Exception as exc:  # noqa: BLE001 - 记忆写入失败不影响目标结果
            logger.warning("AIStateManager: employee memory note failed: %s", exc)


    # ── Goal persistence (restart recovery) ──

    @staticmethod
    def _default_goals_path() -> str:
        """Default location of the persisted goals dict.

        ``LIUHAO_WORKSPACE_ROOT/liuhao_goals.json`` when set (REDIR-able for
        isolation verifiers), else ``<repo_root>/.liuhao_goals.json``.
        """
        root = os.environ.get("LIUHAO_WORKSPACE_ROOT")
        if root:
            return os.path.join(root, "liuhao_goals.json")
        repo_root = os.path.dirname(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        )
        return os.path.join(repo_root, ".liuhao_goals.json")

    # ── Goal archive (bounded hot set + append-only shards) ──

    _TERMINAL_GOAL_STATES = frozenset({
        "completed", "completed_unattributed", "failed", "cancelled",
        "interrupted", "stopped", "error",
    })

    def _goals_active_limit(self) -> int:
        """Hot-set cap. Env override for operators/tests; never below 16."""
        raw = os.environ.get("LIUHAO_GOALS_MAX_ACTIVE", "128")
        try:
            value = int(raw)
        except (TypeError, ValueError):
            value = 128
        return max(16, value)

    def _goals_archive_paths(self, day: Optional[str] = None) -> Tuple[str, str]:
        day = day or time.strftime("%Y%m%d", time.localtime())
        directory = os.path.dirname(os.path.abspath(self._goals_path))
        stem = os.path.basename(self._goals_path)
        full = os.path.join(directory, f"{stem}.archive-{day}.jsonl")
        index = os.path.join(directory, f"{stem}.archive-index-{day}.jsonl")
        return full, index

    @staticmethod
    def _archive_shard_files(base: str) -> List[str]:
        """All archive shard files of the SAME FAMILY as ``base``.

        ``base`` may be today's full-entry shard (``x.jsonl.archive-YYYYMMDD.jsonl``)
        or today's index shard (``x.jsonl.archive-index-YYYYMMDD.jsonl``). The
        family prefix must strip the date: otherwise a reader would only ever
        see TODAY's shard and every older day would silently vanish from the
        list/detail paths. Index and full families are kept apart because a
        full-file prefix would otherwise also match ``*.archive-index-*``.
        """
        name = os.path.basename(base)
        is_index = ".archive-index-" in name
        if is_index:
            prefix = name[:name.index(".archive-index-")] + ".archive-index-"
        else:
            prefix = name[:name.index(".archive-")] + ".archive-"
        directory = os.path.dirname(os.path.abspath(base))
        if not os.path.isdir(directory):
            return []
        found = []
        for candidate in sorted(os.listdir(directory)):
            if not candidate.startswith(prefix):
                continue
            if not (candidate.endswith(".jsonl")
                    or candidate.endswith(".jsonl.gz")):
                continue
            if is_index and ".archive-index-" not in candidate:
                continue
            if not is_index and ".archive-index-" in candidate:
                continue
            found.append(os.path.join(directory, candidate))
        return found

    @staticmethod
    def _write_archive_lines(path: str, lines: List[str]) -> None:
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        with open(path, "a", encoding="utf-8") as fh:
            for line in lines:
                fh.write(line)
                fh.write("\n")
            fh.flush()
            os.fsync(fh.fileno())

    def _append_goal_archive(
        self, entries: List[Dict[str, Any]], summaries: List[Dict[str, Any]]
    ) -> None:
        """Append full entries + list summaries to today's shard, durability first."""
        day = time.strftime("%Y%m%d", time.localtime())
        full_path, index_path = self._goals_archive_paths(day)
        full_lines = [
            json.dumps(entry, ensure_ascii=False, separators=(",", ":"))
            for entry in entries
        ]
        index_lines = [
            json.dumps(summary, ensure_ascii=False, separators=(",", ":"))
            for summary in summaries
        ]
        # Full entries first: if the process dies before the index lands, list()
        # can rebuild the summary from the entry; the reverse would lose detail.
        self._write_archive_lines(full_path, full_lines)
        self._write_archive_lines(index_path, index_lines)
        self._compress_stale_archive_shards()

    def _archive_shard_limit_bytes(self) -> int:
        raw = os.environ.get("LIUHAO_GOALS_ARCHIVE_SHARD_BYTES", "33554432")
        try:
            return max(1048576, int(raw))
        except (TypeError, ValueError):
            return 33554432

    def _compress_stale_archive_shards(self) -> None:
        """Gzip yesterday's shards (and oversized current shards).

        Shards are append-only, so compression is safe: nothing reads a shard
        for writing, and readers transparently open ``*.jsonl.gz``.
        """
        today = time.strftime("%Y%m%d", time.localtime())
        limit = self._archive_shard_limit_bytes()
        for base in self._goals_archive_paths():
            for path in self._archive_shard_files(base):
                if path.endswith(".gz"):
                    continue
                try:
                    stale = f"-{today}." not in os.path.basename(path)
                    oversized = os.path.getsize(path) >= limit
                    if not (stale or oversized):
                        continue
                    target = path + ".gz"
                    with open(path, "rb") as src, gzip.open(target, "wb") as dst:
                        shutil.copyfileobj(src, dst)
                    os.unlink(path)
                except Exception as exc:  # noqa: BLE001 - compression is best effort
                    logger.warning(
                        "AIStateManager: archive shard compression failed for %s: %s",
                        path, exc,
                    )

    @staticmethod
    def _read_archive_lines(path: str) -> Iterator[str]:
        opener = gzip.open if path.endswith(".gz") else open
        try:
            with opener(path, "rt", encoding="utf-8") as fh:
                for line in fh:
                    line = line.strip()
                    if line:
                        yield line
        except (OSError, UnicodeError) as exc:
            logger.warning("AIStateManager: unreadable archive shard %s: %s", path, exc)

    def _iter_archived_entries(self) -> Iterator[Dict[str, Any]]:
        base = self._goals_archive_paths()[0]
        for path in self._archive_shard_files(base):
            for line in self._read_archive_lines(path):
                try:
                    entry = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(entry, dict) and entry.get("goal_id"):
                    yield entry

    def _load_archived_summaries(self) -> Dict[str, Dict[str, Any]]:
        """Load the small list-view index lazily; one scan per process."""
        if self._archived_summaries_loaded:
            return self._archived_summaries
        base = self._goals_archive_paths()[1]
        files = self._archive_shard_files(base)
        if files:
            for path in files:
                for line in self._read_archive_lines(path):
                    try:
                        summary = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if isinstance(summary, dict) and summary.get("goal_id"):
                        self._archived_summaries.setdefault(
                            str(summary["goal_id"]), summary
                        )
        else:
            # Index shard missing (partial write / old data): rebuild it from
            # the durable full-entry shards instead of losing archived goals
            # from the list.
            for entry in self._iter_archived_entries():
                summary = self._goal_summary(entry)
                self._archived_summaries.setdefault(str(entry["goal_id"]), summary)
        self._archived_summaries_loaded = True
        return self._archived_summaries

    def _find_archived_goal(self, goal_id: str) -> Optional[Dict[str, Any]]:
        """Full-detail lookup for a goal that has left the hot set."""
        found = None
        for entry in self._iter_archived_entries():
            if str(entry.get("goal_id")) == goal_id:
                found = entry  # keep the newest occurrence
        return found

    def _lookup_goal(self, goal_id: str) -> Optional[Dict[str, Any]]:
        with self._goal_lock:
            item = self._goals.get(goal_id)
        if item is not None:
            return item
        return self._find_archived_goal(goal_id)

    def _select_archive_candidates(self,
                                   snapshot: Dict[str, Dict[str, Any]]) -> List[str]:
        """Which terminal goals fall outside the hot-set budget (oldest first)."""
        limit = self._goals_active_limit()
        terminal: List[Tuple[str, Dict[str, Any]]] = []
        for goal_id, entry in snapshot.items():
            if not isinstance(entry, dict):
                continue
            if str(entry.get("state") or "").lower() in self._TERMINAL_GOAL_STATES:
                terminal.append((goal_id, entry))
        non_terminal = len(snapshot) - len(terminal)
        budget = max(limit - non_terminal, 0)
        if len(terminal) <= budget:
            return []

        def _created(item: Dict[str, Any]) -> float:
            value = item.get("created_at")
            return float(value) if isinstance(value, (int, float)) else 0.0

        terminal.sort(key=lambda pair: _created(pair[1]), reverse=True)
        return [goal_id for goal_id, _ in terminal[budget:]]

    def _goal_summary(self, item: Dict[str, Any]) -> Dict[str, Any]:
        """List-view record for one goal (also persisted in the archive index)."""
        self._attach_employee_attribution(item)
        tasks = item.get("tasks", [])
        completed = sum(
            1 for t in tasks
            if t.get("status") in ("completed", "completed_unattributed")
        )
        failed = sum(1 for t in tasks if t.get("status") == "failed")
        return {
            "goal_id": item["goal_id"],
            "state": item["state"],
            "natural_language": item.get("natural_language", ""),
            "scope": item.get("scope", ""),
            "created_at": item.get("created_at"),
            "error": item.get("error"),
            "replan_suggested": item.get("replan_suggested", False),
            "task_count": len(tasks),
            "completed_tasks": completed,
            "failed_tasks": failed,
            "project_id": item.get("project_id"),
            "owner_account_id": owner_of_entry(item),
            # 归档索引也保留产物路径：项目视图在目标归档后仍要能回答
            # "产出在哪里"，而不是让历史产出一归档就消失。
            "artifacts": [
                path for path in (item.get("artifacts") or [])
                if isinstance(path, str) and path.strip()
            ],
            "assigned_employee": item.get("assigned_employee"),
            "assigned_employees": item.get("assigned_employees", []),
            "employees": item.get("employees", []),
            "archived": True,
        }


    def _save_goals(self) -> None:
        """Persist ``_goals`` to JSON so executed-goal history survives restart.

        写入是**读-改-写**，不是"整份内存快照覆盖"：

        1. 同进程多线程先过 ``_persist_lock``（线程级排他）；
        2. 再拿操作系统文件锁（多 worker / 多进程排他）；
        3. 在锁内读回磁盘现状，把"磁盘有、内存没有"的目标并进来，然后原子
           替换写盘。

        第 3 步是防丢写的关键：否则两个账号并发提交时，后完成的那次保存会用
        自己较早的快照把对方刚写入的目标抹掉。合并时同一个 goal id 以内存为准
        （该目标由本进程执行，磁盘上的旧版本只会更旧）。
        """
        path = self._goals_path
        tmp_path = None
        try:
            directory = os.path.dirname(os.path.abspath(path))
            if directory:
                os.makedirs(directory, exist_ok=True)
            with _GOALS_PERSIST_LOCK:
                with self._persist_lock:
                    with _exclusive_file_lock(f"{path}.lock"):
                        with self._goal_lock:
                            snapshot = copy.deepcopy(self._goals)
                        # Move finished goals out of the hot file before writing.
                        # Archive first (fsync), then drop them from memory; if
                        # the archive append fails we keep them in the hot set
                        # rather than lose history.
                        archive_ids = self._select_archive_candidates(snapshot)
                        archived_entries: List[Dict[str, Any]] = []
                        archived_summaries: List[Dict[str, Any]] = []
                        for gid in archive_ids:
                            entry = snapshot.get(gid)
                            if not isinstance(entry, dict):
                                continue
                            try:
                                archived_summaries.append(self._goal_summary(dict(entry)))
                                archived_entries.append(entry)
                            except Exception as summary_exc:  # noqa: BLE001
                                logger.warning(
                                    "AIStateManager: cannot summarize goal %s for archive: %s",
                                    gid, summary_exc,
                                )
                        if archived_entries:
                            try:
                                self._append_goal_archive(
                                    archived_entries, archived_summaries
                                )
                            except Exception as archive_exc:  # noqa: BLE001
                                logger.warning(
                                    "AIStateManager: goal archive append failed; "
                                    "keeping goals in the hot set: %s", archive_exc,
                                )
                                archived_entries = []
                                archived_summaries = []
                            else:
                                archived_ids = {
                                    str(entry.get("goal_id")) for entry in archived_entries
                                }
                                with self._goal_lock:
                                    for gid in archived_ids:
                                        live = self._goals.get(gid)
                                        if live is None:
                                            continue
                                        if str(live.get("state") or "").lower() in (
                                                self._TERMINAL_GOAL_STATES):
                                            self._goals.pop(gid, None)
                                    snapshot = copy.deepcopy(self._goals)
                                for gid in archived_ids:
                                    self._goal_threads.pop(gid, None)
                                if not self._archived_summaries_loaded:
                                    self._load_archived_summaries()
                                for summary in archived_summaries:
                                    self._archived_summaries[
                                        str(summary["goal_id"])] = summary
                        disk = self._read_goals_file(path)
                        for gid in {
                            str(entry.get("goal_id")) for entry in archived_entries
                        }:
                            disk.pop(gid, None)
                        merged = self._merge_goal_maps(disk, snapshot)
                        # Serialize each entry exactly once. The old code ran
                        # json.dumps per entry for validation and then json.dump
                        # of the whole map with indent=2 -- two full
                        # serializations, twice the bytes, on every save.
                        parts: List[str] = []
                        for gid, entry in merged.items():
                            try:
                                payload = json.dumps(
                                    entry, ensure_ascii=False, separators=(",", ":")
                                )
                            except (TypeError, ValueError, OverflowError) as exc:
                                # 序列化失败绝不能用一个字符串占位覆盖结构化数据：
                                # 那会把磁盘上原本可用的目标永久毁掉。保留磁盘原值；
                                # 磁盘没有这条记录时跳过（宁可不写，也不写假数据）。
                                if gid in disk:
                                    try:
                                        payload = json.dumps(
                                            disk[gid], ensure_ascii=False,
                                            separators=(",", ":"),
                                        )
                                    except (TypeError, ValueError, OverflowError):
                                        payload = None
                                else:
                                    payload = None
                                logger.warning(
                                    "AIStateManager: goal %s is not JSON-serialisable (%s); "
                                    "keeping the on-disk copy and skipping this entry",
                                    gid,
                                    exc,
                                )
                            if payload is not None:
                                parts.append(
                                    json.dumps(gid, ensure_ascii=False) + ":" + payload
                                )
                        tmp_path = f"{path}.{uuid.uuid4().hex}.tmp"
                        with open(tmp_path, "w", encoding="utf-8") as fh:
                            fh.write("{" + ",".join(parts) + "}")
                            fh.flush()
                            os.fsync(fh.fileno())
                        os.replace(tmp_path, path)
                        # 其它进程刚写入、本进程内存里还没有的目标：纳入可见范围，
                        # 否则同机多 worker 时列表会"少一半"。内存里已有的目标仍以
                        # 内存版本为准（它可能正在执行）。
                        with self._goal_lock:
                            for gid, entry in merged.items():
                                if gid not in self._goals:
                                    self._goals[gid] = entry
        except Exception as exc:  # persistence must never be reported as success
            # error, not warning: a silent persistence failure is how goal
            # history quietly disappears across restarts.
            logger.error("AIStateManager: goals persist failed (%s): %s", path, exc)
            if tmp_path:
                try:
                    os.unlink(tmp_path)
                except OSError:
                    pass
            # 调用方必须看到失败：接口不能返回 200 running 让用户以为已保存。
            raise GoalPersistError(
                "目标记录无法写入本机磁盘，本次状态尚未持久化。"
                "请检查磁盘空间后重试。"
            ) from exc

    @staticmethod
    def _read_goals_file(path: str) -> Dict[str, Any]:
        """读回磁盘上的 goals 字典；读不到/损坏时返回空字典（不阻断写入）。"""
        if not os.path.exists(path):
            return {}
        try:
            with open(path, "r", encoding="utf-8") as fh:
                data = json.load(fh)
            return data if isinstance(data, dict) else {}
        except Exception as exc:  # noqa: BLE001 - 损坏文件不能阻塞新写入
            logger.warning("AIStateManager: goals read-merge skipped: %s", exc)
            return {}

    @staticmethod
    def _merge_goal_maps(
        disk: Dict[str, Any], memory: Dict[str, Any]
    ) -> Dict[str, Any]:
        """磁盘 ∪ 内存；同一 goal id 以内存为准（避免覆盖正在执行的状态）。"""
        merged: Dict[str, Any] = dict(disk)
        merged.update(memory)
        return merged

    @staticmethod
    def _touch(entry: Dict[str, Any]) -> None:
        """记录最后变更时间（合并/展示用，不参与业务判定）。"""
        entry["updated_at"] = time.time()

    @staticmethod
    def _stamp_owner(
        entry: Dict[str, Any], owner: Optional[str] = None
    ) -> Dict[str, Any]:
        """给 goal 及其每个子任务写入 ``owner_account_id``（已存在则不覆盖）。

        子任务单独带归属，是为了"只重试自己的任务"能对任何一层判定，而不依赖
        调用方回看 goal。
        """
        resolved = normalise_owner(owner, fallback=entry.get("owner_account_id"))
        entry["owner_account_id"] = resolved
        tasks = entry.get("tasks")
        if isinstance(tasks, list):
            for task in tasks:
                if isinstance(task, dict):
                    task["owner_account_id"] = (
                        str(task.get("owner_account_id") or "").strip() or resolved
                    )
        # 产物逐条带归属（路径 -> 账号）：下载判定与审计不必回看 goal，
        # 也让"产物属于谁"在持久化数据里是显式事实而不是推断。
        artifacts = entry.get("artifacts")
        if isinstance(artifacts, list):
            entry["artifact_owners"] = {
                str(path).replace("\\", "/").strip().strip("/"): resolved
                for path in artifacts
                if isinstance(path, str) and path
            }
        return entry

    def _backfill_goal_owners(self) -> int:
        """老数据兼容：给没有 ``owner_account_id`` 的 goal/task 补上默认归属。

        老数据的归属按"本机默认账号"处理，而不是报错或丢弃。返回补写的记录数。
        """
        default_owner = default_account_id()
        changed = 0
        with self._goal_lock:
            for entry in self._goals.values():
                if not isinstance(entry, dict):
                    continue
                if not str(entry.get("owner_account_id") or "").strip():
                    entry["owner_account_id"] = default_owner
                    entry["owner_source"] = "legacy-default"
                    self._touch(entry)
                    changed += 1
                owner = owner_of_entry(entry)
                tasks = entry.get("tasks")
                if isinstance(tasks, list):
                    for task in tasks:
                        if not isinstance(task, dict):
                            continue
                        if not str(task.get("owner_account_id") or "").strip():
                            task["owner_account_id"] = owner
                            changed += 1
                if entry.get("artifacts") and "artifact_owners" not in entry:
                    self._stamp_owner(entry, owner)
                    changed += 1
        if changed:
            try:
                self._save_goals()
            except GoalPersistError as exc:
                # 启动期补归属失败不能拖垮整个进程；但必须留下明确日志。
                logger.error("AIStateManager: backfill goals persist failed: %s", exc)
        return changed

    def _load_goals(self) -> None:
        """Reload ``_goals`` from the persisted JSON (no-op if absent).

        加载顺序：读盘 → 老数据补归属 → 崩溃收敛。上一次进程留下的 ``running``
        目标绝不允许永久挂在 running，也**绝不自动重跑**（同一 goal 因重启被
        执行两次会重复产生副作用）。
        """
        path = self._goals_path
        if not os.path.exists(path):
            return
        try:
            with open(path, "r", encoding="utf-8") as fh:
                data = json.load(fh)
            if isinstance(data, dict):
                self._goals = data
        except Exception as exc:
            logger.warning("AIStateManager: goals load failed: %s", exc)
            return
        self._backfill_goal_owners()
        self._reconcile_interrupted_goals()

    def _reconcile_interrupted_goals(self, owner: Optional[str] = None) -> int:
        """把进程中断留下的 ``running`` 目标收敛为 ``interrupted``。

        * 有任务停在 ``running``（进程在任务执行中退出）→ 任务改为
          ``interrupted`` 并给出可读原因：结果未知、可单独重试。
        * 其余 ``pending`` 任务保持排队（它们从未执行，没有可疑产物）。
        * 目标级 ``error`` 说明"中断前未结束、未自动重跑"，供界面直接展示。
        * ``owner``：只处理该账号的目标（重启时传 None 表示系统级全量收敛；
          用户触发的恢复/重试必须传自己的账号，不能动别人的）。
        * 返回收敛的目标数。幂等：再次重启时状态已是 ``interrupted``，不做改动。
        """
        changed = 0
        now = time.time()
        normalised = str(owner or "").strip()
        with self._goal_lock:
            for goal_id, entry in list(self._goals.items()):
                if not isinstance(entry, dict):
                    continue
                if normalised and owner_of_entry(entry) != normalised:
                    continue
                if str(entry.get("state") or "").lower() != "running":
                    continue
                entry["state"] = "interrupted"
                entry["interrupted_at"] = now
                self._touch(entry)
                entry["error"] = (
                    "目标在进程中断前仍未结束（上次状态 running）；重启后已标记为 "
                    "interrupted，未自动重跑。可对受影响的子任务单独重试。"
                )
                tasks = entry.get("tasks")
                if isinstance(tasks, list):
                    for task in tasks:
                        if not isinstance(task, dict):
                            continue
                        if str(task.get("status") or "").lower() != "running":
                            continue
                        task["status"] = "interrupted"
                        task["completed_at"] = now
                        task["error"] = (
                            "进程中断时该任务仍在执行，结果未知（未记录完成）；"
                            "可单独重试，重试会重新执行该任务并校验产物。"
                        )
                    # 崩溃前最后一次进度快照只更新任务卡，产物列表可能是空的。
                    # 按"已完成文件写入任务 + 文件确实在磁盘上"重算，界面看到
                    # 的真实产物才不会因为进程被杀而凭空消失。
                    entry["artifacts"] = self._collect_artifacts_from_cards(tasks)
                    self._stamp_owner(entry, owner_of_entry(entry))
                changed += 1
        if changed:
            try:
                self._save_goals()
            except GoalPersistError as exc:
                logger.error("AIStateManager: reconcile persist failed: %s", exc)
            logger.warning(
                "AIStateManager: reconciled %d interrupted goal(s)%s",
                changed,
                f" for owner={normalised}" if normalised else " from a previous process",
            )
        return changed

    # ── Goal 操作 ──

    def create_and_execute_goal(
        self,
        natural_language: str,
        scope: str = "L1",
        plan_mode: str = "auto",
        verification_criteria: Optional[Dict[str, Any]] = None,
        background: bool = False,
        project_id: Optional[str] = None,
        pinned_agent: Optional[str] = None,
        owner_account_id: str = "",
    ) -> Dict[str, Any]:
        """创建并执行一个 Goal，返回执行结果。

        - 真实绑定到默认 Employee（``liuhao-default``）：提交时记 goal 归属、
          把该员工的 agent 标 BUSY，结束时标回 IDLE 并汇总 goal 计数，使
          roster / KPI 反映真实执行。
        - ``owner_account_id``：提交者的账号（由端点从**已核验**上下文取得）。
          传空则落"本机默认账号"（内部调用 / 单机老部署），不报错。
        - ``background=False``（默认，保持现有契约）：同步跑完，返回
          completed/failed 终态 —— 现有 REST 端点与测试依赖此行为。
        - ``background=True``：在后台线程跑，立即返回 ``state:"running"``，
          供自主工作者场景与取消演示使用（``POST /v1/goals/{id}/stop`` 可干净中止）。
        """
        # Fail fast on a project that is not visible to the submitting account.
        # 挂目标必须校验目标项目属于调用者可见范围：成员不能把自己的目标
        # 挂到主账号（或同级账号）的项目上；越权与不存在同为中性文案。
        if project_id is not None:
            try:
                from ..kernels.identity import _accounts
                from .projects import ProjectStore, exists_for
                # 空 owner 只出现在内部调用/单机老部署：没有可比的调用者身份，
                # 保持旧语义（只校验存在），HTTP 入口始终带已核验的账号。
                submitter = str(owner_account_id or "").strip()
                visible = (
                    exists_for(project_id, submitter)
                    if submitter
                    else ProjectStore().exists(project_id)
                )
                if not visible:
                    raise ValueError(_accounts.NEUTRAL_NOT_FOUND_MESSAGE)
            except ValueError:
                raise
            except Exception as exc:
                raise ValueError(f"Project {project_id} lookup failed: {exc}")

        self._ensure_runtime()
        goal_id = str(uuid.uuid4())[:8]
        owner = normalise_owner(owner_account_id)

        # Bind to the real Employee (count submitted + mark agents BUSY) so the
        # "autonomous worker" actually drives the employee's agent pool.
        # 指名派活时驱动的是**被点名的那位员工**，不是默认管家 —— 否则它的
        # 状态永远显示空闲、KPI 永远为 0（重启后更无迹可查）。
        employee = self._goal_employee(pinned_agent)
        # 同一员工的点名派活是独占的：第二个并发请求直接 409，不排队、不丢。
        self._claim_employee_goal(
            employee.stable_id(), goal_id, exclusive=bool(pinned_agent)
        )
        employee.record_goal_submitted(goal_id)
        employee.mark_agents_busy()
        employee.record_activity(natural_language, employee.STATUS_RUNNING)
        with self._goal_lock:
            self._active_goals.add(goal_id)
        try:
            self._save_employee(employee)
        except Exception:
            # 目标还没开始执行：把刚占用的 agent 状态释放掉，别留下假 BUSY。
            self._release_employee_goal(goal_id)
            if self._unregister_active_goal(goal_id):
                employee.mark_agents_idle()
                try:
                    self._save_employee(employee)
                except Exception as _exc:  # noqa: BLE001 - already failing loudly
                    logger.error(
                        "AIStateManager: employee persist failed while rolling back goal %s: %s",
                        goal_id, _exc,
                    )
            raise

        if background:
            running_entry = {
                "goal_id": goal_id,
                "state": "running",
                "natural_language": natural_language,
                "scope": scope,
                "correlation_id": "",
                "error": None,
                "replan_count": 0,
                "replan_suggested": False,
                "trace": [],
                "tasks": [],
                "artifacts": [],
                # 派活前的能力边界提示：执行没结束就已经能被 GET 读到。
                "notices": [],
                "evaluation": None,
                "created_at": time.time(),
                "project_id": project_id,
                "assigned_agent": pinned_agent,
                "assigned_employee": self._employee_name_for_agent(pinned_agent),
            }
            self._stamp_owner(running_entry, owner)
            with self._goal_lock:
                self._goals[goal_id] = running_entry
            try:
                self._save_goals()
            except GoalPersistError as exc:
                # 记录没落盘就绝不能让调用方看到 200 running：回滚内存记录、
                # 释放 agent，并把真实失败原因返回给调用方。
                with self._goal_lock:
                    self._goals.pop(goal_id, None)
                self._release_employee_goal(goal_id)
                if self._unregister_active_goal(goal_id):
                    employee.mark_agents_idle()
                    try:
                        self._save_employee(employee)
                    except Exception as _exc:  # noqa: BLE001
                        logger.error(
                            "AIStateManager: employee persist failed while rolling back goal %s: %s",
                            goal_id, _exc,
                        )
                raise GoalPersistError(
                    f"目标未能启动：{exc.detail}。目标没有开始执行，"
                    "内存中的临时记录已回滚。"
                ) from exc
            t = threading.Thread(
                target=self._run_bound_goal,
                args=(goal_id, natural_language, scope, plan_mode,
                      verification_criteria, True, project_id, pinned_agent, owner),
                kwargs={"raise_on_persist": False, "notify_outcome": True},
                name=f"goal-{goal_id}",
                daemon=True,
            )
            # 先登记线程再启动：否则线程可能先结束并 pop，随后登记又留下一
            # 个永远不会被清理的线程记录。
            with self._goal_lock:
                self._goal_threads[goal_id] = t
            t.start()
            # Register the link immediately (goal id is known up-front).
            self._link_goal_to_project(project_id, goal_id)
            return running_entry

        entry = self._run_bound_goal(
            goal_id, natural_language, scope, plan_mode, verification_criteria, True,
            project_id, pinned_agent, owner, raise_on_persist=True,
        )
        # Register the link once the goal exists (sync path).
        self._link_goal_to_project(project_id, goal_id)
        return entry

    def _link_goal_to_project(
        self, project_id: Optional[str], goal_id: str
    ) -> None:
        """Best-effort: add a goal to a project's goal_ids (no-op if no project)."""
        if not project_id:
            return
        try:
            from .projects import ProjectStore
            ProjectStore().add_goal(project_id, goal_id)
        except Exception as exc:  # linking must never break goal creation
            logger.warning("goal->project link failed (%s/%s): %s",
                           project_id, goal_id, exc)

    def _unregister_active_goal(self, goal_id: str) -> bool:
        """移除目标的活跃/线程登记；返回是否已无其它活跃目标。

        线程记录必须在 ``finally`` 里清理：异常路径上不清理就会永久泄漏一条
        ``_goal_threads``。agent 是否标 idle 也由这里判定——并发目标里只要还有
        一个没结束，就不能把它的 agent 说成空闲。
        """
        with self._goal_lock:
            self._goal_threads.pop(goal_id, None)
            self._active_goals.discard(goal_id)
            return not self._active_goals

    def _run_bound_goal(
        self,
        goal_id: str,
        natural_language: str,
        scope: str,
        plan_mode: str,
        verification_criteria: Optional[Dict[str, Any]],
        persist: bool,
        project_id: Optional[str] = None,
        pinned_agent: Optional[str] = None,
        owner_account_id: str = "",
        only_task_name: Optional[str] = None,
        on_progress: Optional[Callable[[List[Dict[str, Any]]], None]] = None,
        raise_on_persist: bool = True,
        notify_outcome: bool = False,
    ) -> Dict[str, Any]:
        """后台/同步执行单个 Goal 并维护 Employee 绑定状态（BUSY/IDLE + 计数）。

        ``raise_on_persist``：同步路径为 True（落盘失败必须让接口返回明确
        错误）；后台线程为 False（目标已真实执行完，结果写入内存并在 GET 里
        标记 ``persisted=False`` / ``persist_error``，不能让线程异常静默丢失）。
        """
        accounting_done = False
        final_state: Optional[str] = None
        employee = None
        meter = None
        try:
            try:
                runtime = self._ensure_runtime()
                # 一次 Goal = 一个用量作用域：拆解、逐任务模型调用都汇总到这里，
                # 退出后按任务 label 拆分入账（总额 = 各分项之和）。
                with meter_scope() as active_meter:
                    meter = active_meter
                    result = runtime.run_goal(
                        goal_text=natural_language,
                        goal_id=goal_id,
                        scope=scope,
                        plan_mode=plan_mode,
                        verification_criteria=verification_criteria,
                        persist=persist,
                        on_task_progress=(
                            on_progress
                            if on_progress is not None
                            else (lambda tasks: self._record_task_snapshot(goal_id, tasks))
                        ),
                        pinned_agent=pinned_agent,
                        only_task_name=only_task_name,
                        on_notice=self._goal_notice_recorder(goal_id),
                    )
            except Exception as exc:
                logger.error("_run_bound_goal failed: %s", exc, exc_info=True)
                result = None
                error = "执行没有完成，请稍后重试。"
            else:
                error = None
            usage = snapshot(meter)

            # 更新的是本次真正被驱动的员工（指名 → 被点名者；否则默认管家）。
            employee = self._goal_employee(pinned_agent)
            if result is not None:
                final_state = (
                    result.state.value
                    if hasattr(result.state, "value")
                    else str(result.state)
                )
            # 该员工是否还有别的在跑目标（默认池可以并发，指名派活是独占的）。
            # 只有它名下已无在跑目标时才把 agent 标回 idle。
            self._unregister_active_goal(goal_id)
            employee_free = self._release_employee_goal(goal_id)
            accounting_done = True
            if employee_free:
                employee.mark_agents_idle()
            if final_state is not None:
                employee.record_goal_finished(goal_id, final_state)
            employee.record_activity(
                natural_language,
                employee.STATUS_COMPLETED
                if final_state == "completed"
                else employee.STATUS_ERROR,
            )
            employee_persist_error: Optional[str] = None
            try:
                self._save_employee(employee)
            except Exception as exc:
                # 目标执行本身已经完成，不能因为 KPI 落盘失败丢掉目标结果；
                # 但必须把失败如实带进结果，调用方能看到"员工进度没落盘"。
                employee_persist_error = f"{type(exc).__name__}: {exc}"

            if result is None:
                entry = {
                    "goal_id": goal_id,
                    "state": "failed",
                    "error": error,
                    "natural_language": natural_language,
                    "scope": scope,
                    "correlation_id": "",
                    "replan_count": 0,
                    "replan_suggested": False,
                    "trace": [],
                    "tasks": [],
                    "artifacts": [],
                    "notices": [],
                    "evaluation": None,
                    "created_at": time.time(),
                    "project_id": project_id,
                    "assigned_agent": pinned_agent,
                }
            else:
                entry = self._result_to_dict(result, natural_language, scope)
                entry["project_id"] = project_id
                entry["assigned_agent"] = pinned_agent
            # 归属沿用提交时的值（后台路径由 ``running_entry`` 携带）；绝不因为
            # 执行线程拿不到请求上下文就把目标改挂到别的账号。
            if employee_persist_error:
                entry["employee_persist_error"] = employee_persist_error
            with self._goal_lock:
                prior_owner = (self._goals.get(goal_id) or {}).get("owner_account_id")
            # 员工与任务的关系必须出现在最终结果里：谁做了什么、耗时、成功与否。
            self._attach_employee_attribution(entry)
            # 员工产生的记忆归其所属账号（旁路记录，失败不影响目标结果）。
            self._employee_memory_note(employee, entry)
            self._stamp_owner(entry, prior_owner or owner_account_id)
            self._attach_goal_usage(
                entry,
                usage=usage,
                goal_owner=owner_account_id,
                key_owner=str(getattr(employee, "owner_account_id", "") or owner_account_id),
                goal_id=goal_id,
            )
            with self._goal_lock:
                self._goals[goal_id] = entry
            try:
                self._save_goals()
            except GoalPersistError as exc:
                entry["persisted"] = False
                entry["persist_error"] = exc.detail
                logger.error(
                    "AIStateManager: goal %s finished as %s but could not be persisted: %s",
                    goal_id, entry.get("state"), exc,
                )
                if raise_on_persist:
                    raise GoalPersistError(
                        f"目标已执行结束（真实状态：{entry.get('state')}），"
                        f"但{exc.detail}；重启后该目标记录会丢失。"
                    ) from exc
            # 后台派活：任务结束必须让用户知道（托盘气泡 + 出站队列），
            # 页面关着也能收到；同步调用时用户就在界面前，不重复打扰。
            if notify_outcome:
                try:
                    from ..tasks.goal_notify import notify_goal_outcome

                    notify_goal_outcome(entry)
                except Exception:  # noqa: BLE001 - 通知失败不影响任务结果
                    logger.exception(
                        "AIStateManager: goal %s notify failed", goal_id
                    )
            return entry
        finally:
            if not accounting_done:
                # 异常路径：线程记录与活跃目标都必须释放，否则永久泄漏。
                self._unregister_active_goal(goal_id)
                employee_free = self._release_employee_goal(goal_id)
                try:
                    emp = employee or self._ensure_employee()
                    if employee_free:
                        emp.mark_agents_idle()
                        try:
                            self._save_employee(emp)
                        except Exception as exc:  # noqa: BLE001
                            logger.error(
                                "AIStateManager: employee persist failed after abnormal goal exit %s: %s",
                                goal_id, exc,
                            )
                except Exception as exc:  # noqa: BLE001
                    logger.error(
                        "AIStateManager: could not release employee pool for goal %s: %s",
                        goal_id, exc,
                    )

    # ── 运行中的逐任务进度 ──

    # ── 员工与任务的关系（结果里可见：谁做了什么 / 耗时 / 成败） ──

    @staticmethod
    def _duration_ms(started_at: Any, completed_at: Any) -> Optional[float]:
        """从内核的真实时间戳算耗时；拿不到就返回 None（绝不瞎估）。"""
        if not started_at or not completed_at:
            return None
        try:
            from datetime import datetime

            start = datetime.fromisoformat(str(started_at))
            end = datetime.fromisoformat(str(completed_at))
            delta = (end - start).total_seconds() * 1000
            return round(delta, 3) if delta >= 0 else None
        except Exception:  # noqa: BLE001 - 时间格式不认识就不编造耗时
            return None

    def _attach_employee_attribution(self, entry: Dict[str, Any]) -> Dict[str, Any]:
        """给 goal / 每个子任务补上"员工归属"与耗时、成败。

        数据全部来自真实记录（``assigned_agent`` → 员工名、内核写的
        ``started_at/completed_at``、任务状态），不是装饰性字段。老数据/无法
        解析的 agent id 保持 ``assigned_employee=None``，并计入 ``unattributed``。
        """
        if not isinstance(entry, dict):
            return entry
        agent_map = self._agent_employee_map()
        tasks = entry.get("tasks")
        rollup: Dict[str, Dict[str, Any]] = {}
        if isinstance(tasks, list):
            for task in tasks:
                if not isinstance(task, dict):
                    continue
                agent_id = task.get("assigned_agent")
                employee_name = agent_map.get(str(agent_id)) if agent_id else None
                if employee_name:
                    task["assigned_employee"] = employee_name
                else:
                    task["assigned_employee"] = None
                task["duration_ms"] = self._duration_ms(
                    task.get("started_at"), task.get("completed_at")
                )
                status = str(task.get("status") or "").lower()
                task["success"] = status in ("completed", "completed_unattributed")
                if status in ("completed", "completed_unattributed", "failed"):
                    key = employee_name or "unattributed"
                    bucket = rollup.setdefault(
                        key,
                        {
                            "employee": employee_name,
                            "tasks": 0,
                            "completed": 0,
                            "failed": 0,
                            "total_duration_ms": 0.0,
                        },
                    )
                    bucket["tasks"] += 1
                    if task["success"]:
                        bucket["completed"] += 1
                    else:
                        bucket["failed"] += 1
                    bucket["total_duration_ms"] += task.get("duration_ms") or 0.0
        for bucket in rollup.values():
            bucket["total_duration_ms"] = round(bucket["total_duration_ms"], 3)
        entry["employees"] = list(rollup.values())
        # 上屏的人名不止汇总：任务名/描述/指派理由都是模型写的，同样可能
        # 把活儿记到没参与的人头上。白名单只用"本次真的干了活的人"
        # （任务解析出的 assigned_employee），没有归属数据时不猜测。
        participants = {
            str(task.get("assigned_employee"))
            for task in tasks or []
            if isinstance(task, dict) and task.get("assigned_employee")
        }
        if participants:
            from src.ai.llm_support import scrub_unverified_staff_mentions

            for task in tasks or []:
                if not isinstance(task, dict):
                    continue
                for field in ("name", "description", "assignment_reason"):
                    value = task.get(field)
                    if value:
                        task[field] = scrub_unverified_staff_mentions(
                            value, participants
                        )
            evaluation = entry.get("evaluation")
            if isinstance(evaluation, dict) and evaluation.get("summary"):
                evaluation["summary"] = scrub_unverified_staff_mentions(
                    evaluation.get("summary"), participants
                )
        # goal 级：指名派活优先用钉住的员工；否则用任务里真实出现的员工集合。
        pinned = entry.get("assigned_agent")
        if pinned:
            entry["assigned_employee"] = agent_map.get(str(pinned))
            # 用户点名指派：理由是可核对的事实，不让界面/汇总空着；任务层
            # 也不能保留拆解器给"别人"写的理由。
            entry.setdefault("assignment_reason", "用户点名指派给该员工")
            for task in entry.get("tasks") or []:
                if isinstance(task, dict):
                    task["assignment_reason"] = "用户点名指派"
        if not entry.get("assigned_employee"):
            names = [b["employee"] for b in rollup.values() if b["employee"]]
            entry["assigned_employees"] = sorted(set(names))
        return entry

    def _attach_goal_usage(
        self,
        entry: Dict[str, Any],
        *,
        usage: Dict[str, Any],
        goal_owner: str,
        key_owner: str,
        goal_id: str,
    ) -> Dict[str, Any]:
        """把一次 Goal 的真实 token 用量按任务拆分并写入账号账本。

        数据来源只有一个：provider 在响应里返回的 usage（经 ``usage_meter``
        汇总）。没有真实 usage 就如实写 0 / ``source=none``，绝不估算成"真实"。

        记账口径：
        * ``actor`` = 发起派活的账号（谁在用）；
        * ``key_owner`` = 执行员工所属账号（额度记在谁的 Key 上）；
        * 每个任务一行、规划一行，行数之和 == Goal 总额。
        """
        if not isinstance(entry, dict):
            return entry
        snapshot_usage = usage or {}
        by_label = snapshot_usage.get("by_label") or {}
        tasks = entry.get("tasks")
        task_usage: Dict[str, Dict[str, Any]] = {}
        if isinstance(tasks, list):
            for task in tasks:
                if not isinstance(task, dict):
                    continue
                task_id = task.get("id")
                if not task_id:
                    continue
                bucket = by_label.get(f"task:{task_id}")
                if bucket:
                    item = {
                        "tokens_in": int(bucket.get("tokens_in") or 0),
                        "tokens_out": int(bucket.get("tokens_out") or 0),
                        "total_tokens": int(bucket.get("tokens_in") or 0)
                        + int(bucket.get("tokens_out") or 0),
                        "calls": int(bucket.get("calls") or 0),
                        "model": str(bucket.get("model") or ""),
                        "estimated": False,
                    }
                    task["usage"] = item
                    task_usage[str(task_id)] = item
        planning = by_label.get(PLANNING_LABEL)
        total_in = int(snapshot_usage.get("tokens_in") or 0)
        total_out = int(snapshot_usage.get("tokens_out") or 0)
        parts_in = sum(v["tokens_in"] for v in task_usage.values()) + int(
            (planning or {}).get("tokens_in") or 0
        )
        parts_out = sum(v["tokens_out"] for v in task_usage.values()) + int(
            (planning or {}).get("tokens_out") or 0
        )
        entry["usage"] = {
            "tokens_in": total_in,
            "tokens_out": total_out,
            "total_tokens": total_in + total_out,
            "calls": int(snapshot_usage.get("calls") or 0),
            "source": "provider" if snapshot_usage.get("reported") else "none",
            "estimated": False,
            "planning": (
                {
                    "tokens_in": int(planning.get("tokens_in") or 0),
                    "tokens_out": int(planning.get("tokens_out") or 0),
                    "calls": int(planning.get("calls") or 0),
                    "model": str(planning.get("model") or ""),
                }
                if planning
                else None
            ),
            "by_task": task_usage,
            "parts_tokens_in": parts_in,
            "parts_tokens_out": parts_out,
            "parts_match": parts_in == total_in and parts_out == total_out,
        }
        if not snapshot_usage.get("reported"):
            return entry
        actor = goal_owner or owner_of_entry(entry)
        billed_to = key_owner or actor
        correlation = str(entry.get("correlation_id") or "") or f"goal:{goal_id}"
        rows = []
        if planning:
            rows.append((PLANNING_LABEL, planning))
        for task_id in task_usage:
            rows.append((f"task:{task_id}", by_label.get(f"task:{task_id}") or {}))
        for label, bucket in rows:
            record_usage(
                actor=actor,
                key_owner=billed_to,
                model=str(bucket.get("model") or ""),
                tokens_in=int(bucket.get("tokens_in") or 0),
                tokens_out=int(bucket.get("tokens_out") or 0),
                session_id=f"goal:{goal_id}",
                correlation_id=f"{correlation}:{label}",
                estimated=False,
            )
        return entry

    def _goal_notice_recorder(self, goal_id: str) -> Callable[[str], None]:
        """把"派活前提示"写进仍在运行的目标记录（GET 立即可见）。"""

        def _record(message: str) -> None:
            text = str(message or "").strip()
            if not text:
                return
            with self._goal_lock:
                entry = self._goals.get(goal_id)
                if entry is None or entry.get("state") != "running":
                    return
                notices = list(entry.get("notices") or [])
                if text not in notices:
                    notices.append(text)
                    entry["notices"] = notices

        return _record

    def _record_task_snapshot(self, goal_id: str, tasks: List[Dict[str, Any]]) -> None:
        """把执行内核的**当前**任务快照增量落进 ``_goals[goal_id]["tasks"]``。

        为什么不是"整份覆盖"：执行期间每个 task 的状态由内核自己推进，网关
        只做**按 task id 的槽位更新** —— 只替换快照里出现的那几条，其它任务
        的既有结果原样保留。这样即便两次快照交错（或某次回调晚到），也不会
        用一份不含某个任务结果的旧列表盖掉它。

        竞态保护：
        * 只在目标仍为 ``running`` 时写入 —— 终态一旦落盘，迟到的回调一律
          丢弃，绝不把已完成的任务改回 ``running``；
        * 锁内只做内存合并，序列化写在锁外（``_save_goals`` 自己取快照），
          避免持锁做磁盘 IO。
        """
        if not tasks:
            return
        changed = False
        self._agent_employee_map()  # 预热缓存，避免持锁做磁盘 IO
        with self._goal_lock:
            entry = self._goals.get(goal_id)
            if entry is None or entry.get("state") != "running":
                return
            owner = owner_of_entry(entry)
            current = list(entry.get("tasks") or [])
            positions = {
                t.get("id"): i for i, t in enumerate(current) if t.get("id")
            }
            for snapshot in tasks:
                task_id = snapshot.get("id")
                if not task_id:
                    continue
                # 子任务自带归属：重试/下载判定不必回看 goal，也不会因为快照
                # 来自内核就丢掉 owner。
                snapshot = dict(snapshot, owner_account_id=owner)
                self._attach_employee_attribution({"tasks": [snapshot]})
                position = positions.get(task_id)
                if position is not None:
                    if current[position] != snapshot:
                        current[position] = snapshot
                        changed = True
                else:
                    positions[task_id] = len(current)
                    current.append(snapshot)
                    changed = True
            if changed:
                entry["tasks"] = current
                self._touch(entry)
        if changed:
            try:
                self._save_goals()
            except GoalPersistError as exc:
                # 这只是执行中的进度镜像：落盘失败不能反过来打断正在执行的任务，
                # 但必须记录到目标上，GET 时能看到"进度未落盘"。
                logger.error(
                    "AIStateManager: progress snapshot for goal %s was not persisted: %s",
                    goal_id, exc,
                )
                with self._goal_lock:
                    live = self._goals.get(goal_id)
                    if isinstance(live, dict):
                        live["persisted"] = False
                        live["persist_error"] = exc.detail

    # ── 单独指派员工 / 单任务重试 ──

    def assign_task(
        self,
        agent_id: Optional[str] = None,
        instruction: str = "",
        scope: str = "L1",
        project_id: Optional[str] = None,
        background: bool = True,
        principal: str = "",
        owner_account_id: str = "",
        employee_name: Optional[str] = None,
        viewer: Optional[GoalViewer] = None,
    ) -> Dict[str, Any]:
        """把一件事直接指派给指定员工执行（不经"一句话自动拆解"入口）。

        执行主体二选一：``employee_name``（员工级）或 ``agent_id``（agent 级）。
        两者都不给 → 400；都给出 → 400。指定不存在/不可见/别人的员工一律 404
        （与"不存在"同文案），停用 → 409 —— 绝不静默换成别的员工。

        与 ``POST /v1/goals`` 是**两个独立入口**：这里执行主体由调用方点名，
        而 ``POST /v1/goals`` 仍由管家自己决定怎么拆、派给谁。
        """
        if bool(employee_name) == bool(agent_id):
            raise GoalWorkError(
                400,
                "assign_target_required",
                "必须且只能指定一个执行人：员工名（employee_name）或执行个体（agent_id）",
            )

        if employee_name:
            employee, agent = self.resolve_employee_agent(employee_name, viewer)
        else:
            employee, agent = self._find_employee_with_agent(str(agent_id))
            if agent is None or employee is None:
                raise GoalWorkError(
                    404,
                    "agent_not_found",
                    f"指定的员工不存在或不在当前可见范围：{agent_id}"
                    "（不会改用其他员工执行）",
                )
            if not self._employee_visible(employee, viewer) or not self._employee_writable(
                employee, viewer
            ):
                raise GoalWorkError(
                    404,
                    "agent_not_found",
                    f"指定的员工不存在或不在当前可见范围：{agent_id}"
                    "（不会改用其他员工执行）",
                )
            if not employee.is_active():
                raise GoalWorkError(
                    409,
                    "employee_disabled",
                    f"员工 {employee.name} 已停用，无法接受指派；请先启用后再派活",
                )

        status = agent.status.value if hasattr(agent.status, "value") else str(agent.status)
        if status == "paused":
            raise GoalWorkError(
                409,
                "agent_unavailable",
                f"员工 {employee.name} 的 agent {agent.id} 处于暂停状态，"
                "无法接受指派；请先恢复后再派活",
            )

        entry = self.create_and_execute_goal(
            natural_language=instruction,
            scope=scope,
            project_id=project_id,
            background=background,
            pinned_agent=agent.id,
            owner_account_id=owner_account_id or principal,
        )
        entry["assigned_agent"] = agent.id
        entry["assigned_employee"] = employee.name
        self._audit_goal_action(
            goal_id=str(entry.get("goal_id") or ""),
            action="assign_task",
            principal=principal,
            target=employee.name,
            details={
                "agent_id": agent.id,
                "employee_name": employee.name,
                "background": bool(background),
                "owner_account_id": owner_of_entry(entry),
            },
        )
        return entry

    @staticmethod
    def _retryable_task_states(entry: Dict[str, Any]) -> set:
        """哪些任务状态允许"单独重试"。

        * 失败任务：原有语义不变；
        * ``interrupted``：进程中断时仍在执行、结果未知；
        * 中断目标里的 ``pending/ready/planned``：任务从未执行，
          重启后可以单独补跑（不允许把整个目标静默重跑）。
        """
        retryable = {"failed", "interrupted", "unknown"}
        if str(entry.get("state") or "").lower() == "interrupted":
            retryable |= {"pending", "ready", "planned"}
        return retryable

    @staticmethod
    def _pinned_tasks_snapshot(tasks: List[Dict[str, Any]]) -> Optional[List[Dict[str, Any]]]:
        """把落盘的任务列表还原成内核可执行的原计划快照。

        需要每张任务卡都有 ``capability_id``（否则无法重建执行计划）；``inputs``
        缺失只影响该任务本身，因此按空字典兜底。旧数据（连 capability 都没有）
        退回按任务名重试的兼容路径。
        """
        if not tasks:
            return None
        specs: List[Dict[str, Any]] = []
        for task in tasks:
            if not isinstance(task, dict):
                return None
            if not task.get("capability_id"):
                return None
            specs.append({
                "id": task.get("id"),
                "name": task.get("name"),
                "description": task.get("description"),
                "capability_id": task.get("capability_id"),
                "inputs": task.get("inputs") if isinstance(task.get("inputs"), dict) else {},
                "dependencies": list(task.get("dependencies") or []),
                "max_retries": task.get("max_retries"),
                "timeout_seconds": task.get("timeout_seconds"),
            })
        return specs

    @staticmethod
    def _retry_scope_names(
        tasks: List[Dict[str, Any]], task_id: str
    ) -> List[str]:
        """重试范围：目标任务 + 因它中断而从未执行的（传递）下游任务。

        只纳入 ``pending/ready/planned`` 的下游任务：它们从未执行、没有可疑
        产物，依赖刚被补跑后应当接着完成。已完成/失败的任务绝不在范围内，
        避免"重试一个任务却重跑了别的"。
        """
        target = next((t for t in tasks if t.get("id") == task_id), None)
        if target is None:
            return []
        queued = {"pending", "ready", "planned"}
        scope_ids = {task_id}
        changed = True
        while changed:
            changed = False
            for task in tasks:
                tid = task.get("id")
                if tid in scope_ids:
                    continue
                if str(task.get("status") or "").lower() not in queued:
                    continue
                deps = task.get("dependencies") or []
                if any(dep in scope_ids for dep in deps):
                    scope_ids.add(tid)
                    changed = True
        names: List[str] = []
        for task in tasks:
            if task.get("id") in scope_ids and task.get("name"):
                names.append(str(task["name"]))
        return names or [str(target.get("name") or "")]

    def retry_task(
        self,
        goal_id: str,
        task_id: str,
        principal: str = "",
        viewer: Optional[GoalViewer] = None,
    ) -> Dict[str, Any]:
        """只重跑一个失败/中断任务，其余任务的结果原样保留。

        实现要点：
        * 目标必须存在、任务必须存在、任务状态必须是 ``failed``/``interrupted``
          （中断目标里排队的任务也可补跑）；"已成功"或"仍在运行"的任务明确
          拒绝（409），不会顺带重跑；
        * 有 ``viewer`` 时只允许重试**自己的**目标；别人的目标（以及不存在的
          目标）统一 404，文案完全相同 —— 不暴露存在与否；
        * 重跑时把其余任务标成 SKIPPED（内核层 ``only_task_name``），
          因此**物理上**不会执行它们；
        * 有原计划快照（capability + inputs）时按快照重建任务，避免重启后
          拆解器换了任务名导致"找不到目标"；
        * 进度回调只接受这一个任务的快照，按原 ``task_id`` 回写，
          其余任务在网关侧完全不被触碰 —— "重试一个不影响其他"。
        """
        entry = self._lookup_goal(goal_id)
        tasks = list(entry.get("tasks") or []) if entry else []
        if entry is None:
            raise GoalWorkError(404, "goal_not_found", f"目标不存在：{goal_id}")
        if viewer is not None and not viewer.owns(entry):
            raise GoalWorkError(404, "goal_not_found", f"目标不存在：{goal_id}")
        owner = owner_of_entry(entry)
        target = next((t for t in tasks if t.get("id") == task_id), None)
        if target is None:
            raise GoalWorkError(
                404, "task_not_found", f"目标 {goal_id} 下不存在任务 {task_id}"
            )
        if entry.get("state") == "running":
            raise GoalWorkError(
                409, "goal_running", f"目标 {goal_id} 仍在运行，请等它结束后再重试单个任务"
            )
        if target.get("status") not in self._retryable_task_states(entry):
            raise GoalWorkError(
                409,
                "task_not_retryable",
                f"任务 {task_id} 当前状态为 {target.get('status')!r}，只有失败/中断"
                "（或中断目标里的排队任务）可以重试",
            )

        task_name = str(target.get("name") or "")
        original_agent = target.get("assigned_agent")
        natural_language = str(entry.get("natural_language") or "")
        scope = str(entry.get("scope") or "L1")
        # 重启后拆解器可能给出与原来不同的任务名；用落盘的原计划快照重建
        # 任务（含 inputs/dependencies），保证"接着完成"而不是按名失败。
        # 目标任务自己没有 inputs 的旧数据退回按名重试。
        pinned_tasks = self._pinned_tasks_snapshot(tasks)
        if pinned_tasks is not None and not isinstance(target.get("inputs"), dict):
            pinned_tasks = None
        # 重试范围：目标任务 + 因它中断而从未执行的排队下游任务。只跑这一个
        # 会把目标永远停在"还有任务没做"，用户还得再点一次；把从未执行的
        # 下游任务一起补上，才叫"接着完成"。已完成任务绝不在范围内。
        retry_names = self._retry_scope_names(tasks, task_id)

        # 标记为 running，让轮询方看到"这次重试正在进行"。
        with self._goal_lock:
            running_entry = dict(entry)
            running_entry["state"] = "running"
            running_entry["error"] = None
            self._stamp_owner(running_entry, owner)
            self._touch(running_entry)
            self._goals[goal_id] = running_entry
        try:
            self._save_goals()
        except GoalPersistError:
            # 没落盘就不算开始：把内存恢复成重试前的真实状态，再让接口报错。
            with self._goal_lock:
                self._goals[goal_id] = entry
            raise

        def _progress(snapshots: List[Dict[str, Any]]) -> None:
            for snapshot in snapshots:
                name = str(snapshot.get("name") or "")
                if name not in retry_names:
                    continue
                # 保留原卡片 id：前端按 id 渲染，重跑不应换一张卡。
                if name == task_name:
                    snapshot = dict(snapshot, id=task_id)
                snapshot = dict(snapshot, owner_account_id=owner)
                self._attach_employee_attribution({"tasks": [snapshot]})
                with self._goal_lock:
                    live = self._goals.get(goal_id)
                    if live is None or live.get("state") != "running":
                        return
                    live_tasks = list(live.get("tasks") or [])
                    for index, item in enumerate(live_tasks):
                        if item.get("id") == snapshot.get("id") or \
                                item.get("name") == name:
                            live_tasks[index] = snapshot
                            break
                    else:
                        live_tasks.append(snapshot)
                    live["tasks"] = live_tasks
                    self._touch(live)
                # 快照很小，但也不在锁内落盘。
                try:
                    self._save_goals()
                except GoalPersistError as exc:
                    # 进度回调里抛异常会打断正在执行的任务；只记录，不阻断。
                    logger.error(
                        "AIStateManager: retry progress for goal %s was not persisted: %s",
                        goal_id, exc,
                    )
                    with self._goal_lock:
                        live = self._goals.get(goal_id)
                        if isinstance(live, dict):
                            live["persisted"] = False
                            live["persist_error"] = exc.detail

        result: Any = None
        error: Optional[str] = None
        try:
            runtime = self._ensure_runtime()
            result = runtime.run_goal(
                goal_text=natural_language,
                goal_id=goal_id,
                scope=scope,
                persist=True,
                pinned_agent=original_agent,
                only_task_name=task_name,
                only_task_names=retry_names,
                pinned_tasks=pinned_tasks,
                on_task_progress=_progress,
            )
        except Exception as exc:  # noqa: BLE001 - 真实原因要回给调用方
            error = "重试没有完成，请稍后重试。"
            logger.error("retry_task failed (%s/%s): %s", goal_id, task_id, exc)

        # 合并：只替换重试范围内的任务，其余任务取**旧**值。
        serialized: Optional[Dict[str, Any]] = None
        merged = [dict(t) for t in tasks]
        if result is not None:
            serialized = self._result_to_dict(result, natural_language, scope)
            new_tasks = serialized.get("tasks") or []
            replaced = next((t for t in new_tasks if t.get("name") == task_name), None)
            if replaced is not None:
                replaced = dict(replaced, id=task_id)
            for new_task in new_tasks:
                name = str(new_task.get("name") or "")
                if name not in retry_names:
                    continue  # 范围内的任务才准覆盖旧卡片
                fresh = dict(new_task)
                if name == task_name:
                    fresh["id"] = task_id
                index = next(
                    (i for i, item in enumerate(merged)
                     if item.get("id") == fresh.get("id")),
                    None,
                )
                if index is None:
                    index = next(
                        (i for i, item in enumerate(merged)
                         if item.get("name") == name),
                        None,
                    )
                if index is None:
                    merged.append(fresh)
                else:
                    merged[index] = fresh
            final_state = (
                "completed"
                if all(
                    item.get("status") in (
                        "completed", "completed_unattributed", "skipped"
                    )
                    for item in merged
                )
                else "failed"
            )
            if final_state == "completed":
                final_error = None
            elif replaced is None:
                final_error = (
                    "重试没有执行目标任务（重启后拆解结果与快照不一致）；"
                    "请重新提交目标，或先重试它的依赖任务。"
                )
            elif replaced.get("status") in ("pending", "ready", "planned"):
                final_error = (
                    "重试未执行：依赖任务仍处于未完成状态，任务在等待上游结果；"
                    "请先重试它的依赖任务。"
                )
            else:
                blocker = next(
                    (item for item in merged
                     if item.get("status") not in
                     ("completed", "completed_unattributed", "skipped")),
                    None,
                )
                final_error = (blocker or {}).get("error") or entry.get("error")
        else:
            final_state = "failed"
            final_error = error or entry.get("error")

        final_entry = dict(entry)
        final_entry["tasks"] = merged
        final_entry["state"] = final_state
        final_entry["error"] = final_error
        self._stamp_owner(final_entry, owner)
        self._touch(final_entry)
        if result is not None and serialized is not None:
            final_entry["replan_suggested"] = bool(getattr(result, "replan_suggested", False))
            # Artifacts are FACTS about files on disk. A retry run only executes
            # one task, so its artifact list is a delta -- union it with what the
            # original run already produced instead of dropping real files.
            final_entry["artifacts"] = self._merge_artifacts(
                entry.get("artifacts"), serialized.get("artifacts")
            )
            # Evaluation must describe the WHOLE goal after the merge, not just
            # the one retried task. The retry run reports SUCCESS because it had
            # a single task; blindly storing that made the UI show SUCCESS while
            # the goal was still failed.
            final_entry["evaluation"] = self._recompose_retry_evaluation(
                natural_language,
                serialized.get("evaluation"),
                merged,
            )
        with self._goal_lock:
            self._goals[goal_id] = final_entry
        try:
            self._save_goals()
        except GoalPersistError as exc:
            final_entry["persisted"] = False
            final_entry["persist_error"] = exc.detail
            raise

        self._audit_goal_action(
            goal_id=goal_id,
            action="retry_task",
            principal=principal,
            details={
                "task_id": task_id,
                "task_name": task_name,
                "assigned_agent": original_agent,
                "outcome": final_state,
                "owner_account_id": owner,
            },
        )
        return final_entry

    @staticmethod
    def _merge_artifacts(existing: Any, new: Any) -> List[str]:
        """Union two artifact lists, keeping the first occurrence of each path."""
        merged: List[str] = []
        seen = set()
        for source in (existing or [], new or []):
            if not isinstance(source, list):
                continue
            for item in source:
                if not isinstance(item, str) or not item:
                    continue
                if item in seen:
                    continue
                seen.add(item)
                merged.append(item)
        return merged

    def _recompose_retry_evaluation(
        self,
        natural_language: str,
        retry_evaluation: Any,
        merged_tasks: List[Dict[str, Any]],
    ) -> Any:
        """Rebuild goal-level evaluation from the MERGED task set.

        The retry run only executed one task, so its evaluator saw no failures
        and reported SUCCESS. Storing that next to a still-failed goal was the
        inconsistency this fixes: outcome and summary are now derived from every
        task (old results + the retried one).
        """
        if retry_evaluation is None:
            return None
        evaluation = copy.deepcopy(retry_evaluation)
        completed = [
            t for t in merged_tasks
            if t.get("status") in ("completed", "completed_unattributed")
        ]
        failed = [t for t in merged_tasks if t.get("status") == "failed"]
        if failed:
            outcome_value = "partial" if completed else "failed"
        else:
            outcome_value = "success"
        evaluation["outcome"] = _outcome_label(outcome_value)
        evaluation["failed_task_count"] = len(failed)
        evaluation["completed_task_count"] = len(completed)
        records = [
            {
                "name": t.get("name") or "未命名任务",
                "capability": t.get("capability_id") or t.get("capability") or "",
                "status": t.get("status") or "",
                "result": t.get("result"),
                "error": t.get("error"),
            }
            for t in merged_tasks
        ]
        generator = None
        if _butler_model_planning_enabled():
            try:
                from src.ai.llm_support import default_generate

                generator = default_generate
            except Exception:  # pragma: no cover - import is static in practice
                generator = None
        try:
            from src.ai.llm_support import compose_execution_summary

            evaluation["summary"] = compose_execution_summary(
                natural_language, records, generator
            )
        except Exception:  # noqa: BLE001 - summary must never break retry
            evaluation["summary"] = str(evaluation.get("summary") or "")
        return evaluation

    def replan_goal(
        self, goal_id: str, viewer: Optional[GoalViewer] = None
    ) -> Dict[str, Any]:
        """对已失败的 Goal 执行 replan。"""
        prior_entry = self._lookup_goal(goal_id)
        if prior_entry is None:
            if viewer is not None:
                raise GoalWorkError(404, "goal_not_found", f"目标不存在：{goal_id}")
            raise ValueError(f"Goal {goal_id} not found")
        if viewer is not None and not viewer.owns(prior_entry):
            # 别人的目标：与不存在完全同一响应（404 + 同一文案）。
            raise GoalWorkError(404, "goal_not_found", f"目标不存在：{goal_id}")

        runtime = self._ensure_runtime()
        # 从存储的 dict 重建 AgentRunResult 的关键字段
        from src.ai.agent_runtime import AgentRunResult, AgentRunState, ExecutionTrace
        prior = AgentRunResult(
            goal_id=goal_id,
            correlation_id=prior_entry.get("correlation_id", ""),
            state=AgentRunState(prior_entry.get("state", "failed")),
            context=None,
            evaluation=None,
            trace=ExecutionTrace(correlation_id=prior_entry.get("correlation_id", "")),
            error=prior_entry.get("error"),
            # Thread the STORED count into the prior so the runtime's own
            # ``max_replans`` bound is enforced *through* the gateway. Without
            # this the runtime always sees 0 and can never refuse a replan.
            replan_count=prior_entry.get("replan_count", 0),
        )
        result = runtime.replan(prior, goal_text=prior_entry["natural_language"])
        entry = self._result_to_dict(result, prior_entry["natural_language"],
                                     prior_entry["scope"])
        # ``replan_count`` is the runtime's AUTHORITATIVE count -- already carried
        # by ``_result_to_dict`` from ``result.replan_count``. Never write the
        # unconditional ``prior + 1`` here: when the runtime REFUSES the replan
        # (it hit ``max_replans`` and returned ``prior`` unchanged) nothing
        # re-ran, so counting one more is a quiet lie about how many times the
        # goal actually re-ran.
        # Keep the project association across a replan (honest continuity).
        entry["project_id"] = prior_entry.get("project_id")
        # Replan 不改变归属：无论执行线程能不能拿到请求上下文，owner 都是
        # 原目标的主人。
        self._stamp_owner(entry, owner_of_entry(prior_entry))
        self._touch(entry)
        with self._goal_lock:
            self._goals[goal_id] = entry
        try:
            self._save_goals()
        except GoalPersistError as exc:
            entry["persisted"] = False
            entry["persist_error"] = exc.detail
            raise
        return entry

    def list_goals(
        self, viewer: Optional[GoalViewer] = None
    ) -> List[Dict[str, Any]]:
        """列出已执行 Goal 摘要；传 ``viewer`` 时只返回其可见范围。"""
        with self._goal_lock:
            items = list(self._goals.values())
        summaries = [
            self._goal_summary(item)
            for item in items
            if viewer is None or viewer.sees(item)
        ]
        # 已完成并归档的目标仍然出现在列表里（读路径兼容）：用归档索引里的小
        # 摘要合并，而不是把历史全量读回内存。
        active_ids = {str(item.get("goal_id")) for item in items}
        for goal_id, summary in self._load_archived_summaries().items():
            if goal_id in active_ids:
                continue
            if viewer is not None and not viewer.sees(summary):
                continue
            merged = dict(summary)
            merged.setdefault("archived", True)
            summaries.append(merged)
        return summaries

    def get_goal(
        self, goal_id: str, viewer: Optional[GoalViewer] = None
    ) -> Dict[str, Any]:
        """获取 Goal 详情；传 ``viewer`` 时不可见与不存在返回同一 404。"""
        item = self._lookup_goal(goal_id)
        if item is None:
            if viewer is not None:
                raise GoalWorkError(404, "goal_not_found", f"目标不存在：{goal_id}")
            raise ValueError(f"Goal {goal_id} not found")
        if viewer is not None and not viewer.sees(item):
            raise GoalWorkError(404, "goal_not_found", f"目标不存在：{goal_id}")
        return self._attach_employee_attribution(item)

    def stop_goal(
        self, goal_id: str, viewer: Optional[GoalViewer] = None
    ) -> Dict[str, Any]:
        """人类操作员中止一个 Goal（主权控制）—— 真实取消运行中目标并审计。

        - 若目标仍在后台运行：设置其 stop flag，执行循环会在任务边界抛出
          GoalCancelled，目标被干净置为 ``cancelled``（绝不伪造成功）。
        - 若目标已结束：**如实保留其真实终态**，不在事后把 error 改写成
          "aborted"。把一个已经 completed/failed 的目标标为人类中止，就是让
          操作员看到一个自己并未造成的结果。
        - 有 ``viewer`` 时只能中止**自己的**目标；别人的目标一律 404（与不存在
          同一响应），不暴露存在与否。
        - 无论哪种情况都审计这条人类喊停，使审计链如实记录主权动作。
        """
        item = self._lookup_goal(goal_id)
        if item is None:
            if viewer is not None:
                raise GoalWorkError(404, "goal_not_found", f"目标不存在：{goal_id}")
            raise ValueError(f"Goal {goal_id} not found")
        if viewer is not None and not viewer.owns(item):
            raise GoalWorkError(404, "goal_not_found", f"目标不存在：{goal_id}")

        # Ask the runtime to abort any live execution for this goal. Retry
        # briefly because the background thread registers its stop flag a few
        # ms after the goal is created.
        cancelled = False
        try:
            runtime = self._ensure_runtime()
            deadline = time.time() + 5.0
            while time.time() < deadline:
                if runtime.request_stop(goal_id):
                    cancelled = True
                    break
                with self._goal_lock:
                    cur = self._goals.get(goal_id)
                if cur is not None and cur.get("state") != "running":
                    break
                time.sleep(0.1)
        except Exception as exc:
            logger.warning("stop_goal: request_stop failed: %s", exc)

        if cancelled:
            # Wait for the background execution to observe the stop flag and
            # persist its terminal CANCELLED state (bounded wait).
            self._await_goal(goal_id, timeout=30.0)

        # Audit the human stop (honest: this is an allowed sovereign action).
        try:
            self._audit_goal_stop(goal_id, was_running=cancelled)
        except Exception as exc:
            logger.warning("stop_goal: audit failed: %s", exc)

        # Reflect cancellation in the persisted goal state -- but ONLY for a
        # goal that was actually still running. A goal that had already reached
        # a terminal state keeps its real outcome and its real error: the old
        # code stamped "aborted by human operator" onto anything that received
        # a stop POST, so an already-failed goal appeared to have been stopped
        # by someone who was not there, and its root cause was overwritten.
        with self._goal_lock:
            item = self._goals.get(goal_id)
            if item is not None:
                item = dict(item)
                if item.get("state") not in ("completed", "failed", "cancelled"):
                    item["state"] = "cancelled"
                    item["error"] = item.get("error") or "aborted by human operator"
                    item["replan_suggested"] = False
                self._stamp_owner(item)
                self._touch(item)
                self._goals[goal_id] = item
            final_state = (self._goals.get(goal_id) or {}).get("state")
        try:
            self._save_goals()
        except GoalPersistError as exc:
            with self._goal_lock:
                live = self._goals.get(goal_id)
                if isinstance(live, dict):
                    live["persisted"] = False
                    live["persist_error"] = exc.detail
            raise

        # Keep the employee KPI honest about the cancellation -- and only about
        # a cancellation. Counting every stop POST as a failed goal meant one
        # completed goal was booked BOTH as completed and as failed, inflating
        # the denominator of every success-rate figure derived from these KPIs.
        cancel_persist_error: Optional[str] = None
        if final_state == "cancelled":
            try:
                employee = self._ensure_employee()
                employee.record_goal_finished(goal_id, "cancelled")
                self._persist_employee()
            except Exception as exc:
                # 取消本身已经生效；KPI 落盘失败必须让调用方看见，不能再静默吞掉。
                cancel_persist_error = f"{type(exc).__name__}: {exc}"
                logger.error(
                    "AIStateManager: employee persist failed after cancel %s: %s",
                    goal_id, exc,
                )

        with self._goal_lock:
            result = dict(self._goals.get(goal_id, item))
        if cancel_persist_error:
            result["employee_persist_error"] = cancel_persist_error
        return result

    def _await_goal(self, goal_id: str, timeout: float = 30.0) -> None:
        """Block until the background goal leaves the 'running' state (or timeout)."""
        deadline = time.time() + timeout
        while time.time() < deadline:
            with self._goal_lock:
                item = self._goals.get(goal_id)
            if item is None or item.get("state") != "running":
                return
            time.sleep(0.1)

    def _audit_goal_stop(self, goal_id: str, was_running: bool) -> None:
        """Record the human stop action in the audit chain (honest sovereignty)."""
        from src.kernels.audit import log_event, AuditEventType, AuditScope

        item = self._lookup_goal(goal_id)
        owner = owner_of_entry(item) if item else None
        log_event(
            AuditEventType.GOAL_CONTROL,
            principal_id="human-operator",
            scope=AuditScope.L1,
            outcome="allow",
            details={
                "goal_id": goal_id,
                "action": "stop",
                "was_running": bool(was_running),
                "owner_account_id": owner,
                "reason": "操作人主动中止",
            },
            correlation_id=goal_id,
        )

    def _audit_goal_action(
        self,
        goal_id: str,
        action: str,
        principal: str = "",
        details: Optional[Dict[str, Any]] = None,
        target: str = "",
    ) -> None:
        """Record "who did what to which goal" -- assignment and single-task retry.

        Attributed to the **verified** principal from the bearer token, not a
        literal: an audit chain that names nobody cannot answer "who re-ran
        this". Audit failure never blocks the operation it describes.
        """
        from src.kernels.audit import log_event, AuditEventType, AuditScope

        payload: Dict[str, Any] = {"goal_id": goal_id, "action": action}
        if target:
            payload["target"] = target
        payload.update(details or {})
        try:
            log_event(
                AuditEventType.GOAL_CONTROL,
                principal_id=principal or "unknown",
                scope=AuditScope.L1,
                outcome="allow",
                details=payload,
                correlation_id=goal_id,
            )
        except Exception as exc:  # noqa: BLE001 - audit must not break control flow
            logger.warning("goal audit failed (%s/%s): %s", goal_id, action, exc)

    # ── 产物归属（下载时按 owner 过滤） ──

    @staticmethod
    def _normalise_artifact_path(path: str) -> str:
        return str(path or "").replace("\\", "/").strip().strip("/")

    def artifact_owner_index(self) -> Dict[str, Set[str]]:
        """``工作区相对路径 -> {所属账号}`` 的索引（内存 ∪ 磁盘）。

        只用已知产物：目标条目里的 ``artifacts`` 是"真实写出且落在工作区内"的
        路径。没有归属记录的文件不在此索引里，下载时按"公共文件"处理 ——
        既不假装是某人的产物，也不会误挡通用文件浏览。

        读磁盘是为了同机多 worker：另一个进程刚产生的产物，本进程内存里还
        没有，但下载判定必须立刻生效。
        """
        with self._goal_lock:
            entries: Dict[str, Any] = dict(self._goals)
        for gid, entry in self._read_goals_file(self._goals_path).items():
            if gid not in entries:
                entries[gid] = entry
        index: Dict[str, Set[str]] = {}
        for entry in entries.values():
            if not isinstance(entry, dict):
                continue
            entry_owner = owner_of_entry(entry)
            per_artifact = entry.get("artifact_owners")
            if not isinstance(per_artifact, dict):
                per_artifact = {}
            for path in entry.get("artifacts") or []:
                if not isinstance(path, str):
                    continue
                normalised = self._normalise_artifact_path(path)
                if normalised:
                    owner = str(
                        per_artifact.get(normalised) or entry_owner
                    ).strip() or entry_owner
                    index.setdefault(normalised, set()).add(owner)
        return index

    def artifact_owners(self, path: str) -> Set[str]:
        """某个工作区相对路径的归属账号集合；空集合 = 没有归属记录。"""
        return set(
            self.artifact_owner_index().get(
                self._normalise_artifact_path(path), set()
            )
        )

    # ── Employee / Agent 操作 ──

    def list_agents(
        self, viewer: Optional[GoalViewer] = None
    ) -> List[Dict[str, Any]]:
        """列出**当前账号可见**员工的 agent 池（不只默认员工）。"""
        result: List[Dict[str, Any]] = []
        store = self._store()
        for ename in store.list_employees():
            emp = store.load(ename)
            if emp is None or not self._employee_visible(emp, viewer):
                continue
            for agent in emp.agents.values():
                result.append({
                    "id": agent.id,
                    "agent_type": agent.agent_type,
                    "name": agent.name,
                    "status": agent.status.value,
                    "current_task": agent.current_task,
                    "completed_tasks": agent.completed_tasks,
                    "failed_tasks": agent.failed_tasks,
                    "total_latency_ms": agent.total_latency_ms,
                    "employee_name": emp.name,
                })
        return result

    def get_agent(
        self, agent_id: str, viewer: Optional[GoalViewer] = None
    ) -> Dict[str, Any]:
        """获取单个 agent 详情（跨所有员工按 id 解析；不可见 = 404）。"""
        employee, agent = self._find_employee_with_agent(agent_id)
        if agent is None or not self._employee_visible(employee, viewer):
            raise ValueError(f"Agent {agent_id} not found")
        return {
            "id": agent.id,
            "agent_type": agent.agent_type,
            "name": agent.name,
            "status": agent.status.value,
            "current_task": agent.current_task,
            "completed_tasks": agent.completed_tasks,
            "failed_tasks": agent.failed_tasks,
            "total_latency_ms": agent.total_latency_ms,
            "employee_name": employee.name,
            "metadata": dict(agent.metadata),
            "checkpoint_state": agent.checkpoint_state,
        }

    def pause_agent(
        self, agent_id: str, viewer: Optional[GoalViewer] = None
    ) -> Dict[str, Any]:
        """暂停一个 agent（跨所有员工按 id 解析；只能动自己的员工）。"""
        employee, agent = self._find_employee_with_agent(agent_id)
        if agent is None or not self._employee_writable(employee, viewer) \
                or not self._employee_visible(employee, viewer):
            raise ValueError(f"Agent {agent_id} not found")
        ok = agent.pause()
        if employee is self._ensure_employee():
            self._persist_employee()
        else:
            self._store().save(employee)
        return {"agent_id": agent_id, "paused": ok, "status": agent.status.value}

    def resume_agent(
        self, agent_id: str, viewer: Optional[GoalViewer] = None
    ) -> Dict[str, Any]:
        """恢复一个暂停的 agent（跨所有员工按 id 解析；只能动自己的员工）。"""
        employee, agent = self._find_employee_with_agent(agent_id)
        if agent is None or not self._employee_writable(employee, viewer) \
                or not self._employee_visible(employee, viewer):
            raise ValueError(f"Agent {agent_id} not found")
        ok = agent.resume()
        if employee is self._ensure_employee():
            self._persist_employee()
        else:
            self._store().save(employee)
        return {"agent_id": agent_id, "resumed": ok, "status": agent.status.value}

    def get_employee_stats(
        self, viewer: Optional[GoalViewer] = None
    ) -> Dict[str, Any]:
        """获取 Employee 整体统计（有 viewer 时只取自己可见的员工）。"""
        emp = self._ensure_employee()
        if not self._employee_visible(emp, viewer):
            # 默认员工不在可见范围时，退回"第一个可见员工"，绝不把别人的
            # 员工名与计数回给当前账号。
            emp = None
            store = self._store()
            for ename in store.list_employees():
                candidate = store.load(ename)
                if candidate is not None and self._employee_visible(candidate, viewer):
                    emp = candidate
                    break
        if emp is None:
            return {
                "name": "",
                "agent_count": 0,
                "total_tasks_submitted": 0,
                "total_tasks_completed": 0,
                "total_tasks_failed": 0,
                "total_goals_submitted": 0,
                "total_goals_completed": 0,
                "total_goals_failed": 0,
                "task_queue_length": 0,
            }
        return {
            "name": emp.name,
            "agent_count": len(emp.agents),
            "total_tasks_submitted": emp.total_tasks_submitted,
            "total_tasks_completed": emp.total_tasks_completed,
            "total_tasks_failed": emp.total_tasks_failed,
            "total_goals_submitted": emp.total_goals_submitted,
            "total_goals_completed": emp.total_goals_completed,
            "total_goals_failed": emp.total_goals_failed,
            "task_queue_length": len(emp.task_queue),
        }

    # ── 按名员工生命周期（by-name hire / list / remove） ──

    _SEED_DEFAULT_NAME = "liuhao-default"

    def _store(self) -> "EmployeeStore":
        """返回（懒初始化的）EmployeeStore 单例句柄。"""
        if self._employee_store is None:
            self._ensure_employee()  # 顺便把 store 建好
        return self._employee_store

    def _readonly_store(self) -> "EmployeeStore":
        """只读路径用：只建 store 句柄，**不**触发默认员工的构造。

        ``_store()`` 在空句柄上会调用 ``_ensure_employee()``，进而构造
        ``Employee(enable_observability=True)`` → 按需导入
        ``knowledge.memory``（mem0/openai/qdrant，实测 7–8 秒）。名册归属
        映射只需要读 JSON，不该为了拿一个 store 句柄付这笔成本。
        """
        if self._employee_store is not None:
            return self._employee_store
        from src.ai.employee_store import EmployeeStore

        self._employee_store = EmployeeStore()
        return self._employee_store

    # ── 员工实体的归属 / 可见性 / 解析 ──

    def _employee_owner(self, emp: "Employee") -> str:
        """员工的归属账号；老数据缺失字段时按本机默认账号处理。"""
        return str(getattr(emp, "owner_account_id", "") or "").strip() or default_account_id()

    def _employee_owner_entry(self, emp: "Employee") -> Dict[str, Any]:
        return {"owner_account_id": self._employee_owner(emp)}

    def _employee_visible(self, emp: "Employee", viewer: Optional[GoalViewer]) -> bool:
        """读：根账号看整棵树；成员只看自己的员工。"""
        if viewer is None:
            return True
        return viewer.sees(self._employee_owner_entry(emp))

    def _employee_writable(self, emp: "Employee", viewer: Optional[GoalViewer]) -> bool:
        """写：只允许改自己的员工（根账号对子树员工只读）。"""
        if viewer is None:
            return True
        return viewer.owns(self._employee_owner_entry(emp))

    def _visible_employee(
        self, name: str, viewer: Optional[GoalViewer]
    ) -> Optional["Employee"]:
        """按名解析**可见**员工；不存在与不可见一律 ``None``（不暴露存在与否）。"""
        emp = self._store().load(name)
        if emp is None:
            return None
        if not self._employee_visible(emp, viewer):
            return None
        return emp

    def _require_writable_employee(
        self, name: str, viewer: Optional[GoalViewer]
    ) -> "Employee":
        """写操作用的员工解析：不存在/不可见/非本人 → 同一个 404 文案。"""
        emp = self._visible_employee(name, viewer)
        if emp is None or not self._employee_writable(emp, viewer):
            raise GoalWorkError(
                404, "employee_not_found",
                "员工不存在或不在当前可见范围（不会改用其他员工）",
            )
        return emp

    def resolve_employee_agent(
        self, name: str, viewer: Optional[GoalViewer]
    ) -> "Tuple[Employee, Agent]":
        """把"员工名"解析成一位可用的真实 agent（指名派活的唯一入口）。"""
        emp = self._require_writable_employee(name, viewer)
        if not emp.is_active():
            raise GoalWorkError(
                409, "employee_disabled",
                f"员工 {name} 已停用，无法接受指派；请先启用后再派活",
            )
        agent = next(
            (a for a in emp.agents.values()
             if (a.status.value if hasattr(a.status, "value") else str(a.status)) != "paused"),
            None,
        )
        if agent is None:
            raise GoalWorkError(
                409, "employee_unavailable",
                f"员工 {name} 的所有 agent 都处于暂停状态，无法接受指派",
            )
        return emp, agent

    def _agent_employee_map(self) -> Dict[str, str]:
        """agent id -> 员工名（结果里"谁做了什么"的数据源）。带缓存。"""
        if self._agent_map_cache is not None:
            return self._agent_map_cache
        mapping: Dict[str, str] = {}
        store = self._readonly_store()
        # 只读摘要即可：``store.load()`` 会重建 Employee，默认开启可观测性时
        # 会按需导入 knowledge.memory（mem0 → openai → qdrant_client），实测
        # 让首个 ``GET /v1/goals`` 付 7 秒。归属映射只需要 agent id 与员工名。
        for row in store.list_summaries():
            name = str(row.get("name") or row.get("id") or "")
            if not name:
                continue
            for agent in row.get("agents") or []:
                agent_id = agent.get("id")
                if agent_id:
                    mapping[str(agent_id)] = name
        self._agent_map_cache = mapping
        return mapping

    def _employee_name_for_agent(self, agent_id: Optional[str]) -> Optional[str]:
        if not agent_id:
            return None
        return self._agent_employee_map().get(str(agent_id))

    def _refresh_agent_pool(self) -> None:
        """把**在职**员工的 agent 池与执行器解析表刷新进运行时的拆解器/执行器。

        只纳入本机默认账号名下的员工 —— 账号树里的子账号员工不会被别人的目标
        自动占用（指名派活仍然可以显式使用自己的员工），从而不引入新的跨账号
        可见性/占用。员工增删改停用后立即刷新，使后续派活不再指向停用员工。
        """
        runtime = self._runtime
        if runtime is None:
            return
        with self._employee_pool_lock:
            shared = self._shared_capability_executor
            if shared is None:
                return
            store = self._store()
            pool_owner = default_account_id()
            auto_ids: List[str] = []
            all_ids: List[str] = []
            roster: List[Dict[str, Any]] = []
            for ename in store.list_employees():
                emp = store.load(ename)
                if emp is None or not emp.is_active():
                    continue
                all_ids.extend(emp.agents.keys())
                if self._employee_owner(emp) == pool_owner:
                    auto_ids.extend(emp.agents.keys())
                    # 拆解器据此挑人：只暴露真实名字 / 专长 / agent id，
                    # 模型不可能编出不存在的员工。
                    roster.append(
                        {
                            "name": emp.name,
                            "role": str(getattr(emp, "role_description", "") or ""),
                            "capabilities": list(
                                getattr(emp, "default_capabilities", []) or []
                            ),
                            "agent_ids": list(emp.agents.keys()),
                        }
                    )
            if not all_ids:
                return
            executors = {
                agent_id: (
                    lambda capability_id, inputs, _exec=shared: _exec(capability_id, inputs)
                )
                for agent_id in all_ids
            }
            try:
                runtime.engine.decomposer.agent_ids = auto_ids or all_ids
                runtime.engine.executor.set_agent_resolver(executors.get)
                # 管家自己挑人的花名册 + 汇总用的 agent→员工名解析。
                set_provider = getattr(runtime, "set_employee_provider", None)
                if callable(set_provider):
                    set_provider(lambda: list(roster))
                if hasattr(runtime, "agent_employee_resolver"):
                    runtime.agent_employee_resolver = self._employee_name_for_agent
            except Exception as exc:  # noqa: BLE001 - 非真实运行时（测试替身）时跳过
                logger.debug("AIStateManager: agent pool refresh skipped: %s", exc)
            self._agent_map_cache = None

    # ── 已注册执行器能力目录（"默认能力"只能从中选） ──

    def capability_catalog(self) -> List[Dict[str, Any]]:
        """真实、已 ACTIVE 的执行器能力清单（员工默认能力的可选集合）。"""
        if self._capability_catalog_cache is not None:
            return self._capability_catalog_cache
        catalog: List[Dict[str, Any]] = []
        seen: Set[str] = set()
        try:
            from src.ai.lcore import LCore
            from src.ai.tool_registry import ToolStatus

            lcore = LCore(scope="L1", register_local_tools=True)
            for tool in lcore.tools._tools.values():
                if lcore.tools._status.get(tool.tool_id) != ToolStatus.ACTIVE:
                    continue
                if tool.capability in seen:
                    continue
                seen.add(tool.capability)
                catalog.append({
                    "id": tool.capability,
                    "name": tool.name,
                    "tool_id": tool.tool_id,
                    "description": tool.description,
                    "risk": tool.risk,
                })
        except Exception as exc:  # noqa: BLE001 - 目录不可用时不伪造能力
            logger.warning("AIStateManager: capability catalog unavailable: %s", exc)
        self._capability_catalog_cache = catalog
        return catalog

    def _validate_capabilities(self, capabilities: Optional[List[str]]) -> List[str]:
        """默认能力必须来自已注册执行器；未知能力 → 400 并逐条列出。"""
        if not capabilities:
            return []
        known = {item["id"] for item in self.capability_catalog()}
        unknown = [c for c in capabilities if c not in known]
        if unknown:
            raise GoalWorkError(
                400, "unknown_capability",
                "未知的默认能力（未注册或未激活）："
                + ", ".join(unknown)
                + "；可用能力：" + ", ".join(sorted(known)),
            )
        return list(dict.fromkeys(capabilities))

    #: 员工状态 → 界面文案（不暴露任何内部标识）。
    _STATUS_LABELS = {
        "idle": "空闲",
        "running": "执行中",
        "error": "出错",
        "completed": "已完成",
        "disabled": "已停用",
    }

    @classmethod
    def _last_activity_view(cls, emp: "Employee") -> Optional[Dict[str, Any]]:
        """把持久化的"最后一次活动"转成可直接上屏的字段（时间用 ISO 文本）。"""
        record = getattr(emp, "last_activity", None)
        if not isinstance(record, dict) or not record:
            return None
        description = str(record.get("description") or "").strip()
        if not description:
            return None
        outcome = str(record.get("outcome") or "").strip().lower()
        at_raw = record.get("at")
        at_text: Optional[str] = None
        try:
            from datetime import datetime, timezone

            if isinstance(at_raw, (int, float)):
                at_text = datetime.fromtimestamp(float(at_raw), tz=timezone.utc).isoformat()
        except (TypeError, ValueError, OSError):
            at_text = None
        return {
            "description": description,
            "outcome": outcome or None,
            "outcome_label": cls._STATUS_LABELS.get(outcome, ""),
            "at": at_text,
        }

    def _summarize_employee(
        self, emp: "Employee", viewer: Optional[GoalViewer] = None
    ) -> Dict[str, Any]:
        """把一个真实 Employee 转成列表摘要（含员工实体字段）。"""
        agents = [
            {
                "id": a.id,
                "agent_type": a.agent_type,
                "name": a.name,
                "status": a.status.value,
                "current_task": a.current_task,
                "completed_tasks": a.completed_tasks,
                "failed_tasks": a.failed_tasks,
                "total_latency_ms": a.total_latency_ms,
            }
            for a in emp.agents.values()
        ]
        owner = self._employee_owner(emp)
        read_only = bool(
            viewer is not None
            and self._employee_visible(emp, viewer)
            and not self._employee_writable(emp, viewer)
        )
        status = emp.current_status()
        return {
            "name": emp.name,
            # 面向界面的称呼：系统默认员工不在界面上暴露内部标识。
            "display_name": (
                "默认管家" if emp.name == self._SEED_DEFAULT_NAME else emp.name
            ),
            "employee_id": emp.stable_id(),
            "agent_count": len(emp.agents),
            "agents": agents,
            "role_description": getattr(emp, "role_description", "") or "",
            "default_capabilities": list(getattr(emp, "default_capabilities", []) or []),
            "owner_account_id": owner,
            "employment_status": getattr(emp, "employment_status", "active"),
            "read_only": read_only,
            # 状态 + 最后一次做了什么/什么时候：界面直接展示，不用自己拼。
            "status": status,
            "status_label": self._STATUS_LABELS.get(status, ""),
            "last_activity": self._last_activity_view(emp),
            "stats": {
                "name": emp.name,
                "agent_count": len(emp.agents),
                "total_tasks_submitted": emp.total_tasks_submitted,
                "total_tasks_completed": emp.total_tasks_completed,
                "total_tasks_failed": emp.total_tasks_failed,
                "total_goals_submitted": emp.total_goals_submitted,
                "total_goals_completed": emp.total_goals_completed,
                "total_goals_failed": emp.total_goals_failed,
                "task_queue_length": len(emp.task_queue),
            },
        }

    def hire_employee(
        self,
        name: str,
        agent_count: int = 3,
        agent_types: Optional[List[str]] = None,
        role_description: str = "",
        default_capabilities: Optional[List[str]] = None,
        owner_account_id: str = "",
    ) -> Dict[str, Any]:
        """按名雇佣（创建并持久化）一个新员工。

        幂等且**绝不静默覆盖**已存在的同名员工：若 ``name`` 已存在于 store，
        返回 ``already_existed=True`` 标记，由端点决定回 409。否则用
        ``EmployeeStore.create_employee`` 落盘后返回摘要。

        ``default_capabilities`` 只能是**已注册执行器**的 capability（未知值
        抛 ``GoalWorkError(400)``，逐条列出）；``owner_account_id`` 决定谁能看到
        和改这位员工。
        """
        validated_caps = self._validate_capabilities(default_capabilities)
        store = self._store()
        existing = store.load(name)
        if existing is not None:
            return {
                "created": False,
                "already_existed": True,
                "employee": self._summarize_employee(existing),
            }
        emp = store.create_employee(
            name=name,
            agent_count=agent_count,
            agent_types=agent_types,
            role_description=role_description,
            default_capabilities=validated_caps,
            owner_account_id=normalise_owner(owner_account_id),
        )
        self._agent_map_cache = None
        self._refresh_agent_pool()
        return {
            "created": True,
            "already_existed": False,
            "employee": self._summarize_employee(emp),
        }

    def hire_employees(
        self,
        items: List[Dict[str, Any]],
        owner_account_id: str = "",
    ) -> Dict[str, Any]:
        """批量雇佣：逐条创建并**如实返回每一条的结果**。

        - 新员工 → ``created``；
        - 已存在的同名员工 → ``existing``（绝不覆盖，也不让整批失败）；
        - 校验失败（未知能力等）→ ``failed``，带明确错误码与说明。
        任一条失败都不影响其它条目；全部结果都来自真实的 store 写入。
        """
        created: List[Dict[str, Any]] = []
        existing: List[Dict[str, Any]] = []
        failed: List[Dict[str, Any]] = []
        for item in items:
            name = str(item.get("name") or "").strip()
            if get_trust_manager().is_revoked(name):
                failed.append({
                    "name": name,
                    "code": "employee_revoked",
                    "message": (
                        f"「{name}」已被列入撤销名单，无法创建；"
                        "请先在「信任」页恢复它的信任状态"
                    ),
                })
                continue
            try:
                result = self.hire_employee(
                    name,
                    int(item.get("agent_count") or 3),
                    list(item.get("agent_types") or []) or None,
                    role_description=str(item.get("role_description") or ""),
                    default_capabilities=list(item.get("default_capabilities") or []),
                    owner_account_id=owner_account_id,
                )
            except GoalWorkError as exc:
                failed.append({"name": name, "code": exc.code, "message": exc.detail})
            except Exception as exc:  # noqa: BLE001 - 单条失败不拖垮整批
                logger.warning("batch hire %r failed: %s", name, exc)
                failed.append({
                    "name": name,
                    "code": "employee_create_failed",
                    "message": "创建没有完成，请稍后重试。",
                })
            else:
                (existing if result["already_existed"] else created).append(
                    result["employee"]
                )
        self._agent_map_cache = None
        self._refresh_agent_pool()
        return {
            "created": created,
            "existing": existing,
            "failed": failed,
            "created_count": len(created),
            "existing_count": len(existing),
            "failed_count": len(failed),
            "requested_count": len(items),
        }

    def employee_memories(
        self, name: str, viewer: Optional[GoalViewer] = None
    ) -> Dict[str, Any]:
        """某位员工的记忆：归属账号 = 员工所属账号，可见性沿用员工可见性。

        子账号只能看自己名下员工的记忆；主账号可看整棵子树（只读）。别人的
        员工与不存在的员工都是同一个 404，不暴露存在与否。
        """
        emp = self._visible_employee(name, viewer)
        if emp is None:
            raise GoalWorkError(
                404, "employee_not_found",
                "员工不存在或不在当前可见范围（不会改用其他员工）",
            )
        from .account_memory import list_employee_entries

        entries = list_employee_entries(self._employee_owner(emp), emp.stable_id())
        return {
            "employee": {
                "name": emp.name,
                "display_name": (
                    "默认管家" if emp.name == self._SEED_DEFAULT_NAME else emp.name
                ),
                "employee_id": emp.stable_id(),
            },
            "owner_account_id": self._employee_owner(emp),
            "memories": entries,
            "count": len(entries),
        }

    def adopt_employee(
        self,
        name: str,
        viewer: Optional[GoalViewer] = None,
        *,
        as_name: str = "",
        owner_account_id: str = "",
    ) -> Dict[str, Any]:
        """根账号把子树员工/技能**复制**到自己的空间（只读源，不改源）。

        刻意不实现"根直接往子账号空间里派活"：那样会让子账号看到主账号的
        动作。取用后的副本 ``owner_account_id`` 是根自己，任务在根空间执行，
        子账号侧员工列表、任务列表与用量都不出现任何痕迹。

        来源（账号 + 员工名）只回给调用方并写审计，用于追溯。
        """
        if viewer is None or not viewer.is_root:
            raise GoalWorkError(
                404, "employee_not_found",
                "员工不存在或不在当前可见范围（不会改用其他员工）",
            )
        source = self._visible_employee(name, viewer)
        if source is None:
            raise GoalWorkError(
                404, "employee_not_found",
                "员工不存在或不在当前可见范围（不会改用其他员工）",
            )
        source_owner = self._employee_owner(source)
        root_owner = viewer.principal
        if owner_account_id and normalise_owner(owner_account_id) != source_owner:
            raise GoalWorkError(
                404, "employee_not_found",
                "员工不存在或不在当前可见范围（不会改用其他员工）",
            )
        if source_owner == root_owner:
            raise GoalWorkError(
                400, "employee_already_owned",
                "该员工已在你的工作空间，无需取用",
            )

        target_name = (as_name or "").strip() or f"{source.name}-{source_owner}"
        if self._store().load(target_name) is not None:
            raise GoalWorkError(
                409, "employee_exists",
                f"员工 {target_name!r} 已存在；请换一个名字再取用（不会覆盖已有员工）",
            )

        agent_types = [
            str(getattr(agent, "agent_type", "") or "general")
            for agent in (source.agents or {}).values()
        ] or [str(item) for item in (getattr(source, "agent_types", []) or [])]
        result = self.hire_employee(
            name=target_name,
            agent_count=max(1, len(agent_types)),
            agent_types=agent_types,
            role_description=source.role_description,
            default_capabilities=list(source.default_capabilities or []),
            owner_account_id=root_owner,
        )
        from src.kernels.identity import get_identity_manager

        get_identity_manager().record_event(
            action="employee.adopt",
            actor=root_owner,
            target=root_owner,
            outcome="allowed",
            details={
                "source_account": source_owner,
                "source_employee": source.name,
                "target_employee": target_name,
            },
        )
        return {
            **result,
            "employee": {
                **result["employee"],
                "adopted_from": {
                    "account_id": source_owner,
                    "employee_name": source.name,
                },
            },
        }

    def list_all_employees(
        self, viewer: Optional[GoalViewer] = None
    ) -> List[Dict[str, Any]]:
        """列出**当前账号可见**的真实员工（不止默认），含员工实体字段。

        成员只看自己的员工；账号树根可以看整棵树（``read_only=True``），但
        改/删/停用仍然只作用于自己的员工。
        """
        store = self._store()
        result: List[Dict[str, Any]] = []
        # 一次读库、批量重建：逐个 load() 会把整份员工 JSON 重读 N 遍，
        # 1000 名员工时列表本身就要几分钟。
        for emp in store.load_many():
            if not self._employee_visible(emp, viewer):
                continue
            result.append(self._summarize_employee(emp, viewer))
        return result

    def remove_employee(
        self, name: str, viewer: Optional[GoalViewer] = None
    ) -> bool:
        """移除一名员工。

        硬保护：拒绝删除 seed 默认员工 ``liuhao-default``（网关
        ``_ensure_employee`` 依赖它，删掉会让后续请求 500）。返回 ``True``
        表示删除成功；``False`` 表示该员工本就不存在（或不在当前可见范围，
        与"不存在"同响应，不暴露存在与否）。
        """
        if name == self._SEED_DEFAULT_NAME:
            raise ValueError(
                f"refusing to remove the seed default employee {name!r} "
                f"(the gateway depends on it)"
            )
        emp = self._visible_employee(name, viewer)
        if emp is None or not self._employee_writable(emp, viewer):
            return False
        store = self._store()
        removed = store.remove(name)
        if removed:
            self._agent_map_cache = None
            self._refresh_agent_pool()
        return removed

    def update_employee(
        self,
        name: str,
        viewer: Optional[GoalViewer] = None,
        *,
        new_name: Optional[str] = None,
        role_description: Optional[str] = None,
        default_capabilities: Optional[List[str]] = None,
        employment_status: Optional[str] = None,
    ) -> Dict[str, Any]:
        """改名 / 改职责 / 改默认能力 / 停用启用（只作用于自己的员工）。

        - 不存在/不可见/非本人 → 404（与"不存在"同文案）；
        - 改成已存在的名字 → 409；
        - seed 默认员工不允许改名/停用 → 403（网关依赖它）。
        """
        emp = self._require_writable_employee(name, viewer)
        # 先校验/暂存字段修改，等改名完成后再落到**改名后的那份对象**上，
        # 否则"改名 + 改职责/停用"同时提交时，rename 返回的新对象会把刚改的
        # 字段覆盖回旧值（真实现象：改名成功但职责/状态没保存）。
        next_status: Optional[str] = None
        if employment_status is not None:
            status = str(employment_status).strip().lower()
            if status not in (emp.ACTIVE, emp.DISABLED):
                raise GoalWorkError(
                    400, "invalid_status",
                    f"employment_status 只能是 active/disabled，收到：{employment_status}",
                )
            if status == emp.DISABLED and emp.name == self._SEED_DEFAULT_NAME:
                raise GoalWorkError(
                    403, "seed_protected",
                    f"拒绝停用 seed 默认员工 {name!r}（网关依赖它）",
                )
            next_status = status
        next_role = role_description
        next_caps = (
            self._validate_capabilities(default_capabilities)
            if default_capabilities is not None
            else None
        )
        renamed = False
        if new_name is not None and str(new_name).strip() != emp.name:
            candidate = str(new_name).strip()
            if emp.name == self._SEED_DEFAULT_NAME:
                raise GoalWorkError(
                    403, "seed_protected",
                    f"拒绝重命名 seed 默认员工 {name!r}（网关依赖它）",
                )
            store = self._store()
            if store.load(candidate) is not None:
                raise GoalWorkError(
                    409, "employee_name_taken", f"员工名已被占用：{candidate}"
                )
            renamed_emp = store.rename(emp.name, candidate)
            if renamed_emp is None:
                raise GoalWorkError(404, "employee_not_found",
                                    f"员工不存在或不在当前可见范围：{name}")
            emp = renamed_emp
            renamed = True

        if next_status is not None:
            emp.employment_status = next_status
        if next_role is not None:
            emp.role_description = next_role
        if next_caps is not None:
            emp.default_capabilities = next_caps
        if renamed or next_status is not None or next_role is not None or next_caps is not None:
            self._store().save(emp)
        self._agent_map_cache = None
        self._refresh_agent_pool()
        return {
            "updated": True,
            "renamed": renamed,
            "employee": self._summarize_employee(emp, viewer),
        }

    def _find_employee_with_agent(
        self, agent_id: str
    ) -> "Tuple[Optional[Employee], Optional[Agent]]":
        """跨所有员工按 agent id 解析出 (employee, agent)。

        默认员工（``_ensure_employee`` 持有的内存实例）优先匹配，使其仍是
        真实来源、pause/resume 经 ``_persist_employee`` 落盘；其余按名员工从
        store 按需加载。agent id 全局唯一（``<employee>-a<i>``），因此不会歧义。
        """
        default = self._ensure_employee()
        agent = default.agents.get(agent_id)
        if agent is not None:
            return default, agent
        store = self._store()
        for ename in store.list_employees():
            if ename == default.name:
                continue
            emp = store.load(ename)
            if emp is None:
                continue
            agent = emp.agents.get(agent_id)
            if agent is not None:
                return emp, agent
        return None, None

    # ── 内部工具 ──

    @staticmethod
    def _result_to_dict(result, natural_language: str, scope: str) -> Dict[str, Any]:
        """把 AgentRunResult 转为可序列化的 dict。"""
        # 提取 trace
        trace_entries = []
        if result.trace:
            for entry in result.trace.entries if hasattr(result.trace, 'entries') else []:
                trace_entries.append({
                    "task_id": entry.task_id,
                    "event_type": entry.event_type,
                    "ts": str(entry.ts) if entry.ts else None,
                    "capability": entry.capability,
                    "decision": entry.decision,
                    "output": entry.output if hasattr(entry, 'output') else None,
                    "error": entry.error if hasattr(entry, 'error') else None,
                    "correlation_id": entry.correlation_id,
                })

        # 提取 context 中的 task 信息
        tasks = []
        # Bound unconditionally: line 923 below reads ``plan_tasks`` on EVERY
        # path, including ``result.context`` being falsy. Binding it only inside
        # the ``if`` made that read raise UnboundLocalError in background goal
        # threads (observed in verify_employee_goal_binding.py /
        # verify_http_full_closure.py logs) -- a crash the gates never counted.
        from ..kernels.execution import task_result_text

        plan_tasks = None
        if result.context:
            ctx = result.context
            # The tasks live on the PLAN, not on the context: ``ExecutionContext``
            # has no ``tasks`` field at all (src/kernels/execution/__init__.py:212),
            # so the old ``hasattr(ctx, "tasks")`` guard was always False and this
            # list was ALWAYS empty -- every goal detail served over HTTP rendered
            # as "did nothing, no tasks". Task status is how an operator decides
            # whether a "completed" goal actually completed anything, so read the
            # plan and fall back to the old attribute if a future shape has one.
            plan_tasks = getattr(getattr(ctx, "plan", None), "tasks", None)
            ctx_tasks = plan_tasks if plan_tasks is not None else getattr(ctx, "tasks", None)
            if ctx_tasks:
                for t in ctx_tasks:
                    tasks.append({
                        "id": t.id,
                        "name": t.name,
                        "description": t.description,
                        "status": t.status.value if hasattr(t.status, 'value') else str(t.status),
                        "capability_id": t.capability_id,
                        "assigned_agent": t.assigned_agent,
                        # 为什么派给这个人：拆解时按能力匹配得出，随结果上屏。
                        "assignment_reason": getattr(t, "assignment_reason", None),
                        # 终态不能只看 status：completed 也可能没有任何产出。
                        "produced": bool(getattr(t, "produced", False)),
                        "artifacts": list(getattr(t, "artifacts", []) or []),
                        # 崩溃重启后单任务重试要靠这份快照按原计划重建任务；
                        # 重启后拆解器可能给出不同任务名，不能只存名字。
                        "inputs": AIStateManager._json_safe_inputs(t.inputs),
                        "dependencies": list(t.dependencies or []),
                        "max_retries": t.max_retries,
                        "timeout_seconds": t.timeout_seconds,
                        # 不能 str(dict)：页面上会出现 {'key': 'value'} 原始结构。
                        # 统一走内核的可读渲染（含来源链接与抓取时间）。
                        "result": task_result_text(t.result)[:1200] if t.result else None,
                        "error": t.error,
                        "started_at": str(t.started_at) if t.started_at else None,
                        "completed_at": str(t.completed_at) if t.completed_at else None,
                    })

        # 提取 evaluation
        evaluation = None
        if result.evaluation:
            ev = result.evaluation
            evaluation = {
                # 枚举成员用 .value 上屏：str(enum) 会变成
                # "EvaluationOutcome.PARTIAL"，前端认不出来。
                "outcome": (
                    ev.outcome.value
                    if hasattr(ev.outcome, "value")
                    else (str(ev.outcome) if getattr(ev, "outcome", None) else None)
                ),
                "replan_required": getattr(ev, 'replan_required', False),
                "replan_triggered": getattr(ev, 'replan_triggered', False),
                "summary": getattr(ev, 'summary', ''),
            }
        else:
            state_text = (
                result.state.value if hasattr(result.state, 'value') else str(result.state)
            )
            if state_text in ("failed", "cancelled"):
                # 老记录/更早的失败路径可能没有 evaluation。这里补的仍然只有
                # 证据：一句"未完成 + 原因类型 + 没有产物路径"，不编结果。
                from ..ai.llm_support import failure_summary

                evaluation = {
                    "outcome": _outcome_label("failed"),
                    "replan_required": True,
                    "replan_triggered": False,
                    "summary": failure_summary(result.error),
                }

        # Collect REAL produced artifacts: workspace files a file_write task
        # actually wrote. We only record a path when the task completed AND its
        # output carries an absolute `path` that provably sits inside a workspace
        # root (the one the tool itself used, falling back to the live
        # workspace_root()). Nothing is invented; if no file was written the list
        # stays empty.
        artifacts = AIStateManager._collect_artifacts(plan_tasks or [])

        return {
            "goal_id": result.goal_id,
            "state": result.state.value if hasattr(result.state, 'value') else str(result.state),
            "natural_language": natural_language,
            "scope": scope,
            "correlation_id": result.correlation_id,
            "error": result.error,
            "replan_count": result.replan_count,
            "replan_suggested": result.replan_suggested,
            "memory_keys": result.memory_keys,
            "persist_errors": result.persist_errors,
            "trace": trace_entries,
            "tasks": tasks,
            "artifacts": artifacts,
            # 派活前就说过的话（能力边界），与结论分开保存，便于 UI 前置展示。
            "notices": list(getattr(result, "preflight_notices", []) or []),
            "evaluation": evaluation,
            "created_at": time.time(),
        }

    @staticmethod
    def _json_safe_inputs(value: Any) -> Dict[str, Any]:
        """任务 inputs 会随 goals JSON 落盘，必须可序列化。"""
        if not isinstance(value, dict):
            return {}
        try:
            return json.loads(json.dumps(value, ensure_ascii=False, default=str))
        except (TypeError, ValueError):
            return {str(k): str(v) for k, v in value.items()}

    @staticmethod
    def _collect_artifacts(plan_tasks: List[Any]) -> List[str]:
        """Return workspace-relative paths of files the goal really produced.

        Walks the plan's tasks. For each completed ``file_write`` task whose
        output carries a concrete ``path``, it proves the path lands inside the
        workspace (using the root the tool reported, else the live
        :func:`workspace_root`) and records the relative path. Deduped; never
        invents a path. A path outside the workspace (or with no readable root)
        is skipped fail-closed.
        """
        produced: List[str] = []
        seen: set[str] = set()
        for t in plan_tasks:
            if getattr(t, "capability_id", None) != "file_write":
                continue
            tstatus = t.status.value if hasattr(t.status, "value") else str(t.status)
            # ARM1: ``completed_unattributed`` means the work ran and produced
            # its side effects, but was attributed to an agent that could not be
            # resolved. The artifact is REAL and must still be reported -- the
            # unresolved attribution is a separate concern from whether bytes
            # landed. Excluding it here would hide real work, which is its own
            # dishonesty.
            if tstatus not in ("completed", "completed_unattributed"):
                continue
            res = t.result
            if not isinstance(res, dict):
                continue
            # 写失败的回执里也可能带着 path（例如权限不足），那不是一个
            # 真实产物；只有明确成功才算数。
            if res.get("success") is False:
                continue
            written = res.get("path")
            if not isinstance(written, str) or not written:
                continue
            # Prefer the exact root the tool wrote into; fall back to the live
            # workspace root.
            root = res.get("workspace_root")
            root = root if isinstance(root, str) and root else workspace_root()
            try:
                ws_root = os.path.abspath(root)
                abs_path = os.path.abspath(written)
            except (TypeError, ValueError, OSError):
                continue
            if abs_path == ws_root:
                rel = ""
            elif abs_path.startswith(ws_root + os.sep):
                rel = os.path.relpath(abs_path, ws_root)
            else:
                # Outside the workspace: do not record (fail-closed).
                continue
            # Normalize to forward slashes for a stable, portable reference.
            rel = rel.replace(os.sep, "/")
            if rel and rel not in seen:
                seen.add(rel)
                produced.append(rel)
        return produced

    @staticmethod
    def _collect_artifacts_from_cards(tasks: List[Dict[str, Any]]) -> List[str]:
        """从**落盘任务卡**恢复真实产物（进程被杀后的重启路径）。

        崩溃时最后一次进度快照不包含产物列表，只能按磁盘事实重算：
        任务已 ``completed``、能力是 ``file_write``、其 ``inputs.path`` 指向
        的文件确实存在且非空、并落在工作区内。任一条件不满足就不记，
        绝不把半截/不存在的文件说成产物。
        """
        root = os.path.abspath(workspace_root())
        produced: List[str] = []
        seen: set[str] = set()
        for task in tasks:
            if not isinstance(task, dict):
                continue
            if str(task.get("status") or "").lower() not in (
                "completed", "completed_unattributed"
            ):
                continue
            if str(task.get("capability_id") or "") != "file_write":
                continue
            path = (task.get("inputs") or {}).get("path")
            if not isinstance(path, str) or not path:
                continue
            # 账号分区后产物实际落在 ``accounts/<owner>/``：按任务 owner 还原
            # 物理候选路径（只影响"找得到文件"，不改调度与任务输入）。
            owner = str(
                task.get("owner_account_id") or task.get("owner") or ""
            ).strip()
            candidates = [path]
            if owner and not path.startswith("accounts/"):
                candidates.insert(0, f"accounts/{owner}/{path.lstrip('/')}")
            abs_path = None
            for candidate in candidates:
                probe = os.path.abspath(
                    candidate if os.path.isabs(candidate) else os.path.join(root, candidate)
                )
                if probe != root and not probe.startswith(root + os.sep):
                    continue  # 工作区外，不记录
                try:
                    if os.path.isfile(probe) and os.path.getsize(probe) > 0:
                        abs_path = probe
                        break
                except OSError:
                    continue
            if abs_path is None:
                continue
            rel = os.path.relpath(abs_path, root).replace(os.sep, "/")
            if rel and rel not in seen:
                seen.add(rel)
                produced.append(rel)
        return produced


# ─── REST 端点 ───────────────────────────────────────────────

@router.get("/goals")
def list_goals(viewer: Optional[GoalViewer] = Depends(_goal_viewer)) -> Dict[str, Any]:
    """列出**当前账号可见**的 Goal 及其状态（别人的目标连计数都不进入）。"""
    viewer = _as_viewer(viewer)
    mgr = AIStateManager()
    goals = mgr.list_goals(viewer)
    return {"goals": goals, "count": len(goals)}


@router.post("/goals")
def create_goal(
    req: GoalCreateRequest,
    viewer: Optional[GoalViewer] = Depends(_goal_viewer),
) -> Dict[str, Any]:
    """创建并执行一个新 Goal。

    调用 AgentRuntime.run_goal，走完整链：
    plan → execute → evaluate → persist。

    ``background=true`` 时只在后台启动执行并立即返回 ``state:"running"``，
    使 ``GET /v1/goals/{id}`` 可被轮询、``POST /v1/goals/{id}/stop`` 能真的
    终止一次运行中的执行 —— 没有这个开关，停止能力在 HTTP 面上永远不可达，
    因为目标在 POST 返回前就已经跑完了。
    """
    viewer = _as_viewer(viewer)
    mgr = AIStateManager()
    try:
        pinned_agent: Optional[str] = None
        if req.employee_name:
            # 指名派活：解析成该员工的实际 agent；不可见/停用在这里就拒绝，
            # 绝不悄悄回退成"随便派给默认员工"。
            _employee, _agent = mgr.resolve_employee_agent(req.employee_name, viewer)
            pinned_agent = _agent.id
        return mgr.create_and_execute_goal(
            natural_language=req.natural_language,
            scope=req.scope,
            plan_mode=req.plan_mode,
            verification_criteria=req.verification_criteria,
            background=req.background,
            project_id=req.project_id,
            pinned_agent=pinned_agent,
            owner_account_id=viewer.principal if viewer is not None else "",
        )
    except GoalWorkError as exc:
        raise _http_from_goal_error(exc) from exc
    except ValueError as exc:
        # 例如 project_id 不在调用者的可见范围：与"资源不存在"必须逐字同形，
        # 否则越权请求会拿到一条能区分"存在但无权"的文案。
        from ..kernels.identity import _accounts as _acc
        from .account_context import deny

        logger.warning("goal execution rejected: %s", exc)
        raise deny(
            404, _acc.CODE_SUBTREE_DENIED, _acc.NEUTRAL_NOT_FOUND_MESSAGE
        ) from exc
    except Exception as exc:
        logger.warning("goal execution failed: %s", exc)
        raise HTTPException(
            status_code=503,
            detail="目标执行没有完成。请稍后重试；如果持续出现，请重启软件后再试。",
        ) from exc


@router.get("/goals/{goal_id}")
def get_goal(
    goal_id: str,
    viewer: Optional[GoalViewer] = Depends(_goal_viewer),
) -> Dict[str, Any]:
    """获取 Goal 执行详情（不可见与不存在返回同一个 404）。"""
    viewer = _as_viewer(viewer)
    mgr = AIStateManager()
    try:
        return mgr.get_goal(goal_id, viewer)
    except GoalWorkError as exc:
        raise _http_from_goal_error(exc) from exc
    except ValueError as exc:
        logger.warning("get goal rejected goal_id=%r: %s", goal_id, exc)
        raise HTTPException(
            status_code=404,
            detail="要查看的目标不存在，或不属于当前账号。请刷新列表后重试。",
        ) from exc


@router.post("/goals/{goal_id}/replan")
def replan_goal(
    goal_id: str,
    viewer: Optional[GoalViewer] = Depends(_goal_viewer),
) -> Dict[str, Any]:
    """对失败的 Goal 执行 replan（只限自己的目标）。"""
    viewer = _as_viewer(viewer)
    mgr = AIStateManager()
    try:
        return mgr.replan_goal(goal_id, viewer)
    except GoalWorkError as exc:
        raise _http_from_goal_error(exc) from exc
    except ValueError as exc:
        logger.warning("replan rejected: %s", exc)
        raise HTTPException(
            status_code=404,
            detail="要重新规划的目标不存在或已被删除。请刷新列表后重试。",
        ) from exc
    except Exception as exc:
        logger.warning("replan failed: %s", exc)
        raise HTTPException(
            status_code=503,
            detail="重新规划没有完成。请稍后重试；如果持续出现，请重启软件后再试。",
        ) from exc


@router.post("/goals/{goal_id}/stop")
def stop_goal(
    goal_id: str,
    viewer: Optional[GoalViewer] = Depends(_goal_viewer),
) -> Dict[str, Any]:
    """人类操作员中止一个正在运行的 Goal（主权控制）。

    控制台「中止」按钮的落点。若目标仍在后台运行，设置其 stop flag，执行循环
    会在任务边界干净抛出 GoalCancelled，目标被置为 ``cancelled``（绝不伪造成功）；
    若目标已结束则幂等标记为 ``cancelled``。人类喊停动作会被审计。
    只允许中止自己的目标（别人的一律 404）。
    """
    viewer = _as_viewer(viewer)
    mgr = AIStateManager()
    try:
        return mgr.stop_goal(goal_id, viewer)
    except GoalWorkError as exc:
        raise _http_from_goal_error(exc) from exc
    except ValueError as exc:
        logger.warning("stop goal rejected goal_id=%r: %s", goal_id, exc)
        raise HTTPException(
            status_code=404,
            detail="要停止的目标不存在，或不属于当前账号。请刷新列表后重试。",
        ) from exc


@router.post("/goals/assign", status_code=201)
def assign_goal(
    req: AssignTaskRequest,
    viewer: Optional[GoalViewer] = Depends(_goal_viewer),
) -> Dict[str, Any]:
    """把一件事**直接指派**给指定员工执行（独立于"一句话自动拆解"入口）。

    与 ``POST /v1/goals`` 的差别只在"谁决定执行主体"：
    * 这里由调用方点名 ``employee_name`` 或 ``agent_id``，**必须真实存在**
      （否则 404），绝不静默换成别的员工；
    * ``POST /v1/goals`` 仍由管家自己拆解、自己派活（可选 ``employee_name``
      把整个目标钉给某位员工）。

    权限沿用现有模型：要求人类主权令牌（与 ``POST /v1/goals`` 同一道
    ``require_human_principal``），不因为多了这个入口而放宽。
    ``background=true``（默认）时立即返回 ``running``，随后
    ``GET /v1/goals/{id}`` 会逐条给出任务进度。
    """
    viewer = _as_viewer(viewer)
    mgr = AIStateManager()
    try:
        entry = mgr.assign_task(
            agent_id=req.agent_id,
            employee_name=req.employee_name,
            instruction=req.instruction,
            scope=req.scope,
            project_id=req.project_id,
            background=req.background,
            principal=viewer.principal if viewer is not None else "",
            owner_account_id=viewer.principal if viewer is not None else "",
            viewer=viewer,
        )
        return entry
    except GoalWorkError as exc:
        raise _http_from_goal_error(exc) from exc
    except ValueError as exc:
        logger.warning("assignment rejected: %s", exc)
        raise HTTPException(
            status_code=404,
            detail="要派发的目标或员工不存在。请刷新列表后重试。",
        ) from exc
    except Exception as exc:
        logger.warning("assignment failed: %s", exc)
        raise HTTPException(
            status_code=503,
            detail="任务派发没有完成。请稍后重试；如果持续出现，请重启软件后再试。",
        ) from exc


@router.post("/goals/{goal_id}/tasks/{task_id}/retry")
def retry_goal_task(
    goal_id: str,
    task_id: str,
    viewer: Optional[GoalViewer] = Depends(_goal_viewer),
) -> Dict[str, Any]:
    """只重跑一个失败任务，其余任务的结果原样保留，并记录审计。

    只允许重试**自己的**任务；别人的目标（以及不存在的目标）统一 404，
    文案相同 —— 不暴露存在与否。

    明确拒绝的情况（都有稳定 ``code``）：
    * ``goal_not_found`` / ``task_not_found`` — 目标或任务不存在（404）；
    * ``goal_running`` — 目标还在跑，等结束再重试（409）；
    * ``task_not_retryable`` — 该任务不是 ``failed``（例如已成功），
      绝不顺带重跑其它任务（409）。
    """
    viewer = _as_viewer(viewer)
    mgr = AIStateManager()
    try:
        return mgr.retry_task(
            goal_id=goal_id,
            task_id=task_id,
            principal=viewer.principal if viewer is not None else "",
            viewer=viewer,
        )
    except GoalWorkError as exc:
        raise HTTPException(
            status_code=exc.status_code,
            detail={"code": exc.code, "message": exc.detail},
        ) from exc
    except Exception as exc:
        logger.warning("task retry failed: %s", exc)
        raise HTTPException(
            status_code=503,
            detail="重试任务没有完成。请稍后重试；如果持续出现，请重启软件后再试。",
        ) from exc


@router.get("/employees")
def list_employees(
    viewer: Optional[GoalViewer] = Depends(_goal_viewer),
) -> Dict[str, Any]:
    """列出**当前账号可见**的真实 AI Employee（来自 EmployeeStore，按名）。

    成员只看自己的员工；账号树根可以看整棵树（``read_only=True``）。
    返回 ``employees``（全员摘要列表）作为 P1「按名员工名册」的真实契约；
    同时保留 ``agents``/``count``/``stats``（可见员工的 agent pool），
    以保持现有 console 运行时 Tab 的向后兼容。
    """
    viewer = _as_viewer(viewer)
    mgr = AIStateManager()
    try:
        all_emps = mgr.list_all_employees(viewer)
        default_agents = mgr.list_agents(viewer)
        default_stats = mgr.get_employee_stats(viewer)
        return {
            "employees": all_emps,
            "employee_count": len(all_emps),
            "agents": default_agents,
            "count": len(default_agents),
            "stats": default_stats,
        }
    except Exception as exc:
        logger.warning("employee listing failed: %s", exc)
        raise HTTPException(
            status_code=503,
            detail="读取员工列表没有完成。请稍后重试。",
        ) from exc


@router.get("/employees/capabilities")
def list_employee_capabilities() -> Dict[str, Any]:
    """可选的"默认能力"清单：只列**已注册且 ACTIVE**的本地执行器。

    创建员工时 ``default_capabilities`` 只能从这份清单里选，未知能力会被
    400 拒绝并逐条列出 —— 不让人以为随便写个字符串就能用。
    """
    mgr = AIStateManager()
    catalog = mgr.capability_catalog()
    return {
        "capabilities": catalog,
        "count": len(catalog),
        "ids": [item["id"] for item in catalog],
    }


@router.post("/employees")
def hire_employee(
    req: HireEmployeeRequest,
    viewer: Optional[GoalViewer] = Depends(_goal_viewer),
) -> Dict[str, Any]:
    """按名雇佣一个新 AI Employee（归属调用方账号）。

    若 ``name`` 已存在 → 409（绝不静默覆盖）；创建成功 → 201。
    """
    viewer = _as_viewer(viewer)
    mgr = AIStateManager()
    # 真实信任决议（LHX-C-010）：被撤销的主体不得被（重新）雇佣。
    # 默认注册表为空，``is_revoked`` 对任何主体返回 False，故正常路径不受影响
    # （fail-open-but-meaningful：撤销名单里没有的人照常可雇）。
    if get_trust_manager().is_revoked(req.name):
        # 原始判定原因（信任注册表 / 撤销主体）只进日志，界面只说"发生了什么 +
        # 下一步"。
        logger.warning("hire refused: %r is revoked in the trust registry", req.name)
        raise HTTPException(
            status_code=403,
            detail=(
                f"「{req.name}」已被列入撤销名单，无法创建或重新启用。"
                "请先在「信任」页恢复它的信任状态，或换一个员工名再试。"
            ),
        )
    try:
        result = mgr.hire_employee(
            req.name,
            req.agent_count,
            req.agent_types,
            role_description=req.role_description,
            default_capabilities=req.default_capabilities,
            owner_account_id=viewer.principal if viewer is not None else "",
        )
    except GoalWorkError as exc:
        raise _http_from_goal_error(exc) from exc
    except ValueError as exc:
        logger.warning("hire employee rejected name=%r: %s", req.name, exc)
        raise HTTPException(
            status_code=400,
            detail="员工信息没有通过校验，本次创建没有完成。请检查名称、数量和类型后重试。",
        ) from exc
    if result["already_existed"]:
        raise HTTPException(
            status_code=409,
            detail=(
                f"已经有一个叫「{req.name}」的员工了。请换一个名字，"
                "或先在「我的员工」里查看现有员工。"
            ),
        )
    _audit_employee_action(
        "employee.hired",
        viewer,
        req.name,
        details={
            "employee_id": result["employee"].get("id") or result["employee"].get("name"),
            "agent_count": req.agent_count,
        },
    )
    return JSONResponse(status_code=201, content=result["employee"])


@router.post("/employees/batch", status_code=201)
def hire_employees_batch(
    req: BatchHireEmployeesRequest,
    viewer: Optional[GoalViewer] = Depends(_goal_viewer),
) -> Dict[str, Any]:
    """批量雇佣员工。

    逐条创建并如实返回每一条的结果：``created`` / ``existing`` / ``failed``。
    重名不会覆盖、也不会让整批失败；未知能力等校验失败会带明确原因。
    """
    viewer = _as_viewer(viewer)
    mgr = AIStateManager()
    items = [item.model_dump() for item in req.employees]
    result = mgr.hire_employees(
        items,
        owner_account_id=viewer.principal if viewer is not None else "",
    )
    for employee in result["created"]:
        name = str(employee.get("name") or "")
        if name:
            _audit_employee_action(
                "employee.hired",
                viewer,
                name,
                details={
                    "employee_id": employee.get("id") or name,
                    "batch": True,
                },
            )
    status_code = 201 if result["created_count"] else 200
    return JSONResponse(status_code=status_code, content=result)


@router.post("/employees/adopt", status_code=201)
def adopt_employee(
    req: AdoptEmployeeRequest,
    viewer: Optional[GoalViewer] = Depends(_goal_viewer),
) -> Dict[str, Any]:
    """根把子账号员工/技能复制到自己的空间（仅根可用；成员一律 404）。

    副本归属根自己，任务在根空间执行；来源账号与员工名只回给根并写审计，
    子账号侧员工列表、任务列表与用量都不会出现这次取用。
    """
    viewer = _as_viewer(viewer)
    mgr = AIStateManager()
    try:
        return mgr.adopt_employee(
            req.name,
            viewer,
            as_name=req.as_name,
            owner_account_id=req.owner,
        )
    except GoalWorkError as exc:
        raise _http_from_goal_error(exc) from exc
    except ValueError as exc:
        logger.warning("adopt employee rejected: %s", exc)
        raise HTTPException(
            status_code=400,
            detail="取用没有完成。请检查员工名后重试；如果它不属于当前账号，请换一个。",
        ) from exc


@router.patch("/employees/{name}")
def update_employee(
    name: str,
    req: EmployeeUpdateRequest,
    viewer: Optional[GoalViewer] = Depends(_goal_viewer),
) -> Dict[str, Any]:
    """改名 / 改职责 / 改默认能力 / 停用启用（只能改自己的员工）。

    - 别人的员工与不存在的员工统一 404（不暴露存在与否）；
    - ``{"employment_status": "disabled"}`` 停用；``"active"`` 启用。
    """
    viewer = _as_viewer(viewer)
    mgr = AIStateManager()
    try:
        result = mgr.update_employee(
            name,
            viewer,
            new_name=req.name,
            role_description=req.role_description,
            default_capabilities=req.default_capabilities,
            employment_status=req.employment_status,
        )
    except GoalWorkError as exc:
        raise _http_from_goal_error(exc) from exc
    except ValueError as exc:
        logger.warning("update employee rejected name=%r: %s", name, exc)
        raise HTTPException(
            status_code=400,
            detail="员工信息没有通过校验，本次修改没有完成。请检查填写内容后重试。",
        ) from exc
    employee = result.get("employee") or {}
    final_name = str(employee.get("name") or req.name or name)
    _audit_employee_action(
        "employee.updated",
        viewer,
        final_name,
        details={
            "previous_name": name,
            "renamed": bool(result.get("renamed")),
            "employment_status": employee.get("employment_status"),
        },
    )
    return result


@router.delete("/employees/{name}")
def remove_employee(
    name: str,
    viewer: Optional[GoalViewer] = Depends(_goal_viewer),
) -> Dict[str, Any]:
    """移除一名按名员工（只能删自己的）。

    - 删除 seed 默认 ``liuhao-default`` → 403（保护网关依赖）；
    - 不存在/不可见/别人的员工 → 404；成功 → 200。
    """
    viewer = _as_viewer(viewer)
    mgr = AIStateManager()
    try:
        removed = mgr.remove_employee(name, viewer)
    except ValueError as exc:
        logger.warning("remove employee rejected name=%r: %s", name, exc)
        raise HTTPException(
            status_code=403,
            detail=(
                "这名员工不能移除。系统默认员工需要保留；"
                "如果你只是想让它停用，请改用「停用」。"
            ),
        ) from exc
    if not removed:
        raise HTTPException(
            status_code=404,
            detail=f"没有找到员工「{name}」。请刷新列表后重试。",
        )
    _audit_employee_action("employee.removed", viewer, name)
    return {"removed": name, "ok": True}


@router.get("/employees/{agent_id}")
def get_employee(
    agent_id: str,
    viewer: Optional[GoalViewer] = Depends(_goal_viewer),
) -> Dict[str, Any]:
    """获取单个 AI Employee（agent）详情（不可见 = 404）。"""
    viewer = _as_viewer(viewer)
    mgr = AIStateManager()
    try:
        return mgr.get_agent(agent_id, viewer)
    except ValueError as exc:
        logger.warning("get employee rejected agent_id=%r: %s", agent_id, exc)
        raise HTTPException(
            status_code=404,
            detail="没有找到这个员工，或它不属于当前账号。请刷新列表后重试。",
        ) from exc


@router.get("/employees/{name}/memories")
def list_employee_memories(
    name: str,
    viewer: Optional[GoalViewer] = Depends(_goal_viewer),
) -> Dict[str, Any]:
    """某位员工产生的记忆（归属该员工所属账号）。

    - 子账号只能看自己名下员工的记忆；
    - 主账号可看整棵子树员工的记忆（只读）；
    - 别人的 / 不存在的员工统一 404，不暴露存在与否。
    """
    viewer = _as_viewer(viewer)
    mgr = AIStateManager()
    try:
        return mgr.employee_memories(name, viewer)
    except GoalWorkError as exc:
        raise _http_from_goal_error(exc) from exc


@router.post("/employees/{agent_id}/pause")
def pause_employee(
    agent_id: str,
    viewer: Optional[GoalViewer] = Depends(_goal_viewer),
) -> Dict[str, Any]:
    """暂停一个 AI Employee 的 agent（只能动自己的员工）。"""
    viewer = _as_viewer(viewer)
    mgr = AIStateManager()
    try:
        return mgr.pause_agent(agent_id, viewer)
    except ValueError as exc:
        logger.warning("pause employee rejected agent_id=%r: %s", agent_id, exc)
        raise HTTPException(
            status_code=404,
            detail="暂停没有完成：没有找到这个员工。请刷新列表后重试。",
        ) from exc


@router.post("/employees/{agent_id}/resume")
def resume_employee(
    agent_id: str,
    viewer: Optional[GoalViewer] = Depends(_goal_viewer),
) -> Dict[str, Any]:
    """恢复一个暂停的 AI Employee 的 agent（只能动自己的员工）。"""
    viewer = _as_viewer(viewer)
    mgr = AIStateManager()
    try:
        return mgr.resume_agent(agent_id, viewer)
    except ValueError as exc:
        logger.warning("resume employee rejected agent_id=%r: %s", agent_id, exc)
        raise HTTPException(
            status_code=404,
            detail="恢复没有完成：没有找到这个员工。请刷新列表后重试。",
        ) from exc


@router.get("/workflows")
def list_workflows(
    viewer: Optional[GoalViewer] = Depends(_goal_viewer),
) -> Dict[str, Any]:
    """列出**当前账号可见**的 Workflow 执行状态（以 Goal 为维度）。"""
    viewer = _as_viewer(viewer)
    mgr = AIStateManager()
    goals = mgr.list_goals(viewer)
    workflows = []
    for g in goals:
        workflows.append({
            "goal_id": g["goal_id"],
            "state": g["state"],
            "task_count": g["task_count"],
            "completed_tasks": g["completed_tasks"],
            "failed_tasks": g["failed_tasks"],
            "owner_account_id": g.get("owner_account_id"),
        })
    return {"workflows": workflows, "count": len(workflows)}


@router.get("/workflows/{goal_id}")
def get_workflow(
    goal_id: str,
    viewer: Optional[GoalViewer] = Depends(_goal_viewer),
) -> Dict[str, Any]:
    """获取某个 Goal 的 Workflow 执行状态（不可见 = 404）。"""
    viewer = _as_viewer(viewer)
    mgr = AIStateManager()
    try:
        goal = mgr.get_goal(goal_id, viewer)
        return {
            "goal_id": goal_id,
            "state": goal["state"],
            "tasks": goal.get("tasks", []),
            "trace": goal.get("trace", []),
            "evaluation": goal.get("evaluation"),
            "owner_account_id": owner_of_entry(goal),
        }
    except GoalWorkError as exc:
        raise _http_from_goal_error(exc) from exc
    except ValueError as exc:
        logger.warning("goal detail rejected goal_id=%r: %s", goal_id, exc)
        raise HTTPException(
            status_code=404,
            detail="要查看的目标不存在。请刷新列表后重试。",
        ) from exc
