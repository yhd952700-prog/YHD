"""Comprehensive tool tests for LiuHao AI OS.

Covers:
- BaseTool: registration, lookup, parameter validation, category filtering
- Calculator: basic math, expressions, error handling, safety
- CodeRunner: execution, sandbox limits, error capture, recursion limits
- MemoryTool: add/get/search/list/remove/clear integration with working memory
- DateTime: current time, formatting, date math, parsing
- FileTool: read/write/append/list/exists, path safety
- ToolExecutor: selection, batch execution, timeout, statistics
- ToolRegistry: register/unregister/list/search/categories
"""
from __future__ import annotations

import pytest
import tempfile
import os
from pathlib import Path

from src.ai.tools import (
    BaseTool,
    ToolRegistry,
    ToolResult,
    ParameterSchema,
    ToolCategory,
    ToolStatus,
    ResultStatus,
    CalculatorTool,
    SearchTool,
    CodeRunnerTool,
    MemoryTool,
    DateTimeTool,
    FileTool,
    ToolExecutor,
    ToolCandidate,
    ToolCallStats,
    ToolExecutorStats,
    BatchResult,
)


# ======================================================================
# ParameterSchema Tests (10 tests)
# ======================================================================

class TestParameterSchema:
    """Tests for ParameterSchema validation."""

    def test_validate_required_string(self):
        """Test validation of a required string parameter."""
        schema = ParameterSchema(name="query", type=str, required=True)
        valid, err = schema.validate("hello")
        assert valid is True
        assert err is None

    def test_validate_required_missing(self):
        """Test that required parameter with None fails."""
        schema = ParameterSchema(name="query", type=str, required=True)
        valid, err = schema.validate(None)
        assert valid is False
        assert "required" in err.lower()

    def test_validate_optional_none(self):
        """Test that optional parameter with None is valid."""
        schema = ParameterSchema(name="query", type=str, required=False)
        valid, err = schema.validate(None)
        assert valid is True

    def test_validate_wrong_type(self):
        """Test type mismatch fails validation."""
        schema = ParameterSchema(name="count", type=int, required=True)
        valid, err = schema.validate("not an int")
        assert valid is False
        assert "expected type" in err.lower()

    def test_validate_int_bounds(self):
        """Test numeric min/max validation."""
        schema = ParameterSchema(
            name="count", type=int, min_value=1, max_value=10
        )
        assert schema.validate(5)[0] is True
        assert schema.validate(0)[0] is False
        assert schema.validate(11)[0] is False

    def test_validate_string_length(self):
        """Test string length validation."""
        schema = ParameterSchema(
            name="text", type=str, min_length=2, max_length=10
        )
        assert schema.validate("hi")[0] is True
        assert schema.validate("a")[0] is False
        assert schema.validate("a" * 11)[0] is False

    def test_validate_choices(self):
        """Test choices validation."""
        schema = ParameterSchema(
            name="op", type=str, choices=["add", "remove", "list"]
        )
        assert schema.validate("add")[0] is True
        assert schema.validate("delete")[0] is False

    def test_validate_float_value(self):
        """Test float parameter validation."""
        schema = ParameterSchema(name="ratio", type=float, min_value=0.0, max_value=1.0)
        assert schema.validate(0.5)[0] is True
        assert schema.validate(1.5)[0] is False

    def test_validate_bool_not_int(self):
        """Test that bool is not accepted as int."""
        schema = ParameterSchema(name="count", type=int)
        valid, _ = schema.validate(True)
        assert valid is False

    def test_schema_to_dict(self):
        """Test ParameterSchema to_dict serialization."""
        schema = ParameterSchema(
            name="test",
            type=int,
            description="test param",
            required=True,
            default=5,
            choices=[1, 2, 3],
            min_value=1,
            max_value=10,
        )
        d = schema.to_dict()
        assert d["name"] == "test"
        assert d["type"] == "int"
        assert d["required"] is True
        assert d["default"] == 5
        assert d["choices"] == [1, 2, 3]
        assert d["min_value"] == 1
        assert d["max_value"] == 10


# ======================================================================
# ToolResult Tests (7 tests)
# ======================================================================

