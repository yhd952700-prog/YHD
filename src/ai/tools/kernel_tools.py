"""鎏灏内置工具集 — 把真实内核能力暴露为可被 LLM 调用的工具。

复用 ``src/ai/tool_registry.py`` 的 ``ToolRegistry``（MASTER-SPEC §40 生命周期
REGISTER→VALIDATE→APPROVE→ACTIVE）。工具调用协议采用**提示词约束 + JSON 解析**
的诚实方案：system prompt 告知 LLM 可用工具与调用格式，LLM 需要查询系统信息时
输出 ``{"tool": ..., "args": {...}}`` JSON，主控解析后执行真实工具、把结果喂回
LLM 生成最终回复。明确不伪装成原生 function calling（qwen2.5 的 tool 支持有限，
且本方案对任意 provider 通用）。
"""

from __future__ import annotations

import json
import re
from typing import Any, Callable, Dict, List, Optional, Tuple

from ...kernels.audit import audit_query
from ...kernels.memory import MemoryScope, filter_memory
from ..observability import observe
from ..personal_context import get_personal_context
from ..tool_registry import Tool
from ..tools_local import python_compute as _run_python_compute
from ..world_interface import FilesystemAdapter, WorldInterface, WorldRequest
from ..workspace import workspace_root

# 宽松解析用：小模型可能输出近似但非法的 JSON（如 ``"args: {...}"`` 缺引号），
# 用正则兜底提取工具名与参数。
_FUZZY_TOOL_RE = re.compile(r'"tool"\s*:\s*"([^"]+)"')
_FUZZY_ARGS_RE = re.compile(
    r'"(?:args|arguments)"?\s*:\s*(\{.*?\})\s*\}?\s*$', re.DOTALL
)


def _serialize(value: Any) -> str:
    """JSON 序列化工具输出（不可序列化对象降级为 str）。"""
    return json.dumps(value, ensure_ascii=False, default=str)


def load_known_principals(account: str) -> List[str]:
    """账号的已知子树主体；身份服务不可用时只返回自己（fail-closed）。"""
    try:
        from ...kernels.identity import get_identity_manager

        subtree = list(get_identity_manager().subtree_principals(account))
        return subtree or [account]
    except Exception:  # noqa: BLE001 - 无身份服务时绝不能退化成全局视图
        return [account]


def is_root_account(account: str) -> bool:
    """只有账号树的主账号才允许显式聚合子树记忆。"""
    try:
        from ...kernels.identity import get_identity_manager

        root = get_identity_manager().account_root()
        return bool(root and str(root.principal) == str(account))
    except Exception:  # noqa: BLE001
        return False


def _session_account(session_principal: str, known: Tuple[str, ...]) -> str:
    """``liuhao-{account}-{session}`` -> account（已知账号最长匹配）。"""
    raw = str(session_principal or "")
    if not raw.startswith("liuhao-"):
        return raw
    rest = raw[len("liuhao-"):]
    for candidate in sorted({str(item) for item in known if item}, key=len, reverse=True):
        if rest == candidate or rest.startswith(f"{candidate}-"):
            return candidate
    return ""


def _memory_visible_to(
    entry: Any, accounts: set, known: Tuple[str, ...]
) -> bool:
    """内核记忆的归属判定：只认明确的 principal 标记，未知条目一律不返回。"""
    key = str(getattr(entry, "key", "") or "")
    if key.startswith("profile:"):
        owner = key.split(":", 2)[1] if len(key.split(":", 2)) > 1 else ""
        if owner.startswith("user-"):
            owner = owner[len("user-"):]
        return owner in accounts
    if key.startswith("turn:"):
        rest = key[len("turn:"):]
        session_principal, _, _turn = rest.partition(":")
        return _session_account(session_principal, known) in accounts
    tags = {str(tag) for tag in (getattr(entry, "tags", ()) or ())}
    for tag in tags:
        if tag.startswith("agent:liuhao-"):
            owner = tag[len("agent:liuhao-"):]
            owner = _session_account(f"liuhao-{owner}", known) if "-" in owner else owner
            return owner in accounts
    # 没有归属标记的历史/全局条目：宁可检索不到，也不跨账号泄漏。
    return False


