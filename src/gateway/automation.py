"""电脑操控网关（0.4.6，5-3 电脑操控一期·安全版）。

端点（全部只对登录的人类开放，见 ``main.py`` 的 ``_require_human`` 挂载）：

* ``GET /v1/automation/status`` —— 三重闸门、后端、急停、shell 通道的真实状态；
* ``POST /v1/automation/observe`` —— 只读：前台窗口 / 窗口列表；
* ``POST /v1/automation/action`` —— 打开程序 / 切换聚焦 / 关闭窗口。
  ``open`` / ``focus`` 要前端确认框（``confirmed=true``）；``close`` 不可逆，
  同样要确认，执行前记审计；
* ``POST /v1/automation/estop`` / ``POST /v1/automation/estop/reset`` —— 急停；
* ``POST /v1/automation/shell`` —— shell 命令：默认关闭，仅 Owner；
  危险命令不直接跑，先在审批中心建待办（见
  ``src/gateway/decision_approval.py``），Joseph 点了再调
  ``POST /v1/automation/shell/{approval_id}/execute`` 执行。

非 Windows / 缺依赖时后端如实报 ``native_backend_unavailable``，不返回
假数据、不假装执行成功（真 Windows 实测见 ``docs/d-group-0.4.6.md``）。
"""

from __future__ import annotations

import logging
import os
import time
from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from ..automation.audit import AutomationAudit
from ..automation.backends import create_backend
from ..automation.backends.native_windows import ALLOWED_APPS
from ..automation.contracts import ActionRequest, AutomationError, BackendKind, TargetScope
from ..automation.estop import get_emergency_stop
from ..automation.gate import AutomationGate
from ..automation.shell import describe_shell, evaluate_shell, is_shell_enabled
from .policy import require_human_principal

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v1/automation", tags=["automation"])


def _is_owner(principal: str) -> bool:
    from .auth import _account_is_root

    try:
        return bool(_account_is_root(principal).get("is_root"))
    except Exception:  # noqa: BLE001 - 查不到就按非 Owner 处理
        return False


def _gate_decision(principal: str) -> Dict[str, Any]:
    return AutomationGate().evaluate(principal).as_dict()


def _backend_status() -> Dict[str, Any]:
    available = os.name == "nt"
    detail: Dict[str, Any] = {"platform": os.name, "native_available": available}
    if not available:
        # reason = 机主可见人话（壳的卡片会包一层「…（reason）」，故写短语）；
        # reason_code = 机器串（诊断/日志用，不上界面）
        detail["reason"] = "电脑操控只能在 Windows 上用"
        detail["reason_code"] = "native_backend_unavailable: only runs on Windows"
        return detail
    try:
        import pywinauto  # type: ignore[import-not-found]  # noqa: F401
        import uiautomation  # type: ignore[import-not-found]  # noqa: F401

        detail["pywinauto"] = True
        detail["uiautomation"] = True
    except Exception as exc:  # noqa: BLE001 - 缺哪个如实报哪个
        detail["native_available"] = False
        detail["reason"] = "电脑操控组件没装齐"
        detail["reason_code"] = f"native_backend_unavailable:{type(exc).__name__}"
    return detail


@router.get("/status", response_model=Dict[str, Any])
def automation_status(principal: str = Depends(require_human_principal)) -> Dict[str, Any]:
    """三重闸门 + 后端 + 急停 + shell 通道的真实状态（前端确认框与急停按钮读它）。"""
    estop = get_emergency_stop()
    return {
        "gate": _gate_decision(principal),
        "backend": _backend_status(),
        "allowed_apps": sorted(ALLOWED_APPS),
        "allowed_actions": ["open", "focus", "close", "foreground", "list_windows"],
        "emergency_stop": {
            "active": estop.active,
            "registration": (
                {
                    "registered": estop.registration.registered,
                    "combo": estop.registration.combo,
                    "reason": estop.registration.reason,
                }
                if estop.registration
                else None
            ),
        },
        "shell": describe_shell(),
    }


class ObserveRequest(BaseModel):
    scope: str = Field("window", description="window | screen")
    include_elements: bool = Field(True, description="是否读前台窗口控件结构（只读，不读文本）")


