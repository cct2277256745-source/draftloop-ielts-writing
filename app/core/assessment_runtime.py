"""Assessment-foundation orchestration that excludes legacy report score authority."""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Sequence

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
from .criterion_scoring import CriterionFailure, CriterionScoringService, RunStatus
from .providers import RouteResolution
from .rubric import StructuredRubricSnapshot
from .student_evidence import StudentEvidence
from .submission import SubmissionSnapshot
from .task2_understanding import Task2Understanding


class AssessmentFoundationStatus(str, Enum):
    COMPLETE = "COMPLETE"
    REVIEW_REQUIRED = "REVIEW_REQUIRED"
    FAILED = "FAILED"


@dataclass(frozen=True)
class AssessmentFoundationOutcome:
    status: AssessmentFoundationStatus
    locked_score: LockedScoreSnapshot | None
    diagnosis: CoreDiagnosis | None
    review: ReviewGateDecision | None
    criterion_failures: tuple[CriterionFailure, ...]

    def __post_init__(self) -> None:
        if self.status is AssessmentFoundationStatus.COMPLETE:
            if self.locked_score is None or self.diagnosis is None or self.review is None:
                raise ValueError("A complete assessment requires locked score, diagnosis, and review evidence.")
            if self.review.status is not ReviewStatus.PASS or self.criterion_failures:
                raise ValueError("A complete assessment cannot carry review or criterion failures.")
        elif self.locked_score is not None or self.diagnosis is not None:
            raise ValueError("Incomplete assessment outcomes cannot publish score or diagnosis artifacts.")


class Task2AssessmentFoundationService:
    """Turn accepted P2 artifacts into one reviewed and code-finalized Task 2 result."""

    def __init__(self, criterion_service: CriterionScoringService) -> None:
        self._criterion_service = criterion_service

    def run(
        self,
        snapshot: SubmissionSnapshot,
        understanding: Task2Understanding,
        evidence: StudentEvidence,
        rubric: StructuredRubricSnapshot,
        resolution: RouteResolution,
        *,
        target_band: float | None = None,
        comparison: FinalizationBundle | None = None,
        external_review_reasons: Sequence[ReviewReason] = (),
    ) -> AssessmentFoundationOutcome:
        scoring = self._criterion_service.run(
            snapshot, understanding, evidence, rubric, resolution
        )
        if scoring.status is RunStatus.FAILED or scoring.bundle is None:
            review: ReviewGateDecision | None = None
            if scoring.assessments:
                review = make_review_decision(
                    scoring.execution_record.bundle_sha256
                    or "incomplete:" + snapshot.snapshot_sha256,
                    (ReviewReason.INCOMPLETE,),
                )
            status = (
                AssessmentFoundationStatus.REVIEW_REQUIRED
                if scoring.status is RunStatus.INCOMPLETE
                else AssessmentFoundationStatus.FAILED
            )
            return AssessmentFoundationOutcome(
                status, None, None, review, tuple(scoring.failures)
            )
        bundle = FinalizationBundle.from_task2_bundle(scoring.bundle)
        review = review_scores(
            bundle,
            comparison=comparison,
            external_reasons=external_review_reasons,
        )
        if review.status is ReviewStatus.REVIEW_REQUIRED:
            return AssessmentFoundationOutcome(
                AssessmentFoundationStatus.REVIEW_REQUIRED,
                None,
                None,
                review,
                tuple(scoring.failures),
            )
        locked = finalize_scores(bundle, review)
        diagnosis = build_core_diagnosis(locked, bundle, target_band=target_band)
        return AssessmentFoundationOutcome(
            AssessmentFoundationStatus.COMPLETE,
            locked,
            diagnosis,
            review,
            (),
        )
