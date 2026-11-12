"""
Pharmaceutical Drug Research AI Solution Template.

A comprehensive AI-powered pharmaceutical research assistant for
biotech companies, pharmaceutical firms, and research institutions.
Includes multi-agent roles for research, data analysis, literature
review, and regulatory compliance with accelerated discovery workflows.

Typical workflow:
    target identification -> literature review -> hypothesis
    -> experiment design -> data analysis -> regulatory review
"""

from typing import Optional, Dict, Any, List
from enum import Enum
from dataclasses import dataclass, field

from solutions.templates.base_solution import (
    BaseSolution,
    SolutionMetadata,
    SolutionMetrics,
)


class ResearchPhase(str, Enum):
    """Phases of pharmaceutical research."""
    TARGET_DISCOVERY = "target_discovery"
    HIT_DISCOVERY = "hit_discovery"
    LEAD_OPTIMIZATION = "lead_optimization"
    PRECLINICAL = "preclinical"
    CLINICAL_PHASE1 = "clinical_phase1"
    CLINICAL_PHASE2 = "clinical_phase2"
    CLINICAL_PHASE3 = "clinical_phase3"
    REGULATORY = "regulatory"


class EvidenceLevel(str, Enum):
    """Levels of scientific evidence."""
    IN_SILICO = "in_silico"
    IN_VITRO = "in_vitro"
    IN_VIVO = "in_vivo"
    CLINICAL = "clinical"
    SYSTEMATIC_REVIEW = "systematic_review"
    META_ANALYSIS = "meta_analysis"


class ExperimentType(str, Enum):
    """Types of research experiments."""
    COMPUTATIONAL = "computational"
    IN_VITRO = "in_vitro"
    IN_VIVO = "in_vivo"
    CLINICAL = "clinical"
    BIOINFORMATICS = "bioinformatics"
    STRUCTURAL = "structural"


class PharmaRole(str, Enum):
    """Agent roles in the pharmaceutical research solution."""
    RESEARCH_SCIENTIST = "research_scientist"
    DATA_ANALYST = "data_analyst"
    LITERATURE_REVIEWER = "literature_reviewer"
    REGULATORY_SPECIALIST = "regulatory_specialist"


@dataclass
class ResearchTarget:
    """Represents a drug target or research focus.

    Attributes:
        id: Unique target identifier.
        name: Target name (gene, protein, pathway).
        target_type: Type of target (protein, gene, pathway, etc.).
        disease: Associated disease or condition.
        description: Detailed description of the target.
        known_modulators: Known molecules that modulate this target.
        validation_status: Level of target validation.
        druggability_score: Estimated druggability (0-1).
        therapeutic_area: Therapeutic area category.
    """
    id: str
    name: str = ""
    target_type: str = "protein"
    disease: str = ""
    description: str = ""
    known_modulators: List[str] = field(default_factory=list)
    validation_status: str = "putative"
    druggability_score: float = 0.5
    therapeutic_area: str = ""


@dataclass
class LiteratureFinding:
    """Represents a literature review finding.

    Attributes:
        id: Unique finding identifier.
        title: Title of the paper/source.
        authors: List of authors.
        journal: Journal name.
        year: Publication year.
        doi: Digital Object Identifier.
        evidence_level: Level of evidence.
        key_findings: Key findings from the publication.
        relevance_score: Relevance to current research (0-1).
        methodology: Research methodology used.
        limitations: Study limitations.
    """
    id: str
    title: str = ""
    authors: List[str] = field(default_factory=list)
    journal: str = ""
    year: int = 0
    doi: str = ""
    evidence_level: EvidenceLevel = EvidenceLevel.IN_VITRO
    key_findings: List[str] = field(default_factory=list)
    relevance_score: float = 0.0
    methodology: str = ""
    limitations: List[str] = field(default_factory=list)


@dataclass
class ResearchHypothesis:
    """Represents a research hypothesis.

    Attributes:
        id: Unique hypothesis identifier.
        statement: Hypothesis statement.
        target: Research target.
        rationale: Scientific rationale.
        supporting_evidence: Evidence supporting the hypothesis.
        contradicting_evidence: Evidence against the hypothesis.
        confidence: Confidence level (0-1).
        testable: Whether the hypothesis is readily testable.
        novelty_score: Estimated novelty (0-1).
    """
    id: str
    statement: str = ""
    target: str = ""
    rationale: str = ""
    supporting_evidence: List[str] = field(default_factory=list)
    contradicting_evidence: List[str] = field(default_factory=list)
    confidence: float = 0.5
    testable: bool = True
    novelty_score: float = 0.5