@router.post("/observe", response_model=Dict[str, Any])
def automation_observe(
    req: ObserveRequest, principal: str = Depends(require_human_principal)
) -> Dict[str, Any]:
    """只读观察：前台窗口（window）或窗口列表（screen）。无副作用。"""
    scope = TargetScope.SCREEN if req.scope == "screen" else TargetScope.WINDOW
    try:
        backend = create_backend(BackendKind.NATIVE)
        observation = backend.observe(scope)
    except AutomationError as exc:
        raise HTTPException(
            status_code=503,
            detail={"code": exc.reason, "message": str(exc)},
        ) from exc
    payload: Dict[str, Any] = observation.to_dict()
    if req.include_elements and isinstance(backend, object) and hasattr(backend, "read_foreground_elements"):
        payload["foreground_elements"] = backend.read_foreground_elements()  # type: ignore[attr-defined]
    return {"observation": payload, "backend": "native"}


class ActionRequestBody(BaseModel):
    action: str = Field(..., description="open | focus | close | foreground | list_windows")
    app: str = Field("", description="open 时的程序名：notepad | explorer | browser")
    window_title: str = Field("", description="focus / close 时的窗口标题（子串匹配）")
    confirmed: bool = Field(False, description="前端确认框点了确定（open/focus/close 必填）")


@router.post("/action", response_model=Dict[str, Any])
def automation_action(
    req: ActionRequestBody, principal: str = Depends(require_human_principal)
) -> Dict[str, Any]:
    """执行一个电脑操作。闸门、急停、确认三道检查都在执行前。"""
    gate = AutomationGate().evaluate(principal)
    if not gate.allowed:
        raise HTTPException(
            status_code=403,
            detail={
                "code": "automation_gate_closed",
                "message": f"电脑操控未开启（{gate.reason}）；请先在设置里打开自动化开关。",
                "gate": gate.as_dict(),
            },
        )
    if get_emergency_stop().active:
        raise HTTPException(
            status_code=409,
            detail={"code": "emergency_stop_active", "message": "急停已触发，先解除急停再操作。"},
        )
    action = (req.action or "").strip().lower()
    if action not in ("open", "focus", "close", "foreground", "list_windows"):
        raise HTTPException(
            status_code=400,
            detail={"code": "unsupported_action", "message": f"不支持的动作：{action}"},
        )
    if action in ("open", "focus", "close") and not req.confirmed:
        raise HTTPException(
            status_code=400,
            detail={
                "code": "confirmation_required",
                "message": "请先在确认框里确认这次电脑操作（关窗口不可逆，尤其要看清标题）。",
            },
        )
    backend = create_backend(BackendKind.NATIVE)
    action_request = ActionRequest(
        session_id=f"gateway-{principal}",
        step_no=int(time.time()) % 1_000_000,
        action=action,
        target=TargetScope.WINDOW,
        target_ref=req.window_title or req.app,
        params={"app": req.app, "window_title": req.window_title},
    )
    try:
        capability = backend.capability(action_request)
        # A2b 第 5 项：电脑操作（含打开浏览器）走可靠执行器——后端抛错
        # 才重试最多 3 次、单次超时 30 秒、失败说人话、每步留记录；
        # 闸门/急停/确认三道检查都在上面先过了，这里不绕过任何检查。
        from .a2b import OP_COMPUTER_TIMEOUT_SECONDS, run_reliable_operation

        outcome = run_reliable_operation(
            "computer",
            f"{action} {req.app or req.window_title}".strip(),
            lambda: backend.execute(action_request),
            timeout_seconds=OP_COMPUTER_TIMEOUT_SECONDS,
        )
        if not outcome["ok"]:
            raise AutomationError("execute_failed",
                                  str(outcome.get("message") or outcome.get("error") or "操作失败"))
        result = outcome["result"]
    except AutomationError as exc:
        raise HTTPException(
            status_code=503,
            detail={"code": exc.reason, "message": str(exc)},
        ) from exc
    try:
        AutomationAudit().record_action(
            principal=principal,
            session_id=action_request.session_id,
            step_no=action_request.step_no,
            action=action_request.action,
            outcome=result.status,
            target_ref=action_request.target_ref,
            params=dict(action_request.params),
            fingerprint=action_request.fingerprint(),
            stage="executed",
            error=result.error,
            reversibility=capability.reversibility.value,
        )
    except Exception as exc:  # noqa: BLE001 - 审计失败不掩盖执行结果，但要记日志
        logger.warning("automation audit failed: %s", exc)
    return {
        "status": result.status,
        "ok": result.ok,
        "action": result.action,
        "error": result.error,
        "detail": result.detail,
        "reversible": capability.reversible,
        "observation": result.observation.to_dict() if result.observation else None,
    }


