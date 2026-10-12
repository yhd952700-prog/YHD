"""
LiuHao AI OS Pipeline Component Library.

A comprehensive data pipeline framework with:
- Base pipeline abstractions with DAG support
- Data sources (file, database, API, stream, memory)
- Data transforms (filter, map, reduce, sort, dedup, etc.)
- Data sinks (file, database, API, memory, console, multi)
- Data validation (schema, range, regex, required, unique, type)
- Data aggregation (sum, avg, count, minmax, groupby, percentile, etc.)
- Fluent pipeline builder with branching, conditional, parallel stages
- Hook system for observability

Usage:
    from src.pipelines import (
        Pipeline, PipelineBuilder,
        MemorySource, FileSource,
        FilterTransform, MapTransform,
        FileSink, MemorySink,
        SchemaValidator,
        SumAggregator, AverageAggregator,
    )

    result = (PipelineBuilder("example")
        .source(MemorySource(data=records))
        .transform(FilterTransform(field="age", value=18, operator="ge"))
        .aggregate(SumAggregator(field="score"))
        .sink(MemorySink())
        .run())
"""

from .base import (
    DAGNode,
    GeneratorPipelineStage,
    Pipeline,
    PipelineContext,
    PipelineError,
    PipelineHooks,
    PipelineResult,
    PipelineStage,
    PipelineStatus,
)
from .sources import (
    APISource,
    BaseSource,
    DatabaseSource,
    FileSource,
    MemorySource,
    SourceStats,
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
    TransformStats,
    TypeCastTransform,
)
from .sinks import (
    APISink,
    BaseSink,
    ConsoleSink,
    DatabaseSink,
    FileSink,
    MemorySink,
    MultiSink,
    SinkStats,
)
from .validation import (
    BaseValidator,
    RangeValidator,
    RegexValidator,
    RequiredFieldsValidator,
    SchemaValidator,
    TypeValidator,
    UniqueValidator,
    ValidationIssue,
    ValidationReport,
)
from .aggregation import (
    AggregationResult,
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
from .builder import PipelineBuilder

__all__ = [
    # Base
    "Pipeline",
    "PipelineStage",
    "PipelineContext",
    "PipelineResult",
    "PipelineStatus",
    "PipelineError",
    "PipelineHooks",
    "DAGNode",
    "GeneratorPipelineStage",
    # Sources
    "BaseSource",
    "FileSource",
    "DatabaseSource",
    "APISource",
    "StreamSource",
    "MemorySource",
    "SourceStats",
    # Transforms
    "BaseTransform",
    "FilterTransform",
    "MapTransform",
    "ReduceTransform",
    "SortTransform",
    "DeduplicateTransform",
    "RenameTransform",
    "TypeCastTransform",
    "NormalizeTransform",
    "SplitTransform",
    "JoinTransform",
    "TransformStats",
    # Sinks
    "BaseSink",
    "FileSink",
    "DatabaseSink",
    "APISink",
    "MemorySink",
    "ConsoleSink",
    "MultiSink",
    "SinkStats",
    # Validation
    "BaseValidator",
    "SchemaValidator",
    "RangeValidator",
    "RegexValidator",
    "RequiredFieldsValidator",
    "UniqueValidator",
    "TypeValidator",
    "ValidationReport",
    "ValidationIssue",
    # Aggregation
    "BaseAggregator",
    "SumAggregator",
    "AverageAggregator",
    "CountAggregator",
    "MinMaxAggregator",
    "GroupByAggregator",
    "PercentileAggregator",
    "MovingAverageAggregator",
    "WindowedAggregator",
    "AggregationResult",
    # Builder
    "PipelineBuilder",
]

__version__ = "1.0.0"
