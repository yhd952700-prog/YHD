"""Evaluation metrics for the LiuHao AI OS evaluation framework.

Provides comprehensive evaluation metrics across multiple domains:
- Text generation metrics: BLEU, ROUGE, METEOR, BLEURT interface
- Code generation metrics: pass@k, BLEU code, CodeBLEU
- Classification metrics: accuracy, precision, recall, F1, AUC-ROC
- Retrieval metrics: recall@k, precision@k, MRR, NDCG
- Reasoning metrics: step accuracy, final answer accuracy
- Safety metrics: toxicity, bias, harmful content detection
- Cost metrics: tokens, cost, latency
"""

from __future__ import annotations

import math
import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple, Union


class MetricCategory(str, Enum):
    """Categories of evaluation metrics."""

    TEXT_GENERATION = "text_generation"
    CODE_GENERATION = "code_generation"
    CLASSIFICATION = "classification"
    RETRIEVAL = "retrieval"
    REASONING = "reasoning"
    SAFETY = "safety"
    COST = "cost"


@dataclass
class MetricResult:
    """Result of a metric computation.

    Attributes:
        metric_name: Name of the metric
        category: Metric category
        value: Computed metric value
        confidence: Confidence interval or score
        details: Additional details about the computation
        sample_size: Number of samples evaluated
        metadata: Additional metadata
    """

    metric_name: str
    category: MetricCategory
    value: float
    confidence: float = 1.0
    details: Dict[str, Any] = field(default_factory=dict)
    sample_size: int = 0
    metadata: Dict[str, Any] = field(default_factory=dict)

    def __repr__(self) -> str:
        return (
            f"MetricResult({self.metric_name}={self.value:.4f}, "
            f"n={self.sample_size})"
        )


# ======================================================================
# Text Generation Metrics
# ======================================================================


def _tokenize(text: str) -> List[str]:
    """Simple whitespace tokenization.

    Args:
        text: Input text

    Returns:
        List of tokens
    """
    return text.lower().split()


def _ngrams(tokens: List[str], n: int) -> Counter:
    """Compute n-gram counts.

    Args:
        tokens: List of tokens
        n: N-gram size

    Returns:
        Counter of n-grams
    """
    ngrams = Counter()
    for i in range(len(tokens) - n + 1):
        ngram = tuple(tokens[i : i + n])
        ngrams[ngram] += 1
    return ngrams


def bleu_score(
    reference: str,
    hypothesis: str,
    *,
    max_n: int = 4,
    weights: Optional[List[float]] = None,
) -> float:
    """Compute BLEU score for text generation.

    Args:
        reference: Reference text
        hypothesis: Generated text
        max_n: Maximum n-gram order
        weights: Weights for each n-gram order

    Returns:
        BLEU score (0-1)
    """
    if weights is None:
        weights = [1.0 / max_n] * max_n

    ref_tokens = _tokenize(reference)
    hyp_tokens = _tokenize(hypothesis)

    if not hyp_tokens or not ref_tokens:
        return 0.0

    # Brevity penalty
    bp = min(1.0, math.exp(1 - len(ref_tokens) / len(hyp_tokens)))

    # Modified precision for each n-gram order
    precisions = []
    for n in range(1, max_n + 1):
        ref_ngrams = _ngrams(ref_tokens, n)
        hyp_ngrams = _ngrams(hyp_tokens, n)

        if not hyp_ngrams:
            precisions.append(0.0)
            continue

        clipped = 0
        total = 0
        for ngram, count in hyp_ngrams.items():
            clipped += min(count, ref_ngrams.get(ngram, 0))
            total += count

        precisions.append(clipped / total if total > 0 else 0.0)

    # Geometric mean with weights
    log_sum = 0.0
    for p, w in zip(precisions, weights):
        if p > 0:
            log_sum += w * math.log(p)
        else:
            return 0.0

    return bp * math.exp(log_sum)


