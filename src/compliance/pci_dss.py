"""
PCI DSS compliance module for LiuHao AI OS.

Implements PCI DSS (Payment Card Industry Data Security Standard)
compliance features including cardholder data environment definition,
6 goals / 12 requirements mapping, network security checks, access
control tracking, vulnerability management, testing and monitoring
records, and SAQ (Self-Assessment Questionnaire) interface.
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


class PciDssRequirement(str, Enum):
    """PCI DSS 12 Requirements."""

    REQ_01 = "req_01"  # Install and maintain a firewall
    REQ_02 = "req_02"  # Do not use vendor-supplied defaults
    REQ_03 = "req_03"  # Protect stored cardholder data
    REQ_04 = "req_04"  # Encrypt transmission of cardholder data
    REQ_05 = "req_05"  # Use and regularly update anti-virus
    REQ_06 = "req_06"  # Develop and maintain secure systems
    REQ_07 = "req_07"  # Restrict access to cardholder data by business need-to-know
    REQ_08 = "req_08"  # Identify users and authenticate access
    REQ_09 = "req_09"  # Restrict physical access to cardholder data
    REQ_10 = "req_10"  # Track and monitor all access
    REQ_11 = "req_11"  # Regularly test security systems and processes
    REQ_12 = "req_12"  # Maintain an information security policy


class PciDssGoal(str, Enum):
    """PCI DSS 6 Goals."""

    GOAL_1 = "goal_1"  # Build and Maintain a Secure Network and Systems
    GOAL_2 = "goal_2"  # Protect Cardholder Data
    GOAL_3 = "goal_3"  # Maintain a Vulnerability Management Program
    GOAL_4 = "goal_4"  # Implement Strong Access Control Measures
    GOAL_5 = "goal_5"  # Regularly Monitor and Test Networks
    GOAL_6 = "goal_6"  # Maintain an Information Security Policy


class CardDataType(str, Enum):
    """Types of cardholder data."""

    PAN = "pan"  # Primary Account Number
    CVV = "cvv"  # Card Verification Value
    EXPIRY = "expiry"  # Expiration date
    CARDHOLDER_NAME = "cardholder_name"
    TRACK_DATA = "track_data"  # Track 1/2 data
    PIN = "pin"  # PIN / PIN block
    SENSITIVE_AUTH = "sensitive_auth"  # Sensitive authentication data


class SaqType(str, Enum):
    """PCI DSS SAQ types."""

    SAQ_A = "A"
    SAQ_A_EP = "A-EP"
    SAQ_B = "B"
    SAQ_B_IP = "B-IP"
    SAQ_C = "C"
    SAQ_C_VT = "C-VT"
    SAQ_D = "D"
    SAQ_P2PE = "P2PE"


class CdeComponentType(str, Enum):
    """CDE component types."""

    SERVER = "server"
    DATABASE = "database"
    NETWORK_DEVICE = "network_device"
    WORKSTATION = "workstation"
    APPLICATION = "application"
    FIREWALL = "firewall"
    LOAD_BALANCER = "load_balancer"
    STORAGE = "storage"


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------


@dataclass
class CdeComponent:
    """Component in the Cardholder Data Environment (CDE)."""

    component_id: str
    name: str
    component_type: CdeComponentType
    ip_address: str
    description: str = ""
    in_scope: bool = True
    in_cde: bool = True
    pci_dss_requirements: list[str] = field(default_factory=list)
    owner: str = ""
    last_scanned_at: Optional[float] = None
    last_patched_at: Optional[float] = None
    encryption_enabled: bool = False
    network_zone: str = ""  # CDE, DMZ, internal, etc.
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class PciDssControl:
    """Represents a PCI DSS control/requirement."""

    requirement_id: str
    title: str
    goal: PciDssGoal
    description: str = ""
    sub_requirements: list[str] = field(default_factory=list)
    implemented: bool = False
    implementation_details: str = ""
    evidence: list[str] = field(default_factory=list)
    last_assessed_at: Optional[float] = None
    assessor: Optional[str] = None
    priority: str = "high"  # critical, high, medium, low


@dataclass
class VulnerabilityScan:
    """Record of a vulnerability scan."""

    scan_id: str
    component_id: str
    scanned_at: float = field(default_factory=lambda: time.time())
    scanner: str = ""
    critical_count: int = 0
    high_count: int = 0
    medium_count: int = 0
    low_count: int = 0
    informational_count: int = 0
    scan_report_location: str = ""
    status: str = "completed"  # scheduled, running, completed, failed
    remediated: bool = False


@dataclass
class PenetrationTest:
    """Record of a penetration test."""

    test_id: str
    test_type: str  # network, application, wireless, social engineering
    conducted_at: float = field(default_factory=lambda: time.time())
    conducted_by: str = ""
    scope: str = ""
    findings_count: int = 0
    critical_findings: int = 0
    high_findings: int = 0
    medium_findings: int = 0
    low_findings: int = 0
    report_location: str = ""
    retest_complete: bool = False
    retest_date: Optional[float] = None


@dataclass
class AccessReviewRecord:
    """Record of a PCI DSS access review (Req 7)."""

    review_id: str
    review_type: str  # periodic, on_change, termination
    conducted_at: float = field(default_factory=lambda: time.time())
    conducted_by: str = ""
    total_users_reviewed: int = 0
    access_revoked: int = 0
    exceptions_found: int = 0
    scope: str = ""  # All users, specific system, etc.
    report_location: str = ""


@dataclass
class SaqQuestion:
    """A question in the PCI DSS Self-Assessment Questionnaire."""

    question_id: str
    question_text: str
    requirement: str
    saq_types: list[SaqType] = field(default_factory=list)
    answer: Optional[bool] = None  # True = Yes, False = No, None = N/A
    compensation_control: bool = False
    notes: str = ""
    evidence_reference: str = ""


@dataclass
class PciDssComplianceReport:
    """PCI DSS compliance status report."""

    report_id: str
    generated_at: float
    total_requirements: int = 0
    fully_implemented: int = 0
    partially_implemented: int = 0
    not_implemented: int = 0
    by_goal: dict[str, dict[str, int]] = field(default_factory=dict)
    cde_components: int = 0
    open_vulnerabilities_critical: int = 0
    open_vulnerabilities_high: int = 0
    last_pen_test_date: Optional[float] = None
    last_access_review_date: Optional[float] = None
    overall_compliance_percentage: float = 0.0
    summary: str = ""


# ---------------------------------------------------------------------------
# Cardholder Data Environment (CDE) Manager
# ---------------------------------------------------------------------------


class CdeManager:
    """Manages the Cardholder Data Environment (CDE).

    Tracks all components in scope for PCI DSS, their network
    segmentation, and security status.
    """

    def __init__(self) -> None:
        self._components: dict[str, CdeComponent] = {}

    def add_component(
        self,
        name: str,
        component_type: CdeComponentType,
        ip_address: str,
        in_cde: bool = True,
        in_scope: bool = True,
        network_zone: str = "cde",
        description: str = "",
    ) -> CdeComponent:
        """Add a component to the CDE inventory."""
        component = CdeComponent(
            component_id=str(uuid.uuid4()),
            name=name,
            component_type=component_type,
            ip_address=ip_address,
            in_cde=in_cde,
            in_scope=in_scope,
            network_zone=network_zone,
            description=description,
        )
        self._components[component.component_id] = component
        return component

    def get_component(self, component_id: str) -> Optional[CdeComponent]:
        """Get a component by ID."""
        return self._components.get(component_id)

    def list_components(
        self,
        in_cde_only: bool = False,
        in_scope_only: bool = False,
        component_type: Optional[CdeComponentType] = None,
        network_zone: Optional[str] = None,
    ) -> list[CdeComponent]:
        """List CDE components with optional filters."""
        components = list(self._components.values())
        if in_cde_only:
            components = [c for c in components if c.in_cde]
        if in_scope_only:
            components = [c for c in components if c.in_scope]
        if component_type:
            components = [c for c in components if c.component_type == component_type]
        if network_zone:
            components = [c for c in components if c.network_zone == network_zone]
        return components

    def remove_from_cde(self, component_id: str, reason: str = "") -> bool:
        """Remove a component from the CDE (mark as out of scope)."""
        component = self._components.get(component_id)
        if component is None:
            return False
        component.in_cde = False
        component.in_scope = False
        if reason:
            component.metadata["removal_reason"] = reason
        return True

    def get_cde_summary(self) -> dict[str, Any]:
        """Get a summary of the CDE."""
        cde_components = [c for c in self._components.values() if c.in_cde]
        in_scope = [c for c in self._components.values() if c.in_scope]
        by_type: dict[str, int] = {}
        by_zone: dict[str, int] = {}

        for c in cde_components:
            type_name = c.component_type.value
            by_type[type_name] = by_type.get(type_name, 0) + 1
            by_zone[c.network_zone] = by_zone.get(c.network_zone, 0) + 1

        return {
            "total_components": len(self._components),
            "in_cde": len(cde_components),
            "in_scope": len(in_scope),
            "by_type": by_type,
            "by_zone": by_zone,
        }


# ---------------------------------------------------------------------------
# PCI DSS Controls Framework
# ---------------------------------------------------------------------------


class PciDssControls:
    """PCI DSS 12 Requirements controls framework.

    Maps the 6 goals and 12 requirements of PCI DSS v4.0.
    """

    # Goal -> Requirements mapping
    GOAL_REQUIREMENTS: dict[PciDssGoal, list[PciDssRequirement]] = {
        PciDssGoal.GOAL_1: [PciDssRequirement.REQ_01, PciDssRequirement.REQ_02],
        PciDssGoal.GOAL_2: [PciDssRequirement.REQ_03, PciDssRequirement.REQ_04],
        PciDssGoal.GOAL_3: [PciDssRequirement.REQ_05, PciDssRequirement.REQ_06],
        PciDssGoal.GOAL_4: [PciDssRequirement.REQ_07, PciDssRequirement.REQ_08, PciDssRequirement.REQ_09],
        PciDssGoal.GOAL_5: [PciDssRequirement.REQ_10, PciDssRequirement.REQ_11],
        PciDssGoal.GOAL_6: [PciDssRequirement.REQ_12],
    }

    def __init__(self) -> None:
        self._controls: dict[str, PciDssControl] = {}
        self._init_controls()

    def _init_controls(self) -> None:
        """Initialize all 12 PCI DSS requirements."""
        requirements = [
            (PciDssRequirement.REQ_01, PciDssGoal.GOAL_1,
             "Install and maintain a firewall configuration to protect cardholder data",
             ["Firewall configuration standards",
              "Router configuration standards",
              "Internet connection restrictions",
              "DMZ implementation",
              "Personal firewall on laptops"]),
            (PciDssRequirement.REQ_02, PciDssGoal.GOAL_1,
             "Do not use vendor-supplied defaults for system passwords and other security parameters",
             ["Change default passwords",
              "Remove unnecessary default accounts",
              "Secure configuration standards",
              "Disable unnecessary functionality"]),
            (PciDssRequirement.REQ_03, PciDssGoal.GOAL_2,
             "Protect stored cardholder data",
             ["Data retention and disposal policy",
              "PAN storage limitations",
              "Sensitive authentication data not stored after authorization",
              "Mask PAN when displayed",
              "Render PAN unreadable anywhere stored",
              "Key management procedures"]),
            (PciDssRequirement.REQ_04, PciDssGoal.GOAL_2,
             "Encrypt transmission of cardholder data across open, public networks",
             ["Strong cryptography for transmission",
              "SSL/TLS implementation",
              "Trusted keys and certificates",
              "Only trusted keys accepted"]),
            (PciDssRequirement.REQ_05, PciDssGoal.GOAL_3,
             "Use and regularly update anti-virus software or programs",
             ["Anti-virus on all systems",
              "Anti-virus kept up-to-date",
              "Anti-virus actively running",
              "Anti-virus logs maintained"]),
            (PciDssRequirement.REQ_06, PciDssGoal.GOAL_3,
             "Develop and maintain secure systems and applications",
             ["Security patches installed within one month",
              "Software development security",
              "Web application firewall",
              "Code review",
              "Change control process"]),
            (PciDssRequirement.REQ_07, PciDssGoal.GOAL_4,
             "Restrict access to cardholder data by business need-to-know",
             ["Access control policy",
              "Least privilege principle",
              "Access control list",
              "Access rights reviewed periodically"]),
            (PciDssRequirement.REQ_08, PciDssGoal.GOAL_4,
             "Identify users and authenticate access to system components",
             ["Unique user IDs",
              "Strong password policy",
              "Password complexity requirements",
              "Session timeout",
              "Multi-factor authentication"]),
            (PciDssRequirement.REQ_09, PciDssGoal.GOAL_4,
             "Restrict physical access to cardholder data",
             ["Facility access controls",
              "Visitor controls",
              "Sensitive area access",
              "Media storage and access",
              "Media destruction"]),
            (PciDssRequirement.REQ_10, PciDssGoal.GOAL_5,
             "Track and monitor all access to network resources and cardholder data",
             ["Audit trail for all access to cardholder data",
              "Audit trail includes all user actions",
              "Time synchronization",
              "Secure audit trails",
              "Daily log review",
              "File integrity monitoring"]),
            (PciDssRequirement.REQ_11, PciDssGoal.GOAL_5,
             "Regularly test security systems and processes",
             ["Quarterly vulnerability scans",
              "Annual penetration testing",
              "Wireless network testing",
              "Intrusion detection systems",
              "File integrity monitoring"]),
            (PciDssRequirement.REQ_12, PciDssGoal.GOAL_6,
             "Maintain a policy that addresses information security for all personnel",
             ["Information security policy",
              "Security policy reviewed annually",
              "Security awareness program",
              "Incident response plan",
              "Acceptable use policy",
              "Background checks"]),
        ]

        for req_id, goal, title, sub_reqs in requirements:
            self._controls[req_id.value] = PciDssControl(
                requirement_id=req_id.value,
                title=title,
                goal=goal,
                sub_requirements=sub_reqs,
            )

    def get_control(self, requirement_id: str) -> Optional[PciDssControl]:
        """Get a control by requirement ID."""
        return self._controls.get(requirement_id)

    def list_controls(
        self,
        goal: Optional[PciDssGoal] = None,
        implemented_only: bool = False,
    ) -> list[PciDssControl]:
        """List controls with optional filters."""
        controls = list(self._controls.values())
        if goal:
            controls = [c for c in controls if c.goal == goal]
        if implemented_only:
            controls = [c for c in controls if c.implemented]
        return controls

    def mark_implemented(
        self, requirement_id: str, details: str = "", evidence: Optional[str] = None
    ) -> bool:
        """Mark a requirement as implemented."""
        control = self._controls.get(requirement_id)
        if control is None:
            return False
        control.implemented = True
        control.implementation_details = details
        control.last_assessed_at = time.time()
        if evidence:
            control.evidence.append(evidence)
        return True

    def get_requirements_for_goal(self, goal: PciDssGoal) -> list[PciDssControl]:
        """Get all requirements for a specific goal."""
        req_ids = self.GOAL_REQUIREMENTS.get(goal, [])
        return [self._controls[r.value] for r in req_ids if r.value in self._controls]


# ---------------------------------------------------------------------------
# Vulnerability Management (PCI DSS Req 5, 6, 11)
# ---------------------------------------------------------------------------


class PciVulnerabilityManager:
    """Manages PCI DSS vulnerability management.

    Tracks vulnerability scans, penetration tests, and patch management
    for PCI DSS requirements 5, 6, and 11.
    """

    def __init__(self) -> None:
        self._scans: dict[str, VulnerabilityScan] = {}
        self._pen_tests: dict[str, PenetrationTest] = {}

    def record_scan(
        self,
        component_id: str,
        scanner: str = "",
        critical: int = 0,
        high: int = 0,
        medium: int = 0,
        low: int = 0,
        informational: int = 0,
        report_location: str = "",
    ) -> VulnerabilityScan:
        """Record a vulnerability scan result."""
        scan = VulnerabilityScan(
            scan_id=str(uuid.uuid4()),
            component_id=component_id,
            scanner=scanner,
            critical_count=critical,
            high_count=high,
            medium_count=medium,
            low_count=low,
            informational_count=informational,
            scan_report_location=report_location,
        )
        self._scans[scan.scan_id] = scan
        return scan

    def record_penetration_test(
        self,
        test_type: str,
        conducted_by: str = "",
        scope: str = "",
        critical: int = 0,
        high: int = 0,
        medium: int = 0,
        low: int = 0,
        report_location: str = "",
    ) -> PenetrationTest:
        """Record a penetration test result."""
        total = critical + high + medium + low
        test = PenetrationTest(
            test_id=str(uuid.uuid4()),
            test_type=test_type,
            conducted_by=conducted_by,
            scope=scope,
            findings_count=total,
            critical_findings=critical,
            high_findings=high,
            medium_findings=medium,
            low_findings=low,
            report_location=report_location,
        )
        self._pen_tests[test.test_id] = test
        return test

    def get_open_high_critical(self) -> int:
        """Get count of open high/critical vulnerabilities."""
        total = 0
        for scan in self._scans.values():
            if not scan.remediated:
                total += scan.critical_count + scan.high_count
        return total

    def get_last_quarterly_scans(self) -> list[VulnerabilityScan]:
        """Get scans from the last quarter (PCI DSS requires quarterly)."""
        three_months_ago = time.time() - (90 * 24 * 60 * 60)
        return [s for s in self._scans.values() if s.scanned_at >= three_months_ago]

    def get_last_annual_pen_test(self) -> Optional[PenetrationTest]:
        """Get the most recent penetration test (PCI DSS requires annual)."""
        if not self._pen_tests:
            return None
        return max(self._pen_tests.values(), key=lambda t: t.conducted_at)

    def mark_scan_remediated(self, scan_id: str) -> bool:
        """Mark a scan's vulnerabilities as remediated."""
        scan = self._scans.get(scan_id)
        if scan is None:
            return False
        scan.remediated = True
        return True


