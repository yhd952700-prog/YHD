"""Model Monitoring for MLOps.

Provides model monitoring capabilities:
- Performance drift detection
- Data drift detection
- Concept drift detection
- Model degradation alerts
- Input monitoring
- Output monitoring
- SLA tracking
- Anomaly detection
"""

from __future__ import annotations

import math
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum, auto
from typing import Any, Callable, Dict, List, Optional, Set, Tuple
from uuid import uuid4


class DriftType(str, Enum):
    """Types of drift."""

    DATA_DRIFT = "data_drift"
    CONCEPT_DRIFT = "concept_drift"
    PERFORMANCE_DRIFT = "performance_drift"
    FEATURE_DRIFT = "feature_drift"
    PREDICTION_DRIFT = "prediction_drift"


class DriftSeverity(str, Enum):
    """Drift severity levels."""

    NONE = "none"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class AlertLevel(str, Enum):
    """Alert severity levels."""

    INFO = "info"
    WARNING = "warning"
    ERROR = "error"
    CRITICAL = "critical"


@dataclass
class DriftResult:
    """Result of a drift detection analysis.

    Attributes:
        drift_id: Unique drift event identifier
        drift_type: Type of drift detected
        severity: Severity level
        feature_name: Name of the drifted feature (for feature drift)
        metric: Drift metric value
        threshold: Threshold that was exceeded
        p_value: Statistical p-value (if applicable)
        detected_at: Detection timestamp
        baseline_size: Number of samples in baseline
        current_size: Number of samples in current window
        details: Additional details
    """

    drift_id: str = field(default_factory=lambda: str(uuid4()))
    drift_type: DriftType = DriftType.DATA_DRIFT
    severity: DriftSeverity = DriftSeverity.NONE
    feature_name: str = ""
    metric: float = 0.0
    threshold: float = 0.0
    p_value: Optional[float] = None
    detected_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    baseline_size: int = 0
    current_size: int = 0
    details: Dict[str, Any] = field(default_factory=dict)

    @property
    def is_drifted(self) -> bool:
        """Whether drift was detected."""
        return self.severity != DriftSeverity.NONE


@dataclass
class MonitoringAlert:
    """A monitoring alert.

    Attributes:
        alert_id: Unique alert identifier
        level: Alert severity level
        title: Alert title
        description: Alert description
        model_id: Affected model
        metric_name: Associated metric
        current_value: Current metric value
        threshold_value: Threshold value
        drift_result: Associated drift result (if any)
        created_at: Creation timestamp
        acknowledged: Whether the alert has been acknowledged
        acknowledged_by: Person who acknowledged
        acknowledged_at: Acknowledgment timestamp
        metadata: Additional metadata
    """

    alert_id: str = field(default_factory=lambda: str(uuid4()))
    level: AlertLevel = AlertLevel.INFO
    title: str = ""
    description: str = ""
    model_id: str = ""
    metric_name: str = ""
    current_value: float = 0.0
    threshold_value: float = 0.0
    drift_result: Optional[DriftResult] = None
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    acknowledged: bool = False
    acknowledged_by: str = ""
    acknowledged_at: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class SLAConfig:
    """Service Level Agreement configuration.

    Attributes:
        sla_id: Unique SLA identifier
        name: SLA name
        model_id: Model this SLA applies to
        metric: Metric being tracked
        target: Target value
        threshold: Warning threshold
        period: Evaluation period in seconds
        description: SLA description
    """

    sla_id: str = field(default_factory=lambda: str(uuid4()))
    name: str = ""
    model_id: str = ""
    metric: str = ""
    target: float = 0.0
    threshold: float = 0.0
    period: int = 86400  # 24 hours
    description: str = ""


@dataclass
class SLAResult:
    """SLA evaluation result.

    Attributes:
        sla_id: SLA identifier
        metric: Metric name
        current_value: Current value
        target: Target value
        status: SLA status (met/warning/breached)
        compliance_ratio: Ratio of time SLA was met
        period: Evaluation period
        evaluated_at: Evaluation timestamp
    """

    sla_id: str = ""
    metric: str = ""
    current_value: float = 0.0
    target: float = 0.0
    status: str = "met"
    compliance_ratio: float = 1.0
    period: int = 86400
    evaluated_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


