"""
Supply Chain Optimization AI Solution Template.

A comprehensive AI-powered supply chain optimization solution for
logistics and operations teams. Includes multi-agent roles for
demand forecasting, inventory management, route optimization,
and supplier analysis.

Typical workflow:
    demand forecasting -> inventory planning -> route optimization
    -> supplier evaluation -> cost analysis
"""

from typing import Optional, Dict, Any, List
from enum import Enum
from dataclasses import dataclass, field

from solutions.templates.base_solution import (
    BaseSolution,
    SolutionMetadata,
    SolutionMetrics,
)


class ForecastAccuracy(str, Enum):
    """Forecast accuracy confidence levels."""
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    VERY_HIGH = "very_high"


class InventoryStatus(str, Enum):
    """Inventory status levels."""
    OUT_OF_STOCK = "out_of_stock"
    CRITICAL = "critical"
    LOW = "low"
    OPTIMAL = "optimal"
    OVERSTOCKED = "overstocked"
    EXCESS = "excess"


class SupplierRating(str, Enum):
    """Supplier performance ratings."""
    EXCELLENT = "excellent"
    GOOD = "good"
    AVERAGE = "average"
    BELOW_AVERAGE = "below_average"
    POOR = "poor"


class LogisticsRole(str, Enum):
    """Agent roles in the supply chain optimization solution."""
    DEMAND_FORECASTER = "demand_forecaster"
    INVENTORY_MANAGER = "inventory_manager"
    ROUTE_OPTIMIZER = "route_optimizer"
    SUPPLIER_ANALYST = "supplier_analyst"


@dataclass
class DemandForecast:
    """Represents a demand forecast.

    Attributes:
        forecast_id: Unique forecast identifier.
        product: Product name/SKU.
        period: Forecast period (e.g., "Q4 2024").
        horizon_days: Forecast horizon in days.
        forecasted_units: Forecasted demand units.
        lower_bound: Lower confidence bound.
        upper_bound: Upper confidence bound.
        confidence: Forecast confidence level.
        accuracy_percent: Historical forecast accuracy.
        method: Forecasting method used.
        seasonal_adjusted: Whether seasonally adjusted.
        trend_direction: Upward/downward/flat trend.
    """
    forecast_id: str
    product: str
    period: str = ""
    horizon_days: int = 30
    forecasted_units: float = 0.0
    lower_bound: float = 0.0
    upper_bound: float = 0.0
    confidence: ForecastAccuracy = ForecastAccuracy.MEDIUM
    accuracy_percent: float = 0.0
    method: str = ""
    seasonal_adjusted: bool = True
    trend_direction: str = "flat"


@dataclass
class InventoryItem:
    """Represents an inventory item.

    Attributes:
        sku: Stock keeping unit identifier.
        product_name: Product name.
        category: Product category.
        current_stock: Current stock units.
        reorder_point: Reorder point (units).
        safety_stock: Safety stock level (units).
        lead_time_days: Supplier lead time in days.
        unit_cost: Cost per unit.
        holding_cost: Annual holding cost per unit.
        status: Current inventory status.
        turnover_rate: Inventory turnover rate.
        last_restock_date: ISO 8601 last restock date.
    """
    sku: str
    product_name: str = ""
    category: str = ""
    current_stock: float = 0.0
    reorder_point: float = 0.0
    safety_stock: float = 0.0
    lead_time_days: int = 7
    unit_cost: float = 0.0
    holding_cost: float = 0.0
    status: InventoryStatus = InventoryStatus.OPTIMAL
    turnover_rate: float = 0.0
    last_restock_date: Optional[str] = None


@dataclass
class OptimizedRoute:
    """Represents an optimized delivery route.

    Attributes:
        route_id: Unique route identifier.
        origin: Starting location.
        destination: Final destination.
        waypoints: Intermediate stops.
        total_distance_miles: Total route distance.
        total_time_hours: Estimated total time.
        total_cost: Estimated total cost.
        vehicle_type: Type of vehicle.
        stops_count: Number of delivery stops.
        fuel_cost: Estimated fuel cost.
        emissions_kg: Estimated CO2 emissions in kg.
        optimized_by: Optimization objective.
    """
    route_id: str
    origin: str
    destination: str
    waypoints: List[str] = field(default_factory=list)
    total_distance_miles: float = 0.0
    total_time_hours: float = 0.0
    total_cost: float = 0.0
    vehicle_type: str = "truck"
    stops_count: int = 0
    fuel_cost: float = 0.0
    emissions_kg: float = 0.0
    optimized_by: str = "cost"


