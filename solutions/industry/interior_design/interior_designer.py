"""
Interior Design AI Solution Template.

A comprehensive AI-powered interior design assistant for
design firms, architecture studios, and home furnishings.
Includes multi-agent roles for interior design, space planning,
color consultation, and furniture specification.

Typical workflow:
    client needs -> space planning -> color scheme
    -> furniture selection -> 3D visualization
"""

from typing import Optional, Dict, Any, List
from enum import Enum
from dataclasses import dataclass, field

from solutions.templates.base_solution import (
    BaseSolution,
    SolutionMetadata,
    SolutionMetrics,
)


class SpaceType(str, Enum):
    """Types of interior spaces."""
    LIVING_ROOM = "living_room"
    BEDROOM = "bedroom"
    KITCHEN = "kitchen"
    BATHROOM = "bathroom"
    OFFICE = "office"
    DINING_ROOM = "dining_room"
    HALLWAY = "hallway"
    OPEN_PLAN = "open_plan"
    WHOLE_HOME = "whole_home"
    COMMERCIAL = "commercial"
    HOSPITALITY = "hospitality"
    RETAIL = "retail"


class DesignStyle(str, Enum):
    """Interior design styles."""
    MODERN = "modern"
    CONTEMPORARY = "contemporary"
    TRADITIONAL = "traditional"
    MIDCENTURY = "midcentury_modern"
    INDUSTRIAL = "industrial"
    SCANDINAVIAN = "scandinavian"
    BOHEMIAN = "bohemian"
    FARMHOUSE = "farmhouse"
    MINIMALIST = "minimalist"
    MAXIMALIST = "maximalist"
    COASTAL = "coastal"
    JAPANDI = "japandi"
    ART_DECO = "art_deco"
    HOLLYWOOD_REGENCY = "hollywood_regency"


class BudgetRange(str, Enum):
    """Budget ranges for interior design."""
    BUDGET = "budget"
    MID_RANGE = "mid_range"
    HIGH_END = "high_end"
    LUXURY = "luxury"


class FurnitureCategory(str, Enum):
    """Categories of furniture and decor."""
    SEATING = "seating"
    TABLES = "tables"
    STORAGE = "storage"
    BEDDING = "bedding"
    LIGHTING = "lighting"
    RUGS = "rugs"
    WALL_DECOR = "wall_decor"
    WINDOW_TREATMENTS = "window_treatments"
    ACCESSORIES = "accessories"
    TEXTILES = "textiles"


class InteriorRole(str, Enum):
    """Agent roles in the interior design solution."""
    INTERIOR_DESIGNER = "interior_designer"
    SPACE_PLANNER = "space_planner"
    COLOR_CONSULTANT = "color_consultant"
    FURNITURE_SPECIFIER = "furniture_specifier"


@dataclass
class ClientNeeds:
    """Represents client requirements and preferences.

    Attributes:
        client_id: Unique client identifier.
        name: Client name.
        space_type: Type of space to design.
        space_dimensions: Room dimensions (width, length, height).
        square_footage: Total area in square feet.
        design_style: Preferred design style.
        budget_range: Budget range.
        color_preferences: Preferred colors.
        functional_requirements: Functional needs.
        existing_furniture: Existing furniture to incorporate.
        lifestyle_notes: Lifestyle and usage notes.
        timeline: Project timeline in weeks.
    """
    client_id: str
    name: str = ""
    space_type: SpaceType = SpaceType.LIVING_ROOM
    space_dimensions: Dict[str, float] = field(default_factory=dict)
    square_footage: float = 0.0
    design_style: DesignStyle = DesignStyle.MODERN
    budget_range: BudgetRange = BudgetRange.MID_RANGE
    color_preferences: List[str] = field(default_factory=list)
    functional_requirements: List[str] = field(default_factory=list)
    existing_furniture: List[str] = field(default_factory=list)
    lifestyle_notes: List[str] = field(default_factory=list)
    timeline: int = 8


@dataclass
class SpacePlan:
    """Represents a space planning layout.

    Attributes:
        id: Unique space plan identifier.
        client_id: Associated client ID.
        layout_style: Layout approach/strategy.
        furniture_arrangement: Furniture placement description.
        traffic_flow: Traffic circulation pattern.
        focal_points: Focal points in the room.
        zone_divisions: Different functional zones.
        measurements: Key measurements and clearances.
        accessibility_features: Accessibility considerations.
        spatial_efficiency: Space efficiency score (0-100).
        layout_variations: Alternative layout options.
        scale_proportion: Scale and proportion assessment.
    """
    id: str
    client_id: str = ""
    layout_style: str = ""
    furniture_arrangement: str = ""
    traffic_flow: str = ""
    focal_points: List[str] = field(default_factory=list)
    zone_divisions: List[str] = field(default_factory=list)
    measurements: Dict[str, float] = field(default_factory=dict)
    accessibility_features: List[str] = field(default_factory=list)
    spatial_efficiency: float = 0.0
    layout_variations: List[str] = field(default_factory=list)
    scale_proportion: str = ""


