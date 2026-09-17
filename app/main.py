"""应用入口：加载全局样式并启动主窗口。

运行：python -m app.main （推荐用 bash run.sh 自动建环境）
"""
from __future__ import annotations

import sys
from pathlib import Path

from PySide6.QtWidgets import QApplication

from .ui.main_window import MainWindow

STYLE_PATH = Path(__file__).resolve().parent / "resources" / "styles.qss"


def main() -> None:
    app = QApplication(sys.argv)
    app.setApplicationName("雅思写作批改")

    if STYLE_PATH.exists():
        app.setStyleSheet(STYLE_PATH.read_text(encoding="utf-8"))

    window = MainWindow()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
