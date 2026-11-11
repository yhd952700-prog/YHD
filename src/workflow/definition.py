"""工作流定义：节点 / 连线 / 触发器的数据结构、校验与内置模板。

本模块**只做数据**，不执行任何东西：把"人画的流程"变成一份可校验、可持久化、
可导入导出的 JSON。真正落到执行层的动作由 :mod:`src.workflow.engine` 的
``ExecutionBridge`` 完成 —— 它复用现有 ``AIStateManager.create_and_execute_goal``
与 ``assign_task``，不另造 Agent 执行引擎。

节点类型与执行层落点
====================
============  ==========================================================
``start``     起点（唯一，无入边）
``end``       终点（无出边）
``task``      创建任务：把目标提交给现有执行层，后台跑、不等待
``agent``     Agent 执行：提交目标并轮询到终态（timeout / retry 在这里生效）
``approval``  人工审批：流程挂起，进审批中心待办队列
``review``    结果审核：与 approval 共用待办机制，语义是"看过结果再放行"
``notify``    通知：发一条真实事件 + 审计
``archive``   归档：把本次运行的输入输出固化到运行实例上
``parallel``  并行分支：所有匹配出边同时执行（真实线程）
``join``      汇聚：等待并行分支（``all`` / ``any``）
============  ==========================================================
"""

from __future__ import annotations

import copy
import re
from typing import Any, Dict, Iterable, List, Optional

SCHEMA_VERSION = 1

NODE_TYPES = (
    "start",
    "end",
    "task",
    "agent",
    "approval",
    "review",
    "notify",
    "archive",
    "parallel",
    "join",
)

#: 会真正调用执行层、可能长时间运行的节点。
EXECUTING_NODE_TYPES = ("task", "agent")

#: 需要人工决定、会让流程挂起的节点。
APPROVAL_NODE_TYPES = ("approval", "review")

#: 校验允许的条件操作符。故意保持保守：解析不了的一律拒绝，不猜语义。
CONDITION_OPS = ("eq", "ne", "in", "contains", "truthy", "falsy")

TRIGGER_TYPES = ("manual", "interval", "daily", "event")

NODE_TYPE_LABELS = {
    "start": "开始",
    "end": "结束",
    "task": "创建任务",
    "agent": "Agent 执行",
    "approval": "人工审批",
    "review": "结果审核",
    "notify": "通知",
    "archive": "归档",
    "parallel": "并行分支",
    "join": "汇聚",
}

#: 默认节点超时（秒）。审批节点默认 1 小时；执行节点默认 10 分钟。
DEFAULT_APPROVAL_TIMEOUT = 3600
DEFAULT_EXECUTION_TIMEOUT = 600

MAX_NODES = 200
MAX_EDGES = 400
MAX_TIMEOUT_SECONDS = 7 * 24 * 3600


class DefinitionError(Exception):
    """流程定义不合法（API 层直接翻译成 400）。"""

    def __init__(self, code: str, message: str, status_code: int = 400) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code

    def detail(self) -> Dict[str, str]:
        return {"code": self.code, "message": self.message}


# ── 小工具 ───────────────────────────────────────────────────


def _text(value: Any, *, limit: int = 500) -> str:
    if value is None:
        return ""
    text = str(value).strip()
    return text[:limit]


def _positive_int(value: Any, default: int, *, maximum: int) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return default
    if number <= 0:
        return default
    return min(number, maximum)


def _non_negative_int(value: Any, default: int, *, maximum: int) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return default
    if number < 0:
        return default
    return min(number, maximum)


def _str_list(value: Any, *, limit: int = 50) -> List[str]:
    if isinstance(value, str):
        raw: Iterable[Any] = value.split(",")
    elif isinstance(value, (list, tuple)):
        raw = value
    else:
        return []
    items: List[str] = []
    for item in raw:
        text = _text(item, limit=128)
        if text and text not in items:
            items.append(text)
    return items[:limit]