@observe("tools.make_tools")
def make_tools(
    principal: str,
    status_fn: Optional[Callable[[], Dict[str, Any]]] = None,
    *,
    include_disabled: bool = False,
) -> List[Tool]:
    """构建鎏灏内置工具集（绑定 principal 上下文）。

    Args:
        principal: 主体 ID（审计/记忆归因）。
        status_fn: 可选，返回系统状态 dict 的回调（绑定到 assistant.stats）。
        include_disabled: 为 True 时返回全部工具（含技能页已关的），供
            ``GET /v1/skills/chat-tools`` 列表用；聊天本身（默认 False）
            只注册开关打开的工具——这就是注册闸（技能页透明化 §9.3）。
            关掉的工具即使模型在旧提示词里见过，执行层
            （``ToolRegistry.execute``）也会再拦一次。
    """
    tools: List[Tool] = []

    def search_memory(query: str = "", scope: str = "self", **kwargs: Any) -> str:
        """按关键词搜索**本主体**在 memory kernel 中的记忆条目。

        参数做防御性别名兼容：小模型可能输出 ``key``/``text``/``keyword``
        等非规范字段名（schema 声明为 ``query``），统一归一化。

        MEM-003：内核 L1 检索默认按 principal 强制过滤；只有主账号显式传
        ``scope="subtree"`` 才聚合子树，绝不默认返回全机记忆。
        """
        q = (query or kwargs.get("key") or kwargs.get("text")
             or kwargs.get("keyword") or kwargs.get("q") or "")
        if not q:
            return "请提供 query 参数（要搜索的关键词）。"
        account = (
            principal[len("user-"):]
            if str(principal).startswith("user-")
            else str(principal)
        )
        accounts = {account}
        subtree = tuple(load_known_principals(account))
        if str(scope or "self").strip().lower() == "subtree":
            if is_root_account(account):
                accounts = set(subtree) or accounts
            # 成员传 subtree 一律按 self 处理：越权不能由模型一句话打开。
        entries = filter_memory(MemoryScope.L1)
        q = str(q).lower()
        matches = [
            e for e in entries
            if _memory_visible_to(e, accounts, subtree)
            and (
                q in str(e.value).lower()
                or any(q in t.lower() for t in e.tags)
            )
        ]
        if not matches:
            return "无匹配记忆。"
        items = [
            {"key": e.key, "value": e.value, "tags": sorted(e.tags)}
            for e in matches[:5]
        ]
        return _serialize(items)

    def query_audit(limit: int = 10, **kwargs: Any) -> str:
        """查询本主体最近的审计事件（留痕）。"""
        try:
            limit = int(kwargs.get("limit", limit))
        except (TypeError, ValueError):
            limit = 10
        events = audit_query(principal_id=principal, reverse=True, limit=limit)
        if not events:
            return "无审计记录。"
        items = [
            {
                "type": e["event_type"],
                "outcome": e["outcome"],
                "details": e.get("details"),
            }
            for e in events
        ]
        return _serialize(items)

    tools.append(
        Tool(
            tool_id="liuhao.memory.search",
            name="search_memory",
            version="1.0.0",
            description="按关键词搜索记忆中的条目（key/value/tags）",
            capability="memory.search",
            schema={
                "type": "object",
                "properties": {
                    "query": {"type": "string"},
                    "scope": {
                        "type": "string",
                        "enum": ["self", "subtree"],
                        "default": "self",
                        "description": "仅主账号可用 subtree；成员恒为 self",
                    },
                },
                "required": ["query"],
            },
            fn=search_memory,
            risk="LOW",
        )
    )
    tools.append(
        Tool(
            tool_id="liuhao.audit.query",
            name="query_audit",
            version="1.0.0",
            description="查询最近的审计事件（可审计留痕）",
            capability="audit.query",
            schema={
                "type": "object",
                "properties": {"limit": {"type": "integer", "default": 10}},
            },
            fn=query_audit,
            risk="LOW",
        )
    )

    if status_fn is not None:
        def system_status() -> str:
            """返回当前系统状态（provider/model/轮数等）。"""
            return _serialize(status_fn())

        tools.append(
            Tool(
                tool_id="liuhao.system.status",
                name="system_status",
                version="1.0.0",
                description="查询当前系统运行状态",
                capability="system.status",
                schema={"type": "object", "properties": {}},
                fn=system_status,
                risk="LOW",
            )
        )

    # ============ 真实本地计算工具（RestrictedPython 后端） ============
    # 让驾驶舱对话里的 LLM 真正做本地纯计算（不是模拟）：隔离执行，禁
    # import/open/eval，CPU 超时兜底。结果赋给变量 ``result``。
    def python_compute(code: str = "", expression: str = "", timeout: int = 10,
                       **kwargs: Any) -> str:
        """在受限沙箱中执行纯计算 Python 代码（须把答案赋给变量 result）。"""
        out = _run_python_compute(
            code=code, expression=expression, timeout=timeout, **kwargs
        )
        return _serialize(out)

    tools.append(
        Tool(
            tool_id="liuhao.python.compute",
            name="python_compute",
            version="1.0.0",
            description=(
                "在受限沙箱中执行纯计算 Python 代码（须把答案赋给变量 result）："
                "数学/统计/字符串处理等。禁 import/open/eval，有 CPU 超时。"
            ),
            capability="python_compute",
            schema={
                "type": "object",
                "properties": {
                    "code": {"type": "string",
                             "description": "Python 代码，须把答案赋给变量 result"},
                    "expression": {"type": "string",
                                   "description": "或一个纯表达式（会被包成 result = ...）"},
                },
            },
            fn=python_compute,
            risk="LOW",
            sandbox_policy="restricted_python",
        )
    )

    # ============ KAREN 个人画像工具（让 LLM 在对话中主动记住用户偏好） ============
    pcm = get_personal_context()

    def personal_set_preference(key: str = "", value: str = "", confidence: float = 0.8, **kwargs: Any) -> str:
        """记录用户偏好（如「回复用简体中文」「喜欢简短回答」）。"""
        k = key or kwargs.get("name") or ""
        v = value or kwargs.get("v") or ""
        if not k or not v:
            return "请提供 key 与 value 参数。"
        try:
            conf = float(confidence)
        except (TypeError, ValueError):
            conf = 0.8
        p = pcm.set_preference(principal, k, v, confidence=conf)
        return _serialize({"status": "ok", "key": p.key, "value": p.value, "confidence": p.confidence, "kernel_synced": p.kernel_synced})

    def personal_add_fact(key: str = "", value: str = "", **kwargs: Any) -> str:
        """提议一条用户背景事实，进入待确认区（不直接写长期记忆）。

        MEM-009：模型不得自己给自己写长期记忆（记忆投毒）。只有用户在记忆页
        确认后，才调用 ``record_fact`` 落为长期事实。
        """
        k = key or kwargs.get("name") or ""
        v = value or kwargs.get("v") or ""
        if not k or not v:
            return "请提供 key 与 value 参数。"
        pending = pcm.propose_fact(principal, k, v, source="assistant")
        return _serialize(
            {
                "status": "pending_confirmation",
                "id": pending["id"],
                "key": k,
                "value": v,
                "message": "已提交待确认，尚未写入长期记忆；请用户在记忆页确认。",
            }
        )

    def personal_set_display_name(display_name: str = "", **kwargs: Any) -> str:
        """设置用户称呼（之后回复会按此称呼）。"""
        n = display_name or kwargs.get("name") or ""
        if not n:
            return "请提供 display_name 参数。"
        pcm.set_display_name(principal, n)
        return _serialize({"status": "ok", "display_name": n})

    tools.append(
        Tool(
            tool_id="liuhao.personal.set_preference",
            name="personal_set_preference",
            version="1.0.0",
            description="记录用户偏好（key/value/confidence）",
            capability="personal.write",
            schema={
                "type": "object",
                "properties": {
                    "key": {"type": "string"},
                    "value": {"type": "string"},
                    "confidence": {"type": "number", "default": 0.8},
                },
                "required": ["key", "value"],
            },
            fn=personal_set_preference,
            risk="LOW",
        )
    )
    tools.append(
        Tool(
            tool_id="liuhao.personal.add_fact",
            name="personal_add_fact",
            version="1.0.0",
            description="记录用户背景事实（key/value）",
            capability="personal.write",
            schema={
                "type": "object",
                "properties": {
                    "key": {"type": "string"},
                    "value": {"type": "string"},
                },
                "required": ["key", "value"],
            },
            fn=personal_add_fact,
            risk="LOW",
        )
    )
    tools.append(
        Tool(
            tool_id="liuhao.personal.set_display_name",
            name="personal_set_display_name",
            version="1.0.0",
            description="设置用户称呼",
            capability="personal.write",
            schema={
                "type": "object",
                "properties": {"display_name": {"type": "string"}},
                "required": ["display_name"],
            },
            fn=personal_set_display_name,
            risk="LOW",
        )
    )

    # ----- 文件访问（受工作区闸门约束；**只读**） -------------------------
    # 根在**构建时解析一次**并固定：避免多轮对话过程中环境变量被改动，出现
    # "这一轮能读、下一轮读不了"这种无法复现的行为。适配器必须显式带 root ——
    # 不传即不设限（见 FilesystemAdapter docstring），那等于把闸门留空。
    #
    # 刻意**不提供写入工具**：写入要经过审批链路（policy/approvals）才有意义，
    # 在那条路打通之前先给写权限，等于让模型在无人过问的情况下改动磁盘。
    fs_root = workspace_root()

    # U1/U5 收口：LLM 可调用文件工具是**自主(autonomous)面**，必须显式
    # actor="autonomous" 让其默认拒绝；同时只放行 read / list 这类只读观察，
    # write / exec / shell 一律 fail-closed 拒绝（此处压根不注册任何写工具，
    # 且工作区越界已由 FilesystemAdapter(root=fs_root) 在 resolve_in_workspace
    # 处拦截）。没有显式 authorize_fn 的 autonomous 接口会默认全拒，会让误读
    # 工具直接挂掉，所以这里必须给一个最小只读授权策略。
    def _tool_fs_authorize(request: WorldRequest) -> bool:
        # 工具层是只读观察面：只允许 read / list；任何写或执行都拒绝。
        return request.action in ("read", "list")

    fs_world = WorldInterface(
        adapters=[FilesystemAdapter(root=fs_root)],
        authorize=_tool_fs_authorize,
        actor="autonomous",
    )

    # 单次返回上限：工具输出会整段进上下文，不设限会让一次读文件吃掉整个窗口。
    read_limit = 20000

    def read_file(path: str = "", **kwargs: Any) -> str:
        """读取工作区内的一个文本文件（越界路径被拒）。"""
        target = path or kwargs.get("file") or kwargs.get("filename") or ""
        if not str(target).strip():
            return "请提供 path 参数（工作区内的相对路径）。"
        result = fs_world.observe(
            WorldRequest(adapter="filesystem", action="read",
                         params={"path": str(target)})
        )
        if result.get("status") != "observed":
            return f"读取失败：{result.get('error') or result.get('status')}"
        text = str(result.get("data", ""))
        if not text:
            return f"（文件 {target} 为空，0 字符）"
        if len(text) > read_limit:
            return f"{text[:read_limit]}\n…（已截断，原文 {len(text)} 字符）"
        return text

    def list_files(path: str = ".", **kwargs: Any) -> str:
        """列出工作区内某个目录的条目（越界路径被拒）。"""
        target = path or kwargs.get("dir") or kwargs.get("directory") or "."
        result = fs_world.observe(
            WorldRequest(adapter="filesystem", action="list",
                         params={"path": str(target)})
        )
        if result.get("status") != "observed":
            return f"列目录失败：{result.get('error') or result.get('status')}"
        entries = sorted(str(name) for name in result.get("data", []))[:200]
        return _serialize({
            "workspace": fs_root,
            "path": str(target),
            "entries": entries,
        })

    tools.append(
        Tool(
            tool_id="liuhao.file.read",
            name="read_file",
            version="1.0.0",
            description=(
                "读取工作区内的文本文件。path 用工作区内的相对路径，"
                f"工作区根为 {fs_root}；越界路径会被拒绝。"
            ),
            capability="file.read",
            schema={
                "type": "object",
                "properties": {"path": {"type": "string"}},
                "required": ["path"],
            },
            fn=read_file,
            risk="LOW",
            permission="workspace.read",
        )
    )
    tools.append(
        Tool(
            tool_id="liuhao.file.list",
            name="list_files",
            version="1.0.0",
            description="列出工作区内某个目录的条目（path 用相对路径，默认工作区根）。",
            capability="file.list",
            schema={
                "type": "object",
                "properties": {"path": {"type": "string", "default": "."}},
            },
            fn=list_files,
            risk="LOW",
            permission="workspace.read",
        )
    )

    # ============ 自动化（默认三重关闭：闸门不满足时不注册任何工具） ============
    from ...automation.tools import build_automation_tools

    tools.extend(build_automation_tools(principal))

    # ============ 只读观察（门槛与动作工具不同，这是有意设计） ============
    # 动作工具要求三重闸全部打开（装配 / fake 后端 / 人类主体）；只读观察
    # **不要求**这三道全开，只需要两道：观察开关（环境变量缺省开，管理员可
    # 硬关）+ 人类主体。理由：
    #   1. 观察没有副作用，不注入键鼠、不截图、不写磁盘，装配闸与后端闸
    #      是为"动手"设的，不该连"看"一起卡住；
    #   2. 窗口标题/可执行路径仍属敏感信息，所以主体闸保留，非人类主体
    #      （service / agent / 未知）一律不注册。
    # 不要在这里补装配闸/后端闸：那不是漏了一道，而是两类能力的门槛本来就
    # 不同。闸门不放行时 build_observation_tools 返回空列表，与动作工具一致。
    from ...automation.observe import build_observation_tools

    tools.extend(build_observation_tools(principal))

    # ============ 定时提醒（复用进程内排程器，不另起调度） ============
    from ...tasks.reminder_tool import build_reminder_tool

    tools.append(build_reminder_tool(principal))

    # ============ Bot/分组管理 + 数据备份（完善第二轮 D 精而简审计补通路） ====
    # 审计发现：Boss 面板里「建 Bot」此前只能点界面——聊天工具集里没有
    # 任何 Bot/分组工具，模型在聊天里建不了 Bot；「备份」同样没有工具。
    # 按「一个聊天框办一切」补这两条通路：复用 workbench store 与备份
    # 服务的既有实现，不另起一套。分档 B（写数据，默认关，Owner 在
    # 技能页开启后进聊天工具集），与 reminder.manage / team.delegate
    # 同一口径。归属按当前主体自己的账号，绝不跨账号操作。
    def _account() -> str:
        return (
            principal[len("user-"):]
            if str(principal).startswith("user-")
            else str(principal)
        )

    def manage_bots(
        action: str = "",
        name: str = "",
        job: str = "",
        group_name: str = "",
        bot_id: str = "",
        **kwargs: Any,
    ) -> str:
        """Bot 与分组管理：list / create_bot / create_group / move_bot。"""
        from src.workbench.store import get_workbench_store

        account = _account()
        store = get_workbench_store()
        try:
            if action == "list":
                return _serialize({
                    "bots": store.list_bots([account]),
                    "groups": store.list_groups([account]),
                })
            if action == "create_group":
                if not group_name:
                    return _serialize({"status": "failed", "error": "create_group 需要 group_name"})
                return _serialize({"status": "ok", "group": store.create_group(account, group_name)})
            if action == "create_bot":
                if not name:
                    return _serialize({"status": "failed", "error": "create_bot 需要 name"})
                fields: Dict[str, Any] = {"name": name, "job": job}
                if group_name:
                    groups = store.list_groups([account])
                    match = next((g for g in groups if g.get("name") == group_name), None)
                    if match is None:
                        match = store.create_group(account, group_name)
                    fields["group_id"] = match["id"]
                return _serialize({"status": "ok", "bot": store.create_bot(account, fields)})
            if action == "move_bot":
                if not bot_id or not group_name:
                    return _serialize({"status": "failed", "error": "move_bot 需要 bot_id 与 group_name"})
                groups = store.list_groups([account])
                match = next((g for g in groups if g.get("name") == group_name), None)
                if match is None:
                    match = store.create_group(account, group_name)
                updated = store.update_bot([account], bot_id, {"group_id": match["id"]})
                return _serialize({"status": "ok" if updated else "failed", "bot": updated})
            return _serialize({
                "status": "failed",
                "error": f"未知 action: {action}（可用 list/create_bot/create_group/move_bot）",
            })
        except Exception as exc:  # noqa: BLE001 - 失败如实回给模型，不编造成功
            return _serialize({"status": "failed", "error": str(exc)})

    tools.append(
        Tool(
            tool_id="liuhao.workbench.bots",
            name="manage_bots",
            version="1.0.0",
            description=(
                "管理左侧 Bot 与分组：查看（list）、新建 Bot（create_bot，可同时指定分组，"
                "没有就自动建）、新建分组（create_group）、把 Bot 挪到分组（move_bot）。"
                "用户在聊天里说「建一个管账的 Bot」「把 X 挪到学习分组」时用这个工具，"
                "不要只回复文字说建好了。"
            ),
            capability="workbench.bots",
            schema={
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": ["list", "create_bot", "create_group", "move_bot"],
                    },
                    "name": {"type": "string", "description": "Bot 名字（create_bot 用）"},
                    "job": {"type": "string", "description": "Bot 的职责（可选）"},
                    "group_name": {"type": "string", "description": "分组名"},
                    "bot_id": {"type": "string", "description": "Bot id（move_bot 用，先 list 查）"},
                },
                "required": ["action"],
            },
            fn=manage_bots,
            risk="MEDIUM",
        )
    )

    def backup_now(**kwargs: Any) -> str:
        """立即跑一次产品自带备份（与每日自动备份同一条路径）。"""
        try:
            from src.backup.auto_backup import run_daily_auto_backup
            from src.common.engines import data_dir

            meta = run_daily_auto_backup(data_dir())
            return _serialize({
                "status": "ok",
                "backup_id": meta.backup_id,
                "note": "备份已完成（加密归档，保留策略与每日自动备份一致）。",
            })
        except Exception as exc:  # noqa: BLE001 - 备份失败必须如实回，不许假报完成
            return _serialize({"status": "failed", "error": str(exc)})

    tools.append(
        Tool(
            tool_id="liuhao.backup.now",
            name="backup_now",
            version="1.0.0",
            description=(
                "立即备份当前全部数据（与每天凌晨自动备份同一机制）。"
                "用户说「备份一下现在的数据」时用这个工具，跑完把 backup_id 报给用户。"
            ),
            capability="backup.now",
            schema={"type": "object", "properties": {}},
            fn=backup_now,
            risk="MEDIUM",
        )
    )

    # ============ 云备份快照（大升级第二季 V10） ============
    # 与 backup_now 的分工：backup_now 是本机机器绑定归档（带不走）；
    # cloud_backup 是口令加密快照——数据面与加密信封复用账号凭据包，
    # 换机/灾难恢复靠它。口令只在本次调用的内存里用，工具不保存、不回显；
    # 恢复与删除是不可逆动作，沿用 apply_update 的口径：用户亲口确认后
    # 带 confirm=true 才执行。云端上传默认关，status 里如实说明。
    def cloud_backup(
        action: str = "",
        passphrase: str = "",
        snapshot_id: str = "",
        label: str = "",
        scope: str = "",
        retention: int = 0,
        confirm: bool = False,
        **kwargs: Any,
    ) -> str:
        """云备份快照：status / list / create / restore / set_retention / delete。"""
        from src.gateway import cloud_backup as _cb
        from src.kernels.identity import _accounts as _acct
        from src.kernels.identity import get_identity_manager

        account = _account()
        try:
            verb = str(action or "").strip().lower()
            if verb in ("", "status"):
                status = _cb.cloud_backup_status()
                status["snapshots"] = [
                    _cb._public_meta(meta) for meta in _cb.list_snapshots()
                ]
                return _serialize({"status": "ok", **status})
            if verb == "list":
                return _serialize({
                    "status": "ok",
                    "snapshots": [
                        _cb._public_meta(meta) for meta in _cb.list_snapshots()
                    ],
                    "retention": _cb.effective_retention(),
                })
            if verb == "create":
                if not str(passphrase or ""):
                    return _serialize({
                        "status": "needs_passphrase",
                        "error": (
                            "做云备份快照需要一个备份口令（至少 8 个字符）："
                            "请用户亲口给出，口令只用来加密这份快照，"
                            "不会保存、不会写进任何记录；丢了口令快照打不开。"
                        ),
                    })
                manager = get_identity_manager()
                node = manager.get_account(account)
                is_root = bool(node and node.is_root)
                wanted_scope = str(scope or "").strip().lower() or (
                    "all" if is_root else "self"
                )
                result = _cb.create_snapshot(
                    actor=account,
                    scope=wanted_scope,
                    passphrase=str(passphrase),
                    is_root=is_root,
                    label=str(label or ""),
                )
                snap = result["snapshot"]
                return _serialize({
                    "status": "ok",
                    "snapshot_id": snap["snapshot_id"],
                    "created_at": snap["created_at"],
                    "members": snap["members"],
                    "files": snap["files"],
                    "pruned": result.get("pruned") or [],
                    "note": (
                        "加密快照已存到本机（口令加密，没口令打不开）；"
                        "云端上传默认关，想防丢可把快照文件拷进云盘。"
                        "备份里不带模型 Key，换机恢复后要重填一次。"
                    ),
                })
            if verb == "restore":
                if confirm is not True:
                    return _serialize({
                        "status": "refused",
                        "error": (
                            "恢复快照会把数据合回本机（已有数据不覆盖、不删除），"
                            "请先向用户说明要恢复哪一份快照，用户亲口确认后"
                            "再带 confirm=true 调用。"
                        ),
                    })
                if not str(passphrase or ""):
                    return _serialize({
                        "status": "needs_passphrase",
                        "error": "恢复需要这份快照的备份口令，请用户亲口给出；口令不会保存。",
                    })
                target = str(snapshot_id or "").strip()
                if target in ("", "latest", "最新"):
                    metas = _cb.list_snapshots()
                    if not metas:
                        return _serialize({
                            "status": "failed", "error": "本机还没有任何云备份快照。"
                        })
                    target = str(metas[0]["snapshot_id"])
                result = _cb.restore_snapshot(
                    snapshot_id=target,
                    passphrase=str(passphrase),
                    actor=account,
                )
                return _serialize({
                    "status": "ok",
                    "snapshot_id": target,
                    "restore_mode": result.get("restore_mode"),
                    "notice": result.get("notice") or "恢复完成。",
                })
            if verb == "set_retention":
                value = _cb.set_retention(int(retention or 0))
                return _serialize({
                    "status": "ok",
                    "retention": value,
                    "note": f"云备份快照保留份数已改为 {value} 份（下次做快照时按新数修剪）。",
                })
            if verb == "delete":
                if confirm is not True:
                    return _serialize({
                        "status": "refused",
                        "error": (
                            "删快照不可恢复：请先向用户说明要删哪一份，"
                            "用户亲口确认后再带 confirm=true 调用。"
                        ),
                    })
                result = _cb.delete_snapshot(
                    actor=account, snapshot_id=str(snapshot_id or "")
                )
                return _serialize({"status": "ok", **result})
            return _serialize({
                "status": "failed",
                "error": f"未知动作 {verb}；可用：status / list / create / restore / set_retention / delete。",
            })
        except _acct.AccountError as exc:
            return _serialize({"status": "failed", "error": str(exc)})
        except Exception as exc:  # noqa: BLE001 - 失败如实回，不许假报完成
            return _serialize({
                "status": "failed",
                "error": f"云备份操作失败：{type(exc).__name__}: {exc}",
            })

    tools.append(
        Tool(
            tool_id="liuhao.backup.cloud",
            name="cloud_backup",
            version="1.0.0",
            description=(
                "云备份快照（口令加密、可带走）：status 看现状与清单，create 做一份新快照"
                "（需要用户亲口给备份口令），restore 从快照恢复（须 confirm=true 且要口令），"
                "set_retention 改保留份数，delete 删一份（须 confirm=true）。"
                "用户说「做个云备份」「把数据加密备份一份以后换电脑用」「恢复上次的备份」"
                "时用这个工具；本机同机回滚用 backup_now，不要混。"
            ),
            capability="backup.cloud",
            schema={
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": [
                            "status", "list", "create",
                            "restore", "set_retention", "delete",
                        ],
                    },
                    "passphrase": {
                        "type": "string",
                        "description": "备份口令（create/restore 用，用户亲口给；不保存不回显）",
                    },
                    "snapshot_id": {
                        "type": "string",
                        "description": "快照编号（restore/delete 用；restore 可填 latest）",
                    },
                    "label": {"type": "string", "description": "快照备注（create 用，可选）"},
                    "scope": {
                        "type": "string",
                        "enum": ["all", "self", ""],
                        "description": "导出范围（create 用；缺省主账号 all、成员 self）",
                    },
                    "retention": {
                        "type": "integer",
                        "description": "保留份数（set_retention 用，1–100）",
                    },
                    "confirm": {
                        "type": "boolean",
                        "description": "restore/delete 必须为 true（用户亲口确认后）",
                    },
                },
                "required": ["action"],
            },
            fn=cloud_backup,
            risk="HIGH",
        )
    )

    # ============ 团队编排（第二季 V8：多 Bot 协作） ============
    # 主 Bot 把一句话目标分解成子任务、按岗位派给群聊成员 Bot 执行、汇总
    # 回主线。成员执行走 gateway.chat 的同一条内部路径（去重/用量上限/
    # 知识注入/记账全继承，不另起炉灶）；分解与选择规则在
    # src/ai/orchestration.py。两步口径：不带 confirm 只出计划预览
    # （拆几件、派给谁），用户确认后带 confirm=true 才真跑——编排一跑
    # 就是多路模型调用，先让用户看见规模（设计 R1）。
    def team_orchestrate(
        goal: str = "",
        group_chat_id: str = "",
        confirm: bool = False,
        **kwargs: Any,
    ) -> str:
        """团队编排：preview（默认）/ run（confirm=true）。"""
        try:
            from src.ai.goal_task_graph import GoalDefinition, GoalTaskGraph
            from src.ai.orchestration import MemberCapReached, TeamOrchestrator
            from src.workbench.store import get_workbench_store

            account = _account()
            store = get_workbench_store()
            goal_text = str(goal or "").strip()
            if not goal_text:
                return _serialize({"status": "failed", "error": "请先说清要让团队一起做的目标。"})
            chats = store.list_group_chats([account])
            chat_row = None
            if group_chat_id:
                chat_row = next((c for c in chats if c.get("id") == group_chat_id), None)
                if chat_row is None:
                    return _serialize({"status": "failed", "error": "找不到这个群聊，先用 manage_bots 看一下团队。"})
            elif len(chats) == 1:
                chat_row = chats[0]
            elif not chats:
                return _serialize({
                    "status": "failed",
                    "error": "还没有团队群聊。先建一个群、把要协作的 Bot 拉进来，再让我派活。",
                })
            else:
                return _serialize({
                    "status": "needs_group",
                    "error": "有多个团队群聊，请指明在哪一个群里派活（group_chat_id）。",
                    "group_chats": [{"id": c.get("id"), "name": c.get("name")} for c in chats],
                })
            members = []
            for bot_id in chat_row.get("member_bot_ids") or []:
                bot = store.get_bot([account], bot_id)
                if bot is not None:
                    members.append(bot)
            if not members:
                return _serialize({
                    "status": "failed",
                    "error": "这个群里还没有成员 Bot。先把要协作的 Bot 拉进群，再让我派活。",
                })

            graph = GoalTaskGraph(goal=GoalDefinition(id="", description=goal_text))
            tasks = graph.decompose_goal()
            if not tasks:
                return _serialize({"status": "failed", "error": "这个目标没能拆成子任务，换个说法再试。"})

            def _runner(bot_id: str, brief: str, *, idempotency_key: str) -> str:
                from fastapi import HTTPException

                from src.gateway.chat import ChatRequest
                from src.gateway.chat import chat as _chat_handler

                try:
                    resp = _chat_handler(
                        ChatRequest(session_id=f"workbench-{bot_id}", message=brief),
                        principal=principal,
                        idempotency_key=idempotency_key,
                    )
                except HTTPException as exc:
                    detail = exc.detail if isinstance(exc.detail, dict) else {}
                    if detail.get("code") == "usage_cap_reached":
                        raise MemberCapReached(
                            str(detail.get("message") or "已达用量上限")
                        ) from exc
                    raise RuntimeError(
                        str(detail.get("message") or f"成员调用失败（HTTP {exc.status_code}）")
                    ) from exc
                if resp.status != "completed":
                    raise RuntimeError(resp.error or resp.reason or "成员没有完成这轮任务")
                return resp.reply

            def _cap_status(bot_id: str) -> Dict[str, Any]:
                from src.gateway.a2a import bot_cap_status

                return bot_cap_status(bot_id)

            def _notify(kind: str, result: Dict[str, Any]) -> None:
                from src.gateway.a2a import report_done, report_stuck

                title = f"团队编排：{goal_text[:40]}"
                if kind == "done":
                    report_done(title, "聊天 · 团队编排", owner=account)
                else:
                    failed_items = [i for i in result.get("items", []) if i["status"] != "completed"]
                    missing = "；".join(
                        f"{i.get('bot_name') or i['task_id']}：{i.get('error') or i['status']}"
                        for i in failed_items
                    )
                    report_stuck(title, missing or "部分任务未完成", owner=account)

            from src.common.engines import data_dir

            state_path = str(
                data_dir() / "orchestration" / f"{graph.goal.id}.json"
            )
            orch = TeamOrchestrator(
                graph,
                members,
                _runner,
                cap_status=_cap_status,
                notify=_notify,
                state_path=state_path,
            )
            if confirm is not True:
                plan = orch.plan(graph.goal.id)
                return _serialize({
                    "status": "plan",
                    "note": (
                        f"计划预览（还没开跑）：共 {plan['task_count']} 件子任务，"
                        f"派给 {plan['member_count']} 位成员。确认要跑，"
                        "再带 confirm=true 调用一次。"
                    ),
                    **plan,
                })
            result = orch.run(graph.goal.id)
            return _serialize({
                "status": result["status"],
                "synthesis": result.get("synthesis"),
                "synthesis_error": result.get("synthesis_error"),
                "completed": result["completed"],
                "failed": result["failed"],
                "skipped": result["skipped"],
                "items": [
                    {
                        "task_id": i["task_id"],
                        "bot_name": i.get("bot_name"),
                        "status": i["status"],
                        "error": i.get("error"),
                    }
                    for i in result["items"]
                ],
                "usage_note": result.get("usage_note"),
                "note": (
                    "把 synthesis 原样答复用户；status 为 partial/failed 时"
                    "先如实说明哪几项没成、为什么，不许说成全部完成。"
                ),
            })
        except Exception as exc:  # noqa: BLE001 - 失败如实回，不许假报
            return _serialize({
                "status": "failed",
                "error": f"团队编排失败：{type(exc).__name__}: {exc}",
            })

    tools.append(
        Tool(
            tool_id="liuhao.team.orchestrate",
            name="team_orchestrate",
            version="1.0.0",
            description=(
                "团队编排：把一个目标分解成子任务、按岗位派给某个团队群聊里的成员 Bot "
                "并行/串行执行、收齐后汇总。不带 confirm 只返回计划预览（拆几件、派给谁）；"
                "用户确认后带 confirm=true 才真正执行。用户说「让团队一起做…」「派活给"
                "群里的 Bot」时用这个工具。成员失败会如实报部分完成，不许假装全成。"
            ),
            capability="team.orchestrate",
            schema={
                "type": "object",
                "properties": {
                    "goal": {"type": "string", "description": "要让团队一起完成的目标（一句话）"},
                    "group_chat_id": {
                        "type": "string",
                        "description": "团队群聊 id（只有一个群时可省略；多个群时必填）",
                    },
                    "confirm": {
                        "type": "boolean",
                        "description": "true 才真正执行；缺省只出计划预览",
                    },
                },
                "required": ["goal"],
            },
            fn=team_orchestrate,
            risk="HIGH",
        )
    )

    # ============ 用量/花费查询（大升级 U1：多模型容灾的记账面） ============
    # 用量账本（gateway/account_usage）此前只有设置页与接口能看，聊天里
    # 问「这个月用了多少」无工具可答。按精而简补这条只读通路：口径与
    # 用量页完全同一套（usage_events 的聚合+定价），只统计当前账号本人
    # 发起的调用，未定价模型如实标「暂无价格」绝不按 0 元报。
    def usage_report(month: str = "", **kwargs: Any) -> str:
        """按月汇总当前账号的模型用量与估算花费（按模型拆分）。"""
        try:
            from datetime import datetime, timezone

            from src.gateway.account_usage import usage_events

            account = _account()
            raw = str(month or "").strip()
            if raw in ("", "本月", "这个月", "current"):
                now = datetime.now(timezone.utc)
                year, mon = now.year, now.month
                label = f"{year}-{mon:02d}（本月）"
            else:
                parts = raw.replace("/", "-").split("-")
                year, mon = int(parts[0]), int(parts[1])
                label = f"{year}-{mon:02d}"
            since = f"{year}-{mon:02d}-01T00:00:00+00:00"
            if mon == 12:
                until = f"{year + 1}-01-01T00:00:00+00:00"
            else:
                until = f"{year}-{mon + 1:02d}-01T00:00:00+00:00"
            result = usage_events(
                account,
                owners=[account],
                since=since,
                until=until,
                limit=1,
            )
            by_model = []
            for item in result.get("by_model") or []:
                by_model.append({
                    "model": item.get("model") or "unknown",
                    "tokens_in": item.get("tokens_in"),
                    "tokens_out": item.get("tokens_out"),
                    "total_tokens": item.get("total_tokens"),
                    "cost": item.get("cost"),
                })
            # 第二季 V9：按路由分级拆账 + 省档节省估算（同一批明细行聚合）。
            # 节省 = 省档行按「默认模型价 − 实际应答模型价」× token 估算；
            # 任一省档行未定价 → savings_complete=False 只报已计部分，
            # 绝不给一个干脆的总数（设计 R2）。
            routing_block = None
            try:
                from src.ai.model_router import light_mapping_status

                events = []
                offset = 0
                truncated = False
                for _page in range(5):
                    page = usage_events(
                        account, owners=[account],
                        since=since, until=until,
                        limit=200, offset=offset,
                    )
                    events.extend(page.get("events") or [])
                    if not page.get("has_more"):
                        break
                    offset += 200
                else:
                    truncated = True
                light_events = [e for e in events if e.get("route_grade") == "light"]
                if light_events:
                    status = light_mapping_status()
                    default_price = status.get("default_price")
                    saved = 0.0
                    savings_complete = default_price is not None
                    light_cost = 0.0
                    for e in light_events:
                        if e.get("cost") is None:
                            savings_complete = False
                            continue
                        light_cost += float(e["cost"])
                        model_price = None
                        if default_price is not None:
                            from src.gateway.account_usage import _price_table

                            model_price = _price_table([e.get("model") or ""]).get(
                                e.get("model") or ""
                            )
                        if model_price is None:
                            savings_complete = False
                            continue
                        saved += (float(default_price) - float(model_price)) * (
                            int(e.get("total_tokens") or 0) / 1000.0
                        )
                    routing_block = {
                        "light_calls": len(light_events),
                        "hard_calls": sum(1 for e in events if e.get("route_grade") == "hard"),
                        "light_cost_estimate": round(light_cost, 6),
                        "saved_estimate": round(saved, 6) if savings_complete else None,
                        "savings_complete": savings_complete,
                        "events_truncated": truncated,
                        "note": (
                            "节省为按价格表的估算（与账本同一价格口径）；"
                            "有未定价模型参与时只报已计部分，不给总数。"
                        ),
                    }
            except Exception:  # noqa: BLE001 - 分级拆账失败不拖垮用量主报表
                routing_block = None
            return _serialize({
                "status": "ok",
                "month": label,
                "calls": result.get("total", 0),
                "estimated_cost": result.get("cost"),
                "cost_complete": result.get("cost_complete", True),
                "unknown_models": result.get("unknown_models") or [],
                "by_model": by_model,
                "cost_routing": routing_block,
                "note": (
                    "费用为按价格表估算；unknown_models 里的模型暂无价格、"
                    "未计入费用（不是 0 元）。"
                ),
            })
        except Exception as exc:  # noqa: BLE001 - 查询失败如实回
            return _serialize({"status": "failed", "error": str(exc)})

    # ============ 版本更新（大升级 U2：自动检查 + 手动确认应用） ============
    # 更新引擎（src.upgrade）此前只有 CLI 与 /v1/upgrade 接口，聊天里问
    # 「有新版本吗」无工具可答。补两条通路：check_update 只读（A 档默认
    # 开）；apply_update 是写操作（B 档默认关），且必须带 confirm=true——
    # 模型只有在用户明确说了「确认升级」之后才许传 true，工具内二次校验。
    # 应用走引擎自带流程：下载→哈希校验→升级前备份→替换程序（不动数据
    # 目录）→自检，失败引擎自动回滚，本工具只如实转述结果。
    def check_update(**kwargs: Any) -> str:
        """检查是否有新版本（只检查，绝不自动安装）。"""
        manager = None
        try:
            from src.upgrade.update_check import build_manager_from_env

            manager = build_manager_from_env()
            result = manager.check(trigger="chat")
            return _serialize({
                "status": "ok",
                "current_version": result.current_version,
                "latest_version": result.latest_version,
                "update_available": bool(result.update_available),
                "message": result.message,
                "note": "只检查不安装；用户确认后用 apply_update 应用。",
            })
        except Exception as exc:  # noqa: BLE001 - 检查失败如实回，不许假报已是最新
            return _serialize({
                "status": "failed",
                "error": f"无法检查更新：{type(exc).__name__}: {exc}",
            })
        finally:
            if manager is not None:
                manager.close()

    def apply_update(confirm: bool = False, **kwargs: Any) -> str:
        """应用已检查到的新版本（须用户明确确认）。"""
        if confirm is not True:
            return _serialize({
                "status": "refused",
                "error": (
                    "应用更新必须由用户明确确认：请先向用户说明新版本号与"
                    "影响，用户亲口说「确认升级」后再带 confirm=true 调用。"
                ),
            })
        manager = None
        try:
            from src.upgrade.update_check import build_manager_from_env

            manager = build_manager_from_env()
            check = manager.check(trigger="chat-apply")
            if not check.update_available or check.release is None:
                return _serialize({
                    "status": "ok",
                    "update_available": False,
                    "current_version": check.current_version,
                    "message": check.message,
                })
            download = manager.download(check.release)
            applied = manager.apply(download.path, check.release)
            return _serialize({
                "status": "ok",
                "from_version": applied.from_version,
                "to_version": applied.to_version,
                "backup_id": applied.backup_id,
                "self_check_passed": applied.self_check_passed,
                "message": applied.message,
            })
        except Exception as exc:  # noqa: BLE001 - 失败如实回（引擎侧已自动回滚）
            return _serialize({
                "status": "failed",
                "error": f"升级失败：{type(exc).__name__}: {exc}",
                "note": "升级引擎在自检失败时会自动回滚到升级前备份。",
            })
        finally:
            if manager is not None:
                manager.close()

    # ============ 语音状态（大升级 U3：全链路的自查面） ============
    def voice_status(**kwargs: Any) -> str:
        """语音全链路（识别/合成/唤醒）现在能不能用、用的哪个引擎。"""
        try:
            from src.voice.providers import voice_pipeline_status

            status = voice_pipeline_status()
            return _serialize({
                "status": "ok",
                "ready": status["ready"],
                "providers": status["providers"],
                "stt": {
                    "available": status["stt"].get("available"),
                    "engine": status["stt"].get("engine"),
                    "model": status["stt"].get("model"),
                    "install_hint": status["stt"].get("install_hint") or "",
                },
                "tts": {
                    "available": status["tts"].get("available"),
                    "engine": status["tts"].get("engine"),
                    "voice": status["tts"].get("voice"),
                    "install_hint": status["tts"].get("install_hint") or "",
                },
                "wake": {
                    "available": status["wake"].get("available"),
                    "mic_available": status["wake"].get("mic_available"),
                },
                "session": {
                    "state": (status.get("session") or {}).get("state"),
                    "running": (status.get("session") or {}).get("running"),
                    "turns_done": (status.get("session") or {}).get("turns_done"),
                    "last_prompt": (status.get("session") or {}).get("last_prompt"),
                    "last_turn": (status.get("session") or {}).get("last_turn"),
                },
                "note": "引擎没装时把 install_hint 原样告诉用户，不要假装能语音。",
            })
        except Exception as exc:  # noqa: BLE001
            return _serialize({"status": "failed", "error": str(exc)})

    # ============ 安卓 App 状态（大升级第二季 V3：TWA 套件自查面） ============
    def android_app_status(**kwargs: Any) -> str:
        """安卓 App（TWA）到哪一步了：PWA 底子、打包套件、还差什么。"""
        try:
            from src.twa.status import twa_status

            status = twa_status()
            return _serialize({
                "status": "ok",
                "pwa_ready": status["pwa"]["ready"],
                "pwa_missing": status["pwa"]["missing"],
                "kit_present": status["kit"]["present"],
                "package_id": status["kit"]["package_id"],
                "host": status["kit"]["host"],
                "host_configured": status["kit"]["host_configured"],
                "assetlinks_filled": status["kit"]["assetlinks_filled"],
                "keystore_present": status["kit"]["keystore_present"],
                "ready_to_build": status["ready_to_build"],
                "next_steps": status["next_steps"],
                "guide": status["guide"],
                "note": (
                    "把 next_steps 原样翻成人话告诉用户；真机出包/安装还没验过"
                    "就直说真机待验，不要假装手机已经能装。"
                ),
            })
        except Exception as exc:  # noqa: BLE001
            return _serialize({"status": "failed", "error": str(exc)})

    # ============ 主动管家（大升级 U4：晨间简报 + 例程模板） ============
    def morning_briefing(**kwargs: Any) -> str:
        """现在就生成一份晨间简报（昨日结果 + 今日安排 + 待审批）。"""
        try:
            from src.tasks.briefing import build_morning_briefing

            text = build_morning_briefing(_account())
            return _serialize({"status": "ok", "briefing": text})
        except Exception as exc:  # noqa: BLE001
            return _serialize({"status": "failed", "error": str(exc)})

    def briefing_broadcast(action: str = "start", **kwargs: Any) -> str:
        """简报语音播报：start 念今天的简报；stop 停；status 看进度（V2(b)）。"""
        try:
            from src.tasks.briefing_speech import get_briefing_speech_service

            service = get_briefing_speech_service()
            verb = str(action or "start").strip().lower()
            if verb == "start":
                result = service.start(_account())
                if result.get("status") == "failed":
                    return _serialize(result)
                return _serialize({
                    **result,
                    "note": (
                        "播报已开始合成，控制台会自动出声念简报；"
                        "合成要一点时间。用户说「停 / 别念了」时用 stop。"
                    ),
                })
            if verb == "stop":
                return _serialize({**service.stop(_account()), "note": "已停播。"})
            if verb == "status":
                return _serialize(service.status(_account()))
            return _serialize({
                "status": "failed",
                "error": f"未知动作：{action}（只支持 start / stop / status）",
            })
        except Exception as exc:  # noqa: BLE001
            return _serialize({"status": "failed", "error": str(exc)})

    def push_notifications(action: str = "status", **kwargs: Any) -> str:
        """手机推送（V2(c)）：status/enable/disable/test。默认关。"""
        try:
            from src.tasks import webpush

            verb = str(action or "status").strip().lower()
            if verb == "status":
                return _serialize({"status": "ok", **webpush.push_status(_account())})
            if verb in ("enable", "disable"):
                result = webpush.set_push_enabled(verb == "enable")
                return _serialize({
                    "status": "ok",
                    **result,
                    "note": (
                        "开关已打开。还差最后一步在设备上完成：打开贾维斯的页面，"
                        "点「开启本机推送」小条授权并订阅，这台设备才能收到。"
                        if verb == "enable"
                        else "推送已关闭，不再往任何设备发。"
                    ),
                })
            if verb == "test":
                result = webpush.send_push(webpush.TEST_PUSH, account=_account())
                return _serialize({"status": "ok", **result})
            return _serialize({
                "status": "failed",
                "error": f"未知动作：{action}（只支持 status / enable / disable / test）",
            })
        except Exception as exc:  # noqa: BLE001
            return _serialize({"status": "failed", "error": str(exc)})

    def routine_templates(action: str = "list", template_id: str = "", **kwargs: Any) -> str:
        """例程模板库：list 看模板与状态；enable/disable 一句话开关模板。"""
        try:
            from src.tasks.briefing import list_template_states, set_template_enabled
            from src.tasks.routines import get_routine_store

            store = get_routine_store()
            account = _account()
            verb = str(action or "list").strip().lower()
            if verb == "list":
                return _serialize({
                    "status": "ok",
                    "templates": list_template_states(store, account),
                    "note": "模板默认不启用；用户说「启用晨间简报」时用 enable。",
                })
            if verb in ("enable", "disable"):
                result = set_template_enabled(
                    store,
                    account,
                    created_by=account,
                    template_id=str(template_id or ""),
                    enabled=verb == "enable",
                )
                return _serialize({"status": "ok", **result})
            return _serialize({
                "status": "failed",
                "error": f"未知动作：{action}（只支持 list / enable / disable）",
            })
        except Exception as exc:  # noqa: BLE001
            return _serialize({"status": "failed", "error": str(exc)})

    tools.append(
        Tool(
            tool_id="liuhao.briefing.now",
            name="morning_briefing",
            version="1.0.0",
            description=(
                "生成一份晨间简报：昨日例程结果、今日例程/提醒/日程、待审批一次汇总。"
                "用户说「给我一份晨间简报 / 今天有什么安排」时用这个工具。"
            ),
            capability="briefing.now",
            schema={"type": "object", "properties": {}},
            fn=morning_briefing,
            risk="LOW",
        )
    )
    tools.append(
        Tool(
            tool_id="liuhao.briefing.broadcast",
            name="briefing_broadcast",
            version="1.0.0",
            description=(
                "把晨间简报念出声：action=start 生成简报并合成语音、控制台自动播放；"
                "action=stop 停播；action=status 看合成/播放进度。"
                "用户说「播报简报 / 念一下今天的简报 / 停，别念了」时用这个工具。"
                "语音引擎没装时会如实失败并给出原因，不要假装在播。"
            ),
            capability="briefing.broadcast",
            schema={
                "type": "object",
                "properties": {
                    "action": {"type": "string", "description": "start / stop / status"},
                },
                "required": ["action"],
            },
            fn=briefing_broadcast,
            risk="LOW",
        )
    )
    tools.append(
        Tool(
            tool_id="liuhao.push.notifications",
            name="push_notifications",
            version="1.0.0",
            description=(
                "手机推送开关（PWA Web Push，默认关）：action=status 看开关与本账号订阅数；"
                "action=enable/disable 开关总闸；action=test 给本账号设备发一条测试推送。"
                "用户说「把提醒推到我手机上 / 开启手机推送 / 关掉推送 / 测一下推送」时用。"
                "开启总闸后还要用户在设备页面点一次「开启本机推送」完成授权订阅，如实转达。"
                "推送只发通用文案，不带提醒标题或简报正文。"
            ),
            capability="push.notifications",
            schema={
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "description": "status / enable / disable / test",
                    },
                },
                "required": ["action"],
            },
            fn=push_notifications,
            risk="MEDIUM",
        )
    )
    tools.append(
        Tool(
            tool_id="liuhao.routine.templates",
            name="routine_templates",
            version="1.0.0",
            description=(
                "例程模板库（晨间简报/晚间小结/午饭提醒/熬夜提醒/每周开场）："
                "action=list 查看模板与启用状态；action=enable/disable + template_id 开关某个模板。"
                "用户说「启用晨间简报 / 关掉熬夜提醒」时用这个工具。"
            ),
            capability="routine.templates",
            schema={
                "type": "object",
                "properties": {
                    "action": {"type": "string", "description": "list / enable / disable"},
                    "template_id": {
                        "type": "string",
                        "description": "模板 id：morning_briefing / evening_digest / lunch_rest / late_night_rest / weekly_kickoff",
                    },
                },
                "required": ["action"],
            },
            fn=routine_templates,
            risk="MEDIUM",
        )
    )

    # ============ 多端同步（大升级 U5：默认关，配对后可用） ============
    def device_sync(action: str = "status", peer: str = "", **kwargs: Any) -> str:
        """多端同步管理：status 看状态；enable/disable 开关；
        pair 签发配对令牌（给另一台设备登记）；add_peer 登记对端；
        run + peer 立刻与对端同步一轮。"""
        try:
            from src.sync import service as sync_service
            from src.sync import state as sync_state

            verb = str(action or "status").strip().lower()
            if verb == "status":
                return _serialize({"status": "ok", **sync_service.sync_status(_account())})
            if verb in ("enable", "disable"):
                sync_state.set_enabled(verb == "enable")
                return _serialize({"status": "ok", **sync_service.sync_status(_account())})
            if verb == "pair":
                token = sync_state.create_pairing_token(_account())
                return _serialize({
                    "status": "ok",
                    "pairing_token": token,
                    "note": "只显示这一次：把令牌填到另一台设备的「登记对端」里；本机只存哈希。",
                })
            if verb == "add_peer":
                name = str(kwargs.get("name") or peer or "")
                base_url = str(kwargs.get("base_url") or "")
                token = str(kwargs.get("token") or "")
                if not name or not base_url or not token:
                    return _serialize({"status": "failed", "error": "登记对端需要 name、base_url、token 三样"})
                sync_state.add_peer(name, base_url, token)
                return _serialize({"status": "ok", **sync_service.sync_status(_account())})
            if verb == "run":
                name = str(peer or kwargs.get("name") or "")
                if not name:
                    return _serialize({"status": "failed", "error": "要说清和哪台设备同步（peer=设备名）"})
                result = sync_service.sync_with_peer(name, _account())
                return _serialize({"status": "ok", **result})
            return _serialize({
                "status": "failed",
                "error": f"未知动作：{action}（只支持 status / enable / disable / pair / add_peer / run）",
            })
        except Exception as exc:  # noqa: BLE001
            return _serialize({"status": "failed", "error": str(exc)})

    tools.append(
        Tool(
            tool_id="liuhao.sync.manage",
            name="device_sync",
            version="1.0.0",
            description=(
                "多端同步（两台贾维斯之间同步聊天/Bot/知识/设置，默认关）："
                "action=status 查看；enable/disable 开关；pair 签发配对令牌；"
                "add_peer 登记对端（需 name/base_url/token）；run + peer 立刻同步一轮。"
                "用户说「和我的另一台电脑同步一下 / 开启多端同步」时用这个工具。"
            ),
            capability="sync.manage",
            schema={
                "type": "object",
                "properties": {
                    "action": {"type": "string", "description": "status / enable / disable / pair / add_peer / run"},
                    "peer": {"type": "string", "description": "对端设备名（run 时必填）"},
                    "name": {"type": "string", "description": "登记对端时的设备名"},
                    "base_url": {"type": "string", "description": "登记对端时的地址（对端网关的局域网地址与端口）"},
                    "token": {"type": "string", "description": "登记对端时的配对令牌"},
                },
                "required": ["action"],
            },
            fn=device_sync,
            risk="MEDIUM",
        )
    )

    tools.append(
        Tool(
            tool_id="liuhao.voice.status",
            name="voice_status",
            version="1.0.0",
            description=(
                "查看语音功能（语音识别/朗读/唤醒词）现在能不能用、用的哪个引擎、"
                "缺什么怎么装。用户问「语音能用吗 / 怎么不能语音」时用这个工具。"
            ),
            capability="voice.status",
            schema={"type": "object", "properties": {}},
            fn=voice_status,
            risk="LOW",
        )
    )

    tools.append(
        Tool(
            tool_id="liuhao.twa.status",
            name="android_app_status",
            version="1.0.0",
            description=(
                "查看安卓 App（把贾维斯网页版包成真 App 的 TWA 方案）到哪一步了："
                "PWA 底子齐不齐、打包套件配好没有、还差什么步骤。"
                "用户问「安卓 App 能装了吗 / 怎么把贾维斯装到安卓手机上 / "
                "TWA 打包到哪一步了」时用这个工具。"
            ),
            capability="twa.status",
            schema={"type": "object", "properties": {}},
            fn=android_app_status,
            risk="LOW",
        )
    )

    tools.append(
        Tool(
            tool_id="liuhao.upgrade.check",
            name="check_update",
            version="1.0.0",
            description=(
                "检查贾维斯是否有新版本（只检查、绝不自动安装）。"
                "用户问「有新版本吗 / 检查一下更新」时用这个工具。"
            ),
            capability="upgrade.check",
            schema={"type": "object", "properties": {}},
            fn=check_update,
            risk="LOW",
        )
    )
    tools.append(
        Tool(
            tool_id="liuhao.upgrade.apply",
            name="apply_update",
            version="1.0.0",
            description=(
                "把贾维斯升级到已检查到的新版本：自动下载、校验哈希、"
                "升级前备份、替换程序（不动数据），失败自动回滚，完成后需重启生效。"
                "只有用户明确说「确认升级」后才可调用，且必须传 confirm=true。"
            ),
            capability="upgrade.apply",
            schema={
                "type": "object",
                "properties": {
                    "confirm": {
                        "type": "boolean",
                        "description": "用户已明确确认升级时传 true，否则 false",
                    },
                },
                "required": ["confirm"],
            },
            fn=apply_update,
            risk="HIGH",
        )
    )

    tools.append(
        Tool(
            tool_id="liuhao.usage.report",
            name="usage_report",
            version="1.0.0",
            description=(
                "查询当前账号的模型用量与估算花费（按月、按模型拆分）。"
                "用户问「这个月用了多少 / 花了多少钱 / 哪个模型用得最多」时用这个工具；"
                "month 可传 YYYY-MM，留空为本月。数字与设置里的用量页同一口径。"
            ),
            capability="usage.report",
            schema={
                "type": "object",
                "properties": {
                    "month": {
                        "type": "string",
                        "description": "月份 YYYY-MM，留空为本月",
                    },
                },
            },
            fn=usage_report,
            risk="LOW",
        )
    )

    # ============ 成本智能路由开关（第二季 V9） ============
    # 一句话开关：账号设置 cost_routing_enabled（默认关）。status 如实报
    # 映射现状：未给 chat_light 指派模型/价格护栏不过时，明说「开了也不
    # 生效」及原因，不许只报「已开启」冒充在省钱。
    def cost_routing(action: str = "status", **kwargs: Any) -> str:
        """成本智能路由：status / enable / disable。"""
        try:
            from src.ai.model_router import light_mapping_status
            from src.gateway.a2a import get_account_settings, set_account_settings

            account = _account()
            verb = str(action or "status").strip().lower()
            if verb in ("enable", "disable"):
                enabled = verb == "enable"
                set_account_settings(account, cost_routing_enabled=enabled)
                settings = get_account_settings(account)
                mapping = light_mapping_status()
                return _serialize({
                    "status": "ok",
                    "enabled": bool(settings.get("cost_routing_enabled")),
                    "mapping": mapping,
                    "note": (
                        "已开启。" if enabled else "已关闭，全部对话走原模型链。"
                    ) + (
                        ""
                        if (not enabled or (mapping["configured"] and mapping["price_ok"]))
                        else f"注意：现在开了也不生效——{mapping['reason']}。"
                    ),
                })
            if verb == "status":
                settings = get_account_settings(account)
                mapping = light_mapping_status()
                return _serialize({
                    "status": "ok",
                    "enabled": bool(settings.get("cost_routing_enabled")),
                    "mapping": mapping,
                    "note": (
                        "省档路由"
                        + ("已开启" if settings.get("cost_routing_enabled") else "未开启（默认）")
                        + (
                            f"；省档模型：{mapping['light_model']}（默认模型 {mapping['default_model']}）。"
                            if mapping["configured"] and mapping["price_ok"]
                            else f"；映射现状：{mapping['reason']}。"
                        )
                    ),
                })
            return _serialize({
                "status": "failed",
                "error": f"未知 action: {verb}（可用 status/enable/disable）",
            })
        except Exception as exc:  # noqa: BLE001 - 失败如实回
            return _serialize({"status": "failed", "error": str(exc)})

    tools.append(
        Tool(
            tool_id="liuhao.cost.routing",
            name="cost_routing",
            version="1.0.0",
            description=(
                "成本智能路由（把明确的轻活交给更便宜的模型、硬活永远走原模型）："
                "status 看开关与省档映射现状，enable/disable 开关（账号级，默认关）。"
                "用户说「开启省钱模式/成本路由」「轻活走便宜模型」时用这个工具；"
                "开了但没配省档映射时必须如实说明不生效的原因。"
            ),
            capability="cost.routing",
            schema={
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": ["status", "enable", "disable"],
                    },
                },
                "required": ["action"],
            },
            fn=cost_routing,
            risk="MEDIUM",
        )
    )

    # ============ 插件安全子集 + 知识库/联网检索（0.4.6，5-4/6-4） ============
    # 只接纯计算、无外部依赖、无写文件的插件子集（计算、base64、hash、
    # 单位换算、时区）+ 真知识库检索 + 真联网检索；假 SearchTool 已删，
    # 二维码因“要写文件出图/无库回退是假图案”本轮不接。见 plugin_tools.py。
    from .plugin_tools import build_plugin_tools

    tools.extend(build_plugin_tools())

    # ============ 派给一队（7-3 Step 2：聊天接线） ============
    # 队长（贾维斯本体）把子任务派给底下的队员：异步执行、返回任务 id，
    # 不当场等结果；含危险动作时 approval_required=True，先挂起等批。
    # 注册闸同样管它（B 档默认关，Owner 在技能页开了才进聊天工具集）。
    def delegate_to_team(
        role: str = "",
        task: str = "",
        plan_id: str = "",
        approval_required: bool = False,
        danger_kind: str = "",
        **kwargs: Any,
    ) -> str:
        """把子任务派给一名队员（查资料/整理/提醒/写代码），异步执行。"""
        role_value = (role or kwargs.get("member_role") or "").strip().lower()
        task_text = (task or kwargs.get("text") or "").strip()
        if role_value not in ("researcher", "analyst", "reminder", "coder"):
            return _serialize(
                {
                    "status": "failed",
                    "error": (
                        f"没有这个队员角色：{role_value}（只认 researcher / "
                        "analyst / reminder / coder；队长不接派活）"
                    ),
                }
            )
        if not task_text:
            return _serialize({"status": "failed", "error": "任务不能为空"})
        from ..team_service import get_team_service

        try:
            result = get_team_service().dispatch(
                principal,
                role_value,
                task_text,
                plan_id=str(plan_id or ""),
                approval_required=bool(approval_required),
                danger_kind=str(danger_kind or ""),
            )
        except Exception as exc:  # noqa: BLE001 - 派活失败如实回给模型
            return _serialize({"status": "failed", "error": str(exc)})
        return _serialize(result)

    tools.append(
        Tool(
            tool_id="liuhao.team.delegate",
            name="delegate_to_team",
            version="1.0.0",
            description=(
                "把子任务派给贾维斯底下的队员（researcher 查资料 / analyst 整理 / "
                "reminder 提醒 / coder 写代码），异步执行，返回任务 id 与状态；"
                "含危险动作时 approval_required=true，先挂起等 Joseph 批了才跑"
            ),
            capability="team.delegate",
            schema={
                "type": "object",
                "properties": {
                    "role": {
                        "type": "string",
                        "enum": ["researcher", "analyst", "reminder", "coder"],
                    },
                    "task": {"type": "string"},
                    "plan_id": {"type": "string", "default": ""},
                    "approval_required": {"type": "boolean", "default": False},
                    "danger_kind": {
                        "type": "string",
                        "enum": ["", "delete_file", "code_merge", "send_message"],
                        "default": "",
                    },
                },
                "required": ["role", "task"],
            },
            fn=delegate_to_team,
            risk="MEDIUM",
        )
    )

    # ============ 已安装技能（Q1：聊天接线，复用 SkillRuntime.run） ============
    # 人类聊天以前调不到任何技能：技能提示词注入与 SkillRuntime.run 只在
    # 员工目标链路，聊天链路只有内置工具（审计第 9 条 FAIL）。这里加一个
    # 技能执行工具，让模型经现有工具循环调技能——不另起大重构。
    #   * 只暴露已启用的技能：描述里只列 enabled 的技能与入口；调用时
    #     再实时读一次技能库，未启用/未安装/无入口一律诚实拒绝（描述
    #     过期也骗不了执行层）。
    #   * 首次运行须确认：SkillRuntime.run 无授权时只返回 pending_approval
    #     审批单、不启动任何进程；确认后才执行（见 POST /v1/skills/{id}/run）。
    #   * 注册闸同样管它：分档见 chat_tool_settings（B 档默认关，Owner 在
    #     技能页开了才进聊天工具集）。
    def _enabled_skill_summaries() -> List[Dict[str, Any]]:
        """当前已启用的技能（id/名/入口）；技能库读不到时返回空列表。"""
        try:
            from src.gateway.skill_runtime import SkillRuntime
            from src.gateway.skill_store import SkillStore

            runtime = SkillRuntime()
            out: List[Dict[str, Any]] = []
            for record in SkillStore().list_records():
                if not record.get("enabled"):
                    continue
                package = runtime.package(str(record.get("id") or ""))
                entrypoints = ((package or {}).get("runtime") or {}).get("entrypoints") or []
                out.append(
                    {
                        "skill_id": str(record.get("id") or ""),
                        "name": str(record.get("name") or ""),
                        "entrypoints": [
                            str(item.get("id") or "")
                            for item in entrypoints
                            if isinstance(item, dict) and str(item.get("id") or "")
                        ],
                    }
                )
            return out
        except Exception:  # noqa: BLE001 - 技能库不可用时聊天不能跟着挂
            return []

    _skill_summaries = _enabled_skill_summaries()
    if _skill_summaries:
        _skill_catalog = "; ".join(
            f"{item['name']}（id={item['skill_id']}，入口："
            f"{'、'.join(item['entrypoints']) or '无可执行入口'}）"
            for item in _skill_summaries
        )
        _skill_description = (
            "运行一个已安装并启用的技能（脚本入口）。只能运行下面列出的技能；"
            "未启用、未安装、没有入口的技能一律拒绝。首次运行需要 Joseph 在"
            "技能页确认，确认前只返回审批单、不会执行。"
            f"已启用的技能：{_skill_catalog}。"
            "没有列出的技能不要编 id，先回用户说没装/没启用。"
        )
    else:
        _skill_description = (
            "运行一个已安装并启用的技能（脚本入口）。当前没有已启用的技能，"
            "不要编技能 id，先回用户说没有可用的技能、请先在技能中心安装并启用。"
        )

    def run_skill(
        skill_id: str = "",
        entrypoint: str = "",
        params: Optional[Dict[str, Any]] = None,
        **kwargs: Any,
    ) -> str:
        """运行已安装技能的脚本入口（首次运行须确认，只返回审批单）。"""
        sid = (skill_id or kwargs.get("skill") or "").strip()
        if not sid:
            # 无参数时返回实时可用技能清单，供模型先选再调（描述可能过期，
            # 以这里实时读到的为准）。
            return _serialize(
                {"status": "list", "skills": _enabled_skill_summaries()}
            )
        ep = (entrypoint or kwargs.get("entrypoint_id") or "").strip()
        if not ep:
            return _serialize(
                {"status": "failed", "error": "请提供 entrypoint 参数（技能的运行入口 id）。"}
            )
        try:
            from src.gateway.skill_runtime import (
                SkillRunRefused,
                SkillRuntime,
                SkillRuntimeError,
            )
            from src.gateway.skill_store import SkillNotFound, SkillStore

            try:
                record = SkillStore().get(sid)
            except SkillNotFound:
                return _serialize(
                    {"status": "failed", "error": f"没有这个技能：{sid}。请先在技能中心安装。"}
                )
            if not record.get("enabled"):
                return _serialize(
                    {
                        "status": "failed",
                        "error": f"技能「{record.get('name') or sid}」未启用，请先在技能中心启用。",
                    }
                )
            try:
                state, body = SkillRuntime().run(
                    record=record,
                    entrypoint_id=ep,
                    params=dict(params or {}),
                    requester=principal,
                )
            except SkillRunRefused as exc:
                return _serialize({"status": "failed", "error": str(exc)})
            except SkillRuntimeError as exc:
                return _serialize({"status": "failed", "error": str(exc)})
        except Exception as exc:  # noqa: BLE001 - 技能库读失败也要如实回给模型
            return _serialize({"status": "failed", "error": f"技能运行失败：{exc}"})
        if state == "pending_approval":
            return _serialize(
                {
                    "status": "pending_approval",
                    "skill_id": sid,
                    "approval_id": body.get("approval_id"),
                    "skill_name": body.get("skill_name"),
                    "entrypoint": body.get("entrypoint"),
                    "message": (
                        "首次运行需要 Joseph 在技能页确认，确认前不会执行。"
                        "请把审批单转告用户，等他确认后再跑。"
                    ),
                }
            )
        return _serialize({"status": "finished", "skill_id": sid, "run": body})

    tools.append(
        Tool(
            tool_id="liuhao.skill.run",
            name="run_skill",
            version="1.0.0",
            description=_skill_description,
            capability="skill.run",
            schema={
                "type": "object",
                "properties": {
                    "skill_id": {"type": "string"},
                    "entrypoint": {"type": "string"},
                    "params": {"type": "object"},
                },
                "required": ["skill_id", "entrypoint"],
            },
            fn=run_skill,
            risk="MEDIUM",
        )
    )

    # ============ 技能包生态（大升级第二季 V7：聊天管生命周期） ============
    # 以前装/卸/升级技能只能开技能页点，聊天里说一句话办不了——违反
    # 「精而简：一句话可达」。这里补齐五个工具，全部走
    # ``src/gateway/skill_packages`` 服务层（与 HTTP 面同一套语义）：
    #   * list / inspect 只读：inspect 只解析并摊开权限声明，不安装；
    #   * install / upgrade / uninstall 是写操作：不带 confirm=true 时
    #     只返回"待确认 + 权限声明"，把声明转告用户、等他亲口确认后
    #     才许带 confirm=true 真执行（与 apply_update 同口径）。
    #   * 安装的技能默认未启用，脚本首次运行仍走 run_skill 的审批流，
    #     聊天安装不会给任何包绕过授权的机会。
    def list_skill_packages(**kwargs: Any) -> str:
        """列出已登记技能与已装包（只读）。"""
        try:
            from src.gateway.skill_packages import list_packages

            return _serialize({"status": "ok", "skills": list_packages()})
        except Exception as exc:  # noqa: BLE001
            return _serialize({"status": "failed", "error": f"技能列表读取失败：{exc}"})

    def inspect_skill_package(path: str = "", **kwargs: Any) -> str:
        """预览一个技能包的声明（名称/版本/作者/入口/权限/依赖），不安装。"""
        target = (path or kwargs.get("package_path") or "").strip()
        if not target:
            return _serialize(
                {"status": "failed", "error": "请提供技能包的本机路径（目录或 .zip/.md/.json）。"}
            )
        try:
            from src.gateway.skill_packages import (
                SkillPackageError,
                inspect_path,
            )

            try:
                summary = inspect_path(target)
            except SkillPackageError as exc:
                return _serialize({"status": "failed", "error": str(exc)})
            return _serialize(
                {
                    "status": "preview",
                    "package": summary,
                    "message": (
                        "这只是预览，没有安装。请把权限声明转告用户；"
                        "用户确认安装后再用 install_skill_package 并传 confirm=true。"
                    ),
                }
            )
        except Exception as exc:  # noqa: BLE001
            return _serialize({"status": "failed", "error": f"技能包预览失败：{exc}"})

    def install_skill_package(
        path: str = "", confirm: bool = False, **kwargs: Any
    ) -> str:
        """安装技能包（须用户亲口确认；未确认时只返回权限声明）。"""
        target = (path or kwargs.get("package_path") or "").strip()
        if not target:
            return _serialize(
                {"status": "failed", "error": "请提供技能包的本机路径（目录或 .zip/.md/.json）。"}
            )
        try:
            from src.gateway.skill_packages import (
                SkillPackageError,
                inspect_path,
                install_from_path,
            )

            if confirm is not True:
                try:
                    summary = inspect_path(target)
                except SkillPackageError as exc:
                    return _serialize({"status": "failed", "error": str(exc)})
                return _serialize(
                    {
                        "status": "pending_confirmation",
                        "package": summary,
                        "message": (
                            "尚未安装。请先把这个包的权限声明（声明权限、能力、"
                            "可执行入口、依赖）转告用户，用户亲口说「确认安装」后"
                            "再带 confirm=true 调用。"
                        ),
                    }
                )
            try:
                result = install_from_path(target, principal=principal)
            except SkillPackageError as exc:
                return _serialize({"status": "failed", "error": str(exc)})
            return _serialize({"status": "installed", **result})
        except Exception as exc:  # noqa: BLE001
            return _serialize({"status": "failed", "error": f"技能包安装失败：{exc}"})

    def upgrade_skill_package(
        skill_id: str = "", path: str = "", confirm: bool = False, **kwargs: Any
    ) -> str:
        """升级一个已装技能（须用户亲口确认；未确认时只返回新版声明）。"""
        sid = (skill_id or kwargs.get("skill") or "").strip()
        target = (path or kwargs.get("package_path") or "").strip()
        if not sid or not target:
            return _serialize(
                {"status": "failed", "error": "请提供 skill_id 与新版技能包的本机路径。"}
            )
        try:
            from src.gateway.skill_packages import (
                SkillPackageError,
                inspect_path,
                upgrade_from_path,
            )

            if confirm is not True:
                try:
                    summary = inspect_path(target)
                except SkillPackageError as exc:
                    return _serialize({"status": "failed", "error": str(exc)})
                return _serialize(
                    {
                        "status": "pending_confirmation",
                        "package": summary,
                        "message": (
                            "尚未升级。请把新版的权限声明转告用户，用户亲口说"
                            "「确认升级」后再带 confirm=true 调用。"
                        ),
                    }
                )
            try:
                result = upgrade_from_path(sid, target, principal=principal)
            except SkillPackageError as exc:
                return _serialize({"status": "failed", "error": str(exc)})
            return _serialize({"status": "upgraded", **result})
        except Exception as exc:  # noqa: BLE001
            return _serialize({"status": "failed", "error": f"技能升级失败：{exc}"})

    def uninstall_skill_package(
        skill_id: str = "", confirm: bool = False, **kwargs: Any
    ) -> str:
        """卸载一个技能（须用户亲口确认；被绑定/被依赖时拒绝并点名）。"""
        sid = (skill_id or kwargs.get("skill") or "").strip()
        if not sid:
            return _serialize(
                {"status": "failed", "error": "请提供要卸载的 skill_id。"}
            )
        try:
            from src.gateway.skill_packages import (
                SkillPackageError,
                uninstall_package,
            )
            from src.gateway.skill_store import SkillNotFound, SkillStore

            if confirm is not True:
                try:
                    record = SkillStore().get(sid)
                except SkillNotFound:
                    return _serialize(
                        {"status": "failed", "error": f"没有这个技能：{sid}。"}
                    )
                return _serialize(
                    {
                        "status": "pending_confirmation",
                        "skill_id": sid,
                        "name": record.get("name"),
                        "message": (
                            "尚未卸载。请向用户确认要卸载的是这个技能，"
                            "用户亲口说「确认卸载」后再带 confirm=true 调用。"
                        ),
                    }
                )
            try:
                result = uninstall_package(sid, principal=principal)
            except SkillPackageError as exc:
                return _serialize({"status": "failed", "error": str(exc)})
            return _serialize({"status": "uninstalled", **result})
        except Exception as exc:  # noqa: BLE001
            return _serialize({"status": "failed", "error": f"技能卸载失败：{exc}"})

    tools.append(
        Tool(
            tool_id="liuhao.skill.list_packages",
            name="list_skill_packages",
            version="1.0.0",
            description=(
                "列出已登记的技能与已安装的技能包（名称/版本/作者/是否启用/"
                "依赖）。只读。用户问「装了哪些技能/技能包」时用它，不要凭记忆回答。"
            ),
            capability="skill.manage",
            schema={"type": "object", "properties": {}},
            fn=list_skill_packages,
            risk="LOW",
        )
    )
    tools.append(
        Tool(
            tool_id="liuhao.skill.inspect_package",
            name="inspect_skill_package",
            version="1.0.0",
            description=(
                "预览一个本机技能包（目录或 .zip/.md/.json）：解析清单并摊开"
                "名称/版本/作者/可执行入口/声明权限/依赖核对结果。只预览，"
                "不安装、不产生任何授权。安装任何包之前必须先用它看声明。"
            ),
            capability="skill.manage",
            schema={
                "type": "object",
                "properties": {"path": {"type": "string"}},
                "required": ["path"],
            },
            fn=inspect_skill_package,
            risk="LOW",
        )
    )
    tools.append(
        Tool(
            tool_id="liuhao.skill.install_package",
            name="install_skill_package",
            version="1.0.0",
            description=(
                "从本机路径安装技能包。写操作：不带 confirm 时只返回权限声明"
                "供转告用户；只有用户亲口说「确认安装」后才可传 confirm=true。"
                "安装后技能默认未启用，脚本首次运行仍须单独审批。"
            ),
            capability="skill.manage",
            schema={
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "confirm": {
                        "type": "boolean",
                        "description": "用户已明确确认安装时传 true，否则 false",
                    },
                },
                "required": ["path", "confirm"],
            },
            fn=install_skill_package,
            risk="HIGH",
        )
    )
    tools.append(
        Tool(
            tool_id="liuhao.skill.upgrade_package",
            name="upgrade_skill_package",
            version="1.0.0",
            description=(
                "用本机路径的新包升级一个已装技能（保留技能 id 与员工绑定，"
                "版本/作者/依赖同步更新，旧的运行授权自动失效）。写操作："
                "只有用户亲口说「确认升级」后才可传 confirm=true。"
            ),
            capability="skill.manage",
            schema={
                "type": "object",
                "properties": {
                    "skill_id": {"type": "string"},
                    "path": {"type": "string"},
                    "confirm": {
                        "type": "boolean",
                        "description": "用户已明确确认升级时传 true，否则 false",
                    },
                },
                "required": ["skill_id", "path", "confirm"],
            },
            fn=upgrade_skill_package,
            risk="HIGH",
        )
    )
    tools.append(
        Tool(
            tool_id="liuhao.skill.uninstall_package",
            name="uninstall_skill_package",
            version="1.0.0",
            description=(
                "卸载一个技能（连同已装包与已记住的运行授权）。仍绑定员工或"
                "被别的技能声明为依赖时会拒绝并点名。写操作：只有用户亲口说"
                "「确认卸载」后才可传 confirm=true。"
            ),
            capability="skill.manage",
            schema={
                "type": "object",
                "properties": {
                    "skill_id": {"type": "string"},
                    "confirm": {
                        "type": "boolean",
                        "description": "用户已明确确认卸载时传 true，否则 false",
                    },
                },
                "required": ["skill_id", "confirm"],
            },
            fn=uninstall_skill_package,
            risk="HIGH",
        )
    )

    if not include_disabled:
        # 注册闸：只把技能页开关打开的聊天工具交给模型。函数内懒导入
        # gateway 模块（与 skill_prompt.py 的做法一致），避免模块级循环。
        from src.gateway.chat_tool_settings import is_chat_tool_enabled

        tools = [tool for tool in tools if is_chat_tool_enabled(tool.tool_id)]

    return tools


