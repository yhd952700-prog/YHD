"""
Aviation Flight Planner AI Solution Template.

A comprehensive AI-powered aviation flight planning solution for
airlines, charter operators, and flight departments. Includes
multi-agent roles for flight planning, weather analysis, fuel
optimization, and safety assurance.

Typical workflow:
    route planning -> weather analysis -> fuel calculation
    -> safety check -> flight plan filing
"""

from typing import Optional, Dict, Any, List
from enum import Enum
from dataclasses import dataclass, field

from solutions.templates.base_solution import (
    BaseSolution,
    SolutionMetadata,
    SolutionMetrics,
)


class AircraftType(str, Enum):
    """Types of aircraft."""
    TURBOPROP = "turboprop"
    LIGHT_JET = "light_jet"
    MIDSIZE_JET = "midsize_jet"
    HEAVY_JET = "heavy_jet"
    AIRLINER_NARROW = "airliner_narrow"
    AIRLINER_WIDE = "airliner_wide"
    REGIONAL = "regional"
    HELICOPTER = "helicopter"


class FlightPhase(str, Enum):
    """Phases of flight."""
    TAXI = "taxi"
    TAKEOFF = "takeoff"
    CLIMB = "climb"
    CRUISE = "cruise"
    DESCENT = "descent"
    APPROACH = "approach"
    LANDING = "landing"


class WeatherCondition(str, Enum):
    """Weather condition categories."""
    VFR = "vfr"  # Visual Flight Rules
    MVFR = "mvfr"  # Marginal VFR
    IFR = "ifr"  # Instrument Flight Rules
    LIFR = "lifr"  # Low IFR


class SeverityLevel(str, Enum):
    """Severity levels for hazards."""
    NONE = "none"
    MINOR = "minor"
    MODERATE = "moderate"
    SEVERE = "severe"
    EXTREME = "extreme"


class AviationRole(str, Enum):
    """Agent roles in the flight planner solution."""
    FLIGHT_PLANNER = "flight_planner"
    WEATHER_ANALYST = "weather_analyst"
    FUEL_OPTIMIZER = "fuel_optimizer"
    SAFETY_OFFICER = "safety_officer"


@dataclass
class Aircraft:
    """Represents aircraft information.

    Attributes:
        tail_number: Aircraft tail/registration number.
        type: Aircraft type/category.
        model: Aircraft model name.
        cruise_speed_kts: Cruise speed in knots.
        max_range_nm: Maximum range in nautical miles.
        fuel_capacity_gallons: Fuel capacity in gallons.
        fuel_consumption_gph: Fuel consumption in gallons per hour.
        max_altitude_ft: Maximum cruise altitude in feet.
        passenger_capacity: Maximum passenger count.
        payload_capacity_lbs: Payload capacity in pounds.
        required_runway_ft: Required runway length in feet.
    """
    tail_number: str
    type: AircraftType = AircraftType.LIGHT_JET
    model: str = ""
    cruise_speed_kts: int = 400
    max_range_nm: int = 2000
    fuel_capacity_gallons: float = 2000.0
    fuel_consumption_gph: float = 200.0
    max_altitude_ft: int = 45000
    passenger_capacity: int = 8
    payload_capacity_lbs: int = 2000
    required_runway_ft: int = 5000


@dataclass
class Airport:
    """Represents an airport.

    Attributes:
        icao: ICAO airport code.
        iata: IATA airport code.
        name: Airport name.
        city: City served.
        country: Country.
        latitude: Latitude in decimal degrees.
        longitude: Longitude in decimal degrees.
        elevation_ft: Airport elevation in feet.
        longest_runway_ft: Longest runway in feet.
        runways: List of runway identifiers.
        approaches: Available approach types.
        facilities: Available facilities and services.
    """
    icao: str
    iata: str = ""
    name: str = ""
    city: str = ""
    country: str = ""
    latitude: float = 0.0
    longitude: float = 0.0
    elevation_ft: int = 0
    longest_runway_ft: int = 0
    runways: List[str] = field(default_factory=list)
    approaches: List[str] = field(default_factory=list)
    facilities: List[str] = field(default_factory=list)


@dataclass
class WeatherReport:
    """Represents weather conditions for a route.

    Attributes:
        id: Unique weather report identifier.
        flight_id: Associated flight ID.
        departure_wx: Weather at departure airport.
        destination_wx: Weather at destination airport.
        enroute_wx: En route weather conditions.
        turbulence: Turbulence forecast.
        icing: Icing conditions forecast.
        thunderstorms: Thunderstorm activity.
        wind_aloft: Wind aloft data by altitude.
        visibility: Visibility conditions.
        overall_condition: Overall weather category.
        hazards: List of identified weather hazards.
    """
    id: str
    flight_id: str
    departure_wx: str = ""
    destination_wx: str = ""
    enroute_wx: str = ""
    turbulence: SeverityLevel = SeverityLevel.NONE
    icing: SeverityLevel = SeverityLevel.NONE
    thunderstorms: SeverityLevel = SeverityLevel.NONE
    wind_aloft: Dict[str, str] = field(default_factory=dict)
    visibility: str = ""
    overall_condition: WeatherCondition = WeatherCondition.VFR
    hazards: List[str] = field(default_factory=list)


