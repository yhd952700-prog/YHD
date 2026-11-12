"""
Fashion Designer AI Solution Template.

A comprehensive AI-powered fashion design assistant for
fashion brands, design studios, and apparel companies.
Includes multi-agent roles for fashion design, trend analysis,
pattern making, and quality control.

Typical workflow:
    trend research -> design concept -> pattern creation
    -> prototyping -> quality assurance
"""

from typing import Optional, Dict, Any, List
from enum import Enum
from dataclasses import dataclass, field

from solutions.templates.base_solution import (
    BaseSolution,
    SolutionMetadata,
    SolutionMetrics,
)


class FashionCategory(str, Enum):
    """Categories of fashion/apparel."""
    WOMENSWEAR = "womenswear"
    MENSWEAR = "menswear"
    CHILDRENSWEAR = "childrenswear"
    ACTIVEWEAR = "activewear"
    FORMAL_WEAR = "formal_wear"
    CASUAL_WEAR = "casual_wear"
    ACCESSORIES = "accessories"
    FOOTWEAR = "footwear"
    OUTERWEAR = "outerwear"
    KNITWEAR = "knitwear"
    LINGERIE = "lingerie"
    SUSTAINABLE = "sustainable"


class Season(str, Enum):
    """Fashion seasons."""
    SPRING_SUMMER = "spring_summer"
    FALL_WINTER = "fall_winter"
    RESORT = "resort"
    HOLIDAY = "holiday"
    PRE_FALL = "pre_fall"
    PRE_SPRING = "pre_spring"


class TrendLevel(str, Enum):
    """Trend popularity levels."""
    EMERGING = "emerging"
    RISING = "rising"
    MAINSTREAM = "mainstream"
    PEAK = "peak"
    DECLINING = "declining"


class FabricType(str, Enum):
    """Types of fabrics."""
    COTTON = "cotton"
    WOOL = "wool"
    SILK = "silk"
    LINEN = "linen"
    POLYESTER = "polyester"
    NYLON = "nylon"
    VISCOSE = "viscose"
    LEATHER = "leather"
    DENIM = "denim"
    CASHMERE = "cashmere"
    ORGANIC_COTTON = "organic_cotton"
    RECYCLED = "recycled"


class FashionRole(str, Enum):
    """Agent roles in the fashion design solution."""
    FASHION_DESIGNER = "fashion_designer"
    TREND_ANALYST = "trend_analyst"
    PATTERN_MAKER = "pattern_maker"
    QUALITY_CONTROL = "quality_control"


@dataclass
class TrendReport:
    """Represents a fashion trend analysis report.

    Attributes:
        id: Unique report identifier.
        season: Season for the trend analysis.
        category: Fashion category.
        key_trends: Key trends identified.
        color_trends: Color trends and palettes.
        silhouette_trends: Silhouette and shape trends.
        fabric_trends: Fabric and material trends.
        detail_trends: Detail and embellishment trends.
        trend_levels: Trend maturity levels.
        runway_influence: Runway and influencer data.
        market_demand: Market demand indicators.
        sustainability_trends: Sustainable fashion trends.
    """
    id: str
    season: Season = Season.SPRING_SUMMER
    category: FashionCategory = FashionCategory.WOMENSWEAR
    key_trends: List[str] = field(default_factory=list)
    color_trends: List[str] = field(default_factory=list)
    silhouette_trends: List[str] = field(default_factory=list)
    fabric_trends: List[str] = field(default_factory=list)
    detail_trends: List[str] = field(default_factory=list)
    trend_levels: Dict[str, str] = field(default_factory=dict)
    runway_influence: List[str] = field(default_factory=list)
    market_demand: str = ""
    sustainability_trends: List[str] = field(default_factory=list)


@dataclass
class DesignConcept:
    """Represents a fashion design concept.

    Attributes:
        id: Unique design identifier.
        name: Design/style name.
        category: Fashion category.
        description: Design description.
        inspiration: Sources of inspiration.
        target_demographic: Target customer profile.
        color_palette: Color palette for the design.
        silhouette: Silhouette description.
        fabric_suggestions: Recommended fabrics.
        embellishments: Embellishment and detail ideas.
        collection_theme: Collection theme/concept.
        price_point: Target retail price point.
        design_rating: Design quality rating (0-10).
    """
    id: str
    name: str = ""
    category: FashionCategory = FashionCategory.WOMENSWEAR
    description: str = ""
    inspiration: List[str] = field(default_factory=list)
    target_demographic: str = ""
    color_palette: List[str] = field(default_factory=list)
    silhouette: str = ""
    fabric_suggestions: List[str] = field(default_factory=list)
    embellishments: List[str] = field(default_factory=list)
    collection_theme: str = ""
    price_point: str = ""
    design_rating: float = 0.0