@dataclass
class Supplier:
    """Represents a supplier profile and performance.

    Attributes:
        supplier_id: Unique supplier identifier.
        name: Supplier name.
        category: Product category supplied.
        rating: Overall performance rating.
        on_time_delivery_rate: On-time delivery percentage.
        quality_score: Quality score (0-100).
        cost_score: Cost competitiveness score.
        lead_time_days: Average lead time.
        min_order_qty: Minimum order quantity.
        countries: Countries of operation.
        certifications: List of certifications.
        risk_level: Supplier risk level.
    """
    supplier_id: str
    name: str
    category: str = ""
    rating: SupplierRating = SupplierRating.GOOD
    on_time_delivery_rate: float = 0.9
    quality_score: float = 85.0
    cost_score: float = 75.0
    lead_time_days: int = 14
    min_order_qty: int = 0
    countries: List[str] = field(default_factory=list)
    certifications: List[str] = field(default_factory=list)
    risk_level: str = "medium"


@dataclass
class SupplyChainMetrics(SolutionMetrics):
    """Extended metrics for the supply chain optimization solution.

    Attributes:
        forecast_accuracy: Average forecast accuracy percentage.
        inventory_turnover: Average inventory turnover ratio.
        avg_delivery_time_days: Average delivery time in days.
        cost_savings_percent: Estimated cost savings percentage.
        forecasts_generated: Total forecasts generated.
        routes_optimized: Total routes optimized.
        suppliers_evaluated: Total suppliers evaluated.
        stockout_rate: Stockout rate (percentage of time out of stock).
        fill_rate: Order fill rate percentage.
        perfect_order_rate: Perfect order fulfillment rate.
    """
    forecast_accuracy: float = 0.0
    inventory_turnover: float = 0.0
    avg_delivery_time_days: float = 0.0
    cost_savings_percent: float = 0.0
    forecasts_generated: int = 0
    routes_optimized: int = 0
    suppliers_evaluated: int = 0
    stockout_rate: float = 0.0
    fill_rate: float = 0.0
    perfect_order_rate: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        """Convert supply chain metrics to a dictionary."""
        base = super().to_dict()
        base.update({
            "forecast_accuracy": self.forecast_accuracy,
            "inventory_turnover": self.inventory_turnover,
            "avg_delivery_time_days": self.avg_delivery_time_days,
            "cost_savings_percent": self.cost_savings_percent,
            "forecasts_generated": self.forecasts_generated,
            "routes_optimized": self.routes_optimized,
            "suppliers_evaluated": self.suppliers_evaluated,
            "stockout_rate": self.stockout_rate,
            "fill_rate": self.fill_rate,
            "perfect_order_rate": self.perfect_order_rate,
        })
        return base


