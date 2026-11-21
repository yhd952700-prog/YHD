"""第二季 V8：多 Bot 协作编排服务。

语义（设计文档 upgrade2-evidence/v8-design.md）：主 Bot（Lead）把目标分解
成带依赖的子任务（复用 GoalTaskGraph 引擎），按成员岗位确定性派活，成员
经与单聊同一条内部路径执行，结果汇总回主线；失败如实呈现为「部分完成 +
原因」，绝不冒充全成。

与设计的两处实现裁决（已在第二季总报告 V8 节记录）：
1. 汇总步不做图内节点：引擎的就绪语义只认 completed 依赖，成员失败时图内
   汇总节点永远等不到就绪、会被 blocked 收尾——而汇总恰恰在部分失败时最
   重要。故汇总 = execute_graph 返回后由 Lead 追加一轮执行（同一 runner、
   同一简报格式），聚合结构与设计 §4.5 一致。
2. 成员选择的「Lead 模型选人」留 chooser 钩子，默认走确定性规则（岗位直配
   → 负载兜底）：可复现、可单测、零额外花费；钩子在位，后续接模型选人
   不改接口。

派活并发口径：直连 execute_graph 的 max_parallel（默认 3），不叠
a2a.DispatchQueue 的第二层限流（设计 §5 缺口 4 的二选一裁决：编排直连
引擎；DispatchQueue 保持其外部派活语义，未被本服务消费，特此注明）。
"""

from __future__ import annotations

import threading
import time
from typing import Any, Callable, Dict, List, Optional, Sequence

from src.ai.goal_task_graph import GoalStatus, TaskNode, TaskStatus
from src.workbench.store import JARVIS_ID

#: task_type → 岗位关键词（成员 Bot 的 job 字段命中任一即算岗位直配）。
TASK_JOB_KEYWORDS: Dict[str, List[str]] = {
    "research": ["调研", "市场", "研究", "情报", "搜索"],
    "writing": ["文案", "编辑", "写作", "撰稿", "内容"],
    "data": ["数据", "分析", "统计", "表格"],
    "review": ["审核", "校对", "质检", "审查", "把关"],
    "tool": ["执行", "助理", "操作", "跑腿"],
    "general": [],
}

#: 分解产物的 task_type 收敛域（设计 §3）。
ORCHESTRATION_TASK_TYPES = tuple(TASK_JOB_KEYWORDS.keys())

_UPSTREAM_EXCERPT_CHARS = 800
_BRIEF_MAX_CHARS = 6000


class MemberCapReached(Exception):
    """成员 Bot 已达月度用量上限：不重试、不换人，直接按失败收尾并提示用户。"""


class NoMemberAvailable(Exception):
    """群里没有能接这个任务的成员（池为空/全被排除）：如实失败，不静默改派 Lead。"""


class OrchestrationCancelled(Exception):
    """用户中途喊停后，未开始的任务以此收尾（聚合层呈现为 skipped）。"""


def job_matches(task_type: str, job: str) -> bool:
    keywords = TASK_JOB_KEYWORDS.get(task_type or "general", [])
    text = str(job or "")
    return any(kw in text for kw in keywords)


