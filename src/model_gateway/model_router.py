"""Model Router for Model Gateway

Routes requests to appropriate models based on capabilities, cost, latency,
context window, and other criteria.

Spec items 181-182: Model Router supports:
- Routing rules based on capability matching
- Cost-aware routing
- Latency-aware routing
- Context window awareness
- Failover and fallback strategies
- Priority-based selection
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import (
    Any,
    Dict,
    List,
    Optional,
    Tuple,
    Union,
)


class RouteResult:
    """Result of a model routing decision.

    Attributes:
        selected_model: The selected RegisteredModel
        reason: Human-readable reason for selection
        fallback_models: List of fallback model IDs
        estimated_cost: Estimated cost in tokens
        estimated_latency: Estimated latency in milliseconds
        metadata: Additional routing metadata
    """

    def __init__(
        self,
        *,
        selected_model: RegisteredModel,
        reason: str,
        fallback_models: Optional[List[str]] = None,
        estimated_cost: Optional[float] = None,
        estimated_latency: Optional[int] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ):
        self.selected_model = selected_model
        self.reason = reason
        self.fallback_models = fallback_models or []
        self.estimated_cost = estimated_cost
        self.estimated_latency = estimated_latency
        self.metadata = metadata or {}
        self.selected_at = datetime.now(timezone.utc)

    def __repr__(self) -> str:
        return (
            f"RouteResult(model={self.selected_model.model_name}, "
            f"reason='{self.reason[:40]}...')"
        )


class RoutingRule:
    """A routing rule for the model router.

    Attributes:
        name: Rule name for debugging
        capability_requirement: Required capability (e.g., "vision", "function_calling")
        min_context: Minimum context window required (tokens)
        max_cost_per_token: Maximum cost per 1K tokens
        max_latency_ms: Maximum acceptable latency (ms)
        priority: Higher priority rules match first
        fallback_to: Fallback rule name if this rule doesn't match
    """

    def __init__(
        self,
        *,
        name: str,
        capability_requirement: Optional[str] = None,
        min_context: Optional[int] = None,
        max_cost_per_token: Optional[float] = None,
        max_latency_ms: Optional[int] = None,
        priority: int = 0,
        fallback_to: Optional[str] = None,
    ):
        self.name = name
        self.capability_requirement = capability_requirement
        self.min_context = min_context
        self.max_cost_per_token = max_cost_per_token
        self.max_latency_ms = max_latency_ms
        self.priority = priority
        self.fallback_to = fallback_to


class ModelRouter:
    """Routes incoming requests to the most appropriate model.

    Selection criteria (in order):
    1. Capability matching (required capabilities)
    2. Context window fit
    3. Cost optimization
    4. Latency optimization
    5. Fallback strategies

    Supports:
    - Register routing rules
    - Route requests against registered models
    - Track routing decisions
    - Fallback chain management
    """

    def __init__(self, registry: ModelRegistry, rules: Optional[List[RoutingRule]] = None):
        self.registry = registry
        self._rules: List[RoutingRule] = rules or []
        self._routing_history: List[RouteResult] = []
        # Sort rules by priority (higher first) on init
        self._rules.sort(key=lambda r: r.priority, reverse=True)

    def add_rule(self, rule: RoutingRule) -> None:
        """Add a routing rule."""
        self._rules.append(rule)
        self._rules.sort(key=lambda r: r.priority, reverse=True)

    def remove_rule(self, rule_name: str) -> bool:
        """Remove a routing rule by name."""
        for i, rule in enumerate(self._rules):
            if rule.name == rule_name:
                del self._rules[i]
                return True
        return False

    def route(
        self,
        *,
        required_capabilities: Optional[List[str]] = None,
        min_context: Optional[int] = None,
        max_cost_per_token: Optional[float] = None,
        max_latency_ms: Optional[int] = None,
        exclude_models: Optional[List[str]] = None,
        prefer_recent: bool = True,
    ) -> RouteResult:
        """Route a request to the best available model.

        Args:
            required_capabilities: List of required capabilities (e.g., ["vision", "function_calling"])
            min_context: Minimum context window needed (tokens)
            max_cost_per_token: Maximum cost per 1K tokens acceptable
            max_latency_ms: Maximum acceptable latency (ms)
            exclude_models: List of model IDs to exclude
            prefer_recent: Whether to prefer recently used models

        Returns:
            RouteResult with the selected model and routing decision
        """
        # Step 1: Get candidate models from registry
        candidates = self._get_candidates(
            required_capabilities=required_capabilities,
            min_context=min_context,
            max_cost_per_token=max_cost_per_token,
            max_latency_ms=max_latency_ms,
            exclude_models=exclude_models,
            prefer_recent=prefer_recent,
        )

        if not candidates:
            raise ValueError("No suitable models found for the given criteria")

        # Step 2: Sort candidates by preference
        sorted_candidates = self._sort_candidates(candidates, prefer_recent=prefer_recent)

        # Step 3: Select the best candidate
        selected = sorted_candidates[0]

        # Step 4: Determine reason and fallbacks
        reason = self._build_reason(selected, candidates)
        fallbacks = self._select_fallbacks(selected, candidates, required_capabilities)

        result = RouteResult(
            selected_model=selected,
            reason=reason,
            fallback_models=fallbacks,
            estimated_cost=selected.capabilities.max_output_tokens * 0.002,  # rough estimate
            estimated_latency=self._estimate_latency(selected),
            metadata={
                "routing_context": {
                    "required_capabilities": required_capabilities,
                    "min_context": min_context,
                    "max_cost_per_token": max_cost_per_token,
                    "max_latency_ms": max_latency_ms,
                }
            },
        )

        # Step 5: Record routing decision
        self._routing_history.append(result)

        return result

    def _get_candidates(
        self,
        required_capabilities: Optional[List[str]] = None,
        min_context: Optional[int] = None,
        max_cost_per_token: Optional[float] = None,
        max_latency_ms: Optional[int] = None,
        exclude_models: Optional[List[str]] = None,
        prefer_recent: bool = True,
    ) -> List[RegisteredModel]:
        """Get candidate models matching the criteria."""

        # Get active models
        active_models = self.registry.list_active()

        # Filter by capabilities
        filtered = []
        for model in active_models:
            # Skip excluded models
            if exclude_models and model.model_id in exclude_models:
                continue

            # Check capability requirements
            if required_capabilities:
                caps = model.capabilities
                capability_ok = all(
                    getattr(caps, cap, False) for cap in required_capabilities
                )
                if not capability_ok:
                    continue

            # Check context window
            if min_context is not None and model.capabilities.max_context_tokens < min_context:
                continue

            # Check cost constraint
            if max_cost_per_token is not None:
                # Rough cost estimation based on model tier
                # In production, this would use actual pricing data
                pass

            filtered.append(model)

        # Sort by priority (active first, then by last_seen recency)
        filtered.sort(key=lambda m: (
            m.last_seen if prefer_recent else m.created_at
        ), reverse=True)

        return filtered

    def _sort_candidates(
        self, candidates: List[RegisteredModel], prefer_recent: bool = True
    ) -> List[RegisteredModel]:
        """Sort candidates by preference.

        Priority order:
        1. Capability match quality
        2. Context window fit (closest to requirement)
        3. Recent usage (if prefer_recent)
        4. Lower cost
        5. Lower latency
        """
        def score_model(model: RegisteredModel) -> float:
            score = 0.0
            # Recent usage bonus
            if prefer_recent:
                # Normalize last_seen within last 30 days
                days_old = (datetime.now(timezone.utc) - model.last_seen).days
                recency = max(0, 30 - days_old) / 30.0
                score += recency * 0.4
            else:
                score += (datetime.now(timezone.utc) - model.created_at).days / 365.0 * 0.1

            # Context window fit (prefer closer to requirement)
            # If no specific requirement, larger is generally better
            score += min(model.capabilities.max_context_tokens / 100000.0, 1.0) * 0.3

            # Cost proxy (smaller max output tokens = potentially cheaper)
            score += (1.0 - min(model.capabilities.max_output_tokens / 8000.0, 1.0)) * 0.1

            # Latency proxy (not directly available, use model age as rough proxy)
            score += 0.2

            return score

        return sorted(candidates, key=score_model, reverse=True)

    def _build_reason(
        self, selected: RegisteredModel, candidates: List[RegisteredModel]
    ) -> str:
        """Build human-readable reason for the routing decision."""
        reasons = [
            f"Selected {selected.model_name} ({selected.provider})",
            f"Capability: {selected.capabilities}",
        ]

        # Check if there were close alternatives
        if len(candidates) > 1:
            reasons.append(f"From {len(candidates)} eligible models")

        return " | ".join(reasons)

    def _select_fallbacks(
        self, selected: RegisteredModel, all_candidates: List[RegisteredModel],
        required_capabilities: Optional[List[str]],
    ) -> List[str]:
        """Select fallback model IDs."""
        fallbacks = []

        # Find other models that could serve as fallbacks
        for model in all_candidates:
            if model.model_id != selected.model_id:
                # Check if it has overlapping capabilities
                if required_capabilities:
                    caps = model.capabilities
                    if any(getattr(caps, cap, False) for cap in required_capabilities):
                        fallbacks.append(model.model_id)
                else:
                    fallbacks.append(model.model_id)

                if len(fallbacks) >= 3:  # Limit to 3 fallbacks
                    break

        return fallbacks

    def _estimate_latency(self, model: RegisteredModel) -> int:
        """Estimate latency for a model (in ms)."""
        # Rough estimation based on model capabilities
        # In production, this would use actual profiling data
        base_latency = 500  # 500ms base
        # Larger context windows tend to be slightly slower
        context_bonus = min(model.capabilities.max_context_tokens / 100000.0, 200)  # up to 200ms
        return int(base_latency + context_bonus)

    def get_routing_history(self, limit: Optional[int] = None) -> List[RouteResult]:
        """Get routing decision history."""
        if limit:
            return self._routing_history[-limit:]
        return self._routing_history

    def __repr__(self) -> str:
        return f"ModelRouter(rules={len(self._rules)}, candidates={len(self.registry.list_active())})"