class TestToolResult:
    """Tests for ToolResult factory methods and properties."""

    def test_success_factory(self):
        """Test ToolResult.success factory method."""
        result = ToolResult.success({"answer": 42}, metadata={"key": "val"})
        assert result.status == ResultStatus.SUCCESS
        assert result.output == {"answer": 42}
        assert result.error is None
        assert result.metadata == {"key": "val"}

    def test_failure_factory(self):
        """Test ToolResult.failure factory method."""
        result = ToolResult.failure("something went wrong")
        assert result.status == ResultStatus.FAILED
        assert result.output is None
        assert result.error == "something went wrong"

    def test_timeout_factory(self):
        """Test ToolResult.timeout factory method."""
        result = ToolResult.timeout("took too long")
        assert result.status == ResultStatus.TIMEOUT
        assert result.error == "took too long"

    def test_rejected_factory(self):
        """Test ToolResult.rejected factory method."""
        result = ToolResult.rejected("not allowed")
        assert result.status == ResultStatus.REJECTED
        assert result.error == "not allowed"

    def test_ok_property(self):
        """Test the ok property."""
        assert ToolResult.success("yes").ok is True
        assert ToolResult.failure("no").ok is False
        assert ToolResult.timeout().ok is False

    def test_to_dict(self):
        """Test ToolResult to_dict serialization."""
        result = ToolResult.success(
            output={"data": 123},
            metadata={"source": "test"},
        )
        result.execution_time_ms = 10.5
        d = result.to_dict()
        assert d["status"] == "success"
        assert d["output"] == {"data": 123}
        assert d["metadata"] == {"source": "test"}
        assert d["execution_time_ms"] == 10.5

    def test_result_status_enum(self):
        """Test all ResultStatus values exist."""
        statuses = list(ResultStatus)
        assert ResultStatus.SUCCESS in statuses
        assert ResultStatus.PARTIAL in statuses
        assert ResultStatus.FAILED in statuses
        assert ResultStatus.TIMEOUT in statuses
        assert ResultStatus.REJECTED in statuses


# ======================================================================
# ToolRegistry Tests (8 tests)
# ======================================================================

class TestToolRegistry:
    """Tests for ToolRegistry."""

    def test_register_tool(self):
        """Test registering a tool."""
        registry = ToolRegistry()
        name = registry.register(CalculatorTool())
        assert name == "calculator"
        assert registry.has("calculator")
        assert registry.tool_count == 1

    def test_register_duplicate_raises(self):
        """Test that registering duplicate tool names raises ValueError."""
        registry = ToolRegistry()
        registry.register(CalculatorTool())
        with pytest.raises(ValueError, match="already registered"):
            registry.register(CalculatorTool())

    def test_register_nameless_tool_raises(self):
        """Test that registering a tool without name raises ValueError."""
        class EmptyTool(BaseTool):
            name = ""
            def _execute(self, **kwargs):
                pass
        registry = ToolRegistry()
        with pytest.raises(ValueError, match="name"):
            registry.register(EmptyTool())

    def test_unregister_tool(self):
        """Test unregistering a tool."""
        registry = ToolRegistry()
        registry.register(CalculatorTool())
        assert registry.has("calculator")
        result = registry.unregister("calculator")
        assert result is True
        assert not registry.has("calculator")
        assert registry.tool_count == 0

    def test_unregister_nonexistent(self):
        """Test unregistering a tool that doesn't exist."""
        registry = ToolRegistry()
        result = registry.unregister("nonexistent")
        assert result is False

    def test_get_tool(self):
        """Test getting a tool by name."""
        registry = ToolRegistry()
        registry.register(CalculatorTool())
        tool = registry.get("calculator")
        assert tool is not None
        assert tool.name == "calculator"

    def test_get_nonexistent_returns_none(self):
        """Test getting a nonexistent tool returns None."""
        registry = ToolRegistry()
        assert registry.get("nonexistent") is None

    def test_by_category(self):
        """Test filtering tools by category."""
        registry = ToolRegistry()
        registry.register(CalculatorTool())
        registry.register(SearchTool())
        registry.register(DateTimeTool())

        computation = registry.by_category(ToolCategory.COMPUTATION)
        info = registry.by_category(ToolCategory.INFORMATION)
        utility = registry.by_category(ToolCategory.UTILITY)

        assert len(computation) == 1
        assert len(info) == 1
        assert len(utility) == 1

    def test_list_names(self):
        """Test listing all tool names."""
        registry = ToolRegistry()
        registry.register(CalculatorTool())
        registry.register(SearchTool())
        names = registry.list_names()
        assert names == ["calculator", "search"]

    def test_stats(self):
        """Test registry statistics."""
        registry = ToolRegistry()
        registry.register(CalculatorTool())
        registry.register(SearchTool())
        stats = registry.stats()
        assert stats["total_tools"] == 2
        assert "categories" in stats
        assert "total_calls" in stats


