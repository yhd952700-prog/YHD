"""
Data aggregation stages for LiuHao AI OS pipelines.

Provides various aggregation stages that compute summary statistics
and grouped aggregations over record sets.

Aggregators:
    - SumAggregator: Compute sum of numeric fields
    - AverageAggregator: Compute average of numeric fields
    - CountAggregator: Count records (optionally by group)
    - MinMaxAggregator: Compute min and max values
    - GroupByAggregator: Generic group-by with multiple aggregations
    - PercentileAggregator: Compute percentiles
    - MovingAverageAggregator: Rolling/moving average
    - WindowedAggregator: Tumbling/sliding window aggregation
"""

from __future__ import annotations

import logging
import math
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Callable, Deque, Dict, List, Optional, Tuple, Union

from .base import PipelineContext, PipelineError, PipelineStage

logger = logging.getLogger(__name__)

Record = Dict[str, Any]
Records = List[Record]


@dataclass
class AggregationResult:
    """Result of an aggregation operation.

    Attributes:
        field: Name of the aggregated field.
        value: The computed aggregate value.
        count: Number of records used in the computation.
        aggregation: Name of the aggregation function.
    """

    field: str
    value: Any
    count: int
    aggregation: str


class BaseAggregator(PipelineStage[Records, Records]):
    """Base class for all aggregation stages.

    Aggregators take a list of records and produce one or more
    summary records.
    """

    def __init__(
        self,
        name: Optional[str] = None,
        description: Optional[str] = None,
    ) -> None:
        super().__init__(name=name, description=description)
        self._results: List[AggregationResult] = []

    @property
    def results(self) -> List[AggregationResult]:
        """Aggregation results from the last run."""
        return self._results

    def process(self, data: Records, context: PipelineContext) -> Records:
        """Aggregate input records.

        Args:
            data: Input records.
            context: Pipeline context.

        Returns:
            Aggregated result records.
        """
        self._results = []
        records = data or []
        result = self.aggregate(records, context)
        context.set_variable(f"{self.name}_results", self._results)
        return result

    def aggregate(self, records: Records, context: PipelineContext) -> Records:
        """Core aggregation logic. Override in subclasses.

        Args:
            records: Input records.
            context: Pipeline context.

        Returns:
            Aggregated records.
        """
        raise NotImplementedError

    def _extract_numeric_values(self, records: Records, field: str) -> List[float]:
        """Extract numeric values from a field, skipping non-numeric entries.

        Args:
            records: List of records.
            field: Field name to extract.

        Returns:
            List of float values.
        """
        values: List[float] = []
        for record in records:
            val = record.get(field)
            if val is None:
                continue
            try:
                values.append(float(val))
            except (ValueError, TypeError):
                pass
        return values


class SumAggregator(BaseAggregator):
    """Compute the sum of numeric fields.

    Attributes:
        field: Field name to sum.
        group_by: Optional field to group by before summing.
        output_field: Name of the output field (default: '{field}_sum').
    """

    def __init__(
        self,
        field: str,
        group_by: Optional[str] = None,
        output_field: Optional[str] = None,
        name: Optional[str] = None,
        description: Optional[str] = None,
    ) -> None:
        super().__init__(name=name or "SumAggregator", description=description)
        self.field = field
        self.group_by = group_by
        self.output_field = output_field or f"{field}_sum"

    def aggregate(self, records: Records, context: PipelineContext) -> Records:
        """Compute sum of the field."""
        if self.group_by:
            return self._grouped_sum(records)
        else:
            values = self._extract_numeric_values(records, self.field)
            total = sum(values)
            self._results.append(
                AggregationResult(
                    field=self.field,
                    value=total,
                    count=len(values),
                    aggregation="sum",
                )
            )
            return [{self.output_field: total, "count": len(values)}]

    def _grouped_sum(self, records: Records) -> Records:
        """Compute sum grouped by a field."""
        groups: Dict[Any, List[float]] = {}
        for record in records:
            key = record.get(self.group_by)
            val = record.get(self.field)
            if val is None:
                continue
            try:
                num_val = float(val)
                if key not in groups:
                    groups[key] = []
                groups[key].append(num_val)
            except (ValueError, TypeError):
                pass

        result: Records = []
        for key, values in groups.items():
            total = sum(values)
            self._results.append(
                AggregationResult(
                    field=self.field,
                    value=total,
                    count=len(values),
                    aggregation="sum",
                )
            )
            result.append(
                {
                    self.group_by: key,
                    self.output_field: total,
                    "count": len(values),
                }
            )
        return result


