"""后台批改线程：在 QThread 中调用 LLM，避免阻塞 UI。

执行结果发 finished(WorkflowResult)；输入/预检错误保留 failed(str)。
"""
from __future__ import annotations

from PySide6.QtCore import QThread, Signal

from .application.grading import DesktopGradingApplicationService
from .core.llm import LLMError, grade
from .core.models import GradingRequest, GradingResult
from .core.rubric import load_task2_rubric
from .core.settings import AppSettings
from .core.workflow import (
    StageFailure,
    WorkflowFailureState,
    WorkflowResult,
    WorkflowStage,
)


class GradingWorker(QThread):
    finished = Signal(object)  # WorkflowResult[GradingResult]
    failed = Signal(str)

    def __init__(self, request: GradingRequest, settings: AppSettings) -> None:
        super().__init__()
        self._request = request
        self._settings = settings

    def run(self) -> None:  # noqa: D102
        try:
            service = DesktopGradingApplicationService(
                self._settings,
                grade_fn=grade,
                rubric_loader=load_task2_rubric,
            )
            result: WorkflowResult[GradingResult] = service.grade(self._request)
            self.finished.emit(result)
        except LLMError as e:
            self.failed.emit(str(e))
        except Exception:  # 兜底：保留类型且不暴露原始异常详情
            self.finished.emit(WorkflowResult.failed(StageFailure(
                WorkflowStage.WORKER,
                WorkflowFailureState.UNEXPECTED_FAILURE,
                "批改线程发生意外错误。",
            )))
