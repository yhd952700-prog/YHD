"""
SOC 2 compliance module for LiuHao AI OS.

Implements SOC 2 (Service Organization Control 2) compliance features
including trust services criteria mapping, security/availability/
processing integrity/confidentiality/privacy controls, control testing
and evidence collection, and compliance status tracking.
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------


class TrustPrinciple(str, Enum):
    """SOC 2 Trust Services Criteria (TSC)."""

    SECURITY = "security"  # Common Criteria + CC
    AVAILABILITY = "availability"  # A Series
    PROCESSING_INTEGRITY = "processing_integrity"  # PI Series
    CONFIDENTIALITY = "confidentiality"  # C Series
    PRIVACY = "privacy"  # P Series


class ControlStatus(str, Enum):
    """Status of a control implementation."""

    NOT_STARTED = "not_started"
    IN_PROGRESS = "in_progress"
    IMPLEMENTED = "implemented"
    OPERATING_EFFECTIVELY = "operating_effectively"
    FAILED = "failed"
    NOT_APPLICABLE = "not_applicable"


class ControlCategory(str, Enum):
    """SOC 2 control categories."""

    # Common Criteria (CC) - Security
    CC1_CONTROL_ENVIRONMENT = "cc1"
    CC2_COMMUNICATION = "cc2"
    CC3_RISK_ASSESSMENT = "cc3"
    CC4_MONITORING = "cc4"
    CC5_CONTROL_ACTIVITIES = "cc5"
    CC6_LOGICAL_ACCESS = "cc6"
    CC7_SYSTEM_OPERATIONS = "cc7"
    CC8_CHANGE_MANAGEMENT = "cc8"
    CC9_RISK_MITIGATION = "cc9"
    # Availability (A)
    A1_AVAILABILITY = "a1"
    # Processing Integrity (PI)
    PI1_PROCESSING_INTEGRITY = "pi1"
    # Confidentiality (C)
    C1_CONFIDENTIALITY = "c1"
    # Privacy (P)
    P1_PRIVACY = "p1"


class EvidenceType(str, Enum):
    """Types of control evidence."""

    POLICY_DOCUMENT = "policy"
    PROCEDURE_DOCUMENT = "procedure"
    SCREENSHOT = "screenshot"
    CONFIGURATION = "configuration"
    LOG_OUTPUT = "log_output"
    INTERVIEW_NOTES = "interview"
    TEST_RESULTS = "test_results"
    CERTIFICATION = "certification"
    CONTRACT = "contract"
    REPORT = "report"


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------


@dataclass
class Soc2Control:
    """Represents a single SOC 2 control."""

    control_id: str
    title: str
    description: str
    principle: TrustPrinciple
    category: ControlCategory
    status: ControlStatus = ControlStatus.NOT_STARTED
    points_of_focus: list[str] = field(default_factory=list)
    implementation_details: str = ""
    evidence: list[dict[str, Any]] = field(default_factory=list)
    last_tested_at: Optional[float] = None
    test_frequency: str = "quarterly"  # daily, weekly, monthly, quarterly, annually
    owner: Optional[str] = None
    risk_rating: str = "medium"  # low, medium, high
    notes: str = ""


@dataclass
class ControlTest:
    """Represents a control test/assessment."""

    test_id: str
    control_id: str
    tested_at: float = field(default_factory=lambda: time.time())
    tested_by: str = ""
    status: ControlStatus = ControlStatus.NOT_STARTED
    test_procedure: str = ""
    sample_size: int = 0
    exceptions_found: int = 0
    findings: str = ""
    recommendations: str = ""
    evidence_attachment_ids: list[str] = field(default_factory=list)


@dataclass
class EvidenceRecord:
    """Evidence collected for a control."""

    evidence_id: str
    control_id: str
    evidence_type: EvidenceType
    title: str
    description: str = ""
    collected_at: float = field(default_factory=lambda: time.time())
    collected_by: str = ""
    location: str = ""  # URL or path to evidence
    retention_period: str = "12 months"
    is_archived: bool = False


@dataclass
class Soc2Gap:
    """Represents a gap between current state and SOC 2 requirements."""

    gap_id: str
    control_id: str
    description: str
    severity: str = "medium"  # low, medium, high, critical
    remediation_steps: list[str] = field(default_factory=list)
    target_date: Optional[float] = None
    owner: Optional[str] = None
    closed: bool = False
    closed_at: Optional[float] = None


@dataclass
class Soc2ComplianceReport:
    """SOC 2 compliance status report."""

    report_id: str
    generated_at: float
    trust_principles: list[TrustPrinciple]
    total_controls: int = 0
    operating_effectively: int = 0
    implemented: int = 0
    in_progress: int = 0
    not_started: int = 0
    failed: int = 0
    not_applicable: int = 0
    by_principle: dict[str, dict[str, int]] = field(default_factory=dict)
    by_category: dict[str, dict[str, int]] = field(default_factory=dict)
    open_gaps: int = 0
    high_risk_gaps: int = 0
    overall_score: float = 0.0
    summary: str = ""


# ---------------------------------------------------------------------------
# SOC 2 Controls Framework
# ---------------------------------------------------------------------------


class Soc2ControlsFramework:
    """Defines the SOC 2 controls framework.

    Includes Common Criteria (CC1-CC9) for security plus
    availability, processing integrity, confidentiality, and privacy
    trust services criteria.
    """

    def __init__(self) -> None:
        self._controls: dict[str, Soc2Control] = {}
        self._init_controls()

    def _init_controls(self) -> None:
        """Initialize SOC 2 controls framework."""
        # Common Criteria 1: Control Environment
        self._add_control(
            "CC1.1",
            "Integrity and Ethical Values",
            "The entity demonstrates a commitment to integrity and ethical values.",
            TrustPrinciple.SECURITY,
            ControlCategory.CC1_CONTROL_ENVIRONMENT,
            [
                "Standards of conduct are defined and communicated",
                "Management reinforces expectations of integrity and ethics",
                "Violations are addressed appropriately",
            ],
        )
        self._add_control(
            "CC1.2",
            "Board Independence and Oversight",
            "The board of directors demonstrates independence from management.",
            TrustPrinciple.SECURITY,
            ControlCategory.CC1_CONTROL_ENVIRONMENT,
            [
                "Board has independent members",
                "Board provides oversight of internal controls",
            ],
        )
        self._add_control(
            "CC1.3",
            "Management's Philosophy and Operating Style",
            "Management establishes structures, reporting lines, and authorities.",
            TrustPrinciple.SECURITY,
            ControlCategory.CC1_CONTROL_ENVIRONMENT,
            [
                "Organizational structure is defined",
                "Reporting lines are clear",
                "Roles and responsibilities are defined",
            ],
        )
        self._add_control(
            "CC1.4",
            "Commitment to Competence",
            "The entity demonstrates a commitment to attract and retain competent individuals.",
            TrustPrinciple.SECURITY,
            ControlCategory.CC1_CONTROL_ENVIRONMENT,
            [
                "Hiring standards are defined",
                "Training programs exist",
                "Performance reviews are conducted",
            ],
        )
        self._add_control(
            "CC1.5",
            "Accountability",
            "The entity holds individuals accountable for their responsibilities.",
            TrustPrinciple.SECURITY,
            ControlCategory.CC1_CONTROL_ENVIRONMENT,
            [
                "Performance goals are set",
                "Accountability is enforced",
                "Disciplinary actions are consistent",
            ],
        )

        # Common Criteria 2: Communication
        self._add_control(
            "CC2.1",
            "Objective Communication",
            "The entity communicates information to support the functioning of internal control.",
            TrustPrinciple.SECURITY,
            ControlCategory.CC2_COMMUNICATION,
            [
                "Internal communication channels exist",
                "External communications are managed",
            ],
        )

        # Common Criteria 3: Risk Assessment
        self._add_control(
            "CC3.1",
            "Risk Identification",
            "The entity identifies risks to the achievement of its objectives.",
            TrustPrinciple.SECURITY,
            ControlCategory.CC3_RISK_ASSESSMENT,
            [
                "Risk assessment process is defined",
                "Risks are identified regularly",
                "Risk appetite is defined",
            ],
        )
        self._add_control(
            "CC3.2",
            "Risk Assessment",
            "The entity assesses risks and determines appropriate responses.",
            TrustPrinciple.SECURITY,
            ControlCategory.CC3_RISK_ASSESSMENT,
            [
                "Risk likelihood and impact are assessed",
                "Risk responses are determined",
            ],
        )
        self._add_control(
            "CC3.3",
            "Fraud Risk Assessment",
            "The entity considers potential for fraud.",
            TrustPrinciple.SECURITY,
            ControlCategory.CC3_RISK_ASSESSMENT,
            [
                "Fraud risk factors are evaluated",
                "Anti-fraud programs exist",
            ],
        )
        self._add_control(
            "CC3.4",
            "Significant Changes",
            "The entity identifies and assesses changes that could impact internal control.",
            TrustPrinciple.SECURITY,
            ControlCategory.CC3_RISK_ASSESSMENT,
            [
                "Change risk is assessed",
                "New risks from changes are identified",
            ],
        )

        # Common Criteria 4: Monitoring
        self._add_control(
            "CC4.1",
            "Ongoing Monitoring",
            "The entity monitors internal control effectiveness.",
            TrustPrinciple.SECURITY,
            ControlCategory.CC4_MONITORING,
            [
                "Ongoing monitoring activities exist",
                "Separate evaluations are conducted",
            ],
        )
        self._add_control(
            "CC4.2",
            "Deficiency Communication",
            "Control deficiencies are communicated to management.",
            TrustPrinciple.SECURITY,
            ControlCategory.CC4_MONITORING,
            [
                "Deficiencies are reported",
                "Corrective actions are tracked",
            ],
        )

        # Common Criteria 5: Control Activities
        self._add_control(
            "CC5.1",
            "Control Activities Selection",
            "The entity selects control activities to address risks.",
            TrustPrinciple.SECURITY,
            ControlCategory.CC5_CONTROL_ACTIVITIES,
            [
                "Controls are designed to mitigate risks",
                "Control activities are documented",
            ],
        )
        self._add_control(
            "CC5.2",
            "IT General Controls",
            "The entity implements general controls over technology.",
            TrustPrinciple.SECURITY,
            ControlCategory.CC5_CONTROL_ACTIVITIES,
            [
                "IT governance exists",
                "Change management is in place",
                "Access controls are implemented",
            ],
        )

        # Common Criteria 6: Logical and Physical Access Controls
        self._add_control(
            "CC6.1",
            "Logical Access Security",
            "The entity implements logical access security software.",
            TrustPrinciple.SECURITY,
            ControlCategory.CC6_LOGICAL_ACCESS,
            [
                "Access authentication is required",
                "Unique user identification",
                "Password policies are enforced",
                "Multi-factor authentication",
            ],
        )
        self._add_control(
            "CC6.2",
            "System Access Authorization",
            "The entity authorizes system access.",
            TrustPrinciple.SECURITY,
            ControlCategory.CC6_LOGICAL_ACCESS,
            [
                "Access provisioning process",
                "Access review and approval",
                "Least privilege principle",
            ],
        )
        self._add_control(
            "CC6.3",
            "Access Removal",
            "The entity promptly removes access when no longer required.",
            TrustPrinciple.SECURITY,
            ControlCategory.CC6_LOGICAL_ACCESS,
            [
                "Termination procedures",
                "Role change access review",
                "Periodic access reviews",
            ],
        )
        self._add_control(
            "CC6.4",
            "Physical Access Controls",
            "The entity implements physical access controls.",
            TrustPrinciple.SECURITY,
            ControlCategory.CC6_LOGICAL_ACCESS,
            [
                "Facility access controls",
                "Visitor management",
                "Data center security",
            ],
        )
        self._add_control(
            "CC6.5",
            "Inventory of Assets",
            "The entity maintains an inventory of information assets.",
            TrustPrinciple.SECURITY,
            ControlCategory.CC6_LOGICAL_ACCESS,
            [
                "Asset inventory is maintained",
                "Asset ownership is assigned",
            ],
        )
        self._add_control(
            "CC6.6",
            "Data Classification",
            "The entity classifies information assets.",
            TrustPrinciple.SECURITY,
            ControlCategory.CC6_LOGICAL_ACCESS,
            [
                "Data classification policy",
                "Data handling procedures",
            ],
        )
        self._add_control(
            "CC6.7",
            "Data Encryption",
            "The entity encrypts sensitive data.",
            TrustPrinciple.SECURITY,
            ControlCategory.CC6_LOGICAL_ACCESS,
            [
                "Data at rest encryption",
                "Data in transit encryption",
                "Key management",
            ],
        )
        self._add_control(
            "CC6.8",
            "Endpoint Security",
            "The entity implements endpoint security.",
            TrustPrinciple.SECURITY,
            ControlCategory.CC6_LOGICAL_ACCESS,
            [
                "Anti-malware protection",
                "Endpoint detection and response",
                "Patch management",
            ],
        )

        # Common Criteria 7: System Operations
        self._add_control(
            "CC7.1",
            "System Monitoring",
            "The entity monitors systems for anomalies.",
            TrustPrinciple.SECURITY,
            ControlCategory.CC7_SYSTEM_OPERATIONS,
            [
                "Security event logging",
                "SIEM implementation",
                "Alert triage and response",
            ],
        )
        self._add_control(
            "CC7.2",
            "Incident Management",
            "The entity manages security incidents.",
            TrustPrinciple.SECURITY,
            ControlCategory.CC7_SYSTEM_OPERATIONS,
            [
                "Incident response plan",
                "Incident categorization",
                "Post-incident review",
            ],
        )
        self._add_control(
            "CC7.3",
            "Vulnerability Management",
            "The entity identifies and remediates vulnerabilities.",
            TrustPrinciple.SECURITY,
            ControlCategory.CC7_SYSTEM_OPERATIONS,
            [
                "Vulnerability scanning",
                "Penetration testing",
                "Patch management",
            ],
        )
        self._add_control(
            "CC7.4",
            "Threat Detection",
            "The entity detects security threats.",
            TrustPrinciple.SECURITY,
            ControlCategory.CC7_SYSTEM_OPERATIONS,
            [
                "Intrusion detection",
                "Threat intelligence",
                "Anomaly detection",
            ],
        )

        # Common Criteria 8: Change Management
        self._add_control(
            "CC8.1",
            "Change Management",
            "The entity manages changes to systems and infrastructure.",
            TrustPrinciple.SECURITY,
            ControlCategory.CC8_CHANGE_MANAGEMENT,
            [
                "Change approval process",
                "Change testing",
                "Deployment controls",
                "Rollback procedures",
            ],
        )

        # Common Criteria 9: Risk Mitigation
        self._add_control(
            "CC9.1",
            "Risk Mitigation",
            "The entity identifies and selects risk mitigation options.",
            TrustPrinciple.SECURITY,
            ControlCategory.CC9_RISK_MITIGATION,
            [
                "Risk treatment options",
                "Business continuity planning",
                "Disaster recovery",
            ],
        )
        self._add_control(
            "CC9.2",
            "Vendor Risk Management",
            "The entity assesses and manages vendor risks.",
            TrustPrinciple.SECURITY,
            ControlCategory.CC9_RISK_MITIGATION,
            [
                "Vendor due diligence",
                "Vendor contracts",
                "Vendor monitoring",
            ],
        )

        # Availability Principle
        self._add_control(
            "A1.1",
            "Availability Monitoring",
            "The entity monitors system availability.",
            TrustPrinciple.AVAILABILITY,
            ControlCategory.A1_AVAILABILITY,
            [
                "Uptime monitoring",
                "SLA tracking",
                "Availability reporting",
            ],
        )
        self._add_control(
            "A1.2",
            "Business Continuity",
            "The entity maintains business continuity plans.",
            TrustPrinciple.AVAILABILITY,
            ControlCategory.A1_AVAILABILITY,
            [
                "BCP documentation",
                "Backup procedures",
                "Recovery time objectives",
            ],
        )

        # Processing Integrity
        self._add_control(
            "PI1.1",
            "Processing Accuracy",
            "The entity ensures processing is complete, accurate, and valid.",
            TrustPrinciple.PROCESSING_INTEGRITY,
            ControlCategory.PI1_PROCESSING_INTEGRITY,
            [
                "Input validation",
                "Processing controls",
                "Output verification",
            ],
        )

        # Confidentiality
        self._add_control(
            "C1.1",
            "Confidential Information Protection",
            "The entity protects confidential information.",
            TrustPrinciple.CONFIDENTIALITY,
            ControlCategory.C1_CONFIDENTIALITY,
            [
                "Confidential data identification",
                "Access restrictions",
                "Data loss prevention",
            ],
        )

        # Privacy
        self._add_control(
            "P1.1",
            "Privacy Notice and Consent",
            "The entity provides privacy notice and obtains consent.",
            TrustPrinciple.PRIVACY,
            ControlCategory.P1_PRIVACY,
            [
                "Privacy policy",
                "Consent management",
                "Data subject rights",
            ],
        )

    def _add_control(
        self,
        control_id: str,
        title: str,
        description: str,
        principle: TrustPrinciple,
        category: ControlCategory,
        points_of_focus: list[str],
    ) -> None:
        """Add a control to the framework."""
        self._controls[control_id] = Soc2Control(
            control_id=control_id,
            title=title,
            description=description,
            principle=principle,
            category=category,
            points_of_focus=points_of_focus,
        )

    def get_control(self, control_id: str) -> Optional[Soc2Control]:
        """Get a control by ID."""
        return self._controls.get(control_id)

    def list_controls(
        self,
        principle: Optional[TrustPrinciple] = None,
        category: Optional[ControlCategory] = None,
        status: Optional[ControlStatus] = None,
    ) -> list[Soc2Control]:
        """List controls with optional filters."""
        controls = list(self._controls.values())
        if principle:
            controls = [c for c in controls if c.principle == principle]
        if category:
            controls = [c for c in controls if c.category == category]
        if status:
            controls = [c for c in controls if c.status == status]
        return controls

    def update_control_status(
        self, control_id: str, status: ControlStatus, details: str = ""
    ) -> bool:
        """Update a control's implementation status."""
        control = self._controls.get(control_id)
        if control is None:
            return False
        control.status = status
        if details:
            control.implementation_details = details
        return True

    def set_control_owner(self, control_id: str, owner: str) -> bool:
        """Assign an owner to a control."""
        control = self._controls.get(control_id)
        if control is None:
            return False
        control.owner = owner
        return True


