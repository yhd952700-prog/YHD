"""
HIPAA compliance module for LiuHao AI OS.

Implements HIPAA (Health Insurance Portability and Accountability Act)
compliance features including PHI handling, access controls, audit
controls, breach notification, BAA tracking, Security Rule requirements,
and minimum necessary standard enforcement.
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


class PhiType(str, Enum):
    """Types of Protected Health Information (PHI)."""

    NAME = "name"
    SSN = "ssn"  # Social Security Number
    MEDICAL_RECORD = "medical_record"
    PRESCRIPTION = "prescription"
    DIAGNOSIS = "diagnosis"
    TREATMENT = "treatment"
    INSURANCE = "insurance"
    LAB_RESULT = "lab_result"
    IMAGING = "imaging"
    APPOINTMENT = "appointment"
    BILLING = "billing"
    GENETIC = "genetic"
    MENTAL_HEALTH = "mental_health"
    EMERGENCY_CONTACT = "emergency_contact"
    OTHER = "other"


class AccessType(str, Enum):
    """Types of PHI access."""

    VIEW = "view"
    CREATE = "create"
    MODIFY = "modify"
    DELETE = "delete"
    EXPORT = "export"
    PRINT = "print"
    TRANSFER = "transfer"
    DISCLOSURE = "disclosure"


class BaaStatus(str, Enum):
    """Status of a Business Associate Agreement."""

    PENDING = "pending"
    SIGNED = "signed"
    EXPIRED = "expired"
    TERMINATED = "terminated"
    UNDER_REVIEW = "under_review"


class BreachClassification(str, Enum):
    """HIPAA breach classification."""

    BREACH = "breach"  # Confirmed breach of unsecured PHI
    RCE_EXCEPTION = "rce_exception"  # RCE encryption safe harbor
    LOW_PROBABILITY = "low_probability"  # Low probability of compromise
    NOT_A_BREACH = "not_a_breach"  # Not considered a breach


class SecurityRuleStandard(str, Enum):
    """HIPAA Security Rule standards."""

    ACCESS_CONTROL = "access_control"
    AUDIT_CONTROLS = "audit_controls"
    INTEGRITY = "integrity"
    PERSON_OR_ENTITY_AUTHENTICATION = "authentication"
    TRANSMISSION_SECURITY = "transmission_security"
    SECURITY_MANAGEMENT = "security_management"
    ASSIGNED_SECURITY_RESPONSIBILITY = "assigned_responsibility"
    WORKFORCE_SECURITY = "workforce_security"
    INFORMATION_ACCESS_MANAGEMENT = "information_access_management"
    SECURITY_AWARENESS_AND_TRAINING = "security_training"
    SECURITY_INCIDENT_PROCEDURES = "incident_procedures"
    CONTINGENCY_PLAN = "contingency_plan"
    EVALUATION = "evaluation"
    CONTRACTS_OTHER_ARRANGEMENTS = "contracts"
    FACILITY_ACCESS_CONTROLS = "facility_access"
    WORKSTATION_USE = "workstation_use"
    WORKSTATION_SECURITY = "workstation_security"
    DEVICE_AND_MEDIA_CONTROLS = "device_controls"


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------


@dataclass
class PhiAccessLog:
    """Record of PHI access for audit purposes."""

    log_id: str
    user_id: str
    phi_type: PhiType
    phi_id: str
    access_type: AccessType
    timestamp: float = field(default_factory=lambda: time.time())
    ip_address: Optional[str] = None
    purpose: str = ""  # Reason for access
    minimum_necessary_compliant: Optional[bool] = None
    authorized: bool = True
    patient_id: Optional[str] = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class PhiDataRecord:
    """Represents a PHI data record with tracking."""

    record_id: str
    phi_type: PhiType
    patient_id: str
    created_at: float = field(default_factory=lambda: time.time())
    created_by: Optional[str] = None
    last_accessed_at: Optional[float] = None
    last_accessed_by: Optional[str] = None
    last_modified_at: Optional[float] = None
    last_modified_by: Optional[str] = None
    is_encrypted_at_rest: bool = True
    is_deidentified: bool = False
    access_count: int = 0
    authorized_users: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class BusinessAssociateAgreement:
    """Represents a Business Associate Agreement (BAA)."""

    baa_id: str
    entity_name: str
    entity_type: str  # Business Associate, Subcontractor, etc.
    status: BaaStatus
    signed_date: Optional[float] = None
    effective_date: Optional[float] = None
    expiration_date: Optional[float] = None
    termination_date: Optional[float] = None
    phi_types_involved: list[PhiType] = field(default_factory=list)
    services_provided: list[str] = field(default_factory=list)
    security_requirements: list[str] = field(default_factory=list)
    breach_notification_terms: str = ""
    indemnification: bool = False
    insurance_requirements: str = ""
    contact_person: str = ""
    contact_email: str = ""
    last_audit_date: Optional[float] = None
    risk_assessment_score: Optional[float] = None
    notes: str = ""


@dataclass
class HipaaBreachRecord:
    """HIPAA breach incident record."""

    breach_id: str
    detected_at: float
    classification: BreachClassification
    description: str = ""
    phi_types_affected: list[PhiType] = field(default_factory=list)
    individuals_affected: int = 0
    unsecured_phi: bool = True  # Was the PHI unsecured?
    rce_applied: bool = False  # Was RCE encryption applied?
    risk_assessment: dict[str, str] = field(default_factory=dict)
    mitigation_steps: list[str] = field(default_factory=list)
    notification_required: bool = True
    hhs_notified: bool = False
    hhs_notified_at: Optional[float] = None
    media_notification_required: bool = False  # >500 individuals in a state
    individuals_notified: bool = False
    individuals_notified_at: Optional[float] = None
    notification_deadline: float = 0.0  # 60 days from discovery
    status: str = "investigating"
    assigned_officer: Optional[str] = None

    def __post_init__(self) -> None:
        if self.notification_deadline == 0.0:
            self.notification_deadline = self.detected_at + (60 * 24 * 60 * 60)  # 60 days


@dataclass
class MinimumNecessityEvaluation:
    """Evaluation of minimum necessary standard for a request."""

    evaluation_id: str
    requestor_id: str
    phi_type: PhiType
    purpose: str
    requested_fields: list[str] = field(default_factory=list)
    approved_fields: list[str] = field(default_factory=list)
    is_compliant: bool = True
    justification: str = ""
    evaluated_at: float = field(default_factory=lambda: time.time())
    evaluated_by: Optional[str] = None


# ---------------------------------------------------------------------------
# PHI Access Control
# ---------------------------------------------------------------------------


class PhiAccessManager:
    """Manages access to Protected Health Information.

    Enforces authorization, minimum necessary standard, and
    maintains comprehensive audit logs of all PHI access.
    """

    def __init__(self) -> None:
        self._records: dict[str, PhiDataRecord] = {}
        self._access_logs: list[PhiAccessLog] = []
        self._user_authorizations: dict[str, set[str]] = {}  # user_id -> set of phi_ids

    # ---- Access control ----

    def check_access(
        self,
        user_id: str,
        phi_id: str,
        access_type: AccessType,
        purpose: str = "",
    ) -> tuple[bool, Optional[str]]:
        """Check if a user is authorized to access PHI.

        Returns:
            Tuple of (is_authorized, reason_if_denied)
        """
        record = self._records.get(phi_id)
        if record is None:
            return False, "PHI record not found"

        # Check user authorization
        authorized_users = self._get_authorized_users(phi_id)
        if user_id not in authorized_users and user_id not in record.authorized_users:
            return False, "User not authorized to access this PHI"

        # Check access type-specific permissions
        if access_type in (AccessType.MODIFY, AccessType.DELETE) and not self._can_modify(user_id, phi_id):
            return False, "User not authorized to modify this PHI"

        if access_type == AccessType.EXPORT and not self._can_export(user_id, phi_id):
            return False, "User not authorized to export this PHI"

        return True, None

    def record_access(
        self,
        user_id: str,
        phi_id: str,
        access_type: AccessType,
        purpose: str = "",
        ip_address: Optional[str] = None,
        minimum_necessary_compliant: Optional[bool] = None,
        authorized: bool = True,
    ) -> PhiAccessLog:
        """Record a PHI access event in the audit log."""
        record = self._records.get(phi_id)
        phi_type = record.phi_type if record else PhiType.OTHER
        patient_id = record.patient_id if record else None

        log_entry = PhiAccessLog(
            log_id=str(uuid.uuid4()),
            user_id=user_id,
            phi_type=phi_type,
            phi_id=phi_id,
            access_type=access_type,
            ip_address=ip_address,
            purpose=purpose,
            minimum_necessary_compliant=minimum_necessary_compliant,
            authorized=authorized,
            patient_id=patient_id,
        )

        self._access_logs.append(log_entry)

        # Update record metadata
        if record:
            record.access_count += 1
            record.last_accessed_at = log_entry.timestamp
            record.last_accessed_by = user_id
            if access_type == AccessType.MODIFY:
                record.last_modified_at = log_entry.timestamp
                record.last_modified_by = user_id

        return log_entry

    def authorize_user(
        self, user_id: str, phi_id: str, granted_by: str
    ) -> bool:
        """Authorize a user to access a specific PHI record."""
        record = self._records.get(phi_id)
        if record is None:
            return False
        if user_id not in record.authorized_users:
            record.authorized_users.append(user_id)
        return True

    def revoke_access(self, user_id: str, phi_id: str) -> bool:
        """Revoke a user's access to a PHI record."""
        record = self._records.get(phi_id)
        if record is None:
            return False
        if user_id in record.authorized_users:
            record.authorized_users.remove(user_id)
            return True
        return False

    # ---- Audit controls ----

    def get_access_logs(
        self,
        user_id: Optional[str] = None,
        phi_id: Optional[str] = None,
        patient_id: Optional[str] = None,
        access_type: Optional[AccessType] = None,
        start_time: Optional[float] = None,
        end_time: Optional[float] = None,
        limit: int = 100,
    ) -> list[PhiAccessLog]:
        """Query PHI access logs."""
        logs = list(self._access_logs)

        if user_id:
            logs = [l for l in logs if l.user_id == user_id]
        if phi_id:
            logs = [l for l in logs if l.phi_id == phi_id]
        if patient_id:
            logs = [l for l in logs if l.patient_id == patient_id]
        if access_type:
            logs = [l for l in logs if l.access_type == access_type]
        if start_time:
            logs = [l for l in logs if l.timestamp >= start_time]
        if end_time:
            logs = [l for l in logs if l.timestamp <= end_time]

        return logs[-limit:]

    def get_unauthorized_attempts(
        self, start_time: Optional[float] = None
    ) -> list[PhiAccessLog]:
        """Get unauthorized PHI access attempts."""
        logs = self._access_logs if start_time is None else [
            l for l in self._access_logs if l.timestamp >= start_time
        ]
        return [l for l in logs if not l.authorized]

    # ---- PHI record management ----

    def create_phi_record(
        self,
        phi_type: PhiType,
        patient_id: str,
        created_by: str,
    ) -> PhiDataRecord:
        """Create a new PHI record."""
        record = PhiDataRecord(
            record_id=str(uuid.uuid4()),
            phi_type=phi_type,
            patient_id=patient_id,
            created_by=created_by,
        )
        self._records[record.record_id] = record
        return record

    def deidentify_record(self, phi_id: str) -> bool:
        """Mark a PHI record as de-identified."""
        record = self._records.get(phi_id)
        if record is None:
            return False
        record.is_deidentified = True
        return True

    def _get_authorized_users(self, phi_id: str) -> set[str]:
        """Get all users authorized for a PHI record."""
        return self._user_authorizations.get(phi_id, set())

    def _can_modify(self, user_id: str, phi_id: str) -> bool:
        """Check if a user can modify PHI (simplified)."""
        record = self._records.get(phi_id)
        if record and record.created_by == user_id:
            return True
        return user_id in self._user_authorizations.get(phi_id, set())

    def _can_export(self, user_id: str, phi_id: str) -> bool:
        """Check if a user can export PHI (simplified)."""
        # Export typically requires additional authorization
        return self._can_modify(user_id, phi_id)