# ======================================================================
# Statistical drift detection methods
# ======================================================================


def ks_test_statistic(
    baseline: List[float],
    current: List[float],
) -> Tuple[float, float]:
    """Compute Kolmogorov-Smirnov test statistic.

    Simplified implementation for numerical distributions.

    Args:
        baseline: Baseline distribution samples
        current: Current distribution samples

    Returns:
        Tuple of (KS statistic, approximate p-value)
    """
    if not baseline or not current:
        return 0.0, 1.0

    # Combine and sort all values
    all_values = sorted(set(baseline + current))

    # Compute ECDFs
    baseline_sorted = sorted(baseline)
    current_sorted = sorted(current)

    n_baseline = len(baseline_sorted)
    n_current = len(current_sorted)

    max_diff = 0.0
    b_idx = 0
    c_idx = 0

    for val in all_values:
        # Count elements <= val in baseline
        while b_idx < n_baseline and baseline_sorted[b_idx] <= val:
            b_idx += 1
        while c_idx < n_current and current_sorted[c_idx] <= val:
            c_idx += 1

        ecdf_b = b_idx / n_baseline
        ecdf_c = c_idx / n_current
        diff = abs(ecdf_b - ecdf_c)
        max_diff = max(max_diff, diff)

    # Approximate p-value using Kolmogorov distribution
    n = len(baseline)
    m = len(current)
    en = math.sqrt(n * m / (n + m))
    lam = (en + 0.12 + 0.11 / en) * max_diff

    # Kolmogorov approximation
    if lam == 0:
        p_value = 1.0
    else:
        p_value = 2.0 * sum(
            (-1) ** (k - 1) * math.exp(-2 * k * k * lam * lam)
            for k in range(1, 101)
        )
        p_value = max(0.0, min(1.0, p_value))

    return max_diff, p_value


def psi(
    baseline_counts: List[float],
    current_counts: List[float],
    *,
    epsilon: float = 1e-10,
) -> float:
    """Compute Population Stability Index (PSI).

    PSI = sum((current_pct - baseline_pct) * ln(current_pct / baseline_pct))

    Args:
        baseline_counts: Baseline bin counts or percentages
        current_counts: Current bin counts or percentages
        epsilon: Small value to avoid division by zero

    Returns:
        PSI value
    """
    if len(baseline_counts) != len(current_counts):
        return 0.0

    # Normalize to percentages
    baseline_total = sum(baseline_counts)
    current_total = sum(current_counts)

    if baseline_total == 0 or current_total == 0:
        return 0.0

    baseline_pct = [c / baseline_total for c in baseline_counts]
    current_pct = [c / current_total for c in current_counts]

    psi_value = 0.0
    for b, c in zip(baseline_pct, current_pct):
        b_safe = max(b, epsilon)
        c_safe = max(c, epsilon)
        psi_value += (c_safe - b_safe) * math.log(c_safe / b_safe)

    return psi_value