_TIME_PATTERN = re.compile(r"^([01]?\d|2[0-3]):([0-5]\d)$")


# ── 节点默认配置 ─────────────────────────────────────────────


def _default_config(node_type: str) -> Dict[str, Any]:
    if node_type in ("task", "agent"):
        return {
            "instruction": "",
            "employee_name": "",
            "scope": "L1",
            "project_id": "",
            "wait": node_type == "agent",
            "timeout_seconds": DEFAULT_EXECUTION_TIMEOUT,
            "retry": {"max_attempts": 1, "backoff_seconds": 0},
        }
    if node_type in APPROVAL_NODE_TYPES:
        return {
            "title": "",
            "operation": "",
            "reason": "",
            "approvers": [],
            "timeout_seconds": DEFAULT_APPROVAL_TIMEOUT,
        }
    if node_type == "notify":
        return {"message": "", "level": "info", "channel": "event"}
    if node_type == "archive":
        return {"note": ""}
    if node_type == "join":
        return {"mode": "all"}
    if node_type == "parallel":
        return {}
    return {}


def _normalise_retry(raw: Any) -> Dict[str, Any]:
    if not isinstance(raw, dict):
        return {"max_attempts": 1, "backoff_seconds": 0}
    return {
        "max_attempts": _non_negative_int(raw.get("max_attempts"), 1, maximum=5),
        "backoff_seconds": _non_negative_int(raw.get("backoff_seconds"), 0, maximum=120),
    }


def _normalise_config(node_type: str, raw: Any) -> Dict[str, Any]:
    config = dict(_default_config(node_type))
    if not isinstance(raw, dict):
        return config
    if node_type in ("task", "agent"):
        config["instruction"] = _text(raw.get("instruction"), limit=4000)
        config["employee_name"] = _text(raw.get("employee_name"), limit=128)
        config["scope"] = _text(raw.get("scope"), limit=8) or "L1"
        config["project_id"] = _text(raw.get("project_id"), limit=128)
        config["wait"] = bool(raw.get("wait", node_type == "agent"))
        config["timeout_seconds"] = _positive_int(
            raw.get("timeout_seconds"),
            DEFAULT_EXECUTION_TIMEOUT,
            maximum=MAX_TIMEOUT_SECONDS,
        )
        config["retry"] = _normalise_retry(raw.get("retry"))
    elif node_type in APPROVAL_NODE_TYPES:
        config["title"] = _text(raw.get("title"), limit=200)
        config["operation"] = _text(raw.get("operation"), limit=200)
        config["reason"] = _text(raw.get("reason"), limit=1000)
        config["approvers"] = _str_list(raw.get("approvers"))
        config["timeout_seconds"] = _positive_int(
            raw.get("timeout_seconds"),
            DEFAULT_APPROVAL_TIMEOUT,
            maximum=MAX_TIMEOUT_SECONDS,
        )
    elif node_type == "notify":
        config["message"] = _text(raw.get("message"), limit=2000)
        config["level"] = _text(raw.get("level"), limit=16) or "info"
        config["channel"] = _text(raw.get("channel"), limit=32) or "event"
    elif node_type == "archive":
        config["note"] = _text(raw.get("note"), limit=500)
    elif node_type == "join":
        config["mode"] = "any" if str(raw.get("mode") or "").strip() == "any" else "all"
    return config


