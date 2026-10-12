"""
Input sanitization and validation for LiuHao AI OS.

Provides SQL injection prevention, XSS prevention, command injection
prevention, path traversal prevention, input validation (type, length,
format), output encoding, file upload security checks, and URL validation.
"""

from __future__ import annotations

import html
import ipaddress
import re
import urllib.parse
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------


class ValidationStatus(str, Enum):
    """Status of input validation."""

    VALID = "valid"
    INVALID = "invalid"
    SUSPICIOUS = "suspicious"
    BLOCKED = "blocked"


class SanitizationLevel(str, Enum):
    """Levels of sanitization strictness."""

    STRICT = "strict"  # Remove all potentially dangerous content
    MODERATE = "moderate"  # Neutralize common attacks
    LENIENT = "lenient"  # Only encode for output


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------


@dataclass
class ValidationResult:
    """Result of input validation."""

    is_valid: bool
    status: ValidationStatus
    sanitized_value: Any = None
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    original_length: int = 0
    sanitized_length: int = 0

    def __bool__(self) -> bool:
        return self.is_valid


@dataclass
class FileUploadCheckResult:
    """Result of a file upload security check."""

    is_safe: bool
    file_type: Optional[str] = None
    mime_type: Optional[str] = None
    file_size: int = 0
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    detected_extensions: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# SQL Injection Prevention
# ---------------------------------------------------------------------------


class SqlInjectionGuard:
    """Detects and prevents SQL injection attacks.

    Note: The primary defense against SQL injection should always be
    parameterized queries. This class provides an additional layer of
    detection for monitoring and defense-in-depth.
    """

    # Patterns commonly found in SQL injection attacks
    _INJECTION_PATTERNS: list[tuple[str, str]] = [
        ("sql_comment_single", r"--"),
        ("sql_comment_multi", r"/\*.*?\*/"),
        ("sql_union", r"(?i)\bunion\b.*?\bselect\b"),
        ("sql_drop", r"(?i)\bdrop\s+(table|database|index|view)\b"),
        ("sql_delete", r"(?i)\bdelete\s+from\b"),
        ("sql_insert", r"(?i)\binsert\s+into\b"),
        ("sql_update", r"(?i)\bupdate\b.*?\bset\b"),
        ("sql_alter", r"(?i)\balter\s+(table|database|column)\b"),
        ("sql_create", r"(?i)\bcreate\s+(table|database|index|view|user)\b"),
        ("sql_truncate", r"(?i)\btruncate\s+table\b"),
        ("sql_or_1", r"(?i)\bor\s+['\"]?\d+['\"]?\s*=\s*['\"]?\d+"),
        ("sql_and_1", r"(?i)\band\s+['\"]?\d+['\"]?\s*=\s*['\"]?\d+"),
        ("sql_tautology", r"(?i)['\"]\s*(or|and)\s*['\"].*?['\"]\s*=\s*['\"]"),
        ("sql_semicolon", r";\s*(select|drop|delete|insert|update|alter|create|truncate|exec|execute)"),
        ("sql_exec", r"(?i)\b(exec|execute|sp_|xp_)\w+"),
        ("sql_into_outfile", r"(?i)\binto\s+(outfile|dumpfile)\b"),
        ("sql_load_file", r"(?i)\bload_file\s*\("),
        ("sql_benchmark", r"(?i)\bbenchmark\s*\("),
        ("sql_sleep", r"(?i)\bsleep\s*\("),
        ("sql_waitfor", r"(?i)\bwaitfor\s+delay\b"),
        ("sql_blind", r"(?i)\b(case|if)\s*\(.*?\s*then\s*\d+"),
    ]

    def __init__(self) -> None:
        self._compiled_patterns = [
            (name, re.compile(pattern, re.DOTALL | re.IGNORECASE))
            for name, pattern in self._INJECTION_PATTERNS
        ]

    def detect(self, input_str: str) -> list[str]:
        """Detect SQL injection patterns in input.

        Returns a list of detected pattern names.
        """
        if not input_str:
            return []

        detected = []
        for name, pattern in self._compiled_patterns:
            if pattern.search(input_str):
                detected.append(name)
        return detected

    def is_suspicious(self, input_str: str) -> bool:
        """Check if input contains suspicious SQL injection patterns."""
        return len(self.detect(input_str)) > 0

    def sanitize(self, input_str: str) -> str:
        """Sanitize input by removing common SQL injection patterns.

        This is NOT a substitute for parameterized queries.
        Use only as a defense-in-depth measure.
        """
        if not input_str:
            return input_str

        sanitized = input_str
        # Remove SQL comments
        sanitized = re.sub(r"/\*.*?\*/", "", sanitized, flags=re.DOTALL)
        sanitized = re.sub(r"--.*?$", "", sanitized, flags=re.MULTILINE)
        # Escape single quotes by doubling (works in some SQL dialects)
        sanitized = sanitized.replace("'", "''")
        # Remove semicolons at end
        sanitized = sanitized.rstrip(";")
        return sanitized

    def validate_identifier(self, identifier: str) -> ValidationResult:
        """Validate a SQL identifier (table name, column name, etc.).

        SQL identifiers should only contain alphanumeric characters
        and underscores. This is critical because identifiers cannot
        be parameterized in most SQL databases.
        """
        errors: list[str] = []

        if not identifier:
            errors.append("Identifier cannot be empty")
            return ValidationResult(
                is_valid=False,
                status=ValidationStatus.INVALID,
                sanitized_value=identifier,
                errors=errors,
            )

        if len(identifier) > 128:
            errors.append("Identifier too long (max 128 characters)")

        if not re.match(r"^[a-zA-Z_][a-zA-Z0-9_]*$", identifier):
            errors.append(
                "Identifier must start with a letter or underscore and "
                "contain only alphanumeric characters and underscores"
            )

        # Check for SQL keywords that shouldn't be used as identifiers
        dangerous_keywords = {
            "select", "from", "where", "drop", "delete", "insert", "update",
            "alter", "create", "truncate", "exec", "execute", "union",
            "table", "database", "user", "admin", "root",
        }
        if identifier.lower() in dangerous_keywords:
            errors.append(f"Identifier '{identifier}' is a reserved keyword")

        if self.is_suspicious(identifier):
            errors.append("Identifier contains suspicious SQL patterns")

        is_valid = len(errors) == 0
        return ValidationResult(
            is_valid=is_valid,
            status=ValidationStatus.VALID if is_valid else ValidationStatus.INVALID,
            sanitized_value=identifier,
            errors=errors,
            original_length=len(identifier),
            sanitized_length=len(identifier),
        )