class SupplyChainOptimizationSolution(BaseSolution):
    """Supply Chain Optimization AI solution template.

    Provides a complete, pre-configured supply chain optimization
    solution with multi-agent collaboration for demand forecasting,
    inventory management, route optimization, and supplier analysis.

    Pre-configured agent roles:
        - Demand Forecaster: Generates accurate demand predictions
        - Inventory Manager: Optimizes inventory levels and reorder points
        - Route Optimizer: Optimizes delivery routes and logistics
        - Supplier Analyst: Evaluates and ranks supplier performance

    Workflow:
        1. Demand Forecasting - Predict future demand for products
        2. Inventory Planning - Optimize inventory levels and reorder points
        3. Route Optimization - Optimize delivery routes and logistics
        4. Supplier Evaluation - Assess and rank supplier performance
        5. Cost Analysis - Analyze total costs and identify savings

    Configuration options:
        - industry: Industry for supply chain context
        - forecast_horizon: Default forecast horizon in days
        - optimization_priority: Optimization priority (cost, speed, sustainability)
        - num_suppliers: Number of suppliers to evaluate
        - warehouse_count: Number of warehouses
    """

    def _build_metadata(self) -> SolutionMetadata:
        """Build metadata for the supply chain optimization solution."""
        return SolutionMetadata(
            name="supply-chain-optimization",
            version="1.0.0",
            industry="logistics",
            description=(
                "AI-powered supply chain optimization solution with demand forecasting, "
                "inventory management, route optimization, and supplier analysis."
            ),
            author="LiuHao AI OS",
            tags=["supply-chain", "logistics", "demand-forecasting", "inventory", "optimization"],
            min_liuhao_version="1.0.0",
        )

    def _build_config_schema(self) -> None:
        """Define configuration fields for the supply chain solution."""
        self.config_schema.add_field(
            name="industry",
            field_type="string",
            required=True,
            description="Industry for supply chain context",
        )
        self.config_schema.add_field(
            name="forecast_horizon",
            field_type="int",
            required=False,
            default=30,
            description="Default forecast horizon in days",
        )
        self.config_schema.add_field(
            name="optimization_priority",
            field_type="string",
            required=False,
            default="cost",
            description="Primary optimization objective",
            valid_values=["cost", "speed", "sustainability", "reliability", "balanced"],
        )
        self.config_schema.add_field(
            name="num_suppliers",
            field_type="int",
            required=False,
            default=5,
            description="Number of suppliers to evaluate per category",
        )
        self.config_schema.add_field(
            name="warehouse_count",
            field_type="int",
            required=False,
            default=3,
            description="Number of warehouses in the network",
        )
        self.config_schema.add_field(
            name="service_level_target",
            field_type="float",
            required=False,
            default=0.95,
            description="Target service level (0-1)",
        )
        self.config_schema.add_field(
            name="forecast_method",
            field_type="string",
            required=False,
            default="hybrid",
            description="Demand forecasting method",
            valid_values=["moving_average", "exponential_smoothing", "arima", "ml_based", "hybrid"],
        )

    def _on_initialize(self) -> None:
        """Initialize supply chain optimization solution state."""
        self._agents: Dict[str, Dict[str, Any]] = {}
        self._forecasts: Dict[str, DemandForecast] = {}
        self._inventory: Dict[str, InventoryItem] = {}
        self._routes: Dict[str, OptimizedRoute] = {}
        self._suppliers: Dict[str, Supplier] = {}
        self._forecast_count = 0
        self._route_count = 0
        self._supplier_count = 0

        # Initialize supply chain-specific metrics
        self.sc_metrics = SupplyChainMetrics()

    def _on_deploy(self) -> None:
        """Deploy supply chain optimization agents and workflows."""
        agent_configs = [
            (LogisticsRole.DEMAND_FORECASTER, {
                "name": "Demand Forecaster",
                "description": "Generates accurate demand predictions",
                "system_prompt": self._build_demand_forecaster_prompt(),
                "tools": ["forecast_engine", "trend_analysis", "seasonal_adjustment"],
            }),
            (LogisticsRole.INVENTORY_MANAGER, {
                "name": "Inventory Manager",
                "description": "Optimizes inventory levels and reorder points",
                "system_prompt": self._build_inventory_manager_prompt(),
                "tools": ["inventory_optimizer", "safety_stock_calculator", "reorder_planner"],
            }),
            (LogisticsRole.ROUTE_OPTIMIZER, {
                "name": "Route Optimizer",
                "description": "Optimizes delivery routes and logistics",
                "system_prompt": self._build_route_optimizer_prompt(),
                "tools": ["route_planner", "fleet_optimizer", "load_balancer"],
            }),
            (LogisticsRole.SUPPLIER_ANALYST, {
                "name": "Supplier Analyst",
                "description": "Evaluates and ranks supplier performance",
                "system_prompt": self._build_supplier_analyst_prompt(),
                "tools": ["supplier_scoring", "risk_assessment", "cost_analysis"],
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
        """Process a supply chain optimization request through the workflow.

        Args:
            input_data: Dictionary containing supply chain data.

        Returns:
            Dictionary with optimization results including forecasts,
            inventory plan, optimized routes, and supplier analysis.
        """
        # Step 1: Demand forecasting
        forecasts = self._generate_forecasts(input_data)

        # Step 2: Inventory planning
        inventory_plan = self._optimize_inventory(forecasts)

        # Step 3: Route optimization
        routes = self._optimize_routes(input_data)

        # Step 4: Supplier evaluation
        suppliers = self._evaluate_suppliers(input_data)

        # Step 5: Cost analysis
        cost_analysis = self._analyze_costs(forecasts, inventory_plan, routes, suppliers)

        # Update metrics
        self.sc_metrics.forecasts_generated += len(forecasts)
        self.sc_metrics.routes_optimized += len(routes)
        self.sc_metrics.suppliers_evaluated += len(suppliers)
        self._update_sc_metrics()

        return {
            "demand_forecasting": {
                "forecasts_count": len(forecasts),
                "avg_accuracy": self.sc_metrics.forecast_accuracy,
                "method": self.config.get("forecast_method", "hybrid"),
                "horizon_days": self.config.get("forecast_horizon", 30),
                "forecasts": [
                    {
                        "id": f.forecast_id,
                        "product": f.product,
                        "forecasted_units": f.forecasted_units,
                        "confidence": f.confidence.value,
                        "lower_bound": f.lower_bound,
                        "upper_bound": f.upper_bound,
                        "trend": f.trend_direction,
                    }
                    for f in forecasts[:10]
                ],
            },
            "inventory_planning": {
                "items_count": len(inventory_plan),
                "items_needing_reorder": sum(
                    1 for item in inventory_plan
                    if item.status in (InventoryStatus.LOW, InventoryStatus.CRITICAL, InventoryStatus.OUT_OF_STOCK)
                ),
                "avg_turnover": self.sc_metrics.inventory_turnover,
                "service_level_target": self.config.get("service_level_target", 0.95),
            },
            "route_optimization": {
                "routes_optimized": len(routes),
                "optimization_priority": self.config.get("optimization_priority", "cost"),
                "total_savings_percent": cost_analysis.get("logistics_savings_percent", 0),
                "routes": [
                    {
                        "id": r.route_id,
                        "origin": r.origin,
                        "destination": r.destination,
                        "stops": r.stops_count,
                        "distance_miles": r.total_distance_miles,
                        "time_hours": r.total_time_hours,
                        "cost": r.total_cost,
                    }
                    for r in routes[:5]
                ],
            },
            "supplier_evaluation": {
                "suppliers_evaluated": len(suppliers),
                "top_supplier": suppliers[0].name if suppliers else "",
                "suppliers": [
                    {
                        "id": s.supplier_id,
                        "name": s.name,
                        "rating": s.rating.value,
                        "on_time_rate": s.on_time_delivery_rate,
                        "quality_score": s.quality_score,
                        "lead_time_days": s.lead_time_days,
                        "risk_level": s.risk_level,
                    }
                    for s in suppliers[:5]
                ],
            },
            "cost_analysis": cost_analysis,
        }

    def _on_teardown(self) -> None:
        """Clean up supply chain optimization solution resources."""
        self._agents.clear()
        self._forecasts.clear()
        self._inventory.clear()
        self._routes.clear()
        self._suppliers.clear()
        self._forecast_count = 0
        self._route_count = 0
        self._supplier_count = 0

    # ------------------------------------------------------------------
    # Workflow steps
    # ------------------------------------------------------------------

    def _generate_forecasts(self, data: Dict[str, Any]) -> List[DemandForecast]:
        """Step 1: Generate demand forecasts."""
        products = data.get("products", ["Product A", "Product B", "Product C", "Product D", "Product E"])
        horizon = self.config.get("forecast_horizon", 30)
        method = self.config.get("forecast_method", "hybrid")

        forecasts = []
        for i, product in enumerate(products):
            self._forecast_count += 1
            forecast_id = f"forecast-{self._forecast_count}"

            base_demand = 100 + (i * 50)
            # Add some variance
            forecasted = base_demand * horizon / 30
            lower = forecasted * 0.8
            upper = forecasted * 1.2

            # Accuracy varies by method
            accuracy_map = {
                "moving_average": 0.78,
                "exponential_smoothing": 0.82,
                "arima": 0.85,
                "ml_based": 0.88,
                "hybrid": 0.91,
            }
            accuracy = accuracy_map.get(method, 0.82)

            confidence = ForecastAccuracy.HIGH if accuracy > 0.85 else ForecastAccuracy.MEDIUM

            trends = ["growing", "stable", "declining", "seasonal"]
            trend = trends[i % len(trends)]

            forecast = DemandForecast(
                forecast_id=forecast_id,
                product=product,
                period=f"{horizon}-day forecast",
                horizon_days=horizon,
                forecasted_units=round(forecasted, 1),
                lower_bound=round(lower, 1),
                upper_bound=round(upper, 1),
                confidence=confidence,
                accuracy_percent=accuracy,
                method=method,
                trend_direction=trend,
            )
            forecasts.append(forecast)
            self._forecasts[forecast_id] = forecast

        return forecasts

    def _optimize_inventory(self, forecasts: List[DemandForecast]) -> List[InventoryItem]:
        """Step 2: Optimize inventory levels based on forecasts."""
        service_level = self.config.get("service_level_target", 0.95)
        inventory_items = []

        for forecast in forecasts:
            sku = f"SKU-{forecast.product}"
            daily_demand = forecast.forecasted_units / forecast.horizon_days
            lead_time = 7 + (hash(sku) % 14)  # 7-21 days

            # Safety stock calculation
            demand_std_dev = daily_demand * 0.2
            safety_stock = demand_std_dev * lead_time ** 0.5 * 1.64  # 95% service level

            # Reorder point
            reorder_point = (daily_demand * lead_time) + safety_stock

            # Current stock (simulated)
            current_stock = reorder_point * (0.5 + (hash(sku) % 10) / 10)

            # Determine status
            if current_stock <= 0:
                status = InventoryStatus.OUT_OF_STOCK
            elif current_stock < safety_stock:
                status = InventoryStatus.CRITICAL
            elif current_stock < reorder_point:
                status = InventoryStatus.LOW
            elif current_stock > reorder_point * 3:
                status = InventoryStatus.EXCESS
            elif current_stock > reorder_point * 2:
                status = InventoryStatus.OVERSTOCKED
            else:
                status = InventoryStatus.OPTIMAL

            # Turnover rate
            turnover = 365 / (current_stock / max(1, daily_demand)) if current_stock > 0 else 12.0

            item = InventoryItem(
                sku=sku,
                product_name=forecast.product,
                category="General",
                current_stock=round(current_stock, 1),
                reorder_point=round(reorder_point, 1),
                safety_stock=round(safety_stock, 1),
                lead_time_days=lead_time,
                unit_cost=round(10.0 + (hash(sku) % 90), 2),
                holding_cost=round(2.0 + (hash(sku) % 5), 2),
                status=status,
                turnover_rate=round(turnover, 2),
            )
            inventory_items.append(item)
            self._inventory[sku] = item

        return inventory_items

    def _optimize_routes(self, data: Dict[str, Any]) -> List[OptimizedRoute]:
        """Step 3: Optimize delivery routes."""
        priority = self.config.get("optimization_priority", "cost")
        num_routes = 5

        routes = []
        origins = ["Warehouse A", "Warehouse B", "Distribution Center 1"]
        destinations = ["City Center", "West District", "East Side", "North Point", "South Bay"]

        for i in range(num_routes):
            self._route_count += 1
            route_id = f"route-{self._route_count}"

            base_distance = 50 + (i * 30)
            base_time = 1.5 + (i * 0.5)
            base_cost = 150 + (i * 50)

            # Adjust based on priority
            if priority == "speed":
                base_time *= 0.85
                base_cost *= 1.15
            elif priority == "cost":
                base_cost *= 0.9
                base_time *= 1.1
            elif priority == "sustainability":
                base_cost *= 1.05
                emissions = base_distance * 1.5  # Lower emissions
            else:
                emissions = base_distance * 2.0

            fuel_cost = base_cost * 0.4

            route = OptimizedRoute(
                route_id=route_id,
                origin=origins[i % len(origins)],
                destination=destinations[i % len(destinations)],
                waypoints=[f"Stop {j+1}" for j in range(i)],
                total_distance_miles=round(base_distance, 1),
                total_time_hours=round(base_time, 1),
                total_cost=round(base_cost, 2),
                vehicle_type="truck",
                stops_count=i + 2,
                fuel_cost=round(fuel_cost, 2),
                emissions_kg=round(base_distance * 2.0, 1),
                optimized_by=priority,
            )
            routes.append(route)
            self._routes[route_id] = route

        return routes

    def _evaluate_suppliers(self, data: Dict[str, Any]) -> List[Supplier]:
        """Step 4: Evaluate and rank suppliers."""
        num_suppliers = self.config.get("num_suppliers", 5)

        supplier_names = [
            "Supplier Alpha", "Supplier Beta", "Supplier Gamma",
            "Supplier Delta", "Supplier Epsilon", "Supplier Zeta",
            "Supplier Eta", "Supplier Theta",
        ]

        suppliers = []
        for i in range(min(num_suppliers, len(supplier_names))):
            self._supplier_count += 1
            supplier_id = f"supplier-{self._supplier_count}"

            # Score components
            on_time = 0.80 + (hash(f"ot{i}") % 20) / 100
            quality = 70.0 + (hash(f"q{i}") % 25)
            cost_score = 60.0 + (hash(f"c{i}") % 35)

            # Overall rating
            overall = (on_time * 100 * 0.4) + (quality * 0.35) + (cost_score * 0.25)
            if overall >= 90:
                rating = SupplierRating.EXCELLENT
            elif overall >= 80:
                rating = SupplierRating.GOOD
            elif overall >= 70:
                rating = SupplierRating.AVERAGE
            elif overall >= 60:
                rating = SupplierRating.BELOW_AVERAGE
            else:
                rating = SupplierRating.POOR

            # Risk level
            lead_time = 7 + (i * 3)
            risk = "low" if overall >= 85 else "medium" if overall >= 70 else "high"

            supplier = Supplier(
                supplier_id=supplier_id,
                name=supplier_names[i],
                category="General Supplies",
                rating=rating,
                on_time_delivery_rate=round(on_time, 3),
                quality_score=round(quality, 1),
                cost_score=round(cost_score, 1),
                lead_time_days=lead_time,
                min_order_qty=100 * (i + 1),
                countries=["Country A", "Country B"] if i < 3 else ["Country C"],
                certifications=["ISO 9001"] if i < 5 else [],
                risk_level=risk,
            )
            suppliers.append(supplier)
            self._suppliers[supplier_id] = supplier

        # Sort by overall rating
        suppliers.sort(key=lambda s: (
            {SupplierRating.EXCELLENT: 5, SupplierRating.GOOD: 4,
             SupplierRating.AVERAGE: 3, SupplierRating.BELOW_AVERAGE: 2,
             SupplierRating.POOR: 1}.get(s.rating, 0)
        ), reverse=True)

        return suppliers

    def _analyze_costs(
        self,
        forecasts: List[DemandForecast],
        inventory: List[InventoryItem],
        routes: List[OptimizedRoute],
        suppliers: List[Supplier],
    ) -> Dict[str, Any]:
        """Step 5: Analyze total costs and identify savings."""
        # Inventory costs
        total_inventory_value = sum(item.current_stock * item.unit_cost for item in inventory)
        holding_cost_total = sum(item.holding_cost * item.current_stock for item in inventory)

        # Logistics costs
        total_logistics_cost = sum(r.total_cost for r in routes)

        # Procurement costs (simplified)
        total_procurement = sum(f.forecasted_units * 10 for f in forecasts)

        # Estimated savings from optimization
        inventory_savings = holding_cost_total * 0.15
        logistics_savings = total_logistics_cost * 0.12
        procurement_savings = total_procurement * 0.08

        total_savings = inventory_savings + logistics_savings + procurement_savings
        total_cost = total_inventory_value + total_logistics_cost + total_procurement
        savings_percent = (total_savings / max(1, total_cost)) * 100

        return {
            "total_cost_estimate": round(total_cost, 2),
            "inventory_holding_cost": round(holding_cost_total, 2),
            "logistics_cost": round(total_logistics_cost, 2),
            "procurement_cost": round(total_procurement, 2),
            "estimated_savings": round(total_savings, 2),
            "savings_percent": round(savings_percent, 2),
            "inventory_savings_percent": 15.0,
            "logistics_savings_percent": 12.0,
            "procurement_savings_percent": 8.0,
            "cost_breakdown": {
                "inventory": 45,
                "logistics": 25,
                "procurement": 30,
            },
            "optimization_recommendations": [
                "Consolidate orders from top-performing suppliers",
                "Implement dynamic safety stock based on demand variability",
                "Optimize delivery routes using real-time traffic data",
                "Negotiate volume discounts with key suppliers",
            ],
        }

    # ------------------------------------------------------------------
    # Agent prompts
    # ------------------------------------------------------------------

    def _build_demand_forecaster_prompt(self) -> str:
        """Build the system prompt for the demand forecaster role."""
        method = self.config.get("forecast_method", "hybrid")
        return (
            f"You are a demand forecasting specialist using {method} methodology. "
            f"Generate accurate demand forecasts by analyzing historical data, "
            f"seasonal patterns, market trends, and external factors. "
            f"Provide confidence intervals and clearly communicate forecast "
            f"uncertainty. Continuously validate forecast accuracy and "
            f"refine models based on actual results."
        )

    def _build_inventory_manager_prompt(self) -> str:
        """Build the system prompt for the inventory manager role."""
        service_level = self.config.get("service_level_target", 0.95)
        return (
            f"You are an inventory optimization specialist targeting "
            f"{service_level*100:.0f}% service level. Balance stock availability "
            f"with holding costs. Calculate optimal reorder points, safety stock "
            f"levels, and order quantities using EOQ and other inventory models. "
            f"Identify slow-moving and obsolete inventory. Minimize stockouts "
            f"while avoiding excess inventory."
        )

    def _build_route_optimizer_prompt(self) -> str:
        """Build the system prompt for the route optimizer role."""
        priority = self.config.get("optimization_priority", "cost")
        return (
            f"You are a logistics and route optimization specialist. "
            f"Optimize delivery routes prioritizing {priority}. "
            f"Consider vehicle capacity, driver hours, traffic patterns, "
            f"and delivery windows. Use advanced algorithms for vehicle "
            f"routing and scheduling. Minimize cost, time, or emissions "
            f"while meeting service level requirements."
        )

    def _build_supplier_analyst_prompt(self) -> str:
        """Build the system prompt for the supplier analyst role."""
        return (
            "You are a supplier performance analyst. Evaluate suppliers "
            "across multiple dimensions: quality, delivery, cost, "
            "responsiveness, and financial stability. Use scorecards and "
            "risk assessments. Identify top performers and underperformers. "
            "Recommend supplier development actions and diversification strategies."
        )

    # ------------------------------------------------------------------
    # Metrics
    # ------------------------------------------------------------------

    def _update_sc_metrics(self) -> None:
        """Update supply chain-specific metrics."""
        if self._forecasts:
            accuracies = [f.accuracy_percent for f in self._forecasts.values()]
            self.sc_metrics.forecast_accuracy = sum(accuracies) / len(accuracies)

        if self._inventory:
            turnovers = [item.turnover_rate for item in self._inventory.values()]
            self.sc_metrics.inventory_turnover = sum(turnovers) / len(turnovers)

        self.sc_metrics.avg_delivery_time_days = 3.5
        self.sc_metrics.cost_savings_percent = 10.0
        self.sc_metrics.stockout_rate = 0.03
        self.sc_metrics.fill_rate = 0.97
        self.sc_metrics.perfect_order_rate = 0.92

    def get_supply_chain_metrics(self) -> Dict[str, Any]:
        """Get the current supply chain-specific metrics.

        Returns:
            Dictionary with supply chain metrics including forecast accuracy,
            inventory turnover, delivery time, and cost savings.
        """
        return self.sc_metrics.to_dict()
