"""
Data validation stages for LiuHao AI OS pipelines.

Provides validation stages that check data quality and produce
validation reports with errors and warnings.

Validators:
    - SchemaValidator: Validate against a schema definition
    - RangeValidator: Numeric range checks
    - RegexValidator: Pattern matching
    - RequiredFieldsValidator: Check required fields
    - UniqueValidator: Uniqueness checks
    - TypeValidator: Type checking
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Pattern, Set, Tuple, Union

from .base import PipelineContext, PipelineError, PipelineStage

logger = logging.getLogger(__name__)

Record = Dict[str, Any]
Records = List[Record]


@dataclass
class ValidationIssue:
    """A single validation issue.

    Attributes:
        severity: 'error' or 'warning'.
        field: Name of the field with the issue.
        message: Human-readable description.
        record_index: Index of the record in the input list.
        value: The value that failed validation.
        validator: Name of the validator that found the issue.
    """

    severity: str
    field: str
    message: str
    record_index: int = -1
    value: Any = None
    validator: str = ""


@dataclass
class ValidationReport:
    """Report containing all validation findings.

    Attributes:
        errors: List of error-level issues.
        warnings: List of warning-level issues.
        total_records: Number of records validated.
        valid_count: Number of records with no errors.
    """

    errors: List[ValidationIssue] = field(default_factory=list)
    warnings: List[ValidationIssue] = field(default_factory=list)
    total_records: int = 0
    valid_count: int = 0

    @property
    def error_count(self) -> int:
        """Total number of errors."""
        return len(self.errors)

    @property
    def warning_count(self) -> int:
        """Total number of warnings."""
        return len(self.warnings)

    @property
    def is_valid(self) -> bool:
        """Whether validation passed (no errors)."""
        return self.error_count == 0

    def add_error(
        self,
        field: str,
        message: str,
        record_index: int = -1,
        value: Any = None,
        validator: str = "",
    ) -> None:
        """Add an error issue.

        Args:
            field: Field name.
            message: Error description.
            record_index: Record index.
            value: The invalid value.
            validator: Validator name.
        """
        self.errors.append(
            ValidationIssue(
                severity="error",
                field=field,
                message=message,
                record_index=record_index,
                value=value,
                validator=validator,
            )
        )

    def add_warning(
        self,
        field: str,
        message: str,
        record_index: int = -1,
        value: Any = None,
        validator: str = "",
    ) -> None:
        """Add a warning issue.

        Args:
            field: Field name.
            message: Warning description.
            record_index: Record index.
            value: The problematic value.
            validator: Validator name.
        """
        self.warnings.append(
            ValidationIssue(
                severity="warning",
                field=field,
                message=message,
                record_index=record_index,
                value=value,
                validator=validator,
            )
        )

    def merge(self, other: "ValidationReport") -> None:
        """Merge another report into this one.

        Args:
            other: Another validation report to merge.
        """
        self.errors.extend(other.errors)
        self.warnings.extend(other.warnings)
        self.total_records = max(self.total_records, other.total_records)

    def summary(self) -> Dict[str, Any]:
        """Get a summary dict of the validation results."""
        return {
            "total_records": self.total_records,
            "valid_count": self.valid_count,
            "error_count": self.error_count,
            "warning_count": self.warning_count,
            "is_valid": self.is_valid,
        }


class BaseValidator(PipelineStage[Records, Records]):
    """Base class for all validation stages.

    Validators are pipeline stages that check data quality.
    They pass records through unchanged but accumulate validation
    issues in a ValidationReport stored in the context.
    """

    def __init__(
        self,
        name: Optional[str] = None,
        description: Optional[str] = None,
        fail_on_error: bool = False,
    ) -> None:
        super().__init__(name=name, description=description)
        self.fail_on_error = fail_on_error
        self._report = ValidationReport()

    @property
    def report(self) -> ValidationReport:
        """The validation report from the last run."""
        return self._report

    def process(self, data: Records, context: PipelineContext) -> Records:
        """Validate records and pass them through.

        Args:
            data: Records to validate.
            context: Pipeline context.

        Returns:
            The same records (pass-through).

        Raises:
            PipelineError: If fail_on_error is True and errors are found.
        """
        self._report = ValidationReport()
        records = data or []
        self._report.total_records = len(records)

        self.validate(records, self._report)

        # Count valid records (those with no errors)
        error_record_indices = {e.record_index for e in self._report.errors if e.record_index >= 0}
        self._report.valid_count = len(records) - len(error_record_indices)

        # Store report in context
        key = f"{self.name}_report"
        existing = context.get_variable(key)
        if existing and isinstance(existing, ValidationReport):
            existing.merge(self._report)
        else:
            context.set_variable(key, self._report)

        # Also maintain a combined report
        combined = context.get_variable("validation_report")
        if combined is None:
            combined = ValidationReport()
            context.set_variable("validation_report", combined)
        combined.merge(self._report)

        if self.fail_on_error and self._report.error_count > 0:
            raise PipelineError(
                f"Validation failed with {self._report.error_count} errors",
                stage_name=self.name,
            )

        return records

    def validate(self, records: Records, report: ValidationReport) -> None:
        """Validate records and add issues to the report.

        Override this method in subclasses.

        Args:
            records: Records to validate.
            report: Report to add issues to.
        """
        raise NotImplementedError


class RequiredFieldsValidator(BaseValidator):
    """Validate that required fields are present and not null.

    Attributes:
        required_fields: List of field names that must be present.
        allow_empty_string: Whether empty strings count as valid.
    """

    def __init__(
        self,
        required_fields: List[str],
        allow_empty_string: bool = False,
        name: Optional[str] = None,
        description: Optional[str] = None,
        fail_on_error: bool = False,
    ) -> None:
        super().__init__(
            name=name or "RequiredFieldsValidator",
            description=description,
            fail_on_error=fail_on_error,
        )
        self.required_fields = required_fields
        self.allow_empty_string = allow_empty_string

    def validate(self, records: Records, report: ValidationReport) -> None:
        """Check that all required fields exist in each record."""
        for idx, record in enumerate(records):
            for field in self.required_fields:
                if field not in record:
                    report.add_error(
                        field=field,
                        message=f"Required field '{field}' is missing",
                        record_index=idx,
                        validator=self.name,
                    )
                elif record[field] is None:
                    report.add_error(
                        field=field,
                        message=f"Required field '{field}' is null",
                        record_index=idx,
                        value=None,
                        validator=self.name,
                    )
                elif isinstance(record[field], str) and not record[field].strip():
                    if not self.allow_empty_string:
                        report.add_warning(
                            field=field,
                            message=f"Field '{field}' is empty",
                            record_index=idx,
                            value="",
                            validator=self.name,
                        )


class TypeValidator(BaseValidator):
    """Validate that fields have the expected type.

    Attributes:
        type_map: Dict mapping field names to expected types.
        allow_null: Whether null values are acceptable.
    """

    def __init__(
        self,
        type_map: Dict[str, Union[type, Tuple[type, ...]]],
        allow_null: bool = True,
        name: Optional[str] = None,
        description: Optional[str] = None,
        fail_on_error: bool = False,
    ) -> None:
        super().__init__(
            name=name or "TypeValidator",
            description=description,
            fail_on_error=fail_on_error,
        )
        self.type_map = type_map
        self.allow_null = allow_null

    def _type_name(self, t: Union[type, Tuple[type, ...]]) -> str:
        """Get a human-readable type name."""
        if isinstance(t, tuple):
            return " or ".join(x.__name__ for x in t)
        return t.__name__

    def validate(self, records: Records, report: ValidationReport) -> None:
        """Check field types in each record."""
        for idx, record in enumerate(records):
            for field, expected_type in self.type_map.items():
                if field not in record:
                    continue  # Missing fields handled by RequiredFieldsValidator

                value = record[field]

                if value is None:
                    if not self.allow_null:
                        report.add_error(
                            field=field,
                            message=f"Field '{field}' is null but null not allowed",
                            record_index=idx,
                            value=None,
                            validator=self.name,
                        )
                    continue

                if not isinstance(value, expected_type):
                    report.add_error(
                        field=field,
                        message=(
                            f"Field '{field}' has type {type(value).__name__}, "
                            f"expected {self._type_name(expected_type)}"
                        ),
                        record_index=idx,
                        value=value,
                        validator=self.name,
                    )


class RangeValidator(BaseValidator):
    """Validate that numeric fields fall within a specified range.

    Attributes:
        ranges: Dict mapping field names to (min, max) tuples.
        inclusive_min: Whether the minimum is inclusive.
        inclusive_max: Whether the maximum is inclusive.
    """

    def __init__(
        self,
        ranges: Dict[str, Tuple[Optional[float], Optional[float]]],
        inclusive_min: bool = True,
        inclusive_max: bool = True,
        name: Optional[str] = None,
        description: Optional[str] = None,
        fail_on_error: bool = False,
    ) -> None:
        super().__init__(
            name=name or "RangeValidator",
            description=description,
            fail_on_error=fail_on_error,
        )
        self.ranges = ranges
        self.inclusive_min = inclusive_min
        self.inclusive_max = inclusive_max

    def validate(self, records: Records, report: ValidationReport) -> None:
        """Check numeric ranges for each record."""
        for idx, record in enumerate(records):
            for field, (min_val, max_val) in self.ranges.items():
                if field not in record or record[field] is None:
                    continue

                value = record[field]
                try:
                    num_value = float(value)
                except (ValueError, TypeError):
                    report.add_warning(
                        field=field,
                        message=f"Field '{field}' is not numeric: {value}",
                        record_index=idx,
                        value=value,
                        validator=self.name,
                    )
                    continue

                if min_val is not None:
                    if self.inclusive_min and num_value < min_val:
                        report.add_error(
                            field=field,
                            message=f"Field '{field}' = {num_value} is below minimum {min_val}",
                            record_index=idx,
                            value=num_value,
                            validator=self.name,
                        )
                    elif not self.inclusive_min and num_value <= min_val:
                        report.add_error(
                            field=field,
                            message=(
                                f"Field '{field}' = {num_value} must be greater than {min_val}"
                            ),
                            record_index=idx,
                            value=num_value,
                            validator=self.name,
                        )

                if max_val is not None:
                    if self.inclusive_max and num_value > max_val:
                        report.add_error(
                            field=field,
                            message=f"Field '{field}' = {num_value} exceeds maximum {max_val}",
                            record_index=idx,
                            value=num_value,
                            validator=self.name,
                        )
                    elif not self.inclusive_max and num_value >= max_val:
                        report.add_error(
                            field=field,
                            message=(
                                f"Field '{field}' = {num_value} must be less than {max_val}"
                            ),
                            record_index=idx,
                            value=num_value,
                            validator=self.name,
                        )


class RegexValidator(BaseValidator):
    """Validate that string fields match a regex pattern.

    Attributes:
        patterns: Dict mapping field names to regex patterns.
        match_type: 'fullmatch' (entire string) or 'search' (contains).
    """

    def __init__(
        self,
        patterns: Dict[str, Union[str, Pattern[str]]],
        match_type: str = "fullmatch",
        name: Optional[str] = None,
        description: Optional[str] = None,
        fail_on_error: bool = False,
    ) -> None:
        super().__init__(
            name=name or "RegexValidator",
            description=description,
            fail_on_error=fail_on_error,
        )
        self.patterns = {
            field: re.compile(pat) if isinstance(pat, str) else pat
            for field, pat in patterns.items()
        }
        self.match_type = match_type

        if match_type not in ("fullmatch", "search", "match"):
            raise ValueError(f"match_type must be 'fullmatch', 'search', or 'match'")

    def validate(self, records: Records, report: ValidationReport) -> None:
        """Check regex patterns for each record."""
        for idx, record in enumerate(records):
            for field, pattern in self.patterns.items():
                if field not in record or record[field] is None:
                    continue

                value = record[field]
                if not isinstance(value, str):
                    report.add_warning(
                        field=field,
                        message=f"Field '{field}' is not a string: {type(value).__name__}",
                        record_index=idx,
                        value=value,
                        validator=self.name,
                    )
                    continue

                if self.match_type == "fullmatch":
                    matched = pattern.fullmatch(value)
                elif self.match_type == "match":
                    matched = pattern.match(value)
                else:  # search
                    matched = pattern.search(value)

                if not matched:
                    report.add_error(
                        field=field,
                        message=(
                            f"Field '{field}' does not match pattern '{pattern.pattern}'"
                        ),
                        record_index=idx,
                        value=value,
                        validator=self.name,
                    )


class UniqueValidator(BaseValidator):
    """Validate that values in a field (or combination) are unique.

    Attributes:
        fields: Field name or list of field names to check for uniqueness.
    """

    def __init__(
        self,
        fields: Union[str, List[str]],
        name: Optional[str] = None,
        description: Optional[str] = None,
        fail_on_error: bool = False,
    ) -> None:
        super().__init__(
            name=name or "UniqueValidator",
            description=description,
            fail_on_error=fail_on_error,
        )
        self.fields = fields if isinstance(fields, list) else [fields]

    def validate(self, records: Records, report: ValidationReport) -> None:
        """Check for duplicate values across all records."""
        seen: Dict[Tuple[Any, ...], int] = {}

        for idx, record in enumerate(records):
            key = tuple(record.get(f) for f in self.fields)

            if key in seen:
                first_idx = seen[key]
                field_str = ", ".join(self.fields)
                report.add_error(
                    field=field_str,
                    message=(
                        f"Duplicate value for [{field_str}]: {key} "
                        f"(first seen at index {first_idx})"
                    ),
                    record_index=idx,
                    value=key,
                    validator=self.name,
                )
            else:
                seen[key] = idx


class SchemaValidator(BaseValidator):
    """Validate records against a schema definition.

    Combines multiple validation rules in a single schema definition.

    Schema format:
        {
            "fields": {
                "name": {
                    "type": "str",
                    "required": True,
                    "pattern": "^[A-Za-z ]+$",
                    "min_length": 1,
                    "max_length": 100,
                },
                "age": {
                    "type": "int",
                    "required": True,
                    "min": 0,
                    "max": 150,
                },
                "email": {
                    "type": "str",
                    "required": False,
                    "pattern": "^[\\w.-]+@[\\w.-]+\\.\\w+$",
                },
            }
        }

    Attributes:
        schema: Schema definition dict.
    """

    TYPE_MAP = {
        "str": str,
        "string": str,
        "int": int,
        "integer": int,
        "float": float,
        "number": (int, float),
        "bool": bool,
        "boolean": bool,
        "list": list,
        "dict": dict,
        "object": dict,
    }

    def __init__(
        self,
        schema: Dict[str, Any],
        name: Optional[str] = None,
        description: Optional[str] = None,
        fail_on_error: bool = False,
    ) -> None:
        super().__init__(
            name=name or "SchemaValidator",
            description=description,
            fail_on_error=fail_on_error,
        )
        self.schema = schema
        self._fields_schema: Dict[str, Dict[str, Any]] = schema.get("fields", {})

    def validate(self, records: Records, report: ValidationReport) -> None:
        """Validate all records against the schema."""
        for idx, record in enumerate(records):
            self._validate_record(record, idx, report)

    def _validate_record(
        self, record: Record, idx: int, report: ValidationReport
    ) -> None:
        """Validate a single record."""
        for field, rules in self._fields_schema.items():
            value = record.get(field)
            is_present = field in record

            # Required check
            if rules.get("required", False):
                if not is_present or value is None:
                    report.add_error(
                        field=field,
                        message=f"Required field '{field}' is missing or null",
                        record_index=idx,
                        value=value,
                        validator=self.name,
                    )
                    continue

            if not is_present or value is None:
                continue

            # Type check
            type_name = rules.get("type")
            if type_name:
                expected_type = self.TYPE_MAP.get(type_name.lower())
                if expected_type and not isinstance(value, expected_type):
                    report.add_error(
                        field=field,
                        message=(
                            f"Field '{field}' has type {type(value).__name__}, "
                            f"expected {type_name}"
                        ),
                        record_index=idx,
                        value=value,
                        validator=self.name,
                    )
                    continue

            # String-specific checks
            if isinstance(value, str):
                min_length = rules.get("min_length")
                max_length = rules.get("max_length")
                if min_length is not None and len(value) < min_length:
                    report.add_error(
                        field=field,
                        message=(
                            f"Field '{field}' length {len(value)} < minimum {min_length}"
                        ),
                        record_index=idx,
                        value=value,
                        validator=self.name,
                    )
                if max_length is not None and len(value) > max_length:
                    report.add_error(
                        field=field,
                        message=(
                            f"Field '{field}' length {len(value)} > maximum {max_length}"
                        ),
                        record_index=idx,
                        value=value,
                        validator=self.name,
                    )

                # Pattern check
                pattern_str = rules.get("pattern")
                if pattern_str:
                    try:
                        pattern = re.compile(pattern_str)
                        if not pattern.fullmatch(value):
                            report.add_error(
                                field=field,
                                message=(
                                    f"Field '{field}' does not match pattern '{pattern_str}'"
                                ),
                                record_index=idx,
                                value=value,
                                validator=self.name,
                            )
                    except re.error as e:
                        report.add_warning(
                            field=field,
                            message=f"Invalid pattern for field '{field}': {e}",
                            validator=self.name,
                        )

            # Numeric checks
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                min_val = rules.get("min")
                max_val = rules.get("max")
                if min_val is not None and value < min_val:
                    report.add_error(
                        field=field,
                        message=f"Field '{field}' = {value} is below minimum {min_val}",
                        record_index=idx,
                        value=value,
                        validator=self.name,
                    )
                if max_val is not None and value > max_val:
                    report.add_error(
                        field=field,
                        message=f"Field '{field}' = {value} exceeds maximum {max_val}",
                        record_index=idx,
                        value=value,
                        validator=self.name,
                    )

            # Enum check
            enum_values = rules.get("enum")
            if enum_values and value not in enum_values:
                report.add_error(
                    field=field,
                    message=(
                        f"Field '{field}' = {value!r} is not in allowed values: {enum_values}"
                    ),
                    record_index=idx,
                    value=value,
                    validator=self.name,
                )
