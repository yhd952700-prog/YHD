"""
Comprehensive tests for the LiuHao AI OS Pipeline Component Library.

Covers base pipeline components, sources, transforms, sinks,
validation, aggregation, and the pipeline builder.
"""

from __future__ import annotations

import json
import os
import tempfile
from typing import Dict, List

import pytest

from src.pipelines import (
    # Base
    Pipeline,
    PipelineContext,
    PipelineError,
    PipelineHooks,
    PipelineResult,
    PipelineStage,
    PipelineStatus,
    # Sources
    FileSource,
    MemorySource,
    StreamSource,
    # Transforms
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
    # Sinks
    ConsoleSink,
    FileSink,
    MemorySink,
    MultiSink,
    # Validation
    RangeValidator,
    RegexValidator,
    RequiredFieldsValidator,
    SchemaValidator,
    TypeValidator,
    UniqueValidator,
    ValidationReport,
    # Aggregation
    AverageAggregator,
    CountAggregator,
    GroupByAggregator,
    MinMaxAggregator,
    MovingAverageAggregator,
    PercentileAggregator,
    SumAggregator,
    WindowedAggregator,
    # Builder
    PipelineBuilder,
)


# =============================================================================
# Fixtures
# =============================================================================

@pytest.fixture
def sample_records() -> List[Dict]:
    """Sample test records."""
    return [
        {"id": 1, "name": "Alice", "age": 25, "score": 85.5, "city": "New York", "email": "alice@example.com"},
        {"id": 2, "name": "Bob", "age": 30, "score": 92.0, "city": "London", "email": "bob@example.com"},
        {"id": 3, "name": "Charlie", "age": 22, "score": 78.3, "city": "New York", "email": "charlie@test.org"},
        {"id": 4, "name": "Diana", "age": 35, "score": 95.7, "city": "Paris", "email": "diana@example.com"},
        {"id": 5, "name": "Eve", "age": 28, "score": 88.1, "city": "London", "email": "eve@test.org"},
    ]


@pytest.fixture
def empty_records() -> List[Dict]:
    """Empty record list."""
    return []


@pytest.fixture
def numeric_records() -> List[Dict]:
    """Records with numeric data for aggregation tests."""
    return [
        {"value": 10, "category": "A"},
        {"value": 20, "category": "A"},
        {"value": 30, "category": "B"},
        {"value": 40, "category": "B"},
        {"value": 50, "category": "B"},
    ]


# =============================================================================
# Base Pipeline Tests
# =============================================================================

class TestPipelineStatus:
    """Tests for PipelineStatus enum."""

    def test_status_values_exist(self):
        """All expected status values exist."""
        assert hasattr(PipelineStatus, "IDLE")
        assert hasattr(PipelineStatus, "RUNNING")
        assert hasattr(PipelineStatus, "COMPLETED")
        assert hasattr(PipelineStatus, "FAILED")
        assert hasattr(PipelineStatus, "PAUSED")

    def test_status_is_enum(self):
        """PipelineStatus is a proper enum."""
        from enum import Enum
        assert issubclass(PipelineStatus, Enum)


class TestPipelineContext:
    """Tests for PipelineContext."""

    def test_initial_state(self):
        """Context starts with empty state."""
        ctx = PipelineContext()
        assert ctx.data is None
        assert ctx.stage_history == []
        assert ctx.errors == []
        assert ctx.warnings == []
        assert ctx.variables == {}
        assert ctx.metadata == {}

    def test_mark_stage(self):
        """mark_stage records stage execution."""
        ctx = PipelineContext()
        ctx.mark_stage("stage1")
        ctx.mark_stage("stage2")
        assert ctx.stage_history == ["stage1", "stage2"]

    def test_add_error(self):
        """Errors are added correctly."""
        ctx = PipelineContext()
        err = PipelineError("test error")
        ctx.add_error(err)
        assert ctx.has_errors()
        assert len(ctx.errors) == 1

    def test_add_warning(self):
        """Warnings are added correctly."""
        ctx = PipelineContext()
        ctx.add_warning("test warning")
        assert len(ctx.warnings) == 1
        assert not ctx.has_errors()

    def test_variables(self):
        """Variables can be set and retrieved."""
        ctx = PipelineContext()
        ctx.set_variable("key1", "value1")
        assert ctx.get_variable("key1") == "value1"
        assert ctx.get_variable("nonexistent") is None
        assert ctx.get_variable("nonexistent", "default") == "default"

    def test_pipeline_id_unique(self):
        """Each context gets a unique pipeline ID."""
        ctx1 = PipelineContext()
        ctx2 = PipelineContext()
        assert ctx1.pipeline_id != ctx2.pipeline_id

    def test_elapsed(self):
        """Elapsed time is positive after creation."""
        import time
        ctx = PipelineContext()
        time.sleep(0.01)
        assert ctx.elapsed > 0


class TestPipelineError:
    """Tests for PipelineError."""

    def test_error_message(self):
        """Error message is set correctly."""
        err = PipelineError("something went wrong")
        assert str(err) == "something went wrong"

    def test_error_with_stage(self):
        """Stage name is included in string."""
        err = PipelineError("bad data", stage_name="filter")
        assert "filter" in str(err)

    def test_error_with_pipeline(self):
        """Pipeline name is included in string."""
        err = PipelineError("failed", pipeline_name="my_pipe")
        assert "my_pipe" in str(err)

    def test_original_error(self):
        """Original error is preserved."""
        original = ValueError("root cause")
        err = PipelineError("wrapper", original_error=original)
        assert err.original_error is original


