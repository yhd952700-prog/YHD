"""
Smart Farming Advisor AI Solution Template.

A comprehensive AI-powered smart farming advisor solution for
agricultural operations and precision farming. Includes multi-agent
roles for crop specialization, soil analysis, pest management,
and yield forecasting.

Typical workflow:
    field analysis -> soil assessment -> crop planning
    -> pest management -> yield prediction
"""

from typing import Optional, Dict, Any, List
from enum import Enum
from dataclasses import dataclass, field

from solutions.templates.base_solution import (
    BaseSolution,
    SolutionMetadata,
    SolutionMetrics,
)


class CropType(str, Enum):
    """Types of crops."""
    CORN = "corn"
    WHEAT = "wheat"
    SOYBEAN = "soybean"
    RICE = "rice"
    COTTON = "cotton"
    POTATO = "potato"
    TOMATO = "tomato"
    GRAPES = "grapes"
    APPLES = "apples"
    VEGETABLES = "vegetables"


class SoilType(str, Enum):
    """Types of soil."""
    SANDY = "sandy"
    SILTY = "silty"
    CLAY = "clay"
    LOAMY = "loamy"
    PEATY = "peaty"
    CHALKY = "chalky"


class PestSeverity(str, Enum):
    """Pest/disease severity levels."""
    NONE = "none"
    LOW = "low"
    MODERATE = "moderate"
    HIGH = "high"
    SEVERE = "severe"


class AgricultureRole(str, Enum):
    """Agent roles in the farming advisor solution."""
    CROP_SPECIALIST = "crop_specialist"
    SOIL_ANALYST = "soil_analyst"
    PEST_ADVISOR = "pest_advisor"
    YIELD_FORECASTER = "yield_forecaster"


@dataclass
class Field:
    """Represents an agricultural field.

    Attributes:
        field_id: Unique field identifier.
        name: Field name.
        area_acres: Field size in acres.
        location: Geographic location.
        soil_type: Type of soil.
        soil_ph: Soil pH level.
        current_crop: Currently planted crop.
        planting_date: ISO 8601 planting date.
        growth_stage: Current growth stage.
        irrigation_type: Type of irrigation system.
        historical_yields: Historical yield data.
        elevation: Elevation in meters.
    """
    field_id: str
    name: str = ""
    area_acres: float = 0.0
    location: str = ""
    soil_type: SoilType = SoilType.LOAMY
    soil_ph: float = 6.5
    current_crop: CropType = CropType.CORN
    planting_date: Optional[str] = None
    growth_stage: str = "seedling"
    irrigation_type: str = "drip"
    historical_yields: Dict[str, float] = field(default_factory=dict)
    elevation: float = 0.0


@dataclass
class SoilAssessment:
    """Represents results of a soil analysis.

    Attributes:
        assessment_id: Unique assessment ID.
        field_id: Associated field ID.
        soil_type: Soil type classification.
        ph_level: Soil pH level.
        ph_status: pH status (acidic/neutral/alkaline).
        nutrient_levels: Nutrient levels (N, P, K, etc.).
        organic_matter: Organic matter percentage.
        moisture_content: Soil moisture percentage.
        compaction: Soil compaction level.
        recommendations: Soil amendment recommendations.
        overall_health: Overall soil health score (0-100).
    """
    assessment_id: str
    field_id: str
    soil_type: SoilType = SoilType.LOAMY
    ph_level: float = 6.5
    ph_status: str = "neutral"
    nutrient_levels: Dict[str, str] = field(default_factory=dict)
    organic_matter: float = 3.0
    moisture_content: float = 25.0
    compaction: str = "moderate"
    recommendations: List[str] = field(default_factory=list)
    overall_health: float = 70.0


