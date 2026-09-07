"""Observability Models for LiuHao AI OS"""

from dataclasses import dataclass, field
from typing import Dict, Any, List, Optional
from datetime import datetime
import uuid


class SpanKind:
    """Span kind enumeration."""
    UNKNOWN = "unknown"
    INTERNAL = "internal"
    SERVER = "server"
    CLIENT = "client"
    PRODUCER = "producer"
    CONSUMER = "consumer"


class Status:
    """Span status values."""
    UNKNOWN = "unspecified"
    OK = "ok"
    ERROR = "error"
    CANCELLED = "cancelled"


class SeverityNumber:
    """Severity number for attributes."""
    TRACE = 1
    DEBUG = 2
    INFO = 3
    WARNING = 4
    ERROR = 5
    FATAL = 6


@dataclass
class AttributeValue:
    """Generic attribute value for span attributes."""
    value: Any
    type_: str = "string"

    STRING = "string"
    INT = "int"
    BOOL = "bool"
    DBL = "dbl"
    STRING_ARRAY = "string_array"
    INT_ARRAY = "int_array"
    DBL_ARRAY = "dbl_array"
    BOOL_ARRAY = "bool_array"

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary."""
        return {f"value.{self.type_}": self.value}


@dataclass
class SpanContext:
    """Context carrying span identification information."""
    trace_id: str
    span_id: str
    trace_flags: int = 0x01  # DEFAULT
    trace_state: Optional[str] = None


@dataclass
class Span:
    """Core data model for a distributed trace span."""
    span_id: str
    trace_id: str
    name: str
    kind: str = SpanKind.INTERNAL
    start_time: Optional[int] = None
    end_time: Optional[int] = None
    attributes: Dict[str, Any] = field(default_factory=dict)
    status: str = Status.UNKNOWN
    parent_span_id: Optional[str] = None
    trace_state: Optional[str] = None
    dropped_attributes_count: int = 0

    def __post_init__(self):
        if self.start_time is None:
            self.start_time = int(datetime.now().timestamp() * 1e9)
        if self.end_time is None:
            self.end_time = int(datetime.now().timestamp() * 1e9)

    def set_attribute(self, key: str, value: Any) -> None:
        """Set a span attribute."""
        self.attributes[key] = value

    def get_attribute(self, key: str, default: Any = None) -> Any:
        """Get a span attribute."""
        return self.attributes.get(key, default)

    def to_dict(self) -> Dict[str, Any]:
        """Convert span to dictionary for serialization."""
        return {
            "span_id": self.span_id,
            "trace_id": self.trace_id,
            "name": self.name,
            "kind": self.kind,
            "start_time": self.start_time,
            "end_time": self.end_time,
            "attributes": self.attributes,
            "status": self.status,
            "parent_span_id": self.parent_span_id,
            "trace_state": self.trace_state,
            "dropped_attributes_count": self.dropped_attributes_count,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Span":
        """Create Span from dictionary."""
        return cls(
            span_id=data.get("span_id", str(uuid.uuid4())),
            trace_id=data.get("trace_id", str(uuid.uuid4())),
            name=data.get("name", ""),
            kind=data.get("kind", SpanKind.INTERNAL),
            start_time=data.get("start_time"),
            end_time=data.get("end_time"),
            attributes=data.get("attributes", {}),
            status=data.get("status", Status.UNKNOWN),
            parent_span_id=data.get("parent_span_id"),
            trace_state=data.get("trace_state"),
            dropped_attributes_count=data.get("dropped_attributes_count", 0),
        )


@dataclass
class Event:
    """An event within a span."""
    name: str
    time: Optional[int] = None
    attributes: Dict[str, Any] = field(default_factory=dict)
    dropped_attributes_count: int = 0

    def to_dict(self) -> Dict[str, Any]:
        """Convert event to dictionary."""
        return {
            "name": self.name,
            "time": self.time,
            "attributes": self.attributes,
            "dropped_attributes_count": self.dropped_attributes_count,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Event":
        """Create Event from dictionary."""
        return cls(
            name=data.get("name", ""),
            time=data.get("time"),
            attributes=data.get("attributes", {}),
            dropped_attributes_count=data.get("dropped_attributes_count", 0),
        )


@dataclass
class Link:
    """A link from another span."""
    span_id: str
    trace_id: str
    kind: str = SpanKind.INTERNAL
    attributes: Dict[str, Any] = field(default_factory=dict)
    dropped_attributes_count: int = 0

    def to_dict(self) -> Dict[str, Any]:
        """Convert link to dictionary."""
        return {
            "span_id": self.span_id,
            "trace_id": self.trace_id,
            "kind": self.kind,
            "attributes": self.attributes,
            "dropped_attributes_count": self.dropped_attributes_count,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Link":
        """Create Link from dictionary."""
        return cls(
            span_id=data.get("span_id", ""),
            trace_id=data.get("trace_id", ""),
            kind=data.get("kind", SpanKind.INTERNAL),
            attributes=data.get("attributes", {}),
            dropped_attributes_count=data.get("dropped_attributes_count", 0),
        )


@dataclass
class Resource:
    """Resource information for the span."""
    attributes: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self):
        if not self.attributes:
            self.attributes = {
                "service.name": "liuhao-ai-os",
                "service.version": "1.0.0",
            }


@dataclass
class InstrumentationScope:
    """Instrumentation scope information."""
    name: str
    version: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary."""
        return {"name": self.name, "version": self.version}


