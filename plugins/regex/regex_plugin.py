"""
Regex Toolkit Plugin for LiuHao AI OS

Provides pattern matching, find all matches, replace with groups,
pattern validation, common patterns library, and match explanation.

Capabilities:
- match: Check if pattern matches string
- find_all: Find all matches
- find_first: Find first match
- replace: Replace matches with replacement string
- replace_with_groups: Replace using group references
- split: Split string by pattern
- validate: Validate regex pattern syntax
- get_common_patterns: Get common regex patterns library
- explain: Explain what a regex pattern does
- extract_groups: Extract named groups
"""

import re
from typing import Dict, Any, Optional, List, Tuple, Match
from dataclasses import dataclass, field

from ..plugin_base import (
    BasePlugin,
    PluginMetadata,
    PluginStatus,
    PluginExecutionError,
)


@dataclass
class MatchInfo:
    """Information about a regex match."""
    text: str
    start: int
    end: int
    length: int
    groups: List[str]
    group_dict: Dict[str, str]


@dataclass
class RegexResult:
    """Result of a regex operation."""
    pattern: str
    input_text: str
    matches: List[MatchInfo]
    match_count: int
    success: bool
    error: Optional[str] = None


@dataclass
class ReplaceResult:
    """Result of a regex replace operation."""
    pattern: str
    input_text: str
    replacement: str
    result: str
    replacements_made: int
    success: bool
    error: Optional[str] = None


@dataclass
class ValidationResult:
    """Result of regex pattern validation."""
    pattern: str
    is_valid: bool
    error: Optional[str] = None
    error_position: Optional[int] = None
    flags: List[str] = field(default_factory=list)


@dataclass
class CommonPattern:
    """A common regex pattern from the library."""
    name: str
    pattern: str
    description: str
    category: str
    example: str = ""


