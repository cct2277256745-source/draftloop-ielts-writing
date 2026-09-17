# Route desktop and server presentations through application services

## Status

Accepted for C3.

## Context

The accepted desktop worker called rubric loading and grading orchestration
directly. A server adapter implemented independently would duplicate failure
mapping and could turn a partial or review-required result into a successful HTTP
response.

## Decision

Move the synchronous desktop use case behind
`DesktopGradingApplicationService`, while the asynchronous product platform uses
`ProductPlatformService`. PySide, the in-process API router, and the standard WSGI
adapter are presentation adapters only. API version `v1` returns typed public-safe
errors and exposes domain and durable-job state separately.

The server adapter does not own scoring, report composition, or Locked Score
identity. It persists accepted artifacts only after an injected processor returns
a validated fail-closed outcome.

## Consequences

- Existing desktop behavior remains characterized by its prior tests.
- A deployment may replace WSGI or SQLite without changing use-case contracts.
- Final desktop/web information architecture remains C4 work.
