from __future__ import annotations

import hashlib
import inspect
import os
from pathlib import Path
import tempfile
import unittest
from unittest import mock
from urllib.parse import quote

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QUrl
from PySide6.QtWebEngineCore import QWebEngineSettings
from PySide6.QtWidgets import QApplication, QWidget

import app.web_shell as web_shell
from app.web_shell import (
    AllowlistRequestInterceptor,
    DraftLoopUrlPolicy,
    DraftLoopWebShell,
    NativeCapabilityCode,
    NativeCapabilityError,
    NativeFileBroker,
    NavigationKind,
    ShellNavigationController,
    WebShellConfig,
    apply_secure_web_settings,
)
from app.product_composition import ExportArtifact, ExportState
from app.product_platform.contracts import SubmissionState


class _ExternalOpener:
    def __init__(self) -> None:
        self.urls: list[str] = []

    def __call__(self, url: QUrl) -> bool:
        self.urls.append(url.toString())
        return True


class _Dialog:
    def __init__(
        self,
        *,
        open_path: str | None = None,
        save_path: str | None = None,
    ) -> None:
        self.open_path = open_path
        self.save_path = save_path
        self.open_calls = 0
        self.save_calls = 0

    def choose_task1_image(self) -> str | None:
        self.open_calls += 1
        return self.open_path

    def choose_report_destination(self, suggested_name: str) -> str | None:
        self.save_calls += 1
        return self.save_path


class _FakeSettings:
    def __init__(self) -> None:
        self.values: dict[object, bool] = {}

    def setAttribute(self, attribute, enabled: bool) -> None:
        self.values[attribute] = enabled


class _RequestInfo:
    def __init__(self, url: str) -> None:
        self._url = QUrl(url)
        self.blocked = False

    def requestUrl(self) -> QUrl:
        return self._url

    def block(self, blocked: bool) -> None:
        self.blocked = blocked


def _ready_export_artifact(data: bytes | None = None) -> ExportArtifact:
    pdf = _minimal_pdf() if data is None else data
    return ExportArtifact(
        submission_id="sub-shell-test-001",
        state=ExportState.READY,
        semantic_state=SubmissionState.COMPLETE,
        semantic_result_sha256="a" * 64,
        presentation_sha256="b" * 64,
        source_report_artifact_id="report-shell-test-001",
        source_report_content_sha256="c" * 64,
        artifact_id="export-shell-test-001",
        content_sha256=hashlib.sha256(pdf).hexdigest(),
        media_type="application/pdf",
    )


def _minimal_pdf() -> bytes:
    return b"%PDF-1.7\n1 0 obj\n<<>>\nendobj\n%%EOF\n"