@dataclass
class ScopeSpans:
    """Spans from a single instrumentation scope."""
    scope: InstrumentationScope
    spans: List[Span] = field(default_factory=list)

    def add(self, span: Span) -> None:
        """Add a span."""
        self.spans.append(span)

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary."""
        return {
            "scope": {
                "name": self.scope.name,
                "version": self.scope.version,
            },
            "spans": [span.to_dict() for span in self.spans],
        }


@dataclass
class MetricPoint:
    """A single metric data point."""
    timestamp: int
    value: float
    attributes: Dict[str, Any] = field(default_factory=dict)


@dataclass
class Metric:
    """A metric observation."""
    name: str
    description: str = ""
    unit: str = ""
    kind: str = "gauge"  # gauge, counter, histogram, summary
    aggregation_temporality: str = "delta"
    metric_type: str = "double"
    double_data_points: List[MetricPoint] = field(default_factory=list)
    int_data_points: List[MetricPoint] = field(default_factory=list)
    explicit_histogram: Optional[Any] = None

    def add_observation(self, value: float, attributes: Optional[Dict[str, Any]] = None) -> None:
        """Add a metric observation."""
        point = MetricPoint(
            timestamp=int(datetime.now().timestamp() * 1e9),
            value=value,
            attributes=attributes or {},
        )
        if self.metric_type == "double":
            self.double_data_points.append(point)
        elif self.metric_type == "int":
            self.int_data_points.append(point)

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary."""
        return {
            "name": self.name,
            "description": self.description,
            "unit": self.unit,
            "kind": self.kind,
            "aggregation_temporality": self.aggregation_temporality,
            "metric_type": self.metric_type,
            "double_data_points": [
                {"timestamp": p.timestamp, "value": p.value, "attributes": p.attributes}
                for p in self.double_data_points
            ],
            "int_data_points": [
                {"timestamp": p.timestamp, "value": p.value, "attributes": p.attributes}
                for p in self.int_data_points
            ],
        }


@dataclass
class ResourceMetrics:
    """Resource-level metrics."""
    resource: Resource
    scopes: List[Any] = field(default_factory=list)  # InstrumentationScopes

    def add_scope(self, scope: Any) -> None:
        """Add instrumentation scope."""
        self.scopes.append(scope)

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary."""
        return {
            "resource": {
                "attributes": self.resource.attributes,
            },
            "scope_metrics": [s.to_dict() for s in self.scopes],
        }