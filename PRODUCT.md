# Product

<!-- impeccable:product-schema 1 -->

## Platform

web

## Stack

The final product uses a shared React, TypeScript, and Vite client for responsive
web and a thin PySide WebEngine macOS shell. Both surfaces consume the accepted
C3 application-service and API contracts; the shell adds only typed,
user-mediated open/save capabilities.

## Users

Primary users are Chinese-speaking learners preparing independently for IELTS
Academic Writing. They need to submit Task 1 or Task 2 work, understand an
evidence-linked assessment, revise without losing their own voice, and know what
to practise next. The bounded beta is invite-only for 10–30 learners.

## Product Purpose

DraftLoop helps a learner move through a complete writing loop: submit,
understand, revise, verify, and remember. Success means the learner can act on a
code-locked estimated overall band and four criterion bands, trace feedback to their own text,
use progressively disclosed help, and retain control of their data. It is not an
official IELTS result and does not claim examiner equivalence.

## Positioning

DraftLoop combines a code-owned, fail-closed assessment authority with a
student-led learning loop. Scoring, presentation, revision evidence, and memory
remain separately governed so that incomplete evidence, AI assistance, or past
performance cannot silently become a stronger score or independent mastery.

## Operating Context

- Learners submit a Task 2 prompt and Candidate Script, or a Task 1 prompt,
  Candidate Script, and validated chart image.
- The live report has rewrite and deep-correction pages. The rewrite page shows
  exact evidence and edits before optional long explanations; deep correction
  includes criteria, paragraph analysis, topic learning and next actions.
- Settings combine model configuration and personal data. The model workflow
  supports 16 provider presets, manual model IDs, model discovery and assignments
  for Task 1, Task 2, independent audit and teaching-report stages. Presets do not
  imply that every provider/model has been tested or endorsed.
- Mode A presents the complete report and evidence; Mode B guides one selected
  issue through four progressive hint levels, V2 editing, and verification.
- History, topic learning, memory/mastery explanations, next practice,
  correction, privacy export, deletion, and PDF/export are part of the same
  account-scoped product.
- Work may resume across browser tabs, devices, responsive web, and the macOS
  shell; stale writes and cross-tenant identifiers fail closed.
- The first release is an invite-only beta. Self-registration, payments, teacher
  portals, and marketplaces are not part of C4.

## Capabilities and Constraints

- Every product command enters through a C3 Tenant Principal and C3-owned
  authorization, ownership, idempotency, durable-job, persistence, privacy,
  deletion, and audit controls. `DraftLoopApplicationService` is a composition
  facade over that boundary, never an alternate path to C1/C2 engines.
- Semantic Result, Presentation Projection, and Export Artifact are separate,
  versioned, content-addressed contracts. The frontend does not recalculate or
  upgrade domain state.
- `COMPLETE`, `PARTIAL`, `REVIEW_REQUIRED`, `FAILED`, and disabled capabilities
  remain visibly distinct. `REVIEW_REQUIRED` never appears or exports as a
  normal final report.
- Evidence links use validated locators. Raw Provider output, private traces,
  server credentials, Provider secrets, and private package content are never
  exposed to JavaScript or ordinary UI.
- Progressive hints reveal only the requested level. AI assistance is always
  labelled and is never presented as independent mastery.
- The live local assessment first scores from the Official Rubric and validated
  current Student Evidence. An independent RAG audit receives no initial bands,
  target band or learner history. Code compares the absolute overall-band gap:
  initial >= 7.0 triggers fresh retrieval and Rubric re-scoring at gap >= 0.5;
  initial < 7.0 triggers only at gap > 0.5. Otherwise retain the initial score.
- A triggered re-score receives five inputs: Official Rubric, current validated
  Student Evidence, initial findings and reasons, valid freshly retrieved RAG
  evidence, and specific disagreements. It passes normal validators before
  locking; averaging, choosing the higher score or copying the audit is forbidden.
- RAG retains package rights, provenance and authority gates. The local configured
  private package has an exercised retrieval-to-report path; this does not extend
  its authorization to public distribution. Missing configuration is explicitly
  unaudited. A configured audit or re-score failure requires review, never a
  fabricated agreement. ADR 0020 governs the updated pre-lock timing.
- Composition-dependent implementation stops at
  `COMPOSITION_REVIEW_REQUIRED`. Real-user use stops at
  `BETA_READY_FOR_AUTHORIZATION` until the required operational authorities are
  explicit.

## Brand Commitments

- Product name: **DraftLoop**.
- The interface is Chinese-first (`zh-CN`) with an `en-US` interface locale;
  English IELTS terminology, prompts, Candidate Scripts, and evidence excerpts
  remain in English where accuracy requires it.
- The voice is calm, exact, and learning-oriented. It shows an estimated overall
  band and criterion bands without Likely Range or Confidence labels, labels
  synthetic demonstrations, and avoids official,
  examiner-verified, or guaranteed-improvement language.
- The retained bilingual navy-and-paper report is the incumbent product identity
  to extend across the application, not a disposable export-only theme.

## Evidence on Hand

- Accepted C1 assessment/evidence and C2 coaching/revision/memory contracts,
  audits, and deterministic tests.
- Accepted C3 application/platform boundary, API schemas, privacy controls,
  durable jobs, release evidence, and rollback runbooks.
- A rights-safe synthetic C3 demo submission, clearly labelled non-official and
  non-examiner-verified.
- Current live implementation in `frontend/src/LiveWorkspace.tsx`,
  `RedesignedReport.tsx`, `ModelSettings.tsx` and `writing-studio.css`; updated
  assessment decision in `docs/adr/0020-audit-rubric-scores-before-locking.md`, and current implementation
  evidence in `output/portfolio/PRD.md`.
- Incumbent desktop behavior and report identity in
  `app/ui/`, `app/resources/styles.qss`, and
  `design-preview/report-template-v1.html`.
- No real-user beta results, testimonials, willingness-to-pay evidence, public
  Provider benchmark, or commercial proof exists yet. C4 must not fabricate it.

## Product Principles

1. Evidence before claims: show lineage, uncertainty, and missing evidence
   without filling gaps with confidence theatre.
2. Incomplete stays incomplete: presentation and export never upgrade semantic
   state.
3. Preserve learner agency: keep the learner's facts, position, language, and
   decision-making visible before offering more help.
4. Assistance stays attributable: guided or AI-supported work cannot masquerade
   as independent performance or mastery.
5. Privacy, tenant isolation, rights, accessibility, and rollback are product
   behavior, not release polish.

## Accessibility & Inclusion

Target WCAG 2.2 AA for responsive web and equivalent keyboard, focus, and
screen-reader behavior in the macOS shell. Support reduced motion, zoom and
reflow, high-contrast state distinctions, bilingual screen-reader labels,
long English evidence, Chinese explanatory copy, and operation without color as
the only status cue.
