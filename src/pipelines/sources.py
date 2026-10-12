"""
Data source stages for LiuHao AI OS pipelines.

Provides various data source implementations that can be used as the
entry point of a pipeline. Each source supports both batch and
incremental reading patterns.

Sources:
    - FileSource: Read from text, CSV, JSON, XML files
    - DatabaseSource: Read from SQL databases
    - APISource: Read from REST APIs
    - StreamSource: Streaming data interface
    - MemorySource: Read from in-memory data structures
"""

from __future__ import annotations

import csv
import io
import json
import logging
import os
import xml.etree.ElementTree as ET
from abc import abstractmethod
from dataclasses import dataclass, field
from typing import Any, Dict, Iterator, List, Optional

from .base import PipelineContext, PipelineError, PipelineStage

logger = logging.getLogger(__name__)


@dataclass
class SourceStats:
    """Statistics for a data source read operation.

    Attributes:
        records_read: Total number of records read.
        bytes_read: Total bytes read (if applicable).
        errors: Number of read errors encountered.
        skipped: Number of records skipped.
    """

    records_read: int = 0
    bytes_read: int = 0
    errors: int = 0
    skipped: int = 0


class BaseSource(PipelineStage[None, List[Dict[str, Any]]]):
    """Abstract base class for all data sources.

    Data sources are pipeline stages that produce data from an external
    source. They ignore their input (which is typically None or initial
    context) and output a list of records (dicts).

    Subclasses must implement: read(), read_batch(), has_more(), close().
    """

    def __init__(
        self,
        name: Optional[str] = None,
        batch_size: int = 100,
        description: Optional[str] = None,
    ) -> None:
        super().__init__(name=name, description=description)
        self.batch_size = batch_size
        self._stats = SourceStats()
        self._closed = True  # Start closed; open() sets to False
        self._opened = False  # Whether open() has been called at least once
        self._buffer: List[Dict[str, Any]] = []

    @property
    def stats(self) -> SourceStats:
        """Statistics from the most recent read operation."""
        return self._stats

    def process(self, data: Any, context: PipelineContext) -> List[Dict[str, Any]]:
        """Read all data from the source.

        Args:
            data: Input data (ignored for sources).
            context: Pipeline context.

        Returns:
            List of records read from the source.
        """
        self._stats = SourceStats()
        self._buffer.clear()
        result: List[Dict[str, Any]] = []

        try:
            self.open(context)
            while self.has_more():
                batch = self.read_batch(self.batch_size, context)
                result.extend(batch)
        finally:
            self.close()

        context.set_variable(f"{self.name}_stats", self._stats)
        return result

    @abstractmethod
    def open(self, context: Optional[PipelineContext] = None) -> None:
        """Open the source for reading.

        Args:
            context: Optional pipeline context.
        """
        ...

    @abstractmethod
    def read(self, context: Optional[PipelineContext] = None) -> List[Dict[str, Any]]:
        """Read all available data at once.

        Args:
            context: Optional pipeline context.

        Returns:
            List of records.
        """
        ...

    @abstractmethod
    def read_batch(
        self, size: int, context: Optional[PipelineContext] = None
    ) -> List[Dict[str, Any]]:
        """Read a batch of records.

        Args:
            size: Maximum number of records to read.
            context: Optional pipeline context.

        Returns:
            List of up to `size` records.
        """
        ...

    @abstractmethod
    def has_more(self) -> bool:
        """Check if there is more data to read.

        Returns:
            True if more data is available.
        """
        ...

    @abstractmethod
    def close(self) -> None:
        """Close the source and release resources."""
        ...

    def __iter__(self) -> Iterator[Dict[str, Any]]:
        """Iterate over records one at a time.

        Yields:
            Individual records from the source.
        """
        try:
            self.open()
            while self.has_more():
                batch = self.read_batch(self.batch_size)
                for record in batch:
                    yield record
        finally:
            self.close()


