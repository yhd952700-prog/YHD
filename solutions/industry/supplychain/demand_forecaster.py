"""
Demand Forecasting AI Solution Template.

A comprehensive AI-powered demand forecasting system for
supply chain management, retail, and operations teams.
Includes multi-agent roles for demand planner, data analyst,
market researcher, and inventory coordinator.

Typical workflow:
    historical data -> trend analysis -> market factors
    -> forecast generation -> accuracy tracking
"""

from typing import Optional, Dict, Any, List
from enum import Enum
from dataclasses import dataclass, field

from solutions.templates.base_solution import (
    BaseSolution,
    SolutionMetadata,
    SolutionMetrics,
)


class ForecastMethod(str, Enum):
    """Forecasting methods."""
    ARIMA = "arima"
    EXPONENTIAL_SMOOTHING = "exponential_smoothing"
    LINEAR_REGRESSION = "linear_regression"
    MACHINE_LEARNING = "machine_learning"
    PROPHET = "prophet"
    LSTM = "lstm"
    MOVING_AVERAGE = "moving_average"
    SEASONAL_DECOMPOSITION = "seasonal_decomposition"
    ENSEMBLE = "ensemble"


class ForecastHorizon(str, Enum):
    """Forecast time horizons."""
    SHORT_TERM = "short_term"  # 0-3 months
    MEDIUM_TERM = "medium_term"  # 3-12 months
    LONG_TERM = "long_term"  # 12+ months


class IndustrySector(str, Enum):
    """Industry sectors for demand forecasting."""
    RETAIL = "retail"
    ECOMMERCE = "ecommerce"
    MANUFACTURING = "manufacturing"
    WHOLESALE = "wholesale"
    FOOD_BEVERAGE = "food_beverage"
    PHARMA = "pharma"
    CONSUMER_GOODS = "consumer_goods"
    ELECTRONICS = "electronics"
    FASHION = "fashion"


class DemandPattern(str, Enum):
    """Demand pattern types."""
    STABLE = "stable"
    GROWING = "growing"
    DECLINING = "declining"
    SEASONAL = "seasonal"
    CYCLICAL = "cyclical"
    ERRATIC = "erratic"
    LUMPY = "lumpy"
    INTERMITTENT = "intermittent"


class SupplyChainRole(str, Enum):
    """Agent roles in the demand forecasting solution."""
    DEMAND_PLANNER = "demand_planner"
    DATA_ANALYST = "data_analyst"
    MARKET_RESEARCHER = "market_researcher"
    INVENTORY_COORDINATOR = "inventory_coordinator"


@dataclass
class HistoricalData:
    """Represents historical demand data.

    Attributes:
        id: Unique dataset ID.
        product_id: Product/SKU identifier.
        product_name: Product name.
        category: Product category.
        time_period: Time period covered.
        data_points: Number of data points.
        total_units: Total units sold/demanded.
        avg_demand: Average demand per period.
        demand_std_dev: Standard deviation of demand.
        min_demand: Minimum demand in period.
        max_demand: Maximum demand period.
        growth_rate: Historical growth rate.
        seasonality_strength: Strength of seasonal component.
        trend_direction: Trend direction (up/down/flat).
        data_quality: Data quality score (0-10).
        outliers: Number of outlier data points.
        missing_values: Number of missing values.
    """
    id: str
    product_id: str = ""
    product_name: str = ""
    category: str = ""
    time_period: str = ""
    data_points: int = 0
    total_units: float = 0.0
    avg_demand: float = 0.0
    demand_std_dev: float = 0.0
    min_demand: float = 0.0
    max_demand: float = 0.0
    growth_rate: float = 0.0
    seasonality_strength: float = 0.0
    trend_direction: str = ""
    data_quality: float = 0.0
    outliers: int = 0
    missing_values: int = 0


@dataclass
class TrendAnalysis:
    """Represents demand trend analysis results.

    Attributes:
        id: Unique analysis ID.
        product_id: Associated product ID.
        overall_trend: Overall trend direction and magnitude.
        trend_strength: Strength of trend (0-100).
        seasonal_pattern: Seasonal pattern description.
        seasonal_peaks: Peak demand periods.
        seasonal_troughs: Low demand periods.
        cyclical_patterns: Cyclical patterns identified.
        growth_phase: Current growth phase.
        market_maturity: Market maturity level.
        trend_confidence: Confidence in trend assessment.
        pattern_type: Type of demand pattern.
        trend_components: Decomposed trend components.
        breakpoints: Points where trend changed.
    """
    id: str
    product_id: str = ""
    overall_trend: str = ""
    trend_strength: float = 0.0
    seasonal_pattern: str = ""
    seasonal_peaks: List[str] = field(default_factory=list)
    seasonal_troughs: List[str] = field(default_factory=list)
    cyclical_patterns: List[str] = field(default_factory=list)
    growth_phase: str = ""
    market_maturity: str = ""
    trend_confidence: float = 0.0
    pattern_type: DemandPattern = DemandPattern.STABLE
    trend_components: Dict[str, float] = field(default_factory=dict)
    breakpoints: List[str] = field(default_factory=list)


