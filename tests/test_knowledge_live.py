"""知识库活链路：真实 embedding provider、持久化单例、fail-closed 与 HTTP 面。

设计约束：
* vector store 是进程级 SQLite 单例（conftest 已按用例隔离文件）；
* mock embedding 只在 ``LIUHAO_ENV=development`` + 显式 opt-in 时允许；
* 未配置 provider 时 HTTP 返回 503/``enabled=false``，绝不返回伪造向量。
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from src.knowledge import embedding as embedding_module
from src.knowledge import vector_store as vector_store_module
from src.knowledge.documents import KnowledgeDocumentService

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
def clean_env(monkeypatch):
    for name in _PROVIDER_ENVS:
        monkeypatch.delenv(name, raising=False)
    yield monkeypatch


@pytest.fixture
def dev_mock_env(clean_env):
    clean_env.setenv("LIUHAO_ENV", "development")
    clean_env.setenv("LIUHAO_KNOWLEDGE_ALLOW_MOCK", "1")
    clean_env.setenv("LIUHAO_EMBEDDING_PROVIDER", "mock")
    yield clean_env


class TestVectorStorePersistence:
    def test_singleton_is_reused(self):
        first = vector_store_module.get_vector_store()
        assert vector_store_module.get_vector_store() is first

    def test_vectors_survive_a_singleton_reset(self, dev_mock_env):
        service = KnowledgeDocumentService()
        service.ingest("鎏灏知识库的第一段文本。", doc_id="persisted")
        store = vector_store_module.get_vector_store()
        assert store.count() == service.status()["vectors"]

        vector_store_module.reset_vector_store()
        reloaded = vector_store_module.get_vector_store()
        assert reloaded.count() == store.count()
        assert reloaded.get("persisted#0") is not None

    def test_disk_file_is_created(self):
        store = vector_store_module.get_vector_store()
        assert store.db_path.exists()


class TestEmbeddingProviderSelection:
    def test_production_without_config_fails_closed(self, clean_env):
        clean_env.setenv("LIUHAO_ENV", "production")
        with pytest.raises(embedding_module.EmbeddingUnavailableError) as info:
            embedding_module.build_embedding_provider()
        assert "未启用" in str(info.value)

    def test_mock_requires_explicit_dev_opt_in(self, clean_env):
        clean_env.setenv("LIUHAO_ENV", "production")
        clean_env.setenv("LIUHAO_KNOWLEDGE_ALLOW_MOCK", "1")
        clean_env.setenv("LIUHAO_EMBEDDING_PROVIDER", "mock")
        with pytest.raises(embedding_module.EmbeddingUnavailableError):
            embedding_module.build_embedding_provider()

    def test_auto_never_goes_cloud_even_when_openai_key_exists(self, clean_env):
        # 环境里恰好有 OPENAI_API_KEY 不再等于"允许把知识库文本发到 OpenAI"。
        clean_env.setenv("LIUHAO_ENV", "production")
        clean_env.setenv("OPENAI_API_KEY", "sk-test")
        with pytest.raises(embedding_module.EmbeddingUnavailableError):
            embedding_module.build_embedding_provider()

    def test_explicit_openai_still_uses_a_cloud_key(self, clean_env):
        clean_env.setenv("LIUHAO_ENV", "production")
        clean_env.setenv("OPENAI_API_KEY", "sk-test")
        clean_env.setenv("LIUHAO_EMBEDDING_PROVIDER", "openai")
        provider = embedding_module.build_embedding_provider()
        assert provider.name == "openai"
        assert provider.model == "text-embedding-3-small"

    def test_auto_uses_ollama_when_base_url_is_set(self, clean_env):
        clean_env.setenv("OLLAMA_BASE_URL", "http://127.0.0.1:11434")
        provider = embedding_module.build_embedding_provider()
        assert provider.name == "ollama"
        assert provider.model == "nomic-embed-text"

    def test_auto_falls_back_to_config_ollama_on_a_plain_install(self, clean_env, monkeypatch):
        # 纯安装：没有任何 embedding 环境变量，但 config/default.yaml 声明了本机
        # Ollama，且模型已安装时，知识库应自动可用，而不是显示“未启用”。
        monkeypatch.setattr(
            embedding_module,
            "_installed_ollama_models",
            lambda base_url: ["nomic-embed-text:latest", "qwen2.5:3b"],
        )
        provider = embedding_module.build_embedding_provider()
        assert provider.name == "ollama"
        assert provider.model == "nomic-embed-text"
        assert "localhost:11434" in provider.base_url

    def test_missing_embedding_model_names_the_pull_command(self, clean_env, monkeypatch):
        monkeypatch.setattr(
            embedding_module, "_installed_ollama_models", lambda base_url: ["qwen2.5:3b"]
        )
        with pytest.raises(embedding_module.EmbeddingUnavailableError) as info:
            embedding_module.build_embedding_provider()
        assert "ollama pull nomic-embed-text" in str(info.value)

    def test_ollama_not_running_is_a_readable_reason(self, clean_env, monkeypatch):
        monkeypatch.setattr(
            embedding_module, "_installed_ollama_models", lambda base_url: None
        )
        with pytest.raises(embedding_module.EmbeddingUnavailableError) as info:
            embedding_module.build_embedding_provider()
        assert "未运行" in str(info.value)
        assert "未启用" in str(info.value)

    def test_openai_compatible_payload_is_index_sorted(self, monkeypatch, clean_env):
        captured = {}

        def fake_post(url, payload, headers=None, timeout=None):
            captured.update(url=url, payload=payload, headers=headers or {})
            return {
                "data": [
                    {"index": 1, "embedding": [0.0, 1.0]},
                    {"index": 0, "embedding": [1.0, 0.0]},
                ]
            }

        monkeypatch.setattr(embedding_module, "_post_json", fake_post)
        provider = embedding_module.OpenAICompatibleEmbedder(
            model="m", base_url="https://api.example/v1", api_key="sk"
        )
        vectors = provider.embed(["a", "b"])
        assert vectors == [[1.0, 0.0], [0.0, 1.0]]
        assert captured["url"] == "https://api.example/v1/embeddings"
        assert captured["headers"]["Authorization"] == "Bearer sk"

    def test_ollama_falls_back_to_single_prompt_endpoint(self, monkeypatch):
        calls = []

        def fake_post(url, payload, headers=None, timeout=None):
            calls.append(url)
            if url.endswith("/api/embed"):
                return {"embeddings": []}
            return {"embedding": [0.0, 1.0]}

        monkeypatch.setattr(embedding_module, "_post_json", fake_post)
        provider = embedding_module.OllamaEmbedder(
            model="nomic", base_url="http://127.0.0.1:11434"
        )
        assert provider.embed(["a", "b"]) == [[0.0, 1.0], [0.0, 1.0]]
        assert any(url.endswith("/api/embeddings") for url in calls)


class TestServiceFailClosed:
    def test_status_is_disabled_not_faked(self, clean_env):
        clean_env.setenv("LIUHAO_ENV", "production")
        status = KnowledgeDocumentService().status()
        assert status["enabled"] is False
        assert "未启用" in status["message"]
        assert status["provider"] is None

    def test_ingest_raises_when_disabled(self, clean_env):
        clean_env.setenv("LIUHAO_ENV", "production")
        with pytest.raises(embedding_module.EmbeddingUnavailableError):
            KnowledgeDocumentService().ingest("text")

    def test_ingest_then_search_returns_real_chunks(self, dev_mock_env):
        service = KnowledgeDocumentService()
        result = service.ingest("鎏灏是小豪的操作系统。\n它需要被真实检索。", doc_id="kb")
        assert result["document_id"] == "kb"
        assert result["chunks"] >= 1

        hits = service.search("操作系")["hits"]
        assert hits, "ingest 后的向量必须能被检索到"
        assert any("鎏灏" in hit["text"] for hit in hits)
        assert hits[0]["metadata"]["document_id"] == "kb"


@pytest.fixture
def http_env(dev_mock_env):
    yield dev_mock_env


@pytest.fixture
def client(http_env, monkeypatch, tmp_path):
    monkeypatch.setenv("LIUHAO_HUMAN_IDENTITIES_BACKEND", "file")
    monkeypatch.setenv("LIUHAO_HUMAN_IDENTITIES_FILE", str(tmp_path / "humans.json"))
    import src.kernels.identity as identity_module

    monkeypatch.setattr(identity_module, "_global_manager", None)
    with TestClient(__import__("src.gateway.main", fromlist=["get_app"]).get_app()) as c:
        yield c


def _l0_headers():
    from src.security import get_jwt_handler

    token, _ = get_jwt_handler().create_token(
        subject="kb-human", metadata={"kind": "human", "scope": "L0"}
    )
    return {"Authorization": f"Bearer {token}"}


class TestKnowledgeHttp:
    def test_requires_auth(self, client):
        assert client.get("/v1/knowledge/status").status_code == 401
        assert client.post("/v1/knowledge/search", json={"query": "x"}).status_code == 401

    def test_status_reports_enabled_under_dev_mock(self, client):
        response = client.get("/v1/knowledge/status", headers=_l0_headers())
        assert response.status_code == 200
        body = response.json()
        assert body["enabled"] is True
        assert body["provider"] == "mock"

    def test_ingest_then_search_over_http(self, client):
        headers = _l0_headers()
        ingested = client.post(
            "/v1/knowledge/documents",
            json={"text": "手机通过局域网访问鎏灏驾驶舱。", "doc_id": "manual"},
            headers=headers,
        )
        assert ingested.status_code == 200, ingested.text
        assert ingested.json()["document_id"] == "manual"

        searched = client.post(
            "/v1/knowledge/search",
            json={"query": "局域网", "top_k": 3},
            headers=headers,
        )
        assert searched.status_code == 200, searched.text
        assert searched.json()["hits"]

    def test_legacy_prefix_still_works(self, client):
        headers = _l0_headers()
        response = client.post(
            "/knowledge/documents",
            json={"text": "legacy alias text", "doc_id": "legacy"},
            headers=headers,
        )
        assert response.status_code == 200, response.text
        assert response.json()["document_id"] == "legacy"

    def test_unconfigured_knowledge_answers_503_not_fake_vectors(
        self, client, monkeypatch
    ):
        from src.knowledge.documents import reset_knowledge_service

        for name in _PROVIDER_ENVS:
            monkeypatch.delenv(name, raising=False)
        monkeypatch.setenv("LIUHAO_ENV", "production")
        monkeypatch.setenv("LIUHAO_EMBEDDING_PROVIDER", "auto")
        reset_knowledge_service()
        response = client.post(
            "/v1/knowledge/search",
            json={"query": "x"},
            headers=_l0_headers(),
        )
        assert response.status_code == 503, response.text
        assert "未启用" in response.json()["detail"]

    def test_query_returns_retrieval_without_a_mock_answer(self, client):
        headers = _l0_headers()
        client.post(
            "/v1/knowledge/documents",
            json={"text": "只能检索，不伪造生成回答。", "doc_id": "no-fake"},
            headers=headers,
        )
        response = client.post(
            "/v1/knowledge/query", json={"query": "检索"}, headers=headers
        )
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["answer"] == ""
        assert body["sources"]
        assert "仅检索" in body["metadata"]["note"]
