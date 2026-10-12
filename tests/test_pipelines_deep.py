"""深度测试：src.pipelines 管道组件库。

覆盖 Sources / Transforms / Sinks / Validation / Aggregation /
PipelineBuilder 以及 Pipeline DAG 核心行为。
"""

from __future__ import annotations

import json
import sqlite3
from typing import Any, Dict, List
from unittest.mock import MagicMock, patch

import pytest

from src.pipelines import (
    APISink,
    APISource,
    AverageAggregator,
    ConsoleSink,
    CountAggregator,
    DatabaseSink,
    DatabaseSource,
    DeduplicateTransform,
    FileSink,
    FileSource,
    FilterTransform,
    GeneratorPipelineStage,
    GroupByAggregator,
    JoinTransform,
    MapTransform,
    MemorySink,
    MemorySource,
    MinMaxAggregator,
    MovingAverageAggregator,
    MultiSink,
    NormalizeTransform,
    PercentileAggregator,
    Pipeline,
    PipelineBuilder,
    PipelineContext,
    PipelineError,
    PipelineHooks,
    PipelineStage,
    PipelineStatus,
    RangeValidator,
    ReduceTransform,
    RegexValidator,
    RenameTransform,
    RequiredFieldsValidator,
    SchemaValidator,
    SortTransform,
    SplitTransform,
    StreamSource,
    SumAggregator,
    TypeCastTransform,
    TypeValidator,
    UniqueValidator,
    WindowedAggregator,
)

Record = Dict[str, Any]


def make_records(n: int = 5) -> List[Record]:
    return [{"id": i, "value": i * 10, "group": "a" if i % 2 == 0 else "b"} for i in range(n)]


@pytest.fixture
def ctx() -> PipelineContext:
    return PipelineContext()


@pytest.fixture
def records() -> List[Record]:
    return make_records()


# ----------------------------------------------------------------------
# Sources
# ----------------------------------------------------------------------
class TestMemorySource:
    def test_read_list_of_dicts(self, records, ctx):
        src = MemorySource(data=records)
        out = src.process(None, ctx)
        assert len(out) == 5
        assert src.stats.records_read == 5

    def test_normalize_non_dict_items(self, ctx):
        src = MemorySource(data=[1, "two", {"three": 3}])
        out = src.process(None, ctx)
        assert out[0] == {"value": 1}
        assert out[1] == {"value": "two"}
        assert out[2] == {"three": 3}

    def test_batch_reading(self):
        src = MemorySource(data=make_records(10), batch_size=3)
        src.open()
        batch1 = src.read_batch(3)
        assert len(batch1) == 3
        assert src.has_more()
        src.close()

    def test_none_data_yields_empty(self, ctx):
        src = MemorySource(data=None)
        assert src.process(None, ctx) == []

    def test_single_dict_wrapped_in_list(self, ctx):
        src = MemorySource(data={"a": 1})
        assert src.process(None, ctx) == [{"a": 1}]