class FileSource(BaseSource):
    """Read data from files in various formats.

    Supported formats: txt (line-based), csv, json, jsonl, xml.

    Attributes:
        file_path: Path to the source file.
        file_format: Format of the file ('auto', 'txt', 'csv', 'json', 'jsonl', 'xml').
        encoding: File encoding.
        csv_delimiter: Delimiter character for CSV files.
        json_path: JSONPath-like selector for nested JSON data.
    """

    SUPPORTED_FORMATS = {"auto", "txt", "csv", "json", "jsonl", "xml"}

    def __init__(
        self,
        file_path: str,
        file_format: str = "auto",
        encoding: str = "utf-8",
        csv_delimiter: str = ",",
        csv_has_header: bool = True,
        json_path: Optional[str] = None,
        name: Optional[str] = None,
        batch_size: int = 100,
        description: Optional[str] = None,
    ) -> None:
        super().__init__(name=name or f"FileSource({os.path.basename(file_path)})",
                         batch_size=batch_size, description=description)
        self.file_path = file_path
        self.file_format = file_format.lower()
        self.encoding = encoding
        self.csv_delimiter = csv_delimiter
        self.csv_has_header = csv_has_header
        self.json_path = json_path
        self._file: Optional[io.TextIOBase] = None
        self._reader: Optional[Iterator[Dict[str, Any]]] = None
        self._line_num = 0

    def _detect_format(self) -> str:
        """Detect file format from extension."""
        if self.file_format != "auto":
            if self.file_format not in self.SUPPORTED_FORMATS:
                raise PipelineError(
                    f"Unsupported file format: {self.file_format}. "
                    f"Supported: {self.SUPPORTED_FORMATS}",
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
            "xml": "xml",
        }
        detected = fmt_map.get(ext)
        if detected is None:
            raise PipelineError(
                f"Cannot detect format from extension '{ext}'. "
                f"Please specify file_format explicitly.",
                stage_name=self.name,
            )
        return detected

    def open(self, context: Optional[PipelineContext] = None) -> None:
        """Open the file for reading."""
        if self._closed:
            self._closed = False
        self._line_num = 0
        self._stats = SourceStats()

        if not os.path.exists(self.file_path):
            raise PipelineError(
                f"File not found: {self.file_path}",
                stage_name=self.name,
            )

        self._file = open(self.file_path, mode="r", encoding=self.encoding)
        fmt = self._detect_format()

        if fmt == "txt":
            self._reader = self._read_txt()
        elif fmt == "csv":
            self._reader = self._read_csv()
        elif fmt == "json":
            self._reader = self._read_json()
        elif fmt == "jsonl":
            self._reader = self._read_jsonl()
        elif fmt == "xml":
            self._reader = self._read_xml()

    def _read_txt(self) -> Iterator[Dict[str, Any]]:
        """Read text file line by line."""
        assert self._file is not None
        for line in self._file:
            self._line_num += 1
            line = line.rstrip("\n\r")
            if line:
                self._stats.records_read += 1
                yield {"_line": self._line_num, "content": line}

    def _read_csv(self) -> Iterator[Dict[str, Any]]:
        """Read CSV file row by row."""
        assert self._file is not None
        reader = csv.DictReader(self._file, delimiter=self.csv_delimiter)
        for row in reader:
            self._line_num += 1
            self._stats.records_read += 1
            yield dict(row)

    def _read_json(self) -> Iterator[Dict[str, Any]]:
        """Read JSON file. Expects a list of objects or a single object."""
        assert self._file is not None
        content = self._file.read()
        self._stats.bytes_read = len(content.encode(self.encoding))

        try:
            data = json.loads(content)
        except json.JSONDecodeError as e:
            raise PipelineError(
                f"Invalid JSON in {self.file_path}: {e}",
                stage_name=self.name,
                original_error=e,
            ) from e

        records = self._extract_json_data(data)
        for record in records:
            self._stats.records_read += 1
            yield record if isinstance(record, dict) else {"value": record}

    def _extract_json_data(self, data: Any) -> List[Any]:
        """Extract records from JSON data based on json_path."""
        if self.json_path:
            # Simple JSONPath-like selector: e.g., "data.items"
            keys = self.json_path.split(".")
            current = data
            for key in keys:
                if isinstance(current, dict) and key in current:
                    current = current[key]
                else:
                    return []
            return current if isinstance(current, list) else [current]

        if isinstance(data, list):
            return data
        return [data]

    def _read_jsonl(self) -> Iterator[Dict[str, Any]]:
        """Read JSON Lines file (one JSON object per line)."""
        assert self._file is not None
        for line in self._file:
            self._line_num += 1
            line = line.strip()
            if not line:
                continue
            try:
                record = json.loads(line)
                self._stats.records_read += 1
                yield record if isinstance(record, dict) else {"value": record}
            except json.JSONDecodeError as e:
                self._stats.errors += 1
                logger.warning(f"Invalid JSON on line {self._line_num}: {e}")

    def _read_xml(self) -> Iterator[Dict[str, Any]]:
        """Read XML file, converting child elements to dict records."""
        assert self._file is not None
        content = self._file.read()
        self._stats.bytes_read = len(content.encode(self.encoding))

        try:
            root = ET.fromstring(content)
        except ET.ParseError as e:
            raise PipelineError(
                f"Invalid XML in {self.file_path}: {e}",
                stage_name=self.name,
                original_error=e,
            ) from e

        # Treat each direct child of root as a record
        for child in root:
            record = self._xml_element_to_dict(child)
            self._stats.records_read += 1
            yield record

    def _xml_element_to_dict(self, element: ET.Element) -> Dict[str, Any]:
        """Convert an XML element to a dictionary."""
        result: Dict[str, Any] = {}
        result.update(element.attrib)

        children = list(element)
        if children:
            for child in children:
                child_data = self._xml_element_to_dict(child)
                if child.tag in result:
                    if not isinstance(result[child.tag], list):
                        result[child.tag] = [result[child.tag]]
                    result[child.tag].append(child_data)
                else:
                    result[child.tag] = child_data
        elif element.text and element.text.strip():
            result["_text"] = element.text.strip()

        result["_tag"] = element.tag
        return result

    def read(self, context: Optional[PipelineContext] = None) -> List[Dict[str, Any]]:
        """Read all records from the file."""
        result: List[Dict[str, Any]] = []
        if not self._file:
            self.open(context)
        assert self._reader is not None
        if self._buffer:
            result.extend(self._buffer)
            self._buffer.clear()
        for record in self._reader:
            result.append(record)
        return result

    def read_batch(
        self, size: int, context: Optional[PipelineContext] = None
    ) -> List[Dict[str, Any]]:
        """Read a batch of records from the file."""
        if not self._file:
            self.open(context)
        assert self._reader is not None
        batch: List[Dict[str, Any]] = []
        while self._buffer and len(batch) < size:
            batch.append(self._buffer.pop(0))
        try:
            for _ in range(size - len(batch)):
                batch.append(next(self._reader))
        except StopIteration:
            pass
        return batch

    def has_more(self) -> bool:
        """Check if there are more records to read."""
        if not self._file or self._closed:
            return False
        if self._buffer:
            return True
        if self._reader is None:
            return False
        try:
            self._buffer.append(next(self._reader))
            return True
        except StopIteration:
            return False

    def close(self) -> None:
        """Close the file."""
        if self._file and not self._file.closed:
            self._file.close()
        self._file = None
        self._reader = None
        self._closed = True


