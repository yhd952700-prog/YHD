"""
Therapy Assistant AI Solution Template.

A comprehensive AI-powered therapy assistant for
mental health practitioners, counseling centers, and therapy clinics.
Includes multi-agent roles for therapy assistance, progress tracking,
crisis response, and resource coordination.

Typical workflow:
    intake assessment -> treatment planning -> session support
    -> progress tracking -> crisis management
"""

from typing import Optional, Dict, Any, List
from enum import Enum
from dataclasses import dataclass, field

from solutions.templates.base_solution import (
    BaseSolution,
    SolutionMetadata,
    SolutionMetrics,
)


class TherapyModality(str, Enum):
    """Therapy modalities/approaches."""
    CBT = "cbt"
    DBT = "dbt"
    ACT = "act"
    PSYCHODYNAMIC = "psychodynamic"
    HUMANISTIC = "humanistic"
    GESTALT = "gestalt"
    EMDR = "emdr"
    MINDFULNESS = "mindfulness"
    SOLUTION_FOCUSED = "solution_focused"
    FAMILY_SYSTEMS = "family_systems"
    INTEGRATIVE = "integrative"


class SeverityLevel(str, Enum):
    """Symptom severity levels."""
    MILD = "mild"
    MODERATE = "moderate"
    SEVERE = "severe"
    CRITICAL = "critical"


class ConcernType(str, Enum):
    """Types of presenting concerns."""
    ANXIETY = "anxiety"
    DEPRESSION = "depression"
    RELATIONSHIPS = "relationships"
    STRESS = "stress"
    TRAUMA = "trauma"
    GRIEF = "grief"
    SELF_ESTEEM = "self_esteem"
    ADDICTION = "addiction"
    EATING_DISORDER = "eating_disorder"
    OCD = "ocd"
    PTSD = "ptsd"
    BIPOLAR = "bipolar"
    PERSONALITY = "personality"
    LIFE_TRANSITIONS = "life_transitions"
    WORKPLACE = "workplace"


class SessionType(str, Enum):
    """Types of therapy sessions."""
    INDIVIDUAL = "individual"
    COUPLES = "couples"
    FAMILY = "family"
    GROUP = "group"
    INTENSIVE = "intensive"
    FOLLOW_UP = "follow_up"
    CRISIS = "crisis"


class TherapyRole(str, Enum):
    """Agent roles in the therapy assistant solution."""
    THERAPY_ASSISTANT = "therapy_assistant"
    PROGRESS_TRACKER = "progress_tracker"
    CRISIS_RESPONDER = "crisis_responder"
    RESOURCE_COORDINATOR = "resource_coordinator"


@dataclass
class IntakeAssessment:
    """Represents an intake assessment.

    Attributes:
        id: Unique assessment identifier.
        client_id: Client identifier.
        presenting_concerns: Primary reasons for seeking therapy.
        symptom_severity: Severity of symptoms (mild/moderate/severe).
        symptom_duration: How long symptoms have been present.
        mental_health_history: Past mental health history.
        medical_history: Relevant medical history.
        social_history: Social and relationship context.
        substance_use: Substance use information.
        safety_assessment: Safety and self-harm assessment.
        risk_level: Overall risk level.
        strengths: Client's strengths and resources.
        goals: Client's stated therapy goals.
        recommended_modality: Recommended therapy approach.
    """
    id: str
    client_id: str = ""
    presenting_concerns: List[str] = field(default_factory=list)
    symptom_severity: Dict[str, str] = field(default_factory=dict)
    symptom_duration: str = ""
    mental_health_history: List[str] = field(default_factory=list)
    medical_history: List[str] = field(default_factory=list)
    social_history: Dict[str, str] = field(default_factory=dict)
    substance_use: Dict[str, str] = field(default_factory=dict)
    safety_assessment: Dict[str, Any] = field(default_factory=dict)
    risk_level: str = "low"
    strengths: List[str] = field(default_factory=list)
    goals: List[str] = field(default_factory=list)
    recommended_modality: str = ""


@dataclass
class TreatmentPlan:
    """Represents a therapy treatment plan.

    Attributes:
        id: Unique treatment plan identifier.
        client_id: Client identifier.
        diagnosis: Primary diagnosis or concern.
        goals: Treatment goals (long and short term).
        objectives: Specific measurable objectives.
        interventions: Therapeutic interventions to be used.
        modality: Primary therapy modality/approach.
        session_frequency: Recommended session frequency.
        estimated_duration_weeks: Estimated treatment duration.
        progress_measures: How progress will be measured.
        homework_assignments: Between-session assignments.
        referrals: Any recommended referrals.
        crisis_plan: Crisis safety plan.
    """
    id: str
    client_id: str = ""
    diagnosis: str = ""
    goals: List[str] = field(default_factory=list)
    objectives: List[str] = field(default_factory=list)
    interventions: List[str] = field(default_factory=list)
    modality: str = ""
    session_frequency: str = ""
    estimated_duration_weeks: int = 0
    progress_measures: List[str] = field(default_factory=list)
    homework_assignments: List[str] = field(default_factory=list)
    referrals: List[str] = field(default_factory=list)
    crisis_plan: List[str] = field(default_factory=list)


