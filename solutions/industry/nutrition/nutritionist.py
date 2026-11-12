"""
Nutritionist AI Solution Template.

A comprehensive AI-powered nutritionist assistant for
nutrition clinics, wellness centers, and health practices.
Includes multi-agent roles for nutritionist, diet planner,
meal prep specialist, and progress tracker.

Typical workflow:
    assessment -> goal setting -> meal planning
    -> supplementation advice -> progress tracking
"""

from typing import Optional, Dict, Any, List
from enum import Enum
from dataclasses import dataclass, field

from solutions.templates.base_solution import (
    BaseSolution,
    SolutionMetadata,
    SolutionMetrics,
)


class DietType(str, Enum):
    """Types of dietary patterns."""
    BALANCED = "balanced"
    VEGETARIAN = "vegetarian"
    VEGAN = "vegan"
    KETO = "keto"
    LOW_CARB = "low_carb"
    MEDITERRANEAN = "mediterranean"
    PALEO = "paleo"
    INTERMITTENT_FASTING = "intermittent_fasting"
    HIGH_PROTEIN = "high_protein"
    GLUTEN_FREE = "gluten_free"
    DAIRY_FREE = "dairy_free"
    DIABETIC = "diabetic"
    ANTI_INFLAMMATORY = "anti_inflammatory"
    PLANT_BASED = "plant_based"


class NutritionGoal(str, Enum):
    """Nutrition and health goals."""
    WEIGHT_LOSS = "weight_loss"
    WEIGHT_GAIN = "weight_gain"
    MUSCLE_BUILDING = "muscle_building"
    MAINTENANCE = "maintenance"
    IMPROVE_ENERGY = "improve_energy"
    DIGESTIVE_HEALTH = "digestive_health"
    SPORTS_PERFORMANCE = "sports_performance"
    BLOOD_SUGAR_CONTROL = "blood_sugar_control"
    HEART_HEALTH = "heart_health"
    IMMUNE_SUPPORT = "immune_support"
    ANTI_AGING = "anti_aging"
    PREGNANCY = "pregnancy"


class MealType(str, Enum):
    """Types of meals."""
    BREAKFAST = "breakfast"
    LUNCH = "lunch"
    DINNER = "dinner"
    SNACK = "snack"
    SUPPLEMENT = "supplement"


class ActivityLevel(str, Enum):
    """Physical activity levels."""
    SEDENTARY = "sedentary"
    LIGHT = "light"
    MODERATE = "moderate"
    ACTIVE = "active"
    VERY_ACTIVE = "very_active"


class NutritionRole(str, Enum):
    """Agent roles in the nutritionist solution."""
    NUTRITIONIST = "nutritionist"
    DIET_PLANNER = "diet_planner"
    MEAL_PREP_SPECIALIST = "meal_prep_specialist"
    PROGRESS_TRACKER = "progress_tracker"


@dataclass
class NutritionAssessment:
    """Represents a nutrition assessment.

    Attributes:
        id: Unique assessment identifier.
        client_id: Client identifier.
        age: Client age.
        gender: Client gender.
        height_cm: Height in centimeters.
        weight_kg: Current weight in kilograms.
        goal_weight_kg: Goal weight in kilograms.
        activity_level: Physical activity level.
        current_diet: Description of current eating habits.
        dietary_restrictions: Restrictions and allergies.
        food_preferences: Food likes and dislikes.
        health_conditions: Relevant health conditions.
        medications: Current medications.
        goals: Nutrition and health goals.
        bmi: Body mass index.
        bmr: Basal metabolic rate (calories).
        tdee: Total daily energy expenditure.
        target_calories: Target daily calories.
        protein_target: Daily protein target (grams).
        readiness_score: Readiness for change score (0-10).
    """
    id: str
    client_id: str = ""
    age: int = 30
    gender: str = ""
    height_cm: float = 0.0
    weight_kg: float = 0.0
    goal_weight_kg: float = 0.0
    activity_level: ActivityLevel = ActivityLevel.MODERATE
    current_diet: str = ""
    dietary_restrictions: List[str] = field(default_factory=list)
    food_preferences: Dict[str, List[str]] = field(default_factory=dict)
    health_conditions: List[str] = field(default_factory=list)
    medications: List[str] = field(default_factory=list)
    goals: List[str] = field(default_factory=list)
    bmi: float = 0.0
    bmr: float = 0.0
    tdee: float = 0.0
    target_calories: float = 0.0
    protein_target: float = 0.0
    readiness_score: float = 0.0


@dataclass
class MealPlan:
    """Represents a meal plan.

    Attributes:
        id: Unique meal plan identifier.
        client_id: Client identifier.
        plan_name: Name of the meal plan.
        diet_type: Type of diet pattern.
        daily_calories: Target daily calories.
        macronutrient_breakdown: Macro breakdown (protein, carbs, fat).
        meals: List of meals with details.
        meal_timing: Meal timing schedule.
        shopping_list: Complete shopping list.
        prep_instructions: Meal prep instructions.
        swap_options: Food swap options.
        hydration_goal: Daily water intake goal.
        fiber_target: Daily fiber target.
        plan_duration_days: Duration of the plan in days.
    """
    id: str
    client_id: str = ""
    plan_name: str = ""
    diet_type: DietType = DietType.BALANCED
    daily_calories: float = 0.0
    macronutrient_breakdown: Dict[str, float] = field(default_factory=dict)
    meals: List[Dict[str, Any]] = field(default_factory=list)
    meal_timing: Dict[str, str] = field(default_factory=dict)
    shopping_list: List[str] = field(default_factory=list)
    prep_instructions: List[str] = field(default_factory=list)
    swap_options: Dict[str, List[str]] = field(default_factory=dict)
    hydration_goal: float = 0.0
    fiber_target: float = 0.0
    plan_duration_days: int = 7


@dataclass
class SupplementRecommendation:
    """Represents a supplement recommendation.

    Attributes:
        id: Unique supplement ID.
        name: Supplement name.
        purpose: Reason/reasoning for recommendation.
        dosage: Recommended dosage.
        timing: When to take it.
        food_interactions: Food and medication interactions.
        benefits: Expected benefits.
        potential_side_effects: Possible side effects.
        food_sources: Food sources of this nutrient.
        priority: Priority level.
        duration: Recommended duration of use.
    """
    id: str
    name: str = ""
    purpose: str = ""
    dosage: str = ""
    timing: str = ""
    food_interactions: List[str] = field(default_factory=list)
    benefits: List[str] = field(default_factory=list)
    potential_side_effects: List[str] = field(default_factory=list)
    food_sources: List[str] = field(default_factory=list)
    priority: str = "optional"
    duration: str = ""


