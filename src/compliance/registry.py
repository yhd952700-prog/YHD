"""
Compliance registry for LiuHao AI OS.

Provides a unified registry for compliance frameworks, tracking
compliance status, generating reports, gap analysis, cross-framework
control mapping, evidence collection and management, and compliance
dashboard data.
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Optional


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------


class ComplianceFramework(str, Enum):
    """Supported compliance frameworks."""

    GDPR = "gdpr"
    HIPAA = "hipaa"
    SOC2 = "soc2"
    ISO27001 = "iso27001"
    PCI_DSS = "pci_dss"


class ControlStatus(str, Enum):
    """Status of a control implementation."""

    NOT_STARTED = "not_started"
    IN_PROGRESS = "in_progress"
    IMPLEMENTED = "implemented"
    EVIDENCE_COLLECTED = "evidence_collected"
    CERTIFIED = "certified"
    NOT_APPLICABLE = "not_applicable"
    FAILED = "failed"


class EvidenceStatus(str, Enum):
    """Status of evidence collection."""

    NEEDED = "needed"
    REQUESTED = "requested"
    COLLECTED = "collected"
    REVIEWED = "reviewed"
    APPROVED = "approved"
    EXPIRED = "expired"


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------


@dataclass
class ComplianceControl:
    """A unified compliance control mapped across frameworks."""

    control_id: str
    title: str
    description: str
    framework: ComplianceFramework
    framework_control_id: str  # ID within the framework (e.g., "A.5.1", "CC6.1")
    category: str = ""
    status: ControlStatus = ControlStatus.NOT_STARTED
    mapped_controls: list[str] = field(default_factory=list)  # Cross-framework mapping
    evidence_ids: list[str] = field(default_factory=list)
    owner: Optional[str] = None
    priority: str = "medium"  # critical, high, medium, low
    last_updated: float = field(default_factory=lambda: time.time())
    implementation_details: str = ""
    notes: str = ""


@dataclass
class EvidenceRecord:
    """Evidence record for compliance controls."""

    evidence_id: str
    title: str
    description: str = ""
    evidence_type: str = ""  # policy, screenshot, log, report, etc.
    status: EvidenceStatus = EvidenceStatus.NEEDED
    control_ids: list[str] = field(default_factory=list)
    collected_at: Optional[float] = None
    collected_by: str = ""
    location: str = ""
    expiration_date: Optional[float] = None
    reviewer: Optional[str] = None
    reviewed_at: Optional[float] = None
    file_size: int = 0
    file_format: str = ""


@dataclass
class GapFinding:
    """A compliance gap finding."""

    gap_id: str
    control_id: str
    framework: ComplianceFramework
    description: str
    severity: str = "medium"  # critical, high, medium, low
    remediation_steps: list[str] = field(default_factory=list)
    target_date: Optional[float] = None
    owner: Optional[str] = None
    status: str = "open"  # open, in_progress, closed
    created_at: float = field(default_factory=lambda: time.time())
    closed_at: Optional[float] = None


@dataclass
class ComplianceReport:
    """A comprehensive compliance report."""

    report_id: str
    generated_at: float
    frameworks: list[ComplianceFramework]
    total_controls: int = 0
    controls_by_status: dict[str, int] = field(default_factory=dict)
    controls_by_framework: dict[str, dict[str, int]] = field(default_factory=dict)
    evidence_status: dict[str, int] = field(default_factory=dict)
    open_gaps: int = 0
    critical_gaps: int = 0
    high_gaps: int = 0
    overall_compliance_score: float = 0.0
    framework_scores: dict[str, float] = field(default_factory=dict)
    summary: str = ""
    recommendations: list[str] = field(default_factory=list)


@dataclass
class DashboardData:
    """Compliance dashboard summary data."""

    overall_score: float
    total_controls: int
    implemented_controls: int
    open_gaps: int
    critical_gaps: int
    evidence_needed: int
    evidence_collected: int
    framework_scores: dict[str, float]
    recent_activities: list[dict[str, Any]] = field(default_factory=list)
    upcoming_deadlines: list[dict[str, Any]] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Compliance Registry
# ---------------------------------------------------------------------------


class ComplianceRegistry:
    """Central registry for all compliance frameworks and controls.

    Provides cross-framework control mapping, unified status tracking,
    evidence management, gap analysis, and compliance reporting.
    """

    def __init__(self) -> None:
        self._controls: dict[str, ComplianceControl] = {}
        self._evidence: dict[str, EvidenceRecord] = {}
        self._gaps: dict[str, GapFinding] = {}
        self._frameworks: dict[ComplianceFramework, bool] = {
            f: False for f in ComplianceFramework
        }
        self._activity_log: list[dict[str, Any]] = []
        self._listeners: list[Callable[[dict[str, Any]], None]] = []

    # ---- Framework Registration ----

    def register_framework(self, framework: ComplianceFramework) -> None:
        """Register a compliance framework as active."""
        self._frameworks[framework] = True
        self._log_activity("framework_registered", {"framework": framework.value})

    def unregister_framework(self, framework: ComplianceFramework) -> None:
        """Unregister a compliance framework."""
        self._frameworks[framework] = False
        self._log_activity("framework_unregistered", {"framework": framework.value})

    def is_framework_active(self, framework: ComplianceFramework) -> bool:
        """Check if a framework is active."""
        return self._frameworks.get(framework, False)

    def list_active_frameworks(self) -> list[ComplianceFramework]:
        """List all active frameworks."""
        return [f for f, active in self._frameworks.items() if active]

    # ---- Control Management ----

    def register_control(
        self,
        control_id: str,
        title: str,
        description: str,
        framework: ComplianceFramework,
        framework_control_id: str,
        category: str = "",
        priority: str = "medium",
        owner: Optional[str] = None,
    ) -> ComplianceControl:
        """Register a compliance control."""
        control = ComplianceControl(
            control_id=control_id,
            title=title,
            description=description,
            framework=framework,
            framework_control_id=framework_control_id,
            category=category,
            priority=priority,
            owner=owner,
        )
        self._controls[control_id] = control
        self._log_activity("control_registered", {"control_id": control_id, "framework": framework.value})
        return control

    def get_control(self, control_id: str) -> Optional[ComplianceControl]:
        """Get a control by ID."""
        return self._controls.get(control_id)

    def list_controls(
        self,
        framework: Optional[ComplianceFramework] = None,
        status: Optional[ControlStatus] = None,
        category: Optional[str] = None,
        owner: Optional[str] = None,
    ) -> list[ComplianceControl]:
        """List controls with optional filters."""
        controls = list(self._controls.values())
        if framework:
            controls = [c for c in controls if c.framework == framework]
        if status:
            controls = [c for c in controls if c.status == status]
        if category:
            controls = [c for c in controls if c.category == category]
        if owner:
            controls = [c for c in controls if c.owner == owner]
        return controls

    def update_control_status(
        self, control_id: str, status: ControlStatus, details: str = ""
    ) -> bool:
        """Update a control's implementation status."""
        control = self._controls.get(control_id)
        if control is None:
            return False
        control.status = status
        control.last_updated = time.time()
        if details:
            control.implementation_details = details
        self._log_activity(
            "control_status_updated",
            {"control_id": control_id, "new_status": status.value},
        )
        return True

    def set_control_owner(self, control_id: str, owner: str) -> bool:
        """Assign an owner to a control."""
        control = self._controls.get(control_id)
        if control is None:
            return False
        control.owner = owner
        control.last_updated = time.time()
        return True

    # ---- Cross-Framework Mapping ----

    def map_controls(self, control_id_1: str, control_id_2: str) -> bool:
        """Create a bidirectional mapping between two controls."""
        c1 = self._controls.get(control_id_1)
        c2 = self._controls.get(control_id_2)
        if c1 is None or c2 is None:
            return False

        if control_id_2 not in c1.mapped_controls:
            c1.mapped_controls.append(control_id_2)
        if control_id_1 not in c2.mapped_controls:
            c2.mapped_controls.append(control_id_1)

        self._log_activity(
            "controls_mapped",
            {"control_1": control_id_1, "control_2": control_id_2},
        )
        return True

    def get_mapped_controls(
        self, control_id: str, target_framework: Optional[ComplianceFramework] = None
    ) -> list[ComplianceControl]:
        """Get controls mapped to the given control, optionally filtered by framework."""
        control = self._controls.get(control_id)
        if control is None:
            return []

        mapped = []
        for mapped_id in control.mapped_controls:
            mapped_control = self._controls.get(mapped_id)
            if mapped_control is None:
                continue
            if target_framework and mapped_control.framework != target_framework:
                continue
            mapped.append(mapped_control)
        return mapped

    def get_common_controls(
        self, framework_1: ComplianceFramework, framework_2: ComplianceFramework
    ) -> list[tuple[ComplianceControl, ComplianceControl]]:
        """Get all control mappings between two frameworks."""
        pairs = []
        for control in self.list_controls(framework=framework_1):
            mapped = self.get_mapped_controls(control.control_id, framework_2)
            for m in mapped:
                pairs.append((control, m))
        return pairs

    # ---- Evidence Management ----

    def add_evidence(
        self,
        title: str,
        description: str = "",
        evidence_type: str = "",
        control_ids: Optional[list[str]] = None,
        location: str = "",
        collected_by: str = "",
    ) -> EvidenceRecord:
        """Add evidence for one or more controls."""
        evidence = EvidenceRecord(
            evidence_id=str(uuid.uuid4()),
            title=title,
            description=description,
            evidence_type=evidence_type,
            status=EvidenceStatus.COLLECTED,
            control_ids=control_ids or [],
            collected_at=time.time(),
            collected_by=collected_by,
            location=location,
        )
        self._evidence[evidence.evidence_id] = evidence

        # Link to controls
        for control_id in evidence.control_ids:
            control = self._controls.get(control_id)
            if control and evidence.evidence_id not in control.evidence_ids:
                control.evidence_ids.append(evidence.evidence_id)
                # Update control status if evidence is collected
                if control.status == ControlStatus.NOT_STARTED:
                    control.status = ControlStatus.EVIDENCE_COLLECTED

        self._log_activity(
            "evidence_added",
            {"evidence_id": evidence.evidence_id, "title": title},
        )
        return evidence

    def get_evidence(self, evidence_id: str) -> Optional[EvidenceRecord]:
        """Get evidence by ID."""
        return self._evidence.get(evidence_id)

    def list_evidence(
        self,
        control_id: Optional[str] = None,
        status: Optional[EvidenceStatus] = None,
        evidence_type: Optional[str] = None,
    ) -> list[EvidenceRecord]:
        """List evidence with optional filters."""
        evidence = list(self._evidence.values())
        if control_id:
            evidence = [e for e in evidence if control_id in e.control_ids]
        if status:
            evidence = [e for e in evidence if e.status == status]
        if evidence_type:
            evidence = [e for e in evidence if e.evidence_type == evidence_type]
        return evidence

    def review_evidence(self, evidence_id: str, reviewer: str, approved: bool) -> bool:
        """Review evidence and mark as approved or rejected."""
        evidence = self._evidence.get(evidence_id)
        if evidence is None:
            return False
        evidence.reviewer = reviewer
        evidence.reviewed_at = time.time()
        evidence.status = (
            EvidenceStatus.APPROVED if approved else EvidenceStatus.NEEDED
        )
        return True

    # ---- Gap Analysis ----

    def identify_gaps(self) -> list[GapFinding]:
        """Identify all compliance gaps (non-implemented controls)."""
        gaps = []
        for control in self._controls.values():
            if control.status in (ControlStatus.NOT_STARTED, ControlStatus.FAILED):
                gap = GapFinding(
                    gap_id=str(uuid.uuid4()),
                    control_id=control.control_id,
                    framework=control.framework,
                    description=f"Control {control.framework_control_id} - {control.title}: {control.status.value}",
                    severity=control.priority if control.priority in ("high", "critical") else "medium",
                    remediation_steps=[
                        f"Implement control: {control.title}",
                        f"Review requirements: {control.description[:100]}",
                        "Collect evidence of implementation",
                    ],
                    owner=control.owner,
                )
                self._gaps[gap.gap_id] = gap
                gaps.append(gap)
        return gaps

    def add_gap(
        self,
        control_id: str,
        description: str,
        severity: str = "medium",
        remediation_steps: Optional[list[str]] = None,
    ) -> GapFinding:
        """Manually add a gap finding."""
        control = self._controls.get(control_id)
        framework = control.framework if control else ComplianceFramework.GDPR

        gap = GapFinding(
            gap_id=str(uuid.uuid4()),
            control_id=control_id,
            framework=framework,
            description=description,
            severity=severity,
            remediation_steps=remediation_steps or [],
        )
        self._gaps[gap.gap_id] = gap
        return gap

    def close_gap(self, gap_id: str) -> bool:
        """Close a gap as resolved."""
        gap = self._gaps.get(gap_id)
        if gap is None:
            return False
        gap.status = "closed"
        gap.closed_at = time.time()
        self._log_activity("gap_closed", {"gap_id": gap_id})
        return True

    def get_open_gaps(self, severity: Optional[str] = None) -> list[GapFinding]:
        """Get open gaps, optionally filtered by severity."""
        gaps = [g for g in self._gaps.values() if g.status == "open"]
        if severity:
            gaps = [g for g in gaps if g.severity == severity]
        return gaps

    # ---- Compliance Reporting ----

    def generate_report(
        self,
        frameworks: Optional[list[ComplianceFramework]] = None,
    ) -> ComplianceReport:
        """Generate a comprehensive compliance report.

        Args:
            frameworks: List of frameworks to include. Defaults to all active.

        Returns:
            ComplianceReport with status summary.
        """
        target_frameworks = frameworks or self.list_active_frameworks()

        controls = [
            c for c in self._controls.values()
            if c.framework in target_frameworks
        ]

        total = len(controls)

        # Count by status
        by_status: dict[str, int] = {}
        for control in controls:
            status_key = control.status.value
            by_status[status_key] = by_status.get(status_key, 0) + 1

        # Count by framework
        by_framework: dict[str, dict[str, int]] = {}
        for fw in target_frameworks:
            fw_controls = [c for c in controls if c.framework == fw]
            implemented = sum(
                1 for c in fw_controls
                if c.status in (ControlStatus.IMPLEMENTED, ControlStatus.CERTIFIED, ControlStatus.EVIDENCE_COLLECTED)
            )
            by_framework[fw.value] = {
                "total": len(fw_controls),
                "implemented": implemented,
                "in_progress": sum(1 for c in fw_controls if c.status == ControlStatus.IN_PROGRESS),
                "not_started": sum(1 for c in fw_controls if c.status == ControlStatus.NOT_STARTED),
            }

        # Evidence status
        evidence_status_counts: dict[str, int] = {}
        for evidence in self._evidence.values():
            key = evidence.status.value
            evidence_status_counts[key] = evidence_status_counts.get(key, 0) + 1

        # Gaps
        open_gaps = [g for g in self._gaps.values() if g.status == "open"]
        critical_gaps = [g for g in open_gaps if g.severity == "critical"]
        high_gaps = [g for g in open_gaps if g.severity == "high"]

        # Framework scores
        framework_scores: dict[str, float] = {}
        for fw in target_frameworks:
            fw_controls = [c for c in controls if c.framework == fw]
            if fw_controls:
                implemented = sum(
                    1 for c in fw_controls
                    if c.status in (ControlStatus.IMPLEMENTED, ControlStatus.CERTIFIED, ControlStatus.EVIDENCE_COLLECTED)
                )
                in_progress = sum(1 for c in fw_controls if c.status == ControlStatus.IN_PROGRESS)
                score = ((implemented + 0.5 * in_progress) / len(fw_controls)) * 100
                framework_scores[fw.value] = round(score, 1)

        # Overall score
        overall_score = (
            sum(framework_scores.values()) / len(framework_scores)
            if framework_scores else 0.0
        )

        # Recommendations
        recommendations = []
        if critical_gaps:
            recommendations.append(
                f"Address {len(critical_gaps)} critical gaps immediately"
            )
        if high_gaps:
            recommendations.append(
                f"Prioritize {len(high_gaps)} high-severity gaps"
            )
        evidence_needed = sum(
            1 for c in controls
            if c.status in (ControlStatus.IMPLEMENTED, ControlStatus.IN_PROGRESS)
            and not c.evidence_ids
        )
        if evidence_needed > 0:
            recommendations.append(
                f"Collect evidence for {evidence_needed} implemented controls"
            )

        summary = (
            f"Compliance report for {', '.join(f.value for f in target_frameworks)}. "
            f"Total controls: {total}. "
            f"Overall compliance score: {overall_score:.1f}%. "
            f"{len(open_gaps)} open gaps ({len(critical_gaps)} critical, {len(high_gaps)} high)."
        )

        return ComplianceReport(
            report_id=str(uuid.uuid4()),
            generated_at=time.time(),
            frameworks=target_frameworks,
            total_controls=total,
            controls_by_status=by_status,
            controls_by_framework=by_framework,
            evidence_status=evidence_status_counts,
            open_gaps=len(open_gaps),
            critical_gaps=len(critical_gaps),
            high_gaps=len(high_gaps),
            overall_compliance_score=round(overall_score, 1),
            framework_scores=framework_scores,
            summary=summary,
            recommendations=recommendations,
        )

    # ---- Dashboard ----

    def get_dashboard_data(self) -> DashboardData:
        """Get compliance dashboard summary data."""
        report = self.generate_report()

        # Recent activities (last 10)
        recent = self._activity_log[-10:]

        # Upcoming deadlines (gap target dates)
        deadlines = []
        for gap in self._gaps.values():
            if gap.status == "open" and gap.target_date:
                deadlines.append({
                    "gap_id": gap.gap_id,
                    "description": gap.description[:50],
                    "target_date": gap.target_date,
                    "severity": gap.severity,
                })
        deadlines.sort(key=lambda d: d["target_date"])

        return DashboardData(
            overall_score=report.overall_compliance_score,
            total_controls=report.total_controls,
            implemented_controls=report.controls_by_status.get("implemented", 0)
            + report.controls_by_status.get("evidence_collected", 0)
            + report.controls_by_status.get("certified", 0),
            open_gaps=report.open_gaps,
            critical_gaps=report.critical_gaps,
            evidence_needed=report.evidence_status.get("needed", 0),
            evidence_collected=report.evidence_status.get("collected", 0)
            + report.evidence_status.get("approved", 0),
            framework_scores=report.framework_scores,
            recent_activities=recent,
            upcoming_deadlines=deadlines[:5],
        )

    # ---- Activity Logging ----

    def _log_activity(self, action: str, details: dict[str, Any]) -> None:
        """Log a compliance activity."""
        entry = {
            "timestamp": time.time(),
            "action": action,
            "details": details,
        }
        self._activity_log.append(entry)

        # Notify listeners
        for listener in self._listeners:
            try:
                listener(entry)
            except Exception:
                pass

    def add_listener(self, callback: Callable[[dict[str, Any]], None]) -> None:
        """Add a listener for compliance events."""
        self._listeners.append(callback)

    def remove_listener(self, callback: Callable[[dict[str, Any]], None]) -> bool:
        """Remove a listener. Returns True if found."""
        try:
            self._listeners.remove(callback)
            return True
        except ValueError:
            return False

    def get_activity_log(self, limit: int = 50) -> list[dict[str, Any]]:
        """Get recent activity log entries."""
        return self._activity_log[-limit:]
