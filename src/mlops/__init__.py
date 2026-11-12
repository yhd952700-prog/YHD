"""MLOps Module for LiuHao AI OS.

Provides MLOps capabilities including:
- Experiment tracking and management
- Model registry with staging and approval workflows
- Model monitoring with drift detection
- Feature store with online/offline serving

Note: This module extends the existing MLOps module with
enhanced production-grade features.
"""

from .experiment_tracker import (
    RunStatus,
    ArtifactType,
    Artifact,
    MetricLog,
    ExperimentRun,
    Experiment,
    ExperimentTracker,
)

from .model_registry_mlops import (
    ModelStage,
    ApprovalStatus,
    ModelVersionMLOps,
    ModelLineage,
    ApprovalRequest,
    StageTransition,
    MLOpsModelRegistry,
)

from .monitoring import (
    DriftType,
    DriftSeverity,
    AlertLevel,
    DriftResult,
    MonitoringAlert,
    SLAConfig,
    SLAResult,
    ModelMonitor,
    # Statistical functions
    ks_test_statistic,
    psi,
    wasserstein_distance,
    chi_square_test,
)

from .feature_store import (
    FeatureType,
    FeatureSource,
    ServingMode,
    FeatureDefinition,
    FeatureValue,
    FeatureView,
    FeatureLineage,
    FeatureStore,
)

__all__ = [
    # Experiment tracking
    "RunStatus",
    "ArtifactType",
    "Artifact",
    "MetricLog",
    "ExperimentRun",
    "Experiment",
    "ExperimentTracker",
    # Model registry (MLOps)
    "ModelStage",
    "ApprovalStatus",
    "ModelVersionMLOps",
    "ModelLineage",
    "ApprovalRequest",
    "StageTransition",
    "MLOpsModelRegistry",
    # Monitoring
    "DriftType",
    "DriftSeverity",
    "AlertLevel",
    "DriftResult",
    "MonitoringAlert",
    "SLAConfig",
    "SLAResult",
    "ModelMonitor",
    "ks_test_statistic",
    "psi",
    "wasserstein_distance",
    "chi_square_test",
    # Feature store
    "FeatureType",
    "FeatureSource",
    "ServingMode",
    "FeatureDefinition",
    "FeatureValue",
    "FeatureView",
    "FeatureLineage",
    "FeatureStore",
]
