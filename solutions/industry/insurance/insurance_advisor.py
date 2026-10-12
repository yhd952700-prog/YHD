"""
Insurance Advisor AI Solution Template.

A comprehensive AI-powered insurance advisory solution for
insurance carriers, brokers, and agencies. Includes multi-agent
roles for underwriting, claims adjustment, risk assessment, and
customer service with built-in compliance and fraud detection.

Typical workflow:
    client intake -> risk assessment -> quote generation
    -> policy issuance -> claims processing
"""

from typing import Optional, Dict, Any, List
from enum import Enum
from dataclasses import dataclass, field

from solutions.templates.base_solution import (
    BaseSolution,
    SolutionMetadata,
    SolutionMetrics,
)


class PolicyType(str, Enum):
    """Types of insurance policies."""
    AUTO = "auto"
    HOME = "home"
    LIFE = "life"
    HEALTH = "health"
    BUSINESS = "business"
    TRAVEL = "travel"
    PROPERTY = "property"
    LIABILITY = "liability"


class RiskLevel(str, Enum):
    """Risk assessment levels for underwriting."""
    VERY_LOW = "very_low"
    LOW = "low"
    MODERATE = "moderate"
    HIGH = "high"
    VERY_HIGH = "very_high"


class ClaimStatus(str, Enum):
    """Status of a claim through processing."""
    SUBMITTED = "submitted"
    UNDER_REVIEW = "under_review"
    INVESTIGATING = "investigating"
    APPROVED = "approved"
    DENIED = "denied"
    PAID = "paid"
    APPEALED = "appealed"
    CLOSED = "closed"


class InsuranceRole(str, Enum):
    """Agent roles in the insurance advisor solution."""
    UNDERWRITER = "underwriter"
    CLAIMS_ADJUSTER = "claims_adjuster"
    RISK_ASSESSOR = "risk_assessor"
    CUSTOMER_SERVICE_REP = "customer_service_rep"


@dataclass
class ClientProfile:
    """Represents an insurance client profile.

    Attributes:
        client_id: Unique client identifier.
        name: Client name.
        age: Client age in years.
        location: Client geographic location.
        occupation: Client occupation.
        annual_income: Annual income in base currency.
        existing_policies: List of existing policy IDs.
        risk_score: Calculated risk score (0-100).
        claims_history: Number of past claims.
        preferred_language: Client's preferred language.
    """
    client_id: str
    name: str = ""
    age: int = 40
    location: str = ""
    occupation: str = ""
    annual_income: float = 0.0
    existing_policies: List[str] = field(default_factory=list)
    risk_score: float = 50.0
    claims_history: int = 0
    preferred_language: str = "en"


@dataclass
class RiskAssessment:
    """Represents a risk assessment result.

    Attributes:
        id: Unique assessment identifier.
        client_id: Associated client ID.
        policy_type: Type of policy being assessed.
        risk_level: Assessed risk level.
        risk_score: Numerical risk score (0-100).
        risk_factors: Identified risk factors.
        mitigation_suggestions: Risk mitigation recommendations.
        underwriting_guidance: Guidance for underwriting.
        assessed_at: ISO 8601 timestamp of assessment.
    """
    id: str
    client_id: str
    policy_type: PolicyType = PolicyType.AUTO
    risk_level: RiskLevel = RiskLevel.MODERATE
    risk_score: float = 50.0
    risk_factors: List[str] = field(default_factory=list)
    mitigation_suggestions: List[str] = field(default_factory=list)
    underwriting_guidance: str = ""
    assessed_at: Optional[str] = None


@dataclass
class Quote:
    """Represents an insurance quote.

    Attributes:
        id: Unique quote identifier.
        client_id: Associated client ID.
        policy_type: Type of policy.
        coverage_amount: Coverage amount in base currency.
        premium_monthly: Monthly premium amount.
        premium_annual: Annual premium amount.
        deductible: Deductible amount.
        coverage_details: Detailed coverage breakdown.
        discounts: Applied discounts.
        exclusions: Policy exclusions.
        valid_until: Quote expiration date.
        status: Quote status.
    """
    id: str
    client_id: str
    policy_type: PolicyType = PolicyType.AUTO
    coverage_amount: float = 0.0
    premium_monthly: float = 0.0
    premium_annual: float = 0.0
    deductible: float = 0.0
    coverage_details: Dict[str, Any] = field(default_factory=dict)
    discounts: List[str] = field(default_factory=list)
    exclusions: List[str] = field(default_factory=list)
    valid_until: str = ""
    status: str = "draft"