class DatabaseSource(BaseSource):
    """Read data from a SQL database.

    Provides a generic SQL-based data source interface.
    Actual database connection details are provided via a connection
    function or connection string.

    Attributes:
        query: SQL query to execute.
        connection_factory: Callable that returns a DB connection.
        params: Optional query parameters.
    """

    def __init__(
        self,
        query: str,
        connection_factory: Optional[Any] = None,
        connection_string: Optional[str] = None,
        params: Optional[Dict[str, Any]] = None,
        name: Optional[str] = None,
        batch_size: int = 100,
        description: Optional[str] = None,
    ) -> None:
        super().__init__(name=name or "DatabaseSource",
                         batch_size=batch_size, description=description)
        self.query = query
        self.connection_factory = connection_factory
        self.connection_string = connection_string
        self.params = params or {}
        self._connection = None
        self._cursor = None
        self._result_iter: Optional[Iterator[Dict[str, Any]]] = None

    def open(self, context: Optional[PipelineContext] = None) -> None:
        """Open the database connection and execute the query."""
        self._stats = SourceStats()
        self._closed = False

        if self.connection_factory:
            self._connection = self.connection_factory()
        elif self.connection_string:
            # Lazy import to avoid hard dependency
            try:
                import sqlite3
                self._connection = sqlite3.connect(self.connection_string)
                self._connection.row_factory = sqlite3.Row
            except ImportError as e:
                raise PipelineError(
                    "sqlite3 not available for DatabaseSource",
                    stage_name=self.name,
                    original_error=e,
                ) from e
        else:
            raise PipelineError(
                "DatabaseSource requires either connection_factory or connection_string",
                stage_name=self.name,
            )

        try:
            self._cursor = self._connection.cursor()
            self._cursor.execute(self.query, self.params)
            self._result_iter = self._fetch_results()
        except Exception as e:
            raise PipelineError(
                f"Database query failed: {e}",
                stage_name=self.name,
                original_error=e,
            ) from e

    def _fetch_results(self) -> Iterator[Dict[str, Any]]:
        """Fetch results as an iterator of dicts."""
        assert self._cursor is not None
        columns = [desc[0] for desc in self._cursor.description] if self._cursor.description else []

        for row in self._cursor:
            if hasattr(row, "keys"):
                record = dict(row)
            else:
                record = dict(zip(columns, row))
            self._stats.records_read += 1
            yield record

    def read(self, context: Optional[PipelineContext] = None) -> List[Dict[str, Any]]:
        """Read all results from the query."""
        if not self._connection:
            self.open(context)
        assert self._result_iter is not None
        result: List[Dict[str, Any]] = []
        if self._buffer:
            result.extend(self._buffer)
            self._buffer.clear()
        result.extend(self._result_iter)
        return result

    def read_batch(
        self, size: int, context: Optional[PipelineContext] = None
    ) -> List[Dict[str, Any]]:
        """Read a batch of results."""
        if not self._connection:
            self.open(context)
        assert self._result_iter is not None
        batch: List[Dict[str, Any]] = []
        while self._buffer and len(batch) < size:
            batch.append(self._buffer.pop(0))
        try:
            for _ in range(size - len(batch)):
                batch.append(next(self._result_iter))
        except StopIteration:
            pass
        return batch

    def has_more(self) -> bool:
        """Check if more results are available."""
        if not self._connection or self._closed:
            return False
        if self._buffer:
            return True
        if self._result_iter is None:
            return False
        try:
            self._buffer.append(next(self._result_iter))
            return True
        except StopIteration:
            return False

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
        self._result_iter = None
        self._closed = True


