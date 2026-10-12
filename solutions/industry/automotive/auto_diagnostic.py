"""
Automotive Diagnostic AI Solution Template.

A comprehensive AI-powered automotive diagnostic solution for
repair shops, dealerships, and fleet management. Includes
multi-agent roles for diagnostic analysis, parts identification,
service advising, and quality inspection with accuracy tracking.

Typical workflow:
    symptom intake -> diagnostic analysis -> parts identification
    -> service plan -> quality check
"""

from typing import Optional, Dict, Any, List
from enum import Enum
from dataclasses import dataclass, field

from solutions.templates.base_solution import (
    BaseSolution,
    SolutionMetadata,
    SolutionMetrics,
)


class VehicleType(str, Enum):
    """Types of vehicles."""
    SEDAN = "sedan"
    SUV = "suv"
    TRUCK = "truck"
    VAN = "van"
    MOTORCYCLE = "motorcycle"
    ELECTRIC = "electric"
    HYBRID = "hybrid"
    COMMERCIAL = "commercial"


class DiagnosticConfidence(str, Enum):
    """Confidence levels for diagnostic assessments."""
    LOW = "low"
    MODERATE = "moderate"
    HIGH = "high"
    VERY_HIGH = "very_high"


class RepairUrgency(str, Enum):
    """Urgency levels for recommended repairs."""
    IMMEDIATE = "immediate"
    SOON = "soon"
    SCHEDULED = "scheduled"
    OPTIONAL = "optional"
    MONITOR = "monitor"


class ServiceStatus(str, Enum):
    """Status of a service job through the workflow."""
    DIAGNOSING = "diagnosing"
    PARTS_ORDERED = "parts_ordered"
    IN_PROGRESS = "in_progress"
    QUALITY_CHECK = "quality_check"
    READY = "ready"
    DELIVERED = "delivered"


class AutomotiveRole(str, Enum):
    """Agent roles in the automotive diagnostic solution."""
    DIAGNOSTIC_TECH = "diagnostic_tech"
    PARTS_SPECIALIST = "parts_specialist"
    SERVICE_ADVISOR = "service_advisor"
    QUALITY_INSPECTOR = "quality_inspector"


@dataclass
class VehicleInfo:
    """Represents vehicle information.

    Attributes:
        vin: Vehicle Identification Number.
        make: Vehicle make/manufacturer.
        model: Vehicle model.
        year: Model year.
        vehicle_type: Type/class of vehicle.
        mileage: Current odometer reading.
        engine_type: Engine type and size.
        transmission: Transmission type.
        fuel_type: Fuel type (gasoline, diesel, electric, etc.).
        maintenance_history: List of past services.
        current_codes: Active diagnostic trouble codes.
    """
    vin: str
    make: str = ""
    model: str = ""
    year: int = 2020
    vehicle_type: VehicleType = VehicleType.SEDAN
    mileage: int = 0
    engine_type: str = ""
    transmission: str = "automatic"
    fuel_type: str = "gasoline"
    maintenance_history: List[str] = field(default_factory=list)
    current_codes: List[str] = field(default_factory=list)


@dataclass
class SymptomReport:
    """Represents a reported vehicle symptom.

    Attributes:
        id: Unique symptom report identifier.
        vin: Associated vehicle VIN.
        description: Description of the symptom.
        location: Where the symptom is noticed.
        frequency: How often the symptom occurs.
        severity: Severity rating (1-10).
        when_noticed: When the symptom started.
        conditions: Conditions that trigger the symptom.
        related_codes: Associated diagnostic trouble codes.
    """
    id: str
    vin: str
    description: str = ""
    location: str = ""
    frequency: str = "intermittent"
    severity: int = 5
    when_noticed: str = ""
    conditions: List[str] = field(default_factory=list)
    related_codes: List[str] = field(default_factory=list)


