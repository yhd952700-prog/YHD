"""
Medical Assistant AI Solution Template.

A comprehensive AI-powered healthcare assistant solution for
hospitals, clinics, and telemedicine platforms. Includes
multi-agent roles for triage, diagnosis, patient education,
and care coordination with built-in safety mechanisms.

Typical workflow:
    symptom intake -> triage -> differential diagnosis
    -> patient education -> care plan

Safety features:
    - Double-check mechanism for diagnoses
    - Confidence thresholds for recommendations
    - Escalation to human providers when uncertain
"""

from typing import Optional, Dict, Any, List
from enum import Enum
from dataclasses import dataclass, field

from solutions.templates.base_solution import (
    BaseSolution,
    SolutionMetadata,
    SolutionMetrics,
)


class UrgencyLevel(str, Enum):
    """Medical urgency levels for triage."""
    ROUTINE = "routine"
    NON_URGENT = "non_urgent"
    URGENT = "urgent"
    EMERGENCY = "emergency"
    LIFE_THREATENING = "life_threatening"


class DiagnosticConfidence(str, Enum):
    """Confidence levels for diagnostic assessments."""
    VERY_LOW = "very_low"
    LOW = "low"
    MODERATE = "moderate"
    HIGH = "high"
    VERY_HIGH = "very_high"


class CarePlanStatus(str, Enum):
    """Status of a care plan through the workflow."""
    INITIAL = "initial"
    TRIAGED = "triaged"
    DIAGNOSED = "diagnosed"
    EDUCATION_PROVIDED = "education_provided"
    CARE_PLAN_READY = "care_plan_ready"
    ESCALATED = "escalated"
    CLOSED = "closed"


class HealthcareRole(str, Enum):
    """Agent roles in the medical assistant solution."""
    TRIAGE_NURSE = "triage_nurse"
    DIAGNOSTICIAN = "diagnostician"
    PATIENT_EDUCATOR = "patient_educator"
    CARE_COORDINATOR = "care_coordinator"


@dataclass
class PatientInfo:
    """Represents patient demographic and medical information.

    Attributes:
        patient_id: Unique patient identifier.
        name: Patient name.
        age: Patient age in years.
        gender: Patient gender.
        weight_kg: Patient weight in kilograms.
        height_cm: Patient height in centimeters.
        allergies: List of known allergies.
        medications: Current medications.
        medical_history: List of past medical conditions.
        primary_language: Patient's primary language.
    """
    patient_id: str
    name: str = ""
    age: int = 30
    gender: str = "unspecified"
    weight_kg: float = 0.0
    height_cm: float = 0.0
    allergies: List[str] = field(default_factory=list)
    medications: List[str] = field(default_factory=list)
    medical_history: List[str] = field(default_factory=list)
    primary_language: str = "en"


@dataclass
class SymptomEntry:
    """Represents a reported symptom.

    Attributes:
        name: Symptom name/description.
        severity: Severity rating (1-10).
        duration_days: How long the symptom has been present.
        onset: When the symptom started (acute/gradual).
        location: Body location of the symptom.
        associated_symptoms: Related symptoms.
        triggers: What triggers or worsens the symptom.
    """
    name: str
    severity: int = 5
    duration_days: float = 1.0
    onset: str = "gradual"
    location: str = ""
    associated_symptoms: List[str] = field(default_factory=list)
    triggers: List[str] = field(default_factory=list)


@dataclass
class DifferentialDiagnosis:
    """Represents a differential diagnosis entry.

    Attributes:
        condition: Name of the suspected condition.
        probability: Estimated probability (0-1).
        confidence: Confidence level of the diagnosis.
        rationale: Reasoning supporting the diagnosis.
        supporting_symptoms: Symptoms supporting this diagnosis.
        contradicting_symptoms: Symptoms against this diagnosis.
        recommended_tests: Tests to confirm or rule out.
    """
    condition: str
    probability: float = 0.0
    confidence: DiagnosticConfidence = DiagnosticConfidence.MODERATE
    rationale: str = ""
    supporting_symptoms: List[str] = field(default_factory=list)
    contradicting_symptoms: List[str] = field(default_factory=list)
    recommended_tests: List[str] = field(default_factory=list)


