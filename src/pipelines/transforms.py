"""
Data transformation stages for LiuHao AI OS pipelines.

Provides functional-style transformation stages that operate on lists
of records (dicts). Each transform is a pipeline stage that takes
a list of records as input and returns a transformed list.

Transforms follow functional programming patterns:
- Pure functions (no side effects)
- Immutable data where possible
- Composable via pipeline chaining

Transforms:
    - FilterTransform: Filter rows by condition
    - MapTransform: Apply function to each row
    - ReduceTransform: Aggregate/group by
    - SortTransform: Sort by key
    - DeduplicateTransform: Remove duplicates
    - RenameTransform: Rename fields
    - TypeCastTransform: Type conversion
    - NormalizeTransform: Normalize values to 0-1 range
    - SplitTransform: Split field into multiple
    - JoinTransform: Join two datasets
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Tuple, Union

from .base import PipelineContext, PipelineError, PipelineStage

logger = logging.getLogger(__name__)

Record = Dict[str, Any]
Records = List[Record]
RecordFunc = Callable[[Record], Any]
RecordPredicate = Callable[[Record], bool]


@dataclass
class TransformStats:
    """Statistics for a transform operation.

    Attributes:
        input_count: Number of input records.
        output_count: Number of output records.
        skipped: Number of records skipped/filtered out.
        errors: Number of records that caused errors.
    """

    input_count: int = 0
    output_count: int = 0
    skipped: int = 0
    errors: int = 0


class BaseTransform(PipelineStage[Records, Records]):
    """Base class for all transformation stages.

    Provides common infrastructure and stats tracking.
    """

    def __init__(
        self,
        name: Optional[str] = None,
        description: Optional[str] = None,
    ) -> None:
        super().__init__(name=name, description=description)
        self._stats = TransformStats()

    @property
    def stats(self) -> TransformStats:
        """Statistics from the most recent transform."""
        return self._stats

    def process(self, data: Records, context: PipelineContext) -> Records:
        """Apply the transform to input records.

        Args:
            data: List of input records.
            context: Pipeline context.

        Returns:
            List of transformed records.
        """
        self._stats = TransformStats(input_count=len(data) if data else 0)
        result = self.transform(data or [], context)
        self._stats.output_count = len(result)
        context.set_variable(f"{self.name}_stats", self._stats)
        return result

    def transform(self, records: Records, context: PipelineContext) -> Records:
        """Core transform logic. Override in subclasses.

        Args:
            records: Input records.
            context: Pipeline context.

        Returns:
            Transformed records.
        """
        raise NotImplementedError


class FilterTransform(BaseTransform):
    """Filter records by a predicate function.

    Only records for which the predicate returns True are kept.

    Attributes:
        predicate: Function that takes a record and returns bool.
        field: Optional field name to filter on (used with value/operator).
        value: Value to compare against (used with field/operator).
        operator: Comparison operator ('eq', 'ne', 'gt', 'ge', 'lt', 'le', 'contains', 'in').
    """

    def __init__(
        self,
        predicate: Optional[RecordPredicate] = None,
        field: Optional[str] = None,
        value: Any = None,
        operator: str = "eq",
        name: Optional[str] = None,
        description: Optional[str] = None,
    ) -> None:
        super().__init__(name=name or "FilterTransform", description=description)
        self.predicate = predicate
        self.field = field
        self.value = value
        self.operator = operator

        if predicate is None and field is None:
            raise ValueError("FilterTransform requires either predicate or field parameter")

    def _evaluate(self, record: Record) -> bool:
        """Evaluate the filter condition for a single record."""
        if self.predicate:
            return bool(self.predicate(record))

        assert self.field is not None
        field_value = record.get(self.field)

        ops = {
            "eq": lambda a, b: a == b,
            "ne": lambda a, b: a != b,
            "gt": lambda a, b: a is not None and a > b,
            "ge": lambda a, b: a is not None and a >= b,
            "lt": lambda a, b: a is not None and a < b,
            "le": lambda a, b: a is not None and a <= b,
            "contains": lambda a, b: a is not None and b in a,
            "in": lambda a, b: a in b if isinstance(b, (list, tuple, set)) else False,
            "not_in": lambda a, b: a not in b if isinstance(b, (list, tuple, set)) else True,
            "is_none": lambda a, b: a is None,
            "is_not_none": lambda a, b: a is not None,
            "startswith": lambda a, b: isinstance(a, str) and a.startswith(b),
            "endswith": lambda a, b: isinstance(a, str) and a.endswith(b),
        }

        op_func = ops.get(self.operator)
        if op_func is None:
            raise PipelineError(
                f"Unknown filter operator: {self.operator}",
                stage_name=self.name,
            )

        try:
            return op_func(field_value, self.value)
        except TypeError:
            return False

    def transform(self, records: Records, context: PipelineContext) -> Records:
        """Filter records."""
        result: Records = []
        for record in records:
            try:
                if self._evaluate(record):
                    result.append(record)
                else:
                    self._stats.skipped += 1
            except Exception as e:
                self._stats.errors += 1
                context.add_warning(f"Filter error on record: {e}")
        return result


class MapTransform(BaseTransform):
    """Apply a function to each record.

    The mapping function receives a record and should return a
    (possibly modified) record.

    Attributes:
        mapper: Function that transforms a single record.
        add_field: Optional field name to add (with value from value_func).
        value_func: Function to compute the new field value.
    """

    def __init__(
        self,
        mapper: Optional[Callable[[Record], Record]] = None,
        add_field: Optional[str] = None,
        value_func: Optional[Callable[[Record], Any]] = None,
        name: Optional[str] = None,
        description: Optional[str] = None,
    ) -> None:
        super().__init__(name=name or "MapTransform", description=description)
        self.mapper = mapper
        self.add_field = add_field
        self.value_func = value_func

        if mapper is None and (add_field is None or value_func is None):
            raise ValueError(
                "MapTransform requires either mapper or both add_field and value_func"
            )

    def transform(self, records: Records, context: PipelineContext) -> Records:
        """Apply mapping to each record."""
        result: Records = []
        for record in records:
            try:
                if self.mapper:
                    mapped = self.mapper(record)
                    result.append(mapped)
                else:
                    assert self.add_field is not None
                    assert self.value_func is not None
                    new_record = dict(record)
                    new_record[self.add_field] = self.value_func(record)
                    result.append(new_record)
            except Exception as e:
                self._stats.errors += 1
                context.add_warning(f"MapTransform error: {e}")
                result.append(record)  # Pass through unchanged on error
        return result


class ReduceTransform(BaseTransform):
    """Aggregate or group records by a key function.

    When grouping, returns a list of group records, each containing
    the group key and aggregated values.

    Attributes:
        group_by: Field name or function to group by. If None, reduces to a single record.
        aggregations: Dict mapping output field name to (field, agg_func) tuples.
    """

    def __init__(
        self,
        group_by: Optional[Union[str, Callable[[Record], Any]]] = None,
        aggregations: Optional[Dict[str, Tuple[str, Callable[[List[Any]], Any]]]] = None,
        name: Optional[str] = None,
        description: Optional[str] = None,
    ) -> None:
        super().__init__(name=name or "ReduceTransform", description=description)
        self.group_by = group_by
        self.aggregations = aggregations or {}

    def _get_group_key(self, record: Record) -> Any:
        """Extract the group key from a record."""
        if self.group_by is None:
            return "__all__"
        if callable(self.group_by):
            return self.group_by(record)
        return record.get(self.group_by)

    def _aggregate(self, values: List[Any]) -> Dict[str, Any]:
        """Compute all aggregations for a list of records."""
        result: Dict[str, Any] = {}
        for out_field, (src_field, agg_func) in self.aggregations.items():
            field_values = [v.get(src_field) for v in values if src_field in v]
            result[out_field] = agg_func(field_values)
        return result

    def transform(self, records: Records, context: PipelineContext) -> Records:
        """Group and aggregate records."""
        groups: Dict[Any, List[Record]] = {}

        for record in records:
            try:
                key = self._get_group_key(record)
                if key not in groups:
                    groups[key] = []
                groups[key].append(record)
            except Exception as e:
                self._stats.errors += 1
                context.add_warning(f"ReduceTransform grouping error: {e}")

        result: Records = []
        for key, group_records in groups.items():
            agg_values = self._aggregate(group_records)
            group_record: Record = {"count": len(group_records)}
            if self.group_by is not None:
                key_field = self.group_by if isinstance(self.group_by, str) else "group_key"
                group_record[key_field] = key
            group_record.update(agg_values)
            result.append(group_record)

        return result


class SortTransform(BaseTransform):
    """Sort records by one or more keys.

    Attributes:
        key: Field name or list of field names to sort by.
        reverse: Whether to sort in descending order.
        key_func: Optional custom key extraction function.
    """

    def __init__(
        self,
        key: Optional[Union[str, List[str]]] = None,
        reverse: bool = False,
        key_func: Optional[Callable[[Record], Any]] = None,
        name: Optional[str] = None,
        description: Optional[str] = None,
    ) -> None:
        super().__init__(name=name or "SortTransform", description=description)
        self.key = key
        self.reverse = reverse
        self.key_func = key_func

        if key is None and key_func is None:
            raise ValueError("SortTransform requires either key or key_func")

    def _make_key_func(self) -> Callable[[Record], Any]:
        """Create the sort key function."""
        if self.key_func:
            return self.key_func

        assert self.key is not None
        if isinstance(self.key, str):
            return lambda r: (r.get(self.key) is None, r.get(self.key))
        else:  # List of keys
            def multi_key(r: Record) -> Tuple[Any, ...]:
                return tuple((r.get(k) is None, r.get(k)) for k in self.key)
            return multi_key

    def transform(self, records: Records, context: PipelineContext) -> Records:
        """Sort records."""
        try:
            key_func = self._make_key_func()
            return sorted(records, key=key_func, reverse=self.reverse)
        except Exception as e:
            self._stats.errors += 1
            context.add_warning(f"SortTransform error: {e}")
            return list(records)


class DeduplicateTransform(BaseTransform):
    """Remove duplicate records.

    Attributes:
        key: Field name or list of field names to use for deduplication.
             If None, uses the entire record (hash of all fields).
        keep: Which duplicate to keep ('first' or 'last').
    """

    def __init__(
        self,
        key: Optional[Union[str, List[str]]] = None,
        keep: str = "first",
        name: Optional[str] = None,
        description: Optional[str] = None,
    ) -> None:
        super().__init__(name=name or "DeduplicateTransform", description=description)
        self.key = key
        self.keep = keep

        if keep not in ("first", "last"):
            raise ValueError(f"keep must be 'first' or 'last', got '{keep}'")

    def _dedup_key(self, record: Record) -> Any:
        """Generate a hashable dedup key from a record."""
        if self.key is None:
            # Use all fields (frozenset of items for hashability)
            return tuple(sorted(record.items()))
        if isinstance(self.key, str):
            return record.get(self.key)
        else:
            return tuple(record.get(k) for k in self.key)

    def transform(self, records: Records, context: PipelineContext) -> Records:
        """Remove duplicates."""
        seen: Dict[Any, Record] = {}

        for record in records:
            try:
                dedup_key = self._dedup_key(record)
                if dedup_key in seen:
                    self._stats.skipped += 1
                    if self.keep == "last":
                        seen[dedup_key] = record
                else:
                    seen[dedup_key] = record
            except Exception as e:
                self._stats.errors += 1
                context.add_warning(f"DeduplicateTransform error: {e}")
                # Include records that cause errors
                seen[id(record)] = record

        if self.keep == "first":
            return list(seen.values())
        else:
            # For "last", we need to preserve original order with last occurrences
            last_seen: Dict[Any, int] = {}
            for i, record in enumerate(records):
                dedup_key = self._dedup_key(record)
                last_seen[dedup_key] = i

            result: Records = []
            seen_keys: set = set()
            for i, record in enumerate(records):
                dedup_key = self._dedup_key(record)
                if last_seen.get(dedup_key) == i and dedup_key not in seen_keys:
                    result.append(record)
                    seen_keys.add(dedup_key)
            return result


class RenameTransform(BaseTransform):
    """Rename fields in records.

    Attributes:
        mapping: Dict mapping old field names to new field names.
        keep_original: Whether to keep the original fields alongside renamed ones.
    """

    def __init__(
        self,
        mapping: Dict[str, str],
        keep_original: bool = False,
        name: Optional[str] = None,
        description: Optional[str] = None,
    ) -> None:
        super().__init__(name=name or "RenameTransform", description=description)
        self.mapping = mapping
        self.keep_original = keep_original

    def transform(self, records: Records, context: PipelineContext) -> Records:
        """Rename fields in each record."""
        result: Records = []
        for record in records:
            new_record = dict(record) if self.keep_original else {}
            for key, value in record.items():
                new_key = self.mapping.get(key, key)
                if self.keep_original:
                    new_record[new_key] = value
                else:
                    new_record[new_key] = value
            result.append(new_record)
        return result


class TypeCastTransform(BaseTransform):
    """Cast field values to specified types.

    Attributes:
        type_map: Dict mapping field names to target types (int, float, str, bool).
        strict: If True, raise errors on failed casts. If False, skip bad values.
    """

    CAST_FUNCTIONS: Dict[str, Callable[[Any], Any]] = {
        "int": lambda v: int(v) if v is not None else None,
        "float": lambda v: float(v) if v is not None else None,
        "str": lambda v: str(v) if v is not None else None,
        "bool": lambda v: bool(v) if v is not None else None,
    }

    def __init__(
        self,
        type_map: Dict[str, Union[str, type, Callable[[Any], Any]]],
        strict: bool = False,
        name: Optional[str] = None,
        description: Optional[str] = None,
    ) -> None:
        super().__init__(name=name or "TypeCastTransform", description=description)
        self.type_map = type_map
        self.strict = strict

    def _cast_value(self, value: Any, target: Union[str, type, Callable[[Any], Any]]) -> Any:
        """Cast a single value to the target type."""
        if value is None:
            return None

        if callable(target) and not isinstance(target, type):
            return target(value)

        target_str = target.__name__ if isinstance(target, type) else str(target)
        cast_func = self.CAST_FUNCTIONS.get(target_str.lower())

        if cast_func:
            return cast_func(value)

        if isinstance(target, type):
            return target(value)

        raise ValueError(f"Unsupported target type: {target}")

    def transform(self, records: Records, context: PipelineContext) -> Records:
        """Cast field types."""
        result: Records = []
        for record in records:
            new_record = dict(record)
            for field, target_type in self.type_map.items():
                if field in new_record:
                    try:
                        new_record[field] = self._cast_value(new_record[field], target_type)
                    except (ValueError, TypeError) as e:
                        self._stats.errors += 1
                        if self.strict:
                            raise PipelineError(
                                f"Type cast failed for field '{field}': {e}",
                                stage_name=self.name,
                                original_error=e,
                            ) from e
                        context.add_warning(
                            f"TypeCastTransform: failed to cast '{field}' to {target_type}: {e}"
                        )
            result.append(new_record)
        return result


class NormalizeTransform(BaseTransform):
    """Normalize numeric field values to a 0-1 range.

    Uses min-max normalization: normalized = (value - min) / (max - min)

    Attributes:
        fields: List of field names to normalize, or dict of field -> (min, max).
        min_max: Optional dict of field -> (min_val, max_val). If None, computed from data.
    """

    def __init__(
        self,
        fields: Union[List[str], Dict[str, Tuple[float, float]]],
        min_max: Optional[Dict[str, Tuple[float, float]]] = None,
        name: Optional[str] = None,
        description: Optional[str] = None,
    ) -> None:
        super().__init__(name=name or "NormalizeTransform", description=description)
        self.fields = fields
        self.min_max = min_max

    def _compute_min_max(self, records: Records, field: str) -> Tuple[float, float]:
        """Compute min and max values for a field."""
        values = [
            float(r[field]) for r in records
            if field in r and r[field] is not None
        ]
        if not values:
            return (0.0, 1.0)
        return (min(values), max(values))

    def transform(self, records: Records, context: PipelineContext) -> Records:
        """Normalize specified fields."""
        result: Records = []

        # Determine which fields and their ranges
        if isinstance(self.fields, dict):
            field_ranges = dict(self.fields)
        else:
            field_ranges = {}
            for f in self.fields:
                if self.min_max and f in self.min_max:
                    field_ranges[f] = self.min_max[f]
                else:
                    field_ranges[f] = self._compute_min_max(records, f)

        for record in records:
            new_record = dict(record)
            for field, (min_val, max_val) in field_ranges.items():
                if field in new_record and new_record[field] is not None:
                    try:
                        val = float(new_record[field])
                        if max_val == min_val:
                            new_record[f"{field}_norm"] = 0.0
                        else:
                            new_record[f"{field}_norm"] = (val - min_val) / (max_val - min_val)
                    except (ValueError, TypeError) as e:
                        self._stats.errors += 1
                        context.add_warning(
                            f"NormalizeTransform error for '{field}': {e}"
                        )
            result.append(new_record)

        return result


class SplitTransform(BaseTransform):
    """Split a field value into multiple new fields.

    Attributes:
        source_field: Name of the field to split.
        target_fields: List of names for the resulting fields.
        delimiter: Delimiter string to split on.
        maxsplit: Maximum number of splits.
        keep_source: Whether to keep the original source field.
    """

    def __init__(
        self,
        source_field: str,
        target_fields: List[str],
        delimiter: str = ",",
        maxsplit: int = -1,
        keep_source: bool = False,
        name: Optional[str] = None,
        description: Optional[str] = None,
    ) -> None:
        super().__init__(name=name or "SplitTransform", description=description)
        self.source_field = source_field
        self.target_fields = target_fields
        self.delimiter = delimiter
        self.maxsplit = maxsplit
        self.keep_source = keep_source

    def transform(self, records: Records, context: PipelineContext) -> Records:
        """Split fields in each record."""
        result: Records = []
        for record in records:
            new_record = dict(record)
            source_value = new_record.get(self.source_field, "")

            if source_value is None:
                parts = []
            elif isinstance(source_value, str):
                parts = source_value.split(self.delimiter, self.maxsplit)
            else:
                parts = [str(source_value)]

            for i, target in enumerate(self.target_fields):
                new_record[target] = parts[i] if i < len(parts) else None

            if not self.keep_source and self.source_field in new_record:
                del new_record[self.source_field]

            result.append(new_record)

        return result


class JoinTransform(BaseTransform):
    """Join two datasets on a common key.

    Works with the DAG pipeline pattern where two branches merge.
    The input should be a dict with keys 'left' and 'right' (or the
    two predecessor node IDs).

    Attributes:
        left_key: Field name for the left side join key.
        right_key: Field name for the right side join key.
        join_type: Type of join ('inner', 'left', 'right', 'outer').
        left_label: Label for the left dataset (default 'left').
        right_label: Label for the right dataset (default 'right').
    """

    def __init__(
        self,
        left_key: str,
        right_key: Optional[str] = None,
        join_type: str = "inner",
        left_label: str = "left",
        right_label: str = "right",
        name: Optional[str] = None,
        description: Optional[str] = None,
    ) -> None:
        super().__init__(name=name or "JoinTransform", description=description)
        self.left_key = left_key
        self.right_key = right_key or left_key
        self.join_type = join_type
        self.left_label = left_label
        self.right_label = right_label

        if join_type not in ("inner", "left", "right", "outer"):
            raise ValueError(
                f"join_type must be one of 'inner', 'left', 'right', 'outer', got '{join_type}'"
            )

    def process(self, data: Any, context: PipelineContext) -> Records:
        """Join two datasets.

        Args:
            data: Dict with two keys (the two predecessor outputs), each a list of records.
            context: Pipeline context.

        Returns:
            Joined list of records.
        """
        self._stats = TransformStats()

        if not isinstance(data, dict) or len(data) < 2:
            raise PipelineError(
                "JoinTransform requires two input datasets (dict with 2 keys)",
                stage_name=self.name,
            )

        keys = list(data.keys())
        left_records = data[keys[0]] or []
        right_records = data[keys[1]] or []

        self._stats.input_count = len(left_records) + len(right_records)
        result = self._join(left_records, right_records, context)
        self._stats.output_count = len(result)
        context.set_variable(f"{self.name}_stats", self._stats)
        return result

    def _join(self, left: Records, right: Records, context: PipelineContext) -> Records:
        """Perform the actual join."""
        # Build index on right side
        right_index: Dict[Any, List[Record]] = {}
        for r in right:
            key = r.get(self.right_key)
            if key not in right_index:
                right_index[key] = []
            right_index[key].append(r)

        result: Records = []
        matched_right_keys: set = set()

        for left_rec in left:
            key = left_rec.get(self.left_key)
            matches = right_index.get(key, [])

            if matches:
                matched_right_keys.add(key)
                for right_rec in matches:
                    merged = {}
                    # Prefix left fields
                    for k, v in left_rec.items():
                        merged[f"{self.left_label}_{k}" if k in right_rec else k] = v
                    # Add right fields
                    for k, v in right_rec.items():
                        if k in left_rec:
                            merged[f"{self.right_label}_{k}"] = v
                        else:
                            merged[k] = v
                    result.append(merged)
            elif self.join_type in ("left", "outer"):
                # Left join: include left record with null right fields
                merged = dict(left_rec)
                for k in set().union(*(r.keys() for r in right)) if right else []:
                    if k not in merged:
                        merged[k] = None
                result.append(merged)

        # Right/outer join: include unmatched right records
        if self.join_type in ("right", "outer"):
            for r in right:
                key = r.get(self.right_key)
                if key not in matched_right_keys:
                    merged = dict(r)
                    for k in set().union(*(l.keys() for l in left)) if left else []:
                        if k not in merged:
                            merged[k] = None
                    result.append(merged)

        return result

    def transform(self, records: Records, context: PipelineContext) -> Records:
        """Not used for JoinTransform (uses process instead)."""
        return records
