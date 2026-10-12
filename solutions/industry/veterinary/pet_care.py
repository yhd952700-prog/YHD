"""
Veterinary Pet Care AI Solution Template.

A comprehensive AI-powered veterinary assistant for
veterinary clinics, animal hospitals, and pet care services.
Includes multi-agent roles for vet tech, diagnostics,
treatment planning, and client education.

Typical workflow:
    symptom check -> diagnostic triage -> treatment plan
    -> client education -> follow-up
"""

from typing import Optional, Dict, Any, List
from enum import Enum
from dataclasses import dataclass, field

from solutions.templates.base_solution import (
    BaseSolution,
    SolutionMetadata,
    SolutionMetrics,
)


class PetSpecies(str, Enum):
    """Pet species/types."""
    DOG = "dog"
    CAT = "cat"
    RABBIT = "rabbit"
    HAMSTER = "hamster"
    GUINEA_PIG = "guinea_pig"
    BIRD = "bird"
    REPTILE = "reptile"
    FERRET = "ferret"
    FISH = "fish"
    OTHER = "other"


class UrgencyLevel(str, Enum):
    """Medical urgency levels."""
    ROUTINE = "routine"
    SOON = "soon"
    URGENT = "urgent"
    EMERGENCY = "emergency"


class DiagnosticType(str, Enum):
    """Types of diagnostic procedures."""
    PHYSICAL_EXAM = "physical_exam"
    BLOOD_WORK = "blood_work"
    XRAY = "xray"
    ULTRASOUND = "ultrasound"
    URINALYSIS = "urinalysis"
    FECAL_TEST = "fecal_test"
    BIOPSY = "biopsy"
    EKG = "ekg"
    ALLERGY_TEST = "allergy_test"
    DENTAL_EXAM = "dental_exam"


class TreatmentType(str, Enum):
    """Types of veterinary treatments."""
    MEDICATION = "medication"
    SURGERY = "surgery"
    VACCINATION = "vaccination"
    DENTAL_CLEANING = "dental_cleaning"
    PHYSICAL_THERAPY = "physical_therapy"
    DIETARY_CHANGE = "dietary_change"
    SUPPLEMENTS = "supplements"
    TOPICAL_TREATMENT = "topical_treatment"
    FLUID_THERAPY = "fluid_therapy"
    WOUND_CARE = "wound_care"


class VetRole(str, Enum):
    """Agent roles in the veterinary pet care solution."""
    VET_TECH = "vet_tech"
    DIAGNOSTICIAN = "diagnostician"
    TREATMENT_PLANNER = "treatment_planner"
    CLIENT_EDUCATOR = "client_educator"


@dataclass
class PetProfile:
    """Represents a pet patient profile.

    Attributes:
        pet_id: Unique pet identifier.
        name: Pet name.
        species: Pet species.
        breed: Breed (if applicable).
        age_years: Age in years.
        weight_kg: Weight in kilograms.
        sex: Sex and spay/neuter status.
        medical_history: Past medical conditions.
        current_medications: Current medications.
        allergies: Known allergies.
        vaccination_status: Vaccination history.
        diet: Current diet and feeding schedule.
        lifestyle: Lifestyle and environment notes.
    """
    pet_id: str
    name: str = ""
    species: PetSpecies = PetSpecies.DOG
    breed: str = ""
    age_years: float = 0.0
    weight_kg: float = 0.0
    sex: str = ""
    medical_history: List[str] = field(default_factory=list)
    current_medications: List[str] = field(default_factory=list)
    allergies: List[str] = field(default_factory=list)
    vaccination_status: Dict[str, str] = field(default_factory=dict)
    diet: str = ""
    lifestyle: List[str] = field(default_factory=list)


@dataclass
class SymptomAssessment:
    """Represents a symptom check and initial assessment.

    Attributes:
        id: Unique assessment identifier.
        pet_id: Associated pet ID.
        chief_complaint: Primary reason for visit.
        symptoms: List of reported symptoms.
        duration: How long symptoms have been present.
        severity: Symptom severity (1-10).
        urgency: Urgency level assessment.
        possible_conditions: Differential diagnoses.
        red_flags: Warning signs requiring immediate attention.
        home_care_applicable: Whether home care is appropriate.
        recommended_next_steps: Recommended next actions.
        triage_note: Triage assessment note.
    """
    id: str
    pet_id: str = ""
    chief_complaint: str = ""
    symptoms: List[str] = field(default_factory=list)
    duration: str = ""
    severity: float = 0.0
    urgency: UrgencyLevel = UrgencyLevel.ROUTINE
    possible_conditions: List[str] = field(default_factory=list)
    red_flags: List[str] = field(default_factory=list)
    home_care_applicable: bool = False
    recommended_next_steps: List[str] = field(default_factory=list)
    triage_note: str = ""


@dataclass
class DiagnosticPlan:
    """Represents a diagnostic workup plan.

    Attributes:
        id: Unique diagnostic plan identifier.
        pet_id: Associated pet ID.
        assessment_id: Linked symptom assessment ID.
        tests_ordered: List of diagnostic tests to perform.
        test_priorities: Priority order of tests.
        expected_findings: What each test is expected to reveal.
        preparation_requirements: Preparation needed for each test.
        estimated_cost: Estimated cost of diagnostics.
        turnaround_time: Expected time for results.
        differential_diagnoses: Working differential diagnoses.
        additional_notes: Additional diagnostic notes.
    """
    id: str
    pet_id: str = ""
    assessment_id: str = ""
    tests_ordered: List[str] = field(default_factory=list)
    test_priorities: Dict[str, int] = field(default_factory=dict)
    expected_findings: Dict[str, str] = field(default_factory=dict)
    preparation_requirements: Dict[str, str] = field(default_factory=dict)
    estimated_cost: float = 0.0
    turnaround_time: str = ""
    differential_diagnoses: List[str] = field(default_factory=list)
    additional_notes: List[str] = field(default_factory=list)


