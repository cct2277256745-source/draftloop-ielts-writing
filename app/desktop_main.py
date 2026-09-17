"""Desktop entry point for the current DraftLoop web workspace.

The desktop build is a thin, local-only shell around the same React surface
used by the browser build. The Python service stays on loopback and owns the
local data directory; the WebEngine window only receives the rendered product
UI and the explicitly allowed native file capabilities.
"""
from __future__ import annotations

import os
from pathlib import Path
import socket
import subprocess
import sys
import time

from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication, QMessageBox

from .web_shell import DraftLoopWebShell, WebShellConfig


PROJECT_ROOT = Path(__file__).resolve().parents[1]
CLIENT_ROOT = PROJECT_ROOT / "frontend" / "dist" / "client"
APP_ICON = PROJECT_ROOT / "app" / "resources" / "app.icns"


def _free_loopback_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


def _wait_for_server(process: subprocess.Popen[bytes], port: int, timeout: float = 20.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError("DraftLoop 的本地服务未能启动，请先重新构建前端资源。")
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.25):
                return
        except OSError:
            time.sleep(0.1)
    raise RuntimeError("DraftLoop 本地服务启动超时。")


def _start_local_service(port: int) -> subprocess.Popen[bytes]:
    data_root = Path(
        os.environ.get(
            "DRAFTLOOP_DATA_ROOT",
            Path.home() / "Library" / "Application Support" / "DraftLoop",
        )
    )
    data_root.mkdir(parents=True, exist_ok=True)
    command = [
        sys.executable,
        "-m",
        "app.product_composition.local_browser",
        "--data-root",
        str(data_root),
        "--client-root",
        str(CLIENT_ROOT),
        "--port",
        str(port),
        "--allow-local-session",
    ]
    return subprocess.Popen(
        command,
        cwd=PROJECT_ROOT,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )


def _stop_local_service(process: subprocess.Popen[bytes]) -> None:
    if process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=3)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=2)


def main() -> int:
    if not CLIENT_ROOT.joinpath("index.html").is_file():
        print(
            "Missing frontend/dist/client/index.html. Run `npm run build --prefix frontend` first.",
            file=sys.stderr,
        )
        return 2

    port = _free_loopback_port()
    service = _start_local_service(port)
    try:
        _wait_for_server(service, port)
    except Exception as exc:
        _stop_local_service(service)
        print(str(exc), file=sys.stderr)
        return 1

    app = QApplication(sys.argv)
    app.setApplicationName("DraftLoop")
    app.setApplicationDisplayName("DraftLoop · IELTS Writing Coach")
    app.setOrganizationName("DraftLoop")
    if APP_ICON.is_file():
        app.setWindowIcon(QIcon(str(APP_ICON)))

    window = DraftLoopWebShell(WebShellConfig(f"http://127.0.0.1:{port}"))
    window.setWindowTitle("DraftLoop · IELTS Writing Coach")
    window.setMinimumSize(1120, 760)
    window.resize(1480, 960)
    window.show()

    app.aboutToQuit.connect(lambda: _stop_local_service(service))
    try:
        return int(app.exec())
    except Exception as exc:
        QMessageBox.critical(window, "DraftLoop", str(exc))
        return 1
    finally:
        _stop_local_service(service)


if __name__ == "__main__":
    raise SystemExit(main())