class TestFileSource:
    def test_read_txt(self, tmp_path, ctx):
        p = tmp_path / "data.txt"
        p.write_text("line1\nline2\nline3\n", encoding="utf-8")
        src = FileSource(str(p))
        out = src.process(None, ctx)
        assert len(out) == 3
        assert out[0] == {"_line": 1, "content": "line1"}

    def test_read_csv(self, tmp_path, ctx):
        p = tmp_path / "data.csv"
        p.write_text("name,age\nalice,30\nbob,25\n", encoding="utf-8")
        src = FileSource(str(p))
        out = src.process(None, ctx)
        assert out[0]["name"] == "alice"
        assert out[1]["age"] == "25"

    def test_read_json_list(self, tmp_path, ctx):
        p = tmp_path / "data.json"
        p.write_text(json.dumps([{"a": 1}, {"a": 2}]), encoding="utf-8")
        src = FileSource(str(p))
        out = src.process(None, ctx)
        assert [r["a"] for r in out] == [1, 2]

    def test_read_json_with_path(self, tmp_path, ctx):
        p = tmp_path / "data.json"
        p.write_text(json.dumps({"data": {"items": [{"x": 1}, {"x": 2}]}}), encoding="utf-8")
        src = FileSource(str(p), json_path="data.items")
        out = src.process(None, ctx)
        assert len(out) == 2 and out[0]["x"] == 1

    def test_read_jsonl(self, tmp_path, ctx):
        p = tmp_path / "data.jsonl"
        p.write_text('{"a": 1}\n{"a": 2}\n', encoding="utf-8")
        src = FileSource(str(p))
        out = src.process(None, ctx)
        assert len(out) == 2

    def test_read_xml(self, tmp_path, ctx):
        p = tmp_path / "data.xml"
        p.write_text(
            '<root><item name="a" value="1"/><item name="b" value="2"/></root>',
            encoding="utf-8",
        )
        src = FileSource(str(p))
        out = src.process(None, ctx)
        assert len(out) == 2
        assert out[0]["name"] == "a"
        assert out[0]["_tag"] == "item"

    def test_file_not_found_raises(self, ctx):
        src = FileSource("does_not_exist_12345.txt")
        with pytest.raises(PipelineError):
            src.process(None, ctx)

    def test_unsupported_format_raises(self, tmp_path, ctx):
        p = tmp_path / "data.badext"
        p.write_text("x", encoding="utf-8")
        src = FileSource(str(p))
        with pytest.raises(PipelineError):
            src.process(None, ctx)


class TestDatabaseSource:
    def _make_db(self):
        conn = sqlite3.connect(":memory:")
        conn.execute("CREATE TABLE users (id INTEGER, name TEXT)")
        conn.executemany("INSERT INTO users VALUES (?, ?)", [(1, "a"), (2, "b")])
        return conn

    def test_read_from_factory(self, ctx):
        src = DatabaseSource(query="SELECT * FROM users", connection_factory=self._make_db)
        out = src.process(None, ctx)
        assert len(out) == 2
        assert out[0]["name"] == "a"

    def test_missing_connection_info_raises(self, ctx):
        src = DatabaseSource(query="SELECT 1")
        with pytest.raises(PipelineError):
            src.process(None, ctx)

    def test_bad_query_raises(self, ctx):
        src = DatabaseSource(
            query="SELECT * FROM missing_table", connection_factory=self._make_db
        )
        with pytest.raises(PipelineError):
            src.process(None, ctx)


class TestStreamSource:
    def test_read_all_finite_stream(self, ctx):
        src = StreamSource(iterator=iter(make_records(4)))
        out = src.process(None, ctx)
        assert len(out) == 4
        assert src.stats.records_read == 4

    def test_batch_reading(self):
        src = StreamSource(iterator=iter(make_records(5)), batch_size=2)
        src.open()
        assert len(src.read_batch(2)) == 2
        assert src.has_more()
        assert len(src.read_batch(3)) == 3
        src.read_batch(2)
        assert not src.has_more()
        src.close()


class TestAPISource:
    def test_fetch_simple_list(self, ctx):
        mock_resp = MagicMock()
        mock_resp.json.return_value = [{"id": 1}, {"id": 2}]
        mock_resp.content = b"[{}]"
        mock_resp.raise_for_status.return_value = None
        with patch("requests.request", return_value=mock_resp):
            src = APISource(url="http://example.com/api")
            out = src.process(None, ctx)
        assert len(out) == 2
        assert src.stats.records_read == 2

    def test_data_key_extraction(self, ctx):
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"data": {"items": [{"id": 9}]}}
        mock_resp.content = b"{}"
        mock_resp.raise_for_status.return_value = None
        with patch("requests.request", return_value=mock_resp):
            src = APISource(url="http://example.com/api", data_key="data.items")
            out = src.process(None, ctx)
        assert out == [{"id": 9}]

    def test_http_error_raises_pipeline_error(self, ctx):
        with patch("requests.request", side_effect=RuntimeError("boom")):
            src = APISource(url="http://example.com/api")
            with pytest.raises(PipelineError):
                src.process(None, ctx)


