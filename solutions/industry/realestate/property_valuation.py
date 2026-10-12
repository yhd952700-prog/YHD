"""
Property Valuation AI Solution Template.

A comprehensive AI-powered property valuation assistant for
real estate agencies, appraisal firms, and investment companies.
Includes multi-agent roles for appraiser, market analyst,
data collector, and report writer.

Typical workflow:
    property info -> market analysis -> comparable sales
    -> value calculation -> appraisal report
"""

from typing import Optional, Dict, Any, List
from enum import Enum
from dataclasses import dataclass, field

from solutions.templates.base_solution import (
    BaseSolution,
    SolutionMetadata,
    SolutionMetrics,
)


class PropertyType(str, Enum):
    """Types of properties."""
    SINGLE_FAMILY = "single_family"
    CONDO = "condo"
    TOWNHOUSE = "townhouse"
    MULTI_FAMILY = "multi_family"
    COMMERCIAL = "commercial"
    INDUSTRIAL = "industrial"
    LAND = "land"
    MIXED_USE = "mixed_use"
    RETAIL = "retail"
    OFFICE = "office"
    WAREHOUSE = "warehouse"
    APARTMENT_BUILDING = "apartment_building"


class ValuationMethod(str, Enum):
    """Property valuation methods."""
    SALES_COMPARISON = "sales_comparison"
    INCOME = "income"
    COST = "cost"
    DCF = "dcf"
    CAP_RATE = "cap_rate"
    GRM = "grm"  # Gross Rent Multiplier


class MarketCondition(str, Enum):
    """Real estate market conditions."""
    HOT = "hot"
    SELLERS_MARKET = "sellers_market"
    BALANCED = "balanced"
    BUYERS_MARKET = "buyers_market"
    COOLING = "cooling"
    RECESSIONARY = "recessionary"


class PropertyFeature(str, Enum):
    """Property feature categories."""
    BEDROOMS = "bedrooms"
    BATHROOMS = "bathrooms"
    SQUARE_FOOTAGE = "square_footage"
    LOT_SIZE = "lot_size"
    YEAR_BUILT = "year_built"
    GARAGE = "garage"
    POOL = "pool"
    BASEMENT = "basement"
    FIREPLACE = "fireplace"
    RENOVATION = "renovation"
    AMENITIES = "amenities"
    VIEW = "view"
    LOCATION = "location"


class RealEstateRole(str, Enum):
    """Agent roles in the property valuation solution."""
    APPRAISER = "appraiser"
    MARKET_ANALYST = "market_analyst"
    DATA_COLLECTOR = "data_collector"
    REPORT_WRITER = "report_writer"


@dataclass
class PropertyInfo:
    """Represents property information for valuation.

    Attributes:
        id: Unique property identifier.
        address: Full property address.
        property_type: Type of property.
        bedrooms: Number of bedrooms.
        bathrooms: Number of bathrooms.
        square_footage: Interior square footage.
        lot_size_acres: Lot size in acres.
        year_built: Year property was built.
        garage: Garage spaces.
        pool: Whether property has a pool.
        basement: Basement type (none, partial, full, finished).
        condition: Property condition rating (1-10).
        renovations: Recent renovations and upgrades.
        amenities: Community/property amenities.
        zoning: Zoning classification.
        view: View quality.
        neighborhood: Neighborhood description.
        year_renovated: Year of last major renovation.
    """
    id: str
    address: str = ""
    property_type: PropertyType = PropertyType.SINGLE_FAMILY
    bedrooms: int = 0
    bathrooms: float = 0.0
    square_footage: int = 0
    lot_size_acres: float = 0.0
    year_built: int = 0
    garage: int = 0
    pool: bool = False
    basement: str = "none"
    condition: float = 0.0
    renovations: List[str] = field(default_factory=list)
    amenities: List[str] = field(default_factory=list)
    zoning: str = ""
    view: str = "none"
    neighborhood: str = ""
    year_renovated: int = 0


@dataclass
class MarketAnalysis:
    """Represents a real estate market analysis.

    Attributes:
        id: Unique market analysis ID.
        property_id: Associated property ID.
        market_area: Geographic market area.
        market_condition: Current market condition.
        median_price: Median property price in area.
        avg_price_per_sqft: Average price per square foot.
        days_on_market: Average days on market.
        inventory_levels: Inventory level assessment.
        price_trend: Price trend direction and rate.
        comparable_sales_count: Number of recent comparable sales.
        absorption_rate: Market absorption rate.
        supply_months: Months of supply available.
        buyer_demand: Buyer demand strength (1-10).
        economic_factors: Economic factors affecting market.
        seasonality: Seasonal factors.
    """
    id: str
    property_id: str = ""
    market_area: str = ""
    market_condition: MarketCondition = MarketCondition.BALANCED
    median_price: float = 0.0
    avg_price_per_sqft: float = 0.0
    days_on_market: float = 0.0
    inventory_levels: str = ""
    price_trend: str = ""
    comparable_sales_count: int = 0
    absorption_rate: float = 0.0
    supply_months: float = 0.0
    buyer_demand: float = 0.0
    economic_factors: List[str] = field(default_factory=list)
    seasonality: str = ""


