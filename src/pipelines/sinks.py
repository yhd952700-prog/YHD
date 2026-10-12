"""
Data sink (destination) stages for LiuHao AI OS pipelines.

Provides various output destinations for pipeline results.
Each sink supports batch writing, flushing, and cleanup.

Sinks:
    - FileSink: Write to file (txt, csv, json)
    - DatabaseSink: Write to SQL database
    - APISink: Send data to REST API
    - MemorySink: Store in memory system
    - ConsoleSink: Print to console
    - MultiSink: Write to multiple sinks simultaneously
"""

from __future__ import annotations

import csv
import io
import json
import logging
import os
import sys
from abc import abstractmethod
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, TextIO

from .base import PipelineContext, PipelineError, PipelineStage

logger = logging.getLogger(__name__)

Record = Dict[str, Any]
Records = List[Record]


@dataclass
class SinkStats:
    """Statistics for a sink write operation.

    Attributes:
        records_written: Total records successfully written.
        records_failed: Records that failed to write.
        bytes_written: Total bytes written (if applicable).
        batches: Number of batches written.
    """

    records_written: int = 0
    records_failed: int = 0
    bytes_written: int = 0
    batches: int = 0


class BaseSink(PipelineStage[Records, Records]):
    """Abstract base class for all data sinks.

    Data sinks are pipeline stages that write data to an external
    destination. They pass their input through as output, so they
    can be placed mid-pipeline for tee/branch scenarios.

    Subclasses must implement: open(), write_batch(), flush(), close().
    """

    def __init__(
        self,
        name: Optional[str] = None,
        description: Optional[str] = None,
    ) -> None:
        super().__init__(name=name, description=description)
        self._stats = SinkStats()
        self._is_open = False
        self._buffer: Records = []

    @property
    def stats(self) -> SinkStats:
        """Statistics from the most recent write operation."""
        return self._stats

    def process(self, data: Records, context: PipelineContext) -> Records:
        """Write data to the sink and pass it through.

        Args:
            data: Records to write.
            context: Pipeline context.

        Returns:
            The same records (pass-through).
        """
        self._stats = SinkStats()
        records = data or []

        try:
            self.open(context)
            if records:
                self.write_batch(records, context)
            self.flush()
        finally:
            self.close()

        context.set_variable(f"{self.name}_stats", self._stats)
        return records  # Pass through

    @abstractmethod
    def open(self, context: Optional[PipelineContext] = None) -> None:
        """Open the sink for writing.

        Args:
            context: Optional pipeline context.
        """
        ...

    @abstractmethod
    def write_batch(
        self, records: Records, context: Optional[PipelineContext] = None
    ) -> int:
        """Write a batch of records.

        Args:
            records: List of records to write.
            context: Optional pipeline context.

        Returns:
            Number of records successfully written.
        """
        ...

    @abstractmethod
    def flush(self) -> None:
        """Flush any buffered data to the destination."""
        ...

    @abstractmethod
    def close(self) -> None:
        """Close the sink and release resources."""
        ...


