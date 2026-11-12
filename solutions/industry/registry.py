"""
Industry Solution Registry.

Provides a central registry for discovering, registering, and
retrieving industry-specific AI solutions within the LiuHao AI OS
platform. Supports listing all solutions, getting specific ones
by name, and searching by industry category.
"""

from typing import Dict, List, Optional, Type
import importlib

from solutions.templates.base_solution import BaseSolution, SolutionMetadata


class SolutionRegistry:
    """Registry for industry-specific AI solution templates.

    The SolutionRegistry provides a centralized way to discover and
    instantiate industry solutions. It maintains a catalog of all
    available solutions and supports lookup by name, industry, and
    tags.

    Attributes:
        _solutions: Internal mapping of solution names to solution classes.
        _loaded: Whether all built-in solutions have been loaded.
    """

    # Mapping of solution name -> (module path, class name, industry)
    _SOLUTION_REGISTRY: Dict[str, Dict[str, str]] = {
        "financial-advisor": {
            "module": "solutions.industry.financial.financial_advisor",
            "class": "FinancialAdvisorSolution",
            "industry": "financial",
        },
        "medical-assistant": {
            "module": "solutions.industry.healthcare.medical_assistant",
            "class": "MedicalAssistantSolution",
            "industry": "healthcare",
        },
        "ai-tutor": {
            "module": "solutions.industry.education.tutor",
            "class": "AITutorSolution",
            "industry": "education",
        },
        "legal-assistant": {
            "module": "solutions.industry.legal.legal_assistant",
            "class": "LegalAssistantSolution",
            "industry": "legal",
        },
        "marketing-assistant": {
            "module": "solutions.industry.marketing.marketing_assistant",
            "class": "MarketingAssistantSolution",
            "industry": "marketing",
        },
        "hr-assistant": {
            "module": "solutions.industry.hr.hr_assistant",
            "class": "HRAssistantSolution",
            "industry": "human_resources",
        },
        "qc-inspector": {
            "module": "solutions.industry.manufacturing.qc_inspector",
            "class": "QCInspectorSolution",
            "industry": "manufacturing",
        },
        "customer-experience": {
            "module": "solutions.industry.retail.customer_experience",
            "class": "CustomerExperienceSolution",
            "industry": "retail",
        },
        "cybersecurity-analyst": {
            "module": "solutions.industry.cybersecurity.security_analyst",
            "class": "CybersecurityAnalystSolution",
            "industry": "cybersecurity",
        },
        "supply-chain-optimization": {
            "module": "solutions.industry.logistics.supply_chain",
            "class": "SupplyChainOptimizationSolution",
            "industry": "logistics",
        },
        "property-advisor": {
            "module": "solutions.industry.realestate.property_advisor",
            "class": "PropertyAdvisorSolution",
            "industry": "real_estate",
        },
        "farming-advisor": {
            "module": "solutions.industry.agriculture.farming_advisor",
            "class": "FarmingAdvisorSolution",
            "industry": "agriculture",
        },
        "content-studio": {
            "module": "solutions.industry.media.content_studio",
            "class": "ContentStudioSolution",
            "industry": "media",
        },
        "energy-optimizer": {
            "module": "solutions.industry.energy.energy_optimizer",
            "class": "EnergyOptimizerSolution",
            "industry": "energy",
        },
        "game-designer": {
            "module": "solutions.industry.gaming.game_designer",
            "class": "GameDesignerSolution",
            "industry": "gaming",
        },
    }

    def __init__(self) -> None:
        """Initialize the solution registry.

        The registry starts with known solution entries. Solution
        classes are lazily loaded on first access to avoid importing
        all modules at startup.
        """
        self._loaded_classes: Dict[str, Type[BaseSolution]] = {}
        self._custom_solutions: Dict[str, Type[BaseSolution]] = {}

    def list_solutions(self) -> List[Dict[str, str]]:
        """List all available industry solutions.

        Returns a list of solution metadata summaries without
        instantiating any solution classes.

        Returns:
            List of dictionaries with solution name, industry,
            and description for each registered solution.
        """
        solutions = []
        for name, info in self._SOLUTION_REGISTRY.items():
            solutions.append({
                "name": name,
                "industry": info["industry"],
                "class_name": info["class"],
                "module": info["module"],
            })

        # Add custom registered solutions
        for name, cls in self._custom_solutions.items():
            # Create a temporary instance to get metadata
            try:
                instance = cls()
                meta = instance.metadata
                solutions.append({
                    "name": name,
                    "industry": meta.industry,
                    "class_name": cls.__name__,
                    "module": cls.__module__,
                    "description": meta.description,
                    "version": meta.version,
                })
            except Exception:
                solutions.append({
                    "name": name,
                    "industry": "custom",
                    "class_name": cls.__name__,
                    "module": cls.__module__,
                })

        return solutions

    def get_solution(
        self, name: str, config: Optional[Dict] = None
    ) -> BaseSolution:
        """Get an instance of a solution by name.

        Lazily loads the solution class on first access and creates
        a new instance with the provided configuration.

        Args:
            name: Solution name (e.g., "financial-advisor").
            config: Optional configuration dictionary for the solution.

        Returns:
            An instance of the requested solution class.

        Raises:
            ValueError: If the solution name is not found in the registry.
            ImportError: If the solution module cannot be imported.
        """
        # Check custom solutions first
        if name in self._custom_solutions:
            solution_class = self._custom_solutions[name]
            return solution_class(config=config or {})

        # Check built-in registry
        if name not in self._SOLUTION_REGISTRY:
            available = ", ".join(self._SOLUTION_REGISTRY.keys())
            raise ValueError(
                f"Solution '{name}' not found in registry. "
                f"Available solutions: {available}"
            )

        # Lazy load the class
        if name not in self._loaded_classes:
            self._loaded_classes[name] = self._load_solution_class(name)

        solution_class = self._loaded_classes[name]
        return solution_class(config=config or {})

    def get_solution_class(self, name: str) -> Type[BaseSolution]:
        """Get the solution class (not an instance) by name.

        Args:
            name: Solution name.

        Returns:
            The solution class (subclass of BaseSolution).

        Raises:
            ValueError: If the solution name is not found.
            ImportError: If the solution module cannot be imported.
        """
        if name in self._custom_solutions:
            return self._custom_solutions[name]

        if name not in self._SOLUTION_REGISTRY:
            available = ", ".join(self._SOLUTION_REGISTRY.keys())
            raise ValueError(
                f"Solution '{name}' not found. Available: {available}"
            )

        if name not in self._loaded_classes:
            self._loaded_classes[name] = self._load_solution_class(name)

        return self._loaded_classes[name]

    def search_by_industry(self, industry: str) -> List[Dict[str, str]]:
        """Search for solutions by industry category.

        Performs a case-insensitive substring match on the industry
        field of all registered solutions.

        Args:
            industry: Industry keyword to search for (e.g., "finance",
                "health", "retail").

        Returns:
            List of solution info dictionaries that match the
            industry query.
        """
        industry_lower = industry.lower()
        results = []

        for name, info in self._SOLUTION_REGISTRY.items():
            if industry_lower in info["industry"].lower():
                results.append({
                    "name": name,
                    "industry": info["industry"],
                    "class_name": info["class"],
                    "module": info["module"],
                })

        # Also search custom solutions
        for name, cls in self._custom_solutions.items():
            try:
                instance = cls()
                if industry_lower in instance.metadata.industry.lower():
                    results.append({
                        "name": name,
                        "industry": instance.metadata.industry,
                        "class_name": cls.__name__,
                        "description": instance.metadata.description,
                    })
            except Exception:
                pass

        return results

    def register_solution(
        self, name: str, solution_class: Type[BaseSolution]
    ) -> None:
        """Register a custom solution class.

        Allows users to extend the registry with their own solution
        implementations that follow the BaseSolution interface.

        Args:
            name: Unique name for the solution.
            solution_class: The solution class (must be a subclass
                of BaseSolution).

        Raises:
            ValueError: If a solution with the same name already exists.
            TypeError: If solution_class is not a subclass of BaseSolution.
        """
        if name in self._SOLUTION_REGISTRY or name in self._custom_solutions:
            raise ValueError(
                f"A solution named '{name}' already exists in the registry"
            )

        if not issubclass(solution_class, BaseSolution):
            raise TypeError(
                f"solution_class must be a subclass of BaseSolution, "
                f"got {solution_class.__name__}"
            )

        self._custom_solutions[name] = solution_class

    def unregister_solution(self, name: str) -> None:
        """Remove a custom solution from the registry.

        Only removes custom-registered solutions; built-in solutions
        cannot be unregistered.

        Args:
            name: Name of the custom solution to remove.

        Raises:
            ValueError: If the solution is not a custom registered solution.
        """
        if name not in self._custom_solutions:
            raise ValueError(
                f"Custom solution '{name}' not found in registry"
            )
        del self._custom_solutions[name]

    def get_metadata(self, name: str) -> SolutionMetadata:
        """Get metadata for a solution without full initialization.

        Creates a minimal instance to access metadata fields.

        Args:
            name: Solution name.

        Returns:
            SolutionMetadata for the requested solution.
        """
        solution = self.get_solution(name)
        return solution.metadata

    def list_industries(self) -> List[str]:
        """Get a list of all unique industries represented in the registry.

        Returns:
            Sorted list of industry category names.
        """
        industries = set()
        for info in self._SOLUTION_REGISTRY.values():
            industries.add(info["industry"])

        for cls in self._custom_solutions.values():
            try:
                instance = cls()
                industries.add(instance.metadata.industry)
            except Exception:
                pass

        return sorted(industries)

    def count(self) -> int:
        """Return the total number of registered solutions.

        Returns:
            Total count of solutions (built-in + custom).
        """
        return len(self._SOLUTION_REGISTRY) + len(self._custom_solutions)

    def _load_solution_class(self, name: str) -> Type[BaseSolution]:
        """Dynamically import and load a solution class.

        Args:
            name: Solution name to load.

        Returns:
            The loaded solution class.

        Raises:
            ImportError: If the module or class cannot be loaded.
        """
        info = self._SOLUTION_REGISTRY[name]
        module_path = info["module"]
        class_name = info["class"]

        try:
            module = importlib.import_module(module_path)
        except ImportError as e:
            raise ImportError(
                f"Failed to import solution module '{module_path}': {e}"
            ) from e

        try:
            solution_class = getattr(module, class_name)
        except AttributeError as e:
            raise ImportError(
                f"Class '{class_name}' not found in module '{module_path}'"
            ) from e

        if not issubclass(solution_class, BaseSolution):
            raise TypeError(
                f"Class '{class_name}' in module '{module_path}' is not "
                f"a subclass of BaseSolution"
            )

        return solution_class

    def __contains__(self, name: str) -> bool:
        """Check if a solution is registered (supports 'in' operator)."""
        return (
            name in self._SOLUTION_REGISTRY
            or name in self._custom_solutions
        )

    def __len__(self) -> int:
        """Return the number of registered solutions."""
        return self.count()

    def __repr__(self) -> str:
        """Return string representation of the registry."""
        return (
            f"SolutionRegistry("
            f"built_in={len(self._SOLUTION_REGISTRY)}, "
            f"custom={len(self._custom_solutions)})"
        )
