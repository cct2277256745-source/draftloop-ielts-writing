"""Code-owned score review, deterministic finalization, and immutable score identity."""
from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal, ROUND_HALF_UP
from enum import Enum
import math
from types import MappingProxyType
from typing import Any, Mapping, Sequence

from .submission import digest


SCORE_CALCULATION_POLICY_VERSION = "ielts-four-criterion-half-up-v1"
SCORING_REVIEW_POLICY_VERSION = "scoring-review-gate-v1"
LOCKED_SCORE_SNAPSHOT_VERSION = "locked-score-snapshot-v1"
FINALIZATION_BUNDLE_VERSION = "finalization-bundle-v1"
_TASK_CRITERIA = {
    "task1": ("TA", "CC", "LR", "GRA"),
    "task2": ("TR", "CC", "LR", "GRA"),
}
_CONFIDENCE_ORDER = {"LOW": 0, "MEDIUM": 1, "HIGH": 2}


class AssessmentFinalizationError(ValueError):
    """A score artifact is incomplete, internally inconsistent, or mutated."""


class ReviewStatus(str, Enum):
    PASS = "PASS"
    REVIEW_REQUIRED = "REVIEW_REQUIRED"


class ReviewReason(str, Enum):
    INCOMPLETE = "INCOMPLETE"
    LOW_CONFIDENCE = "LOW_CONFIDENCE"
    DISAGREEMENT = "DISAGREEMENT"
    EVIDENCE_CONFLICT = "EVIDENCE_CONFLICT"
    TASK_UNDERSTANDING_CONFLICT = "TASK_UNDERSTANDING_CONFLICT"
    TASK1_FACT_CONFLICT = "TASK1_FACT_CONFLICT"
    COMPARISON_NOT_COMPARABLE = "COMPARISON_NOT_COMPARABLE"
    SCHEMA_INTEGRITY = "SCHEMA_INTEGRITY"


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


def _criterion_order(task_type: str, criterion: str) -> int:
    try:
        return _TASK_CRITERIA[task_type].index(criterion)
    except (KeyError, ValueError) as exc:
        raise AssessmentFinalizationError("Criterion does not belong to the task.") from exc


def _valid_half_band(value: float) -> bool:
    return (
        not isinstance(value, bool)
        and isinstance(value, (int, float))
        and math.isfinite(float(value))
        and 0.0 <= float(value) <= 9.0
        and math.isclose(float(value) * 2, round(float(value) * 2), abs_tol=1e-9)
    )


def round_overall(values: Sequence[float]) -> float:
    """Average exactly four valid half-band scores and round ties upward to 0.5."""
    if len(values) != 4 or any(not _valid_half_band(value) for value in values):
        raise AssessmentFinalizationError("Overall calculation requires four valid half-band scores.")
    average = sum((Decimal(str(float(value))) for value in values), Decimal("0")) / Decimal("4")
    return float((average * Decimal("2")).quantize(Decimal("1"), rounding=ROUND_HALF_UP) / Decimal("2"))


