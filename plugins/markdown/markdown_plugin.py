"""
Markdown Processing Plugin for LiuHao AI OS

Provides Markdown to HTML conversion, HTML to Markdown, linting,
table of contents generation, frontmatter parsing, and formatting.

Capabilities:
- md_to_html: Convert Markdown to HTML
- html_to_md: Convert HTML to Markdown
- lint: Lint Markdown content for common issues
- generate_toc: Generate table of contents
- parse_frontmatter: Parse YAML frontmatter
- format_md: Format/beautify Markdown
- extract_headings: Extract all headings
- word_count: Count words in Markdown
"""

import re
import html
from typing import Dict, Any, Optional, List, Tuple
from dataclasses import dataclass, field

from ..plugin_base import (
    BasePlugin,
    PluginMetadata,
    PluginStatus,
    PluginExecutionError,
)


@dataclass
class ConversionResult:
    """Result of a Markdown/HTML conversion."""
    source_format: str
    target_format: str
    content: str
    length: int
    line_count: int


@dataclass
class LintIssue:
    """A single lint issue in Markdown."""
    line: int
    column: int
    severity: str  # error, warning, info
    rule: str
    message: str
    suggestion: str = ""


@dataclass
class LintResult:
    """Result of Markdown linting."""
    issues: List[LintIssue]
    error_count: int
    warning_count: int
    info_count: int
    score: int  # 0-100


@dataclass
class TocEntry:
    """A table of contents entry."""
    level: int
    text: str
    anchor: str
    line_number: int


@dataclass
class FrontmatterResult:
    """Result of frontmatter parsing."""
    has_frontmatter: bool
    data: Dict[str, Any]
    body: str
    raw_frontmatter: str