@dataclass
class Claim:
    """Represents an insurance claim.

    Attributes:
        id: Unique claim identifier.
        policy_id: Associated policy ID.
        client_id: Associated client ID.
        claim_type: Type of claim.
        incident_description: Description of the incident.
        claimed_amount: Amount being claimed.
        approved_amount: Amount approved for payment.
        status: Current claim status.
        adjuster_notes: Notes from claims adjuster.
        investigation_findings: Investigation results.
        filed_at: ISO 8601 filing timestamp.
        resolved_at: ISO 8601 resolution timestamp.
    """
    id: str
    policy_id: str
    client_id: str
    claim_type: str = ""
    incident_description: str = ""
    claimed_amount: float = 0.0
    approved_amount: float = 0.0
    status: ClaimStatus = ClaimStatus.SUBMITTED
    adjuster_notes: str = ""
    investigation_findings: List[str] = field(default_factory=list)
    filed_at: Optional[str] = None
    resolved_at: Optional[str] = None


@dataclass
class InsuranceMetrics(SolutionMetrics):
    """Extended metrics for the insurance advisor solution.

    Attributes:
        quote_accuracy: Accuracy rate of generated quotes.
        avg_claims_processing_days: Average claims processing time in days.
        customer_satisfaction: Average customer satisfaction score (0-5).
        policies_issued: Total policies issued.
        claims_processed: Total claims processed.
        avg_risk_score: Average client risk score.
        underwriting_approval_rate: Rate of applications approved.
        fraud_detection_rate: Rate of fraud detected in claims.
        avg_quote_time_min: Average quote generation time in minutes.
    """
    quote_accuracy: float = 0.0
    avg_claims_processing_days: float = 0.0
    customer_satisfaction: float = 0.0
    policies_issued: int = 0
    claims_processed: int = 0
    avg_risk_score: float = 50.0
    underwriting_approval_rate: float = 0.0
    fraud_detection_rate: float = 0.0
    avg_quote_time_min: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        """Convert insurance metrics to a dictionary."""
        base = super().to_dict()
        base.update({
            "quote_accuracy": self.quote_accuracy,
            "avg_claims_processing_days": self.avg_claims_processing_days,
            "customer_satisfaction": self.customer_satisfaction,
            "policies_issued": self.policies_issued,
            "claims_processed": self.claims_processed,
            "avg_risk_score": self.avg_risk_score,
            "underwriting_approval_rate": self.underwriting_approval_rate,
            "fraud_detection_rate": self.fraud_detection_rate,
            "avg_quote_time_min": self.avg_quote_time_min,
        })
        return base