# ---------------------------------------------------------------------------
# Evidence Collection Manager
# ---------------------------------------------------------------------------


class EvidenceManager:
    """Manages evidence collection for SOC 2 controls."""

    def __init__(self, framework: Soc2ControlsFramework) -> None:
        self._framework = framework
        self._evidence: dict[str, EvidenceRecord] = {}

    def add_evidence(
        self,
        control_id: str,
        evidence_type: Union[EvidenceType, str],
        title: str,
        description: str = "",
        location: str = "",
        collected_by: str = "",
    ) -> EvidenceRecord:
        """Add evidence for a control."""
        # Convert string to enum if needed
        if isinstance(evidence_type, str):
            try:
                evidence_type = EvidenceType(evidence_type)
            except ValueError:
                evidence_type = EvidenceType.OTHER
        record = EvidenceRecord(
            evidence_id=str(uuid.uuid4()),
            control_id=control_id,
            evidence_type=evidence_type,
            title=title,
            description=description,
            collected_by=collected_by,
            location=location,
        )
        self._evidence[record.evidence_id] = record

        # Link to control
        control = self._framework.get_control(control_id)
        if control:
            control.evidence.append({
                "evidence_id": record.evidence_id,
                "title": title,
                "type": evidence_type.value,
            })

        return record

    def get_evidence(self, evidence_id: str) -> Optional[EvidenceRecord]:
        """Get evidence by ID."""
        return self._evidence.get(evidence_id)

    def list_evidence(
        self,
        control_id: Optional[str] = None,
        evidence_type: Optional[EvidenceType] = None,
    ) -> list[EvidenceRecord]:
        """List evidence with optional filters."""
        evidence = list(self._evidence.values())
        if control_id:
            evidence = [e for e in evidence if e.control_id == control_id]
        if evidence_type:
            evidence = [e for e in evidence if e.evidence_type == evidence_type]
        return evidence

    def archive_evidence(self, evidence_id: str) -> bool:
        """Archive evidence that is no longer current."""
        evidence = self._evidence.get(evidence_id)
        if evidence is None:
            return False
        evidence.is_archived = True
        return True