# ----------------------------------------------------------------------
# Transforms
# ----------------------------------------------------------------------
class TestFilterTransform:
    def test_filter_by_field_ge(self, records, ctx):
        t = FilterTransform(field="value", value=20, operator="ge")
        out = t.process(records, ctx)
        assert [r["value"] for r in out] == [20, 30, 40]

    def test_filter_by_predicate(self, records, ctx):
        t = FilterTransform(predicate=lambda r: r["group"] == "a")
        out = t.process(records, ctx)
        assert all(r["group"] == "a" for r in out)

    def test_filter_operators(self, records, ctx):
        assert len(FilterTransform(field="value", value=10, operator="eq").process(records, ctx)) == 1
        assert len(FilterTransform(field="value", value=10, operator="ne").process(records, ctx)) == 4
        assert len(FilterTransform(field="group", value=["a"], operator="in").process(records, ctx)) == 3
        assert len(FilterTransform(field="group", value="a", operator="startswith").process(records, ctx)) == 3

    def test_requires_predicate_or_field(self):
        with pytest.raises(ValueError):
            FilterTransform()

    def test_stats_track_skipped(self, records, ctx):
        t = FilterTransform(field="value", value=20, operator="ge")
        t.process(records, ctx)
        assert t.stats.input_count == 5
        assert t.stats.skipped == 2
        assert t.stats.output_count == 3


class TestMapTransform:
    def test_mapper_function(self, records, ctx):
        t = MapTransform(mapper=lambda r: {**r, "double": r["value"] * 2})
        out = t.process(records, ctx)
        assert out[1]["double"] == 20

    def test_add_field_mode(self, records, ctx):
        t = MapTransform(add_field="is_even", value_func=lambda r: r["id"] % 2 == 0)
        out = t.process(records, ctx)
        assert out[0]["is_even"] is True
        assert out[1]["is_even"] is False

    def test_requires_mapper_or_field(self):
        with pytest.raises(ValueError):
            MapTransform()

    def test_error_passthrough(self, ctx):
        def bad_mapper(r):
            if r["id"] == 1:
                raise RuntimeError("bad")
            return r

        t = MapTransform(mapper=bad_mapper)
        out = t.process(make_records(3), ctx)
        assert len(out) == 3
        assert t.stats.errors == 1


class TestReduceTransform:
    def test_group_by_with_aggregations(self, records, ctx):
        t = ReduceTransform(
            group_by="group",
            aggregations={"total": ("value", sum)},
        )
        out = t.process(records, ctx)
        by_group = {r["group"]: r for r in out}
        assert by_group["a"]["total"] == 0 + 20 + 40
        assert by_group["a"]["count"] == 3

    def test_reduce_all_to_single_record(self, records, ctx):
        t = ReduceTransform(aggregations={"total": ("value", sum)})
        out = t.process(records, ctx)
        assert len(out) == 1
        assert out[0]["total"] == 100
        assert out[0]["count"] == 5


class TestSortTransform:
    def test_sort_by_key_ascending(self, ctx):
        data = [{"v": 3}, {"v": 1}, {"v": 2}]
        out = SortTransform(key="v").process(data, ctx)
        assert [r["v"] for r in out] == [1, 2, 3]

    def test_sort_descending(self, ctx):
        data = [{"v": 1}, {"v": 3}, {"v": 2}]
        out = SortTransform(key="v", reverse=True).process(data, ctx)
        assert [r["v"] for r in out] == [3, 2, 1]

    def test_sort_none_values_last(self, ctx):
        data = [{"v": None}, {"v": 1}, {"v": 0}]
        out = SortTransform(key="v").process(data, ctx)
        assert out[-1]["v"] is None

    def test_requires_key(self):
        with pytest.raises(ValueError):
            SortTransform()