def rouge_n(
    reference: str,
    hypothesis: str,
    *,
    n: int = 1,
) -> Dict[str, float]:
    """Compute ROUGE-N score.

    Args:
        reference: Reference text
        hypothesis: Generated text
        n: N-gram size

    Returns:
        Dictionary with precision, recall, and f1
    """
    ref_tokens = _tokenize(reference)
    hyp_tokens = _tokenize(hypothesis)

    if not ref_tokens or not hyp_tokens:
        return {"precision": 0.0, "recall": 0.0, "f1": 0.0}

    ref_ngrams = _ngrams(ref_tokens, n)
    hyp_ngrams = _ngrams(hyp_tokens, n)

    overlap = 0
    for ngram, count in hyp_ngrams.items():
        overlap += min(count, ref_ngrams.get(ngram, 0))

    precision = overlap / sum(hyp_ngrams.values()) if hyp_ngrams else 0.0
    recall = overlap / sum(ref_ngrams.values()) if ref_ngrams else 0.0

    f1 = (
        2 * precision * recall / (precision + recall)
        if (precision + recall) > 0
        else 0.0
    )

    return {"precision": precision, "recall": recall, "f1": f1}


def rouge_l(
    reference: str,
    hypothesis: str,
) -> Dict[str, float]:
    """Compute ROUGE-L (Longest Common Subsequence) score.

    Args:
        reference: Reference text
        hypothesis: Generated text

    Returns:
        Dictionary with precision, recall, and f1
    """
    ref_tokens = _tokenize(reference)
    hyp_tokens = _tokenize(hypothesis)

    if not ref_tokens or not hyp_tokens:
        return {"precision": 0.0, "recall": 0.0, "f1": 0.0}

    # LCS length
    m, n = len(ref_tokens), len(hyp_tokens)
    dp = [[0] * (n + 1) for _ in range(m + 1)]

    for i in range(1, m + 1):
        for j in range(1, n + 1):
            if ref_tokens[i - 1] == hyp_tokens[j - 1]:
                dp[i][j] = dp[i - 1][j - 1] + 1
            else:
                dp[i][j] = max(dp[i - 1][j], dp[i][j - 1])

    lcs_length = dp[m][n]

    precision = lcs_length / n if n > 0 else 0.0
    recall = lcs_length / m if m > 0 else 0.0
    f1 = (
        2 * precision * recall / (precision + recall)
        if (precision + recall) > 0
        else 0.0
    )

    return {"precision": precision, "recall": recall, "f1": f1}


def meteor_score(
    reference: str,
    hypothesis: str,
    *,
    alpha: float = 0.9,
    beta: float = 3.0,
    gamma: float = 0.5,
) -> float:
    """Compute simplified METEOR score.

    This is a simplified implementation. Full METEOR includes stemming
    and synonym matching.

    Args:
        reference: Reference text
        hypothesis: Generated text
        alpha: Precision weight
        beta: Recall weight
        gamma: Fragment penalty weight

    Returns:
        METEOR score (0-1)
    """
    ref_tokens = _tokenize(reference)
    hyp_tokens = _tokenize(hypothesis)

    if not ref_tokens or not hyp_tokens:
        return 0.0

    # Unigram matches
    ref_counter = Counter(ref_tokens)
    hyp_counter = Counter(hyp_tokens)
    matches = 0
    for token, count in hyp_counter.items():
        matches += min(count, ref_counter.get(token, 0))

    precision = matches / len(hyp_tokens)
    recall = matches / len(ref_tokens)

    if precision == 0 or recall == 0:
        return 0.0

    # F-score
    fmean = (
        (1 + beta**2) * precision * recall / (beta**2 * precision + recall)
    )

    # Fragment penalty (simplified)
    # Count number of chunks (consecutive matches)
    matched_indices_ref = set()
    matched_indices_hyp = set()

    for i, h_token in enumerate(hyp_tokens):
        for j, r_token in enumerate(ref_tokens):
            if h_token == r_token and j not in matched_indices_ref:
                matched_indices_ref.add(j)
                matched_indices_hyp.add(i)
                break

    sorted_hyp = sorted(matched_indices_hyp)
    chunks = 1
    for i in range(1, len(sorted_hyp)):
        if sorted_hyp[i] != sorted_hyp[i - 1] + 1:
            chunks += 1

    if matches > 0:
        fragment_penalty = gamma * (chunks / matches) ** 3
    else:
        fragment_penalty = 0.0

    return fmean * (1 - fragment_penalty)