# ---------------------------------------------------------------------------
# Control Testing
# ---------------------------------------------------------------------------


class ControlTestingManager:
    """Manages SOC 2 control testing and assessments."""

    def __init__(self, framework: Soc2ControlsFramework) -> None:
        self._framework = framework
        self._tests: dict[str, ControlTest] = {}

    def create_test(
        self,
        control_id: str,
        procedure: str,
        tested_by: str = "",
        sample_size: int = 0,
    ) -> ControlTest:
        """Create a new control test."""
        test = ControlTest(
            test_id=str(uuid.uuid4()),
            control_id=control_id,
            test_procedure=procedure,
            tested_by=tested_by,
            sample_size=sample_size,
        )
        self._tests[test.test_id] = test
        return test

    def complete_test(
        self,
        test_id: str,
        status: ControlStatus,
        exceptions: int = 0,
        findings: str = "",
        recommendations: str = "",
    ) -> bool:
        """Complete a control test with results."""
        test = self._tests.get(test_id)
        if test is None:
            return False

        test.status = status
        test.exceptions_found = exceptions
        test.findings = findings
        test.recommendations = recommendations
        test.tested_at = time.time()

        # Update control status
        self._framework.update_control_status(test.control_id, status)
        control = self._framework.get_control(test.control_id)
        if control:
            control.last_tested_at = test.tested_at

        return True

    def get_test(self, test_id: str) -> Optional[ControlTest]:
        """Get a test by ID."""
        return self._tests.get(test_id)

    def list_tests(
        self,
        control_id: Optional[str] = None,
        status: Optional[ControlStatus] = None,
    ) -> list[ControlTest]:
        """List tests with optional filters."""
        tests = list(self._tests.values())
        if control_id:
            tests = [t for t in tests if t.control_id == control_id]
        if status:
            tests = [t for t in tests if t.status == status]
        return tests


