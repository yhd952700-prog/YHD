"""Tests for the PlanValidator module."""
from __future__ import annotations

import pytest

from src.ai.planner.parser import ParsedPlan, ParsedTask
from src.ai.planner.validator import (
    PlanValidator,
    ValidationResult,
    ValidationIssue,
    IssueSeverity,
)


# Sample capabilities for testing
TEST_CAPABILITIES = [
    {
        "id": "WriteFile",
        "name": "Write File",
        "description": "Write content to a file",
        "allowed_scopes": ["L1", "L2", "L3"],
        "inputs_schema": {
            "path": {"type": "string", "required": True, "description": "File path"},
            "content": {"type": "string", "required": True, "description": "File content"},
        },
    },
    {
        "id": "ReadFile",
        "name": "Read File",
        "description": "Read content from a file",
        "allowed_scopes": ["L1", "L2", "L3"],
        "inputs_schema": {
            "path": {"type": "string", "required": True, "description": "File path"},
        },
    },
    {
        "id": "ExecuteCode",
        "name": "Execute Code",
        "description": "Execute code in a sandbox",
        "allowed_scopes": ["L2", "L3"],
        "inputs_schema": {
            "code": {"type": "string", "required": True, "description": "Code to execute"},
            "timeout": {"type": "integer", "required": False, "description": "Timeout in seconds"},
        },
    },
    {
        "id": "WebSearch",
        "name": "Web Search",
        "description": "Search the web for information",
        "allowed_scopes": ["L2", "L3"],
        "inputs_schema": {
            "query": {"type": "string", "required": True, "description": "Search query"},
        },
    },
]


class TestValidationResult:
    """Test the ValidationResult class."""

    def test_initial_state(self):
        result = ValidationResult()
        assert result.is_valid is True
        assert result.issues == []
        assert result.errors == []
        assert result.warnings == []

    def test_add_error(self):
        result = ValidationResult()
        result.add_error("Something went wrong", task_id="t1", field="capability_id")

        assert result.is_valid is False
        assert len(result.errors) == 1
        assert result.errors[0].severity == IssueSeverity.ERROR
        assert result.errors[0].message == "Something went wrong"
        assert result.errors[0].task_id == "t1"
        assert "t1" in result.invalid_tasks

    def test_add_warning(self):
        result = ValidationResult()
        result.add_warning("Be careful", task_id="t1")

        assert result.is_valid is True  # Warnings don't invalidate
        assert len(result.warnings) == 1
        assert result.warnings[0].severity == IssueSeverity.WARNING

    def test_add_info(self):
        result = ValidationResult()
        result.add_info("Just so you know")

        assert result.is_valid is True
        assert len(result.issues) == 1
        assert result.issues[0].severity == IssueSeverity.INFO

    def test_summary(self):
        result = ValidationResult()
        result.add_error("E1", task_id="t1")
        result.add_warning("W1")
        result.valid_tasks = ["t2", "t3"]
        result.invalid_tasks = ["t1"]

        summary = result.summary()
        assert "FAIL" in summary
        assert "2 valid" in summary
        assert "1 invalid" in summary
        assert "1 errors" in summary
        assert "1 warnings" in summary


