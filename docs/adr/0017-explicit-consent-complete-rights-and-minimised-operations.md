# Make consent explicit, rights complete, and operations data-minimised

## Status

Accepted for C3.

## Context

Candidate Scripts and Task 1 images are sensitive learner content. Ordinary
product analytics, support intake, and recovery copies can silently become shadow
stores that defeat revocation, export, or deletion.

## Decision

Unknown consent denies training use. Consent grant and revocation are versioned and
receipted. Authenticated export contains the complete owner-scoped primary records
but excludes password hashes and sessions. Deletion is asynchronous and idempotent:
it verifies every registered backup, purges those copies, removes upload bytes,
purges primary/derived/eligible operational records, then retains only a minimised
subject hash and completion receipt. Retention uses the same backup-aware deletion
path and defaults to dry-run discovery.

Operational events accept only versioned event types, allowlisted short
dimensions, and numeric values. Full Candidate Scripts, questions, images,
Provider bodies, tokens, and raw exceptions have no event field. Aggregates use
small-cohort suppression and a bounded disclosure-query budget. This is explicitly
not a differential-privacy claim. Support intake is structured and carries no
free-text essay field.

## Consequences

- Missing backup media blocks deletion rather than claiming completion.
- Privacy receipts prove actions but do not retain the deleted content.
- Real-user retention, legal, and compliance decisions remain human/C4 gates.