class TestDeduplicateTransform:
    def test_dedup_by_key_keep_first(self, ctx):
        data = [{"id": 1, "v": "a"}, {"id": 1, "v": "b"}, {"id": 2, "v": "c"}]
        out = DeduplicateTransform(key="id", keep="first").process(data, ctx)
        assert len(out) == 2
        assert out[0]["v"] == "a"

    def test_dedup_by_key_keep_last(self, ctx):
        data = [{"id": 1, "v": "a"}, {"id": 1, "v": "b"}, {"id": 2, "v": "c"}]
        out = DeduplicateTransform(key="id", keep="last").process(data, ctx)
        assert len(out) == 2
        assert out[0]["v"] == "b"

    def test_invalid_keep_raises(self):
        with pytest.raises(ValueError):
            DeduplicateTransform(key="id", keep="middle")


class TestRenameTypeCastNormalizeSplit:
    def test_rename_fields(self, records, ctx):
        t = RenameTransform(mapping={"value": "score"})
        out = t.process(records, ctx)
        assert "score" in out[0] and "value" not in out[0]

    def test_type_cast_int(self, ctx):
        data = [{"age": "30"}, {"age": "25"}]
        out = TypeCastTransform(type_map={"age": "int"}).process(data, ctx)
        assert out[0]["age"] == 30 and isinstance(out[0]["age"], int)

    def test_type_cast_strict_raises(self, ctx):
        data = [{"age": "not_a_number"}]
        t = TypeCastTransform(type_map={"age": "int"}, strict=True)
        with pytest.raises(PipelineError):
            t.process(data, ctx)

    def test_normalize_min_max(self, ctx):
        data = [{"v": 0}, {"v": 50}, {"v": 100}]
        out = NormalizeTransform(fields=["v"]).process(data, ctx)
        assert out[0]["v_norm"] == 0.0
        assert out[1]["v_norm"] == 0.5
        assert out[2]["v_norm"] == 1.0

    def test_split_field(self, ctx):
        data = [{"full_name": "Doe,John"}]
        t = SplitTransform(source_field="full_name", target_fields=["last", "first"])
        out = t.process(data, ctx)
        assert out[0]["last"] == "Doe"
        assert out[0]["first"] == "John"
        assert "full_name" not in out[0]


class TestJoinTransform:
    def test_inner_join(self, ctx):
        left = [{"id": 1, "l": "x"}, {"id": 2, "l": "y"}]
        right = [{"id": 1, "r": "p"}, {"id": 3, "r": "q"}]
        t = JoinTransform(left_key="id")
        out = t.process({"left": left, "right": right}, ctx)
        assert len(out) == 1
        assert out[0]["l"] == "x" and out[0]["r"] == "p"

    def test_left_join_includes_unmatched(self, ctx):
        left = [{"id": 1}, {"id": 2}]
        right = [{"id": 1, "r": 5}]
        t = JoinTransform(left_key="id", join_type="left")
        out = t.process({"left": left, "right": right}, ctx)
        assert len(out) == 2

    def test_invalid_join_type_raises(self):
        with pytest.raises(ValueError):
            JoinTransform(left_key="id", join_type="cross")

    def test_requires_two_inputs(self, ctx):
        t = JoinTransform(left_key="id")
        with pytest.raises(PipelineError):
            t.process([{"id": 1}], ctx)