@dataclass
class ComparableSale:
    """Represents a comparable sale (comp).

    Attributes:
        id: Unique comp ID.
        address: Property address.
        sale_price: Sale price.
        sale_date: Date of sale.
        property_type: Type of property.
        bedrooms: Number of bedrooms.
        bathrooms: Number of bathrooms.
        square_footage: Square footage.
        lot_size: Lot size.
        year_built: Year built.
        condition: Condition rating.
        distance_miles: Distance from subject property.
        similarity_score: Overall similarity score (0-100).
        adjustments: Price adjustments made.
        adjusted_price: Price after adjustments.
        features: Key features and amenities.
        photos: Photo references (if available).
    """
    id: str
    address: str = ""
    sale_price: float = 0.0
    sale_date: str = ""
    property_type: str = ""
    bedrooms: int = 0
    bathrooms: float = 0.0
    square_footage: int = 0
    lot_size: float = 0.0
    year_built: int = 0
    condition: float = 0.0
    distance_miles: float = 0.0
    similarity_score: float = 0.0
    adjustments: Dict[str, float] = field(default_factory=dict)
    adjusted_price: float = 0.0
    features: List[str] = field(default_factory=list)
    photos: List[str] = field(default_factory=list)


@dataclass
class ValuationResult:
    """Represents a property valuation result.

    Attributes:
        id: Unique valuation ID.
        property_id: Associated property ID.
        valuation_method: Primary valuation method used.
        estimated_value: Estimated property value.
        value_range_low: Low end of value range.
        value_range_high: High end of value range.
        confidence: Confidence level (0-100).
        price_per_sqft: Price per square foot.
        comps_used: Number of comparables used.
        key_value_drivers: Primary factors driving value.
        upside_potential: Upside potential factors.
        downside_risks: Downside risk factors.
        recommended_list_price: Recommended listing price.
        appraisal_value: Formal appraisal value.
        valuation_date: Date of valuation.
        next_review_date: Recommended next review date.
    """
    id: str
    property_id: str = ""
    valuation_method: str = ""
    estimated_value: float = 0.0
    value_range_low: float = 0.0
    value_range_high: float = 0.0
    confidence: float = 0.0
    price_per_sqft: float = 0.0
    comps_used: int = 0
    key_value_drivers: List[str] = field(default_factory=list)
    upside_potential: List[str] = field(default_factory=list)
    downside_risks: List[str] = field(default_factory=list)
    recommended_list_price: float = 0.0
    appraisal_value: float = 0.0
    valuation_date: str = ""
    next_review_date: str = ""


@dataclass
class AppraisalReport:
    """Represents an appraisal report.

    Attributes:
        id: Unique report ID.
        property_id: Associated property ID.
        report_type: Type of appraisal report.
        effective_date: Effective date of appraisal.
        subject_property: Subject property summary.
        market_analysis: Market analysis summary.
        sales_comparison_approach: Sales comparison details.
        income_approach: Income approach details (if applicable).
        cost_approach: Cost approach details (if applicable).
        reconciliation: Reconciliation of approaches.
        final_value_estimate: Final value conclusion.
        limiting_conditions: Limiting conditions and assumptions.
        certifications: Appraiser certifications.
        exhibits: List of exhibits and attachments.
        report_quality: Quality score of the report (0-10).
    """
    id: str
    property_id: str = ""
    report_type: str = ""
    effective_date: str = ""
    subject_property: str = ""
    market_analysis: str = ""
    sales_comparison_approach: str = ""
    income_approach: str = ""
    cost_approach: str = ""
    reconciliation: str = ""
    final_value_estimate: float = 0.0
    limiting_conditions: List[str] = field(default_factory=list)
    certifications: List[str] = field(default_factory=list)
    exhibits: List[str] = field(default_factory=list)
    report_quality: float = 0.0


@dataclass
class RealEstateMetrics(SolutionMetrics):
    """Extended metrics for the property valuation solution.

    Attributes:
        valuation_accuracy: Valuation accuracy rate.
        report_quality: Average report quality score.
        market_coverage: Market data coverage score.
        properties_valued: Total properties valued.
        avg_value: Average property value.
        avg_confidence: Average confidence level.
        appraisal_turnaround: Average appraisal turnaround time (days).
        comp_selection_quality: Comparable selection quality.
        adjustment_accuracy: Adjustment accuracy.
        client_satisfaction: Client satisfaction rating.
    """
    valuation_accuracy: float = 0.0
    report_quality: float = 0.0
    market_coverage: float = 0.0
    properties_valued: int = 0
    avg_value: float = 0.0
    avg_confidence: float = 0.0
    appraisal_turnaround: float = 0.0
    comp_selection_quality: float = 0.0
    adjustment_accuracy: float = 0.0
    client_satisfaction: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        """Convert real estate metrics to a dictionary."""
        base = super().to_dict()
        base.update({
            "valuation_accuracy": self.valuation_accuracy,
            "report_quality": self.report_quality,
            "market_coverage": self.market_coverage,
            "properties_valued": self.properties_valued,
            "avg_value": self.avg_value,
            "avg_confidence": self.avg_confidence,
            "appraisal_turnaround": self.appraisal_turnaround,
            "comp_selection_quality": self.comp_selection_quality,
            "adjustment_accuracy": self.adjustment_accuracy,
            "client_satisfaction": self.client_satisfaction,
        })
        return base


