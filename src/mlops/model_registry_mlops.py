"""MLOps Model Registry.

MLOps-focused model registry with:
- Model versioning
- Model staging (development -> staging -> production)
- Model lineage tracking
- Model metadata and documentation
- Approval workflow
- Rollback support
- Model performance monitoring
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum, auto
from typing import Any, Callable, Dict, List, Optional, Set, Tuple
from uuid import uuid4


class ModelStage(str, Enum):
    """Model staging levels."""

    NONE = "none"
    DEVELOPMENT = "development"
    STAGING = "staging"
    PRODUCTION = "production"
    ARCHIVED = "archived"


class ApprovalStatus(str, Enum):
    """Approval workflow status."""

    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    REVIEWING = "reviewing"
    REVOKED = "revoked"


@dataclass
class ModelVersionMLOps:
    """A specific version of a model in the MLOps registry.

    Attributes:
        version_id: Unique version identifier
        model_name: Model name
        version: Version string (semantic)
        stage: Current deployment stage
        description: Version description
        metrics: Performance metrics
        artifacts: List of artifact paths
        created_by: Creator identifier
        created_at: Creation timestamp
        training_data: Reference to training data
        code_commit: Code commit hash
        approved_by: Approver identifier
        approved_at: Approval timestamp
        approval_status: Approval status
        lineage: List of predecessor version IDs
        metadata: Additional metadata
    """

    version_id: str = field(default_factory=lambda: str(uuid4()))
    model_name: str = ""
    version: str = "1.0.0"
    stage: ModelStage = ModelStage.NONE
    description: str = ""
    metrics: Dict[str, float] = field(default_factory=dict)
    artifacts: List[str] = field(default_factory=list)
    created_by: str = ""
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    training_data: str = ""
    code_commit: str = ""
    approved_by: Optional[str] = None
    approved_at: Optional[str] = None
    approval_status: ApprovalStatus = ApprovalStatus.PENDING
    lineage: List[str] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)

    @property
    def is_approved(self) -> bool:
        """Whether this version is approved."""
        return self.approval_status == ApprovalStatus.APPROVED

    @property
    def is_production(self) -> bool:
        """Whether this version is in production."""
        return self.stage == ModelStage.PRODUCTION


@dataclass
class ModelLineage:
    """Model lineage information.

    Tracks the ancestry and descendants of a model version.

    Attributes:
        version_id: Version identifier
        parents: Parent version IDs (versions this was derived from)
        children: Child version IDs (versions derived from this)
        training_datasets: Datasets used for training
        base_models: Base models used (for fine-tuning)
        transformations: List of transformations applied
    """

    version_id: str = ""
    parents: List[str] = field(default_factory=list)
    children: List[str] = field(default_factory=list)
    training_datasets: List[str] = field(default_factory=list)
    base_models: List[str] = field(default_factory=list)
    transformations: List[str] = field(default_factory=list)


@dataclass
class ApprovalRequest:
    """An approval request for a model version.

    Attributes:
        request_id: Unique request identifier
        version_id: Version being approved
        requester: Person requesting approval
        approver: Assigned approver
        status: Current status
        comments: List of comment entries
        created_at: Creation timestamp
        resolved_at: Resolution timestamp
        decision: Final decision (approved/rejected)
    """

    request_id: str = field(default_factory=lambda: str(uuid4()))
    version_id: str = ""
    requester: str = ""
    approver: str = ""
    status: ApprovalStatus = ApprovalStatus.PENDING
    comments: List[Dict[str, Any]] = field(default_factory=list)
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    resolved_at: Optional[str] = None
    decision: Optional[str] = None


@dataclass
class StageTransition:
    """A record of a stage transition.

    Attributes:
        transition_id: Unique transition identifier
        version_id: Version that was transitioned
        from_stage: Previous stage
        to_stage: New stage
        performed_by: Person who performed the transition
        reason: Reason for the transition
        timestamp: Transition timestamp
    """

    transition_id: str = field(default_factory=lambda: str(uuid4()))
    version_id: str = ""
    from_stage: ModelStage = ModelStage.NONE
    to_stage: ModelStage = ModelStage.NONE
    performed_by: str = ""
    reason: str = ""
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


class MLOpsModelRegistry:
    """MLOps-focused model registry.

    Manages model versions, staging, approval workflows, lineage,
    and performance monitoring.

    Usage:
        registry = MLOpsModelRegistry()
        version = registry.register_version("my-model", "1.0.0")
        registry.transition_stage(version.version_id, ModelStage.STAGING)
    """

    def __init__(self) -> None:
        """Initialize the MLOps model registry."""
        self._models: Dict[str, List[str]] = {}  # model_name -> list of version IDs
        self._versions: Dict[str, ModelVersionMLOps] = {}
        self._lineage: Dict[str, ModelLineage] = {}
        self._approvals: Dict[str, ApprovalRequest] = {}
        self._transitions: List[StageTransition] = []
        self._production_versions: Dict[str, str] = {}  # model_name -> version_id
        self._staging_versions: Dict[str, str] = {}  # model_name -> version_id

    # ------------------------------------------------------------------
    # Model registration
    # ------------------------------------------------------------------

    def register_version(
        self,
        model_name: str,
        version: str,
        *,
        description: str = "",
        metrics: Optional[Dict[str, float]] = None,
        artifacts: Optional[List[str]] = None,
        created_by: str = "",
        training_data: str = "",
        code_commit: str = "",
        parent_version_id: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> ModelVersionMLOps:
        """Register a new model version.

        Args:
            model_name: Model name
            version: Version string
            description: Version description
            metrics: Performance metrics
            artifacts: List of artifact paths
            created_by: Creator identifier
            training_data: Training data reference
            code_commit: Code commit hash
            parent_version_id: Parent version (for lineage)
            metadata: Additional metadata

        Returns:
            Registered model version
        """
        model_version = ModelVersionMLOps(
            model_name=model_name,
            version=version,
            description=description,
            metrics=metrics or {},
            artifacts=artifacts or [],
            created_by=created_by,
            training_data=training_data,
            code_commit=code_commit,
            metadata=metadata or {},
        )

        self._versions[model_version.version_id] = model_version

        if model_name not in self._models:
            self._models[model_name] = []
        self._models[model_name].append(model_version.version_id)

        # Set up lineage
        lineage = ModelLineage(version_id=model_version.version_id)
        if parent_version_id and parent_version_id in self._versions:
            lineage.parents.append(parent_version_id)
            model_version.lineage.append(parent_version_id)

            # Update parent's children
            if parent_version_id not in self._lineage:
                self._lineage[parent_version_id] = ModelLineage(
                    version_id=parent_version_id
                )
            self._lineage[parent_version_id].children.append(
                model_version.version_id
            )

        if training_data:
            lineage.training_datasets.append(training_data)

        self._lineage[model_version.version_id] = lineage

        return model_version

    def get_version(self, version_id: str) -> Optional[ModelVersionMLOps]:
        """Get a model version by ID.

        Args:
            version_id: Version identifier

        Returns:
            Model version if found, None otherwise
        """
        return self._versions.get(version_id)

    def get_latest_version(
        self, model_name: str
    ) -> Optional[ModelVersionMLOps]:
        """Get the latest version of a model.

        Args:
            model_name: Model name

        Returns:
            Latest version, or None if no versions
        """
        version_ids = self._models.get(model_name, [])
        if not version_ids:
            return None

        # Return the most recently created
        latest = max(
            version_ids,
            key=lambda vid: self._versions[vid].created_at,
        )
        return self._versions[latest]

    def get_production_version(
        self, model_name: str
    ) -> Optional[ModelVersionMLOps]:
        """Get the production version of a model.

        Args:
            model_name: Model name

        Returns:
            Production version, or None
        """
        version_id = self._production_versions.get(model_name)
        if version_id:
            return self._versions.get(version_id)
        return None

    def list_models(self) -> List[str]:
        """List all registered model names.

        Returns:
            List of model names
        """
        return list(self._models.keys())

    def list_versions(
        self,
        model_name: str,
        *,
        stage: Optional[ModelStage] = None,
    ) -> List[ModelVersionMLOps]:
        """List versions of a model.

        Args:
            model_name: Model name
            stage: Filter by stage

        Returns:
            List of model versions
        """
        version_ids = self._models.get(model_name, [])
        versions = [self._versions[vid] for vid in version_ids if vid in self._versions]

        if stage:
            versions = [v for v in versions if v.stage == stage]

        # Sort by creation time, newest first
        versions.sort(key=lambda v: v.created_at, reverse=True)
        return versions

    # ------------------------------------------------------------------
    # Stage management
    # ------------------------------------------------------------------

    def transition_stage(
        self,
        version_id: str,
        target_stage: ModelStage,
        *,
        performed_by: str = "",
        reason: str = "",
    ) -> Optional[ModelVersionMLOps]:
        """Transition a model version to a new stage.

        Args:
            version_id: Version identifier
            target_stage: Target stage
            performed_by: Person performing the transition
            reason: Reason for transition

        Returns:
            Updated model version, or None if not found
        """
        version = self._versions.get(version_id)
        if not version:
            return None

        from_stage = version.stage

        # Record transition
        transition = StageTransition(
            version_id=version_id,
            from_stage=from_stage,
            to_stage=target_stage,
            performed_by=performed_by,
            reason=reason,
        )
        self._transitions.append(transition)

        # Update stage
        version.stage = target_stage

        # Update production/staging trackers
        if target_stage == ModelStage.PRODUCTION:
            self._production_versions[version.model_name] = version_id
        elif target_stage == ModelStage.STAGING:
            self._staging_versions[version.model_name] = version_id
        elif from_stage == ModelStage.PRODUCTION:
            if self._production_versions.get(version.model_name) == version_id:
                del self._production_versions[version.model_name]
        elif from_stage == ModelStage.STAGING:
            if self._staging_versions.get(version.model_name) == version_id:
                del self._staging_versions[version.model_name]

        return version

    def promote_to_staging(
        self,
        version_id: str,
        *,
        performed_by: str = "",
        reason: str = "Promoted to staging",
    ) -> Optional[ModelVersionMLOps]:
        """Promote a version to staging.

        Args:
            version_id: Version identifier
            performed_by: Person performing the promotion
            reason: Reason for promotion

        Returns:
            Updated version
        """
        return self.transition_stage(
            version_id,
            ModelStage.STAGING,
            performed_by=performed_by,
            reason=reason,
        )

    def promote_to_production(
        self,
        version_id: str,
        *,
        performed_by: str = "",
        reason: str = "Promoted to production",
    ) -> Optional[ModelVersionMLOps]:
        """Promote a version to production.

        Args:
            version_id: Version identifier
            performed_by: Person performing the promotion
            reason: Reason for promotion

        Returns:
            Updated version
        """
        return self.transition_stage(
            version_id,
            ModelStage.PRODUCTION,
            performed_by=performed_by,
            reason=reason,
        )

    def rollback(
        self,
        model_name: str,
        *,
        performed_by: str = "",
        reason: str = "Rollback",
    ) -> Optional[ModelVersionMLOps]:
        """Rollback to the previous production version.

        Args:
            model_name: Model name
            performed_by: Person performing the rollback
            reason: Reason for rollback

        Returns:
            New production version, or None if rollback not possible
        """
        current_prod_id = self._production_versions.get(model_name)
        if not current_prod_id:
            return None

        # Get all versions except current production and archived
        all_versions = self.list_versions(model_name)
        candidates = [
            v for v in all_versions
            if v.version_id != current_prod_id
            and v.stage != ModelStage.ARCHIVED
        ]

        if not candidates:
            return None

        # Pick the newest candidate (first in the sorted list)
        rollback_version = candidates[0]

        # Archive current production
        self.transition_stage(
            current_prod_id,
            ModelStage.ARCHIVED,
            performed_by=performed_by,
            reason=f"Archived during rollback: {reason}",
        )

        # Promote rollback version to production
        self.transition_stage(
            rollback_version.version_id,
            ModelStage.PRODUCTION,
            performed_by=performed_by,
            reason=reason,
        )

        return rollback_version

    def get_transition_history(
        self, version_id: str
    ) -> List[StageTransition]:
        """Get the stage transition history for a version.

        Args:
            version_id: Version identifier

        Returns:
            List of transitions, oldest first
        """
        return [
            t for t in self._transitions if t.version_id == version_id
        ]

    # ------------------------------------------------------------------
    # Approval workflow
    # ------------------------------------------------------------------

    def request_approval(
        self,
        version_id: str,
        *,
        requester: str = "",
        approver: str = "",
    ) -> Optional[ApprovalRequest]:
        """Request approval for a model version.

        Args:
            version_id: Version identifier
            requester: Person requesting approval
            approver: Assigned approver

        Returns:
            Created approval request, or None if version not found
        """
        version = self._versions.get(version_id)
        if not version:
            return None

        request = ApprovalRequest(
            version_id=version_id,
            requester=requester,
            approver=approver,
            status=ApprovalStatus.PENDING,
        )

        self._approvals[request.request_id] = request
        version.approval_status = ApprovalStatus.PENDING

        return request

    def review_approval(
        self,
        request_id: str,
        *,
        reviewer: str = "",
        comments: str = "",
    ) -> Optional[ApprovalRequest]:
        """Mark an approval request as being reviewed.

        Args:
            request_id: Request identifier
            reviewer: Reviewer identifier
            comments: Review comments

        Returns:
            Updated approval request
        """
        request = self._approvals.get(request_id)
        if not request:
            return None

        request.status = ApprovalStatus.REVIEWING
        request.comments.append(
            {
                "author": reviewer,
                "text": comments,
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "type": "review_start",
            }
        )

        version = self._versions.get(request.version_id)
        if version:
            version.approval_status = ApprovalStatus.REVIEWING

        return request

    def approve_version(
        self,
        request_id: str,
        *,
        approver: str = "",
        comments: str = "",
    ) -> Optional[ApprovalRequest]:
        """Approve a model version.

        Args:
            request_id: Approval request ID
            approver: Approver identifier
            comments: Approval comments

        Returns:
            Updated approval request
        """
        request = self._approvals.get(request_id)
        if not request:
            return None

        request.status = ApprovalStatus.APPROVED
        request.decision = "approved"
        request.resolved_at = datetime.now(timezone.utc).isoformat()
        request.comments.append(
            {
                "author": approver,
                "text": comments,
                "timestamp": request.resolved_at,
                "type": "approval",
            }
        )

        version = self._versions.get(request.version_id)
        if version:
            version.approval_status = ApprovalStatus.APPROVED
            version.approved_by = approver
            version.approved_at = request.resolved_at

        return request

    def reject_version(
        self,
        request_id: str,
        *,
        approver: str = "",
        reason: str = "",
    ) -> Optional[ApprovalRequest]:
        """Reject a model version.

        Args:
            request_id: Approval request ID
            approver: Approver identifier
            reason: Rejection reason

        Returns:
            Updated approval request
        """
        request = self._approvals.get(request_id)
        if not request:
            return None

        request.status = ApprovalStatus.REJECTED
        request.decision = "rejected"
        request.resolved_at = datetime.now(timezone.utc).isoformat()
        request.comments.append(
            {
                "author": approver,
                "text": reason,
                "timestamp": request.resolved_at,
                "type": "rejection",
            }
        )

        version = self._versions.get(request.version_id)
        if version:
            version.approval_status = ApprovalStatus.REJECTED

        return request

    # ------------------------------------------------------------------
    # Lineage tracking
    # ------------------------------------------------------------------

    def get_lineage(self, version_id: str) -> Optional[ModelLineage]:
        """Get lineage information for a version.

        Args:
            version_id: Version identifier

        Returns:
            Lineage information, or None if not found
        """
        return self._lineage.get(version_id)

    def get_full_lineage_tree(self, version_id: str) -> Dict[str, Any]:
        """Get the full lineage tree for a version.

        Args:
            version_id: Version identifier

        Returns:
            Nested lineage tree
        """
        version = self._versions.get(version_id)
        if not version:
            return {}

        def build_tree(vid: str, visited: Set[str]) -> Dict[str, Any]:
            if vid in visited:
                return {"version_id": vid, "error": "circular_reference"}
            visited.add(vid)

            v = self._versions.get(vid)
            lineage = self._lineage.get(vid)

            if not v:
                return {"version_id": vid, "not_found": True}

            children = []
            if lineage:
                for child_id in lineage.children:
                    children.append(build_tree(child_id, visited.copy()))

            return {
                "version_id": vid,
                "model_name": v.model_name,
                "version": v.version,
                "stage": v.stage.value,
                "children": children,
            }

        return build_tree(version_id, set())

    # ------------------------------------------------------------------
    # Performance monitoring
    # ------------------------------------------------------------------

    def update_metrics(
        self,
        version_id: str,
        metrics: Dict[str, float],
    ) -> Optional[ModelVersionMLOps]:
        """Update performance metrics for a version.

        Args:
            version_id: Version identifier
            metrics: Dictionary of metric_name -> value

        Returns:
            Updated model version
        """
        version = self._versions.get(version_id)
        if not version:
            return None

        version.metrics.update(metrics)
        return version

    def compare_versions(
        self,
        model_name: str,
        *,
        metric: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Compare all versions of a model.

        Args:
            model_name: Model name
            metric: Specific metric to compare (None = all)

        Returns:
            Comparison data
        """
        versions = self.list_versions(model_name)
        if not versions:
            return {"model": model_name, "versions": {}}

        comparison: Dict[str, Any] = {"model": model_name, "versions": {}}

        for version in versions:
            version_data = {
                "version": version.version,
                "stage": version.stage.value,
                "created_at": version.created_at,
                "metrics": dict(version.metrics),
            }
            comparison["versions"][version.version_id] = version_data

        # Find best version for each metric
        all_metrics: Set[str] = set()
        for v in versions:
            all_metrics.update(v.metrics.keys())

        comparison["best_by_metric"] = {}
        for metric_name in all_metrics:
            if metric and metric_name != metric:
                continue
            best = max(
                versions,
                key=lambda v: v.metrics.get(metric_name, float("-inf")),
            )
            comparison["best_by_metric"][metric_name] = {
                "version_id": best.version_id,
                "version": best.version,
                "value": best.metrics[metric_name],
            }

        return comparison

    # ------------------------------------------------------------------
    # Statistics
    # ------------------------------------------------------------------

    def get_stats(self) -> Dict[str, Any]:
        """Get registry statistics.

        Returns:
            Dictionary of statistics
        """
        stage_counts: Dict[str, int] = {}
        for v in self._versions.values():
            key = v.stage.value
            stage_counts[key] = stage_counts.get(key, 0) + 1

        return {
            "models": len(self._models),
            "total_versions": len(self._versions),
            "versions_by_stage": stage_counts,
            "production_models": len(self._production_versions),
            "staging_models": len(self._staging_versions),
            "approvals_pending": sum(
                1
                for a in self._approvals.values()
                if a.status == ApprovalStatus.PENDING
            ),
            "total_transitions": len(self._transitions),
        }

    def __repr__(self) -> str:
        return (
            f"MLOpsModelRegistry(models={len(self._models)}, "
            f"versions={len(self._versions)})"
        )
