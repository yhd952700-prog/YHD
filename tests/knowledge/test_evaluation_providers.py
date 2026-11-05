"""指定 provider 重测、阈值扫描与“无真实源”如实标注的回归测试。"""

from __future__ import annotations

import pytest

from src.knowledge import evaluation
from src.knowledge.abstention import AbstentionConfig
from src.knowledge.documents import KnowledgeDocumentService
from src.knowledge.embedding import EmbeddingUnavailableError
from src.knowledge.evaluation import HashingEmbedder
from src.knowledge.vector_store import PersistentVectorStore


class _TinyEmbedder:
    """A non-hashing stand-in: proves the runner accepts injected providers."""

    name = "fake"
    model = "unit-3d"

    def embed(self, texts):
        vectors = []
        for text in texts:
            vectors.append(
                [
                    float(len(text)) + 1.0,
                    float(sum(map(ord, text)) % 97) + 1.0,
                    1.0,
                ]
            )
        return vectors


def _raw_row(
    category: str,
    relevant,
    raw_ranked,
    top: float,
    runner: float,
    lexical: int = 0,
) -> dict:
    return {
        "query": f"q-{top}",
        "category": category,
        "relevant": list(relevant),
        "mode": "hybrid",
        "raw_ranked": list(raw_ranked),
        "answer_marker": None,
        "marker_in_top5": None,
        "top_score": top,
        "runner_up_score": runner,
        "lexical_candidates": lexical,
    }


class TestBuildProvider:
    def test_hashing_is_the_builtin_baseline(self):
        spec = evaluation.build_provider("hashing")
        assert spec.key == "hashing"
        assert isinstance(spec.embedder, HashingEmbedder)

    def test_openai_without_key_is_unavailable_not_faked(self, monkeypatch):
        monkeypatch.delenv("LIUHAO_EMBEDDING_API_KEY", raising=False)
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        with pytest.raises(EmbeddingUnavailableError):
            evaluation.build_provider("openai")

    def test_unknown_provider_is_rejected(self):
        with pytest.raises(EmbeddingUnavailableError):
            evaluation.build_provider("magic")


class TestSweep:
    def test_recommendation_keeps_abstention_and_avoids_false_abstention(self):
        rows = [
            _raw_row("fact", ["kb-001"], ["kb-001"], 0.80, 0.70, 1),
            _raw_row("fact", ["kb-002"], ["kb-002"], 0.75, 0.60, 1),
            _raw_row("unanswerable", [], [], 0.60, 0.58, 0),
        ]
        sweep = evaluation.sweep_thresholds(
            rows,
            min_top_values=(0.50, 0.70, 0.80),
            min_gap_values=(0.05, 0.20),
            strong_match_values=(0.85,),
        )
        recommended = sweep["recommended"]
        assert recommended["thresholds"] == {
            "min_top_similarity": 0.70,
            # Metric-identical gap values keep the documented default of 0.05;
            # a needlessly large gap would reject near-duplicate neighbours.
            "min_top_gap": 0.05,
            "strong_match_similarity": 0.85,
        }
        assert recommended["holds_abstention"] is True
        assert recommended["metrics"]["false_abstention_rate"] == 0.0
        # 0.80 would reject the 0.75 answerable query: abstention must not be
        # bought with false abstentions.
        assert recommended["metrics"]["answerable_recall@5"] == 1.0

    def test_observed_scores_are_reported_next_to_thresholds(self):
        rows = [
            _raw_row("fact", ["kb-001"], ["kb-001"], 0.80, 0.70, 1),
            _raw_row("unanswerable", [], [], 0.60, 0.58, 0),
        ]
        sweep = evaluation.sweep_thresholds(
            rows,
            min_top_values=(0.50,),
            min_gap_values=(0.05,),
            strong_match_values=(0.85,),
        )
        observed = sweep["recommended"]["observed_scores"]
        assert observed["answerable_top_min"] == 0.80
        assert observed["unanswerable_top_max"] == 0.60
        assert observed["unanswerable_lexical_max"] == 0


class TestRunnerInjection:
    def test_injected_embedder_is_recorded_and_baseline_is_not_applicable(self):
        run = evaluation.run_evaluation(embedder=_TinyEmbedder())
        assert run["embedder"]["name"] == "fake"
        assert run["metrics"]["queries"] == 30
        report = evaluation.baseline_report(run)
        assert report["baseline_applicable"] is False
        assert report["regression"] is None


class TestSearchConfigInjection:
    def test_search_honours_a_pinned_strict_config(self, tmp_path):
        store = PersistentVectorStore(str(tmp_path / "kb.sqlite3"))
        service = KnowledgeDocumentService(
            vector_store=store, embedder=HashingEmbedder()
        )
        try:
            service.ingest("# 部署\n\n局域网部署默认端口 8700。", doc_id="doc-1")
            strict = AbstentionConfig(
                min_top_similarity=0.99,
                min_top_gap=0.99,
                strong_match_similarity=0.999,
            )
            result = service.search(
                "局域网部署端口", top_k=4, abstention_config=strict
            )
            assert result["retrieval"]["status"] == "no_relevant"
            assert result["hits"] == []
            assert result["retrieval"]["thresholds"]["min_top_score"] == 0.99
        finally:
            store.close()


class TestHonestUnavailable:
    def test_auto_without_any_source_says_so(self, monkeypatch):
        monkeypatch.delenv("LIUHAO_EMBEDDING_API_KEY", raising=False)
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        monkeypatch.setattr(evaluation, "probe_ollama", lambda *a, **k: None)
        report = evaluation.compare_providers(["auto"])
        assert report["real_provider_available"] is False
        assert report["real_model_verified"] is False
        assert "本机无可用 embedding 源，真实模型下阈值未验证" in (
            report["real_model_note"]
        )
        assert report["providers"][0]["status"] == "unavailable"


class TestComparisonTable:
    def test_hashing_row_is_reported_with_its_three_groups(self, monkeypatch):
        monkeypatch.delenv("LIUHAO_EMBEDDING_API_KEY", raising=False)
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        monkeypatch.setattr(evaluation, "probe_ollama", lambda *a, **k: None)
        report = evaluation.compare_providers(["hashing"])
        row = report["providers"][0]
        assert row["status"] == "ok"
        assert row["baseline_applicable"] is True
        for key in (
            "answerable_recall@5",
            "no_answer_abstained",
            "false_abstention_rate",
        ):
            assert key in row["metrics"]
        assert "recall@5" in report["comparison_table"]
