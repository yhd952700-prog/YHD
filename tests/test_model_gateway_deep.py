"""Deep tests for src.model_gateway.

Covers:
- ModelRegistryExtended: register/query/compare/best-for-task
- SmartRouter: routing strategies, rules, circuit breaker, fallback
- ModelLoadBalancer: balancing strategies, health, failover
- MemoryCache / SemanticCache / PromptCache
- CostOptimizer: usage tracking, budgets, recommendations, batching
"""

import time

import pytest

from src.model_gateway.router import (
    CircuitBreaker,
    CircuitBreakerState,
    RoutingRule,
    RoutingStrategy,
    RoutingRequest,
    SmartRouter,
)
from src.model_gateway.model_registry_extended import (
    Capability,
    ModelPerformance,
    ModelRegistryExtended,
    ModelStatus,
    ModelTier,
    RegisteredModelExtended,
    TaskType,
)
from src.model_gateway.load_balancer import (
    EndpointHealth,
    LoadBalancingStrategy,
    ModelEndpoint,
    ModelLoadBalancer,
)
from src.model_gateway.prompt_cache import MemoryCache, PromptCache, SemanticCache
from src.model_gateway.cost_optimizer import (
    BudgetPeriod,
    CostOptimizer,
    TokenUsageRecord,
)


# ----------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------


def make_model(
    model_id: str,
    *,
    tier: ModelTier = ModelTier.STANDARD,
    status: ModelStatus = ModelStatus.ACTIVE,
    capabilities=None,
    cost_in: float = 0.001,
    cost_out: float = 0.002,
    latency: float = 500.0,
    context_window: int = 8192,
    benchmarks=None,
    provider: str = "testco",
) -> RegisteredModelExtended:
    perf = ModelPerformance(avg_latency_ms=latency)
    return RegisteredModelExtended(
        model_id=model_id,
        model_name=model_id.upper(),
        provider=provider,
        tier=tier,
        status=status,
        capabilities=capabilities if capabilities is not None else {Capability.TEXT_GENERATION},
        context_window=context_window,
        cost_per_1k_input=cost_in,
        cost_per_1k_output=cost_out,
        performance=perf,
        benchmarks=benchmarks or {TaskType.TEXT_GENERATION.value: 0.8},
    )


def make_request(**kwargs) -> RoutingRequest:
    defaults = dict(
        task_type=TaskType.TEXT_GENERATION,
        estimated_input_tokens=1000,
        estimated_output_tokens=500,
    )
    defaults.update(kwargs)
    return RoutingRequest(**defaults)


# ----------------------------------------------------------------------
# Model registry
# ----------------------------------------------------------------------


