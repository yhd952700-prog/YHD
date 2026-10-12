"""Human Evaluation Interface for the evaluation framework.

Provides human evaluation task creation, annotation assignment,
inter-annotator agreement calculation, quality control, and
adjudication workflows.
"""

from __future__ import annotations

import math
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum, auto
from typing import Any, Callable, Dict, List, Optional, Set, Tuple
from uuid import uuid4


class AnnotationStatus(str, Enum):
    """Annotation task status."""

    PENDING = "pending"
    ASSIGNED = "assigned"
    IN_PROGRESS = "in_progress"
    SUBMITTED = "submitted"
    REVIEWED = "reviewed"
    ADJUDICATED = "adjudicated"
    REJECTED = "rejected"


class AnnotationPriority(str, Enum):
    """Annotation priority levels."""

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


@dataclass
class AnnotationGuidelines:
    """Annotation guidelines document.

    Attributes:
        guideline_id: Unique guideline identifier
        title: Guideline title
        content: Full guideline text/content
        criteria: List of evaluation criteria
        examples: Annotated examples for reference
        version: Guideline version
        created_at: Creation timestamp
        updated_at: Last update timestamp
    """

    guideline_id: str = field(default_factory=lambda: str(uuid4()))
    title: str = ""
    content: str = ""
    criteria: List[str] = field(default_factory=list)
    examples: List[Dict[str, Any]] = field(default_factory=list)
    version: str = "1.0.0"
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    updated_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


@dataclass
class AnnotationTask:
    """A single human annotation task.

    Attributes:
        task_id: Unique task identifier
        evaluation_id: Parent evaluation ID
        item_id: Item being annotated
        item_data: Data for the item to annotate
        criteria: List of evaluation criteria
        assigned_to: Annotator assigned to this task
        status: Current status
        priority: Task priority
        annotations: List of submitted annotations
        deadline: Deadline for completion
        created_at: Creation timestamp
        submitted_at: Submission timestamp
        metadata: Additional metadata
    """

    task_id: str = field(default_factory=lambda: str(uuid4()))
    evaluation_id: str = ""
    item_id: str = ""
    item_data: Dict[str, Any] = field(default_factory=dict)
    criteria: List[str] = field(default_factory=list)
    assigned_to: Optional[str] = None
    status: AnnotationStatus = AnnotationStatus.PENDING
    priority: AnnotationPriority = AnnotationPriority.MEDIUM
    annotations: List[Dict[str, Any]] = field(default_factory=list)
    deadline: Optional[str] = None
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    submitted_at: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)

    @property
    def is_complete(self) -> bool:
        """Whether all required annotations are submitted."""
        return self.status in (
            AnnotationStatus.SUBMITTED,
            AnnotationStatus.REVIEWED,
            AnnotationStatus.ADJUDICATED,
        )


@dataclass
class Annotation:
    """A single annotation submission.

    Attributes:
        annotation_id: Unique annotation identifier
        task_id: Task this annotation belongs to
        annotator_id: Annotator who submitted
        ratings: Dictionary of criterion -> rating
        comments: Free-text comments
        flags: List of flagged issues
        time_spent_seconds: Time spent on annotation
        submitted_at: Submission timestamp
        is_gold: Whether this is a gold standard annotation
    """

    annotation_id: str = field(default_factory=lambda: str(uuid4()))
    task_id: str = ""
    annotator_id: str = ""
    ratings: Dict[str, float] = field(default_factory=dict)
    comments: str = ""
    flags: List[str] = field(default_factory=list)
    time_spent_seconds: float = 0.0
    submitted_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    is_gold: bool = False


@dataclass
class AnnotatorProfile:
    """Profile of a human annotator.

    Attributes:
        annotator_id: Unique annotator identifier
        name: Annotator name
        email: Annotator email
        expertise: List of areas of expertise
        tasks_completed: Number of tasks completed
        tasks_assigned: Number of tasks assigned
        avg_time_per_task: Average time per task in seconds
        agreement_score: Average agreement with other annotators
        quality_score: Overall quality score
        status: Annotator status (active, inactive, etc.)
        joined_at: Join timestamp
    """

    annotator_id: str = field(default_factory=lambda: str(uuid4()))
    name: str = ""
    email: str = ""
    expertise: List[str] = field(default_factory=list)
    tasks_completed: int = 0
    tasks_assigned: int = 0
    avg_time_per_task: float = 0.0
    agreement_score: float = 0.0
    quality_score: float = 0.0
    status: str = "active"
    joined_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


