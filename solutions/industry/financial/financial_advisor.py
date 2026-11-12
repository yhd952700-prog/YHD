"""
Financial Advisor AI Solution Template.

A comprehensive AI-powered financial advisory solution for wealth
management firms, banks, and financial institutions. Includes
multi-agent roles for analysis, risk assessment, compliance, and
portfolio management.

Typical workflow:
    client intake -> risk assessment -> portfolio analysis
    -> recommendations -> compliance review
"""

from typing import Optional, Dict, Any, List
from enum import Enum
from dataclasses import dataclass, field

from solutions.templates.base_solution import (
    BaseSolution,
    SolutionMetadata,
    SolutionMetrics,
)


class RiskTolerance(str, Enum):
    """Client risk tolerance levels."""
    CONSERVATIVE = "conservative"
    MODERATELY_CONSERVATIVE = "moderately_conservative"
    MODERATE = "moderate"
    MODERATELY_AGGRESSIVE = "moderately_aggressive"
    AGGRESSIVE = "aggressive"


class FinancialGoal(str, Enum):
    """Client financial goal categories."""
    RETIREMENT = "retirement"
    WEALTH_GROWTH = "wealth_growth"
    INCOME = "income"
    CAPITAL_PRESERVATION = "capital_preservation"
    TAX_OPTIMIZATION = "tax_optimization"
    ESTATE_PLANNING = "estate_planning"


class RecommendationStatus(str, Enum):
    """Status of a financial recommendation through the workflow."""
    DRAFT = "draft"
    ANALYZED = "analyzed"
    RISK_ASSESSED = "risk_assessed"
    COMPLIANCE_PENDING = "compliance_pending"
    COMPLIANCE_APPROVED = "compliance_approved"
    COMPLIANCE_REJECTED = "compliance_rejected"
    CLIENT_READY = "client_ready"
    PRESENTED = "presented"


class FinancialRole(str, Enum):
    """Agent roles in the financial advisory solution."""
    FINANCIAL_ANALYST = "financial_analyst"
    RISK_ASSESSOR = "risk_assessor"
    COMPLIANCE_OFFICER = "compliance_officer"
    PORTFOLIO_MANAGER = "portfolio_manager"


@dataclass
class ClientProfile:
    """Represents a client's financial profile.

    Attributes:
        client_id: Unique client identifier.
        name: Client name.
        age: Client age in years.
        annual_income: Annual income in base currency.
        net_worth: Total net worth in base currency.
        investment_horizon_years: Investment time horizon.
        risk_tolerance: Client's risk tolerance level.
        goals: List of financial goals.
        existing_portfolio: Current portfolio holdings.
        risk_score: Calculated risk score (0-100).
        created_at: ISO 8601 creation timestamp.
    """
    client_id: str
    name: str = ""
    age: int = 40
    annual_income: float = 0.0
    net_worth: float = 0.0
    investment_horizon_years: int = 10
    risk_tolerance: RiskTolerance = RiskTolerance.MODERATE
    goals: List[FinancialGoal] = field(default_factory=list)
    existing_portfolio: Dict[str, Any] = field(default_factory=dict)
    risk_score: float = 50.0
    created_at: Optional[str] = None


@dataclass
class PortfolioRecommendation:
    """Represents a portfolio recommendation.

    Attributes:
        id: Unique recommendation identifier.
        client_id: Associated client ID.
        asset_allocation: Target asset allocation by category.
        expected_return: Expected annual return (percentage).
        risk_level: Estimated risk level (0-100).
        recommendations: List of specific recommendations.
        rationale: Explanation of the recommendation strategy.
        status: Current workflow status.
        compliance_notes: Compliance review notes.
        fees_estimate: Estimated annual fees (percentage).
        created_at: ISO 8601 creation timestamp.
    """
    id: str
    client_id: str
    asset_allocation: Dict[str, float] = field(default_factory=dict)
    expected_return: float = 0.0
    risk_level: float = 50.0
    recommendations: List[str] = field(default_factory=list)
    rationale: str = ""
    status: RecommendationStatus = RecommendationStatus.DRAFT
    compliance_notes: str = ""
    fees_estimate: float = 0.0
    created_at: Optional[str] = None