# ---------------------------------------------------------------------------
# Minimum Necessary Standard
# ---------------------------------------------------------------------------


class MinimumNecessityEnforcer:
    """Enforces the HIPAA Minimum Necessary Standard.

    The minimum necessary standard requires that protected health
    information used or disclosed be limited to the minimum necessary
    to accomplish the intended purpose.
    """

    def __init__(self) -> None:
        self._role_profiles: dict[str, set[PhiType]] = {}
        self._purpose_profiles: dict[str, set[PhiType]] = {}
        self._evaluations: list[MinimumNecessityEvaluation] = []
        self._init_default_profiles()

    def _init_default_profiles(self) -> None:
        """Initialize default minimum necessary profiles."""
        # Role-based profiles
        self._role_profiles["physician"] = {
            PhiType.MEDICAL_RECORD, PhiType.DIAGNOSIS, PhiType.TREATMENT,
            PhiType.PRESCRIPTION, PhiType.LAB_RESULT, PhiType.IMAGING,
            PhiType.APPOINTMENT, PhiType.NAME,
        }
        self._role_profiles["nurse"] = {
            PhiType.MEDICAL_RECORD, PhiType.TREATMENT,
            PhiType.APPOINTMENT, PhiType.NAME, PhiType.VITAL_SIGNS if hasattr(PhiType, 'VITAL_SIGNS') else PhiType.OTHER,
        }
        self._role_profiles["billing"] = {
            PhiType.BILLING, PhiType.INSURANCE,
            PhiType.NAME, PhiType.MEDICAL_RECORD,
        }
        self._role_profiles["receptionist"] = {
            PhiType.NAME, PhiType.APPOINTMENT,
            PhiType.INSURANCE, PhiType.BILLING,
            PhiType.EMERGENCY_CONTACT,
        }
        self._role_profiles["researcher"] = {
            PhiType.OTHER,  # Only de-identified data
        }

        # Purpose-based profiles
        self._purpose_profiles["treatment"] = {
            PhiType.MEDICAL_RECORD, PhiType.DIAGNOSIS, PhiType.TREATMENT,
            PhiType.PRESCRIPTION, PhiType.LAB_RESULT, PhiType.IMAGING,
            PhiType.NAME, PhiType.APPOINTMENT,
        }
        self._purpose_profiles["payment"] = {
            PhiType.BILLING, PhiType.INSURANCE,
            PhiType.NAME, PhiType.MEDICAL_RECORD,
        }
        self._purpose_profiles["healthcare_operations"] = {
            PhiType.NAME, PhiType.APPOINTMENT,
            PhiType.BILLING, PhiType.MEDICAL_RECORD,
        }

    def evaluate_request(
        self,
        requestor_id: str,
        role: str,
        phi_type: PhiType,
        purpose: str,
        requested_fields: list[str],
        evaluated_by: Optional[str] = None,
    ) -> MinimumNecessityEvaluation:
        """Evaluate a PHI access request against minimum necessary standard.

        Args:
            requestor_id: ID of the person requesting access.
            role: Role of the requestor.
            phi_type: Type of PHI being requested.
            purpose: Purpose of the access (treatment, payment, etc.).
            requested_fields: List of specific data fields requested.
            evaluated_by: Person conducting the evaluation.

        Returns:
            MinimumNecessityEvaluation with the decision.
        """
        # Determine allowed phi types based on role and purpose
        role_allowed = self._role_profiles.get(role, set())
        purpose_allowed = self._purpose_profiles.get(purpose, set())
        allowed = role_allowed & purpose_allowed  # Intersection

        is_compliant = phi_type in allowed
        approved_fields = requested_fields if is_compliant else []

        # If not compliant, provide justification
        justification = ""
        if not is_compliant:
            justification = (
                f"PHI type '{phi_type.value}' not authorized for role '{role}' "
                f"and purpose '{purpose}'. "
                f"Allowed types: {[t.value for t in allowed]}"
            )

        evaluation = MinimumNecessityEvaluation(
            evaluation_id=str(uuid.uuid4()),
            requestor_id=requestor_id,
            phi_type=phi_type,
            purpose=purpose,
            requested_fields=requested_fields,
            approved_fields=approved_fields,
            is_compliant=is_compliant,
            justification=justification,
            evaluated_by=evaluated_by,
        )

        self._evaluations.append(evaluation)
        return evaluation

    def add_role_profile(self, role: str, phi_types: list[PhiType]) -> None:
        """Add or update a role-based minimum necessary profile."""
        self._role_profiles[role] = set(phi_types)

    def add_purpose_profile(self, purpose: str, phi_types: list[PhiType]) -> None:
        """Add or update a purpose-based minimum necessary profile."""
        self._purpose_profiles[purpose] = set(phi_types)

    def get_allowed_phi_types(self, role: str, purpose: str) -> list[PhiType]:
        """Get PHI types allowed for a role + purpose combination."""
        role_allowed = self._role_profiles.get(role, set())
        purpose_allowed = self._purpose_profiles.get(purpose, set())
        return list(role_allowed & purpose_allowed)