class UrlPolicyTests(unittest.TestCase):
    def setUp(self) -> None:
        self.policy = DraftLoopUrlPolicy(
            "https://app.draftloop.test",
            "qrc:/draftloop/",
        )

    def test_allows_exact_origin_and_only_the_bundled_resource_root(self) -> None:
        allowed = (
            "https://app.draftloop.test/",
            "https://app.draftloop.test/report?id=1#evidence",
            "https://app.draftloop.test:443/history",
            "qrc:/draftloop/index.html",
            "qrc:/draftloop/assets/app.js?v=1",
        )
        for url in allowed:
            with self.subTest(url=url):
                self.assertTrue(self.policy.allows(url))

        blocked = (
            "http://app.draftloop.test/",
            "https://app.draftloop.test:444/",
            "https://app.draftloop.test.evil.example/",
            "https://user@app.draftloop.test/",
            "qrc:/other/index.html",
            "qrc:/draftloop/../private.txt",
            "qrc:/draftloop/%2e%2e/private.txt",
            "qrc:/draftloop/%252e%252e/private.txt",
        )
        for url in blocked:
            for value in (url, QUrl(url)):
                with self.subTest(url=url, value_type=type(value).__name__):
                    self.assertFalse(self.policy.allows(value))

    def test_rejects_dangerous_and_unconfigured_schemes(self) -> None:
        for url in (
            "file:///etc/passwd",
            "javascript:alert(1)",
            "data:text/html,unsafe",
            "blob:https://app.draftloop.test/id",
            "ftp://example.test/file",
            "ws://app.draftloop.test/socket",
            "about:blank",
            "https://[malformed-host/",
        ):
            with self.subTest(url=url):
                self.assertFalse(self.policy.allows(url))
                self.assertFalse(self.policy.is_external_web_url(url))

    def test_recursively_and_excessively_encoded_qrc_traversal_fails_closed(self) -> None:
        traversal = "../private.txt"
        recursively_encoded = traversal
        for _ in range(8):
            recursively_encoded = quote(recursively_encoded, safe="")
        excessively_encoded = traversal
        for _ in range(web_shell.MAX_PERCENT_DECODE_PASSES + 4):
            excessively_encoded = quote(excessively_encoded, safe="")

        blocked = (
            f"qrc:/draftloop/{recursively_encoded}",
            f"qrc:/draftloop/{excessively_encoded}",
            "qrc:/draftloop/%2525255c..%2525255cprivate.txt",
            "qrc:/draftloop/%25252e%25252e%25252fprivate.txt",
            "qrc:/draftloop/%C0%AE%C0%AE/private.txt",
            "qrc:/draftloop/%ZZ/private.txt",
            "qrc:/draftloop/literal%25name.txt",
        )
        for url in blocked:
            for value in (url, QUrl(url)):
                with self.subTest(url=url, value_type=type(value).__name__):
                    self.assertFalse(self.policy.allows(value))

    def test_request_interceptor_applies_the_allowlist_to_subresources(self) -> None:
        interceptor = AllowlistRequestInterceptor(self.policy)
        allowed = _RequestInfo("https://app.draftloop.test/assets/app.js")
        external = _RequestInfo("https://cdn.example.test/tracker.js")
        local_file = _RequestInfo("file:///tmp/private.txt")

        interceptor.interceptRequest(allowed)
        interceptor.interceptRequest(external)
        interceptor.interceptRequest(local_file)

        self.assertFalse(allowed.blocked)
        self.assertTrue(external.blocked)
        self.assertTrue(local_file.blocked)

    def test_configuration_rejects_remote_plain_http_and_non_origin_values(self) -> None:
        for origin in (
            "http://app.draftloop.test",
            "https://user:password@app.draftloop.test",
            "https://app.draftloop.test/path",
            "file:///tmp/app",
        ):
            with self.subTest(origin=origin):
                with self.assertRaises(ValueError):
                    DraftLoopUrlPolicy(origin, "qrc:/draftloop/")

        self.assertTrue(
            DraftLoopUrlPolicy(
                "http://127.0.0.1:8765",
                "qrc:/draftloop/",
            ).allows("http://127.0.0.1:8765/v1/health")
        )


class NavigationControllerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.opener = _ExternalOpener()
        self.controller = ShellNavigationController(
            DraftLoopUrlPolicy(
                "https://app.draftloop.test",
                "qrc:/draftloop/",
            ),
            self.opener,
        )

    def test_only_user_clicked_external_http_links_leave_the_shell(self) -> None:
        self.assertFalse(self.controller.accept_navigation(
            "https://docs.example.test/help",
            NavigationKind.LINK_CLICKED,
            is_main_frame=True,
        ))
        self.assertEqual(self.opener.urls, ["https://docs.example.test/help"])

        for kind in (
            NavigationKind.REDIRECT,
            NavigationKind.TYPED,
            NavigationKind.FORM_SUBMITTED,
            NavigationKind.OTHER,
        ):
            with self.subTest(kind=kind):
                self.assertFalse(self.controller.accept_navigation(
                    "https://docs.example.test/help",
                    kind,
                    is_main_frame=True,
                ))
        self.assertEqual(len(self.opener.urls), 1)

    def test_allowed_navigation_stays_inside_and_dangerous_links_are_never_opened(self) -> None:
        self.assertTrue(self.controller.accept_navigation(
            "https://app.draftloop.test/report",
            NavigationKind.LINK_CLICKED,
            is_main_frame=True,
        ))
        self.assertFalse(self.controller.accept_navigation(
            "file:///etc/passwd",
            NavigationKind.LINK_CLICKED,
            is_main_frame=True,
        ))
        self.assertFalse(self.controller.accept_navigation(
            "https://docs.example.test/help",
            NavigationKind.LINK_CLICKED,
            is_main_frame=False,
        ))
        self.assertEqual(self.opener.urls, [])

    def test_popups_are_always_denied_and_only_user_external_popups_are_handed_off(self) -> None:
        self.assertFalse(self.controller.accept_popup(
            "https://docs.example.test/help",
            user_initiated=True,
        ))
        self.assertFalse(self.controller.accept_popup(
            "https://docs.example.test/automatic",
            user_initiated=False,
        ))
        self.assertFalse(self.controller.accept_popup(
            "https://app.draftloop.test/report",
            user_initiated=True,
        ))
        self.assertEqual(self.opener.urls, ["https://docs.example.test/help"])


