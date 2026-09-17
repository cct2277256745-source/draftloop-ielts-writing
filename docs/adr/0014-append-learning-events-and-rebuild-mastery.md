# Append learning events and rebuild mastery

C2 stores immutable Learning Events and derives Mastery Projections by
deterministic replay, with correction and deletion represented as later events and
assistance eligibility enforced by a versioned policy. This is more explicit than
updating one mastery row, but it makes provenance, tenant isolation, regression,
policy rebuilds, and the rule that AI-assisted content cannot independently raise
mastery auditable without allowing memory into scoring.
