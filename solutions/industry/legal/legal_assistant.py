"""
Legal Assistant AI Solution Template.

A comprehensive AI-powered legal assistant solution for law firms,
corporate legal departments, and legal service providers. Includes
multi-agent roles for legal research, contract analysis, compliance
checking, and case summarization.

Typical workflow:
    intake -> legal research -> contract analysis
    -> compliance check -> summary opinion
"""

from typing import Optional, Dict, Any, List
from enum import Enum
from dataclasses import dataclass, field

from solutions.templates.base_solution import (
    BaseSolution,
    SolutionMetadata,
    SolutionMetrics,
)


class LegalArea(str, Enum):
    """Areas of law covered by the legal assistant."""
    CONTRACT_LAW = "contract_law"
    CORPORATE_LAW = "corporate_law"
    INTELLECTUAL_PROPERTY = "intellectual_property"
    EMPLOYMENT_LAW = "employment_law"
    REAL_ESTATE_LAW = "real_estate_law"
    TAX_LAW = "tax_law"
    CRIMINAL_LAW = "criminal_law"
    FAMILY_LAW = "family_law"
    IMMIGRATION_LAW = "immigration_law"
    ENVIRONMENTAL_LAW = "environmental_law"


class CasePriority(str, Enum):
    """Priority levels for legal cases."""
    ROUTINE = "routine"
    STANDARD = "standard"
    HIGH = "high"
    URGENT = "urgent"
    CRITICAL = "critical"


class ComplianceStatus(str, Enum):
    """Status of a compliance review."""
    COMPLIANT = "compliant"
    PARTIALLY_COMPLIANT = "partially_compliant"
    NON_COMPLIANT = "non_compliant"
    REVIEW_REQUIRED = "review_required"


class LegalRole(str, Enum):
    """Agent roles in the legal assistant solution."""
    LEGAL_RESEARCHER = "legal_researcher"
    CONTRACT_ANALYST = "contract_analyst"
    COMPLIANCE_CHECKER = "compliance_checker"
    CASE_SUMMARIZER = "case_summarizer"


@dataclass
class LegalCase:
    """Represents a legal case or matter.

    Attributes:
        case_id: Unique case identifier.
        title: Case title or description.
        legal_area: Area of law involved.
        priority: Case priority level.
        description: Detailed case description.
        client_id: Client identifier.
        documents: List of associated document IDs.
        key_issues: Key legal issues identified.
        applicable_laws: List of applicable laws/regulations.
        status: Current case status.
        created_at: ISO 8601 creation timestamp.
        due_date: ISO 8601 due date, if any.
    """
    case_id: str
    title: str = ""
    legal_area: LegalArea = LegalArea.CONTRACT_LAW
    priority: CasePriority = CasePriority.STANDARD
    description: str = ""
    client_id: str = ""
    documents: List[str] = field(default_factory=list)
    key_issues: List[str] = field(default_factory=list)
    applicable_laws: List[str] = field(default_factory=list)
    status: str = "new"
    created_at: Optional[str] = None
    due_date: Optional[str] = None


@dataclass
class ResearchResult:
    """Represents a legal research result.

    Attributes:
        id: Unique result identifier.
        title: Title of the case, statute, or document.
        type: Type of legal source (case, statute, regulation, commentary).
        jurisdiction: Jurisdiction of the source.
        relevance: Relevance score (0-1).
        citation: Legal citation string.
        summary: Brief summary of the finding.
        key_principles: Key legal principles established.
        date: Date of the source.
    """
    id: str
    title: str
    type: str = "case"
    jurisdiction: str = ""
    relevance: float = 0.0
    citation: str = ""
    summary: str = ""
    key_principles: List[str] = field(default_factory=list)
    date: Optional[str] = None


