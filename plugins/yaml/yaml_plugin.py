"""
YAML Processing Plugin for LiuHao AI OS

Provides YAML parsing, generation, validation, conversion to/from JSON,
YAML merge, and basic schema validation.

Capabilities:
- parse: Parse YAML string to dict
- dump: Generate YAML string from data
- validate: Validate YAML syntax
- to_json: Convert YAML to JSON string
- from_json: Convert JSON string to YAML
- merge: Merge multiple YAML documents
- schema_validate: Validate data against a simple schema
- get_value: Get value using dot-notation path
- set_value: Set value using dot-notation path
"""

import re
import json
from typing import Dict, Any, Optional, List, Tuple, Union
from dataclasses import dataclass, field

from ..plugin_base import (
    BasePlugin,
    PluginMetadata,
    PluginStatus,
    PluginExecutionError,
)


@dataclass
class YamlResult:
    """Result of a YAML operation."""
    success: bool
    data: Any = None
    error: Optional[str] = None
    error_line: Optional[int] = None
    error_column: Optional[int] = None


@dataclass
class ValidationResult:
    """Result of YAML validation."""
    is_valid: bool
    errors: List[str]
    warnings: List[str]
    document_count: int


@dataclass
class MergeResult:
    """Result of YAML merge operation."""
    merged: Dict[str, Any]
    conflicts: List[str]
    sources_count: int


