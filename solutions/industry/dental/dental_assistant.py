"""
Dental Care Assistant AI Solution Template.

A comprehensive AI-powered dental care assistant for
dental clinics and oral health practices.
Includes multi-agent roles for dental hygienist,
treatment planning, patient education, and insurance coordination.

Typical workflow:
    patient intake -> examination -> treatment plan
    -> patient education -> scheduling
"""

from typing import Optional, Dict, Any, List
from enum import Enum
from dataclasses import dataclass, field

from solutions.templates.base_solution import (
    BaseSolution,
    SolutionMetadata,
    SolutionMetrics,
)


class DentalProcedure(str, Enum):
    """Types of dental procedures."""
    CLEANING = "cleaning"
    EXAM = "exam"
    XRAY = "xray"
    FILLING = "filling"
    CROWN = "crown"
    ROOT_CANAL = "root_canal"
    EXTRACTION = "extraction"
    WHITENING = "whitening"
    IMPLANT = "implant"
    BRACES = "braces"
    INVISALIGN = "invisalign"
    VENEER = "veneer"
    BRIDGE = "bridge"
    DENTURE = "denture"
    GUM_TREATMENT = "gum_treatment"


class UrgencyDental(str, Enum):
    """Dental urgency levels."""
    ROUTINE = "routine"
    SOON = "soon"
    URGENT = "urgent"
    EMERGENCY = "emergency"


class InsuranceType(str, Enum):
    """Types of dental insurance."""
    PPO = "ppo"
    HMO = "hmo"
    DHMO = "dhmo"
    INDIVIDUAL = "individual"
    MEDICARE = "medicare"
    MEDICAID = "medicaid"
    NONE = "none"


class OralHealthIssue(str, Enum):
    """Common oral health issues."""
    CAVITIES = "cavities"
    GUM_DISEASE = "gum_disease"
    SENSITIVITY = "sensitivity"
    BAD_BREATH = "bad_breath"
    PLAQUE = "plaque"
    TARTAR = "tartar"
    WISDOM_TEETH = "wisdom_teeth"
    CROWDING = "crowding"
    GAP = "gap"
    DISCOLORATION = "discoloration"
    CRACKED_TOOTH = "cracked_tooth"
    MISSING_TOOTH = "missing_tooth"


class DentalRole(str, Enum):
    """Agent roles in the dental assistant solution."""
    DENTAL_HYGIENIST = "dental_hygienist"
    TREATMENT_PLANNER = "treatment_planner"
    PATIENT_EDUCATOR = "patient_educator"
    INSURANCE_COORDINATOR = "insurance_coordinator"


@dataclass
class PatientProfile:
    """Represents a dental patient profile.

    Attributes:
        patient_id: Unique patient identifier.
        name: Patient name.
        age: Patient age.
        dental_history: Past dental procedures and history.
        medical_history: Medical conditions affecting dental care.
        medications: Current medications.
        allergies: Known allergies.
        current_issues: Current dental concerns.
        oral_hygiene_habits: Oral care routine description.
        diet: Dietary factors affecting oral health.
        insurance_provider: Dental insurance provider.
        insurance_type: Type of insurance plan.
        last_dental_visit: Date of last dental visit.
        dental_anxiety: Level of dental anxiety (0-10).
    """
    patient_id: str
    name: str = ""
    age: int = 30
    dental_history: List[str] = field(default_factory=list)
    medical_history: List[str] = field(default_factory=list)
    medications: List[str] = field(default_factory=list)
    allergies: List[str] = field(default_factory=list)
    current_issues: List[str] = field(default_factory=list)
    oral_hygiene_habits: Dict[str, Any] = field(default_factory=dict)
    diet: List[str] = field(default_factory=list)
    insurance_provider: str = ""
    insurance_type: InsuranceType = InsuranceType.NONE
    last_dental_visit: str = ""
    dental_anxiety: float = 0.0


@dataclass
class DentalExam:
    """Represents a dental examination result.

    Attributes:
        exam_id: Unique exam identifier.
        patient_id: Associated patient ID.
        exam_type: Type of examination.
        overall_oral_health: Overall oral health score (0-100).
        gum_health: Gum health score (0-100).
        tooth_condition: Tooth condition assessment.
        cavities_found: Number of cavities detected.
        plaque_tartar: Plaque and tartar assessment.
        xray_findings: X-ray findings summary.
        gum_pockets: Gum pocket depth measurements.
        bite_alignment: Bite and alignment assessment.
        issues_identified: List of identified issues.
        urgency_level: Overall urgency assessment.
        exam_notes: General examination notes.
    """
    exam_id: str
    patient_id: str = ""
    exam_type: str = "comprehensive"
    overall_oral_health: float = 0.0
    gum_health: float = 0.0
    tooth_condition: str = ""
    cavities_found: int = 0
    plaque_tartar: str = ""
    xray_findings: str = ""
    gum_pockets: Dict[str, float] = field(default_factory=dict)
    bite_alignment: str = ""
    issues_identified: List[str] = field(default_factory=list)
    urgency_level: UrgencyDental = UrgencyDental.ROUTINE
    exam_notes: str = ""


@dataclass
class TreatmentPlan:
    """Represents a dental treatment plan.

    Attributes:
        plan_id: Unique treatment plan identifier.
        patient_id: Associated patient ID.
        diagnosis: Primary diagnosis.
        procedures: List of procedures with details.
        phases: Treatment phases and ordering.
        total_estimated_cost: Total estimated cost.
        insurance_coverage: Estimated insurance coverage.
        patient_responsibility: Patient's estimated out-of-pocket cost.
        timeline_weeks: Estimated treatment timeline in weeks.
        urgency: Treatment urgency.
        alternatives: Alternative treatment options.
        priority_procedures: Procedures that should be done first.
        pre_treatment_instructions: Pre-treatment instructions.
    """
    plan_id: str
    patient_id: str = ""
    diagnosis: str = ""
    procedures: List[Dict[str, Any]] = field(default_factory=list)
    phases: List[str] = field(default_factory=list)
    total_estimated_cost: float = 0.0
    insurance_coverage: float = 0.0
    patient_responsibility: float = 0.0
    timeline_weeks: float = 0.0
    urgency: UrgencyDental = UrgencyDental.ROUTINE
    alternatives: List[str] = field(default_factory=list)
    priority_procedures: List[str] = field(default_factory=list)
    pre_treatment_instructions: List[str] = field(default_factory=list)


