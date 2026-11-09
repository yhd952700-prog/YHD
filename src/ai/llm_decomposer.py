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
import re
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
    "preview_start",
    "preview_stop",
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
    # 预览的静态根在工具注册时已固定，因此没有必填输入；``entry`` 只是
    # 工作区内可选的页面路径。``preview_stop`` 不填 handle 表示停止全部。
    "preview_start": (),
    "preview_stop": (),
}

MAX_TASKS = 8

#: 日志里出现的密钥形态。拒绝路径要把模型 raw 留痕，但绝不能把 Key 写进
#: 日志——诊断价值不能换来一次凭据泄露。
_SECRET_PATTERN = re.compile(
    r"(sk-[A-Za-z0-9_\-]{4,}|Bearer\s+[A-Za-z0-9._\-]+)",
    re.IGNORECASE,
)


def _redact_raw(raw: Any, limit_bytes: int = 4096) -> str:
    """把模型原始产出压成单行日志片段：先脱敏，再按 UTF-8 截断到 ≤4KB。"""
    text = str(raw or "")
    text = _SECRET_PATTERN.sub("[REDACTED]", text)
    encoded = text.encode("utf-8")
    if len(encoded) > limit_bytes:
        text = encoded[:limit_bytes].decode("utf-8", errors="ignore") + "…[truncated]"
    return " ".join(text.split())


