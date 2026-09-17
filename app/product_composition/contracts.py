"""Versioned, content-addressed read models for the DraftLoop product facade."""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
import re
from types import MappingProxyType
from typing import Any, Mapping

from app.product_platform.contracts import SubmissionState, digest


SEMANTIC_RESULT_VERSION = "c4-semantic-result-v1"
PRESENTATION_PROJECTION_VERSION = "c4-presentation-projection-v1"
EXPORT_ARTIFACT_VERSION = "c4-export-artifact-v2"


_SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")


def _is_sha256(value: object) -> bool:
    return isinstance(value, str) and _SHA256_PATTERN.fullmatch(value) is not None


class PresentationState(str, Enum):
    NORMAL = "NORMAL"
    PARTIAL = "PARTIAL"
    REVIEW_REQUIRED = "REVIEW_REQUIRED"
    FAILED = "FAILED"
    UNAVAILABLE = "UNAVAILABLE"


class ExportState(str, Enum):
    READY = "READY"
    BLOCKED = "BLOCKED"


def _freeze(value: Any) -> Any:
    if isinstance(value, Mapping):
        return MappingProxyType({str(key): _freeze(child) for key, child in value.items()})
    if isinstance(value, (list, tuple)):
        return tuple(_freeze(child) for child in value)
    return value


def _thaw(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _thaw(child) for key, child in value.items()}
    if isinstance(value, tuple):
        return [_thaw(child) for child in value]
    return value


@dataclass(frozen=True)
class SemanticResult:
    """A C4 identity for one validated C3 semantic-status snapshot."""

    submission_id: str
    state: SubmissionState
    submission_version: int
    accepted_result_sha256: str | None
    failure_code: str | None = None
    semantic_result_sha256: str = field(init=False)
    version: str = field(default=SEMANTIC_RESULT_VERSION, init=False)

    def __post_init__(self) -> None:
        if not isinstance(self.submission_id, str) or not self.submission_id:
            raise ValueError("Semantic results require a submission identity.")
        if not isinstance(self.state, SubmissionState):
            raise ValueError("Semantic results require a known C3 submission state.")
        if (
            not isinstance(self.submission_version, int)
            or isinstance(self.submission_version, bool)
            or self.submission_version < 1
        ):
            raise ValueError("Semantic results require a positive C3 submission version.")
        if self.accepted_result_sha256 is not None and not _is_sha256(self.accepted_result_sha256):
            raise ValueError("Semantic result hashes must be canonical SHA-256 values.")
        if self.state is SubmissionState.COMPLETE and self.accepted_result_sha256 is None:
            raise ValueError("Complete semantic results require an accepted C3 result hash.")
        object.__setattr__(self, "semantic_result_sha256", digest(self.content(include_hash=False)))

    def content(self, *, include_hash: bool = True) -> dict[str, Any]:
        value = {
            "version": self.version,
            "submissionId": self.submission_id,
            "state": self.state.value,
            "submissionVersion": self.submission_version,
            "acceptedResultSha256": self.accepted_result_sha256,
            "failureCode": self.failure_code,
        }
        if include_hash:
            value["semanticResultSha256"] = self.semantic_result_sha256
        return value


@dataclass(frozen=True)
class PresentationProjection:
    """A presentation contract derived from, but never substituted for, a semantic result."""

    submission_id: str
    state: PresentationState
    semantic_state: SubmissionState
    semantic_result_sha256: str
    report_artifact_id: str | None = None
    report_content_sha256: str | None = None
    payload: Mapping[str, Any] | None = None
    failure_code: str | None = None
    presentation_sha256: str = field(init=False)
    version: str = field(default=PRESENTATION_PROJECTION_VERSION, init=False)

    def __post_init__(self) -> None:
        if not isinstance(self.submission_id, str) or not self.submission_id:
            raise ValueError("Presentation projections require a submission identity.")
        if not isinstance(self.state, PresentationState):
            raise ValueError("Presentation projections require a known presentation state.")
        if not isinstance(self.semantic_state, SubmissionState):
            raise ValueError("Presentation projections require a known semantic state.")
        if not _is_sha256(self.semantic_result_sha256):
            raise ValueError("Presentation projections require semantic lineage.")
        if self.payload is not None:
            object.__setattr__(self, "payload", _freeze(self.payload))
        if self.state is PresentationState.NORMAL:
            if self.semantic_state is not SubmissionState.COMPLETE:
                raise ValueError("Normal presentation requires a complete semantic result.")
            if not isinstance(self.report_artifact_id, str) or not self.report_artifact_id:
                raise ValueError("Normal presentation requires a C3 report artifact identity.")
            if not _is_sha256(self.report_content_sha256):
                raise ValueError("Normal presentation requires a valid C3 report hash.")
            if not isinstance(self.payload, Mapping):
                raise ValueError("Normal presentation requires a report payload.")
            if self.failure_code is not None:
                raise ValueError("Normal presentation cannot carry a failure code.")
        elif (
            self.report_artifact_id is not None
            or self.report_content_sha256 is not None
            or self.payload is not None
            or not isinstance(self.failure_code, str)
            or not self.failure_code
        ):
            raise ValueError("Non-normal presentation cannot carry normal-report content.")
        object.__setattr__(self, "presentation_sha256", digest(self.content(include_hash=False)))

    def content(self, *, include_hash: bool = True) -> dict[str, Any]:
        value = {
            "version": self.version,
            "submissionId": self.submission_id,
            "state": self.state.value,
            "semanticState": self.semantic_state.value,
            "semanticResultSha256": self.semantic_result_sha256,
            "reportArtifactId": self.report_artifact_id,
            "reportContentSha256": self.report_content_sha256,
            "payload": _thaw(self.payload),
            "failureCode": self.failure_code,
        }
        if include_hash:
            value["presentationSha256"] = self.presentation_sha256
        return value


