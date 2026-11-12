"""
Mining Operations Optimizer AI Solution Template.

A comprehensive AI-powered mining operations optimization solution
for mining companies, mineral processing plants, and resource
extraction operations. Includes multi-agent roles for mining
engineering, geology, safety, and equipment management.

Typical workflow:
    site assessment -> extraction planning -> safety review
    -> equipment scheduling -> production optimization
"""

from typing import Optional, Dict, Any, List
from enum import Enum
from dataclasses import dataclass, field

from solutions.templates.base_solution import (
    BaseSolution,
    SolutionMetadata,
    SolutionMetrics,
)


class MineralType(str, Enum):
    """Types of minerals/resources."""
    COAL = "coal"
    IRON_ORE = "iron_ore"
    COPPER = "copper"
    GOLD = "gold"
    DIAMOND = "diamond"
    BAUXITE = "bauxite"
    NICKEL = "nickel"
    ZINC = "zinc"
    LITHIUM = "lithium"
    RARE_EARTH = "rare_earth"


class MiningMethod(str, Enum):
    """Mining extraction methods."""
    OPEN_PIT = "open_pit"
    UNDERGROUND = "underground"
    STRIP = "strip"
    MOUNTAINTOP = "mountaintop"
    PLACER = "placer"
    SOLUTION = "solution"
    IN_SITU = "in_situ"


class EquipmentType(str, Enum):
    """Types of mining equipment."""
    EXCAVATOR = "excavator"
    HAUL_TRUCK = "haul_truck"
    DRILL = "drill"
    BULLDOZER = "bulldozer"
    LOADER = "loader"
    CRUSHER = "crusher"
    CONVEYOR = "conveyor"
    GRADER = "grader"
    SCRAPER = "scraper"


class RiskCategory(str, Enum):
    """Risk categories for mining operations."""
    GEOLOGICAL = "geological"
    EQUIPMENT = "equipment"
    SAFETY = "safety"
    ENVIRONMENTAL = "environmental"
    OPERATIONAL = "operational"
    SUPPLY_CHAIN = "supply_chain"


class MiningRole(str, Enum):
    """Agent roles in the mining operations solution."""
    MINING_ENGINEER = "mining_engineer"
    GEOLOGIST = "geologist"
    SAFETY_OFFICER = "safety_officer"
    EQUIPMENT_MANAGER = "equipment_manager"


@dataclass
class MineSite:
    """Represents a mining site.

    Attributes:
        site_id: Unique site identifier.
        name: Site name.
        location: Geographic location.
        mineral_type: Primary mineral being extracted.
        mining_method: Extraction method used.
        reserves_tonnes: Estimated mineral reserves in tonnes.
        reserve_grade: Average reserve grade (percentage).
        operating_days_per_year: Operating days per year.
        current_production_tpd: Current production in tonnes per day.
        max_capacity_tpd: Maximum capacity in tonnes per day.
        site_area_km2: Site area in square kilometers.
        depth_m: Average mining depth in meters.
    """
    site_id: str
    name: str = ""
    location: str = ""
    mineral_type: MineralType = MineralType.IRON_ORE
    mining_method: MiningMethod = MiningMethod.OPEN_PIT
    reserves_tonnes: float = 0.0
    reserve_grade: float = 0.0
    operating_days_per_year: int = 350
    current_production_tpd: float = 0.0
    max_capacity_tpd: float = 0.0
    site_area_km2: float = 0.0
    depth_m: float = 0.0


@dataclass
class GeologicalAssessment:
    """Represents a geological assessment.

    Attributes:
        id: Unique assessment identifier.
        site_id: Associated site ID.
        reserve_estimate: Updated reserve estimate in tonnes.
        grade_distribution: Grade distribution analysis.
        ore_body_geometry: Ore body shape and orientation.
        geotechnical_conditions: Geotechnical stability assessment.
        water_table_depth: Depth of water table in meters.
        seismic_risk: Seismic activity risk level.
        confidence: Confidence level in assessment (0-1).
        exploration_recommendations: Recommended exploration areas.
        risk_areas: Identified geological risk areas.
    """
    id: str
    site_id: str
    reserve_estimate: float = 0.0
    grade_distribution: str = ""
    ore_body_geometry: str = ""
    geotechnical_conditions: str = ""
    water_table_depth: float = 0.0
    seismic_risk: str = "low"
    confidence: float = 0.0
    exploration_recommendations: List[str] = field(default_factory=list)
    risk_areas: List[str] = field(default_factory=list)


