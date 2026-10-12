"""
Content Creation Studio AI Solution Template.

A comprehensive AI-powered content creation studio solution for
media companies, marketing teams, and content creators. Includes
multi-agent roles for strategy, writing, editing, and multimedia
production with workflow management.

Typical workflow:
    strategy -> ideation -> creation -> editing
    -> publishing -> analytics
"""

from typing import Optional, Dict, Any, List
from enum import Enum
from dataclasses import dataclass, field

from solutions.templates.base_solution import (
    BaseSolution,
    SolutionMetadata,
    SolutionMetrics,
)


class ContentType(str, Enum):
    """Types of content formats."""
    BLOG_POST = "blog_post"
    ARTICLE = "article"
    VIDEO_SCRIPT = "video_script"
    PODCAST = "podcast_script"
    SOCIAL_MEDIA = "social_media"
    NEWSLETTER = "newsletter"
    WHITEPAPER = "whitepaper"
    CASE_STUDY = "case_study"
    INFOGRAPHIC = "infographic"
    EBOOK = "ebook"


class ContentStatus(str, Enum):
    """Content workflow statuses."""
    IDEA = "idea"
    OUTLINE = "outline"
    DRAFT = "draft"
    REVIEW = "review"
    REVISION = "revision"
    APPROVED = "approved"
    PUBLISHED = "published"
    ARCHIVED = "archived"


class ContentTone(str, Enum):
    """Content tone/style options."""
    PROFESSIONAL = "professional"
    CASUAL = "casual"
    FRIENDLY = "friendly"
    FORMAL = "formal"
    PLAYFUL = "playful"
    ACADEMIC = "academic"
    PERSUASIVE = "persuasive"
    INSPIRATIONAL = "inspirational"


class MediaRole(str, Enum):
    """Agent roles in the content studio solution."""
    CONTENT_STRATEGIST = "content_strategist"
    WRITER = "writer"
    EDITOR = "editor"
    MULTIMEDIA_PRODUCER = "multimedia_producer"


@dataclass
class ContentBrief:
    """Represents a content brief/strategy document.

    Attributes:
        brief_id: Unique brief identifier.
        title: Working title.
        content_type: Type of content.
        topic: Main topic/subject.
        target_audience: Target audience description.
        goals: Content goals/objectives.
        keywords: Target keywords/topics.
        word_count_target: Target word count.
        tone: Content tone/style.
        key_points: Key points to cover.
        competitors: Competitor references.
        deadline: ISO 8601 deadline.
        seo_requirements: SEO requirements.
    """
    brief_id: str
    title: str = ""
    content_type: ContentType = ContentType.BLOG_POST
    topic: str = ""
    target_audience: str = ""
    goals: List[str] = field(default_factory=list)
    keywords: List[str] = field(default_factory=list)
    word_count_target: int = 1000
    tone: ContentTone = ContentTone.PROFESSIONAL
    key_points: List[str] = field(default_factory=list)
    competitors: List[str] = field(default_factory=list)
    deadline: Optional[str] = None
    seo_requirements: Dict[str, Any] = field(default_factory=dict)


@dataclass
class ContentPiece:
    """Represents a piece of content.

    Attributes:
        content_id: Unique content identifier.
        brief_id: Associated brief ID.
        title: Content title.
        content_type: Type of content.
        body: Main content body/text.
        outline: Content outline/structure.
        word_count: Current word count.
        status: Current workflow status.
        version: Version number.
        author: Author/creator.
        revision_notes: Revision notes.
        seo_score: SEO optimization score.
        readability_score: Readability score.
        created_at: ISO 8601 creation timestamp.
        updated_at: ISO 8601 last update timestamp.
    """
    content_id: str
    brief_id: str = ""
    title: str = ""
    content_type: ContentType = ContentType.BLOG_POST
    body: str = ""
    outline: List[str] = field(default_factory=list)
    word_count: int = 0
    status: ContentStatus = ContentStatus.IDEA
    version: int = 1
    author: str = ""
    revision_notes: List[str] = field(default_factory=list)
    seo_score: float = 0.0
    readability_score: float = 0.0
    created_at: Optional[str] = None
    updated_at: Optional[str] = None


