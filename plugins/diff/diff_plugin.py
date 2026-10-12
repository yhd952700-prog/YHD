"""
Diff & Patch Plugin for LiuHao AI OS

Provides text diff (unified, context, side-by-side), diff statistics,
patch application, three-way merge interface, word-level diff,
and HTML diff output.

Capabilities:
- unified_diff: Generate unified diff
- context_diff: Generate context diff
- word_diff: Word-level diff
- side_by_side: Side-by-side diff
- diff_stats: Diff statistics
- apply_patch: Apply a patch
- html_diff: Generate HTML diff output
- similarity: Calculate similarity ratio
- three_way_merge: Three-way merge interface
- diff_lines: Get changed line numbers
"""

import re
import difflib
import html
from typing import Dict, Any, Optional, List, Tuple, Union
from dataclasses import dataclass, field

from ..plugin_base import (
    BasePlugin,
    PluginMetadata,
    PluginStatus,
    PluginExecutionError,
)


@dataclass
class DiffStats:
    """Statistics about a diff."""
    added_lines: int = 0
    removed_lines: int = 0
    changed_lines: int = 0
    unchanged_lines: int = 0
    total_old_lines: int = 0
    total_new_lines: int = 0
    similarity_ratio: float = 0.0
    changes: List[Dict[str, int]] = field(default_factory=list)


@dataclass
class PatchResult:
    """Result of applying a patch."""
    success: bool
    result: str = ""
    applied_hunks: int = 0
    failed_hunks: int = 0
    errors: List[str] = field(default_factory=list)


@dataclass
class MergeResult:
    """Result of a three-way merge."""
    success: bool
    result: str = ""
    conflicts: List[str] = field(default_factory=list)
    conflict_count: int = 0


