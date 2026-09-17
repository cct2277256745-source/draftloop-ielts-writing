"""Fail-closed workflow outcomes for the grading pipeline."""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Generic, Optional, Tuple, TypeVar


class WorkflowStatus(str, Enum):
    COMPLETE = "COMPLETE"
    PARTIAL = "PARTIAL"
    FAILED = "FAILED"
    REVIEW_REQUIRED = "REVIEW_REQUIRED"


class WorkflowFailureState(str, Enum):
    TASK_DEFINITION_UNRESOLVED = "TASK_DEFINITION_UNRESOLVED"
    STUDENT_EVIDENCE_INVALID = "STUDENT_EVIDENCE_INVALID"
    STUDENT_EVIDENCE_INCOMPLETE = "STUDENT_EVIDENCE_INCOMPLETE"
    LOW_CONFIDENCE = "LOW_CONFIDENCE"
    DISAGREEMENT = "DISAGREEMENT"
    EVIDENCE_CONFLICT = "EVIDENCE_CONFLICT"
    TASK1_FACT_CONFLICT = "TASK1_FACT_CONFLICT"
    SCHEMA_FAILURE = "SCHEMA_FAILURE"
    PROVIDER_FAILURE = "PROVIDER_FAILURE"
    REVIEW_REQUIRED = "REVIEW_REQUIRED"
    UNEXPECTED_FAILURE = "UNEXPECTED_FAILURE"
    RUBRIC_FAILURE = "RUBRIC_FAILURE"


class WorkflowStage(str, Enum):
    RUBRIC_PREFLIGHT = "rubric_preflight"
    TASK2_UNDERSTANDING = "task2_understanding"
    STUDENT_EVIDENCE = "student_evidence"
    MAIN_REVIEW = "main_review"
    SYNTAX_ENHANCEMENT = "syntax_enhancement"
    FINAL_VALIDATION = "final_validation"
    WORKER = "worker"


@dataclass(frozen=True)
class StageFailure:
    stage: WorkflowStage
    state: WorkflowFailureState
    message: str


T = TypeVar("T")


@dataclass(frozen=True)
class WorkflowResult(Generic[T]):
    status: WorkflowStatus
    completeness: bool
    stage_failures: Tuple[StageFailure, ...] = field(default_factory=tuple)
    review_reasons: Tuple[WorkflowFailureState, ...] = field(default_factory=tuple)
    payload: Optional[T] = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "stage_failures", tuple(self.stage_failures))
        object.__setattr__(self, "review_reasons", tuple(self.review_reasons))
        if self.status is WorkflowStatus.COMPLETE:
            if not self.completeness or self.payload is None:
                raise ValueError("COMPLETE requires a final report payload.")
            if self.stage_failures or self.review_reasons:
                raise ValueError("COMPLETE cannot carry failures or review reasons.")
            return
        if self.completeness:
            raise ValueError("Only COMPLETE may set completeness=True.")
        if self.payload is not None:
            raise ValueError("Non-complete workflows cannot carry a report payload.")

    @classmethod
    def complete(cls, payload: T) -> "WorkflowResult[T]":
        return cls(
            status=WorkflowStatus.COMPLETE,
            completeness=True,
            payload=payload,
        )

    @classmethod
    def partial(cls, *failures: StageFailure) -> "WorkflowResult[T]":
        return cls(
            status=WorkflowStatus.PARTIAL,
            completeness=False,
            stage_failures=tuple(failures),
        )

    @classmethod
    def failed(cls, *failures: StageFailure) -> "WorkflowResult[T]":
        return cls(
            status=WorkflowStatus.FAILED,
            completeness=False,
            stage_failures=tuple(failures),
        )

    @classmethod
    def review_required(
        cls, *reasons: WorkflowFailureState
    ) -> "WorkflowResult[T]":
        return cls(
            status=WorkflowStatus.REVIEW_REQUIRED,
            completeness=False,
            review_reasons=tuple(reasons),
        )
