"""
Marine Navigation AI Solution Template.

A comprehensive AI-powered marine navigation assistant for
shipping companies, cruise lines, and maritime operations.
Includes multi-agent roles for navigation, weather routing,
route optimization, and safety inspection.

Typical workflow:
    voyage planning -> weather routing -> navigation
    -> safety monitoring -> arrival optimization
"""

from typing import Optional, Dict, Any, List
from enum import Enum
from dataclasses import dataclass, field

from solutions.templates.base_solution import (
    BaseSolution,
    SolutionMetadata,
    SolutionMetrics,
)


class VesselType(str, Enum):
    """Types of vessels."""
    CONTAINER_SHIP = "container_ship"
    BULK_CARRIER = "bulk_carrier"
    OIL_TANKER = "oil_tanker"
    CRUISE_SHIP = "cruise_ship"
    CARGO_SHIP = "cargo_ship"
    FERRY = "ferry"
    YACHT = "yacht"
    FISHING_VESSEL = "fishing_vessel"
    OFFSHORE = "offshore"


class SeaState(str, Enum):
    """Sea state conditions (Beaufort scale equivalent)."""
    CALM = "calm"
    SLIGHT = "slight"
    MODERATE = "moderate"
    ROUGH = "rough"
    VERY_ROUGH = "very_rough"
    HIGH = "high"
    VERY_HIGH = "very_high"
    PHENOMENAL = "phenomenal"


class NavStatus(str, Enum):
    """Navigation status."""
    AT_ANCHOR = "at_anchor"
    MOORED = "moored"
    UNDERWAY = "underway"
    MANEUVERING = "maneuvering"
    RESTRICTED = "restricted_maneuverability"
    CONSTRAINED = "constrained_by_draft"
    FISHING = "fishing"
    SAILING = "sailing"


class HazardType(str, Enum):
    """Types of navigational hazards."""
    REEF = "reef"
    SHALLOW_WATER = "shallow_water"
    TRAFFIC = "traffic"
    STORM = "storm"
    ICE = "ice"
    PIRACY = "piracy"
    NAV_AID_FAILURE = "nav_aid_failure"
    RESTRICTED_VISIBILITY = "restricted_visibility"


class MarineRole(str, Enum):
    """Agent roles in the marine navigation solution."""
    NAVIGATION_OFFICER = "navigation_officer"
    WEATHER_ANALYST = "weather_analyst"
    ROUTE_OPTIMIZER = "route_optimizer"
    SAFETY_INSPECTOR = "safety_inspector"


@dataclass
class Vessel:
    """Represents vessel information.

    Attributes:
        imo_number: IMO ship identification number.
        name: Vessel name.
        type: Vessel type.
        flag: Flag state/registry.
        length_m: Overall length in meters.
        beam_m: Beam/width in meters.
        draft_m: Current draft in meters.
        max_draft_m: Maximum draft in meters.
        gross_tonnage: Gross tonnage.
        deadweight_tonnes: Deadweight tonnage (DWT).
        service_speed_knots: Service speed in knots.
        max_speed_knots: Maximum speed in knots.
        fuel_consumption_tpd: Fuel consumption per day in tonnes.
        crew: Number of crew members.
        passengers: Number of passengers (if applicable).
    """
    imo_number: str
    name: str = ""
    type: VesselType = VesselType.CONTAINER_SHIP
    flag: str = ""
    length_m: float = 0.0
    beam_m: float = 0.0
    draft_m: float = 0.0
    max_draft_m: float = 0.0
    gross_tonnage: float = 0.0
    deadweight_tonnes: float = 0.0
    service_speed_knots: float = 0.0
    max_speed_knots: float = 0.0
    fuel_consumption_tpd: float = 0.0
    crew: int = 0
    passengers: int = 0


@dataclass
class Port:
    """Represents a port.

    Attributes:
        code: UN/LOCODE or IATA port code.
        name: Port name.
        country: Country.
        latitude: Latitude in decimal degrees.
        longitude: Longitude in decimal degrees.
        max_draft_m: Maximum draft available in meters.
        max_length_m: Maximum vessel length accommodated.
        facilities: Available facilities.
        services: Available services.
        average_wait_hours: Average waiting time in hours.
        is_ice_free: Whether port is ice-free year-round.
    """
    code: str
    name: str = ""
    country: str = ""
    latitude: float = 0.0
    longitude: float = 0.0
    max_draft_m: float = 0.0
    max_length_m: float = 0.0
    facilities: List[str] = field(default_factory=list)
    services: List[str] = field(default_factory=list)
    average_wait_hours: float = 0.0
    is_ice_free: bool = True