class InsuranceAdvisorSolution(BaseSolution):
    """Insurance Advisor AI solution template.

    Provides a complete, pre-configured insurance advisory solution
    with multi-agent collaboration for client intake, risk assessment,
    quote generation, policy issuance, and claims processing.

    Pre-configured agent roles:
        - Underwriter: Evaluates risk and determines policy terms
        - Claims Adjuster: Investigates and adjudicates insurance claims
        - Risk Assessor: Analyzes client risk factors and exposures
        - Customer Service Rep: Handles client inquiries and support

    Workflow:
        1. Client Intake - Gather client information and needs
        2. Risk Assessment - Analyze risk factors and exposures
        3. Quote Generation - Generate policy quotes with pricing
        4. Policy Issuance - Issue approved policies
        5. Claims Processing - Process and adjudicate claims

    Configuration options:
        - policy_types: List of policy types to support
        - underwriting_guidelines: Underwriting rule set
        - risk_tolerance: Maximum acceptable risk level
        - auto_approve_threshold: Risk threshold for auto-approval
        - claims_fraud_check_enabled: Enable fraud detection on claims
    """

    def _build_metadata(self) -> SolutionMetadata:
        """Build metadata for the insurance advisor solution."""
        return SolutionMetadata(
            name="insurance-advisor",
            version="1.0.0",
            industry="insurance",
            description=(
                "AI-powered insurance advisory solution with automated "
                "risk assessment, quote generation, policy underwriting, "
                "and claims processing with built-in fraud detection."
            ),
            author="LiuHao AI OS",
            tags=["insurance", "underwriting", "claims", "risk-assessment", "fraud-detection"],
            min_liuhao_version="1.0.0",
        )

    def _build_config_schema(self) -> None:
        """Define configuration fields for the insurance advisor solution."""
        self.config_schema.add_field(
            name="policy_types",
            field_type="list",
            required=False,
            default=["auto", "home", "life"],
            description="List of policy types supported by this deployment",
        )
        self.config_schema.add_field(
            name="underwriting_guidelines",
            field_type="string",
            required=False,
            default="standard",
            description="Underwriting guideline set to use",
            valid_values=["conservative", "standard", "aggressive", "custom"],
        )
        self.config_schema.add_field(
            name="risk_tolerance",
            field_type="string",
            required=False,
            default="moderate",
            description="Maximum acceptable risk level for approval",
            valid_values=["very_low", "low", "moderate", "high", "very_high"],
        )
        self.config_schema.add_field(
            name="auto_approve_threshold",
            field_type="int",
            required=False,
            default=40,
            description="Risk score threshold below which auto-approval applies (0-100)",
        )
        self.config_schema.add_field(
            name="claims_fraud_check_enabled",
            field_type="bool",
            required=False,
            default=True,
            description="Whether to enable fraud detection on claims",
        )
        self.config_schema.add_field(
            name="quote_validity_days",
            field_type="int",
            required=False,
            default=30,
            description="Number of days a generated quote remains valid",
        )
        self.config_schema.add_field(
            name="jurisdiction",
            field_type="string",
            required=False,
            default="us",
            description="Regulatory jurisdiction for insurance compliance",
            valid_values=["us", "eu", "uk", "cn", "jp", "au", "global"],
        )

    def _on_initialize(self) -> None:
        """Initialize insurance advisor solution state."""
        self._agents: Dict[str, Dict[str, Any]] = {}
        self._clients: Dict[str, ClientProfile] = {}
        self._assessments: Dict[str, RiskAssessment] = {}
        self._quotes: Dict[str, Quote] = {}
        self._claims: Dict[str, Claim] = {}
        self._quote_count = 0
        self._client_count = 0
        self._assessment_count = 0
        self._claim_count = 0

        # Initialize insurance-specific metrics
        self.insurance_metrics = InsuranceMetrics()

        # Risk factor patterns
        self._risk_patterns = {
            "high_risk_occupations": ["construction", "mining", "firefighter", "police"],
            "high_risk_locations": ["flood_zone", "earthquake-zone", "high-crime"],
            "claim_frequency_threshold": 2,
        }

    def _on_deploy(self) -> None:
        """Deploy insurance advisor agents and workflows."""
        agent_configs = [
            (InsuranceRole.UNDERWRITER, {
                "name": "Underwriter",
                "description": "Evaluates risk and determines policy terms and pricing",
                "system_prompt": self._build_underwriter_prompt(),
                "tools": ["risk_evaluator", "pricing_engine", "policy_generator"],
            }),
            (InsuranceRole.CLAIMS_ADJUSTER, {
                "name": "Claims Adjuster",
                "description": "Investigates and adjudicates insurance claims",
                "system_prompt": self._build_claims_adjuster_prompt(),
                "tools": ["claim_investigator", "damage_assessor", "fraud_detector"],
            }),
            (InsuranceRole.RISK_ASSESSOR, {
                "name": "Risk Assessor",
                "description": "Analyzes client risk factors and exposures",
                "system_prompt": self._build_risk_assessor_prompt(),
                "tools": ["risk_modeling", "exposure_analysis", "mitigation_planner"],
            }),
            (InsuranceRole.CUSTOMER_SERVICE_REP, {
                "name": "Customer Service Rep",
                "description": "Handles client inquiries, policy changes, and support",
                "system_prompt": self._build_customer_service_prompt(),
                "tools": ["policy_lookup", "claim_status", "billing_support"],
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
        """Process an insurance request through the full workflow.

        Args:
            input_data: Dictionary containing client data with at least
                ``client_id`` and request type (quote or claim).

        Returns:
            Dictionary with insurance results including risk assessment,
            quote details, or claim processing status.
        """
        request_type = input_data.get("request_type", "quote")

        if request_type == "claim":
            return self._process_claim(input_data)
        else:
            return self._process_quote_request(input_data)

    def _process_quote_request(self, input_data: Dict[str, Any]) -> Dict[str, Any]:
        """Process a new quote request through the workflow."""
        # Step 1: Client intake
        client = self._intake_client(input_data)

        # Step 2: Risk assessment
        assessment = self._assess_risk(client, input_data)

        # Step 3: Quote generation
        quote = self._generate_quote(client, assessment, input_data)

        # Step 4: Underwriting decision
        underwriting_decision = self._underwriting_decision(assessment)

        # Update metrics
        self.insurance_metrics.policies_issued += 1 if underwriting_decision["approved"] else 0
        self._update_insurance_metrics()

        return {
            "client_id": client.client_id,
            "client": {
                "name": client.name,
                "risk_score": client.risk_score,
                "claims_history": client.claims_history,
            },
            "risk_assessment": {
                "id": assessment.id,
                "risk_level": assessment.risk_level.value,
                "risk_score": assessment.risk_score,
                "risk_factors": assessment.risk_factors,
                "mitigation_suggestions": assessment.mitigation_suggestions,
            },
            "quote": {
                "id": quote.id,
                "policy_type": quote.policy_type.value,
                "coverage_amount": quote.coverage_amount,
                "premium_monthly": quote.premium_monthly,
                "premium_annual": quote.premium_annual,
                "deductible": quote.deductible,
                "discounts": quote.discounts,
                "valid_until": quote.valid_until,
                "status": quote.status,
            },
            "underwriting": underwriting_decision,
        }

    def _process_claim(self, input_data: Dict[str, Any]) -> Dict[str, Any]:
        """Process an insurance claim through the workflow."""
        client_id = input_data.get("client_id", "unknown")
        policy_id = input_data.get("policy_id", "unknown")

        # Step 1: Claim intake
        claim = self._intake_claim(client_id, policy_id, input_data)

        # Step 2: Fraud detection
        fraud_flags = []
        if self.config.get("claims_fraud_check_enabled", True):
            fraud_flags = self._check_fraud(claim)

        # Step 3: Claim investigation
        claim = self._investigate_claim(claim, fraud_flags)

        # Step 4: Claim adjudication
        claim = self._adjudicate_claim(claim)

        # Update metrics
        self.insurance_metrics.claims_processed += 1
        self._update_insurance_metrics()

        return {
            "claim_id": claim.id,
            "policy_id": claim.policy_id,
            "client_id": claim.client_id,
            "claim": {
                "type": claim.claim_type,
                "claimed_amount": claim.claimed_amount,
                "approved_amount": claim.approved_amount,
                "status": claim.status.value,
                "adjuster_notes": claim.adjuster_notes,
            },
            "fraud_check": {
                "flags_detected": len(fraud_flags) > 0,
                "flags": fraud_flags,
            },
            "investigation": {
                "findings": claim.investigation_findings,
            },
        }

    def _on_teardown(self) -> None:
        """Clean up insurance advisor solution resources."""
        self._agents.clear()
        self._clients.clear()
        self._assessments.clear()
        self._quotes.clear()
        self._claims.clear()
        self._quote_count = 0
        self._client_count = 0
        self._assessment_count = 0
        self._claim_count = 0

    # ------------------------------------------------------------------
    # Workflow steps
    # ------------------------------------------------------------------

    def _intake_client(self, data: Dict[str, Any]) -> ClientProfile:
        """Step 1: Create client profile from intake data."""
        self._client_count += 1
        client_id = data.get("client_id", f"client-{self._client_count}")

        client = ClientProfile(
            client_id=client_id,
            name=data.get("name", ""),
            age=data.get("age", 40),
            location=data.get("location", ""),
            occupation=data.get("occupation", ""),
            annual_income=data.get("annual_income", 0.0),
            claims_history=data.get("claims_history", 0),
            preferred_language=data.get("preferred_language", "en"),
        )
        self._clients[client_id] = client
        return client

    def _assess_risk(
        self, client: ClientProfile, data: Dict[str, Any]
    ) -> RiskAssessment:
        """Step 2: Perform risk assessment for the client."""
        self._assessment_count += 1
        assessment_id = f"risk-{self._assessment_count}"
        policy_type = PolicyType(data.get("policy_type", "auto"))

        # Calculate base risk score
        risk_score = 50.0
        risk_factors = []
        suggestions = []

        # Age factor
        if client.age < 25:
            risk_score += 15
            risk_factors.append("Young driver / low experience")
        elif client.age > 70:
            risk_score += 10
            risk_factors.append("Advanced age")
        elif 35 <= client.age <= 55:
            risk_score -= 10
            suggestions.append("Prime age discount eligible")

        # Claims history
        if client.claims_history >= 3:
            risk_score += 20
            risk_factors.append(f"Multiple prior claims ({client.claims_history})")
        elif client.claims_history == 0:
            risk_score -= 10
            suggestions.append("Claims-free discount eligible")

        # Occupation risk
        occupation_lower = client.occupation.lower()
        for risky_job in self._risk_patterns["high_risk_occupations"]:
            if risky_job in occupation_lower:
                risk_score += 10
                risk_factors.append(f"High-risk occupation: {client.occupation}")
                break

        # Location risk
        location_lower = client.location.lower()
        for risk_loc in self._risk_patterns["high_risk_locations"]:
            if risk_loc.replace("-", " ") in location_lower:
                risk_score += 10
                risk_factors.append(f"High-risk location: {client.location}")
                suggestions.append("Consider additional safety measures")
                break

        risk_score = max(0.0, min(100.0, risk_score))
        client.risk_score = risk_score

        # Determine risk level
        if risk_score < 20:
            risk_level = RiskLevel.VERY_LOW
        elif risk_score < 40:
            risk_level = RiskLevel.LOW
        elif risk_score < 60:
            risk_level = RiskLevel.MODERATE
        elif risk_score < 80:
            risk_level = RiskLevel.HIGH
        else:
            risk_level = RiskLevel.VERY_HIGH

        guidance = (
            f"Client presents {risk_level.value} risk profile with a "
            f"score of {risk_score:.1f}. "
            f"Key factors: {', '.join(risk_factors) if risk_factors else 'None identified'}."
        )

        assessment = RiskAssessment(
            id=assessment_id,
            client_id=client.client_id,
            policy_type=policy_type,
            risk_level=risk_level,
            risk_score=round(risk_score, 1),
            risk_factors=risk_factors,
            mitigation_suggestions=suggestions,
            underwriting_guidance=guidance,
        )
        self._assessments[assessment_id] = assessment
        return assessment

    def _generate_quote(
        self,
        client: ClientProfile,
        assessment: RiskAssessment,
        data: Dict[str, Any],
    ) -> Quote:
        """Step 3: Generate insurance quote based on risk assessment."""
        self._quote_count += 1
        quote_id = f"quote-{self._quote_count}"

        coverage_amount = data.get("coverage_amount", 100000.0)
        policy_type = assessment.policy_type

        # Base premium calculation based on policy type
        base_rates = {
            PolicyType.AUTO: 0.005,
            PolicyType.HOME: 0.003,
            PolicyType.LIFE: 0.002,
            PolicyType.HEALTH: 0.01,
            PolicyType.BUSINESS: 0.008,
            PolicyType.TRAVEL: 0.05,
            PolicyType.PROPERTY: 0.004,
            PolicyType.LIABILITY: 0.006,
        }
        base_rate = base_rates.get(policy_type, 0.005)

        # Risk multiplier
        risk_multiplier = 0.5 + (assessment.risk_score / 100) * 1.5  # 0.5x to 2.0x

        # Calculate premium
        annual_premium = coverage_amount * base_rate * risk_multiplier
        monthly_premium = annual_premium / 12

        # Deductible based on risk level
        deductible_tiers = {
            RiskLevel.VERY_LOW: coverage_amount * 0.01,
            RiskLevel.LOW: coverage_amount * 0.02,
            RiskLevel.MODERATE: coverage_amount * 0.05,
            RiskLevel.HIGH: coverage_amount * 0.1,
            RiskLevel.VERY_HIGH: coverage_amount * 0.15,
        }
        deductible = deductible_tiers.get(assessment.risk_level, coverage_amount * 0.05)

        # Discounts
        discounts = []
        if client.claims_history == 0:
            discounts.append("Claims-free discount (10%)")
            annual_premium *= 0.9
            monthly_premium = annual_premium / 12

        if assessment.risk_level in (RiskLevel.VERY_LOW, RiskLevel.LOW):
            discounts.append("Low-risk discount (5%)")
            annual_premium *= 0.95
            monthly_premium = annual_premium / 12

        # Calculate validity
        validity_days = self.config.get("quote_validity_days", 30)

        quote = Quote(
            id=quote_id,
            client_id=client.client_id,
            policy_type=policy_type,
            coverage_amount=coverage_amount,
            premium_monthly=round(monthly_premium, 2),
            premium_annual=round(annual_premium, 2),
            deductible=round(deductible, 2),
            coverage_details={
                "liability_coverage": coverage_amount * 0.6,
                "property_damage": coverage_amount * 0.3,
                "medical_payments": coverage_amount * 0.1,
            },
            discounts=discounts,
            exclusions=[
                "Intentional acts",
                "War and nuclear hazards",
                "Normal wear and tear",
            ],
            valid_until=f"{validity_days} days from issuance",
            status="generated",
        )
        self._quotes[quote_id] = quote
        return quote

    def _underwriting_decision(
        self, assessment: RiskAssessment
    ) -> Dict[str, Any]:
        """Step 4: Make underwriting decision based on risk assessment."""
        risk_tolerance = self.config.get("risk_tolerance", "moderate")
        auto_threshold = self.config.get("auto_approve_threshold", 40)

        tolerance_levels = {
            "very_low": 20,
            "low": 40,
            "moderate": 60,
            "high": 80,
            "very_high": 100,
        }
        max_acceptable = tolerance_levels.get(risk_tolerance, 60)

        approved = assessment.risk_score <= max_acceptable
        auto_approved = assessment.risk_score <= auto_threshold

        decision = {
            "approved": approved,
            "auto_approved": auto_approved,
            "risk_score": assessment.risk_score,
            "tolerance_level": risk_tolerance,
            "conditions": [],
            "decision_type": "auto" if auto_approved else ("manual_review" if approved else "declined"),
        }

        if approved and not auto_approved:
            decision["conditions"].append("Requires manual underwriter review")
        if not approved:
            decision["reason"] = (
                f"Risk score {assessment.risk_score:.1f} exceeds "
                f"maximum acceptable ({max_acceptable}) for {risk_tolerance} tolerance"
            )

        return decision

    def _intake_claim(
        self, client_id: str, policy_id: str, data: Dict[str, Any]
    ) -> Claim:
        """Step 1 (claims): Intake a new claim."""
        self._claim_count += 1
        claim_id = f"claim-{self._claim_count}"

        claim = Claim(
            id=claim_id,
            policy_id=policy_id,
            client_id=client_id,
            claim_type=data.get("claim_type", "property_damage"),
            incident_description=data.get("incident_description", ""),
            claimed_amount=data.get("claimed_amount", 0.0),
            status=ClaimStatus.UNDER_REVIEW,
        )
        self._claims[claim_id] = claim
        return claim

    def _check_fraud(self, claim: Claim) -> List[str]:
        """Step 2 (claims): Check for potential fraud indicators."""
        flags = []
        description_lower = claim.incident_description.lower()

        fraud_indicators = [
            ("filed too soon after policy start", "recent"),
            ("vague or inconsistent details", "vague"),
            ("no police report for theft", "theft"),
            ("claim amount rounded suspiciously", "round"),
            ("multiple recent claims", "multiple"),
        ]

        if len(claim.incident_description) < 20:
            flags.append("Minimal incident details provided")

        if claim.claimed_amount > 0 and claim.claimed_amount % 10000 == 0:
            flags.append("Suspiciously round claim amount")

        return flags

    def _investigate_claim(
        self, claim: Claim, fraud_flags: List[str]
    ) -> Claim:
        """Step 3 (claims): Investigate the claim details."""
        findings = []

        if fraud_flags:
            claim.status = ClaimStatus.INVESTIGATING
            findings.append(f"Fraud indicators detected: {len(fraud_flags)}")
            findings.append("Enhanced review recommended")
        else:
            findings.append("No immediate red flags detected")
            findings.append("Standard processing path")

        # Coverage check
        findings.append("Coverage verified for policy")
        findings.append("Claim type matches policy coverage")

        claim.investigation_findings = findings
        return claim

    def _adjudicate_claim(self, claim: Claim) -> Claim:
        """Step 4 (claims): Adjudicate the claim decision."""
        if claim.status == ClaimStatus.INVESTIGATING:
            # Fraud investigation pending further review
            claim.adjuster_notes = (
                "Claim flagged for enhanced investigation due to "
                "potential fraud indicators. Requires adjuster follow-up."
            )
            claim.approved_amount = 0.0
        else:
            # Standard approval (simplified)
            claim.approved_amount = claim.claimed_amount * 0.85  # 85% of claimed
            claim.status = ClaimStatus.APPROVED
            claim.adjuster_notes = (
                "Claim approved after standard review. "
                "Approved amount reflects deductible and coverage limits."
            )

        return claim

    # ------------------------------------------------------------------
    # Agent prompts
    # ------------------------------------------------------------------

    def _build_underwriter_prompt(self) -> str:
        """Build the system prompt for the underwriter role."""
        jurisdiction = self.config.get("jurisdiction", "us")
        return (
            "You are an experienced insurance underwriter responsible for "
            "evaluating risk and determining policy terms, conditions, and "
            f"pricing. Operating under {jurisdiction} regulatory requirements. "
            "Analyze applicant information, assess risk exposures, apply "
            "underwriting guidelines, and make sound decisions on policy "
            "issuance. Always document your reasoning and ensure compliance "
            "with applicable regulations."
        )

    def _build_claims_adjuster_prompt(self) -> str:
        """Build the system prompt for the claims adjuster role."""
        return (
            "You are a skilled insurance claims adjuster. Investigate "
            "claims thoroughly, assess damages accurately, determine "
            "coverage applicability, and make fair settlement decisions. "
            "Look for signs of potential fraud while treating claimants "
            "with respect and professionalism. Document all findings "
            "clearly and ensure timely claim resolution."
        )

    def _build_risk_assessor_prompt(self) -> str:
        """Build the system prompt for the risk assessor role."""
        return (
            "You are a risk assessment specialist for insurance underwriting. "
            "Analyze client profiles, identify risk factors and exposures, "
            "quantify risk levels using established models, and recommend "
            "mitigation strategies. Consider both qualitative and quantitative "
            "factors in your assessments and provide clear, actionable "
            "recommendations for risk reduction."
        )

    def _build_customer_service_prompt(self) -> str:
        """Build the system prompt for the customer service rep role."""
        return (
            "You are a helpful insurance customer service representative. "
            "Assist clients with policy inquiries, claim status updates, "
            "billing questions, and coverage explanations. Be empathetic, "
            "clear, and accurate in all communications. Escalate complex "
            "issues to the appropriate department when needed and ensure "
            "clients feel supported throughout their insurance journey."
        )

    # ------------------------------------------------------------------
    # Metrics
    # ------------------------------------------------------------------

    def _update_insurance_metrics(self) -> None:
        """Update insurance-specific metrics."""
        if self._assessments:
            total = len(self._assessments)
            avg_risk = sum(a.risk_score for a in self._assessments.values()) / total
            self.insurance_metrics.avg_risk_score = avg_risk

        if self._quotes:
            total_quotes = len(self._quotes)
            self.insurance_metrics.quote_accuracy = (
                self.insurance_metrics.policies_issued / total_quotes
                if total_quotes > 0 else 0.0
            )

        if self._claims:
            total_claims = len(self._claims)
            fraud_count = sum(
                1 for c in self._claims.values()
                if c.status == ClaimStatus.INVESTIGATING
            )
            self.insurance_metrics.fraud_detection_rate = (
                fraud_count / total_claims if total_claims > 0 else 0.0
            )

    def get_insurance_metrics(self) -> Dict[str, Any]:
        """Get the current insurance-specific metrics.

        Returns:
            Dictionary with insurance metrics including quote accuracy,
            claims processing time, customer satisfaction, and fraud detection.
        """
        return self.insurance_metrics.to_dict()