# ======================================================================
# CalculatorTool Tests (8 tests)
# ======================================================================

class TestCalculatorTool:
    """Tests for CalculatorTool."""

    def test_basic_arithmetic(self):
        """Test basic arithmetic operations."""
        calc = CalculatorTool()
        result = calc.execute(expression="2 + 3")
        assert result.ok is True
        assert result.output["result"] == 5

    def test_complex_expression(self):
        """Test more complex expression."""
        calc = CalculatorTool()
        result = calc.execute(expression="2 + 3 * 4")
        assert result.ok is True
        assert result.output["result"] == 14  # operator precedence

    def test_parentheses(self):
        """Test parentheses grouping."""
        calc = CalculatorTool()
        result = calc.execute(expression="(2 + 3) * 4")
        assert result.ok is True
        assert result.output["result"] == 20

    def test_math_functions(self):
        """Test math functions like sqrt, sin, etc."""
        calc = CalculatorTool()
        result = calc.execute(expression="sqrt(16)")
        assert result.ok is True
        assert result.output["result"] == 4.0

    def test_constants(self):
        """Test math constants pi and e."""
        calc = CalculatorTool()
        result = calc.execute(expression="pi")
        assert result.ok is True
        assert abs(result.output["result"] - 3.14159) < 0.01

    def test_division_by_zero(self):
        """Test division by zero error handling."""
        calc = CalculatorTool()
        result = calc.execute(expression="1 / 0")
        assert result.ok is False
        assert "division by zero" in result.error.lower()

    def test_safety_dangerous_patterns(self):
        """Test that dangerous patterns are rejected."""
        calc = CalculatorTool()
        result = calc.execute(expression="__import__('os')")
        assert result.ok is False
        assert "disallowed" in result.error.lower()

    def test_syntax_error(self):
        """Test syntax error handling."""
        calc = CalculatorTool()
        result = calc.execute(expression="2 +* 3")
        assert result.ok is False

    def test_missing_expression(self):
        """Test that missing expression parameter fails."""
        calc = CalculatorTool()
        result = calc.execute()
        assert result.ok is False
        assert "missing" in result.error.lower()


# ======================================================================
# CodeRunnerTool Tests (7 tests)
# ======================================================================

class TestCodeRunnerTool:
    """Tests for CodeRunnerTool."""

    def test_simple_code_execution(self):
        """Test executing simple Python code."""
        runner = CodeRunnerTool()
        result = runner.execute(code="x = 1 + 1\nresult = x")
        assert result.ok is True
        assert result.output["result"] == 2

    def test_print_output_capture(self):
        """Test that print output is captured."""
        runner = CodeRunnerTool()
        result = runner.execute(code="print('hello world')")
        assert result.ok is True
        assert "hello world" in result.output["output"]

    def test_allowed_modules(self):
        """Test that allowed modules can be used."""
        runner = CodeRunnerTool()
        result = runner.execute(code="result = math.sqrt(25)")
        assert result.ok is True
        assert result.output["result"] == 5.0

    def test_dangerous_import_blocked(self):
        """Test that dangerous imports are blocked."""
        runner = CodeRunnerTool()
        result = runner.execute(code="import os\nresult = os.getcwd()")
        assert result.ok is False
        assert "disallowed" in result.error.lower()

    def test_dangerous_function_blocked(self):
        """Test that dangerous functions like eval are blocked."""
        runner = CodeRunnerTool()
        result = runner.execute(code="result = eval('1+1')")
        assert result.ok is False

    def test_recursion_limit(self):
        """Test that recursion depth is limited."""
        runner = CodeRunnerTool()
        code = """
def recurse(n):
    if n == 0:
        return 0
    return recurse(n - 1) + 1
result = recurse(1000)
"""
        result = runner.execute(code=code)
        # Should either fail with recursion error or be limited
        assert result.ok is False or result.output["result"] <= 500

    def test_code_syntax_error(self):
        """Test syntax error handling in code runner."""
        runner = CodeRunnerTool()
        result = runner.execute(code="def broken(:")
        assert result.ok is False


