"""Locator-bound Task 1 student claims validated against reconciled ChartFacts."""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
import hashlib
import json
import logging
import math
from pathlib import Path
from typing import Any, Mapping

from .providers import ProviderCallRequest, ProviderCallResult, ProviderTransport, RouteResolution
from .submission import EssayVersion, digest
from .task1_facts import (
    ChartFact,
    ReconciledChartFacts,
    ReconciliationStatus,
    Task1ImageSnapshot,
)


TASK1_SUBMISSION_VERSION = "task1-submission-snapshot-v1"
TASK1_CLAIM_SCHEMA_VERSION = "task1-chart-claims-output-v2"
TASK1_CLAIM_SEMANTIC_VERSION = "task1-chart-claim-validation-v3"
TASK1_CLAIM_REPAIR_POLICY_VERSION = "task1-chart-claim-repair-v2"
_PROMPT_PATH = Path(__file__).resolve().parents[1] / "prompts" / "task1_chart_claims.md"


class Task1ClaimError(ValueError):
    """Task 1 claim extraction or validation failed closed."""

    def __init__(self, message, provider_failure_code=None):
        super().__init__(message)
        self.provider_failure_code = provider_failure_code


class ClaimType(str, Enum):
    VALUE = "VALUE"
    UNIT = "UNIT"
    TIME = "TIME"
    TREND = "TREND"
    RANKING = "RANKING"
    PROPORTION = "PROPORTION"
    COMPARISON = "COMPARISON"
    OVERVIEW = "OVERVIEW"
    CAUSALITY = "CAUSALITY"


class ClaimStatus(str, Enum):
    VERIFIED = "VERIFIED"
    INACCURATE = "INACCURATE"
    AMBIGUOUS = "AMBIGUOUS"


class ClaimArtifactStatus(str, Enum):
    COMPLETE = "COMPLETE"
    REVIEW_REQUIRED = "REVIEW_REQUIRED"


class OverviewCoverage(str, Enum):
    PRESENT = "PRESENT"
    MISSING = "MISSING"
    AMBIGUOUS = "AMBIGUOUS"


@dataclass(frozen=True)
class Task1SubmissionSnapshot:
    task_type: str
    question_content_sha256: str
    image_snapshot_sha256: str
    essay_version: EssayVersion = field(repr=False, compare=False)
    snapshot_sha256: str = ""
    version: str = TASK1_SUBMISSION_VERSION
    question: str = field(default="", repr=False, compare=False)

    @classmethod
    def create(cls, source: Task1ImageSnapshot, essay: str) -> "Task1SubmissionSnapshot":
        version = EssayVersion.create(essay)
        payload = {
            "version": TASK1_SUBMISSION_VERSION,
            "taskType": "task1",
            "questionContentSha256": source.question_content_sha256,
            "imageSnapshotSha256": source.snapshot_sha256,
            "essayVersionId": version.essay_version_id,
            "locatorManifestSha256": version.locator_manifest_sha256,
        }
        return cls(
            "task1",
            source.question_content_sha256,
            source.snapshot_sha256,
            version,
            digest(payload),
            question=source.question,
        )

    def identity(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "taskType": self.task_type,
            "questionContentSha256": self.question_content_sha256,
            "imageSnapshotSha256": self.image_snapshot_sha256,
            "essayVersionId": self.essay_version.essay_version_id,
            "locatorManifestSha256": self.essay_version.locator_manifest_sha256,
            "snapshotSha256": self.snapshot_sha256,
        }


@dataclass(frozen=True)
class StudentChartClaim:
    claim_id: str
    claim_type: ClaimType
    paragraph_id: str
    sentence_id: str
    start: int
    end: int
    fact_keys: tuple[str, ...]
    claimed_value: str
    claimed_unit: str
    claimed_direction: str
    material: bool
    status: ClaimStatus
    reasons: tuple[str, ...]
    calculation_json: str | None = None

    def content(self) -> dict[str, Any]:
        return {
            "claimId": self.claim_id,
            "claimType": self.claim_type.value,
            "paragraphId": self.paragraph_id,
            "sentenceId": self.sentence_id,
            "start": self.start,
            "end": self.end,
            "factKeys": list(self.fact_keys),
            "claimedValue": self.claimed_value,
            "claimedUnit": self.claimed_unit,
            "claimedDirection": self.claimed_direction,
            "material": self.material,
            "status": self.status.value,
            "reasons": list(self.reasons),
            "calculation": json.loads(self.calculation_json) if self.calculation_json else None,
        }


