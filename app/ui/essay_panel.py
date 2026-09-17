"""中栏：考生文章栏 —— 作文编辑器 + 实时字数 + 开始批改按钮。"""
from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QButtonGroup, QFrame, QHBoxLayout, QLabel, QPushButton, QTextEdit, QVBoxLayout,
)


class EssayPanel(QFrame):
    grade_requested = Signal()

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("pane")
        self.setProperty("class", "pane")
        self._target_band = 7.5

        root = QVBoxLayout(self)
        root.setContentsMargins(18, 18, 18, 18)
        root.setSpacing(12)

        header = QHBoxLayout()
        title_box = QVBoxLayout()
        title_box.setSpacing(2)
        eyebrow = QLabel("CANDIDATE")
        eyebrow.setProperty("class", "eyebrow")
        title = QLabel("考生文章栏")
        title.setProperty("class", "paneTitle")
        title_box.addWidget(eyebrow)
        title_box.addWidget(title)
        header.addLayout(title_box)
        header.addStretch()
        self.word_count = QLabel("0 words")
        self.word_count.setObjectName("wordCount")
        header.addWidget(self.word_count)

        self.clear_btn = QPushButton("清除")
        self.clear_btn.setProperty("class", "clearButton")
        self.clear_btn.clicked.connect(self.clear)
        header.addWidget(self.clear_btn)
        root.addLayout(header)

        self.essay_edit = QTextEdit()
        self.essay_edit.setObjectName("candidateEssay")
        self.essay_edit.setPlaceholderText("在此粘贴考生作文……")
        self.essay_edit.textChanged.connect(self._update_word_count)
        root.addWidget(self.essay_edit, 1)

        target_row = QHBoxLayout()
        target_row.setSpacing(8)
        target_title = QLabel("目标重写版本")
        target_title.setProperty("class", "fieldLabel")
        target_row.addWidget(target_title)
        target_row.addStretch()

        self._target_group = QButtonGroup(self)
        self._target_group.setExclusive(True)
        for band in (7.5, 8.0, 8.5):
            btn = QPushButton(f"Band {band:g}")
            btn.setProperty("class", "targetSegment")
            btn.setCheckable(True)
            btn.clicked.connect(lambda checked=False, value=band: self._set_target_band(value))
            self._target_group.addButton(btn)
            target_row.addWidget(btn)
            if band == self._target_band:
                btn.setChecked(True)
        root.addLayout(target_row)

        action = QHBoxLayout()
        self.grade_btn = QPushButton("开始批改")
        self.grade_btn.setObjectName("primary")
        self.grade_btn.clicked.connect(self.grade_requested.emit)
        action.addWidget(self.grade_btn)

        self.error_label = QLabel("")
        self.error_label.setObjectName("errorText")
        self.error_label.setWordWrap(True)
        action.addWidget(self.error_label, 1)
        root.addLayout(action)

    def _update_word_count(self) -> None:
        text = self.essay_edit.toPlainText().strip()
        count = len(text.split()) if text else 0
        self.word_count.setText(f"{count} words")

    def essay(self) -> str:
        return self.essay_edit.toPlainText()

    def target_band(self) -> float:
        return self._target_band

    def _set_target_band(self, value: float) -> None:
        self._target_band = value

    def set_busy(self, busy: bool) -> None:
        self.grade_btn.setEnabled(not busy)
        for btn in self._target_group.buttons():
            btn.setEnabled(not busy)
        self.grade_btn.setText("批改中……" if busy else "开始批改")

    def set_error(self, message: str) -> None:
        self.error_label.setText(message)

    def clear(self) -> None:
        self.essay_edit.clear()
        self.set_error("")