def wasserstein_distance(
    baseline: List[float],
    current: List[float],
) -> float:
    """Compute 1D Wasserstein distance (Earth Mover's Distance).

    Args:
        baseline: Baseline samples
        current: Current samples

    Returns:
        Wasserstein distance
    """
    if not baseline or not current:
        return 0.0

    baseline_sorted = sorted(baseline)
    current_sorted = sorted(current)

    n = len(baseline_sorted)
    m = len(current_sorted)

    # Compute using quantile-based approach
    total = 0.0
    max_len = max(n, m)

    for i in range(max_len):
        # Interpolate if needed
        b_idx = min(i * n // max_len, n - 1)
        c_idx = min(i * m // max_len, m - 1)
        total += abs(baseline_sorted[b_idx] - current_sorted[c_idx])

    return total / max_len


def chi_square_test(
    observed: List[int],
    expected: List[int],
) -> Tuple[float, float]:
    """Compute chi-square test statistic and p-value.

    Simplified implementation for categorical distributions.

    Args:
        observed: Observed frequencies
        expected: Expected frequencies

    Returns:
        Tuple of (chi-square statistic, approximate p-value)
    """
    if len(observed) != len(expected) or not observed:
        return 0.0, 1.0

    chi_sq = 0.0
    df = len(observed) - 1

    for o, e in zip(observed, expected):
        if e == 0:
            continue
        chi_sq += (o - e) ** 2 / e

    # Approximate p-value using Wilson-Hilferty transformation
    if df <= 0:
        return chi_sq, 1.0

    # Normal approximation
    z = (chi_sq / df) ** (1 / 3) - (1 - 2 / (9 * df))
    z /= math.sqrt(2 / (9 * df))

    # Approximate p-value from z-score
    p_value = 2 * (1 - 0.5 * (1 + math.erf(z / math.sqrt(2))))
    p_value = max(0.0, min(1.0, p_value))

    return chi_sq, p_value


# ======================================================================
# Model Monitor
# ======================================================================


class ModelMonitor:
    """Model monitoring and drift detection.

    Monitors model inputs, outputs, and performance over time.
    Detects drift, degradation, and anomalies.

    Usage:
        monitor = ModelMonitor(model_id="my-model")
        monitor.record_prediction(input_data, output_data, ground_truth)
        drift = monitor.detect_drift()
    """

    def __init__(
        self,
        model_id: str,
        *,
        window_size: int = 1000,
        baseline_size: int = 1000,
        drift_threshold: float = 0.2,
        p_value_threshold: float = 0.05,
    ) -> None:
        """Initialize the model monitor.

        Args:
            model_id: Model identifier
            window_size: Size of the sliding window for current data
            baseline_size: Number of baseline samples to keep
            drift_threshold: PSI threshold for drift detection
            p_value_threshold: P-value threshold for statistical tests
        """
        self.model_id = model_id
        self._window_size = window_size
        self._baseline_size = baseline_size
        self._drift_threshold = drift_threshold
        self._p_value_threshold = p_value_threshold

        # Data storage
        self._baseline_inputs: Dict[str, List[float]] = {}
        self._baseline_outputs: List[float] = []
        self._baseline_metrics: Dict[str, float] = {}

        self._current_inputs: Dict[str, deque] = {}
        self._current_outputs: deque = deque(maxlen=window_size)
        self._current_labels: deque = deque(maxlen=window_size)

        # Metrics history
        self._metric_history: Dict[str, List[Tuple[str, float]]] = {}
        self._prediction_count: int = 0
        self._error_count: int = 0
        self._latency_sum: float = 0.0

        # Alerts
        self._alerts: List[MonitoringAlert] = []
        self._drift_history: List[DriftResult] = []

        # SLA tracking
        self._slas: Dict[str, SLAConfig] = {}
        self._sla_violations: Dict[str, List[str]] = {}

        # Anomaly detection state
        self._anomaly_thresholds: Dict[str, Dict[str, float]] = {}

    # ------------------------------------------------------------------
    # Baseline management
    # ------------------------------------------------------------------

    def set_baseline(
        self,
        *,
        input_features: Optional[Dict[str, List[float]]] = None,
        outputs: Optional[List[float]] = None,
        metrics: Optional[Dict[str, float]] = None,
    ) -> None:
        """Set the baseline reference data.

        Args:
            input_features: Dictionary of feature_name -> list of values
            outputs: List of model outputs
            metrics: Dictionary of baseline metric values
        """
        if input_features:
            for name, values in input_features.items():
                self._baseline_inputs[name] = list(values[: self._baseline_size])

        if outputs:
            self._baseline_outputs = list(outputs[: self._baseline_size])

        if metrics:
            self._baseline_metrics = dict(metrics)

    def update_baseline_from_current(self) -> None:
        """Update baseline from current window data."""
        for name, values in self._current_inputs.items():
            self._baseline_inputs[name] = list(values)

        self._baseline_outputs = list(self._current_outputs)

    # ------------------------------------------------------------------
    # Data recording
    # ------------------------------------------------------------------

    def record_prediction(
        self,
        input_features: Dict[str, float],
        output: float,
        *,
        ground_truth: Optional[float] = None,
        latency_ms: float = 0.0,
        is_error: bool = False,
    ) -> None:
        """Record a prediction for monitoring.

        Args:
            input_features: Input feature values
            output: Model output/prediction
            ground_truth: Ground truth label (if available)
            latency_ms: Prediction latency in milliseconds
            is_error: Whether the prediction resulted in an error
        """
        self._prediction_count += 1
        self._latency_sum += latency_ms

        if is_error:
            self._error_count += 1

        # Store input features
        for name, value in input_features.items():
            if name not in self._current_inputs:
                self._current_inputs[name] = deque(maxlen=self._window_size)
            self._current_inputs[name].append(value)

        # Store output
        self._current_outputs.append(output)

        # Store ground truth if available
        if ground_truth is not None:
            self._current_labels.append(ground_truth)

    def record_metric(
        self,
        metric_name: str,
        value: float,
        *,
        timestamp: Optional[str] = None,
    ) -> None:
        """Record a performance metric.

        Args:
            metric_name: Metric name
            value: Metric value
            timestamp: Timestamp (default = now)
        """
        if metric_name not in self._metric_history:
            self._metric_history[metric_name] = []

        ts = timestamp or datetime.now(timezone.utc).isoformat()
        self._metric_history[metric_name].append((ts, value))

        # Keep only last 10000 entries
        if len(self._metric_history[metric_name]) > 10000:
            self._metric_history[metric_name] = self._metric_history[metric_name][
                -10000:
            ]

    # ------------------------------------------------------------------
    # Drift detection
    # ------------------------------------------------------------------

    def detect_data_drift(
        self,
        feature_name: Optional[str] = None,
    ) -> List[DriftResult]:
        """Detect data drift in input features.

        Args:
            feature_name: Specific feature to check (None = all)

        Returns:
            List of drift detection results
        """
        results: List[DriftResult] = []

        features_to_check = (
            [feature_name] if feature_name else list(self._current_inputs.keys())
        )

        for name in features_to_check:
            baseline = self._baseline_inputs.get(name, [])
            current = list(self._current_inputs.get(name, []))

            if not baseline or not current:
                continue

            # Compute PSI
            # Create histogram bins
            all_vals = baseline + current
            if not all_vals:
                continue

            min_val = min(all_vals)
            max_val = max(all_vals)
            n_bins = 10

            if max_val == min_val:
                continue

            baseline_counts = [0] * n_bins
            current_counts = [0] * n_bins

            for v in baseline:
                idx = min(int((v - min_val) / (max_val - min_val) * n_bins), n_bins - 1)
                baseline_counts[idx] += 1

            for v in current:
                idx = min(int((v - min_val) / (max_val - min_val) * n_bins), n_bins - 1)
                current_counts[idx] += 1

            psi_value = psi(baseline_counts, current_counts)

            # Also compute KS statistic
            ks_stat, p_val = ks_test_statistic(baseline, current)

            # Determine severity
            severity = self._psi_severity(psi_value)

            result = DriftResult(
                drift_type=DriftType.FEATURE_DRIFT,
                severity=severity,
                feature_name=name,
                metric=psi_value,
                threshold=self._drift_threshold,
                p_value=p_val,
                baseline_size=len(baseline),
                current_size=len(current),
                details={
                    "ks_statistic": ks_stat,
                    "ks_p_value": p_val,
                    "psi_value": psi_value,
                    "baseline_mean": sum(baseline) / len(baseline) if baseline else 0,
                    "current_mean": sum(current) / len(current) if current else 0,
                },
            )

            results.append(result)

            if severity != DriftSeverity.NONE:
                self._drift_history.append(result)
                self._maybe_alert(result)

        return results

    def detect_prediction_drift(self) -> DriftResult:
        """Detect drift in model predictions/outputs.

        Returns:
            Drift detection result
        """
        baseline = self._baseline_outputs
        current = list(self._current_outputs)

        if not baseline or not current:
            return DriftResult(
                drift_type=DriftType.PREDICTION_DRIFT,
                severity=DriftSeverity.NONE,
                feature_name="output",
                metric=0.0,
                threshold=self._drift_threshold,
                details={"reason": "insufficient_data"},
            )

        # Compute PSI on outputs
        all_vals = baseline + current
        min_val = min(all_vals)
        max_val = max(all_vals)
        n_bins = 10

        if max_val == min_val:
            return DriftResult(
                drift_type=DriftType.PREDICTION_DRIFT,
                severity=DriftSeverity.NONE,
                feature_name="output",
                metric=0.0,
                threshold=self._drift_threshold,
                details={"reason": "zero_variance"},
            )

        baseline_counts = [0] * n_bins
        current_counts = [0] * n_bins

        for v in baseline:
            idx = min(int((v - min_val) / (max_val - min_val) * n_bins), n_bins - 1)
            baseline_counts[idx] += 1

        for v in current:
            idx = min(int((v - min_val) / (max_val - min_val) * n_bins), n_bins - 1)
            current_counts[idx] += 1

        psi_value = psi(baseline_counts, current_counts)
        wd = wasserstein_distance(baseline, current)
        severity = self._psi_severity(psi_value)

        result = DriftResult(
            drift_type=DriftType.PREDICTION_DRIFT,
            severity=severity,
            feature_name="output",
            metric=psi_value,
            threshold=self._drift_threshold,
            baseline_size=len(baseline),
            current_size=len(current),
            details={
                "wasserstein_distance": wd,
                "psi_value": psi_value,
                "baseline_mean": sum(baseline) / len(baseline),
                "current_mean": sum(current) / len(current),
            },
        )

        if severity != DriftSeverity.NONE:
            self._drift_history.append(result)
            self._maybe_alert(result)

        return result

    def detect_performance_drift(
        self,
        metric_name: str,
    ) -> DriftResult:
        """Detect performance drift (model degradation).

        Args:
            metric_name: Name of the performance metric

        Returns:
            Drift detection result
        """
        baseline_val = self._baseline_metrics.get(metric_name)
        history = self._metric_history.get(metric_name, [])

        if baseline_val is None or not history:
            return DriftResult(
                drift_type=DriftType.PERFORMANCE_DRIFT,
                severity=DriftSeverity.NONE,
                feature_name=metric_name,
                metric=0.0,
                threshold=0.0,
                details={"reason": "insufficient_data"},
            )

        # Compute recent average
        recent_values = [v for _, v in history[-100:]]
        if not recent_values:
            return DriftResult(
                drift_type=DriftType.PERFORMANCE_DRIFT,
                severity=DriftSeverity.NONE,
                feature_name=metric_name,
                metric=0.0,
                threshold=0.0,
                details={"reason": "no_recent_data"},
            )

        recent_avg = sum(recent_values) / len(recent_values)

        # Compute relative change
        if baseline_val == 0:
            relative_change = 0.0
        else:
            relative_change = abs(recent_avg - baseline_val) / abs(baseline_val)

        # Determine severity
        severity = DriftSeverity.NONE
        if relative_change > 0.5:
            severity = DriftSeverity.CRITICAL
        elif relative_change > 0.3:
            severity = DriftSeverity.HIGH
        elif relative_change > 0.2:
            severity = DriftSeverity.MEDIUM
        elif relative_change > 0.1:
            severity = DriftSeverity.LOW

        result = DriftResult(
            drift_type=DriftType.PERFORMANCE_DRIFT,
            severity=severity,
            feature_name=metric_name,
            metric=relative_change,
            threshold=0.1,
            baseline_size=1,
            current_size=len(recent_values),
            details={
                "baseline_value": baseline_val,
                "current_value": recent_avg,
                "absolute_change": recent_avg - baseline_val,
                "relative_change": relative_change,
            },
        )

        if severity != DriftSeverity.NONE:
            self._drift_history.append(result)
            self._maybe_alert(result)

        return result

    def detect_all_drift(self) -> Dict[str, List[DriftResult]]:
        """Run all drift detection checks.

        Returns:
            Dictionary of drift_type -> list of results
        """
        return {
            "data_drift": self.detect_data_drift(),
            "prediction_drift": [self.detect_prediction_drift()],
            "performance_drift": [
                self.detect_performance_drift(m)
                for m in self._baseline_metrics
            ],
        }

    # ------------------------------------------------------------------
    # Anomaly detection
    # ------------------------------------------------------------------

    def detect_anomalies(
        self,
        metric_name: str,
        value: float,
        *,
        method: str = "zscore",
        threshold: float = 3.0,
    ) -> bool:
        """Detect if a value is anomalous.

        Args:
            metric_name: Metric name
            value: Value to check
            method: Detection method ("zscore", "iqr")
            threshold: Anomaly threshold

        Returns:
            True if the value is anomalous
        """
        history = [v for _, v in self._metric_history.get(metric_name, [])]

        if len(history) < 10:
            return False

        if method == "zscore":
            mean = sum(history) / len(history)
            variance = sum((x - mean) ** 2 for x in history) / len(history)
            std = math.sqrt(variance)

            if std == 0:
                return False

            z_score = abs(value - mean) / std
            return z_score > threshold

        elif method == "iqr":
            sorted_vals = sorted(history)
            n = len(sorted_vals)
            q1 = sorted_vals[n // 4]
            q3 = sorted_vals[3 * n // 4]
            iqr = q3 - q1

            lower = q1 - threshold * iqr
            upper = q3 + threshold * iqr

            return value < lower or value > upper

        return False

    # ------------------------------------------------------------------
    # SLA management
    # ------------------------------------------------------------------

    def add_sla(self, sla: SLAConfig) -> None:
        """Add an SLA to monitor.

        Args:
            sla: SLA configuration
        """
        self._slas[sla.sla_id] = sla
        self._sla_violations[sla.sla_id] = []

    def evaluate_sla(self, sla_id: str) -> Optional[SLAResult]:
        """Evaluate an SLA.

        Args:
            sla_id: SLA identifier

        Returns:
            SLA evaluation result, or None if not found
        """
        sla = self._slas.get(sla_id)
        if not sla:
            return None

        history = self._metric_history.get(sla.metric, [])
        if not history:
            return SLAResult(
                sla_id=sla_id,
                metric=sla.metric,
                current_value=0.0,
                target=sla.target,
                status="no_data",
                compliance_ratio=0.0,
                period=sla.period,
            )

        # Get recent values within the period
        recent = [v for _, v in history[-1000:]]
        if not recent:
            return SLAResult(
                sla_id=sla_id,
                metric=sla.metric,
                current_value=0.0,
                target=sla.target,
                status="no_data",
                compliance_ratio=0.0,
                period=sla.period,
            )

        current_value = recent[-1]

        # Compute compliance (assuming higher is better for accuracy-like metrics)
        # For latency-like metrics, this would be inverted
        compliant = sum(1 for v in recent if v >= sla.target)
        compliance_ratio = compliant / len(recent) if recent else 0.0

        if current_value >= sla.target:
            status = "met"
        elif current_value >= sla.threshold:
            status = "warning"
        else:
            status = "breached"
            self._sla_violations[sla_id].append(
                datetime.now(timezone.utc).isoformat()
            )

        return SLAResult(
            sla_id=sla_id,
            metric=sla.metric,
            current_value=current_value,
            target=sla.target,
            status=status,
            compliance_ratio=compliance_ratio,
            period=sla.period,
        )

    # ------------------------------------------------------------------
    # Alerting
    # ------------------------------------------------------------------

    def _maybe_alert(self, drift: DriftResult) -> None:
        """Create an alert if drift is significant."""
        if drift.severity in (DriftSeverity.HIGH, DriftSeverity.CRITICAL):
            level = (
                AlertLevel.CRITICAL
                if drift.severity == DriftSeverity.CRITICAL
                else AlertLevel.ERROR
            )

            alert = MonitoringAlert(
                level=level,
                title=f"{drift.drift_type.value} detected in {drift.feature_name}",
                description=(
                    f"Drift severity: {drift.severity.value}. "
                    f"Drift metric: {drift.metric:.4f}, "
                    f"threshold: {drift.threshold}"
                ),
                model_id=self.model_id,
                metric_name=drift.feature_name,
                current_value=drift.metric,
                threshold_value=drift.threshold,
                drift_result=drift,
            )
            self._alerts.append(alert)

    def get_alerts(
        self,
        *,
        level: Optional[AlertLevel] = None,
        acknowledged: Optional[bool] = None,
    ) -> List[MonitoringAlert]:
        """Get alerts, optionally filtered.

        Args:
            level: Filter by alert level
            acknowledged: Filter by acknowledgment status

        Returns:
            List of matching alerts
        """
        alerts = list(self._alerts)

        if level:
            alerts = [a for a in alerts if a.level == level]
        if acknowledged is not None:
            alerts = [a for a in alerts if a.acknowledged == acknowledged]

        return alerts

    def acknowledge_alert(
        self, alert_id: str, *, acknowledged_by: str = ""
    ) -> Optional[MonitoringAlert]:
        """Acknowledge an alert.

        Args:
            alert_id: Alert identifier
            acknowledged_by: Person acknowledging

        Returns:
            Updated alert, or None if not found
        """
        for alert in self._alerts:
            if alert.alert_id == alert_id:
                alert.acknowledged = True
                alert.acknowledged_by = acknowledged_by
                alert.acknowledged_at = datetime.now(timezone.utc).isoformat()
                return alert
        return None

    # ------------------------------------------------------------------
    # Helper methods
    # ------------------------------------------------------------------

    def _psi_severity(self, psi_value: float) -> DriftSeverity:
        """Determine drift severity from PSI value.

        Standard interpretation:
        - PSI < 0.1: No significant drift
        - 0.1 <= PSI < 0.2: Slight drift (monitor)
        - 0.2 <= PSI < 0.3: Moderate drift
        - PSI >= 0.3: Severe drift
        """
        if psi_value < 0.1:
            return DriftSeverity.NONE
        elif psi_value < 0.2:
            return DriftSeverity.LOW
        elif psi_value < 0.3:
            return DriftSeverity.MEDIUM
        elif psi_value < 0.5:
            return DriftSeverity.HIGH
        else:
            return DriftSeverity.CRITICAL

    # ------------------------------------------------------------------
    # Statistics
    # ------------------------------------------------------------------

    def get_stats(self) -> Dict[str, Any]:
        """Get monitor statistics.

        Returns:
            Dictionary of statistics
        """
        avg_latency = (
            self._latency_sum / self._prediction_count
            if self._prediction_count > 0
            else 0.0
        )

        error_rate = (
            self._error_count / self._prediction_count
            if self._prediction_count > 0
            else 0.0
        )

        return {
            "model_id": self.model_id,
            "predictions_recorded": self._prediction_count,
            "error_count": self._error_count,
            "error_rate": error_rate,
            "avg_latency_ms": avg_latency,
            "tracked_features": len(self._current_inputs),
            "tracked_metrics": len(self._metric_history),
            "total_alerts": len(self._alerts),
            "unacknowledged_alerts": sum(
                1 for a in self._alerts if not a.acknowledged
            ),
            "drift_events": len(self._drift_history),
            "slas": len(self._slas),
        }

    def __repr__(self) -> str:
        return (
            f"ModelMonitor(model_id={self.model_id}, "
            f"predictions={self._prediction_count})"
        )