# ---------------------------------------------------------------------------
# BAA Management
# ---------------------------------------------------------------------------


class BaaManager:
    """Manages Business Associate Agreements (BAAs)."""

    def __init__(self) -> None:
        self._baas: dict[str, BusinessAssociateAgreement] = {}

    def create_baa(
        self,
        entity_name: str,
        entity_type: str,
        phi_types: list[PhiType],
        services: list[str],
    ) -> BusinessAssociateAgreement:
        """Create a new BAA record (pending status)."""
        baa = BusinessAssociateAgreement(
            baa_id=str(uuid.uuid4()),
            entity_name=entity_name,
            entity_type=entity_type,
            status=BaaStatus.PENDING,
            phi_types_involved=phi_types,
            services_provided=services,
        )
        self._baas[baa.baa_id] = baa
        return baa

    def get_baa(self, baa_id: str) -> Optional[BusinessAssociateAgreement]:
        """Get a BAA by ID."""
        return self._baas.get(baa_id)

    def sign_baa(self, baa_id: str, effective_date: float, expiration_date: float) -> bool:
        """Mark a BAA as signed."""
        baa = self._baas.get(baa_id)
        if baa is None:
            return False
        baa.status = BaaStatus.SIGNED
        baa.signed_date = time.time()
        baa.effective_date = effective_date
        baa.expiration_date = expiration_date
        return True

    def terminate_baa(self, baa_id: str, reason: str = "") -> bool:
        """Terminate a BAA."""
        baa = self._baas.get(baa_id)
        if baa is None:
            return False
        baa.status = BaaStatus.TERMINATED
        baa.termination_date = time.time()
        if reason:
            baa.notes += f"\nTermination reason: {reason}"
        return True

    def get_active_baas(self) -> list[BusinessAssociateAgreement]:
        """Get all currently active BAAs."""
        now = time.time()
        return [
            b for b in self._baas.values()
            if b.status == BaaStatus.SIGNED
            and b.effective_date is not None
            and now >= b.effective_date
            and (b.expiration_date is None or now < b.expiration_date)
        ]

    def get_expiring_baas(self, days: int = 30) -> list[BusinessAssociateAgreement]:
        """Get BAAs expiring within the specified number of days."""
        now = time.time()
        cutoff = now + (days * 24 * 60 * 60)
        return [
            b for b in self._baas.values()
            if b.status == BaaStatus.SIGNED
            and b.expiration_date is not None
            and now < b.expiration_date <= cutoff
        ]

    def has_baa(self, entity_name: str) -> bool:
        """Check if there's an active BAA with an entity."""
        now = time.time()
        for b in self._baas.values():
            if (
                b.entity_name == entity_name
                and b.status == BaaStatus.SIGNED
                and b.effective_date is not None
                and now >= b.effective_date
                and (b.expiration_date is None or now < b.expiration_date)
            ):
                return True
        return False

    def list_baas(self, status: Optional[BaaStatus] = None) -> list[BusinessAssociateAgreement]:
        """List BAAs with optional status filter."""
        baas = list(self._baas.values())
        if status:
            baas = [b for b in baas if b.status == status]
        return baas


