"""C 回归：加难评测集的覆盖、可复现性与基线不得回退。"""

from __future__ import annotations

from src.knowledge import evaluation
from src.knowledge.vector_store import tokenize_for_index

_EXPECTED_CATEGORIES = {
    "fact",
    "identifier",
    "synonym",
    "cross_paragraph",
    "section",
    "unanswerable",
}
_ZERO_OVERLAP_QUERIES = {
    "如何做备份",
    "磁盘快满了怎么办",
    "登录后老是掉线",
    "服务器频繁重启是什么原因",
}


def _tokens(text: str) -> set:
    return set(tokenize_for_index(text).split())


class TestEvaluationSet:
    def test_size_is_within_the_agreed_range(self):
        assert 20 <= len(evaluation.QUERIES) <= 30

    def test_all_categories_are_covered(self):
        categories = {query.category for query in evaluation.QUERIES}
        assert categories == _EXPECTED_CATEGORIES

    def test_every_answerable_label_points_at_a_real_document(self):
        known = {document.doc_id for document in evaluation.CORPUS}
        for query in evaluation.QUERIES:
            if query.category == "unanswerable":
                assert query.relevant_doc_ids == ()
            else:
                assert query.relevant_doc_ids
                assert set(query.relevant_doc_ids) <= known

    def test_zero_overlap_synonyms_really_have_no_surface_overlap(self):
        corpus = {
            document.doc_id: _tokens(document.text)
            for document in evaluation.CORPUS
        }
        hard = [
            query
            for query in evaluation.QUERIES
            if query.query in _ZERO_OVERLAP_QUERIES
        ]
        assert len(hard) == len(_ZERO_OVERLAP_QUERIES)
        for query in hard:
            target = query.relevant_doc_ids[0]
            assert not (_tokens(query.query) & corpus[target]), (
                f"{query.query} 与目标 {target} 仍有字面重合，难度不够"
            )

    def test_unanswerable_query_is_present(self):
        unanswerable = [
            query
            for query in evaluation.QUERIES
            if query.category == "unanswerable"
        ]
        assert unanswerable
        assert all(query.relevant_doc_ids == () for query in unanswerable)

    def test_long_document_section_query_has_an_answer_marker(self):
        marker_queries = [
            query for query in evaluation.QUERIES if query.answer_marker
        ]
        assert marker_queries


class TestReproducibility:
    def test_two_runs_produce_identical_metrics(self):
        first = evaluation.run_evaluation()["metrics"]
        second = evaluation.run_evaluation()["metrics"]
        assert first == second

    def test_metrics_are_bounded(self):
        metrics = evaluation.run_evaluation()["metrics"]
        for key in evaluation.BASELINE:
            assert 0.0 <= float(metrics[key]) <= 1.0

    def test_section_marker_rows_are_graded(self):
        result = evaluation.run_evaluation()
        marker_rows = [
            row for row in result["rows"] if row["marker_hit@5"] is not None
        ]
        assert marker_rows
        assert all(
            0.0 <= float(row["marker_hit@5"]) <= 1.0 for row in marker_rows
        )

    def test_baseline_is_deliberately_not_saturated(self):
        # 1.0 基线证明不了检索质量；加难后应落在有提升空间的位置。
        assert evaluation.BASELINE["recall@5"] < 1.0
        assert evaluation.BASELINE["recall@5"] >= 0.75

    def test_baseline_is_not_regressed(self):
        report = evaluation.baseline_report()
        assert report["regression"] is False, report["delta"]
        for key, floor in evaluation.BASELINE.items():
            assert float(report["metrics"][key]) >= floor - 1e-4
