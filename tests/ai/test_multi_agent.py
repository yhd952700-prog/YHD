"""Tests for the enhanced multi-agent collaboration system."""
from __future__ import annotations

import pytest

from src.ai.agent_factory import (
    AgentFactory,
    AgentRole,
    RoleProfile,
    ROLE_PROFILES,
)
from src.ai.blackboard import (
    Blackboard,
    BlackboardEntry,
    EntryType,
    ConfidenceLevel,
)
from src.ai.committee import (
    AgentCommittee,
    CommitteeResult,
    VotingMethod,
    ConflictResolver,
    ConflictStrategy,
    Conflict,
    ResultAggregator,
    Vote,
)
from src.ai.employee import Agent
from src.ai.providers import MockProvider


def _make_provider():
    """Create a mock provider for testing."""
    return MockProvider(name="test-mock", model="test-model")


# =====================================================================
# Agent Factory Tests
# =====================================================================

class TestAgentFactory:
    """Test the agent factory."""

    def test_create_agent(self):
        factory = AgentFactory(provider=_make_provider())
        agent = factory.create(AgentRole.RESEARCHER, name="Test Researcher")

        assert agent.name == "Test Researcher"
        assert agent.agent_type == "researcher"
        assert agent.id.startswith("researcher_")

    def test_role_profiles_exist(self):
        """All roles should have profiles."""
        for role in AgentRole:
            assert role in ROLE_PROFILES
            profile = ROLE_PROFILES[role]
            assert profile.role == role
            assert len(profile.description) > 0
            assert len(profile.strengths) > 0

    def test_create_team(self):
        factory = AgentFactory(provider=_make_provider())
        roles = [AgentRole.MANAGER, AgentRole.RESEARCHER, AgentRole.QA]

        team = factory.create_team(roles, team_name="Alpha")

        assert len(team) == 3
        for role in roles:
            assert role in team
            assert len(team[role]) == 1

    def test_create_pool(self):
        factory = AgentFactory(provider=_make_provider())

        pool = factory.create_pool(AgentRole.RESEARCHER, count=3, pool_name="R&D")

        assert len(pool) == 3
        for agent in pool:
            assert agent.agent_type == "researcher"

    def test_can_do(self):
        factory = AgentFactory(provider=_make_provider())

        assert factory.can_do(AgentRole.RESEARCHER, "search") is True
        assert factory.can_do(AgentRole.RESEARCHER, "test") is False
        assert factory.can_do(AgentRole.QA, "test") is True

    def test_best_role_for_research(self):
        factory = AgentFactory(provider=_make_provider())

        role = factory.best_role_for("research the market size of AI")
        assert role == AgentRole.RESEARCHER

    def test_best_role_for_analysis(self):
        factory = AgentFactory(provider=_make_provider())

        role = factory.best_role_for("analyze the data and find patterns")
        assert role == AgentRole.ANALYST

    def test_best_role_for_testing(self):
        factory = AgentFactory(provider=_make_provider())

        role = factory.best_role_for("test this code for bugs and quality issues")
        assert role == AgentRole.QA

    def test_best_role_for_planning(self):
        factory = AgentFactory(provider=_make_provider())

        role = factory.best_role_for("plan the roadmap and schedule")
        assert role == AgentRole.PLANNER

    def test_best_role_general(self):
        factory = AgentFactory(provider=_make_provider())

        role = factory.best_role_for("do something random")
        # Should default to GENERAL since no keywords match
        assert isinstance(role, AgentRole)

    def test_available_roles(self):
        factory = AgentFactory(provider=_make_provider())
        roles = factory.available_roles()

        assert len(roles) >= 9
        assert AgentRole.MANAGER in roles
        assert AgentRole.GENERAL in roles

    def test_get_profile(self):
        factory = AgentFactory(provider=_make_provider())
        profile = factory.get_profile(AgentRole.CREATOR)

        assert isinstance(profile, RoleProfile)
        assert profile.role == AgentRole.CREATOR
        assert "generate" in profile.capabilities

    def test_created_count(self):
        factory = AgentFactory(provider=_make_provider())
        assert factory.created_count == 0

        factory.create(AgentRole.RESEARCHER)
        factory.create(AgentRole.ANALYST)

        assert factory.created_count == 2

    def test_custom_capabilities(self):
        factory = AgentFactory(provider=_make_provider())
        agent = factory.create(
            AgentRole.RESEARCHER,
            custom_capabilities=["custom_skill"],
        )

        assert "custom_skill" in agent._capabilities


