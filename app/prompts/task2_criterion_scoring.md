# Task 2 criterion assessment

Return exactly one JSON object matching `requiredOutputSchema`. Do not return
markdown, commentary, an overall score, coaching, diagnosis, or a rewrite.

Assess only the `criterion` in the payload. Use the official claims in
`criterionRubric` as scoring authority. `derivedInternal` is optional
organization only and must not add a scoring requirement.

The complete Candidate Script is read-only semantic context. It is not an
alternate evidence-authority channel. Every student-evidence assertion used by
a finding must cite an observation and permitted span IDs supplied in
`studentEvidence`. Never invent a quote, offset, span, observation, or evidence.

Return an exact whole-band anchor or an adjacent whole-band range. If the
supportable uncertainty spans non-adjacent bands, return `UNASSESSABLE`; never
narrow it. For an assessed range, support every selected anchor separately and,
when the upper anchor is below 9, show why the next higher anchor is not met.

For each `ANCHOR_SUPPORT` finding, `claimFit` must be `DEMONSTRATED` or
`PARTIALLY_DEMONSTRATED`. For each `HIGHER_BAND_BOUNDARY`, cite the next
higher anchor and use `NOT_DEMONSTRATED` or `CONTRADICTED`: this finding
explains why that stronger claim is not met. If a stronger claim is partially
supported, describe the missing requirement in the rationale and cite the
validated observation showing that gap; do not label a boundary as demonstrated.

Confidence describes confidence that the true criterion band is inside the
declared range. It is not a probability. `ADJACENT_ANCHOR_MIX` describes range
shape and is not itself a limitation.

Contradiction hooks are structured signals only. Use a hook only when it
actually affects the assessment; merely receiving one must not change a score.

For `UNPROVABLE_SPECIAL_CONDITION`, observable related evidence may use only
`role: UNASSESSABLE_SUPPORT` with `claimFit: RELATED_EVIDENCE_ONLY`. It must not
claim the condition is demonstrated or partially demonstrated, and it must not
be used as band-range or higher-boundary support.

Do not return `estimatedBand` or `findingId`; those are assigned by code.

Copy every field in `lineage` exactly into the top-level output, preserving all
64-character hashes. Do not calculate or abbreviate them. A finding's
dimensionId may be null; when used, it must be a key in that exact band's
derivedInternal.bands[].dimensions.performance_dimensions and EVERY cited claim
must be listed under it. Do not combine claims from different dimensions under
one dimensionId. Use null when no single dimension fits all cited claims.

Stop immediately after the closing JSON brace. Do not append assessment notes,
comments, code fences, prose rationales, or explanations after the object.
