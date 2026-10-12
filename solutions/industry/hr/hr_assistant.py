"""
HR AI Assistant Solution Template.

A comprehensive AI-powered HR assistant solution for human resources
departments and talent teams. Includes multi-agent roles for recruiting,
onboarding, performance analysis, and policy advisory.

Typical workflow:
    job description -> resume screening -> interview scheduling
    -> onboarding -> performance review
"""

from typing import Optional, Dict, Any, List
from enum import Enum
from dataclasses import dataclass, field

from solutions.templates.base_solution import (
    BaseSolution,
    SolutionMetadata,
    SolutionMetrics,
)


class EmploymentType(str, Enum):
    """Employment types."""
    FULL_TIME = "full_time"
    PART_TIME = "part_time"
    CONTRACT = "contract"
    INTERNSHIP = "internship"
    TEMPORARY = "temporary"


class SeniorityLevel(str, Enum):
    """Seniority levels for positions."""
    ENTRY = "entry_level"
    JUNIOR = "junior"
    MID = "mid_level"
    SENIOR = "senior"
    LEAD = "lead"
    DIRECTOR = "director"
    EXECUTIVE = "executive"


class CandidateStatus(str, Enum):
    """Status of a candidate in the hiring pipeline."""
    APPLIED = "applied"
    SCREENED = "screened"
    PHONE_SCREEN = "phone_screen"
    TECHNICAL_INTERVIEW = "technical_interview"
    BEHAVIORAL_INTERVIEW = "behavioral_interview"
    FINAL_INTERVIEW = "final_interview"
    OFFERED = "offered"
    OFFER_ACCEPTED = "offer_accepted"
    OFFER_DECLINED = "offer_declined"
    REJECTED = "rejected"
    WITHDRAWN = "withdrawn"
    HIRED = "hired"


class HRRole(str, Enum):
    """Agent roles in the HR assistant solution."""
    RECRUITER = "recruiter"
    ONBOARDING_SPECIALIST = "onboarding_specialist"
    PERFORMANCE_ANALYST = "performance_analyst"
    POLICY_ADVISOR = "policy_advisor"


@dataclass
class JobDescription:
    """Represents a job description.

    Attributes:
        job_id: Unique job identifier.
        title: Job title.
        department: Department.
        employment_type: Type of employment.
        seniority: Seniority level.
        location: Job location.
        remote: Whether the position is remote.
        salary_range: Salary range (min, max).
        requirements: List of job requirements.
        responsibilities: List of job responsibilities.
        benefits: List of benefits offered.
        description: Full job description text.
        posted_date: ISO 8601 posting date.
    """
    job_id: str
    title: str
    department: str = ""
    employment_type: EmploymentType = EmploymentType.FULL_TIME
    seniority: SeniorityLevel = SeniorityLevel.MID
    location: str = ""
    remote: bool = False
    salary_range: Dict[str, float] = field(default_factory=dict)
    requirements: List[str] = field(default_factory=list)
    responsibilities: List[str] = field(default_factory=list)
    benefits: List[str] = field(default_factory=list)
    description: str = ""
    posted_date: Optional[str] = None


@dataclass
class Candidate:
    """Represents a job candidate.

    Attributes:
        candidate_id: Unique candidate identifier.
        name: Candidate name.
        email: Candidate email.
        job_id: Job applied for.
        resume_text: Resume text content.
        skills: List of skills from resume.
        experience_years: Years of experience.
        education: Education background.
        match_score: Overall match score (0-100).
        skill_match: Skill match percentage.
        experience_match: Experience match score.
        status: Current candidate status.
        applied_date: ISO 8601 application date.
        notes: Recruiter notes.
    """
    candidate_id: str
    name: str = ""
    email: str = ""
    job_id: str = ""
    resume_text: str = ""
    skills: List[str] = field(default_factory=list)
    experience_years: float = 0.0
    education: str = ""
    match_score: float = 0.0
    skill_match: float = 0.0
    experience_match: float = 0.0
    status: CandidateStatus = CandidateStatus.APPLIED
    applied_date: Optional[str] = None
    notes: List[str] = field(default_factory=list)


