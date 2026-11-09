"""
Tool Registry & Router — MASTER-SPEC Phase 9 (L-Core) / §40-41.

Bridges the Execution Kernel's ``capability_id``-based planning to *real* tool
execution. The Execution Kernel's ``ActionExecutor`` simulates capability calls
(``_simulate_capability``); this module supplies the missing piece — a registry
of concrete tools (each bound to a ``capability``) with a lifecycle, and a
router that maps a ``capability_id`` to an active tool and executes it.

Assembly: pass ``ToolRouter(registry).as_capability_executor()`` as the
``capability_executor`` of ``ActionExecutor`` to wire real execution; without it
the kernel keeps taking the simulated path.

§40 lifecycle: REGISTER -> VALIDATE -> APPROVE -> ACTIVE -> SUSPENDED -> REVOKED.
Only ACTIVE tools are executable.
"""

from __future__ import annotations

import contextvars
import hashlib
import json
import logging
import os
import threading
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Dict, List, Mapping, Optional

from ..kernels.execution import ActionResult
from .observability import observe
from .audit import audited

logger = logging.getLogger(__name__)

#: 工具执行默认超时（秒）。可用 ``LIUHAO_TOOL_TIMEOUT_SECONDS`` 覆盖。
DEFAULT_TOOL_TIMEOUT_SECONDS = 30.0
#: 没有显式 ``audit_policy`` 的工具，执行审计落到这个动作名。
DEFAULT_TOOL_AUDIT_ACTION = "p9.tool.execute"
#: 注册表内置的**只读**权限白名单。写/执行类权限一律拒绝，直到注入
#: :class:`ToolRegistry` 的 ``permission_checker`` 明确放行（fail-closed）。
DEFAULT_ALLOWED_PERMISSIONS = frozenset({"workspace.read"})

#: 旧版 schema 的简写类型（``{"path": "str"}``）到 JSON Schema 类型的映射。
_LEGACY_TYPE_MAP = {
    "str": "string",
    "string": "string",
    "int": "integer",
    "integer": "integer",
    "float": "number",
    "number": "number",
    "bool": "boolean",
    "boolean": "boolean",
    "dict": "object",
    "object": "object",
    "list": "array",
    "array": "array",
    "any": None,
}

#: 审计参数摘要最多记录多少个字段；超出只记录字段数。
MAX_AUDIT_PARAM_KEYS = 32

#: 超时后最多等待执行线程多少秒再放弃观察（线程本身是 daemon，不会阻塞退出）。
_TIMEOUT_SLACK_SECONDS = 0.0


def tool_timeout_seconds() -> float:
    """读取全局工具执行超时（1–600 秒，非法值回落默认 30 秒）。"""
    raw = (os.environ.get("LIUHAO_TOOL_TIMEOUT_SECONDS") or "").strip()
    try:
        value = float(raw) if raw else DEFAULT_TOOL_TIMEOUT_SECONDS
    except ValueError:
        value = DEFAULT_TOOL_TIMEOUT_SECONDS
    return max(1.0, min(600.0, value))


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8", errors="replace")).hexdigest()


def _param_digest(value: Any) -> Any:
    """参数摘要：标量记值，字符串/容器只记长度与 sha256（不写明文）。"""
    if isinstance(value, str):
        return {"type": "string", "len": len(value), "sha256": _sha256_text(value)}
    if isinstance(value, bool):
        return {"type": "boolean", "value": value}
    if isinstance(value, (int, float)):
        return {"type": type(value).__name__, "value": value}
    if isinstance(value, Mapping):
        payload = json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)
        return {"type": "object", "keys": len(value), "sha256": _sha256_text(payload)}
    if isinstance(value, (list, tuple, set)):
        payload = json.dumps(list(value), ensure_ascii=False, sort_keys=True, default=str)
        return {"type": "array", "items": len(value), "sha256": _sha256_text(payload)}
    return {"type": type(value).__name__, "sha256": _sha256_text(str(value))}


def _summarize_inputs(inputs: Optional[Mapping[str, Any]]) -> Dict[str, Any]:
    if not isinstance(inputs, Mapping):
        return {}
    items = sorted(inputs.items(), key=lambda item: str(item[0]))
    summary = {str(k): _param_digest(v) for k, v in items[:MAX_AUDIT_PARAM_KEYS]}
    if len(items) > MAX_AUDIT_PARAM_KEYS:
        summary["_truncated_keys"] = len(items) - MAX_AUDIT_PARAM_KEYS
    return summary