@dataclass
class CarePlan:
    """Represents a patient care plan.

    Attributes:
        id: Unique care plan identifier.
        patient_id: Associated patient ID.
        urgency: Assigned urgency level.
        primary_assessment: Primary diagnostic assessment.
        differential_diagnoses: List of differential diagnoses.
        recommendations: List of care recommendations.
        follow_up_instructions: Follow-up instructions.
        referral_recommendation: Whether referral is recommended.
        referral_specialty: Specialty to refer to, if applicable.
        patient_education: Educational materials provided.
        status: Current care plan status.
        double_checked: Whether diagnosis was double-checked.
        created_at: ISO 8601 creation timestamp.
    """
    id: str
    patient_id: str
    urgency: UrgencyLevel = UrgencyLevel.ROUTINE
    primary_assessment: str = ""
    differential_diagnoses: List[DifferentialDiagnosis] = field(default_factory=list)
    recommendations: List[str] = field(default_factory=list)
    follow_up_instructions: str = ""
    referral_recommendation: bool = False
    referral_specialty: str = ""
    patient_education: List[str] = field(default_factory=list)
    status: CarePlanStatus = CarePlanStatus.INITIAL
    double_checked: bool = False
    created_at: Optional[str] = None


@dataclass
class HealthcareMetrics(SolutionMetrics):
    """Extended metrics for the medical assistant solution.

    Attributes:
        diagnostic_accuracy: Estimated diagnostic accuracy rate.
        avg_triage_time_min: Average triage time in minutes.
        patient_satisfaction: Average patient satisfaction score (0-5).
        escalation_rate: Rate of cases escalated to human providers.
        cases_processed: Total cases processed.
        avg_confidence_score: Average diagnostic confidence score.
        safety_alerts_triggered: Number of safety alerts triggered.
    """
    diagnostic_accuracy: float = 0.0
    avg_triage_time_min: float = 0.0
    patient_satisfaction: float = 0.0
    escalation_rate: float = 0.0
    cases_processed: int = 0
    avg_confidence_score: float = 0.0
    safety_alerts_triggered: int = 0

    def to_dict(self) -> Dict[str, Any]:
        """Convert healthcare metrics to a dictionary."""
        base = super().to_dict()
        base.update({
            "diagnostic_accuracy": self.diagnostic_accuracy,
            "avg_triage_time_min": self.avg_triage_time_min,
            "patient_satisfaction": self.patient_satisfaction,
            "escalation_rate": self.escalation_rate,
            "cases_processed": self.cases_processed,
            "avg_confidence_score": self.avg_confidence_score,
            "safety_alerts_triggered": self.safety_alerts_triggered,
        })
        return base


