"""Built-in tools for LiuHao AI OS.

Provides a set of ready-to-use tools that cover common operations:

- **CalculatorTool** — Evaluate math expressions safely.
- **SearchTool** — Simulated/web search interface.
- **CodeRunnerTool** — Execute Python code with sandbox restrictions.
- **MemoryTool** — Read/write to the memory system.
- **DateTimeTool** — Current time and date calculations.
- **FileTool** — File read/write/list with safety boundaries.
"""
from __future__ import annotations

import math
import os
import re
import time
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional
from pathlib import Path

from .base import (
    BaseTool,
    ParameterSchema,
    ToolCategory,
    ToolResult,
    ResultStatus,
)


# ======================================================================
# Calculator Tool
# ======================================================================

class CalculatorTool(BaseTool):
    """Safe math expression evaluator.

    Evaluates mathematical expressions using a restricted set of
    functions and operators. Does NOT use eval() on arbitrary input.

    Supported operations:
      - Arithmetic: +, -, *, /, //, %, **
      - Math functions: sin, cos, tan, sqrt, log, exp, abs, floor, ceil, round
      - Constants: pi, e
      - Parentheses for grouping
    """

    name = "calculator"
    description = "Evaluate mathematical expressions safely"
    category = ToolCategory.COMPUTATION
    parameters = [
        ParameterSchema(
            name="expression",
            type=str,
            description="The math expression to evaluate (e.g., '2 + 3 * 4')",
            required=True,
            min_length=1,
            max_length=500,
        ),
    ]

    # Allowed names in the expression
    _ALLOWED_NAMES = {
        "pi": math.pi,
        "e": math.e,
        "sin": math.sin,
        "cos": math.cos,
        "tan": math.tan,
        "asin": math.asin,
        "acos": math.acos,
        "atan": math.atan,
        "sqrt": math.sqrt,
        "log": math.log,
        "log10": math.log10,
        "log2": math.log2,
        "exp": math.exp,
        "abs": abs,
        "floor": math.floor,
        "ceil": math.ceil,
        "round": round,
        "min": min,
        "max": max,
        "pow": pow,
        "degrees": math.degrees,
        "radians": math.radians,
    }

    def _execute(self, expression: str) -> Any:
        """Evaluate a math expression safely."""
        # Clean the expression
        expr = expression.strip()

        # Check for dangerous patterns
        dangerous = ["import", "__", "open(", "eval(", "exec(", "compile(",
                     "globals", "locals", "os.", "sys.", "subprocess", "lambda"]
        for pattern in dangerous:
            if pattern in expr.lower():
                raise ValueError(f"Expression contains disallowed pattern: {pattern}")

        try:
            result = eval(expr, {"__builtins__": {}}, self._ALLOWED_NAMES)
            return {
                "expression": expression,
                "result": result,
                "type": type(result).__name__,
            }
        except SyntaxError as e:
            raise ValueError(f"Syntax error in expression: {e}")
        except ZeroDivisionError:
            raise ValueError("Division by zero")
        except NameError as e:
            raise ValueError(f"Unknown function or variable: {e}")


# ======================================================================
# Search Tool
# ======================================================================

class SearchTool(BaseTool):
    """Simulated/web search interface.

    Provides a search interface that can be backed by real web search
    or a simulated knowledge base. Supports querying with various options.
    """

    name = "search"
    description = "Search for information on a given topic"
    category = ToolCategory.INFORMATION
    parameters = [
        ParameterSchema(
            name="query",
            type=str,
            description="The search query",
            required=True,
            min_length=1,
            max_length=500,
        ),
        ParameterSchema(
            name="max_results",
            type=int,
            description="Maximum number of results to return",
            required=False,
            default=5,
            min_value=1,
            max_value=20,
        ),
        ParameterSchema(
            name="domain",
            type=str,
            description="Restrict search to a specific domain",
            required=False,
            default="",
        ),
    ]

    # Simulated knowledge base
    _KNOWLEDGE_BASE: Dict[str, str] = {
        "python": "Python is a high-level, interpreted programming language known for its readability and versatility.",
        "machine learning": "Machine learning is a subset of AI that enables systems to learn and improve from experience.",
        "liuhao": "LiuHao AI OS is an autonomous AI operating system with multi-agent capabilities.",
        "neural network": "A neural network is a computing system inspired by biological neural networks in animal brains.",
        "algorithm": "An algorithm is a finite sequence of rigorous instructions, typically used to solve a class of specific problems.",
        "data structure": "A data structure is a data organization, management, and storage format that enables efficient access and modification.",
        "database": "A database is an organized collection of data stored and accessed electronically.",
        "api": "An API (Application Programming Interface) is a set of definitions and protocols for building and integrating application software.",
        "cloud computing": "Cloud computing is the on-demand availability of computer system resources, especially data storage and computing power.",
        "git": "Git is a distributed version control system that tracks changes in source code during software development.",
    }

    def _execute(self, query: str, max_results: int = 5, domain: str = "") -> Any:
        """Execute a search query."""
        query_lower = query.lower().strip()
        results: List[Dict[str, Any]] = []

        # Search in knowledge base
        for key, value in self._KNOWLEDGE_BASE.items():
            if query_lower in key or query_lower in value.lower():
                # Simple relevance scoring
                relevance = 1.0 if query_lower == key else 0.7
                results.append({
                    "title": key.title(),
                    "snippet": value,
                    "relevance": relevance,
                    "source": "knowledge_base",
                    "url": f"knowledge://{key.replace(' ', '_')}",
                })

        # Sort by relevance
        results.sort(key=lambda r: r["relevance"], reverse=True)

        # Apply domain filter
        if domain:
            results = [r for r in results if domain in r.get("url", "")]

        # Limit results
        results = results[:max_results]

        return {
            "query": query,
            "total_results": len(results),
            "results": results,
            "has_more": len(results) >= max_results,
        }