@dataclass(frozen=True)
class FinalizableAssessment:
    criterion: str
    lower_band: int
    upper_band: int
    estimated_band: float
    confidence: str
    confidence_reasons: tuple[str, ...]
    findings: tuple[Mapping[str, Any], ...] = field(repr=False)
    used_contradiction_hooks: tuple[Mapping[str, Any], ...] = field(repr=False)
    authoritative_lineage: Mapping[str, str] = field(repr=False)
    execution_identity: Mapping[str, Any] = field(repr=False)
    source_assessment_sha256: str = ""

    def __post_init__(self) -> None:
        if not isinstance(self.lower_band, int) or not isinstance(self.upper_band, int):
            raise AssessmentFinalizationError("Criterion bounds must be whole bands.")
        if not 0 <= self.lower_band <= self.upper_band <= 9 or self.upper_band - self.lower_band > 1:
            raise AssessmentFinalizationError("Criterion bounds must be exact or adjacent whole bands.")
        expected = (self.lower_band + self.upper_band) / 2
        if not _valid_half_band(self.estimated_band) or not math.isclose(self.estimated_band, expected):
            raise AssessmentFinalizationError("Criterion estimated band is not code-derivable from its bounds.")
        if self.confidence not in _CONFIDENCE_ORDER:
            raise AssessmentFinalizationError("Criterion confidence is invalid.")
        if not self.source_assessment_sha256:
            raise AssessmentFinalizationError("Criterion assessment identity is required.")
        object.__setattr__(self, "confidence_reasons", tuple(sorted(set(self.confidence_reasons))))
        object.__setattr__(self, "findings", tuple(_freeze(item) for item in self.findings))
        object.__setattr__(
            self, "used_contradiction_hooks", tuple(_freeze(item) for item in self.used_contradiction_hooks)
        )
        object.__setattr__(self, "authoritative_lineage", _freeze(self.authoritative_lineage))
        object.__setattr__(self, "execution_identity", _freeze(self.execution_identity))

    @classmethod
    def from_assessment(cls, value: Any) -> "FinalizableAssessment":
        status = getattr(value, "status", None)
        status_value = getattr(status, "value", status)
        if status_value != "ASSESSED":
            raise AssessmentFinalizationError("Only assessed criterion records can be finalized.")
        try:
            raw_content = value.content(include_hash=False)
        except (AttributeError, TypeError) as exc:
            raise AssessmentFinalizationError("Criterion assessment cannot be serialized.") from exc
        if digest(raw_content) != value.assessment_sha256:
            raise AssessmentFinalizationError("Criterion assessment hash mismatch.")
        return cls(
            criterion=value.criterion,
            lower_band=value.lower_band,
            upper_band=value.upper_band,
            estimated_band=value.estimated_band,
            confidence=value.confidence,
            confidence_reasons=tuple(value.confidence_reasons),
            findings=tuple(value.findings),
            used_contradiction_hooks=tuple(value.used_contradiction_hooks),
            authoritative_lineage=value.authoritative_lineage,
            execution_identity=value.execution_identity.content(),
            source_assessment_sha256=value.assessment_sha256,
        )

    def content(self) -> dict[str, Any]:
        return {
            "criterion": self.criterion,
            "lowerBand": self.lower_band,
            "upperBand": self.upper_band,
            "estimatedBand": self.estimated_band,
            "confidence": self.confidence,
            "confidenceReasons": list(self.confidence_reasons),
            "findings": [_thaw(item) for item in self.findings],
            "usedContradictionHooks": [_thaw(item) for item in self.used_contradiction_hooks],
            "authoritativeLineage": dict(self.authoritative_lineage),
            "executionIdentity": _thaw(self.execution_identity),
            "sourceAssessmentSha256": self.source_assessment_sha256,
        }


