"""
Code Formatter Plugin for LiuHao AI OS

Provides code formatting, linting, and complexity analysis
for multiple programming languages.

Capabilities:
- format_code: Format code in various languages
- lint_code: Basic linting and style checking
- analyze_complexity: Code complexity analysis
- detect_language: Detect programming language
"""

import re
from typing import Dict, Any, Optional, List, Tuple
from dataclasses import dataclass, field

from ..plugin_base import (
    BasePlugin,
    PluginMetadata,
    PluginStatus,
    PluginExecutionError,
)


@dataclass
class FormatResult:
    """Result of a code formatting operation."""
    original_code: str
    formatted_code: str
    language: str
    style: str
    changes: int
    original_lines: int
    formatted_lines: int


@dataclass
class LintIssue:
    """A single lint issue."""
    line: int
    column: int
    severity: str  # "error", "warning", "info"
    rule: str
    message: str
    suggestion: Optional[str] = None


@dataclass
class LintResult:
    """Result of a linting operation."""
    code: str
    language: str
    issues: List[LintIssue]
    error_count: int
    warning_count: int
    info_count: int


@dataclass
class ComplexityResult:
    """Result of code complexity analysis."""
    language: str
    total_lines: int
    code_lines: int
    comment_lines: int
    blank_lines: int
    functions: int
    classes: int
    avg_function_length: float
    max_indentation: int
    estimated_complexity: str  # "low", "medium", "high", "very_high"
    complexity_score: float