def _current_correlation_id(explicit: Optional[str] = None) -> str:
    """复用调用链 correlation/trace id；没有则生成一个。"""
    if explicit:
        return explicit
    try:
        from .observability import TraceContext

        ctx = TraceContext.current()
        if ctx.correlation_id:
            return ctx.correlation_id
        if ctx.trace_id:
            return ctx.trace_id
    except Exception:  # pragma: no cover - 观测模块不可用时的降级
        pass
    return uuid.uuid4().hex


def normalize_tool_schema(schema: Optional[Mapping[str, Any]]) -> Dict[str, Any]:
    """把两套历史 schema 归一成 JSON Schema。

    ``src/ai/tools_local.py`` 的历史格式是 ``{"path": "str"}``（字段→类型简写），
    ``src/ai/tools/kernel_tools.py`` 用的是标准 JSON Schema。注册表统一成后者，
    这样校验器、审计和未来的外部消费者只需要理解一种格式。
    """
    if not isinstance(schema, Mapping) or not schema:
        return {"type": "object", "properties": {}}
    if "type" in schema or "properties" in schema or "required" in schema:
        return dict(schema)

    properties: Dict[str, Any] = {}
    for key, value in schema.items():
        name = str(key)
        if isinstance(value, str):
            spec: Dict[str, Any] = {}
            mapped = _LEGACY_TYPE_MAP.get(value.strip().lower(), None)
            if mapped:
                spec["type"] = mapped
            properties[name] = spec
        elif isinstance(value, Mapping):
            properties[name] = dict(value)
        else:
            properties[name] = {}
    return {"type": "object", "properties": properties}


def _type_matches(value: Any, expected: Any) -> bool:
    expected = _LEGACY_TYPE_MAP.get(str(expected).strip().lower(), expected)
    if expected is None:
        return True
    if expected == "string":
        return isinstance(value, str)
    if expected == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if expected == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if expected == "boolean":
        return isinstance(value, bool)
    if expected == "object":
        return isinstance(value, Mapping)
    if expected == "array":
        return isinstance(value, (list, tuple))
    return True


def validate_tool_inputs(
    schema: Optional[Mapping[str, Any]], inputs: Any
) -> List[str]:
    """按 (已归一的) schema 校验入参，返回中文错误列表（空表示通过）。

    刻意**不**默认拒绝未知参数：智能体工具对别名字段（``key``/``text``）有
    防御性兼容，schema 未声明 ``additionalProperties=False`` 时保留该行为。
    """
    if not isinstance(inputs, Mapping):
        return ["参数必须是 JSON 对象（键值对）"]

    schema = normalize_tool_schema(schema)
    errors: List[str] = []

    required = schema.get("required")
    if isinstance(required, (list, tuple)):
        for key in required:
            if key not in inputs or inputs.get(key) is None:
                errors.append(f"缺少必填参数 {key}")

    properties = schema.get("properties")
    if isinstance(properties, Mapping):
        for key, value in inputs.items():
            spec = properties.get(key)
            if not isinstance(spec, Mapping):
                if schema.get("additionalProperties") is False:
                    errors.append(f"不支持参数 {key}")
                continue
            if value is None:
                continue
            expected = spec.get("type")
            if expected is not None:
                types = expected if isinstance(expected, (list, tuple)) else [expected]
                if not any(_type_matches(value, t) for t in types):
                    errors.append(
                        f"参数 {key} 类型错误：期望 {expected}，实际 {type(value).__name__}"
                    )
                    continue
            enum = spec.get("enum")
            if isinstance(enum, (list, tuple)) and value not in enum:
                errors.append(f"参数 {key} 取值不在允许范围 {list(enum)}")
    return errors


class ToolExecutionTimeout(Exception):
    """工具执行超过允许时间（执行线程被放弃等待，服务不随之挂死）。"""


