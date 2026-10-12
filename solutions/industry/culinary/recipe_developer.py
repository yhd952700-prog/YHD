"""
Culinary Recipe Developer AI Solution Template.

A comprehensive AI-powered culinary recipe development solution for
restaurants, food brands, and culinary professionals. Includes
multi-agent roles for executive chef, nutritionist, food stylist,
and cost analyst with balanced recipe creation.

Typical workflow:
    cuisine selection -> ingredient sourcing -> recipe development
    -> nutrition analysis -> cost calculation -> plating
"""

from typing import Optional, Dict, Any, List
from enum import Enum
from dataclasses import dataclass, field

from solutions.templates.base_solution import (
    BaseSolution,
    SolutionMetadata,
    SolutionMetrics,
)


class CuisineType(str, Enum):
    """Types of cuisines."""
    CHINESE = "chinese"
    ITALIAN = "italian"
    FRENCH = "french"
    JAPANESE = "japanese"
    MEXICAN = "mexican"
    THAI = "thai"
    INDIAN = "indian"
    MEDITERRANEAN = "mediterranean"
    AMERICAN = "american"
    KOREAN = "korean"
    VIETNAMESE = "vietnamese"
    FUSION = "fusion"


class DishType(str, Enum):
    """Types of dishes."""
    APPETIZER = "appetizer"
    SOUP = "soup"
    SALAD = "salad"
    MAIN_COURSE = "main_course"
    SIDE_DISH = "side_dish"
    DESSERT = "dessert"
    BREAKFAST = "breakfast"
    BEVERAGE = "beverage"
    SNACK = "snack"


class DietaryRestriction(str, Enum):
    """Dietary restriction categories."""
    VEGETARIAN = "vegetarian"
    VEGAN = "vegan"
    GLUTEN_FREE = "gluten_free"
    DAIRY_FREE = "dairy_free"
    NUT_FREE = "nut_free"
    KOSHER = "kosher"
    HALAL = "halal"
    LOW_CARB = "low_carb"
    KETO = "keto"
    LOW_SODIUM = "low_sodium"


class DifficultyLevel(str, Enum):
    """Cooking difficulty levels."""
    EASY = "easy"
    MEDIUM = "medium"
    HARD = "hard"
    PROFESSIONAL = "professional"


class CulinaryRole(str, Enum):
    """Agent roles in the recipe developer solution."""
    EXECUTIVE_CHEF = "executive_chef"
    NUTRITIONIST = "nutritionist"
    FOOD_STYLIST = "food_stylist"
    COST_ANALYST = "cost_analyst"


@dataclass
class Ingredient:
    """Represents a recipe ingredient.

    Attributes:
        name: Ingredient name.
        quantity: Quantity amount.
        unit: Unit of measurement.
        category: Ingredient category.
        cost_per_unit: Cost per unit.
        total_cost: Total cost for this ingredient.
        substitutions: Possible substitutions.
        notes: Preparation notes.
    """
    name: str
    quantity: float = 0.0
    unit: str = ""
    category: str = ""
    cost_per_unit: float = 0.0
    total_cost: float = 0.0
    substitutions: List[str] = field(default_factory=list)
    notes: str = ""


@dataclass
class NutritionInfo:
    """Represents nutritional information for a recipe.

    Attributes:
        calories: Total calories per serving.
        protein_g: Protein in grams.
        carbohydrates_g: Carbohydrates in grams.
        fat_g: Fat in grams.
        saturated_fat_g: Saturated fat in grams.
        fiber_g: Dietary fiber in grams.
        sugar_g: Sugar in grams.
        sodium_mg: Sodium in milligrams.
        cholesterol_mg: Cholesterol in milligrams.
        vitamins: Key vitamins present.
        minerals: Key minerals present.
        allergens: Allergens present in the dish.
    """
    calories: float = 0.0
    protein_g: float = 0.0
    carbohydrates_g: float = 0.0
    fat_g: float = 0.0
    saturated_fat_g: float = 0.0
    fiber_g: float = 0.0
    sugar_g: float = 0.0
    sodium_mg: float = 0.0
    cholesterol_mg: float = 0.0
    vitamins: List[str] = field(default_factory=list)
    minerals: List[str] = field(default_factory=list)
    allergens: List[str] = field(default_factory=list)


@dataclass
class Recipe:
    """Represents a complete recipe.

    Attributes:
        id: Unique recipe identifier.
        name: Recipe name.
        cuisine: Type of cuisine.
        dish_type: Type of dish.
        description: Recipe description.
        servings: Number of servings.
        prep_time_min: Preparation time in minutes.
        cook_time_min: Cooking time in minutes.
        total_time_min: Total time in minutes.
        difficulty: Difficulty level.
        ingredients: List of ingredients.
        instructions: Cooking instructions (ordered list).
        nutrition: Nutritional information per serving.
        equipment: Required equipment.
        tips: Cooking tips and tricks.
        plating_guide: Plating and presentation guide.
        dietary_tags: Dietary compliance tags.
        quality_score: Overall recipe quality score (0-10).
    """
    id: str
    name: str = ""
    cuisine: CuisineType = CuisineType.FUSION
    dish_type: DishType = DishType.MAIN_COURSE
    description: str = ""
    servings: int = 4
    prep_time_min: int = 0
    cook_time_min: int = 0
    total_time_min: int = 0
    difficulty: DifficultyLevel = DifficultyLevel.MEDIUM
    ingredients: List[Ingredient] = field(default_factory=list)
    instructions: List[str] = field(default_factory=list)
    nutrition: NutritionInfo = field(default_factory=NutritionInfo)
    equipment: List[str] = field(default_factory=list)
    tips: List[str] = field(default_factory=list)
    plating_guide: str = ""
    dietary_tags: List[str] = field(default_factory=list)
    quality_score: float = 0.0


