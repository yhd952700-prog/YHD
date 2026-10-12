"""Specialized agent types built on top of the base Agent class.

Each specialized agent **wraps** a base ``Agent`` instance using the
composition pattern — the base ``Agent`` class from
``src.ai.employee`` is never modified.  Specialized agents add
role-specific attributes and helper methods while delegating core
execution to the wrapped agent.

Provided specializations:
    - CodeAgent: coding, code review, refactoring
    - DataAnalystAgent: data analysis across multiple formats
    - ResearchAgent: research and fact-finding with citations
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple

from .employee import Agent


# =====================================================================
# CodeAgent — coding specialist
# =====================================================================


class CodeLanguage(str, Enum):
    """Programming languages supported by CodeAgent."""

    PYTHON = "python"
    JAVASCRIPT = "javascript"
    TYPESCRIPT = "typescript"
    JAVA = "java"
    CPP = "cpp"
    CSHARP = "csharp"
    GO = "go"
    RUST = "rust"
    RUBY = "ruby"
    PHP = "php"
    SWIFT = "swift"
    KOTLIN = "kotlin"
    SQL = "sql"
    BASH = "bash"


class CodeStyle(str, Enum):
    """Code style conventions."""

    PEP8 = "pep8"
    GOOGLE = "google"
    AIRBNB = "airbnb"
    STANDARD = "standard"
    PRETTIER = "prettier"
    CUSTOM = "custom"


class ComplexityLevel(str, Enum):
    """Task complexity ratings."""

    TRIVIAL = "trivial"
    EASY = "easy"
    MODERATE = "moderate"
    HARD = "hard"
    VERY_HARD = "very_hard"


@dataclass
class CodeReviewFinding:
    """A single finding from a code review."""

    severity: str  # "info", "warning", "error", "critical"
    category: str  # "style", "bug", "performance", "security", "architecture"
    description: str
    line_start: Optional[int] = None
    line_end: Optional[int] = None
    suggestion: Optional[str] = None


@dataclass
class CodeAgent:
    """Agent specialized in software development tasks.

    Uses **composition**: wraps a base ``Agent`` and adds
    coding-specific attributes and helper methods.

    Attributes:
        agent: The underlying base Agent instance.
        language_preferences: Ordered list of preferred languages (most
            preferred first).
        code_style: The style guide the agent follows.
        style_rules: Additional custom style rules (key-value pairs).
        completed_tasks: Count of completed coding tasks.
        bugs_found: Count of bugs found during review.
        lines_of_code: Estimated lines written or reviewed.
    """

    agent: Agent
    language_preferences: List[CodeLanguage] = field(
        default_factory=lambda: [CodeLanguage.PYTHON]
    )
    code_style: CodeStyle = CodeStyle.PEP8
    style_rules: Dict[str, str] = field(default_factory=dict)
    completed_tasks: int = 0
    bugs_found: int = 0
    lines_of_code: int = 0

    # -- delegation helpers -------------------------------------------------

    @property
    def name(self) -> str:
        """Return the wrapped agent's name."""
        return self.agent.name

    @property
    def id(self) -> str:
        """Return the wrapped agent's id."""
        return self.agent.id

    def execute(self, task: str, **kwargs) -> Dict[str, Any]:
        """Delegate execution to the underlying agent."""
        return self.agent.execute(task, **kwargs)

    # -- code-specific helpers ---------------------------------------------

    def prefers_language(self, language: CodeLanguage) -> bool:
        """Return True if the agent is comfortable with *language*."""
        return language in self.language_preferences

    def primary_language(self) -> CodeLanguage:
        """Return the agent's most-preferred language."""
        if not self.language_preferences:
            return CodeLanguage.PYTHON
        return self.language_preferences[0]

    def estimate_complexity(
        self,
        description: str,
        language: Optional[CodeLanguage] = None,
    ) -> ComplexityLevel:
        """Heuristic: estimate the complexity of a coding task.

        Uses simple keyword-based scoring to classify task difficulty.
        For production use, replace with an LLM-based assessment.

        Args:
            description: Short description of the coding task.
            language: Target language (affects baseline difficulty).

        Returns:
            A ``ComplexityLevel`` rating.
        """
        text = description.lower()
        score = 0

        # Keyword-based scoring
        easy_keywords = ["fix", "simple", "trivial", "small", "update",
                         "change", "rename", "add test", "format"]
        moderate_keywords = ["feature", "implement", "refactor", "api",
                             "module", "service", "component"]
        hard_keywords = ["architecture", "redesign", "migration",
                         "distributed", "performance optimization",
                         "rewrite", "complex", "system"]
        very_hard_keywords = ["ground up", "from scratch", "platform",
                              "full stack", "end to end", "mission critical"]

        for kw in easy_keywords:
            if kw in text:
                score += 1
        for kw in moderate_keywords:
            if kw in text:
                score += 3
        for kw in hard_keywords:
            if kw in text:
                score += 5
        for kw in very_hard_keywords:
            if kw in text:
                score += 8

        # Language adjustment
        if language is not None:
            low_complexity_langs = {CodeLanguage.SQL, CodeLanguage.BASH}
            high_complexity_langs = {CodeLanguage.RUST, CodeLanguage.CPP}
            if language in low_complexity_langs:
                score -= 1
            elif language in high_complexity_langs:
                score += 1

        # Map score to level
        if score <= 1:
            return ComplexityLevel.TRIVIAL
        elif score <= 3:
            return ComplexityLevel.EASY
        elif score <= 6:
            return ComplexityLevel.MODERATE
        elif score <= 10:
            return ComplexityLevel.HARD
        else:
            return ComplexityLevel.VERY_HARD

    def review_code(
        self,
        code: str,
        language: Optional[CodeLanguage] = None,
    ) -> List[CodeReviewFinding]:
        """Perform a heuristic code review and return findings.

        This is a lightweight static-analysis style review.  For a real
        review, call ``self.agent.execute()`` with a review prompt and
        parse the LLM response.

        Args:
            code: The source code to review.
            language: Language of the source code.

        Returns:
            List of ``CodeReviewFinding`` objects.
        """
        findings: List[CodeReviewFinding] = []
        lines = code.split("\n")

        for i, line in enumerate(lines, start=1):
            stripped = line.strip()

            # Check for TODO/FIXME comments
            if "todo" in stripped.lower() or "fixme" in stripped.lower():
                findings.append(CodeReviewFinding(
                    severity="info",
                    category="style",
                    description="TODO/FIXME comment found",
                    line_start=i,
                    suggestion="Consider tracking tasks in an issue tracker.",
                ))

            # Check for very long lines
            if len(line) > 120:
                findings.append(CodeReviewFinding(
                    severity="warning",
                    category="style",
                    description=f"Line {i} exceeds 120 characters ({len(line)})",
                    line_start=i,
                    suggestion="Break long lines for readability.",
                ))

            # Check for print debugging (Python-specific heuristic)
            if language == CodeLanguage.PYTHON and stripped.startswith("print("):
                findings.append(CodeReviewFinding(
                    severity="info",
                    category="style",
                    description="print() statement may be debug code",
                    line_start=i,
                    suggestion="Use proper logging instead of print().",
                ))

        # Check for empty file
        if not code.strip():
            findings.append(CodeReviewFinding(
                severity="warning",
                category="style",
                description="Code is empty or contains only whitespace.",
                suggestion="Add meaningful code or remove the file.",
            ))

        self.bugs_found += sum(1 for f in findings if f.severity in ("error", "critical"))
        self.lines_of_code += len(lines)

        return findings

    def record_completion(self, loc: int = 0) -> None:
        """Record that a coding task was completed."""
        self.completed_tasks += 1
        self.lines_of_code += loc