class EstopRequest(BaseModel):
    reason: str = Field("manual", max_length=200)


@router.post("/estop", response_model=Dict[str, Any])
def automation_estop(
    req: EstopRequest, principal: str = Depends(require_human_principal)
) -> Dict[str, Any]:
    """急停：立即冻结后续电脑操作（前端红色急停按钮调它）。"""
    report = get_emergency_stop().trigger(req.reason or "manual")
    return {"emergency_stop": report}


@router.post("/estop/reset", response_model=Dict[str, Any])
def automation_estop_reset(principal: str = Depends(require_human_principal)) -> Dict[str, Any]:
    """解除急停（Owner 才能解除；普通成员只能触发，不能解除）。"""
    if not _is_owner(principal):
        raise HTTPException(
            status_code=403,
            detail={"code": "estop_reset_owner_only", "message": "只有最高权限账号才能解除急停。"},
        )
    get_emergency_stop().reset()
    return {"emergency_stop": {"active": False}}


class ShellRequest(BaseModel):
    command: str = Field(..., min_length=1, max_length=2000, description="要执行的命令")
    use_shell: bool = Field(False, description="是否经 shell 解释（管道/重定向才需要）")


def _run_shell_command(command: str, *, use_shell: bool, dangerous: bool) -> Dict[str, Any]:
    """真实执行一条已授权的 shell 命令（Owner + 闸门 + 审批都已通过才到这里）。

    A2b 第 5 项：执行走 ``a2b.run_reliable_operation``——启动失败重试
    最多 3 次（退避 1s/2s/4s）、单次超时 60 秒、失败说人话、每步留
    记录；超时不重试（底层进程可能还在跑，重试等于跑两遍）；命令本身
    跑完返回非零退出码也不重试。安全检查（闸门/审批/allowlist）都在
    调用方先过，这里不绕过任何检查。
    """
    from ..ai.world_interface import ShellAdapter, WorldRequest
    from .a2b import OP_SHELL_TIMEOUT_SECONDS, run_reliable_operation

    started = time.time()
    outcome = run_reliable_operation(
        "shell",
        f"shell: {command[:80]}",
        lambda: ShellAdapter().execute(
            WorldRequest(
                adapter="shell",
                action="run",
                params={"command": command, "shell": bool(use_shell or dangerous)},
                metadata={"actor": "owner-shell"},
            )
        ),
        timeout_seconds=OP_SHELL_TIMEOUT_SECONDS,
        retry_on_timeout=False,
    )
    if outcome["ok"]:
        result = outcome["result"] or {}
        try:
            exit_code: Optional[int] = int(result.get("returncode", 0))
        except (TypeError, ValueError):
            exit_code = None
    else:
        result = {"stdout": "", "stderr": outcome.get("message") or outcome.get("error") or "",
                  "returncode": None}
        exit_code = None
    duration_ms = (time.time() - started) * 1000.0
    try:
        from ..security.authority_audit import record_host_command

        record_host_command(
            command=command,
            exit_code=exit_code,
            duration_ms=duration_ms,
            actor="owner-shell",
            dangerous=dangerous,
            confirm_state="approved" if dangerous else "not_required",
            use_shell=bool(use_shell or dangerous),
        )
    except Exception as exc:  # noqa: BLE001 - 审计失败记日志，不掩盖执行结果
        logger.warning("shell audit failed: %s", exc)
    return {
        "stdout": str(result.get("stdout") or "")[:4000],
        "stderr": str(result.get("stderr") or "")[:4000],
        "returncode": exit_code,
        "duration_ms": round(duration_ms, 1),
    }


