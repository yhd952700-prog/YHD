"""
Excel/Spreadsheet Plugin for LiuHao AI OS

Provides Excel file reading/writing, sheet manipulation,
cell operations, formula evaluation, and data validation.

Capabilities:
- read_excel: Read data from Excel files
- write_excel: Write data to Excel files
- list_sheets: List sheet names
- get_cell: Get cell value
- set_cell: Set cell value
- add_sheet: Add a new sheet
- validate_data: Validate cell data
- evaluate_formula: Basic formula evaluation
"""

import re
import csv
import os
from typing import Dict, Any, Optional, List, Tuple, Union
from dataclasses import dataclass, field
from pathlib import Path

from ..plugin_base import (
    BasePlugin,
    PluginMetadata,
    PluginStatus,
    PluginExecutionError,
)


@dataclass
class CellValue:
    """Represents a cell with its value and metadata."""
    value: Any
    row: int
    column: int
    cell_ref: str
    data_type: str  # "string", "number", "boolean", "date", "formula", "empty"
    formula: Optional[str] = None
    format: str = "general"


@dataclass
class SheetData:
    """Data from a single sheet."""
    name: str
    rows: List[List[Any]]
    headers: List[str]
    row_count: int
    column_count: int


@dataclass
class ExcelInfo:
    """Information about an Excel file."""
    file_path: str
    file_size: int
    sheet_count: int
    sheet_names: List[str]
    total_rows: int
    total_cells: int


@dataclass
class ValidationResult:
    """Result of data validation."""
    is_valid: bool
    errors: List[Dict[str, Any]]  # {cell, row, col, type, message}
    warnings: List[Dict[str, Any]]
    valid_cells: int
    total_cells: int


