"""对话链路的知识库注入：检索、引用、降级、开关、边界。

全部走真实 HTTP 面 + 真实 ``KnowledgeDocumentService``（mock embedding 仅限
显式开发/测试模式），并替换 LLM 为捕获提示词的替身，以证明上下文确实进了
system prompt，而不是只出现在响应元信息里。
"""
from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client(monkeypatch, tmp_path):
    monkeypatch.setenv("LIUHAO_HUMAN_IDENTITIES_BACKEND", "file")
    monkeypatch.setenv("LIUHAO_HUMAN_IDENTITIES_FILE", str(tmp_path / "humans.json"))
    monkeypatch.setenv(
        "LIUHAO_HUMAN_IDENTITIES_INTEGRITY_KEY", "chat-knowledge-test-key"
    )
    # mock embedding 只在显式 dev/test 模式允许（src.knowledge 的 fail-closed 规则）。
    monkeypatch.setenv("LIUHAO_ENV", "development")
    monkeypatch.setenv("LIUHAO_KNOWLEDGE_ALLOW_MOCK", "1")
    monkeypatch.setenv("LIUHAO_EMBEDDING_PROVIDER", "mock")
    monkeypatch.setenv("LIUHAO_KNOWLEDGE_DB", str(tmp_path / "kb.sqlite3"))
    # mock 向量全为 0，余弦相似度为 0；允许 0 分命中以便在测试里验证注入链路。
    # 分差门槛在默认注入用例里设为 0；需要验证“平铺候选不注入”的用例会覆盖它。
    monkeypatch.setenv("LIUHAO_CHAT_KNOWLEDGE_MIN_SCORE", "0")
    monkeypatch.setenv("LIUHAO_CHAT_KNOWLEDGE_MIN_MARGIN", "0")

    import src.kernels.identity as identity_module
    from src.knowledge.documents import reset_knowledge_service
    from src.knowledge.vector_store import reset_vector_store

    import src.gateway.chat as chat_module

    monkeypatch.setattr(identity_module, "_global_manager", None)
    reset_knowledge_service()
    reset_vector_store()
    # 会话实例是进程级单例：不清空的话第二个测试会复用第一个测试的
    # assistant，替换掉 LLM 替身也看不到它。
    chat_module._sessions.clear()
    chat_module._session_users.clear()
    from src.gateway.main import get_app

    with TestClient(get_app()) as test_client:
        yield test_client
    chat_module._sessions.clear()
    chat_module._session_users.clear()
    reset_knowledge_service()
    reset_vector_store()


def _token(scope="L0"):
    from src.security import get_jwt_handler

    token, _ = get_jwt_handler().create_token(
        subject="knowledge-human",
        metadata={"kind": "human", "scope": scope},
    )
    return {"Authorization": f"Bearer {token}"}


class _PromptCapturingAssistant:
    """替身只替换 LLM，保留网关真实会话路由与提示词注入。"""

    def __init__(self, name="x", profile_principal=None, **kwargs):
        self.name = name
        self.profile_principal = profile_principal
        self.system_prompt = "BASE-SYSTEM-PROMPT"
        self.provider = None
        self.turn = 0
        self.seen_prompts = []

    def chat(self, message):
        self.seen_prompts.append(self.system_prompt)
        return {
            "reply": "ok",
            "status": "completed",
            "turn": 1,
            "correlation_id": "c1",
            "agent": self.name,
        }

    def chat_stream(self, message):
        self.seen_prompts.append(self.system_prompt)
        yield "o"
        yield "k"


@pytest.fixture
def fake_llm(monkeypatch):
    import src.gateway.chat as chat_mod

    instances = []

    class Factory(_PromptCapturingAssistant):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            instances.append(self)

    monkeypatch.setattr(chat_mod, "LiuHaoAssistant", Factory)
    return instances


def _ingest(client, text: str, doc_id: str):
    response = client.post(
        "/v1/knowledge/documents",
        headers=_token(),
        json={"text": text, "doc_id": doc_id},
    )
    assert response.status_code == 200, response.text
    return response.json()


def test_empty_knowledge_degrades_and_reports_it(client, fake_llm):
    response = client.post("/v1/chat", headers=_token(), json={"message": "你好"})
    assert response.status_code == 200, response.text
    body = response.json()
    knowledge = body["knowledge"]
    assert knowledge["used"] is False
    assert knowledge["reason"] == "empty"
    assert "本次未使用知识库" in knowledge["message"]
    # “库里没有文档”与“库里没有答案”必须说清楚，不能混用同一句文案。
    assert "知识库为空" in knowledge["message"]
    assert "未找到相关内容" not in knowledge["message"]
    # 普通对话没有被破坏。
    assert body["reply"] == "ok"
    assert fake_llm[0].seen_prompts == ["BASE-SYSTEM-PROMPT"]