@dataclass
class TreatmentPlan:
    """Represents a veterinary treatment plan.

    Attributes:
        id: Unique treatment plan identifier.
        pet_id: Associated pet ID.
        diagnosis: Primary diagnosis.
        secondary_issues: Secondary or concurrent issues.
        treatments: List of prescribed treatments.
        medications: Medication details (name, dose, frequency, duration).
        dietary_recommendations: Dietary changes recommended.
        activity_restrictions: Activity level restrictions.
        follow_up_schedule: Follow-up appointment schedule.
        expected_recovery_time: Expected recovery timeline.
        potential_complications: Possible complications to watch for.
        success_rate: Estimated treatment success rate.
        treatment_urgency: How soon treatment should begin.
    """
    id: str
    pet_id: str = ""
    diagnosis: str = ""
    secondary_issues: List[str] = field(default_factory=list)
    treatments: List[str] = field(default_factory=list)
    medications: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    dietary_recommendations: List[str] = field(default_factory=list)
    activity_restrictions: List[str] = field(default_factory=list)
    follow_up_schedule: List[str] = field(default_factory=list)
    expected_recovery_time: str = ""
    potential_complications: List[str] = field(default_factory=list)
    success_rate: float = 0.0
    treatment_urgency: str = ""


@dataclass
class ClientEducation:
    """Represents client education materials and guidance.

    Attributes:
        id: Unique education record identifier.
        pet_id: Associated pet ID.
        condition_explanation: Explanation of the condition.
        medication_instructions: How to administer medications.
        home_care_guidelines: At-home care instructions.
        warning_signs: Signs that require immediate veterinary attention.
        prevention_tips: Tips to prevent recurrence.
        nutritional_advice: Nutritional guidance.
        behavior_tips: Behavior and training advice.
        common_questions: Answers to frequently asked questions.
        resources: Additional resources and references.
        understanding_check: Client comprehension check.
    """
    id: str
    pet_id: str = ""
    condition_explanation: str = ""
    medication_instructions: List[str] = field(default_factory=list)
    home_care_guidelines: List[str] = field(default_factory=list)
    warning_signs: List[str] = field(default_factory=list)
    prevention_tips: List[str] = field(default_factory=list)
    nutritional_advice: List[str] = field(default_factory=list)
    behavior_tips: List[str] = field(default_factory=list)
    common_questions: Dict[str, str] = field(default_factory=dict)
    resources: List[str] = field(default_factory=list)
    understanding_check: List[str] = field(default_factory=list)


@dataclass
class VetMetrics(SolutionMetrics):
    """Extended metrics for the veterinary pet care solution.

    Attributes:
        diagnostic_accuracy: Diagnostic accuracy rate.
        treatment_success: Treatment success rate.
        client_satisfaction: Client satisfaction score (0-5).
        pets_assisted: Total pets assisted.
        avg_triage_time: Average triage time in minutes.
        follow_up_rate: Follow-up appointment completion rate.
        medication_adherence: Estimated medication adherence rate.
        complication_rate: Rate of complications.
        emergency_response_time: Emergency response time.
        client_education_quality: Quality of client education (0-10).
    """
    diagnostic_accuracy: float = 0.0
    treatment_success: float = 0.0
    client_satisfaction: float = 0.0
    pets_assisted: int = 0
    avg_triage_time: float = 0.0
    follow_up_rate: float = 0.0
    medication_adherence: float = 0.0
    complication_rate: float = 0.0
    emergency_response_time: float = 0.0
    client_education_quality: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        """Convert vet metrics to a dictionary."""
        base = super().to_dict()
        base.update({
            "diagnostic_accuracy": self.diagnostic_accuracy,
            "treatment_success": self.treatment_success,
            "client_satisfaction": self.client_satisfaction,
            "pets_assisted": self.pets_assisted,
            "avg_triage_time": self.avg_triage_time,
            "follow_up_rate": self.follow_up_rate,
            "medication_adherence": self.medication_adherence,
            "complication_rate": self.complication_rate,
            "emergency_response_time": self.emergency_response_time,
            "client_education_quality": self.client_education_quality,
        })
        return base