class APISource(BaseSource):
    """Read data from a REST API endpoint.

    Supports pagination via offset/limit or page/per_page patterns.

    Attributes:
        url: API endpoint URL.
        method: HTTP method (GET, POST).
        headers: HTTP headers.
        params: Query parameters.
        json_body: JSON body for POST requests.
        pagination: Pagination config dict.
    """

    def __init__(
        self,
        url: str,
        method: str = "GET",
        headers: Optional[Dict[str, str]] = None,
        params: Optional[Dict[str, Any]] = None,
        json_body: Optional[Dict[str, Any]] = None,
        pagination: Optional[Dict[str, Any]] = None,
        data_key: Optional[str] = None,
        name: Optional[str] = None,
        batch_size: int = 100,
        description: Optional[str] = None,
    ) -> None:
        super().__init__(name=name or f"APISource({url[:50]})",
                         batch_size=batch_size, description=description)
        self.url = url
        self.method = method.upper()
        self.headers = headers or {}
        self.params = params or {}
        self.json_body = json_body
        self.pagination = pagination or {}
        self.data_key = data_key
        self._results: List[Dict[str, Any]] = []
        self._current_index = 0
        self._page = 0
        self._total_fetched = 0

    def open(self, context: Optional[PipelineContext] = None) -> None:
        """Initialize the API source."""
        self._stats = SourceStats()
        self._closed = False
        self._results.clear()
        self._current_index = 0
        self._page = 0
        self._total_fetched = 0
        # Fetch first page
        self._fetch_page()

    def _fetch_page(self) -> None:
        """Fetch the next page of results from the API."""
        try:
            import requests
        except ImportError as e:
            raise PipelineError(
                "requests library is required for APISource",
                stage_name=self.name,
                original_error=e,
            ) from e

        request_params = dict(self.params)

        if self.pagination:
            page_param = self.pagination.get("page_param", "page")
            per_page_param = self.pagination.get("per_page_param", "per_page")
            per_page = self.pagination.get("per_page", self.batch_size)
            request_params[page_param] = self._page
            request_params[per_page_param] = per_page

        try:
            response = requests.request(
                method=self.method,
                url=self.url,
                headers=self.headers,
                params=request_params if self.method == "GET" else None,
                json=self.json_body if self.method == "POST" else None,
                timeout=self.pagination.get("timeout", 30),
            )
            response.raise_for_status()
            data = response.json()
        except Exception as e:
            raise PipelineError(
                f"API request failed: {e}",
                stage_name=self.name,
                original_error=e,
            ) from e

        records = self._extract_records(data)
        self._stats.bytes_read += len(response.content)
        self._results.extend(records)
        self._stats.records_read += len(records)
        self._page += 1

    def _extract_records(self, data: Any) -> List[Dict[str, Any]]:
        """Extract records from API response data."""
        if self.data_key:
            keys = self.data_key.split(".")
            current = data
            for key in keys:
                if isinstance(current, dict) and key in current:
                    current = current[key]
                else:
                    return []
            data = current

        if isinstance(data, list):
            return [r if isinstance(r, dict) else {"value": r} for r in data]
        if isinstance(data, dict):
            return [data]
        return []

    def _has_more_pages(self) -> bool:
        """Check if there are more pages to fetch."""
        if not self.pagination:
            return False
        max_pages = self.pagination.get("max_pages")
        if max_pages and self._page >= max_pages:
            return False
        # Assume there's more if we got a full page last time
        return len(self._results) >= self._total_fetched + self.batch_size

    def read(self, context: Optional[PipelineContext] = None) -> List[Dict[str, Any]]:
        """Read all records from the API."""
        if self._closed:
            self.open(context)

        while self._has_more_pages():
            self._fetch_page()

        all_results = list(self._results)
        self._current_index = len(all_results)
        return all_results

    def read_batch(
        self, size: int, context: Optional[PipelineContext] = None
    ) -> List[Dict[str, Any]]:
        """Read a batch of records from the API."""
        if self._closed:
            self.open(context)

        # Fetch more if needed
        while (
            self._current_index + size > len(self._results)
            and self._has_more_pages()
        ):
            self._fetch_page()

        batch = self._results[self._current_index : self._current_index + size]
        self._current_index += len(batch)
        return batch

    def has_more(self) -> bool:
        """Check if there is more data to read."""
        if self._closed:
            return False
        if self._current_index < len(self._results):
            return True
        return self._has_more_pages()

    def close(self) -> None:
        """Close the API source."""
        self._closed = True