class TestPipelineHooks:
    """Tests for the PipelineHooks system."""

    def test_register_and_fire(self):
        """Hooks can be registered and fired."""
        hooks = PipelineHooks()
        calls = []

        def callback(ctx):
            calls.append("on_start")

        hooks.register("on_start", callback)
        hooks.fire("on_start", PipelineContext())
        assert len(calls) == 1

    def test_unregister(self):
        """Hooks can be unregistered."""
        hooks = PipelineHooks()
        calls = []

        def callback(ctx):
            calls.append("called")

        hooks.register("on_start", callback)
        hooks.fire("on_start", PipelineContext())
        assert len(calls) == 1

        assert hooks.unregister("on_start", callback) is True
        hooks.fire("on_start", PipelineContext())
        assert len(calls) == 1  # No additional calls

    def test_unknown_hook_register(self):
        """Registering unknown hook raises ValueError."""
        hooks = PipelineHooks()
        with pytest.raises(ValueError):
            hooks.register("nonexistent", lambda: None)

    def test_fluent_on_start(self):
        """Fluent on_start registration works."""
        hooks = PipelineHooks()
        result = hooks.on_start(lambda ctx: None)
        assert result is hooks

    def test_hook_error_does_not_propagate(self):
        """Errors in hook callbacks don't propagate."""
        hooks = PipelineHooks()

        def bad_callback(ctx):
            raise RuntimeError("hook failed")

        hooks.register("on_start", bad_callback)
        # Should not raise
        hooks.fire("on_start", PipelineContext())


class TestPipelineResult:
    """Tests for PipelineResult."""

    def test_success_property(self):
        """success is True when status is COMPLETED and no errors."""
        ctx = PipelineContext()
        result = PipelineResult(
            status=PipelineStatus.COMPLETED,
            context=ctx,
            error_count=0,
        )
        assert result.success is True

    def test_failed_property_with_errors(self):
        """failed is True when there are errors."""
        ctx = PipelineContext()
        result = PipelineResult(
            status=PipelineStatus.COMPLETED,
            context=ctx,
            error_count=1,
        )
        assert result.failed is True

    def test_failed_property_failed_status(self):
        """failed is True when status is FAILED."""
        ctx = PipelineContext()
        result = PipelineResult(
            status=PipelineStatus.FAILED,
            context=ctx,
            error_count=0,
        )
        assert result.failed is True


class TestPipelineStage:
    """Tests for PipelineStage abstract base."""

    def test_cannot_instantiate_abstract(self):
        """Abstract stage cannot be instantiated directly."""
        with pytest.raises(TypeError):
            PipelineStage()  # type: ignore

    def test_concrete_stage(self):
        """Concrete stage implementation works."""
        class DoubleStage(PipelineStage):
            def process(self, data, context):
                return [r * 2 for r in data]

        stage = DoubleStage(name="double")
        ctx = PipelineContext()
        result = stage([1, 2, 3], ctx)
        assert result == [2, 4, 6]
        assert stage.status == PipelineStatus.COMPLETED
        assert "double" in ctx.stage_history

    def test_stage_wraps_exception(self):
        """Stage exceptions are wrapped in PipelineError."""
        class BadStage(PipelineStage):
            def process(self, data, context):
                raise ValueError("bad value")

        stage = BadStage(name="bad")
        ctx = PipelineContext()
        with pytest.raises(PipelineError) as exc_info:
            stage(None, ctx)
        assert "bad value" in str(exc_info.value)
        assert exc_info.value.stage_name == "bad"
        assert stage.status == PipelineStatus.FAILED

    def test_stage_setup_teardown(self):
        """Setup and teardown are called."""
        calls = []

        class TrackedStage(PipelineStage):
            def setup(self, context):
                calls.append("setup")
            def process(self, data, context):
                calls.append("process")
                return data
            def teardown(self, context):
                calls.append("teardown")

        stage = TrackedStage(name="tracked")
        stage(None, PipelineContext())
        assert calls == ["setup", "process", "teardown"]


class TestPipelineDAG:
    """Tests for Pipeline DAG construction and execution."""

    def test_add_stage(self):
        """Stages can be added to the pipeline."""
        class NoOp(PipelineStage):
            def process(self, data, context):
                return data

        p = Pipeline(name="test")
        nid = p.add_stage(NoOp(name="s1"))
        assert nid == "s1"
        assert p.stage_count == 1

    def test_linear_pipeline_from_stages(self):
        """from_stages creates a linear pipeline."""
        class AddOne(PipelineStage):
            def process(self, data, context):
                return [x + 1 for x in data]

        class Double(PipelineStage):
            def process(self, data, context):
                return [x * 2 for x in data]

        p = Pipeline.from_stages([AddOne(name="add"), Double(name="double")], name="linear")
        result = p.run([1, 2, 3])
        assert result.success
        assert result.output == [4, 6, 8]  # (1+1)*2=4, etc.

    def test_dag_cycle_detection(self):
        """Cycles are detected and raise ValueError."""
        class NoOp(PipelineStage):
            def process(self, data, context):
                return data

        p = Pipeline(name="cycle_test")
        p.add_stage(NoOp(name="a"))
        p.add_stage(NoOp(name="b"), after=["a"])
        with pytest.raises(ValueError, match="Cycle"):
            p.connect("b", "a")

    def test_topological_order(self):
        """Topological order is correct."""
        class NoOp(PipelineStage):
            def process(self, data, context):
                return data

        p = Pipeline(name="topo")
        p.add_stage(NoOp(name="a"))
        p.add_stage(NoOp(name="b"), after=["a"])
        p.add_stage(NoOp(name="c"), after=["a"])
        p.add_stage(NoOp(name="d"), after=["b", "c"])

        order = p.topological_order()
        assert order.index("a") < order.index("b")
        assert order.index("a") < order.index("c")
        assert order.index("b") < order.index("d")
        assert order.index("c") < order.index("d")

    def test_pipeline_run_success(self):
        """Pipeline runs successfully with correct result."""
        class Upper(PipelineStage):
            def process(self, data, context):
                return [s.upper() for s in data]

        p = Pipeline.from_stages([Upper(name="upper")], name="test")
        result = p.run(["hello", "world"])
        assert result.success
        assert result.output == ["HELLO", "WORLD"]
        assert result.stages_completed == 1
        assert result.duration_seconds >= 0

    def test_pipeline_run_failure(self):
        """Pipeline failure is properly reported."""
        class Bad(PipelineStage):
            def process(self, data, context):
                raise PipelineError("intentional failure")

        p = Pipeline.from_stages([Bad(name="bad")], name="failing")
        result = p.run(None)
        assert result.failed
        assert result.status == PipelineStatus.FAILED
        assert result.error_count > 0

    def test_pipeline_hooks_fire(self):
        """Pipeline hooks are fired during execution."""
        class NoOp(PipelineStage):
            def process(self, data, context):
                return data

        events = []
        hooks = PipelineHooks()
        hooks.on_start(lambda ctx: events.append("start"))
        hooks.on_stage_start(lambda name, ctx: events.append(f"stage_start:{name}"))
        hooks.on_stage_end(lambda name, result, ctx: events.append(f"stage_end:{name}"))
        hooks.on_end(lambda result: events.append("end"))

        p = Pipeline.from_stages([NoOp(name="s1")], name="hook_test", hooks=hooks)
        p.run(None)

        assert "start" in events
        assert "stage_start:s1" in events
        assert "stage_end:s1" in events
        assert "end" in events