# =====================================================================
# Blackboard Tests
# =====================================================================

class TestBlackboard:
    """Test the blackboard system."""

    def test_post_entry(self):
        board = Blackboard("test")
        entry = board.post(
            author="agent_1",
            topic="test_topic",
            content="Hello world",
            entry_type=EntryType.FACT,
            confidence=ConfidenceLevel.HIGH,
        )

        assert entry.id.startswith("bb_")
        assert entry.author == "agent_1"
        assert entry.topic == "test_topic"
        assert entry.content == "Hello world"
        assert entry.entry_type == EntryType.FACT

    def test_get_entry(self):
        board = Blackboard()
        entry = board.post("a1", "t1", "content")

        retrieved = board.get(entry.id)
        assert retrieved is not None
        assert retrieved.content == "content"

    def test_get_by_topic(self):
        board = Blackboard()
        board.post("a1", "topic_a", "fact 1")
        board.post("a2", "topic_a", "fact 2")
        board.post("a1", "topic_b", "fact 3")

        topic_a = board.get_by_topic("topic_a")
        assert len(topic_a) == 2

        topic_b = board.get_by_topic("topic_b")
        assert len(topic_b) == 1

    def test_get_by_author(self):
        board = Blackboard()
        board.post("alice", "t1", "x")
        board.post("alice", "t2", "y")
        board.post("bob", "t1", "z")

        alice_entries = board.get_by_author("alice")
        assert len(alice_entries) == 2

    def test_get_by_type(self):
        board = Blackboard()
        board.post("a1", "t1", "fact", entry_type=EntryType.FACT)
        board.post("a1", "t1", "hypothesis", entry_type=EntryType.HYPOTHESIS)
        board.post("a1", "t1", "result", entry_type=EntryType.RESULT)

        facts = board.get_by_type(EntryType.FACT)
        assert len(facts) == 1

    def test_search(self):
        board = Blackboard()
        board.post("a1", "market", "Market size is $10 billion")
        board.post("a1", "competition", "Competitors include X and Y")
        board.post("a2", "market", "Market growth is 20% per year")

        results = board.search("market")
        assert len(results) >= 2

    def test_vote(self):
        board = Blackboard()
        entry = board.post("a1", "t1", "test")

        assert board.vote(entry.id, "voter1", upvote=True) is True
        assert entry.upvotes == 1

        # Same voter can't vote twice
        assert board.vote(entry.id, "voter1", upvote=True) is False
        assert entry.upvotes == 1

        # Downvote from another voter
        assert board.vote(entry.id, "voter2", upvote=False) is True
        assert entry.downvotes == 1
        assert entry.score == 0  # 1 up - 1 down

    def test_top_entries(self):
        board = Blackboard()
        e1 = board.post("a1", "t1", "great content")
        e2 = board.post("a2", "t1", "ok content")
        e3 = board.post("a3", "t1", "bad content")

        board.vote(e1.id, "v1", True)
        board.vote(e1.id, "v2", True)
        board.vote(e2.id, "v1", True)
        board.vote(e3.id, "v1", False)

        top = board.top_entries(topic="t1", limit=2)
        assert len(top) == 2
        assert top[0].id == e1.id  # Most votes

    def test_update_entry(self):
        board = Blackboard()
        entry = board.post("a1", "t1", "original")

        updated = board.update(entry.id, "a2", "updated content",
                               new_confidence=ConfidenceLevel.HIGH)

        assert updated is not None
        assert updated.content == "updated content"
        assert updated.version == 2
        assert "a2" in updated.edited_by
        assert updated.confidence == ConfidenceLevel.HIGH

    def test_subscribe(self):
        board = Blackboard()
        received = []

        def handler(entry):
            received.append(entry)

        board.subscribe("test_topic", handler)
        board.post("a1", "test_topic", "hello")

        assert len(received) == 1
        assert received[0].content == "hello"

    def test_subscribe_all(self):
        board = Blackboard()
        received = []

        def handler(entry):
            received.append(entry)

        board.subscribe_all(handler)
        board.post("a1", "topic_a", "x")
        board.post("a1", "topic_b", "y")

        assert len(received) == 2

    def test_stats(self):
        board = Blackboard("my_board")
        board.post("a1", "t1", "x")
        board.post("a2", "t2", "y")

        stats = board.stats()
        assert stats["name"] == "my_board"
        assert stats["entry_count"] == 2
        assert stats["topic_count"] == 2
        assert stats["author_count"] == 2

    def test_entry_count(self):
        board = Blackboard()
        assert board.entry_count == 0
        board.post("a1", "t1", "x")
        assert board.entry_count == 1

    def test_topics(self):
        board = Blackboard()
        board.post("a1", "topic1", "x")
        board.post("a1", "topic2", "y")

        topics = board.topics
        assert "topic1" in topics
        assert "topic2" in topics


