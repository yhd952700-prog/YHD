"""
ISO 27001 compliance module for LiuHao AI OS.

Implements ISO/IEC 27001 compliance features including ISMS management,
Annex A controls mapping, risk assessment framework, Statement of
Applicability (SoA), internal audit tracking, corrective action tracking,
and management review records.
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


class RiskLevel(str, Enum):
    """Risk assessment levels."""

    VERY_LOW = "very_low"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    VERY_HIGH = "very_high"


class RiskTreatment(str, Enum):
    """Risk treatment options."""

    AVOID = "avoid"
    MITIGATE = "mitigate"
    TRANSFER = "transfer"
    ACCEPT = "accept"


class ControlImplementationStatus(str, Enum):
    """Status of Annex A control implementation."""

    NOT_APPLICABLE = "not_applicable"
    NOT_STARTED = "not_started"
    PLANNED = "planned"
    PARTIALLY_IMPLEMENTED = "partially_implemented"
    FULLY_IMPLEMENTED = "fully_implemented"
    CERTIFIED = "certified"


class AuditStatus(str, Enum):
    """Status of an internal audit."""

    PLANNED = "planned"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    FOLLOW_UP_REQUIRED = "follow_up_required"
    CLOSED = "closed"


class CorrectiveActionStatus(str, Enum):
    """Status of a corrective action."""

    IDENTIFIED = "identified"
    ROOT_CAUSE_ANALYSIS = "root_cause_analysis"
    ACTION_PLAN = "action_plan"
    IMPLEMENTING = "implementing"
    VERIFIED = "verified"
    CLOSED = "closed"


class ManagementReviewTopic(str, Enum):
    """Topics for management review."""

    SUITABILITY = "suitability"
    ADEQUACY = "adequacy"
    EFFECTIVENESS = "effectiveness"
    RISK_ASSESSMENT = "risk_assessment"
    OBJECTIVES = "objectives"
    RESOURCES = "resources"
    IMPROVEMENTS = "improvements"


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------


@dataclass
class AnnexAControl:
    """ISO 27001 Annex A control."""

    control_id: str  # e.g., "A.5.1"
    title: str
    clause: str  # Annex A clause reference
    description: str
    category: str  # Control category
    status: ControlImplementationStatus = ControlImplementationStatus.NOT_STARTED
    applicable: bool = True
    justification: str = ""  # For SoA: why applicable or not
    implementation_method: str = ""
    evidence: list[str] = field(default_factory=list)
    last_reviewed_at: Optional[float] = None
    owner: Optional[str] = None
    risk_treatment: RiskTreatment = RiskTreatment.MITIGATE


@dataclass
class Risk:
    """Represents an identified risk."""

    risk_id: str
    description: str
    asset: str
    threat: str
    vulnerability: str
    likelihood: int = 3  # 1-5 scale
    impact: int = 3  # 1-5 scale
    risk_level: RiskLevel = RiskLevel.MEDIUM
    treatment: RiskTreatment = RiskTreatment.MITIGATE
    controls: list[str] = field(default_factory=list)  # Control IDs
    owner: Optional[str] = None
    identified_at: float = field(default_factory=lambda: time.time())
    last_assessed_at: Optional[float] = None
    residual_risk_level: Optional[RiskLevel] = None
    acceptance_justification: str = ""
    status: str = "open"  # open, treated, accepted, closed
    notes: str = ""


@dataclass
class RiskAssessment:
    """A risk assessment cycle."""

    assessment_id: str
    title: str
    description: str
    start_date: float = field(default_factory=lambda: time.time())
    end_date: Optional[float] = None
    methodology: str = ""
    scope: str = ""
    assessor: Optional[str] = None
    risks: list[str] = field(default_factory=list)  # risk IDs
    status: str = "in_progress"
    summary: str = ""


@dataclass
class StatementOfApplicability:
    """Statement of Applicability (SoA) - required by ISO 27001."""

    soa_id: str
    version: str = "1.0"
    created_at: float = field(default_factory=lambda: time.time())
    last_updated_at: Optional[float] = None
    approved_by: Optional[str] = None
    approved_at: Optional[float] = None
    total_controls: int = 0
    applicable_controls: int = 0
    not_applicable_controls: int = 0
    fully_implemented: int = 0
    partially_implemented: int = 0
    not_started: int = 0


@dataclass
class InternalAudit:
    """Represents an internal ISO 27001 audit."""

    audit_id: str
    title: str
    start_date: float
    end_date: float
    audit_type: str = "internal"  # internal, external, surveillance
    lead_auditor: str = ""
    auditors: list[str] = field(default_factory=list)
    scope: str = ""
    status: AuditStatus = AuditStatus.PLANNED
    findings: list[dict[str, str]] = field(default_factory=list)
    nonconformities: int = 0
    major_nonconformities: int = 0
    minor_nonconformities: int = 0
    observations: int = 0
    report_location: str = ""
    follow_up_date: Optional[float] = None


@dataclass
class CorrectiveAction:
    """Corrective and preventive action (CAPA)."""

    action_id: str
    title: str
    description: str
    source: str = ""  # Audit, incident, management review, etc.
    root_cause: str = ""
    action_plan: list[str] = field(default_factory=list)
    status: CorrectiveActionStatus = CorrectiveActionStatus.IDENTIFIED
    owner: Optional[str] = None
    identified_at: float = field(default_factory=lambda: time.time())
    due_date: Optional[float] = None
    completed_at: Optional[float] = None
    verified_at: Optional[float] = None
    effectiveness_verified: bool = False
    related_risk_ids: list[str] = field(default_factory=list)
    related_control_ids: list[str] = field(default_factory=list)


@dataclass
class ManagementReview:
    """Top management review record."""

    review_id: str
    title: str
    review_date: float = field(default_factory=lambda: time.time())
    attendees: list[str] = field(default_factory=list)
    topics: dict[str, str] = field(default_factory=dict)
    decisions: list[str] = field(default_factory=list)
    action_items: list[str] = field(default_factory=list)
    minutes_location: str = ""
    next_review_date: Optional[float] = None


# ---------------------------------------------------------------------------
# Annex A Controls Framework
# ---------------------------------------------------------------------------


class AnnexAFramework:
    """ISO 27001 Annex A controls framework.

    Contains the 14 control categories from Annex A of ISO/IEC 27001:2022.
    """

    def __init__(self) -> None:
        self._controls: dict[str, AnnexAControl] = {}
        self._init_controls()

    def _init_controls(self) -> None:
        """Initialize Annex A controls (representative sample of all 93 controls)."""

        # A.5: Information Security Policies
        self._add_control("A.5.1", "Information Security Policy", "A.5",
            "Management direction for information security.",
            "Policies", ["Policy document", "Management approval", "Regular review"])

        self._add_control("A.5.2", "Information Security Policies Review", "A.5",
            "Policies are reviewed at planned intervals.",
            "Policies", ["Review schedule", "Update process"])

        # A.6: Organization of Information Security
        self._add_control("A.6.1", "Information Security Roles and Responsibilities", "A.6",
            "Information security responsibilities are defined.",
            "Organization", ["Roles defined", "Responsibilities assigned"])

        self._add_control("A.6.2", "Segregation of Duties", "A.6",
            "Conflicting duties and areas of responsibility are segregated.",
            "Organization", ["SOD matrix", "Role design"])

        # A.7: Human Resource Security
        self._add_control("A.7.1", "Screening", "A.7",
            "Background verification checks on all candidates.",
            "HR Security", ["Background checks", "Reference verification"])

        self._add_control("A.7.2", "Terms and Conditions of Employment", "A.7",
            "Employees agree to relevant information security terms.",
            "HR Security", ["Employment contracts", "Confidentiality agreements"])

        self._add_control("A.7.3", "Information Security Awareness, Education and Training", "A.7",
            "All employees receive security awareness training.",
            "HR Security", ["Training program", "Awareness campaigns"])

        # A.8: Asset Management
        self._add_control("A.8.1", "Inventory of Assets", "A.8",
            "Assets associated with information and information processing are identified.",
            "Asset Management", ["Asset inventory", "Asset ownership"])

        self._add_control("A.8.2", "Classification of Information", "A.8",
            "Information is classified in terms of legal requirements and business impact.",
            "Asset Management", ["Classification scheme", "Labeling procedures"])

        # A.9: Access Control
        self._add_control("A.9.1", "Business Requirement of Access Control", "A.9",
            "Access control policy is defined and reviewed.",
            "Access Control", ["Access policy", "Business needs"])

        self._add_control("A.9.2", "User Access Provisioning", "A.9",
            "A formal user access provisioning process is implemented.",
            "Access Control", ["Provisioning process", "Approval workflow"])

        self._add_control("A.9.3", "Privileged Access Rights", "A.9",
            "Allocation and use of privileged access rights is controlled.",
            "Access Control", ["Privilege management", "Least privilege"])

        self._add_control("A.9.4", "Review of User Access Rights", "A.9",
            "Asset owners review user access rights at regular intervals.",
            "Access Control", ["Periodic reviews", "Access recertification"])

        self._add_control("A.9.5", "Authentication of Users", "A.9",
            "All users are required to authenticate before accessing systems.",
            "Access Control", ["Password policy", "MFA", "Authentication methods"])

        # A.10: Cryptography
        self._add_control("A.10.1", "Policy on the Use of Cryptographic Controls", "A.10",
            "A policy on the use of cryptographic controls is developed.",
            "Cryptography", ["Cryptography policy", "Key management"])

        self._add_control("A.10.2", "Key Management", "A.10",
            "A key management policy and system is implemented.",
            "Cryptography", ["Key lifecycle", "Secure storage"])

        # A.11: Physical and Environmental Security
        self._add_control("A.11.1", "Physical Security Perimeter", "A.11",
            "Security perimeters are used to protect areas containing sensitive information.",
            "Physical Security", ["Perimeter security", "Access control"])

        self._add_control("A.11.2", "Equipment Security", "A.11",
            "Equipment is sited and protected to reduce environmental threats.",
            "Physical Security", ["Equipment placement", "Protection measures"])

        # A.12: Operations Security
        self._add_control("A.12.1", "Operational Procedures and Responsibilities", "A.12",
            "Operating procedures for information processing facilities are documented.",
            "Operations", ["Runbooks", "Change management"])

        self._add_control("A.12.2", "Protection Against Malware", "A.12",
            "Detection, prevention, and recovery controls to protect against malware.",
            "Operations", ["Anti-malware", "Detection tools"])

        self._add_control("A.12.3", "Backup", "A.12",
            "Backup copies of information are taken and tested regularly.",
            "Operations", ["Backup policy", "Restore testing"])

        self._add_control("A.12.4", "Logging and Monitoring", "A.12",
            "Event logs are recorded and monitored.",
            "Operations", ["Audit logging", "Log review"])

        self._add_control("A.12.5", "Control of Operational Software", "A.12",
            "Installation of software on operational systems is controlled.",
            "Operations", ["Change management", "Approval process"])

        # A.13: Communications Security
        self._add_control("A.13.1", "Network Security Management", "A.13",
            "Networks are managed and controlled to protect information.",
            "Communications", ["Network security", "Segmentation"])

        self._add_control("A.13.2", "Information Transfer", "A.13",
            "Information transfer policies and procedures are in place.",
            "Communications", ["Secure transfer", "Encryption in transit"])

        # A.14: System Acquisition, Development and Maintenance
        self._add_control("A.14.1", "Security Requirements of Information Systems", "A.14",
            "Security requirements are identified and agreed for each system.",
            "Development", ["Secure SDLC", "Security requirements"])

        self._add_control("A.14.2", "Security in Development and Support Processes", "A.14",
            "Security is embedded in the development lifecycle.",
            "Development", ["Secure coding", "Security testing"])

        self._add_control("A.14.3", "Test Data", "A.14",
            "Test data must be selected carefully and protected.",
            "Development", ["Data masking", "Test environment security"])

        # A.15: Supplier Relationships
        self._add_control("A.15.1", "Information Security in Supplier Relationships", "A.15",
            "Information security risks from suppliers are addressed.",
            "Supplier", ["Vendor assessment", "Contracts"])

        self._add_control("A.15.2", "Supplier Service Delivery Management", "A.15",
            "Suppliers are monitored and reviewed.",
            "Supplier", ["Supplier monitoring", "Audits"])

        # A.16: Information Security Incident Management
        self._add_control("A.16.1", "Management of Information Security Incidents", "A.16",
            "A consistent approach to incident management is in place.",
            "Incident", ["Incident response", "Escalation procedures"])

        self._add_control("A.16.2", "Reporting Information Security Events", "A.16",
            "Events and weaknesses are reported through appropriate channels.",
            "Incident", ["Reporting mechanism", "Incident classification"])

        self._add_control("A.16.3", "Learning from Information Security Incidents", "A.16",
            "Lessons learned from incidents are used to improve controls.",
            "Incident", ["Post-incident review", "Continuous improvement"])

        # A.17: Business Continuity Management
        self._add_control("A.17.1", "Information Security Continuity", "A.17",
            "Information security continuity is embedded in business continuity.",
            "BCM", ["BCP", "Security in BC plans"])

        self._add_control("A.17.2", "Redundancies", "A.17",
            "Redundancies of information processing facilities are implemented.",
            "BCM", ["High availability", "Disaster recovery"])

        # A.18: Compliance
        self._add_control("A.18.1", "Identification of Applicable Legislation", "A.18",
            "All relevant legislative and regulatory requirements are identified.",
            "Compliance", ["Legal register", "Compliance review"])

        self._add_control("A.18.2", "Intellectual Property Rights", "A.18",
            "Procedures to ensure compliance with IPR legislation.",
            "Compliance", ["License management", "IPR policy"])

        self._add_control("A.18.3", "Protection of Records", "A.18",
            "Records are protected from loss, destruction, and falsification.",
            "Compliance", ["Record retention", "Archive management"])

    def _add_control(
        self,
        control_id: str,
        title: str,
        clause: str,
        description: str,
        category: str,
        evidence: list[str],
    ) -> None:
        """Add a control to the framework."""
        self._controls[control_id] = AnnexAControl(
            control_id=control_id,
            title=title,
            clause=clause,
            description=description,
            category=category,
            evidence=evidence,
        )

    def get_control(self, control_id: str) -> Optional[AnnexAControl]:
        """Get a control by ID."""
        return self._controls.get(control_id)

    def list_controls(
        self,
        category: Optional[str] = None,
        status: Optional[ControlImplementationStatus] = None,
        applicable_only: bool = False,
    ) -> list[AnnexAControl]:
        """List controls with optional filters."""
        controls = list(self._controls.values())
        if category:
            controls = [c for c in controls if c.category == category]
        if status:
            controls = [c for c in controls if c.status == status]
        if applicable_only:
            controls = [c for c in controls if c.applicable]
        return controls

    def update_status(
        self, control_id: str, status: ControlImplementationStatus, method: str = ""
    ) -> bool:
        """Update a control's implementation status."""
        control = self._controls.get(control_id)
        if control is None:
            return False
        control.status = status
        if method:
            control.implementation_method = method
        control.last_reviewed_at = time.time()
        return True

    def set_applicability(
        self, control_id: str, applicable: bool, justification: str
    ) -> bool:
        """Set whether a control is applicable (for SoA)."""
        control = self._controls.get(control_id)
        if control is None:
            return False
        control.applicable = applicable
        control.justification = justification
        return True