# =============================================================================
# Source Tests
# =============================================================================

class TestMemorySource:
    """Tests for MemorySource."""

    def test_read_list_of_dicts(self, sample_records):
        """Reading a list of dicts works."""
        source = MemorySource(data=sample_records)
        result = source.read()
        assert len(result) == 5
        assert result[0]["name"] == "Alice"

    def test_read_single_dict(self):
        """Reading a single dict wraps it in a list."""
        source = MemorySource(data={"key": "value"})
        result = source.read()
        assert len(result) == 1
        assert result[0]["key"] == "value"

    def test_read_none(self):
        """Reading None returns empty list."""
        source = MemorySource(data=None)
        result = source.read()
        assert result == []

    def test_has_more_and_batch(self):
        """Batch reading and has_more work correctly."""
        source = MemorySource(data=[{"i": i} for i in range(10)], batch_size=3)
        source.open()
        all_records = []
        while source.has_more():
            batch = source.read_batch(3)
            all_records.extend(batch)
        assert len(all_records) == 10
        source.close()

    def test_as_pipeline_stage(self):
        """MemorySource works as a pipeline stage."""
        source = MemorySource(data=[{"a": 1}, {"a": 2}])
        ctx = PipelineContext()
        result = source(None, ctx)
        assert len(result) == 2


class TestFileSource:
    """Tests for FileSource."""

    def test_read_json_file(self, sample_records):
        """Reading a JSON file works."""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            json.dump(sample_records, f)
            temp_path = f.name

        try:
            source = FileSource(file_path=temp_path, file_format="json")
            result = source.read()
            source.close()
            assert len(result) == 5
            assert result[0]["name"] == "Alice"
        finally:
            try:
                os.unlink(temp_path)
            except PermissionError:
                pass

    def test_read_csv_file(self):
        """Reading a CSV file works."""
        csv_content = "name,age\nAlice,25\nBob,30\n"
        with tempfile.NamedTemporaryFile(mode="w", suffix=".csv", delete=False) as f:
            f.write(csv_content)
            temp_path = f.name

        try:
            source = FileSource(file_path=temp_path, file_format="csv")
            result = source.read()
            source.close()
            assert len(result) == 2
            assert result[0]["name"] == "Alice"
            assert result[0]["age"] == "25"
        finally:
            try:
                os.unlink(temp_path)
            except PermissionError:
                pass

    def test_read_jsonl_file(self):
        """Reading a JSONL file works."""
        lines = [
            json.dumps({"id": 1, "val": "a"}),
            json.dumps({"id": 2, "val": "b"}),
        ]
        with tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False) as f:
            f.write("\n".join(lines))
            temp_path = f.name

        try:
            source = FileSource(file_path=temp_path, file_format="jsonl")
            result = source.read()
            source.close()
            assert len(result) == 2
            assert result[0]["id"] == 1
        finally:
            try:
                os.unlink(temp_path)
            except PermissionError:
                pass

    def test_read_txt_file(self):
        """Reading a text file works."""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as f:
            f.write("line1\nline2\nline3\n")
            temp_path = f.name

        try:
            source = FileSource(file_path=temp_path, file_format="txt")
            result = source.read()
            source.close()
            assert len(result) == 3
            assert result[0]["content"] == "line1"
        finally:
            try:
                os.unlink(temp_path)
            except PermissionError:
                pass

    def test_file_not_found(self):
        """Nonexistent file raises PipelineError."""
        source = FileSource(file_path="/nonexistent/file.json")
        with pytest.raises(PipelineError, match="File not found"):
            source.open()


class TestStreamSource:
    """Tests for StreamSource."""

    def test_read_from_iterator(self):
        """Reading from an iterator works."""
        def gen():
            for i in range(5):
                yield {"index": i}

        source = StreamSource(iterator=gen())
        result = source.read_batch(3)
        assert len(result) == 3
        assert result[0]["index"] == 0

    def test_has_more(self):
        """has_more works for streams."""
        def gen():
            for i in range(3):
                yield {"i": i}

        source = StreamSource(iterator=gen())
        source.open()
        assert source.has_more()
        source.read_batch(10)
        assert not source.has_more()
        source.close()


# =============================================================================
# Transform Tests
# =============================================================================

