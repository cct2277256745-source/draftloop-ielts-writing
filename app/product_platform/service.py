"""C3 application use cases and durable worker orchestration."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Protocol

from .contracts import (
    ConsentPurpose,
    ErrorCode,
    PlatformError,
    Principal,
    ProcessingOutcome,
    SubmitCommand,
    digest,
)
from .persistence import ClaimedJob, PlatformStore
from .uploads import UploadStorage


@dataclass(frozen=True)
class ProcessingInput:
    submission_id: str
    tenant_id: str
    owner_id: str
    task_type: str
    question: str
    candidate_script: str
    target_band: float | None
    upload_bytes: bytes | None


class SubmissionProcessor(Protocol):
    def __call__(self, value: ProcessingInput) -> ProcessingOutcome:
        ...


class ProductPlatformService:
    def __init__(self, store: PlatformStore, *, uploads: UploadStorage | None = None, rag_runtime=None) -> None:
        self.store = store
        self.uploads = uploads
        self.rag_runtime = rag_runtime

    @classmethod
    def for_local_runtime(cls, store: PlatformStore, *, uploads=None, environ=None):
        """Explicit local bootstrap; the ordinary/public constructor remains unchanged."""
        from app.core.rag_local_runtime import LocalRagRuntime
        return cls(store, uploads=uploads, rag_runtime=LocalRagRuntime(environ))

    def local_coaching_processor(self, foundation):
        """Connect the accepted foundation adapter to this runtime's C3 worker."""
        from app.application.rag_coaching import C3RagCoachingProcessor, PostScoreRagCoachingService
        from app.core.rag_local_runtime import LocalRagRuntime
        runtime = self.rag_runtime if self.rag_runtime is not None else LocalRagRuntime({})
        return C3RagCoachingProcessor(foundation, PostScoreRagCoachingService(runtime))

    def rag_status(self, principal: Principal) -> Mapping[str, Any]:
        if not isinstance(principal, Principal):
            raise PlatformError(ErrorCode.UNAUTHENTICATED, "A C3 Tenant Principal is required.")
        if self.rag_runtime is not None:
            return self.rag_runtime.projection()
        return {"state": "RAG_DISABLED", "reason": "PACKAGE_NOT_CONFIGURED", "package": None,
                "mode": None, "capability": None, "publicSafeAuthorized": False,
                "commercialAuthorized": False, "directScoreAuthority": False}

    def submit(self, principal: Principal, command: SubmitCommand) -> Mapping[str, Any]:
        row, created = self.store.create_submission_and_job(
            principal,
            idempotency_hash=digest({
                "tenantId": principal.tenant_id,
                "ownerId": principal.user_id,
                "idempotencyKey": command.idempotency_key,
            }),
            request_sha256=command.request_digest(),
            task_type=command.task_type,
            question=command.question,
            candidate_script=command.candidate_script,
            target_band=command.target_band,
            upload_id=command.upload_id,
        )
        return {
            "submissionId": row["submission_id"],
            "state": row["state"],
            "requestSha256": row["request_sha256"],
            "created": created,
        }

    def status(self, principal: Principal, submission_id: str) -> Mapping[str, Any]:
        submission = self.store.submission(principal, submission_id)
        job = self.store.job_for_submission(principal, submission_id)
        return {
            "submissionId": submission["submission_id"],
            "state": submission["state"],
            "submissionVersion": submission["version"],
            "resultSha256": submission["accepted_result_sha256"],
            "job": {
                "state": job["state"],
                "progress": job["progress"],
                "attempts": job["attempts"],
                "version": job["version"],
                "failureCode": job["failure_code"],
                "cancelRequested": bool(job["cancel_requested"]),
            },
        }

    def report(self, principal: Principal, submission_id: str) -> Mapping[str, Any]:
        return self.store.report(principal, submission_id)

    def history(self, principal: Principal, *, limit: int = 50) -> tuple[Mapping[str, Any], ...]:
        return self.store.history(principal, limit=limit)

    def writing_source(self, principal: Principal, submission_id: str) -> Mapping[str, Any]:
        row = self.store.submission(principal, submission_id, include_raw=True)
        return {"question": row["question"], "candidateScript": row["candidate_script"],
                "taskType": row["task_type"], "createdAt": row["created_at"],
                "uploadId": row["upload_id"], "targetBand": row["target_band"]}

    def assessment_issues(self, principal: Principal, submission_id: str) -> list[dict]:
        artifact = self.store.criterion_scoring_diagnostic(principal, submission_id)
        if artifact is None:
            return []
        # Learners receive recovery categories, never internal execution records,
        # raw responses, unfinalized bands or provider configuration.
        return [{"criterion": row["criterion"], "status": row["semanticStatus"],
                 "failureCode": row["providerFailureCode"] or row["validationCode"]}
                for row in artifact["payload"]["criteria"]]

    def add_revision(
        self,
        principal: Principal,
        submission_id: str,
        payload: Mapping[str, Any],
    ) -> Mapping[str, Any]:
        if not payload:
            raise PlatformError(ErrorCode.INVALID_REQUEST, "Revision payload is required.")
        return self.store.append_submission_record(
            principal,
            submission_id,
            table="revisions",
            id_column="revision_id",
            id_prefix="revision",
            payload=payload,
        )

    def learning_records(self, principal, submission_id, table):
        return self.store.learning_records(principal, submission_id, table)

    def learning_memory_inputs(self, principal):
        return self.store.learning_memory_inputs(principal)

    def append_learning_record(self, principal, submission_id, **command):
        return self.store.append_learning_record(principal, submission_id, **command)

    def add_hint(
        self,
        principal: Principal,
        submission_id: str,
        payload: Mapping[str, Any],
    ) -> Mapping[str, Any]:
        if not payload:
            raise PlatformError(ErrorCode.INVALID_REQUEST, "Hint event payload is required.")
        return self.store.append_submission_record(
            principal,
            submission_id,
            table="hint_events",
            id_column="hint_event_id",
            id_prefix="hint",
            payload=payload,
        )

    def append_memory(self, principal: Principal, payload: Mapping[str, Any]) -> Mapping[str, Any]:
        if not payload:
            raise PlatformError(ErrorCode.INVALID_REQUEST, "Memory payload is required.")
        return self.store.append_memory(principal, payload)

    def memory(self, principal: Principal) -> tuple[Mapping[str, Any], ...]:
        return self.store.memory(principal)

    def create_support_case(
        self,
        principal: Principal,
        *,
        category: str,
        failure_code: str | None = None,
        submission_id: str | None = None,
    ) -> Mapping[str, Any]:
        return self.store.create_support_case(
            principal,
            category=category,
            failure_code=failure_code,
            submission_id=submission_id,
        )

    def cancel(self, principal: Principal, submission_id: str) -> Mapping[str, str]:
        state = self.store.request_cancel(principal, submission_id)
        return {"submissionId": submission_id, "jobState": state.value}

    def set_consent(
        self,
        principal: Principal,
        purpose: ConsentPurpose,
        *,
        granted: bool,
        policy_version: str,
    ) -> Mapping[str, Any]:
        value = self.store.set_consent(
            principal,
            purpose,
            granted=granted,
            policy_version=policy_version,
        )
        receipt = self.store.audit_receipt(
            tenant_id=principal.tenant_id,
            owner_id=principal.user_id,
            action="CONSENT_GRANTED" if granted else "CONSENT_REVOKED",
            detail={
                "purpose": purpose.value,
                "policyVersion": policy_version,
            },
        )
        return {**value, "receipt": receipt}

    def run_one_job(
        self,
        worker_id: str,
        processor: SubmissionProcessor,
        *,
        lease_seconds: int = 60,
    ) -> Mapping[str, Any] | None:
        claim = self.store.claim_next_job(worker_id, lease_seconds=lease_seconds)
        if claim is None:
            return None
        return self._run_claim(claim, processor)

    def _run_claim(
        self,
        claim: ClaimedJob,
        processor: SubmissionProcessor,
    ) -> Mapping[str, Any]:
        submission = self.store.worker_submission(claim.submission_id)
        upload_bytes = None
        if submission["upload_id"] is not None:
            if self.uploads is None:
                self.store.fail_job(
                    claim.job_id,
                    claim.lease_owner,
                    failure_code="UPLOAD_STORAGE_UNAVAILABLE",
                    retriable=False,
                )
                return {"jobId": claim.job_id, "state": "FAILED"}
            upload_bytes = self.uploads.read(
                Principal(claim.tenant_id, claim.owner_id),
                str(submission["upload_id"]),
            )
        value = ProcessingInput(
            claim.submission_id,
            claim.tenant_id,
            claim.owner_id,
            str(submission["task_type"]),
            str(submission["question"]),
            str(submission["candidate_script"]),
            submission["target_band"],
            upload_bytes,
        )
        try:
            self.store.update_job_progress(claim.job_id, claim.lease_owner, 25)
            outcome = processor(value)
            self.store.update_job_progress(claim.job_id, claim.lease_owner, 90)
            result_sha = self.store.complete_job(claim.job_id, claim.lease_owner, outcome)
            return {"jobId": claim.job_id, "state": "SUCCEEDED", "resultSha256": result_sha}
        except PlatformError:
            raise
        except Exception:
            state = self.store.fail_job(
                claim.job_id,
                claim.lease_owner,
                failure_code="PROCESSOR_FAILURE",
                retriable=True,
            )
            return {"jobId": claim.job_id, "state": state.value}
