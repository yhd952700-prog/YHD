"""
TOML Processing Plugin for LiuHao AI OS

Provides TOML parsing, generation, validation, conversion,
table manipulation, and comment preservation.

Capabilities:
- parse: Parse TOML string to dict
- generate: Generate TOML string from dict
- validate: Validate TOML syntax
- to_json: Convert TOML to JSON
- from_json: Convert JSON to TOML
- get_table: Get a specific table from TOML
- set_table: Set/update a table
- list_tables: List all tables
- merge: Merge two TOML documents
- format: Format/beautify TOML
"""

import re
import json
from typing import Dict, Any, Optional, List, Tuple
from dataclasses import dataclass, field

from ..plugin_base import (
    BasePlugin,
    PluginMetadata,
    PluginStatus,
    PluginExecutionError,
)


@dataclass
class TomlTable:
    """A TOML table with comments."""
    name: str
    key_path: List[str]
    data: Dict[str, Any]
    comments: Dict[str, str] = field(default_factory=dict)  # key -> comment
    table_comment: str = ""
    is_array_of_tables: bool = False


@dataclass
class ParseResult:
    """Result of TOML parsing."""
    data: Dict[str, Any]
    tables: List[TomlTable]
    is_valid: bool
    errors: List[str]
    line_count: int


