"""
Energy Optimization AI Solution Template.

A comprehensive AI-powered energy optimization solution for
buildings, facilities, and industrial operations. Includes
multi-agent roles for energy analysis, grid optimization,
cost advisory, and sustainability reporting.

Typical workflow:
    consumption analysis -> optimization recommendations
    -> cost calculation -> sustainability report
"""

from typing import Optional, Dict, Any, List
from enum import Enum
from dataclasses import dataclass, field

from solutions.templates.base_solution import (
    BaseSolution,
    SolutionMetadata,
    SolutionMetrics,
)


class EnergyType(str, Enum):
    """Types of energy sources."""
    ELECTRICITY = "electricity"
    NATURAL_GAS = "natural_gas"
    SOLAR = "solar"
    WIND = "wind"
    HYDRO = "hydro"
    GEOTHERMAL = "geothermal"
    BIOMASS = "biomass"
    STEAM = "steam"
    CHILLED_WATER = "chilled_water"


class FacilityType(str, Enum):
    """Types of facilities."""
    OFFICE = "office_building"
    MANUFACTURING = "manufacturing_plant"
    WAREHOUSE = "warehouse"
    DATA_CENTER = "data_center"
    RETAIL = "retail_store"
    HOSPITAL = "hospital"
    SCHOOL = "school"
    RESIDENTIAL = "residential"
    MIXED_USE = "mixed_use"


class OptimizationType(str, Enum):
    """Types of energy optimization strategies."""
    LIGHTING = "lighting"
    HVAC = "hvac"
    EQUIPMENT = "equipment"
    SCHEDULING = "scheduling"
    DEMAND_RESPONSE = "demand_response"
    RENEWABLE_INTEGRATION = "renewable_integration"
    BUILDING_ENVELOPE = "building_envelope"
    WASTE_HEAT = "waste_heat_recovery"


class EnergyRole(str, Enum):
    """Agent roles in the energy optimizer solution."""
    ENERGY_ANALYST = "energy_analyst"
    GRID_OPTIMIZER = "grid_optimizer"
    COST_ADVISOR = "cost_advisor"
    SUSTAINABILITY_EXPERT = "sustainability_expert"


@dataclass
class EnergyConsumption:
    """Represents energy consumption data.

    Attributes:
        period: Time period of the data.
        energy_type: Type of energy.
        total_kwh: Total consumption in kWh.
        peak_kw: Peak demand in kW.
        cost: Total cost of this energy type.
        cost_per_unit: Cost per unit of energy.
        trend: Consumption trend direction.
        breakdown: Consumption breakdown by area/equipment.
        hourly_profile: Hourly consumption profile.
    """
    period: str = ""
    energy_type: EnergyType = EnergyType.ELECTRICITY
    total_kwh: float = 0.0
    peak_kw: float = 0.0
    cost: float = 0.0
    cost_per_unit: float = 0.0
    trend: str = "stable"
    breakdown: Dict[str, float] = field(default_factory=dict)
    hourly_profile: List[float] = field(default_factory=list)


@dataclass
class OptimizationRecommendation:
    """Represents an energy optimization recommendation.

    Attributes:
        rec_id: Unique recommendation ID.
        title: Recommendation title.
        type: Type of optimization.
        description: Detailed description.
        current_consumption_kwh: Current consumption.
        projected_savings_kwh: Projected annual savings (kWh).
        savings_percent: Savings as percentage.
        implementation_cost: Estimated implementation cost.
        annual_savings_value: Annual dollar savings.
        roi_months: Payback period in months.
        priority: Implementation priority.
        difficulty: Implementation difficulty.
        impact: Environmental impact reduction.
    """
    rec_id: str
    title: str
    type: OptimizationType = OptimizationType.HVAC
    description: str = ""
    current_consumption_kwh: float = 0.0
    projected_savings_kwh: float = 0.0
    savings_percent: float = 0.0
    implementation_cost: float = 0.0
    annual_savings_value: float = 0.0
    roi_months: float = 0.0
    priority: str = "medium"
    difficulty: str = "medium"
    impact: str = "medium"