class AverageAggregator(BaseAggregator):
    """Compute the average (mean) of numeric fields.

    Attributes:
        field: Field name to average.
        group_by: Optional field to group by.
        output_field: Name of the output field (default: '{field}_avg').
    """

    def __init__(
        self,
        field: str,
        group_by: Optional[str] = None,
        output_field: Optional[str] = None,
        name: Optional[str] = None,
        description: Optional[str] = None,
    ) -> None:
        super().__init__(name=name or "AverageAggregator", description=description)
        self.field = field
        self.group_by = group_by
        self.output_field = output_field or f"{field}_avg"

    def aggregate(self, records: Records, context: PipelineContext) -> Records:
        """Compute average of the field."""
        if self.group_by:
            return self._grouped_avg(records)
        else:
            values = self._extract_numeric_values(records, self.field)
            avg = sum(values) / len(values) if values else 0.0
            self._results.append(
                AggregationResult(
                    field=self.field,
                    value=avg,
                    count=len(values),
                    aggregation="average",
                )
            )
            return [{self.output_field: avg, "count": len(values)}]

    def _grouped_avg(self, records: Records) -> Records:
        """Compute average grouped by a field."""
        groups: Dict[Any, List[float]] = {}
        for record in records:
            key = record.get(self.group_by)
            val = record.get(self.field)
            if val is None:
                continue
            try:
                num_val = float(val)
                if key not in groups:
                    groups[key] = []
                groups[key].append(num_val)
            except (ValueError, TypeError):
                pass

        result: Records = []
        for key, values in groups.items():
            avg = sum(values) / len(values) if values else 0.0
            self._results.append(
                AggregationResult(
                    field=self.field,
                    value=avg,
                    count=len(values),
                    aggregation="average",
                )
            )
            result.append(
                {
                    self.group_by: key,
                    self.output_field: avg,
                    "count": len(values),
                }
            )
        return result


class CountAggregator(BaseAggregator):
    """Count records, optionally grouped by a field.

    Attributes:
        group_by: Optional field to group by before counting.
        output_field: Name of the output count field.
    """

    def __init__(
        self,
        group_by: Optional[str] = None,
        output_field: str = "count",
        name: Optional[str] = None,
        description: Optional[str] = None,
    ) -> None:
        super().__init__(name=name or "CountAggregator", description=description)
        self.group_by = group_by
        self.output_field = output_field

    def aggregate(self, records: Records, context: PipelineContext) -> Records:
        """Count records."""
        if self.group_by:
            groups: Dict[Any, int] = {}
            for record in records:
                key = record.get(self.group_by)
                groups[key] = groups.get(key, 0) + 1

            result: Records = []
            for key, count in groups.items():
                self._results.append(
                    AggregationResult(
                        field=self.group_by or "records",
                        value=count,
                        count=count,
                        aggregation="count",
                    )
                )
                result.append({self.group_by: key, self.output_field: count})
            return result
        else:
            count = len(records)
            self._results.append(
                AggregationResult(
                    field="records",
                    value=count,
                    count=count,
                    aggregation="count",
                )
            )
            return [{self.output_field: count}]


