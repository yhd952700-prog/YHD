"""
Marketing AI Assistant Solution Template.

A comprehensive AI-powered marketing assistant solution for
marketing teams, agencies, and growth departments. Includes
multi-agent roles for market research, copywriting, campaign
design, and data analysis with A/B testing capabilities.

Typical workflow:
    market research -> campaign design -> content creation
    -> A/B testing -> performance analysis
"""

from typing import Optional, Dict, Any, List
from enum import Enum
from dataclasses import dataclass, field

from solutions.templates.base_solution import (
    BaseSolution,
    SolutionMetadata,
    SolutionMetrics,
)


class CampaignType(str, Enum):
    """Types of marketing campaigns."""
    BRAND_AWARENESS = "brand_awareness"
    LEAD_GENERATION = "lead_generation"
    CONVERSION = "conversion"
    RETENTION = "retention"
    ENGAGEMENT = "engagement"
    LAUNCH = "product_launch"


class Channel(str, Enum):
    """Marketing channels."""
    EMAIL = "email"
    SOCIAL_MEDIA = "social_media"
    SEARCH = "search"
    DISPLAY = "display"
    CONTENT = "content"
    VIDEO = "video"
    PAID_SOCIAL = "paid_social"
    SMS = "sms"
    PUSH = "push"
    IN_APP = "in_app"


class ContentFormat(str, Enum):
    """Content format types."""
    BLOG_POST = "blog_post"
    EMAIL = "email"
    SOCIAL_POST = "social_post"
    AD_COPY = "ad_copy"
    VIDEO_SCRIPT = "video_script"
    LANDING_PAGE = "landing_page"
    NEWSLETTER = "newsletter"
    WHITEPAPER = "whitepaper"
    CASE_STUDY = "case_study"
    INFOGRAPHIC = "infographic"


class MarketingRole(str, Enum):
    """Agent roles in the marketing assistant solution."""
    MARKET_RESEARCHER = "market_researcher"
    COPYWRITER = "copywriter"
    CAMPAIGN_DESIGNER = "campaign_designer"
    DATA_ANALYST = "data_analyst"


@dataclass
class TargetAudience:
    """Represents a target audience segment.

    Attributes:
        segment_name: Name of the audience segment.
        demographics: Demographic characteristics.
        psychographics: Psychological and behavioral traits.
        pain_points: Key pain points of the audience.
        motivations: Key motivations and desires.
        channels: Preferred communication channels.
        size_estimate: Estimated audience size.
        priority: Priority level of this segment.
    """
    segment_name: str
    demographics: Dict[str, Any] = field(default_factory=dict)
    psychographics: List[str] = field(default_factory=list)
    pain_points: List[str] = field(default_factory=list)
    motivations: List[str] = field(default_factory=list)
    channels: List[Channel] = field(default_factory=list)
    size_estimate: str = ""
    priority: int = 1


@dataclass
class MarketingContent:
    """Represents a piece of marketing content.

    Attributes:
        id: Unique content identifier.
        title: Content title/headline.
        format: Content format type.
        channel: Distribution channel.
        body: Main content body.
        cta: Call-to-action text.
        target_segment: Target audience segment.
        tone: Content tone/style.
        keywords: SEO/target keywords.
        word_count: Word count of the content.
        variant: Variant letter for A/B testing.
        performance: Performance metrics (if tested).
        created_at: ISO 8601 creation timestamp.
    """
    id: str
    title: str
    format: ContentFormat = ContentFormat.BLOG_POST
    channel: Channel = Channel.CONTENT
    body: str = ""
    cta: str = ""
    target_segment: str = ""
    tone: str = "professional"
    keywords: List[str] = field(default_factory=list)
    word_count: int = 0
    variant: str = "A"
    performance: Dict[str, Any] = field(default_factory=dict)
    created_at: Optional[str] = None