@dataclass
class SustainabilityReport:
    """Represents a sustainability report.

    Attributes:
        report_id: Unique report ID.
        facility: Facility name/ID.
        period: Reporting period.
        total_consumption_kwh: Total energy consumption.
        total_cost: Total energy cost.
        carbon_emissions_kg: Total CO2 emissions in kg.
        carbon_reduction_kg: Carbon reduction achieved.
        renewable_energy_percent: Percentage from renewables.
        energy_star_score: ENERGY STAR score (1-100).
        leed_equivalent: LEED equivalence rating.
        savings_summary: Summary of all savings.
        green_initiatives: List of sustainability initiatives.
        recommendations: Future sustainability recommendations.
    """
    report_id: str
    facility: str = ""
    period: str = ""
    total_consumption_kwh: float = 0.0
    total_cost: float = 0.0
    carbon_emissions_kg: float = 0.0
    carbon_reduction_kg: float = 0.0
    renewable_energy_percent: float = 0.0
    energy_star_score: float = 0.0
    leed_equivalent: str = ""
    savings_summary: Dict[str, Any] = field(default_factory=dict)
    green_initiatives: List[str] = field(default_factory=list)
    recommendations: List[str] = field(default_factory=list)


@dataclass
class EnergyMetrics(SolutionMetrics):
    """Extended metrics for the energy optimizer solution.

    Attributes:
        cost_savings_percent: Total cost savings percentage.
        energy_efficiency_ratio: Energy efficiency improvement ratio.
        carbon_reduction_percent: Carbon emission reduction percentage.
        facilities_optimized: Number of facilities optimized.
        total_savings_kwh: Total energy savings (kWh).
        total_savings_value: Total dollar savings.
        avg_roi_months: Average ROI in months.
        renewable_integration: Renewable energy integration rate.
    """
    cost_savings_percent: float = 0.0
    energy_efficiency_ratio: float = 0.0
    carbon_reduction_percent: float = 0.0
    facilities_optimized: int = 0
    total_savings_kwh: float = 0.0
    total_savings_value: float = 0.0
    avg_roi_months: float = 0.0
    renewable_integration: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        """Convert energy metrics to a dictionary."""
        base = super().to_dict()
        base.update({
            "cost_savings_percent": self.cost_savings_percent,
            "energy_efficiency_ratio": self.energy_efficiency_ratio,
            "carbon_reduction_percent": self.carbon_reduction_percent,
            "facilities_optimized": self.facilities_optimized,
            "total_savings_kwh": self.total_savings_kwh,
            "total_savings_value": self.total_savings_value,
            "avg_roi_months": self.avg_roi_months,
            "renewable_integration": self.renewable_integration,
        })
        return base


