# IELTS Writing AI Coach

This context defines the language used for IELTS Writing assessment,
calibration references, and learner-facing feedback.

## Language

**Rubric Initial Assessment**:
The first complete four-criterion assessment based only on the Official Rubric and validated evidence from the current Candidate Script, before calibration review and final score locking.
_Avoid_: Locked score, RAG score

**RAG Calibration Audit**:
An independent reference judgment using gated retrieval evidence and the current Candidate Script's validated evidence, compared with the Rubric Initial Assessment to identify a material discrepancy. It has no direct score authority.
_Avoid_: Score override, official reference band, average score

**Calibration Re-score**:
A new Rubric-based assessment that resolves a threshold-triggering disagreement using initial findings, current validated evidence, gated calibration references and explicit disagreement reasons before final locking.
_Avoid_: Score blending, favourable retry selection, RAG replacement

**Candidate Script**:
A writing response authored by a learner or test taker and submitted for assessment.
_Avoid_: Model answer, reference essay

**Synthetic Reference**:
An original machine-generated essay that is permanently labelled synthetic and designed to exhibit a controlled set of IELTS-like performance characteristics. It is not an official sample, examiner-verified script, or true band result.
_Avoid_: Official sample, verified Band essay, ground truth

**Target Overall Profile**:
A corpus-management label used to group Synthetic References near an intended performance boundary. It does not assert an awarded IELTS band.
_Avoid_: True band, official band

**Criterion Profile**:
The intended whole-band-like performance anchor for one of TR/TA, CC, LR, or GRA in a Synthetic Reference.
_Avoid_: Criterion score, awarded score

**Borderline Profile**:
A combination of adjacent whole-band Criterion Profiles used to model a half-band corpus label without inventing an official half-band descriptor.
_Avoid_: Half-band descriptor

**Rubric Schema**:
The reviewed, versioned, structured representation of the project’s IELTS Writing assessment criteria and source provenance. It is the highest authority for generation and verification constraints.
_Avoid_: Prompt knowledge, model intuition

**Task-Scoped Rubric Package**:
A complete rubric version whose identity, pin, criteria, source coordinates, status,
and hashes belong to one writing task. Shared criterion definitions do not permit one
task to import another task's band-descriptor artifacts.
_Avoid_: Cross-task criterion shortcut, shared descriptor table

**Structured Rubric Snapshot**:
A deeply immutable, validated runtime value containing a pinned rubric identity,
approved provenance metadata, legal/review status, criterion definitions, exact
whole-band official claims, and separately labelled internal interpretations. It is
created only after every package and authority gate passes.
_Avoid_: Prompt rubric, mutable rubric document, model-memory descriptor

**Rubric Pin**:
A code-owned allow record that binds one rubric version to exact JSON/schema asset
hashes, approved source provenance hashes, required status gates, and one Runtime
Content Hash. A pin authorises no new descriptor wording by itself.
_Avoid_: Current pointer, source PDF path, cache key

**Runtime Content Hash**:
The SHA-256 identity of the canonical, domain-specific runtime semantics: rubric
identity, approved authority source identity/classification, legal status, criterion
definitions, official whole-band claims, and `DERIVED_INTERNAL` interpretations. It is
the only rubric hash allowed in Provider identity, cache identity, traces, and results.
_Avoid_: Asset byte hash, PDF hash, local path hash

**Source Provenance Hash**:
The SHA-256 of an approved official source PDF, retained only as provenance metadata
and used by an optional offline verification boundary. The PDF and its local path are
not runtime dependencies and never enter Provider, cache, trace, or result identity.
_Avoid_: Runtime content identity, package integrity hash

**Official Rubric Source**:
An IELTS-published source explicitly approved for the versioned Rubric Schema. Only this source class may contribute wording or rules to `OFFICIAL_RUBRIC`.
_Avoid_: Helpful source, calibration article

**Derived Internal Interpretation**:
A machine-readable mapping from exact official claims to dimensions or evidence expectations, always labelled `DERIVED_INTERNAL`. It is not official IELTS wording and may not add a scoring requirement.
_Avoid_: Official interpretation, examiner rule

**Private Research Only**:
A research source that may inform future offline analysis or research feature profiles but cannot enter the Rubric Schema or become a direct band rule.
_Avoid_: Rubric evidence, scoring authority

**Criterion Card**:
A sample-specific account of the observed TR/TA, CC, LR, or GRA characteristics in one Synthetic Reference.
_Avoid_: Rubric excerpt, official examiner comment