@dataclass
class MarketFactorAnalysis:
    """Represents market factor analysis for demand.

    Attributes:
        id: Unique analysis ID.
        product_id: Associated product ID.
        economic_factors: Economic factors affecting demand.
        market_conditions: Current market conditions.
        competitor_activity: Competitor activity and impact.
        customer_behavior: Customer behavior trends.
        seasonality_factors: Seasonality drivers.
        promotional_impact: Promotions and marketing impact.
        pricing_effects: Price elasticity effects.
        external_factors: External factors (weather, events, etc.).
        supply_constraints: Supply-side constraints.
        regulatory_impacts: Regulatory and policy impacts.
        factor_weights: Relative importance of each factor.
        lead_indicators: Leading indicators to monitor.
    """
    id: str
    product_id: str = ""
    economic_factors: List[str] = field(default_factory=list)
    market_conditions: str = ""
    competitor_activity: List[str] = field(default_factory=list)
    customer_behavior: List[str] = field(default_factory=list)
    seasonality_factors: List[str] = field(default_factory=list)
    promotional_impact: str = ""
    pricing_effects: str = ""
    external_factors: List[str] = field(default_factory=list)
    supply_constraints: List[str] = field(default_factory=list)
    regulatory_impacts: List[str] = field(default_factory=list)
    factor_weights: Dict[str, float] = field(default_factory=dict)
    lead_indicators: List[str] = field(default_factory=list)


@dataclass
class DemandForecast:
    """Represents a demand forecast.

    Attributes:
        id: Unique forecast ID.
        product_id: Associated product ID.
        forecast_method: Method used for forecasting.
        forecast_horizon: Time horizon of the forecast.
        forecast_periods: Number of periods forecasted.
        base_forecast: Base forecast values.
        upper_bound: Upper confidence bound.
        lower_bound: Lower confidence bound.
        confidence_level: Confidence level of forecast.
        forecast_accuracy: Expected forecast accuracy.
        total_forecast_units: Total forecasted units.
        period_forecasts: Forecast by time period.
        seasonal_adjustment: Seasonal adjustment applied.
        trend_adjustment: Trend adjustment applied.
        risk_factors: Factors that could affect forecast accuracy.
        upside_scenarios: Upside scenario forecasts.
        downside_scenarios: Downside scenario forecasts.
    """
    id: str
    product_id: str = ""
    forecast_method: str = ""
    forecast_horizon: str = ""
    forecast_periods: int = 0
    base_forecast: List[float] = field(default_factory=list)
    upper_bound: List[float] = field(default_factory=list)
    lower_bound: List[float] = field(default_factory=list)
    confidence_level: float = 0.0
    forecast_accuracy: float = 0.0
    total_forecast_units: float = 0.0
    period_forecasts: List[Dict[str, Any]] = field(default_factory=list)
    seasonal_adjustment: float = 0.0
    trend_adjustment: float = 0.0
    risk_factors: List[str] = field(default_factory=list)
    upside_scenarios: Dict[str, float] = field(default_factory=dict)
    downside_scenarios: Dict[str, float] = field(default_factory=dict)


@dataclass
class ForecastAccuracy:
    """Represents forecast accuracy tracking.

    Attributes:
        id: Unique accuracy record ID.
        product_id: Associated product ID.
        period: Period of accuracy measurement.
        forecast_vs_actual: Forecast vs actual values.
        mae: Mean Absolute Error.
        mse: Mean Squared Error.
        rmse: Root Mean Squared Error.
        mape: Mean Absolute Percentage Error.
        wmape: Weighted MAPE.
        bias: Forecast bias (over/under forecast).
        accuracy_rate: Overall accuracy percentage.
        trend_accuracy: Trend direction accuracy.
        seasonal_accuracy: Seasonal forecast accuracy.
        best_method: Best performing forecast method.
        improvement_opportunities: Areas for improvement.
    """
    id: str
    product_id: str = ""
    period: str = ""
    forecast_vs_actual: Dict[str, float] = field(default_factory=dict)
    mae: float = 0.0
    mse: float = 0.0
    rmse: float = 0.0
    mape: float = 0.0
    wmape: float = 0.0
    bias: float = 0.0
    accuracy_rate: float = 0.0
    trend_accuracy: float = 0.0
    seasonal_accuracy: float = 0.0
    best_method: str = ""
    improvement_opportunities: List[str] = field(default_factory=list)


@dataclass
class SupplyChainMetrics(SolutionMetrics):
    """Extended metrics for the demand forecasting solution.

    Attributes:
        forecast_accuracy: Forecast accuracy percentage.
        inventory_turnover: Inventory turnover rate.
        stockout_rate: Stockout rate percentage.
        forecasts_generated: Total forecasts generated.
        avg_mape: Average MAPE across all forecasts.
        bias: Average forecast bias.
        service_level: Customer service level.
        inventory_reduction: Inventory reduction from better forecasting.
        demand_pattern_accuracy: Demand pattern detection accuracy.
        seasonal_forecast_accuracy: Seasonal forecast accuracy.
    """
    forecast_accuracy: float = 0.0
    inventory_turnover: float = 0.0
    stockout_rate: float = 0.0
    forecasts_generated: int = 0
    avg_mape: float = 0.0
    bias: float = 0.0
    service_level: float = 0.0
    inventory_reduction: float = 0.0
    demand_pattern_accuracy: float = 0.0
    seasonal_forecast_accuracy: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        """Convert supply chain metrics to a dictionary."""
        base = super().to_dict()
        base.update({
            "forecast_accuracy": self.forecast_accuracy,
            "inventory_turnover": self.inventory_turnover,
            "stockout_rate": self.stockout_rate,
            "forecasts_generated": self.forecasts_generated,
            "avg_mape": self.avg_mape,
            "bias": self.bias,
            "service_level": self.service_level,
            "inventory_reduction": self.inventory_reduction,
            "demand_pattern_accuracy": self.demand_pattern_accuracy,
            "seasonal_forecast_accuracy": self.seasonal_forecast_accuracy,
        })
        return base


