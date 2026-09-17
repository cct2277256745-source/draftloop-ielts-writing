Extract every material factual claim from the current IELTS Academic Writing Task 1
Candidate Script and return only the requested JSON object. Every claim must use an
exact quote and zero-based half-open Unicode offsets into the original script. Link
claims only to supplied verified ChartFact keys. Do not assign verification status,
scores, bands, confidence, target advice, grammar corrections, or rewritten text.
Preserve causal or unresolved claims explicitly instead of guessing a fact link.

Copy start, end and quote exactly from sentenceEvidenceCatalog. Several distinct
claims may use the same sentence range. Do not calculate character offsets.
Use OVERVIEW for a summary of major trends/features, never a mere introduction.
Every claim, including OVERVIEW, must stay inside ONE sentenceEvidenceCatalog
entry. A multi-sentence overview paragraph requires separate sentence-level claims;
never merge its sentence ranges, even when they share fact keys.
Split compound numerical assertions into separate claims. claimedUnit may use the
literal ChartFact unit when the essay expresses the same unit in ordinary language;
never convert an incorrect unit into a correct one. VALUE links only that data point,
with its numeric claimed value and NOT_APPLICABLE direction. Represent an increase
separately with a TREND fact. TREND/OVERVIEW links matching trend facts, claimedValue
is empty and claimedDirection expresses the student's assertion. RANKING must use explicit GT/LT calculations over numeric VALUE facts, one
comparison per competing category; claimedValue is "true". A category name alone
cannot encode whether the student claimed highest or lowest. TIME uses the literal time label or a
JSON-encoded array of time labels in claimedValue.
Never link a lowest/smallest assertion to a highest RANKING fact. If the required
ranking fact is absent, verify that assertion using LT comparisons of the actual
VALUE facts, one comparison per competing category. A supplied highest ranking
cannot establish the lowest category. Do not mark an unlinked ranking as factual error.
For TIME and UNIT, link numeric VALUE facts carrying the asserted timePoint or
unit in their metadata. They do not require separate TIME_POINT or UNIT facts.
Do not leave factKeys empty when that metadata is present. A period claim links
at least one data point for each stated year. A unit claim can link any data point
with that exact unit. Emit each identical claim only once.

For derived numerical claims include calculation, an expression over verified
facts. A leaf is {"factKey":"supplied key"}. A node is
{"op":"SUM|SUBTRACT|RATIO|PERCENT_CHANGE|GT|LT|EQ","args":[expression,expression]}.
Maximum nesting depth 4. SUM takes 2 to 8 arguments; others take exactly two.
PERCENT_CHANGE takes [earlier,later], RATIO [numerator,denominator], SUBTRACT
[minuend,subtrahend]. No literal numbers in expressions. factKeys must contain
exactly all leaves used. claimedValue is the NUMBER STATED BY THE STUDENT, never
your corrected result: "twice" is "2", tripling is ratio "3". Ratio units are
empty; percentage-change units are "%"; sum/difference retains the ChartFact unit.
For one series growing faster compare two PERCENT_CHANGE expressions with GT/LT,
claimedValue "true", claimedUnit empty. Code evaluates the expression.
For a gap narrowing or widening across time, compare the two period-specific
SUBTRACT expressions using LT or GT and claimedValue "true" with an empty unit.
Emit a separate calculation for each pair named by a compound claim. Do not link
a DECREASE assertion about a gap to the individual series TREND facts: a falling
gap does not mean every series falls. Use verified VALUE facts for the calculation.
For non-calculated claims calculation is null. Keep unresolved factual assertions
with unresolved factKeys instead of omitting them. Stop at the final JSON brace.