**Production Candidate**:
A Synthetic Reference that has passed automated generation and verification gates but may still require human spot review before production retrieval.
_Avoid_: Approved sample, ground truth

**Mutation Sample**:
A Synthetic Reference deliberately altered to test whether a scorer reacts to a controlled criterion change. It never enters the production reference corpus.
_Avoid_: Training sample, production reference

**Metric Definition**:
A versioned statement of one evaluation measure's unit, denominator, aggregation rule, and evidence boundary. It records observed system behavior without asserting writing quality or human agreement.
_Avoid_: Quality claim, examiner metric

**Threshold Policy**:
A versioned, provenance-bound decision policy that states whether a Metric Definition is required, deferred, or not applicable and, when established, its comparison threshold.
_Avoid_: Implicit pass bar, zero-default threshold

**Phase Gate Decision**:
An immutable ACCEPT, REJECT, or NEEDS_REVISION decision over validated metric evidence and one Threshold Policy. A Phase Gate Decision states its claim scope and cannot substitute code completion for evidence.
_Avoid_: CODE_DONE, unsupported release claim

**Submission Snapshot**:
An immutable, content-addressed intake projection binding one task type, exact question content, and one Essay Version. It is not a mutable UI request or a user/event identity.
_Avoid_: Editable submission, user record

**Essay Version**:
An immutable, content-addressed version of a Candidate Script with a versioned locator manifest. Its exact original text remains the evidence authority.
_Avoid_: Normalized essay, rewritten evidence

**Original-Text Locator**:
A zero-based, half-open Unicode code-point range into exact original submitted text. It never points into a normalized or rewritten copy.
_Avoid_: Byte offset, normalized-text position

**Task 2 Understanding**:
A versioned, non-scoring artifact that records Task 2 requirements and structural learner-state observations against a Submission Snapshot. It may gate only unresolved task-definition or artifact-integrity prerequisites; learner weakness or ambiguity remains scoreable.
_Avoid_: Criterion assessment, confidence gate, awarded band

**Task Requirement**:
One explicit action required by a Task 2 prompt, anchored to an Original-Text Locator in that prompt.
_Avoid_: Inferred score rule, generic essay type label

**Evidence Span**:
One exact, quote-verified zero-based half-open Unicode code-point range within the current Essay Version, bound to its containing paragraph and sentence.
_Avoid_: Approximate match, normalized-text excerpt

**Student Evidence Observation**:
A non-scoring, criterion-specific statement about the current Candidate Script, supported by one or more Evidence Spans or by an explicit absence/global scope.
_Avoid_: Band rationale, generic all-criterion claim

**Observation Scope**:
The declared evidence form of a Student Evidence Observation: exact spans, a whole current paragraph, an explicit absence, or an explicit global claim.
_Avoid_: Empty quote, implied missing evidence

**Criterion Assessment**:
A versioned, validated Task 2 result for exactly one of TR, CC, LR, or GRA. It
contains either a supported Candidate Band Range and Assessment Confidence or a
typed unassessable result, and never asserts an overall score.
_Avoid_: Overall assessment, coaching diagnosis, Provider-authored score identity

**Assessment Finding**:
A structured link between official rubric claims and validated, same-criterion
Student Evidence Observation and Evidence Span identifiers. Its stable identity is
assigned by code after validation and canonical ordering.
_Avoid_: Free-text rationale, invented quote, unvalidated essay observation

**Candidate Band Range**:
An exact whole-band anchor or two adjacent whole-band anchors supported for one
criterion. Its midpoint is code-derived; non-adjacent uncertainty is not narrowed
into a Candidate Band Range.
_Avoid_: Official half-band descriptor, silently narrowed uncertainty

**Assessment Confidence**:
A structured level and reason set describing confidence that the true criterion
level lies inside one Candidate Band Range. It is not an empirical probability,
examiner-agreement measure, or range-width alias.
_Avoid_: Accuracy probability, calibration claim, automatic range penalty

**Criterion Assessment Bundle**:
A code-owned, content-addressed collection containing exactly one validated,
assessed Criterion Assessment for each of TR, CC, LR, and GRA. It contains no
overall score and is never created for an incomplete run.
_Avoid_: Partial bundle, promoted score report, overall-band result

**Score Review Decision**:
A content-addressed publication decision over one complete Criterion Assessment
Bundle and any comparable repeat or critical conflict evidence. A review-required
decision carries precise typed reasons and cannot publish a score.
_Avoid_: Confidence note, favorable retry selection, automatic averaging