def tools_for_role(
    principal: str,
    role: str,
    status_fn: Optional[Callable[[], Dict[str, Any]]] = None,
    *,
    include_disabled: bool = False,
) -> List[Tool]:
    """某队员角色能调的聊天工具子集（7-3 Step 3）。

    队员的工具是贾维斯工具集的子集（``collaboration.ROLE_TOOL_ALLOWLIST``），
    角色表里没有的 capability 调不到；``make_tools()`` 里没有的工具不会
    凭空出现（不编能力）。未知角色返回空列表。注册闸同样生效。
    """
    from ..collaboration import ROLE_TOOL_ALLOWLIST

    allowed = ROLE_TOOL_ALLOWLIST.get((role or "").strip().lower())
    if allowed is None:
        return []
    return [
        tool
        for tool in make_tools(principal, status_fn, include_disabled=include_disabled)
        if tool.capability in allowed
    ]


def _tool_params_hint(tool: Tool) -> str:
    """只列参数名与必填标记：小模型需要"该传哪些字段"，不需要完整 JSON Schema。

    类型、枚举、默认值都留在 ``schema`` 里给校验/审计用；提示词里塞长 schema
    会把小模型带偏（实测它会照抄字段说明而不是真的调用工具）。
    """
    schema = getattr(tool, "schema", None)
    if not isinstance(schema, dict):
        return ""
    properties = schema.get("properties")
    if not isinstance(properties, dict) or not properties:
        return ""
    required = set(schema.get("required") or [])
    names = [
        f"{name}*" if name in required else str(name) for name in properties
    ]
    return "；参数：" + " / ".join(names)