# ---------------------------------------------------------------------------
# Security Rule Compliance
# ---------------------------------------------------------------------------


class SecurityRuleChecker:
    """HIPAA Security Rule compliance checker.

    Tracks compliance with the Security Rule's administrative,
    physical, and technical safeguards.
    """

    def __init__(self) -> None:
        self._controls: dict[str, dict[str, Any]] = {}
        self._init_security_rule_controls()

    def _init_security_rule_controls(self) -> None:
        """Initialize Security Rule controls with required specifications."""
        controls = {
            # Administrative Safeguards
            "security_management": {
                "standard": SecurityRuleStandard.SECURITY_MANAGEMENT.value,
                "category": "administrative",
                "required": True,
                "implementations": [
                    "risk_analysis",
                    "risk_management",
                    "sanction_policy",
                    "information_system_activity_review",
                ],
                "implemented": False,
                "evidence": [],
            },
            "assigned_security_responsibility": {
                "standard": SecurityRuleStandard.ASSIGNED_SECURITY_RESPONSIBILITY.value,
                "category": "administrative",
                "required": True,
                "implementations": ["security_officer"],
                "implemented": False,
                "evidence": [],
            },
            "workforce_security": {
                "standard": SecurityRuleStandard.WORKFORCE_SECURITY.value,
                "category": "administrative",
                "required": True,
                "implementations": [
                    "authorization_and_supervision",
                    "workforce_clearance",
                    "termination_procedures",
                ],
                "implemented": False,
                "evidence": [],
            },
            "information_access_management": {
                "standard": SecurityRuleStandard.INFORMATION_ACCESS_MANAGEMENT.value,
                "category": "administrative",
                "required": True,
                "implementations": [
                    "isolating_healthcare_roles",
                    "access_authorization",
                    "access_establishment_and_modification",
                ],
                "implemented": False,
                "evidence": [],
            },
            "security_awareness_and_training": {
                "standard": SecurityRuleStandard.SECURITY_AWARENESS_AND_TRAINING.value,
                "category": "administrative",
                "required": True,
                "implementations": [
                    "security_reminders",
                    "protection_from_malicious_software",
                    "log_in_monitoring",
                    "password_management",
                ],
                "implemented": False,
                "evidence": [],
            },
            "security_incident_procedures": {
                "standard": SecurityRuleStandard.SECURITY_INCIDENT_PROCEDURES.value,
                "category": "administrative",
                "required": True,
                "implementations": ["response_and_reporting"],
                "implemented": False,
                "evidence": [],
            },
            "contingency_plan": {
                "standard": SecurityRuleStandard.CONTINGENCY_PLAN.value,
                "category": "administrative",
                "required": True,
                "implementations": [
                    "data_backup_plan",
                    "disaster_recovery_plan",
                    "emergency_mode_operation_plan",
                    "testing_and_revision_procedures",
                    "applications_and_data_criticality_analysis",
                ],
                "implemented": False,
                "evidence": [],
            },
            "evaluation": {
                "standard": SecurityRuleStandard.EVALUATION.value,
                "category": "administrative",
                "required": True,
                "implementations": ["periodic_evaluation"],
                "implemented": False,
                "evidence": [],
            },
            "contracts": {
                "standard": SecurityRuleStandard.CONTRACTS_OTHER_ARRANGEMENTS.value,
                "category": "administrative",
                "required": True,
                "implementations": ["business_associate_contracts"],
                "implemented": False,
                "evidence": [],
            },
            # Physical Safeguards
            "facility_access_controls": {
                "standard": SecurityRuleStandard.FACILITY_ACCESS_CONTROLS.value,
                "category": "physical",
                "required": True,
                "implementations": [
                    "contingency_operations",
                    "facility_security_plan",
                    "access_control_and_validation_procedures",
                    "maintenance_records",
                ],
                "implemented": False,
                "evidence": [],
            },
            "workstation_use": {
                "standard": SecurityRuleStandard.WORKSTATION_USE.value,
                "category": "physical",
                "required": True,
                "implementations": ["workstation_use"],
                "implemented": False,
                "evidence": [],
            },
            "workstation_security": {
                "standard": SecurityRuleStandard.WORKSTATION_SECURITY.value,
                "category": "physical",
                "required": True,
                "implementations": ["physical_safeguards"],
                "implemented": False,
                "evidence": [],
            },
            "device_and_media_controls": {
                "standard": SecurityRuleStandard.DEVICE_AND_MEDIA_CONTROLS.value,
                "category": "physical",
                "required": True,
                "implementations": [
                    "disposal",
                    "media_reuse",
                    "accountability",
                    "data_backup_and_storage",
                ],
                "implemented": False,
                "evidence": [],
            },
            # Technical Safeguards
            "access_control": {
                "standard": SecurityRuleStandard.ACCESS_CONTROL.value,
                "category": "technical",
                "required": True,
                "implementations": [
                    "unique_user_identification",
                    "emergency_access_procedure",
                    "automatic_logoff",
                    "encryption_and_decryption",
                ],
                "implemented": False,
                "evidence": [],
            },
            "audit_controls": {
                "standard": SecurityRuleStandard.AUDIT_CONTROLS.value,
                "category": "technical",
                "required": True,
                "implementations": ["audit_logging"],
                "implemented": False,
                "evidence": [],
            },
            "integrity": {
                "standard": SecurityRuleStandard.INTEGRITY.value,
                "category": "technical",
                "required": True,
                "implementations": ["mechanism_to_authenticate_electronic_protected_health_information"],
                "implemented": False,
                "evidence": [],
            },
            "authentication": {
                "standard": SecurityRuleStandard.PERSON_OR_ENTITY_AUTHENTICATION.value,
                "category": "technical",
                "required": True,
                "implementations": ["verification"],
                "implemented": False,
                "evidence": [],
            },
            "transmission_security": {
                "standard": SecurityRuleStandard.TRANSMISSION_SECURITY.value,
                "category": "technical",
                "required": True,
                "implementations": [
                    "integrity_controls",
                    "encryption",
                ],
                "implemented": False,
                "evidence": [],
            },
        }

        self._controls = controls

    def mark_control_implemented(
        self, control_id: str, evidence: str = "", implementation: Optional[str] = None
    ) -> bool:
        """Mark a control as implemented with evidence."""
        control = self._controls.get(control_id)
        if control is None:
            return False
        control["implemented"] = True
        if evidence:
            control["evidence"].append(evidence)
        return True

    def get_compliance_status(self) -> dict[str, Any]:
        """Get overall Security Rule compliance status."""
        total = len(self._controls)
        implemented = sum(1 for c in self._controls.values() if c["implemented"])

        by_category: dict[str, dict[str, int]] = {}
        for cid, control in self._controls.items():
            cat = control["category"]
            if cat not in by_category:
                by_category[cat] = {"total": 0, "implemented": 0}
            by_category[cat]["total"] += 1
            if control["implemented"]:
                by_category[cat]["implemented"] += 1

        return {
            "total_controls": total,
            "implemented_controls": implemented,
            "compliance_percentage": (implemented / total * 100) if total else 0,
            "by_category": by_category,
            "controls": dict(self._controls),
        }

    def list_controls(
        self,
        category: Optional[str] = None,
        implemented_only: bool = False,
    ) -> dict[str, dict[str, Any]]:
        """List security controls with optional filters."""
        controls = dict(self._controls)
        if category:
            controls = {k: v for k, v in controls.items() if v["category"] == category}
        if implemented_only:
            controls = {k: v for k, v in controls.items() if v["implemented"]}
        return controls