# ---------------------------------------------------------------------------
# SOC 2 Compliance Manager
# ---------------------------------------------------------------------------


class Soc2ComplianceManager:
    """Main SOC 2 compliance management class.

    Coordinates controls, evidence, testing, gap analysis, and reporting.
    """

    def __init__(self) -> None:
        self._framework = Soc2ControlsFramework()
        self._evidence = EvidenceManager(self._framework)
        self._testing = ControlTestingManager(self._framework)
        self._gaps: dict[str, Soc2Gap] = {}
        self._auditor: Optional[str] = None
        self._audit_period_start: Optional[float] = None
        self._audit_period_end: Optional[float] = None

    # ---- Control Management ----

    @property
    def framework(self) -> Soc2ControlsFramework:
        """Access the controls framework."""
        return self._framework

    @property
    def evidence(self) -> EvidenceManager:
        """Access the evidence manager."""
        return self._evidence

    @property
    def testing(self) -> ControlTestingManager:
        """Access the testing manager."""
        return self._testing

    # ---- Gap Analysis ----

    def identify_gaps(self) -> list[Soc2Gap]:
        """Identify compliance gaps based on current control status."""
        gaps: list[Soc2Gap] = []
        for control in self._framework.list_controls():
            if control.status in (ControlStatus.NOT_STARTED, ControlStatus.FAILED):
                severity = "high" if control.risk_rating == "high" else "medium"
                gap = Soc2Gap(
                    gap_id=str(uuid.uuid4()),
                    control_id=control.control_id,
                    description=f"Control {control.control_id} - {control.title} is not implemented",
                    severity=severity,
                    remediation_steps=[
                        f"Implement {control.title}",
                        f"Address points of focus: {', '.join(control.points_of_focus[:3])}",
                        "Collect evidence of implementation",
                        "Test control operating effectiveness",
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
    ) -> Soc2Gap:
        """Manually add a gap."""
        gap = Soc2Gap(
            gap_id=str(uuid.uuid4()),
            control_id=control_id,
            description=description,
            severity=severity,
            remediation_steps=remediation_steps or [],
        )
        self._gaps[gap.gap_id] = gap
        return gap

    def close_gap(self, gap_id: str) -> bool:
        """Close a gap as remediated."""
        gap = self._gaps.get(gap_id)
        if gap is None:
            return False
        gap.closed = True
        gap.closed_at = time.time()
        return True

    def get_open_gaps(self) -> list[Soc2Gap]:
        """Get all open (unclosed) gaps."""
        return [g for g in self._gaps.values() if not g.closed]

    # ---- Compliance Reporting ----

    def generate_report(
        self, trust_principles: Optional[list[TrustPrinciple]] = None
    ) -> Soc2ComplianceReport:
        """Generate a SOC 2 compliance status report."""
        principles = trust_principles or [
            TrustPrinciple.SECURITY,
            TrustPrinciple.AVAILABILITY,
            TrustPrinciple.CONFIDENTIALITY,
        ]

        controls = [
            c for c in self._framework.list_controls()
            if c.principle in principles
        ]

        total = len(controls)
        operating = sum(1 for c in controls if c.status == ControlStatus.OPERATING_EFFECTIVELY)
        implemented = sum(1 for c in controls if c.status == ControlStatus.IMPLEMENTED)
        in_progress = sum(1 for c in controls if c.status == ControlStatus.IN_PROGRESS)
        not_started = sum(1 for c in controls if c.status == ControlStatus.NOT_STARTED)
        failed = sum(1 for c in controls if c.status == ControlStatus.FAILED)
        not_applicable = sum(1 for c in controls if c.status == ControlStatus.NOT_APPLICABLE)

        # By principle
        by_principle: dict[str, dict[str, int]] = {}
        for principle in principles:
            p_controls = [c for c in controls if c.principle == principle]
            by_principle[principle.value] = {
                "total": len(p_controls),
                "operating_effectively": sum(1 for c in p_controls if c.status == ControlStatus.OPERATING_EFFECTIVELY),
                "implemented": sum(1 for c in p_controls if c.status == ControlStatus.IMPLEMENTED),
                "in_progress": sum(1 for c in p_controls if c.status == ControlStatus.IN_PROGRESS),
                "not_started": sum(1 for c in p_controls if c.status == ControlStatus.NOT_STARTED),
            }

        # By category
        by_category: dict[str, dict[str, int]] = {}
        for control in controls:
            cat = control.category.value
            if cat not in by_category:
                by_category[cat] = {"total": 0, "implemented": 0}
            by_category[cat]["total"] += 1
            if control.status in (ControlStatus.IMPLEMENTED, ControlStatus.OPERATING_EFFECTIVELY):
                by_category[cat]["implemented"] += 1

        open_gaps = [g for g in self._gaps.values() if not g.closed]
        high_risk_gaps = [g for g in open_gaps if g.severity in ("high", "critical")]

        # Calculate score: operating + implemented + 0.5*in_progress / total
        score = (operating + implemented + 0.5 * in_progress) / total * 100 if total else 0

        summary = (
            f"SOC 2 compliance report: {total} total controls. "
            f"{operating} operating effectively, {implemented} implemented, "
            f"{in_progress} in progress, {not_started} not started. "
            f"Overall score: {score:.1f}%. "
            f"{len(open_gaps)} open gaps ({len(high_risk_gaps)} high risk)."
        )

        return Soc2ComplianceReport(
            report_id=str(uuid.uuid4()),
            generated_at=time.time(),
            trust_principles=principles,
            total_controls=total,
            operating_effectively=operating,
            implemented=implemented,
            in_progress=in_progress,
            not_started=not_started,
            failed=failed,
            not_applicable=not_applicable,
            by_principle=by_principle,
            by_category=by_category,
            open_gaps=len(open_gaps),
            high_risk_gaps=len(high_risk_gaps),
            overall_score=score,
            summary=summary,
        )

    def set_audit_period(self, start: float, end: float, auditor: str) -> None:
        """Set the audit period and auditor info."""
        self._audit_period_start = start
        self._audit_period_end = end
        self._auditor = auditor