class FileSink(BaseSink):
    """Write records to a file in various formats.

    Supported formats: txt, csv, json, jsonl.

    Attributes:
        file_path: Path to the output file.
        file_format: Output format ('auto', 'txt', 'csv', 'json', 'jsonl').
        encoding: File encoding.
        mode: File write mode ('w' for overwrite, 'a' for append).
        csv_delimiter: Delimiter for CSV output.
        indent: JSON indentation (None for compact).
    """

    SUPPORTED_FORMATS = {"auto", "txt", "csv", "json", "jsonl"}

    def __init__(
        self,
        file_path: str,
        file_format: str = "auto",
        encoding: str = "utf-8",
        mode: str = "w",
        csv_delimiter: str = ",",
        indent: Optional[int] = None,
        name: Optional[str] = None,
        description: Optional[str] = None,
    ) -> None:
        super().__init__(name=name or f"FileSink({os.path.basename(file_path)})",
                         description=description)
        self.file_path = file_path
        self.file_format = file_format.lower()
        self.encoding = encoding
        self.mode = mode
        self.csv_delimiter = csv_delimiter
        self.indent = indent
        self._file: Optional[TextIO] = None
        self._csv_writer: Optional[csv.DictWriter] = None
        self._header_written = False

    def _detect_format(self) -> str:
        """Detect file format from extension."""
        if self.file_format != "auto":
            if self.file_format not in self.SUPPORTED_FORMATS:
                raise PipelineError(
                    f"Unsupported file format: {self.file_format}",
                    stage_name=self.name,
                )
            return self.file_format

        ext = os.path.splitext(self.file_path)[1].lower().lstrip(".")
        fmt_map = {
            "txt": "txt",
            "csv": "csv",
            "json": "json",
            "jsonl": "jsonl",
            "ndjson": "jsonl",
        }
        detected = fmt_map.get(ext)
        if detected is None:
            raise PipelineError(
                f"Cannot detect format from extension '{ext}'",
                stage_name=self.name,
            )
        return detected

    def open(self, context: Optional[PipelineContext] = None) -> None:
        """Open the output file."""
        self._stats = SinkStats()
        self._header_written = False

        # Ensure directory exists
        directory = os.path.dirname(self.file_path)
        if directory and not os.path.exists(directory):
            os.makedirs(directory, exist_ok=True)

        self._file = open(self.file_path, mode=self.mode, encoding=self.encoding, newline="")
        self._is_open = True

    def write_batch(
        self, records: Records, context: Optional[PipelineContext] = None
    ) -> int:
        """Write a batch of records to the file."""
        if not self._is_open or not self._file:
            raise PipelineError("FileSink is not open", stage_name=self.name)

        fmt = self._detect_format()
        written = 0

        try:
            if fmt == "txt":
                written = self._write_txt(records)
            elif fmt == "csv":
                written = self._write_csv(records)
            elif fmt == "json":
                written = self._write_json(records)
            elif fmt == "jsonl":
                written = self._write_jsonl(records)

            self._stats.batches += 1
            return written
        except Exception as e:
            self._stats.records_failed += len(records)
            raise PipelineError(
                f"Failed to write to file: {e}",
                stage_name=self.name,
                original_error=e,
            ) from e

    def _write_txt(self, records: Records) -> int:
        """Write records as text lines."""
        assert self._file is not None
        count = 0
        for record in records:
            if isinstance(record, dict) and "content" in record:
                line = str(record["content"])
            else:
                line = str(record)
            self._file.write(line + "\n")
            count += 1
        self._stats.records_written += count
        return count

    def _write_csv(self, records: Records) -> int:
        """Write records as CSV rows."""
        assert self._file is not None
        if not records:
            return 0

        fieldnames = list(records[0].keys())
        if self._csv_writer is None:
            self._csv_writer = csv.DictWriter(
                self._file, fieldnames=fieldnames, delimiter=self.csv_delimiter
            )
            if not self._header_written:
                self._csv_writer.writeheader()
                self._header_written = True

        count = 0
        for record in records:
            self._csv_writer.writerow(record)
            count += 1
        self._stats.records_written += count
        return count

    def _write_json(self, records: Records) -> int:
        """Write records as a JSON array."""
        assert self._file is not None
        content = json.dumps(records, indent=self.indent, ensure_ascii=False)
        self._file.write(content)
        self._stats.records_written += len(records)
        self._stats.bytes_written += len(content.encode(self.encoding))
        return len(records)

    def _write_jsonl(self, records: Records) -> int:
        """Write records as JSON Lines (one object per line)."""
        assert self._file is not None
        count = 0
        for record in records:
            line = json.dumps(record, ensure_ascii=False)
            self._file.write(line + "\n")
            count += 1
            self._stats.bytes_written += len(line.encode(self.encoding)) + 1
        self._stats.records_written += count
        return count

    def flush(self) -> None:
        """Flush the file buffer."""
        if self._file:
            self._file.flush()

    def close(self) -> None:
        """Close the file."""
        if self._file and not self._file.closed:
            self._file.close()
        self._file = None
        self._csv_writer = None
        self._is_open = False


