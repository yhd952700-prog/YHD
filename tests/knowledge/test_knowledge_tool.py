"""M5 回归：KnowledgeSearchTool 必须走真实向量检索，未配置时明确不可用。"""

from __future__ import annotations

import pytest

from src.ai.tools.builtin import KnowledgeSearchTool
from src.knowledge.tool import knowledge_search

_PROVIDER_ENVS = (
    "LIUHAO_EMBEDDING_PROVIDER",
    "LIUHAO_EMBEDDING_MODEL",
    "LIUHAO_EMBEDDING_API_KEY",
    "LIUHAO_KNOWLEDGE_ALLOW_MOCK",
    "OPENAI_API_KEY",
    "OPENAI_BASE_URL",
    "OLLAMA_BASE_URL",
    "LIUHAO_OLLAMA_BASE_URL",
    "LIUHAO_ENV",
    "APP_ENV",
)


@pytest.fixture
def unconfigured_env(monkeypatch):
    for name in _PROVIDER_ENVS:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("LIUHAO_ENV", "production")
    monkeypatch.setenv("LIUHAO_EMBEDDING_PROVIDER", "auto")
    return monkeypatch


@pytest.fixture
def dev_mock_env(unconfigured_env):
    unconfigured_env.setenv("LIUHAO_ENV", "development")
    unconfigured_env.setenv("LIUHAO_KNOWLEDGE_ALLOW_MOCK", "1")
    unconfigured_env.setenv("LIUHAO_EMBEDDING_PROVIDER", "mock")
    return unconfigured_env


def test_unconfigured_tool_fails_without_fake_results(unconfigured_env):
    result = KnowledgeSearchTool().execute(query="python")

    assert result.ok is False
    assert "未启用" in (result.error or "")
    assert result.output is None


def test_unconfigured_function_reports_unavailable_not_canned_content(unconfigured_env):
    payload = knowledge_search("python")

    assert payload["available"] is False
    assert payload["results"] == []
    assert "未启用" in payload["reason"]
    # 旧实现会返回硬编码的 "Python is a high-level..."，这里必须为空。
    assert payload.get("results") == []


def test_configured_tool_returns_real_ingested_chunks(dev_mock_env):
    from src.knowledge.documents import KnowledgeDocumentService

    service = KnowledgeDocumentService()
    service.ingest("鎏灏知识库专用检索文本：局域网驱动舱。", doc_id="tool-doc")

    result = KnowledgeSearchTool().execute(query="局域网", top_k=3)

    assert result.ok is True
    assert result.output["total_results"] >= 1
    assert any("局域网" in item["text"] for item in result.output["results"])
    assert all(item["document_id"] == "tool-doc" for item in result.output["results"])


def test_tool_has_no_hardcoded_corpus():
    assert not hasattr(KnowledgeSearchTool, "_KNOWLEDGE_BASE")
    assert "knowledge_search" == KnowledgeSearchTool.name
