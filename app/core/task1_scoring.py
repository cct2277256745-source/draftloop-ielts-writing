"""Rubric-only Task 1 criterion scoring and image-to-locked-score orchestration."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from enum import Enum
import hashlib
import json
import logging
from pathlib import Path
from types import MappingProxyType
from typing import Any, Mapping, Sequence

from .assessment_finalization import (
    FinalizationBundle,
    LockedScoreSnapshot,
    ReviewGateDecision,
    ReviewReason,
    ReviewStatus,
    finalize_scores,
    make_review_decision,
    review_scores,
)
from .core_diagnosis import CoreDiagnosis, build_core_diagnosis
from .providers import ProviderCallRequest, ProviderCallResult, ProviderTransport, RouteResolution
from .rubric import StructuredRubricSnapshot, TASK1_CRITERIA, TASK1_EXPECTED_RUNTIME_HASH
from .submission import digest
from .task1_claims import (
    ClaimArtifactStatus,
    ClaimValidationArtifact,
    Task1ClaimError,
    Task1ClaimService,
    Task1SubmissionSnapshot,
)
from .task1_facts import (
    ChartFact,
    ReconciledChartFacts,
    ReconciliationStatus,
    Task1FactPipeline,
    Task1FactPipelineOutcome,
    Task1ImageSnapshot,
    reconcile_chart_facts,
)


TASK1_SCORING_SCHEMA_VERSION = "task1-criterion-assessment-output-v1"
TASK1_SCORING_SEMANTIC_VERSION = "task1-criterion-scoring-v1"
TASK1_SCORING_REPAIR_POLICY_VERSION = "task1-criterion-scoring-repair-v2"
TASK1_ASSESSMENT_BUNDLE_VERSION = "task1-criterion-assessment-bundle-v1"
_PROMPT_PATH = Path(__file__).resolve().parents[1] / "prompts" / "task1_criterion_scoring.md"
_LIMITATION_REASONS = frozenset({
    "EVIDENCE_COVERAGE_LIMITED",
    "WITHIN_CRITERION_TENSION",
    "BOUNDARY_DISTINCTION_LIMITED",
    "FACTUAL_ACCURACY_LIMITED",
    "OVERVIEW_COVERAGE_LIMITED",
})
_FITS_BY_ROLE = {
    "ANCHOR_SUPPORT": frozenset({"DEMONSTRATED", "PARTIALLY_DEMONSTRATED"}),
    "HIGHER_BAND_BOUNDARY": frozenset({"NOT_DEMONSTRATED", "CONTRADICTED"}),
    "UNASSESSABLE_SUPPORT": frozenset({"RELATED_EVIDENCE_ONLY"}),
}


class Task1ScoringError(ValueError):
    """Task 1 score input or output failed its authority contract."""


class Task1AssessmentStatus(str, Enum):
    ASSESSED = "ASSESSED"
    UNASSESSABLE = "UNASSESSABLE"


class Task1ScoringStatus(str, Enum):
    COMPLETE = "COMPLETE"
    INCOMPLETE = "INCOMPLETE"
    FAILED = "FAILED"


class Task1FoundationStatus(str, Enum):
    COMPLETE = "COMPLETE"
    REVIEW_REQUIRED = "REVIEW_REQUIRED"
    FAILED = "FAILED"


def _freeze(value: Any) -> Any:
    if isinstance(value, Mapping):
        return MappingProxyType({str(key): _freeze(child) for key, child in value.items()})
    if isinstance(value, (list, tuple)):
        return tuple(_freeze(child) for child in value)
    return value


def _thaw(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _thaw(child) for key, child in value.items()}
    if isinstance(value, tuple):
        return [_thaw(child) for child in value]
    return value


@dataclass(frozen=True)
class Task1ScoringExecutionIdentity:
    provider_id: str
    route_id: str
    model_id: str
    prompt_version: str
    schema_version: str
    semantic_version: str
    repair_policy_version: str
    rubric_id: str
    rubric_version: str
    runtime_content_sha256: str
    task1_submission_sha256: str
    image_snapshot_sha256: str
    reconciled_facts_sha256: str
    claim_validation_sha256: str

    def content(self) -> dict[str, Any]:
        return {
            "providerId": self.provider_id,
            "routeId": self.route_id,
            "modelId": self.model_id,
            "promptVersion": self.prompt_version,
            "schemaVersion": self.schema_version,
            "semanticVersion": self.semantic_version,
            "repairPolicyVersion": self.repair_policy_version,
            "rubricId": self.rubric_id,
            "rubricVersion": self.rubric_version,
            "runtimeContentSha256": self.runtime_content_sha256,
            "task1SubmissionSha256": self.task1_submission_sha256,
            "imageSnapshotSha256": self.image_snapshot_sha256,
            "reconciledFactsSha256": self.reconciled_facts_sha256,
            "claimValidationSha256": self.claim_validation_sha256,
        }


@dataclass(frozen=True)
class Task1CriterionAssessment:
    criterion: str
    status: Task1AssessmentStatus
    lower_band: int | None
    upper_band: int | None
    estimated_band: float | None
    confidence: str | None
    confidence_reasons: tuple[str, ...]
    unassessable_reasons: tuple[str, ...]
    findings: tuple[Mapping[str, Any], ...] = field(repr=False)
    used_contradiction_hooks: tuple[Mapping[str, Any], ...] = field(default=(), repr=False)
    authoritative_lineage: Mapping[str, str] = field(default_factory=dict, repr=False)
    execution_identity: Task1ScoringExecutionIdentity = field(default=None, repr=False)  # type: ignore[assignment]
    assessment_sha256: str = ""

    def content(self, *, include_hash: bool = True) -> dict[str, Any]:
        value = {
            "criterion": self.criterion,
            "status": self.status.value,
            "lowerBand": self.lower_band,
            "upperBand": self.upper_band,
            "estimatedBand": self.estimated_band,
            "confidence": self.confidence,
            "confidenceReasons": list(self.confidence_reasons),
            "unassessableReasons": list(self.unassessable_reasons),
            "findings": [_thaw(item) for item in self.findings],
            "usedContradictionHooks": [],
            "authoritativeLineage": dict(self.authoritative_lineage),
            "executionIdentity": self.execution_identity.content(),
        }
        if include_hash:
            value["assessmentSha256"] = self.assessment_sha256
        return value


@dataclass(frozen=True)
class Task1CriterionAssessmentBundle:
    assessments: tuple[Task1CriterionAssessment, ...]
    bundle_sha256: str
    version: str = TASK1_ASSESSMENT_BUNDLE_VERSION


@dataclass(frozen=True)
class Task1ScoringOutcome:
    status: Task1ScoringStatus
    assessments: tuple[Task1CriterionAssessment, ...]
    failures: tuple[str, ...]
    bundle: Task1CriterionAssessmentBundle | None


def _prompt() -> str:
    try:
        return _PROMPT_PATH.read_text(encoding="utf-8")
    except OSError as exc:
        raise Task1ScoringError("Task 1 criterion-scoring prompt is unavailable.") from exc


def _prompt_version() -> str:
    return "sha256:" + hashlib.sha256(_prompt().encode("utf-8")).hexdigest()


def _rubric_projection(rubric: StructuredRubricSnapshot, criterion: str) -> dict[str, Any]:
    selected = next((item for item in rubric.criteria if item.code == criterion), None)
    if selected is None:
        raise Task1ScoringError("Task 1 criterion rubric is unavailable.")
    return {
        "authority": "OFFICIAL_RUBRIC",
        "criterion": criterion,
        "officialDefinition": selected.official_definition,
        "bands": [{
            "band": anchor.band,
            "officialClaims": [
                {"id": claim.claim_id, "text": claim.text}
                for claim in anchor.official_claims
            ],
        } for anchor in selected.anchors],
        "derivedInternal": {
            "authority": "DERIVED_INTERNAL",
            "organizationalOnly": True,
            "mayAddScoringRequirements": False,
        },
    }


def _lineage(
    submission: Task1SubmissionSnapshot,
    facts: ReconciledChartFacts,
    claims: ClaimValidationArtifact,
    rubric: StructuredRubricSnapshot,
    criterion: str,
) -> dict[str, str]:
    return {
        "task1SubmissionSha256": submission.snapshot_sha256,
        "essayVersionId": submission.essay_version.essay_version_id,
        "locatorManifestSha256": submission.essay_version.locator_manifest_sha256,
        "imageSnapshotSha256": submission.image_snapshot_sha256,
        "reconciledFactsSha256": facts.artifact_sha256,
        "claimValidationSha256": claims.artifact_sha256,
        "rubricId": rubric.rubric_id,
        "rubricVersion": rubric.version,
        "runtimeContentSha256": rubric.runtime_content_sha256,
        "criterion": criterion,
        "schemaVersion": TASK1_SCORING_SCHEMA_VERSION,
    }


def _required_shape(lineage: Mapping[str, str]) -> dict[str, Any]:
    return {
        **lineage,
        "status": "ASSESSED|UNASSESSABLE",
        "lowerBand": None,
        "upperBand": None,
        "confidence": None,
        "confidenceReasons": [],
        "unassessableReasons": [],
        "findings": [{
            "role": "ANCHOR_SUPPORT|HIGHER_BAND_BOUNDARY|UNASSESSABLE_SUPPORT",
            "anchorBand": 0,
            "claimFit": "DEMONSTRATED|PARTIALLY_DEMONSTRATED|NOT_DEMONSTRATED|CONTRADICTED|RELATED_EVIDENCE_ONLY",
            "officialClaimIds": [],
            "evidenceRefs": [{"claimIds": [], "locatorIds": []}],
        }],
    }


def build_task1_criterion_input(
    submission: Task1SubmissionSnapshot,
    facts: ReconciledChartFacts,
    claims: ClaimValidationArtifact,
    rubric: StructuredRubricSnapshot,
    criterion: str,
) -> dict[str, Any]:
    if criterion not in TASK1_CRITERIA:
        raise Task1ScoringError("Task 1 criterion is invalid.")
    lineage = _lineage(submission, facts, claims, rubric, criterion)
    payload: dict[str, Any] = {
        "taskType": "task1",
        "criterion": criterion,
        "lineage": lineage,
        "candidateScriptContext": {
            "authority": "READ_ONLY_SEMANTIC_CONTEXT",
            "mayCreateEvidence": False,
            "text": submission.essay_version.original_text,
        },
        "locatorManifest": submission.essay_version.locator_manifest(),
        "criterionRubric": _rubric_projection(rubric, criterion),
        "requiredOutputShape": _required_shape(lineage),
        "outputRules": {
            "confidence": {"allowed": ["HIGH", "MEDIUM", "LOW"],
                "reasonCodes": sorted(_LIMITATION_REASONS | {"RANGE_WELL_SUPPORTED"}),
                "HIGH": "Include RANGE_WELL_SUPPORTED and no limitation reason.",
                "MEDIUM_or_LOW": "Include at least one limitation reason code."},
            "claimFitByRole": {role: sorted(fits) for role, fits in _FITS_BY_ROLE.items()},
            "evidenceRefs": {"claimIds": "Only supplied chart-claim IDs for TA; empty array for CC/LR/GRA.",
                "locatorIds": "Empty array for TA; only supplied essay locator IDs for CC/LR/GRA."},
            "anchors": "ANCHOR_SUPPORT for EVERY selected integer band, including upperBand. A separate HIGHER_BAND_BOUNDARY at upperBand + 1 only when upperBand < 9.",
        },
    }
    if criterion == "TA":
        payload["taskAchievementEvidence"] = {
            "authority": "VERIFIED_TASK1_FACTS_AND_CLAIMS",
            "reconciledFacts": facts.content(),
            "claimValidation": claims.content(),
        }
    else:
        payload["languageEvidenceBoundary"] = {
            "authority": "CURRENT_CANDIDATE_SCRIPT_ONLY",
            "factualAccuracyMayChangeCriterion": False,
            "allowedLocatorIds": sorted(
                submission.essay_version.paragraph_ids()
                | submission.essay_version.sentence_ids()
            ),
        }
    return payload


_ROOT_KEYS = frozenset({
    "task1SubmissionSha256", "essayVersionId", "locatorManifestSha256",
    "imageSnapshotSha256", "reconciledFactsSha256", "claimValidationSha256",
    "rubricId", "rubricVersion", "runtimeContentSha256", "criterion",
    "schemaVersion", "status", "lowerBand", "upperBand", "confidence",
    "confidenceReasons", "unassessableReasons", "findings",
})
_FINDING_KEYS = frozenset({
    "role", "anchorBand", "claimFit", "officialClaimIds", "evidenceRefs",
})
_REF_KEYS = frozenset({"claimIds", "locatorIds"})
_CONFIDENCE = frozenset({"HIGH", "MEDIUM", "LOW"})


def _validate_findings(
    raw_findings: Sequence[Mapping[str, Any]],
    *,
    criterion: str,
    rubric: StructuredRubricSnapshot,
    submission: Task1SubmissionSnapshot,
    claims: ClaimValidationArtifact,
) -> tuple[Mapping[str, Any], ...]:
    selected = next(item for item in rubric.criteria if item.code == criterion)
    claims_by_band = {
        (anchor.band, claim.claim_id)
        for anchor in selected.anchors
        for claim in anchor.official_claims
    }
    claim_ids = {item.claim_id for item in claims.claims}
    locator_ids = submission.essay_version.paragraph_ids() | submission.essay_version.sentence_ids()
    result: list[dict[str, Any]] = []
    seen: set[str] = set()
    for raw in raw_findings:
        if not isinstance(raw, Mapping) or set(raw) != _FINDING_KEYS:
            raise Task1ScoringError("Task 1 scoring finding fields are invalid.")
        role, fit, band = raw["role"], raw["claimFit"], raw["anchorBand"]
        if role not in {"ANCHOR_SUPPORT", "HIGHER_BAND_BOUNDARY", "UNASSESSABLE_SUPPORT"}:
            raise Task1ScoringError("Task 1 scoring finding role is invalid.")
        if not isinstance(band, int) or not 0 <= band <= 9:
            raise Task1ScoringError("Task 1 scoring finding band is invalid.")
        if fit not in _FITS_BY_ROLE[role]:
            raise Task1ScoringError("Task 1 scoring finding fit is invalid.")
        official = raw["officialClaimIds"]
        refs = raw["evidenceRefs"]
        if (
            not isinstance(official, list)
            or not official
            or len(set(official)) != len(official)
            or any((band, item) not in claims_by_band for item in official)
            or not isinstance(refs, list)
            or not refs
        ):
            raise Task1ScoringError("Task 1 scoring finding authority is invalid.")
        canonical_refs: list[dict[str, Any]] = []
        for ref in refs:
            if not isinstance(ref, Mapping) or set(ref) != _REF_KEYS:
                raise Task1ScoringError("Task 1 scoring evidence reference is invalid.")
            cited_claims = tuple(sorted(set(ref["claimIds"]))) if isinstance(ref["claimIds"], list) else ()
            cited_locators = tuple(sorted(set(ref["locatorIds"]))) if isinstance(ref["locatorIds"], list) else ()
            if criterion == "TA":
                if not cited_claims or cited_locators or not set(cited_claims).issubset(claim_ids):
                    raise Task1ScoringError("TA findings require validated chart claims only.")
            elif not cited_locators or cited_claims or not set(cited_locators).issubset(locator_ids):
                raise Task1ScoringError("Language findings require current essay locators only.")
            canonical_refs.append({"claimIds": list(cited_claims), "locatorIds": list(cited_locators)})
        finding = {
            "role": role,
            "anchorBand": band,
            "claimFit": fit,
            "officialClaimIds": sorted(official),
            "evidenceRefs": sorted(canonical_refs, key=lambda item: json.dumps(item, sort_keys=True)),
        }
        marker = json.dumps(finding, sort_keys=True, separators=(",", ":"))
        if marker in seen:
            raise Task1ScoringError("Task 1 scoring findings are duplicated.")
        seen.add(marker)
        result.append(finding)
    result.sort(key=lambda item: (item["role"], item["anchorBand"], json.dumps(item, sort_keys=True)))
    return tuple(
        _freeze({"findingId": f"f{index:04d}", **item})
        for index, item in enumerate(result, start=1)
    )


def validate_task1_scoring_output(
    value: Mapping[str, Any],
    *,
    criterion: str,
    submission: Task1SubmissionSnapshot,
    facts: ReconciledChartFacts,
    claims: ClaimValidationArtifact,
    rubric: StructuredRubricSnapshot,
    identity: Task1ScoringExecutionIdentity,
) -> Task1CriterionAssessment:
    if not isinstance(value, Mapping) or set(value) != _ROOT_KEYS:
        raise Task1ScoringError("Task 1 scoring output fields are invalid.")
    lineage = _lineage(submission, facts, claims, rubric, criterion)
    if any(value[key] != expected for key, expected in lineage.items()):
        raise Task1ScoringError("Task 1 scoring output lineage differs.")
    try:
        status = Task1AssessmentStatus(value["status"])
    except (TypeError, ValueError) as exc:
        raise Task1ScoringError("Task 1 scoring status is invalid.") from exc
    findings = _validate_findings(
        value["findings"],
        criterion=criterion,
        rubric=rubric,
        submission=submission,
        claims=claims,
    )
    lower, upper = value["lowerBand"], value["upperBand"]
    confidence = value["confidence"]
    confidence_reasons = value["confidenceReasons"]
    unassessable = value["unassessableReasons"]
    if (
        not isinstance(confidence_reasons, list)
        or any(not isinstance(item, str) for item in confidence_reasons)
        or len(set(confidence_reasons)) != len(confidence_reasons)
        or not isinstance(unassessable, list)
        or any(not isinstance(item, str) for item in unassessable)
    ):
        raise Task1ScoringError("Task 1 scoring confidence or reasons are invalid.")
    estimated: float | None = None
    if status is Task1AssessmentStatus.ASSESSED:
        if (
            not isinstance(lower, int)
            or not isinstance(upper, int)
            or not 0 <= lower <= upper <= 9
            or upper - lower > 1
            or confidence not in _CONFIDENCE
            or unassessable
        ):
            raise Task1ScoringError("Task 1 assessed range is invalid.")
        anchors = {
            item["anchorBand"] for item in findings if item["role"] == "ANCHOR_SUPPORT"
        }
        if not set(range(lower, upper + 1)).issubset(anchors):
            raise Task1ScoringError("Task 1 selected anchors lack evidence.")
        if upper < 9 and not any(
            item["role"] == "HIGHER_BAND_BOUNDARY" and item["anchorBand"] == upper + 1
            for item in findings
        ):
            raise Task1ScoringError("Task 1 next-higher boundary is missing.")
        reasons = set(confidence_reasons)
        if confidence == "HIGH":
            if "RANGE_WELL_SUPPORTED" not in reasons or reasons & _LIMITATION_REASONS:
                raise Task1ScoringError("Task 1 high confidence is inconsistent.")
        elif not reasons & _LIMITATION_REASONS:
            raise Task1ScoringError("Task 1 limited confidence lacks a reason.")
        estimated = (lower + upper) / 2
    elif (
        lower is not None
        or upper is not None
        or confidence is not None
        or confidence_reasons
        or not unassessable
    ):
        raise Task1ScoringError("Task 1 unassessable result is invalid.")
    assessment = Task1CriterionAssessment(
        criterion=criterion,
        status=status,
        lower_band=lower,
        upper_band=upper,
        estimated_band=estimated,
        confidence=confidence,
        confidence_reasons=tuple(sorted(confidence_reasons)),
        unassessable_reasons=tuple(sorted(set(unassessable))),
        findings=findings,
        authoritative_lineage=_freeze(lineage),
        execution_identity=identity,
    )
    return Task1CriterionAssessment(**{
        **assessment.__dict__,
        "assessment_sha256": digest(assessment.content(include_hash=False)),
    })


class Task1CriterionScoringService:
    def __init__(self, transport: ProviderTransport, *, on_criterion=None) -> None:
        self._transport = transport
        self._on_criterion = on_criterion or (lambda event: None)

    def _preflight(
        self,
        submission: Task1SubmissionSnapshot,
        facts: ReconciledChartFacts,
        claims: ClaimValidationArtifact,
        rubric: StructuredRubricSnapshot,
        resolution: RouteResolution,
    ) -> tuple[Any, Task1ScoringExecutionIdentity] | None:
        contract = resolution.contract if isinstance(resolution, RouteResolution) else None
        submission_identity = {
            "version": submission.version,
            "taskType": submission.task_type,
            "questionContentSha256": submission.question_content_sha256,
            "imageSnapshotSha256": submission.image_snapshot_sha256,
            "essayVersionId": submission.essay_version.essay_version_id,
            "locatorManifestSha256": submission.essay_version.locator_manifest_sha256,
        }
        if (
            contract is None
            or not resolution.ok
            or resolution.route.route_id != "task1.criterion_scoring"
            or submission.task_type != "task1"
            or digest(submission_identity) != submission.snapshot_sha256
            or facts.status is not ReconciliationStatus.VERIFIED
            or claims.status is not ClaimArtifactStatus.COMPLETE
            or claims.task1_submission_sha256 != submission.snapshot_sha256
            or claims.reconciled_facts_sha256 != facts.artifact_sha256
            or rubric.task_type != "task1"
            or tuple(item.code for item in rubric.criteria) != TASK1_CRITERIA
            or rubric.runtime_content_sha256 != TASK1_EXPECTED_RUNTIME_HASH
        ):
            return None
        prompt_version = _prompt_version()
        identity = Task1ScoringExecutionIdentity(
            provider_id=contract.provider_id,
            route_id=resolution.route.route_id,
            model_id=contract.model.model_id,
            prompt_version=prompt_version,
            schema_version=TASK1_SCORING_SCHEMA_VERSION,
            semantic_version=TASK1_SCORING_SEMANTIC_VERSION,
            repair_policy_version=TASK1_SCORING_REPAIR_POLICY_VERSION,
            rubric_id=rubric.rubric_id,
            rubric_version=rubric.version,
            runtime_content_sha256=rubric.runtime_content_sha256,
            task1_submission_sha256=submission.snapshot_sha256,
            image_snapshot_sha256=submission.image_snapshot_sha256,
            reconciled_facts_sha256=facts.artifact_sha256,
            claim_validation_sha256=claims.artifact_sha256,
        )
        return contract, identity

    def _one(
        self,
        criterion: str,
        contract: Any,
        identity: Task1ScoringExecutionIdentity,
        submission: Task1SubmissionSnapshot,
        facts: ReconciledChartFacts,
        claims: ClaimValidationArtifact,
        rubric: StructuredRubricSnapshot,
        calibration_review=None,
    ) -> Task1CriterionAssessment | str:
        payload = build_task1_criterion_input(submission, facts, claims, rubric, criterion)
        prompt = _prompt()
        if calibration_review is not None:
            from .calibration import RE_SCORE_INSTRUCTION
            payload['calibrationReview'] = calibration_review.criteria[criterion]
            prompt += '\n' + RE_SCORE_INSTRUCTION
        messages = [
            {"role": "system", "content": prompt},
            {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
        ]
        previous = None
        rule = "Return valid JSON without trailing prose."
        for attempt in (1, 2):
            attempt_messages = list(messages)
            if attempt == 2:
                attempt_messages.append({"role": "assistant", "content": previous})
                attempt_messages.append({
                    "role": "user",
                    "content": "Return one corrected JSON object with the same inputs. Do not add scores, facts, evidence, or locators outside the supplied authorities. Validation rule: " + rule,
                })
            result = self._transport.call(contract, ProviderCallRequest(
                attempt_messages,
                f"Task 1 {criterion} assessment",
                require_json_object=True,
                validate_json_object=False,
            ))
            if not isinstance(result, ProviderCallResult) or not result.ok:
                code = result.failure.code.value if isinstance(result, ProviderCallResult) and result.failure else 'PROVIDER_FAILURE'
                return f"{criterion}:{code}"
            try:
                raw = json.loads(result.content)
                if not isinstance(raw, Mapping):
                    raise Task1ScoringError("Task 1 criterion output is not an object.")
                return validate_task1_scoring_output(
                    raw,
                    criterion=criterion,
                    submission=submission,
                    facts=facts,
                    claims=claims,
                    rubric=rubric,
                    identity=identity,
                )
            except (json.JSONDecodeError, Task1ScoringError) as exc:
                logging.getLogger(__name__).warning("task1_scoring_validation criterion=%s attempt=%s rule=%s",
                    criterion, attempt, str(exc) if isinstance(exc, Task1ScoringError) else "INVALID_JSON")
                previous = result.content
                if isinstance(exc, Task1ScoringError):
                    rule = str(exc)
                continue
        return f"{criterion}:OUTPUT_INVALID_AFTER_REPAIR"

    def run(
        self,
        submission: Task1SubmissionSnapshot,
        facts: ReconciledChartFacts,
        claims: ClaimValidationArtifact,
        rubric: StructuredRubricSnapshot,
        resolution: RouteResolution,
        *, calibration_review=None,
    ) -> Task1ScoringOutcome:
        prepared = self._preflight(submission, facts, claims, rubric, resolution)
        if prepared is None:
            return Task1ScoringOutcome(Task1ScoringStatus.FAILED, (), ("PREFLIGHT_FAILED",), None)
        contract, identity = prepared
        if calibration_review is not None:
            from dataclasses import replace
            from .calibration import CalibrationReview, RE_SCORE_INSTRUCTION
            if not isinstance(calibration_review, CalibrationReview):
                raise Task1ScoringError('Typed calibration context required.')
            calibration_review.validate(submission, rubric)
            identity = replace(identity, prompt_version=digest({'base': identity.prompt_version,
                'instruction': RE_SCORE_INSTRUCTION, 'review': calibration_review.review_sha256}))
        results: dict[str, Task1CriterionAssessment | str] = {}
        with ThreadPoolExecutor(max_workers=4) as executor:
            futures = {
                executor.submit(
                    self._one, criterion, contract, identity,
                    submission, facts, claims, rubric, calibration_review,
                ): criterion
                for criterion in TASK1_CRITERIA
            }
            for future in as_completed(futures):
                criterion = futures[future]
                try:
                    results[criterion] = future.result()
                except Exception:
                    results[criterion] = f"{criterion}:UNEXPECTED_FAILURE"
                value = results[criterion]
                self._on_criterion({"criterion": criterion,
                    "status": value.status.value if isinstance(value, Task1CriterionAssessment) else "FAILED"})
        assessments = tuple(
            results[criterion]
            for criterion in TASK1_CRITERIA
            if isinstance(results.get(criterion), Task1CriterionAssessment)
        )
        failures = tuple(
            str(results[criterion])
            for criterion in TASK1_CRITERIA
            if not isinstance(results.get(criterion), Task1CriterionAssessment)
        )
        all_assessed = len(assessments) == 4 and all(
            item.status is Task1AssessmentStatus.ASSESSED for item in assessments
        )
        bundle: Task1CriterionAssessmentBundle | None = None
        if all_assessed:
            payload = {
                "version": TASK1_ASSESSMENT_BUNDLE_VERSION,
                "sharedLineage": identity.content(),
                "assessmentSha256ByCriterion": {
                    item.criterion: item.assessment_sha256 for item in assessments
                },
            }
            bundle = Task1CriterionAssessmentBundle(assessments, digest(payload))
            status = Task1ScoringStatus.COMPLETE
        elif assessments:
            status = Task1ScoringStatus.INCOMPLETE
        else:
            status = Task1ScoringStatus.FAILED
        return Task1ScoringOutcome(status, assessments, failures, bundle)


@dataclass(frozen=True)
class Task1AssessmentFoundationOutcome:
    status: Task1FoundationStatus
    fact_pipeline: Task1FactPipelineOutcome
    claims: ClaimValidationArtifact | None
    assessments: tuple[Task1CriterionAssessment, ...]
    review: ReviewGateDecision
    locked_score: LockedScoreSnapshot | None
    diagnosis: CoreDiagnosis | None
    failures: tuple[str, ...]
    finalization_bundle: FinalizationBundle | None = None


class Task1AssessmentFoundationService:
    def __init__(
        self,
        fact_pipeline: Task1FactPipeline,
        claim_service: Task1ClaimService,
        scoring_service: Task1CriterionScoringService,
        *, on_checkpoint=None, prelock_review=None,
    ) -> None:
        self._facts = fact_pipeline
        self._claims = claim_service
        self._scoring = scoring_service
        self._checkpoint = on_checkpoint or (lambda event: None)
        self._prelock_review = prelock_review

    def run(
        self,
        source: Task1ImageSnapshot,
        submission: Task1SubmissionSnapshot,
        rubric: StructuredRubricSnapshot,
        *,
        extraction_resolution: RouteResolution,
        verification_resolution: RouteResolution,
        claim_resolution: RouteResolution,
        scoring_resolution: RouteResolution,
        target_band: float | None = None,
    ) -> Task1AssessmentFoundationOutcome:
        if (
            submission.image_snapshot_sha256 != source.snapshot_sha256
            or submission.question_content_sha256 != source.question_content_sha256
        ):
            empty_facts = Task1FactPipelineOutcome(
                None, None, reconcile_chart_facts(source, None, None)
            )
            review = make_review_decision(
                "task1-lineage:" + submission.snapshot_sha256,
                (ReviewReason.SCHEMA_INTEGRITY,),
            )
            return Task1AssessmentFoundationOutcome(
                Task1FoundationStatus.FAILED,
                empty_facts,
                None,
                (),
                review,
                None,
                None,
                ("TASK1_SOURCE_LINEAGE_MISMATCH",),
            )
        self._checkpoint({"stage": "CHART_FACTS"})
        fact_pipeline = self._facts.run(
            source, extraction_resolution, verification_resolution
        )
        if fact_pipeline.reconciled.status is not ReconciliationStatus.VERIFIED:
            reason = (
                ReviewReason.TASK1_FACT_CONFLICT
                if fact_pipeline.reconciled.status is ReconciliationStatus.CONFLICT
                else ReviewReason.INCOMPLETE
            )
            review = make_review_decision(
                "task1-facts:" + fact_pipeline.reconciled.artifact_sha256,
                (reason,),
            )
            return Task1AssessmentFoundationOutcome(
                Task1FoundationStatus.REVIEW_REQUIRED,
                fact_pipeline,
                None,
                (),
                review,
                None,
                None,
                (fact_pipeline.reconciled.status.value, *fact_pipeline.provider_failures),
            )
        try:
            self._checkpoint({"stage": "CHART_CLAIMS"})
            claims = self._claims.run(
                submission, fact_pipeline.reconciled, claim_resolution
            )
        except Task1ClaimError as exc:
            review = make_review_decision(
                "task1-claims:" + submission.snapshot_sha256,
                (ReviewReason.INCOMPLETE,),
            )
            return Task1AssessmentFoundationOutcome(
                Task1FoundationStatus.FAILED,
                fact_pipeline,
                None,
                (),
                review,
                None,
                None,
                (exc.provider_failure_code or "CLAIM_VALIDATION_FAILED",),
            )
        if claims.status is ClaimArtifactStatus.REVIEW_REQUIRED:
            review = make_review_decision(
                "task1-claims:" + claims.artifact_sha256,
                (ReviewReason.EVIDENCE_CONFLICT,),
            )
            return Task1AssessmentFoundationOutcome(
                Task1FoundationStatus.REVIEW_REQUIRED,
                fact_pipeline,
                claims,
                (),
                review,
                None,
                None,
                ("AMBIGUOUS_MATERIAL_CLAIM",),
            )
        self._checkpoint({"stage": "CRITERION_SCORING"})
        scoring = self._scoring.run(
            submission, fact_pipeline.reconciled, claims, rubric, scoring_resolution
        )
        if scoring.bundle is None:
            review = make_review_decision(
                "task1-scoring:" + submission.snapshot_sha256,
                (ReviewReason.INCOMPLETE,),
            )
            return Task1AssessmentFoundationOutcome(
                Task1FoundationStatus.REVIEW_REQUIRED,
                fact_pipeline,
                claims,
                scoring.assessments,
                review,
                None,
                None,
                scoring.failures,
            )
        bundle = FinalizationBundle.create(
            "task1", scoring.bundle.assessments,
            source_bundle_sha256=scoring.bundle.bundle_sha256,
        )
        if self._prelock_review is not None:
            bundle = self._prelock_review(bundle, fact_pipeline.reconciled, claims)
            if bundle is None:
                return Task1AssessmentFoundationOutcome(Task1FoundationStatus.REVIEW_REQUIRED,
                    fact_pipeline, claims, scoring.assessments,
                    make_review_decision('calibration:' + submission.snapshot_sha256, (ReviewReason.DISAGREEMENT,)),
                    None, None, ('CALIBRATION_REVIEW_REQUIRED',))
        review = review_scores(bundle)
        if review.status is ReviewStatus.REVIEW_REQUIRED:
            return Task1AssessmentFoundationOutcome(
                Task1FoundationStatus.REVIEW_REQUIRED,
                fact_pipeline,
                claims,
                scoring.assessments,
                review,
                None,
                None,
                scoring.failures,
            )
        locked = finalize_scores(bundle, review)
        diagnosis = build_core_diagnosis(locked, bundle, target_band=target_band)
        return Task1AssessmentFoundationOutcome(
            Task1FoundationStatus.COMPLETE,
            fact_pipeline,
            claims,
            scoring.assessments,
            review,
            locked,
            diagnosis,
            (),
            bundle,
        )