# ----------------------------------------------------------------------
# Sinks
# ----------------------------------------------------------------------
class TestFileSink:
    def test_write_json(self, tmp_path, records, ctx):
        p = str(tmp_path / "out.json")
        sink = FileSink(p)
        out = sink.process(records, ctx)
        assert out == records  # pass-through
        data = json.loads(open(p, encoding="utf-8").read())
        assert len(data) == 5
        assert sink.stats.records_written == 5

    def test_write_jsonl(self, tmp_path, records, ctx):
        p = str(tmp_path / "out.jsonl")
        FileSink(p).process(records, ctx)
        lines = open(p, encoding="utf-8").read().strip().split("\n")
        assert len(lines) == 5

    def test_write_csv(self, tmp_path, ctx):
        p = str(tmp_path / "out.csv")
        FileSink(p).process([{"a": 1, "b": 2}, {"a": 3, "b": 4}], ctx)
        content = open(p, encoding="utf-8").read()
        assert "a,b" in content and "1,2" in content

    def test_write_txt_uses_content_field(self, tmp_path, ctx):
        p = str(tmp_path / "out.txt")
        FileSink(p).process([{"content": "hello"}, {"content": "world"}], ctx)
        assert open(p, encoding="utf-8").read() == "hello\nworld\n"

    def test_creates_parent_directory(self, tmp_path, records, ctx):
        p = str(tmp_path / "nested" / "dir" / "out.json")
        FileSink(p).process(records, ctx)
        assert (tmp_path / "nested" / "dir" / "out.json").exists()


class TestDatabaseSink:
    def test_insert_records(self, ctx):
        conn = sqlite3.connect(":memory:")
        conn.execute("CREATE TABLE t (id INTEGER, value INTEGER)")

        class NonClosingConn:
            def __init__(self, inner):
                self._inner = inner

            def __getattr__(self, name):
                return getattr(self._inner, name)

            def close(self):
                pass

        sink = DatabaseSink(table_name="t", connection_factory=lambda: NonClosingConn(conn))
        sink.process([{"id": 1, "value": 10}, {"id": 2, "value": 20}], ctx)
        rows = conn.execute("SELECT * FROM t ORDER BY id").fetchall()
        assert rows == [(1, 10), (2, 20)]
        assert sink.stats.records_written == 2

    def test_missing_connection_raises(self, ctx):
        sink = DatabaseSink(table_name="t")
        with pytest.raises(PipelineError):
            sink.process([{"a": 1}], ctx)


class TestAPISink:
    def test_batch_post(self, records, ctx):
        mock_resp = MagicMock(ok=True)
        mock_resp.raise_for_status.return_value = None
        with patch("requests.request", return_value=mock_resp) as m:
            sink = APISink(url="http://example.com/collect", batch_mode=True)
            written_sink_out = sink.process(records, ctx)
        assert m.called
        assert sink.stats.records_written == 5
        assert written_sink_out == records

    def test_failure_raises(self, records, ctx):
        with patch("requests.request", side_effect=RuntimeError("net down")):
            sink = APISink(url="http://example.com/collect")
            with pytest.raises(PipelineError):
                sink.process(records, ctx)


class TestMemoryConsoleMultiSink:
    def test_memory_sink_internal_store(self, records, ctx):
        sink = MemorySink()
        sink.process(records, ctx)
        assert sink.data == records

    def test_memory_sink_external_store(self, records, ctx):
        store: List[Record] = []
        sink = MemorySink(store=store)
        sink.process(records, ctx)
        assert store == records

    def test_console_sink_json_output(self, records, ctx, capsys):
        sink = ConsoleSink(output_format="line")
        sink.process(records[:2], ctx)
        captured = capsys.readouterr()
        assert '"id": 0' in captured.out or '"id":0' in captured.out

    def test_multi_sink_fanout(self, records, ctx):
        s1, s2 = MemorySink(), MemorySink()
        multi = MultiSink(sinks=[s1, s2])
        multi.process(records, ctx)
        assert s1.data == records
        assert s2.data == records