def _run_tool_with_timeout(
    fn: Callable[..., Any], inputs: Dict[str, Any], timeout: float
) -> Any:
    """在独立 daemon 线程里执行工具，超时抛 :class:`ToolExecutionTimeout`。

    用裸线程而不是 ``ThreadPoolExecutor``：后者的 worker 不是 daemon，且
    ``atexit`` 会 join 所有 worker；一个卡在 Windows 设备名/命名管道上的工具
    会让进程退出也一起挂住。daemon 线程 + ``Event.wait`` 保证**调用方**按时
    返回，卡住的线程最多泄漏一个，不影响事件循环与进程退出。
    """
    box: Dict[str, Any] = {}
    done = threading.Event()
    # contextvars 不会自动跨线程传播；copy_context 让工具内的"当前会话/主体"
    # 等上下文（如 system_status 的 status_fn）在 worker 线程里仍然可见。
    run_context = contextvars.copy_context()

    def _runner() -> None:
        try:
            box["output"] = run_context.run(lambda: fn(**inputs))
        except BaseException as exc:  # noqa: BLE001 - 原样交回调用线程
            box["error"] = exc
        finally:
            done.set()

    thread = threading.Thread(
        target=_runner, daemon=True, name="liuhao-tool-exec"
    )
    thread.start()
    if not done.wait(max(0.0, timeout) + _TIMEOUT_SLACK_SECONDS):
        raise ToolExecutionTimeout(
            f"工具执行超时（超过 {timeout:.1f} 秒），已放弃等待"
        )
    if "error" in box:
        raise box["error"]
    return box.get("output")


def _default_permission_check(principal: str, permission: str) -> bool:
    """注册表内置的保守权限判定：默认只放行只读观察权限。

    更细粒度的策略（按主体/审批单/策略引擎）应通过
    ``ToolRegistry(permission_checker=...)`` 注入；未注入时**写/执行类权限一律
    拒绝**（fail-closed），避免声明了 ``permission`` 却毫无作用的假象。
    """
    return permission in DEFAULT_ALLOWED_PERMISSIONS


class ToolStatus(Enum):
    """§40 tool lifecycle states."""
    REGISTERED = "registered"
    VALIDATED = "validated"
    APPROVED = "approved"
    ACTIVE = "active"
    SUSPENDED = "suspended"
    REVOKED = "revoked"


@dataclass
class Tool:
    """A concrete, callable tool (MASTER-SPEC §40 Tool)."""

    tool_id: str
    name: str
    version: str
    description: str
    capability: str  # capability_id this tool satisfies (Execution Kernel namespace)
    schema: Dict[str, Any]  # input parameter schema
    fn: Callable[..., Any]  # real execution function
    risk: str = "LOW"
    permission: Optional[str] = None
    dependencies: List[str] = field(default_factory=list)
    sandbox_policy: Optional[str] = None
    health: str = "UNKNOWN"
    audit_policy: Optional[str] = None


