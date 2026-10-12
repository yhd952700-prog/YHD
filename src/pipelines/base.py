"""
Base pipeline components for LiuHao AI OS.

Provides the foundational abstractions for building data pipelines:
- Pipeline base class with DAG support
- PipelineStage abstract class for all pipeline stages
- PipelineContext for data passing between stages
- PipelineStatus enum for lifecycle management
- PipelineResult for execution results
- PipelineError for error handling
- Hook system for observability and extension points
"""

from __future__ import annotations

import logging
import time
import uuid
from abc import ABC, abstractmethod
from collections import deque
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import (
    Any,
    Callable,
    Dict,
    Generator,
    Generic,
    Iterable,
    List,
    Optional,
    Set,
    TypeVar,
)

logger = logging.getLogger(__name__)

T = TypeVar("T")
R = TypeVar("R")


class PipelineStatus(Enum):
    """Enumeration of pipeline lifecycle states."""

    IDLE = auto()
    RUNNING = auto()
    COMPLETED = auto()
    FAILED = auto()
    PAUSED = auto()


class PipelineError(Exception):
    """Base exception for pipeline-related errors.

    Attributes:
        message: Human-readable error description.
        stage_name: Name of the stage where the error occurred, if known.
        pipeline_name: Name of the pipeline that encountered the error.
        original_error: The underlying exception that caused this error.
    """

    def __init__(
        self,
        message: str,
        stage_name: Optional[str] = None,
        pipeline_name: Optional[str] = None,
        original_error: Optional[BaseException] = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.stage_name = stage_name
        self.pipeline_name = pipeline_name
        self.original_error = original_error

    def __str__(self) -> str:
        parts = [self.message]
        if self.stage_name:
            parts.append(f"[stage={self.stage_name}]")
        if self.pipeline_name:
            parts.append(f"[pipeline={self.pipeline_name}]")
        return " ".join(parts)


@dataclass
class PipelineContext:
    """Context object passed between pipeline stages.

    Provides shared state, metadata, and data flow tracking
    throughout pipeline execution.

    Attributes:
        data: The current data payload being processed.
        metadata: Dictionary of arbitrary metadata key-value pairs.
        stage_history: Ordered list of stage names that have been executed.
        errors: List of errors encountered during pipeline execution.
        warnings: List of warnings encountered during pipeline execution.
        started_at: Timestamp when the pipeline started (epoch seconds).
        variables: Mutable dict for passing arbitrary state between stages.
    """

    data: Any = None
    metadata: Dict[str, Any] = field(default_factory=dict)
    stage_history: List[str] = field(default_factory=list)
    errors: List[PipelineError] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    started_at: float = field(default_factory=time.time)
    variables: Dict[str, Any] = field(default_factory=dict)
    _pipeline_id: str = field(default_factory=lambda: str(uuid.uuid4()))

    @property
    def pipeline_id(self) -> str:
        """Unique identifier for this pipeline execution."""
        return self._pipeline_id

    @property
    def elapsed(self) -> float:
        """Elapsed time since pipeline start in seconds."""
        return time.time() - self.started_at

    def mark_stage(self, stage_name: str) -> None:
        """Record that a stage has been executed.

        Args:
            stage_name: Name of the completed stage.
        """
        self.stage_history.append(stage_name)

    def add_error(self, error: PipelineError) -> None:
        """Add an error to the context.

        Args:
            error: The pipeline error to record.
        """
        self.errors.append(error)

    def add_warning(self, warning: str) -> None:
        """Add a warning message to the context.

        Args:
            warning: The warning message to record.
        """
        self.warnings.append(warning)

    def has_errors(self) -> bool:
        """Check if any errors have been recorded."""
        return len(self.errors) > 0

    def set_variable(self, key: str, value: Any) -> None:
        """Set a variable in the context.

        Args:
            key: Variable name.
            value: Variable value.
        """
        self.variables[key] = value

    def get_variable(self, key: str, default: Any = None) -> Any:
        """Get a variable from the context.

        Args:
            key: Variable name.
            default: Default value if key not found.

        Returns:
            The variable value, or default if not found.
        """
        return self.variables.get(key, default)


@dataclass
class PipelineResult:
    """Result of a pipeline execution.

    Attributes:
        status: Final status of the pipeline.
        context: The pipeline context with all state after execution.
        output: The final output data.
        error_count: Number of errors encountered.
        warning_count: Number of warnings encountered.
        stages_completed: Number of stages successfully completed.
        total_stages: Total number of stages in the pipeline.
        duration_seconds: Total wall-clock time of execution.
    """

    status: PipelineStatus
    context: PipelineContext
    output: Any = None
    error_count: int = 0
    warning_count: int = 0
    stages_completed: int = 0
    total_stages: int = 0
    duration_seconds: float = 0.0

    @property
    def success(self) -> bool:
        """Whether the pipeline completed successfully."""
        return self.status == PipelineStatus.COMPLETED and self.error_count == 0

    @property
    def failed(self) -> bool:
        """Whether the pipeline failed."""
        return self.status == PipelineStatus.FAILED or self.error_count > 0


class PipelineStage(ABC, Generic[T, R]):
    """Abstract base class for all pipeline stages.

    A stage represents a single processing step in a pipeline.
    Subclasses must implement the `process` method.

    Attributes:
        name: Human-readable name of the stage.
        description: Optional description of what the stage does.
    """

    def __init__(self, name: Optional[str] = None, description: Optional[str] = None) -> None:
        self.name = name or self.__class__.__name__
        self.description = description or ""
        self._status: PipelineStatus = PipelineStatus.IDLE

    @property
    def status(self) -> PipelineStatus:
        """Current status of this stage."""
        return self._status

    @abstractmethod
    def process(self, data: T, context: PipelineContext) -> R:
        """Process the input data and return output.

        Args:
            data: Input data to process.
            context: Pipeline context for shared state.

        Returns:
            Processed output data.

        Raises:
            PipelineError: If processing fails.
        """
        ...

    def setup(self, context: PipelineContext) -> None:
        """Called once before the stage processes data.

        Override to perform initialization.

        Args:
            context: Pipeline context.
        """
        pass

    def teardown(self, context: PipelineContext) -> None:
        """Called once after the stage finishes processing.

        Override to perform cleanup.

        Args:
            context: Pipeline context.
        """
        pass

    def __call__(self, data: T, context: PipelineContext) -> R:
        """Make the stage callable for functional composition.

        Args:
            data: Input data to process.
            context: Pipeline context.

        Returns:
            Processed output data.
        """
        self._status = PipelineStatus.RUNNING
        try:
            self.setup(context)
            result = self.process(data, context)
            context.mark_stage(self.name)
            self._status = PipelineStatus.COMPLETED
            return result
        except PipelineError:
            self._status = PipelineStatus.FAILED
            raise
        except Exception as e:
            self._status = PipelineStatus.FAILED
            raise PipelineError(
                f"Stage '{self.name}' failed: {e}",
                stage_name=self.name,
                original_error=e,
            ) from e
        finally:
            try:
                self.teardown(context)
            except Exception as te:
                logger.warning(f"Teardown failed for stage '{self.name}': {te}")

    def __repr__(self) -> str:
        return f"<{self.__class__.__name__}(name={self.name!r})>"


class PipelineHooks:
    """Hook system for pipeline lifecycle events.

    Provides callback hooks that are invoked at various points
    during pipeline execution. Multiple callbacks can be registered
    for each hook point.

    Hook points:
        - on_start: Called when pipeline starts execution.
        - on_stage_start: Called before each stage begins.
        - on_stage_end: Called after each stage completes.
        - on_end: Called when pipeline finishes (success or failure).
        - on_error: Called when an error occurs during execution.
        - on_pause: Called when pipeline is paused.
        - on_resume: Called when pipeline is resumed.
    """

    def __init__(self) -> None:
        self._hooks: Dict[str, List[Callable[..., None]]] = {
            "on_start": [],
            "on_stage_start": [],
            "on_stage_end": [],
            "on_end": [],
            "on_error": [],
            "on_pause": [],
            "on_resume": [],
        }

    def register(self, hook_name: str, callback: Callable[..., None]) -> None:
        """Register a callback for a hook point.

        Args:
            hook_name: Name of the hook point.
            callback: Callable to invoke when the hook fires.

        Raises:
            ValueError: If hook_name is not a recognized hook.
        """
        if hook_name not in self._hooks:
            raise ValueError(
                f"Unknown hook '{hook_name}'. Valid hooks: {list(self._hooks.keys())}"
            )
        self._hooks[hook_name].append(callback)

    def unregister(self, hook_name: str, callback: Callable[..., None]) -> bool:
        """Unregister a previously registered callback.

        Args:
            hook_name: Name of the hook point.
            callback: The callback to remove.

        Returns:
            True if the callback was found and removed, False otherwise.
        """
        if hook_name not in self._hooks:
            return False
        try:
            self._hooks[hook_name].remove(callback)
            return True
        except ValueError:
            return False

    def fire(self, hook_name: str, *args: Any, **kwargs: Any) -> None:
        """Fire a hook, invoking all registered callbacks.

        Errors in callbacks are logged but do not propagate.

        Args:
            hook_name: Name of the hook to fire.
            *args: Positional arguments to pass to callbacks.
            **kwargs: Keyword arguments to pass to callbacks.
        """
        if hook_name not in self._hooks:
            return
        for callback in self._hooks[hook_name]:
            try:
                callback(*args, **kwargs)
            except Exception as e:
                logger.error(f"Hook callback '{hook_name}' failed: {e}")

    def on_start(self, callback: Callable[[PipelineContext], None]) -> "PipelineHooks":
        """Register an on_start callback (fluent style).

        Args:
            callback: Callback receiving the pipeline context.

        Returns:
            Self for chaining.
        """
        self.register("on_start", callback)
        return self

    def on_stage_start(
        self, callback: Callable[[str, PipelineContext], None]
    ) -> "PipelineHooks":
        """Register an on_stage_start callback (fluent style).

        Args:
            callback: Callback receiving (stage_name, context).

        Returns:
            Self for chaining.
        """
        self.register("on_stage_start", callback)
        return self

    def on_stage_end(
        self, callback: Callable[[str, Any, PipelineContext], None]
    ) -> "PipelineHooks":
        """Register an on_stage_end callback (fluent style).

        Args:
            callback: Callback receiving (stage_name, result, context).

        Returns:
            Self for chaining.
        """
        self.register("on_stage_end", callback)
        return self

    def on_end(self, callback: Callable[[PipelineResult], None]) -> "PipelineHooks":
        """Register an on_end callback (fluent style).

        Args:
            callback: Callback receiving the pipeline result.

        Returns:
            Self for chaining.
        """
        self.register("on_end", callback)
        return self

    def on_error(self, callback: Callable[[PipelineError, PipelineContext], None]) -> "PipelineHooks":
        """Register an on_error callback (fluent style).

        Args:
            callback: Callback receiving (error, context).

        Returns:
            Self for chaining.
        """
        self.register("on_error", callback)
        return self


class DAGNode:
    """A node in the pipeline DAG (Directed Acyclic Graph).

    Represents a stage and its connections to other stages.

    Attributes:
        stage: The pipeline stage for this node.
        predecessors: List of node IDs that must complete before this node.
        successors: List of node IDs that depend on this node.
    """

    def __init__(self, node_id: str, stage: PipelineStage) -> None:
        self.node_id = node_id
        self.stage = stage
        self.predecessors: List[str] = []
        self.successors: List[str] = []

    def __repr__(self) -> str:
        return f"<DAGNode(id={self.node_id!r}, stage={self.stage.name!r})>"


class Pipeline:
    """Base pipeline class with DAG-based execution support.

    Supports both linear pipelines and complex DAG-structured pipelines
    with branching and merging. Provides hooks for observability and
    a rich result object.

    Attributes:
        name: Name of the pipeline.
        status: Current pipeline status.
        hooks: PipelineHooks instance for lifecycle callbacks.
    """

    def __init__(
        self,
        name: str = "pipeline",
        hooks: Optional[PipelineHooks] = None,
    ) -> None:
        self.name = name
        self.status: PipelineStatus = PipelineStatus.IDLE
        self.hooks = hooks or PipelineHooks()
        self._nodes: Dict[str, DAGNode] = {}
        self._entry_points: List[str] = []
        self._exit_points: List[str] = []

    # --- DAG construction ---

    def add_stage(
        self,
        stage: PipelineStage,
        *,
        node_id: Optional[str] = None,
        after: Optional[List[str]] = None,
        before: Optional[List[str]] = None,
    ) -> str:
        """Add a stage to the pipeline DAG.

        Args:
            stage: The pipeline stage to add.
            node_id: Optional unique identifier for this node. Auto-generated if None.
            after: List of node IDs that must complete before this stage runs.
            before: List of node IDs that this stage must complete before.

        Returns:
            The node ID of the added stage.

        Raises:
            ValueError: If node_id already exists or would create a cycle.
        """
        nid = node_id or stage.name
        if nid in self._nodes:
            raise ValueError(f"Node '{nid}' already exists in pipeline '{self.name}'")

        node = DAGNode(nid, stage)
        self._nodes[nid] = node

        if after:
            for pred_id in after:
                if pred_id not in self._nodes:
                    raise ValueError(
                        f"Cannot add '{nid}' after '{pred_id}': node does not exist"
                    )
                node.predecessors.append(pred_id)
                self._nodes[pred_id].successors.append(nid)

        if before:
            for succ_id in before:
                if succ_id not in self._nodes:
                    raise ValueError(
                        f"Cannot add '{nid}' before '{succ_id}': node does not exist"
                    )
                node.successors.append(succ_id)
                self._nodes[succ_id].predecessors.append(nid)

        self._recompute_entry_exit()
        self._validate_no_cycles()
        return nid

    def connect(self, from_node: str, to_node: str) -> None:
        """Connect two existing nodes in the DAG.

        Args:
            from_node: Source node ID.
            to_node: Target node ID.

        Raises:
            ValueError: If either node doesn't exist or connection would create a cycle.
        """
        if from_node not in self._nodes:
            raise ValueError(f"Source node '{from_node}' not found")
        if to_node not in self._nodes:
            raise ValueError(f"Target node '{to_node}' not found")

        self._nodes[from_node].successors.append(to_node)
        self._nodes[to_node].predecessors.append(from_node)

        try:
            self._validate_no_cycles()
        except ValueError:
            # Rollback
            self._nodes[from_node].successors.remove(to_node)
            self._nodes[to_node].predecessors.remove(from_node)
            raise

        self._recompute_entry_exit()

    def _recompute_entry_exit(self) -> None:
        """Recompute entry and exit point lists."""
        self._entry_points = [
            nid for nid, node in self._nodes.items() if not node.predecessors
        ]
        self._exit_points = [
            nid for nid, node in self._nodes.items() if not node.successors
        ]

    def _validate_no_cycles(self) -> None:
        """Validate that the DAG has no cycles.

        Raises:
            ValueError: If a cycle is detected.
        """
        if not self._nodes:
            return

        # Kahn's algorithm for topological sort
        in_degree = {nid: len(node.predecessors) for nid, node in self._nodes.items()}
        queue = deque([nid for nid, deg in in_degree.items() if deg == 0])
        visited_count = 0

        while queue:
            current = queue.popleft()
            visited_count += 1
            for succ in self._nodes[current].successors:
                in_degree[succ] -= 1
                if in_degree[succ] == 0:
                    queue.append(succ)

        if visited_count != len(self._nodes):
            raise ValueError(f"Cycle detected in pipeline '{self.name}' DAG")

    def topological_order(self) -> List[str]:
        """Return node IDs in topological order.

        Returns:
            List of node IDs in valid execution order.
        """
        if not self._nodes:
            return []

        in_degree = {nid: len(node.predecessors) for nid, node in self._nodes.items()}
        queue = deque(sorted([nid for nid, deg in in_degree.items() if deg == 0]))
        result = []

        while queue:
            current = queue.popleft()
            result.append(current)
            for succ in sorted(self._nodes[current].successors):
                in_degree[succ] -= 1
                if in_degree[succ] == 0:
                    queue.append(succ)

        return result

    # --- Execution ---

    def run(self, initial_data: Any = None) -> PipelineResult:
        """Execute the pipeline with the given initial data.

        Args:
            initial_data: Data to feed into the entry point(s) of the pipeline.

        Returns:
            PipelineResult with the outcome of the execution.
        """
        start_time = time.time()
        context = PipelineContext(data=initial_data)
        context.metadata["pipeline_name"] = self.name

        self.status = PipelineStatus.RUNNING
        self.hooks.fire("on_start", context)

        node_outputs: Dict[str, Any] = {}
        completed: Set[str] = set()
        stages_completed = 0

        try:
            order = self.topological_order()

            for nid in order:
                node = self._nodes[nid]
                stage = node.stage

                # Gather inputs from predecessors
                if not node.predecessors:
                    stage_input = initial_data
                elif len(node.predecessors) == 1:
                    stage_input = node_outputs[node.predecessors[0]]
                else:
                    # Merge multiple inputs into a dict keyed by predecessor node ID
                    stage_input = {
                        pred: node_outputs[pred] for pred in node.predecessors
                    }

                self.hooks.fire("on_stage_start", stage.name, context)

                try:
                    output = stage(stage_input, context)
                    node_outputs[nid] = output
                    completed.add(nid)
                    stages_completed += 1

                    self.hooks.fire("on_stage_end", stage.name, output, context)

                except PipelineError as pe:
                    pe.pipeline_name = self.name
                    context.add_error(pe)
                    self.hooks.fire("on_error", pe, context)
                    raise

            # Collect final output from exit points
            if len(self._exit_points) == 1:
                final_output = node_outputs[self._exit_points[0]]
            elif len(self._exit_points) > 1:
                final_output = {ep: node_outputs[ep] for ep in self._exit_points}
            else:
                final_output = None

            self.status = PipelineStatus.COMPLETED
            result = PipelineResult(
                status=PipelineStatus.COMPLETED,
                context=context,
                output=final_output,
                error_count=len(context.errors),
                warning_count=len(context.warnings),
                stages_completed=stages_completed,
                total_stages=len(self._nodes),
                duration_seconds=time.time() - start_time,
            )

        except PipelineError as pe:
            self.status = PipelineStatus.FAILED
            result = PipelineResult(
                status=PipelineStatus.FAILED,
                context=context,
                output=None,
                error_count=len(context.errors),
                warning_count=len(context.warnings),
                stages_completed=stages_completed,
                total_stages=len(self._nodes),
                duration_seconds=time.time() - start_time,
            )
        except Exception as e:
            self.status = PipelineStatus.FAILED
            pe = PipelineError(
                f"Pipeline '{self.name}' failed unexpectedly: {e}",
                pipeline_name=self.name,
                original_error=e,
            )
            context.add_error(pe)
            self.hooks.fire("on_error", pe, context)
            result = PipelineResult(
                status=PipelineStatus.FAILED,
                context=context,
                output=None,
                error_count=len(context.errors),
                warning_count=len(context.warnings),
                stages_completed=stages_completed,
                total_stages=len(self._nodes),
                duration_seconds=time.time() - start_time,
            )

        self.hooks.fire("on_end", result)
        return result

    def run_async(self, initial_data: Any = None) -> "PipelineResult":
        """Alias for run() — async variant reserved for future use.

        Args:
            initial_data: Data to feed into the pipeline.

        Returns:
            PipelineResult with the outcome.
        """
        return self.run(initial_data)

    # --- Convenience constructors ---

    @classmethod
    def from_stages(
        cls,
        stages: List[PipelineStage],
        name: str = "pipeline",
        hooks: Optional[PipelineHooks] = None,
    ) -> "Pipeline":
        """Create a linear pipeline from an ordered list of stages.

        Args:
            stages: Ordered list of stages (first runs first).
            name: Pipeline name.
            hooks: Optional hooks instance.

        Returns:
            A new Pipeline instance with the stages connected linearly.
        """
        pipeline = cls(name=name, hooks=hooks)
        prev_id: Optional[str] = None
        for stage in stages:
            after = [prev_id] if prev_id else None
            nid = pipeline.add_stage(stage, after=after)
            prev_id = nid
        return pipeline

    @property
    def stage_count(self) -> int:
        """Total number of stages in the pipeline."""
        return len(self._nodes)

    def __repr__(self) -> str:
        return (
            f"<Pipeline(name={self.name!r}, stages={self.stage_count}, "
            f"status={self.status.name})>"
        )


class GeneratorPipelineStage(PipelineStage[T, Generator[R, None, None]], Generic[T, R]):
    """Base class for stages that produce streaming/generator output.

    Subclasses should implement `process_stream` to yield results
    incrementally rather than returning a single value.
    """

    def process(self, data: T, context: PipelineContext) -> Generator[R, None, None]:
        """Process data and return a generator of results.

        Args:
            data: Input data to process.
            context: Pipeline context.

        Yields:
            Processed items one at a time.
        """
        yield from self.process_stream(data, context)

    @abstractmethod
    def process_stream(self, data: T, context: PipelineContext) -> Generator[R, None, None]:
        """Process data as a stream, yielding results incrementally.

        Args:
            data: Input data to process.
            context: Pipeline context.

        Yields:
            Individual processed items.
        """
        ...
