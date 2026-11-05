"""弃答闸回归：定稿字段、边界 `>=` 语义、三种状态与评测集表现。"""

from __future__ import annotations

from typing import List

import pytest

from src.knowledge.abstention import (
    NO_RELEVANT,
    REASON_AMBIGUOUS,
    REASON_BELOW_THRESHOLD,
    REASON_EMPTY_CORPUS,
    STATUS_OK,
    AbstentionConfig,
    assess_confidence,
)
from src.knowledge.documents import KnowledgeDocumentService
from src.knowledge import evaluation
from src.knowledge.vector_store import PersistentVectorStore


class _KeywordEmbedder:
    """含“局域网”的文本向量为 [1,0]，其它为 [0,1]；用意是制造相关/不相关两群。"""

    name = "keyword-test"
    model = "keyword-test"

    def embed(self, texts: List[str]) -> List[List[float]]:
        return [
            [1.0, 0.0] if "局域网" in text else [0.0, 1.0]
            for text in texts
        ]


def _config(**overrides) -> AbstentionConfig:
    base = {
        "min_top_similarity": 0.5,
        "min_top_gap": 0.25,
        "strong_match_similarity": 2.0,  # 默认关闭强匹配豁免
    }
    base.update(overrides)
    return AbstentionConfig(**base)


class TestBoundaryIsGreaterOrEqual:
    """定稿语义：恰好等于阈值算通过（`>=`），略低才算失败。"""

    def test_top_score_exactly_on_the_floor_passes(self):
        config = _config(min_top_gap=0.0)
        at_floor = assess_confidence(
            {"a": 0.5, "b": 0.5}, 0, config=config
        )
        below_floor = assess_confidence(
            {"a": 0.4999, "b": 0.4999}, 0, config=config
        )
        assert at_floor["checks"]["absolute"] is True
        assert at_floor["confident"] is True
        assert below_floor["checks"]["absolute"] is False
        assert below_floor["confident"] is False
        assert below_floor["reason"] == REASON_BELOW_THRESHOLD

    def test_gap_exactly_on_the_threshold_passes(self):
        config = _config()
        at_gap = assess_confidence({"a": 0.75, "b": 0.5}, 0, config=config)
        below_gap = assess_confidence({"a": 0.7499, "b": 0.5}, 0, config=config)
        assert at_gap["checks"]["relative"] is True
        assert at_gap["confident"] is True
        assert below_gap["checks"]["relative"] is False
        assert below_gap["confident"] is False
        assert below_gap["reason"] == REASON_AMBIGUOUS

    def test_strong_match_at_threshold_bypasses_the_gap(self):
        config = _config(min_top_gap=0.9, strong_match_similarity=0.85)
        at_match = assess_confidence({"a": 0.85, "b": 0.85}, 0, config=config)
        below_match = assess_confidence(
            {"a": 0.8499, "b": 0.8499}, 0, config=config
        )
        assert at_match["checks"]["strong_match"] is True
        assert at_match["confident"] is True
        assert below_match["checks"]["strong_match"] is False
        assert below_match["confident"] is False
        assert below_match["reason"] == REASON_AMBIGUOUS

    def test_zero_vector_mock_falls_back_to_the_keyword_path(self):
        # 开发 mock provider 全是零向量：分数无信息量，只能看词法佐证。
        with_lexical = assess_confidence({"a": 0.0, "b": 0.0}, 1, config=_config())
        without_lexical = assess_confidence({"a": 0.0}, 0, config=_config())
        assert with_lexical["confident"] is True
        assert without_lexical["confident"] is False
        assert without_lexical["reason"] == REASON_BELOW_THRESHOLD

    def test_explicit_zero_threshold_accepts_degenerate_vectors(self):
        # 调用方 threshold<=0 已明确接受 0 分（网关测试替身），不再二次否决。
        verdict = assess_confidence(
            {"a": 0.0}, 0, config=_config(), zero_score_allowed=True
        )
        assert verdict["confident"] is True
        assert verdict["reason"] is None


class TestVerdictFields:
    def test_thresholds_and_distribution_are_reported(self):
        verdict = assess_confidence(
            {"a": 0.9, "b": 0.8, "c": 0.7}, 0, config=_config()
        )
        assert verdict["thresholds"] == {
            "min_top_score": 0.5,
            "min_score_gap": 0.25,
        }
        assert [item["chunk_id"] for item in verdict["distribution"]] == [
            "a",
            "b",
            "c",
        ]
        assert verdict["top_score"] == 0.9
        assert verdict["runner_up_score"] == 0.8

    def test_no_candidates_reports_nulls_and_the_reason(self):
        no_relevant = assess_confidence({}, 0, config=_config(), empty_corpus=True)
        assert no_relevant["status"] == NO_RELEVANT
        assert no_relevant["reason"] == REASON_EMPTY_CORPUS
        assert no_relevant["top_score"] is None
        assert no_relevant["runner_up_score"] is None

    def test_non_empty_library_without_a_match_is_not_empty_corpus(self):
        no_relevant = assess_confidence(
            {}, 0, config=_config(), empty_corpus=False
        )
        assert no_relevant["status"] == NO_RELEVANT
        assert no_relevant["reason"] == REASON_BELOW_THRESHOLD


