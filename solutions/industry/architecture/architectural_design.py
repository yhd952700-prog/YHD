"""
Architectural Design AI Solution Template.

A comprehensive AI-powered architectural design assistant for
architecture firms, design studios, and construction companies.
Includes multi-agent roles for architectural design, structural
engineering, MEP design, and sustainability consulting with
code compliance verification.

Typical workflow:
    requirements -> conceptual design -> structural analysis
    -> systems design -> sustainability optimization
"""

from typing import Optional, Dict, Any, List
from enum import Enum
from dataclasses import dataclass, field

from solutions.templates.base_solution import (
    BaseSolution,
    SolutionMetadata,
    SolutionMetrics,
)


class BuildingType(str, Enum):
    """Types of building projects."""
    RESIDENTIAL = "residential"
    COMMERCIAL = "commercial"
    OFFICE = "office"
    HOSPITALITY = "hospitality"
    EDUCATIONAL = "educational"
    HEALTHCARE = "healthcare"
    INDUSTRIAL = "industrial"
    MIXED_USE = "mixed_use"
    PUBLIC = "public"


class DesignPhase(str, Enum):
    """Phases of architectural design."""
    SCHEMATIC = "schematic"
    DESIGN_DEVELOPMENT = "design_development"
    CONSTRUCTION_DOCUMENT = "construction_document"
    BIDDING = "bidding"
    CONSTRUCTION = "construction"


class StructuralSystem(str, Enum):
    """Types of structural systems."""
    STEEL_FRAME = "steel_frame"
    CONCRETE_FRAME = "concrete_frame"
    MASONRY = "masonry"
    TIMBER_FRAME = "timber_frame"
    COMPOSITE = "composite"
    PRECAST = "precast"


class SustainabilityRating(str, Enum):
    """Sustainability certification levels."""
    NONE = "none"
    BASIC = "basic"
    SILVER = "silver"
    GOLD = "gold"
    PLATINUM = "platinum"


class ArchitectureRole(str, Enum):
    """Agent roles in the architectural design solution."""
    LEAD_ARCHITECT = "lead_architect"
    STRUCTURAL_ENGINEER = "structural_engineer"
    MEP_DESIGNER = "mep_designer"
    SUSTAINABILITY_CONSULTANT = "sustainability_consultant"


@dataclass
class ProjectRequirements:
    """Represents architectural project requirements.

    Attributes:
        project_id: Unique project identifier.
        project_name: Name of the project.
        building_type: Type of building.
        site_address: Project site location.
        total_area_sqm: Total floor area in square meters.
        number_of_floors: Number of above-ground floors.
        basement_floors: Number of basement levels.
        occupancy_type: Primary occupancy type.
        budget: Total project budget.
        design_phase: Current design phase.
        client_priorities: Client's priority areas.
        regulatory_jurisdiction: Jurisdiction for code compliance.
    """
    project_id: str
    project_name: str = ""
    building_type: BuildingType = BuildingType.COMMERCIAL
    site_address: str = ""
    total_area_sqm: float = 0.0
    number_of_floors: int = 1
    basement_floors: int = 0
    occupancy_type: str = ""
    budget: float = 0.0
    design_phase: DesignPhase = DesignPhase.SCHEMATIC
    client_priorities: List[str] = field(default_factory=list)
    regulatory_jurisdiction: str = "local"


@dataclass
class ConceptDesign:
    """Represents a conceptual design option.

    Attributes:
        id: Unique design identifier.
        project_id: Associated project ID.
        name: Design option name.
        description: Design concept description.
        architectural_style: Architectural style.
        massing_concept: Massing strategy description.
        facade_concept: Facade design concept.
        spatial_organization: Spatial layout strategy.
        circulation_strategy: Circulation design approach.
        estimated_area_sqm: Estimated total area.
        design_score: Overall design quality score (0-100).
        aesthetic_score: Aesthetic quality score (0-10).
        functionality_score: Functionality score (0-10).
    """
    id: str
    project_id: str
    name: str = ""
    description: str = ""
    architectural_style: str = "modern"
    massing_concept: str = ""
    facade_concept: str = ""
    spatial_organization: str = ""
    circulation_strategy: str = ""
    estimated_area_sqm: float = 0.0
    design_score: float = 0.0
    aesthetic_score: float = 0.0
    functionality_score: float = 0.0