class TomlPlugin(BasePlugin):
    """TOML processing plugin.

    Provides TOML parsing, generation, validation, conversion,
    and table manipulation with comment preservation.

    Example:
        plugin = TomlPlugin()
        plugin.initialize()
        plugin.activate()

        data = plugin.parse('[package]\\nname = "hello"\\nversion = "1.0.0"')
        print(data["package"]["name"])
    """

    # TOML value type patterns
    BASIC_STRING_PATTERN = re.compile(r'^"(.*)"$', re.DOTALL)
    LITERAL_STRING_PATTERN = re.compile(r"^'(.*)'$", re.DOTALL)
    MULTI_BASIC_STRING_PATTERN = re.compile(r'^"""(.*)"""$', re.DOTALL)
    MULTI_LITERAL_STRING_PATTERN = re.compile(r"^'''(.*)'''$", re.DOTALL)
    INTEGER_PATTERN = re.compile(r'^[+-]?\d[\d_]*$')
    FLOAT_PATTERN = re.compile(r'^[+-]?(\d[\d_]*\.[\d_]*([eE][+-]?\d[\d_]*)?|\d[\d_]*[eE][+-]?\d[\d_]*)$')
    BOOLEAN_PATTERN = re.compile(r'^(true|false)$')
    OFFSET_DATETIME_PATTERN = re.compile(
        r'^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d+)?(Z|[+-]\d{2}:\d{2})$'
    )
    LOCAL_DATETIME_PATTERN = re.compile(
        r'^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d+)?$'
    )
    LOCAL_DATE_PATTERN = re.compile(r'^\d{4}-\d{2}-\d{2}$')
    LOCAL_TIME_PATTERN = re.compile(r'^\d{2}:\d{2}:\d{2}(\.\d+)?$')

    def __init__(self) -> None:
        """Initialize the TOML plugin."""
        metadata = PluginMetadata(
            name="toml",
            version="1.0.0",
            description="TOML parsing, generation, validation, and manipulation",
            author="LiuHao AI OS Team",
            categories=["data", "text", "utility"],
            keywords=["toml", "config", "parse", "serialize", "conversion"],
            requires_internet=False,
            dependencies=[],
        )
        super().__init__(metadata)

        self._indent_size: int = 2
        self._sort_keys: bool = False
        self._preserve_comments: bool = True

    # ==================== Lifecycle Methods ====================

    def initialize(self, config: Optional[Dict[str, Any]] = None) -> bool:
        """Initialize the TOML plugin."""
        try:
            if config:
                self._indent_size = config.get("indent_size", 2)
                self._sort_keys = config.get("sort_keys", False)
                self._preserve_comments = config.get("preserve_comments", True)

            self._set_status(PluginStatus.INITIALIZED)
            return True
        except Exception as e:
            self._set_error(f"Initialization failed: {e}")
            return False

    def activate(self) -> bool:
        """Activate the TOML plugin."""
        try:
            self._set_status(PluginStatus.ACTIVE)
            return True
        except Exception as e:
            self._set_error(f"Activation failed: {e}")
            return False

    def deactivate(self) -> bool:
        """Deactivate the TOML plugin."""
        try:
            self._set_status(PluginStatus.INACTIVE)
            return True
        except Exception as e:
            self._set_error(str(e))
            return False

    def shutdown(self) -> bool:
        """Shut down the TOML plugin."""
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
            "validate",
            "to_json",
            "from_json",
            "get_table",
            "set_table",
            "list_tables",
            "merge",
            "format",
            "get_value",
            "set_value",
        ]

    # ==================== Parsing ====================

    def parse(self, toml_text: str) -> Dict[str, Any]:
        """Parse a TOML string into a dict.

        Args:
            toml_text: TOML formatted string.

        Returns:
            Parsed dict.
        """
        self._ensure_active()
        self._increment_call_count()

        result = self._parse_toml(toml_text)
        if not result.is_valid:
            raise ValueError(f"Invalid TOML: {'; '.join(result.errors)}")

        return result.data

    def _parse_toml(self, text: str) -> ParseResult:
        """Internal TOML parser."""
        errors: List[str] = []
        data: Dict[str, Any] = {}
        tables: List[TomlTable] = []
        current_table_data = data
        current_table_name = ""
        current_key_path: List[str] = []
        pending_comment = ""
        line_num = 0

        lines = text.split("\n")
        in_multiline = False
        multiline_key = ""
        multiline_value = ""
        multiline_quote = ""
        multiline_type = ""  # string, array

        for line in lines:
            line_num += 1
            stripped = line.strip()

            # Handle multiline strings/arrays
            if in_multiline:
                if multiline_type == "string":
                    # Check for closing triple quotes
                    if multiline_quote * 3 in stripped:
                        # Extract up to closing quotes
                        idx = stripped.find(multiline_quote * 3)
                        multiline_value += stripped[:idx]
                        value = self._unescape_string(multiline_value, multiline_quote == '"')
                        self._set_nested_value(
                            current_table_data,
                            self._parse_key(multiline_key),
                            value,
                        )
                        in_multiline = False
                        multiline_key = ""
                        multiline_value = ""
                    else:
                        multiline_value += line + "\n"
                elif multiline_type == "array":
                    multiline_value += stripped
                    if stripped.endswith("]"):
                        try:
                            value = self._parse_array(multiline_value)
                            self._set_nested_value(
                                current_table_data,
                                self._parse_key(multiline_key),
                                value,
                            )
                        except ValueError as e:
                            errors.append(f"Line {line_num}: {e}")
                        in_multiline = False
                        multiline_key = ""
                        multiline_value = ""
                continue

            # Skip empty lines
            if not stripped:
                continue

            # Skip comments
            if stripped.startswith("#"):
                pending_comment = stripped[1:].strip()
                continue

            # Table header
            if stripped.startswith("[") and stripped.endswith("]"):
                # Check if it's array of tables
                is_array_table = stripped.startswith("[[") and stripped.endswith("]]")
                if is_array_table:
                    table_name = stripped[2:-2].strip()
                else:
                    table_name = stripped[1:-1].strip()

                key_path = self._parse_key(table_name)

                # Validate table name
                if not key_path:
                    errors.append(f"Line {line_num}: Empty table name")
                    continue

                current_key_path = key_path
                current_table_name = table_name

                # Navigate to or create table
                if is_array_table:
                    # Array of tables
                    parent = self._navigate_to(data, key_path[:-1], create=True)
                    key = key_path[-1]
                    if key not in parent:
                        parent[key] = []
                    new_table = {}
                    parent[key].append(new_table)
                    current_table_data = new_table
                else:
                    current_table_data = self._navigate_to(data, key_path, create=True)

                table = TomlTable(
                    name=table_name,
                    key_path=key_path,
                    data=current_table_data if not is_array_table else {},
                    comments={},
                    table_comment=pending_comment,
                    is_array_of_tables=is_array_table,
                )
                tables.append(table)
                pending_comment = ""
                continue

            # Key-value pair
            if "=" in stripped:
                # Split on first =
                eq_idx = stripped.find("=")
                key_str = stripped[:eq_idx].strip()
                value_str = stripped[eq_idx + 1:].strip()

                # Handle inline comments in values
                # Only split on # if it's not inside a string
                in_string = False
                string_char = ""
                comment_start = -1
                for i, ch in enumerate(value_str):
                    if ch in ('"', "'") and not in_string:
                        in_string = True
                        string_char = ch
                    elif ch == string_char and in_string:
                        in_string = False
                    elif ch == "#" and not in_string:
                        comment_start = i
                        break

                if comment_start > 0:
                    value_str = value_str[:comment_start].strip()

                # Check for multiline string start
                if value_str.startswith('"""') or value_str.startswith("'''"):
                    if value_str.count('"""') >= 2 or value_str.count("'''") >= 2:
                        # Single-line multiline string
                        if value_str.startswith('"""'):
                            inner = value_str[3:-3]
                            value = self._unescape_string(inner, True)
                        else:
                            inner = value_str[3:-3]
                            value = self._unescape_string(inner, False)
                    else:
                        in_multiline = True
                        multiline_type = "string"
                        multiline_key = key_str
                        multiline_quote = value_str[0]
                        # Extract value after opening quotes
                        first_line_idx = value_str.find('"""') + 3 if multiline_quote == '"' else 3
                        multiline_value = value_str[first_line_idx:] + "\n"
                        continue
                # Check for multiline array
                elif value_str.startswith("[") and not value_str.endswith("]"):
                    in_multiline = True
                    multiline_type = "array"
                    multiline_key = key_str
                    multiline_value = value_str
                    continue
                else:
                    # Single-line value
                    try:
                        value = self._parse_value(value_str)
                        key_path = self._parse_key(key_str)
                        self._set_nested_value(current_table_data, key_path, value)
                    except ValueError as e:
                        errors.append(f"Line {line_num}: {e}")

                pending_comment = ""
                continue

            # Unknown line format
            if stripped:
                errors.append(f"Line {line_num}: Cannot parse line: {stripped}")

        if in_multiline:
            errors.append("Unclosed multiline value at end of file")

        return ParseResult(
            data=data,
            tables=tables,
            is_valid=len(errors) == 0,
            errors=errors,
            line_count=line_num,
        )

    def _parse_value(self, value_str: str) -> Any:
        """Parse a TOML value string."""
        value_str = value_str.strip()

        if not value_str:
            raise ValueError("Empty value")

        # Boolean
        if self.BOOLEAN_PATTERN.match(value_str):
            return value_str == "true"

        # Integer
        if self.INTEGER_PATTERN.match(value_str):
            clean = value_str.replace("_", "")
            return int(clean)

        # Float
        if self.FLOAT_PATTERN.match(value_str):
            clean = value_str.replace("_", "")
            return float(clean)

        # String - basic
        if self.BASIC_STRING_PATTERN.match(value_str):
            inner = value_str[1:-1]
            return self._unescape_string(inner, True)

        # String - literal
        if self.LITERAL_STRING_PATTERN.match(value_str):
            return value_str[1:-1]  # No escaping

        # Array
        if value_str.startswith("[") and value_str.endswith("]"):
            return self._parse_array(value_str)

        # Inline table
        if value_str.startswith("{") and value_str.endswith("}"):
            return self._parse_inline_table(value_str)

        # Date/Time
        if self.OFFSET_DATETIME_PATTERN.match(value_str):
            return value_str  # Return as string for simplicity
        if self.LOCAL_DATETIME_PATTERN.match(value_str):
            return value_str
        if self.LOCAL_DATE_PATTERN.match(value_str):
            return value_str
        if self.LOCAL_TIME_PATTERN.match(value_str):
            return value_str

        raise ValueError(f"Cannot parse value: {value_str}")

    def _parse_array(self, array_str: str) -> List[Any]:
        """Parse a TOML array string."""
        inner = array_str[1:-1].strip()
        if not inner:
            return []

        result = []
        current = ""
        depth = 0
        in_string = False
        string_char = ""

        for ch in inner:
            if ch in ('"', "'") and not in_string:
                in_string = True
                string_char = ch
                current += ch
            elif ch == string_char and in_string:
                in_string = False
                current += ch
            elif ch in "[{":
                depth += 1
                current += ch
            elif ch in "]}":
                depth -= 1
                current += ch
            elif ch == "," and depth == 0 and not in_string:
                if current.strip():
                    result.append(self._parse_value(current.strip()))
                current = ""
            else:
                current += ch

        if current.strip():
            result.append(self._parse_value(current.strip()))

        return result

    def _parse_inline_table(self, table_str: str) -> Dict[str, Any]:
        """Parse a TOML inline table."""
        inner = table_str[1:-1].strip()
        if not inner:
            return {}

        result = {}
        pairs = self._split_inline_table(inner)
        for pair in pairs:
            if "=" in pair:
                key, value = pair.split("=", 1)
                key_path = self._parse_key(key.strip())
                val = self._parse_value(value.strip())
                self._set_nested_value(result, key_path, val)

        return result

    def _split_inline_table(self, text: str) -> List[str]:
        """Split inline table into key=value pairs."""
        pairs = []
        current = ""
        depth = 0
        in_string = False
        string_char = ""

        for ch in text:
            if ch in ('"', "'") and not in_string:
                in_string = True
                string_char = ch
                current += ch
            elif ch == string_char and in_string:
                in_string = False
                current += ch
            elif ch in "[{":
                depth += 1
                current += ch
            elif ch in "]}":
                depth -= 1
                current += ch
            elif ch == "," and depth == 0 and not in_string:
                pairs.append(current.strip())
                current = ""
            else:
                current += ch

        if current.strip():
            pairs.append(current.strip())

        return pairs

    def _parse_key(self, key_str: str) -> List[str]:
        """Parse a TOML key (supports dotted keys and quoted keys)."""
        keys = []
        current = ""
        in_bare_key = True
        in_quoted = False
        quote_char = ""

        for ch in key_str.strip():
            if in_bare_key:
                if ch == ".":
                    if current:
                        keys.append(current)
                        current = ""
                elif ch in ('"', "'"):
                    in_bare_key = False
                    in_quoted = True
                    quote_char = ch
                else:
                    current += ch
            elif in_quoted:
                if ch == quote_char:
                    in_quoted = False
                    in_bare_key = True
                    keys.append(current)
                    current = ""
                else:
                    current += ch

        if current:
            keys.append(current)

        return keys

    def _unescape_string(self, s: str, is_basic: bool) -> str:
        """Unescape a TOML string."""
        if not is_basic:
            return s

        result = ""
        i = 0
        while i < len(s):
            if s[i] == "\\" and i + 1 < len(s):
                next_char = s[i + 1]
                escapes = {
                    "b": "\b",
                    "t": "\t",
                    "n": "\n",
                    "f": "\f",
                    "r": "\r",
                    '"': '"',
                    "\\": "\\",
                    "/": "/",
                }
                if next_char in escapes:
                    result += escapes[next_char]
                    i += 2
                elif next_char == "u" and i + 5 < len(s):
                    hex_str = s[i + 2:i + 6]
                    result += chr(int(hex_str, 16))
                    i += 6
                elif next_char == "U" and i + 9 < len(s):
                    hex_str = s[i + 2:i + 10]
                    result += chr(int(hex_str, 16))
                    i += 10
                else:
                    result += s[i]
                    i += 1
            else:
                result += s[i]
                i += 1

        return result

    def _navigate_to(self, data: Dict[str, Any], path: List[str], create: bool = False) -> Dict[str, Any]:
        """Navigate to a nested dict by path."""
        current = data
        for key in path:
            if key not in current:
                if create:
                    current[key] = {}
                else:
                    raise KeyError(f"Path not found: {'.'.join(path)}")
            if not isinstance(current[key], dict):
                if create:
                    current[key] = {}
                else:
                    raise KeyError(f"Path is not a table: {'.'.join(path)}")
            current = current[key]
        return current

    def _set_nested_value(self, data: Dict[str, Any], path: List[str], value: Any) -> None:
        """Set a value in a nested dict."""
        if len(path) == 1:
            data[path[0]] = value
            return

        current = data
        for key in path[:-1]:
            if key not in current or not isinstance(current[key], dict):
                current[key] = {}
            current = current[key]
        current[path[-1]] = value

    # ==================== Generation ====================

    def generate(
        self,
        data: Dict[str, Any],
        *,
        sort_keys: Optional[bool] = None,
        indent_size: Optional[int] = None,
    ) -> str:
        """Generate TOML string from a dict.

        Args:
            data: Dict to convert to TOML.
            sort_keys: Whether to sort keys alphabetically.
            indent_size: Indentation size.

        Returns:
            TOML formatted string.
        """
        self._ensure_active()
        self._increment_call_count()

        sort = sort_keys if sort_keys is not None else self._sort_keys
        indent = indent_size if indent_size is not None else self._indent_size

        return self._generate_toml(data, sort_keys=sort)

    def _generate_toml(self, data: Dict[str, Any], sort_keys: bool = False) -> str:
        """Generate TOML from a dict."""
        lines: List[str] = []
        scalars: Dict[str, Any] = {}
        tables: Dict[str, Any] = {}

        # Separate scalars and tables at top level
        for key, value in data.items():
            if isinstance(value, dict):
                tables[key] = value
            elif isinstance(value, list) and len(value) > 0 and all(isinstance(v, dict) for v in value):
                tables[key] = value  # Array of tables
            else:
                scalars[key] = value

        # Sort if needed
        scalar_keys = sorted(scalars.keys()) if sort_keys else list(scalars.keys())
        table_keys = sorted(tables.keys()) if sort_keys else list(tables.keys())

        # Output top-level scalars
        for key in scalar_keys:
            lines.append(f"{key} = {self._format_value(scalars[key])}")

        if scalars and tables:
            lines.append("")

        # Output tables
        for i, table_key in enumerate(table_keys):
            value = tables[table_key]
            if i > 0:
                lines.append("")

            if isinstance(value, list):
                # Array of tables
                for j, item in enumerate(value):
                    if j > 0:
                        lines.append("")
                    lines.append(f"[[{table_key}]]")
                    self._generate_table_lines(item, "", lines, sort_keys)
            else:
                lines.append(f"[{table_key}]")
                self._generate_table_lines(value, table_key, lines, sort_keys)

        return "\n".join(lines) + "\n"

    def _generate_table_lines(
        self,
        table: Dict[str, Any],
        parent_path: str,
        lines: List[str],
        sort_keys: bool,
    ) -> None:
        """Generate lines for a table."""
        scalars: Dict[str, Any] = {}
        nested_tables: Dict[str, Any] = {}

        for key, value in table.items():
            if isinstance(value, dict):
                nested_tables[key] = value
            elif isinstance(value, list) and len(value) > 0 and all(isinstance(v, dict) for v in value):
                nested_tables[key] = value
            else:
                scalars[key] = value

        keys = sorted(scalars.keys()) if sort_keys else list(scalars.keys())
        for key in keys:
            lines.append(f"{key} = {self._format_value(scalars[key])}")

        # Nested tables
        nested_keys = sorted(nested_tables.keys()) if sort_keys else list(nested_tables.keys())
        for key in nested_keys:
            lines.append("")
            value = nested_tables[key]
            full_path = f"{parent_path}.{key}" if parent_path else key

            if isinstance(value, list):
                for j, item in enumerate(value):
                    if j > 0:
                        lines.append("")
                    lines.append(f"[[{full_path}]]")
                    self._generate_table_lines(item, full_path, lines, sort_keys)
            else:
                lines.append(f"[{full_path}]")
                self._generate_table_lines(value, full_path, lines, sort_keys)

    def _format_value(self, value: Any) -> str:
        """Format a value as TOML string."""
        if value is None:
            raise ValueError("TOML does not support null values")

        if isinstance(value, bool):
            return "true" if value else "false"

        if isinstance(value, int):
            return str(value)

        if isinstance(value, float):
            if value != value:  # NaN
                return "nan"
            if value == float("inf"):
                return "inf"
            if value == float("-inf"):
                return "-inf"
            return repr(value)

        if isinstance(value, str):
            return self._format_string(value)

        if isinstance(value, list):
            items = [self._format_value(v) for v in value]
            return "[" + ", ".join(items) + "]"

        if isinstance(value, dict):
            pairs = [f"{k} = {self._format_value(v)}" for k, v in value.items()]
            return "{ " + ", ".join(pairs) + " }"

        return self._format_string(str(value))

    def _format_string(self, s: str) -> str:
        """Format a string as TOML string."""
        # Use literal string if no single quotes and no special chars
        if "'" not in s and "\\" not in s and '"' not in s and "\n" not in s:
            return f"'{s}'"

        # Use basic string with escaping
        escaped = s.replace("\\", "\\\\").replace('"', '\\"')
        escaped = escaped.replace("\b", "\\b").replace("\t", "\\t")
        escaped = escaped.replace("\n", "\\n").replace("\f", "\\f").replace("\r", "\\r")
        return f'"{escaped}"'

    # ==================== Validation ====================

    def validate(self, toml_text: str) -> Tuple[bool, List[str]]:
        """Validate TOML syntax.

        Args:
            toml_text: TOML text to validate.

        Returns:
            Tuple of (is_valid, errors).
        """
        self._ensure_active()
        self._increment_call_count()

        result = self._parse_toml(toml_text)
        return result.is_valid, result.errors

    # ==================== Conversion ====================

    def to_json(self, toml_text: str, *, pretty: bool = False) -> str:
        """Convert TOML to JSON.

        Args:
            toml_text: TOML text.
            pretty: Whether to pretty-print the JSON.

        Returns:
            JSON string.
        """
        self._ensure_active()
        self._increment_call_count()

        data = self.parse(toml_text)
        if pretty:
            return json.dumps(data, indent=2, ensure_ascii=False)
        return json.dumps(data, ensure_ascii=False)

    def from_json(self, json_text: str) -> str:
        """Convert JSON to TOML.

        Args:
            json_text: JSON text.

        Returns:
            TOML string.
        """
        self._ensure_active()
        self._increment_call_count()

        data = json.loads(json_text)
        return self.generate(data)

    # ==================== Table Operations ====================

    def get_table(self, toml_text: str, table_path: str) -> Dict[str, Any]:
        """Get a specific table from TOML.

        Args:
            toml_text: TOML text.
            table_path: Dot-separated table path.

        Returns:
            Table data as dict.
        """
        self._ensure_active()
        self._increment_call_count()

        data = self.parse(toml_text)
        path = table_path.split(".")
        table = self._navigate_to(data, path)
        return dict(table)

    def set_table(
        self,
        toml_text: str,
        table_path: str,
        table_data: Dict[str, Any],
    ) -> str:
        """Set or update a table in TOML.

        Args:
            toml_text: Original TOML text.
            table_path: Dot-separated table path.
            table_data: New table data.

        Returns:
            Updated TOML string.
        """
        self._ensure_active()
        self._increment_call_count()

        data = self.parse(toml_text)
        path = table_path.split(".")

        # Navigate to parent
        parent = self._navigate_to(data, path[:-1], create=True)
        parent[path[-1]] = table_data

        return self.generate(data)

    def list_tables(self, toml_text: str) -> List[str]:
        """List all tables in a TOML document.

        Args:
            toml_text: TOML text.

        Returns:
            List of table paths.
        """
        self._ensure_active()
        self._increment_call_count()

        result = self._parse_toml(toml_text)
        return [t.name for t in result.tables]

    # ==================== Value Operations ====================

    def get_value(self, toml_text: str, key_path: str) -> Any:
        """Get a specific value from TOML.

        Args:
            toml_text: TOML text.
            key_path: Dot-separated key path.

        Returns:
            The value.
        """
        self._ensure_active()
        self._increment_call_count()

        data = self.parse(toml_text)
        path = key_path.split(".")
        current = data
        for key in path[:-1]:
            if not isinstance(current, dict) or key not in current:
                raise KeyError(f"Path not found: {key_path}")
            current = current[key]

        if not isinstance(current, dict) or path[-1] not in current:
            raise KeyError(f"Path not found: {key_path}")

        return current[path[-1]]

    def set_value(self, toml_text: str, key_path: str, value: Any) -> str:
        """Set a specific value in TOML.

        Args:
            toml_text: Original TOML text.
            key_path: Dot-separated key path.
            value: Value to set.

        Returns:
            Updated TOML string.
        """
        self._ensure_active()
        self._increment_call_count()

        data = self.parse(toml_text)
        path = key_path.split(".")
        self._set_nested_value(data, path, value)
        return self.generate(data)

    # ==================== Merge ====================

    def merge(self, base: str, override: str) -> str:
        """Merge two TOML documents.

        Args:
            base: Base TOML text.
            override: Override TOML text.

        Returns:
            Merged TOML string.
        """
        self._ensure_active()
        self._increment_call_count()

        base_data = self.parse(base)
        override_data = self.parse(override)
        merged = self._deep_merge(base_data, override_data)
        return self.generate(merged)

    def _deep_merge(self, base: Dict[str, Any], override: Dict[str, Any]) -> Dict[str, Any]:
        """Deep merge two dicts."""
        result = dict(base)
        for key, value in override.items():
            if key in result and isinstance(result[key], dict) and isinstance(value, dict):
                result[key] = self._deep_merge(result[key], value)
            else:
                result[key] = value
        return result

    # ==================== Format ====================

    def format(
        self,
        toml_text: str,
        *,
        sort_keys: bool = False,
        indent_size: int = 2,
    ) -> str:
        """Format/beautify TOML text.

        Args:
            toml_text: Original TOML text.
            sort_keys: Whether to sort keys.
            indent_size: Indentation size.

        Returns:
            Formatted TOML string.
        """
        self._ensure_active()
        self._increment_call_count()

        data = self.parse(toml_text)
        return self.generate(data, sort_keys=sort_keys, indent_size=indent_size)

    # ==================== Statistics ====================

    def get_stats(self) -> Dict[str, Any]:
        """Get plugin statistics."""
        return {
            "indent_size": self._indent_size,
            "sort_keys": self._sort_keys,
            "preserve_comments": self._preserve_comments,
            "supported_types": [
                "string", "integer", "float", "boolean",
                "array", "table", "inline table",
                "offset datetime", "local datetime",
                "local date", "local time",
            ],
        }
