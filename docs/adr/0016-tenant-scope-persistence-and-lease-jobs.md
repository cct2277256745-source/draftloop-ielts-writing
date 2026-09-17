# Enforce tenant ownership in repositories and separate lease jobs from results

## Status

Accepted for C3.

## Context

Submission retries, worker crashes, guessed identifiers, and shared persistence
can duplicate results or expose another learner's records unless identity,
idempotency, and state transitions meet at one transactional boundary.

## Decision

Every persisted product record carries tenant and owner lineage. Repository reads
check both and return `NOT_FOUND` for unauthorized identifiers. Tenant admins may
operate only inside their own tenant. Session tokens are random, stored only as
hashes, expire, and can be revoked; passwords use a versioned salted
PBKDF2-HMAC-SHA256 representation.

The reproducible adapter is SQLite with foreign keys, full synchronous commits,
mode-0600 database files, reversible schema migration, online backup/restore, and
transactional unique constraints. Submission Intent is unique per tenant, owner,
and idempotency hash. Durable jobs use bounded attempts, exclusive time-limited
leases, monotonic progress, cancellation checks at acceptance, and a state machine
separate from Submission state. Only one semantic result hash and one report
artifact can be accepted for a Submission.

## Consequences

- The local adapter proves isolation and recovery contracts, not production
  database operations or managed-service availability.
- Crashed leases return to retry or terminal failure without false completion.
- Deployment-specific encryption-at-rest and infrastructure selection require a
  later authorized environment decision; no such claim is made by C3.