@dataclass
class FinancialMetrics(SolutionMetrics):
    """Extended metrics for the financial advisory solution.

    Attributes:
        avg_portfolio_return: Average portfolio return percentage.
        avg_risk_score: Average client risk score.
        compliance_rate: Rate of recommendations passing compliance.
        client_satisfaction: Average client satisfaction score (0-5).
        portfolios_managed: Number of active portfolios managed.
        recommendations_generated: Total recommendations generated.
        avg_analysis_time_min: Average analysis time in minutes.
    """
    avg_portfolio_return: float = 0.0
    avg_risk_score: float = 50.0
    compliance_rate: float = 1.0
    client_satisfaction: float = 0.0
    portfolios_managed: int = 0
    recommendations_generated: int = 0
    avg_analysis_time_min: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        """Convert financial metrics to a dictionary."""
        base = super().to_dict()
        base.update({
            "avg_portfolio_return": self.avg_portfolio_return,
            "avg_risk_score": self.avg_risk_score,
            "compliance_rate": self.compliance_rate,
            "client_satisfaction": self.client_satisfaction,
            "portfolios_managed": self.portfolios_managed,
            "recommendations_generated": self.recommendations_generated,
            "avg_analysis_time_min": self.avg_analysis_time_min,
        })
        return base


class FinancialAdvisorSolution(BaseSolution):
    """Financial Advisor AI solution template.

    Provides a complete, pre-configured financial advisory solution
    with multi-agent collaboration for client profiling, risk
    assessment, portfolio analysis, recommendation generation,
    and compliance review.

    Pre-configured agent roles:
        - Financial Analyst: Performs market and portfolio analysis
        - Risk Assessor: Evaluates client risk tolerance and portfolio risk
        - Compliance Officer: Reviews recommendations for regulatory compliance
        - Portfolio Manager: Constructs and optimizes client portfolios

    Workflow:
        1. Client Intake - Gather client financial information
        2. Risk Assessment - Evaluate risk tolerance and capacity
        3. Portfolio Analysis - Analyze current portfolio and market conditions
        4. Recommendations - Generate tailored investment recommendations
        5. Compliance Review - Verify recommendations meet regulatory requirements

    Configuration options:
        - jurisdiction: Regulatory jurisdiction (e.g., "us", "eu", "cn")
        - compliance_level: Strictness of compliance checks
        - default_benchmark: Default benchmark index
        - min_portfolio_size: Minimum portfolio size to manage
        - rebalance_frequency: Portfolio rebalancing frequency
    """

    def _build_metadata(self) -> SolutionMetadata:
        """Build metadata for the financial advisor solution."""
        return SolutionMetadata(
            name="financial-advisor",
            version="1.0.0",
            industry="financial",
            description=(
                "AI-powered financial advisory solution with intelligent "
                "client profiling, risk assessment, portfolio optimization, "
                "and automated compliance review."
            ),
            author="LiuHao AI OS",
            tags=["finance", "wealth-management", "portfolio", "compliance", "risk"],
            min_liuhao_version="1.0.0",
        )

    def _build_config_schema(self) -> None:
        """Define configuration fields for the financial advisory solution."""
        self.config_schema.add_field(
            name="jurisdiction",
            field_type="string",
            required=True,
            description="Regulatory jurisdiction for compliance",
            valid_values=["us", "eu", "uk", "cn", "jp", "sg", "global"],
        )
        self.config_schema.add_field(
            name="compliance_level",
            field_type="string",
            required=False,
            default="standard",
            description="Strictness level of compliance checks",
            valid_values=["basic", "standard", "strict", "enhanced"],
        )
        self.config_schema.add_field(
            name="default_benchmark",
            field_type="string",
            required=False,
            default="SP500",
            description="Default benchmark index for performance comparison",
        )
        self.config_schema.add_field(
            name="min_portfolio_size",
            field_type="float",
            required=False,
            default=10000.0,
            description="Minimum portfolio size accepted for management",
        )
        self.config_schema.add_field(
            name="rebalance_frequency",
            field_type="string",
            required=False,
            default="quarterly",
            description="Portfolio rebalancing frequency",
            valid_values=["monthly", "quarterly", "semi-annual", "annual"],
        )
        self.config_schema.add_field(
            name="risk_model",
            field_type="string",
            required=False,
            default="modern_portfolio_theory",
            description="Risk analysis model to use",
            valid_values=["modern_portfolio_theory", "black_litterman", "factor_model", "value_at_risk"],
        )
        self.config_schema.add_field(
            name="max_risk_score",
            field_type="int",
            required=False,
            default=85,
            description="Maximum allowed portfolio risk score (0-100)",
        )

    def _on_initialize(self) -> None:
        """Initialize financial advisor solution state."""
        self._agents: Dict[str, Dict[str, Any]] = {}
        self._clients: Dict[str, ClientProfile] = {}
        self._recommendations: Dict[str, PortfolioRecommendation] = {}
        self._recommendation_count = 0
        self._client_count = 0

        # Initialize financial-specific metrics
        self.financial_metrics = FinancialMetrics()

    def _on_deploy(self) -> None:
        """Deploy financial advisor agents and workflows."""
        agent_configs = [
            (FinancialRole.FINANCIAL_ANALYST, {
                "name": "Financial Analyst",
                "description": "Performs market analysis and portfolio evaluation",
                "system_prompt": self._build_financial_analyst_prompt(),
                "tools": ["market_data", "portfolio_analysis", "financial_modeling"],
            }),
            (FinancialRole.RISK_ASSESSOR, {
                "name": "Risk Assessor",
                "description": "Evaluates client risk tolerance and portfolio risk",
                "system_prompt": self._build_risk_assessor_prompt(),
                "tools": ["risk_modeling", "stress_testing", "scenario_analysis"],
            }),
            (FinancialRole.COMPLIANCE_OFFICER, {
                "name": "Compliance Officer",
                "description": "Reviews recommendations for regulatory compliance",
                "system_prompt": self._build_compliance_officer_prompt(),
                "tools": ["compliance_check", "regulation_lookup", "audit_trail"],
            }),
            (FinancialRole.PORTFOLIO_MANAGER, {
                "name": "Portfolio Manager",
                "description": "Constructs and optimizes client portfolios",
                "system_prompt": self._build_portfolio_manager_prompt(),
                "tools": ["asset_allocation", "optimization_engine", "rebalancing"],
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
        """Process a client advisory request through the full workflow.

        Args:
            input_data: Dictionary containing client data with at least
                ``client_id`` and basic financial information.

        Returns:
            Dictionary with portfolio recommendation results including
            asset allocation, expected return, risk level, and compliance status.
        """
        # Step 1: Client intake
        client = self._intake_client(input_data)

        # Step 2: Risk assessment
        client = self._assess_risk(client)

        # Step 3: Portfolio analysis
        portfolio_analysis = self._analyze_portfolio(client)

        # Step 4: Generate recommendations
        recommendation = self._generate_recommendations(client, portfolio_analysis)

        # Step 5: Compliance review
        recommendation = self._compliance_review(recommendation)

        # Update metrics
        self.financial_metrics.recommendations_generated += 1
        self._update_financial_metrics()

        return {
            "client_id": client.client_id,
            "client": {
                "name": client.name,
                "risk_tolerance": client.risk_tolerance.value,
                "risk_score": client.risk_score,
                "investment_horizon": client.investment_horizon_years,
            },
            "recommendation": {
                "id": recommendation.id,
                "asset_allocation": recommendation.asset_allocation,
                "expected_return": recommendation.expected_return,
                "risk_level": recommendation.risk_level,
                "status": recommendation.status.value,
                "recommendations": recommendation.recommendations,
                "rationale": recommendation.rationale,
            },
            "compliance": {
                "status": recommendation.status.value,
                "notes": recommendation.compliance_notes,
            },
            "portfolio_analysis": portfolio_analysis,
        }

    def _on_teardown(self) -> None:
        """Clean up financial advisor solution resources."""
        self._agents.clear()
        self._clients.clear()
        self._recommendations.clear()
        self._recommendation_count = 0
        self._client_count = 0

    # ------------------------------------------------------------------
    # Workflow steps
    # ------------------------------------------------------------------

    def _intake_client(self, data: Dict[str, Any]) -> ClientProfile:
        """Step 1: Create client profile from intake data."""
        self._client_count += 1
        client_id = data.get("client_id", f"client-{self._client_count}")

        goals = data.get("goals", [])
        parsed_goals = []
        for goal in goals:
            try:
                parsed_goals.append(FinancialGoal(goal))
            except ValueError:
                pass

        client = ClientProfile(
            client_id=client_id,
            name=data.get("name", ""),
            age=data.get("age", 40),
            annual_income=data.get("annual_income", 0.0),
            net_worth=data.get("net_worth", 0.0),
            investment_horizon_years=data.get("investment_horizon_years", 10),
            risk_tolerance=RiskTolerance(
                data.get("risk_tolerance", RiskTolerance.MODERATE.value)
            ),
            goals=parsed_goals,
            existing_portfolio=data.get("existing_portfolio", {}),
        )
        self._clients[client_id] = client
        return client

    def _assess_risk(self, client: ClientProfile) -> ClientProfile:
        """Step 2: Assess client risk tolerance and capacity."""
        # Calculate risk score based on multiple factors
        base_score = {
            RiskTolerance.CONSERVATIVE: 20,
            RiskTolerance.MODERATELY_CONSERVATIVE: 35,
            RiskTolerance.MODERATE: 50,
            RiskTolerance.MODERATELY_AGGRESSIVE: 65,
            RiskTolerance.AGGRESSIVE: 80,
        }[client.risk_tolerance]

        # Adjust for age (younger = higher capacity)
        age_factor = max(0.7, min(1.3, (65 - client.age) / 30))
        # Adjust for investment horizon
        horizon_factor = min(1.3, client.investment_horizon_years / 10)
        # Adjust for income stability proxy (net worth ratio)
        if client.annual_income > 0:
            nw_ratio = client.net_worth / client.annual_income
            nw_factor = min(1.2, max(0.8, nw_ratio / 5))
        else:
            nw_factor = 1.0

        risk_score = base_score * age_factor * horizon_factor * nw_factor
        client.risk_score = max(0.0, min(100.0, risk_score))
        return client

    def _analyze_portfolio(self, client: ClientProfile) -> Dict[str, Any]:
        """Step 3: Analyze current portfolio and market conditions."""
        portfolio = client.existing_portfolio
        total_value = sum(portfolio.values()) if portfolio else 0.0

        # Calculate current allocation
        allocation = {}
        if total_value > 0:
            for asset, value in portfolio.items():
                allocation[asset] = round(value / total_value * 100, 2)

        # Simulated market analysis
        analysis = {
            "current_value": total_value,
            "current_allocation": allocation,
            "diversification_score": min(100.0, len(allocation) * 15),
            "benchmark": self.config.get("default_benchmark", "SP500"),
            "market_outlook": "neutral",
            "sector_exposure": {
                "technology": 25.0,
                "healthcare": 15.0,
                "financials": 12.0,
                "consumer": 18.0,
                "other": 30.0,
            },
        }
        return analysis

    def _generate_recommendations(
        self,
        client: ClientProfile,
        analysis: Dict[str, Any],
    ) -> PortfolioRecommendation:
        """Step 4: Generate tailored investment recommendations."""
        self._recommendation_count += 1
        rec_id = f"rec-{self._recommendation_count}"

        # Generate asset allocation based on risk score
        risk_score = client.risk_score
        allocation = self._calculate_asset_allocation(risk_score)

        # Expected return estimate based on risk
        expected_return = 3.0 + (risk_score / 100) * 7.0  # 3% to 10% range

        # Generate recommendations list
        recommendations = [
            f"Allocate {allocation.get('equities', 0):.1f}% to equities for growth exposure",
            f"Allocate {allocation.get('fixed_income', 0):.1f}% to fixed income for stability",
            f"Allocate {allocation.get('alternatives', 0):.1f}% to alternatives for diversification",
            f"Maintain {allocation.get('cash', 0):.1f}% in cash for liquidity",
            f"Rebalance portfolio {self.config.get('rebalance_frequency', 'quarterly')}",
        ]

        rationale = (
            f"Based on a risk score of {risk_score:.1f} and an investment "
            f"horizon of {client.investment_horizon_years} years, this "
            f"portfolio allocation targets an expected annual return of "
            f"{expected_return:.1f}% while maintaining an appropriate "
            f"risk level for the client's profile."
        )

        recommendation = PortfolioRecommendation(
            id=rec_id,
            client_id=client.client_id,
            asset_allocation=allocation,
            expected_return=round(expected_return, 2),
            risk_level=risk_score,
            recommendations=recommendations,
            rationale=rationale,
            status=RecommendationStatus.COMPLIANCE_PENDING,
            fees_estimate=1.0,  # 1% annual fee estimate
        )
        self._recommendations[rec_id] = recommendation
        return recommendation

    def _calculate_asset_allocation(self, risk_score: float) -> Dict[str, float]:
        """Calculate target asset allocation based on risk score."""
        # Linear interpolation between conservative and aggressive portfolios
        risk_ratio = risk_score / 100.0

        allocation = {
            "equities": round(20.0 + risk_ratio * 60.0, 1),       # 20% to 80%
            "fixed_income": round(50.0 - risk_ratio * 35.0, 1),   # 50% to 15%
            "alternatives": round(10.0 + risk_ratio * 15.0, 1),   # 10% to 25%
            "cash": round(20.0 - risk_ratio * 15.0, 1),           # 20% to 5%
        }

        # Ensure total = 100%
        total = sum(allocation.values())
        if total > 0:
            allocation = {k: round(v / total * 100, 1) for k, v in allocation.items()}

        return allocation

    def _compliance_review(
        self,
        recommendation: PortfolioRecommendation,
    ) -> PortfolioRecommendation:
        """Step 5: Review recommendation for regulatory compliance."""
        compliance_level = self.config.get("compliance_level", "standard")
        jurisdiction = self.config.get("jurisdiction", "us")
        max_risk = self.config.get("max_risk_score", 85)

        issues = []

        # Check risk level
        if recommendation.risk_level > max_risk:
            issues.append(
                f"Portfolio risk level ({recommendation.risk_level:.1f}) exceeds "
                f"maximum allowed ({max_risk})"
            )

        # Check concentration limits
        for asset, pct in recommendation.asset_allocation.items():
            if pct > 60:
                issues.append(f"Over-concentration in {asset}: {pct}%")

        # Jurisdiction-specific checks
        if jurisdiction == "eu" and compliance_level in ("strict", "enhanced"):
            if recommendation.fees_estimate > 1.5:
                issues.append("Fee structure requires additional disclosure under MiFID II")

        # Determine pass/fail
        if issues:
            recommendation.status = RecommendationStatus.COMPLIANCE_REJECTED
            recommendation.compliance_notes = "; ".join(issues)
        else:
            recommendation.status = RecommendationStatus.COMPLIANCE_APPROVED
            recommendation.compliance_notes = (
                f"Compliance check passed under {jurisdiction} jurisdiction "
                f"at {compliance_level} level."
            )

        return recommendation

    # ------------------------------------------------------------------
    # Agent prompts
    # ------------------------------------------------------------------

    def _build_financial_analyst_prompt(self) -> str:
        """Build the system prompt for the financial analyst role."""
        return (
            "You are a senior financial analyst specializing in portfolio "
            "analysis and market research. Analyze market trends, evaluate "
            "investment opportunities, and provide data-driven insights. "
            "Always ground your analysis in fundamental and technical data. "
            "Clearly distinguish between facts and opinions."
        )

    def _build_risk_assessor_prompt(self) -> str:
        """Build the system prompt for the risk assessor role."""
        return (
            "You are a risk assessment specialist for wealth management. "
            "Evaluate client risk tolerance, capacity, and portfolio risk "
            "using established frameworks including modern portfolio theory, "
            "value-at-risk, and stress testing. Provide clear risk ratings "
            "and explain the methodology used."
        )

    def _build_compliance_officer_prompt(self) -> str:
        """Build the system prompt for the compliance officer role."""
        jurisdiction = self.config.get("jurisdiction", "us")
        return (
            f"You are a compliance officer for a financial advisory firm "
            f"operating under {jurisdiction} jurisdiction. Review investment "
            f"recommendations for regulatory compliance, suitability, and "
            f"disclosure requirements. Flag any issues and provide specific "
            f"guidance for remediation."
        )

    def _build_portfolio_manager_prompt(self) -> str:
        """Build the system prompt for the portfolio manager role."""
        return (
            "You are an experienced portfolio manager responsible for "
            "constructing and optimizing client portfolios. Use modern "
            "portfolio theory and asset allocation strategies to build "
            "diversified portfolios aligned with each client's risk profile "
            "and financial goals. Consider tax efficiency and costs."
        )

    # ------------------------------------------------------------------
    # Metrics
    # ------------------------------------------------------------------

    def _update_financial_metrics(self) -> None:
        """Update financial-specific metrics."""
        if self._recommendations:
            approved = sum(
                1 for r in self._recommendations.values()
                if r.status == RecommendationStatus.COMPLIANCE_APPROVED
            )
            total = len(self._recommendations)
            self.financial_metrics.compliance_rate = approved / total if total > 0 else 1.0

            avg_return = sum(
                r.expected_return for r in self._recommendations.values()
            ) / total
            self.financial_metrics.avg_portfolio_return = avg_return

            avg_risk = sum(
                r.risk_level for r in self._recommendations.values()
            ) / total
            self.financial_metrics.avg_risk_score = avg_risk

        self.financial_metrics.portfolios_managed = len(self._clients)

    def get_financial_metrics(self) -> Dict[str, Any]:
        """Get the current financial-specific metrics.

        Returns:
            Dictionary with financial metrics including portfolio return,
            risk scores, compliance rate, and client satisfaction.
        """
        return self.financial_metrics.to_dict()