class TestModelRegistryExtended:
    def test_register_and_get(self):
        reg = ModelRegistryExtended()
        reg.register(make_model("m1"))
        assert reg.get("m1").model_name == "M1"
        assert "m1" in reg
        assert len(reg) == 1

    def test_unregister(self):
        reg = ModelRegistryExtended()
        reg.register(make_model("m1"))
        reg.unregister("m1")
        assert reg.get("m1") is None
        assert len(reg) == 0

    def test_update_replaces_model(self):
        reg = ModelRegistryExtended()
        reg.register(make_model("m1", provider="old"))
        reg.update(make_model("m1", provider="new"))
        assert reg.get("m1").provider == "new"
        assert len(reg) == 1

    def test_list_all(self):
        reg = ModelRegistryExtended()
        reg.register(make_model("a"))
        reg.register(make_model("b"))
        assert {m.model_id for m in reg.list_all()} == {"a", "b"}

    def test_filter_by_capability(self):
        reg = ModelRegistryExtended()
        reg.register(make_model("text", capabilities={Capability.TEXT_GENERATION}))
        reg.register(make_model("vision", capabilities={Capability.VISION}))
        result = reg.filter_by_capability(Capability.VISION)
        assert [m.model_id for m in result] == ["vision"]

    def test_filter_by_tier_and_status(self):
        reg = ModelRegistryExtended()
        reg.register(make_model("eco", tier=ModelTier.ECONOMY))
        reg.register(make_model("prem", tier=ModelTier.PREMIUM))
        reg.register(make_model("dep", status=ModelStatus.DEPRECATED))
        assert [m.model_id for m in reg.filter_by_tier(ModelTier.PREMIUM)] == ["prem"]
        assert [m.model_id for m in reg.filter_by_status(ModelStatus.DEPRECATED)] == ["dep"]

    def test_filter_by_provider(self):
        reg = ModelRegistryExtended()
        reg.register(make_model("o1", provider="openai"))
        reg.register(make_model("a1", provider="anthropic"))
        assert [m.model_id for m in reg.filter_by_provider("openai")] == ["o1"]

    def test_query_multiple_criteria(self):
        reg = ModelRegistryExtended()
        reg.register(make_model("m1", tier=ModelTier.ECONOMY, cost_in=0.0001, cost_out=0.0002))
        reg.register(make_model("m2", tier=ModelTier.PREMIUM, cost_in=0.01, cost_out=0.02))
        result = reg.query(tier=ModelTier.ECONOMY)
        assert [m.model_id for m in result] == ["m1"]

    def test_compare_models(self):
        reg = ModelRegistryExtended()
        reg.register(make_model("m1", cost_in=0.001))
        reg.register(make_model("m2", cost_in=0.005))
        comparison = reg.compare(["m1", "m2"])
        assert set(comparison.keys()) == {"m1", "m2"}
        assert comparison["m1"]["cost_per_1k_input"] == 0.001

    def test_get_best_for_task(self):
        reg = ModelRegistryExtended()
        reg.register(make_model("weak", benchmarks={TaskType.TEXT_GENERATION.value: 0.5}))
        reg.register(make_model("strong", benchmarks={TaskType.TEXT_GENERATION.value: 0.95}))
        best = reg.get_best_for_task(TaskType.TEXT_GENERATION)
        assert best is not None and best.model_id == "strong"

    def test_get_stats(self):
        reg = ModelRegistryExtended()
        reg.register(make_model("m1", tier=ModelTier.ECONOMY))
        reg.register(make_model("m2", tier=ModelTier.PREMIUM))
        stats = reg.get_stats()
        assert stats["total_models"] == 2

    def test_model_to_dict_from_dict_roundtrip(self):
        model = make_model("m1", capabilities={Capability.TEXT_GENERATION, Capability.VISION})
        data = model.to_dict()
        restored = RegisteredModelExtended.from_dict(data)
        assert restored.model_id == "m1"
        assert Capability.VISION in restored.capabilities
        assert restored.tier == model.tier

    def test_model_estimate_cost(self):
        model = make_model("m1", cost_in=0.002, cost_out=0.004)
        cost = model.estimate_cost(2000, 1000)
        assert cost == pytest.approx(0.004 + 0.004)


# ----------------------------------------------------------------------
# Smart router
# ----------------------------------------------------------------------


