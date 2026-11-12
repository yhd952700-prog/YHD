"""对话链路的知识库注入：检索、引用、降级、开关、边界。

全部走真实 HTTP 面 + 真实 ``KnowledgeDocumentService``（mock embedding 仅限
显式开发/测试模式），并替换 LLM 为捕获提示词的替身，以证明上下文确实进了
system prompt，而不是只出现在响应元信息里。
"""
from __future__ import annotations

import json
from contextlib import contextmanager

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
    # 重复提交去重也是进程级缓存：同一主体 + 同一句话在窗口内会直接重放上一
    # 个测试的响应，测的就不是本测试安装的知识库结果了。
    chat_module._dedup_entries.clear()
    from src.gateway.main import get_app

    with TestClient(get_app()) as test_client:
        yield test_client
    chat_module._sessions.clear()
    chat_module._session_users.clear()
    chat_module._dedup_entries.clear()
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

    @contextmanager
    def serialized_turn(self):
        """替身没有并发写风险，占位即可（真实实现是会话级互斥锁）。"""
        yield


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


#: 弃答契约：``used=False`` 时也注入一段“没有资料”的指令，让模型直接说没找到，
#: 而不是用自身记忆补答案。片段正文与来源仍然一个字都不注入。
_ABSTAIN_INSTRUCTION_MARKERS = (
    "[知识库检索结果]",
    "本次没有检索到与该问题相关的资料",
    "[知识库检索结果结束]",
)