def _normalise_position(raw: Any, index: int) -> Dict[str, int]:
    if isinstance(raw, dict):
        try:
            return {"x": int(raw.get("x", 0)), "y": int(raw.get("y", 0))}
        except (TypeError, ValueError):
            pass
    # 没画布坐标时按列排开，编辑器打开即可读。
    return {"x": 80 + (index % 4) * 220, "y": 60 + (index // 4) * 140}


def _normalise_condition(raw: Any) -> Optional[Dict[str, Any]]:
    if raw is None:
        return None
    if not isinstance(raw, dict):
        raise DefinitionError("invalid_condition", "连线条件必须是对象或 null")
    field = _text(raw.get("field"), limit=200)
    op = _text(raw.get("op"), limit=20) or "eq"
    if not field:
        raise DefinitionError("invalid_condition", "连线条件缺少 field")
    if op not in CONDITION_OPS:
        raise DefinitionError(
            "invalid_condition",
            f"不支持的连线条件操作符：{op}；可选 {', '.join(CONDITION_OPS)}",
        )
    return {"field": field, "op": op, "value": raw.get("value")}


# ── 触发器 ───────────────────────────────────────────────────


def _normalise_trigger(raw: Any, index: int) -> Dict[str, Any]:
    if not isinstance(raw, dict):
        raise DefinitionError("invalid_trigger", f"第 {index + 1} 个触发器不是对象")
    kind = _text(raw.get("type"), limit=16) or "manual"
    if kind not in TRIGGER_TYPES:
        raise DefinitionError(
            "invalid_trigger",
            f"不支持的触发器类型：{kind}；可选 {', '.join(TRIGGER_TYPES)}",
        )
    trigger: Dict[str, Any] = {"type": kind}
    if kind == "interval":
        seconds = _positive_int(raw.get("every_seconds"), 0, maximum=MAX_TIMEOUT_SECONDS)
        if seconds <= 0:
            raise DefinitionError(
                "invalid_trigger", "定时触发（interval）必须给出 every_seconds（>0）"
            )
        trigger["every_seconds"] = seconds
    elif kind == "daily":
        at = _text(raw.get("at"), limit=5)
        if not _TIME_PATTERN.match(at):
            raise DefinitionError(
                "invalid_trigger", "每日触发（daily）必须给出 24 小时制 at，例如 09:00"
            )
        trigger["at"] = at
    elif kind == "event":
        event = _text(raw.get("event"), limit=128)
        if not event:
            raise DefinitionError("invalid_trigger", "事件触发（event）必须给出事件名")
        trigger["event"] = event
        filters = raw.get("filters")
        if isinstance(filters, dict):
            trigger["filters"] = {
                _text(key, limit=64): _text(value, limit=128)
                for key, value in filters.items()
                if _text(key, limit=64)
            }
    return trigger


# ── 主校验 ───────────────────────────────────────────────────


def normalise_definition(
    raw: Any,
    *,
    owner_account_id: str = "",
    created_by: str = "",
) -> Dict[str, Any]:
    """校验并返回一份**新**的定义字典（深拷贝，不修改入参）。

    失败抛 :class:`DefinitionError`，消息里指出具体是哪个节点/连线。
    """
    if not isinstance(raw, dict):
        raise DefinitionError("invalid_definition", "流程定义必须是 JSON 对象")

    name = _text(raw.get("name"), limit=128)
    if not name:
        raise DefinitionError("invalid_definition", "流程名称不能为空")

    nodes_raw = raw.get("nodes")
    if not isinstance(nodes_raw, list) or not nodes_raw:
        raise DefinitionError("invalid_definition", "流程至少需要一个节点")
    if len(nodes_raw) > MAX_NODES:
        raise DefinitionError(
            "invalid_definition", f"节点数量超过上限（{MAX_NODES}）"
        )

    nodes: List[Dict[str, Any]] = []
    seen_ids: set[str] = set()
    for index, item in enumerate(nodes_raw):
        if not isinstance(item, dict):
            raise DefinitionError("invalid_node", f"第 {index + 1} 个节点不是对象")
        node_id = _text(item.get("id"), limit=64)
        if not node_id:
            raise DefinitionError("invalid_node", f"第 {index + 1} 个节点缺少 id")
        if node_id in seen_ids:
            raise DefinitionError("duplicate_node_id", f"节点 id 重复：{node_id}")
        seen_ids.add(node_id)
        node_type = _text(item.get("type"), limit=16)
        if node_type not in NODE_TYPES:
            raise DefinitionError(
                "invalid_node",
                f"节点 {node_id} 的类型 {node_type or '(空)'} 不支持；"
                f"可选 {', '.join(NODE_TYPES)}",
            )
        node_name = _text(item.get("name"), limit=128) or NODE_TYPE_LABELS[node_type]
        nodes.append(
            {
                "id": node_id,
                "type": node_type,
                "name": node_name,
                "config": _normalise_config(node_type, item.get("config")),
                "position": _normalise_position(item.get("position"), index),
            }
        )

    by_id = {node["id"]: node for node in nodes}
    start_nodes = [node for node in nodes if node["type"] == "start"]
    end_nodes = [node for node in nodes if node["type"] == "end"]
    if len(start_nodes) != 1:
        raise DefinitionError(
            "invalid_definition", f"必须且只能有一个 start 节点，当前 {len(start_nodes)} 个"
        )
    if not end_nodes:
        raise DefinitionError("invalid_definition", "流程至少需要一个 end 节点")

    edges_raw = raw.get("edges")
    if edges_raw is None:
        edges_raw = []
    if not isinstance(edges_raw, list):
        raise DefinitionError("invalid_definition", "edges 必须是数组")
    if len(edges_raw) > MAX_EDGES:
        raise DefinitionError("invalid_definition", f"连线数量超过上限（{MAX_EDGES}）")

    edges: List[Dict[str, Any]] = []
    seen_edges: set[tuple[str, str, str]] = set()
    for index, item in enumerate(edges_raw):
        if not isinstance(item, dict):
            raise DefinitionError("invalid_edge", f"第 {index + 1} 条连线不是对象")
        source = _text(item.get("from"), limit=64)
        target = _text(item.get("to"), limit=64)
        if source not in by_id:
            raise DefinitionError("invalid_edge", f"连线起点不存在：{source}")
        if target not in by_id:
            raise DefinitionError("invalid_edge", f"连线终点不存在：{target}")
        if source == target:
            raise DefinitionError("invalid_edge", f"不允许自环：{source}")
        condition = _normalise_condition(item.get("condition"))
        signature = (source, target, repr(condition))
        if signature in seen_edges:
            raise DefinitionError("duplicate_edge", f"重复连线：{source} → {target}")
        seen_edges.add(signature)
        edges.append(
            {
                "id": _text(item.get("id"), limit=64) or f"e{index + 1}",
                "from": source,
                "to": target,
                "condition": condition,
            }
        )

    _validate_graph(nodes, edges, by_id)

    triggers_raw = raw.get("triggers")
    if triggers_raw is None:
        triggers_raw = [{"type": "manual"}]
    if not isinstance(triggers_raw, list):
        raise DefinitionError("invalid_definition", "triggers 必须是数组")
    triggers = [_normalise_trigger(item, index) for index, item in enumerate(triggers_raw)]
    if not triggers:
        triggers = [{"type": "manual"}]

    variables = raw.get("variables")
    if not isinstance(variables, dict):
        variables = {}

    return {
        "schema_version": SCHEMA_VERSION,
        "name": name,
        "description": _text(raw.get("description"), limit=2000),
        "enabled": bool(raw.get("enabled", True)),
        "variables": copy.deepcopy(variables),
        "nodes": nodes,
        "edges": edges,
        "triggers": triggers,
        "owner_account_id": _text(owner_account_id, limit=128),
        "created_by": _text(created_by, limit=128),
    }


def _validate_graph(
    nodes: List[Dict[str, Any]],
    edges: List[Dict[str, Any]],
    by_id: Dict[str, Dict[str, Any]],
) -> None:
    incoming: Dict[str, List[Dict[str, Any]]] = {node["id"]: [] for node in nodes}
    outgoing: Dict[str, List[Dict[str, Any]]] = {node["id"]: [] for node in nodes}
    for edge in edges:
        outgoing[edge["from"]].append(edge)
        incoming[edge["to"]].append(edge)

    for node in nodes:
        node_id = node["id"]
        if node["type"] == "start" and incoming[node_id]:
            raise DefinitionError("invalid_edge", f"start 节点不允许有入边：{node_id}")
        if node["type"] == "end" and outgoing[node_id]:
            raise DefinitionError("invalid_edge", f"end 节点不允许有出边：{node_id}")
        if node["type"] not in ("start", "end") and not incoming[node_id]:
            raise DefinitionError(
                "invalid_graph", f"节点 {node_id} 没有上游连线，永远不会被执行"
            )
        if node["type"] != "end" and not outgoing[node_id]:
            raise DefinitionError(
                "invalid_graph", f"节点 {node_id} 没有下游连线，流程会断在这里"
            )

    # 分支约束：普通节点最多一条无条件出边；多条出边必须显式声明 parallel 或全部带条件。
    for node in nodes:
        outs = outgoing[node["id"]]
        if len(outs) <= 1:
            continue
        if node["type"] == "parallel":
            continue
        if node["type"] == "join":
            raise DefinitionError(
                "invalid_graph", f"汇聚节点 {node['id']} 只能有一条出边"
            )
        unconditional = [edge for edge in outs if edge["condition"] is None]
        if len(unconditional) > 1:
            raise DefinitionError(
                "invalid_graph",
                f"节点 {node['id']} 有多条无条件出边；请改用 parallel 节点或给连线加条件",
            )
        if not unconditional and len(outs) > 1:
            # 全部是条件分支：可以，后续按条件走第一条命中的。
            continue

    # DAG 校验（Kahn）：环路会让流程无法确定何时结束，直接拒绝。
    indegree = {node_id: len(incoming[node_id]) for node_id in incoming}
    queue = [node_id for node_id, degree in indegree.items() if degree == 0]
    visited = 0
    adjacency = {node_id: [edge["to"] for edge in outgoing[node_id]] for node_id in outgoing}
    while queue:
        current = queue.pop()
        visited += 1
        for target in adjacency[current]:
            indegree[target] -= 1
            if indegree[target] == 0:
                queue.append(target)
    if visited != len(nodes):
        raise DefinitionError("invalid_graph", "流程存在环路；当前版本只支持有向无环图")

    # 并行分支内的人工审批没法在恢复时重建分支栅栏：在定义期就拒绝，
    # 而不是跑到一半才失败。
    for node in nodes:
        if node["type"] != "parallel":
            continue
        pending = [edge["to"] for edge in outgoing[node["id"]]]
        seen: set[str] = set()
        while pending:
            current = pending.pop()
            if current in seen:
                continue
            seen.add(current)
            target = by_id[current]
            if target["type"] == "join":
                continue
            if target["type"] in APPROVAL_NODE_TYPES:
                raise DefinitionError(
                    "invalid_graph",
                    f"并行分支内的节点 {current} 是人工审批；请把审批节点移到并行之外",
                )
            pending.extend(edge["to"] for edge in outgoing[current])


# ── 图查询辅助 ───────────────────────────────────────────────


def node_by_id(definition: Dict[str, Any], node_id: str) -> Optional[Dict[str, Any]]:
    for node in definition.get("nodes", []):
        if node.get("id") == node_id:
            return node
    return None


def outgoing_edges(definition: Dict[str, Any], node_id: str) -> List[Dict[str, Any]]:
    return [edge for edge in definition.get("edges", []) if edge.get("from") == node_id]


def incoming_edges(definition: Dict[str, Any], node_id: str) -> List[Dict[str, Any]]:
    return [edge for edge in definition.get("edges", []) if edge.get("to") == node_id]


def start_node_id(definition: Dict[str, Any]) -> str:
    for node in definition.get("nodes", []):
        if node.get("type") == "start":
            return str(node["id"])
    raise DefinitionError("invalid_definition", "流程缺少 start 节点")


def resolve_field(context: Dict[str, Any], field: str) -> Any:
    """按点号路径取值（``steps.approve.outcome`` / ``input.topic``）。

    取不到返回 ``None`` —— 条件判定按"不成立"处理，绝不默认成成功。
    """
    current: Any = context
    for part in str(field or "").split("."):
        if not part:
            continue
        if isinstance(current, dict):
            current = current.get(part)
        elif isinstance(current, (list, tuple)):
            try:
                current = current[int(part)]
            except (ValueError, IndexError):
                return None
        else:
            return None
        if current is None:
            return None
    return current


def evaluate_condition(condition: Optional[Dict[str, Any]], context: Dict[str, Any]) -> bool:
    """判定一条连线条件；无条件 / 判定不了都按"不选"处理（由调用方兜底）。"""
    if not condition:
        return False
    actual = resolve_field(context, str(condition.get("field") or ""))
    expected = condition.get("value")
    op = str(condition.get("op") or "eq")
    if op == "eq":
        return actual == expected
    if op == "ne":
        return actual != expected
    if op == "in":
        if isinstance(expected, (list, tuple, set)):
            return actual in expected
        return False
    if op == "contains":
        if isinstance(actual, (list, tuple, set, str)):
            return expected in actual
        return False
    if op == "truthy":
        return bool(actual)
    if op == "falsy":
        return not bool(actual)
    return False


# ── 内置模板 ─────────────────────────────────────────────────


def builtin_submit_approve_execute_archive() -> Dict[str, Any]:
    """Boss 的标准流程：提交 → 审批 → 执行 → 归档。"""
    return {
        "schema_version": SCHEMA_VERSION,
        "name": "提交 → 审批 → 执行 → 归档",
        "description": "提交一件事，人工审批通过后由 AI 员工执行，结果归档备查。",
        "enabled": True,
        "variables": {"topic": "", "instruction": ""},
        "nodes": [
            {
                "id": "start",
                "type": "start",
                "name": "提交",
                "config": {},
                "position": {"x": 60, "y": 120},
            },
            {
                "id": "approve",
                "type": "approval",
                "name": "人工审批",
                "config": {
                    "title": "审批本次执行申请",
                    "operation": "提交并执行一个 AI 目标",
                    "reason": "执行前需要人工确认范围与影响。",
                    "approvers": [],
                    "timeout_seconds": 3600,
                    "on_timeout": "reject",
                },
                "position": {"x": 260, "y": 120},
            },
            {
                "id": "execute",
                "type": "agent",
                "name": "Agent 执行",
                "config": {
                    "instruction": "{{input.instruction}}",
                    "employee_name": "",
                    "scope": "L1",
                    "project_id": "",
                    "wait": True,
                    "timeout_seconds": 900,
                    "retry": {"max_attempts": 1, "backoff_seconds": 0},
                },
                "position": {"x": 460, "y": 120},
            },
            {
                "id": "notify",
                "type": "notify",
                "name": "通知",
                "config": {
                    "message": "流程「{{definition.name}}」执行完成",
                    "level": "info",
                    "channel": "event",
                },
                "position": {"x": 660, "y": 120},
            },
            {
                "id": "archive",
                "type": "archive",
                "name": "归档",
                "config": {"note": "结果与执行记录一并归档"},
                "position": {"x": 860, "y": 120},
            },
            {
                "id": "end",
                "type": "end",
                "name": "结束",
                "config": {},
                "position": {"x": 1060, "y": 120},
            },
        ],
        "edges": [
            {"id": "e1", "from": "start", "to": "approve", "condition": None},
            {"id": "e2", "from": "approve", "to": "execute", "condition": None},
            {"id": "e3", "from": "execute", "to": "notify", "condition": None},
            {"id": "e4", "from": "notify", "to": "archive", "condition": None},
            {"id": "e5", "from": "archive", "to": "end", "condition": None},
        ],
        "triggers": [{"type": "manual"}],
    }
