"""Evaluation metrics preserve graded ranks and identify misses."""
from scripts.evaluation.retrieval import evaluate


def test_relevance_metrics_count_ranked_hits_and_misses():
    class Search:
        def semantic_search(self, query, limit):
            names = {"first": ["target", "noise"], "second": ["noise", "target"], "miss": ["noise"]}[query]
            return dict(complete=True, items=[dict(file_path="code.py", symbol_name=name) for name in names])

    dataset = dict(version=1, thresholds=dict(recall_at_5=0.75, mrr_at_5=0.5), queries=[
        dict(id=query, query=query, relevant=[["code.py", "target"]]) for query in ("first", "second", "miss")])
    result = evaluate(Search(), dataset, repeats=2)
    assert result["metrics"] == dict(recall_at_1=1 / 3, recall_at_5=2 / 3, mrr_at_5=0.5)
    assert not result["target_met"]
    assert result["target_gaps"] == ["recall_at_5=0.6667 < 0.75"]