@dataclass
class StructuralAnalysis:
    """Represents structural analysis results.

    Attributes:
        id: Unique analysis identifier.
        project_id: Associated project ID.
        structural_system: Recommended structural system.
        material_specification: Primary structural materials.
        column_layout: Column grid description.
        load_analysis: Load analysis summary.
        seismic_analysis: Seismic performance assessment.
        wind_analysis: Wind load assessment.
        foundation_type: Recommended foundation type.
        structural_efficiency: Efficiency score (0-1).
        code_compliant: Whether design meets structural codes.
        issues: Identified structural issues.
        recommendations: Structural recommendations.
    """
    id: str
    project_id: str
    structural_system: StructuralSystem = StructuralSystem.STEEL_FRAME
    material_specification: str = ""
    column_layout: str = ""
    load_analysis: str = ""
    seismic_analysis: str = ""
    wind_analysis: str = ""
    foundation_type: str = ""
    structural_efficiency: float = 0.0
    code_compliant: bool = True
    issues: List[str] = field(default_factory=list)
    recommendations: List[str] = field(default_factory=list)


@dataclass
class MEPSystems:
    """Represents MEP (Mechanical, Electrical, Plumbing) systems design.

    Attributes:
        id: Unique MEP design identifier.
        project_id: Associated project ID.
        hvac_system: HVAC system type and specs.
        electrical_system: Electrical system design.
        plumbing_system: Plumbing system design.
        fire_protection: Fire protection system.
        energy_estimate: Estimated energy consumption.
        hvac_capacity_kw: HVAC capacity in kW.
        electrical_capacity_kw: Electrical capacity in kW.
        system_efficiency: Overall MEP efficiency (0-1).
        coordination_notes: Coordination issues and notes.
    """
    id: str
    project_id: str
    hvac_system: str = ""
    electrical_system: str = ""
    plumbing_system: str = ""
    fire_protection: str = ""
    energy_estimate: float = 0.0
    hvac_capacity_kw: float = 0.0
    electrical_capacity_kw: float = 0.0
    system_efficiency: float = 0.0
    coordination_notes: List[str] = field(default_factory=list)


@dataclass
class SustainabilityAssessment:
    """Represents a sustainability assessment.

    Attributes:
        id: Unique assessment identifier.
        project_id: Associated project ID.
        rating_system: Certification system used (LEED, BREEAM, etc.).
        target_rating: Target certification level.
        current_score: Current sustainability score.
        energy_performance: Energy performance score (0-100).
        water_efficiency: Water efficiency score (0-100).
        materials_quality: Materials sustainability score (0-100).
        indoor_quality: Indoor environmental quality score (0-100).
        site_sustainability: Site sustainability score (0-100).
        carbon_footprint: Estimated carbon footprint (tons CO2/year).
        strategies: Sustainability strategies recommended.
        innovation_points: Potential innovation credits.
    """
    id: str
    project_id: str
    rating_system: str = "LEED"
    target_rating: SustainabilityRating = SustainabilityRating.SILVER
    current_score: float = 0.0
    energy_performance: float = 0.0
    water_efficiency: float = 0.0
    materials_quality: float = 0.0
    indoor_quality: float = 0.0
    site_sustainability: float = 0.0
    carbon_footprint: float = 0.0
    strategies: List[str] = field(default_factory=list)
    innovation_points: List[str] = field(default_factory=list)


@dataclass
class ArchitectureMetrics(SolutionMetrics):
    """Extended metrics for the architectural design solution.

    Attributes:
        design_quality: Overall design quality score (0-100).
        code_compliance: Code compliance rate.
        sustainability_score: Overall sustainability score.
        projects_designs: Total design projects processed.
        concepts_generated: Total design concepts generated.
        avg_design_time_days: Average design time in days.
        client_satisfaction: Client satisfaction score (0-5).
        structural_efficiency: Average structural efficiency.
        energy_efficiency_rating: Energy efficiency rating.
        design_iterations: Average number of design iterations.
    """
    design_quality: float = 0.0
    code_compliance: float = 0.0
    sustainability_score: float = 0.0
    projects_designs: int = 0
    concepts_generated: int = 0
    avg_design_time_days: float = 0.0
    client_satisfaction: float = 0.0
    structural_efficiency: float = 0.0
    energy_efficiency_rating: float = 0.0
    design_iterations: int = 0

    def to_dict(self) -> Dict[str, Any]:
        """Convert architecture metrics to a dictionary."""
        base = super().to_dict()
        base.update({
            "design_quality": self.design_quality,
            "code_compliance": self.code_compliance,
            "sustainability_score": self.sustainability_score,
            "projects_designs": self.projects_designs,
            "concepts_generated": self.concepts_generated,
            "avg_design_time_days": self.avg_design_time_days,
            "client_satisfaction": self.client_satisfaction,
            "structural_efficiency": self.structural_efficiency,
            "energy_efficiency_rating": self.energy_efficiency_rating,
            "design_iterations": self.design_iterations,
        })
        return base


