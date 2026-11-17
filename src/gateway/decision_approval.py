"""把「需要 Joseph 拍板」的事送进审批中心（0.4.6 D 组）。

电脑操控的危险 shell 命令、小队里需要拍板的子任务，都走这里：建一个
最小的工作流 run（定义里只有一个审批节点），再建一条待办。Joseph 在
审批中心（``GET /v1/approvals``）里能看到、能点通过/拒绝；点了通过，
run 正常完成；点了拒绝，run 取消 —— 用的是工作流引擎现成的
``apply_approval_decision``，不是另起一套审批表，所以审批中心的通过/
拒绝按钮对这些待办同样有效。

调用方拿到 ``approval_id`` 后轮询 ``get_workflow_store().get_approval()``
看状态：``pending`` 还在等，``approved`` / ``rejected`` 已有结论。
"""

from __future__ import annotations

from typing import Any, Dict, Optional, Sequence

TEAM_DEF_ID = "jarvis-team-decision"
SHELL_DEF_ID = "jarvis-shell-decision"
DECISION_NODE_ID = "decision"
DECISION_TIMEOUT_SECONDS = 7 * 24 * 3600
#: 危险 shell 命令待办的过期时间：30 分钟（防诱导 §3.1）。
#: 危险命令的上下文很快就过期（一周后谁还记得当时要删的是什么），
#: 7 天太长——Joseph 应该在命令还新鲜的时候就决定，过期未决的待办
#: 自动失效，不再能批。小队拍板（team.decision）仍用 7 天。
SHELL_DANGEROUS_TIMEOUT_SECONDS = 30 * 60


def _definition(name: str) -> Dict[str, Any]:
    return {
        "name": name,
        "nodes": [
            {"id": DECISION_NODE_ID, "type": "approval", "name": "等 Joseph 拍板"},
        ],
        "edges": [],
    }


def create_decision_approval(
    *,
    title: str,
    operation: str,
    reason: str,
    submitted_by: str,
    owner_account_id: str,
    def_id: str = TEAM_DEF_ID,
    timeout_seconds: int = DECISION_TIMEOUT_SECONDS,
    extra_inputs: Optional[Dict[str, Any]] = None,
    approvers: Optional[Sequence[str]] = None,
) -> Dict[str, Any]:
    """建 run + step + 待办，返回 ``{"run_id", "approval_id", "approval"}``。

    ``operation`` 是给审批中心看的操作名（如 ``shell.dangerous`` /
    ``team.decision``），``reason`` 写清楚「要决定什么、为什么要问」。
    ``extra_inputs`` 存进 run 的 inputs（例如 shell 危险命令的完整原文），
    供批复后执行时原样取出 —— 绝不能从截断过的标题里反推命令原文。

    ``approvers``：谁能批这条待办。默认是提交人自己（自批）；删文件时
    由调用方按规矩传：提交人不是 Owner 时传 Owner（须 Owner 批），
    Owner 删自己文件时不传（自批）。注意只改 approvers 不够——
    审批中心还允许 ``owner_account_id`` 本人拍板，所以非 Owner 提交
    时调用方必须同时把 ``owner_account_id`` 设成 Owner，否则提交人
    仍能以“归属人”身份自己批掉。
    """
    from src.workflow.store import get_workflow_store

    store = get_workflow_store()
    inputs: Dict[str, Any] = {
        "title": title,
        "operation": operation,
        "reason": reason,
    }
    if extra_inputs:
        inputs.update(extra_inputs)
    run = store.create_run(
        _definition(title),
        def_id=def_id,
        def_version=1,
        trigger_type="manual",
        trigger_payload={"title": title, "operation": operation},
        inputs=inputs,
        owner_account_id=owner_account_id,
        submitted_by=submitted_by,
    )
    run_id = str(run["run_id"])
    step = store.begin_step(
        run_id,
        node={"id": DECISION_NODE_ID, "type": "approval", "name": "等 Joseph 拍板"},
        seq=1,
        inputs={"title": title, "operation": operation},
    )
    approval = store.create_approval(
        run_id=run_id,
        step_id=str(step["step_id"]),
        node_id=DECISION_NODE_ID,
        def_id=def_id,
        title=title,
        operation=operation,
        reason=reason,
        approvers=list(approvers) if approvers is not None else [submitted_by],
        submitted_by=submitted_by,
        owner_account_id=owner_account_id,
        timeout_seconds=timeout_seconds,
    )
    return {"run_id": run_id, "approval_id": str(approval["approval_id"]), "approval": approval}


def decision_status(approval_id: str) -> Optional[Dict[str, Any]]:
    """读一条拍板待办的最新状态；不存在返回 None。"""
    from src.workflow.store import get_workflow_store

    return get_workflow_store().get_approval(approval_id)


__all__ = [
    "DECISION_NODE_ID",
    "DECISION_TIMEOUT_SECONDS",
    "SHELL_DANGEROUS_TIMEOUT_SECONDS",
    "SHELL_DEF_ID",
    "TEAM_DEF_ID",
    "create_decision_approval",
    "decision_status",
]
