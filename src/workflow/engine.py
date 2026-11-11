"""工作流运行引擎：把定义里的节点真正落到现有执行层。

三条底线
========
1. **不另造 Agent 执行引擎。** ``task`` / ``agent`` 节点只是调
   ``AIStateManager.create_and_execute_goal`` / ``assign_task``，``notify``
   只是往现有事件总线发事件。引擎自己不理解"任务怎么做"，只理解"什么时候做、
   做完没有、失败了要不要重试"。
2. **审批是流程的一部分，不是状态备注。** 审批节点会把运行置为
   ``waiting_approval`` 并真的停下来；通过后从该节点的出边继续，拒绝/超时
   则终止运行并写审计。没有"只改状态不动流程"的分支。
3. **不伪造成功。** 目标失败、超时、并行未汇聚，一律落到 ``failed``，
   错误原因原样进步骤记录和运行实例。

并发模型
========
每次运行由一个守护线程推进；节点内不阻塞 HTTP 请求。并行节点用线程池
真并发执行各分支，全部返回后在 ``join`` 处继续。同一运行用可重入锁串行化，
避免"审批通过"和"超时扫描"同时推进会话。
"""

from __future__ import annotations

import logging
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

from .definition import (
    APPROVAL_NODE_TYPES,
    DefinitionError,
    evaluate_condition,
    node_by_id,
    outgoing_edges,
    resolve_field,
    start_node_id,
)
from .store import (
    StoreError,
    TERMINAL_RUN_STATUSES,
    WorkflowStore,
    get_workflow_store,
)

logger = logging.getLogger(__name__)

TERMINAL_GOAL_STATES = frozenset(
    {"completed", "failed", "cancelled", "interrupted", "error", "stopped"}
)
FAILED_GOAL_STATES = frozenset({"failed", "error", "interrupted"})

_TEMPLATE_PATTERN = re.compile(r"\{\{\s*([^{}]+?)\s*\}\}")


class NodeFailure(Exception):
    """节点执行失败（触发重试或终止运行）。"""

    def __init__(self, message: str, *, code: str = "node_failed", retryable: bool = True):
        super().__init__(message)
        self.message = message
        self.code = code
        self.retryable = retryable


class RunCancelled(Exception):
    """运行被人或超时终止。"""


class _AwaitApproval(Exception):
    def __init__(self, approval_id: str, step_id: str, node_id: str) -> None:
        super().__init__(f"waiting approval {approval_id}")
        self.approval_id = approval_id
        self.step_id = step_id
        self.node_id = node_id


# ── 执行层桥接 ───────────────────────────────────────────────


class ExecutionBridge:
    """引擎与执行层之间的唯一缝。测试可注入假实现，生产用 Gateway 实现。"""

    def submit_task(
        self,
        *,
        instruction: str,
        scope: str,
        project_id: str,
        owner_account_id: str,
        employee_name: str,
    ) -> Dict[str, Any]:  # pragma: no cover - 接口声明
        raise NotImplementedError

    def run_agent(
        self,
        *,
        instruction: str,
        scope: str,
        project_id: str,
        owner_account_id: str,
        employee_name: str,
        timeout_seconds: int,
        should_stop: Optional[Callable[[], bool]] = None,
    ) -> Dict[str, Any]:  # pragma: no cover - 接口声明
        raise NotImplementedError

    def notify(
        self,
        *,
        message: str,
        level: str,
        run_id: str,
        definition_name: str,
        node_name: str,
    ) -> Dict[str, Any]:  # pragma: no cover - 接口声明
        raise NotImplementedError


