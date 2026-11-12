"""
CSV Processing Plugin for LiuHao AI OS

Provides CSV parsing with different delimiters, generation,
conversion to/from JSON, validation, column operations,
and row filtering/transformation.

Capabilities:
- parse: Parse CSV string to list of dicts
- generate: Generate CSV string from data
- to_json: Convert CSV to JSON string
- from_json: Convert JSON array to CSV
- validate: Validate CSV structure
- filter_rows: Filter rows by condition
- select_columns: Select specific columns
- add_column: Add a computed column
- sort_rows: Sort rows by column
- aggregate: Aggregate column values
- transpose: Transpose rows and columns
"""

import re
import json
from typing import Dict, Any, Optional, List, Tuple, Union, Callable
from dataclasses import dataclass, field

from ..plugin_base import (
    BasePlugin,
    PluginMetadata,
    PluginStatus,
    PluginExecutionError,
)


@dataclass
class CsvParseResult:
    """Result of CSV parsing."""
    success: bool
    rows: List[Dict[str, Any]]
    headers: List[str]
    row_count: int
    column_count: int
    error: Optional[str] = None


@dataclass
class CsvValidationResult:
    """Result of CSV validation."""
    is_valid: bool
    errors: List[str]
    warnings: List[str]
    row_count: int
    column_count: int
    headers: List[str]