# ---------------------------------------------------------------------------
# Access Control Tracking (PCI DSS Req 7, 8)
# ---------------------------------------------------------------------------


class PciAccessControlManager:
    """Manages PCI DSS access control tracking (Requirements 7 & 8)."""

    def __init__(self) -> None:
        self._access_reviews: dict[str, AccessReviewRecord] = {}

    def record_access_review(
        self,
        review_type: str,
        conducted_by: str = "",
        total_users: int = 0,
        access_revoked: int = 0,
        exceptions_found: int = 0,
        scope: str = "",
    ) -> AccessReviewRecord:
        """Record a periodic access review (Req 7)."""
        review = AccessReviewRecord(
            review_id=str(uuid.uuid4()),
            review_type=review_type,
            conducted_by=conducted_by,
            total_users_reviewed=total_users,
            access_revoked=access_revoked,
            exceptions_found=exceptions_found,
            scope=scope,
        )
        self._access_reviews[review.review_id] = review
        return review

    def get_last_review(self, review_type: Optional[str] = None) -> Optional[AccessReviewRecord]:
        """Get the most recent access review."""
        reviews = list(self._access_reviews.values())
        if review_type:
            reviews = [r for r in reviews if r.review_type == review_type]
        if not reviews:
            return None
        return max(reviews, key=lambda r: r.conducted_at)

    def is_quarterly_review_current(self) -> bool:
        """Check if a quarterly access review has been done within 90 days."""
        last = self.get_last_review()
        if last is None:
            return False
        return time.time() - last.conducted_at <= (90 * 24 * 60 * 60)


