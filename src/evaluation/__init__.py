"""Evaluation Framework for LiuHao AI OS.

Provides comprehensive evaluation capabilities:
- Evaluation metrics across multiple domains (text, code, classification, retrieval, reasoning, safety, cost)
- Benchmark runner with dataset management and report generation
- Human evaluation interface with annotation management and quality control
- Red teaming framework for adversarial testing and safety evaluation
"""

from .metrics import (
    MetricCategory,
    MetricResult,
    MetricAggregator,
    CostMetrics,
    # Text generation metrics
    bleu_score,
    rouge_n,
    rouge_l,
    meteor_score,
    # Code generation metrics
    pass_at_k,
    code_bleu,
    # Classification metrics
    accuracy,
    precision_recall_f1,
    auc_roc,
    # Retrieval metrics
    recall_at_k,
    precision_at_k,
    mrr,
    ndcg_at_k,
    # Reasoning metrics
    step_accuracy,
    final_answer_accuracy,
    # Safety metrics
    toxicity_score,
    bias_score,
    harmful_content_detection,
)

from .benchmark_runner import (
    BenchmarkStatus,
    EvaluationStrategy,
    BenchmarkDataset,
    TestSuite,
    BenchmarkResult,
    Baseline,
    BenchmarkRunner,
)

from .human_eval import (
    AnnotationStatus,
    AnnotationPriority,
    AnnotationGuidelines,
    AnnotationTask,
    Annotation,
    AnnotatorProfile,
    HumanEvaluationManager,
    # Agreement metrics
    cohens_kappa,
    fleiss_kappa,
    krippendorffs_alpha,
    # Consensus
    majority_vote,
    mean_aggregation,
)

from .red_team import (
    AttackType,
    VulnerabilitySeverity,
    AttackStatus,
    AttackPrompt,
    AttackResult,
    VulnerabilityReport,
    SafetyEvaluationPrompt,
    AttackStrategies,
    JailbreakDetector,
    SafetyEvaluator,
    RedTeamRunner,
)

__all__ = [
    # Metrics
    "MetricCategory",
    "MetricResult",
    "MetricAggregator",
    "CostMetrics",
    "bleu_score",
    "rouge_n",
    "rouge_l",
    "meteor_score",
    "pass_at_k",
    "code_bleu",
    "accuracy",
    "precision_recall_f1",
    "auc_roc",
    "recall_at_k",
    "precision_at_k",
    "mrr",
    "ndcg_at_k",
    "step_accuracy",
    "final_answer_accuracy",
    "toxicity_score",
    "bias_score",
    "harmful_content_detection",
    # Benchmark runner
    "BenchmarkStatus",
    "EvaluationStrategy",
    "BenchmarkDataset",
    "TestSuite",
    "BenchmarkResult",
    "Baseline",
    "BenchmarkRunner",
    # Human evaluation
    "AnnotationStatus",
    "AnnotationPriority",
    "AnnotationGuidelines",
    "AnnotationTask",
    "Annotation",
    "AnnotatorProfile",
    "HumanEvaluationManager",
    "cohens_kappa",
    "fleiss_kappa",
    "krippendorffs_alpha",
    "majority_vote",
    "mean_aggregation",
    # Red teaming
    "AttackType",
    "VulnerabilitySeverity",
    "AttackStatus",
    "AttackPrompt",
    "AttackResult",
    "VulnerabilityReport",
    "SafetyEvaluationPrompt",
    "AttackStrategies",
    "JailbreakDetector",
    "SafetyEvaluator",
    "RedTeamRunner",
]