@dataclass
class ABTestResult:
    """Represents results of an A/B test.

    Attributes:
        test_id: Unique test identifier.
        campaign_id: Associated campaign ID.
        variant_a: Variant A details.
        variant_b: Variant B details.
        metric: Primary metric being tested.
        winner: Winning variant (A, B, or inconclusive).
        confidence: Statistical confidence level (0-1).
        lift: Percentage lift of winner over loser.
        sample_size: Number of users in each variant.
        duration_hours: Test duration in hours.
        details: Detailed test metrics.
    """
    test_id: str
    campaign_id: str
    variant_a: Dict[str, Any] = field(default_factory=dict)
    variant_b: Dict[str, Any] = field(default_factory=dict)
    metric: str = "conversion_rate"
    winner: str = "inconclusive"
    confidence: float = 0.0
    lift: float = 0.0
    sample_size: int = 0
    duration_hours: float = 0.0
    details: Dict[str, Any] = field(default_factory=dict)


@dataclass
class Campaign:
    """Represents a marketing campaign.

    Attributes:
        id: Unique campaign identifier.
        name: Campaign name.
        type: Campaign type/objective.
        description: Campaign description.
        target_audience: Target audience segments.
        channels: Channels to use.
        budget: Campaign budget.
        duration_days: Campaign duration in days.
        content: List of content pieces in the campaign.
        ab_tests: List of A/B tests in the campaign.
        performance: Overall campaign performance metrics.
        status: Current campaign status.
        start_date: ISO 8601 start date.
        end_date: ISO 8601 end date.
    """
    id: str
    name: str
    type: CampaignType = CampaignType.BRAND_AWARENESS
    description: str = ""
    target_audience: List[TargetAudience] = field(default_factory=list)
    channels: List[Channel] = field(default_factory=list)
    budget: float = 0.0
    duration_days: int = 30
    content: List[MarketingContent] = field(default_factory=list)
    ab_tests: List[ABTestResult] = field(default_factory=list)
    performance: Dict[str, Any] = field(default_factory=dict)
    status: str = "draft"
    start_date: Optional[str] = None
    end_date: Optional[str] = None


@dataclass
class MarketingMetrics(SolutionMetrics):
    """Extended metrics for the marketing assistant solution.

    Attributes:
        engagement_rate: Average engagement rate across campaigns.
        conversion_rate: Average conversion rate.
        avg_roi: Average return on investment.
        campaigns_launched: Total campaigns launched.
        content_created: Total content pieces created.
        ab_tests_run: Total A/B tests executed.
        avg_open_rate: Average email open rate.
        avg_click_through_rate: Average click-through rate.
        cost_per_acquisition: Average customer acquisition cost.
    """
    engagement_rate: float = 0.0
    conversion_rate: float = 0.0
    avg_roi: float = 0.0
    campaigns_launched: int = 0
    content_created: int = 0
    ab_tests_run: int = 0
    avg_open_rate: float = 0.0
    avg_click_through_rate: float = 0.0
    cost_per_acquisition: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        """Convert marketing metrics to a dictionary."""
        base = super().to_dict()
        base.update({
            "engagement_rate": self.engagement_rate,
            "conversion_rate": self.conversion_rate,
            "avg_roi": self.avg_roi,
            "campaigns_launched": self.campaigns_launched,
            "content_created": self.content_created,
            "ab_tests_run": self.ab_tests_run,
            "avg_open_rate": self.avg_open_rate,
            "avg_click_through_rate": self.avg_click_through_rate,
            "cost_per_acquisition": self.cost_per_acquisition,
        })
        return base