# ======================================================================
# Inter-Annotator Agreement
# ======================================================================


def cohens_kappa(
    annotations_a: List[Any],
    annotations_b: List[Any],
) -> float:
    """Compute Cohen's Kappa for two annotators.

    Measures inter-annotator agreement for categorical data.

    Args:
        annotations_a: Annotations from annotator A
        annotations_b: Annotations from annotator B

    Returns:
        Cohen's Kappa score (-1 to 1)
    """
    if len(annotations_a) != len(annotations_b) or len(annotations_a) == 0:
        return 0.0

    n = len(annotations_a)

    # Observed agreement
    agreements = sum(1 for a, b in zip(annotations_a, annotations_b) if a == b)
    po = agreements / n

    # Expected agreement by chance
    categories_a = Counter(annotations_a)
    categories_b = Counter(annotations_b)

    all_categories = set(categories_a.keys()) | set(categories_b.keys())
    pe = 0.0
    for cat in all_categories:
        pa = categories_a.get(cat, 0) / n
        pb = categories_b.get(cat, 0) / n
        pe += pa * pb

    if pe == 1.0:
        return 1.0

    kappa = (po - pe) / (1 - pe) if (1 - pe) != 0 else 0.0
    return kappa


def fleiss_kappa(
    annotations_matrix: List[List[Any]],
) -> float:
    """Compute Fleiss' Kappa for multiple annotators.

    Measures inter-annotator agreement when there are more than two
    annotators and each item may be annotated by different subsets.

    Args:
        annotations_matrix: Matrix where rows are items and columns are
                           annotator assignments (each cell is a category label)

    Returns:
        Fleiss' Kappa score (-1 to 1)
    """
    if not annotations_matrix or not annotations_matrix[0]:
        return 0.0

    n_items = len(annotations_matrix)
    n_annotators = len(annotations_matrix[0])

    # Collect all categories
    all_categories: Set[Any] = set()
    for row in annotations_matrix:
        all_categories.update(row)
    all_categories = sorted(all_categories, key=str)

    # Build counts matrix: n_items x n_categories
    counts = []
    for row in annotations_matrix:
        row_counts = Counter(row)
        counts.append([row_counts.get(cat, 0) for cat in all_categories])

    # P_i: extent of agreement for each item
    p_i_list = []
    for item_counts in counts:
        total = sum(item_counts)
        if total <= 1:
            p_i_list.append(1.0)
            continue
        sum_sq = sum(c * c for c in item_counts)
        p_i = (sum_sq - total) / (total * (total - 1))
        p_i_list.append(p_i)

    P_bar = sum(p_i_list) / n_items

    # P_e: expected agreement by chance
    p_j_list = []
    n_total = n_items * n_annotators
    for j in range(len(all_categories)):
        col_sum = sum(counts[i][j] for i in range(n_items))
        p_j = col_sum / n_total
        p_j_list.append(p_j)

    P_e = sum(p * p for p in p_j_list)

    if P_e == 1.0:
        return 1.0

    kappa = (P_bar - P_e) / (1 - P_e) if (1 - P_e) != 0 else 0.0
    return kappa


def krippendorffs_alpha(
    data: List[List[Optional[float]]],
    *,
    level_of_measurement: str = "interval",
) -> float:
    """Compute Kriendorff's Alpha (simplified for interval data).

    Note: This is a simplified implementation for interval/ratio data.

    Args:
        data: Matrix of annotations (items x annotators), None = missing
        level_of_measurement: Type of data ("nominal", "ordinal", "interval", "ratio")

    Returns:
        Krippendorff's Alpha score
    """
    if not data or not data[0]:
        return 0.0

    n_items = len(data)
    n_annotators = len(data[0])

    # Filter items with at least 2 annotations
    valid_items = [
        [v for v in row if v is not None]
        for row in data
        if sum(1 for v in row if v is not None) >= 2
    ]

    if len(valid_items) < 2:
        return 0.0

    # For interval data, use squared difference
    # Observed disagreement
    D_o = 0.0
    total_pairs = 0

    for item_values in valid_items:
        k = len(item_values)
        if k < 2:
            continue
        for i in range(k):
            for j in range(i + 1, k):
                diff = item_values[i] - item_values[j]
                D_o += diff * diff
                total_pairs += 1

    if total_pairs == 0:
        return 0.0

    D_o /= total_pairs

    # Expected disagreement
    all_values = [v for item in valid_items for v in item]
    if len(all_values) < 2:
        return 0.0

    mean_val = sum(all_values) / len(all_values)
    D_e = sum((v - mean_val) ** 2 for v in all_values) / len(all_values)

    if D_e == 0:
        return 1.0

    alpha = 1 - D_o / D_e
    return alpha