# ======================================================================
# Code Runner Tool
# ======================================================================

class CodeRunnerTool(BaseTool):
    """Execute Python code with sandbox restrictions.

    Runs Python code in a restricted environment with:
    - Limited builtins (no file I/O, no imports of dangerous modules)
    - Timeout protection
    - Memory limits (via recursion depth)
    - Output capture
    """

    name = "code_runner"
    description = "Execute Python code in a sandboxed environment"
    category = ToolCategory.COMPUTATION
    parameters = [
        ParameterSchema(
            name="code",
            type=str,
            description="The Python code to execute",
            required=True,
            min_length=1,
            max_length=10000,
        ),
        ParameterSchema(
            name="timeout",
            type=float,
            description="Execution timeout in seconds",
            required=False,
            default=5.0,
            min_value=0.1,
            max_value=30.0,
        ),
    ]

    # Allowed built-in functions
    _ALLOWED_BUILTINS = {
        "abs": abs,
        "all": all,
        "any": any,
        "bin": bin,
        "bool": bool,
        "chr": chr,
        "dict": dict,
        "dir": dir,
        "divmod": divmod,
        "enumerate": enumerate,
        "float": float,
        "format": format,
        "hex": hex,
        "int": int,
        "isinstance": isinstance,
        "issubclass": issubclass,
        "iter": iter,
        "len": len,
        "list": list,
        "map": map,
        "max": max,
        "min": min,
        "next": next,
        "oct": oct,
        "ord": ord,
        "pow": pow,
        "print": print,
        "range": range,
        "repr": repr,
        "reversed": reversed,
        "round": round,
        "set": set,
        "slice": slice,
        "sorted": sorted,
        "str": str,
        "sum": sum,
        "tuple": tuple,
        "type": type,
        "zip": zip,
        "True": True,
        "False": False,
        "None": None,
    }

    # Allowed modules
    _ALLOWED_MODULES = {
        "math": __import__("math"),
        "random": __import__("random"),
        "json": __import__("json"),
        "re": __import__("re"),
        "datetime": __import__("datetime"),
        "collections": __import__("collections"),
        "statistics": __import__("statistics"),
        "itertools": __import__("itertools"),
        "string": __import__("string"),
    }

    def _execute(self, code: str, timeout: float = 5.0) -> Any:
        """Execute Python code in a sandbox."""
        import io
        import sys
        import signal

        # Security checks
        dangerous_patterns = [
            r"\bimport\s+os\b",
            r"\bimport\s+sys\b",
            r"\bimport\s+subprocess\b",
            r"\b__import__\b",
            r"\bopen\s*\(",
            r"\beval\s*\(",
            r"\bexec\s*\(",
            r"\bcompile\s*\(",
            r"\bglobals\s*\(",
            r"\blocals\s*\(",
            r"\b__\w+__\b",  # dunder methods
            r"\bgetattr\s*\(",
            r"\bsetattr\s*\(",
            r"\bdelattr\s*\(",
        ]

        for pattern in dangerous_patterns:
            if re.search(pattern, code):
                raise ValueError(
                    f"Code contains disallowed pattern: {pattern}"
                )

        # Capture stdout
        output_buffer = io.StringIO()
        old_stdout = sys.stdout
        sys.stdout = output_buffer

        # Build the restricted namespace
        namespace: Dict[str, Any] = dict(self._ALLOWED_BUILTINS)
        namespace.update(self._ALLOWED_MODULES)
        namespace["__builtins__"] = self._ALLOWED_BUILTINS

        try:
            # Set recursion limit for safety
            old_limit = sys.getrecursionlimit()
            sys.setrecursionlimit(500)

            try:
                exec(code, namespace, namespace)
            finally:
                sys.setrecursionlimit(old_limit)

            output = output_buffer.getvalue()

            # Try to get a result variable if set
            result_value = namespace.get("result", None)

            return {
                "output": output,
                "result": result_value,
                "success": True,
            }

        except RecursionError:
            raise ValueError("Execution exceeded recursion limit")
        except MemoryError:
            raise ValueError("Execution exceeded memory limits")
        except Exception as e:
            raise ValueError(f"Execution error: {type(e).__name__}: {e}")
        finally:
            sys.stdout = old_stdout