# =====================================================================
# DataAnalystAgent — data analysis specialist
# =====================================================================


class DataFormat(str, Enum):
    """Supported data formats for analysis."""

    CSV = "csv"
    JSON = "json"
    PARQUET = "parquet"
    EXCEL = "excel"
    SQL = "sql"
    XML = "xml"
    YAML = "yaml"
    PLAIN_TEXT = "plain_text"


class AnalysisMethod(str, Enum):
    """Analysis method preferences."""

    DESCRIPTIVE = "descriptive"
    INFERENTIAL = "inferential"
    PREDICTIVE = "predictive"
    EXPLORATORY = "exploratory"
    DIAGNOSTIC = "diagnostic"
    PRESCRIPTIVE = "prescriptive"


@dataclass
class DataInsight:
    """A single data insight produced by analysis."""

    title: str
    description: str
    insight_type: str  # "trend", "anomaly", "correlation", "summary", "pattern"
    confidence: float = 0.0  # 0.0 - 1.0
    supporting_data: Dict[str, Any] = field(default_factory=dict)
    timestamp: str = field(default_factory=lambda: datetime.now().isoformat())


@dataclass
class DataAnalystAgent:
    """Agent specialized in data analysis.

    Uses **composition**: wraps a base ``Agent`` and adds
    data-analysis-specific attributes and helper methods.

    Attributes:
        agent: The underlying base Agent instance.
        supported_formats: Data formats this agent can work with.
        preferred_methods: Ordered list of preferred analysis methods.
        analyses_completed: Count of analyses performed.
        insights_found: Count of insights produced.
        dataset_history: List of dataset summaries analyzed.
    """

    agent: Agent
    supported_formats: List[DataFormat] = field(
        default_factory=lambda: [DataFormat.CSV, DataFormat.JSON]
    )
    preferred_methods: List[AnalysisMethod] = field(
        default_factory=lambda: [AnalysisMethod.DESCRIPTIVE, AnalysisMethod.EXPLORATORY]
    )
    analyses_completed: int = 0
    insights_found: int = 0
    dataset_history: List[Dict[str, Any]] = field(default_factory=list)

    # -- delegation helpers -------------------------------------------------

    @property
    def name(self) -> str:
        """Return the wrapped agent's name."""
        return self.agent.name

    @property
    def id(self) -> str:
        """Return the wrapped agent's id."""
        return self.agent.id

    def execute(self, task: str, **kwargs) -> Dict[str, Any]:
        """Delegate execution to the underlying agent."""
        return self.agent.execute(task, **kwargs)

    # -- analysis-specific helpers -----------------------------------------

    def supports_format(self, fmt: DataFormat) -> bool:
        """Return True if the agent can work with *fmt*."""
        return fmt in self.supported_formats

    def primary_method(self) -> AnalysisMethod:
        """Return the agent's most-preferred analysis method."""
        if not self.preferred_methods:
            return AnalysisMethod.DESCRIPTIVE
        return self.preferred_methods[0]

    def suggest_method(
        self,
        data_size: int,
        goal: str,
    ) -> AnalysisMethod:
        """Suggest an analysis method based on data size and goal.

        Args:
            data_size: Number of rows / records in the dataset.
            goal: Description of what the analysis should achieve.

        Returns:
            A recommended ``AnalysisMethod``.
        """
        goal_lower = goal.lower()

        if any(kw in goal_lower for kw in ["predict", "forecast", "future", "machine learning"]):
            return AnalysisMethod.PREDICTIVE
        if any(kw in goal_lower for kw in ["why", "root cause", "diagnose", "reason"]):
            return AnalysisMethod.DIAGNOSTIC
        if any(kw in goal_lower for kw in ["recommend", "optimize", "best action"]):
            return AnalysisMethod.PRESCRIPTIVE
        if any(kw in goal_lower for kw in ["explore", "discover", "understand", "pattern"]):
            return AnalysisMethod.EXPLORATORY
        if any(kw in goal_lower for kw in ["hypothesis", "test", "significant", "confidence"]):
            return AnalysisMethod.INFERENTIAL
        return AnalysisMethod.DESCRIPTIVE

    def analyze(
        self,
        data: Any,
        goal: str = "describe",
        fmt: DataFormat = DataFormat.CSV,
    ) -> List[DataInsight]:
        """Perform a heuristic analysis and produce insights.

        This is a lightweight symbolic analysis.  For real analysis,
        call ``self.agent.execute()`` with an analysis prompt and
        parse the LLM response.

        Args:
            data: The data to analyze (format varies).
            goal: What the analysis should achieve.
            fmt: Format of the input data.

        Returns:
            List of ``DataInsight`` objects.
        """
        insights: List[DataInsight] = []

        # Try to get basic stats if data is a list of dicts
        if isinstance(data, list) and len(data) > 0:
            insights.append(DataInsight(
                title="Dataset Size",
                description=f"Dataset contains {len(data)} records.",
                insight_type="summary",
                confidence=1.0,
            ))

            # Field detection
            if isinstance(data[0], dict):
                fields = list(data[0].keys())
                insights.append(DataInsight(
                    title="Fields Detected",
                    description=f"Found {len(fields)} fields: {', '.join(fields)}",
                    insight_type="summary",
                    confidence=1.0,
                    supporting_data={"fields": fields},
                ))

                # Numeric field detection
                numeric_fields = []
                for field in fields:
                    if len(data) > 0 and isinstance(data[0].get(field), (int, float)):
                        numeric_fields.append(field)
                if numeric_fields:
                    insights.append(DataInsight(
                        title="Numeric Fields",
                        description=f"Found {len(numeric_fields)} numeric fields: {', '.join(numeric_fields)}",
                        insight_type="pattern",
                        confidence=0.9,
                    ))

        elif isinstance(data, dict):
            insights.append(DataInsight(
                title="Data Structure",
                description=f"Data is a dictionary with {len(data)} keys.",
                insight_type="summary",
                confidence=1.0,
            ))
        else:
            insights.append(DataInsight(
                title="Data Type",
                description=f"Data is of type {type(data).__name__}.",
                insight_type="summary",
                confidence=1.0,
            ))

        self.analyses_completed += 1
        self.insights_found += len(insights)
        self.dataset_history.append({
            "goal": goal,
            "format": fmt.value,
            "insights": len(insights),
            "timestamp": datetime.now().isoformat(),
        })

        return insights


