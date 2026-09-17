"""Hardened, policy-first PySide WebEngine host for the C4 web client.

The policy and native capability objects are independent from Chromium so they
can be exercised with ordinary offscreen unit tests.  The existing Qt Widgets
entry point intentionally does not import this module.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
import hashlib
import hmac
import ipaddress
import os
from pathlib import Path
import re
import stat
from typing import Callable, Protocol
from urllib.parse import unquote, urlsplit

from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWebEngineCore import (
    QWebEnginePage,
    QWebEngineProfile,
    QWebEngineSettings,
    QWebEngineUrlRequestInterceptor,
)
from PySide6.QtWebEngineWidgets import QWebEngineView
from PySide6.QtWidgets import QFileDialog, QMainWindow, QWidget

from app.product_composition.contracts import (
    EXPORT_ARTIFACT_VERSION,
    ExportArtifact,
    ExportState,
)
from app.product_platform.contracts import SubmissionState, digest


DEFAULT_BUNDLED_RESOURCE_ROOT = "qrc:/draftloop/"
MAX_TASK1_IMAGE_BYTES = 10 * 1024 * 1024
MAX_REPORT_PDF_BYTES = 50 * 1024 * 1024
MAX_RESOURCE_PATH_BYTES = 4096
MAX_PERCENT_DECODE_PASSES = 12
_SHA256_PATTERN = re.compile(r"[0-9a-f]{64}\Z")


@dataclass(frozen=True)
class _WebOrigin:
    scheme: str
    host: str
    port: int


def _url_text(value: str | QUrl) -> str:
    return value.toString() if isinstance(value, QUrl) else str(value)


def _contains_unsafe_url_text(value: str) -> bool:
    return (
        not value
        or "\\" in value
        or any(ord(character) < 0x20 or character.isspace() for character in value)
    )


def _normalise_host(host: str | None) -> str:
    if not host or host.endswith("."):
        raise ValueError("A canonical URL host is required.")
    try:
        return ipaddress.ip_address(host).compressed
    except ValueError:
        try:
            encoded = host.encode("idna").decode("ascii").lower()
        except UnicodeError as exc:
            raise ValueError("The URL host is invalid.") from exc
        if not encoded or "%" in encoded:
            raise ValueError("The URL host is invalid.")
        return encoded


def _is_loopback(host: str) -> bool:
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def _parse_web_origin(value: str, *, configured: bool) -> _WebOrigin:
    if _contains_unsafe_url_text(value):
        raise ValueError("The web origin is invalid.")
    parsed = urlsplit(value)
    scheme = parsed.scheme.lower()
    if scheme not in {"http", "https"}:
        raise ValueError("The web origin must use HTTP or HTTPS.")
    if parsed.username is not None or parsed.password is not None:
        raise ValueError("Credentials are not permitted in URLs.")
    host = _normalise_host(parsed.hostname)
    try:
        port = parsed.port or (443 if scheme == "https" else 80)
    except ValueError as exc:
        raise ValueError("The web origin port is invalid.") from exc
    if configured:
        if parsed.path not in {"", "/"} or parsed.query or parsed.fragment:
            raise ValueError("The configured URL must be an origin, not a path.")
        if scheme == "http" and not _is_loopback(host):
            raise ValueError("Plain HTTP is permitted only for loopback development.")
    return _WebOrigin(scheme, host, port)


def _fully_unquote(value: str) -> str | None:
    """Decode nested URL escaping, failing closed when it does not converge.

    Chromium and Qt may decode a resource URL at different stages.  A fixed
    small number of passes therefore turns a sufficiently over-encoded ``..``
    segment into an allowlist bypass.  The bound here is a rejection limit, not
    a point at which partially decoded input becomes trusted.
    """
    decoded = value
    if len(decoded.encode("utf-8")) > MAX_RESOURCE_PATH_BYTES:
        return None
    for _ in range(MAX_PERCENT_DECODE_PASSES):
        try:
            next_value = unquote(decoded, encoding="utf-8", errors="strict")
        except UnicodeDecodeError:
            return None
        if next_value == decoded:
            # A remaining percent sign is either malformed escaping or an
            # encoded literal percent.  Both are rejected because Qt may
            # canonicalize them differently on a later pass.
            return None if "%" in decoded else decoded
        decoded = next_value
        if len(decoded.encode("utf-8")) > MAX_RESOURCE_PATH_BYTES:
            return None
    try:
        converged = unquote(decoded, encoding="utf-8", errors="strict")
    except UnicodeDecodeError:
        return None
    return decoded if converged == decoded and "%" not in decoded else None


def _safe_resource_path(value: str) -> str | None:
    decoded = _fully_unquote(value)
    if (
        decoded is None
        or not decoded.startswith("/")
        or decoded.startswith("//")
        or "\\" in decoded
        or "\x00" in decoded
        or any(character.isspace() or ord(character) < 0x20 for character in decoded)
    ):
        return None
    parts = decoded.split("/")
    if any(part in {".", ".."} for part in parts):
        return None
    return decoded


class DraftLoopUrlPolicy:
    """Allow only one exact web origin and one namespaced resource tree."""

    def __init__(
        self,
        draftloop_origin: str,
        bundled_resource_root: str = DEFAULT_BUNDLED_RESOURCE_ROOT,
    ) -> None:
        self._origin = _parse_web_origin(draftloop_origin, configured=True)
        root = urlsplit(bundled_resource_root)
        root_path = _safe_resource_path(root.path)
        if (
            root.scheme.lower() != "qrc"
            or root.netloc
            or root.query
            or root.fragment
            or root_path is None
            or not root_path.endswith("/")
        ):
            raise ValueError("The bundled resource root must be a qrc directory URL.")
        self._resource_root = root_path

    def allows(self, value: str | QUrl) -> bool:
        raw = _url_text(value)
        if _contains_unsafe_url_text(raw):
            return False
        try:
            parsed = urlsplit(raw)
        except ValueError:
            return False
        scheme = parsed.scheme.lower()
        if scheme in {"http", "https"}:
            try:
                return _parse_web_origin(raw, configured=False) == self._origin
            except ValueError:
                return False
        if scheme != "qrc" or parsed.netloc:
            return False
        path = _safe_resource_path(parsed.path)
        return path is not None and path.startswith(self._resource_root)

    def is_external_web_url(self, value: str | QUrl) -> bool:
        raw = _url_text(value)
        if _contains_unsafe_url_text(raw) or self.allows(raw):
            return False
        try:
            _parse_web_origin(raw, configured=False)
        except ValueError:
            return False
        return True


class NavigationKind(str, Enum):
    LINK_CLICKED = "LINK_CLICKED"
    TYPED = "TYPED"
    FORM_SUBMITTED = "FORM_SUBMITTED"
    REDIRECT = "REDIRECT"
    BACK_FORWARD = "BACK_FORWARD"
    RELOAD = "RELOAD"
    OTHER = "OTHER"


ExternalUrlHandler = Callable[[QUrl], bool]


def _open_external_url(url: QUrl) -> bool:
    return QDesktopServices.openUrl(url)


class ShellNavigationController:
    """Makes navigation and OS-browser handoff decisions without a web view."""

    def __init__(
        self,
        policy: DraftLoopUrlPolicy,
        external_opener: ExternalUrlHandler = _open_external_url,
    ) -> None:
        self.policy = policy
        self._external_opener = external_opener

    def _handoff_external(self, value: str | QUrl) -> bool:
        if not self.policy.is_external_web_url(value):
            return False
        try:
            return bool(self._external_opener(QUrl(_url_text(value))))
        except Exception:
            return False

    def accept_navigation(
        self,
        value: str | QUrl,
        kind: NavigationKind,
        *,
        is_main_frame: bool,
    ) -> bool:
        if self.policy.allows(value):
            return True
        if (
            is_main_frame
            and kind is NavigationKind.LINK_CLICKED
            and self.policy.is_external_web_url(value)
        ):
            self._handoff_external(value)
        return False

    def accept_popup(self, value: str | QUrl, *, user_initiated: bool) -> bool:
        if user_initiated and self.policy.is_external_web_url(value):
            self._handoff_external(value)
        return False


SECURE_WEB_ATTRIBUTES: tuple[tuple[str, bool], ...] = (
    ("JavascriptEnabled", True),
    ("JavascriptCanOpenWindows", False),
    ("JavascriptCanAccessClipboard", False),
    ("JavascriptCanPaste", False),
    ("LocalStorageEnabled", False),
    ("LocalContentCanAccessFileUrls", False),
    ("LocalContentCanAccessRemoteUrls", False),
    ("AllowRunningInsecureContent", False),
    ("AllowGeolocationOnInsecureOrigins", False),
    ("HyperlinkAuditingEnabled", False),
    ("PluginsEnabled", False),
    ("PdfViewerEnabled", False),
    ("FullScreenSupportEnabled", False),
    ("ScreenCaptureEnabled", False),
    ("NavigateOnDropEnabled", False),
    ("DnsPrefetchEnabled", False),
    ("AutoLoadIconsForPage", False),
    ("TouchIconsEnabled", False),
    ("BackForwardCacheEnabled", False),
    ("PlaybackRequiresUserGesture", True),
    ("WebRTCPublicInterfacesOnly", True),
    ("LinksIncludedInFocusChain", True),
)


def apply_secure_web_settings(settings) -> tuple[str, ...]:
    """Apply every supported hardened setting and report the applied names."""
    applied: list[str] = []
    attributes = QWebEngineSettings.WebAttribute
    for name, enabled in SECURE_WEB_ATTRIBUTES:
        attribute = getattr(attributes, name, None)
        if attribute is not None:
            settings.setAttribute(attribute, enabled)
            applied.append(name)
    return tuple(applied)


class AllowlistRequestInterceptor(QWebEngineUrlRequestInterceptor):
    """Apply the same allowlist to navigations and subresource requests."""

    def __init__(self, policy: DraftLoopUrlPolicy, parent=None) -> None:
        super().__init__(parent)
        self._policy = policy

    def interceptRequest(self, info) -> None:  # noqa: N802 - Qt virtual method
        if not self._policy.allows(info.requestUrl()):
            info.block(True)


def _navigation_kind(value) -> NavigationKind:
    mapping = {
        QWebEnginePage.NavigationType.NavigationTypeLinkClicked:
            NavigationKind.LINK_CLICKED,
        QWebEnginePage.NavigationType.NavigationTypeTyped:
            NavigationKind.TYPED,
        QWebEnginePage.NavigationType.NavigationTypeFormSubmitted:
            NavigationKind.FORM_SUBMITTED,
        QWebEnginePage.NavigationType.NavigationTypeRedirect:
            NavigationKind.REDIRECT,
        QWebEnginePage.NavigationType.NavigationTypeBackForward:
            NavigationKind.BACK_FORWARD,
        QWebEnginePage.NavigationType.NavigationTypeReload:
            NavigationKind.RELOAD,
    }
    return mapping.get(value, NavigationKind.OTHER)


class HardenedWebPage(QWebEnginePage):
    def __init__(
        self,
        profile: QWebEngineProfile,
        controller: ShellNavigationController,
        parent=None,
    ) -> None:
        super().__init__(profile, parent)
        self._controller = controller
        self.newWindowRequested.connect(self._on_new_window_requested)
        file_access = getattr(self, "fileSystemAccessRequested", None)
        if file_access is not None:
            file_access.connect(lambda request: request.reject())
        self.fullScreenRequested.connect(lambda request: request.reject())
        self.featurePermissionRequested.connect(self._deny_legacy_permission)
        permission_requested = getattr(self, "permissionRequested", None)
        if permission_requested is not None:
            permission_requested.connect(lambda permission: permission.deny())

    def acceptNavigationRequest(  # noqa: N802 - Qt virtual method
        self,
        url: QUrl,
        navigation_type,
        is_main_frame: bool,
    ) -> bool:
        return self._controller.accept_navigation(
            url,
            _navigation_kind(navigation_type),
            is_main_frame=is_main_frame,
        )

    def chooseFiles(self, mode, old_files, accepted_mime_types):  # noqa: N802
        return []

    def javaScriptConsoleMessage(self, level, message, line_number, source_id):  # noqa: N802
        # Console payloads can contain learner content; the shell emits none.
        return None

    def _on_new_window_requested(self, request) -> None:
        self._controller.accept_popup(
            request.requestedUrl(),
            user_initiated=bool(request.isUserInitiated()),
        )

    def _deny_legacy_permission(self, security_origin, feature) -> None:
        self.setFeaturePermission(
            security_origin,
            feature,
            QWebEnginePage.PermissionPolicy.PermissionDeniedByUser,
        )


class HardenedWebView(QWebEngineView):
    """Dedicated off-record WebEngine view with no ambient native capabilities."""

    def __init__(self, controller: ShellNavigationController, parent=None) -> None:
        super().__init__(parent)
        self.setAcceptDrops(False)
        self.setContextMenuPolicy(Qt.ContextMenuPolicy.NoContextMenu)

        self._profile = QWebEngineProfile(self)
        self._profile.setHttpCacheType(QWebEngineProfile.HttpCacheType.MemoryHttpCache)
        self._profile.setPersistentCookiesPolicy(
            QWebEngineProfile.PersistentCookiesPolicy.NoPersistentCookies
        )
        if hasattr(self._profile, "setPushServiceEnabled"):
            self._profile.setPushServiceEnabled(False)
        self._profile.setSpellCheckEnabled(False)
        self._interceptor = AllowlistRequestInterceptor(
            controller.policy,
            self._profile,
        )
        self._profile.setUrlRequestInterceptor(self._interceptor)
        self._profile.downloadRequested.connect(lambda download: download.cancel())

        page = HardenedWebPage(self._profile, controller, self)
        self.setPage(page)
        apply_secure_web_settings(self.settings())


class NativeCapabilityCode(str, Enum):
    UNSAFE_PATH = "UNSAFE_PATH"
    UNSUPPORTED_EXTENSION = "UNSUPPORTED_EXTENSION"
    INVALID_ARTIFACT = "INVALID_ARTIFACT"
    INVALID_CONTENT = "INVALID_CONTENT"
    IO_FAILURE = "IO_FAILURE"


class NativeCapabilityError(ValueError):
    def __init__(self, code: NativeCapabilityCode, message: str) -> None:
        super().__init__(message)
        self.code = code


class NativeDialogPort(Protocol):
    def choose_task1_image(self) -> str | None:
        ...

    def choose_report_destination(self, suggested_name: str) -> str | None:
        ...


class QtNativeDialogAdapter:
    """The only source of native paths; both operations require a user dialog."""

    def __init__(self, parent: QWidget | None = None) -> None:
        self._parent = parent

    def choose_task1_image(self) -> str | None:
        selected, _ = QFileDialog.getOpenFileName(
            self._parent,
            "选择 Task 1 图表",
            "",
            "Images (*.png *.jpg *.jpeg *.webp)",
        )
        return selected or None

    def choose_report_destination(self, suggested_name: str) -> str | None:
        selected, _ = QFileDialog.getSaveFileName(
            self._parent,
            "导出 DraftLoop PDF",
            suggested_name,
            "PDF (*.pdf)",
        )
        return selected or None


@dataclass(frozen=True)
class SelectedTaskImage:
    display_name: str
    media_type: str
    byte_size: int
    content_sha256: str
    data: bytes = field(repr=False)


@dataclass(frozen=True)
class SavedReportPdf:
    display_name: str
    byte_size: int
    content_sha256: str
    submission_id: str
    export_sha256: str


_IMAGE_TYPES = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
}


def _absolute_dialog_path(raw: str) -> Path:
    if (
        not raw
        or "\x00" in raw
        or "\\" in raw
        or any(part == ".." for part in Path(raw).parts)
    ):
        raise NativeCapabilityError(
            NativeCapabilityCode.UNSAFE_PATH,
            "The selected path is not permitted.",
        )
    path = Path(raw)
    if not path.is_absolute():
        raise NativeCapabilityError(
            NativeCapabilityCode.UNSAFE_PATH,
            "The selected path is not permitted.",
        )
    return path


def _valid_image_content(media_type: str, data: bytes) -> bool:
    if media_type == "image/png":
        return data.startswith(b"\x89PNG\r\n\x1a\n")
    if media_type == "image/jpeg":
        return data.startswith(b"\xff\xd8\xff")
    if media_type == "image/webp":
        return len(data) >= 12 and data.startswith(b"RIFF") and data[8:12] == b"WEBP"
    return False


def _is_sha256(value: object) -> bool:
    return isinstance(value, str) and _SHA256_PATTERN.fullmatch(value) is not None


def _require_ready_pdf_artifact(artifact: ExportArtifact) -> None:
    """Require the independently authorized C4 export contract and its lineage."""
    if not isinstance(artifact, ExportArtifact):
        raise NativeCapabilityError(
            NativeCapabilityCode.INVALID_ARTIFACT,
            "A typed DraftLoop export artifact is required.",
        )
    lineage_hashes = (
        artifact.semantic_result_sha256,
        artifact.presentation_sha256,
        artifact.source_report_content_sha256,
        artifact.content_sha256,
        artifact.export_sha256,
    )
    if (
        artifact.version != EXPORT_ARTIFACT_VERSION
        or artifact.state is not ExportState.READY
        or artifact.semantic_state is not SubmissionState.COMPLETE
        or artifact.media_type != "application/pdf"
        or artifact.failure_code is not None
        or not isinstance(artifact.submission_id, str)
        or not artifact.submission_id.strip()
        or any(character.isspace() or ord(character) < 0x20 for character in artifact.submission_id)
        or not isinstance(artifact.source_report_artifact_id, str)
        or not artifact.source_report_artifact_id
        or not isinstance(artifact.artifact_id, str)
        or not artifact.artifact_id
        or not all(_is_sha256(value) for value in lineage_hashes)
        or not hmac.compare_digest(
            artifact.export_sha256,
            digest(artifact.content(include_hash=False)),
        )
    ):
        raise NativeCapabilityError(
            NativeCapabilityCode.INVALID_ARTIFACT,
            "The report export artifact is not READY or has invalid lineage.",
        )


def _valid_pdf_content(data: object) -> bool:
    if not isinstance(data, bytes) or not (0 < len(data) <= MAX_REPORT_PDF_BYTES):
        return False
    first_line_end = data.find(b"\n", 0, 16)
    if first_line_end == -1:
        return False
    header = data[:first_line_end].rstrip(b"\r")
    if re.fullmatch(rb"%PDF-(?:1\.[0-7]|2\.0)", header) is None:
        return False
    return data.rstrip(b"\t\n\f\r ").endswith(b"%%EOF")


def _safe_read_regular_file(path: Path, max_bytes: int) -> bytes:
    if path.is_symlink():
        raise NativeCapabilityError(
            NativeCapabilityCode.UNSAFE_PATH,
            "Symbolic-link selections are not permitted.",
        )
    try:
        resolved = path.resolve(strict=True)
        flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
        descriptor = os.open(str(resolved), flags)
        try:
            details = os.fstat(descriptor)
            if not stat.S_ISREG(details.st_mode) or not (0 < details.st_size <= max_bytes):
                raise NativeCapabilityError(
                    NativeCapabilityCode.INVALID_CONTENT,
                    "The selected file size or type is invalid.",
                )
            with os.fdopen(descriptor, "rb", closefd=False) as handle:
                return handle.read(max_bytes + 1)
        finally:
            os.close(descriptor)
    except NativeCapabilityError:
        raise
    except OSError as exc:
        raise NativeCapabilityError(
            NativeCapabilityCode.IO_FAILURE,
            "The selected file could not be read.",
        ) from exc


class NativeFileBroker:
    """Two typed file operations; callers cannot supply an ambient filesystem path."""

    def __init__(self, dialog: NativeDialogPort) -> None:
        self._dialog = dialog

    def select_task1_image(self) -> SelectedTaskImage | None:
        selected = self._dialog.choose_task1_image()
        if selected is None:
            return None
        path = _absolute_dialog_path(selected)
        media_type = _IMAGE_TYPES.get(path.suffix.lower())
        if media_type is None:
            raise NativeCapabilityError(
                NativeCapabilityCode.UNSUPPORTED_EXTENSION,
                "The selected image extension is unsupported.",
            )
        data = _safe_read_regular_file(path, MAX_TASK1_IMAGE_BYTES)
        if len(data) > MAX_TASK1_IMAGE_BYTES or not _valid_image_content(media_type, data):
            raise NativeCapabilityError(
                NativeCapabilityCode.INVALID_CONTENT,
                "The selected image content is invalid.",
            )
        return SelectedTaskImage(
            display_name=path.name,
            media_type=media_type,
            byte_size=len(data),
            content_sha256=hashlib.sha256(data).hexdigest(),
            data=data,
        )

    def save_report_pdf(
        self,
        data: bytes,
        *,
        export_artifact: ExportArtifact,
        suggested_name: str,
    ) -> SavedReportPdf | None:
        _require_ready_pdf_artifact(export_artifact)
        if (
            not suggested_name
            or suggested_name != Path(suggested_name).name
            or "\\" in suggested_name
            or suggested_name in {".", ".."}
        ):
            raise NativeCapabilityError(
                NativeCapabilityCode.UNSAFE_PATH,
                "The suggested report name is not permitted.",
            )
        if Path(suggested_name).suffix.lower() != ".pdf":
            raise NativeCapabilityError(
                NativeCapabilityCode.UNSUPPORTED_EXTENSION,
                "Reports must use the PDF extension.",
            )
        if not _valid_pdf_content(data):
            raise NativeCapabilityError(
                NativeCapabilityCode.INVALID_CONTENT,
                "The report content is not a valid bounded PDF.",
            )
        content_sha256 = hashlib.sha256(data).hexdigest()
        if not hmac.compare_digest(export_artifact.content_sha256, content_sha256):
            raise NativeCapabilityError(
                NativeCapabilityCode.INVALID_ARTIFACT,
                "The PDF bytes do not match the authorized export artifact.",
            )

        selected = self._dialog.choose_report_destination(suggested_name)
        if selected is None:
            return None
        lexical_path = _absolute_dialog_path(selected)
        if lexical_path.suffix.lower() != ".pdf":
            raise NativeCapabilityError(
                NativeCapabilityCode.UNSUPPORTED_EXTENSION,
                "Reports must use the PDF extension.",
            )
        if lexical_path.exists() and (
            lexical_path.is_symlink() or not lexical_path.is_file()
        ):
            raise NativeCapabilityError(
                NativeCapabilityCode.UNSAFE_PATH,
                "The selected destination is not permitted.",
            )
        try:
            parent = lexical_path.parent.resolve(strict=True)
            if not parent.is_dir():
                raise OSError("destination parent is not a directory")
            destination = parent / lexical_path.name
            flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC | getattr(os, "O_NOFOLLOW", 0)
            descriptor = os.open(str(destination), flags, 0o600)
            try:
                os.fchmod(descriptor, 0o600)
                with os.fdopen(descriptor, "wb", closefd=False) as handle:
                    handle.write(data)
                    handle.flush()
                    os.fsync(handle.fileno())
            finally:
                os.close(descriptor)
        except OSError as exc:
            raise NativeCapabilityError(
                NativeCapabilityCode.IO_FAILURE,
                "The report could not be saved.",
            ) from exc
        return SavedReportPdf(
            display_name=lexical_path.name,
            byte_size=len(data),
            content_sha256=content_sha256,
            submission_id=export_artifact.submission_id,
            export_sha256=export_artifact.export_sha256,
        )


@dataclass(frozen=True)
class WebShellConfig:
    draftloop_origin: str
    bundled_resource_root: str = DEFAULT_BUNDLED_RESOURCE_ROOT
    start_url: str | None = None

    def __post_init__(self) -> None:
        policy = DraftLoopUrlPolicy(
            self.draftloop_origin,
            self.bundled_resource_root,
        )
        if self.start_url is not None and not policy.allows(self.start_url):
            raise ValueError("The shell start URL is outside the configured allowlist.")

    def resolved_start_url(self) -> str:
        return self.start_url or self.draftloop_origin.rstrip("/") + "/"


def _create_web_view(
    controller: ShellNavigationController,
    parent: QWidget | None,
) -> QWidget:
    return HardenedWebView(controller, parent)


class DraftLoopWebShell(QMainWindow):
    """Thin host; product data and credentials never enter its constructor."""

    def __init__(
        self,
        config: WebShellConfig,
        *,
        external_opener: ExternalUrlHandler = _open_external_url,
        dialog_adapter: NativeDialogPort | None = None,
        parent=None,
    ) -> None:
        super().__init__(parent)
        policy = DraftLoopUrlPolicy(
            config.draftloop_origin,
            config.bundled_resource_root,
        )
        self.navigation = ShellNavigationController(policy, external_opener)
        self.native_files = NativeFileBroker(
            dialog_adapter or QtNativeDialogAdapter(self)
        )
        # Deliberately not constructor-injectable: a caller must not be able to
        # replace the hardened profile/page stack with an arbitrary QWidget.
        # Unit tests patch this private module seam instead.
        view = _create_web_view(self.navigation, self)
        if not isinstance(view, QWidget):
            raise TypeError("The web view factory must return a QWidget.")
        self.setCentralWidget(view)
        self.web_view = view
        load = getattr(view, "load", None)
        if not callable(load):
            raise TypeError("The web view must expose a load method.")
        load(QUrl(config.resolved_start_url()))


__all__ = [
    "AllowlistRequestInterceptor",
    "DraftLoopUrlPolicy",
    "DraftLoopWebShell",
    "HardenedWebPage",
    "HardenedWebView",
    "NativeCapabilityCode",
    "NativeCapabilityError",
    "NativeFileBroker",
    "NavigationKind",
    "SavedReportPdf",
    "SelectedTaskImage",
    "ShellNavigationController",
    "WebShellConfig",
    "apply_secure_web_settings",
]