@dataclass
class NutritionProgress:
    """Represents nutrition progress tracking.

    Attributes:
        id: Unique progress record ID.
        client_id: Client identifier.
        period: Time period covered.
        weight_change_kg: Weight change in kilograms.
        body_composition_changes: Body composition changes.
        plan_adherence: Plan adherence percentage.
        goal_progress: Progress toward each goal.
        energy_levels: Reported energy levels (1-10).
        digestion_improvement: Digestion improvement rating.
        cravings_reduction: Cravings reduction.
        measurements: Body measurements.
        lab_markers: Relevant lab markers.
        challenges: Challenges faced.
        successes: Wins and successes.
        recommendations: Recommendations for next period.
    """
    id: str
    client_id: str = ""
    period: str = ""
    weight_change_kg: float = 0.0
    body_composition_changes: Dict[str, float] = field(default_factory=dict)
    plan_adherence: float = 0.0
    goal_progress: Dict[str, float] = field(default_factory=dict)
    energy_levels: float = 0.0
    digestion_improvement: float = 0.0
    cravings_reduction: float = 0.0
    measurements: Dict[str, float] = field(default_factory=dict)
    lab_markers: Dict[str, str] = field(default_factory=dict)
    challenges: List[str] = field(default_factory=list)
    successes: List[str] = field(default_factory=list)
    recommendations: List[str] = field(default_factory=list)


@dataclass
class NutritionMetrics(SolutionMetrics):
    """Extended metrics for the nutritionist solution.

    Attributes:
        client_outcomes: Average client outcome score (0-10).
        plan_adherence: Average plan adherence rate.
        goal_achievement_rate: Goal achievement rate.
        clients_served: Total clients served.
        avg_weight_loss_kg: Average weight loss per client (kg).
        avg_energy_improvement: Average energy improvement (1-10).
        meal_plan_satisfaction: Meal plan satisfaction rating.
        supplement_effectiveness: Perceived supplement effectiveness.
        follow_up_rate: Follow-up appointment rate.
        avg_program_duration: Average program duration (weeks).
    """
    client_outcomes: float = 0.0
    plan_adherence: float = 0.0
    goal_achievement_rate: float = 0.0
    clients_served: int = 0
    avg_weight_loss_kg: float = 0.0
    avg_energy_improvement: float = 0.0
    meal_plan_satisfaction: float = 0.0
    supplement_effectiveness: float = 0.0
    follow_up_rate: float = 0.0
    avg_program_duration: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        """Convert nutrition metrics to a dictionary."""
        base = super().to_dict()
        base.update({
            "client_outcomes": self.client_outcomes,
            "plan_adherence": self.plan_adherence,
            "goal_achievement_rate": self.goal_achievement_rate,
            "clients_served": self.clients_served,
            "avg_weight_loss_kg": self.avg_weight_loss_kg,
            "avg_energy_improvement": self.avg_energy_improvement,
            "meal_plan_satisfaction": self.meal_plan_satisfaction,
            "supplement_effectiveness": self.supplement_effectiveness,
            "follow_up_rate": self.follow_up_rate,
            "avg_program_duration": self.avg_program_duration,
        })
        return base


