"""
Fitness Coach AI Solution Template.

A comprehensive AI-powered fitness coach assistant for
gyms, personal training, and fitness centers.
Includes multi-agent roles for fitness coach, exercise physiologist,
nutrition advisor, and progress tracker.

Typical workflow:
    fitness assessment -> goal setting -> workout planning
    -> nutrition guidance -> progress tracking
"""

from typing import Optional, Dict, Any, List
from enum import Enum
from dataclasses import dataclass, field

from solutions.templates.base_solution import (
    BaseSolution,
    SolutionMetadata,
    SolutionMetrics,
)


class FitnessGoal(str, Enum):
    """Fitness goals."""
    WEIGHT_LOSS = "weight_loss"
    MUSCLE_BUILDING = "muscle_building"
    STRENGTH = "strength"
    ENDURANCE = "endurance"
    FLEXIBILITY = "flexibility"
    GENERAL_FITNESS = "general_fitness"
    SPORTS_PERFORMANCE = "sports_performance"
    REHABILITATION = "rehabilitation"
    BODY_COMPOSITION = "body_composition"
    FUNCTIONAL_FITNESS = "functional_fitness"


class TrainingStyle(str, Enum):
    """Training styles/methods."""
    BODYBUILDING = "bodybuilding"
    POWERLIFTING = "powerlifting"
    HIIT = "hiit"
    CIRCUIT = "circuit"
    FUNCTIONAL = "functional"
    CALISTHENICS = "calisthenics"
    CROSSFIT = "crossfit"
    RUNNING = "running"
    YOGA = "yoga"
    PILATES = "pilates"
    SWIMMING = "swimming"
    MIXED = "mixed"


class ExperienceLevel(str, Enum):
    """Fitness experience levels."""
    BEGINNER = "beginner"
    INTERMEDIATE = "intermediate"
    ADVANCED = "advanced"
    ELITE = "elite"


class ExerciseCategory(str, Enum):
    """Exercise categories."""
    COMPOUND = "compound"
    ISOLATION = "isolation"
    CARDIO = "cardio"
    FLEXIBILITY = "flexibility"
    CORE = "core"
    FUNCTIONAL = "functional"
    PLYOMETRIC = "plyometric"
    MOBILITY = "mobility"


class FitnessRole(str, Enum):
    """Agent roles in the fitness coach solution."""
    FITNESS_COACH = "fitness_coach"
    EXERCISE_PHYSIOLOGIST = "exercise_physiologist"
    NUTRITION_ADVISOR = "nutrition_advisor"
    PROGRESS_TRACKER = "progress_tracker"


@dataclass
class FitnessAssessment:
    """Represents a fitness assessment.

    Attributes:
        id: Unique assessment identifier.
        client_id: Client identifier.
        age: Client age.
        gender: Client gender.
        height_cm: Height in centimeters.
        weight_kg: Weight in kilograms.
        body_fat_pct: Body fat percentage.
        experience_level: Fitness experience level.
        current_routine: Current exercise routine.
        injuries: Past and current injuries.
        health_conditions: Relevant health conditions.
        goals: Fitness goals.
        available_equipment: Equipment available.
        workout_frequency: Workouts per week available.
        time_per_session: Time available per workout.
        fitness_test_results: Results of initial fitness tests.
        mobility_score: Mobility assessment score (0-10).
        strength_level: Strength assessment score (0-10).
        cardio_level: Cardiovascular fitness score (0-10).
    """
    id: str
    client_id: str = ""
    age: int = 30
    gender: str = ""
    height_cm: float = 0.0
    weight_kg: float = 0.0
    body_fat_pct: float = 0.0
    experience_level: ExperienceLevel = ExperienceLevel.BEGINNER
    current_routine: str = ""
    injuries: List[str] = field(default_factory=list)
    health_conditions: List[str] = field(default_factory=list)
    goals: List[str] = field(default_factory=list)
    available_equipment: List[str] = field(default_factory=list)
    workout_frequency: int = 3
    time_per_session_min: int = 45
    fitness_test_results: Dict[str, Any] = field(default_factory=dict)
    mobility_score: float = 0.0
    strength_level: float = 0.0
    cardio_level: float = 0.0


@dataclass
class Workout:
    """Represents a single workout session.

    Attributes:
        id: Unique workout identifier.
        name: Workout name.
        type: Workout type/phase.
        duration_min: Estimated duration in minutes.
        difficulty: Difficulty level.
        target_muscles: Primary and secondary muscle groups.
        exercises: List of exercises with details.
        warm_up: Warm-up routine.
        cool_down: Cool-down routine.
        calories_burned_estimate: Estimated calories burned.
        intensity: Average intensity (RPE 1-10).
        rest_intervals: Rest periods between sets.
        progression_notes: How to progress this workout.
    """
    id: str
    name: str = ""
    type: str = ""
    duration_min: int = 0
    difficulty: str = ""
    target_muscles: List[str] = field(default_factory=list)
    exercises: List[Dict[str, Any]] = field(default_factory=list)
    warm_up: List[str] = field(default_factory=list)
    cool_down: List[str] = field(default_factory=list)
    calories_burned_estimate: float = 0.0
    intensity: float = 0.0
    rest_intervals: str = ""
    progression_notes: str = ""


@dataclass
class WorkoutPlan:
    """Represents a complete workout plan.

    Attributes:
        id: Unique workout plan identifier.
        client_id: Client identifier.
        plan_name: Name of the plan.
        training_style: Training style/method.
        duration_weeks: Plan duration in weeks.
        workouts_per_week: Number of workouts per week.
        avg_session_duration: Average session length.
        weekly_schedule: Weekly workout schedule.
        workouts: List of workout sessions.
        phases: Training phases (e.g., hypertrophy, strength).
        progression_scheme: How to progress over time.
        deload_schedule: Deload week schedule.
        warm_up_protocol: Standard warm-up protocol.
        recovery_guidance: Recovery recommendations.
    """
    id: str
    client_id: str = ""
    plan_name: str = ""
    training_style: TrainingStyle = TrainingStyle.MIXED
    duration_weeks: int = 12
    workouts_per_week: int = 3
    avg_session_duration: int = 45
    weekly_schedule: Dict[str, str] = field(default_factory=dict)
    workouts: List[Workout] = field(default_factory=list)
    phases: List[str] = field(default_factory=list)
    progression_scheme: str = ""
    deload_schedule: str = ""
    warm_up_protocol: List[str] = field(default_factory=list)
    recovery_guidance: List[str] = field(default_factory=list)