@dataclass
class ColorScheme:
    """Represents a color palette/scheme.

    Attributes:
        id: Unique color scheme identifier.
        client_id: Associated client ID.
        scheme_name: Name of the color scheme.
        primary_colors: Primary colors (walls, large surfaces).
        secondary_colors: Secondary colors (furniture, accents).
        accent_colors: Accent colors (decor, pillows, art).
        neutral_base: Neutral base colors.
        material_colors: Wood, metal, stone colors.
        mood_description: Mood/feeling created by the scheme.
        color_temperature: Warm or cool overall temperature.
        contrast_level: Level of contrast in the scheme.
        psychological_effects: Psychological effects of colors.
        paint_suggestions: Specific paint color recommendations.
    """
    id: str
    client_id: str = ""
    scheme_name: str = ""
    primary_colors: List[str] = field(default_factory=list)
    secondary_colors: List[str] = field(default_factory=list)
    accent_colors: List[str] = field(default_factory=list)
    neutral_base: List[str] = field(default_factory=list)
    material_colors: Dict[str, str] = field(default_factory=dict)
    mood_description: str = ""
    color_temperature: str = ""
    contrast_level: str = ""
    psychological_effects: List[str] = field(default_factory=list)
    paint_suggestions: List[str] = field(default_factory=list)


@dataclass
class FurnitureItem:
    """Represents a furniture/decor item specification.

    Attributes:
        item_id: Unique item identifier.
        name: Item name/description.
        category: Furniture category.
        style: Design style.
        dimensions: Dimensions (W x D x H).
        material: Primary materials.
        color: Color/finish.
        price_estimate: Estimated price.
        brand: Recommended brand/manufacturer.
        placement: Placement in the room.
        purpose: Functional purpose.
        alternatives: Alternative options.
        priority: Priority level (must-have vs nice-to-have).
    """
    item_id: str
    name: str = ""
    category: FurnitureCategory = FurnitureCategory.SEATING
    style: str = ""
    dimensions: Dict[str, float] = field(default_factory=dict)
    material: str = ""
    color: str = ""
    price_estimate: float = 0.0
    brand: str = ""
    placement: str = ""
    purpose: str = ""
    alternatives: List[str] = field(default_factory=list)
    priority: str = "must_have"


@dataclass
class DesignPackage:
    """Represents a complete design package.

    Attributes:
        id: Unique package identifier.
        client_id: Associated client ID.
        concept_summary: Overall design concept summary.
        design_board: Mood board/concept board items.
        total_budget_estimate: Estimated total project cost.
        implementation_phases: Phased implementation plan.
        shopping_list: Complete shopping list.
        diy_options: DIY-friendly alternatives.
        pro_tips: Professional tips and tricks.
        design_rating: Overall design quality rating (0-10).
        client_satisfaction_estimate: Estimated client satisfaction.
    """
    id: str
    client_id: str = ""
    concept_summary: str = ""
    design_board: List[str] = field(default_factory=list)
    total_budget_estimate: float = 0.0
    implementation_phases: List[str] = field(default_factory=list)
    shopping_list: List[str] = field(default_factory=list)
    diy_options: List[str] = field(default_factory=list)
    pro_tips: List[str] = field(default_factory=list)
    design_rating: float = 0.0
    client_satisfaction_estimate: float = 0.0


@dataclass
class InteriorMetrics(SolutionMetrics):
    """Extended metrics for the interior design solution.

    Attributes:
        client_satisfaction: Average client satisfaction score (0-5).
        design_quality: Overall design quality score (0-10).
        budget_adherence: Budget adherence percentage.
        projects_completed: Total design projects completed.
        avg_budget: Average project budget.
        color_scheme_quality: Color scheme quality rating.
        space_efficiency: Average space efficiency score.
        furniture_matching_rate: Furniture style matching rate.
        visualization_quality: 3D visualization quality rating.
        project_timeline_weeks: Average project timeline in weeks.
    """
    client_satisfaction: float = 0.0
    design_quality: float = 0.0
    budget_adherence: float = 0.0
    projects_completed: int = 0
    avg_budget: float = 0.0
    color_scheme_quality: float = 0.0
    space_efficiency: float = 0.0
    furniture_matching_rate: float = 0.0
    visualization_quality: float = 0.0
    project_timeline_weeks: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        """Convert interior design metrics to a dictionary."""
        base = super().to_dict()
        base.update({
            "client_satisfaction": self.client_satisfaction,
            "design_quality": self.design_quality,
            "budget_adherence": self.budget_adherence,
            "projects_completed": self.projects_completed,
            "avg_budget": self.avg_budget,
            "color_scheme_quality": self.color_scheme_quality,
            "space_efficiency": self.space_efficiency,
            "furniture_matching_rate": self.furniture_matching_rate,
            "visualization_quality": self.visualization_quality,
            "project_timeline_weeks": self.project_timeline_weeks,
        })
        return base