class CodeFormatterPlugin(BasePlugin):
    """Code formatting plugin with multi-language support.

    Supports Python, JavaScript, Go, Java, C++, and more.
    Provides formatting, linting, and complexity analysis.

    Example:
        plugin = CodeFormatterPlugin()
        plugin.initialize()
        plugin.activate()

        result = plugin.format_code(code, language="python")
        print(result.formatted_code)
    """

    SUPPORTED_LANGUAGES = [
        "python", "javascript", "typescript", "go", "java",
        "c", "cpp", "csharp", "ruby", "php", "rust", "swift",
        "kotlin", "html", "css", "json", "yaml", "xml", "sql",
        "bash", "shell",
    ]

    FORMAT_STYLES = ["standard", "compact", "expanded", "google", "pep8"]

    def __init__(self) -> None:
        """Initialize the code formatter plugin."""
        metadata = PluginMetadata(
            name="code_formatter",
            version="1.0.0",
            description="Code formatting and analysis plugin",
            author="LiuHao AI OS Team",
            categories=["development", "code", "utility"],
            keywords=["format", "lint", "code", "beautify", "complexity"],
            requires_internet=False,
            dependencies=[],
        )
        super().__init__(metadata)

        self._format_count: int = 0

    # ==================== Lifecycle Methods ====================

    def initialize(self, config: Optional[Dict[str, Any]] = None) -> bool:
        """Initialize the code formatter plugin."""
        try:
            self._set_status(PluginStatus.INITIALIZED)
            return True
        except Exception as e:
            self._set_error(f"Initialization failed: {e}")
            return False

    def activate(self) -> bool:
        """Activate the code formatter plugin."""
        try:
            self._set_status(PluginStatus.ACTIVE)
            return True
        except Exception as e:
            self._set_error(f"Activation failed: {e}")
            return False

    def deactivate(self) -> bool:
        """Deactivate the code formatter plugin."""
        try:
            self._set_status(PluginStatus.INACTIVE)
            return True
        except Exception as e:
            self._set_error(str(e))
            return False

    def shutdown(self) -> bool:
        """Shut down the code formatter plugin."""
        try:
            self._set_status(PluginStatus.SHUTDOWN)
            return True
        except Exception as e:
            self._set_error(str(e))
            return False

    def get_capabilities(self) -> List[str]:
        """Return the list of capabilities."""
        return [
            "format_code",
            "lint_code",
            "analyze_complexity",
            "detect_language",
            "get_supported_languages",
        ]

    # ==================== Core Methods ====================

    def format_code(
        self,
        code: str,
        language: Optional[str] = None,
        style: str = "standard",
        indent_size: int = 4,
        max_line_length: int = 100,
    ) -> FormatResult:
        """Format source code.

        Args:
            code: The source code to format.
            language: Programming language. Auto-detected if None.
            style: Formatting style ('standard', 'compact', 'expanded').
            indent_size: Number of spaces per indent level.
            max_line_length: Maximum line length.

        Returns:
            FormatResult with formatted code.

        Raises:
            ValueError: If the language is not supported.
        """
        self._ensure_active()
        self._increment_call_count()
        self._format_count += 1

        if not code:
            return FormatResult(
                original_code=code,
                formatted_code=code,
                language=language or "unknown",
                style=style,
                changes=0,
                original_lines=0,
                formatted_lines=0,
            )

        if language is None:
            language = self._detect_language_internal(code)

        if language not in self.SUPPORTED_LANGUAGES:
            raise ValueError(f"Unsupported language: {language}")

        original_lines = code.count("\n") + 1

        # Apply formatting based on language
        formatted = self._apply_formatting(
            code, language, style, indent_size, max_line_length
        )

        formatted_lines = formatted.count("\n") + 1

        # Count changes (simplified)
        changes = sum(
            1 for a, b in zip(code.splitlines(), formatted.splitlines())
            if a.strip() != b.strip()
        )

        return FormatResult(
            original_code=code,
            formatted_code=formatted,
            language=language,
            style=style,
            changes=changes,
            original_lines=original_lines,
            formatted_lines=formatted_lines,
        )

    def lint_code(
        self,
        code: str,
        language: Optional[str] = None,
        strictness: str = "normal",
    ) -> LintResult:
        """Run basic linting on code.

        Args:
            code: The source code to lint.
            language: Programming language. Auto-detected if None.
            strictness: Lint strictness ('lenient', 'normal', 'strict').

        Returns:
            LintResult with list of issues.
        """
        self._ensure_active()
        self._increment_call_count()

        if not code:
            return LintResult(
                code=code,
                language=language or "unknown",
                issues=[],
                error_count=0,
                warning_count=0,
                info_count=0,
            )

        if language is None:
            language = self._detect_language_internal(code)

        issues: List[LintIssue] = []
        lines = code.splitlines()

        # General checks
        for i, line in enumerate(lines, 1):
            # Line too long
            if len(line) > 100:
                issues.append(LintIssue(
                    line=i,
                    column=101,
                    severity="warning",
                    rule="line-too-long",
                    message=f"Line too long ({len(line)} > 100 characters)",
                ))

            # Trailing whitespace
            if line.rstrip() != line and line.strip():
                issues.append(LintIssue(
                    line=i,
                    column=len(line.rstrip()) + 1,
                    severity="info",
                    rule="trailing-whitespace",
                    message="Trailing whitespace",
                    suggestion=line.rstrip(),
                ))

            # Tab indentation (mixed)
            if line.startswith("\t") and "    " in code:
                issues.append(LintIssue(
                    line=i,
                    column=1,
                    severity="warning",
                    rule="mixed-indentation",
                    message="Mixed indentation (tabs and spaces)",
                ))

        # Language-specific checks
        if language == "python":
            issues.extend(self._lint_python(code, lines, strictness))
        elif language in ("javascript", "typescript"):
            issues.extend(self._lint_javascript(code, lines, strictness))
        elif language == "go":
            issues.extend(self._lint_generic(code, lines, strictness))

        # Sort by line number
        issues.sort(key=lambda x: (x.line, x.column))

        error_count = sum(1 for i in issues if i.severity == "error")
        warning_count = sum(1 for i in issues if i.severity == "warning")
        info_count = sum(1 for i in issues if i.severity == "info")

        return LintResult(
            code=code,
            language=language,
            issues=issues,
            error_count=error_count,
            warning_count=warning_count,
            info_count=info_count,
        )

    def analyze_complexity(
        self,
        code: str,
        language: Optional[str] = None,
    ) -> ComplexityResult:
        """Analyze code complexity.

        Args:
            code: The source code to analyze.
            language: Programming language. Auto-detected if None.

        Returns:
            ComplexityResult with complexity metrics.
        """
        self._ensure_active()
        self._increment_call_count()

        if not code:
            return ComplexityResult(
                language=language or "unknown",
                total_lines=0,
                code_lines=0,
                comment_lines=0,
                blank_lines=0,
                functions=0,
                classes=0,
                avg_function_length=0.0,
                max_indentation=0,
                estimated_complexity="low",
                complexity_score=0.0,
            )

        if language is None:
            language = self._detect_language_internal(code)

        lines = code.splitlines()
        total_lines = len(lines)

        code_lines = 0
        comment_lines = 0
        blank_lines = 0
        max_indent = 0
        functions = 0
        classes = 0
        function_lengths: List[int] = []

        in_comment_block = False
        current_func_lines = 0
        in_function = False

        # Define comment markers by language
        comment_styles = self._get_comment_styles(language)

        for line in lines:
            stripped = line.strip()

            if not stripped:
                blank_lines += 1
                if in_function:
                    current_func_lines += 1
                continue

            # Measure indentation
            indent = len(line) - len(line.lstrip())
            if indent > max_indent:
                max_indent = indent

            # Check for block comments
            is_comment = False
            if comment_styles["block_start"] and comment_styles["block_start"] in stripped:
                in_comment_block = True
                is_comment = True
            if comment_styles["block_end"] and comment_styles["block_end"] in stripped:
                in_comment_block = False
                is_comment = True

            if in_comment_block or stripped.startswith(comment_styles["line"]):
                comment_lines += 1
                continue

            code_lines += 1
            if in_function:
                current_func_lines += 1

            # Detect function definitions
            if language == "python":
                if re.match(r"^def\s+\w+", stripped):
                    functions += 1
                    if in_function and current_func_lines > 0:
                        function_lengths.append(current_func_lines)
                    in_function = True
                    current_func_lines = 1
                if re.match(r"^class\s+\w+", stripped):
                    classes += 1
            elif language in ("javascript", "typescript"):
                if re.match(r"^(function|const\s+\w+\s*=|let\s+\w+\s*=.*=>|class\s+\w+\s*\{)", stripped):
                    if "function" in stripped or "=>" in stripped:
                        functions += 1
                        if in_function and current_func_lines > 0:
                            function_lengths.append(current_func_lines)
                        in_function = True
                        current_func_lines = 1
                    if "class" in stripped:
                        classes += 1
            elif language == "go":
                if re.match(r"^func\s+", stripped):
                    functions += 1
                    if in_function and current_func_lines > 0:
                        function_lengths.append(current_func_lines)
                    in_function = True
                    current_func_lines = 1
            elif language == "java":
                if re.match(r"^\s*(public|private|protected)?\s*(static)?\s*\w+\s+\w+\s*\(", stripped):
                    if "class" not in stripped:
                        functions += 1
                if re.match(r"^.*class\s+\w+", stripped):
                    classes += 1

        # Don't forget the last function
        if in_function and current_func_lines > 0:
            function_lengths.append(current_func_lines)

        avg_func_length = (
            sum(function_lengths) / len(function_lengths)
            if function_lengths else 0.0
        )

        # Calculate complexity score
        indent_level = max_indent / 4  # Assume 4-space indent
        size_factor = min(code_lines / 200, 2.0)
        func_factor = min(functions / 20, 2.0)
        func_len_factor = min(avg_func_length / 50, 2.0)

        complexity_score = (
            indent_level * 0.3 +
            size_factor * 0.25 +
            func_factor * 0.2 +
            func_len_factor * 0.25
        )

        if complexity_score < 0.5:
            estimated = "low"
        elif complexity_score < 1.0:
            estimated = "medium"
        elif complexity_score < 1.5:
            estimated = "high"
        else:
            estimated = "very_high"

        return ComplexityResult(
            language=language,
            total_lines=total_lines,
            code_lines=code_lines,
            comment_lines=comment_lines,
            blank_lines=blank_lines,
            functions=functions,
            classes=classes,
            avg_function_length=round(avg_func_length, 1),
            max_indentation=max_indent,
            estimated_complexity=estimated,
            complexity_score=round(complexity_score, 2),
        )

    def detect_language(self, code: str) -> str:
        """Detect the programming language of code.

        Args:
            code: The source code to analyze.

        Returns:
            Detected language name.
        """
        self._ensure_active()
        self._increment_call_count()
        return self._detect_language_internal(code)

    def get_supported_languages(self) -> List[str]:
        """Get list of supported languages."""
        return list(self.SUPPORTED_LANGUAGES)

    # ==================== Internal Helpers ====================

    def _detect_language_internal(self, code: str) -> str:
        """Detect programming language using heuristics."""
        scores: Dict[str, float] = {}

        # Python indicators
        if re.search(r"def\s+\w+\s*\(.*\):", code):
            scores["python"] = scores.get("python", 0) + 3
        if re.search(r"import\s+\w+(\s+as\s+\w+)?|from\s+\w+\s+import", code):
            scores["python"] = scores.get("python", 0) + 2
        if re.search(r"print\(", code):
            scores["python"] = scores.get("python", 0) + 1
        if "__name__" in code and "__main__" in code:
            scores["python"] = scores.get("python", 0) + 3

        # JavaScript indicators
        if re.search(r"function\s+\w+|const\s+\w+\s*=|let\s+\w+\s*=", code):
            scores["javascript"] = scores.get("javascript", 0) + 2
        if re.search(r"console\.log|console\.error", code):
            scores["javascript"] = scores.get("javascript", 0) + 2
        if "=>" in code:
            scores["javascript"] = scores.get("javascript", 0) + 1.5
        if re.search(r"require\(|module\.exports", code):
            scores["javascript"] = scores.get("javascript", 0) + 2

        # TypeScript indicators
        if re.search(r":\s*(string|number|boolean|any|void)\b", code):
            scores["typescript"] = scores.get("typescript", 0) + 2
        if "interface " in code or "type " in code:
            scores["typescript"] = scores.get("typescript", 0) + 2

        # Go indicators
        if re.search(r"^func\s+", code, re.MULTILINE):
            scores["go"] = scores.get("go", 0) + 3
        if re.search(r"^package\s+\w+", code, re.MULTILINE):
            scores["go"] = scores.get("go", 0) + 2
        if "fmt.Println" in code or "fmt.Printf" in code:
            scores["go"] = scores.get("go", 0) + 2

        # Java indicators
        if re.search(r"public\s+(class|static|void|int|String)", code):
            scores["java"] = scores.get("java", 0) + 2
        if re.search(r"System\.out\.print", code):
            scores["java"] = scores.get("java", 0) + 2
        if "import java." in code:
            scores["java"] = scores.get("java", 0) + 2

        # C/C++ indicators
        if re.search(r"#include\s*[<\"]", code):
            scores["cpp"] = scores.get("cpp", 0) + 2
        if re.search(r"int\s+main\s*\(", code):
            scores["cpp"] = scores.get("cpp", 0) + 3
        if "printf" in code or "scanf" in code:
            scores["c"] = scores.get("c", 0) + 2

        # Ruby indicators
        if re.search(r"def\s+\w+(\s*\(.*\))?\s*$", code, re.MULTILINE):
            if "end" in code:
                scores["ruby"] = scores.get("ruby", 0) + 2
        if "puts " in code or "print " in code:
            scores["ruby"] = scores.get("ruby", 0) + 1

        # HTML indicators
        if re.search(r"<(!DOCTYPE|html|head|body|div|span|p\b)", code, re.I):
            scores["html"] = scores.get("html", 0) + 3

        # CSS indicators
        if re.search(r"[\w-]+\s*\{[^}]*\}", code) and "color" in code:
            scores["css"] = scores.get("css", 0) + 2

        # SQL indicators
        if re.search(r"(SELECT|INSERT|UPDATE|DELETE|CREATE TABLE)\s+", code, re.I):
            scores["sql"] = scores.get("sql", 0) + 3

        # Shell/bash indicators
        if code.startswith("#!") and ("bash" in code or "sh" in code):
            scores["bash"] = scores.get("bash", 0) + 3
        if re.search(r"^#!/", code):
            scores["shell"] = scores.get("shell", 0) + 2

        if not scores:
            return "unknown"

        return max(scores, key=scores.get)

    def _apply_formatting(
        self,
        code: str,
        language: str,
        style: str,
        indent_size: int,
        max_line_length: int,
    ) -> str:
        """Apply code formatting (simplified implementation)."""
        lines = code.splitlines()
        result: List[str] = []

        # Basic normalization: trailing whitespace
        for line in lines:
            cleaned = line.rstrip()
            result.append(cleaned)

        # Ensure trailing newline
        formatted = "\n".join(result)
        if code.endswith("\n") and not formatted.endswith("\n"):
            formatted += "\n"

        # Language-specific improvements
        if language == "python":
            formatted = self._format_python(formatted, style, indent_size)

        return formatted

    def _format_python(self, code: str, style: str, indent_size: int) -> str:
        """Basic Python formatting."""
        lines = code.splitlines()
        result: List[str] = []
        current_indent = 0
        indent_str = " " * indent_size

        for line in lines:
            stripped = line.strip()
            if not stripped:
                result.append("")
                continue

            # Decrease indent for closing blocks
            if stripped.startswith(")") or stripped.startswith("]") or stripped.startswith("}"):
                current_indent = max(0, current_indent - 1)
            elif stripped.startswith("except ") or stripped == "except:" or stripped == "finally:":
                current_indent = max(0, current_indent - 1)
            elif stripped.startswith("elif ") or stripped.startswith("else:"):
                current_indent = max(0, current_indent - 1)

            result.append(indent_str * current_indent + stripped)

            # Increase indent for opening blocks
            if stripped.endswith(":") and not stripped.startswith("#"):
                if not stripped.startswith("def ") and stripped.count(":") == 1:
                    pass  # Already handled
                current_indent += 1
            elif stripped.endswith("(") and not stripped.startswith("def "):
                # Might be a multi-line call
                pass

        return "\n".join(result)

    def _get_comment_styles(self, language: str) -> Dict[str, str]:
        """Get comment syntax for a language."""
        styles = {
            "python": {"line": "#", "block_start": '"""', "block_end": '"""'},
            "javascript": {"line": "//", "block_start": "/*", "block_end": "*/"},
            "typescript": {"line": "//", "block_start": "/*", "block_end": "*/"},
            "go": {"line": "//", "block_start": "/*", "block_end": "*/"},
            "java": {"line": "//", "block_start": "/*", "block_end": "*/"},
            "c": {"line": "//", "block_start": "/*", "block_end": "*/"},
            "cpp": {"line": "//", "block_start": "/*", "block_end": "*/"},
            "ruby": {"line": "#", "block_start": "=begin", "block_end": "=end"},
            "html": {"line": "", "block_start": "<!--", "block_end": "-->"},
            "css": {"line": "", "block_start": "/*", "block_end": "*/"},
            "sql": {"line": "--", "block_start": "/*", "block_end": "*/"},
            "bash": {"line": "#", "block_start": "", "block_end": ""},
            "shell": {"line": "#", "block_start": "", "block_end": ""},
        }
        return styles.get(language, {"line": "//", "block_start": "/*", "block_end": "*/"})

    def _lint_python(self, code: str, lines: List[str], strictness: str) -> List[LintIssue]:
        """Python-specific lint checks."""
        issues: List[LintIssue] = []

        for i, line in enumerate(lines, 1):
            stripped = line.strip()

            # Check for mixed tabs and spaces
            if line.startswith(" ") and "\t" in line[:len(line) - len(line.lstrip())]:
                issues.append(LintIssue(
                    line=i, column=1, severity="error",
                    rule="E101", message="Mixed tabs and spaces in indentation",
                ))

            # Check indentation is multiple of 4
            indent = len(line) - len(line.lstrip())
            if indent > 0 and indent % 4 != 0 and stripped:
                issues.append(LintIssue(
                    line=i, column=1, severity="warning",
                    rule="W191", message=f"Indentation is {indent} spaces (not multiple of 4)",
                ))

            # Check for bare except
            if re.match(r"except\s*:", stripped):
                issues.append(LintIssue(
                    line=i, column=1, severity="warning",
                    rule="E722", message="Do not use bare except, specify exception type",
                ))

            # Check for import order (simplified)
            if strictness == "strict" and stripped.startswith("import ") and i > 1:
                prev = lines[i - 2].strip() if i > 1 else ""
                if prev.startswith("import "):
                    curr_module = stripped.split()[1].split(".")[0]
                    prev_module = prev.split()[1].split(".")[0]
                    if curr_module < prev_module:
                        issues.append(LintIssue(
                            line=i, column=1, severity="info",
                            rule="I100", message="Import statements are not in alphabetical order",
                        ))

        return issues

    def _lint_javascript(self, code: str, lines: List[str], strictness: str) -> List[LintIssue]:
        """JavaScript-specific lint checks."""
        issues: List[LintIssue] = []

        for i, line in enumerate(lines, 1):
            stripped = line.strip()

            # Check for console.log in production
            if "console.log" in stripped and strictness == "strict":
                issues.append(LintIssue(
                    line=i, column=stripped.find("console.log") + 1,
                    severity="warning",
                    rule="no-console",
                    message="Unexpected console.log statement",
                ))

            # Check for var (prefer let/const)
            if re.match(r"^var\s+", stripped):
                issues.append(LintIssue(
                    line=i, column=1, severity="warning",
                    rule="no-var", message="Use let or const instead of var",
                ))

            # Check for == (prefer ===)
            if " == " in stripped or " != " in stripped:
                issues.append(LintIssue(
                    line=i, column=1, severity="warning",
                    rule="eqeqeq", message="Use === and !== instead of == and !=",
                ))

        return issues

    def _lint_generic(self, code: str, lines: List[str], strictness: str) -> List[LintIssue]:
        """Generic lint checks."""
        issues: List[LintIssue] = []

        # Check for TODO/FIXME comments
        for i, line in enumerate(lines, 1):
            if "TODO" in line:
                issues.append(LintIssue(
                    line=i, column=line.find("TODO") + 1,
                    severity="info",
                    rule="todo-comment",
                    message="TODO comment found",
                ))
            if "FIXME" in line:
                issues.append(LintIssue(
                    line=i, column=line.find("FIXME") + 1,
                    severity="warning",
                    rule="fixme-comment",
                    message="FIXME comment found",
                ))

        return issues
