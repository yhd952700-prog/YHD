"""
Retail Customer Experience AI Solution Template.

A comprehensive AI-powered retail customer experience solution
for e-commerce and brick-and-mortar retailers. Includes multi-agent
roles for customer service, product expertise, personalization,
and feedback analysis.

Typical workflow:
    customer inquiry -> product recommendations -> personalized service
    -> feedback collection -> analysis
"""

from typing import Optional, Dict, Any, List
from enum import Enum
from dataclasses import dataclass, field

from solutions.templates.base_solution import (
    BaseSolution,
    SolutionMetadata,
    SolutionMetrics,
)


class CustomerTier(str, Enum):
    """Customer loyalty tier levels."""
    BRONZE = "bronze"
    SILVER = "silver"
    GOLD = "gold"
    PLATINUM = "platinum"
    VIP = "vip"


class ChannelType(str, Enum):
    """Customer interaction channels."""
    WEB = "web"
    MOBILE = "mobile"
    EMAIL = "email"
    CHAT = "chat"
    PHONE = "phone"
    IN_STORE = "in_store"
    SOCIAL = "social_media"


class InquiryCategory(str, Enum):
    """Categories of customer inquiries."""
    PRODUCT_INFO = "product_info"
    ORDER_STATUS = "order_status"
    RETURN_REFUND = "return_refund"
    SHIPPING = "shipping"
    PAYMENT = "payment"
    ACCOUNT = "account"
    TECH_SUPPORT = "tech_support"
    FEEDBACK = "feedback"
    COMPLAINT = "complaint"
    GENERAL = "general"


class RetailRole(str, Enum):
    """Agent roles in the retail customer experience solution."""
    CUSTOMER_SERVICE_AGENT = "customer_service_agent"
    PRODUCT_EXPERT = "product_expert"
    PERSONALIZATION_ENGINE = "personalization_engine"
    FEEDBACK_ANALYST = "feedback_analyst"


@dataclass
class CustomerProfile:
    """Represents a retail customer profile.

    Attributes:
        customer_id: Unique customer identifier.
        name: Customer name.
        email: Customer email.
        tier: Loyalty tier level.
        lifetime_value: Total lifetime value.
        orders_count: Total number of orders.
        last_order_date: ISO 8601 date of last order.
        preferences: Customer preferences.
        purchase_history: Recent purchase history.
        browsing_history: Recent browsing history.
        satisfaction_score: CSAT score (0-5).
        location: Customer location.
    """
    customer_id: str
    name: str = ""
    email: str = ""
    tier: CustomerTier = CustomerTier.BRONZE
    lifetime_value: float = 0.0
    orders_count: int = 0
    last_order_date: Optional[str] = None
    preferences: Dict[str, Any] = field(default_factory=dict)
    purchase_history: List[Dict[str, Any]] = field(default_factory=list)
    browsing_history: List[str] = field(default_factory=list)
    satisfaction_score: float = 4.0
    location: str = ""


@dataclass
class Product:
    """Represents a retail product.

    Attributes:
        product_id: Unique product identifier.
        name: Product name.
        category: Product category.
        price: Product price.
        rating: Average customer rating (0-5).
        review_count: Number of reviews.
        inventory: Current inventory level.
        description: Product description.
        features: Key product features.
        tags: Product tags for categorization.
    """
    product_id: str
    name: str
    category: str = ""
    price: float = 0.0
    rating: float = 4.0
    review_count: int = 0
    inventory: int = 0
    description: str = ""
    features: List[str] = field(default_factory=list)
    tags: List[str] = field(default_factory=list)


@dataclass
class Recommendation:
    """Represents a product recommendation.

    Attributes:
        product: The recommended product.
        reason: Why this product was recommended.
        relevance_score: Relevance score (0-1).
        personalization_type: Type of personalization used.
        confidence: Recommendation confidence (0-1).
        expected_conversion: Expected conversion probability.
    """
    product: Product
    reason: str = ""
    relevance_score: float = 0.0
    personalization_type: str = "general"
    confidence: float = 0.0
    expected_conversion: float = 0.0


