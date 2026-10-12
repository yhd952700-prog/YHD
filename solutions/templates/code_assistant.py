"""
Code Assistant Solution Template.

A pre-configured solution template for building AI-powered code
assistance and review systems on the LiuHao AI OS platform. Includes
code reviewer, architect, and QA engineer roles with a structured
code review workflow.

Workflow:
    code review -> analysis -> suggestions -> testing -> documentation
"""

from typing import Optional, Dict, Any, List
from enum import Enum
from dataclasses import dataclass, field

from solutions.templates.base_solution import (
    BaseSolution,
    SolutionMetadata,
    SolutionMetrics,
)


class CodeReviewSeverity(str, Enum):
    """Severity levels for code review issues."""
    INFO = "info"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class CodeReviewCategory(str, Enum):
    """Categories of code review findings."""
    BUG = "bug"
    SECURITY = "security"
    PERFORMANCE = "performance"
    STYLE = "style"
    BEST_PRACTICE = "best_practice"
    MAINTAINABILITY = "maintainability"
    DOCUMENTATION = "documentation"
    ARCHITECTURE = "architecture"


class CodeRole(str, Enum):
    """Pre-defined agent roles in the code assistant solution."""
    CODE_REVIEWER = "code_reviewer"
    ARCHITECT = "architect"
    QA_ENGINEER = "qa_engineer"
    DOCUMENTATION_SPECIALIST = "documentation_specialist"


@dataclass
class CodeIssue:
    """Represents a single issue found during code review.

    Attributes:
        id: Unique issue identifier.
        severity: Severity level.
        category: Issue category.
        file: File path where the issue was found.
        line: Line number (1-based).
        title: Short issue title/summary.
        description: Detailed issue description.
        suggestion: Suggested fix.
        code_snippet: Relevant code snippet.
        confidence: Confidence score (0-1).
    """
    id: str
    severity: CodeReviewSeverity
    category: CodeReviewCategory
    file: str
    line: int
    title: str
    description: str = ""
    suggestion: str = ""
    code_snippet: str = ""
    confidence: float = 0.0


@dataclass
class CodeReviewResult:
    """Result of a complete code review.

    Attributes:
        review_id: Unique review identifier.
        files_reviewed: Number of files reviewed.
        total_issues: Total number of issues found.
        issues: List of code issues found.
        overall_score: Overall code quality score (0-100).
        summary: Summary of findings.
        recommendations: List of recommendations.
    """
    review_id: str
    files_reviewed: int = 0
    total_issues: int = 0
    issues: List[CodeIssue] = field(default_factory=list)
    overall_score: float = 0.0
    summary: str = ""
    recommendations: List[str] = field(default_factory=list)


@dataclass
class CodeMetrics(SolutionMetrics):
    """Extended metrics for the code assistant solution.

    Attributes:
        total_reviews: Total number of code reviews performed.
        avg_issues_per_review: Average number of issues found per review.
        avg_code_quality_score: Average code quality score across reviews.
        bugs_found: Total number of bugs identified.
        security_issues_found: Total number of security issues.
        suggestions_accepted: Number of suggestions accepted/applied.
        languages_analyzed: Set of programming languages analyzed.
    """
    total_reviews: int = 0
    avg_issues_per_review: float = 0.0
    avg_code_quality_score: float = 0.0
    bugs_found: int = 0
    security_issues_found: int = 0
    suggestions_accepted: int = 0
    languages_analyzed: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        """Convert code metrics to a dictionary."""
        base = super().to_dict()
        base.update({
            "total_reviews": self.total_reviews,
            "avg_issues_per_review": self.avg_issues_per_review,
            "avg_code_quality_score": self.avg_code_quality_score,
            "bugs_found": self.bugs_found,
            "security_issues_found": self.security_issues_found,
            "suggestions_accepted": self.suggestions_accepted,
            "languages_analyzed": list(self.languages_analyzed),
        })
        return base


