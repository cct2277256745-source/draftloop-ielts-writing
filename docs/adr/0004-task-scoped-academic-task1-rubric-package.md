# Keep Academic Task 1 rubric assets task-scoped

## Status

Accepted for P1-02.

## Context

Academic Task 1 and Task 2 use the same four broad assessment families, and their CC,
LR, and GRA definitions come from shared sections of the Key Assessment Criteria
publication. It is therefore tempting to make Task 1 reference the existing Task 2
criterion JSON. The official Band Descriptors publication, however, has separate Task
1 and Task 2 tables whose wording and page coordinates are not interchangeable. Task
1 also replaces TR with TA and has an Academic-specific information-transfer boundary.

## Decision

Reuse the immutable snapshot types, validation algorithm, three hash domains, and
optional provenance verifier from P1-01, but keep Task 1 identity, pin, schemas,
manifest, current pointer, criterion files, source IDs, page coordinates, and review
gate independent.

Shared criterion definitions may be represented in both task packages only after
mapping them to each task's approved source coordinates. Task 1 never imports a Task 2
criterion file or assumes that shared definitions imply shared band descriptors.

## Consequences

- Task-specific source drift cannot silently alter both tasks or misstate provenance.
- Some exact definition text is duplicated, but each package remains complete,
  content-addressed, independently reviewable, and independently replaceable.
- The shared loader is configuration-driven and Task 2 retains its accepted identity.
- Future source changes require an explicit per-task version and pin decision.
- This ADR extends ADR 0003's hash and pin model without changing P1-01 authority.