class StreamSource(BaseSource):
    """Streaming data source for continuous data feeds.

    Wraps any iterator/generator as a pipeline source,
    supporting continuous data consumption.

    Attributes:
        iterator: The iterator to read from.
    """

    def __init__(
        self,
        iterator: Iterator[Dict[str, Any]],
        name: Optional[str] = None,
        batch_size: int = 100,
        description: Optional[str] = None,
    ) -> None:
        super().__init__(name=name or "StreamSource",
                         batch_size=batch_size, description=description)
        self._iterator = iterator
        self._has_more_data = True

    def open(self, context: Optional[PipelineContext] = None) -> None:
        """Initialize the stream source."""
        self._stats = SourceStats()
        self._closed = False
        self._has_more_data = True

    def read(self, context: Optional[PipelineContext] = None) -> List[Dict[str, Any]]:
        """Read all available data (dangerous for infinite streams!)."""
        if self._closed:
            self.open(context)
        result: List[Dict[str, Any]] = []
        for record in self._iterator:
            self._stats.records_read += 1
            result.append(record)
        self._has_more_data = False
        return result

    def read_batch(
        self, size: int, context: Optional[PipelineContext] = None
    ) -> List[Dict[str, Any]]:
        """Read a batch of records from the stream."""
        if self._closed:
            self.open(context)

        batch: List[Dict[str, Any]] = []
        try:
            for _ in range(size):
                record = next(self._iterator)
                self._stats.records_read += 1
                batch.append(record)
        except StopIteration:
            self._has_more_data = False
        return batch

    def has_more(self) -> bool:
        """Check if the stream has more data."""
        return self._has_more_data and not self._closed

    def close(self) -> None:
        """Close the stream source."""
        self._closed = True
        self._has_more_data = False