class SecureSettingsTests(unittest.TestCase):
    def test_secure_settings_disable_native_and_cross_origin_capabilities(self) -> None:
        settings = _FakeSettings()
        applied = apply_secure_web_settings(settings)

        expected_false = (
            "JavascriptCanOpenWindows",
            "JavascriptCanAccessClipboard",
            "LocalStorageEnabled",
            "LocalContentCanAccessFileUrls",
            "LocalContentCanAccessRemoteUrls",
            "AllowRunningInsecureContent",
            "PluginsEnabled",
            "PdfViewerEnabled",
            "FullScreenSupportEnabled",
            "ScreenCaptureEnabled",
            "NavigateOnDropEnabled",
        )
        for name in expected_false:
            attribute = getattr(QWebEngineSettings.WebAttribute, name)
            self.assertIn(name, applied)
            self.assertFalse(settings.values[attribute])

        javascript = QWebEngineSettings.WebAttribute.JavascriptEnabled
        self.assertTrue(settings.values[javascript])


class NativeFileBrokerTests(unittest.TestCase):
    def test_task1_image_open_is_dialog_mediated_validated_and_path_free(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            image_path = Path(directory) / "chart.png"
            image_bytes = b"\x89PNG\r\n\x1a\n" + b"synthetic-image"
            image_path.write_bytes(image_bytes)
            dialog = _Dialog(open_path=str(image_path))

            selected = NativeFileBroker(dialog).select_task1_image()

            self.assertEqual(dialog.open_calls, 1)
            self.assertEqual(selected.display_name, "chart.png")
            self.assertEqual(selected.media_type, "image/png")
            self.assertEqual(selected.data, image_bytes)
            self.assertFalse(hasattr(selected, "path"))
            self.assertNotIn(str(image_path), repr(selected))

    def test_task1_image_rejects_relative_traversal_symlinks_and_spoofed_content(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            valid = root / "chart.png"
            valid.write_bytes(b"\x89PNG\r\n\x1a\ncontent")
            symlink = root / "link.png"
            symlink.symlink_to(valid)
            spoofed = root / "spoofed.png"
            spoofed.write_bytes(b"not-an-image")

            cases = (
                "relative/chart.png",
                str(root / "nested" / ".." / "chart.png"),
                str(symlink),
                str(spoofed),
            )
            for selected_path in cases:
                with self.subTest(selected_path=selected_path):
                    with self.assertRaises(NativeCapabilityError):
                        NativeFileBroker(
                            _Dialog(open_path=selected_path)
                        ).select_task1_image()

    def test_pdf_save_is_typed_validated_and_dialog_mediated(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "DraftLoop-report.pdf"
            dialog = _Dialog(save_path=str(output))
            payload = _minimal_pdf()
            artifact = _ready_export_artifact()

            saved = NativeFileBroker(dialog).save_report_pdf(
                payload,
                export_artifact=artifact,
                suggested_name="DraftLoop-report.pdf",
            )

            self.assertEqual(dialog.save_calls, 1)
            self.assertEqual(output.read_bytes(), payload)
            self.assertEqual(saved.display_name, output.name)
            self.assertEqual(saved.submission_id, artifact.submission_id)
            self.assertEqual(saved.export_sha256, artifact.export_sha256)
            self.assertFalse(hasattr(saved, "path"))
            self.assertNotIn(str(output), repr(saved))

    def test_pdf_save_requires_ready_typed_artifact_with_intact_lineage(self) -> None:
        valid_pdf = _minimal_pdf()
        dialog = _Dialog(save_path="/tmp/report.pdf")
        blocked = ExportArtifact(
            submission_id="sub-shell-test-002",
            state=ExportState.BLOCKED,
            semantic_state=SubmissionState.REVIEW_REQUIRED,
            semantic_result_sha256="a" * 64,
            presentation_sha256="b" * 64,
            failure_code="NORMAL_EXPORT_UNAVAILABLE",
        )
        malformed_lineage = _ready_export_artifact()
        object.__setattr__(malformed_lineage, "semantic_result_sha256", "not-a-sha256")
        tampered = _ready_export_artifact()
        object.__setattr__(tampered, "export_sha256", "d" * 64)

        for artifact in (blocked, malformed_lineage, tampered, object()):
            with self.subTest(artifact=artifact):
                with self.assertRaises(NativeCapabilityError) as invalid:
                    NativeFileBroker(dialog).save_report_pdf(
                        valid_pdf,
                        export_artifact=artifact,  # type: ignore[arg-type]
                        suggested_name="report.pdf",
                    )
                self.assertEqual(
                    invalid.exception.code,
                    NativeCapabilityCode.INVALID_ARTIFACT,
                )
        self.assertEqual(dialog.save_calls, 0)

        wrong_bytes = _minimal_pdf().replace(b"<<>>", b"<</Title (changed)>>")
        with self.assertRaises(NativeCapabilityError) as content_mismatch:
            NativeFileBroker(dialog).save_report_pdf(
                wrong_bytes,
                export_artifact=_ready_export_artifact(),
                suggested_name="report.pdf",
            )
        self.assertEqual(
            content_mismatch.exception.code,
            NativeCapabilityCode.INVALID_ARTIFACT,
        )
        self.assertEqual(dialog.save_calls, 0)

    def test_pdf_save_rejects_invalid_names_destinations_and_content(self) -> None:
        dialog = _Dialog(save_path="/tmp/report.pdf")
        artifact = _ready_export_artifact()
        with self.assertRaises(NativeCapabilityError) as unsafe_name:
            NativeFileBroker(dialog).save_report_pdf(
                _minimal_pdf(),
                export_artifact=artifact,
                suggested_name="../report.pdf",
            )
        self.assertEqual(unsafe_name.exception.code, NativeCapabilityCode.UNSAFE_PATH)
        self.assertEqual(dialog.save_calls, 0)

        with self.assertRaises(NativeCapabilityError):
            NativeFileBroker(_Dialog(save_path="relative.pdf")).save_report_pdf(
                _minimal_pdf(),
                export_artifact=artifact,
                suggested_name="report.pdf",
            )
        with self.assertRaises(NativeCapabilityError):
            NativeFileBroker(_Dialog(save_path="/tmp/report.txt")).save_report_pdf(
                _minimal_pdf(),
                export_artifact=artifact,
                suggested_name="report.pdf",
            )
        with self.assertRaises(NativeCapabilityError) as invalid_pdf:
            NativeFileBroker(_Dialog(save_path="/tmp/report.pdf")).save_report_pdf(
                b"not-a-pdf",
                export_artifact=artifact,
                suggested_name="report.pdf",
            )
        self.assertEqual(invalid_pdf.exception.code, NativeCapabilityCode.INVALID_CONTENT)

        with self.assertRaises(NativeCapabilityError) as prefix_only:
            NativeFileBroker(_Dialog(save_path="/tmp/report.pdf")).save_report_pdf(
                b"%PDF-1.7\nprefix-only payload",
                export_artifact=artifact,
                suggested_name="report.pdf",
            )
        self.assertEqual(prefix_only.exception.code, NativeCapabilityCode.INVALID_CONTENT)

    def test_cancelled_dialogs_have_no_effect(self) -> None:
        dialog = _Dialog()
        broker = NativeFileBroker(dialog)
        self.assertIsNone(broker.select_task1_image())
        self.assertIsNone(broker.save_report_pdf(
            _minimal_pdf(),
            export_artifact=_ready_export_artifact(),
            suggested_name="report.pdf",
        ))


class ShellDependencyBoundaryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def test_shell_can_be_constructed_with_a_fake_view_without_starting_webengine(self) -> None:
        loaded: list[str] = []

        class FakeView(QWidget):
            def load(self, url: QUrl) -> None:
                loaded.append(url.toString())

        def view_factory(controller, parent):
            self.assertIsInstance(controller, ShellNavigationController)
            return FakeView(parent)

        with mock.patch.object(
            web_shell,
            "_create_web_view",
            side_effect=view_factory,
        ):
            shell = DraftLoopWebShell(
                WebShellConfig(
                    draftloop_origin="https://app.draftloop.test",
                    start_url="qrc:/draftloop/index.html",
                ),
                external_opener=_ExternalOpener(),
                dialog_adapter=_Dialog(),
            )
        self.assertEqual(loaded, ["qrc:/draftloop/index.html"])
        shell.close()

    def test_public_constructor_cannot_replace_the_hardened_web_view(self) -> None:
        parameters = inspect.signature(DraftLoopWebShell.__init__).parameters
        self.assertNotIn("view_factory", parameters)
        with self.assertRaises(TypeError):
            DraftLoopWebShell(
                WebShellConfig("https://app.draftloop.test"),
                view_factory=lambda controller, parent: QWidget(parent),  # type: ignore[call-arg]
            )

    def test_public_shell_configuration_has_no_secret_or_generic_bridge_surface(self) -> None:
        fields = set(WebShellConfig.__dataclass_fields__)
        self.assertEqual(
            fields,
            {"draftloop_origin", "bundled_resource_root", "start_url"},
        )
        with self.assertRaises(TypeError):
            WebShellConfig(  # type: ignore[call-arg]
                draftloop_origin="https://app.draftloop.test",
                provider_api_key="must-not-be-accepted",
            )

        source = inspect.getsource(web_shell)
        self.assertNotIn("runJavaScript", source)
        self.assertNotIn("setHtml", source)
        self.assertNotIn("setWebChannel", source)
        self.assertNotIn("QtWebChannel", source)


if __name__ == "__main__":
    unittest.main()