@dataclass
class ContractAnalysis:
    """Represents results of a contract analysis.

    Attributes:
        contract_id: Contract/document identifier.
        contract_type: Type of contract.
        parties: Parties to the contract.
        key_clauses: Key clauses identified in the contract.
        obligations: List of obligations identified.
        risks: Identified risks or problematic clauses.
        unusual_terms: Unusual or non-standard terms.
        missing_clauses: Recommended missing clauses.
        overall_risk_level: Overall risk assessment (low/medium/high).
        red_flags: Critical red flag issues.
        negotiation_points: Suggested negotiation points.
    """
    contract_id: str
    contract_type: str = ""
    parties: List[str] = field(default_factory=list)
    key_clauses: List[Dict[str, Any]] = field(default_factory=list)
    obligations: List[str] = field(default_factory=list)
    risks: List[str] = field(default_factory=list)
    unusual_terms: List[str] = field(default_factory=list)
    missing_clauses: List[str] = field(default_factory=list)
    overall_risk_level: str = "medium"
    red_flags: List[str] = field(default_factory=list)
    negotiation_points: List[str] = field(default_factory=list)


@dataclass
class LegalOpinion:
    """Represents a legal opinion or summary.

    Attributes:
        id: Unique opinion identifier.
        case_id: Associated case ID.
        summary: Executive summary of the analysis.
        key_findings: Key legal findings.
        recommendations: Recommended actions.
        compliance_status: Overall compliance assessment.
        confidence_level: Confidence in the analysis (0-1).
        disclaimers: Legal disclaimers.
        further_research: Areas requiring further research.
        created_at: ISO 8601 creation timestamp.
    """
    id: str
    case_id: str
    summary: str = ""
    key_findings: List[str] = field(default_factory=list)
    recommendations: List[str] = field(default_factory=list)
    compliance_status: ComplianceStatus = ComplianceStatus.REVIEW_REQUIRED
    confidence_level: float = 0.0
    disclaimers: List[str] = field(default_factory=list)
    further_research: List[str] = field(default_factory=list)
    created_at: Optional[str] = None


@dataclass
class LegalMetrics(SolutionMetrics):
    """Extended metrics for the legal assistant solution.

    Attributes:
        research_depth: Average depth of legal research (0-1).
        contract_accuracy: Accuracy rate of contract analysis.
        compliance_rate: Rate of documents found compliant.
        cases_processed: Total cases processed.
        contracts_analyzed: Total contracts analyzed.
        avg_research_time_min: Average research time per case.
        avg_contract_analysis_time_min: Average contract analysis time.
        red_flags_detected: Total red flags identified.
    """
    research_depth: float = 0.0
    contract_accuracy: float = 0.0
    compliance_rate: float = 0.0
    cases_processed: int = 0
    contracts_analyzed: int = 0
    avg_research_time_min: float = 0.0
    avg_contract_analysis_time_min: float = 0.0
    red_flags_detected: int = 0

    def to_dict(self) -> Dict[str, Any]:
        """Convert legal metrics to a dictionary."""
        base = super().to_dict()
        base.update({
            "research_depth": self.research_depth,
            "contract_accuracy": self.contract_accuracy,
            "compliance_rate": self.compliance_rate,
            "cases_processed": self.cases_processed,
            "contracts_analyzed": self.contracts_analyzed,
            "avg_research_time_min": self.avg_research_time_min,
            "avg_contract_analysis_time_min": self.avg_contract_analysis_time_min,
            "red_flags_detected": self.red_flags_detected,
        })
        return base


