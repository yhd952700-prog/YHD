"""
Customer Support AI Solution Template.

A pre-configured solution template for building AI-powered customer
support systems on the LiuHao AI OS platform. Includes support agent
roles, ticket triage workflows, knowledge base integration, and
quality checks.

Typical workflow:
    ticket intake -> triage -> research -> respond -> escalate
"""

from typing import Optional, Dict, Any, List
from enum import Enum
from dataclasses import dataclass, field

from solutions.templates.base_solution import (
    BaseSolution,
    SolutionMetadata,
    SolutionMetrics,
)


class TicketPriority(str, Enum):
    """Priority levels for support tickets."""
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    URGENT = "urgent"


class TicketStatus(str, Enum):
    """Status of a support ticket through the workflow."""
    NEW = "new"
    TRIAGED = "triaged"
    IN_PROGRESS = "in_progress"
    AWAITING_RESPONSE = "awaiting_response"
    ESCALATED = "escalated"
    RESOLVED = "resolved"
    CLOSED = "closed"


class AgentRole(str, Enum):
    """Pre-defined agent roles in the customer support solution."""
    SUPPORT_AGENT = "support_agent"
    QA_AGENT = "qa_agent"
    KNOWLEDGE_MANAGER = "knowledge_manager"
    ESCALATION_MANAGER = "escalation_manager"


@dataclass
class SupportTicket:
    """Represents a customer support ticket.

    Attributes:
        id: Unique ticket identifier.
        subject: Short summary of the issue.
        description: Detailed problem description.
        customer_id: Customer identifier.
        priority: Ticket priority level.
        status: Current workflow status.
        category: Issue category (e.g., "billing", "technical").
        assigned_agent: Agent currently handling the ticket.
        response: Generated response (if any).
        quality_score: Quality rating of the response (0-100).
        created_at: ISO 8601 creation timestamp.
        resolved_at: ISO 8601 resolution timestamp.
        tags: Tags for categorization.
        metadata: Additional ticket metadata.
    """
    id: str
    subject: str
    description: str
    customer_id: str
    priority: TicketPriority = TicketPriority.MEDIUM
    status: TicketStatus = TicketStatus.NEW
    category: str = "general"
    assigned_agent: Optional[str] = None
    response: Optional[str] = None
    quality_score: Optional[float] = None
    created_at: Optional[str] = None
    resolved_at: Optional[str] = None
    tags: List[str] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class SupportMetrics(SolutionMetrics):
    """Extended metrics for the customer support solution.

    Attributes:
        avg_resolution_time_min: Average ticket resolution time in minutes.
        avg_first_response_time_min: Average first response time in minutes.
        satisfaction_score: Average customer satisfaction score (0-5).
        escalation_rate: Fraction of tickets that get escalated (0-1).
        tickets_resolved: Total number of resolved tickets.
        tickets_escalated: Total number of escalated tickets.
    """
    avg_resolution_time_min: float = 0.0
    avg_first_response_time_min: float = 0.0
    satisfaction_score: float = 0.0
    escalation_rate: float = 0.0
    tickets_resolved: int = 0
    tickets_escalated: int = 0

    def to_dict(self) -> Dict[str, Any]:
        """Convert support metrics to a dictionary."""
        base = super().to_dict()
        base.update({
            "avg_resolution_time_min": self.avg_resolution_time_min,
            "avg_first_response_time_min": self.avg_first_response_time_min,
            "satisfaction_score": self.satisfaction_score,
            "escalation_rate": self.escalation_rate,
            "tickets_resolved": self.tickets_resolved,
            "tickets_escalated": self.tickets_escalated,
        })
        return base


