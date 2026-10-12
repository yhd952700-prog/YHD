"""
Pipeline builder with fluent API for LiuHao AI OS pipelines.

Provides a declarative, chainable interface for constructing pipelines.
Supports linear pipelines, branching, merging, conditional stages,
and parallel execution.

Also supports loading pipeline definitions from YAML/JSON config files.

Usage:
    pipeline = (PipelineBuilder("my_pipeline")
        .source(MemorySource(data=records))
        .transform(FilterTransform(field="age", value=18, operator="ge"))
        .transform(MapTransform(add_field="is_adult", value_func=lambda r: True))
        .validate(SchemaValidator(schema={...}))
        .sink(FileSink("output.json"))
        .build())

    result = pipeline.run()
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Tuple, Union

from .aggregation import (
    AverageAggregator,
    BaseAggregator,
    CountAggregator,
    GroupByAggregator,
    MinMaxAggregator,
    MovingAverageAggregator,
    PercentileAggregator,
    SumAggregator,
    WindowedAggregator,
)
from .base import Pipeline, PipelineContext, PipelineHooks, PipelineResult, PipelineStage
from .sinks import (
    APISink,
    BaseSink,
    ConsoleSink,
    DatabaseSink,
    FileSink,
    MemorySink,
    MultiSink,
)
from .sources import (
    APISource,
    BaseSource,
    DatabaseSource,
    FileSource,
    MemorySource,
    StreamSource,
)
from .transforms import (
    BaseTransform,
    DeduplicateTransform,
    FilterTransform,
    JoinTransform,
    MapTransform,
    NormalizeTransform,
    ReduceTransform,
    RenameTransform,
    SortTransform,
    SplitTransform,
    TypeCastTransform,
)
from .validation import (
    BaseValidator,
    RangeValidator,
    RegexValidator,
    RequiredFieldsValidator,
    SchemaValidator,
    TypeValidator,
    UniqueValidator,
    ValidationReport,
)

logger = logging.getLogger(__name__)

Record = Dict[str, Any]
Records = List[Record]


class _BranchBuilder:
    """Helper for building a branch within a pipeline.

    Returned by PipelineBuilder.branch() and used to add stages
    to a branch before merging back.
    """

    def __init__(self, builder: "PipelineBuilder", branch_name: str, from_node: str) -> None:
        self._builder = builder
        self._branch_name = branch_name
        self._from_node = from_node
        self._last_node: Optional[str] = None

    def transform(self, transform: BaseTransform, name: Optional[str] = None) -> "_BranchBuilder":
        """Add a transform stage to this branch.

        Args:
            transform: The transform stage.
            name: Optional node name override.

        Returns:
            Self for chaining.
        """
        node_id = name or f"{self._branch_name}_{transform.name}"
        after = [self._last_node] if self._last_node else [self._from_node]
        self._builder._pipeline.add_stage(transform, node_id=node_id, after=after)
        self._last_node = node_id
        return self

    def validate(self, validator: BaseValidator, name: Optional[str] = None) -> "_BranchBuilder":
        """Add a validation stage to this branch.

        Args:
            validator: The validator stage.
            name: Optional node name override.

        Returns:
            Self for chaining.
        """
        node_id = name or f"{self._branch_name}_{validator.name}"
        after = [self._last_node] if self._last_node else [self._from_node]
        self._builder._pipeline.add_stage(validator, node_id=node_id, after=after)
        self._last_node = node_id
        return self

    def aggregate(self, aggregator: BaseAggregator, name: Optional[str] = None) -> "_BranchBuilder":
        """Add an aggregation stage to this branch.

        Args:
            aggregator: The aggregator stage.
            name: Optional node name override.

        Returns:
            Self for chaining.
        """
        node_id = name or f"{self._branch_name}_{aggregator.name}"
        after = [self._last_node] if self._last_node else [self._from_node]
        self._builder._pipeline.add_stage(aggregator, node_id=node_id, after=after)
        self._last_node = node_id
        return self

    def sink(self, sink: BaseSink, name: Optional[str] = None) -> "_BranchBuilder":
        """Add a sink stage to this branch.

        Args:
            sink: The sink stage.
            name: Optional node name override.

        Returns:
            Self for chaining.
        """
        node_id = name or f"{self._branch_name}_{sink.name}"
        after = [self._last_node] if self._last_node else [self._from_node]
        self._builder._pipeline.add_stage(sink, node_id=node_id, after=after)
        self._last_node = node_id
        return self

    def end(self, merge_into: Optional[str] = None) -> "PipelineBuilder":
        """End the branch and return to the main builder.

        Args:
            merge_into: Optional node to merge into. If None, merges with main line.

        Returns:
            The parent PipelineBuilder.
        """
        if self._last_node:
            self._builder._pending_merges.append(self._last_node)
        return self._builder


class PipelineBuilder:
    """Fluent API builder for constructing pipelines.

    Provides a chainable interface for building data pipelines with
    sources, transforms, validators, aggregators, and sinks.

    Supports:
        - Linear pipeline construction
        - Branching and merging (DAG)
        - Conditional stages (if/then/else)
        - Parallel stage execution
        - YAML/JSON configuration loading
    """

    def __init__(self, name: str = "pipeline") -> None:
        self.name = name
        self._pipeline = Pipeline(name=name)
        self._source_node: Optional[str] = None
        self._last_node: Optional[str] = None
        self._pending_merges: List[str] = []
        self._hooks = PipelineHooks()

    # --- Core stage adders ---

    def source(self, source: BaseSource, name: Optional[str] = None) -> "PipelineBuilder":
        """Add a source stage as the pipeline entry point.

        Args:
            source: The data source stage.
            name: Optional node name override.

        Returns:
            Self for chaining.

        Raises:
            ValueError: If a source has already been added.
        """
        if self._source_node is not None:
            raise ValueError("Pipeline already has a source stage")

        node_id = name or f"source_{source.name}"
        self._pipeline.add_stage(source, node_id=node_id)
        self._source_node = node_id
        self._last_node = node_id
        return self

    def transform(self, transform: BaseTransform, name: Optional[str] = None) -> "PipelineBuilder":
        """Add a transform stage to the pipeline.

        Args:
            transform: The transform stage.
            name: Optional node name override.

        Returns:
            Self for chaining.
        """
        node_id = name or f"transform_{transform.name}_{self._count_stages()}"
        after = [self._last_node] if self._last_node else None
        self._pipeline.add_stage(transform, node_id=node_id, after=after)
        self._last_node = node_id
        return self

    def validate(self, validator: BaseValidator, name: Optional[str] = None) -> "PipelineBuilder":
        """Add a validation stage to the pipeline.

        Args:
            validator: The validator stage.
            name: Optional node name override.

        Returns:
            Self for chaining.
        """
        node_id = name or f"validate_{validator.name}_{self._count_stages()}"
        after = [self._last_node] if self._last_node else None
        self._pipeline.add_stage(validator, node_id=node_id, after=after)
        self._last_node = node_id
        return self

    def aggregate(self, aggregator: BaseAggregator, name: Optional[str] = None) -> "PipelineBuilder":
        """Add an aggregation stage to the pipeline.

        Args:
            aggregator: The aggregator stage.
            name: Optional node name override.

        Returns:
            Self for chaining.
        """
        node_id = name or f"aggregate_{aggregator.name}_{self._count_stages()}"
        after = [self._last_node] if self._last_node else None
        self._pipeline.add_stage(aggregator, node_id=node_id, after=after)
        self._last_node = node_id
        return self

    def sink(self, sink: BaseSink, name: Optional[str] = None) -> "PipelineBuilder":
        """Add a sink stage to the pipeline.

        Args:
            sink: The sink stage.
            name: Optional node name override.

        Returns:
            Self for chaining.
        """
        node_id = name or f"sink_{sink.name}_{self._count_stages()}"
        after = [self._last_node] if self._last_node else None
        self._pipeline.add_stage(sink, node_id=node_id, after=after)
        self._last_node = node_id
        return self

    def stage(self, stage: PipelineStage, name: Optional[str] = None) -> "PipelineBuilder":
        """Add a generic stage to the pipeline.

        Args:
            stage: Any pipeline stage.
            name: Optional node name override.

        Returns:
            Self for chaining.
        """
        node_id = name or f"stage_{stage.name}_{self._count_stages()}"
        after = [self._last_node] if self._last_node else None
        self._pipeline.add_stage(stage, node_id=node_id, after=after)
        self._last_node = node_id
        return self

    # --- Branching ---

    def branch(self, branch_name: str) -> _BranchBuilder:
        """Create a new branch from the current point.

        The branch runs in parallel with the main pipeline path.
        Call .end() on the returned branch builder to merge back.

        Args:
            branch_name: Name for the branch.

        Returns:
            A _BranchBuilder for adding stages to the branch.
        """
        if self._last_node is None:
            raise ValueError("Cannot branch: no stages added yet")

        return _BranchBuilder(self, branch_name, self._last_node)

    def merge(self, merge_name: str = "merge") -> "PipelineBuilder":
        """Merge all pending branches into the main pipeline.

        Creates a join stage that merges outputs from all branches.

        Args:
            merge_name: Name for the merge node.

        Returns:
            Self for chaining.
        """
        if not self._pending_merges:
            return self

        # Create a merge/join stage
        merge_nodes = [self._last_node] + self._pending_merges
        join_stage = JoinTransform(
            left_key="__merge_key__",
            right_key="__merge_key__",
            join_type="outer",
            name=merge_name,
        )
        node_id = f"merge_{merge_name}_{self._count_stages()}"
        self._pipeline.add_stage(join_stage, node_id=node_id, after=merge_nodes)
        self._last_node = node_id
        self._pending_merges.clear()
        return self

    # --- Conditional stages ---

    def if_then_else(
        self,
        condition: Callable[[Records], bool],
        then_stages: List[PipelineStage],
        else_stages: Optional[List[PipelineStage]] = None,
        name: str = "conditional",
    ) -> "PipelineBuilder":
        """Add conditional stages based on a predicate.

        The condition is evaluated on the data at this point in the pipeline.
        If True, the then_stages are executed; otherwise, else_stages (if any).

        Note: This is implemented as a custom stage that internally routes data.

        Args:
            condition: Function that takes records and returns bool.
            then_stages: Stages to run if condition is True.
            else_stages: Stages to run if condition is False.
            name: Base name for the conditional stages.

        Returns:
            Self for chaining.
        """
        if self._last_node is None:
            raise ValueError("Cannot add conditional: no stages added yet")

        # Build a conditional stage
        class ConditionalStage(PipelineStage):
            def __init__(self, cond, then_pipe, else_pipe, name):
                super().__init__(name=name)
                self.condition = cond
                self.then_pipeline = then_pipe
                self.else_pipeline = else_pipe

            def process(self, data, context):
                if self.condition(data):
                    result = self.then_pipeline.run(data)
                    if result.failed:
                        raise result.context.errors[0] if result.context.errors else Exception(
                            "Then branch failed"
                        )
                    return result.output
                elif self.else_pipeline:
                    result = self.else_pipeline.run(data)
                    if result.failed:
                        raise result.context.errors[0] if result.context.errors else Exception(
                            "Else branch failed"
                        )
                    return result.output
                else:
                    return data

        then_pipeline = Pipeline.from_stages(then_stages, name=f"{name}_then")
        else_pipeline = (
            Pipeline.from_stages(else_stages, name=f"{name}_else")
            if else_stages
            else None
        )

        cond_stage = ConditionalStage(condition, then_pipeline, else_pipeline, name)
        node_id = f"cond_{name}_{self._count_stages()}"
        self._pipeline.add_stage(cond_stage, node_id=node_id, after=[self._last_node])
        self._last_node = node_id
        return self

    # --- Parallel stages ---

    def parallel(self, stages: List[PipelineStage], name: str = "parallel") -> "PipelineBuilder":
        """Run multiple stages in parallel (fan-out pattern).

        Each stage receives the same input and produces its own output.
        The outputs are collected into a dict keyed by stage name.

        Args:
            stages: List of stages to run in parallel.
            name: Base name for the parallel group.

        Returns:
            Self for chaining.
        """
        if self._last_node is None:
            raise ValueError("Cannot add parallel: no stages added yet")

        # Add each stage as a branch from the current point
        parallel_nodes = []
        for i, s in enumerate(stages):
            node_id = f"{name}_{i}_{s.name}"
            self._pipeline.add_stage(s, node_id=node_id, after=[self._last_node])
            parallel_nodes.append(node_id)

        # Add a collector stage that merges all parallel outputs
        class ParallelCollector(PipelineStage):
            def __init__(self, stage_names, name):
                super().__init__(name=name)
                self.stage_names = stage_names

            def process(self, data, context):
                # data is a dict keyed by predecessor node IDs
                return data

        collector = ParallelCollector([s.name for s in stages], f"{name}_collector")
        collector_id = f"{name}_collector_{self._count_stages()}"
        self._pipeline.add_stage(collector, node_id=collector_id, after=parallel_nodes)
        self._last_node = collector_id
        return self

    # --- Hooks ---

    def on_start(self, callback: Callable[[PipelineContext], None]) -> "PipelineBuilder":
        """Register an on_start hook.

        Args:
            callback: Hook callback.

        Returns:
            Self for chaining.
        """
        self._hooks.on_start(callback)
        return self

    def on_stage_start(
        self, callback: Callable[[str, PipelineContext], None]
    ) -> "PipelineBuilder":
        """Register an on_stage_start hook.

        Args:
            callback: Hook callback.

        Returns:
            Self for chaining.
        """
        self._hooks.on_stage_start(callback)
        return self

    def on_stage_end(
        self, callback: Callable[[str, Any, PipelineContext], None]
    ) -> "PipelineBuilder":
        """Register an on_stage_end hook.

        Args:
            callback: Hook callback.

        Returns:
            Self for chaining.
        """
        self._hooks.on_stage_end(callback)
        return self

    def on_end(self, callback: Callable[[PipelineResult], None]) -> "PipelineBuilder":
        """Register an on_end hook.

        Args:
            callback: Hook callback.

        Returns:
            Self for chaining.
        """
        self._hooks.on_end(callback)
        return self

    def on_error(
        self, callback: Callable[[Exception, PipelineContext], None]
    ) -> "PipelineBuilder":
        """Register an on_error hook.

        Args:
            callback: Hook callback.

        Returns:
            Self for chaining.
        """
        self._hooks.on_error(callback)
        return self

    # --- Build & run ---

    def build(self) -> Pipeline:
        """Build and return the pipeline.

        Returns:
            The constructed Pipeline instance.
        """
        # Merge any pending branches
        if self._pending_merges:
            self.merge()

        self._pipeline.hooks = self._hooks
        return self._pipeline

    def run(self, initial_data: Any = None) -> PipelineResult:
        """Build and immediately run the pipeline.

        Args:
            initial_data: Initial data to feed into the pipeline.

        Returns:
            PipelineResult with execution outcome.
        """
        pipeline = self.build()
        return pipeline.run(initial_data)

    # --- Config-based construction ---

    @classmethod
    def from_config(cls, config: Dict[str, Any]) -> "PipelineBuilder":
        """Build a pipeline from a configuration dictionary.

        Config format:
            {
                "name": "my_pipeline",
                "stages": [
                    {"type": "source.memory", "data": [...]},
                    {"type": "transform.filter", "field": "age", "value": 18, "operator": "ge"},
                    {"type": "sink.file", "file_path": "output.json"},
                ]
            }

        Args:
            config: Pipeline configuration dictionary.

        Returns:
            A new PipelineBuilder with the configured stages.
        """
        name = config.get("name", "pipeline")
        builder = cls(name)

        stages_config = config.get("stages", [])
        for stage_config in stages_config:
            stage = cls._create_stage_from_config(stage_config)
            builder.stage(stage)

        return builder

    @classmethod
    def from_yaml_file(cls, file_path: str) -> "PipelineBuilder":
        """Load a pipeline configuration from a YAML file.

        Args:
            file_path: Path to the YAML config file.

        Returns:
            A new PipelineBuilder.
        """
        import yaml

        with open(file_path, "r", encoding="utf-8") as f:
            config = yaml.safe_load(f)

        return cls.from_config(config)

    @classmethod
    def from_json_file(cls, file_path: str) -> "PipelineBuilder":
        """Load a pipeline configuration from a JSON file.

        Args:
            file_path: Path to the JSON config file.

        Returns:
            A new PipelineBuilder.
        """
        with open(file_path, "r", encoding="utf-8") as f:
            config = json.load(f)

        return cls.from_config(config)

    @classmethod
    def _create_stage_from_config(cls, config: Dict[str, Any]) -> PipelineStage:
        """Create a pipeline stage from a config dict.

        Args:
            config: Stage configuration with 'type' key.

        Returns:
            A PipelineStage instance.
        """
        stage_type = config.get("type", "")
        params = {k: v for k, v in config.items() if k != "type"}

        # Sources
        if stage_type == "source.memory":
            return MemorySource(**params)
        elif stage_type == "source.file":
            return FileSource(**params)
        elif stage_type == "source.database":
            return DatabaseSource(**params)
        elif stage_type == "source.api":
            return APISource(**params)

        # Transforms
        elif stage_type == "transform.filter":
            return FilterTransform(**params)
        elif stage_type == "transform.map":
            return MapTransform(**params)
        elif stage_type == "transform.reduce":
            return ReduceTransform(**params)
        elif stage_type == "transform.sort":
            return SortTransform(**params)
        elif stage_type == "transform.deduplicate":
            return DeduplicateTransform(**params)
        elif stage_type == "transform.rename":
            return RenameTransform(**params)
        elif stage_type == "transform.typecast":
            return TypeCastTransform(**params)
        elif stage_type == "transform.normalize":
            return NormalizeTransform(**params)
        elif stage_type == "transform.split":
            return SplitTransform(**params)

        # Validators
        elif stage_type == "validate.schema":
            return SchemaValidator(**params)
        elif stage_type == "validate.range":
            return RangeValidator(**params)
        elif stage_type == "validate.regex":
            return RegexValidator(**params)
        elif stage_type == "validate.required":
            return RequiredFieldsValidator(**params)
        elif stage_type == "validate.unique":
            return UniqueValidator(**params)
        elif stage_type == "validate.type":
            return TypeValidator(**params)

        # Aggregators
        elif stage_type == "aggregate.sum":
            return SumAggregator(**params)
        elif stage_type == "aggregate.average":
            return AverageAggregator(**params)
        elif stage_type == "aggregate.count":
            return CountAggregator(**params)
        elif stage_type == "aggregate.minmax":
            return MinMaxAggregator(**params)
        elif stage_type == "aggregate.groupby":
            return GroupByAggregator(**params)
        elif stage_type == "aggregate.percentile":
            return PercentileAggregator(**params)
        elif stage_type == "aggregate.moving_average":
            return MovingAverageAggregator(**params)
        elif stage_type == "aggregate.windowed":
            return WindowedAggregator(**params)

        # Sinks
        elif stage_type == "sink.file":
            return FileSink(**params)
        elif stage_type == "sink.database":
            return DatabaseSink(**params)
        elif stage_type == "sink.api":
            return APISink(**params)
        elif stage_type == "sink.memory":
            return MemorySink(**params)
        elif stage_type == "sink.console":
            return ConsoleSink(**params)

        else:
            raise ValueError(f"Unknown stage type: {stage_type}")

    def _count_stages(self) -> int:
        """Get the current number of stages (for unique naming)."""
        return self._pipeline.stage_count