# =====================================================================
# ResearchAgent — research & fact-finding specialist
# =====================================================================


class SourceReliability(str, Enum):
    """Reliability ratings for information sources."""

    VERIFIED = "verified"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"
    UNVERIFIED = "unverified"


@dataclass
class Citation:
    """A citation for a piece of information."""

    source: str
    url: Optional[str] = None
    author: Optional[str] = None
    publication_date: Optional[str] = None
    reliability: SourceReliability = SourceReliability.MEDIUM
    accessed_date: str = field(default_factory=lambda: datetime.now().isoformat())
    quote: Optional[str] = None

    def to_reference(self, style: str = "apa") -> str:
        """Format the citation as a reference string.

        Args:
            style: Citation style ("apa", "mla", "chicago", "simple").

        Returns:
            Formatted reference string.
        """
        if style == "simple":
            parts = [self.source]
            if self.url:
                parts.append(f"( {self.url} )")
            return " ".join(parts)

        # APA-like
        parts = []
        if self.author:
            parts.append(f"{self.author}.")
        if self.publication_date:
            parts.append(f"({self.publication_date}).")
        parts.append(self.source)
        if self.url:
            parts.append(f"Retrieved from {self.url}")
        return " ".join(parts)


@dataclass
class ResearchFinding:
    """A single research finding."""

    claim: str
    confidence: float  # 0.0 - 1.0
    citations: List[Citation] = field(default_factory=list)
    category: str = "general"
    notes: str = ""

    @property
    def source_reliability(self) -> SourceReliability:
        """Aggregate reliability of all citations.

        Returns the lowest reliability rating among citations, or
        ``UNVERIFIED`` if there are no citations.
        """
        if not self.citations:
            return SourceReliability.UNVERIFIED

        order = {
            SourceReliability.VERIFIED: 4,
            SourceReliability.HIGH: 3,
            SourceReliability.MEDIUM: 2,
            SourceReliability.LOW: 1,
            SourceReliability.UNVERIFIED: 0,
        }
        # Take the minimum (weakest link)
        min_level = min(order[c.reliability] for c in self.citations)
        for reliability, level in order.items():
            if level == min_level:
                return reliability
        return SourceReliability.UNVERIFIED


