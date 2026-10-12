"""Tests for the PlanParser module."""
from __future__ import annotations

import json
import pytest

from src.ai.planner.parser import PlanParser, ParsedPlan, ParsedTask, quick_parse_tasks


class TestPlanParser:
    """Test the PlanParser class."""

    def test_parse_valid_json(self):
        """Parser should correctly parse valid JSON output."""
        output = json.dumps({
            "tasks": [
                {
                    "id": "task_1",
                    "name": "Read file",
                    "description": "Read the input file",
                    "capability_id": "ReadFile",
                    "inputs": {"path": "input.txt"},
                    "depends_on": [],
                    "verification_criteria": "File contents are read",
                },
                {
                    "id": "task_2",
                    "name": "Process data",
                    "capability_id": "ExecuteCode",
                    "inputs": {"code": "print(1+1)"},
                    "depends_on": ["task_1"],
                },
            ],
            "clarification_needed": False,
            "plan_summary": "Read file then process",
            "estimated_complexity": "low",
            "parallelizable": False,
        })

        parser = PlanParser()
        plan = parser.parse(output)

        assert plan.parse_success is True
        assert plan.task_count == 2
        assert plan.valid_task_count == 2
        assert plan.plan_summary == "Read file then process"
        assert plan.estimated_complexity == "low"
        assert plan.parallelizable is False
        assert plan.clarification_needed is False

        # Check first task
        t1 = plan.get_task_by_id("task_1")
        assert t1 is not None
        assert t1.name == "Read file"
        assert t1.capability_id == "ReadFile"
        assert t1.inputs == {"path": "input.txt"}
        assert t1.depends_on == []

        # Check second task and dependency
        t2 = plan.get_task_by_id("task_2")
        assert t2 is not None
        assert t2.depends_on == ["task_1"]

    def test_parse_with_code_fence(self):
        """Parser should handle markdown code fences."""
        output = """Here's your plan:

```json
{
  "tasks": [
    {
      "id": "t1",
      "name": "Test task",
      "capability_id": "WriteFile",
      "inputs": {"path": "test.txt", "content": "hello"},
      "depends_on": []
    }
  ],
  "clarification_needed": false,
  "plan_summary": "Test"
}
```

Let me know if you need changes!"""

        parser = PlanParser()
        plan = parser.parse(output)

        assert plan.parse_success is True
        assert plan.task_count == 1
        assert plan.tasks[0].name == "Test task"

    def test_parse_empty_output(self):
        """Parser should handle empty output gracefully."""
        parser = PlanParser()
        plan = parser.parse("")

        assert plan.parse_success is False
        assert len(plan.parse_errors) > 0
        assert "JSON" in plan.parse_errors[0]

    def test_parse_invalid_json(self):
        """Parser should handle invalid JSON."""
        parser = PlanParser()
        plan = parser.parse("{this is not valid json}")

        assert plan.parse_success is False
        assert any("decode" in e.lower() for e in plan.parse_errors)

    def test_parse_missing_capability(self):
        """Tasks without capability_id should be marked invalid."""
        output = json.dumps({
            "tasks": [
                {
                    "id": "t1",
                    "name": "No capability",
                    "inputs": {},
                    "depends_on": [],
                }
            ],
            "clarification_needed": False,
            "plan_summary": "Test",
        })

        parser = PlanParser()
        plan = parser.parse(output)

        # Non-strict: parse succeeds but task has warnings
        assert plan.parse_success is True
        assert plan.task_count == 1
        assert plan.tasks[0].is_valid is False
        assert any("capability_id" in w for w in plan.tasks[0].parse_warnings)

    def test_parse_strict_mode(self):
        """In strict mode, invalid tasks cause parse failure."""
        output = json.dumps({
            "tasks": [
                {
                    "id": "t1",
                    "name": "No capability",
                    "inputs": {},
                    "depends_on": [],
                }
            ],
            "clarification_needed": False,
            "plan_summary": "Test",
        })

        parser = PlanParser(strict=True)
        plan = parser.parse(output)

        assert plan.parse_success is False
        assert any("capability" in e.lower() for e in plan.parse_errors)

    def test_parse_circular_dependency(self):
        """Parser should detect circular dependencies."""
        output = json.dumps({
            "tasks": [
                {"id": "a", "name": "Task A", "capability_id": "X", "depends_on": ["b"]},
                {"id": "b", "name": "Task B", "capability_id": "X", "depends_on": ["a"]},
            ],
            "clarification_needed": False,
            "plan_summary": "Circular",
        })

        parser = PlanParser()
        plan = parser.parse(output)

        assert plan.parse_success is False
        assert any("circular" in e.lower() for e in plan.parse_errors)

    def test_parse_clarification_needed(self):
        """Parser should handle clarification_needed flag."""
        output = json.dumps({
            "tasks": [],
            "clarification_needed": True,
            "clarification_reason": "I need more details about the goal",
            "plan_summary": "Need clarification",
        })

        parser = PlanParser()
        plan = parser.parse(output)

        assert plan.parse_success is True
        assert plan.clarification_needed is True
        assert plan.clarification_reason == "I need more details about the goal"

    def test_parse_duplicate_ids(self):
        """Parser should handle duplicate task IDs."""
        output = json.dumps({
            "tasks": [
                {"id": "dup", "name": "First", "capability_id": "X"},
                {"id": "dup", "name": "Second", "capability_id": "Y"},
            ],
            "clarification_needed": False,
            "plan_summary": "Duplicate IDs",
        })

        parser = PlanParser()
        plan = parser.parse(output)

        assert plan.parse_success is True
        assert plan.task_count == 2
        # IDs should be deduplicated
        ids = [t.id for t in plan.tasks]
        assert len(set(ids)) == 2  # All unique after dedup

    def test_parse_string_dependency(self):
        """Parser should handle single string dependency (convert to list)."""
        output = json.dumps({
            "tasks": [
                {"id": "a", "name": "A", "capability_id": "X"},
                {"id": "b", "name": "B", "capability_id": "Y", "depends_on": "a"},
            ],
            "clarification_needed": False,
            "plan_summary": "String dep",
        })

        parser = PlanParser()
        plan = parser.parse(output)

        assert plan.parse_success is True
        assert plan.tasks[1].depends_on == ["a"]

    def test_parsed_task_to_dict(self):
        """ParsedTask.to_dict() should return a serialisable dict."""
        task = ParsedTask(
            id="t1",
            name="Test",
            description="Test task",
            capability_id="WriteFile",
            inputs={"path": "test.txt"},
            depends_on=["t0"],
            verification_criteria="File exists",
        )

        d = task.to_dict()
        assert d["id"] == "t1"
        assert d["name"] == "Test"
        assert d["capability_id"] == "WriteFile"
        assert d["inputs"] == {"path": "test.txt"}
        assert d["depends_on"] == ["t0"]

        # Should be JSON-serialisable
        json.dumps(d)


class TestQuickParseTasks:
    """Test the quick_parse_tasks helper."""

    def test_quick_parse_valid(self):
        output = json.dumps({
            "tasks": [
                {"id": "t1", "name": "T1", "capability_id": "X", "inputs": {}},
                {"id": "t2", "name": "T2", "capability_id": "Y", "inputs": {}},
            ],
            "clarification_needed": False,
            "plan_summary": "Test",
        })

        tasks = quick_parse_tasks(output)
        assert len(tasks) == 2
        assert tasks[0]["id"] == "t1"

    def test_quick_parse_invalid(self):
        tasks = quick_parse_tasks("not json at all")
        assert tasks == []

    def test_quick_parse_empty_tasks(self):
        output = json.dumps({
            "tasks": [],
            "clarification_needed": False,
            "plan_summary": "Empty",
        })
        tasks = quick_parse_tasks(output)
        assert tasks == []
