"""
Tests for all LiuHao AI OS industry solutions (solutions/industry/).

For every industry solution module this verifies:
- the module imports cleanly,
- the solution class instantiates,
- basic attributes: metadata.name, metadata.industry and a non-empty
  feature/tag list (SolutionMetadata.tags),
- basic functionality: initialize() and monitor().

A MockProvider is built for every test and passed to the solution
constructor whenever the constructor accepts a ``provider`` parameter
(currently no solution does; they take ``config`` only).
"""

import importlib
import inspect
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from solutions.templates.base_solution import BaseSolution, SolutionStatus  # noqa: E402
from src.ai.providers import MockProvider  # noqa: E402

# (module_path, class_name, expected metadata.name, expected metadata.industry)
SOLUTIONS = [
    ("solutions.industry.agriculture.farming_advisor", "FarmingAdvisorSolution", "farming-advisor", "agriculture"),
    ("solutions.industry.architecture.architectural_design", "ArchitecturalDesignSolution", "architectural-design", "architecture"),
    ("solutions.industry.automotive.auto_diagnostic", "AutoDiagnosticSolution", "auto-diagnostic", "automotive"),
    ("solutions.industry.aviation.flight_planner", "FlightPlannerSolution", "flight-planner", "aviation"),
    ("solutions.industry.culinary.recipe_developer", "RecipeDeveloperSolution", "recipe-developer", "culinary"),
    ("solutions.industry.cybersecurity.security_analyst", "CybersecurityAnalystSolution", "cybersecurity-analyst", "cybersecurity"),
    ("solutions.industry.dental.dental_assistant", "DentalAssistantSolution", "dental-assistant", "dental"),
    ("solutions.industry.education.tutor", "AITutorSolution", "ai-tutor", "education"),
    ("solutions.industry.energy.energy_optimizer", "EnergyOptimizerSolution", "energy-optimizer", "energy"),
    ("solutions.industry.fashion.fashion_designer", "FashionDesignerSolution", "fashion-designer", "fashion"),
    ("solutions.industry.financial.financial_advisor", "FinancialAdvisorSolution", "financial-advisor", "financial"),
    ("solutions.industry.fitness.fitness_coach", "FitnessCoachSolution", "fitness-coach", "fitness"),
    ("solutions.industry.gaming.game_designer", "GameDesignerSolution", "game-designer", "gaming"),
    ("solutions.industry.healthcare.medical_assistant", "MedicalAssistantSolution", "medical-assistant", "healthcare"),
    ("solutions.industry.hospitality.hotel_concierge", "HotelConciergeSolution", "hotel-concierge", "hospitality"),
    ("solutions.industry.hr.hr_assistant", "HRAssistantSolution", "hr-assistant", "human_resources"),
    ("solutions.industry.insurance.fraud_detection", "FraudDetectionSolution", "fraud-detection", "insurance"),
    ("solutions.industry.insurance.insurance_advisor", "InsuranceAdvisorSolution", "insurance-advisor", "insurance"),
    ("solutions.industry.interior_design.interior_designer", "InteriorDesignerSolution", "interior-designer", "interior_design"),
    ("solutions.industry.legal.contract_reviewer", "ContractReviewerSolution", "contract-reviewer", "legal"),
    ("solutions.industry.legal.legal_assistant", "LegalAssistantSolution", "legal-assistant", "legal"),
    ("solutions.industry.logistics.supply_chain", "SupplyChainOptimizationSolution", "supply-chain-optimization", "logistics"),
    ("solutions.industry.manufacturing.qc_inspector", "QCInspectorSolution", "qc-inspector", "manufacturing"),
    ("solutions.industry.marine.marine_navigation", "MarineNavigationSolution", "marine-navigation", "marine"),
    ("solutions.industry.marketing.marketing_assistant", "MarketingAssistantSolution", "marketing-assistant", "marketing"),
    ("solutions.industry.media.content_studio", "ContentStudioSolution", "content-studio", "media"),
    ("solutions.industry.mining.mining_operations", "MiningOperationsSolution", "mining-operations", "mining"),
    ("solutions.industry.music.music_producer", "MusicProducerSolution", "music-producer", "music"),
    ("solutions.industry.nutrition.nutritionist", "NutritionistSolution", "nutritionist", "nutrition"),
    ("solutions.industry.pharmaceutical.drug_research", "DrugResearchSolution", "drug-research", "pharmaceutical"),
    ("solutions.industry.psychology.therapist_helper", "TherapistHelperSolution", "therapist-helper", "psychology"),
    ("solutions.industry.quality.quality_assurance", "QualityAssuranceSolution", "quality-assurance", "quality"),
    ("solutions.industry.realestate.property_advisor", "PropertyAdvisorSolution", "property-advisor", "real_estate"),
    ("solutions.industry.realestate.property_valuation", "PropertyValuationSolution", "property-valuation", "realestate"),
    ("solutions.industry.retail.customer_experience", "CustomerExperienceSolution", "customer-experience", "retail"),
    ("solutions.industry.sports.sports_coach", "SportsCoachSolution", "sports-coach", "sports"),
    ("solutions.industry.supplychain.demand_forecaster", "DemandForecasterSolution", "demand-forecaster", "supplychain"),
    ("solutions.industry.veterinary.pet_care", "PetCareSolution", "pet-care", "veterinary"),
]


@pytest.fixture()
def provider():
    """Mock LLM provider used as the ``provider`` argument where accepted."""
    return MockProvider(name="test", model="test")


def _build_solution(module_path: str, class_name: str, provider) -> BaseSolution:
    """Import the solution class and instantiate it.

    Passes the MockProvider as ``provider`` when the constructor accepts
    it; otherwise instantiates with the default ``config`` argument.
    """
    module = importlib.import_module(module_path)
    solution_class = getattr(module, class_name)
    assert issubclass(solution_class, BaseSolution)

    kwargs = {}
    init_params = inspect.signature(solution_class.__init__).parameters
    if "provider" in init_params:
        kwargs["provider"] = provider
    return solution_class(**kwargs)


@pytest.mark.parametrize(
    "module_path,class_name,expected_name,expected_industry",
    SOLUTIONS,
    ids=[entry[1] for entry in SOLUTIONS],
)
def test_industry_solution(
    module_path, class_name, expected_name, expected_industry, provider
):
    """Import, instantiate and check basic attributes of one solution."""
    solution = _build_solution(module_path, class_name, provider)

    # Basic attributes
    assert solution.metadata.name == expected_name
    assert solution.metadata.name
    assert solution.metadata.industry == expected_industry
    # Feature list (tags) must be non-empty
    assert isinstance(solution.metadata.tags, list)
    assert len(solution.metadata.tags) > 0

    # Basic functionality: lifecycle initialize + monitor
    assert solution.status == SolutionStatus.UNINITIALIZED
    solution.initialize()
    assert solution.status == SolutionStatus.INITIALIZED

    info = solution.get_info()
    assert info["name"] == expected_name
    assert info["industry"] == expected_industry
    assert info["tags"]

    report = solution.monitor()
    assert report["name"] == expected_name
    assert "health" in report
    assert "metrics" in report