# ======================================================================
# MemoryTool Tests (8 tests)
# ======================================================================

class TestMemoryTool:
    """Tests for MemoryTool."""

    def test_add_memory(self):
        """Test adding a memory item."""
        tool = MemoryTool()
        result = tool.execute(operation="add", content="test memory")
        assert result.ok is True
        assert result.output["success"] is True
        assert "item" in result.output

    def test_get_memory(self):
        """Test getting a memory item by ID."""
        tool = MemoryTool()
        add_result = tool.execute(operation="add", content="find me")
        item_id = add_result.output["item"]["id"]

        get_result = tool.execute(operation="get", item_id=item_id)
        assert get_result.ok is True
        assert get_result.output["found"] is True
        assert get_result.output["item"]["content"] == "find me"

    def test_get_nonexistent(self):
        """Test getting a nonexistent memory item."""
        tool = MemoryTool()
        result = tool.execute(operation="get", item_id="nonexistent")
        assert result.ok is True
        assert result.output["found"] is False

    def test_search_memory(self):
        """Test searching memory items."""
        tool = MemoryTool()
        tool.execute(operation="add", content="apple pie recipe")
        tool.execute(operation="add", content="banana bread recipe")
        tool.execute(operation="add", content="steak dinner")

        result = tool.execute(operation="search", query="recipe")
        assert result.ok is True
        assert result.output["count"] >= 2

    def test_list_memory(self):
        """Test listing memory items."""
        tool = MemoryTool()
        tool.execute(operation="add", content="item 1")
        tool.execute(operation="add", content="item 2")

        result = tool.execute(operation="list", limit=10)
        assert result.ok is True
        assert result.output["count"] >= 2

    def test_remove_memory(self):
        """Test removing a memory item."""
        tool = MemoryTool()
        add_result = tool.execute(operation="add", content="to be removed")
        item_id = add_result.output["item"]["id"]

        remove_result = tool.execute(operation="remove", item_id=item_id)
        assert remove_result.ok is True
        assert remove_result.output["removed"] is True

    def test_clear_memory(self):
        """Test clearing all memory."""
        tool = MemoryTool()
        tool.execute(operation="add", content="item 1")
        tool.execute(operation="add", content="item 2")

        clear_result = tool.execute(operation="clear")
        assert clear_result.ok is True
        assert clear_result.output["success"] is True

        list_result = tool.execute(operation="list")
        assert list_result.output["count"] == 0

    def test_invalid_operation(self):
        """Test that invalid operation fails."""
        tool = MemoryTool()
        result = tool.execute(operation="invalid_op")
        assert result.ok is False


# ======================================================================
# DateTimeTool Tests (7 tests)
# ======================================================================

class TestDateTimeTool:
    """Tests for DateTimeTool."""

    def test_now_operation(self):
        """Test getting current time."""
        dt = DateTimeTool()
        result = dt.execute(operation="now")
        assert result.ok is True
        assert "datetime" in result.output
        assert "date" in result.output
        assert "time" in result.output
        assert "weekday" in result.output

    def test_format_operation(self):
        """Test date formatting."""
        dt = DateTimeTool()
        result = dt.execute(operation="format", date_str="2024-01-15", format_str="%d/%m/%Y")
        assert result.ok is True
        assert result.output["formatted"] == "15/01/2024"

    def test_add_days_positive(self):
        """Test adding days to a date."""
        dt = DateTimeTool()
        result = dt.execute(operation="add_days", date_str="2024-01-01", days=10)
        assert result.ok is True
        assert "2024-01-11" in result.output["result"]

    def test_add_days_negative(self):
        """Test subtracting days from a date."""
        dt = DateTimeTool()
        result = dt.execute(operation="add_days", date_str="2024-01-11", days=-10)
        assert result.ok is True
        assert "2024-01-01" in result.output["result"]

    def test_diff_operation(self):
        """Test date difference calculation."""
        dt = DateTimeTool()
        result = dt.execute(
            operation="diff",
            date_str="2024-01-01",
            date_str2="2024-01-11",
        )
        assert result.ok is True
        assert result.output["days_diff"] == 10

    def test_parse_operation(self):
        """Test date parsing."""
        dt = DateTimeTool()
        result = dt.execute(operation="parse", date_str="2024-06-15")
        assert result.ok is True
        assert "parsed" in result.output
        assert result.output["year"] == 2024
        assert result.output["month"] == 6
        assert result.output["day"] == 15

    def test_invalid_operation(self):
        """Test invalid datetime operation."""
        dt = DateTimeTool()
        result = dt.execute(operation="invalid")
        assert result.ok is False


