"""Prompt templates for the LLM planning engine.

All prompts are designed to produce structured, parseable output that
maps cleanly onto the Execution Kernel's Task / Plan data model.

Design principles:
  1. **Output is JSON, always.** No prose, no markdown code fences.
     The parser expects valid JSON and will reject anything else.
  2. **Tasks map to capabilities.** Every task MUST specify a
     ``capability_id`` that exists in the capability registry.
  3. **Dependencies are explicit.** Use ``depends_on`` to declare
     task ordering — the planner builds the DAG, not the executor.
  4. **Honesty over completeness.** If the goal is unclear or the
     planner doesn't know how to achieve it, return an empty task
     list with a ``clarification_needed`` flag — never invent
     capabilities the system doesn't have.
  5. **Safety constraints are baked in.** The prompt includes the
     scope and capability limits so the LLM cannot plan beyond
     what is authorised.
"""
from __future__ import annotations

from typing import List, Dict, Any, Optional


# ---------------------------------------------------------------------------
# System prompt — the planner's identity and constraints
# ---------------------------------------------------------------------------

SYSTEM_PROMPT = """你是鎏灏（LIUHAO）AI 操作系统的智能规划引擎。

你的职责是将用户的自然语言目标分解为结构化的任务计划。
你必须严格遵守以下规则：

## 核心原则
1. **只规划系统具备的能力** — 每个任务必须对应一个真实存在的 capability_id
2. **诚实优先** — 如果你不确定如何完成目标，明确说明需要澄清，绝不要编造
3. **安全合规** — 绝不能规划违反安全策略或超出权限范围的任务
4. **可验证** — 每个任务都应该有明确的完成标准

## 输出格式
你必须输出严格的 JSON，不要有任何额外文字、解释或 markdown 标记。

JSON 结构：
{
  "tasks": [
    {
      "id": "唯一任务ID",
      "name": "任务名称",
      "description": "任务描述",
      "capability_id": "对应的能力ID",
      "inputs": {"参数名": "参数值"},
      "depends_on": ["依赖的任务ID列表"],
      "verification_criteria": "验证标准"
    }
  ],
  "clarification_needed": false,
  "clarification_reason": "",
  "plan_summary": "计划概述",
  "estimated_complexity": "low|medium|high",
  "parallelizable": true
}

## 任务类型说明
- WriteFile: 写入文件，inputs 需要 path 和 content
- ReadFile: 读取文件，inputs 需要 path
- ExecuteCode: 执行代码，inputs 需要 code 或 expression
- WebSearch: 网络搜索，inputs 需要 query
- Plan: 子规划，inputs 需要 sub_goal
- Compute: 计算任务，inputs 需要 expression
- SendMessage: 发送消息，inputs 需要 recipient 和 content
- AuditCheck: 审计检查
- Verify: 验证任务结果
"""


# ---------------------------------------------------------------------------
# Capability context prompt — tells the LLM what the system CAN do
# ---------------------------------------------------------------------------

def build_capability_context(capabilities: List[Dict[str, Any]]) -> str:
    """Build the capability listing section of the prompt.

    Args:
        capabilities: List of capability dicts with id, name, description, inputs_schema

    Returns:
        Formatted capability listing for the prompt
    """
    lines = ["## 系统可用能力清单", ""]
    for cap in capabilities:
        cap_id = cap.get("id", "unknown")
        name = cap.get("name", cap_id)
        desc = cap.get("description", "")
        inputs_schema = cap.get("inputs_schema", {})

        lines.append(f"### {cap_id} — {name}")
        lines.append(f"描述: {desc}")
        if inputs_schema:
            lines.append("输入参数:")
            for param_name, param_info in inputs_schema.items():
                param_type = param_info.get("type", "any")
                param_required = param_info.get("required", False)
                param_desc = param_info.get("description", "")
                required_mark = "（必填）" if param_required else ""
                lines.append(f"  - {param_name}: {param_type}{required_mark} — {param_desc}")
        lines.append("")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Goal decomposition prompt — the main planning request
# ---------------------------------------------------------------------------