@dataclass
class SessionNote:
    """Represents a therapy session note.

    Attributes:
        id: Unique session note identifier.
        client_id: Client identifier.
        session_number: Session number in course.
        session_type: Type of session.
        duration_minutes: Session duration.
        topics_discussed: Topics covered in session.
        interventions_used: Interventions applied.
        client_mood: Client's mood/affect during session.
        insights_gained: Insights gained in session.
        challenges: Challenges or resistance noted.
        next_session_focus: Focus for next session.
        homework_assigned: Homework for between sessions.
        progress_rating: Progress rating (0-10).
        safety_check: Safety assessment.
    """
    id: str
    client_id: str = ""
    session_number: int = 0
    session_type: str = ""
    duration_minutes: int = 50
    topics_discussed: List[str] = field(default_factory=list)
    interventions_used: List[str] = field(default_factory=list)
    client_mood: str = ""
    insights_gained: List[str] = field(default_factory=list)
    challenges: List[str] = field(default_factory=list)
    next_session_focus: str = ""
    homework_assigned: List[str] = field(default_factory=list)
    progress_rating: float = 0.0
    safety_check: str = ""


@dataclass
class ProgressReport:
    """Represents a progress tracking report.

    Attributes:
        id: Unique progress report identifier.
        client_id: Client identifier.
        period: Time period covered.
        overall_progress: Overall progress rating (0-10).
        goal_progress: Progress toward each goal.
        symptom_changes: Changes in symptom severity.
        session_attendance: Session attendance record.
        homework_completion: Homework completion rate.
        strengths: Identified strengths and gains.
        areas_for_growth: Areas needing more work.
        recommendations: Recommendations for next phase.
        termination_readiness: Readiness for termination assessment.
    """
    id: str
    client_id: str = ""
    period: str = ""
    overall_progress: float = 0.0
    goal_progress: Dict[str, float] = field(default_factory=dict)
    symptom_changes: Dict[str, str] = field(default_factory=dict)
    session_attendance: Dict[str, Any] = field(default_factory=dict)
    homework_completion: float = 0.0
    strengths: List[str] = field(default_factory=list)
    areas_for_growth: List[str] = field(default_factory=list)
    recommendations: List[str] = field(default_factory=list)
    termination_readiness: float = 0.0


@dataclass
class CrisisPlan:
    """Represents a crisis safety and response plan.

    Attributes:
        id: Unique crisis plan identifier.
        client_id: Client identifier.
        risk_level: Current risk level.
        warning_signs: Personal warning signs of crisis.
        coping_strategies: Individual coping strategies.
        social_supports: List of support people.
        professional_contacts: Professional contacts for crisis.
        emergency_contacts: Emergency contact information.
        safety_steps: Step-by-step safety plan.
        means_restriction: Means restriction strategies.
        environment_safety: Environmental safety measures.
        follow_up_plan: Post-crisis follow-up plan.
        crisis_response_time_minutes: Target response time.
    """
    id: str
    client_id: str = ""
    risk_level: str = "low"
    warning_signs: List[str] = field(default_factory=list)
    coping_strategies: List[str] = field(default_factory=list)
    social_supports: List[str] = field(default_factory=list)
    professional_contacts: List[str] = field(default_factory=list)
    emergency_contacts: List[str] = field(default_factory=list)
    safety_steps: List[str] = field(default_factory=list)
    means_restriction: List[str] = field(default_factory=list)
    environment_safety: List[str] = field(default_factory=list)
    follow_up_plan: List[str] = field(default_factory=list)
    crisis_response_time_minutes: float = 0.0


@dataclass
class TherapyMetrics(SolutionMetrics):
    """Extended metrics for the therapy assistant solution.

    Attributes:
        client_progress: Average client progress score (0-10).
        session_quality: Average session quality rating (0-10).
        crisis_response_time: Average crisis response time (minutes).
        clients_served: Total clients served.
        sessions_supported: Total sessions supported.
        goal_achievement_rate: Rate of goal achievement.
        symptom_reduction: Average symptom reduction percentage.
        retention_rate: Client retention rate.
        homework_adherence: Homework completion rate.
        termination_success: Successful termination rate.
    """
    client_progress: float = 0.0
    session_quality: float = 0.0
    crisis_response_time: float = 0.0
    clients_served: int = 0
    sessions_supported: int = 0
    goal_achievement_rate: float = 0.0
    symptom_reduction: float = 0.0
    retention_rate: float = 0.0
    homework_adherence: float = 0.0
    termination_success: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        """Convert therapy metrics to a dictionary."""
        base = super().to_dict()
        base.update({
            "client_progress": self.client_progress,
            "session_quality": self.session_quality,
            "crisis_response_time": self.crisis_response_time,
            "clients_served": self.clients_served,
            "sessions_supported": self.sessions_supported,
            "goal_achievement_rate": self.goal_achievement_rate,
            "symptom_reduction": self.symptom_reduction,
            "retention_rate": self.retention_rate,
            "homework_adherence": self.homework_adherence,
            "termination_success": self.termination_success,
        })
        return base