class ToolRegistry:
    """§40 registry enforcing the tool lifecycle."""

    def __init__(
        self,
        *,
        timeout_seconds: Optional[float] = None,
        permission_checker: Optional[Callable[[str, str], bool]] = None,
    ) -> None:
        self._tools: Dict[str, Tool] = {}
        self._status: Dict[str, ToolStatus] = {}
        self.timeout_seconds = (
            float(timeout_seconds) if timeout_seconds is not None
            else tool_timeout_seconds()
        )
        self._permission_checker = permission_checker or _default_permission_check

    # -------------------------------------------------------------- lifecycle
    @observe("tool_registry.register")
    @audited("p9.tool.register", module="src.ai.tool_registry")
    def register(self, tool: Tool) -> str:
        # 统一 schema 格式（旧版 ``{"path": "str"}`` → JSON Schema），并检测
        # 能力名冲突：同能力多工具会让路由结果依赖注册顺序，这里至少留痕。
        tool.schema = normalize_tool_schema(tool.schema)
        if tool.tool_id in self._tools:
            logger.warning(
                "工具 %s 被重复注册，旧定义将被覆盖（name=%s）",
                tool.tool_id, tool.name,
            )
        same_capability = [
            t.tool_id for t in self._tools.values()
            if t.capability == tool.capability and t.tool_id != tool.tool_id
        ]
        if same_capability:
            logger.warning(
                "能力 %s 已由工具 %s 提供，现在又注册 %s；路由将按 tool_id 排序取第一个",
                tool.capability, same_capability, tool.tool_id,
            )
        self._tools[tool.tool_id] = tool
        self._status[tool.tool_id] = ToolStatus.REGISTERED
        return tool.tool_id

    def validate(self, tool_id: str) -> bool:
        if self._status.get(tool_id) != ToolStatus.REGISTERED:
            return False
        tool = self._tools[tool_id]
        if not (tool.name and tool.description and tool.capability and callable(tool.fn)):
            return False
        self._status[tool_id] = ToolStatus.VALIDATED
        return True

    @audited("p9.tool.approve", module="src.ai.tool_registry")
    def approve(self, tool_id: str) -> bool:
        if self._status.get(tool_id) != ToolStatus.VALIDATED:
            return False
        self._status[tool_id] = ToolStatus.APPROVED
        return True

    @audited("p9.tool.activate", module="src.ai.tool_registry")
    def activate(self, tool_id: str) -> bool:
        if self._status.get(tool_id) != ToolStatus.APPROVED:
            return False
        self._status[tool_id] = ToolStatus.ACTIVE
        return True

    @audited("p9.tool.suspend", module="src.ai.tool_registry")
    def suspend(self, tool_id: str) -> bool:
        if tool_id not in self._tools:
            return False
        self._status[tool_id] = ToolStatus.SUSPENDED
        return True

    @audited("p9.tool.revoke", module="src.ai.tool_registry")
    def revoke(self, tool_id: str) -> bool:
        if tool_id not in self._tools:
            return False
        self._status[tool_id] = ToolStatus.REVOKED
        return True

    # ---------------------------------------------------------------- queries
    def get(self, tool_id: str) -> Optional[Tool]:
        return self._tools.get(tool_id)

    def status(self, tool_id: str) -> Optional[ToolStatus]:
        return self._status.get(tool_id)

    def list_active_by_capability(self, capability: str) -> List[Tool]:
        return [
            t for t in self._tools.values()
            if t.capability == capability and self._status[t.tool_id] == ToolStatus.ACTIVE
        ]

    # -------------------------------------------------------------- execution
    def _audit_execution(
        self,
        *,
        tool: Optional[Tool],
        tool_id: str,
        principal: str,
        correlation_id: str,
        outcome: str,
        reason: str = "",
        error_class: Optional[str] = None,
        duration_ms: float = 0.0,
        inputs: Optional[Mapping[str, Any]] = None,
        permission_checked: bool = False,
        permission_allowed: bool = True,
    ) -> None:
        """写一条工具执行审计；审计本身失败绝不影响工具结果。

        条目包含"谁（actor）/何时（log_event 自带 timestamp）/什么参数摘要
        （``params`` 只有类型、长度、sha256）/结果状态（outcome）/
        correlation_id"，失败调用同样留痕。
        """
        try:
            from .audit import emit
        except Exception:  # pragma: no cover - 审计模块不可用时的降级
            return
        action = (
            tool.audit_policy
            if tool is not None and tool.audit_policy
            else DEFAULT_TOOL_AUDIT_ACTION
        )
        details: Dict[str, Any] = {
            "tool_id": tool_id,
            "capability": tool.capability if tool is not None else None,
            "risk": tool.risk if tool is not None else None,
            "params": _summarize_inputs(inputs),
            "param_keys": sorted(str(k) for k in (inputs or {}))[:MAX_AUDIT_PARAM_KEYS],
            "error_class": error_class,
            "error_summary": (reason or "")[:200],
            "duration_ms": round(duration_ms, 3),
            "permission": tool.permission if tool is not None else None,
            "permission_checked": permission_checked,
            "permission_allowed": permission_allowed,
        }
        try:
            emit(
                action,
                module="src.ai.tool_registry",
                outcome=outcome,
                reason=(reason or "")[:500],
                correlation_id=correlation_id,
                actor=principal,
                **details,
            )
        except Exception as exc:  # pragma: no cover - 审计链故障只记日志
            logger.warning("工具执行审计写入失败（tool=%s）：%s", tool_id, exc)

    @observe("tool_registry.execute")
    def execute(
        self,
        tool_id: str,
        inputs: Dict[str, Any],
        *,
        principal_id: Optional[str] = None,
        correlation_id: Optional[str] = None,
    ) -> ActionResult:
        """Execute a tool (only when ACTIVE). Returns an ``ActionResult``.

        执行路径现在真正消费 ``Tool.audit_policy`` / ``permission`` /
        ``schema``：每次调用（成功、失败、超时、越权、参数非法）都写一条审计；
        工具在独立 daemon 线程运行并受 ``timeout_seconds`` 约束，超时返回失败
        而不是让调用方（事件循环/生成器线程）挂住。
        """
        started = time.perf_counter()
        principal = principal_id or "unknown"
        cid = _current_correlation_id(correlation_id)
        safe_inputs: Dict[str, Any] = dict(inputs) if isinstance(inputs, Mapping) else {}

        def elapsed() -> float:
            return (time.perf_counter() - started) * 1000.0

        tool = self._tools.get(tool_id)
        if tool is None:
            error = f"unknown tool {tool_id}"
            self._audit_execution(
                tool=None, tool_id=tool_id, principal=principal,
                correlation_id=cid, outcome="failure", reason=error,
                error_class="UnknownTool", duration_ms=elapsed(),
                inputs=safe_inputs,
            )
            return ActionResult(action_id=tool_id, success=False, error=error)

        if self._status[tool_id] != ToolStatus.ACTIVE:
            error = f"tool {tool_id} is not active"
            self._audit_execution(
                tool=tool, tool_id=tool_id, principal=principal,
                correlation_id=cid, outcome="failure", reason=error,
                error_class="ToolNotActive", duration_ms=elapsed(),
                inputs=safe_inputs,
            )
            return ActionResult(action_id=tool_id, success=False, error=error)

        # ---- 权限：声明的 permission 必须在执行路径被真正消费 ----
        permission_checked = False
        permission_allowed = True
        if tool.permission:
            permission_checked = True
            try:
                permission_allowed = bool(
                    self._permission_checker(principal, tool.permission)
                )
            except Exception as exc:  # 判定器故障 → fail-closed
                logger.warning(
                    "工具 %s 权限判定器异常（permission=%s）：%s",
                    tool_id, tool.permission, exc,
                )
                permission_allowed = False
            if not permission_allowed:
                error = (
                    f"工具 {tool_id} 需要权限 {tool.permission}，"
                    f"主体 {principal} 未获授权"
                )
                self._audit_execution(
                    tool=tool, tool_id=tool_id, principal=principal,
                    correlation_id=cid, outcome="denied", reason=error,
                    error_class="PermissionDenied", duration_ms=elapsed(),
                    inputs=safe_inputs,
                    permission_checked=True, permission_allowed=False,
                )
                return ActionResult(
                    action_id=tool_id, success=False, error=error,
                    output={"success": False, "status": "denied", "error": error},
                )

        # ---- schema 校验：拒绝参数类型/必填项错误，而不是等 fn 里 TypeError ----
        schema_errors = validate_tool_inputs(tool.schema, safe_inputs)
        if schema_errors:
            error = "参数校验失败：" + "；".join(schema_errors)
            self._audit_execution(
                tool=tool, tool_id=tool_id, principal=principal,
                correlation_id=cid, outcome="failure", reason=error,
                error_class="SchemaValidationError", duration_ms=elapsed(),
                inputs=safe_inputs,
                permission_checked=permission_checked,
                permission_allowed=permission_allowed,
            )
            return ActionResult(
                action_id=tool_id, success=False, error=error,
                output={"success": False, "status": "invalid_args", "error": error},
            )

        try:
            output = _run_tool_with_timeout(
                tool.fn, safe_inputs, self.timeout_seconds
            )
        except ToolExecutionTimeout as exc:
            error = f"{tool_id} {exc}"
            self._audit_execution(
                tool=tool, tool_id=tool_id, principal=principal,
                correlation_id=cid, outcome="timeout", reason=error,
                error_class="ToolExecutionTimeout", duration_ms=elapsed(),
                inputs=safe_inputs,
                permission_checked=permission_checked,
                permission_allowed=permission_allowed,
            )
            return ActionResult(
                action_id=tool_id, success=False, error=error,
                output={"success": False, "status": "timeout", "error": error},
            )
        except Exception as exc:  # noqa: BLE001 - surface as failed action
            error = str(exc) or type(exc).__name__
            self._audit_execution(
                tool=tool, tool_id=tool_id, principal=principal,
                correlation_id=cid, outcome="failure", reason=error,
                error_class=type(exc).__name__, duration_ms=elapsed(),
                inputs=safe_inputs,
                permission_checked=permission_checked,
                permission_allowed=permission_allowed,
            )
            return ActionResult(action_id=tool_id, success=False, error=error)
        # In-band rejection: several tools (notably ``python_compute`` and
        # ``file_write``) report a refusal by RETURNING
        # ``{"success": False, "status": "rejected", "error": ...}`` instead of
        # raising. Wrapping that in ``ActionResult(success=True)`` is a silent
        # success: the tool explicitly said it refused, yet the registry would
        # record a successful action and only a downstream verifier would
        # happen to notice. Propagate the tool's own verdict honestly so the
        # failure cannot be mistaken for success.
        #
        # Conservative: only an explicit ``False`` counts (not any falsy value),
        # so a tool that merely omits ``success`` is still treated as success.
        if isinstance(output, Mapping) and output.get("success", True) is False:
            reason = output.get("error") or output.get("status") or "tool reported failure"
            self._audit_execution(
                tool=tool, tool_id=tool_id, principal=principal,
                correlation_id=cid, outcome="failure", reason=str(reason),
                error_class="ToolReportedFailure", duration_ms=elapsed(),
                inputs=safe_inputs,
                permission_checked=permission_checked,
                permission_allowed=permission_allowed,
            )
            return ActionResult(action_id=tool_id, success=False,
                                error=str(reason), output=output)
        self._audit_execution(
            tool=tool, tool_id=tool_id, principal=principal,
            correlation_id=cid, outcome="success", reason="执行成功",
            duration_ms=elapsed(), inputs=safe_inputs,
            permission_checked=permission_checked,
            permission_allowed=permission_allowed,
        )
        return ActionResult(action_id=tool_id, success=True, output=output)