class CustomerSupportSolution(BaseSolution):
    """Customer Support AI solution template.

    Provides a complete, pre-configured customer support solution
    with multiple agent roles, a standard ticket workflow,
    knowledge base integration, and quality assurance.

    Pre-configured agent roles:
        - Support Agent: Primary responder, handles ticket intake and response
        - QA Agent: Reviews responses for quality and accuracy
        - Knowledge Manager: Maintains and searches the knowledge base
        - Escalation Manager: Handles escalated tickets and complex issues

    Workflow:
        1. Ticket Intake - Receive and categorize new tickets
        2. Triage - Assign priority and route to appropriate agent
        3. Research - Search knowledge base for relevant information
        4. Respond - Generate and refine response
        5. Quality Check - Verify response quality and accuracy
        6. Escalate (if needed) - Route to human agent or specialist
        7. Resolve - Close ticket and learn from interaction

    Configuration options:
        - knowledge_base_id: ID of the support knowledge base
        - escalation_threshold: Quality score below which to escalate
        - auto_respond_enabled: Whether to auto-send responses
        - default_priority: Default ticket priority level
        - language: Primary support language
    """

    def _build_metadata(self) -> SolutionMetadata:
        """Build metadata for the customer support solution."""
        return SolutionMetadata(
            name="customer-support",
            version="1.0.0",
            industry="customer-service",
            description=(
                "AI-powered customer support solution with intelligent "
                "ticket triage, knowledge base integration, and "
                "response quality assurance."
            ),
            author="LiuHao AI OS",
            tags=["customer-support", "helpdesk", "tickets", "knowledge-base"],
            min_liuhao_version="1.0.0",
        )

    def _build_config_schema(self) -> None:
        """Define configuration fields for the customer support solution."""
        self.config_schema.add_field(
            name="knowledge_base_id",
            field_type="string",
            required=True,
            description="ID of the knowledge base for support articles",
        )
        self.config_schema.add_field(
            name="escalation_threshold",
            field_type="float",
            required=False,
            default=70.0,
            description="Quality score below which tickets are escalated (0-100)",
        )
        self.config_schema.add_field(
            name="auto_respond_enabled",
            field_type="bool",
            required=False,
            default=True,
            description="Whether to automatically send AI-generated responses",
        )
        self.config_schema.add_field(
            name="default_priority",
            field_type="string",
            required=False,
            default="medium",
            description="Default priority for new tickets",
            valid_values=["low", "medium", "high", "urgent"],
        )
        self.config_schema.add_field(
            name="language",
            field_type="string",
            required=False,
            default="en",
            description="Primary support language code",
        )
        self.config_schema.add_field(
            name="support_hours",
            field_type="dict",
            required=False,
            description="Support hours configuration",
        )
        self.config_schema.add_field(
            name="max_tickets_per_agent",
            field_type="int",
            required=False,
            default=20,
            description="Maximum concurrent tickets per agent",
        )
        self.config_schema.add_field(
            name="response_tone",
            field_type="string",
            required=False,
            default="professional",
            description="Tone/style for AI responses",
            valid_values=["professional", "friendly", "casual", "formal"],
        )

    def _on_initialize(self) -> None:
        """Initialize customer support solution state."""
        self._agents: Dict[str, Dict[str, Any]] = {}
        self._tickets: Dict[str, SupportTicket] = {}
        self._ticket_count = 0

        # Initialize support-specific metrics
        self.support_metrics = SupportMetrics()

    def _on_deploy(self) -> None:
        """Deploy customer support agents and workflows."""
        # Create the four agent roles
        agent_configs = [
            (AgentRole.SUPPORT_AGENT, {
                "name": "Support Agent",
                "description": "Primary customer support agent",
                "system_prompt": self._build_support_agent_prompt(),
                "tools": ["knowledge_search", "ticket_management"],
            }),
            (AgentRole.QA_AGENT, {
                "name": "QA Agent",
                "description": "Quality assurance agent for response review",
                "system_prompt": self._build_qa_agent_prompt(),
                "tools": ["quality_check", "response_evaluation"],
            }),
            (AgentRole.KNOWLEDGE_MANAGER, {
                "name": "Knowledge Manager",
                "description": "Knowledge base search and maintenance agent",
                "system_prompt": self._build_knowledge_manager_prompt(),
                "tools": ["knowledge_search", "document_search"],
            }),
            (AgentRole.ESCALATION_MANAGER, {
                "name": "Escalation Manager",
                "description": "Handles escalated tickets and complex issues",
                "system_prompt": self._build_escalation_manager_prompt(),
                "tools": ["ticket_escalation", "routing"],
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
        """Process a support ticket through the full workflow.

        Args:
            input_data: Dictionary containing ticket data with at least
                ``subject``, ``description``, and ``customer_id``.

        Returns:
            Dictionary with ticket processing results including
            response, quality score, and action taken.
        """
        # Step 1: Create ticket
        ticket = self._intake_ticket(input_data)

        # Step 2: Triage
        ticket = self._triage_ticket(ticket)

        # Step 3: Research (knowledge base search)
        research_results = self._research_ticket(ticket)

        # Step 4: Generate response
        response = self._generate_response(ticket, research_results)
        ticket.response = response
        ticket.status = TicketStatus.AWAITING_RESPONSE

        # Step 5: Quality check
        quality_score = self._quality_check(ticket)
        ticket.quality_score = quality_score

        # Step 6: Decide whether to escalate
        escalation_threshold = self.config.get("escalation_threshold", 70.0)
        action = "respond"
        escalation_reason = None

        if quality_score < escalation_threshold or ticket.priority == TicketPriority.URGENT:
            ticket = self._escalate_ticket(ticket)
            action = "escalate"
            escalation_reason = (
                "Low quality score" if quality_score < escalation_threshold
                else "Urgent priority"
            )
            self.support_metrics.tickets_escalated += 1
        else:
            ticket.status = TicketStatus.RESOLVED
            self.support_metrics.tickets_resolved += 1

        # Update support metrics
        self._update_support_metrics()

        return {
            "ticket_id": ticket.id,
            "ticket": {
                "subject": ticket.subject,
                "priority": ticket.priority.value,
                "category": ticket.category,
                "status": ticket.status.value,
            },
            "response": response,
            "quality_score": quality_score,
            "action": action,
            "escalation_reason": escalation_reason,
            "research_results_count": len(research_results),
        }

    def _on_teardown(self) -> None:
        """Clean up customer support solution resources."""
        self._agents.clear()
        self._tickets.clear()
        self._ticket_count = 0

    # ------------------------------------------------------------------
    # Workflow steps
    # ------------------------------------------------------------------

    def _intake_ticket(self, data: Dict[str, Any]) -> SupportTicket:
        """Step 1: Create a new support ticket from input data."""
        self._ticket_count += 1
        ticket = SupportTicket(
            id=f"ticket-{self._ticket_count}",
            subject=data.get("subject", "No subject"),
            description=data.get("description", ""),
            customer_id=data.get("customer_id", "unknown"),
            priority=TicketPriority(
                data.get("priority", self.config.get("default_priority", "medium"))
            ),
            status=TicketStatus.NEW,
            tags=data.get("tags", []),
        )
        self._tickets[ticket.id] = ticket
        return ticket

    def _triage_ticket(self, ticket: SupportTicket) -> SupportTicket:
        """Step 2: Triage the ticket - categorize and set priority."""
        # Simple categorization based on keywords
        description_lower = ticket.description.lower()
        category = "general"

        category_keywords = {
            "billing": ["billing", "invoice", "payment", "charge", "refund", "price"],
            "technical": ["error", "bug", "crash", "not working", "fail", "broken", "technical"],
            "account": ["account", "login", "password", "access", "profile", "user"],
            "feature": ["feature", "request", "suggestion", "enhancement", "idea"],
        }

        for cat, keywords in category_keywords.items():
            if any(kw in description_lower for kw in keywords):
                category = cat
                break

        ticket.category = category
        ticket.status = TicketStatus.TRIAGED
        ticket.assigned_agent = AgentRole.SUPPORT_AGENT.value
        return ticket

    def _research_ticket(self, ticket: SupportTicket) -> List[Dict[str, Any]]:
        """Step 3: Search knowledge base for relevant information."""
        # In a real implementation, this would call the knowledge base API
        query = f"{ticket.subject} {ticket.description}"
        return [
            {
                "title": f"Article about {ticket.category}",
                "relevance": 0.85,
                "snippet": "Relevant knowledge base article snippet...",
            }
        ]

    def _generate_response(
        self,
        ticket: SupportTicket,
        research: List[Dict[str, Any]],
    ) -> str:
        """Step 4: Generate a response to the customer."""
        tone = self.config.get("response_tone", "professional")
        return (
            f"Thank you for reaching out regarding '{ticket.subject}'. "
            f"I understand you're experiencing an issue with {ticket.category}. "
            f"Based on our knowledge base, here's what might help: "
            f"[Response generated in {tone} tone]"
        )

    def _quality_check(self, ticket: SupportTicket) -> float:
        """Step 5: Evaluate response quality.

        Returns a quality score from 0-100 based on:
        - Relevance to the ticket
        - Completeness of information
        - Tone and professionalism
        - Knowledge base citation accuracy
        """
        # Simulated quality scoring
        base_score = 85.0
        if ticket.priority == TicketPriority.URGENT:
            base_score -= 5.0  # More complex = harder to score high
        if len(ticket.description) > 500:
            base_score -= 3.0  # Longer tickets are more complex
        return max(0.0, min(100.0, base_score))

    def _escalate_ticket(self, ticket: SupportTicket) -> SupportTicket:
        """Step 6: Escalate the ticket to a human/specialist agent."""
        ticket.status = TicketStatus.ESCALATED
        ticket.assigned_agent = AgentRole.ESCALATION_MANAGER.value
        return ticket

    # ------------------------------------------------------------------
    # Agent prompts
    # ------------------------------------------------------------------

    def _build_support_agent_prompt(self) -> str:
        """Build the system prompt for the support agent role."""
        tone = self.config.get("response_tone", "professional")
        return (
            f"You are a helpful customer support agent. "
            f"Respond in a {tone} tone. "
            f"Always be empathetic and professional. "
            f"Use the knowledge base to find accurate answers. "
            f"If you're unsure about something, escalate to a specialist."
        )

    def _build_qa_agent_prompt(self) -> str:
        """Build the system prompt for the QA agent role."""
        return (
            "You are a quality assurance agent for customer support. "
            "Review support responses for accuracy, completeness, tone, "
            "and adherence to company policies. Provide a quality score "
            "from 0-100 and specific feedback for improvement."
        )

    def _build_knowledge_manager_prompt(self) -> str:
        """Build the system prompt for the knowledge manager role."""
        return (
            "You are a knowledge base specialist. "
            "Search the knowledge base to find the most relevant articles "
            "for customer inquiries. Summarize key points and suggest "
            "improvements to knowledge base content when gaps are found."
        )

    def _build_escalation_manager_prompt(self) -> str:
        """Build the system prompt for the escalation manager role."""
        return (
            "You are an escalation manager for complex support issues. "
            "Handle tickets that couldn't be resolved by the first-line agent. "
            "Coordinate with specialists, manage customer expectations, "
            "and ensure timely resolution of escalated issues."
        )

    # ------------------------------------------------------------------
    # Metrics
    # ------------------------------------------------------------------

    def _update_support_metrics(self) -> None:
        """Update support-specific metrics based on ticket counts."""
        total = self.support_metrics.tickets_resolved + self.support_metrics.tickets_escalated
        if total > 0:
            self.support_metrics.escalation_rate = (
                self.support_metrics.tickets_escalated / total
            )

    def get_support_metrics(self) -> Dict[str, Any]:
        """Get the current support-specific metrics.

        Returns:
            Dictionary with support metrics including resolution time,
            satisfaction, and escalation rate.
        """
        return self.support_metrics.to_dict()
