"""Tool framework for LiuHao AI OS.

Provides a complete tool execution system with:

- **BaseTool** — Abstract base class for all tools with parameter
  schema validation (dataclass-based Pydantic-style) and
  standardized results.

- **ToolRegistry** — Registry for registering and looking up tools
  by name and category.

- **ToolExecutor** — Tool execution manager with selection, parameter
  validation, timeout handling, batch execution, and statistics.

- **Built-in Tools** — Calculator, Search, CodeRunner, Memory,
  DateTime, and File tools.
"""
from __future__ import annotations

from .base import (
    BaseTool,
    ToolRegistry,
    ToolResult,
    ParameterSchema,
    ToolCategory,
    ToolStatus,
    ResultStatus,
)
from .builtin import (
    CalculatorTool,
    SearchTool,
    CodeRunnerTool,
    MemoryTool,
    DateTimeTool,
    FileTool,
)
from .executor import (
    ToolExecutor,
    ToolCandidate,
    ToolCallStats,
    ToolExecutorStats,
    BatchResult,
)

# Legacy kernel-backed tool surface (formerly ``src/ai/tools.py``; moved into
# this package as ``kernel_tools.py`` because a same-named package shadows a
# same-named module). ``liuhao.py`` imports these from the package root.
from .kernel_tools import (
    build_tool_prompt,
    make_tools,
    parse_tool_call,
)

__all__ = [
    # Base
    "BaseTool",
    "ToolRegistry",
    "ToolResult",
    "ParameterSchema",
    "ToolCategory",
    "ToolStatus",
    "ResultStatus",
    # Built-in tools
    "CalculatorTool",
    "SearchTool",
    "CodeRunnerTool",
    "MemoryTool",
    "DateTimeTool",
    "FileTool",
    # Executor
    "ToolExecutor",
    "ToolCandidate",
    "ToolCallStats",
    "ToolExecutorStats",
    "BatchResult",
    # Legacy kernel-backed tools
    "build_tool_prompt",
    "make_tools",
    "parse_tool_call",
]