class MarketingAssistantSolution(BaseSolution):
    """Marketing AI Assistant solution template.

    Provides a complete, pre-configured marketing AI assistant
    with multi-agent collaboration for market research, copywriting,
    campaign design, and data analysis with A/B testing.

    Pre-configured agent roles:
        - Market Researcher: Conducts market and audience research
        - Copywriter: Creates compelling marketing copy and content
        - Campaign Designer: Designs multi-channel marketing campaigns
        - Data Analyst: Analyzes campaign performance and provides insights

    Workflow:
        1. Market Research - Analyze market, audience, and competitors
        2. Campaign Design - Design campaign strategy and structure
        3. Content Creation - Generate marketing content and copy
        4. A/B Testing - Create variants and run optimization tests
        5. Performance Analysis - Analyze results and provide insights

    Configuration options:
        - industry: Industry for marketing context
        - target_audience: Primary target audience segment
        - channels: Marketing channels to use
        - tone: Default content tone
        - ab_testing_enabled: Whether to enable A/B testing
    """

    def _build_metadata(self) -> SolutionMetadata:
        """Build metadata for the marketing assistant solution."""
        return SolutionMetadata(
            name="marketing-assistant",
            version="1.0.0",
            industry="marketing",
            description=(
                "AI-powered marketing assistant solution with market research, "
                "campaign design, content creation, A/B testing, and performance analysis."
            ),
            author="LiuHao AI OS",
            tags=["marketing", "advertising", "content", "campaigns", "ab-testing", "analytics"],
            min_liuhao_version="1.0.0",
        )

    def _build_config_schema(self) -> None:
        """Define configuration fields for the marketing assistant solution."""
        self.config_schema.add_field(
            name="industry",
            field_type="string",
            required=True,
            description="Industry for marketing context",
        )
        self.config_schema.add_field(
            name="brand_name",
            field_type="string",
            required=False,
            default="",
            description="Brand name to use in marketing content",
        )
        self.config_schema.add_field(
            name="tone",
            field_type="string",
            required=False,
            default="professional",
            description="Default content tone/style",
            valid_values=["professional", "friendly", "casual", "bold", "luxury", "playful"],
        )
        self.config_schema.add_field(
            name="primary_channel",
            field_type="string",
            required=False,
            default="email",
            description="Primary marketing channel",
            valid_values=[c.value for c in Channel],
        )
        self.config_schema.add_field(
            name="ab_testing_enabled",
            field_type="bool",
            required=False,
            default=True,
            description="Whether to enable A/B testing for content",
        )
        self.config_schema.add_field(
            name="campaign_type",
            field_type="string",
            required=False,
            default="lead_generation",
            description="Default campaign type/objective",
            valid_values=[t.value for t in CampaignType],
        )
        self.config_schema.add_field(
            name="default_budget",
            field_type="float",
            required=False,
            default=10000.0,
            description="Default campaign budget",
        )
        self.config_schema.add_field(
            name="content_quantity",
            field_type="string",
            required=False,
            default="standard",
            description="Quantity of content to generate per campaign",
            valid_values=["minimal", "standard", "comprehensive", "full_funnel"],
        )

    def _on_initialize(self) -> None:
        """Initialize marketing assistant solution state."""
        self._agents: Dict[str, Dict[str, Any]] = {}
        self._campaigns: Dict[str, Campaign] = {}
        self._content_library: Dict[str, MarketingContent] = {}
        self._ab_tests: Dict[str, ABTestResult] = {}
        self._campaign_count = 0
        self._content_count = 0
        self._test_count = 0

        # Initialize marketing-specific metrics
        self.marketing_metrics = MarketingMetrics()

    def _on_deploy(self) -> None:
        """Deploy marketing assistant agents and workflows."""
        agent_configs = [
            (MarketingRole.MARKET_RESEARCHER, {
                "name": "Market Researcher",
                "description": "Conducts market and audience research",
                "system_prompt": self._build_market_researcher_prompt(),
                "tools": ["market_data", "competitor_analysis", "audience_insights"],
            }),
            (MarketingRole.COPYWRITER, {
                "name": "Copywriter",
                "description": "Creates compelling marketing copy and content",
                "system_prompt": self._build_copywriter_prompt(),
                "tools": ["content_generator", "headline_tester", "seo_optimizer"],
            }),
            (MarketingRole.CAMPAIGN_DESIGNER, {
                "name": "Campaign Designer",
                "description": "Designs multi-channel marketing campaigns",
                "system_prompt": self._build_campaign_designer_prompt(),
                "tools": ["campaign_builder", "journey_mapper", "channel_optimizer"],
            }),
            (MarketingRole.DATA_ANALYST, {
                "name": "Data Analyst",
                "description": "Analyzes campaign performance and provides insights",
                "system_prompt": self._build_data_analyst_prompt(),
                "tools": ["analytics", "ab_test_engine", "attribution_modeling"],
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
        """Process a marketing campaign through the full workflow.

        Args:
            input_data: Dictionary containing campaign brief with at least
                ``campaign_name`` and ``objective``.

        Returns:
            Dictionary with campaign results including strategy, content,
            A/B test plan, and performance projections.
        """
        # Step 1: Market research
        audience, market_insights = self._conduct_research(input_data)

        # Step 2: Campaign design
        campaign = self._design_campaign(input_data, audience, market_insights)

        # Step 3: Content creation
        content_list = self._create_content(campaign)
        campaign.content = content_list

        # Step 4: A/B testing
        ab_tests = []
        if self.config.get("ab_testing_enabled", True):
            ab_tests = self._create_ab_tests(campaign)
            campaign.ab_tests = ab_tests

        # Step 5: Performance analysis (projections)
        performance = self._analyze_performance(campaign)
        campaign.performance = performance

        # Update metrics
        self.marketing_metrics.campaigns_launched += 1
        self.marketing_metrics.content_created += len(content_list)
        self.marketing_metrics.ab_tests_run += len(ab_tests)
        self._update_marketing_metrics()

        return {
            "campaign_id": campaign.id,
            "campaign": {
                "name": campaign.name,
                "type": campaign.type.value,
                "channels": [c.value for c in campaign.channels],
                "budget": campaign.budget,
                "duration_days": campaign.duration_days,
                "status": campaign.status,
            },
            "market_research": {
                "audience_segments": [
                    {
                        "name": a.segment_name,
                        "priority": a.priority,
                        "pain_points": a.pain_points[:3],
                        "channels": [c.value for c in a.channels],
                    }
                    for a in audience
                ],
                "insights": market_insights,
            },
            "content": {
                "total_pieces": len(content_list),
                "items": [
                    {
                        "id": c.id,
                        "title": c.title,
                        "format": c.format.value,
                        "channel": c.channel.value,
                        "variant": c.variant,
                    }
                    for c in content_list
                ],
            },
            "ab_tests": {
                "total_tests": len(ab_tests),
                "tests": [
                    {
                        "test_id": t.test_id,
                        "metric": t.metric,
                        "winner": t.winner,
                        "confidence": t.confidence,
                        "lift": t.lift,
                    }
                    for t in ab_tests
                ],
            },
            "performance_projections": performance,
        }

    def _on_teardown(self) -> None:
        """Clean up marketing assistant solution resources."""
        self._agents.clear()
        self._campaigns.clear()
        self._content_library.clear()
        self._ab_tests.clear()
        self._campaign_count = 0
        self._content_count = 0
        self._test_count = 0

    # ------------------------------------------------------------------
    # Workflow steps
    # ------------------------------------------------------------------

    def _conduct_research(
        self, input_data: Dict[str, Any]
    ) -> tuple[List[TargetAudience], List[str]]:
        """Step 1: Conduct market and audience research."""
        industry = self.config.get("industry", "general")
        campaign_type = input_data.get(
            "campaign_type", self.config.get("campaign_type", CampaignType.LEAD_GENERATION.value)
        )

        # Create target audience segments
        audiences = [
            TargetAudience(
                segment_name="Primary Audience",
                demographics={
                    "age_range": "25-45",
                    "income": "medium-high",
                    "location": "national",
                },
                psychographics=[
                    "Quality-conscious",
                    "Value convenience",
                    "Research before purchase",
                ],
                pain_points=[
                    f"Finding reliable {industry} solutions",
                    "Time-consuming decision process",
                    "Unclear pricing and value",
                ],
                motivations=[
                    "Save time and effort",
                    "Get better results",
                    "Reduce costs",
                ],
                channels=[Channel.EMAIL, Channel.SOCIAL_MEDIA, Channel.SEARCH],
                size_estimate="Large",
                priority=1,
            ),
            TargetAudience(
                segment_name="Secondary Audience",
                demographics={
                    "age_range": "35-55",
                    "income": "high",
                    "location": "urban",
                },
                psychographics=[
                    "Decision-makers",
                    "ROI-focused",
                    "Prefer data-driven insights",
                ],
                pain_points=[
                    "Need proven ROI",
                    "Integration complexity concerns",
                    "Team adoption challenges",
                ],
                motivations=[
                    "Drive business growth",
                    "Improve efficiency",
                    "Gain competitive advantage",
                ],
                channels=[Channel.EMAIL, Channel.CONTENT, Channel.LINKEDIN if hasattr(Channel, 'LINKEDIN') else Channel.SOCIAL_MEDIA],
                size_estimate="Medium",
                priority=2,
            ),
        ]

        # Market insights
        insights = [
            f"Growing demand for {industry} solutions in digital channels",
            "Content marketing drives 3x more leads than traditional outbound",
            "Personalized campaigns show 20% higher conversion rates",
            "Video content continues to outperform other formats",
            f"Mobile engagement accounts for 60%+ of {industry} digital interactions",
        ]

        return audiences, insights

    def _design_campaign(
        self,
        input_data: Dict[str, Any],
        audiences: List[TargetAudience],
        insights: List[str],
    ) -> Campaign:
        """Step 2: Design the marketing campaign."""
        self._campaign_count += 1
        campaign_id = f"campaign-{self._campaign_count}"

        campaign_type = CampaignType(
            input_data.get("campaign_type", self.config.get("campaign_type", "lead_generation"))
        )
        name = input_data.get("campaign_name", f"Campaign {self._campaign_count}")
        description = input_data.get("description", f"{campaign_type.value.replace('_', ' ').title()} campaign for {self.config.get('industry', 'our brand')}")

        # Determine channels based on audience and campaign type
        channels_set = set()
        for audience in audiences:
            for ch in audience.channels:
                channels_set.add(ch)
        channels = list(channels_set)

        # Budget allocation
        total_budget = input_data.get("budget", self.config.get("default_budget", 10000.0))

        campaign = Campaign(
            id=campaign_id,
            name=name,
            type=campaign_type,
            description=description,
            target_audience=audiences,
            channels=channels,
            budget=total_budget,
            duration_days=input_data.get("duration_days", 30),
            status="designed",
        )
        self._campaigns[campaign_id] = campaign
        return campaign

    def _create_content(self, campaign: Campaign) -> List[MarketingContent]:
        """Step 3: Generate marketing content for the campaign."""
        content_quantity = self.config.get("content_quantity", "standard")
        quantity_map = {
            "minimal": 3,
            "standard": 6,
            "comprehensive": 10,
            "full_funnel": 15,
        }
        num_pieces = quantity_map.get(content_quantity, 6)

        content_list = []
        formats = [ContentFormat.EMAIL, ContentFormat.SOCIAL_POST, ContentFormat.BLOG_POST,
                   ContentFormat.AD_COPY, ContentFormat.LANDING_PAGE, ContentFormat.NEWSLETTER]

        for i in range(num_pieces):
            self._content_count += 1
            fmt = formats[i % len(formats)]
            channel = self._channel_for_format(fmt)
            segment = campaign.target_audience[i % len(campaign.target_audience)].segment_name

            content = MarketingContent(
                id=f"content-{self._content_count}",
                title=f"{campaign.type.value.replace('_', ' ').title()} - {fmt.value.replace('_', ' ').title()} {i+1}",
                format=fmt,
                channel=channel,
                body=(
                    f"Marketing content for {campaign.name} targeting {segment}. "
                    f"Key message: Solve your {self.config.get('industry', 'business')} challenges "
                    f"with our proven solution. Experience the difference today."
                ),
                cta="Get Started Today",
                target_segment=segment,
                tone=self.config.get("tone", "professional"),
                keywords=[self.config.get("industry", "solution"), "best", "affordable", "results"],
                word_count=300 + (i * 50),
                variant="A",
            )
            content_list.append(content)
            self._content_library[content.id] = content

        return content_list

    def _channel_for_format(self, fmt: ContentFormat) -> Channel:
        """Map content format to appropriate channel."""
        mapping = {
            ContentFormat.EMAIL: Channel.EMAIL,
            ContentFormat.SOCIAL_POST: Channel.SOCIAL_MEDIA,
            ContentFormat.AD_COPY: Channel.PAID_SOCIAL,
            ContentFormat.BLOG_POST: Channel.CONTENT,
            ContentFormat.LANDING_PAGE: Channel.SEARCH,
            ContentFormat.VIDEO_SCRIPT: Channel.VIDEO,
            ContentFormat.NEWSLETTER: Channel.EMAIL,
            ContentFormat.WHITEPAPER: Channel.CONTENT,
            ContentFormat.CASE_STUDY: Channel.CONTENT,
            ContentFormat.INFOGRAPHIC: Channel.SOCIAL_MEDIA,
        }
        return mapping.get(fmt, Channel.CONTENT)

    def _create_ab_tests(self, campaign: Campaign) -> List[ABTestResult]:
        """Step 4: Create and simulate A/B tests for the campaign."""
        if not campaign.content:
            return []

        tests = []
        test_metrics = ["open_rate", "click_through_rate", "conversion_rate", "engagement"]

        # Create 2-3 A/B tests
        num_tests = min(3, len(campaign.content) // 2)
        for i in range(num_tests):
            self._test_count += 1
            base_content = campaign.content[i]

            # Simulate test results
            lift = 5.0 + (i * 3.5)  # 5% to 12% lift
            confidence = 0.85 + (i * 0.05)
            winner = "A" if i % 2 == 0 else "B"

            test = ABTestResult(
                test_id=f"abtest-{self._test_count}",
                campaign_id=campaign.id,
                variant_a={
                    "title": base_content.title,
                    "description": "Original version",
                    "performance": {"conversion_rate": 0.025},
                },
                variant_b={
                    "title": f"{base_content.title} [Variant B]",
                    "description": "Optimized version with improved headline",
                    "performance": {"conversion_rate": 0.025 * (1 + lift/100)},
                },
                metric=test_metrics[i % len(test_metrics)],
                winner=winner,
                confidence=round(min(0.99, confidence), 3),
                lift=round(lift, 1),
                sample_size=1000 + (i * 500),
                duration_hours=72.0,
                details={
                    "variant_a_users": 1000,
                    "variant_b_users": 1000,
                    "stat_significance": "p < 0.05",
                },
            )
            tests.append(test)
            self._ab_tests[test.test_id] = test

        return tests

    def _analyze_performance(self, campaign: Campaign) -> Dict[str, Any]:
        """Step 5: Analyze and project campaign performance."""
        # Baseline metrics by campaign type
        baselines = {
            CampaignType.BRAND_AWARENESS: {"engagement": 0.08, "reach": 100000, "cpm": 5.0},
            CampaignType.LEAD_GENERATION: {"cvr": 0.03, "cpl": 25.0, "leads": 400},
            CampaignType.CONVERSION: {"cvr": 0.05, "cpa": 50.0, "revenue": 20000},
            CampaignType.RETENTION: {"retention": 0.85, "churn_reduction": 0.15, "ltv_boost": 0.10},
            CampaignType.ENGAGEMENT: {"engagement": 0.12, "time_on_site": 180, "shares": 500},
            CampaignType.LAUNCH: {"signups": 1000, "buzz_score": 75, "pr_mentions": 20},
        }

        baseline = baselines.get(campaign.type, baselines[CampaignType.LEAD_GENERATION])

        # Apply optimization lift from A/B tests
        ab_lift = sum(t.lift for t in campaign.ab_tests) / max(1, len(campaign.ab_tests)) / 100
        optimized_lift = 1 + ab_lift

        # Calculate projected metrics
        projected = {}
        for metric, value in baseline.items():
            if isinstance(value, (int, float)):
                projected[metric] = round(value * optimized_lift, 2)
            else:
                projected[metric] = value

        # ROI calculation
        if campaign.budget > 0:
            if "revenue" in projected:
                projected_roi = (projected["revenue"] - campaign.budget) / campaign.budget * 100
            elif "leads" in projected:
                # Assume $100 LTV per lead
                projected_revenue = projected["leads"] * 100
                projected_roi = (projected_revenue - campaign.budget) / campaign.budget * 100
            else:
                projected_roi = 120.0  # Default 120% ROI
        else:
            projected_roi = 0.0

        performance = {
            "projected_metrics": projected,
            "projected_roi_percent": round(projected_roi, 1),
            "ab_test_lift_percent": round(ab_lift * 100, 1),
            "budget_utilization": 0.9,
            "channel_performance": {
                ch.value: round(0.7 + (hash(ch.value) % 30) / 100, 2)
                for ch in campaign.channels
            },
            "recommendations": [
                "Focus budget on top-performing channels",
                "Scale winning A/B test variants",
                "Retarget engaged users for higher conversions",
                "Test additional creatives to maintain performance",
            ],
        }
        return performance

    # ------------------------------------------------------------------
    # Agent prompts
    # ------------------------------------------------------------------

    def _build_market_researcher_prompt(self) -> str:
        """Build the system prompt for the market researcher role."""
        industry = self.config.get("industry", "general")
        return (
            f"You are a market research specialist with deep expertise in the "
            f"{industry} industry. Conduct thorough market analysis including "
            f"audience segmentation, competitor analysis, trend identification, "
            f"and opportunity assessment. Use data-driven insights and provide "
            f"actionable recommendations. Identify key pain points, motivations, "
            f"and preferred channels for target audiences."
        )

    def _build_copywriter_prompt(self) -> str:
        """Build the system prompt for the copywriter role."""
        tone = self.config.get("tone", "professional")
        brand = self.config.get("brand_name", "our brand")
        return (
            f"You are a senior marketing copywriter specializing in the "
            f"{self.config.get('industry', 'technology')} industry. "
            f"Write compelling, conversion-focused copy in a {tone} tone "
            f"for {brand}. Create attention-grabbing headlines, clear "
            f"value propositions, and strong calls-to-action. Adapt copy "
            f"for different channels and formats. Always focus on benefits "
            f"over features and speak directly to audience pain points."
        )

    def _build_campaign_designer_prompt(self) -> str:
        """Build the system prompt for the campaign designer role."""
        return (
            "You are a marketing campaign strategist. Design comprehensive, "
            "multi-channel marketing campaigns that align with business goals. "
            "Create customer journeys, allocate budgets effectively across channels, "
            "and plan content calendars. Consider the full marketing funnel from "
            "awareness to conversion to retention. Ensure campaigns are measurable "
            "and include clear KPIs and optimization opportunities."
        )

    def _build_data_analyst_prompt(self) -> str:
        """Build the system prompt for the data analyst role."""
        return (
            "You are a marketing data analyst who turns data into actionable "
            "insights. Analyze campaign performance across channels, identify "
            "trends and patterns, run A/B tests with proper statistical methods, "
            "and provide clear recommendations for optimization. Use attribution "
            "modeling to understand customer journeys. Present findings clearly "
            "with both numbers and strategic implications."
        )

    # ------------------------------------------------------------------
    # Metrics
    # ------------------------------------------------------------------

    def _update_marketing_metrics(self) -> None:
        """Update marketing-specific metrics."""
        if self._campaigns:
            performances = [c.performance for c in self._campaigns.values() if c.performance]
            if performances:
                rois = [p.get("projected_roi_percent", 0) for p in performances]
                self.marketing_metrics.avg_roi = sum(rois) / len(rois)

        # Set baseline rates
        self.marketing_metrics.engagement_rate = 0.065
        self.marketing_metrics.conversion_rate = 0.032
        self.marketing_metrics.avg_open_rate = 0.22
        self.marketing_metrics.avg_click_through_rate = 0.045
        self.marketing_metrics.cost_per_acquisition = 45.0

    def get_marketing_metrics(self) -> Dict[str, Any]:
        """Get the current marketing-specific metrics.

        Returns:
            Dictionary with marketing metrics including engagement rate,
            conversion rate, ROI, and content creation volume.
        """
        return self.marketing_metrics.to_dict()