class LegalAssistantSolution(BaseSolution):
    """Legal Assistant AI solution template.

    Provides a complete, pre-configured legal AI assistant solution
    with multi-agent collaboration for legal research, contract
    analysis, compliance checking, and case summarization.

    Pre-configured agent roles:
        - Legal Researcher: Conducts comprehensive legal research
        - Contract Analyst: Analyzes contracts for risks, obligations, and issues
        - Compliance Checker: Verifies compliance with laws and regulations
        - Case Summarizer: Produces concise case summaries and legal opinions

    Workflow:
        1. Intake - Receive and categorize new legal matters
        2. Legal Research - Research applicable laws, cases, and precedents
        3. Contract Analysis - Analyze contracts for risks and obligations
        4. Compliance Check - Verify compliance with relevant regulations
        5. Summary Opinion - Produce comprehensive legal opinion/summary

    Configuration options:
        - jurisdiction: Primary legal jurisdiction
        - legal_area: Primary area of law specialization
        - compliance_standard: Compliance standard to apply
        - research_depth: Depth of legal research (basic, standard, deep)
        - enable_red_flag_detection: Whether to detect red flag clauses
    """

    def _build_metadata(self) -> SolutionMetadata:
        """Build metadata for the legal assistant solution."""
        return SolutionMetadata(
            name="legal-assistant",
            version="1.0.0",
            industry="legal",
            description=(
                "AI-powered legal assistant solution with legal research, "
                "contract analysis, compliance checking, and case summarization."
            ),
            author="LiuHao AI OS",
            tags=["legal", "law", "contracts", "compliance", "research", "legal-tech"],
            min_liuhao_version="1.0.0",
        )

    def _build_config_schema(self) -> None:
        """Define configuration fields for the legal assistant solution."""
        self.config_schema.add_field(
            name="jurisdiction",
            field_type="string",
            required=True,
            description="Primary legal jurisdiction",
            valid_values=["us-federal", "us-state", "eu", "uk", "cn", "jp", "international"],
        )
        self.config_schema.add_field(
            name="legal_area",
            field_type="string",
            required=False,
            default="contract_law",
            description="Primary area of law specialization",
            valid_values=[e.value for e in LegalArea],
        )
        self.config_schema.add_field(
            name="compliance_standard",
            field_type="string",
            required=False,
            default="standard",
            description="Compliance review standard",
            valid_values=["basic", "standard", "comprehensive", "enhanced"],
        )
        self.config_schema.add_field(
            name="research_depth",
            field_type="string",
            required=False,
            default="standard",
            description="Depth of legal research to perform",
            valid_values=["basic", "standard", "deep", "exhaustive"],
        )
        self.config_schema.add_field(
            name="enable_red_flag_detection",
            field_type="bool",
            required=False,
            default=True,
            description="Whether to detect red flag clauses in contracts",
        )
        self.config_schema.add_field(
            name="include_disclaimer",
            field_type="bool",
            required=False,
            default=True,
            description="Whether to include legal disclaimer in outputs",
        )
        self.config_schema.add_field(
            name="confidence_threshold",
            field_type="float",
            required=False,
            default=0.7,
            description="Minimum confidence for non-review results (0-1)",
        )

    def _on_initialize(self) -> None:
        """Initialize legal assistant solution state."""
        self._agents: Dict[str, Dict[str, Any]] = {}
        self._cases: Dict[str, LegalCase] = {}
        self._research_results: Dict[str, List[ResearchResult]] = {}
        self._contract_analyses: Dict[str, ContractAnalysis] = {}
        self._opinions: Dict[str, LegalOpinion] = {}
        self._case_count = 0
        self._opinion_count = 0

        # Initialize legal-specific metrics
        self.legal_metrics = LegalMetrics()

    def _on_deploy(self) -> None:
        """Deploy legal assistant agents and workflows."""
        agent_configs = [
            (LegalRole.LEGAL_RESEARCHER, {
                "name": "Legal Researcher",
                "description": "Conducts comprehensive legal research",
                "system_prompt": self._build_legal_researcher_prompt(),
                "tools": ["case_law_search", "statute_search", "legal_database"],
            }),
            (LegalRole.CONTRACT_ANALYST, {
                "name": "Contract Analyst",
                "description": "Analyzes contracts for risks and obligations",
                "system_prompt": self._build_contract_analyst_prompt(),
                "tools": ["contract_parser", "clause_extractor", "risk_detector"],
            }),
            (LegalRole.COMPLIANCE_CHECKER, {
                "name": "Compliance Checker",
                "description": "Verifies compliance with laws and regulations",
                "system_prompt": self._build_compliance_checker_prompt(),
                "tools": ["regulation_library", "compliance_checker", "audit_trail"],
            }),
            (LegalRole.CASE_SUMMARIZER, {
                "name": "Case Summarizer",
                "description": "Produces concise case summaries and legal opinions",
                "system_prompt": self._build_case_summarizer_prompt(),
                "tools": ["summary_generator", "brief_writer", "opinion_drafter"],
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
        """Process a legal matter through the full workflow.

        Args:
            input_data: Dictionary containing case data with at least
                ``case_id`` and ``description`` or contract text.

        Returns:
            Dictionary with legal analysis results including research,
            contract analysis, compliance status, and legal opinion.
        """
        # Step 1: Case intake
        case = self._intake_case(input_data)

        # Step 2: Legal research
        research_results = self._conduct_research(case)

        # Step 3: Contract analysis (if contract provided)
        contract_analysis = None
        if input_data.get("contract_text") or input_data.get("contract_id"):
            contract_analysis = self._analyze_contract(case, input_data)
            self.legal_metrics.contracts_analyzed += 1
            self.legal_metrics.red_flags_detected += len(contract_analysis.red_flags)

        # Step 4: Compliance check
        compliance_result = self._check_compliance(case, research_results, contract_analysis)

        # Step 5: Generate legal opinion / summary
        opinion = self._generate_opinion(
            case, research_results, contract_analysis, compliance_result
        )

        # Update metrics
        self.legal_metrics.cases_processed += 1
        self._update_legal_metrics()

        return {
            "case_id": case.case_id,
            "case": {
                "title": case.title,
                "legal_area": case.legal_area.value,
                "priority": case.priority.value,
                "key_issues": case.key_issues,
            },
            "research": {
                "results_count": len(research_results),
                "top_results": [
                    {
                        "title": r.title,
                        "type": r.type,
                        "relevance": r.relevance,
                        "citation": r.citation,
                    }
                    for r in research_results[:5]
                ],
                "applicable_laws": case.applicable_laws,
            },
            "contract_analysis": {
                "overall_risk": contract_analysis.overall_risk_level if contract_analysis else None,
                "key_clauses_count": len(contract_analysis.key_clauses) if contract_analysis else 0,
                "risks_identified": contract_analysis.risks if contract_analysis else [],
                "red_flags": contract_analysis.red_flags if contract_analysis else [],
                "negotiation_points": contract_analysis.negotiation_points if contract_analysis else [],
            } if contract_analysis else None,
            "compliance": {
                "status": compliance_result["status"].value,
                "issues": compliance_result["issues"],
                "recommendations": compliance_result["recommendations"],
            },
            "opinion": {
                "id": opinion.id,
                "summary": opinion.summary,
                "key_findings": opinion.key_findings,
                "recommendations": opinion.recommendations,
                "confidence_level": opinion.confidence_level,
                "disclaimers": opinion.disclaimers,
            },
        }

    def _on_teardown(self) -> None:
        """Clean up legal assistant solution resources."""
        self._agents.clear()
        self._cases.clear()
        self._research_results.clear()
        self._contract_analyses.clear()
        self._opinions.clear()
        self._case_count = 0
        self._opinion_count = 0

    # ------------------------------------------------------------------
    # Workflow steps
    # ------------------------------------------------------------------

    def _intake_case(self, data: Dict[str, Any]) -> LegalCase:
        """Step 1: Create and categorize a new legal case."""
        self._case_count += 1
        case_id = data.get("case_id", f"case-{self._case_count}")

        # Determine legal area
        legal_area = LegalArea(
            data.get("legal_area", self.config.get("legal_area", LegalArea.CONTRACT_LAW.value))
        )

        # Extract key issues from description
        description = data.get("description", "")
        key_issues = self._extract_key_issues(description, legal_area)

        case = LegalCase(
            case_id=case_id,
            title=data.get("title", f"Case {self._case_count}"),
            legal_area=legal_area,
            priority=CasePriority(data.get("priority", CasePriority.STANDARD.value)),
            description=description,
            client_id=data.get("client_id", ""),
            documents=data.get("documents", []),
            key_issues=key_issues,
            status="intake_complete",
        )
        self._cases[case_id] = case
        return case

    def _extract_key_issues(self, description: str, legal_area: LegalArea) -> List[str]:
        """Extract key legal issues from case description."""
        issues = []
        desc_lower = description.lower()

        issue_patterns = {
            LegalArea.CONTRACT_LAW: [
                ("breach", "Breach of contract"),
                ("termination", "Contract termination"),
                ("liability", "Liability limitations"),
                ("indemnif", "Indemnification obligations"),
                ("warrant", "Warranty provisions"),
                ("payment", "Payment terms dispute"),
            ],
            LegalArea.EMPLOYMENT_LAW: [
                ("terminat", "Employment termination"),
                ("discriminat", "Discrimination claim"),
                ("harassment", "Workplace harassment"),
                ("wage", "Wage and hour issues"),
                ("benefit", "Benefits disputes"),
                ("wrongful", "Wrongful termination"),
            ],
            LegalArea.INTELLECTUAL_PROPERTY: [
                ("infring", "IP infringement"),
                ("patent", "Patent issues"),
                ("trademark", "Trademark matters"),
                ("copyright", "Copyright concerns"),
                ("trade secret", "Trade secret protection"),
            ],
        }

        patterns = issue_patterns.get(legal_area, [])
        for keyword, issue in patterns:
            if keyword in desc_lower:
                issues.append(issue)

        return issues[:5] if issues else ["General legal consultation"]

    def _conduct_research(self, case: LegalCase) -> List[ResearchResult]:
        """Step 2: Conduct legal research on the case issues."""
        research_depth = self.config.get("research_depth", "standard")
        depth_counts = {
            "basic": 3,
            "standard": 6,
            "deep": 10,
            "exhaustive": 15,
        }
        num_results = depth_counts.get(research_depth, 6)

        results = []
        for i in range(num_results):
            relevance = round(0.95 - (i * 0.08), 2)
            results.append(ResearchResult(
                id=f"research-{case.case_id}-{i+1}",
                title=f"{case.legal_area.value.replace('_', ' ').title()} - Precedent {i+1}",
                type="case" if i % 3 != 0 else "statute",
                jurisdiction=self.config.get("jurisdiction", "us-federal"),
                relevance=max(0.3, relevance),
                citation=f"Reference {i+1} - {case.legal_area.value}",
                summary=f"Legal precedent regarding {case.key_issues[0] if case.key_issues else case.legal_area.value}",
                key_principles=[
                    f"Principle {i+1}a: Established legal standard",
                    f"Principle {i+1}b: Key interpretation rule",
                ],
            ))

        # Update applicable laws
        case.applicable_laws = [
            f"{case.legal_area.value.replace('_', ' ').title()} Act",
            "Relevant statutory provisions",
            "Applicable case law precedents",
        ]

        self._research_results[case.case_id] = results
        return results

    def _analyze_contract(
        self, case: LegalCase, input_data: Dict[str, Any]
    ) -> ContractAnalysis:
        """Step 3: Analyze contract for risks, obligations, and issues."""
        contract_id = input_data.get("contract_id", f"contract-{case.case_id}")

        # Extract key clauses
        key_clauses = [
            {"name": "Payment Terms", "summary": "Standard net-30 payment terms", "risk": "low"},
            {"name": "Liability Limitation", "summary": "Cap on liability at contract value", "risk": "medium"},
            {"name": "Indemnification", "summary": "Mutual indemnification provisions", "risk": "medium"},
            {"name": "Termination", "summary": "Termination for cause and convenience", "risk": "low"},
            {"name": "Confidentiality", "summary": "Mutual non-disclosure obligations", "risk": "low"},
            {"name": "Dispute Resolution", "summary": "Arbitration clause specified", "risk": "medium"},
        ]

        # Identify risks
        risks = [
            "Liability cap may be insufficient for potential damages",
            "Indemnification scope could be overly broad",
            "Termination notice period is shorter than industry standard",
        ]

        # Red flag detection
        red_flags = []
        if self.config.get("enable_red_flag_detection", True):
            # Simulated red flag detection
            if case.priority in (CasePriority.HIGH, CasePriority.URGENT, CasePriority.CRITICAL):
                red_flags.append("Unusual indemnification language detected")
            if len(case.key_issues) > 2:
                red_flags.append("Multiple high-risk clauses present")

        # Unusual terms
        unusual_terms = [
            "Non-standard governing law clause",
            "Automatic renewal without notification",
        ] if len(red_flags) > 0 else []

        # Missing clauses
        missing_clauses = [
            "Force majeure provision may be inadequate",
            "Data protection clause missing or incomplete",
        ]

        # Determine overall risk
        risk_level = "low"
        if red_flags:
            risk_level = "high"
        elif len(risks) > 2:
            risk_level = "medium"

        # Negotiation points
        negotiation_points = [
            "Consider increasing liability cap",
            "Clarify indemnification scope",
            "Add data protection provisions",
        ]

        analysis = ContractAnalysis(
            contract_id=contract_id,
            contract_type=case.legal_area.value,
            parties=["Party A", "Party B"],
            key_clauses=key_clauses,
            obligations=[
                "Payment obligations within 30 days",
                "Confidentiality obligations during and after term",
                "Performance of contractual duties",
            ],
            risks=risks,
            unusual_terms=unusual_terms,
            missing_clauses=missing_clauses,
            overall_risk_level=risk_level,
            red_flags=red_flags,
            negotiation_points=negotiation_points,
        )
        self._contract_analyses[contract_id] = analysis
        return analysis

    def _check_compliance(
        self,
        case: LegalCase,
        research: List[ResearchResult],
        contract_analysis: Optional[ContractAnalysis],
    ) -> Dict[str, Any]:
        """Step 4: Check compliance with applicable laws and regulations."""
        compliance_standard = self.config.get("compliance_standard", "standard")
        issues = []
        recommendations = []

        # Determine compliance status
        if contract_analysis and contract_analysis.red_flags:
            status = ComplianceStatus.NON_COMPLIANT
            issues.extend(contract_analysis.red_flags)
            recommendations.append("Address red flag clauses before proceeding")
        elif contract_analysis and contract_analysis.overall_risk_level == "high":
            status = ComplianceStatus.PARTIALLY_COMPLIANT
            issues.extend(contract_analysis.risks[:3])
            recommendations.append("Review and mitigate high-risk provisions")
        else:
            status = ComplianceStatus.COMPLIANT

        # Jurisdiction-specific checks
        jurisdiction = self.config.get("jurisdiction", "us-federal")
        if compliance_standard in ("comprehensive", "enhanced"):
            recommendations.append(
                f"Verify compliance with {jurisdiction} specific requirements"
            )
            if jurisdiction == "eu":
                issues.append("GDPR compliance verification recommended")
                recommendations.append("Conduct GDPR compliance review")
            elif jurisdiction == "us-federal":
                recommendations.append("Verify federal and state-level compliance")

        if status == ComplianceStatus.COMPLIANT and not recommendations:
            recommendations.append("No compliance issues identified. Standard monitoring recommended.")

        return {
            "status": status,
            "issues": issues,
            "recommendations": recommendations,
            "standard_applied": compliance_standard,
            "jurisdiction": jurisdiction,
        }

    def _generate_opinion(
        self,
        case: LegalCase,
        research: List[ResearchResult],
        contract_analysis: Optional[ContractAnalysis],
        compliance: Dict[str, Any],
    ) -> LegalOpinion:
        """Step 5: Generate comprehensive legal opinion/summary."""
        self._opinion_count += 1
        opinion_id = f"opinion-{self._opinion_count}"

        # Calculate confidence
        base_confidence = 0.6
        research_boost = min(0.2, len(research) * 0.02)
        confidence = min(0.95, base_confidence + research_boost)

        if compliance["status"] == ComplianceStatus.NON_COMPLIANT:
            confidence -= 0.1

        # Summary
        summary = (
            f"Analysis of {case.title} ({case.legal_area.value.replace('_', ' ')}): "
            f"Based on review of {len(research)} legal sources and "
            f"{'contract analysis' if contract_analysis else 'legal research'}, "
            f"the overall compliance status is {compliance['status'].value}. "
            f"Key issues include: {', '.join(case.key_issues[:3])}."
        )

        # Key findings
        key_findings = [
            f"Identified {len(case.key_issues)} key legal issue(s) in the matter",
            f"Found {len(research)} relevant legal sources for analysis",
            f"Compliance status: {compliance['status'].value}",
        ]
        if contract_analysis:
            key_findings.append(
                f"Contract risk level: {contract_analysis.overall_risk_level}"
            )
            key_findings.append(
                f"Identified {len(contract_analysis.red_flags)} red flag(s) in contract"
            )

        # Recommendations
        recommendations = list(compliance["recommendations"])
        if contract_analysis:
            recommendations.extend(contract_analysis.negotiation_points[:2])

        # Disclaimers
        disclaimers = []
        if self.config.get("include_disclaimer", True):
            disclaimers.extend([
                "This analysis is generated by AI and is for informational purposes only.",
                "This does not constitute legal advice. Consult a qualified attorney.",
                "AI-generated analysis may contain errors or omissions.",
                f"Analysis performed with {confidence*100:.0f}% confidence level.",
            ])

        # Further research areas
        further_research = []
        if confidence < self.config.get("confidence_threshold", 0.7):
            further_research.append("Additional legal research recommended")
        if contract_analysis and contract_analysis.red_flags:
            further_research.append("Deep dive into red flag provisions")

        opinion = LegalOpinion(
            id=opinion_id,
            case_id=case.case_id,
            summary=summary,
            key_findings=key_findings,
            recommendations=recommendations,
            compliance_status=compliance["status"],
            confidence_level=round(confidence, 2),
            disclaimers=disclaimers,
            further_research=further_research,
        )
        self._opinions[opinion_id] = opinion
        return opinion

    # ------------------------------------------------------------------
    # Agent prompts
    # ------------------------------------------------------------------

    def _build_legal_researcher_prompt(self) -> str:
        """Build the system prompt for the legal researcher role."""
        jurisdiction = self.config.get("jurisdiction", "us-federal")
        return (
            f"You are a legal research specialist with expertise in "
            f"{jurisdiction} jurisdiction. Conduct thorough legal research "
            f"to find relevant case law, statutes, regulations, and legal "
            f"commentary. Evaluate the precedential value of sources and "
            f"provide accurate citations. Distinguish between binding and "
            f"persuasive authority. Always note the jurisdiction and date "
            f"of sources."
        )

    def _build_contract_analyst_prompt(self) -> str:
        """Build the system prompt for the contract analyst role."""
        return (
            "You are an experienced contract analyst and attorney. "
            "Thoroughly analyze contracts to identify key clauses, "
            "obligations, risks, and potential issues. Flag unusual "
            "or one-sided terms. Identify missing provisions that "
            "should be included. Provide clear risk assessments and "
            "practical negotiation recommendations. Pay special "
            "attention to liability, indemnification, and termination clauses."
        )

    def _build_compliance_checker_prompt(self) -> str:
        """Build the system prompt for the compliance checker role."""
        jurisdiction = self.config.get("jurisdiction", "us-federal")
        standard = self.config.get("compliance_standard", "standard")
        return (
            f"You are a legal compliance specialist with expertise in "
            f"{jurisdiction} regulations. Review legal documents and "
            f"matrices for compliance with applicable laws, regulations, "
            f"and industry standards at the {standard} level. Identify "
            f"specific compliance gaps, provide remediation recommendations, "
            f"and classify compliance status clearly. Document all findings "
            f"with specific regulatory references."
        )

    def _build_case_summarizer_prompt(self) -> str:
        """Build the system prompt for the case summarizer role."""
        return (
            "You are a legal writing specialist who excels at producing "
            "clear, concise legal summaries and opinions. Synthesize "
            "complex legal research and analysis into coherent, actionable "
            "insights. Structure summaries for different audiences (clients, "
            "attorneys, executives). Always include appropriate disclaimers "
            "and clearly distinguish between conclusions and recommendations."
        )

    # ------------------------------------------------------------------
    # Metrics
    # ------------------------------------------------------------------

    def _update_legal_metrics(self) -> None:
        """Update legal-specific metrics."""
        if self._opinions:
            total = len(self._opinions)
            compliant = sum(
                1 for o in self._opinions.values()
                if o.compliance_status == ComplianceStatus.COMPLIANT
            )
            self.legal_metrics.compliance_rate = compliant / total if total > 0 else 1.0

            avg_confidence = sum(
                o.confidence_level for o in self._opinions.values()
            ) / total
            self.legal_metrics.contract_accuracy = avg_confidence

        # Research depth approximation
        depth_scores = {"basic": 0.4, "standard": 0.7, "deep": 0.85, "exhaustive": 0.95}
        self.legal_metrics.research_depth = depth_scores.get(
            self.config.get("research_depth", "standard"), 0.7
        )

    def get_legal_metrics(self) -> Dict[str, Any]:
        """Get the current legal-specific metrics.

        Returns:
            Dictionary with legal metrics including research depth,
            contract accuracy, compliance rate, and red flag count.
        """
        return self.legal_metrics.to_dict()