@dataclass
class CustomerInteraction:
    """Represents a customer service interaction.

    Attributes:
        interaction_id: Unique interaction ID.
        customer_id: Customer identifier.
        channel: Interaction channel.
        category: Inquiry category.
        query: Customer query/message.
        response: AI response.
        resolution_status: Resolution status.
        csat_score: Customer satisfaction score (if provided).
        duration_seconds: Interaction duration.
        products_recommended: Number of products recommended.
        created_at: ISO 8601 timestamp.
    """
    interaction_id: str
    customer_id: str
    channel: ChannelType = ChannelType.CHAT
    category: InquiryCategory = InquiryCategory.GENERAL
    query: str = ""
    response: str = ""
    resolution_status: str = "pending"
    csat_score: Optional[float] = None
    duration_seconds: float = 0.0
    products_recommended: int = 0
    created_at: Optional[str] = None


@dataclass
class RetailMetrics(SolutionMetrics):
    """Extended metrics for the retail customer experience solution.

    Attributes:
        csat_score: Average customer satisfaction score (0-5).
        conversion_rate: Average conversion rate.
        avg_order_value: Average order value.
        interactions_handled: Total interactions handled.
        products_recommended: Total products recommended.
        first_contact_resolution: First-contact resolution rate.
        avg_response_time_sec: Average response time in seconds.
        return_rate: Product return rate.
    """
    csat_score: float = 0.0
    conversion_rate: float = 0.0
    avg_order_value: float = 0.0
    interactions_handled: int = 0
    products_recommended: int = 0
    first_contact_resolution: float = 0.0
    avg_response_time_sec: float = 0.0
    return_rate: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        """Convert retail metrics to a dictionary."""
        base = super().to_dict()
        base.update({
            "csat_score": self.csat_score,
            "conversion_rate": self.conversion_rate,
            "avg_order_value": self.avg_order_value,
            "interactions_handled": self.interactions_handled,
            "products_recommended": self.products_recommended,
            "first_contact_resolution": self.first_contact_resolution,
            "avg_response_time_sec": self.avg_response_time_sec,
            "return_rate": self.return_rate,
        })
        return base


