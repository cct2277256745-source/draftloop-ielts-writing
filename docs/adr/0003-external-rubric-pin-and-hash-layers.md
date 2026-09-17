# Keep the Task 2 rubric pin outside private assets and separate hash domains

## Status

Accepted for P1-01.

## Context

ADR 0002 makes the immutable version directory selected by `current.json` the
read-only Task 2 scoring authority. Those descriptor assets remain local/private and
Git-ignored. Runtime consumers nevertheless need a reviewable trust root, exact cache
identity, package tamper detection, and optional evidence that local PDFs match the
approved official sources. One hash cannot safely represent all three concerns: raw
JSON formatting is not scoring semantics, and a local PDF path is not runtime identity.

## Decision

Keep a tracked, code-owned `RubricPin` outside `rubrics/task2/**`. It allows one rubric
identity/version, the exact raw-byte hashes of current/manifest/schema/criterion JSON,
the approved official source IDs/classifications/PDF hashes, the review gate, legal
status, and one expected Runtime Content Hash.

Use three non-interchangeable hash domains:

1. `runtime_content_sha256` hashes a fixed canonical semantic envelope and is the only
   rubric hash used by cache, trace, result, or Provider identity.
2. `asset_file_sha256` checks local package bytes before parsing and never leaves the
   loader/validator boundary.
3. `source_pdf_sha256` is provenance metadata checked only when a caller explicitly
   supplies a PDF to the offline verifier. PDFs and local paths are optional and cannot
   influence runtime identity.

The loader validates the pin and complete package before constructing a frozen
`StructuredRubricSnapshot`. Any missing, modified, escaped, symlinked, incomplete, or
authority-contaminated package fails closed before Provider preflight. `current.json`
selects the pinned version and proves the 5/6/7 projection only; it is not the scoring
snapshot.

## Consequences

- Private descriptor files stay untracked and untouched while the runtime contract and
  trust root are reviewable.
- Whitespace-only asset changes require a new pin even when runtime semantics do not
  change; semantic changes additionally require a new runtime hash and regression run.
- Moving or renaming a verified source PDF has no runtime effect.
- A missing private rubric makes Task 2 explicitly unavailable while Task 1 keeps its
  pre-P1-02 path.
- Updating an official source/version is a deliberate new pin/version decision, not an
  environment-variable or prompt change.

This ADR extends ADR 0002 and does not alter its authority meaning.