@dataclass
class ExperimentDesign:
    """Represents an experiment design.

    Attributes:
        id: Unique experiment identifier.
        name: Experiment name.
        type: Type of experiment.
        hypothesis_id: Associated hypothesis ID.
        objective: Primary objective.
        methodology: Experimental methodology.
        variables: Variables to test (independent, dependent, control).
        sample_size: Required sample size.
        duration_weeks: Estimated duration in weeks.
        materials_required: List of required materials.
        expected_outcome: Expected outcome.
        success_criteria: Criteria for success.
        risk_level: Risk level of the experiment.
    """
    id: str
    name: str = ""
    type: ExperimentType = ExperimentType.COMPUTATIONAL
    hypothesis_id: str = ""
    objective: str = ""
    methodology: str = ""
    variables: Dict[str, List[str]] = field(default_factory=dict)
    sample_size: int = 0
    duration_weeks: float = 0.0
    materials_required: List[str] = field(default_factory=list)
    expected_outcome: str = ""
    success_criteria: List[str] = field(default_factory=list)
    risk_level: str = "medium"


@dataclass
class DataAnalysisResult:
    """Represents results of data analysis.

    Attributes:
        id: Unique analysis result identifier.
        experiment_id: Associated experiment ID.
        summary: Analysis summary.
        key_findings: Key findings from the analysis.
        statistical_significance: Whether results are statistically significant.
        p_value: Primary p-value if applicable.
        effect_size: Effect size measurement.
        confidence_interval: Confidence interval range.
        visualizations: Types of visualizations generated.
        conclusions: Analysis conclusions.
        follow_up_recommendations: Recommended follow-up analyses.
    """
    id: str
    experiment_id: str = ""
    summary: str = ""
    key_findings: List[str] = field(default_factory=list)
    statistical_significance: bool = False
    p_value: float = 0.0
    effect_size: float = 0.0
    confidence_interval: str = ""
    visualizations: List[str] = field(default_factory=list)
    conclusions: str = ""
    follow_up_recommendations: List[str] = field(default_factory=list)


@dataclass
class PharmaMetrics(SolutionMetrics):
    """Extended metrics for the pharmaceutical research solution.

    Attributes:
        research_quality: Overall research quality score (0-5).
        novel_findings: Number of novel discoveries.
        literature_coverage: Percentage of relevant literature covered.
        targets_evaluated: Total targets evaluated.
        hypotheses_generated: Total hypotheses generated.
        experiments_designed: Total experiments designed.
        avg_hypothesis_confidence: Average hypothesis confidence.
        regulatory_readiness: Regulatory preparedness score.
        literature_review_count: Number of papers reviewed.
    """
    research_quality: float = 0.0
    novel_findings: int = 0
    literature_coverage: float = 0.0
    targets_evaluated: int = 0
    hypotheses_generated: int = 0
    experiments_designed: int = 0
    avg_hypothesis_confidence: float = 0.0
    regulatory_readiness: float = 0.0
    literature_review_count: int = 0

    def to_dict(self) -> Dict[str, Any]:
        """Convert pharma metrics to a dictionary."""
        base = super().to_dict()
        base.update({
            "research_quality": self.research_quality,
            "novel_findings": self.novel_findings,
            "literature_coverage": self.literature_coverage,
            "targets_evaluated": self.targets_evaluated,
            "hypotheses_generated": self.hypotheses_generated,
            "experiments_designed": self.experiments_designed,
            "avg_hypothesis_confidence": self.avg_hypothesis_confidence,
            "regulatory_readiness": self.regulatory_readiness,
            "literature_review_count": self.literature_review_count,
        })
        return base