class PetCareSolution(BaseSolution):
    """Veterinary Pet Care AI solution template.

    Provides a complete, pre-configured veterinary pet care solution
    with multi-agent collaboration for symptom assessment, diagnostic
    triage, treatment planning, and client education.

    Pre-configured agent roles:
        - Vet Tech: Conducts initial assessment and triage
        - Diagnostician: Analyzes symptoms and recommends diagnostics
        - Treatment Planner: Develops comprehensive treatment plans
        - Client Educator: Provides guidance and education to pet owners

    Workflow:
        1. Symptom Check - Gather symptoms and perform initial triage
        2. Diagnostic Triage - Recommend appropriate diagnostic tests
        3. Treatment Plan - Develop treatment recommendations
        4. Client Education - Provide pet owner guidance and education
        5. Follow-up - Schedule and manage follow-up care

    Configuration options:
        - primary_species: Primary species served
        - practice_type: Type of veterinary practice
        - triage_level: Triage aggressiveness level
        - telehealth_enabled: Whether telehealth features are enabled
        - follow_up_protocol: Follow-up protocol strictness
    """

    def _build_metadata(self) -> SolutionMetadata:
        """Build metadata for the veterinary pet care solution."""
        return SolutionMetadata(
            name="pet-care",
            version="1.0.0",
            industry="veterinary",
            description=(
                "AI-powered veterinary pet care assistant with symptom "
                "assessment, diagnostic triage, treatment planning, and "
                "client education for companion animals."
            ),
            author="LiuHao AI OS",
            tags=["veterinary", "pet-care", "diagnostics", "treatment", "animal-health", "triage"],
            min_liuhao_version="1.0.0",
        )

    def _build_config_schema(self) -> None:
        """Define configuration fields for the veterinary pet care solution."""
        self.config_schema.add_field(
            name="primary_species",
            field_type="string",
            required=False,
            default="dog",
            description="Primary species served by the practice",
            valid_values=[
                "dog", "cat", "rabbit", "hamster", "guinea_pig",
                "bird", "reptile", "ferret", "fish", "other",
            ],
        )
        self.config_schema.add_field(
            name="practice_type",
            field_type="string",
            required=False,
            default="general_practice",
            description="Type of veterinary practice",
            valid_values=[
                "general_practice", "emergency", "specialist",
                "mobile", "telehealth", "integrative",
            ],
        )
        self.config_schema.add_field(
            name="triage_level",
            field_type="string",
            required=False,
            default="moderate",
            description="Triage aggressiveness level",
            valid_values=["conservative", "moderate", "aggressive"],
        )
        self.config_schema.add_field(
            name="telehealth_enabled",
            field_type="bool",
            required=False,
            default=True,
            description="Whether telehealth features are enabled",
        )
        self.config_schema.add_field(
            name="follow_up_protocol",
            field_type="string",
            required=False,
            default="standard",
            description="Follow-up protocol strictness",
            valid_values=["minimal", "standard", "comprehensive"],
        )
        self.config_schema.add_field(
            name="client_communication_style",
            field_type="string",
            required=False,
            default="supportive",
            description="Client communication style",
            valid_values=["direct", "supportive", "educational", "empathetic"],
        )
        self.config_schema.add_field(
            name="emergency_response_minutes",
            field_type="int",
            required=False,
            default=30,
            description="Target emergency response time in minutes",
        )

    def _on_initialize(self) -> None:
        """Initialize veterinary pet care solution state."""
        self._agents: Dict[str, Dict[str, Any]] = {}
        self._pet_profiles: Dict[str, PetProfile] = {}
        self._assessments: Dict[str, SymptomAssessment] = {}
        self._diagnostic_plans: Dict[str, DiagnosticPlan] = {}
        self._treatment_plans: Dict[str, TreatmentPlan] = {}
        self._client_education: Dict[str, ClientEducation] = {}
        self._pet_count = 0
        self._assessment_count = 0
        self._diagnostic_count = 0
        self._treatment_count = 0
        self._education_count = 0

        # Initialize vet-specific metrics
        self.vet_metrics = VetMetrics()

        # Symptom-condition database
        self._symptom_db = self._build_symptom_database()

    def _build_symptom_database(self) -> Dict[str, Any]:
        """Build symptom-condition database by species."""
        return {
            PetSpecies.DOG: {
                "vomiting": {
                    "conditions": ["gastroenteritis", "pancreatitis", "intestinal obstruction", "kidney disease"],
                    "urgency": "soon",
                    "red_flags": ["bloody vomit", "unproductive retching", "abdominal pain", "lethargy"],
                    "diagnostics": ["blood_work", "xray", "ultrasound"],
                },
                "diarrhea": {
                    "conditions": ["gastroenteritis", "parasites", "dietary indiscretion", "IBD"],
                    "urgency": "soon",
                    "red_flags": ["bloody stool", "black tarry stool", "severe lethargy", "loss of appetite"],
                    "diagnostics": ["fecal_test", "blood_work"],
                },
                "lethargy": {
                    "conditions": ["infection", "anemia", "organ disease", "pain"],
                    "urgency": "urgent",
                    "red_flags": ["collapse", "unresponsiveness", "pale gums", "difficulty breathing"],
                    "diagnostics": ["blood_work", "physical_exam"],
                },
                "loss_of_appetite": {
                    "conditions": ["dental disease", "GI issues", "organ disease", "stress"],
                    "urgency": "soon",
                    "red_flags": ["complete refusal >24h", "weight loss", "vomiting with it"],
                    "diagnostics": ["blood_work", "dental_exam"],
                },
                "coughing": {
                    "conditions": ["kennel cough", "heart disease", "collapsing trachea", "pneumonia"],
                    "urgency": "routine",
                    "red_flags": ["difficulty breathing", "blue gums", "exercise intolerance"],
                    "diagnostics": ["xray", "ekg"],
                },
                "itching": {
                    "conditions": ["allergies", "fleas", "skin infection", "mange"],
                    "urgency": "routine",
                    "red_flags": ["open sores", "hair loss", "severe redness", "swelling"],
                    "diagnostics": ["allergy_test", "skin_scrape"],
                },
                "limping": {
                    "conditions": ["arthritis", "injury", "ACL tear", "hip dysplasia"],
                    "urgency": "soon",
                    "red_flags": ["unable to bear weight", "swelling", "severe pain", "open wound"],
                    "diagnostics": ["xray", "physical_exam"],
                },
            },
            PetSpecies.CAT: {
                "vomiting": {
                    "conditions": ["hairballs", "gastroenteritis", "kidney disease", "IBD"],
                    "urgency": "soon",
                    "red_flags": ["frequent vomiting", "bloody vomit", "lethargy", "not eating"],
                    "diagnostics": ["blood_work", "xray", "ultrasound"],
                },
                "not_eating": {
                    "conditions": ["dental disease", "kidney disease", "liver disease", "stress"],
                    "urgency": "urgent",
                    "red_flags": [">24h without food", "lethargy", "vomiting", "weight loss"],
                    "diagnostics": ["blood_work", "urinalysis"],
                },
                "urinating_outside_box": {
                    "conditions": ["UTI", "kidney disease", "stress", "bladder stones"],
                    "urgency": "soon",
                    "red_flags": ["blood in urine", "straining to urinate", "crying while urinating"],
                    "diagnostics": ["urinalysis", "xray"],
                },
                "sneezing": {
                    "conditions": ["upper respiratory infection", "allergies", "nasal polyps"],
                    "urgency": "routine",
                    "red_flags": ["difficulty breathing", "nasal discharge", "loss of appetite"],
                    "diagnostics": ["physical_exam"],
                },
                "scratching": {
                    "conditions": ["fleas", "allergies", "ear mites", "ringworm"],
                    "urgency": "routine",
                    "red_flags": ["hair loss", "open sores", "swelling"],
                    "diagnostics": ["skin_scrape", "flea_check"],
                },
                "hiding": {
                    "conditions": ["stress", "pain", "illness", "fear"],
                    "urgency": "soon",
                    "red_flags": ["not eating", "not drinking", "lethargy", "weight loss"],
                    "diagnostics": ["physical_exam", "blood_work"],
                },
            },
        }

    def _on_deploy(self) -> None:
        """Deploy veterinary pet care agents and workflows."""
        agent_configs = [
            (VetRole.VET_TECH, {
                "name": "Vet Tech",
                "description": "Conducts initial assessment and triage",
                "system_prompt": self._build_vet_tech_prompt(),
                "tools": ["symptom_check", "triage", "vital_signs"],
            }),
            (VetRole.DIAGNOSTICIAN, {
                "name": "Diagnostician",
                "description": "Analyzes symptoms and recommends diagnostics",
                "system_prompt": self._build_diagnostician_prompt(),
                "tools": ["diagnostic_recommendations", "result_interpretation"],
            }),
            (VetRole.TREATMENT_PLANNER, {
                "name": "Treatment Planner",
                "description": "Develops comprehensive treatment plans",
                "system_prompt": self._build_treatment_planner_prompt(),
                "tools": ["treatment_recommendations", "medication_dosing"],
            }),
            (VetRole.CLIENT_EDUCATOR, {
                "name": "Client Educator",
                "description": "Provides guidance and education to pet owners",
                "system_prompt": self._build_client_educator_prompt(),
                "tools": ["client_education", "home_care_guidance", "medication_instructions"],
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
        """Process a veterinary care request through the full workflow.

        Args:
            input_data: Dictionary containing pet and symptom information.

        Returns:
            Dictionary with veterinary care results including symptom
            assessment, diagnostic plan, treatment plan, and client
            education materials.
        """
        # Step 1: Create/get pet profile
        pet = self._get_or_create_pet(input_data)

        # Step 2: Symptom assessment and triage
        assessment = self._assess_symptoms(pet, input_data)

        # Step 3: Diagnostic plan
        diagnostic = self._create_diagnostic_plan(pet, assessment)

        # Step 4: Treatment plan
        treatment = self._create_treatment_plan(pet, assessment, diagnostic)

        # Step 5: Client education
        education = self._create_client_education(pet, assessment, treatment)

        # Store and update metrics
        self._pet_profiles[pet.pet_id] = pet
        self._assessments[assessment.id] = assessment
        self._diagnostic_plans[diagnostic.id] = diagnostic
        self._treatment_plans[treatment.id] = treatment
        self._client_education[education.id] = education

        self.vet_metrics.pets_assisted += 1
        self._update_vet_metrics()

        return {
            "pet_profile": {
                "id": pet.pet_id,
                "name": pet.name,
                "species": pet.species.value,
                "breed": pet.breed,
                "age_years": pet.age_years,
                "weight_kg": pet.weight_kg,
                "sex": pet.sex,
                "medical_history": pet.medical_history,
                "allergies": pet.allergies,
            },
            "symptom_assessment": {
                "id": assessment.id,
                "chief_complaint": assessment.chief_complaint,
                "symptoms": assessment.symptoms,
                "duration": assessment.duration,
                "severity": assessment.severity,
                "urgency": assessment.urgency.value,
                "possible_conditions": assessment.possible_conditions,
                "red_flags": assessment.red_flags,
                "home_care_applicable": assessment.home_care_applicable,
                "recommended_next_steps": assessment.recommended_next_steps,
                "triage_note": assessment.triage_note,
            },
            "diagnostic_plan": {
                "id": diagnostic.id,
                "tests_ordered": diagnostic.tests_ordered,
                "test_priorities": diagnostic.test_priorities,
                "expected_findings": diagnostic.expected_findings,
                "preparation_requirements": diagnostic.preparation_requirements,
                "estimated_cost": diagnostic.estimated_cost,
                "turnaround_time": diagnostic.turnaround_time,
                "differential_diagnoses": diagnostic.differential_diagnoses,
            },
            "treatment_plan": {
                "id": treatment.id,
                "diagnosis": treatment.diagnosis,
                "secondary_issues": treatment.secondary_issues,
                "treatments": treatment.treatments,
                "medications": list(treatment.medications.keys()),
                "dietary_recommendations": treatment.dietary_recommendations,
                "activity_restrictions": treatment.activity_restrictions,
                "follow_up_schedule": treatment.follow_up_schedule,
                "expected_recovery_time": treatment.expected_recovery_time,
                "potential_complications": treatment.potential_complications,
                "success_rate": treatment.success_rate,
            },
            "client_education": {
                "id": education.id,
                "condition_explanation": education.condition_explanation,
                "medication_instructions": education.medication_instructions,
                "home_care_guidelines": education.home_care_guidelines,
                "warning_signs": education.warning_signs,
                "prevention_tips": education.prevention_tips,
                "nutritional_advice": education.nutritional_advice,
            },
        }

    def _on_teardown(self) -> None:
        """Clean up veterinary pet care solution resources."""
        self._agents.clear()
        self._pet_profiles.clear()
        self._assessments.clear()
        self._diagnostic_plans.clear()
        self._treatment_plans.clear()
        self._client_education.clear()
        self._pet_count = 0
        self._assessment_count = 0
        self._diagnostic_count = 0
        self._treatment_count = 0
        self._education_count = 0

    # ------------------------------------------------------------------
    # Workflow steps
    # ------------------------------------------------------------------

    def _get_or_create_pet(self, data: Dict[str, Any]) -> PetProfile:
        """Get existing pet or create new pet profile."""
        pet_id = data.get("pet_id")
        if pet_id and pet_id in self._pet_profiles:
            return self._pet_profiles[pet_id]

        self._pet_count += 1
        new_id = f"pet-{self._pet_count}"

        try:
            species = PetSpecies(data.get("species", self.config.get("primary_species", "dog")))
        except ValueError:
            species = PetSpecies.DOG

        pet = PetProfile(
            pet_id=new_id,
            name=data.get("pet_name", f"Pet {self._pet_count}"),
            species=species,
            breed=data.get("breed", ""),
            age_years=float(data.get("age_years", 5.0)),
            weight_kg=float(data.get("weight_kg", 10.0)),
            sex=data.get("sex", ""),
            medical_history=data.get("medical_history", []),
            current_medications=data.get("current_medications", []),
            allergies=data.get("allergies", []),
            vaccination_status=data.get("vaccination_status", {}),
            diet=data.get("diet", ""),
            lifestyle=data.get("lifestyle", []),
        )
        self._pet_profiles[new_id] = pet
        return pet

    def _assess_symptoms(self, pet: PetProfile, data: Dict[str, Any]) -> SymptomAssessment:
        """Step 1: Assess symptoms and triage."""
        self._assessment_count += 1
        assessment_id = f"assess-{self._assessment_count}"

        symptoms = data.get("symptoms", [])
        chief_complaint = data.get("chief_complaint", symptoms[0] if symptoms else "general check-up")
        duration = data.get("duration", "unknown")
        severity = float(data.get("severity", 5.0))

        # Look up conditions from symptom database
        species_db = self._symptom_db.get(pet.species, self._symptom_db[PetSpecies.DOG])
        possible_conditions = []
        all_red_flags = []
        all_urgencies = []

        for symptom in symptoms:
            symptom_key = symptom.lower().replace(" ", "_")
            # Try direct match
            if symptom_key in species_db:
                info = species_db[symptom_key]
                possible_conditions.extend(info["conditions"])
                all_red_flags.extend(info["red_flags"])
                all_urgencies.append(info["urgency"])
            else:
                # Try partial match
                for key, info in species_db.items():
                    if key in symptom_key or symptom_key in key:
                        possible_conditions.extend(info["conditions"])
                        all_red_flags.extend(info["red_flags"])
                        all_urgencies.append(info["urgency"])
                        break

        # Deduplicate
        possible_conditions = list(set(possible_conditions))[:6]
        all_red_flags = list(set(all_red_flags))

        # Determine urgency
        urgency_hierarchy = ["routine", "soon", "urgent", "emergency"]
        if severity >= 8:
            urgency = UrgencyLevel.URGENT
        elif severity >= 6:
            urgency = UrgencyLevel.SOON
        else:
            max_urgency = "routine"
            for u in all_urgencies:
                if urgency_hierarchy.index(u) > urgency_hierarchy.index(max_urgency):
                    max_urgency = u
            urgency = UrgencyLevel(max_urgency) if max_urgency in [e.value for e in UrgencyLevel] else UrgencyLevel.ROUTINE

        # Home care applicable?
        home_care = urgency in (UrgencyLevel.ROUTINE, UrgencyLevel.SOON) and severity < 7

        # Next steps
        next_steps = []
        if urgency == UrgencyLevel.EMERGENCY:
            next_steps = [
                "SEEK EMERGENCY VETERINARY CARE IMMEDIATELY",
                "Do not wait - this condition requires immediate attention",
                "Contact your nearest 24-hour emergency animal hospital",
            ]
        elif urgency == UrgencyLevel.URGENT:
            next_steps = [
                "Schedule a same-day veterinary appointment",
                "Monitor for worsening symptoms",
                "Prepare list of symptoms for the vet",
                "Bring any relevant medical history",
            ]
        elif urgency == UrgencyLevel.SOON:
            next_steps = [
                "Schedule a veterinary appointment within 24-48 hours",
                "Monitor symptoms and note any changes",
                "Follow basic home care guidelines",
                "Do not give any medications without vet approval",
            ]
        else:
            next_steps = [
                "Schedule a routine veterinary appointment",
                "Home care may be appropriate - see guidelines",
                "Monitor and track symptoms",
                "Seek vet care if symptoms worsen",
            ]

        # Triage note
        triage_note = (
            f"Pet presents with {', '.join(symptoms) if symptoms else 'general concern'}. "
            f"Symptom severity rated {severity}/10. "
            f"Based on presentation, urgency level is {urgency.value}. "
            f"{'Emergency attention recommended.' if urgency == UrgencyLevel.EMERGENCY else ''}"
            f"{'Please consult with a veterinarian promptly.' if urgency in (UrgencyLevel.URGENT, UrgencyLevel.SOON) else ''}"
        )

        assessment = SymptomAssessment(
            id=assessment_id,
            pet_id=pet.pet_id,
            chief_complaint=chief_complaint,
            symptoms=symptoms,
            duration=duration,
            severity=severity,
            urgency=urgency,
            possible_conditions=possible_conditions if possible_conditions else ["general illness", "needs veterinary evaluation"],
            red_flags=all_red_flags,
            home_care_applicable=home_care,
            recommended_next_steps=next_steps,
            triage_note=triage_note,
        )
        self._assessments[assessment_id] = assessment
        return assessment

    def _create_diagnostic_plan(
        self, pet: PetProfile, assessment: SymptomAssessment
    ) -> DiagnosticPlan:
        """Step 2: Create diagnostic plan."""
        self._diagnostic_count += 1
        diagnostic_id = f"diag-{self._diagnostic_count}"

        species_db = self._symptom_db.get(pet.species, self._symptom_db[PetSpecies.DOG])

        # Collect all relevant diagnostics
        all_diagnostics = set()
        for symptom in assessment.symptoms:
            symptom_key = symptom.lower().replace(" ", "_")
            if symptom_key in species_db:
                all_diagnostics.update(species_db[symptom_key]["diagnostics"])

        if not all_diagnostics:
            all_diagnostics = {"physical_exam", "blood_work"}

        # Convert to list
        tests = list(all_diagnostics)

        # Test priorities
        priorities = {}
        for i, test in enumerate(tests):
            priorities[test] = i + 1

        # Expected findings
        expected_findings = {
            "physical_exam": "Complete physical examination including vital signs, auscultation, palpation, and visual assessment",
            "blood_work": "Complete blood count (CBC) and chemistry panel to assess organ function, blood cells, and electrolytes",
            "xray": "Radiographic images to evaluate bones, joints, chest, or abdomen",
            "ultrasound": "Ultrasonographic examination of abdominal organs or cardiac structures",
            "urinalysis": "Analysis of urine to evaluate kidney function, urinary tract health, and metabolic status",
            "fecal_test": "Examination of feces for parasites, bacteria, and abnormal contents",
            "ekg": "Electrocardiogram to evaluate heart rhythm and electrical activity",
            "allergy_test": "Testing for environmental or food allergies",
            "dental_exam": "Oral examination including teeth, gums, and oral tissues",
        }
        expected = {t: expected_findings.get(t, f"Diagnostic test: {t}") for t in tests}

        # Preparation requirements
        prep = {
            "blood_work": "Fast for 8-12 hours; water is okay",
            "xray": "No special preparation needed; may need sedation depending on area",
            "ultrasound": "Fast for 8-12 hours; full bladder for abdominal ultrasound",
            "urinalysis": "Collect urine sample within 2 hours of appointment",
            "fecal_test": "Bring fresh fecal sample (less than 24 hours old)",
            "physical_exam": "No preparation needed",
            "ekg": "No special preparation; avoid exercise before test",
        }
        preparation = {t: prep.get(t, "Follow standard preparation guidelines") for t in tests}

        # Estimated cost
        cost_map = {
            "physical_exam": 75,
            "blood_work": 150,
            "xray": 200,
            "ultrasound": 350,
            "urinalysis": 50,
            "fecal_test": 40,
            "ekg": 100,
            "allergy_test": 250,
            "dental_exam": 100,
        }
        total_cost = sum(cost_map.get(t, 100) for t in tests)

        # Turnaround time
        turnaround = (
            "Physical exam results: immediate\n"
            "Blood work: 24-48 hours\n"
            "Imaging (xray/ultrasound): same day\n"
            "Fecal/urinalysis: 24-48 hours"
        )

        diagnostic = DiagnosticPlan(
            id=diagnostic_id,
            pet_id=pet.pet_id,
            assessment_id=assessment.id,
            tests_ordered=tests,
            test_priorities=priorities,
            expected_findings=expected,
            preparation_requirements=preparation,
            estimated_cost=float(total_cost),
            turnaround_time=turnaround,
            differential_diagnoses=assessment.possible_conditions,
            additional_notes=[
                "Diagnostics help confirm diagnosis and rule out other conditions",
                "Additional tests may be recommended based on initial findings",
                "Please discuss any concerns with your veterinarian",
            ],
        )
        self._diagnostic_plans[diagnostic_id] = diagnostic
        return diagnostic

    def _create_treatment_plan(
        self,
        pet: PetProfile,
        assessment: SymptomAssessment,
        diagnostic: DiagnosticPlan,
    ) -> TreatmentPlan:
        """Step 3: Create treatment plan."""
        self._treatment_count += 1
        treatment_id = f"treat-{self._treatment_count}"

        # Primary diagnosis (most likely from differential list)
        primary_diagnosis = assessment.possible_conditions[0] if assessment.possible_conditions else "pending diagnosis"

        # Secondary issues
        secondary = assessment.possible_conditions[1:3] if len(assessment.possible_conditions) > 1 else []

        # Treatment types based on symptoms
        treatments = []
        medications = {}

        for condition in assessment.possible_conditions:
            if "infection" in condition.lower() or "uti" in condition.lower():
                treatments.append("Antibiotic therapy")
                medications["Antibiotic"] = {"dose": "weight-based", "frequency": "twice daily", "duration": "7-14 days"}
            elif "allergy" in condition.lower() or "itching" in condition.lower():
                treatments.append("Antihistamine therapy")
                treatments.append("Topical treatment")
                medications["Antihistamine"] = {"dose": "weight-based", "frequency": "once to twice daily", "duration": "as needed"}
            elif "gastro" in condition.lower() or "vomiting" in condition.lower() or "diarrhea" in condition.lower():
                treatments.append("Dietary modification")
                treatments.append("Fluid therapy if dehydrated")
                medications["Anti-nausea medication"] = {"dose": "weight-based", "frequency": "as needed", "duration": "3-5 days"}
                medications["Probiotics"] = {"dose": "standard", "frequency": "daily", "duration": "2-4 weeks"}
            elif "pain" in condition.lower() or "arthrit" in condition.lower() or "limp" in condition.lower():
                treatments.append("Pain management")
                treatments.append("Physical therapy")
                medications["Pain medication"] = {"dose": "weight-based", "frequency": "as directed", "duration": "as needed"}
            elif "dental" in condition.lower():
                treatments.append("Dental cleaning")
                treatments.append("At-home dental care")

        if not treatments:
            treatments = ["Supportive care", "Monitoring", "Follow-up evaluation"]

        # Dietary recommendations
        dietary = []
        if any(s in str(assessment.symptoms).lower() for s in ["vomiting", "diarrhea", "gastro"]):
            dietary.extend([
                "Bland diet (boiled chicken and rice) for 3-5 days",
                "Small, frequent meals",
                "Ensure access to fresh water at all times",
                "Gradually transition back to regular food",
            ])
        else:
            dietary.extend([
                "Continue current diet unless otherwise directed",
                "Ensure fresh water is always available",
                "Monitor appetite and food intake",
            ])

        # Activity restrictions
        activity = []
        if any(s in str(assessment.symptoms).lower() for s in ["limping", "pain", "injury", "surgery"]):
            activity.extend([
                "Restricted activity - leash walks only",
                "No running, jumping, or rough play",
                "Limit stair climbing",
                "Provide comfortable resting area",
            ])
        else:
            activity.append("Normal activity unless symptoms worsen")

        # Follow-up schedule
        follow_up = {
            "emergency": ["Recheck in 24-48 hours", "Weekly follow-up until stable"],
            "urgent": ["Recheck in 3-5 days", "Follow-up in 2 weeks if improving"],
            "soon": ["Recheck in 1 week", "Follow-up in 2-3 weeks"],
            "routine": ["Recheck in 2 weeks", "Follow-up in 1 month"],
        }
        follow_up_schedule = follow_up.get(assessment.urgency.value, follow_up["routine"])

        # Recovery time
        recovery_times = {
            "emergency": "Variable - depends on specific condition",
            "urgent": "1-2 weeks with appropriate treatment",
            "soon": "3-7 days with treatment",
            "routine": "1-2 weeks",
        }
        recovery = recovery_times.get(assessment.urgency.value, "1-2 weeks")

        # Complications
        complications = [
            "Worsening of symptoms",
            "Adverse reaction to medication",
            "Secondary infection",
            "Delayed healing",
        ]

        # Success rate
        success_rates = {
            "routine": 90.0,
            "soon": 80.0,
            "urgent": 70.0,
            "emergency": 50.0,
        }
        success_rate = success_rates.get(assessment.urgency.value, 80.0)

        # Treatment urgency
        treatment_urgency = {
            "emergency": "Immediate treatment required",
            "urgent": "Begin treatment as soon as possible",
            "soon": "Start treatment within 24-48 hours",
            "routine": "Begin treatment at next appointment",
        }.get(assessment.urgency.value, "Standard treatment timeline")

        treatment = TreatmentPlan(
            id=treatment_id,
            pet_id=pet.pet_id,
            diagnosis=primary_diagnosis,
            secondary_issues=secondary,
            treatments=treatments,
            medications=medications,
            dietary_recommendations=dietary,
            activity_restrictions=activity,
            follow_up_schedule=follow_up_schedule,
            expected_recovery_time=recovery,
            potential_complications=complications,
            success_rate=success_rate,
            treatment_urgency=treatment_urgency,
        )
        self._treatment_plans[treatment_id] = treatment
        return treatment

    def _create_client_education(
        self,
        pet: PetProfile,
        assessment: SymptomAssessment,
        treatment: TreatmentPlan,
    ) -> ClientEducation:
        """Step 4: Create client education materials."""
        self._education_count += 1
        education_id = f"edu-{self._education_count}"

        # Condition explanation
        condition_explanation = (
            f"Based on your pet's symptoms, the most likely diagnosis is "
            f"{treatment.diagnosis}. This is a {'common' if assessment.urgency in (UrgencyLevel.ROUTINE, UrgencyLevel.SOON) else 'serious'} "
            f"condition in {pet.species.value}s. With proper treatment, "
            f"the prognosis is {'good' if treatment.success_rate > 70 else 'guarded'} "
            f"(estimated success rate: {treatment.success_rate}%). "
            f"It's important to follow the treatment plan and attend all "
            f"follow-up appointments."
        )

        # Medication instructions
        med_instructions = [
            "Always follow the exact dosage prescribed by your veterinarian",
            "Give medication with food if it causes stomach upset (unless directed otherwise)",
            "Complete the full course of antibiotics, even if your pet seems better",
            "Store medications as directed (refrigeration, light protection, etc.)",
            "Never give human medications without veterinary approval",
            "If you miss a dose, give it as soon as you remember unless it's almost time for the next dose",
        ]

        # Home care guidelines
        home_care = [
            "Monitor your pet's symptoms and note any changes",
            "Ensure your pet has access to fresh water at all times",
            "Keep your pet comfortable and minimize stress",
            "Follow dietary recommendations exactly",
            "Administer all medications as prescribed",
            "Restrict activity as recommended",
            "Keep the environment clean and quiet for recovery",
        ]

        # Warning signs
        warning_signs = [
            "Worsening of current symptoms",
            "Development of new symptoms",
            "Loss of appetite or refusal to drink",
            "Lethargy or unresponsiveness",
            "Difficulty breathing",
            "Severe vomiting or diarrhea",
            "Signs of pain (crying, hiding, aggression)",
            "Seizures or collapse",
        ]
        if assessment.red_flags:
            warning_signs = assessment.red_flags + warning_signs[:5]

        # Prevention tips
        prevention = [
            "Keep up with regular veterinary check-ups",
            "Maintain up-to-date vaccinations",
            "Provide regular parasite prevention (fleas, ticks, heartworm)",
            "Feed a balanced, age-appropriate diet",
            "Maintain healthy weight through diet and exercise",
            "Provide regular dental care",
            "Keep toxic substances out of reach",
            "Provide mental stimulation and exercise",
        ]

        # Nutritional advice
        nutritional = [
            "Feed a high-quality, species-appropriate diet",
            "Follow portion guidelines to maintain healthy weight",
            "Avoid table scraps and human food",
            "Provide fresh water at all times",
            "Consider age-specific and condition-specific diets",
        ]
        if treatment.dietary_recommendations:
            nutritional = treatment.dietary_recommendations + nutritional

        # Behavior tips
        behavior = [
            "Be patient - illness can cause changes in behavior",
            "Maintain routine as much as possible",
            "Provide quiet, safe spaces for rest",
            "Gentle interaction and affection as tolerated",
        ]

        # Common questions
        common_questions = {
            "How long will it take for my pet to feel better?": (
                f"Most pets start showing improvement within 24-72 hours of starting treatment. "
                f"Full recovery typically takes {treatment.expected_recovery_time}."
            ),
            "Can my pet still go for walks?": (
                "Short, leash walks are usually fine unless specifically restricted. "
                "Avoid strenuous activity and watch for signs of fatigue."
            ),
            "What if I miss a dose of medication?": (
                "Give the missed dose as soon as you remember, unless it's almost time for the next dose. "
                "Never double up on doses. If unsure, contact your veterinarian."
            ),
            "When should I be concerned?": (
                "Contact your vet immediately if symptoms worsen, if new symptoms develop, "
                "or if your pet seems significantly worse."
            ),
        }

        # Resources
        resources = [
            "American Veterinary Medical Association (AVMA)",
            "ASPCA Animal Poison Control Center",
            "Pet health and wellness articles",
            "Local emergency veterinary hospitals",
        ]

        # Understanding check
        understanding = [
            "I understand my pet's diagnosis and treatment plan",
            "I know how to administer all medications correctly",
            "I understand the warning signs that require immediate attention",
            "I know when my follow-up appointment is",
            "I know who to contact if I have questions or concerns",
        ]

        education = ClientEducation(
            id=education_id,
            pet_id=pet.pet_id,
            condition_explanation=condition_explanation,
            medication_instructions=med_instructions,
            home_care_guidelines=home_care,
            warning_signs=warning_signs,
            prevention_tips=prevention,
            nutritional_advice=nutritional,
            behavior_tips=behavior,
            common_questions=common_questions,
            resources=resources,
            understanding_check=understanding,
        )
        self._client_education[education_id] = education
        return education

    # ------------------------------------------------------------------
    # Agent prompts
    # ------------------------------------------------------------------

    def _build_vet_tech_prompt(self) -> str:
        """Build the system prompt for the vet tech role."""
        species = self.config.get("primary_species", "dog")
        return (
            f"You are a skilled veterinary technician experienced with {species}s. "
            "Conduct thorough symptom assessments and triage patients appropriately. "
            "Gather complete patient histories, record vital signs accurately, "
            "and identify red flags that require immediate attention. "
            "Communicate clearly and compassionately with pet owners while "
            "maintaining professional standards of care."
        )

    def _build_diagnostician_prompt(self) -> str:
        """Build the system prompt for the diagnostician role."""
        return (
            "You are a veterinary diagnostician with expertise in interpreting "
            "symptoms and recommending appropriate diagnostic tests. Develop "
            "differential diagnoses systematically, considering both common "
            "and rare conditions. Order diagnostics strategically - start "
            "with the most informative and cost-effective tests. Interpret "
            "results accurately and update diagnoses as new information becomes available."
        )

    def _build_treatment_planner_prompt(self) -> str:
        """Build the system prompt for the treatment planner role."""
        return (
            "You are a veterinary treatment planning specialist. Develop "
            "comprehensive, evidence-based treatment plans tailored to "
            "each patient's specific condition, age, and overall health. "
            "Consider medication options, dietary changes, lifestyle "
            "modifications, and follow-up care. Ensure treatment plans "
            "are practical, affordable, and clearly communicated to clients. "
            "Always prioritize patient safety and well-being."
        )

    def _build_client_educator_prompt(self) -> str:
        """Build the system prompt for the client educator role."""
        style = self.config.get("client_communication_style", "supportive")
        return (
            f"You are a veterinary client educator with a {style} communication style. "
            "Explain medical conditions and treatments in clear, understandable language "
            "without unnecessary jargon. Provide thorough home care instructions, "
            "medication administration guidance, and warning signs to watch for. "
            "Address client concerns empathetically and ensure they fully understand "
            "how to care for their pet. Empower pet owners with knowledge and resources."
        )

    # ------------------------------------------------------------------
    # Metrics
    # ------------------------------------------------------------------

    def _update_vet_metrics(self) -> None:
        """Update veterinary-specific metrics."""
        total = self.vet_metrics.pets_assisted
        if total == 0:
            return

        # Diagnostic accuracy (estimated based on typical veterinary rates)
        self.vet_metrics.diagnostic_accuracy = 75.0

        # Treatment success
        if self._treatment_plans:
            avg_success = sum(t.success_rate for t in self._treatment_plans.values()) / len(self._treatment_plans)
            self.vet_metrics.treatment_success = avg_success

        # Client satisfaction
        self.vet_metrics.client_satisfaction = 4.2  # Base estimate

        # Triage time (minutes)
        triage_level = self.config.get("triage_level", "moderate")
        triage_times = {
            "conservative": 15,
            "moderate": 10,
            "aggressive": 5,
        }
        self.vet_metrics.avg_triage_time = float(triage_times.get(triage_level, 10))

        # Follow-up rate
        follow_up_protocol = self.config.get("follow_up_protocol", "standard")
        follow_rates = {
            "minimal": 60.0,
            "standard": 80.0,
            "comprehensive": 95.0,
        }
        self.vet_metrics.follow_up_rate = follow_rates.get(follow_up_protocol, 80.0)

        # Medication adherence
        self.vet_metrics.medication_adherence = 70.0  # Typical estimate

        # Complication rate
        self.vet_metrics.complication_rate = 5.0  # Percentage

        # Emergency response time
        self.vet_metrics.emergency_response_time = float(
            self.config.get("emergency_response_minutes", 30)
        )

        # Client education quality
        self.vet_metrics.client_education_quality = 8.5

    def get_vet_metrics(self) -> Dict[str, Any]:
        """Get the current veterinary pet care metrics.

        Returns:
            Dictionary with vet metrics including diagnostic accuracy,
            treatment success, client satisfaction, and triage time.
        """
        return self.vet_metrics.to_dict()