def build_decomposition_prompt(
    goal_text: str,
    scope: str,
    capabilities: List[Dict[str, Any]],
    context: Optional[Dict[str, Any]] = None,
    history: Optional[List[Dict[str, Any]]] = None,
) -> List[Dict[str, str]]:
    """Build the full message list for goal decomposition.

    Args:
        goal_text: The user's natural language goal
        scope: The authorisation scope (L1 / L2 / L3)
        capabilities: Available capabilities for this scope
        context: Additional context (memory, profile, etc.)
        history: Previous task executions / conversation history

    Returns:
        List of message dicts ready for the LLM chat API
    """
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
    ]

    # Add capability context as a user message so it stays fresh
    cap_context = build_capability_context(capabilities)
    messages.append({"role": "user", "content": cap_context})
    messages.append({
        "role": "assistant",
        "content": "已了解系统能力清单。请告诉我你的目标。"
    })

    # Add scope constraint
    scope_msg = f"当前权限范围: {scope}\n你只能规划在 {scope} 范围内允许的任务。"
    messages.append({"role": "user", "content": scope_msg})
    messages.append({
        "role": "assistant",
        "content": f"了解。我将严格在 {scope} 权限范围内规划任务。"
    })

    # Add history if provided
    if history:
        history_lines = ["## 历史对话", ""]
        for i, h in enumerate(history[-5:], 1):  # Last 5 turns
            role = h.get("role", "unknown")
            content = h.get("content", "")[:500]  # Truncate
            history_lines.append(f"{i}. [{role}]: {content}")
        history_lines.append("")
        messages.append({"role": "user", "content": "\n".join(history_lines)})
        messages.append({
            "role": "assistant",
            "content": "已了解历史上下文。"
        })

    # Add memory context if provided
    if context and context.get("relevant_memories"):
        memories = context["relevant_memories"]
        mem_lines = ["## 相关记忆", ""]
        for i, mem in enumerate(memories[:5], 1):
            mem_lines.append(f"{i}. {mem.get('content', mem)}")
        mem_lines.append("")
        messages.append({"role": "user", "content": "\n".join(mem_lines)})
        messages.append({
            "role": "assistant",
            "content": "已考虑相关记忆，将在规划中应用。"
        })

    # The actual goal — the main user request
    goal_msg = f"""## 当前目标

请将以下目标分解为结构化任务计划：

目标: {goal_text}

请直接输出 JSON 格式的计划。"""

    messages.append({"role": "user", "content": goal_msg})

    return messages


# ---------------------------------------------------------------------------
# Re-planning prompt — used when verification fails and we need to adjust
# ---------------------------------------------------------------------------

def build_replanning_prompt(
    goal_text: str,
    original_plan: Dict[str, Any],
    failed_tasks: List[Dict[str, Any]],
    completed_tasks: List[Dict[str, Any]],
    error_summary: str,
) -> List[Dict[str, str]]:
    """Build a re-planning prompt based on execution feedback.

    Args:
        goal_text: The original goal
        original_plan: The plan that was executed
        failed_tasks: Tasks that failed
        completed_tasks: Tasks that succeeded
        error_summary: Summary of what went wrong

    Returns:
        Message list for the LLM
    """
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
    ]

    replan_msg = f"""## 重新规划请求

原始目标: {goal_text}

### 已完成的任务
{_format_tasks_brief(completed_tasks)}

### 失败的任务
{_format_tasks_brief(failed_tasks)}

### 错误摘要
{error_summary}

请根据执行结果重新规划剩余的任务。
注意：
1. 已完成的任务不需要重复执行
2. 分析失败原因，调整后续任务策略
3. 如果目标已无法完成，明确说明原因

请输出重新规划后的 JSON 任务列表。"""

    messages.append({"role": "user", "content": replan_msg})
    return messages


def _format_tasks_brief(tasks: List[Dict[str, Any]]) -> str:
    """Format a task list for the prompt."""
    if not tasks:
        return "（无）"
    lines = []
    for i, task in enumerate(tasks, 1):
        name = task.get("name", task.get("id", f"task_{i}"))
        status = task.get("status", "unknown")
        error = task.get("error", "")
        line = f"{i}. {name} [{status}]"
        if error:
            line += f" — {error[:100]}"
        lines.append(line)
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Strategy selection prompt — pick the best planning approach
# ---------------------------------------------------------------------------

STRATEGY_SELECTION_PROMPT = """根据目标特征，选择最合适的规划策略：

## 可用策略
1. **direct** — 直接执行：简单、明确的单步任务
2. **linear** — 线性规划：有明确先后顺序的多步任务
3. **parallel** — 并行规划：可同时执行的独立任务
4. **hierarchical** — 层次规划：复杂大目标，需要先分解为子目标
5. **exploratory** — 探索式规划：目标不明确，需要先调研再规划
6. **iterative** — 迭代规划：需要多轮试错和调整的任务

## 输出格式
{
  "strategy": "策略名称",
  "confidence": 0.0-1.0,
  "reason": "选择理由"
}

目标特征：
- 复杂度: {complexity_hint}
- 明确度: {clarity_hint}
- 任务数量预估: {task_count_hint}

目标: {goal_text}

请输出策略选择 JSON。"""


def build_strategy_selection_prompt(
    goal_text: str,
    complexity_hint: str = "unknown",
    clarity_hint: str = "unknown",
    task_count_hint: str = "unknown",
) -> List[Dict[str, str]]:
    """Build the strategy selection prompt."""
    content = STRATEGY_SELECTION_PROMPT.format(
        goal_text=goal_text,
        complexity_hint=complexity_hint,
        clarity_hint=clarity_hint,
        task_count_hint=task_count_hint,
    )
    return [
        {"role": "system", "content": "你是一个规划策略选择器。"},
        {"role": "user", "content": content},
    ]