# ======================================================================
# Code Generation Metrics
# ======================================================================


def pass_at_k(
    num_samples: int,
    num_correct: int,
    k: int = 1,
) -> float:
    """Compute pass@k metric for code generation.

    Estimates the probability of having at least one correct sample
    when generating k samples.

    Args:
        num_samples: Total number of samples generated
        num_correct: Number of correct samples
        k: Number of samples to consider

    Returns:
        pass@k estimate (0-1)
    """
    if num_correct == 0:
        return 0.0
    if num_correct >= num_samples:
        return 1.0
    if k >= num_samples:
        return 1.0 if num_correct > 0 else 0.0

    # Use the unbiased estimator: 1 - C(n-c, k) / C(n, k)
    # where n = num_samples, c = num_correct
    # For numerical stability, compute step by step
    n = num_samples
    c = num_correct

    # Compute product (n-c-i)/(n-i) for i in 0..k-1
    product = 1.0
    for i in range(k):
        product *= (n - c - i) / (n - i)

    return 1.0 - product


def code_bleu(
    reference: str,
    hypothesis: str,
    *,
    max_n: int = 4,
) -> float:
    """Compute CodeBLEU score for code generation.

    Simplified implementation focusing on n-gram matching.
    Full CodeBLEU includes syntax matching and data flow.

    Args:
        reference: Reference code
        hypothesis: Generated code
        max_n: Maximum n-gram order

    Returns:
        CodeBLEU score (0-1)
    """
    # Use token-based BLEU with code-specific tokenization
    def code_tokenize(code: str) -> List[str]:
        """Simple code tokenizer."""
        # Split on whitespace and punctuation boundaries
        tokens = re.findall(
            r"[A-Za-z_][A-Za-z0-9_]*|[0-9]+|[^\sA-Za-z0-9_]",
            code,
        )
        return [t for t in tokens if t.strip()]

    ref_tokens = code_tokenize(reference)
    hyp_tokens = code_tokenize(hypothesis)

    if not ref_tokens or not hyp_tokens:
        return 0.0

    # Brevity penalty
    bp = min(1.0, math.exp(1 - len(ref_tokens) / len(hyp_tokens)))

    precisions = []
    for n in range(1, max_n + 1):
        ref_ngrams = _ngrams(ref_tokens, n)
        hyp_ngrams = _ngrams(hyp_tokens, n)

        if not hyp_ngrams:
            precisions.append(0.0)
            continue

        clipped = 0
        total = 0
        for ngram, count in hyp_ngrams.items():
            clipped += min(count, ref_ngrams.get(ngram, 0))
            total += count

        precisions.append(clipped / total if total > 0 else 0.0)

    log_sum = 0.0
    weights = [1.0 / max_n] * max_n
    for p, w in zip(precisions, weights):
        if p > 0:
            log_sum += w * math.log(p)
        else:
            return 0.0

    return bp * math.exp(log_sum)


# ======================================================================
# Classification Metrics
# ======================================================================


def accuracy(
    y_true: Sequence[Any],
    y_pred: Sequence[Any],
) -> float:
    """Compute classification accuracy.

    Args:
        y_true: True labels
        y_pred: Predicted labels

    Returns:
        Accuracy (0-1)
    """
    if len(y_true) != len(y_pred) or len(y_true) == 0:
        return 0.0

    correct = sum(1 for t, p in zip(y_true, y_pred) if t == p)
    return correct / len(y_true)


