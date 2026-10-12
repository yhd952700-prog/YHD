"""
GDPR compliance module for LiuHao AI OS.

Implements GDPR (General Data Protection Regulation) compliance features
including data subject rights, consent management, data processing
records, DPIA templates, right to be forgotten workflow, data breach
notification procedures, and privacy by design checklists.
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


class DataSubjectRight(str, Enum):
    """GDPR data subject rights."""

    ACCESS = "access"  # Right of access
    RECTIFICATION = "rectification"  # Right to rectification
    ERASURE = "erasure"  # Right to erasure (right to be forgotten)
    RESTRICTION = "restriction"  # Right to restriction of processing
    DATA_PORTABILITY = "data_portability"  # Right to data portability
    OBJECT = "object"  # Right to object
    AUTOMATED_DECISION = "automated_decision"  # Rights related to automated decision-making


class ConsentStatus(str, Enum):
    """Status of a consent record."""

    GRANTED = "granted"
    WITHDRAWN = "withdrawn"
    EXPIRED = "expired"
    PENDING = "pending"


class ProcessingActivityType(str, Enum):
    """Types of data processing activities."""

    COLLECTION = "collection"
    STORAGE = "storage"
    PROCESSING = "processing"
    SHARING = "sharing"
    TRANSFER = "transfer"
    DELETION = "deletion"
    ANALYSIS = "analysis"
    PROFILING = "profiling"


class DataCategory(str, Enum):
    """Categories of personal data."""

    IDENTIFICATION = "identification"  # Name, ID, etc.
    CONTACT = "contact"  # Email, phone, address
    FINANCIAL = "financial"  # Payment info, bank details
    HEALTH = "health"  # Medical/health data (special category)
    BIOMETRIC = "biometric"  # Fingerprints, face data (special category)
    LOCATION = "location"  # GPS, IP-based location
    USAGE = "usage"  # Usage patterns, behavior
    SENSITIVE = "sensitive"  # Racial, political, religious (special category)


class DpiaStatus(str, Enum):
    """Status of a Data Protection Impact Assessment."""

    DRAFT = "draft"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    REQUIRES_CONSULTATION = "requires_consultation"
    HIGH_RISK = "high_risk"


class BreachStatus(str, Enum):
    """Status of a data breach notification."""

    DETECTED = "detected"
    ASSESSING = "assessing"
    NOTIFIED = "notified"
    RESOLVED = "resolved"
    CLOSED = "closed"


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------


@dataclass
class ConsentRecord:
    """Represents a user consent record."""

    consent_id: str
    user_id: str
    purpose: str  # What the consent is for
    status: ConsentStatus
    granted_at: float
    withdrawn_at: Optional[float] = None
    expires_at: Optional[float] = None
    scope: list[str] = field(default_factory=list)  # Specific data types
    method: str = "explicit"  # How consent was obtained
    ip_address: Optional[str] = None
    user_agent: Optional[str] = None
    version: str = "1.0"  # Consent form version
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class DataProcessingRecord:
    """Record of a data processing activity (Article 30)."""

    record_id: str
    controller: str
    processor: Optional[str]
    activity_type: ProcessingActivityType
    data_categories: list[DataCategory]
    data_subjects: list[str]  # Categories of data subjects
    purposes: list[str]
    recipients: list[str] = field(default_factory=list)
    transfers_to_third_countries: list[str] = field(default_factory=list)
    retention_period: Optional[str] = None
    security_measures: list[str] = field(default_factory=list)
    legal_basis: str = ""
    started_at: float = field(default_factory=lambda: time.time())
    ended_at: Optional[float] = None
    description: str = ""
    risk_level: str = "medium"


@dataclass
class DataSubjectRequest:
    """A request from a data subject exercising their rights."""

    request_id: str
    user_id: str
    right: DataSubjectRight
    status: str = "pending"  # pending, processing, completed, rejected
    submitted_at: float = field(default_factory=lambda: time.time())
    completed_at: Optional[float] = None
    deadline: float = 0.0  # Response deadline (1 month from submission)
    details: dict[str, Any] = field(default_factory=dict)
    response: Optional[str] = None
    rejection_reason: Optional[str] = None
    handler: Optional[str] = None  # Who is handling the request

    def __post_init__(self) -> None:
        if self.deadline == 0.0:
            self.deadline = self.submitted_at + (30 * 24 * 60 * 60)  # 1 month


@dataclass
class DpiaAssessment:
    """Data Protection Impact Assessment (Article 35)."""

    dpia_id: str
    project_name: str
    status: DpiaStatus
    description: str = ""
    data_categories: list[DataCategory] = field(default_factory=list)
    processing_purposes: list[str] = field(default_factory=list)
    necessity_proportionality: str = ""
    risk_assessment: dict[str, str] = field(default_factory=dict)
    mitigation_measures: list[str] = field(default_factory=list)
    data_protection_officer_consulted: bool = False
    created_at: float = field(default_factory=lambda: time.time())
    completed_at: Optional[float] = None
    assessor: Optional[str] = None
    prior_consultation_required: bool = False


@dataclass
class DataBreachRecord:
    """Personal data breach record (Article 33/34)."""

    breach_id: str
    detected_at: float
    status: BreachStatus
    description: str = ""
    nature_of_breach: str = ""  # What data was involved
    categories_of_data: list[DataCategory] = field(default_factory=list)
    approx_data_subjects: int = 0  # Approximate number affected
    likely_consequences: str = ""
    measures_taken: list[str] = field(default_factory=list)
    controller_notified: bool = False
    controller_notified_at: Optional[float] = None
    supervisory_authority_notified: bool = False
    supervisory_authority_notified_at: Optional[float] = None
    subjects_notified: bool = False
    subjects_notified_at: Optional[float] = None
    notification_deadline: float = 0.0  # 72 hours from detection
    risk_level: str = "medium"  # low, medium, high

    def __post_init__(self) -> None:
        if self.notification_deadline == 0.0:
            self.notification_deadline = self.detected_at + (72 * 60 * 60)  # 72 hours


@dataclass
class PrivacyByDesignChecklist:
    """Privacy by design checklist (Article 25)."""

    checklist_id: str
    project_name: str
    items: dict[str, bool] = field(default_factory=dict)
    completed: bool = False
    last_reviewed_at: Optional[float] = None
    reviewer: Optional[str] = None


# ---------------------------------------------------------------------------
# Data Subject Rights Manager
# ---------------------------------------------------------------------------


class DataSubjectRightsManager:
    """Manages data subject rights requests under GDPR.

    Handles the full lifecycle of access, rectification, erasure,
    portability, and other data subject rights requests.
    """

    def __init__(self) -> None:
        self._requests: dict[str, DataSubjectRequest] = {}
        self._user_data: dict[str, dict[str, Any]] = {}  # user_id -> data catalog

    # ---- Request management ----

    def submit_request(
        self,
        user_id: str,
        right: DataSubjectRight,
        details: Optional[dict[str, Any]] = None,
    ) -> DataSubjectRequest:
        """Submit a new data subject rights request.

        Args:
            user_id: The data subject's ID.
            right: The right being exercised.
            details: Additional request details.

        Returns:
            The created DataSubjectRequest.
        """
        request = DataSubjectRequest(
            request_id=str(uuid.uuid4()),
            user_id=user_id,
            right=right,
            details=details or {},
        )
        self._requests[request.request_id] = request
        return request

    def get_request(self, request_id: str) -> Optional[DataSubjectRequest]:
        """Get a request by ID."""
        return self._requests.get(request_id)

    def list_requests(
        self,
        user_id: Optional[str] = None,
        status: Optional[str] = None,
        right: Optional[DataSubjectRight] = None,
    ) -> list[DataSubjectRequest]:
        """List data subject requests with optional filters."""
        requests = list(self._requests.values())
        if user_id:
            requests = [r for r in requests if r.user_id == user_id]
        if status:
            requests = [r for r in requests if r.status == status]
        if right:
            requests = [r for r in requests if r.right == right]
        return requests

    def process_request(self, request_id: str, handler: str) -> bool:
        """Mark a request as being processed."""
        request = self._requests.get(request_id)
        if request is None:
            return False
        request.status = "processing"
        request.handler = handler
        return True

    def complete_request(
        self,
        request_id: str,
        response: str,
        data: Optional[Any] = None,
    ) -> bool:
        """Complete a data subject request.

        Args:
            request_id: The request ID.
            response: Human-readable response text.
            data: Exported data (for access/portability requests).

        Returns:
            True if the request was found and completed.
        """
        request = self._requests.get(request_id)
        if request is None:
            return False
        request.status = "completed"
        request.completed_at = time.time()
        request.response = response
        if data is not None:
            request.details["exported_data"] = data
        return True

    def reject_request(
        self, request_id: str, reason: str
    ) -> bool:
        """Reject a request with a reason.

        Under GDPR, requests can be rejected if they are manifestly
        unfounded or excessive.
        """
        request = self._requests.get(request_id)
        if request is None:
            return False
        request.status = "rejected"
        request.completed_at = time.time()
        request.rejection_reason = reason
        return True

    # ---- Right-specific operations ----

    def get_data_for_access(self, user_id: str) -> dict[str, Any]:
        """Compile all personal data for a data subject access request.

        Returns a structured dictionary of all data categories held
        about the user.
        """
        user_data = self._user_data.get(user_id, {})
        return {
            "user_id": user_id,
            "data_categories": list(user_data.keys()),
            "data": user_data,
            "generated_at": time.time(),
            "format": "json",
        }

    def export_for_portability(self, user_id: str, format: str = "json") -> dict[str, Any]:
        """Export data in a machine-readable format for portability.

        Includes data the user has provided and data derived from
        their activities.
        """
        user_data = self._user_data.get(user_id, {})
        return {
            "user_id": user_id,
            "export_format": format,
            "exported_at": time.time(),
            "data": user_data,
            "portable_fields": list(user_data.keys()),
        }

    def erase_data(
        self, user_id: str, scope: Optional[list[str]] = None
    ) -> dict[str, int]:
        """Erase personal data (right to be forgotten).

        Args:
            user_id: The user whose data to erase.
            scope: Specific data categories to erase. None = all.

        Returns:
            Dictionary with counts of erased items per category.
        """
        erased: dict[str, int] = {}
        if user_id not in self._user_data:
            return erased

        user_data = self._user_data[user_id]
        if scope:
            for category in scope:
                if category in user_data:
                    erased[category] = len(user_data[category]) if hasattr(user_data[category], '__len__') else 1
                    del user_data[category]
        else:
            for category, data in user_data.items():
                erased[category] = len(data) if hasattr(data, '__len__') else 1
            self._user_data[user_id] = {}

        return erased

    def get_overdue_requests(self) -> list[DataSubjectRequest]:
        """Get requests that have passed their deadline."""
        now = time.time()
        return [
            r for r in self._requests.values()
            if r.status in ("pending", "processing") and now > r.deadline
        ]

    def register_user_data(self, user_id: str, data: dict[str, Any]) -> None:
        """Register user data for access/erasure operations."""
        if user_id not in self._user_data:
            self._user_data[user_id] = {}
        self._user_data[user_id].update(data)


# ---------------------------------------------------------------------------
# Consent Management
# ---------------------------------------------------------------------------


class ConsentManager:
    """Manages user consent records for GDPR compliance.

    Tracks consent grants, withdrawals, and provides audit trails
    for consent transactions. Supports consent versioning and
    granular scoping.
    """

    def __init__(self) -> None:
        self._consents: dict[str, list[ConsentRecord]] = {}  # user_id -> consent history

    def grant_consent(
        self,
        user_id: str,
        purpose: str,
        scope: Optional[list[str]] = None,
        method: str = "explicit",
        expires_in: Optional[int] = None,
        ip_address: Optional[str] = None,
        user_agent: Optional[str] = None,
        version: str = "1.0",
    ) -> ConsentRecord:
        """Record a consent grant.

        Args:
            user_id: The user giving consent.
            purpose: What the consent is for (e.g. "marketing", "analytics").
            scope: Specific data types or features covered.
            method: How consent was obtained.
            expires_in: Optional expiration in seconds.
            ip_address: IP address at time of consent.
            user_agent: User agent at time of consent.
            version: Consent form version.

        Returns:
            The ConsentRecord.
        """
        now = time.time()
        record = ConsentRecord(
            consent_id=str(uuid.uuid4()),
            user_id=user_id,
            purpose=purpose,
            status=ConsentStatus.GRANTED,
            granted_at=now,
            expires_at=now + expires_in if expires_in else None,
            scope=scope or [],
            method=method,
            ip_address=ip_address,
            user_agent=user_agent,
            version=version,
        )

        if user_id not in self._consents:
            self._consents[user_id] = []
        self._consents[user_id].append(record)
        return record

    def withdraw_consent(
        self, user_id: str, purpose: str, reason: Optional[str] = None
    ) -> Optional[ConsentRecord]:
        """Withdraw a previously granted consent.

        Returns the updated consent record, or None if not found.
        """
        if user_id not in self._consents:
            return None

        now = time.time()
        for record in reversed(self._consents[user_id]):
            if record.purpose == purpose and record.status == ConsentStatus.GRANTED:
                record.status = ConsentStatus.WITHDRAWN
                record.withdrawn_at = now
                if reason:
                    record.metadata["withdrawal_reason"] = reason
                return record

        return None

    def has_consent(self, user_id: str, purpose: str, scope: Optional[str] = None) -> bool:
        """Check if a user has valid consent for a purpose.

        Args:
            user_id: The user to check.
            purpose: The consent purpose.
            scope: Optional specific scope within the purpose.

        Returns:
            True if valid consent exists.
        """
        if user_id not in self._consents:
            return False

        now = time.time()
        for record in reversed(self._consents[user_id]):
            if record.purpose != purpose:
                continue
            if record.status != ConsentStatus.GRANTED:
                continue
            if record.expires_at and now > record.expires_at:
                continue
            if scope and record.scope and scope not in record.scope:
                continue
            return True

        return False

    def get_consent_history(
        self, user_id: str, purpose: Optional[str] = None
    ) -> list[ConsentRecord]:
        """Get consent history for a user."""
        records = self._consents.get(user_id, [])
        if purpose:
            records = [r for r in records if r.purpose == purpose]
        return list(records)

    def get_active_consents(self, user_id: str) -> list[ConsentRecord]:
        """Get all currently active consents for a user."""
        now = time.time()
        return [
            r for r in self._consents.get(user_id, [])
            if r.status == ConsentStatus.GRANTED
            and (r.expires_at is None or now < r.expires_at)
        ]

    def get_expired_consents(self) -> list[ConsentRecord]:
        """Get all expired consents across all users."""
        now = time.time()
        expired = []
        for records in self._consents.values():
            for r in records:
                if r.expires_at and now > r.expires_at and r.status == ConsentStatus.GRANTED:
                    expired.append(r)
        return expired


# ---------------------------------------------------------------------------
# Data Processing Records (Article 30)
# ---------------------------------------------------------------------------


class ProcessingRecordsManager:
    """Maintains records of processing activities (Article 30 GDPR)."""

    def __init__(self) -> None:
        self._records: dict[str, DataProcessingRecord] = {}

    def add_record(
        self,
        controller: str,
        activity_type: ProcessingActivityType,
        data_categories: list[DataCategory],
        data_subjects: list[str],
        purposes: list[str],
        processor: Optional[str] = None,
        legal_basis: str = "",
        description: str = "",
    ) -> DataProcessingRecord:
        """Add a new processing activity record."""
        record = DataProcessingRecord(
            record_id=str(uuid.uuid4()),
            controller=controller,
            processor=processor,
            activity_type=activity_type,
            data_categories=data_categories,
            data_subjects=data_subjects,
            purposes=purposes,
            legal_basis=legal_basis,
            description=description,
        )
        self._records[record.record_id] = record
        return record

    def get_record(self, record_id: str) -> Optional[DataProcessingRecord]:
        """Get a processing record by ID."""
        return self._records.get(record_id)

    def list_records(
        self,
        controller: Optional[str] = None,
        activity_type: Optional[ProcessingActivityType] = None,
        data_category: Optional[DataCategory] = None,
    ) -> list[DataProcessingRecord]:
        """List processing records with optional filters."""
        records = list(self._records.values())
        if controller:
            records = [r for r in records if r.controller == controller]
        if activity_type:
            records = [r for r in records if r.activity_type == activity_type]
        if data_category:
            records = [r for r in records if data_category in r.data_categories]
        return records

    def update_record(self, record_id: str, **kwargs: Any) -> Optional[DataProcessingRecord]:
        """Update a processing record."""
        record = self._records.get(record_id)
        if record is None:
            return None
        for key, value in kwargs.items():
            if hasattr(record, key):
                setattr(record, key, value)
        return record

    def end_processing(self, record_id: str) -> bool:
        """Mark a processing activity as ended."""
        record = self._records.get(record_id)
        if record is None:
            return False
        record.ended_at = time.time()
        return True

    def generate_article30_report(self) -> dict[str, Any]:
        """Generate an Article 30 compliance report.

        Returns a structured report suitable for regulatory review.
        """
        active = [r for r in self._records.values() if r.ended_at is None]
        by_type: dict[str, int] = {}
        by_category: dict[str, int] = {}

        for r in active:
            type_name = r.activity_type.value
            by_type[type_name] = by_type.get(type_name, 0) + 1
            for cat in r.data_categories:
                cat_name = cat.value
                by_category[cat_name] = by_category.get(cat_name, 0) + 1

        return {
            "total_records": len(self._records),
            "active_records": len(active),
            "by_activity_type": by_type,
            "by_data_category": by_category,
            "records": [r.__dict__ for r in active],
            "generated_at": time.time(),
        }


# ---------------------------------------------------------------------------
# DPIA Management
# ---------------------------------------------------------------------------


class DpiaManager:
    """Manages Data Protection Impact Assessments (Article 35)."""

    def __init__(self) -> None:
        self._dpias: dict[str, DpiaAssessment] = {}

    def create_dpia(
        self,
        project_name: str,
        description: str = "",
        data_categories: Optional[list[DataCategory]] = None,
        processing_purposes: Optional[list[str]] = None,
    ) -> DpiaAssessment:
        """Create a new DPIA."""
        dpia = DpiaAssessment(
            dpia_id=str(uuid.uuid4()),
            project_name=project_name,
            status=DpiaStatus.DRAFT,
            description=description,
            data_categories=data_categories or [],
            processing_purposes=processing_purposes or [],
        )
        self._dpias[dpia.dpia_id] = dpia
        return dpia

    def get_dpia(self, dpia_id: str) -> Optional[DpiaAssessment]:
        """Get a DPIA by ID."""
        return self._dpias.get(dpia_id)

    def update_status(self, dpia_id: str, status: DpiaStatus) -> bool:
        """Update DPIA status."""
        dpia = self._dpias.get(dpia_id)
        if dpia is None:
            return False
        dpia.status = status
        if status == DpiaStatus.COMPLETED:
            dpia.completed_at = time.time()
        return True

    def assess_risk(
        self,
        dpia_id: str,
        likelihood: str,
        severity: str,
        overall: str,
        notes: str = "",
    ) -> bool:
        """Record risk assessment results."""
        dpia = self._dpias.get(dpia_id)
        if dpia is None:
            return False
        dpia.risk_assessment = {
            "likelihood": likelihood,
            "severity": severity,
            "overall": overall,
            "notes": notes,
        }
        return True

    def add_mitigation(self, dpia_id: str, measure: str) -> bool:
        """Add a risk mitigation measure."""
        dpia = self._dpias.get(dpia_id)
        if dpia is None:
            return False
        dpia.mitigation_measures.append(measure)
        return True

    def list_dpias(
        self, status: Optional[DpiaStatus] = None
    ) -> list[DpiaAssessment]:
        """List DPIAs with optional status filter."""
        dpias = list(self._dpias.values())
        if status:
            dpias = [d for d in dpias if d.status == status]
        return dpias

    def is_dpia_required(
        self,
        data_categories: list[DataCategory],
        processing_types: list[ProcessingActivityType],
        at_scale: bool = False,
        new_technology: bool = False,
        systematic_monitoring: bool = False,
        vulnerable_subjects: bool = False,
    ) -> bool:
        """Determine if a DPIA is required (Article 35(1)).

        A DPIA is required when processing is likely to result in
        high risk to the rights and freedoms of natural persons.
        """
        high_risk_indicators = 0

        # Special category data
        special_categories = {
            DataCategory.HEALTH,
            DataCategory.BIOMETRIC,
            DataCategory.SENSITIVE,
        }
        if any(c in special_categories for c in data_categories):
            high_risk_indicators += 2

        # Systematic monitoring
        if systematic_monitoring:
            high_risk_indicators += 2

        # Processing at large scale
        if at_scale:
            high_risk_indicators += 1

        # New technology
        if new_technology:
            high_risk_indicators += 1

        # Vulnerable data subjects
        if vulnerable_subjects:
            high_risk_indicators += 1

        # Profiling or automated decision-making
        if ProcessingActivityType.PROFILING in processing_types:
            high_risk_indicators += 2

        # Data matching/combining datasets
        if len(data_categories) >= 3:
            high_risk_indicators += 1

        return high_risk_indicators >= 3


# ---------------------------------------------------------------------------
# Data Breach Management
# ---------------------------------------------------------------------------


class DataBreachManager:
    """Manages personal data breach records and notifications (Articles 33-34)."""

    def __init__(self) -> None:
        self._breaches: dict[str, DataBreachRecord] = {}

    def record_breach(
        self,
        description: str,
        nature_of_breach: str = "",
        categories_of_data: Optional[list[DataCategory]] = None,
        approx_data_subjects: int = 0,
        detected_at: Optional[float] = None,
    ) -> DataBreachRecord:
        """Record a detected data breach."""
        breach = DataBreachRecord(
            breach_id=str(uuid.uuid4()),
            detected_at=detected_at or time.time(),
            status=BreachStatus.DETECTED,
            description=description,
            nature_of_breach=nature_of_breach,
            categories_of_data=categories_of_data or [],
            approx_data_subjects=approx_data_subjects,
        )
        self._breaches[breach.breach_id] = breach
        return breach

    def get_breach(self, breach_id: str) -> Optional[DataBreachRecord]:
        """Get a breach record by ID."""
        return self._breaches.get(breach_id)

    def assess_breach(
        self,
        breach_id: str,
        risk_level: str,
        likely_consequences: str,
        measures: list[str],
    ) -> bool:
        """Record breach assessment results."""
        breach = self._breaches.get(breach_id)
        if breach is None:
            return False
        breach.status = BreachStatus.ASSESSING
        breach.risk_level = risk_level
        breach.likely_consequences = likely_consequences
        breach.measures_taken.extend(measures)
        return True

    def notify_supervisory_authority(self, breach_id: str) -> bool:
        """Record notification to supervisory authority.

        Per Article 33, notification must be made within 72 hours
        of becoming aware of the breach, unless unlikely to result
        in risk to rights and freedoms.
        """
        breach = self._breaches.get(breach_id)
        if breach is None:
            return False
        breach.supervisory_authority_notified = True
        breach.supervisory_authority_notified_at = time.time()
        if breach.status == BreachStatus.ASSESSING:
            breach.status = BreachStatus.NOTIFIED
        return True

    def notify_data_subjects(self, breach_id: str) -> bool:
        """Record notification to affected data subjects.

        Per Article 34, required when the breach is likely to result
        in high risk to the rights and freedoms of natural persons.
        """
        breach = self._breaches.get(breach_id)
        if breach is None:
            return False
        breach.subjects_notified = True
        breach.subjects_notified_at = time.time()
        return True

    def resolve_breach(self, breach_id: str) -> bool:
        """Mark a breach as resolved."""
        breach = self._breaches.get(breach_id)
        if breach is None:
            return False
        breach.status = BreachStatus.RESOLVED
        return True

    def close_breach(self, breach_id: str) -> bool:
        """Close a breach case."""
        breach = self._breaches.get(breach_id)
        if breach is None:
            return False
        breach.status = BreachStatus.CLOSED
        return True

    def get_pending_notifications(self) -> list[DataBreachRecord]:
        """Get breaches that may require supervisory authority notification."""
        now = time.time()
        return [
            b for b in self._breaches.values()
            if not b.supervisory_authority_notified
            and b.status in (BreachStatus.DETECTED, BreachStatus.ASSESSING)
            and b.risk_level in ("medium", "high")
        ]

    def is_within_72h_window(self, breach_id: str) -> bool:
        """Check if a breach is still within the 72-hour notification window."""
        breach = self._breaches.get(breach_id)
        if breach is None:
            return False
        return time.time() < breach.notification_deadline


# ---------------------------------------------------------------------------
# Privacy by Design
# ---------------------------------------------------------------------------


class PrivacyByDesignChecker:
    """Privacy by design checklist management (Article 25)."""

    _REQUIRED_ITEMS: dict[str, str] = {
        "data_minimization": "Collect only the minimum data necessary",
        "purpose_limitation": "Specify purposes and limit processing to them",
        "transparency": "Inform data subjects about processing",
        "individual_participation": "Allow data subjects to access and control their data",
        "security_by_default": "Implement security measures by default",
        "anonymization_pseudonymization": "Use anonymization or pseudonymization where possible",
        "privacy_impact_assessment": "Conduct privacy impact assessments",
        "data_lifecycle_management": "Implement data retention and deletion policies",
        "access_control": "Implement role-based access controls",
        "encryption": "Encrypt personal data at rest and in transit",
        "audit_logging": "Log access to personal data",
        "vendor_management": "Assess and monitor third-party data processors",
        "breach_response": "Have data breach response procedures",
        "privacy_training": "Provide privacy training for staff",
        "privacy_reviews": "Conduct periodic privacy reviews",
    }

    def create_checklist(self, project_name: str) -> PrivacyByDesignChecklist:
        """Create a new privacy by design checklist for a project."""
        items = {key: False for key in self._REQUIRED_ITEMS}
        checklist = PrivacyByDesignChecklist(
            checklist_id=str(uuid.uuid4()),
            project_name=project_name,
            items=items,
        )
        return checklist

    def mark_item(
        self, checklist: PrivacyByDesignChecklist, item_key: str, completed: bool
    ) -> bool:
        """Mark a checklist item as completed or not."""
        if item_key not in checklist.items:
            return False
        checklist.items[item_key] = completed
        checklist.completed = all(checklist.items.values())
        checklist.last_reviewed_at = time.time()
        return True

    def get_completion_percentage(self, checklist: PrivacyByDesignChecklist) -> float:
        """Calculate checklist completion percentage."""
        if not checklist.items:
            return 0.0
        completed = sum(1 for v in checklist.items.values() if v)
        return (completed / len(checklist.items)) * 100

    def get_item_description(self, item_key: str) -> Optional[str]:
        """Get the description for a checklist item."""
        return self._REQUIRED_ITEMS.get(item_key)

    def list_all_items(self) -> dict[str, str]:
        """List all required privacy by design items."""
        return dict(self._REQUIRED_ITEMS)

    def get_incomplete_items(self, checklist: PrivacyByDesignChecklist) -> list[tuple[str, str]]:
        """Get a list of incomplete checklist items with descriptions."""
        return [
            (key, desc)
            for key, desc in self._REQUIRED_ITEMS.items()
            if not checklist.items.get(key, False)
        ]