def _member_load_order(members: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
    return sorted(
        members,
        key=lambda m: (int(m.get("order_index") or 0), str(m.get("id") or "")),
    )


def select_assignments(
    tasks: Sequence[TaskNode],
    members: Sequence[Dict[str, Any]],
    *,
    chooser: Optional[Callable[[TaskNode, List[Dict[str, Any]]], Optional[str]]] = None,
) -> Dict[str, Optional[str]]:
    """确定性成员选择（设计 §4.1）。返回 task_id → bot_id（None = 无人可派）。

    规则顺序：岗位直配唯一命中 → 直派；多命中/无命中 → chooser 钩子（默认
    None）→ 当前已派任务最少的成员（平局按 order_index、id）。同一成员在
    本轮的负载按已派数累计。结果同时写回 task.metadata["bot_id"] 与
    task.assigned_agent（引擎的预绑定不覆盖口径与此配套）。
    """
    pool = [m for m in members if not m.get("hidden")]
    load: Dict[str, int] = {str(m["id"]): 0 for m in pool}
    assignments: Dict[str, Optional[str]] = {}
    for task in tasks:
        if task.metadata.get("kind") == "synthesis":
            continue
        chosen: Optional[str] = None
        reason = ""
        direct = [m for m in pool if job_matches(task.task_type, str(m.get("job") or ""))]
        if len(direct) == 1:
            chosen = str(direct[0]["id"])
            reason = "岗位直配"
        else:
            candidates = direct if direct else list(pool)
            if chooser is not None and candidates:
                picked = chooser(task, candidates)
                if picked:
                    chosen = str(picked)
                    reason = "Lead 选人"
            if chosen is None and candidates:
                ordered = _member_load_order(candidates)
                chosen = str(min(ordered, key=lambda m: load[str(m["id"])])["id"])
                reason = "负载兜底" if not direct else "岗位多命中·负载兜底"
        if chosen is not None:
            load[chosen] += 1
            task.metadata["bot_id"] = chosen
            task.assigned_agent = chosen
        task.metadata["select_reason"] = reason or "无可用成员"
        assignments[task.id] = chosen
    return assignments


def build_brief(
    tasks_by_id: Dict[str, TaskNode],
    task: TaskNode,
    *,
    handover: Optional[Dict[str, str]] = None,
) -> str:
    """任务简报 = 子任务描述 + 上游已完成结果摘录（设计 §4.3 第 3 点）。"""
    parts: List[str] = [f"任务：{task.description}"]
    if handover:
        parts.append(
            f"（接手任务：原成员「{handover.get('from_name', '')}」执行失败，"
            f"原因：{handover.get('error', '')}。请你接手完成。）"
        )
    for dep_id in task.depends_on:
        dep = tasks_by_id.get(dep_id)
        if dep is None or dep.status != TaskStatus.COMPLETED or dep.result is None:
            continue
        excerpt = str(dep.result)[:_UPSTREAM_EXCERPT_CHARS]
        parts.append(f"上游结果（来自任务 {dep.id}）：{excerpt}")
    brief = "\n".join(parts)
    return brief[:_BRIEF_MAX_CHARS]


def build_synthesis_brief(
    goal_description: str,
    tasks: Sequence[TaskNode],
    *,
    lead_did: Optional[List[str]] = None,
) -> str:
    """汇总简报：目标 + 每项子任务的结局（成功给摘录、失败给原因）。"""
    parts: List[str] = [
        f"总目标：{goal_description}",
        "请把下面各成员的子任务结果汇总成一份给用户的完整答复。",
    ]
    failed = [t for t in tasks if t.status != TaskStatus.COMPLETED]
    if failed:
        names = "、".join(
            f"{t.id}（{t.metadata.get('bot_name') or t.metadata.get('bot_id') or '未派'}）："
            f"{t.error or t.status.value}"
            for t in failed
        )
        parts.append(
            f"注意：有 {len(failed)} 项没有完成，开头先如实说明哪几项没成、为什么，"
            f"不许假装全部完成。未完成项：{names}"
        )
    for task in tasks:
        who = task.metadata.get("bot_name") or task.metadata.get("bot_id") or "?"
        if task.status == TaskStatus.COMPLETED:
            parts.append(f"[{task.id} · {who} · 已完成] {str(task.result)[:_UPSTREAM_EXCERPT_CHARS]}")
        else:
            parts.append(f"[{task.id} · {who} · {task.status.value}] 原因：{task.error or '无'}")
    if lead_did:
        parts.append(f"（其中 {', '.join(lead_did)} 是 Lead 自己完成的，汇总里要明示。）")
    return "\n".join(parts)[:_BRIEF_MAX_CHARS]


def aggregate(
    goal_id: str,
    goal_description: str,
    tasks: Sequence[TaskNode],
    *,
    synthesis: Optional[str] = None,
    usage_snapshot: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """聚合结构（设计 §4.5）：逐项结局 + 总状态 + 用量（按 task:<id> 拆账）。"""
    by_label = (usage_snapshot or {}).get("by_label", {}) if usage_snapshot else {}
    items: List[Dict[str, Any]] = []
    completed = failed = skipped = 0
    for task in tasks:
        label = by_label.get(f"task:{task.id}", {})
        if task.status == TaskStatus.COMPLETED:
            status = "completed"
            completed += 1
        elif task.metadata.get("cancelled"):
            status = "skipped"
            skipped += 1
        else:
            status = "failed"
            failed += 1
        items.append({
            "task_id": task.id,
            "bot_id": task.metadata.get("bot_id"),
            "bot_name": task.metadata.get("bot_name"),
            "status": status,
            "output": str(task.result) if task.result is not None else None,
            "error": task.error,
            "attempts": task.metadata.get("attempts", []),
            "usage": {
                "tokens_in": int(label.get("tokens_in", 0)),
                "tokens_out": int(label.get("tokens_out", 0)),
                "model": label.get("model"),
            },
        })
    if completed and not failed and not skipped:
        overall = "completed"
    elif not completed:
        overall = "failed"
    else:
        overall = "partial"
    return {
        "goal_id": goal_id,
        "goal_description": goal_description,
        "status": overall,
        "completed": completed,
        "failed": failed,
        "skipped": skipped,
        "items": items,
        "synthesis": synthesis,
        "usage_total": {
            "tokens_in": int((usage_snapshot or {}).get("tokens_in", 0)),
            "tokens_out": int((usage_snapshot or {}).get("tokens_out", 0)),
            "calls": int((usage_snapshot or {}).get("calls", 0)),
            "by_label": by_label,
        },
    }


class TeamOrchestrator:
    """一次团队编排的运行时。runner/cap_status/notify 全部注入，便于演练与单测。"""

    def __init__(
        self,
        graph: Any,
        members: Sequence[Dict[str, Any]],
        runner: Callable[..., str],
        *,
        lead_id: str = JARVIS_ID,
        lead_name: str = "贾维斯",
        cap_status: Optional[Callable[[str], Dict[str, Any]]] = None,
        notify: Optional[Callable[[str, Dict[str, Any]], None]] = None,
        chooser: Optional[Callable[[TaskNode, List[Dict[str, Any]]], Optional[str]]] = None,
        state_path: Optional[str] = None,
    ) -> None:
        self.graph = graph
        self.members = [dict(m) for m in members]
        self.runner = runner
        self.lead_id = lead_id
        self.lead_name = lead_name
        self.cap_status = cap_status
        self.notify = notify
        self.chooser = chooser
        self.state_path = state_path
        self._cancelled = threading.Event()
        self._bot_locks: Dict[str, threading.Lock] = {}
        self._locks_guard = threading.Lock()

    # -- 计划（预览，不执行） -------------------------------------------------
    def plan(self, goal_id: str) -> Dict[str, Any]:
        tasks = self.graph.get_tasks_for_goal(goal_id)
        assignments = select_assignments(tasks, self.members, chooser=self.chooser)
        by_id = {str(m["id"]): m for m in self.members}
        preview = []
        for task in tasks:
            bot_id = assignments.get(task.id)
            bot = by_id.get(bot_id or "")
            task.metadata["bot_name"] = (bot or {}).get("name") if bot else None
            preview.append({
                "task_id": task.id,
                "description": task.description,
                "task_type": task.task_type,
                "depends_on": list(task.depends_on),
                "bot_id": bot_id,
                "bot_name": (bot or {}).get("name") if bot else None,
                "select_reason": task.metadata.get("select_reason"),
            })
        return {
            "goal_id": goal_id,
            "goal_description": self.graph.goal.description if self.graph.goal else "",
            "task_count": len(tasks),
            "member_count": len([m for m in self.members if not m.get("hidden")]),
            "tasks": preview,
            "unassigned": [p["task_id"] for p in preview if not p["bot_id"]],
        }

    # -- 取消（软取消：未开始的置 skipped 口径，进行中的自然收尾） ------------
    def request_cancel(self) -> None:
        self._cancelled.set()

    # -- 执行 -----------------------------------------------------------------
    def _bot_lock(self, bot_id: str) -> threading.Lock:
        with self._locks_guard:
            return self._bot_locks.setdefault(bot_id, threading.Lock())

    def _member_brief_for(self, task: TaskNode, handover: Optional[Dict[str, str]] = None) -> str:
        return build_brief(self.graph.tasks, task, handover=handover)

    def _run_one(self, task: TaskNode, bot_id: str, brief: str, attempt: int) -> str:
        if self.cap_status is not None and bot_id != self.lead_id:
            status = self.cap_status(bot_id)
            if status.get("capped"):
                raise MemberCapReached(
                    f"成员「{task.metadata.get('bot_name') or bot_id}」本月用量已达上限"
                    f"（${status.get('cap_usd')}），这件活先停。要继续，去设置里调高它的上限。"
                )
        with self._bot_lock(bot_id):
            return self.runner(
                bot_id,
                brief,
                idempotency_key=f"{self.graph.goal.id if self.graph.goal else 'goal'}:{task.id}:{attempt}",
            )

    def _executor(self, agent_id: str, description: str, task: TaskNode) -> str:
        if self._cancelled.is_set():
            task.metadata["cancelled"] = True
            raise OrchestrationCancelled("用户已喊停，这个任务没有开始。")
        bot_id = task.metadata.get("bot_id")
        if not bot_id:
            raise NoMemberAvailable("群里没有能干这活的成员（成员池为空或无人匹配）。")
        by_id = {str(m["id"]): m for m in self.members}
        attempts: List[Dict[str, Any]] = task.metadata.setdefault("attempts", [])
        tried: List[str] = []
        current = bot_id
        attempt_no = 0
        while True:
            attempt_no += 1
            bot = by_id.get(current, {})
            task.metadata["bot_id"] = current
            task.metadata["bot_name"] = bot.get("name")
            task.assigned_agent = current
            handover = None
            if attempts:
                last = attempts[-1]
                handover = {"from_name": last.get("bot_name") or last.get("bot_id") or "",
                            "error": last.get("error") or ""}
            brief = self._member_brief_for(task, handover=handover)
            try:
                return self._run_one(task, current, brief, attempt_no)
            except MemberCapReached:
                attempts.append({"bot_id": current, "bot_name": bot.get("name"),
                                 "error": "已达月度用量上限（不换人重试）"})
                raise
            except OrchestrationCancelled:
                raise
            except Exception as exc:  # 成员失败：换人重试 1 次（同岗位优先）
                attempts.append({"bot_id": current, "bot_name": bot.get("name"),
                                 "error": str(exc)})
                tried.append(current)
                if attempt_no >= 2:
                    raise
                substitute = self._find_substitute(task, tried)
                if substitute is None:
                    raise
                current = substitute

    def _find_substitute(self, task: TaskNode, tried: List[str]) -> Optional[str]:
        pool = [m for m in self.members
                if not m.get("hidden") and str(m["id"]) not in tried
                and str(m["id"]) != self.lead_id]
        if not pool:
            return None
        same_job = [m for m in pool if job_matches(task.task_type, str(m.get("job") or ""))]
        candidates = same_job or pool
        return str(_member_load_order(candidates)[0]["id"])

    def run(
        self,
        goal_id: str,
        *,
        task_timeout_seconds: float = 300.0,
        max_parallel: int = 3,
    ) -> Dict[str, Any]:
        from src.ai.usage_meter import meter_scope, snapshot

        plan = self.plan(goal_id)
        tasks = self.graph.get_tasks_for_goal(goal_id)
        started = time.time()
        with meter_scope() as meter:
            self.graph.execute_graph(
                goal_id,
                self._executor,
                max_parallel=max_parallel,
                task_timeout_seconds=task_timeout_seconds,
                pass_task=True,
            )
            # 汇总：Lead 在图外追加一轮（裁决 1）。成员全失败也照样汇总，
            # 如实交代；汇总本身失败则 synthesis=None 并在聚合里注明。
            synthesis: Optional[str] = None
            synthesis_error: Optional[str] = None
            brief = build_synthesis_brief(
                self.graph.goal.description if self.graph.goal else "", tasks)
            try:
                synthesis = self.runner(
                    self.lead_id, brief,
                    idempotency_key=f"{goal_id}:synthesis",
                )
            except Exception as exc:  # noqa: BLE001 - 汇总失败如实标注，不假装
                synthesis_error = str(exc)
            usage = snapshot(meter)
        result = aggregate(
            goal_id,
            self.graph.goal.description if self.graph.goal else "",
            tasks,
            synthesis=synthesis,
            usage_snapshot=usage,
        )
        result["plan"] = plan
        result["duration_seconds"] = round(time.time() - started, 2)
        if not result["usage_total"]["calls"]:
            # 生产路径下成员每轮自带计量并记入各自的账号账本（按 Bot 在
            # 用量面可查）；本聚合的 by_label 只统计直接在本作用域内上报
            # 的调用。不许拿 0 冒充「没花钱」，在此明示账的去处。
            result["usage_note"] = (
                "成员各自的用量已记入其账号账本（按 Bot 查用量）；"
                "此处仅统计编排作用域内直接上报的调用。"
            )
        if synthesis_error:
            result["synthesis_error"] = synthesis_error
        if self.state_path:
            try:
                self.graph.save_state(self.state_path)
                result["state_path"] = self.state_path
            except Exception as exc:  # noqa: BLE001 - 存档失败不改变执行结论
                result["state_error"] = str(exc)
        if self.notify is not None:
            kind = "done" if result["status"] == "completed" else "stuck"
            try:
                self.notify(kind, result)
            except Exception:  # noqa: BLE001 - 通知失败不改变执行结论
                pass
        return result
