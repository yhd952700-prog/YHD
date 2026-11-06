"""LLM-backed goal decomposition with a deterministic, never-empty fallback.

The deterministic :class:`~src.kernels.execution.GoalDecomposer` is keyword
matching. That is honest but narrow: a perfectly ordinary Chinese sentence such
as "帮我查一下今天有什么值得关注的 AI 开源项目，然后整理成三条要点" matches no
English keyword and used to fall through to a single ``execution_pipeline``
task that nothing could execute.

This layer asks the *configured default model* for a real plan, validates it
against the capabilities that actually have executors, and only accepts it if
it is executable. On timeout / missing model / malformed JSON / unknown
capability it falls back to the existing keyword decomposer -- so the user
always gets either executable work or an explicit, readable failure, never a
silent "0 tasks".
"""
from __future__ import annotations

import logging
import uuid
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence, Set

from src.kernels.execution import Goal, Task

from .llm_support import (
    default_generate,
    generate_with_timeout,
    model_timeout_seconds,
    parse_json_payload,
)

logger = logging.getLogger("liuhao.ai.llm_decomposer")

#: Capabilities this layer may propose when the registry cannot be introspected.
#: Mirrors the capabilities the local tool set can execute today; the live
#: registry wins whenever it is available.
KNOWN_CAPABILITIES: tuple = (
    "file_write",
    "python_compute",
    "network_bus",
    "execution_pipeline",
)

#: Capabilities that call the model to generate prose. A timeout there is not a
#: transient blip, so the engine must not retry it three times (each retry costs
#: another full prose budget). One retry keeps a genuinely transient failure
#: recoverable without multiplying a slow model into minutes.
PROSE_CAPABILITIES = frozenset({"network_bus", "execution_pipeline"})

#: Input keys the executor of each capability can actually consume. The model is
#: told about them, and the plan is rejected (=> keyword fallback) when a task
#: cannot satisfy its own executor.
CAPABILITY_INPUT_CONTRACT: Dict[str, tuple] = {
    "file_write": ("path", "content"),
    "python_compute": ("code", "expression"),
    "network_bus": ("query", "goal", "instruction"),
    "execution_pipeline": ("instruction", "goal", "query"),
}

MAX_TASKS = 8