@router.post("/shell", response_model=Dict[str, Any])
def automation_shell(
    req: ShellRequest, principal: str = Depends(require_human_principal)
) -> Dict[str, Any]:
    """Shell 命令入口：默认关闭，仅 Owner；危险命令进审批中心等 Joseph 点确认。"""
    decision = evaluate_shell(req.command, is_owner=_is_owner(principal))
    if not decision.allowed and not decision.deferred:
        raise HTTPException(
            status_code=403,
            detail={
                "code": decision.reason,
                "message": {
                    "shell_disabled": "Shell 通道默认关闭，请先在设置里打开（仅 Owner 可开）。",
                    "shell_owner_only": "Shell 命令仅最高权限账号（Owner）可用。",
                    "escalation_refused": "提权命令一律拒绝（全权模式也不提权）。",
                    "empty_command": "命令不能为空。",
                }.get(decision.reason, decision.reason),
            },
        )
    if decision.deferred:
        from .decision_approval import (
            SHELL_DANGEROUS_TIMEOUT_SECONDS,
            SHELL_DEF_ID,
            create_decision_approval,
        )

        created = create_decision_approval(
            title=f"危险命令待确认：{req.command[:80]}",
            operation="shell.dangerous",
            reason=(
                f"Owner 请求执行危险命令（{decision.dangerous}），"
                "已进审批中心等你确认；确认前不会执行。"
            ),
            submitted_by=principal,
            owner_account_id=principal,
            def_id=SHELL_DEF_ID,
            timeout_seconds=SHELL_DANGEROUS_TIMEOUT_SECONDS,
            extra_inputs={"command": req.command},
        )
        return {
            "status": "deferred",
            "deferred": True,
            "approval_id": created["approval_id"],
            "run_id": created["run_id"],
            "dangerous": decision.dangerous,
            "message": "危险命令已送进审批中心，等你点了确认再执行。",
        }
    outcome = _run_shell_command(req.command, use_shell=req.use_shell, dangerous=False)
    return {"status": "executed", "deferred": False, **outcome}


@router.post("/shell/{approval_id}/execute", response_model=Dict[str, Any])
def automation_shell_execute_approved(
    approval_id: str, principal: str = Depends(require_human_principal)
) -> Dict[str, Any]:
    """执行一条已在审批中心通过的危险命令：先查审批状态，通过了才跑。"""
    if not _is_owner(principal):
        raise HTTPException(
            status_code=403,
            detail={"code": "shell_owner_only", "message": "Shell 命令仅最高权限账号（Owner）可用。"},
        )
    if not is_shell_enabled():
        raise HTTPException(
            status_code=403,
            detail={"code": "shell_disabled", "message": "Shell 通道已关闭。"},
        )
    from .decision_approval import decision_status

    approval = decision_status(approval_id)
    if approval is None:
        raise HTTPException(
            status_code=404,
            detail={"code": "approval_not_found", "message": f"审批不存在：{approval_id}"},
        )
    if str(approval.get("status")) != "approved":
        return {
            "status": "waiting",
            "approval_status": str(approval.get("status")),
            "message": "还在等审批：Joseph 还没点确认，这条命令不会执行。",
        }
    # 命令原文从 run 的 inputs 里原样取出（建待办时存的完整命令），绝不
    # 从截断过的标题里反推 —— 标题只截 80 字展示用。
    from src.workflow.store import get_workflow_store

    run = get_workflow_store().get_run(str(approval.get("run_id") or ""))
    command = str((run or {}).get("inputs", {}).get("command") or "").strip()
    if not command:
        raise HTTPException(
            status_code=400,
            detail={"code": "command_not_recoverable", "message": "审批里没有可执行的命令原文。"},
        )
    outcome = _run_shell_command(command, use_shell=True, dangerous=True)
    return {"status": "executed", "deferred": False, "approval_id": approval_id, **outcome}


__all__ = ["router"]
