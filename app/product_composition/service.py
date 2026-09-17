"""C4 product facade over the accepted C3 application-service boundary."""
from __future__ import annotations

import re
from typing import Any, Mapping

from app.product_platform.contracts import (
    ErrorCode,
    JobState,
    PlatformError,
    Principal,
    SubmissionState,
    SubmitCommand,
    digest,
)
from app.product_platform.service import ProductPlatformService

from .contracts import (
    ExportArtifact,
    ExportState,
    PresentationProjection,
    PresentationState,
    SemanticResult,
)


_SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")


def _is_sha256(value: object) -> bool:
    return isinstance(value, str) and _SHA256_PATTERN.fullmatch(value) is not None


class DraftLoopApplicationService:
    """Compose product read models without bypassing C3 ownership or state rules."""

    def __init__(self, platform: ProductPlatformService) -> None:
        self._platform = platform

    @staticmethod
    def _require_principal(principal: Principal) -> Principal:
        if not isinstance(principal, Principal):
            raise PlatformError(ErrorCode.UNAUTHENTICATED, "A C3 Tenant Principal is required.")
        return principal

    def submit(self, principal: Principal, command: SubmitCommand) -> Mapping[str, Any]:
        return self._platform.submit(self._require_principal(principal), command)

    def rag_status(self, principal: Principal) -> Mapping[str, Any]:
        return self._platform.rag_status(self._require_principal(principal))

    def semantic_result(self, principal: Principal, submission_id: str) -> SemanticResult:
        status = self._platform.status(self._require_principal(principal), submission_id)
        if not isinstance(status, Mapping):
            raise PlatformError(ErrorCode.CONFLICT, "C3 returned an invalid semantic status.")
        try:
            state_value = status["state"]
            returned_id = status["submissionId"]
            submission_version = status["submissionVersion"]
            result_sha = status.get("resultSha256")
            job = status.get("job")
            failure_code = job.get("failureCode") if isinstance(job, Mapping) else None
        except (KeyError, TypeError) as exc:
            raise PlatformError(ErrorCode.CONFLICT, "C3 returned an invalid semantic status.") from exc
        if not isinstance(state_value, str):
            raise PlatformError(ErrorCode.CONFLICT, "C3 returned an invalid semantic status.")
        try:
            state = SubmissionState(state_value)
        except ValueError as exc:
            raise PlatformError(ErrorCode.CONFLICT, "C3 returned an invalid semantic status.") from exc
        if (
            not isinstance(returned_id, str)
            or not returned_id
            or returned_id != submission_id
            or not isinstance(submission_version, int)
            or isinstance(submission_version, bool)
            or submission_version < 1
        ):
            raise PlatformError(ErrorCode.CONFLICT, "C3 returned mismatched semantic lineage.")
        if result_sha is not None and not _is_sha256(result_sha):
            raise PlatformError(ErrorCode.CONFLICT, "C3 returned an invalid semantic result hash.")
        if state is SubmissionState.COMPLETE:
            if not _is_sha256(result_sha):
                raise PlatformError(ErrorCode.CONFLICT, "C3 returned an invalid complete result hash.")
            if not self._complete_job_snapshot_is_consistent(job):
                raise PlatformError(ErrorCode.CONFLICT, "C3 returned an inconsistent complete snapshot.")
        if failure_code is not None and not isinstance(failure_code, str):
            raise PlatformError(ErrorCode.CONFLICT, "C3 returned an invalid failure code.")
        return SemanticResult(
            submission_id=returned_id,
            state=state,
            submission_version=submission_version,
            accepted_result_sha256=result_sha,
            failure_code=str(failure_code) if failure_code is not None else None,
        )

    def presentation_projection(
        self,
        principal: Principal,
        submission_id: str,
    ) -> PresentationProjection:
        principal = self._require_principal(principal)
        semantic = self.semantic_result(principal, submission_id)
        if semantic.state is not SubmissionState.COMPLETE:
            return self._blocked_presentation(semantic)

        try:
            report = self._platform.report(principal, submission_id)
        except PlatformError as exc:
            if exc.code is not ErrorCode.NOT_FOUND:
                raise
            return self._failed_presentation(semantic, "REPORT_NOT_AVAILABLE")

        # C3 currently exposes status and report as separate authorised reads.
        # Re-read status and reject a torn/stale pair rather than presenting a
        # report against a semantic snapshot that changed during composition.
        confirmed_semantic = self.semantic_result(principal, submission_id)
        if not self._same_semantic_snapshot(semantic, confirmed_semantic):
            return self._failed_presentation(semantic, "PRESENTATION_SNAPSHOT_CHANGED")

        if not isinstance(report, Mapping):
            return self._failed_presentation(semantic, "PRESENTATION_CONTRACT_INVALID")
        try:
            artifact_id = report["artifactId"]
            content_sha = report["contentSha256"]
            payload = report["payload"]
        except (KeyError, TypeError):
            return self._failed_presentation(semantic, "PRESENTATION_CONTRACT_INVALID")
        if (
            not isinstance(artifact_id, str)
            or not artifact_id
            or not _is_sha256(content_sha)
            or not isinstance(payload, Mapping)
        ):
            return self._failed_presentation(semantic, "PRESENTATION_CONTRACT_INVALID")
        payload_value = dict(payload)
        payload_state = payload_value.get("status")
        if not isinstance(payload_state, str):
            return self._failed_presentation(semantic, "PRESENTATION_CONTRACT_INVALID")
        try:
            declared_payload_state = SubmissionState(payload_state)
            artifact_hash_matches = content_sha == digest(payload_value)
            semantic_hash_matches = semantic.accepted_result_sha256 == digest({
                "state": SubmissionState.COMPLETE.value,
                "payload": payload_value,
                "failureCode": None,
            })
        except (TypeError, ValueError):
            return self._failed_presentation(semantic, "PRESENTATION_CONTRACT_INVALID")
        payload_is_normal = declared_payload_state is SubmissionState.COMPLETE
        if not artifact_hash_matches or not semantic_hash_matches or not payload_is_normal:
            return self._failed_presentation(semantic, "PRESENTATION_LINEAGE_MISMATCH")
        return PresentationProjection(
            submission_id=submission_id,
            state=PresentationState.NORMAL,
            semantic_state=semantic.state,
            semantic_result_sha256=semantic.semantic_result_sha256,
            report_artifact_id=artifact_id,
            report_content_sha256=content_sha,
            payload=payload_value,
        )

    def export_artifact(self, principal: Principal, submission_id: str) -> ExportArtifact:
        principal = self._require_principal(principal)
        presentation = self.presentation_projection(principal, submission_id)
        if presentation.state is not PresentationState.NORMAL:
            return ExportArtifact(
                submission_id=submission_id,
                state=ExportState.BLOCKED,
                semantic_state=presentation.semantic_state,
                semantic_result_sha256=presentation.semantic_result_sha256,
                presentation_sha256=presentation.presentation_sha256,
                failure_code=presentation.failure_code or "NORMAL_EXPORT_UNAVAILABLE",
            )
        # The accepted C3 slice has no export materialisation service.  Preserve
        # the validated source lineage, but do not fabricate an artifact id,
        # exported-byte hash, media type, persistence event, or audit event.
        return ExportArtifact(
            submission_id=submission_id,
            state=ExportState.BLOCKED,
            semantic_state=presentation.semantic_state,
            semantic_result_sha256=presentation.semantic_result_sha256,
            presentation_sha256=presentation.presentation_sha256,
            source_report_artifact_id=presentation.report_artifact_id,
            source_report_content_sha256=presentation.report_content_sha256,
            failure_code="EXPORT_ARTIFACT_NOT_MATERIALIZED",
        )

    @staticmethod
    def _complete_job_snapshot_is_consistent(job: object) -> bool:
        if not isinstance(job, Mapping):
            return False
        state = job.get("state")
        progress = job.get("progress")
        attempts = job.get("attempts")
        version = job.get("version")
        return (
            state == JobState.SUCCEEDED.value
            and isinstance(progress, int)
            and not isinstance(progress, bool)
            and progress == 100
            and isinstance(attempts, int)
            and not isinstance(attempts, bool)
            and attempts >= 1
            and isinstance(version, int)
            and not isinstance(version, bool)
            and version >= 1
        )

    @staticmethod
    def _same_semantic_snapshot(left: SemanticResult, right: SemanticResult) -> bool:
        return (
            left.submission_id == right.submission_id
            and left.state is right.state
            and left.submission_version == right.submission_version
            and left.accepted_result_sha256 == right.accepted_result_sha256
            and left.failure_code == right.failure_code
        )

    @staticmethod
    def _blocked_presentation(semantic: SemanticResult) -> PresentationProjection:
        state = {
            SubmissionState.PARTIAL: PresentationState.PARTIAL,
            SubmissionState.REVIEW_REQUIRED: PresentationState.REVIEW_REQUIRED,
            SubmissionState.FAILED: PresentationState.FAILED,
        }.get(semantic.state, PresentationState.UNAVAILABLE)
        return PresentationProjection(
            submission_id=semantic.submission_id,
            state=state,
            semantic_state=semantic.state,
            semantic_result_sha256=semantic.semantic_result_sha256,
            failure_code=f"SEMANTIC_{semantic.state.value}_NO_NORMAL_PRESENTATION",
        )

    @staticmethod
    def _failed_presentation(
        semantic: SemanticResult,
        failure_code: str,
    ) -> PresentationProjection:
        return PresentationProjection(
            submission_id=semantic.submission_id,
            state=PresentationState.FAILED,
            semantic_state=semantic.state,
            semantic_result_sha256=semantic.semantic_result_sha256,
            failure_code=failure_code,
        )
