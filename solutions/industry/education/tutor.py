"""
AI Tutor Solution Template.

A comprehensive AI-powered tutoring solution for educational
institutions, e-learning platforms, and corporate training.
Includes adaptive learning, multi-agent roles for subject
expertise, pedagogy design, progress tracking, and assessment.

Typical workflow:
    assessment -> lesson plan -> interactive tutoring
    -> quizzes -> progress report

Adaptive learning features:
    - Difficulty adjustment based on performance
    - Personalized learning paths
    - Knowledge gap detection
    - Paced progression
"""

from typing import Optional, Dict, Any, List
from enum import Enum
from dataclasses import dataclass, field

from solutions.templates.base_solution import (
    BaseSolution,
    SolutionMetadata,
    SolutionMetrics,
)


class DifficultyLevel(str, Enum):
    """Difficulty levels for learning content."""
    BEGINNER = "beginner"
    ELEMENTARY = "elementary"
    INTERMEDIATE = "intermediate"
    ADVANCED = "advanced"
    EXPERT = "expert"


class LearningStyle(str, Enum):
    """Preferred learning styles."""
    VISUAL = "visual"
    AUDITORY = "auditory"
    READING_WRITING = "reading_writing"
    KINESTHETIC = "kinesthetic"


class MasteryLevel(str, Enum):
    """Mastery levels for topics/skills."""
    NOT_ATTEMPTED = "not_attempted"
    INTRODUCED = "introduced"
    PRACTICING = "practicing"
    PROFICIENT = "proficient"
    MASTERED = "mastered"


class EducationRole(str, Enum):
    """Agent roles in the AI tutoring solution."""
    SUBJECT_EXPERT = "subject_expert"
    PEDAGOGY_DESIGNER = "pedagogy_designer"
    PROGRESS_TRACKER = "progress_tracker"
    ASSESSMENT_CREATOR = "assessment_creator"


@dataclass
class StudentProfile:
    """Represents a learner's profile and progress.

    Attributes:
        student_id: Unique student identifier.
        name: Student name.
        age: Student age.
        grade_level: Current grade/educational level.
        subject: Subject being studied.
        learning_style: Preferred learning style.
        current_level: Current difficulty level in the subject.
        mastery_map: Topic mastery levels.
        learning_goals: Student's learning objectives.
        strengths: Identified strengths.
        weaknesses: Areas needing improvement.
        total_study_minutes: Total study time logged.
        sessions_completed: Number of tutoring sessions completed.
    """
    student_id: str
    name: str = ""
    age: int = 15
    grade_level: str = ""
    subject: str = "mathematics"
    learning_style: LearningStyle = LearningStyle.READING_WRITING
    current_level: DifficultyLevel = DifficultyLevel.INTERMEDIATE
    mastery_map: Dict[str, MasteryLevel] = field(default_factory=dict)
    learning_goals: List[str] = field(default_factory=list)
    strengths: List[str] = field(default_factory=list)
    weaknesses: List[str] = field(default_factory=list)
    total_study_minutes: int = 0
    sessions_completed: int = 0


@dataclass
class QuizQuestion:
    """Represents a quiz or assessment question.

    Attributes:
        id: Unique question identifier.
        question: The question text.
        question_type: Type of question (multiple_choice, short_answer, etc.).
        options: Answer options for multiple choice.
        correct_answer: The correct answer.
        difficulty: Difficulty level of the question.
        topic: Topic/concept being tested.
        points: Point value of the question.
        hint: Optional hint for the student.
        explanation: Explanation of the correct answer.
    """
    id: str
    question: str
    question_type: str = "multiple_choice"
    options: List[str] = field(default_factory=list)
    correct_answer: str = ""
    difficulty: DifficultyLevel = DifficultyLevel.INTERMEDIATE
    topic: str = ""
    points: int = 1
    hint: str = ""
    explanation: str = ""