@dataclass
class OnboardingPlan:
    """Represents an employee onboarding plan.

    Attributes:
        plan_id: Unique onboarding plan identifier.
        employee_id: Employee identifier.
        job_title: Job title.
        department: Department.
        start_date: Start date.
        tasks: List of onboarding tasks with status.
        training_modules: List of training modules.
        paperwork_required: Required paperwork list.
        buddy_mentor: Assigned buddy/mentor.
        completed_tasks: Number of completed tasks.
        total_tasks: Total number of tasks.
        status: Current onboarding status.
    """
    plan_id: str
    employee_id: str
    job_title: str = ""
    department: str = ""
    start_date: Optional[str] = None
    tasks: List[Dict[str, Any]] = field(default_factory=list)
    training_modules: List[str] = field(default_factory=list)
    paperwork_required: List[str] = field(default_factory=list)
    buddy_mentor: str = ""
    completed_tasks: int = 0
    total_tasks: int = 0
    status: str = "pending"


@dataclass
class PerformanceReview:
    """Represents a performance review.

    Attributes:
        review_id: Unique review identifier.
        employee_id: Employee identifier.
        period: Review period.
        overall_rating: Overall performance rating (1-5).
        strengths: Identified strengths.
        areas_to_improve: Areas for improvement.
        goals: Goals for next period.
        feedback: Collected feedback.
        skills_ratings: Ratings for specific skills.
        recommendations: Development recommendations.
        completed_date: ISO 8601 completion date.
    """
    review_id: str
    employee_id: str
    period: str = ""
    overall_rating: float = 3.0
    strengths: List[str] = field(default_factory=list)
    areas_to_improve: List[str] = field(default_factory=list)
    goals: List[str] = field(default_factory=list)
    feedback: List[str] = field(default_factory=list)
    skills_ratings: Dict[str, float] = field(default_factory=dict)
    recommendations: List[str] = field(default_factory=list)
    completed_date: Optional[str] = None


@dataclass
class HRMetrics(SolutionMetrics):
    """Extended metrics for the HR assistant solution.

    Attributes:
        time_to_hire_days: Average time to hire in days.
        retention_rate: Employee retention rate (0-1).
        employee_satisfaction: Average employee satisfaction score (0-5).
        positions_filled: Total positions filled.
        candidates_screened: Total candidates screened.
        onboarding_completion_rate: Onboarding completion rate.
        offer_acceptance_rate: Offer acceptance rate.
        avg_performance_rating: Average performance rating.
    """
    time_to_hire_days: float = 0.0
    retention_rate: float = 0.0
    employee_satisfaction: float = 0.0
    positions_filled: int = 0
    candidates_screened: int = 0
    onboarding_completion_rate: float = 0.0
    offer_acceptance_rate: float = 0.0
    avg_performance_rating: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        """Convert HR metrics to a dictionary."""
        base = super().to_dict()
        base.update({
            "time_to_hire_days": self.time_to_hire_days,
            "retention_rate": self.retention_rate,
            "employee_satisfaction": self.employee_satisfaction,
            "positions_filled": self.positions_filled,
            "candidates_screened": self.candidates_screened,
            "onboarding_completion_rate": self.onboarding_completion_rate,
            "offer_acceptance_rate": self.offer_acceptance_rate,
            "avg_performance_rating": self.avg_performance_rating,
        })
        return base


