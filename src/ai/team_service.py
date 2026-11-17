"""贾维斯的一队：队长单例（7-3 Step 2/3/4，七家合并 P5/P6）。

Joseph 只跟一个贾维斯说话——这里是队长（贾维斯本体）建队、派活、汇总、
拍板、急停的唯一入口：

* 建队：队长 + 查资料 / 整理 / 提醒 / 写代码 4 个固定角色，各 1 名队员；
  队员拿角色提示词（``collaboration.ROLE_SYSTEM_PROMPTS``），没有独立
  聊天入口、不直接接 Joseph 的消息。
* 派活：经 ``MultiAgentTeam.delegate_async``（异步、120 秒超时、
  超时/连接失败重试 1 次、同一队员已有在跑任务时 409、不并行抢占），
  任务与消息落 ``team_store``（SQLite，重启后还在）。
* 拍板：含危险动作的派活先挂为 awaiting_approval，Joseph 批了才跑、
  拒了标 rejected；没批之前队员一个字也不执行。
* 急停：``stop_all`` 一次取消全部在跑与待批任务（只做 DB 写 + 标志位，
  5 秒内完成）；后台晚归的结果不再采用。
* 恢复：网关重启后，``recover_unfinished`` 把上次中断前还在跑的任务
  标失败并写清原因，待批任务原样保留（审批不丢）。
* 成品（P5）：队员完成时产物先落盘为真实文件（队员工作区
  ``agents/<agent_id>/workspace/<task_id>.md``），再记完成；写失败
  不谎称已交付。队员只能读写自己工作区里的文件（路径校验），Joseph
  与队长能看到全部。
* 换模型（P6）：每名队员可配不同模型——配的是 Joseph 在设置 → 模型管理
  里已保存的模型配置 id（Key 只存在模型配置中心，队员表里不存 Key）；
  没配就与贾维斯共用默认模型；配的模型找不到了老实报错、不静默换人。

审计：每次派活、审批决定、重试/取消/换人、急停都写审计
（``src/kernels/audit/``），归属调用方账号（队员的记录挂在队长名下）。
"""

from __future__ import annotations

import logging
import re
import threading
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence

from src.common.data_paths import repo_root, resolve_store_path

from .collaboration import (
    DANGER_KINDS,
    DEFAULT_DELEGATE_TIMEOUT_SECONDS,
    ROLE_SYSTEM_PROMPTS,
    Role,
)
from .team_store import STORE_MODULE_IMPORTED_AT, get_team_store

logger = logging.getLogger(__name__)

#: 角色名（网关/聊天传进来的字符串）→ Role 枚举。
ROLE_BY_NAME = {
    "manager": Role.MANAGER,
    "researcher": Role.RESEARCHER,
    "analyst": Role.ANALYST,
    "reminder": Role.REMINDER,
    "coder": Role.CODER,
}

#: 本进程启动时间（重启恢复的判定线：只有创建时间早于它的在跑任务，
#: 才可能是上个进程留下的；本进程内新建的任务绝不能被自动恢复误伤）。
#: 注意：不能用本模块自己的导入时间——本模块是懒导入的，任务可能在
#: 导入前就已建好。锚点取 team_store 的导入时间（见 STORE_MODULE_IMPORTED_AT）：
#: 建任务必经 team_store，所以本进程的任务一定晚于它。
_PROCESS_STARTED_AT = STORE_MODULE_IMPORTED_AT

#: 队员工作区 agent_id 只许安全字符（防路径穿越：队员只能进自己的目录）。
_AGENT_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")


def _audit(action: str, *, principal_id: str, outcome: str,
           details: Dict[str, Any], correlation_id: str) -> None:
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
    except Exception:  # noqa: BLE001 - 审计写不进去不能拖垮派活
        logger.warning("team audit failed: %s", action)