@dataclass
class Equipment:
    """Represents a piece of mining equipment.

    Attributes:
        equipment_id: Unique equipment identifier.
        type: Equipment type.
        model: Equipment model.
        capacity: Equipment capacity rating.
        utilization_rate: Current utilization rate (0-1).
        availability_rate: Availability rate (0-1).
        condition: Equipment condition rating (0-10).
        last_maintenance: Date of last maintenance.
        next_maintenance: Date of next scheduled maintenance.
        hours_operated: Total operating hours.
        fuel_consumption_per_hour: Fuel consumption rate.
        operator: Assigned operator.
    """
    equipment_id: str
    type: EquipmentType = EquipmentType.HAUL_TRUCK
    model: str = ""
    capacity: float = 0.0
    utilization_rate: float = 0.0
    availability_rate: float = 0.0
    condition: float = 0.0
    last_maintenance: str = ""
    next_maintenance: str = ""
    hours_operated: float = 0.0
    fuel_consumption_per_hour: float = 0.0
    operator: str = ""


@dataclass
class ProductionPlan:
    """Represents a production plan.

    Attributes:
        id: Unique plan identifier.
        site_id: Associated site ID.
        period: Planning period (e.g., "monthly", "quarterly").
        target_production_tonnes: Target production in tonnes.
        target_grade: Target mineral grade.
        daily_schedule: Daily production schedule.
        shift_schedule: Shift schedule details.
        equipment_allocation: Equipment allocation plan.
        personnel_required: Personnel required per shift.
        estimated_cost: Estimated production cost.
        expected_revenue: Expected revenue from production.
        profit_margin: Expected profit margin.
        confidence_level: Confidence in meeting targets (0-1).
    """
    id: str
    site_id: str
    period: str = "monthly"
    target_production_tonnes: float = 0.0
    target_grade: float = 0.0
    daily_schedule: Dict[str, Any] = field(default_factory=dict)
    shift_schedule: Dict[str, Any] = field(default_factory=dict)
    equipment_allocation: List[str] = field(default_factory=list)
    personnel_required: int = 0
    estimated_cost: float = 0.0
    expected_revenue: float = 0.0
    profit_margin: float = 0.0
    confidence_level: float = 0.0


@dataclass
class SafetyAssessment:
    """Represents a safety assessment.

    Attributes:
        id: Unique assessment identifier.
        site_id: Associated site ID.
        overall_rating: Overall safety rating (0-100).
        hazard_identification: Identified hazards by category.
        risk_matrix: Risk assessment matrix summary.
        safety_protocols: Required safety protocols.
        training_requirements: Training requirements.
        ppe_requirements: PPE requirements by area.
        emergency_procedures: Emergency procedures.
        incident_rate: Incident rate per million hours.
        recommendations: Safety improvement recommendations.
    """
    id: str
    site_id: str
    overall_rating: float = 0.0
    hazard_identification: Dict[str, List[str]] = field(default_factory=dict)
    risk_matrix: str = ""
    safety_protocols: List[str] = field(default_factory=list)
    training_requirements: List[str] = field(default_factory=list)
    ppe_requirements: List[str] = field(default_factory=list)
    emergency_procedures: List[str] = field(default_factory=list)
    incident_rate: float = 0.0
    recommendations: List[str] = field(default_factory=list)


@dataclass
class MiningMetrics(SolutionMetrics):
    """Extended metrics for the mining operations solution.

    Attributes:
        production_rate: Average production rate (tonnes/day).
        safety_incidents: Total safety incidents recorded.
        equipment_utilization: Average equipment utilization rate.
        sites_optimized: Number of sites optimized.
        production_efficiency: Overall production efficiency (0-1).
        cost_per_tonne: Average cost per tonne produced.
        recovery_rate: Mineral recovery rate (percentage).
        downtime_hours: Total equipment downtime hours.
        grade_control: Grade control accuracy.
        environmental_compliance: Environmental compliance score.
    """
    production_rate: float = 0.0
    safety_incidents: int = 0
    equipment_utilization: float = 0.0
    sites_optimized: int = 0
    production_efficiency: float = 0.0
    cost_per_tonne: float = 0.0
    recovery_rate: float = 0.0
    downtime_hours: float = 0.0
    grade_control: float = 0.0
    environmental_compliance: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        """Convert mining metrics to a dictionary."""
        base = super().to_dict()
        base.update({
            "production_rate": self.production_rate,
            "safety_incidents": self.safety_incidents,
            "equipment_utilization": self.equipment_utilization,
            "sites_optimized": self.sites_optimized,
            "production_efficiency": self.production_efficiency,
            "cost_per_tonne": self.cost_per_tonne,
            "recovery_rate": self.recovery_rate,
            "downtime_hours": self.downtime_hours,
            "grade_control": self.grade_control,
            "environmental_compliance": self.environmental_compliance,
        })
        return base