class YamlPlugin(BasePlugin):
    """YAML processing plugin.

    Provides YAML parsing, generation, validation, format conversion,
    and schema validation using a pure-Python implementation.

    Example:
        plugin = YamlPlugin()
        plugin.initialize()
        plugin.activate()

        result = plugin.parse("key: value\nnumber: 42")
        print(result.data)
    """

    def __init__(self) -> None:
        """Initialize the YAML plugin."""
        metadata = PluginMetadata(
            name="yaml",
            version="1.0.0",
            description="YAML parsing, generation, validation, and conversion",
            author="LiuHao AI OS Team",
            categories=["data", "conversion", "utility", "serialization"],
            keywords=["yaml", "yml", "parse", "serialize", "json", "config"],
            requires_internet=False,
            dependencies=[],
        )
        super().__init__(metadata)

        self._indent_size: int = 2
        self._default_flow_style: bool = False

    # ==================== Lifecycle Methods ====================

    def initialize(self, config: Optional[Dict[str, Any]] = None) -> bool:
        """Initialize the YAML plugin."""
        try:
            if config:
                self._indent_size = config.get("indent_size", 2)
                self._default_flow_style = config.get("default_flow_style", False)

            self._set_status(PluginStatus.INITIALIZED)
            return True
        except Exception as e:
            self._set_error(f"Initialization failed: {e}")
            return False

    def activate(self) -> bool:
        """Activate the YAML plugin."""
        try:
            self._set_status(PluginStatus.ACTIVE)
            return True
        except Exception as e:
            self._set_error(f"Activation failed: {e}")
            return False

    def deactivate(self) -> bool:
        """Deactivate the YAML plugin."""
        try:
            self._set_status(PluginStatus.INACTIVE)
            return True
        except Exception as e:
            self._set_error(str(e))
            return False

    def shutdown(self) -> bool:
        """Shut down the YAML plugin."""
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
            "dump",
            "validate",
            "to_json",
            "from_json",
            "merge",
            "schema_validate",
            "get_value",
            "set_value",
            "flatten",
            "unflatten",
        ]

    # ==================== Parsing ====================

    def parse(self, yaml_text: str) -> YamlResult:
        """Parse a YAML string into Python data structures.

        Supports a subset of YAML features: mappings, sequences,
        scalars (strings, numbers, booleans, null), comments,
        multi-line strings, and nested structures.

        Args:
            yaml_text: The YAML text to parse.

        Returns:
            YamlResult with parsed data or error info.
        """
        self._ensure_active()
        self._increment_call_count()

        try:
            lines = yaml_text.split("\n")
            data, _ = self._parse_lines(lines, 0, 0)
            return YamlResult(success=True, data=data)
        except Exception as e:
            return YamlResult(
                success=False,
                error=str(e),
            )

    def _parse_lines(
        self, lines: List[str], start_idx: int, base_indent: int
    ) -> Tuple[Any, int]:
        """Parse YAML lines starting from start_idx.

        Returns (parsed_value, next_line_index).
        """
        # Determine if this is a list or dict based on first non-empty line
        i = start_idx
        while i < len(lines):
            stripped = lines[i].strip()
            if not stripped or stripped.startswith("#"):
                i += 1
                continue

            indent = len(lines[i]) - len(lines[i].lstrip())
            if indent < base_indent:
                return None, i

            if stripped.startswith("- "):
                return self._parse_list(lines, i, base_indent)
            elif ": " in stripped or stripped.endswith(":"):
                return self._parse_dict(lines, i, base_indent)
            else:
                # Scalar value
                value = self._parse_scalar(stripped)
                return value, i + 1

        return None, i

    def _parse_dict(
        self, lines: List[str], start_idx: int, base_indent: int
    ) -> Tuple[Dict[str, Any], int]:
        """Parse a YAML mapping."""
        result: Dict[str, Any] = {}
        i = start_idx

        while i < len(lines):
            line = lines[i]
            stripped = line.strip()

            if not stripped or stripped.startswith("#"):
                i += 1
                continue

            indent = len(line) - len(line.lstrip())
            if indent < base_indent:
                break

            if indent > base_indent:
                # This shouldn't happen for a dict at this level
                i += 1
                continue

            # List item at dict level means we're done
            if stripped.startswith("- "):
                break

            # Check for key: value or key:
            if ":" not in stripped:
                i += 1
                continue

            key, _, value_str = stripped.partition(":")
            key = key.strip()
            value_str = value_str.strip()

            # Handle quoted keys
            if (key.startswith('"') and key.endswith('"')) or \
               (key.startswith("'") and key.endswith("'")):
                key = key[1:-1]

            if value_str == "":
                # Value might be on next line(s) (nested structure or multiline)
                i += 1
                # Skip empty lines
                while i < len(lines) and (lines[i].strip() == "" or lines[i].strip().startswith("#")):
                    i += 1

                if i < len(lines):
                    next_indent = len(lines[i]) - len(lines[i].lstrip())
                    if next_indent > base_indent:
                        # Check for multi-line string indicators
                        next_stripped = lines[i].strip()
                        if next_stripped in ("|", ">"):
                            # Multi-line string
                            multiline_text, i = self._parse_multiline(lines, i + 1, next_indent)
                            result[key] = multiline_text
                        else:
                            nested_value, i = self._parse_lines(lines, i, next_indent)
                            result[key] = nested_value
                    else:
                        result[key] = None
                else:
                    result[key] = None
            elif value_str == "{}":
                result[key] = {}
                i += 1
            elif value_str == "[]":
                result[key] = []
                i += 1
            else:
                result[key] = self._parse_scalar(value_str)
                i += 1

        return result, i

    def _parse_list(
        self, lines: List[str], start_idx: int, base_indent: int
    ) -> Tuple[List[Any], int]:
        """Parse a YAML sequence."""
        result: List[Any] = []
        i = start_idx

        while i < len(lines):
            line = lines[i]
            stripped = line.strip()

            if not stripped or stripped.startswith("#"):
                i += 1
                continue

            indent = len(line) - len(line.lstrip())
            if indent < base_indent:
                break

            if not stripped.startswith("- "):
                break

            # Get the value after "- "
            value_str = stripped[2:].strip()

            if value_str == "":
                # Nested structure
                i += 1
                while i < len(lines) and (lines[i].strip() == "" or lines[i].strip().startswith("#")):
                    i += 1
                if i < len(lines):
                    next_indent = len(lines[i]) - len(lines[i].lstrip())
                    if next_indent > base_indent + 2:
                        nested_value, i = self._parse_lines(lines, i, next_indent)
                        result.append(nested_value)
                    else:
                        result.append(None)
                else:
                    result.append(None)
            elif ": " in value_str or value_str.endswith(":"):
                # Inline dict starting at list item
                # Parse the rest of the dict
                # Reconstruct with proper indentation
                nested_lines = [line.replace("- ", "  ", 1)]
                i += 1
                while i < len(lines):
                    next_line = lines[i]
                    next_stripped = next_line.strip()
                    if not next_stripped or next_stripped.startswith("#"):
                        nested_lines.append(next_line)
                        i += 1
                        continue
                    next_indent = len(next_line) - len(next_line.lstrip())
                    if next_indent <= base_indent and next_stripped.startswith("- ") is False:
                        break
                    if next_indent <= base_indent and next_stripped.startswith("- "):
                        break
                    nested_lines.append(next_line)
                    i += 1
                nested_dict, _ = self._parse_dict(nested_lines, 0, base_indent + 2)
                result.append(nested_dict)
            else:
                result.append(self._parse_scalar(value_str))
                i += 1

        return result, i

    def _parse_scalar(self, value: str) -> Any:
        """Parse a YAML scalar value."""
        value = value.strip()

        # Remove trailing comments (but not inside quotes)
        if not (value.startswith('"') or value.startswith("'")):
            # Simple comment detection
            comment_idx = value.find(" #")
            if comment_idx > 0:
                value = value[:comment_idx].strip()

        # Null
        if value.lower() in ("null", "~", ""):
            return None

        # Boolean
        if value.lower() in ("true", "yes", "on"):
            return True
        if value.lower() in ("false", "no", "off"):
            return False

        # Integer
        try:
            if value.startswith("0x") or value.startswith("0X"):
                return int(value, 16)
            if value.startswith("0o") or value.startswith("0O"):
                return int(value, 8)
            if re.match(r"^-?\d+$", value):
                return int(value)
        except ValueError:
            pass

        # Float
        try:
            if re.match(r"^-?\d+\.\d+$", value) or value.lower() in (".inf", "-.inf", ".nan"):
                return float(value)
            if re.match(r"^-?\d+e\d+$", value, re.IGNORECASE):
                return float(value)
        except ValueError:
            pass

        # Quoted string
        if value.startswith('"') and value.endswith('"'):
            return value[1:-1].encode().decode("unicode_escape")
        if value.startswith("'") and value.endswith("'"):
            return value[1:-1].replace("''", "'")

        return value

    def _parse_multiline(
        self, lines: List[str], start_idx: int, base_indent: int
    ) -> Tuple[str, int]:
        """Parse a multi-line string (| or >)."""
        text_lines: List[str] = []
        i = start_idx

        while i < len(lines):
            line = lines[i]
            stripped = line.strip()
            indent = len(line) - len(line.lstrip())

            if stripped == "":
                text_lines.append("")
                i += 1
                continue

            if indent <= base_indent:
                break

            text_lines.append(line.strip())
            i += 1

        # Remove trailing empty lines
        while text_lines and text_lines[-1] == "":
            text_lines.pop()

        return "\n".join(text_lines) + "\n", i

    # ==================== Generation ====================

    def dump(
        self,
        data: Any,
        *,
        indent_size: Optional[int] = None,
        sort_keys: bool = False,
    ) -> str:
        """Serialize Python data to a YAML string.

        Args:
            data: The data to serialize.
            indent_size: Indentation size (uses default if None).
            sort_keys: Whether to sort dictionary keys.

        Returns:
            YAML string.
        """
        self._ensure_active()
        self._increment_call_count()

        indent = indent_size if indent_size is not None else self._indent_size
        return self._dump_value(data, indent, 0, sort_keys).rstrip() + "\n"

    def _dump_value(
        self, value: Any, indent: int, level: int, sort_keys: bool
    ) -> str:
        """Serialize a single value."""
        if value is None:
            return "null"

        if isinstance(value, bool):
            return "true" if value else "false"

        if isinstance(value, int):
            return str(value)

        if isinstance(value, float):
            return str(value)

        if isinstance(value, str):
            return self._dump_string(value)

        if isinstance(value, (list, tuple)):
            return self._dump_list(value, indent, level, sort_keys)

        if isinstance(value, dict):
            return self._dump_dict(value, indent, level, sort_keys)

        # Fallback
        return str(value)

    def _dump_string(self, value: str) -> str:
        """Serialize a string value."""
        if "\n" in value:
            # Multi-line string
            lines = value.split("\n")
            result = "|"
            for line in lines:
                if line:
                    result += "\n  " + line
                else:
                    result += "\n"
            return result

        # Check if string needs quoting
        needs_quote = False
        if value == "":
            return '""'

        # Check for special values that would be parsed as non-string
        lower = value.lower()
        if lower in ("true", "false", "yes", "no", "on", "off", "null", "~"):
            needs_quote = True

        if re.match(r"^-?\d+$", value):
            needs_quote = True

        if re.match(r"^-?\d+\.\d+$", value):
            needs_quote = True

        if value.startswith(("#", "-", "?", ":", "{", "}", "[", "]", ",", "&", "*", "!", "|", ">", "%", "@", "`")):
            needs_quote = True

        if ": " in value or " # " in value:
            needs_quote = True

        if needs_quote:
            if '"' in value and "'" not in value:
                return f"'{value}'"
            else:
                escaped = value.replace("\\", "\\\\").replace('"', '\\"')
                return f'"{escaped}"'

        return value

    def _dump_list(
        self, lst: list, indent: int, level: int, sort_keys: bool
    ) -> str:
        """Serialize a list."""
        if not lst:
            return "[]"

        # Check if all items are simple scalars for inline option
        all_scalars = all(
            isinstance(item, (str, int, float, bool, type(None)))
            for item in lst
        )
        if all_scalars and len(lst) <= 5:
            items = [self._dump_value(item, indent, level + 1, sort_keys) for item in lst]
            inline = "[" + ", ".join(items) + "]"
            if len(inline) < 80:
                return inline

        lines: List[str] = []
        prefix = " " * (level * indent)
        for item in lst:
            if isinstance(item, dict):
                dict_lines = self._dump_dict(item, indent, level + 1, sort_keys).split("\n")
                # Replace first line's prefix with "- "
                first_line = dict_lines[0].lstrip()
                lines.append(f"{prefix}- {first_line}")
                lines.extend(dict_lines[1:])
            elif isinstance(item, list):
                list_lines = self._dump_list(item, indent, level + 1, sort_keys).split("\n")
                first_line = list_lines[0].lstrip()
                lines.append(f"{prefix}- {first_line}")
                lines.extend(list_lines[1:])
            else:
                val_str = self._dump_value(item, indent, level + 1, sort_keys)
                lines.append(f"{prefix}- {val_str}")

        return "\n".join(lines)

    def _dump_dict(
        self, dct: dict, indent: int, level: int, sort_keys: bool
    ) -> str:
        """Serialize a dictionary."""
        if not dct:
            return "{}"

        lines: List[str] = []
        prefix = " " * (level * indent)

        keys = sorted(dct.keys()) if sort_keys else list(dct.keys())

        for key in keys:
            value = dct[key]
            key_str = str(key)

            # Quote key if needed
            if not re.match(r"^[a-zA-Z_][a-zA-Z0-9_\-]*$", key_str):
                key_str = f'"{key_str}"'

            if isinstance(value, dict) and value:
                lines.append(f"{prefix}{key_str}:")
                dict_str = self._dump_dict(value, indent, level + 1, sort_keys)
                lines.append(dict_str)
            elif isinstance(value, list) and value:
                lines.append(f"{prefix}{key_str}:")
                list_str = self._dump_list(value, indent, level + 1, sort_keys)
                lines.append(list_str)
            elif isinstance(value, str) and "\n" in value:
                lines.append(f"{prefix}{key_str}: |")
                for val_line in value.split("\n"):
                    if val_line:
                        lines.append(f"{prefix}  {val_line}")
                    else:
                        lines.append("")
            else:
                val_str = self._dump_value(value, indent, level + 1, sort_keys)
                lines.append(f"{prefix}{key_str}: {val_str}")

        return "\n".join(lines)

    # ==================== Validation ====================

    def validate(self, yaml_text: str) -> ValidationResult:
        """Validate YAML syntax.

        Args:
            yaml_text: The YAML text to validate.

        Returns:
            ValidationResult with validity and errors.
        """
        self._ensure_active()
        self._increment_call_count()

        errors: List[str] = []
        warnings: List[str] = []
        lines = yaml_text.split("\n")

        # Basic indentation consistency check
        indent_sizes = set()
        in_code_block = False

        for line_num, line in enumerate(lines, 1):
            stripped = line.strip()

            if not stripped or stripped.startswith("#"):
                continue

            # Tab check
            if "\t" in line[:len(line) - len(line.lstrip())]:
                errors.append(f"Line {line_num}: uses tabs for indentation (use spaces)")

            indent = len(line) - len(line.lstrip())
            if indent > 0:
                indent_sizes.add(indent)

        # Check indent consistency
        if len(indent_sizes) > 1:
            indent_list = sorted(indent_sizes)
            base_indent = min(indent_list)
            for indent in indent_list:
                if indent % base_indent != 0:
                    warnings.append(f"Inconsistent indentation: {indent} spaces is not a multiple of {base_indent}")

        # Try parsing
        try:
            result = self.parse(yaml_text)
            if not result.success:
                errors.append(f"Parse error: {result.error}")
        except Exception as e:
            errors.append(f"Validation error: {e}")

        # Count documents
        doc_count = 1
        for line in lines:
            if line.strip() == "---":
                doc_count += 1

        return ValidationResult(
            is_valid=len(errors) == 0,
            errors=errors,
            warnings=warnings,
            document_count=doc_count,
        )

    # ==================== Conversion ====================

    def to_json(self, yaml_text: str, *, pretty: bool = True) -> YamlResult:
        """Convert YAML string to JSON string.

        Args:
            yaml_text: The YAML text to convert.
            pretty: Whether to pretty-print the JSON output.

        Returns:
            YamlResult with JSON string in data.
        """
        self._ensure_active()
        self._increment_call_count()

        parse_result = self.parse(yaml_text)
        if not parse_result.success:
            return parse_result

        try:
            if pretty:
                json_str = json.dumps(parse_result.data, indent=2, ensure_ascii=False, default=str)
            else:
                json_str = json.dumps(parse_result.data, ensure_ascii=False, default=str)
            return YamlResult(success=True, data=json_str)
        except Exception as e:
            return YamlResult(success=False, error=str(e))

    def from_json(self, json_text: str) -> YamlResult:
        """Convert JSON string to YAML string.

        Args:
            json_text: The JSON text to convert.

        Returns:
            YamlResult with YAML string in data.
        """
        self._ensure_active()
        self._increment_call_count()

        try:
            data = json.loads(json_text)
            yaml_str = self.dump(data)
            return YamlResult(success=True, data=yaml_str)
        except json.JSONDecodeError as e:
            return YamlResult(success=False, error=f"JSON parse error: {e}")
        except Exception as e:
            return YamlResult(success=False, error=str(e))

    # ==================== Merge ====================

    def merge(
        self,
        sources: List[Union[str, Dict[str, Any]]],
        *,
        overwrite: bool = True,
        merge_lists: bool = False,
    ) -> MergeResult:
        """Merge multiple YAML sources.

        Args:
            sources: List of YAML strings or dicts to merge.
            overwrite: Whether later sources overwrite earlier ones.
            merge_lists: Whether to merge lists or replace them.

        Returns:
            MergeResult with merged data and conflicts.
        """
        self._ensure_active()
        self._increment_call_count()

        merged: Dict[str, Any] = {}
        conflicts: List[str] = []

        for idx, source in enumerate(sources):
            # Parse if string
            if isinstance(source, str):
                result = self.parse(source)
                if not result.success:
                    conflicts.append(f"Source {idx}: {result.error}")
                    continue
                source_data = result.data
            else:
                source_data = source

            if not isinstance(source_data, dict):
                conflicts.append(f"Source {idx}: not a dictionary/mapping")
                continue

            self._deep_merge(merged, source_data, overwrite, merge_lists, conflicts, f"source_{idx}")

        return MergeResult(
            merged=merged,
            conflicts=conflicts,
            sources_count=len(sources),
        )

    def _deep_merge(
        self,
        target: Dict[str, Any],
        source: Dict[str, Any],
        overwrite: bool,
        merge_lists: bool,
        conflicts: List[str],
        source_name: str,
        prefix: str = "",
    ) -> None:
        """Deep merge source into target."""
        for key, value in source.items():
            path = f"{prefix}.{key}" if prefix else key

            if key not in target:
                target[key] = value
            elif isinstance(target[key], dict) and isinstance(value, dict):
                self._deep_merge(target[key], value, overwrite, merge_lists, conflicts, source_name, path)
            elif isinstance(target[key], list) and isinstance(value, list) and merge_lists:
                target[key] = target[key] + value
            elif overwrite:
                if target[key] != value:
                    conflicts.append(f"{path}: overwritten by {source_name}")
                target[key] = value
            else:
                if target[key] != value:
                    conflicts.append(f"{path}: conflict, keeping original")

    # ==================== Schema Validation ====================

    def schema_validate(
        self,
        data: Dict[str, Any],
        schema: Dict[str, Any],
    ) -> Tuple[bool, List[str]]:
        """Validate data against a simple schema.

        Schema format:
        {
            "type": "object",
            "properties": {
                "name": {"type": "string", "required": True},
                "age": {"type": "integer", "required": False, "min": 0},
                "tags": {"type": "array", "items": {"type": "string"}},
            }
        }

        Args:
            data: The data to validate.
            schema: The schema definition.

        Returns:
            Tuple of (is_valid, list_of_errors).
        """
        self._ensure_active()
        self._increment_call_count()

        errors: List[str] = []
        self._validate_value(data, schema, "$", errors)
        return len(errors) == 0, errors

    def _validate_value(
        self,
        value: Any,
        schema: Dict[str, Any],
        path: str,
        errors: List[str],
    ) -> None:
        """Validate a value against schema."""
        expected_type = schema.get("type", "any")

        # Type checking
        if expected_type == "object" or expected_type == "dict":
            if not isinstance(value, dict):
                errors.append(f"{path}: expected object, got {type(value).__name__}")
                return
            self._validate_object(value, schema, path, errors)
        elif expected_type == "array" or expected_type == "list":
            if not isinstance(value, list):
                errors.append(f"{path}: expected array, got {type(value).__name__}")
                return
            self._validate_array(value, schema, path, errors)
        elif expected_type == "string":
            if not isinstance(value, str):
                errors.append(f"{path}: expected string, got {type(value).__name__}")
                return
            self._validate_string(value, schema, path, errors)
        elif expected_type == "integer":
            if not isinstance(value, int) or isinstance(value, bool):
                errors.append(f"{path}: expected integer, got {type(value).__name__}")
                return
            self._validate_number(value, schema, path, errors)
        elif expected_type == "number":
            if not isinstance(value, (int, float)) or isinstance(value, bool):
                errors.append(f"{path}: expected number, got {type(value).__name__}")
                return
            self._validate_number(value, schema, path, errors)
        elif expected_type == "boolean":
            if not isinstance(value, bool):
                errors.append(f"{path}: expected boolean, got {type(value).__name__}")
                return
        elif expected_type == "null":
            if value is not None:
                errors.append(f"{path}: expected null, got {type(value).__name__}")

        # Enum check
        if "enum" in schema:
            if value not in schema["enum"]:
                errors.append(f"{path}: value must be one of {schema['enum']}")

    def _validate_object(
        self,
        obj: Dict[str, Any],
        schema: Dict[str, Any],
        path: str,
        errors: List[str],
    ) -> None:
        """Validate an object/mapping."""
        properties = schema.get("properties", {})
        required = schema.get("required", [])

        # Check required fields
        for prop in required:
            if prop not in obj:
                errors.append(f"{path}.{prop}: required field missing")

        # Validate each property
        for prop, prop_schema in properties.items():
            if prop in obj:
                self._validate_value(obj[prop], prop_schema, f"{path}.{prop}", errors)

        # Check for additional properties
        if schema.get("additionalProperties") is False:
            for key in obj:
                if key not in properties:
                    errors.append(f"{path}.{key}: unexpected property")

    def _validate_array(
        self,
        arr: List[Any],
        schema: Dict[str, Any],
        path: str,
        errors: List[str],
    ) -> None:
        """Validate an array/list."""
        min_items = schema.get("minItems")
        max_items = schema.get("maxItems")

        if min_items is not None and len(arr) < min_items:
            errors.append(f"{path}: must have at least {min_items} items")
        if max_items is not None and len(arr) > max_items:
            errors.append(f"{path}: must have at most {max_items} items")

        items_schema = schema.get("items")
        if items_schema:
            for i, item in enumerate(arr):
                self._validate_value(item, items_schema, f"{path}[{i}]", errors)

    def _validate_string(
        self,
        value: str,
        schema: Dict[str, Any],
        path: str,
        errors: List[str],
    ) -> None:
        """Validate a string."""
        min_length = schema.get("minLength")
        max_length = schema.get("maxLength")
        pattern = schema.get("pattern")

        if min_length is not None and len(value) < min_length:
            errors.append(f"{path}: must be at least {min_length} characters")
        if max_length is not None and len(value) > max_length:
            errors.append(f"{path}: must be at most {max_length} characters")
        if pattern and not re.match(pattern, value):
            errors.append(f"{path}: does not match pattern '{pattern}'")

    def _validate_number(
        self,
        value: Union[int, float],
        schema: Dict[str, Any],
        path: str,
        errors: List[str],
    ) -> None:
        """Validate a number."""
        minimum = schema.get("minimum")
        maximum = schema.get("maximum")

        if minimum is not None and value < minimum:
            errors.append(f"{path}: must be >= {minimum}")
        if maximum is not None and value > maximum:
            errors.append(f"{path}: must be <= {maximum}")

    # ==================== Path Operations ====================

    def get_value(self, data: Dict[str, Any], path: str, default: Any = None) -> Any:
        """Get a value using dot-notation path.

        Args:
            data: The data structure to search.
            path: Dot-separated path like "database.host".
            default: Default value if path not found.

        Returns:
            The value at the path, or default.
        """
        self._ensure_active()
        self._increment_call_count()

        parts = path.split(".")
        current: Any = data

        for part in parts:
            if isinstance(current, dict):
                if part in current:
                    current = current[part]
                else:
                    return default
            elif isinstance(current, list):
                try:
                    idx = int(part)
                    current = current[idx]
                except (ValueError, IndexError):
                    return default
            else:
                return default

        return current

    def set_value(self, data: Dict[str, Any], path: str, value: Any) -> Dict[str, Any]:
        """Set a value using dot-notation path. Creates intermediate dicts.

        Args:
            data: The data structure to modify.
            path: Dot-separated path like "database.host".
            value: The value to set.

        Returns:
            The modified data structure.
        """
        self._ensure_active()
        self._increment_call_count()

        parts = path.split(".")
        current = data

        for i, part in enumerate(parts[:-1]):
            if part not in current or not isinstance(current[part], dict):
                current[part] = {}
            current = current[part]

        current[parts[-1]] = value
        return data

    # ==================== Flatten / Unflatten ====================

    def flatten(self, data: Dict[str, Any], separator: str = ".") -> Dict[str, Any]:
        """Flatten a nested dict to a single level with dot-notation keys.

        Args:
            data: The nested dictionary.
            separator: Key separator character.

        Returns:
            Flattened dictionary.
        """
        self._ensure_active()
        self._increment_call_count()

        result: Dict[str, Any] = {}
        self._flatten_dict(data, "", separator, result)
        return result

    def _flatten_dict(
        self, dct: Dict[str, Any], prefix: str, separator: str, result: Dict[str, Any]
    ) -> None:
        """Recursively flatten a dict."""
        for key, value in dct.items():
            new_key = f"{prefix}{separator}{key}" if prefix else key
            if isinstance(value, dict) and value:
                self._flatten_dict(value, new_key, separator, result)
            else:
                result[new_key] = value

    def unflatten(self, data: Dict[str, Any], separator: str = ".") -> Dict[str, Any]:
        """Unflatten a flat dict with dot-notation keys to nested structure.

        Args:
            data: The flat dictionary.
            separator: Key separator character.

        Returns:
            Nested dictionary.
        """
        self._ensure_active()
        self._increment_call_count()

        result: Dict[str, Any] = {}

        for key, value in data.items():
            parts = key.split(separator)
            current = result
            for part in parts[:-1]:
                if part not in current or not isinstance(current[part], dict):
                    current[part] = {}
                current = current[part]
            current[parts[-1]] = value

        return result

    # ==================== Statistics ====================

    def get_stats(self) -> Dict[str, Any]:
        """Get plugin statistics."""
        return {
            "indent_size": self._indent_size,
            "supported_types": ["string", "integer", "float", "boolean", "null", "list", "dict"],
            "schema_types": ["object", "array", "string", "integer", "number", "boolean", "null"],
        }