class MinMaxAggregator(BaseAggregator):
    """Compute minimum and maximum values of numeric fields.

    Attributes:
        field: Field name to compute min/max for.
        group_by: Optional field to group by.
    """

    def __init__(
        self,
        field: str,
        group_by: Optional[str] = None,
        name: Optional[str] = None,
        description: Optional[str] = None,
    ) -> None:
        super().__init__(name=name or "MinMaxAggregator", description=description)
        self.field = field
        self.group_by = group_by

    def aggregate(self, records: Records, context: PipelineContext) -> Records:
        """Compute min and max of the field."""
        if self.group_by:
            return self._grouped_minmax(records)
        else:
            values = self._extract_numeric_values(records, self.field)
            if not values:
                return [{f"{self.field}_min": None, f"{self.field}_max": None, "count": 0}]

            min_val = min(values)
            max_val = max(values)
            self._results.append(
                AggregationResult(
                    field=self.field,
                    value=min_val,
                    count=len(values),
                    aggregation="min",
                )
            )
            self._results.append(
                AggregationResult(
                    field=self.field,
                    value=max_val,
                    count=len(values),
                    aggregation="max",
                )
            )
            return [
                {
                    f"{self.field}_min": min_val,
                    f"{self.field}_max": max_val,
                    "count": len(values),
                }
            ]

    def _grouped_minmax(self, records: Records) -> Records:
        """Compute min/max grouped by a field."""
        groups: Dict[Any, List[float]] = {}
        for record in records:
            key = record.get(self.group_by)
            val = record.get(self.field)
            if val is None:
                continue
            try:
                num_val = float(val)
                if key not in groups:
                    groups[key] = []
                groups[key].append(num_val)
            except (ValueError, TypeError):
                pass

        result: Records = []
        for key, values in groups.items():
            if not values:
                continue
            min_val = min(values)
            max_val = max(values)
            self._results.append(
                AggregationResult(
                    field=self.field,
                    value=min_val,
                    count=len(values),
                    aggregation="min",
                )
            )
            self._results.append(
                AggregationResult(
                    field=self.field,
                    value=max_val,
                    count=len(values),
                    aggregation="max",
                )
            )
            result.append(
                {
                    self.group_by: key,
                    f"{self.field}_min": min_val,
                    f"{self.field}_max": max_val,
                    "count": len(values),
                }
            )
        return result