# ---------------------------------------------------------------------------
# Risk Assessment Framework
# ---------------------------------------------------------------------------


class RiskAssessmentManager:
    """Manages the ISO 27001 risk assessment process.

    Implements a qualitative risk assessment methodology with
    likelihood-impact matrix and risk treatment options.
    """

    # Risk level matrix: likelihood x impact
    _RISK_MATRIX: dict[tuple[int, int], RiskLevel] = {
        (1, 1): RiskLevel.VERY_LOW,
        (1, 2): RiskLevel.VERY_LOW,
        (1, 3): RiskLevel.LOW,
        (1, 4): RiskLevel.LOW,
        (1, 5): RiskLevel.MEDIUM,
        (2, 1): RiskLevel.VERY_LOW,
        (2, 2): RiskLevel.LOW,
        (2, 3): RiskLevel.LOW,
        (2, 4): RiskLevel.MEDIUM,
        (2, 5): RiskLevel.MEDIUM,
        (3, 1): RiskLevel.LOW,
        (3, 2): RiskLevel.LOW,
        (3, 3): RiskLevel.MEDIUM,
        (3, 4): RiskLevel.HIGH,
        (3, 5): RiskLevel.HIGH,
        (4, 1): RiskLevel.MEDIUM,
        (4, 2): RiskLevel.MEDIUM,
        (4, 3): RiskLevel.HIGH,
        (4, 4): RiskLevel.HIGH,
        (4, 5): RiskLevel.VERY_HIGH,
        (5, 1): RiskLevel.MEDIUM,
        (5, 2): RiskLevel.HIGH,
        (5, 3): RiskLevel.HIGH,
        (5, 4): RiskLevel.VERY_HIGH,
        (5, 5): RiskLevel.VERY_HIGH,
    }

    def __init__(self) -> None:
        self._risks: dict[str, Risk] = {}
        self._assessments: dict[str, RiskAssessment] = {}

    def identify_risk(
        self,
        description: str,
        asset: str,
        threat: str,
        vulnerability: str,
        likelihood: int = 3,
        impact: int = 3,
        owner: Optional[str] = None,
    ) -> Risk:
        """Identify and register a new risk."""
        risk_level = self._calculate_risk_level(likelihood, impact)
        risk = Risk(
            risk_id=str(uuid.uuid4()),
            description=description,
            asset=asset,
            threat=threat,
            vulnerability=vulnerability,
            likelihood=likelihood,
            impact=impact,
            risk_level=risk_level,
            owner=owner,
        )
        self._risks[risk.risk_id] = risk
        return risk

    def assess_risk(
        self, risk_id: str, likelihood: int, impact: int
    ) -> Optional[Risk]:
        """Reassess a risk's likelihood and impact."""
        risk = self._risks.get(risk_id)
        if risk is None:
            return None
        risk.likelihood = likelihood
        risk.impact = impact
        risk.risk_level = self._calculate_risk_level(likelihood, impact)
        risk.last_assessed_at = time.time()
        return risk

    def set_treatment(
        self,
        risk_id: str,
        treatment: RiskTreatment,
        controls: Optional[list[str]] = None,
        justification: str = "",
    ) -> Optional[Risk]:
        """Set the risk treatment strategy."""
        risk = self._risks.get(risk_id)
        if risk is None:
            return None
        risk.treatment = treatment
        if controls:
            risk.controls = controls
        if justification:
            risk.acceptance_justification = justification
        return risk

    def set_residual_risk(self, risk_id: str, level: RiskLevel) -> Optional[Risk]:
        """Set the residual risk level after treatment."""
        risk = self._risks.get(risk_id)
        if risk is None:
            return None
        risk.residual_risk_level = level
        return risk

    def get_risk(self, risk_id: str) -> Optional[Risk]:
        """Get a risk by ID."""
        return self._risks.get(risk_id)

    def list_risks(
        self,
        level: Optional[RiskLevel] = None,
        treatment: Optional[RiskTreatment] = None,
        status: Optional[str] = None,
    ) -> list[Risk]:
        """List risks with optional filters."""
        risks = list(self._risks.values())
        if level:
            risks = [r for r in risks if r.risk_level == level]
        if treatment:
            risks = [r for r in risks if r.treatment == treatment]
        if status:
            risks = [r for r in risks if r.status == status]
        return risks

    def create_assessment(
        self,
        title: str,
        description: str = "",
        methodology: str = "",
        scope: str = "",
        assessor: str = "",
    ) -> RiskAssessment:
        """Create a new risk assessment cycle."""
        assessment = RiskAssessment(
            assessment_id=str(uuid.uuid4()),
            title=title,
            description=description,
            methodology=methodology,
            scope=scope,
            assessor=assessor,
        )
        self._assessments[assessment.assessment_id] = assessment
        return assessment

    def close_assessment(self, assessment_id: str, summary: str) -> bool:
        """Close a risk assessment cycle."""
        assessment = self._assessments.get(assessment_id)
        if assessment is None:
            return False
        assessment.end_date = time.time()
        assessment.status = "completed"
        assessment.summary = summary
        return True

    def _calculate_risk_level(self, likelihood: int, impact: int) -> RiskLevel:
        """Calculate risk level from likelihood and impact."""
        likelihood = max(1, min(5, likelihood))
        impact = max(1, min(5, impact))
        return self._RISK_MATRIX.get((likelihood, impact), RiskLevel.MEDIUM)

    def get_risk_summary(self) -> dict[str, Any]:
        """Get a summary of all risks by level and treatment."""
        by_level: dict[str, int] = {}
        by_treatment: dict[str, int] = {}

        for risk in self._risks.values():
            level = risk.risk_level.value
            by_level[level] = by_level.get(level, 0) + 1
            treatment = risk.treatment.value
            by_treatment[treatment] = by_treatment.get(treatment, 0) + 1

        return {
            "total_risks": len(self._risks),
            "by_level": by_level,
            "by_treatment": by_treatment,
            "high_and_above": sum(
                1 for r in self._risks.values()
                if r.risk_level in (RiskLevel.HIGH, RiskLevel.VERY_HIGH)
            ),
        }