@dataclass
class WeatherRoutingReport:
    """Represents a weather routing report.

    Attributes:
        id: Unique report identifier.
        voyage_id: Associated voyage ID.
        sea_state: Current and forecast sea states.
        wind_conditions: Wind conditions by area.
        wave_height_m: Significant wave height in meters.
        swell_direction: Swell direction.
        precipitation: Precipitation forecast.
        visibility: Visibility conditions.
        storm_systems: Identified storm systems.
        ice_conditions: Ice conditions (if applicable).
        recommended_route: Weather-optimized route recommendation.
        delay_estimate_hours: Estimated delay from weather (hours).
        hazards: Weather-related hazards.
    """
    id: str
    voyage_id: str
    sea_state: SeaState = SeaState.MODERATE
    wind_conditions: str = ""
    wave_height_m: float = 0.0
    swell_direction: str = ""
    precipitation: str = ""
    visibility: str = "good"
    storm_systems: List[str] = field(default_factory=list)
    ice_conditions: str = "none"
    recommended_route: str = ""
    delay_estimate_hours: float = 0.0
    hazards: List[str] = field(default_factory=list)


@dataclass
class VoyagePlan:
    """Represents a complete voyage plan.

    Attributes:
        id: Unique voyage plan identifier.
        voyage_number: Voyage number.
        vessel_imo: Vessel IMO number.
        departure_port: Departure port code.
        arrival_port: Arrival port code.
        waypoints: List of route waypoints.
        total_distance_nm: Total distance in nautical miles.
        estimated_time_hours: Estimated voyage time in hours.
        departure_time: Planned departure time.
        eta: Estimated time of arrival.
        optimal_speed_knots: Optimal speed for fuel efficiency.
        route_type: Type of routing (great circle, rhumb line, coastal).
        fuel_required_tonnes: Fuel required for voyage.
        alternate_ports: List of alternate/emergency ports.
        safety_rating: Overall safety rating (0-100).
        status: Voyage plan status.
    """
    id: str
    voyage_number: str = ""
    vessel_imo: str = ""
    departure_port: str = ""
    arrival_port: str = ""
    waypoints: List[str] = field(default_factory=list)
    total_distance_nm: float = 0.0
    estimated_time_hours: float = 0.0
    departure_time: str = ""
    eta: str = ""
    optimal_speed_knots: float = 0.0
    route_type: str = "great_circle"
    fuel_required_tonnes: float = 0.0
    alternate_ports: List[str] = field(default_factory=list)
    safety_rating: float = 0.0
    status: str = "planned"


@dataclass
class SafetyInspection:
    """Represents a safety inspection result.

    Attributes:
        id: Unique inspection identifier.
        voyage_id: Associated voyage ID.
        overall_rating: Overall safety rating (0-100).
        nav_equipment_status: Status of navigation equipment.
        lifesaving_equipment: Status of lifesaving equipment.
        fire_safety: Fire safety status.
        hull_integrity: Hull integrity status.
        cargo_securing: Cargo securing status.
        crew_competency: Crew competency assessment.
        emergency_procedures: Emergency procedure readiness.
        deficiencies: List of identified deficiencies.
        recommendations: Safety recommendations.
        certificate_status: Status of safety certificates.
    """
    id: str
    voyage_id: str
    overall_rating: float = 0.0
    nav_equipment_status: str = "operational"
    lifesaving_equipment: str = "compliant"
    fire_safety: str = "compliant"
    hull_integrity: str = "good"
    cargo_securing: str = "verified"
    crew_competency: str = "certified"
    emergency_procedures: str = "current"
    deficiencies: List[str] = field(default_factory=list)
    recommendations: List[str] = field(default_factory=list)
    certificate_status: str = "valid"


@dataclass
class MarineMetrics(SolutionMetrics):
    """Extended metrics for the marine navigation solution.

    Attributes:
        voyage_time: Average voyage time in hours.
        fuel_efficiency: Fuel efficiency score (0-100).
        safety_incidents: Number of safety incidents.
        voyages_planned: Total voyages planned.
        avg_distance_nm: Average voyage distance in nautical miles.
        on_time_arrival_rate: Rate of on-time arrivals.
        route_optimization_savings: Fuel savings from route optimization.
        piracy_risk_exposure: Piracy risk exposure level.
        weather_delay_hours: Average weather-related delay hours.
        compliance_score: Regulatory compliance score.
    """
    voyage_time: float = 0.0
    fuel_efficiency: float = 0.0
    safety_incidents: int = 0
    voyages_planned: int = 0
    avg_distance_nm: float = 0.0
    on_time_arrival_rate: float = 0.0
    route_optimization_savings: float = 0.0
    piracy_risk_exposure: float = 0.0
    weather_delay_hours: float = 0.0
    compliance_score: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        """Convert marine metrics to a dictionary."""
        base = super().to_dict()
        base.update({
            "voyage_time": self.voyage_time,
            "fuel_efficiency": self.fuel_efficiency,
            "safety_incidents": self.safety_incidents,
            "voyages_planned": self.voyages_planned,
            "avg_distance_nm": self.avg_distance_nm,
            "on_time_arrival_rate": self.on_time_arrival_rate,
            "route_optimization_savings": self.route_optimization_savings,
            "piracy_risk_exposure": self.piracy_risk_exposure,
            "weather_delay_hours": self.weather_delay_hours,
            "compliance_score": self.compliance_score,
        })
        return base


