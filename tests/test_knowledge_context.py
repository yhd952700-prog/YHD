"""弃答时也要约束模型：没资料就明说，不许用自身记忆编答案。"""

from __future__ import annotations

import src.knowledge.documents as documents
from src.gateway import knowledge_context


class _FakeService:
    def __init__(self, retrieval):
        self._retrieval = retrieval

    def status(self):
        return {"enabled": True, "provider": "ollama", "model": "nomic-embed-text"}

    def search(self, query, **kwargs):
        # 顶层 hits 与 retrieval.hits 是同一份列表（契约如此）。
        return {
            "hits": list(self._retrieval.get("hits") or []),
            "retrieval": self._retrieval,
        }


def _ambiguous():
    return {
        "status": "no_relevant",
        "reason": "ambiguous",
        "top_score": 0.6632,
        "runner_up_score": 0.6547,
        "thresholds": {"min_top_score": 0.65, "min_score_gap": 0.05},
        "hits": [],
    }


def _ok_with_skipped_hit():
    hits = [
        {
            "doc_id": "kb--doc-a#0",
            "score": 0.74,
            "text": "甲块包含答案第一句。",
            "metadata": {"document_id": "kb--doc-a", "chunk_index": 0},
        },
        {
            # 空文本命中：组装时必须跳过，且不得让编号移位。
            "doc_id": "kb--doc-b#1",
            "score": 0.71,
            "text": "",
            "metadata": {"document_id": "kb--doc-b", "chunk_index": 1},
        },
        {
            "doc_id": "kb--doc-c#2",
            "score": 0.68,
            "text": "丙块包含答案第二句。",
            "metadata": {"document_id": "kb--doc-c", "chunk_index": 2},
        },
    ]
    return {
        "hits": hits,
        "retrieval": {
            "status": "ok",
            "reason": None,
            "top_score": 0.74,
            "runner_up_score": 0.71,
            "thresholds": {"min_top_score": 0.65, "min_score_gap": 0.05},
        },
    }


def test_sources_stay_aligned_with_citation_labels_when_a_hit_is_skipped(monkeypatch):
    monkeypatch.setattr(
        documents, "get_knowledge_service", lambda: _FakeService(_ok_with_skipped_hit())
    )
    result = knowledge_context.build_knowledge_context(
        "答案分布在两块里？", enabled=True
    )

    assert result["used"] is True
    sources = result["sources"]
    assert [source["document_id"] for source in sources] == [
        "kb--doc-a",
        "kb--doc-c",
    ]
    # 标签 [N] 与 sources[N-1] 一一对应：空片段被跳过后，编号必须连续，
    # 否则客户端把回复里的 [2] 映射回 sources[1] 时会展示错误的命中块。
    assert "[1] 文档=kb--doc-a" in result["context"]
    assert "[2] 文档=kb--doc-c" in result["context"]
    assert "[3]" not in result["context"]


def test_abstained_retrieval_still_tells_the_model_to_say_not_found(monkeypatch):
    monkeypatch.setattr(
        documents, "get_knowledge_service", lambda: _FakeService(_ambiguous())
    )
    result = knowledge_context.build_knowledge_context(
        "知识库的备份保留多少天？", enabled=True
    )

    assert result["used"] is False
    assert result["decision"] == "ambiguous"
    # used=False 但 context 是“没有资料”的指令，而不是片段。
    assert "知识库中没有找到相关内容" in result["context"]
    assert "不要猜测" in result["context"]


def test_abstain_instruction_is_applied_to_the_system_prompt(monkeypatch):
    monkeypatch.setattr(
        documents, "get_knowledge_service", lambda: _FakeService(_ambiguous())
    )
    result = knowledge_context.build_knowledge_context("随便问问", enabled=True)

    class _Assistant:
        system_prompt = "BASE"

    assistant = _Assistant()
    with knowledge_context.knowledge_prompt(assistant, result):
        assert assistant.system_prompt.startswith("BASE")
        assert "知识库中没有找到相关内容" in assistant.system_prompt
    assert assistant.system_prompt == "BASE"


def test_user_disabled_retrieval_gets_no_instruction(monkeypatch):
    monkeypatch.setattr(
        documents, "get_knowledge_service", lambda: _FakeService(_ambiguous())
    )
    result = knowledge_context.build_knowledge_context("随便问问", enabled=False)

    assert result["used"] is False
    assert result["decision"] == "disabled_by_user"
    assert result["context"] == ""