**Locked Score Snapshot**:
An immutable, content-addressed assessment result created by code only after the
Score Review Decision passes. It contains the four criterion scores, deterministic
overall and range, confidence, rubric lineage, and calculation-policy identity.
_Avoid_: Provider score payload, editable report score, target-band estimate

**Core Diagnosis**:
A compact, evidence-linked account of the strongest criterion, genuine
bottlenecks, keep items, and next actions derived after a Locked Score Snapshot.
It may be shorter than three items when reliable evidence is sparse.
_Avoid_: Full coaching report, manufactured problem list, score adjustment

**Full Coaching**:
A bounded, evidence-linked post-score learning artifact that expands a Core
Diagnosis without changing its Locked Score lineage. Unsupported or sparse needs
are omitted or represented as partial rather than filled with generic advice.
_Avoid_: Score rationale, unlimited feedback, model-generated problem list

**Mode A Report Document**:
A pure, versioned presentation contract that copies Locked Score fields and
orders validated Full Coaching sections with localized estimated-band language.
Review-required documents publish no score projection.
_Avoid_: Score calculator, official result, PDF layout

**Revision Ledger**:
An immutable, locator-aware comparison of two Essay Versions whose Student
Changes and alignment flags are evidence rather than judgments of improvement.
_Avoid_: Revised score, edit-quality result, mutable draft history

**Guided Hint Session**:
A per-issue monotonic assistance record that reveals location, category,
guidance, and an optional reference in four explicit levels.
_Avoid_: Automatic rewrite, untracked help, mastery evidence

**Revision Classification**:
An evidence-linked judgment over a Revision Ledger that distinguishes real
improvement, cosmetic change, unchanged issues, new errors, over-editing, and
regression while keeping uncertain cases reviewable.
_Avoid_: Score movement, edit-size heuristic, mastery update

**Revision Verification Summary**:
A deterministic aggregation of validated Revision Classifications with explicit
metric numerators, denominators, and not-applicable states.
_Avoid_: Revised band, hidden-denominator success rate, course grade

**Topic/Language Knowledge Asset**:
A versioned coaching-only item whose source, rights, owner, tenant, naturalness,
and source-artifact lineage determine whether it may be used.
_Avoid_: Scoring reference, provenance-free vocabulary, public corpus assumption

**Topic Learning Pack**:
A bounded, possibly empty selection of allowed Topic/Language Knowledge Assets
matched to verified current Candidate Script needs with their evidence lineage.
_Avoid_: Advanced-vocabulary filler, mastery record, scoring context

**Minimal Edit Plan**:
An evidence-linked set of `KEEP`, `FIX`, `DEVELOP`, `DIVERSIFY`, `REMOVE`, or
`RESTRUCTURE` operations that preserves the learner's facts, position, intent,
style, and useful language before adding complexity.
_Avoid_: Whole-script rewrite, target-band imitation, automatic improvement

**Learning Event**:
An immutable, append-only record of learner behavior with essay, revision,
evidence, topic, correctness, assistance, owner, and tenant lineage. Correction
and deletion are later events rather than mutation of raw history.
_Avoid_: Editable mastery row, model suggestion, score prior

**Mastery Projection**:
A deterministic rebuild of one learner skill through exposure, guided use,
independent use, cross-topic transfer, and stable mastery using only eligible
Learning Events under a versioned policy.
_Avoid_: AI confidence, historical band, permanent learner label

**Memory-Aware Recommendation**:
A post-score practice priority that cites current Candidate Script evidence and,
when available, a Mastery Projection; current evidence always outranks memory.
_Avoid_: Score adjustment, automated curriculum, opaque personalization

**C2 Rollback Decision**:
A content-addressed feature state that disables one learning layer and all later
dependent layers while preserving accepted Locked Score and assessment artifacts.
_Avoid_: Data rollback, score restoration, arbitrary feature combination

**Task 1 Image Snapshot**:
An immutable, content-addressed identity for one Academic Task 1 question and its
validated source image. It retains log-safe media metadata while the original
pixels remain confined to authorized vision boundaries.
_Avoid_: Image path, mutable upload, chart summary

**ChartFacts**:
A versioned, image-bound account of the visible chart type, units, categories,
time points, values, trends, comparisons, process steps, or map features. It is
independent of the Candidate Script and assigns no score.
_Avoid_: Student claim, TA assessment, plausible chart reconstruction