class InteriorDesignerSolution(BaseSolution):
    """Interior Design AI solution template.

    Provides a complete, pre-configured interior design solution
    with multi-agent collaboration for needs assessment, space
    planning, color consultation, furniture specification, and
    3D visualization guidance.

    Pre-configured agent roles:
        - Interior Designer: Develops overall design concepts and vision
        - Space Planner: Optimizes furniture layout and circulation
        - Color Consultant: Creates color palettes and material schemes
        - Furniture Specifier: Selects and specifies furniture and decor

    Workflow:
        1. Client Needs - Gather requirements, preferences, and budget
        2. Space Planning - Design furniture layout and circulation
        3. Color Scheme - Develop color palette and materials
        4. Furniture Selection - Specify furniture and decor items
        5. 3D Visualization - Guide visualization and presentation

    Configuration options:
        - space_type: Type of space to design
        - design_style: Preferred design style
        - budget_range: Budget range for the project
        - square_footage: Space size in square feet
        - quality_level: Quality/finish level
    """

    def _build_metadata(self) -> SolutionMetadata:
        """Build metadata for the interior designer solution."""
        return SolutionMetadata(
            name="interior-designer",
            version="1.0.0",
            industry="interior_design",
            description=(
                "AI-powered interior design assistant with space planning, "
                "color consultation, furniture specification, and 3D "
                "visualization guidance."
            ),
            author="LiuHao AI OS",
            tags=["interior-design", "space-planning", "color-scheme", "furniture", "3d-visualization"],
            min_liuhao_version="1.0.0",
        )

    def _build_config_schema(self) -> None:
        """Define configuration fields for the interior design solution."""
        self.config_schema.add_field(
            name="space_type",
            field_type="string",
            required=False,
            default="living_room",
            description="Type of space to design",
            valid_values=[
                "living_room", "bedroom", "kitchen", "bathroom",
                "office", "dining_room", "hallway", "open_plan",
                "whole_home", "commercial", "hospitality", "retail",
            ],
        )
        self.config_schema.add_field(
            name="design_style",
            field_type="string",
            required=False,
            default="modern",
            description="Preferred interior design style",
            valid_values=[
                "modern", "contemporary", "traditional", "midcentury_modern",
                "industrial", "scandinavian", "bohemian", "farmhouse",
                "minimalist", "maximalist", "coastal", "japandi",
                "art_deco", "hollywood_regency",
            ],
        )
        self.config_schema.add_field(
            name="budget_range",
            field_type="string",
            required=False,
            default="mid_range",
            description="Budget range for the project",
            valid_values=["budget", "mid_range", "high_end", "luxury"],
        )
        self.config_schema.add_field(
            name="square_footage",
            field_type="float",
            required=False,
            default=300.0,
            description="Space size in square feet",
        )
        self.config_schema.add_field(
            name="quality_level",
            field_type="string",
            required=False,
            default="good",
            description="Quality and finish level",
            valid_values=["basic", "good", "better", "best"],
        )
        self.config_schema.add_field(
            name="sustainable_focus",
            field_type="float",
            required=False,
            default=0.3,
            description="Level of sustainable design focus (0-1)",
        )
        self.config_schema.add_field(
            name="pet_friendly",
            field_type="bool",
            required=False,
            default=False,
            description="Whether to design for pets",
        )

    def _on_initialize(self) -> None:
        """Initialize interior design solution state."""
        self._agents: Dict[str, Dict[str, Any]] = {}
        self._client_needs: Dict[str, ClientNeeds] = {}
        self._space_plans: Dict[str, SpacePlan] = {}
        self._color_schemes: Dict[str, ColorScheme] = {}
        self._furniture_items: Dict[str, FurnitureItem] = {}
        self._design_packages: Dict[str, DesignPackage] = {}
        self._client_count = 0
        self._space_plan_count = 0
        self._color_count = 0
        self._furniture_count = 0
        self._package_count = 0

        # Initialize interior-specific metrics
        self.interior_metrics = InteriorMetrics()

        # Style color palettes
        self._style_palettes = self._build_style_palettes()

    def _build_style_palettes(self) -> Dict[str, Any]:
        """Build style-specific color palettes and characteristics."""
        return {
            DesignStyle.MODERN: {
                "colors": ["white", "gray", "black", "wood tones", "one accent color"],
                "neutrals": ["bright white", "cool gray", "charcoal"],
                "materials": ["glass", "metal", "leather", "polished wood"],
                "furniture_style": ["clean lines", "low profile", "geometric shapes"],
                "mood": "Sleek, uncluttered, sophisticated",
            },
            DesignStyle.MIDCENTURY: {
                "colors": ["olive green", "mustard yellow", "burnt orange", "teal", "warm wood"],
                "neutrals": ["warm white", "light oak", "walnut"],
                "materials": ["teak wood", "walnut", "brass", "wool", "linen"],
                "furniture_style": ["organic shapes", "tapered legs", "low seating"],
                "mood": "Warm, nostalgic, timeless",
            },
            DesignStyle.SCANDINAVIAN: {
                "colors": ["white", "soft gray", "muted blue", "blush pink", "light wood"],
                "neutrals": ["off-white", "birch", "ash gray"],
                "materials": ["light wood", "linen", "wool", "sheepskin", "ceramic"],
                "furniture_style": ["light wood frames", "cozy textiles", "functional beauty"],
                "mood": "Light, airy, cozy, hygge",
            },
            DesignStyle.INDUSTRIAL: {
                "colors": ["black", "gray", "brown", "rust", "exposed brick red"],
                "neutrals": ["concrete gray", "charcoal", "raw steel"],
                "materials": ["exposed brick", "concrete", "raw steel", "reclaimed wood", "leather"],
                "furniture_style": ["metal frames", "distressed wood", "utilitarian"],
                "mood": "Edgy, raw, urban, masculine",
            },
            DesignStyle.BOHEMIAN: {
                "colors": ["terracotta", "saffron", "indigo", "emerald", "gold"],
                "neutrals": ["cream", "sand", "warm beige"],
                "materials": ["rattan", "macrame", "velvet", "brass", "patterned textiles"],
                "furniture_style": ["layered textiles", "vintage pieces", "global influences"],
                "mood": "Eclectic, layered, free-spirited",
            },
            DesignStyle.FARMHOUSE: {
                "colors": ["white", "cream", "soft gray", "muted blue", "weathered wood"],
                "neutrals": ["antique white", "driftwood gray", "oatmeal"],
                "materials": ["shiplap", "reclaimed wood", "linen", "cast iron", "galvanized metal"],
                "furniture_style": ["distressed finishes", "slipcovers", "farm tables"],
                "mood": "Warm, welcoming, rustic, homey",
            },
            DesignStyle.MINIMALIST: {
                "colors": ["white", "one neutral", "one accent"],
                "neutrals": ["white", "light gray", "warm beige"],
                "materials": ["natural wood", "stone", "matte finishes"],
                "furniture_style": ["essential pieces only", "clean lines", "negative space"],
                "mood": "Calm, ordered, serene, uncluttered",
            },
            DesignStyle.JAPANDI: {
                "colors": ["warm white", "light wood", "moss green", "charcoal", "soft black"],
                "neutrals": ["warm white", "oak", "charcoal"],
                "materials": ["oak wood", "bamboo", "rice paper", "linen", "stone"],
                "furniture_style": ["low profile", "natural materials", "wabi-sabi aesthetic"],
                "mood": "Serene, balanced, mindful, minimalist with warmth",
            },
        }

    def _on_deploy(self) -> None:
        """Deploy interior design agents and workflows."""
        agent_configs = [
            (InteriorRole.INTERIOR_DESIGNER, {
                "name": "Interior Designer",
                "description": "Develops overall design concepts and vision",
                "system_prompt": self._build_interior_designer_prompt(),
                "tools": ["concept_development", "mood_boards", "styling"],
            }),
            (InteriorRole.SPACE_PLANNER, {
                "name": "Space Planner",
                "description": "Optimizes furniture layout and circulation",
                "system_prompt": self._build_space_planner_prompt(),
                "tools": ["furniture_layout", "circulation", "ergonomics"],
            }),
            (InteriorRole.COLOR_CONSULTANT, {
                "name": "Color Consultant",
                "description": "Creates color palettes and material schemes",
                "system_prompt": self._build_color_consultant_prompt(),
                "tools": ["color_theory", "material_selection", "paint_suggestions"],
            }),
            (InteriorRole.FURNITURE_SPECIFIER, {
                "name": "Furniture Specifier",
                "description": "Selects and specifies furniture and decor",
                "system_prompt": self._build_furniture_specifier_prompt(),
                "tools": ["furniture_selection", "product_research", "budget_management"],
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
        """Process an interior design request through the full workflow.

        Args:
            input_data: Dictionary containing client requirements.

        Returns:
            Dictionary with interior design results including client
            needs assessment, space plan, color scheme, furniture
            selections, and design package.
        """
        # Step 1: Client needs assessment
        client = self._assess_client_needs(input_data)

        # Step 2: Space planning
        space_plan = self._plan_space(client)

        # Step 3: Color scheme
        color_scheme = self._create_color_scheme(client)

        # Step 4: Furniture selection
        furniture = self._select_furniture(client, space_plan, color_scheme)

        # Step 5: Design package / visualization
        design_package = self._create_design_package(client, space_plan, color_scheme, furniture)

        # Store and update metrics
        self._client_needs[client.client_id] = client
        self._space_plans[space_plan.id] = space_plan
        self._color_schemes[color_scheme.id] = color_scheme
        self._design_packages[design_package.id] = design_package

        self.interior_metrics.projects_completed += 1
        self._update_interior_metrics()

        return {
            "client_needs": {
                "id": client.client_id,
                "name": client.name,
                "space_type": client.space_type.value,
                "square_footage": client.square_footage,
                "design_style": client.design_style.value,
                "budget_range": client.budget_range.value,
                "color_preferences": client.color_preferences,
                "functional_requirements": client.functional_requirements,
                "timeline_weeks": client.timeline,
            },
            "space_plan": {
                "id": space_plan.id,
                "layout_style": space_plan.layout_style,
                "furniture_arrangement": space_plan.furniture_arrangement,
                "traffic_flow": space_plan.traffic_flow,
                "focal_points": space_plan.focal_points,
                "zone_divisions": space_plan.zone_divisions,
                "spatial_efficiency": space_plan.spatial_efficiency,
                "layout_variations": space_plan.layout_variations,
                "accessibility_features": space_plan.accessibility_features,
            },
            "color_scheme": {
                "id": color_scheme.id,
                "scheme_name": color_scheme.scheme_name,
                "primary_colors": color_scheme.primary_colors,
                "secondary_colors": color_scheme.secondary_colors,
                "accent_colors": color_scheme.accent_colors,
                "neutral_base": color_scheme.neutral_base,
                "material_colors": color_scheme.material_colors,
                "mood_description": color_scheme.mood_description,
                "color_temperature": color_scheme.color_temperature,
                "contrast_level": color_scheme.contrast_level,
                "paint_suggestions": color_scheme.paint_suggestions,
            },
            "furniture": {
                "item_count": len(furniture),
                "items": [
                    {
                        "id": f.item_id,
                        "name": f.name,
                        "category": f.category.value,
                        "material": f.material,
                        "color": f.color,
                        "price_estimate": f.price_estimate,
                        "placement": f.placement,
                        "priority": f.priority,
                    }
                    for f in furniture
                ],
            },
            "design_package": {
                "id": design_package.id,
                "concept_summary": design_package.concept_summary,
                "total_budget_estimate": design_package.total_budget_estimate,
                "implementation_phases": design_package.implementation_phases,
                "design_rating": design_package.design_rating,
                "pro_tips": design_package.pro_tips,
                "diy_options": design_package.diy_options,
            },
        }

    def _on_teardown(self) -> None:
        """Clean up interior design solution resources."""
        self._agents.clear()
        self._client_needs.clear()
        self._space_plans.clear()
        self._color_schemes.clear()
        self._furniture_items.clear()
        self._design_packages.clear()
        self._client_count = 0
        self._space_plan_count = 0
        self._color_count = 0
        self._furniture_count = 0
        self._package_count = 0

    # ------------------------------------------------------------------
    # Workflow steps
    # ------------------------------------------------------------------

    def _assess_client_needs(self, data: Dict[str, Any]) -> ClientNeeds:
        """Step 1: Assess client needs and preferences."""
        self._client_count += 1
        client_id = f"client-{self._client_count}"

        try:
            space_type = SpaceType(data.get("space_type", self.config.get("space_type", "living_room")))
        except ValueError:
            space_type = SpaceType.LIVING_ROOM

        try:
            style = DesignStyle(data.get("design_style", self.config.get("design_style", "modern")))
        except ValueError:
            style = DesignStyle.MODERN

        try:
            budget = BudgetRange(data.get("budget_range", self.config.get("budget_range", "mid_range")))
        except ValueError:
            budget = BudgetRange.MID_RANGE

        sqft = data.get("square_footage", self.config.get("square_footage", 300.0))

        client = ClientNeeds(
            client_id=client_id,
            name=data.get("client_name", f"Client {self._client_count}"),
            space_type=space_type,
            space_dimensions=data.get("dimensions", {}),
            square_footage=sqft,
            design_style=style,
            budget_range=budget,
            color_preferences=data.get("color_preferences", []),
            functional_requirements=data.get("functional_requirements", []),
            existing_furniture=data.get("existing_furniture", []),
            lifestyle_notes=data.get("lifestyle_notes", []),
            timeline=data.get("timeline", 8),
        )
        self._client_needs[client_id] = client
        return client

    def _plan_space(self, client: ClientNeeds) -> SpacePlan:
        """Step 2: Plan the space layout."""
        self._space_plan_count += 1
        plan_id = f"space-{self._space_plan_count}"

        space_type = client.space_type
        style = client.design_style

        # Layout style by space type
        layout_styles = {
            SpaceType.LIVING_ROOM: "Conversational grouping centered around focal point",
            SpaceType.BEDROOM: "Bed centered with flanking nightstands and symmetrical balance",
            SpaceType.KITCHEN: "Work triangle (sink, stove, fridge) with efficient circulation",
            SpaceType.BATHROOM: "Vanity, toilet, and shower/bath with clear floor space",
            SpaceType.OFFICE: "Desk positioned for natural light, storage accessible",
            SpaceType.DINING_ROOM: "Dining table centered with equal circulation on all sides",
            SpaceType.OPEN_PLAN: "Zoned areas with visual connection and clear pathways",
        }
        layout_style = layout_styles.get(space_type, "Balanced furniture arrangement")

        # Furniture arrangement
        arrangements = {
            SpaceType.LIVING_ROOM: (
                "Sofa facing focal point (TV/fireplace), two chairs flanking, "
                "coffee table centered, side tables with lamps"
            ),
            SpaceType.BEDROOM: (
                "Queen/king bed against main wall, two nightstands, "
                "dresser opposite, seating area if space allows"
            ),
            SpaceType.KITCHEN: (
                "Cooking zone, prep zone, cleanup zone arranged in triangle, "
                "island for additional prep and seating"
            ),
        }
        furniture_arrangement = arrangements.get(space_type, layout_style)

        # Traffic flow
        traffic_flows = {
            SpaceType.LIVING_ROOM: "Entry to seating area, clear path to other rooms, 36\" minimum circulation",
            SpaceType.BEDROOM: "Entry to bed, access to closet and bathroom, 30\" minimum around bed",
            SpaceType.KITCHEN: "Primary work triangle, secondary path for multiple cooks, 48\" work aisles",
        }
        traffic_flow = traffic_flows.get(space_type, "Clear circulation paths with 36\" minimum width")

        # Focal points
        focal_points = {
            SpaceType.LIVING_ROOM: ["Fireplace or TV wall", "Large window with view", "Statement light fixture"],
            SpaceType.BEDROOM: ["Bed as focal element", "Feature wall behind bed", "Statement chandelier"],
            SpaceType.KITCHEN: ["Range hood and backsplash", "Island as centerpiece", "Statement lighting"],
            SpaceType.OFFICE: ["Desk and view", "Shelving/display wall"],
        }
        focal = focal_points.get(space_type, ["Primary feature wall"])

        # Zone divisions
        zones = {
            SpaceType.LIVING_ROOM: ["Seating/conversation zone", "Media/entertainment zone", "Reading nook"],
            SpaceType.BEDROOM: ["Sleep zone", "Dressing/closet zone", "Seating/reading zone"],
            SpaceType.OPEN_PLAN: ["Living zone", "Dining zone", "Kitchen zone"],
        }
        zone_divs = zones.get(space_type, ["Primary functional zone"])

        # Space efficiency
        sqft = client.square_footage
        efficiency = min(100.0, 70 + sqft / 100)
        if style in (DesignStyle.MINIMALIST, DesignStyle.SCANDINAVIAN):
            efficiency += 5
        if style == DesignStyle.MAXIMALIST:
            efficiency -= 10

        # Layout variations
        variations = [
            "Primary layout - optimized for daily use",
            "Alternative A - for entertaining/gatherings",
            "Alternative B - for media/relaxation focus",
        ]

        # Accessibility
        accessibility = [
            "36\" minimum door widths",
            "No-step transitions between rooms",
            "Lever-style door handles",
            "Dimmable lighting at multiple heights",
        ]

        plan = SpacePlan(
            id=plan_id,
            client_id=client.client_id,
            layout_style=layout_style,
            furniture_arrangement=furniture_arrangement,
            traffic_flow=traffic_flow,
            focal_points=focal,
            zone_divisions=zone_divs,
            measurements={"minimum_circulation_width_in": 36, "walkway_clearance_in": 30},
            accessibility_features=accessibility,
            spatial_efficiency=round(efficiency, 1),
            layout_variations=variations,
            scale_proportion="Furniture scaled to room size with appropriate negative space",
        )
        self._space_plans[plan_id] = plan
        return plan

    def _create_color_scheme(self, client: ClientNeeds) -> ColorScheme:
        """Step 3: Create color scheme."""
        self._color_count += 1
        color_id = f"color-{self._color_count}"

        style = client.design_style
        palette = self._style_palettes.get(style, self._style_palettes[DesignStyle.MODERN])

        # Primary/secondary/accent division
        primaries = palette["neutrals"][:2]
        secondaries = palette["colors"][:2]
        accents = palette["colors"][2:4] if len(palette["colors"]) > 2 else ["one accent"]

        # Material colors
        materials = {mat: mat for mat in palette["materials"][:4]}

        # Mood
        mood = palette["mood"]

        # Color temperature
        warm_styles = [DesignStyle.FARMHOUSE, DesignStyle.BOHEMIAN, DesignStyle.MIDCENTURY, DesignStyle.JAPANDI]
        cool_styles = [DesignStyle.MODERN, DesignStyle.MINIMALIST, DesignStyle.INDUSTRIAL]
        if style in warm_styles:
            temperature = "Warm"
        elif style in cool_styles:
            temperature = "Cool"
        else:
            temperature = "Balanced warm/cool"

        # Contrast level
        contrast_levels = {
            DesignStyle.MINIMALIST: "Low contrast - subtle differences",
            DesignStyle.MAXIMALIST: "High contrast - bold combinations",
            DesignStyle.MODERN: "Moderate contrast - clear definition",
            DesignStyle.INDUSTRIAL: "High contrast - dark and light",
            DesignStyle.SCANDINAVIAN: "Low-moderate contrast - soft transitions",
        }
        contrast = contrast_levels.get(style, "Moderate contrast")

        # Psychological effects
        psychological = [
            "Warm tones create cozy, inviting atmosphere",
            "Cool tones promote calm and focus",
            "Neutral base provides visual rest and versatility",
            "Accent colors add energy and focal points",
        ]

        # Paint suggestions
        paint_suggestions = [
            f"Walls: {palette['neutrals'][0]} - matte finish",
            f"Trim/Ceiling: {palette['neutrals'][1]} - satin finish",
            f"Accent wall: {palette['colors'][0]} - eggshell finish",
        ]

        scheme_name = f"{style.value.replace('_', ' ').title()} {client.space_type.value.replace('_', ' ').title()} Scheme"

        scheme = ColorScheme(
            id=color_id,
            client_id=client.client_id,
            scheme_name=scheme_name,
            primary_colors=primaries,
            secondary_colors=secondaries,
            accent_colors=accents,
            neutral_base=palette["neutrals"],
            material_colors=materials,
            mood_description=mood,
            color_temperature=temperature,
            contrast_level=contrast,
            psychological_effects=psychological,
            paint_suggestions=paint_suggestions,
        )
        self._color_schemes[color_id] = scheme
        return scheme

    def _select_furniture(
        self,
        client: ClientNeeds,
        space_plan: SpacePlan,
        color_scheme: ColorScheme,
    ) -> List[FurnitureItem]:
        """Step 4: Select furniture and decor items."""
        style = client.design_style
        space_type = client.space_type
        budget = client.budget_range

        # Budget multipliers
        budget_multipliers = {
            BudgetRange.BUDGET: 0.5,
            BudgetRange.MID_RANGE: 1.0,
            BudgetRange.HIGH_END: 2.0,
            BudgetRange.LUXURY: 4.0,
        }
        mult = budget_multipliers.get(budget, 1.0)

        # Furniture by space type
        space_furniture = {
            SpaceType.LIVING_ROOM: [
                {"name": "Sofa", "category": FurnitureCategory.SEATING, "price": 1500, "material": "fabric", "placement": "Against main wall, facing focal point"},
                {"name": "Coffee Table", "category": FurnitureCategory.TABLES, "price": 400, "material": "wood/glass", "placement": "Centered in seating group"},
                {"name": "Armchair (x2)", "category": FurnitureCategory.SEATING, "price": 600, "material": "fabric/leather", "placement": "Flanking sofa or in conversation area"},
                {"name": "Side Table (x2)", "category": FurnitureCategory.TABLES, "price": 200, "material": "wood/metal", "placement": "Beside seating pieces"},
                {"name": "Media Console", "category": FurnitureCategory.STORAGE, "price": 800, "material": "wood", "placement": "Below TV on focal wall"},
                {"name": "Area Rug", "category": FurnitureCategory.RUGS, "price": 500, "material": "wool/synthetic", "placement": "Under seating group"},
                {"name": "Floor Lamp", "category": FurnitureCategory.LIGHTING, "price": 250, "material": "metal/fabric", "placement": "Corner or beside chair"},
                {"name": "Table Lamp (x2)", "category": FurnitureCategory.LIGHTING, "price": 150, "material": "metal/glass", "placement": "On side tables"},
                {"name": "Wall Art", "category": FurnitureCategory.WALL_DECOR, "price": 300, "material": "canvas/framed", "placement": "Above sofa or on feature wall"},
                {"name": "Throw Pillows", "category": FurnitureCategory.ACCESSORIES, "price": 80, "material": "various textiles", "placement": "On sofa and chairs"},
            ],
            SpaceType.BEDROOM: [
                {"name": "Bed Frame", "category": FurnitureCategory.BEDDING, "price": 1200, "material": "wood/upholstered", "placement": "Centered on main wall"},
                {"name": "Nightstand (x2)", "category": FurnitureCategory.TABLES, "price": 300, "material": "wood", "placement": "Flanking bed"},
                {"name": "Dresser", "category": FurnitureCategory.STORAGE, "price": 900, "material": "wood", "placement": "Opposite bed wall"},
                {"name": "Area Rug", "category": FurnitureCategory.RUGS, "price": 400, "material": "wool/synthetic", "placement": "Under bed, extending on sides"},
                {"name": "Pendant Light", "category": FurnitureCategory.LIGHTING, "price": 350, "material": "metal/glass", "placement": "Centered over bed or nightstands"},
                {"name": "Accent Chair", "category": FurnitureCategory.SEATING, "price": 400, "material": "fabric", "placement": "Corner or by window"},
            ],
            SpaceType.OFFICE: [
                {"name": "Desk", "category": FurnitureCategory.TABLES, "price": 800, "material": "wood/metal", "placement": "Facing window or wall"},
                {"name": "Office Chair", "category": FurnitureCategory.SEATING, "price": 500, "material": "mesh/fabric", "placement": "At desk, ergonomic position"},
                {"name": "Bookshelf", "category": FurnitureCategory.STORAGE, "price": 600, "material": "wood/metal", "placement": "Against wall"},
                {"name": "Desk Lamp", "category": FurnitureCategory.LIGHTING, "price": 150, "material": "metal", "placement": "On desk, task lighting"},
                {"name": "Area Rug", "category": FurnitureCategory.RUGS, "price": 300, "material": "low-pile", "placement": "Under desk area"},
            ],
        }

        items_data = space_furniture.get(space_type, space_furniture[SpaceType.LIVING_ROOM])
        furniture = []

        for idx, item_data in enumerate(items_data):
            self._furniture_count += 1
            item_id = f"furn-{self._furniture_count}"

            price = round(item_data["price"] * mult, 2)
            priority = "must_have" if idx < 5 else "nice_to_have"

            item = FurnitureItem(
                item_id=item_id,
                name=item_data["name"],
                category=item_data["category"],
                style=style.value.replace("_", " ").title(),
                dimensions={
                    "width_in": 48 + idx * 10,
                    "depth_in": 24 + idx * 5,
                    "height_in": 30 + idx * 3,
                },
                material=item_data["material"],
                color=color_scheme.secondary_colors[idx % len(color_scheme.secondary_colors)] if color_scheme.secondary_colors else "neutral",
                price_estimate=price,
                brand=f"{style.value.title()} Collection",
                placement=item_data["placement"],
                purpose=f"Essential {item_data['category'].value.replace('_', ' ')} for {space_type.value.replace('_', ' ')}",
                alternatives=[f"Alternative style {i+1}" for i in range(2)],
                priority=priority,
            )
            furniture.append(item)
            self._furniture_items[item_id] = item

        return furniture

    def _create_design_package(
        self,
        client: ClientNeeds,
        space_plan: SpacePlan,
        color_scheme: ColorScheme,
        furniture: List[FurnitureItem],
    ) -> DesignPackage:
        """Step 5: Create complete design package."""
        self._package_count += 1
        package_id = f"pkg-{self._package_count}"

        # Concept summary
        concept = (
            f"A {client.design_style.value.replace('_', ' ')} "
            f"{client.space_type.value.replace('_', ' ')} design featuring "
            f"{color_scheme.mood_description.lower()}. "
            f"The space is organized with {space_plan.layout_style.lower()}, "
            f"creating a {color_scheme.color_temperature.lower()} atmosphere "
            f"with {color_scheme.contrast_level.lower()}."
        )

        # Design board items
        design_board = [
            f"Inspiration: {client.design_style.value.replace('_', ' ')} interiors",
            f"Color palette: {', '.join(color_scheme.primary_colors + color_scheme.accent_colors)}",
            f"Key piece: {furniture[0].name if furniture else 'Statement furniture'}",
            f"Material mix: {', '.join(list(color_scheme.material_colors.keys())[:3])}",
            f"Lighting: Layered ambient, task, and accent lighting",
            f"Textiles: {color_scheme.material_colors.get('linen', 'mixed fabrics')}",
        ]

        # Total budget estimate
        total_furniture = sum(f.price_estimate for f in furniture)
        # Add other costs (paint, installation, accessories, etc.)
        other_costs = total_furniture * 0.3
        total_budget = total_furniture + other_costs

        # Implementation phases
        phases = [
            "Phase 1: Planning and procurement (1-2 weeks)",
            "Phase 2: Prep and painting (1 week)",
            "Phase 3: Furniture delivery and assembly (1 week)",
            "Phase 4: Styling and finishing touches (2-3 days)",
            "Phase 5: Final review and adjustments (1 day)",
        ]

        # Pro tips
        pro_tips = [
            "Always test paint colors on the wall before committing",
            "Measure doorways and stairwells before ordering large furniture",
            "Layer lighting at different heights for depth and warmth",
            "Add personal items and art to make the space feel lived-in",
            "Leave negative space - you don't need to fill every corner",
        ]

        # DIY options
        diy_options = [
            "Paint walls yourself to save on labor costs",
            "Refinish thrifted furniture for budget-friendly unique pieces",
            "Create DIY wall art using canvas and acrylic paints",
            "Sew your own throw pillows with inexpensive fabric",
            "Install peel-and-stick wallpaper for rent-friendly updates",
        ]

        # Design rating
        base_rating = 7.5
        if client.budget_range in (BudgetRange.HIGH_END, BudgetRange.LUXURY):
            base_rating += 1.0
        if space_plan.spatial_efficiency > 80:
            base_rating += 0.5
        design_rating = min(10.0, base_rating)

        package = DesignPackage(
            id=package_id,
            client_id=client.client_id,
            concept_summary=concept,
            design_board=design_board,
            total_budget_estimate=round(total_budget, 2),
            implementation_phases=phases,
            shopping_list=[f.name for f in furniture],
            diy_options=diy_options,
            pro_tips=pro_tips,
            design_rating=round(design_rating, 1),
            client_satisfaction_estimate=round(design_rating * 0.5, 1),
        )
        self._design_packages[package_id] = package
        return package

    # ------------------------------------------------------------------
    # Agent prompts
    # ------------------------------------------------------------------

    def _build_interior_designer_prompt(self) -> str:
        """Build the system prompt for the interior designer role."""
        style = self.config.get("design_style", "modern").replace("_", " ")
        return (
            f"You are an interior designer specializing in {style} design. "
            "Create cohesive, functional, and beautiful interiors that reflect "
            "client personality while maintaining design integrity. Balance "
            "aesthetics with practicality, considering how the space will "
            "actually be lived in. Develop complete design concepts with "
            "attention to scale, proportion, color, texture, and light. "
            "Every element should have a purpose and contribute to the whole."
        )

    def _build_space_planner_prompt(self) -> str:
        """Build the system prompt for the space planner role."""
        return (
            "You are an expert space planner and ergonomics specialist. "
            "Design furniture layouts that optimize circulation, function, "
            "and comfort. Consider traffic patterns, sight lines, focal "
            "points, and zoning. Ensure clear pathways, appropriate clearances, "
            "and ergonomic furniture placement. Maximize the utility of every "
            "square foot while maintaining visual balance and comfort."
        )

    def _build_color_consultant_prompt(self) -> str:
        """Build the system prompt for the color consultant role."""
        return (
            "You are a color consultant with deep knowledge of color theory, "
            "color psychology, and material finishes. Create harmonious color "
            "palettes that set the desired mood while considering room size, "
            "natural light, and function. Specify paint colors, material "
            "finishes, and textile colors that work together beautifully. "
            "Consider both the aesthetic and psychological effects of color."
        )

    def _build_furniture_specifier_prompt(self) -> str:
        """Build the system prompt for the furniture specifier role."""
        budget = self.config.get("budget_range", "mid_range").replace("_", " ")
        return (
            f"You are a furniture specification expert with knowledge of "
            f"{budget} market products. Source furniture and decor that "
            "matches the design style, fits the space, and meets budget "
            "requirements. Consider quality, durability, comfort, and style. "
            "Provide multiple options at different price points and ensure "
            "all pieces work together cohesively in the overall scheme."
        )

    # ------------------------------------------------------------------
    # Metrics
    # ------------------------------------------------------------------

    def _update_interior_metrics(self) -> None:
        """Update interior design-specific metrics."""
        if self._design_packages:
            total = len(self._design_packages)
            avg_rating = sum(p.design_rating for p in self._design_packages.values()) / total
            self.interior_metrics.design_quality = avg_rating
            self.interior_metrics.client_satisfaction = avg_rating / 2  # Scale to 0-5

            avg_budget = sum(
                p.total_budget_estimate for p in self._design_packages.values()
            ) / total
            self.interior_metrics.avg_budget = avg_budget

        if self._space_plans:
            total = len(self._space_plans)
            avg_eff = sum(s.spatial_efficiency for s in self._space_plans.values()) / total
            self.interior_metrics.space_efficiency = avg_eff

        if self._color_schemes:
            # Color scheme quality estimate
            self.interior_metrics.color_scheme_quality = 8.0

        if self._furniture_items:
            # Furniture matching rate
            self.interior_metrics.furniture_matching_rate = 85.0

        # Visualization quality
        quality_level = self.config.get("quality_level", "good")
        viz_quality = {
            "basic": 6.0,
            "good": 7.5,
            "better": 8.5,
            "best": 9.5,
        }.get(quality_level, 7.5)
        self.interior_metrics.visualization_quality = viz_quality

        # Budget adherence
        self.interior_metrics.budget_adherence = 92.0  # Estimate

        # Timeline
        if self._client_needs:
            timelines = [c.timeline for c in self._client_needs.values()]
            self.interior_metrics.project_timeline_weeks = sum(timelines) / len(timelines)

    def get_interior_metrics(self) -> Dict[str, Any]:
        """Get the current interior design metrics.

        Returns:
            Dictionary with interior design metrics including client
            satisfaction, design quality, budget adherence, and space efficiency.
        """
        return self.interior_metrics.to_dict()