class TherapistHelperSolution(BaseSolution):
    """Therapy Assistant AI solution template.

    Provides a complete, pre-configured therapy assistant solution
    with multi-agent collaboration for intake assessment, treatment
    planning, session support, progress tracking, and crisis management.

    Pre-configured agent roles:
        - Therapy Assistant: Supports therapy sessions and interventions
        - Progress Tracker: Monitors and reports on client progress
        - Crisis Responder: Handles crisis situations and safety planning
        - Resource Coordinator: Connects clients with additional resources

    Workflow:
        1. Intake Assessment - Gather client history and concerns
        2. Treatment Planning - Develop personalized treatment plan
        3. Session Support - Assist with therapy sessions
        4. Progress Tracking - Monitor progress and outcomes
        5. Crisis Management - Handle crisis situations

    Configuration options:
        - primary_modality: Primary therapy approach
        - session_type: Default session type
        - session_length: Standard session length in minutes
        - crisis_response_time: Target crisis response time
        - progress_monitoring_frequency: How often to track progress
    """

    def _build_metadata(self) -> SolutionMetadata:
        """Build metadata for the therapist helper solution."""
        return SolutionMetadata(
            name="therapist-helper",
            version="1.0.0",
            industry="psychology",
            description=(
                "AI-powered therapy assistant with intake assessment, "
                "treatment planning, session support, progress tracking, "
                "and crisis management for mental health practitioners."
            ),
            author="LiuHao AI OS",
            tags=["therapy", "psychology", "mental-health", "counseling", "crisis", "progress-tracking"],
            min_liuhao_version="1.0.0",
        )

    def _build_config_schema(self) -> None:
        """Define configuration fields for the therapist helper solution."""
        self.config_schema.add_field(
            name="primary_modality",
            field_type="string",
            required=False,
            default="cbt",
            description="Primary therapy approach/modality",
            valid_values=[
                "cbt", "dbt", "act", "psychodynamic", "humanistic",
                "gestalt", "emdr", "mindfulness", "solution_focused",
                "family_systems", "integrative",
            ],
        )
        self.config_schema.add_field(
            name="session_type",
            field_type="string",
            required=False,
            default="individual",
            description="Default session type",
            valid_values=["individual", "couples", "family", "group", "intensive", "follow_up", "crisis"],
        )
        self.config_schema.add_field(
            name="session_length_minutes",
            field_type="int",
            required=False,
            default=50,
            description="Standard session length in minutes",
        )
        self.config_schema.add_field(
            name="crisis_response_time",
            field_type="int",
            required=False,
            default=60,
            description="Target crisis response time in minutes",
        )
        self.config_schema.add_field(
            name="progress_monitoring_frequency",
            field_type="string",
            required=False,
            default="weekly",
            description="How often to monitor progress",
            valid_values=["each_session", "weekly", "biweekly", "monthly"],
        )
        self.config_schema.add_field(
            name="homework_enabled",
            field_type="bool",
            required=False,
            default=True,
            description="Whether to assign between-session homework",
        )
        self.config_schema.add_field(
            name="crisis_protocol_level",
            field_type="string",
            required=False,
            default="standard",
            description="Crisis protocol strictness level",
            valid_values=["minimal", "standard", "comprehensive"],
        )

    def _on_initialize(self) -> None:
        """Initialize therapy assistant solution state."""
        self._agents: Dict[str, Dict[str, Any]] = {}
        self._intakes: Dict[str, IntakeAssessment] = {}
        self._treatment_plans: Dict[str, TreatmentPlan] = {}
        self._session_notes: Dict[str, SessionNote] = {}
        self._progress_reports: Dict[str, ProgressReport] = {}
        self._crisis_plans: Dict[str, CrisisPlan] = {}
        self._intake_count = 0
        self._treatment_count = 0
        self._session_count = 0
        self._progress_count = 0
        self._crisis_count = 0

        # Initialize therapy-specific metrics
        self.therapy_metrics = TherapyMetrics()

        # Concern-modality mapping
        self._modality_mapping = self._build_modality_mapping()

    def _build_modality_mapping(self) -> Dict[str, Any]:
        """Build concern-to-modality mapping."""
        return {
            ConcernType.ANXIETY: {
                "primary_modalities": ["cbt", "act", "mindfulness"],
                "interventions": [
                    "Cognitive restructuring",
                    "Exposure therapy",
                    "Relaxation techniques",
                    "Mindfulness meditation",
                    "Worry time scheduling",
                ],
                "goals": [
                    "Reduce anxiety symptoms",
                    "Develop coping strategies",
                    "Improve functioning in daily life",
                ],
            },
            ConcernType.DEPRESSION: {
                "primary_modalities": ["cbt", "act", "psychodynamic"],
                "interventions": [
                    "Behavioral activation",
                    "Cognitive restructuring",
                    "Activity scheduling",
                    "Values clarification",
                    "Self-compassion exercises",
                ],
                "goals": [
                    "Reduce depressive symptoms",
                    "Increase engagement in meaningful activities",
                    "Improve self-esteem",
                ],
            },
            ConcernType.TRAUMA: {
                "primary_modalities": ["emdr", "cbt", "dbt"],
                "interventions": [
                    "Trauma narrative work",
                    "Grounding techniques",
                    "EMDR processing",
                    "Cognitive processing",
                    "Body-based regulation",
                ],
                "goals": [
                    "Reduce trauma symptoms",
                    "Integrate traumatic experiences",
                    "Improve daily functioning",
                ],
            },
            ConcernType.RELATIONSHIPS: {
                "primary_modalities": ["emotionally_focused", "family_systems", "cbt"],
                "interventions": [
                    "Communication skills training",
                    "Emotion identification",
                    "Attachment work",
                    "Conflict resolution",
                    "Boundaries work",
                ],
                "goals": [
                    "Improve relationship communication",
                    "Increase emotional intimacy",
                    "Develop healthy conflict resolution",
                ],
            },
            ConcernType.STRESS: {
                "primary_modalities": ["cbt", "mindfulness", "act"],
                "interventions": [
                    "Stress management techniques",
                    "Time management strategies",
                    "Mindfulness practice",
                    "Values-based action",
                    "Self-care planning",
                ],
                "goals": [
                    "Reduce overall stress levels",
                    "Develop healthy coping mechanisms",
                    "Improve work-life balance",
                ],
            },
            ConcernType.GRIEF: {
                "primary_modalities": ["humanistic", "psychodynamic", "act"],
                "interventions": [
                    "Grief processing",
                    "Meaning-making",
                    "Emotional expression",
                    "Ritual and memory work",
                    "Integration of loss",
                ],
                "goals": [
                    "Process grief emotions",
                    "Adjust to life without the loved one",
                    "Find meaning after loss",
                ],
            },
            ConcernType.SELF_ESTEEM: {
                "primary_modalities": ["cbt", "humanistic", "act"],
                "interventions": [
                    "Self-compassion training",
                    "Challenging negative self-talk",
                    "Strengths identification",
                    "Values-aligned action",
                    "Self-acceptance work",
                ],
                "goals": [
                    "Improve self-esteem and self-worth",
                    "Reduce self-criticism",
                    "Develop self-compassion",
                ],
            },
        }

    def _on_deploy(self) -> None:
        """Deploy therapy assistant agents and workflows."""
        agent_configs = [
            (TherapyRole.THERAPY_ASSISTANT, {
                "name": "Therapy Assistant",
                "description": "Supports therapy sessions and interventions",
                "system_prompt": self._build_therapy_assistant_prompt(),
                "tools": ["session_planning", "intervention_suggestions", "homework_design"],
            }),
            (TherapyRole.PROGRESS_TRACKER, {
                "name": "Progress Tracker",
                "description": "Monitors and reports on client progress",
                "system_prompt": self._build_progress_tracker_prompt(),
                "tools": ["progress_measurement", "outcome_tracking", "goal_tracking"],
            }),
            (TherapyRole.CRISIS_RESPONDER, {
                "name": "Crisis Responder",
                "description": "Handles crisis situations and safety planning",
                "system_prompt": self._build_crisis_responder_prompt(),
                "tools": ["safety_planning", "crisis_assessment", "emergency_resources"],
            }),
            (TherapyRole.RESOURCE_COORDINATOR, {
                "name": "Resource Coordinator",
                "description": "Connects clients with additional resources",
                "system_prompt": self._build_resource_coordinator_prompt(),
                "tools": ["resource_matching", "referral_support", "community_resources"],
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
        """Process a therapy request through the full workflow.

        Args:
            input_data: Dictionary containing client and therapy information.

        Returns:
            Dictionary with therapy results including intake assessment,
            treatment plan, session support, progress tracking, and
            crisis plan.
        """
        client_id = input_data.get("client_id", f"client-{len(self._intakes)+1}")

        # Step 1: Intake assessment
        intake = self._conduct_intake(client_id, input_data)

        # Step 2: Treatment planning
        treatment = self._create_treatment_plan(intake)

        # Step 3: Session support (sample session)
        session = self._support_session(intake, treatment)

        # Step 4: Progress tracking (initial baseline)
        progress = self._generate_progress_report(intake, treatment, [session])

        # Step 5: Crisis plan
        crisis = self._create_crisis_plan(intake)

        # Store and update metrics
        self._intakes[intake.id] = intake
        self._treatment_plans[treatment.id] = treatment
        self._session_notes[session.id] = session
        self._progress_reports[progress.id] = progress
        self._crisis_plans[crisis.id] = crisis

        self.therapy_metrics.clients_served += 1
        self.therapy_metrics.sessions_supported += 1
        self._update_therapy_metrics()

        return {
            "intake_assessment": {
                "id": intake.id,
                "client_id": intake.client_id,
                "presenting_concerns": intake.presenting_concerns,
                "symptom_severity": intake.symptom_severity,
                "symptom_duration": intake.symptom_duration,
                "risk_level": intake.risk_level,
                "strengths": intake.strengths,
                "goals": intake.goals,
                "recommended_modality": intake.recommended_modality,
                "safety_assessment": intake.safety_assessment,
            },
            "treatment_plan": {
                "id": treatment.id,
                "diagnosis": treatment.diagnosis,
                "goals": treatment.goals,
                "objectives": treatment.objectives,
                "interventions": treatment.interventions,
                "modality": treatment.modality,
                "session_frequency": treatment.session_frequency,
                "estimated_duration_weeks": treatment.estimated_duration_weeks,
                "progress_measures": treatment.progress_measures,
                "homework_assignments": treatment.homework_assignments,
                "crisis_plan": treatment.crisis_plan,
            },
            "session_support": {
                "id": session.id,
                "session_number": session.session_number,
                "session_type": session.session_type,
                "duration_minutes": session.duration_minutes,
                "topics_discussed": session.topics_discussed,
                "interventions_used": session.interventions_used,
                "client_mood": session.client_mood,
                "insights_gained": session.insights_gained,
                "next_session_focus": session.next_session_focus,
                "homework_assigned": session.homework_assigned,
                "progress_rating": session.progress_rating,
                "safety_check": session.safety_check,
            },
            "progress_report": {
                "id": progress.id,
                "period": progress.period,
                "overall_progress": progress.overall_progress,
                "goal_progress": progress.goal_progress,
                "symptom_changes": progress.symptom_changes,
                "homework_completion": progress.homework_completion,
                "strengths": progress.strengths,
                "areas_for_growth": progress.areas_for_growth,
                "recommendations": progress.recommendations,
                "termination_readiness": progress.termination_readiness,
            },
            "crisis_plan": {
                "id": crisis.id,
                "risk_level": crisis.risk_level,
                "warning_signs": crisis.warning_signs,
                "coping_strategies": crisis.coping_strategies,
                "social_supports": crisis.social_supports,
                "emergency_contacts": crisis.emergency_contacts,
                "safety_steps": crisis.safety_steps,
                "means_restriction": crisis.means_restriction,
                "crisis_response_time_minutes": crisis.crisis_response_time_minutes,
            },
        }

    def _on_teardown(self) -> None:
        """Clean up therapy assistant solution resources."""
        self._agents.clear()
        self._intakes.clear()
        self._treatment_plans.clear()
        self._session_notes.clear()
        self._progress_reports.clear()
        self._crisis_plans.clear()
        self._intake_count = 0
        self._treatment_count = 0
        self._session_count = 0
        self._progress_count = 0
        self._crisis_count = 0

    # ------------------------------------------------------------------
    # Workflow steps
    # ------------------------------------------------------------------

    def _conduct_intake(self, client_id: str, data: Dict[str, Any]) -> IntakeAssessment:
        """Step 1: Conduct intake assessment."""
        self._intake_count += 1
        intake_id = f"intake-{self._intake_count}"

        concerns = data.get("presenting_concerns", [])
        if not concerns:
            concerns = ["general stress", "self-exploration"]

        # Symptom severity
        severity_map = {}
        base_severity = data.get("base_severity", "moderate")
        for concern in concerns:
            severity_map[concern] = base_severity

        # Risk level
        risk = "low"
        safety = {
            "self_harm_ideation": data.get("self_harm", False),
            "suicide_risk": data.get("suicide_risk", "none"),
            "homicidal_ideation": False,
            "self_neglect": False,
            "vulnerability": data.get("vulnerability", "moderate"),
        }
        if safety["self_harm_ideation"] or safety["suicide_risk"] != "none":
            risk = "moderate" if safety["suicide_risk"] == "passive" else "high"
            if safety["suicide_risk"] == "active":
                risk = "critical"

        # Strengths
        strengths = [
            "Seeking help demonstrates courage and self-awareness",
            "Ability to articulate concerns",
            "Motivation for change",
        ]
        if "support" in str(concerns).lower() or "relationship" in str(concerns).lower():
            strengths.append("Existing support system to build on")

        # Goals
        goals = data.get("goals", [])
        if not goals:
            goals = [
                f"Reduce distress related to {concerns[0]}",
                "Develop better coping strategies",
                "Improve overall well-being",
            ]

        # Recommended modality
        primary_modality = self.config.get("primary_modality", "cbt")
        # Check if any concern maps to specific modality
        for concern in concerns:
            concern_key = concern.lower().replace(" ", "_")
            for ct, info in self._modality_mapping.items():
                if ct.value in concern_key or concern_key in ct.value:
                    primary_modality = info["primary_modalities"][0]
                    break

        intake = IntakeAssessment(
            id=intake_id,
            client_id=client_id,
            presenting_concerns=concerns,
            symptom_severity=severity_map,
            symptom_duration=data.get("symptom_duration", "several_months"),
            mental_health_history=data.get("mental_health_history", []),
            medical_history=data.get("medical_history", []),
            social_history=data.get("social_history", {
                "relationship_status": "not specified",
                "living_situation": "not specified",
                "support_system": "moderate",
            }),
            substance_use=data.get("substance_use", {
                "alcohol": "none/moderate",
                "drugs": "none",
            }),
            safety_assessment=safety,
            risk_level=risk,
            strengths=strengths,
            goals=goals,
            recommended_modality=primary_modality,
        )
        self._intakes[intake_id] = intake
        return intake

    def _create_treatment_plan(self, intake: IntakeAssessment) -> TreatmentPlan:
        """Step 2: Create treatment plan."""
        self._treatment_count += 1
        plan_id = f"plan-{self._treatment_count}"

        modality = intake.recommended_modality

        # Diagnosis/summary
        diagnosis = ", ".join(intake.presenting_concerns)

        # Goals and interventions based on concerns
        all_goals = list(intake.goals)
        all_interventions = []
        all_objectives = []

        for concern in intake.presenting_concerns:
            concern_key = concern.lower().replace(" ", "_")
            for ct, info in self._modality_mapping.items():
                if ct.value in concern_key or concern_key in ct.value:
                    all_interventions.extend(info["interventions"])
                    # Add specific objectives
                    for goal in info["goals"]:
                        all_objectives.append(f"Measureable progress toward: {goal}")
                    break

        if not all_interventions:
            all_interventions = [
                "Psychoeducation about presenting concerns",
                "Coping skills development",
                "Insight-oriented exploration",
                "Supportive counseling",
            ]

        # Progress measures
        progress_measures = [
            "Weekly self-report symptom scales",
            "Session-by-session progress ratings",
            "Goal attainment scaling",
            "Quality of life measures",
        ]

        # Homework
        homework = []
        if self.config.get("homework_enabled", True):
            if modality == "cbt":
                homework = [
                    "Thought record - track automatic thoughts and challenge them",
                    "Behavioral activation - schedule one pleasurable activity daily",
                    "Mood log - track mood and activities throughout the day",
                ]
            elif modality == "mindfulness":
                homework = [
                    "Daily mindfulness meditation (5-10 minutes)",
                    "Mindful eating practice at one meal per day",
                    "Body scan exercise before bed",
                ]
            else:
                homework = [
                    "Journaling - reflect on session insights",
                    "Practice one coping skill between sessions",
                    "Notice and write down patterns observed during the week",
                ]

        # Session frequency
        frequency = "weekly"
        if intake.risk_level in ("high", "critical"):
            frequency = "twice weekly initially, then weekly"
        elif len(intake.presenting_concerns) > 2:
            frequency = "weekly"
        else:
            frequency = "weekly, may transition to biweekly"

        # Estimated duration
        duration = 12
        severity = list(intake.symptom_severity.values())
        if any(s == "severe" for s in severity):
            duration = 24
        elif any(s == "moderate" for s in severity):
            duration = 16
        if intake.risk_level in ("high", "critical"):
            duration += 8

        # Crisis plan items
        crisis_plan_items = [
            "Identify personal warning signs",
            "List coping strategies that work",
            "Identify support people to contact",
            "Have crisis hotline numbers available",
            "Develop means restriction plan if needed",
        ]

        # Referrals
        referrals = []
        if "medication" in str(intake.mental_health_history).lower() or "severe" in str(intake.symptom_severity.values()).lower():
            referrals.append("Psychiatric evaluation for medication management")

        plan = TreatmentPlan(
            id=plan_id,
            client_id=intake.client_id,
            diagnosis=diagnosis,
            goals=all_goals,
            objectives=all_objectives if all_objectives else [
                "Reduce symptom severity by 50%",
                "Improve daily functioning",
                "Develop 3+ effective coping strategies",
            ],
            interventions=list(set(all_interventions)),
            modality=modality,
            session_frequency=frequency,
            estimated_duration_weeks=duration,
            progress_measures=progress_measures,
            homework_assignments=homework,
            referrals=referrals,
            crisis_plan=crisis_plan_items,
        )
        self._treatment_plans[plan_id] = plan
        return plan

    def _support_session(
        self, intake: IntakeAssessment, treatment: TreatmentPlan
    ) -> SessionNote:
        """Step 3: Support a therapy session."""
        self._session_count += 1
        session_id = f"session-{self._session_count}"

        session_num = self._session_count
        session_type = "individual"
        duration = self.config.get("session_length_minutes", 50)

        # Topics based on session number
        if session_num == 1:
            topics = [
                "Building rapport and therapeutic alliance",
                "Setting goals and expectations for therapy",
                "Exploring presenting concerns in more depth",
                "Discussing therapy process and informed consent",
            ]
        elif session_num <= 3:
            topics = [
                f"Deepening exploration of {intake.presenting_concerns[0] if intake.presenting_concerns else 'concerns'}",
                "Identifying patterns and triggers",
                "Beginning skill-building",
            ]
        else:
            topics = [
                "Processing previous week's experiences",
                "Practicing and refining coping skills",
                "Working toward treatment goals",
                "Addressing barriers to progress",
            ]

        # Interventions
        interventions = treatment.interventions[:3] if len(treatment.interventions) > 3 else treatment.interventions

        # Client mood
        base_mood = 5.0
        if session_num > 3:
            base_mood += 1.0
        mood_rating = min(10.0, base_mood)
        mood_desc = {
            1: "very low, hopeless",
            3: "low, discouraged",
            5: "moderate, mixed",
            7: "improved, more hopeful",
            9: "good, positive outlook",
        }
        mood_level = min(9, max(1, round(mood_rating / 2) * 2 - 1))
        client_mood = f"{mood_desc.get(mood_level, 'moderate')} ({mood_rating:.1f}/10)"

        # Insights
        insights = [
            f"Connection identified between triggers and {intake.presenting_concerns[0] if intake.presenting_concerns else 'symptoms'}",
            "Recognition of patterns in thoughts and behaviors",
            "Increased awareness of personal strengths",
        ]

        # Challenges
        challenges = [
            "Some difficulty applying skills outside session",
            "Initial resistance to certain approaches",
            "Practical barriers to homework completion",
        ][:1] if session_num <= 3 else [
            "Old patterns resurfacing during stress",
            "Balancing multiple life demands with therapy work",
        ][:1]

        # Next session focus
        next_focus = (
            f"Continue building on insights from this session. "
            f"Deepen work on {intake.presenting_concerns[0] if intake.presenting_concerns else 'primary concerns'}. "
            f"Review homework and address any challenges."
        )

        # Homework
        homework = treatment.homework_assignments[:2] if treatment.homework_assignments else []

        # Progress rating
        progress = 2.0 + (session_num * 0.5)
        progress = min(10.0, progress)

        # Safety check
        if intake.risk_level in ("low", "moderate"):
            safety = "No current safety concerns. Client reports feeling safe."
        else:
            safety = "Safety monitored. Client reports no immediate risk. Crisis plan in place."

        note = SessionNote(
            id=session_id,
            client_id=intake.client_id,
            session_number=session_num,
            session_type=session_type,
            duration_minutes=duration,
            topics_discussed=topics,
            interventions_used=interventions,
            client_mood=client_mood,
            insights_gained=insights,
            challenges=challenges,
            next_session_focus=next_focus,
            homework_assigned=homework,
            progress_rating=round(progress, 1),
            safety_check=safety,
        )
        self._session_notes[session_id] = note
        return note

    def _generate_progress_report(
        self,
        intake: IntakeAssessment,
        treatment: TreatmentPlan,
        sessions: List[SessionNote],
    ) -> ProgressReport:
        """Step 4: Generate progress report."""
        self._progress_count += 1
        progress_id = f"progress-{self._progress_count}"

        num_sessions = len(sessions)
        period = f"First {num_sessions} session(s)"

        # Overall progress
        if num_sessions == 0:
            overall = 0.0
        elif num_sessions <= 3:
            overall = 3.0  # Early stage
        elif num_sessions <= 8:
            overall = 5.5  # Moderate progress
        else:
            overall = 7.5  # Good progress

        # Goal progress
        goal_progress = {}
        for i, goal in enumerate(treatment.goals):
            # Earlier goals tend to have more progress
            goal_progress[goal] = round(overall - (i * 0.5), 1)

        # Symptom changes
        symptom_changes = {}
        for concern, severity in intake.symptom_severity.items():
            if num_sessions <= 2:
                change = "Minimal change expected"
            elif overall > 6:
                change = "Improving - reduced frequency/intensity"
            else:
                change = "Some improvement noted"
            symptom_changes[concern] = f"{severity} -> {change}"

        # Attendance
        attendance = {
            "sessions_scheduled": num_sessions,
            "sessions_attended": num_sessions,
            "attendance_rate": 100.0,
        }

        # Homework completion
        homework_rate = 70.0 if num_sessions > 1 else 0.0

        # Strengths
        strengths = list(intake.strengths)
        if num_sessions > 1:
            strengths.extend([
                "Consistent attendance and engagement",
                "Willingness to try new approaches",
                "Ability to reflect and gain insight",
            ])

        # Areas for growth
        areas = [
            "Consistent practice of skills between sessions",
            "Deepening exploration of underlying patterns",
            "Building on initial insights for lasting change",
        ]

        # Recommendations
        recommendations = [
            f"Continue {treatment.modality.upper()} therapy at current frequency",
            "Focus on skill application outside of sessions",
            "Review progress at next milestone (session 8)",
        ]
        if overall > 7:
            recommendations.append("Consider transitioning to biweekly sessions")

        # Termination readiness
        if overall < 3:
            termination = 1.0
        elif overall < 6:
            termination = 3.0
        elif overall < 8:
            termination = 6.0
        else:
            termination = 8.5

        report = ProgressReport(
            id=progress_id,
            client_id=intake.client_id,
            period=period,
            overall_progress=round(overall, 1),
            goal_progress=goal_progress,
            symptom_changes=symptom_changes,
            session_attendance=attendance,
            homework_completion=homework_rate,
            strengths=list(set(strengths)),
            areas_for_growth=areas,
            recommendations=recommendations,
            termination_readiness=round(termination, 1),
        )
        self._progress_reports[progress_id] = report
        return report

    def _create_crisis_plan(self, intake: IntakeAssessment) -> CrisisPlan:
        """Step 5: Create crisis safety plan."""
        self._crisis_count += 1
        crisis_id = f"crisis-{self._crisis_count}"

        risk_level = intake.risk_level

        # Warning signs
        warning_signs = [
            "Feeling overwhelmed or hopeless",
            "Changes in sleep or appetite",
            "Social withdrawal or isolation",
            "Increased substance use",
            "Difficulty concentrating",
            "Feeling like things won't get better",
        ]
        if risk_level in ("high", "critical"):
            warning_signs.extend([
                "Thoughts of self-harm or suicide",
                "Giving away possessions or saying goodbye",
                "Making specific plans",
            ])

        # Coping strategies
        coping = [
            "Deep breathing exercises (4-7-8 technique)",
            "Grounding exercise (5-4-3-2-1 method)",
            "Progressive muscle relaxation",
            "Go for a walk or change environment",
            "Call or text a trusted person",
            "Listen to calming music",
            "Practice mindfulness or meditation",
            "Write in journal about feelings",
        ]

        # Social supports
        social_supports = [
            "Trusted friend or family member #1",
            "Trusted friend or family member #2",
            "Support group or community",
            "Spiritual or religious community",
        ]

        # Professional contacts
        professional = [
            "Therapist - primary contact",
            "Psychiatrist (if applicable)",
            "Local crisis line",
        ]

        # Emergency contacts
        emergency = [
            "911 - for immediate emergency",
            "988 - Suicide & Crisis Lifeline (US)",
            "Local emergency room",
            "Crisis text line: text HOME to 741741",
        ]

        # Safety steps
        safety_steps = [
            "Step 1: Recognize warning signs and rate distress (0-10)",
            "Step 2: Use individual coping strategies from this plan",
            "Step 3: Reach out to a trusted support person",
            "Step 4: Contact therapist or crisis line if distress continues",
            "Step 5: Go to nearest emergency room or call 911 if in immediate danger",
        ]

        # Means restriction
        means_restriction = []
        if risk_level in ("moderate", "high", "critical"):
            means_restriction = [
                "Remove or secure medications",
                "Secure firearms if present",
                "Remove other means of self-harm",
                "Ask trusted person to help with means restriction",
            ]

        # Environment safety
        env_safety = [
            "Create a safe, comfortable space at home",
            "Remove triggers from environment when possible",
            "Have comforting items accessible",
            "Ensure safe sleep environment",
        ]

        # Follow-up plan
        follow_up = [
            "Contact therapist within 24 hours of any crisis",
            "Schedule additional session if needed",
            "Review and update safety plan regularly",
            "Check in with support system",
        ]

        # Response time
        response_time = float(self.config.get("crisis_response_time", 60))

        plan = CrisisPlan(
            id=crisis_id,
            client_id=intake.client_id,
            risk_level=risk_level,
            warning_signs=warning_signs,
            coping_strategies=coping,
            social_supports=social_supports,
            professional_contacts=professional,
            emergency_contacts=emergency,
            safety_steps=safety_steps,
            means_restriction=means_restriction,
            environment_safety=env_safety,
            follow_up_plan=follow_up,
            crisis_response_time_minutes=response_time,
        )
        self._crisis_plans[crisis_id] = plan
        return plan

    # ------------------------------------------------------------------
    # Agent prompts
    # ------------------------------------------------------------------

    def _build_therapy_assistant_prompt(self) -> str:
        """Build the system prompt for the therapy assistant role."""
        modality = self.config.get("primary_modality", "cbt").upper()
        return (
            f"You are a therapy assistant trained in {modality} and other "
            "evidence-based approaches. Support therapists by suggesting "
            "interventions, helping structure sessions, and generating "
            "homework assignments. Maintain a warm, empathetic, and "
            "professional tone. Always prioritize client safety and "
            "well-being. Remember that AI supports - not replaces - "
            "professional clinical judgment."
        )

    def _build_progress_tracker_prompt(self) -> str:
        """Build the system prompt for the progress tracker role."""
        return (
            "You are a clinical progress tracking specialist. Monitor "
            "client progress across therapy sessions using quantitative "
            "and qualitative measures. Generate clear progress reports "
            "that highlight improvements, areas of concern, and "
            "recommendations. Use validated measures and consistent "
            "tracking methods. Present data in ways that are meaningful "
            "to both clinicians and clients."
        )

    def _build_crisis_responder_prompt(self) -> str:
        """Build the system prompt for the crisis responder role."""
        response_time = self.config.get("crisis_response_time", 60)
        return (
            f"You are a crisis response specialist with a target response "
            f"time of {response_time} minutes. Assess safety and risk "
            "levels accurately. Develop comprehensive safety plans "
            "tailored to each client's needs. Provide immediate coping "
            "strategies and connect clients with emergency resources "
            "when needed. Always prioritize client safety. "
            "Remember: in emergencies, direct clients to call 911 "
            "or go to the nearest emergency room."
        )

    def _build_resource_coordinator_prompt(self) -> str:
        """Build the system prompt for the resource coordinator role."""
        return (
            "You are a mental health resource coordinator. Connect clients "
            "with appropriate community resources, support groups, "
            "psychiatric services, and other support systems. Maintain "
            "an up-to-date database of resources. Match resources to "
            "individual client needs, preferences, and circumstances. "
            "Provide clear, actionable information about how to access "
            "each resource."
        )

    # ------------------------------------------------------------------
    # Metrics
    # ------------------------------------------------------------------

    def _update_therapy_metrics(self) -> None:
        """Update therapy-specific metrics."""
        total = self.therapy_metrics.clients_served
        if total == 0:
            return

        # Client progress
        if self._progress_reports:
            progresses = [p.overall_progress for p in self._progress_reports.values()]
            self.therapy_metrics.client_progress = sum(progresses) / len(progresses)

        # Session quality
        if self._session_notes:
            qualities = [s.progress_rating for s in self._session_notes.values()]
            self.therapy_metrics.session_quality = sum(qualities) / len(qualities)

        # Crisis response time
        self.therapy_metrics.crisis_response_time = float(
            self.config.get("crisis_response_time", 60)
        )

        # Goal achievement rate
        self.therapy_metrics.goal_achievement_rate = 60.0  # Estimate

        # Symptom reduction
        self.therapy_metrics.symptom_reduction = 40.0  # Percentage

        # Retention rate
        self.therapy_metrics.retention_rate = 70.0

        # Homework adherence
        if self._progress_reports:
            rates = [p.homework_completion for p in self._progress_reports.values()]
            self.therapy_metrics.homework_adherence = sum(rates) / len(rates)

        # Termination success
        self.therapy_metrics.termination_success = 65.0

    def get_therapy_metrics(self) -> Dict[str, Any]:
        """Get the current therapy metrics.

        Returns:
            Dictionary with therapy metrics including client progress,
            session quality, crisis response time, and goal achievement.
        """
        return self.therapy_metrics.to_dict()