**Reconciled Chart Facts**:
The independently confirmed subset of two ChartFacts observations for the same
Task 1 Image Snapshot. A critical discrepancy produces `TASK1_FACT_CONFLICT`
instead of a complete artifact.
_Avoid_: Majority vote, extractor correction, unverified chart summary

**Student Chart Claim**:
A material factual assertion from the current Task 1 Candidate Script, bound to
an exact Original-Text Locator and classified as verified, inaccurate, or
ambiguous against Reconciled Chart Facts.
_Avoid_: Grammar finding, inferred claim, chart fact

**Task 1 Criterion Assessment**:
A validated TA, CC, LR, or GRA result whose Task Achievement evidence comes from
Reconciled Chart Facts and Student Chart Claims, while language criteria remain
bound to the current Candidate Script. It never asserts an overall score.
_Avoid_: Image-to-score response, factual-language blend, Provider overall

**Authorized Frozen Package**:
A capability created only after configured-path, control-document, declared-use,
target-environment, and rights checks pass for one immutable external retrieval
release. Without it, runtime artifacts may not be opened.
_Avoid_: Corpus directory, valid-looking manifest, inferred permission

**Validated Frozen Package**:
A read-only runtime identity whose declared bytes, 729-object set, source linkage,
dense and BM25 mappings, and pinned embedding contract all match after an
Authorized Frozen Package exists. Validation never repairs or rebuilds it.
_Avoid_: Copied corpus, mutable index, synthetic contract fixture

**Retrieval Evidence Pack**:
An ordered, content-addressed result from frozen Dense Top20 and BM25 Top20 merged
by RRF k=60 to at most five items. Criterion routing is advisory and every item has
`DIRECT_SCORE_AUTHORITY=false`.
_Avoid_: Scoring context, similar-band precedent, reranked examples

**Local Private Research Authorization**:
An explicit operator grant to use an eligible private research release for local,
post-score empirical evidence and coaching without changing its rights status.
It never grants Public Safe Authorization or commercial permission.
_Avoid_: Public-safe package, commercial rights, scoring authority

**Public Safe Authorization**:
A separate rights decision permitting the declared public product use and release
boundary; private research authorization cannot satisfy or imply it.
_Avoid_: Local consent, technical validation, repository code license

**RAG Evidence Gate**:
A deterministic decision that classifies a Retrieval Evidence Pack as sufficient,
insufficient, or conflicting against rights, provenance, Official Rubric, and
current Student Evidence. Only a sufficient consumer-safe projection is exposed.
_Avoid_: Similarity threshold score, conflict reconciliation, band transfer

**RAG Promotion Decision**:
A target-environment-specific, content-addressed choice of promoted, available but
not promoted, or disabled. Rights and failed critical gates are non-compensable,
and rollback always restores independent rubric-only scoring.
_Avoid_: Retrieval benchmark result, package availability, global permission

**Application Service Boundary**:
A presentation-independent use-case layer through which desktop and server
adapters submit work, read state, and obtain accepted domain artifacts. It cannot
upgrade incomplete domain outcomes or mutate locked identities.
_Avoid_: UI worker logic, HTTP-owned domain behavior

**Tenant Principal**:
An authenticated user identity bound to exactly one tenant and one versioned role.
Repository access fails closed when tenant or owner context is missing, and a
guessed cross-tenant identifier is indistinguishable from an absent resource.
_Avoid_: Client-supplied owner ID, global user context

**Submission Intent**:
One owner-scoped, content-bound idempotency identity for a requested assessment.
Repeated delivery of the same intent returns the same Submission record; reuse for
different semantic input is a conflict.
_Avoid_: Retry counter, mutable draft, job identity

**Durable Platform Job**:
A persisted queue/lease/retry/cancellation record that drives processing without
being the assessment result. Expired leases may be recovered, but job success
cannot fabricate or replace an accepted domain result.
_Avoid_: WorkflowResult, report status, background thread

**Privacy Receipt**:
A content-addressed, data-minimised record proving that a consent, export,
retention, or deletion action occurred under one policy version. It is not the
underlying Candidate Script or a substitute for complete data-rights execution.
_Avoid_: Raw audit log, analytics event, consent checkbox

**Community Release Candidate**:
A deterministic artifact built only from explicitly public-safe roots after
denied-path and leak scanning. It contains no private RAG objects or identifiers
and is preparation evidence, not a published release.
_Avoid_: Full working tree archive, private-package-disabled copy, public launch