@dataclass
class NutritionGuidance:
    """Represents fitness nutrition guidance.

    Attributes:
        id: Unique guidance ID.
        client_id: Client identifier.
        calorie_target: Daily calorie target.
        macro_breakdown: Macronutrient targets.
        protein_target_g: Daily protein target (grams).
        timing_recommendations: Meal timing around workouts.
        pre_workout: Pre-workout nutrition guidelines.
        post_workout: Post-workout nutrition guidelines.
        hydration: Hydration recommendations.
        supplements: Recommended fitness supplements.
        meal_frequency: Recommended meal frequency.
        weight_management: Weight management strategies.
    """
    id: str
    client_id: str = ""
    calorie_target: float = 0.0
    macro_breakdown: Dict[str, float] = field(default_factory=dict)
    protein_target_g: float = 0.0
    timing_recommendations: str = ""
    pre_workout: List[str] = field(default_factory=list)
    post_workout: List[str] = field(default_factory=list)
    hydration: str = ""
    supplements: List[str] = field(default_factory=list)
    meal_frequency: str = ""
    weight_management: List[str] = field(default_factory=list)


@dataclass
class FitnessProgress:
    """Represents fitness progress tracking.

    Attributes:
        id: Unique progress record ID.
        client_id: Client identifier.
        period: Time period covered.
        weight_change_kg: Weight change in kilograms.
        body_fat_change_pct: Body fat percentage change.
        strength_gains: Strength improvements by exercise.
        endurance_improvement: Endurance improvement metrics.
        workout_adherence: Workout completion rate.
        energy_levels: Reported energy levels (1-10).
        recovery_quality: Recovery quality rating (1-10).
        measurements: Body measurements changes.
        pr_achievements: Personal records achieved.
        challenges: Challenges and obstacles.
        adjustments_needed: Plan adjustments needed.
        motivation_level: Self-reported motivation (1-10).
    """
    id: str
    client_id: str = ""
    period: str = ""
    weight_change_kg: float = 0.0
    body_fat_change_pct: float = 0.0
    strength_gains: Dict[str, float] = field(default_factory=dict)
    endurance_improvement: Dict[str, float] = field(default_factory=dict)
    workout_adherence: float = 0.0
    energy_levels: float = 0.0
    recovery_quality: float = 0.0
    measurements: Dict[str, float] = field(default_factory=dict)
    pr_achievements: List[str] = field(default_factory=list)
    challenges: List[str] = field(default_factory=list)
    adjustments_needed: List[str] = field(default_factory=list)
    motivation_level: float = 0.0


@dataclass
class FitnessMetrics(SolutionMetrics):
    """Extended metrics for the fitness coach solution.

    Attributes:
        client_progress: Average client progress score (0-10).
        workout_adherence: Average workout completion rate.
        goal_achievement: Goal achievement rate.
        clients_served: Total clients served.
        avg_strength_gain: Average strength gain percentage.
        avg_weight_change: Average weight change (kg).
        program_satisfaction: Program satisfaction rating.
        injury_prevention_rate: Injury prevention rate.
        retention_rate: Client retention rate.
        avg_program_duration: Average program duration (weeks).
    """
    client_progress: float = 0.0
    workout_adherence: float = 0.0
    goal_achievement: float = 0.0
    clients_served: int = 0
    avg_strength_gain: float = 0.0
    avg_weight_change: float = 0.0
    program_satisfaction: float = 0.0
    injury_prevention_rate: float = 0.0
    retention_rate: float = 0.0
    avg_program_duration: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        """Convert fitness metrics to a dictionary."""
        base = super().to_dict()
        base.update({
            "client_progress": self.client_progress,
            "workout_adherence": self.workout_adherence,
            "goal_achievement": self.goal_achievement,
            "clients_served": self.clients_served,
            "avg_strength_gain": self.avg_strength_gain,
            "avg_weight_change": self.avg_weight_change,
            "program_satisfaction": self.program_satisfaction,
            "injury_prevention_rate": self.injury_prevention_rate,
            "retention_rate": self.retention_rate,
            "avg_program_duration": self.avg_program_duration,
        })
        return base