class MiningOperationsSolution(BaseSolution):
    """Mining Operations Optimizer AI solution template.

    Provides a complete, pre-configured mining operations optimization
    solution with multi-agent collaboration for site assessment,
    extraction planning, safety review, equipment scheduling, and
    production optimization.

    Pre-configured agent roles:
        - Mining Engineer: Designs extraction sequences and production plans
        - Geologist: Analyzes geological data and reserve estimates
        - Safety Officer: Conducts safety assessments and risk reviews
        - Equipment Manager: Optimizes equipment allocation and maintenance

    Workflow:
        1. Site Assessment - Evaluate site conditions and reserves
        2. Extraction Planning - Design mining sequence and schedule
        3. Safety Review - Conduct safety risk assessment
        4. Equipment Scheduling - Optimize equipment allocation
        5. Production Optimization - Maximize production efficiency

    Configuration options:
        - mineral_type: Primary mineral type
        - mining_method: Mining method used
        - production_target: Daily production target in tonnes
        - safety_standard: Safety standard to apply
        - equipment_fleet_size: Size of equipment fleet
    """

    def _build_metadata(self) -> SolutionMetadata:
        """Build metadata for the mining operations solution."""
        return SolutionMetadata(
            name="mining-operations",
            version="1.0.0",
            industry="mining",
            description=(
                "AI-powered mining operations optimizer with production "
                "planning, equipment scheduling, safety management, and "
                "geological analysis capabilities."
            ),
            author="LiuHao AI OS",
            tags=["mining", "production", "safety", "equipment", "optimization", "geology"],
            min_liuhao_version="1.0.0",
        )

    def _build_config_schema(self) -> None:
        """Define configuration fields for the mining operations solution."""
        self.config_schema.add_field(
            name="mineral_type",
            field_type="string",
            required=False,
            default="iron_ore",
            description="Primary mineral type being extracted",
            valid_values=[
                "coal", "iron_ore", "copper", "gold", "diamond",
                "bauxite", "nickel", "zinc", "lithium", "rare_earth",
            ],
        )
        self.config_schema.add_field(
            name="mining_method",
            field_type="string",
            required=False,
            default="open_pit",
            description="Mining extraction method",
            valid_values=[
                "open_pit", "underground", "strip", "mountaintop",
                "placer", "solution", "in_situ",
            ],
        )
        self.config_schema.add_field(
            name="production_target_tpd",
            field_type="float",
            required=False,
            default=50000.0,
            description="Daily production target in tonnes",
        )
        self.config_schema.add_field(
            name="safety_standard",
            field_type="string",
            required=False,
            default="iso45001",
            description="Safety management standard",
            valid_values=["iso45001", "osha", "msha", "national", "corporate"],
        )
        self.config_schema.add_field(
            name="equipment_fleet_size",
            field_type="int",
            required=False,
            default=20,
            description="Total number of equipment units in the fleet",
        )
        self.config_schema.add_field(
            name="shifts_per_day",
            field_type="int",
            required=False,
            default=3,
            description="Number of operating shifts per day",
        )
        self.config_schema.add_field(
            name="environmental_compliance_level",
            field_type="string",
            required=False,
            default="standard",
            description="Environmental compliance level",
            valid_values=["basic", "standard", "enhanced", "premium"],
        )

    def _on_initialize(self) -> None:
        """Initialize mining operations solution state."""
        self._agents: Dict[str, Dict[str, Any]] = {}
        self._sites: Dict[str, MineSite] = {}
        self._geological_assessments: Dict[str, GeologicalAssessment] = {}
        self._equipment: Dict[str, Equipment] = {}
        self._production_plans: Dict[str, ProductionPlan] = {}
        self._safety_assessments: Dict[str, SafetyAssessment] = {}
        self._site_count = 0
        self._geo_count = 0
        self._equipment_count = 0
        self._plan_count = 0
        self._safety_count = 0

        # Initialize mining-specific metrics
        self.mining_metrics = MiningMetrics()

        # Mineral properties
        self._mineral_properties = self._build_mineral_properties()

    def _build_mineral_properties(self) -> Dict[str, Any]:
        """Build mineral properties database."""
        return {
            MineralType.COAL: {"typical_grade_pct": 60, "price_per_tonne": 80, "recovery_rate": 0.85, "cost_per_tonne": 45},
            MineralType.IRON_ORE: {"typical_grade_pct": 62, "price_per_tonne": 120, "recovery_rate": 0.80, "cost_per_tonne": 60},
            MineralType.COPPER: {"typical_grade_pct": 0.8, "price_per_tonne": 8000, "recovery_rate": 0.75, "cost_per_tonne": 4500},
            MineralType.GOLD: {"typical_grade_pct": 0.001, "price_per_tonne": 2000000, "recovery_rate": 0.70, "cost_per_tonne": 1200000},
            MineralType.DIAMOND: {"typical_grade_pct": 0.00005, "price_per_tonne": 5000000, "recovery_rate": 0.60, "cost_per_tonne": 3000000},
            MineralType.BAUXITE: {"typical_grade_pct": 50, "price_per_tonne": 45, "recovery_rate": 0.85, "cost_per_tonne": 25},
            MineralType.NICKEL: {"typical_grade_pct": 1.5, "price_per_tonne": 18000, "recovery_rate": 0.70, "cost_per_tonne": 10000},
            MineralType.LITHIUM: {"typical_grade_pct": 1.2, "price_per_tonne": 25000, "recovery_rate": 0.65, "cost_per_tonne": 15000},
        }

    def _on_deploy(self) -> None:
        """Deploy mining operations agents and workflows."""
        agent_configs = [
            (MiningRole.MINING_ENGINEER, {
                "name": "Mining Engineer",
                "description": "Designs extraction sequences and production plans",
                "system_prompt": self._build_mining_engineer_prompt(),
                "tools": ["mine_design", "production_planning", "blast_design"],
            }),
            (MiningRole.GEOLOGIST, {
                "name": "Geologist",
                "description": "Analyzes geological data and reserve estimates",
                "system_prompt": self._build_geologist_prompt(),
                "tools": ["geological_modeling", "reserve_estimation", "exploration_planning"],
            }),
            (MiningRole.SAFETY_OFFICER, {
                "name": "Safety Officer",
                "description": "Conducts safety assessments and risk reviews",
                "system_prompt": self._build_safety_officer_prompt(),
                "tools": ["hazard_identification", "risk_assessment", "compliance_audit"],
            }),
            (MiningRole.EQUIPMENT_MANAGER, {
                "name": "Equipment Manager",
                "description": "Optimizes equipment allocation and maintenance",
                "system_prompt": self._build_equipment_manager_prompt(),
                "tools": ["fleet_management", "maintenance_scheduling", "utilization_tracking"],
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
        """Process a mining operations optimization request.

        Args:
            input_data: Dictionary containing mine site parameters.

        Returns:
            Dictionary with optimization results including geological
            assessment, production plan, safety assessment, and
            equipment schedule.
        """
        # Step 1: Site assessment
        site = self._assess_site(input_data)

        # Step 2: Geological assessment
        geo_assessment = self._perform_geological_assessment(site)

        # Step 3: Extraction planning / production plan
        production_plan = self._develop_production_plan(site, geo_assessment)

        # Step 4: Equipment scheduling
        equipment_schedule = self._schedule_equipment(site, production_plan)

        # Step 5: Safety review
        safety_assessment = self._perform_safety_assessment(site, production_plan)

        # Store and update metrics
        self._sites[site.site_id] = site
        self._geological_assessments[geo_assessment.id] = geo_assessment
        self._production_plans[production_plan.id] = production_plan
        self._safety_assessments[safety_assessment.id] = safety_assessment

        self.mining_metrics.sites_optimized += 1
        self._update_mining_metrics()

        return {
            "site": {
                "id": site.site_id,
                "name": site.name,
                "mineral_type": site.mineral_type.value,
                "mining_method": site.mining_method.value,
                "reserves_tonnes": site.reserves_tonnes,
                "reserve_grade": site.reserve_grade,
                "current_production_tpd": site.current_production_tpd,
                "max_capacity_tpd": site.max_capacity_tpd,
            },
            "geological_assessment": {
                "id": geo_assessment.id,
                "reserve_estimate": geo_assessment.reserve_estimate,
                "grade_distribution": geo_assessment.grade_distribution,
                "confidence": geo_assessment.confidence,
                "seismic_risk": geo_assessment.seismic_risk,
                "exploration_recommendations": geo_assessment.exploration_recommendations,
                "risk_areas": geo_assessment.risk_areas,
            },
            "production_plan": {
                "id": production_plan.id,
                "period": production_plan.period,
                "target_production_tonnes": production_plan.target_production_tonnes,
                "target_grade": production_plan.target_grade,
                "estimated_cost": production_plan.estimated_cost,
                "expected_revenue": production_plan.expected_revenue,
                "profit_margin": production_plan.profit_margin,
                "confidence_level": production_plan.confidence_level,
                "equipment_allocation": production_plan.equipment_allocation,
            },
            "equipment": {
                "fleet_size": len(self._equipment),
                "average_utilization": self._calculate_avg_utilization(),
                "average_availability": self._calculate_avg_availability(),
                "schedule_summary": equipment_schedule,
            },
            "safety": {
                "id": safety_assessment.id,
                "overall_rating": safety_assessment.overall_rating,
                "incident_rate": safety_assessment.incident_rate,
                "hazards_identified": len(safety_assessment.hazard_identification),
                "recommendations": safety_assessment.recommendations,
                "emergency_procedures": safety_assessment.emergency_procedures,
            },
        }

    def _on_teardown(self) -> None:
        """Clean up mining operations solution resources."""
        self._agents.clear()
        self._sites.clear()
        self._geological_assessments.clear()
        self._equipment.clear()
        self._production_plans.clear()
        self._safety_assessments.clear()
        self._site_count = 0
        self._geo_count = 0
        self._equipment_count = 0
        self._plan_count = 0
        self._safety_count = 0

    # ------------------------------------------------------------------
    # Workflow steps
    # ------------------------------------------------------------------

    def _assess_site(self, data: Dict[str, Any]) -> MineSite:
        """Step 1: Assess the mining site."""
        self._site_count += 1
        site_id = f"site-{self._site_count}"

        try:
            mineral = MineralType(data.get("mineral_type", self.config.get("mineral_type", "iron_ore")))
        except ValueError:
            mineral = MineralType.IRON_ORE

        try:
            method = MiningMethod(data.get("mining_method", self.config.get("mining_method", "open_pit")))
        except ValueError:
            method = MiningMethod.OPEN_PIT

        props = self._mineral_properties.get(mineral, self._mineral_properties[MineralType.IRON_ORE])
        target_production = self.config.get("production_target_tpd", 50000.0)

        # Calculate reserves based on typical mine life (10-20 years)
        mine_life_years = 15
        operating_days = 350
        reserves = target_production * operating_days * mine_life_years / props["recovery_rate"]

        site = MineSite(
            site_id=site_id,
            name=data.get("site_name", f"Mine Site {self._site_count}"),
            location=data.get("location", ""),
            mineral_type=mineral,
            mining_method=method,
            reserves_tonnes=round(reserves, 0),
            reserve_grade=props["typical_grade_pct"],
            operating_days_per_year=operating_days,
            current_production_tpd=target_production * 0.8,
            max_capacity_tpd=target_production * 1.2,
            site_area_km2=data.get("site_area_km2", 10.0),
            depth_m=data.get("depth_m", 200.0),
        )
        return site

    def _perform_geological_assessment(self, site: MineSite) -> GeologicalAssessment:
        """Step 2: Perform geological assessment."""
        self._geo_count += 1
        geo_id = f"geo-{self._geo_count}"

        # Reserve confidence based on exploration level
        confidence = 0.75  # Indicated resource level

        # Grade distribution
        grade_distribution = (
            f"Grade ranges from {site.reserve_grade * 0.7:.2f}% to "
            f"{site.reserve_grade * 1.3:.2f}%, average {site.reserve_grade:.2f}%. "
            f"Higher grade zones identified in the northern sector."
        )

        # Ore body geometry
        geometry = {
            MiningMethod.OPEN_PIT: "Tabular to lenticular ore body, sub-horizontal orientation",
            MiningMethod.UNDERGROUND: "Veins and replacement bodies, steep dip",
            MiningMethod.STRIP: "Near-horizontal seams, multiple benches",
        }.get(site.mining_method, "Irregular ore body geometry")

        # Geotechnical conditions
        geo_conditions = "Stable rock mass with occasional fault zones"
        if site.depth_m > 300:
            geo_conditions = "Moderate stress conditions with increasing depth"
        if site.mining_method == MiningMethod.UNDERGROUND:
            geo_conditions = "Rock mass classification: Good to fair. Ground support required"

        # Risk areas
        risk_areas = [
            "Western fault zone - potential water inflow",
            "Eastern boundary - near pit slope limits",
        ]
        if site.depth_m > 200:
            risk_areas.append("Deep zones - increased rock burst potential")

        # Exploration recommendations
        exploration = [
            "Infill drilling in high-grade northern zone",
            "Geophysical survey to extend known ore body",
            "Diamond drilling for reserve upgrade to proven category",
        ]

        assessment = GeologicalAssessment(
            id=geo_id,
            site_id=site.site_id,
            reserve_estimate=round(site.reserves_tonnes * confidence, 0),
            grade_distribution=grade_distribution,
            ore_body_geometry=geometry,
            geotechnical_conditions=geo_conditions,
            water_table_depth=site.depth_m * 0.6,
            seismic_risk="low" if site.mining_method == MiningMethod.OPEN_PIT else "medium",
            confidence=confidence,
            exploration_recommendations=exploration,
            risk_areas=risk_areas,
        )
        self._geological_assessments[geo_id] = assessment
        return assessment

    def _develop_production_plan(
        self, site: MineSite, geo_assessment: GeologicalAssessment
    ) -> ProductionPlan:
        """Step 3: Develop production plan."""
        self._plan_count += 1
        plan_id = f"plan-{self._plan_count}"

        target = self.config.get("production_target_tpd", 50000.0)
        shifts = self.config.get("shifts_per_day", 3)
        props = self._mineral_properties.get(site.mineral_type, {})

        # Monthly production target
        operating_days = site.operating_days_per_year / 12
        monthly_target = target * operating_days

        # Personnel required
        personnel_per_shift = {
            MiningMethod.OPEN_PIT: 40,
            MiningMethod.UNDERGROUND: 60,
            MiningMethod.STRIP: 35,
        }.get(site.mining_method, 40)
        total_personnel = personnel_per_shift * shifts

        # Cost calculation
        cost_per_tonne = props.get("cost_per_tonne", 50)
        total_cost = monthly_target * cost_per_tonne

        # Revenue calculation
        price_per_tonne = props.get("price_per_tonne", 100)
        grade_factor = site.reserve_grade / props.get("typical_grade_pct", 1)
        revenue = monthly_target * price_per_tonne * grade_factor * props.get("recovery_rate", 0.8)

        # Profit margin
        profit_margin = ((revenue - total_cost) / revenue) * 100 if revenue > 0 else 0

        # Confidence level
        confidence = 0.7 + geo_assessment.confidence * 0.2
        confidence = min(0.95, confidence)

        # Equipment allocation
        fleet_size = self.config.get("equipment_fleet_size", 20)
        allocation = []
        equipment_types = {
            EquipmentType.EXCAVATOR: max(2, fleet_size // 6),
            EquipmentType.HAUL_TRUCK: max(6, fleet_size // 3),
            EquipmentType.DRILL: max(2, fleet_size // 10),
            EquipmentType.BULLDOZER: max(2, fleet_size // 8),
            EquipmentType.LOADER: max(2, fleet_size // 10),
        }
        for eq_type, count in equipment_types.items():
            allocation.append(f"{count}x {eq_type.value.replace('_', ' ').title()}")

        # Create equipment entries
        self._create_equipment_fleet(equipment_types)

        plan = ProductionPlan(
            id=plan_id,
            site_id=site.site_id,
            period="monthly",
            target_production_tonnes=round(monthly_target, 0),
            target_grade=site.reserve_grade,
            daily_schedule={
                "shifts_per_day": shifts,
                "hours_per_shift": 8,
                "target_per_shift_tonnes": round(target / shifts, 0),
                "maintenance_window": "2 hours per shift",
            },
            shift_schedule={
                "shift_1": "06:00 - 14:00",
                "shift_2": "14:00 - 22:00",
                "shift_3": "22:00 - 06:00",
            },
            equipment_allocation=allocation,
            personnel_required=total_personnel,
            estimated_cost=round(total_cost, 2),
            expected_revenue=round(revenue, 2),
            profit_margin=round(profit_margin, 1),
            confidence_level=round(confidence, 2),
        )
        self._production_plans[plan_id] = plan
        return plan

    def _create_equipment_fleet(self, equipment_types: Dict[EquipmentType, int]) -> None:
        """Create equipment fleet entries."""
        for eq_type, count in equipment_types.items():
            for i in range(count):
                self._equipment_count += 1
                eq_id = f"eq-{self._equipment_count}"
                equipment = Equipment(
                    equipment_id=eq_id,
                    type=eq_type,
                    model=f"{eq_type.value.title()} Model {i+1}",
                    capacity=100.0 if eq_type == EquipmentType.HAUL_TRUCK else 50.0,
                    utilization_rate=round(0.7 + (i % 3) * 0.05, 2),
                    availability_rate=round(0.85 + (i % 4) * 0.03, 2),
                    condition=round(7.0 + (i % 4), 1),
                    hours_operated=5000.0 + i * 200,
                    fuel_consumption_per_hour=50.0 + i * 5,
                    operator=f"Operator {i+1}",
                )
                self._equipment[eq_id] = equipment

    def _schedule_equipment(
        self, site: MineSite, production_plan: ProductionPlan
    ) -> Dict[str, Any]:
        """Step 4: Schedule equipment for optimal utilization."""
        shifts = self.config.get("shifts_per_day", 3)

        # Calculate optimal utilization
        avg_util = self._calculate_avg_utilization()
        target_util = min(0.92, avg_util + 0.05)  # Aim for improvement

        # Maintenance scheduling
        total_equipment = len(self._equipment)
        maintenance_per_shift = max(1, int(total_equipment * 0.1 / shifts))

        return {
            "total_equipment": total_equipment,
            "shifts_per_day": shifts,
            "target_utilization": round(target_util, 2),
            "maintenance_per_shift": maintenance_per_shift,
            "rotational_schedule": "Equipment rotates through shifts for equal utilization",
            "optimization_measures": [
                "Route optimization to minimize haul distance",
                "Predictive maintenance to reduce breakdowns",
                "Operator training for efficiency improvement",
                "Fuel management program",
            ],
        }

    def _perform_safety_assessment(
        self, site: MineSite, production_plan: ProductionPlan
    ) -> SafetyAssessment:
        """Step 5: Perform safety assessment."""
        self._safety_count += 1
        safety_id = f"safety-{self._safety_count}"

        # Hazard identification by category
        hazards = {
            "geological": [
                "Rock fall hazards in high walls",
                "Slope stability monitoring required",
                "Potential for ground vibration damage",
            ],
            "equipment": [
                "Heavy equipment interaction risks",
                "Blind spots on haul trucks",
                "Maintenance-related hazards",
            ],
            "safety": [
                "Dust exposure and respiratory risks",
                "Noise-induced hearing loss risk",
                "Working at heights",
            ],
            "environmental": [
                "Dust and particulate control",
                "Water management and runoff",
                "Waste rock management",
            ],
        }

        # Underground-specific hazards
        if site.mining_method == MiningMethod.UNDERGROUND:
            hazards["geological"].extend([
                "Rock burst potential at depth",
                "Gas accumulation monitoring",
                "Roof fall hazards",
            ])
            hazards["safety"].append("Emergency egress requirements")

        # Safety protocols
        protocols = [
            "Daily pre-shift safety briefings",
            "Regular equipment safety inspections",
            "Mandatory PPE in all operating areas",
            "Permit-to-work system for high-risk tasks",
            "Stop work authority for all personnel",
        ]

        # Training requirements
        training = [
            "MSHA/regulatory safety training (annual)",
            "Equipment-specific operator certification",
            "First aid and CPR certification",
            "Hazard identification and reporting",
            "Emergency response procedures",
        ]

        # PPE requirements
        ppe = [
            "Hard hat (all personnel)",
            "Safety glasses/goggles",
            "Steel-toe boots",
            "High-visibility clothing",
            "Hearing protection in high-noise areas",
            "Respiratory protection where required",
        ]

        # Emergency procedures
        emergency = [
            "Site emergency alarm system",
            "Designated assembly points",
            "Emergency evacuation routes",
            "On-site first aid and medical response",
            "Fire response procedures",
            "Rescue team on call 24/7",
        ]

        # Overall safety rating
        safety_standard = self.config.get("safety_standard", "iso45001")
        base_rating = {
            "iso45001": 85,
            "osha": 80,
            "msha": 82,
            "national": 75,
            "corporate": 78,
        }.get(safety_standard, 80)

        # Adjust for mining method
        if site.mining_method == MiningMethod.UNDERGROUND:
            base_rating -= 5
        elif site.mining_method == MiningMethod.OPEN_PIT:
            base_rating += 2

        assessment = SafetyAssessment(
            id=safety_id,
            site_id=site.site_id,
            overall_rating=float(base_rating),
            hazard_identification=hazards,
            risk_matrix="5x5 risk matrix (likelihood x consequence)",
            safety_protocols=protocols,
            training_requirements=training,
            ppe_requirements=ppe,
            emergency_procedures=emergency,
            incident_rate=2.5,  # Per million hours, typical
            recommendations=[
                "Implement real-time gas monitoring system",
                "Enhance slope stability monitoring program",
                "Expand behavioral safety observations",
                "Increase safety training frequency",
            ],
        )
        self._safety_assessments[safety_id] = assessment
        return assessment

    def _calculate_avg_utilization(self) -> float:
        """Calculate average equipment utilization rate."""
        if not self._equipment:
            return 0.0
        total = sum(e.utilization_rate for e in self._equipment.values())
        return total / len(self._equipment)

    def _calculate_avg_availability(self) -> float:
        """Calculate average equipment availability rate."""
        if not self._equipment:
            return 0.0
        total = sum(e.availability_rate for e in self._equipment.values())
        return total / len(self._equipment)

    # ------------------------------------------------------------------
    # Agent prompts
    # ------------------------------------------------------------------

    def _build_mining_engineer_prompt(self) -> str:
        """Build the system prompt for the mining engineer role."""
        method = self.config.get("mining_method", "open_pit").replace("_", " ")
        return (
            f"You are a senior mining engineer specializing in {method} operations. "
            "Design optimal extraction sequences, blast patterns, and "
            "production schedules that maximize resource recovery while "
            "minimizing costs. Consider geotechnical constraints, equipment "
            "capabilities, and safety requirements. Optimize mine plans "
            "for long-term value and operational efficiency. Ensure all "
            "designs comply with regulatory standards."
        )

    def _build_geologist_prompt(self) -> str:
        """Build the system prompt for the geologist role."""
        mineral = self.config.get("mineral_type", "iron_ore").replace("_", " ")
        return (
            f"You are an experienced economic geologist specializing in "
            f"{mineral} deposits. Analyze geological data, build resource "
            "models, and estimate reserves according to industry standards "
            "(JORC/NI 43-101). Identify exploration targets, assess ore body "
            "continuity, and provide confidence levels for resource "
            "classifications. Clearly communicate geological risks and "
            "uncertainties."
        )

    def _build_safety_officer_prompt(self) -> str:
        """Build the system prompt for the safety officer role."""
        standard = self.config.get("safety_standard", "iso45001")
        return (
            f"You are a mining safety professional ensuring compliance with "
            f"{standard.upper()} safety standards. Identify all hazards, "
            "assess risks using formal risk matrices, and develop "
            "comprehensive safety protocols. Develop training programs, "
            "emergency response plans, and safety audit procedures. "
            "Maintain a zero-harm culture and ensure every worker returns "
            "home safely at the end of every shift."
        )

    def _build_equipment_manager_prompt(self) -> str:
        """Build the system prompt for the equipment manager role."""
        fleet_size = self.config.get("equipment_fleet_size", 20)
        return (
            f"You are an equipment fleet manager responsible for {fleet_size} "
            "units of mining equipment. Optimize equipment utilization, "
            "schedule preventive maintenance, minimize downtime, and manage "
            "equipment lifecycle costs. Implement predictive maintenance "
            "programs, track performance metrics, and ensure the right "
            "equipment is available at the right time. Balance production "
            "requirements with equipment health and longevity."
        )

    # ------------------------------------------------------------------
    # Metrics
    # ------------------------------------------------------------------

    def _update_mining_metrics(self) -> None:
        """Update mining-specific metrics."""
        if self._sites:
            total = len(self._sites)
            avg_production = sum(
                s.current_production_tpd for s in self._sites.values()
            ) / total
            self.mining_metrics.production_rate = avg_production

            # Production efficiency
            efficiencies = [
                s.current_production_tpd / s.max_capacity_tpd
                for s in self._sites.values() if s.max_capacity_tpd > 0
            ]
            if efficiencies:
                self.mining_metrics.production_efficiency = sum(efficiencies) / len(efficiencies)

        if self._equipment:
            self.mining_metrics.equipment_utilization = self._calculate_avg_utilization()

            # Downtime estimate
            total_hours = sum(e.hours_operated for e in self._equipment.values())
            downtime = total_hours * (1 - self._calculate_avg_availability())
            self.mining_metrics.downtime_hours = round(downtime, 1)

        if self._safety_assessments:
            total = len(self._safety_assessments)
            incidents = sum(
                len(s.hazard_identification) for s in self._safety_assessments.values()
            )
            self.mining_metrics.safety_incidents = incidents

        if self._production_plans:
            total = len(self._production_plans)
            avg_cost = sum(
                pp.estimated_cost / pp.target_production_tonnes
                for pp in self._production_plans.values()
                if pp.target_production_tonnes > 0
            ) / total
            self.mining_metrics.cost_per_tonne = round(avg_cost, 2)

        # Recovery rate from mineral properties
        mineral = MineralType(self.config.get("mineral_type", "iron_ore"))
        props = self._mineral_properties.get(mineral, {})
        self.mining_metrics.recovery_rate = props.get("recovery_rate", 0.75) * 100

        # Environmental compliance
        env_level = self.config.get("environmental_compliance_level", "standard")
        env_scores = {"basic": 70, "standard": 85, "enhanced": 92, "premium": 98}
        self.mining_metrics.environmental_compliance = float(env_scores.get(env_level, 85))

        # Grade control accuracy
        if self._geological_assessments:
            total = len(self._geological_assessments)
            avg_conf = sum(g.confidence for g in self._geological_assessments.values()) / total
            self.mining_metrics.grade_control = round(avg_conf * 100, 1)

    def get_mining_metrics(self) -> Dict[str, Any]:
        """Get the current mining operations metrics.

        Returns:
            Dictionary with mining metrics including production rate,
            safety incidents, equipment utilization, and efficiency.
        """
        return self.mining_metrics.to_dict()