class TestSmartRouter:
    def test_cost_optimized_picks_cheapest(self):
        router = SmartRouter(default_strategy=RoutingStrategy.COST_OPTIMIZED)
        router.add_model(make_model("cheap", cost_in=0.0001, cost_out=0.0002, tier=ModelTier.ECONOMY))
        router.add_model(make_model("expensive", cost_in=0.05, cost_out=0.1, tier=ModelTier.PREMIUM))
        decision = router.route(make_request())
        assert decision.model.model_id == "cheap"
        assert decision.strategy == RoutingStrategy.COST_OPTIMIZED
        assert decision.estimated_cost > 0

    def test_latency_optimized_picks_fastest(self):
        router = SmartRouter(default_strategy=RoutingStrategy.LATENCY_OPTIMIZED)
        router.add_model(make_model("slow", latency=3000.0))
        router.add_model(make_model("fast", latency=100.0))
        decision = router.route(make_request())
        assert decision.model.model_id == "fast"
        assert decision.estimated_latency_ms > 0

    def test_quality_optimized_picks_best_benchmark(self):
        router = SmartRouter(default_strategy=RoutingStrategy.QUALITY_OPTIMIZED)
        router.add_model(make_model("lowq", benchmarks={TaskType.TEXT_GENERATION.value: 0.4}))
        router.add_model(make_model("highq", benchmarks={TaskType.TEXT_GENERATION.value: 0.99}))
        decision = router.route(make_request())
        assert decision.model.model_id == "highq"

    def test_balanced_returns_decision_with_fallbacks(self):
        router = SmartRouter(default_strategy=RoutingStrategy.BALANCED)
        for i in range(3):
            router.add_model(make_model(f"m{i}", cost_in=0.001 * (i + 1)))
        decision = router.route(make_request())
        assert decision.model is not None
        assert len(decision.fallback_models) >= 1
        assert decision.reason

    def test_round_robin_cycles_models(self):
        router = SmartRouter(default_strategy=RoutingStrategy.ROUND_ROBIN)
        router.add_model(make_model("m1"))
        router.add_model(make_model("m2"))
        first = router.route(make_request()).model.model_id
        second = router.route(make_request()).model.model_id
        third = router.route(make_request()).model.model_id
        assert first != second
        assert third == first

    def test_least_loaded_balances_load(self):
        router = SmartRouter(default_strategy=RoutingStrategy.LEAST_LOADED)
        router.add_model(make_model("m1"))
        router.add_model(make_model("m2"))
        picks = {router.route(make_request()).model.model_id for _ in range(2)}
        assert picks == {"m1", "m2"}

    def test_no_eligible_models_raises(self):
        router = SmartRouter()
        router.add_model(make_model("m1", capabilities={Capability.VISION}))
        with pytest.raises(ValueError):
            router.route(make_request(required_capabilities={Capability.CODE_GENERATION}))

    def test_max_cost_filters_expensive(self):
        router = SmartRouter(default_strategy=RoutingStrategy.COST_OPTIMIZED)
        router.add_model(make_model("expensive", cost_in=1.0, cost_out=1.0))
        router.add_model(make_model("cheap", cost_in=0.0001, cost_out=0.0001))
        decision = router.route(make_request(max_cost=0.001))
        assert decision.model.model_id == "cheap"

    def test_min_quality_filters_low_benchmark(self):
        router = SmartRouter()
        router.add_model(make_model("lowq", benchmarks={TaskType.TEXT_GENERATION.value: 0.3}))
        router.add_model(make_model("highq", benchmarks={TaskType.TEXT_GENERATION.value: 0.9}))
        decision = router.route(make_request(min_quality_score=0.8))
        assert decision.model.model_id == "highq"

    def test_context_window_filter(self):
        router = SmartRouter()
        router.add_model(make_model("smallctx", context_window=1024))
        router.add_model(make_model("bigctx", context_window=128000))
        decision = router.route(make_request(context_size=60000))
        assert decision.model.model_id == "bigctx"

    def test_inactive_model_excluded(self):
        router = SmartRouter()
        router.add_model(make_model("dep", status=ModelStatus.DEPRECATED))
        router.add_model(make_model("act", status=ModelStatus.ACTIVE))
        decision = router.route(make_request())
        assert decision.model.model_id == "act"

    def test_rule_pins_model(self):
        router = SmartRouter()
        router.add_model(make_model("m1"))
        router.add_model(make_model("pinned"))
        router.add_rule(RoutingRule(
            name="pin-vip",
            condition=lambda req: req.user_id == "vip",
            action={"pinned_model": "pinned"},
            priority=10,
        ))
        decision = router.route(make_request(user_id="vip"))
        assert decision.model.model_id == "pinned"
        assert decision.confidence == 1.0

    def test_rule_overrides_strategy(self):
        router = SmartRouter(default_strategy=RoutingStrategy.BALANCED)
        router.add_model(make_model("cheap", cost_in=0.0001, cost_out=0.0001))
        router.add_model(make_model("expensive", cost_in=0.05, cost_out=0.05))
        router.add_rule(RoutingRule(
            name="cheap-for-bulk",
            condition=lambda req: True,
            action={"strategy": RoutingStrategy.COST_OPTIMIZED.value},
        ))
        decision = router.route(make_request())
        assert decision.model.model_id == "cheap"

    def test_remove_rule(self):
        router = SmartRouter()
        router.add_model(make_model("m1"))
        router.add_rule(RoutingRule(name="r1", condition=lambda r: True, action={}))
        router.remove_rule("r1")
        assert all(r.name != "r1" for r in router._rules)

    def test_circuit_breaker_opens_after_failures(self):
        router = SmartRouter()
        router.add_model(make_model("flaky"))
        for _ in range(5):
            router.record_failure("flaky")
        with pytest.raises(ValueError):
            router.route(make_request())

    def test_circuit_breaker_recovers(self):
        cb = CircuitBreaker(model_id="m", failure_threshold=2, success_threshold=1, reset_timeout=0.01)
        cb.record_failure()
        cb.record_failure()
        assert cb.state == CircuitBreakerState.OPEN
        assert not cb.can_send_request()
        time.sleep(0.02)
        assert cb.can_send_request()  # half-open
        cb.record_success()
        assert cb.state == CircuitBreakerState.CLOSED

    def test_record_success_resets(self):
        router = SmartRouter()
        router.add_model(make_model("m1"))
        router.record_failure("m1")
        router.record_success("m1")
        decision = router.route(make_request())
        assert decision.model.model_id == "m1"

    def test_get_fallback(self):
        router = SmartRouter()
        router.add_model(make_model("primary"))
        router.add_model(make_model("backup"))
        fallback = router.get_fallback("primary", make_request())
        assert fallback is not None
        assert fallback.model.model_id == "backup"

    def test_add_remove_model(self):
        router = SmartRouter()
        router.add_model(make_model("m1"))
        assert router.get_model("m1") is not None
        router.remove_model("m1")
        assert router.get_model("m1") is None
        assert router.list_models() == []

    def test_get_stats(self):
        router = SmartRouter()
        router.add_model(make_model("m1"))
        router.route(make_request())
        stats = router.get_stats()
        assert stats["total_models"] == 1
        assert stats["active_models"] == 1