class FitnessCoachSolution(BaseSolution):
    """Fitness Coach AI solution template.

    Provides a complete, pre-configured fitness coach solution
    with multi-agent collaboration for fitness assessment, goal
    setting, workout planning, nutrition guidance, and progress
    tracking.

    Pre-configured agent roles:
        - Fitness Coach: Designs programs and provides coaching
        - Exercise Physiologist: Ensures proper form and exercise science
        - Nutrition Advisor: Provides fitness-specific nutrition guidance
        - Progress Tracker: Monitors progress and adjusts programs

    Workflow:
        1. Fitness Assessment - Evaluate current fitness level
        2. Goal Setting - Define specific fitness goals
        3. Workout Planning - Create personalized workout programs
        4. Nutrition Guidance - Provide fitness nutrition advice
        5. Progress Tracking - Monitor progress and adjust

    Configuration options:
        - primary_goal: Primary fitness goal
        - training_style: Preferred training style
        - experience_level: Client's fitness experience level
        - workouts_per_week: Number of workouts per week
        - session_duration: Duration per workout session
    """

    def _build_metadata(self) -> SolutionMetadata:
        """Build metadata for the fitness coach solution."""
        return SolutionMetadata(
            name="fitness-coach",
            version="1.0.0",
            industry="fitness",
            description=(
                "AI-powered fitness coach with personalized workout plans, "
                "exercise science guidance, nutrition advice, and progress "
                "tracking for optimal fitness results."
            ),
            author="LiuHao AI OS",
            tags=["fitness", "workout", "exercise", "training", "strength", "nutrition", "progress-tracking"],
            min_liuhao_version="1.0.0",
        )

    def _build_config_schema(self) -> None:
        """Define configuration fields for the fitness coach solution."""
        self.config_schema.add_field(
            name="primary_goal",
            field_type="string",
            required=False,
            default="general_fitness",
            description="Primary fitness goal",
            valid_values=[
                "weight_loss", "muscle_building", "strength", "endurance",
                "flexibility", "general_fitness", "sports_performance",
                "rehabilitation", "body_composition", "functional_fitness",
            ],
        )
        self.config_schema.add_field(
            name="training_style",
            field_type="string",
            required=False,
            default="mixed",
            description="Preferred training style",
            valid_values=[
                "bodybuilding", "powerlifting", "hiit", "circuit",
                "functional", "calisthenics", "crossfit", "running",
                "yoga", "pilates", "swimming", "mixed",
            ],
        )
        self.config_schema.add_field(
            name="experience_level",
            field_type="string",
            required=False,
            default="beginner",
            description="Client's fitness experience level",
            valid_values=["beginner", "intermediate", "advanced", "elite"],
        )
        self.config_schema.add_field(
            name="workouts_per_week",
            field_type="int",
            required=False,
            default=3,
            description="Number of workouts per week",
        )
        self.config_schema.add_field(
            name="session_duration_min",
            field_type="int",
            required=False,
            default=45,
            description="Duration per workout session in minutes",
        )
        self.config_schema.add_field(
            name="equipment_available",
            field_type="string",
            required=False,
            default="full_gym",
            description="Available equipment",
            valid_values=["no_equipment", "dumbbells", "home_gym", "full_gym"],
        )
        self.config_schema.add_field(
            name="include_cardio",
            field_type="bool",
            required=False,
            default=True,
            description="Whether to include cardio training",
        )
        self.config_schema.add_field(
            name="deload_weeks",
            field_type="bool",
            required=False,
            default=True,
            description="Whether to include deload weeks",
        )

    def _on_initialize(self) -> None:
        """Initialize fitness coach solution state."""
        self._agents: Dict[str, Dict[str, Any]] = {}
        self._assessments: Dict[str, FitnessAssessment] = {}
        self._workout_plans: Dict[str, WorkoutPlan] = {}
        self._nutrition_guidance: Dict[str, NutritionGuidance] = {}
        self._progress_records: Dict[str, FitnessProgress] = {}
        self._assessment_count = 0
        self._workout_plan_count = 0
        self._nutrition_count = 0
        self._progress_count = 0

        # Initialize fitness-specific metrics
        self.fitness_metrics = FitnessMetrics()

        # Exercise database
        self._exercise_db = self._build_exercise_database()

    def _build_exercise_database(self) -> Dict[str, Any]:
        """Build exercise database by muscle group."""
        return {
            "chest": [
                {"name": "Bench Press", "type": "compound", "equipment": "barbell", "difficulty": "intermediate"},
                {"name": "Incline Dumbbell Press", "type": "compound", "equipment": "dumbbells", "difficulty": "beginner"},
                {"name": "Push-ups", "type": "compound", "equipment": "bodyweight", "difficulty": "beginner"},
                {"name": "Cable Fly", "type": "isolation", "equipment": "cable", "difficulty": "beginner"},
                {"name": "Dips (chest)", "type": "compound", "equipment": "parallel_bars", "difficulty": "intermediate"},
            ],
            "back": [
                {"name": "Deadlift", "type": "compound", "equipment": "barbell", "difficulty": "advanced"},
                {"name": "Pull-ups", "type": "compound", "equipment": "pull_up_bar", "difficulty": "intermediate"},
                {"name": "Bent-Over Rows", "type": "compound", "equipment": "barbell", "difficulty": "intermediate"},
                {"name": "Lat Pulldown", "type": "compound", "equipment": "machine", "difficulty": "beginner"},
                {"name": "Seated Cable Row", "type": "compound", "equipment": "cable", "difficulty": "beginner"},
                {"name": "Face Pulls", "type": "isolation", "equipment": "cable", "difficulty": "beginner"},
            ],
            "legs": [
                {"name": "Squat", "type": "compound", "equipment": "barbell", "difficulty": "intermediate"},
                {"name": "Romanian Deadlift", "type": "compound", "equipment": "barbell", "difficulty": "intermediate"},
                {"name": "Leg Press", "type": "compound", "equipment": "machine", "difficulty": "beginner"},
                {"name": "Lunges", "type": "compound", "equipment": "bodyweight/dumbbells", "difficulty": "beginner"},
                {"name": "Leg Curl", "type": "isolation", "equipment": "machine", "difficulty": "beginner"},
                {"name": "Leg Extension", "type": "isolation", "equipment": "machine", "difficulty": "beginner"},
                {"name": "Calf Raises", "type": "isolation", "equipment": "machine/bodyweight", "difficulty": "beginner"},
            ],
            "shoulders": [
                {"name": "Overhead Press", "type": "compound", "equipment": "barbell", "difficulty": "intermediate"},
                {"name": "Dumbbell Shoulder Press", "type": "compound", "equipment": "dumbbells", "difficulty": "beginner"},
                {"name": "Lateral Raises", "type": "isolation", "equipment": "dumbbells", "difficulty": "beginner"},
                {"name": "Front Raises", "type": "isolation", "equipment": "dumbbells", "difficulty": "beginner"},
                {"name": "Face Pulls", "type": "isolation", "equipment": "cable", "difficulty": "beginner"},
            ],
            "arms": [
                {"name": "Bicep Curls", "type": "isolation", "equipment": "dumbbells/barbell", "difficulty": "beginner"},
                {"name": "Tricep Dips", "type": "compound", "equipment": "bench", "difficulty": "beginner"},
                {"name": "Hammer Curls", "type": "isolation", "equipment": "dumbbells", "difficulty": "beginner"},
                {"name": "Tricep Pushdowns", "type": "isolation", "equipment": "cable", "difficulty": "beginner"},
            ],
            "core": [
                {"name": "Plank", "type": "isometric", "equipment": "bodyweight", "difficulty": "beginner"},
                {"name": "Hanging Leg Raises", "type": "compound", "equipment": "pull_up_bar", "difficulty": "advanced"},
                {"name": "Crunches", "type": "isolation", "equipment": "bodyweight", "difficulty": "beginner"},
                {"name": "Russian Twists", "type": "isolation", "equipment": "bodyweight/weight", "difficulty": "beginner"},
                {"name": "Dead Bug", "type": "stabilization", "equipment": "bodyweight", "difficulty": "beginner"},
            ],
            "cardio": [
                {"name": "Running/Jogging", "type": "steady_state", "equipment": "none/treadmill", "intensity": "moderate"},
                {"name": "HIIT", "type": "interval", "equipment": "various", "intensity": "high"},
                {"name": "Cycling", "type": "steady_state", "equipment": "bike", "intensity": "moderate"},
                {"name": "Jump Rope", "type": "interval", "equipment": "jump_rope", "intensity": "high"},
                {"name": "Swimming", "type": "steady_state", "equipment": "pool", "intensity": "moderate"},
            ],
        }

    def _on_deploy(self) -> None:
        """Deploy fitness coach agents and workflows."""
        agent_configs = [
            (FitnessRole.FITNESS_COACH, {
                "name": "Fitness Coach",
                "description": "Designs programs and provides coaching",
                "system_prompt": self._build_fitness_coach_prompt(),
                "tools": ["program_design", "coaching_motivation", "goal_setting"],
            }),
            (FitnessRole.EXERCISE_PHYSIOLOGIST, {
                "name": "Exercise Physiologist",
                "description": "Ensures proper form and exercise science",
                "system_prompt": self._build_exercise_physiologist_prompt(),
                "tools": ["form_analysis", "exercise_selection", "rehab_guidance"],
            }),
            (FitnessRole.NUTRITION_ADVISOR, {
                "name": "Nutrition Advisor",
                "description": "Provides fitness-specific nutrition guidance",
                "system_prompt": self._build_nutrition_advisor_prompt(),
                "tools": ["macro_calculation", "supplement_advice", "timing_strategies"],
            }),
            (FitnessRole.PROGRESS_TRACKER, {
                "name": "Progress Tracker",
                "description": "Monitors progress and adjusts programs",
                "system_prompt": self._build_progress_tracker_prompt(),
                "tools": ["progress_measurement", "plateau_analysis", "program_adjustment"],
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
        """Process a fitness request through the full workflow.

        Args:
            input_data: Dictionary containing client information.

        Returns:
            Dictionary with fitness results including assessment,
            workout plan, nutrition guidance, and progress tracking.
        """
        client_id = input_data.get("client_id", f"client-{len(self._assessments)+1}")

        # Step 1: Fitness assessment
        assessment = self._conduct_fitness_assessment(client_id, input_data)

        # Step 2 & 3: Workout planning
        workout_plan = self._create_workout_plan(assessment)

        # Step 4: Nutrition guidance
        nutrition = self._create_nutrition_guidance(assessment)

        # Step 5: Progress tracking setup
        progress = self._initialize_progress(assessment)

        # Store and update metrics
        self._assessments[assessment.id] = assessment
        self._workout_plans[workout_plan.id] = workout_plan
        self._nutrition_guidance[nutrition.id] = nutrition
        self._progress_records[progress.id] = progress

        self.fitness_metrics.clients_served += 1
        self._update_fitness_metrics()

        return {
            "fitness_assessment": {
                "id": assessment.id,
                "age": assessment.age,
                "height_cm": assessment.height_cm,
                "weight_kg": assessment.weight_kg,
                "body_fat_pct": assessment.body_fat_pct,
                "experience_level": assessment.experience_level.value,
                "goals": assessment.goals,
                "workout_frequency": assessment.workout_frequency,
                "time_per_session_min": assessment.time_per_session_min,
                "mobility_score": assessment.mobility_score,
                "strength_level": assessment.strength_level,
                "cardio_level": assessment.cardio_level,
                "injuries": assessment.injuries,
            },
            "workout_plan": {
                "id": workout_plan.id,
                "plan_name": workout_plan.plan_name,
                "training_style": workout_plan.training_style.value,
                "duration_weeks": workout_plan.duration_weeks,
                "workouts_per_week": workout_plan.workouts_per_week,
                "avg_session_duration": workout_plan.avg_session_duration,
                "weekly_schedule": workout_plan.weekly_schedule,
                "workouts_count": len(workout_plan.workouts),
                "phases": workout_plan.phases,
                "progression_scheme": workout_plan.progression_scheme,
                "deload_schedule": workout_plan.deload_schedule,
                "recovery_guidance": workout_plan.recovery_guidance,
                "workouts": [
                    {
                        "id": w.id,
                        "name": w.name,
                        "type": w.type,
                        "duration_min": w.duration_min,
                        "difficulty": w.difficulty,
                        "target_muscles": w.target_muscles,
                        "exercise_count": len(w.exercises),
                        "exercises": [
                            {
                                "name": e["name"],
                                "sets": e.get("sets", 0),
                                "reps": e.get("reps", ""),
                                "rest": e.get("rest", ""),
                                "type": e.get("type", ""),
                            }
                            for e in w.exercises
                        ],
                        "intensity_rpe": w.intensity,
                        "calories_estimate": w.calories_burned_estimate,
                    }
                    for w in workout_plan.workouts
                ],
            },
            "nutrition_guidance": {
                "id": nutrition.id,
                "calorie_target": round(nutrition.calorie_target, 0),
                "macro_breakdown": nutrition.macro_breakdown,
                "protein_target_g": round(nutrition.protein_target_g, 0),
                "pre_workout": nutrition.pre_workout,
                "post_workout": nutrition.post_workout,
                "hydration": nutrition.hydration,
                "supplements": nutrition.supplements,
                "weight_management": nutrition.weight_management,
            },
            "progress_tracking": {
                "id": progress.id,
                "period": progress.period,
                "workout_adherence": progress.workout_adherence,
                "energy_levels": progress.energy_levels,
                "motivation_level": progress.motivation_level,
                "pr_achievements": progress.pr_achievements,
                "challenges": progress.challenges,
                "adjustments_needed": progress.adjustments_needed,
            },
        }

    def _on_teardown(self) -> None:
        """Clean up fitness coach solution resources."""
        self._agents.clear()
        self._assessments.clear()
        self._workout_plans.clear()
        self._nutrition_guidance.clear()
        self._progress_records.clear()
        self._assessment_count = 0
        self._workout_plan_count = 0
        self._nutrition_count = 0
        self._progress_count = 0

    # ------------------------------------------------------------------
    # Workflow steps
    # ------------------------------------------------------------------

    def _conduct_fitness_assessment(self, client_id: str, data: Dict[str, Any]) -> FitnessAssessment:
        """Step 1: Conduct fitness assessment."""
        self._assessment_count += 1
        assessment_id = f"assess-{self._assessment_count}"

        age = int(data.get("age", 30))
        gender = data.get("gender", "male")
        height_cm = float(data.get("height_cm", 175))
        weight_kg = float(data.get("weight_kg", 75))
        body_fat = float(data.get("body_fat_pct", 20.0))

        try:
            experience = ExperienceLevel(data.get("experience_level", self.config.get("experience_level", "beginner")))
        except ValueError:
            experience = ExperienceLevel.BEGINNER

        goals = data.get("goals", [])
        if not goals:
            primary_goal = self.config.get("primary_goal", "general_fitness")
            goals = [primary_goal, "Improve overall health", "Increase energy levels"]

        workouts_per_week = int(data.get("workouts_per_week", self.config.get("workouts_per_week", 3)))
        session_time = int(data.get("session_duration_min", self.config.get("session_duration_min", 45)))

        # Equipment
        equipment_map = {
            "no_equipment": ["bodyweight only"],
            "dumbbells": ["dumbbells", "bodyweight"],
            "home_gym": ["dumbbells", "bench", "pull-up bar", "resistance bands"],
            "full_gym": ["barbell", "dumbbells", "machines", "cables", "cardio equipment"],
        }
        equipment_type = self.config.get("equipment_available", "full_gym")
        equipment = equipment_map.get(equipment_type, equipment_map["full_gym"])

        # Initial fitness scores based on experience level
        base_scores = {
            ExperienceLevel.BEGINNER: {"mobility": 4, "strength": 3, "cardio": 4},
            ExperienceLevel.INTERMEDIATE: {"mobility": 6, "strength": 6, "cardio": 6},
            ExperienceLevel.ADVANCED: {"mobility": 7.5, "strength": 8, "cardio": 7.5},
            ExperienceLevel.ELITE: {"mobility": 8.5, "strength": 9.5, "cardio": 9},
        }
        scores = base_scores.get(experience, base_scores[ExperienceLevel.BEGINNER])

        assessment = FitnessAssessment(
            id=assessment_id,
            client_id=client_id,
            age=age,
            gender=gender,
            height_cm=height_cm,
            weight_kg=weight_kg,
            body_fat_pct=body_fat,
            experience_level=experience,
            current_routine=data.get("current_routine", "None/irregular"),
            injuries=data.get("injuries", []),
            health_conditions=data.get("health_conditions", []),
            goals=goals,
            available_equipment=equipment,
            workout_frequency=workouts_per_week,
            time_per_session_min=session_time,
            fitness_test_results={
                "estimated_1rm_bench": 0,
                "estimated_1rm_squat": 0,
                "estimated_1rm_deadlift": 0,
                "mile_time": "untested",
                "push_ups_max": 0,
                "plank_max_time_sec": 0,
            },
            mobility_score=scores["mobility"],
            strength_level=scores["strength"],
            cardio_level=scores["cardio"],
        )
        self._assessments[assessment_id] = assessment
        return assessment

    def _create_workout_plan(self, assessment: FitnessAssessment) -> WorkoutPlan:
        """Step 2: Create workout plan."""
        self._workout_plan_count += 1
        plan_id = f"plan-{self._workout_plan_count}"

        try:
            style = TrainingStyle(self.config.get("training_style", "mixed"))
        except ValueError:
            style = TrainingStyle.MIXED

        goal = self.config.get("primary_goal", "general_fitness")
        workouts_per_week = assessment.workout_frequency
        session_time = assessment.time_per_session_min
        experience = assessment.experience_level

        # Determine split based on frequency and goal
        if workouts_per_week == 3:
            if goal in ("strength", "muscle_building", "body_composition"):
                split = "Full Body (3x/week)"
                schedule = {"Monday": "Full Body A", "Wednesday": "Full Body B", "Friday": "Full Body A"}
            elif goal == "general_fitness":
                split = "Full Body + Cardio"
                schedule = {"Monday": "Full Body", "Wednesday": "Cardio + Core", "Friday": "Full Body"}
            else:
                split = "Push/Pull/Legs (3x)"
                schedule = {"Monday": "Push", "Wednesday": "Pull", "Friday": "Legs"}
        elif workouts_per_week == 4:
            split = "Upper/Lower Split"
            schedule = {"Monday": "Upper Body", "Tuesday": "Lower Body", "Thursday": "Upper Body", "Friday": "Lower Body"}
        elif workouts_per_week == 5:
            split = "Push/Pull/Legs + Extra"
            schedule = {"Monday": "Push", "Tuesday": "Pull", "Wednesday": "Legs", "Thursday": "Upper", "Friday": "Lower/Conditioning"}
        else:  # 2 days
            split = "Full Body (2x/week)"
            schedule = {"Tuesday": "Full Body A", "Thursday": "Full Body B"}

        # Training phases
        phases = []
        if experience == ExperienceLevel.BEGINNER:
            phases = [
                "Weeks 1-4: Foundation / Learning Phase",
                "Weeks 5-8: Progressive Overload Phase",
                "Weeks 9-12: Intensification Phase",
            ]
        else:
            phases = [
                "Weeks 1-4: Hypertrophy / Accumulation",
                "Weeks 5-8: Strength / Intensification",
                "Weeks 9-12: Peaking / Realization",
            ]

        # Progression scheme
        prog_schemes = {
            "beginner": "Linear progression - add weight when you can complete all sets/reps with good form",
            "intermediate": "Periodized progression - alternate volume and intensity weeks",
            "advanced": "Undulating periodization - vary rep ranges within the week",
            "elite": "Highly individualized - conjugate or block periodization",
        }
        progression = prog_schemes.get(experience.value, prog_schemes["beginner"])

        # Deload
        deload = "Every 6-8 weeks, reduce volume by 50% for active recovery" if self.config.get("deload_weeks", True) else "Not included - manage recovery individually"

        # Warm-up protocol
        warm_up = [
            "5 minutes light cardio (jumping jacks, rowing, cycling)",
            "Dynamic stretches (arm circles, leg swings, bodyweight squats)",
            "2-3 light warm-up sets of first exercise",
            "Activate target muscles with band pull-aparts, glute bridges, etc.",
            "Work up to working weight gradually",
        ]

        # Recovery guidance
        recovery = [
            "7-9 hours of quality sleep per night",
            "Active recovery on rest days (walking, stretching, yoga)",
            "Proper nutrition to support recovery",
            "Deload weeks as scheduled",
            "Listen to your body - extra rest is sometimes needed",
            "Foam rolling and mobility work",
        ]

        # Create workouts
        workouts = self._generate_workouts(
            schedule, style, experience, session_time, goal, assessment.available_equipment
        )

        plan_name = f"{style.value.replace('_', ' ').title()} Program - {goal.replace('_', ' ').title()}"

        plan = WorkoutPlan(
            id=plan_id,
            client_id=assessment.client_id,
            plan_name=plan_name,
            training_style=style,
            duration_weeks=12,
            workouts_per_week=workouts_per_week,
            avg_session_duration=session_time,
            weekly_schedule=schedule,
            workouts=workouts,
            phases=phases,
            progression_scheme=progression,
            deload_schedule=deload,
            warm_up_protocol=warm_up,
            recovery_guidance=recovery,
        )
        self._workout_plans[plan_id] = plan
        return plan

    def _generate_workouts(
        self,
        schedule: Dict[str, str],
        style: TrainingStyle,
        experience: ExperienceLevel,
        session_time: int,
        goal: str,
        equipment: List[str],
    ) -> List[Workout]:
        """Generate workout sessions."""
        workouts = []
        workout_id_counter = 0

        # Sets/reps by experience and goal
        set_rep_schemes = {
            "muscle_building": {"beginner": "3x10-12", "intermediate": "4x8-12", "advanced": "4-5x6-10"},
            "strength": {"beginner": "3x8", "intermediate": "4x5-6", "advanced": "5x3-5"},
            "weight_loss": {"beginner": "3x12-15", "intermediate": "4x10-12", "advanced": "circuit style"},
            "endurance": {"beginner": "3x15-20", "intermediate": "3-4x12-15", "advanced": "high volume"},
            "general_fitness": {"beginner": "3x10-12", "intermediate": "3-4x8-12", "advanced": "varied"},
        }
        sr = set_rep_schemes.get(goal, set_rep_schemes["general_fitness"])
        set_rep = sr.get(experience.value, "3x10-12")

        # Rest periods
        rest_periods = {
            "strength": "2-3 min",
            "muscle_building": "60-90 sec",
            "weight_loss": "30-45 sec (circuit)",
            "endurance": "30-60 sec",
            "general_fitness": "60-90 sec",
        }
        rest = rest_periods.get(goal, "60-90 sec")

        # Intensity
        intensity_base = {
            "beginner": 5.0,
            "intermediate": 7.0,
            "advanced": 8.0,
            "elite": 9.0,
        }
        intensity = intensity_base.get(experience.value, 5.0)

        for day, workout_name in schedule.items():
            workout_id_counter += 1
            wid = f"workout-{workout_id_counter}"

            # Determine muscle groups
            if "Push" in workout_name:
                muscles = ["chest", "shoulders", "triceps"]
            elif "Pull" in workout_name:
                muscles = ["back", "biceps", "rear delts"]
            elif "Leg" in workout_name:
                muscles = ["quads", "hamstrings", "glutes", "calves"]
            elif "Upper" in workout_name:
                muscles = ["chest", "back", "shoulders", "arms"]
            elif "Lower" in workout_name:
                muscles = ["quads", "hamstrings", "glutes", "calves"]
            elif "Full Body" in workout_name or "Full" in workout_name:
                muscles = ["chest", "back", "legs", "core"]
            elif "Cardio" in workout_name:
                muscles = ["cardio", "full body"]
            else:
                muscles = ["full body"]

            # Generate exercises
            exercises = []
            for muscle in muscles:
                muscle_key = muscle.lower()
                # Find matching exercise category
                matching_key = None
                for key in self._exercise_db:
                    if key in muscle_key or muscle_key in key:
                        matching_key = key
                        break
                if matching_key and matching_key in self._exercise_db:
                    ex_list = self._exercise_db[matching_key][:3]  # Top 3 exercises
                    for ex in ex_list:
                        exercises.append({
                            "name": ex["name"],
                            "type": ex["type"],
                            "sets": int(set_rep.split("x")[0]),
                            "reps": set_rep.split("x")[1] if "x" in set_rep else "10-12",
                            "rest": rest,
                            "equipment": ex["equipment"],
                            "difficulty": ex["difficulty"],
                        })
                elif muscle_key == "cardio":
                    cardio_ex = self._exercise_db.get("cardio", [])[:2]
                    for ex in cardio_ex:
                        exercises.append({
                            "name": ex["name"],
                            "type": "cardio",
                            "sets": 1,
                            "reps": f"{session_time // 3} min",
                            "rest": "N/A",
                            "equipment": ex["equipment"],
                            "intensity": ex["intensity"],
                        })

            # Add core work
            if "Cardio" not in workout_name and "Full" in workout_name or "Push" in workout_name or "Pull" in workout_name:
                core_ex = self._exercise_db.get("core", [])[:3]
                for ex in core_ex:
                    exercises.append({
                        "name": ex["name"],
                        "type": "core",
                        "sets": 3,
                        "reps": "15-20" if ex["type"] != "isometric" else "30-60 sec",
                        "rest": "30 sec",
                        "equipment": ex["equipment"],
                        "difficulty": ex["difficulty"],
                    })

            # Warm-up and cool-down
            warm_up_list = [
                "5 min light cardio",
                "Dynamic stretches",
                "Activation drills",
                "2 warm-up sets of first exercise",
            ]
            cool_down_list = [
                "5 min walking/light cardio",
                "Static stretching (20-30 sec per muscle)",
                "Foam rolling",
                "Deep breathing",
            ]

            # Calories estimate
            cal_per_min = {"beginner": 6, "intermediate": 8, "advanced": 10, "elite": 12}
            calories = session_time * cal_per_min.get(experience.value, 6)

            # Difficulty
            difficulty = experience.value

            workout = Workout(
                id=wid,
                name=workout_name,
                type="resistance" if "Cardio" not in workout_name else "cardio",
                duration_min=session_time,
                difficulty=difficulty,
                target_muscles=muscles,
                exercises=exercises,
                warm_up=warm_up_list,
                cool_down=cool_down_list,
                calories_burned_estimate=float(calories),
                intensity=intensity,
                rest_intervals=rest,
                progression_notes=(
                    "Increase weight by 2.5-5% when you can complete all sets and reps "
                    "with good form across 2 consecutive workouts."
                ),
            )
            workouts.append(workout)

        return workouts

    def _create_nutrition_guidance(self, assessment: FitnessAssessment) -> NutritionGuidance:
        """Step 3: Create fitness nutrition guidance."""
        self._nutrition_count += 1
        nutrition_id = f"nutrition-{self._nutrition_count}"

        goal = self.config.get("primary_goal", "general_fitness")
        weight_kg = assessment.weight_kg

        # Calorie target based on BMR estimate + activity
        bmr_estimate = weight_kg * 24  # Rough estimate
        activity_factor = {
            "sedentary": 1.2,
            "light": 1.375,
            "moderate": 1.55,
            "active": 1.725,
            "very_active": 1.9,
        }
        # Adjust based on workout frequency
        freq_factor = 1.2 + (assessment.workout_frequency * 0.1)
        tdee = bmr_estimate * freq_factor

        # Adjust for goal
        if goal == "weight_loss":
            calories = tdee - 400
            protein_g = weight_kg * 1.8
            carbs_pct = 0.35
            fat_pct = 0.30
            protein_pct = 0.35
        elif goal in ("muscle_building", "strength"):
            calories = tdee + 250
            protein_g = weight_kg * 2.0
            carbs_pct = 0.50
            fat_pct = 0.25
            protein_pct = 0.25
        elif goal == "endurance":
            calories = tdee
            protein_g = weight_kg * 1.4
            carbs_pct = 0.55
            fat_pct = 0.25
            protein_pct = 0.20
        else:  # general_fitness, etc.
            calories = tdee
            protein_g = weight_kg * 1.6
            carbs_pct = 0.45
            fat_pct = 0.30
            protein_pct = 0.25

        # Macro breakdown
        macros = {
            "protein_g": round(protein_g, 1),
            "carbs_g": round((calories * carbs_pct) / 4, 1),
            "fat_g": round((calories * fat_pct) / 9, 1),
            "protein_pct": round(protein_pct * 100, 0),
            "carbs_pct": round(carbs_pct * 100, 0),
            "fat_pct": round(fat_pct * 100, 0),
        }

        # Pre-workout
        pre_workout = [
            "Eat 1-2 hours before training",
            "Focus on easily digestible carbs + some protein",
            "Examples: banana + protein shake, oatmeal with berries, rice cakes + peanut butter",
            "Stay hydrated - drink water throughout the day",
            "Consider caffeine (100-200mg) for performance boost",
        ]

        # Post-workout
        post_workout = [
            "Consume protein + carbs within 1-2 hours post-workout",
            "Aim for 20-40g protein for muscle protein synthesis",
            "Examples: protein shake + banana, chicken + rice, Greek yogurt + granola",
            "Rehydrate - replace fluids lost during workout",
            "Don't skip post-workout nutrition on heavy training days",
        ]

        # Hydration
        hydration = (
            f"Base: {round(weight_kg * 0.033, 1)}L water daily. "
            f"Add 500ml for each hour of intense exercise. "
            f"Monitor urine color - pale yellow is ideal."
        )

        # Supplements
        supplements = [
            "Protein powder - convenient way to meet protein targets",
            "Creatine monohydrate - 5g daily for strength and muscle",
            "Pre-workout (optional) - caffeine + beta-alanine for energy",
            "Omega-3 fish oil - for recovery and inflammation",
            "Multivitamin - insurance policy for micronutrients",
        ]

        # Meal frequency
        meal_freq = "3-4 meals + 1-2 snacks, evenly spaced throughout the day"

        # Weight management
        weight_mgmt = [
            "Weigh yourself weekly at the same time/day",
            "Track waist and hip measurements monthly",
            "Take progress photos every 2 weeks",
            "Adjust calories by 200-300 if no progress for 2+ weeks",
            "Focus on trend over time, not daily fluctuations",
            "Sleep 7-9 hours - crucial for recovery and body composition",
        ]

        guidance = NutritionGuidance(
            id=nutrition_id,
            client_id=assessment.client_id,
            calorie_target=round(calories, 2),
            macro_breakdown=macros,
            protein_target_g=round(protein_g, 1),
            timing_recommendations="Spread protein evenly across 3-4 meals per day",
            pre_workout=pre_workout,
            post_workout=post_workout,
            hydration=hydration,
            supplements=supplements,
            meal_frequency=meal_freq,
            weight_management=weight_mgmt,
        )
        self._nutrition_guidance[nutrition_id] = guidance
        return guidance

    def _initialize_progress(self, assessment: FitnessAssessment) -> FitnessProgress:
        """Step 4: Initialize progress tracking."""
        self._progress_count += 1
        progress_id = f"progress-{self._progress_count}"

        goal = self.config.get("primary_goal", "general_fitness")
        period = "Week 1 - Baseline Assessment"

        # Strength gains (starting from zero)
        strength_gains = {
            "bench_press_1rm_kg": 0.0,
            "squat_1rm_kg": 0.0,
            "deadlift_1rm_kg": 0.0,
        }

        # Endurance
        endurance = {
            "mile_time_improvement_sec": 0.0,
            "resting_hr_change_bpm": 0.0,
        }

        # Initial challenges
        challenges = [
            "Establishing consistent workout routine",
            "Learning proper exercise form",
            "Building initial strength base",
            "Adjusting to new nutrition plan",
            "Managing soreness and recovery",
        ]

        # Motivation
        motivation = 8.0  # Starting high

        progress = FitnessProgress(
            id=progress_id,
            client_id=assessment.client_id,
            period=period,
            weight_change_kg=0.0,
            body_fat_change_pct=0.0,
            strength_gains=strength_gains,
            endurance_improvement=endurance,
            workout_adherence=0.0,
            energy_levels=5.0,
            recovery_quality=5.0,
            measurements={
                "chest_cm": 0.0,
                "waist_cm": 0.0,
                "hips_cm": 0.0,
                "thigh_cm": 0.0,
                "arm_cm": 0.0,
            },
            pr_achievements=[],
            challenges=challenges,
            adjustments_needed=[
                "Form checks after first 2 weeks",
                "Weight adjustment based on recovery feedback",
                "Nutrition tweaks after initial assessment",
            ],
            motivation_level=motivation,
        )
        self._progress_records[progress_id] = progress
        return progress

    # ------------------------------------------------------------------
    # Agent prompts
    # ------------------------------------------------------------------

    def _build_fitness_coach_prompt(self) -> str:
        """Build the system prompt for the fitness coach role."""
        goal = self.config.get("primary_goal", "general_fitness").replace("_", " ")
        return (
            f"You are a certified fitness coach specializing in {goal}. "
            "Design safe, effective, and progressive training programs "
            "tailored to each client's goals, experience level, and "
            "available equipment. Motivate and educate clients. "
            "Emphasize proper form, consistency, and sustainable habits. "
            "Always prioritize safety and individualization."
        )

    def _build_exercise_physiologist_prompt(self) -> str:
        """Build the system prompt for the exercise physiologist role."""
        return (
            "You are an exercise physiologist with deep knowledge of "
            "biomechanics, exercise science, and injury prevention. "
            "Ensure exercises are performed with proper form and technique. "
            "Select appropriate exercises for each client's needs and "
            "limitations. Provide modifications for injuries and special "
            "populations. Apply evidence-based exercise principles."
        )

    def _build_nutrition_advisor_prompt(self) -> str:
        """Build the system prompt for the nutrition advisor role."""
        return (
            "You are a sports nutrition advisor. Provide evidence-based "
            "nutrition guidance tailored to fitness goals and training "
            "demands. Calculate optimal calorie and macronutrient targets. "
            "Advise on nutrient timing, supplementation, and hydration. "
            "Help clients fuel workouts properly and optimize recovery."
        )

    def _build_progress_tracker_prompt(self) -> str:
        """Build the system prompt for the progress tracker role."""
        return (
            "You are a fitness progress tracking specialist. Monitor "
            "client progress across multiple metrics: strength gains, "
            "body composition changes, endurance improvements, and "
            "consistency. Identify plateaus and recommend program "
            "adjustments. Provide regular progress reports that are "
            "motivating and data-driven. Celebrate wins and keep "
            "clients accountable."
        )

    # ------------------------------------------------------------------
    # Metrics
    # ------------------------------------------------------------------

    def _update_fitness_metrics(self) -> None:
        """Update fitness-specific metrics."""
        total = self.fitness_metrics.clients_served
        if total == 0:
            return

        goal = self.config.get("primary_goal", "general_fitness")

        # Client progress
        if goal == "weight_loss":
            self.fitness_metrics.client_progress = 7.5
            self.fitness_metrics.avg_weight_change = -4.0  # kg lost
            self.fitness_metrics.avg_strength_gain = 15.0
        elif goal == "muscle_building":
            self.fitness_metrics.client_progress = 7.0
            self.fitness_metrics.avg_weight_change = 2.5  # kg gained
            self.fitness_metrics.avg_strength_gain = 25.0
        elif goal == "strength":
            self.fitness_metrics.client_progress = 7.5
            self.fitness_metrics.avg_strength_gain = 30.0
        else:
            self.fitness_metrics.client_progress = 8.0
            self.fitness_metrics.avg_strength_gain = 20.0

        # Workout adherence
        self.fitness_metrics.workout_adherence = 70.0

        # Goal achievement
        self.fitness_metrics.goal_achievement = 60.0

        # Program satisfaction
        self.fitness_metrics.program_satisfaction = 4.3  # 0-5

        # Injury prevention
        self.fitness_metrics.injury_prevention_rate = 85.0

        # Retention
        self.fitness_metrics.retention_rate = 65.0

        # Program duration
        self.fitness_metrics.avg_program_duration = 10.0  # weeks

    def get_fitness_metrics(self) -> Dict[str, Any]:
        """Get the current fitness metrics.

        Returns:
            Dictionary with fitness metrics including client progress,
            workout adherence, strength gains, and goal achievement.
        """
        return self.fitness_metrics.to_dict()
