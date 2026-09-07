"""ZOON Specialized Agent Framework — MASTER-SPEC §65-67 tests."""
import uuid

import pytest

from src.ai.domain_templates import (
    BUILTIN_TEMPLATES,
    DomainTemplate,
    DomainTemplateRegistry,
    SpecializedAgentFactory,
    build_builtin_registry,
    get_domain_registry,
)


# §67 至少支持的领域清单
REQUIRED_DOMAINS = [
    "research", "coding", "data", "security", "finance", "marketing",
    "devops", "operations", "monitoring", "science", "legal_research",
    "product", "qa",
]


class _StubProvider:
    def generate_with_retry(self, prompt, **kwargs):
        return "stub-response"


def _patch_provider(monkeypatch):
    from src.ai import agent_factory as af
    monkeypatch.setattr(af, "get_provider", lambda: _StubProvider())


class TestDomainTemplate:
    def test_to_dict_covers_chain(self):
        t = DomainTemplate(
            domain="research",
            display_name="Research",
            agent_type="research",
            system_prompt="p",
            capabilities=["read"],
            skills=["s1"],
            knowledge_domains=["k1"],
            tools=["t1"],
            policies=["read:docs"],
            evaluation_suite="research.eval",
            memory_policy="mid_term",
        )
        d = t.to_dict()
        assert d["domain"] == "research"
        assert d["capabilities"] == ["read"]
        assert d["policies"] == ["read:docs"]
        assert d["memory_policy"] == "mid_term"


class TestDomainTemplateRegistry:
    def test_register_get_list(self):
        r = DomainTemplateRegistry()
        r.register(DomainTemplate("coding", "Coding", "coding", "p"))
        assert r.get("coding") is not None
        assert r.list() == ["coding"]
        assert "coding" in r
        assert len(r) == 1

    def test_register_duplicate_raises(self):
        r = DomainTemplateRegistry()
        r.register(DomainTemplate("coding", "Coding", "coding", "p"))
        with pytest.raises(ValueError):
            r.register(DomainTemplate("coding", "Coding v2", "coding", "p2"))

    def test_get_missing_returns_none(self):
        r = DomainTemplateRegistry()
        assert r.get("nope") is None

    def test_unregister(self):
        r = DomainTemplateRegistry()
        r.register(DomainTemplate("coding", "Coding", "coding", "p"))
        assert r.unregister("coding") is True
        assert r.unregister("coding") is False
        assert len(r) == 0


class TestBuiltinTemplates:
    def test_covers_required_domains(self):
        domains = {t.domain for t in BUILTIN_TEMPLATES}
        assert set(REQUIRED_DOMAINS) <= domains

    def test_domains_are_unique(self):
        domains = [t.domain for t in BUILTIN_TEMPLATES]
        assert len(domains) == len(set(domains))

    def test_every_template_has_identity_and_prompt(self):
        for t in BUILTIN_TEMPLATES:
            assert t.domain
            assert t.display_name
            assert t.agent_type
            assert t.system_prompt.strip(), f"{t.domain} has empty system_prompt"

    def test_build_builtin_registry_has_13(self):
        r = build_builtin_registry()
        assert len(r) == len(REQUIRED_DOMAINS)


class TestSpecializedAgentFactory:
    def test_to_agent_spec_maps_fields(self):
        t = DomainTemplate(
            domain="security",
            display_name="Security",
            agent_type="security",
            system_prompt="security persona",
            capabilities=["audit"],
            policies=["read:security"],
            evaluation_suite="security.eval",
            memory_policy="mid_term",
        )
        spec = SpecializedAgentFactory().to_agent_spec(t, goal="assess")
        assert spec.agent_type == "security"
        assert spec.goal == "assess"
        assert spec.system_prompt == "security persona"
        assert spec.capabilities == ["audit"]
        assert spec.permissions == ["read:security"]
        assert spec.evaluation_suite == "security.eval"
        assert spec.memory_policy == "mid_term"

    def test_create_agent_end_to_end(self, monkeypatch):
        _patch_provider(monkeypatch)
        name = f"zoon-{uuid.uuid4().hex[:8]}"
        out = SpecializedAgentFactory().create_agent(
            "research", goal="summarize X", name=name
        )
        assert out["agent"] is not None
        assert out["agent"].agent_type == "research"
        assert out["agent"].system_prompt.startswith("你是一名严谨的研究分析师")
        assert out["identity"] is not None
        assert out["identity"].principal == name
        assert out["memory"] is not None
        assert out["policy"] is not None
        assert out["domain"] == "research"
        assert out["template"].domain == "research"

    def test_create_agent_unknown_domain_raises(self):
        with pytest.raises(ValueError):
            SpecializedAgentFactory().create_agent("not_a_domain", goal="g")


class TestGetDomainRegistry:
    def test_singleton_has_builtin_templates(self):
        r = get_domain_registry()
        assert len(r) == len(REQUIRED_DOMAINS)
        assert r.get("finance") is not None
        assert r.get("qa") is not None
