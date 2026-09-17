#!/usr/bin/env python3
"""Offline CLI for the P1-05 evaluation-infrastructure gate."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from app.core.benchmark_registry import load_registry  # noqa: E402
from app.core.evaluation_gate import (  # noqa: E402
    DEFAULT_GATE_ROOT,
    EvaluationGate,
    GateErrorCode,
    EvaluationGateError,
    foundation_run,
    load_definitions,
    load_metric_report,
    load_policy,
    load_registry_artifact,
)


def _output(value: object) -> None:
    print(json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2))


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate P1-05 offline metric and gate evidence.")
    parser.add_argument("command", choices=("validate-policy", "metrics", "decide", "foundation"))
    parser.add_argument("--gate-root", type=Path, default=DEFAULT_GATE_ROOT)
    parser.add_argument("--artifact", type=Path)
    parser.add_argument("--report", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    try:
        definitions, policy = load_definitions(args.gate_root), load_policy(args.gate_root)
        gate = EvaluationGate(definitions, policy)
        if args.command == "validate-policy":
            result: object = {"status": "POLICY_VALID", "policySha256": policy.content_sha256, "dictionarySha256": policy.dictionary_sha256}
        elif args.command == "foundation":
            if args.output is None:
                raise EvaluationGateError(GateErrorCode.INVALID_SCHEMA, "--output required")
            artifact, report, decision = foundation_run(args.output, args.gate_root)
            result = {"status": decision.acceptance or decision.decision.value, "runId": artifact.run.run_id, "report": report.to_dict(), "decision": decision.to_dict()}
        elif args.command == "metrics":
            if args.artifact is None:
                raise EvaluationGateError(GateErrorCode.INVALID_SCHEMA, "--artifact required")
            registry = load_registry()
            report = gate.metric_report(load_registry_artifact(args.artifact, registry), registry)
            result = report.to_dict()
        else:
            if args.report is None:
                raise EvaluationGateError(GateErrorCode.INVALID_SCHEMA, "--report required")
            decision = gate.decide(load_metric_report(json.loads(args.report.read_text(encoding="utf-8"))))
            result = decision.to_dict()
    except (EvaluationGateError, OSError, ValueError) as exc:
        _output({"status": "PHASE_1_BLOCKED", "error": getattr(getattr(exc, "code", None), "value", "INVALID_INPUT")})
        return 1
    _output(result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
