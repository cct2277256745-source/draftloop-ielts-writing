# Make Phase 1 an evaluation-infrastructure gate, not a quality claim

## Status

Accepted for P1-05.

## Context

P1-05 supplies the metric and gate infrastructure required by later Task 2 and
Task 1 scoring acceptance work. At this point the project has no scoring
implementation, real-provider benchmark, or human-scored benchmark. Treating
those absent inputs as a failed Phase 1 product-quality claim would block the
later phases that are responsible for producing the evidence.

Conversely, accepting source code alone would permit missing rights, malformed
artifacts, or hidden expected failures to masquerade as evaluation readiness.
There is also no empirical basis for numerical score, latency, failure, or cost
thresholds, and human-scored accuracy is an explicit product non-goal.

## Decision

Phase 1 accepts only evaluation-infrastructure readiness. Every P1-05 decision
uses the claim scope `EVALUATION_INFRASTRUCTURE_ONLY`; an ACCEPT carries
`PHASE_1_ACCEPT` and asserts no score quality, examiner agreement, human
verification, or production-performance result.

Metric definitions, units, denominators, aggregation rules, missing-data states,
and a versioned threshold-policy mechanism are fixed now. Thresholds without
empirical support remain explicitly deferred. `THRESHOLD_NOT_ESTABLISHED`,
`UNKNOWN`, and `NOT_EVALUATED` remain visible but do not block this foundation
policy. A later policy that marks a metric REQUIRED must provide both an
appropriate threshold and measurement; otherwise its decision is
`NEEDS_REVISION`.

Rights compliance and deterministic expected-check conformance are required
Phase 1 integrity measures. Artifact or policy tampering is rejected before a
decision is issued. Human review metadata may support blinded evidence or
bad-case workflow only; it cannot carry human score, awarded-band, examiner
agreement, QWK, or MAE claims.

## Consequences

- P2/P3 can reuse one immutable evaluator while supplying their own versioned,
  empirically justified policies and metric observations.
- Offline fixture evidence proves denominator, rights, artifact, and fail-closed
  behavior only; it cannot be relabelled as quality validation.
- Unknown cost is retained as unknown and cannot become free by aggregation.
- Rollback removes P1-05 contracts and policy artifacts without changing P1-03,
  P1-04, rubric packages, or runtime scoring behavior.