class TestFilterTransform:
    """Tests for FilterTransform."""

    def test_filter_predicate(self, sample_records):
        """Filter with a predicate function works."""
        transform = FilterTransform(predicate=lambda r: r["age"] > 25)
        ctx = PipelineContext()
        result = transform(sample_records, ctx)
        assert len(result) == 3  # Bob, Diana, Eve
        assert all(r["age"] > 25 for r in result)

    def test_filter_field_eq(self, sample_records):
        """Filter with field eq operator works."""
        transform = FilterTransform(field="city", value="London", operator="eq")
        ctx = PipelineContext()
        result = transform(sample_records, ctx)
        assert len(result) == 2
        assert all(r["city"] == "London" for r in result)

    def test_filter_field_ge(self, sample_records):
        """Filter with ge operator works."""
        transform = FilterTransform(field="age", value=30, operator="ge")
        ctx = PipelineContext()
        result = transform(sample_records, ctx)
        assert len(result) == 2  # Bob (30), Diana (35)

    def test_filter_contains(self, sample_records):
        """Filter with contains operator works."""
        transform = FilterTransform(field="email", value="example.com", operator="contains")
        ctx = PipelineContext()
        result = transform(sample_records, ctx)
        assert len(result) == 3  # Alice, Bob, Diana

    def test_filter_empty_result(self, sample_records):
        """Filter that matches nothing returns empty list."""
        transform = FilterTransform(field="age", value=100, operator="gt")
        ctx = PipelineContext()
        result = transform(sample_records, ctx)
        assert result == []


class TestMapTransform:
    """Tests for MapTransform."""

    def test_map_add_field(self, sample_records):
        """Adding a computed field works."""
        transform = MapTransform(
            add_field="age_next_year",
            value_func=lambda r: r["age"] + 1,
        )
        ctx = PipelineContext()
        result = transform(sample_records, ctx)
        assert len(result) == 5
        assert result[0]["age_next_year"] == 26
        # Original fields preserved
        assert result[0]["age"] == 25

    def test_map_custom_mapper(self, sample_records):
        """Custom mapper function works."""
        transform = MapTransform(
            mapper=lambda r: {**r, "name_upper": r["name"].upper()}
        )
        ctx = PipelineContext()
        result = transform(sample_records, ctx)
        assert result[0]["name_upper"] == "ALICE"

    def test_map_preserves_original_count(self, sample_records):
        """Map preserves record count."""
        transform = MapTransform(add_field="x", value_func=lambda r: 1)
        ctx = PipelineContext()
        result = transform(sample_records, ctx)
        assert len(result) == len(sample_records)


class TestSortTransform:
    """Tests for SortTransform."""

    def test_sort_ascending(self, sample_records):
        """Sort ascending by field works."""
        transform = SortTransform(key="age")
        ctx = PipelineContext()
        result = transform(sample_records, ctx)
        ages = [r["age"] for r in result]
        assert ages == sorted(ages)

    def test_sort_descending(self, sample_records):
        """Sort descending works."""
        transform = SortTransform(key="score", reverse=True)
        ctx = PipelineContext()
        result = transform(sample_records, ctx)
        scores = [r["score"] for r in result]
        assert scores == sorted(scores, reverse=True)

    def test_sort_multiple_keys(self):
        """Sort by multiple keys works."""
        records = [
            {"a": 1, "b": 3},
            {"a": 1, "b": 1},
            {"a": 2, "b": 2},
        ]
        transform = SortTransform(key=["a", "b"])
        ctx = PipelineContext()
        result = transform(records, ctx)
        assert result[0]["a"] == 1 and result[0]["b"] == 1
        assert result[1]["a"] == 1 and result[1]["b"] == 3
        assert result[2]["a"] == 2 and result[2]["b"] == 2


class TestDeduplicateTransform:
    """Tests for DeduplicateTransform."""

    def test_dedup_single_field(self):
        """Deduplication by single field works."""
        records = [
            {"id": 1, "val": "a"},
            {"id": 2, "val": "b"},
            {"id": 1, "val": "a2"},  # duplicate id
        ]
        transform = DeduplicateTransform(key="id")
        ctx = PipelineContext()
        result = transform(records, ctx)
        assert len(result) == 2
        assert result[0]["id"] == 1
        assert result[0]["val"] == "a"  # keeps first

    def test_dedup_keep_last(self):
        """Dedup with keep='last' keeps last occurrence."""
        records = [
            {"id": 1, "val": "first"},
            {"id": 1, "val": "last"},
        ]
        transform = DeduplicateTransform(key="id", keep="last")
        ctx = PipelineContext()
        result = transform(records, ctx)
        assert len(result) == 1
        assert result[0]["val"] == "last"

    def test_dedup_entire_record(self):
        """Dedup by entire record works."""
        records = [
            {"a": 1, "b": 2},
            {"a": 1, "b": 2},
            {"a": 3, "b": 4},
        ]
        transform = DeduplicateTransform()
        ctx = PipelineContext()
        result = transform(records, ctx)
        assert len(result) == 2


class TestRenameTransform:
    """Tests for RenameTransform."""

    def test_rename_fields(self):
        """Renaming fields works."""
        records = [{"old_name": "value", "keep": "me"}]
        transform = RenameTransform(mapping={"old_name": "new_name"})
        ctx = PipelineContext()
        result = transform(records, ctx)
        assert "new_name" in result[0]
        assert "old_name" not in result[0]
        assert result[0]["new_name"] == "value"

    def test_rename_keep_original(self):
        """keep_original preserves old fields."""
        records = [{"a": 1}]
        transform = RenameTransform(mapping={"a": "b"}, keep_original=True)
        ctx = PipelineContext()
        result = transform(records, ctx)
        assert result[0]["a"] == 1
        assert result[0]["b"] == 1


