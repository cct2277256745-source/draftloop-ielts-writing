"""Application boundary for the accepted synchronous desktop grading flow."""
from __future__ import annotations

from typing import Any, Callable

from ..core.llm import grade
from ..core.models import GradingRequest, GradingResult
from ..core.rubric import RubricLoadError, load_task2_rubric
from ..core.settings import AppSettings
from ..core.workflow import (
    StageFailure,
    WorkflowFailureState,
    WorkflowResult,
    WorkflowStage,
)


class DesktopGradingApplicationService:
    """One use case shared by desktop and future presentation adapters."""

    def __init__(
        self,
        settings: AppSettings,
        *,
        grade_fn: Callable[..., WorkflowResult[GradingResult]] = grade,
        rubric_loader: Callable[[], Any] = load_task2_rubric,
    ) -> None:
        self._settings = settings
        self._grade = grade_fn
        self._rubric_loader = rubric_loader

    def grade(self, request: GradingRequest) -> WorkflowResult[GradingResult]:
        try:
            rubric_snapshot = self._rubric_loader() if request.taskType == "task2" else None
        except RubricLoadError:
            return WorkflowResult.failed(StageFailure(
                WorkflowStage.RUBRIC_PREFLIGHT,
                WorkflowFailureState.RUBRIC_FAILURE,
                "Task 2 rubric is unavailable or invalid.",
            ))
        return self._grade(
            request,
            settings=self._settings,
            rubric_snapshot=rubric_snapshot,
        )