@dataclass
class LessonPlan:
    """Represents a structured lesson plan.

    Attributes:
        id: Unique lesson plan identifier.
        student_id: Associated student ID.
        subject: Subject of the lesson.
        topic: Main topic of the lesson.
        subtopics: Sub-topics covered in the lesson.
        difficulty: Difficulty level of the lesson.
        duration_minutes: Estimated lesson duration.
        objectives: Learning objectives for the lesson.
        prerequisites: Prerequisite knowledge/skills.
        content_sections: Ordered list of content sections.
        activities: Interactive activities in the lesson.
        resources: Additional learning resources.
        assessment_method: How learning will be assessed.
    """
    id: str
    student_id: str
    subject: str = ""
    topic: str = ""
    subtopics: List[str] = field(default_factory=list)
    difficulty: DifficultyLevel = DifficultyLevel.INTERMEDIATE
    duration_minutes: int = 45
    objectives: List[str] = field(default_factory=list)
    prerequisites: List[str] = field(default_factory=list)
    content_sections: List[Dict[str, Any]] = field(default_factory=list)
    activities: List[str] = field(default_factory=list)
    resources: List[str] = field(default_factory=list)
    assessment_method: str = "quiz"


@dataclass
class ProgressReport:
    """Represents a student's progress report.

    Attributes:
        student_id: Student identifier.
        subject: Subject of the report.
        period: Time period covered by the report.
        topics_covered: List of topics covered.
        mastery_summary: Summary of mastery levels by topic.
        strengths: Current strengths.
        areas_to_improve: Areas needing improvement.
        learning_gain: Measured learning gain (pre/post comparison).
        engagement_score: Student engagement score (0-100).
        recommendations: Recommendations for next steps.
        next_lesson: Suggested next lesson topic.
        generated_at: ISO 8601 generation timestamp.
    """
    student_id: str
    subject: str
    period: str = "current_session"
    topics_covered: List[str] = field(default_factory=list)
    mastery_summary: Dict[str, str] = field(default_factory=dict)
    strengths: List[str] = field(default_factory=list)
    areas_to_improve: List[str] = field(default_factory=list)
    learning_gain: float = 0.0
    engagement_score: float = 0.0
    recommendations: List[str] = field(default_factory=list)
    next_lesson: str = ""
    generated_at: Optional[str] = None


@dataclass
class EducationMetrics(SolutionMetrics):
    """Extended metrics for the AI tutoring solution.

    Attributes:
        avg_learning_gain: Average learning gain across students.
        engagement_rate: Average student engagement rate (0-1).
        completion_rate: Average course/session completion rate.
        students_active: Number of active students.
        sessions_total: Total tutoring sessions delivered.
        questions_answered: Total questions answered.
        avg_session_duration_min: Average session duration in minutes.
        mastery_rate: Rate of topics achieving mastery.
    """
    avg_learning_gain: float = 0.0
    engagement_rate: float = 0.0
    completion_rate: float = 0.0
    students_active: int = 0
    sessions_total: int = 0
    questions_answered: int = 0
    avg_session_duration_min: float = 0.0
    mastery_rate: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        """Convert education metrics to a dictionary."""
        base = super().to_dict()
        base.update({
            "avg_learning_gain": self.avg_learning_gain,
            "engagement_rate": self.engagement_rate,
            "completion_rate": self.completion_rate,
            "students_active": self.students_active,
            "sessions_total": self.sessions_total,
            "questions_answered": self.questions_answered,
            "avg_session_duration_min": self.avg_session_duration_min,
            "mastery_rate": self.mastery_rate,
        })
        return base