class GatewayExecutionBridge(ExecutionBridge):
    """复用 ``AIStateManager`` 的默认实现（延迟导入，避免拖慢网关启动）。"""

    def _manager(self):
        # ``AIStateManager`` 定义在 ``src/gateway/ai_management.py``（``src/ai/`` 下
        # 没有 ``state_manager`` 模块）。放在函数里延迟导入：网关启动时不必为此
        # 先拉起 ai_management 的整棵依赖，也避免 gateway ↔ workflow 的导入环。
        from src.gateway.ai_management import AIStateManager

        return AIStateManager()

    def submit_task(
        self,
        *,
        instruction: str,
        scope: str,
        project_id: str,
        owner_account_id: str,
        employee_name: str,
    ) -> Dict[str, Any]:
        manager = self._manager()
        result = manager.create_and_execute_goal(
            natural_language=instruction,
            scope=scope or "L1",
            background=True,
            project_id=project_id or None,
            pinned_agent=employee_name or None,
            owner_account_id=owner_account_id or "",
        )
        goal_id = str(result.get("id") or result.get("goal_id") or "")
        if not goal_id:
            raise NodeFailure("执行层没有返回目标 id", code="execution_contract")
        return {"goal_id": goal_id, "state": str(result.get("state") or "running")}

    def run_agent(
        self,
        *,
        instruction: str,
        scope: str,
        project_id: str,
        owner_account_id: str,
        employee_name: str,
        timeout_seconds: int,
        should_stop: Optional[Callable[[], bool]] = None,
    ) -> Dict[str, Any]:
        manager = self._manager()
        result = manager.create_and_execute_goal(
            natural_language=instruction,
            scope=scope or "L1",
            background=True,
            project_id=project_id or None,
            pinned_agent=employee_name or None,
            owner_account_id=owner_account_id or "",
        )
        goal_id = str(result.get("id") or result.get("goal_id") or "")
        if not goal_id:
            raise NodeFailure("执行层没有返回目标 id", code="execution_contract")
        deadline = time.time() + max(1, int(timeout_seconds or 1))
        last: Dict[str, Any] = dict(result)
        while True:
            if should_stop is not None and should_stop():
                raise RunCancelled("运行已取消")
            state = str(last.get("state") or "").lower()
            if state in TERMINAL_GOAL_STATES:
                break
            if time.time() >= deadline:
                raise NodeFailure(
                    f"Agent 执行超过 {timeout_seconds} 秒未结束（目标 {goal_id} 仍在运行）",
                    code="execution_timeout",
                )
            time.sleep(0.5)
            try:
                last = manager.get_goal(goal_id)
            except Exception as exc:  # noqa: BLE001 - 读不到就按失败处理，不猜
                raise NodeFailure(f"读取目标状态失败：{exc}", code="execution_read_failed")
        state = str(last.get("state") or "").lower()
        if state != "completed":
            message = str(last.get("error") or f"目标最终状态为 {state or 'unknown'}")
            raise NodeFailure(
                message,
                code="execution_failed" if state in FAILED_GOAL_STATES else "execution_not_completed",
            )
        artifacts = last.get("artifacts") if isinstance(last.get("artifacts"), list) else []
        return {
            "goal_id": goal_id,
            "state": state,
            "summary": str(last.get("summary") or last.get("result") or "")[:2000],
            "artifacts": [a for a in artifacts if isinstance(a, (str, dict))][:20],
            "task_count": len(last.get("tasks") or []) if isinstance(last.get("tasks"), list) else 0,
        }

    def notify(
        self,
        *,
        message: str,
        level: str,
        run_id: str,
        definition_name: str,
        node_name: str,
    ) -> Dict[str, Any]:
        _publish_event(
            "workflow.notification",
            {
                "message": message,
                "level": level,
                "run_id": run_id,
                "definition": definition_name,
                "node": node_name,
            },
            correlation_id=run_id,
        )
        return {"delivered": ["event"], "message": message}


# ── 审计 / 事件（失败绝不影响业务） ──────────────────────────


def _audit(
    action: str,
    *,
    principal_id: str,
    outcome: str,
    details: Dict[str, Any],
    correlation_id: str,
) -> None:
    try:
        from src.kernels.audit import AuditEventType, AuditScope, log_event

        log_event(
            AuditEventType.STATE_CHANGE,
            principal_id or "system",
            AuditScope.L1,
            outcome,
            {"action": action, **details},
            correlation_id=correlation_id,
        )
    except Exception as exc:  # noqa: BLE001 - 审计写不进去不能拖垮流程
        logger.warning("workflow audit failed: %s (%s)", exc, action)


def _publish_event(event_type: str, data: Dict[str, Any], *, correlation_id: str) -> None:
    try:
        from src.kernels.event import publish_event

        publish_event(
            type=event_type,
            source="workflow",
            data=data,
            correlation_id=correlation_id,
        )
    except Exception as exc:  # noqa: BLE001 - 事件总线不可用时只记日志
        logger.warning("workflow event publish failed: %s (%s)", exc, event_type)


# ── 运行上下文 ───────────────────────────────────────────────


class _RunContext:
    def __init__(self, run: Dict[str, Any]) -> None:
        self.run = run
        self.run_id = str(run["run_id"])
        self.definition = run["definition"]
        self.by_id = {node["id"]: node for node in self.definition.get("nodes", [])}
        self.variables = run.get("variables") or {}
        if not isinstance(self.variables.get("steps"), dict):
            self.variables["steps"] = {}
        if not isinstance(self.variables.get("input"), dict):
            self.variables["input"] = run.get("inputs") or {}
        self.variables["definition"] = {"name": self.definition.get("name", "")}
        self.variables["run"] = {"id": self.run_id}
        self.seq = 0
        self.seq_lock = threading.Lock()
        self.cancelled = False

    def next_seq(self) -> int:
        with self.seq_lock:
            self.seq += 1
            return self.seq


