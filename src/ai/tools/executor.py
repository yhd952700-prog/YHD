"""Tool executor for LiuHao AI OS.

Provides tool selection, parameter extraction and validation,
execution with timeout and error handling, result formatting,
batch execution, and tool call statistics.

The executor sits between the reasoning layer and individual tools,
handling the logistics of choosing the right tool, preparing inputs,
and managing execution.
"""
from __future__ import annotations

import time
import threading
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

from .base import (
    BaseTool,
    ToolRegistry,
    ToolResult,
    ResultStatus,
    ToolCategory,
)


# ======================================================================
# Tool Selection
# ======================================================================

@dataclass
class ToolCandidate:
    """A candidate tool for a task, with a relevance score."""

    tool_name: str
    score: float  # 0.0 - 1.0
    reason: str
    matched_keywords: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "tool_name": self.tool_name,
            "score": self.score,
            "reason": self.reason,
            "matched_keywords": list(self.matched_keywords),
        }


# ======================================================================
# Execution Statistics
# ======================================================================

@dataclass
class ToolCallStats:
    """Statistics for a single tool call."""

    tool_name: str
    status: ResultStatus
    execution_time_ms: float
    input_size: int
    output_size: int
    error: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "tool_name": self.tool_name,
            "status": self.status.value,
            "execution_time_ms": self.execution_time_ms,
            "input_size": self.input_size,
            "output_size": self.output_size,
            "error": self.error,
        }


@dataclass
class ToolExecutorStats:
    """Aggregate statistics for the tool executor."""

    total_calls: int = 0
    successful_calls: int = 0
    failed_calls: int = 0
    timeout_calls: int = 0
    total_time_ms: float = 0.0
    tool_stats: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    call_history: List[ToolCallStats] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "total_calls": self.total_calls,
            "successful_calls": self.successful_calls,
            "failed_calls": self.failed_calls,
            "timeout_calls": self.timeout_calls,
            "total_time_ms": round(self.total_time_ms, 3),
            "avg_time_ms": round(
                self.total_time_ms / max(1, self.total_calls), 3
            ),
            "success_rate": round(
                self.successful_calls / max(1, self.total_calls), 3
            ),
            "per_tool": dict(self.tool_stats),
        }


# ======================================================================
# Batch Execution Result
# ======================================================================

@dataclass
class BatchResult:
    """Result of a batch tool execution."""

    results: List[ToolResult]
    total_calls: int
    successful: int
    failed: int
    total_time_ms: float

    @property
    def all_succeeded(self) -> bool:
        return self.successful == self.total_calls

    def to_dict(self) -> Dict[str, Any]:
        return {
            "results": [r.to_dict() for r in self.results],
            "total_calls": self.total_calls,
            "successful": self.successful,
            "failed": self.failed,
            "total_time_ms": self.total_time_ms,
            "all_succeeded": self.all_succeeded,
        }


# ======================================================================
# Tool Executor
# ======================================================================

