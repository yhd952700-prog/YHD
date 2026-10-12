"""
Markdown Table Plugin for LiuHao AI OS

Provides markdown table generation from data, parsing markdown tables,
formatting/alignment, adding/removing rows and columns, sorting,
and CSV to markdown table conversion.

Capabilities:
- generate: Generate markdown table from data
- parse: Parse markdown table to data
- format: Format/align a markdown table
- add_row: Add a row to a markdown table
- add_column: Add a column to a markdown table
- remove_row: Remove a row from a markdown table
- remove_column: Remove a column from a markdown table
- sort_table: Sort a markdown table by column
- csv_to_md: Convert CSV to markdown table
- md_to_csv: Convert markdown table to CSV
- transpose: Transpose rows and columns
"""

import re
import csv
import io
from typing import Dict, Any, Optional, List, Tuple, Union
from dataclasses import dataclass, field

from ..plugin_base import (
    BasePlugin,
    PluginMetadata,
    PluginStatus,
    PluginExecutionError,
)


@dataclass
class TableData:
    """Parsed markdown table data."""
    headers: List[str]
    rows: List[List[str]]
    alignments: List[str]
    row_count: int
    column_count: int


@dataclass
class TableGenerationResult:
    """Result of table generation."""
    markdown: str
    row_count: int
    column_count: int
    width: int


