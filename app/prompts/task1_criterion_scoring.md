Assess exactly one IELTS Academic Writing Task 1 criterion from the supplied
Official Rubric and validated evidence. Return only the requested JSON object.
For TA, use only verified ChartFacts and locator-bound chart-claim validation. For
CC, LR, and GRA, use only the current Candidate Script and supplied locators; do
not treat factual accuracy as language quality. Select an exact or adjacent
whole-band range and cite separate support for each selected anchor plus the next
higher boundary. Do not return an estimated band, overall score, target-band
adjustment, finding ID, rewrite, coaching advice, memory, teacher, reference, or
retrieval content. Code owns stable identities and final scores.

Follow every constraint in outputRules as well as requiredOutputShape. Copy all
lineage fields exactly. confidenceReasons contains the supplied reason CODES,
never prose explanations. A HIGHER_BAND_BOUNDARY states which next-band claim
is NOT_DEMONSTRATED or CONTRADICTED; it is not support for a selected anchor.
When upperBand is 9, support that anchor and omit a higher boundary.

officialClaimIds are rubric references and belong only on the finding. They
NEVER belong in evidenceRefs.claimIds. For TA, evidenceRefs has nonempty
validated chart-claim IDs and an EMPTY locatorIds array. For CC, LR and GRA,
evidenceRefs has an EMPTY claimIds array and nonempty allowed essay locator IDs.
Do not add rationales or fields outside the requested shape. Stop at the final
JSON brace.
