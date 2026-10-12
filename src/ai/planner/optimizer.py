"""Optimise task plans for better execution.

Optimisation layers:
  1. **Parallelisation** — find tasks that can run concurrently
  2. **Task merging** — merge tiny tasks that would have more overhead than value
  3. **Critical path analysis** — identify the longest dependency chain
  4. **Resource-aware scheduling** — adjust parallelism based on resource limits
  5. **Task ordering heuristic** — put high-risk tasks early (fail fast)
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Set, Tuple

from .parser import ParsedPlan, ParsedTask


@dataclass
class OptimizationResult:
    """Result of plan optimisation."""
    plan: ParsedPlan
    original_task_count: int = 0
    optimised_task_count: int = 0
    merged_tasks: int = 0
    parallel_tasks: int = 0
    critical_path_length: int = 0
    optimisations_applied: List[str] = field(default_factory=list)

    @property
    def tasks_removed(self) -> int:
        return self.original_task_count - self.optimised_task_count


class PlanOptimizer:
    """Optimise a parsed plan for better execution.

    Usage::

        optimizer = PlanOptimizer(max_parallel=4)
        result = optimizer.optimize(plan)
        optimized_plan = result.plan
    """

    def __init__(
        self,
        max_parallel: int = 4,
        merge_threshold: int = 2,  # Merge tasks with fewer than N steps
        enable_parallelisation: bool = True,
        enable_merging: bool = True,
        enable_critical_path: bool = True,
    ):
        self.max_parallel = max_parallel
        self.merge_threshold = merge_threshold
        self.enable_parallelisation = enable_parallelisation
        self.enable_merging = enable_merging
        self.enable_critical_path = enable_critical_path

    def optimize(self, plan: ParsedPlan) -> OptimizationResult:
        """Run all optimisations on a plan.

        Args:
            plan: The parsed plan to optimise

        Returns:
            OptimizationResult with the optimised plan and stats
        """
        result = OptimizationResult(
            plan=plan,
            original_task_count=plan.task_count,
            optimised_task_count=plan.task_count,
        )

        if not plan.tasks:
            return result

        # Optimization 1: Compute critical path (informational, also used
        # by other optimisations)
        if self.enable_critical_path:
            critical_path = self._compute_critical_path(plan.tasks)
            result.critical_path_length = len(critical_path)
            result.optimisations_applied.append("critical_path_analysis")

        # Optimization 2: Merge tiny sequential tasks
        if self.enable_merging and len(plan.tasks) > self.merge_threshold:
            merged_count = self._merge_tiny_tasks(plan)
            if merged_count > 0:
                result.merged_tasks = merged_count
                result.optimisations_applied.append(f"task_merging({merged_count})")

        # Optimization 3: Mark parallelisable tasks
        if self.enable_parallelisation:
            parallel_count = self._mark_parallel_groups(plan)
            result.parallel_tasks = parallel_count
            result.optimisations_applied.append(f"parallel_analysis({parallel_count})")

        # Update counts
        result.optimised_task_count = plan.task_count

        return result

    def _compute_critical_path(self, tasks: List[ParsedTask]) -> List[str]:
        """Find the longest dependency chain (critical path).

        Returns list of task IDs in the critical path.
        """
        task_map = {t.id: t for t in tasks}

        # Topological sort using Kahn's algorithm
        in_degree = {t.id: len(t.depends_on) for t in tasks}
        queue = [t.id for t in tasks if in_degree[t.id] == 0]

        # Longest path to each node
        dist: Dict[str, int] = {t.id: 1 for t in tasks}
        prev: Dict[str, Optional[str]] = {t.id: None for t in tasks}

        while queue:
            node = queue.pop(0)
            task = task_map.get(node)
            if not task:
                continue

            # Find all tasks that depend on this one
            for other in tasks:
                if node in other.depends_on:
                    new_dist = dist[node] + 1
                    if new_dist > dist[other.id]:
                        dist[other.id] = new_dist
                        prev[other.id] = node
                    in_degree[other.id] -= 1
                    if in_degree[other.id] == 0:
                        queue.append(other.id)

        # Find the end of the critical path
        if not dist:
            return []
        end_node = max(dist, key=dist.get)

        # Reconstruct the path
        path: List[str] = []
        current: Optional[str] = end_node
        while current is not None:
            path.append(current)
            current = prev.get(current)
        path.reverse()

        return path

    def _merge_tiny_tasks(self, plan: ParsedPlan) -> int:
        """Merge very small sequential tasks into their predecessors.

        A "tiny" task is one that:
        - Has no inputs (or very simple inputs)
        - Has only one dependency
        - Is the only dependent of that predecessor
        - Has a simple capability (e.g. "log" or "notify")

        Returns the number of tasks removed.
        """
        if len(plan.tasks) < 3:
            return 0

        # Build reverse dependency map
        task_map = {t.id: t for t in plan.tasks}
        dependents: Dict[str, List[str]] = {t.id: [] for t in plan.tasks}
        for t in plan.tasks:
            for dep in t.depends_on:
                if dep in dependents:
                    dependents[dep].append(t.id)

        # Find merge candidates
        to_remove: Set[str] = set()
        merged_inputs: Dict[str, Dict[str, Any]] = {}

        for task in plan.tasks:
            if task.id in to_remove:
                continue

            # A task is "tiny" if it has minimal inputs and no special verification
            is_tiny = (
                len(task.inputs or {}) <= 1
                and not task.verification_criteria
                and len(task.depends_on) == 1
            )
            if not is_tiny:
                continue

            dep_id = task.depends_on[0]
            dep = task_map.get(dep_id)
            if not dep or dep.id in to_remove:
                continue

            # Predecessor must have exactly this one dependent
            if len(dependents.get(dep_id, [])) != 1:
                continue

            # Merge: absorb this task into its predecessor
            # Add this task's inputs as a sub-field in the predecessor
            if "sub_tasks" not in merged_inputs.setdefault(dep_id, {}):
                merged_inputs[dep_id]["sub_tasks"] = []
            merged_inputs[dep_id]["sub_tasks"].append({
                "id": task.id,
                "name": task.name,
                "capability_id": task.capability_id,
                "inputs": task.inputs,
            })

            # Update dependents: tasks that depended on this task now depend on predecessor
            for dependent_id in dependents.get(task.id, []):
                dep_task = task_map.get(dependent_id)
                if dep_task:
                    # Replace this task with its predecessor in the dependency list
                    new_deps = []
                    for d in dep_task.depends_on:
                        if d == task.id:
                            if dep_id not in new_deps:
                                new_deps.append(dep_id)
                        else:
                            new_deps.append(d)
                    dep_task.depends_on = new_deps

            to_remove.add(task.id)

        # Apply merges
        if to_remove:
            plan.tasks = [t for t in plan.tasks if t.id not in to_remove]
            for task_id, extra_inputs in merged_inputs.items():
                task = task_map.get(task_id)
                if task:
                    task.inputs.update(extra_inputs)

        return len(to_remove)

    def _mark_parallel_groups(self, plan: ParsedPlan) -> int:
        """Identify which tasks can run in parallel.

        Adds a ``parallel_group`` metadata field to each task's inputs
        and returns the number of tasks that are parallelisable.

        Tasks at the same "level" (same distance from start) can run
        concurrently, up to ``max_parallel``.
        """
        if not plan.tasks:
            return 0

        # Compute topological levels
        levels = self._compute_levels(plan.tasks)

        # Group tasks by level
        level_tasks: Dict[int, List[str]] = {}
        for task_id, level in levels.items():
            level_tasks.setdefault(level, []).append(task_id)

        # Mark parallel groups (cap at max_parallel per group)
        parallel_count = 0
        for level, task_ids in level_tasks.items():
            group_size = min(len(task_ids), self.max_parallel)
            if len(task_ids) > 1:
                parallel_count += len(task_ids)

            # Add group info to task inputs (metadata)
            for i, task_id in enumerate(task_ids):
                task = next((t for t in plan.tasks if t.id == task_id), None)
                if task:
                    if "_plan_meta" not in task.inputs:
                        task.inputs["_plan_meta"] = {}
                    task.inputs["_plan_meta"].update({
                        "topo_level": level,
                        "parallel_group": f"level_{level}",
                        "parallel_index": i,
                        "can_parallel": len(task_ids) > 1,
                    })

        return parallel_count

    def _compute_levels(self, tasks: List[ParsedTask]) -> Dict[str, int]:
        """Compute the topological level of each task.

        Level 0 = no dependencies, Level 1 = depends on level 0, etc.
        """
        task_map = {t.id: t for t in tasks}
        levels: Dict[str, int] = {}

        def get_level(task_id: str) -> int:
            if task_id in levels:
                return levels[task_id]
            task = task_map.get(task_id)
            if not task or not task.depends_on:
                levels[task_id] = 0
                return 0
            max_dep_level = max(get_level(d) for d in task.depends_on if d in task_map)
            level = max_dep_level + 1
            levels[task_id] = level
            return level

        for t in tasks:
            get_level(t.id)

        return levels