# ---------------------------------------------------------------------------
# ISMS Manager
# ---------------------------------------------------------------------------


class IsmsManager:
    """Information Security Management System (ISMS) manager.

    Coordinates Annex A controls, risk assessment, SoA, internal audits,
    corrective actions, and management reviews for ISO 27001 compliance.
    """

    def __init__(self) -> None:
        self._annex_a = AnnexAFramework()
        self._risk = RiskAssessmentManager()
        self._audits: dict[str, InternalAudit] = {}
        self._corrective_actions: dict[str, CorrectiveAction] = {}
        self._reviews: dict[str, ManagementReview] = {}
        self._soa: Optional[StatementOfApplicability] = None

    @property
    def annex_a(self) -> AnnexAFramework:
        """Access the Annex A controls framework."""
        return self._annex_a

    @property
    def risk_manager(self) -> RiskAssessmentManager:
        """Access the risk assessment manager."""
        return self._risk

    # ---- Statement of Applicability ----

    def generate_soa(
        self, approved_by: Optional[str] = None
    ) -> StatementOfApplicability:
        """Generate the Statement of Applicability (SoA)."""
        controls = self._annex_a.list_controls()
        applicable = [c for c in controls if c.applicable]
        not_applicable = [c for c in controls if not c.applicable]
        fully_implemented = [
            c for c in applicable
            if c.status == ControlImplementationStatus.FULLY_IMPLEMENTED
        ]
        partially_implemented = [
            c for c in applicable
            if c.status == ControlImplementationStatus.PARTIALLY_IMPLEMENTED
        ]
        not_started = [
            c for c in applicable
            if c.status == ControlImplementationStatus.NOT_STARTED
        ]

        self._soa = StatementOfApplicability(
            soa_id=str(uuid.uuid4()),
            total_controls=len(controls),
            applicable_controls=len(applicable),
            not_applicable_controls=len(not_applicable),
            fully_implemented=len(fully_implemented),
            partially_implemented=len(partially_implemented),
            not_started=len(not_started),
            last_updated_at=time.time(),
            approved_by=approved_by,
            approved_at=time.time() if approved_by else None,
        )
        return self._soa

    def get_soa(self) -> Optional[StatementOfApplicability]:
        """Get the current SoA."""
        return self._soa

    # ---- Internal Audit ----

    def schedule_audit(
        self,
        title: str,
        start_date: float,
        end_date: float,
        lead_auditor: str = "",
        scope: str = "",
    ) -> InternalAudit:
        """Schedule a new internal audit."""
        audit = InternalAudit(
            audit_id=str(uuid.uuid4()),
            title=title,
            start_date=start_date,
            end_date=end_date,
            lead_auditor=lead_auditor,
            scope=scope,
        )
        self._audits[audit.audit_id] = audit
        return audit

    def complete_audit(
        self,
        audit_id: str,
        findings: list[dict[str, str]],
        major_nc: int = 0,
        minor_nc: int = 0,
        observations: int = 0,
    ) -> bool:
        """Complete an audit with findings."""
        audit = self._audits.get(audit_id)
        if audit is None:
            return False
        audit.findings = findings
        audit.major_nonconformities = major_nc
        audit.minor_nonconformities = minor_nc
        audit.observations = observations
        audit.nonconformities = major_nc + minor_nc
        audit.status = (
            AuditStatus.FOLLOW_UP_REQUIRED if (major_nc + minor_nc) > 0
            else AuditStatus.COMPLETED
        )
        return True

    def get_audit(self, audit_id: str) -> Optional[InternalAudit]:
        """Get an audit by ID."""
        return self._audits.get(audit_id)

    def list_audits(self, status: Optional[AuditStatus] = None) -> list[InternalAudit]:
        """List audits with optional status filter."""
        audits = list(self._audits.values())
        if status:
            audits = [a for a in audits if a.status == status]
        return audits

    # ---- Corrective Actions ----

    def create_corrective_action(
        self,
        title: str,
        description: str,
        source: str = "",
        owner: Optional[str] = None,
        due_date: Optional[float] = None,
    ) -> CorrectiveAction:
        """Create a new corrective action."""
        action = CorrectiveAction(
            action_id=str(uuid.uuid4()),
            title=title,
            description=description,
            source=source,
            owner=owner,
            due_date=due_date,
        )
        self._corrective_actions[action.action_id] = action
        return action

    def update_corrective_action_status(
        self, action_id: str, status: CorrectiveActionStatus
    ) -> bool:
        """Update corrective action status."""
        action = self._corrective_actions.get(action_id)
        if action is None:
            return False
        action.status = status
        if status == CorrectiveActionStatus.VERIFIED:
            action.verified_at = time.time()
            action.effectiveness_verified = True
        if status == CorrectiveActionStatus.CLOSED:
            action.completed_at = time.time()
        return True

    def get_corrective_action(self, action_id: str) -> Optional[CorrectiveAction]:
        """Get a corrective action by ID."""
        return self._corrective_actions.get(action_id)

    def list_corrective_actions(
        self, status: Optional[CorrectiveActionStatus] = None
    ) -> list[CorrectiveAction]:
        """List corrective actions with optional filter."""
        actions = list(self._corrective_actions.values())
        if status:
            actions = [a for a in actions if a.status == status]
        return actions

    # ---- Management Review ----

    def create_management_review(
        self,
        title: str,
        attendees: list[str],
        topics: dict[str, str],
    ) -> ManagementReview:
        """Create a new management review record."""
        review = ManagementReview(
            review_id=str(uuid.uuid4()),
            title=title,
            attendees=attendees,
            topics=topics,
        )
        self._reviews[review.review_id] = review
        return review

    def complete_review(
        self,
        review_id: str,
        decisions: list[str],
        action_items: list[str],
        next_review_date: Optional[float] = None,
    ) -> bool:
        """Complete a management review."""
        review = self._reviews.get(review_id)
        if review is None:
            return False
        review.decisions = decisions
        review.action_items = action_items
        review.next_review_date = next_review_date
        return True

    def get_review(self, review_id: str) -> Optional[ManagementReview]:
        """Get a management review by ID."""
        return self._reviews.get(review_id)