def test_retrieval_is_injected_and_cited(client, fake_llm):
    _ingest(
        client,
        "鎏灏知识库测试片段：部署口令必须存放在加密配置中。",
        doc_id="handbook",
    )
    response = client.post(
        "/v1/chat",
        headers=_token(),
        json={"message": "部署口令应该放在哪里？"},
    )
    assert response.status_code == 200, response.text
    knowledge = response.json()["knowledge"]
    assert knowledge["used"] is True
    assert knowledge["decision"] == "inject"
    assert knowledge["hits"] >= 1
    assert knowledge["diagnostics"]["top_score"] >= 0
    source = knowledge["sources"][0]
    assert source["document_id"] == "handbook"
    assert source["chunk_index"] == 0
    assert "加密配置" in source["snippet"]
    # 关键证明：上下文确实被拼进了 system prompt。
    prompt = fake_llm[0].seen_prompts[0]
    assert "[知识库上下文]" in prompt
    assert "部署口令必须存放在加密配置中" in prompt
    assert "BASE-SYSTEM-PROMPT" in prompt
    # 注入是临时覆盖，调用后已恢复，不会污染后续轮次。
    assert fake_llm[0].system_prompt == "BASE-SYSTEM-PROMPT"


def test_user_can_disable_retrieval(client, fake_llm):
    _ingest(client, "被关闭检索时不应进入提示词。", doc_id="doc-a")
    response = client.post(
        "/v1/chat",
        headers=_token(),
        json={"message": "hi", "use_knowledge": False},
    )
    assert response.status_code == 200, response.text
    knowledge = response.json()["knowledge"]
    assert knowledge["used"] is False
    assert knowledge["reason"] == "disabled_by_user"
    assert fake_llm[0].seen_prompts == ["BASE-SYSTEM-PROMPT"]


def test_retrieval_failure_degrades_silently(client, fake_llm, monkeypatch):
    import src.knowledge.documents as documents

    class BrokenService:
        def status(self):
            return {"enabled": True, "vectors": 5}

        def search(self, *args, **kwargs):
            raise RuntimeError("vector store offline")

    monkeypatch.setattr(documents, "get_knowledge_service", lambda: BrokenService())
    response = client.post("/v1/chat", headers=_token(), json={"message": "hi"})
    assert response.status_code == 200, response.text
    knowledge = response.json()["knowledge"]
    assert knowledge["used"] is False
    assert knowledge["reason"] == "error"
    assert "本次未使用知识库" in knowledge["message"]
    assert response.json()["reply"] == "ok"
    assert fake_llm[0].seen_prompts == ["BASE-SYSTEM-PROMPT"]


def test_context_and_hits_are_bounded(client, fake_llm):
    # 足够长，确保 chunker 产出明显多于 top_k 的片段。
    _ingest(client, "很长的知识片段。" * 2000, doc_id="long-doc")
    response = client.post("/v1/chat", headers=_token(), json={"message": "长文档"})
    assert response.status_code == 200, response.text
    knowledge = response.json()["knowledge"]
    assert knowledge["used"] is True
    assert knowledge["hits"] <= 4
    assert knowledge["context_tokens"] <= 600
    prompt = fake_llm[0].seen_prompts[0]
    # 只注入了裁剪后的片段，不可能把整库（>40000 字）塞进去。
    assert len(prompt) < 600 * 4 + len("BASE-SYSTEM-PROMPT") + 200


def test_stream_done_frame_carries_knowledge_metadata(client, fake_llm):
    _ingest(client, "流式答复也要说明知识库是否使用。", doc_id="stream-doc")
    with client.stream(
        "POST", "/v1/chat/stream", headers=_token(), json={"message": "stream"}
    ) as response:
        assert response.status_code == 200
        events = [
            json.loads(line[6:])
            for line in response.iter_lines()
            if line.startswith("data: ")
        ]
    done = events[-1]
    assert done["type"] == "done"
    assert done["knowledge"]["used"] is True
    assert done["knowledge"]["hits"] >= 1


# ==================== 无弃答能力时的注入保险 ====================


def _unrelated_hits(scores):
    """模拟“库里没有答案，但仍凑满 top_k 条不相干片段”的检索侧。"""
    return [
        {
            "doc_id": f"noise-{index}#0",
            "score": score,
            "text": f"与问题无关的第 {index} 条片段",
            "metadata": {"document_id": f"noise-{index}", "chunk_index": 0},
        }
        for index, score in enumerate(scores)
    ]


class _ScriptedService:
    """固定返回给定检索结果，用来验证网关侧的两道闸。"""

    def __init__(self, result, *, vectors=10):
        self._result = result
        self._vectors = vectors

    def status(self):
        return {"enabled": True, "vectors": self._vectors}

    def search(self, *args, **kwargs):
        return self._result


def _install(monkeypatch, result):
    import src.knowledge.documents as documents

    monkeypatch.setattr(
        documents, "get_knowledge_service", lambda: _ScriptedService(result)
    )