@dataclass
class PatternSpec:
    """Represents a pattern specification.

    Attributes:
        id: Unique pattern identifier.
        design_id: Associated design ID.
        pattern_name: Pattern style name.
        size_range: Available size range.
        pattern_pieces: List of pattern pieces.
        grain_lines: Grain line specifications.
        seam_allowance: Seam allowance measurement.
        difficulty: Pattern difficulty level.
        construction_notes: Construction notes and techniques.
        sizing_method: Sizing methodology used.
        yardage_required: Fabric yardage required.
        notions_required: Notions and supplies needed.
        pattern_efficiency: Pattern efficiency score (0-10).
    """
    id: str
    design_id: str = ""
    pattern_name: str = ""
    size_range: str = ""
    pattern_pieces: List[str] = field(default_factory=list)
    grain_lines: Dict[str, str] = field(default_factory=dict)
    seam_allowance: str = ""
    difficulty: str = "medium"
    construction_notes: List[str] = field(default_factory=list)
    sizing_method: str = ""
    yardage_required: float = 0.0
    notions_required: List[str] = field(default_factory=list)
    pattern_efficiency: float = 0.0


@dataclass
class QualityCheck:
    """Represents a quality control check.

    Attributes:
        id: Unique quality check identifier.
        design_id: Associated design ID.
        overall_rating: Overall quality rating (0-100).
        construction_quality: Construction quality score.
        fabric_quality: Fabric quality assessment.
        fit_accuracy: Fit accuracy score.
        stitching_quality: Stitching quality score.
        finishing_quality: Finishing quality score.
        compliance_checks: Compliance and safety checks.
        defects_found: List of identified defects.
        passed: Whether quality check passed.
        rework_needed: Items requiring rework.
        sustainability_verification: Sustainable sourcing verification.
    """
    id: str
    design_id: str = ""
    overall_rating: float = 0.0
    construction_quality: float = 0.0
    fabric_quality: float = 0.0
    fit_accuracy: float = 0.0
    stitching_quality: float = 0.0
    finishing_quality: float = 0.0
    compliance_checks: List[str] = field(default_factory=list)
    defects_found: List[str] = field(default_factory=list)
    passed: bool = True
    rework_needed: List[str] = field(default_factory=list)
    sustainability_verification: str = ""


@dataclass
class FashionMetrics(SolutionMetrics):
    """Extended metrics for the fashion design solution.

    Attributes:
        design_quality: Average design quality score (0-10).
        trend_accuracy: Trend prediction accuracy rate.
        production_efficiency: Production efficiency score (0-100).
        designs_created: Total designs created.
        patterns_generated: Total patterns generated.
        avg_design_rating: Average design rating.
        quality_pass_rate: Quality check pass rate.
        sustainability_score: Sustainability score (0-100).
        collection_size: Average collection size.
        time_to_market: Average time from concept to sample.
    """
    design_quality: float = 0.0
    trend_accuracy: float = 0.0
    production_efficiency: float = 0.0
    designs_created: int = 0
    patterns_generated: int = 0
    avg_design_rating: float = 0.0
    quality_pass_rate: float = 0.0
    sustainability_score: float = 0.0
    collection_size: float = 0.0
    time_to_market: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        """Convert fashion metrics to a dictionary."""
        base = super().to_dict()
        base.update({
            "design_quality": self.design_quality,
            "trend_accuracy": self.trend_accuracy,
            "production_efficiency": self.production_efficiency,
            "designs_created": self.designs_created,
            "patterns_generated": self.patterns_generated,
            "avg_design_rating": self.avg_design_rating,
            "quality_pass_rate": self.quality_pass_rate,
            "sustainability_score": self.sustainability_score,
            "collection_size": self.collection_size,
            "time_to_market": self.time_to_market,
        })
        return base