# ----------------------------------------------------------------------
# Load balancer
# ----------------------------------------------------------------------


def make_endpoint(model_id: str, endpoint_id: str, **kwargs) -> ModelEndpoint:
    defaults = dict(
        model_id=model_id,
        endpoint_id=endpoint_id,
        url=f"http://{endpoint_id}.example.com",
        health=EndpointHealth.HEALTHY,
    )
    defaults.update(kwargs)
    return ModelEndpoint(**defaults)


class TestModelLoadBalancer:
    def test_add_and_list_endpoints(self):
        lb = ModelLoadBalancer()
        lb.add_endpoint(make_endpoint("m1", "e1"))
        lb.add_endpoint(make_endpoint("m1", "e2"))
        lb.add_endpoint(make_endpoint("m2", "e3"))
        assert len(lb.list_endpoints()) == 3
        assert len(lb.list_endpoints("m1")) == 2

    def test_remove_endpoint(self):
        lb = ModelLoadBalancer()
        lb.add_endpoint(make_endpoint("m1", "e1"))
        lb.remove_endpoint("e1")
        assert lb.get_endpoint("e1") is None

    def test_round_robin_rotates(self):
        lb = ModelLoadBalancer(strategy=LoadBalancingStrategy.ROUND_ROBIN)
        lb.add_endpoint(make_endpoint("m1", "e1"))
        lb.add_endpoint(make_endpoint("m1", "e2"))
        picks = [lb.select_endpoint("m1").endpoint_id for _ in range(4)]
        assert picks == ["e1", "e2", "e1", "e2"]

    def test_least_connections(self):
        lb = ModelLoadBalancer(strategy=LoadBalancingStrategy.LEAST_CONNECTIONS)
        lb.add_endpoint(make_endpoint("m1", "busy", current_connections=10))
        lb.add_endpoint(make_endpoint("m1", "idle", current_connections=1))
        assert lb.select_endpoint("m1").endpoint_id == "idle"

    def test_least_latency(self):
        lb = ModelLoadBalancer(strategy=LoadBalancingStrategy.LEAST_LATENCY)
        lb.add_endpoint(make_endpoint("m1", "slow", avg_latency_ms=900))
        lb.add_endpoint(make_endpoint("m1", "fast", avg_latency_ms=50))
        assert lb.select_endpoint("m1").endpoint_id == "fast"

    def test_ip_hash_deterministic(self):
        lb = ModelLoadBalancer(strategy=LoadBalancingStrategy.IP_HASH)
        for i in range(3):
            lb.add_endpoint(make_endpoint("m1", f"e{i}"))
        first = lb.select_endpoint("m1", request_id="user-42").endpoint_id
        for _ in range(3):
            assert lb.select_endpoint("m1", request_id="user-42").endpoint_id == first

    def test_unhealthy_endpoint_not_selected(self):
        lb = ModelLoadBalancer()
        lb.add_endpoint(make_endpoint("m1", "bad", health=EndpointHealth.UNHEALTHY))
        lb.add_endpoint(make_endpoint("m1", "good", health=EndpointHealth.HEALTHY))
        assert lb.select_endpoint("m1").endpoint_id == "good"

    def test_no_available_returns_none(self):
        lb = ModelLoadBalancer()
        lb.add_endpoint(make_endpoint("m1", "bad", health=EndpointHealth.UNHEALTHY))
        assert lb.select_endpoint("m1") is None

    def test_begin_end_request_updates_counters(self):
        lb = ModelLoadBalancer()
        lb.add_endpoint(make_endpoint("m1", "e1"))
        assert lb.begin_request("e1") is True
        assert lb.get_endpoint("e1").current_connections == 1
        lb.end_request("e1", success=True, latency_ms=100.0)
        ep = lb.get_endpoint("e1")
        assert ep.current_connections == 0
        assert ep.total_requests == 1
        assert ep.avg_latency_ms > 0

    def test_end_request_failure_increments_failures(self):
        lb = ModelLoadBalancer()
        lb.add_endpoint(make_endpoint("m1", "e1"))
        lb.begin_request("e1")
        lb.end_request("e1", success=False, latency_ms=10.0)
        assert lb.get_endpoint("e1").total_failures == 1
        assert lb.get_endpoint("e1").failure_rate == 1.0

    def test_failover_endpoint(self):
        lb = ModelLoadBalancer()
        lb.add_endpoint(make_endpoint("m1", "primary"))
        lb.add_endpoint(make_endpoint("m1", "backup"))
        failover = lb.get_failover_endpoint("primary", "m1")
        assert failover is not None and failover.endpoint_id == "backup"

    def test_health_check_with_custom_fn(self):
        from src.model_gateway.load_balancer import HealthCheckConfig

        lb = ModelLoadBalancer(
            health_check_config=HealthCheckConfig(check_fn=lambda ep: True)
        )
        lb.add_endpoint(make_endpoint("m1", "e1", health=EndpointHealth.UNKNOWN))
        health = lb.run_health_check("e1")
        assert health == EndpointHealth.HEALTHY
        assert lb.get_endpoint("e1").health == EndpointHealth.HEALTHY

    def test_get_stats(self):
        lb = ModelLoadBalancer()
        lb.add_endpoint(make_endpoint("m1", "e1"))
        lb.begin_request("e1")
        lb.end_request("e1", success=True, latency_ms=50.0)
        stats = lb.get_stats()
        assert stats["total_endpoints"] == 1