class MarkdownTablePlugin(BasePlugin):
    """Markdown table tools plugin.

    Provides table generation, parsing, formatting, and manipulation
    for Markdown tables.

    Example:
        plugin = MarkdownTablePlugin()
        plugin.initialize()
        plugin.activate()

        table = plugin.generate(["Name", "Age"], [["Alice", "30"], ["Bob", "25"]])
        print(table.markdown)
    """

    ALIGNMENT_LEFT = "left"
    ALIGNMENT_RIGHT = "right"
    ALIGNMENT_CENTER = "center"
    ALIGNMENT_NONE = "none"

    def __init__(self) -> None:
        """Initialize the markdown table plugin."""
        metadata = PluginMetadata(
            name="markdown_table",
            version="1.0.0",
            description="Markdown table generation, parsing, and manipulation",
            author="LiuHao AI OS Team",
            categories=["text", "utility", "markdown"],
            keywords=["markdown", "table", "csv", "format", "data"],
            requires_internet=False,
            dependencies=[],
        )
        super().__init__(metadata)

        self._default_alignments: List[str] = []
        self._min_col_width: int = 3
        self._padding: int = 1

    # ==================== Lifecycle Methods ====================

    def initialize(self, config: Optional[Dict[str, Any]] = None) -> bool:
        """Initialize the markdown table plugin."""
        try:
            if config:
                self._default_alignments = config.get("default_alignments", [])
                self._min_col_width = config.get("min_col_width", 3)
                self._padding = config.get("padding", 1)

            self._set_status(PluginStatus.INITIALIZED)
            return True
        except Exception as e:
            self._set_error(f"Initialization failed: {e}")
            return False

    def activate(self) -> bool:
        """Activate the markdown table plugin."""
        try:
            self._set_status(PluginStatus.ACTIVE)
            return True
        except Exception as e:
            self._set_error(f"Activation failed: {e}")
            return False

    def deactivate(self) -> bool:
        """Deactivate the markdown table plugin."""
        try:
            self._set_status(PluginStatus.INACTIVE)
            return True
        except Exception as e:
            self._set_error(str(e))
            return False

    def shutdown(self) -> bool:
        """Shut down the markdown table plugin."""
        try:
            self._set_status(PluginStatus.SHUTDOWN)
            return True
        except Exception as e:
            self._set_error(str(e))
            return False

    def get_capabilities(self) -> List[str]:
        """Return the list of capabilities."""
        return [
            "generate",
            "parse",
            "format",
            "add_row",
            "add_column",
            "remove_row",
            "remove_column",
            "sort_table",
            "csv_to_md",
            "md_to_csv",
            "transpose",
            "align",
        ]

    # ==================== Generation ====================

    def generate(
        self,
        headers: List[str],
        rows: List[List[Any]],
        *,
        alignments: Optional[List[str]] = None,
        min_width: Optional[int] = None,
    ) -> TableGenerationResult:
        """Generate a markdown table from data.

        Args:
            headers: List of column headers.
            rows: List of row data.
            alignments: List of alignment types (left, right, center).
            min_width: Minimum column width.

        Returns:
            TableGenerationResult with markdown table.
        """
        self._ensure_active()
        self._increment_call_count()

        min_w = min_width or self._min_col_width
        num_cols = len(headers)

        # Convert all values to strings
        str_rows = []
        for row in rows:
            str_row = [str(cell) for cell in row]
            # Pad or truncate to match header count
            if len(str_row) < num_cols:
                str_row += [""] * (num_cols - len(str_row))
            elif len(str_row) > num_cols:
                str_row = str_row[:num_cols]
            str_rows.append(str_row)

        # Determine alignments
        aligns = alignments or self._default_alignments
        if len(aligns) < num_cols:
            aligns = aligns + [self.ALIGNMENT_NONE] * (num_cols - len(aligns))
        aligns = aligns[:num_cols]

        # Calculate column widths
        col_widths = []
        for col_idx in range(num_cols):
            max_width = max(
                len(headers[col_idx]),
                max((len(row[col_idx]) for row in str_rows), default=0),
                min_w,
            )
            col_widths.append(max_width)

        # Build the table
        lines: List[str] = []

        # Header row
        header_cells = []
        for i, header in enumerate(headers):
            cell = self._pad_cell(header, col_widths[i], aligns[i])
            header_cells.append(cell)
        lines.append("|" + "|".join(header_cells) + "|")

        # Separator row
        sep_cells = []
        for i in range(num_cols):
            sep_cells.append(self._separator_cell(col_widths[i], aligns[i]))
        lines.append("|" + "|".join(sep_cells) + "|")

        # Data rows
        for row in str_rows:
            row_cells = []
            for i, cell in enumerate(row):
                row_cells.append(self._pad_cell(cell, col_widths[i], aligns[i]))
            lines.append("|" + "|".join(row_cells) + "|")

        markdown = "\n".join(lines) + "\n"

        return TableGenerationResult(
            markdown=markdown,
            row_count=len(rows),
            column_count=num_cols,
            width=len(lines[0]) if lines else 0,
        )

    def _pad_cell(self, content: str, width: int, alignment: str) -> str:
        """Pad a cell content to width with specified alignment."""
        pad = self._padding
        total_pad = pad * 2
        content_width = width - total_pad

        if alignment == self.ALIGNMENT_LEFT:
            padded = content.ljust(content_width)
        elif alignment == self.ALIGNMENT_RIGHT:
            padded = content.rjust(content_width)
        elif alignment == self.ALIGNMENT_CENTER:
            padded = content.center(content_width)
        else:
            padded = content.ljust(content_width)

        return " " * pad + padded + " " * pad

    def _separator_cell(self, width: int, alignment: str) -> str:
        """Generate a separator cell with alignment markers."""
        pad = self._padding
        content_width = width - pad * 2

        if alignment == self.ALIGNMENT_LEFT:
            dashes = ":" + "-" * (content_width - 1)
        elif alignment == self.ALIGNMENT_RIGHT:
            dashes = "-" * (content_width - 1) + ":"
        elif alignment == self.ALIGNMENT_CENTER:
            if content_width >= 3:
                dashes = ":" + "-" * (content_width - 2) + ":"
            else:
                dashes = ":" * content_width
        else:
            dashes = "-" * content_width

        return " " * pad + dashes + " " * pad

    # ==================== Parsing ====================

    def parse(self, markdown_table: str) -> TableData:
        """Parse a markdown table into structured data.

        Args:
            markdown_table: The markdown table string.

        Returns:
            TableData with parsed headers, rows, and alignments.
        """
        self._ensure_active()
        self._increment_call_count()

        lines = markdown_table.strip().split("\n")
        lines = [line.strip() for line in lines if line.strip()]

        if len(lines) < 2:
            raise ValueError("Invalid markdown table: need at least header and separator rows")

        # Parse header
        headers = self._parse_row(lines[0])

        # Parse separator for alignment
        separator_cells = self._parse_row(lines[1])
        alignments = []
        for cell in separator_cells:
            cell_clean = cell.strip()
            left_colon = cell_clean.startswith(":")
            right_colon = cell_clean.endswith(":")

            if left_colon and right_colon:
                alignments.append(self.ALIGNMENT_CENTER)
            elif right_colon:
                alignments.append(self.ALIGNMENT_RIGHT)
            elif left_colon:
                alignments.append(self.ALIGNMENT_LEFT)
            else:
                alignments.append(self.ALIGNMENT_NONE)

        # Parse data rows
        rows = []
        for line in lines[2:]:
            if line.strip():
                row = self._parse_row(line)
                # Ensure row has same number of cells as headers
                while len(row) < len(headers):
                    row.append("")
                rows.append(row[:len(headers)])

        return TableData(
            headers=headers,
            rows=rows,
            alignments=alignments,
            row_count=len(rows),
            column_count=len(headers),
        )

    def _parse_row(self, line: str) -> List[str]:
        """Parse a single table row line."""
        line = line.strip()

        # Remove leading and trailing pipes
        if line.startswith("|"):
            line = line[1:]
        if line.endswith("|"):
            line = line[:-1]

        cells = []
        current = []
        in_pipe = False

        for char in line:
            if char == "|":
                cells.append("".join(current).strip())
                current = []
            else:
                current.append(char)

        cells.append("".join(current).strip())
        return cells

    # ==================== Formatting ====================

    def format(
        self,
        markdown_table: str,
        *,
        alignments: Optional[List[str]] = None,
    ) -> str:
        """Format/beautify a markdown table.

        Args:
            markdown_table: The markdown table to format.
            alignments: New alignments (keep current if None).

        Returns:
            Formatted markdown table string.
        """
        self._ensure_active()
        self._increment_call_count()

        table_data = self.parse(markdown_table)

        # Use provided alignments or keep existing
        if alignments:
            table_data.alignments = alignments + table_data.alignments[len(alignments):]

        result = self.generate(
            table_data.headers,
            table_data.rows,
            alignments=table_data.alignments,
        )

        return result.markdown

    def align(
        self,
        markdown_table: str,
        column: Union[int, str],
        alignment: str,
    ) -> str:
        """Set alignment for a specific column.

        Args:
            markdown_table: The markdown table.
            column: Column index (0-based) or header name.
            alignment: Alignment type (left, right, center, none).

        Returns:
            Table with updated alignment.
        """
        self._ensure_active()
        self._increment_call_count()

        table_data = self.parse(markdown_table)

        # Find column index
        if isinstance(column, int):
            col_idx = column
        else:
            try:
                col_idx = table_data.headers.index(column)
            except ValueError:
                raise ValueError(f"Column '{column}' not found")

        if col_idx < 0 or col_idx >= table_data.column_count:
            raise ValueError(f"Column index {col_idx} out of range")

        table_data.alignments[col_idx] = alignment

        result = self.generate(
            table_data.headers,
            table_data.rows,
            alignments=table_data.alignments,
        )

        return result.markdown

    # ==================== Row Operations ====================

    def add_row(
        self,
        markdown_table: str,
        row: List[Any],
        *,
        position: Optional[int] = None,
    ) -> str:
        """Add a row to a markdown table.

        Args:
            markdown_table: The markdown table.
            row: Row data to add.
            position: Insert position (0-based, None = append at end).

        Returns:
            Table with added row.
        """
        self._ensure_active()
        self._increment_call_count()

        table_data = self.parse(markdown_table)
        str_row = [str(cell) for cell in row]

        # Pad row to match column count
        while len(str_row) < table_data.column_count:
            str_row.append("")
        str_row = str_row[:table_data.column_count]

        if position is None or position >= table_data.row_count:
            table_data.rows.append(str_row)
        elif position <= 0:
            table_data.rows.insert(0, str_row)
        else:
            table_data.rows.insert(position, str_row)

        result = self.generate(
            table_data.headers,
            table_data.rows,
            alignments=table_data.alignments,
        )

        return result.markdown

    def remove_row(
        self,
        markdown_table: str,
        row_index: int,
    ) -> str:
        """Remove a row from a markdown table.

        Args:
            markdown_table: The markdown table.
            row_index: Index of row to remove (0-based).

        Returns:
            Table with row removed.
        """
        self._ensure_active()
        self._increment_call_count()

        table_data = self.parse(markdown_table)

        if row_index < 0 or row_index >= table_data.row_count:
            raise ValueError(f"Row index {row_index} out of range")

        table_data.rows.pop(row_index)

        result = self.generate(
            table_data.headers,
            table_data.rows,
            alignments=table_data.alignments,
        )

        return result.markdown

    # ==================== Column Operations ====================

    def add_column(
        self,
        markdown_table: str,
        header: str,
        *,
        values: Optional[List[Any]] = None,
        position: Optional[int] = None,
        alignment: str = "left",
        default_value: str = "",
    ) -> str:
        """Add a column to a markdown table.

        Args:
            markdown_table: The markdown table.
            header: Column header text.
            values: Column values for each row.
            position: Insert position (0-based, None = append at end).
            alignment: Column alignment.
            default_value: Default value for rows without value.

        Returns:
            Table with added column.
        """
        self._ensure_active()
        self._increment_call_count()

        table_data = self.parse(markdown_table)
        str_values = [str(v) for v in (values or [])]

        # Pad values to match row count
        while len(str_values) < table_data.row_count:
            str_values.append(default_value)
        str_values = str_values[:table_data.row_count]

        if position is None or position >= table_data.column_count:
            table_data.headers.append(header)
            table_data.alignments.append(alignment)
            for i, row in enumerate(table_data.rows):
                row.append(str_values[i])
        elif position <= 0:
            table_data.headers.insert(0, header)
            table_data.alignments.insert(0, alignment)
            for i, row in enumerate(table_data.rows):
                row.insert(0, str_values[i])
        else:
            table_data.headers.insert(position, header)
            table_data.alignments.insert(position, alignment)
            for i, row in enumerate(table_data.rows):
                row.insert(position, str_values[i])

        result = self.generate(
            table_data.headers,
            table_data.rows,
            alignments=table_data.alignments,
        )

        return result.markdown

    def remove_column(
        self,
        markdown_table: str,
        column: Union[int, str],
    ) -> str:
        """Remove a column from a markdown table.

        Args:
            markdown_table: The markdown table.
            column: Column index or header name.

        Returns:
            Table with column removed.
        """
        self._ensure_active()
        self._increment_call_count()

        table_data = self.parse(markdown_table)

        # Find column index
        if isinstance(column, int):
            col_idx = column
        else:
            try:
                col_idx = table_data.headers.index(column)
            except ValueError:
                raise ValueError(f"Column '{column}' not found")

        if col_idx < 0 or col_idx >= table_data.column_count:
            raise ValueError(f"Column index {col_idx} out of range")

        # Remove header and alignment
        table_data.headers.pop(col_idx)
        table_data.alignments.pop(col_idx)

        # Remove from each row
        for row in table_data.rows:
            if col_idx < len(row):
                row.pop(col_idx)

        result = self.generate(
            table_data.headers,
            table_data.rows,
            alignments=table_data.alignments,
        )

        return result.markdown

    # ==================== Sorting ====================

    def sort_table(
        self,
        markdown_table: str,
        column: Union[int, str],
        *,
        descending: bool = False,
        numeric: bool = False,
    ) -> str:
        """Sort a markdown table by a column.

        Args:
            markdown_table: The markdown table.
            column: Column index or header name to sort by.
            descending: Sort in descending order.
            numeric: Treat values as numbers.

        Returns:
            Sorted table.
        """
        self._ensure_active()
        self._increment_call_count()

        table_data = self.parse(markdown_table)

        # Find column index
        if isinstance(column, int):
            col_idx = column
        else:
            try:
                col_idx = table_data.headers.index(column)
            except ValueError:
                raise ValueError(f"Column '{column}' not found")

        if col_idx < 0 or col_idx >= table_data.column_count:
            raise ValueError(f"Column index {col_idx} out of range")

        def sort_key(row: List[str]) -> Any:
            value = row[col_idx] if col_idx < len(row) else ""
            if numeric:
                try:
                    return float(value)
                except (ValueError, TypeError):
                    return 0
            return value.lower()

        table_data.rows.sort(key=sort_key, reverse=descending)

        result = self.generate(
            table_data.headers,
            table_data.rows,
            alignments=table_data.alignments,
        )

        return result.markdown

    # ==================== CSV Conversion ====================

    def csv_to_md(
        self,
        csv_text: str,
        *,
        has_header: bool = True,
        delimiter: str = ",",
        alignments: Optional[List[str]] = None,
    ) -> str:
        """Convert CSV text to a markdown table.

        Args:
            csv_text: CSV text.
            has_header: Whether first row is header.
            delimiter: CSV delimiter.
            alignments: Column alignments.

        Returns:
            Markdown table string.
        """
        self._ensure_active()
        self._increment_call_count()

        reader = csv.reader(io.StringIO(csv_text), delimiter=delimiter)
        all_rows = list(reader)

        if not all_rows:
            return ""

        if has_header:
            headers = all_rows[0]
            rows = all_rows[1:]
        else:
            num_cols = len(all_rows[0]) if all_rows else 0
            headers = [f"Column {i + 1}" for i in range(num_cols)]
            rows = all_rows

        result = self.generate(headers, rows, alignments=alignments)
        return result.markdown

    def md_to_csv(
        self,
        markdown_table: str,
        *,
        delimiter: str = ",",
    ) -> str:
        """Convert a markdown table to CSV.

        Args:
            markdown_table: The markdown table.
            delimiter: CSV delimiter.

        Returns:
            CSV string.
        """
        self._ensure_active()
        self._increment_call_count()

        table_data = self.parse(markdown_table)

        output = io.StringIO()
        writer = csv.writer(output, delimiter=delimiter)

        writer.writerow(table_data.headers)
        for row in table_data.rows:
            writer.writerow(row)

        return output.getvalue()

    # ==================== Transpose ====================

    def transpose(self, markdown_table: str) -> str:
        """Transpose a markdown table (rows become columns).

        Args:
            markdown_table: The markdown table.

        Returns:
            Transposed table.
        """
        self._ensure_active()
        self._increment_call_count()

        table_data = self.parse(markdown_table)

        # First column becomes headers
        new_headers = [row[0] if row else "" for row in table_data.rows]

        # Headers become first column
        new_rows: List[List[str]] = []
        for col_idx in range(1, table_data.column_count):
            new_row = [table_data.headers[col_idx]]
            for row in table_data.rows:
                new_row.append(row[col_idx] if col_idx < len(row) else "")
            new_rows.append(new_row)

        # Use first row's alignment for new columns (simplified)
        new_alignments = [self.ALIGNMENT_NONE] + table_data.alignments[1:] \
            if len(table_data.alignments) > 1 else [self.ALIGNMENT_NONE]

        result = self.generate(
            new_headers,
            new_rows,
            alignments=new_alignments[:len(new_headers)],
        )

        return result.markdown

    # ==================== Table Info ====================

    def get_info(self, markdown_table: str) -> Dict[str, Any]:
        """Get information about a markdown table.

        Args:
            markdown_table: The markdown table.

        Returns:
            Dict with table statistics.
        """
        self._ensure_active()
        self._increment_call_count()

        table_data = self.parse(markdown_table)

        # Calculate column widths
        col_widths = []
        for i in range(table_data.column_count):
            header_width = len(table_data.headers[i])
            max_cell = max(
                (len(row[i]) for row in table_data.rows if i < len(row)),
                default=0,
            )
            col_widths.append(max(header_width, max_cell))

        return {
            "row_count": table_data.row_count,
            "column_count": table_data.column_count,
            "headers": table_data.headers,
            "alignments": table_data.alignments,
            "column_widths": col_widths,
            "total_width": sum(col_widths) + (table_data.column_count + 1) * 2 + table_data.column_count + 1,
            "cells": table_data.row_count * table_data.column_count,
            "empty_cells": sum(
                1 for row in table_data.rows
                for cell in row if cell.strip() == ""
            ),
        }

    # ==================== Statistics ====================

    def get_stats(self) -> Dict[str, Any]:
        """Get plugin statistics."""
        return {
            "default_alignments_count": len(self._default_alignments),
            "min_col_width": self._min_col_width,
            "padding": self._padding,
            "supported_alignments": ["left", "right", "center", "none"],
            "features": [
                "generate", "parse", "format", "sort",
                "add/remove rows", "add/remove columns",
                "csv conversion", "transpose",
            ],
        }