class MarineNavigationSolution(BaseSolution):
    """Marine Navigation AI solution template.

    Provides a complete, pre-configured marine navigation assistant
    with multi-agent collaboration for voyage planning, weather
    routing, navigation support, safety monitoring, and arrival
    optimization.

    Pre-configured agent roles:
        - Navigation Officer: Plans and monitors vessel navigation
        - Weather Analyst: Analyzes marine weather and sea conditions
        - Route Optimizer: Optimizes routes for speed, fuel, and safety
        - Safety Inspector: Conducts safety inspections and compliance checks

    Workflow:
        1. Voyage Planning - Plan the complete voyage from departure to arrival
        2. Weather Routing - Optimize route considering weather conditions
        3. Navigation - Monitor and guide navigation during voyage
        4. Safety Monitoring - Continuous safety monitoring and alerts
        5. Arrival Optimization - Optimize arrival approach and port entry

    Configuration options:
        - vessel_type: Type of vessel
        - routing_preference: Routing optimization preference
        - safety_standard: Maritime safety standard
        - fuel_price: Fuel price per tonne
        - minimum_ukc_m: Minimum under-keel clearance in meters
    """

    def _build_metadata(self) -> SolutionMetadata:
        """Build metadata for the marine navigation solution."""
        return SolutionMetadata(
            name="marine-navigation",
            version="1.0.0",
            industry="marine",
            description=(
                "AI-powered marine navigation assistant with voyage planning, "
                "weather routing, safety monitoring, and port arrival "
                "optimization."
            ),
            author="LiuHao AI OS",
            tags=["marine", "navigation", "shipping", "weather-routing", "safety", "voyage-planning"],
            min_liuhao_version="1.0.0",
        )

    def _build_config_schema(self) -> None:
        """Define configuration fields for the marine navigation solution."""
        self.config_schema.add_field(
            name="vessel_type",
            field_type="string",
            required=False,
            default="container_ship",
            description="Type of vessel for navigation planning",
            valid_values=[
                "container_ship", "bulk_carrier", "oil_tanker", "cruise_ship",
                "cargo_ship", "ferry", "yacht", "fishing_vessel", "offshore",
            ],
        )
        self.config_schema.add_field(
            name="routing_preference",
            field_type="string",
            required=False,
            default="balanced",
            description="Routing optimization preference",
            valid_values=["fastest", "most_fuel_efficient", "safest", "balanced"],
        )
        self.config_schema.add_field(
            name="safety_standard",
            field_type="string",
            required=False,
            default="solas",
            description="Maritime safety standard",
            valid_values=["solas", "marpol", "stcw", "ism", "tmsa"],
        )
        self.config_schema.add_field(
            name="fuel_price_per_tonne",
            field_type="float",
            required=False,
            default=600.0,
            description="Marine fuel price per tonne",
        )
        self.config_schema.add_field(
            name="minimum_ukc_m",
            field_type="float",
            required=False,
            default=2.0,
            description="Minimum under-keel clearance in meters",
        )
        self.config_schema.add_field(
            name="piracy_awareness",
            field_type="bool",
            required=False,
            default=True,
            description="Whether to include piracy risk assessment",
        )
        self.config_schema.add_field(
            name="regulatory_body",
            field_type="string",
            required=False,
            default="imo",
            description="Regulatory body for compliance",
            valid_values=["imo", "uscg", "mca", "classnk", "dnv"],
        )

    def _on_initialize(self) -> None:
        """Initialize marine navigation solution state."""
        self._agents: Dict[str, Dict[str, Any]] = {}
        self._vessels: Dict[str, Vessel] = {}
        self._ports: Dict[str, Port] = {}
        self._weather_reports: Dict[str, WeatherRoutingReport] = {}
        self._voyage_plans: Dict[str, VoyagePlan] = {}
        self._safety_inspections: Dict[str, SafetyInspection] = {}
        self._voyage_count = 0
        self._weather_count = 0
        self._safety_count = 0

        # Initialize marine-specific metrics
        self.marine_metrics = MarineMetrics()

        # Vessel performance database
        self._vessel_db = self._build_vessel_database()

        # Port database
        self._port_db = self._build_port_database()

    def _build_vessel_database(self) -> Dict[str, Any]:
        """Build vessel performance database."""
        return {
            VesselType.CONTAINER_SHIP: {
                "length_m": 366,
                "beam_m": 49,
                "max_draft_m": 15.5,
                "gross_tonnage": 140000,
                "deadweight_tonnes": 150000,
                "service_speed_knots": 22,
                "max_speed_knots": 25,
                "fuel_consumption_tpd": 250,
                "crew": 25,
                "passengers": 0,
            },
            VesselType.BULK_CARRIER: {
                "length_m": 300,
                "beam_m": 50,
                "max_draft_m": 18,
                "gross_tonnage": 95000,
                "deadweight_tonnes": 180000,
                "service_speed_knots": 14,
                "max_speed_knots": 16,
                "fuel_consumption_tpd": 55,
                "crew": 22,
                "passengers": 0,
            },
            VesselType.OIL_TANKER: {
                "length_m": 330,
                "beam_m": 60,
                "max_draft_m": 22,
                "gross_tonnage": 160000,
                "deadweight_tonnes": 320000,
                "service_speed_knots": 15,
                "max_speed_knots": 17,
                "fuel_consumption_tpd": 80,
                "crew": 28,
                "passengers": 0,
            },
            VesselType.CRUISE_SHIP: {
                "length_m": 345,
                "beam_m": 41,
                "max_draft_m": 8.5,
                "gross_tonnage": 225000,
                "deadweight_tonnes": 20000,
                "service_speed_knots": 22,
                "max_speed_knots": 25,
                "fuel_consumption_tpd": 200,
                "crew": 2200,
                "passengers": 6500,
            },
            VesselType.FERRY: {
                "length_m": 200,
                "beam_m": 30,
                "max_draft_m": 6,
                "gross_tonnage": 40000,
                "deadweight_tonnes": 5000,
                "service_speed_knots": 20,
                "max_speed_knots": 24,
                "fuel_consumption_tpd": 80,
                "crew": 80,
                "passengers": 2500,
            },
        }

    def _build_port_database(self) -> Dict[str, Any]:
        """Build port information database."""
        return {
            "SGSIN": {"name": "Singapore", "country": "Singapore", "lat": 1.264, "lon": 103.849, "max_draft_m": 16, "max_length_m": 500, "wait_hours": 6, "ice_free": True},
            "USLAX": {"name": "Los Angeles", "country": "USA", "lat": 33.717, "lon": -118.270, "max_draft_m": 14, "max_length_m": 365, "wait_hours": 12, "ice_free": True},
            "USNYC": {"name": "New York", "country": "USA", "lat": 40.713, "lon": -74.006, "max_draft_m": 12, "max_length_m": 350, "wait_hours": 8, "ice_free": True},
            "NLRTM": {"name": "Rotterdam", "country": "Netherlands", "lat": 51.924, "lon": 4.478, "max_draft_m": 20, "max_length_m": 500, "wait_hours": 4, "ice_free": False},
            "CNSHA": {"name": "Shanghai", "country": "China", "lat": 31.230, "lon": 121.474, "max_draft_m": 17, "max_length_m": 400, "wait_hours": 18, "ice_free": True},
            "HKHKG": {"name": "Hong Kong", "country": "Hong Kong", "lat": 22.303, "lon": 114.169, "max_draft_m": 15, "max_length_m": 400, "wait_hours": 10, "ice_free": True},
            "DEHAM": {"name": "Hamburg", "country": "Germany", "lat": 53.551, "lon": 9.994, "max_draft_m": 13, "max_length_m": 350, "wait_hours": 6, "ice_free": False},
            "AEJEA": {"name": "Jebel Ali", "country": "UAE", "lat": 25.033, "lon": 55.070, "max_draft_m": 17, "max_length_m": 400, "wait_hours": 8, "ice_free": True},
            "JPTYO": {"name": "Tokyo", "country": "Japan", "lat": 35.652, "lon": 139.839, "max_draft_m": 14, "max_length_m": 350, "wait_hours": 5, "ice_free": True},
            "AUSYD": {"name": "Sydney", "country": "Australia", "lat": -33.869, "lon": 151.209, "max_draft_m": 12, "max_length_m": 300, "wait_hours": 4, "ice_free": True},
        }

    def _on_deploy(self) -> None:
        """Deploy marine navigation agents and workflows."""
        agent_configs = [
            (MarineRole.NAVIGATION_OFFICER, {
                "name": "Navigation Officer",
                "description": "Plans and monitors vessel navigation",
                "system_prompt": self._build_navigation_officer_prompt(),
                "tools": ["ecdis", "radar", "gps", "charts"],
            }),
            (MarineRole.WEATHER_ANALYST, {
                "name": "Weather Analyst",
                "description": "Analyzes marine weather and sea conditions",
                "system_prompt": self._build_weather_analyst_prompt(),
                "tools": ["weather_routing", "wave_models", "satellite_imagery"],
            }),
            (MarineRole.ROUTE_OPTIMIZER, {
                "name": "Route Optimizer",
                "description": "Optimizes routes for speed, fuel, and safety",
                "system_prompt": self._build_route_optimizer_prompt(),
                "tools": ["route_planner", "fuel_optimizer", "current_models"],
            }),
            (MarineRole.SAFETY_INSPECTOR, {
                "name": "Safety Inspector",
                "description": "Conducts safety inspections and compliance checks",
                "system_prompt": self._build_safety_inspector_prompt(),
                "tools": ["safety_checklist", "compliance_audit", "emergency_drill"],
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
        """Process a marine navigation/voyage planning request.

        Args:
            input_data: Dictionary containing voyage parameters.

        Returns:
            Dictionary with voyage plan, weather routing, safety
            inspection, and navigation guidance.
        """
        # Step 1: Voyage planning
        voyage_plan = self._plan_voyage(input_data)

        # Step 2: Weather routing
        weather = self._weather_route(voyage_plan)

        # Step 3: Route optimization (apply weather)
        voyage_plan = self._optimize_route(voyage_plan, weather)

        # Step 4: Safety monitoring/inspection
        safety = self._conduct_safety_inspection(voyage_plan)

        # Step 5: Arrival optimization
        arrival_optimization = self._optimize_arrival(voyage_plan, safety)

        # Store and update metrics
        self._voyage_plans[voyage_plan.id] = voyage_plan
        self._weather_reports[weather.id] = weather
        self._safety_inspections[safety.id] = safety

        self.marine_metrics.voyages_planned += 1
        self._update_marine_metrics()

        return {
            "voyage_plan": {
                "id": voyage_plan.id,
                "voyage_number": voyage_plan.voyage_number,
                "departure": voyage_plan.departure_port,
                "arrival": voyage_plan.arrival_port,
                "distance_nm": voyage_plan.total_distance_nm,
                "estimated_time_hours": voyage_plan.estimated_time_hours,
                "optimal_speed_knots": voyage_plan.optimal_speed_knots,
                "route_type": voyage_plan.route_type,
                "fuel_required_tonnes": voyage_plan.fuel_required_tonnes,
                "alternate_ports": voyage_plan.alternate_ports,
                "safety_rating": voyage_plan.safety_rating,
                "waypoints": voyage_plan.waypoints,
            },
            "weather_routing": {
                "id": weather.id,
                "sea_state": weather.sea_state.value,
                "wave_height_m": weather.wave_height_m,
                "visibility": weather.visibility,
                "storm_systems": weather.storm_systems,
                "recommended_route": weather.recommended_route,
                "delay_estimate_hours": weather.delay_estimate_hours,
                "hazards": weather.hazards,
            },
            "safety": {
                "id": safety.id,
                "overall_rating": safety.overall_rating,
                "nav_equipment": safety.nav_equipment_status,
                "certificate_status": safety.certificate_status,
                "deficiencies": safety.deficiencies,
                "recommendations": safety.recommendations,
                "emergency_procedures": safety.emergency_procedures,
            },
            "arrival_optimization": arrival_optimization,
        }

    def _on_teardown(self) -> None:
        """Clean up marine navigation solution resources."""
        self._agents.clear()
        self._vessels.clear()
        self._ports.clear()
        self._weather_reports.clear()
        self._voyage_plans.clear()
        self._safety_inspections.clear()
        self._voyage_count = 0
        self._weather_count = 0
        self._safety_count = 0

    # ------------------------------------------------------------------
    # Workflow steps
    # ------------------------------------------------------------------

    def _plan_voyage(self, data: Dict[str, Any]) -> VoyagePlan:
        """Step 1: Plan the voyage."""
        self._voyage_count += 1
        voyage_id = f"voyage-{self._voyage_count}"

        departure = data.get("departure_port", "SGSIN")
        arrival = data.get("arrival_port", "USLAX")
        vessel_type = VesselType(self.config.get("vessel_type", "container_ship"))
        perf = self._vessel_db.get(vessel_type, self._vessel_db[VesselType.CONTAINER_SHIP])

        # Calculate great circle distance
        dep_port = self._port_db.get(departure, self._port_db.get("SGSIN", {}))
        arr_port = self._port_db.get(arrival, self._port_db.get("USLAX", {}))

        import math
        R = 3440  # Nautical miles
        lat1, lon1 = math.radians(dep_port.get("lat", 1.3)), math.radians(dep_port.get("lon", 103.8))
        lat2, lon2 = math.radians(arr_port.get("lat", 33.7)), math.radians(arr_port.get("lon", -118.3))

        dlat = lat2 - lat1
        dlon = lon2 - lon1
        a = math.sin(dlat/2)**2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon/2)**2
        c = 2 * math.atan2(math.sqrt(a), math.sqrt(1-a))
        distance = R * c

        # Determine optimal speed based on routing preference
        pref = self.config.get("routing_preference", "balanced")
        speed_multipliers = {
            "fastest": 1.0,
            "most_fuel_efficient": 0.8,
            "safest": 0.85,
            "balanced": 0.9,
        }
        optimal_speed = perf["service_speed_knots"] * speed_multipliers.get(pref, 0.9)

        # Estimate voyage time
        est_time = distance / optimal_speed

        # Fuel calculation
        fuel_consumption = perf["fuel_consumption_tpd"]
        # Adjust for speed (cubic relationship)
        speed_factor = (optimal_speed / perf["service_speed_knots"]) ** 3
        daily_fuel = fuel_consumption * speed_factor
        fuel_required = daily_fuel * (est_time / 24)

        # Add reserve
        fuel_required *= 1.1  # 10% reserve

        # Alternate ports
        alternate_ports = []
        if arrival == "USLAX":
            alternate_ports = ["USNYC", "NLRTM"]
        elif arrival == "SGSIN":
            alternate_ports = ["HKHKG", "CNSHA"]
        else:
            alternate_ports = ["SGSIN", "USLAX"]

        # Waypoints (simplified)
        waypoints = [
            departure,
            f"WP01 ({round(dep_port.get('lat', 0) + 5, 1)}N, {round(dep_port.get('lon', 0) + 10, 1)}E)",
            f"WP02 (Mid-ocean waypoint)",
            f"WP03 ({round(arr_port.get('lat', 0) - 5, 1)}N, {round(arr_port.get('lon', 0) - 10, 1)}W)",
            arrival,
        ]

        # Safety rating
        base_safety = 85.0
        if pref == "safest":
            base_safety = 92.0
        elif pref == "fastest":
            base_safety = 80.0

        voyage_plan = VoyagePlan(
            id=voyage_id,
            voyage_number=f"V{self._voyage_count:04d}",
            vessel_imo=data.get("vessel_imo", f"IMO{1000000 + self._voyage_count}"),
            departure_port=departure,
            arrival_port=arrival,
            waypoints=waypoints,
            total_distance_nm=round(distance, 1),
            estimated_time_hours=round(est_time, 1),
            optimal_speed_knots=round(optimal_speed, 1),
            route_type="great_circle",
            fuel_required_tonnes=round(fuel_required, 1),
            alternate_ports=alternate_ports,
            safety_rating=base_safety,
            status="planned",
        )
        return voyage_plan

    def _weather_route(self, voyage_plan: VoyagePlan) -> WeatherRoutingReport:
        """Step 2: Perform weather routing analysis."""
        self._weather_count += 1
        wx_id = f"wx-{self._weather_count}"

        # Simulated sea state
        import random
        random.seed(hash(voyage_plan.departure_port + voyage_plan.arrival_port) % 10000)

        sea_states = [SeaState.SLIGHT, SeaState.MODERATE, SeaState.ROUGH]
        sea_state = random.choice(sea_states)
        wave_heights = {
            SeaState.CALM: 0.5,
            SeaState.SLIGHT: 1.5,
            SeaState.MODERATE: 3.0,
            SeaState.ROUGH: 5.0,
            SeaState.VERY_ROUGH: 7.0,
            SeaState.HIGH: 9.0,
        }
        wave_height = wave_heights.get(sea_state, 3.0)

        # Wind conditions
        wind_dir = random.randint(0, 360)
        wind_speed = 15 + random.randint(0, 30)

        # Storm systems
        storms = []
        if sea_state in (SeaState.ROUGH, SeaState.VERY_ROUGH):
            storms.append(f"Low pressure system (wind force 8-9)")

        # Hazards
        hazards = []
        if sea_state == SeaState.ROUGH:
            hazards.append("Rough seas - reduce speed for comfort/safety")
        if wind_speed > 30:
            hazards.append("Strong wind conditions")
        if storms:
            hazards.append("Storm system in vicinity - monitor closely")

        # Visibility
        visibility_options = ["good", "good", "moderate", "poor"]
        visibility = random.choice(visibility_options)
        if visibility == "poor":
            hazards.append("Reduced visibility - proceed with caution")

        # Delay estimate
        delay_hours = 0
        if sea_state == SeaState.MODERATE:
            delay_hours = voyage_plan.estimated_time_hours * 0.03
        elif sea_state == SeaState.ROUGH:
            delay_hours = voyage_plan.estimated_time_hours * 0.08

        # Recommended route
        if storms:
            route_rec = "Recommended route: south of storm system for smoother seas"
        else:
            route_rec = "Great circle route recommended - favorable conditions"

        weather = WeatherRoutingReport(
            id=wx_id,
            voyage_id=voyage_plan.id,
            sea_state=sea_state,
            wind_conditions=f"{wind_speed} knots from {wind_dir} degrees",
            wave_height_m=wave_height,
            swell_direction=f"{wind_dir + 180 if wind_dir < 180 else wind_dir - 180} degrees",
            precipitation="Scattered showers" if sea_state != SeaState.CALM else "Clear",
            visibility=visibility,
            storm_systems=storms,
            ice_conditions="none",
            recommended_route=route_rec,
            delay_estimate_hours=round(delay_hours, 1),
            hazards=hazards,
        )
        return weather

    def _optimize_route(
        self, voyage_plan: VoyagePlan, weather: WeatherRoutingReport
    ) -> VoyagePlan:
        """Step 3: Optimize route based on weather conditions."""
        # Adjust speed based on weather
        if weather.sea_state == SeaState.ROUGH:
            voyage_plan.optimal_speed_knots *= 0.85
            voyage_plan.estimated_time_hours = (
                voyage_plan.total_distance_nm / voyage_plan.optimal_speed_knots
            )
            voyage_plan.route_type = "weather_optimized"

        # Adjust fuel estimate
        speed_factor = (
            voyage_plan.optimal_speed_knots / self._vessel_db.get(
                VesselType(self.config.get("vessel_type", "container_ship")),
                {"service_speed_knots": 22}
            )["service_speed_knots"]
        ) ** 3

        perf = self._vessel_db.get(
            VesselType(self.config.get("vessel_type", "container_ship")), {}
        )
        daily_fuel = perf.get("fuel_consumption_tpd", 250) * speed_factor
        fuel_required = daily_fuel * (voyage_plan.estimated_time_hours / 24) * 1.1
        voyage_plan.fuel_required_tonnes = round(fuel_required, 1)

        # Update safety rating
        if weather.sea_state == SeaState.ROUGH:
            voyage_plan.safety_rating = max(70, voyage_plan.safety_rating - 8)
        elif weather.sea_state == SeaState.MODERATE:
            voyage_plan.safety_rating = max(75, voyage_plan.safety_rating - 3)

        # Route optimization savings
        pref = self.config.get("routing_preference", "balanced")
        savings = {
            "fastest": 1.0,
            "most_fuel_efficient": 8.0,
            "safest": 3.0,
            "balanced": 5.0,
        }.get(pref, 5.0)
        voyage_plan.safety_rating = round(voyage_plan.safety_rating, 1)

        return voyage_plan

    def _conduct_safety_inspection(self, voyage_plan: VoyagePlan) -> SafetyInspection:
        """Step 4: Conduct safety inspection."""
        self._safety_count += 1
        safety_id = f"safety-{self._safety_count}"

        safety_standard = self.config.get("safety_standard", "solas")
        base_rating = {
            "solas": 88,
            "marpol": 85,
            "stcw": 87,
            "ism": 86,
            "tmsa": 90,
        }.get(safety_standard, 85)

        # Deficiencies (simulated)
        deficiencies = []
        recommendations = [
            "Verify all safety certificates are current and valid",
            "Conduct emergency drill before departure",
            "Ensure lifeboat and rescue boat readiness",
            "Test all navigation equipment",
            "Verify firefighting equipment inspection status",
        ]

        # Add vessel-specific considerations
        vessel_type = VesselType(self.config.get("vessel_type", "container_ship"))
        if vessel_type == VesselType.OIL_TANKER:
            recommendations.append("Verify inert gas system operation")
            recommendations.append("Check cargo tank atmosphere monitoring")
        elif vessel_type == VesselType.CRUISE_SHIP:
            recommendations.append("Verify MES (Marine Evacuation System)")
            recommendations.append("Review passenger muster procedures")

        # Piracy risk assessment
        if self.config.get("piracy_awareness", True):
            # Check if route goes through high-risk areas
            route = voyage_plan.departure_port + voyage_plan.arrival_port
            high_risk = any(
                p in route for p in ["AEJEA", "SGSIN"]
            )  # Simplified check
            if high_risk:
                recommendations.append("Piracy risk assessment recommended for this route")
                recommendations.append("Consider BMP5 measures for high-risk areas")
                deficiencies.append("Piracy mitigation plan review required")

        inspection = SafetyInspection(
            id=safety_id,
            voyage_id=voyage_plan.id,
            overall_rating=float(base_rating - len(deficiencies) * 2),
            nav_equipment_status="operational",
            lifesaving_equipment="compliant",
            fire_safety="compliant",
            hull_integrity="good",
            cargo_securing="verified",
            crew_competency="certified",
            emergency_procedures="current",
            deficiencies=deficiencies,
            recommendations=recommendations,
            certificate_status="valid",
        )
        self._safety_inspections[safety_id] = inspection
        return inspection

    def _optimize_arrival(
        self, voyage_plan: VoyagePlan, safety: SafetyInspection
    ) -> Dict[str, Any]:
        """Step 5: Optimize arrival and port entry."""
        arr_port = self._port_db.get(voyage_plan.arrival_port, {})
        min_ukc = self.config.get("minimum_ukc_m", 2.0)

        vessel_type = VesselType(self.config.get("vessel_type", "container_ship"))
        perf = self._vessel_db.get(vessel_type, {})
        vessel_draft = perf.get("max_draft_m", 15)

        # Check port draft
        port_draft = arr_port.get("max_draft_m", 15)
        ukc = port_draft - vessel_draft
        draft_ok = ukc >= min_ukc

        # Estimated wait time
        wait_hours = arr_port.get("wait_hours", 8)

        # Optimal arrival time (tide consideration)
        arrival_options = [
            "Arrive on rising tide for maximum under-keel clearance",
            "Optimal approach speed: 8-10 knots in fairway",
            "Pilot boarding point: 5 nm from port entrance",
        ]

        # Tug requirements
        vessel_length = perf.get("length_m", 300)
        tugs_required = 2 if vessel_length > 250 else 1
        if vessel_length > 350:
            tugs_required = 3

        return {
            "port": voyage_plan.arrival_port,
            "port_name": arr_port.get("name", ""),
            "estimated_wait_hours": wait_hours,
            "under_keel_clearance_m": round(ukc, 2),
            "draft_compliant": draft_ok,
            "minimum_ukc_required_m": min_ukc,
            "tugs_required": tugs_required,
            "arrival_recommendations": arrival_options,
            "pilot_required": True,
            "vessel_traffic_service": "Active - maintain listening watch",
        }

    # ------------------------------------------------------------------
    # Agent prompts
    # ------------------------------------------------------------------

    def _build_navigation_officer_prompt(self) -> str:
        """Build the system prompt for the navigation officer role."""
        return (
            "You are a senior deck officer responsible for navigation "
            "watchkeeping and voyage planning. Maintain strict navigational "
            "standards using ECDIS, radar, and visual observations. "
            "Ensure compliance with COLREGs, maintain proper lookout, "
            "and execute passage plans safely. Monitor vessel position "
            "continuously and make prudent adjustments as conditions require. "
            "Safety of navigation is always the highest priority."
        )

    def _build_weather_analyst_prompt(self) -> str:
        """Build the system prompt for the weather analyst role."""
        return (
            "You are a marine meteorologist specializing in weather routing. "
            "Analyze global and regional weather patterns, sea state forecasts, "
            "ocean currents, and storm systems. Provide detailed weather "
            "briefings and route recommendations that optimize for safety, "
            "comfort, and fuel efficiency. Monitor changing conditions and "
            "provide timely updates to the bridge team. Identify potential "
            "hazards including heavy weather, ice, and restricted visibility."
        )

    def _build_route_optimizer_prompt(self) -> str:
        """Build the system prompt for the route optimizer role."""
        pref = self.config.get("routing_preference", "balanced")
        return (
            f"You are a marine route optimization specialist with focus on "
            f"{pref} routing. Calculate optimal routes considering great "
            "circle sailing, weather conditions, ocean currents, traffic "
            "separation schemes, and regulatory requirements. Balance speed, "
            "fuel consumption, and safety to achieve the best overall voyage "
            "outcome. Provide alternative routes and clearly explain "
            "trade-offs."
        )

    def _build_safety_inspector_prompt(self) -> str:
        """Build the system prompt for the safety inspector role."""
        standard = self.config.get("safety_standard", "solas").upper()
        return (
            f"You are a marine safety inspector ensuring compliance with "
            f"{standard} and other applicable regulations. Conduct thorough "
            "safety inspections covering navigation equipment, lifesaving "
            "appliances, fire safety, hull integrity, and emergency systems. "
            "Identify deficiencies, recommend corrective actions, and verify "
            "that the vessel is seaworthy for the intended voyage. Maintain "
            "the highest safety standards."
        )

    # ------------------------------------------------------------------
    # Metrics
    # ------------------------------------------------------------------

    def _update_marine_metrics(self) -> None:
        """Update marine-specific metrics."""
        if self._voyage_plans:
            total = len(self._voyage_plans)
            avg_time = sum(v.estimated_time_hours for v in self._voyage_plans.values()) / total
            self.marine_metrics.voyage_time = avg_time

            avg_dist = sum(v.total_distance_nm for v in self._voyage_plans.values()) / total
            self.marine_metrics.avg_distance_nm = avg_dist

            # On-time rate estimate
            on_time = sum(
                1 for v in self._voyage_plans.values()
                if v.safety_rating > 80
            )
            self.marine_metrics.on_time_arrival_rate = on_time / total if total > 0 else 0.85

        if self._weather_reports:
            total = len(self._weather_reports)
            avg_delay = sum(
                w.delay_estimate_hours for w in self._weather_reports.values()
            ) / total
            self.marine_metrics.weather_delay_hours = avg_delay

        if self._safety_inspections:
            total = len(self._safety_inspections)
            incidents = sum(
                len(s.deficiencies) for s in self._safety_inspections.values()
            )
            self.marine_metrics.safety_incidents = incidents

            avg_compliance = sum(
                s.overall_rating for s in self._safety_inspections.values()
            ) / total
            self.marine_metrics.compliance_score = avg_compliance

        # Fuel efficiency score
        pref = self.config.get("routing_preference", "balanced")
        efficiency_scores = {
            "fastest": 70,
            "most_fuel_efficient": 92,
            "safest": 80,
            "balanced": 85,
        }
        self.marine_metrics.fuel_efficiency = float(efficiency_scores.get(pref, 85))
        self.marine_metrics.route_optimization_savings = (
            efficiency_scores.get(pref, 85) - 70  # Savings vs baseline
        )

        # Piracy risk
        if self._voyage_plans and self.config.get("piracy_awareness", True):
            self.marine_metrics.piracy_risk_exposure = 0.3  # Simplified
        else:
            self.marine_metrics.piracy_risk_exposure = 0.0

    def get_marine_metrics(self) -> Dict[str, Any]:
        """Get the current marine navigation metrics.

        Returns:
            Dictionary with marine metrics including voyage time,
            fuel efficiency, safety incidents, and compliance score.
        """
        return self.marine_metrics.to_dict()