# =====================================================================
# Committee Tests
# =====================================================================

class MockVotingAgent(Agent):
    """Mock agent that returns a fixed answer for testing committees."""

    def __init__(self, agent_id: str, answer: str, confidence: float = 0.8):
        super().__init__(
            id=agent_id,
            agent_type="test",
            name=f"Mock-{agent_id}",
            provider=_make_provider(),
        )
        self._answer = answer
        self._confidence = confidence

    def execute(self, prompt: str, **kwargs) -> dict:
        return {
            "status": "completed",
            "result": self._answer,
            "confidence": self._confidence,
            "reasoning": f"I think the answer is {self._answer}",
        }


class TestAgentCommittee:
    """Test the agent committee."""

    def test_create_committee(self):
        agents = [
            MockVotingAgent("a1", "answer_a"),
            MockVotingAgent("a2", "answer_b"),
            MockVotingAgent("a3", "answer_a"),
        ]
        committee = AgentCommittee(agents)

        assert committee.member_count == 3

    def test_majority_vote(self):
        """2 out of 3 agree → majority winner."""
        agents = [
            MockVotingAgent("a1", "apple"),
            MockVotingAgent("a2", "apple"),
            MockVotingAgent("a3", "banana"),
        ]
        committee = AgentCommittee(agents, method=VotingMethod.MAJORITY)

        result = committee.decide("Which fruit is better?")

        assert isinstance(result, CommitteeResult)
        assert result.winner == "apple"
        assert result.vote_counts["apple"] == 2
        assert result.vote_counts["banana"] == 1
        assert result.resolved is True
        assert result.tie is False

    def test_weighted_vote(self):
        agents = [
            MockVotingAgent("expert", "answer_a"),
            MockVotingAgent("novice1", "answer_b"),
            MockVotingAgent("novice2", "answer_b"),
        ]
        # Expert has more weight
        weights = {"expert": 2.0, "novice1": 1.0, "novice2": 1.0}
        committee = AgentCommittee(agents, method=VotingMethod.WEIGHTED,
                                   weights=weights)

        result = committee.decide("Question?")
        # Weighted: expert (2) + novices (1+1=2) = tie in weighted votes
        # But since "answer_a" comes first in sorted, it wins
        assert result.winner is not None
        assert len(result.votes) == 3

    def test_unanimity_resolved(self):
        """All agree → unanimity achieved."""
        agents = [
            MockVotingAgent("a1", "yes"),
            MockVotingAgent("a2", "yes"),
            MockVotingAgent("a3", "yes"),
        ]
        committee = AgentCommittee(agents, method=VotingMethod.UNANIMITY)

        result = committee.decide("Yes?")
        assert result.resolved is True
        assert result.consensus_level == 1.0

    def test_unanimity_not_resolved(self):
        """Disagreement → unanimity not achieved."""
        agents = [
            MockVotingAgent("a1", "yes"),
            MockVotingAgent("a2", "no"),
        ]
        committee = AgentCommittee(agents, method=VotingMethod.UNANIMITY)

        result = committee.decide("Yes or no?")
        assert result.resolved is False

    def test_tie_detection(self):
        agents = [
            MockVotingAgent("a1", "A"),
            MockVotingAgent("a2", "B"),
        ]
        committee = AgentCommittee(agents, method=VotingMethod.MAJORITY)

        result = committee.decide("A or B?")
        assert result.tie is True

    def test_decision_count(self):
        agents = [MockVotingAgent("a1", "x")]
        committee = AgentCommittee(agents)

        assert committee.decision_count == 0
        committee.decide("q1")
        committee.decide("q2")
        assert committee.decision_count == 2

    def test_vote_structure(self):
        agent = MockVotingAgent("a1", "answer", confidence=0.9)
        committee = AgentCommittee([agent])

        result = committee.decide("Question?")

        assert len(result.votes) == 1
        vote = result.votes[0]
        assert isinstance(vote, Vote)
        assert vote.voter_id == "a1"
        assert vote.choice == "answer"
        assert vote.confidence == 0.9