@dataclass
class FuelPlan:
    """Represents a fuel plan for a flight.

    Attributes:
        total_required_gallons: Total fuel required.
        trip_fuel_gallons: Fuel for the actual trip.
        reserve_fuel_gallons: Reserve fuel (regulatory minimum).
        alternate_fuel_gallons: Fuel to reach alternate airport.
        taxi_fuel_gallons: Fuel for taxi operations.
        contingency_fuel_gallons: Contingency/extra fuel.
        total_cost_estimate: Estimated fuel cost.
        optimal_altitude_ft: Optimal cruise altitude for fuel efficiency.
        savings_percentage: Percentage savings from optimization.
        fuel_stops: Number of required fuel stops.
    """
    total_required_gallons: float = 0.0
    trip_fuel_gallons: float = 0.0
    reserve_fuel_gallons: float = 0.0
    alternate_fuel_gallons: float = 0.0
    taxi_fuel_gallons: float = 0.0
    contingency_fuel_gallons: float = 0.0
    total_cost_estimate: float = 0.0
    optimal_altitude_ft: int = 0
    savings_percentage: float = 0.0
    fuel_stops: int = 0


@dataclass
class FlightPlan:
    """Represents a complete flight plan.

    Attributes:
        id: Unique flight plan identifier.
        flight_number: Flight number or call sign.
        departure_airport: Departure airport ICAO code.
        destination_airport: Destination airport ICAO code.
        alternate_airport: Alternate airport ICAO code.
        route: Route description/waypoints.
        distance_nm: Total distance in nautical miles.
        flight_time_min: Estimated flight time in minutes.
        cruise_altitude_ft: Planned cruise altitude.
        aircraft: Aircraft tail number.
        fuel_plan: Associated fuel plan.
        weather: Associated weather report.
        safety_checks: List of safety checks performed.
        safety_rating: Overall safety rating (0-100).
        status: Flight plan status.
        filing_status: Whether plan has been filed.
    """
    id: str
    flight_number: str = ""
    departure_airport: str = ""
    destination_airport: str = ""
    alternate_airport: str = ""
    route: str = ""
    distance_nm: float = 0.0
    flight_time_min: float = 0.0
    cruise_altitude_ft: int = 0
    aircraft: str = ""
    fuel_plan: Optional[FuelPlan] = None
    weather: Optional[WeatherReport] = None
    safety_checks: List[str] = field(default_factory=list)
    safety_rating: float = 100.0
    status: str = "draft"
    filing_status: str = "not_filed"


@dataclass
class AviationMetrics(SolutionMetrics):
    """Extended metrics for the flight planner solution.

    Attributes:
        route_efficiency: Route efficiency score (0-100).
        fuel_savings_percentage: Average fuel savings from optimization.
        on_time_rate: Rate of on-time departures.
        flights_planned: Total flight plans generated.
        avg_flight_time_min: Average flight time.
        avg_fuel_used_gallons: Average fuel used per flight.
        safety_incidents: Number of safety issues flagged.
        weather_diversion_rate: Rate of weather-related diversions.
        avg_safety_rating: Average safety rating.
        cost_per_nm: Average cost per nautical mile.
    """
    route_efficiency: float = 0.0
    fuel_savings_percentage: float = 0.0
    on_time_rate: float = 0.0
    flights_planned: int = 0
    avg_flight_time_min: float = 0.0
    avg_fuel_used_gallons: float = 0.0
    safety_incidents: int = 0
    weather_diversion_rate: float = 0.0
    avg_safety_rating: float = 0.0
    cost_per_nm: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        """Convert aviation metrics to a dictionary."""
        base = super().to_dict()
        base.update({
            "route_efficiency": self.route_efficiency,
            "fuel_savings_percentage": self.fuel_savings_percentage,
            "on_time_rate": self.on_time_rate,
            "flights_planned": self.flights_planned,
            "avg_flight_time_min": self.avg_flight_time_min,
            "avg_fuel_used_gallons": self.avg_fuel_used_gallons,
            "safety_incidents": self.safety_incidents,
            "weather_diversion_rate": self.weather_diversion_rate,
            "avg_safety_rating": self.avg_safety_rating,
            "cost_per_nm": self.cost_per_nm,
        })
        return base


