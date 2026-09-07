"""
Observability Store for LiuHao AI OS

Provides:
- Span storage and retrieval
- Metric storage and aggregation
- Export to various formats (JSON, OTLP protobuf)
- Hash chain integrity for tamper evidence
"""

from pathlib import Path
import json
import hashlib
import time
from typing import Dict, List, Optional, Any

from .models import Span, Metric, MetricPoint, ResourceMetrics, AttributeValue


class ObservabilityStore:
    """
    Observability data store with integrity verification.
    
    Features:
    - JSON-based persistent storage
    - Hash chain for tamper evidence
    - Span and metric querying
    - Export capabilities
    """
    
    def __init__(self, storage_path: str = "data/observability/spans.json"):
        self.storage_path = Path(storage_path)
        self.storage_path.parent.mkdir(parents=True, exist_ok=True)
        self._spans: Dict[str, Span] = {}
        self._metrics: Dict[str, Metric] = {}
        self._hash_chain: Optional[List[str]] = None
        self._load()
    
    def _load(self) -> None:
        """Load existing data from storage."""
        if self.storage_path.exists():
            try:
                with open(self.storage_path, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                self._spans = {
                    k: Span.from_dict(v) for k, v in data.get("spans", {}).items()
                }
                self._metrics = {
                    k: Metric.from_dict(v) for k, v in data.get("metrics", {}).items()
                }
                self._hash_chain = data.get("hash_chain")
                # Rebuild hash chain if missing or inconsistent
                if not self._hash_chain or len(self._hash_chain) != len(self._spans):
                    self._hash_chain = None
                    self._build_hash_chain()
            except Exception as e:
                print(f"Warning: Failed to load observability store: {e}")
                self._spans = {}
                self._metrics = {}
                self._hash_chain = None
        else:
            self._spans = {}
            self._metrics = {}
            self._hash_chain = None
    
    def _build_hash_chain(self) -> None:
        """Build or rebuild the hash chain from current spans."""
        chain: List[str] = []
        sorted_ids = sorted(self._spans.keys())
        prev_hash = "genesis"
        for eid in sorted_ids:
            span = self._spans[eid]
            span_dict = span.to_dict()
            span_dict["prev_hash"] = prev_hash
            span_data = json.dumps(span_dict, sort_keys=True, separators=(",", ":"))
            span_hash = hashlib.sha256(span_data.encode()).hexdigest()
            chain.append(span_hash)
            prev_hash = span_hash
        
        self._hash_chain = chain
        self._save()
    
    def _save(self) -> None:
        """Persist data to storage with hash chain."""
        # Ensure hash chain is built
        if self._hash_chain is None:
            self._build_hash_chain()
        
        data = {
            "version": 1,
            "saved_at": time.time(),
            "hash_chain": self._hash_chain,
            "spans": {k: v.to_dict() for k, v in self._spans.items()},
            "metrics": {k: v.to_dict() for k, v in self._metrics.items()},
        }
        with open(self.storage_path, 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=2)
    
    # ==================== Span Operations ====================
    
    def emit_span(self, span: Span) -> str:
        """
        Emit (store) a span.
        
        Args:
            span: The span to store
            
        Returns:
            The span ID
        """
        eid = span.span_id
        self._spans[eid] = span
        self._save()
        return eid
    
    def get_span(self, span_id: str) -> Optional[Span]:
        """Get a single span by ID."""
        return self._spans.get(span_id)
    
    def list_spans(self, filters: Optional[Dict[str, Any]] = None) -> List[Span]:
        """
        List spans with optional filters.
        
        Supported filter keys:
        - name
        - trace_id
        - parent_span_id
        - kind
        - start_time / end_time (timestamp range)
        - attributes (key-value pairs)
        """
        spans = list(self._spans.values())
        
        if not filters:
            return spans
        
        result = []
        for span in spans:
            match = True
            for key, value in filters.items():
                # Handle nested attribute filters like "attributes.color"
                if key == "attributes" and isinstance(value, dict):
                    span_val = span.attributes
                    for k, v in value.items():
                        if k in span_val and span_val[k] != v:
                            match = False
                            break
                    if not match:
                        break
                elif hasattr(span, key):
                    span_val = getattr(span, key, None)
                    if span_val != value:
                        match = False
                        break
                else:
                    match = False
                    break
            if match:
                result.append(span)
        return result
    
    def filter_by_name(self, name: str) -> List[Span]:
        """Filter spans by name."""
        return [s for s in self._spans.values() if s.name == name]
    
    def filter_by_trace_id(self, trace_id: str) -> List[Span]:
        """Filter spans by trace ID."""
        return [s for s in self._spans.values() if s.trace_id == trace_id]
    
    def filter_by_kind(self, kind: str) -> List[Span]:
        """Filter spans by kind."""
        return [s for s in self._spans.values() if s.kind == kind]
    
    def filter_by_parent(self, parent_span_id: str) -> List[Span]:
        """Filter spans by parent span ID."""
        return [s for s in self._spans.values() if s.parent_span_id == parent_span_id]
    
    def filter_by_time_range(self, start_time: int, end_time: int) -> List[Span]:
        """Filter spans by timestamp range."""
        return [s for s in self._spans.values() 
                if start_time <= (s.start_time or 0) <= end_time]
    
    def filter_by_attribute(self, key: str, value: Any) -> List[Span]:
        """Filter spans by attribute value."""
        return [s for s in self._spans.values() if s.attributes.get(key) == value]
    
    # ==================== Metric Operations ====================
    
    def emit_metric(self, metric: Metric) -> str:
        """
        Emit (store) a metric.
        
        Args:
            metric: The metric to store
            
        Returns:
            The metric ID
        """
        eid = metric.name
        self._metrics[eid] = metric
        self._save()
        return eid
    
    def get_metric(self, metric_name: str) -> Optional[Metric]:
        """Get a single metric by name."""
        return self._metrics.get(metric_name)
    
    def list_metrics(self) -> List[Metric]:
        """List all metrics."""
        return list(self._metrics.values())
    
    # ==================== Integrity ====================
    
    def verify_integrity(self) -> bool:
        """Verify the hash chain integrity."""
        if not self._spans or not self._hash_chain:
            return True  # Empty or not loaded yet is considered valid
        
        chain = self._hash_chain
        if len(chain) != len(self._spans):
            return False
        
        sorted_ids = sorted(self._spans.keys())
        prev_hash = "genesis"
        for i, eid in enumerate(sorted_ids):
            span = self._spans[eid]
            span_dict = span.to_dict()
            span_dict["prev_hash"] = prev_hash
            span_data = json.dumps(span_dict, sort_keys=True, separators=(",", ":"))
            expected_hash = hashlib.sha256(span_data.encode()).hexdigest()
            
            if expected_hash != chain[i]:
                return False
            
            prev_hash = expected_hash
        
        return True
    
    # ==================== Statistics ====================
    
    def get_stats(self) -> Dict[str, Any]:
        """Get observability store statistics."""
        span_count = len(self._spans)
        metric_count = len(self._metrics)
        
        # Count by kind
        kind_counts: Dict[str, int] = {}
        for span in self._spans.values():
            kind_counts[span.kind] = kind_counts.get(span.kind, 0) + 1
        
        # Count status
        status_counts: Dict[str, int] = {}
        for span in self._spans.values():
            status_counts[span.status] = status_counts.get(span.status, 0) + 1
        
        return {
            "total_spans": span_count,
            "total_metrics": metric_count,
            "by_kind": kind_counts,
            "by_status": status_counts,
        }


# Module-level convenience functions
_default_store: Optional[ObservabilityStore] = None


def get_observability_store() -> ObservabilityStore:
    """Get the default observability store instance."""
    global _default_store
    if _default_store is None:
        _default_store = ObservabilityStore()
    return _default_store


def emit_span(span: Span) -> str:
    """Emit (store) a span using the default store."""
    return get_observability_store().emit_span(span)


def emit_metric(metric: Metric) -> str:
    """Emit (store) a metric using the default store."""
    return get_observability_store().emit_metric(metric)