# ----------------------------------------------------------------------
# Caches
# ----------------------------------------------------------------------


class TestMemoryCache:
    def test_set_and_get(self):
        cache = MemoryCache()
        cache.set("k1", {"resp": "hello"})
        assert cache.get("k1") == {"resp": "hello"}

    def test_missing_key_returns_none(self):
        cache = MemoryCache()
        assert cache.get("nope") is None

    def test_has_and_contains(self):
        cache = MemoryCache()
        cache.set("k1", 1)
        assert cache.has("k1")
        assert "k1" in cache
        assert not cache.has("k2")

    def test_delete(self):
        cache = MemoryCache()
        cache.set("k1", 1)
        assert cache.delete("k1") is True
        assert cache.get("k1") is None
        assert cache.delete("k1") is False

    def test_ttl_expiration(self):
        cache = MemoryCache()
        cache.set("k1", "v", ttl_seconds=1)
        assert cache.get("k1") == "v"
        time.sleep(1.1)
        assert cache.get("k1") is None

    def test_lru_eviction(self):
        cache = MemoryCache(max_entries=2)
        cache.set("a", 1)
        cache.set("b", 2)
        cache.set("c", 3)  # triggers eviction of one entry
        assert len(cache) == 2
        assert cache.get("c") == 3
        assert cache.get_stats().evictions >= 1

    def test_clear(self):
        cache = MemoryCache()
        cache.set("a", 1)
        cache.clear()
        assert len(cache) == 0
        assert cache.get("a") is None

    def test_stats_hit_rate(self):
        cache = MemoryCache()
        cache.set("a", 1)
        cache.get("a")
        cache.get("miss")
        stats = cache.get_stats()
        assert stats.hits == 1
        assert stats.misses == 1
        assert stats.hit_rate == pytest.approx(0.5)