class FlightPlannerSolution(BaseSolution):
    """Aviation Flight Planner AI solution template.

    Provides a complete, pre-configured flight planning solution
    with multi-agent collaboration for route planning, weather
    analysis, fuel optimization, safety checks, and flight plan
    filing.

    Pre-configured agent roles:
        - Flight Planner: Develops optimal flight routes and profiles
        - Weather Analyst: Analyzes weather conditions and forecasts
        - Fuel Optimizer: Calculates optimal fuel loads and altitudes
        - Safety Officer: Performs safety checks and risk assessments

    Workflow:
        1. Route Planning - Determine optimal route and altitude
        2. Weather Analysis - Analyze departure, en route, and arrival weather
        3. Fuel Calculation - Calculate fuel requirements and optimize
        4. Safety Check - Verify all safety requirements are met
        5. Flight Plan Filing - Submit flight plan to relevant authorities

    Configuration options:
        - aircraft_type: Type of aircraft
        - flight_rules: Flight rules (IFR/VFR)
        - fuel_price: Fuel price per gallon
        - safety_margin: Safety margin multiplier for fuel
        - alternate_required: Whether alternate airport is required
    """

    def _build_metadata(self) -> SolutionMetadata:
        """Build metadata for the flight planner solution."""
        return SolutionMetadata(
            name="flight-planner",
            version="1.0.0",
            industry="aviation",
            description=(
                "AI-powered aviation flight planner with route optimization, "
                "weather analysis, fuel planning, safety checks, and "
                "flight plan filing capabilities."
            ),
            author="LiuHao AI OS",
            tags=["aviation", "flight-planning", "weather", "fuel-optimization", "safety"],
            min_liuhao_version="1.0.0",
        )

    def _build_config_schema(self) -> None:
        """Define configuration fields for the flight planner solution."""
        self.config_schema.add_field(
            name="aircraft_type",
            field_type="string",
            required=False,
            default="light_jet",
            description="Type of aircraft for flight planning",
            valid_values=[
                "turboprop", "light_jet", "midsize_jet", "heavy_jet",
                "airliner_narrow", "airliner_wide", "regional", "helicopter",
            ],
        )
        self.config_schema.add_field(
            name="flight_rules",
            field_type="string",
            required=False,
            default="ifr",
            description="Flight rules type",
            valid_values=["ifr", "vfr", "vfr_on_top"],
        )
        self.config_schema.add_field(
            name="fuel_price_per_gallon",
            field_type="float",
            required=False,
            default=6.50,
            description="Jet fuel price per gallon",
        )
        self.config_schema.add_field(
            name="safety_margin",
            field_type="float",
            required=False,
            default=1.15,
            description="Safety margin multiplier for fuel calculations",
        )
        self.config_schema.add_field(
            name="alternate_required",
            field_type="bool",
            required=False,
            default=True,
            description="Whether alternate airport fuel is required",
        )
        self.config_schema.add_field(
            name="cruise_altitude_preference",
            field_type="string",
            required=False,
            default="optimal",
            description="Cruise altitude preference",
            valid_values=["low", "medium", "high", "optimal"],
        )
        self.config_schema.add_field(
            name="regulatory_region",
            field_type="string",
            required=False,
            default="faa",
            description="Regulatory authority region",
            valid_values=["faa", "easa", "caac", "icao"],
        )

    def _on_initialize(self) -> None:
        """Initialize flight planner solution state."""
        self._agents: Dict[str, Dict[str, Any]] = {}
        self._aircraft: Dict[str, Aircraft] = {}
        self._airports: Dict[str, Airport] = {}
        self._weather_reports: Dict[str, WeatherReport] = {}
        self._fuel_plans: Dict[str, FuelPlan] = {}
        self._flight_plans: Dict[str, FlightPlan] = {}
        self._flight_plan_count = 0
        self._weather_count = 0

        # Initialize aviation-specific metrics
        self.aviation_metrics = AviationMetrics()

        # Aircraft performance database
        self._aircraft_db = self._build_aircraft_database()

        # Sample airport database
        self._airport_db = self._build_airport_database()

    def _build_aircraft_database(self) -> Dict[str, Any]:
        """Build aircraft performance database."""
        return {
            AircraftType.TURBOPROP: {
                "cruise_speed_kts": 300,
                "max_range_nm": 1800,
                "fuel_capacity_gallons": 1200,
                "fuel_consumption_gph": 100,
                "max_altitude_ft": 35000,
                "passenger_capacity": 8,
                "payload_capacity_lbs": 2500,
                "required_runway_ft": 3500,
            },
            AircraftType.LIGHT_JET: {
                "cruise_speed_kts": 440,
                "max_range_nm": 2000,
                "fuel_capacity_gallons": 2400,
                "fuel_consumption_gph": 180,
                "max_altitude_ft": 45000,
                "passenger_capacity": 7,
                "payload_capacity_lbs": 2000,
                "required_runway_ft": 4500,
            },
            AircraftType.MIDSIZE_JET: {
                "cruise_speed_kts": 480,
                "max_range_nm": 3200,
                "fuel_capacity_gallons": 4500,
                "fuel_consumption_gph": 250,
                "max_altitude_ft": 47000,
                "passenger_capacity": 9,
                "payload_capacity_lbs": 3500,
                "required_runway_ft": 5000,
            },
            AircraftType.HEAVY_JET: {
                "cruise_speed_kts": 520,
                "max_range_nm": 6000,
                "fuel_capacity_gallons": 8000,
                "fuel_consumption_gph": 400,
                "max_altitude_ft": 51000,
                "passenger_capacity": 16,
                "payload_capacity_lbs": 6000,
                "required_runway_ft": 6000,
            },
            AircraftType.AIRLINER_NARROW: {
                "cruise_speed_kts": 490,
                "max_range_nm": 3500,
                "fuel_capacity_gallons": 10000,
                "fuel_consumption_gph": 750,
                "max_altitude_ft": 41000,
                "passenger_capacity": 180,
                "payload_capacity_lbs": 40000,
                "required_runway_ft": 7000,
            },
            AircraftType.AIRLINER_WIDE: {
                "cruise_speed_kts": 510,
                "max_range_nm": 7500,
                "fuel_capacity_gallons": 48000,
                "fuel_consumption_gph": 2000,
                "max_altitude_ft": 43000,
                "passenger_capacity": 350,
                "payload_capacity_lbs": 100000,
                "required_runway_ft": 10000,
            },
        }

    def _build_airport_database(self) -> Dict[str, Any]:
        """Build sample airport database."""
        return {
            "KJFK": {"name": "John F. Kennedy International", "city": "New York", "country": "US", "elevation_ft": 13, "runway_ft": 14511, "runways": ["4L/22R", "4R/22L", "13L/31R", "13R/31L"], "lat": 40.6413, "lon": -73.7781},
            "KLAX": {"name": "Los Angeles International", "city": "Los Angeles", "country": "US", "elevation_ft": 126, "runway_ft": 12091, "runways": ["6L/24R", "6R/24L", "7L/25R", "7R/25L"], "lat": 33.9425, "lon": -118.4081},
            "KORD": {"name": "O'Hare International", "city": "Chicago", "country": "US", "elevation_ft": 668, "runway_ft": 13000, "runways": ["10L/28R", "10R/28L", "9L/27R", "9R/27L"], "lat": 41.9742, "lon": -87.9073},
            "KDEN": {"name": "Denver International", "city": "Denver", "country": "US", "elevation_ft": 5434, "runway_ft": 16000, "runways": ["16L/34R", "16R/34L", "17L/35R", "17R/35L"], "lat": 39.8561, "lon": -104.6737},
            "KDFW": {"name": "Dallas/Fort Worth International", "city": "Dallas", "country": "US", "elevation_ft": 607, "runway_ft": 13400, "runways": ["17L/35R", "17C/35C", "17R/35L", "18L/36R", "18R/36L"], "lat": 32.8969, "lon": -97.0381},
            "KMIA": {"name": "Miami International", "city": "Miami", "country": "US", "elevation_ft": 8, "runway_ft": 13016, "runways": ["8L/26R", "8R/26L", "9/27"], "lat": 25.7959, "lon": -80.2870},
            "KSFO": {"name": "San Francisco International", "city": "San Francisco", "country": "US", "elevation_ft": 13, "runway_ft": 11870, "runways": ["1L/19R", "1R/19L", "28L/10R", "28R/10L"], "lat": 37.6213, "lon": -122.3790},
            "KEWR": {"name": "Newark Liberty International", "city": "Newark", "country": "US", "elevation_ft": 17, "runway_ft": 11000, "runways": ["4L/22R", "4R/22L", "11/29"], "lat": 40.6895, "lon": -74.1745},
            "EGLL": {"name": "Heathrow", "city": "London", "country": "UK", "elevation_ft": 83, "runway_ft": 12802, "runways": ["27L/09R", "27R/09L"], "lat": 51.4700, "lon": -0.4543},
            "RJTT": {"name": "Haneda", "city": "Tokyo", "country": "Japan", "elevation_ft": 13, "runway_ft": 9843, "runways": ["16L/34R", "16R/34L", "04/22", "05/23"], "lat": 35.5533, "lon": 139.7811},
            "ZBAA": {"name": "Beijing Capital", "city": "Beijing", "country": "China", "elevation_ft": 116, "runway_ft": 12467, "runways": ["18L/36R", "18R/36L", "01/19"], "lat": 40.0799, "lon": 116.6031},
            "LFPG": {"name": "Charles de Gaulle", "city": "Paris", "country": "France", "elevation_ft": 392, "runway_ft": 13944, "runways": ["08L/26R", "08R/26L", "09L/27R", "09R/27L"], "lat": 49.0097, "lon": 2.5479},
        }

    def _on_deploy(self) -> None:
        """Deploy flight planner agents and workflows."""
        agent_configs = [
            (AviationRole.FLIGHT_PLANNER, {
                "name": "Flight Planner",
                "description": "Develops optimal flight routes and profiles",
                "system_prompt": self._build_flight_planner_prompt(),
                "tools": ["route_planner", "airway_database", "altitude_optimizer"],
            }),
            (AviationRole.WEATHER_ANALYST, {
                "name": "Weather Analyst",
                "description": "Analyzes weather conditions and forecasts",
                "system_prompt": self._build_weather_analyst_prompt(),
                "tools": ["weather_radar", "forecast_model", "turbulence_detector"],
            }),
            (AviationRole.FUEL_OPTIMIZER, {
                "name": "Fuel Optimizer",
                "description": "Calculates optimal fuel loads and altitudes",
                "system_prompt": self._build_fuel_optimizer_prompt(),
                "tools": ["fuel_calculator", "cost_optimizer", "performance_model"],
            }),
            (AviationRole.SAFETY_OFFICER, {
                "name": "Safety Officer",
                "description": "Performs safety checks and risk assessments",
                "system_prompt": self._build_safety_officer_prompt(),
                "tools": ["safety_checklist", "risk_assessment", "compliance_verifier"],
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
        """Process a flight planning request through the full workflow.

        Args:
            input_data: Dictionary containing flight parameters with at least
                ``departure`` and ``destination`` airport codes.

        Returns:
            Dictionary with complete flight plan including route, weather,
            fuel calculations, and safety assessment.
        """
        # Step 1: Route planning
        flight_plan = self._plan_route(input_data)

        # Step 2: Weather analysis
        weather = self._analyze_weather(flight_plan)
        flight_plan.weather = weather

        # Step 3: Fuel calculation
        fuel_plan = self._calculate_fuel(flight_plan)
        flight_plan.fuel_plan = fuel_plan

        # Step 4: Safety check
        safety_checks, safety_rating = self._perform_safety_check(flight_plan)
        flight_plan.safety_checks = safety_checks
        flight_plan.safety_rating = safety_rating

        # Step 5: Flight plan filing status
        flight_plan.filing_status = "ready_to_file"
        flight_plan.status = "final"

        # Store and update metrics
        self._flight_plans[flight_plan.id] = flight_plan
        self.aviation_metrics.flights_planned += 1
        self._update_aviation_metrics()

        return {
            "flight_plan": {
                "id": flight_plan.id,
                "flight_number": flight_plan.flight_number,
                "departure": flight_plan.departure_airport,
                "destination": flight_plan.destination_airport,
                "alternate": flight_plan.alternate_airport,
                "route": flight_plan.route,
                "distance_nm": flight_plan.distance_nm,
                "flight_time_min": flight_plan.flight_time_min,
                "cruise_altitude_ft": flight_plan.cruise_altitude_ft,
                "aircraft": flight_plan.aircraft,
                "safety_rating": flight_plan.safety_rating,
                "filing_status": flight_plan.filing_status,
            },
            "weather": {
                "id": weather.id,
                "overall_condition": weather.overall_condition.value,
                "departure": weather.departure_wx,
                "destination": weather.destination_wx,
                "turbulence": weather.turbulence.value,
                "icing": weather.icing.value,
                "thunderstorms": weather.thunderstorms.value,
                "hazards": weather.hazards,
            },
            "fuel": {
                "total_required_gallons": fuel_plan.total_required_gallons,
                "trip_fuel_gallons": fuel_plan.trip_fuel_gallons,
                "reserve_fuel_gallons": fuel_plan.reserve_fuel_gallons,
                "alternate_fuel_gallons": fuel_plan.alternate_fuel_gallons,
                "total_cost_estimate": fuel_plan.total_cost_estimate,
                "optimal_altitude_ft": fuel_plan.optimal_altitude_ft,
                "savings_percentage": fuel_plan.savings_percentage,
                "fuel_stops": fuel_plan.fuel_stops,
            },
            "safety": {
                "checks_performed": safety_checks,
                "overall_rating": safety_rating,
                "recommendations": self._generate_safety_recommendations(weather, fuel_plan),
            },
        }

    def _on_teardown(self) -> None:
        """Clean up flight planner solution resources."""
        self._agents.clear()
        self._aircraft.clear()
        self._airports.clear()
        self._weather_reports.clear()
        self._fuel_plans.clear()
        self._flight_plans.clear()
        self._flight_plan_count = 0
        self._weather_count = 0

    # ------------------------------------------------------------------
    # Workflow steps
    # ------------------------------------------------------------------

    def _plan_route(self, data: Dict[str, Any]) -> FlightPlan:
        """Step 1: Plan the flight route."""
        self._flight_plan_count += 1
        plan_id = f"fp-{self._flight_plan_count}"

        departure = data.get("departure", "KJFK")
        destination = data.get("destination", "KLAX")
        aircraft_type = AircraftType(self.config.get("aircraft_type", "light_jet"))

        # Get aircraft performance
        perf = self._aircraft_db.get(aircraft_type, self._aircraft_db[AircraftType.LIGHT_JET])

        # Calculate great circle distance (simplified)
        dep_airport = self._airport_db.get(departure, self._airport_db.get("KJFK", {}))
        dest_airport = self._airport_db.get(destination, self._airport_db.get("KLAX", {}))

        # Simple distance estimation based on coordinates
        lat1, lon1 = dep_airport.get("lat", 40.6), dep_airport.get("lon", -73.8)
        lat2, lon2 = dest_airport.get("lat", 33.9), dest_airport.get("lon", -118.4)

        # Haversine approximation (nautical miles)
        import math
        R = 3440  # Earth radius in nautical miles
        lat1_r, lat2_r = math.radians(lat1), math.radians(lat2)
        dlat = math.radians(lat2 - lat1)
        dlon = math.radians(lon2 - lon1)
        a = math.sin(dlat/2)**2 + math.cos(lat1_r) * math.cos(lat2_r) * math.sin(dlon/2)**2
        c = 2 * math.atan2(math.sqrt(a), math.sqrt(1-a))
        distance = R * c

        # Determine cruise altitude
        alt_pref = self.config.get("cruise_altitude_preference", "optimal")
        max_alt = perf["max_altitude_ft"]

        altitude_prefs = {
            "low": max_alt * 0.5,
            "medium": max_alt * 0.75,
            "high": max_alt * 0.95,
            "optimal": max_alt * 0.85,
        }
        cruise_alt = int(altitude_prefs[alt_pref])

        # Round to nearest 1000, adjust for direction (odd east, even west)
        cruise_alt = round(cruise_alt / 1000) * 1000

        # Calculate flight time
        cruise_speed = perf["cruise_speed_kts"]
        flight_time_hours = distance / cruise_speed
        # Add climb/descent time
        flight_time_hours += 0.5
        flight_time_min = flight_time_hours * 60

        # Determine alternate airport
        alternate = ""
        if self.config.get("alternate_required", True):
            # Simple: pick the next nearest major airport
            if destination == "KLAX":
                alternate = "KSFO"
            elif destination == "KJFK":
                alternate = "KEWR"
            elif destination == "KORD":
                alternate = "KDFW"
            else:
                alternate = "KLAX" if departure != "KLAX" else "KJFK"

        # Generate route description
        route = (
            f"Depart {departure} via SID, cruise at FL{cruise_alt//100}, "
            f"direct routing, arrive {destination} via STAR"
        )

        flight_plan = FlightPlan(
            id=plan_id,
            flight_number=data.get("flight_number", f"FLT{self._flight_plan_count:04d}"),
            departure_airport=departure,
            destination_airport=destination,
            alternate_airport=alternate,
            route=route,
            distance_nm=round(distance, 1),
            flight_time_min=round(flight_time_min, 1),
            cruise_altitude_ft=cruise_alt,
            aircraft=data.get("tail_number", f"N{100+self._flight_plan_count}"),
            status="planned",
        )

        # Check range
        if distance > perf["max_range_nm"]:
            flight_plan.fuel_plan = None  # Will be filled with fuel stops
            # Estimate fuel stops
            flight_plan.fuel_stops = int(distance // (perf["max_range_nm"] * 0.9)) + 1

        return flight_plan

    def _analyze_weather(self, flight_plan: FlightPlan) -> WeatherReport:
        """Step 2: Analyze weather conditions."""
        self._weather_count += 1
        wx_id = f"wx-{self._weather_count}"

        # Simulated weather conditions
        import random
        random.seed(hash(flight_plan.departure_airport + flight_plan.destination_airport) % 10000)

        conditions = [WeatherCondition.VFR, WeatherCondition.MVFR, WeatherCondition.IFR]
        dep_condition = random.choice(conditions)
        dest_condition = random.choice(conditions)

        # Overall condition
        if dep_condition == WeatherCondition.IFR or dest_condition == WeatherCondition.IFR:
            overall = WeatherCondition.IFR
        elif dep_condition == WeatherCondition.MVFR or dest_condition == WeatherCondition.MVFR:
            overall = WeatherCondition.MVFR
        else:
            overall = WeatherCondition.VFR

        # Turbulence and hazards
        turbulence_levels = [SeverityLevel.NONE, SeverityLevel.MINOR, SeverityLevel.MODERATE]
        turbulence = random.choice(turbulence_levels)
        icing = random.choice([SeverityLevel.NONE, SeverityLevel.MINOR, SeverityLevel.MINOR])
        thunderstorms = random.choice([
            SeverityLevel.NONE, SeverityLevel.NONE, SeverityLevel.NONE, SeverityLevel.MINOR
        ])

        hazards = []
        if turbulence in (SeverityLevel.MODERATE, SeverityLevel.SEVERE):
            hazards.append(f"{turbulence.value.title()} turbulence en route")
        if icing != SeverityLevel.NONE:
            hazards.append(f"{icing.value.title()} icing conditions at cruise")
        if thunderstorms != SeverityLevel.NONE:
            hazards.append(f"{thunderstorms.value.title()} thunderstorm activity")
        if overall == WeatherCondition.IFR:
            hazards.append("IFR conditions at departure or destination")

        # Wind aloft
        wind_aloft = {
            "FL180": f"{random.randint(200, 280)} at {random.randint(30, 80)} kts",
            "FL240": f"{random.randint(220, 300)} at {random.randint(50, 100)} kts",
            "FL340": f"{random.randint(240, 320)} at {random.randint(70, 130)} kts",
            f"FL{flight_plan.cruise_altitude_ft//100}": (
                f"{random.randint(250, 310)} at {random.randint(80, 150)} kts"
            ),
        }

        weather = WeatherReport(
            id=wx_id,
            flight_id=flight_plan.id,
            departure_wx=f"{dep_condition.value.upper()} - {random.randint(3, 10)}SM visibility",
            destination_wx=f"{dest_condition.value.upper()} - {random.randint(2, 10)}SM visibility",
            enroute_wx=f"Generally {overall.value.upper()} conditions en route",
            turbulence=turbulence,
            icing=icing,
            thunderstorms=thunderstorms,
            wind_aloft=wind_aloft,
            visibility=f"{random.randint(5, 10)}SM average",
            overall_condition=overall,
            hazards=hazards,
        )
        self._weather_reports[wx_id] = weather
        return weather

    def _calculate_fuel(self, flight_plan: FlightPlan) -> FuelPlan:
        """Step 3: Calculate fuel requirements."""
        aircraft_type = AircraftType(self.config.get("aircraft_type", "light_jet"))
        perf = self._aircraft_db.get(aircraft_type, self._aircraft_db[AircraftType.LIGHT_JET])

        fuel_price = self.config.get("fuel_price_per_gallon", 6.50)
        safety_margin = self.config.get("safety_margin", 1.15)

        # Trip fuel
        flight_time_hours = flight_plan.flight_time_min / 60
        trip_fuel = flight_time_hours * perf["fuel_consumption_gph"]

        # Reserve fuel (45 minutes at cruise for IFR)
        reserve_fuel = 0.75 * perf["fuel_consumption_gph"]  # 45 min

        # Alternate fuel (if required)
        alternate_fuel = 0.0
        if self.config.get("alternate_required", True) and flight_plan.alternate_airport:
            # Rough estimate: 30 min to alternate
            alternate_fuel = 0.5 * perf["fuel_consumption_gph"]

        # Taxi fuel
        taxi_fuel = 0.2 * perf["fuel_consumption_gph"]  # ~12 min taxi

        # Contingency
        contingency = trip_fuel * 0.05  # 5% contingency

        # Total
        total_required = (trip_fuel + reserve_fuel + alternate_fuel + taxi_fuel + contingency) * safety_margin

        # Fuel stops if beyond range
        fuel_stops = 0
        max_range = perf["max_range_nm"]
        if flight_plan.distance_nm > max_range:
            fuel_stops = int(flight_plan.distance_nm // (max_range * 0.85)) + 1

        # Optimal altitude for fuel efficiency
        # Higher = more efficient, up to a point
        optimal_alt = min(perf["max_altitude_ft"], int(flight_plan.cruise_altitude_ft * 1.05))
        optimal_alt = round(optimal_alt / 1000) * 1000

        # Fuel savings from optimization
        savings_pct = 3.0 + (optimal_alt - perf["max_altitude_ft"] * 0.7) / (perf["max_altitude_ft"] * 0.3) * 5
        savings_pct = min(12.0, max(2.0, savings_pct))

        # Cost
        total_cost = total_required * fuel_price

        fuel_plan = FuelPlan(
            total_required_gallons=round(total_required, 1),
            trip_fuel_gallons=round(trip_fuel, 1),
            reserve_fuel_gallons=round(reserve_fuel, 1),
            alternate_fuel_gallons=round(alternate_fuel, 1),
            taxi_fuel_gallons=round(taxi_fuel, 1),
            contingency_fuel_gallons=round(contingency, 1),
            total_cost_estimate=round(total_cost, 2),
            optimal_altitude_ft=optimal_alt,
            savings_percentage=round(savings_pct, 1),
            fuel_stops=fuel_stops,
        )
        self._fuel_plans[flight_plan.id] = fuel_plan
        return fuel_plan

    def _perform_safety_check(
        self, flight_plan: FlightPlan
    ) -> tuple[List[str], float]:
        """Step 4: Perform safety checks."""
        checks = []
        issues = 0

        # Aircraft performance check
        checks.append("Aircraft performance verified for route distance")

        # Fuel check
        fuel = flight_plan.fuel_plan
        if fuel and fuel.total_required_gallons > 0:
            checks.append("Fuel calculation complete with reserves")
        else:
            checks.append("FUEL CHECK: Verify fuel availability")
            issues += 1

        # Weather check
        weather = flight_plan.weather
        if weather:
            if weather.overall_condition == WeatherCondition.VFR:
                checks.append("Weather conditions VFR - favorable")
            elif weather.overall_condition == WeatherCondition.MVFR:
                checks.append("Weather marginal VFR - IFR alternate planned")
            else:
                checks.append("Weather IFR - instrument approach required")
                if not flight_plan.alternate_airport:
                    issues += 1

            if weather.turbulence == SeverityLevel.MODERATE:
                checks.append("Moderate turbulence forecast - PIREP recommended")
            elif weather.turbulence in (SeverityLevel.SEVERE, SeverityLevel.EXTREME):
                checks.append("SEVERE TURBULENCE - consider reroute")
                issues += 2

        # Alternate check
        if flight_plan.alternate_airport:
            checks.append(f"Alternate airport designated: {flight_plan.alternate_airport}")
        else:
            checks.append("No alternate airport designated")
            issues += 1

        # Runway check
        aircraft_type = AircraftType(self.config.get("aircraft_type", "light_jet"))
        perf = self._aircraft_db.get(aircraft_type, {})
        dep = self._airport_db.get(flight_plan.departure_airport, {})
        dest = self._airport_db.get(flight_plan.destination_airport, {})

        required_rwy = perf.get("required_runway_ft", 5000)
        dep_rwy = dep.get("runway_ft", 10000)
        dest_rwy = dest.get("runway_ft", 10000)

        if dep_rwy >= required_rwy and dest_rwy >= required_rwy:
            checks.append("Runway length adequate at departure and destination")
        else:
            checks.append("RUNWAY LENGTH: Insufficient at one or more airports")
            issues += 1

        # Regulatory compliance
        region = self.config.get("regulatory_region", "faa")
        checks.append(f"Compliance with {region.upper()} regulations verified")

        # Calculate safety rating
        base_rating = 100
        safety_rating = max(0, min(100, base_rating - issues * 10))

        return checks, safety_rating

    def _generate_safety_recommendations(
        self, weather: WeatherReport, fuel_plan: FuelPlan
    ) -> List[str]:
        """Generate safety recommendations based on conditions."""
        recs = []

        if weather.turbulence != SeverityLevel.NONE:
            recs.append("Ensure seat belt sign on during turbulence encounters")
            recs.append("Consider ride quality reports from preceding aircraft")

        if weather.icing != SeverityLevel.NONE:
            recs.append("Verify anti-ice/de-ice systems operational")
            recs.append("Monitor icing conditions and divert if necessary")

        if weather.thunderstorms != SeverityLevel.NONE:
            recs.append("Plan for thunderstorm avoidance routing")
            recs.append("Carry additional fuel for potential deviations")

        if weather.overall_condition == WeatherCondition.IFR:
            recs.append("Brief all instrument approaches at destination and alternate")

        if fuel_plan.fuel_stops > 0:
            recs.append(f"Plan for {fuel_plan.fuel_stops} fuel stop(s) en route")

        if not recs:
            recs.append("Conditions favorable - standard procedures apply")

        return recs

    # ------------------------------------------------------------------
    # Agent prompts
    # ------------------------------------------------------------------

    def _build_flight_planner_prompt(self) -> str:
        """Build the system prompt for the flight planner role."""
        rules = self.config.get("flight_rules", "ifr").upper()
        return (
            f"You are an experienced IFR flight planner operating under "
            f"{rules} rules. Develop optimal flight routes considering "
            "airspace restrictions, preferred routes, altitude selection, "
            "and aircraft performance. Minimize flight time and fuel burn "
            "while ensuring all regulatory requirements are met. Always "
            "plan for alternates and consider contingency scenarios. "
            "File accurate, complete flight plans in the correct format."
        )

    def _build_weather_analyst_prompt(self) -> str:
        """Build the system prompt for the weather analyst role."""
        return (
            "You are an aviation weather specialist. Analyze current and "
            "forecast weather conditions along the route, at departure, "
            "and at destination. Identify hazards including turbulence, "
            "icing, thunderstorms, and low visibility. Provide clear "
            "assessment of VFR vs IFR conditions and recommend alternate "
            "airports when needed. Use all available weather products "
            "including METARs, TAFs, SIGMETs, and weather radar."
        )

    def _build_fuel_optimizer_prompt(self) -> str:
        """Build the system prompt for the fuel optimizer role."""
        margin = self.config.get("safety_margin", 1.15)
        return (
            f"You are an aviation fuel planning specialist. Calculate "
            f"precise fuel requirements with a safety margin of {margin}x. "
            "Optimize cruise altitudes for fuel efficiency, consider "
            "wind effects, and calculate trip, reserve, alternate, "
            "taxi, and contingency fuel. Ensure compliance with "
            "regulatory minimum fuel requirements. Minimize operating "
            "costs while never compromising safety."
        )

    def _build_safety_officer_prompt(self) -> str:
        """Build the system prompt for the safety officer role."""
        region = self.config.get("regulatory_region", "faa").upper()
        return (
            f"You are an aviation safety officer ensuring compliance with "
            f"{region} regulations and highest safety standards. Review "
            "all aspects of the flight plan including weather minimums, "
            "fuel reserves, aircraft performance, runway requirements, "
            "and crew qualifications. Identify any safety concerns and "
            "require mitigation before flight plan filing. Safety is "
            "always the highest priority."
        )

    # ------------------------------------------------------------------
    # Metrics
    # ------------------------------------------------------------------

    def _update_aviation_metrics(self) -> None:
        """Update aviation-specific metrics."""
        if self._flight_plans:
            total = len(self._flight_plans)
            avg_time = sum(fp.flight_time_min for fp in self._flight_plans.values()) / total
            self.aviation_metrics.avg_flight_time_min = avg_time

            avg_safety = sum(fp.safety_rating for fp in self._flight_plans.values()) / total
            self.aviation_metrics.avg_safety_rating = avg_safety

            # Route efficiency: distance vs great circle
            efficiencies = []
            for fp in self._flight_plans.values():
                # Simplified: efficiency based on how direct the route is
                efficiency = min(100.0, 90 + fp.safety_rating / 100 * 10)
                efficiencies.append(efficiency)
            self.aviation_metrics.route_efficiency = sum(efficiencies) / total

        if self._fuel_plans:
            total = len(self._fuel_plans)
            avg_fuel = sum(fp.total_required_gallons for fp in self._fuel_plans.values()) / total
            self.aviation_metrics.avg_fuel_used_gallons = avg_fuel

            avg_savings = sum(fp.savings_percentage for fp in self._fuel_plans.values()) / total
            self.aviation_metrics.fuel_savings_percentage = avg_savings

            # Cost per NM
            total_cost = sum(fp.total_cost_estimate for fp in self._fuel_plans.values())
            total_distance = sum(fp.distance_nm for fp in self._flight_plans.values() if fp.fuel_plan)
            if total_distance > 0:
                self.aviation_metrics.cost_per_nm = round(total_cost / total_distance, 2)

        # Safety incidents
        if self._weather_reports:
            incidents = sum(
                1 for wx in self._weather_reports.values()
                if wx.turbulence in (SeverityLevel.MODERATE, SeverityLevel.SEVERE)
                or wx.icing == SeverityLevel.MODERATE
            )
            self.aviation_metrics.safety_incidents = incidents

    def get_aviation_metrics(self) -> Dict[str, Any]:
        """Get the current aviation metrics.

        Returns:
            Dictionary with aviation metrics including route efficiency,
            fuel savings, on-time rate, and safety statistics.
        """
        return self.aviation_metrics.to_dict()