class TestHybridSearchContract:
    def _store(self, tmp_path) -> PersistentVectorStore:
        store = PersistentVectorStore(str(tmp_path / "vectors.sqlite3"))
        store.insert(
            "lan#0",
            [1.0, 0.0],
            {"text": "局域网访问设置。", "document_id": "lan"},
        )
        store.insert(
            "doc#0",
            [1.0, 0.0],
            {"text": "完全无关的说明。", "document_id": "doc"},
        )
        return store

    def test_normal_hit_exposes_all_contract_fields(self, tmp_path):
        store = self._store(tmp_path)
        results, retrieval = store.hybrid_search("局域网", [1.0, 0.0], top_k=2)
        assert retrieval["status"] == STATUS_OK
        assert retrieval["reason"] is None
        assert retrieval["top_score"] == 1.0
        assert retrieval["runner_up_score"] is not None
        assert retrieval["thresholds"] == {
            "min_top_score": 0.08,
            "min_score_gap": 0.05,
        }
        assert results
        store.close()

    def test_no_relevant_content_returns_empty_hits(self, tmp_path):
        store = self._store(tmp_path)
        results, retrieval = store.hybrid_search(
            "员工年假怎么申请", [0.1, 1.0], top_k=10
        )
        assert results == []
        assert retrieval["status"] == NO_RELEVANT
        assert retrieval["reason"] == REASON_AMBIGUOUS
        assert retrieval["top_score"] > 0
        assert retrieval["runner_up_score"] is not None
        store.close()

    def test_empty_corpus_is_a_distinct_reason(self, tmp_path):
        store = PersistentVectorStore(str(tmp_path / "empty.sqlite3"))
        results, retrieval = store.hybrid_search("局域网", [1.0, 0.0])
        assert results == []
        assert retrieval["status"] == NO_RELEVANT
        assert retrieval["reason"] == REASON_EMPTY_CORPUS
        assert retrieval["top_score"] is None
        assert retrieval["runner_up_score"] is None
        store.close()

    def test_ambiguous_candidates_do_not_get_padded_to_top_k(self, tmp_path):
        store = PersistentVectorStore(str(tmp_path / "ambiguous.sqlite3"))
        for index in range(10):
            store.insert(
                f"noise#{index}",
                [1.0, 0.0],
                {"text": "同主题但无区分度的说明。", "document_id": "noise"},
            )
        config = AbstentionConfig(
            min_top_similarity=0.5,
            min_top_gap=0.25,
            strong_match_similarity=2.0,
        )
        results, retrieval = store.hybrid_search(
            "年假", [1.0, 0.0], top_k=10, config=config
        )
        assert results == [], "无相关内容时不得硬塞 10 条"
        assert retrieval["status"] == NO_RELEVANT
        assert retrieval["reason"] == REASON_AMBIGUOUS
        assert retrieval["confidence"]["candidate_count"] == 10
        store.close()


class TestServiceMirrorsHits:
    def test_retrieval_hits_mirrors_the_top_level_list(self, tmp_path):
        store = PersistentVectorStore(str(tmp_path / "service.sqlite3"))
        service = KnowledgeDocumentService(
            vector_store=store, embedder=_KeywordEmbedder()
        )
        service.ingest("局域网访问设置。", doc_id="lan")

        hit = service.search("局域网", top_k=3)
        assert hit["retrieval"]["status"] == STATUS_OK
        assert hit["retrieval"]["hits"] == hit["hits"]
        assert hit["hits"]

        miss = service.search("员工年假怎么申请", top_k=10)
        assert miss["retrieval"]["status"] == NO_RELEVANT
        assert miss["retrieval"]["hits"] == []
        assert miss["hits"] == []
        store.close()


@pytest.fixture(scope="module")
def evaluation_result():
    return evaluation.run_evaluation()


class TestEvaluationSet:
    def test_unanswerable_query_is_abstained(self, evaluation_result):
        rows = [
            row
            for row in evaluation_result["rows"]
            if row["category"] == "unanswerable"
        ]
        assert rows
        for row in rows:
            assert row["abstained"] is True
            assert row["status"] == NO_RELEVANT
            assert row["returned"] == 0
            assert row["ranked"] == []

    def test_answerable_queries_are_not_falsely_abstained(self, evaluation_result):
        rows = [row for row in evaluation_result["rows"] if row["relevant"]]
        assert rows
        assert all(row["status"] == STATUS_OK for row in rows)
        assert all(row["abstained"] is False for row in rows)
        assert evaluation_result["metrics"]["false_abstention_rate"] == 0.0

    def test_three_metric_groups_are_reported(self, evaluation_result):
        metrics = evaluation_result["metrics"]
        assert metrics["answerable_recall@5"] > 0.0
        assert metrics["no_answer_abstained"] == 1.0
        assert metrics["false_abstention_rate"] == 0.0