@pytest.mark.parametrize(
    "signal",
    [
        {"no_relevant": True},
        {"has_relevant": False},
        {"retrieval": {"status": "no_relevant"}},
        {"reason": "no_relevant_content"},
    ],
)
def test_explicit_no_relevant_signal_skips_injection(
    client, fake_llm, monkeypatch, signal
):
    """检索侧显式说“无相关内容”时，即使凑了 10 条高分片段也不注入。"""
    result = {
        "query": "部署口令应该放在哪里？",
        "count": 10,
        "retrieval": {"mode": "vector"},
        "hits": _unrelated_hits([0.9] * 10),
        **signal,
    }
    _install(monkeypatch, result)

    response = client.post(
        "/v1/chat", headers=_token(), json={"message": "部署口令应该放在哪里？"}
    )
    assert response.status_code == 200, response.text
    knowledge = response.json()["knowledge"]
    assert knowledge["used"] is False
    # 元信息必须说“未找到相关内容”，而不是“知识库为空”——两者含义不同。
    assert knowledge["reason"] == "no_relevant"
    assert knowledge["decision"] == "no_relevant"
    assert "未找到相关内容" in knowledge["message"]
    assert "知识库为空" not in knowledge["message"]
    assert knowledge["sources"] == []
    assert knowledge["context_tokens"] == 0
    assert knowledge["diagnostics"]["candidates"] == 10
    # 对话照常进行，且垃圾片段一个字都没进提示词。
    assert response.json()["reply"] == "ok"
    assert fake_llm[0].seen_prompts == ["BASE-SYSTEM-PROMPT"]


def test_low_top_score_is_not_injected(client, fake_llm, monkeypatch):
    """检索侧没给否定信号，但最高分低于门槛：网关自己也要拦住。"""
    monkeypatch.setenv("LIUHAO_CHAT_KNOWLEDGE_MIN_SCORE", "0.5")
    monkeypatch.setenv("LIUHAO_CHAT_KNOWLEDGE_MIN_MARGIN", "0.02")
    _install(
        monkeypatch,
        {"hits": _unrelated_hits([0.1] * 10), "count": 10},
    )

    response = client.post("/v1/chat", headers=_token(), json={"message": "随便问问"})
    assert response.status_code == 200, response.text
    knowledge = response.json()["knowledge"]
    assert knowledge["used"] is False
    assert knowledge["reason"] == "low_score"
    assert knowledge["decision"] == "low_score"
    assert "未找到足够相关的内容" in knowledge["message"]
    assert "知识库为空" not in knowledge["message"]
    diagnostics = knowledge["diagnostics"]
    assert diagnostics["candidates"] == 10
    assert diagnostics["top_score"] == pytest.approx(0.1)
    assert diagnostics["min_score"] == pytest.approx(0.5)
    assert response.json()["reply"] == "ok"
    assert fake_llm[0].seen_prompts == ["BASE-SYSTEM-PROMPT"]


def test_flat_candidates_are_ambiguous_and_not_injected(
    client, fake_llm, monkeypatch
):
    """分数“一锅平铺”（最高与次高差距过小）说明没有真正的相关项。"""
    monkeypatch.setenv("LIUHAO_CHAT_KNOWLEDGE_MIN_SCORE", "0.5")
    monkeypatch.setenv("LIUHAO_CHAT_KNOWLEDGE_MIN_MARGIN", "0.02")
    _install(
        monkeypatch,
        {"hits": _unrelated_hits([0.9, 0.895, 0.5, 0.5, 0.5]), "count": 5},
    )

    response = client.post("/v1/chat", headers=_token(), json={"message": "随便问问"})
    assert response.status_code == 200, response.text
    knowledge = response.json()["knowledge"]
    assert knowledge["used"] is False
    assert knowledge["reason"] == "ambiguous"
    assert knowledge["decision"] == "ambiguous"
    assert "分差" in knowledge["message"]
    diagnostics = knowledge["diagnostics"]
    assert diagnostics["top_score"] == pytest.approx(0.9)
    assert diagnostics["runner_up_score"] == pytest.approx(0.895)
    assert diagnostics["min_margin"] == pytest.approx(0.02)
    assert response.json()["reply"] == "ok"
    assert fake_llm[0].seen_prompts == ["BASE-SYSTEM-PROMPT"]


def test_gate_pass_injects_and_reports_diagnostics(client, fake_llm, monkeypatch):
    """通过闸门时仍正常注入并带引用，且元信息给出决定理由。"""
    monkeypatch.setenv("LIUHAO_CHAT_KNOWLEDGE_MIN_SCORE", "0.5")
    monkeypatch.setenv("LIUHAO_CHAT_KNOWLEDGE_MIN_MARGIN", "0.02")
    _install(
        monkeypatch,
        {"hits": _unrelated_hits([0.91, 0.62]), "count": 2},
    )

    response = client.post("/v1/chat", headers=_token(), json={"message": "部署口令"})
    assert response.status_code == 200, response.text
    knowledge = response.json()["knowledge"]
    assert knowledge["used"] is True
    assert knowledge["decision"] == "inject"
    assert knowledge["reason"] == ""
    assert knowledge["hits"] == 2
    assert [source["document_id"] for source in knowledge["sources"]] == [
        "noise-0",
        "noise-1",
    ]
    assert knowledge["diagnostics"]["top_score"] == pytest.approx(0.91)
    assert knowledge["diagnostics"]["runner_up_score"] == pytest.approx(0.62)
    assert "[知识库上下文]" in fake_llm[0].seen_prompts[0]