class EnergyOptimizerSolution(BaseSolution):
    """Energy Optimization AI solution template.

    Provides a complete, pre-configured energy optimization solution
    with multi-agent collaboration for energy analysis, grid
    optimization, cost reduction, and sustainability reporting.

    Pre-configured agent roles:
        - Energy Analyst: Analyzes consumption patterns and identifies waste
        - Grid Optimizer: Optimizes energy usage and demand
        - Cost Advisor: Analyzes energy costs and procurement strategies
        - Sustainability Expert: Tracks carbon footprint and green initiatives

    Workflow:
        1. Consumption Analysis - Analyze energy usage patterns
        2. Optimization Recommendations - Identify efficiency opportunities
        3. Cost Calculation - Calculate savings and ROI
        4. Sustainability Report - Generate carbon and ESG reporting

    Configuration options:
        - facility_type: Type of facility
        - energy_sources: Primary energy sources
        - optimization_goal: Primary optimization objective
        - sustainability_target: Sustainability target level
        - tariff_structure: Electricity tariff structure
    """

    def _build_metadata(self) -> SolutionMetadata:
        """Build metadata for the energy optimizer solution."""
        return SolutionMetadata(
            name="energy-optimizer",
            version="1.0.0",
            industry="energy",
            description=(
                "AI-powered energy optimization solution with consumption analysis, "
                "cost reduction, efficiency improvements, and sustainability reporting."
            ),
            author="LiuHao AI OS",
            tags=["energy", "sustainability", "optimization", "efficiency", "carbon-reduction"],
            min_liuhao_version="1.0.0",
        )

    def _build_config_schema(self) -> None:
        """Define configuration fields for the energy optimizer solution."""
        self.config_schema.add_field(
            name="facility_type",
            field_type="string",
            required=True,
            description="Type of facility to optimize",
            valid_values=[t.value for t in FacilityType],
        )
        self.config_schema.add_field(
            name="optimization_goal",
            field_type="string",
            required=False,
            default="cost_savings",
            description="Primary optimization objective",
            valid_values=["cost_savings", "carbon_reduction", "energy_independence", "reliability", "balanced"],
        )
        self.config_schema.add_field(
            name="sustainability_target",
            field_type="string",
            required=False,
            default="moderate",
            description="Sustainability target level",
            valid_values=["basic", "moderate", "aggressive", "net_zero"],
        )
        self.config_schema.add_field(
            name="tariff_structure",
            field_type="string",
            required=False,
            default="flat_rate",
            description="Electricity tariff structure",
            valid_values=["flat_rate", "time_of_use", "demand_charge", "real_time_pricing"],
        )
        self.config_schema.add_field(
            name="annual_budget_kwh",
            field_type="float",
            required=False,
            default=1000000.0,
            description="Annual energy budget in kWh",
        )
        self.config_schema.add_field(
            name="renewable_penetration_target",
            field_type="float",
            required=False,
            default=0.3,
            description="Target renewable energy penetration (0-1)",
        )
        self.config_schema.add_field(
            name="facility_size_sqft",
            field_type="float",
            required=False,
            default=50000.0,
            description="Facility size in square feet",
        )

    def _on_initialize(self) -> None:
        """Initialize energy optimizer solution state."""
        self._agents: Dict[str, Dict[str, Any]] = {}
        self._consumption_data: Dict[str, EnergyConsumption] = {}
        self._recommendations: Dict[str, OptimizationRecommendation] = {}
        self._sustainability_reports: Dict[str, SustainabilityReport] = {}
        self._rec_count = 0
        self._report_count = 0

        # Initialize energy-specific metrics
        self.energy_metrics = EnergyMetrics()

    def _on_deploy(self) -> None:
        """Deploy energy optimizer agents and workflows."""
        agent_configs = [
            (EnergyRole.ENERGY_ANALYST, {
                "name": "Energy Analyst",
                "description": "Analyzes consumption patterns and identifies waste",
                "system_prompt": self._build_energy_analyst_prompt(),
                "tools": ["consumption_analyzer", "pattern_detector", "benchmarking"],
            }),
            (EnergyRole.GRID_OPTIMIZER, {
                "name": "Grid Optimizer",
                "description": "Optimizes energy usage and demand",
                "system_prompt": self._build_grid_optimizer_prompt(),
                "tools": ["demand_response", "load_shifting", "peak_shaving"],
            }),
            (EnergyRole.COST_ADVISOR, {
                "name": "Cost Advisor",
                "description": "Analyzes energy costs and procurement strategies",
                "system_prompt": self._build_cost_advisor_prompt(),
                "tools": ["rate_analysis", "procurement_optimizer", "roi_calculator"],
            }),
            (EnergyRole.SUSTAINABILITY_EXPERT, {
                "name": "Sustainability Expert",
                "description": "Tracks carbon footprint and green initiatives",
                "system_prompt": self._build_sustainability_prompt(),
                "tools": ["carbon_calculator", "esg_reporting", "renewable_sizing"],
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
        """Process an energy optimization request through the workflow.

        Args:
            input_data: Dictionary containing facility and energy data.

        Returns:
            Dictionary with optimization results including consumption analysis,
            recommendations, cost savings, and sustainability report.
        """
        # Step 1: Consumption analysis
        consumption = self._analyze_consumption(input_data)

        # Step 2: Generate optimization recommendations
        recommendations = self._generate_recommendations(consumption)

        # Step 3: Cost calculation and ROI
        cost_analysis = self._calculate_costs(consumption, recommendations)

        # Step 4: Sustainability report
        sustainability = self._generate_sustainability_report(
            consumption, recommendations
        )

        # Update metrics
        self.energy_metrics.facilities_optimized += 1
        self.energy_metrics.total_savings_kwh += sum(
            r.projected_savings_kwh for r in recommendations
        )
        self.energy_metrics.total_savings_value += sum(
            r.annual_savings_value for r in recommendations
        )
        self._update_energy_metrics(recommendations)

        return {
            "consumption_analysis": {
                "facility_type": self.config.get("facility_type", "office_building"),
                "total_consumption_kwh": consumption.total_kwh,
                "peak_demand_kw": consumption.peak_kw,
                "total_cost": consumption.cost,
                "energy_type": consumption.energy_type.value,
                "trend": consumption.trend,
                "breakdown": consumption.breakdown,
            },
            "optimization_recommendations": {
                "total_recommendations": len(recommendations),
                "total_savings_kwh": sum(r.projected_savings_kwh for r in recommendations),
                "total_savings_value": sum(r.annual_savings_value for r in recommendations),
                "weighted_roi_months": cost_analysis.get("weighted_roi_months", 0),
                "recommendations": [
                    {
                        "id": r.rec_id,
                        "title": r.title,
                        "type": r.type.value,
                        "savings_kwh": r.projected_savings_kwh,
                        "savings_percent": r.savings_percent,
                        "cost": r.implementation_cost,
                        "annual_savings": r.annual_savings_value,
                        "roi_months": r.roi_months,
                        "priority": r.priority,
                        "difficulty": r.difficulty,
                    }
                    for r in recommendations
                ],
            },
            "cost_analysis": cost_analysis,
            "sustainability": {
                "carbon_emissions_kg": sustainability.carbon_emissions_kg,
                "carbon_reduction_kg": sustainability.carbon_reduction_kg,
                "carbon_reduction_percent": self.energy_metrics.carbon_reduction_percent,
                "renewable_percent": sustainability.renewable_energy_percent,
                "energy_star_score": sustainability.energy_star_score,
                "leed_equivalent": sustainability.leed_equivalent,
                "initiatives": sustainability.green_initiatives,
                "recommendations": sustainability.recommendations,
            },
        }

    def _on_teardown(self) -> None:
        """Clean up energy optimizer solution resources."""
        self._agents.clear()
        self._consumption_data.clear()
        self._recommendations.clear()
        self._sustainability_reports.clear()
        self._rec_count = 0
        self._report_count = 0

    # ------------------------------------------------------------------
    # Workflow steps
    # ------------------------------------------------------------------

    def _analyze_consumption(self, data: Dict[str, Any]) -> EnergyConsumption:
        """Step 1: Analyze energy consumption patterns."""
        facility = FacilityType(self.config.get("facility_type", FacilityType.OFFICE.value))
        size = self.config.get("facility_size_sqft", 50000.0)
        annual_budget = self.config.get("annual_budget_kwh", 1000000.0)

        # Base consumption by facility type (kWh/sqft/year)
        intensity = {
            FacilityType.OFFICE: 15.0,
            FacilityType.MANUFACTURING: 50.0,
            FacilityType.WAREHOUSE: 8.0,
            FacilityType.DATA_CENTER: 200.0,
            FacilityType.RETAIL: 20.0,
            FacilityType.HOSPITAL: 45.0,
            FacilityType.SCHOOL: 12.0,
            FacilityType.RESIDENTIAL: 5.0,
        }
        base_consumption = size * intensity.get(facility, 15.0)

        # Current consumption (some inefficiency built in)
        current_consumption = base_consumption * 1.15  # 15% inefficient
        current_consumption = min(current_consumption, annual_budget * 1.2)

        # Cost calculation
        rate = 0.12  # $0.12/kWh
        total_cost = current_consumption * rate

        # Breakdown by system
        breakdown = {
            "HVAC": current_consumption * 0.40,
            "Lighting": current_consumption * 0.20,
            "Equipment": current_consumption * 0.25,
            "Water Heating": current_consumption * 0.08,
            "Other": current_consumption * 0.07,
        }

        # Peak demand
        peak_kw = current_consumption / (365 * 8)  # Approximate peak

        consumption = EnergyConsumption(
            period="annual",
            energy_type=EnergyType.ELECTRICITY,
            total_kwh=round(current_consumption, 0),
            peak_kw=round(peak_kw, 1),
            cost=round(total_cost, 2),
            cost_per_unit=rate,
            trend="rising" if annual_budget < current_consumption else "stable",
            breakdown={k: round(v, 0) for k, v in breakdown.items()},
        )
        self._consumption_data["main"] = consumption
        return consumption

    def _generate_recommendations(
        self, consumption: EnergyConsumption
    ) -> List[OptimizationRecommendation]:
        """Step 2: Generate energy optimization recommendations."""
        goal = self.config.get("optimization_goal", "cost_savings")
        recommendations = []

        rec_definitions = [
            (OptimizationType.LIGHTING, "LED Lighting Upgrade", 0.50, 5000, "low", "easy"),
            (OptimizationType.HVAC, "HVAC Optimization & Controls", 0.25, 15000, "high", "medium"),
            (OptimizationType.SCHEDULING, "Smart Scheduling & Occupancy", 0.15, 3000, "high", "easy"),
            (OptimizationType.EQUIPMENT, "High-Efficiency Equipment", 0.10, 20000, "medium", "medium"),
            (OptimizationType.DEMAND_RESPONSE, "Demand Response Program", 0.08, 2000, "high", "easy"),
            (OptimizationType.BUILDING_ENVELOPE, "Building Envelope Improvements", 0.12, 30000, "medium", "hard"),
        ]

        for opt_type, title, savings_ratio, cost, priority, difficulty in rec_definitions:
            self._rec_count += 1
            rec_id = f"rec-{self._rec_count}"

            current = consumption.total_kwh * (
                0.4 if opt_type == OptimizationType.HVAC else
                0.2 if opt_type == OptimizationType.LIGHTING else
                0.25 if opt_type == OptimizationType.EQUIPMENT else
                0.2 if opt_type == OptimizationType.SCHEDULING else
                1.0  # For demand response, calculate on total
            )

            savings_kwh = current * savings_ratio
            savings_value = savings_kwh * consumption.cost_per_unit
            roi_months = (cost / savings_value * 12) if savings_value > 0 else 999

            # Adjust priority based on goal
            if goal == "cost_savings" and roi_months < 24:
                priority = "high"
            elif goal == "carbon_reduction" and savings_ratio > 0.15:
                priority = "high"

            # Environmental impact
            impact = "high" if savings_kwh > 50000 else "medium" if savings_kwh > 10000 else "low"

            rec = OptimizationRecommendation(
                rec_id=rec_id,
                title=title,
                type=opt_type,
                description=f"Upgrade/optimize {opt_type.value} to reduce energy consumption by {savings_ratio*100:.0f}% in this category.",
                current_consumption_kwh=round(current, 0),
                projected_savings_kwh=round(savings_kwh, 0),
                savings_percent=round(savings_ratio * 100, 1),
                implementation_cost=cost,
                annual_savings_value=round(savings_value, 2),
                roi_months=round(roi_months, 1),
                priority=priority,
                difficulty=difficulty,
                impact=impact,
            )
            recommendations.append(rec)
            self._recommendations[rec_id] = rec

        # Sort by priority then ROI
        priority_order = {"high": 0, "medium": 1, "low": 2}
        recommendations.sort(key=lambda r: (priority_order.get(r.priority, 2), r.roi_months))
        return recommendations

    def _calculate_costs(
        self, consumption: EnergyConsumption, recommendations: List[OptimizationRecommendation]
    ) -> Dict[str, Any]:
        """Step 3: Calculate costs, savings, and ROI."""
        total_savings_kwh = sum(r.projected_savings_kwh for r in recommendations)
        total_savings_value = sum(r.annual_savings_value for r in recommendations)
        total_investment = sum(r.implementation_cost for r in recommendations)

        savings_percent = (total_savings_kwh / consumption.total_kwh) * 100 if consumption.total_kwh > 0 else 0

        # Weighted ROI
        if total_savings_value > 0:
            weighted_roi = (total_investment / total_savings_value) * 12
        else:
            weighted_roi = 999

        # Demand charge savings
        tariff = self.config.get("tariff_structure", "flat_rate")
        demand_savings = 0.0
        if tariff in ("demand_charge", "time_of_use"):
            demand_savings = total_savings_value * 0.15

        return {
            "current_annual_cost": consumption.cost,
            "projected_annual_cost": round(consumption.cost - total_savings_value - demand_savings, 2),
            "total_savings_value": round(total_savings_value, 2),
            "total_investment": total_investment,
            "savings_percent": round(savings_percent, 1),
            "weighted_roi_months": round(weighted_roi, 1),
            "demand_charge_savings": round(demand_savings, 2),
            "payback_years": round(weighted_roi / 12, 1),
            "lifecycle_savings_10yr": round(total_savings_value * 10 - total_investment, 2),
            "financing_options": [
                "Energy savings performance contract (ESPC)",
                "Power purchase agreement (PPA)",
                "Utility rebates and incentives",
                "Green financing / PACE",
            ],
        }

    def _generate_sustainability_report(
        self, consumption: EnergyConsumption, recommendations: List[OptimizationRecommendation]
    ) -> SustainabilityReport:
        """Step 4: Generate sustainability and ESG report."""
        self._report_count += 1
        report_id = f"sustain-{self._report_count}"

        # Carbon emissions (kg CO2) - US grid average: ~0.417 kg CO2/kWh
        emission_factor = 0.417
        current_emissions = consumption.total_kwh * emission_factor
        total_savings = sum(r.projected_savings_kwh for r in recommendations)
        carbon_reduction = total_savings * emission_factor

        # Renewable target
        target = self.config.get("renewable_penetration_target", 0.3)
        current_renewable = target * 0.5  # Currently at half target

        # ENERGY STAR score
        facility = self.config.get("facility_type", "office_building")
        base_score = 50
        efficiency_bonus = min(30, (total_savings / consumption.total_kwh) * 100 * 2)
        energy_star = min(100, base_score + efficiency_bonus)

        # LEED equivalence
        if energy_star >= 90:
            leed = "Platinum"
        elif energy_star >= 80:
            leed = "Gold"
        elif energy_star >= 65:
            leed = "Silver"
        elif energy_star >= 50:
            leed = "Certified"
        else:
            leed = "Not certified"

        # Green initiatives
        initiatives = [
            "LED lighting retrofit",
            "Smart HVAC controls",
            "Occupancy-based scheduling",
        ]

        target_level = self.config.get("sustainability_target", "moderate")
        if target_level in ("aggressive", "net_zero"):
            initiatives.extend([
                "Solar panel installation",
                "Battery energy storage",
                "Electric vehicle charging",
            ])

        report = SustainabilityReport(
            report_id=report_id,
            facility=facility,
            period="annual",
            total_consumption_kwh=consumption.total_kwh,
            total_cost=consumption.cost,
            carbon_emissions_kg=round(current_emissions, 0),
            carbon_reduction_kg=round(carbon_reduction, 0),
            renewable_energy_percent=round(current_renewable * 100, 1),
            energy_star_score=round(energy_star, 1),
            leed_equivalent=leed,
            savings_summary={
                "energy_savings_kwh": total_savings,
                "cost_savings": sum(r.annual_savings_value for r in recommendations),
                "carbon_reduction_kg": carbon_reduction,
                "trees_equivalent": round(carbon_reduction / 21, 0),  # ~21kg CO2/tree/year
            },
            green_initiatives=initiatives,
            recommendations=[
                f"Increase renewable energy to {target*100:.0f}%",
                "Implement real-time energy monitoring",
                "Engage occupants in energy conservation",
                "Set quarterly energy reduction targets",
            ],
        )
        self._sustainability_reports[report_id] = report
        return report

    # ------------------------------------------------------------------
    # Agent prompts
    # ------------------------------------------------------------------

    def _build_energy_analyst_prompt(self) -> str:
        """Build the system prompt for the energy analyst role."""
        facility = self.config.get("facility_type", "office building")
        return (
            f"You are an energy analyst specializing in {facility} facilities. "
            f"Analyze energy consumption data to identify patterns, inefficiencies, "
            f"and waste. Benchmark against similar facilities. Use interval data "
            f"analysis, load profiling, and end-use disaggregation to pinpoint "
            f"specific energy saving opportunities."
        )

    def _build_grid_optimizer_prompt(self) -> str:
        """Build the system prompt for the grid optimizer role."""
        tariff = self.config.get("tariff_structure", "flat_rate")
        return (
            f"You are an energy grid optimization specialist. Optimize energy "
            f"usage patterns to reduce costs and improve efficiency under a "
            f"{tariff} tariff structure. Implement demand response, load shifting, "
            f"peak shaving, and storage strategies. Balance cost, reliability, "
            f"and sustainability objectives."
        )

    def _build_cost_advisor_prompt(self) -> str:
        """Build the system prompt for the cost advisor role."""
        return (
            "You are an energy cost and procurement advisor. Analyze utility "
            "tariffs, identify cost-saving opportunities, and recommend procurement "
            "strategies. Calculate ROI, payback periods, and lifecycle costs for "
            "energy projects. Identify available incentives, rebates, and "
            "financing options."
        )

    def _build_sustainability_prompt(self) -> str:
        """Build the system prompt for the sustainability expert role."""
        target = self.config.get("sustainability_target", "moderate")
        return (
            f"You are a sustainability and ESG specialist targeting {target} level. "
            f"Calculate carbon footprints, track emissions reductions, and develop "
            f"sustainability strategies. Recommend renewable energy integration, "
            f"energy efficiency measures, and green building practices. "
            f"Generate ESG and sustainability reporting aligned with standards."
        )

    # ------------------------------------------------------------------
    # Metrics
    # ------------------------------------------------------------------

    def _update_energy_metrics(self, recommendations: List[OptimizationRecommendation]) -> None:
        """Update energy-specific metrics."""
        if recommendations:
            rois = [r.roi_months for r in recommendations if r.roi_months < 200]
            if rois:
                self.energy_metrics.avg_roi_months = sum(rois) / len(rois)

        if self._consumption_data:
            total = self._consumption_data.get("main", EnergyConsumption()).total_kwh
            if total > 0:
                self.energy_metrics.cost_savings_percent = (
                    self.energy_metrics.total_savings_kwh / total * 100
                )
                self.energy_metrics.carbon_reduction_percent = (
                    self.energy_metrics.cost_savings_percent
                )
                self.energy_metrics.energy_efficiency_ratio = (
                    1 + self.energy_metrics.cost_savings_percent / 100
                )

        self.energy_metrics.renewable_integration = self.config.get(
            "renewable_penetration_target", 0.3
        ) * 0.5

    def get_energy_metrics(self) -> Dict[str, Any]:
        """Get the current energy-specific metrics.

        Returns:
            Dictionary with energy metrics including cost savings,
            efficiency ratio, carbon reduction, and ROI.
        """
        return self.energy_metrics.to_dict()