@dataclass(frozen=True)
class ClaimValidationArtifact:
    status: ClaimArtifactStatus
    task1_submission_sha256: str
    reconciled_facts_sha256: str
    claims: tuple[StudentChartClaim, ...]
    overview_coverage: OverviewCoverage
    provider_id: str
    model_id: str
    prompt_version: str
    schema_version: str
    semantic_version: str
    artifact_sha256: str

    def content(self, *, include_hash: bool = True) -> dict[str, Any]:
        value = {
            "status": self.status.value,
            "task1SubmissionSha256": self.task1_submission_sha256,
            "reconciledFactsSha256": self.reconciled_facts_sha256,
            "claims": [item.content() for item in self.claims],
            "overviewCoverage": self.overview_coverage.value,
            "providerId": self.provider_id,
            "modelId": self.model_id,
            "promptVersion": self.prompt_version,
            "schemaVersion": self.schema_version,
            "semanticVersion": self.semantic_version,
        }
        if include_hash:
            value["artifactSha256"] = self.artifact_sha256
        return value


_ROOT_KEYS = frozenset({"task1SubmissionSha256", "reconciledFactsSha256", "claims"})
_CLAIM_KEYS = frozenset({
    "claimType", "start", "end", "quote", "factKeys", "claimedValue",
    "claimedUnit", "claimedDirection", "material",
})
_DIRECTIONS = frozenset({"INCREASE", "DECREASE", "STABLE", "MIXED", "NOT_APPLICABLE", ""})


def _normal(value: Any) -> str:
    return " ".join(str(value).strip().split())