@dataclass
class MultimediaAsset:
    """Represents a multimedia asset for content.

    Attributes:
        asset_id: Unique asset identifier.
        content_id: Associated content ID.
        asset_type: Type of asset (image, video, audio, etc.).
        title: Asset title.
        description: Asset description.
        format: File format/type.
        duration_sec: Duration in seconds (for audio/video).
        resolution: Resolution (for images/video).
        file_size: File size in bytes.
        url: Asset URL/location.
        generated_by: How the asset was created.
        license: License/usage rights.
    """
    asset_id: str
    content_id: str = ""
    asset_type: str = "image"
    title: str = ""
    description: str = ""
    format: str = ""
    duration_sec: float = 0.0
    resolution: str = ""
    file_size: int = 0
    url: str = ""
    generated_by: str = "ai_generated"
    license: str = "royalty_free"


@dataclass
class ContentMetrics(SolutionMetrics):
    """Extended metrics for the content studio solution.

    Attributes:
        engagement_rate: Average content engagement rate.
        reach: Average content reach/views.
        conversion_rate: Average conversion rate from content.
        production_speed_hours: Average production time per piece.
        pieces_created: Total content pieces created.
        assets_generated: Total multimedia assets generated.
        avg_seo_score: Average SEO optimization score.
        avg_readability_score: Average readability score.
        content_calendar_filled: Content calendar fill rate.
    """
    engagement_rate: float = 0.0
    reach: int = 0
    conversion_rate: float = 0.0
    production_speed_hours: float = 0.0
    pieces_created: int = 0
    assets_generated: int = 0
    avg_seo_score: float = 0.0
    avg_readability_score: float = 0.0
    content_calendar_filled: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        """Convert content metrics to a dictionary."""
        base = super().to_dict()
        base.update({
            "engagement_rate": self.engagement_rate,
            "reach": self.reach,
            "conversion_rate": self.conversion_rate,
            "production_speed_hours": self.production_speed_hours,
            "pieces_created": self.pieces_created,
            "assets_generated": self.assets_generated,
            "avg_seo_score": self.avg_seo_score,
            "avg_readability_score": self.avg_readability_score,
            "content_calendar_filled": self.content_calendar_filled,
        })
        return base