class CustomerExperienceSolution(BaseSolution):
    """Retail Customer Experience AI solution template.

    Provides a complete, pre-configured retail customer experience
    solution with multi-agent collaboration for customer service,
    product expertise, personalization, and feedback analysis.

    Pre-configured agent roles:
        - Customer Service Agent: Handles customer inquiries and issues
        - Product Expert: Provides detailed product information and advice
        - Personalization Engine: Delivers personalized recommendations
        - Feedback Analyst: Analyzes customer feedback and sentiment

    Workflow:
        1. Customer Inquiry - Receive and categorize customer message
        2. Product Recommendations - Provide personalized product suggestions
        3. Personalized Service - Tailor response to customer profile
        4. Feedback Collection - Gather customer feedback
        5. Analysis - Analyze feedback and sentiment

    Configuration options:
        - retail_segment: Retail segment (fashion, electronics, etc.)
        - personalization_level: Level of personalization
        - language: Primary customer service language
        - enable_visual_search: Whether to enable visual product search
        - recommendation_strategy: Product recommendation strategy
    """

    def _build_metadata(self) -> SolutionMetadata:
        """Build metadata for the retail customer experience solution."""
        return SolutionMetadata(
            name="customer-experience",
            version="1.0.0",
            industry="retail",
            description=(
                "AI-powered retail customer experience solution with "
                "personalized recommendations, customer service, "
                "and feedback analysis."
            ),
            author="LiuHao AI OS",
            tags=["retail", "customer-experience", "personalization", "ecommerce", "csat"],
            min_liuhao_version="1.0.0",
        )

    def _build_config_schema(self) -> None:
        """Define configuration fields for the retail solution."""
        self.config_schema.add_field(
            name="retail_segment",
            field_type="string",
            required=True,
            description="Retail product segment",
            valid_values=[
                "fashion", "electronics", "home_goods", "beauty",
                "grocery", "sports", "books", "toys", "automotive",
                "general_merchandise",
            ],
        )
        self.config_schema.add_field(
            name="personalization_level",
            field_type="string",
            required=False,
            default="standard",
            description="Level of personalization",
            valid_values=["basic", "standard", "advanced", "hyper"],
        )
        self.config_schema.add_field(
            name="language",
            field_type="string",
            required=False,
            default="en",
            description="Primary customer service language",
        )
        self.config_schema.add_field(
            name="enable_visual_search",
            field_type="bool",
            required=False,
            default=False,
            description="Whether to enable visual product search",
        )
        self.config_schema.add_field(
            name="recommendation_strategy",
            field_type="string",
            required=False,
            default="hybrid",
            description="Product recommendation strategy",
            valid_values=["collaborative", "content_based", "popular", "hybrid", "ai_driven"],
        )
        self.config_schema.add_field(
            name="num_recommendations",
            field_type="int",
            required=False,
            default=5,
            description="Number of product recommendations to show",
        )
        self.config_schema.add_field(
            name="tone",
            field_type="string",
            required=False,
            default="friendly",
            description="Customer service tone",
            valid_values=["friendly", "professional", "casual", "luxury"],
        )

    def _on_initialize(self) -> None:
        """Initialize retail customer experience solution state."""
        self._agents: Dict[str, Dict[str, Any]] = {}
        self._customers: Dict[str, CustomerProfile] = {}
        self._products: Dict[str, Product] = {}
        self._interactions: Dict[str, CustomerInteraction] = {}
        self._interaction_count = 0
        self._product_count = 0

        # Initialize retail-specific metrics
        self.retail_metrics = RetailMetrics()

    def _on_deploy(self) -> None:
        """Deploy retail customer experience agents and workflows."""
        agent_configs = [
            (RetailRole.CUSTOMER_SERVICE_AGENT, {
                "name": "Customer Service Agent",
                "description": "Handles customer inquiries and issues",
                "system_prompt": self._build_customer_service_prompt(),
                "tools": ["order_lookup", "return_processor", "account_manager"],
            }),
            (RetailRole.PRODUCT_EXPERT, {
                "name": "Product Expert",
                "description": "Provides detailed product information and advice",
                "system_prompt": self._build_product_expert_prompt(),
                "tools": ["product_catalog", "specification_lookup", "comparison_tool"],
            }),
            (RetailRole.PERSONALIZATION_ENGINE, {
                "name": "Personalization Engine",
                "description": "Delivers personalized recommendations",
                "system_prompt": self._build_personalization_prompt(),
                "tools": ["recommendation_engine", "customer_segments", "trending_products"],
            }),
            (RetailRole.FEEDBACK_ANALYST, {
                "name": "Feedback Analyst",
                "description": "Analyzes customer feedback and sentiment",
                "system_prompt": self._build_feedback_analyst_prompt(),
                "tools": ["sentiment_analysis", "topic_extraction", "trend_analysis"],
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
        """Process a customer interaction through the full workflow.

        Args:
            input_data: Dictionary containing customer inquiry data
                with at least ``customer_id`` and ``query``.

        Returns:
            Dictionary with customer service results including
            response, recommendations, and feedback analysis.
        """
        # Step 1: Receive and categorize inquiry
        customer = self._get_or_create_customer(input_data)
        category = self._categorize_inquiry(input_data.get("query", ""))

        # Step 2: Generate product recommendations
        recommendations = self._generate_recommendations(
            customer, input_data.get("query", "")
        )

        # Step 3: Generate personalized response
        response = self._generate_personalized_response(
            customer, category, input_data.get("query", ""), recommendations
        )

        # Step 4: Collect and analyze feedback
        feedback_analysis = self._analyze_feedback(
            input_data.get("query", ""), customer
        )

        # Record interaction
        interaction = self._record_interaction(
            customer, category, input_data, response, recommendations
        )

        # Update metrics
        self.retail_metrics.interactions_handled += 1
        self.retail_metrics.products_recommended += len(recommendations)
        self._update_retail_metrics(customer)

        return {
            "interaction_id": interaction.interaction_id,
            "customer": {
                "id": customer.customer_id,
                "tier": customer.tier.value,
                "lifetime_value": customer.lifetime_value,
                "orders_count": customer.orders_count,
            },
            "inquiry": {
                "category": category.value,
                "query": input_data.get("query", ""),
                "channel": input_data.get("channel", ChannelType.CHAT.value),
            },
            "response": {
                "message": response,
                "personalization_level": self.config.get("personalization_level", "standard"),
            },
            "recommendations": [
                {
                    "product_id": r.product.product_id,
                    "name": r.product.name,
                    "price": r.product.price,
                    "rating": r.product.rating,
                    "reason": r.reason,
                    "relevance_score": r.relevance_score,
                }
                for r in recommendations
            ],
            "feedback_analysis": feedback_analysis,
            "resolution": {
                "status": interaction.resolution_status,
                "first_contact_resolution": interaction.resolution_status == "resolved",
            },
        }

    def _on_teardown(self) -> None:
        """Clean up retail customer experience solution resources."""
        self._agents.clear()
        self._customers.clear()
        self._products.clear()
        self._interactions.clear()
        self._interaction_count = 0
        self._product_count = 0

    # ------------------------------------------------------------------
    # Workflow steps
    # ------------------------------------------------------------------

    def _get_or_create_customer(self, data: Dict[str, Any]) -> CustomerProfile:
        """Get existing customer or create new profile."""
        customer_id = data.get("customer_id", "guest")
        if customer_id in self._customers:
            return self._customers[customer_id]

        # Determine tier based on LTV
        ltv = data.get("lifetime_value", 0.0)
        if ltv > 5000:
            tier = CustomerTier.VIP
        elif ltv > 2000:
            tier = CustomerTier.PLATINUM
        elif ltv > 1000:
            tier = CustomerTier.GOLD
        elif ltv > 300:
            tier = CustomerTier.SILVER
        else:
            tier = CustomerTier.BRONZE

        customer = CustomerProfile(
            customer_id=customer_id,
            name=data.get("name", ""),
            email=data.get("email", ""),
            tier=tier,
            lifetime_value=ltv,
            orders_count=data.get("orders_count", 0),
            preferences=data.get("preferences", {}),
            purchase_history=data.get("purchase_history", []),
            browsing_history=data.get("browsing_history", []),
            satisfaction_score=data.get("satisfaction_score", 4.0),
            location=data.get("location", ""),
        )
        self._customers[customer_id] = customer
        return customer

    def _categorize_inquiry(self, query: str) -> InquiryCategory:
        """Step 1: Categorize customer inquiry type."""
        query_lower = query.lower()

        category_keywords = {
            InquiryCategory.PRODUCT_INFO: ["product", "recommend", "suggest", "looking for", "what about", "size", "color"],
            InquiryCategory.ORDER_STATUS: ["order", "tracking", "where is", "delivery", "shipped", "status"],
            InquiryCategory.RETURN_REFUND: ["return", "refund", "exchange", "send back", "money back"],
            InquiryCategory.SHIPPING: ["shipping", "delivery", "arrive", "ship", "carrier"],
            InquiryCategory.PAYMENT: ["payment", "charge", "billing", "credit card", "paypal", "invoice"],
            InquiryCategory.ACCOUNT: ["account", "login", "password", "profile", "settings"],
            InquiryCategory.TECH_SUPPORT: ["help", "not working", "error", "issue", "broken", "trouble"],
            InquiryCategory.FEEDBACK: ["feedback", "review", "suggestion", "opinion"],
            InquiryCategory.COMPLAINT: ["complaint", "bad", "terrible", "worst", "unhappy", "disappointed"],
        }

        best_category = InquiryCategory.GENERAL
        max_matches = 0

        for category, keywords in category_keywords.items():
            matches = sum(1 for kw in keywords if kw in query_lower)
            if matches > max_matches:
                max_matches = matches
                best_category = category

        return best_category

    def _generate_recommendations(
        self, customer: CustomerProfile, query: str
    ) -> List[Recommendation]:
        """Step 2: Generate personalized product recommendations."""
        num_recs = self.config.get("num_recommendations", 5)
        strategy = self.config.get("recommendation_strategy", "hybrid")
        segment = self.config.get("retail_segment", "general_merchandise")

        # Sample product catalog
        products = self._get_sample_products(segment, num_recs * 2)

        # Score products based on strategy
        scored = []
        for product in products:
            base_score = product.rating / 5.0

            # Personalization boosts
            personalization_type = "general"
            reason = f"Popular in {segment}"

            if strategy in ("hybrid", "collaborative", "ai_driven"):
                # Collaborative/personalization boost
                if customer.orders_count > 0:
                    base_score += 0.1
                    personalization_type = "collaborative"
                    reason = "Customers like you also bought"

            if strategy in ("hybrid", "content_based", "ai_driven"):
                # Content-based boost from query
                query_terms = set(query.lower().split())
                product_terms = set(product.tags + [product.name.lower()])
                overlap = len(query_terms & product_terms)
                if overlap > 0:
                    base_score += min(0.2, overlap * 0.05)
                    personalization_type = "content_based"
                    reason = f"Matches your interest in {product.category}"

            # Tier-based personalization boost
            tier_boost = {
                CustomerTier.VIP: 0.15,
                CustomerTier.PLATINUM: 0.10,
                CustomerTier.GOLD: 0.05,
            }.get(customer.tier, 0.0)
            base_score += tier_boost

            # Inventory check
            if product.inventory == 0:
                base_score -= 0.3

            score = min(1.0, base_score)
            scored.append((product, score, reason, personalization_type))

        # Sort and take top N
        scored.sort(key=lambda x: x[1], reverse=True)
        top = scored[:num_recs]

        recommendations = []
        for product, score, reason, ptype in top:
            rec = Recommendation(
                product=product,
                reason=reason,
                relevance_score=round(score, 3),
                personalization_type=ptype,
                confidence=round(0.5 + score * 0.4, 2),
                expected_conversion=round(0.02 + score * 0.08, 4),
            )
            recommendations.append(rec)

        return recommendations

    def _get_sample_products(self, segment: str, count: int) -> List[Product]:
        """Get sample products for the retail segment."""
        products = []
        for i in range(count):
            self._product_count += 1
            pid = f"product-{self._product_count}"
            product = Product(
                product_id=pid,
                name=f"{segment.title()} Product {i+1}",
                category=f"{segment}/category-{i%3+1}",
                price=round(29.99 + (i * 15), 2),
                rating=round(3.5 + (hash(pid) % 15) / 10, 1),
                review_count=10 + (i * 7),
                inventory=max(0, 50 - i * 3),
                features=[f"Feature {j+1}" for j in range(3)],
                tags=[segment, f"tag-{i%5}", f"style-{i%3}"],
            )
            products.append(product)
            self._products[pid] = product
        return products

    def _generate_personalized_response(
        self,
        customer: CustomerProfile,
        category: InquiryCategory,
        query: str,
        recommendations: List[Recommendation],
    ) -> str:
        """Step 3: Generate personalized customer response."""
        tone = self.config.get("tone", "friendly")
        segment = self.config.get("retail_segment", "retail")

        # Tier-specific personalization
        tier_greeting = {
            CustomerTier.VIP: f"Dear valued {customer.name},",
            CustomerTier.PLATINUM: f"Hello {customer.name},",
            CustomerTier.GOLD: f"Hi {customer.name},",
            CustomerTier.SILVER: f"Hi {customer.name},",
            CustomerTier.BRONZE: f"Hi there,",
        }.get(customer.tier, "Hi there,")

        # Category-specific response
        responses = {
            InquiryCategory.PRODUCT_INFO: (
                f"I'd be happy to help you find the perfect {segment} products! "
                f"Based on your interests, I've selected some items I think you'll love. "
                f"Each recommendation is tailored to your preferences and our best sellers."
            ),
            InquiryCategory.ORDER_STATUS: (
                f"Thank you for your order inquiry. I can help you check the status "
                f"of your order. Please have your order number ready, and I'll look "
                f"up the latest tracking information for you right away."
            ),
            InquiryCategory.RETURN_REFUND: (
                f"I understand you'd like to return or exchange an item. "
                f"Our return policy allows for easy returns within 30 days. "
                f"I can start the return process for you - it only takes a moment."
            ),
            InquiryCategory.SHIPPING: (
                f"Let me help you with your shipping question. "
                f"We offer multiple shipping options including standard, "
                f"express, and next-day delivery. Free shipping on orders over $50."
            ),
            InquiryCategory.PAYMENT: (
                f"I can help with your payment question. We accept all major "
                f"credit cards, PayPal, and other payment methods. Your security "
                f"and privacy are our top priorities."
            ),
            InquiryCategory.COMPLAINT: (
                f"I'm truly sorry to hear about your experience. Your satisfaction "
                f"is very important to us. Let me make this right immediately. "
                f"What can I do to help resolve this issue for you?"
            ),
        }

        base_response = responses.get(
            category,
            f"Thank you for reaching out! I'm here to help with your {segment} shopping needs."
        )

        # Add recommendations mention
        if recommendations and category in (InquiryCategory.PRODUCT_INFO, InquiryCategory.GENERAL):
            base_response += (
                f" I've also selected {len(recommendations)} products that might interest you. "
                f"Would you like me to tell you more about any of them?"
            )

        # Personalized closing
        closing = {
            CustomerTier.VIP: "Your dedicated support team is always here for you.",
            CustomerTier.PLATINUM: "As a valued Platinum member, your satisfaction is our priority.",
            CustomerTier.GOLD: "Thank you for being a Gold member!",
            CustomerTier.SILVER: "We appreciate your business!",
            CustomerTier.BRONZE: "Happy shopping!",
        }.get(customer.tier, "Happy shopping!")

        return f"{tier_greeting}\n\n{base_response}\n\n{closing}"

    def _analyze_feedback(self, query: str, customer: CustomerProfile) -> Dict[str, Any]:
        """Step 4: Analyze customer feedback and sentiment."""
        query_lower = query.lower()

        # Simple sentiment analysis
        positive_words = ["great", "good", "love", "excellent", "amazing", "perfect", "happy", "thank"]
        negative_words = ["bad", "terrible", "awful", "worst", "hate", "disappointed", "problem", "issue"]

        pos_count = sum(1 for w in positive_words if w in query_lower)
        neg_count = sum(1 for w in negative_words if w in query_lower)

        if pos_count > neg_count:
            sentiment = "positive"
            score = 0.7 + (pos_count * 0.05)
        elif neg_count > pos_count:
            sentiment = "negative"
            score = 0.3 - (neg_count * 0.05)
        else:
            sentiment = "neutral"
            score = 0.5

        # Extract topics
        topics = []
        topic_keywords = {
            "product_quality": ["quality", "broken", "defect", "material"],
            "shipping": ["shipping", "delivery", "late", "arrived"],
            "customer_service": ["service", "support", "help", "staff"],
            "price_value": ["price", "expensive", "value", "worth", "cost"],
            "website_app": ["website", "app", "checkout", "easy", "difficult"],
        }
        for topic, keywords in topic_keywords.items():
            if any(kw in query_lower for kw in keywords):
                topics.append(topic)

        return {
            "sentiment": sentiment,
            "sentiment_score": round(max(0.0, min(1.0, score)), 2),
            "topics": topics,
            "actionable_insights": [
                f"Customer sentiment is {sentiment}",
                f"Key topics: {', '.join(topics) if topics else 'general inquiry'}",
            ] if topics else [f"Customer sentiment is {sentiment}"],
        }

    def _record_interaction(
        self,
        customer: CustomerProfile,
        category: InquiryCategory,
        input_data: Dict[str, Any],
        response: str,
        recommendations: List[Recommendation],
    ) -> CustomerInteraction:
        """Record the customer interaction."""
        self._interaction_count += 1
        interaction_id = f"interaction-{self._interaction_count}"

        interaction = CustomerInteraction(
            interaction_id=interaction_id,
            customer_id=customer.customer_id,
            channel=ChannelType(input_data.get("channel", ChannelType.CHAT.value)),
            category=category,
            query=input_data.get("query", ""),
            response=response,
            resolution_status="resolved" if category != InquiryCategory.COMPLAINT else "pending",
            products_recommended=len(recommendations),
        )
        self._interactions[interaction_id] = interaction
        return interaction

    # ------------------------------------------------------------------
    # Agent prompts
    # ------------------------------------------------------------------

    def _build_customer_service_prompt(self) -> str:
        """Build the system prompt for the customer service agent role."""
        tone = self.config.get("tone", "friendly")
        segment = self.config.get("retail_segment", "retail")
        return (
            f"You are a {tone} and helpful customer service representative "
            f"for a {segment} retailer. Always be empathetic, patient, and "
            f"solution-focused. Listen carefully to customer concerns and "
            f"provide clear, accurate information. Go the extra mile to "
            f"ensure customer satisfaction. Handle complaints gracefully "
            f"and focus on finding solutions."
        )

    def _build_product_expert_prompt(self) -> str:
        """Build the system prompt for the product expert role."""
        segment = self.config.get("retail_segment", "retail")
        return (
            f"You are a product expert with deep knowledge of {segment} products. "
            f"Provide accurate, detailed product information including features, "
            f"specifications, sizing, and compatibility. Help customers find the "
            f"right products for their needs by asking clarifying questions and "
            f"making informed recommendations. Be honest about product limitations."
        )

    def _build_personalization_prompt(self) -> str:
        """Build the system prompt for the personalization engine role."""
        level = self.config.get("personalization_level", "standard")
        return (
            f"You are a personalization specialist for retail. "
            f"Deliver {level} personalized product recommendations based on "
            f"customer behavior, preferences, purchase history, and browsing patterns. "
            f"Use collaborative filtering, content-based recommendations, and "
            f"trend analysis. Always explain why products are recommended to "
            f"build trust and transparency."
        )

    def _build_feedback_analyst_prompt(self) -> str:
        """Build the system prompt for the feedback analyst role."""
        return (
            "You are a customer feedback analyst. Analyze customer feedback "
            "to extract sentiment, identify key topics, and uncover actionable "
            "insights. Track trends over time and highlight areas for improvement. "
            "Provide both quantitative metrics and qualitative understanding of "
            "customer needs and pain points."
        )

    # ------------------------------------------------------------------
    # Metrics
    # ------------------------------------------------------------------

    def _update_retail_metrics(self, customer: CustomerProfile) -> None:
        """Update retail-specific metrics."""
        if self._customers:
            sat_scores = [c.satisfaction_score for c in self._customers.values()]
            self.retail_metrics.csat_score = sum(sat_scores) / len(sat_scores)

        self.retail_metrics.conversion_rate = 0.035
        self.retail_metrics.avg_order_value = 85.0
        self.retail_metrics.first_contact_resolution = 0.78
        self.retail_metrics.avg_response_time_sec = 15.0
        self.retail_metrics.return_rate = 0.08

    def get_retail_metrics(self) -> Dict[str, Any]:
        """Get the current retail-specific metrics.

        Returns:
            Dictionary with retail metrics including CSAT score,
            conversion rate, average order value, and resolution rate.
        """
        return self.retail_metrics.to_dict()