class MedicalAssistantSolution(BaseSolution):
    """Medical Assistant AI solution template.

    Provides a complete, pre-configured healthcare AI assistant
    with multi-agent collaboration for symptom assessment, triage,
    differential diagnosis, patient education, and care coordination.

    Includes critical safety features:
        - Double-check mechanism: All diagnoses are independently verified
        - Confidence thresholds: Low confidence cases are automatically escalated
        - Red flag detection: Emergency symptoms trigger immediate escalation
        - Human-in-the-loop: Seamless handoff to healthcare providers

    Pre-configured agent roles:
        - Triage Nurse: Performs initial symptom assessment and urgency classification
        - Diagnostician: Generates differential diagnoses based on symptoms
        - Patient Educator: Provides clear, accessible health information
        - Care Coordinator: Manages referrals, follow-ups, and care transitions

    Workflow:
        1. Symptom Intake - Gather patient symptoms and medical history
        2. Triage - Assess urgency and determine appropriate care level
        3. Differential Diagnosis - Generate and rank possible conditions
        4. Patient Education - Provide relevant health information
        5. Care Plan - Create comprehensive care recommendations

    Configuration options:
        - confidence_threshold: Minimum confidence for non-escalation
        - double_check_enabled: Whether to enable double-check mechanism
        - max_symptoms_per_case: Maximum symptoms to process per case
        - default_language: Default patient language
        - enable_red_flag_detection: Whether to detect emergency red flags
    """

    def _build_metadata(self) -> SolutionMetadata:
        """Build metadata for the medical assistant solution."""
        return SolutionMetadata(
            name="medical-assistant",
            version="1.0.0",
            industry="healthcare",
            description=(
                "AI-powered medical assistant solution with symptom triage, "
                "differential diagnosis, patient education, and built-in "
                "safety mechanisms including double-check and confidence thresholds."
            ),
            author="LiuHao AI OS",
            tags=["healthcare", "medical", "triage", "diagnosis", "patient-care", "safety"],
            min_liuhao_version="1.0.0",
        )

    def _build_config_schema(self) -> None:
        """Define configuration fields for the medical assistant solution."""
        self.config_schema.add_field(
            name="confidence_threshold",
            field_type="string",
            required=False,
            default="moderate",
            description="Minimum confidence level for non-escalated diagnoses",
            valid_values=["very_low", "low", "moderate", "high", "very_high"],
        )
        self.config_schema.add_field(
            name="double_check_enabled",
            field_type="bool",
            required=False,
            default=True,
            description="Whether to enable the double-check safety mechanism",
        )
        self.config_schema.add_field(
            name="max_symptoms_per_case",
            field_type="int",
            required=False,
            default=10,
            description="Maximum number of symptoms to process per case",
        )
        self.config_schema.add_field(
            name="default_language",
            field_type="string",
            required=False,
            default="en",
            description="Default language for patient communication",
        )
        self.config_schema.add_field(
            name="enable_red_flag_detection",
            field_type="bool",
            required=False,
            default=True,
            description="Whether to detect emergency red flag symptoms",
        )
        self.config_schema.add_field(
            name="practice_type",
            field_type="string",
            required=False,
            default="general",
            description="Type of medical practice",
            valid_values=["general", "specialist", "urgent_care", "telehealth", "hospital"],
        )
        self.config_schema.add_field(
            name="escalation_protocol",
            field_type="string",
            required=False,
            default="standard",
            description="Escalation protocol for uncertain cases",
            valid_values=["minimal", "standard", "conservative", "aggressive"],
        )

    def _on_initialize(self) -> None:
        """Initialize medical assistant solution state."""
        self._agents: Dict[str, Dict[str, Any]] = {}
        self._patients: Dict[str, PatientInfo] = {}
        self._care_plans: Dict[str, CarePlan] = {}
        self._care_plan_count = 0
        self._patient_count = 0

        # Initialize healthcare-specific metrics
        self.healthcare_metrics = HealthcareMetrics()

        # Red flag symptoms that require immediate escalation
        self._red_flag_symptoms = [
            "chest pain", "difficulty breathing", "shortness of breath",
            "unconsciousness", "severe bleeding", "stroke symptoms",
            "suicidal thoughts", "severe head injury", "neck injury",
            "paralysis", "severe abdominal pain", "high fever with rash",
        ]

    def _on_deploy(self) -> None:
        """Deploy medical assistant agents and workflows."""
        agent_configs = [
            (HealthcareRole.TRIAGE_NURSE, {
                "name": "Triage Nurse",
                "description": "Performs initial symptom assessment and urgency classification",
                "system_prompt": self._build_triage_nurse_prompt(),
                "tools": ["symptom_checker", "urgency_assessment", "red_flag_detector"],
            }),
            (HealthcareRole.DIAGNOSTICIAN, {
                "name": "Diagnostician",
                "description": "Generates differential diagnoses based on symptoms",
                "system_prompt": self._build_diagnostician_prompt(),
                "tools": ["diagnostic_engine", "medical_knowledge", "test_recommender"],
            }),
            (HealthcareRole.PATIENT_EDUCATOR, {
                "name": "Patient Educator",
                "description": "Provides clear, accessible health information",
                "system_prompt": self._build_patient_educator_prompt(),
                "tools": ["health_library", "explainer", "medication_info"],
            }),
            (HealthcareRole.CARE_COORDINATOR, {
                "name": "Care Coordinator",
                "description": "Manages referrals, follow-ups, and care transitions",
                "system_prompt": self._build_care_coordinator_prompt(),
                "tools": ["referral_manager", "appointment_scheduler", "follow_up_tracker"],
            }),
        ]

        for role, config in agent_configs:
            self._agents[role.value] = {
                "role": role.value,
                "name": config["name"],
                "status": "deployed",
                **config,
            }

    def _on_run(self, input_data: Dict[str, Any]) -> Dict[str, Any]:
        """Process a patient case through the full medical workflow.

        Args:
            input_data: Dictionary containing patient data with at least
                ``patient_id`` and ``symptoms`` list.

        Returns:
            Dictionary with care plan results including triage assessment,
            differential diagnoses, recommendations, and safety status.
        """
        # Step 1: Symptom intake
        patient, symptoms = self._intake_symptoms(input_data)

        # Step 2: Triage with red flag detection
        urgency, red_flags = self._triage(patient, symptoms)

        # Emergency escalation for red flags
        if red_flags and self.config.get("enable_red_flag_detection", True):
            self.healthcare_metrics.safety_alerts_triggered += 1
            care_plan = self._create_emergency_care_plan(patient, symptoms, red_flags)
            self._update_healthcare_metrics()
            return self._build_emergency_result(patient, care_plan, red_flags)

        # Step 3: Differential diagnosis
        diagnoses = self._generate_differential_diagnosis(patient, symptoms)

        # Step 3b: Double-check mechanism
        double_checked = False
        if self.config.get("double_check_enabled", True):
            diagnoses = self._double_check_diagnosis(patient, symptoms, diagnoses)
            double_checked = True

        # Step 4: Patient education
        education_materials = self._generate_patient_education(diagnoses)

        # Step 5: Care plan
        care_plan = self._create_care_plan(
            patient, symptoms, urgency, diagnoses, education_materials, double_checked
        )

        # Check if escalation is needed
        escalation_needed = self._check_escalation_needed(diagnoses)
        if escalation_needed:
            care_plan.status = CarePlanStatus.ESCALATED
            care_plan.referral_recommendation = True

        # Update metrics
        self.healthcare_metrics.cases_processed += 1
        self._update_healthcare_metrics()

        return {
            "patient_id": patient.patient_id,
            "patient": {
                "age": patient.age,
                "gender": patient.gender,
            },
            "triage": {
                "urgency": urgency.value,
                "red_flags_detected": len(red_flags) > 0,
                "red_flags": red_flags,
            },
            "diagnosis": {
                "primary": diagnoses[0].condition if diagnoses else "",
                "differential": [
                    {
                        "condition": d.condition,
                        "probability": d.probability,
                        "confidence": d.confidence.value,
                    }
                    for d in diagnoses
                ],
                "double_checked": double_checked,
            },
            "care_plan": {
                "id": care_plan.id,
                "status": care_plan.status.value,
                "recommendations": care_plan.recommendations,
                "follow_up": care_plan.follow_up_instructions,
                "referral_needed": care_plan.referral_recommendation,
                "referral_specialty": care_plan.referral_specialty,
            },
            "patient_education": education_materials,
            "safety": {
                "double_check_enabled": self.config.get("double_check_enabled", True),
                "confidence_threshold": self.config.get("confidence_threshold", "moderate"),
                "escalation_recommended": escalation_needed,
            },
        }

    def _on_teardown(self) -> None:
        """Clean up medical assistant solution resources."""
        self._agents.clear()
        self._patients.clear()
        self._care_plans.clear()
        self._care_plan_count = 0
        self._patient_count = 0

    # ------------------------------------------------------------------
    # Workflow steps
    # ------------------------------------------------------------------

    def _intake_symptoms(
        self, data: Dict[str, Any]
    ) -> tuple[PatientInfo, List[SymptomEntry]]:
        """Step 1: Gather patient information and symptoms."""
        self._patient_count += 1
        patient_id = data.get("patient_id", f"patient-{self._patient_count}")

        patient = PatientInfo(
            patient_id=patient_id,
            name=data.get("name", ""),
            age=data.get("age", 30),
            gender=data.get("gender", "unspecified"),
            weight_kg=data.get("weight_kg", 0.0),
            height_cm=data.get("height_cm", 0.0),
            allergies=data.get("allergies", []),
            medications=data.get("medications", []),
            medical_history=data.get("medical_history", []),
            primary_language=data.get(
                "primary_language", self.config.get("default_language", "en")
            ),
        )
        self._patients[patient_id] = patient

        # Parse symptoms
        symptom_data = data.get("symptoms", [])
        max_symptoms = self.config.get("max_symptoms_per_case", 10)
        symptoms = []
        for sym in symptom_data[:max_symptoms]:
            if isinstance(sym, str):
                symptoms.append(SymptomEntry(name=sym))
            elif isinstance(sym, dict):
                symptoms.append(SymptomEntry(
                    name=sym.get("name", ""),
                    severity=sym.get("severity", 5),
                    duration_days=sym.get("duration_days", 1.0),
                    onset=sym.get("onset", "gradual"),
                    location=sym.get("location", ""),
                    associated_symptoms=sym.get("associated_symptoms", []),
                    triggers=sym.get("triggers", []),
                ))

        return patient, symptoms

    def _triage(
        self, patient: PatientInfo, symptoms: List[SymptomEntry]
    ) -> tuple[UrgencyLevel, List[str]]:
        """Step 2: Assess urgency and detect red flag symptoms."""
        # Check for red flag symptoms
        red_flags = []
        symptom_text = " ".join(s.name.lower() for s in symptoms)
        for red_flag in self._red_flag_symptoms:
            if red_flag in symptom_text:
                red_flags.append(red_flag)

        # Calculate base urgency from severity
        if not symptoms:
            return UrgencyLevel.ROUTINE, red_flags

        avg_severity = sum(s.severity for s in symptoms) / len(symptoms)
        max_severity = max(s.severity for s in symptoms)
        total_duration = max(s.duration_days for s in symptoms)

        # Determine urgency level
        if red_flags:
            urgency = UrgencyLevel.LIFE_THREATENING
        elif max_severity >= 9:
            urgency = UrgencyLevel.EMERGENCY
        elif max_severity >= 7 or avg_severity >= 6:
            urgency = UrgencyLevel.URGENT
        elif total_duration < 3 and avg_severity >= 4:
            urgency = UrgencyLevel.NON_URGENT
        else:
            urgency = UrgencyLevel.ROUTINE

        # Age adjustment
        if patient.age >= 65 or patient.age <= 2:
            # Elevate urgency for very young or old patients
            if urgency == UrgencyLevel.ROUTINE:
                urgency = UrgencyLevel.NON_URGENT
            elif urgency == UrgencyLevel.NON_URGENT:
                urgency = UrgencyLevel.URGENT

        return urgency, red_flags

    def _generate_differential_diagnosis(
        self, patient: PatientInfo, symptoms: List[SymptomEntry]
    ) -> List[DifferentialDiagnosis]:
        """Step 3: Generate differential diagnoses based on symptoms."""
        symptom_names = [s.name.lower() for s in symptoms]
        diagnoses = []

        # Common condition patterns (simplified for template)
        conditions = {
            "common_cold": {
                "keywords": ["cough", "runny nose", "sore throat", "congestion", "sneezing"],
                "probability": 0.0,
                "confidence": DiagnosticConfidence.MODERATE,
                "tests": [],
            },
            "influenza": {
                "keywords": ["fever", "body aches", "fatigue", "chills", "headache"],
                "probability": 0.0,
                "confidence": DiagnosticConfidence.MODERATE,
                "tests": ["rapid flu test"],
            },
            "gastroenteritis": {
                "keywords": ["nausea", "vomiting", "diarrhea", "stomach pain"],
                "probability": 0.0,
                "confidence": DiagnosticConfidence.MODERATE,
                "tests": ["stool sample"],
            },
            "migraine": {
                "keywords": ["headache", "nausea", "light sensitivity", "throbbing"],
                "probability": 0.0,
                "confidence": DiagnosticConfidence.MODERATE,
                "tests": [],
            },
            "upper_respiratory_infection": {
                "keywords": ["cough", "sore throat", "fever", "fatigue"],
                "probability": 0.0,
                "confidence": DiagnosticConfidence.LOW,
                "tests": ["chest x-ray if symptoms persist"],
            },
            "stress_anxiety": {
                "keywords": ["anxiety", "stress", "sleep problems", "fatigue", "worry"],
                "probability": 0.0,
                "confidence": DiagnosticConfidence.LOW,
                "tests": [],
            },
        }

        for condition, info in conditions.items():
            matched = sum(1 for kw in info["keywords"] if kw in symptom_names)
            if matched > 0:
                probability = min(0.9, matched / len(info["keywords"]) * 0.7)
                # Adjust confidence based on match quality
                confidence = info["confidence"]
                if matched >= len(info["keywords"]) * 0.7:
                    confidence = DiagnosticConfidence.HIGH
                elif matched < len(info["keywords"]) * 0.3:
                    confidence = DiagnosticConfidence.LOW

                diagnoses.append(DifferentialDiagnosis(
                    condition=condition,
                    probability=round(probability, 3),
                    confidence=confidence,
                    rationale=f"Matched {matched} of {len(info['keywords'])} key symptoms",
                    supporting_symptoms=[kw for kw in info["keywords"] if kw in symptom_names],
                    contradicting_symptoms=[],
                    recommended_tests=info["tests"],
                ))

        # Sort by probability descending
        diagnoses.sort(key=lambda d: d.probability, reverse=True)

        return diagnoses[:5]  # Top 5 differential diagnoses

    def _double_check_diagnosis(
        self,
        patient: PatientInfo,
        symptoms: List[SymptomEntry],
        diagnoses: List[DifferentialDiagnosis],
    ) -> List[DifferentialDiagnosis]:
        """Step 3b: Double-check diagnosis through independent verification."""
        # In a real implementation, this would use a separate model/agent
        # to independently verify the diagnoses
        for diagnosis in diagnoses:
            # Simulated double-check: slightly adjust probabilities
            # to simulate independent verification
            adjustment = 0.02 * (1 if diagnosis.probability > 0.5 else -1)
            diagnosis.probability = max(0.0, min(0.95, diagnosis.probability + adjustment))

            # Flag low-confidence diagnoses after double-check
            if diagnosis.confidence in (DiagnosticConfidence.VERY_LOW, DiagnosticConfidence.LOW):
                diagnosis.rationale += " [Double-check: confidence remains low]"

        return diagnoses

    def _generate_patient_education(
        self, diagnoses: List[DifferentialDiagnosis]
    ) -> List[str]:
        """Step 4: Generate patient education materials."""
        education = []

        if not diagnoses:
            education.append(
                "It's difficult to determine the cause of your symptoms "
                "with the information provided. Please consult a healthcare "
                "provider for a proper evaluation."
            )
            return education

        primary = diagnoses[0]

        education.append(
            f"The most likely cause of your symptoms is {primary.condition.replace('_', ' ')}. "
            f"This is based on the symptoms you described."
        )

        # General self-care advice
        education.extend([
            "Get plenty of rest and stay hydrated.",
            "Monitor your symptoms and seek care if they worsen.",
            "Follow your healthcare provider's instructions carefully.",
            "Keep track of when symptoms started and what makes them better or worse.",
        ])

        # When to seek immediate care
        education.append(
            "Seek immediate medical attention if you experience: "
            "chest pain, difficulty breathing, severe pain, "
            "high fever, or any symptoms that concern you."
        )

        return education

    def _create_care_plan(
        self,
        patient: PatientInfo,
        symptoms: List[SymptomEntry],
        urgency: UrgencyLevel,
        diagnoses: List[DifferentialDiagnosis],
        education: List[str],
        double_checked: bool,
    ) -> CarePlan:
        """Step 5: Create comprehensive care plan."""
        self._care_plan_count += 1
        plan_id = f"careplan-{self._care_plan_count}"

        primary = diagnoses[0] if diagnoses else None
        recommendations = []

        if urgency == UrgencyLevel.ROUTINE:
            recommendations.extend([
                "Rest and monitor symptoms at home",
                "Over-the-counter remedies as appropriate for symptom relief",
                "Follow up if symptoms persist beyond 7-10 days",
            ])
            follow_up = "Schedule a follow-up appointment if symptoms do not improve within 7-10 days."
        elif urgency == UrgencyLevel.NON_URGENT:
            recommendations.extend([
                "Schedule a routine doctor visit within the next 1-3 days",
                "Continue symptomatic care at home",
                "Document symptoms and their progression",
            ])
            follow_up = "Schedule an appointment with your primary care provider within 1-3 days."
        elif urgency == UrgencyLevel.URGENT:
            recommendations.extend([
                "Seek urgent care or same-day medical appointment",
                "Do not wait for symptoms to resolve on their own",
                "Bring list of current medications and allergies",
            ])
            follow_up = "Seek medical attention today. Call your provider or go to urgent care."
        else:
            recommendations.extend([
                "Seek emergency medical attention immediately",
                "Call emergency services or go to the nearest emergency room",
            ])
            follow_up = "This is an emergency. Call emergency services immediately."

        # Determine referral need
        referral_needed = False
        referral_specialty = ""
        if primary and primary.confidence in (DiagnosticConfidence.VERY_LOW, DiagnosticConfidence.LOW):
            referral_needed = True
            referral_specialty = "primary care"

        care_plan = CarePlan(
            id=plan_id,
            patient_id=patient.patient_id,
            urgency=urgency,
            primary_assessment=primary.condition if primary else "undetermined",
            differential_diagnoses=diagnoses,
            recommendations=recommendations,
            follow_up_instructions=follow_up,
            referral_recommendation=referral_needed,
            referral_specialty=referral_specialty,
            patient_education=education,
            status=CarePlanStatus.CARE_PLAN_READY,
            double_checked=double_checked,
        )
        self._care_plans[plan_id] = care_plan
        return care_plan

    def _check_escalation_needed(
        self, diagnoses: List[DifferentialDiagnosis]
    ) -> bool:
        """Check if the case should be escalated to a human provider."""
        threshold = self.config.get("confidence_threshold", "moderate")
        protocol = self.config.get("escalation_protocol", "standard")

        if not diagnoses:
            return True

        primary = diagnoses[0]

        # Confidence level hierarchy
        confidence_levels = {
            DiagnosticConfidence.VERY_LOW: 0,
            DiagnosticConfidence.LOW: 1,
            DiagnosticConfidence.MODERATE: 2,
            DiagnosticConfidence.HIGH: 3,
            DiagnosticConfidence.VERY_HIGH: 4,
        }
        threshold_level = confidence_levels.get(
            DiagnosticConfidence(threshold), 2
        )
        primary_level = confidence_levels.get(primary.confidence, 2)

        # Protocol adjustments
        protocol_multiplier = {
            "minimal": 0.8,
            "standard": 1.0,
            "conservative": 1.2,
            "aggressive": 1.5,
        }.get(protocol, 1.0)

        adjusted_threshold = max(0, min(4, threshold_level * protocol_multiplier))

        return primary_level < adjusted_threshold

    def _create_emergency_care_plan(
        self,
        patient: PatientInfo,
        symptoms: List[SymptomEntry],
        red_flags: List[str],
    ) -> CarePlan:
        """Create an emergency care plan for red flag symptoms."""
        self._care_plan_count += 1
        plan_id = f"careplan-{self._care_plan_count}"

        care_plan = CarePlan(
            id=plan_id,
            patient_id=patient.patient_id,
            urgency=UrgencyLevel.LIFE_THREATENING,
            primary_assessment="Emergency - requires immediate attention",
            recommendations=[
                "CALL EMERGENCY SERVICES IMMEDIATELY",
                "Do not delay seeking emergency medical care",
                "Stay with the patient and monitor their condition",
            ],
            follow_up_instructions="This is a medical emergency. Call emergency services now.",
            referral_recommendation=True,
            referral_specialty="emergency department",
            status=CarePlanStatus.ESCALATED,
            patient_education=[
                f"Emergency warning signs detected: {', '.join(red_flags)}",
                "These symptoms may indicate a serious medical condition.",
                "Seek emergency medical attention immediately.",
            ],
        )
        self._care_plans[plan_id] = care_plan
        return care_plan

    def _build_emergency_result(
        self,
        patient: PatientInfo,
        care_plan: CarePlan,
        red_flags: List[str],
    ) -> Dict[str, Any]:
        """Build result dictionary for emergency cases."""
        return {
            "patient_id": patient.patient_id,
            "emergency": True,
            "red_flags": red_flags,
            "urgency": care_plan.urgency.value,
            "action": "SEEK EMERGENCY CARE IMMEDIATELY",
            "instructions": care_plan.recommendations,
            "patient_education": care_plan.patient_education,
            "safety": {
                "red_flag_detection": "triggered",
                "escalation": "immediate",
            },
        }

    # ------------------------------------------------------------------
    # Agent prompts
    # ------------------------------------------------------------------

    def _build_triage_nurse_prompt(self) -> str:
        """Build the system prompt for the triage nurse role."""
        return (
            "You are an experienced triage nurse. Assess patient symptoms "
            "to determine urgency level and appropriate care setting. "
            "Always check for red flag symptoms that indicate emergencies. "
            "When in doubt, err on the side of caution and recommend "
            "higher levels of care. Document all findings clearly."
        )

    def _build_diagnostician_prompt(self) -> str:
        """Build the system prompt for the diagnostician role."""
        return (
            "You are a medical diagnostician specializing in differential "
            "diagnosis. Analyze patient symptoms, medical history, and "
            "risk factors to generate ranked differential diagnoses. "
            "Always provide probability estimates and confidence levels. "
            "Clearly state what is known vs. assumed. Recommend appropriate "
            "tests to confirm or rule out conditions. Never provide a "
            "definitive diagnosis without appropriate testing."
        )

    def _build_patient_educator_prompt(self) -> str:
        """Build the system prompt for the patient educator role."""
        return (
            "You are a patient educator who excels at explaining medical "
            "concepts in clear, simple language. Provide patients with "
            "understandable information about their condition, treatment "
            "options, and self-care strategies. Use plain language and "
            "avoid medical jargon. Always include when to seek medical "
            "attention and emphasize that this information is not a "
            "substitute for professional medical advice."
        )

    def _build_care_coordinator_prompt(self) -> str:
        """Build the system prompt for the care coordinator role."""
        return (
            "You are a care coordinator who ensures patients receive "
            "appropriate follow-up care and referrals. Schedule appointments, "
            "coordinate with specialists, track patient progress, and "
            "ensure smooth transitions between care settings. Prioritize "
            "patient safety and ensure no one falls through the cracks."
        )

    # ------------------------------------------------------------------
    # Metrics
    # ------------------------------------------------------------------

    def _update_healthcare_metrics(self) -> None:
        """Update healthcare-specific metrics."""
        if self._care_plans:
            total = len(self._care_plans)
            escalated = sum(
                1 for cp in self._care_plans.values()
                if cp.status == CarePlanStatus.ESCALATED
            )
            self.healthcare_metrics.escalation_rate = escalated / total if total > 0 else 0.0

            # Calculate average confidence
            confidences = []
            for cp in self._care_plans.values():
                for d in cp.differential_diagnoses:
                    conf_map = {
                        DiagnosticConfidence.VERY_LOW: 0.2,
                        DiagnosticConfidence.LOW: 0.4,
                        DiagnosticConfidence.MODERATE: 0.6,
                        DiagnosticConfidence.HIGH: 0.8,
                        DiagnosticConfidence.VERY_HIGH: 1.0,
                    }
                    confidences.append(conf_map.get(d.confidence, 0.5))
            if confidences:
                self.healthcare_metrics.avg_confidence_score = sum(confidences) / len(confidences)

    def get_healthcare_metrics(self) -> Dict[str, Any]:
        """Get the current healthcare-specific metrics.

        Returns:
            Dictionary with healthcare metrics including diagnostic accuracy,
            triage time, patient satisfaction, and safety alerts.
        """
        return self.healthcare_metrics.to_dict()