class MarkdownPlugin(BasePlugin):
    """Markdown processing plugin.

    Provides Markdown to HTML conversion, HTML to Markdown,
    linting, TOC generation, frontmatter parsing, and formatting.

    Example:
        plugin = MarkdownPlugin()
        plugin.initialize()
        plugin.activate()

        result = plugin.md_to_html("# Hello World")
        print(result.content)
    """

    # Lint rules configuration
    DEFAULT_LINT_RULES = {
        "line_length": True,
        "max_line_length": 120,
        "heading_increment": True,
        "no_trailing_whitespace": True,
        "no_duplicate_headings": True,
        "list_marker_consistency": True,
        "code_fence_language": True,
        "first_line_heading": False,
    }

    def __init__(self) -> None:
        """Initialize the markdown plugin."""
        metadata = PluginMetadata(
            name="markdown",
            version="1.0.0",
            description="Markdown processing, conversion, linting, and formatting",
            author="LiuHao AI OS Team",
            categories=["text", "conversion", "utility", "markdown"],
            keywords=["markdown", "html", "lint", "toc", "frontmatter", "format"],
            requires_internet=False,
            dependencies=[],
        )
        super().__init__(metadata)

        self._lint_rules: Dict[str, Any] = dict(self.DEFAULT_LINT_RULES)
        self._heading_slug_cache: Dict[str, int] = {}

    # ==================== Lifecycle Methods ====================

    def initialize(self, config: Optional[Dict[str, Any]] = None) -> bool:
        """Initialize the markdown plugin."""
        try:
            if config:
                if "lint_rules" in config:
                    self._lint_rules.update(config["lint_rules"])

            self._set_status(PluginStatus.INITIALIZED)
            return True
        except Exception as e:
            self._set_error(f"Initialization failed: {e}")
            return False

    def activate(self) -> bool:
        """Activate the markdown plugin."""
        try:
            self._set_status(PluginStatus.ACTIVE)
            return True
        except Exception as e:
            self._set_error(f"Activation failed: {e}")
            return False

    def deactivate(self) -> bool:
        """Deactivate the markdown plugin."""
        try:
            self._set_status(PluginStatus.INACTIVE)
            return True
        except Exception as e:
            self._set_error(str(e))
            return False

    def shutdown(self) -> bool:
        """Shut down the markdown plugin."""
        try:
            self._heading_slug_cache.clear()
            self._set_status(PluginStatus.SHUTDOWN)
            return True
        except Exception as e:
            self._set_error(str(e))
            return False

    def get_capabilities(self) -> List[str]:
        """Return the list of capabilities."""
        return [
            "md_to_html",
            "html_to_md",
            "lint",
            "generate_toc",
            "parse_frontmatter",
            "format_md",
            "extract_headings",
            "word_count",
            "get_lint_rules",
        ]

    # ==================== Markdown to HTML ====================

    def md_to_html(
        self,
        markdown_text: str,
        *,
        hard_wrap: bool = False,
        sanitize: bool = True,
    ) -> ConversionResult:
        """Convert Markdown to HTML.

        Supports common Markdown features: headings, bold, italic,
        links, images, lists, code blocks, blockquotes, tables,
        horizontal rules, and inline code.

        Args:
            markdown_text: The Markdown text to convert.
            hard_wrap: Whether to convert newlines to <br> tags.
            sanitize: Whether to escape HTML in the input.

        Returns:
            ConversionResult with HTML content.
        """
        self._ensure_active()
        self._increment_call_count()

        if sanitize:
            # Escape HTML special characters first
            text = html.escape(markdown_text)
        else:
            text = markdown_text

        lines = text.split("\n")
        html_lines: List[str] = []
        i = 0

        while i < len(lines):
            line = lines[i]

            # Code fences
            if line.startswith("```"):
                lang = line[3:].strip()
                code_lines: List[str] = []
                i += 1
                while i < len(lines) and not lines[i].startswith("```"):
                    code_lines.append(lines[i])
                    i += 1
                code_text = "\n".join(code_lines)
                if lang:
                    html_lines.append(f'<pre><code class="language-{lang}">{code_text}</code></pre>')
                else:
                    html_lines.append(f"<pre><code>{code_text}</code></pre>")
                i += 1
                continue

            # Headings
            heading_match = re.match(r"^(#{1,6})\s+(.+)$", line)
            if heading_match:
                level = len(heading_match.group(1))
                content = heading_match.group(2).strip()
                html_lines.append(f"<h{level}>{self._inline_format(content)}</h{level}>")
                i += 1
                continue

            # Horizontal rule
            if re.match(r"^\s*([-*_])\s*\1\s*\1[\s\1]*$", line) and line.strip():
                html_lines.append("<hr>")
                i += 1
                continue

            # Blockquote
            if line.startswith(">"):
                quote_lines: List[str] = []
                while i < len(lines) and lines[i].startswith(">"):
                    quote_lines.append(lines[i].lstrip("> ").lstrip(">"))
                    i += 1
                quote_text = " ".join(quote_lines)
                html_lines.append(f"<blockquote>{self._inline_format(quote_text)}</blockquote>")
                continue

            # Unordered list
            if re.match(r"^\s*[-*+]\s+", line):
                items: List[str] = []
                while i <(len(lines)) and re.match(r"^\s*[-*+]\s+", lines[i]):
                    item_text = re.sub(r"^\s*[-*+]\s+", "", lines[i])
                    items.append(f"<li>{self._inline_format(item_text)}</li>")
                    i += 1
                html_lines.append("<ul>" + "".join(items) + "</ul>")
                continue

            # Ordered list
            if re.match(r"^\s*\d+\.\s+", line):
                items: List[str] = []
                while i < len(lines) and re.match(r"^\s*\d+\.\s+", lines[i]):
                    item_text = re.sub(r"^\s*\d+\.\s+", "", lines[i])
                    items.append(f"<li>{self._inline_format(item_text)}</li>")
                    i += 1
                html_lines.append("<ol>" + "".join(items) + "</ol>")
                continue

            # Table (simple detection)
            if "|" in line and i + 1 < len(lines) and re.match(r"^[\s|:-]+$", lines[i + 1]):
                table_lines: List[str] = []
                while i < len(lines) and "|" in lines[i] and lines[i].strip():
                    table_lines.append(lines[i])
                    i += 1
                html_lines.append(self._table_to_html(table_lines))
                continue

            # Empty line
            if line.strip() == "":
                html_lines.append("")
                i += 1
                continue

            # Paragraph
            para_lines: List[str] = [line]
            i += 1
            while i < len(lines) and lines[i].strip() != "" and not self._is_block_start(lines[i]):
                para_lines.append(lines[i])
                i += 1
            if hard_wrap:
                para_text = "<br>".join(para_lines)
            else:
                para_text = " ".join(para_lines)
            html_lines.append(f"<p>{self._inline_format(para_text)}</p>")

        html_content = "\n".join(html_lines)

        return ConversionResult(
            source_format="markdown",
            target_format="html",
            content=html_content,
            length=len(html_content),
            line_count=html_content.count("\n") + 1,
        )

    def _inline_format(self, text: str) -> str:
        """Apply inline Markdown formatting."""
        # Images ![alt](url)
        text = re.sub(
            r"!\[([^\]]*)\]\(([^)]+)\)",
            r'<img src="\2" alt="\1">',
            text,
        )
        # Links [text](url)
        text = re.sub(
            r"\[([^\]]+)\]\(([^)]+)\)",
            r'<a href="\2">\1</a>',
            text,
        )
        # Bold **text** or __text__
        text = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", text)
        text = re.sub(r"__(.+?)__", r"<strong>\1</strong>", text)
        # Italic *text* or _text_
        text = re.sub(r"\*(.+?)\*", r"<em>\1</em>", text)
        text = re.sub(r"_(.+?)_", r"<em>\1</em>", text)
        # Inline code `code`
        text = re.sub(r"`([^`]+)`", r"<code>\1</code>", text)
        # Strikethrough ~~text~~
        text = re.sub(r"~~(.+?)~~", r"<del>\1</del>", text)
        return text

    def _is_block_start(self, line: str) -> bool:
        """Check if a line starts a block-level element."""
        if re.match(r"^#{1,6}\s+", line):
            return True
        if line.startswith("```"):
            return True
        if line.startswith(">"):
            return True
        if re.match(r"^\s*[-*+]\s+", line):
            return True
        if re.match(r"^\s*\d+\.\s+", line):
            return True
        if re.match(r"^\s*[-*_]\s*\1\s*\1[\s\1]*$", line) and line.strip():
            return True
        return False

    def _table_to_html(self, table_lines: List[str]) -> str:
        """Convert markdown table lines to HTML."""
        if len(table_lines) < 2:
            return ""

        # Parse header
        headers = [c.strip() for c in table_lines[0].strip("|").split("|")]
        # Skip separator line
        rows = []
        for line in table_lines[2:]:
            cells = [c.strip() for c in line.strip("|").split("|")]
            rows.append(cells)

        html_parts = ["<table>", "<thead>", "<tr>"]
        for h in headers:
            html_parts.append(f"<th>{self._inline_format(h)}</th>")
        html_parts.append("</tr></thead><tbody>")
        for row in rows:
            html_parts.append("<tr>")
            for cell in row:
                html_parts.append(f"<td>{self._inline_format(cell)}</td>")
            html_parts.append("</tr>")
        html_parts.append("</tbody></table>")
        return "".join(html_parts)

    # ==================== HTML to Markdown ====================

    def html_to_md(self, html_text: str) -> ConversionResult:
        """Convert HTML to Markdown.

        Supports common HTML elements: headings, paragraphs, bold,
        italic, links, lists, code blocks, blockquotes, and images.

        Args:
            html_text: The HTML text to convert.

        Returns:
            ConversionResult with Markdown content.
        """
        self._ensure_active()
        self._increment_call_count()

        text = html_text.strip()

        # Code blocks
        text = re.sub(
            r"<pre><code(?: class=\"language-(\w+)\")?>(.*?)</code></pre>",
            lambda m: f"```{m.group(1) or ''}\n{m.group(2).strip()}\n```",
            text,
            flags=re.DOTALL,
        )

        # Headings
        for level in range(6, 0, -1):
            text = re.sub(
                rf"<h{level}>(.*?)</h{level}>",
                lambda m, l=level: f"\n{'#' * l} {m.group(1).strip()}\n",
                text,
                flags=re.DOTALL | re.IGNORECASE,
            )

        # Paragraphs
        text = re.sub(r"<p>(.*?)</p>", lambda m: f"\n{m.group(1).strip()}\n", text, flags=re.DOTALL | re.IGNORECASE)

        # Line breaks
        text = re.sub(r"<br\s*/?>", "\n", text, flags=re.IGNORECASE)

        # Bold
        text = re.sub(r"<strong>(.*?)</strong>", r"**\1**", text, flags=re.DOTALL | re.IGNORECASE)
        text = re.sub(r"<b>(.*?)</b>", r"**\1**", text, flags=re.DOTALL | re.IGNORECASE)

        # Italic
        text = re.sub(r"<em>(.*?)</em>", r"*\1*", text, flags=re.DOTALL | re.IGNORECASE)
        text = re.sub(r"<i>(.*?)</i>", r"*\1*", text, flags=re.DOTALL | re.IGNORECASE)

        # Inline code
        text = re.sub(r"<code>(.*?)</code>", r"`\1`", text, flags=re.DOTALL | re.IGNORECASE)

        # Links
        text = re.sub(r'<a\s+href="([^"]+)"[^>]*>(.*?)</a>', r"[\2](\1)", text, flags=re.DOTALL | re.IGNORECASE)

        # Images
        text = re.sub(r'<img\s+src="([^"]+)"\s+alt="([^"]*)"[^>]*/?>', r"![\2](\1)", text, flags=re.IGNORECASE)
        text = re.sub(r'<img\s+alt="([^"]*)"\s+src="([^"]+)"[^>]*/?>', r"![\1](\2)", text, flags=re.IGNORECASE)

        # Blockquotes
        text = re.sub(
            r"<blockquote>(.*?)</blockquote>",
            lambda m: "\n" + "\n".join(f"> {line}" for line in m.group(1).strip().split("\n") if line.strip()) + "\n",
            text,
            flags=re.DOTALL | re.IGNORECASE,
        )

        # Unordered lists
        text = re.sub(r"<ul>(.*?)</ul>", lambda m: self._html_list_to_md(m.group(1), "-"), text, flags=re.DOTALL | re.IGNORECASE)

        # Ordered lists
        text = re.sub(r"<ol>(.*?)</ol>", lambda m: self._html_list_to_md(m.group(1), "1."), text, flags=re.DOTALL | re.IGNORECASE)

        # List items
        text = re.sub(r"<li>(.*?)</li>", r"\1", text, flags=re.DOTALL | re.IGNORECASE)

        # Horizontal rule
        text = re.sub(r"<hr\s*/?>", "\n---\n", text, flags=re.IGNORECASE)

        # Unescape HTML entities
        text = html.unescape(text)

        # Clean up excessive newlines
        text = re.sub(r"\n{3,}", "\n\n", text).strip()

        return ConversionResult(
            source_format="html",
            target_format="markdown",
            content=text,
            length=len(text),
            line_count=text.count("\n") + 1,
        )

    def _html_list_to_md(self, items_html: str, marker: str) -> str:
        """Convert HTML list items to Markdown list."""
        items = re.findall(r"<li>(.*?)</li>", items_html, flags=re.DOTALL | re.IGNORECASE)
        lines = []
        for i, item in enumerate(items):
            if marker == "1.":
                prefix = f"{i + 1}."
            else:
                prefix = marker
            lines.append(f"{prefix} {item.strip()}")
        return "\n" + "\n".join(lines) + "\n"

    # ==================== Linting ====================

    def lint(
        self,
        markdown_text: str,
        rules: Optional[Dict[str, Any]] = None,
    ) -> LintResult:
        """Lint Markdown content for common issues.

        Args:
            markdown_text: The Markdown text to lint.
            rules: Optional rule overrides. Uses defaults if None.

        Returns:
            LintResult with issues and score.
        """
        self._ensure_active()
        self._increment_call_count()

        active_rules = dict(self._lint_rules)
        if rules:
            active_rules.update(rules)

        issues: List[LintIssue] = []
        lines = markdown_text.split("\n")
        headings_seen: Dict[str, List[int]] = {}
        prev_heading_level = 0
        list_marker = None
        in_code_block = False

        for line_num, line in enumerate(lines, 1):
            # Toggle code block state
            if line.startswith("```"):
                in_code_block = not in_code_block
                if in_code_block and active_rules.get("code_fence_language"):
                    if not line[3:].strip():
                        issues.append(LintIssue(
                            line=line_num,
                            column=1,
                            severity="warning",
                            rule="code-fence-language",
                            message="Code block has no language specified",
                            suggestion="Add a language identifier like ```python",
                        ))
                continue

            if in_code_block:
                continue

            # Line length
            if active_rules.get("line_length"):
                max_len = active_rules.get("max_line_length", 120)
                if len(line) > max_len:
                    issues.append(LintIssue(
                        line=line_num,
                        column=max_len + 1,
                        severity="warning",
                        rule="line-length",
                        message=f"Line too long ({len(line)} > {max_len})",
                        suggestion="Consider wrapping the line or breaking content",
                    ))

            # Trailing whitespace
            if active_rules.get("no_trailing_whitespace") and line.endswith((" ", "\t")):
                issues.append(LintIssue(
                    line=line_num,
                    column=len(line.rstrip()) + 1,
                    severity="warning",
                    rule="no-trailing-whitespace",
                    message="Line has trailing whitespace",
                    suggestion="Remove trailing whitespace",
                ))

            # Headings
            heading_match = re.match(r"^(#{1,6})\s+(.+)$", line)
            if heading_match:
                level = len(heading_match.group(1))
                text = heading_match.group(2).strip()

                # Heading increment check
                if active_rules.get("heading_increment") and prev_heading_level > 0:
                    if level > prev_heading_level + 1:
                        issues.append(LintIssue(
                            line=line_num,
                            column=1,
                            severity="warning",
                            rule="heading-increment",
                            message=f"Heading level jumps from H{prev_heading_level} to H{level}",
                            suggestion="Headings should increment by one level at a time",
                        ))
                prev_heading_level = level

                # Duplicate headings
                if active_rules.get("no_duplicate_headings"):
                    slug = self._slugify(text)
                    if slug in headings_seen:
                        issues.append(LintIssue(
                            line=line_num,
                            column=1,
                            severity="warning",
                            rule="no-duplicate-headings",
                            message=f"Duplicate heading: '{text}'",
                            suggestion=f"First seen on line {headings_seen[slug][0]}",
                        ))
                    headings_seen.setdefault(slug, []).append(line_num)

            # List marker consistency
            if active_rules.get("list_marker_consistency"):
                ul_match = re.match(r"^\s*([-*+])\s+", line)
                if ul_match:
                    marker = ul_match.group(1)
                    if list_marker is None:
                        list_marker = marker
                    elif marker != list_marker:
                        issues.append(LintIssue(
                            line=line_num,
                            column=1,
                            severity="info",
                            rule="list-marker-consistency",
                            message=f"Inconsistent list marker: using '{marker}', previous used '{list_marker}'",
                            suggestion=f"Use '{list_marker}' consistently",
                        ))

        # Score calculation
        total = len(issues)
        errors = sum(1 for i in issues if i.severity == "error")
        warnings = sum(1 for i in issues if i.severity == "warning")
        infos = sum(1 for i in issues if i.severity == "info")

        score = max(0, 100 - errors * 15 - warnings * 5 - infos * 2)

        return LintResult(
            issues=issues,
            error_count=errors,
            warning_count=warnings,
            info_count=infos,
            score=score,
        )

    # ==================== Table of Contents ====================

    def generate_toc(
        self,
        markdown_text: str,
        *,
        max_level: int = 3,
        min_level: int = 1,
        ordered: bool = False,
    ) -> List[TocEntry]:
        """Generate a table of contents from Markdown headings.

        Args:
            markdown_text: The Markdown text.
            max_level: Maximum heading level to include.
            min_level: Minimum heading level to include.
            ordered: Whether to generate ordered list markers.

        Returns:
            List of TocEntry objects.
        """
        self._ensure_active()
        self._increment_call_count()

        self._heading_slug_cache.clear()
        entries: List[TocEntry] = []
        lines = markdown_text.split("\n")
        in_code_block = False

        for line_num, line in enumerate(lines, 1):
            if line.startswith("```"):
                in_code_block = not in_code_block
                continue
            if in_code_block:
                continue

            heading_match = re.match(r"^(#{1,6})\s+(.+)$", line)
            if heading_match:
                level = len(heading_match.group(1))
                if min_level <= level <= max_level:
                    text = heading_match.group(2).strip()
                    anchor = self._slugify(text, unique=True)
                    entries.append(TocEntry(
                        level=level,
                        text=text,
                        anchor=anchor,
                        line_number=line_num,
                    ))

        return entries

    def toc_to_markdown(self, entries: List[TocEntry]) -> str:
        """Convert TOC entries to Markdown list format."""
        lines = []
        for entry in entries:
            indent = "  " * (entry.level - 1)
            lines.append(f"{indent}- [{entry.text}](#{entry.anchor})")
        return "\n".join(lines)

    # ==================== Frontmatter ====================

    def parse_frontmatter(self, markdown_text: str) -> FrontmatterResult:
        """Parse YAML frontmatter from Markdown content.

        Frontmatter is delimited by --- at the start of the file.

        Args:
            markdown_text: The Markdown text with potential frontmatter.

        Returns:
            FrontmatterResult with parsed data and body.
        """
        self._ensure_active()
        self._increment_call_count()

        lines = markdown_text.split("\n")

        if not lines or lines[0].strip() != "---":
            return FrontmatterResult(
                has_frontmatter=False,
                data={},
                body=markdown_text,
                raw_frontmatter="",
            )

        # Find closing ---
        end_idx = -1
        for i in range(1, len(lines)):
            if lines[i].strip() == "---":
                end_idx = i
                break

        if end_idx == -1:
            return FrontmatterResult(
                has_frontmatter=False,
                data={},
                body=markdown_text,
                raw_frontmatter="",
            )

        raw = "\n".join(lines[1:end_idx])
        body = "\n".join(lines[end_idx + 1:]).lstrip("\n")

        # Simple YAML-like parsing (key: value)
        data: Dict[str, Any] = {}
        current_list_key: Optional[str] = None
        current_dict_key: Optional[str] = None

        for line in lines[1:end_idx]:
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue

            # List item
            if stripped.startswith("- "):
                if current_list_key:
                    value = stripped[2:].strip()
                    data[current_list_key].append(value)
                continue

            # Key-value pair
            if ":" in stripped:
                key, _, value = stripped.partition(":")
                key = key.strip()
                value = value.strip()

                # Check if value is a list start
                if value == "":
                    data[key] = []
                    current_list_key = key
                    current_dict_key = None
                else:
                    # Parse value type
                    data[key] = self._parse_yaml_value(value)
                    current_list_key = None
                    current_dict_key = key

        return FrontmatterResult(
            has_frontmatter=True,
            data=data,
            body=body,
            raw_frontmatter=raw,
        )

    def _parse_yaml_value(self, value: str) -> Any:
        """Parse a simple YAML scalar value."""
        value = value.strip()

        # Boolean
        if value.lower() in ("true", "yes"):
            return True
        if value.lower() in ("false", "no"):
            return False

        # Null
        if value.lower() in ("null", "~", ""):
            return None

        # Integer
        try:
            return int(value)
        except ValueError:
            pass

        # Float
        try:
            return float(value)
        except ValueError:
            pass

        # String (strip quotes)
        if (value.startswith('"') and value.endswith('"')) or \
           (value.startswith("'") and value.endswith("'")):
            return value[1:-1]

        return value

    # ==================== Formatting ====================

    def format_md(self, markdown_text: str) -> str:
        """Format/beautify Markdown content.

        Applies consistent formatting: heading spacing, list indentation,
        code block language, and line normalization.

        Args:
            markdown_text: The Markdown text to format.

        Returns:
            Formatted Markdown text.
        """
        self._ensure_active()
        self._increment_call_count()

        lines = markdown_text.split("\n")
        formatted: List[str] = []
        in_code_block = False

        for i, line in enumerate(lines):
            # Code blocks
            if line.startswith("```"):
                in_code_block = not in_code_block
                formatted.append(line.rstrip())
                continue

            if in_code_block:
                formatted.append(line.rstrip())
                continue

            # Headings - ensure space after #
            heading_match = re.match(r"^(#{1,6})(\S)", line)
            if heading_match:
                line = heading_match.group(1) + " " + line[len(heading_match.group(1)):]

            # Trailing whitespace
            line = line.rstrip()

            # Unordered list - consistent marker
            line = re.sub(r"^\s*\*\s+", "- ", line)
            line = re.sub(r"^\s*\+\s+", "- ", line)

            formatted.append(line)

        # Normalize blank lines around headings
        result_lines: List[str] = []
        for i, line in enumerate(formatted):
            if re.match(r"^#{1,6}\s+", line):
                if result_lines and result_lines[-1].strip() != "" and i > 0:
                    result_lines.append("")
            result_lines.append(line)

        # Collapse multiple blank lines
        final: List[str] = []
        blank_count = 0
        for line in result_lines:
            if line.strip() == "":
                blank_count += 1
                if blank_count <= 2:
                    final.append(line)
            else:
                blank_count = 0
                final.append(line)

        return "\n".join(final).strip() + "\n"

    # ==================== Utilities ====================

    def extract_headings(self, markdown_text: str) -> List[Dict[str, Any]]:
        """Extract all headings from Markdown text.

        Args:
            markdown_text: The Markdown text.

        Returns:
            List of heading dicts with level, text, and line.
        """
        self._ensure_active()
        self._increment_call_count()

        headings: List[Dict[str, Any]] = []
        lines = markdown_text.split("\n")
        in_code_block = False

        for line_num, line in enumerate(lines, 1):
            if line.startswith("```"):
                in_code_block = not in_code_block
                continue
            if in_code_block:
                continue

            heading_match = re.match(r"^(#{1,6})\s+(.+)$", line)
            if heading_match:
                headings.append({
                    "level": len(heading_match.group(1)),
                    "text": heading_match.group(2).strip(),
                    "line": line_num,
                })

        return headings

    def word_count(self, markdown_text: str) -> Dict[str, Any]:
        """Count words, characters, and lines in Markdown text.

        Args:
            markdown_text: The Markdown text.

        Returns:
            Dict with word_count, char_count, line_count, reading_time.
        """
        self._ensure_active()
        self._increment_call_count()

        # Strip Markdown syntax for word counting
        plain_text = self._strip_markdown(markdown_text)
        words = plain_text.split()
        word_count = len(words)
        char_count = len(plain_text)
        line_count = markdown_text.count("\n") + 1

        # Estimate reading time (200 words per minute)
        reading_minutes = max(1, round(word_count / 200))

        return {
            "word_count": word_count,
            "char_count": char_count,
            "char_count_no_spaces": len(plain_text.replace(" ", "")),
            "line_count": line_count,
            "paragraph_count": sum(1 for p in re.split(r"\n\s*\n", markdown_text.strip()) if p.strip()),
            "reading_time_minutes": reading_minutes,
            "reading_time_text": f"~{reading_minutes} min read",
        }

    def _strip_markdown(self, text: str) -> str:
        """Strip basic Markdown syntax to get plain text."""
        # Remove code blocks
        text = re.sub(r"```.*?```", "", text, flags=re.DOTALL)
        # Remove headings markers
        text = re.sub(r"^#{1,6}\s+", "", text, flags=re.MULTILINE)
        # Remove list markers
        text = re.sub(r"^\s*[-*+]\s+", "", text, flags=re.MULTILINE)
        text = re.sub(r"^\s*\d+\.\s+", "", text, flags=re.MULTILINE)
        # Remove bold/italic markers
        text = re.sub(r"\*\*(.+?)\*\*", r"\1", text)
        text = re.sub(r"__(.+?)__", r"\1", text)
        text = re.sub(r"\*(.+?)\*", r"\1", text)
        text = re.sub(r"_(.+?)_", r"\1", text)
        # Remove inline code markers
        text = re.sub(r"`([^`]+)`", r"\1", text)
        # Remove links but keep text
        text = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", text)
        # Remove images
        text = re.sub(r"!\[([^\]]*)\]\([^)]+\)", r"\1", text)
        # Remove blockquote markers
        text = re.sub(r"^>\s?", "", text, flags=re.MULTILINE)
        return text

    def get_lint_rules(self) -> Dict[str, Any]:
        """Get the current lint rule configuration."""
        return dict(self._lint_rules)

    def _slugify(self, text: str, unique: bool = False) -> str:
        """Convert heading text to URL slug."""
        slug = text.lower()
        slug = re.sub(r"[^\w\s-]", "", slug)
        slug = re.sub(r"[\s_]+", "-", slug)
        slug = re.sub(r"-+", "-", slug)
        slug = slug.strip("-")

        if unique:
            if slug in self._heading_slug_cache:
                self._heading_slug_cache[slug] += 1
                slug = f"{slug}-{self._heading_slug_cache[slug]}"
            else:
                self._heading_slug_cache[slug] = 0

        return slug

    # ==================== Statistics ====================

    def get_stats(self) -> Dict[str, Any]:
        """Get plugin statistics."""
        return {
            "lint_rules": len(self._lint_rules),
            "supported_features": [
                "md_to_html",
                "html_to_md",
                "lint",
                "toc",
                "frontmatter",
                "format",
            ],
        }