class CodeAssistantSolution(BaseSolution):
    """Code Assistant solution template.

    Provides a comprehensive AI-powered code review and assistance
    solution with multiple specialized agent roles.

    Pre-configured roles:
        - Code Reviewer: Performs detailed code review, finds bugs and style issues
        - Architect: Reviews architecture, design patterns, and system design
        - QA Engineer: Analyzes test coverage and suggests test cases
        - Documentation Specialist: Reviews and improves code documentation

    Workflow:
        1. Code Review - Analyze code for issues across multiple dimensions
        2. Analysis - Deep analysis of architecture, patterns, and design
        3. Suggestions - Generate actionable improvement suggestions
        4. Testing - Analyze test coverage and suggest test cases
        5. Documentation - Review and suggest documentation improvements

    Configuration options:
        - languages: List of programming languages to support
        - strictness: Review strictness level (lenient, default, strict)
        - check_security: Whether to run security checks
        - check_performance: Whether to check for performance issues
        - required_severity: Minimum severity to report
        - auto_fix_enabled: Whether to suggest auto-fixable changes
    """

    def _build_metadata(self) -> SolutionMetadata:
        """Build metadata for the code assistant solution."""
        return SolutionMetadata(
            name="code-assistant",
            version="1.0.0",
            industry="software-development",
            description=(
                "AI-powered code review and assistance solution with "
                "multi-role analysis, best practices enforcement, "
                "and automated quality checks."
            ),
            author="LiuHao AI OS",
            tags=["code-review", "code-quality", "devtools", "ai-coding"],
            min_liuhao_version="1.0.0",
        )

    def _build_config_schema(self) -> None:
        """Define configuration fields for the code assistant solution."""
        self.config_schema.add_field(
            name="languages",
            field_type="list",
            required=False,
            default=["python", "javascript", "typescript"],
            description="List of programming languages to support",
        )
        self.config_schema.add_field(
            name="strictness",
            field_type="string",
            required=False,
            default="default",
            description="Review strictness level",
            valid_values=["lenient", "default", "strict"],
        )
        self.config_schema.add_field(
            name="check_security",
            field_type="bool",
            required=False,
            default=True,
            description="Whether to run security vulnerability checks",
        )
        self.config_schema.add_field(
            name="check_performance",
            field_type="bool",
            required=False,
            default=True,
            description="Whether to check for performance issues",
        )
        self.config_schema.add_field(
            name="required_severity",
            field_type="string",
            required=False,
            default="low",
            description="Minimum issue severity to report",
            valid_values=["info", "low", "medium", "high", "critical"],
        )
        self.config_schema.add_field(
            name="auto_fix_enabled",
            field_type="bool",
            required=False,
            default=False,
            description="Whether to include auto-fix suggestions",
        )
        self.config_schema.add_field(
            name="max_files_per_review",
            field_type="int",
            required=False,
            default=50,
            description="Maximum number of files to review in one pass",
        )
        self.config_schema.add_field(
            name="coding_standards",
            field_type="dict",
            required=False,
            description="Coding standard configurations per language",
        )

    def _on_initialize(self) -> None:
        """Initialize code assistant solution state."""
        self._agents: Dict[str, Dict[str, Any]] = {}
        self._review_count = 0
        self._best_practices: Dict[str, List[str]] = {}
        self.code_metrics = CodeMetrics()

    def _on_deploy(self) -> None:
        """Deploy code assistant agents and best practices."""
        # Create agent roles
        agent_configs = [
            (CodeRole.CODE_REVIEWER, {
                "name": "Code Reviewer",
                "description": "Performs detailed code review",
                "specialties": ["bugs", "style", "best_practices"],
                "system_prompt": self._build_code_reviewer_prompt(),
            }),
            (CodeRole.ARCHITECT, {
                "name": "Architect",
                "description": "Reviews architecture and design patterns",
                "specialties": ["architecture", "design_patterns", "scalability"],
                "system_prompt": self._build_architect_prompt(),
            }),
            (CodeRole.QA_ENGINEER, {
                "name": "QA Engineer",
                "description": "Analyzes test coverage and suggests tests",
                "specialties": ["testing", "coverage", "edge_cases"],
                "system_prompt": self._build_qa_engineer_prompt(),
            }),
            (CodeRole.DOCUMENTATION_SPECIALIST, {
                "name": "Documentation Specialist",
                "description": "Reviews and improves code documentation",
                "specialties": ["docstrings", "api_docs", "comments"],
                "system_prompt": self._build_docs_prompt(),
            }),
        ]

        for role, config in agent_configs:
            self._agents[role.value] = {
                "role": role.value,
                "name": config["name"],
                "status": "deployed",
                **config,
            }

        # Load best practices
        self._load_best_practices()

    def _on_run(self, input_data: Dict[str, Any]) -> Dict[str, Any]:
        """Run a complete code review analysis.

        Args:
            input_data: Dictionary with code review input:
                - files: List of file objects with path and content
                - language: Programming language
                - context: Optional project context
                - diff: Optional diff for PR review mode

        Returns:
            Dictionary with review results including issues,
            overall score, and recommendations.
        """
        self._review_count += 1
        files = input_data.get("files", [])
        language = input_data.get("language", "python")
        context = input_data.get("context", {})

        # Track language
        if language not in self.code_metrics.languages_analyzed:
            self.code_metrics.languages_analyzed.append(language)

        # Step 1: Code review (find issues)
        issues = self._review_code(files, language, context)

        # Step 2: Architecture analysis
        arch_suggestions = self._analyze_architecture(files, language)

        # Step 3: Generate improvement suggestions
        suggestions = self._generate_suggestions(issues, arch_suggestions)

        # Step 4: Testing analysis
        test_suggestions = self._analyze_testing(files, language)

        # Step 5: Documentation review
        doc_suggestions = self._review_documentation(files, language)

        # Calculate overall score
        overall_score = self._calculate_overall_score(
            issues, len(files)
        )

        # Build result
        result = CodeReviewResult(
            review_id=f"review-{self._review_count}",
            files_reviewed=len(files),
            total_issues=len(issues),
            issues=issues,
            overall_score=overall_score,
            summary=self._generate_summary(issues, overall_score),
            recommendations=suggestions + test_suggestions + doc_suggestions,
        )

        # Update metrics
        self.code_metrics.total_reviews += 1
        self._update_avg_metrics(issues, overall_score)

        return {
            "review_id": result.review_id,
            "files_reviewed": result.files_reviewed,
            "total_issues": result.total_issues,
            "overall_score": result.overall_score,
            "issues_by_severity": self._group_issues_by_severity(issues),
            "issues_by_category": self._group_issues_by_category(issues),
            "issues": [
                {
                    "id": issue.id,
                    "severity": issue.severity.value,
                    "category": issue.category.value,
                    "file": issue.file,
                    "line": issue.line,
                    "title": issue.title,
                    "description": issue.description,
                    "suggestion": issue.suggestion,
                    "confidence": issue.confidence,
                }
                for issue in issues
            ],
            "summary": result.summary,
            "recommendations": result.recommendations,
        }

    def _on_teardown(self) -> None:
        """Clean up code assistant solution resources."""
        self._agents.clear()
        self._review_count = 0
        self._best_practices.clear()

    # ------------------------------------------------------------------
    # Workflow steps
    # ------------------------------------------------------------------

    def _review_code(
        self,
        files: List[Dict[str, Any]],
        language: str,
        context: Dict[str, Any],
    ) -> List[CodeIssue]:
        """Step 1: Review code for bugs, style, and best practice issues."""
        issues: List[CodeIssue] = []
        issue_id = 0
        strictness = self.config.get("strictness", "default")
        check_security = self.config.get("check_security", True)
        check_performance = self.config.get("check_performance", True)

        severity_multiplier = {
            "lenient": 0.7,
            "default": 1.0,
            "strict": 1.3,
        }.get(strictness, 1.0)

        for file_info in files:
            file_path = file_info.get("path", "unknown")
            content = file_info.get("content", "")
            lines = content.split("\n")

            # Simulate finding issues based on content length and patterns
            base_issues = max(1, min(10, len(lines) // 20))

            for i in range(int(base_issues * severity_multiplier)):
                issue_id += 1
                line_num = (i * 20 + 1) % max(1, len(lines))

                # Determine issue type
                if i % 4 == 0 and check_security:
                    severity = CodeReviewSeverity.HIGH
                    category = CodeReviewCategory.SECURITY
                    title = "Potential security vulnerability"
                    self.code_metrics.security_issues_found += 1
                elif i % 3 == 0 and check_performance:
                    severity = CodeReviewSeverity.MEDIUM
                    category = CodeReviewCategory.PERFORMANCE
                    title = "Potential performance issue"
                elif i % 5 == 0:
                    severity = CodeReviewSeverity.HIGH
                    category = CodeReviewCategory.BUG
                    title = "Possible bug"
                    self.code_metrics.bugs_found += 1
                elif i % 2 == 0:
                    severity = CodeReviewSeverity.LOW
                    category = CodeReviewCategory.STYLE
                    title = "Style improvement"
                else:
                    severity = CodeReviewSeverity.MEDIUM
                    category = CodeReviewCategory.BEST_PRACTICE
                    title = "Best practice recommendation"

                issues.append(CodeIssue(
                    id=f"issue-{issue_id}",
                    severity=severity,
                    category=category,
                    file=file_path,
                    line=line_num,
                    title=title,
                    description=f"Review finding in {file_path} at line {line_num}",
                    suggestion="Consider reviewing this code for potential improvements.",
                    code_snippet=lines[line_num - 1].strip() if line_num <= len(lines) else "",
                    confidence=0.7 + (i % 3) * 0.1,
                ))

        return issues

    def _analyze_architecture(
        self,
        files: List[Dict[str, Any]],
        language: str,
    ) -> List[str]:
        """Step 2: Analyze architecture and design patterns."""
        suggestions = []
        if len(files) > 10:
            suggestions.append(
                "Consider modularizing the codebase into smaller, focused modules"
            )
        if len(files) > 0:
            suggestions.append(
                "Review dependency structure to ensure clean architecture boundaries"
            )
        suggestions.append(
            "Evaluate whether current design patterns align with project requirements"
        )
        return suggestions

    def _generate_suggestions(
        self,
        issues: List[CodeIssue],
        arch_suggestions: List[str],
    ) -> List[str]:
        """Step 3: Generate actionable improvement suggestions."""
        suggestions = list(arch_suggestions)

        high_count = sum(
            1 for i in issues if i.severity == CodeReviewSeverity.HIGH
        )
        critical_count = sum(
            1 for i in issues if i.severity == CodeReviewSeverity.CRITICAL
        )

        if critical_count > 0:
            suggestions.append(
                f"PRIORITY: Fix {critical_count} critical issues before merging"
            )
        if high_count > 0:
            suggestions.append(
                f"Address {high_count} high-severity issues"
            )

        return suggestions

    def _analyze_testing(
        self,
        files: List[Dict[str, Any]],
        language: str,
    ) -> List[str]:
        """Step 4: Analyze testing and suggest test cases."""
        suggestions = []
        if files:
            suggestions.append("Ensure unit tests cover edge cases and error paths")
            suggestions.append("Consider adding integration tests for module interactions")
            suggestions.append("Verify test coverage meets project thresholds")
        return suggestions

    def _review_documentation(
        self,
        files: List[Dict[str, Any]],
        language: str,
    ) -> List[str]:
        """Step 5: Review documentation quality."""
        suggestions = []
        if files:
            suggestions.append("Ensure all public functions have docstrings")
            suggestions.append("Verify complex logic has explanatory comments")
            suggestions.append("Check that API documentation is up to date")
        return suggestions

    def _calculate_overall_score(
        self,
        issues: List[CodeIssue],
        file_count: int,
    ) -> float:
        """Calculate an overall code quality score (0-100)."""
        if file_count == 0:
            return 100.0

        severity_weights = {
            CodeReviewSeverity.INFO: 1,
            CodeReviewSeverity.LOW: 2,
            CodeReviewSeverity.MEDIUM: 5,
            CodeReviewSeverity.HIGH: 10,
            CodeReviewSeverity.CRITICAL: 20,
        }

        penalty = sum(
            severity_weights.get(issue.severity, 1)
            for issue in issues
        )

        # Base score minus penalty, capped at reasonable range
        base_score = 100.0
        penalty_per_file = penalty / max(1, file_count)
        score = base_score - (penalty_per_file * 2)

        return max(0.0, min(100.0, score))

    def _generate_summary(
        self,
        issues: List[CodeIssue],
        score: float,
    ) -> str:
        """Generate a human-readable summary of the review."""
        if score >= 90:
            grade = "excellent"
        elif score >= 75:
            grade = "good"
        elif score >= 60:
            grade = "fair"
        else:
            grade = "needs improvement"

        critical = sum(1 for i in issues if i.severity == CodeReviewSeverity.CRITICAL)
        high = sum(1 for i in issues if i.severity == CodeReviewSeverity.HIGH)

        return (
            f"Code quality is {grade} (score: {score:.1f}/100). "
            f"Found {len(issues)} issues: {critical} critical, {high} high. "
            f"{'Address critical issues immediately. ' if critical > 0 else ''}"
            f"Review recommendations for details."
        )

    # ------------------------------------------------------------------
    # Helper methods
    # ------------------------------------------------------------------

    def _group_issues_by_severity(
        self, issues: List[CodeIssue]
    ) -> Dict[str, int]:
        """Group issues by severity level."""
        result: Dict[str, int] = {}
        for issue in issues:
            key = issue.severity.value
            result[key] = result.get(key, 0) + 1
        return result

    def _group_issues_by_category(
        self, issues: List[CodeIssue]
    ) -> Dict[str, int]:
        """Group issues by category."""
        result: Dict[str, int] = {}
        for issue in issues:
            key = issue.category.value
            result[key] = result.get(key, 0) + 1
        return result

    def _update_avg_metrics(
        self,
        issues: List[CodeIssue],
        score: float,
    ) -> None:
        """Update running average metrics."""
        total = self.code_metrics.total_reviews
        if total <= 1:
            self.code_metrics.avg_issues_per_review = len(issues)
            self.code_metrics.avg_code_quality_score = score
        else:
            alpha = 0.1
            self.code_metrics.avg_issues_per_review = (
                alpha * len(issues)
                + (1 - alpha) * self.code_metrics.avg_issues_per_review
            )
            self.code_metrics.avg_code_quality_score = (
                alpha * score
                + (1 - alpha) * self.code_metrics.avg_code_quality_score
            )

    def _load_best_practices(self) -> None:
        """Load best practices guidelines for each supported language."""
        languages = self.config.get("languages", ["python"])
        for lang in languages:
            self._best_practices[lang] = [
                "Follow language-specific style guidelines",
                "Write descriptive variable and function names",
                "Keep functions focused and at a single level of abstraction",
                "Handle errors appropriately and consistently",
                "Write tests for critical code paths",
            ]

    # ------------------------------------------------------------------
    # Agent prompts
    # ------------------------------------------------------------------

    def _build_code_reviewer_prompt(self) -> str:
        """Build the system prompt for the code reviewer role."""
        strictness = self.config.get("strictness", "default")
        return (
            f"You are an expert code reviewer with {strictness} strictness. "
            f"Analyze code for bugs, style issues, and best practices. "
            f"Be thorough but constructive in your feedback."
        )

    def _build_architect_prompt(self) -> str:
        """Build the system prompt for the architect role."""
        return (
            "You are a senior software architect. "
            "Review code for architectural concerns, design patterns, "
            "scalability, maintainability, and system design principles. "
            "Provide strategic recommendations for long-term code health."
        )

    def _build_qa_engineer_prompt(self) -> str:
        """Build the system prompt for the QA engineer role."""
        return (
            "You are a quality assurance engineer. "
            "Analyze code for testability, edge cases, and potential failures. "
            "Suggest test cases and verify that testing strategies are comprehensive."
        )

    def _build_docs_prompt(self) -> str:
        """Build the system prompt for the documentation specialist role."""
        return (
            "You are a documentation specialist. "
            "Review code for documentation quality: docstrings, comments, "
            "API docs, and inline explanations. Suggest improvements for clarity."
        )

    # ------------------------------------------------------------------
    # Public utility methods
    # ------------------------------------------------------------------

    def get_code_metrics(self) -> Dict[str, Any]:
        """Get the current code assistant metrics.

        Returns:
            Dictionary with code review metrics.
        """
        return self.code_metrics.to_dict()

    def get_best_practices(self, language: str) -> List[str]:
        """Get best practices for a specific language.

        Args:
            language: Programming language.

        Returns:
            List of best practice guidelines.
        """
        return self._best_practices.get(language, [])

    def enforce_best_practices(
        self,
        code: str,
        language: str,
    ) -> Dict[str, Any]:
        """Check code against best practices.

        Args:
            code: Source code to check.
            language: Programming language.

        Returns:
            Dictionary with violations and suggestions.
        """
        practices = self._best_practices.get(language, [])
        violations: List[str] = []

        # Simple heuristic checks
        if len(code) > 500:
            violations.append(
                "Consider breaking large code blocks into smaller functions"
            )

        return {
            "language": language,
            "total_practices": len(practices),
            "violations": violations,
            "compliance_score": max(0, 100 - len(violations) * 20),
        }