class RegexPlugin(BasePlugin):
    """Regex toolkit plugin with matching, replacement, and utilities.

    Provides pattern matching, find all matches, replace with groups,
    pattern validation, common patterns library, and match explanation.

    Example:
        plugin = RegexPlugin()
        plugin.initialize()
        plugin.activate()

        result = plugin.find_all(r"\b\w+@\w+\.\w+\b", text)
        print(result.matches)
    """

    # Common regex patterns library
    COMMON_PATTERNS: List[CommonPattern] = [
        CommonPattern(
            name="email",
            pattern=r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}",
            description="Matches email addresses",
            category="validation",
            example="user@example.com",
        ),
        CommonPattern(
            name="url",
            pattern=r"https?://[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}(/[^\s]*)?",
            description="Matches HTTP/HTTPS URLs",
            category="validation",
            example="https://example.com/path",
        ),
        CommonPattern(
            name="ipv4",
            pattern=r"\b(?:(?:25[0-5]|2[0-4][0-9]|[01]?[0-9][0-9]?)\.){3}(?:25[0-5]|2[0-4][0-9]|[01]?[0-9][0-9]?)\b",
            description="Matches IPv4 addresses",
            category="validation",
            example="192.168.1.1",
        ),
        CommonPattern(
            name="phone_us",
            pattern=r"\+?1?[-.\s]?\(?\d{3}\)?[-.\s]?\d{3}[-.\s]?\d{4}",
            description="Matches US phone numbers",
            category="validation",
            example="(555) 123-4567",
        ),
        CommonPattern(
            name="phone_cn",
            pattern=r"1[3-9]\d{9}",
            description="Matches Chinese mobile phone numbers",
            category="validation",
            example="13812345678",
        ),
        CommonPattern(
            name="date_yyyy_mm_dd",
            pattern=r"\d{4}[-/](?:0[1-9]|1[0-2])[-/](?:0[1-9]|[12]\d|3[01])",
            description="Matches dates in YYYY-MM-DD or YYYY/MM/DD format",
            category="date_time",
            example="2024-01-15",
        ),
        CommonPattern(
            name="time_24h",
            pattern=r"(?:[01]\d|2[0-3]):[0-5]\d(?::[0-5]\d)?",
            description="Matches 24-hour time format",
            category="date_time",
            example="14:30:00",
        ),
        CommonPattern(
            name="time_12h",
            pattern=r"(?:0?[1-9]|1[0-2]):[0-5]\d\s*[APap][Mm]",
            description="Matches 12-hour time with AM/PM",
            category="date_time",
            example="2:30 PM",
        ),
        CommonPattern(
            name="credit_card",
            pattern=r"(?:\d[ -]*?){13,16}",
            description="Matches credit card numbers (basic pattern)",
            category="finance",
            example="4111-1111-1111-1111",
        ),
        CommonPattern(
            name="ssn_us",
            pattern=r"\d{3}-\d{2}-\d{4}",
            description="Matches US Social Security Numbers",
            category="identification",
            example="123-45-6789",
        ),
        CommonPattern(
            name="id_card_cn",
            pattern=r"[1-9]\d{5}(?:19|20)\d{2}(?:0[1-9]|1[0-2])(?:0[1-9]|[12]\d|3[01])\d{3}[\dXx]",
            description="Matches Chinese ID card numbers (18 digits)",
            category="identification",
            example="110101199001011234",
        ),
        CommonPattern(
            name="postal_code_us",
            pattern=r"\d{5}(?:-\d{4})?",
            description="Matches US ZIP codes",
            category="location",
            example="12345-6789",
        ),
        CommonPattern(
            name="postal_code_cn",
            pattern=r"[1-9]\d{5}",
            description="Matches Chinese postal codes",
            category="location",
            example="100000",
        ),
        CommonPattern(
            name="html_tag",
            pattern=r"<([a-zA-Z][a-zA-Z0-9]*)\b[^>]*>(.*?)</\1>",
            description="Matches paired HTML tags",
            category="web",
            example="<div>content</div>",
        ),
        CommonPattern(
            name="hex_color",
            pattern=r"#(?:[0-9a-fA-F]{3}){1,2}\b",
            description="Matches hex color codes",
            category="web",
            example="#FF5733",
        ),
        CommonPattern(
            name="ip_port",
            pattern=r"\b(?:(?:25[0-5]|2[0-4][0-9]|[01]?[0-9][0-9]?)\.){3}(?:25[0-5]|2[0-4][0-9]|[01]?[0-9][0-9]?):\d{1,5}\b",
            description="Matches IP:port combinations",
            category="network",
            example="192.168.1.1:8080",
        ),
        CommonPattern(
            name="mac_address",
            pattern=r"(?:[0-9A-Fa-f]{2}[:-]){5}[0-9A-Fa-f]{2}",
            description="Matches MAC addresses",
            category="network",
            example="00:1A:2B:3C:4D:5E",
        ),
        CommonPattern(
            name="uuid",
            pattern=r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}",
            description="Matches UUIDs",
            category="identification",
            example="550e8400-e29b-41d4-a716-446655440000",
        ),
        CommonPattern(
            name="currency",
            pattern=r"[$€£¥₹₽](?:\d{1,3}(?:[,.]\d{3})*|(?:\d+))(?:[.,]\d{2})?",
            description="Matches currency amounts with symbols",
            category="finance",
            example="$1,234.56",
        ),
        CommonPattern(
            name="hashtag",
            pattern=r"#\w+",
            description="Matches hashtag patterns",
            category="social",
            example="#technology",
        ),
        CommonPattern(
            name="mention",
            pattern=r"@\w+",
            description="Matches @mention patterns",
            category="social",
            example="@username",
        ),
        CommonPattern(
            name="word",
            pattern=r"\b\w+\b",
            description="Matches whole words",
            category="text",
            example="hello",
        ),
        CommonPattern(
            name="number",
            pattern=r"-?\d+(?:\.\d+)?",
            description="Matches integers and decimals",
            category="text",
            example="-123.45",
        ),
    ]

    def __init__(self) -> None:
        """Initialize the regex plugin."""
        metadata = PluginMetadata(
            name="regex",
            version="1.0.0",
            description="Regular expression toolkit with patterns library",
            author="LiuHao AI OS Team",
            categories=["text", "utility", "validation"],
            keywords=["regex", "regular expression", "pattern", "match", "validate"],
            requires_internet=False,
            dependencies=[],
        )
        super().__init__(metadata)

        self._pattern_cache: Dict[str, re.Pattern] = {}
        self._cache_size: int = 500

    # ==================== Lifecycle Methods ====================

    def initialize(self, config: Optional[Dict[str, Any]] = None) -> bool:
        """Initialize the regex plugin."""
        try:
            if config:
                self._cache_size = config.get("cache_size", 500)

            self._set_status(PluginStatus.INITIALIZED)
            return True
        except Exception as e:
            self._set_error(f"Initialization failed: {e}")
            return False

    def activate(self) -> bool:
        """Activate the regex plugin."""
        try:
            self._set_status(PluginStatus.ACTIVE)
            return True
        except Exception as e:
            self._set_error(f"Activation failed: {e}")
            return False

    def deactivate(self) -> bool:
        """Deactivate the regex plugin."""
        try:
            self._set_status(PluginStatus.INACTIVE)
            return True
        except Exception as e:
            self._set_error(str(e))
            return False

    def shutdown(self) -> bool:
        """Shut down the regex plugin."""
        try:
            self._pattern_cache.clear()
            self._set_status(PluginStatus.SHUTDOWN)
            return True
        except Exception as e:
            self._set_error(str(e))
            return False

    def get_capabilities(self) -> List[str]:
        """Return the list of capabilities."""
        return [
            "match",
            "find_all",
            "find_first",
            "replace",
            "replace_with_groups",
            "split",
            "validate",
            "get_common_patterns",
            "get_pattern_by_name",
            "explain",
            "extract_groups",
            "finditer",
        ]

    # ==================== Regex Methods ====================

    def match(
        self,
        pattern: str,
        text: str,
        flags: int = 0,
    ) -> bool:
        """Check if a pattern matches the entire text (from start).

        Args:
            pattern: Regex pattern string.
            text: Text to match against.
            flags: Regex flags (re.IGNORECASE, re.MULTILINE, etc.).

        Returns:
            True if the pattern matches from the start of the text.
        """
        self._ensure_active()
        self._increment_call_count()

        try:
            compiled = self._compile(pattern, flags)
            return bool(compiled.match(text))
        except re.error:
            return False

    def find_first(
        self,
        pattern: str,
        text: str,
        flags: int = 0,
    ) -> Optional[MatchInfo]:
        """Find the first match of a pattern in text.

        Args:
            pattern: Regex pattern string.
            text: Text to search in.
            flags: Regex flags.

        Returns:
            MatchInfo for the first match, or None.
        """
        self._ensure_active()
        self._increment_call_count()

        try:
            compiled = self._compile(pattern, flags)
            m = compiled.search(text)
            if m:
                return self._match_to_info(m)
            return None
        except re.error:
            return None

    def find_all(
        self,
        pattern: str,
        text: str,
        flags: int = 0,
        max_matches: Optional[int] = None,
    ) -> RegexResult:
        """Find all matches of a pattern in text.

        Args:
            pattern: Regex pattern string.
            text: Text to search in.
            flags: Regex flags.
            max_matches: Maximum number of matches to return.

        Returns:
            RegexResult with all matches.
        """
        self._ensure_active()
        self._increment_call_count()

        try:
            compiled = self._compile(pattern, flags)
            matches: List[MatchInfo] = []

            for i, m in enumerate(compiled.finditer(text)):
                if max_matches and i >= max_matches:
                    break
                matches.append(self._match_to_info(m))

            return RegexResult(
                pattern=pattern,
                input_text=text,
                matches=matches,
                match_count=len(matches),
                success=True,
            )
        except re.error as e:
            return RegexResult(
                pattern=pattern,
                input_text=text,
                matches=[],
                match_count=0,
                success=False,
                error=str(e),
            )

    def replace(
        self,
        pattern: str,
        text: str,
        replacement: str,
        flags: int = 0,
        count: int = 0,
    ) -> ReplaceResult:
        """Replace all matches with a replacement string.

        Args:
            pattern: Regex pattern string.
            text: Text to search in.
            replacement: Replacement string (supports \1, \g<name>).
            flags: Regex flags.
            count: Maximum replacements (0 = all).

        Returns:
            ReplaceResult with the modified text.
        """
        self._ensure_active()
        self._increment_call_count()

        try:
            compiled = self._compile(pattern, flags)

            # Count replacements
            matches = list(compiled.finditer(text))
            num_replacements = len(matches) if count == 0 else min(count, len(matches))

            result = compiled.sub(replacement, text, count=count)

            return ReplaceResult(
                pattern=pattern,
                input_text=text,
                replacement=replacement,
                result=result,
                replacements_made=num_replacements,
                success=True,
            )
        except re.error as e:
            return ReplaceResult(
                pattern=pattern,
                input_text=text,
                replacement=replacement,
                result=text,
                replacements_made=0,
                success=False,
                error=str(e),
            )

    def replace_with_groups(
        self,
        pattern: str,
        text: str,
        replacement_func,
        flags: int = 0,
    ) -> ReplaceResult:
        """Replace matches using a function that receives match groups.

        Args:
            pattern: Regex pattern string.
            text: Text to search in.
            replacement_func: Function that takes match groups dict and returns replacement.
            flags: Regex flags.

        Returns:
            ReplaceResult with the modified text.
        """
        self._ensure_active()
        self._increment_call_count()

        try:
            compiled = self._compile(pattern, flags)
            count = 0

            def replacer(m: Match) -> str:
                nonlocal count
                count += 1
                groups = {
                    "match": m.group(0),
                    "groups": list(m.groups()),
                    "groupdict": m.groupdict(),
                    "start": m.start(),
                    "end": m.end(),
                }
                return replacement_func(groups)

            result = compiled.sub(replacer, text)

            return ReplaceResult(
                pattern=pattern,
                input_text=text,
                replacement="(function)",
                result=result,
                replacements_made=count,
                success=True,
            )
        except re.error as e:
            return ReplaceResult(
                pattern=pattern,
                input_text=text,
                replacement="(function)",
                result=text,
                replacements_made=0,
                success=False,
                error=str(e),
            )

    def split(
        self,
        pattern: str,
        text: str,
        flags: int = 0,
        maxsplit: int = 0,
    ) -> List[str]:
        """Split text by regex pattern.

        Args:
            pattern: Regex pattern to split on.
            text: Text to split.
            flags: Regex flags.
            maxsplit: Maximum number of splits (0 = unlimited).

        Returns:
            List of split string parts.
        """
        self._ensure_active()
        self._increment_call_count()

        try:
            compiled = self._compile(pattern, flags)
            return compiled.split(text, maxsplit=maxsplit)
        except re.error:
            return [text]

    def validate(self, pattern: str) -> ValidationResult:
        """Validate a regex pattern syntax.

        Args:
            pattern: Regex pattern string to validate.

        Returns:
            ValidationResult with validity and error info.
        """
        self._ensure_active()
        self._increment_call_count()

        try:
            re.compile(pattern)
            return ValidationResult(
                pattern=pattern,
                is_valid=True,
            )
        except re.error as e:
            return ValidationResult(
                pattern=pattern,
                is_valid=False,
                error=str(e),
                error_position=e.pos if hasattr(e, "pos") else None,
            )

    def extract_groups(
        self,
        pattern: str,
        text: str,
        flags: int = 0,
    ) -> List[Dict[str, Any]]:
        """Extract named groups from all matches.

        Args:
            pattern: Regex pattern with named groups.
            text: Text to search in.
            flags: Regex flags.

        Returns:
            List of dicts with named group values.
        """
        self._ensure_active()
        self._increment_call_count()

        try:
            compiled = self._compile(pattern, flags)
            results = []

            for m in compiled.finditer(text):
                results.append({
                    "full_match": m.group(0),
                    "groups": list(m.groups()),
                    "named_groups": m.groupdict(),
                    "start": m.start(),
                    "end": m.end(),
                })

            return results
        except re.error:
            return []

    # ==================== Patterns Library ====================

    def get_common_patterns(
        self,
        category: Optional[str] = None,
    ) -> List[Dict[str, str]]:
        """Get common regex patterns from the library.

        Args:
            category: Optional category filter.

        Returns:
            List of pattern info dicts.
        """
        self._ensure_active()
        self._increment_call_count()

        patterns = []
        for p in self.COMMON_PATTERNS:
            if category and p.category != category:
                continue
            patterns.append({
                "name": p.name,
                "pattern": p.pattern,
                "description": p.description,
                "category": p.category,
                "example": p.example,
            })
        return patterns

    def get_pattern_by_name(self, name: str) -> Optional[CommonPattern]:
        """Get a common pattern by name.

        Args:
            name: Pattern name.

        Returns:
            CommonPattern or None.
        """
        self._ensure_active()

        for p in self.COMMON_PATTERNS:
            if p.name.lower() == name.lower():
                return p
        return None

    def explain(self, pattern: str) -> Dict[str, Any]:
        """Explain what a regex pattern does (basic explanation).

        Provides a breakdown of common regex components.

        Args:
            pattern: Regex pattern to explain.

        Returns:
            Dict with explanation and component breakdown.
        """
        self._ensure_active()
        self._increment_call_count()

        components = []

        # Analyze common components
        i = 0
        while i < len(pattern):
            c = pattern[i]

            if c == "\\":
                if i + 1 < len(pattern):
                    next_c = pattern[i + 1]
                    explanations = {
                        "d": "digit (0-9)",
                        "D": "non-digit",
                        "w": "word character (a-z, A-Z, 0-9, _)",
                        "W": "non-word character",
                        "s": "whitespace",
                        "S": "non-whitespace",
                        "b": "word boundary",
                        "B": "non-word boundary",
                        "n": "newline",
                        "t": "tab",
                        "r": "carriage return",
                        ".": "literal dot",
                    }
                    if next_c in explanations:
                        components.append({
                            "position": i,
                            "token": f"\\{next_c}",
                            "meaning": explanations[next_c],
                        })
                        i += 2
                        continue
                i += 1
                continue

            if c == ".":
                components.append({"position": i, "token": ".", "meaning": "any character except newline"})
            elif c == "^":
                components.append({"position": i, "token": "^", "meaning": "start of string/line"})
            elif c == "$":
                components.append({"position": i, "token": "$", "meaning": "end of string/line"})
            elif c == "*":
                components.append({"position": i, "token": "*", "meaning": "zero or more of previous"})
            elif c == "+":
                components.append({"position": i, "token": "+", "meaning": "one or more of previous"})
            elif c == "?":
                components.append({"position": i, "token": "?", "meaning": "zero or one of previous (or lazy)"})
            elif c == "{":
                # Find closing brace
                end = pattern.find("}", i)
                if end > i:
                    quantifier = pattern[i:end+1]
                    components.append({
                        "position": i,
                        "token": quantifier,
                        "meaning": f"exact/range repetition: {quantifier}",
                    })
                    i = end + 1
                    continue
            elif c == "[":
                # Find closing bracket
                end = pattern.find("]", i)
                if end > i:
                    char_class = pattern[i:end+1]
                    negated = len(char_class) > 1 and char_class[1] == "^"
                    components.append({
                        "position": i,
                        "token": char_class,
                        "meaning": f"character class ({'any of' if not negated else 'none of'})",
                    })
                    i = end + 1
                    continue
            elif c == "(":
                if i + 1 < len(pattern):
                    if pattern[i+1:i+3] == "?:":
                        components.append({"position": i, "token": "(?:...)", "meaning": "non-capturing group"})
                    elif pattern[i+1:i+3] == "?=":
                        components.append({"position": i, "token": "(?=...)", "meaning": "positive lookahead"})
                    elif pattern[i+1:i+3] == "?!":
                        components.append({"position": i, "token": "(?!...)", "meaning": "negative lookahead"})
                    elif pattern[i+1:i+3] == "?<":
                        components.append({"position": i, "token": "(?<name>...)", "meaning": "named capturing group"})
                    else:
                        components.append({"position": i, "token": "(...)", "meaning": "capturing group"})
            elif c == "|":
                components.append({"position": i, "token": "|", "meaning": "alternation (OR)"})

            i += 1

        # Count groups
        group_count = len(re.findall(r'\((?!\?)', pattern))

        return {
            "pattern": pattern,
            "length": len(pattern),
            "components": components,
            "group_count": group_count,
            "summary": f"Pattern with {len(components)} components and {group_count} capturing groups",
        }

    # ==================== Internal Helpers ====================

    def _compile(self, pattern: str, flags: int) -> re.Pattern:
        """Compile and cache a regex pattern."""
        cache_key = f"{pattern}:{flags}"

        if cache_key in self._pattern_cache:
            return self._pattern_cache[cache_key]

        compiled = re.compile(pattern, flags)

        # Simple cache with size limit
        if len(self._pattern_cache) >= self._cache_size:
            # Remove oldest entry
            oldest = next(iter(self._pattern_cache))
            del self._pattern_cache[oldest]

        self._pattern_cache[cache_key] = compiled
        return compiled

    def _match_to_info(self, m: Match) -> MatchInfo:
        """Convert a regex Match object to MatchInfo."""
        return MatchInfo(
            text=m.group(0),
            start=m.start(),
            end=m.end(),
            length=m.end() - m.start(),
            groups=list(m.groups()),
            group_dict=m.groupdict(),
        )

    # ==================== Statistics ====================

    def get_stats(self) -> Dict[str, Any]:
        """Get plugin statistics."""
        return {
            "common_patterns": len(self.COMMON_PATTERNS),
            "cache_size": len(self._pattern_cache),
            "cache_max": self._cache_size,
            "categories": list(set(p.category for p in self.COMMON_PATTERNS)),
        }
