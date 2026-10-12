"""
JSON Toolkit Plugin for LiuHao AI OS

Provides JSON validation, formatting, minification, diff,
JSONPath query, schema validation, and conversion to CSV/YAML/XML.

Capabilities:
- validate: Validate JSON syntax
- format: Format/beautify JSON
- minify: Minify JSON
- diff: Compute diff between two JSON objects
- jsonpath: Query JSON using JSONPath expressions
- schema_validate: Validate JSON against JSON Schema
- to_csv: Convert JSON array to CSV
- to_yaml: Convert JSON to YAML string
- to_xml: Convert JSON to XML string
- sort_keys: Sort JSON keys recursively
- flatten: Flatten nested JSON
- unflatten: Unflatten to nested JSON
"""

import re
import json
from typing import Dict, Any, Optional, List, Tuple, Union
from dataclasses import dataclass, field
from difflib import SequenceMatcher

from ..plugin_base import (
    BasePlugin,
    PluginMetadata,
    PluginStatus,
    PluginExecutionError,
)


@dataclass
class JsonValidationResult:
    """Result of JSON validation."""
    is_valid: bool
    error: Optional[str] = None
    error_line: Optional[int] = None
    error_column: Optional[int] = None
    structure: Optional[str] = None  # object, array, string, etc.


@dataclass
class DiffEntry:
    """A single diff entry."""
    path: str
    operation: str  # added, removed, changed
    old_value: Any = None
    new_value: Any = None


@dataclass
class DiffResult:
    """Result of JSON diff."""
    changes: List[DiffEntry]
    added_count: int
    removed_count: int
    changed_count: int
    unchanged_count: int
    similarity: float