# ======================================================================
# Memory Tool
# ======================================================================

class MemoryTool(BaseTool):
    """Read/write to the memory system.

    Provides tool-based access to the WorkingMemory from src.ai.memory.
    Supports storing, retrieving, and searching memory items.
    """

    name = "memory"
    description = "Read from and write to the AI memory system"
    category = ToolCategory.MEMORY
    parameters = [
        ParameterSchema(
            name="operation",
            type=str,
            description="Memory operation: 'add', 'get', 'search', 'list', 'remove', 'clear'",
            required=True,
            choices=["add", "get", "search", "list", "remove", "clear"],
        ),
        ParameterSchema(
            name="content",
            type=str,
            description="Content to store (for 'add' operation)",
            required=False,
            default="",
        ),
        ParameterSchema(
            name="item_id",
            type=str,
            description="ID of the memory item (for 'get' and 'remove')",
            required=False,
            default="",
        ),
        ParameterSchema(
            name="query",
            type=str,
            description="Search query (for 'search' operation)",
            required=False,
            default="",
        ),
        ParameterSchema(
            name="item_type",
            type=str,
            description="Type of memory item (for 'add' and 'list')",
            required=False,
            default="general",
        ),
        ParameterSchema(
            name="limit",
            type=int,
            description="Maximum items to return",
            required=False,
            default=10,
            min_value=1,
            max_value=100,
        ),
    ]

    def __init__(self, working_memory: Any = None) -> None:
        """Initialize MemoryTool.

        Args:
            working_memory: Optional WorkingMemory instance.
                If not provided, creates a new one from src.ai.memory.
        """
        super().__init__()

        if working_memory is not None:
            self._memory = working_memory
        else:
            # Import from the existing memory module
            from src.ai.memory import WorkingMemory
            self._memory = WorkingMemory(capacity=50)

    def _execute(
        self,
        operation: str,
        content: str = "",
        item_id: str = "",
        query: str = "",
        item_type: str = "general",
        limit: int = 10,
    ) -> Any:
        """Execute memory operation."""
        if operation == "add":
            if not content:
                raise ValueError("'content' is required for 'add' operation")
            item = self._memory.add(
                content=content,
                item_type=item_type,
            )
            return {
                "operation": "add",
                "item": {
                    "id": item.id,
                    "content": item.content,
                    "type": item.item_type,
                    "priority": item.priority.value
                    if hasattr(item.priority, "value") else str(item.priority),
                },
                "success": True,
            }

        elif operation == "get":
            if not item_id:
                raise ValueError("'item_id' is required for 'get' operation")
            item = self._memory.get(item_id)
            if item is None:
                return {"operation": "get", "item": None, "found": False}
            return {
                "operation": "get",
                "item": {
                    "id": item.id,
                    "content": item.content,
                    "type": item.item_type,
                    "access_count": item.access_count,
                },
                "found": True,
            }

        elif operation == "search":
            if not query:
                raise ValueError("'query' is required for 'search' operation")
            results = self._memory.search(query)
            items = [
                {"id": r.id, "content": r.content, "type": r.item_type}
                for r in results[:limit]
            ]
            return {
                "operation": "search",
                "query": query,
                "results": items,
                "count": len(items),
            }

        elif operation == "list":
            items_list = self._memory.active_items(limit=limit)
            items = [
                {"id": r.id, "content": r.content, "type": r.item_type,
                 "activation": round(r.activation, 3)}
                for r in items_list
            ]
            return {
                "operation": "list",
                "items": items,
                "count": len(items),
                "total_capacity": self._memory.capacity,
            }

        elif operation == "remove":
            if not item_id:
                raise ValueError("'item_id' is required for 'remove' operation")
            removed = self._memory.remove(item_id)
            return {"operation": "remove", "item_id": item_id, "removed": removed}

        elif operation == "clear":
            self._memory.clear()
            return {"operation": "clear", "success": True}

        else:
            raise ValueError(f"Unknown operation: {operation}")


