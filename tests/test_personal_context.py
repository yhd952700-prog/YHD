"""KAREN Personal Context — MASTER-SPEC §22 tests."""
import pytest

from src.ai.personal_context import (
    PersonalContextManager,
    Preference,
    UserProfile,
    get_personal_context,
)


class _FakeMemoryKernel:
    """可观测的 memory kernel stub —— 验证「写穿下沉」确实发生。"""

    def __init__(self):
        self.stored = []

    def store(self, key, value, tier=None, scope=None, tags=None):
        self.stored.append((key, value, set(tags or ())))
        return None


@pytest.fixture
def manager(tmp_path):
    return PersonalContextManager(db_path=str(tmp_path / "pc.db"))


@pytest.fixture
def manager_with_fake_memory(tmp_path):
    fake = _FakeMemoryKernel()
    mgr = PersonalContextManager(db_path=str(tmp_path / "pc2.db"), memory_kernel=fake)
    return mgr, fake


class TestPreferenceAndProfile:
    def test_preference_to_dict(self):
        p = Preference(key="language", value="zh", confidence=0.9)
        d = p.to_dict()
        assert d["key"] == "language"
        assert d["value"] == "zh"
        assert d["confidence"] == 0.9

    def test_profile_to_dict(self):
        prof = UserProfile(principal_id="u1", display_name="Test")
        assert prof.to_dict()["principal_id"] == "u1"


class TestPersonalContextManager:
    def test_preference_roundtrip(self, manager):
        manager.set_preference("u1", "language", "zh", confidence=0.9)
        p = manager.get_preference("u1", "language")
        assert p is not None
        assert p.value == "zh"
        assert p.confidence == 0.9
        assert p.source == "explicit"

    def test_preference_overwrite_updates(self, manager):
        manager.set_preference("u1", "tone", "formal", confidence=0.5)
        manager.set_preference("u1", "tone", "casual", confidence=0.8)
        p = manager.get_preference("u1", "tone")
        assert p.value == "casual"
        assert p.confidence == 0.8

    def test_get_all_preferences(self, manager):
        manager.set_preference("u1", "a", 1)
        manager.set_preference("u1", "b", 2)
        prefs = manager.get_all_preferences("u1")
        assert set(prefs.keys()) == {"a", "b"}

    def test_get_missing_preference_returns_none(self, manager):
        assert manager.get_preference("u1", "nope") is None

    def test_record_and_get_fact(self, manager):
        manager.record_fact("u1", "city", "Shanghai")
        assert manager.get_fact("u1", "city") == "Shanghai"
        assert manager.get_facts("u1") == {"city": "Shanghai"}

    def test_fact_value_can_be_complex(self, manager):
        manager.record_fact("u1", "stack", {"lang": "python", "db": "postgres"})
        assert manager.get_fact("u1", "stack") == {"lang": "python", "db": "postgres"}

    def test_interests_expertise_relationships(self, manager):
        manager.add_interest("u1", "AI OS")
        manager.add_interest("u1", "AI OS")  # 去重
        manager.add_expertise("u1", "Python")
        manager.add_relationship("u1", "Alice", "colleague")
        prof = manager.get_profile("u1")
        assert prof.interests == ["AI OS"]
        assert prof.expertise == ["Python"]
        assert prof.relationships == {"Alice": "colleague"}


class TestProfileAggregation:
    def test_get_profile_snapshot(self, manager):
        manager.set_display_name("u1", "Liu")
        manager.set_preference("u1", "language", "zh")
        manager.record_fact("u1", "role", "principal engineer")
        manager.add_expertise("u1", "systems")
        prof = manager.get_profile("u1")
        assert prof.principal_id == "u1"
        assert prof.display_name == "Liu"
        assert "language" in prof.preferences
        assert prof.facts["role"] == "principal engineer"
        assert "systems" in prof.expertise

    def test_summarize_includes_preferences_and_facts(self, manager):
        manager.set_display_name("u1", "Liu")
        manager.set_preference("u1", "language", "zh", confidence=0.9)
        manager.record_fact("u1", "city", "Shanghai")
        summary = manager.summarize("u1")
        assert "Liu" in summary
        assert "language" in summary
        assert "city" in summary

    def test_summarize_empty_profile(self, manager):
        summary = manager.summarize("u1")
        assert "暂无个人画像" in summary

    def test_has_profile_false_when_empty(self, manager):
        """空画像 has_profile=False —— 供对话链路判断要不要注入画像段。"""
        assert manager.has_profile("u1") is False

    def test_has_profile_true_after_any_write(self, manager):
        manager.record_fact("u1", "city", "Shanghai")
        assert manager.has_profile("u1") is True
        # 另一主体仍为空（隔离）
        assert manager.has_profile("u2") is False

    def test_suggest_context_matches(self, manager):
        manager.set_preference("u1", "language", "Chinese")
        manager.record_fact("u1", "city", "Shanghai")
        manager.add_interest("u1", "distributed systems")
        suggestions = manager.suggest_context("u1", "Shanghai")
        assert any("city" in s for s in suggestions)
        suggestions2 = manager.suggest_context("u1", "distributed")
        assert any("关注领域" in s for s in suggestions2)


class TestIsolationAndPersistence:
    def test_per_principal_isolation(self, manager):
        manager.set_preference("alice", "language", "en")
        manager.set_preference("bob", "language", "zh")
        assert manager.get_preference("alice", "language").value == "en"
        assert manager.get_preference("bob", "language").value == "zh"

    def test_clear_only_target_principal(self, manager):
        manager.set_preference("alice", "x", 1)
        manager.set_preference("bob", "x", 2)
        manager.clear("alice")
        assert manager.get_preference("alice", "x") is None
        assert manager.get_preference("bob", "x").value == 2

    def test_persistence_across_instances(self, tmp_path):
        db = str(tmp_path / "persist.db")
        a = PersonalContextManager(db_path=db)
        a.set_preference("u1", "language", "zh")
        a.record_fact("u1", "city", "Shanghai")
        # 新实例读同一 db 文件，应恢复画像。
        b = PersonalContextManager(db_path=db)
        assert b.get_preference("u1", "language").value == "zh"
        assert b.get_fact("u1", "city") == "Shanghai"


class TestMemoryPermissionSync:
    def test_set_preference_syncs_to_memory_kernel(self, manager_with_fake_memory):
        mgr, fake = manager_with_fake_memory
        mgr.set_preference("u1", "language", "zh")
        assert len(fake.stored) == 1
        key, value, tags = fake.stored[0]
        assert "profile:u1:" in key
        assert value == "zh"
        assert "personal" in tags

    def test_record_fact_syncs_to_memory_kernel(self, manager_with_fake_memory):
        mgr, fake = manager_with_fake_memory
        mgr.record_fact("u1", "city", "Shanghai")
        assert len(fake.stored) == 1
        assert "profile:u1:fact:city" in fake.stored[0][0]


class TestSingleton:
    def test_get_personal_context_returns_same_instance(self):
        a = get_personal_context()
        b = get_personal_context()
        assert a is b
