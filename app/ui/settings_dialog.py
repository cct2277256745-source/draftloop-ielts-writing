"""Task-specific provider route configuration backed by legacy QSettings keys."""
from __future__ import annotations

from PySide6.QtWidgets import (
    QDialog, QDialogButtonBox, QFormLayout, QGroupBox, QLabel, QLineEdit,
    QTabWidget, QVBoxLayout, QWidget,
)

from ..core.settings import AppSettings


class SettingsDialog(QDialog):
    def __init__(self, settings: AppSettings, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("API 设置")
        self.setMinimumWidth(620)
        self._edits: dict[str, QLineEdit] = {}

        layout = QVBoxLayout(self)
        tabs = QTabWidget()
        tabs.addTab(self._task_tab("task1", settings), "Task 1 小作文")
        tabs.addTab(self._task_tab("task2", settings), "Task 2 大作文")
        layout.addWidget(tabs)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _task_tab(self, task: str, settings: AppSettings) -> QWidget:
        tab = QWidget()
        root = QVBoxLayout(tab)
        primary_note = (
            "用于主批改和最终验证。Task 1 的图像处理策略由已验证的能力路由决定。"
            if task == "task1" else "用于主批改和最终验证。"
        )
        root.addWidget(self._config_group(
            task, "qwen", "主批改路由", primary_note, settings
        ))
        syntax_note = (
            "用于句法增强；该阶段仅接收文字和上游摘要，不接收原始图像。"
            if task == "task1"
            else "用于句法增强；不改变评分、知识权威或最终验证职责。"
        )
        root.addWidget(self._config_group(
            task, "deepseek", "句法增强路由", syntax_note, settings
        ))
        root.addStretch()
        return tab

    def _config_group(
        self, task: str, role: str, title: str, note: str, settings: AppSettings
    ) -> QGroupBox:
        box = QGroupBox(title)
        form = QFormLayout(box)
        note_label = QLabel(note)
        note_label.setWordWrap(True)
        form.addRow(note_label)
        for field_name, label in (
            ("api_key", "API Key"),
            ("base_url", "Base URL"),
            ("model", "Model"),
        ):
            key = f"{task}_{role}_{field_name}"
            edit = QLineEdit(str(getattr(settings, key)))
            if field_name == "api_key":
                edit.setEchoMode(QLineEdit.Password)
            self._edits[key] = edit
            form.addRow(label, edit)
        return box

    def result_settings(self) -> AppSettings:
        return AppSettings(**{key: edit.text().strip() for key, edit in self._edits.items()})