class TestTypeCastTransform:
    """Tests for TypeCastTransform."""

    def test_cast_int_to_str(self):
        """Casting int to string works."""
        records = [{"num": 42}]
        transform = TypeCastTransform(type_map={"num": str})
        ctx = PipelineContext()
        result = transform(records, ctx)
        assert isinstance(result[0]["num"], str)
        assert result[0]["num"] == "42"

    def test_cast_str_to_int(self):
        """Casting string to int works."""
        records = [{"num": "42"}]
        transform = TypeCastTransform(type_map={"num": int})
        ctx = PipelineContext()
        result = transform(records, ctx)
        assert isinstance(result[0]["num"], int)
        assert result[0]["num"] == 42

    def test_cast_invalid_strict(self):
        """Strict mode raises on invalid cast."""
        records = [{"num": "not_a_number"}]
        transform = TypeCastTransform(type_map={"num": int}, strict=True)
        ctx = PipelineContext()
        with pytest.raises(PipelineError):
            transform(records, ctx)


class TestNormalizeTransform:
    """Tests for NormalizeTransform."""

    def test_normalize_list_of_fields(self):
        """Normalization with list of fields works."""
        records = [{"val": 0}, {"val": 50}, {"val": 100}]
        transform = NormalizeTransform(fields=["val"])
        ctx = PipelineContext()
        result = transform(records, ctx)
        assert result[0]["val_norm"] == 0.0
        assert result[1]["val_norm"] == 0.5
        assert result[2]["val_norm"] == 1.0

    def test_normalize_with_min_max_dict(self):
        """Normalization with explicit min/max works."""
        records = [{"val": 10}, {"val": 20}]
        transform = NormalizeTransform(fields={"val": (0, 40)})
        ctx = PipelineContext()
        result = transform(records, ctx)
        assert result[0]["val_norm"] == 0.25
        assert result[1]["val_norm"] == 0.5


class TestSplitTransform:
    """Tests for SplitTransform."""

    def test_split_field(self):
        """Splitting a field works."""
        records = [{"name": "John Doe"}]
        transform = SplitTransform(
            source_field="name",
            target_fields=["first", "last"],
            delimiter=" ",
        )
        ctx = PipelineContext()
        result = transform(records, ctx)
        assert result[0]["first"] == "John"
        assert result[0]["last"] == "Doe"

    def test_split_keep_source(self):
        """keep_source preserves original field."""
        records = [{"data": "a,b,c"}]
        transform = SplitTransform(
            source_field="data",
            target_fields=["x", "y", "z"],
            keep_source=True,
        )
        ctx = PipelineContext()
        result = transform(records, ctx)
        assert "data" in result[0]
        assert result[0]["x"] == "a"


class TestReduceTransform:
    """Tests for ReduceTransform."""

    def test_group_by_with_sum(self, numeric_records):
        """Group by with sum aggregation works."""
        transform = ReduceTransform(
            group_by="category",
            aggregations={"total": ("value", sum)},
        )
        ctx = PipelineContext()
        result = transform(numeric_records, ctx)
        assert len(result) == 2
        cat_a = [r for r in result if r["category"] == "A"][0]
        cat_b = [r for r in result if r["category"] == "B"][0]
        assert cat_a["total"] == 30  # 10 + 20
        assert cat_b["total"] == 120  # 30 + 40 + 50

    def test_reduce_all(self, numeric_records):
        """Reducing all records to single works."""
        transform = ReduceTransform(
            aggregations={"total": ("value", sum)},
        )
        ctx = PipelineContext()
        result = transform(numeric_records, ctx)
        assert len(result) == 1
        assert result[0]["total"] == 150


# =============================================================================
# Sink Tests
# =============================================================================

class TestMemorySink:
    """Tests for MemorySink."""

    def test_write_records(self, sample_records):
        """Writing records to memory sink works."""
        sink = MemorySink()
        sink.open()
        count = sink.write_batch(sample_records)
        sink.close()
        assert count == 5
        assert len(sink.data) == 5

    def test_data_passthrough(self, sample_records):
        """Sink passes data through as a pipeline stage."""
        sink = MemorySink()
        ctx = PipelineContext()
        result = sink(sample_records, ctx)
        assert result == sample_records
        assert len(sink.data) == 5

    def test_clear(self, sample_records):
        """Clear empties the sink."""
        sink = MemorySink()
        sink.open()
        sink.write_batch(sample_records)
        assert len(sink.data) == 5
        sink.clear()
        assert len(sink.data) == 0
        sink.close()


class TestFileSink:
    """Tests for FileSink."""

    def test_write_json(self, sample_records):
        """Writing JSON file works."""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            temp_path = f.name

        try:
            sink = FileSink(file_path=temp_path, file_format="json")
            sink.open()
            sink.write_batch(sample_records)
            sink.close()

            with open(temp_path, "r") as f:
                data = json.load(f)
            assert len(data) == 5
            assert data[0]["name"] == "Alice"
        finally:
            os.unlink(temp_path)

    def test_write_jsonl(self, sample_records):
        """Writing JSONL file works."""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False) as f:
            temp_path = f.name

        try:
            sink = FileSink(file_path=temp_path, file_format="jsonl")
            sink.open()
            sink.write_batch(sample_records)
            sink.close()

            with open(temp_path, "r") as f:
                lines = [json.loads(line) for line in f if line.strip()]
            assert len(lines) == 5
        finally:
            os.unlink(temp_path)

    def test_write_csv(self):
        """Writing CSV file works."""
        records = [{"name": "Alice", "age": "25"}, {"name": "Bob", "age": "30"}]
        with tempfile.NamedTemporaryFile(mode="w", suffix=".csv", delete=False) as f:
            temp_path = f.name

        try:
            sink = FileSink(file_path=temp_path, file_format="csv")
            sink.open()
            sink.write_batch(records)
            sink.close()

            with open(temp_path, "r") as f:
                lines = f.readlines()
            assert len(lines) == 3  # header + 2 rows
            assert "name" in lines[0]
        finally:
            os.unlink(temp_path)