# ---------------------------------------------------------------------------
# XSS Prevention
# ---------------------------------------------------------------------------


class XssGuard:
    """Cross-Site Scripting prevention utilities.

    Provides input sanitization and output encoding for preventing
    XSS attacks in web applications.
    """

    # Dangerous HTML tags that can execute scripts
    _DANGEROUS_TAGS = {
        "script", "iframe", "object", "embed", "form", "input", "button",
        "textarea", "select", "option", "link", "style", "meta", "base",
        "svg", "math", "video", "audio", "img",  # img can have onload
    }

    # Dangerous event handler attributes
    _DANGEROUS_ATTRIBUTES = {
        "onload", "onerror", "onclick", "onmouseover", "onmouseout",
        "onfocus", "onblur", "onchange", "onsubmit", "onkeydown",
        "onkeypress", "onkeyup", "ondblclick", "onmousedown", "onmouseup",
        "onmousemove", "oncontextmenu", "onwheel", "onscroll",
        "onabort", "oncanplay", "onended", "onpause", "onplay",
        "onplaying", "onprogress", "onratechange", "onseeked",
        "onstalled", "onsuspend", "ontimeupdate", "onvolumechange",
        "onwaiting", "ontoggle", "onshow", "oninvalid", "onreset",
        "oninput", "onsearch", "ondrag", "ondragend", "ondragenter",
        "ondragleave", "ondragover", "ondragstart", "ondrop",
        "oncopy", "oncut", "onpaste", "onbeforecopy", "onbeforecut",
        "onbeforepaste", "onhashchange", "onpopstate", "onstorage",
    }

    # Patterns for javascript: URIs
    _JS_URI_PATTERN = re.compile(
        r"(?i)(javascript|vbscript|data):.*", re.IGNORECASE
    )

    # Pattern for HTML tags
    _TAG_PATTERN = re.compile(r"<(/?)(\w+)([^>]*)>", re.DOTALL)

    def detect(self, input_str: str) -> list[str]:
        """Detect potential XSS patterns in input.

        Returns a list of detected issue types.
        """
        if not input_str:
            return []

        detected = []
        lowered = input_str.lower()

        # Check for script tags
        if re.search(r"<\s*script[^>]*>", lowered, re.IGNORECASE):
            detected.append("script_tag")

        # Check for event handlers
        for attr in self._DANGEROUS_ATTRIBUTES:
            if re.search(rf"\b{attr}\s*=", lowered, re.IGNORECASE):
                detected.append(f"event_handler_{attr}")
                break  # Only report one event handler detection

        # Check for javascript: URIs
        if self._JS_URI_PATTERN.search(lowered):
            detected.append("javascript_uri")

        # Check for HTML comments that could hide scripts
        if "<!--" in lowered and "-->" in lowered:
            detected.append("html_comment")

        # Check for expression() CSS (old IE XSS vector)
        if re.search(r"expression\s*\(", lowered, re.IGNORECASE):
            detected.append("css_expression")

        return detected

    def is_suspicious(self, input_str: str) -> bool:
        """Check if input contains suspicious XSS patterns."""
        return len(self.detect(input_str)) > 0

    def sanitize_html(
        self,
        input_str: str,
        level: SanitizationLevel = SanitizationLevel.STRICT,
    ) -> str:
        """Sanitize HTML content to prevent XSS.

        Args:
            input_str: Input HTML string.
            level: Sanitization strictness level.

        Returns:
            Sanitized string.
        """
        if not input_str:
            return input_str

        if level == SanitizationLevel.STRICT:
            # Strip all HTML tags
            return self._strip_all_tags(input_str)

        if level == SanitizationLevel.MODERATE:
            # Remove dangerous tags and attributes, keep safe ones
            return self._remove_dangerous_tags(input_str)

        # LENIENT: just encode everything
        return html.escape(input_str)

    def _strip_all_tags(self, input_str: str) -> str:
        """Remove all HTML tags from input."""
        # Remove script and style blocks completely (with content)
        result = re.sub(
            r"<\s*(script|style)[^>]*>.*?<\s*/\s*\1\s*>",
            "",
            input_str,
            flags=re.DOTALL | re.IGNORECASE,
        )
        # Remove all remaining tags
        result = re.sub(r"<[^>]*>", "", result)
        return result

    def _remove_dangerous_tags(self, input_str: str) -> str:
        """Remove only dangerous HTML tags, keep safe ones."""

        def replace_tag(match: re.Match[str]) -> str:
            closing = match.group(1)
            tag_name = match.group(2).lower()
            attrs = match.group(3)

            if tag_name in self._DANGEROUS_TAGS:
                return ""

            # Remove dangerous attributes
            cleaned_attrs = self._clean_attributes(attrs)
            return f"<{closing}{tag_name}{cleaned_attrs}>"

        result = self._TAG_PATTERN.sub(replace_tag, input_str)
        return result

    def _clean_attributes(self, attrs: str) -> str:
        """Remove dangerous attributes from an HTML tag's attribute string."""
        # Remove event handlers
        for attr in self._DANGEROUS_ATTRIBUTES:
            attrs = re.sub(
                rf"\s+{attr}\s*=\s*(['\"]).*?\1",
                "",
                attrs,
                flags=re.IGNORECASE | re.DOTALL,
            )
            attrs = re.sub(
                rf"\s+{attr}\s*=\s*[^'\"\s]+",
                "",
                attrs,
                flags=re.IGNORECASE,
            )

        # Remove javascript: URIs in href/src/etc
        attrs = re.sub(
            r"(href|src|action|data|formaction)\s*=\s*(['\"])javascript:.*?\2",
            r"\1=\2#\2",
            attrs,
            flags=re.IGNORECASE,
        )

        return attrs

    def encode_for_html(self, input_str: str) -> str:
        """Encode a string for safe inclusion in HTML content.

        This is the primary defense against XSS for data displayed
        in HTML context.
        """
        return html.escape(input_str, quote=True)

    def encode_for_attribute(self, input_str: str) -> str:
        """Encode a string for safe inclusion in HTML attributes.

        More aggressive than HTML content encoding.
        """
        return html.escape(input_str, quote=True)

    def encode_for_javascript(self, input_str: str) -> str:
        """Encode a string for safe inclusion in JavaScript string context.

        Encodes non-alphanumeric characters as \\xHH or \\uHHHH.
        """
        result = []
        for char in input_str:
            code = ord(char)
            if char.isalnum() or char in ("_", "-", "."):
                result.append(char)
            elif code < 256:
                result.append(f"\\x{code:02x}")
            else:
                result.append(f"\\u{code:04x}")
        return "".join(result)

    def encode_for_url(self, input_str: str) -> str:
        """Encode a string for safe inclusion in URL parameters."""
        return urllib.parse.quote(input_str, safe="")