# ----------------------------------------------------------------------
# Validation
# ----------------------------------------------------------------------
class TestValidators:
    def test_required_fields(self, ctx):
        v = RequiredFieldsValidator(required_fields=["id", "name"])
        data = [{"id": 1, "name": "a"}, {"id": 2}]
        v.process(data, ctx)
        assert v.report.error_count == 1
        assert v.report.valid_count == 1

    def test_required_null_is_error(self, ctx):
        v = RequiredFieldsValidator(required_fields=["name"])
        v.process([{"name": None}], ctx)
        assert v.report.error_count == 1

    def test_type_validator(self, ctx):
        v = TypeValidator(type_map={"age": int})
        v.process([{"age": 30}, {"age": "30"}], ctx)
        assert v.report.error_count == 1

    def test_type_validator_null_allowed(self, ctx):
        v = TypeValidator(type_map={"age": int}, allow_null=True)
        v.process([{"age": None}], ctx)
        assert v.report.is_valid

    def test_range_validator(self, ctx):
        v = RangeValidator(ranges={"age": (0, 120)})
        v.process([{"age": 30}, {"age": 200}, {"age": -5}], ctx)
        assert v.report.error_count == 2

    def test_range_exclusive_bounds(self, ctx):
        v = RangeValidator(ranges={"x": (0, 10)}, inclusive_min=False, inclusive_max=False)
        v.process([{"x": 0}, {"x": 10}, {"x": 5}], ctx)
        assert v.report.error_count == 2

    def test_regex_validator(self, ctx):
        v = RegexValidator(patterns={"email": r"^[\w.-]+@[\w.-]+\.\w+$"})
        v.process([{"email": "a@b.com"}, {"email": "bad"}], ctx)
        assert v.report.error_count == 1

    def test_regex_invalid_match_type(self):
        with pytest.raises(ValueError):
            RegexValidator(patterns={"x": ".*"}, match_type="bogus")

    def test_unique_validator(self, ctx):
        v = UniqueValidator(fields="id")
        v.process([{"id": 1}, {"id": 2}, {"id": 1}], ctx)
        assert v.report.error_count == 1

    def test_schema_validator(self, ctx):
        schema = {
            "fields": {
                "name": {"type": "str", "required": True},
                "age": {"type": "int", "required": True, "min": 0, "max": 150},
            }
        }
        v = SchemaValidator(schema=schema)
        v.process([{"name": "alice", "age": 30}, {"name": "bob", "age": 200}], ctx)
        assert v.report.error_count >= 1

    def test_fail_on_error_raises(self, ctx):
        v = RequiredFieldsValidator(required_fields=["missing"], fail_on_error=True)
        with pytest.raises(PipelineError):
            v.process([{"other": 1}], ctx)

    def test_validation_report_stored_in_context(self, ctx):
        v = RequiredFieldsValidator(required_fields=["id"])
        v.process([{"id": 1}], ctx)
        assert ctx.get_variable("validation_report") is not None