# ======================================================================
# FileTool Tests (7 tests)
# ======================================================================

class TestFileTool:
    """Tests for FileTool."""

    @pytest.fixture
    def temp_dir(self):
        """Create a temporary directory for file tests."""
        with tempfile.TemporaryDirectory() as tmpdir:
            yield Path(tmpdir)

    def test_write_and_read_file(self, temp_dir):
        """Test writing and reading a file."""
        tool = FileTool(base_dir=str(temp_dir))
        write_result = tool.execute(
            operation="write",
            path="test.txt",
            content="hello world",
        )
        assert write_result.ok is True

        read_result = tool.execute(operation="read", path="test.txt")
        assert read_result.ok is True
        assert read_result.output["content"] == "hello world"

    def test_append_file(self, temp_dir):
        """Test appending to a file."""
        tool = FileTool(base_dir=str(temp_dir))
        tool.execute(operation="write", path="append.txt", content="line1\n")
        append_result = tool.execute(
            operation="append",
            path="append.txt",
            content="line2\n",
        )
        assert append_result.ok is True

        read_result = tool.execute(operation="read", path="append.txt")
        assert "line1" in read_result.output["content"]
        assert "line2" in read_result.output["content"]

    def test_file_exists(self, temp_dir):
        """Test checking if a file exists."""
        tool = FileTool(base_dir=str(temp_dir))
        tool.execute(operation="write", path="exists.txt", content="data")

        exists_result = tool.execute(operation="exists", path="exists.txt")
        assert exists_result.ok is True
        assert exists_result.output["exists"] is True

        not_exists = tool.execute(operation="exists", path="nope.txt")
        assert not_exists.output["exists"] is False

    def test_list_files(self, temp_dir):
        """Test listing files in directory."""
        tool = FileTool(base_dir=str(temp_dir))
        tool.execute(operation="write", path="a.txt", content="1")
        tool.execute(operation="write", path="b.txt", content="2")

        list_result = tool.execute(operation="list", path=".")
        assert list_result.ok is True
        assert list_result.output["count"] >= 2
        names = [f["name"] for f in list_result.output["entries"]]
        assert "a.txt" in names
        assert "b.txt" in names

    def test_read_nonexistent_file(self, temp_dir):
        """Test reading a file that doesn't exist."""
        tool = FileTool(base_dir=str(temp_dir))
        result = tool.execute(operation="read", path="nope.txt")
        assert result.ok is False

    def test_path_safety_parent_dir(self, temp_dir):
        """Test that path traversal attempts are blocked."""
        tool = FileTool(base_dir=str(temp_dir))
        result = tool.execute(
            operation="read",
            path="../etc/passwd",
        )
        # Should either fail or be normalized safely
        assert result.ok is False

    def test_invalid_file_operation(self, temp_dir):
        """Test invalid file operation."""
        tool = FileTool(base_dir=str(temp_dir))
        result = tool.execute(operation="invalid_op", path="test.txt")
        assert result.ok is False


# ======================================================================
# ToolExecutor Tests (8 tests)
# ======================================================================

