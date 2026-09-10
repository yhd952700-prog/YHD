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

from ..kernels.audit import audit_query
from ..kernels.memory import MemoryScope, filter_memory
from .observability import observe
from .personal_context import get_personal_context
from .tool_registry import Tool

# 宽松解析用：小模型可能输出近似但非法的 JSON（如 ``"args: {...}"`` 缺引号），
# 用正则兜底提取工具名与参数。
_FUZZY_TOOL_RE = re.compile(r'"tool"\s*:\s*"([^"]+)"')
_FUZZY_ARGS_RE = re.compile(
    r'"(?:args|arguments)"?\s*:\s*(\{.*?\})\s*\}?\s*$', re.DOTALL
)


def _serialize(value: Any) -> str:
    """JSON 序列化工具输出（不可序列化对象降级为 str）。"""
    return json.dumps(value, ensure_ascii=False, default=str)


@observe("tools.make_tools")
def make_tools(
    principal: str,
    status_fn: Optional[Callable[[], Dict[str, Any]]] = None,
) -> List[Tool]:
    """构建鎏灏内置工具集（绑定 principal 上下文）。

    Args:
        principal: 主体 ID（审计/记忆归因）。
        status_fn: 可选，返回系统状态 dict 的回调（绑定到 assistant.stats）。
    """
    tools: List[Tool] = []

    def search_memory(query: str = "", **kwargs: Any) -> str:
        """按关键词搜索本主体在 memory kernel 中的记忆条目。

        参数做防御性别名兼容：小模型可能输出 ``key``/``text``/``keyword``
        等非规范字段名（schema 声明为 ``query``），统一归一化。
        """
        q = (query or kwargs.get("key") or kwargs.get("text")
             or kwargs.get("keyword") or kwargs.get("q") or "")
        if not q:
            return "请提供 query 参数（要搜索的关键词）。"
        entries = filter_memory(MemoryScope.L1)
        q = str(q).lower()
        matches = [
            e for e in entries
            if q in str(e.value).lower()
            or any(q in t.lower() for t in e.tags)
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
                "properties": {"query": {"type": "string"}},
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
        return _serialize({"status": "ok", "key": p.key, "value": p.value, "confidence": p.confidence})

    def personal_add_fact(key: str = "", value: str = "", **kwargs: Any) -> str:
        """记录用户背景事实（如「公司=鎏灏科技」「所在城市=深圳」）。"""
        k = key or kwargs.get("name") or ""
        v = value or kwargs.get("v") or ""
        if not k or not v:
            return "请提供 key 与 value 参数。"
        pcm.record_fact(principal, k, v)
        return _serialize({"status": "ok", "key": k, "value": v})

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

    return tools


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
        lines.append(f"- {t.name}: {t.description}")
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
