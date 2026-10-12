"""Parse LLM planning output into structured task data.

The parser is defensive — LLM output is unreliable, so we:
  1. Strip markdown code fences (LLMs love to wrap JSON in ```json)
  2. Validate required fields
  3. Coerce types where safe
  4. Return a ParsedPlan with validation issues rather than crashing
  5. Never silently drop tasks; if a task is invalid, note why
"""
from __future__ import annotations

import json
import re
import uuid
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class ParsedTask:
    """A single task parsed from the LLM's plan output."""
    id: str
    name: str
    description: str = ""
    capability_id: str = ""
    inputs: Dict[str, Any] = field(default_factory=dict)
    depends_on: List[str] = field(default_factory=list)
    verification_criteria: str = ""
    # Metadata filled in by the parser
    parse_warnings: List[str] = field(default_factory=list)
    is_valid: bool = True

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "description": self.description,
            "capability_id": self.capability_id,
            "inputs": self.inputs,
            "depends_on": self.depends_on,
            "verification_criteria": self.verification_criteria,
        }


@dataclass
class ParsedPlan:
    """A full plan parsed from LLM output."""
    tasks: List[ParsedTask] = field(default_factory=list)
    clarification_needed: bool = False
    clarification_reason: str = ""
    plan_summary: str = ""
    estimated_complexity: str = "medium"
    parallelizable: bool = True
    # Parse metadata
    raw_output: str = ""
    parse_errors: List[str] = field(default_factory=list)
    parse_warnings: List[str] = field(default_factory=list)
    parse_success: bool = False

    @property
    def task_count(self) -> int:
        return len(self.tasks)

    @property
    def valid_task_count(self) -> int:
        return sum(1 for t in self.tasks if t.is_valid)

    def get_task_by_id(self, task_id: str) -> Optional[ParsedTask]:
        for t in self.tasks:
            if t.id == task_id:
                return t
        return None