class TestToolExecutor:
    """Tests for ToolExecutor."""

    @pytest.fixture
    def executor(self):
        """Create a ToolExecutor with registered tools."""
        registry = ToolRegistry()
        registry.register(CalculatorTool())
        registry.register(SearchTool())
        registry.register(DateTimeTool())
        return ToolExecutor(registry)

    def test_execute_tool(self, executor):
        """Test executing a tool through the executor."""
        result = executor.execute("calculator", expression="2 + 2")
        assert result.ok is True
        assert result.output["result"] == 4

    def test_execute_nonexistent_tool(self, executor):
        """Test executing a tool that doesn't exist."""
        result = executor.execute("nonexistent", foo="bar")
        assert result.ok is False

    def test_select_tools_keyword(self, executor):
        """Test tool selection based on keywords."""
        candidates = executor.select_tools("calculate the sum of 5 and 3")
        assert len(candidates) > 0
        assert candidates[0].tool_name == "calculator"

    def test_select_tools_search_keyword(self, executor):
        """Test tool selection for search queries."""
        candidates = executor.select_tools("find information about python")
        assert len(candidates) > 0
        assert any(c.tool_name == "search" for c in candidates)

    def test_tool_candidate_to_dict(self):
        """Test ToolCandidate to_dict."""
        tc = ToolCandidate(
            tool_name="calc",
            score=0.9,
            reason="math needed",
            matched_keywords=["calculate"],
        )
        d = tc.to_dict()
        assert d["tool_name"] == "calc"
        assert d["score"] == 0.9
        assert d["reason"] == "math needed"
        assert d["matched_keywords"] == ["calculate"]

    def test_batch_execute(self, executor):
        """Test batch tool execution."""
        batch = [
            ("calculator", {"expression": "1 + 1"}),
            ("calculator", {"expression": "2 * 3"}),
        ]
        batch_result = executor.batch_execute(batch)
        assert isinstance(batch_result, BatchResult)
        assert batch_result.total_calls == 2
        assert batch_result.successful == 2
        assert batch_result.all_succeeded is True

    def test_executor_stats(self, executor):
        """Test executor statistics tracking."""
        executor.execute("calculator", expression="1+1")
        executor.execute("calculator", expression="2+2")
        executor.execute("nonexistent", x=1)  # will fail

        stats = executor.stats
        assert isinstance(stats, ToolExecutorStats)
        assert stats.total_calls >= 3
        assert stats.successful_calls >= 2
        assert stats.failed_calls >= 1

    def test_executor_stats_to_dict(self, executor):
        """Test ToolExecutorStats to_dict."""
        executor.execute("calculator", expression="1+1")
        stats = executor.stats
        d = stats.to_dict()
        assert d["total_calls"] >= 1
        assert "success_rate" in d
        assert "avg_time_ms" in d
        assert "per_tool" in d

    def test_batch_result_all_succeeded_false(self, executor):
        """Test BatchResult all_succeeded when some fail."""
        batch = [
            ("calculator", {"expression": "1+1"}),
            ("nonexistent", {}),
        ]
        batch_result = executor.batch_execute(batch)
        assert batch_result.total_calls == 2
        assert batch_result.successful == 1
        assert batch_result.failed == 1
        assert batch_result.all_succeeded is False


# ======================================================================
# BaseTool Properties Tests (5 tests)
# ======================================================================

class TestBaseToolProperties:
    """Tests for BaseTool properties and info."""

    def test_call_count_tracks(self):
        """Test that call_count tracks executions."""
        calc = CalculatorTool()
        assert calc.call_count == 0
        calc.execute(expression="1+1")
        assert calc.call_count == 1
        calc.execute(expression="2+2")
        assert calc.call_count == 2

    def test_total_time_accumulates(self):
        """Test that total_time_ms accumulates."""
        calc = CalculatorTool()
        calc.execute(expression="1+1")
        calc.execute(expression="2+2")
        assert calc.total_time_ms >= 0

    def test_avg_time_calculated(self):
        """Test average time calculation."""
        calc = CalculatorTool()
        assert calc.avg_time_ms == 0.0  # no calls yet
        calc.execute(expression="1+1")
        calc.execute(expression="2+2")
        assert calc.avg_time_ms >= 0

    def test_tool_info_dict(self):
        """Test tool info() method."""
        calc = CalculatorTool()
        info = calc.info()
        assert info["name"] == "calculator"
        assert info["category"] == "computation"
        assert isinstance(info["parameters"], list)
        assert info["call_count"] == 0

    def test_tool_status(self):
        """Test tool status property."""
        calc = CalculatorTool()
        assert calc.status == ToolStatus.REGISTERED

        registry = ToolRegistry()
        registry.register(calc)
        assert calc.status == ToolStatus.VALID
