"""主窗口：三栏可拖动布局 + 顶栏 + 批改/导出/设置编排。"""
from __future__ import annotations

from datetime import datetime
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QFileDialog, QHBoxLayout, QLabel, QMainWindow, QMessageBox, QPushButton,
    QSplitter, QVBoxLayout, QWidget,
)

from ..core.corpus import find_references
from ..core.image_input import validate_image_input
from ..core.models import GradingRequest, GradingResult
from ..core.pdf_report import export_report
from ..core.providers import ProviderFailureCode, provider_preflight
from ..core.settings import AppSettings
from ..core.workflow import WorkflowResult
from ..worker import GradingWorker
from .essay_panel import EssayPanel
from .feedback_panel import FeedbackPanel
from .settings_dialog import SettingsDialog
from .topic_panel import TopicPanel


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("雅思写作批改")
        self.resize(1440, 920)
        self.setMinimumSize(1100, 700)

        self._settings = AppSettings.load()
        self._worker: GradingWorker | None = None

        root = QWidget()
        root.setObjectName("root")
        outer = QVBoxLayout(root)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        outer.addWidget(self._build_topbar())

        body = QWidget()
        body_layout = QVBoxLayout(body)
        body_layout.setContentsMargins(14, 12, 14, 14)

        self.topic_panel = TopicPanel()
        self.essay_panel = EssayPanel()
        self.feedback_panel = FeedbackPanel()

        self.splitter = QSplitter(Qt.Horizontal)
        self.splitter.addWidget(self.topic_panel)
        self.splitter.addWidget(self.essay_panel)
        self.splitter.addWidget(self.feedback_panel)
        self.splitter.setSizes([360, 420, 520])
        self.splitter.setChildrenCollapsible(False)
        for i in range(3):
            self.splitter.setStretchFactor(i, 1)
        body_layout.addWidget(self.splitter)
        outer.addWidget(body, 1)

        self.setCentralWidget(root)

        self.essay_panel.grade_requested.connect(self._on_grade)
        self.feedback_panel.export_requested.connect(self._on_export)

    def _build_topbar(self) -> QWidget:
        bar = QWidget()
        bar.setObjectName("topbar")
        layout = QHBoxLayout(bar)
        layout.setContentsMargins(18, 12, 18, 12)

        title_box = QVBoxLayout()
        title_box.setSpacing(1)
        title = QLabel("雅思写作批改")
        title.setObjectName("appTitle")
        subtitle = QLabel("考官式评分 · 高分范文 · PDF 报告")
        subtitle.setObjectName("appSubtitle")
        title_box.addWidget(title)
        title_box.addWidget(subtitle)
        layout.addLayout(title_box)
        layout.addStretch()

        self.settings_btn = QPushButton("⚙  设置")
        self.settings_btn.clicked.connect(self._open_settings)
        layout.addWidget(self.settings_btn)
        return bar

    def _open_settings(self) -> None:
        dialog = SettingsDialog(self._settings, self)
        if dialog.exec():
            self._settings = dialog.result_settings()
            self._settings.save()

    def _on_grade(self) -> None:
        if self._worker and self._worker.isRunning():
            return
        self.essay_panel.set_error("")

        task_type = self.topic_panel.task_type

        question = self.topic_panel.question()
        essay = self.essay_panel.essay()
        if not question.strip():
            self.essay_panel.set_error("请先输入雅思写作题目。")
            return
        if not essay.strip():
            self.essay_panel.set_error("请先粘贴考生作文。")
            return

        image_resolution = validate_image_input(
            task_type, self.topic_panel.image_path()
        )
        if image_resolution.failure is not None:
            self.essay_panel.set_error(image_resolution.failure.message)
            return

        routes = provider_preflight(
            self._settings, task_type, image_resolution.image is not None
        )
        required_stages = (
            ("task2_understanding", "student_evidence", "main_review", "final_validation")
            if task_type == "task2" else ("main_review", "final_validation")
        )
        for stage in required_stages:
            failure = routes[stage].failure
            if failure is not None:
                if failure.code is ProviderFailureCode.INCOMPATIBLE_ROUTE:
                    self.essay_panel.set_error(failure.message)
                    return
                self.essay_panel.set_error(f"{failure.message} 请点右上角「设置」填写。")
                self._open_settings()
                return

        references = find_references(task_type, question, top_k=2)
        request = GradingRequest(
            taskType=task_type,
            question=question,
            essay=essay,
            targetBand=self.essay_panel.target_band(),
            references=references,
            imagePath=self.topic_panel.image_path(),
        )

        self.essay_panel.set_busy(True)
        self.feedback_panel.set_busy(True)

        self._worker = GradingWorker(request, self._settings)
        self._worker.finished.connect(self._on_grade_done)
        self._worker.failed.connect(self._on_grade_failed)
        self._worker.start()

    def _on_grade_done(self, result: WorkflowResult[GradingResult]) -> None:
        self.essay_panel.set_busy(False)
        self.feedback_panel.set_workflow_result(
            result,
            self.topic_panel.question(),
            self.essay_panel.essay(),
        )

    def _on_grade_failed(self, message: str) -> None:
        self.essay_panel.set_busy(False)
        self.feedback_panel._show_empty()
        self.essay_panel.set_error(message)

    def _on_export(self) -> None:
        result = self.feedback_panel.result
        if not result:
            return
        default_name = f"IELTS-Report-{datetime.now():%Y%m%d-%H%M%S}.pdf"
        path, _ = QFileDialog.getSaveFileName(
            self, "导出批改报告 PDF", str(Path.home() / default_name), "PDF (*.pdf)"
        )
        if not path:
            return
        try:
            export_report(
                path,
                self.topic_panel.task_type,
                self.topic_panel.question(),
                self.essay_panel.essay(),
                result,
            )
        except Exception as e:
            QMessageBox.warning(self, "导出失败", f"无法生成 PDF：{e}")
            return
        QMessageBox.information(self, "导出成功", f"报告已保存到：\n{path}")
