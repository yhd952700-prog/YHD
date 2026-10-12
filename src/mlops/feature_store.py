"""Feature Store for MLOps.

Provides feature store interface with:
- Feature definition
- Feature computation
- Feature caching
- Feature versioning
- Feature serving (online/offline)
- Feature validation
- Feature lineage
"""

from __future__ import annotations

import hashlib
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum, auto
from typing import Any, Callable, Dict, List, Optional, Set, Tuple, Union
from uuid import uuid4


class FeatureType(str, Enum):
    """Types of features."""

    NUMERIC = "numeric"
    CATEGORICAL = "categorical"
    TEXT = "text"
    EMBEDDING = "embedding"
    BOOLEAN = "boolean"
    DATETIME = "datetime"
    LIST = "list"
    MAP = "map"
    IMAGE = "image"
    AUDIO = "audio"


class FeatureSource(str, Enum):
    """Sources of features."""

    RAW = "raw"
    DERIVED = "derived"
    AGGREGATED = "aggregated"
    EXTERNAL = "external"
    MODEL_OUTPUT = "model_output"


class ServingMode(str, Enum):
    """Feature serving modes."""

    ONLINE = "online"
    OFFLINE = "offline"
    BOTH = "both"


@dataclass
class FeatureDefinition:
    """Definition of a feature.

    Attributes:
        feature_name: Unique feature name
        feature_type: Type of feature
        source: Feature source type
        description: Feature description
        dtype: Data type (e.g., "float32", "int64", "string")
        shape: Tensor shape (for array features)
        default_value: Default value when feature is missing
        valid_range: Valid value range [min, max] for numeric
        valid_categories: Valid categories for categorical
        tags: Categorization tags
        version: Feature version
        created_at: Creation timestamp
        updated_at: Last update timestamp
        owners: List of feature owners
        serving_mode: Serving mode (online/offline/both)
        ttl_seconds: Cache TTL in seconds
        entity_keys: Entity keys this feature is associated with
    """

    feature_name: str
    feature_type: FeatureType = FeatureType.NUMERIC
    source: FeatureSource = FeatureSource.RAW
    description: str = ""
    dtype: str = "float32"
    shape: Optional[List[int]] = None
    default_value: Any = None
    valid_range: Optional[Tuple[float, float]] = None
    valid_categories: Optional[List[str]] = None
    tags: List[str] = field(default_factory=list)
    version: str = "1.0.0"
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    updated_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    owners: List[str] = field(default_factory=list)
    serving_mode: ServingMode = ServingMode.BOTH
    ttl_seconds: int = 3600
    entity_keys: List[str] = field(default_factory=list)

    def validate(self, value: Any) -> Tuple[bool, List[str]]:
        """Validate a feature value.

        Args:
            value: Value to validate

        Returns:
            Tuple of (is_valid, list of errors)
        """
        errors: List[str] = []

        # Check None vs default
        if value is None:
            if self.default_value is not None:
                return True, []
            errors.append("Value is None and no default")
            return False, errors

        # Range validation for numeric
        if self.feature_type == FeatureType.NUMERIC and self.valid_range:
            try:
                val = float(value)
                min_val, max_val = self.valid_range
                if val < min_val or val > max_val:
                    errors.append(
                        f"Value {val} outside valid range [{min_val}, {max_val}]"
                    )
            except (ValueError, TypeError):
                errors.append(f"Cannot convert value to numeric: {value}")

        # Category validation
        if self.feature_type == FeatureType.CATEGORICAL and self.valid_categories:
            if value not in self.valid_categories:
                errors.append(
                    f"Value '{value}' not in valid categories: {self.valid_categories}"
                )

        # Type check
        if self.feature_type == FeatureType.BOOLEAN:
            if not isinstance(value, bool):
                errors.append(f"Expected boolean, got {type(value).__name__}")

        return len(errors) == 0, errors


@dataclass
class FeatureValue:
    """A computed feature value.

    Attributes:
        feature_name: Feature name
        entity_key: Entity identifier
        value: Feature value
        version: Feature version
        computed_at: Computation timestamp
        expires_at: Expiration timestamp
        source: How this value was obtained (computed/cached/external)
        is_valid: Whether the value passed validation
        validation_errors: List of validation errors
    """

    feature_name: str
    entity_key: str
    value: Any
    version: str = "1.0.0"
    computed_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    expires_at: Optional[str] = None
    source: str = "computed"
    is_valid: bool = True
    validation_errors: List[str] = field(default_factory=list)