# ---------------------------------------------------------------------------
# HIPAA Breach Management
# ---------------------------------------------------------------------------


class HipaaBreachManager:
    """Manages HIPAA breach incidents and notifications.

    Per HIPAA Breach Notification Rule, breaches of unsecured PHI
    must be notified to HHS within 60 days of discovery.
    """

    def __init__(self) -> None:
        self._breaches: dict[str, HipaaBreachRecord] = {}

    def report_breach(
        self,
        description: str,
        phi_types: list[PhiType],
        individuals_affected: int,
        unsecured_phi: bool = True,
        detected_at: Optional[float] = None,
    ) -> HipaaBreachRecord:
        """Report a new potential breach."""
        breach = HipaaBreachRecord(
            breach_id=str(uuid.uuid4()),
            detected_at=detected_at or time.time(),
            classification=BreachClassification.BREACH if unsecured_phi else BreachClassification.RCE_EXCEPTION,
            description=description,
            phi_types_affected=phi_types,
            individuals_affected=individuals_affected,
            unsecured_phi=unsecured_phi,
        )
        self._breaches[breach.breach_id] = breach
        return breach

    def classify_breach(
        self,
        breach_id: str,
        classification: BreachClassification,
        risk_assessment: Optional[dict[str, str]] = None,
    ) -> bool:
        """Classify a breach incident."""
        breach = self._breaches.get(breach_id)
        if breach is None:
            return False
        breach.classification = classification
        if risk_assessment:
            breach.risk_assessment = risk_assessment

        # Determine if notification is required
        breach.notification_required = classification == BreachClassification.BREACH

        # Media notification required if >500 individuals in a state/jurisdiction
        breach.media_notification_required = (
            classification == BreachClassification.BREACH
            and individuals_affected >= 500
            if (individuals_affected := breach.individuals_affected) else False
        )
        return True

    def notify_hhs(self, breach_id: str) -> bool:
        """Record notification to HHS."""
        breach = self._breaches.get(breach_id)
        if breach is None:
            return False
        breach.hhs_notified = True
        breach.hhs_notified_at = time.time()
        return True

    def notify_individuals(self, breach_id: str) -> bool:
        """Record notification to affected individuals."""
        breach = self._breaches.get(breach_id)
        if breach is None:
            return False
        breach.individuals_notified = True
        breach.individuals_notified_at = time.time()
        return True

    def get_pending_notifications(self) -> list[HipaaBreachRecord]:
        """Get breaches requiring notification."""
        return [
            b for b in self._breaches.values()
            if b.notification_required and not b.hhs_notified
        ]

    def is_within_60day_window(self, breach_id: str) -> bool:
        """Check if breach is still within 60-day notification window."""
        breach = self._breaches.get(breach_id)
        if breach is None:
            return False
        return time.time() < breach.notification_deadline

    def get_breach(self, breach_id: str) -> Optional[HipaaBreachRecord]:
        """Get a breach record by ID."""
        return self._breaches.get(breach_id)
