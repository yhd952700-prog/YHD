"""拆任务 + 派给内部 worker + 同步审批结论。

拆分规则是关键词启发式（不调模型、不编造「已理解」）：

* 含「查 / 搜索 / 资料 / 联网」→ ``research``（查资料）；
* 含「文件 / 整理 / 归档 / 找文件」→ ``files``（整理文件）；
* 含「提醒 / 到点 / 定时 / 记得」→ ``reminder``（管提醒）；
* 含「代码 / 改代码 / 修 / 写程序」→ ``code``（写代码）；
* 都不含 → 一个 ``research`` 子任务「先弄清任务范围」，直接进审批问 Joseph。

执行（``advance_task``，只跑 ``queued`` 的子任务）：

* ``files``：只读列工作区文件，按任务里的关键词筛，返回真实匹配数；
* ``reminder``：调提醒服务真建一条提醒；建不出来转拍板；
* ``research`` / ``code``：没有可执行的真实 handler，直接建审批待办
  问 Joseph（范围 / 要不要做），状态 ``needs_decision``。

同步（``sync_decisions``）：读审批中心里那条待办的状态 —— 通过了，
子任务记 ``approved_waiting``（已批准，等对应能力接线：research 等 6-4
联网搜索，code 等自改代码二期）；拒绝了，子任务记 ``failed``，整单
任务记 ``cancelled``。批了不等于做完了，状态名本身就写明了这一点。
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Dict, List, Tuple

from .store import TeamStore

SUBTASK_KINDS = ("research", "files", "reminder", "code")

_KIND_KEYWORDS: Tuple[Tuple[str, Tuple[str, ...]], ...] = (
    ("research", ("查", "搜索", "资料", "联网", "找找", "了解")),
    ("files", ("文件", "整理", "归档", "找文件", "文件夹")),
    ("reminder", ("提醒", "到点", "定时", "记得", "别忘")),
    ("code", ("代码", "改代码", "修", "写程序", "加功能", "bug", "BUG")),
)

_KIND_LABELS = {
    "research": "查资料",
    "files": "整理文件",
    "reminder": "管提醒",
    "code": "写代码",
}

#: 批了之后等的能力接线说明（写进子任务 detail，界面直接展示）。
_WAITING_NOTES = {
    "research": "已批准，等联网搜索接线（6-4）后执行；现在不会假装已查完。",
    "code": "已批准，等自改代码二期（自动提议）后执行；现在不会假装已改完。",
}


def decompose(title: str) -> List[Tuple[str, str]]:
    """把一句话任务拆成 ``[(kind, 子任务标题)]``（至少一个，不编造）。"""
    text = str(title or "").strip()
    found: List[Tuple[str, str]] = []
    for kind, keywords in _KIND_KEYWORDS:
        if any(word in text for word in keywords):
            found.append((kind, f"{_KIND_LABELS[kind]}：{text[:60]}"))
    if not found:
        found.append(("research", f"先弄清任务范围：{text[:60]}"))
    return found


def create_team_task(
    store: TeamStore, title: str, created_by: str
) -> Dict[str, Any]:
    """建单 + 拆子任务 + 记事件；不执行任何子任务（执行走 ``advance_task``）。"""
    title = str(title or "").strip()
    if not title:
        raise ValueError("任务标题不能为空")
    task = store.create_task(title, created_by)
    task_id = str(task["task_id"])
    for kind, subtask_title in decompose(title):
        store.add_subtask(task_id, kind, subtask_title)
    store.add_event(task_id, "created", f"Jarvis 把任务拆成 {len(decompose(title))} 个子任务")
    return store.get_task(task_id) or task


def _workspace_root() -> Path:
    from src.common.data_paths import data_root

    root = data_root()
    if root is not None:
        return root / "workspace"
    return Path(__file__).resolve().parents[2] / "data" / "workspace"


def _run_files_subtask(store: TeamStore, task_id: str, subtask: Dict[str, Any], title: str) -> None:
    """整理文件 worker：只读列工作区文件，返回真实匹配结果。"""
    store.update_subtask(str(subtask["subtask_id"]), status="running", progress=30)
    root = _workspace_root()
    keywords = [word for word in ("文件", "整理", "归档") if word in title]
    matched: List[str] = []
    total = 0
    if root.is_dir():
        for path in sorted(root.rglob("*")):
            if not path.is_file():
                continue
            total += 1
            if total > 500:
                break
            if not keywords or any(word in path.name for word in keywords):
                matched.append(path.name)
                if len(matched) >= 20:
                    break
    store.update_subtask(
        str(subtask["subtask_id"]),
        status="done",
        progress=100,
        detail=f"工作区共 {total} 个文件，匹配 {len(matched)} 个：{', '.join(matched[:10]) or '无'}",
    )
    store.add_event(task_id, "subtask_done", f"整理文件完成：匹配 {len(matched)} 个文件")


def _run_reminder_subtask(
    store: TeamStore, task_id: str, subtask: Dict[str, Any], title: str, principal: str
) -> None:
    """管提醒 worker：调提醒服务真建提醒；建不出来转拍板。"""
    store.update_subtask(str(subtask["subtask_id"]), status="running", progress=30)
    try:
        from src.tasks.reminders import get_reminder_service

        record = get_reminder_service().create(principal, title)
        store.update_subtask(
            str(subtask["subtask_id"]),
            status="done",
            progress=100,
            detail=f"提醒已建：{record.get('title') or title}（{record.get('run_at') or ''}）",
        )
        store.add_event(task_id, "subtask_done", "提醒已创建")
    except Exception as exc:  # noqa: BLE001 - 建提醒失败不算任务失败，转拍板问人
        _ask_decision(
            store,
            task_id,
            subtask,
            principal,
            question=f"提醒没建起来（{type(exc).__name__}）：要不要换个说法再建？原文：{title[:80]}",
        )


def _ask_decision(
    store: TeamStore,
    task_id: str,
    subtask: Dict[str, Any],
    principal: str,
    *,
    question: str,
) -> None:
    """需要拍板：建审批中心待办，子任务记 needs_decision（等 Joseph 点）。"""
    from src.gateway.decision_approval import TEAM_DEF_ID, create_decision_approval

    created = create_decision_approval(
        title=f"小队子任务待拍板：{subtask['title'][:60]}",
        operation="team.decision",
        reason=question,
        submitted_by=principal,
        owner_account_id=principal,
        def_id=TEAM_DEF_ID,
    )
    store.update_subtask(
        str(subtask["subtask_id"]),
        status="needs_decision",
        progress=50,
        detail=question,
        approval_id=created["approval_id"],
        run_id=created["run_id"],
    )
    store.add_event(
        task_id,
        "needs_decision",
        f"子任务「{subtask['title'][:30]}」已送进审批中心等拍板",
    )


def advance_task(store: TeamStore, task_id: str, principal: str) -> Dict[str, Any]:
    """推进一单：只跑 ``queued`` 的子任务；research/code 直接进审批问人。

    子任务按 ``kind`` 对应一个小队成员（``team_members`` 表；派活时按需为
    该 kind 建一条记录，不再首次打开/首次推进就播种 4 人）；成员被停用时
    该子任务转拍板问人，不静默跳过、也不假装有别人顶上。
    """
    task = store.get_task(task_id)
    if task is None:
        raise ValueError(f"任务不存在：{task_id}")
    title = str(task.get("title") or "")
    for subtask in task.get("subtasks") or []:
        if str(subtask.get("status")) != "queued":
            continue
        kind = str(subtask.get("kind"))
        member = None
        try:
            member = store.get_member_by_kind(principal, kind)
        except AttributeError:  # pragma: no cover - 旧替身 store 没有成员表
            member = None
        if member is None and kind in _KIND_LABELS:
            # 空白开局：派活时按需为该 kind 建一条成员记录（只建这一种），
            # 没派过活的 kind 不在前台出现；4 种角色本身不删。
            try:
                member = store.ensure_member_for_kind(principal, kind)
            except AttributeError:  # pragma: no cover - 旧替身 store 没有成员表
                member = None
        if member is not None and not member.get("active"):
            _ask_decision(
                store,
                task_id,
                subtask,
                principal,
                question=(
                    f"子任务「{_KIND_LABELS.get(kind, kind)}」对应的成员"
                    f"「{member.get('name') or kind}」已被停用：先在小队里启用它"
                    "（或换个成员），再决定要不要做这一步。"
                ),
            )
            continue
        if kind == "files":
            _run_files_subtask(store, task_id, subtask, title)
        elif kind == "reminder":
            _run_reminder_subtask(store, task_id, subtask, title, principal)
        elif kind in ("research", "code"):
            _ask_decision(
                store,
                task_id,
                subtask,
                principal,
                question=(
                    f"子任务「{_KIND_LABELS.get(kind, kind)}」需要你拍板："
                    f"范围是「{title[:80]}」，确认无误我就动手；"
                    "现在还没有自动执行这一步的能力，批了我也不会假装已做完。"
                ),
            )
        else:
            store.update_subtask(
                str(subtask["subtask_id"]),
                status="failed",
                detail=f"未知的子任务类型：{kind}",
            )
    task = store.get_task(task_id) or task
    statuses = [str(item.get("status")) for item in task.get("subtasks") or []]
    if statuses and all(status == "done" for status in statuses):
        store.update_task_status(task_id, "done")
    elif any(status == "needs_decision" for status in statuses):
        store.update_task_status(task_id, "waiting_decision")
    elif any(status == "failed" for status in statuses):
        store.update_task_status(task_id, "partial")
    return store.get_task(task_id) or task


def sync_decisions(store: TeamStore, task_id: str) -> Dict[str, Any]:
    """把审批中心的结论同步回子任务：通过→approved_waiting，拒绝→failed。"""
    from src.gateway.decision_approval import decision_status

    task = store.get_task(task_id)
    if task is None:
        raise ValueError(f"任务不存在：{task_id}")
    changed = False
    for subtask in task.get("subtasks") or []:
        if str(subtask.get("status")) != "needs_decision":
            continue
        approval_id = str(subtask.get("approval_id") or "")
        if not approval_id:
            continue
        approval = decision_status(approval_id)
        if approval is None:
            continue
        status = str(approval.get("status"))
        if status == "approved":
            kind = str(subtask.get("kind"))
            store.update_subtask(
                str(subtask["subtask_id"]),
                status="approved_waiting",
                progress=60,
                detail=_WAITING_NOTES.get(kind, "已批准，等对应能力接线后执行。"),
            )
            store.add_event(task_id, "decision_approved", f"子任务「{subtask['title'][:30]}」已批准")
            changed = True
        elif status == "rejected":
            store.update_subtask(
                str(subtask["subtask_id"]),
                status="failed",
                detail="Joseph 在审批中心拒绝了这个子任务。",
            )
            store.add_event(task_id, "decision_rejected", f"子任务「{subtask['title'][:30]}」被拒绝")
            changed = True
    if changed:
        task = store.get_task(task_id) or task
        statuses = [str(item.get("status")) for item in task.get("subtasks") or []]
        if any(status == "failed" for status in statuses):
            store.update_task_status(task_id, "partial")
        elif all(status in ("done", "approved_waiting") for status in statuses) and statuses:
            store.update_task_status(task_id, "waiting_capability")
    return store.get_task(task_id) or task


__all__ = [
    "SUBTASK_KINDS",
    "advance_task",
    "create_team_task",
    "decompose",
    "sync_decisions",
]