class HRAssistantSolution(BaseSolution):
    """HR AI Assistant solution template.

    Provides a complete, pre-configured HR AI assistant with
    multi-agent collaboration for recruiting, onboarding,
    performance analysis, and policy advisory.

    Pre-configured agent roles:
        - Recruiter: Manages job descriptions, resume screening, and interviews
        - Onboarding Specialist: Creates and manages employee onboarding
        - Performance Analyst: Analyzes performance data and generates reviews
        - Policy Advisor: Provides HR policy guidance and compliance

    Workflow:
        1. Job Description - Create and optimize job descriptions
        2. Resume Screening - Screen and rank candidates
        3. Interview Scheduling - Coordinate interview process
        4. Onboarding - Create structured onboarding plans
        5. Performance Review - Conduct performance analysis

    Configuration options:
        - company_size: Size of the company
        - industry: Industry for HR context
        - hiring_workflow: Hiring workflow complexity
        - remote_first: Whether the company is remote-first
        - compliance_region: HR compliance region
    """

    def _build_metadata(self) -> SolutionMetadata:
        """Build metadata for the HR assistant solution."""
        return SolutionMetadata(
            name="hr-assistant",
            version="1.0.0",
            industry="human_resources",
            description=(
                "AI-powered HR assistant solution with recruiting, resume screening, "
                "onboarding, performance analysis, and policy advisory."
            ),
            author="LiuHao AI OS",
            tags=["hr", "recruiting", "talent", "onboarding", "performance", "human-resources"],
            min_liuhao_version="1.0.0",
        )

    def _build_config_schema(self) -> None:
        """Define configuration fields for the HR assistant solution."""
        self.config_schema.add_field(
            name="company_size",
            field_type="string",
            required=True,
            description="Size of the company",
            valid_values=["startup", "small", "medium", "large", "enterprise"],
        )
        self.config_schema.add_field(
            name="industry",
            field_type="string",
            required=False,
            default="technology",
            description="Industry for HR context",
        )
        self.config_schema.add_field(
            name="hiring_workflow",
            field_type="string",
            required=False,
            default="standard",
            description="Hiring workflow complexity",
            valid_values=["simple", "standard", "comprehensive", "enterprise"],
        )
        self.config_schema.add_field(
            name="remote_first",
            field_type="bool",
            required=False,
            default=False,
            description="Whether the company is remote-first",
        )
        self.config_schema.add_field(
            name="compliance_region",
            field_type="string",
            required=False,
            default="us",
            description="HR compliance region",
            valid_values=["us", "eu", "uk", "cn", "jp", "global"],
        )
        self.config_schema.add_field(
            name="screening_threshold",
            field_type="float",
            required=False,
            default=70.0,
            description="Minimum match score to pass screening (0-100)",
        )
        self.config_schema.add_field(
            name="onboarding_duration_days",
            field_type="int",
            required=False,
            default=30,
            description="Standard onboarding duration in days",
        )

    def _on_initialize(self) -> None:
        """Initialize HR assistant solution state."""
        self._agents: Dict[str, Dict[str, Any]] = {}
        self._jobs: Dict[str, JobDescription] = {}
        self._candidates: Dict[str, Candidate] = {}
        self._onboarding_plans: Dict[str, OnboardingPlan] = {}
        self._performance_reviews: Dict[str, PerformanceReview] = {}
        self._job_count = 0
        self._candidate_count = 0

        # Initialize HR-specific metrics
        self.hr_metrics = HRMetrics()

    def _on_deploy(self) -> None:
        """Deploy HR assistant agents and workflows."""
        agent_configs = [
            (HRRole.RECRUITER, {
                "name": "Recruiter",
                "description": "Manages job descriptions and candidate screening",
                "system_prompt": self._build_recruiter_prompt(),
                "tools": ["job_writer", "resume_parser", "candidate_ranking"],
            }),
            (HRRole.ONBOARDING_SPECIALIST, {
                "name": "Onboarding Specialist",
                "description": "Creates and manages employee onboarding",
                "system_prompt": self._build_onboarding_specialist_prompt(),
                "tools": ["onboarding_builder", "checklist_generator", "training_assigner"],
            }),
            (HRRole.PERFORMANCE_ANALYST, {
                "name": "Performance Analyst",
                "description": "Analyzes performance data and generates reviews",
                "system_prompt": self._build_performance_analyst_prompt(),
                "tools": ["review_generator", "feedback_analyzer", "goal_setter"],
            }),
            (HRRole.POLICY_ADVISOR, {
                "name": "Policy Advisor",
                "description": "Provides HR policy guidance and compliance",
                "system_prompt": self._build_policy_advisor_prompt(),
                "tools": ["policy_library", "compliance_checker", "policy_explainer"],
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
        """Process an HR request through the appropriate workflow.

        Args:
            input_data: Dictionary containing HR request data.
                The workflow type is determined by the ``request_type`` field.

        Returns:
            Dictionary with HR processing results.
        """
        request_type = input_data.get("request_type", "recruiting")

        if request_type == "recruiting":
            return self._run_recruiting_workflow(input_data)
        elif request_type == "onboarding":
            return self._run_onboarding_workflow(input_data)
        elif request_type == "performance":
            return self._run_performance_workflow(input_data)
        elif request_type == "policy":
            return self._run_policy_workflow(input_data)
        else:
            return self._run_recruiting_workflow(input_data)

    def _run_recruiting_workflow(self, input_data: Dict[str, Any]) -> Dict[str, Any]:
        """Run the full recruiting workflow."""
        # Step 1: Create/enhance job description
        job = self._create_job_description(input_data)

        # Step 2: Screen candidates
        candidates = self._screen_candidates(job, input_data.get("candidates", []))

        # Step 3: Schedule interviews (for top candidates)
        top_candidates = [c for c in candidates if c.match_score >= self.config.get("screening_threshold", 70)]
        interview_schedule = self._schedule_interviews(top_candidates)

        # Update metrics
        self.hr_metrics.candidates_screened += len(candidates)
        if top_candidates:
            self.hr_metrics.positions_filled += 1
        self._update_hr_metrics()

        return {
            "workflow": "recruiting",
            "job": {
                "id": job.job_id,
                "title": job.title,
                "department": job.department,
                "seniority": job.seniority.value,
                "requirements_count": len(job.requirements),
            },
            "screening": {
                "total_candidates": len(candidates),
                "passed_count": len(top_candidates),
                "threshold": self.config.get("screening_threshold", 70),
                "top_candidates": [
                    {
                        "id": c.candidate_id,
                        "name": c.name,
                        "match_score": c.match_score,
                        "skill_match": c.skill_match,
                        "experience": c.experience_years,
                    }
                    for c in top_candidates[:5]
                ],
            },
            "interview_schedule": interview_schedule,
        }

    def _run_onboarding_workflow(self, input_data: Dict[str, Any]) -> Dict[str, Any]:
        """Run the onboarding workflow."""
        plan = self._create_onboarding_plan(input_data)
        self.hr_metrics.onboarding_completion_rate = 0.85
        self._update_hr_metrics()

        return {
            "workflow": "onboarding",
            "plan": {
                "id": plan.plan_id,
                "employee_id": plan.employee_id,
                "job_title": plan.job_title,
                "department": plan.department,
                "total_tasks": plan.total_tasks,
                "tasks": [t["name"] for t in plan.tasks[:10]],
                "training_modules": plan.training_modules,
                "paperwork": plan.paperwork_required,
                "status": plan.status,
            },
        }

    def _run_performance_workflow(self, input_data: Dict[str, Any]) -> Dict[str, Any]:
        """Run the performance review workflow."""
        review = self._create_performance_review(input_data)
        self.hr_metrics.avg_performance_rating = review.overall_rating
        self._update_hr_metrics()

        return {
            "workflow": "performance",
            "review": {
                "id": review.review_id,
                "employee_id": review.employee_id,
                "period": review.period,
                "overall_rating": review.overall_rating,
                "strengths": review.strengths,
                "areas_to_improve": review.areas_to_improve,
                "goals": review.goals,
                "recommendations": review.recommendations,
                "skills_ratings": review.skills_ratings,
            },
        }

    def _run_policy_workflow(self, input_data: Dict[str, Any]) -> Dict[str, Any]:
        """Run the policy advisory workflow."""
        policy_query = input_data.get("query", "")
        region = self.config.get("compliance_region", "us")

        answer, related_policies = self._advise_on_policy(policy_query)

        return {
            "workflow": "policy",
            "query": policy_query,
            "region": region,
            "answer": answer,
            "related_policies": related_policies,
            "disclaimer": "This is for informational purposes only. Consult HR for official guidance.",
        }

    def _on_teardown(self) -> None:
        """Clean up HR assistant solution resources."""
        self._agents.clear()
        self._jobs.clear()
        self._candidates.clear()
        self._onboarding_plans.clear()
        self._performance_reviews.clear()
        self._job_count = 0
        self._candidate_count = 0

    # ------------------------------------------------------------------
    # Workflow step implementations
    # ------------------------------------------------------------------

    def _create_job_description(self, data: Dict[str, Any]) -> JobDescription:
        """Step 1: Create an optimized job description."""
        self._job_count += 1
        job_id = data.get("job_id", f"job-{self._job_count}")
        title = data.get("title", f"Position {self._job_count}")

        job = JobDescription(
            job_id=job_id,
            title=title,
            department=data.get("department", "General"),
            employment_type=EmploymentType(data.get("employment_type", EmploymentType.FULL_TIME.value)),
            seniority=SeniorityLevel(data.get("seniority", SeniorityLevel.MID.value)),
            location=data.get("location", ""),
            remote=data.get("remote", self.config.get("remote_first", False)),
            salary_range=data.get("salary_range", {"min": 0, "max": 0}),
            requirements=data.get("requirements", [
                f"Experience in {self.config.get('industry', 'technology')}",
                "Strong communication skills",
                "Team collaboration experience",
                "Problem-solving abilities",
            ]),
            responsibilities=data.get("responsibilities", [
                f"Perform core {title} responsibilities",
                "Collaborate with cross-functional teams",
                "Contribute to team goals and initiatives",
            ]),
            benefits=data.get("benefits", [
                "Competitive salary",
                "Health insurance",
                "401k matching",
                "Flexible work arrangement",
                "Professional development budget",
            ]),
            description=data.get("description", ""),
        )
        self._jobs[job_id] = job
        return job

    def _screen_candidates(
        self, job: JobDescription, candidate_data: List[Dict[str, Any]]
    ) -> List[Candidate]:
        """Step 2: Screen and rank candidates for the position."""
        candidates = []

        if not candidate_data:
            # Generate sample candidates
            candidate_data = [
                {"name": "Candidate One", "skills": ["Python", "SQL", "Leadership"], "experience": 5},
                {"name": "Candidate Two", "skills": ["Java", "Agile", "Communication"], "experience": 3},
                {"name": "Candidate Three", "skills": ["Python", "Machine Learning", "Research"], "experience": 7},
                {"name": "Candidate Four", "skills": ["Project Management", "Scrum", "Leadership"], "experience": 8},
                {"name": "Candidate Five", "skills": ["Data Analysis", "SQL", "Excel"], "experience": 2},
            ]

        for i, cdata in enumerate(candidate_data):
            self._candidate_count += 1
            cand_id = f"candidate-{self._candidate_count}"

            # Calculate match scores
            skills = cdata.get("skills", [])
            required_skills = [r.lower() for r in job.requirements]
            matched_skills = sum(1 for s in skills if any(s.lower() in req for req in required_skills))
            skill_match = (matched_skills / max(1, len(job.requirements))) * 100

            experience = cdata.get("experience", 0)
            exp_target = {"entry_level": 1, "junior": 3, "mid_level": 5, "senior": 7, "lead": 10}
            target = exp_target.get(job.seniority.value, 5)
            experience_match = min(100.0, (experience / target) * 100) if target > 0 else 50.0

            overall = (skill_match * 0.6) + (experience_match * 0.4)

            candidate = Candidate(
                candidate_id=cand_id,
                name=cdata.get("name", f"Candidate {i+1}"),
                email=cdata.get("email", ""),
                job_id=job.job_id,
                skills=skills,
                experience_years=experience,
                match_score=round(overall, 1),
                skill_match=round(skill_match, 1),
                experience_match=round(experience_match, 1),
                status=CandidateStatus.SCREENED,
            )
            candidates.append(candidate)
            self._candidates[cand_id] = candidate

        # Sort by match score descending
        candidates.sort(key=lambda c: c.match_score, reverse=True)
        return candidates

    def _schedule_interviews(self, candidates: List[Candidate]) -> List[Dict[str, Any]]:
        """Step 3: Schedule interviews for top candidates."""
        workflow = self.config.get("hiring_workflow", "standard")
        stages = {
            "simple": ["phone_screen", "final_interview"],
            "standard": ["phone_screen", "technical_interview", "final_interview"],
            "comprehensive": ["phone_screen", "technical_interview", "behavioral_interview", "final_interview"],
            "enterprise": ["phone_screen", "technical_interview", "behavioral_interview", "team_interview", "final_interview"],
        }.get(workflow, ["phone_screen", "final_interview"])

        schedule = []
        for candidate in candidates[:5]:
            schedule.append({
                "candidate_id": candidate.candidate_id,
                "candidate_name": candidate.name,
                "interview_stages": stages,
                "estimated_days_to_complete": len(stages) * 3,
            })
        return schedule

    def _create_onboarding_plan(self, data: Dict[str, Any]) -> OnboardingPlan:
        """Create a structured onboarding plan."""
        employee_id = data.get("employee_id", "new-hire-1")
        plan_id = f"onboarding-{employee_id}"
        duration = self.config.get("onboarding_duration_days", 30)

        tasks = [
            {"name": "Complete paperwork", "phase": "pre-day-1", "assigned_to": "HR", "status": "pending"},
            {"name": "Set up accounts and access", "phase": "pre-day-1", "assigned_to": "IT", "status": "pending"},
            {"name": "Welcome and introduction", "phase": "day-1", "assigned_to": "Manager", "status": "pending"},
            {"name": "Team meet and greet", "phase": "day-1", "assigned_to": "Team", "status": "pending"},
            {"name": "Company orientation", "phase": "week-1", "assigned_to": "HR", "status": "pending"},
            {"name": "Role-specific training", "phase": "week-1", "assigned_to": "Manager", "status": "pending"},
            {"name": "Tools and systems training", "phase": "week-1", "assigned_to": "IT", "status": "pending"},
            {"name": "First project assignment", "phase": "week-2", "assigned_to": "Manager", "status": "pending"},
            {"name": "Buddy/mentor check-in", "phase": "week-2", "assigned_to": "Buddy", "status": "pending"},
            {"name": "30-day check-in", "phase": "month-1", "assigned_to": "Manager", "status": "pending"},
        ]

        plan = OnboardingPlan(
            plan_id=plan_id,
            employee_id=employee_id,
            job_title=data.get("job_title", ""),
            department=data.get("department", ""),
            tasks=tasks,
            training_modules=[
                "Company Culture and Values",
                "Compliance and Ethics",
                "Security and Privacy",
                "Role-Specific Skills",
                "Tools and Systems",
            ],
            paperwork_required=[
                "Employment agreement",
                "Tax forms",
                "Benefits enrollment",
                "NDA/Confidentiality agreement",
            ],
            buddy_mentor=data.get("buddy", "Assigned buddy"),
            completed_tasks=0,
            total_tasks=len(tasks),
            status="ready",
        )
        self._onboarding_plans[plan_id] = plan
        return plan

    def _create_performance_review(self, data: Dict[str, Any]) -> PerformanceReview:
        """Create a performance review."""
        employee_id = data.get("employee_id", "employee-1")
        review_id = f"review-{employee_id}"
        period = data.get("period", "Q4 2024")

        # Generate review based on data
        overall_rating = data.get("overall_rating", 3.5)

        review = PerformanceReview(
            review_id=review_id,
            employee_id=employee_id,
            period=period,
            overall_rating=overall_rating,
            strengths=data.get("strengths", [
                "Strong technical skills",
                "Reliable and consistent delivery",
                "Good team collaboration",
            ]),
            areas_to_improve=data.get("areas_to_improve", [
                "Public speaking/presentations",
                "Strategic thinking",
            ]),
            goals=data.get("goals", [
                "Complete advanced certification",
                "Lead at least one major project",
                "Improve cross-functional communication",
            ]),
            feedback=data.get("feedback", [
                "Consistently meets deadlines",
                "Great attention to detail",
                "Could take more initiative",
            ]),
            skills_ratings={
                "Technical Skills": overall_rating + 0.2,
                "Communication": overall_rating - 0.1,
                "Collaboration": overall_rating,
                "Problem Solving": overall_rating + 0.1,
                "Leadership": overall_rating - 0.3,
            },
            recommendations=[
                "Enroll in leadership development program",
                "Take on more client-facing opportunities",
                "Consider cross-functional project participation",
            ],
        )
        self._performance_reviews[review_id] = review
        return review

    def _advise_on_policy(self, query: str) -> tuple[str, List[str]]:
        """Provide HR policy guidance."""
        query_lower = query.lower()

        if "leave" in query_lower or "pto" in query_lower or "vacation" in query_lower:
            answer = (
                "Our leave policy includes: 15 days of paid vacation per year, "
                "10 paid holidays, 5 personal days, and 10 sick days. "
                "Vacation accrues monthly and rolls over up to 5 days. "
                "Please submit leave requests through the HR portal at least 2 weeks in advance."
            )
            related = ["Paid Time Off Policy", "Leave of Absence Policy"]
        elif "remote" in query_lower or "work from home" in query_lower:
            answer = (
                f"Our remote work policy: {'Fully remote' if self.config.get('remote_first', False) else 'Hybrid (3 days in office)'} arrangement. "
                "Employees are expected to be available during core hours (10am-2pm local time). "
                "All remote work must comply with our security and data protection policies."
            )
            related = ["Remote Work Policy", "Digital Security Policy"]
        elif "salary" in query_lower or "compensation" in query_lower or "pay" in query_lower:
            answer = (
                "Compensation is determined by role, level, experience, and market benchmarks. "
                "Salaries are reviewed annually during performance reviews. "
                "We offer competitive compensation including base salary, performance bonuses, "
                "and equity options for eligible positions."
            )
            related = ["Compensation Philosophy", "Bonus Structure", "Equity Plan"]
        else:
            answer = (
                "Thank you for your question. I'd recommend reviewing the employee handbook "
                "or reaching out to your HR business partner for specific policy guidance. "
                "I can help with general information about leave, benefits, remote work, "
                "compensation, and other common HR topics."
            )
            related = ["Employee Handbook", "HR Contact Directory"]

        return answer, related

    # ------------------------------------------------------------------
    # Agent prompts
    # ------------------------------------------------------------------

    def _build_recruiter_prompt(self) -> str:
        """Build the system prompt for the recruiter role."""
        industry = self.config.get("industry", "technology")
        return (
            f"You are a senior technical recruiter specializing in the {industry} industry. "
            f"Write compelling job descriptions, screen resumes effectively, "
            f"and identify top talent. Focus on both hard skills and cultural fit. "
            f"Use structured interview frameworks and provide clear candidate "
            f"evaluations. Aim for diverse and inclusive hiring practices."
        )

    def _build_onboarding_specialist_prompt(self) -> str:
        """Build the system prompt for the onboarding specialist role."""
        return (
            "You are an employee onboarding specialist. Create comprehensive "
            "onboarding plans that set new hires up for success. Include "
            "pre-day-1 preparation, first-week immersion, role-specific training, "
            "and 30/60/90-day milestones. Ensure a great first impression "
            "and accelerate time-to-productivity."
        )

    def _build_performance_analyst_prompt(self) -> str:
        """Build the system prompt for the performance analyst role."""
        return (
            "You are a performance management specialist. Conduct fair, "
            "balanced performance reviews based on objective data and "
            "qualitative feedback. Identify strengths to leverage and "
            "areas for growth. Create SMART goals and development plans. "
            "Focus on constructive feedback that drives improvement."
        )

    def _build_policy_advisor_prompt(self) -> str:
        """Build the system prompt for the policy advisor role."""
        region = self.config.get("compliance_region", "us")
        return (
            f"You are an HR policy advisor with expertise in {region} employment law. "
            f"Provide accurate, clear guidance on HR policies and procedures. "
            f"Always include appropriate disclaimers and recommend consulting "
            f"with HR professionals for specific situations. Stay current "
            f"with regulatory changes and compliance requirements."
        )

    # ------------------------------------------------------------------
    # Metrics
    # ------------------------------------------------------------------

    def _update_hr_metrics(self) -> None:
        """Update HR-specific metrics."""
        # Set baseline values
        self.hr_metrics.time_to_hire_days = 21.0
        self.hr_metrics.retention_rate = 0.88
        self.hr_metrics.employee_satisfaction = 4.0
        self.hr_metrics.offer_acceptance_rate = 0.85
        self.hr_metrics.onboarding_completion_rate = 0.92

    def get_hr_metrics(self) -> Dict[str, Any]:
        """Get the current HR-specific metrics.

        Returns:
            Dictionary with HR metrics including time-to-hire, retention rate,
            employee satisfaction, and performance ratings.
        """
        return self.hr_metrics.to_dict()