class TestSemanticCache:
    def test_store_and_exact_lookup(self):
        cache = SemanticCache()
        cache.store("What is AI?", "AI is artificial intelligence")
        match = cache.lookup("What is AI?")
        assert match is not None
        assert match.similarity == pytest.approx(1.0)
        assert match.value == "AI is artificial intelligence"

    def test_similar_prompt_lookup(self):
        cache = SemanticCache(similarity_threshold=0.5)
        cache.store("the quick brown fox jumps", "result1")
        match = cache.lookup("the quick brown fox leaps over")
        assert match is not None
        assert match.similarity >= 0.5

    def test_dissimilar_prompt_no_match(self):
        cache = SemanticCache(similarity_threshold=0.99)
        cache.store("completely different topic xyz", "r")
        assert cache.lookup("aaaaaaaa bbbbbbbb cccccccc") is None

    def test_clear_and_len(self):
        cache = SemanticCache()
        cache.store("p1", "r1")
        cache.store("p2", "r2")
        assert len(cache) == 2
        cache.clear()
        assert len(cache) == 0

    def test_stats_recorded(self):
        cache = SemanticCache()
        cache.store("hello world", "r")
        cache.lookup("hello world")
        cache.lookup("totally unrelated query qqq")
        stats = cache.get_stats()
        assert stats.semantic_hits >= 1


class TestPromptCache:
    def test_exact_roundtrip(self):
        pc = PromptCache()
        pc.set_exact("key1", {"text": "answer"})
        assert pc.get_exact("key1") == {"text": "answer"}

    def test_delete_exact(self):
        pc = PromptCache()
        pc.set_exact("k", "v")
        assert pc.delete_exact("k") is True
        assert pc.get_exact("k") is None

    def test_semantic_store_and_lookup(self):
        pc = PromptCache()
        pc.store_semantic("Explain quantum computing", "QC explanation")
        match = pc.lookup_semantic("Explain quantum computing")
        assert match is not None

    def test_query_put_combined(self):
        pc = PromptCache()
        pc.put("What is 2+2?", "4")
        result = pc.query("What is 2+2?")
        assert result is not None

    def test_clear_all(self):
        pc = PromptCache()
        pc.set_exact("k", "v")
        pc.store_semantic("prompt", "resp")
        pc.clear_all()
        assert pc.get_exact("k") is None
        stats = pc.get_stats()
        assert stats["memory"]["entries"] == 0


# ----------------------------------------------------------------------
# Cost optimizer
# ----------------------------------------------------------------------