@dataclass
class PatientEducation:
    """Represents patient education materials.

    Attributes:
        id: Unique education record ID.
        patient_id: Associated patient ID.
        oral_hygiene_instructions: Oral care instructions.
        brushing_technique: Proper brushing technique guide.
        flossing_technique: Proper flossing technique guide.
        dietary_advice: Dietary recommendations for oral health.
        procedure_explanation: Explanation of recommended procedures.
        post_treatment_care: Post-procedure care instructions.
        warning_signs: Signs to watch for after treatment.
        prevention_strategies: Strategies to prevent future issues.
        faq: Frequently asked questions and answers.
        follow_up_reminders: Follow-up and maintenance reminders.
    """
    id: str
    patient_id: str = ""
    oral_hygiene_instructions: List[str] = field(default_factory=list)
    brushing_technique: List[str] = field(default_factory=list)
    flossing_technique: List[str] = field(default_factory=list)
    dietary_advice: List[str] = field(default_factory=list)
    procedure_explanation: str = ""
    post_treatment_care: List[str] = field(default_factory=list)
    warning_signs: List[str] = field(default_factory=list)
    prevention_strategies: List[str] = field(default_factory=list)
    faq: Dict[str, str] = field(default_factory=dict)
    follow_up_reminders: List[str] = field(default_factory=list)


@dataclass
class InsuranceEstimate:
    """Represents an insurance coverage estimate.

    Attributes:
        estimate_id: Unique estimate identifier.
        patient_id: Associated patient ID.
        provider: Insurance provider.
        plan_type: Insurance plan type.
        annual_maximum: Annual maximum benefit.
        deductible: Annual deductible.
        preventive_coverage: Preventive care coverage percentage.
        basic_coverage: Basic procedures coverage percentage.
        major_coverage: Major procedures coverage percentage.
        orthodontic_coverage: Orthodontic coverage details.
        waiting_periods: Waiting periods for services.
        in_network_provider: Whether provider is in-network.
        estimated_coverage_per_procedure: Coverage by procedure.
        pre_authorization_required: Procedures needing pre-auth.
    """
    estimate_id: str
    patient_id: str = ""
    provider: str = ""
    plan_type: str = ""
    annual_maximum: float = 0.0
    deductible: float = 0.0
    preventive_coverage: float = 0.0
    basic_coverage: float = 0.0
    major_coverage: float = 0.0
    orthodontic_coverage: str = ""
    waiting_periods: Dict[str, int] = field(default_factory=dict)
    in_network_provider: bool = True
    estimated_coverage_per_procedure: Dict[str, float] = field(default_factory=dict)
    pre_authorization_required: List[str] = field(default_factory=list)


@dataclass
class DentalMetrics(SolutionMetrics):
    """Extended metrics for the dental assistant solution.

    Attributes:
        patient_satisfaction: Patient satisfaction score (0-5).
        treatment_acceptance: Treatment plan acceptance rate.
        hygiene_scores: Average hygiene improvement scores.
        patients_served: Total patients served.
        avg_treatment_cost: Average treatment plan cost.
        insurance_acceptance_rate: Insurance acceptance rate.
        no_show_rate: Appointment no-show rate.
        oral_health_improvement: Average oral health improvement.
        appointment_wait_time: Average wait time for appointments.
        treatment_completion_rate: Treatment completion rate.
    """
    patient_satisfaction: float = 0.0
    treatment_acceptance: float = 0.0
    hygiene_scores: float = 0.0
    patients_served: int = 0
    avg_treatment_cost: float = 0.0
    insurance_acceptance_rate: float = 0.0
    no_show_rate: float = 0.0
    oral_health_improvement: float = 0.0
    appointment_wait_time: float = 0.0
    treatment_completion_rate: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        """Convert dental metrics to a dictionary."""
        base = super().to_dict()
        base.update({
            "patient_satisfaction": self.patient_satisfaction,
            "treatment_acceptance": self.treatment_acceptance,
            "hygiene_scores": self.hygiene_scores,
            "patients_served": self.patients_served,
            "avg_treatment_cost": self.avg_treatment_cost,
            "insurance_acceptance_rate": self.insurance_acceptance_rate,
            "no_show_rate": self.no_show_rate,
            "oral_health_improvement": self.oral_health_improvement,
            "appointment_wait_time": self.appointment_wait_time,
            "treatment_completion_rate": self.treatment_completion_rate,
        })
        return base