@dataclass
class CostBreakdown:
    """Represents recipe cost analysis.

    Attributes:
        recipe_id: Associated recipe ID.
        total_cost: Total ingredient cost.
        cost_per_serving: Cost per serving.
        food_cost_percentage: Target food cost percentage.
        suggested_price: Suggested menu price.
        margin: Gross margin percentage.
        most_expensive_items: Most expensive ingredients.
        cost_saving_tips: Tips to reduce cost.
        premium_ingredients: Premium/pricy ingredients.
    """
    recipe_id: str = ""
    total_cost: float = 0.0
    cost_per_serving: float = 0.0
    food_cost_percentage: float = 30.0
    suggested_price: float = 0.0
    margin: float = 0.0
    most_expensive_items: List[str] = field(default_factory=list)
    cost_saving_tips: List[str] = field(default_factory=list)
    premium_ingredients: List[str] = field(default_factory=list)


@dataclass
class CulinaryMetrics(SolutionMetrics):
    """Extended metrics for the recipe developer solution.

    Attributes:
        recipe_quality: Average recipe quality score (0-10).
        nutritional_balance: Nutritional balance score (0-10).
        cost_efficiency: Cost efficiency score (0-10).
        recipes_developed: Total recipes created.
        avg_prep_time: Average preparation time.
        avg_cook_time: Average cooking time.
        cuisines_covered: Number of cuisines covered.
        dietary_accommodations: Number of dietary variants.
        plating_quality: Average plating quality score.
        customer_rating: Average customer recipe rating.
    """
    recipe_quality: float = 0.0
    nutritional_balance: float = 0.0
    cost_efficiency: float = 0.0
    recipes_developed: int = 0
    avg_prep_time: float = 0.0
    avg_cook_time: float = 0.0
    cuisines_covered: int = 0
    dietary_accommodations: int = 0
    plating_quality: float = 0.0
    customer_rating: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        """Convert culinary metrics to a dictionary."""
        base = super().to_dict()
        base.update({
            "recipe_quality": self.recipe_quality,
            "nutritional_balance": self.nutritional_balance,
            "cost_efficiency": self.cost_efficiency,
            "recipes_developed": self.recipes_developed,
            "avg_prep_time": self.avg_prep_time,
            "avg_cook_time": self.avg_cook_time,
            "cuisines_covered": self.cuisines_covered,
            "dietary_accommodations": self.dietary_accommodations,
            "plating_quality": self.plating_quality,
            "customer_rating": self.customer_rating,
        })
        return base