class TestCostOptimizer:
    def test_set_and_get_model_cost(self):
        opt = CostOptimizer()
        opt.set_model_cost("gpt-x", 0.001, 0.002)
        assert opt.get_model_cost("gpt-x") == (0.001, 0.002)

    def test_calculate_cost(self):
        opt = CostOptimizer()
        opt.set_model_cost("m", 0.001, 0.002)
        cost = opt.calculate_cost("m", 1000, 1000)
        assert cost == pytest.approx(0.003)

    def test_calculate_cost_unknown_model(self):
        opt = CostOptimizer()
        assert opt.calculate_cost("unknown", 1000, 1000) == 0.0

    def test_track_usage_auto_computes_cost(self):
        opt = CostOptimizer()
        opt.set_model_cost("m", 0.001, 0.002)
        record = TokenUsageRecord(model_id="m", input_tokens=2000, output_tokens=1000)
        opt.track_usage(record)
        assert record.cost == pytest.approx(0.004)
        assert record.total_tokens == 3000

    def test_get_usage_aggregation(self):
        opt = CostOptimizer()
        opt.set_model_cost("m", 0.001, 0.001)
        opt.track_usage(TokenUsageRecord(model_id="m", input_tokens=1000, output_tokens=1000, user_id="u1", project_id="p1"))
        opt.track_usage(TokenUsageRecord(model_id="m", input_tokens=1000, output_tokens=1000, user_id="u1", project_id="p1"))
        usage = opt.get_usage(project_id="p1")
        assert usage["request_count"] == 2
        assert usage["total_tokens"] == 4000
        assert usage["total_cost"] == pytest.approx(0.004)

    def test_get_usage_cache_hits(self):
        opt = CostOptimizer()
        opt.track_usage(TokenUsageRecord(model_id="m", input_tokens=10, output_tokens=10, cache_hit=True))
        usage = opt.get_usage()
        assert usage["cache_hit_count"] == 1

    def test_budget_set_and_get(self):
        opt = CostOptimizer()
        budget = opt.set_budget("proj-1", 100.0, period=BudgetPeriod.MONTHLY)
        assert budget.limit == 100.0
        fetched = opt.get_budget("proj-1")
        assert fetched is not None and fetched.limit == 100.0

    def test_check_budget_within(self):
        opt = CostOptimizer()
        opt.set_budget("proj-1", 100.0)
        result = opt.check_budget("proj-1", request_cost=1.0)
        assert result["allowed"] is True
        assert result["reason"] == "within_budget"

    def test_check_budget_over(self):
        opt = CostOptimizer()
        budget = opt.set_budget("proj-1", 10.0)
        budget.spent = 10.0
        result = opt.check_budget("proj-1", request_cost=1.0)
        assert result["allowed"] is False
        assert result["reason"] == "over_budget"

    def test_check_budget_near_limit(self):
        opt = CostOptimizer()
        budget = opt.set_budget("proj-1", 100.0, threshold=0.8)
        budget.spent = 85.0
        result = opt.check_budget("proj-1", request_cost=1.0)
        assert result["allowed"] is True
        assert result["reason"] == "near_limit"

    def test_check_budget_no_budget_set(self):
        opt = CostOptimizer()
        result = opt.check_budget("nothing")
        assert result["allowed"] is True
        assert result["reason"] == "no_budget_set"

    def test_track_usage_updates_budget_spent(self):
        opt = CostOptimizer()
        opt.set_model_cost("m", 0.001, 0.001)
        opt.set_budget("proj-1", 50.0)
        opt.track_usage(TokenUsageRecord(model_id="m", input_tokens=1000, output_tokens=1000, project_id="proj-1"))
        assert opt.get_budget("proj-1").spent == pytest.approx(0.002)

    def test_project_cost(self):
        opt = CostOptimizer()
        opt.set_model_cost("m", 0.001, 0.002)
        projection = opt.project_cost("m", 100, 1000, 500)
        assert projection["total_cost"] == pytest.approx(100 * (0.001 + 0.001))

    def test_recommendations_for_heavy_usage(self):
        opt = CostOptimizer()
        opt.set_model_cost("premium-m", 0.05, 0.1)
        for _ in range(15):
            opt.track_usage(TokenUsageRecord(
                model_id="premium-m", input_tokens=1000, output_tokens=1000, project_id="p1"
            ))
        recs = opt.get_recommendations(project_id="p1")
        assert len(recs) >= 1
        assert any(r.estimated_savings >= 0 for r in recs)

    def test_plan_batch(self):
        opt = CostOptimizer()
        opt.set_model_cost("m", 0.001, 0.001)
        plan = opt.plan_batch("m", 100, 1000, 500, batch_size=10)
        assert plan is not None

    def test_get_stats(self):
        opt = CostOptimizer()
        opt.set_model_cost("m", 0.001, 0.001)
        opt.track_usage(TokenUsageRecord(model_id="m", input_tokens=10, output_tokens=10))
        stats = opt.get_stats()
        assert stats["total_usage_records"] == 1
        assert stats["models_tracked"] == 1