class TestPlanValidator:
    """Test the PlanValidator class."""

    def test_valid_plan_passes(self):
        """A valid plan should pass validation."""
        plan = ParsedPlan(
            tasks=[
                ParsedTask(
                    id="t1",
                    name="Write file",
                    capability_id="WriteFile",
                    inputs={"path": "test.txt", "content": "hello"},
                ),
            ],
            parse_success=True,
        )

        validator = PlanValidator(capabilities=TEST_CAPABILITIES, scope="L2")
        result = validator.validate(plan)

        assert result.is_valid is True
        assert len(result.errors) == 0

    def test_unknown_capability_fails(self):
        """Tasks with unknown capability_id should fail validation."""
        plan = ParsedPlan(
            tasks=[
                ParsedTask(
                    id="t1",
                    name="Fake task",
                    capability_id="FakeCapability",
                    inputs={},
                ),
            ],
            parse_success=True,
        )

        validator = PlanValidator(capabilities=TEST_CAPABILITIES, scope="L2")
        result = validator.validate(plan)

        assert result.is_valid is False
        assert any("does not exist" in e.message for e in result.errors)
        assert "t1" in result.invalid_tasks

    def test_missing_capability_id_fails(self):
        """Tasks without capability_id should fail validation."""
        plan = ParsedPlan(
            tasks=[
                ParsedTask(id="t1", name="No cap", inputs={}),
            ],
            parse_success=True,
        )

        validator = PlanValidator(capabilities=TEST_CAPABILITIES, scope="L2")
        result = validator.validate(plan)

        assert result.is_valid is False
        assert any("capability_id" in e.message for e in result.errors)

    def test_scope_violation_fails(self):
        """Capabilities not allowed at current scope should fail."""
        plan = ParsedPlan(
            tasks=[
                ParsedTask(
                    id="t1",
                    name="Execute code",
                    capability_id="ExecuteCode",
                    inputs={"code": "print(1)"},
                ),
            ],
            parse_success=True,
        )

        # ExecuteCode is only allowed at L2+, so L1 should fail
        validator = PlanValidator(capabilities=TEST_CAPABILITIES, scope="L1")
        result = validator.validate(plan)

        assert result.is_valid is False
        assert any("scope" in e.message.lower() for e in result.errors)

    def test_scope_allowed_passes(self):
        """Capabilities allowed at current scope should pass."""
        plan = ParsedPlan(
            tasks=[
                ParsedTask(
                    id="t1",
                    name="Write file",
                    capability_id="WriteFile",
                    inputs={"path": "t.txt", "content": "x"},
                ),
            ],
            parse_success=True,
        )

        # WriteFile is allowed at L1
        validator = PlanValidator(capabilities=TEST_CAPABILITIES, scope="L1")
        result = validator.validate(plan)

        assert result.is_valid is True

    def test_missing_required_input_fails(self):
        """Missing required inputs should fail validation."""
        plan = ParsedPlan(
            tasks=[
                ParsedTask(
                    id="t1",
                    name="Write file",
                    capability_id="WriteFile",
                    inputs={"content": "hello"},  # Missing 'path'
                ),
            ],
            parse_success=True,
        )

        validator = PlanValidator(capabilities=TEST_CAPABILITIES, scope="L2")
        result = validator.validate(plan)

        assert result.is_valid is False
        assert any("path" in e.message and "required" in e.message.lower() for e in result.errors)

    def test_optional_input_missing_is_ok(self):
        """Missing optional inputs should be fine."""
        plan = ParsedPlan(
            tasks=[
                ParsedTask(
                    id="t1",
                    name="Execute code",
                    capability_id="ExecuteCode",
                    inputs={"code": "print(1)"},  # 'timeout' is optional
                ),
            ],
            parse_success=True,
        )

        validator = PlanValidator(capabilities=TEST_CAPABILITIES, scope="L2")
        result = validator.validate(plan)

        assert result.is_valid is True

    def test_unknown_input_warns(self):
        """Unknown inputs should produce a warning (not error)."""
        plan = ParsedPlan(
            tasks=[
                ParsedTask(
                    id="t1",
                    name="Write file",
                    capability_id="WriteFile",
                    inputs={
                        "path": "t.txt",
                        "content": "x",
                        "mystery_param": "value",  # Not in schema
                    },
                ),
            ],
            parse_success=True,
        )

        validator = PlanValidator(capabilities=TEST_CAPABILITIES, scope="L2")
        result = validator.validate(plan)

        assert result.is_valid is True  # Warnings don't invalidate
        assert any("mystery_param" in w.message for w in result.warnings)

    def test_deny_list_blocks_capability(self):
        """Capabilities on the deny list should be blocked."""
        plan = ParsedPlan(
            tasks=[
                ParsedTask(
                    id="t1",
                    name="Web search",
                    capability_id="WebSearch",
                    inputs={"query": "test"},
                ),
            ],
            parse_success=True,
        )

        validator = PlanValidator(
            capabilities=TEST_CAPABILITIES,
            scope="L2",
            deny_list={"WebSearch"},
        )
        result = validator.validate(plan)

        assert result.is_valid is False
        assert any("deny" in e.message.lower() for e in result.errors)

    def test_empty_plan_warns(self):
        """An empty plan should produce a warning."""
        plan = ParsedPlan(
            tasks=[],
            parse_success=True,
        )

        validator = PlanValidator(capabilities=TEST_CAPABILITIES, scope="L2")
        result = validator.validate(plan)

        assert result.is_valid is True  # Empty but technically valid
        assert any("no tasks" in w.message.lower() for w in result.warnings)

    def test_parse_failure_propagates(self):
        """If plan didn't parse, validation reports parse errors."""
        plan = ParsedPlan(
            parse_success=False,
            parse_errors=["Bad JSON"],
        )

        validator = PlanValidator(capabilities=TEST_CAPABILITIES, scope="L2")
        result = validator.validate(plan)

        assert result.is_valid is False
        assert any("PARSE_ERROR" in e.code for e in result.errors)

    def test_clarification_needed_flagged(self):
        """Plans needing clarification should have an info issue."""
        plan = ParsedPlan(
            tasks=[],
            clarification_needed=True,
            clarification_reason="Need more info",
            parse_success=True,
        )

        validator = PlanValidator(capabilities=TEST_CAPABILITIES, scope="L2")
        result = validator.validate(plan)

        assert any("CLARIFICATION_NEEDED" in i.code for i in result.issues)

    def test_custom_validator(self):
        """Custom validators should be called for each task."""
        def custom_check(task: ParsedTask):
            if task.capability_id == "WriteFile" and "evil" in task.inputs.get("path", ""):
                return ValidationIssue(
                    severity=IssueSeverity.ERROR,
                    message="Evil path detected",
                    task_id=task.id,
                    code="EVIL_PATH",
                )
            return None

        plan = ParsedPlan(
            tasks=[
                ParsedTask(
                    id="t1",
                    name="Bad file",
                    capability_id="WriteFile",
                    inputs={"path": "evil.txt", "content": "x"},
                ),
            ],
            parse_success=True,
        )

        validator = PlanValidator(
            capabilities=TEST_CAPABILITIES,
            scope="L2",
            custom_validators=[custom_check],
        )
        result = validator.validate(plan)

        assert result.is_valid is False
        assert any("EVIL_PATH" in e.code for e in result.errors)