class DatabaseSink(BaseSink):
    """Write records to a SQL database table.

    Attributes:
        table_name: Name of the target table.
        connection_factory: Callable returning a DB connection.
        connection_string: SQLite connection string (alternative to factory).
        batch_size: Number of records per insert batch.
    """

    def __init__(
        self,
        table_name: str,
        connection_factory: Optional[Any] = None,
        connection_string: Optional[str] = None,
        batch_size: int = 100,
        name: Optional[str] = None,
        description: Optional[str] = None,
    ) -> None:
        super().__init__(name=name or f"DatabaseSink({table_name})",
                         description=description)
        self.table_name = table_name
        self.connection_factory = connection_factory
        self.connection_string = connection_string
        self.batch_size = batch_size
        self._connection = None
        self._cursor = None

    def open(self, context: Optional[PipelineContext] = None) -> None:
        """Open database connection."""
        self._stats = SinkStats()

        if self.connection_factory:
            self._connection = self.connection_factory()
        elif self.connection_string:
            try:
                import sqlite3
                self._connection = sqlite3.connect(self.connection_string)
            except ImportError as e:
                raise PipelineError(
                    "sqlite3 not available for DatabaseSink",
                    stage_name=self.name,
                    original_error=e,
                ) from e
        else:
            raise PipelineError(
                "DatabaseSink requires connection_factory or connection_string",
                stage_name=self.name,
            )

        self._cursor = self._connection.cursor()
        self._is_open = True

    def write_batch(
        self, records: Records, context: Optional[PipelineContext] = None
    ) -> int:
        """Write a batch of records to the database."""
        if not self._is_open or not self._connection or not self._cursor:
            raise PipelineError("DatabaseSink is not open", stage_name=self.name)

        if not records:
            return 0

        total_written = 0
        for i in range(0, len(records), self.batch_size):
            batch = records[i : i + self.batch_size]
            try:
                written = self._insert_batch(batch)
                total_written += written
                self._stats.batches += 1
            except Exception as e:
                self._stats.records_failed += len(batch)
                raise PipelineError(
                    f"Database insert failed: {e}",
                    stage_name=self.name,
                    original_error=e,
                ) from e

        self._stats.records_written += total_written
        return total_written

    def _insert_batch(self, records: Records) -> int:
        """Insert a single batch of records."""
        assert self._cursor is not None
        if not records:
            return 0

        columns = list(records[0].keys())
        placeholders = ", ".join(["?"] * len(columns))
        column_list = ", ".join(columns)
        sql = f"INSERT INTO {self.table_name} ({column_list}) VALUES ({placeholders})"

        values = [tuple(r.get(col) for col in columns) for r in records]
        self._cursor.executemany(sql, values)
        self._connection.commit()
        return len(records)

    def flush(self) -> None:
        """Commit any pending transactions."""
        if self._connection:
            try:
                self._connection.commit()
            except Exception:
                pass

    def close(self) -> None:
        """Close the database connection."""
        if self._cursor:
            try:
                self._cursor.close()
            except Exception:
                pass
            self._cursor = None
        if self._connection:
            try:
                self._connection.close()
            except Exception:
                pass
            self._connection = None
        self._is_open = False


