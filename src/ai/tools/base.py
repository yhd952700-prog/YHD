"""Base tool framework for LiuHao AI OS.

Provides the abstract base class for tools, parameter schema validation
(using dataclasses in a Pydantic-style), tool result types, and a
tool registry for registering and looking up tools by name/category.

Design principles:
  - Tools are self-describing (name, description, parameters)
  - Parameter validation is explicit and typed
  - Results carry status, output, and metadata
  - Registry supports lookup by name, category, and capability
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional, Type


# ======================================================================
# Enums
# ======================================================================

class ToolCategory(str, Enum):
    """Categories for classifying tools."""

    COMPUTATION = "computation"
    """Math, calculation, code execution tools."""

    INFORMATION = "information"
    """Search, lookup, knowledge retrieval tools."""

    MEMORY = "memory"
    """Memory read/write tools."""

    UTILITY = "utility"
    """General utility tools (time, etc.)."""

    FILE = "file"
    """File system interaction tools."""

    COMMUNICATION = "communication"
    """Messaging, notification tools."""

    ANALYSIS = "analysis"
    """Data analysis and processing tools."""

    CREATIVE = "creative"
    """Content generation and creative tools."""


class ToolStatus(str, Enum):
    """Lifecycle status of a tool."""

    REGISTERED = "registered"
    """Tool is registered but not yet validated."""

    VALID = "valid"
    """Tool passed validation."""

    ACTIVE = "active"
    """Tool is active and available for use."""

    DISABLED = "disabled"
    """Tool is temporarily disabled."""

    ERROR = "error"
    """Tool has an error and cannot be used."""


class ResultStatus(str, Enum):
    """Status of a tool execution result."""

    SUCCESS = "success"
    """Tool executed successfully."""

    PARTIAL = "partial"
    """Tool partially succeeded (some output, some issues)."""

    FAILED = "failed"
    """Tool execution failed."""

    TIMEOUT = "timeout"
    """Tool execution timed out."""

    REJECTED = "rejected"
    """Tool execution was rejected (e.g., safety policy)."""


# ======================================================================
# Parameter Schema
# ======================================================================

@dataclass
class ParameterSchema:
    """Schema definition for a single tool parameter.

    Similar to Pydantic/JSON Schema style but implemented with dataclasses
    for lightweight validation without extra dependencies.

    Attributes:
        name: Parameter name.
        type: Expected Python type (str, int, float, bool, list, dict, etc.).
        description: Human-readable description.
        required: Whether the parameter is required.
        default: Default value if not provided.
        choices: List of allowed values (for enum-like parameters).
        min_value: Minimum value (for numeric types).
        max_value: Maximum value (for numeric types).
        min_length: Minimum length (for strings/lists).
        max_length: Maximum length (for strings/lists).
        pattern: Regex pattern (for string validation).
        nested_schema: Schema for nested objects/dicts.
    """

    name: str
    type: Type = str
    description: str = ""
    required: bool = True
    default: Any = None
    choices: Optional[List[Any]] = None
    min_value: Optional[float] = None
    max_value: Optional[float] = None
    min_length: Optional[int] = None
    max_length: Optional[int] = None
    pattern: Optional[str] = None
    nested_schema: Optional[List["ParameterSchema"]] = None

    def validate(self, value: Any) -> tuple[bool, Optional[str]]:
        """Validate a value against this schema.

        Returns:
            Tuple of (is_valid, error_message or None).
        """
        # Check type
        if value is None:
            if self.required and self.default is None:
                return False, f"Parameter '{self.name}' is required but got None"
            return True, None

        if not self._check_type(value):
            expected = self.type.__name__ if hasattr(self.type, "__name__") else str(self.type)
            return False, (
                f"Parameter '{self.name}' expected type {expected}, "
                f"got {type(value).__name__}"
            )

        # Check choices
        if self.choices is not None and value not in self.choices:
            return False, (
                f"Parameter '{self.name}' must be one of {self.choices}, "
                f"got {value!r}"
            )

        # Check numeric bounds
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            if self.min_value is not None and value < self.min_value:
                return False, (
                    f"Parameter '{self.name}' must be >= {self.min_value}, "
                    f"got {value}"
                )
            if self.max_value is not None and value > self.max_value:
                return False, (
                    f"Parameter '{self.name}' must be <= {self.max_value}, "
                    f"got {value}"
                )

        # Check length bounds
        if isinstance(value, (str, list, dict)):
            length = len(value)
            if self.min_length is not None and length < self.min_length:
                return False, (
                    f"Parameter '{self.name}' must have length >= {self.min_length}, "
                    f"got {length}"
                )
            if self.max_length is not None and length > self.max_length:
                return False, (
                    f"Parameter '{self.name}' must have length <= {self.max_length}, "
                    f"got {length}"
                )

        # Check pattern (for strings)
        if isinstance(value, str) and self.pattern is not None:
            import re
            if not re.match(self.pattern, value):
                return False, (
                    f"Parameter '{self.name}' does not match pattern {self.pattern}"
                )

        # Validate nested schema (for dicts)
        if isinstance(value, dict) and self.nested_schema is not None:
            for param_schema in self.nested_schema:
                sub_value = value.get(param_schema.name, param_schema.default)
                valid, error = param_schema.validate(sub_value)
                if not valid:
                    return False, f"Nested parameter error: {error}"

        return True, None

    def _check_type(self, value: Any) -> bool:
        """Check if value matches the expected type."""
        # Handle bool specially (bool is a subclass of int)
        if self.type == int and isinstance(value, bool):
            return False
        if self.type == float and isinstance(value, bool):
            return False
        return isinstance(value, self.type)

    def to_dict(self) -> Dict[str, Any]:
        """Serialize schema to a dictionary."""
        return {
            "name": self.name,
            "type": self.type.__name__,
            "description": self.description,
            "required": self.required,
            "default": self.default,
            "choices": list(self.choices) if self.choices else None,
            "min_value": self.min_value,
            "max_value": self.max_value,
            "min_length": self.min_length,
            "max_length": self.max_length,
            "pattern": self.pattern,
            "nested_schema": (
                [s.to_dict() for s in self.nested_schema]
                if self.nested_schema else None
            ),
        }


# ======================================================================
# Tool Result
# ======================================================================

@dataclass
class ToolResult:
    """Result of a tool execution.

    Attributes:
        status: Execution status (success, failed, timeout, etc.).
        output: The primary output data.
        error: Error message if status is not success.
        metadata: Additional metadata about the execution.
        execution_time_ms: How long the execution took.
    """

    status: ResultStatus = ResultStatus.SUCCESS
    output: Any = None
    error: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)
    execution_time_ms: float = 0.0

    @classmethod
    def success(
        cls,
        output: Any,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> "ToolResult":
        """Create a successful result."""
        return cls(
            status=ResultStatus.SUCCESS,
            output=output,
            metadata=metadata or {},
        )

    @classmethod
    def failure(
        cls,
        error: str,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> "ToolResult":
        """Create a failed result."""
        return cls(
            status=ResultStatus.FAILED,
            output=None,
            error=error,
            metadata=metadata or {},
        )

    @classmethod
    def timeout(cls, error: str = "Execution timed out") -> "ToolResult":
        """Create a timeout result."""
        return cls(status=ResultStatus.TIMEOUT, error=error)

    @classmethod
    def rejected(cls, reason: str) -> "ToolResult":
        """Create a rejected result."""
        return cls(status=ResultStatus.REJECTED, error=reason)

    @property
    def ok(self) -> bool:
        """Whether the result is successful."""
        return self.status == ResultStatus.SUCCESS

    def to_dict(self) -> Dict[str, Any]:
        """Serialize result to a dictionary."""
        return {
            "status": self.status.value,
            "output": self.output,
            "error": self.error,
            "metadata": dict(self.metadata),
            "execution_time_ms": self.execution_time_ms,
        }


# ======================================================================
# Base Tool
# ======================================================================

class BaseTool(ABC):
    """Abstract base class for all tools.

    Subclasses must implement ``_execute()`` and define the tool's
    name, description, and parameter schema.

    Usage::

        class MyTool(BaseTool):
            name = "my_tool"
            description = "Does something useful"
            category = ToolCategory.UTILITY

            parameters = [
                ParameterSchema(name="input", type=str, description="The input"),
            ]

            def _execute(self, **kwargs):
                return {"result": kwargs["input"]}
    """

    name: str = ""
    description: str = ""
    category: ToolCategory = ToolCategory.UTILITY
    parameters: List[ParameterSchema] = field(default_factory=list)  # type: ignore

    def __init__(self) -> None:
        """Initialize the tool."""
        self._status: ToolStatus = ToolStatus.REGISTERED
        self._call_count: int = 0
        self._total_time_ms: float = 0.0

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def execute(self, **kwargs: Any) -> ToolResult:
        """Execute the tool with validated parameters.

        Args:
            **kwargs: Tool parameters.

        Returns:
            ToolResult with the execution outcome.
        """
        import time

        start_time = time.time()
        self._call_count += 1

        try:
            # Validate parameters
            valid, error = self.validate_params(kwargs)
            if not valid:
                return ToolResult.failure(f"Parameter validation error: {error}")

            # Apply defaults for missing optional parameters
            params = self._apply_defaults(kwargs)

            # Execute
            output = self._execute(**params)
            result = ToolResult.success(output)

        except Exception as e:
            result = ToolResult.failure(str(e))

        result.execution_time_ms = round((time.time() - start_time) * 1000, 3)
        self._total_time_ms += result.execution_time_ms
        return result

    def validate_params(self, params: Dict[str, Any]) -> tuple[bool, Optional[str]]:
        """Validate parameters against the tool's schema.

        Args:
            params: Dictionary of parameter values.

        Returns:
            Tuple of (is_valid, error_message or None).
        """
        schema_map = {p.name: p for p in self.parameters}

        # Check required parameters
        for param in self.parameters:
            if param.required and param.name not in params and param.default is None:
                return False, f"Missing required parameter: '{param.name}'"

        # Validate provided parameters
        for name, value in params.items():
            if name not in schema_map:
                # Unknown parameters are allowed (extra data ignored)
                continue
            valid, error = schema_map[name].validate(value)
            if not valid:
                return False, error

        return True, None

    # ------------------------------------------------------------------
    # Abstract method
    # ------------------------------------------------------------------

    @abstractmethod
    def _execute(self, **kwargs: Any) -> Any:
        """Execute the tool logic. Must be implemented by subclasses.

        Args:
            **kwargs: Validated tool parameters.

        Returns:
            The tool output (will be wrapped in ToolResult.success).
        """
        raise NotImplementedError

    # ------------------------------------------------------------------
    # Properties & info
    # ------------------------------------------------------------------

    @property
    def status(self) -> ToolStatus:
        """Current tool status."""
        return self._status

    @property
    def call_count(self) -> int:
        """Number of times this tool has been called."""
        return self._call_count

    @property
    def total_time_ms(self) -> float:
        """Total execution time across all calls."""
        return self._total_time_ms

    @property
    def avg_time_ms(self) -> float:
        """Average execution time per call."""
        if self._call_count == 0:
            return 0.0
        return self._total_time_ms / self._call_count

    def info(self) -> Dict[str, Any]:
        """Get tool metadata as a dictionary."""
        return {
            "name": self.name,
            "description": self.description,
            "category": self.category.value,
            "parameters": [p.to_dict() for p in self.parameters],
            "status": self._status.value,
            "call_count": self._call_count,
            "avg_time_ms": round(self.avg_time_ms, 3),
        }

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _apply_defaults(self, params: Dict[str, Any]) -> Dict[str, Any]:
        """Apply default values for missing optional parameters."""
        result = dict(params)
        for param in self.parameters:
            if param.name not in result and not param.required and param.default is not None:
                result[param.name] = param.default
        return result


# ======================================================================
# Tool Registry
# ======================================================================

class ToolRegistry:
    """Registry for managing and looking up tools.

    Supports registration, lookup by name/category, and bulk operations.

    Usage::

        registry = ToolRegistry()
        registry.register(CalculatorTool())
        registry.register(SearchTool())

        tool = registry.get("calculator")
        tools = registry.by_category(ToolCategory.COMPUTATION)
    """

    def __init__(self) -> None:
        self._tools: Dict[str, BaseTool] = {}
        self._categories: Dict[ToolCategory, List[str]] = {}

    # ------------------------------------------------------------------
    # Registration
    # ------------------------------------------------------------------

    def register(self, tool: BaseTool) -> str:
        """Register a tool.

        Args:
            tool: The tool instance to register.

        Returns:
            The tool name.

        Raises:
            ValueError: If a tool with the same name is already registered.
        """
        if not tool.name:
            raise ValueError("Tool must have a name")

        if tool.name in self._tools:
            raise ValueError(f"Tool '{tool.name}' is already registered")

        self._tools[tool.name] = tool
        tool._status = ToolStatus.VALID

        # Add to category index
        if tool.category not in self._categories:
            self._categories[tool.category] = []
        self._categories[tool.category].append(tool.name)

        return tool.name

    def unregister(self, tool_name: str) -> bool:
        """Unregister a tool.

        Args:
            tool_name: Name of the tool to remove.

        Returns:
            True if the tool was found and removed.
        """
        tool = self._tools.pop(tool_name, None)
        if tool is None:
            return False

        # Remove from category index
        if tool.category in self._categories:
            if tool_name in self._categories[tool.category]:
                self._categories[tool.category].remove(tool_name)

        return True

    # ------------------------------------------------------------------
    # Lookup
    # ------------------------------------------------------------------

    def get(self, tool_name: str) -> Optional[BaseTool]:
        """Get a tool by name.

        Args:
            tool_name: Name of the tool.

        Returns:
            The tool instance, or None if not found.
        """
        return self._tools.get(tool_name)

    def has(self, tool_name: str) -> bool:
        """Check if a tool is registered."""
        return tool_name in self._tools

    def by_category(self, category: ToolCategory) -> List[BaseTool]:
        """Get all tools in a category."""
        names = self._categories.get(category, [])
        return [self._tools[name] for name in names if name in self._tools]

    def list_names(self) -> List[str]:
        """List all registered tool names."""
        return sorted(self._tools.keys())

    def list_all(self) -> List[BaseTool]:
        """List all registered tools."""
        return list(self._tools.values())

    # ------------------------------------------------------------------
    # Properties
    # ------------------------------------------------------------------

    @property
    def tool_count(self) -> int:
        """Number of registered tools."""
        return len(self._tools)

    @property
    def categories(self) -> List[ToolCategory]:
        """List of categories that have tools."""
        return [cat for cat, names in self._categories.items() if names]

    def stats(self) -> Dict[str, Any]:
        """Get registry statistics."""
        category_counts = {
            cat.value: len(names)
            for cat, names in self._categories.items()
        }
        total_calls = sum(t.call_count for t in self._tools.values())
        return {
            "total_tools": len(self._tools),
            "categories": category_counts,
            "total_calls": total_calls,
        }