class DemandForecasterSolution(BaseSolution):
    """Demand Forecasting AI solution template.

    Provides a complete, pre-configured demand forecasting solution
    with multi-agent collaboration for historical analysis, trend
    analysis, market factor analysis, forecast generation, and
    accuracy tracking.

    Pre-configured agent roles:
        - Demand Planner: Leads overall demand planning and forecasting
        - Data Analyst: Analyzes historical data and builds models
        - Market Researcher: Analyzes market and external factors
        - Inventory Coordinator: Translates forecasts to inventory plans

    Workflow:
        1. Historical Data - Analyze past demand patterns
        2. Trend Analysis - Identify trends and seasonality
        3. Market Factors - Incorporate external market factors
        4. Forecast Generation - Generate demand forecast
        5. Accuracy Tracking - Monitor and improve forecast accuracy

    Configuration options:
        - forecast_method: Primary forecasting method
        - forecast_horizon: Forecast time horizon
        - industry_sector: Industry sector context
        - confidence_level: Confidence level for forecast intervals
        - accuracy_target: Target forecast accuracy (MAPE)
    """

    def _build_metadata(self) -> SolutionMetadata:
        """Build metadata for the demand forecaster solution."""
        return SolutionMetadata(
            name="demand-forecaster",
            version="1.0.0",
            industry="supplychain",
            description=(
                "AI-powered demand forecasting with trend analysis, "
                "market factor integration, multi-method forecasting, "
                "and accuracy tracking for supply chain optimization."
            ),
            author="LiuHao AI OS",
            tags=["demand-forecasting", "supply-chain", "inventory", "prediction", "analytics", "planning"],
            min_liuhao_version="1.0.0",
        )

    def _build_config_schema(self) -> None:
        """Define configuration fields for the demand forecaster solution."""
        self.config_schema.add_field(
            name="forecast_method",
            field_type="string",
            required=False,
            default="ensemble",
            description="Primary forecasting method",
            valid_values=[
                "arima", "exponential_smoothing", "linear_regression",
                "machine_learning", "prophet", "lstm", "moving_average",
                "seasonal_decomposition", "ensemble",
            ],
        )
        self.config_schema.add_field(
            name="forecast_horizon",
            field_type="string",
            required=False,
            default="medium_term",
            description="Forecast time horizon",
            valid_values=["short_term", "medium_term", "long_term"],
        )
        self.config_schema.add_field(
            name="industry_sector",
            field_type="string",
            required=False,
            default="retail",
            description="Industry sector context",
            valid_values=[
                "retail", "ecommerce", "manufacturing", "wholesale",
                "food_beverage", "pharma", "consumer_goods",
                "electronics", "fashion",
            ],
        )
        self.config_schema.add_field(
            name="confidence_level",
            field_type="float",
            required=False,
            default=0.95,
            description="Confidence level for forecast intervals (0-1)",
        )
        self.config_schema.add_field(
            name="accuracy_target_mape",
            field_type="float",
            required=False,
            default=15.0,
            description="Target MAPE (Mean Absolute Percentage Error)",
        )
        self.config_schema.add_field(
            name="seasonality_enabled",
            field_type="bool",
            required=False,
            default=True,
            description="Whether to incorporate seasonality",
        )
        self.config_schema.add_field(
            name="promotional_forecasting",
            field_type="bool",
            required=False,
            default=True,
            description="Whether to include promotional impact",
        )

    def _on_initialize(self) -> None:
        """Initialize demand forecasting solution state."""
        self._agents: Dict[str, Dict[str, Any]] = {}
        self._historical_data: Dict[str, HistoricalData] = {}
        self._trend_analyses: Dict[str, TrendAnalysis] = {}
        self._market_analyses: Dict[str, MarketFactorAnalysis] = {}
        self._forecasts: Dict[str, DemandForecast] = {}
        self._accuracy_records: Dict[str, ForecastAccuracy] = {}
        self._data_count = 0
        self._trend_count = 0
        self._market_count = 0
        self._forecast_count = 0
        self._accuracy_count = 0

        # Initialize supply chain-specific metrics
        self.supplychain_metrics = SupplyChainMetrics()

        # Seasonality patterns by industry
        self._seasonality_patterns = self._build_seasonality_patterns()

    def _build_seasonality_patterns(self) -> Dict[str, Any]:
        """Build seasonality patterns by industry sector."""
        return {
            IndustrySector.RETAIL: {
                "peak_months": ["November", "December"],
                "trough_months": ["January", "February"],
                "seasonality_strength": 0.4,
                "pattern": "Holiday-driven peak in Q4, post-holiday dip",
                "key_events": ["Black Friday", "Cyber Monday", "Holiday season", "Back to school"],
            },
            IndustrySector.ECOMMERCE: {
                "peak_months": ["November", "December"],
                "trough_months": ["January", "February"],
                "seasonality_strength": 0.35,
                "pattern": "Strong Q4 peak, growing mid-year events",
                "key_events": ["Prime Day", "Black Friday", "Cyber Monday", "Holiday season"],
            },
            IndustrySector.FOOD_BEVERAGE: {
                "peak_months": ["November", "December", "July"],
                "trough_months": ["January", "February"],
                "seasonality_strength": 0.2,
                "pattern": "Holiday peaks, summer BBQ season, steady baseline",
                "key_events": ["Holiday season", "Summer BBQ", "Back to school"],
            },
            IndustrySector.FASHION: {
                "peak_months": ["November", "December", "March", "August"],
                "trough_months": ["January", "February", "July"],
                "seasonality_strength": 0.5,
                "pattern": "Biannual peaks (holiday + spring/summer transition)",
                "key_events": ["Holiday season", "Spring collection", "Back to school", "Summer season"],
            },
            IndustrySector.PHARMA: {
                "peak_months": ["January", "February", "October"],
                "trough_months": ["July", "August"],
                "seasonality_strength": 0.15,
                "pattern": "Cold/flu season peak, relatively stable overall",
                "key_events": ["Cold and flu season", "Allergy season", "Open enrollment"],
            },
            IndustrySector.CONSUMER_GOODS: {
                "peak_months": ["November", "December"],
                "trough_months": ["January", "February"],
                "seasonality_strength": 0.3,
                "pattern": "Holiday peak, relatively stable baseline",
                "key_events": ["Holiday season", "Back to school", "Spring cleaning"],
            },
            IndustrySector.ELECTRONICS: {
                "peak_months": ["November", "December"],
                "trough_months": ["January", "February"],
                "seasonality_strength": 0.45,
                "pattern": "Very strong Q4 holiday peak, new product cycles",
                "key_events": ["Holiday season", "Back to school", "New product launches"],
            },
            IndustrySector.MANUFACTURING: {
                "peak_months": ["March", "September"],
                "trough_months": ["December", "July"],
                "seasonality_strength": 0.15,
                "pattern": "Pre-holiday build-up, summer slowdowns",
                "key_events": ["Q4 build for holiday", "Summer shutdowns", "Fiscal year end"],
            },
        }

    def _on_deploy(self) -> None:
        """Deploy demand forecasting agents and workflows."""
        agent_configs = [
            (SupplyChainRole.DEMAND_PLANNER, {
                "name": "Demand Planner",
                "description": "Leads overall demand planning and forecasting",
                "system_prompt": self._build_demand_planner_prompt(),
                "tools": ["demand_planning", "forecast_reconciliation", "sop"],
            }),
            (SupplyChainRole.DATA_ANALYST, {
                "name": "Data Analyst",
                "description": "Analyzes historical data and builds models",
                "system_prompt": self._build_data_analyst_prompt(),
                "tools": ["statistical_modeling", "forecast_generation", "accuracy_analysis"],
            }),
            (SupplyChainRole.MARKET_RESEARCHER, {
                "name": "Market Researcher",
                "description": "Analyzes market and external factors",
                "system_prompt": self._build_market_researcher_prompt(),
                "tools": ["market_analysis", "competitor_tracking", "economic_indicators"],
            }),
            (SupplyChainRole.INVENTORY_COORDINATOR, {
                "name": "Inventory Coordinator",
                "description": "Translates forecasts to inventory plans",
                "system_prompt": self._build_inventory_coordinator_prompt(),
                "tools": ["inventory_planning", "safety_stock", "service_level"],
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
        """Process a demand forecasting request through the full workflow.

        Args:
            input_data: Dictionary containing product and demand data.

        Returns:
            Dictionary with forecasting results including historical data
            analysis, trend analysis, market factors, demand forecast,
            and accuracy tracking.
        """
        product_id = input_data.get("product_id", f"prod-{len(self._historical_data)+1}")

        # Step 1: Historical data analysis
        historical = self._analyze_historical_data(product_id, input_data)

        # Step 2: Trend analysis
        trends = self._analyze_trends(historical)

        # Step 3: Market factor analysis
        market = self._analyze_market_factors(historical, trends)

        # Step 4: Forecast generation
        forecast = self._generate_forecast(historical, trends, market)

        # Step 5: Accuracy tracking setup
        accuracy = self._initialize_accuracy_tracking(historical, forecast)

        # Store and update metrics
        self._historical_data[historical.id] = historical
        self._trend_analyses[trends.id] = trends
        self._market_analyses[market.id] = market
        self._forecasts[forecast.id] = forecast
        self._accuracy_records[accuracy.id] = accuracy

        self.supplychain_metrics.forecasts_generated += 1
        self._update_supplychain_metrics()

        return {
            "historical_data": {
                "id": historical.id,
                "product_id": historical.product_id,
                "product_name": historical.product_name,
                "category": historical.category,
                "time_period": historical.time_period,
                "data_points": historical.data_points,
                "total_units": historical.total_units,
                "avg_demand": historical.avg_demand,
                "demand_std_dev": historical.demand_std_dev,
                "min_demand": historical.min_demand,
                "max_demand": historical.max_demand,
                "growth_rate": historical.growth_rate,
                "seasonality_strength": historical.seasonality_strength,
                "trend_direction": historical.trend_direction,
                "data_quality": historical.data_quality,
                "outliers": historical.outliers,
            },
            "trend_analysis": {
                "id": trends.id,
                "overall_trend": trends.overall_trend,
                "trend_strength": trends.trend_strength,
                "seasonal_pattern": trends.seasonal_pattern,
                "seasonal_peaks": trends.seasonal_peaks,
                "seasonal_troughs": trends.seasonal_troughs,
                "cyclical_patterns": trends.cyclical_patterns,
                "growth_phase": trends.growth_phase,
                "market_maturity": trends.market_maturity,
                "trend_confidence": trends.trend_confidence,
                "pattern_type": trends.pattern_type.value,
                "trend_components": trends.trend_components,
                "breakpoints": trends.breakpoints,
            },
            "market_factors": {
                "id": market.id,
                "economic_factors": market.economic_factors,
                "market_conditions": market.market_conditions,
                "competitor_activity": market.competitor_activity,
                "customer_behavior": market.customer_behavior,
                "seasonality_factors": market.seasonality_factors,
                "promotional_impact": market.promotional_impact,
                "pricing_effects": market.pricing_effects,
                "external_factors": market.external_factors,
                "supply_constraints": market.supply_constraints,
                "factor_weights": market.factor_weights,
                "lead_indicators": market.lead_indicators,
            },
            "demand_forecast": {
                "id": forecast.id,
                "forecast_method": forecast.forecast_method,
                "forecast_horizon": forecast.forecast_horizon,
                "forecast_periods": forecast.forecast_periods,
                "total_forecast_units": forecast.total_forecast_units,
                "confidence_level": forecast.confidence_level,
                "forecast_accuracy": forecast.forecast_accuracy,
                "period_forecasts_count": len(forecast.period_forecasts),
                "period_forecasts": forecast.period_forecasts,
                "risk_factors": forecast.risk_factors,
                "upside_scenarios": forecast.upside_scenarios,
                "downside_scenarios": forecast.downside_scenarios,
            },
            "accuracy_tracking": {
                "id": accuracy.id,
                "period": accuracy.period,
                "mape": accuracy.mape,
                "wmape": accuracy.wmape,
                "mae": accuracy.mae,
                "rmse": accuracy.rmse,
                "bias": accuracy.bias,
                "accuracy_rate": accuracy.accuracy_rate,
                "trend_accuracy": accuracy.trend_accuracy,
                "best_method": accuracy.best_method,
                "improvement_opportunities": accuracy.improvement_opportunities,
            },
        }

    def _on_teardown(self) -> None:
        """Clean up demand forecasting solution resources."""
        self._agents.clear()
        self._historical_data.clear()
        self._trend_analyses.clear()
        self._market_analyses.clear()
        self._forecasts.clear()
        self._accuracy_records.clear()
        self._data_count = 0
        self._trend_count = 0
        self._market_count = 0
        self._forecast_count = 0
        self._accuracy_count = 0

    # ------------------------------------------------------------------
    # Workflow steps
    # ------------------------------------------------------------------

    def _analyze_historical_data(self, product_id: str, data: Dict[str, Any]) -> HistoricalData:
        """Step 1: Analyze historical demand data."""
        self._data_count += 1
        data_id = f"hist-{self._data_count}"

        sector = IndustrySector(self.config.get("industry_sector", "retail"))
        horizon = self.config.get("forecast_horizon", "medium_term")

        # Determine data points based on horizon
        data_points_map = {
            "short_term": 24,  # 2 years monthly
            "medium_term": 36,  # 3 years monthly
            "long_term": 60,  # 5 years monthly
        }
        data_points = data_points_map.get(horizon, 36)

        avg_demand = float(data.get("avg_demand", 1000))
        std_dev = avg_demand * 0.2  # 20% coefficient of variation
        growth_rate = float(data.get("growth_rate", 5.0))  # 5% annual

        total_units = avg_demand * data_points

        min_demand = avg_demand * 0.6
        max_demand = avg_demand * 1.6

        # Seasonality strength
        seasonality_data = self._seasonality_patterns.get(sector, {})
        seasonality_strength = seasonality_data.get("seasonality_strength", 0.3)

        # Trend direction
        if growth_rate > 3:
            trend_dir = "Growing"
        elif growth_rate < -2:
            trend_dir = "Declining"
        else:
            trend_dir = "Stable"

        # Data quality
        data_quality = 8.0  # Default good quality

        # Outliers
        outliers = int(data_points * 0.02)  # ~2% outliers

        hist = HistoricalData(
            id=data_id,
            product_id=product_id,
            product_name=data.get("product_name", f"Product {self._data_count}"),
            category=data.get("category", sector.value),
            time_period=f"Last {data_points} months",
            data_points=data_points,
            total_units=round(total_units, 2),
            avg_demand=round(avg_demand, 2),
            demand_std_dev=round(std_dev, 2),
            min_demand=round(min_demand, 2),
            max_demand=round(max_demand, 2),
            growth_rate=growth_rate,
            seasonality_strength=round(seasonality_strength, 2),
            trend_direction=trend_dir,
            data_quality=data_quality,
            outliers=outliers,
            missing_values=int(data_points * 0.01),  # 1% missing
        )
        self._historical_data[data_id] = hist
        return hist

    def _analyze_trends(self, historical: HistoricalData) -> TrendAnalysis:
        """Step 2: Analyze trends and patterns."""
        self._trend_count += 1
        trend_id = f"trend-{self._trend_count}"

        sector = IndustrySector(self.config.get("industry_sector", "retail"))
        seasonality = self._seasonality_patterns.get(sector, {})

        # Overall trend
        if historical.growth_rate > 5:
            overall = f"Strong growth ({historical.growth_rate}% annual)"
            strength = 80.0
            growth_phase = "Growth"
            market_maturity = "Growing market"
            pattern = DemandPattern.GROWING
        elif historical.growth_rate > 2:
            overall = f"Moderate growth ({historical.growth_rate}% annual)"
            strength = 60.0
            growth_phase = "Mature growth"
            market_maturity = "Established market"
            pattern = DemandPattern.GROWING
        elif historical.growth_rate > -2:
            overall = f"Stable ({historical.growth_rate:+.1f}% annual)"
            strength = 30.0
            growth_phase = "Mature"
            market_maturity = "Mature market"
            pattern = DemandPattern.STABLE
        else:
            overall = f"Declining ({historical.growth_rate}% annual)"
            strength = 50.0
            growth_phase = "Decline"
            market_maturity = "Declining market"
            pattern = DemandPattern.DECLINING

        # Seasonal pattern
        seasonal_pattern = seasonality.get("pattern", "Moderate seasonality")
        peaks = seasonality.get("peak_months", [])
        troughs = seasonality.get("trough_months", [])

        # Cyclical patterns
        cyclical = []
        if sector in (IndustrySector.MANUFACTURING, IndustrySector.WHOLESALE):
            cyclical.append("Business cycle sensitivity")
        if historical.seasonality_strength > 0.3:
            cyclical.append("Strong seasonal cycle")

        # Trend confidence
        confidence = 60.0
        if historical.data_quality > 8:
            confidence += 15
        if historical.data_points >= 36:
            confidence += 10
        if historical.outliers < historical.data_points * 0.03:
            confidence += 5
        confidence = min(95.0, confidence)

        # Trend components
        components = {
            "trend": historical.growth_rate,
            "seasonality": historical.seasonality_strength * 100,
            "cyclical": 15.0,
            "random_noise": 100 - strength - (historical.seasonality_strength * 100) - 15,
        }

        # Breakpoints
        breakpoints = [
            "Month 12: First year-over-year comparison point",
            f"Seasonal peaks in {', '.join(peaks[:2])}",
        ]

        # Adjust pattern for seasonality
        if historical.seasonality_strength > 0.3 and pattern == DemandPattern.STABLE:
            pattern = DemandPattern.SEASONAL

        analysis = TrendAnalysis(
            id=trend_id,
            product_id=historical.product_id,
            overall_trend=overall,
            trend_strength=round(strength, 1),
            seasonal_pattern=seasonal_pattern,
            seasonal_peaks=peaks,
            seasonal_troughs=troughs,
            cyclical_patterns=cyclical,
            growth_phase=growth_phase,
            market_maturity=market_maturity,
            trend_confidence=round(confidence, 1),
            pattern_type=pattern,
            trend_components={k: round(v, 2) for k, v in components.items()},
            breakpoints=breakpoints,
        )
        self._trend_analyses[trend_id] = analysis
        return analysis

    def _analyze_market_factors(
        self, historical: HistoricalData, trends: TrendAnalysis
    ) -> MarketFactorAnalysis:
        """Step 3: Analyze market factors."""
        self._market_count += 1
        market_id = f"market-{self._market_count}"

        sector = IndustrySector(self.config.get("industry_sector", "retail"))
        seasonality = self._seasonality_patterns.get(sector, {})

        # Economic factors
        economic_factors = [
            "Consumer spending trends",
            "Disposable income levels",
            "Interest rates and financing availability",
            "Inflation impact on pricing",
            "Employment and wage growth",
        ]

        # Market conditions
        if trends.growth_phase == "Growth":
            conditions = "Growing market with increasing demand"
        elif trends.growth_phase == "Mature":
            conditions = "Mature market with stable demand"
        else:
            conditions = "Declining market with softening demand"

        # Competitor activity
        competitors = [
            "New product launches from key competitors",
            "Pricing changes and promotions",
            "Market share shifts",
            "New entrants and competitive intensity",
        ]

        # Customer behavior
        customer = [
            "Channel preferences (online vs offline)",
            "Purchase frequency and basket size",
            "Brand loyalty and switching behavior",
            "Sustainability and ethical preferences",
            "Price sensitivity",
        ]

        # Seasonality factors
        seasonality_factors = seasonality.get("key_events", [])

        # Promotional impact
        promo_impact = (
            "Promotions drive significant demand lift. Estimated promotional "
            "elasticity: 2-5x baseline during major promotional events. "
            "Key promotional periods align with seasonal peaks."
        )

        # Pricing effects
        pricing = (
            "Price elasticity estimated at -1.2 (moderate sensitivity). "
            "Competitive pricing pressure is significant in current market. "
            "Value-based pricing strategies show better resilience."
        )

        # External factors
        external = [
            "Macroeconomic conditions",
            "Regulatory and compliance changes",
            "Supply chain disruptions",
            "Weather and climate impacts",
            "Technology and innovation",
        ]

        # Supply constraints
        supply = [
            "Supplier lead times",
            "Raw material availability",
            "Logistics and transportation capacity",
            "Labor availability",
        ]

        # Regulatory impacts
        regulatory = [
            "Product safety regulations",
            "Labeling and packaging requirements",
            "Import/export tariffs and restrictions",
            "Environmental regulations",
        ]

        # Factor weights
        weights = {
            "historical_demand": 0.40,
            "seasonality": 0.20,
            "economic_conditions": 0.15,
            "competitive_factors": 0.10,
            "promotional_activity": 0.10,
            "other_external": 0.05,
        }

        # Leading indicators
        lead_indicators = [
            "Consumer confidence index",
            "Retail sales trends",
            "Website traffic and search volume",
            "Quote/inquiry volume",
            "Backorder levels",
            "Industry leading indicators",
        ]

        analysis = MarketFactorAnalysis(
            id=market_id,
            product_id=historical.product_id,
            economic_factors=economic_factors,
            market_conditions=conditions,
            competitor_activity=competitors,
            customer_behavior=customer,
            seasonality_factors=seasonality_factors,
            promotional_impact=promo_impact,
            pricing_effects=pricing,
            external_factors=external,
            supply_constraints=supply,
            regulatory_impacts=regulatory,
            factor_weights=weights,
            lead_indicators=lead_indicators,
        )
        self._market_analyses[market_id] = analysis
        return analysis

    def _generate_forecast(
        self,
        historical: HistoricalData,
        trends: TrendAnalysis,
        market: MarketFactorAnalysis,
    ) -> DemandForecast:
        """Step 4: Generate demand forecast."""
        self._forecast_count += 1
        forecast_id = f"forecast-{self._forecast_count}"

        method = self.config.get("forecast_method", "ensemble")
        horizon = self.config.get("forecast_horizon", "medium_term")
        confidence_level = self.config.get("confidence_level", 0.95)

        # Number of forecast periods
        periods_map = {"short_term": 3, "medium_term": 12, "long_term": 36}
        num_periods = periods_map.get(horizon, 12)

        # Base forecast calculation
        # Start with average demand, apply trend and seasonality
        base_demand = historical.avg_demand
        growth_rate_monthly = historical.growth_rate / 100 / 12
        seasonality_strength = historical.seasonality_strength

        base_forecast = []
        upper_bound = []
        lower_bound = []
        period_forecasts = []

        seasonality_data = self._seasonality_patterns.get(
            IndustrySector(self.config.get("industry_sector", "retail")), {}
        )
        peaks = seasonality_data.get("peak_months", [])
        troughs = seasonality_data.get("trough_months", [])

        total = 0.0

        for i in range(num_periods):
            # Trend component
            trend_factor = 1 + (growth_rate_monthly * (i + 1))

            # Seasonal factor (simplified sine wave pattern)
            if self.config.get("seasonality_enabled", True):
                # Simulate seasonal pattern
                month_idx = i % 12
                # Simple seasonal index approximation
                seasonal_idx = 1 + seasonality_strength * 0.3 * (
                    1 if month_idx in [10, 11] else  # Nov/Dec peak
                    -0.5 if month_idx in [0, 1] else  # Jan/Feb trough
                    0.2 * (month_idx % 6 - 3) / 3  # Gradual change
                )
            else:
                seasonal_idx = 1.0

            # Forecast value
            forecast_val = base_demand * trend_factor * seasonal_idx

            # Confidence interval (widens with horizon)
            uncertainty = 0.1 + (i * 0.01)  # Increases over time
            upper = forecast_val * (1 + uncertainty)
            lower = forecast_val * (1 - uncertainty)

            base_forecast.append(round(forecast_val, 2))
            upper_bound.append(round(upper, 2))
            lower_bound.append(round(lower, 2))
            total += forecast_val

            period_forecasts.append({
                "period": f"Month {i+1}",
                "forecast": round(forecast_val, 2),
                "upper": round(upper, 2),
                "lower": round(lower, 2),
                "trend_component": round(trend_factor, 4),
                "seasonal_component": round(seasonal_idx, 4),
            })

        # Forecast accuracy (expected)
        # MAPE decreases with horizon
        expected_mape = {
            "short_term": 10.0,
            "medium_term": 15.0,
            "long_term": 25.0,
        }
        expected_acc = 100 - expected_mape.get(horizon, 15.0)

        # Adjust for data quality
        if historical.data_quality > 8:
            expected_acc += 3
        expected_acc = min(95.0, expected_acc)

        # Seasonal and trend adjustments
        seasonal_adj = seasonality_strength * 100
        trend_adj = historical.growth_rate

        # Risk factors
        risk_factors = [
            "Forecast uncertainty increases with time horizon",
            "Seasonal estimates may not capture unusual patterns",
            "Economic conditions can significantly impact demand",
            "Competitive actions are difficult to predict",
            "Supply chain disruptions could affect availability",
            "Promotional calendar changes can shift demand",
        ]

        # Scenario analysis
        upside = {
            "Strong economic growth": total * 1.15,
            "Competitor exit/misfire": total * 1.10,
            "Viral/market trend boost": total * 1.20,
        }
        downside = {
            "Economic recession": total * 0.80,
            "New competitor entry": total * 0.90,
            "Supply chain disruption": total * 0.85,
        }

        forecast = DemandForecast(
            id=forecast_id,
            product_id=historical.product_id,
            forecast_method=method,
            forecast_horizon=horizon,
            forecast_periods=num_periods,
            base_forecast=base_forecast,
            upper_bound=upper_bound,
            lower_bound=lower_bound,
            confidence_level=confidence_level * 100,
            forecast_accuracy=round(expected_acc, 1),
            total_forecast_units=round(total, 2),
            period_forecasts=period_forecasts,
            seasonal_adjustment=round(seasonal_adj, 2),
            trend_adjustment=round(trend_adj, 2),
            risk_factors=risk_factors,
            upside_scenarios={k: round(v, 2) for k, v in upside.items()},
            downside_scenarios={k: round(v, 2) for k, v in downside.items()},
        )
        self._forecasts[forecast_id] = forecast
        return forecast

    def _initialize_accuracy_tracking(
        self, historical: HistoricalData, forecast: DemandForecast
    ) -> ForecastAccuracy:
        """Step 5: Initialize accuracy tracking."""
        self._accuracy_count += 1
        accuracy_id = f"accuracy-{self._accuracy_count}"

        # Baseline accuracy metrics (from backtest)
        target_mape = self.config.get("accuracy_target_mape", 15.0)

        # Expected MAPE by method
        method_mapes = {
            "moving_average": 20.0,
            "exponential_smoothing": 15.0,
            "arima": 12.0,
            "prophet": 11.0,
            "machine_learning": 10.0,
            "lstm": 9.0,
            "ensemble": 8.5,
            "linear_regression": 18.0,
            "seasonal_decomposition": 14.0,
        }
        method = self.config.get("forecast_method", "ensemble")
        base_mape = method_mapes.get(method, 15.0)

        # Adjust for data quality
        if historical.data_quality > 8:
            base_mape *= 0.9
        elif historical.data_quality < 5:
            base_mape *= 1.2

        mape = base_mape
        wmape = mape * 0.95  # WMAPE usually better
        mae = historical.avg_demand * (mape / 100)
        rmse = mae * 1.2
        bias = 2.0  # Slight positive bias (tendency to overforecast)

        accuracy_rate = 100 - mape

        # Trend accuracy
        trend_acc = 80.0
        if historical.growth_rate > 5 or historical.growth_rate < -5:
            trend_acc -= 10  # Harder to forecast extreme trends

        # Seasonal accuracy
        seasonal_acc = 75.0
        if historical.seasonality_strength > 0.3:
            seasonal_acc -= 10  # More seasonality = harder to forecast

        # Best method
        best = "ensemble"

        # Improvement opportunities
        improvements = [
            "Incorporate more external data sources",
            "Add promotional event calibration",
            "Implement hierarchical forecasting",
            "Add machine learning models for pattern detection",
            "Improve outlier detection and handling",
            "Add causal factors to the model",
        ]

        accuracy = ForecastAccuracy(
            id=accuracy_id,
            product_id=historical.product_id,
            period="Backtest period (baseline)",
            forecast_vs_actual={
                "period_1": historical.avg_demand * 1.05,
                "period_2": historical.avg_demand * 0.98,
                "period_3": historical.avg_demand * 1.02,
            },
            mae=round(mae, 2),
            mse=round(rmse ** 2, 2),
            rmse=round(rmse, 2),
            mape=round(mape, 2),
            wmape=round(wmape, 2),
            bias=round(bias, 2),
            accuracy_rate=round(accuracy_rate, 1),
            trend_accuracy=round(trend_acc, 1),
            seasonal_accuracy=round(seasonal_acc, 1),
            best_method=best,
            improvement_opportunities=improvements,
        )
        self._accuracy_records[accuracy_id] = accuracy
        return accuracy

    # ------------------------------------------------------------------
    # Agent prompts
    # ------------------------------------------------------------------

    def _build_demand_planner_prompt(self) -> str:
        """Build the system prompt for the demand planner role."""
        horizon = self.config.get("forecast_horizon", "medium_term").replace("_", " ")
        return (
            f"You are a demand planning expert specializing in {horizon} forecasting. "
            "Lead the demand planning process, reconcile forecasts from multiple "
            "sources, and ensure alignment with business goals. Collaborate with "
            "sales, marketing, operations, and finance. Facilitate S&OP processes. "
            "Balance statistical rigor with business judgment. Own the forecast "
            "accuracy metrics and drive continuous improvement."
        )

    def _build_data_analyst_prompt(self) -> str:
        """Build the system prompt for the data analyst role."""
        method = self.config.get("forecast_method", "ensemble").replace("_", " ")
        return (
            f"You are a forecasting data analyst specializing in {method} techniques. "
            "Build and validate forecasting models. Analyze historical data patterns, "
            "trends, and seasonality. Test multiple forecasting approaches and select "
            "the best performing models. Calculate forecast accuracy metrics. "
            "Continuously improve models and methodologies."
        )

    def _build_market_researcher_prompt(self) -> str:
        """Build the system prompt for the market researcher role."""
        sector = self.config.get("industry_sector", "retail").replace("_", " ")
        return (
            f"You are a market research analyst in the {sector} sector. "
            "Analyze market trends, competitive dynamics, customer behavior, "
            "and economic indicators that impact demand. Identify leading "
            "indicators and market signals. Research competitor activities "
            "and their potential impact. Provide market intelligence that "
            "improves forecast accuracy and informs strategic decisions."
        )

    def _build_inventory_coordinator_prompt(self) -> str:
        """Build the system prompt for the inventory coordinator role."""
        return (
            "You are an inventory planning and optimization specialist. "
            "Translate demand forecasts into optimal inventory plans. "
            "Calculate safety stock levels, reorder points, and order "
            "quantities. Balance service levels with inventory costs. "
            "Optimize inventory turnover while minimizing stockouts. "
            "Consider lead times, supplier reliability, and constraints."
        )

    # ------------------------------------------------------------------
    # Metrics
    # ------------------------------------------------------------------

    def _update_supplychain_metrics(self) -> None:
        """Update supply chain-specific metrics."""
        total = self.supplychain_metrics.forecasts_generated
        if total == 0:
            return

        # Forecast accuracy
        if self._accuracy_records:
            accs = [a.accuracy_rate for a in self._accuracy_records.values()]
            self.supplychain_metrics.forecast_accuracy = sum(accs) / len(accs)

            mapes = [a.mape for a in self._accuracy_records.values()]
            self.supplychain_metrics.avg_mape = sum(mapes) / len(mapes)

            biases = [a.bias for a in self._accuracy_records.values()]
            self.supplychain_metrics.bias = sum(biases) / len(biases)

            seasonal_accs = [a.seasonal_accuracy for a in self._accuracy_records.values()]
            self.supplychain_metrics.seasonal_forecast_accuracy = sum(seasonal_accs) / len(seasonal_accs)

        # Inventory turnover (theoretical improvement)
        self.supplychain_metrics.inventory_turnover = 6.0  # Baseline

        # Stockout rate
        self.supplychain_metrics.stockout_rate = 3.0  # Percentage

        # Service level
        self.supplychain_metrics.service_level = 95.0

        # Inventory reduction from better forecasting
        self.supplychain_metrics.inventory_reduction = 15.0  # Percentage

        # Demand pattern accuracy
        self.supplychain_metrics.demand_pattern_accuracy = 85.0

    def get_supplychain_metrics(self) -> Dict[str, Any]:
        """Get the current demand forecasting metrics.

        Returns:
            Dictionary with supply chain metrics including forecast
            accuracy, inventory turnover, stockout rate, and service level.
        """
        return self.supplychain_metrics.to_dict()