class CsvPlugin(BasePlugin):
    """CSV processing plugin.

    Provides CSV parsing, generation, conversion, validation,
    and data manipulation operations.

    Example:
        plugin = CsvPlugin()
        plugin.initialize()
        plugin.activate()

        result = plugin.parse("name,age\nAlice,30\nBob,25")
        print(result.rows)
    """

    def __init__(self) -> None:
        """Initialize the CSV plugin."""
        metadata = PluginMetadata(
            name="csv",
            version="1.0.0",
            description="CSV parsing, generation, validation, and transformation",
            author="LiuHao AI OS Team",
            categories=["data", "conversion", "utility", "serialization"],
            keywords=["csv", "parse", "serialize", "data", "table", "spreadsheet"],
            requires_internet=False,
            dependencies=[],
        )
        super().__init__(metadata)

        self._default_delimiter: str = ","
        self._default_quotechar: str = '"'
        self._default_has_header: bool = True

    # ==================== Lifecycle Methods ====================

    def initialize(self, config: Optional[Dict[str, Any]] = None) -> bool:
        """Initialize the CSV plugin."""
        try:
            if config:
                self._default_delimiter = config.get("delimiter", ",")
                self._default_quotechar = config.get("quotechar", '"')
                self._default_has_header = config.get("has_header", True)

            self._set_status(PluginStatus.INITIALIZED)
            return True
        except Exception as e:
            self._set_error(f"Initialization failed: {e}")
            return False

    def activate(self) -> bool:
        """Activate the CSV plugin."""
        try:
            self._set_status(PluginStatus.ACTIVE)
            return True
        except Exception as e:
            self._set_error(f"Activation failed: {e}")
            return False

    def deactivate(self) -> bool:
        """Deactivate the CSV plugin."""
        try:
            self._set_status(PluginStatus.INACTIVE)
            return True
        except Exception as e:
            self._set_error(str(e))
            return False

    def shutdown(self) -> bool:
        """Shut down the CSV plugin."""
        try:
            self._set_status(PluginStatus.SHUTDOWN)
            return True
        except Exception as e:
            self._set_error(str(e))
            return False

    def get_capabilities(self) -> List[str]:
        """Return the list of capabilities."""
        return [
            "parse",
            "generate",
            "to_json",
            "from_json",
            "validate",
            "filter_rows",
            "select_columns",
            "add_column",
            "sort_rows",
            "aggregate",
            "transpose",
            "merge",
            "deduplicate",
        ]

    # ==================== Parsing ====================

    def parse(
        self,
        csv_text: str,
        *,
        delimiter: Optional[str] = None,
        quotechar: Optional[str] = None,
        has_header: Optional[bool] = None,
        skip_empty_rows: bool = True,
    ) -> CsvParseResult:
        """Parse a CSV string into a list of dictionaries.

        Args:
            csv_text: The CSV text to parse.
            delimiter: Field delimiter character.
            quotechar: Quote character.
            has_header: Whether the first row is a header.
            skip_empty_rows: Whether to skip empty rows.

        Returns:
            CsvParseResult with parsed data.
        """
        self._ensure_active()
        self._increment_call_count()

        delim = delimiter or self._default_delimiter
        quote = quotechar or self._default_quotechar
        header = has_header if has_header is not None else self._default_has_header

        try:
            rows: List[List[str]] = []
            current_row: List[str] = []
            current_field = []
            in_quotes = False

            i = 0
            text = csv_text

            while i < len(text):
                c = text[i]

                if in_quotes:
                    if c == quote:
                        # Check for escaped quote (double quote)
                        if i + 1 < len(text) and text[i + 1] == quote:
                            current_field.append(quote)
                            i += 2
                            continue
                        else:
                            in_quotes = False
                            i += 1
                            continue
                    else:
                        current_field.append(c)
                        i += 1
                        continue
                else:
                    if c == quote:
                        in_quotes = True
                        i += 1
                        continue
                    elif c == delim:
                        current_row.append("".join(current_field))
                        current_field = []
                        i += 1
                        continue
                    elif c == "\r":
                        # Handle \r\n
                        if i + 1 < len(text) and text[i + 1] == "\n":
                            i += 1
                        current_row.append("".join(current_field))
                        current_field = []
                        rows.append(current_row)
                        current_row = []
                        i += 1
                        continue
                    elif c == "\n":
                        current_row.append("".join(current_field))
                        current_field = []
                        rows.append(current_row)
                        current_row = []
                        i += 1
                        continue
                    else:
                        current_field.append(c)
                        i += 1
                        continue

            # Handle last field/row if file doesn't end with newline
            if current_field or current_row:
                current_row.append("".join(current_field))
                rows.append(current_row)

            # Skip empty rows
            if skip_empty_rows:
                rows = [r for r in rows if any(cell.strip() for cell in r)]

            if not rows:
                return CsvParseResult(
                    success=True,
                    rows=[],
                    headers=[],
                    row_count=0,
                    column_count=0,
                )

            if header:
                headers = [h.strip() for h in rows[0]]
                data_rows = rows[1:]
            else:
                num_cols = len(rows[0])
                headers = [f"column_{i + 1}" for i in range(num_cols)]
                data_rows = rows

            # Convert to dicts
            dict_rows: List[Dict[str, Any]] = []
            for row in data_rows:
                row_dict: Dict[str, Any] = {}
                for i, h in enumerate(headers):
                    if i < len(row):
                        row_dict[h] = self._auto_type(row[i])
                    else:
                        row_dict[h] = ""
                dict_rows.append(row_dict)

            return CsvParseResult(
                success=True,
                rows=dict_rows,
                headers=headers,
                row_count=len(dict_rows),
                column_count=len(headers),
            )

        except Exception as e:
            return CsvParseResult(
                success=False,
                rows=[],
                headers=[],
                row_count=0,
                column_count=0,
                error=str(e),
            )

    def _auto_type(self, value: str) -> Any:
        """Auto-convert string value to appropriate type."""
        value = value.strip()

        if value == "":
            return ""

        # Integer
        try:
            if re.match(r"^-?\d+$", value):
                return int(value)
        except ValueError:
            pass

        # Float
        try:
            if re.match(r"^-?\d+\.\d+$", value):
                return float(value)
        except ValueError:
            pass

        # Boolean
        if value.lower() in ("true", "yes"):
            return True
        if value.lower() in ("false", "no"):
            return False

        return value

    # ==================== Generation ====================

    def generate(
        self,
        rows: List[Dict[str, Any]],
        *,
        delimiter: Optional[str] = None,
        quotechar: Optional[str] = None,
        include_header: bool = True,
        headers: Optional[List[str]] = None,
    ) -> str:
        """Generate CSV string from list of dicts.

        Args:
            rows: List of dictionaries to convert.
            delimiter: Field delimiter character.
            quotechar: Quote character.
            include_header: Whether to include header row.
            headers: Custom header list. Detected from data if None.

        Returns:
            CSV string.
        """
        self._ensure_active()
        self._increment_call_count()

        delim = delimiter or self._default_delimiter
        quote = quotechar or self._default_quotechar

        if not rows:
            return ""

        # Determine headers
        if headers is None:
            header_set: List[str] = []
            for row in rows:
                for key in row.keys():
                    if key not in header_set:
                        header_set.append(key)
            headers = header_set

        csv_lines: List[str] = []

        # Header row
        if include_header:
            csv_lines.append(self._row_to_csv(headers, delim, quote))

        # Data rows
        for row in rows:
            values = [str(row.get(h, "")) for h in headers]
            csv_lines.append(self._row_to_csv(values, delim, quote))

        return "\n".join(csv_lines) + "\n"

    def _row_to_csv(self, values: List[str], delimiter: str, quotechar: str) -> str:
        """Convert a list of values to a CSV row."""
        escaped = []
        for v in values:
            needs_quote = (
                delimiter in v
                or quotechar in v
                or "\n" in v
                or "\r" in v
            )
            if needs_quote:
                escaped_v = v.replace(quotechar, quotechar + quotechar)
                escaped.append(f"{quotechar}{escaped_v}{quotechar}")
            else:
                escaped.append(v)
        return delimiter.join(escaped)

    # ==================== Conversion ====================

    def to_json(
        self,
        csv_text: str,
        *,
        pretty: bool = True,
        delimiter: Optional[str] = None,
    ) -> str:
        """Convert CSV string to JSON string.

        Args:
            csv_text: The CSV text.
            pretty: Whether to pretty-print JSON.
            delimiter: CSV delimiter.

        Returns:
            JSON string.
        """
        self._ensure_active()
        self._increment_call_count()

        result = self.parse(csv_text, delimiter=delimiter)
        if not result.success:
            return json.dumps({"error": result.error})

        if pretty:
            return json.dumps(result.rows, indent=2, ensure_ascii=False)
        else:
            return json.dumps(result.rows, ensure_ascii=False)

    def from_json(
        self,
        json_text: str,
        *,
        delimiter: Optional[str] = None,
    ) -> str:
        """Convert JSON array to CSV string.

        Args:
            json_text: JSON array string.
            delimiter: CSV delimiter.

        Returns:
            CSV string.
        """
        self._ensure_active()
        self._increment_call_count()

        try:
            data = json.loads(json_text)
            if not isinstance(data, list):
                raise ValueError("JSON must be an array of objects")
            return self.generate(data, delimiter=delimiter)
        except json.JSONDecodeError as e:
            raise ValueError(f"Invalid JSON: {e}")

    # ==================== Validation ====================

    def validate(
        self,
        csv_text: str,
        *,
        delimiter: Optional[str] = None,
        schema: Optional[Dict[str, Any]] = None,
    ) -> CsvValidationResult:
        """Validate CSV structure and optionally against schema.

        Args:
            csv_text: The CSV text to validate.
            delimiter: CSV delimiter.
            schema: Optional schema with column types and constraints.

        Returns:
            CsvValidationResult with validation info.
        """
        self._ensure_active()
        self._increment_call_count()

        errors: List[str] = []
        warnings: List[str] = []

        result = self.parse(csv_text, delimiter=delimiter)

        if not result.success:
            return CsvValidationResult(
                is_valid=False,
                errors=[f"Parse error: {result.error}"],
                warnings=[],
                row_count=0,
                column_count=0,
                headers=[],
            )

        # Check for consistent column count
        lines = csv_text.strip().split("\n")
        expected_cols = len(result.headers)
        for line_num, line in enumerate(lines, 1):
            if not line.strip():
                continue
            # Simple count (doesn't handle quoted delimiters perfectly)
            fields = self._count_fields(line, delimiter or self._default_delimiter)
            if fields != expected_cols:
                warnings.append(
                    f"Row {line_num}: has {fields} fields, expected {expected_cols}"
                )

        # Schema validation
        if schema:
            for row_idx, row in enumerate(result.rows, 2):  # +2 for header + 1-index
                for col_name, col_schema in schema.items():
                    if col_name not in result.headers:
                        errors.append(f"Schema column '{col_name}' not found in CSV")
                        continue

                    value = row.get(col_name, "")
                    expected_type = col_schema.get("type", "string")
                    required = col_schema.get("required", False)

                    if required and (value == "" or value is None):
                        errors.append(f"Row {row_idx}, column '{col_name}': required field is empty")
                        continue

                    if value == "" or value is None:
                        continue

                    if expected_type == "integer":
                        try:
                            int(value)
                        except (ValueError, TypeError):
                            errors.append(
                                f"Row {row_idx}, column '{col_name}': "
                                f"expected integer, got '{value}'"
                            )
                    elif expected_type == "number":
                        try:
                            float(value)
                        except (ValueError, TypeError):
                            errors.append(
                                f"Row {row_idx}, column '{col_name}': "
                                f"expected number, got '{value}'"
                            )
                    elif expected_type == "email":
                        if not re.match(r"^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$", str(value)):
                            errors.append(
                                f"Row {row_idx}, column '{col_name}': "
                                f"invalid email format: '{value}'"
                            )

        return CsvValidationResult(
            is_valid=len(errors) == 0,
            errors=errors,
            warnings=warnings,
            row_count=result.row_count,
            column_count=result.column_count,
            headers=result.headers,
        )

    def _count_fields(self, line: str, delimiter: str) -> int:
        """Count fields in a CSV line, respecting quotes."""
        count = 1
        in_quotes = False
        quotechar = self._default_quotechar

        for c in line:
            if c == quotechar:
                in_quotes = not in_quotes
            elif c == delimiter and not in_quotes:
                count += 1

        return count

    # ==================== Row Operations ====================

    def filter_rows(
        self,
        rows: List[Dict[str, Any]],
        condition: Dict[str, Any],
    ) -> List[Dict[str, Any]]:
        """Filter rows based on conditions.

        Condition format:
        {"column": "age", "operator": ">=", "value": 18}
        or multiple conditions with $and/$or

        Args:
            rows: List of row dicts.
            condition: Filter condition.

        Returns:
            Filtered list of rows.
        """
        self._ensure_active()
        self._increment_call_count()

        if not rows:
            return []

        return [row for row in rows if self._matches_condition(row, condition)]

    def _matches_condition(self, row: Dict[str, Any], condition: Dict[str, Any]) -> bool:
        """Check if a row matches a filter condition."""
        if "$and" in condition:
            return all(self._matches_condition(row, c) for c in condition["$and"])
        if "$or" in condition:
            return any(self._matches_condition(row, c) for c in condition["$or"])
        if "$not" in condition:
            return not self._matches_condition(row, condition["$not"])

        column = condition.get("column", "")
        operator = condition.get("operator", "==")
        value = condition.get("value")
        row_value = row.get(column)

        if operator == "==" or operator == "eq":
            return str(row_value) == str(value)
        elif operator == "!=" or operator == "ne":
            return str(row_value) != str(value)
        elif operator == ">" or operator == "gt":
            try:
                return float(row_value) > float(value)
            except (ValueError, TypeError):
                return str(row_value) > str(value)
        elif operator == ">=" or operator == "gte":
            try:
                return float(row_value) >= float(value)
            except (ValueError, TypeError):
                return str(row_value) >= str(value)
        elif operator == "<" or operator == "lt":
            try:
                return float(row_value) < float(value)
            except (ValueError, TypeError):
                return str(row_value) < str(value)
        elif operator == "<=" or operator == "lte":
            try:
                return float(row_value) <= float(value)
            except (ValueError, TypeError):
                return str(row_value) <= str(value)
        elif operator == "contains":
            return str(value).lower() in str(row_value).lower()
        elif operator == "starts_with":
            return str(row_value).lower().startswith(str(value).lower())
        elif operator == "ends_with":
            return str(row_value).lower().endswith(str(value).lower())
        elif operator == "in":
            return row_value in (value if isinstance(value, list) else [value])
        elif operator == "regex":
            return bool(re.search(str(value), str(row_value)))
        elif operator == "is_empty":
            return row_value == "" or row_value is None
        elif operator == "not_empty":
            return row_value != "" and row_value is not None

        return False

    # ==================== Column Operations ====================

    def select_columns(
        self,
        rows: List[Dict[str, Any]],
        columns: List[str],
    ) -> List[Dict[str, Any]]:
        """Select specific columns from rows.

        Args:
            rows: List of row dicts.
            columns: List of column names to keep.

        Returns:
            Rows with only selected columns.
        """
        self._ensure_active()
        self._increment_call_count()

        return [
            {col: row.get(col, "") for col in columns}
            for row in rows
        ]

    def add_column(
        self,
        rows: List[Dict[str, Any]],
        column_name: str,
        value_or_func: Union[Any, Callable[[Dict[str, Any]], Any]],
    ) -> List[Dict[str, Any]]:
        """Add a new column to all rows.

        Args:
            rows: List of row dicts.
            column_name: Name of the new column.
            value_or_func: Static value or function(row) -> value.

        Returns:
            Rows with the new column added.
        """
        self._ensure_active()
        self._increment_call_count()

        result = []
        for row in rows:
            new_row = dict(row)
            if callable(value_or_func):
                new_row[column_name] = value_or_func(row)
            else:
                new_row[column_name] = value_or_func
            result.append(new_row)
        return result

    def rename_column(
        self,
        rows: List[Dict[str, Any]],
        old_name: str,
        new_name: str,
    ) -> List[Dict[str, Any]]:
        """Rename a column in all rows.

        Args:
            rows: List of row dicts.
            old_name: Current column name.
            new_name: New column name.

        Returns:
            Rows with renamed column.
        """
        self._ensure_active()
        self._increment_call_count()

        result = []
        for row in rows:
            new_row = {}
            for key, value in row.items():
                if key == old_name:
                    new_row[new_name] = value
                else:
                    new_row[key] = value
            result.append(new_row)
        return result

    def delete_column(
        self,
        rows: List[Dict[str, Any]],
        column_name: str,
    ) -> List[Dict[str, Any]]:
        """Delete a column from all rows.

        Args:
            rows: List of row dicts.
            column_name: Name of column to delete.

        Returns:
            Rows with the column removed.
        """
        self._ensure_active()
        self._increment_call_count()

        result = []
        for row in rows:
            new_row = {k: v for k, v in row.items() if k != column_name}
            result.append(new_row)
        return result

    # ==================== Sorting ====================

    def sort_rows(
        self,
        rows: List[Dict[str, Any]],
        column: str,
        *,
        descending: bool = False,
        numeric: bool = False,
    ) -> List[Dict[str, Any]]:
        """Sort rows by a column.

        Args:
            rows: List of row dicts.
            column: Column to sort by.
            descending: Sort in descending order.
            numeric: Treat values as numbers.

        Returns:
            Sorted list of rows.
        """
        self._ensure_active()
        self._increment_call_count()

        def sort_key(row: Dict[str, Any]) -> Any:
            value = row.get(column, "")
            if numeric:
                try:
                    return float(value)
                except (ValueError, TypeError):
                    return 0
            return str(value)

        return sorted(rows, key=sort_key, reverse=descending)

    # ==================== Aggregation ====================

    def aggregate(
        self,
        rows: List[Dict[str, Any]],
        column: str,
        operation: str = "sum",
        group_by: Optional[str] = None,
    ) -> Union[float, Dict[str, float]]:
        """Aggregate column values.

        Args:
            rows: List of row dicts.
            column: Column to aggregate.
            operation: One of sum, avg, min, max, count, count_distinct.
            group_by: Optional column to group by.

        Returns:
            Aggregate value, or dict of group -> value if group_by is set.
        """
        self._ensure_active()
        self._increment_call_count()

        if group_by:
            groups: Dict[str, List[Any]] = {}
            for row in rows:
                key = str(row.get(group_by, ""))
                value = row.get(column, "")
                groups.setdefault(key, []).append(value)
            return {
                group: self._compute_aggregation(values, operation)
                for group, values in groups.items()
            }

        values = [row.get(column, "") for row in rows]
        return self._compute_aggregation(values, operation)

    def _compute_aggregation(self, values: List[Any], operation: str) -> float:
        """Compute aggregation on a list of values."""
        operation = operation.lower()

        if operation == "count":
            return len(values)

        if operation == "count_distinct":
            return len(set(str(v) for v in values))

        # Numeric operations
        numeric_values: List[float] = []
        for v in values:
            try:
                numeric_values.append(float(v))
            except (ValueError, TypeError):
                pass

        if not numeric_values:
            return 0.0

        if operation == "sum":
            return sum(numeric_values)
        elif operation == "avg" or operation == "mean":
            return sum(numeric_values) / len(numeric_values)
        elif operation == "min":
            return min(numeric_values)
        elif operation == "max":
            return max(numeric_values)
        elif operation == "median":
            sorted_vals = sorted(numeric_values)
            n = len(sorted_vals)
            mid = n // 2
            if n % 2 == 0:
                return (sorted_vals[mid - 1] + sorted_vals[mid]) / 2
            else:
                return sorted_vals[mid]

        return 0.0

    # ==================== Other Operations ====================

    def transpose(self, rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Transpose rows and columns.

        Converts column headers to row values and vice versa.

        Args:
            rows: List of row dicts.

        Returns:
            Transposed list of rows.
        """
        self._ensure_active()
        self._increment_call_count()

        if not rows:
            return []

        headers = list(rows[0].keys())
        result: List[Dict[str, Any]] = []

        for header in headers:
            new_row: Dict[str, Any] = {"field": header}
            for i, row in enumerate(rows):
                new_row[f"row_{i + 1}"] = row.get(header, "")
            result.append(new_row)

        return result

    def deduplicate(
        self,
        rows: List[Dict[str, Any]],
        columns: Optional[List[str]] = None,
        keep: str = "first",
    ) -> List[Dict[str, Any]]:
        """Remove duplicate rows.

        Args:
            rows: List of row dicts.
            columns: Columns to consider for deduplication. All columns if None.
            keep: "first" or "last" - which duplicate to keep.

        Returns:
            Deduplicated list of rows.
        """
        self._ensure_active()
        self._increment_call_count()

        seen: Dict[str, Dict[str, Any]] = {}

        for row in rows:
            if columns:
                key_parts = [str(row.get(c, "")) for c in columns]
            else:
                key_parts = [str(row.get(k, "")) for k in sorted(row.keys())]
            key = "||".join(key_parts)

            if keep == "first":
                if key not in seen:
                    seen[key] = row
            else:  # last
                seen[key] = row

        return list(seen.values())

    def merge(
        self,
        rows1: List[Dict[str, Any]],
        rows2: List[Dict[str, Any]],
        key_column: str,
        how: str = "inner",
    ) -> List[Dict[str, Any]]:
        """Merge two CSV datasets on a key column.

        Args:
            rows1: First dataset.
            rows2: Second dataset.
            key_column: Column to join on.
            how: Join type: inner, left, right, outer.

        Returns:
            Merged list of rows.
        """
        self._ensure_active()
        self._increment_call_count()

        # Build lookup for rows2
        lookup: Dict[str, List[Dict[str, Any]]] = {}
        for row in rows2:
            key = str(row.get(key_column, ""))
            lookup.setdefault(key, []).append(row)

        result: List[Dict[str, Any]] = []
        used_keys: set = set()

        for row1 in rows1:
            key = str(row1.get(key_column, ""))
            if key in lookup:
                for row2 in lookup[key]:
                    merged = dict(row1)
                    for k, v in row2.items():
                        if k != key_column or k not in merged:
                            merged[k] = v
                    result.append(merged)
                used_keys.add(key)
            elif how in ("left", "outer"):
                result.append(dict(row1))

        if how in ("right", "outer"):
            # Add rows from right side that didn't match
            for key, matches in lookup.items():
                if key not in used_keys:
                    for row2 in matches:
                        result.append(dict(row2))

        return result

    # ==================== Statistics ====================

    def get_stats(self) -> Dict[str, Any]:
        """Get plugin statistics."""
        return {
            "default_delimiter": self._default_delimiter,
            "default_quotechar": self._default_quotechar,
            "supported_operations": [
                "parse", "generate", "filter", "sort", "aggregate",
                "select", "add_column", "rename", "delete", "transpose",
                "deduplicate", "merge",
            ],
        }