@dataclass(frozen=True)
class FinalizationBundle:
    task_type: str
    assessments: tuple[FinalizableAssessment, ...]
    source_bundle_sha256: str
    bundle_sha256: str
    version: str = FINALIZATION_BUNDLE_VERSION

    @classmethod
    def create(
        cls,
        task_type: str,
        assessments: Sequence[Any],
        *,
        source_bundle_sha256: str,
    ) -> "FinalizationBundle":
        if task_type not in _TASK_CRITERIA or not source_bundle_sha256:
            raise AssessmentFinalizationError("Finalization bundle identity is invalid.")
        records = tuple(
            sorted(
                (FinalizableAssessment.from_assessment(item) for item in assessments),
                key=lambda item: _criterion_order(task_type, item.criterion),
            )
        )
        if tuple(item.criterion for item in records) != _TASK_CRITERIA[task_type]:
            raise AssessmentFinalizationError("Finalization requires exactly four task criteria.")
        payload = {
            "version": FINALIZATION_BUNDLE_VERSION,
            "taskType": task_type,
            "sourceBundleSha256": source_bundle_sha256,
            "assessments": [item.content() for item in records],
        }
        return cls(task_type, records, source_bundle_sha256, digest(payload))

    @classmethod
    def from_task2_bundle(cls, value: Any) -> "FinalizationBundle":
        try:
            assessments = tuple(value.assessments)
            identity = assessments[0].execution_identity.content()
            expected = digest({
                "version": value.version,
                "sharedLineage": identity,
                "assessmentSha256ByCriterion": {
                    item.criterion: item.assessment_sha256 for item in assessments
                },
            })
        except (AttributeError, IndexError, TypeError) as exc:
            raise AssessmentFinalizationError("Task 2 criterion bundle is invalid.") from exc
        if expected != value.bundle_sha256:
            raise AssessmentFinalizationError("Task 2 criterion bundle hash mismatch.")
        return cls.create("task2", assessments, source_bundle_sha256=value.bundle_sha256)

    def content(self, *, include_hash: bool = True) -> dict[str, Any]:
        value = {
            "version": self.version,
            "taskType": self.task_type,
            "sourceBundleSha256": self.source_bundle_sha256,
            "assessments": [item.content() for item in self.assessments],
        }
        if include_hash:
            value["bundleSha256"] = self.bundle_sha256
        return value


def validate_finalization_bundle(bundle: FinalizationBundle) -> None:
    if not isinstance(bundle, FinalizationBundle) or bundle.task_type not in _TASK_CRITERIA:
        raise AssessmentFinalizationError("Finalization bundle type is invalid.")
    if tuple(item.criterion for item in bundle.assessments) != _TASK_CRITERIA[bundle.task_type]:
        raise AssessmentFinalizationError("Finalization bundle criterion set is invalid.")
    if digest(bundle.content(include_hash=False)) != bundle.bundle_sha256:
        raise AssessmentFinalizationError("Finalization bundle hash mismatch.")


@dataclass(frozen=True)
class ReviewGateDecision:
    status: ReviewStatus
    reasons: tuple[ReviewReason, ...]
    bundle_sha256: str
    comparison_bundle_sha256: str | None
    policy_version: str
    decision_sha256: str

    def content(self, *, include_hash: bool = True) -> dict[str, Any]:
        value = {
            "status": self.status.value,
            "reasons": [item.value for item in self.reasons],
            "bundleSha256": self.bundle_sha256,
            "comparisonBundleSha256": self.comparison_bundle_sha256,
            "policyVersion": self.policy_version,
        }
        if include_hash:
            value["decisionSha256"] = self.decision_sha256
        return value


def review_scores(
    bundle: FinalizationBundle,
    *,
    comparison: FinalizationBundle | None = None,
    external_reasons: Sequence[ReviewReason] = (),
) -> ReviewGateDecision:
    reasons: set[ReviewReason] = set(external_reasons)
    try:
        validate_finalization_bundle(bundle)
    except AssessmentFinalizationError:
        reasons.add(ReviewReason.SCHEMA_INTEGRITY)
    if len(bundle.assessments) != 4:
        reasons.add(ReviewReason.INCOMPLETE)
    for assessment in bundle.assessments:
        if assessment.confidence == "LOW":
            reasons.add(ReviewReason.LOW_CONFIDENCE)
        if assessment.used_contradiction_hooks:
            reasons.add(ReviewReason.EVIDENCE_CONFLICT)
    comparison_sha: str | None = None
    if comparison is not None:
        comparison_sha = comparison.bundle_sha256
        try:
            validate_finalization_bundle(comparison)
            current_identity = digest(_thaw(bundle.assessments[0].execution_identity))
            comparison_identity = digest(_thaw(comparison.assessments[0].execution_identity))
            if bundle.task_type != comparison.task_type or current_identity != comparison_identity:
                reasons.add(ReviewReason.COMPARISON_NOT_COMPARABLE)
            elif any(
                current.estimated_band != prior.estimated_band
                for current, prior in zip(bundle.assessments, comparison.assessments)
            ):
                reasons.add(ReviewReason.DISAGREEMENT)
        except AssessmentFinalizationError:
            reasons.add(ReviewReason.COMPARISON_NOT_COMPARABLE)
    return make_review_decision(
        bundle.bundle_sha256,
        reasons,
        comparison_bundle_sha256=comparison_sha,
    )