class ArchitecturalDesignSolution(BaseSolution):
    """Architectural Design AI solution template.

    Provides a complete, pre-configured architectural design assistant
    with multi-agent collaboration for conceptual design, structural
    analysis, MEP systems design, and sustainability optimization.

    Pre-configured agent roles:
        - Lead Architect: Develops design concepts and spatial organization
        - Structural Engineer: Performs structural analysis and design
        - MEP Designer: Designs mechanical, electrical, plumbing systems
        - Sustainability Consultant: Optimizes for sustainability and energy

    Workflow:
        1. Requirements - Gather and analyze project requirements
        2. Conceptual Design - Generate design concepts and massing
        3. Structural Analysis - Analyze structural requirements
        4. Systems Design - Design MEP and building systems
        5. Sustainability Optimization - Optimize for sustainability

    Configuration options:
        - building_type: Type of building project
        - design_phase: Current design phase
        - sustainability_target: Target sustainability certification
        - structural_system_preference: Preferred structural system
        - jurisdiction: Regulatory jurisdiction
    """

    def _build_metadata(self) -> SolutionMetadata:
        """Build metadata for the architectural design solution."""
        return SolutionMetadata(
            name="architectural-design",
            version="1.0.0",
            industry="architecture",
            description=(
                "AI-powered architectural design solution with conceptual "
                "design generation, structural analysis, MEP systems design, "
                "and sustainability optimization."
            ),
            author="LiuHao AI OS",
            tags=["architecture", "design", "structural", "MEP", "sustainability", "green-building"],
            min_liuhao_version="1.0.0",
        )

    def _build_config_schema(self) -> None:
        """Define configuration fields for the architectural design solution."""
        self.config_schema.add_field(
            name="building_type",
            field_type="string",
            required=False,
            default="commercial",
            description="Type of building project",
            valid_values=[
                "residential", "commercial", "office", "hospitality",
                "educational", "healthcare", "industrial", "mixed_use", "public",
            ],
        )
        self.config_schema.add_field(
            name="design_phase",
            field_type="string",
            required=False,
            default="schematic",
            description="Current design phase",
            valid_values=[
                "schematic", "design_development", "construction_document",
                "bidding", "construction",
            ],
        )
        self.config_schema.add_field(
            name="sustainability_target",
            field_type="string",
            required=False,
            default="silver",
            description="Target sustainability certification level",
            valid_values=["none", "basic", "silver", "gold", "platinum"],
        )
        self.config_schema.add_field(
            name="sustainability_system",
            field_type="string",
            required=False,
            default="LEED",
            description="Sustainability certification system",
            valid_values=["LEED", "BREEAM", "WELL", "DGNB", "Green Star", "CASBEE"],
        )
        self.config_schema.add_field(
            name="structural_system_preference",
            field_type="string",
            required=False,
            default="steel_frame",
            description="Preferred structural system",
            valid_values=[
                "steel_frame", "concrete_frame", "masonry",
                "timber_frame", "composite", "precast",
            ],
        )
        self.config_schema.add_field(
            name="jurisdiction",
            field_type="string",
            required=False,
            default="us",
            description="Regulatory jurisdiction for building codes",
            valid_values=["us", "eu", "uk", "cn", "jp", "au", "global"],
        )
        self.config_schema.add_field(
            name="design_options_count",
            field_type="int",
            required=False,
            default=3,
            description="Number of design options to generate",
        )

    def _on_initialize(self) -> None:
        """Initialize architectural design solution state."""
        self._agents: Dict[str, Dict[str, Any]] = {}
        self._projects: Dict[str, ProjectRequirements] = {}
        self._designs: Dict[str, ConceptDesign] = {}
        self._structural_analyses: Dict[str, StructuralAnalysis] = {}
        self._mep_designs: Dict[str, MEPSystems] = {}
        self._sustainability_assessments: Dict[str, SustainabilityAssessment] = {}
        self._project_count = 0
        self._design_count = 0
        self._structural_count = 0
        self._mep_count = 0
        self._sustainability_count = 0

        # Initialize architecture-specific metrics
        self.architecture_metrics = ArchitectureMetrics()

        # Building type characteristics
        self._building_characteristics = self._build_characteristics()

    def _build_characteristics(self) -> Dict[str, Any]:
        """Build building type characteristics database."""
        return {
            "residential": {
                "typical_floors": 1,
                "area_per_floor_sqm": 150,
                "hvac_per_sqm_kw": 0.05,
                "electrical_per_sqm_kw": 0.04,
                "structural_systems": ["timber_frame", "concrete_frame", "steel_frame"],
                "design_styles": ["modern", "traditional", "contemporary", "minimalist"],
            },
            "commercial": {
                "typical_floors": 5,
                "area_per_floor_sqm": 1000,
                "hvac_per_sqm_kw": 0.08,
                "electrical_per_sqm_kw": 0.07,
                "structural_systems": ["steel_frame", "concrete_frame", "composite"],
                "design_styles": ["modern", "contemporary", "corporate", "sustainable"],
            },
            "office": {
                "typical_floors": 10,
                "area_per_floor_sqm": 1500,
                "hvac_per_sqm_kw": 0.09,
                "electrical_per_sqm_kw": 0.08,
                "structural_systems": ["steel_frame", "concrete_frame", "composite"],
                "design_styles": ["modern", "corporate", "sustainable", "biophilic"],
            },
            "hospitality": {
                "typical_floors": 8,
                "area_per_floor_sqm": 1200,
                "hvac_per_sqm_kw": 0.1,
                "electrical_per_sqm_kw": 0.09,
                "structural_systems": ["concrete_frame", "steel_frame", "composite"],
                "design_styles": ["luxury", "modern", "boutique", "resort"],
            },
            "educational": {
                "typical_floors": 3,
                "area_per_floor_sqm": 2000,
                "hvac_per_sqm_kw": 0.07,
                "electrical_per_sqm_kw": 0.06,
                "structural_systems": ["concrete_frame", "steel_frame", "masonry"],
                "design_styles": ["modern", "institutional", "sustainable", "collaborative"],
            },
            "healthcare": {
                "typical_floors": 6,
                "area_per_floor_sqm": 2500,
                "hvac_per_sqm_kw": 0.12,
                "electrical_per_sqm_kw": 0.1,
                "structural_systems": ["concrete_frame", "steel_frame", "composite"],
                "design_styles": ["modern", "institutional", "evidence-based", "healing"],
            },
            "industrial": {
                "typical_floors": 1,
                "area_per_floor_sqm": 5000,
                "hvac_per_sqm_kw": 0.06,
                "electrical_per_sqm_kw": 0.08,
                "structural_systems": ["steel_frame", "precast", "concrete_frame"],
                "design_styles": ["industrial", "functional", "logistics-optimized"],
            },
        }

    def _on_deploy(self) -> None:
        """Deploy architectural design agents and workflows."""
        agent_configs = [
            (ArchitectureRole.LEAD_ARCHITECT, {
                "name": "Lead Architect",
                "description": "Develops design concepts and spatial organization",
                "system_prompt": self._build_lead_architect_prompt(),
                "tools": ["concept_design", "massing_study", "spatial_planning"],
            }),
            (ArchitectureRole.STRUCTURAL_ENGINEER, {
                "name": "Structural Engineer",
                "description": "Performs structural analysis and design",
                "system_prompt": self._build_structural_engineer_prompt(),
                "tools": ["structural_analysis", "code_check", "material_optimization"],
            }),
            (ArchitectureRole.MEP_DESIGNER, {
                "name": "MEP Designer",
                "description": "Designs mechanical, electrical, plumbing systems",
                "system_prompt": self._build_mep_designer_prompt(),
                "tools": ["hvac_design", "electrical_design", "plumbing_design"],
            }),
            (ArchitectureRole.SUSTAINABILITY_CONSULTANT, {
                "name": "Sustainability Consultant",
                "description": "Optimizes for sustainability and energy efficiency",
                "system_prompt": self._build_sustainability_consultant_prompt(),
                "tools": ["energy_modeling", "life_cycle_analysis", "green_certification"],
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
        """Process an architectural design request through the full workflow.

        Args:
            input_data: Dictionary containing project requirements with at least
                ``project_name`` and basic project parameters.

        Returns:
            Dictionary with design results including concepts, structural
            analysis, MEP design, and sustainability assessment.
        """
        # Step 1: Requirements gathering
        project = self._gather_requirements(input_data)

        # Step 2: Conceptual design
        design_concepts = self._generate_concepts(project)

        # Step 3: Structural analysis
        structural = self._perform_structural_analysis(project, design_concepts[0])

        # Step 4: MEP systems design
        mep = self._design_mep_systems(project, design_concepts[0])

        # Step 5: Sustainability optimization
        sustainability = self._assess_sustainability(project, design_concepts[0], mep)

        # Update metrics
        self.architecture_metrics.projects_designs += 1
        self.architecture_metrics.concepts_generated += len(design_concepts)
        self._update_architecture_metrics()

        return {
            "project": {
                "id": project.project_id,
                "name": project.project_name,
                "building_type": project.building_type.value,
                "total_area_sqm": project.total_area_sqm,
                "floors": project.number_of_floors,
                "budget": project.budget,
                "design_phase": project.design_phase.value,
            },
            "design_concepts": [
                {
                    "id": d.id,
                    "name": d.name,
                    "style": d.architectural_style,
                    "description": d.description,
                    "design_score": d.design_score,
                    "aesthetic_score": d.aesthetic_score,
                    "functionality_score": d.functionality_score,
                }
                for d in design_concepts
            ],
            "structural": {
                "id": structural.id,
                "system": structural.structural_system.value,
                "materials": structural.material_specification,
                "efficiency": structural.structural_efficiency,
                "code_compliant": structural.code_compliant,
                "issues": structural.issues,
                "recommendations": structural.recommendations,
            },
            "mep": {
                "id": mep.id,
                "hvac": mep.hvac_system,
                "electrical": mep.electrical_system,
                "hvac_capacity_kw": mep.hvac_capacity_kw,
                "electrical_capacity_kw": mep.electrical_capacity_kw,
                "system_efficiency": mep.system_efficiency,
                "coordination_notes": mep.coordination_notes,
            },
            "sustainability": {
                "id": sustainability.id,
                "rating_system": sustainability.rating_system,
                "target_rating": sustainability.target_rating.value,
                "current_score": sustainability.current_score,
                "energy_performance": sustainability.energy_performance,
                "water_efficiency": sustainability.water_efficiency,
                "carbon_footprint": sustainability.carbon_footprint,
                "strategies": sustainability.strategies,
            },
        }

    def _on_teardown(self) -> None:
        """Clean up architectural design solution resources."""
        self._agents.clear()
        self._projects.clear()
        self._designs.clear()
        self._structural_analyses.clear()
        self._mep_designs.clear()
        self._sustainability_assessments.clear()
        self._project_count = 0
        self._design_count = 0
        self._structural_count = 0
        self._mep_count = 0
        self._sustainability_count = 0

    # ------------------------------------------------------------------
    # Workflow steps
    # ------------------------------------------------------------------

    def _gather_requirements(self, data: Dict[str, Any]) -> ProjectRequirements:
        """Step 1: Gather and analyze project requirements."""
        self._project_count += 1
        project_id = f"proj-{self._project_count}"

        try:
            btype = BuildingType(data.get("building_type", self.config.get("building_type", "commercial")))
        except ValueError:
            btype = BuildingType.COMMERCIAL

        try:
            phase = DesignPhase(data.get("design_phase", self.config.get("design_phase", "schematic")))
        except ValueError:
            phase = DesignPhase.SCHEMATIC

        # Calculate default area if not provided
        chars = self._building_characteristics.get(btype.value, self._building_characteristics["commercial"])
        default_area = chars["typical_floors"] * chars["area_per_floor_sqm"]

        project = ProjectRequirements(
            project_id=project_id,
            project_name=data.get("project_name", f"Project {self._project_count}"),
            building_type=btype,
            site_address=data.get("site_address", ""),
            total_area_sqm=data.get("total_area_sqm", default_area),
            number_of_floors=data.get("number_of_floors", chars["typical_floors"]),
            basement_floors=data.get("basement_floors", 0),
            occupancy_type=data.get("occupancy_type", ""),
            budget=data.get("budget", default_area * 2000),  # Rough $2000/sqm
            design_phase=phase,
            client_priorities=data.get("client_priorities", []),
            regulatory_jurisdiction=self.config.get("jurisdiction", "us"),
        )
        self._projects[project_id] = project
        return project

    def _generate_concepts(
        self, project: ProjectRequirements
    ) -> List[ConceptDesign]:
        """Step 2: Generate design concepts."""
        num_options = self.config.get("design_options_count", 3)
        chars = self._building_characteristics.get(
            project.building_type.value,
            self._building_characteristics["commercial"],
        )
        styles = chars["design_styles"]
        concepts = []

        concept_variations = [
            {
                "name": "Option A - Modern Minimalist",
                "style": "modern",
                "massing": "Clean rectangular volume with strategic setbacks",
                "facade": "Floor-to-ceiling glazing with vertical fins",
                "spatial": "Open floor plans with flexible zoning",
                "circulation": "Central core with perimeter circulation",
                "aesthetic": 8.5,
                "functionality": 8.0,
            },
            {
                "name": "Option B - Sustainable Biophilic",
                "style": "sustainable",
                "massing": "Stepped terraces with green roofs and gardens",
                "facade": "Living walls and operable windows with sun shading",
                "spatial": "Biophilic design with natural light and plantings",
                "circulation": "Daylit corridors with garden views",
                "aesthetic": 8.0,
                "functionality": 8.5,
            },
            {
                "name": "Option C - Bold Contemporary",
                "style": "contemporary",
                "massing": "Dynamic volumes with cantilevers and double-height spaces",
                "facade": "Mixed materials creating visual rhythm and depth",
                "spatial": "Dramatic atriums and interconnected spaces",
                "circulation": "Feature staircases and experiential paths",
                "aesthetic": 9.0,
                "functionality": 7.5,
            },
        ]

        for i in range(min(num_options, len(concept_variations))):
            self._design_count += 1
            design_id = f"design-{self._design_count}"
            var = concept_variations[i]

            # Adjust scores based on client priorities
            priorities = [p.lower() for p in project.client_priorities]
            aesthetic_adj = 0
            functional_adj = 0
            if "aesthetics" in priorities or "design" in priorities:
                aesthetic_adj = 0.5
            if "functionality" in priorities or "efficiency" in priorities:
                functional_adj = 0.5
            if "sustainability" in priorities:
                if i == 1:  # Sustainable option gets boost
                    aesthetic_adj += 0.5
                    functional_adj += 0.5

            aesthetic = min(10.0, var["aesthetic"] + aesthetic_adj)
            functionality = min(10.0, var["functionality"] + functional_adj)
            design_score = (aesthetic * 0.4 + functionality * 0.6) * 10

            concept = ConceptDesign(
                id=design_id,
                project_id=project.project_id,
                name=var["name"],
                description=(
                    f"A {var['style']} design approach featuring "
                    f"{var['massing'].lower()}. The design emphasizes "
                    f"{var['spatial'].lower()} with {var['facade'].lower()}."
                ),
                architectural_style=var["style"],
                massing_concept=var["massing"],
                facade_concept=var["facade"],
                spatial_organization=var["spatial"],
                circulation_strategy=var["circulation"],
                estimated_area_sqm=project.total_area_sqm,
                design_score=round(design_score, 1),
                aesthetic_score=round(aesthetic, 1),
                functionality_score=round(functionality, 1),
            )
            concepts.append(concept)
            self._designs[design_id] = concept

        return concepts

    def _perform_structural_analysis(
        self, project: ProjectRequirements, design: ConceptDesign
    ) -> StructuralAnalysis:
        """Step 3: Perform structural analysis."""
        self._structural_count += 1
        struct_id = f"struct-{self._structural_count}"

        # Determine appropriate structural system
        pref_system = self.config.get("structural_system_preference", "steel_frame")
        chars = self._building_characteristics.get(
            project.building_type.value,
            self._building_characteristics["commercial"],
        )

        # Check if preferred system is suitable
        if pref_system in chars["structural_systems"]:
            system = StructuralSystem(pref_system)
        else:
            system = StructuralSystem(chars["structural_systems"][0])

        # Height-based adjustments
        height_factor = min(1.0, project.number_of_floors / 20)
        efficiency = 0.7 + (1 - height_factor) * 0.2  # Shorter = more efficient

        # Code compliance
        code_compliant = True
        issues = []
        recommendations = []

        if project.number_of_floors > 20:
            issues.append("High-rise requires specialized structural analysis")
            recommendations.append("Consider wind tunnel testing")
            efficiency -= 0.1

        if project.basement_floors > 2:
            issues.append("Deep basement requires additional geotechnical analysis")
            recommendations.append("Recommend detailed geotechnical investigation")

        if project.total_area_sqm > 50000:
            recommendations.append("Consider modular construction for large footprint")

        # Foundation recommendation
        if project.number_of_floors > 10:
            foundation = "Pile foundation with pile cap"
        elif project.basement_floors > 0:
            foundation = "Raft/mat foundation"
        else:
            foundation = "Spread footings"

        materials = {
            StructuralSystem.STEEL_FRAME: "Structural steel (ASTM A992) with composite deck",
            StructuralSystem.CONCRETE_FRAME: "Reinforced concrete (4000 psi) with flat slabs",
            StructuralSystem.MASONRY: "Reinforced masonry with concrete bond beams",
            StructuralSystem.TIMBER_FRAME: "Glulam and CLT mass timber structure",
            StructuralSystem.COMPOSITE: "Steel-concrete composite structure",
            StructuralSystem.PRECAST: "Precast concrete panels and beams",
        }

        analysis = StructuralAnalysis(
            id=struct_id,
            project_id=project.project_id,
            structural_system=system,
            material_specification=materials[system],
            column_layout=f"Regular grid pattern, approximately {8 + (project.number_of_floors // 10) * 2}m x {8 + (project.number_of_floors // 10) * 2}m",
            load_analysis=f"Dead load: ~5 kPa, Live load: 2.4-4.8 kPa depending on occupancy",
            seismic_analysis="Seismic design per applicable code (risk category determined by occupancy)",
            wind_analysis=f"Wind loads calculated per local code; building height: {project.number_of_floors * 3.5}m",
            foundation_type=foundation,
            structural_efficiency=round(max(0.3, efficiency), 2),
            code_compliant=code_compliant,
            issues=issues,
            recommendations=recommendations,
        )
        self._structural_analyses[struct_id] = analysis
        return analysis

    def _design_mep_systems(
        self, project: ProjectRequirements, design: ConceptDesign
    ) -> MEPSystems:
        """Step 4: Design MEP systems."""
        self._mep_count += 1
        mep_id = f"mep-{self._mep_count}"

        chars = self._building_characteristics.get(
            project.building_type.value,
            self._building_characteristics["commercial"],
        )

        area = project.total_area_sqm
        hvac_kw = area * chars["hvac_per_sqm_kw"]
        electrical_kw = area * chars["electrical_per_sqm_kw"]

        # HVAC system selection based on building type
        hvac_systems = {
            "residential": "Split system with heat pumps",
            "commercial": "VAV (Variable Air Volume) with central plant",
            "office": "VAV with dedicated outdoor air system",
            "hospitality": "Fan coil units with central chiller/boiler",
            "educational": "VAV with energy recovery ventilation",
            "healthcare": "100% outside air system with filtration",
            "industrial": "Make-up air units with exhaust systems",
        }

        # Electrical system
        electrical_systems = {
            "residential": "Single-phase, 200A service",
            "commercial": "Three-phase, 480V service with step-down transformers",
            "office": "Three-phase with UPS for critical systems",
            "hospitality": "Three-phase with emergency backup generator",
            "educational": "Three-phase distribution with smart lighting",
            "healthcare": "Three-phase with redundant UPS and emergency power",
            "industrial": "Three-phase 480V with motor control centers",
        }

        btype = project.building_type.value

        # System efficiency based on sustainability target
        target = self.config.get("sustainability_target", "silver")
        efficiency_map = {
            "none": 0.6,
            "basic": 0.7,
            "silver": 0.78,
            "gold": 0.85,
            "platinum": 0.92,
        }
        efficiency = efficiency_map.get(target, 0.78)

        # Adjust for efficiency (higher efficiency = lower capacity needed)
        hvac_kw = hvac_kw * (1.0 / efficiency) * 0.8  # 0.8 is base efficiency
        electrical_kw = electrical_kw * (1.0 / efficiency) * 0.85

        mep = MEPSystems(
            id=mep_id,
            project_id=project.project_id,
            hvac_system=hvac_systems.get(btype, "VAV system"),
            electrical_system=electrical_systems.get(btype, "Three-phase service"),
            plumbing_system=(
                "Domestic cold/hot water, sanitary drainage, "
                "stormwater management, gas piping"
            ),
            fire_protection=(
                "Automatic sprinkler system per NFPA 13, "
                "fire alarm system, standpipe system for buildings over 30m"
            ),
            energy_estimate=round((hvac_kw + electrical_kw) * 8 * 250 / 1000, 0),  # MWh/year
            hvac_capacity_kw=round(hvac_kw, 1),
            electrical_capacity_kw=round(electrical_kw, 1),
            system_efficiency=efficiency,
            coordination_notes=[
                "MEP coordination required in ceiling plenum spaces",
                "Structural penetrations coordinated at design development",
                "BIM coordination meetings scheduled at key milestones",
            ],
        )
        self._mep_designs[mep_id] = mep
        return mep

    def _assess_sustainability(
        self,
        project: ProjectRequirements,
        design: ConceptDesign,
        mep: MEPSystems,
    ) -> SustainabilityAssessment:
        """Step 5: Assess and optimize sustainability."""
        self._sustainability_count += 1
        sust_id = f"sust-{self._sustainability_count}"

        target = SustainabilityRating(self.config.get("sustainability_target", "silver"))
        system = self.config.get("sustainability_system", "LEED")

        # Calculate scores
        base_scores = {
            "none": 20,
            "basic": 35,
            "silver": 50,
            "gold": 70,
            "platinum": 90,
        }
        base = base_scores.get(target.value, 50)

        # Energy performance
        energy_score = base + (mep.system_efficiency - 0.7) * 100
        energy_score = min(100.0, max(0.0, energy_score))

        # Water efficiency
        water_score = base * 0.8 + 10  # Slightly lower baseline
        water_score = min(100.0, water_score)

        # Materials
        materials_score = base * 0.7 + 15
        if "sustainable" in design.architectural_style.lower():
            materials_score += 10
        materials_score = min(100.0, materials_score)

        # Indoor quality
        indoor_score = base * 0.8 + 10
        indoor_score = min(100.0, indoor_score)

        # Site
        site_score = base * 0.75 + 10
        site_score = min(100.0, site_score)

        # Overall score (weighted)
        overall_score = (
            energy_score * 0.30
            + water_score * 0.15
            + materials_score * 0.20
            + indoor_score * 0.20
            + site_score * 0.15
        )

        # Strategies
        strategies = [
            "High-efficiency HVAC with energy recovery ventilation",
            "LED lighting with occupancy and daylight controls",
            "Low-flow plumbing fixtures (40% water reduction target)",
            "Cool roof / green roof for heat island reduction",
            "Sustainable materials with recycled content",
            "Improved indoor air quality with enhanced filtration",
            "Solar-ready roof design for future PV installation",
        ]

        if target in (SustainabilityRating.GOLD, SustainabilityRating.PLATINUM):
            strategies.extend([
                "On-site renewable energy generation",
                "Rainwater harvesting system",
                "Net-zero energy design approach",
                "Biophilic design elements",
            ])

        # Innovation points
        innovation = [
            "Smart building management system with real-time optimization",
            "Integrated daylight and occupancy control system",
        ]

        # Carbon footprint estimate (tons CO2/year)
        carbon = mep.energy_estimate * 0.4  # ~0.4 tons CO2 per MWh

        assessment = SustainabilityAssessment(
            id=sust_id,
            project_id=project.project_id,
            rating_system=system,
            target_rating=target,
            current_score=round(overall_score, 1),
            energy_performance=round(energy_score, 1),
            water_efficiency=round(water_score, 1),
            materials_quality=round(materials_score, 1),
            indoor_quality=round(indoor_score, 1),
            site_sustainability=round(site_score, 1),
            carbon_footprint=round(carbon, 1),
            strategies=strategies,
            innovation_points=innovation,
        )
        self._sustainability_assessments[sust_id] = assessment
        return assessment

    # ------------------------------------------------------------------
    # Agent prompts
    # ------------------------------------------------------------------

    def _build_lead_architect_prompt(self) -> str:
        """Build the system prompt for the lead architect role."""
        btype = self.config.get("building_type", "commercial")
        return (
            f"You are a lead architect specializing in {btype} buildings. "
            "Create innovative, functional, and aesthetically compelling "
            "design concepts that meet client requirements while respecting "
            "site constraints, budget parameters, and regulatory requirements. "
            "Consider spatial flow, natural light, material expression, and "
            "user experience. Produce designs that are buildable, sustainable, "
            "and timeless."
        )

    def _build_structural_engineer_prompt(self) -> str:
        """Build the system prompt for the structural engineer role."""
        jurisdiction = self.config.get("jurisdiction", "us")
        return (
            f"You are a licensed structural engineer practicing under "
            f"{jurisdiction} building codes. Analyze architectural designs "
            "and develop efficient, safe, and economical structural systems. "
            "Ensure compliance with all applicable codes including seismic, "
            "wind, and gravity load requirements. Optimize structural "
            "systems for material efficiency and constructability while "
            "supporting the architectural vision."
        )

    def _build_mep_designer_prompt(self) -> str:
        """Build the system prompt for the MEP designer role."""
        return (
            "You are an experienced MEP (Mechanical, Electrical, Plumbing) "
            "designer. Design efficient, reliable, and cost-effective "
            "building systems that meet project requirements for comfort, "
            "safety, and energy performance. Coordinate with architectural "
            "and structural teams to ensure systems integrate seamlessly. "
            "Prioritize energy efficiency, maintainability, and user comfort."
        )

    def _build_sustainability_consultant_prompt(self) -> str:
        """Build the system prompt for the sustainability consultant role."""
        system = self.config.get("sustainability_system", "LEED")
        target = self.config.get("sustainability_target", "silver")
        return (
            f"You are a {system} accredited sustainability consultant "
            f"targeting {target} certification. Integrate sustainable design "
            "strategies across all building systems - energy, water, materials, "
            "indoor quality, and site. Identify cost-effective measures that "
            "maximize environmental benefit. Think holistically about life "
            "cycle impacts and long-term building performance."
        )

    # ------------------------------------------------------------------
    # Metrics
    # ------------------------------------------------------------------

    def _update_architecture_metrics(self) -> None:
        """Update architecture-specific metrics."""
        if self._designs:
            total = len(self._designs)
            avg_design = sum(d.design_score for d in self._designs.values()) / total
            self.architecture_metrics.design_quality = avg_design

        if self._structural_analyses:
            total = len(self._structural_analyses)
            avg_eff = sum(
                s.structural_efficiency for s in self._structural_analyses.values()
            ) / total
            self.architecture_metrics.structural_efficiency = avg_eff

            compliant = sum(
                1 for s in self._structural_analyses.values() if s.code_compliant
            )
            self.architecture_metrics.code_compliance = (
                compliant / total if total > 0 else 1.0
            )

        if self._sustainability_assessments:
            total = len(self._sustainability_assessments)
            avg_sust = sum(
                s.current_score for s in self._sustainability_assessments.values()
            ) / total
            self.architecture_metrics.sustainability_score = avg_sust

            avg_energy = sum(
                s.energy_performance for s in self._sustainability_assessments.values()
            ) / total
            self.architecture_metrics.energy_efficiency_rating = avg_energy

    def get_architecture_metrics(self) -> Dict[str, Any]:
        """Get the current architectural design metrics.

        Returns:
            Dictionary with architecture metrics including design quality,
            code compliance, sustainability scores, and efficiency ratings.
        """
        return self.architecture_metrics.to_dict()