class FashionDesignerSolution(BaseSolution):
    """Fashion Designer AI solution template.

    Provides a complete, pre-configured fashion design solution
    with multi-agent collaboration for trend research, design
    concepts, pattern making, prototyping, and quality assurance.

    Pre-configured agent roles:
        - Fashion Designer: Creates design concepts and collections
        - Trend Analyst: Researches and forecasts fashion trends
        - Pattern Maker: Develops patterns and technical specifications
        - Quality Control: Ensures quality standards and compliance

    Workflow:
        1. Trend Research - Analyze current and emerging trends
        2. Design Concept - Develop design concepts and collection themes
        3. Pattern Creation - Create technical patterns and specs
        4. Prototyping - Guide sample development and fitting
        5. Quality Assurance - Verify quality and compliance standards

    Configuration options:
        - fashion_category: Fashion/apparel category
        - season: Season for design
        - price_point: Target price point
        - sustainability_focus: Level of sustainability focus
        - collection_size: Number of pieces in collection
    """

    def _build_metadata(self) -> SolutionMetadata:
        """Build metadata for the fashion designer solution."""
        return SolutionMetadata(
            name="fashion-designer",
            version="1.0.0",
            industry="fashion",
            description=(
                "AI-powered fashion design assistant with trend forecasting, "
                "design concepts, pattern making, and quality assurance."
            ),
            author="LiuHao AI OS",
            tags=["fashion", "design", "trends", "pattern-making", "quality", "sustainability"],
            min_liuhao_version="1.0.0",
        )

    def _build_config_schema(self) -> None:
        """Define configuration fields for the fashion designer solution."""
        self.config_schema.add_field(
            name="fashion_category",
            field_type="string",
            required=False,
            default="womenswear",
            description="Fashion/apparel category",
            valid_values=[
                "womenswear", "menswear", "childrenswear", "activewear",
                "formal_wear", "casual_wear", "accessories", "footwear",
                "outerwear", "knitwear", "lingerie", "sustainable",
            ],
        )
        self.config_schema.add_field(
            name="season",
            field_type="string",
            required=False,
            default="spring_summer",
            description="Fashion season",
            valid_values=[
                "spring_summer", "fall_winter", "resort", "holiday",
                "pre_fall", "pre_spring",
            ],
        )
        self.config_schema.add_field(
            name="price_point",
            field_type="string",
            required=False,
            default="contemporary",
            description="Target price point/market positioning",
            valid_values=["budget", "mass_market", "contemporary", "premium", "luxury"],
        )
        self.config_schema.add_field(
            name="sustainability_focus",
            field_type="float",
            required=False,
            default=0.5,
            description="Level of sustainability focus (0-1)",
        )
        self.config_schema.add_field(
            name="collection_size",
            field_type="int",
            required=False,
            default=12,
            description="Number of pieces in the collection",
        )
        self.config_schema.add_field(
            name="target_market",
            field_type="string",
            required=False,
            default="global",
            description="Target geographic market",
            valid_values=["us", "eu", "asia", "global"],
        )
        self.config_schema.add_field(
            name="quality_standard",
            field_type="string",
            required=False,
            default="premium",
            description="Quality standard level",
            valid_values=["basic", "good", "premium", "luxury"],
        )

    def _on_initialize(self) -> None:
        """Initialize fashion design solution state."""
        self._agents: Dict[str, Dict[str, Any]] = {}
        self._trend_reports: Dict[str, TrendReport] = {}
        self._designs: Dict[str, DesignConcept] = {}
        self._patterns: Dict[str, PatternSpec] = {}
        self._quality_checks: Dict[str, QualityCheck] = {}
        self._trend_count = 0
        self._design_count = 0
        self._pattern_count = 0
        self._quality_count = 0

        # Initialize fashion-specific metrics
        self.fashion_metrics = FashionMetrics()

        # Trend database
        self._trend_database = self._build_trend_database()

    def _build_trend_database(self) -> Dict[str, Any]:
        """Build fashion trend database by category and season."""
        return {
            FashionCategory.WOMENSWEAR: {
                "colors": ["butter yellow", "lilac", "sage green", "cobalt blue", "chocolate brown"],
                "silhouettes": ["oversized blazers", "midi skirts", "wide leg trousers", "structured dresses"],
                "fabrics": ["linen blend", "satin", "chunky knits", "leather", "organic cotton"],
                "details": ["statement sleeves", "ruffles", "cut-outs", "bold hardware", "asymmetric hems"],
            },
            FashionCategory.MENSWEAR: {
                "colors": ["navy", "camel", "forest green", "burgundy", "charcoal"],
                "silhouettes": ["relaxed fit suits", "overshirts", "wide leg pants", "boxy tees"],
                "fabrics": ["wool blend", "denim", "corduroy", "heavy cotton", "cashmere"],
                "details": ["utility pockets", "workwear details", "minimal branding", "layering pieces"],
            },
            FashionCategory.ACTIVEWEAR: {
                "colors": ["neon accents", "earthy tones", "pastels", "monochrome black"],
                "silhouettes": ["high-waisted leggings", "oversized hoodies", "sports bras", "track pants"],
                "fabrics": ["moisture-wicking polyester", "recycled nylon", "cotton blend", "merino wool"],
                "details": ["seamless construction", "mesh panels", "compression fit", "reflective details"],
            },
            FashionCategory.SUSTAINABLE: {
                "colors": ["natural dyes", "earth tones", "indigo", "plant-based colors"],
                "silhouettes": ["timeless silhouettes", "modular designs", "gender neutral", "size inclusive"],
                "fabrics": ["organic cotton", "recycled polyester", "hemp", "tencel", "deadstock"],
                "details": ["zero waste patterns", "upcycled details", "repair kits", "take-back programs"],
            },
        }

    def _on_deploy(self) -> None:
        """Deploy fashion design agents and workflows."""
        agent_configs = [
            (FashionRole.FASHION_DESIGNER, {
                "name": "Fashion Designer",
                "description": "Creates design concepts and collections",
                "system_prompt": self._build_fashion_designer_prompt(),
                "tools": ["sketch_generation", "collection_planning", "fabric_selection"],
            }),
            (FashionRole.TREND_ANALYST, {
                "name": "Trend Analyst",
                "description": "Researches and forecasts fashion trends",
                "system_prompt": self._build_trend_analyst_prompt(),
                "tools": ["runway_analysis", "street_style", "consumer_research"],
            }),
            (FashionRole.PATTERN_MAKER, {
                "name": "Pattern Maker",
                "description": "Develops patterns and technical specifications",
                "system_prompt": self._build_pattern_maker_prompt(),
                "tools": ["pattern_drafting", "grading", "marker_making"],
            }),
            (FashionRole.QUALITY_CONTROL, {
                "name": "Quality Control",
                "description": "Ensures quality standards and compliance",
                "system_prompt": self._build_quality_control_prompt(),
                "tools": ["inspection_checklist", "compliance_audit", "fabric_testing"],
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
        """Process a fashion design request through the full workflow.

        Args:
            input_data: Dictionary containing design parameters.

        Returns:
            Dictionary with fashion design results including trend
            analysis, design concepts, patterns, and quality checks.
        """
        # Step 1: Trend research
        trends = self._analyze_trends(input_data)

        # Step 2: Design concepts
        design = self._create_design_concept(input_data, trends)

        # Step 3: Pattern creation
        pattern = self._create_pattern_spec(design)

        # Step 4: Quality assurance
        quality = self._perform_quality_check(design, pattern)

        # Store and update metrics
        self._trend_reports[trends.id] = trends
        self._designs[design.id] = design
        self._patterns[pattern.id] = pattern
        self._quality_checks[quality.id] = quality

        self.fashion_metrics.designs_created += 1
        self.fashion_metrics.patterns_generated += 1
        self._update_fashion_metrics()

        return {
            "trend_analysis": {
                "id": trends.id,
                "season": trends.season.value,
                "category": trends.category.value,
                "key_trends": trends.key_trends,
                "color_trends": trends.color_trends,
                "silhouette_trends": trends.silhouette_trends,
                "fabric_trends": trends.fabric_trends,
                "detail_trends": trends.detail_trends,
                "sustainability_trends": trends.sustainability_trends,
                "market_demand": trends.market_demand,
            },
            "design_concept": {
                "id": design.id,
                "name": design.name,
                "category": design.category.value,
                "description": design.description,
                "inspiration": design.inspiration,
                "target_demographic": design.target_demographic,
                "color_palette": design.color_palette,
                "silhouette": design.silhouette,
                "fabric_suggestions": design.fabric_suggestions,
                "embellishments": design.embellishments,
                "price_point": design.price_point,
                "design_rating": design.design_rating,
            },
            "pattern_spec": {
                "id": pattern.id,
                "pattern_name": pattern.pattern_name,
                "size_range": pattern.size_range,
                "pattern_pieces_count": len(pattern.pattern_pieces),
                "pattern_pieces": pattern.pattern_pieces,
                "difficulty": pattern.difficulty,
                "yardage_required": pattern.yardage_required,
                "notions_required": pattern.notions_required,
                "construction_notes": pattern.construction_notes,
                "pattern_efficiency": pattern.pattern_efficiency,
            },
            "quality_check": {
                "id": quality.id,
                "overall_rating": quality.overall_rating,
                "construction_quality": quality.construction_quality,
                "fabric_quality": quality.fabric_quality,
                "fit_accuracy": quality.fit_accuracy,
                "stitching_quality": quality.stitching_quality,
                "finishing_quality": quality.finishing_quality,
                "passed": quality.passed,
                "defects_found": quality.defects_found,
                "rework_needed": quality.rework_needed,
                "compliance_checks": quality.compliance_checks,
                "sustainability_verification": quality.sustainability_verification,
            },
        }

    def _on_teardown(self) -> None:
        """Clean up fashion design solution resources."""
        self._agents.clear()
        self._trend_reports.clear()
        self._designs.clear()
        self._patterns.clear()
        self._quality_checks.clear()
        self._trend_count = 0
        self._design_count = 0
        self._pattern_count = 0
        self._quality_count = 0

    # ------------------------------------------------------------------
    # Workflow steps
    # ------------------------------------------------------------------

    def _analyze_trends(self, data: Dict[str, Any]) -> TrendReport:
        """Step 1: Analyze fashion trends."""
        self._trend_count += 1
        trend_id = f"trend-{self._trend_count}"

        try:
            category = FashionCategory(data.get("fashion_category", self.config.get("fashion_category", "womenswear")))
        except ValueError:
            category = FashionCategory.WOMENSWEAR

        try:
            season = Season(data.get("season", self.config.get("season", "spring_summer")))
        except ValueError:
            season = Season.SPRING_SUMMER

        # Get trends from database
        trend_data = self._trend_database.get(category, self._trend_database[FashionCategory.WOMENSWEAR])

        # Seasonal adjustments
        season_colors = {
            Season.SPRING_SUMMER: ["pastels", "bright colors", "white", "neon accents"],
            Season.FALL_WINTER: ["earth tones", "jewel tones", "dark neutrals", "burgundy"],
            Season.RESORT: ["tropical prints", "bright whites", "vacation colors"],
            Season.HOLIDAY: ["red and green", "metallics", "rich velvets"],
        }
        season_colors_list = season_colors.get(season, [])

        # Key trends
        key_trends = [
            "Quiet luxury and minimalist aesthetics",
            "Sustainable and circular fashion",
            "Y2K and 90s nostalgia revivals",
            "Athleisure and comfort-first dressing",
            "Statement accessories and bold details",
        ]

        # Trend levels
        trend_levels = {
            "Quiet luxury": "mainstream",
            "Sustainable fashion": "rising",
            "Y2K revival": "peak",
            "Athleisure": "mainstream",
            "Statement details": "rising",
        }

        # Runway influence
        runway_influence = [
            "Oversized silhouettes continuing from major fashion weeks",
            "Color palettes trending from recent runway shows",
            "Fabric innovations seen in designer collections",
        ]

        # Sustainability trends
        sustainability = [
            "Increased demand for transparent supply chains",
            "Growth in rental and resale markets",
            "Innovations in recycled and bio-based materials",
            "Consumer preference for quality over quantity",
        ]

        # Market demand
        market_demand = (
            f"Strong market demand for {category.value} in {season.value.replace('_', ' ')}. "
            "Consumer spending shifting toward quality and sustainability."
        )

        report = TrendReport(
            id=trend_id,
            season=season,
            category=category,
            key_trends=key_trends,
            color_trends=trend_data["colors"] + season_colors_list,
            silhouette_trends=trend_data["silhouettes"],
            fabric_trends=trend_data["fabrics"],
            detail_trends=trend_data["details"],
            trend_levels=trend_levels,
            runway_influence=runway_influence,
            market_demand=market_demand,
            sustainability_trends=sustainability,
        )
        self._trend_reports[trend_id] = report
        return report

    def _create_design_concept(
        self, data: Dict[str, Any], trends: TrendReport
    ) -> DesignConcept:
        """Step 2: Create design concept."""
        self._design_count += 1
        design_id = f"design-{self._design_count}"

        category = trends.category
        season = trends.season
        price_point = self.config.get("price_point", "contemporary")
        sust_focus = self.config.get("sustainability_focus", 0.5)

        # Design concept
        design_name = data.get("design_name", f"Design {self._design_count:03d}")
        description = (
            f"A {price_point} {category.value.replace('_', ' ')} piece "
            f"for {season.value.replace('_', ' ')} season. "
            f"Featuring {trends.silhouette_trends[0]} silhouette "
            f"with {trends.detail_trends[0]} details."
        )

        # Inspiration
        inspirations = [
            f"Runway trends from {season.value.replace('_', ' ')} fashion weeks",
            "Street style and influencer aesthetics",
            "Vintage and archival references",
            "Architectural and artistic influences",
            "Cultural and global design influences",
        ]

        # Color palette (top 3 from trends + 1 accent)
        color_palette = trends.color_trends[:3] + ["neutral base"]

        # Silhouette
        silhouette = (
            f"{trends.silhouette_trends[0]} with {trends.silhouette_trends[1] if len(trends.silhouette_trends) > 1 else 'refined details'}"
        )

        # Fabric suggestions
        fabrics = trends.fabric_trends[:3]
        if sust_focus > 0.6:
            fabrics = [f for f in fabrics if any(
                s in f.lower() for s in ["organic", "recycled", "hemp", "tencel"]
            )] + fabrics[:2]

        # Embellishments
        embellishments = trends.detail_trends[:3]

        # Target demographic
        demographics = {
            "budget": "Price-conscious consumer, value-focused",
            "mass_market": "Mainstream consumer, trend-aware",
            "contemporary": "Fashion-forward professional, quality-focused",
            "premium": "Affluent consumer, brand-conscious",
            "luxury": "High-net-worth individual, exclusivity-driven",
        }
        target_demo = demographics.get(price_point, demographics["contemporary"])

        # Design rating
        base_rating = 7.0
        if price_point in ("premium", "luxury"):
            base_rating += 1.0
        if sust_focus > 0.7:
            base_rating += 0.5
        design_rating = min(10.0, base_rating)

        design = DesignConcept(
            id=design_id,
            name=design_name,
            category=category,
            description=description,
            inspiration=inspirations,
            target_demographic=target_demo,
            color_palette=color_palette,
            silhouette=silhouette,
            fabric_suggestions=fabrics,
            embellishments=embellishments,
            collection_theme=f"{season.value.replace('_', ' ').title()} Collection",
            price_point=price_point,
            design_rating=round(design_rating, 1),
        )
        self._designs[design_id] = design
        return design

    def _create_pattern_spec(self, design: DesignConcept) -> PatternSpec:
        """Step 3: Create pattern specification."""
        self._pattern_count += 1
        pattern_id = f"pattern-{self._pattern_count}"

        # Pattern pieces based on category
        pieces = {
            FashionCategory.WOMENSWEAR: [
                "Front Bodice", "Back Bodice", "Sleeve (Left)", "Sleeve (Right)",
                "Front Skirt", "Back Skirt", "Collar", "Cuff", "Facing", "Lining",
            ],
            FashionCategory.MENSWEAR: [
                "Front Panel (Left)", "Front Panel (Right)", "Back Panel",
                "Sleeve (Left)", "Sleeve (Right)", "Collar", "Cuff", "Pocket", "Facing",
            ],
            FashionCategory.ACTIVEWEAR: [
                "Front Legging", "Back Legging", "Waistband", "Gusset",
                "Front Top", "Back Top", "Sleeve", "Neckline Binding",
            ],
            FashionCategory.OUTERWEAR: [
                "Front (Left)", "Front (Right)", "Back", "Side Panel",
                "Sleeve (Left)", "Sleeve (Right)", "Collar", "Cuff",
                "Pocket", "Lining", "Interfacing",
            ],
        }
        pattern_pieces = pieces.get(design.category, pieces[FashionCategory.WOMENSWEAR])

        # Grain lines
        grain_lines = {}
        for piece in pattern_pieces:
            if "sleeve" in piece.lower():
                grain_lines[piece] = "Lengthwise grain, parallel to center"
            elif "front" in piece.lower() or "back" in piece.lower():
                grain_lines[piece] = "Lengthwise grain on center fold"
            else:
                grain_lines[piece] = "Lengthwise grain"

        # Difficulty
        difficulty_map = {
            "budget": "easy",
            "mass_market": "medium",
            "contemporary": "medium",
            "premium": "hard",
            "luxury": "hard",
        }
        difficulty = difficulty_map.get(design.price_point, "medium")

        # Yardage
        yardage = {
            FashionCategory.WOMENSWEAR: 2.5,
            FashionCategory.MENSWEAR: 3.0,
            FashionCategory.ACTIVEWEAR: 1.8,
            FashionCategory.OUTERWEAR: 4.0,
            FashionCategory.ACCESSORIES: 0.5,
        }
        yardage_required = yardage.get(design.category, 2.5)

        # Notions
        notions = [
            "Matching thread",
            "Interfacing",
            "Buttons/closures",
            "Zipper (if applicable)",
        ]
        if design.price_point in ("premium", "luxury"):
            notions.append("Premium lining fabric")
            notions.append("Shoulder pads (structured garments)")

        # Construction notes
        construction = [
            "French seams for clean finish",
            "Flat-felled seams on high-stress areas",
            "Bias-bound neckline and armholes",
            "Topstitching for reinforcement and detail",
            "Hem with blind stitch or double fold",
        ]

        # Pattern efficiency
        efficiency_base = 7.0
        if difficulty == "easy":
            efficiency_base = 8.5
        elif difficulty == "hard":
            efficiency_base = 6.0
        pattern_efficiency = efficiency_base

        pattern = PatternSpec(
            id=pattern_id,
            design_id=design.id,
            pattern_name=f"{design.name} - Master Pattern",
            size_range="XS-XXL (6 sizes)",
            pattern_pieces=pattern_pieces,
            grain_lines=grain_lines,
            seam_allowance="5/8\" (1.5cm) standard",
            difficulty=difficulty,
            construction_notes=construction,
            sizing_method="Standard graded sizing, ASTM based",
            yardage_required=yardage_required,
            notions_required=notions,
            pattern_efficiency=round(pattern_efficiency, 1),
        )
        self._patterns[pattern_id] = pattern
        return pattern

    def _perform_quality_check(
        self, design: DesignConcept, pattern: PatternSpec
    ) -> QualityCheck:
        """Step 4: Perform quality assurance check."""
        self._quality_count += 1
        quality_id = f"qc-{self._quality_count}"

        quality_standard = self.config.get("quality_standard", "premium")
        base_scores = {
            "basic": 70,
            "good": 80,
            "premium": 90,
            "luxury": 95,
        }
        base = base_scores.get(quality_standard, 80)

        # Individual scores
        construction = base + 2
        fabric = base
        fit = base - 2 if pattern.difficulty == "hard" else base
        stitching = base + 3
        finishing = base + 1

        # Overall
        overall = (construction + fabric + fit + stitching + finishing) / 5

        # Compliance checks
        compliance = [
            "Fabric testing: flammability, colorfastness, shrinkage",
            "Seam strength and durability testing",
            "Fit verification across size range",
            "Labeling and care instruction compliance",
            "Social compliance audit of manufacturing",
        ]

        # Defects (simulated minor)
        defects = [
            "Minor stitching inconsistency on sample #3",
            "Slight color variation between dye lots",
        ]
        if quality_standard in ("premium", "luxury"):
            defects = []  # Higher standard = fewer defects

        # Rework needed
        rework = []
        if defects:
            rework = [
                "Consistent stitching quality across all samples",
                "Dye lot control for color consistency",
            ]

        # Sustainability verification
        sust_focus = self.config.get("sustainability_focus", 0.5)
        if sust_focus > 0.7:
            sust_verification = (
                "Comprehensive sustainability verification: "
                "organic content certified, fair labor confirmed, "
                "transparency in supply chain documented"
            )
        elif sust_focus > 0.4:
            sust_verification = (
                "Basic sustainability verification: "
                "some eco-certified materials, labor standards confirmed"
            )
        else:
            sust_verification = "Standard compliance, no specific sustainability verification"

        quality = QualityCheck(
            id=quality_id,
            design_id=design.id,
            overall_rating=round(overall, 1),
            construction_quality=float(construction),
            fabric_quality=float(fabric),
            fit_accuracy=float(fit),
            stitching_quality=float(stitching),
            finishing_quality=float(finishing),
            compliance_checks=compliance,
            defects_found=defects,
            passed=overall >= 75,
            rework_needed=rework,
            sustainability_verification=sust_verification,
        )
        self._quality_checks[quality_id] = quality
        return quality

    # ------------------------------------------------------------------
    # Agent prompts
    # ------------------------------------------------------------------

    def _build_fashion_designer_prompt(self) -> str:
        """Build the system prompt for the fashion designer role."""
        category = self.config.get("fashion_category", "womenswear").replace("_", " ")
        season = self.config.get("season", "spring_summer").replace("_", " ")
        return (
            f"You are a fashion designer specializing in {category} for "
            f"{season}. Create cohesive, innovative designs that balance "
            "trend awareness with brand identity. Consider silhouette, "
            "color, fabric, and details in every design. Develop complete "
            "collections that tell a story and work together commercially. "
            "Ensure designs are both creative and technically feasible."
        )

    def _build_trend_analyst_prompt(self) -> str:
        """Build the system prompt for the trend analyst role."""
        return (
            "You are a fashion trend analyst with expertise in consumer "
            "behavior, cultural shifts, and style forecasting. Identify "
            "emerging trends across runways, street style, social media, "
            "and culture. Distinguish between fleeting fads and lasting "
            "trends. Provide actionable trend intelligence that informs "
            "design decisions, with clear timelines and market relevance."
        )

    def _build_pattern_maker_prompt(self) -> str:
        """Build the system prompt for the pattern maker role."""
        return (
            "You are an expert pattern maker and technical designer. "
            "Translate design concepts into precise, production-ready "
            "patterns. Ensure accurate fit across all sizes, efficient "
            "fabric usage, and clear construction instructions. Grade "
            "patterns proportionally and consider manufacturing feasibility. "
            "Your technical specifications must be precise and complete."
        )

    def _build_quality_control_prompt(self) -> str:
        """Build the system prompt for the quality control role."""
        standard = self.config.get("quality_standard", "premium")
        return (
            f"You are a quality assurance specialist maintaining {standard} "
            "quality standards in fashion production. Inspect garments for "
            "construction quality, fabric integrity, fit accuracy, and "
            "finishing details. Ensure compliance with regulatory requirements "
            "and brand standards. Identify defects, recommend corrections, "
            "and verify that every piece meets specifications before release."
        )

    # ------------------------------------------------------------------
    # Metrics
    # ------------------------------------------------------------------

    def _update_fashion_metrics(self) -> None:
        """Update fashion-specific metrics."""
        if self._designs:
            total = len(self._designs)
            avg_rating = sum(d.design_rating for d in self._designs.values()) / total
            self.fashion_metrics.design_quality = avg_rating
            self.fashion_metrics.avg_design_rating = avg_rating

        if self._patterns:
            total = len(self._patterns)
            avg_eff = sum(p.pattern_efficiency for p in self._patterns.values()) / total
            self.fashion_metrics.production_efficiency = avg_eff * 10

        if self._quality_checks:
            total = len(self._quality_checks)
            passed = sum(1 for q in self._quality_checks.values() if q.passed)
            self.fashion_metrics.quality_pass_rate = passed / total * 100 if total > 0 else 0

            avg_overall = sum(q.overall_rating for q in self._quality_checks.values()) / total
            self.fashion_metrics.design_quality = avg_overall / 10  # Scale to 0-10

        # Sustainability score
        sust_focus = self.config.get("sustainability_focus", 0.5)
        self.fashion_metrics.sustainability_score = sust_focus * 100

        # Trend accuracy
        self.fashion_metrics.trend_accuracy = 75.0  # Base estimate

        # Time to market (weeks)
        price_point = self.config.get("price_point", "contemporary")
        ttm_map = {
            "budget": 12,
            "mass_market": 16,
            "contemporary": 20,
            "premium": 24,
            "luxury": 30,
        }
        self.fashion_metrics.time_to_market = float(ttm_map.get(price_point, 20))

        # Collection size
        self.fashion_metrics.collection_size = float(
            self.config.get("collection_size", 12)
        )

    def get_fashion_metrics(self) -> Dict[str, Any]:
        """Get the current fashion design metrics.

        Returns:
            Dictionary with fashion metrics including design quality,
            trend accuracy, production efficiency, and sustainability.
        """
        return self.fashion_metrics.to_dict()