# ======================================================================
# Consensus Building
# ======================================================================


def majority_vote(annotations: List[Any]) -> Tuple[Any, float]:
    """Compute majority vote consensus.

    Args:
        annotations: List of annotations from different annotators

    Returns:
        Tuple of (consensus_value, confidence)
    """
    if not annotations:
        return None, 0.0

    counter = Counter(annotations)
    most_common, count = counter.most_common(1)[0]
    confidence = count / len(annotations)

    return most_common, confidence


def mean_aggregation(values: List[float]) -> Tuple[float, float]:
    """Aggregate numerical annotations using mean and std.

    Args:
        values: List of numerical annotations

    Returns:
        Tuple of (mean, std_dev)
    """
    if not values:
        return 0.0, 0.0

    n = len(values)
    mean_val = sum(values) / n

    if n < 2:
        return mean_val, 0.0

    variance = sum((v - mean_val) ** 2 for v in values) / (n - 1)
    std_dev = math.sqrt(variance)

    return mean_val, std_dev


# ======================================================================
# Human Evaluation Manager
# ======================================================================


class HumanEvaluationManager:
    """Manager for human evaluation workflows.

    Handles task creation, assignment, quality control,
    and adjudication workflows.

    Usage:
        manager = HumanEvaluationManager()
        manager.create_tasks(items=[...], criteria=["quality", "accuracy"])
        manager.assign_task(task_id, annotator_id)
    """

    def __init__(self) -> None:
        """Initialize the human evaluation manager."""
        self._tasks: Dict[str, AnnotationTask] = {}
        self._annotators: Dict[str, AnnotatorProfile] = {}
        self._guidelines: Dict[str, AnnotationGuidelines] = {}
        self._evaluations: Dict[str, Dict[str, Any]] = {}
        self._gold_standards: Dict[str, Dict[str, Any]] = {}
        self._quality_checks: Dict[str, Dict[str, Any]] = {}

    # ------------------------------------------------------------------
    # Annotator management
    # ------------------------------------------------------------------

    def register_annotator(self, profile: AnnotatorProfile) -> None:
        """Register an annotator.

        Args:
            profile: Annotator profile
        """
        self._annotators[profile.annotator_id] = profile

    def get_annotator(self, annotator_id: str) -> Optional[AnnotatorProfile]:
        """Get an annotator profile.

        Args:
            annotator_id: Annotator identifier

        Returns:
            Profile if found, None otherwise
        """
        return self._annotators.get(annotator_id)

    def list_annotators(self) -> List[AnnotatorProfile]:
        """List all annotators.

        Returns:
            List of annotator profiles
        """
        return list(self._annotators.values())

    # ------------------------------------------------------------------
    # Guidelines management
    # ------------------------------------------------------------------

    def add_guidelines(self, guidelines: AnnotationGuidelines) -> None:
        """Add annotation guidelines.

        Args:
            guidelines: Guidelines document
        """
        self._guidelines[guidelines.guideline_id] = guidelines

    def get_guidelines(self, guideline_id: str) -> Optional[AnnotationGuidelines]:
        """Get guidelines by ID.

        Args:
            guideline_id: Guideline identifier

        Returns:
            Guidelines if found, None otherwise
        """
        return self._guidelines.get(guideline_id)

    # ------------------------------------------------------------------
    # Task management
    # ------------------------------------------------------------------

    def create_task(
        self,
        item_id: str,
        item_data: Dict[str, Any],
        *,
        criteria: List[str],
        evaluation_id: str = "",
        priority: AnnotationPriority = AnnotationPriority.MEDIUM,
    ) -> AnnotationTask:
        """Create a new annotation task.

        Args:
            item_id: Item identifier
            item_data: Data for the item
            criteria: List of evaluation criteria
            evaluation_id: Parent evaluation ID
            priority: Task priority

        Returns:
            Created annotation task
        """
        task = AnnotationTask(
            evaluation_id=evaluation_id,
            item_id=item_id,
            item_data=item_data,
            criteria=criteria,
            priority=priority,
        )
        self._tasks[task.task_id] = task
        return task

    def create_tasks(
        self,
        items: List[Dict[str, Any]],
        *,
        criteria: List[str],
        evaluation_id: str = "",
        item_id_key: str = "id",
    ) -> List[AnnotationTask]:
        """Create multiple annotation tasks.

        Args:
            items: List of items to annotate
            criteria: Evaluation criteria
            evaluation_id: Parent evaluation ID
            item_id_key: Key to use for item ID

        Returns:
            List of created tasks
        """
        tasks = []
        for item in items:
            item_id = str(item.get(item_id_key, str(uuid4())))
            task = self.create_task(
                item_id=item_id,
                item_data=item,
                criteria=criteria,
                evaluation_id=evaluation_id,
            )
            tasks.append(task)
        return tasks

    def assign_task(
        self, task_id: str, annotator_id: str
    ) -> Optional[AnnotationTask]:
        """Assign a task to an annotator.

        Args:
            task_id: Task identifier
            annotator_id: Annotator identifier

        Returns:
            Updated task if successful, None otherwise
        """
        task = self._tasks.get(task_id)
        annotator = self._annotators.get(annotator_id)

        if not task or not annotator:
            return None

        task.assigned_to = annotator_id
        task.status = AnnotationStatus.ASSIGNED
        annotator.tasks_assigned += 1

        return task

    def assign_tasks_auto(
        self,
        task_ids: List[str],
        *,
        num_annotators_per_task: int = 2,
    ) -> Dict[str, List[str]]:
        """Auto-assign tasks to annotators.

        Distributes tasks evenly across active annotators.

        Args:
            task_ids: List of task IDs to assign
            num_annotators_per_task: Number of annotators per task

        Returns:
            Dictionary of task_id -> list of assigned annotator_ids
        """
        active_annotators = [
            a.annotator_id
            for a in self._annotators.values()
            if a.status == "active"
        ]

        if not active_annotators:
            return {}

        assignments: Dict[str, List[str]] = {}
        annotator_idx = 0

        for task_id in task_ids:
            task_assignments = []
            for _ in range(min(num_annotators_per_task, len(active_annotators))):
                annotator_id = active_annotators[
                    annotator_idx % len(active_annotators)
                ]
                task_assignments.append(annotator_id)
                self.assign_task(task_id, annotator_id)
                annotator_idx += 1
            assignments[task_id] = task_assignments

        return assignments

    # ------------------------------------------------------------------
    # Annotation submission
    # ------------------------------------------------------------------

    def submit_annotation(
        self,
        task_id: str,
        annotator_id: str,
        ratings: Dict[str, float],
        *,
        comments: str = "",
        flags: Optional[List[str]] = None,
        time_spent_seconds: float = 0.0,
    ) -> Optional[Annotation]:
        """Submit an annotation for a task.

        Args:
            task_id: Task identifier
            annotator_id: Annotator identifier
            ratings: Dictionary of criterion -> rating
            comments: Free-text comments
            flags: List of flagged issues
            time_spent_seconds: Time spent

        Returns:
            Created annotation if successful, None otherwise
        """
        task = self._tasks.get(task_id)
        if not task:
            return None

        annotation = Annotation(
            task_id=task_id,
            annotator_id=annotator_id,
            ratings=dict(ratings),
            comments=comments,
            flags=flags or [],
            time_spent_seconds=time_spent_seconds,
        )

        task.annotations.append(
            {
                "annotation_id": annotation.annotation_id,
                "annotator_id": annotator_id,
                "ratings": dict(ratings),
                "comments": comments,
                "flags": flags or [],
                "time_spent_seconds": time_spent_seconds,
                "submitted_at": annotation.submitted_at,
            }
        )

        # Update task status
        if task.status in (AnnotationStatus.ASSIGNED, AnnotationStatus.IN_PROGRESS):
            task.status = AnnotationStatus.SUBMITTED
            task.submitted_at = datetime.now(timezone.utc).isoformat()

        # Update annotator stats
        annotator = self._annotators.get(annotator_id)
        if annotator:
            annotator.tasks_completed += 1
            if annotator.tasks_completed == 1:
                annotator.avg_time_per_task = time_spent_seconds
            else:
                annotator.avg_time_per_task = (
                    annotator.avg_time_per_task * (annotator.tasks_completed - 1)
                    + time_spent_seconds
                ) / annotator.tasks_completed

        return annotation

    # ------------------------------------------------------------------
    # Quality control
    # ------------------------------------------------------------------

    def add_gold_standard(
        self,
        item_id: str,
        gold_ratings: Dict[str, float],
        *,
        explanation: str = "",
    ) -> None:
        """Add a gold standard annotation for quality control.

        Args:
            item_id: Item identifier
            gold_ratings: Gold standard ratings
            explanation: Explanation of the gold standard
        """
        self._gold_standards[item_id] = {
            "ratings": gold_ratings,
            "explanation": explanation,
        }

    def check_annotator_quality(
        self, annotator_id: str
    ) -> Dict[str, Any]:
        """Check annotator quality against gold standards.

        Args:
            annotator_id: Annotator identifier

        Returns:
            Quality check results
        """
        gold_matches = 0
        gold_total = 0
        total_deviation = 0.0

        for task in self._tasks.values():
            for ann in task.annotations:
                if ann["annotator_id"] != annotator_id:
                    continue
                if task.item_id in self._gold_standards:
                    gold_total += 1
                    gold = self._gold_standards[task.item_id]["ratings"]
                    # Compute average deviation across criteria
                    deviations = []
                    for criterion, gold_val in gold.items():
                        ann_val = ann["ratings"].get(criterion, 0.0)
                        deviations.append(abs(ann_val - gold_val))
                    if deviations:
                        avg_dev = sum(deviations) / len(deviations)
                        total_deviation += avg_dev
                        if avg_dev < 0.5:  # Within 0.5 points = match
                            gold_matches += 1

        accuracy = gold_matches / gold_total if gold_total > 0 else 0.0
        avg_deviation = total_deviation / gold_total if gold_total > 0 else 0.0

        return {
            "annotator_id": annotator_id,
            "gold_standard_accuracy": accuracy,
            "avg_deviation": avg_deviation,
            "gold_items_evaluated": gold_total,
            "quality_score": max(0.0, 1.0 - avg_deviation / 5.0),  # Assuming 0-5 scale
        }

    # ------------------------------------------------------------------
    # Adjudication
    # ------------------------------------------------------------------

    def get_consensus(
        self, task_id: str
    ) -> Dict[str, Dict[str, Any]]:
        """Get consensus annotations for a task.

        Args:
            task_id: Task identifier

        Returns:
            Dictionary of criterion -> consensus data
        """
        task = self._tasks.get(task_id)
        if not task or not task.annotations:
            return {}

        consensus: Dict[str, Dict[str, Any]] = {}

        # Group ratings by criterion
        criterion_values: Dict[str, List[float]] = defaultdict(list)
        for ann in task.annotations:
            for criterion, rating in ann["ratings"].items():
                criterion_values[criterion].append(rating)

        # Compute consensus for each criterion
        for criterion, values in criterion_values.items():
            mean_val, std_val = mean_aggregation(values)
            consensus[criterion] = {
                "mean": mean_val,
                "std_dev": std_val,
                "num_annotations": len(values),
                "agreement": max(0.0, 1.0 - std_val / 5.0),  # Normalized agreement
                "values": values,
            }

        return consensus

    def get_disagreements(
        self,
        *,
        threshold: float = 1.0,
    ) -> List[Dict[str, Any]]:
        """Find tasks with high annotator disagreement.

        Args:
            threshold: Std dev threshold for flagging disagreement

        Returns:
            List of tasks with high disagreement
        """
        disagreements = []

        for task_id, task in self._tasks.items():
            if len(task.annotations) < 2:
                continue

            consensus = self.get_consensus(task_id)
            max_disagreement = 0.0
            for criterion, data in consensus.items():
                if data["std_dev"] > max_disagreement:
                    max_disagreement = data["std_dev"]

            if max_disagreement >= threshold:
                disagreements.append(
                    {
                        "task_id": task_id,
                        "item_id": task.item_id,
                        "max_disagreement": max_disagreement,
                        "num_annotations": len(task.annotations),
                        "consensus": consensus,
                    }
                )

        disagreements.sort(key=lambda x: x["max_disagreement"], reverse=True)
        return disagreements

    def adjudicate(
        self,
        task_id: str,
        final_ratings: Dict[str, float],
        *,
        adjudicator_id: str,
        comments: str = "",
    ) -> Optional[AnnotationTask]:
        """Adjudicate a task with final ratings.

        Args:
            task_id: Task identifier
            final_ratings: Final adjudicated ratings
            adjudicator_id: Adjudicator identifier
            comments: Adjudication comments

        Returns:
            Updated task if successful, None otherwise
        """
        task = self._tasks.get(task_id)
        if not task:
            return None

        task.status = AnnotationStatus.ADJUDICATED
        task.metadata["adjudication"] = {
            "final_ratings": final_ratings,
            "adjudicator_id": adjudicator_id,
            "comments": comments,
            "adjudicated_at": datetime.now(timezone.utc).isoformat(),
        }

        return task

    # ------------------------------------------------------------------
    # Agreement computation
    # ------------------------------------------------------------------

    def compute_inter_annotator_agreement(
        self,
        *,
        criterion: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Compute inter-annotator agreement across all tasks.

        Args:
            criterion: Specific criterion to evaluate (None for all)

        Returns:
            Agreement statistics
        """
        # Collect annotations per task per criterion
        task_criterion_annotations: Dict[str, Dict[str, List[float]]] = defaultdict(
            lambda: defaultdict(list)
        )

        for task_id, task in self._tasks.items():
            for ann in task.annotations:
                for crit, rating in ann["ratings"].items():
                    if criterion and crit != criterion:
                        continue
                    task_criterion_annotations[task_id][crit].append(rating)

        # Compute agreement for each criterion
        results: Dict[str, Any] = {}

        for crit in set(
            c
            for task_data in task_criterion_annotations.values()
            for c in task_data.keys()
        ):
            # Collect all annotations for this criterion
            all_annotations: List[List[float]] = []
            for task_data in task_criterion_annotations.values():
                if crit in task_data and len(task_data[crit]) >= 2:
                    all_annotations.append(task_data[crit])

            if not all_annotations:
                continue

            # For numerical ratings, compute Krippendorff's alpha
            # Convert to the expected format
            data_matrix = []
            for annotations in all_annotations:
                data_matrix.append(annotations)

            alpha = krippendorffs_alpha([
                [v if i < len(row) else None for i, v in enumerate(row)]
                for row in data_matrix
            ])

            # Also compute average pairwise agreement
            pairwise_agreements = []
            for annotations in all_annotations:
                if len(annotations) >= 2:
                    # Use mean absolute deviation as inverse agreement
                    mean_val = sum(annotations) / len(annotations)
                    mad = sum(abs(v - mean_val) for v in annotations) / len(annotations)
                    agreement = max(0.0, 1.0 - mad / 5.0)
                    pairwise_agreements.append(agreement)

            results[crit] = {
                "krippendorffs_alpha": alpha,
                "avg_agreement": (
                    sum(pairwise_agreements) / len(pairwise_agreements)
                    if pairwise_agreements
                    else 0.0
                ),
                "num_tasks": len(all_annotations),
            }

        return results

    # ------------------------------------------------------------------
    # Statistics
    # ------------------------------------------------------------------

    def get_stats(self) -> Dict[str, Any]:
        """Get manager statistics.

        Returns:
            Dictionary of statistics
        """
        status_counts = Counter(task.status.value for task in self._tasks.values())

        return {
            "total_tasks": len(self._tasks),
            "tasks_by_status": dict(status_counts),
            "total_annotators": len(self._annotators),
            "active_annotators": sum(
                1 for a in self._annotators.values() if a.status == "active"
            ),
            "gold_standards": len(self._gold_standards),
            "guidelines": len(self._guidelines),
        }

    def __repr__(self) -> str:
        return (
            f"HumanEvaluationManager(tasks={len(self._tasks)}, "
            f"annotators={len(self._annotators)})"
        )
