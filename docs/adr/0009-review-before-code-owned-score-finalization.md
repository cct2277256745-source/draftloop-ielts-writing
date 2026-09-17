# Review before code-owned score finalization

C1 treats Provider criterion output as unfinalized semantic evidence: code validates
and canonicalizes it, a deterministic review gate blocks incomplete, conflicting,
low-confidence, or non-comparable results, and only a passing decision can create an
immutable Locked Score Snapshot. This rejects the simpler legacy design in which a
Provider or report composer supplies the overall, because that design cannot prove
stable calculation, mutation resistance, or fail-closed publication.