# ---------------------------------------------------------------------------
# Command Injection Prevention
# ---------------------------------------------------------------------------


class CommandInjectionGuard:
    """Detects and prevents command injection attacks.

    Provides validation for strings that will be used in shell commands
    or subprocess calls.
    """

    # Dangerous shell metacharacters
    _DANGEROUS_CHARS = set(";&|`$<>(){}[]\\'\" \t\n\r")

    # Command injection patterns
    _INJECTION_PATTERNS = [
        r";\s*\w+",  # ; command
        r"\|\s*\w+",  # | pipe
        r"&\s*\w+",  # & background/chain
        r"`[^`]*`",  # backtick substitution
        r"\$\([^)]*\)",  # $() substitution
        r"\$\{[^}]*\}",  # ${} variable
        r"\n\s*\w+",  # newline command
        r"\r\s*\w+",  # carriage return command
        r"\|\|\s*\w+",  # || OR
        r"&&\s*\w+",  # && AND
    ]

    def __init__(self) -> None:
        self._compiled_patterns = [re.compile(p, re.IGNORECASE) for p in self._INJECTION_PATTERNS]

    def detect(self, input_str: str) -> list[str]:
        """Detect command injection patterns.

        Returns a list of detected pattern descriptions.
        """
        if not input_str:
            return []

        detected = []
        for pattern in self._compiled_patterns:
            if pattern.search(input_str):
                detected.append(pattern.pattern)
        return detected

    def is_suspicious(self, input_str: str) -> bool:
        """Check if input contains suspicious command injection patterns."""
        return len(self.detect(input_str)) > 0

    def is_safe_filename(self, filename: str) -> bool:
        """Check if a string is safe to use as a filename in commands.

        Safe filenames contain only alphanumeric chars, underscores,
        hyphens, and dots. No path separators or special characters.
        """
        if not filename:
            return False
        # Block path traversal
        if ".." in filename or "/" in filename or "\\" in filename:
            return False
        # Block null bytes
        if "\x00" in filename:
            return False
        # Allow only safe characters
        return bool(re.match(r"^[a-zA-Z0-9._-]+$", filename))

    def sanitize_filename(self, filename: str) -> str:
        """Sanitize a filename by removing dangerous characters."""
        if not filename:
            return "unnamed"

        # Remove path components
        filename = filename.replace("/", "_").replace("\\", "_")
        # Remove dangerous characters
        filename = re.sub(r"[^\w.\-]", "_", filename)
        # Remove leading dots (hidden files / path traversal)
        filename = filename.lstrip(".")
        # Limit length
        if len(filename) > 255:
            name, _, ext = filename.rpartition(".")
            if ext and len(ext) <= 10:
                filename = name[: 255 - len(ext) - 1] + "." + ext
            else:
                filename = filename[:255]
        return filename or "unnamed"

    def validate_shell_argument(self, arg: str) -> ValidationResult:
        """Validate that a string is safe to use as a shell argument.

        This is extremely strict - shell arguments should never be
        constructed from user input. Use subprocess with a list of
        arguments instead.
        """
        errors: list[str] = []
        warnings: list[str] = []

        if not arg:
            return ValidationResult(
                is_valid=True,
                status=ValidationStatus.VALID,
                sanitized_value=arg,
            )

        if self.is_suspicious(arg):
            errors.append("Argument contains suspicious shell metacharacters")

        # Check for dangerous characters
        for char in self._DANGEROUS_CHARS:
            if char in arg:
                errors.append(f"Argument contains dangerous character: {repr(char)}")
                break

        # Check for shell builtins/commands
        dangerous_commands = {
            "rm", "cp", "mv", "dd", "chmod", "chown", "wget", "curl",
            "bash", "sh", "zsh", "nc", "netcat", "cat", "echo",
        }
        if arg.strip().lower() in dangerous_commands:
            errors.append("Argument looks like a dangerous command")

        is_valid = len(errors) == 0
        return ValidationResult(
            is_valid=is_valid,
            status=ValidationStatus.VALID if is_valid else ValidationStatus.BLOCKED,
            sanitized_value=arg,
            errors=errors,
            warnings=warnings,
            original_length=len(arg),
            sanitized_length=len(arg),
        )


