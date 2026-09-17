"""Rubric-only, criterion-isolated Task 2 assessment for P2-03."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from enum import Enum
import hashlib
import json
from pathlib import Path
from threading import Lock
from time import monotonic
from types import MappingProxyType
from typing import Any, Mapping, Sequence

from jsonschema import Draft202012Validator

from .providers import (
    ProviderCallRequest,
    ProviderCallResult,
    ProviderContract,
    ProviderFailureCode,
    ProviderTransport,
    RouteResolution,
    route_for,
)
from .rubric import StructuredRubricSnapshot, is_approved_task2_snapshot
from .student_evidence import StudentEvidence, StudentEvidenceStatus
from .submission import SubmissionSnapshot, canonical_json, digest
from .task2_understanding import Task2Understanding, UnderstandingStatus


CRITERION_SCORING_SCHEMA_VERSION = "criterion-assessment-output-v1"
CRITERION_SCORING_SEMANTIC_VERSION = "criterion-scoring-v1"
CRITERION_SCORING_REPAIR_POLICY_VERSION = "criterion-scoring-repair-v2"
CRITERION_ASSESSMENT_BUNDLE_VERSION = "criterion-assessment-bundle-v1"
_CRITERIA = ("TR", "CC", "LR", "GRA")
_ROOT = Path(__file__).resolve().parents[1]
_SCHEMA_PATH = _ROOT / "resources" / "criterion_scoring" / "v1" / "criterion-assessment-output.schema.json"
_PROMPT_PATH = _ROOT / "prompts" / "task2_criterion_scoring.md"
_LIMITATION_REASONS = frozenset({
    "EVIDENCE_COVERAGE_LIMITED",
    "WITHIN_CRITERION_TENSION",
    "BOUNDARY_DISTINCTION_LIMITED",
})


class CriterionScoringError(ValueError):
    """Safe P2-03 boundary error."""


class CriterionOutputError(CriterionScoringError):
    """Provider output failed strict schema or semantic validation."""

    def __init__(self, message, *, path=None):
        super().__init__(message)
        self.diagnostic_code, default_path = OUTPUT_DIAGNOSTICS.get(message, ("OUTPUT_INVALID", None))
        self.diagnostic_path = path if path is not None else default_path


# Diagnostic-only vocabulary for existing predicates. Never contains error values,
# quotes, Provider messages, or exceptions; does not participate in artifact hashes.
OUTPUT_DIAGNOSTICS = {
    "Criterion output is not valid JSON.": ("OUTPUT_JSON_INVALID", "$"),
    "Criterion output must be an object.": ("OUTPUT_OBJECT_REQUIRED", "$"),
    "Criterion output has an invalid schema.": ("OUTPUT_SCHEMA_INVALID", "$"),
    "Criterion output lineage differs.": ("OUTPUT_LINEAGE_MISMATCH", "$"),
    "Anchor support claim fit is invalid.": ("ANCHOR_SUPPORT_CLAIM_FIT_INVALID", "$.findings"),
    "Higher boundary claim fit is invalid.": ("HIGHER_BOUNDARY_CLAIM_FIT_INVALID", "$.findings"),
    "Unassessable support is invalid.": ("UNASSESSABLE_SUPPORT_INVALID", "$.findings"),
    "Unassessable support requires an unprovable condition.": ("UNPROVABLE_CONDITION_REQUIRED", "$.findings"),
    "Related-only evidence requires unassessable support.": ("RELATED_ONLY_SUPPORT_INVALID", "$.findings"),
    "Assessed results cannot contain unassessable support.": ("ASSESSED_SUPPORT_INVALID", "$.findings"),
    "Finding official claims are invalid.": ("OFFICIAL_CLAIM_REFERENCE_INVALID", "$.findings"),
    "Finding dimension mapping is invalid.": ("DIMENSION_MAPPING_INVALID", "$.findings"),
    "Finding evidence is not in the criterion evidence slice.": ("EVIDENCE_SLICE_REFERENCE_INVALID", "$.findings"),
    "Finding span IDs are duplicated.": ("SPAN_REFERENCE_DUPLICATED", "$.findings"),
    "Span evidence reference is invalid.": ("SPAN_REFERENCE_INVALID", "$.findings"),
    "Non-span evidence cannot cite span IDs.": ("NON_SPAN_REFERENCE_INVALID", "$.findings"),
    "Finding evidence references are duplicated.": ("EVIDENCE_REFERENCE_DUPLICATED", "$.findings"),
    "Semantic findings are duplicated.": ("SEMANTIC_FINDING_DUPLICATED", "$.findings"),
    "Used contradiction hook is invalid.": ("CONTRADICTION_HOOK_INVALID", "$.usedContradictionHooks"),
    "Candidate band range must be exact or adjacent.": ("RANGE_NOT_EXACT_OR_ADJACENT", "$.upperBand"),
    "Unassessable support cannot support a range.": ("RANGE_SUPPORT_INVALID", "$.findings"),
    "Selected anchors lack separate support.": ("ANCHOR_SUPPORT_MISSING", "$.findings"),
    "The next higher band boundary is missing.": ("HIGHER_BAND_BOUNDARY_MISSING", "$.findings"),
    "Higher boundary targets an invalid anchor.": ("HIGHER_BOUNDARY_ANCHOR_INVALID", "$.findings"),
    "Adjacent range shape and confidence reasons differ.": ("ADJACENT_CONFIDENCE_MISMATCH", "$.confidenceReasons"),
    "High confidence reasons are inconsistent.": ("HIGH_CONFIDENCE_INCONSISTENT", "$.confidenceReasons"),
    "Medium or low confidence lacks a limitation reason.": ("CONFIDENCE_LIMITATION_MISSING", "$.confidenceReasons"),
    "Assessed contradiction hooks and confidence differ.": ("HOOK_CONFIDENCE_MISMATCH", "$.confidenceReasons"),
    "Band 0 requires validated global evidence.": ("BAND_ZERO_GLOBAL_EVIDENCE_MISSING", "$.findings"),
    "Frozen word count requires the Band 1 condition.": ("BAND_ONE_CONDITION_MISSING", "$.findings"),
    "Band 1 word-count claim contradicts frozen word count.": ("BAND_ONE_WORD_COUNT_CONFLICT", "$.findings"),
    "Non-adjacent uncertainty lacks boundary proof.": ("NON_ADJACENT_BOUNDARY_PROOF_MISSING", "$.findings"),
    "Evidence tension requires a used hook.": ("TENSION_HOOK_MISSING", "$.usedContradictionHooks"),
    "Evidence tension findings do not cover hook endpoints.": ("TENSION_ENDPOINT_COVERAGE_MISSING", "$.findings"),
    "Unassessable used hooks lack the tension reason.": ("UNASSESSABLE_TENSION_REASON_MISSING", "$.unassessableReasons"),
    "An unprovable special condition cannot form a band boundary.": ("UNPROVABLE_BOUNDARY_INVALID", "$.findings"),
    "Unassessable support is restricted to unprovable conditions.": ("UNASSESSABLE_CONDITION_INVALID", "$.findings"),
}


class RunStatus(str, Enum):
    COMPLETE = "COMPLETE"
    INCOMPLETE = "INCOMPLETE"
    FAILED = "FAILED"


class AssessmentStatus(str, Enum):
    ASSESSED = "ASSESSED"
    UNASSESSABLE = "UNASSESSABLE"


class FailureCategory(str, Enum):
    RUBRIC_INVALID = "RUBRIC_INVALID"
    TASK_UNDERSTANDING_INVALID = "TASK_UNDERSTANDING_INVALID"
    STUDENT_EVIDENCE_INVALID = "STUDENT_EVIDENCE_INVALID"
    LINEAGE_MISMATCH = "LINEAGE_MISMATCH"
    INPUT_PROJECTION_INVALID = "INPUT_PROJECTION_INVALID"
    ROUTE_UNAVAILABLE = "ROUTE_UNAVAILABLE"
    ORCHESTRATION_FAILURE = "ORCHESTRATION_FAILURE"
    PROVIDER_FAILURE = "PROVIDER_FAILURE"
    OUTPUT_INVALID_AFTER_REPAIR = "OUTPUT_INVALID_AFTER_REPAIR"
    UNEXPECTED_CRITERION_FAILURE = "UNEXPECTED_CRITERION_FAILURE"


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


def _sha256_text(value: str) -> str:
    return "sha256:" + hashlib.sha256(value.encode("utf-8")).hexdigest()


def prompt_text() -> str:
    try:
        return _PROMPT_PATH.read_text(encoding="utf-8")
    except OSError as exc:
        raise CriterionScoringError("Criterion scoring prompt is unavailable.") from exc


def prompt_version() -> str:
    return _sha256_text(prompt_text())


def output_schema() -> dict[str, Any]:
    try:
        value = json.loads(_SCHEMA_PATH.read_text(encoding="utf-8"))
        Draft202012Validator.check_schema(value)
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError) as exc:
        raise CriterionScoringError("Criterion scoring schema is unavailable.") from exc
    return value


@dataclass(frozen=True)
class CriterionScoringExecutionIdentity:
    provider_id: str
    route_id: str
    configuration_key: str
    model_id: str
    model_version: str | None
    base_url: str
    prompt_version: str
    schema_version: str
    semantic_version: str
    repair_policy_version: str
    rubric_id: str
    rubric_version: str
    runtime_content_sha256: str
    submission_snapshot_id: str
    essay_version_id: str
    locator_manifest_sha256: str
    task2_understanding_sha256: str
    student_evidence_sha256: str

    def content(self) -> dict[str, Any]:
        return {
            "providerId": self.provider_id,
            "routeId": self.route_id,
            "configurationKey": self.configuration_key,
            "modelId": self.model_id,
            "modelVersion": self.model_version,
            "baseUrl": self.base_url,
            "promptVersion": self.prompt_version,
            "schemaVersion": self.schema_version,
            "semanticVersion": self.semantic_version,
            "repairPolicyVersion": self.repair_policy_version,
            "rubricId": self.rubric_id,
            "rubricVersion": self.rubric_version,
            "runtimeContentSha256": self.runtime_content_sha256,
            "submissionSnapshotId": self.submission_snapshot_id,
            "essayVersionId": self.essay_version_id,
            "locatorManifestSha256": self.locator_manifest_sha256,
            "task2UnderstandingSha256": self.task2_understanding_sha256,
            "studentEvidenceSha256": self.student_evidence_sha256,
        }


@dataclass(frozen=True)
class AttemptAudit:
    criterion: str
    attempt: int
    elapsed_ms: int
    validation_state: str
    provider_failure_code: str | None
    prompt_tokens: int | None
    completion_tokens: int | None
    total_tokens: int | None
    validation_code: str | None = None
    validation_path: str | None = None


class CriterionScoringAttemptRecorder:
    """Concurrency-safe trace-only telemetry; never enters artifact hashes."""

    def __init__(self) -> None:
        self._lock = Lock()
        self._attempts: list[AttemptAudit] = []

    def record(
        self,
        criterion: str,
        attempt: int,
        elapsed_ms: int,
        validation_state: str,
        result: ProviderCallResult | None,
        diagnostic: CriterionOutputError | None = None,
    ) -> None:
        failure_code = None
        usage = None
        if isinstance(result, ProviderCallResult):
            failure_code = result.failure.code.value if result.failure else None
            usage = result.usage
        audit = AttemptAudit(
            criterion=criterion,
            attempt=attempt,
            elapsed_ms=elapsed_ms,
            validation_state=validation_state,
            provider_failure_code=failure_code,
            prompt_tokens=usage.prompt_tokens if usage else None,
            completion_tokens=usage.completion_tokens if usage else None,
            total_tokens=usage.total_tokens if usage else None,
            validation_code=diagnostic.diagnostic_code if diagnostic else None,
            validation_path=diagnostic.diagnostic_path if diagnostic else None,
        )
        with self._lock:
            self._attempts.append(audit)

    def snapshot(self) -> tuple[AttemptAudit, ...]:
        with self._lock:
            return tuple(sorted(self._attempts, key=lambda item: (_CRITERIA.index(item.criterion), item.attempt)))


@dataclass(frozen=True)
class CriterionFailure:
    criterion: str | None
    category: FailureCategory
    provider_failure_code: ProviderFailureCode | None = None

    def safe_content(self) -> dict[str, Any]:
        return {
            "criterion": self.criterion,
            "category": self.category.value,
            "providerFailureCode": self.provider_failure_code.value if self.provider_failure_code else None,
        }


@dataclass(frozen=True)
class CriterionAssessment:
    criterion: str
    status: AssessmentStatus
    lower_band: int | None
    upper_band: int | None
    estimated_band: float | None
    confidence: str | None
    confidence_reasons: tuple[str, ...]
    unassessable_reasons: tuple[str, ...]
    findings: tuple[Mapping[str, Any], ...] = field(repr=False)
    used_contradiction_hooks: tuple[Mapping[str, str], ...] = field(repr=False)
    authoritative_lineage: Mapping[str, str] = field(repr=False)
    execution_identity: CriterionScoringExecutionIdentity = field(repr=False)
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
            "usedContradictionHooks": [_thaw(item) for item in self.used_contradiction_hooks],
            "authoritativeLineage": dict(self.authoritative_lineage),
            "executionIdentity": self.execution_identity.content(),
        }
        if include_hash:
            value["assessmentSha256"] = self.assessment_sha256
        return value


@dataclass(frozen=True)
class CriterionAssessmentBundle:
    assessments: tuple[CriterionAssessment, ...]
    bundle_sha256: str
    version: str = CRITERION_ASSESSMENT_BUNDLE_VERSION

    def content(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "assessmentSha256ByCriterion": {
                item.criterion: item.assessment_sha256 for item in self.assessments
            },
            "bundleSha256": self.bundle_sha256,
        }


@dataclass(frozen=True)
class CriterionScoringExecutionRecord:
    status: RunStatus
    assessment_sha256_by_criterion: Mapping[str, str]
    failures: tuple[CriterionFailure, ...]
    bundle_sha256: str | None
    attempts: tuple[AttemptAudit, ...]


@dataclass(frozen=True)
class CriterionScoringOutcome:
    status: RunStatus
    assessments: tuple[CriterionAssessment, ...]
    failures: tuple[CriterionFailure, ...]
    bundle: CriterionAssessmentBundle | None
    execution_record: CriterionScoringExecutionRecord


@dataclass(frozen=True)
class _Preflight:
    snapshot: SubmissionSnapshot
    understanding: Task2Understanding
    evidence: StudentEvidence
    rubric: StructuredRubricSnapshot
    contract: ProviderContract
    identity: CriterionScoringExecutionIdentity
    payloads: Mapping[str, Mapping[str, Any]]


def _lineage(snapshot: SubmissionSnapshot, understanding: Task2Understanding, evidence: StudentEvidence, rubric: StructuredRubricSnapshot, criterion: str) -> dict[str, str]:
    return {
        "submissionSnapshotId": snapshot.submission_snapshot_id,
        "essayVersionId": snapshot.essay_version.essay_version_id,
        "locatorManifestSha256": snapshot.essay_version.locator_manifest_sha256,
        "task2UnderstandingSha256": understanding.artifact_sha256,
        "studentEvidenceSha256": evidence.artifact_sha256,
        "rubricId": rubric.rubric_id,
        "rubricVersion": rubric.version,
        "runtimeContentSha256": rubric.runtime_content_sha256,
        "criterion": criterion,
        "schemaVersion": CRITERION_SCORING_SCHEMA_VERSION,
    }


def _criterion_rubric(rubric: StructuredRubricSnapshot, criterion: str) -> dict[str, Any]:
    selected = next((item for item in rubric.criteria if item.code == criterion), None)
    if selected is None:
        raise CriterionScoringError("Criterion rubric projection is unavailable.")
    return {
        "authority": "OFFICIAL_RUBRIC",
        "criterion": selected.code,
        "officialDefinition": selected.official_definition,
        "bands": [
            {
                "band": anchor.band,
                "officialClaims": [
                    {"id": claim.claim_id, "text": claim.text}
                    for claim in anchor.official_claims
                ],
            }
            for anchor in selected.anchors
        ],
        "derivedInternal": {
            "authority": "DERIVED_INTERNAL",
            "organizationalOnly": True,
            "mayAddScoringRequirements": False,
            "bands": [
                {"band": anchor.band, "dimensions": _thaw(anchor.derived_internal)}
                for anchor in selected.anchors
            ],
        },
    }


def _criterion_evidence(evidence: StudentEvidence, criterion: str) -> dict[str, Any]:
    observations = [
        _thaw(item) for item in evidence.payload["observations"] if item["criterion"] == criterion
    ]
    observation_ids = {item["observationId"] for item in observations}
    hooks = [
        _thaw(item)
        for item in evidence.payload["contradictionHooks"]
        if item["leftObservationId"] in observation_ids and item["rightObservationId"] in observation_ids
    ]
    return {
        "authority": "VALIDATED_P2_02_ONLY",
        "observations": observations,
        "contradictionHooks": hooks,
    }


def _understanding_projection(understanding: Task2Understanding, criterion: str) -> dict[str, Any]:
    full = understanding.main_review_projection()
    if criterion == "TR":
        return _thaw(full)
    lineage = {
        key: _thaw(full[key])
        for key in ("understandingSha256", "schemaVersion", "semanticVersion", "submissionSnapshotId", "essayVersionId", "locatorManifestSha256")
    }
    if criterion == "CC":
        lineage["argumentNodes"] = _thaw(full["argumentNodes"])
    return lineage


def build_criterion_input(
    snapshot: SubmissionSnapshot,
    understanding: Task2Understanding,
    evidence: StudentEvidence,
    rubric: StructuredRubricSnapshot,
    criterion: str,
) -> dict[str, Any]:
    if criterion not in _CRITERIA:
        raise CriterionScoringError("Criterion is invalid.")
    value: dict[str, Any] = {
        "taskType": "task2",
        "criterion": criterion,
        "lineage": _lineage(snapshot, understanding, evidence, rubric, criterion),
        "candidateScriptContext": {
            "authority": "READ_ONLY_SEMANTIC_CONTEXT",
            "mayCreateEvidence": False,
            "text": snapshot.essay_version.original_text,
        },
        "locatorManifest": snapshot.essay_version.locator_manifest(),
        "wordCount": {
            "authority": "FROZEN_ESSAY_VERSION",
            "value": snapshot.essay_version.word_count,
        },
        "criterionRubric": _criterion_rubric(rubric, criterion),
        "studentEvidence": _criterion_evidence(evidence, criterion),
        "task2Understanding": _understanding_projection(understanding, criterion),
        "requiredOutputSchema": output_schema(),
    }
    if criterion == "TR":
        value["question"] = snapshot.question
    return value


def _preflight(
    snapshot: SubmissionSnapshot,
    understanding: Task2Understanding,
    evidence: StudentEvidence,
    rubric: StructuredRubricSnapshot,
    resolution: RouteResolution,
) -> _Preflight | CriterionFailure:
    try:
        rebuilt = SubmissionSnapshot.create(
            snapshot.task_type, snapshot.question, snapshot.essay_version.original_text
        )
    except (AttributeError, TypeError, ValueError):
        return CriterionFailure(None, FailureCategory.INPUT_PROJECTION_INVALID)
    if (
        rebuilt.submission_snapshot_id != snapshot.submission_snapshot_id
        or rebuilt.snapshot_sha256 != snapshot.snapshot_sha256
        or rebuilt.question_content_sha256 != snapshot.question_content_sha256
        or rebuilt.essay_version.essay_version_id != snapshot.essay_version.essay_version_id
        or rebuilt.essay_version.content_sha256 != snapshot.essay_version.content_sha256
        or rebuilt.essay_version.locator_manifest_sha256 != snapshot.essay_version.locator_manifest_sha256
        or rebuilt.essay_version.word_count != snapshot.essay_version.word_count
    ):
        return CriterionFailure(None, FailureCategory.LINEAGE_MISMATCH)
    if not is_approved_task2_snapshot(rubric):
        return CriterionFailure(None, FailureCategory.RUBRIC_INVALID)
    if not isinstance(understanding, Task2Understanding) or understanding.status is not UnderstandingStatus.VALID:
        return CriterionFailure(None, FailureCategory.TASK_UNDERSTANDING_INVALID)
    if not isinstance(evidence, StudentEvidence) or evidence.status is not StudentEvidenceStatus.VALID:
        return CriterionFailure(None, FailureCategory.STUDENT_EVIDENCE_INVALID)
    understanding_identity = {
        "semanticVersion": understanding.semantic_version,
        "promptVersion": understanding.prompt_version,
        "output": _thaw(understanding.payload),
        "status": understanding.status.value,
        "reviewReasons": [item.value for item in understanding.review_reasons],
    }
    evidence_identity = {
        "semanticVersion": evidence.semantic_version,
        "promptVersion": evidence.prompt_version,
        "output": _thaw(evidence.payload),
        "status": evidence.status.value,
        "reviewReasons": [item.value for item in evidence.review_reasons],
    }
    if digest(understanding_identity) != understanding.artifact_sha256:
        return CriterionFailure(None, FailureCategory.TASK_UNDERSTANDING_INVALID)
    if digest(evidence_identity) != evidence.artifact_sha256:
        return CriterionFailure(None, FailureCategory.STUDENT_EVIDENCE_INVALID)
    essay = snapshot.essay_version
    understanding_payload = understanding.payload
    evidence_payload = evidence.payload
    if (
        understanding_payload["submissionSnapshotId"] != snapshot.submission_snapshot_id
        or understanding_payload["essayVersionId"] != essay.essay_version_id
        or understanding_payload["locatorManifestSha256"] != essay.locator_manifest_sha256
        or evidence_payload["submissionSnapshotId"] != snapshot.submission_snapshot_id
        or evidence_payload["essayVersionId"] != essay.essay_version_id
        or evidence_payload["locatorManifestSha256"] != essay.locator_manifest_sha256
        or evidence_payload["task2UnderstandingSha256"] != understanding.artifact_sha256
    ):
        return CriterionFailure(None, FailureCategory.LINEAGE_MISMATCH)
    expected_route = route_for("task2", "criterion_scoring")
    if (
        expected_route is None
        or not isinstance(resolution, RouteResolution)
        or resolution.route != expected_route
        or not resolution.ok
        or resolution.contract is None
    ):
        nested = resolution.failure.code if isinstance(resolution, RouteResolution) and resolution.failure else None
        return CriterionFailure(None, FailureCategory.ROUTE_UNAVAILABLE, nested)
    try:
        prompt_digest = prompt_version()
        payloads = {
            criterion: build_criterion_input(snapshot, understanding, evidence, rubric, criterion)
            for criterion in _CRITERIA
        }
    except (CriterionScoringError, KeyError, TypeError, ValueError):
        return CriterionFailure(None, FailureCategory.INPUT_PROJECTION_INVALID)
    contract = resolution.contract
    identity = CriterionScoringExecutionIdentity(
        provider_id=contract.provider_id,
        route_id=expected_route.route_id,
        configuration_key=expected_route.configuration_key,
        model_id=contract.model.model_id,
        model_version=None,
        base_url=contract.snapshot.base_url,
        prompt_version=prompt_digest,
        schema_version=CRITERION_SCORING_SCHEMA_VERSION,
        semantic_version=CRITERION_SCORING_SEMANTIC_VERSION,
        repair_policy_version=CRITERION_SCORING_REPAIR_POLICY_VERSION,
        rubric_id=rubric.rubric_id,
        rubric_version=rubric.version,
        runtime_content_sha256=rubric.runtime_content_sha256,
        submission_snapshot_id=snapshot.submission_snapshot_id,
        essay_version_id=essay.essay_version_id,
        locator_manifest_sha256=essay.locator_manifest_sha256,
        task2_understanding_sha256=understanding.artifact_sha256,
        student_evidence_sha256=evidence.artifact_sha256,
    )
    return _Preflight(snapshot, understanding, evidence, rubric, contract, identity, _freeze(payloads))


def _parse_object(text: str) -> dict[str, Any]:
    try:
        value = json.loads(text)
    except (TypeError, json.JSONDecodeError) as exc:
        raise CriterionOutputError("Criterion output is not valid JSON.") from exc
    if not isinstance(value, dict):
        raise CriterionOutputError("Criterion output must be an object.")
    return value


def _observation_index(evidence: StudentEvidence, criterion: str) -> dict[str, Mapping[str, Any]]:
    return {
        item["observationId"]: item
        for item in evidence.payload["observations"]
        if item["criterion"] == criterion
    }


def _claim_index(rubric: StructuredRubricSnapshot, criterion: str) -> tuple[dict[tuple[int, str], Any], dict[tuple[int, str], set[str]]]:
    selected = next(item for item in rubric.criteria if item.code == criterion)
    claims: dict[tuple[int, str], Any] = {}
    dimensions: dict[tuple[int, str], set[str]] = {}
    for anchor in selected.anchors:
        for claim in anchor.official_claims:
            claims[(anchor.band, claim.claim_id)] = claim
        dimension_map = anchor.derived_internal.get("performance_dimensions", {})
        if not isinstance(dimension_map, Mapping):
            continue  # Special-condition bands explicitly declare no dimensions.
        for dimension_id, claim_ids in dimension_map.items():
            # The rubric nests dimension -> claim IDs under performance_dimensions.
            # Other derived-internal keys are annotations, not dimensions.
            if isinstance(claim_ids, (list, tuple)):
                for claim_id in claim_ids:
                    dimensions.setdefault((anchor.band, str(claim_id)), set()).add(str(dimension_id))
    return claims, dimensions


def _finding_key(finding: Mapping[str, Any]) -> tuple[Any, ...]:
    role_order = {"ANCHOR_SUPPORT": 0, "HIGHER_BAND_BOUNDARY": 1, "UNASSESSABLE_SUPPORT": 2}
    return (
        role_order[finding["role"]],
        finding["anchorBand"],
        finding["dimensionId"] or "",
        finding["claimFit"],
        canonical_json({
            "officialClaimIds": finding["officialClaimIds"],
            "evidenceRefs": finding["evidenceRefs"],
        }),
    )


def _canonical_findings(
    raw_findings: Sequence[Mapping[str, Any]],
    *,
    criterion: str,
    status: AssessmentStatus,
    unassessable_reasons: set[str],
    evidence: StudentEvidence,
    rubric: StructuredRubricSnapshot,
) -> tuple[Mapping[str, Any], ...]:
    observations = _observation_index(evidence, criterion)
    claims, dimensions = _claim_index(rubric, criterion)
    semantic_seen: set[str] = set()
    canonical: list[dict[str, Any]] = []
    for raw in raw_findings:
        role = raw["role"]
        fit = raw["claimFit"]
        if role == "ANCHOR_SUPPORT" and fit not in {"DEMONSTRATED", "PARTIALLY_DEMONSTRATED"}:
            raise CriterionOutputError("Anchor support claim fit is invalid.")
        if role == "HIGHER_BAND_BOUNDARY" and fit not in {"NOT_DEMONSTRATED", "CONTRADICTED"}:
            raise CriterionOutputError("Higher boundary claim fit is invalid.")
        if role == "UNASSESSABLE_SUPPORT":
            if status is not AssessmentStatus.UNASSESSABLE or fit != "RELATED_EVIDENCE_ONLY":
                raise CriterionOutputError("Unassessable support is invalid.")
            if "UNPROVABLE_SPECIAL_CONDITION" not in unassessable_reasons:
                raise CriterionOutputError("Unassessable support requires an unprovable condition.")
        elif fit == "RELATED_EVIDENCE_ONLY":
            raise CriterionOutputError("Related-only evidence requires unassessable support.")
        if status is AssessmentStatus.ASSESSED and role == "UNASSESSABLE_SUPPORT":
            raise CriterionOutputError("Assessed results cannot contain unassessable support.")
        band = raw["anchorBand"]
        claim_ids = sorted(raw["officialClaimIds"])
        if len(claim_ids) != len(set(claim_ids)) or any((band, claim_id) not in claims for claim_id in claim_ids):
            raise CriterionOutputError("Finding official claims are invalid.")
        dimension_id = raw["dimensionId"]
        if dimension_id is not None and any(dimension_id not in dimensions.get((band, claim_id), set()) for claim_id in claim_ids):
            raise CriterionOutputError("Finding dimension mapping is invalid.")
        refs: list[dict[str, Any]] = []
        ref_seen: set[str] = set()
        for raw_ref in raw["evidenceRefs"]:
            observation_id = raw_ref["observationId"]
            observation = observations.get(observation_id)
            if observation is None:
                raise CriterionOutputError("Finding evidence is not in the criterion evidence slice.")
            span_ids = sorted(raw_ref["spanIds"])
            if len(span_ids) != len(set(span_ids)):
                raise CriterionOutputError("Finding span IDs are duplicated.")
            available = {span["spanId"] for span in observation["evidenceSpans"]}
            if observation["scope"] == "SPAN":
                if not span_ids or not set(span_ids).issubset(available):
                    raise CriterionOutputError("Span evidence reference is invalid.")
            elif span_ids:
                raise CriterionOutputError("Non-span evidence cannot cite span IDs.")
            ref = {"observationId": observation_id, "spanIds": span_ids}
            marker = canonical_json(ref)
            if marker in ref_seen:
                raise CriterionOutputError("Finding evidence references are duplicated.")
            ref_seen.add(marker)
            refs.append(ref)
        refs.sort(key=lambda item: (item["observationId"], tuple(item["spanIds"])))
        finding = {
            "role": role,
            "anchorBand": band,
            "dimensionId": dimension_id,
            "claimFit": fit,
            "officialClaimIds": claim_ids,
            "evidenceRefs": refs,
        }
        marker = canonical_json(finding)
        if marker in semantic_seen:
            raise CriterionOutputError("Semantic findings are duplicated.")
        semantic_seen.add(marker)
        canonical.append(finding)
    canonical.sort(key=_finding_key)
    return tuple(_freeze({"findingId": f"f{index:04d}", **finding}) for index, finding in enumerate(canonical, start=1))


def _canonical_hooks(raw_hooks: Sequence[Mapping[str, str]], evidence: StudentEvidence, criterion: str) -> tuple[Mapping[str, str], ...]:
    observations = _observation_index(evidence, criterion)
    allowed = {
        tuple(sorted((hook["leftObservationId"], hook["rightObservationId"])))
        for hook in evidence.payload["contradictionHooks"]
        if hook["leftObservationId"] in observations and hook["rightObservationId"] in observations
    }
    hooks: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for raw in raw_hooks:
        marker = tuple(sorted((raw["leftObservationId"], raw["rightObservationId"])))
        if marker not in allowed or marker in seen:
            raise CriterionOutputError("Used contradiction hook is invalid.")
        seen.add(marker)
        hooks.append({"leftObservationId": marker[0], "rightObservationId": marker[1]})
    hooks.sort(key=lambda item: (item["leftObservationId"], item["rightObservationId"]))
    return tuple(_freeze(item) for item in hooks)


def _validate_assessed(
    value: Mapping[str, Any],
    findings: Sequence[Mapping[str, Any]],
    hooks: Sequence[Mapping[str, str]],
    evidence: StudentEvidence,
    word_count: int,
    criterion: str,
) -> float:
    lower, upper = value["lowerBand"], value["upperBand"]
    if upper < lower or upper - lower > 1:
        raise CriterionOutputError("Candidate band range must be exact or adjacent.")
    if any(item["role"] == "UNASSESSABLE_SUPPORT" for item in findings):
        raise CriterionOutputError("Unassessable support cannot support a range.")
    anchors = {
        item["anchorBand"]
        for item in findings
        if item["role"] == "ANCHOR_SUPPORT"
    }
    if not set(range(lower, upper + 1)).issubset(anchors):
        raise CriterionOutputError("Selected anchors lack separate support.")
    if upper < 9 and not any(
        item["role"] == "HIGHER_BAND_BOUNDARY" and item["anchorBand"] == upper + 1
        for item in findings
    ):
        raise CriterionOutputError("The next higher band boundary is missing.")
    if any(item["role"] == "HIGHER_BAND_BOUNDARY" and item["anchorBand"] != upper + 1 for item in findings):
        raise CriterionOutputError("Higher boundary targets an invalid anchor.")
    reasons = set(value["confidenceReasons"])
    adjacent = lower != upper
    if adjacent != ("ADJACENT_ANCHOR_MIX" in reasons):
        raise CriterionOutputError("Adjacent range shape and confidence reasons differ.")
    if value["confidence"] == "HIGH":
        if "RANGE_WELL_SUPPORTED" not in reasons or reasons & _LIMITATION_REASONS:
            raise CriterionOutputError("High confidence reasons are inconsistent.")
    elif not reasons & _LIMITATION_REASONS:
        raise CriterionOutputError("Medium or low confidence lacks a limitation reason.")
    if bool(hooks) != ("WITHIN_CRITERION_TENSION" in reasons):
        raise CriterionOutputError("Assessed contradiction hooks and confidence differ.")
    if lower == 0:
        global_ids = {
            observation_id
            for observation_id, observation in _observation_index(evidence, criterion).items()
            if observation["scope"] == "GLOBAL"
        }
        band_zero_refs = {
            ref["observationId"]
            for item in findings
            if item["role"] == "ANCHOR_SUPPORT" and item["anchorBand"] == 0
            for ref in item["evidenceRefs"]
        }
        if not band_zero_refs or not band_zero_refs.issubset(global_ids):
            raise CriterionOutputError("Band 0 requires validated global evidence.")
    word_claim = f"{criterion}-1-1"
    cited_word_claim = any(word_claim in item["officialClaimIds"] for item in findings)
    if word_count <= 20 and lower != 0:
        if (lower, upper) != (1, 1) or not cited_word_claim:
            raise CriterionOutputError("Frozen word count requires the Band 1 condition.")
    elif word_count > 20 and cited_word_claim:
        raise CriterionOutputError("Band 1 word-count claim contradicts frozen word count.")
    return (lower + upper) / 2


def _validate_unassessable(
    value: Mapping[str, Any],
    findings: Sequence[Mapping[str, Any]],
    hooks: Sequence[Mapping[str, str]],
) -> None:
    reasons = set(value["unassessableReasons"])
    if "NON_ADJACENT_BAND_UNCERTAINTY" in reasons:
        anchors = {
            item["anchorBand"] for item in findings if item["role"] == "ANCHOR_SUPPORT"
        }
        if len(anchors) < 2 or max(anchors) - min(anchors) <= 1:
            raise CriterionOutputError("Non-adjacent uncertainty lacks boundary proof.")
    if "EVIDENCE_TENSION_PREVENTS_RANGE" in reasons:
        if not hooks:
            raise CriterionOutputError("Evidence tension requires a used hook.")
        referenced = {
            ref["observationId"] for item in findings for ref in item["evidenceRefs"]
        }
        if any(
            hook["leftObservationId"] not in referenced or hook["rightObservationId"] not in referenced
            for hook in hooks
        ):
            raise CriterionOutputError("Evidence tension findings do not cover hook endpoints.")
    if hooks and "EVIDENCE_TENSION_PREVENTS_RANGE" not in reasons:
        raise CriterionOutputError("Unassessable used hooks lack the tension reason.")
    special = "UNPROVABLE_SPECIAL_CONDITION" in reasons
    special_findings = [item for item in findings if item["role"] == "UNASSESSABLE_SUPPORT"]
    if special:
        if any(
            item["role"] != "UNASSESSABLE_SUPPORT"
            and "ALL-0-1" in item["officialClaimIds"]
            for item in findings
        ):
            raise CriterionOutputError("An unprovable special condition cannot form a band boundary.")
    elif special_findings:
        raise CriterionOutputError("Unassessable support is restricted to unprovable conditions.")


def validate_provider_output(
    text: str,
    *,
    criterion: str,
    preflight: _Preflight,
) -> CriterionAssessment:
    value = _parse_object(text)
    errors = list(Draft202012Validator(output_schema()).iter_errors(value))
    if errors:
        # Diagnostic traversal only; the authoritative rejection above is unchanged.
        # Ignore the other discriminated branch when selecting a useful field path.
        schema = output_schema()
        status_value = value.get("status")
        branch = {"ASSESSED": 0, "UNASSESSABLE": 1}.get(status_value) if isinstance(status_value, str) else None
        selected = errors[0]
        if branch is not None:
            def leaves(error):
                if not error.context:
                    return [error]
                return [leaf for child in error.context for leaf in leaves(child)]
            relevant = [e for e in leaves(selected) if list(e.absolute_schema_path)[:2] == ["oneOf", branch]]
            if relevant:
                selected = relevant[0]
        keys = set(schema["$defs"]["common"]["properties"])
        keys.update(schema["$defs"]["common"]["required"])
        for name in ("finding", "hook", "evidenceRef"):
            keys.update(schema["$defs"][name]["properties"])
        path = "$"
        for part in selected.absolute_path:
            if isinstance(part, int) and part >= 0:
                path += f"[{part}]"
            elif part in keys:
                path += "." + part
            else:
                break
        raise CriterionOutputError("Criterion output has an invalid schema.", path=path)
    expected_lineage = _lineage(preflight.snapshot, preflight.understanding, preflight.evidence, preflight.rubric, criterion)
    if any(value[key] != expected for key, expected in expected_lineage.items()):
        key = next(key for key, expected in expected_lineage.items() if value[key] != expected)
        raise CriterionOutputError("Criterion output lineage differs.", path="$." + key)
    status = AssessmentStatus(value["status"])
    unassessable_reasons = set(value["unassessableReasons"])
    findings = _canonical_findings(
        value["findings"], criterion=criterion, status=status,
        unassessable_reasons=unassessable_reasons,
        evidence=preflight.evidence, rubric=preflight.rubric,
    )
    hooks = _canonical_hooks(value["usedContradictionHooks"], preflight.evidence, criterion)
    estimated: float | None = None
    if status is AssessmentStatus.ASSESSED:
        estimated = _validate_assessed(
            value, findings, hooks, preflight.evidence,
            preflight.snapshot.essay_version.word_count, criterion,
        )
    else:
        _validate_unassessable(value, findings, hooks)
    assessment = CriterionAssessment(
        criterion=criterion,
        status=status,
        lower_band=value["lowerBand"],
        upper_band=value["upperBand"],
        estimated_band=estimated,
        confidence=value["confidence"],
        confidence_reasons=tuple(sorted(value["confidenceReasons"])),
        unassessable_reasons=tuple(sorted(value["unassessableReasons"])),
        findings=tuple(findings),
        used_contradiction_hooks=tuple(hooks),
        authoritative_lineage=_freeze(expected_lineage),
        execution_identity=preflight.identity,
    )
    assessment_hash = digest(assessment.content(include_hash=False))
    return CriterionAssessment(**{**assessment.__dict__, "assessment_sha256": assessment_hash})


class CriterionScoringService:
    """Independent four-call P2-03 service; it has no score cache or UI integration."""

    def __init__(self, transport: ProviderTransport, *, on_criterion=None) -> None:
        self._transport = transport
        self._on_criterion = on_criterion or (lambda event: None)

    def _run_criterion(
        self,
        criterion: str,
        preflight: _Preflight,
        recorder: CriterionScoringAttemptRecorder,
    ) -> CriterionAssessment | CriterionFailure:
        prompt = prompt_text()
        payload = _thaw(preflight.payloads[criterion])
        if 'calibrationReview' in payload:
            from .calibration import RE_SCORE_INSTRUCTION
            prompt += '\n' + RE_SCORE_INSTRUCTION
        messages: list[dict[str, Any]] = [
            {"role": "system", "content": prompt},
            {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
        ]
        for attempt in (1, 2):
            started = monotonic()
            try:
                result = self._transport.call(
                    preflight.contract,
                    ProviderCallRequest(
                        messages=messages,
                        display_name=f"Task 2 {criterion} criterion assessment",
                        require_json_object=True,
                        validate_json_object=False,
                        stream=True,
                        temperature=0.0,
                    ),
                )
            except Exception:
                elapsed = int((monotonic() - started) * 1000)
                recorder.record(criterion, attempt, elapsed, "UNEXPECTED", None)
                return CriterionFailure(criterion, FailureCategory.UNEXPECTED_CRITERION_FAILURE)
            elapsed = int((monotonic() - started) * 1000)
            if not isinstance(result, ProviderCallResult):
                recorder.record(criterion, attempt, elapsed, "UNEXPECTED", None)
                return CriterionFailure(criterion, FailureCategory.UNEXPECTED_CRITERION_FAILURE)
            if not result.ok:
                recorder.record(criterion, attempt, elapsed, "PROVIDER_FAILURE", result)
                code = result.failure.code if result.failure else ProviderFailureCode.PROVIDER_FAILURE
                return CriterionFailure(criterion, FailureCategory.PROVIDER_FAILURE, code)
            try:
                assessment = validate_provider_output(result.content, criterion=criterion, preflight=preflight)
            except CriterionOutputError as error:
                recorder.record(criterion, attempt, elapsed, "INVALID", result, error)
                if attempt == 2:
                    return CriterionFailure(criterion, FailureCategory.OUTPUT_INVALID_AFTER_REPAIR)
                messages = messages + [{"role": "assistant", "content": result.content}, {
                    "role": "user",
                    "content": (
                        "Repair the prior output. Return exactly one JSON object matching "
                        "requiredOutputSchema and every authority, evidence, boundary, confidence, "
                        "hook, and special-condition rule. Do not add explanations. "
                        f"Validation: {error.diagnostic_code} at {error.diagnostic_path or '$'}. "
                        f"Rule: {str(error)}"
                    ),
                }]
                continue
            recorder.record(criterion, attempt, elapsed, "VALID", result)
            return assessment
        return CriterionFailure(criterion, FailureCategory.OUTPUT_INVALID_AFTER_REPAIR)

    def run(
        self,
        snapshot: SubmissionSnapshot,
        understanding: Task2Understanding,
        evidence: StudentEvidence,
        rubric: StructuredRubricSnapshot,
        resolution: RouteResolution,
        *, calibration_review=None,
    ) -> CriterionScoringOutcome:
        recorder = CriterionScoringAttemptRecorder()
        try:
            prepared = _preflight(snapshot, understanding, evidence, rubric, resolution)
            if calibration_review is not None and not isinstance(prepared, CriterionFailure):
                from dataclasses import replace
                from .calibration import CalibrationReview, RE_SCORE_INSTRUCTION
                if not isinstance(calibration_review, CalibrationReview):
                    raise ValueError('Typed calibration context required.')
                calibration_review.validate(snapshot, rubric)
                payloads = _thaw(prepared.payloads)
                for criterion in _CRITERIA:
                    payloads[criterion]['calibrationReview'] = calibration_review.criteria[criterion]
                prepared = replace(prepared, payloads=_freeze(payloads), identity=replace(prepared.identity,
                    prompt_version=digest({'base': prepared.identity.prompt_version,
                        'instruction': RE_SCORE_INSTRUCTION, 'review': calibration_review.review_sha256})))
        except Exception:
            prepared = CriterionFailure(None, FailureCategory.INPUT_PROJECTION_INVALID)
        if isinstance(prepared, CriterionFailure):
            record = CriterionScoringExecutionRecord(
                RunStatus.FAILED, MappingProxyType({}), (prepared,), None, (),
            )
            return CriterionScoringOutcome(RunStatus.FAILED, (), (prepared,), None, record)
        results: dict[str, CriterionAssessment | CriterionFailure] = {}
        try:
            with ThreadPoolExecutor(max_workers=4, thread_name_prefix="criterion-scoring") as executor:
                futures = {
                    executor.submit(self._run_criterion, criterion, prepared, recorder): criterion
                    for criterion in _CRITERIA
                }
                for future in as_completed(futures):
                    criterion = futures[future]
                    try:
                        results[criterion] = future.result()
                    except Exception:
                        results[criterion] = CriterionFailure(
                            criterion, FailureCategory.UNEXPECTED_CRITERION_FAILURE
                        )
                    value = results[criterion]
                    self._on_criterion({"criterion": criterion,
                        "status": value.status.value if isinstance(value, CriterionAssessment) else "FAILED"})
        except Exception:
            failure = CriterionFailure(None, FailureCategory.ORCHESTRATION_FAILURE)
            record = CriterionScoringExecutionRecord(
                RunStatus.FAILED, MappingProxyType({}), (failure,), None, recorder.snapshot(),
            )
            return CriterionScoringOutcome(RunStatus.FAILED, (), (failure,), None, record)
        if set(results) != set(_CRITERIA):
            failure = CriterionFailure(None, FailureCategory.ORCHESTRATION_FAILURE)
            record = CriterionScoringExecutionRecord(
                RunStatus.FAILED, MappingProxyType({}), (failure,), None, recorder.snapshot(),
            )
            return CriterionScoringOutcome(RunStatus.FAILED, (), (failure,), None, record)
        assessments = tuple(
            results[criterion] for criterion in _CRITERIA if isinstance(results[criterion], CriterionAssessment)
        )
        failures = tuple(
            results[criterion] for criterion in _CRITERIA if isinstance(results[criterion], CriterionFailure)
        )
        all_assessed = len(assessments) == 4 and all(item.status is AssessmentStatus.ASSESSED for item in assessments)
        bundle: CriterionAssessmentBundle | None = None
        if all_assessed:
            bundle_payload = {
                "version": CRITERION_ASSESSMENT_BUNDLE_VERSION,
                "sharedLineage": prepared.identity.content(),
                "assessmentSha256ByCriterion": {
                    item.criterion: item.assessment_sha256 for item in assessments
                },
            }
            bundle = CriterionAssessmentBundle(assessments, digest(bundle_payload))
            status = RunStatus.COMPLETE
        elif assessments:
            status = RunStatus.INCOMPLETE
        else:
            status = RunStatus.FAILED
        hashes = MappingProxyType({item.criterion: item.assessment_sha256 for item in assessments})
        record = CriterionScoringExecutionRecord(
            status=status,
            assessment_sha256_by_criterion=hashes,
            failures=failures,
            bundle_sha256=bundle.bundle_sha256 if bundle else None,
            attempts=recorder.snapshot(),
        )
        return CriterionScoringOutcome(status, assessments, failures, bundle, record)