class TeamService:
    """队长（贾维斯）的一队：建队、派活、拍板、急停、恢复、汇总。"""

    def __init__(
        self,
        provider: Any = None,
        store: Any = None,
        provider_factory: Optional[Callable[[str], Any]] = None,
    ) -> None:
        self._provider = provider
        self._provider_factory = provider_factory
        # 显式传了 store（测试）就用它；没传则每次取 get_team_store() 单例，
        # 避免 reset_team_store() 之后还指着已关闭的旧连接。
        self._explicit_store = store
        self._team: Any = None
        self._manager_id: str = ""
        self._lock = threading.RLock()
        self._recovered = False

    @property
    def _store(self) -> Any:
        if self._explicit_store is not None:
            return self._explicit_store
        return get_team_store()

    # ── 建队 ──

    def _base_provider(self) -> Any:
        if self._provider is not None:
            return self._provider
        from .providers import get_provider

        return get_provider()

    def _ensure_team(self) -> Any:
        with self._lock:
            if self._team is not None:
                # store 单例被 reset 过（测试换库）时重建队伍，不指旧连接。
                if (
                    self._explicit_store is None
                    and self._team.team_store is not get_team_store()
                ):
                    self._team = None
                    self._manager_id = ""
                    self._recovered = False
                else:
                    return self._team
            from .collaboration import MultiAgentTeam

            team = MultiAgentTeam(
                "jarvis-team",
                provider=self._base_provider(),
                team_store=self._store,
            )
            team.artifact_writer = self.write_artifact
            self._manager_id = team.hire(
                Role.MANAGER, name="jarvis",
                system_prompt=ROLE_SYSTEM_PROMPTS[Role.MANAGER],
            )
            for role in (Role.RESEARCHER, Role.ANALYST, Role.REMINDER, Role.CODER):
                agent_id = team.hire(
                    role, name=role.value,
                    system_prompt=ROLE_SYSTEM_PROMPTS[role],
                )
                member = team.members[agent_id]
                member.agent.provider_resolver = (
                    lambda role_value=role.value: self.provider_for_member(role_value)
                )
                # 空白开局：建队只雇内存里的执行者，不写队员记录。
                # 队员记录只在真派活给这个角色时由 delegate_async 经
                # create_task/create_held_task + ensure_member 按需建；
                # 否则派一次活会凭空多出 3 名从没干过活的“空闲”队员。
            self._team = team
            if not self._recovered:
                self._recovered = True
                self.recover_unfinished()
            return team

    @property
    def manager_id(self) -> str:
        self._ensure_team()
        return self._manager_id

    # ── 派活 / 拍板 / 叫停 ──

    def dispatch(
        self,
        principal: str,
        role: str,
        task: str,
        *,
        plan_id: str = "",
        step_total: int = 1,
        timeout: float = DEFAULT_DELEGATE_TIMEOUT_SECONDS,
        approval_required: bool = False,
        danger_kind: str = "",
    ) -> Dict[str, Any]:
        """队长派活：校验 → delegate_async → 审计。"""
        role_key = (role or "").strip().lower()
        if role_key not in ROLE_BY_NAME or role_key == "manager":
            raise ValueError(
                f"没有这个队员角色：{role}（只认 researcher / analyst / "
                "reminder / coder；队长不接派活）"
            )
        if not (task or "").strip():
            raise ValueError("任务不能为空")
        if danger_kind and danger_kind not in DANGER_KINDS:
            raise ValueError(
                f"未知的危险动作种类：{danger_kind}（只认 {list(DANGER_KINDS)}）"
            )
        team = self._ensure_team()
        result = team.delegate_async(
            self._manager_id,
            ROLE_BY_NAME[role_key],
            task.strip(),
            plan_id=plan_id,
            step_total=max(1, step_total),
            timeout=float(timeout),
            approval_required=approval_required,
            danger_kind=danger_kind,
        )
        _audit(
            "team.dispatch_held" if result.get("status") == "awaiting_approval"
            else "team.dispatch",
            principal_id=principal,
            outcome="success" if result.get("status") in ("accepted", "awaiting_approval")
            else "deny",
            details={
                "role": role_key,
                "task_id": result.get("task_id") or "",
                "plan_id": plan_id,
                "approval_required": approval_required,
                "danger_kind": danger_kind,
                "status": result.get("status"),
                "error": result.get("error") or "",
            },
            correlation_id=str(result.get("correlation_id") or ""),
        )
        return result

    def approve_task(self, task_id: str, principal: str) -> Dict[str, Any]:
        """Joseph 批了：开始执行。"""
        team = self._ensure_team()
        result = team.approve_and_run(task_id)
        _audit(
            "team.approve", principal_id=principal,
            outcome="success" if result.get("status") == "accepted" else "deny",
            details={"task_id": task_id, "status": result.get("status"),
                     "error": result.get("error") or ""},
            correlation_id=str(result.get("correlation_id") or task_id),
        )
        return result

    def reject_task(self, task_id: str, principal: str, reason: str = "") -> Dict[str, Any]:
        """Joseph 拒了：任务标 rejected，什么都没执行。"""
        team = self._ensure_team()
        team._mark_final(task_id)
        try:
            record = self._store.reject_task(task_id, reason or "Joseph 拒绝了")
        except KeyError:
            return {"status": "failed", "error": f"unknown team task {task_id}"}
        _audit(
            "team.reject", principal_id=principal, outcome="success",
            details={"task_id": task_id, "role": record["role"],
                     "reason": reason or "Joseph 拒绝了"},
            correlation_id=str(record.get("correlation_id") or task_id),
        )
        return {"status": "rejected", "task_id": task_id, "record": record}

    def cancel_task(self, task_id: str, principal: str, reason: str = "") -> Dict[str, Any]:
        """叫停一条队员任务（记录标已取消；晚归结果不再采用）。"""
        team = self._ensure_team()
        result = team.cancel_team_task(task_id, reason or "Joseph 叫停")
        if result.get("status") == "cancelled":
            _audit(
                "team.cancel", principal_id=principal, outcome="success",
                details={"task_id": task_id, "reason": reason or "Joseph 叫停"},
                correlation_id=task_id,
            )
        return result

    def stop_all(self, principal: str) -> Dict[str, Any]:
        """急停（7-3 Step 4）：正在跑的 + 待批的队员任务全部停下。

        只做 DB 写 + 标志位，不等任何一次模型调用返回；耗时实测写进
        返回值与审计（目标 5 秒内）。
        """
        team = self._ensure_team()
        started = time.monotonic()
        cancelled: List[str] = []
        for status in ("running", "awaiting_approval"):
            for task in self._store.list_tasks(status=status, limit=200):
                task_id = str(task["task_id"])
                team._mark_final(task_id)
                try:
                    self._store.cancel_task(task_id, "急停：全队停下")
                except KeyError:
                    continue
                cancelled.append(task_id)
        elapsed = time.monotonic() - started
        _audit(
            "team.stop_all", principal_id=principal, outcome="success",
            details={
                "cancelled": cancelled,
                "cancelled_count": len(cancelled),
                "elapsed_seconds": round(elapsed, 3),
            },
            correlation_id="",
        )
        return {
            "cancelled": cancelled,
            "cancelled_count": len(cancelled),
            "elapsed_seconds": round(elapsed, 3),
        }

    # ── 重启恢复 ──

    def recover_unfinished(self, *, only_stale: bool = True) -> Dict[str, Any]:
        """重启恢复：上次中断前还在跑的任务标失败并写清原因；待批的保留。

        队伍本身（5 名队员）由 team_store 的种子保证，重启后还在；
        进行中的审批（workflow_approvals 表）不受影响，仍可点。

        ``only_stale=True``（默认，首次建队时自动调）：只恢复创建时间
        早于本进程启动的任务——本进程内新建的在跑任务绝不能被误伤。
        ``only_stale=False``（POST /v1/team/recover 显式调）：全量恢复。
        """
        interrupted: List[str] = []
        for task in self._store.list_tasks(status="running", limit=200):
            if only_stale and float(task.get("created_at") or 0) >= _PROCESS_STARTED_AT:
                continue
            task_id = str(task["task_id"])
            self._store.mark_interrupted(
                task_id, "网关重启前中断，未完成（重启后恢复，队员回空闲）"
            )
            interrupted.append(task_id)
        awaiting = [
            str(task["task_id"])
            for task in self._store.list_tasks(status="awaiting_approval", limit=200)
        ]
        return {"interrupted": interrupted, "awaiting_approval": awaiting}

    # ── 汇总 ──

    def summarize(
        self,
        task_ids: Optional[Sequence[str]] = None,
        plan_id: str = "",
    ) -> Dict[str, Any]:
        """汇总（验 → 并 → 报）：每条结论带来源，部分失败如实报。"""
        if task_ids:
            tasks = []
            for task_id in task_ids:
                try:
                    tasks.append(self._store.get_task(str(task_id)))
                except KeyError:
                    continue
        elif plan_id:
            tasks = self._store.list_tasks(limit=200)
            tasks = [t for t in tasks if t.get("plan_id") == plan_id]
        else:
            tasks = self._store.list_tasks(limit=50)
        done = [t for t in tasks if t["status"] == "done"]
        failed = [t for t in tasks if t["status"] in ("failed", "rejected")]
        blocked = [t for t in tasks if t["status"] == "needs_model"]
        pending = [
            t for t in tasks
            if t["status"] in ("running", "awaiting_approval", "pending")
        ]

        def _entry(task: Dict[str, Any]) -> Dict[str, Any]:
            return {
                "task_id": task["task_id"],
                "role": task["role"],
                "display_name": self._display_name(task["role"]),
                "agent_id": task["agent_id"],
                "title": task["title"],
                "status": task["status"],
                "result_text": task.get("result_text") or "",
                "sources": task.get("sources") or [],
                "artifacts": task.get("artifacts") or [],
                "error": task.get("error") or "",
            }

        lines = [
            f"已完成 {len(done)} 项，未完成 {len(failed) + len(pending) + len(blocked)} 项。"
        ]
        for task in failed:
            lines.append(
                f"未完成：{self._display_name(task['role'])}"
                f"「{task['title']}」——{task.get('error') or '失败原因未说明'}"
            )
        for task in blocked:
            lines.append(
                f"卡住了：{self._display_name(task['role'])}"
                f"「{task['title']}」——{task.get('error') or '没有配置模型'}"
            )
        for task in pending:
            lines.append(
                f"进行中：{self._display_name(task['role'])}"
                f"「{task['title']}」（{task['status']}）"
            )
        return {
            "plan_id": plan_id,
            "summary_text": "\n".join(lines),
            "completed": [_entry(t) for t in done],
            "failed": [_entry(t) for t in failed],
            "blocked": [_entry(t) for t in blocked],
            "pending": [_entry(t) for t in pending],
        }

    @staticmethod
    def _display_name(role: str) -> str:
        from .team_store import ROLE_DISPLAY_NAMES

        return ROLE_DISPLAY_NAMES.get(role, role)

    # ── 队员工作区与成品（P5）──

    def member_workspace_dir(self, agent_id: str) -> Path:
        """一名队员的工作区目录（只许安全字符，防路径穿越）。"""
        if not _AGENT_ID_RE.match(agent_id or ""):
            raise ValueError(f"队员 id 不合法：{agent_id!r}")
        path = Path(
            resolve_store_path(
                f"agents/{agent_id}/workspace/.keep",
                repo_root() / "data" / "agents" / agent_id / "workspace" / ".keep",
                workspace=True,
            )
        ).parent
        path.mkdir(parents=True, exist_ok=True)
        return path

    def write_artifact(
        self, agent_id: str, task_id: str, result_text: str
    ) -> Dict[str, Any]:
        """队员产物落盘为真实文件（P5），返回产物记录（记进任务 artifacts）。"""
        directory = self.member_workspace_dir(agent_id)
        title = ""
        try:
            title = str(self._store.get_task(task_id).get("title") or "")
        except KeyError:
            title = ""
        filename = f"{task_id}.md"
        path = directory / filename
        header = (
            f"# 队员产物\n\n- 任务：{title or task_id}\n"
            f"- 任务 id：{task_id}\n- 队员：{agent_id}\n"
            f"- 生成时间（UTC）：{time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())}\n\n---\n\n"
        )
        path.write_text(header + (result_text or ""), encoding="utf-8")
        return {
            "kind": "file",
            "name": filename,
            "path": str(path),
            "agent_id": agent_id,
        }

    def read_artifact(self, task_id: str, name: str) -> Dict[str, Any]:
        """读一条任务的产物文件（只许在队员工作区里读，防穿越）。"""
        if not name or "/" in name or "\\" in name or name in (".", ".."):
            raise ValueError(f"产物文件名不合法：{name!r}")
        task = self._store.get_task(task_id)
        artifacts = task.get("artifacts") or []
        match = next(
            (a for a in artifacts if isinstance(a, dict) and a.get("name") == name),
            None,
        )
        if match is None:
            raise KeyError(f"任务 {task_id} 没有这个产物：{name}")
        agent_id = str(match.get("agent_id") or task.get("agent_id") or "")
        path = self.member_workspace_dir(agent_id) / name
        if not path.is_file():
            raise FileNotFoundError(f"产物文件不在了：{path}")
        return {
            "name": name,
            "agent_id": agent_id,
            "path": str(path),
            "content": path.read_text(encoding="utf-8"),
        }

    # ── 队员换模型（P6）──

    def set_member_model(
        self, role: str, *, model_config_id: str = ""
    ) -> Dict[str, Any]:
        """给一名队员配模型：只认 Joseph 在设置里已保存的模型配置 id。

        空 id = 清掉，队员回到与贾维斯共用默认模型。配置不存在/已禁用
        时 404 语义的 ValueError，不静默换人、不编一个默认模型顶上。
        传 provider/model/base_url 直配是不接受的：队员不直接存 Key，
        Key 只由 Joseph 在设置 → 模型管理里填一次。
        """
        role_key = (role or "").strip().lower()
        if role_key not in ROLE_BY_NAME or role_key == "manager":
            raise ValueError(f"没有这个队员角色：{role}")
        config_id = (model_config_id or "").strip()
        if config_id:
            cfg = self._resolve_model_config(config_id)
            if cfg is None:
                raise ValueError(
                    f"模型配置找不到了：{config_id}（不存在或已禁用），"
                    "请在设置 → 模型与 Key 里确认后再配"
                )
            self._store.set_member_model(
                role_key,
                model_config_id=config_id,
                model_provider=str(cfg.get("provider") or ""),
                model_name=str(cfg.get("model") or cfg.get("name") or ""),
                model_base_url=str(cfg.get("base_url") or ""),
            )
        else:
            self._store.set_member_model(role_key)
        return self._store.get_member(role_key)

    def get_member_model(self, role: str) -> Dict[str, Any]:
        role_key = (role or "").strip().lower()
        if role_key not in ROLE_BY_NAME:
            raise ValueError(f"没有这个队员角色：{role}")
        return self._store.get_member(role_key)

    @staticmethod
    def _resolve_model_config(model_config_id: str) -> Optional[Dict[str, Any]]:
        from .model_config import ModelConfigStore

        try:
            return ModelConfigStore().resolve(model_config_id)
        except Exception:  # noqa: BLE001 - 解析失败按“找不到”处理，不吞错
            logger.warning("member model config resolve failed: %s", model_config_id)
            return None

    def provider_for_member(self, role: str) -> Any:
        """队员执行时解析 provider：配了模型就用配的，没配就共用默认。

        配的模型解析不到（配置被删/禁用/Key 失效）时抛 ValueError，
        任务记失败、聊天如实说，不静默改用默认模型顶上。
        """
        if self._provider_factory is not None:
            return self._provider_factory(role)
        member = self._store.get_member(role)
        config_id = (member.get("model_config_id") or "").strip()
        if not config_id:
            return self._base_provider()
        cfg = self._resolve_model_config(config_id)
        if cfg is None:
            raise ValueError(
                f"队员 {role} 配的模型找不到了（配置 {config_id} 不存在或已禁用），"
                "请在设置 → 模型与 Key 里重新配"
            )
        from .providers import provider_from_config

        return provider_from_config(cfg)


_service: Optional[TeamService] = None
_service_lock = threading.Lock()


def get_team_service(
    provider: Any = None,
    provider_factory: Optional[Callable[[str], Any]] = None,
    store: Any = None,
) -> TeamService:
    global _service
    with _service_lock:
        if _service is None:
            _service = TeamService(
                provider=provider, provider_factory=provider_factory, store=store
            )
        return _service


def reset_team_service() -> None:
    """测试用：丢掉单例（调用方负责 reset_team_store / 换库）。"""
    global _service
    with _service_lock:
        _service = None