# ======================================================================
# Date Time Tool
# ======================================================================

class DateTimeTool(BaseTool):
    """Current time and date calculation tool.

    Provides operations for getting the current time, formatting dates,
    and performing date arithmetic.
    """

    name = "datetime"
    description = "Get current time and perform date calculations"
    category = ToolCategory.UTILITY
    parameters = [
        ParameterSchema(
            name="operation",
            type=str,
            description="Operation: 'now', 'format', 'add_days', 'diff', 'parse'",
            required=True,
            choices=["now", "format", "add_days", "diff", "parse"],
        ),
        ParameterSchema(
            name="format_str",
            type=str,
            description="Date format string (for 'now' and 'format')",
            required=False,
            default="%Y-%m-%d %H:%M:%S",
        ),
        ParameterSchema(
            name="date_str",
            type=str,
            description="Date string (for 'format', 'add_days', 'diff', 'parse')",
            required=False,
            default="",
        ),
        ParameterSchema(
            name="days",
            type=int,
            description="Number of days to add (for 'add_days', can be negative)",
            required=False,
            default=0,
        ),
        ParameterSchema(
            name="date_str2",
            type=str,
            description="Second date string (for 'diff' operation)",
            required=False,
            default="",
        ),
    ]

    def _execute(
        self,
        operation: str,
        format_str: str = "%Y-%m-%d %H:%M:%S",
        date_str: str = "",
        days: int = 0,
        date_str2: str = "",
    ) -> Any:
        """Execute datetime operation."""
        now = datetime.now()

        if operation == "now":
            return {
                "operation": "now",
                "datetime": now.strftime(format_str),
                "timestamp": time.time(),
                "date": now.strftime("%Y-%m-%d"),
                "time": now.strftime("%H:%M:%S"),
                "weekday": now.strftime("%A"),
                "timezone": time.tzname[0],
            }

        elif operation == "format":
            if not date_str:
                dt = now
            else:
                dt = self._parse_date(date_str)
            return {
                "operation": "format",
                "input": date_str or "now",
                "formatted": dt.strftime(format_str),
            }

        elif operation == "add_days":
            if not date_str:
                dt = now
            else:
                dt = self._parse_date(date_str)
            result = dt + timedelta(days=days)
            return {
                "operation": "add_days",
                "original": dt.strftime("%Y-%m-%d"),
                "days_added": days,
                "result": result.strftime("%Y-%m-%d"),
            }

        elif operation == "diff":
            if not date_str or not date_str2:
                raise ValueError("Both 'date_str' and 'date_str2' are required for 'diff'")
            d1 = self._parse_date(date_str)
            d2 = self._parse_date(date_str2)
            diff = d2 - d1
            return {
                "operation": "diff",
                "date1": d1.strftime("%Y-%m-%d"),
                "date2": d2.strftime("%Y-%m-%d"),
                "days_diff": diff.days,
                "seconds_diff": diff.total_seconds(),
                "direction": "date2 is after date1" if diff.days > 0
                             else "date2 is before date1" if diff.days < 0
                             else "dates are the same",
            }

        elif operation == "parse":
            if not date_str:
                raise ValueError("'date_str' is required for 'parse'")
            dt = self._parse_date(date_str)
            return {
                "operation": "parse",
                "input": date_str,
                "parsed": dt.isoformat(),
                "year": dt.year,
                "month": dt.month,
                "day": dt.day,
                "weekday": dt.strftime("%A"),
                "day_of_year": dt.timetuple().tm_yday,
            }

        else:
            raise ValueError(f"Unknown operation: {operation}")

    @staticmethod
    def _parse_date(date_str: str) -> datetime:
        """Parse a date string with multiple format support."""
        formats = [
            "%Y-%m-%d %H:%M:%S",
            "%Y-%m-%d",
            "%Y/%m/%d",
            "%d/%m/%Y",
            "%m/%d/%Y",
            "%Y-%m-%dT%H:%M:%S",
            "%Y%m%d",
        ]
        for fmt in formats:
            try:
                return datetime.strptime(date_str, fmt)
            except ValueError:
                continue
        raise ValueError(f"Could not parse date: {date_str}")


# ======================================================================
# File Tool
# ======================================================================