# ---------------------------------------------------------------------------
# SAQ Interface
# ---------------------------------------------------------------------------


class SaqManager:
    """PCI DSS Self-Assessment Questionnaire interface.

    Provides a framework for completing PCI DSS SAQs based on
    the applicable SAQ type for the merchant environment.
    """

    def __init__(self, saq_type: SaqType = SaqType.SAQ_D) -> None:
        self._saq_type = saq_type
        self._questions: dict[str, SaqQuestion] = {}
        self._merchant_name: str = ""
        self._dba: str = ""
        self._merchant_id: str = ""

    def add_question(
        self,
        question_id: str,
        question_text: str,
        requirement: str,
        saq_types: list[SaqType],
    ) -> SaqQuestion:
        """Add a SAQ question."""
        question = SaqQuestion(
            question_id=question_id,
            question_text=question_text,
            requirement=requirement,
            saq_types=saq_types,
        )
        self._questions[question_id] = question
        return question

    def answer_question(
        self,
        question_id: str,
        answer: bool,
        compensation_control: bool = False,
        notes: str = "",
        evidence: str = "",
    ) -> bool:
        """Answer a SAQ question."""
        question = self._questions.get(question_id)
        if question is None:
            return False
        question.answer = answer
        question.compensation_control = compensation_control
        question.notes = notes
        question.evidence_reference = evidence
        return True

    def get_questions_for_saq(self, saq_type: Optional[SaqType] = None) -> list[SaqQuestion]:
        """Get all questions applicable to a SAQ type."""
        target = saq_type or self._saq_type
        return [q for q in self._questions.values() if target in q.saq_types]

    def get_completion_percentage(self) -> float:
        """Calculate SAQ completion percentage."""
        questions = self.get_questions_for_saq()
        if not questions:
            return 0.0
        answered = sum(1 for q in questions if q.answer is not None)
        return (answered / len(questions)) * 100

    def get_yes_percentage(self) -> float:
        """Calculate percentage of 'Yes' answers among answered questions."""
        questions = self.get_questions_for_saq()
        answered = [q for q in questions if q.answer is not None]
        if not answered:
            return 0.0
        yes_count = sum(1 for q in answered if q.answer)
        return (yes_count / len(answered)) * 100


