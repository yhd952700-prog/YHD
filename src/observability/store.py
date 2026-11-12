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
import threading
import time
from src.common.atomic_write import (
    atomic_write_text,
    exclusive_file_lock,
    read_text_with_retry,
)
from src.common.hash_chain import compute_hash, verify_declared_hash, DEFAULT_HASH_ALG
from typing import Dict, List, Optional, Any

from .models import Span, Metric

#: 同进程内多个 store 实例（以及多 worker 进程）的写入串行锁。
_PROCESS_WRITE_LOCK = threading.Lock()


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
        self._hash_alg: str = DEFAULT_HASH_ALG
        self._load()

    def _load(self) -> None:
        """Load existing data from storage."""
        if self.storage_path.exists():
            try:
                data = json.loads(read_text_with_retry(self.storage_path))
                self._spans = {
                    k: Span.from_dict(v) for k, v in data.get("spans", {}).items()
                }
                self._metrics = {
                    k: Metric.from_dict(v) for k, v in data.get("metrics", {}).items()
                }
                self._hash_chain = data.get("hash_chain")
                # P0-8c: NO default fallback. An envelope that declares
                # no algorithm is UNVERIFIED, never silently read as sha256.
                self._hash_alg = data.get("hash_alg")
                # Rebuild hash chain if missing or inconsistent
                if self._hash_alg and (
                    not self._hash_chain or len(self._hash_chain) != len(self._spans)
                ):
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

    def _build_hash_chain(self, *, persist: bool = True) -> None:
        """Build or rebuild the hash chain from current spans.

        ``persist=False`` is used from :meth:`_save` (which is already inside
        the write transaction) so rebuilding does not recurse into another
        save/lock acquisition.
        """
        # P0-8c: refuse to (re)build a chain without a declared algorithm.
        # Defaulting to sha256 is exactly what the containment forbids;
        # the operator must stamp legacy data via the migration script.
        if not self._hash_alg:
            raise ValueError(
                "cannot build a hash chain without a declared hash_alg; "
                "run scripts/migrate_p08_hash_alg.py to stamp legacy data"
            )
        chain: List[str] = []
        sorted_ids = sorted(self._spans.keys())
        prev_hash = "genesis"
        for eid in sorted_ids:
            span = self._spans[eid]
            span_dict = span.to_dict()
            span_dict["prev_hash"] = prev_hash
            span_data = json.dumps(span_dict, sort_keys=True, separators=(",", ":"))
            span_hash = compute_hash(self._hash_alg, span_data.encode())
            chain.append(span_hash)
            prev_hash = span_hash

        self._hash_chain = chain
        if persist:
            self._save()

    def _save(self) -> None:
        """Persist data to storage with hash chain.

        读-改-写事务：进程锁 → OS 文件锁 → 锁内重读磁盘 → 按 span/metric id
        合并（同 id 以内存为准，磁盘上其它进程刚写入的数据保留）→ 原子替换。
        合并后重建哈希链，避免"新 spans + 旧链"的错位。
        """
        path = str(self.storage_path)
        with _PROCESS_WRITE_LOCK:
            with exclusive_file_lock(f"{path}.lock", description="可观测性存储"):
                self._merge_disk_state()
                # Ensure hash chain is built
                if self._hash_alg:
                    self._build_hash_chain(persist=False)
                else:
                    # 没有声明算法就不伪造链：保持"未验证"语义。
                    self._hash_chain = None

                data = {
                    "version": 1,
                    "saved_at": time.time(),
                    "hash_alg": self._hash_alg,
                    "hash_chain": self._hash_chain,
                    "spans": {k: v.to_dict() for k, v in self._spans.items()},
                    "metrics": {k: v.to_dict() for k, v in self._metrics.items()},
                }
                # Temp file + fsync + os.replace: the old full rewrite truncated
                # the store first, so a crash mid-write lost every previously
                # recorded span.
                atomic_write_text(path, json.dumps(data, indent=2))

    def _merge_disk_state(self) -> None:
        """锁内重读磁盘；磁盘独有 span/metric 并回内存，同 id 以内存为准。"""
        if not self.storage_path.exists():
            return
        data = json.loads(read_text_with_retry(self.storage_path))
        if not isinstance(data, dict):
            raise ValueError(
                f"可观测性存储结构不合法（{self.storage_path}）：期望 JSON 对象"
            )
        for key, value in (data.get("spans") or {}).items():
            if key not in self._spans:
                self._spans[key] = Span.from_dict(value)
        for key, value in (data.get("metrics") or {}).items():
            if key not in self._metrics:
                self._metrics[key] = Metric.from_dict(value)

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
        if not self._spans:
            return True  # Empty or not loaded yet is considered valid
        # P0-8c: data that declares no algorithm (or carries no chain) is
        # UNVERIFIED -- fail closed, never a silent pass and never a
        # silent fallback to sha256.
        if not self._hash_alg or not self._hash_chain:
            return False

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
            ok, _reason = verify_declared_hash(self._hash_alg, span_data.encode(), chain[i])
            if not ok:
                return False

            prev_hash = chain[i]

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
