"""Run the real ONNX model against labeled behavioral queries."""
import argparse
import json
import os
from pathlib import Path
import statistics
import shutil
import sys
from tempfile import TemporaryDirectory
import time
from unittest.mock import patch

from auto_index_mcp.core.service import AutoIndexService

REPOSITORY = Path(__file__).resolve().parents[2]
DATASET = REPOSITORY / "tests/fixtures/retrieval_queries.json"


def evaluate(service, dataset, repeats=3):
    cases = dataset["queries"]
    service.semantic_search(cases[0]["query"], limit=5)
    rows, elapsed = [], []
    for case in cases:
        relevant = {tuple(item) for item in case["relevant"]}
        ranks, times, sizes, orders = [], [], [], []
        for _ in range(repeats):
            started = time.perf_counter()
            result = service.semantic_search(case["query"], limit=5)
            duration = time.perf_counter() - started
            if not result.get("complete"):
                raise RuntimeError(f"incomplete evaluation query {case['id']}: {result}")
            identities = [(hit["file_path"], hit["symbol_name"]) for hit in result["items"]]
            orders.append(tuple(identities))
            ranks.append(next((index + 1 for index, identity in enumerate(identities) if identity in relevant), None))
            times.append(duration)
            sizes.append(len(json.dumps(result, ensure_ascii=False).encode("utf-8")))
        elapsed.extend(times)
        rows.append(dict(id=case["id"], query=case["query"], rank=ranks[0], stable=len(set(orders)) == 1,
                         hits=identities, mean_seconds=statistics.mean(times),
                         stdev_seconds=statistics.pstdev(times), mean_response_bytes=statistics.mean(sizes)))
    metrics = dict(recall_at_1=sum(row["rank"] == 1 for row in rows) / len(rows),
                   recall_at_5=sum(row["rank"] is not None for row in rows) / len(rows),
                   mrr_at_5=sum(1 / row["rank"] if row["rank"] else 0 for row in rows) / len(rows))
    thresholds = dataset["thresholds"]
    failures = [f"{key}={metrics[key]:.4f} < {value}" for key, value in thresholds.items() if metrics[key] < value]
    if not all(row["stable"] for row in rows):
        failures.append("ranking changed across repeated queries")
    return dict(evaluation_completed=True, target_met=not failures, target_gaps=failures, dataset_version=dataset["version"],
                queries=len(cases), repeats=repeats, warmup_queries=1, metrics=metrics, thresholds=thresholds,
                recall_definition="query hit rate: any accepted implementation in top K; alternatives are interchangeable",
                mean_query_seconds=statistics.mean(elapsed), stdev_query_seconds=statistics.pstdev(elapsed),
                tool_calls_per_query=1, response_measure="UTF-8 bytes; not tokenizer tokens", results=rows)


def run_evaluation(repeats=3):
    dataset = json.loads(DATASET.read_text(encoding="utf-8"))
    temporary_root = REPOSITORY / ".pytest_cache"
    temporary_root.mkdir(exist_ok=True)
    with TemporaryDirectory(prefix="retrieval-", dir=temporary_root) as temporary:
        root = Path(temporary)
        source = root / "source"
        shutil.copytree(REPOSITORY / dataset["scope"], source, ignore=shutil.ignore_patterns("__pycache__"))
        environment = dict(AUTO_INDEX_CACHE_DIR=str(root / "cache"), AUTO_INDEX_REGISTRY_DIR=str(root / "registry"),
                           AUTO_INDEX_CONTROL_DIR=str(root / "control"), AUTO_INDEX_SEMANTIC_MODE="eager",
                           AUTO_INDEX_EMBEDDING_MODEL=str(REPOSITORY / "models/minilm-onnx"))
        with patch.dict(os.environ, environment):
            service = AutoIndexService(index_root=root / "index")
            try:
                built = service.enable(str(source))
                worker = service.embedding_background
                if worker is None:
                    raise RuntimeError("real model did not complete its embedding pass")
                deadline = time.monotonic() + 600
                while not worker.wait(15):
                    print(f"embedding: {worker.status()['phase']}, vectors={service.embedding_indexer.count() if service.embedding_indexer else 0}",
                          file=sys.stderr, flush=True)
                    if time.monotonic() >= deadline:
                        raise TimeoutError("real model embedding exceeded ten minutes")
                status = service.embedding_status()
                if not status["complete"]:
                    raise RuntimeError(f"real model evaluation unavailable: {status}")
                report = evaluate(service, dataset, repeats)
                report.update(model=service.embedding_indexer.model_key, files=built["file_count"],
                              symbols=service.store.symbol_count(), vector_count=status["vector_count"])
                return report
            finally:
                service.disable()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--check-target", action="store_true", help="fail when the declared retrieval targets are unmet")
    parser.add_argument("--report-only", action="store_true", help="record unmet targets without a failing exit status")
    parser.add_argument("--baseline", type=Path, help="fail if Hit@5 or MRR@5 regresses from a saved measured baseline")
    args = parser.parse_args()
    if args.repeats < 1:
        parser.error("--repeats must be positive")
    if args.report_only and args.check_target:
        parser.error("--report-only and --check-target are mutually exclusive")
    report = run_evaluation(args.repeats)
    regressions = []
    if args.baseline:
        baseline = json.loads(args.baseline.read_text(encoding="utf-8"))
        if baseline["dataset_version"] != report["dataset_version"]:
            raise ValueError("baseline dataset version differs")
        regressions = [key for key in ("recall_at_5", "mrr_at_5")
                       if report["metrics"][key] + 1e-9 < baseline["metrics"][key]]
    report["baseline_regressions"] = regressions
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps({key: value for key, value in report.items() if key != "results"}, indent=2))
    for row in report["results"]:
        print(f"{row['id']}: rank={row['rank']} top={row['hits'][:2]}")
    return int(bool(regressions) or (not args.report_only and not report["target_met"]))


if __name__ == "__main__":
    raise SystemExit(main())