class LLMTaskDecomposer:
    """Wrap a deterministic decomposer with a model-first planning attempt.

    ``inner`` stays the single fallback source of truth. ``agent_ids`` is proxied
    in both directions so the gateway's existing injection keeps working through
    any number of wrappers.
    """

    def __init__(
        self,
        inner: Any,
        generator: Optional[Callable[[str], str]] = None,
        capability_provider: Optional[Callable[[], Iterable[str]]] = None,
        timeout_seconds: Optional[float] = None,
        max_tasks: int = MAX_TASKS,
    ) -> None:
        self._inner = inner
        self._generator = generator or default_generate
        self._capability_provider = capability_provider
        self._timeout = timeout_seconds
        self._max_tasks = max(1, int(max_tasks))
        # Decomposition is deterministic per (goal id, text); caching it keeps a
        # retry on the *same* goal using the *same* task names, which is what
        # the gateway's single-task retry matches on. Bounded to avoid growth.
        self._cache: Dict[tuple, List[Dict[str, Any]]] = {}
        self._cache_order: List[tuple] = []
        self._only_task_name: Optional[str] = None

    # -- proxies -------------------------------------------------------------
    @property
    def agent_ids(self) -> Any:
        return getattr(self._inner, "agent_ids", None)

    @agent_ids.setter
    def agent_ids(self, value: Any) -> None:
        setattr(self._inner, "agent_ids", value)

    @property
    def only_task_name(self) -> Optional[str]:
        return self._only_task_name

    @only_task_name.setter
    def only_task_name(self, value: Optional[str]) -> None:
        self._only_task_name = value

    def __getattr__(self, name: str) -> Any:
        return getattr(self._inner, name)

    # -- decomposition -------------------------------------------------------
    def decompose(self, goal: Goal) -> List[Task]:
        key = (str(getattr(goal, "id", "")), str(getattr(goal, "natural_language", "")))

        cached = self._cache.get(key)
        if cached is not None:
            return self._materialize(goal, cached)

        # A single-task retry cannot tolerate a *fresh* plan with different task
        # names (the gateway retries by name). If this process has no cache for
        # the goal -- e.g. it restarted after a crash -- use the deterministic
        # path, whose names are reproducible.
        if self._only_task_name:
            return self._inner.decompose(goal)

        # Deterministic plans that are already fully executable are authoritative.
        # A goal that explicitly names a file / computation must keep its exact
        # tool-shaped tasks: letting the model "improve" such a plan was how an
        # explicit `write ... to notes/retry.txt` turned into a model-only
        # synthesis that never touched the disk. The model is consulted only
        # when the keyword plan is empty, needs a capability nothing can run,
        # or is a single abstract step (the case that used to produce 0 tasks).
        fallback = self._inner.decompose(goal)
        allowed = self._allowed_capabilities()
        if self._deterministic_plan_is_executable(fallback, allowed):
            return fallback

        try:
            tasks = self._plan_with_model(goal, allowed)
            if not tasks:
                raise ValueError("模型没有给出任何可执行子任务")
        except Exception as exc:  # noqa: BLE001 - fallback is the contract
            logger.warning(
                "LLM decomposition unavailable (%s: %s); falling back to keyword planner",
                type(exc).__name__,
                exc,
            )
            return fallback

        snapshot = [self._snapshot(task) for task in tasks]
        self._store_cache(key, snapshot)
        return self._materialize(goal, snapshot)

    @staticmethod
    def _deterministic_plan_is_executable(
        tasks: Sequence[Task], allowed: Set[str]
    ) -> bool:
        """Whether the keyword plan can be executed as-is.

        Requires BOTH: every capability has a real executor, AND at least one
        task uses a concrete tool (not merely the two prose capabilities). A
        plan consisting only of ``network_bus``/``execution_pipeline`` is
        exactly the shape the model exists to improve on, so it is not final.
        """
        if not tasks:
            return False
        if any(str(task.capability_id) not in allowed for task in tasks):
            return False
        return any(str(task.capability_id) not in PROSE_CAPABILITIES for task in tasks)

    def _plan_with_model(self, goal: Goal, allowed: Optional[Set[str]] = None) -> List[Task]:
        allowed = allowed or self._allowed_capabilities()
        prompt = self._build_prompt(goal, allowed)
        raw = generate_with_timeout(
            self._generator,
            prompt,
            timeout=self._timeout if self._timeout is not None else model_timeout_seconds(),
        )
        payload = parse_json_payload(raw)
        specs = self._normalize_specs(payload)
        # Agent assignment happens in ``_materialize`` (the single place every
        # path materializes tasks from), so it is not repeated here.
        return self._build_tasks(goal, specs, allowed)

    def _allowed_capabilities(self) -> Set[str]:
        if self._capability_provider is None:
            return set(KNOWN_CAPABILITIES)
        try:
            available = set(self._capability_provider() or ())
        except Exception as exc:  # noqa: BLE001 - introspection failure
            logger.warning("capability introspection failed: %s", exc)
            available = set()
        # The registry is authoritative when non-empty; otherwise fall back to
        # the capabilities this local tool set is shipped with.
        return available or set(KNOWN_CAPABILITIES)

    def _build_prompt(self, goal: Goal, allowed: Set[str]) -> str:
        capabilities = "、".join(sorted(allowed))
        contract_lines = []
        for cap in sorted(allowed):
            required = CAPABILITY_INPUT_CONTRACT.get(cap)
            if required:
                contract_lines.append(f"- {cap}: 输入必须包含 {', '.join(required)} 之一")
            else:
                contract_lines.append(f"- {cap}: 输入按该能力要求填写")
        return (
            "你是「鎏灏管家」的任务拆解器。把用户目标拆成 1-"
            f"{self._max_tasks} 个可执行子任务，只输出一个 JSON 数组，不要任何解释。\n"
            "每个任务对象字段：\n"
            '- name: 简短中文任务名（≤20 字，必须唯一）\n'
            '- description: 一句话说明任务要产出什么\n'
            f"- capability: 只能从以下列表选择：{capabilities}\n"
            "- inputs: 对象，必须满足该能力的输入约定\n"
            '- depends_on: 依赖的任务 name 数组；没有依赖写 []\n\n'
            "能力输入约定：\n"
            + "\n".join(contract_lines)
            + "\n\n规则：\n"
            "1. 先调研、后整理的任务，整理任务的 depends_on 必须包含调研任务的 name。\n"
            "2. 相互独立、互不等待的任务不要写 depends_on —— 它们会并行执行。\n"
            "3. 不要编造用户没有要求的能力或任务。\n"
            "4. 目标只需要一步时就只给一个任务；不要为了凑数拆开。\n"
            "5. 调研/搜索/查询用 network_bus；整理/汇总/写作/出要点用 execution_pipeline。\n\n"
            f"用户目标：{goal.natural_language}"
        )

    def _normalize_specs(self, payload: Any) -> List[Dict[str, Any]]:
        if isinstance(payload, dict):
            for key in ("tasks", "subtasks", "plan", "steps"):
                if isinstance(payload.get(key), list):
                    payload = payload[key]
                    break
        if not isinstance(payload, list):
            raise ValueError("模型输出不是任务数组")

        specs: List[Dict[str, Any]] = []
        for item in payload[: self._max_tasks]:
            if not isinstance(item, dict):
                raise ValueError("任务数组里包含非对象元素")
            name = str(item.get("name") or item.get("title") or "").strip()
            capability = str(
                item.get("capability") or item.get("capability_id") or ""
            ).strip()
            description = str(item.get("description") or name).strip()
            inputs = item.get("inputs") or {}
            depends_on = item.get("depends_on", item.get("dependencies", []))
            if not name or not capability:
                raise ValueError("任务缺少 name 或 capability")
            if not isinstance(inputs, dict):
                raise ValueError(f"任务 {name} 的 inputs 不是对象")
            if isinstance(depends_on, (str, int)):
                depends_on = [depends_on]
            if not isinstance(depends_on, list):
                raise ValueError(f"任务 {name} 的 depends_on 不是数组")
            specs.append(
                {
                    "name": name,
                    "description": description,
                    "capability": capability,
                    "inputs": dict(inputs),
                    "depends_on": list(depends_on),
                }
            )
        return specs

    def _build_tasks(
        self,
        goal: Goal,
        specs: Sequence[Dict[str, Any]],
        allowed: Set[str],
    ) -> List[Task]:
        tasks: List[Task] = []
        used_names: Set[str] = set()
        for spec in specs:
            capability = str(spec["capability"])
            if capability not in allowed:
                # Unknown capability => reject the whole model plan and use the
                # deterministic one. Accepting it would recreate the phantom-task
                # defect this change exists to remove.
                raise ValueError(f"模型提出了没有执行器的能力：{capability}")
            required = CAPABILITY_INPUT_CONTRACT.get(capability) or ()
            inputs = dict(spec.get("inputs") or {})
            if required and not any(inputs.get(key) for key in required):
                raise ValueError(f"任务 {spec['name']} 缺少能力 {capability} 的必需输入")

            name = self._unique_name(str(spec["name"]), used_names)
            used_names.add(name)
            tasks.append(
                Task(
                    id=str(uuid.uuid4())[:8],
                    goal_id=goal.id,
                    name=name,
                    description=str(spec.get("description") or name),
                    capability_id=capability,
                    inputs=inputs,
                    scope=goal.scope,
                    max_retries=1 if capability in PROSE_CAPABILITIES else 3,
                )
            )

        # Resolve dependencies by exact name or by (0- or 1-based) index.
        by_name = {task.name: task for task in tasks}
        for index, (task, spec) in enumerate(zip(tasks, specs)):
            for reference in spec.get("depends_on") or []:
                dependency: Optional[Task] = None
                if isinstance(reference, int):
                    if 0 <= reference < len(tasks):
                        dependency = tasks[reference]
                    elif 1 <= reference <= len(tasks):
                        dependency = tasks[reference - 1]
                else:
                    label = str(reference).strip()
                    dependency = by_name.get(label)
                    if dependency is None and label.isdigit():
                        position = int(label)
                        if 0 <= position < len(tasks):
                            dependency = tasks[position]
                        elif 1 <= position <= len(tasks):
                            dependency = tasks[position - 1]
                if dependency is None or dependency is task:
                    logger.debug(
                        "LLM plan dependency %r of task %r could not be resolved",
                        reference,
                        task.name,
                    )
                    continue
                if dependency.id not in task.dependencies:
                    task.dependencies.append(dependency.id)
        return tasks

    @staticmethod
    def _unique_name(name: str, used: Set[str]) -> str:
        candidate = name[:40]
        suffix = 2
        while candidate in used:
            candidate = f"{name[:38]}#{suffix}"
            suffix += 1
        return candidate

    def _store_cache(self, key: tuple, snapshot: List[Dict[str, Any]]) -> None:
        if key not in self._cache:
            self._cache_order.append(key)
        self._cache[key] = snapshot
        while len(self._cache_order) > 64:
            oldest = self._cache_order.pop(0)
            self._cache.pop(oldest, None)

    @staticmethod
    def _snapshot(task: Task) -> Dict[str, Any]:
        return {
            "id": task.id,
            "name": task.name,
            "description": task.description,
            "capability": task.capability_id,
            "inputs": dict(task.inputs),
            "dependencies": list(task.dependencies),
            "max_retries": task.max_retries,
        }

    def _materialize(
        self, goal: Goal, snapshot: List[Dict[str, Any]]
    ) -> List[Task]:
        tasks = [
            Task(
                id=item["id"],
                goal_id=goal.id,
                name=item["name"],
                description=item["description"],
                capability_id=item["capability"],
                inputs=dict(item["inputs"]),
                dependencies=list(item["dependencies"]),
                scope=goal.scope,
                max_retries=item.get("max_retries", 3),
            )
            for item in snapshot
        ]
        assign = getattr(self._inner, "_assign_agents", None)
        if callable(assign):
            assign(goal, tasks)
        return tasks


__all__ = ["LLMTaskDecomposer", "KNOWN_CAPABILITIES", "CAPABILITY_INPUT_CONTRACT"]