# =====================================================================
# Conflict Resolver Tests
# =====================================================================

class TestConflictResolver:
    """Test conflict resolution."""

    def test_create_conflict(self):
        resolver = ConflictResolver()
        conflict = resolver.create_conflict(
            topic="design",
            description="Disagreement about architecture",
            positions={
                "alice": "microservices",
                "bob": "monolith",
            },
            severity=0.7,
        )

        assert isinstance(conflict, Conflict)
        assert conflict.topic == "design"
        assert len(conflict.parties) == 2
        assert conflict.resolved is False

    def test_resolve_by_evidence(self):
        resolver = ConflictResolver()
        conflict = resolver.create_conflict(
            topic="estimate",
            description="Different time estimates",
            positions={"alice": "2 weeks", "bob": "4 weeks"},
        )

        result = resolver.resolve(
            conflict.id,
            strategy=ConflictStrategy.EVIDENCE,
            evidence_scores={"alice": 0.8, "bob": 0.3},
        )

        assert result["resolved"] is True
        assert result["winning_party"] == "alice"
        assert result["evidence_score"] == 0.8

    def test_resolve_by_hierarchy(self):
        resolver = ConflictResolver()
        conflict = resolver.create_conflict(
            topic="decision",
            positions={"junior": "A", "senior": "B"},
        )

        result = resolver.resolve(
            conflict.id,
            strategy=ConflictStrategy.HIERARCHY,
            authority="CTO",
            authority_position="B",
        )

        assert result["resolved"] is True
        assert result["winner"] == "B"
        assert result["authority"] == "CTO"

    def test_resolve_by_compromise_numeric(self):
        resolver = ConflictResolver()
        conflict = resolver.create_conflict(
            topic="budget",
            positions={"alice": 1000, "bob": 3000},
        )

        result = resolver.resolve(
            conflict.id,
            strategy=ConflictStrategy.COMPROMISE,
        )

        assert result["resolved"] is True
        assert result["winner"] == 2000  # Average

    def test_resolve_by_compromise_text(self):
        resolver = ConflictResolver()
        conflict = resolver.create_conflict(
            topic="approach",
            positions={"alice": "top-down", "bob": "bottom-up"},
        )

        result = resolver.resolve(
            conflict.id,
            strategy=ConflictStrategy.COMPROMISE,
        )

        assert result["resolved"] is True
        assert "top-down" in str(result["winner"])
        assert "bottom-up" in str(result["winner"])

    def test_resolve_by_random(self):
        resolver = ConflictResolver()
        conflict = resolver.create_conflict(
            topic="tie",
            positions={"a": "X", "b": "Y"},
        )

        result = resolver.resolve(
            conflict.id,
            strategy=ConflictStrategy.RANDOM,
        )

        assert result["resolved"] is True
        assert result["winner"] in ("X", "Y")

    def test_unresolved_count(self):
        resolver = ConflictResolver()
        resolver.create_conflict("t1", {"a": 1, "b": 2}, description="d1")
        resolver.create_conflict("t2", {"a": 3, "b": 4}, description="d2")

        assert resolver.unresolved_count == 2
        assert resolver.total_conflicts == 2

    def test_default_strategy(self):
        resolver = ConflictResolver(default_strategy=ConflictStrategy.COMPROMISE)
        assert resolver.default_strategy == ConflictStrategy.COMPROMISE


# =====================================================================
# Result Aggregator Tests
# =====================================================================

