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
            return _serialize({
                "status": "ok",
                "month": label,
                "calls": result.get("total", 0),
                "estimated_cost": result.get("cost"),
                "cost_complete": result.get("cost_complete", True),
                "unknown_models": result.get("unknown_models") or [],
                "by_model": by_model,
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