@dataclass
class DiagnosticResult:
    """Represents a diagnostic assessment result.

    Attributes:
        id: Unique diagnostic result identifier.
        vin: Associated vehicle VIN.
        suspected_issue: Primary suspected problem.
        confidence: Confidence level of the diagnosis.
        probability: Estimated probability (0-1).
        supporting_evidence: Evidence supporting the diagnosis.
        possible_causes: Ranked list of possible causes.
        recommended_tests: Tests to confirm the diagnosis.
        diagnosis_time_minutes: Time taken for diagnosis.
    """
    id: str
    vin: str
    suspected_issue: str = ""
    confidence: DiagnosticConfidence = DiagnosticConfidence.MODERATE
    probability: float = 0.0
    supporting_evidence: List[str] = field(default_factory=list)
    possible_causes: List[Dict[str, Any]] = field(default_factory=list)
    recommended_tests: List[str] = field(default_factory=list)
    diagnosis_time_minutes: float = 0.0


@dataclass
class PartReplacement:
    """Represents a parts recommendation.

    Attributes:
        part_number: OEM or aftermarket part number.
        part_name: Name/description of the part.
        quantity: Quantity needed.
        unit_price: Price per unit.
        total_price: Total parts cost.
        availability: Availability status.
        supplier: Parts supplier.
        lead_time_days: Estimated delivery time.
    """
    part_number: str = ""
    part_name: str = ""
    quantity: int = 1
    unit_price: float = 0.0
    total_price: float = 0.0
    availability: str = "in_stock"
    supplier: str = ""
    lead_time_days: int = 0


@dataclass
class ServicePlan:
    """Represents a complete service plan.

    Attributes:
        id: Unique service plan identifier.
        vin: Associated vehicle VIN.
        diagnostic_result: Associated diagnostic result.
        parts_required: List of parts needed.
        labor_hours: Estimated labor hours.
        labor_cost: Estimated labor cost.
        parts_cost: Total parts cost.
        total_cost: Total estimated cost.
        urgency: Repair urgency level.
        estimated_completion_days: Estimated completion time.
        status: Current service status.
        first_time_fix: Whether fix is expected on first visit.
    """
    id: str
    vin: str
    diagnostic_result: Optional[DiagnosticResult] = None
    parts_required: List[PartReplacement] = field(default_factory=list)
    labor_hours: float = 0.0
    labor_cost: float = 0.0
    parts_cost: float = 0.0
    total_cost: float = 0.0
    urgency: RepairUrgency = RepairUrgency.SCHEDULED
    estimated_completion_days: int = 1
    status: ServiceStatus = ServiceStatus.DIAGNOSING
    first_time_fix: bool = True


@dataclass
class AutomotiveMetrics(SolutionMetrics):
    """Extended metrics for the automotive diagnostic solution.

    Attributes:
        diagnostic_accuracy: Accuracy rate of diagnoses.
        avg_repair_time_hours: Average repair completion time.
        first_time_fix_rate: Rate of issues fixed on first visit.
        vehicles_serviced: Total vehicles serviced.
        diagnoses_generated: Total diagnoses performed.
        avg_diagnostic_confidence: Average confidence score.
        parts_accuracy: Accuracy of parts identification.
        customer_approval_rate: Rate of service plan approvals.
        avg_service_cost: Average service ticket cost.
    """
    diagnostic_accuracy: float = 0.0
    avg_repair_time_hours: float = 0.0
    first_time_fix_rate: float = 0.0
    vehicles_serviced: int = 0
    diagnoses_generated: int = 0
    avg_diagnostic_confidence: float = 0.0
    parts_accuracy: float = 0.0
    customer_approval_rate: float = 0.0
    avg_service_cost: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        """Convert automotive metrics to a dictionary."""
        base = super().to_dict()
        base.update({
            "diagnostic_accuracy": self.diagnostic_accuracy,
            "avg_repair_time_hours": self.avg_repair_time_hours,
            "first_time_fix_rate": self.first_time_fix_rate,
            "vehicles_serviced": self.vehicles_serviced,
            "diagnoses_generated": self.diagnoses_generated,
            "avg_diagnostic_confidence": self.avg_diagnostic_confidence,
            "parts_accuracy": self.parts_accuracy,
            "customer_approval_rate": self.customer_approval_rate,
            "avg_service_cost": self.avg_service_cost,
        })
        return base