@dataclass(frozen=True)
class ExportArtifact:
    """A separate export contract.

    ``READY`` means a native/export adapter has actually materialized and
    content-addressed an artifact.  A normal report alone is only source
    material and cannot make this contract ready.

    ``export_sha256`` addresses this descriptor; ``content_sha256`` addresses
    the separately materialized export bytes.
    """

    submission_id: str
    state: ExportState
    semantic_state: SubmissionState
    semantic_result_sha256: str
    presentation_sha256: str
    source_report_artifact_id: str | None = None
    source_report_content_sha256: str | None = None
    artifact_id: str | None = None
    content_sha256: str | None = None
    media_type: str | None = None
    failure_code: str | None = None
    export_sha256: str = field(init=False)
    version: str = field(default=EXPORT_ARTIFACT_VERSION, init=False)

    def __post_init__(self) -> None:
        if not isinstance(self.submission_id, str) or not self.submission_id:
            raise ValueError("Export contracts require a submission identity.")
        if not isinstance(self.state, ExportState):
            raise ValueError("Export contracts require a known export state.")
        if not isinstance(self.semantic_state, SubmissionState):
            raise ValueError("Export contracts require a known semantic state.")
        if not _is_sha256(self.semantic_result_sha256) or not _is_sha256(self.presentation_sha256):
            raise ValueError("Export contracts require semantic and presentation lineage.")
        source_present = (
            self.source_report_artifact_id is not None
            or self.source_report_content_sha256 is not None
        )
        source_valid = (
            isinstance(self.source_report_artifact_id, str)
            and bool(self.source_report_artifact_id)
            and _is_sha256(self.source_report_content_sha256)
        )
        if source_present and not source_valid:
            raise ValueError("Export source report identity and hash must be supplied together.")
        if source_valid and self.semantic_state is not SubmissionState.COMPLETE:
            raise ValueError("Only a complete semantic result can provide export source material.")
        if self.state is ExportState.READY:
            if self.semantic_state is not SubmissionState.COMPLETE or not source_valid:
                raise ValueError("Ready export artifacts require complete report lineage.")
            if not isinstance(self.artifact_id, str) or not self.artifact_id:
                raise ValueError("Ready export artifacts require their own artifact identity.")
            if self.artifact_id == self.source_report_artifact_id:
                raise ValueError("Export and source report artifacts require separate identities.")
            if not _is_sha256(self.content_sha256):
                raise ValueError("Ready export artifacts require a content hash for exported bytes.")
            if not isinstance(self.media_type, str) or not self.media_type.strip():
                raise ValueError("Ready export artifacts require a media type.")
            if self.failure_code is not None:
                raise ValueError("Ready export artifacts cannot carry a failure code.")
        elif (
            self.artifact_id is not None
            or self.content_sha256 is not None
            or self.media_type is not None
            or not isinstance(self.failure_code, str)
            or not self.failure_code
        ):
            raise ValueError("Blocked export contracts cannot claim a materialized artifact.")
        object.__setattr__(self, "export_sha256", digest(self.content(include_hash=False)))

    @property
    def report_content_sha256(self) -> str | None:
        """Compatibility alias for the source report hash used by version 1."""

        return self.source_report_content_sha256

    def content(self, *, include_hash: bool = True) -> dict[str, Any]:
        value = {
            "version": self.version,
            "submissionId": self.submission_id,
            "state": self.state.value,
            "semanticState": self.semantic_state.value,
            "semanticResultSha256": self.semantic_result_sha256,
            "presentationSha256": self.presentation_sha256,
            "sourceReportArtifactId": self.source_report_artifact_id,
            "sourceReportContentSha256": self.source_report_content_sha256,
            "artifactId": self.artifact_id,
            "contentSha256": self.content_sha256,
            "mediaType": self.media_type,
            "failureCode": self.failure_code,
        }
        if include_hash:
            value["exportSha256"] = self.export_sha256
        return value