class TestMultiSink:
    """Tests for MultiSink."""

    def test_writes_to_all_sinks(self, sample_records):
        """MultiSink writes to all child sinks."""
        sink1 = MemorySink()
        sink2 = MemorySink()
        multi = MultiSink(sinks=[sink1, sink2])

        multi.open()
        multi.write_batch(sample_records)
        multi.close()

        assert len(sink1.data) == 5
        assert len(sink2.data) == 5


class TestConsoleSink:
    """Tests for ConsoleSink."""

    def test_writes_to_stdout(self, sample_records, capsys):
        """ConsoleSink writes to stdout."""
        sink = ConsoleSink(output_format="json", pretty_print=False)
        sink.open()
        sink.write_batch(sample_records[:2])
        sink.close()

        captured = capsys.readouterr()
        assert "Alice" in captured.out


# =============================================================================
# Validation Tests
# =============================================================================

class TestRequiredFieldsValidator:
    """Tests for RequiredFieldsValidator."""

    def test_all_present(self, sample_records):
        """All required fields present -> no errors."""
        validator = RequiredFieldsValidator(required_fields=["id", "name", "age"])
        report = ValidationReport()
        validator.validate(sample_records, report)
        assert report.is_valid
        assert report.error_count == 0

    def test_missing_field(self):
        """Missing required field generates error."""
        records = [{"id": 1, "name": "Alice"}, {"id": 2}]  # missing name in second
        validator = RequiredFieldsValidator(required_fields=["id", "name"])
        report = ValidationReport()
        validator.validate(records, report)
        assert not report.is_valid
        assert report.error_count == 1
        assert report.errors[0].field == "name"
        assert report.errors[0].record_index == 1

    def test_null_field(self):
        """Null required field generates error."""
        records = [{"id": 1, "name": None}]
        validator = RequiredFieldsValidator(required_fields=["id", "name"])
        report = ValidationReport()
        validator.validate(records, report)
        assert report.error_count == 1


class TestTypeValidator:
    """Tests for TypeValidator."""

    def test_correct_types(self, sample_records):
        """Correct types -> no errors."""
        validator = TypeValidator(type_map={"id": int, "name": str, "age": int})
        report = ValidationReport()
        validator.validate(sample_records, report)
        assert report.is_valid

    def test_wrong_type(self):
        """Wrong type generates error."""
        records = [{"age": "twenty"}]
        validator = TypeValidator(type_map={"age": int})
        report = ValidationReport()
        validator.validate(records, report)
        assert report.error_count == 1
        assert "age" in report.errors[0].message

    def test_allow_null(self):
        """Null values are allowed by default."""
        records = [{"age": None}]
        validator = TypeValidator(type_map={"age": int})
        report = ValidationReport()
        validator.validate(records, report)
        assert report.is_valid


class TestRangeValidator:
    """Tests for RangeValidator."""

    def test_within_range(self):
        """Values within range -> no errors."""
        records = [{"val": 5}, {"val": 10}, {"val": 15}]
        validator = RangeValidator(ranges={"val": (0, 20)})
        report = ValidationReport()
        validator.validate(records, report)
        assert report.is_valid

    def test_below_min(self):
        """Values below minimum generate error."""
        records = [{"val": -5}]
        validator = RangeValidator(ranges={"val": (0, 100)})
        report = ValidationReport()
        validator.validate(records, report)
        assert report.error_count == 1

    def test_above_max(self):
        """Values above maximum generate error."""
        records = [{"val": 200}]
        validator = RangeValidator(ranges={"val": (0, 100)})
        report = ValidationReport()
        validator.validate(records, report)
        assert report.error_count == 1


class TestRegexValidator:
    """Tests for RegexValidator."""

    def test_valid_pattern(self):
        """Values matching pattern -> no errors."""
        records = [{"email": "test@example.com"}, {"email": "a@b.org"}]
        validator = RegexValidator(
            patterns={"email": r"[\w.-]+@[\w.-]+\.\w+"}
        )
        report = ValidationReport()
        validator.validate(records, report)
        assert report.is_valid

    def test_invalid_pattern(self):
        """Non-matching values generate error."""
        records = [{"email": "not_an_email"}]
        validator = RegexValidator(
            patterns={"email": r"[\w.-]+@[\w.-]+\.\w+"}
        )
        report = ValidationReport()
        validator.validate(records, report)
        assert report.error_count == 1


class TestUniqueValidator:
    """Tests for UniqueValidator."""

    def test_all_unique(self, sample_records):
        """All unique values -> no errors."""
        validator = UniqueValidator(fields="id")
        report = ValidationReport()
        validator.validate(sample_records, report)
        assert report.is_valid

    def test_duplicate(self):
        """Duplicate values generate errors."""
        records = [{"id": 1}, {"id": 2}, {"id": 1}]
        validator = UniqueValidator(fields="id")
        report = ValidationReport()
        validator.validate(records, report)
        assert report.error_count == 1

    def test_composite_key(self):
        """Composite key uniqueness works."""
        records = [
            {"first": "John", "last": "Doe"},
            {"first": "Jane", "last": "Doe"},
            {"first": "John", "last": "Doe"},  # duplicate
        ]
        validator = UniqueValidator(fields=["first", "last"])
        report = ValidationReport()
        validator.validate(records, report)
        assert report.error_count == 1