class ToolExecutor:
    """Tool execution manager.

    Handles tool selection, parameter validation, execution with
    timeout and error handling, result formatting, batch execution,
    and call statistics tracking.

    Usage::

        executor = ToolExecutor(registry)
        result = executor.execute("calculator", expression="2 + 2")

        # Select best tool for a task
        candidates = executor.select_tools("calculate the area of a circle")

        # Batch execution
        batch_result = executor.batch_execute([
            ("calculator", {"expression": "2 + 2"}),
            ("datetime", {"operation": "now"}),
        ])
    """

    def __init__(
        self,
        registry: Optional[ToolRegistry] = None,
        default_timeout: float = 30.0,
    ):
        """Initialize the tool executor.

        Args:
            registry: Tool registry to use. Creates a new one if None.
            default_timeout: Default execution timeout in seconds.
        """
        self.registry = registry or ToolRegistry()
        self.default_timeout = default_timeout
        self._stats = ToolExecutorStats()
        self._keyword_map: Dict[str, List[str]] = {}
        self._build_keyword_map()

    # ------------------------------------------------------------------
    # Tool Selection
    # ------------------------------------------------------------------

    def select_tools(
        self,
        task_description: str,
        top_k: int = 3,
    ) -> List[ToolCandidate]:
        """Select the most relevant tools for a task description.

        Uses keyword matching and heuristic scoring to rank tools.

        Args:
            task_description: Description of the task to accomplish.
            top_k: Number of top candidates to return.

        Returns:
            List of ToolCandidate objects, sorted by relevance score.
        """
        task_lower = task_description.lower()
        task_words = set(task_lower.split())
        candidates: List[ToolCandidate] = []

        for tool in self.registry.list_all():
            score, matched, reason = self._score_tool(tool, task_lower, task_words)
            if score > 0:
                candidates.append(ToolCandidate(
                    tool_name=tool.name,
                    score=score,
                    reason=reason,
                    matched_keywords=matched,
                ))

        # Sort by score descending
        candidates.sort(key=lambda c: c.score, reverse=True)
        return candidates[:top_k]

    def select_best(self, task_description: str) -> Optional[str]:
        """Select the single best tool for a task.

        Args:
            task_description: Description of the task.

        Returns:
            Name of the best tool, or None if no good match.
        """
        candidates = self.select_tools(task_description, top_k=1)
        if candidates and candidates[0].score >= 0.15:
            return candidates[0].tool_name
        return None

    def _score_tool(
        self,
        tool: BaseTool,
        task_lower: str,
        task_words: set,
    ) -> tuple[float, List[str], str]:
        """Score a tool's relevance to a task.

        Returns:
            Tuple of (score, matched_keywords, reason).
        """
        score = 0.0
        matched: List[str] = []
        reasons: List[str] = []

        # Check tool name
        tool_name_lower = tool.name.lower()
        if tool_name_lower in task_lower:
            score += 0.5
            matched.append(tool.name)
            reasons.append(f"tool name '{tool.name}' mentioned")

        # Check description keywords
        desc_words = set(tool.description.lower().split())
        desc_matches = task_words & desc_words
        if desc_matches:
            match_score = min(0.3, len(desc_matches) * 0.05)
            score += match_score
            matched.extend(list(desc_matches)[:5])
            reasons.append(f"{len(desc_matches)} description keyword matches")

        # Check category keywords
        category_keywords = {
            ToolCategory.COMPUTATION: {"calculate", "compute", "math", "code",
                                       "evaluate", "run", "execute", "sum",
                                       "equation", "formula", "plus", "minus",
                                       "times", "divide", "multiply", "add",
                                       "subtract", "area", "volume", "solver",
                                       "calculation", "arithmetic", "expression"},
            ToolCategory.INFORMATION: {"search", "find", "look up", "query",
                                       "information", "knowledge", "research",
                                       "lookup"},
            ToolCategory.MEMORY: {"remember", "recall", "memory", "store",
                                  "save", "retrieve", "forget", "memorize"},
            ToolCategory.UTILITY: {"time", "date", "now", "today", "clock",
                                   "utility", "tool"},
            ToolCategory.FILE: {"file", "read", "write", "save to", "load from",
                                "directory", "folder", "path"},
            ToolCategory.ANALYSIS: {"analyze", "compare", "statistics",
                                    "data", "report", "summarize"},
            ToolCategory.CREATIVE: {"generate", "create", "write", "design",
                                    "creative", "imagine"},
            ToolCategory.COMMUNICATION: {"send", "message", "notify",
                                         "email", "chat", "tell"},
        }

        cat_words = category_keywords.get(tool.category, set())
        cat_matches = task_words & cat_words
        if cat_matches:
            match_score = min(0.5, len(cat_matches) * 0.15)
            score += match_score
            matched.extend(list(cat_matches)[:3])
            reasons.append(f"{len(cat_matches)} category keyword matches")

        # Normalize score
        score = min(1.0, score)

        reason = "; ".join(reasons) if reasons else "no direct match"
        return score, matched, reason

    # ------------------------------------------------------------------
    # Execution
    # ------------------------------------------------------------------

    def execute(
        self,
        tool_name: str,
        timeout: Optional[float] = None,
        **params: Any,
    ) -> ToolResult:
        """Execute a tool by name with the given parameters.

        Args:
            tool_name: Name of the tool to execute.
            timeout: Optional timeout override (in seconds).
            **params: Tool parameters.

        Returns:
            ToolResult with the execution outcome.
        """
        tool = self.registry.get(tool_name)
        if tool is None:
            result = ToolResult.failure(f"Tool not found: {tool_name}")
            self._record_call(tool_name, result, params)
            return result

        # Validate parameters
        valid, error = tool.validate_params(params)
        if not valid:
            result = ToolResult.failure(f"Parameter validation failed: {error}")
            self._record_call(tool_name, result, params)
            return result

        # Execute with timeout
        timeout_val = timeout if timeout is not None else self.default_timeout
        result = self._execute_with_timeout(tool, params, timeout_val)

        self._record_call(tool_name, result, params)
        return result

    def _execute_with_timeout(
        self,
        tool: BaseTool,
        params: Dict[str, Any],
        timeout: float,
    ) -> ToolResult:
        """Execute a tool with a timeout using a thread.

        Note: For tools that don't cooperate with thread interruption,
        the timeout is best-effort. The tool thread may continue running
        but the result will be returned as a timeout.
        """
        result_container: List[Optional[ToolResult]] = [None]
        exception_container: List[Optional[Exception]] = [None]

        def run_tool():
            try:
                result_container[0] = tool.execute(**params)
            except Exception as e:
                exception_container[0] = e

        thread = threading.Thread(target=run_tool, daemon=True)
        thread.start()
        thread.join(timeout=timeout)

        if thread.is_alive():
            return ToolResult.timeout(
                f"Tool '{tool.name}' exceeded {timeout}s timeout"
            )

        if exception_container[0] is not None:
            return ToolResult.failure(str(exception_container[0]))

        if result_container[0] is not None:
            return result_container[0]

        return ToolResult.failure("Tool execution produced no result")

    # ------------------------------------------------------------------
    # Batch Execution
    # ------------------------------------------------------------------

    def batch_execute(
        self,
        calls: List[tuple[str, Dict[str, Any]]],
        stop_on_error: bool = False,
        timeout: Optional[float] = None,
    ) -> BatchResult:
        """Execute multiple tool calls in sequence.

        Args:
            calls: List of (tool_name, params_dict) tuples.
            stop_on_error: If True, stop at the first failure.
            timeout: Optional timeout per call.

        Returns:
            BatchResult with all results.
        """
        start_time = time.time()
        results: List[ToolResult] = []
        successful = 0
        failed = 0

        for tool_name, params in calls:
            result = self.execute(tool_name, timeout=timeout, **params)
            results.append(result)

            if result.ok:
                successful += 1
            else:
                failed += 1
                if stop_on_error:
                    break

        total_time = (time.time() - start_time) * 1000

        return BatchResult(
            results=results,
            total_calls=len(results),
            successful=successful,
            failed=failed,
            total_time_ms=round(total_time, 2),
        )

    # ------------------------------------------------------------------
    # Result Formatting
    # ------------------------------------------------------------------

    def format_result(self, result: ToolResult, style: str = "text") -> str:
        """Format a tool result for display.

        Args:
            result: The tool result to format.
            style: Formatting style ('text', 'json', 'brief').

        Returns:
            Formatted string representation.
        """
        if style == "json":
            import json
            return json.dumps(result.to_dict(), indent=2, default=str)

        elif style == "brief":
            if result.ok:
                output = str(result.output)[:200]
                return f"[OK] {output}"
            else:
                return f"[FAILED] {result.error}"

        else:  # text
            lines = [f"Status: {result.status.value}"]
            if result.ok:
                lines.append(f"Output: {result.output}")
            else:
                lines.append(f"Error: {result.error}")
            lines.append(f"Execution time: {result.execution_time_ms:.2f}ms")
            if result.metadata:
                lines.append(f"Metadata: {result.metadata}")
            return "\n".join(lines)

    # ------------------------------------------------------------------
    # Statistics
    # ------------------------------------------------------------------

    @property
    def stats(self) -> ToolExecutorStats:
        """Get execution statistics."""
        return self._stats

    def reset_stats(self) -> None:
        """Reset all statistics."""
        self._stats = ToolExecutorStats()

    def _record_call(
        self,
        tool_name: str,
        result: ToolResult,
        params: Dict[str, Any],
    ) -> None:
        """Record a tool call in statistics."""
        self._stats.total_calls += 1
        self._stats.total_time_ms += result.execution_time_ms

        if result.status == ResultStatus.SUCCESS:
            self._stats.successful_calls += 1
        elif result.status == ResultStatus.TIMEOUT:
            self._stats.timeout_calls += 1
            self._stats.failed_calls += 1
        else:
            self._stats.failed_calls += 1

        # Per-tool stats
        if tool_name not in self._stats.tool_stats:
            self._stats.tool_stats[tool_name] = {
                "calls": 0,
                "success": 0,
                "failures": 0,
                "total_time_ms": 0.0,
            }

        ts = self._stats.tool_stats[tool_name]
        ts["calls"] += 1
        if result.ok:
            ts["success"] += 1
        else:
            ts["failures"] += 1
        ts["total_time_ms"] += result.execution_time_ms

        # Call history
        output_size = len(str(result.output)) if result.output is not None else 0
        input_size = len(str(params))
        self._stats.call_history.append(ToolCallStats(
            tool_name=tool_name,
            status=result.status,
            execution_time_ms=result.execution_time_ms,
            input_size=input_size,
            output_size=output_size,
            error=result.error,
        ))

        # Keep history bounded
        if len(self._stats.call_history) > 1000:
            self._stats.call_history = self._stats.call_history[-1000:]

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _build_keyword_map(self) -> None:
        """Build a keyword-to-tools map for faster selection."""
        self._keyword_map.clear()
        for tool in self.registry.list_all():
            keywords = self._extract_keywords(tool)
            for kw in keywords:
                if kw not in self._keyword_map:
                    self._keyword_map[kw] = []
                self._keyword_map[kw].append(tool.name)

    def _extract_keywords(self, tool: BaseTool) -> List[str]:
        """Extract keywords from a tool's name and description."""
        keywords = [tool.name.lower()]
        keywords.extend(tool.description.lower().split())
        # Filter short words and common words
        common = {"the", "a", "an", "to", "for", "of", "in", "and", "with",
                  "is", "on", "from", "by", "at", "this", "that", "it",
                  "its", "can", "be", "are", "was", "were"}
        return [kw for kw in keywords if len(kw) > 2 and kw not in common]

    # ------------------------------------------------------------------
    # Registry proxy methods
    # ------------------------------------------------------------------

    def register_tool(self, tool: BaseTool) -> str:
        """Register a tool and rebuild the keyword map."""
        name = self.registry.register(tool)
        self._build_keyword_map()
        return name

    def available_tools(self) -> List[str]:
        """List all available tool names."""
        return self.registry.list_names()

    def tool_info(self, tool_name: str) -> Optional[Dict[str, Any]]:
        """Get information about a tool."""
        tool = self.registry.get(tool_name)
        if tool is None:
            return None
        return tool.info()