class DrugResearchSolution(BaseSolution):
    """Pharmaceutical Drug Research AI solution template.

    Provides a complete, pre-configured pharmaceutical research
    assistant with multi-agent collaboration for target identification,
    literature review, hypothesis generation, experiment design,
    data analysis, and regulatory review.

    Pre-configured agent roles:
        - Research Scientist: Designs research approaches and interprets results
        - Data Analyst: Performs statistical analysis and data interpretation
        - Literature Reviewer: Conducts comprehensive literature reviews
        - Regulatory Specialist: Ensures compliance with regulatory requirements

    Workflow:
        1. Target Identification - Identify and validate drug targets
        2. Literature Review - Survey existing research landscape
        3. Hypothesis Generation - Formulate testable hypotheses
        4. Experiment Design - Design experiments to test hypotheses
        5. Data Analysis - Analyze experimental results
        6. Regulatory Review - Assess regulatory requirements

    Configuration options:
        - therapeutic_area: Primary therapeutic area of focus
        - research_phase: Current research phase
        - regulatory_region: Primary regulatory region
        - literature_depth: Depth of literature review
        - novelty_preference: Preference for novel vs established targets
    """

    def _build_metadata(self) -> SolutionMetadata:
        """Build metadata for the drug research solution."""
        return SolutionMetadata(
            name="drug-research",
            version="1.0.0",
            industry="pharmaceutical",
            description=(
                "AI-powered pharmaceutical research assistant with target "
                "identification, literature review, hypothesis generation, "
                "experiment design, and regulatory compliance support."
            ),
            author="LiuHao AI OS",
            tags=["pharmaceutical", "drug-discovery", "research", "bioinformatics", "regulatory"],
            min_liuhao_version="1.0.0",
        )

    def _build_config_schema(self) -> None:
        """Define configuration fields for the drug research solution."""
        self.config_schema.add_field(
            name="therapeutic_area",
            field_type="string",
            required=False,
            default="oncology",
            description="Primary therapeutic area of research focus",
            valid_values=[
                "oncology", "cardiology", "neurology", "immunology",
                "infectious_disease", "metabolic", "respiratory",
                "gastroenterology", "dermatology", "ophthalmology", "rare_disease",
            ],
        )
        self.config_schema.add_field(
            name="research_phase",
            field_type="string",
            required=False,
            default="target_discovery",
            description="Current phase of research",
            valid_values=[
                "target_discovery", "hit_discovery", "lead_optimization",
                "preclinical", "clinical_phase1", "clinical_phase2",
                "clinical_phase3", "regulatory",
            ],
        )
        self.config_schema.add_field(
            name="regulatory_region",
            field_type="string",
            required=False,
            default="us",
            description="Primary regulatory region for compliance",
            valid_values=["us", "eu", "jp", "cn", "global"],
        )
        self.config_schema.add_field(
            name="literature_depth",
            field_type="string",
            required=False,
            default="comprehensive",
            description="Depth of literature review",
            valid_values=["basic", "standard", "comprehensive", "exhaustive"],
        )
        self.config_schema.add_field(
            name="novelty_preference",
            field_type="float",
            required=False,
            default=0.5,
            description="Preference for novel vs established targets (0-1)",
        )
        self.config_schema.add_field(
            name="max_hypotheses",
            field_type="int",
            required=False,
            default=5,
            description="Maximum number of hypotheses to generate",
        )
        self.config_schema.add_field(
            name="statistical_threshold",
            field_type="float",
            required=False,
            default=0.05,
            description="P-value threshold for statistical significance",
        )

    def _on_initialize(self) -> None:
        """Initialize pharmaceutical research solution state."""
        self._agents: Dict[str, Dict[str, Any]] = {}
        self._targets: Dict[str, ResearchTarget] = {}
        self._literature: Dict[str, LiteratureFinding] = {}
        self._hypotheses: Dict[str, ResearchHypothesis] = {}
        self._experiments: Dict[str, ExperimentDesign] = {}
        self._analyses: Dict[str, DataAnalysisResult] = {}
        self._target_count = 0
        self._literature_count = 0
        self._hypothesis_count = 0
        self._experiment_count = 0
        self._analysis_count = 0

        # Initialize pharma-specific metrics
        self.pharma_metrics = PharmaMetrics()

        # Target knowledge base
        self._target_database = self._build_target_database()

    def _build_target_database(self) -> Dict[str, Any]:
        """Build a knowledge base of known drug targets."""
        return {
            "oncology": [
                {"name": "EGFR", "type": "kinase", "druggability": 0.9, "validation": "clinical"},
                {"name": "KRAS", "type": "GTPase", "druggability": 0.6, "validation": "clinical"},
                {"name": "PD-1", "type": "immune_checkpoint", "druggability": 0.85, "validation": "clinical"},
                {"name": "BRCA1/2", "type": "dna_repair", "druggability": 0.7, "validation": "clinical"},
                {"name": "PI3K", "type": "kinase", "druggability": 0.8, "validation": "clinical"},
                {"name": "MYC", "type": "transcription_factor", "druggability": 0.3, "validation": "preclinical"},
            ],
            "cardiology": [
                {"name": "ACE", "type": "enzyme", "druggability": 0.95, "validation": "clinical"},
                {"name": "HMG-CoA reductase", "type": "enzyme", "druggability": 0.9, "validation": "clinical"},
                {"name": "PCSK9", "type": "protease", "druggability": 0.7, "validation": "clinical"},
                {"name": "SGLT2", "type": "transporter", "druggability": 0.8, "validation": "clinical"},
            ],
            "neurology": [
                {"name": "BACE1", "type": "protease", "druggability": 0.7, "validation": "clinical"},
                {"name": "tau protein", "type": "structural", "druggability": 0.4, "validation": "preclinical"},
                {"name": "NMDA receptor", "type": "ion_channel", "druggability": 0.75, "validation": "clinical"},
                {"name": "dopamine D2 receptor", "type": "gpcr", "druggability": 0.9, "validation": "clinical"},
            ],
            "immunology": [
                {"name": "TNF-alpha", "type": "cytokine", "druggability": 0.85, "validation": "clinical"},
                {"name": "IL-6", "type": "cytokine", "druggability": 0.8, "validation": "clinical"},
                {"name": "JAK", "type": "kinase", "druggability": 0.75, "validation": "clinical"},
                {"name": "CTLA-4", "type": "immune_checkpoint", "druggability": 0.7, "validation": "clinical"},
            ],
        }

    def _on_deploy(self) -> None:
        """Deploy pharmaceutical research agents and workflows."""
        agent_configs = [
            (PharmaRole.RESEARCH_SCIENTIST, {
                "name": "Research Scientist",
                "description": "Designs research approaches and interprets results",
                "system_prompt": self._build_research_scientist_prompt(),
                "tools": ["target_discovery", "hypothesis_generation", "experiment_design"],
            }),
            (PharmaRole.DATA_ANALYST, {
                "name": "Data Analyst",
                "description": "Performs statistical analysis and data interpretation",
                "system_prompt": self._build_data_analyst_prompt(),
                "tools": ["statistical_analysis", "bioinformatics", "visualization"],
            }),
            (PharmaRole.LITERATURE_REVIEWER, {
                "name": "Literature Reviewer",
                "description": "Conducts comprehensive literature reviews",
                "system_prompt": self._build_literature_reviewer_prompt(),
                "tools": ["pubmed_search", "citation_analysis", "systematic_review"],
            }),
            (PharmaRole.REGULATORY_SPECIALIST, {
                "name": "Regulatory Specialist",
                "description": "Ensures compliance with regulatory requirements",
                "system_prompt": self._build_regulatory_specialist_prompt(),
                "tools": ["regulation_lookup", "compliance_check", "submission_prep"],
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
        """Process a research request through the full workflow.

        Args:
            input_data: Dictionary containing research parameters with at least
                ``disease`` or ``target`` specification.

        Returns:
            Dictionary with research results including target analysis,
            literature findings, hypotheses, and experiment designs.
        """
        # Step 1: Target identification
        target = self._identify_target(input_data)

        # Step 2: Literature review
        literature_findings = self._conduct_literature_review(target)

        # Step 3: Hypothesis generation
        hypotheses = self._generate_hypotheses(target, literature_findings)

        # Step 4: Experiment design
        experiments = self._design_experiments(hypotheses)

        # Step 5: Data analysis guidance
        analysis_guidance = self._design_analysis_approach(experiments)

        # Step 6: Regulatory review
        regulatory_assessment = self._assess_regulatory_requirements(target, input_data)

        # Update metrics
        self.pharma_metrics.targets_evaluated += 1
        self.pharma_metrics.hypotheses_generated += len(hypotheses)
        self.pharma_metrics.experiments_designed += len(experiments)
        self.pharma_metrics.literature_review_count += len(literature_findings)
        self._update_pharma_metrics()

        return {
            "target": {
                "id": target.id,
                "name": target.name,
                "type": target.target_type,
                "disease": target.disease,
                "druggability_score": target.druggability_score,
                "validation_status": target.validation_status,
                "therapeutic_area": target.therapeutic_area,
            },
            "literature_review": {
                "findings_count": len(literature_findings),
                "key_findings": [
                    {
                        "id": f.id,
                        "title": f.title,
                        "evidence_level": f.evidence_level.value,
                        "relevance": f.relevance_score,
                        "year": f.year,
                    }
                    for f in literature_findings[:5]  # Top 5
                ],
                "coverage_estimate": self.pharma_metrics.literature_coverage,
            },
            "hypothes": [
                {
                    "id": h.id,
                    "statement": h.statement,
                    "confidence": h.confidence,
                    "novelty_score": h.novelty_score,
                    "testable": h.testable,
                }
                for h in hypotheses
            ],
            "experiments": [
                {
                    "id": e.id,
                    "name": e.name,
                    "type": e.type.value,
                    "objective": e.objective,
                    "duration_weeks": e.duration_weeks,
                    "risk_level": e.risk_level,
                }
                for e in experiments
            ],
            "analysis_approach": analysis_guidance,
            "regulatory": regulatory_assessment,
        }

    def _on_teardown(self) -> None:
        """Clean up pharmaceutical research solution resources."""
        self._agents.clear()
        self._targets.clear()
        self._literature.clear()
        self._hypotheses.clear()
        self._experiments.clear()
        self._analyses.clear()
        self._target_count = 0
        self._literature_count = 0
        self._hypothesis_count = 0
        self._experiment_count = 0
        self._analysis_count = 0

    # ------------------------------------------------------------------
    # Workflow steps
    # ------------------------------------------------------------------

    def _identify_target(self, data: Dict[str, Any]) -> ResearchTarget:
        """Step 1: Identify and validate drug targets."""
        self._target_count += 1
        target_id = f"target-{self._target_count}"

        therapeutic_area = self.config.get("therapeutic_area", "oncology")
        disease = data.get("disease", "")
        target_name = data.get("target_name", "")

        # Look up known targets
        target_info = None
        area_targets = self._target_database.get(therapeutic_area, [])

        if target_name:
            # Specific target requested
            for t in area_targets:
                if t["name"].lower() == target_name.lower():
                    target_info = t
                    break
            if target_info is None:
                # Novel or unknown target
                target_info = {
                    "name": target_name,
                    "type": data.get("target_type", "protein"),
                    "druggability": 0.5,
                    "validation": "putative",
                }
        else:
            # Recommend top targets for the disease area
            novelty_pref = self.config.get("novelty_preference", 0.5)
            if novelty_pref > 0.6 and area_targets:
                # Prefer less validated, more novel targets
                target_info = min(area_targets, key=lambda t: t["druggability"])
            elif area_targets:
                # Prefer well-validated targets
                target_info = max(area_targets, key=lambda t: t["druggability"])
            else:
                target_info = {
                    "name": "Novel Target",
                    "type": "protein",
                    "druggability": 0.5,
                    "validation": "putative",
                }

        target = ResearchTarget(
            id=target_id,
            name=target_info["name"],
            target_type=target_info["type"],
            disease=disease or therapeutic_area,
            description=(
                f"{target_info['name']} is a {target_info['type']} target "
                f"in the {therapeutic_area} therapeutic area with a "
                f"druggability score of {target_info['druggability']}. "
                f"Validation status: {target_info['validation']}."
            ),
            validation_status=target_info["validation"],
            druggability_score=target_info["druggability"],
            therapeutic_area=therapeutic_area,
        )
        self._targets[target_id] = target
        return target

    def _conduct_literature_review(
        self, target: ResearchTarget
    ) -> List[LiteratureFinding]:
        """Step 2: Conduct comprehensive literature review."""
        depth = self.config.get("literature_depth", "comprehensive")
        depth_counts = {
            "basic": 5,
            "standard": 15,
            "comprehensive": 30,
            "exhaustive": 50,
        }
        num_papers = depth_counts.get(depth, 15)

        findings = []
        evidence_levels = [
            EvidenceLevel.CLINICAL,
            EvidenceLevel.IN_VIVO,
            EvidenceLevel.IN_VITRO,
            EvidenceLevel.IN_SILICO,
        ]

        for i in range(num_papers):
            self._literature_count += 1
            lit_id = f"lit-{self._literature_count}"

            evidence_level = evidence_levels[i % len(evidence_levels)]
            relevance = 0.6 + (i % 5) * 0.08  # 0.6 to 0.92

            finding = LiteratureFinding(
                id=lit_id,
                title=f"Study of {target.name} in {target.disease} - Article {i+1}",
                authors=[f"Author {j+1}" for j in range(3)],
                journal=["Nature", "Science", "Cell", "PNAS", "J Clin Invest"][i % 5],
                year=2020 + (i % 6),
                doi=f"10.1000/example.{i+1:05d}",
                evidence_level=evidence_level,
                key_findings=[
                    f"{target.name} expression correlates with disease progression",
                    f"Inhibition of {target.name} reduces disease markers in preclinical models",
                    f"Phase {i % 3 + 1} study shows promising results",
                ],
                relevance_score=round(relevance, 2),
                methodology=["In vitro studies", "Animal models", "Clinical trial"][i % 3],
                limitations=[
                    "Small sample size",
                    "Short follow-up period",
                    "Limited generalizability",
                ][i % 3: (i % 3) + 1],
            )
            findings.append(finding)
            self._literature[lit_id] = finding

        return findings

    def _generate_hypotheses(
        self, target: ResearchTarget, literature: List[LiteratureFinding]
    ) -> List[ResearchHypothesis]:
        """Step 3: Generate research hypotheses."""
        max_hypotheses = self.config.get("max_hypotheses", 5)
        hypotheses = []

        hypothesis_templates = [
            {
                "statement": f"Inhibition of {target.name} will reduce disease progression in {target.disease}",
                "confidence": 0.7,
                "novelty": 0.3,
            },
            {
                "statement": f"{target.name} expression level predicts patient response to targeted therapy",
                "confidence": 0.6,
                "novelty": 0.5,
            },
            {
                "statement": f"Combined targeting of {target.name} with standard therapy improves outcomes",
                "confidence": 0.5,
                "novelty": 0.7,
            },
            {
                "statement": f"{target.name} plays a novel role in disease pathogenesis beyond known functions",
                "confidence": 0.4,
                "novelty": 0.9,
            },
            {
                "statement": f"Biomarker panel including {target.name} enables early disease detection",
                "confidence": 0.5,
                "novelty": 0.6,
            },
        ]

        for i, template in enumerate(hypothesis_templates[:max_hypotheses]):
            self._hypothesis_count += 1
            hyp_id = f"hyp-{self._hypothesis_count}"

            # Adjust based on target druggability and validation
            adjusted_confidence = template["confidence"] * (0.7 + target.druggability_score * 0.3)

            supporting = [
                f"Target druggability score: {target.druggability_score}",
                f"Validation status: {target.validation_status}",
            ]
            if literature:
                supporting.append(
                    f"Supported by {len([l for l in literature if l.relevance_score > 0.7])} "
                    f"high-relevance publications"
                )

            hypothesis = ResearchHypothesis(
                id=hyp_id,
                statement=template["statement"],
                target=target.name,
                rationale=(
                    f"Based on the established role of {target.name} in "
                    f"{target.disease} pathways and supporting evidence from "
                    f"literature review."
                ),
                supporting_evidence=supporting,
                contradicting_evidence=[
                    "Some conflicting results in published literature",
                    "Mechanism not fully elucidated",
                ] if i > 1 else [],
                confidence=round(adjusted_confidence, 2),
                testable=True,
                novelty_score=template["novelty"],
            )
            hypotheses.append(hypothesis)
            self._hypotheses[hyp_id] = hypothesis

        return hypotheses

    def _design_experiments(
        self, hypotheses: List[ResearchHypothesis]
    ) -> List[ExperimentDesign]:
        """Step 4: Design experiments to test hypotheses."""
        experiments = []

        for idx, hypothesis in enumerate(hypotheses[:3]):  # Design for top 3 hypotheses
            self._experiment_count += 1
            exp_id = f"exp-{self._experiment_count}"

            exp_types = [
                (ExperimentType.IN_VITRO, "In vitro functional assay"),
                (ExperimentType.IN_VIVO, "In vivo efficacy study"),
                (ExperimentType.BIOINFORMATICS, "Bioinformatics analysis"),
            ]
            exp_type, exp_name = exp_types[idx % len(exp_types)]

            durations = {
                ExperimentType.COMPUTATIONAL: 1.0,
                ExperimentType.BIOINFORMATICS: 2.0,
                ExperimentType.IN_VITRO: 4.0,
                ExperimentType.IN_VIVO: 12.0,
                ExperimentType.CLINICAL: 52.0,
                ExperimentType.STRUCTURAL: 8.0,
            }

            risk_levels = ["low", "medium", "high"]
            risk = risk_levels[idx % 3]

            experiment = ExperimentDesign(
                id=exp_id,
                name=f"{exp_name} for {hypothesis.target}",
                type=exp_type,
                hypothesis_id=hypothesis.id,
                objective=f"Test hypothesis: {hypothesis.statement[:80]}...",
                methodology=(
                    f"{exp_type.value.capitalize()} experiment designed to "
                    f"evaluate the role of {hypothesis.target} in the "
                    f"proposed mechanism."
                ),
                variables={
                    "independent": [hypothesis.target + " expression/inhibition"],
                    "dependent": ["Cell viability", "Proliferation", "Apoptosis"],
                    "control": ["Vehicle control", "Positive control", "Negative control"],
                },
                sample_size=3 if exp_type in (ExperimentType.IN_VIVO, ExperimentType.IN_VITRO) else 50,
                duration_weeks=durations.get(exp_type, 4.0),
                materials_required=[
                    f"{hypothesis.target} inhibitor/siRNA",
                    "Cell lines or animal models",
                    "Assay kits and reagents",
                    "Imaging and detection equipment",
                ],
                expected_outcome=(
                    f"Expected to confirm or refute the hypothesis that "
                    f"{hypothesis.target} modulation affects disease phenotypes."
                ),
                success_criteria=[
                    "Statistically significant difference between groups",
                    "Dose-dependent response observed",
                    "Consistent results across replicates",
                ],
                risk_level=risk,
            )
            experiments.append(experiment)
            self._experiments[exp_id] = experiment

        return experiments

    def _design_analysis_approach(
        self, experiments: List[ExperimentDesign]
    ) -> Dict[str, Any]:
        """Step 5: Design data analysis approach."""
        threshold = self.config.get("statistical_threshold", 0.05)

        return {
            "statistical_methods": [
                "Descriptive statistics (mean, median, standard deviation)",
                "Student's t-test or ANOVA for group comparisons",
                "Regression analysis for dose-response relationships",
                "Survival analysis where applicable",
                "Multiple testing correction (Bonferroni or FDR)",
            ],
            "significance_threshold": threshold,
            "visualization_plan": [
                "Bar charts for group comparisons",
                "Dose-response curves",
                "Kaplan-Meier survival plots",
                "Heatmaps for gene expression data",
                "Volcano plots for differential analysis",
            ],
            "quality_control": [
                "Outlier detection and handling",
                "Normality testing",
                "Variance homogeneity check",
                "Sample size power analysis review",
            ],
            "reproducibility_checks": [
                "Biological replicates verification",
                "Technical replicates consistency",
                "Independent validation cohort",
            ],
        }

    def _assess_regulatory_requirements(
        self, target: ResearchTarget, data: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Step 6: Assess regulatory requirements."""
        region = self.config.get("regulatory_region", "us")
        phase = self.config.get("research_phase", "target_discovery")

        regulatory_bodies = {
            "us": "FDA",
            "eu": "EMA",
            "jp": "PMDA",
            "cn": "NMPA",
            "global": "ICH",
        }
        body = regulatory_bodies.get(region, "FDA")

        requirements = {
            "target_discovery": [
                "No direct regulatory requirements at this stage",
                "Document all research for future IND support",
                "Establish target validation data package",
            ],
            "preclinical": [
                "GLP-compliant safety studies required",
                "Pharmacology and toxicology data package",
                "CMC (Chemistry, Manufacturing, Controls) development",
                "Animal model efficacy data",
            ],
            "clinical_phase1": [
                "IND/CTA submission required",
                "Safety and tolerability assessment",
                "Dose escalation study design",
                "PK/PD characterization",
            ],
            "clinical_phase2": [
                "Efficacy signal detection",
                "Dose-response characterization",
                "Safety monitoring expansion",
                "Regulatory mid-point consultation recommended",
            ],
            "clinical_phase3": [
                "Pivotal efficacy and safety studies",
                "Statistical plan aligned with regulatory guidance",
                "Risk management plan development",
                "Post-marketing commitment planning",
            ],
            "regulatory": [
                "NDA/BLA/MAA submission preparation",
                "Complete response letter readiness",
                "Labeling negotiations",
                "Post-approval requirements planning",
            ],
        }

        phase_reqs = requirements.get(phase, requirements["target_discovery"])

        return {
            "regulatory_body": body,
            "region": region,
            "current_phase": phase,
            "requirements": phase_reqs,
            "readiness_score": self._calculate_regulatory_readiness(phase),
            "next_milestones": self._get_next_milestones(phase),
            "estimated_time_to_next_phase": self._estimate_timeline(phase),
        }

    def _calculate_regulatory_readiness(self, phase: str) -> float:
        """Calculate regulatory readiness score."""
        readiness_scores = {
            "target_discovery": 0.1,
            "hit_discovery": 0.2,
            "lead_optimization": 0.35,
            "preclinical": 0.55,
            "clinical_phase1": 0.65,
            "clinical_phase2": 0.75,
            "clinical_phase3": 0.9,
            "regulatory": 0.95,
        }
        return readiness_scores.get(phase, 0.1)

    def _get_next_milestones(self, phase: str) -> List[str]:
        """Get next regulatory milestones."""
        milestones = {
            "target_discovery": ["Target validation complete", "Hit identification"],
            "hit_discovery": ["Hit-to-lead transition", "SAR development"],
            "lead_optimization": ["Lead candidate selection", "IND-enabling studies start"],
            "preclinical": ["IND/CTA submission", "Phase 1 trial initiation"],
            "clinical_phase1": ["Phase 1 completion", "Phase 2 initiation"],
            "clinical_phase2": ["Phase 2 completion", "Phase 3 initiation"],
            "clinical_phase3": ["Phase 3 completion", "Regulatory submission"],
            "regulatory": ["Approval decision", "Post-marketing studies"],
        }
        return milestones.get(phase, ["Research continues"])

    def _estimate_timeline(self, phase: str) -> str:
        """Estimate timeline to next phase."""
        timelines = {
            "target_discovery": "1-2 years",
            "hit_discovery": "6-12 months",
            "lead_optimization": "1-2 years",
            "preclinical": "1-2 years",
            "clinical_phase1": "6-12 months",
            "clinical_phase2": "1-2 years",
            "clinical_phase3": "2-4 years",
            "regulatory": "6-18 months",
        }
        return timelines.get(phase, "Variable")

    # ------------------------------------------------------------------
    # Agent prompts
    # ------------------------------------------------------------------

    def _build_research_scientist_prompt(self) -> str:
        """Build the system prompt for the research scientist role."""
        therapeutic_area = self.config.get("therapeutic_area", "oncology")
        return (
            f"You are a senior research scientist specializing in {therapeutic_area} "
            "drug discovery. Design rigorous research approaches, develop "
            "testable hypotheses, and interpret experimental results. "
            "Maintain high scientific standards, consider alternative "
            "explanations, and design experiments that produce clear, "
            "interpretable results. Always ground your work in existing "
            "literature while pushing for innovative advances."
        )

    def _build_data_analyst_prompt(self) -> str:
        """Build the system prompt for the data analyst role."""
        threshold = self.config.get("statistical_threshold", 0.05)
        return (
            f"You are a biostatistician and data analyst specializing in "
            "pharmaceutical research. Apply rigorous statistical methods, "
            "perform appropriate hypothesis testing with alpha = {threshold}, "
            "and ensure all analyses are reproducible. Use appropriate "
            "visualizations to communicate findings clearly. Always check "
            "assumptions, handle outliers properly, and be transparent "
            "about limitations."
        )

    def _build_literature_reviewer_prompt(self) -> str:
        """Build the system prompt for the literature reviewer role."""
        depth = self.config.get("literature_depth", "comprehensive")
        return (
            "You are a scientific literature review specialist conducting a "
            f"{depth} review. Systematically search, evaluate, and synthesize "
            "research findings from peer-reviewed publications. Assess study "
            "quality, identify gaps in knowledge, and highlight key findings. "
            "Be thorough, objective, and systematic in your approach. "
            "Critically evaluate methodology and report biases."
        )

    def _build_regulatory_specialist_prompt(self) -> str:
        """Build the system prompt for the regulatory specialist role."""
        region = self.config.get("regulatory_region", "us")
        body = {"us": "FDA", "eu": "EMA", "jp": "PMDA", "cn": "NMPA", "global": "ICH"}.get(region, "FDA")
        return (
            f"You are a regulatory affairs specialist experienced with {body} "
            f"requirements for pharmaceutical development in the {region} region. "
            "Ensure research plans and data packages align with regulatory "
            "expectations at each development phase. Identify regulatory "
            "gaps early and recommend remediation strategies. Stay current "
            "with evolving regulatory guidance and ensure compliance at "
            "every stage."
        )

    # ------------------------------------------------------------------
    # Metrics
    # ------------------------------------------------------------------

    def _update_pharma_metrics(self) -> None:
        """Update pharmaceutical-specific metrics."""
        if self._hypotheses:
            total = len(self._hypotheses)
            avg_conf = sum(h.confidence for h in self._hypotheses.values()) / total
            self.pharma_metrics.avg_hypothesis_confidence = avg_conf

            novel = sum(
                1 for h in self._hypotheses.values() if h.novelty_score > 0.7
            )
            self.pharma_metrics.novel_findings = novel

        # Literature coverage estimate based on depth
        depth = self.config.get("literature_depth", "comprehensive")
        coverage_map = {
            "basic": 0.3,
            "standard": 0.6,
            "comprehensive": 0.85,
            "exhaustive": 0.95,
        }
        self.pharma_metrics.literature_coverage = coverage_map.get(depth, 0.6)

        # Research quality estimate
        if self._targets and self._hypotheses:
            quality_score = (
                0.3 * (sum(t.druggability_score for t in self._targets.values()) / len(self._targets))
                + 0.3 * self.pharma_metrics.avg_hypothesis_confidence
                + 0.2 * self.pharma_metrics.literature_coverage
                + 0.2 * self.pharma_metrics.regulatory_readiness
            ) * 5  # Scale to 0-5
            self.pharma_metrics.research_quality = round(quality_score, 2)

        # Regulatory readiness
        phase = self.config.get("research_phase", "target_discovery")
        self.pharma_metrics.regulatory_readiness = self._calculate_regulatory_readiness(phase)

    def get_pharma_metrics(self) -> Dict[str, Any]:
        """Get the current pharmaceutical research metrics.

        Returns:
            Dictionary with pharma metrics including research quality,
            novel findings, literature coverage, and target evaluations.
        """
        return self.pharma_metrics.to_dict()
