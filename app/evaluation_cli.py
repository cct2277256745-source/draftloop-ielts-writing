#!/usr/bin/env python3
"""Offline fixture entry point for the P1-03 benchmark runner."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from app.core.evaluation import (  # noqa: E402
    ArtifactStore,
    BenchmarkCase,
    BenchmarkRun,
    EvaluationError,
    EvaluationErrorCode,
    EvaluationRunner,
    ExperimentConfig,
    FixtureExecutor,
    TierLabel,
    compare_runs,
)
from app.core.observability import ModelPricing  # noqa: E402
from decimal import Decimal  # noqa: E402


def fixture_cases() -> tuple[BenchmarkCase, ...]:
    """Small public-safe fixtures; they do not encode IELTS scoring claims."""
    return (
        BenchmarkCase("fixture-missing-usage", TierLabel.B, "fixture", {"fixtureOutcome": "missing_usage", "latencyMs": 2}),
        BenchmarkCase("fixture-schema-failure", TierLabel.C, "fixture", {"fixtureOutcome": "schema_failure", "latencyMs": 3}),
        BenchmarkCase("fixture-success", TierLabel.C, "fixture", {"fixtureOutcome": "success", "latencyMs": 1}),
    )


def build_run(semantic_version: str) -> BenchmarkRun:
    config = ExperimentConfig(
        semantic_version=semantic_version,
        executor_id="offline-fixture-v1",
        version_snapshot={
            "runner": "p1-03-v1",
            "providerContract": "p0-02-provider-neutral",
            "usageLedger": "p0-05",
            "rubricRuntime": "task-scoped-p1-01-p1-02",
        },
        options={"offline": True, "fixture": "p1-03"},
    )
    return BenchmarkRun.create(fixture_cases(), config)


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the deterministic offline P1-03 fixture benchmark.")
    parser.add_argument("--artifact-dir", required=True, type=Path)
    parser.add_argument("--semantic-version", default="fixture-v1")
    parser.add_argument("--interrupt-after", type=int, default=None, metavar="N")
    parser.add_argument("--compare-artifact-dir", type=Path, default=None)
    args = parser.parse_args()

    try:
        run = build_run(args.semantic_version)
        runner = EvaluationRunner(
            FixtureExecutor(),
            pricing={
                ("offline-fixture", "fixture-model", "fixture-model@v1"): ModelPricing(
                    provider="offline-fixture", model="fixture-model", model_version="fixture-model@v1",
                    input_per_million=Decimal("1"), output_per_million=Decimal("2"),
                )
            },
        )
        store = ArtifactStore(args.artifact_dir)
        artifact = runner.execute(run, store, args.interrupt_after)
        output: dict[str, object] = {
            "status": "BENCHMARK_RUNNER_PASS" if artifact.state.value == "COMPLETE" else "BENCHMARK_RUNNER_INCOMPLETE",
            "runId": run.run_id,
            "artifact": str(store.json_path(run)),
            "summary": artifact.summary(),
        }
        if args.compare_artifact_dir is not None:
            other = ArtifactStore(args.compare_artifact_dir).load(run)
            if other is None:
                raise EvaluationError(EvaluationErrorCode.INVALID_CONFIG, "comparison artifact does not exist")
            comparison = compare_runs(artifact, other)
            output["comparison"] = {
                "comparisonIdentity": comparison.comparison_identity,
                "comparable": comparison.comparable,
                "reason": comparison.reason,
            }
    except (EvaluationError, OSError, ValueError) as exc:
        print(json.dumps({"status": "BENCHMARK_RUNNER_BLOCKED", "error": getattr(exc, "code", "INVALID_INPUT")}, default=lambda value: getattr(value, "value", str(value))))
        return 1
    print(json.dumps(output, ensure_ascii=False, sort_keys=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