def precision_recall_f1(
    y_true: Sequence[Any],
    y_pred: Sequence[Any],
    *,
    positive_label: Any = 1,
    average: str = "binary",
) -> Dict[str, float]:
    """Compute precision, recall, and F1 score.

    Args:
        y_true: True labels
        y_pred: Predicted labels
        positive_label: Positive class label for binary classification
        average: Averaging strategy ("binary", "micro", "macro", "weighted")

    Returns:
        Dictionary with precision, recall, and f1
    """
    if len(y_true) != len(y_pred) or len(y_true) == 0:
        return {"precision": 0.0, "recall": 0.0, "f1": 0.0}

    if average == "binary":
        tp = sum(
            1
            for t, p in zip(y_true, y_pred)
            if t == positive_label and p == positive_label
        )
        fp = sum(
            1
            for t, p in zip(y_true, y_pred)
            if t != positive_label and p == positive_label
        )
        fn = sum(
            1
            for t, p in zip(y_true, y_pred)
            if t == positive_label and p != positive_label
        )

        precision = tp / (tp + fp) if (tp + fp) > 0 else 0.0
        recall = tp / (tp + fn) if (tp + fn) > 0 else 0.0
        f1 = (
            2 * precision * recall / (precision + recall)
            if (precision + recall) > 0
            else 0.0
        )

        return {"precision": precision, "recall": recall, "f1": f1}

    elif average == "macro":
        classes = set(y_true) | set(y_pred)
        precisions = []
        recalls = []
        f1s = []

        for cls in classes:
            result = precision_recall_f1(
                y_true, y_pred, positive_label=cls, average="binary"
            )
            precisions.append(result["precision"])
            recalls.append(result["recall"])
            f1s.append(result["f1"])

        return {
            "precision": sum(precisions) / len(precisions) if precisions else 0.0,
            "recall": sum(recalls) / len(recalls) if recalls else 0.0,
            "f1": sum(f1s) / len(f1s) if f1s else 0.0,
        }

    elif average == "micro":
        # Micro-averaging = accuracy for multi-class
        acc = accuracy(y_true, y_pred)
        return {"precision": acc, "recall": acc, "f1": acc}

    else:
        # Weighted
        classes = set(y_true)
        class_counts = Counter(y_true)
        total = len(y_true)

        weighted_p = 0.0
        weighted_r = 0.0
        weighted_f = 0.0

        for cls in classes:
            result = precision_recall_f1(
                y_true, y_pred, positive_label=cls, average="binary"
            )
            weight = class_counts[cls] / total if total > 0 else 0.0
            weighted_p += result["precision"] * weight
            weighted_r += result["recall"] * weight
            weighted_f += result["f1"] * weight

        return {
            "precision": weighted_p,
            "recall": weighted_r,
            "f1": weighted_f,
        }


def auc_roc(
    y_true: Sequence[int],
    y_scores: Sequence[float],
) -> float:
    """Compute AUC-ROC score.

    Args:
        y_true: True binary labels (0 or 1)
        y_scores: Predicted scores/probabilities

    Returns:
        AUC-ROC score (0-1)
    """
    if len(y_true) != len(y_scores) or len(y_true) == 0:
        return 0.0

    # Sort by score descending
    pairs = sorted(zip(y_scores, y_true), key=lambda x: -x[0])

    positives = sum(1 for y in y_true if y == 1)
    negatives = len(y_true) - positives

    if positives == 0 or negatives == 0:
        return 0.5  # Undefined, return 0.5 as baseline

    # Compute AUC using the trapezoidal rule
    tp = 0
    fp = 0
    prev_tpr = 0.0
    prev_fpr = 0.0
    auc = 0.0

    for score, label in pairs:
        if label == 1:
            tp += 1
        else:
            fp += 1

        tpr = tp / positives
        fpr = fp / negatives

        auc += (fpr - prev_fpr) * (tpr + prev_tpr) / 2
        prev_tpr = tpr
        prev_fpr = fpr

    return auc


# ======================================================================
# Retrieval Metrics
# ======================================================================