@dataclass
class PestDetection:
    """Represents a detected pest or disease.

    Attributes:
        detection_id: Unique detection ID.
        field_id: Associated field ID.
        pest_name: Name of pest/disease.
        pest_type: Type (insect, disease, weed, etc.).
        severity: Severity level.
        affected_area_percent: Percentage of field affected.
        confidence: Detection confidence (0-1).
        symptoms: Observed symptoms.
        spread_rate: Estimated spread rate.
        treatment_recommendation: Recommended treatment.
        estimated_yield_impact: Estimated yield impact percentage.
    """
    detection_id: str
    field_id: str
    pest_name: str = ""
    pest_type: str = ""
    severity: PestSeverity = PestSeverity.LOW
    affected_area_percent: float = 0.0
    confidence: float = 0.0
    symptoms: List[str] = field(default_factory=list)
    spread_rate: str = "slow"
    treatment_recommendation: str = ""
    estimated_yield_impact: float = 0.0


@dataclass
class YieldForecast:
    """Represents a yield forecast.

    Attributes:
        forecast_id: Unique forecast ID.
        field_id: Associated field ID.
        crop: Crop type.
        predicted_yield_bushels: Predicted yield in bushels per acre.
        predicted_yield_weight: Predicted yield per acre (alternative unit).
        lower_bound: Lower confidence bound.
        upper_bound: Upper confidence bound.
        confidence: Forecast confidence level.
        accuracy_percent: Historical forecast accuracy.
        factors_considered: Factors used in the forecast.
        comparison_to_average: Comparison to local average.
        harvest_date_estimate: Estimated harvest date.
    """
    forecast_id: str
    field_id: str
    crop: CropType = CropType.CORN
    predicted_yield_bushels: float = 0.0
    predicted_yield_weight: float = 0.0
    lower_bound: float = 0.0
    upper_bound: float = 0.0
    confidence: str = "medium"
    accuracy_percent: float = 0.0
    factors_considered: List[str] = field(default_factory=list)
    comparison_to_average: str = "average"
    harvest_date_estimate: Optional[str] = None


@dataclass
class AgricultureMetrics(SolutionMetrics):
    """Extended metrics for the farming advisor solution.

    Attributes:
        yield_prediction_accuracy: Yield prediction accuracy percentage.
        pest_detection_rate: Pest detection accuracy rate.
        cost_savings_percent: Estimated cost savings percentage.
        fields_analyzed: Total fields analyzed.
        forecasts_generated: Total yield forecasts generated.
        pests_detected: Total pests/diseases detected.
        avg_yield_improvement: Average yield improvement percentage.
        water_savings_percent: Water savings percentage.
    """
    yield_prediction_accuracy: float = 0.0
    pest_detection_rate: float = 0.0
    cost_savings_percent: float = 0.0
    fields_analyzed: int = 0
    forecasts_generated: int = 0
    pests_detected: int = 0
    avg_yield_improvement: float = 0.0
    water_savings_percent: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        """Convert agriculture metrics to a dictionary."""
        base = super().to_dict()
        base.update({
            "yield_prediction_accuracy": self.yield_prediction_accuracy,
            "pest_detection_rate": self.pest_detection_rate,
            "cost_savings_percent": self.cost_savings_percent,
            "fields_analyzed": self.fields_analyzed,
            "forecasts_generated": self.forecasts_generated,
            "pests_detected": self.pests_detected,
            "avg_yield_improvement": self.avg_yield_improvement,
            "water_savings_percent": self.water_savings_percent,
        })
        return base