class APISink(BaseSink):
    """Send records to a REST API endpoint.

    Attributes:
        url: API endpoint URL.
        method: HTTP method (POST, PUT, PATCH).
        headers: HTTP headers.
        batch_mode: Whether to send all records in one request or individually.
        timeout: Request timeout in seconds.
    """

    def __init__(
        self,
        url: str,
        method: str = "POST",
        headers: Optional[Dict[str, str]] = None,
        batch_mode: bool = True,
        timeout: int = 30,
        name: Optional[str] = None,
        description: Optional[str] = None,
    ) -> None:
        super().__init__(name=name or f"APISink({url[:50]})", description=description)
        self.url = url
        self.method = method.upper()
        self.headers = headers or {}
        self.batch_mode = batch_mode
        self.timeout = timeout

    def open(self, context: Optional[PipelineContext] = None) -> None:
        """Initialize the API sink."""
        self._stats = SinkStats()
        self._is_open = True

    def write_batch(
        self, records: Records, context: Optional[PipelineContext] = None
    ) -> int:
        """Send records to the API."""
        if not self._is_open:
            raise PipelineError("APISink is not open", stage_name=self.name)

        try:
            import requests
        except ImportError as e:
            raise PipelineError(
                "requests library is required for APISink",
                stage_name=self.name,
                original_error=e,
            ) from e

        try:
            if self.batch_mode:
                payload = records
                response = requests.request(
                    method=self.method,
                    url=self.url,
                    headers=self.headers,
                    json=payload,
                    timeout=self.timeout,
                )
                response.raise_for_status()
                written = len(records)
            else:
                written = 0
                for record in records:
                    response = requests.request(
                        method=self.method,
                        url=self.url,
                        headers=self.headers,
                        json=record,
                        timeout=self.timeout,
                    )
                    if response.ok:
                        written += 1
                    else:
                        self._stats.records_failed += 1

            self._stats.records_written += written
            self._stats.batches += 1
            return written

        except Exception as e:
            self._stats.records_failed += len(records) if self.batch_mode else 1
            raise PipelineError(
                f"API request failed: {e}",
                stage_name=self.name,
                original_error=e,
            ) from e

    def flush(self) -> None:
        """No-op for API sink (data is sent immediately)."""
        pass

    def close(self) -> None:
        """Close the API sink."""
        self._is_open = False


class MemorySink(BaseSink):
    """Store records in an in-memory list.

    Useful for testing, accumulating results, or building
    intermediate data stores in a pipeline.

    Attributes:
        store: Optional external list to write to.
    """

    def __init__(
        self,
        store: Optional[Records] = None,
        name: Optional[str] = None,
        description: Optional[str] = None,
    ) -> None:
        super().__init__(name=name or "MemorySink", description=description)
        self._external_store = store
        self._internal_store: Records = []

    @property
    def data(self) -> Records:
        """The data stored in this sink."""
        return self._external_store if self._external_store is not None else self._internal_store

    def open(self, context: Optional[PipelineContext] = None) -> None:
        """Initialize the memory sink."""
        self._stats = SinkStats()
        if self._external_store is None:
            self._internal_store.clear()
        self._is_open = True

    def write_batch(
        self, records: Records, context: Optional[PipelineContext] = None
    ) -> int:
        """Write records to memory."""
        if not self._is_open:
            raise PipelineError("MemorySink is not open", stage_name=self.name)

        store = self._external_store if self._external_store is not None else self._internal_store
        store.extend(records)
        self._stats.records_written += len(records)
        self._stats.batches += 1
        return len(records)

    def flush(self) -> None:
        """No-op for memory sink."""
        pass

    def close(self) -> None:
        """Close the memory sink."""
        self._is_open = False

    def clear(self) -> None:
        """Clear all stored data."""
        if self._external_store is not None:
            self._external_store.clear()
        else:
            self._internal_store.clear()