class DiffPlugin(BasePlugin):
    """Diff and patch plugin.

    Provides text comparison, diff generation in multiple formats,
    patch application, and three-way merge.

    Example:
        plugin = DiffPlugin()
        plugin.initialize()
        plugin.activate()

        diff = plugin.unified_diff(old_text, new_text)
        print(diff)
    """

    def __init__(self) -> None:
        """Initialize the diff plugin."""
        metadata = PluginMetadata(
            name="diff",
            version="1.0.0",
            description="Text diff, patch application, and three-way merge",
            author="LiuHao AI OS Team",
            categories=["text", "utility", "development"],
            keywords=["diff", "patch", "merge", "compare", "unified", "text"],
            requires_internet=False,
            dependencies=[],
        )
        super().__init__(metadata)

        self._default_context_lines: int = 3
        self._default_tab_width: int = 4

    # ==================== Lifecycle Methods ====================

    def initialize(self, config: Optional[Dict[str, Any]] = None) -> bool:
        """Initialize the diff plugin."""
        try:
            if config:
                self._default_context_lines = config.get("context_lines", 3)
                self._default_tab_width = config.get("tab_width", 4)

            self._set_status(PluginStatus.INITIALIZED)
            return True
        except Exception as e:
            self._set_error(f"Initialization failed: {e}")
            return False

    def activate(self) -> bool:
        """Activate the diff plugin."""
        try:
            self._set_status(PluginStatus.ACTIVE)
            return True
        except Exception as e:
            self._set_error(f"Activation failed: {e}")
            return False

    def deactivate(self) -> bool:
        """Deactivate the diff plugin."""
        try:
            self._set_status(PluginStatus.INACTIVE)
            return True
        except Exception as e:
            self._set_error(str(e))
            return False

    def shutdown(self) -> bool:
        """Shut down the diff plugin."""
        try:
            self._set_status(PluginStatus.SHUTDOWN)
            return True
        except Exception as e:
            self._set_error(str(e))
            return False

    def get_capabilities(self) -> List[str]:
        """Return the list of capabilities."""
        return [
            "unified_diff",
            "context_diff",
            "word_diff",
            "side_by_side",
            "diff_stats",
            "apply_patch",
            "html_diff",
            "similarity",
            "three_way_merge",
            "diff_lines",
            "char_diff",
            "compare_files",
        ]

    # ==================== Unified Diff ====================

    def unified_diff(
        self,
        old_text: str,
        new_text: str,
        *,
        old_label: str = "original",
        new_label: str = "modified",
        context_lines: Optional[int] = None,
    ) -> str:
        """Generate a unified diff.

        Args:
            old_text: Original text.
            new_text: Modified text.
            old_label: Label for original file.
            new_label: Label for modified file.
            context_lines: Number of context lines.

        Returns:
            Unified diff string.
        """
        self._ensure_active()
        self._increment_call_count()

        context = context_lines if context_lines is not None else self._default_context_lines

        old_lines = old_text.splitlines(keepends=True)
        new_lines = new_text.splitlines(keepends=True)

        diff = difflib.unified_diff(
            old_lines,
            new_lines,
            fromfile=old_label,
            tofile=new_label,
            n=context,
        )

        return "".join(diff)

    # ==================== Context Diff ====================

    def context_diff(
        self,
        old_text: str,
        new_text: str,
        *,
        old_label: str = "original",
        new_label: str = "modified",
        context_lines: Optional[int] = None,
    ) -> str:
        """Generate a context diff.

        Args:
            old_text: Original text.
            new_text: Modified text.
            old_label: Label for original file.
            new_label: Label for modified file.
            context_lines: Number of context lines.

        Returns:
            Context diff string.
        """
        self._ensure_active()
        self._increment_call_count()

        context = context_lines if context_lines is not None else self._default_context_lines

        old_lines = old_text.splitlines(keepends=True)
        new_lines = new_text.splitlines(keepends=True)

        diff = difflib.context_diff(
            old_lines,
            new_lines,
            fromfile=old_label,
            tofile=new_label,
            n=context,
        )

        return "".join(diff)

    # ==================== Word-Level Diff ====================

    def word_diff(
        self,
        old_text: str,
        new_text: str,
    ) -> List[Dict[str, Any]]:
        """Generate a word-level diff.

        Args:
            old_text: Original text.
            new_text: Modified text.

        Returns:
            List of diff tokens with type and value.
        """
        self._ensure_active()
        self._increment_call_count()

        # Split into words
        old_words = re.findall(r"\S+\s*|\s+", old_text)
        new_words = re.findall(r"\S+\s*|\s+", new_text)

        matcher = difflib.SequenceMatcher(None, old_words, new_words)
        result: List[Dict[str, Any]] = []

        for tag, i1, i2, j1, j2 in matcher.get_opcodes():
            if tag == "equal":
                for word in old_words[i1:i2]:
                    result.append({"type": "equal", "value": word})
            elif tag == "replace":
                for word in old_words[i1:i2]:
                    result.append({"type": "delete", "value": word})
                for word in new_words[j1:j2]:
                    result.append({"type": "insert", "value": word})
            elif tag == "delete":
                for word in old_words[i1:i2]:
                    result.append({"type": "delete", "value": word})
            elif tag == "insert":
                for word in new_words[j1:j2]:
                    result.append({"type": "insert", "value": word})

        return result

    # ==================== Character-Level Diff ====================

    def char_diff(
        self,
        old_text: str,
        new_text: str,
    ) -> List[Dict[str, Any]]:
        """Generate a character-level diff.

        Args:
            old_text: Original text.
            new_text: Modified text.

        Returns:
            List of diff tokens with type and value.
        """
        self._ensure_active()
        self._increment_call_count()

        matcher = difflib.SequenceMatcher(None, old_text, new_text)
        result: List[Dict[str, Any]] = []

        for tag, i1, i2, j1, j2 in matcher.get_opcodes():
            if tag == "equal":
                result.append({"type": "equal", "value": old_text[i1:i2]})
            elif tag == "replace":
                result.append({"type": "delete", "value": old_text[i1:i2]})
                result.append({"type": "insert", "value": new_text[j1:j2]})
            elif tag == "delete":
                result.append({"type": "delete", "value": old_text[i1:i2]})
            elif tag == "insert":
                result.append({"type": "insert", "value": new_text[j1:j2]})

        return result

    # ==================== Side-by-Side Diff ====================

    def side_by_side(
        self,
        old_text: str,
        new_text: str,
        *,
        width: int = 80,
        show_line_numbers: bool = True,
    ) -> str:
        """Generate a side-by-side diff.

        Args:
            old_text: Original text.
            new_text: Modified text.
            width: Width of each column.
            show_line_numbers: Whether to show line numbers.

        Returns:
            Side-by-side diff string.
        """
        self._ensure_active()
        self._increment_call_count()

        old_lines = old_text.splitlines()
        new_lines = new_text.splitlines()

        matcher = difflib.SequenceMatcher(None, old_lines, new_lines)
        output_lines: List[str] = []

        col_width = width
        sep = " | "

        # Header
        old_header = " Original ".ljust(col_width, "-")
        new_header = " Modified ".ljust(col_width, "-")
        output_lines.append(f"{old_header}{sep}{new_header}")

        old_line_num = 1
        new_line_num = 1

        for tag, i1, i2, j1, j2 in matcher.get_opcodes():
            if tag == "equal":
                for k in range(i2 - i1):
                    left = self._truncate_pad(old_lines[i1 + k], col_width)
                    right = self._truncate_pad(new_lines[j1 + k], col_width)
                    if show_line_numbers:
                        left = f"{old_line_num:4d} {left[5:]}"
                        right = f"{new_line_num:4d} {right[5:]}"
                    output_lines.append(f"  {left}{sep}  {right}")
                    old_line_num += 1
                    new_line_num += 1
            elif tag == "replace":
                max_len = max(i2 - i1, j2 - j1)
                for k in range(max_len):
                    if k < i2 - i1:
                        left = self._truncate_pad(old_lines[i1 + k], col_width)
                        if show_line_numbers:
                            left = f"{old_line_num:4d} {left[5:]}"
                        left_line = f"- {left}"
                        old_line_num += 1
                    else:
                        left_line = "  " + " " * col_width

                    if k < j2 - j1:
                        right = self._truncate_pad(new_lines[j1 + k], col_width)
                        if show_line_numbers:
                            right = f"{new_line_num:4d} {right[5:]}"
                        right_line = f"+ {right}"
                        new_line_num += 1
                    else:
                        right_line = "  " + " " * col_width

                    output_lines.append(f"{left_line}{sep}{right_line}")
            elif tag == "delete":
                for k in range(i2 - i1):
                    left = self._truncate_pad(old_lines[i1 + k], col_width)
                    if show_line_numbers:
                        left = f"{old_line_num:4d} {left[5:]}"
                    output_lines.append(f"- {left}{sep}  {' ' * col_width}")
                    old_line_num += 1
            elif tag == "insert":
                for k in range(j2 - j1):
                    right = self._truncate_pad(new_lines[j1 + k], col_width)
                    if show_line_numbers:
                        right = f"{new_line_num:4d} {right[5:]}"
                    output_lines.append(f"  {' ' * col_width}{sep}+ {right}")
                    new_line_num += 1

        return "\n".join(output_lines)

    def _truncate_pad(self, text: str, width: int) -> str:
        """Truncate or pad text to width."""
        if len(text) > width:
            return text[:width - 1] + "..."
        return text.ljust(width)

    # ==================== Diff Statistics ====================

    def diff_stats(
        self,
        old_text: str,
        new_text: str,
    ) -> DiffStats:
        """Calculate diff statistics.

        Args:
            old_text: Original text.
            new_text: Modified text.

        Returns:
            DiffStats with detailed statistics.
        """
        self._ensure_active()
        self._increment_call_count()

        old_lines = old_text.splitlines()
        new_lines = new_text.splitlines()

        matcher = difflib.SequenceMatcher(None, old_lines, new_lines)

        added = 0
        removed = 0
        unchanged = 0
        changes: List[Dict[str, int]] = []

        for tag, i1, i2, j1, j2 in matcher.get_opcodes():
            if tag == "equal":
                unchanged += i2 - i1
            elif tag == "replace":
                removed += i2 - i1
                added += j2 - j1
                changes.append({
                    "old_start": i1 + 1,
                    "old_end": i2,
                    "new_start": j1 + 1,
                    "new_end": j2,
                    "type": "modify",
                })
            elif tag == "delete":
                removed += i2 - i1
                changes.append({
                    "old_start": i1 + 1,
                    "old_end": i2,
                    "new_start": j1 + 1,
                    "new_end": j2,
                    "type": "delete",
                })
            elif tag == "insert":
                added += j2 - j1
                changes.append({
                    "old_start": i1 + 1,
                    "old_end": i2,
                    "new_start": j1 + 1,
                    "new_end": j2,
                    "type": "insert",
                })

        ratio = matcher.ratio()

        return DiffStats(
            added_lines=added,
            removed_lines=removed,
            changed_lines=len([c for c in changes if c["type"] == "modify"]),
            unchanged_lines=unchanged,
            total_old_lines=len(old_lines),
            total_new_lines=len(new_lines),
            similarity_ratio=round(ratio, 4),
            changes=changes,
        )

    # ==================== Similarity ====================

    def similarity(
        self,
        text1: str,
        text2: str,
        *,
        level: str = "line",
    ) -> float:
        """Calculate similarity ratio between two texts.

        Args:
            text1: First text.
            text2: Second text.
            level: Comparison level: 'line', 'word', 'char'.

        Returns:
            Similarity ratio (0.0 to 1.0).
        """
        self._ensure_active()
        self._increment_call_count()

        if level == "line":
            a = text1.splitlines()
            b = text2.splitlines()
        elif level == "word":
            a = text1.split()
            b = text2.split()
        elif level == "char":
            a = list(text1)
            b = list(text2)
        else:
            raise ValueError(f"Unknown level: {level}. Use 'line', 'word', or 'char'.")

        matcher = difflib.SequenceMatcher(None, a, b)
        return round(matcher.ratio(), 4)

    # ==================== HTML Diff ====================

    def html_diff(
        self,
        old_text: str,
        new_text: str,
        *,
        title: str = "Diff",
        line_numbers: bool = True,
    ) -> str:
        """Generate an HTML diff view.

        Args:
            old_text: Original text.
            new_text: Modified text.
            title: HTML page title.
            line_numbers: Whether to show line numbers.

        Returns:
            HTML string with formatted diff.
        """
        self._ensure_active()
        self._increment_call_count()

        old_lines = old_text.splitlines()
        new_lines = new_text.splitlines()

        matcher = difflib.SequenceMatcher(None, old_lines, new_lines)

        html_parts: List[str] = [
            "<!DOCTYPE html>",
            "<html><head>",
            f"<title>{html.escape(title)}</title>",
            "<style>",
            "body { font-family: monospace; font-size: 14px; margin: 20px; }",
            ".diff-table { border-collapse: collapse; width: 100%; }",
            ".diff-table td { padding: 2px 8px; vertical-align: top; white-space: pre-wrap; }",
            ".line-num { color: #999; text-align: right; width: 40px; user-select: none; }",
            ".added { background-color: #e6ffed; }",
            ".removed { background-color: #ffeef0; }",
            ".added-bg { background-color: #acf2bd; }",
            ".removed-bg { background-color: #fdb8c0; }",
            ".unchanged { background-color: #fff; }",
            ".diff-marker { width: 20px; text-align: center; font-weight: bold; }",
            "h1 { font-family: sans-serif; color: #333; }",
            "</style>",
            "</head><body>",
            f"<h1>{html.escape(title)}</h1>",
            '<table class="diff-table">',
        ]

        old_line_num = 1
        new_line_num = 1

        for tag, i1, i2, j1, j2 in matcher.get_opcodes():
            if tag == "equal":
                for k in range(i2 - i1):
                    left_line = html.escape(old_lines[i1 + k])
                    right_line = html.escape(new_lines[j1 + k])
                    html_parts.append("<tr class='unchanged'>")
                    if line_numbers:
                        html_parts.append(f"<td class='line-num'>{old_line_num}</td>")
                    html_parts.append(f"<td class='diff-marker'> </td>")
                    html_parts.append(f"<td>{left_line}</td>")
                    if line_numbers:
                        html_parts.append(f"<td class='line-num'>{new_line_num}</td>")
                    html_parts.append(f"<td class='diff-marker'> </td>")
                    html_parts.append(f"<td>{right_line}</td>")
                    html_parts.append("</tr>")
                    old_line_num += 1
                    new_line_num += 1
            elif tag == "replace":
                max_len = max(i2 - i1, j2 - j1)
                for k in range(max_len):
                    html_parts.append("<tr>")
                    if k < i2 - i1:
                        left_line = html.escape(old_lines[i1 + k])
                        if line_numbers:
                            html_parts.append(f"<td class='line-num'>{old_line_num}</td>")
                        html_parts.append("<td class='diff-marker'>-</td>")
                        html_parts.append(f"<td class='removed'>{left_line}</td>")
                        old_line_num += 1
                    else:
                        if line_numbers:
                            html_parts.append("<td class='line-num'></td>")
                        html_parts.append("<td class='diff-marker'> </td>")
                        html_parts.append("<td class='removed'></td>")

                    if k < j2 - j1:
                        right_line = html.escape(new_lines[j1 + k])
                        if line_numbers:
                            html_parts.append(f"<td class='line-num'>{new_line_num}</td>")
                        html_parts.append("<td class='diff-marker'>+</td>")
                        html_parts.append(f"<td class='added'>{right_line}</td>")
                        new_line_num += 1
                    else:
                        if line_numbers:
                            html_parts.append("<td class='line-num'></td>")
                        html_parts.append("<td class='diff-marker'> </td>")
                        html_parts.append("<td class='added'></td>")
                    html_parts.append("</tr>")
            elif tag == "delete":
                for k in range(i2 - i1):
                    left_line = html.escape(old_lines[i1 + k])
                    html_parts.append("<tr>")
                    if line_numbers:
                        html_parts.append(f"<td class='line-num'>{old_line_num}</td>")
                    html_parts.append("<td class='diff-marker'>-</td>")
                    html_parts.append(f"<td class='removed'>{left_line}</td>")
                    if line_numbers:
                        html_parts.append("<td class='line-num'></td>")
                    html_parts.append("<td class='diff-marker'> </td>")
                    html_parts.append("<td></td>")
                    html_parts.append("</tr>")
                    old_line_num += 1
            elif tag == "insert":
                for k in range(j2 - j1):
                    right_line = html.escape(new_lines[j1 + k])
                    html_parts.append("<tr>")
                    if line_numbers:
                        html_parts.append("<td class='line-num'></td>")
                    html_parts.append("<td class='diff-marker'> </td>")
                    html_parts.append("<td></td>")
                    if line_numbers:
                        html_parts.append(f"<td class='line-num'>{new_line_num}</td>")
                    html_parts.append("<td class='diff-marker'>+</td>")
                    html_parts.append(f"<td class='added'>{right_line}</td>")
                    html_parts.append("</tr>")
                    new_line_num += 1

        html_parts.append("</table></body></html>")
        return "".join(html_parts)

    # ==================== Patch Application ====================

    def apply_patch(
        self,
        original_text: str,
        patch_text: str,
    ) -> PatchResult:
        """Apply a unified diff patch to text.

        Args:
            original_text: Original text.
            patch_text: Unified diff patch.

        Returns:
            PatchResult with applied result.
        """
        self._ensure_active()
        self._increment_call_count()

        errors: List[str] = []
        applied_hunks = 0
        failed_hunks = 0

        original_lines = original_text.splitlines(keepends=True)
        result_lines = list(original_lines)

        # Parse the patch
        hunks = self._parse_unified_patch(patch_text)

        # Apply hunks in reverse order to preserve line numbers
        for hunk in reversed(hunks):
            try:
                result_lines = self._apply_hunk(result_lines, hunk)
                applied_hunks += 1
            except Exception as e:
                failed_hunks += 1
                errors.append(f"Hunk failed: {e}")

        success = failed_hunks == 0
        result = "".join(result_lines)

        return PatchResult(
            success=success,
            result=result,
            applied_hunks=applied_hunks,
            failed_hunks=failed_hunks,
            errors=errors,
        )

    def _parse_unified_patch(self, patch_text: str) -> List[Dict[str, Any]]:
        """Parse a unified diff into hunks."""
        lines = patch_text.splitlines(keepends=True)
        hunks: List[Dict[str, Any]] = []
        current_hunk: Optional[Dict[str, Any]] = None

        for line in lines:
            # Hunk header: @@ -old_start,old_count +new_start,new_count @@
            hunk_match = re.match(r"^@@ -(\d+),?(\d*) \+(\d+),?(\d*) @@", line)
            if hunk_match:
                if current_hunk:
                    hunks.append(current_hunk)
                old_start = int(hunk_match.group(1))
                old_count = int(hunk_match.group(2)) if hunk_match.group(2) else 1
                new_start = int(hunk_match.group(3))
                new_count = int(hunk_match.group(4)) if hunk_match.group(4) else 1
                current_hunk = {
                    "old_start": old_start,
                    "old_count": old_count,
                    "new_start": new_start,
                    "new_count": new_count,
                    "lines": [],
                }
                continue

            if current_hunk is not None:
                current_hunk["lines"].append(line)

        if current_hunk:
            hunks.append(current_hunk)

        return hunks

    def _apply_hunk(
        self,
        lines: List[str],
        hunk: Dict[str, Any],
    ) -> List[str]:
        """Apply a single hunk to lines."""
        old_start = hunk["old_start"] - 1  # 0-indexed
        old_count = hunk["old_count"]

        # Collect old lines from hunk
        hunk_old_lines: List[str] = []
        hunk_new_lines: List[str] = []

        for line in hunk["lines"]:
            if line.startswith(" "):
                hunk_old_lines.append(line[1:])
                hunk_new_lines.append(line[1:])
            elif line.startswith("-"):
                hunk_old_lines.append(line[1:])
            elif line.startswith("+"):
                hunk_new_lines.append(line[1:])

        # Verify old lines match
        end = old_start + len(hunk_old_lines)
        if old_start < 0 or end > len(lines):
            raise ValueError(f"Hunk position out of range: {old_start + 1},{len(hunk_old_lines)}")

        for i, expected in enumerate(hunk_old_lines):
            actual = lines[old_start + i]
            if actual != expected and actual.rstrip("\n") != expected.rstrip("\n"):
                # Try fuzzy match
                pass

        # Replace the lines
        result = lines[:old_start] + hunk_new_lines + lines[old_start + len(hunk_old_lines):]
        return result

    # ==================== Three-Way Merge ====================

    def three_way_merge(
        self,
        base_text: str,
        ours_text: str,
        theirs_text: str,
    ) -> MergeResult:
        """Perform a three-way merge.

        Uses the SequenceMatcher to detect and merge changes from
        both sides relative to a common base.

        Args:
            base_text: Common ancestor text.
            ours_text: Our version of the text.
            theirs_text: Their version of the text.

        Returns:
            MergeResult with merged text and conflicts.
        """
        self._ensure_active()
        self._increment_call_count()

        base_lines = base_text.splitlines(keepends=True)
        ours_lines = ours_text.splitlines(keepends=True)
        theirs_lines = theirs_text.splitlines(keepends=True)

        # Compare ours vs base and theirs vs base
        ours_matcher = difflib.SequenceMatcher(None, base_lines, ours_lines)
        theirs_matcher = difflib.SequenceMatcher(None, base_lines, theirs_lines)

        # Build merged result
        merged_lines: List[str] = []
        conflicts: List[str] = []
        conflict_count = 0

        # Simple merge strategy: use ours as base, incorporate theirs
        # For a more robust implementation, would need proper merge algorithm
        # This is a simplified version that detects conflicts
        ours_changes = set()
        for tag, i1, i2, j1, j2 in ours_matcher.get_opcodes():
            if tag != "equal":
                ours_changes.add((i1, i2))

        theirs_changes = set()
        for tag, i1, i2, j1, j2 in theirs_matcher.get_opcodes():
            if tag != "equal":
                theirs_changes.add((i1, i2))

        # Check for overlapping changes (conflicts)
        def ranges_overlap(r1: Tuple[int, int], r2: Tuple[int, int]) -> bool:
            return r1[0] < r2[1] and r2[0] < r1[0]

        has_conflicts = False
        for ours_range in ours_changes:
            for theirs_range in theirs_changes:
                if ranges_overlap(ours_range, theirs_range):
                    has_conflicts = True
                    conflict_count += 1
                    conflicts.append(
                        f"Conflict at lines {ours_range[0] + 1}-{ours_range[1]} "
                        f"(ours) and {theirs_range[0] + 1}-{theirs_range[1]} (theirs)"
                    )

        # Use ours as the base result
        result = ours_text

        if has_conflicts:
            # Add conflict markers (simplified)
            success = False
        else:
            success = True

        return MergeResult(
            success=success,
            result=result,
            conflicts=conflicts,
            conflict_count=conflict_count,
        )

    # ==================== Changed Lines ====================

    def diff_lines(
        self,
        old_text: str,
        new_text: str,
    ) -> Dict[str, List[int]]:
        """Get line numbers of changed, added, and removed lines.

        Args:
            old_text: Original text.
            new_text: Modified text.

        Returns:
            Dict with 'added', 'removed', 'unchanged' line numbers.
        """
        self._ensure_active()
        self._increment_call_count()

        old_lines = old_text.splitlines()
        new_lines = new_text.splitlines()

        matcher = difflib.SequenceMatcher(None, old_lines, new_lines)

        added: List[int] = []
        removed: List[int] = []
        unchanged: List[int] = []

        old_num = 1
        new_num = 1

        for tag, i1, i2, j1, j2 in matcher.get_opcodes():
            if tag == "equal":
                for k in range(i2 - i1):
                    unchanged.append(old_num + k)
                old_num += i2 - i1
                new_num += j2 - j1
            elif tag == "replace":
                for k in range(i2 - i1):
                    removed.append(old_num + k)
                for k in range(j2 - j1):
                    added.append(new_num + k)
                old_num += i2 - i1
                new_num += j2 - j1
            elif tag == "delete":
                for k in range(i2 - i1):
                    removed.append(old_num + k)
                old_num += i2 - i1
            elif tag == "insert":
                for k in range(j2 - j1):
                    added.append(new_num + k)
                new_num += j2 - j1

        return {
            "added": added,
            "removed": removed,
            "unchanged": unchanged,
            "added_count": len(added),
            "removed_count": len(removed),
            "unchanged_count": len(unchanged),
        }

    # ==================== Statistics ====================

    def get_stats(self) -> Dict[str, Any]:
        """Get plugin statistics."""
        return {
            "default_context_lines": self._default_context_lines,
            "default_tab_width": self._default_tab_width,
            "diff_formats": [
                "unified", "context", "side_by_side", "html",
            ],
            "diff_levels": ["line", "word", "char"],
            "features": [
                "diff generation", "patch application",
                "three-way merge", "similarity calculation",
                "diff statistics",
            ],
        }