def _render(template: str, context: Dict[str, Any]) -> str:
    """极简模板：``{{a.b}}`` 取不到就留空，不做表达式求值。"""

    def _replace(match: "re.Match[str]") -> str:
        value = resolve_field(context, match.group(1))
        if value is None:
            return ""
        if isinstance(value, (dict, list)):
            return str(value)
        return str(value)

    return _TEMPLATE_PATTERN.sub(_replace, template or "")


def _next_node_id(
    definition: Dict[str, Any], node: Dict[str, Any], context: Dict[str, Any]
) -> Optional[str]:
    edges = outgoing_edges(definition, node["id"])
    if not edges:
        return None
    for edge in edges:
        condition = edge.get("condition")
        if condition and evaluate_condition(condition, context):
            return str(edge["to"])
    for edge in edges:
        if not edge.get("condition"):
            return str(edge["to"])
    return None


# ── 引擎 ─────────────────────────────────────────────────────


class WorkflowRuntime:
    def __init__(
        self,
        store: Optional[WorkflowStore] = None,
        bridge: Optional[ExecutionBridge] = None,
        *,
        sleep: Callable[[float], None] = time.sleep,
        max_steps: int = 500,
    ) -> None:
        self.store = store or get_workflow_store()
        self.bridge = bridge or GatewayExecutionBridge()
        self._sleep = sleep
        self._max_steps = max_steps
        self._locks: Dict[str, threading.RLock] = {}
        self._locks_guard = threading.Lock()
        self._cancelled: set[str] = set()

    # ── 对外：启动 / 恢复 / 取消 ──────────────────────────────

    def start_run(
        self,
        definition_record: Dict[str, Any],
        *,
        trigger_type: str = "manual",
        trigger_payload: Optional[Dict[str, Any]] = None,
        inputs: Optional[Dict[str, Any]] = None,
        submitted_by: str = "",
        owner_account_id: str = "",
        background: bool = True,
    ) -> Dict[str, Any]:
        definition = definition_record.get("definition") or {}
        if not definition_record.get("enabled", True):
            raise StoreError(409, "workflow_disabled", "流程已停用，不能运行")
        run = self.store.create_run(
            definition,
            def_id=str(definition_record["def_id"]),
            def_version=int(definition_record.get("version") or 1),
            trigger_type=trigger_type,
            trigger_payload=trigger_payload or {},
            inputs=inputs or {},
            owner_account_id=owner_account_id
            or str(definition_record.get("owner_account_id") or ""),
            submitted_by=submitted_by,
        )
        if background:
            thread = threading.Thread(
                target=self._advance_thread,
                args=(run["run_id"], start_node_id(definition)),
                name=f"workflow-{run['run_id']}",
                daemon=True,
            )
            thread.start()
        else:
            self._advance_run(run["run_id"], start_node_id(definition))
            run = self.store.get_run(run["run_id"]) or run
        return run

    def _advance_thread(self, run_id: str, start: str) -> None:
        try:
            self._advance_run(run_id, start)
        except Exception:  # noqa: BLE001 - 线程里的异常必须自己兜住
            logger.exception("workflow run thread crashed: %s", run_id)
            try:
                self.store.finish_run(
                    run_id,
                    status="failed",
                    outcome="error",
                    error="工作流线程异常退出",
                    error_code="runtime_crash",
                )
            except Exception:  # noqa: BLE001
                pass

    def _lock_for(self, run_id: str) -> threading.RLock:
        with self._locks_guard:
            lock = self._locks.get(run_id)
            if lock is None:
                lock = threading.RLock()
                self._locks[run_id] = lock
            return lock

    def _advance_run(self, run_id: str, start_node_id_value: str) -> None:
        with self._lock_for(run_id):
            run = self.store.get_run(run_id)
            if run is None:
                raise StoreError(404, "run_not_found", f"运行实例不存在：{run_id}")
            if run["status"] in TERMINAL_RUN_STATUSES:
                return
            ctx = _RunContext(run)
            ctx.cancelled = run_id in self._cancelled
            first_start = run.get("started_at") is None
            self.store.mark_run_running(run_id, current_node_id=start_node_id_value)
            if first_start:
                _audit(
                    "workflow.run.started",
                    principal_id=run.get("submitted_by", ""),
                    outcome="success",
                    details={
                        "run_id": run_id,
                        "definition": run.get("name", ""),
                        "definition_id": run.get("def_id", ""),
                        "trigger": run.get("trigger_type", "manual"),
                    },
                    correlation_id=run_id,
                )
            try:
                self._walk(ctx, start_node_id_value)
            except _AwaitApproval:
                return
            except RunCancelled as exc:
                self._finish_cancelled(run_id, str(exc) or "运行已取消", code="cancelled")
                return
            except NodeFailure as exc:
                self._finish_failed(run_id, exc)
                return
            except StoreError:
                raise
            except Exception as exc:  # noqa: BLE001
                logger.exception("workflow run failed: %s", run_id)
                self._finish_failed(
                    run_id, NodeFailure(f"运行异常：{exc}", code="runtime_error", retryable=False)
                )
                return
            else:
                self.store.finish_run(run_id, status="completed", outcome="completed")
                _audit(
                    "workflow.run.completed",
                    principal_id=run.get("submitted_by", ""),
                    outcome="success",
                    details={"run_id": run_id, "definition": run.get("name", "")},
                    correlation_id=run_id,
                )
                _publish_event(
                    "workflow.run.completed",
                    {"run_id": run_id, "definition_id": run.get("def_id", "")},
                    correlation_id=run_id,
                )
            finally:
                self._cancelled.discard(run_id)

    # ── 遍历 ────────────────────────────────────────────────

    def _walk(self, ctx: _RunContext, node_id: Optional[str]) -> None:
        steps = 0
        while node_id:
            steps += 1
            if steps > self._max_steps:
                raise NodeFailure("流程执行步数超过上限，疑似死循环", code="too_many_steps", retryable=False)
            if ctx.cancelled or ctx.run_id in self._cancelled:
                raise RunCancelled("运行已取消")
            node = ctx.by_id.get(node_id)
            if node is None:
                raise NodeFailure(f"节点不存在：{node_id}", code="invalid_graph", retryable=False)
            self.store.set_run_node(ctx.run_id, node_id)
            if node["type"] == "end":
                return
            if node["type"] == "join":
                # 单线程走到 join（非并行汇聚）时按"已满足"处理，直接往下走。
                node_id = _next_node_id(ctx.definition, node, ctx.variables)
                continue
            if node["type"] == "parallel":
                node_id = self._run_parallel(ctx, node)
                continue
            outputs = self._execute_with_retry(ctx, node)
            ctx.variables["steps"][node["id"]] = outputs
            self.store.update_run_variables(ctx.run_id, ctx.variables)
            node_id = _next_node_id(ctx.definition, node, ctx.variables)

    def _execute_with_retry(self, ctx: _RunContext, node: Dict[str, Any]) -> Dict[str, Any]:
        config = node.get("config") or {}
        retry = config.get("retry") if isinstance(config.get("retry"), dict) else {}
        max_attempts = max(1, min(5, int(retry.get("max_attempts") or 1)))
        backoff = max(0, min(120, int(retry.get("backoff_seconds") or 0)))
        last_error: Optional[NodeFailure] = None
        for attempt in range(1, max_attempts + 1):
            step = self.store.begin_step(
                ctx.run_id,
                node=node,
                seq=ctx.next_seq(),
                inputs=self._step_inputs(ctx, node),
                attempt=attempt,
            )
            try:
                outputs = self._execute_node(ctx, node, step)
            except _AwaitApproval:
                # 步骤已在 _request_approval 里落成 waiting_approval（先落步骤再建待办，
                # 否则审批刚出现就被处理时会把 rejected 覆盖回 waiting）。
                raise
            except RunCancelled:
                self.store.finish_step(step["step_id"], status="cancelled", error="运行已取消")
                raise
            except NodeFailure as exc:
                self.store.finish_step(
                    step["step_id"],
                    status="failed",
                    error=exc.message,
                    error_code=exc.code,
                )
                last_error = exc
                if not exc.retryable or attempt >= max_attempts:
                    raise
                _audit(
                    "workflow.step.retry",
                    principal_id=ctx.run.get("submitted_by", ""),
                    outcome="failure",
                    details={
                        "run_id": ctx.run_id,
                        "node_id": node["id"],
                        "attempt": attempt,
                        "error": exc.message,
                    },
                    correlation_id=ctx.run_id,
                )
                if backoff:
                    self._sleep(backoff)
                continue
            except Exception as exc:  # noqa: BLE001 - 未预期异常也要落到步骤上
                failure = NodeFailure(f"节点执行异常：{exc}", code="node_exception", retryable=False)
                self.store.finish_step(
                    step["step_id"],
                    status="failed",
                    error=failure.message,
                    error_code=failure.code,
                )
                raise failure
            else:
                return self.store.finish_step(
                    step["step_id"], status="completed", outputs=outputs
                ).get("outputs", outputs)
        raise last_error or NodeFailure("节点执行失败", code="node_failed")

    def _step_inputs(self, ctx: _RunContext, node: Dict[str, Any]) -> Dict[str, Any]:
        config = node.get("config") or {}
        inputs: Dict[str, Any] = {
            "node_id": node["id"],
            "node_type": node["type"],
            "node_name": node.get("name", ""),
        }
        if node["type"] in ("task", "agent"):
            inputs["instruction"] = _render(str(config.get("instruction") or ""), ctx.variables)
            inputs["employee_name"] = config.get("employee_name", "")
            inputs["scope"] = config.get("scope", "L1")
            inputs["project_id"] = config.get("project_id", "")
        elif node["type"] in APPROVAL_NODE_TYPES:
            inputs["operation"] = _render(str(config.get("operation") or ""), ctx.variables)
            inputs["approvers"] = config.get("approvers", [])
            inputs["timeout_seconds"] = config.get("timeout_seconds")
        elif node["type"] == "notify":
            inputs["message"] = _render(str(config.get("message") or ""), ctx.variables)
        return inputs

    def _execute_node(
        self, ctx: _RunContext, node: Dict[str, Any], step: Dict[str, Any]
    ) -> Dict[str, Any]:
        node_type = node["type"]
        config = node.get("config") or {}
        if node_type == "start":
            return {"started": True, "at": time.time()}
        if node_type == "archive":
            return {
                "archived": True,
                "note": str(config.get("note") or "")[:500],
                "inputs": ctx.run.get("inputs") or {},
                "at": time.time(),
            }
        if node_type == "notify":
            message = _render(str(config.get("message") or ""), ctx.variables).strip()
            if not message:
                raise NodeFailure("通知内容为空", code="notify_empty", retryable=False)
            result = self.bridge.notify(
                message=message,
                level=str(config.get("level") or "info"),
                run_id=ctx.run_id,
                definition_name=str(ctx.definition.get("name") or ""),
                node_name=str(node.get("name") or ""),
            )
            return {"message": message, **result}
        if node_type == "task":
            instruction = _render(str(config.get("instruction") or ""), ctx.variables).strip()
            if not instruction:
                raise NodeFailure(
                    "任务指令为空；请检查节点配置或上游变量",
                    code="instruction_empty",
                    retryable=False,
                )
            result = self.bridge.submit_task(
                instruction=instruction,
                scope=str(config.get("scope") or "L1"),
                project_id=str(config.get("project_id") or ""),
                owner_account_id=str(ctx.run.get("owner_account_id") or ""),
                employee_name=str(config.get("employee_name") or ""),
            )
            return {"instruction": instruction, **result}
        if node_type == "agent":
            instruction = _render(str(config.get("instruction") or ""), ctx.variables).strip()
            if not instruction:
                raise NodeFailure(
                    "Agent 指令为空；请检查节点配置或上游变量",
                    code="instruction_empty",
                    retryable=False,
                )
            timeout_seconds = int(config.get("timeout_seconds") or 600)
            result = self.bridge.run_agent(
                instruction=instruction,
                scope=str(config.get("scope") or "L1"),
                project_id=str(config.get("project_id") or ""),
                owner_account_id=str(ctx.run.get("owner_account_id") or ""),
                employee_name=str(config.get("employee_name") or ""),
                timeout_seconds=timeout_seconds,
                should_stop=lambda: ctx.run_id in self._cancelled,
            )
            return {"instruction": instruction, **result}
        if node_type in APPROVAL_NODE_TYPES:
            return self._request_approval(ctx, node, step)
        raise NodeFailure(
            f"节点类型 {node_type} 没有执行器", code="unsupported_node", retryable=False
        )

    def _request_approval(
        self, ctx: _RunContext, node: Dict[str, Any], step: Dict[str, Any]
    ) -> Dict[str, Any]:
        config = node.get("config") or {}
        # 先把步骤置为 waiting_approval，再创建待办：这样待办一出现，
        # 步骤状态就已经是终态前的稳定值，不会被并发处理覆盖。
        self.store.mark_step_waiting(step["step_id"])
        approvers = config.get("approvers")
        if not isinstance(approvers, list) or not approvers:
            approvers = [str(ctx.run.get("owner_account_id") or "")]
        title = str(config.get("title") or "").strip() or str(node.get("name") or "人工审批")
        operation = _render(str(config.get("operation") or ""), ctx.variables).strip()
        if not operation:
            operation = f"运行流程「{ctx.definition.get('name', '')}」到节点「{node.get('name', '')}」"
        reason = str(config.get("reason") or "").strip() or "流程在此节点需要人工确认，未通过则终止。"
        approval = self.store.create_approval(
            run_id=ctx.run_id,
            step_id=step["step_id"],
            node_id=node["id"],
            def_id=str(ctx.run.get("def_id") or ""),
            title=title,
            operation=operation,
            reason=reason,
            approvers=[item for item in approvers if item],
            submitted_by=str(ctx.run.get("submitted_by") or ""),
            owner_account_id=str(ctx.run.get("owner_account_id") or ""),
            timeout_seconds=int(config.get("timeout_seconds") or 0),
        )
        self.store.set_run_waiting_approval(ctx.run_id, node["id"])
        _audit(
            "workflow.approval.requested",
            principal_id=str(ctx.run.get("submitted_by") or ""),
            outcome="pending",
            details={
                "run_id": ctx.run_id,
                "approval_id": approval["approval_id"],
                "node_id": node["id"],
                "operation": operation,
                "expires_at": approval.get("expires_at"),
            },
            correlation_id=ctx.run_id,
        )
        _publish_event(
            "workflow.approval.requested",
            {
                "run_id": ctx.run_id,
                "approval_id": approval["approval_id"],
                "title": title,
                "operation": operation,
            },
            correlation_id=ctx.run_id,
        )
        raise _AwaitApproval(approval["approval_id"], step["step_id"], node["id"])

    # ── 并行 ────────────────────────────────────────────────

    def _run_parallel(self, ctx: _RunContext, node: Dict[str, Any]) -> Optional[str]:
        edges = outgoing_edges(ctx.definition, node["id"])
        targets: List[str] = []
        for edge in edges:
            condition = edge.get("condition")
            if condition is None or evaluate_condition(condition, ctx.variables):
                targets.append(str(edge["to"]))
        if not targets:
            return _next_node_id(ctx.definition, node, ctx.variables)
        if len(targets) == 1:
            return targets[0]

        results: List[Tuple[str, Optional[str], Optional[NodeFailure]]] = []
        branch_step = self.store.begin_step(
            ctx.run_id,
            node=node,
            seq=ctx.next_seq(),
            inputs={"branches": targets},
        )
        try:
            with ThreadPoolExecutor(max_workers=min(8, len(targets))) as pool:
                futures = [pool.submit(self._run_branch, ctx, target) for target in targets]
                for future in futures:
                    results.append(future.result())
        except Exception as exc:  # noqa: BLE001
            self.store.finish_step(
                branch_step["step_id"],
                status="failed",
                error=str(exc),
                error_code="parallel_failed",
            )
            raise

        for status, _, failure in results:
            if status == "failed" and failure is not None:
                self.store.finish_step(
                    branch_step["step_id"],
                    status="failed",
                    outputs={"branches": len(targets)},
                    error=failure.message,
                    error_code=failure.code,
                )
                raise failure
        join_ids = {value for status, value, _ in results if status == "join" and value}
        ended = [item for item in results if item[0] == "end"]
        if not join_ids:
            self.store.finish_step(
                branch_step["step_id"],
                status="completed",
                outputs={"branches": len(targets), "join": None},
            )
            return None
        if len(join_ids) > 1:
            self.store.finish_step(
                branch_step["step_id"],
                status="failed",
                error="并行分支汇聚到了不同节点",
                error_code="join_mismatch",
            )
            raise NodeFailure(
                f"并行分支汇聚到了不同节点：{', '.join(sorted(join_ids))}",
                code="join_mismatch",
                retryable=False,
            )
        join_id = join_ids.pop()
        join_node = ctx.by_id.get(join_id or "")
        if join_node is None:
            raise NodeFailure(f"汇聚节点不存在：{join_id}", code="invalid_graph", retryable=False)
        mode = str((join_node.get("config") or {}).get("mode") or "all")
        if mode == "all" and ended:
            self.store.finish_step(
                branch_step["step_id"],
                status="failed",
                error="并行分支没有全部汇聚",
                error_code="join_incomplete",
            )
            raise NodeFailure(
                "并行分支没有全部汇聚到同一个 join 节点",
                code="join_incomplete",
                retryable=False,
            )
        self.store.finish_step(
            branch_step["step_id"],
            status="completed",
            outputs={"branches": len(targets), "join": join_id},
        )
        join_outputs = {
            "branches": len(targets),
            "joined": len(results) if mode == "all" else len(results) - len(ended),
            "mode": mode,
            "completed": int(time.time()),
        }
        ctx.variables["steps"][join_node["id"]] = join_outputs
        self.store.update_run_variables(ctx.run_id, ctx.variables)
        join_step = self.store.begin_step(
            ctx.run_id,
            node=join_node,
            seq=ctx.next_seq(),
            inputs={"mode": mode, "branches": len(targets)},
        )
        self.store.finish_step(join_step["step_id"], status="completed", outputs=join_outputs)
        return _next_node_id(ctx.definition, join_node, ctx.variables)

    def _run_branch(
        self, ctx: _RunContext, start: str
    ) -> Tuple[str, Optional[str], Optional[NodeFailure]]:
        try:
            node_id: Optional[str] = start
            guard = 0
            while node_id:
                guard += 1
                if guard > self._max_steps:
                    raise NodeFailure("并行分支步数超限", code="too_many_steps", retryable=False)
                if ctx.run_id in self._cancelled:
                    raise RunCancelled("运行已取消")
                node = ctx.by_id.get(node_id)
                if node is None:
                    raise NodeFailure(f"节点不存在：{node_id}", code="invalid_graph", retryable=False)
                if node["type"] == "end":
                    return ("end", None, None)
                if node["type"] == "join":
                    return ("join", node["id"], None)
                if node["type"] == "parallel":
                    node_id = self._run_parallel(ctx, node)
                    continue
                self.store.set_run_node(ctx.run_id, node["id"])
                outputs = self._execute_with_retry(ctx, node)
                ctx.variables["steps"][node["id"]] = outputs
                self.store.update_run_variables(ctx.run_id, ctx.variables)
                node_id = _next_node_id(ctx.definition, node, ctx.variables)
            return ("end", None, None)
        except NodeFailure as exc:
            return ("failed", None, exc)
        except _AwaitApproval:
            return (
                "failed",
                None,
                NodeFailure(
                    "并行分支内的人工审批暂不支持，请把审批节点移到并行之外",
                    code="approval_in_parallel",
                    retryable=False,
                ),
            )
        except RunCancelled as exc:
            return ("failed", None, NodeFailure(str(exc) or "运行已取消", code="cancelled", retryable=False))

    # ── 审批结果 → 驱动流程 ─────────────────────────────────

    def apply_approval_decision(
        self,
        approval_id: str,
        *,
        decision: str,
        decided_by: str,
        comment: str = "",
    ) -> Dict[str, Any]:
        approval = self.store.decide_approval(
            approval_id, decision=decision, decided_by=decided_by, comment=comment
        )
        run_id = str(approval["run_id"])
        node_id = str(approval["node_id"])
        step_id = str(approval["step_id"])
        run = self.store.get_run(run_id)
        if run is None:
            raise StoreError(404, "run_not_found", f"运行实例不存在：{run_id}")
        definition = run["definition"]
        node = node_by_id(definition, node_id)
        outputs = {
            "outcome": decision,
            "approval_id": approval_id,
            "decided_by": decided_by,
            "comment": comment,
            "decided_at": approval.get("decided_at"),
        }
        variables = run.get("variables") or {}
        if not isinstance(variables.get("steps"), dict):
            variables["steps"] = {}
        variables["steps"][node_id] = outputs
        self.store.update_run_variables(run_id, variables)
        _audit(
            "workflow.approval.decided",
            principal_id=decided_by,
            outcome="success" if decision == "approved" else "deny",
            details={
                "run_id": run_id,
                "approval_id": approval_id,
                "node_id": node_id,
                "decision": decision,
                "comment": comment,
                "submitted_by": approval.get("submitted_by", ""),
                "operation": approval.get("operation", ""),
            },
            correlation_id=run_id,
        )

        if decision != "approved":
            self.store.finish_step(
                step_id,
                status="rejected",
                outputs=outputs,
                error=f"审批被 {decided_by} 拒绝" + (f"：{comment}" if comment else ""),
                error_code="approval_rejected",
            )
            self.store.finish_run(
                run_id,
                status="cancelled",
                outcome="rejected",
                error=f"审批被 {decided_by} 拒绝" + (f"：{comment}" if comment else ""),
                error_code="approval_rejected",
            )
            _publish_event(
                "workflow.run.cancelled",
                {"run_id": run_id, "reason": "approval_rejected"},
                correlation_id=run_id,
            )
            return self.store.get_run(run_id) or run

        self.store.finish_step(step_id, status="completed", outputs=outputs)
        if node is None:
            raise StoreError(409, "invalid_graph", f"审批节点的定义已缺失：{node_id}")
        next_id = _next_node_id(definition, node, variables)
        if next_id is None:
            self.store.finish_run(run_id, status="completed", outcome="completed")
            return self.store.get_run(run_id) or run
        self.store.mark_run_running(run_id, current_node_id=next_id)
        thread = threading.Thread(
            target=self._advance_thread,
            args=(run_id, next_id),
            name=f"workflow-{run_id}-resume",
            daemon=True,
        )
        thread.start()
        return self.store.get_run(run_id) or run

    # ── 超时 / 取消 ─────────────────────────────────────────

    def expire_overdue(self, *, now: Optional[float] = None) -> List[str]:
        expired = self.store.expire_overdue_approvals(now=now)
        affected: List[str] = []
        for approval in expired:
            if not approval:
                continue
            run_id = str(approval["run_id"])
            step_id = str(approval["step_id"])
            try:
                self.store.finish_step(
                    step_id,
                    status="failed",
                    outputs={"outcome": "expired", "approval_id": approval["approval_id"]},
                    error="审批超时自动失效",
                    error_code="approval_expired",
                )
                self.store.finish_run(
                    run_id,
                    status="cancelled",
                    outcome="expired",
                    error="审批超时自动失效，流程已终止",
                    error_code="approval_expired",
                )
                _audit(
                    "workflow.approval.expired",
                    principal_id="system",
                    outcome="deny",
                    details={
                        "run_id": run_id,
                        "approval_id": approval["approval_id"],
                        "expires_at": approval.get("expires_at"),
                    },
                    correlation_id=run_id,
                )
                _publish_event(
                    "workflow.approval.expired",
                    {"run_id": run_id, "approval_id": approval["approval_id"]},
                    correlation_id=run_id,
                )
                affected.append(run_id)
            except Exception:  # noqa: BLE001 - 单条失败不阻断其它过期
                logger.exception("expire approval failed: %s", approval.get("approval_id"))
        return affected

    def cancel_run(self, run_id: str, *, cancelled_by: str, reason: str = "") -> Dict[str, Any]:
        run = self.store.get_run(run_id)
        if run is None:
            raise StoreError(404, "run_not_found", f"运行实例不存在：{run_id}")
        if run["status"] in TERMINAL_RUN_STATUSES:
            raise StoreError(409, "run_finished", f"运行已结束（{run['status']}），不能取消")
        self._cancelled.add(run_id)
        message = f"被 {cancelled_by} 取消" + (f"：{reason}" if reason else "")
        self.store.cancel_pending_approvals(run_id, comment=message)
        current = run.get("current_node_id")
        if current:
            step = self.store.get_step_by_node(run_id, str(current))
            if step and step["status"] in ("running", "waiting_approval", "pending"):
                self.store.finish_step(
                    step["step_id"], status="cancelled", error=message, error_code="cancelled"
                )
        self.store.finish_run(
            run_id, status="cancelled", outcome="cancelled", error=message, error_code="cancelled"
        )
        _audit(
            "workflow.run.cancelled",
            principal_id=cancelled_by,
            outcome="deny",
            details={"run_id": run_id, "reason": reason},
            correlation_id=run_id,
        )
        _publish_event(
            "workflow.run.cancelled", {"run_id": run_id, "reason": reason}, correlation_id=run_id
        )
        return self.store.get_run(run_id) or run

    # ── 失败收尾 ────────────────────────────────────────────

    def _finish_failed(self, run_id: str, failure: NodeFailure) -> None:
        run = self.store.get_run(run_id) or {}
        current = run.get("current_node_id")
        if current:
            step = self.store.get_step_by_node(run_id, str(current))
            if step and step["status"] in ("running", "waiting_approval", "pending"):
                self.store.finish_step(
                    step["step_id"],
                    status="failed",
                    error=failure.message,
                    error_code=failure.code,
                )
        self.store.finish_run(
            run_id,
            status="failed",
            outcome="failed",
            error=failure.message,
            error_code=failure.code,
        )
        _audit(
            "workflow.run.failed",
            principal_id=str(run.get("submitted_by") or ""),
            outcome="failure",
            details={"run_id": run_id, "error": failure.message, "code": failure.code},
            correlation_id=run_id,
        )
        _publish_event(
            "workflow.run.failed",
            {"run_id": run_id, "error": failure.message, "code": failure.code},
            correlation_id=run_id,
        )

    def _finish_cancelled(self, run_id: str, message: str, *, code: str) -> None:
        self.store.finish_run(
            run_id, status="cancelled", outcome="cancelled", error=message, error_code=code
        )


# ── 单例 ─────────────────────────────────────────────────────

_runtime: Optional[WorkflowRuntime] = None
_runtime_lock = threading.Lock()


def get_workflow_runtime() -> WorkflowRuntime:
    global _runtime
    if _runtime is None:
        with _runtime_lock:
            if _runtime is None:
                _runtime = WorkflowRuntime()
    return _runtime


def reset_workflow_runtime() -> None:
    global _runtime
    with _runtime_lock:
        _runtime = None