class PlanParser:
    """Parse LLM output into a structured ParsedPlan.

    Usage::

        parser = PlanParser()
        plan = parser.parse(llm_output_text)
        if plan.parse_success:
            for task in plan.tasks:
                print(task.name, task.capability_id)
        else:
            print("Parse failed:", plan.parse_errors)
    """

    # Common markdown / code-fence patterns LLMs produce
    _CODE_FENCE = re.compile(r"```(?:json)?\s*(.*?)\s*```", re.DOTALL | re.IGNORECASE)
    _JSON_OBJECT = re.compile(r"\{.*\}", re.DOTALL)

    def __init__(self, strict: bool = False):
        """
        Args:
            strict: If True, any parse warning becomes an error and
                    parse_success is False. Use in testing; in production
                    we prefer partial plans over no plan.
        """
        self.strict = strict

    def parse(self, raw_output: str) -> ParsedPlan:
        """Parse raw LLM output into a ParsedPlan.

        Args:
            raw_output: The raw text from the LLM

        Returns:
            ParsedPlan — always returns a plan object; check
            ``parse_success`` and ``parse_errors`` for issues.
        """
        plan = ParsedPlan(raw_output=raw_output)

        # Step 1: Extract JSON from the output
        json_str = self._extract_json(raw_output)
        if json_str is None:
            plan.parse_errors.append("Could not find valid JSON in output")
            return plan

        # Step 2: Parse JSON
        try:
            data = json.loads(json_str)
        except json.JSONDecodeError as e:
            plan.parse_errors.append(f"JSON decode error: {e}")
            return plan

        if not isinstance(data, dict):
            plan.parse_errors.append(f"Expected JSON object, got {type(data).__name__}")
            return plan

        # Step 3: Extract top-level fields
        plan.clarification_needed = bool(data.get("clarification_needed", False))
        plan.clarification_reason = str(data.get("clarification_reason", ""))
        plan.plan_summary = str(data.get("plan_summary", ""))
        plan.estimated_complexity = str(data.get("estimated_complexity", "medium"))
        plan.parallelizable = bool(data.get("parallelizable", True))

        # Step 4: Parse tasks
        raw_tasks = data.get("tasks", [])
        if not isinstance(raw_tasks, list):
            plan.parse_errors.append("'tasks' field is not a list")
            return plan

        for i, raw_task in enumerate(raw_tasks):
            task = self._parse_task(raw_task, index=i)
            plan.tasks.append(task)
            if not task.is_valid and self.strict:
                plan.parse_errors.append(
                    f"Task {i} ({task.name or 'unnamed'}): {'; '.join(task.parse_warnings)}"
                )
            else:
                plan.parse_warnings.extend(
                    f"Task {i} ({task.name or 'unnamed'}): {w}"
                    for w in task.parse_warnings
                )

        # Step 5: Validate task IDs are unique
        task_ids = [t.id for t in plan.tasks if t.id]
        if len(task_ids) != len(set(task_ids)):
            dupes = [tid for tid in task_ids if task_ids.count(tid) > 1]
            plan.parse_warnings.append(f"Duplicate task IDs: {set(dupes)}")
            # Deduplicate by appending suffix
            seen: Dict[str, int] = {}
            for t in plan.tasks:
                if t.id in seen:
                    seen[t.id] += 1
                    t.id = f"{t.id}_{seen[t.id]}"
                else:
                    seen[t.id] = 0

        # Step 6: Validate dependency references
        valid_ids = {t.id for t in plan.tasks}
        for t in plan.tasks:
            bad_deps = [d for d in t.depends_on if d not in valid_ids]
            if bad_deps:
                t.parse_warnings.append(f"Unknown dependencies: {bad_deps}")
                t.depends_on = [d for d in t.depends_on if d in valid_ids]

        # Step 7: Detect circular dependencies
        if self._has_circular_deps(plan.tasks):
            plan.parse_errors.append("Circular dependency detected in task graph")
            return plan

        # Success — if we got here and have no errors
        plan.parse_success = not plan.parse_errors
        return plan

    def _extract_json(self, text: str) -> Optional[str]:
        """Extract JSON string from LLM output.

        Tries, in order:
        1. Code-fenced JSON (```json ... ```)
        2. First JSON object match
        3. The whole text (if it looks like JSON)
        """
        if not text:
            return None

        # Try code fences first — most common LLM pattern
        m = self._CODE_FENCE.search(text)
        if m:
            return m.group(1).strip()

        # Try to find a JSON object
        m = self._JSON_OBJECT.search(text)
        if m:
            return m.group(0).strip()

        # If the whole thing looks like JSON, use it
        stripped = text.strip()
        if stripped.startswith("{") and stripped.endswith("}"):
            return stripped

        return None

    def _parse_task(self, raw_task: Any, index: int) -> ParsedTask:
        """Parse a single task dict from the LLM output."""
        task = ParsedTask(
            id=f"task_{index + 1}",
            name=f"Task {index + 1}",
        )

        if not isinstance(raw_task, dict):
            task.is_valid = False
            task.parse_warnings.append(f"Task is not a dict (got {type(raw_task).__name__})")
            return task

        # ID
        raw_id = raw_task.get("id")
        if raw_id:
            task.id = str(raw_id)
        else:
            task.parse_warnings.append("Missing 'id', using default")

        # Name
        raw_name = raw_task.get("name")
        if raw_name:
            task.name = str(raw_name)
        else:
            task.parse_warnings.append("Missing 'name'")

        # Description
        task.description = str(raw_task.get("description", ""))

        # Capability ID — this is CRITICAL
        raw_cap = raw_task.get("capability_id")
        if raw_cap:
            task.capability_id = str(raw_cap)
        else:
            task.is_valid = False
            task.parse_warnings.append("Missing required field 'capability_id'")

        # Inputs
        raw_inputs = raw_task.get("inputs", {})
        if isinstance(raw_inputs, dict):
            task.inputs = dict(raw_inputs)
        else:
            task.parse_warnings.append("'inputs' is not a dict, ignoring")
            task.inputs = {}

        # Dependencies
        raw_deps = raw_task.get("depends_on", [])
        if isinstance(raw_deps, list):
            task.depends_on = [str(d) for d in raw_deps]
        elif isinstance(raw_deps, str):
            # LLM might put a single dep as a string
            task.depends_on = [raw_deps]
            task.parse_warnings.append("'depends_on' is a string, converted to list")
        else:
            task.parse_warnings.append("'depends_on' is not a list, ignoring")
            task.depends_on = []

        # Verification criteria
        task.verification_criteria = str(
            raw_task.get("verification_criteria", "")
        )

        return task

    def _has_circular_deps(self, tasks: List[ParsedTask]) -> bool:
        """Check for circular dependencies using DFS."""
        task_map = {t.id: t for t in tasks}
        visited: set[str] = set()
        in_stack: set[str] = set()

        def dfs(task_id: str) -> bool:
            visited.add(task_id)
            in_stack.add(task_id)
            task = task_map.get(task_id)
            if task:
                for dep in task.depends_on:
                    if dep not in visited:
                        if dfs(dep):
                            return True
                    elif dep in in_stack:
                        return True
            in_stack.discard(task_id)
            return False

        for t in tasks:
            if t.id not in visited:
                if dfs(t.id):
                    return True
        return False


def quick_parse_tasks(raw_output: str) -> List[Dict[str, Any]]:
    """Quick-and-dirty parse for when you just need task dicts.

    For production use, prefer :class:`PlanParser` which gives you
    full validation and error reporting.

    Args:
        raw_output: Raw LLM output text

    Returns:
        List of task dicts (empty list on failure)
    """
    parser = PlanParser(strict=False)
    plan = parser.parse(raw_output)
    if not plan.parse_success and not plan.tasks:
        return []
    return [t.to_dict() for t in plan.tasks if t.is_valid]
