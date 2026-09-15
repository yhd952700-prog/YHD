"""Event Kernel unit tests.

Covers: publish/subscribe delivery, event-type filtering, scope-based
filtering, correlation-id filtering, metadata filters, dead-letter
handling on handler exceptions, retry, unsubscribe, history/chain
queries, correlation chaining, and bus statistics.
"""
import pytest

from src.kernels.event import (
    DeadLetterEntry,
    Event,
    EventBus,
    EventPriority,
    EventScope,
    Subscription,
)


@pytest.fixture
def bus() -> EventBus:
    return EventBus()


# =====================================================================
# Publish / subscribe
# =====================================================================

class TestPublishSubscribe:
    def test_publish_returns_correlation_id(self, bus):
        cid = bus.publish(Event(type="a", source="s"))
        assert isinstance(cid, str) and len(cid) > 0

    def test_subscriber_receives_matching_event(self, bus):
        received = []
        bus.subscribe("a", lambda e: received.append(e))
        bus.publish(Event(type="a", source="s", data={"x": 1}))
        assert len(received) == 1
        assert received[0].data == {"x": 1}

    def test_subscriber_ignores_other_event_type(self, bus):
        received = []
        bus.subscribe("a", lambda e: received.append(e))
        bus.publish(Event(type="b", source="s"))
        assert received == []

    def test_unsubscribe_stops_delivery(self, bus):
        received = []
        sub_id = bus.subscribe("a", lambda e: received.append(e))
        bus.publish(Event(type="a", source="s"))
        assert len(received) == 1
        assert bus.unsubscribe(sub_id) is True
        bus.publish(Event(type="a", source="s"))
        assert len(received) == 1

    def test_unsubscribe_unknown_returns_false(self, bus):
        assert bus.unsubscribe("nonexistent") is False


# =====================================================================
# Filters
# =====================================================================

class TestFilters:
    def test_scope_blocks_higher_event_scope(self, bus):
        # Subscriber at L2 cannot receive an L5 event.
        received = []
        bus.subscribe("a", lambda e: received.append(e), scope=EventScope.L2)
        bus.publish(Event(type="a", source="s", scope=EventScope.L5))
        assert received == []

    def test_scope_allows_lower_event_scope(self, bus):
        # Subscriber at L5 can receive an L2 event.
        received = []
        bus.subscribe("a", lambda e: received.append(e), scope=EventScope.L5)
        bus.publish(Event(type="a", source="s", scope=EventScope.L2))
        assert len(received) == 1

    def test_correlation_filter(self, bus):
        received = []
        bus.subscribe("a", lambda e: received.append(e), correlation_id="c-1")
        bus.publish(Event(type="a", source="s", correlation_id="c-1"))
        bus.publish(Event(type="a", source="s", correlation_id="c-2"))
        assert len(received) == 1

    def test_metadata_filters(self, bus):
        received = []
        bus.subscribe("a", lambda e: received.append(e), filters={"region": "us"})
        bus.publish(Event(type="a", source="s", metadata={"region": "us"}))
        bus.publish(Event(type="a", source="s", metadata={"region": "eu"}))
        assert len(received) == 1


# =====================================================================
# Dead letters & retry
# =====================================================================

class TestDeadLetter:
    def test_handler_exception_goes_to_dead_letter(self, bus):
        def boom(e):
            raise RuntimeError("kaboom")

        bus.subscribe("a", boom)
        bus.publish(Event(type="a", source="s"))
        dls = bus.get_dead_letters()
        assert len(dls) == 1
        assert isinstance(dls[0], DeadLetterEntry)
        assert isinstance(dls[0].error, RuntimeError)

    def test_retry_dead_letter_reinvokes_handler(self, bus):
        calls = {"n": 0}

        def boom(e):
            calls["n"] += 1
            raise RuntimeError("kaboom")

        bus.subscribe("a", boom)
        bus.publish(Event(type="a", source="s"))
        assert len(bus.get_dead_letters()) == 1
        assert bus.retry_dead_letter(0) is True
        # Handler was invoked again on retry.
        assert calls["n"] == 2
        # There are now two dead letters; the original is resolved.
        all_dls = bus.get_dead_letters(unresolved_only=False)
        assert len(all_dls) == 2
        assert all_dls[0].resolved is True


# =====================================================================
# History & correlation
# =====================================================================

class TestHistory:
    def test_event_history_filter_by_type(self, bus):
        bus.publish(Event(type="a", source="s1"))
        bus.publish(Event(type="b", source="s2"))
        hist = bus.get_event_history(event_type="a")
        assert len(hist) == 1
        assert hist[0].type == "a"

    def test_correlation_chain(self, bus):
        bus.publish(Event(type="a", source="s", correlation_id="c1"))
        bus.publish(Event(type="b", source="s", correlation_id="c1"))
        chain = bus.get_correlation_chain("c1")
        assert len(chain) == 2

    def test_event_with_correlation_preserves_chain(self, bus):
        e = Event(type="a", source="s", correlation_id="c1", causation_id="c0")
        e2 = e.with_correlation("c2")
        assert e2.correlation_id == "c2"
        assert e2.causation_id == "c1"
        assert e2.type == "a"


# =====================================================================
# Stats & subscription object
# =====================================================================

class TestStats:
    def test_stats_reflect_published_events(self, bus):
        bus.publish(Event(type="a", source="s"))
        bus.publish(Event(type="b", source="s"))
        s = bus.stats()
        assert s["event_history_size"] == 2
        assert s["total_subscriptions"] >= 0

    def test_subscription_matches_wildcard(self, bus):
        sub = Subscription(id="x", event_type="*", handler=lambda e: None)
        assert sub.matches(Event(type="anything", source="s")) is True


# =====================================================================
# Regression: wildcard subscriptions must not accumulate on publish
# =====================================================================

class TestWildcardDeliveryRegression:
    """Regression for the ``publish`` aliasing bug.

    ``EventBus._subscriptions`` is a ``defaultdict(list)``, so
    ``.get(event.type, [])`` handed back the stored list *itself* and the old
    ``.extend(...)`` mutated it in place: every publish permanently appended
    the wildcard subscriptions to the type's own list. Wildcard handlers were
    therefore invoked once per accumulated copy, and the list grew without
    bound.
    """

    def test_wildcard_handler_called_exactly_once_per_publish(self, bus):
        calls = []
        bus.subscribe("*", lambda e: calls.append(e.type))
        for _ in range(5):
            bus.publish(Event(type="a", source="s"))
        # Before the fix this was 1+2+3+4+5 == 15 deliveries.
        assert calls == ["a"] * 5

    def test_publish_does_not_grow_the_type_subscription_list(self, bus):
        bus.subscribe("*", lambda e: None)
        bus.subscribe("a", lambda e: None)
        for _ in range(5):
            bus.publish(Event(type="a", source="s"))
        # 1 type-specific + 1 wildcard; repeated publishes must not append.
        assert bus.stats()["total_subscriptions"] == 2

    def test_same_subscription_on_type_and_wildcard_fires_once(self, bus):
        """De-duplication: one Subscription registered on both keys fires once."""
        calls = []
        sub = Subscription(id="dup", event_type="a", handler=lambda e: calls.append(1))
        bus._subscriptions["a"].append(sub)
        bus._subscriptions["*"].append(sub)
        bus.publish(Event(type="a", source="s"))
        assert calls == [1]