class AutoDiagnosticSolution(BaseSolution):
    """Automotive Diagnostic AI solution template.

    Provides a complete, pre-configured automotive diagnostic solution
    with multi-agent collaboration for symptom intake, diagnostic
    analysis, parts identification, service planning, and quality
    assurance.

    Pre-configured agent roles:
        - Diagnostic Tech: Performs vehicle diagnostics and troubleshooting
        - Parts Specialist: Identifies required parts and checks availability
        - Service Advisor: Communicates with customers and manages service
        - Quality Inspector: Verifies repair quality before delivery

    Workflow:
        1. Symptom Intake - Gather vehicle symptoms and diagnostic codes
        2. Diagnostic Analysis - Analyze symptoms to identify root cause
        3. Parts Identification - Identify required parts and availability
        4. Service Plan - Create comprehensive service estimate
        5. Quality Check - Verify repair completion and quality

    Configuration options:
        - shop_type: Type of repair facility
        - labor_rate: Hourly labor rate
        - diagnostic_fee: Flat diagnostic fee
        - warranty_included: Whether warranty is included
        - quality_check_required: Whether quality inspection is mandatory
    """

    def _build_metadata(self) -> SolutionMetadata:
        """Build metadata for the automotive diagnostic solution."""
        return SolutionMetadata(
            name="auto-diagnostic",
            version="1.0.0",
            industry="automotive",
            description=(
                "AI-powered automotive diagnostic solution with intelligent "
                "troubleshooting, parts identification, service planning, "
                "and quality assurance for repair facilities."
            ),
            author="LiuHao AI OS",
            tags=["automotive", "diagnostics", "repair", "parts", "quality-assurance"],
            min_liuhao_version="1.0.0",
        )

    def _build_config_schema(self) -> None:
        """Define configuration fields for the automotive diagnostic solution."""
        self.config_schema.add_field(
            name="shop_type",
            field_type="string",
            required=False,
            default="independent",
            description="Type of repair facility",
            valid_values=["dealership", "independent", "specialty", "fleet", "quick_lube"],
        )
        self.config_schema.add_field(
            name="labor_rate",
            field_type="float",
            required=False,
            default=120.0,
            description="Hourly labor rate in base currency",
        )
        self.config_schema.add_field(
            name="diagnostic_fee",
            field_type="float",
            required=False,
            default=120.0,
            description="Flat diagnostic fee",
        )
        self.config_schema.add_field(
            name="warranty_included",
            field_type="bool",
            required=False,
            default=True,
            description="Whether parts and labor warranty is included",
        )
        self.config_schema.add_field(
            name="quality_check_required",
            field_type="bool",
            required=False,
            default=True,
            description="Whether quality inspection is required before delivery",
        )
        self.config_schema.add_field(
            name="supported_vehicle_types",
            field_type="list",
            required=False,
            default=["sedan", "suv", "truck", "hybrid", "electric"],
            description="List of supported vehicle types",
        )
        self.config_schema.add_field(
            name="parts_markup",
            field_type="float",
            required=False,
            default=0.25,
            description="Parts markup percentage (as decimal)",
        )

    def _on_initialize(self) -> None:
        """Initialize automotive diagnostic solution state."""
        self._agents: Dict[str, Dict[str, Any]] = {}
        self._vehicles: Dict[str, VehicleInfo] = {}
        self._symptoms: Dict[str, SymptomReport] = {}
        self._diagnostics: Dict[str, DiagnosticResult] = {}
        self._service_plans: Dict[str, ServicePlan] = {}
        self._vehicle_count = 0
        self._symptom_count = 0
        self._diagnostic_count = 0
        self._service_plan_count = 0

        # Initialize automotive-specific metrics
        self.automotive_metrics = AutomotiveMetrics()

        # Diagnostic knowledge base (common symptom patterns)
        self._diagnostic_patterns = self._build_diagnostic_patterns()

    def _build_diagnostic_patterns(self) -> Dict[str, Any]:
        """Build common diagnostic patterns (simplified knowledge base)."""
        return {
            "engine_misfire": {
                "symptoms": ["rough idle", "loss of power", "check engine light", "shaking"],
                "codes": ["P0300", "P0301", "P0302", "P0303", "P0304"],
                "causes": [
                    {"cause": "Spark plug failure", "probability": 0.35},
                    {"cause": "Ignition coil failure", "probability": 0.30},
                    {"cause": "Fuel injector clog", "probability": 0.20},
                    {"cause": "Vacuum leak", "probability": 0.10},
                    {"cause": "Compression issue", "probability": 0.05},
                ],
                "parts": ["spark plugs", "ignition coil", "fuel injector"],
                "labor_hours": 2.0,
                "urgency": "soon",
            },
            "brake_wear": {
                "symptoms": ["squeaking brakes", "grinding noise", "brake pedal vibration", "reduced braking"],
                "codes": [],
                "causes": [
                    {"cause": "Brake pad wear", "probability": 0.60},
                    {"cause": "Rotor wear/warping", "probability": 0.25},
                    {"cause": "Caliper issue", "probability": 0.10},
                    {"cause": "Brake fluid contamination", "probability": 0.05},
                ],
                "parts": ["brake pads", "rotors", "brake calipers"],
                "labor_hours": 2.5,
                "urgency": "soon",
            },
            "battery_issue": {
                "symptoms": ["hard starting", "dead battery", "dimming lights", "electrical issues"],
                "codes": ["P0562", "P0563"],
                "causes": [
                    {"cause": "Battery age/failure", "probability": 0.50},
                    {"cause": "Alternator failure", "probability": 0.30},
                    {"cause": "Parasitic drain", "probability": 0.15},
                    {"cause": "Loose/corroded connections", "probability": 0.05},
                ],
                "parts": ["battery", "alternator"],
                "labor_hours": 1.0,
                "urgency": "immediate",
            },
            "oil_leak": {
                "symptoms": ["oil spots", "low oil level", "burning oil smell", "smoke from engine"],
                "codes": [],
                "causes": [
                    {"cause": "Valve cover gasket leak", "probability": 0.35},
                    {"cause": "Oil pan gasket leak", "probability": 0.25},
                    {"cause": "Oil seal failure", "probability": 0.20},
                    {"cause": "Oil filter leak", "probability": 0.15},
                    {"cause": "Cracked component", "probability": 0.05},
                ],
                "parts": ["gasket set", "oil seal", "oil filter"],
                "labor_hours": 3.0,
                "urgency": "scheduled",
            },
            "transmission_issue": {
                "symptoms": ["slipping gears", "rough shifting", "transmission fluid leak", "delayed engagement"],
                "codes": ["P0700", "P0720", "P0730"],
                "causes": [
                    {"cause": "Low transmission fluid", "probability": 0.30},
                    {"cause": "Solenoid failure", "probability": 0.25},
                    {"cause": "Torque converter issue", "probability": 0.20},
                    {"cause": "Clutch wear", "probability": 0.15},
                    {"cause": "Internal transmission damage", "probability": 0.10},
                ],
                "parts": ["transmission fluid", "solenoid", "torque converter"],
                "labor_hours": 5.0,
                "urgency": "soon",
            },
            "suspension_issue": {
                "symptoms": ["bumpy ride", "pulling to side", "uneven tire wear", "clunking noise"],
                "codes": [],
                "causes": [
                    {"cause": "Shock/strut wear", "probability": 0.35},
                    {"cause": "Ball joint wear", "probability": 0.25},
                    {"cause": "Tie rod end wear", "probability": 0.20},
                    {"cause": "Bushing wear", "probability": 0.15},
                    {"cause": "Spring failure", "probability": 0.05},
                ],
                "parts": ["shocks/struts", "ball joints", "tie rod ends"],
                "labor_hours": 3.5,
                "urgency": "soon",
            },
            "ac_not_cooling": {
                "symptoms": ["warm air", "ac not cold", "weak airflow", "strange noise from ac"],
                "codes": [],
                "causes": [
                    {"cause": "Refrigerant leak", "probability": 0.40},
                    {"cause": "Compressor failure", "probability": 0.25},
                    {"cause": "Condenser issue", "probability": 0.15},
                    {"cause": "Evaporator issue", "probability": 0.10},
                    {"cause": "Electrical issue", "probability": 0.10},
                ],
                "parts": ["refrigerant", "ac compressor", "condenser"],
                "labor_hours": 2.0,
                "urgency": "scheduled",
            },
        }

    def _on_deploy(self) -> None:
        """Deploy automotive diagnostic agents and workflows."""
        agent_configs = [
            (AutomotiveRole.DIAGNOSTIC_TECH, {
                "name": "Diagnostic Technician",
                "description": "Performs vehicle diagnostics and troubleshooting",
                "system_prompt": self._build_diagnostic_tech_prompt(),
                "tools": ["obd_reader", "diagnostic_database", "troubleshooting_guide"],
            }),
            (AutomotiveRole.PARTS_SPECIALIST, {
                "name": "Parts Specialist",
                "description": "Identifies required parts and checks availability",
                "system_prompt": self._build_parts_specialist_prompt(),
                "tools": ["parts_catalog", "inventory_check", "supplier_network"],
            }),
            (AutomotiveRole.SERVICE_ADVISOR, {
                "name": "Service Advisor",
                "description": "Communicates with customers and manages service",
                "system_prompt": self._build_service_advisor_prompt(),
                "tools": ["service_writer", "appointment_scheduler", "customer_communication"],
            }),
            (AutomotiveRole.QUALITY_INSPECTOR, {
                "name": "Quality Inspector",
                "description": "Verifies repair quality before vehicle delivery",
                "system_prompt": self._build_quality_inspector_prompt(),
                "tools": ["inspection_checklist", "test_drive", "quality_verification"],
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
        """Process a vehicle diagnostic request through the full workflow.

        Args:
            input_data: Dictionary containing vehicle data with at least
                ``vin`` and symptom descriptions.

        Returns:
            Dictionary with diagnostic results, service plan, and
            quality check information.
        """
        # Step 1: Symptom intake
        vehicle, symptoms = self._intake_symptoms(input_data)

        # Step 2: Diagnostic analysis
        diagnosis = self._perform_diagnosis(vehicle, symptoms)

        # Step 3: Parts identification
        parts = self._identify_parts(diagnosis)

        # Step 4: Service plan
        service_plan = self._create_service_plan(vehicle, diagnosis, parts)

        # Step 5: Quality check plan
        quality_plan = self._create_quality_check_plan(diagnosis)

        # Update metrics
        self.automotive_metrics.vehicles_serviced += 1
        self.automotive_metrics.diagnoses_generated += 1
        self._update_automotive_metrics()

        return {
            "vehicle": {
                "vin": vehicle.vin,
                "make": vehicle.make,
                "model": vehicle.model,
                "year": vehicle.year,
                "mileage": vehicle.mileage,
                "vehicle_type": vehicle.vehicle_type.value,
            },
            "symptoms": [
                {"description": s.description, "severity": s.severity, "frequency": s.frequency}
                for s in symptoms
            ],
            "diagnosis": {
                "id": diagnosis.id,
                "suspected_issue": diagnosis.suspected_issue,
                "confidence": diagnosis.confidence.value,
                "probability": diagnosis.probability,
                "possible_causes": diagnosis.possible_causes,
                "recommended_tests": diagnosis.recommended_tests,
                "supporting_evidence": diagnosis.supporting_evidence,
            },
            "service_plan": {
                "id": service_plan.id,
                "parts_required": [
                    {
                        "part_number": p.part_number,
                        "part_name": p.part_name,
                        "quantity": p.quantity,
                        "unit_price": p.unit_price,
                        "total_price": p.total_price,
                        "availability": p.availability,
                    }
                    for p in service_plan.parts_required
                ],
                "labor_hours": service_plan.labor_hours,
                "labor_cost": service_plan.labor_cost,
                "parts_cost": service_plan.parts_cost,
                "total_cost": service_plan.total_cost,
                "urgency": service_plan.urgency.value,
                "estimated_completion_days": service_plan.estimated_completion_days,
                "first_time_fix": service_plan.first_time_fix,
            },
            "quality_check": quality_plan,
        }

    def _on_teardown(self) -> None:
        """Clean up automotive diagnostic solution resources."""
        self._agents.clear()
        self._vehicles.clear()
        self._symptoms.clear()
        self._diagnostics.clear()
        self._service_plans.clear()
        self._vehicle_count = 0
        self._symptom_count = 0
        self._diagnostic_count = 0
        self._service_plan_count = 0

    # ------------------------------------------------------------------
    # Workflow steps
    # ------------------------------------------------------------------

    def _intake_symptoms(
        self, data: Dict[str, Any]
    ) -> tuple[VehicleInfo, List[SymptomReport]]:
        """Step 1: Gather vehicle information and symptoms."""
        self._vehicle_count += 1
        vin = data.get("vin", f"vin-{self._vehicle_count}")

        try:
            vtype = VehicleType(data.get("vehicle_type", "sedan"))
        except ValueError:
            vtype = VehicleType.SEDAN

        vehicle = VehicleInfo(
            vin=vin,
            make=data.get("make", ""),
            model=data.get("model", ""),
            year=data.get("year", 2020),
            vehicle_type=vtype,
            mileage=data.get("mileage", 0),
            engine_type=data.get("engine_type", ""),
            transmission=data.get("transmission", "automatic"),
            fuel_type=data.get("fuel_type", "gasoline"),
            maintenance_history=data.get("maintenance_history", []),
            current_codes=data.get("diagnostic_codes", []),
        )
        self._vehicles[vin] = vehicle

        # Parse symptoms
        symptom_data = data.get("symptoms", [])
        symptoms = []
        for idx, sym in enumerate(symptom_data):
            self._symptom_count += 1
            sym_id = f"sym-{self._symptom_count}"
            if isinstance(sym, str):
                symptom = SymptomReport(
                    id=sym_id,
                    vin=vin,
                    description=sym,
                )
            elif isinstance(sym, dict):
                symptom = SymptomReport(
                    id=sym_id,
                    vin=vin,
                    description=sym.get("description", ""),
                    location=sym.get("location", ""),
                    frequency=sym.get("frequency", "intermittent"),
                    severity=sym.get("severity", 5),
                    when_noticed=sym.get("when_noticed", ""),
                    conditions=sym.get("conditions", []),
                    related_codes=sym.get("related_codes", []),
                )
            else:
                continue
            symptoms.append(symptom)
            self._symptoms[sym_id] = symptom

        return vehicle, symptoms

    def _perform_diagnosis(
        self, vehicle: VehicleInfo, symptoms: List[SymptomReport]
    ) -> DiagnosticResult:
        """Step 2: Perform diagnostic analysis."""
        self._diagnostic_count += 1
        diag_id = f"diag-{self._diagnostic_count}"

        # Match symptom patterns
        all_symptom_text = " ".join(
            s.description.lower() + " " + " ".join(s.related_codes)
            for s in symptoms
        )

        best_match = None
        best_score = 0

        for issue_name, pattern in self._diagnostic_patterns.items():
            score = 0
            # Match symptom keywords
            for symptom_keyword in pattern["symptoms"]:
                if symptom_keyword.lower() in all_symptom_text:
                    score += 2
            # Match diagnostic codes
            for code in pattern["codes"]:
                if code in all_symptom_text.upper():
                    score += 3
            if score > best_score:
                best_score = score
                best_match = (issue_name, pattern)

        if best_match and best_score > 0:
            issue_name, pattern = best_match
            # Calculate confidence based on match quality
            max_possible = len(pattern["symptoms"]) * 2 + len(pattern["codes"]) * 3
            match_ratio = best_score / max_possible if max_possible > 0 else 0

            if match_ratio >= 0.7:
                confidence = DiagnosticConfidence.VERY_HIGH
            elif match_ratio >= 0.5:
                confidence = DiagnosticConfidence.HIGH
            elif match_ratio >= 0.3:
                confidence = DiagnosticConfidence.MODERATE
            else:
                confidence = DiagnosticConfidence.LOW

            probability = min(0.95, 0.3 + match_ratio * 0.65)

            # Supporting evidence
            evidence = []
            for symptom_keyword in pattern["symptoms"]:
                if symptom_keyword.lower() in all_symptom_text:
                    evidence.append(f"Symptom match: {symptom_keyword}")
            for code in pattern["codes"]:
                if code in all_symptom_text.upper():
                    evidence.append(f"Diagnostic code match: {code}")

            # Recommended tests
            tests = [
                "Visual inspection of suspected components",
                "Scan tool diagnostics for verification",
            ]
            if pattern["causes"][0]["cause"] != "Battery age/failure":
                tests.append("Component testing to confirm failure")

            diagnosis = DiagnosticResult(
                id=diag_id,
                vin=vehicle.vin,
                suspected_issue=issue_name.replace("_", " ").title(),
                confidence=confidence,
                probability=round(probability, 3),
                supporting_evidence=evidence,
                possible_causes=pattern["causes"],
                recommended_tests=tests,
            )
        else:
            # Generic diagnosis when no pattern matches
            diagnosis = DiagnosticResult(
                id=diag_id,
                vin=vehicle.vin,
                suspected_issue="Requires further diagnostic testing",
                confidence=DiagnosticConfidence.LOW,
                probability=0.2,
                supporting_evidence=["Insufficient data for automated diagnosis"],
                possible_causes=[{"cause": "Unknown - requires physical inspection", "probability": 1.0}],
                recommended_tests=[
                    "Comprehensive diagnostic scan",
                    "Visual inspection by technician",
                    "Road test for symptom verification",
                ],
            )

        self._diagnostics[diag_id] = diagnosis
        return diagnosis

    def _identify_parts(self, diagnosis: DiagnosticResult) -> List[PartReplacement]:
        """Step 3: Identify required parts for the repair."""
        parts = []
        issue_lower = diagnosis.suspected_issue.lower().replace(" ", "_")

        pattern = self._diagnostic_patterns.get(issue_lower)
        if not pattern:
            return parts

        parts_list = pattern["parts"]
        parts_pricing = {
            "spark plugs": 80.0,
            "ignition coil": 120.0,
            "fuel injector": 200.0,
            "brake pads": 60.0,
            "rotors": 100.0,
            "brake calipers": 180.0,
            "battery": 150.0,
            "alternator": 350.0,
            "gasket set": 75.0,
            "oil seal": 45.0,
            "oil filter": 15.0,
            "transmission fluid": 80.0,
            "solenoid": 150.0,
            "torque converter": 450.0,
            "shocks/struts": 250.0,
            "ball joints": 120.0,
            "tie rod ends": 80.0,
            "refrigerant": 60.0,
            "ac compressor": 600.0,
            "condenser": 200.0,
        }

        markup = self.config.get("parts_markup", 0.25)

        for idx, part_name in enumerate(parts_list):
            base_price = parts_pricing.get(part_name, 100.0)
            unit_price = round(base_price * (1 + markup), 2)
            quantity = 1
            if "spark plugs" in part_name:
                quantity = 4
            if "brake pads" in part_name or "rotors" in part_name:
                quantity = 2

            part = PartReplacement(
                part_number=f"PART-{idx + 1:04d}",
                part_name=part_name.replace("_", " ").title(),
                quantity=quantity,
                unit_price=unit_price,
                total_price=round(unit_price * quantity, 2),
                availability="in_stock" if idx < 2 else "1-2 days",
                supplier="OEM" if idx == 0 else "Aftermarket",
                lead_time_days=0 if idx < 2 else 2,
            )
            parts.append(part)

        return parts

    def _create_service_plan(
        self,
        vehicle: VehicleInfo,
        diagnosis: DiagnosticResult,
        parts: List[PartReplacement],
    ) -> ServicePlan:
        """Step 4: Create comprehensive service plan."""
        self._service_plan_count += 1
        plan_id = f"sp-{self._service_plan_count}"

        # Calculate costs
        labor_rate = self.config.get("labor_rate", 120.0)
        diagnostic_fee = self.config.get("diagnostic_fee", 120.0)

        issue_lower = diagnosis.suspected_issue.lower().replace(" ", "_")
        pattern = self._diagnostic_patterns.get(issue_lower)
        labor_hours = pattern["labor_hours"] if pattern else 2.0

        # Adjust labor based on confidence (lower confidence = more time)
        if diagnosis.confidence == DiagnosticConfidence.LOW:
            labor_hours *= 1.5
        elif diagnosis.confidence == DiagnosticConfidence.MODERATE:
            labor_hours *= 1.2

        parts_cost = sum(p.total_price for p in parts)
        labor_cost = round(labor_hours * labor_rate, 2)
        total_cost = round(parts_cost + labor_cost + diagnostic_fee, 2)

        # Determine urgency
        if pattern:
            urgency = RepairUrgency(pattern["urgency"])
        else:
            urgency = RepairUrgency.SCHEDULED

        # First time fix estimate
        first_time_fix = diagnosis.confidence in (
            DiagnosticConfidence.HIGH, DiagnosticConfidence.VERY_HIGH
        )

        # Estimated completion
        lead_time = max((p.lead_time_days for p in parts), default=0)
        est_days = max(1, lead_time + 1)

        service_plan = ServicePlan(
            id=plan_id,
            vin=vehicle.vin,
            diagnostic_result=diagnosis,
            parts_required=parts,
            labor_hours=round(labor_hours, 1),
            labor_cost=labor_cost,
            parts_cost=round(parts_cost, 2),
            total_cost=total_cost,
            urgency=urgency,
            estimated_completion_days=est_days,
            status=ServiceStatus.DIAGNOSING,
            first_time_fix=first_time_fix,
        )
        self._service_plans[plan_id] = service_plan
        return service_plan

    def _create_quality_check_plan(self, diagnosis: DiagnosticResult) -> Dict[str, Any]:
        """Step 5: Create quality inspection plan."""
        required = self.config.get("quality_check_required", True)

        checks = [
            "Verify repair completeness",
            "Test drive to confirm issue resolution",
            "Scan for any remaining diagnostic codes",
            "Visual quality inspection of workmanship",
        ]

        if self.config.get("warranty_included", True):
            checks.append("Verify warranty coverage documentation")

        return {
            "required": required,
            "checks": checks,
            "estimated_time_minutes": 30 if required else 0,
            "test_drive_required": True,
        }

    # ------------------------------------------------------------------
    # Agent prompts
    # ------------------------------------------------------------------

    def _build_diagnostic_tech_prompt(self) -> str:
        """Build the system prompt for the diagnostic tech role."""
        return (
            "You are an experienced automotive diagnostic technician with "
            "ASE certification. Use your deep knowledge of automotive systems "
            "to diagnose vehicle issues accurately. Follow systematic "
            "troubleshooting procedures, use diagnostic tools effectively, "
            "and always verify your diagnoses with tests. Document all "
            "findings clearly and recommend the most cost-effective repair "
            "approach. Prioritize safety and accuracy over speed."
        )

    def _build_parts_specialist_prompt(self) -> str:
        """Build the system prompt for the parts specialist role."""
        return (
            "You are an automotive parts specialist with extensive knowledge "
            "of vehicle parts, cross-references, and supplier networks. "
            "Identify the correct parts for each repair, verify fitment, "
            "check availability across multiple suppliers, and find the "
            "best balance of quality, price, and delivery time. Always "
            "confirm part compatibility before ordering."
        )

    def _build_service_advisor_prompt(self) -> str:
        """Build the system prompt for the service advisor role."""
        return (
            "You are a professional service advisor who bridges the gap "
            "between technicians and customers. Explain vehicle issues and "
            "repair recommendations in clear, understandable language. "
            "Provide accurate cost estimates, set proper expectations for "
            "timing, and ensure customers understand their options. "
            "Maintain excellent communication throughout the service process."
        )

    def _build_quality_inspector_prompt(self) -> str:
        """Build the system prompt for the quality inspector role."""
        return (
            "You are a quality assurance inspector for automotive repairs. "
            "Your job is to verify that every repair meets the highest "
            "standards before the vehicle is returned to the customer. "
            "Perform thorough inspections, conduct test drives, verify "
            "that all work was completed correctly, and ensure no new "
            "issues were introduced. Sign off only when you are fully "
            "satisfied with the quality of work."
        )

    # ------------------------------------------------------------------
    # Metrics
    # ------------------------------------------------------------------

    def _update_automotive_metrics(self) -> None:
        """Update automotive-specific metrics."""
        if self._diagnostics:
            total = len(self._diagnostics)
            conf_map = {
                DiagnosticConfidence.LOW: 0.25,
                DiagnosticConfidence.MODERATE: 0.5,
                DiagnosticConfidence.HIGH: 0.75,
                DiagnosticConfidence.VERY_HIGH: 1.0,
            }
            avg_conf = sum(
                conf_map.get(d.confidence, 0.5)
                for d in self._diagnostics.values()
            ) / total
            self.automotive_metrics.avg_diagnostic_confidence = avg_conf

        if self._service_plans:
            total = len(self._service_plans)
            ftf_count = sum(
                1 for sp in self._service_plans.values() if sp.first_time_fix
            )
            self.automotive_metrics.first_time_fix_rate = (
                ftf_count / total if total > 0 else 0.0
            )

            avg_cost = sum(
                sp.total_cost for sp in self._service_plans.values()
            ) / total
            self.automotive_metrics.avg_service_cost = avg_cost

    def get_automotive_metrics(self) -> Dict[str, Any]:
        """Get the current automotive-specific metrics.

        Returns:
            Dictionary with automotive metrics including diagnostic accuracy,
            repair time, first-time fix rate, and service costs.
        """
        return self.automotive_metrics.to_dict()