class DentalAssistantSolution(BaseSolution):
    """Dental Care Assistant AI solution template.

    Provides a complete, pre-configured dental care assistant solution
    with multi-agent collaboration for patient intake, examination,
    treatment planning, patient education, and insurance coordination.

    Pre-configured agent roles:
        - Dental Hygienist: Conducts exams and cleanings, assesses oral health
        - Treatment Planner: Develops comprehensive treatment plans
        - Patient Educator: Provides oral health education and guidance
        - Insurance Coordinator: Handles insurance verification and estimates

    Workflow:
        1. Patient Intake - Gather patient history and current concerns
        2. Examination - Assess oral health and identify issues
        3. Treatment Plan - Develop personalized treatment plan
        4. Patient Education - Provide oral health guidance
        5. Scheduling - Coordinate appointments and insurance

    Configuration options:
        - practice_type: Type of dental practice
        - insurance_accepted: Whether insurance is accepted
        - sedation_options: Available sedation options
        - appointment_availability: Appointment availability level
        - payment_plans: Whether payment plans are offered
    """

    def _build_metadata(self) -> SolutionMetadata:
        """Build metadata for the dental assistant solution."""
        return SolutionMetadata(
            name="dental-assistant",
            version="1.0.0",
            industry="dental",
            description=(
                "AI-powered dental care assistant with oral health assessment, "
                "treatment planning, patient education, and insurance coordination."
            ),
            author="LiuHao AI OS",
            tags=["dental", "oral-health", "treatment-planning", "patient-education", "insurance"],
            min_liuhao_version="1.0.0",
        )

    def _build_config_schema(self) -> None:
        """Define configuration fields for the dental assistant solution."""
        self.config_schema.add_field(
            name="practice_type",
            field_type="string",
            required=False,
            default="general_dentistry",
            description="Type of dental practice",
            valid_values=[
                "general_dentistry", "pediatric", "orthodontic",
                "endodontic", "periodontal", "oral_surgery",
                "cosmetic", "prosthodontic",
            ],
        )
        self.config_schema.add_field(
            name="insurance_accepted",
            field_type="bool",
            required=False,
            default=True,
            description="Whether dental insurance is accepted",
        )
        self.config_schema.add_field(
            name="sedation_options",
            field_type="string",
            required=False,
            default="none",
            description="Available sedation options",
            valid_values=["none", "nitrous_oxide", "oral_sedation", "iv_sedation", "general_anesthesia"],
        )
        self.config_schema.add_field(
            name="appointment_availability",
            field_type="string",
            required=False,
            default="standard",
            description="Appointment availability level",
            valid_values=["limited", "standard", "extended", "same_day"],
        )
        self.config_schema.add_field(
            name="payment_plans",
            field_type="bool",
            required=False,
            default=True,
            description="Whether payment plans are offered",
        )
        self.config_schema.add_field(
            name="tele_dentistry_enabled",
            field_type="bool",
            required=False,
            default=False,
            description="Whether teledentistry consultations are available",
        )
        self.config_schema.add_field(
            name="preventive_focus",
            field_type="float",
            required=False,
            default=0.7,
            description="Level of preventive care focus (0-1)",
        )

    def _on_initialize(self) -> None:
        """Initialize dental assistant solution state."""
        self._agents: Dict[str, Dict[str, Any]] = {}
        self._patients: Dict[str, PatientProfile] = {}
        self._exams: Dict[str, DentalExam] = {}
        self._treatment_plans: Dict[str, TreatmentPlan] = {}
        self._patient_education: Dict[str, PatientEducation] = {}
        self._insurance_estimates: Dict[str, InsuranceEstimate] = {}
        self._patient_count = 0
        self._exam_count = 0
        self._treatment_count = 0
        self._education_count = 0
        self._insurance_count = 0

        # Initialize dental-specific metrics
        self.dental_metrics = DentalMetrics()

        # Procedure cost database
        self._procedure_costs = self._build_procedure_cost_database()

    def _build_procedure_cost_database(self) -> Dict[str, Any]:
        """Build procedure cost and details database."""
        return {
            DentalProcedure.CLEANING: {
                "name": "Professional Cleaning",
                "cost": 150,
                "duration_min": 45,
                "category": "preventive",
                "description": "Professional teeth cleaning, scaling, and polishing",
                "insurance_coverage": 0.80,
            },
            DentalProcedure.EXAM: {
                "name": "Comprehensive Exam",
                "cost": 100,
                "duration_min": 30,
                "category": "preventive",
                "description": "Complete oral examination including visual inspection and charting",
                "insurance_coverage": 0.80,
            },
            DentalProcedure.XRAY: {
                "name": "Dental X-Rays",
                "cost": 125,
                "duration_min": 15,
                "category": "diagnostic",
                "description": "Full mouth or bitewing X-rays for diagnostic purposes",
                "insurance_coverage": 0.70,
            },
            DentalProcedure.FILLING: {
                "name": "Dental Filling",
                "cost": 250,
                "duration_min": 45,
                "category": "basic",
                "description": "Tooth-colored composite filling for cavity restoration",
                "insurance_coverage": 0.50,
            },
            DentalProcedure.CROWN: {
                "name": "Dental Crown",
                "cost": 1200,
                "duration_min": 120,
                "category": "major",
                "description": "Full tooth restoration with ceramic or porcelain crown",
                "insurance_coverage": 0.30,
            },
            DentalProcedure.ROOT_CANAL: {
                "name": "Root Canal Therapy",
                "cost": 1000,
                "duration_min": 90,
                "category": "major",
                "description": "Endodontic treatment to save infected tooth",
                "insurance_coverage": 0.30,
            },
            DentalProcedure.EXTRACTION: {
                "name": "Tooth Extraction",
                "cost": 200,
                "duration_min": 30,
                "category": "basic",
                "description": "Simple or surgical tooth removal",
                "insurance_coverage": 0.50,
            },
            DentalProcedure.WHITENING: {
                "name": "Teeth Whitening",
                "cost": 400,
                "duration_min": 60,
                "category": "cosmetic",
                "description": "Professional teeth whitening treatment",
                "insurance_coverage": 0.0,
            },
            DentalProcedure.IMPLANT: {
                "name": "Dental Implant",
                "cost": 3000,
                "duration_min": 180,
                "category": "major",
                "description": "Surgical placement of dental implant with crown",
                "insurance_coverage": 0.10,
            },
            DentalProcedure.BRACES: {
                "name": "Traditional Braces",
                "cost": 5000,
                "duration_min": 60,
                "category": "orthodontic",
                "description": "Full orthodontic treatment with traditional braces",
                "insurance_coverage": 0.25,
            },
            DentalProcedure.INVISALIGN: {
                "name": "Invisalign",
                "cost": 5500,
                "duration_min": 45,
                "category": "orthodontic",
                "description": "Clear aligner orthodontic treatment",
                "insurance_coverage": 0.25,
            },
            DentalProcedure.VENEER: {
                "name": "Dental Veneer",
                "cost": 1000,
                "duration_min": 60,
                "category": "cosmetic",
                "description": "Porcelain veneer for cosmetic tooth improvement",
                "insurance_coverage": 0.0,
            },
            DentalProcedure.GUM_TREATMENT: {
                "name": "Gum Disease Treatment",
                "cost": 300,
                "duration_min": 60,
                "category": "basic",
                "description": "Scaling and root planing for periodontal disease",
                "insurance_coverage": 0.50,
            },
        }

    def _on_deploy(self) -> None:
        """Deploy dental assistant agents and workflows."""
        agent_configs = [
            (DentalRole.DENTAL_HYGIENIST, {
                "name": "Dental Hygienist",
                "description": "Conducts exams and assesses oral health",
                "system_prompt": self._build_dental_hygienist_prompt(),
                "tools": ["oral_exam", "cleaning_recommendation", "hygiene_assessment"],
            }),
            (DentalRole.TREATMENT_PLANNER, {
                "name": "Treatment Planner",
                "description": "Develops comprehensive treatment plans",
                "system_prompt": self._build_treatment_planner_prompt(),
                "tools": ["treatment_planning", "cost_estimation", "phased_treatment"],
            }),
            (DentalRole.PATIENT_EDUCATOR, {
                "name": "Patient Educator",
                "description": "Provides oral health education and guidance",
                "system_prompt": self._build_patient_educator_prompt(),
                "tools": ["oral_hygiene_guidance", "procedure_explanation", "post_op_care"],
            }),
            (DentalRole.INSURANCE_COORDINATOR, {
                "name": "Insurance Coordinator",
                "description": "Handles insurance verification and estimates",
                "system_prompt": self._build_insurance_coordinator_prompt(),
                "tools": ["insurance_verification", "cost_estimates", "pre_authorizations"],
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
        """Process a dental care request through the full workflow.

        Args:
            input_data: Dictionary containing patient and dental information.

        Returns:
            Dictionary with dental care results including patient profile,
            exam results, treatment plan, patient education, and
            insurance estimate.
        """
        # Step 1: Patient intake
        patient = self._get_or_create_patient(input_data)

        # Step 2: Examination
        exam = self._conduct_exam(patient, input_data)

        # Step 3: Treatment plan
        treatment = self._create_treatment_plan(patient, exam)

        # Step 4: Patient education
        education = self._create_patient_education(patient, exam, treatment)

        # Step 5: Insurance estimate (if applicable)
        insurance = None
        if patient.insurance_type != InsuranceType.NONE:
            insurance = self._estimate_insurance(patient, treatment)

        # Store and update metrics
        self._patients[patient.patient_id] = patient
        self._exams[exam.exam_id] = exam
        self._treatment_plans[treatment.plan_id] = treatment
        self._patient_education[education.id] = education
        if insurance:
            self._insurance_estimates[insurance.estimate_id] = insurance

        self.dental_metrics.patients_served += 1
        self._update_dental_metrics()

        result = {
            "patient_profile": {
                "id": patient.patient_id,
                "name": patient.name,
                "age": patient.age,
                "current_issues": patient.current_issues,
                "dental_history": patient.dental_history,
                "insurance_provider": patient.insurance_provider,
                "insurance_type": patient.insurance_type.value,
                "dental_anxiety": patient.dental_anxiety,
            },
            "dental_exam": {
                "id": exam.exam_id,
                "exam_type": exam.exam_type,
                "overall_oral_health": exam.overall_oral_health,
                "gum_health": exam.gum_health,
                "cavities_found": exam.cavities_found,
                "plaque_tartar": exam.plaque_tartar,
                "issues_identified": exam.issues_identified,
                "urgency_level": exam.urgency_level.value,
                "exam_notes": exam.exam_notes,
            },
            "treatment_plan": {
                "id": treatment.plan_id,
                "diagnosis": treatment.diagnosis,
                "procedures_count": len(treatment.procedures),
                "procedures": [
                    {
                        "name": p["name"],
                        "category": p["category"],
                        "cost": p["cost"],
                        "duration_min": p["duration_min"],
                        "priority": p["priority"],
                    }
                    for p in treatment.procedures
                ],
                "phases": treatment.phases,
                "total_estimated_cost": treatment.total_estimated_cost,
                "insurance_coverage": treatment.insurance_coverage,
                "patient_responsibility": treatment.patient_responsibility,
                "timeline_weeks": treatment.timeline_weeks,
                "urgency": treatment.urgency.value,
                "priority_procedures": treatment.priority_procedures,
            },
            "patient_education": {
                "id": education.id,
                "oral_hygiene_instructions": education.oral_hygiene_instructions,
                "brushing_technique": education.brushing_technique,
                "flossing_technique": education.flossing_technique,
                "dietary_advice": education.dietary_advice,
                "procedure_explanation": education.procedure_explanation,
                "post_treatment_care": education.post_treatment_care,
                "warning_signs": education.warning_signs,
                "prevention_strategies": education.prevention_strategies,
            },
        }

        if insurance:
            result["insurance_estimate"] = {
                "id": insurance.estimate_id,
                "provider": insurance.provider,
                "plan_type": insurance.plan_type,
                "annual_maximum": insurance.annual_maximum,
                "deductible": insurance.deductible,
                "preventive_coverage": insurance.preventive_coverage,
                "basic_coverage": insurance.basic_coverage,
                "major_coverage": insurance.major_coverage,
                "estimated_coverage_per_procedure": insurance.estimated_coverage_per_procedure,
            }

        return result

    def _on_teardown(self) -> None:
        """Clean up dental assistant solution resources."""
        self._agents.clear()
        self._patients.clear()
        self._exams.clear()
        self._treatment_plans.clear()
        self._patient_education.clear()
        self._insurance_estimates.clear()
        self._patient_count = 0
        self._exam_count = 0
        self._treatment_count = 0
        self._education_count = 0
        self._insurance_count = 0

    # ------------------------------------------------------------------
    # Workflow steps
    # ------------------------------------------------------------------

    def _get_or_create_patient(self, data: Dict[str, Any]) -> PatientProfile:
        """Get existing patient or create new patient profile."""
        patient_id = data.get("patient_id")
        if patient_id and patient_id in self._patients:
            return self._patients[patient_id]

        self._patient_count += 1
        new_id = f"patient-{self._patient_count}"

        try:
            insurance_type = InsuranceType(data.get("insurance_type", "none"))
        except ValueError:
            insurance_type = InsuranceType.NONE

        patient = PatientProfile(
            patient_id=new_id,
            name=data.get("patient_name", f"Patient {self._patient_count}"),
            age=int(data.get("age", 30)),
            dental_history=data.get("dental_history", []),
            medical_history=data.get("medical_history", []),
            medications=data.get("medications", []),
            allergies=data.get("allergies", []),
            current_issues=data.get("current_issues", []),
            oral_hygiene_habits=data.get("oral_hygiene", {
                "brushes_per_day": 2,
                "flosses": False,
                "mouthwash": False,
                "brush_duration_min": 1,
            }),
            diet=data.get("diet", []),
            insurance_provider=data.get("insurance_provider", ""),
            insurance_type=insurance_type,
            last_dental_visit=data.get("last_visit", "more than 1 year ago"),
            dental_anxiety=float(data.get("dental_anxiety", 3.0)),
        )
        self._patients[new_id] = patient
        return patient

    def _conduct_exam(self, patient: PatientProfile, data: Dict[str, Any]) -> DentalExam:
        """Step 1: Conduct dental examination."""
        self._exam_count += 1
        exam_id = f"exam-{self._exam_count}"

        exam_type = data.get("exam_type", "comprehensive")

        # Issues identified from current issues + assessment
        issues = list(patient.current_issues)
        if not issues:
            issues = ["routine check-up needed"]

        # Calculate overall oral health
        hygiene = patient.oral_hygiene_habits
        base_score = 70.0
        if hygiene.get("brushes_per_day", 1) >= 2:
            base_score += 5
        if hygiene.get("flosses", False):
            base_score += 10
        if hygiene.get("mouthwash", False):
            base_score += 3
        if "cavities" in str(issues).lower():
            base_score -= 15
        if "gum" in str(issues).lower():
            base_score -= 10
        if "pain" in str(issues).lower():
            base_score -= 10

        overall = max(20.0, min(100.0, base_score))

        # Gum health
        gum_health = max(30.0, overall - 10) if "gum" in str(issues).lower() else min(95.0, overall + 5)

        # Cavities
        cavities = data.get("cavities", 0)
        if "cavity" in str(issues).lower() or "cavities" in str(issues).lower():
            cavities = max(cavities, 1)

        # Plaque/tartar
        plaque_levels = ["minimal", "mild", "moderate", "heavy"]
        plaque_idx = max(0, min(3, int((100 - overall) / 20)))
        plaque_tartar = plaque_levels[plaque_idx]

        # X-ray findings
        xray_findings = (
            "Bitewing X-rays show general tooth structure. "
            f"{cavities} carious lesion(s) detected. "
            "Bone levels appear " + ("normal" if gum_health > 70 else "slightly reduced") + "."
        )

        # Gum pockets
        gum_pockets = {}
        if "gum" in str(issues).lower() or gum_health < 70:
            gum_pockets = {
                "upper_right": 4.5,
                "upper_left": 4.0,
                "lower_right": 5.0,
                "lower_left": 4.0,
            }
        else:
            gum_pockets = {
                "upper_right": 2.5,
                "upper_left": 2.0,
                "lower_right": 3.0,
                "lower_left": 2.5,
            }

        # Bite alignment
        bite_alignment = data.get("bite_alignment", "normal")
        if "crowding" in str(issues).lower():
            bite_alignment = "crowded"
        elif "gap" in str(issues).lower():
            bite_alignment = "gaps present"

        # Urgency
        urgency = UrgencyDental.ROUTINE
        if "pain" in str(issues).lower() or "emergency" in str(issues).lower():
            urgency = UrgencyDental.URGENT
        if "swelling" in str(issues).lower() or "abscess" in str(issues).lower():
            urgency = UrgencyDental.EMERGENCY
        if "cavity" in str(issues).lower() and urgency == UrgencyDental.ROUTINE:
            urgency = UrgencyDental.SOON

        # Exam notes
        exam_notes = (
            f"Patient presents with {', '.join(issues)}. "
            f"Overall oral health assessed at {overall:.0f}/100. "
            f"{'Caries detected - restorative treatment recommended.' if cavities > 0 else 'No active caries detected.'} "
            f"Gum health: {gum_health:.0f}/100. "
            f"Plaque/tartar: {plaque_tartar}. "
            f"Professional cleaning recommended."
        )

        exam = DentalExam(
            exam_id=exam_id,
            patient_id=patient.patient_id,
            exam_type=exam_type,
            overall_oral_health=round(overall, 1),
            gum_health=round(gum_health, 1),
            tooth_condition="good" if overall > 80 else ("fair" if overall > 60 else "poor"),
            cavities_found=int(cavities),
            plaque_tartar=plaque_tartar,
            xray_findings=xray_findings,
            gum_pockets=gum_pockets,
            bite_alignment=bite_alignment,
            issues_identified=issues,
            urgency_level=urgency,
            exam_notes=exam_notes,
        )
        self._exams[exam_id] = exam
        return exam

    def _create_treatment_plan(
        self, patient: PatientProfile, exam: DentalExam
    ) -> TreatmentPlan:
        """Step 2: Create treatment plan."""
        self._treatment_count += 1
        plan_id = f"treat-{self._treatment_count}"

        # Determine needed procedures based on exam findings
        procedures = []
        total_cost = 0.0
        total_time = 0

        # Always include exam and cleaning (preventive)
        procedures.append(self._build_procedure_item(DentalProcedure.EXAM, 1, "preventive"))
        procedures.append(self._build_procedure_item(DentalProcedure.CLEANING, 1, "preventive"))
        procedures.append(self._build_procedure_item(DentalProcedure.XRAY, 1, "diagnostic"))

        # Based on cavities
        if exam.cavities_found > 0:
            procedures.append(
                self._build_procedure_item(DentalProcedure.FILLING, exam.cavities_found, "high")
            )

        # Based on gum health
        if exam.gum_health < 60:
            procedures.append(
                self._build_procedure_item(DentalProcedure.GUM_TREATMENT, 1, "high")
            )

        # Based on specific issues
        issues_str = str(exam.issues_identified).lower()
        if "wisdom" in issues_str:
            procedures.append(
                self._build_procedure_item(DentalProcedure.EXTRACTION, 1, "medium")
            )
        if "missing" in issues_str:
            procedures.append(
                self._build_procedure_item(DentalProcedure.IMPLANT, 1, "medium")
            )
        if "whitening" in issues_str or "discoloration" in issues_str:
            procedures.append(
                self._build_procedure_item(DentalProcedure.WHITENING, 1, "low")
            )
        if "crowding" in issues_str or "gap" in issues_str:
            procedures.append(
                self._build_procedure_item(DentalProcedure.INVISALIGN, 1, "low")
            )
        if "cracked" in issues_str:
            procedures.append(
                self._build_procedure_item(DentalProcedure.CROWN, 1, "high")
            )

        # Calculate totals
        for proc in procedures:
            total_cost += proc["cost"]
            total_time += proc["duration_min"]

        # Insurance coverage estimate
        insurance_coverage = 0.0
        if patient.insurance_type != InsuranceType.NONE:
            for proc in procedures:
                proc_info = self._procedure_costs.get(proc["procedure_type"], {})
                coverage_pct = proc_info.get("insurance_coverage", 0.0)
                # Adjust for insurance type
                if patient.insurance_type == InsuranceType.PPO:
                    coverage_pct *= 1.0
                elif patient.insurance_type == InsuranceType.HMO:
                    coverage_pct *= 0.9
                elif patient.insurance_type == InsuranceType.DHMO:
                    coverage_pct *= 0.95
                insurance_coverage += proc["cost"] * coverage_pct

        patient_responsibility = total_cost - insurance_coverage

        # Timeline
        priority_procs = [p for p in procedures if p["priority"] == "high"]
        medium_procs = [p for p in procedures if p["priority"] == "medium"]
        low_procs = [p for p in procedures if p["priority"] == "low"]

        # Phases
        phases = []
        if priority_procs:
            phases.append("Phase 1: Urgent/Priority Care (Week 1-2)")
        phases.append("Phase 2: Basic Treatment (Week 2-4)")
        if medium_procs or low_procs:
            phases.append("Phase 3: Comprehensive/Cosmetic Treatment (Month 2+)")
        phases.append("Phase 4: Maintenance (Every 6 months)")

        # Timeline estimate in weeks
        timeline = 2.0  # Base for preventive
        if priority_procs:
            timeline += 2.0
        if len(procedures) > 4:
            timeline += 4.0
        if any(p["category"] == "major" or p["category"] == "orthodontic" for p in procedures):
            timeline += 8.0

        # Diagnosis
        diagnosis_parts = []
        if exam.cavities_found > 0:
            diagnosis_parts.append(f"{exam.cavities_found} carious lesions")
        if exam.gum_health < 70:
            diagnosis_parts.append("gingivitis/mild periodontitis")
        if not diagnosis_parts:
            diagnosis_parts.append("routine preventive care needed")
        diagnosis = ", ".join(diagnosis_parts)

        # Priority procedures
        priority_procedure_names = [p["name"] for p in procedures if p["priority"] == "high"]

        # Alternatives
        alternatives = [
            "Watchful waiting with improved home care (for early issues)",
            "Different material options (amalgam vs composite, etc.)",
            "Phased treatment to spread costs over time",
            "Payment plan options available",
        ]

        # Pre-treatment instructions
        pre_treatment = [
            "Continue regular oral hygiene routine",
            "Take any prescribed medications as directed",
            "Arrive 15 minutes early for appointments",
            "Bring insurance card and ID to each visit",
            "Inform staff of any changes in medical history",
        ]
        if patient.dental_anxiety > 5:
            pre_treatment.append("Discuss anxiety management options with your dentist")

        plan = TreatmentPlan(
            plan_id=plan_id,
            patient_id=patient.patient_id,
            diagnosis=diagnosis,
            procedures=procedures,
            phases=phases,
            total_estimated_cost=round(total_cost, 2),
            insurance_coverage=round(insurance_coverage, 2),
            patient_responsibility=round(patient_responsibility, 2),
            timeline_weeks=round(timeline, 1),
            urgency=exam.urgency_level,
            alternatives=alternatives,
            priority_procedures=priority_procedure_names,
            pre_treatment_instructions=pre_treatment,
        )
        self._treatment_plans[plan_id] = plan
        return plan

    def _build_procedure_item(
        self,
        procedure: DentalProcedure,
        quantity: int,
        priority: str,
    ) -> Dict[str, Any]:
        """Build a procedure item dictionary."""
        info = self._procedure_costs.get(procedure, {
            "name": procedure.value.replace("_", " ").title(),
            "cost": 200,
            "duration_min": 30,
            "category": "basic",
            "description": "Dental procedure",
        })
        return {
            "procedure_type": procedure,
            "name": info["name"],
            "quantity": quantity,
            "cost": info["cost"] * quantity,
            "duration_min": info["duration_min"] * quantity,
            "category": info["category"],
            "description": info["description"],
            "priority": priority,
        }

    def _create_patient_education(
        self,
        patient: PatientProfile,
        exam: DentalExam,
        treatment: TreatmentPlan,
    ) -> PatientEducation:
        """Step 3: Create patient education materials."""
        self._education_count += 1
        education_id = f"edu-{self._education_count}"

        # Oral hygiene instructions
        hygiene_instructions = [
            "Brush teeth at least twice daily for 2 minutes each time",
            "Floss at least once daily to clean between teeth",
            "Use fluoride toothpaste to strengthen enamel",
            "Replace toothbrush every 3-4 months or when bristles fray",
            "Consider using an electric toothbrush for more effective cleaning",
            "Use antimicrobial mouthwash to reduce bacteria",
            "Clean tongue to reduce bacteria and bad breath",
        ]

        # Brushing technique
        brushing = [
            "Position brush at 45-degree angle to gums",
            "Use gentle circular motions on all tooth surfaces",
            "Brush outer, inner, and chewing surfaces of each tooth",
            "Brush tongue to remove bacteria and freshen breath",
            "Spend 30 seconds on each quadrant of the mouth",
            "Use light pressure - aggressive brushing damages gums",
        ]

        # Flossing technique
        flossing = [
            "Use about 18 inches of floss, wrapping around fingers",
            "Gently slide floss between teeth using sawing motion",
            "Curve floss around each tooth in C-shape",
            "Slide floss up and down against tooth surface",
            "Go slightly below gum line on each tooth",
            "Use clean section of floss for each tooth",
            "Rinse mouth after flossing",
        ]

        # Dietary advice
        dietary = [
            "Limit sugary foods and drinks to meal times",
            "Avoid frequent snacking on carbohydrates",
            "Drink plenty of water throughout the day",
            "Choose tooth-friendly snacks (cheese, nuts, vegetables)",
            "Avoid acidic foods and drinks that erode enamel",
            "Chew sugar-free gum after meals to stimulate saliva",
            "Limit coffee, tea, and red wine that stain teeth",
        ]

        # Procedure explanation
        proc_names = [p["name"] for p in treatment.procedures if p["priority"] in ("high", "preventive", "diagnostic")]
        procedure_explanation = (
            f"Your treatment plan includes: {', '.join(proc_names)}. "
            f"These procedures are designed to address {treatment.diagnosis}. "
            f"The total estimated time is approximately "
            f"{sum(p['duration_min'] for p in treatment.procedures)} minutes "
            f"across {len(treatment.phases)} phases. "
            f"Most procedures are performed under local anesthesia and you "
            f"should be able to return to normal activities the same day."
        )

        # Post-treatment care
        post_care = [
            "Avoid eating until numbness wears off to prevent biting tongue/cheek",
            "Take pain medication as directed if needed",
            "Avoid hot foods and drinks for 24 hours after fillings",
            "Maintain soft food diet for first day after extractions",
            "Avoid smoking, alcohol, and vigorous rinsing for 24 hours",
            "Apply cold compress to reduce swelling if needed",
            "Continue regular oral hygiene (gentle near treatment area)",
        ]

        # Warning signs
        warning_signs = [
            "Severe pain not relieved by medication",
            "Heavy bleeding that doesn't stop with pressure",
            "Swelling that gets worse after 48 hours",
            "Fever or signs of infection",
            "Difficulty breathing or swallowing",
            "Adverse reaction to medication",
            "Numbness that persists longer than expected",
        ]

        # Prevention strategies
        prevention = [
            "Maintain excellent oral hygiene at home",
            "Visit dentist every 6 months for check-ups and cleanings",
            "Use fluoridated toothpaste and mouthwash",
            "Consider dental sealants for back teeth (especially children)",
            "Wear mouthguard during sports activities",
            "Avoid using teeth as tools (opening packages, etc.)",
            "Regular dental X-rays as recommended",
            "Replace missing teeth to prevent shifting and bone loss",
        ]

        # FAQ
        faq = {
            "Is the procedure painful?": (
                "Most procedures are performed under local anesthesia, so you "
                "shouldn't feel pain during treatment. You may experience "
                "sensitivity or discomfort afterward, which can be managed "
                "with over-the-counter pain medication."
            ),
            "How long will the results last?": (
                "Dental work longevity depends on the procedure and your oral "
                "hygiene. Fillings typically last 5-15 years, crowns 10-20 "
                "years, and implants can last a lifetime with proper care."
            ),
            "Can I eat normally after treatment?": (
                "For most procedures, you can eat after the numbness wears off. "
                "Stick to soft foods for the first day and avoid very hot or "
                "cold items. Chew on the opposite side of the treatment area."
            ),
            "What if I have dental anxiety?": (
                "Dental anxiety is very common. We offer various comfort "
                "options including nitrous oxide, oral sedation, and breaks "
                "during treatment. Please discuss your concerns with our team."
            ),
            "How often should I get my teeth cleaned?": (
                "Most people benefit from professional cleanings every 6 months. "
                "If you have gum disease or other conditions, more frequent "
                "visits (every 3-4 months) may be recommended."
            ),
        }

        # Follow-up reminders
        follow_up = [
            "Schedule next cleaning and exam in 6 months",
            "Set reminders to replace toothbrush every 3 months",
            "Floss daily - set a phone reminder if needed",
            "Contact office if symptoms worsen or new issues arise",
            "Update medical history at each visit",
        ]

        education = PatientEducation(
            id=education_id,
            patient_id=patient.patient_id,
            oral_hygiene_instructions=hygiene_instructions,
            brushing_technique=brushing,
            flossing_technique=flossing,
            dietary_advice=dietary,
            procedure_explanation=procedure_explanation,
            post_treatment_care=post_care,
            warning_signs=warning_signs,
            prevention_strategies=prevention,
            faq=faq,
            follow_up_reminders=follow_up,
        )
        self._patient_education[education_id] = education
        return education

    def _estimate_insurance(
        self, patient: PatientProfile, treatment: TreatmentPlan
    ) -> InsuranceEstimate:
        """Step 4: Estimate insurance coverage."""
        self._insurance_count += 1
        estimate_id = f"ins-{self._insurance_count}"

        # Coverage percentages by insurance type
        coverage_map = {
            InsuranceType.PPO: {
                "annual_max": 2000,
                "deductible": 50,
                "preventive": 0.80,
                "basic": 0.50,
                "major": 0.30,
                "ortho": 0.25,
            },
            InsuranceType.HMO: {
                "annual_max": 1500,
                "deductible": 25,
                "preventive": 0.90,
                "basic": 0.60,
                "major": 0.30,
                "ortho": 0.20,
            },
            InsuranceType.DHMO: {
                "annual_max": 1000,
                "deductible": 0,
                "preventive": 1.0,
                "basic": 0.80,
                "major": 0.50,
                "ortho": 0.20,
            },
            InsuranceType.INDIVIDUAL: {
                "annual_max": 1500,
                "deductible": 100,
                "preventive": 0.70,
                "basic": 0.40,
                "major": 0.20,
                "ortho": 0.15,
            },
            InsuranceType.MEDICAID: {
                "annual_max": 1000,
                "deductible": 0,
                "preventive": 1.0,
                "basic": 0.80,
                "major": 0.50,
                "ortho": 0.0,
            },
            InsuranceType.NONE: {
                "annual_max": 0,
                "deductible": 0,
                "preventive": 0.0,
                "basic": 0.0,
                "major": 0.0,
                "ortho": 0.0,
            },
        }

        coverage = coverage_map.get(patient.insurance_type, coverage_map[InsuranceType.NONE])

        # Calculate per-procedure coverage
        per_procedure = {}
        for proc in treatment.procedures:
            category = proc["category"]
            if category == "preventive" or category == "diagnostic":
                pct = coverage["preventive"]
            elif category == "basic":
                pct = coverage["basic"]
            elif category == "major":
                pct = coverage["major"]
            elif category == "orthodontic":
                pct = coverage["ortho"]
            elif category == "cosmetic":
                pct = 0.0
            else:
                pct = 0.50
            per_procedure[proc["name"]] = round(proc["cost"] * pct, 2)

        # Waiting periods
        waiting_periods = {
            "preventive": 0,
            "basic": 6,
            "major": 12,
            "orthodontic": 12,
        }

        # Pre-authorization requirements
        pre_auth = []
        for proc in treatment.procedures:
            if proc["category"] in ("major", "orthodontic") and proc["cost"] > 500:
                pre_auth.append(proc["name"])

        estimate = InsuranceEstimate(
            estimate_id=estimate_id,
            patient_id=patient.patient_id,
            provider=patient.insurance_provider or "Not specified",
            plan_type=patient.insurance_type.value.upper(),
            annual_maximum=float(coverage["annual_max"]),
            deductible=float(coverage["deductible"]),
            preventive_coverage=coverage["preventive"] * 100,
            basic_coverage=coverage["basic"] * 100,
            major_coverage=coverage["major"] * 100,
            orthodontic_coverage=(
                f"{coverage['ortho'] * 100:.0f}% coverage, lifetime max varies"
            ),
            waiting_periods=waiting_periods,
            in_network_provider=True,
            estimated_coverage_per_procedure=per_procedure,
            pre_authorization_required=pre_auth,
        )
        self._insurance_estimates[estimate_id] = estimate
        return estimate

    # ------------------------------------------------------------------
    # Agent prompts
    # ------------------------------------------------------------------

    def _build_dental_hygienist_prompt(self) -> str:
        """Build the system prompt for the dental hygienist role."""
        return (
            "You are an experienced dental hygienist. Conduct thorough oral "
            "examinations, assess gum health, evaluate oral hygiene, and "
            "identify dental issues. Provide professional cleanings and "
            "oral hygiene education. Document findings accurately and "
            "communicate concerns to the dentist. Focus on preventive care "
            "and early detection of dental problems."
        )

    def _build_treatment_planner_prompt(self) -> str:
        """Build the system prompt for the treatment planner role."""
        return (
            "You are a dental treatment planning specialist. Develop "
            "comprehensive, phased treatment plans based on examination "
            "findings. Prioritize urgent and preventive care first, then "
            "address restorative and cosmetic needs. Present multiple "
            "options where appropriate, clearly explaining pros, cons, "
            "and costs. Ensure treatment plans are realistic, affordable, "
            "and tailored to each patient's needs and priorities."
        )

    def _build_patient_educator_prompt(self) -> str:
        """Build the system prompt for the patient educator role."""
        return (
            "You are a dental patient educator. Explain dental conditions, "
            "procedures, and oral hygiene in clear, understandable language. "
            "Provide step-by-step instructions for brushing, flossing, and "
            "post-treatment care. Address patient concerns and anxieties "
            "with empathy. Motivate patients to maintain good oral hygiene "
            "and attend regular dental visits. Use visual descriptions "
            "and analogies to help patients understand."
        )

    def _build_insurance_coordinator_prompt(self) -> str:
        """Build the system prompt for the insurance coordinator role."""
        return (
            "You are a dental insurance coordinator. Verify insurance "
            "benefits, estimate coverage for procedures, and handle "
            "pre-authorizations. Clearly explain what insurance covers, "
            "what the patient's out-of-pocket costs will be, and any "
            "limitations or waiting periods. Help patients understand "
            "their benefits and maximize their coverage. Assist with "
            "payment plan options when needed."
        )

    # ------------------------------------------------------------------
    # Metrics
    # ------------------------------------------------------------------

    def _update_dental_metrics(self) -> None:
        """Update dental-specific metrics."""
        total = self.dental_metrics.patients_served
        if total == 0:
            return

        # Patient satisfaction
        self.dental_metrics.patient_satisfaction = 4.3

        # Treatment acceptance rate
        self.dental_metrics.treatment_acceptance = 65.0

        # Hygiene scores
        if self._exams:
            scores = [e.overall_oral_health for e in self._exams.values()]
            self.dental_metrics.hygiene_scores = sum(scores) / len(scores)

        # Average treatment cost
        if self._treatment_plans:
            costs = [t.total_estimated_cost for t in self._treatment_plans.values()]
            self.dental_metrics.avg_treatment_cost = sum(costs) / len(costs)

        # Insurance acceptance rate
        if self._insurance_estimates:
            self.dental_metrics.insurance_acceptance_rate = 85.0

        # No-show rate
        self.dental_metrics.no_show_rate = 8.0

        # Oral health improvement
        self.dental_metrics.oral_health_improvement = 15.0  # Percentage improvement

        # Appointment wait time
        availability = self.config.get("appointment_availability", "standard")
        wait_times = {
            "limited": 21,
            "standard": 14,
            "extended": 7,
            "same_day": 1,
        }
        self.dental_metrics.appointment_wait_time = float(wait_times.get(availability, 14))

        # Treatment completion rate
        self.dental_metrics.treatment_completion_rate = 70.0

    def get_dental_metrics(self) -> Dict[str, Any]:
        """Get the current dental assistant metrics.

        Returns:
            Dictionary with dental metrics including patient satisfaction,
            treatment acceptance, hygiene scores, and treatment cost.
        """
        return self.dental_metrics.to_dict()