def recall_at_k(
    relevant: Set[Any],
    retrieved: Sequence[Any],
    k: int = 10,
) -> float:
    """Compute Recall@k for retrieval.

    Args:
        relevant: Set of relevant items
        retrieved: Ordered list of retrieved items
        k: Number of top items to consider

    Returns:
        Recall@k (0-1)
    """
    if not relevant:
        return 0.0

    top_k = set(retrieved[:k])
    hits = len(relevant & top_k)
    return hits / len(relevant)


def precision_at_k(
    relevant: Set[Any],
    retrieved: Sequence[Any],
    k: int = 10,
) -> float:
    """Compute Precision@k for retrieval.

    Args:
        relevant: Set of relevant items
        retrieved: Ordered list of retrieved items
        k: Number of top items to consider

    Returns:
        Precision@k (0-1)
    """
    if k == 0:
        return 0.0

    top_k = set(retrieved[:k])
    hits = len(relevant & top_k)
    return hits / min(k, len(retrieved)) if retrieved else 0.0


def mrr(
    relevant: Set[Any],
    retrieved: Sequence[Any],
) -> float:
    """Compute Mean Reciprocal Rank.

    Args:
        relevant: Set of relevant items
        retrieved: Ordered list of retrieved items

    Returns:
        MRR score
    """
    for i, item in enumerate(retrieved):
        if item in relevant:
            return 1.0 / (i + 1)
    return 0.0


def ndcg_at_k(
    relevant_scores: Dict[Any, float],
    retrieved: Sequence[Any],
    k: int = 10,
) -> float:
    """Compute Normalized Discounted Cumulative Gain at k.

    Args:
        relevant_scores: Dictionary mapping items to relevance scores
        retrieved: Ordered list of retrieved items
        k: Number of top items to consider

    Returns:
        NDCG@k (0-1)
    """

    def dcg(items: Sequence[Any], scores: Dict[Any, float], k_val: int) -> float:
        total = 0.0
        for i, item in enumerate(items[:k_val]):
            rel = scores.get(item, 0.0)
            total += rel / math.log2(i + 2)  # i+2 because positions are 1-indexed
        return total

    actual_dcg = dcg(retrieved, relevant_scores, k)

    # Ideal DCG: sort relevant items by score descending
    ideal_items = sorted(
        relevant_scores.keys(),
        key=lambda x: relevant_scores[x],
        reverse=True,
    )
    ideal_dcg = dcg(ideal_items, relevant_scores, k)

    if ideal_dcg == 0:
        return 0.0

    return actual_dcg / ideal_dcg


# ======================================================================
# Reasoning Metrics
# ======================================================================


def step_accuracy(
    reference_steps: Sequence[str],
    predicted_steps: Sequence[str],
) -> Dict[str, float]:
    """Evaluate step-by-step reasoning accuracy.

    Args:
        reference_steps: Reference reasoning steps
        predicted_steps: Predicted reasoning steps

    Returns:
        Dictionary with step accuracy metrics
    """
    if not reference_steps:
        return {"accuracy": 0.0, "coverage": 0.0, "num_steps": 0}

    # Simple matching: compare step by step
    correct = 0
    total = len(reference_steps)

    for i, ref_step in enumerate(reference_steps):
        if i < len(predicted_steps):
            # Check if predicted step is semantically close (simplified: token overlap)
            ref_tokens = set(_tokenize(ref_step))
            pred_tokens = set(_tokenize(predicted_steps[i]))
            if ref_tokens and pred_tokens:
                overlap = len(ref_tokens & pred_tokens) / len(ref_tokens)
                if overlap >= 0.5:
                    correct += 1

    coverage = min(len(predicted_steps) / total, 1.0)

    return {
        "accuracy": correct / total if total > 0 else 0.0,
        "coverage": coverage,
        "num_steps": total,
        "predicted_steps": len(predicted_steps),
    }


def final_answer_accuracy(
    reference_answers: Sequence[Any],
    predicted_answers: Sequence[Any],
) -> float:
    """Compute final answer accuracy for reasoning tasks.

    Args:
        reference_answers: Ground truth answers
        predicted_answers: Predicted answers

    Returns:
        Final answer accuracy (0-1)
    """
    return accuracy(reference_answers, predicted_answers)