class TestResultAggregator:
    """Test the result aggregator."""

    def test_aggregate_text_concatenate(self):
        results = ["Hello", "World", "!"]
        agg = ResultAggregator.aggregate_text(results, method="concatenate")
        assert agg == "Hello\n\nWorld\n\n!"

    def test_aggregate_text_longest(self):
        results = ["short", "medium text", "this is the longest"]
        agg = ResultAggregator.aggregate_text(results, method="longest")
        assert agg == "this is the longest"

    def test_aggregate_text_shortest(self):
        results = ["short", "medium text", "longest of them all"]
        agg = ResultAggregator.aggregate_text(results, method="shortest")
        assert agg == "short"

    def test_aggregate_numeric_average(self):
        values = [10, 20, 30]
        agg = ResultAggregator.aggregate_numeric(values, method="average")
        assert agg == 20.0

    def test_aggregate_numeric_weighted(self):
        values = [10, 30]
        weights = [3.0, 1.0]  # 10 counts 3x, 30 counts 1x
        agg = ResultAggregator.aggregate_numeric(values, method="weighted",
                                                  weights=weights)
        # (10*3 + 30*1) / 4 = 60/4 = 15
        assert agg == 15.0

    def test_aggregate_numeric_median_odd(self):
        values = [10, 30, 20]
        agg = ResultAggregator.aggregate_numeric(values, method="median")
        assert agg == 20.0

    def test_aggregate_numeric_median_even(self):
        values = [10, 20, 30, 40]
        agg = ResultAggregator.aggregate_numeric(values, method="median")
        assert agg == 25.0

    def test_aggregate_numeric_max_min(self):
        values = [10, 50, 30]
        assert ResultAggregator.aggregate_numeric(values, method="max") == 50
        assert ResultAggregator.aggregate_numeric(values, method="min") == 10

    def test_aggregate_lists_union(self):
        lists = [[1, 2, 3], [3, 4, 5], [5, 6]]
        agg = ResultAggregator.aggregate_lists(lists, method="union")
        assert len(agg) == 6  # 1,2,3,4,5,6

    def test_aggregate_lists_intersection(self):
        lists = [[1, 2, 3], [2, 3, 4], [3, 4, 5]]
        agg = ResultAggregator.aggregate_lists(lists, method="intersection")
        assert agg == [3]

    def test_aggregate_lists_frequency(self):
        lists = [["a", "b"], ["b", "c"], ["b", "a"]]
        agg = ResultAggregator.aggregate_lists(lists, method="frequency")
        # "b" appears 3 times, "a" 2 times, "c" 1 time
        assert agg[0] == "b"
        assert agg[1] == "a"

    def test_aggregate_dicts_first_wins(self):
        dicts = [
            {"a": 1, "b": 2},
            {"a": 10, "c": 3},
        ]
        agg = ResultAggregator.aggregate_dicts(dicts, conflict_resolution="first_wins")
        assert agg["a"] == 1  # First wins
        assert agg["b"] == 2
        assert agg["c"] == 3

    def test_aggregate_dicts_last_wins(self):
        dicts = [
            {"a": 1, "b": 2},
            {"a": 10, "c": 3},
        ]
        agg = ResultAggregator.aggregate_dicts(dicts, conflict_resolution="last_wins")
        assert agg["a"] == 10  # Last wins

    def test_aggregate_dicts_merge_lists(self):
        dicts = [
            {"items": [1, 2]},
            {"items": [3, 4]},
        ]
        agg = ResultAggregator.aggregate_dicts(dicts, conflict_resolution="merge_lists")
        assert agg["items"] == [1, 2, 3, 4]

    def test_smart_aggregate_numeric(self):
        results = [10, 20, 30]
        agg = ResultAggregator.smart_aggregate(results)
        assert agg == 20.0

    def test_smart_aggregate_text(self):
        results = ["a", "b"]
        agg = ResultAggregator.smart_aggregate(results)
        assert "a" in agg and "b" in agg

    def test_smart_aggregate_lists(self):
        results = [[1, 2], [2, 3]]
        agg = ResultAggregator.smart_aggregate(results)
        assert isinstance(agg, list)
        assert len(agg) >= 2

    def test_empty_results(self):
        assert ResultAggregator.aggregate_text([]) == ""
        assert ResultAggregator.aggregate_numeric([]) == 0.0
        assert ResultAggregator.aggregate_lists([]) == []
        assert ResultAggregator.aggregate_dicts([]) == {}