class GroupByAggregator(BaseAggregator):
    """Group records by a key and compute multiple aggregations per group.

    Attributes:
        group_by: Field name or function to group by.
        aggregations: Dict mapping output field name to (field, agg_func).
            Supported agg_func names: 'sum', 'avg', 'count', 'min', 'max', 'median'.
    """

    BUILTIN_AGGREGATIONS: Dict[str, Callable[[List[float]], float]] = {
        "sum": sum,
        "avg": lambda v: sum(v) / len(v) if v else 0.0,
        "min": lambda v: min(v) if v else float("nan"),
        "max": lambda v: max(v) if v else float("nan"),
        "count": len,
        "median": lambda v: sorted(v)[len(v) // 2] if v else float("nan"),
    }

    def __init__(
        self,
        group_by: Union[str, Callable[[Record], Any]],
        aggregations: Dict[str, Tuple[str, Union[str, Callable[[List[float]], Any]]]],
        name: Optional[str] = None,
        description: Optional[str] = None,
    ) -> None:
        super().__init__(name=name or "GroupByAggregator", description=description)
        self.group_by = group_by
        self.aggregations = aggregations

    def _get_group_key(self, record: Record) -> Any:
        """Extract the group key from a record."""
        if callable(self.group_by):
            return self.group_by(record)
        return record.get(self.group_by)

    def _resolve_agg_func(
        self, func_spec: Union[str, Callable[[List[float]], Any]]
    ) -> Callable[[List[float]], Any]:
        """Resolve an aggregation function from spec."""
        if callable(func_spec):
            return func_spec
        if isinstance(func_spec, str):
            func = self.BUILTIN_AGGREGATIONS.get(func_spec)
            if func is None:
                raise PipelineError(
                    f"Unknown aggregation function: {func_spec}",
                    stage_name=self.name,
                )
            return func
        raise PipelineError(
            f"Invalid aggregation function spec: {func_spec}",
            stage_name=self.name,
        )

    def aggregate(self, records: Records, context: PipelineContext) -> Records:
        """Group and aggregate records."""
        groups: Dict[Any, Records] = {}

        for record in records:
            key = self._get_group_key(record)
            if key not in groups:
                groups[key] = []
            groups[key].append(record)

        result: Records = []
        for key, group_records in groups.items():
            group_result: Record = {}

            key_field = self.group_by if isinstance(self.group_by, str) else "group_key"
            group_result[key_field] = key
            group_result["count"] = len(group_records)

            for out_field, (src_field, agg_spec) in self.aggregations.items():
                values = self._extract_numeric_values(group_records, src_field)
                agg_func = self._resolve_agg_func(agg_spec)
                try:
                    group_result[out_field] = agg_func(values)
                except Exception as e:
                    group_result[out_field] = None
                    context.add_warning(
                        f"GroupByAggregator: {out_field} aggregation failed: {e}"
                    )

                self._results.append(
                    AggregationResult(
                        field=src_field,
                        value=group_result[out_field],
                        count=len(values),
                        aggregation=out_field,
                    )
                )

            result.append(group_result)

        return result


class PercentileAggregator(BaseAggregator):
    """Compute percentiles of numeric fields.

    Uses the nearest-rank method for percentile calculation.

    Attributes:
        field: Field name to compute percentiles for.
        percentiles: List of percentiles to compute (e.g., [25, 50, 75, 90, 95, 99]).
        group_by: Optional field to group by.
    """

    def __init__(
        self,
        field: str,
        percentiles: List[float] = None,
        group_by: Optional[str] = None,
        name: Optional[str] = None,
        description: Optional[str] = None,
    ) -> None:
        super().__init__(name=name or "PercentileAggregator", description=description)
        self.field = field
        self.percentiles = percentiles or [25, 50, 75, 90, 95, 99]
        self.group_by = group_by

    def _compute_percentile(self, sorted_values: List[float], p: float) -> float:
        """Compute a percentile using nearest-rank method."""
        if not sorted_values:
            return 0.0
        n = len(sorted_values)
        rank = math.ceil(p / 100.0 * n)
        rank = max(1, min(rank, n))
        return sorted_values[rank - 1]

    def aggregate(self, records: Records, context: PipelineContext) -> Records:
        """Compute percentiles."""
        if self.group_by:
            return self._grouped_percentiles(records)
        else:
            values = sorted(self._extract_numeric_values(records, self.field))
            result: Record = {"count": len(values)}
            for p in self.percentiles:
                val = self._compute_percentile(values, p)
                result[f"p{p}"] = val
                self._results.append(
                    AggregationResult(
                        field=self.field,
                        value=val,
                        count=len(values),
                        aggregation=f"p{p}",
                    )
                )
            return [result]

    def _grouped_percentiles(self, records: Records) -> Records:
        """Compute percentiles grouped by a field."""
        groups: Dict[Any, List[float]] = {}
        for record in records:
            key = record.get(self.group_by)
            val = record.get(self.field)
            if val is None:
                continue
            try:
                num_val = float(val)
                if key not in groups:
                    groups[key] = []
                groups[key].append(num_val)
            except (ValueError, TypeError):
                pass

        result: Records = []
        for key, values in groups.items():
            sorted_vals = sorted(values)
            group_result: Record = {
                self.group_by: key,
                "count": len(values),
            }
            for p in self.percentiles:
                val = self._compute_percentile(sorted_vals, p)
                group_result[f"p{p}"] = val
                self._results.append(
                    AggregationResult(
                        field=self.field,
                        value=val,
                        count=len(values),
                        aggregation=f"p{p}",
                    )
                )
            result.append(group_result)
        return result


class MovingAverageAggregator(BaseAggregator):
    """Compute a moving (rolling) average over a series of records.

    Preserves the input records but adds a moving average field.

    Attributes:
        field: Field name to compute the moving average for.
        window_size: Size of the moving window.
        output_field: Name of the output field.
        min_periods: Minimum number of observations required (default: window_size).
    """

    def __init__(
        self,
        field: str,
        window_size: int = 10,
        output_field: Optional[str] = None,
        min_periods: Optional[int] = None,
        name: Optional[str] = None,
        description: Optional[str] = None,
    ) -> None:
        super().__init__(name=name or "MovingAverageAggregator", description=description)
        self.field = field
        self.window_size = window_size
        self.output_field = output_field or f"{field}_ma{window_size}"
        self.min_periods = min_periods or window_size

    def aggregate(self, records: Records, context: PipelineContext) -> Records:
        """Compute moving average for each record."""
        window: Deque[float] = deque(maxlen=self.window_size)
        result: Records = []

        for record in records:
            new_record = dict(record)
            val = record.get(self.field)

            if val is not None:
                try:
                    num_val = float(val)
                    window.append(num_val)
                except (ValueError, TypeError):
                    pass

            if len(window) >= self.min_periods:
                avg = sum(window) / len(window)
                new_record[self.output_field] = avg
            else:
                new_record[self.output_field] = None

            result.append(new_record)

        self._results.append(
            AggregationResult(
                field=self.field,
                value=None,
                count=len(records),
                aggregation=f"moving_average_{self.window_size}",
            )
        )

        return result


class WindowedAggregator(BaseAggregator):
    """Aggregate records over time or sequence windows.

    Supports tumbling windows (non-overlapping) and sliding windows (overlapping).

    Attributes:
        field: Field name to aggregate.
        window_type: 'tumbling' or 'sliding'.
        window_size: Number of records per window.
        slide: Slide interval (for sliding windows, defaults to 1).
        agg_func: Aggregation function name ('sum', 'avg', 'min', 'max', 'count').
        key_field: Optional field to use for window key (e.g., timestamp).
    """

    def __init__(
        self,
        field: str,
        window_type: str = "tumbling",
        window_size: int = 10,
        slide: Optional[int] = None,
        agg_func: str = "avg",
        key_field: Optional[str] = None,
        name: Optional[str] = None,
        description: Optional[str] = None,
    ) -> None:
        super().__init__(name=name or "WindowedAggregator", description=description)
        self.field = field
        self.window_type = window_type
        self.window_size = window_size
        self.slide = slide or (window_size if window_type == "tumbling" else 1)
        self.agg_func_name = agg_func
        self.key_field = key_field

        agg_map = {
            "sum": sum,
            "avg": lambda v: sum(v) / len(v) if v else 0.0,
            "min": lambda v: min(v) if v else None,
            "max": lambda v: max(v) if v else None,
            "count": len,
        }
        if agg_func not in agg_map:
            raise PipelineError(
                f"Unknown aggregation function: {agg_func}",
                stage_name=self.name,
            )
        self._agg_func = agg_map[agg_func]

    def aggregate(self, records: Records, context: PipelineContext) -> Records:
        """Compute windowed aggregations."""
        values = self._extract_numeric_values(records, self.field)

        if not values:
            return []

        result: Records = []

        if self.window_type == "tumbling":
            for i in range(0, len(values), self.window_size):
                window = values[i : i + self.window_size]
                agg_val = self._agg_func(window)
                self._results.append(
                    AggregationResult(
                        field=self.field,
                        value=agg_val,
                        count=len(window),
                        aggregation=f"window_{self.agg_func_name}",
                    )
                )
                result.append(
                    {
                        "window_start": i,
                        "window_end": i + len(window) - 1,
                        self.agg_func_name: agg_val,
                        "count": len(window),
                    }
                )
        elif self.window_type == "sliding":
            for i in range(0, len(values) - self.window_size + 1, self.slide):
                window = values[i : i + self.window_size]
                agg_val = self._agg_func(window)
                self._results.append(
                    AggregationResult(
                        field=self.field,
                        value=agg_val,
                        count=len(window),
                        aggregation=f"sliding_{self.agg_func_name}",
                    )
                )
                result.append(
                    {
                        "window_start": i,
                        "window_end": i + self.window_size - 1,
                        self.agg_func_name: agg_val,
                        "count": len(window),
                    }
                )
        else:
            raise PipelineError(
                f"Unknown window type: {self.window_type}",
                stage_name=self.name,
            )

        return result