class ConsoleSink(BaseSink):
    """Print records to the console (stdout).

    Attributes:
        format: Output format ('json', 'table', 'line').
        pretty_print: Whether to pretty-print JSON output.
        stream: Output stream (stdout or stderr).
    """

    def __init__(
        self,
        output_format: str = "json",
        pretty_print: bool = True,
        stream: str = "stdout",
        name: Optional[str] = None,
        description: Optional[str] = None,
    ) -> None:
        super().__init__(name=name or "ConsoleSink", description=description)
        self.output_format = output_format
        self.pretty_print = pretty_print
        self._stream_name = stream
        self._stream: Optional[TextIO] = None

    def open(self, context: Optional[PipelineContext] = None) -> None:
        """Initialize the console sink."""
        self._stats = SinkStats()
        self._stream = sys.stdout if self._stream_name == "stdout" else sys.stderr
        self._is_open = True

    def write_batch(
        self, records: Records, context: Optional[PipelineContext] = None
    ) -> int:
        """Write records to the console."""
        if not self._is_open or not self._stream:
            raise PipelineError("ConsoleSink is not open", stage_name=self.name)

        if self.output_format == "json":
            indent = 2 if self.pretty_print else None
            output = json.dumps(records, indent=indent, ensure_ascii=False)
            print(output, file=self._stream)
        elif self.output_format == "table":
            self._print_table(records)
        elif self.output_format == "line":
            for record in records:
                print(json.dumps(record, ensure_ascii=False), file=self._stream)
        else:
            for record in records:
                print(record, file=self._stream)

        self._stats.records_written += len(records)
        self._stats.batches += 1
        return len(records)

    def _print_table(self, records: Records) -> None:
        """Print records as a simple table."""
        assert self._stream is not None
        if not records:
            print("(empty)", file=self._stream)
            return

        columns = list(records[0].keys())
        col_widths = {col: len(col) for col in columns}

        for record in records:
            for col in columns:
                val_len = len(str(record.get(col, "")))
                if val_len > col_widths[col]:
                    col_widths[col] = min(val_len, 50)  # Cap at 50 chars

        # Header
        header = " | ".join(col.ljust(col_widths[col]) for col in columns)
        print(header, file=self._stream)
        print("-" * len(header), file=self._stream)

        # Rows
        for record in records:
            row = " | ".join(
                str(record.get(col, ""))[: col_widths[col]].ljust(col_widths[col])
                for col in columns
            )
            print(row, file=self._stream)

    def flush(self) -> None:
        """Flush the output stream."""
        if self._stream:
            self._stream.flush()

    def close(self) -> None:
        """Close the console sink (does not actually close stdout/stderr)."""
        self._is_open = False
        self._stream = None


class MultiSink(BaseSink):
    """Write to multiple sinks simultaneously.

    Fan-out pattern: each record is written to all child sinks.
    If any sink fails, the error is tracked but other sinks continue.

    Attributes:
        sinks: List of sink instances to write to.
        fail_fast: If True, raise on first sink failure. If False, continue.
    """

    def __init__(
        self,
        sinks: List[BaseSink],
        fail_fast: bool = False,
        name: Optional[str] = None,
        description: Optional[str] = None,
    ) -> None:
        super().__init__(name=name or "MultiSink", description=description)
        self.sinks = sinks
        self.fail_fast = fail_fast

    def open(self, context: Optional[PipelineContext] = None) -> None:
        """Open all child sinks."""
        self._stats = SinkStats()
        errors = []
        for sink in self.sinks:
            try:
                sink.open(context)
            except Exception as e:
                errors.append(str(e))
                if self.fail_fast:
                    raise PipelineError(
                        f"Failed to open sink '{sink.name}': {e}",
                        stage_name=self.name,
                        original_error=e,
                    ) from e
        if errors:
            logger.warning(f"MultiSink: some sinks failed to open: {errors}")
        self._is_open = True

    def write_batch(
        self, records: Records, context: Optional[PipelineContext] = None
    ) -> int:
        """Write records to all child sinks."""
        if not self._is_open:
            raise PipelineError("MultiSink is not open", stage_name=self.name)

        total_written = 0
        for sink in self.sinks:
            try:
                written = sink.write_batch(records, context)
                total_written = max(total_written, written)
            except Exception as e:
                self._stats.records_failed += len(records)
                if self.fail_fast:
                    raise PipelineError(
                        f"Sink '{sink.name}' failed: {e}",
                        stage_name=self.name,
                        original_error=e,
                    ) from e
                logger.error(f"MultiSink: sink '{sink.name}' failed: {e}")

        self._stats.records_written += total_written
        self._stats.batches += 1
        return total_written

    def flush(self) -> None:
        """Flush all child sinks."""
        for sink in self.sinks:
            try:
                sink.flush()
            except Exception as e:
                logger.error(f"MultiSink: flush failed for '{sink.name}': {e}")

    def close(self) -> None:
        """Close all child sinks."""
        for sink in self.sinks:
            try:
                sink.close()
            except Exception as e:
                logger.error(f"MultiSink: close failed for '{sink.name}': {e}")
        self._is_open = False