class MemorySource(BaseSource):
    """Data source backed by in-memory data structures.

    Useful for testing, demo pipelines, and feeding pre-loaded
    data into a pipeline.

    Attributes:
        data: The in-memory data to serve as source.
    """

    def __init__(
        self,
        data: Any,
        name: Optional[str] = None,
        batch_size: int = 100,
        description: Optional[str] = None,
    ) -> None:
        super().__init__(name=name or "MemorySource",
                         batch_size=batch_size, description=description)
        self._source_data = data
        self._records: List[Dict[str, Any]] = []
        self._position = 0

    def open(self, context: Optional[PipelineContext] = None) -> None:
        """Initialize the memory source."""
        self._stats = SourceStats()
        self._closed = False
        self._position = 0
        self._records = self._normalize_data(self._source_data)
        self._stats.records_read = len(self._records)

    def _normalize_data(self, data: Any) -> List[Dict[str, Any]]:
        """Normalize input data to a list of dicts."""
        if data is None:
            return []
        if isinstance(data, list):
            result = []
            for item in data:
                if isinstance(item, dict):
                    result.append(item)
                else:
                    result.append({"value": item})
            return result
        if isinstance(data, dict):
            return [data]
        return [{"value": data}]

    def read(self, context: Optional[PipelineContext] = None) -> List[Dict[str, Any]]:
        """Read all records."""
        if self._closed:
            self.open(context)
        self._position = len(self._records)
        return list(self._records)

    def read_batch(
        self, size: int, context: Optional[PipelineContext] = None
    ) -> List[Dict[str, Any]]:
        """Read a batch of records."""
        if self._closed:
            self.open(context)

        end = min(self._position + size, len(self._records))
        batch = self._records[self._position:end]
        self._position = end
        return batch

    def has_more(self) -> bool:
        """Check if there are more records."""
        return not self._closed and self._position < len(self._records)

    def close(self) -> None:
        """Close the memory source."""
        self._closed = True
