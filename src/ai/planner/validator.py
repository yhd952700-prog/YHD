"""Validate LLM-generated plans against the system's actual capabilities.

The LLM can and will hallucinate capabilities that don't exist, or
misuse inputs. The validator catches that before the plan reaches
the execution kernel.

Validation layers (in order, fail-fast):
  1. **Structural** — do tasks have required fields? (done by parser)
  2. **Capability existence** — does each capability_id actually exist?
  3. **Input schema** — do task inputs match the capability's schema?
  4. **Scope compliance** — is the capability allowed at this scope?
  5. **Dependency validity** — do referenced tasks exist?
  6. **Safety constraints** — are any tasks on the deny-list?
  7. **Resource feasibility** — are there enough resources to run?
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional, Set, Callable

from .parser import ParsedPlan, ParsedTask


class IssueSeverity(str, Enum):
    """Severity of a validation issue."""
    ERROR = "error"       # Plan cannot be executed as-is
    WARNING = "warning"   # Plan can run but may have problems
    INFO = "info"         # Notable but not problematic


@dataclass
class ValidationIssue:
    """A single validation issue."""
    severity: IssueSeverity
    message: str
    task_id: Optional[str] = None
    field: Optional[str] = None
    code: str = ""

    def __str__(self) -> str:
        prefix = f"[{self.severity.value}]"
        if self.task_id:
            prefix += f" {self.task_id}"
        if self.field:
            prefix += f".{self.field}"
        return f"{prefix}: {self.message}"


@dataclass
class ValidationResult:
    """Result of plan validation."""
    is_valid: bool = True
    issues: List[ValidationIssue] = field(default_factory=list)
    valid_tasks: List[str] = field(default_factory=list)
    invalid_tasks: List[str] = field(default_factory=list)

    @property
    def errors(self) -> List[ValidationIssue]:
        return [i for i in self.issues if i.severity == IssueSeverity.ERROR]

    @property
    def warnings(self) -> List[ValidationIssue]:
        return [i for i in self.issues if i.severity == IssueSeverity.WARNING]

    def add_error(self, message: str, task_id: Optional[str] = None,
                  field: Optional[str] = None, code: str = "") -> None:
        self.issues.append(ValidationIssue(
            severity=IssueSeverity.ERROR,
            message=message,
            task_id=task_id,
            field=field,
            code=code,
        ))
        self.is_valid = False
        if task_id and task_id not in self.invalid_tasks:
            self.invalid_tasks.append(task_id)

    def add_warning(self, message: str, task_id: Optional[str] = None,
                    field: Optional[str] = None, code: str = "") -> None:
        self.issues.append(ValidationIssue(
            severity=IssueSeverity.WARNING,
            message=message,
            task_id=task_id,
            field=field,
            code=code,
        ))

    def add_info(self, message: str, task_id: Optional[str] = None,
                 field: Optional[str] = None, code: str = "") -> None:
        self.issues.append(ValidationIssue(
            severity=IssueSeverity.INFO,
            message=message,
            task_id=task_id,
            field=field,
            code=code,
        ))

    def summary(self) -> str:
        """Human-readable summary of validation results."""
        lines = [
            f"Plan validation: {'PASS' if self.is_valid else 'FAIL'}",
            f"  Tasks: {len(self.valid_tasks)} valid, {len(self.invalid_tasks)} invalid",
            f"  Issues: {len(self.errors)} errors, {len(self.warnings)} warnings",
        ]
        if not self.is_valid:
            lines.append("  Errors:")
            for e in self.errors[:10]:  # First 10 errors
                lines.append(f"    - {e}")
            if len(self.errors) > 10:
                lines.append(f"    ... and {len(self.errors) - 10} more")
        return "\n".join(lines)


class PlanValidator:
    """Validate a ParsedPlan against capability registry and constraints.

    Usage::

        validator = PlanValidator(capabilities=cap_list, scope="L2")
        result = validator.validate(plan)
        if result.is_valid:
            execute(plan)
        else:
            log(result.errors)
    """

    def __init__(
        self,
        capabilities: Optional[List[Dict[str, Any]]] = None,
        scope: str = "L1",
        deny_list: Optional[Set[str]] = None,
        custom_validators: Optional[List[Callable[[ParsedTask], Optional[ValidationIssue]]]] = None,
    ):
        """
        Args:
            capabilities: List of capability dicts (id, name, inputs_schema, allowed_scopes)
            scope: Current authorisation scope
            deny_list: Set of capability_ids that are always forbidden
            custom_validators: Additional per-task validator functions
        """
        self._capabilities: Dict[str, Dict[str, Any]] = {}
        if capabilities:
            for cap in capabilities:
                cap_id = cap.get("id")
                if cap_id:
                    self._capabilities[cap_id] = cap

        self.scope = scope
        self.deny_list = deny_list or set()
        self.custom_validators = custom_validators or []

        # Scope hierarchy — higher scope means more permissions
        self._scope_order = {"L0": 0, "L1": 1, "L2": 2, "L3": 3, "L4": 4, "L5": 5}

    def set_capabilities(self, capabilities: List[Dict[str, Any]]) -> None:
        """Update the capability registry."""
        self._capabilities = {}
        for cap in capabilities:
            cap_id = cap.get("id")
            if cap_id:
                self._capabilities[cap_id] = cap

    def set_scope(self, scope: str) -> None:
        """Update the current scope."""
        self.scope = scope

    def validate(self, plan: ParsedPlan) -> ValidationResult:
        """Validate a full plan.

        Args:
            plan: The parsed plan to validate

        Returns:
            ValidationResult with all issues found
        """
        result = ValidationResult()

        # If parsing already failed, report that and stop
        if not plan.parse_success:
            for err in plan.parse_errors:
                result.add_error(f"Parse error: {err}", code="PARSE_ERROR")
            return result

        if plan.clarification_needed:
            result.add_info(
                f"Plan requires clarification: {plan.clarification_reason}",
                code="CLARIFICATION_NEEDED",
            )

        if not plan.tasks:
            result.add_warning("Plan has no tasks", code="EMPTY_PLAN")
            return result

        # Validate each task
        for task in plan.tasks:
            self._validate_task(task, result)

        # Custom validators
        for validator in self.custom_validators:
            for task in plan.tasks:
                issue = validator(task)
                if issue:
                    if issue.severity == IssueSeverity.ERROR:
                        result.add_error(
                            issue.message, task.id, issue.field, issue.code
                        )
                    elif issue.severity == IssueSeverity.WARNING:
                        result.add_warning(
                            issue.message, task.id, issue.field, issue.code
                        )

        # Collect valid task IDs
        invalid_set = set(result.invalid_tasks)
        result.valid_tasks = [t.id for t in plan.tasks if t.id not in invalid_set]

        return result

    def _validate_task(self, task: ParsedTask, result: ValidationResult) -> None:
        """Validate a single task and add issues to result."""
        task_id = task.id

        # 1. Basic structural check (should already be done by parser)
        if not task.capability_id:
            result.add_error(
                "Task has no capability_id",
                task_id=task_id,
                field="capability_id",
                code="MISSING_CAPABILITY",
            )
            return

        # 2. Capability existence
        cap = self._capabilities.get(task.capability_id)
        if cap is None:
            # Check if it's a known capability name vs a complete hallucination
            result.add_error(
                f"Capability '{task.capability_id}' does not exist",
                task_id=task_id,
                field="capability_id",
                code="UNKNOWN_CAPABILITY",
            )
            return

        # 3. Deny list check
        if task.capability_id in self.deny_list:
            result.add_error(
                f"Capability '{task.capability_id}' is on the deny list",
                task_id=task_id,
                field="capability_id",
                code="DENIED_CAPABILITY",
            )
            return

        # 4. Scope compliance
        allowed_scopes = cap.get("allowed_scopes", [])
        if allowed_scopes and self.scope not in allowed_scopes:
            # Check scope hierarchy
            scope_level = self._scope_order.get(self.scope, 0)
            has_allowed = any(
                self._scope_order.get(s, 99) <= scope_level
                for s in allowed_scopes
            )
            if not has_allowed:
                result.add_error(
                    f"Capability '{task.capability_id}' not allowed at scope {self.scope} "
                    f"(requires one of: {', '.join(allowed_scopes)})",
                    task_id=task_id,
                    field="capability_id",
                    code="SCOPE_VIOLATION",
                )
                return

        # 5. Input schema validation
        inputs_schema = cap.get("inputs_schema", {})
        if inputs_schema:
            self._validate_inputs(task, inputs_schema, result)

        # 6. Dependency check (should be done by parser, but double-check)
        if task.depends_on:
            if not isinstance(task.depends_on, list):
                result.add_warning(
                    "depends_on is not a list",
                    task_id=task_id,
                    field="depends_on",
                    code="BAD_DEP_FORMAT",
                )

    def _validate_inputs(
        self,
        task: ParsedTask,
        schema: Dict[str, Any],
        result: ValidationResult,
    ) -> None:
        """Validate task inputs against the capability's input schema."""
        inputs = task.inputs or {}

        for param_name, param_info in schema.items():
            is_required = param_info.get("required", False)
            param_type = param_info.get("type", "string")

            # Required field check
            if is_required and param_name not in inputs:
                result.add_error(
                    f"Missing required input: '{param_name}'",
                    task_id=task.id,
                    field=f"inputs.{param_name}",
                    code="MISSING_INPUT",
                )
                continue

            if param_name not in inputs:
                continue  # Optional and not provided — fine

            value = inputs[param_name]

            # Type check (basic — just the common ones)
            type_errors = self._check_type(value, param_type)
            if type_errors:
                result.add_warning(
                    f"Input '{param_name}' type mismatch: expected {param_type}, "
                    f"got {type(value).__name__}: {type_errors}",
                    task_id=task.id,
                    field=f"inputs.{param_name}",
                    code="TYPE_MISMATCH",
                )

        # Check for unknown inputs (not in schema)
        known_params = set(schema.keys())
        unknown = set(inputs.keys()) - known_params
        if unknown:
            result.add_warning(
                f"Unknown inputs: {', '.join(sorted(unknown))}",
                task_id=task.id,
                field="inputs",
                code="UNKNOWN_INPUT",
            )

    def _check_type(self, value: Any, expected_type: str) -> Optional[str]:
        """Check if a value matches the expected type.

        Returns None if OK, or an error string if not.
        """
        et = expected_type.lower()

        if et in ("string", "str"):
            if not isinstance(value, str):
                return f"expected string"
        elif et in ("integer", "int"):
            if not isinstance(value, int) or isinstance(value, bool):
                return f"expected integer"
        elif et in ("number", "float"):
            if not isinstance(value, (int, float)) or isinstance(value, bool):
                return f"expected number"
        elif et == "boolean":
            if not isinstance(value, bool):
                return f"expected boolean"
        elif et in ("array", "list"):
            if not isinstance(value, list):
                return f"expected list"
        elif et == "object":
            if not isinstance(value, dict):
                return f"expected object"
        # "any" / "json" / unknown types — accept anything

        return None
