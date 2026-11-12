"""M3 回归：Ollama 旧版本（/api/embed 返回 404/400）必须回退到 /api/embeddings。"""

from __future__ import annotations

import pytest

from src.knowledge import embedding as embedding_module


def _endpoint_404(url: str) -> embedding_module.EmbeddingEndpointError:
    return embedding_module.EmbeddingEndpointError(
        f"embedding endpoint {url} returned HTTP 404", status_code=404
    )


class TestOllamaEndpointFallback:
    def test_404_on_api_embed_falls_back_to_legacy_endpoint(self, monkeypatch):
        calls = []

        def fake_post(url, payload, headers=None, timeout=None):
            calls.append((url, payload))
            if url.endswith("/api/embed"):
                raise _endpoint_404(url)
            return {"embedding": [0.1, 0.2]}

        monkeypatch.setattr(embedding_module, "_post_json", fake_post)
        provider = embedding_module.OllamaEmbedder(
            model="nomic", base_url="http://127.0.0.1:11434"
        )

        assert provider.embed(["a", "b"]) == [[0.1, 0.2], [0.1, 0.2]]
        assert [url for url, _ in calls] == [
            "http://127.0.0.1:11434/api/embed",
            "http://127.0.0.1:11434/api/embeddings",
            "http://127.0.0.1:11434/api/embeddings",
        ]

    def test_400_on_api_embed_also_falls_back(self, monkeypatch):
        def fake_post(url, payload, headers=None, timeout=None):
            if url.endswith("/api/embed"):
                raise embedding_module.EmbeddingEndpointError(
                    f"embedding endpoint {url} returned HTTP 400", status_code=400
                )
            return {"embedding": [1.0]}

        monkeypatch.setattr(embedding_module, "_post_json", fake_post)
        provider = embedding_module.OllamaEmbedder(
            model="nomic", base_url="http://127.0.0.1:11434"
        )
        assert provider.embed(["x"]) == [[1.0]]

    def test_both_endpoints_missing_reports_version_unsupported_not_unconfigured(
        self, monkeypatch
    ):
        def fake_post(url, payload, headers=None, timeout=None):
            raise embedding_module.EmbeddingEndpointError(
                f"embedding endpoint {url} returned HTTP 404", status_code=404
            )

        monkeypatch.setattr(embedding_module, "_post_json", fake_post)
        provider = embedding_module.OllamaEmbedder(
            model="nomic", base_url="http://127.0.0.1:11434"
        )
        with pytest.raises(embedding_module.EmbeddingUnavailableError) as info:
            provider.embed(["x"])

        message = str(info.value)
        assert "版本不支持" in message
        assert "不是未配置" in message

    def test_non_version_errors_are_not_swallowed_as_fallback(self, monkeypatch):
        calls = []

        def fake_post(url, payload, headers=None, timeout=None):
            calls.append(url)
            raise embedding_module.EmbeddingEndpointError(
                f"embedding endpoint {url} returned HTTP 500", status_code=500
            )

        monkeypatch.setattr(embedding_module, "_post_json", fake_post)
        provider = embedding_module.OllamaEmbedder(
            model="nomic", base_url="http://127.0.0.1:11434"
        )
        with pytest.raises(embedding_module.EmbeddingEndpointError) as info:
            provider.embed(["x"])
        assert info.value.status_code == 500
        assert calls == ["http://127.0.0.1:11434/api/embed"]

    def test_unreachable_local_ollama_reports_how_to_enable(self, monkeypatch):
        monkeypatch.delenv("OLLAMA_BASE_URL", raising=False)
        monkeypatch.delenv("LIUHAO_OLLAMA_BASE_URL", raising=False)
        monkeypatch.setenv("LIUHAO_EMBEDDING_PROVIDER", "ollama")
        # 纯安装会自动探测 config 声明的本机 Ollama；这里显式模拟"没启动"，
        # 让提示保持可复现，而不是依赖跑测试的机器上 Ollama 是否在运行。
        monkeypatch.setattr(
            embedding_module, "_installed_ollama_models", lambda base_url: None
        )
        with pytest.raises(embedding_module.EmbeddingUnavailableError) as info:
            embedding_module.build_embedding_provider()
        message = str(info.value)
        assert "知识库未启用" in message
        assert "未运行" in message


class TestOllamaShapeFallbackStillWorks:
    def test_200_with_wrong_shape_falls_back(self, monkeypatch):
        def fake_post(url, payload, headers=None, timeout=None):
            if url.endswith("/api/embed"):
                return {"embeddings": "not-a-list"}
            return {"embedding": [0.3]}

        monkeypatch.setattr(embedding_module, "_post_json", fake_post)
        provider = embedding_module.OllamaEmbedder(
            model="nomic", base_url="http://127.0.0.1:11434"
        )
        assert provider.embed(["x"]) == [[0.3]]