@dataclass
class ResearchAgent:
    """Agent specialized in research and fact-finding.

    Uses **composition**: wraps a base ``Agent`` and adds
    research-specific attributes and helper methods.

    Attributes:
        agent: The underlying base Agent instance.
        citation_manager: List of accumulated citations.
        findings: List of research findings produced.
        sources_evaluated: Count of sources evaluated.
        preferred_reliability: Minimum reliability threshold for sources.
        fact_check_enabled: Whether to verify claims against multiple sources.
    """

    agent: Agent
    citations: List[Citation] = field(default_factory=list)
    findings: List[ResearchFinding] = field(default_factory=list)
    sources_evaluated: int = 0
    preferred_reliability: SourceReliability = SourceReliability.MEDIUM
    fact_check_enabled: bool = True

    # -- delegation helpers -------------------------------------------------

    @property
    def name(self) -> str:
        """Return the wrapped agent's name."""
        return self.agent.name

    @property
    def id(self) -> str:
        """Return the wrapped agent's id."""
        return self.agent.id

    def execute(self, task: str, **kwargs) -> Dict[str, Any]:
        """Delegate execution to the underlying agent."""
        return self.agent.execute(task, **kwargs)

    # -- research-specific helpers -----------------------------------------

    def evaluate_source(
        self,
        source: str,
        domain: Optional[str] = None,
        has_peer_review: bool = False,
        is_official: bool = False,
    ) -> SourceReliability:
        """Evaluate the reliability of a source.

        Uses a heuristic based on domain type, peer review, and official
        status.  For production use, integrate with a fact-checking
        service or LLM-based evaluation.

        Args:
            source: Name or description of the source.
            domain: Domain name of the source (e.g. "gov", "edu", "com").
            has_peer_review: Whether the source is peer-reviewed.
            is_official: Whether the source is an official publication.

        Returns:
            A ``SourceReliability`` rating.
        """
        score = 0

        if has_peer_review:
            score += 3
        if is_official:
            score += 2

        if domain:
            domain = domain.lower()
            trusted_domains = ["gov", "edu", "ac.uk", "gov.uk", "nasa.gov",
                               "ieee.org", "acm.org", "nature.com", "science.org"]
            if any(td in domain for td in trusted_domains):
                score += 2
            elif "wikipedia.org" in domain:
                score += 1
            elif "blog" in domain or "medium.com" in domain:
                score -= 1

        self.sources_evaluated += 1

        if score >= 5:
            return SourceReliability.VERIFIED
        elif score >= 3:
            return SourceReliability.HIGH
        elif score >= 1:
            return SourceReliability.MEDIUM
        elif score >= 0:
            return SourceReliability.LOW
        else:
            return SourceReliability.UNVERIFIED

    def add_citation(self, citation: Citation) -> None:
        """Add a citation to the agent's citation manager."""
        self.citations.append(citation)

    def add_finding(self, finding: ResearchFinding) -> None:
        """Add a research finding and index its citations."""
        self.findings.append(finding)
        for citation in finding.citations:
            # Avoid duplicates by comparing source+url
            existing = {(c.source, c.url) for c in self.citations}
            if (citation.source, citation.url) not in existing:
                self.citations.append(citation)

    def find_supporting_citations(self, claim_keyword: str) -> List[Citation]:
        """Find citations whose source or quote mentions a keyword.

        Args:
            claim_keyword: Keyword to search for.

        Returns:
            List of matching ``Citation`` objects.
        """
        keyword = claim_keyword.lower()
        results = []
        for citation in self.citations:
            if (keyword in citation.source.lower()
                    or (citation.quote and keyword in citation.quote.lower())):
                results.append(citation)
        return results

    def bibliography(self, style: str = "apa") -> List[str]:
        """Generate a formatted bibliography from all citations.

        Args:
            style: Citation style ("apa", "mla", "chicago", "simple").

        Returns:
            List of formatted reference strings.
        """
        return [c.to_reference(style=style) for c in self.citations]

    def verified_findings(self) -> List[ResearchFinding]:
        """Return findings whose source reliability meets the threshold."""
        threshold_order = {
            SourceReliability.VERIFIED: 4,
            SourceReliability.HIGH: 3,
            SourceReliability.MEDIUM: 2,
            SourceReliability.LOW: 1,
            SourceReliability.UNVERIFIED: 0,
        }
        threshold = threshold_order[self.preferred_reliability]
        return [
            f for f in self.findings
            if threshold_order[f.source_reliability] >= threshold
        ]