# ======================================================================
# Safety Metrics
# ======================================================================


def toxicity_score(text: str) -> float:
    """Estimate toxicity of text (simplified keyword-based).

    Note: This is a placeholder implementation. For production use,
    integrate with a proper toxicity detection model.

    Args:
        text: Text to evaluate

    Returns:
        Toxicity score (0-1)
    """
    toxic_words = {
        "hate", "stupid", "idiot", "kill", "violent", "attack",
        "threat", "harass", "abuse", "discriminate", "racist",
        "sexist", "slur",
    }

    tokens = _tokenize(text.lower())
    if not tokens:
        return 0.0

    toxic_count = sum(1 for t in tokens if t in toxic_words)
    return min(1.0, toxic_count / len(tokens) * 5)  # Scale up


def bias_score(
    text: str,
    groups: Optional[List[str]] = None,
) -> float:
    """Estimate bias in text (simplified).

    Note: This is a placeholder implementation.

    Args:
        text: Text to evaluate
        groups: List of demographic groups to check

    Returns:
        Bias score (0-1)
    """
    if groups is None:
        groups = [
            "men", "women", "male", "female", "boy", "girl",
            "old", "young", "elderly", "youth",
            "rich", "poor", "wealthy",
        ]

    tokens = _tokenize(text.lower())
    if not tokens:
        return 0.0

    group_counts = Counter()
    for token in tokens:
        for group in groups:
            if group in token:
                group_counts[group] += 1

    if len(group_counts) <= 1:
        return 0.0

    # Measure imbalance in group mentions
    values = list(group_counts.values())
    max_count = max(values)
    min_count = min(values)

    if max_count == 0:
        return 0.0

    return 1.0 - (min_count / max_count)


def harmful_content_detection(text: str) -> Dict[str, Any]:
    """Detect potentially harmful content (simplified).

    Args:
        text: Text to evaluate

    Returns:
        Dictionary with detection results
    """
    result = {
        "is_harmful": False,
        "toxicity": toxicity_score(text),
        "bias": bias_score(text),
        "categories": [],
        "confidence": 0.0,
    }

    if result["toxicity"] > 0.3:
        result["categories"].append("toxicity")
    if result["bias"] > 0.5:
        result["categories"].append("bias")

    result["is_harmful"] = len(result["categories"]) > 0
    result["confidence"] = max(result["toxicity"], result["bias"])

    return result


# ======================================================================
# Cost Metrics
# ======================================================================


@dataclass
class CostMetrics:
    """Cost-related metrics for model evaluation.

    Attributes:
        input_tokens: Number of input tokens
        output_tokens: Number of output tokens
        total_tokens: Total tokens
        cost: Total cost in dollars
        latency_ms: Latency in milliseconds
        cost_per_1k_input: Cost per 1K input tokens
        cost_per_1k_output: Cost per 1K output tokens
        tokens_per_second: Throughput
    """

    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0
    cost: float = 0.0
    latency_ms: float = 0.0
    cost_per_1k_input: float = 0.0
    cost_per_1k_output: float = 0.0
    tokens_per_second: float = 0.0

    @classmethod
    def compute(
        cls,
        input_tokens: int,
        output_tokens: int,
        cost_per_1k_input: float,
        cost_per_1k_output: float,
        latency_ms: float,
    ) -> "CostMetrics":
        """Compute cost metrics from raw data.

        Args:
            input_tokens: Input token count
            output_tokens: Output token count
            cost_per_1k_input: Cost per 1K input tokens
            cost_per_1k_output: Cost per 1K output tokens
            latency_ms: Latency in milliseconds

        Returns:
            CostMetrics instance
        """
        total = input_tokens + output_tokens
        cost = (
            input_tokens / 1000.0 * cost_per_1k_input
            + output_tokens / 1000.0 * cost_per_1k_output
        )
        tps = (total / (latency_ms / 1000.0)) if latency_ms > 0 else 0.0

        return cls(
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            total_tokens=total,
            cost=cost,
            latency_ms=latency_ms,
            cost_per_1k_input=cost_per_1k_input,
            cost_per_1k_output=cost_per_1k_output,
            tokens_per_second=tps,
        )


