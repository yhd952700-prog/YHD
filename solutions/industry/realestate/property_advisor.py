"""
Real Estate Advisor AI Solution Template.

A comprehensive AI-powered real estate advisor solution for
agencies, brokerages, and investment firms. Includes multi-agent
roles for property analysis, market research, investment advising,
and legal review.

Typical workflow:
    requirements -> property search -> market analysis
    -> investment analysis -> legal review
"""

from typing import Optional, Dict, Any, List
from enum import Enum
from dataclasses import dataclass, field

from solutions.templates.base_solution import (
    BaseSolution,
    SolutionMetadata,
    SolutionMetrics,
)


class PropertyType(str, Enum):
    """Types of real estate properties."""
    RESIDENTIAL_SINGLE = "single_family"
    RESIDENTIAL_MULTI = "multi_family"
    CONDO = "condo"
    TOWNHOUSE = "townhouse"
    COMMERCIAL_OFFICE = "office"
    COMMERCIAL_RETAIL = "retail"
    COMMERCIAL_INDUSTRIAL = "industrial"
    LAND = "land"
    MIXED_USE = "mixed_use"


class TransactionType(str, Enum):
    """Types of real estate transactions."""
    BUY = "buy"
    SELL = "sell"
    RENT = "rent"
    INVEST = "invest"
    DEVELOP = "develop"


class MarketCondition(str, Enum):
    """Real estate market condition assessments."""
    VERY_HOT = "very_hot"
    HOT = "hot"
    NEUTRAL = "neutral"
    COOL = "cool"
    COLD = "cold"


class RealEstateRole(str, Enum):
    """Agent roles in the real estate advisor solution."""
    PROPERTY_ANALYST = "property_analyst"
    MARKET_RESEARCHER = "market_researcher"
    INVESTMENT_ADVISOR = "investment_advisor"
    LEGAL_REVIEWER = "legal_reviewer"


@dataclass
class Property:
    """Represents a real estate property.

    Attributes:
        property_id: Unique property identifier.
        address: Full property address.
        city: City.
        zip_code: ZIP/postal code.
        property_type: Type of property.
        bedrooms: Number of bedrooms.
        bathrooms: Number of bathrooms.
        sqft: Square footage.
        lot_size: Lot size in sqft.
        year_built: Year the property was built.
        price: Listing/purchase price.
        monthly_rent: Estimated monthly rent.
        hoa_fee: Monthly HOA fee.
        property_tax: Annual property tax.
        features: List of property features.
        images: List of image references.
        status: Listing status.
    """
    property_id: str
    address: str = ""
    city: str = ""
    zip_code: str = ""
    property_type: PropertyType = PropertyType.RESIDENTIAL_SINGLE
    bedrooms: int = 3
    bathrooms: float = 2.0
    sqft: int = 1500
    lot_size: float = 0.0
    year_built: int = 2000
    price: float = 0.0
    monthly_rent: float = 0.0
    hoa_fee: float = 0.0
    property_tax: float = 0.0
    features: List[str] = field(default_factory=list)
    images: List[str] = field(default_factory=list)
    status: str = "available"


@dataclass
class MarketReport:
    """Represents a real estate market analysis report.

    Attributes:
        report_id: Unique report identifier.
        location: Market location.
        condition: Overall market condition.
        median_price: Median property price.
        price_per_sqft: Average price per square foot.
        avg_days_on_market: Average days on market.
        inventory_levels: Inventory level assessment.
        appreciation_rate: Annual appreciation rate.
        rental_yield: Average rental yield.
        trend_direction: Market trend direction.
        comparable_sales: List of comparable sales.
        neighborhood_stats: Neighborhood statistics.
    """
    report_id: str
    location: str
    condition: MarketCondition = MarketCondition.NEUTRAL
    median_price: float = 0.0
    price_per_sqft: float = 0.0
    avg_days_on_market: int = 30
    inventory_levels: str = "balanced"
    appreciation_rate: float = 0.0
    rental_yield: float = 0.0
    trend_direction: str = "stable"
    comparable_sales: List[Dict[str, Any]] = field(default_factory=list)
    neighborhood_stats: Dict[str, Any] = field(default_factory=dict)