class _PlanAttemptError(ValueError):
    """一次模型拆解尝试的失败，携带原始产出供拒绝日志留痕。"""

    def __init__(self, message: str, raw: str = "") -> None:
        super().__init__(message)
        self.raw = raw


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
        employee_provider: Optional[Callable[[], Sequence[Dict[str, Any]]]] = None,
    ) -> None:
        self._inner = inner
        self._generator = generator or default_generate
        self._capability_provider = capability_provider
        self._timeout = timeout_seconds
        self._max_tasks = max(1, int(max_tasks))
        #: 可选的花名册提供者：返回 ``[{name, role, capabilities, agent_ids}]``。
        #: 管家自己挑人时只能从这份真实花名册里选，避免模型编造员工。
        self._employee_provider = employee_provider
        # Decomposition is deterministic per (goal id, text); caching it keeps a
        # retry on the *same* goal using the *same* task names, which is what
        # the gateway's single-task retry matches on. Bounded to avoid growth.
        self._cache: Dict[tuple, List[Dict[str, Any]]] = {}
        self._cache_order: List[tuple] = []
        self._cache_source: Dict[tuple, str] = {}
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

    def set_employee_provider(
        self, provider: Optional[Callable[[], Sequence[Dict[str, Any]]]]
    ) -> None:
        """替换真实员工花名册（雇佣/停用后由网关刷新）。"""
        self._employee_provider = provider

    # -- decomposition -------------------------------------------------------
    def decompose(self, goal: Goal) -> List[Task]:
        key = (str(getattr(goal, "id", "")), str(getattr(goal, "natural_language", "")))

        cached = self._cache.get(key)
        if cached is not None:
            source = self._cache_source.get(key)
            if source:
                goal.metadata["decomposition_source"] = source
            return self._apply_forced_assignment(
                goal, self._materialize(goal, cached)
            )

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
            goal.metadata["decomposition_source"] = "keyword"
            return self._apply_forced_assignment(goal, fallback)

        try:
            tasks = self._plan_with_model(goal, allowed)
        except _PlanAttemptError as exc:
            # 模型确实回答了，但计划没过契约。这不是"模型不可用"，而是"计划
            # 需要修一次"：把校验错误原文回喂，只允许修 inputs，不重排任务。
            logger.warning(
                "evt=decompose.rejected attempt=1 goal=%s error=%s raw=%s",
                getattr(goal, "id", ""),
                exc,
                _redact_raw(exc.raw),
            )
            try:
                tasks = self._plan_with_model(
                    goal,
                    allowed,
                    retry_error=str(exc),
                    retry_plan=exc.raw,
                )
            except Exception as retry_exc:  # noqa: BLE001 - fallback is the contract
                logger.warning(
                    "evt=decompose.rejected attempt=2 goal=%s error=%s raw=%s",
                    getattr(goal, "id", ""),
                    retry_exc,
                    _redact_raw(getattr(retry_exc, "raw", "")),
                )
                logger.warning(
                    "LLM decomposition unavailable (%s: %s); falling back to keyword planner",
                    type(retry_exc).__name__,
                    retry_exc,
                )
                goal.metadata["decomposition_source"] = "keyword"
                return self._apply_forced_assignment(goal, fallback)
            logger.info(
                "evt=decompose.retry_succeeded attempt=2 goal=%s",
                getattr(goal, "id", ""),
            )
            goal.metadata["decomposition_source"] = "model_retry"
        except Exception as exc:  # noqa: BLE001 - fallback is the contract
            # Timeout / no model / transport error: retrying costs another full
            # prose budget, so degrade immediately.
            logger.warning(
                "LLM decomposition unavailable (%s: %s); falling back to keyword planner",
                type(exc).__name__,
                exc,
            )
            goal.metadata["decomposition_source"] = "keyword"
            return self._apply_forced_assignment(goal, fallback)
        else:
            goal.metadata["decomposition_source"] = "model"

        snapshot = [self._snapshot(task) for task in tasks]
        self._store_cache(
            key, snapshot, goal.metadata.get("decomposition_source") or "model"
        )
        return self._apply_forced_assignment(
            goal, self._materialize(goal, snapshot)
        )

    @staticmethod
    def _deterministic_plan_is_executable(
        tasks: Sequence[Task], allowed: Set[str]
    ) -> bool:
        """Whether the keyword plan can be executed as-is.

        Requires ALL of: every capability has a real executor, every task can
        satisfy its executor's input contract, AND at least one task uses a
        concrete tool (not merely the two prose capabilities). A plan consisting
        only of ``network_bus``/``execution_pipeline`` is exactly the shape the
        model exists to improve on, so it is not final.

        The input-contract check matters because a keyword plan can name a real
        capability while supplying inputs its executor cannot consume: a goal
        mentioning "Python" grew a ``python_compute`` task whose only input was
        ``{"goal": ...}``. That plan used to be adopted as "executable" and then
        rejected by the planner contract -- failing the whole goal before the
        model was ever consulted.
        """
        if not tasks:
            return False
        for task in tasks:
            capability = str(task.capability_id)
            if capability not in allowed:
                return False
            required = CAPABILITY_INPUT_CONTRACT.get(capability) or ()
            inputs = getattr(task, "inputs", None) or {}
            if required and not any(inputs.get(key) for key in required):
                return False
        return any(str(task.capability_id) not in PROSE_CAPABILITIES for task in tasks)

    def _plan_with_model(
        self,
        goal: Goal,
        allowed: Optional[Set[str]] = None,
        retry_error: Optional[str] = None,
        retry_plan: Optional[str] = None,
    ) -> List[Task]:
        allowed = allowed or self._allowed_capabilities()
        prompt = self._build_prompt(
            goal,
            allowed,
            retry_error=retry_error,
            retry_plan=retry_plan,
        )
        raw = generate_with_timeout(
            self._generator,
            prompt,
            timeout=self._timeout if self._timeout is not None else model_timeout_seconds(),
        )
        try:
            payload = parse_json_payload(raw)
            specs = self._normalize_specs(payload)
            # Agent assignment happens in ``_materialize`` (the single place every
            # path materializes tasks from), so it is not repeated here.
            tasks = self._build_tasks(goal, specs, allowed)
            if not tasks:
                raise ValueError("模型没有给出任何可执行子任务")
            return tasks
        except Exception as exc:  # noqa: BLE001 - carry raw for the rejection log
            raise _PlanAttemptError(str(exc), raw=raw) from exc

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

    def _build_prompt(
        self,
        goal: Goal,
        allowed: Set[str],
        retry_error: Optional[str] = None,
        retry_plan: Optional[str] = None,
    ) -> str:
        capabilities = "、".join(sorted(allowed))
        contract_lines = []
        for cap in sorted(allowed):
            required = CAPABILITY_INPUT_CONTRACT.get(cap)
            if required:
                contract_lines.append(f"- {cap}: 输入必须包含 {', '.join(required)} 之一")
            else:
                contract_lines.append(f"- {cap}: 输入按该能力要求填写")
        roster = self._employee_roster()
        if roster:
            roster_lines = [
                "- {name}{role}，默认能力：{caps}".format(
                    name=employee["name"],
                    role=f"，职责：{employee['role']}" if employee["role"] else "",
                    caps="、".join(employee["capabilities"]) or "未限定专长",
                )
                for employee in roster
            ]
            roster_block = (
                "\n可指派员工（assign_to 只能填这里出现的名字；没有合适的人就写空字符串）：\n"
                + "\n".join(roster_lines)
            )
            assign_fields = (
                '- assign_to: 执行该任务的员工名（必须来自上面的员工列表；没有合适的人写 ""）\n'
                '- assign_reason: 一句话说明为什么派给这个员工（他的能力/职责如何匹配该任务）；'
                'assign_to 为空时也写 ""\n'
            )
        else:
            roster_block = ""
            assign_fields = ""

        return (
            "你是「鎏灏管家」的任务拆解器。把用户目标拆成 1-"
            f"{self._max_tasks} 个可执行子任务，只输出一个 JSON 数组，不要任何解释。\n"
            "每个任务对象字段：\n"
            '- name: 简短中文任务名（≤20 字，必须唯一）\n'
            '- description: 一句话说明任务要产出什么\n'
            f"- capability: 只能从以下列表选择：{capabilities}\n"
            "- inputs: 对象，必须满足该能力的输入约定\n"
            '- depends_on: 依赖的任务 name 数组；没有依赖写 []\n'
            + assign_fields
            + "\n能力输入约定：\n"
            + "\n".join(contract_lines)
            + roster_block
            + "\n\n规则：\n"
            "1. 一句话里如果有多件不同的事（例如「改首页 + 写宣传文案」），必须拆成多个任务，"
            "每个任务只做一件可独立验收的事；不要把多件事合并成一条待办。\n"
            "2. 用户没有指定由谁做时，由你从员工列表里挑最合适的人，并在 assign_reason 写清理由；"
            "用户指定了人时，assign_to 必须用他，assign_reason 写「用户指定」。\n"
            "3. 先调研、后整理的任务，整理任务的 depends_on 必须包含调研任务的 name。\n"
            "4. 相互独立、互不等待的任务不要写 depends_on —— 它们会并行执行。\n"
            "5. 不要编造用户没有要求的能力或任务。\n"
            "6. 目标只需要一步时就只给一个任务；不要为了凑数拆开。\n"
            "7. 调研/搜索/查询用 network_bus；整理/汇总/写作/出要点用 execution_pipeline。\n"
            "8. python_compute 只在用户明确给出代码或算式时使用；只是提到 Python  "
            "（例如「查 Python 新特性」）时不要生成计算任务。\n"
            "9. file_write 的 inputs.content 必须是要落盘的完整正文（例如完整 HTML），"
            "不能只写「某某的代码」这类占位说明；path 用相对路径。\n"
            "10. 交付可在浏览器打开的网页（HTML/CSS/JS）时，必须在 file_write 任务之后"
            "增加一个 preview_start 任务：depends_on 包含写入任务名，inputs 填 "
            "{\"entry\": \"刚写入页面的相对路径\"}。它会在工作区起本地静态预览，"
            "并把真实可访问性自检结果（HTTP 状态、响应字节、SHA256、页面标题）回传。\n"
            "11. preview_start 只读工作区、不执行任何命令、不出网；它只接受工作区内的"
            "相对入口路径。预览结束用 preview_stop（不填 handle 表示停止全部）。\n\n"
            + (
                "上一次输出的计划被校验拒绝，错误原文："
                f"{retry_error}\n"
                + (
                    f"上一次计划（只允许改 inputs）：{_redact_raw(retry_plan, 8192)}\n"
                    if retry_plan
                    else ""
                )
                + "请只修 inputs，不要改任务结构（不要增删任务，也不要改 name / "
                "capability / depends_on）；仍然只输出同一个 JSON 数组，不要解释。\n\n"
                if retry_error
                else ""
            )
            + f"用户目标：{goal.natural_language}"
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
            description_text = str(item.get("description") or "").strip()
            description = description_text or name
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
                    "description_from_model": bool(description_text),
                    "capability": capability,
                    "inputs": dict(inputs),
                    "depends_on": list(depends_on),
                    "assign_to": str(
                        item.get("assign_to") or item.get("assignee") or ""
                    ).strip(),
                    "assign_reason": str(
                        item.get("assign_reason") or item.get("reason") or ""
                    ).strip(),
                }
            )
        return specs

    # -- 员工指派 -------------------------------------------------------------
    def _employee_roster(self) -> List[Dict[str, Any]]:
        """真实可指派员工（名称、职责、默认能力、真实 agent id）。"""
        if self._employee_provider is None:
            return []
        try:
            raw = self._employee_provider() or ()
        except Exception as exc:  # noqa: BLE001 - 花名册不可用时不指派
            logger.warning("employee roster unavailable: %s", exc)
            return []
        roster: List[Dict[str, Any]] = []
        for item in raw:
            if not isinstance(item, dict):
                continue
            name = str(item.get("name") or "").strip()
            agents = [
                str(agent) for agent in (item.get("agent_ids") or []) if str(agent).strip()
            ]
            if not name or not agents:
                continue
            roster.append(
                {
                    "name": name,
                    "role": str(item.get("role") or "").strip()[:60],
                    "capabilities": [
                        str(cap)
                        for cap in (item.get("capabilities") or [])
                        if str(cap).strip()
                    ],
                    "agent_ids": agents,
                }
            )
        return roster

    @staticmethod
    def _clean_reason(reason: str) -> str:
        cleaned = " ".join(str(reason or "").split()).strip().strip("。；;")
        return cleaned[:120]

    @classmethod
    def _choose_assignment(
        cls,
        capability: str,
        requested_name: str,
        requested_reason: str,
        roster: Sequence[Dict[str, Any]],
    ) -> Optional[tuple]:
        """把任务落到一个真实花名册员工上，并给出可核对的理由。

        模型给的名字只有在花名册里、且其默认能力与任务能力相容时才采纳；
        否则由管家按能力匹配重新挑人。理由永远对应真实数据，不是编的。
        """
        if not roster:
            return None
        by_name = {employee["name"]: employee for employee in roster}
        requested = by_name.get(str(requested_name or "").strip())
        if requested is not None and (
            not requested["capabilities"] or capability in requested["capabilities"]
        ):
            reason = cls._clean_reason(requested_reason) or (
                f"默认能力包含 {capability}，与该任务匹配"
                if requested["capabilities"]
                else "未限定专长，按任务分工承接"
            )
            return requested, reason

        matched = [
            employee for employee in roster if capability in employee["capabilities"]
        ]
        if matched:
            employee = matched[0]
            note = ""
            if requested is not None and requested["name"] != employee["name"]:
                note = f"（模型原建议 {requested['name']} 与其默认能力不符，已改派）"
            return employee, f"默认能力包含 {capability}，与该任务匹配{note}"

        generalists = [employee for employee in roster if not employee["capabilities"]]
        if generalists:
            return (
                generalists[0],
                f"未限定专长，作为可执行人力承接「{capability}」任务",
            )
        return None

    @staticmethod
    def _single_named_employee(
        goal_text: str, roster: Sequence[Dict[str, Any]]
    ) -> Optional[Dict[str, Any]]:
        """目标原文只点名一个花名册员工时返回他，否则返回 None。

        「让小王整理这三份材料」是最自然的说法，逐任务让模型挑人会把同一件
        事拆给五个人。只在恰好一个名字出现时生效；出现两个名字说明用户没有
        说"全交给他"，不强派。
        """
        text = str(goal_text or "")
        named = [
            employee
            for employee in roster
            if employee["name"] and employee["name"] in text
        ]
        return named[0] if len(named) == 1 else None

    def _apply_forced_assignment(
        self, goal: Goal, tasks: Sequence[Task]
    ) -> List[Task]:
        """用户点名一个员工时，把所有能力兼容的任务都派给他。"""
        forced = self._single_named_employee(
            getattr(goal, "natural_language", ""), self._employee_roster()
        )
        if forced is None:
            return list(tasks)
        for task in tasks:
            capabilities = forced["capabilities"]
            if capabilities and task.capability_id not in capabilities:
                # 能力不兼容时不越过能力门，仍交给能执行它的人。
                continue
            task.assigned_agent = forced["agent_ids"][0]
            task.agent_claimed = True
            task.assigned_employee = forced["name"]
            task.assignment_reason = "用户点名指派"
        return list(tasks)

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
                if capability in PROSE_CAPABILITIES:
                    # 纯文本能力只需要"同一任务文本"：补 description / name
                    # 不是编造新内容，而是把模型已写出的任务正文接到执行器真正
                    # 读取的字段上。补了什么必须留痕，事后可查。
                    source = (
                        "description"
                        if spec.get("description_from_model")
                        else "name"
                    )
                    text = (
                        str(spec.get("description") or "").strip()
                        or str(spec.get("name") or "").strip()
                    )
                    inputs["instruction"] = text
                    logger.warning(
                        "evt=decompose.input_backfilled field=instruction "
                        "source=%s task=%s",
                        source,
                        spec["name"],
                    )
                else:
                    # file_write 的 path/content 补出来就是编造产物，宁可拒绝
                    # 也不能替模型虚构一个文件名或正文。
                    raise ValueError(
                        f"任务 {spec['name']} 缺少能力 {capability} 的必需输入"
                    )

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

        # 指派：把每个任务落到真实花名册员工上（模型建议 + 能力匹配校验）。
        roster = self._employee_roster()
        for task, spec in zip(tasks, specs):
            choice = self._choose_assignment(
                str(task.capability_id),
                str(spec.get("assign_to") or ""),
                str(spec.get("assign_reason") or ""),
                roster,
            )
            if choice is None:
                continue
            employee, reason = choice
            task.assigned_agent = employee["agent_ids"][0]
            task.agent_claimed = True
            task.assigned_employee = employee["name"]
            task.assignment_reason = reason

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

    def _store_cache(
        self,
        key: tuple,
        snapshot: List[Dict[str, Any]],
        source: str = "model",
    ) -> None:
        if key not in self._cache:
            self._cache_order.append(key)
        self._cache[key] = snapshot
        self._cache_source[key] = source
        while len(self._cache_order) > 64:
            oldest = self._cache_order.pop(0)
            self._cache.pop(oldest, None)
            self._cache_source.pop(oldest, None)

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
            "assigned_agent": task.assigned_agent,
            "assigned_employee": getattr(task, "assigned_employee", None),
            "assignment_reason": getattr(task, "assignment_reason", None),
        }

    def _materialize(
        self, goal: Goal, snapshot: List[Dict[str, Any]]
    ) -> List[Task]:
        # 花名册可能在计划缓存之后变化（雇佣/停用）；agent id 按当前花名册
        # 重新解析一次，员工本人不变，避免把任务派给一个已不存在的 agent。
        roster = {employee["name"]: employee for employee in self._employee_roster()}
        tasks: List[Task] = []
        for item in snapshot:
            task = Task(
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
            employee = roster.get(str(item.get("assigned_employee") or ""))
            agent_id = (
                employee["agent_ids"][0]
                if employee is not None
                else item.get("assigned_agent")
            )
            if agent_id:
                task.assigned_agent = agent_id
                task.agent_claimed = True
                task.assigned_employee = (
                    employee["name"] if employee is not None else item.get("assigned_employee")
                )
                task.assignment_reason = item.get("assignment_reason")
            tasks.append(task)
        assign = getattr(self._inner, "_assign_agents", None)
        if callable(assign):
            assign(goal, tasks)
        return tasks


__all__ = ["LLMTaskDecomposer", "KNOWN_CAPABILITIES", "CAPABILITY_INPUT_CONTRACT"]