# ======================================================================
# Metric Aggregation
# ======================================================================


class MetricAggregator:
    """Aggregates metrics across multiple evaluations.

    Provides methods for computing means, confidence intervals,
    and statistical summaries.
    """

    def __init__(self) -> None:
        """Initialize the aggregator."""
        self._results: Dict[str, List[float]] = defaultdict(list)
        self._metadata: Dict[str, List[Dict[str, Any]]] = defaultdict(list)

    def add_result(self, name: str, value: float, metadata: Optional[Dict[str, Any]] = None) -> None:
        """Add a metric result.

        Args:
            name: Metric name
            value: Metric value
            metadata: Additional metadata
        """
        self._results[name].append(value)
        if metadata:
            self._metadata[name].append(metadata)

    def mean(self, name: str) -> float:
        """Compute mean of a metric.

        Args:
            name: Metric name

        Returns:
            Mean value
        """
        values = self._results.get(name, [])
        if not values:
            return 0.0
        return sum(values) / len(values)

    def std_dev(self, name: str) -> float:
        """Compute standard deviation of a metric.

        Args:
            name: Metric name

        Returns:
            Standard deviation
        """
        values = self._results.get(name, [])
        if len(values) < 2:
            return 0.0

        mean_val = self.mean(name)
        variance = sum((v - mean_val) ** 2 for v in values) / (len(values) - 1)
        return math.sqrt(variance)

    def confidence_interval(
        self, name: str, confidence: float = 0.95
    ) -> Tuple[float, float]:
        """Compute confidence interval using normal approximation.

        Args:
            name: Metric name
            confidence: Confidence level (0-1)

        Returns:
            Tuple of (lower_bound, upper_bound)
        """
        values = self._results.get(name, [])
        if len(values) < 2:
            val = values[0] if values else 0.0
            return (val, val)

        mean_val = self.mean(name)
        std = self.std_dev(name)
        n = len(values)

        # Z-score approximation for confidence level
        z_scores = {0.90: 1.645, 0.95: 1.96, 0.99: 2.576}
        z = z_scores.get(confidence, 1.96)

        margin = z * std / math.sqrt(n)
        return (mean_val - margin, mean_val + margin)

    def percentile(self, name: str, percentile: float) -> float:
        """Compute a percentile value.

        Args:
            name: Metric name
            percentile: Percentile (0-100)

        Returns:
            Percentile value
        """
        values = sorted(self._results.get(name, []))
        if not values:
            return 0.0

        k = (len(values) - 1) * percentile / 100.0
        f = math.floor(k)
        c = math.ceil(k)

        if f == c:
            return values[int(k)]

        d0 = values[f] * (c - k)
        d1 = values[c] * (k - f)
        return d0 + d1

    def summary(self, name: str) -> Dict[str, float]:
        """Get a full statistical summary for a metric.

        Args:
            name: Metric name

        Returns:
            Dictionary with statistical summary
        """
        values = self._results.get(name, [])
        if not values:
            return {
                "count": 0,
                "mean": 0.0,
                "std_dev": 0.0,
                "min": 0.0,
                "max": 0.0,
                "p25": 0.0,
                "p50": 0.0,
                "p75": 0.0,
            }

        return {
            "count": len(values),
            "mean": self.mean(name),
            "std_dev": self.std_dev(name),
            "min": min(values),
            "max": max(values),
            "p25": self.percentile(name, 25),
            "p50": self.percentile(name, 50),
            "p75": self.percentile(name, 75),
        }

    def all_metrics(self) -> Dict[str, Dict[str, float]]:
        """Get summaries for all metrics.

        Returns:
            Dictionary of metric_name -> summary
        """
        return {name: self.summary(name) for name in self._results}

    def __repr__(self) -> str:
        return f"MetricAggregator(metrics={len(self._results)})"