# ----------------------------------------------------------------------
# Aggregation
# ----------------------------------------------------------------------
class TestAggregators:
    def test_sum(self, records, ctx):
        agg = SumAggregator(field="value")
        out = agg.process(records, ctx)
        assert out[0]["value_sum"] == 100
        assert agg.results[0].aggregation == "sum"

    def test_sum_grouped(self, records, ctx):
        agg = SumAggregator(field="value", group_by="group")
        out = agg.process(records, ctx)
        by_group = {r["group"]: r["value_sum"] for r in out}
        assert by_group["a"] == 60
        assert by_group["b"] == 40

    def test_average(self, records, ctx):
        out = AverageAggregator(field="value").process(records, ctx)
        assert out[0]["value_avg"] == 20.0

    def test_count(self, records, ctx):
        out = CountAggregator().process(records, ctx)
        assert out[0]["count"] == 5

    def test_count_grouped(self, records, ctx):
        out = CountAggregator(group_by="group").process(records, ctx)
        by_group = {r["group"]: r["count"] for r in out}
        assert by_group == {"a": 3, "b": 2}

    def test_minmax(self, records, ctx):
        out = MinMaxAggregator(field="value").process(records, ctx)
        assert out[0]["value_min"] == 0
        assert out[0]["value_max"] == 40

    def test_groupby_builtin_aggs(self, records, ctx):
        agg = GroupByAggregator(
            group_by="group",
            aggregations={"total": ("value", "sum"), "avg_v": ("value", "avg")},
        )
        out = agg.process(records, ctx)
        by_group = {r["group"]: r for r in out}
        assert by_group["a"]["total"] == 60
        assert by_group["a"]["avg_v"] == 20.0

    def test_percentile(self, ctx):
        data = [{"v": i} for i in range(1, 101)]
        agg = PercentileAggregator(field="v", percentiles=[50, 90])
        out = agg.process(data, ctx)
        assert out[0]["p50"] == 50
        assert out[0]["p90"] == 90

    def test_moving_average(self, ctx):
        data = [{"v": float(i)} for i in range(6)]
        agg = MovingAverageAggregator(field="v", window_size=3)
        out = agg.process(data, ctx)
        assert out[0]["v_ma3"] is None
        assert out[2]["v_ma3"] == pytest.approx(1.0)
        assert out[5]["v_ma3"] == pytest.approx(4.0)

    def test_windowed_tumbling(self, ctx):
        data = [{"v": float(i)} for i in range(10)]
        agg = WindowedAggregator(field="v", window_type="tumbling", window_size=5, agg_func="sum")
        out = agg.process(data, ctx)
        assert len(out) == 2
        assert out[0]["sum"] == 10.0
        assert out[1]["sum"] == 35.0

    def test_windowed_invalid_agg_raises(self):
        with pytest.raises(PipelineError):
            WindowedAggregator(field="v", agg_func="bogus")


# ----------------------------------------------------------------------
# Pipeline core (DAG)
# ----------------------------------------------------------------------
class TestPipelineDAG:
    def test_linear_pipeline(self):
        p = Pipeline.from_stages(
            [
                MemorySource(data=make_records(3)),
                FilterTransform(field="value", value=10, operator="ge"),
                MemorySink(),
            ],
            name="linear",
        )
        result = p.run()
        assert result.success
        assert result.stages_completed == 3
        assert len(result.output) == 2

    def test_duplicate_node_id_raises(self):
        p = Pipeline("dup")
        p.add_stage(MemorySource(data=[]), node_id="x")
        with pytest.raises(ValueError):
            p.add_stage(MemorySink(), node_id="x")

    def test_cycle_detection(self):
        p = Pipeline("cyc")
        p.add_stage(MemorySource(data=[]), node_id="a")
        p.add_stage(MemorySink(), node_id="b", after=["a"])
        with pytest.raises(ValueError):
            p.connect("b", "a")

    def test_topological_order(self):
        p = Pipeline("topo")
        p.add_stage(MemorySource(data=[]), node_id="first")
        p.add_stage(MemorySink(), node_id="second", after=["first"])
        assert p.topological_order() == ["first", "second"]

    def test_stage_failure_produces_failed_result(self):
        class FailingStage(PipelineStage):
            def process(self, data, context):
                raise RuntimeError("kaboom")

        p = Pipeline.from_stages([MemorySource(data=[{"a": 1}]), FailingStage(name="boom")])
        result = p.run()
        assert result.failed
        assert result.error_count == 1
        assert "boom" in str(result.context.errors[0])

    def test_hooks_fire_in_order(self):
        events: List[str] = []
        hooks = PipelineHooks()
        hooks.on_start(lambda c: events.append("start"))
        hooks.on_stage_end(lambda name, out, c: events.append(f"stage:{name}"))
        hooks.on_end(lambda r: events.append("end"))
        p = Pipeline.from_stages([MemorySource(data=[{"a": 1}])], hooks=hooks)
        p.run()
        assert events[0] == "start"
        assert events[-1] == "end"
        assert any(e.startswith("stage:") for e in events)

    def test_on_error_hook_fires(self):
        fired: List[str] = []
        hooks = PipelineHooks()
        hooks.on_error(lambda err, c: fired.append(str(err)))

        class FailingStage(PipelineStage):
            def process(self, data, context):
                raise ValueError("nope")

        p = Pipeline.from_stages([FailingStage(name="f")], hooks=hooks)
        p.run()
        assert len(fired) == 1

    def test_unknown_hook_registration_raises(self):
        hooks = PipelineHooks()
        with pytest.raises(ValueError):
            hooks.register("on_nonexistent", lambda: None)

    def test_pipeline_error_str_includes_stage(self):
        err = PipelineError("bad", stage_name="s1", pipeline_name="p1")
        text = str(err)
        assert "bad" in text and "s1" in text and "p1" in text

    def test_context_tracks_stage_history(self):
        p = Pipeline.from_stages([MemorySource(data=[{"a": 1}], name="src"), MemorySink(name="snk")])
        result = p.run()
        assert result.context.stage_history == ["src", "snk"]

    def test_generator_stage(self):
        class GenStage(GeneratorPipelineStage):
            def process_stream(self, data, context):
                for i in range(3):
                    yield {"i": i}

        stage = GenStage(name="gen")
        out = list(stage.process(None, PipelineContext()))
        assert len(out) == 3