class ToolRouter:
    """Maps a ``capability_id`` to an active tool and executes it."""

    def __init__(self, registry: ToolRegistry) -> None:
        self.registry = registry

    def route(self, capability_id: str) -> Optional[Tool]:
        # 同能力多工具时不能依赖 dict 插入顺序（注册顺序会随加载顺序漂移），
        # 按 tool_id 排序取第一个，并在有歧义时留一条 warning。
        tools = sorted(
            self.registry.list_active_by_capability(capability_id),
            key=lambda t: t.tool_id,
        )
        if len(tools) > 1:
            logger.warning(
                "能力 %s 有 %d 个 ACTIVE 工具（%s），按 tool_id 取第一个 %s",
                capability_id, len(tools),
                [t.tool_id for t in tools], tools[0].tool_id,
            )
        return tools[0] if tools else None

    @observe("tool_router.execute")
    def execute(
        self,
        capability_id: str,
        inputs: Dict[str, Any],
        *,
        principal_id: Optional[str] = None,
        correlation_id: Optional[str] = None,
    ) -> ActionResult:
        tool = self.route(capability_id)
        if tool is None:
            return ActionResult(action_id="", success=False,
                                error=f"no active tool for capability {capability_id}")
        return self.registry.execute(
            tool.tool_id, inputs,
            principal_id=principal_id, correlation_id=correlation_id,
        )

    def as_capability_executor(self) -> Callable[..., Any]:
        """Return a ``CapabilityExecutor``-compatible closure for the kernel.

        Signature (ACTION-01)::

            run(capability_id, inputs, *, action_id: str | None = None) -> Any

        ``action_id`` 是执行层给出的**稳定幂等键**（同一次执行的所有重试都相同）。
        老执行器契约 ``run(capability_id, inputs)`` 依然成立：该参数是关键字参数
        且有默认值，旧调用方不会崩。把它放进返回包，便于工具/审计侧按同一个键
        去重外部副作用（发消息、调外部接口等不可重入操作）。

        The closure raises ``RuntimeError`` on a failed/unrouted tool so that the
        Execution Kernel's existing ``except Exception`` path turns it into a
        ``success=False`` action result.
        """

        def run(
            capability_id: str,
            inputs: Dict[str, Any],
            *,
            action_id: Optional[str] = None,
        ) -> Any:
            result = self.execute(capability_id, inputs)
            if not result.success:
                raise RuntimeError(result.error)
            # 统一返回包：执行引擎按 ``success`` 判定成败，裸 dict 会被 fail-closed
            # 判成 "unconfirmed"。这里补上 success，同时保留工具原有字段。
            output = result.output
            if isinstance(output, Mapping):
                envelope = dict(output)
                envelope.setdefault("success", True)
            else:
                envelope = {"success": True, "data": output}
            if action_id:
                # 显式字段，不塞进 inputs —— inputs 是业务参数，可能被工具覆盖。
                envelope.setdefault("action_id", action_id)
            return envelope

        return run