def _number(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        number = float(str(value).replace(",", "").strip())
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _locator(snapshot: Task1SubmissionSnapshot, start: int, end: int, quote: str) -> tuple[str, str]:
    essay = snapshot.essay_version
    if (
        isinstance(start, bool)
        or isinstance(end, bool)
        or not isinstance(start, int)
        or not isinstance(end, int)
        or start < 0
        or end <= start
        or end > len(essay.original_text)
        or essay.original_text[start:end] != quote
    ):
        raise Task1ClaimError("Task 1 chart claim does not round-trip to original text.")
    for paragraph in essay.paragraphs:
        for sentence in paragraph.sentences:
            if start >= sentence.start and end <= sentence.end:
                return paragraph.locator.locator_id, sentence.locator_id
    raise Task1ClaimError("Task 1 chart claim crosses a sentence or paragraph boundary.")


def _unit_key(value):
    unit=_normal(value).casefold()
    # Axis labels often include the quantity name; the parenthesized percent
    # symbol identifies the unit. Percentage points are not percentage change.
    return '%' if unit in {'%','percent','percentage','per cent'} or unit.endswith('(%)') else unit


def _comparison_status(
    raw: Mapping[str, Any],
    facts: Mapping[str, ChartFact],
) -> tuple[ClaimStatus, tuple[str, ...]]:
    keys = tuple(sorted(set(str(key) for key in raw["factKeys"])))
    matched = [facts[key] for key in keys if key in facts]
    if not keys or len(matched) != len(keys):
        return ClaimStatus.AMBIGUOUS, ("FACT_LINK_UNRESOLVED",)
    if raw["claimType"] == ClaimType.CAUSALITY.value:
        return ClaimStatus.AMBIGUOUS, ("CAUSALITY_NOT_CHART_VERIFIABLE",)
    if raw["claimType"] == ClaimType.RANKING.value and raw.get("calculation") is None:
        # A category name alone cannot distinguish highest from lowest. This is
        # an extractor binding defect, not evidence of a student factual error.
        raise Task1ClaimError("RANKING requires explicit GT/LT comparisons of numeric VALUE facts; emit one comparison per competing category, retaining the student's highest/lowest assertion.")
    claimed_value = _normal(raw["claimedValue"])
    claimed_unit = _normal(raw["claimedUnit"])
    claimed_direction = _normal(raw["claimedDirection"]).upper()
    if raw.get("calculation") is not None:
        try:
            expected, unit, used = _calculate(raw["calculation"], facts)
        except Task1ClaimError:
            return ClaimStatus.AMBIGUOUS, ("CALCULATION_UNRESOLVED",)
        if used != set(keys):
            return ClaimStatus.AMBIGUOUS, ("CALCULATION_FACT_LINK_MISMATCH",)
        if _unit_key(claimed_unit) != _unit_key(unit):
            return ClaimStatus.INACCURATE, ("UNIT_MISMATCH",)
        if isinstance(expected, bool):
            agrees = claimed_value.casefold() == str(expected).lower()
        else:
            actual = _number(claimed_value)
            if actual is None:
                return ClaimStatus.AMBIGUOUS, ("CALCULATION_VALUE_MISSING",)
            tolerance = max(0.01 if not unit or unit == "%" else 0.5, abs(expected) * 0.01)
            agrees = abs(actual - expected) <= tolerance
        return (ClaimStatus.VERIFIED, ("CALCULATED_FACTS_MATCH",)) if agrees else (ClaimStatus.INACCURATE, ("VALUE_MISMATCH",))
    if raw["claimType"] == ClaimType.TIME.value:
        try:
            stated = json.loads(claimed_value) if claimed_value.startswith("[") else [claimed_value]
            if not isinstance(stated, list) or not all(isinstance(x, str) or type(x) is int for x in stated):
                raise ValueError()
            agrees = {str(x) for x in stated} == {fact.time_point for fact in matched if fact.time_point}
            return (ClaimStatus.VERIFIED, ("TIME_POINTS_MATCH",)) if agrees else (ClaimStatus.INACCURATE, ("TIME_MISMATCH",))
        except ValueError:
            return ClaimStatus.AMBIGUOUS, ("TIME_UNRESOLVED",)
    if raw["claimType"] in {ClaimType.COMPARISON.value, ClaimType.PROPORTION.value} and not claimed_value:
        return ClaimStatus.AMBIGUOUS, ("COMPARISON_VALUE_MISSING",)
    inaccurate: set[str] = set()
    ambiguous: set[str] = set()
    for fact in matched:
        if claimed_unit:
            if _unit_key(claimed_unit) != _unit_key(fact.unit):
                inaccurate.add("UNIT_MISMATCH")
        elif fact.unit and raw["claimType"] in {ClaimType.VALUE.value, ClaimType.UNIT.value, ClaimType.PROPORTION.value}:
            ambiguous.add("UNIT_OMITTED")
        if claimed_direction and claimed_direction != "NOT_APPLICABLE":
            if claimed_direction != fact.direction:
                inaccurate.add("DIRECTION_MISMATCH")
        if claimed_value:
            left, right = _number(claimed_value), _number(fact.value)
            if left is not None and right is not None:
                tolerance = max(0.5, 0.01 * max(abs(left), abs(right)))
                if abs(left - right) > tolerance:
                    inaccurate.add("VALUE_MISMATCH")
            elif claimed_value.casefold() != fact.value.casefold():
                inaccurate.add("VALUE_MISMATCH")
    if inaccurate:
        return ClaimStatus.INACCURATE, tuple(sorted(inaccurate))
    if ambiguous:
        return ClaimStatus.AMBIGUOUS, tuple(sorted(ambiguous))
    return ClaimStatus.VERIFIED, ("FACTS_MATCH",)


def _calculate(expression, facts, depth=0):
    """Evaluate a bounded arithmetic tree over verified numeric facts, never code."""
    if depth > 4 or not isinstance(expression, Mapping):
        raise Task1ClaimError("Calculation must be a bounded fact expression.")
    if set(expression) == {"factKey"}:
        key = expression["factKey"]
        fact = facts.get(key) if isinstance(key, str) else None
        number = _number(fact.value) if fact else None
        if fact is None or number is None:
            raise Task1ClaimError("Calculation requires a verified numeric fact.")
        return number, fact.unit, {key}
    if set(expression) != {"op", "args"} or not isinstance(expression["args"], list):
        raise Task1ClaimError("Calculation fields are invalid.")
    op, args = expression["op"], expression["args"]
    if not isinstance(op, str) or op not in {"SUM", "SUBTRACT", "RATIO", "PERCENT_CHANGE", "GT", "LT", "EQ"} or not (2 <= len(args) <= 8 if op == "SUM" else len(args) == 2):
        raise Task1ClaimError("Calculation operation is invalid.")
    operands = [_calculate(item, facts, depth + 1) for item in args]
    values = [item[0] for item in operands]
    if any(isinstance(item, bool) for item in values) or len({_unit_key(item[1]) for item in operands}) != 1:
        raise Task1ClaimError("Calculation operands have incompatible units.")
    left, right = values[:2]
    unit = operands[0][1]
    if op == "SUM": result = sum(values)
    elif op == "SUBTRACT": result = left - right
    elif op == "RATIO":
        if right == 0: raise Task1ClaimError("Calculation denominator is zero.")
        result, unit = left / right, ""
    elif op == "PERCENT_CHANGE":
        if left == 0: raise Task1ClaimError("Calculation baseline is zero.")
        result, unit = (right - left) / abs(left) * 100, "%"
    else:
        result = left > right if op == "GT" else left < right if op == "LT" else math.isclose(left, right, rel_tol=1e-9, abs_tol=1e-9)
        unit = ""
    if not math.isfinite(result): raise Task1ClaimError("Calculation result must be finite.")
    return result, unit, set().union(*(item[2] for item in operands))


def validate_claim_output(
    value: Mapping[str, Any],
    snapshot: Task1SubmissionSnapshot,
    reconciled: ReconciledChartFacts,
    *,
    provider_id: str,
    model_id: str,
    prompt_version: str,
) -> ClaimValidationArtifact:
    if not isinstance(value, Mapping) or set(value) != _ROOT_KEYS:
        raise Task1ClaimError("Task 1 chart claim output fields are invalid.")
    if (
        value["task1SubmissionSha256"] != snapshot.snapshot_sha256
        or value["reconciledFactsSha256"] != reconciled.artifact_sha256
        or reconciled.status is not ReconciliationStatus.VERIFIED
    ):
        raise Task1ClaimError("Task 1 claim lineage or verified facts are invalid.")
    raw_claims = value["claims"]
    if not isinstance(raw_claims, list) or any(not isinstance(item, Mapping) for item in raw_claims):
        raise Task1ClaimError("Task 1 chart claims are invalid.")
    facts = {item.fact_key: item for item in reconciled.facts}
    claims: list[StudentChartClaim] = []
    locators: set[str] = set()
    for index, raw in enumerate(raw_claims):
        if set(raw) not in (_CLAIM_KEYS, _CLAIM_KEYS | {"calculation"}):
            missing = ", ".join(sorted(_CLAIM_KEYS - set(raw))) or "none"
            extra_count = len(set(raw) - _CLAIM_KEYS - {"calculation"})
            raise Task1ClaimError(f"Task 1 chart claim fields are invalid at claims[{index}]: missing {missing}; unexpected field count {extra_count}. Include all required fields even when empty; use claimedUnit=\"\" for unitless comparisons. Check every claim for the same omission.")
        try:
            claim_type = ClaimType(raw["claimType"])
        except (TypeError, ValueError) as exc:
            raise Task1ClaimError("Task 1 chart claim type is invalid.") from exc
        if (
            not isinstance(raw["quote"], str)
            or not isinstance(raw["factKeys"], list)
            or any(not isinstance(key, str) for key in raw["factKeys"])
            or not isinstance(raw["material"], bool)
        ):
            raise Task1ClaimError("Task 1 chart claim evidence is invalid.")
        direction = _normal(raw["claimedDirection"]).upper()
        if direction not in _DIRECTIONS:
            raise Task1ClaimError("Task 1 chart claim direction is invalid.")
        paragraph_id, sentence_id = _locator(
            snapshot, raw["start"], raw["end"], raw["quote"]
        )
        marker = digest({key: raw[key] for key in sorted(raw) if key != "material"})
        if marker in locators:
            raise Task1ClaimError("Task 1 chart claims are duplicated.")
        locators.add(marker)
        status, reasons = _comparison_status(raw, facts)
        identity = {
            "claimType": claim_type.value,
            "start": raw["start"],
            "end": raw["end"],
            "factKeys": sorted(set(raw["factKeys"])),
            "claimedValue": _normal(raw["claimedValue"]),
            "claimedUnit": _normal(raw["claimedUnit"]),
            "claimedDirection": direction,
            "calculation": raw.get("calculation"),
        }
        claims.append(StudentChartClaim(
            claim_id="chart-claim:" + digest(identity),
            claim_type=claim_type,
            paragraph_id=paragraph_id,
            sentence_id=sentence_id,
            start=raw["start"],
            end=raw["end"],
            fact_keys=tuple(sorted(set(raw["factKeys"]))),
            claimed_value=identity["claimedValue"],
            claimed_unit=identity["claimedUnit"],
            claimed_direction=direction,
            material=raw["material"],
            status=status,
            reasons=reasons,
            calculation_json=json.dumps(raw["calculation"], sort_keys=True) if raw.get("calculation") is not None else None,
        ))
    claims.sort(key=lambda item: (item.start, item.end, item.claim_type.value))
    overview_claims = [item for item in claims if item.claim_type is ClaimType.OVERVIEW]
    if not overview_claims:
        overview = OverviewCoverage.MISSING
    elif any(item.status is ClaimStatus.VERIFIED for item in overview_claims):
        overview = OverviewCoverage.PRESENT
    else:
        overview = OverviewCoverage.AMBIGUOUS
    status = (
        ClaimArtifactStatus.REVIEW_REQUIRED
        if not claims or any(item.material and item.status is ClaimStatus.AMBIGUOUS for item in claims)
        else ClaimArtifactStatus.COMPLETE
    )
    partial = ClaimValidationArtifact(
        status=status,
        task1_submission_sha256=snapshot.snapshot_sha256,
        reconciled_facts_sha256=reconciled.artifact_sha256,
        claims=tuple(claims),
        overview_coverage=overview,
        provider_id=provider_id,
        model_id=model_id,
        prompt_version=prompt_version,
        schema_version=TASK1_CLAIM_SCHEMA_VERSION,
        semantic_version=TASK1_CLAIM_SEMANTIC_VERSION,
        artifact_sha256="",
    )
    return ClaimValidationArtifact(**{
        **partial.__dict__,
        "artifact_sha256": digest(partial.content(include_hash=False)),
    })


def _prompt() -> str:
    try:
        return _PROMPT_PATH.read_text(encoding="utf-8")
    except OSError as exc:
        raise Task1ClaimError("Task 1 chart claim prompt is unavailable.") from exc


def _prompt_version() -> str:
    return "sha256:" + hashlib.sha256(_prompt().encode("utf-8")).hexdigest()


class Task1ClaimService:
    def __init__(self, transport: ProviderTransport) -> None:
        self._transport = transport

    def run(
        self,
        snapshot: Task1SubmissionSnapshot,
        reconciled: ReconciledChartFacts,
        resolution: RouteResolution,
    ) -> ClaimValidationArtifact:
        contract = resolution.contract if isinstance(resolution, RouteResolution) else None
        if (
            contract is None
            or not resolution.ok
            or resolution.route.task_type != "task1"
            or resolution.route.stage != "chart_claim_extraction"
        ):
            raise Task1ClaimError("Task 1 chart claim route is unavailable.")
        payload = {
            "taskType": "task1",
            "task1Submission": snapshot.identity(),
            "candidateScript": snapshot.essay_version.original_text,
            "locatorManifest": snapshot.essay_version.locator_manifest(),
            "sentenceEvidenceCatalog": [
                {"start": sentence.start, "end": sentence.end,
                 "quote": snapshot.essay_version.original_text[sentence.start:sentence.end]}
                for paragraph in snapshot.essay_version.paragraphs for sentence in paragraph.sentences
            ],
            "reconciledFacts": reconciled.content(),
            "requiredOutputShape": {
                "task1SubmissionSha256": snapshot.snapshot_sha256,
                "reconciledFactsSha256": reconciled.artifact_sha256,
                "claims": [{
                    "claimType": "VALUE|UNIT|TIME|TREND|RANKING|PROPORTION|COMPARISON|OVERVIEW|CAUSALITY",
                    "start": 0,
                    "end": 1,
                    "quote": "",
                    "factKeys": [],
                    "claimedValue": "",
                    "claimedUnit": "",
                    "claimedDirection": "INCREASE|DECREASE|STABLE|MIXED|NOT_APPLICABLE|",
                    "material": True,
                    "calculation": None,
                }],
            },
        }
        prompt_version = _prompt_version()
        messages = [
            {"role": "system", "content": _prompt()},
            {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
        ]
        last_error: Exception | None = None
        previous = None
        for attempt in (1, 2):
            attempt_messages = list(messages)
            if attempt == 2:
                attempt_messages.append({"role": "assistant", "content": previous})
                attempt_messages.append({
                    "role": "user",
                    "content": "Return one corrected JSON object. Copy exact quotes and offsets from the sentenceEvidenceCatalog where possible. Do not add scores or advice. Validation rule: " + (str(last_error) if isinstance(last_error, Task1ClaimError) else "Return valid JSON without trailing prose."),
                })
            result = self._transport.call(contract, ProviderCallRequest(
                attempt_messages,
                "Task 1 chart claim extraction",
                stream=False,
                max_tokens=32000,
                # GLM 5.3 defaults to max reasoning; extraction does not need
                # max deliberation; high preserves reasoning for factual comparisons.
                reasoning_effort="high" if contract.model.model_id.lower() in {"glm-5.3", "glm-5.3-flash"} else None,
                require_json_object=True,
                validate_json_object=False,
            ))
            if not isinstance(result, ProviderCallResult) or not result.ok:
                raise Task1ClaimError("Task 1 chart claim Provider failed.",
                    result.failure.code.value if isinstance(result, ProviderCallResult) and result.failure else None)
            try:
                raw = json.loads(result.content)
                if not isinstance(raw, Mapping):
                    raise Task1ClaimError("Task 1 chart claim output is not an object.")
                # Report independent binding/schema defects together so the
                # single allowed repair does not chase only the first error.
                problems = []
                if isinstance(raw.get("claims"), list):
                    for index, item in enumerate(raw["claims"]):
                        try:
                            validate_claim_output({**raw, "claims": [item]}, snapshot, reconciled,
                                provider_id=contract.provider_id, model_id=contract.model.model_id,
                                prompt_version=prompt_version)
                        except Task1ClaimError as exc:
                            problems.append(f"claims[{index}]: {exc}")
                        if isinstance(item, Mapping) and item.get("claimType") == "RANKING" and item.get("calculation") is None:
                            problems.append(f"claims[{index}]: RANKING must have a GT/LT calculation of numeric VALUE facts, not a category-only ranking link.")
                    if problems:
                        raise Task1ClaimError("Repair all listed claim defects together: " + " | ".join(problems[:24]))
                return validate_claim_output(
                    raw,
                    snapshot,
                    reconciled,
                    provider_id=contract.provider_id,
                    model_id=contract.model.model_id,
                    prompt_version=prompt_version,
                )
            except (json.JSONDecodeError, Task1ClaimError) as exc:
                logging.getLogger(__name__).warning("chart_claim_validation attempt=%s rule=%s", attempt,
                    str(exc) if isinstance(exc, Task1ClaimError) else "INVALID_JSON")
                last_error = exc
                previous = result.content
        raise Task1ClaimError("Task 1 chart claim output remained invalid after repair.") from last_error