# ---------------------------------------------------------------------------
# Path Traversal Prevention
# ---------------------------------------------------------------------------


class PathTraversalGuard:
    """Detects and prevents path traversal attacks."""

    _TRAVERSAL_PATTERNS = [
        r"\.\./",  # Unix style
        r"\.\.\\",  # Windows style
        r"%2e%2e%2f",  # URL encoded
        r"%2e%2e/",  # Mixed encoding
        r"\.\.%2f",  # Mixed encoding
        r"%2e%2e%5c",  # URL encoded backslash
        r"....//",  # Double traversal
        r"...\\.\\.",  # Obfuscated
        r"/\.\./",  # Inline traversal
    ]

    def __init__(self) -> None:
        self._compiled_patterns = [re.compile(p, re.IGNORECASE) for p in self._TRAVERSAL_PATTERNS]

    def detect(self, path: str) -> list[str]:
        """Detect path traversal patterns."""
        if not path:
            return []

        detected = []
        for pattern in self._compiled_patterns:
            if pattern.search(path):
                detected.append(pattern.pattern)
        return detected

    def is_suspicious(self, path: str) -> bool:
        """Check if a path contains traversal patterns."""
        return len(self.detect(path)) > 0

    def sanitize(self, path: str) -> str:
        """Sanitize a path by removing traversal sequences."""
        if not path:
            return path

        # Decode URL encoding first
        decoded = urllib.parse.unquote(path)

        # Remove traversal sequences
        sanitized = decoded
        prev = None
        while prev != sanitized:
            prev = sanitized
            # Remove ../ and ..\
            sanitized = re.sub(r"\.\.[/\\]", "", sanitized)
            # Remove /../ variants
            sanitized = re.sub(r"[/\\]\.\.[/\\]?", "/", sanitized)

        # Normalize path separators
        sanitized = sanitized.replace("\\", "/")

        # Remove leading slashes (make relative)
        sanitized = sanitized.lstrip("/")

        return sanitized

    def safe_join(self, base_dir: str, *paths: str) -> Optional[str]:
        """Safely join paths ensuring the result stays within base_dir.

        Returns the joined path if safe, None if it would escape.
        """
        import os

        # Join all paths
        joined = base_dir
        for p in paths:
            joined = os.path.join(joined, p)

        # Normalize the path
        normalized = os.path.normpath(joined)
        normalized_base = os.path.normpath(base_dir)

        # Ensure the result is within base_dir
        # Use case-insensitive comparison on Windows
        if os.name == "nt":
            if not normalized.lower().startswith(normalized_base.lower() + os.sep) and normalized.lower() != normalized_base.lower():
                return None
        else:
            if not normalized.startswith(normalized_base + os.sep) and normalized != normalized_base:
                return None

        return normalized

    def validate_path(self, path: str, base_dir: Optional[str] = None) -> ValidationResult:
        """Validate a file path for safety."""
        errors: list[str] = []
        warnings: list[str] = []

        if not path:
            errors.append("Path cannot be empty")
            return ValidationResult(
                is_valid=False,
                status=ValidationStatus.INVALID,
                sanitized_value=path,
                errors=errors,
            )

        # Check for null bytes
        if "\x00" in path:
            errors.append("Path contains null bytes")

        # Check for traversal patterns
        if self.is_suspicious(path):
            warnings.append("Path contains traversal sequences")

        # Check absolute path
        import os

        if os.path.isabs(path):
            warnings.append("Path is absolute")

        # Check against base directory
        if base_dir:
            safe = self.safe_join(base_dir, path)
            if safe is None:
                errors.append("Path escapes base directory")

        is_valid = len(errors) == 0
        status = ValidationStatus.VALID
        if warnings:
            status = ValidationStatus.SUSPICIOUS
        if not is_valid:
            status = ValidationStatus.BLOCKED

        return ValidationResult(
            is_valid=is_valid,
            status=status,
            sanitized_value=self.sanitize(path),
            errors=errors,
            warnings=warnings,
            original_length=len(path),
            sanitized_length=len(self.sanitize(path)),
        )