class TestSchemaValidator:
    """Tests for SchemaValidator."""

    def test_full_schema_validation(self, sample_records):
        """Full schema validation works."""
        schema = {
            "fields": {
                "id": {"type": "int", "required": True, "min": 1},
                "name": {"type": "str", "required": True, "min_length": 1},
                "age": {"type": "int", "required": True, "min": 0, "max": 150},
                "email": {"type": "str", "required": False},
            }
        }
        validator = SchemaValidator(schema=schema)
        report = ValidationReport()
        validator.validate(sample_records, report)
        assert report.is_valid

    def test_schema_with_enum(self):
        """Schema enum validation works."""
        schema = {
            "fields": {
                "status": {"type": "str", "required": True, "enum": ["active", "inactive"]},
            }
        }
        records = [{"status": "active"}, {"status": "unknown"}]
        validator = SchemaValidator(schema=schema)
        report = ValidationReport()
        validator.validate(records, report)
        assert report.error_count == 1


# =============================================================================
# Aggregation Tests
# =============================================================================

class TestSumAggregator:
    """Tests for SumAggregator."""

    def test_sum_simple(self, numeric_records):
        """Sum of all values works."""
        agg = SumAggregator(field="value")
        ctx = PipelineContext()
        result = agg(numeric_records, ctx)
        assert result[0]["value_sum"] == 150

    def test_sum_grouped(self, numeric_records):
        """Sum grouped by category works."""
        agg = SumAggregator(field="value", group_by="category")
        ctx = PipelineContext()
        result = agg(numeric_records, ctx)
        groups = {r["category"]: r["value_sum"] for r in result}
        assert groups["A"] == 30
        assert groups["B"] == 120


class TestAverageAggregator:
    """Tests for AverageAggregator."""

    def test_avg_simple(self, numeric_records):
        """Average of all values works."""
        agg = AverageAggregator(field="value")
        ctx = PipelineContext()
        result = agg(numeric_records, ctx)
        assert result[0]["value_avg"] == 30.0  # (10+20+30+40+50)/5 = 30

    def test_avg_grouped(self, numeric_records):
        """Average grouped by category works."""
        agg = AverageAggregator(field="value", group_by="category")
        ctx = PipelineContext()
        result = agg(numeric_records, ctx)
        groups = {r["category"]: r["value_avg"] for r in result}
        assert groups["A"] == 15.0
        assert groups["B"] == 40.0


class TestCountAggregator:
    """Tests for CountAggregator."""

    def test_count_all(self, numeric_records):
        """Counting all records works."""
        agg = CountAggregator()
        ctx = PipelineContext()
        result = agg(numeric_records, ctx)
        assert result[0]["count"] == 5

    def test_count_grouped(self, numeric_records):
        """Counting grouped by category works."""
        agg = CountAggregator(group_by="category")
        ctx = PipelineContext()
        result = agg(numeric_records, ctx)
        groups = {r["category"]: r["count"] for r in result}
        assert groups["A"] == 2
        assert groups["B"] == 3


class TestMinMaxAggregator:
    """Tests for MinMaxAggregator."""

    def test_minmax(self, numeric_records):
        """Min and max computation works."""
        agg = MinMaxAggregator(field="value")
        ctx = PipelineContext()
        result = agg(numeric_records, ctx)
        assert result[0]["value_min"] == 10
        assert result[0]["value_max"] == 50


class TestPercentileAggregator:
    """Tests for PercentileAggregator."""

    def test_percentiles(self):
        """Percentile computation works."""
        records = [{"v": i} for i in range(1, 101)]  # 1 to 100
        agg = PercentileAggregator(field="v", percentiles=[50, 90, 99])
        ctx = PipelineContext()
        result = agg(records, ctx)
        assert result[0]["p50"] == 50
        assert result[0]["p90"] == 90
        assert result[0]["p99"] == 99


class TestGroupByAggregator:
    """Tests for GroupByAggregator."""

    def test_groupby_multiple_aggs(self, numeric_records):
        """Group by with multiple aggregations works."""
        agg = GroupByAggregator(
            group_by="category",
            aggregations={
                "total": ("value", "sum"),
                "avg_val": ("value", "avg"),
                "min_val": ("value", "min"),
            },
        )
        ctx = PipelineContext()
        result = agg(numeric_records, ctx)
        assert len(result) == 2
        cat_a = [r for r in result if r["category"] == "A"][0]
        assert cat_a["total"] == 30
        assert cat_a["avg_val"] == 15.0
        assert cat_a["min_val"] == 10


class TestMovingAverageAggregator:
    """Tests for MovingAverageAggregator."""

    def test_moving_average(self):
        """Moving average computation works."""
        records = [{"v": i} for i in range(1, 6)]  # [1, 2, 3, 4, 5]
        agg = MovingAverageAggregator(field="v", window_size=3, min_periods=1)
        ctx = PipelineContext()
        result = agg(records, ctx)
        assert len(result) == 5
        assert result[0]["v_ma3"] == 1.0  # [1]
        assert result[1]["v_ma3"] == 1.5  # [1, 2]
        assert result[2]["v_ma3"] == 2.0  # [1, 2, 3]
        assert result[3]["v_ma3"] == 3.0  # [2, 3, 4]
        assert result[4]["v_ma3"] == 4.0  # [3, 4, 5]


class TestWindowedAggregator:
    """Tests for WindowedAggregator."""

    def test_tumbling_window(self):
        """Tumbling window aggregation works."""
        records = [{"v": i} for i in range(10)]
        agg = WindowedAggregator(
            field="v",
            window_type="tumbling",
            window_size=5,
            agg_func="sum",
        )
        ctx = PipelineContext()
        result = agg(records, ctx)
        assert len(result) == 2
        assert result[0]["sum"] == 10  # 0+1+2+3+4
        assert result[1]["sum"] == 35  # 5+6+7+8+9

    def test_sliding_window(self):
        """Sliding window aggregation works."""
        records = [{"v": i} for i in range(5)]
        agg = WindowedAggregator(
            field="v",
            window_type="sliding",
            window_size=3,
            slide=1,
            agg_func="avg",
        )
        ctx = PipelineContext()
        result = agg(records, ctx)
        assert len(result) == 3
        assert result[0]["avg"] == 1.0  # (0+1+2)/3
        assert result[1]["avg"] == 2.0  # (1+2+3)/3
        assert result[2]["avg"] == 3.0  # (2+3+4)/3


