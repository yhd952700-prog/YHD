"""HD-06 — capacity-planning hooks.

Capacity planning is *observability + warning only*. It estimates storage and
emits threshold warnings so operators can grow storage **before** a problem --
it NEVER triggers deletion. This is deliberate: the safe-default subsystem must
not delete data to make room.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List

from .lifecycle import LifecycleMetadata


@dataclass
class CapacityReport:
    """Estimated capacity posture for a set of retained records."""

    record_count: int = 0
    estimated_bytes: int = 0
    storage_limit_bytes: int = 0
    headroom_ratio: float = 1.0  # free / limit (1.0 == unlimited / empty)
    warnings: List[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "record_count": self.record_count,
            "estimated_bytes": self.estimated_bytes,
            "storage_limit_bytes": self.storage_limit_bytes,
            "headroom_ratio": self.headroom_ratio,
            "warnings": list(self.warnings),
        }


class CapacityEstimator:
    """Estimates capacity and warns; never deletes or mutates records."""

    def estimate(
        self,
        records: List[LifecycleMetadata],
        avg_record_bytes: int,
        storage_limit_bytes: int = 0,
    ) -> CapacityReport:
        count = len(records)
        estimated = count * max(avg_record_bytes, 1)
        headroom = 1.0
        if storage_limit_bytes > 0:
            used = min(estimated, storage_limit_bytes)
            headroom = max(0.0, (storage_limit_bytes - used) / storage_limit_bytes)
        return CapacityReport(
            record_count=count,
            estimated_bytes=estimated,
            storage_limit_bytes=storage_limit_bytes,
            headroom_ratio=headroom,
        )

    def check_thresholds(
        self, report: CapacityReport, warn_at_ratio: float = 0.8
    ) -> List[str]:
        """Return warnings when headroom drops below ``warn_at_ratio``.

        Purely advisory. The caller decides what (if anything) to do; this hook
        performs no destructive action.
        """
        warnings: List[str] = []
        if report.storage_limit_bytes > 0 and report.headroom_ratio < warn_at_ratio:
            warnings.append(
                f"retention storage headroom low: {report.headroom_ratio:.2%} "
                f"remaining (warn at {warn_at_ratio:.0%})"
            )
        if report.record_count == 0:
            warnings.append("no retained records to estimate")
        report.warnings.extend(warnings)
        return warnings