# ---------------------------------------------------------------------------
# Input Validation
# ---------------------------------------------------------------------------


class InputValidator:
    """Comprehensive input validation utilities."""

    _EMAIL_PATTERN = re.compile(
        r"^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$"
    )
    _URL_PATTERN = re.compile(
        r"^https?://"  # scheme
        r"([a-zA-Z0-9]([a-zA-Z0-9-]*[a-zA-Z0-9])?\.)+"  # domain
        r"[a-zA-Z]{2,}"  # TLD
        r"(:\d{1,5})?"  # port
        r"(/[^\s]*)?"  # path
        r"$",
        re.IGNORECASE,
    )
    _USERNAME_PATTERN = re.compile(r"^[a-zA-Z0-9_-]{3,64}$")
    _UUID_PATTERN = re.compile(
        r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$",
        re.IGNORECASE,
    )
    _IPV4_PATTERN = re.compile(r"^(?:\d{1,3}\.){3}\d{1,3}$")
    _IPV6_PATTERN = re.compile(r"^[0-9a-fA-F:]+$")
    _PHONE_PATTERN = re.compile(r"^\+?[0-9\s\-()]{7,20}$")

    def __init__(self) -> None:
        self.sql_guard = SqlInjectionGuard()
        self.xss_guard = XssGuard()
        self.command_guard = CommandInjectionGuard()
        self.path_guard = PathTraversalGuard()

    def validate_string(
        self,
        value: str,
        min_length: int = 0,
        max_length: int = 10000,
        allowed_chars: Optional[str] = None,
        pattern: Optional[str] = None,
        check_xss: bool = True,
        check_sql: bool = False,
    ) -> ValidationResult:
        """Validate a string input.

        Args:
            value: The string to validate.
            min_length: Minimum length.
            max_length: Maximum length.
            allowed_chars: If set, string must only contain these characters.
            pattern: If set, string must match this regex pattern.
            check_xss: Whether to check for XSS patterns.
            check_sql: Whether to check for SQL injection patterns.

        Returns:
            ValidationResult.
        """
        errors: list[str] = []
        warnings: list[str] = []

        if not isinstance(value, str):
            errors.append(f"Expected string, got {type(value).__name__}")
            return ValidationResult(
                is_valid=False,
                status=ValidationStatus.INVALID,
                errors=errors,
            )

        original_length = len(value)

        # Length checks
        if len(value) < min_length:
            errors.append(f"String too short: minimum {min_length} characters")
        if len(value) > max_length:
            errors.append(f"String too long: maximum {max_length} characters")

        # Character set check
        if allowed_chars:
            for char in value:
                if char not in allowed_chars:
                    errors.append(f"String contains disallowed character: {repr(char)}")
                    break

        # Pattern check
        if pattern and not re.match(pattern, value):
            errors.append("String does not match required pattern")

        # Security checks
        if check_xss and self.xss_guard.is_suspicious(value):
            warnings.append("String contains potential XSS patterns")

        if check_sql and self.sql_guard.is_suspicious(value):
            warnings.append("String contains potential SQL injection patterns")

        is_valid = len(errors) == 0
        status = ValidationStatus.VALID
        if warnings:
            status = ValidationStatus.SUSPICIOUS
        if not is_valid:
            status = ValidationStatus.INVALID

        return ValidationResult(
            is_valid=is_valid,
            status=status,
            sanitized_value=value,
            errors=errors,
            warnings=warnings,
            original_length=original_length,
            sanitized_length=len(value),
        )

    def validate_email(self, email: str) -> ValidationResult:
        """Validate an email address."""
        result = self.validate_string(email, min_length=3, max_length=254, check_xss=True)
        if not result.is_valid:
            return result

        if not self._EMAIL_PATTERN.match(email):
            result.is_valid = False
            result.status = ValidationStatus.INVALID
            result.errors.append("Invalid email format")

        return result

    def validate_url(self, url: str, allowed_schemes: Optional[list[str]] = None) -> ValidationResult:
        """Validate a URL.

        Args:
            url: The URL to validate.
            allowed_schemes: List of allowed URL schemes (default: ['http', 'https']).
        """
        result = self.validate_string(url, min_length=1, max_length=2048, check_xss=True)
        if not result.is_valid:
            return result

        allowed = allowed_schemes or ["http", "https"]

        try:
            parsed = urllib.parse.urlparse(url)
            if parsed.scheme not in allowed:
                result.errors.append(f"URL scheme '{parsed.scheme}' not allowed")
                result.is_valid = False
                result.status = ValidationStatus.INVALID
            if not parsed.netloc:
                result.errors.append("URL is missing host")
                result.is_valid = False
                result.status = ValidationStatus.INVALID
            # Check for javascript: scheme (bypassed urlparse)
            if url.lower().startswith("javascript:"):
                result.errors.append("javascript: URLs are not allowed")
                result.is_valid = False
                result.status = ValidationStatus.BLOCKED
        except Exception:
            result.errors.append("Invalid URL format")
            result.is_valid = False
            result.status = ValidationStatus.INVALID

        return result

    def validate_username(self, username: str) -> ValidationResult:
        """Validate a username."""
        result = self.validate_string(username, min_length=3, max_length=64, check_xss=True)
        if not result.is_valid:
            return result

        if not self._USERNAME_PATTERN.match(username):
            result.is_valid = False
            result.status = ValidationStatus.INVALID
            result.errors.append(
                "Username must be 3-64 characters and contain only "
                "letters, numbers, underscores, and hyphens"
            )

        return result

    def validate_ip_address(self, ip: str) -> ValidationResult:
        """Validate an IP address (IPv4 or IPv6)."""
        result = self.validate_string(ip, min_length=1, max_length=45)
        if not result.is_valid:
            return result

        try:
            ipaddress.ip_address(ip)
        except ValueError:
            result.is_valid = False
            result.status = ValidationStatus.INVALID
            result.errors.append("Invalid IP address format")

        return result

    def validate_uuid(self, value: str) -> ValidationResult:
        """Validate a UUID string."""
        result = self.validate_string(value, min_length=36, max_length=36)
        if not result.is_valid:
            return result

        if not self._UUID_PATTERN.match(value):
            result.is_valid = False
            result.status = ValidationStatus.INVALID
            result.errors.append("Invalid UUID format")

        return result

    def validate_integer(
        self,
        value: Any,
        min_value: Optional[int] = None,
        max_value: Optional[int] = None,
    ) -> ValidationResult:
        """Validate an integer value."""
        errors: list[str] = []

        if isinstance(value, bool) or not isinstance(value, int):
            errors.append(f"Expected integer, got {type(value).__name__}")
            return ValidationResult(
                is_valid=False,
                status=ValidationStatus.INVALID,
                errors=errors,
            )

        if min_value is not None and value < min_value:
            errors.append(f"Value {value} is less than minimum {min_value}")

        if max_value is not None and value > max_value:
            errors.append(f"Value {value} is greater than maximum {max_value}")

        is_valid = len(errors) == 0
        return ValidationResult(
            is_valid=is_valid,
            status=ValidationStatus.VALID if is_valid else ValidationStatus.INVALID,
            sanitized_value=value,
            errors=errors,
        )