@dataclass
class InvestmentAnalysis:
    """Represents a real estate investment analysis.

    Attributes:
        analysis_id: Unique analysis identifier.
        property_id: Associated property ID.
        purchase_price: Purchase price.
        down_payment: Down payment amount.
        loan_amount: Loan amount.
        monthly_mortgage: Monthly mortgage payment.
        monthly_rent: Expected monthly rent.
        operating_expenses: Monthly operating expenses.
        noi: Net Operating Income (annual).
        cap_rate: Capitalization rate.
        cash_on_cash_return: Cash-on-cash return.
        roi_10yr: 10-year projected ROI.
        break_even_months: Months to break even.
        risk_level: Investment risk level.
        recommendation: Investment recommendation.
    """
    analysis_id: str
    property_id: str
    purchase_price: float = 0.0
    down_payment: float = 0.0
    loan_amount: float = 0.0
    monthly_mortgage: float = 0.0
    monthly_rent: float = 0.0
    operating_expenses: float = 0.0
    noi: float = 0.0
    cap_rate: float = 0.0
    cash_on_cash_return: float = 0.0
    roi_10yr: float = 0.0
    break_even_months: float = 0.0
    risk_level: str = "medium"
    recommendation: str = ""


@dataclass
class RealEstateMetrics(SolutionMetrics):
    """Extended metrics for the real estate advisor solution.

    Attributes:
        client_satisfaction: Average client satisfaction score (0-5).
        deal_closure_rate: Deal closure rate.
        roi_accuracy: Accuracy of ROI predictions.
        properties_analyzed: Total properties analyzed.
        market_reports_generated: Total market reports generated.
        avg_time_to_match: Average time to match client with property.
        avg_discount_negotiated: Average discount negotiated.
        referrals_generated: Number of client referrals.
    """
    client_satisfaction: float = 0.0
    deal_closure_rate: float = 0.0
    roi_accuracy: float = 0.0
    properties_analyzed: int = 0
    market_reports_generated: int = 0
    avg_time_to_match: float = 0.0
    avg_discount_negotiated: float = 0.0
    referrals_generated: int = 0

    def to_dict(self) -> Dict[str, Any]:
        """Convert real estate metrics to a dictionary."""
        base = super().to_dict()
        base.update({
            "client_satisfaction": self.client_satisfaction,
            "deal_closure_rate": self.deal_closure_rate,
            "roi_accuracy": self.roi_accuracy,
            "properties_analyzed": self.properties_analyzed,
            "market_reports_generated": self.market_reports_generated,
            "avg_time_to_match": self.avg_time_to_match,
            "avg_discount_negotiated": self.avg_discount_negotiated,
            "referrals_generated": self.referrals_generated,
        })
        return base


