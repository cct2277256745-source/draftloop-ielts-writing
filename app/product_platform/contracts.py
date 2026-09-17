"""Versioned C3 application and transport contracts."""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
import hashlib
import json
from typing import Any, Mapping
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .criterion_diagnostic import CriterionScoringDiagnostic


API_VERSION = "v1"
PLATFORM_CONTRACT_VERSION = "c3-platform-contract-v1"


def canonical_json(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def digest(value: Any) -> str:
    payload = value if isinstance(value, str) else canonical_json(value)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


class ErrorCode(str, Enum):
    INVALID_REQUEST = "INVALID_REQUEST"
    UNAUTHENTICATED = "UNAUTHENTICATED"
    FORBIDDEN = "FORBIDDEN"
    NOT_FOUND = "NOT_FOUND"
    VERSION_UNSUPPORTED = "VERSION_UNSUPPORTED"
    IDEMPOTENCY_CONFLICT = "IDEMPOTENCY_CONFLICT"
    CONFLICT = "CONFLICT"
    CANCELLED = "CANCELLED"
    QUOTA_EXCEEDED = "QUOTA_EXCEEDED"
    PRIVACY_BUDGET_EXHAUSTED = "PRIVACY_BUDGET_EXHAUSTED"
    UNSAFE_UPLOAD = "UNSAFE_UPLOAD"
    INTERNAL_FAILURE = "INTERNAL_FAILURE"


_HTTP_STATUS = {
    ErrorCode.INVALID_REQUEST: 400,
    ErrorCode.UNAUTHENTICATED: 401,
    ErrorCode.FORBIDDEN: 403,
    ErrorCode.NOT_FOUND: 404,
    ErrorCode.VERSION_UNSUPPORTED: 404,
    ErrorCode.IDEMPOTENCY_CONFLICT: 409,
    ErrorCode.CONFLICT: 409,
    ErrorCode.CANCELLED: 409,
    ErrorCode.QUOTA_EXCEEDED: 429,
    ErrorCode.PRIVACY_BUDGET_EXHAUSTED: 429,
    ErrorCode.UNSAFE_UPLOAD: 422,
    ErrorCode.INTERNAL_FAILURE: 500,
}


class PlatformError(ValueError):
    """Typed, public-safe platform failure."""

    def __init__(self, code: ErrorCode, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.public_message = message
        self.http_status = _HTTP_STATUS[code]


class Role(str, Enum):
    LEARNER = "LEARNER"
    TENANT_ADMIN = "TENANT_ADMIN"
    SUPPORT = "SUPPORT"


@dataclass(frozen=True)
class Principal:
    tenant_id: str
    user_id: str
    role: Role = Role.LEARNER
    session_id: str | None = None

    def __post_init__(self) -> None:
        if not self.tenant_id or not self.user_id or not isinstance(self.role, Role):
            raise PlatformError(ErrorCode.UNAUTHENTICATED, "A complete principal is required.")

    @property
    def can_manage_tenant(self) -> bool:
        return self.role is Role.TENANT_ADMIN


class SubmissionState(str, Enum):
    QUEUED = "QUEUED"
    PROCESSING = "PROCESSING"
    COMPLETE = "COMPLETE"
    PARTIAL = "PARTIAL"
    REVIEW_REQUIRED = "REVIEW_REQUIRED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"
    DELETING = "DELETING"
    DELETED = "DELETED"


class JobState(str, Enum):
    QUEUED = "QUEUED"
    LEASED = "LEASED"
    RETRY_WAIT = "RETRY_WAIT"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


class ConsentPurpose(str, Enum):
    TRAINING = "TRAINING"
    PRODUCT_ANALYTICS = "PRODUCT_ANALYTICS"


@dataclass(frozen=True)
class SubmitCommand:
    task_type: str
    question: str = field(repr=False)
    candidate_script: str = field(repr=False)
    idempotency_key: str = field(repr=False)
    target_band: float | None = None
    upload_id: str | None = None

    def __post_init__(self) -> None:
        if self.task_type not in {"task1", "task2"}:
            raise PlatformError(ErrorCode.INVALID_REQUEST, "taskType must be task1 or task2.")
        if not isinstance(self.question, str) or not self.question.strip():
            raise PlatformError(ErrorCode.INVALID_REQUEST, "Task question is required.")
        if not isinstance(self.candidate_script, str) or not self.candidate_script.strip():
            raise PlatformError(ErrorCode.INVALID_REQUEST, "Candidate Script is required.")
        if not isinstance(self.idempotency_key, str) or not (8 <= len(self.idempotency_key) <= 200):
            raise PlatformError(ErrorCode.INVALID_REQUEST, "A valid idempotency key is required.")
        if self.target_band is not None and not (0.0 <= float(self.target_band) <= 9.0):
            raise PlatformError(ErrorCode.INVALID_REQUEST, "targetBand must be between 0 and 9.")
        if self.task_type == "task1" and not self.upload_id:
            raise PlatformError(ErrorCode.INVALID_REQUEST, "Task 1 requires an authorised image upload.")
        if self.task_type == "task2" and self.upload_id is not None:
            raise PlatformError(ErrorCode.INVALID_REQUEST, "Task 2 cannot carry an image upload.")

    def request_digest(self) -> str:
        return digest({
            "version": PLATFORM_CONTRACT_VERSION,
            "taskType": self.task_type,
            "question": self.question,
            "candidateScript": self.candidate_script,
            "targetBand": self.target_band,
            "uploadId": self.upload_id,
        })


@dataclass(frozen=True)
class ProcessingOutcome:
    state: SubmissionState
    payload: Mapping[str, Any] | None = None
    failure_code: str | None = None
    criterion_diagnostic: CriterionScoringDiagnostic | None = field(default=None, repr=False)

    def __post_init__(self) -> None:
        if self.criterion_diagnostic is not None:
            from .criterion_diagnostic import CriterionScoringDiagnostic
            if not isinstance(self.criterion_diagnostic, CriterionScoringDiagnostic) or self.state is SubmissionState.COMPLETE:
                raise PlatformError(ErrorCode.INVALID_REQUEST, "Invalid internal diagnostic attachment.")
        allowed = {
            SubmissionState.COMPLETE,
            SubmissionState.PARTIAL,
            SubmissionState.REVIEW_REQUIRED,
            SubmissionState.FAILED,
        }
        if self.state not in allowed:
            raise PlatformError(ErrorCode.INVALID_REQUEST, "Invalid semantic result state.")
        if self.state is SubmissionState.COMPLETE and self.payload is None:
            raise PlatformError(ErrorCode.INVALID_REQUEST, "Complete results require a payload.")
        if self.state is not SubmissionState.COMPLETE and self.payload is not None:
            raise PlatformError(ErrorCode.INVALID_REQUEST, "Non-complete results cannot carry a report.")

    @property
    def result_sha256(self) -> str:
        return digest({
            "state": self.state.value,
            "payload": dict(self.payload) if self.payload is not None else None,
            "failureCode": self.failure_code,
        })


@dataclass(frozen=True)
class ApiRequest:
    method: str
    path: str
    headers: Mapping[str, str] = field(default_factory=dict, repr=False)
    body: Mapping[str, Any] = field(default_factory=dict, repr=False)


@dataclass(frozen=True)
class ApiResponse:
    status: int
    body: Mapping[str, Any]
    api_version: str = API_VERSION

    def public_body(self) -> dict[str, Any]:
        return {"apiVersion": self.api_version, **dict(self.body)}


def error_response(error: PlatformError) -> ApiResponse:
    return ApiResponse(error.http_status, {
        "error": {
            "code": error.code.value,
            "message": error.public_message,
        }
    })