# ---------------------------------------------------------------------------
# File Upload Security
# ---------------------------------------------------------------------------


class FileUploadSecurity:
    """Security checks for file uploads."""

    # Allowed MIME types and their expected extensions
    _SAFE_MIME_TYPES: dict[str, list[str]] = {
        "image/png": [".png"],
        "image/jpeg": [".jpg", ".jpeg"],
        "image/gif": [".gif"],
        "image/webp": [".webp"],
        "image/svg+xml": [".svg"],
        "application/pdf": [".pdf"],
        "text/plain": [".txt"],
        "text/csv": [".csv"],
        "application/json": [".json"],
        "application/zip": [".zip"],
        "application/x-tar": [".tar"],
        "application/gzip": [".gz", ".tar.gz"],
    }

    # MIME types that can contain executable content
    _DANGEROUS_MIME_TYPES = {
        "application/x-executable",
        "application/x-msdownload",
        "application/x-sh",
        "application/x-csh",
        "text/x-python",
        "text/x-perl",
        "text/x-php",
        "application/x-httpd-php",
        "application/java-archive",
        "application/x-java-applet",
    }

    # Dangerous file extensions
    _DANGEROUS_EXTENSIONS = {
        ".exe", ".dll", ".bat", ".cmd", ".sh", ".bash", ".zsh", ".csh",
        ".py", ".pl", ".php", ".php3", ".php4", ".php5", ".phtml",
        ".jsp", ".asp", ".aspx", ".exe", ".msi", ".com", ".vbs",
        ".js", ".jse", ".wsf", ".wsh", ".ps1", ".psm1",
        ".jar", ".class", ".war", ".ear",
        ".htaccess", ".htpasswd",
    }

    def __init__(self, max_file_size: int = 50 * 1024 * 1024) -> None:  # 50MB default
        self._max_file_size = max_file_size

    def check_upload(
        self,
        filename: str,
        content: bytes,
        content_type: Optional[str] = None,
        allowed_mime_types: Optional[dict[str, list[str]]] = None,
    ) -> FileUploadCheckResult:
        """Perform security checks on an uploaded file.

        Args:
            filename: Original filename.
            content: File content bytes.
            content_type: Declared Content-Type from upload.
            allowed_mime_types: Override the default safe MIME types map.

        Returns:
            FileUploadCheckResult with check results.
        """
        errors: list[str] = []
        warnings: list[str] = []

        # Check file size
        file_size = len(content)
        if file_size == 0:
            errors.append("File is empty")
        if file_size > self._max_file_size:
            errors.append(
                f"File size ({file_size} bytes) exceeds maximum "
                f"({self._max_file_size} bytes)"
            )

        # Check filename for path traversal
        path_guard = PathTraversalGuard()
        if path_guard.is_suspicious(filename):
            errors.append("Filename contains path traversal sequences")

        # Check filename for dangerous characters
        command_guard = CommandInjectionGuard()
        if not command_guard.is_safe_filename(filename):
            warnings.append("Filename contains potentially dangerous characters")

        # Get file extension
        import os

        _, ext = os.path.splitext(filename)
        ext = ext.lower()

        detected_extensions = [ext] if ext else []

        # Check for dangerous extensions
        if ext in self._DANGEROUS_EXTENSIONS:
            errors.append(f"File extension '{ext}' is not allowed")

        # Check MIME type
        mime_types = allowed_mime_types or self._SAFE_MIME_TYPES
        detected_mime = content_type or self._detect_mime_type(content, ext)

        if detected_mime in self._DANGEROUS_MIME_TYPES:
            errors.append(f"Detected MIME type '{detected_mime}' is dangerous")

        # If MIME type is in allowed list, verify extension matches
        if detected_mime and detected_mime in mime_types:
            allowed_exts = mime_types[detected_mime]
            if ext not in allowed_exts:
                warnings.append(
                    f"File extension '{ext}' does not match "
                    f"detected MIME type '{detected_mime}'"
                )

        # Check for polyglot / double extension attacks
        if filename.count(".") > 1:
            parts = filename.lower().split(".")
            for part in parts[1:-1]:
                if f".{part}" in self._DANGEROUS_EXTENSIONS:
                    errors.append(
                        f"Filename contains dangerous extension in middle: .{part}"
                    )

        # Magic byte check for common formats
        if ext == ".pdf" and not content.startswith(b"%PDF"):
            warnings.append("File has .pdf extension but doesn't start with PDF magic bytes")
        if ext in (".png",) and not content.startswith(b"\x89PNG\r\n\x1a\n"):
            warnings.append("File has .png extension but doesn't have PNG magic bytes")
        if ext in (".jpg", ".jpeg") and not content.startswith(b"\xff\xd8\xff"):
            warnings.append("File has .jpg extension but doesn't have JPEG magic bytes")
        if ext == ".gif" and not content[:6] in (b"GIF87a", b"GIF89a"):
            warnings.append("File has .gif extension but doesn't have GIF magic bytes")

        # Check for embedded scripts in SVG
        if ext == ".svg":
            content_lower = content.lower()
            if b"<script" in content_lower or b"onload=" in content_lower:
                errors.append("SVG file contains potentially dangerous script content")

        is_safe = len(errors) == 0
        return FileUploadCheckResult(
            is_safe=is_safe,
            file_type=ext.lstrip("."),
            mime_type=detected_mime,
            file_size=file_size,
            errors=errors,
            warnings=warnings,
            detected_extensions=detected_extensions,
        )

    def _detect_mime_type(self, content: bytes, extension: str) -> Optional[str]:
        """Detect MIME type from file content (basic magic byte detection)."""
        if content.startswith(b"\x89PNG\r\n\x1a\n"):
            return "image/png"
        if content.startswith(b"\xff\xd8\xff"):
            return "image/jpeg"
        if content[:6] in (b"GIF87a", b"GIF89a"):
            return "image/gif"
        if content.startswith(b"RIFF") and content[8:12] == b"WEBP":
            return "image/webp"
        if content.startswith(b"%PDF"):
            return "application/pdf"
        if content.startswith(b"PK\x03\x04"):
            return "application/zip"
        if content.startswith(b"\x1f\x8b"):
            return "application/gzip"
        if content.startswith(b"{") or content.startswith(b"["):
            return "application/json"
        # Fallback based on extension
        if extension == ".svg":
            return "image/svg+xml"
        if extension == ".csv":
            return "text/csv"
        if extension == ".txt":
            return "text/plain"
        return None

    def generate_safe_filename(self, original_filename: str, prefix: str = "upload") -> str:
        """Generate a safe, random filename preserving the extension."""
        import os
        import secrets

        _, ext = os.path.splitext(original_filename)
        ext = ext.lower()

        # Sanitize extension
        if ext in self._DANGEROUS_EXTENSIONS:
            ext = ".txt"  # Rename dangerous extensions

        random_id = secrets.token_hex(16)
        return f"{prefix}_{random_id}{ext}"