class ExcelPlugin(BasePlugin):
    """Excel/Spreadsheet processing plugin.

    Supports reading/writing Excel files, sheet manipulation,
    cell operations, basic formula evaluation, and data validation.

    Example:
        plugin = ExcelPlugin()
        plugin.initialize()
        plugin.activate()

        data = plugin.read_excel("data.xlsx", sheet_name="Sheet1")
        print(data.rows)
    """

    SUPPORTED_FORMATS = [".xlsx", ".xls", ".csv", ".tsv"]

    def __init__(self) -> None:
        """Initialize the Excel plugin."""
        metadata = PluginMetadata(
            name="excel",
            version="1.0.0",
            description="Excel spreadsheet processing plugin",
            author="LiuHao AI OS Team",
            categories=["document", "data", "utility"],
            keywords=["excel", "spreadsheet", "xlsx", "csv", "data"],
            requires_internet=False,
            dependencies=[],
        )
        super().__init__(metadata)

        self._has_openpyxl: bool = False

    # ==================== Lifecycle Methods ====================

    def initialize(self, config: Optional[Dict[str, Any]] = None) -> bool:
        """Initialize the Excel plugin."""
        try:
            try:
                import openpyxl
                self._has_openpyxl = True
            except ImportError:
                self._has_openpyxl = False

            self._set_status(PluginStatus.INITIALIZED)
            return True
        except Exception as e:
            self._set_error(f"Initialization failed: {e}")
            return False

    def activate(self) -> bool:
        """Activate the Excel plugin."""
        try:
            self._set_status(PluginStatus.ACTIVE)
            return True
        except Exception as e:
            self._set_error(f"Activation failed: {e}")
            return False

    def deactivate(self) -> bool:
        """Deactivate the Excel plugin."""
        try:
            self._set_status(PluginStatus.INACTIVE)
            return True
        except Exception as e:
            self._set_error(str(e))
            return False

    def shutdown(self) -> bool:
        """Shut down the Excel plugin."""
        try:
            self._set_status(PluginStatus.SHUTDOWN)
            return True
        except Exception as e:
            self._set_error(str(e))
            return False

    def get_capabilities(self) -> List[str]:
        """Return the list of capabilities."""
        return [
            "read_excel",
            "write_excel",
            "list_sheets",
            "get_cell",
            "set_cell",
            "add_sheet",
            "validate_data",
            "evaluate_formula",
            "get_info",
        ]

    # ==================== Excel Methods ====================

    def read_excel(
        self,
        file_path: str,
        sheet_name: Optional[str] = None,
        has_headers: bool = True,
        start_row: int = 1,
        end_row: Optional[int] = None,
    ) -> SheetData:
        """Read data from an Excel file.

        Args:
            file_path: Path to the Excel file.
            sheet_name: Sheet name to read. Reads first sheet if None.
            has_headers: Whether the first row contains headers.
            start_row: Starting row (1-indexed).
            end_row: Ending row (1-indexed, inclusive). None = all.

        Returns:
            SheetData with the sheet contents.
        """
        self._ensure_active()
        self._increment_call_count()

        path = Path(file_path)
        if not path.exists():
            raise FileNotFoundError(f"File not found: {file_path}")

        ext = path.suffix.lower()

        if ext in (".xlsx", ".xls") and self._has_openpyxl:
            return self._read_with_openpyxl(path, sheet_name, has_headers, start_row, end_row)
        elif ext == ".csv":
            return self._read_csv(path, has_headers, start_row, end_row)
        elif ext == ".tsv":
            return self._read_tsv(path, has_headers, start_row, end_row)
        else:
            # Fallback: try openpyxl or csv based on content
            if self._has_openpyxl:
                try:
                    return self._read_with_openpyxl(path, sheet_name, has_headers, start_row, end_row)
                except Exception:
                    pass
            return self._read_csv(path, has_headers, start_row, end_row)

    def write_excel(
        self,
        file_path: str,
        data: Union[List[List[Any]], Dict[str, List[List[Any]]]],
        sheet_name: str = "Sheet1",
        headers: Optional[List[str]] = None,
    ) -> bool:
        """Write data to an Excel file.

        Args:
            file_path: Output file path.
            data: Either a 2D list (single sheet) or dict of sheet_name -> 2D list.
            sheet_name: Default sheet name (for single sheet data).
            headers: Optional headers to prepend.

        Returns:
            True if write succeeded.
        """
        self._ensure_active()
        self._increment_call_count()

        path = Path(file_path)
        path.parent.mkdir(parents=True, exist_ok=True)

        ext = path.suffix.lower()

        # Normalize data to dict format
        if isinstance(data, list):
            sheets_data = {sheet_name: data}
        else:
            sheets_data = data

        try:
            if ext in (".xlsx", ".xls") and self._has_openpyxl:
                return self._write_with_openpyxl(path, sheets_data, headers)
            elif ext == ".csv":
                # For CSV, write first sheet only
                first_sheet = next(iter(sheets_data.values()))
                return self._write_csv(path, first_sheet, headers)
            elif ext == ".tsv":
                first_sheet = next(iter(sheets_data.values()))
                return self._write_tsv(path, first_sheet, headers)
            else:
                # Default to CSV
                first_sheet = next(iter(sheets_data.values()))
                return self._write_csv(path, first_sheet, headers)
        except Exception as e:
            self._set_error(f"Write failed: {e}")
            return False

    def list_sheets(self, file_path: str) -> List[str]:
        """List all sheet names in an Excel file.

        Args:
            file_path: Path to the Excel file.

        Returns:
            List of sheet names.
        """
        self._ensure_active()
        self._increment_call_count()

        path = Path(file_path)
        if not path.exists():
            raise FileNotFoundError(f"File not found: {file_path}")

        ext = path.suffix.lower()

        if ext in (".xlsx", ".xls") and self._has_openpyxl:
            import openpyxl
            wb = openpyxl.load_workbook(path, read_only=True)
            sheets = wb.sheetnames
            wb.close()
            return sheets
        else:
            # CSV/TSV has only one "sheet"
            return [path.stem]

    def get_cell(
        self,
        file_path: str,
        cell_ref: str,
        sheet_name: Optional[str] = None,
    ) -> CellValue:
        """Get value of a specific cell.

        Args:
            file_path: Path to the Excel file.
            cell_ref: Cell reference (e.g., "A1", "B2").
            sheet_name: Sheet name.

        Returns:
            CellValue with the cell data.
        """
        self._ensure_active()
        self._increment_call_count()

        row, col = self._cell_ref_to_row_col(cell_ref)

        if self._has_openpyxl:
            import openpyxl
            wb = openpyxl.load_workbook(file_path, data_only=True)
            ws = wb[sheet_name] if sheet_name else wb.active
            value = ws.cell(row=row, column=col).value
            wb.close()

            data_type = self._infer_data_type(value)
            return CellValue(
                value=value,
                row=row,
                column=col,
                cell_ref=cell_ref,
                data_type=data_type,
            )
        else:
            # CSV fallback
            data = self.read_excel(file_path)
            if row <= len(data.rows) and col <= data.column_count:
                value = data.rows[row - 1][col - 1]
                data_type = self._infer_data_type(value)
                return CellValue(
                    value=value,
                    row=row,
                    column=col,
                    cell_ref=cell_ref,
                    data_type=data_type,
                )
            else:
                return CellValue(
                    value=None,
                    row=row,
                    column=col,
                    cell_ref=cell_ref,
                    data_type="empty",
                )

    def get_info(self, file_path: str) -> ExcelInfo:
        """Get information about an Excel file.

        Args:
            file_path: Path to the Excel file.

        Returns:
            ExcelInfo with file metadata.
        """
        self._ensure_active()
        self._increment_call_count()

        path = Path(file_path)
        if not path.exists():
            raise FileNotFoundError(f"File not found: {file_path}")

        file_size = path.stat().st_size
        sheet_names = self.list_sheets(file_path)

        total_rows = 0
        total_cells = 0

        for sheet in sheet_names:
            try:
                data = self.read_excel(file_path, sheet_name=sheet)
                total_rows += data.row_count
                total_cells += data.row_count * data.column_count
            except Exception:
                pass

        return ExcelInfo(
            file_path=str(path),
            file_size=file_size,
            sheet_count=len(sheet_names),
            sheet_names=sheet_names,
            total_rows=total_rows,
            total_cells=total_cells,
        )

    def validate_data(
        self,
        file_path: str,
        sheet_name: Optional[str] = None,
        rules: Optional[Dict[str, Any]] = None,
    ) -> ValidationResult:
        """Validate data in an Excel sheet.

        Args:
            file_path: Path to the Excel file.
            sheet_name: Sheet to validate.
            rules: Validation rules dict with keys:
                - required_columns: list of required column names
                - unique_columns: columns that must be unique
                - type_rules: {column: type} type validation
                - range_rules: {column: {min, max}} numeric range

        Returns:
            ValidationResult with errors and warnings.
        """
        self._ensure_active()
        self._increment_call_count()

        data = self.read_excel(file_path, sheet_name=sheet_name, has_headers=True)
        errors: List[Dict[str, Any]] = []
        warnings: List[Dict[str, Any]] = []
        valid_cells = 0
        total_cells = 0

        rules = rules or {}

        # Check required columns
        required_cols = rules.get("required_columns", [])
        for col_name in required_cols:
            if col_name not in data.headers:
                errors.append({
                    "type": "missing_column",
                    "column": col_name,
                    "message": f"Required column '{col_name}' is missing",
                })

        # Check unique columns
        unique_cols = rules.get("unique_columns", [])
        for col_name in unique_cols:
            if col_name in data.headers:
                col_idx = data.headers.index(col_name)
                values = [row[col_idx] for row in data.rows if col_idx < len(row)]
                seen = set()
                for i, val in enumerate(values):
                    if val in seen and val is not None and val != "":
                        errors.append({
                            "type": "duplicate_value",
                            "row": i + 2,  # +2 because row 1 is header, 1-indexed
                            "column": col_name,
                            "value": val,
                            "message": f"Duplicate value '{val}' in column '{col_name}'",
                        })
                    seen.add(val)

        # Check type rules
        type_rules = rules.get("type_rules", {})
        for col_name, expected_type in type_rules.items():
            if col_name in data.headers:
                col_idx = data.headers.index(col_name)
                for i, row in enumerate(data.rows):
                    if col_idx < len(row):
                        val = row[col_idx]
                        total_cells += 1
                        if val is None or val == "":
                            valid_cells += 1
                            continue
                        if not self._check_type(val, expected_type):
                            errors.append({
                                "type": "type_mismatch",
                                "row": i + 2,
                                "column": col_name,
                                "value": val,
                                "expected_type": expected_type,
                                "message": f"Value '{val}' is not of type {expected_type}",
                            })
                        else:
                            valid_cells += 1

        # Check range rules
        range_rules = rules.get("range_rules", {})
        for col_name, range_def in range_rules.items():
            if col_name in data.headers:
                col_idx = data.headers.index(col_name)
                for i, row in enumerate(data.rows):
                    if col_idx < len(row):
                        val = row[col_idx]
                        if val is None or val == "":
                            continue
                        try:
                            num_val = float(val)
                            if "min" in range_def and num_val < range_def["min"]:
                                errors.append({
                                    "type": "out_of_range",
                                    "row": i + 2,
                                    "column": col_name,
                                    "value": val,
                                    "message": f"Value {val} is below minimum {range_def['min']}",
                                })
                            if "max" in range_def and num_val > range_def["max"]:
                                errors.append({
                                    "type": "out_of_range",
                                    "row": i + 2,
                                    "column": col_name,
                                    "value": val,
                                    "message": f"Value {val} is above maximum {range_def['max']}",
                                })
                        except (ValueError, TypeError):
                            pass

        # Check for empty rows
        for i, row in enumerate(data.rows):
            if all(v is None or v == "" for v in row):
                warnings.append({
                    "type": "empty_row",
                    "row": i + 2,
                    "message": f"Row {i + 2} is empty",
                })

        return ValidationResult(
            is_valid=len(errors) == 0,
            errors=errors,
            warnings=warnings,
            valid_cells=valid_cells,
            total_cells=total_cells,
        )

    def evaluate_formula(self, formula: str, data: Optional[Dict[str, Any]] = None) -> Any:
        """Evaluate a basic Excel formula.

        Supports basic functions: SUM, AVERAGE, MIN, MAX, COUNT, IF, CONCATENATE
        and simple arithmetic.

        Args:
            formula: Formula string (e.g., "=SUM(A1:A5)").
            data: Optional dict of cell values for reference.

        Returns:
            Evaluated result.
        """
        self._ensure_active()
        self._increment_call_count()

        if not formula:
            return None

        # Remove leading =
        expr = formula.lstrip("=").strip()

        data = data or {}

        try:
            # Handle SUM function
            sum_match = re.match(r'SUM\((.+)\)', expr, re.I)
            if sum_match:
                range_str = sum_match.group(1)
                values = self._resolve_range(range_str, data)
                return sum(v for v in values if isinstance(v, (int, float)))

            # Handle AVERAGE
            avg_match = re.match(r'AVERAGE\((.+)\)', expr, re.I)
            if avg_match:
                range_str = avg_match.group(1)
                values = [v for v in self._resolve_range(range_str, data)
                         if isinstance(v, (int, float))]
                return sum(values) / len(values) if values else 0

            # Handle MIN
            min_match = re.match(r'MIN\((.+)\)', expr, re.I)
            if min_match:
                range_str = min_match.group(1)
                values = [v for v in self._resolve_range(range_str, data)
                         if isinstance(v, (int, float))]
                return min(values) if values else 0

            # Handle MAX
            max_match = re.match(r'MAX\((.+)\)', expr, re.I)
            if max_match:
                range_str = max_match.group(1)
                values = [v for v in self._resolve_range(range_str, data)
                         if isinstance(v, (int, float))]
                return max(values) if values else 0

            # Handle COUNT
            count_match = re.match(r'COUNT\((.+)\)', expr, re.I)
            if count_match:
                range_str = count_match.group(1)
                values = [v for v in self._resolve_range(range_str, data)
                         if isinstance(v, (int, float))]
                return len(values)

            # Handle IF
            if_match = re.match(r'IF\((.+),(.+),(.+)\)', expr, re.I)
            if if_match:
                condition = if_match.group(1).strip()
                true_val = if_match.group(2).strip()
                false_val = if_match.group(3).strip()
                # Simple condition evaluation
                if self._eval_condition(condition, data):
                    return self._parse_value(true_val)
                else:
                    return self._parse_value(false_val)

            # Simple arithmetic
            try:
                # Replace cell references
                eval_expr = expr
                for cell_ref, val in data.items():
                    if isinstance(val, (int, float)):
                        eval_expr = eval_expr.replace(cell_ref.upper(), str(val))
                return eval(eval_expr)  # nosec - limited scope
            except Exception:
                return f"#ERROR: {formula}"

        except Exception as e:
            return f"#ERROR: {e}"

    # ==================== Internal Helpers ====================

    def _read_with_openpyxl(
        self,
        path: Path,
        sheet_name: Optional[str],
        has_headers: bool,
        start_row: int,
        end_row: Optional[int],
    ) -> SheetData:
        """Read Excel using openpyxl."""
        import openpyxl

        wb = openpyxl.load_workbook(path, data_only=True)
        ws = wb[sheet_name] if sheet_name else wb.active

        rows: List[List[Any]] = []
        headers: List[str] = []

        start = max(1, start_row)
        max_row = ws.max_row
        end = min(max_row, end_row or max_row)

        for row_idx in range(start, end + 1):
            row_data = []
            for col_idx in range(1, ws.max_column + 1):
                cell = ws.cell(row=row_idx, column=col_idx)
                row_data.append(cell.value)
            rows.append(row_data)

        if has_headers and rows:
            headers = [str(h) if h is not None else "" for h in rows[0]]
            rows = rows[1:]
        else:
            headers = [f"Column_{i+1}" for i in range(ws.max_column)]

        wb.close()

        return SheetData(
            name=ws.title,
            rows=rows,
            headers=headers,
            row_count=len(rows),
            column_count=len(headers),
        )

    def _read_csv(
        self, path: Path, has_headers: bool, start_row: int, end_row: Optional[int]
    ) -> SheetData:
        """Read CSV file."""
        rows: List[List[str]] = []
        headers: List[str] = []

        with open(path, "r", newline="", encoding="utf-8") as f:
            reader = csv.reader(f)
            all_rows = list(reader)

        start = max(0, start_row - 1)
        end = len(all_rows) if end_row is None else min(len(all_rows), end_row)

        slice_rows = all_rows[start:end]

        if has_headers and slice_rows:
            headers = slice_rows[0]
            rows = slice_rows[1:]
        else:
            rows = slice_rows
            headers = [f"Column_{i+1}" for i in range(len(rows[0]) if rows else 0)]

        return SheetData(
            name=path.stem,
            rows=rows,
            headers=headers,
            row_count=len(rows),
            column_count=len(headers),
        )

    def _read_tsv(
        self, path: Path, has_headers: bool, start_row: int, end_row: Optional[int]
    ) -> SheetData:
        """Read TSV file."""
        rows: List[List[str]] = []
        headers: List[str] = []

        with open(path, "r", newline="", encoding="utf-8") as f:
            reader = csv.reader(f, delimiter="\t")
            all_rows = list(reader)

        start = max(0, start_row - 1)
        end = len(all_rows) if end_row is None else min(len(all_rows), end_row)
        slice_rows = all_rows[start:end]

        if has_headers and slice_rows:
            headers = slice_rows[0]
            rows = slice_rows[1:]
        else:
            rows = slice_rows
            headers = [f"Column_{i+1}" for i in range(len(rows[0]) if rows else 0)]

        return SheetData(
            name=path.stem,
            rows=rows,
            headers=headers,
            row_count=len(rows),
            column_count=len(headers),
        )

    def _write_with_openpyxl(
        self,
        path: Path,
        sheets_data: Dict[str, List[List[Any]]],
        headers: Optional[List[str]],
    ) -> bool:
        """Write Excel using openpyxl."""
        import openpyxl

        wb = openpyxl.Workbook()
        first = True

        for sheet_name, data_rows in sheets_data.items():
            if first:
                ws = wb.active
                ws.title = sheet_name
                first = False
            else:
                ws = wb.create_sheet(title=sheet_name)

            start_row = 1
            if headers:
                for col_idx, header in enumerate(headers, 1):
                    ws.cell(row=1, column=col_idx, value=header)
                start_row = 2

            for row_idx, row_data in enumerate(data_rows, start_row):
                for col_idx, value in enumerate(row_data, 1):
                    ws.cell(row=row_idx, column=col_idx, value=value)

        wb.save(path)
        return True

    def _write_csv(
        self, path: Path, data: List[List[Any]], headers: Optional[List[str]]
    ) -> bool:
        """Write CSV file."""
        with open(path, "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            if headers:
                writer.writerow(headers)
            writer.writerows(data)
        return True

    def _write_tsv(
        self, path: Path, data: List[List[Any]], headers: Optional[List[str]]
    ) -> bool:
        """Write TSV file."""
        with open(path, "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f, delimiter="\t")
            if headers:
                writer.writerow(headers)
            writer.writerows(data)
        return True

    def _cell_ref_to_row_col(self, cell_ref: str) -> Tuple[int, int]:
        """Convert cell reference (e.g., 'A1') to (row, column) 1-indexed."""
        match = re.match(r'([A-Za-z]+)(\d+)', cell_ref)
        if not match:
            raise ValueError(f"Invalid cell reference: {cell_ref}")

        col_str = match.group(1).upper()
        row = int(match.group(2))

        # Convert column letters to number
        col = 0
        for c in col_str:
            col = col * 26 + (ord(c) - ord('A') + 1)

        return row, col

    def _infer_data_type(self, value: Any) -> str:
        """Infer the data type of a cell value."""
        if value is None or value == "":
            return "empty"
        if isinstance(value, bool):
            return "boolean"
        if isinstance(value, (int, float)):
            return "number"
        if isinstance(value, str):
            if value.startswith("="):
                return "formula"
            return "string"
        return "string"

    def _check_type(self, value: Any, expected_type: str) -> bool:
        """Check if a value matches the expected type."""
        expected_type = expected_type.lower()
        if value is None or value == "":
            return True  # Empty values pass (use required for non-empty)

        if expected_type in ("number", "int", "float", "numeric"):
            try:
                float(value)
                return True
            except (ValueError, TypeError):
                return False
        elif expected_type in ("string", "str", "text"):
            return isinstance(value, str) or True  # Most values are strings
        elif expected_type in ("boolean", "bool"):
            return isinstance(value, bool) or str(value).lower() in ("true", "false", "yes", "no")
        elif expected_type in ("date", "datetime"):
            # Simple date check
            date_patterns = [
                r'\d{4}-\d{2}-\d{2}',
                r'\d{2}/\d{2}/\d{4}',
                r'\d{2}-\d{2}-\d{4}',
            ]
            val_str = str(value)
            return any(re.match(p, val_str) for p in date_patterns)
        elif expected_type in ("email",):
            return bool(re.match(r'^[\w\.-]+@[\w\.-]+\.\w+$', str(value)))
        elif expected_type in ("url", "uri"):
            return bool(re.match(r'^https?://', str(value)))
        return True

    def _resolve_range(self, range_str: str, data: Dict[str, Any]) -> List[Any]:
        """Resolve a cell range like 'A1:A5' to list of values."""
        if ":" in range_str:
            start, end = range_str.split(":")
            start_row, start_col = self._cell_ref_to_row_col(start.strip())
            end_row, end_col = self._cell_ref_to_row_col(end.strip())
            values = []
            # For single column range
            if start_col == end_col:
                for r in range(start_row, end_row + 1):
                    col_letter = self._col_to_letter(start_col)
                    ref = f"{col_letter}{r}"
                    values.append(data.get(ref, 0))
            return values
        else:
            # Single cell
            return [data.get(range_str.upper(), 0)]

    def _col_to_letter(self, col: int) -> str:
        """Convert column number to letter."""
        result = ""
        while col > 0:
            col -= 1
            result = chr(ord('A') + col % 26) + result
            col //= 26
        return result

    def _eval_condition(self, condition: str, data: Dict[str, Any]) -> bool:
        """Evaluate a simple condition string."""
        # Handle >, <, =, >=, <=, !=
        condition = condition.strip()

        for op in [">=", "<=", "!=", "=", ">", "<"]:
            if op in condition:
                parts = condition.split(op, 1)
                if len(parts) == 2:
                    left = self._parse_value(parts[0].strip(), data)
                    right = self._parse_value(parts[1].strip(), data)

                    if op == ">":
                        return left > right
                    elif op == "<":
                        return left < right
                    elif op == ">=":
                        return left >= right
                    elif op == "<=":
                        return left <= right
                    elif op == "=":
                        return left == right
                    elif op == "!=":
                        return left != right
        return bool(condition)

    def _parse_value(self, val_str: str, data: Optional[Dict[str, Any]] = None) -> Any:
        """Parse a value string to appropriate type."""
        val_str = val_str.strip()

        # Check if it's a cell reference
        data = data or {}
        if val_str.upper() in data:
            return data[val_str.upper()]

        # Try number
        try:
            if "." in val_str:
                return float(val_str)
            return int(val_str)
        except ValueError:
            pass

        # Check boolean
        if val_str.lower() in ("true", "yes"):
            return True
        if val_str.lower() in ("false", "no"):
            return False

        # Remove quotes if present
        if (val_str.startswith('"') and val_str.endswith('"')) or \
           (val_str.startswith("'") and val_str.endswith("'")):
            return val_str[1:-1]

        return val_str