def make_review_decision(
    bundle_sha256: str,
    reasons: Sequence[ReviewReason],
    *,
    comparison_bundle_sha256: str | None = None,
) -> ReviewGateDecision:
    if not isinstance(bundle_sha256, str) or not bundle_sha256:
        raise AssessmentFinalizationError("Review decision bundle identity is required.")
    ordered = tuple(sorted(set(reasons), key=lambda item: item.value))
    status = ReviewStatus.PASS if not ordered else ReviewStatus.REVIEW_REQUIRED
    payload = {
        "status": status.value,
        "reasons": [item.value for item in ordered],
        "bundleSha256": bundle_sha256,
        "comparisonBundleSha256": comparison_bundle_sha256,
        "policyVersion": SCORING_REVIEW_POLICY_VERSION,
    }
    return ReviewGateDecision(
        status, ordered, bundle_sha256, comparison_bundle_sha256,
        SCORING_REVIEW_POLICY_VERSION, digest(payload),
    )


@dataclass(frozen=True)
class LockedCriterionScore:
    criterion: str
    score: float
    lower_band: int
    upper_band: int
    confidence: str
    assessment_sha256: str

    def content(self) -> dict[str, Any]:
        return {
            "criterion": self.criterion,
            "score": self.score,
            "lowerBand": self.lower_band,
            "upperBand": self.upper_band,
            "confidence": self.confidence,
            "assessmentSha256": self.assessment_sha256,
        }


@dataclass(frozen=True)
class LockedScoreSnapshot:
    task_type: str
    criteria: tuple[LockedCriterionScore, ...]
    overall_band: float
    overall_lower_bound: float
    overall_upper_bound: float
    confidence: str
    source_bundle_sha256: str
    finalization_bundle_sha256: str
    review_decision_sha256: str
    rubric_id: str
    rubric_version: str
    runtime_content_sha256: str
    calculation_policy_version: str
    snapshot_sha256: str
    version: str = LOCKED_SCORE_SNAPSHOT_VERSION

    def content(self, *, include_hash: bool = True) -> dict[str, Any]:
        value = {
            "version": self.version,
            "taskType": self.task_type,
            "criteria": [item.content() for item in self.criteria],
            "overallBand": self.overall_band,
            "overallLowerBound": self.overall_lower_bound,
            "overallUpperBound": self.overall_upper_bound,
            "confidence": self.confidence,
            "sourceBundleSha256": self.source_bundle_sha256,
            "finalizationBundleSha256": self.finalization_bundle_sha256,
            "reviewDecisionSha256": self.review_decision_sha256,
            "rubricId": self.rubric_id,
            "rubricVersion": self.rubric_version,
            "runtimeContentSha256": self.runtime_content_sha256,
            "calculationPolicyVersion": self.calculation_policy_version,
        }
        if include_hash:
            value["snapshotSha256"] = self.snapshot_sha256
        return value

    def score_by_criterion(self) -> Mapping[str, float]:
        return MappingProxyType({item.criterion: item.score for item in self.criteria})