# ---------------------------------------------------------------------------
# PCI DSS Compliance Manager
# ---------------------------------------------------------------------------


class PciDssComplianceManager:
    """Main PCI DSS compliance manager.

    Coordinates CDE management, controls, vulnerability management,
    access reviews, and compliance reporting.
    """

    def __init__(self) -> None:
        self._cde = CdeManager()
        self._controls = PciDssControls()
        self._vulnerability = PciVulnerabilityManager()
        self._access = PciAccessControlManager()
        self._saq = SaqManager()
        self._merchant_level: str = ""  # Level 1-4

    @property
    def cde(self) -> CdeManager:
        return self._cde

    @property
    def controls(self) -> PciDssControls:
        return self._controls

    @property
    def vulnerability(self) -> PciVulnerabilityManager:
        return self._vulnerability

    @property
    def access_control(self) -> PciAccessControlManager:
        return self._access

    @property
    def saq(self) -> SaqManager:
        return self._saq

    def generate_report(self) -> PciDssComplianceReport:
        """Generate a PCI DSS compliance status report."""
        all_controls = self._controls.list_controls()
        implemented = [c for c in all_controls if c.implemented]

        by_goal: dict[str, dict[str, int]] = {}
        for goal in PciDssGoal:
            goal_controls = self._controls.get_requirements_for_goal(goal)
            by_goal[goal.value] = {
                "total": len(goal_controls),
                "implemented": sum(1 for c in goal_controls if c.implemented),
            }

        cde_summary = self._cde.get_cde_summary()
        last_pen_test = self._vulnerability.get_last_annual_pen_test()
        last_access_review = self._access.get_last_review()

        compliance_pct = (len(implemented) / len(all_controls) * 100) if all_controls else 0

        summary = (
            f"PCI DSS compliance report: {len(all_controls)} requirements, "
            f"{len(implemented)} implemented ({compliance_pct:.1f}%). "
            f"CDE has {cde_summary['in_cde']} components. "
            f"{self._vulnerability.get_open_high_critical()} open high/critical vulnerabilities."
        )

        return PciDssComplianceReport(
            report_id=str(uuid.uuid4()),
            generated_at=time.time(),
            total_requirements=len(all_controls),
            fully_implemented=len(implemented),
            partially_implemented=0,  # Would track sub-reqs
            not_implemented=len(all_controls) - len(implemented),
            by_goal=by_goal,
            cde_components=cde_summary["in_cde"],
            open_vulnerabilities_critical=sum(
                s.critical_count for s in self._vulnerability._scans.values() if not s.remediated
            ),
            open_vulnerabilities_high=sum(
                s.high_count for s in self._vulnerability._scans.values() if not s.remediated
            ),
            last_pen_test_date=last_pen_test.conducted_at if last_pen_test else None,
            last_access_review_date=last_access_review.conducted_at if last_access_review else None,
            overall_compliance_percentage=compliance_pct,
            summary=summary,
        )