@observe("tools.build_tool_prompt")
def build_tool_prompt(tools: List[Tool]) -> str:
    """生成注入 system prompt 的工具描述段。"""
    if not tools:
        return ""
    lines = [
        "",
        "你可以调用以下工具查询系统信息。需要查询时，只输出一行 JSON，不要输出任何其他文字：",
        '{"tool": "<工具名>", "args": {...}}',
        "可用的工具：",
    ]
    for t in tools:
        lines.append(f"- {t.name}: {t.description}{_tool_params_hint(t)}")
    lines.append("如果不需要查询，直接正常回答即可。")
    return "\n".join(lines)


@observe("tools.parse_tool_call")
def parse_tool_call(text: str) -> Optional[Tuple[str, Dict[str, Any]]]:
    """从 LLM 输出中提取工具调用，返回 ``(tool_name, args)`` 或 None。

    容错解析：支持纯 JSON、`` ```json ... ``` `` 代码块包裹两种形式；解析失败
    或非工具调用返回 None（此时按普通文本回复处理，不中断对话）。
    """
    stripped = text.strip()

    def _try_load(candidate: str) -> Optional[Tuple[str, Dict[str, Any]]]:
        try:
            data = json.loads(candidate)
        except (json.JSONDecodeError, TypeError):
            return None
        if isinstance(data, dict) and isinstance(data.get("tool"), str):
            args = data.get("args") or {}
            if isinstance(args, dict):
                return data["tool"], args
        return None

    # 1. 纯 JSON（整段就是一个对象）
    if stripped.startswith("{") and stripped.endswith("}"):
        result = _try_load(stripped)
        if result:
            return result

    # 2. ```json ... ``` 代码块包裹
    if stripped.startswith("```"):
        # 去掉首行 ```json 和末尾 ```
        lines = stripped.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        result = _try_load("\n".join(lines).strip())
        if result:
            return result

    # 3. 宽松兜底：小模型可能输出近似但非法的 JSON（缺引号/冒号等），
    #    用正则提取工具名 + 尽量解析 args；解析失败则 args 置空。
    tool_match = _FUZZY_TOOL_RE.search(text)
    if tool_match:
        tool_name = tool_match.group(1)
        args: Dict[str, Any] = {}
        args_match = _FUZZY_ARGS_RE.search(text)
        if args_match:
            try:
                parsed = json.loads(args_match.group(1))
                if isinstance(parsed, dict):
                    args = parsed
            except (json.JSONDecodeError, TypeError):
                pass
        return tool_name, args

    return None