class RecipeDeveloperSolution(BaseSolution):
    """Culinary Recipe Developer AI solution template.

    Provides a complete, pre-configured recipe development solution
    with multi-agent collaboration for cuisine selection, ingredient
    sourcing, recipe development, nutrition analysis, cost calculation,
    and plating guidance.

    Pre-configured agent roles:
        - Executive Chef: Develops recipes and flavor profiles
        - Nutritionist: Analyzes nutritional content and dietary compliance
        - Food Stylist: Advises on presentation and plating techniques
        - Cost Analyst: Calculates recipe costs and pricing strategies

    Workflow:
        1. Cuisine Selection - Determine cuisine and dish type
        2. Ingredient Sourcing - Select and source ingredients
        3. Recipe Development - Create complete recipe with instructions
        4. Nutrition Analysis - Analyze nutritional content
        5. Cost Calculation - Calculate costs and suggest pricing
        6. Plating - Provide plating and presentation guidance

    Configuration options:
        - cuisine: Primary cuisine type
        - dish_type: Type of dish to develop
        - difficulty: Target difficulty level
        - target_food_cost: Target food cost percentage
        - dietary_restrictions: List of dietary restrictions
    """

    def _build_metadata(self) -> SolutionMetadata:
        """Build metadata for the recipe developer solution."""
        return SolutionMetadata(
            name="recipe-developer",
            version="1.0.0",
            industry="culinary",
            description=(
                "AI-powered culinary recipe developer with nutrition analysis, "
                "cost calculation, plating guidance, and dietary accommodation."
            ),
            author="LiuHao AI OS",
            tags=["culinary", "recipe", "nutrition", "food", "cooking", "cost-analysis"],
            min_liuhao_version="1.0.0",
        )

    def _build_config_schema(self) -> None:
        """Define configuration fields for the recipe developer solution."""
        self.config_schema.add_field(
            name="cuisine",
            field_type="string",
            required=False,
            default="italian",
            description="Primary cuisine type",
            valid_values=[
                "chinese", "italian", "french", "japanese", "mexican",
                "thai", "indian", "mediterranean", "american", "korean",
                "vietnamese", "fusion",
            ],
        )
        self.config_schema.add_field(
            name="dish_type",
            field_type="string",
            required=False,
            default="main_course",
            description="Type of dish to develop",
            valid_values=[
                "appetizer", "soup", "salad", "main_course", "side_dish",
                "dessert", "breakfast", "beverage", "snack",
            ],
        )
        self.config_schema.add_field(
            name="difficulty",
            field_type="string",
            required=False,
            default="medium",
            description="Target cooking difficulty level",
            valid_values=["easy", "medium", "hard", "professional"],
        )
        self.config_schema.add_field(
            name="target_food_cost",
            field_type="float",
            required=False,
            default=30.0,
            description="Target food cost percentage (for menu pricing)",
        )
        self.config_schema.add_field(
            name="dietary_restrictions",
            field_type="list",
            required=False,
            default=[],
            description="List of dietary restrictions to accommodate",
        )
        self.config_schema.add_field(
            name="servings",
            field_type="int",
            required=False,
            default=4,
            description="Default number of servings",
        )
        self.config_schema.add_field(
            name="max_prep_time_min",
            field_type="int",
            required=False,
            default=60,
            description="Maximum acceptable preparation time in minutes",
        )

    def _on_initialize(self) -> None:
        """Initialize recipe developer solution state."""
        self._agents: Dict[str, Dict[str, Any]] = {}
        self._recipes: Dict[str, Recipe] = {}
        self._cost_analyses: Dict[str, CostBreakdown] = {}
        self._recipe_count = 0
        self._cuisines_covered: set = set()

        # Initialize culinary-specific metrics
        self.culinary_metrics = CulinaryMetrics()

        # Ingredient database
        self._ingredient_db = self._build_ingredient_database()

    def _build_ingredient_database(self) -> Dict[str, Any]:
        """Build ingredient database with costs and nutrition."""
        return {
            "proteins": {
                "chicken breast": {"cost_per_unit": 8.99, "unit": "lb", "protein": 31, "fat": 3.6, "calories": 165},
                "beef tenderloin": {"cost_per_unit": 24.99, "unit": "lb", "protein": 26, "fat": 15, "calories": 250},
                "salmon fillet": {"cost_per_unit": 12.99, "unit": "lb", "protein": 22, "fat": 13, "calories": 208},
                "tofu": {"cost_per_unit": 2.99, "unit": "lb", "protein": 8, "fat": 4, "calories": 76},
                "shrimp": {"cost_per_unit": 15.99, "unit": "lb", "protein": 24, "fat": 0.3, "calories": 99},
            },
            "vegetables": {
                "spinach": {"cost_per_unit": 2.99, "unit": "lb", "protein": 2.9, "fat": 0.4, "calories": 23},
                "broccoli": {"cost_per_unit": 1.99, "unit": "lb", "protein": 2.8, "fat": 0.4, "calories": 34},
                "carrots": {"cost_per_unit": 0.99, "unit": "lb", "protein": 0.9, "fat": 0.2, "calories": 41},
                "bell peppers": {"cost_per_unit": 2.49, "unit": "lb", "protein": 1, "fat": 0.3, "calories": 20},
                "mushrooms": {"cost_per_unit": 3.99, "unit": "lb", "protein": 3.1, "fat": 0.3, "calories": 22},
                "onion": {"cost_per_unit": 0.79, "unit": "lb", "protein": 1.1, "fat": 0.1, "calories": 40},
                "garlic": {"cost_per_unit": 4.99, "unit": "lb", "protein": 6.4, "fat": 0.5, "calories": 149},
            },
            "grains": {
                "rice": {"cost_per_unit": 1.49, "unit": "lb", "protein": 2.7, "fat": 0.3, "calories": 130},
                "pasta": {"cost_per_unit": 1.29, "unit": "lb", "protein": 5.3, "fat": 1.1, "calories": 131},
                "quinoa": {"cost_per_unit": 4.99, "unit": "lb", "protein": 4.4, "fat": 1.9, "calories": 120},
                "bread": {"cost_per_unit": 2.99, "unit": "loaf", "protein": 8, "fat": 2, "calories": 265},
            },
            "dairy": {
                "butter": {"cost_per_unit": 3.99, "unit": "lb", "protein": 0.9, "fat": 81, "calories": 717},
                "olive oil": {"cost_per_unit": 9.99, "unit": "liter", "protein": 0, "fat": 100, "calories": 884},
                "cheese": {"cost_per_unit": 5.99, "unit": "lb", "protein": 25, "fat": 33, "calories": 402},
                "cream": {"cost_per_unit": 3.49, "unit": "pint", "protein": 2, "fat": 37, "calories": 340},
            },
            "herbs_spices": {
                "salt": {"cost_per_unit": 0.49, "unit": "lb", "protein": 0, "fat": 0, "calories": 0},
                "black pepper": {"cost_per_unit": 10.99, "unit": "lb", "protein": 0, "fat": 0, "calories": 0},
                "fresh herbs": {"cost_per_unit": 2.99, "unit": "bunch", "protein": 0, "fat": 0, "calories": 0},
            },
        }

    def _on_deploy(self) -> None:
        """Deploy recipe developer agents and workflows."""
        agent_configs = [
            (CulinaryRole.EXECUTIVE_CHEF, {
                "name": "Executive Chef",
                "description": "Develops recipes and flavor profiles",
                "system_prompt": self._build_executive_chef_prompt(),
                "tools": ["recipe_database", "flavor_pairing", "technique_library"],
            }),
            (CulinaryRole.NUTRITIONIST, {
                "name": "Nutritionist",
                "description": "Analyzes nutritional content and dietary compliance",
                "system_prompt": self._build_nutritionist_prompt(),
                "tools": ["nutrition_calculator", "dietary_checker", "meal_planning"],
            }),
            (CulinaryRole.FOOD_STYLIST, {
                "name": "Food Stylist",
                "description": "Advises on presentation and plating techniques",
                "system_prompt": self._build_food_stylist_prompt(),
                "tools": ["plating_guide", "presentation_techniques", "garnish_suggestions"],
            }),
            (CulinaryRole.COST_ANALYST, {
                "name": "Cost Analyst",
                "description": "Calculates recipe costs and pricing strategies",
                "system_prompt": self._build_cost_analyst_prompt(),
                "tools": ["cost_calculator", "pricing_engine", "supplier_comparison"],
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
        """Process a recipe development request through the full workflow.

        Args:
            input_data: Dictionary containing recipe requirements with at least
                ``recipe_name`` or cuisine type specification.

        Returns:
            Dictionary with complete recipe including ingredients,
            instructions, nutrition info, cost breakdown, and plating guide.
        """
        # Step 1: Cuisine and dish type selection
        cuisine, dish_type = self._select_cuisine_and_type(input_data)

        # Step 2: Ingredient sourcing
        ingredients = self._select_ingredients(cuisine, dish_type, input_data)

        # Step 3: Recipe development
        recipe = self._develop_recipe(cuisine, dish_type, ingredients, input_data)

        # Step 4: Nutrition analysis
        nutrition = self._analyze_nutrition(recipe)
        recipe.nutrition = nutrition

        # Step 5: Cost calculation
        cost = self._calculate_cost(recipe)
        self._cost_analyses[recipe.id] = cost

        # Step 6: Plating guide
        plating = self._generate_plating_guide(recipe)
        recipe.plating_guide = plating

        # Store and update metrics
        self._recipes[recipe.id] = recipe
        self._cuisines_covered.add(cuisine.value)
        self.culinary_metrics.recipes_developed += 1
        self._update_culinary_metrics()

        return {
            "recipe": {
                "id": recipe.id,
                "name": recipe.name,
                "cuisine": recipe.cuisine.value,
                "dish_type": recipe.dish_type.value,
                "description": recipe.description,
                "servings": recipe.servings,
                "prep_time_min": recipe.prep_time_min,
                "cook_time_min": recipe.cook_time_min,
                "total_time_min": recipe.total_time_min,
                "difficulty": recipe.difficulty.value,
                "quality_score": recipe.quality_score,
            },
            "ingredients": [
                {
                    "name": i.name,
                    "quantity": i.quantity,
                    "unit": i.unit,
                    "category": i.category,
                    "cost": i.total_cost,
                    "substitutions": i.substitutions,
                }
                for i in recipe.ingredients
            ],
            "instructions": recipe.instructions,
            "nutrition": {
                "calories": nutrition.calories,
                "protein_g": nutrition.protein_g,
                "carbohydrates_g": nutrition.carbohydrates_g,
                "fat_g": nutrition.fat_g,
                "fiber_g": nutrition.fiber_g,
                "sodium_mg": nutrition.sodium_mg,
                "allergens": nutrition.allergens,
            },
            "cost": {
                "total_cost": cost.total_cost,
                "cost_per_serving": cost.cost_per_serving,
                "suggested_price": cost.suggested_price,
                "food_cost_pct": cost.food_cost_percentage,
                "margin": cost.margin,
                "most_expensive": cost.most_expensive_items,
                "saving_tips": cost.cost_saving_tips,
            },
            "plating": plating,
            "equipment": recipe.equipment,
            "tips": recipe.tips,
            "dietary_tags": recipe.dietary_tags,
        }

    def _on_teardown(self) -> None:
        """Clean up recipe developer solution resources."""
        self._agents.clear()
        self._recipes.clear()
        self._cost_analyses.clear()
        self._recipe_count = 0
        self._cuisines_covered.clear()

    # ------------------------------------------------------------------
    # Workflow steps
    # ------------------------------------------------------------------

    def _select_cuisine_and_type(
        self, data: Dict[str, Any]
    ) -> tuple[CuisineType, DishType]:
        """Step 1: Select cuisine and dish type."""
        cuisine_value = data.get("cuisine", self.config.get("cuisine", "italian"))
        dish_value = data.get("dish_type", self.config.get("dish_type", "main_course"))

        try:
            cuisine = CuisineType(cuisine_value)
        except ValueError:
            cuisine = CuisineType.ITALIAN

        try:
            dish_type = DishType(dish_value)
        except ValueError:
            dish_type = DishType.MAIN_COURSE

        return cuisine, dish_type

    def _select_ingredients(
        self, cuisine: CuisineType, dish_type: DishType, data: Dict[str, Any]
    ) -> List[Ingredient]:
        """Step 2: Select and source ingredients."""
        ingredients = []
        dietary_restrictions = data.get(
            "dietary_restrictions", self.config.get("dietary_restrictions", [])
        )
        restrictions_lower = [r.lower() for r in dietary_restrictions]

        # Cuisine-specific ingredient profiles
        cuisine_ingredients = {
            CuisineType.ITALIAN: {
                "proteins": ["chicken breast"],
                "vegetables": ["spinach", "broccoli", "bell peppers", "mushrooms", "onion", "garlic"],
                "grains": ["pasta"],
                "dairy": ["olive oil", "cheese"],
                "herbs_spices": ["salt", "black pepper", "fresh herbs"],
            },
            CuisineType.CHINESE: {
                "proteins": ["shrimp", "chicken breast"],
                "vegetables": ["broccoli", "bell peppers", "mushrooms", "onion", "garlic", "carrots"],
                "grains": ["rice"],
                "dairy": ["olive oil"],
                "herbs_spices": ["salt", "black pepper", "fresh herbs"],
            },
            CuisineType.JAPANESE: {
                "proteins": ["salmon fillet"],
                "vegetables": ["mushrooms", "carrots"],
                "grains": ["rice"],
                "dairy": [],
                "herbs_spices": ["salt", "fresh herbs"],
            },
            CuisineType.MEXICAN: {
                "proteins": ["chicken breast", "beef tenderloin"],
                "vegetables": ["bell peppers", "onion", "garlic"],
                "grains": ["rice", "bread"],
                "dairy": ["cheese"],
                "herbs_spices": ["salt", "black pepper", "fresh herbs"],
            },
            CuisineType.FRENCH: {
                "proteins": ["chicken breast", "salmon fillet"],
                "vegetables": ["mushrooms", "onion", "garlic", "carrots"],
                "grains": ["bread", "pasta"],
                "dairy": ["butter", "cream", "cheese", "olive oil"],
                "herbs_spices": ["salt", "black pepper", "fresh herbs"],
            },
            CuisineType.THAI: {
                "proteins": ["shrimp", "chicken breast"],
                "vegetables": ["bell peppers", "mushrooms", "onion", "garlic", "carrots"],
                "grains": ["rice"],
                "dairy": [],
                "herbs_spices": ["salt", "fresh herbs"],
            },
            CuisineType.INDIAN: {
                "proteins": ["chicken breast"],
                "vegetables": ["spinach", "onion", "garlic", "carrots", "bell peppers"],
                "grains": ["rice", "bread"],
                "dairy": ["cream", "butter"],
                "herbs_spices": ["salt", "black pepper", "fresh herbs"],
            },
            CuisineType.MEDITERRANEAN: {
                "proteins": ["salmon fillet", "chicken breast"],
                "vegetables": ["spinach", "broccoli", "bell peppers", "mushrooms", "onion", "garlic"],
                "grains": ["pasta", "bread"],
                "dairy": ["olive oil", "cheese"],
                "herbs_spices": ["salt", "black pepper", "fresh herbs"],
            },
            CuisineType.FUSION: {
                "proteins": ["chicken breast", "shrimp"],
                "vegetables": ["spinach", "broccoli", "bell peppers", "mushrooms", "onion", "garlic"],
                "grains": ["rice", "quinoa"],
                "dairy": ["olive oil"],
                "herbs_spices": ["salt", "black pepper", "fresh herbs"],
            },
        }

        profile = cuisine_ingredients.get(cuisine, cuisine_ingredients[CuisineType.FUSION])

        # Build ingredient list
        for category, items in profile.items():
            for item in items:
                # Skip if dietary restriction conflicts
                if category == "dairy" and "dairy_free" in restrictions_lower:
                    continue
                if category == "proteins" and item in ["chicken breast", "beef tenderloin", "salmon fillet", "shrimp"]:
                    if "vegetarian" in restrictions_lower or "vegan" in restrictions_lower:
                        continue
                if item == "tofu" and "vegan" not in restrictions_lower and "vegetarian" not in restrictions_lower:
                    continue

                db_item = None
                for cat, cat_items in self._ingredient_db.items():
                    if item in cat_items:
                        db_item = cat_items[item]
                        break

                if db_item:
                    # Calculate quantity based on dish type and servings
                    servings = self.config.get("servings", 4)
                    if category == "proteins":
                        quantity = 0.25 * servings  # ~4oz per serving
                    elif category == "vegetables":
                        quantity = 0.15 * servings
                    elif category == "grains":
                        quantity = 0.2 * servings
                    elif category == "dairy":
                        quantity = 0.05 * servings
                    else:
                        quantity = 0.02 * servings

                    quantity = round(quantity, 2)
                    total_cost = round(quantity * db_item["cost_per_unit"], 2)

                    # Substitutions
                    substitutions = []
                    if category == "proteins":
                        if item != "tofu":
                            substitutions.append("tofu (vegetarian)")
                    if category == "dairy":
                        substitutions.append("plant-based alternative")

                    ingredient = Ingredient(
                        name=item.title(),
                        quantity=quantity,
                        unit=db_item["unit"],
                        category=category,
                        cost_per_unit=db_item["cost_per_unit"],
                        total_cost=total_cost,
                        substitutions=substitutions,
                    )
                    ingredients.append(ingredient)

        return ingredients

    def _develop_recipe(
        self,
        cuisine: CuisineType,
        dish_type: DishType,
        ingredients: List[Ingredient],
        data: Dict[str, Any],
    ) -> Recipe:
        """Step 3: Develop complete recipe with instructions."""
        self._recipe_count += 1
        recipe_id = f"recipe-{self._recipe_count}"

        try:
            difficulty = DifficultyLevel(
                data.get("difficulty", self.config.get("difficulty", "medium"))
            )
        except ValueError:
            difficulty = DifficultyLevel.MEDIUM

        servings = self.config.get("servings", 4)

        # Generate recipe name
        protein_names = [i.name for i in ingredients if i.category == "proteins"]
        veg_names = [i.name for i in ingredients if i.category == "vegetables"]
        grain_names = [i.name for i in ingredients if i.category == "grains"]

        main_protein = protein_names[0] if protein_names else "Vegetable"
        style = cuisine.value.title()
        recipe_name = f"{style} {main_protein} with {', '.join(veg_names[:2])}"

        # Times based on difficulty
        prep_times = {
            DifficultyLevel.EASY: 15,
            DifficultyLevel.MEDIUM: 30,
            DifficultyLevel.HARD: 45,
            DifficultyLevel.PROFESSIONAL: 60,
        }
        cook_times = {
            DifficultyLevel.EASY: 15,
            DifficultyLevel.MEDIUM: 25,
            DifficultyLevel.HARD: 40,
            DifficultyLevel.PROFESSIONAL: 60,
        }
        prep_time = prep_times[difficulty]
        cook_time = cook_times[difficulty]
        total_time = prep_time + cook_time

        # Generate instructions
        instructions = self._generate_instructions(ingredients, cuisine, difficulty)

        # Equipment
        equipment = ["Chef's knife", "Cutting board", "Mixing bowls", "Measuring cups/spoons"]
        if dish_type in (DishType.MAIN_COURSE, DishType.SIDE_DISH):
            equipment.extend(["Saute pan or skillet", "Stove top"])
        if grain_names:
            equipment.append("Pot with lid")
        if difficulty in (DifficultyLevel.HARD, DifficultyLevel.PROFESSIONAL):
            equipment.extend(["Food thermometer", "Fine mesh sieve"])

        # Tips
        tips = [
            "Read the entire recipe before starting",
            "Prep all ingredients before cooking (mise en place)",
            "Taste and adjust seasoning before serving",
        ]
        if difficulty == DifficultyLevel.EASY:
            tips.append("This recipe is forgiving - adjust quantities to taste")
        if difficulty == DifficultyLevel.PROFESSIONAL:
            tips.append("Precision is key - use a kitchen scale for best results")

        # Dietary tags
        dietary_tags = []
        restrictions = data.get(
            "dietary_restrictions", self.config.get("dietary_restrictions", [])
        )
        if restrictions:
            dietary_tags.extend(restrictions)
        else:
            # Auto-detect
            if not any(i.category == "proteins" for i in ingredients if i.name.lower() not in ["tofu"]):
                dietary_tags.append("vegetarian")
            if not any(i.category == "dairy" for i in ingredients):
                dietary_tags.append("dairy_free")

        # Quality score
        quality_score = 7.0 + len(ingredients) * 0.1
        if difficulty == DifficultyLevel.PROFESSIONAL:
            quality_score += 1
        quality_score = min(10.0, quality_score)

        recipe = Recipe(
            id=recipe_id,
            name=recipe_name,
            cuisine=cuisine,
            dish_type=dish_type,
            description=(
                f"A {difficulty.value} {cuisine.value} {dish_type.value.replace('_', ' ')} "
                f"featuring {main_protein.lower()} with fresh vegetables and "
                f"authentic {cuisine.value} flavors."
            ),
            servings=servings,
            prep_time_min=prep_time,
            cook_time_min=cook_time,
            total_time_min=total_time,
            difficulty=difficulty,
            ingredients=ingredients,
            instructions=instructions,
            equipment=equipment,
            tips=tips,
            dietary_tags=dietary_tags,
            quality_score=round(quality_score, 1),
        )
        return recipe

    def _generate_instructions(
        self, ingredients: List[Ingredient], cuisine: CuisineType, difficulty: DifficultyLevel
    ) -> List[str]:
        """Generate cooking instructions."""
        instructions = []

        # Prep steps
        instructions.append(
            "Prepare all ingredients: wash vegetables, measure out seasonings, "
            "and portion proteins according to the ingredient list."
        )

        # Cutting/prep
        instructions.append(
            "Cut vegetables into uniform pieces for even cooking. "
            "Mince garlic and chop fresh herbs."
        )

        # Protein prep
        proteins = [i for i in ingredients if i.category == "proteins"]
        if proteins:
            instructions.append(
                f"Season {proteins[0].name.lower()} with salt and pepper. "
                "Let it come to room temperature for 15 minutes before cooking."
            )

        # Cooking main
        instructions.append(
            "Heat oil in a large pan over medium-high heat. "
            "Add protein and cook until golden on all sides, then set aside."
        )

        # Vegetable cooking
        instructions.append(
            "In the same pan, add vegetables and saute until tender-crisp, "
            "about 5-7 minutes. Start with harder vegetables first."
        )

        # Sauce/finishing
        instructions.append(
            "Return protein to the pan. Add any sauce ingredients "
            "and simmer until flavors meld together, about 3-5 minutes."
        )

        # Grain cooking
        grains = [i for i in ingredients if i.category == "grains"]
        if grains:
            instructions.insert(2,
                f"Cook {grains[0].name.lower()} according to package directions. "
                "Fluff with a fork and set aside."
            )

        # Plating
        instructions.append(
            "Plate the dish attractively. Garnish with fresh herbs. "
            "Serve immediately for best flavor and texture."
        )

        # Professional extra steps
        if difficulty in (DifficultyLevel.HARD, DifficultyLevel.PROFESSIONAL):
            instructions.insert(-1,
                "Taste and adjust seasoning. Add a finishing touch of "
                "high-quality oil or a sprinkle of finishing salt."
            )

        return instructions

    def _analyze_nutrition(self, recipe: Recipe) -> NutritionInfo:
        """Step 4: Analyze nutritional content per serving."""
        servings = recipe.servings
        total_calories = 0.0
        total_protein = 0.0
        total_fat = 0.0
        total_carbs = 0.0
        total_fiber = 0.0
        total_sodium = 0.0
        total_cholesterol = 0.0
        allergens = []

        for ingredient in recipe.ingredients:
            # Look up nutrition info from database
            found = False
            for cat, items in self._ingredient_db.items():
                if ingredient.name.lower() in items:
                    info = items[ingredient.name.lower()]
                    # Approximate: use per-lb nutrition and scale
                    multiplier = ingredient.quantity  # quantity in "units"
                    total_calories += info.get("calories", 0) * multiplier
                    total_protein += info.get("protein", 0) * multiplier
                    total_fat += info.get("fat", 0) * multiplier
                    found = True
                    break
            if not found:
                # Estimate
                if ingredient.category == "proteins":
                    total_calories += 150 * ingredient.quantity
                    total_protein += 25 * ingredient.quantity
                elif ingredient.category == "vegetables":
                    total_calories += 30 * ingredient.quantity
                    total_fiber += 2 * ingredient.quantity
                elif ingredient.category == "grains":
                    total_calories += 150 * ingredient.quantity
                    total_carbs += 30 * ingredient.quantity
                elif ingredient.category == "dairy":
                    total_calories += 100 * ingredient.quantity
                    total_fat += 10 * ingredient.quantity

            # Allergen detection
            name_lower = ingredient.name.lower()
            if any(d in name_lower for d in ["cheese", "butter", "cream", "milk"]):
                allergens.append("dairy")
            if any(n in name_lower for n in ["peanut", "walnut", "almond"]):
                allergens.append("nuts")
            if "wheat" in name_lower or "pasta" in name_lower or "bread" in name_lower:
                allergens.append("gluten")

        # Calculate carbs by difference (approximate)
        if total_carbs == 0 and total_calories > 0:
            remaining_cal = total_calories - (total_protein * 4 + total_fat * 9)
            total_carbs = max(0, remaining_cal / 4)

        # Sodium estimate
        total_sodium = 500 * len(recipe.ingredients) / 3

        # Per serving
        nutrition = NutritionInfo(
            calories=round(total_calories / servings, 0),
            protein_g=round(total_protein / servings, 1),
            carbohydrates_g=round(total_carbs / servings, 1),
            fat_g=round(total_fat / servings, 1),
            saturated_fat_g=round(total_fat * 0.3 / servings, 1),
            fiber_g=round(total_fiber / servings + 3, 1),
            sodium_mg=round(total_sodium / servings, 0),
            cholesterol_mg=round(50 / servings, 0) if any(
                i.category == "proteins" for i in recipe.ingredients
            ) else 0,
            vitamins=["Vitamin A", "Vitamin C", "Vitamin K"],
            minerals=["Iron", "Calcium", "Potassium"],
            allergens=list(set(allergens)),
        )
        return nutrition

    def _calculate_cost(self, recipe: Recipe) -> CostBreakdown:
        """Step 5: Calculate recipe costs and pricing."""
        total_cost = sum(i.total_cost for i in recipe.ingredients)
        cost_per_serving = total_cost / recipe.servings if recipe.servings > 0 else 0

        target_food_cost = self.config.get("target_food_cost", 30.0)
        suggested_price = round(cost_per_serving / (target_food_cost / 100), 2)
        margin = round(100 - target_food_cost, 1)

        # Most expensive items
        sorted_ingredients = sorted(
            recipe.ingredients, key=lambda i: i.total_cost, reverse=True
        )
        most_expensive = [
            f"{i.name}: ${i.total_cost:.2f}" for i in sorted_ingredients[:3]
        ]

        # Premium ingredients
        premium = [
            i.name for i in recipe.ingredients
            if i.cost_per_unit > 10.0
        ]

        # Cost saving tips
        tips = []
        if premium:
            tips.append(f"Consider more affordable alternatives for premium ingredients")
        tips.append("Buy in bulk for frequently used ingredients")
        tips.append("Use seasonal produce for better prices and quality")
        tips.append("Reduce food waste by repurposing trimmings and leftovers")

        return CostBreakdown(
            recipe_id=recipe.id,
            total_cost=round(total_cost, 2),
            cost_per_serving=round(cost_per_serving, 2),
            food_cost_percentage=target_food_cost,
            suggested_price=suggested_price,
            margin=margin,
            most_expensive_items=most_expensive,
            cost_saving_tips=tips,
            premium_ingredients=premium,
        )

    def _generate_plating_guide(self, recipe: Recipe) -> str:
        """Step 6: Generate plating and presentation guide."""
        dish = recipe.dish_type.value.replace("_", " ").title()
        cuisine = recipe.cuisine.value.title()

        guides = {
            DishType.MAIN_COURSE: (
                f"For this {cuisine} main course, plate the protein as the focal "
                "point slightly off-center. Arrange vegetables in a complementary "
                "arrangement. Spoon sauce underneath or around the protein. "
                "Garnish with fresh herbs and a sprinkle of finishing salt. "
                "Wipe the rim of the plate for a clean presentation."
            ),
            DishType.APPETIZER: (
                f"Present this {cuisine} appetizer on a small, attractive plate "
                "or platter. Create visual interest with height and color contrast. "
                "Use garnishes that are edible and enhance the flavor profile. "
                "Consider serving with a small dipping sauce on the side."
            ),
            DishType.DESSERT: (
                f"Plate this {cuisine} dessert with precision and artistry. "
                "Use a ring mold or stencil for clean shapes. Add a sauce "
                "drizzle for visual appeal. Top with a fresh garnish that "
                "provides color contrast. Serve on a chilled plate for "
                "cold desserts."
            ),
            DishType.SALAD: (
                f"Arrange this {cuisine} salad in a wide, shallow bowl or "
                "on a plate. Layer ingredients for visual appeal. "
                "Dress lightly just before serving. Top with a final "
                "sprinkle of cheese or herbs for color and texture."
            ),
            DishType.SOUP: (
                f"Serve this {cuisine} soup in a warm bowl. Garnish on top "
                "with a swirl of cream, fresh herbs, or crispy elements "
                "for texture contrast. Wipe the bowl rim clean. "
                "Serve with crusty bread on the side."
            ),
        }

        return guides.get(recipe.dish_type, guides[DishType.MAIN_COURSE])

    # ------------------------------------------------------------------
    # Agent prompts
    # ------------------------------------------------------------------

    def _build_executive_chef_prompt(self) -> str:
        """Build the system prompt for the executive chef role."""
        cuisine = self.config.get("cuisine", "italian")
        return (
            f"You are an executive chef specializing in {cuisine} cuisine. "
            "Develop recipes that balance flavor, technique, and practicality. "
            "Consider flavor profiles, texture combinations, and cooking "
            "methods to create dishes that are delicious and well-executed. "
            "Always think about balance - acid, fat, salt, heat, sweet, umami. "
            "Ensure recipes are clear, reproducible, and yield consistent results."
        )

    def _build_nutritionist_prompt(self) -> str:
        """Build the system prompt for the nutritionist role."""
        return (
            "You are a registered dietitian nutritionist. Analyze recipes "
            "for nutritional content, identify potential allergens, and "
            "suggest modifications for various dietary needs. Ensure "
            "nutritional balance across macronutrients and highlight key "
            "micronutrients. Provide clear, evidence-based recommendations "
            "for healthier alternatives without sacrificing flavor."
        )

    def _build_food_stylist_prompt(self) -> str:
        """Build the system prompt for the food stylist role."""
        return (
            "You are a professional food stylist and photographer. Advise on "
            "plating techniques, presentation, and visual appeal. Consider "
            "color harmony, texture contrast, height, and negative space. "
            "Recommend garnishes and serving vessels that enhance the dish. "
            "Ensure the presentation matches the cuisine style and dish type."
        )

    def _build_cost_analyst_prompt(self) -> str:
        """Build the system prompt for the cost analyst role."""
        target = self.config.get("target_food_cost", 30.0)
        return (
            "You are a restaurant cost analyst. Calculate precise recipe "
            "costs, determine optimal pricing strategies, and identify "
            f"opportunities to achieve the target food cost of {target}%. "
            "Analyze ingredient costs, suggest cost-saving alternatives, "
            "and ensure menu pricing supports desired profit margins. "
            "Consider portion control, waste reduction, and supplier optimization."
        )

    # ------------------------------------------------------------------
    # Metrics
    # ------------------------------------------------------------------

    def _update_culinary_metrics(self) -> None:
        """Update culinary-specific metrics."""
        if self._recipes:
            total = len(self._recipes)
            avg_quality = sum(r.quality_score for r in self._recipes.values()) / total
            self.culinary_metrics.recipe_quality = avg_quality

            avg_prep = sum(r.prep_time_min for r in self._recipes.values()) / total
            self.culinary_metrics.avg_prep_time = avg_prep

            avg_cook = sum(r.cook_time_min for r in self._recipes.values()) / total
            self.culinary_metrics.avg_cook_time = avg_cook

            # Nutritional balance estimate
            avg_cal = sum(r.nutrition.calories for r in self._recipes.values()) / total
            balance_score = min(10.0, 10 - abs(avg_cal - 500) / 100)
            self.culinary_metrics.nutritional_balance = round(balance_score, 1)

        self.culinary_metrics.cuisines_covered = len(self._cuisines_covered)

        # Cost efficiency
        if self._cost_analyses:
            total = len(self._cost_analyses)
            avg_margin = sum(c.margin for c in self._cost_analyses.values()) / total
            efficiency = min(10.0, avg_margin / 10)
            self.culinary_metrics.cost_efficiency = round(efficiency, 1)

    def get_culinary_metrics(self) -> Dict[str, Any]:
        """Get the current culinary metrics.

        Returns:
            Dictionary with culinary metrics including recipe quality,
            nutritional balance, cost efficiency, and development stats.
        """
        return self.culinary_metrics.to_dict()