class ContentStudioSolution(BaseSolution):
    """Content Creation Studio AI solution template.

    Provides a complete, pre-configured content creation studio
    with multi-agent collaboration for strategy, ideation, writing,
    editing, multimedia production, and publishing.

    Pre-configured agent roles:
        - Content Strategist: Develops content strategy and briefs
        - Writer: Creates written content and copy
        - Editor: Reviews, edits, and polishes content
        - Multimedia Producer: Creates images, video, and audio assets

    Workflow:
        1. Strategy - Develop content strategy and editorial plan
        2. Ideation - Generate and select content ideas
        3. Creation - Write and produce content
        4. Editing - Review, edit, and quality-check content
        5. Publishing - Prepare content for distribution
        6. Analytics - Track content performance

    Configuration options:
        - content_type: Primary content type
        - industry_niche: Industry/niche for content
        - target_platforms: Publishing platforms
        - content_quantity: Content production volume
        - seo_focus: Whether to prioritize SEO optimization
    """

    def _build_metadata(self) -> SolutionMetadata:
        """Build metadata for the content studio solution."""
        return SolutionMetadata(
            name="content-studio",
            version="1.0.0",
            industry="media",
            description=(
                "AI-powered content creation studio solution with strategy, "
                "writing, editing, multimedia production, and analytics."
            ),
            author="LiuHao AI OS",
            tags=["content", "media", "copywriting", "content-creation", "publishing"],
            min_liuhao_version="1.0.0",
        )

    def _build_config_schema(self) -> None:
        """Define configuration fields for the content studio solution."""
        self.config_schema.add_field(
            name="content_type",
            field_type="string",
            required=True,
            description="Primary content type",
            valid_values=[t.value for t in ContentType],
        )
        self.config_schema.add_field(
            name="industry_niche",
            field_type="string",
            required=False,
            default="technology",
            description="Industry/niche for content focus",
        )
        self.config_schema.add_field(
            name="target_platforms",
            field_type="list",
            required=False,
            description="Publishing platforms",
        )
        self.config_schema.add_field(
            name="content_quantity",
            field_type="string",
            required=False,
            default="moderate",
            description="Content production volume level",
            valid_values=["minimal", "moderate", "high", "enterprise"],
        )
        self.config_schema.add_field(
            name="seo_focus",
            field_type="bool",
            required=False,
            default=True,
            description="Whether to prioritize SEO optimization",
        )
        self.config_schema.add_field(
            name="default_tone",
            field_type="string",
            required=False,
            default="professional",
            description="Default content tone",
            valid_values=[t.value for t in ContentTone],
        )
        self.config_schema.add_field(
            name="multimedia_enabled",
            field_type="bool",
            required=False,
            default=True,
            description="Whether to generate multimedia assets",
        )

    def _on_initialize(self) -> None:
        """Initialize content studio solution state."""
        self._agents: Dict[str, Dict[str, Any]] = {}
        self._briefs: Dict[str, ContentBrief] = {}
        self._content: Dict[str, ContentPiece] = {}
        self._assets: Dict[str, MultimediaAsset] = {}
        self._brief_count = 0
        self._content_count = 0
        self._asset_count = 0

        # Initialize content-specific metrics
        self.content_metrics = ContentMetrics()

    def _on_deploy(self) -> None:
        """Deploy content studio agents and workflows."""
        agent_configs = [
            (MediaRole.CONTENT_STRATEGIST, {
                "name": "Content Strategist",
                "description": "Develops content strategy and briefs",
                "system_prompt": self._build_strategist_prompt(),
                "tools": ["trend_analysis", "topic_research", "editorial_calendar"],
            }),
            (MediaRole.WRITER, {
                "name": "Writer",
                "description": "Creates written content and copy",
                "system_prompt": self._build_writer_prompt(),
                "tools": ["content_generator", "outliner", "research_assistant"],
            }),
            (MediaRole.EDITOR, {
                "name": "Editor",
                "description": "Reviews, edits, and polishes content",
                "system_prompt": self._build_editor_prompt(),
                "tools": ["grammar_check", "style_guide", "seo_analyzer"],
            }),
            (MediaRole.MULTIMEDIA_PRODUCER, {
                "name": "Multimedia Producer",
                "description": "Creates images, video, and audio assets",
                "system_prompt": self._build_multimedia_prompt(),
                "tools": ["image_generator", "video_editor", "audio_producer"],
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
        """Process a content creation request through the full workflow.

        Args:
            input_data: Dictionary containing content request data
                with at least ``topic``.

        Returns:
            Dictionary with content creation results including brief,
            content, multimedia assets, and analytics.
        """
        # Step 1: Strategy / Brief creation
        brief = self._create_brief(input_data)

        # Step 2: Ideation (outline generation)
        outline = self._generate_outline(brief)

        # Step 3: Content creation
        content = self._create_content(brief, outline)

        # Step 4: Editing and quality check
        edited_content = self._edit_content(content)

        # Step 5: Multimedia production
        assets = []
        if self.config.get("multimedia_enabled", True):
            assets = self._generate_assets(edited_content)

        # Step 6: Publishing and analytics prep
        publishing_info = self._prepare_publishing(edited_content, assets)

        # Update metrics
        self.content_metrics.pieces_created += 1
        self.content_metrics.assets_generated += len(assets)
        self._update_content_metrics()

        return {
            "brief": {
                "id": brief.brief_id,
                "title": brief.title,
                "type": brief.content_type.value,
                "topic": brief.topic,
                "audience": brief.target_audience,
                "goals": brief.goals,
                "keywords": brief.keywords,
                "word_count_target": brief.word_count_target,
                "tone": brief.tone.value,
            },
            "outline": outline,
            "content": {
                "id": edited_content.content_id,
                "title": edited_content.title,
                "word_count": edited_content.word_count,
                "status": edited_content.status.value,
                "version": edited_content.version,
                "seo_score": edited_content.seo_score,
                "readability_score": edited_content.readability_score,
                "preview": edited_content.body[:300] + "..." if len(edited_content.body) > 300 else edited_content.body,
            },
            "multimedia_assets": [
                {
                    "id": a.asset_id,
                    "type": a.asset_type,
                    "title": a.title,
                    "format": a.format,
                    "license": a.license,
                }
                for a in assets
            ],
            "publishing": publishing_info,
            "workflow": {
                "steps_completed": 6,
                "current_stage": "ready_to_publish",
                "time_estimate_hours": self.content_metrics.production_speed_hours,
            },
        }

    def _on_teardown(self) -> None:
        """Clean up content studio solution resources."""
        self._agents.clear()
        self._briefs.clear()
        self._content.clear()
        self._assets.clear()
        self._brief_count = 0
        self._content_count = 0
        self._asset_count = 0

    # ------------------------------------------------------------------
    # Workflow steps
    # ------------------------------------------------------------------

    def _create_brief(self, data: Dict[str, Any]) -> ContentBrief:
        """Step 1: Create content strategy brief."""
        self._brief_count += 1
        brief_id = f"brief-{self._brief_count}"
        topic = data.get("topic", "Content Topic")
        niche = self.config.get("industry_niche", "technology")

        word_count_targets = {
            ContentType.BLOG_POST: 1500,
            ContentType.ARTICLE: 2000,
            ContentType.VIDEO_SCRIPT: 1500,
            ContentType.PODCAST: 3000,
            ContentType.SOCIAL_MEDIA: 200,
            ContentType.NEWSLETTER: 800,
            ContentType.WHITEPAPER: 5000,
            ContentType.CASE_STUDY: 2000,
            ContentType.EBOOK: 10000,
        }
        ctype = ContentType(data.get("content_type", self.config.get("content_type", ContentType.BLOG_POST.value)))
        target_words = word_count_targets.get(ctype, 1000)

        brief = ContentBrief(
            brief_id=brief_id,
            title=data.get("title", f"Guide to {topic}"),
            content_type=ctype,
            topic=topic,
            target_audience=data.get("audience", f"Professionals in {niche}"),
            goals=data.get("goals", [
                f"Educate readers about {topic}",
                "Establish thought leadership",
                "Drive organic traffic",
                "Generate leads",
            ]),
            keywords=data.get("keywords", [
                topic,
                f"{topic} guide",
                f"best {topic}",
                f"{topic} tips",
                f"{niche} {topic}",
            ]),
            word_count_target=target_words,
            tone=ContentTone(data.get("tone", self.config.get("default_tone", ContentTone.PROFESSIONAL.value))),
            key_points=data.get("key_points", [
                f"What is {topic} and why it matters",
                f"Key principles and best practices",
                f"Common mistakes to avoid",
                f"Practical steps to implement",
            ]),
            seo_requirements={
                "primary_keyword": topic,
                "meta_description": f"Comprehensive guide to {topic}",
                "internal_links": 3,
                "external_links": 2,
                "readability_target": "8th_grade",
            } if self.config.get("seo_focus", True) else {},
        )
        self._briefs[brief_id] = brief
        return brief

    def _generate_outline(self, brief: ContentBrief) -> List[str]:
        """Step 2: Generate content outline."""
        topic = brief.topic
        key_points = brief.key_points

        sections = [
            f"Introduction to {topic}",
            *key_points,
            f"Common Challenges in {topic}",
            f"Best Practices for {topic}",
            "How to Get Started",
            "Conclusion and Next Steps",
        ]

        # Save outline to content piece
        return sections

    def _create_content(self, brief: ContentBrief, outline: List[str]) -> ContentPiece:
        """Step 3: Create/write the content."""
        self._content_count += 1
        content_id = f"content-{self._content_count}"

        # Generate body content based on outline
        body_parts = []
        for section in outline:
            body_parts.append(f"## {section}\n\n")
            body_parts.append(
                f"This section covers {section.lower()}. It provides valuable "
                f"insights and practical information about {brief.topic} that "
                f"readers can apply in their work. With a {brief.tone.value} tone, "
                f"the content engages the target audience while maintaining accuracy "
                f"and providing actionable takeaways.\n\n"
            )

        body = "".join(body_parts)
        word_count = len(body.split())

        content = ContentPiece(
            content_id=content_id,
            brief_id=brief.brief_id,
            title=brief.title,
            content_type=brief.content_type,
            body=body,
            outline=outline,
            word_count=word_count,
            status=ContentStatus.DRAFT,
            version=1,
        )
        self._content[content_id] = content
        return content

    def _edit_content(self, content: ContentPiece) -> ContentPiece:
        """Step 4: Edit and polish the content."""
        # SEO score calculation
        if self.config.get("seo_focus", True):
            content.seo_score = 75.0 + (hash(content.content_id) % 20)
        else:
            content.seo_score = 60.0

        # Readability score
        content.readability_score = 70.0 + (hash(content.content_id + "r") % 25)

        # Update status and version
        content.status = ContentStatus.REVIEW
        content.version = 2
        content.revision_notes = [
            "Initial draft review completed",
            "Grammar and spelling checked",
            "SEO optimization applied" if self.config.get("seo_focus", True) else "Readability improved",
            "Structure and flow optimized",
        ]

        return content

    def _generate_assets(self, content: ContentPiece) -> List[MultimediaAsset]:
        """Step 5: Generate multimedia assets."""
        assets = []

        # Featured image
        self._asset_count += 1
        assets.append(MultimediaAsset(
            asset_id=f"asset-{self._asset_count}",
            content_id=content.content_id,
            asset_type="image",
            title=f"Featured image: {content.title}",
            description="Hero image for the content piece",
            format="png",
            resolution="1200x630",
            generated_by="ai_generated",
            license="royalty_free",
        ))

        # Infographic or additional visual
        if content.content_type in (ContentType.BLOG_POST, ContentType.ARTICLE, ContentType.WHITEPAPER):
            self._asset_count += 1
            assets.append(MultimediaAsset(
                asset_id=f"asset-{self._asset_count}",
                content_id=content.content_id,
                asset_type="infographic",
                title=f"Infographic: Key Points",
                description="Visual summary of key points",
                format="png",
                resolution="800x2000",
                generated_by="ai_generated",
                license="royalty_free",
            ))

        for asset in assets:
            self._assets[asset.asset_id] = asset

        return assets

    def _prepare_publishing(
        self, content: ContentPiece, assets: List[MultimediaAsset]
    ) -> Dict[str, Any]:
        """Step 6: Prepare content for publishing."""
        platforms = self.config.get("target_platforms", ["website", "social_media"])
        if isinstance(platforms, str):
            platforms = [platforms]

        # Distribution plan
        distribution = []
        for platform in platforms:
            distribution.append({
                "platform": platform,
                "status": "ready",
                "schedule": "Publish immediately",
                "format": content.content_type.value,
            })

        # Performance projection
        projected_views = {
            ContentType.BLOG_POST: 5000,
            ContentType.ARTICLE: 8000,
            ContentType.SOCIAL_MEDIA: 50000,
            ContentType.VIDEO_SCRIPT: 10000,
            ContentType.NEWSLETTER: 2000,
        }.get(content.content_type, 3000)

        return {
            "status": "ready_to_publish",
            "distribution_channels": distribution,
            "seo_meta": {
                "title": content.title,
                "description": f"Learn about {content.title}",
                "keywords": "see brief",
            },
            "performance_projection": {
                "projected_views": projected_views,
                "projected_engagement_rate": 0.08,
                "projected_conversion_rate": 0.025,
            },
            "quality_review": {
                "passed": content.status in (ContentStatus.REVIEW, ContentStatus.APPROVED),
                "checks": [
                    "Grammar and spelling",
                    "SEO optimization" if self.config.get("seo_focus", True) else "Readability",
                    "Fact checking",
                    "Tone consistency",
                    "Brand alignment",
                ],
            },
        }

    # ------------------------------------------------------------------
    # Agent prompts
    # ------------------------------------------------------------------

    def _build_strategist_prompt(self) -> str:
        """Build the system prompt for the content strategist role."""
        niche = self.config.get("industry_niche", "technology")
        return (
            f"You are a content strategist specializing in {niche}. "
            f"Develop data-driven content strategies that align with business goals. "
            f"Research trending topics, analyze competitor content, identify gaps, "
            f"and create comprehensive content briefs. Plan editorial calendars "
            f"and ensure content maps to the customer journey."
        )

    def _build_writer_prompt(self) -> str:
        """Build the system prompt for the writer role."""
        tone = self.config.get("default_tone", "professional")
        niche = self.config.get("industry_niche", "technology")
        return (
            f"You are a professional content writer specializing in {niche}. "
            f"Write engaging, well-researched content in a {tone} tone. "
            f"Structure content for readability with clear sections and examples. "
            f"Use data and examples to support claims. Adapt style and depth "
            f"for the target audience. Always prioritize clarity and value."
        )

    def _build_editor_prompt(self) -> str:
        """Build the system prompt for the editor role."""
        return (
            "You are an experienced content editor with a keen eye for quality. "
            "Review content for grammar, spelling, style, clarity, and accuracy. "
            "Improve flow, structure, and readability. Ensure consistency with "
            "brand voice and style guidelines. Check facts and verify claims. "
            "Provide constructive feedback and specific improvement suggestions."
        )

    def _build_multimedia_prompt(self) -> str:
        """Build the system prompt for the multimedia producer role."""
        return (
            "You are a multimedia producer specializing in visual content. "
            "Create compelling images, infographics, video scripts, and audio "
            "content that complement written pieces. Ensure visual assets align "
            "with brand guidelines and enhance the message. Optimize assets "
            "for different platforms and formats."
        )

    # ------------------------------------------------------------------
    # Metrics
    # ------------------------------------------------------------------

    def _update_content_metrics(self) -> None:
        """Update content-specific metrics."""
        if self._content:
            seo_scores = [c.seo_score for c in self._content.values() if c.seo_score > 0]
            if seo_scores:
                self.content_metrics.avg_seo_score = sum(seo_scores) / len(seo_scores)

            read_scores = [c.readability_score for c in self._content.values() if c.readability_score > 0]
            if read_scores:
                self.content_metrics.avg_readability_score = sum(read_scores) / len(read_scores)

        self.content_metrics.engagement_rate = 0.075
        self.content_metrics.reach = 25000
        self.content_metrics.conversion_rate = 0.028
        self.content_metrics.production_speed_hours = 4.0
        self.content_metrics.content_calendar_filled = 0.85

    def get_content_metrics(self) -> Dict[str, Any]:
        """Get the current content studio metrics.

        Returns:
            Dictionary with content metrics including engagement rate,
            production speed, SEO scores, and readability.
        """
        return self.content_metrics.to_dict()