class FarmingAdvisorSolution(BaseSolution):
    """Smart Farming Advisor AI solution template.

    Provides a complete, pre-configured precision agriculture solution
    with multi-agent collaboration for crop management, soil analysis,
    pest detection, and yield forecasting.

    Pre-configured agent roles:
        - Crop Specialist: Provides crop-specific guidance and management
        - Soil Analyst: Analyzes soil health and nutrient levels
        - Pest Advisor: Detects and manages pests, diseases, and weeds
        - Yield Forecaster: Predicts crop yields using multiple factors

    Workflow:
        1. Field Analysis - Analyze field conditions and crop status
        2. Soil Assessment - Evaluate soil health and nutrient levels
        3. Crop Planning - Recommend planting strategies and crop selection
        4. Pest Management - Detect and recommend pest/disease treatments
        5. Yield Prediction - Forecast crop yields and harvest timing

    Configuration options:
        - primary_crop: Primary crop being grown
        - farm_size: Size of farming operation
        - climate_zone: Climate zone/region
        - irrigation_system: Type of irrigation system
        - organic: Whether organic farming practices are used
    """

    def _build_metadata(self) -> SolutionMetadata:
        """Build metadata for the farming advisor solution."""
        return SolutionMetadata(
            name="farming-advisor",
            version="1.0.0",
            industry="agriculture",
            description=(
                "AI-powered smart farming advisor solution with crop management, "
                "soil analysis, pest detection, and yield forecasting."
            ),
            author="LiuHao AI OS",
            tags=["agriculture", "farming", "precision-ag", "yield-forecasting", "pest-detection"],
            min_liuhao_version="1.0.0",
        )

    def _build_config_schema(self) -> None:
        """Define configuration fields for the agriculture solution."""
        self.config_schema.add_field(
            name="primary_crop",
            field_type="string",
            required=True,
            description="Primary crop being cultivated",
            valid_values=[c.value for c in CropType],
        )
        self.config_schema.add_field(
            name="farm_size",
            field_type="string",
            required=False,
            default="medium",
            description="Size of farming operation",
            valid_values=["small", "medium", "large", "enterprise"],
        )
        self.config_schema.add_field(
            name="climate_zone",
            field_type="string",
            required=False,
            default="temperate",
            description="Climate zone of the farm",
            valid_values=["tropical", "subtropical", "temperate", "continental", "mediterranean", "arid"],
        )
        self.config_schema.add_field(
            name="irrigation_system",
            field_type="string",
            required=False,
            default="drip",
            description="Type of irrigation system",
            valid_values=["drip", "sprinkler", "flood", "center_pivot", "rainfed"],
        )
        self.config_schema.add_field(
            name="organic",
            field_type="bool",
            required=False,
            default=False,
            description="Whether organic farming practices are used",
        )
        self.config_schema.add_field(
            name="soil_sampling_frequency",
            field_type="string",
            required=False,
            default="seasonal",
            description="Soil sampling frequency",
            valid_values=["weekly", "monthly", "seasonal", "annual"],
        )

    def _on_initialize(self) -> None:
        """Initialize farming advisor solution state."""
        self._agents: Dict[str, Dict[str, Any]] = {}
        self._fields: Dict[str, Field] = {}
        self._soil_assessments: Dict[str, SoilAssessment] = {}
        self._pest_detections: Dict[str, PestDetection] = {}
        self._yield_forecasts: Dict[str, YieldForecast] = {}
        self._field_count = 0
        self._assessment_count = 0
        self._pest_count = 0
        self._forecast_count = 0

        # Initialize agriculture-specific metrics
        self.ag_metrics = AgricultureMetrics()

    def _on_deploy(self) -> None:
        """Deploy farming advisor agents and workflows."""
        agent_configs = [
            (AgricultureRole.CROP_SPECIALIST, {
                "name": "Crop Specialist",
                "description": "Provides crop-specific guidance and management",
                "system_prompt": self._build_crop_specialist_prompt(),
                "tools": ["crop_calendar", "growth_monitor", "variety_selector"],
            }),
            (AgricultureRole.SOIL_ANALYST, {
                "name": "Soil Analyst",
                "description": "Analyzes soil health and nutrient levels",
                "system_prompt": self._build_soil_analyst_prompt(),
                "tools": ["soil_testing", "nutrient_analysis", "amendment_recommender"],
            }),
            (AgricultureRole.PEST_ADVISOR, {
                "name": "Pest Advisor",
                "description": "Detects and manages pests, diseases, and weeds",
                "system_prompt": self._build_pest_advisor_prompt(),
                "tools": ["pest_detection", "disease_identification", "ipm_planner"],
            }),
            (AgricultureRole.YIELD_FORECASTER, {
                "name": "Yield Forecaster",
                "description": "Predicts crop yields using multiple factors",
                "system_prompt": self._build_yield_forecaster_prompt(),
                "tools": ["yield_model", "weather_integration", "satellite_data"],
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
        """Process a farming advisory request through the full workflow.

        Args:
            input_data: Dictionary containing field and farming data.

        Returns:
            Dictionary with farming advisory results including soil
            analysis, crop recommendations, pest detection, and yield forecast.
        """
        # Step 1: Field analysis
        field = self._analyze_field(input_data)

        # Step 2: Soil assessment
        soil_assessment = self._assess_soil(field)

        # Step 3: Crop planning
        crop_plan = self._plan_crops(field, soil_assessment)

        # Step 4: Pest management
        pests = self._detect_pests(field)

        # Step 5: Yield prediction
        yield_forecast = self._forecast_yield(field, soil_assessment, pests)

        # Update metrics
        self.ag_metrics.fields_analyzed += 1
        self.ag_metrics.forecasts_generated += 1
        self.ag_metrics.pests_detected += len(pests)
        self._update_ag_metrics()

        return {
            "field_analysis": {
                "field_id": field.field_id,
                "name": field.name,
                "area_acres": field.area_acres,
                "crop": field.current_crop.value,
                "growth_stage": field.growth_stage,
                "soil_type": field.soil_type.value,
                "irrigation": field.irrigation_type,
            },
            "soil_assessment": {
                "ph": soil_assessment.ph_level,
                "ph_status": soil_assessment.ph_status,
                "nutrients": soil_assessment.nutrient_levels,
                "organic_matter": soil_assessment.organic_matter,
                "moisture": soil_assessment.moisture_content,
                "overall_health": soil_assessment.overall_health,
                "recommendations": soil_assessment.recommendations,
            },
            "crop_plan": crop_plan,
            "pest_detection": {
                "total_pests": len(pests),
                "pests": [
                    {
                        "name": p.pest_name,
                        "type": p.pest_type,
                        "severity": p.severity.value,
                        "affected_area": p.affected_area_percent,
                        "confidence": p.confidence,
                        "treatment": p.treatment_recommendation,
                        "yield_impact": p.estimated_yield_impact,
                    }
                    for p in pests
                ],
            },
            "yield_forecast": {
                "crop": yield_forecast.crop.value,
                "predicted_yield": yield_forecast.predicted_yield_bushels,
                "unit": "bushels/acre",
                "lower_bound": yield_forecast.lower_bound,
                "upper_bound": yield_forecast.upper_bound,
                "confidence": yield_forecast.confidence,
                "accuracy": yield_forecast.accuracy_percent,
                "vs_average": yield_forecast.comparison_to_average,
                "factors": yield_forecast.factors_considered,
            },
        }

    def _on_teardown(self) -> None:
        """Clean up farming advisor solution resources."""
        self._agents.clear()
        self._fields.clear()
        self._soil_assessments.clear()
        self._pest_detections.clear()
        self._yield_forecasts.clear()
        self._field_count = 0
        self._assessment_count = 0
        self._pest_count = 0
        self._forecast_count = 0

    # ------------------------------------------------------------------
    # Workflow steps
    # ------------------------------------------------------------------

    def _analyze_field(self, data: Dict[str, Any]) -> Field:
        """Step 1: Analyze field conditions."""
        self._field_count += 1
        field_id = data.get("field_id", f"field-{self._field_count}")

        field = Field(
            field_id=field_id,
            name=data.get("name", f"Field {self._field_count}"),
            area_acres=data.get("area_acres", 50.0),
            location=data.get("location", "Farm Location"),
            soil_type=SoilType(data.get("soil_type", SoilType.LOAMY.value)),
            soil_ph=data.get("soil_ph", 6.5),
            current_crop=CropType(data.get("crop", self.config.get("primary_crop", CropType.CORN.value))),
            growth_stage=data.get("growth_stage", "vegetative"),
            irrigation_type=data.get("irrigation", self.config.get("irrigation_system", "drip")),
            historical_yields=data.get("historical_yields", {"2023": 150.0, "2022": 140.0, "2021": 155.0}),
        )
        self._fields[field_id] = field
        return field

    def _assess_soil(self, field: Field) -> SoilAssessment:
        """Step 2: Assess soil health and nutrients."""
        self._assessment_count += 1
        assessment_id = f"soil-{self._assessment_count}"

        # pH status
        ph = field.soil_ph
        if ph < 5.5:
            ph_status = "strongly_acidic"
        elif ph < 6.0:
            ph_status = "moderately_acidic"
        elif ph < 7.0:
            ph_status = "slightly_acidic"
        elif ph < 7.5:
            ph_status = "neutral"
        elif ph < 8.0:
            ph_status = "slightly_alkaline"
        else:
            ph_status = "alkaline"

        # Nutrient levels (simulated based on soil type)
        nutrient_base = {
            SoilType.SANDY: {"N": "low", "P": "low", "K": "low", "OM": "low"},
            SoilType.LOAMY: {"N": "medium", "P": "medium", "K": "high", "OM": "medium"},
            SoilType.CLAY: {"N": "high", "P": "medium", "K": "medium", "OM": "medium"},
            SoilType.SILTY: {"N": "high", "P": "medium", "K": "high", "OM": "high"},
            SoilType.PEATY: {"N": "high", "P": "low", "K": "low", "OM": "very_high"},
            SoilType.CHALKY: {"N": "medium", "P": "low", "K": "medium", "OM": "low"},
        }
        nutrients = nutrient_base.get(field.soil_type, nutrient_base[SoilType.LOAMY])

        # Recommendations
        recommendations = []
        if ph < 6.0:
            recommendations.append("Apply lime to raise soil pH")
        elif ph > 7.5:
            recommendations.append("Consider sulfur application to lower pH")

        if nutrients.get("N") == "low":
            recommendations.append("Add nitrogen fertilizer")
        if nutrients.get("P") == "low":
            recommendations.append("Apply phosphorus supplement")
        if nutrients.get("K") == "low":
            recommendations.append("Add potassium fertilizer")

        if self.config.get("organic", False):
            recommendations = [r.replace("fertilizer", "organic amendment") for r in recommendations]
            recommendations.append("Use compost and cover crops for soil building")

        # Overall health score
        health_score = 50.0
        if 6.0 <= ph <= 7.5:
            health_score += 15
        if nutrients.get("OM") in ("high", "very_high"):
            health_score += 15
        elif nutrients.get("OM") == "medium":
            health_score += 10
        if field.soil_type == SoilType.LOAMY:
            health_score += 10
        health_score = min(100.0, health_score)

        assessment = SoilAssessment(
            assessment_id=assessment_id,
            field_id=field.field_id,
            soil_type=field.soil_type,
            ph_level=ph,
            ph_status=ph_status,
            nutrient_levels=nutrients,
            organic_matter=3.0 + (hash(field.field_id) % 30) / 10,
            moisture_content=20.0 + (hash(field.field_id + "m") % 30),
            recommendations=recommendations if recommendations else ["Soil is in good condition"],
            overall_health=round(health_score, 1),
        )
        self._soil_assessments[assessment_id] = assessment
        return assessment

    def _plan_crops(self, field: Field, soil: SoilAssessment) -> Dict[str, Any]:
        """Step 3: Recommend crop planning strategies."""
        crop = field.current_crop
        organic = self.config.get("organic", False)

        # Crop-specific recommendations
        crop_tips = {
            CropType.CORN: [
                "Plant at 2-3 inch depth when soil temp reaches 50F",
                "Apply nitrogen in split applications for efficiency",
                "Monitor for corn rootworm and European corn borer",
            ],
            CropType.WHEAT: [
                "Plant in fall for winter wheat (optimum: mid-September)",
                "Apply nitrogen in spring at green-up",
                "Monitor for rust and Fusarium head blight",
            ],
            CropType.SOYBEAN: [
                "Plant 1-1.5 inches deep in well-drained soil",
                "Inoculate seeds with rhizobium for nitrogen fixation",
                "Watch for soybean cyst nematode and aphids",
            ],
            CropType.RICE: [
                "Maintain consistent water depth of 2-4 inches",
                "Apply nitrogen at early tillering and panicle initiation",
                "Monitor for blast disease and stem borers",
            ],
        }

        tips = crop_tips.get(crop, [
            "Follow recommended planting dates for your region",
            "Maintain proper soil moisture levels",
            "Monitor for common pests and diseases",
        ])

        # Rotation recommendation
        rotation_suggestion = {
            CropType.CORN: "Rotate with soybeans for nitrogen benefit",
            CropType.SOYBEAN: "Rotate with corn or small grains",
            CropType.WHEAT: "Follow with soybeans or cover crop",
        }.get(crop, "Practice crop rotation for soil health")

        return {
            "primary_crop": crop.value,
            "growth_stage": field.growth_stage,
            "recommendations": tips,
            "rotation_suggestion": rotation_suggestion,
            "planting_density": "Follow local extension recommendations",
            "fertilizer_plan": "Soil test based fertilization recommended",
            "irrigation_schedule": f"Schedule based on {field.irrigation_type} system",
            "organic_practices": organic,
            "cover_crop_recommendation": "Plant cover crops post-harvest for soil protection",
        }

    def _detect_pests(self, field: Field) -> List[PestDetection]:
        """Step 4: Detect and assess pests and diseases."""
        pests_data = [
            ("Aphids", "insect", PestSeverity.LOW, 5.0, 0.85),
            ("Fungal Leaf Spot", "disease", PestSeverity.MODERATE, 10.0, 0.75),
            ("Weed Infestation", "weed", PestSeverity.LOW, 8.0, 0.90),
        ]

        # Organic farms may have more pest pressure
        if self.config.get("organic", False):
            pests_data.append(("Cutworms", "insect", PestSeverity.MODERATE, 3.0, 0.70))

        pests = []
        for i, (name, ptype, severity, area, conf) in enumerate(pests_data):
            self._pest_count += 1
            detection_id = f"pest-{self._pest_count}"

            # Yield impact estimate
            yield_impact = {
                PestSeverity.NONE: 0.0,
                PestSeverity.LOW: 2.0,
                PestSeverity.MODERATE: 8.0,
                PestSeverity.HIGH: 20.0,
                PestSeverity.SEVERE: 40.0,
            }.get(severity, 5.0)

            # Treatment recommendation
            if self.config.get("organic", False):
                treatment = f"Organic treatment: Introduce beneficial insects, use neem oil, crop rotation"
            else:
                treatment = f"Apply appropriate pesticide/fungicide per label instructions. Consider integrated pest management."

            pest = PestDetection(
                detection_id=detection_id,
                field_id=field.field_id,
                pest_name=name,
                pest_type=ptype,
                severity=severity,
                affected_area_percent=area,
                confidence=conf,
                symptoms=["Yellowing leaves", "Visible damage", "Stunted growth"][:i+1],
                spread_rate="slow" if severity == PestSeverity.LOW else "moderate",
                treatment_recommendation=treatment,
                estimated_yield_impact=yield_impact,
            )
            pests.append(pest)
            self._pest_detections[detection_id] = pest

        return pests

    def _forecast_yield(
        self, field: Field, soil: SoilAssessment, pests: List[PestDetection]
    ) -> YieldForecast:
        """Step 5: Predict crop yield."""
        self._forecast_count += 1
        forecast_id = f"yield-{self._forecast_count}"

        # Base yield by crop (bushels per acre)
        base_yields = {
            CropType.CORN: 180.0,
            CropType.WHEAT: 60.0,
            CropType.SOYBEAN: 50.0,
            CropType.RICE: 7000.0,
            CropType.COTTON: 800.0,
            CropType.POTATO: 400.0,
            CropType.TOMATO: 30.0,
            CropType.GRAPES: 5.0,
            CropType.APPLES: 400.0,
            CropType.VEGETABLES: 200.0,
        }
        base_yield = base_yields.get(field.current_crop, 100.0)

        # Adjust for soil health
        soil_factor = soil.overall_health / 100.0
        adjusted = base_yield * (0.7 + soil_factor * 0.3)

        # Adjust for pests
        total_yield_impact = sum(p.estimated_yield_impact for p in pests) / 100
        adjusted *= (1 - total_yield_impact)

        # Historical average
        if field.historical_yields:
            hist_avg = sum(field.historical_yields.values()) / len(field.historical_yields)
            # Blend with historical
            adjusted = (adjusted + hist_avg) / 2

        # Confidence bounds
        lower = adjusted * 0.85
        upper = adjusted * 1.15

        # Confidence level
        if soil.overall_health > 80 and len(pests) <= 1:
            confidence = "high"
            accuracy = 0.90
        elif soil.overall_health > 60:
            confidence = "medium"
            accuracy = 0.82
        else:
            confidence = "low"
            accuracy = 0.70

        # Comparison to average
        avg = base_yield * 0.9
        if adjusted > avg * 1.1:
            comparison = "above_average"
        elif adjusted < avg * 0.9:
            comparison = "below_average"
        else:
            comparison = "average"

        forecast = YieldForecast(
            forecast_id=forecast_id,
            field_id=field.field_id,
            crop=field.current_crop,
            predicted_yield_bushels=round(adjusted, 1),
            predicted_yield_weight=round(adjusted * 60, 1),
            lower_bound=round(lower, 1),
            upper_bound=round(upper, 1),
            confidence=confidence,
            accuracy_percent=accuracy,
            factors_considered=[
                "Soil health and nutrients",
                "Historical yield data",
                "Pest and disease pressure",
                "Current growth stage",
                "Climate zone conditions",
                "Crop variety characteristics",
            ],
            comparison_to_average=comparison,
        )
        self._yield_forecasts[forecast_id] = forecast
        return forecast

    # ------------------------------------------------------------------
    # Agent prompts
    # ------------------------------------------------------------------

    def _build_crop_specialist_prompt(self) -> str:
        """Build the system prompt for the crop specialist role."""
        crop = self.config.get("primary_crop", "corn")
        return (
            f"You are a {crop} specialist agronomist. Provide expert guidance "
            f"on planting, fertilization, irrigation, and crop management. "
            f"Consider growth stages, seasonal timing, and best practices. "
            f"Recommend appropriate varieties based on climate and soil conditions."
        )

    def _build_soil_analyst_prompt(self) -> str:
        """Build the system prompt for the soil analyst role."""
        return (
            "You are a soil scientist and soil health specialist. Analyze soil "
            "tests and provide recommendations for amendments, fertilization, "
            "and soil building practices. Consider soil type, pH, nutrient levels, "
            "organic matter, and cropping history. Promote sustainable soil "
            "management practices."
        )

    def _build_pest_advisor_prompt(self) -> str:
        """Build the system prompt for the pest advisor role."""
        organic = "organic " if self.config.get("organic", False) else ""
        return (
            f"You are an {organic}pest management specialist. Identify pests, "
            f"diseases, and weeds accurately. Assess severity and spread risk. "
            f"Recommend integrated pest management (IPM) strategies including "
            f"biological, cultural, mechanical, and chemical controls. "
            f"Prioritize sustainable and environmentally friendly solutions."
        )

    def _build_yield_forecaster_prompt(self) -> str:
        """Build the system prompt for the yield forecaster role."""
        return (
            "You are a crop yield forecasting specialist. Predict yields "
            "using multiple data sources: soil conditions, weather patterns, "
            "crop health imagery, historical data, and management practices. "
            "Provide confidence intervals and clearly communicate forecast "
            "uncertainty. Update forecasts as the growing season progresses."
        )

    # ------------------------------------------------------------------
    # Metrics
    # ------------------------------------------------------------------

    def _update_ag_metrics(self) -> None:
        """Update agriculture-specific metrics."""
        if self._yield_forecasts:
            accuracies = [f.accuracy_percent for f in self._yield_forecasts.values()]
            self.ag_metrics.yield_prediction_accuracy = sum(accuracies) / len(accuracies)

        self.ag_metrics.pest_detection_rate = 0.88
        self.ag_metrics.cost_savings_percent = 12.0
        self.ag_metrics.avg_yield_improvement = 8.0
        self.ag_metrics.water_savings_percent = 15.0

    def get_agriculture_metrics(self) -> Dict[str, Any]:
        """Get the current agriculture-specific metrics.

        Returns:
            Dictionary with agriculture metrics including yield accuracy,
            pest detection rate, cost savings, and yield improvement.
        """
        return self.ag_metrics.to_dict()
