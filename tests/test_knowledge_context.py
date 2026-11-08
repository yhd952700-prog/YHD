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
        return {"hits": [], "retrieval": self._retrieval}


def _ambiguous():
    return {
        "status": "no_relevant",
        "reason": "ambiguous",
        "top_score": 0.6632,
        "runner_up_score": 0.6547,
        "thresholds": {"min_top_score": 0.65, "min_score_gap": 0.05},
        "hits": [],
    }


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