# =============================================================================
# Builder Tests
# =============================================================================

class TestPipelineBuilder:
    """Tests for PipelineBuilder fluent API."""

    def test_linear_pipeline(self, sample_records):
        """Building a linear pipeline with builder works."""
        result = (
            PipelineBuilder("test_linear")
            .source(MemorySource(data=sample_records))
            .transform(FilterTransform(field="age", value=25, operator="ge"))
            .aggregate(CountAggregator())
            .sink(MemorySink())
            .run()
        )
        assert result.success
        assert result.output[0]["count"] == 4  # 25 and above

    def test_branching_pipeline(self, sample_records):
        """Building a pipeline with branching works."""
        builder = PipelineBuilder("branch_test")
        builder.source(MemorySource(data=sample_records))

        # Main line: count
        builder.aggregate(CountAggregator(output_field="total"))

        # Branch: filter then count adults
        (builder
            .branch("adults")
            .transform(FilterTransform(field="age", value=18, operator="ge"))
            .aggregate(CountAggregator(output_field="adult_count"))
            .end())

        pipeline = builder.build()
        result = pipeline.run()
        assert result.success

    def test_conditional_stage(self, sample_records):
        """Conditional stage works."""
        result = (
            PipelineBuilder("conditional_test")
            .source(MemorySource(data=sample_records))
            .if_then_else(
                condition=lambda r: len(r) > 3,
                then_stages=[CountAggregator(output_field="count")],
                else_stages=[SumAggregator(field="age")],
            )
            .run()
        )
        assert result.success
        # Should have taken the then branch (5 > 3 records)
        assert result.output[0]["count"] == 5

    def test_from_config(self, sample_records):
        """Building from config dict works."""
        config = {
            "name": "config_pipeline",
            "stages": [
                {"type": "source.memory", "data": sample_records},
                {"type": "transform.filter", "field": "age", "value": 25, "operator": "ge"},
                {"type": "aggregate.count"},
                {"type": "sink.memory"},
            ],
        }
        builder = PipelineBuilder.from_config(config)
        result = builder.run()
        assert result.success
        assert result.output[0]["count"] == 4

    def test_hooks_with_builder(self, sample_records):
        """Hooks registered via builder fire correctly."""
        events = []
        result = (
            PipelineBuilder("hook_builder")
            .on_start(lambda ctx: events.append("start"))
            .on_end(lambda result: events.append("end"))
            .source(MemorySource(data=sample_records))
            .sink(MemorySink())
            .run()
        )
        assert result.success
        assert "start" in events
        assert "end" in events

    def test_build_returns_pipeline(self, sample_records):
        """build() returns a Pipeline instance."""
        pipeline = (
            PipelineBuilder("build_test")
            .source(MemorySource(data=sample_records))
            .build()
        )
        assert isinstance(pipeline, Pipeline)
        assert pipeline.name == "build_test"


# =============================================================================
# Integration / End-to-End Tests
# =============================================================================

class TestEndToEndPipelines:
    """End-to-end pipeline integration tests."""

    def test_full_etl_pipeline(self, sample_records):
        """Full ETL pipeline: source -> transform -> validate -> aggregate -> sink."""
        output_store = []
        result = (
            PipelineBuilder("etl_demo")
            .source(MemorySource(data=sample_records))
            .transform(FilterTransform(field="age", value=21, operator="ge"))
            .validate(RequiredFieldsValidator(required_fields=["id", "name", "email"]))
            .transform(MapTransform(
                add_field="score_grade",
                value_func=lambda r: "A" if r["score"] >= 90 else "B" if r["score"] >= 80 else "C",
            ))
            .aggregate(CountAggregator(group_by="score_grade"))
            .sink(MemorySink(store=output_store))
            .run()
        )

        assert result.success
        assert len(output_store) > 0
        # 4 records >= 21 (all except Charlie who is 22? Wait, 22 >= 21, so all 5 are >= 21)
        # Actually: Alice(25), Bob(30), Charlie(22), Diana(35), Eve(28) = all 5
        grade_counts = {r["score_grade"]: r["count"] for r in output_store}
        assert "A" in grade_counts  # Bob(92), Diana(95.7) = 2
        assert grade_counts["A"] == 2

    def test_pipeline_with_error_handling(self):
        """Pipeline error handling works correctly."""
        class FailingStage(PipelineStage):
            def process(self, data, context):
                raise ValueError("intentional error")

        result = (
            PipelineBuilder("error_test")
            .source(MemorySource(data=[{"a": 1}]))
            .stage(FailingStage(name="boom"))
            .run()
        )

        assert result.failed
        assert result.status == PipelineStatus.FAILED
        assert result.error_count > 0

    def test_validation_report_in_context(self, sample_records):
        """Validation report is available in context."""
        result = (
            PipelineBuilder("validation_test")
            .source(MemorySource(data=sample_records))
            .validate(RangeValidator(ranges={"age": (0, 100)}))
            .validate(UniqueValidator(fields="id"))
            .run()
        )

        assert result.success
        report = result.context.get_variable("validation_report")
        assert report is not None
        assert report.is_valid
        assert report.total_records == 5

    def test_stats_in_context(self, sample_records):
        """Stage stats are available in context."""
        result = (
            PipelineBuilder("stats_test")
            .source(MemorySource(data=sample_records))
            .transform(FilterTransform(field="city", value="London", operator="eq"))
            .run()
        )

        assert result.success
        stats = result.context.get_variable("FilterTransform_stats")
        assert stats is not None
        assert stats.input_count == 5
        assert stats.output_count == 2