def _assert_abstain_instruction_only(prompt: str, *fragments: str) -> None:
    """提示词里只有“没有资料”的指令，没有任何被检索到的片段。"""
    for marker in _ABSTAIN_INSTRUCTION_MARKERS:
        assert marker in prompt
    for fragment in fragments:
        assert fragment not in prompt


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
    # 空库由知识库侧判为 no_relevant / empty_corpus，原因原样透传。
    assert knowledge["reason"] == "empty_corpus"
    assert knowledge["decision"] == "empty_corpus"
    assert knowledge["diagnostics"]["gate"] == "knowledge"
    assert knowledge["diagnostics"]["retrieval_status"] == "no_relevant"
    assert "本次未使用知识库" in knowledge["message"]
    # “库里没有文档”与“库里没有答案”必须说清楚，不能混用同一句文案。
    assert "知识库为空" in knowledge["message"]
    assert "未找到相关内容" not in knowledge["message"]
    # 普通对话没有被破坏；空库仍要给模型“没有资料”的指令，但不注入任何片段。
    assert body["reply"] == "ok"
    _assert_abstain_instruction_only(fake_llm[0].seen_prompts[0])


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
        {"reason": "no_relevant_content"},
    ],
)
def test_legacy_no_relevant_shapes_still_skip_injection(
    client, fake_llm, monkeypatch, signal
):
    """没有权威 ``retrieval.status`` 的旧实现仍按并集匹配兼容。"""
    result = {
        "query": "部署口令应该放在哪里？",
        "count": 10,
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
    assert knowledge["reason"] == "no_relevant"
    assert knowledge["decision"] == "no_relevant"
    assert "未找到相关内容" in knowledge["message"]
    assert "知识库为空" not in knowledge["message"]
    assert knowledge["sources"] == []
    # 弃答也有指令（“没有资料”），但没有任何一条检索片段被注入。
    assert knowledge["context_tokens"] > 0
    assert knowledge["diagnostics"]["candidates"] == 10
    assert knowledge["diagnostics"]["gate"] == "gateway"
    # 对话照常进行，且垃圾片段一个字都没进提示词。
    assert response.json()["reply"] == "ok"
    _assert_abstain_instruction_only(
        fake_llm[0].seen_prompts[0], "与问题无关的第"
    )


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
    _assert_abstain_instruction_only(
        fake_llm[0].seen_prompts[0], "与问题无关的第"
    )


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
    _assert_abstain_instruction_only(
        fake_llm[0].seen_prompts[0], "与问题无关的第"
    )


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


# ==================== 知识库定稿契约的集成测试 ====================


def _retrieval(status, reason=None, hits=None, top_score=None, runner_up=None):
    """``src.knowledge.abstention`` 的定稿结构（逐字段照抄）。"""
    return {
        "mode": "hybrid",
        "fts_available": True,
        "status": status,
        "reason": reason,
        "top_score": top_score,
        "runner_up_score": runner_up,
        "thresholds": {"min_top_score": 0.08, "min_score_gap": 0.05},
        "abstained": status == "no_relevant",
        "hits": list(hits or []),
    }


def test_contract_ok_injects_even_below_gateway_threshold(
    client, fake_llm, monkeypatch
):
    """知识库判 ok 时以它为准：RRF 分数低于网关 0.25 也照常注入。"""
    hits = _unrelated_hits([0.032, 0.016])
    result = {
        "query": "部署口令",
        "count": 10,
        "hits": hits,
        "retrieval": _retrieval(
            "ok", None, hits, top_score=0.18, runner_up=0.09
        ),
    }
    _install(monkeypatch, result)
    # 网关旧门槛保持默认 0.25（fixture 里是 0，这里显式恢复成生产值）。
    monkeypatch.setenv("LIUHAO_CHAT_KNOWLEDGE_MIN_SCORE", "0.25")
    monkeypatch.setenv("LIUHAO_CHAT_KNOWLEDGE_MIN_MARGIN", "0.02")

    response = client.post("/v1/chat", headers=_token(), json={"message": "部署口令"})
    assert response.status_code == 200, response.text
    knowledge = response.json()["knowledge"]
    assert knowledge["used"] is True
    assert knowledge["decision"] == "inject"
    assert knowledge["hits"] == 2
    assert [source["document_id"] for source in knowledge["sources"]] == [
        "noise-0",
        "noise-1",
    ]
    diagnostics = knowledge["diagnostics"]
    assert diagnostics["gate"] == "knowledge"
    assert diagnostics["retrieval_status"] == "ok"
    assert diagnostics["top_score"] == pytest.approx(0.18)
    assert diagnostics["runner_up_score"] == pytest.approx(0.09)
    assert diagnostics["thresholds"] == {
        "min_top_score": 0.08,
        "min_score_gap": 0.05,
    }
    assert "[知识库上下文]" in fake_llm[0].seen_prompts[0]


@pytest.mark.parametrize(
    "reason,message,forbidden",
    [
        (
            "below_threshold",
            "本次未使用知识库：未找到相关内容",
            "知识库为空",
        ),
        (
            "ambiguous",
            "本次未使用知识库：找到的内容不够确定",
            "知识库为空",
        ),
        (
            "empty_corpus",
            "本次未使用知识库：知识库为空（暂无可检索文档）",
            "未找到相关内容",
        ),
    ],
)
def test_contract_abstention_reasons_pass_through(
    client, fake_llm, monkeypatch, reason, message, forbidden
):
    """三种弃答原因必须原样透传，且各自对应不同的用户文案。"""
    result = {
        "query": "员工年假怎么申请？",
        "count": 10,
        "hits": [],
        "retrieval": _retrieval("no_relevant", reason, [], 0.04, 0.03),
    }
    _install(monkeypatch, result)

    response = client.post(
        "/v1/chat", headers=_token(), json={"message": "员工年假怎么申请？"}
    )
    assert response.status_code == 200, response.text
    knowledge = response.json()["knowledge"]
    assert knowledge["used"] is False
    assert knowledge["reason"] == reason
    assert knowledge["decision"] == reason
    assert knowledge["message"] == message
    assert forbidden not in knowledge["message"]
    assert knowledge["sources"] == []
    assert knowledge["hits"] == 0
    # 弃答指令会进提示词，但没有任何片段被当成引用注入。
    assert knowledge["context_tokens"] > 0
    diagnostics = knowledge["diagnostics"]
    assert diagnostics["gate"] == "knowledge"
    assert diagnostics["retrieval_status"] == "no_relevant"
    assert diagnostics["retrieval_reason"] == reason
    assert diagnostics["candidates"] == 0
    assert diagnostics["top_score"] == pytest.approx(0.04)
    assert diagnostics["thresholds"]["min_top_score"] == pytest.approx(0.08)
    # 对话照常，且没有任何片段进入提示词。
    assert response.json()["reply"] == "ok"
    _assert_abstain_instruction_only(fake_llm[0].seen_prompts[0])


def test_authoritative_status_beats_legacy_flags(client, fake_llm, monkeypatch):
    """``status="ok"`` 是权威字段，旧的否定标记不能反过来盖住它。"""
    hits = _unrelated_hits([0.4])
    result = {
        "query": "q",
        "count": 5,
        "hits": hits,
        # 旧实现的标记，与权威 status 冲突；必须以 status 为准。
        "has_relevant": False,
        "retrieval": _retrieval("ok", None, hits, 0.3, None),
    }
    _install(monkeypatch, result)

    response = client.post("/v1/chat", headers=_token(), json={"message": "q"})
    assert response.status_code == 200, response.text
    knowledge = response.json()["knowledge"]
    assert knowledge["used"] is True
    assert knowledge["decision"] == "inject"
    assert knowledge["sources"][0]["document_id"] == "noise-0"


@pytest.mark.parametrize("bad_score", [None, "not-a-number", -0.1])
def test_contract_ok_with_unusable_scores_falls_back(
    client, fake_llm, monkeypatch, bad_score
):
    """分数缺失/异常是网关唯一的否决权；此时按弃答处理并说明原因。"""
    hits = [
        {
            "doc_id": "noise-0#0",
            "score": bad_score,
            "text": "正文",
            "metadata": {"document_id": "noise-0", "chunk_index": 0},
        }
    ]
    result = {
        "query": "q",
        "count": 1,
        "hits": hits,
        "retrieval": _retrieval("ok", None, hits, 0.2, None),
    }
    _install(monkeypatch, result)

    response = client.post("/v1/chat", headers=_token(), json={"message": "q"})
    assert response.status_code == 200, response.text
    knowledge = response.json()["knowledge"]
    assert knowledge["used"] is False
    assert knowledge["reason"] == "score_anomaly"
    assert "分数缺失或异常" in knowledge["message"]
    assert knowledge["sources"] == []
    assert response.json()["reply"] == "ok"
    assert fake_llm[0].seen_prompts == ["BASE-SYSTEM-PROMPT"]