# ----------------------------------------------------------------------
# PipelineBuilder
# ----------------------------------------------------------------------
class TestPipelineBuilder:
    def test_fluent_chain(self):
        sink = MemorySink()
        result = (
            PipelineBuilder("chain")
            .source(MemorySource(data=make_records(10)))
            .transform(FilterTransform(field="value", value=50, operator="ge"))
            .sink(sink)
            .run()
        )
        assert result.success
        assert len(sink.data) == 5

    def test_second_source_raises(self):
        b = PipelineBuilder("x").source(MemorySource(data=[]))
        with pytest.raises(ValueError):
            b.source(MemorySource(data=[]))

    def test_branch_requires_stages(self):
        with pytest.raises(ValueError):
            PipelineBuilder("x").branch("b1")

    def test_aggregate_in_chain(self):
        result = (
            PipelineBuilder("agg")
            .source(MemorySource(data=make_records(5)))
            .aggregate(SumAggregator(field="value"))
            .sink(MemorySink())
            .run()
        )
        assert result.output[0]["value_sum"] == 100

    def test_validate_in_chain(self):
        result = (
            PipelineBuilder("val")
            .source(MemorySource(data=[{"id": 1}]))
            .validate(RequiredFieldsValidator(required_fields=["id"]))
            .sink(MemorySink())
            .run()
        )
        assert result.success

    def test_hooks_via_builder(self):
        events: List[str] = []
        result = (
            PipelineBuilder("hooked")
            .source(MemorySource(data=[{"a": 1}]))
            .on_start(lambda c: events.append("s"))
            .on_end(lambda r: events.append("e"))
            .sink(MemorySink())
            .run()
        )
        assert result.success
        assert events == ["s", "e"]

    def test_from_config_memory_pipeline(self):
        config = {
            "name": "cfg_pipe",
            "stages": [
                {"type": "source.memory", "data": [{"v": 1}, {"v": 5}]},
                {"type": "transform.filter", "field": "v", "value": 2, "operator": "ge"},
                {"type": "sink.memory"},
            ],
        }
        builder = PipelineBuilder.from_config(config)
        result = builder.run()
        assert result.success
        assert len(result.output) == 1
        assert result.output[0]["v"] == 5

    def test_from_config_unknown_type_raises(self):
        with pytest.raises(ValueError):
            PipelineBuilder.from_config({"stages": [{"type": "nope.nope"}]})

    def test_build_returns_pipeline_with_stages(self):
        p = (
            PipelineBuilder("b")
            .source(MemorySource(data=[]))
            .sink(MemorySink())
            .build()
        )
        assert isinstance(p, Pipeline)
        assert p.stage_count == 2
