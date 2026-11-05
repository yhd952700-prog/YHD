"""C 回归：评测集规模/覆盖、可复现性、基线不得回退。"""

from __future__ import annotations

from src.knowledge import evaluation

_EXPECTED_CATEGORIES = {"fact", "identifier", "synonym", "cross_paragraph"}


class TestEvaluationSet:
    def test_size_is_within_the_agreed_range(self):
        assert 20 <= len(evaluation.QUERIES) <= 30

    def test_all_four_categories_are_covered(self):
        categories = {query.category for query in evaluation.QUERIES}
        assert categories == _EXPECTED_CATEGORIES

    def test_every_label_points_at_a_real_document(self):
        known = {document.doc_id for document in evaluation.CORPUS}
        for query in evaluation.QUERIES:
            assert query.relevant_doc_ids
            assert set(query.relevant_doc_ids) <= known


class TestReproducibility:
    def test_two_runs_produce_identical_metrics(self):
        first = evaluation.run_evaluation()["metrics"]
        second = evaluation.run_evaluation()["metrics"]
        assert first == second

    def test_metrics_are_bounded(self):
        metrics = evaluation.run_evaluation()["metrics"]
        for key in ("recall@5", "recall@10", "mrr"):
            assert 0.0 <= float(metrics[key]) <= 1.0

    def test_baseline_is_not_regressed(self):
        report = evaluation.baseline_report()
        assert report["regression"] is False, report["delta"]
        for key, floor in evaluation.BASELINE.items():
            assert float(report["metrics"][key]) >= floor - 1e-4
