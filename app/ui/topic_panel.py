"""左栏：题目栏 —— Task 1 / Task 2 切换 + 题目输入 + 语料状态。"""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (
    QButtonGroup, QFileDialog, QFrame, QHBoxLayout, QLabel, QPushButton, QTextEdit,
    QVBoxLayout, QWidget,
)

from ..core.corpus import corpus_counts
from ..core.image_input import validate_image_input


class TopicPanel(QFrame):
    task_changed = Signal(str)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("pane")
        self.setProperty("class", "pane")
        self._task_type = "task2"
        self._image_path = ""

        root = QVBoxLayout(self)
        root.setContentsMargins(18, 18, 18, 18)
        root.setSpacing(12)

        # 标题 + Task 切换
        header = QHBoxLayout()
        title_box = QVBoxLayout()
        title_box.setSpacing(2)
        eyebrow = QLabel("PROMPT")
        eyebrow.setProperty("class", "eyebrow")
        title = QLabel("题目栏")
        title.setProperty("class", "paneTitle")
        title_box.addWidget(eyebrow)
        title_box.addWidget(title)
        header.addLayout(title_box)
        header.addStretch()

        self.clear_btn = QPushButton("清除")
        self.clear_btn.setProperty("class", "clearButton")
        self.clear_btn.clicked.connect(self.clear)
        header.addWidget(self.clear_btn)

        seg = QHBoxLayout()
        seg.setSpacing(0)
        self._group = QButtonGroup(self)
        self.btn_task1 = self._make_segment("Task 1", "task1")
        self.btn_task2 = self._make_segment("Task 2", "task2")
        self.btn_task2.setChecked(True)
        seg.addWidget(self.btn_task1)
        seg.addWidget(self.btn_task2)
        header.addLayout(seg)
        root.addLayout(header)

        # 题目输入
        self.question_edit = QTextEdit()
        self.question_edit.setPlaceholderText("在此粘贴或输入雅思写作题目……")
        self.question_edit.setMinimumHeight(160)
        root.addWidget(self.question_edit, 1)

        self.image_box = QWidget()
        image_layout = QVBoxLayout(self.image_box)
        image_layout.setContentsMargins(0, 0, 0, 0)
        image_layout.setSpacing(8)
        self.image_hint = QLabel(
            "建议上传清晰完整的图表图片，避免裁掉标题、单位、图例和横纵轴。\n"
            "支持 PNG、JPG/JPEG、WebP，文件不能超过 10 MiB。"
        )
        self.image_hint.setWordWrap(True)
        self.image_hint.setProperty("class", "fieldLabel")
        image_layout.addWidget(self.image_hint)
        self.image_preview = QLabel("尚未上传图表图片")
        self.image_preview.setAlignment(Qt.AlignCenter)
        self.image_preview.setMinimumHeight(130)
        self.image_preview.setObjectName("imagePreview")
        image_layout.addWidget(self.image_preview)
        image_actions = QHBoxLayout()
        self.upload_btn = QPushButton("上传 / 更换图片")
        self.remove_btn = QPushButton("删除图片")
        self.remove_btn.setEnabled(False)
        self.upload_btn.clicked.connect(self._choose_image)
        self.remove_btn.clicked.connect(self._remove_image)
        image_actions.addWidget(self.upload_btn)
        image_actions.addWidget(self.remove_btn)
        image_layout.addLayout(image_actions)
        required = validate_image_input("task1", "").failure
        self._required_image_message = required.message if required else "Task 1 必须上传图表图片。"
        self.no_image_warning = QLabel(self._required_image_message)
        self.no_image_warning.setWordWrap(True)
        self.no_image_warning.setProperty("class", "warnBox")
        image_layout.addWidget(self.no_image_warning)
        self.image_box.setVisible(False)
        root.addWidget(self.image_box)

        # 语料状态
        t1, t2 = corpus_counts()
        self.corpus_label = QLabel(
            f"📚 内置范文语料：已加载 {t1 + t2} 篇（Task 1 {t1} · Task 2 {t2}）"
        )
        self.corpus_label.setObjectName("corpusStatus")
        self.corpus_label.setWordWrap(True)
        root.addWidget(self.corpus_label)

    def _make_segment(self, text: str, value: str) -> QPushButton:
        btn = QPushButton(text)
        btn.setProperty("class", "segment")
        btn.setCheckable(True)
        btn.setCursor(self.cursor())
        self._group.addButton(btn)
        btn.clicked.connect(lambda: self._set_task(value))
        return btn

    def _set_task(self, value: str) -> None:
        self._task_type = value
        if value == "task2" and self._image_path:
            self._remove_image()
        self.image_box.setVisible(value == "task1")
        self.task_changed.emit(value)

    @property
    def task_type(self) -> str:
        return self._task_type

    def question(self) -> str:
        return self.question_edit.toPlainText()

    def image_path(self) -> str:
        return self._image_path

    def _choose_image(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "选择 Task 1 图表图片", "", "Images (*.png *.jpg *.jpeg *.webp)"
        )
        if not path:
            return
        resolution = validate_image_input("task1", path)
        if resolution.failure is not None:
            self._remove_image()
            self.no_image_warning.setText(resolution.failure.message)
            return
        pixmap = QPixmap(path)
        if pixmap.isNull():
            self._remove_image()
            self.no_image_warning.setText("图片读取失败，请重新上传清晰的 PNG、JPG 或 WebP 图片。")
            return
        self._image_path = str(Path(path))
        self.image_preview.setPixmap(
            pixmap.scaled(320, 220, Qt.KeepAspectRatio, Qt.SmoothTransformation)
        )
        self.image_preview.setToolTip(self._image_path)
        self.remove_btn.setEnabled(True)
        self.no_image_warning.setVisible(False)

    def _remove_image(self) -> None:
        self._image_path = ""
        self.image_preview.clear()
        self.image_preview.setText("尚未上传图表图片")
        self.remove_btn.setEnabled(False)
        self.no_image_warning.setText(self._required_image_message)
        self.no_image_warning.setVisible(True)

    def clear(self) -> None:
        self.question_edit.clear()
        self._remove_image()