def finalize_scores(
    bundle: FinalizationBundle,
    review: ReviewGateDecision,
) -> LockedScoreSnapshot:
    validate_finalization_bundle(bundle)
    if (
        not isinstance(review, ReviewGateDecision)
        or digest(review.content(include_hash=False)) != review.decision_sha256
        or review.bundle_sha256 != bundle.bundle_sha256
        or review.status is not ReviewStatus.PASS
        or review.reasons
    ):
        raise AssessmentFinalizationError("Scores cannot be finalized before the review gate passes.")
    criteria = tuple(
        LockedCriterionScore(
            item.criterion,
            item.estimated_band,
            item.lower_band,
            item.upper_band,
            item.confidence,
            item.source_assessment_sha256,
        )
        for item in bundle.assessments
    )
    identity = _thaw(bundle.assessments[0].execution_identity)
    required_identity = ("rubricId", "rubricVersion", "runtimeContentSha256")
    if any(not isinstance(identity.get(key), str) or not identity[key] for key in required_identity):
        raise AssessmentFinalizationError("Rubric identity is missing from score finalization.")
    confidence = min((item.confidence for item in criteria), key=lambda item: _CONFIDENCE_ORDER[item])
    values = [item.score for item in criteria]
    lower_values = [float(item.lower_band) for item in criteria]
    upper_values = [float(item.upper_band) for item in criteria]
    partial = LockedScoreSnapshot(
        task_type=bundle.task_type,
        criteria=criteria,
        overall_band=round_overall(values),
        overall_lower_bound=round_overall(lower_values),
        overall_upper_bound=round_overall(upper_values),
        confidence=confidence,
        source_bundle_sha256=bundle.source_bundle_sha256,
        finalization_bundle_sha256=bundle.bundle_sha256,
        review_decision_sha256=review.decision_sha256,
        rubric_id=identity["rubricId"],
        rubric_version=identity["rubricVersion"],
        runtime_content_sha256=identity["runtimeContentSha256"],
        calculation_policy_version=SCORE_CALCULATION_POLICY_VERSION,
        snapshot_sha256="",
    )
    snapshot = LockedScoreSnapshot(**{**partial.__dict__, "snapshot_sha256": digest(partial.content(include_hash=False))})
    validate_locked_score_snapshot(snapshot)
    return snapshot


def validate_locked_score_snapshot(snapshot: LockedScoreSnapshot) -> None:
    if not isinstance(snapshot, LockedScoreSnapshot) or snapshot.task_type not in _TASK_CRITERIA:
        raise AssessmentFinalizationError("Locked score snapshot type is invalid.")
    if tuple(item.criterion for item in snapshot.criteria) != _TASK_CRITERIA[snapshot.task_type]:
        raise AssessmentFinalizationError("Locked score criterion set is invalid.")
    if snapshot.calculation_policy_version != SCORE_CALCULATION_POLICY_VERSION:
        raise AssessmentFinalizationError("Locked score calculation policy is unsupported.")
    if snapshot.overall_band != round_overall([item.score for item in snapshot.criteria]):
        raise AssessmentFinalizationError("Locked score overall was mutated.")
    if snapshot.overall_lower_bound != round_overall([float(item.lower_band) for item in snapshot.criteria]):
        raise AssessmentFinalizationError("Locked score lower bound was mutated.")
    if snapshot.overall_upper_bound != round_overall([float(item.upper_band) for item in snapshot.criteria]):
        raise AssessmentFinalizationError("Locked score upper bound was mutated.")
    if digest(snapshot.content(include_hash=False)) != snapshot.snapshot_sha256:
        raise AssessmentFinalizationError("Locked score snapshot hash mismatch.")


def assert_consumer_scores(
    snapshot: LockedScoreSnapshot,
    *,
    overall_band: float,
    criterion_scores: Mapping[str, float],
) -> None:
    """Reject report/UI/PDF score projections that differ from the locked snapshot."""
    validate_locked_score_snapshot(snapshot)
    if overall_band != snapshot.overall_band or dict(criterion_scores) != dict(snapshot.score_by_criterion()):
        raise AssessmentFinalizationError("A downstream consumer attempted to replace locked scores.")