class AITutorSolution(BaseSolution):
    """AI Tutor solution template.

    Provides a complete, pre-configured AI tutoring solution
    with adaptive learning, multi-agent collaboration for
    subject expertise, pedagogy design, progress tracking,
    and assessment creation.

    Adaptive learning features:
        - Dynamic difficulty adjustment based on real-time performance
        - Personalized learning paths tailored to individual needs
        - Knowledge gap detection and targeted remediation
        - Self-paced progression with scaffolding support
        - Multiple learning style accommodations

    Pre-configured agent roles:
        - Subject Expert: Provides deep subject matter knowledge
        - Pedagogy Designer: Designs effective learning experiences
        - Progress Tracker: Monitors and analyzes student progress
        - Assessment Creator: Generates quizzes and evaluations

    Workflow:
        1. Assessment - Evaluate current knowledge and skill level
        2. Lesson Plan - Create personalized learning plan
        3. Interactive Tutoring - Deliver adaptive instruction
        4. Quizzes - Test understanding with adaptive questions
        5. Progress Report - Generate comprehensive progress summary

    Configuration options:
        - subject: Subject area to tutor
        - adaptive_difficulty: Whether to enable adaptive difficulty
        - learning_pace: Pace of learning (slow, moderate, fast)
        - assessment_frequency: How often to assess progress
        - max_session_minutes: Maximum session duration
    """

    def _build_metadata(self) -> SolutionMetadata:
        """Build metadata for the AI tutor solution."""
        return SolutionMetadata(
            name="ai-tutor",
            version="1.0.0",
            industry="education",
            description=(
                "AI-powered tutoring solution with adaptive learning, "
                "personalized lesson plans, interactive instruction, "
                "and comprehensive progress tracking."
            ),
            author="LiuHao AI OS",
            tags=["education", "tutoring", "adaptive-learning", "assessment", "edtech"],
            min_liuhao_version="1.0.0",
        )

    def _build_config_schema(self) -> None:
        """Define configuration fields for the AI tutoring solution."""
        self.config_schema.add_field(
            name="subject",
            field_type="string",
            required=True,
            description="Subject area for tutoring",
            valid_values=[
                "mathematics", "physics", "chemistry", "biology",
                "computer_science", "english", "history", "geography",
                "economics", "psychology", "language_learning",
                "programming", "data_science", "business", "general",
            ],
        )
        self.config_schema.add_field(
            name="adaptive_difficulty",
            field_type="bool",
            required=False,
            default=True,
            description="Whether to enable adaptive difficulty adjustment",
        )
        self.config_schema.add_field(
            name="learning_pace",
            field_type="string",
            required=False,
            default="moderate",
            description="Pace of learning progression",
            valid_values=["slow", "moderate", "fast", "self_paced"],
        )
        self.config_schema.add_field(
            name="assessment_frequency",
            field_type="string",
            required=False,
            default="per_lesson",
            description="How frequently to assess student progress",
            valid_values=["per_lesson", "per_topic", "per_unit", "weekly", "monthly"],
        )
        self.config_schema.add_field(
            name="max_session_minutes",
            field_type="int",
            required=False,
            default=60,
            description="Maximum tutoring session duration in minutes",
        )
        self.config_schema.add_field(
            name="default_difficulty",
            field_type="string",
            required=False,
            default="intermediate",
            description="Default starting difficulty level",
            valid_values=["beginner", "elementary", "intermediate", "advanced", "expert"],
        )
        self.config_schema.add_field(
            name="question_count_per_quiz",
            field_type="int",
            required=False,
            default=10,
            description="Number of questions per quiz",
        )
        self.config_schema.add_field(
            name="enable_hints",
            field_type="bool",
            required=False,
            default=True,
            description="Whether to provide hints for quiz questions",
        )

    def _on_initialize(self) -> None:
        """Initialize AI tutor solution state."""
        self._agents: Dict[str, Dict[str, Any]] = {}
        self._students: Dict[str, StudentProfile] = {}
        self._lesson_plans: Dict[str, LessonPlan] = {}
        self._progress_reports: Dict[str, ProgressReport] = {}
        self._session_count = 0
        self._student_count = 0

        # Initialize education-specific metrics
        self.education_metrics = EducationMetrics()

    def _on_deploy(self) -> None:
        """Deploy AI tutor agents and workflows."""
        agent_configs = [
            (EducationRole.SUBJECT_EXPERT, {
                "name": "Subject Expert",
                "description": "Provides deep subject matter knowledge",
                "system_prompt": self._build_subject_expert_prompt(),
                "tools": ["knowledge_base", "explanation_generator", "example_creator"],
            }),
            (EducationRole.PEDAGOGY_DESIGNER, {
                "name": "Pedagogy Designer",
                "description": "Designs effective learning experiences",
                "system_prompt": self._build_pedagogy_designer_prompt(),
                "tools": ["lesson_planner", "activity_designer", "scaffolding_engine"],
            }),
            (EducationRole.PROGRESS_TRACKER, {
                "name": "Progress Tracker",
                "description": "Monitors and analyzes student progress",
                "system_prompt": self._build_progress_tracker_prompt(),
                "tools": ["analytics", "gap_analysis", "report_generator"],
            }),
            (EducationRole.ASSESSMENT_CREATOR, {
                "name": "Assessment Creator",
                "description": "Generates quizzes and evaluations",
                "system_prompt": self._build_assessment_creator_prompt(),
                "tools": ["question_generator", "quiz_builder", "rubric_creator"],
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
        """Process a tutoring session through the full workflow.

        Args:
            input_data: Dictionary containing student data and session
                parameters with at least ``student_id`` and ``topic``.

        Returns:
            Dictionary with tutoring session results including lesson
            plan, quiz results, and progress report.
        """
        # Step 1: Initial assessment
        student = self._get_or_create_student(input_data)
        assessment_results = self._assess_student(student, input_data)

        # Step 2: Create lesson plan (adaptive)
        lesson_plan = self._create_lesson_plan(student, assessment_results)

        # Step 3: Interactive tutoring (simulated)
        tutoring_results = self._deliver_tutoring(student, lesson_plan)

        # Step 4: Quiz / assessment
        quiz_results = self._administer_quiz(student, lesson_plan)

        # Step 5: Adaptive adjustment & progress report
        progress_report = self._generate_progress_report(
            student, lesson_plan, assessment_results, quiz_results
        )

        # Apply adaptive difficulty adjustment
        if self.config.get("adaptive_difficulty", True):
            self._adjust_difficulty(student, quiz_results)

        # Update metrics
        self._session_count += 1
        student.sessions_completed += 1
        self.education_metrics.sessions_total += 1
        self._update_education_metrics()

        return {
            "student_id": student.student_id,
            "student": {
                "name": student.name,
                "subject": student.subject,
                "current_level": student.current_level.value,
                "learning_style": student.learning_style.value,
            },
            "assessment": {
                "initial_score": assessment_results.get("score", 0),
                "knowledge_gaps": assessment_results.get("gaps", []),
                "strengths": assessment_results.get("strengths", []),
            },
            "lesson_plan": {
                "id": lesson_plan.id,
                "topic": lesson_plan.topic,
                "subtopics": lesson_plan.subtopics,
                "difficulty": lesson_plan.difficulty.value,
                "duration_minutes": lesson_plan.duration_minutes,
                "objectives": lesson_plan.objectives,
            },
            "tutoring": tutoring_results,
            "quiz": {
                "score": quiz_results.get("score", 0),
                "total_questions": quiz_results.get("total_questions", 0),
                "correct_answers": quiz_results.get("correct_answers", 0),
                "topics_covered": quiz_results.get("topics", []),
            },
            "progress": {
                "learning_gain": progress_report.learning_gain,
                "engagement_score": progress_report.engagement_score,
                "mastery_updates": progress_report.mastery_summary,
                "recommendations": progress_report.recommendations,
                "next_lesson": progress_report.next_lesson,
            },
            "adaptive_learning": {
                "adaptive_difficulty_enabled": self.config.get("adaptive_difficulty", True),
                "difficulty_adjusted": self.config.get("adaptive_difficulty", False),
                "new_level": student.current_level.value,
            },
        }

    def _on_teardown(self) -> None:
        """Clean up AI tutor solution resources."""
        self._agents.clear()
        self._students.clear()
        self._lesson_plans.clear()
        self._progress_reports.clear()
        self._session_count = 0
        self._student_count = 0

    # ------------------------------------------------------------------
    # Workflow steps
    # ------------------------------------------------------------------

    def _get_or_create_student(self, data: Dict[str, Any]) -> StudentProfile:
        """Get existing student or create a new profile."""
        student_id = data.get("student_id")
        if student_id and student_id in self._students:
            return self._students[student_id]

        self._student_count += 1
        student_id = student_id or f"student-{self._student_count}"

        student = StudentProfile(
            student_id=student_id,
            name=data.get("name", ""),
            age=data.get("age", 15),
            grade_level=data.get("grade_level", ""),
            subject=data.get("subject", self.config.get("subject", "mathematics")),
            learning_style=LearningStyle(
                data.get("learning_style", LearningStyle.READING_WRITING.value)
            ),
            current_level=DifficultyLevel(
                self.config.get("default_difficulty", DifficultyLevel.INTERMEDIATE.value)
            ),
            learning_goals=data.get("learning_goals", []),
        )
        self._students[student_id] = student
        return student

    def _assess_student(
        self, student: StudentProfile, input_data: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Step 1: Assess current knowledge and skill level."""
        topic = input_data.get("topic", "general")
        prior_score = input_data.get("pre_assessment_score")

        if prior_score is not None:
            score = float(prior_score)
        else:
            # Simulate assessment based on student's current level
            level_scores = {
                DifficultyLevel.BEGINNER: 30.0,
                DifficultyLevel.ELEMENTARY: 45.0,
                DifficultyLevel.INTERMEDIATE: 65.0,
                DifficultyLevel.ADVANCED: 80.0,
                DifficultyLevel.EXPERT: 92.0,
            }
            score = level_scores.get(student.current_level, 65.0)
            # Add some variance
            score = max(0.0, min(100.0, score + (hash(student.student_id) % 20 - 10)))

        # Identify knowledge gaps and strengths
        gaps = [
            f"{topic} fundamentals need reinforcement" if score < 70 else "",
            "Advanced problem-solving skills needed" if score < 85 else "",
        ]
        gaps = [g for g in gaps if g]

        strengths = [
            f"{topic} basic concepts understood" if score >= 50 else "",
            "Strong analytical skills" if score >= 75 else "",
        ]
        strengths = [s for s in strengths if s]

        return {
            "score": round(score, 1),
            "level": student.current_level.value,
            "gaps": gaps,
            "strengths": strengths,
            "topics_assessed": [topic],
        }

    def _create_lesson_plan(
        self, student: StudentProfile, assessment: Dict[str, Any]
    ) -> LessonPlan:
        """Step 2: Create personalized lesson plan based on assessment."""
        plan_id = f"lesson-{self._session_count + 1}"

        score = assessment.get("score", 65.0)
        topic = assessment.get("topics_assessed", ["general"])[0]

        # Adjust difficulty based on assessment
        if self.config.get("adaptive_difficulty", True):
            if score < 40:
                difficulty = DifficultyLevel.BEGINNER
            elif score < 60:
                difficulty = DifficultyLevel.ELEMENTARY
            elif score < 80:
                difficulty = DifficultyLevel.INTERMEDIATE
            elif score < 95:
                difficulty = DifficultyLevel.ADVANCED
            else:
                difficulty = DifficultyLevel.EXPERT
        else:
            difficulty = student.current_level

        # Adjust duration based on pace
        pace = self.config.get("learning_pace", "moderate")
        duration = {"slow": 30, "moderate": 45, "fast": 60, "self_paced": 45}.get(pace, 45)

        # Generate subtopics
        subtopics = self._generate_subtopics(topic, difficulty)

        # Learning objectives
        objectives = [
            f"Understand key concepts of {topic}",
            f"Apply {topic} knowledge to solve problems",
            f"Demonstrate mastery of core {topic} skills",
        ]

        # Content sections
        content_sections = [
            {"title": f"Introduction to {topic}", "type": "concept", "duration": duration * 0.15},
            {"title": f"Core Concepts of {topic}", "type": "explanation", "duration": duration * 0.35},
            {"title": f"Examples and Practice", "type": "practice", "duration": duration * 0.30},
            {"title": f"Summary and Review", "type": "review", "duration": duration * 0.20},
        ]

        activities = [
            "Interactive examples with step-by-step explanations",
            "Guided practice problems with immediate feedback",
            f"Real-world applications of {topic}",
        ]

        lesson_plan = LessonPlan(
            id=plan_id,
            student_id=student.student_id,
            subject=student.subject,
            topic=topic,
            subtopics=subtopics,
            difficulty=difficulty,
            duration_minutes=duration,
            objectives=objectives,
            prerequisites=assessment.get("gaps", [])[:2],
            content_sections=content_sections,
            activities=activities,
            resources=[f"{topic} reference materials", "Practice exercises"],
        )
        self._lesson_plans[plan_id] = lesson_plan
        return lesson_plan

    def _generate_subtopics(self, topic: str, difficulty: DifficultyLevel) -> List[str]:
        """Generate subtopics based on main topic and difficulty."""
        base_subtopics = {
            "general": ["Fundamentals", "Applications", "Advanced concepts"],
            "algebra": ["Variables", "Equations", "Functions", "Inequalities"],
            "geometry": ["Shapes", "Angles", "Proofs", "Coordinates"],
            "calculus": ["Limits", "Derivatives", "Integrals", "Applications"],
        }

        subtopics = base_subtopics.get(topic.lower(), [
            f"{topic} - Basic Principles",
            f"{topic} - Intermediate Concepts",
            f"{topic} - Advanced Applications",
        ])

        # Filter by difficulty
        level_index = {
            DifficultyLevel.BEGINNER: 1,
            DifficultyLevel.ELEMENTARY: 2,
            DifficultyLevel.INTERMEDIATE: 3,
            DifficultyLevel.ADVANCED: 4,
            DifficultyLevel.EXPERT: 5,
        }.get(difficulty, 3)

        return subtopics[:max(1, min(len(subtopics), level_index))]

    def _deliver_tutoring(
        self, student: StudentProfile, lesson_plan: LessonPlan
    ) -> Dict[str, Any]:
        """Step 3: Deliver interactive tutoring content."""
        # Simulated tutoring delivery
        sections_completed = len(lesson_plan.content_sections)
        activities_completed = len(lesson_plan.activities)

        # Simulate engagement based on learning style match
        engagement_base = 70.0
        style_boost = 10.0  # Learning style accommodation adds engagement
        engagement = min(100.0, engagement_base + style_boost)

        return {
            "sections_completed": sections_completed,
            "activities_completed": activities_completed,
            "engagement_score": round(engagement, 1),
            "interactions_count": sections_completed * 3 + activities_completed * 2,
            "time_spent_minutes": lesson_plan.duration_minutes,
            "sections": [s["title"] for s in lesson_plan.content_sections],
        }

    def _administer_quiz(
        self, student: StudentProfile, lesson_plan: LessonPlan
    ) -> Dict[str, Any]:
        """Step 4: Administer quiz and assess understanding."""
        num_questions = self.config.get("question_count_per_quiz", 10)

        # Generate quiz questions
        questions = self._generate_quiz_questions(lesson_plan, num_questions)

        # Simulate quiz results based on difficulty and student level
        difficulty_factor = {
            DifficultyLevel.BEGINNER: 0.9,
            DifficultyLevel.ELEMENTARY: 0.85,
            DifficultyLevel.INTERMEDIATE: 0.75,
            DifficultyLevel.ADVANCED: 0.65,
            DifficultyLevel.EXPERT: 0.55,
        }.get(lesson_plan.difficulty, 0.75)

        level_factor = {
            DifficultyLevel.BEGINNER: 0.6,
            DifficultyLevel.ELEMENTARY: 0.7,
            DifficultyLevel.INTERMEDIATE: 0.8,
            DifficultyLevel.ADVANCED: 0.9,
            DifficultyLevel.EXPERT: 0.95,
        }.get(student.current_level, 0.75)

        # Performance is a combination of difficulty and student level
        correct_rate = min(0.98, 0.5 + (level_factor - difficulty_factor + 0.5) * 0.5)
        correct_answers = int(num_questions * correct_rate)
        score = round(correct_answers / num_questions * 100, 1)

        self.education_metrics.questions_answered += num_questions

        return {
            "total_questions": num_questions,
            "correct_answers": correct_answers,
            "score": score,
            "questions": [q.id for q in questions],
            "topics": lesson_plan.subtopics,
            "hints_used": 2 if self.config.get("enable_hints", True) else 0,
        }

    def _generate_quiz_questions(
        self, lesson_plan: LessonPlan, count: int
    ) -> List[QuizQuestion]:
        """Generate quiz questions for the lesson."""
        questions = []
        for i in range(count):
            topic = lesson_plan.subtopics[i % len(lesson_plan.subtopics)] if lesson_plan.subtopics else lesson_plan.topic
            questions.append(QuizQuestion(
                id=f"q{self._session_count}-{i+1}",
                question=f"Question about {topic} (concept {i+1})",
                question_type="multiple_choice",
                options=["Option A", "Option B", "Option C", "Option D"],
                correct_answer="Option A",
                difficulty=lesson_plan.difficulty,
                topic=topic,
                points=1,
                hint="Consider the core principle of this concept.",
                explanation="This answer is correct because it aligns with the fundamental principle.",
            ))
        return questions

    def _generate_progress_report(
        self,
        student: StudentProfile,
        lesson_plan: LessonPlan,
        assessment: Dict[str, Any],
        quiz_results: Dict[str, Any],
    ) -> ProgressReport:
        """Step 5: Generate progress report with learning metrics."""
        pre_score = assessment.get("score", 0)
        post_score = quiz_results.get("score", 0)
        learning_gain = max(0.0, post_score - pre_score)

        # Update mastery map
        for topic in lesson_plan.subtopics:
            current_mastery = student.mastery_map.get(topic, MasteryLevel.NOT_ATTEMPTED)
            if post_score >= 90:
                new_mastery = MasteryLevel.MASTERED
            elif post_score >= 75:
                new_mastery = MasteryLevel.PROFICIENT
            elif post_score >= 50:
                new_mastery = MasteryLevel.PRACTICING
            else:
                new_mastery = MasteryLevel.INTRODUCED
            student.mastery_map[topic] = new_mastery

        # Generate recommendations
        recommendations = []
        if post_score >= 90:
            recommendations.append("Excellent work! Ready to advance to more challenging content.")
            next_topic = f"Advanced {lesson_plan.topic}"
        elif post_score >= 70:
            recommendations.append("Good progress. Continue practicing to strengthen understanding.")
            next_topic = f"Next level {lesson_plan.topic}"
        elif post_score >= 50:
            recommendations.append("Making progress. Review and additional practice recommended.")
            next_topic = f"Review: {lesson_plan.topic}"
        else:
            recommendations.append("Needs more foundational work. Consider revisiting prerequisites.")
            next_topic = f"Foundations of {lesson_plan.topic}"

        report = ProgressReport(
            student_id=student.student_id,
            subject=student.subject,
            period=f"session-{self._session_count + 1}",
            topics_covered=lesson_plan.subtopics,
            mastery_summary={t: m.value for t, m in student.mastery_map.items()},
            strengths=assessment.get("strengths", []),
            areas_to_improve=assessment.get("gaps", []),
            learning_gain=round(learning_gain, 1),
            engagement_score=quiz_results.get("score", 70),
            recommendations=recommendations,
            next_lesson=next_topic,
        )
        self._progress_reports[student.student_id] = report
        return report

    def _adjust_difficulty(
        self, student: StudentProfile, quiz_results: Dict[str, Any]
    ) -> None:
        """Adaptively adjust student's difficulty level based on performance."""
        score = quiz_results.get("score", 50)
        levels = list(DifficultyLevel)
        current_idx = levels.index(student.current_level)

        if score >= 90 and current_idx < len(levels) - 1:
            # Promote to next level
            student.current_level = levels[current_idx + 1]
        elif score < 40 and current_idx > 0:
            # Demote to previous level
            student.current_level = levels[current_idx - 1]

    # ------------------------------------------------------------------
    # Agent prompts
    # ------------------------------------------------------------------

    def _build_subject_expert_prompt(self) -> str:
        """Build the system prompt for the subject expert role."""
        subject = self.config.get("subject", "general")
        return (
            f"You are a subject matter expert in {subject}. "
            f"Provide clear, accurate explanations of concepts at an "
            f"appropriate level for the student. Use examples, analogies, "
            f"and step-by-step reasoning to make complex ideas accessible. "
            f"Always verify information for correctness and cite sources "
            f"when appropriate."
        )

    def _build_pedagogy_designer_prompt(self) -> str:
        """Build the system prompt for the pedagogy designer role."""
        return (
            "You are an instructional designer and pedagogy expert. "
            "Design effective learning experiences that accommodate "
            "different learning styles and paces. Use scaffolding "
            "techniques, spaced repetition, and active learning "
            "strategies. Ensure lessons build on prior knowledge "
            "and provide appropriate challenge levels."
        )

    def _build_progress_tracker_prompt(self) -> str:
        """Build the system prompt for the progress tracker role."""
        return (
            "You are a learning analytics specialist. Track and analyze "
            "student progress across topics and skills. Identify knowledge "
            "gaps, learning patterns, and areas for improvement. Generate "
            "actionable insights and clear progress reports that help "
            "students understand their growth and next steps."
        )

    def _build_assessment_creator_prompt(self) -> str:
        """Build the system prompt for the assessment creator role."""
        return (
            "You are an assessment design specialist. Create fair, "
            "effective quizzes and evaluations that accurately measure "
            "student understanding. Use a variety of question types and "
            "difficulty levels. Provide clear explanations for correct "
            "answers and create helpful hints that guide without giving "
            "away the answer. Ensure assessments align with learning objectives."
        )

    # ------------------------------------------------------------------
    # Metrics
    # ------------------------------------------------------------------

    def _update_education_metrics(self) -> None:
        """Update education-specific metrics."""
        if self._students:
            self.education_metrics.students_active = len(self._students)

            # Calculate average learning gain from reports
            gains = [r.learning_gain for r in self._progress_reports.values()]
            if gains:
                self.education_metrics.avg_learning_gain = sum(gains) / len(gains)

            # Calculate engagement
            engagements = [r.engagement_score for r in self._progress_reports.values()]
            if engagements:
                self.education_metrics.engagement_rate = sum(engagements) / len(engagements) / 100.0

            # Calculate mastery rate
            all_masteries = []
            for student in self._students.values():
                all_masteries.extend(student.mastery_map.values())
            if all_masteries:
                mastered = sum(
                    1 for m in all_masteries
                    if m in (MasteryLevel.MASTERED, MasteryLevel.PROFICIENT)
                )
                self.education_metrics.mastery_rate = mastered / len(all_masteries)

    def get_education_metrics(self) -> Dict[str, Any]:
        """Get the current education-specific metrics.

        Returns:
            Dictionary with education metrics including learning gain,
            engagement rate, completion rate, and mastery rate.
        """
        return self.education_metrics.to_dict()
