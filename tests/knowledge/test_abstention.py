"""弃答闸回归：定稿字段、边界 `>=` 语义、三种状态与评测集表现。"""

from __future__ import annotations

from typing import List

import pytest

from src.knowledge.abstention import (
    CALIBRATED_PROFILES,
    ENV_CALIBRATED_FOR,
    ENV_MIN_GAP,
    ENV_MIN_SIMILARITY,
    ENV_STRONG_MATCH,
    NO_RELEVANT,
    REASON_AMBIGUOUS,
    REASON_BELOW_THRESHOLD,
    REASON_EMPTY_CORPUS,
    STATUS_OK,
    UNCALIBRATED_CONFIG,
    AbstentionConfig,
    assess_confidence,
    embedder_identity,
    resolve_abstention_config,
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


class TestLexicalRescue:
    """A keyword hit may rescue a low score, but never a near-tie."""

    def test_common_word_near_tie_is_ambiguous_not_confident(self):
        config = AbstentionConfig(
            min_top_similarity=0.65, min_top_gap=0.05, strong_match_similarity=0.85
        )
        # 实测：真实 40KB 文档里问“知识库的备份保留多少天”，top 0.6632 /
        # runner-up 0.6547，只因“知识库”这个词命中词法列表就判 ok，模型随后编造天数。
        verdict = assess_confidence(
            {"kb-032#0": 0.6632, "kb-033#0": 0.6547},
            3,
            config=config,
        )
        assert verdict["status"] == NO_RELEVANT
        assert verdict["reason"] == REASON_AMBIGUOUS

    def test_clear_keyword_win_still_rescues_a_low_absolute_score(self):
        config = AbstentionConfig(
            min_top_similarity=0.65, min_top_gap=0.05, strong_match_similarity=0.85
        )
        verdict = assess_confidence(
            {"ticket#0": 0.70, "other#0": 0.40},
            1,
            config=config,
        )
        assert verdict["status"] == STATUS_OK

    def test_margin_is_relative_to_the_provider_floor(self):
        # hashing 的 0.08 门槛下，0.0991 只高出 0.019 也算清晰命中（评测集
        # 最低的可答命中分），不能被借用 nomic 的绝对间距误杀。
        config = AbstentionConfig(
            min_top_similarity=0.08, min_top_gap=0.05, strong_match_similarity=0.85
        )
        verdict = assess_confidence(
            {"faq#0": 0.0991, "faq#1": 0.05},
            1,
            config=config,
        )
        assert verdict["status"] == STATUS_OK

    def test_relative_gap_compares_documents_not_sibling_chunks(self):
        # 实测（ollama nomic-embed-text, 8 docs / 81 chunks）：研发问题 top
        # 0.7116 与同文档兄弟块 0.6671 打出 0.0445 的块级间距，被误判为
        # “不可区分”；按文档取每篇最高分后，与第二篇文档 0.6570 的真实间距
        # 是 0.0546，第二判据应当放行。
        config = AbstentionConfig(
            min_top_similarity=0.08, min_top_gap=0.05, strong_match_similarity=0.85
        )
        verdict = assess_confidence(
            {
                "kb--pdf-c9dcfa5138cf3ba5#1": 0.0820,
                "kb--pdf-c9dcfa5138cf3ba5#3": 0.0500,
                "kb--md-a7144f9ec41d10b2#47": 0.0300,
            },
            3,
            config=config,
        )
        # 块级间距 0.0320 < 0.05，文档级间距 0.0520 >= 0.05。
        assert verdict["checks"]["doc_gap"] == 0.052
        assert verdict["checks"]["relative"] is True
        assert verdict["status"] == STATUS_OK

    def test_rare_token_flag_cannot_override_the_ambiguous_band(self):
        # 实测（同上语料）：罕见词标记（df <= 2）在 81 块的小库里 20 问 19 问
        # 置位（“补贴”“上限”“支持”“工程”都是 df=2），3 个无答案问题全靠它
        # 放行。窄带第二判据恢复权威：标记只进 checks，不再单独改判。
        config = AbstentionConfig(
            min_top_similarity=0.65, min_top_gap=0.05, strong_match_similarity=0.85
        )
        verdict = assess_confidence(
            {"kb-032#0": 0.6632, "kb-033#0": 0.6547},
            40,
            config=config,
            lexical_rare_match=True,
        )
        assert verdict["checks"]["lexical_rare"] is True
        assert verdict["checks"]["relative"] is False
        assert verdict["status"] == NO_RELEVANT
        assert verdict["reason"] == REASON_AMBIGUOUS


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

        hit = service.search("局域网", top_k=3, abstention_config=_config())
        assert hit["retrieval"]["status"] == STATUS_OK
        assert hit["retrieval"]["hits"] == hit["hits"]
        assert hit["hits"]

        miss = service.search(
            "员工年假怎么申请", top_k=10, abstention_config=_config()
        )
        assert miss["retrieval"]["status"] == NO_RELEVANT
        assert miss["retrieval"]["hits"] == []
        assert miss["hits"] == []
        store.close()


class TestCalibrationProvenance:
    """没有该 provider 的评测证据时，不得沿用别的模型的阈值。"""

    @pytest.fixture(autouse=True)
    def _clean_env(self, monkeypatch):
        for name in (
            ENV_CALIBRATED_FOR,
            ENV_MIN_SIMILARITY,
            ENV_MIN_GAP,
            ENV_STRONG_MATCH,
        ):
            monkeypatch.delenv(name, raising=False)

    def test_unknown_provider_gets_lenient_uncalibrated_gate(self):
        gate, meta = resolve_abstention_config("openai", "text-embedding-3-large")
        assert gate == UNCALIBRATED_CONFIG
        assert meta["embedder"] == "openai:text-embedding-3-large"
        assert meta["calibrated"] is False
        assert meta["thresholds_from"] == "uncalibrated"
        assert "没有评测证据" in meta["reason"]

    def test_known_profiles_report_their_own_calibration(self):
        gate, meta = resolve_abstention_config("ollama", "nomic-embed-text")
        assert gate == CALIBRATED_PROFILES["ollama:nomic-embed-text"]
        assert meta["calibrated"] is True
        assert meta["thresholds_from"] == "ollama:nomic-embed-text"

        hashing, hashing_meta = resolve_abstention_config(
            "hashing", "sha256-bigram-512"
        )
        assert hashing.min_top_similarity == 0.08
        assert hashing_meta["thresholds_from"] == "hashing:sha256-bigram-512"

    def test_identity_is_normalized(self):
        assert embedder_identity(" Ollama ", "Nomic-Embed-Text") == (
            "ollama:nomic-embed-text"
        )
        assert embedder_identity("mock", None) == "mock"
        assert embedder_identity(None, None) == "unknown"

    def test_declaration_for_another_provider_is_rejected(self, monkeypatch):
        monkeypatch.setenv(ENV_CALIBRATED_FOR, "ollama:nomic-embed-text")
        monkeypatch.setenv(ENV_MIN_SIMILARITY, "0.65")
        monkeypatch.setenv(ENV_MIN_GAP, "0.05")
        monkeypatch.setenv(ENV_STRONG_MATCH, "0.85")

        gate, meta = resolve_abstention_config("openai", "text-embedding-3-large")
        assert gate == UNCALIBRATED_CONFIG
        assert meta["thresholds_from"] == "uncalibrated"
        assert "另一个 embedding" in meta["reason"]

    def test_declaration_without_all_thresholds_is_uncalibrated(self, monkeypatch):
        monkeypatch.setenv(ENV_CALIBRATED_FOR, "openai:text-embedding-3-large")
        monkeypatch.setenv(ENV_MIN_SIMILARITY, "0.5")
        # gap / strong 缺失：半个校准不可用
        gate, meta = resolve_abstention_config(
            "openai", "text-embedding-3-large"
        )
        assert gate == UNCALIBRATED_CONFIG
        assert meta["calibrated"] is False
        assert "半个校准" in meta["reason"]

    def test_complete_declaration_enables_its_thresholds(self, monkeypatch):
        monkeypatch.setenv(ENV_CALIBRATED_FOR, "openai:text-embedding-3-large")
        monkeypatch.setenv(ENV_MIN_SIMILARITY, "0.71")
        monkeypatch.setenv(ENV_MIN_GAP, "0.06")
        monkeypatch.setenv(ENV_STRONG_MATCH, "0.9")

        gate, meta = resolve_abstention_config(
            "openai", "text-embedding-3-large"
        )
        assert gate.min_top_similarity == 0.71
        assert meta["calibrated"] is True
        assert meta["thresholds_from"] == "openai:text-embedding-3-large"

    def test_pinned_config_is_marked_explicit_not_calibrated(self):
        pinned = _config()
        gate, meta = resolve_abstention_config(
            "openai", "text-embedding-3-large", pinned=pinned
        )
        assert gate is pinned
        assert meta["thresholds_from"] == "explicit"
        assert meta["calibrated"] is False

    def test_unknown_embedder_never_false_abstains(self, tmp_path):
        store = PersistentVectorStore(str(tmp_path / "uncalibrated.sqlite3"))
        service = KnowledgeDocumentService(
            vector_store=store, embedder=_KeywordEmbedder()
        )
        service.ingest("局域网访问设置。", doc_id="lan")

        miss = service.search("员工年假怎么申请", top_k=10)
        assert miss["hits"], "宽松弃答不能把没命中的问题也判成没找到"
        assert miss["retrieval"]["status"] == STATUS_OK
        assert miss["retrieval"]["thresholds"]["min_top_score"] == -1.0
        calibration = miss["retrieval"]["abstention_calibration"]
        assert calibration["embedder"] == "keyword-test:keyword-test"
        assert calibration["calibrated"] is False
        assert calibration["thresholds_from"] == "uncalibrated"
        store.close()

    def test_calibrated_embedder_exposes_profile_source(self, tmp_path):
        store = PersistentVectorStore(str(tmp_path / "hashing.sqlite3"))
        service = KnowledgeDocumentService(
            vector_store=store, embedder=evaluation.HashingEmbedder()
        )
        service.ingest("局域网访问设置。", doc_id="lan")

        calibration = service.search("局域网", top_k=3)["retrieval"][
            "abstention_calibration"
        ]
        assert calibration["calibrated"] is True
        assert calibration["thresholds_from"] == "hashing:sha256-bigram-512"
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