class PropertyValuationSolution(BaseSolution):
    """Property Valuation AI solution template.

    Provides a complete, pre-configured property valuation solution
    with multi-agent collaboration for data collection, market
    analysis, comparable sales analysis, value calculation, and
    report generation.

    Pre-configured agent roles:
        - Appraiser: Conducts property valuation and reconciliation
        - Market Analyst: Analyzes market conditions and trends
        - Data Collector: Gathers property and market data
        - Report Writer: Generates comprehensive appraisal reports

    Workflow:
        1. Property Info - Gather property details and characteristics
        2. Market Analysis - Analyze local market conditions
        3. Comparable Sales - Identify and adjust comparable sales
        4. Value Calculation - Calculate estimated property value
        5. Appraisal Report - Generate formal appraisal report

    Configuration options:
        - property_type: Primary property type
        - valuation_method: Primary valuation method
        - market_area: Geographic market area
        - report_type: Type of appraisal report
        - data_sources: Data sources to use
    """

    def _build_metadata(self) -> SolutionMetadata:
        """Build metadata for the property valuation solution."""
        return SolutionMetadata(
            name="property-valuation",
            version="1.0.0",
            industry="realestate",
            description=(
                "AI-powered property valuation with market analysis, "
                "comparable sales adjustment, value estimation, and "
                "appraisal report generation for real estate professionals."
            ),
            author="LiuHao AI OS",
            tags=["real-estate", "valuation", "appraisal", "market-analysis", "comparables", "property"],
            min_liuhao_version="1.0.0",
        )

    def _build_config_schema(self) -> None:
        """Define configuration fields for the property valuation solution."""
        self.config_schema.add_field(
            name="property_type",
            field_type="string",
            required=False,
            default="single_family",
            description="Primary property type",
            valid_values=[
                "single_family", "condo", "townhouse", "multi_family",
                "commercial", "industrial", "land", "mixed_use",
                "retail", "office", "warehouse", "apartment_building",
            ],
        )
        self.config_schema.add_field(
            name="valuation_method",
            field_type="string",
            required=False,
            default="sales_comparison",
            description="Primary valuation method",
            valid_values=[
                "sales_comparison", "income", "cost", "dcf",
                "cap_rate", "grm",
            ],
        )
        self.config_schema.add_field(
            name="market_area",
            field_type="string",
            required=False,
            default="metro",
            description="Geographic market area type",
            valid_values=["urban", "suburban", "rural", "metro"],
        )
        self.config_schema.add_field(
            name="report_type",
            field_type="string",
            required=False,
            default="full_appraisal",
            description="Type of appraisal report",
            valid_values=["summary", "full_appraisal", "desktop", "drive_by", "broker_price_opinion"],
        )
        self.config_schema.add_field(
            name="comp_count",
            field_type="int",
            required=False,
            default=6,
            description="Number of comparable sales to use",
        )
        self.config_schema.add_field(
            name="adjustment_method",
            field_type="string",
            required=False,
            default="paired_sales",
            description="Adjustment methodology",
            valid_values=["paired_sales", "multiple_regression", "cost_based", "qualitative"],
        )
        self.config_schema.add_field(
            name="data_quality_requirement",
            field_type="string",
            required=False,
            default="high",
            description="Required data quality level",
            valid_values=["basic", "medium", "high", "verified"],
        )

    def _on_initialize(self) -> None:
        """Initialize property valuation solution state."""
        self._agents: Dict[str, Dict[str, Any]] = {}
        self._properties: Dict[str, PropertyInfo] = {}
        self._market_analyses: Dict[str, MarketAnalysis] = {}
        self._comparables: Dict[str, ComparableSale] = {}
        self._valuations: Dict[str, ValuationResult] = {}
        self._reports: Dict[str, AppraisalReport] = {}
        self._property_count = 0
        self._market_count = 0
        self._comp_count = 0
        self._valuation_count = 0
        self._report_count = 0

        # Initialize real estate-specific metrics
        self.realestate_metrics = RealEstateMetrics()

        # Market data simulation
        self._market_data = self._build_market_data()

    def _build_market_data(self) -> Dict[str, Any]:
        """Build market data by property type and area."""
        return {
            PropertyType.SINGLE_FAMILY: {
                "urban": {"median_price": 750000, "price_per_sqft": 450, "dom": 30, "supply_months": 2.5},
                "suburban": {"median_price": 500000, "price_per_sqft": 250, "dom": 45, "supply_months": 3.5},
                "rural": {"median_price": 350000, "price_per_sqft": 180, "dom": 60, "supply_months": 5.0},
            },
            PropertyType.CONDO: {
                "urban": {"median_price": 550000, "price_per_sqft": 500, "dom": 35, "supply_months": 3.0},
                "suburban": {"median_price": 350000, "price_per_sqft": 300, "dom": 50, "supply_months": 4.0},
            },
            PropertyType.TOWNHOUSE: {
                "urban": {"median_price": 650000, "price_per_sqft": 350, "dom": 28, "supply_months": 2.0},
                "suburban": {"median_price": 400000, "price_per_sqft": 220, "dom": 40, "supply_months": 3.0},
            },
            PropertyType.MULTI_FAMILY: {
                "urban": {"median_price": 1500000, "price_per_sqft": 200, "dom": 60, "supply_months": 6.0},
                "suburban": {"median_price": 800000, "price_per_sqft": 150, "dom": 75, "supply_months": 7.0},
            },
            PropertyType.COMMERCIAL: {
                "urban": {"median_price": 2000000, "price_per_sqft": 300, "dom": 90, "supply_months": 8.0},
                "suburban": {"median_price": 1000000, "price_per_sqft": 150, "dom": 120, "supply_months": 10.0},
            },
        }

    def _on_deploy(self) -> None:
        """Deploy property valuation agents and workflows."""
        agent_configs = [
            (RealEstateRole.APPRAISER, {
                "name": "Appraiser",
                "description": "Conducts property valuation and reconciliation",
                "system_prompt": self._build_appraiser_prompt(),
                "tools": ["valuation", "adjustments", "reconciliation"],
            }),
            (RealEstateRole.MARKET_ANALYST, {
                "name": "Market Analyst",
                "description": "Analyzes market conditions and trends",
                "system_prompt": self._build_market_analyst_prompt(),
                "tools": ["market_research", "trend_analysis", "forecasting"],
            }),
            (RealEstateRole.DATA_COLLECTOR, {
                "name": "Data Collector",
                "description": "Gathers property and market data",
                "system_prompt": self._build_data_collector_prompt(),
                "tools": ["property_data", "mls_data", "public_records"],
            }),
            (RealEstateRole.REPORT_WRITER, {
                "name": "Report Writer",
                "description": "Generates comprehensive appraisal reports",
                "system_prompt": self._build_report_writer_prompt(),
                "tools": ["report_generation", "document_assembly", "quality_control"],
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
        """Process a property valuation request through the full workflow.

        Args:
            input_data: Dictionary containing property information.

        Returns:
            Dictionary with valuation results including property info,
            market analysis, comparable sales, valuation result, and
            appraisal report.
        """
        # Step 1: Property info
        property_info = self._gather_property_info(input_data)

        # Step 2: Market analysis
        market = self._analyze_market(property_info)

        # Step 3: Comparable sales
        comparables = self._find_comparables(property_info, market)

        # Step 4: Value calculation
        valuation = self._calculate_value(property_info, market, comparables)

        # Step 5: Appraisal report
        report = self._generate_report(property_info, market, comparables, valuation)

        # Store and update metrics
        self._properties[property_info.id] = property_info
        self._market_analyses[market.id] = market
        for comp in comparables:
            self._comparables[comp.id] = comp
        self._valuations[valuation.id] = valuation
        self._reports[report.id] = report

        self.realestate_metrics.properties_valued += 1
        self._update_realestate_metrics()

        return {
            "property_info": {
                "id": property_info.id,
                "address": property_info.address,
                "property_type": property_info.property_type.value,
                "bedrooms": property_info.bedrooms,
                "bathrooms": property_info.bathrooms,
                "square_footage": property_info.square_footage,
                "lot_size_acres": property_info.lot_size_acres,
                "year_built": property_info.year_built,
                "garage": property_info.garage,
                "pool": property_info.pool,
                "basement": property_info.basement,
                "condition": property_info.condition,
                "neighborhood": property_info.neighborhood,
                "view": property_info.view,
            },
            "market_analysis": {
                "id": market.id,
                "market_area": market.market_area,
                "market_condition": market.market_condition.value,
                "median_price": market.median_price,
                "avg_price_per_sqft": market.avg_price_per_sqft,
                "days_on_market": market.days_on_market,
                "price_trend": market.price_trend,
                "supply_months": market.supply_months,
                "buyer_demand": market.buyer_demand,
                "absorption_rate": market.absorption_rate,
                "economic_factors": market.economic_factors,
                "seasonality": market.seasonality,
            },
            "comparable_sales": {
                "total_comps": len(comparables),
                "comps": [
                    {
                        "id": c.id,
                        "address": c.address,
                        "sale_price": c.sale_price,
                        "sale_date": c.sale_date,
                        "bedrooms": c.bedrooms,
                        "bathrooms": c.bathrooms,
                        "square_footage": c.square_footage,
                        "distance_miles": c.distance_miles,
                        "similarity_score": c.similarity_score,
                        "adjustments": c.adjustments,
                        "adjusted_price": c.adjusted_price,
                    }
                    for c in comparables
                ],
            },
            "valuation": {
                "id": valuation.id,
                "valuation_method": valuation.valuation_method,
                "estimated_value": valuation.estimated_value,
                "value_range_low": valuation.value_range_low,
                "value_range_high": valuation.value_range_high,
                "confidence": valuation.confidence,
                "price_per_sqft": valuation.price_per_sqft,
                "comps_used": valuation.comps_used,
                "key_value_drivers": valuation.key_value_drivers,
                "upside_potential": valuation.upside_potential,
                "downside_risks": valuation.downside_risks,
                "recommended_list_price": valuation.recommended_list_price,
                "appraisal_value": valuation.appraisal_value,
                "valuation_date": valuation.valuation_date,
                "next_review_date": valuation.next_review_date,
            },
            "appraisal_report": {
                "id": report.id,
                "report_type": report.report_type,
                "effective_date": report.effective_date,
                "final_value_estimate": report.final_value_estimate,
                "sales_comparison_approach": report.sales_comparison_approach[:200] + "...",
                "reconciliation": report.reconciliation[:200] + "...",
                "limiting_conditions_count": len(report.limiting_conditions),
                "exhibits_count": len(report.exhibits),
                "report_quality": report.report_quality,
            },
        }

    def _on_teardown(self) -> None:
        """Clean up property valuation solution resources."""
        self._agents.clear()
        self._properties.clear()
        self._market_analyses.clear()
        self._comparables.clear()
        self._valuations.clear()
        self._reports.clear()
        self._property_count = 0
        self._market_count = 0
        self._comp_count = 0
        self._valuation_count = 0
        self._report_count = 0

    # ------------------------------------------------------------------
    # Workflow steps
    # ------------------------------------------------------------------

    def _gather_property_info(self, data: Dict[str, Any]) -> PropertyInfo:
        """Step 1: Gather property information."""
        self._property_count += 1
        property_id = f"property-{self._property_count}"

        try:
            prop_type = PropertyType(data.get("property_type", self.config.get("property_type", "single_family")))
        except ValueError:
            prop_type = PropertyType.SINGLE_FAMILY

        sqft = int(data.get("square_footage", 2000))
        beds = int(data.get("bedrooms", 3))
        baths = float(data.get("bathrooms", 2.0))

        property_info = PropertyInfo(
            id=property_id,
            address=data.get("address", f"{property_id} Main Street"),
            property_type=prop_type,
            bedrooms=beds,
            bathrooms=baths,
            square_footage=sqft,
            lot_size_acres=float(data.get("lot_size_acres", 0.25)),
            year_built=int(data.get("year_built", 2000)),
            garage=int(data.get("garage", 2)),
            pool=bool(data.get("pool", False)),
            basement=data.get("basement", "none"),
            condition=float(data.get("condition", 7.0)),
            renovations=data.get("renovations", []),
            amenities=data.get("amenities", []),
            zoning=data.get("zoning", "residential"),
            view=data.get("view", "none"),
            neighborhood=data.get("neighborhood", "Standard Subdivision"),
            year_renovated=int(data.get("year_renovated", 0)),
        )
        self._properties[property_id] = property_info
        return property_info

    def _analyze_market(self, property_info: PropertyInfo) -> MarketAnalysis:
        """Step 2: Analyze the local market."""
        self._market_count += 1
        market_id = f"market-{self._market_count}"

        market_area = self.config.get("market_area", "suburban")
        prop_type = property_info.property_type

        # Get market data
        type_data = self._market_data.get(prop_type, self._market_data[PropertyType.SINGLE_FAMILY])
        area_data = type_data.get(market_area, type_data.get("suburban", {}))

        median_price = area_data.get("median_price", 500000)
        price_per_sqft = area_data.get("price_per_sqft", 250)
        dom = area_data.get("dom", 45)
        supply = area_data.get("supply_months", 3.5)

        # Determine market condition
        if supply < 2:
            condition = MarketCondition.HOT
        elif supply < 3:
            condition = MarketCondition.SELLERS_MARKET
        elif supply < 6:
            condition = MarketCondition.BALANCED
        elif supply < 9:
            condition = MarketCondition.BUYERS_MARKET
        else:
            condition = MarketCondition.COOLING

        # Price trend
        if condition == MarketCondition.HOT:
            trend = f"Prices rising rapidly (+10-15% YoY)"
        elif condition == MarketCondition.SELLERS_MARKET:
            trend = f"Prices rising moderately (+5-10% YoY)"
        elif condition == MarketCondition.BALANCED:
            trend = f"Prices stable (+2-5% YoY)"
        elif condition == MarketCondition.BUYERS_MARKET:
            trend = f"Prices flat to slightly declining (0 to -3% YoY)"
        else:
            trend = f"Prices declining (-3 to -10% YoY)"

        # Inventory levels
        inventory_desc = {
            MarketCondition.HOT: "Very low inventory, multiple offers common",
            MarketCondition.SELLERS_MARKET: "Low inventory, sellers have advantage",
            MarketCondition.BALANCED: "Moderate inventory, balanced market",
            MarketCondition.BUYERS_MARKET: "High inventory, buyers have advantage",
            MarketCondition.COOLING: "Very high inventory, market cooling",
        }.get(condition, "Balanced market")

        # Buyer demand
        demand = {
            MarketCondition.HOT: 9.0,
            MarketCondition.SELLERS_MARKET: 7.5,
            MarketCondition.BALANCED: 5.5,
            MarketCondition.BUYERS_MARKET: 4.0,
            MarketCondition.COOLING: 2.5,
        }.get(condition, 5.0)

        # Absorption rate (monthly sales / active listings)
        absorption = 1 / supply * 100 if supply > 0 else 0

        # Economic factors
        economic_factors = [
            "Interest rates affecting affordability",
            "Employment levels and wage growth",
            "Migration patterns and population growth",
            "New construction activity",
            "Mortgage rate trends",
        ]

        # Seasonality
        seasonality = (
            "Spring (Mar-May): Peak buying season, highest prices\n"
            "Summer (Jun-Aug): Strong activity, some slowdown\n"
            "Fall (Sep-Nov): Moderate activity, price softening\n"
            "Winter (Dec-Feb): Lowest activity, best buyer opportunities"
        )

        comps_count = int(self.config.get("comp_count", 6))

        analysis = MarketAnalysis(
            id=market_id,
            property_id=property_info.id,
            market_area=f"{market_area.title()} {prop_type.value.replace('_', ' ').title()} Market",
            market_condition=condition,
            median_price=float(median_price),
            avg_price_per_sqft=float(price_per_sqft),
            days_on_market=float(dom),
            inventory_levels=inventory_desc,
            price_trend=trend,
            comparable_sales_count=comps_count,
            absorption_rate=round(absorption, 1),
            supply_months=float(supply),
            buyer_demand=demand,
            economic_factors=economic_factors,
            seasonality=seasonality,
        )
        self._market_analyses[market_id] = analysis
        return analysis

    def _find_comparables(
        self, property_info: PropertyInfo, market: MarketAnalysis
    ) -> List[ComparableSale]:
        """Step 3: Find and adjust comparable sales."""
        comparables = []
        comp_count = int(self.config.get("comp_count", 6))

        prop_sqft = property_info.square_footage
        prop_beds = property_info.bedrooms
        prop_baths = property_info.bathrooms
        price_per_sqft = market.avg_price_per_sqft

        # Generate comparable sales with variations
        for i in range(comp_count):
            self._comp_count += 1
            comp_id = f"comp-{self._comp_count}"

            # Vary comp characteristics
            sqft_variation = 0.85 + (i * 0.05)  # 85%-115% of subject
            comp_sqft = int(prop_sqft * sqft_variation)
            comp_beds = max(1, prop_beds + (i - comp_count // 2))
            comp_baths = max(1.0, prop_baths + ((i - comp_count // 2) * 0.5))

            # Sale price based on sqft and adjustments
            base_price = comp_sqft * price_per_sqft

            # Add some variation
            price_factor = 0.92 + (i * 0.03)  # Range of prices
            sale_price = base_price * price_factor

            # Distance from subject
            distance = 0.5 + (i * 0.3)  # 0.5 to ~2 miles

            # Similarity score
            sqft_diff = abs(comp_sqft - prop_sqft) / prop_sqft
            bed_diff = abs(comp_beds - prop_beds)
            bath_diff = abs(comp_baths - prop_baths)
            similarity = 100 - (sqft_diff * 30) - (bed_diff * 5) - (bath_diff * 3) - (distance * 2)
            similarity = max(60.0, min(98.0, similarity))

            # Calculate adjustments
            adjustments = {}

            # Size adjustment
            size_diff = prop_sqft - comp_sqft
            if abs(size_diff) > 50:
                adj = size_diff * price_per_sqft * 0.7  # Size adjustment at 70% of marginal value
                adjustments["size"] = round(adj, 2)

            # Bedroom adjustment
            bed_adj = (prop_beds - comp_beds) * 15000
            if bed_adj != 0:
                adjustments["bedrooms"] = bed_adj

            # Bathroom adjustment
            bath_adj = (prop_baths - comp_baths) * 10000
            if bath_adj != 0:
                adjustments["bathrooms"] = bath_adj

            # Garage adjustment
            garage_adj = (property_info.garage - 2) * 5000
            if garage_adj != 0:
                adjustments["garage"] = garage_adj

            # Pool adjustment
            if property_info.pool and i % 3 != 0:
                adjustments["pool"] = 25000
            elif not property_info.pool and i % 4 == 0:
                adjustments["pool"] = -25000

            # Condition adjustment
            cond_diff = property_info.condition - 7.0
            if abs(cond_diff) > 0.5:
                adjustments["condition"] = cond_diff * (sale_price * 0.03)

            # Location/view adjustment
            if property_info.view != "none":
                view_values = {"none": 0, "good": 15000, "great": 30000, "spectacular": 50000}
                adjustments["view"] = view_values.get(property_info.view, 0)

            # Calculate adjusted price
            total_adj = sum(adjustments.values())
            adjusted_price = sale_price + total_adj

            # Features
            features = [
                f"{comp_beds} bedrooms, {comp_baths} bathrooms",
                f"{comp_sqft} sq ft",
                f"Built around {property_info.year_built + (i - 3)}",
            ]
            if i % 2 == 0:
                features.append("2-car garage")
            if i % 3 == 0:
                features.append("Updated kitchen")

            comp = ComparableSale(
                id=comp_id,
                address=f"{100 + i * 10} {['Oak', 'Maple', 'Pine', 'Elm', 'Cedar', 'Walnut'][i % 6]} Street",
                sale_price=round(sale_price, 2),
                sale_date=f"Sold {30 + i * 5} days ago",
                property_type=property_info.property_type.value,
                bedrooms=comp_beds,
                bathrooms=comp_baths,
                square_footage=comp_sqft,
                lot_size=0.2 + (i * 0.05),
                year_built=property_info.year_built + (i - 3),
                condition=6.5 + (i * 0.3),
                distance_miles=round(distance, 1),
                similarity_score=round(similarity, 1),
                adjustments={k: round(v, 2) for k, v in adjustments.items()},
                adjusted_price=round(adjusted_price, 2),
                features=features,
            )
            comparables.append(comp)

        return comparables

    def _calculate_value(
        self,
        property_info: PropertyInfo,
        market: MarketAnalysis,
        comparables: List[ComparableSale],
    ) -> ValuationResult:
        """Step 4: Calculate property value."""
        self._valuation_count += 1
        val_id = f"valuation-{self._valuation_count}"

        method = self.config.get("valuation_method", "sales_comparison")

        # Sales comparison approach
        # Weight comparables by similarity score
        if comparables:
            total_similarity = sum(c.similarity_score for c in comparables)
            weighted_value = sum(
                c.adjusted_price * (c.similarity_score / total_similarity)
                for c in comparables
            )
        else:
            weighted_value = property_info.square_footage * market.avg_price_per_sqft

        # Adjust for property condition
        condition_factor = 0.85 + (property_info.condition * 0.02)  # 6.5 -> 0.98, 10 -> 1.05
        adjusted_value = weighted_value * condition_factor

        # Additional adjustments
        # Pool
        if property_info.pool:
            adjusted_value += 25000

        # Garage
        garage_extra = max(0, property_info.garage - 2) * 5000
        adjusted_value += garage_extra

        # View
        view_values = {"none": 0, "good": 15000, "great": 30000, "spectacular": 50000}
        adjusted_value += view_values.get(property_info.view, 0)

        # Renovation premium
        if property_info.year_renovated > 0:
            adjusted_value *= 1.05  # 5% premium for renovated

        # Market condition adjustment
        if market.market_condition == MarketCondition.HOT:
            adjusted_value *= 1.05
        elif market.market_condition == MarketCondition.SELLERS_MARKET:
            adjusted_value *= 1.02
        elif market.market_condition == MarketCondition.BUYERS_MARKET:
            adjusted_value *= 0.97
        elif market.market_condition == MarketCondition.COOLING:
            adjusted_value *= 0.93

        # Round to nearest thousand
        estimated_value = round(adjusted_value / 1000) * 1000

        # Value range
        range_pct = 0.05  # +/- 5%
        value_low = round(estimated_value * (1 - range_pct) / 1000) * 1000
        value_high = round(estimated_value * (1 + range_pct) / 1000) * 1000

        # Confidence level
        base_confidence = 80.0
        if len(comparables) >= 6:
            base_confidence += 5
        if property_info.condition > 7:
            base_confidence += 2
        if market.market_condition == MarketCondition.BALANCED:
            base_confidence += 3
        confidence = min(98.0, base_confidence)

        # Price per sqft
        price_per_sqft = estimated_value / property_info.square_footage if property_info.square_footage > 0 else 0

        # Key value drivers
        key_drivers = [
            f"Location in {property_info.neighborhood} neighborhood",
            f"{property_info.bedrooms} bedrooms / {property_info.bathrooms} bathrooms layout",
            f"{property_info.square_footage} sq ft of living space",
            f"Condition rated {property_info.condition}/10",
            f"{market.market_condition.value.replace('_', ' ').title()} market conditions",
        ]
        if property_info.pool:
            key_drivers.append("Swimming pool adds value")
        if property_info.garage > 2:
            key_drivers.append(f"{property_info.garage}-car garage")
        if property_info.view != "none":
            key_drivers.append(f"{property_info.view} view premium")

        # Upside potential
        upside = [
            "Kitchen or bathroom renovation could add significant value",
            "Finished basement would increase living space",
            "Adding a bedroom could increase value",
            "ADU (accessory dwelling unit) potential",
            "Market appreciation in growing neighborhood",
        ]

        # Downside risks
        downside = [
            "Market cooling could reduce property values",
            "Rising interest rates reduce buyer pool",
            "Older systems (HVAC, roof) may need replacement",
            "Location factors (busy street, nearby commercial)",
            "Economic downturn risk",
        ]

        # Recommended list price
        if market.market_condition in (MarketCondition.HOT, MarketCondition.SELLERS_MARKET):
            list_price = estimated_value * 1.03
        elif market.market_condition == MarketCondition.BALANCED:
            list_price = estimated_value
        else:
            list_price = estimated_value * 0.97
        list_price = round(list_price / 1000) * 1000

        # Appraisal value (typically slightly more conservative)
        appraisal_value = round(estimated_value * 0.98 / 1000) * 1000

        val = ValuationResult(
            id=val_id,
            property_id=property_info.id,
            valuation_method=method,
            estimated_value=float(estimated_value),
            value_range_low=float(value_low),
            value_range_high=float(value_high),
            confidence=round(confidence, 1),
            price_per_sqft=round(price_per_sqft, 2),
            comps_used=len(comparables),
            key_value_drivers=key_drivers,
            upside_potential=upside,
            downside_risks=downside,
            recommended_list_price=float(list_price),
            appraisal_value=float(appraisal_value),
            valuation_date="Current date",
            next_review_date="In 3 months",
        )
        self._valuations[val_id] = val
        return val

    def _generate_report(
        self,
        property_info: PropertyInfo,
        market: MarketAnalysis,
        comparables: List[ComparableSale],
        valuation: ValuationResult,
    ) -> AppraisalReport:
        """Step 5: Generate appraisal report."""
        self._report_count += 1
        report_id = f"report-{self._report_count}"

        report_type = self.config.get("report_type", "full_appraisal")

        # Subject property summary
        subject_summary = (
            f"{property_info.property_type.value.replace('_', ' ').title()} "
            f"located at {property_info.address}. "
            f"Built in {property_info.year_built}, featuring "
            f"{property_info.bedrooms} bedrooms, {property_info.bathrooms} bathrooms, "
            f"and {property_info.square_footage} square feet of living space. "
            f"Situated on {property_info.lot_size_acres} acre lot with "
            f"{property_info.garage}-car garage. Condition rated "
            f"{property_info.condition}/10."
        )

        # Market analysis summary
        market_summary = (
            f"Market analysis indicates a {market.market_condition.value.replace('_', ' ')} "
            f"with median prices around ${market.median_price:,.0f}. "
            f"Average price per square foot is ${market.avg_price_per_sqft:,.0f}. "
            f"Properties sell in approximately {market.days_on_market:.0f} days. "
            f"Supply of {market.supply_months:.1f} months suggests "
            f"{market.inventory_levels.lower()}. {market.price_trend}."
        )

        # Sales comparison approach
        sales_comp_summary = (
            f"The sales comparison approach was used with {len(comparables)} "
            f"comparable sales. Comparables ranged in price from "
            f"${min(c.sale_price for c in comparables):,.0f} to "
            f"${max(c.sale_price for c in comparables):,.0f}, "
            f"with adjusted prices clustering around "
            f"${valuation.estimated_value:,.0f}. "
            f"Adjustments were made for differences in size, bedroom count, "
            f"bathroom count, condition, and amenities. "
            f"Comparables were weighted by similarity score, with the most "
            f"similar properties receiving the highest weight."
        )

        # Income approach (if applicable)
        income_summary = "N/A - Not applicable for owner-occupied residential property"

        # Cost approach
        cost_summary = (
            f"Cost approach: Replacement cost estimated at "
            f"${property_info.square_footage * 150:,.0f} "
            f"(${150}/sqft), less depreciation of approximately "
            f"{2025 - property_info.year_built} years, plus land value of "
            f"approximately $100,000. This approach provides a secondary "
            f"indicator of value and generally supports the sales "
            f"comparison conclusion."
        )

        # Reconciliation
        reconciliation = (
            f"After careful consideration of all approaches, the sales "
            f"comparison approach was given the greatest weight due to "
            f"the availability of reliable comparable sales data and "
            f"the nature of the property. The cost approach was used as "
            f"a secondary indicator. The income approach was not applicable. "
            f"After reconciliation of all relevant data, the final "
            f"estimated market value is ${valuation.estimated_value:,.0f}, "
            f"with a confidence level of {valuation.confidence:.0f}%."
        )

        # Limiting conditions
        limiting_conditions = [
            "This valuation is based on data available at the time of analysis",
            "Physical inspection was not performed - condition is estimated",
            "Legal description and title not verified",
            "Environmental conditions not investigated",
            "Value estimate is as of current date and may change with market conditions",
            "Comparable sales data from public records and MLS - not independently verified",
            "This is an automated valuation estimate, not a formal appraisal",
            "Not intended for lending or legal purposes without further verification",
        ]

        # Certifications
        certifications = [
            "Valuation performed using standard appraisal methodology",
            "No personal interest in the subject property",
            "Compensation not contingent on reported value",
            "Analysis performed with reasonable care and due diligence",
        ]

        # Exhibits
        exhibits = [
            "Subject property photograph (if available)",
            "Comparable sales photos and details",
            "Location map - subject and comparables",
            "Market data summary",
            "Adjustment worksheet",
            "Neighborhood data",
        ]

        # Report quality score
        quality = 7.5
        if report_type == "full_appraisal":
            quality = 8.5
        if len(comparables) >= 6:
            quality += 0.5
        quality = min(10.0, quality)

        report = AppraisalReport(
            id=report_id,
            property_id=property_info.id,
            report_type=report_type,
            effective_date="Current date",
            subject_property=subject_summary,
            market_analysis=market_summary,
            sales_comparison_approach=sales_comp_summary,
            income_approach=income_summary,
            cost_approach=cost_summary,
            reconciliation=reconciliation,
            final_value_estimate=valuation.estimated_value,
            limiting_conditions=limiting_conditions,
            certifications=certifications,
            exhibits=exhibits,
            report_quality=round(quality, 1),
        )
        self._reports[report_id] = report
        return report

    # ------------------------------------------------------------------
    # Agent prompts
    # ------------------------------------------------------------------

    def _build_appraiser_prompt(self) -> str:
        """Build the system prompt for the appraiser role."""
        method = self.config.get("valuation_method", "sales_comparison").replace("_", " ")
        return (
            f"You are a certified real estate appraiser specializing in "
            f"{method} valuation. Conduct thorough, unbiased property "
            "valuations using standard appraisal methodology. Analyze "
            "comparable sales, make appropriate adjustments, and reconcile "
            "multiple approaches to value. Maintain professional standards "
            "and comply with USPAP guidelines. Clearly document assumptions, "
            "limiting conditions, and the reasoning behind value conclusions."
        )

    def _build_market_analyst_prompt(self) -> str:
        """Build the system prompt for the market analyst role."""
        return (
            "You are a real estate market analyst. Analyze local market "
            "conditions including supply and demand dynamics, price trends, "
            "absorption rates, and economic indicators. Identify market "
            "cycles and seasonal patterns. Provide data-driven market "
            "forecasts and trend analysis. Consider macroeconomic factors, "
            "demographic shifts, and local development that impact values."
        )

    def _build_data_collector_prompt(self) -> str:
        """Build the system prompt for the data collector role."""
        return (
            "You are a real estate data collection specialist. Gather "
            "accurate, comprehensive property data from multiple sources "
            "including MLS records, public records, tax assessments, and "
            "property databases. Verify data accuracy and identify the "
            "most relevant comparable sales. Collect data on property "
            "characteristics, transaction history, and market trends. "
            "Prioritize data quality and relevance."
        )

    def _build_report_writer_prompt(self) -> str:
        """Build the system prompt for the report writer role."""
        report_type = self.config.get("report_type", "full_appraisal").replace("_", " ")
        return (
            f"You are an appraisal report writer producing {report_type} "
            "reports. Generate professional, well-structured appraisal "
            "reports that clearly communicate valuation methodology, "
            "data, and conclusions. Include all necessary exhibits, "
            "certifications, and limiting conditions. Ensure reports "
            "are compliant with industry standards and are clear, "
            "accurate, and well-organized."
        )

    # ------------------------------------------------------------------
    # Metrics
    # ------------------------------------------------------------------

    def _update_realestate_metrics(self) -> None:
        """Update real estate-specific metrics."""
        total = self.realestate_metrics.properties_valued
        if total == 0:
            return

        # Valuation accuracy
        self.realestate_metrics.valuation_accuracy = 90.0

        # Report quality
        if self._reports:
            qualities = [r.report_quality for r in self._reports.values()]
            self.realestate_metrics.report_quality = sum(qualities) / len(qualities)

        # Market coverage
        self.realestate_metrics.market_coverage = 85.0

        # Average value
        if self._valuations:
            values = [v.estimated_value for v in self._valuations.values()]
            self.realestate_metrics.avg_value = sum(values) / len(values)

        # Average confidence
        if self._valuations:
            confidences = [v.confidence for v in self._valuations.values()]
            self.realestate_metrics.avg_confidence = sum(confidences) / len(confidences)

        # Turnaround time
        report_type = self.config.get("report_type", "full_appraisal")
        turnarounds = {
            "summary": 0.5,
            "desktop": 1,
            "broker_price_opinion": 1,
            "drive_by": 2,
            "full_appraisal": 5,
        }
        self.realestate_metrics.appraisal_turnaround = turnarounds.get(report_type, 3)

        # Comp selection quality
        self.realestate_metrics.comp_selection_quality = 8.0

        # Adjustment accuracy
        self.realestate_metrics.adjustment_accuracy = 85.0

        # Client satisfaction
        self.realestate_metrics.client_satisfaction = 4.3

    def get_realestate_metrics(self) -> Dict[str, Any]:
        """Get the current property valuation metrics.

        Returns:
            Dictionary with real estate metrics including valuation
            accuracy, report quality, market coverage, and turnaround time.
        """
        return self.realestate_metrics.to_dict()
