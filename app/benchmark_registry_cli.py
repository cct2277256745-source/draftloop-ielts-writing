#!/usr/bin/env python3
"""Offline CLI for validating and exporting the P1-04 registry package."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from app.core.benchmark_registry import (  # noqa: E402
    DEFAULT_REGISTRY_ROOT,
    RegistryValidationError,
    load_registry,
    registry_gate,
)


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate the offline P1-04 benchmark registry.")
    parser.add_argument("command", choices=("validate", "coverage", "export-run-cases"))
    parser.add_argument("--registry-root", type=Path, default=DEFAULT_REGISTRY_ROOT)
    args = parser.parse_args()
    try:
        registry = load_registry(args.registry_root)
        gate = registry_gate(registry)
        if args.command == "validate":
            output: object = gate
        elif args.command == "coverage":
            output = registry.coverage_summary()
        else:
            output = {
                "registryIdentity": registry.content_sha256,
                "cases": [case.identity_payload() for case in registry.executable_cases()],
            }
    except (RegistryValidationError, OSError, ValueError) as exc:
        print(json.dumps({
            "status": "BENCHMARK_REGISTRY_BLOCKED",
            "error": getattr(getattr(exc, "code", None), "value", "INVALID_INPUT"),
        }, sort_keys=True))
        return 1
    print(json.dumps(output, ensure_ascii=False, sort_keys=True, indent=2))
    return 0 if args.command != "validate" or gate["status"] == "BENCHMARK_REGISTRY_PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