class NutritionistSolution(BaseSolution):
    """Nutritionist AI solution template.

    Provides a complete, pre-configured nutritionist solution
    with multi-agent collaboration for assessment, goal setting,
    meal planning, supplementation advice, and progress tracking.

    Pre-configured agent roles:
        - Nutritionist: Conducts assessments and provides guidance
        - Diet Planner: Creates personalized meal plans
        - Meal Prep Specialist: Provides meal prep strategies and recipes
        - Progress Tracker: Monitors progress and adjusts plans

    Workflow:
        1. Assessment - Evaluate current nutrition status and needs
        2. Goal Setting - Define clear, measurable nutrition goals
        3. Meal Planning - Create personalized meal plans
        4. Supplementation - Recommend appropriate supplements
        5. Progress Tracking - Monitor progress and adjust as needed

    Configuration options:
        - diet_type: Primary dietary pattern
        - primary_goal: Primary nutrition goal
        - plan_duration: Duration of meal plans
        - supplement_approach: Approach to supplementation
        - tracking_frequency: Progress tracking frequency
    """

    def _build_metadata(self) -> SolutionMetadata:
        """Build metadata for the nutritionist solution."""
        return SolutionMetadata(
            name="nutritionist",
            version="1.0.0",
            industry="nutrition",
            description=(
                "AI-powered nutritionist assistant with personalized "
                "meal planning, supplementation advice, and progress "
                "tracking for optimal health outcomes."
            ),
            author="LiuHao AI OS",
            tags=["nutrition", "diet", "meal-planning", "supplements", "health", "weight-management"],
            min_liuhao_version="1.0.0",
        )

    def _build_config_schema(self) -> None:
        """Define configuration fields for the nutritionist solution."""
        self.config_schema.add_field(
            name="diet_type",
            field_type="string",
            required=False,
            default="balanced",
            description="Primary dietary pattern",
            valid_values=[
                "balanced", "vegetarian", "vegan", "keto", "low_carb",
                "mediterranean", "paleo", "intermittent_fasting",
                "high_protein", "gluten_free", "dairy_free", "diabetic",
                "anti_inflammatory", "plant_based",
            ],
        )
        self.config_schema.add_field(
            name="primary_goal",
            field_type="string",
            required=False,
            default="weight_loss",
            description="Primary nutrition goal",
            valid_values=[
                "weight_loss", "weight_gain", "muscle_building",
                "maintenance", "improve_energy", "digestive_health",
                "sports_performance", "blood_sugar_control",
                "heart_health", "immune_support", "anti_aging", "pregnancy",
            ],
        )
        self.config_schema.add_field(
            name="plan_duration_days",
            field_type="int",
            required=False,
            default=7,
            description="Duration of meal plans in days",
        )
        self.config_schema.add_field(
            name="supplement_approach",
            field_type="string",
            required=False,
            default="moderate",
            description="Approach to supplementation",
            valid_values=["minimal", "moderate", "comprehensive", "food_first"],
        )
        self.config_schema.add_field(
            name="tracking_frequency",
            field_type="string",
            required=False,
            default="weekly",
            description="Progress tracking frequency",
            valid_values=["daily", "weekly", "biweekly", "monthly"],
        )
        self.config_schema.add_field(
            name="meal_frequency",
            field_type="int",
            required=False,
            default=3,
            description="Number of main meals per day",
        )
        self.config_schema.add_field(
            name="snack_included",
            field_type="bool",
            required=False,
            default=True,
            description="Whether to include snacks in meal plans",
        )

    def _on_initialize(self) -> None:
        """Initialize nutritionist solution state."""
        self._agents: Dict[str, Dict[str, Any]] = {}
        self._assessments: Dict[str, NutritionAssessment] = {}
        self._meal_plans: Dict[str, MealPlan] = {}
        self._supplements: Dict[str, SupplementRecommendation] = {}
        self._progress_records: Dict[str, NutritionProgress] = {}
        self._assessment_count = 0
        self._meal_plan_count = 0
        self._supplement_count = 0
        self._progress_count = 0

        # Initialize nutrition-specific metrics
        self.nutrition_metrics = NutritionMetrics()

        # Food database (simplified)
        self._food_database = self._build_food_database()

    def _build_food_database(self) -> Dict[str, Any]:
        """Build a food database organized by category."""
        return {
            "proteins": [
                {"name": "Chicken breast", "protein_per_100g": 31, "calories_per_100g": 165, "tags": ["lean", "animal"]},
                {"name": "Salmon", "protein_per_100g": 20, "calories_per_100g": 208, "tags": ["omega3", "fish"]},
                {"name": "Greek yogurt", "protein_per_100g": 10, "calories_per_100g": 59, "tags": ["dairy", "probiotic"]},
                {"name": "Eggs", "protein_per_100g": 13, "calories_per_100g": 155, "tags": ["complete", "versatile"]},
                {"name": "Tofu", "protein_per_100g": 8, "calories_per_100g": 76, "tags": ["plant", "soy"]},
                {"name": "Lentils", "protein_per_100g": 9, "calories_per_100g": 116, "tags": ["plant", "fiber"]},
                {"name": "Beef (lean)", "protein_per_100g": 26, "calories_per_100g": 250, "tags": ["red_meat", "iron"]},
                {"name": "Chickpeas", "protein_per_100g": 7, "calories_per_100g": 164, "tags": ["plant", "fiber"]},
            ],
            "carbs": [
                {"name": "Brown rice", "carbs_per_100g": 23, "calories_per_100g": 112, "tags": ["whole_grain"]},
                {"name": "Quinoa", "carbs_per_100g": 21, "calories_per_100g": 120, "tags": ["complete_protein", "ancient_grain"]},
                {"name": "Oats", "carbs_per_100g": 66, "calories_per_100g": 389, "tags": ["fiber", "breakfast"]},
                {"name": "Sweet potato", "carbs_per_100g": 20, "calories_per_100g": 86, "tags": ["vitamin_a", "complex"]},
                {"name": "Whole wheat bread", "carbs_per_100g": 41, "calories_per_100g": 247, "tags": ["whole_grain"]},
                {"name": "Banana", "carbs_per_100g": 23, "calories_per_100g": 89, "tags": ["fruit", "potassium"]},
                {"name": "Oats", "carbs_per_100g": 66, "calories_per_100g": 389, "tags": ["beta_glucan"]},
            ],
            "fats": [
                {"name": "Avocado", "fat_per_100g": 15, "calories_per_100g": 160, "tags": ["healthy", "plant"]},
                {"name": "Almonds", "fat_per_100g": 50, "calories_per_100g": 579, "tags": ["nuts", "vitamin_e"]},
                {"name": "Olive oil", "fat_per_100g": 100, "calories_per_100g": 884, "tags": ["monounsaturated", "heart_healthy"]},
                {"name": "Chia seeds", "fat_per_100g": 31, "calories_per_100g": 486, "tags": ["omega3", "fiber"]},
                {"name": "Peanut butter", "fat_per_100g": 50, "calories_per_100g": 588, "tags": ["protein", "spread"]},
            ],
            "vegetables": [
                {"name": "Spinach", "calories_per_100g": 23, "tags": ["leafy_green", "iron"]},
                {"name": "Broccoli", "calories_per_100g": 34, "tags": ["cruciferous", "vitamin_c"]},
                {"name": "Bell peppers", "calories_per_100g": 20, "tags": ["vitamin_c", "colorful"]},
                {"name": "Carrots", "calories_per_100g": 41, "tags": ["vitamin_a", "crunchy"]},
                {"name": "Kale", "calories_per_100g": 49, "tags": ["superfood", "leafy_green"]},
            ],
            "fruits": [
                {"name": "Blueberries", "calories_per_100g": 57, "tags": ["antioxidants", "berry"]},
                {"name": "Apples", "calories_per_100g": 52, "tags": ["fiber", "versatile"]},
                {"name": "Strawberries", "calories_per_100g": 32, "tags": ["vitamin_c", "berry"]},
                {"name": "Oranges", "calories_per_100g": 47, "tags": ["vitamin_c", "citrus"]},
            ],
        }

    def _on_deploy(self) -> None:
        """Deploy nutritionist agents and workflows."""
        agent_configs = [
            (NutritionRole.NUTRITIONIST, {
                "name": "Nutritionist",
                "description": "Conducts assessments and provides guidance",
                "system_prompt": self._build_nutritionist_prompt(),
                "tools": ["nutritional_assessment", "dietary_analysis", "goal_setting"],
            }),
            (NutritionRole.DIET_PLANNER, {
                "name": "Diet Planner",
                "description": "Creates personalized meal plans",
                "system_prompt": self._build_diet_planner_prompt(),
                "tools": ["meal_planning", "calorie_calculation", "macro_targeting"],
            }),
            (NutritionRole.MEAL_PREP_SPECIALIST, {
                "name": "Meal Prep Specialist",
                "description": "Provides meal prep strategies and recipes",
                "system_prompt": self._build_meal_prep_specialist_prompt(),
                "tools": ["recipe_development", "batch_cooking", "shopping_lists"],
            }),
            (NutritionRole.PROGRESS_TRACKER, {
                "name": "Progress Tracker",
                "description": "Monitors progress and adjusts plans",
                "system_prompt": self._build_progress_tracker_prompt(),
                "tools": ["progress_monitoring", "weight_tracking", "goal_attainment"],
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
        """Process a nutrition request through the full workflow.

        Args:
            input_data: Dictionary containing client information.

        Returns:
            Dictionary with nutrition results including assessment,
            meal plan, supplement recommendations, and progress tracking.
        """
        client_id = input_data.get("client_id", f"client-{len(self._assessments)+1}")

        # Step 1: Nutrition assessment
        assessment = self._conduct_assessment(client_id, input_data)

        # Step 2: Goal setting (included in assessment)

        # Step 3: Meal planning
        meal_plan = self._create_meal_plan(assessment)

        # Step 4: Supplement recommendations
        supplements = self._recommend_supplements(assessment)

        # Step 5: Progress tracking setup
        progress = self._initialize_progress(assessment)

        # Store and update metrics
        self._assessments[assessment.id] = assessment
        self._meal_plans[meal_plan.id] = meal_plan
        for supp in supplements:
            self._supplements[supp.id] = supp
        self._progress_records[progress.id] = progress

        self.nutrition_metrics.clients_served += 1
        self._update_nutrition_metrics()

        return {
            "nutrition_assessment": {
                "id": assessment.id,
                "age": assessment.age,
                "height_cm": assessment.height_cm,
                "weight_kg": assessment.weight_kg,
                "goal_weight_kg": assessment.goal_weight_kg,
                "activity_level": assessment.activity_level.value,
                "bmi": round(assessment.bmi, 1),
                "bmr": round(assessment.bmr, 0),
                "tdee": round(assessment.tdee, 0),
                "target_calories": round(assessment.target_calories, 0),
                "protein_target_g": round(assessment.protein_target, 0),
                "goals": assessment.goals,
                "dietary_restrictions": assessment.dietary_restrictions,
                "readiness_score": assessment.readiness_score,
            },
            "meal_plan": {
                "id": meal_plan.id,
                "plan_name": meal_plan.plan_name,
                "diet_type": meal_plan.diet_type.value,
                "daily_calories": round(meal_plan.daily_calories, 0),
                "macronutrient_breakdown": meal_plan.macronutrient_breakdown,
                "meals_count": len(meal_plan.meals),
                "meals": [
                    {
                        "name": m["name"],
                        "type": m["type"],
                        "calories": m["calories"],
                        "protein_g": m.get("protein_g", 0),
                        "carbs_g": m.get("carbs_g", 0),
                        "fat_g": m.get("fat_g", 0),
                        "foods": m.get("foods", []),
                    }
                    for m in meal_plan.meals
                ],
                "meal_timing": meal_plan.meal_timing,
                "shopping_list_count": len(meal_plan.shopping_list),
                "prep_instructions": meal_plan.prep_instructions,
                "hydration_goal_l": meal_plan.hydration_goal,
                "fiber_target_g": meal_plan.fiber_target,
                "plan_duration_days": meal_plan.plan_duration_days,
            },
            "supplements": [
                {
                    "id": s.id,
                    "name": s.name,
                    "purpose": s.purpose,
                    "dosage": s.dosage,
                    "timing": s.timing,
                    "benefits": s.benefits,
                    "food_sources": s.food_sources,
                    "priority": s.priority,
                }
                for s in supplements
            ],
            "progress_tracking": {
                "id": progress.id,
                "period": progress.period,
                "plan_adherence": progress.plan_adherence,
                "energy_levels": progress.energy_levels,
                "challenges": progress.challenges,
                "successes": progress.successes,
                "recommendations": progress.recommendations,
            },
        }

    def _on_teardown(self) -> None:
        """Clean up nutritionist solution resources."""
        self._agents.clear()
        self._assessments.clear()
        self._meal_plans.clear()
        self._supplements.clear()
        self._progress_records.clear()
        self._assessment_count = 0
        self._meal_plan_count = 0
        self._supplement_count = 0
        self._progress_count = 0

    # ------------------------------------------------------------------
    # Workflow steps
    # ------------------------------------------------------------------

    def _conduct_assessment(self, client_id: str, data: Dict[str, Any]) -> NutritionAssessment:
        """Step 1: Conduct nutrition assessment."""
        self._assessment_count += 1
        assessment_id = f"assess-{self._assessment_count}"

        age = int(data.get("age", 30))
        gender = data.get("gender", "female")
        height_cm = float(data.get("height_cm", 170))
        weight_kg = float(data.get("weight_kg", 70))
        goal_weight_kg = float(data.get("goal_weight_kg", weight_kg - 5))

        try:
            activity = ActivityLevel(data.get("activity_level", "moderate"))
        except ValueError:
            activity = ActivityLevel.MODERATE

        # BMI calculation
        height_m = height_cm / 100
        bmi = weight_kg / (height_m * height_m)

        # BMR calculation (Mifflin-St Jeor)
        if gender == "male":
            bmr = 10 * weight_kg + 6.25 * height_cm - 5 * age + 5
        else:
            bmr = 10 * weight_kg + 6.25 * height_cm - 5 * age - 161

        # TDEE calculation
        activity_multipliers = {
            ActivityLevel.SEDENTARY: 1.2,
            ActivityLevel.LIGHT: 1.375,
            ActivityLevel.MODERATE: 1.55,
            ActivityLevel.ACTIVE: 1.725,
            ActivityLevel.VERY_ACTIVE: 1.9,
        }
        tdee = bmr * activity_multipliers.get(activity, 1.55)

        # Target calories based on goal
        goal = data.get("primary_goal", self.config.get("primary_goal", "weight_loss"))
        if goal == "weight_loss":
            target_calories = tdee - 500  # Moderate deficit
        elif goal == "weight_gain":
            target_calories = tdee + 300  # Moderate surplus
        elif goal == "muscle_building":
            target_calories = tdee + 250
        elif goal == "maintenance":
            target_calories = tdee
        else:
            target_calories = tdee  # Default to maintenance

        # Protein target (grams per kg of body weight)
        protein_per_kg = {
            "weight_loss": 1.6,
            "weight_gain": 1.6,
            "muscle_building": 2.0,
            "maintenance": 1.2,
            "sports_performance": 1.8,
        }
        protein_target = weight_kg * protein_per_kg.get(goal, 1.2)

        # Goals
        goals = data.get("goals", [])
        if not goals:
            goals = [
                f"{'Lose' if 'loss' in goal else 'Gain' if 'gain' in goal else 'Maintain'} weight to reach {goal_weight_kg} kg",
                "Improve energy levels and vitality",
                "Develop sustainable, healthy eating habits",
            ]

        # Readiness score
        readiness = float(data.get("readiness_score", 7.0))

        assessment = NutritionAssessment(
            id=assessment_id,
            client_id=client_id,
            age=age,
            gender=gender,
            height_cm=height_cm,
            weight_kg=weight_kg,
            goal_weight_kg=goal_weight_kg,
            activity_level=activity,
            current_diet=data.get("current_diet", "Standard Western diet"),
            dietary_restrictions=data.get("dietary_restrictions", []),
            food_preferences=data.get("food_preferences", {
                "likes": [],
                "dislikes": [],
            }),
            health_conditions=data.get("health_conditions", []),
            medications=data.get("medications", []),
            goals=goals,
            bmi=round(bmi, 2),
            bmr=round(bmr, 2),
            tdee=round(tdee, 2),
            target_calories=round(target_calories, 2),
            protein_target=round(protein_target, 1),
            readiness_score=readiness,
        )
        self._assessments[assessment_id] = assessment
        return assessment

    def _create_meal_plan(self, assessment: NutritionAssessment) -> MealPlan:
        """Step 2: Create personalized meal plan."""
        self._meal_plan_count += 1
        plan_id = f"mealplan-{self._meal_plan_count}"

        try:
            diet_type = DietType(self.config.get("diet_type", "balanced"))
        except ValueError:
            diet_type = DietType.BALANCED

        # Macronutrient breakdown based on diet type and goal
        goal = self.config.get("primary_goal", "weight_loss")
        macro_profiles = {
            DietType.BALANCED: {"protein": 0.30, "carbs": 0.40, "fat": 0.30},
            DietType.KETO: {"protein": 0.25, "carbs": 0.05, "fat": 0.70},
            DietType.LOW_CARB: {"protein": 0.35, "carbs": 0.15, "fat": 0.50},
            DietType.HIGH_PROTEIN: {"protein": 0.40, "carbs": 0.30, "fat": 0.30},
            DietType.MEDITERRANEAN: {"protein": 0.20, "carbs": 0.50, "fat": 0.30},
            DietType.VEGAN: {"protein": 0.20, "carbs": 0.55, "fat": 0.25},
            DietType.VEGETARIAN: {"protein": 0.25, "carbs": 0.45, "fat": 0.30},
            DietType.PALEO: {"protein": 0.30, "carbs": 0.25, "fat": 0.45},
        }
        macros = macro_profiles.get(diet_type, macro_profiles[DietType.BALANCED])

        # Adjust for goal
        if goal in ("weight_loss", "muscle_building"):
            macros["protein"] += 0.05
            macros["carbs"] -= 0.05

        calories = assessment.target_calories
        protein_g = (calories * macros["protein"]) / 4
        carbs_g = (calories * macros["carbs"]) / 4
        fat_g = (calories * macros["fat"]) / 9

        # Meal distribution
        num_meals = self.config.get("meal_frequency", 3)
        include_snacks = self.config.get("snack_included", True)
        meal_cal_ratio = 1.0 / num_meals

        meals = []

        # Breakfast
        breakfast = {
            "name": "Breakfast",
            "type": "breakfast",
            "calories": round(calories * meal_cal_ratio, 0),
            "protein_g": round(protein_g * meal_cal_ratio, 1),
            "carbs_g": round(carbs_g * meal_cal_ratio, 1),
            "fat_g": round(fat_g * meal_cal_ratio, 1),
            "foods": [],
            "recipe": "",
        }
        if diet_type in (DietType.KETO, DietType.LOW_CARB):
            breakfast["foods"] = ["Eggs (2-3)", "Avocado (1/2)", "Spinach", "Bacon/turkey sausage"]
            breakfast["recipe"] = "Vegetable omelet with avocado side and sautéed spinach"
        elif diet_type in (DietType.VEGAN, DietType.PLANT_BASED):
            breakfast["foods"] = ["Overnight oats", "Almond milk", "Chia seeds", "Berries", "Peanut butter"]
            breakfast["recipe"] = "Overnight oats with chia seeds, berries, and nut butter"
        else:
            breakfast["foods"] = ["Oatmeal", "Greek yogurt", "Berries", "Almonds", "Egg (optional)"]
            breakfast["recipe"] = "Protein oatmeal with Greek yogurt, mixed berries, and sliced almonds"
        meals.append(breakfast)

        # Lunch
        lunch = {
            "name": "Lunch",
            "type": "lunch",
            "calories": round(calories * meal_cal_ratio, 0),
            "protein_g": round(protein_g * meal_cal_ratio, 1),
            "carbs_g": round(carbs_g * meal_cal_ratio, 1),
            "fat_g": round(fat_g * meal_cal_ratio, 1),
            "foods": [],
            "recipe": "",
        }
        if diet_type in (DietType.VEGAN, DietType.PLANT_BASED, DietType.VEGETARIAN):
            lunch["foods"] = ["Quinoa bowl", "Chickpeas/black beans", "Mixed vegetables", "Avocado", "Tahini dressing"]
            lunch["recipe"] = "Buddha bowl with quinoa, roasted vegetables, legumes, and tahini dressing"
        elif diet_type == DietType.KETO:
            lunch["foods"] = ["Grilled chicken/salmon", "Cauliflower rice", "Broccoli", "Butter/olive oil"]
            lunch["recipe"] = "Grilled protein with cauliflower rice, roasted broccoli, and butter drizzle"
        elif diet_type == DietType.MEDITERRANEAN:
            lunch["foods"] = ["Grilled chicken/fish", "Brown rice/quinoa", "Mediterranean salad", "Olive oil dressing"]
            lunch["recipe"] = "Mediterranean grilled protein bowl with whole grains and fresh salad"
        else:
            lunch["foods"] = ["Grilled chicken breast", "Brown rice/quinoa", "Mixed vegetables", "Salad with olive oil"]
            lunch["recipe"] = "Balanced grilled chicken bowl with whole grains and steamed vegetables"
        meals.append(lunch)

        # Dinner
        dinner = {
            "name": "Dinner",
            "type": "dinner",
            "calories": round(calories * meal_cal_ratio, 0),
            "protein_g": round(protein_g * meal_cal_ratio, 1),
            "carbs_g": round(carbs_g * meal_cal_ratio, 1),
            "fat_g": round(fat_g * meal_cal_ratio, 1),
            "foods": [],
            "recipe": "",
        }
        if diet_type in (DietType.VEGAN, DietType.PLANT_BASED):
            dinner["foods"] = ["Lentil curry/stew", "Brown rice", "Sweet potato", "Leafy greens"]
            dinner["recipe"] = "Lentil and vegetable curry with brown rice and side salad"
        elif diet_type == DietType.KETO:
            dinner["foods"] = ["Salmon/steak", "Asparagus", "Cauliflower mash", "Butter"]
            dinner["recipe"] = "Baked salmon with roasted asparagus and garlic cauliflower mash"
        else:
            dinner["foods"] = ["Lean protein (fish/chicken/tofu)", "Sweet potato/rice", "Leafy greens", "Vegetables"]
            dinner["recipe"] = "Baked protein with sweet potato and large mixed green salad"
        meals.append(dinner)

        # Snacks
        if include_snacks:
            snacks = ["Morning snack", "Afternoon snack"]
            for snack_name in snacks:
                snack_cal = calories * 0.1
                snack = {
                    "name": snack_name,
                    "type": "snack",
                    "calories": round(snack_cal, 0),
                    "protein_g": round(protein_g * 0.08, 1),
                    "carbs_g": round(carbs_g * 0.1, 1),
                    "fat_g": round(fat_g * 0.1, 1),
                    "foods": [],
                    "recipe": "",
                }
                if diet_type in (DietType.VEGAN, DietType.PLANT_BASED):
                    snack["foods"] = ["Apple slices", "Almond butter", "Handful of nuts"]
                    snack["recipe"] = "Apple with almond butter or mixed nuts"
                elif diet_type == DietType.KETO:
                    snack["foods"] = ["Cheese sticks", "Olives", "Hard-boiled egg"]
                    snack["recipe"] = "Cheese and olives or hard-boiled egg"
                else:
                    snack["foods"] = ["Greek yogurt", "Protein bar", "Fruit and nuts"]
                    snack["recipe"] = "Greek yogurt with berries or protein snack bar"
                meals.append(snack)

        # Meal timing
        meal_timing = {
            "breakfast": "7:00-8:00 AM",
            "morning_snack": "10:00-10:30 AM" if include_snacks else "N/A",
            "lunch": "12:30-1:30 PM",
            "afternoon_snack": "3:30-4:00 PM" if include_snacks else "N/A",
            "dinner": "6:30-7:30 PM",
        }

        # Shopping list
        shopping = [
            "Proteins: Chicken breast, salmon, eggs, Greek yogurt",
            "Grains: Brown rice, quinoa, oats",
            "Vegetables: Spinach, broccoli, bell peppers, carrots, sweet potato",
            "Fruits: Blueberries, bananas, apples",
            "Healthy fats: Avocado, almonds, olive oil, chia seeds",
            "Dairy/alternatives: Greek yogurt, almond milk",
            "Spices and condiments",
        ]
        if diet_type in (DietType.VEGAN, DietType.PLANT_BASED):
            shopping = [
                "Plant proteins: Lentils, chickpeas, black beans, tofu, tempeh",
                "Grains: Brown rice, quinoa, oats, whole grain bread",
                "Vegetables: Spinach, broccoli, kale, bell peppers, sweet potato",
                "Fruits: Blueberries, bananas, apples, oranges",
                "Healthy fats: Avocado, nuts, seeds, olive oil",
                "Plant milks: Almond, oat, or soy milk",
                "Legumes and pulses for variety",
            ]
        if diet_type == DietType.KETO:
            shopping = [
                "Proteins: Eggs, chicken, beef, salmon, bacon",
                "Low-carb veggies: Spinach, broccoli, cauliflower, zucchini, asparagus",
                "Healthy fats: Avocado, butter, olive oil, MCT oil, nuts",
                "Dairy: Cheese, heavy cream, Greek yogurt (in moderation)",
                "Low-carb sweeteners: Stevia, erythritol",
            ]

        # Prep instructions
        prep_instructions = [
            "Plan meals for the week on Sunday",
            "Grocery shop once per week with a list",
            "Prep proteins and grains in advance (batch cooking)",
            "Wash and chop vegetables ahead of time",
            "Prepare snack portions in containers",
            "Cook large batches of grains/staples",
        ]

        # Swap options
        swap_options = {
            "chicken": ["turkey breast", "tofu", "tempeh", "fish"],
            "rice": ["quinoa", "cauliflower rice", "barley", "farro"],
            "dairy": ["almond milk", "oat milk", "coconut milk", "cashew cream"],
            "pasta": ["zucchini noodles", "spaghetti squash", "whole grain pasta"],
            "bread": ["lettuce wraps", "whole grain tortilla", "cloud bread (keto)"],
        }

        # Hydration goal
        hydration = round(weight_kg * 0.033, 1)  # 33ml per kg

        # Fiber target
        fiber_target = 25 if gender == "female" else 38

        plan_name = f"{diet_type.value.replace('_', ' ').title()} Plan - {goal.replace('_', ' ').title()}"

        plan = MealPlan(
            id=plan_id,
            client_id=assessment.client_id,
            plan_name=plan_name,
            diet_type=diet_type,
            daily_calories=round(calories, 2),
            macronutrient_breakdown={
                "protein_g": round(protein_g, 1),
                "carbs_g": round(carbs_g, 1),
                "fat_g": round(fat_g, 1),
                "protein_pct": round(macros["protein"] * 100, 0),
                "carbs_pct": round(macros["carbs"] * 100, 0),
                "fat_pct": round(macros["fat"] * 100, 0),
            },
            meals=meals,
            meal_timing=meal_timing,
            shopping_list=shopping,
            prep_instructions=prep_instructions,
            swap_options=swap_options,
            hydration_goal=hydration,
            fiber_target=float(fiber_target),
            plan_duration_days=self.config.get("plan_duration_days", 7),
        )
        self._meal_plans[plan_id] = plan
        return plan

    def _recommend_supplements(self, assessment: NutritionAssessment) -> List[SupplementRecommendation]:
        """Step 3: Recommend supplements."""
        supplements = []
        approach = self.config.get("supplement_approach", "moderate")
        goal = self.config.get("primary_goal", "weight_loss")
        diet_type = self.config.get("diet_type", "balanced")

        # Always recommended (food_first would have fewer)
        always_include = approach != "food_first"

        # Multivitamin
        if always_include:
            self._supplement_count += 1
            supplements.append(SupplementRecommendation(
                id=f"supp-{self._supplement_count}",
                name="Multivitamin",
                purpose="Insurance policy for nutrient gaps, especially with restrictive diets",
                dosage="1 tablet daily with food",
                timing="With breakfast or largest meal",
                food_interactions=["Take with food containing some fat for best absorption"],
                benefits=["Fills nutritional gaps", "Supports overall health", "Convenient insurance policy"],
                potential_side_effects=["Upset stomach if taken on empty stomach", "Unpleasant aftertaste with some brands"],
                food_sources=["Varied diet with fruits, vegetables, whole grains, lean proteins"],
                priority="basic",
                duration="Ongoing as dietary insurance",
            ))

        # Omega-3 / Fish oil
        if approach in ("moderate", "comprehensive"):
            self._supplement_count += 1
            is_vegan = diet_type in ("vegan", "plant_based")
            name = "Algae-based DHA/EPA" if is_vegan else "Omega-3 Fish Oil"
            supplements.append(SupplementRecommendation(
                id=f"supp-{self._supplement_count}",
                name=name,
                purpose="Supports heart health, brain function, and reduces inflammation",
                dosage="1000-2000mg combined EPA/DHA daily",
                timing="With a meal containing fat",
                food_interactions=["May increase bleeding risk with blood thinners"],
                benefits=[
                    "Supports cardiovascular health",
                    "Reduces systemic inflammation",
                    "Supports brain function and mood",
                    "Important for eye health",
                ],
                potential_side_effects=[
                    "Fishy aftertaste/burps",
                    "Mild gastrointestinal upset",
                    "Thin blood (rare, at high doses)",
                ],
                food_sources=["Fatty fish (salmon, mackerel, sardines)", "Walnuts", "Chia seeds", "Flaxseeds", "Hemp seeds"],
                priority="recommended",
                duration="Ongoing for long-term health benefits",
            ))

        # Vitamin D
        if approach in ("moderate", "comprehensive"):
            self._supplement_count += 1
            supplements.append(SupplementRecommendation(
                id=f"supp-{self._supplement_count}",
                name="Vitamin D3",
                purpose="Essential for bone health, immune function, and mood",
                dosage="1000-2000 IU daily (get blood test to confirm needs)",
                timing="With a meal containing fat for best absorption",
                food_interactions=["Better absorbed with dietary fat"],
                benefits=[
                    "Supports bone health and calcium absorption",
                    "Boosts immune function",
                    "Supports mood regulation",
                    "Important for muscle function",
                ],
                potential_side_effects=[
                    "Toxicity at very high doses (rare)",
                    "Nausea, vomiting (at extreme doses)",
                ],
                food_sources=["Fatty fish", "Egg yolks", "Fortified foods", "Sun exposure (UVB)"],
                priority="recommended",
                duration="Ongoing, especially during winter months",
            ))

        # Protein powder
        if goal in ("muscle_building", "weight_loss", "weight_gain") or diet_type in ("vegan", "plant_based"):
            self._supplement_count += 1
            source = "Plant-based (pea/rice/hemp blend)" if diet_type in ("vegan", "plant_based", "vegetarian") else "Whey protein isolate"
            supplements.append(SupplementRecommendation(
                id=f"supp-{self._supplement_count}",
                name=f"Protein Powder ({source})",
                purpose="Convenient way to meet daily protein goals",
                dosage="1-2 scoops (20-30g protein) daily",
                timing="Post-workout or as meal supplement",
                food_interactions=["Mix with water, milk, or in smoothies"],
                benefits=[
                    "Helps meet protein targets",
                    "Convenient for busy schedules",
                    "Supports muscle recovery and growth",
                    "Can help with satiety (weight loss)",
                ],
                potential_side_effects=[
                    "Digestive upset (with whey in lactose-intolerant)",
                    "Bloating at high doses",
                ],
                food_sources=["Chicken breast", "Fish", "Eggs", "Greek yogurt", "Lentils", "Tofu"],
                priority="recommended" if goal in ("muscle_building", "weight_loss") else "optional",
                duration="As needed to meet protein goals",
            ))

        # Probiotics
        if approach in ("comprehensive",) or "digestive" in goal:
            self._supplement_count += 1
            supplements.append(SupplementRecommendation(
                id=f"supp-{self._supplement_count}",
                name="Probiotics",
                purpose="Supports gut health, digestion, and immune function",
                dosage="10-50 billion CFU daily (strain-specific varies)",
                timing="First thing in the morning or 30 min before meals",
                food_interactions=["Best taken away from antibiotics (wait 2 hours)"],
                benefits=[
                    "Supports digestive health",
                    "Boosts immune function (70% immune system in gut)",
                    "May improve mood (gut-brain axis)",
                    "Helps with nutrient absorption",
                ],
                potential_side_effects=[
                    "Mild gas and bloating initially (die-off effect)",
                    "Rare - digestive discomfort",
                ],
                food_sources=["Yogurt/kefir", "Sauerkraut", "Kimchi", "Miso", "Kombucha", "Pickled vegetables"],
                priority="optional",
                duration="4-8 weeks initially, then as needed",
            ))

        # Magnesium
        if approach == "comprehensive":
            self._supplement_count += 1
            supplements.append(SupplementRecommendation(
                id=f"supp-{self._supplement_count}",
                name="Magnesium (glycinate or citrate)",
                purpose="Supports muscle function, sleep, stress response, and energy",
                dosage="200-400mg daily (elemental magnesium)",
                timing="Evening, before bed (magnesium glycinate is best for sleep)",
                food_interactions=["May cause loose stools at high doses"],
                benefits=[
                    "Supports muscle relaxation and recovery",
                    "Improves sleep quality",
                    "Supports stress management",
                    "Important for energy production",
                    "Helps with constipation (magnesium citrate)",
                ],
                potential_side_effects=[
                    "Diarrhea at high doses (especially citrate)",
                    "Mild gastrointestinal upset",
                ],
                food_sources=["Dark chocolate", "Almonds", "Spinach", "Pumpkin seeds", "Black beans", "Avocado"],
                priority="optional",
                duration="Ongoing if deficient or helpful for sleep/stress",
            ))

        return supplements

    def _initialize_progress(self, assessment: NutritionAssessment) -> NutritionProgress:
        """Step 4: Initialize progress tracking."""
        self._progress_count += 1
        progress_id = f"progress-{self._progress_count}"

        goal = self.config.get("primary_goal", "weight_loss")
        period = "Week 1 - Initial baseline"

        # Initial baseline values
        goal_progress = {}
        for g in assessment.goals:
            goal_progress[g] = 0.0  # Starting point

        # Initial challenges and successes
        initial_challenges = [
            "Adjusting to new eating patterns",
            "Planning and preparing meals consistently",
            "Managing cravings and hunger",
            "Finding time for meal prep",
        ]
        initial_successes = [
            "Taking the first step toward better health",
            "Having a clear plan to follow",
            "Access to personalized guidance",
        ]

        recommendations = [
            "Start with small, manageable changes",
            "Focus on consistency over perfection",
            "Track food intake initially to build awareness",
            "Weigh in weekly at the same time/day",
            "Take progress photos for motivation",
            "Stay hydrated - thirst is often mistaken for hunger",
        ]

        progress = NutritionProgress(
            id=progress_id,
            client_id=assessment.client_id,
            period=period,
            weight_change_kg=0.0,
            body_composition_changes={
                "body_fat_pct_change": 0.0,
                "muscle_mass_change_kg": 0.0,
            },
            plan_adherence=0.0,
            goal_progress=goal_progress,
            energy_levels=5.0,  # Baseline
            digestion_improvement=0.0,
            cravings_reduction=0.0,
            measurements={
                "waist_cm": 0.0,
                "hips_cm": 0.0,
                "chest_cm": 0.0,
            },
            lab_markers={
                "baseline": "Recommend baseline labs before starting (lipid panel, fasting glucose, vitamin D)",
            },
            challenges=initial_challenges,
            successes=initial_successes,
            recommendations=recommendations,
        )
        self._progress_records[progress_id] = progress
        return progress

    # ------------------------------------------------------------------
    # Agent prompts
    # ------------------------------------------------------------------

    def _build_nutritionist_prompt(self) -> str:
        """Build the system prompt for the nutritionist role."""
        return (
            "You are a registered dietitian/nutritionist. Conduct thorough "
            "nutritional assessments, analyze dietary patterns, and provide "
            "evidence-based nutrition guidance. Consider each client's unique "
            "needs, preferences, health conditions, and goals. Focus on "
            "sustainable, balanced approaches rather than extreme diets. "
            "Always prioritize client safety and well-being."
        )

    def _build_diet_planner_prompt(self) -> str:
        """Build the system prompt for the diet planner role."""
        diet = self.config.get("diet_type", "balanced").replace("_", " ")
        return (
            f"You are a diet planning specialist specializing in {diet} eating. "
            "Create personalized, nutritionally balanced meal plans that meet "
            "specific calorie and macronutrient targets. Ensure variety, "
            "practicality, and enjoyment in every plan. Consider food "
            "preferences, allergies, cultural background, and cooking skill "
            "level. Provide clear portions, timing, and swap options."
        )

    def _build_meal_prep_specialist_prompt(self) -> str:
        """Build the system prompt for the meal prep specialist role."""
        return (
            "You are a meal prep and recipe development expert. Create "
            "practical, time-efficient meal prep strategies and recipes. "
            "Provide batch cooking guidance, shopping lists organized by "
            "aisle, storage tips, and make-ahead ideas. Make healthy eating "
            "accessible and sustainable for busy lifestyles. Offer varying "
            "levels of complexity from beginner to advanced."
        )

    def _build_progress_tracker_prompt(self) -> str:
        """Build the system prompt for the progress tracker role."""
        frequency = self.config.get("tracking_frequency", "weekly")
        return (
            f"You are a nutrition progress tracking specialist. Monitor "
            f"client progress on a {frequency} basis using multiple metrics "
            "including weight, body measurements, energy levels, digestion, "
            "and goal attainment. Generate insightful progress reports that "
            "celebrate wins, identify challenges, and provide actionable "
            "recommendations. Adjust meal plans as needed based on progress "
            "and client feedback."
        )

    # ------------------------------------------------------------------
    # Metrics
    # ------------------------------------------------------------------

    def _update_nutrition_metrics(self) -> None:
        """Update nutrition-specific metrics."""
        total = self.nutrition_metrics.clients_served
        if total == 0:
            return

        # Client outcomes
        if self._progress_records:
            # Estimate based on goal type
            goal = self.config.get("primary_goal", "weight_loss")
            if goal == "weight_loss":
                self.nutrition_metrics.avg_weight_loss_kg = 0.5 * 8  # ~0.5kg/week for 8 weeks
                self.nutrition_metrics.client_outcomes = 7.5
            elif goal == "muscle_building":
                self.nutrition_metrics.client_outcomes = 7.0
            else:
                self.nutrition_metrics.client_outcomes = 8.0

        # Plan adherence
        self.nutrition_metrics.plan_adherence = 65.0

        # Goal achievement
        self.nutrition_metrics.goal_achievement_rate = 60.0

        # Energy improvement
        self.nutrition_metrics.avg_energy_improvement = 2.0  # On 1-10 scale

        # Meal plan satisfaction
        self.nutrition_metrics.meal_plan_satisfaction = 4.2  # 0-5

        # Supplement effectiveness
        approach = self.config.get("supplement_approach", "moderate")
        eff_map = {
            "minimal": 6.0,
            "moderate": 7.0,
            "comprehensive": 7.5,
            "food_first": 6.5,
        }
        self.nutrition_metrics.supplement_effectiveness = eff_map.get(approach, 7.0)

        # Follow-up rate
        self.nutrition_metrics.follow_up_rate = 75.0

        # Program duration
        self.nutrition_metrics.avg_program_duration = 12.0  # weeks

    def get_nutrition_metrics(self) -> Dict[str, Any]:
        """Get the current nutrition metrics.

        Returns:
            Dictionary with nutrition metrics including client outcomes,
            plan adherence, goal achievement, and weight loss results.
        """
        return self.nutrition_metrics.to_dict()