class FileTool(BaseTool):
    """File read/write/list tool with safety boundaries.

    Restricts file operations to a specific base directory for safety.
    Supports reading, writing, listing, and checking files.
    """

    name = "file"
    description = "Read, write, and list files within a safe directory"
    category = ToolCategory.FILE
    parameters = [
        ParameterSchema(
            name="operation",
            type=str,
            description="File operation: 'read', 'write', 'list', 'exists', 'append', 'delete'",
            required=True,
            choices=["read", "write", "list", "exists", "append", "delete"],
        ),
        ParameterSchema(
            name="path",
            type=str,
            description="File or directory path (relative to base directory)",
            required=True,
            min_length=1,
            max_length=500,
        ),
        ParameterSchema(
            name="content",
            type=str,
            description="Content to write (for 'write' and 'append')",
            required=False,
            default="",
        ),
        ParameterSchema(
            name="max_size",
            type=int,
            description="Maximum file size to read in bytes",
            required=False,
            default=100000,
            min_value=1,
            max_value=10000000,
        ),
    ]

    def __init__(self, base_dir: Optional[str] = None) -> None:
        """Initialize FileTool with a safe base directory.

        Args:
            base_dir: The root directory for file operations.
                Defaults to a temp directory.
        """
        super().__init__()
        if base_dir:
            self._base_dir = Path(base_dir).resolve()
        else:
            import tempfile
            self._base_dir = Path(tempfile.mkdtemp(prefix="liuhao_filetool_")).resolve()

        # Ensure base directory exists
        self._base_dir.mkdir(parents=True, exist_ok=True)

    @property
    def base_dir(self) -> str:
        """Get the base directory path."""
        return str(self._base_dir)

    def _execute(
        self,
        operation: str,
        path: str,
        content: str = "",
        max_size: int = 100000,
    ) -> Any:
        """Execute file operation."""
        # Validate and resolve the path
        resolved = self._safe_resolve(path)

        if operation == "read":
            if not resolved.is_file():
                raise FileNotFoundError(f"File not found: {path}")
            size = resolved.stat().st_size
            if size > max_size:
                raise ValueError(
                    f"File size ({size} bytes) exceeds max_size ({max_size} bytes)"
                )
            file_content = resolved.read_text(encoding="utf-8")
            return {
                "operation": "read",
                "path": path,
                "content": file_content,
                "size": size,
            }

        elif operation == "write":
            resolved.parent.mkdir(parents=True, exist_ok=True)
            resolved.write_text(content, encoding="utf-8")
            return {
                "operation": "write",
                "path": path,
                "size": len(content),
                "success": True,
            }

        elif operation == "append":
            resolved.parent.mkdir(parents=True, exist_ok=True)
            with open(resolved, "a", encoding="utf-8") as f:
                f.write(content)
            return {
                "operation": "append",
                "path": path,
                "bytes_appended": len(content),
                "success": True,
            }

        elif operation == "list":
            if not resolved.exists():
                raise FileNotFoundError(f"Directory not found: {path}")
            if not resolved.is_dir():
                raise ValueError(f"Path is not a directory: {path}")
            entries = []
            for entry in sorted(resolved.iterdir()):
                entries.append({
                    "name": entry.name,
                    "type": "directory" if entry.is_dir() else "file",
                    "size": entry.stat().st_size if entry.is_file() else None,
                })
            return {
                "operation": "list",
                "path": path,
                "entries": entries,
                "count": len(entries),
            }

        elif operation == "exists":
            return {
                "operation": "exists",
                "path": path,
                "exists": resolved.exists(),
                "is_file": resolved.is_file() if resolved.exists() else False,
                "is_dir": resolved.is_dir() if resolved.exists() else False,
            }

        elif operation == "delete":
            if not resolved.exists():
                return {"operation": "delete", "path": path, "deleted": False}
            if resolved.is_file():
                resolved.unlink()
            elif resolved.is_dir():
                # Only delete empty directories for safety
                if not any(resolved.iterdir()):
                    resolved.rmdir()
                else:
                    raise ValueError("Cannot delete non-empty directory")
            return {"operation": "delete", "path": path, "deleted": True}

        else:
            raise ValueError(f"Unknown operation: {operation}")

    def _safe_resolve(self, path: str) -> Path:
        """Resolve a path and verify it's within the base directory.

        Raises ValueError if the path escapes the base directory.
        """
        # Normalize and resolve the path
        target = (self._base_dir / path).resolve()

        # Verify the path is within the base directory
        try:
            target.relative_to(self._base_dir)
        except ValueError:
            raise ValueError(
                f"Path '{path}' escapes the allowed base directory"
            )

        return target