class JsonToolPlugin(BasePlugin):
    """JSON toolkit plugin.

    Provides JSON validation, formatting, minification, diff,
    JSONPath query, and schema validation.

    Example:
        plugin = JsonToolPlugin()
        plugin.initialize()
        plugin.activate()

        result = plugin.format('{"a": 1, "b": 2}')
        print(result)
    """

    def __init__(self) -> None:
        """Initialize the JSON toolkit plugin."""
        metadata = PluginMetadata(
            name="json_tool",
            version="1.0.0",
            description="JSON validation, formatting, diff, query, and conversion",
            author="LiuHao AI OS Team",
            categories=["data", "utility", "serialization", "development"],
            keywords=["json", "validate", "format", "diff", "jsonpath", "schema"],
            requires_internet=False,
            dependencies=[],
        )
        super().__init__(metadata)

        self._default_indent: int = 2
        self._max_diff_depth: int = 20

    # ==================== Lifecycle Methods ====================

    def initialize(self, config: Optional[Dict[str, Any]] = None) -> bool:
        """Initialize the JSON toolkit plugin."""
        try:
            if config:
                self._default_indent = config.get("default_indent", 2)
                self._max_diff_depth = config.get("max_diff_depth", 20)

            self._set_status(PluginStatus.INITIALIZED)
            return True
        except Exception as e:
            self._set_error(f"Initialization failed: {e}")
            return False

    def activate(self) -> bool:
        """Activate the JSON toolkit plugin."""
        try:
            self._set_status(PluginStatus.ACTIVE)
            return True
        except Exception as e:
            self._set_error(f"Activation failed: {e}")
            return False

    def deactivate(self) -> bool:
        """Deactivate the JSON toolkit plugin."""
        try:
            self._set_status(PluginStatus.INACTIVE)
            return True
        except Exception as e:
            self._set_error(str(e))
            return False

    def shutdown(self) -> bool:
        """Shut down the JSON toolkit plugin."""
        try:
            self._set_status(PluginStatus.SHUTDOWN)
            return True
        except Exception as e:
            self._set_error(str(e))
            return False

    def get_capabilities(self) -> List[str]:
        """Return the list of capabilities."""
        return [
            "validate",
            "format",
            "minify",
            "diff",
            "jsonpath",
            "schema_validate",
            "to_csv",
            "to_yaml",
            "to_xml",
            "sort_keys",
            "flatten",
            "unflatten",
            "count_elements",
            "find_value",
        ]

    # ==================== Validation ====================

    def validate(self, json_text: str) -> JsonValidationResult:
        """Validate JSON syntax.

        Args:
            json_text: The JSON text to validate.

        Returns:
            JsonValidationResult with validity and error info.
        """
        self._ensure_active()
        self._increment_call_count()

        try:
            data = json.loads(json_text)
            structure = type(data).__name__
            return JsonValidationResult(
                is_valid=True,
                structure=structure,
            )
        except json.JSONDecodeError as e:
            return JsonValidationResult(
                is_valid=False,
                error=str(e),
                error_line=e.lineno,
                error_column=e.colno,
            )
        except Exception as e:
            return JsonValidationResult(
                is_valid=False,
                error=str(e),
            )

    # ==================== Formatting ====================

    def format(
        self,
        json_text: str,
        *,
        indent: Optional[int] = None,
        sort_keys: bool = False,
        ensure_ascii: bool = False,
    ) -> str:
        """Format/beautify JSON text.

        Args:
            json_text: The JSON text to format.
            indent: Indentation size.
            sort_keys: Whether to sort keys alphabetically.
            ensure_ascii: Whether to escape non-ASCII characters.

        Returns:
            Formatted JSON string.
        """
        self._ensure_active()
        self._increment_call_count()

        indent = indent if indent is not None else self._default_indent
        data = json.loads(json_text)
        return json.dumps(data, indent=indent, sort_keys=sort_keys, ensure_ascii=ensure_ascii)

    def minify(
        self,
        json_text: str,
        *,
        ensure_ascii: bool = False,
    ) -> str:
        """Minify JSON text (remove whitespace).

        Args:
            json_text: The JSON text to minify.
            ensure_ascii: Whether to escape non-ASCII characters.

        Returns:
            Minified JSON string.
        """
        self._ensure_active()
        self._increment_call_count()

        data = json.loads(json_text)
        return json.dumps(data, separators=(",", ":"), ensure_ascii=ensure_ascii)

    def sort_keys(self, json_text: str, *, indent: Optional[int] = None) -> str:
        """Sort JSON keys recursively.

        Args:
            json_text: The JSON text.
            indent: Output indentation.

        Returns:
            JSON with sorted keys.
        """
        self._ensure_active()
        self._increment_call_count()

        indent = indent if indent is not None else self._default_indent
        data = json.loads(json_text)
        return json.dumps(data, indent=indent, sort_keys=True, ensure_ascii=False)

    # ==================== Diff ====================

    def diff(
        self,
        old_json: Union[str, Dict[str, Any]],
        new_json: Union[str, Dict[str, Any]],
        *,
        max_depth: Optional[int] = None,
    ) -> DiffResult:
        """Compute the diff between two JSON objects.

        Args:
            old_json: Original JSON string or dict.
            new_json: New JSON string or dict.
            max_depth: Maximum depth to compare.

        Returns:
            DiffResult with all changes.
        """
        self._ensure_active()
        self._increment_call_count()

        # Parse if strings
        if isinstance(old_json, str):
            old_data = json.loads(old_json)
        else:
            old_data = old_json

        if isinstance(new_json, str):
            new_data = json.loads(new_json)
        else:
            new_data = new_json

        max_depth = max_depth or self._max_diff_depth
        changes: List[DiffEntry] = []

        self._diff_value(old_data, new_data, "$", changes, 0, max_depth)

        added = sum(1 for c in changes if c.operation == "added")
        removed = sum(1 for c in changes if c.operation == "removed")
        changed = sum(1 for c in changes if c.operation == "changed")

        # Calculate similarity
        total_keys = self._count_elements(old_data) + self._count_elements(new_data)
        if total_keys > 0:
            unchanged = max(0, total_keys - added - removed - changed * 2)
            similarity = 1.0 - (added + removed + changed) / max(1, total_keys / 2)
            similarity = max(0.0, min(1.0, similarity))
        else:
            unchanged = 0
            similarity = 1.0

        return DiffResult(
            changes=changes,
            added_count=added,
            removed_count=removed,
            changed_count=changed,
            unchanged_count=unchanged,
            similarity=round(similarity, 4),
        )

    def _diff_value(
        self,
        old_val: Any,
        new_val: Any,
        path: str,
        changes: List[DiffEntry],
        depth: int,
        max_depth: int,
    ) -> None:
        """Recursively compute diff between two values."""
        if depth > max_depth:
            return

        if type(old_val) != type(new_val):
            changes.append(DiffEntry(
                path=path,
                operation="changed",
                old_value=old_val,
                new_value=new_val,
            ))
            return

        if isinstance(old_val, dict):
            self._diff_dict(old_val, new_val, path, changes, depth, max_depth)
        elif isinstance(old_val, list):
            self._diff_list(old_val, new_val, path, changes, depth, max_depth)
        else:
            if old_val != new_val:
                changes.append(DiffEntry(
                    path=path,
                    operation="changed",
                    old_value=old_val,
                    new_value=new_val,
                ))

    def _diff_dict(
        self,
        old: Dict[str, Any],
        new: Dict[str, Any],
        path: str,
        changes: List[DiffEntry],
        depth: int,
        max_depth: int,
    ) -> None:
        """Diff two dictionaries."""
        all_keys = set(old.keys()) | set(new.keys())

        for key in sorted(all_keys):
            key_path = f"{path}.{key}" if path != "$" else f"$.{key}"

            if key not in old:
                changes.append(DiffEntry(
                    path=key_path,
                    operation="added",
                    new_value=new[key],
                ))
            elif key not in new:
                changes.append(DiffEntry(
                    path=key_path,
                    operation="removed",
                    old_value=old[key],
                ))
            else:
                self._diff_value(old[key], new[key], key_path, changes, depth + 1, max_depth)

    def _diff_list(
        self,
        old: List[Any],
        new: List[Any],
        path: str,
        changes: List[DiffEntry],
        depth: int,
        max_depth: int,
    ) -> None:
        """Diff two lists (by index)."""
        min_len = min(len(old), len(new))

        for i in range(min_len):
            idx_path = f"{path}[{i}]"
            self._diff_value(old[i], new[i], idx_path, changes, depth + 1, max_depth)

        # Extra elements in new
        for i in range(min_len, len(new)):
            idx_path = f"{path}[{i}]"
            changes.append(DiffEntry(
                path=idx_path,
                operation="added",
                new_value=new[i],
            ))

        # Missing elements from old
        for i in range(min_len, len(old)):
            idx_path = f"{path}[{i}]"
            changes.append(DiffEntry(
                path=idx_path,
                operation="removed",
                old_value=old[i],
            ))

    def _count_elements(self, data: Any) -> int:
        """Count total elements in a JSON structure."""
        if isinstance(data, dict):
            return 1 + sum(self._count_elements(v) for v in data.values())
        elif isinstance(data, list):
            return 1 + sum(self._count_elements(v) for v in data)
        else:
            return 1

    # ==================== JSONPath Query ====================

    def jsonpath(
        self,
        json_data: Union[str, Dict[str, Any], List[Any]],
        expression: str,
    ) -> List[Any]:
        """Query JSON data using JSONPath expressions.

        Supports a subset of JSONPath:
        - $.store.book[0].title
        - $.store.book[*].author
        - $..author (recursive descent)
        - $.store.book[?(@.price < 10)]
        - $.store.book[:2] (slice)
        - $.store.book[0,1] (union)

        Args:
            json_data: JSON string or parsed data.
            expression: JSONPath expression.

        Returns:
            List of matching values.
        """
        self._ensure_active()
        self._increment_call_count()

        if isinstance(json_data, str):
            data = json.loads(json_data)
        else:
            data = json_data

        # Parse the expression
        if not expression.startswith("$"):
            expression = "$" + expression

        return self._evaluate_path(data, expression)

    def _evaluate_path(self, data: Any, expression: str) -> List[Any]:
        """Evaluate a JSONPath expression."""
        results: List[Any] = []
        tokens = self._tokenize_path(expression)
        current_nodes = [data]

        for token in tokens[1:]:  # Skip the root $
            next_nodes: List[Any] = []

            for node in current_nodes:
                matches = self._apply_token(node, token)
                next_nodes.extend(matches)

            current_nodes = next_nodes
            if not current_nodes:
                break

        return current_nodes

    def _tokenize_path(self, expression: str) -> List[str]:
        """Tokenize a JSONPath expression."""
        tokens: List[str] = []
        i = 0

        while i < len(expression):
            c = expression[i]

            if c == "$":
                tokens.append("$")
                i += 1
            elif c == ".":
                # Dot notation
                if i + 1 < len(expression) and expression[i + 1] == ".":
                    # Recursive descent
                    tokens.append("..")
                    i += 2
                else:
                    # Property name
                    i += 1
                    j = i
                    while j < len(expression) and expression[j] not in ".[]()?":
                        j += 1
                    tokens.append("." + expression[i:j])
                    i = j
            elif c == "[":
                # Bracket notation
                j = i + 1
                depth = 1
                while j < len(expression) and depth > 0:
                    if expression[j] == "[":
                        depth += 1
                    elif expression[j] == "]":
                        depth -= 1
                    j += 1
                tokens.append(expression[i:j])
                i = j
            elif c == "?":
                # Filter expression
                j = i
                paren_depth = 0
                while j < len(expression):
                    if expression[j] == "(":
                        paren_depth += 1
                    elif expression[j] == ")":
                        paren_depth -= 1
                        if paren_depth == 0:
                            j += 1
                            break
                    j += 1
                tokens.append(expression[i:j])
                i = j
            else:
                i += 1

        return tokens

    def _apply_token(self, node: Any, token: str) -> List[Any]:
        """Apply a single path token to a node."""
        results: List[Any] = []

        if token.startswith(".") and token != "..":
            # Property access
            prop = token[1:]
            if isinstance(node, dict) and prop in node:
                results.append(node[prop])

        elif token == "..":
            # Recursive descent - collect all
            self._collect_all(node, results)

        elif token.startswith("["):
            # Bracket access
            content = token[1:-1]

            if content == "*" or content == "":
                # All elements
                if isinstance(node, list):
                    results.extend(node)
                elif isinstance(node, dict):
                    results.extend(node.values())

            elif content.startswith("?"):
                # Filter
                if isinstance(node, list):
                    for item in node:
                        if self._evaluate_filter(item, content):
                            results.append(item)

            elif ":" in content:
                # Slice
                if isinstance(node, list):
                    parts = content.split(":")
                    start = int(parts[0]) if parts[0] else 0
                    end = int(parts[1]) if len(parts) > 1 and parts[1] else len(node)
                    step = int(parts[2]) if len(parts) > 2 and parts[2] else 1
                    results.extend(node[start:end:step])

            elif "," in content:
                # Union of indices
                if isinstance(node, list):
                    for idx_str in content.split(","):
                        try:
                            idx = int(idx_str.strip())
                            if 0 <= idx < len(node):
                                results.append(node[idx])
                        except ValueError:
                            pass
                elif isinstance(node, dict):
                    for key in content.split(","):
                        key = key.strip().strip("'\"")
                        if key in node:
                            results.append(node[key])

            else:
                # Single index or key
                if isinstance(node, list):
                    try:
                        idx = int(content)
                        if -len(node) <= idx < len(node):
                            results.append(node[idx])
                    except ValueError:
                        pass
                elif isinstance(node, dict):
                    key = content.strip("'\"")
                    if key in node:
                        results.append(node[key])

        return results

    def _collect_all(self, node: Any, results: List[Any]) -> None:
        """Recursively collect all values."""
        if isinstance(node, dict):
            for value in node.values():
                results.append(value)
                self._collect_all(value, results)
        elif isinstance(node, list):
            for item in node:
                results.append(item)
                self._collect_all(item, results)

    def _evaluate_filter(self, item: Any, filter_expr: str) -> bool:
        """Evaluate a filter expression (basic support)."""
        # Simple filter: ?(@.key op value)
        expr = filter_expr[2:-1] if filter_expr.startswith("?(") else filter_expr
        expr = expr.replace("@.", "")

        # Parse simple comparisons
        ops = [">=", "<=", "!=", "==", ">", "<", "="]
        for op in ops:
            if op in expr:
                key, _, value = expr.partition(op)
                key = key.strip()
                value = value.strip().strip("'\"")

                if not isinstance(item, dict) or key not in item:
                    return False

                item_val = item[key]
                op_normalized = "==" if op == "=" else op

                try:
                    num_item = float(item_val)
                    num_val = float(value)
                    if op_normalized == "==":
                        return num_item == num_val
                    elif op_normalized == "!=":
                        return num_item != num_val
                    elif op_normalized == ">":
                        return num_item > num_val
                    elif op_normalized == ">=":
                        return num_item >= num_val
                    elif op_normalized == "<":
                        return num_item < num_val
                    elif op_normalized == "<=":
                        return num_item <= num_val
                except (ValueError, TypeError):
                    if op_normalized == "==":
                        return str(item_val) == value
                    elif op_normalized == "!=":
                        return str(item_val) != value

        return True

    # ==================== Schema Validation ====================

    def schema_validate(
        self,
        json_data: Union[str, Any],
        schema: Union[str, Dict[str, Any]],
    ) -> Tuple[bool, List[str]]:
        """Validate JSON data against a JSON Schema (subset).

        Supports: type, properties, required, items, enum,
        minimum, maximum, minLength, maxLength, pattern,
        minItems, maxItems.

        Args:
            json_data: JSON data to validate.
            schema: JSON Schema definition.

        Returns:
            Tuple of (is_valid, list_of_errors).
        """
        self._ensure_active()
        self._increment_call_count()

        if isinstance(json_data, str):
            data = json.loads(json_data)
        else:
            data = json_data

        if isinstance(schema, str):
            schema_dict = json.loads(schema)
        else:
            schema_dict = schema

        errors: List[str] = []
        self._validate_schema(data, schema_dict, "$", errors)
        return len(errors) == 0, errors

    def _validate_schema(
        self,
        data: Any,
        schema: Dict[str, Any],
        path: str,
        errors: List[str],
    ) -> None:
        """Validate data against schema recursively."""
        expected_type = schema.get("type")

        if expected_type:
            if not self._check_type(data, expected_type):
                errors.append(f"{path}: expected type '{expected_type}', got '{type(data).__name__}'")
                return

        if "enum" in schema:
            if data not in schema["enum"]:
                errors.append(f"{path}: value must be one of {schema['enum']}")

        if isinstance(data, dict):
            properties = schema.get("properties", {})
            required = schema.get("required", [])

            for prop in required:
                if prop not in data:
                    errors.append(f"{path}.{prop}: required property missing")

            for prop, prop_schema in properties.items():
                if prop in data:
                    self._validate_schema(data[prop], prop_schema, f"{path}.{prop}", errors)

        elif isinstance(data, list):
            items_schema = schema.get("items")
            if items_schema:
                for i, item in enumerate(data):
                    self._validate_schema(item, items_schema, f"{path}[{i}]", errors)

            if "minItems" in schema and len(data) < schema["minItems"]:
                errors.append(f"{path}: must have at least {schema['minItems']} items")
            if "maxItems" in schema and len(data) > schema["maxItems"]:
                errors.append(f"{path}: must have at most {schema['maxItems']} items")

        elif isinstance(data, str):
            if "minLength" in schema and len(data) < schema["minLength"]:
                errors.append(f"{path}: must be at least {schema['minLength']} characters")
            if "maxLength" in schema and len(data) > schema["maxLength"]:
                errors.append(f"{path}: must be at most {schema['maxLength']} characters")
            if "pattern" in schema and not re.search(schema["pattern"], data):
                errors.append(f"{path}: does not match pattern '{schema['pattern']}'")

        elif isinstance(data, (int, float)):
            if "minimum" in schema and data < schema["minimum"]:
                errors.append(f"{path}: must be >= {schema['minimum']}")
            if "maximum" in schema and data > schema["maximum"]:
                errors.append(f"{path}: must be <= {schema['maximum']}")
            if "exclusiveMinimum" in schema and data <= schema["exclusiveMinimum"]:
                errors.append(f"{path}: must be > {schema['exclusiveMinimum']}")
            if "exclusiveMaximum" in schema and data >= schema["exclusiveMaximum"]:
                errors.append(f"{path}: must be < {schema['exclusiveMaximum']}")

    def _check_type(self, data: Any, expected: Union[str, List[str]]) -> bool:
        """Check if data matches expected JSON type."""
        if isinstance(expected, list):
            return any(self._check_type(data, t) for t in expected)

        if expected == "object":
            return isinstance(data, dict)
        elif expected == "array":
            return isinstance(data, list)
        elif expected == "string":
            return isinstance(data, str)
        elif expected == "integer":
            return isinstance(data, int) and not isinstance(data, bool)
        elif expected == "number":
            return isinstance(data, (int, float)) and not isinstance(data, bool)
        elif expected == "boolean":
            return isinstance(data, bool)
        elif expected == "null":
            return data is None

        return False

    # ==================== Conversions ====================

    def to_csv(self, json_text: str) -> str:
        """Convert JSON array to CSV string.

        Args:
            json_text: JSON array of objects.

        Returns:
            CSV string.
        """
        self._ensure_active()
        self._increment_call_count()

        data = json.loads(json_text)
        if not isinstance(data, list):
            raise ValueError("JSON must be an array of objects")

        if not data:
            return ""

        # Flatten nested dicts for CSV
        flat_data = [self._flatten_for_csv(row) for row in data]

        # Get all headers
        headers: List[str] = []
        for row in flat_data:
            for key in row.keys():
                if key not in headers:
                    headers.append(key)

        # Generate CSV
        lines = [",".join(self._csv_escape(h) for h in headers)]
        for row in flat_data:
            values = [self._csv_escape(str(row.get(h, ""))) for h in headers]
            lines.append(",".join(values))

        return "\n".join(lines) + "\n"

    def _flatten_for_csv(self, obj: Any, prefix: str = "") -> Dict[str, str]:
        """Flatten nested dict for CSV output."""
        result: Dict[str, str] = {}

        if isinstance(obj, dict):
            for key, value in obj.items():
                new_key = f"{prefix}.{key}" if prefix else key
                if isinstance(value, (dict, list)):
                    result[new_key] = json.dumps(value, ensure_ascii=False)
                else:
                    result[new_key] = str(value)
        elif isinstance(obj, list):
            result[prefix or "value"] = json.dumps(obj, ensure_ascii=False)
        else:
            result[prefix or "value"] = str(obj)

        return result

    def _csv_escape(self, value: str) -> str:
        """Escape a value for CSV output."""
        if "," in value or '"' in value or "\n" in value:
            return '"' + value.replace('"', '""') + '"'
        return value

    def to_yaml(self, json_text: str, *, indent: int = 2) -> str:
        """Convert JSON to YAML string (simple conversion).

        Args:
            json_text: JSON string.
            indent: YAML indentation size.

        Returns:
            YAML string.
        """
        self._ensure_active()
        self._increment_call_count()

        data = json.loads(json_text)
        return self._value_to_yaml(data, indent, 0)

    def _value_to_yaml(self, value: Any, indent: int, level: int) -> str:
        """Convert a Python value to YAML string."""
        prefix = " " * (level * indent)

        if value is None:
            return "null"
        elif isinstance(value, bool):
            return "true" if value else "false"
        elif isinstance(value, (int, float)):
            return str(value)
        elif isinstance(value, str):
            if "\n" in value:
                lines = value.split("\n")
                result = "|"
                for line in lines:
                    if line:
                        result += "\n" + prefix + "  " + line
                    else:
                        result += "\n"
                return result
            if re.match(r"^[a-zA-Z_][\w\s-]*$", value) and value.lower() not in ("true", "false", "yes", "no", "null"):
                return value
            return '"' + value.replace('"', '\\"') + '"'
        elif isinstance(value, list):
            if not value:
                return "[]"
            lines: List[str] = []
            for item in value:
                if isinstance(item, dict):
                    dict_yaml = self._value_to_yaml(item, indent, level + 1)
                    dict_lines = dict_yaml.split("\n")
                    first = dict_lines[0].lstrip()
                    lines.append(f"{prefix}- {first}")
                    lines.extend(dict_lines[1:])
                else:
                    item_yaml = self._value_to_yaml(item, indent, level + 1)
                    lines.append(f"{prefix}- {item_yaml}")
            return "\n".join(lines)
        elif isinstance(value, dict):
            if not value:
                return "{}"
            lines: List[str] = []
            for key, val in value.items():
                if isinstance(val, (dict, list)) and val:
                    lines.append(f"{prefix}{key}:")
                    lines.append(self._value_to_yaml(val, indent, level + 1))
                else:
                    val_str = self._value_to_yaml(val, indent, level + 1)
                    lines.append(f"{prefix}{key}: {val_str}")
            return "\n".join(lines)
        return str(value)

    def to_xml(self, json_text: str, *, root: str = "root", pretty: bool = True) -> str:
        """Convert JSON to XML string.

        Args:
            json_text: JSON string.
            root: Root element name.
            pretty: Whether to indent output.

        Returns:
            XML string.
        """
        self._ensure_active()
        self._increment_call_count()

        data = json.loads(json_text)
        xml_content = self._value_to_xml(data, root, 0 if pretty else -1)

        declaration = '<?xml version="1.0" encoding="UTF-8"?>\n'
        return declaration + xml_content

    def _value_to_xml(self, value: Any, tag: str, indent_level: int) -> str:
        """Convert a value to XML element."""
        indent = "  " * indent_level if indent_level >= 0 else ""
        newline = "\n" if indent_level >= 0 else ""

        if value is None:
            return f'{indent}<{tag}/>{newline}'
        elif isinstance(value, bool):
            return f'{indent}<{tag}>{"true" if value else "false"}</{tag}>{newline}'
        elif isinstance(value, (int, float)):
            return f'{indent}<{tag}>{value}</{tag}>{newline}'
        elif isinstance(value, str):
            escaped = (value.replace("&", "&amp;")
                       .replace("<", "&lt;")
                       .replace(">", "&gt;")
                       .replace('"', "&quot;"))
            return f'{indent}<{tag}>{escaped}</{tag}>{newline}'
        elif isinstance(value, list):
            parts = [f"{indent}<{tag}>{newline}"]
            for item in value:
                if indent_level >= 0:
                    parts.append(self._value_to_xml(item, "item", indent_level + 1))
                else:
                    parts.append(self._value_to_xml(item, "item", -1))
            parts.append(f"{indent}</{tag}>{newline}")
            return "".join(parts)
        elif isinstance(value, dict):
            parts = [f"{indent}<{tag}>{newline}"]
            for key, val in value.items():
                safe_key = re.sub(r"[^a-zA-Z0-9_-]", "_", str(key))
                if safe_key and not safe_key[0].isalpha():
                    safe_key = "_" + safe_key
                if indent_level >= 0:
                    parts.append(self._value_to_xml(val, safe_key, indent_level + 1))
                else:
                    parts.append(self._value_to_xml(val, safe_key, -1))
            parts.append(f"{indent}</{tag}>{newline}")
            return "".join(parts)
        return f"{indent}<{tag}>{value}</{tag}>{newline}"

    # ==================== Flatten / Unflatten ====================

    def flatten(self, data: Dict[str, Any], separator: str = ".") -> Dict[str, Any]:
        """Flatten nested JSON to flat key-value pairs.

        Args:
            data: Nested dictionary.
            separator: Key separator.

        Returns:
            Flattened dictionary.
        """
        self._ensure_active()
        self._increment_call_count()

        result: Dict[str, Any] = {}
        self._flatten_recursive(data, "", separator, result)
        return result

    def _flatten_recursive(
        self, obj: Any, prefix: str, separator: str, result: Dict[str, Any]
    ) -> None:
        """Recursively flatten nested structures."""
        if isinstance(obj, dict):
            if not obj and prefix:
                result[prefix] = {}
                return
            for key, value in obj.items():
                new_key = f"{prefix}{separator}{key}" if prefix else key
                if isinstance(value, (dict, list)):
                    self._flatten_recursive(value, new_key, separator, result)
                else:
                    result[new_key] = value
        elif isinstance(obj, list):
            if not obj and prefix:
                result[prefix] = []
                return
            for i, item in enumerate(obj):
                new_key = f"{prefix}[{i}]" if prefix else f"[{i}]"
                if isinstance(item, (dict, list)):
                    self._flatten_recursive(item, new_key, separator, result)
                else:
                    result[new_key] = item

    def unflatten(self, flat_data: Dict[str, Any], separator: str = ".") -> Dict[str, Any]:
        """Unflatten a flat dict to nested structure.

        Args:
            flat_data: Flat dictionary with dot-notation keys.
            separator: Key separator.

        Returns:
            Nested dictionary.
        """
        self._ensure_active()
        self._increment_call_count()

        result: Dict[str, Any] = {}

        for key, value in flat_data.items():
            self._set_nested_value(result, key, value, separator)

        return result

    def _set_nested_value(
        self, obj: Dict[str, Any], key: str, value: Any, separator: str
    ) -> None:
        """Set a value in a nested structure from a flat key."""
        parts = self._parse_flat_key(key, separator)
        current: Any = obj

        for i, (part_type, part_val) in enumerate(parts[:-1]):
            next_type, _ = parts[i + 1] if i + 1 < len(parts) else (None, None)

            if part_type == "key":
                if part_val not in current or not isinstance(current[part_val], dict):
                    current[part_val] = {} if next_type == "key" else []
                current = current[part_val]
            elif part_type == "index":
                idx = int(part_val)
                while len(current) <= idx:
                    current.append({} if next_type == "key" else [])
                current = current[idx]

        # Set final value
        last_type, last_val = parts[-1]
        if last_type == "key":
            current[last_val] = value
        elif last_type == "index":
            idx = int(last_val)
            while len(current) <= idx:
                current.append(None)
            current[idx] = value

    def _parse_flat_key(self, key: str, separator: str) -> List[Tuple[str, str]]:
        """Parse a flat key into parts (key/index)."""
        parts: List[Tuple[str, str]] = []

        # Split by separator, but handle array indices
        tokens = key.split(separator)
        for token in tokens:
            # Handle array indices like key[0]
            arr_match = re.match(r"^([^\[]+)(\[.+\])$", token)
            if arr_match:
                name = arr_match.group(1)
                indices_str = arr_match.group(2)
                parts.append(("key", name))
                # Parse all indices
                for idx_match in re.finditer(r"\[(\d+)\]", indices_str):
                    parts.append(("index", idx_match.group(1)))
            else:
                parts.append(("key", token))

        return parts

    # ==================== Utility ====================

    def count_elements(self, json_data: Union[str, Any]) -> Dict[str, int]:
        """Count elements by type in JSON data.

        Args:
            json_data: JSON string or parsed data.

        Returns:
            Dict with counts per type.
        """
        self._ensure_active()
        self._increment_call_count()

        if isinstance(json_data, str):
            data = json.loads(json_data)
        else:
            data = json_data

        counts = {
            "object": 0,
            "array": 0,
            "string": 0,
            "number": 0,
            "boolean": 0,
            "null": 0,
            "total": 0,
            "max_depth": 0,
        }

        self._count_recursive(data, counts, 1)
        return counts

    def _count_recursive(self, data: Any, counts: Dict[str, int], depth: int) -> None:
        """Count elements recursively."""
        counts["total"] += 1
        counts["max_depth"] = max(counts["max_depth"], depth)

        if isinstance(data, dict):
            counts["object"] += 1
            for value in data.values():
                self._count_recursive(value, counts, depth + 1)
        elif isinstance(data, list):
            counts["array"] += 1
            for item in data:
                self._count_recursive(item, counts, depth + 1)
        elif isinstance(data, str):
            counts["string"] += 1
        elif isinstance(data, bool):
            counts["boolean"] += 1
        elif isinstance(data, (int, float)):
            counts["number"] += 1
        elif data is None:
            counts["null"] += 1

    def find_value(
        self,
        json_data: Union[str, Any],
        search_value: Any,
        *,
        case_sensitive: bool = True,
    ) -> List[str]:
        """Find all paths containing a specific value.

        Args:
            json_data: JSON data to search.
            search_value: Value to find.
            case_sensitive: Whether string matching is case-sensitive.

        Returns:
            List of JSONPath-style paths.
        """
        self._ensure_active()
        self._increment_call_count()

        if isinstance(json_data, str):
            data = json.loads(json_data)
        else:
            data = json_data

        paths: List[str] = []
        self._find_recursive(data, search_value, "$", paths, case_sensitive)
        return paths

    def _find_recursive(
        self,
        data: Any,
        search: Any,
        path: str,
        paths: List[str],
        case_sensitive: bool,
    ) -> None:
        """Recursively search for a value."""
        match = False

        if isinstance(search, str) and isinstance(data, str):
            if case_sensitive:
                match = search == data
            else:
                match = search.lower() == data.lower()
        elif isinstance(search, (int, float)) and isinstance(data, (int, float)):
            match = search == data
        else:
            match = search == data

        if match:
            paths.append(path)

        if isinstance(data, dict):
            for key, value in data.items():
                self._find_recursive(value, search, f"{path}.{key}", paths, case_sensitive)
        elif isinstance(data, list):
            for i, item in enumerate(data):
                self._find_recursive(item, search, f"{path}[{i}]", paths, case_sensitive)

    # ==================== Statistics ====================

    def get_stats(self) -> Dict[str, Any]:
        """Get plugin statistics."""
        return {
            "default_indent": self._default_indent,
            "max_diff_depth": self._max_diff_depth,
            "supported_jsonpath_features": [
                "dot notation", "bracket notation", "wildcard",
                "recursive descent", "array slicing", "filter expressions",
            ],
            "schema_supported": [
                "type", "properties", "required", "items", "enum",
                "minimum", "maximum", "minLength", "maxLength",
                "pattern", "minItems", "maxItems",
            ],
        }