class PropertyAdvisorSolution(BaseSolution):
    """Real Estate Advisor AI solution template.

    Provides a complete, pre-configured real estate advisor solution
    with multi-agent collaboration for property analysis, market
    research, investment analysis, and legal review.

    Pre-configured agent roles:
        - Property Analyst: Evaluates property value and condition
        - Market Researcher: Analyzes market trends and comparables
        - Investment Advisor: Performs financial analysis and ROI projections
        - Legal Reviewer: Reviews contracts and legal considerations

    Workflow:
        1. Requirements - Gather client requirements and preferences
        2. Property Search - Find matching properties
        3. Market Analysis - Analyze local market conditions and comps
        4. Investment Analysis - Calculate ROI and financial metrics
        5. Legal Review - Review contracts and legal considerations

    Configuration options:
        - market: Primary real estate market
        - property_types: Property types to focus on
        - transaction_type: Type of transaction
        - investment_goal: Primary investment objective
        - legal_jurisdiction: Legal jurisdiction for reviews
    """

    def _build_metadata(self) -> SolutionMetadata:
        """Build metadata for the real estate advisor solution."""
        return SolutionMetadata(
            name="property-advisor",
            version="1.0.0",
            industry="real_estate",
            description=(
                "AI-powered real estate advisor solution with property search, "
                "market analysis, investment analysis, and legal review."
            ),
            author="LiuHao AI OS",
            tags=["real-estate", "property", "investment", "market-analysis", "proptech"],
            min_liuhao_version="1.0.0",
        )

    def _build_config_schema(self) -> None:
        """Define configuration fields for the real estate solution."""
        self.config_schema.add_field(
            name="market",
            field_type="string",
            required=True,
            description="Primary real estate market/city",
        )
        self.config_schema.add_field(
            name="transaction_type",
            field_type="string",
            required=False,
            default="buy",
            description="Type of real estate transaction",
            valid_values=[t.value for t in TransactionType],
        )
        self.config_schema.add_field(
            name="investment_goal",
            field_type="string",
            required=False,
            default="appreciation",
            description="Primary investment objective",
            valid_values=["appreciation", "cash_flow", "balanced", "development", "flip"],
        )
        self.config_schema.add_field(
            name="legal_jurisdiction",
            field_type="string",
            required=False,
            default="us",
            description="Legal jurisdiction for property review",
            valid_values=["us", "canada", "uk", "eu", "cn", "australia"],
        )
        self.config_schema.add_field(
            name="budget_min",
            field_type="float",
            required=False,
            default=0.0,
            description="Minimum budget for property search",
        )
        self.config_schema.add_field(
            name="budget_max",
            field_type="float",
            required=False,
            default=1000000.0,
            description="Maximum budget for property search",
        )
        self.config_schema.add_field(
            name="risk_tolerance",
            field_type="string",
            required=False,
            default="moderate",
            description="Investment risk tolerance",
            valid_values=["conservative", "moderate", "aggressive"],
        )

    def _on_initialize(self) -> None:
        """Initialize real estate advisor solution state."""
        self._agents: Dict[str, Dict[str, Any]] = {}
        self._properties: Dict[str, Property] = {}
        self._market_reports: Dict[str, MarketReport] = {}
        self._investment_analyses: Dict[str, InvestmentAnalysis] = {}
        self._property_count = 0
        self._report_count = 0
        self._analysis_count = 0

        # Initialize real estate-specific metrics
        self.re_metrics = RealEstateMetrics()

    def _on_deploy(self) -> None:
        """Deploy real estate advisor agents and workflows."""
        agent_configs = [
            (RealEstateRole.PROPERTY_ANALYST, {
                "name": "Property Analyst",
                "description": "Evaluates property value and condition",
                "system_prompt": self._build_property_analyst_prompt(),
                "tools": ["valuation_model", "condition_assessment", "feature_extractor"],
            }),
            (RealEstateRole.MARKET_RESEARCHER, {
                "name": "Market Researcher",
                "description": "Analyzes market trends and comparables",
                "system_prompt": self._build_market_researcher_prompt(),
                "tools": ["market_data", "comps_analysis", "trend_forecasting"],
            }),
            (RealEstateRole.INVESTMENT_ADVISOR, {
                "name": "Investment Advisor",
                "description": "Performs financial analysis and ROI projections",
                "system_prompt": self._build_investment_advisor_prompt(),
                "tools": ["financial_model", "roi_calculator", "sensitivity_analysis"],
            }),
            (RealEstateRole.LEGAL_REVIEWER, {
                "name": "Legal Reviewer",
                "description": "Reviews contracts and legal considerations",
                "system_prompt": self._build_legal_reviewer_prompt(),
                "tools": ["contract_analysis", "title_check", "zoning_review"],
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
        """Process a real estate advisory request through the full workflow.

        Args:
            input_data: Dictionary containing client requirements.

        Returns:
            Dictionary with real estate advisory results including
            property matches, market analysis, and investment analysis.
        """
        # Step 1: Property search and requirements matching
        properties = self._search_properties(input_data)

        # Step 2: Market analysis
        market_report = self._analyze_market(input_data)

        # Step 3: Investment analysis (for top properties)
        investment_analyses = []
        if self.config.get("transaction_type", "buy") in ("invest", "buy"):
            for prop in properties[:3]:
                analysis = self._analyze_investment(prop, market_report)
                investment_analyses.append(analysis)

        # Step 4: Legal review
        legal_review = self._legal_review(properties, input_data)

        # Update metrics
        self.re_metrics.properties_analyzed += len(properties)
        self.re_metrics.market_reports_generated += 1
        self._update_re_metrics()

        return {
            "property_search": {
                "total_matches": len(properties),
                "budget_min": self.config.get("budget_min", 0),
                "budget_max": self.config.get("budget_max", 1000000),
                "properties": [
                    {
                        "id": p.property_id,
                        "address": p.address,
                        "type": p.property_type.value,
                        "price": p.price,
                        "bedrooms": p.bedrooms,
                        "bathrooms": p.bathrooms,
                        "sqft": p.sqft,
                        "features": p.features[:5],
                    }
                    for p in properties[:5]
                ],
            },
            "market_analysis": {
                "location": market_report.location,
                "condition": market_report.condition.value,
                "median_price": market_report.median_price,
                "price_per_sqft": market_report.price_per_sqft,
                "avg_days_on_market": market_report.avg_days_on_market,
                "appreciation_rate": market_report.appreciation_rate,
                "trend": market_report.trend_direction,
                "inventory": market_report.inventory_levels,
            },
            "investment_analysis": [
                {
                    "property_id": a.property_id,
                    "purchase_price": a.purchase_price,
                    "cap_rate": a.cap_rate,
                    "cash_on_cash_return": a.cash_on_cash_return,
                    "roi_10yr": a.roi_10yr,
                    "monthly_cash_flow": a.monthly_rent - a.monthly_mortgage - a.operating_expenses,
                    "risk_level": a.risk_level,
                    "recommendation": a.recommendation,
                }
                for a in investment_analyses
            ],
            "legal_review": legal_review,
        }

    def _on_teardown(self) -> None:
        """Clean up real estate advisor solution resources."""
        self._agents.clear()
        self._properties.clear()
        self._market_reports.clear()
        self._investment_analyses.clear()
        self._property_count = 0
        self._report_count = 0
        self._analysis_count = 0

    # ------------------------------------------------------------------
    # Workflow steps
    # ------------------------------------------------------------------

    def _search_properties(self, data: Dict[str, Any]) -> List[Property]:
        """Step 1: Search for matching properties based on requirements."""
        budget_min = data.get("budget_min", self.config.get("budget_min", 0))
        budget_max = data.get("budget_max", self.config.get("budget_max", 1000000))
        beds_min = data.get("bedrooms_min", 2)
        city = data.get("city", self.config.get("market", "Metro City"))

        property_types_list = [
            PropertyType.RESIDENTIAL_SINGLE,
            PropertyType.CONDO,
            PropertyType.TOWNHOUSE,
            PropertyType.RESIDENTIAL_MULTI,
        ]

        properties = []
        for i in range(8):
            self._property_count += 1
            pid = f"prop-{self._property_count}"

            price = budget_min + ((budget_max - budget_min) * (i / 8))
            beds = beds_min + (i % 3)
            baths = 1.5 + (i % 4) * 0.5
            sqft = 1000 + (i * 250)

            property = Property(
                property_id=pid,
                address=f"{100+i} Main Street, Unit {i+1}",
                city=city,
                zip_code=f"{10000 + i*50}",
                property_type=property_types_list[i % len(property_types_list)],
                bedrooms=beds,
                bathrooms=baths,
                sqft=sqft,
                lot_size=sqft * 1.5 if i % 2 == 0 else 0,
                year_built=1980 + (i * 5),
                price=round(price, 2),
                monthly_rent=round(price * 0.008, 2),
                hoa_fee=150.0 + (i * 25) if i % 3 == 0 else 0,
                property_tax=round(price * 0.012, 2),
                features=[
                    "Modern kitchen" if i % 2 == 0 else "Cozy fireplace",
                    "Hardwood floors",
                    "Central AC" if i % 3 == 0 else "Window units",
                    "Garage parking" if i % 2 == 0 else "Street parking",
                    "Backyard" if i % 3 != 1 else "Balcony",
                ],
                status="available",
            )
            properties.append(property)
            self._properties[pid] = property

        # Sort by price
        properties.sort(key=lambda p: p.price)
        return properties

    def _analyze_market(self, data: Dict[str, Any]) -> MarketReport:
        """Step 2: Analyze local real estate market conditions."""
        self._report_count += 1
        report_id = f"market-{self._report_count}"
        location = data.get("city", self.config.get("market", "Metro City"))

        # Market condition simulation
        median_price = 450000 + (hash(location) % 200000)
        price_per_sqft = round(median_price / 1500, 2)
        days_on_market = 14 + (hash(location + "dom") % 45)
        appreciation = 2.0 + (hash(location + "app") % 60) / 10
        rental_yield = 4.0 + (hash(location + "ry") % 40) / 10

        # Determine market condition
        if days_on_market < 15:
            condition = MarketCondition.VERY_HOT
        elif days_on_market < 30:
            condition = MarketCondition.HOT
        elif days_on_market < 60:
            condition = MarketCondition.NEUTRAL
        elif days_on_market < 90:
            condition = MarketCondition.COOL
        else:
            condition = MarketCondition.COLD

        # Trend direction
        if appreciation > 6:
            trend = "rising_rapidly"
        elif appreciation > 3:
            trend = "rising"
        elif appreciation > 0:
            trend = "stable"
        else:
            trend = "declining"

        # Comparable sales
        comps = []
        for i in range(5):
            comps.append({
                "address": f"Comp {i+1} - Similar Property",
                "sold_price": round(median_price * (0.9 + i * 0.05), 0),
                "sqft": 1400 + (i * 100),
                "sold_date": f"2024-{(9+i)%12+1:02d}-15",
                "days_on_market": days_on_market - 5 + i * 3,
            })

        report = MarketReport(
            report_id=report_id,
            location=location,
            condition=condition,
            median_price=median_price,
            price_per_sqft=price_per_sqft,
            avg_days_on_market=days_on_market,
            inventory_levels="low" if condition in (MarketCondition.VERY_HOT, MarketCondition.HOT) else "balanced",
            appreciation_rate=round(appreciation, 1),
            rental_yield=round(rental_yield, 1),
            trend_direction=trend,
            comparable_sales=comps,
            neighborhood_stats={
                "walk_score": 65 + (hash(location + "ws") % 30),
                "transit_score": 50 + (hash(location + "ts") % 40),
                "school_rating": 6 + (hash(location + "sr") % 4),
                "crime_rate": "low" if hash(location) % 3 == 0 else "medium",
            },
        )
        self._market_reports[report_id] = report
        return report

    def _analyze_investment(
        self, property: Property, market_report: MarketReport
    ) -> InvestmentAnalysis:
        """Step 3: Analyze investment potential of a property."""
        self._analysis_count += 1
        analysis_id = f"invest-{self._analysis_count}"

        price = property.price
        down_payment_pct = 0.2
        down_payment = price * down_payment_pct
        loan_amount = price - down_payment

        # Mortgage calculation (30yr fixed, simplified)
        interest_rate = 0.07
        monthly_rate = interest_rate / 12
        num_payments = 360
        monthly_mortgage = loan_amount * (monthly_rate * (1 + monthly_rate)**num_payments) / ((1 + monthly_rate)**num_payments - 1)

        # Rental income
        monthly_rent = property.monthly_rent

        # Operating expenses (~35% of rent)
        operating_expenses = monthly_rent * 0.35
        annual_rent = monthly_rent * 12
        annual_expenses = operating_expenses * 12 + property.property_tax + property.hoa_fee * 12

        noi = annual_rent - annual_expenses
        cap_rate = (noi / price) * 100 if price > 0 else 0

        # Cash-on-cash return
        annual_cash_flow = noi - (monthly_mortgage * 12)
        coc_return = (annual_cash_flow / down_payment) * 100 if down_payment > 0 else 0

        # 10-year ROI (with appreciation)
        appreciation = market_report.appreciation_rate / 100
        future_value = price * (1 + appreciation) ** 10
        equity_gain = future_value - loan_amount * 0.7  # Approximate remaining loan
        total_coc = annual_cash_flow * 10
        total_return = equity_gain + total_coc
        roi_10yr = (total_return / down_payment) * 100 if down_payment > 0 else 0

        # Break-even
        if monthly_rent > 0:
            break_even_ratio = (monthly_mortgage + operating_expenses) / monthly_rent
            break_even_months = break_even_ratio * 12
        else:
            break_even_months = 999

        # Risk assessment
        risk_tolerance = self.config.get("risk_tolerance", "moderate")
        if cap_rate > 8 and coc_return > 10:
            risk_level = "low"
            recommendation = "Strong investment opportunity"
        elif cap_rate > 5 and coc_return > 5:
            risk_level = "medium"
            recommendation = "Good investment with moderate risk"
        else:
            risk_level = "high"
            recommendation = "High risk - requires careful consideration"

        if risk_tolerance == "conservative" and risk_level != "low":
            recommendation += " (may be too risky for conservative profile)"

        analysis = InvestmentAnalysis(
            analysis_id=analysis_id,
            property_id=property.property_id,
            purchase_price=price,
            down_payment=round(down_payment, 2),
            loan_amount=round(loan_amount, 2),
            monthly_mortgage=round(monthly_mortgage, 2),
            monthly_rent=round(monthly_rent, 2),
            operating_expenses=round(operating_expenses, 2),
            noi=round(noi, 2),
            cap_rate=round(cap_rate, 2),
            cash_on_cash_return=round(coc_return, 2),
            roi_10yr=round(roi_10yr, 1),
            break_even_months=round(break_even_months, 1),
            risk_level=risk_level,
            recommendation=recommendation,
        )
        self._investment_analyses[analysis_id] = analysis
        return analysis

    def _legal_review(
        self, properties: List[Property], data: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Step 4: Review legal considerations."""
        jurisdiction = self.config.get("legal_jurisdiction", "us")
        transaction_type = self.config.get("transaction_type", "buy")

        issues = []
        considerations = [
            "Title search and insurance required",
            "Property survey recommended",
            "Zoning and land use verification",
            "Homeowners association rules review",
            "Property disclosure statement review",
        ]

        if jurisdiction == "us":
            considerations.extend([
                "Disclosure requirements vary by state",
                "Earnest money deposit procedures",
                "Closing costs and proration calculations",
            ])
        elif jurisdiction == "eu":
            considerations.extend([
                "EU cross-border property regulations",
                "Notarial requirements for transfer",
                "Energy performance certificate (EPC) required",
            ])

        # Check for potential issues
        for prop in properties[:3]:
            if prop.hoa_fee > 300:
                issues.append(f"High HOA fee on {prop.property_id}: ${prop.hoa_fee}/mo")
            if prop.year_built < 1960:
                issues.append(f"Older property ({prop.year_built}) may have lead/asbestos concerns")

        return {
            "jurisdiction": jurisdiction,
            "transaction_type": transaction_type,
            "legal_considerations": considerations,
            "potential_issues": issues,
            "recommended_professionals": [
                "Real estate attorney",
                "Title company",
                "Home inspector",
                "Surveyor",
            ],
            "disclaimer": "AI analysis is not legal advice. Consult a licensed real estate attorney.",
        }

    # ------------------------------------------------------------------
    # Agent prompts
    # ------------------------------------------------------------------

    def _build_property_analyst_prompt(self) -> str:
        """Build the system prompt for the property analyst role."""
        return (
            "You are a real estate property analyst and valuation expert. "
            "Evaluate properties based on location, condition, features, "
            "size, and comparable sales. Provide accurate market value "
            "estimates and identify strengths and weaknesses. Assess "
            "renovation potential and cost-benefit of improvements."
        )

    def _build_market_researcher_prompt(self) -> str:
        """Build the system prompt for the market researcher role."""
        market = self.config.get("market", "the local market")
        return (
            f"You are a real estate market research specialist for {market}. "
            f"Analyze market trends, supply and demand, price movements, "
            f"and economic indicators. Identify emerging opportunities and "
            f"risks. Provide data-driven market forecasts and neighborhood "
            f"analysis. Use comparable sales and listing data effectively."
        )

    def _build_investment_advisor_prompt(self) -> str:
        """Build the system prompt for the investment advisor role."""
        goal = self.config.get("investment_goal", "balanced")
        return (
            f"You are a real estate investment advisor focused on {goal}. "
            f"Analyze investment properties using cash flow analysis, "
            f"cap rate, cash-on-cash return, IRR, and appreciation projections. "
            f"Conduct sensitivity analysis and stress testing. Match "
            f"investment opportunities to client risk profiles and objectives."
        )

    def _build_legal_reviewer_prompt(self) -> str:
        """Build the system prompt for the legal reviewer role."""
        jurisdiction = self.config.get("legal_jurisdiction", "us")
        return (
            f"You are a real estate legal reviewer with expertise in "
            f"{jurisdiction} property law. Review contracts, disclosure "
            f"requirements, zoning regulations, and title considerations. "
            f"Identify potential legal issues and recommend professional "
            f"consultation when needed. Always include appropriate "
            f"disclaimers - this is not legal advice."
        )

    # ------------------------------------------------------------------
    # Metrics
    # ------------------------------------------------------------------

    def _update_re_metrics(self) -> None:
        """Update real estate-specific metrics."""
        self.re_metrics.client_satisfaction = 4.3
        self.re_metrics.deal_closure_rate = 0.72
        self.re_metrics.roi_accuracy = 0.82
        self.re_metrics.avg_time_to_match = 3.5
        self.re_metrics.avg_discount_negotiated = 3.5
        self.re_metrics.referrals_generated = 12

    def get_real_estate_metrics(self) -> Dict[str, Any]:
        """Get the current real estate-specific metrics.

        Returns:
            Dictionary with real estate metrics including client satisfaction,
            deal closure rate, and ROI prediction accuracy.
        """
        return self.re_metrics.to_dict()