@dataclass
class FeatureView:
    """A logical view of related features.

    Attributes:
        view_name: View name
        feature_names: List of feature names in this view
        entity_key: Primary entity key
        description: View description
        tags: Categorization tags
        created_at: Creation timestamp
    """

    view_name: str
    feature_names: List[str] = field(default_factory=list)
    entity_key: str = ""
    description: str = ""
    tags: List[str] = field(default_factory=list)
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


@dataclass
class FeatureLineage:
    """Feature lineage information.

    Attributes:
        feature_name: Feature name
        input_features: Features used to compute this feature
        output_features: Features that depend on this feature
        data_sources: Data sources used
        transformations: Transformations applied
        computed_by: Function/process that computes this feature
    """

    feature_name: str = ""
    input_features: List[str] = field(default_factory=list)
    output_features: List[str] = field(default_factory=list)
    data_sources: List[str] = field(default_factory=list)
    transformations: List[str] = field(default_factory=list)
    computed_by: str = ""


class FeatureStore:
    """Feature store interface.

    Manages feature definitions, computation, caching, validation,
    and serving.

    Usage:
        store = FeatureStore()
        store.register_feature(FeatureDefinition("user_age", FeatureType.NUMERIC))
        value = store.get_feature("user_age", "user_123")
    """

    def __init__(
        self,
        *,
        cache_max_entries: int = 10000,
        online_store: Optional[Dict[str, FeatureValue]] = None,
    ) -> None:
        """Initialize the feature store.

        Args:
            cache_max_entries: Maximum number of cached feature values
            online_store: Optional pre-populated online store
        """
        self._features: Dict[str, FeatureDefinition] = {}
        self._views: Dict[str, FeatureView] = {}
        self._lineage: Dict[str, FeatureLineage] = {}
        self._computers: Dict[str, Callable[[str, Dict[str, Any]], Any]] = {}

        # Online store (entity_key:feature_name -> FeatureValue)
        self._online_store: Dict[str, FeatureValue] = online_store or {}
        self._cache_max_entries = cache_max_entries

        # Offline store metadata (just tracks what's available)
        self._offline_store: Dict[str, Set[str]] = {}  # feature_name -> set of entity_keys

        # Statistics
        self._cache_hits: int = 0
        self._cache_misses: int = 0
        self._total_computations: int = 0
        self._validation_failures: int = 0

    # ------------------------------------------------------------------
    # Feature definition
    # ------------------------------------------------------------------

    def register_feature(
        self,
        definition: FeatureDefinition,
        *,
        computer: Optional[Callable[[str, Dict[str, Any]], Any]] = None,
        input_features: Optional[List[str]] = None,
    ) -> None:
        """Register a feature definition.

        Args:
            definition: Feature definition
            computer: Function to compute the feature (entity_key, context) -> value
            input_features: Features this one depends on (for lineage)
        """
        self._features[definition.feature_name] = definition

        if computer:
            self._computers[definition.feature_name] = computer

        # Set up lineage
        lineage = FeatureLineage(
            feature_name=definition.feature_name,
            input_features=input_features or [],
            computed_by=computer.__name__ if computer else "",
        )
        self._lineage[definition.feature_name] = lineage

        # Update parent lineages (add as output)
        if input_features:
            for parent_name in input_features:
                if parent_name not in self._lineage:
                    self._lineage[parent_name] = FeatureLineage(
                        feature_name=parent_name
                    )
                if definition.feature_name not in self._lineage[parent_name].output_features:
                    self._lineage[parent_name].output_features.append(
                        definition.feature_name
                    )

    def get_feature_definition(
        self, feature_name: str
    ) -> Optional[FeatureDefinition]:
        """Get a feature definition.

        Args:
            feature_name: Feature name

        Returns:
            Feature definition if found, None otherwise
        """
        return self._features.get(feature_name)

    def list_features(
        self,
        *,
        feature_type: Optional[FeatureType] = None,
        tags: Optional[List[str]] = None,
    ) -> List[FeatureDefinition]:
        """List features with optional filters.

        Args:
            feature_type: Filter by type
            tags: Filter by tags

        Returns:
            List of matching feature definitions
        """
        features = list(self._features.values())

        if feature_type:
            features = [f for f in features if f.feature_type == feature_type]

        if tags:
            features = [
                f for f in features if all(t in f.tags for t in tags)
            ]

        return features

    # ------------------------------------------------------------------
    # Feature computation
    # ------------------------------------------------------------------

    def set_computer(
        self,
        feature_name: str,
        computer: Callable[[str, Dict[str, Any]], Any],
        *,
        input_features: Optional[List[str]] = None,
    ) -> None:
        """Set the computation function for a feature.

        Args:
            feature_name: Feature name
            computer: Computation function (entity_key, context) -> value
            input_features: Dependencies
        """
        self._computers[feature_name] = computer

        if feature_name in self._lineage:
            self._lineage[feature_name].computed_by = computer.__name__
            if input_features:
                self._lineage[feature_name].input_features = input_features

    def compute_feature(
        self,
        feature_name: str,
        entity_key: str,
        *,
        context: Optional[Dict[str, Any]] = None,
    ) -> FeatureValue:
        """Compute a feature value.

        Args:
            feature_name: Feature name
            entity_key: Entity identifier
            context: Additional computation context

        Returns:
            Computed feature value
        """
        definition = self._features.get(feature_name)
        if not definition:
            return FeatureValue(
                feature_name=feature_name,
                entity_key=entity_key,
                value=None,
                is_valid=False,
                validation_errors=[f"Feature {feature_name} not defined"],
                source="error",
            )

        computer = self._computers.get(feature_name)
        if not computer:
            # Use default value
            value = definition.default_value
            source = "default"
        else:
            try:
                value = computer(entity_key, context or {})
                source = "computed"
                self._total_computations += 1
            except Exception as e:
                value = definition.default_value
                source = "computation_failed"
                return FeatureValue(
                    feature_name=feature_name,
                    entity_key=entity_key,
                    value=value,
                    version=definition.version,
                    source=source,
                    is_valid=False,
                    validation_errors=[f"Computation failed: {str(e)}"],
                )

        # Validate
        is_valid, errors = definition.validate(value)
        if not is_valid:
            self._validation_failures += 1

        # Compute expiration
        expires_at = None
        if definition.ttl_seconds > 0:
            exp_time = time.time() + definition.ttl_seconds
            expires_at = datetime.fromtimestamp(exp_time, tz=timezone.utc).isoformat()

        feature_value = FeatureValue(
            feature_name=feature_name,
            entity_key=entity_key,
            value=value,
            version=definition.version,
            source=source,
            is_valid=is_valid,
            validation_errors=errors,
            expires_at=expires_at,
        )

        return feature_value

    # ------------------------------------------------------------------
    # Feature serving (online)
    # ------------------------------------------------------------------

    def get_feature(
        self,
        feature_name: str,
        entity_key: str,
        *,
        context: Optional[Dict[str, Any]] = None,
        compute_if_missing: bool = True,
    ) -> Optional[FeatureValue]:
        """Get a feature value from the online store.

        Args:
            feature_name: Feature name
            entity_key: Entity identifier
            context: Context for computation if needed
            compute_if_missing: Whether to compute if not in cache

        Returns:
            Feature value, or None if not available
        """
        cache_key = f"{entity_key}:{feature_name}"

        # Check cache
        cached = self._online_store.get(cache_key)
        if cached:
            # Check expiration
            if cached.expires_at:
                exp_time = datetime.fromisoformat(
                    cached.expires_at.replace("Z", "+00:00")
                )
                if datetime.now(timezone.utc) > exp_time:
                    # Expired
                    del self._online_store[cache_key]
                else:
                    self._cache_hits += 1
                    return cached
            else:
                self._cache_hits += 1
                return cached

        self._cache_misses += 1

        if not compute_if_missing:
            return None

        # Compute the feature
        value = self.compute_feature(feature_name, entity_key, context=context)

        # Store in cache
        if value.is_valid:
            self._online_store[cache_key] = value
            self._evict_if_needed()

        return value

    def get_features(
        self,
        feature_names: List[str],
        entity_key: str,
        *,
        context: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Optional[FeatureValue]]:
        """Get multiple feature values.

        Args:
            feature_names: List of feature names
            entity_key: Entity identifier
            context: Computation context

        Returns:
            Dictionary of feature_name -> FeatureValue
        """
        results: Dict[str, Optional[FeatureValue]] = {}
        for name in feature_names:
            results[name] = self.get_feature(
                name, entity_key, context=context
            )
        return results

    def get_view(
        self,
        view_name: str,
        entity_key: str,
        *,
        context: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Optional[FeatureValue]]:
        """Get all features in a feature view.

        Args:
            view_name: Feature view name
            entity_key: Entity identifier
            context: Computation context

        Returns:
            Dictionary of feature_name -> FeatureValue
        """
        view = self._views.get(view_name)
        if not view:
            return {}

        return self.get_features(view.feature_names, entity_key, context=context)

    # ------------------------------------------------------------------
    # Feature serving (offline)
    # ------------------------------------------------------------------

    def get_offline_features(
        self,
        feature_names: List[str],
        entity_keys: List[str],
        *,
        timestamp: Optional[str] = None,
    ) -> Dict[str, Dict[str, Any]]:
        """Get offline features for batch entities.

        Args:
            feature_names: List of feature names
            entity_keys: List of entity keys
            timestamp: Point-in-time timestamp

        Returns:
            Dictionary of entity_key -> {feature_name: value}
        """
        results: Dict[str, Dict[str, Any]] = {}

        for entity_key in entity_keys:
            entity_results: Dict[str, Any] = {}
            for feature_name in feature_names:
                cache_key = f"{entity_key}:{feature_name}"
                cached = self._online_store.get(cache_key)
                if cached:
                    entity_results[feature_name] = cached.value
                else:
                    entity_results[feature_name] = None
            results[entity_key] = entity_results

        return results

    def write_offline_features(
        self,
        feature_data: Dict[str, Dict[str, Any]],
        feature_names: List[str],
    ) -> int:
        """Write features to the offline store.

        Args:
            feature_data: Dictionary of entity_key -> {feature_name: value}
            feature_names: List of feature names being written

        Returns:
            Number of features written
        """
        count = 0

        for entity_key, features in feature_data.items():
            for feature_name in feature_names:
                if feature_name not in features:
                    continue

                definition = self._features.get(feature_name)
                if not definition:
                    continue

                value = features[feature_name]
                is_valid, _ = definition.validate(value)

                cache_key = f"{entity_key}:{feature_name}"
                self._online_store[cache_key] = FeatureValue(
                    feature_name=feature_name,
                    entity_key=entity_key,
                    value=value,
                    version=definition.version,
                    source="offline_import",
                    is_valid=is_valid,
                )

                if feature_name not in self._offline_store:
                    self._offline_store[feature_name] = set()
                self._offline_store[feature_name].add(entity_key)

                count += 1

        return count

    # ------------------------------------------------------------------
    # Feature view management
    # ------------------------------------------------------------------

    def create_view(
        self,
        view_name: str,
        feature_names: List[str],
        *,
        entity_key: str = "",
        description: str = "",
        tags: Optional[List[str]] = None,
    ) -> FeatureView:
        """Create a feature view.

        Args:
            view_name: View name
            feature_names: Features to include
            entity_key: Primary entity key
            description: View description
            tags: View tags

        Returns:
            Created feature view
        """
        view = FeatureView(
            view_name=view_name,
            feature_names=list(feature_names),
            entity_key=entity_key,
            description=description,
            tags=tags or [],
        )
        self._views[view_name] = view
        return view

    def get_feature_view(self, view_name: str) -> Optional[FeatureView]:
        """Get a feature view.

        Args:
            view_name: View name

        Returns:
            Feature view if found, None otherwise
        """
        return self._views.get(view_name)

    # ------------------------------------------------------------------
    # Feature validation
    # ------------------------------------------------------------------

    def validate_features(
        self,
        features: Dict[str, Any],
    ) -> Dict[str, Tuple[bool, List[str]]]:
        """Validate a set of feature values.

        Args:
            features: Dictionary of feature_name -> value

        Returns:
            Dictionary of feature_name -> (is_valid, errors)
        """
        results: Dict[str, Tuple[bool, List[str]]] = {}

        for name, value in features.items():
            definition = self._features.get(name)
            if not definition:
                results[name] = (False, [f"Feature {name} not defined"])
                continue

            is_valid, errors = definition.validate(value)
            results[name] = (is_valid, errors)

        return results

    # ------------------------------------------------------------------
    # Feature lineage
    # ------------------------------------------------------------------

    def get_lineage(self, feature_name: str) -> Optional[FeatureLineage]:
        """Get lineage for a feature.

        Args:
            feature_name: Feature name

        Returns:
            Lineage information, or None if not found
        """
        return self._lineage.get(feature_name)

    def get_lineage_graph(self, feature_name: str) -> Dict[str, Any]:
        """Get full lineage graph for a feature.

        Args:
            feature_name: Feature name

        Returns:
            Nested lineage graph
        """
        if feature_name not in self._lineage:
            return {}

        def build_graph(name: str, visited: Set[str]) -> Dict[str, Any]:
            if name in visited:
                return {"feature_name": name, "circular": True}
            visited.add(name)

            lineage = self._lineage.get(name)
            if not lineage:
                return {"feature_name": name, "not_found": True}

            parents = []
            for parent in lineage.input_features:
                parents.append(build_graph(parent, visited.copy()))

            return {
                "feature_name": name,
                "computed_by": lineage.computed_by,
                "parents": parents,
                "num_children": len(lineage.output_features),
            }

        return build_graph(feature_name, set())

    # ------------------------------------------------------------------
    # Cache management
    # ------------------------------------------------------------------

    def _evict_if_needed(self) -> None:
        """Evict oldest entries if cache is full."""
        if len(self._online_store) <= self._cache_max_entries:
            return

        # Simple eviction: remove oldest entries
        # Sort by computed_at and remove oldest 10%
        sorted_items = sorted(
            self._online_store.items(),
            key=lambda x: x[1].computed_at,
        )

        to_remove = max(1, len(sorted_items) // 10)
        for key, _ in sorted_items[:to_remove]:
            del self._online_store[key]

    def clear_cache(self) -> None:
        """Clear the feature cache."""
        self._online_store.clear()

    def invalidate_feature(self, feature_name: str) -> int:
        """Invalidate all cached values for a feature.

        Args:
            feature_name: Feature name

        Returns:
            Number of entries invalidated
        """
        keys_to_remove = [
            k for k in self._online_store
            if k.endswith(f":{feature_name}")
        ]
        for key in keys_to_remove:
            del self._online_store[key]
        return len(keys_to_remove)

    def invalidate_entity(self, entity_key: str) -> int:
        """Invalidate all cached features for an entity.

        Args:
            entity_key: Entity identifier

        Returns:
            Number of entries invalidated
        """
        keys_to_remove = [
            k for k in self._online_store
            if k.startswith(f"{entity_key}:")
        ]
        for key in keys_to_remove:
            del self._online_store[key]
        return len(keys_to_remove)

    # ------------------------------------------------------------------
    # Statistics
    # ------------------------------------------------------------------

    def get_stats(self) -> Dict[str, Any]:
        """Get feature store statistics.

        Returns:
            Dictionary of statistics
        """
        total_requests = self._cache_hits + self._cache_misses
        hit_rate = (
            self._cache_hits / total_requests if total_requests > 0 else 0.0
        )

        return {
            "features_defined": len(self._features),
            "feature_views": len(self._views),
            "online_cache_size": len(self._online_store),
            "cache_hits": self._cache_hits,
            "cache_misses": self._cache_misses,
            "cache_hit_rate": hit_rate,
            "total_computations": self._total_computations,
            "validation_failures": self._validation_failures,
            "computers_registered": len(self._computers),
            "features_with_lineage": len(self._lineage),
        }

    def __repr__(self) -> str:
        return (
            f"FeatureStore(features={len(self._features)}, "
            f"cached={len(self._online_store)})"
        )
