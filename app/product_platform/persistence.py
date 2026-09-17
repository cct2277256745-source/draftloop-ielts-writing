"""Tenant-isolated SQLite persistence and durable C3 job state.

SQLite is the reproducible local/test adapter.  The application boundary never
depends on SQLite-specific rows, so a deployment adapter can replace it without
changing presentation or accepted domain contracts.
"""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import secrets
import sqlite3
import threading
from typing import Any, Callable, Iterator, Mapping

from .contracts import (
    ConsentPurpose,
    ErrorCode,
    JobState,
    PlatformError,
    Principal,
    ProcessingOutcome,
    Role,
    SubmissionState,
    canonical_json,
    digest,
)


SCHEMA_VERSION = 1


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _iso(value: datetime) -> str:
    if value.tzinfo is None:
        raise ValueError("timestamps require timezone information")
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _public_id(prefix: str) -> str:
    return f"{prefix}_{secrets.token_hex(16)}"


_UP_V1 = """
CREATE TABLE IF NOT EXISTS tenants (
    tenant_id TEXT PRIMARY KEY,
    display_name TEXT NOT NULL,
    version INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS users (
    user_id TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL REFERENCES tenants(tenant_id) ON DELETE CASCADE,
    email_hash TEXT NOT NULL,
    password_hash TEXT NOT NULL,
    role TEXT NOT NULL,
    version INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL,
    deleted_at TEXT,
    UNIQUE(tenant_id, email_hash)
);
CREATE TABLE IF NOT EXISTS sessions (
    session_hash TEXT PRIMARY KEY,
    session_id TEXT NOT NULL UNIQUE,
    tenant_id TEXT NOT NULL,
    user_id TEXT NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
    expires_at TEXT NOT NULL,
    revoked_at TEXT,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS submissions (
    submission_id TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL,
    owner_id TEXT NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
    idempotency_hash TEXT NOT NULL,
    request_sha256 TEXT NOT NULL,
    task_type TEXT NOT NULL,
    question TEXT NOT NULL,
    candidate_script TEXT NOT NULL,
    target_band REAL,
    upload_id TEXT,
    state TEXT NOT NULL,
    accepted_result_sha256 TEXT,
    version INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(tenant_id, owner_id, idempotency_hash)
);
CREATE TABLE IF NOT EXISTS artifacts (
    artifact_id TEXT PRIMARY KEY,
    submission_id TEXT NOT NULL REFERENCES submissions(submission_id) ON DELETE CASCADE,
    tenant_id TEXT NOT NULL,
    owner_id TEXT NOT NULL,
    kind TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    content_sha256 TEXT NOT NULL,
    version INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL,
    UNIQUE(submission_id, kind)
);
CREATE TABLE IF NOT EXISTS jobs (
    job_id TEXT PRIMARY KEY,
    submission_id TEXT NOT NULL UNIQUE REFERENCES submissions(submission_id) ON DELETE CASCADE,
    tenant_id TEXT NOT NULL,
    owner_id TEXT NOT NULL,
    state TEXT NOT NULL,
    progress INTEGER NOT NULL DEFAULT 0,
    attempts INTEGER NOT NULL DEFAULT 0,
    max_attempts INTEGER NOT NULL DEFAULT 3,
    lease_owner TEXT,
    lease_expires_at TEXT,
    cancel_requested INTEGER NOT NULL DEFAULT 0,
    failure_code TEXT,
    version INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS revisions (
    revision_id TEXT PRIMARY KEY,
    submission_id TEXT NOT NULL REFERENCES submissions(submission_id) ON DELETE CASCADE,
    tenant_id TEXT NOT NULL,
    owner_id TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    content_sha256 TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS hint_events (
    hint_event_id TEXT PRIMARY KEY,
    submission_id TEXT NOT NULL REFERENCES submissions(submission_id) ON DELETE CASCADE,
    tenant_id TEXT NOT NULL,
    owner_id TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    content_sha256 TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS memory_records (
    memory_id TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL,
    owner_id TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    content_sha256 TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS support_cases (
    case_id TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL,
    owner_id TEXT NOT NULL,
    submission_id TEXT REFERENCES submissions(submission_id) ON DELETE SET NULL,
    category TEXT NOT NULL,
    failure_code TEXT,
    status TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS uploads (
    upload_id TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL,
    owner_id TEXT NOT NULL,
    media_type TEXT NOT NULL,
    byte_size INTEGER NOT NULL,
    content_sha256 TEXT NOT NULL,
    storage_key TEXT NOT NULL UNIQUE,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS consents (
    tenant_id TEXT NOT NULL,
    owner_id TEXT NOT NULL,
    purpose TEXT NOT NULL,
    granted INTEGER NOT NULL,
    policy_version TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY(tenant_id, owner_id, purpose)
);
CREATE TABLE IF NOT EXISTS audit_receipts (
    receipt_id TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL,
    owner_id TEXT,
    subject_sha256 TEXT NOT NULL,
    action TEXT NOT NULL,
    detail_json TEXT NOT NULL,
    receipt_sha256 TEXT NOT NULL UNIQUE,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS deletion_requests (
    deletion_id TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL,
    owner_id TEXT,
    subject_sha256 TEXT NOT NULL,
    state TEXT NOT NULL,
    requested_at TEXT NOT NULL,
    completed_at TEXT,
    UNIQUE(tenant_id, subject_sha256)
);
CREATE TABLE IF NOT EXISTS metric_events (
    event_id TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL,
    actor_sha256 TEXT NOT NULL,
    event_type TEXT NOT NULL,
    dimensions_json TEXT NOT NULL,
    numeric_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS quota_usage (
    tenant_id TEXT NOT NULL,
    owner_id TEXT NOT NULL,
    quota_name TEXT NOT NULL,
    window_key TEXT NOT NULL,
    amount INTEGER NOT NULL,
    PRIMARY KEY(tenant_id, owner_id, quota_name, window_key)
);
CREATE TABLE IF NOT EXISTS backup_catalog (
    backup_id TEXT PRIMARY KEY,
    path TEXT NOT NULL UNIQUE,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_submissions_owner ON submissions(tenant_id, owner_id, created_at);
CREATE INDEX IF NOT EXISTS idx_jobs_claim ON jobs(state, lease_expires_at, created_at);
CREATE INDEX IF NOT EXISTS idx_metrics_type ON metric_events(event_type, created_at);
"""


_DOWN_V1 = """
DROP TABLE IF EXISTS backup_catalog;
DROP TABLE IF EXISTS quota_usage;
DROP TABLE IF EXISTS metric_events;
DROP TABLE IF EXISTS deletion_requests;
DROP TABLE IF EXISTS audit_receipts;
DROP TABLE IF EXISTS consents;
DROP TABLE IF EXISTS uploads;
DROP TABLE IF EXISTS support_cases;
DROP TABLE IF EXISTS memory_records;
DROP TABLE IF EXISTS hint_events;
DROP TABLE IF EXISTS revisions;
DROP TABLE IF EXISTS jobs;
DROP TABLE IF EXISTS artifacts;
DROP TABLE IF EXISTS submissions;
DROP TABLE IF EXISTS sessions;
DROP TABLE IF EXISTS users;
DROP TABLE IF EXISTS tenants;
"""


@dataclass(frozen=True)
class ClaimedJob:
    job_id: str
    submission_id: str
    tenant_id: str
    owner_id: str
    attempt: int
    lease_owner: str
    lease_expires_at: str


class PlatformStore:
    """Single enforcement point for ownership, versions, and durable state."""

    def __init__(
        self,
        database_path: str | Path,
        *,
        clock: Callable[[], datetime] = _utcnow,
    ) -> None:
        self.path = Path(database_path).resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._clock = clock
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(str(self.path), check_same_thread=False, isolation_level=None)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA foreign_keys=ON")
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA synchronous=FULL")
        try:
            os.chmod(self.path, 0o600)
        except OSError:
            pass
        self.apply_migrations()

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        with self._lock:
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                yield self._conn
            except Exception:
                self._conn.rollback()
                raise
            else:
                self._conn.commit()

    def apply_migrations(self) -> int:
        with self.transaction() as conn:
            conn.execute(
                "CREATE TABLE IF NOT EXISTS schema_migrations (version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL)"
            )
            row = conn.execute("SELECT MAX(version) AS version FROM schema_migrations").fetchone()
            current = int(row["version"] or 0)
            if current < 1:
                conn.executescript(_UP_V1)
                conn.execute(
                    "INSERT INTO schema_migrations(version, applied_at) VALUES (?, ?)",
                    (1, self.now()),
                )
                current = 1
            if current != SCHEMA_VERSION:
                raise PlatformError(ErrorCode.CONFLICT, "Unsupported database schema version.")
            return current

    def rollback_latest(self) -> int:
        with self.transaction() as conn:
            row = conn.execute("SELECT MAX(version) AS version FROM schema_migrations").fetchone()
            current = int(row["version"] or 0)
            if current == 1:
                conn.executescript(_DOWN_V1)
                conn.execute("DELETE FROM schema_migrations WHERE version=1")
                return 0
            return current

    def schema_version(self) -> int:
        with self._lock:
            row = self._conn.execute("SELECT MAX(version) AS version FROM schema_migrations").fetchone()
            return int(row["version"] or 0)

    def now(self) -> str:
        return _iso(self._clock())

    def create_tenant(self, display_name: str) -> str:
        if not display_name.strip():
            raise PlatformError(ErrorCode.INVALID_REQUEST, "Tenant name is required.")
        tenant_id = _public_id("tenant")
        with self.transaction() as conn:
            conn.execute(
                "INSERT INTO tenants(tenant_id, display_name, created_at) VALUES (?, ?, ?)",
                (tenant_id, display_name.strip(), self.now()),
            )
        return tenant_id

    def create_user(
        self,
        tenant_id: str,
        email_hash: str,
        password_hash: str,
        role: Role,
    ) -> str:
        if not tenant_id or not email_hash or not password_hash or not isinstance(role, Role):
            raise PlatformError(ErrorCode.INVALID_REQUEST, "Complete user data is required.")
        user_id = _public_id("user")
        try:
            with self.transaction() as conn:
                exists = conn.execute(
                    "SELECT 1 FROM tenants WHERE tenant_id=?", (tenant_id,)
                ).fetchone()
                if exists is None:
                    raise PlatformError(ErrorCode.NOT_FOUND, "Tenant not found.")
                conn.execute(
                    "INSERT INTO users(user_id, tenant_id, email_hash, password_hash, role, created_at) VALUES (?, ?, ?, ?, ?, ?)",
                    (user_id, tenant_id, email_hash, password_hash, role.value, self.now()),
                )
        except sqlite3.IntegrityError as exc:
            raise PlatformError(ErrorCode.CONFLICT, "User already exists.") from exc
        return user_id

    def user_by_email_hash(self, tenant_id: str, email_hash: str) -> Mapping[str, Any] | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM users WHERE tenant_id=? AND email_hash=? AND deleted_at IS NULL",
                (tenant_id, email_hash),
            ).fetchone()
            return dict(row) if row is not None else None

    def create_session(
        self,
        *,
        session_id: str,
        session_hash: str,
        tenant_id: str,
        user_id: str,
        expires_at: str,
    ) -> None:
        with self.transaction() as conn:
            conn.execute(
                "INSERT INTO sessions(session_hash, session_id, tenant_id, user_id, expires_at, created_at) VALUES (?, ?, ?, ?, ?, ?)",
                (session_hash, session_id, tenant_id, user_id, expires_at, self.now()),
            )

    def session(self, session_hash: str) -> Mapping[str, Any] | None:
        with self._lock:
            row = self._conn.execute(
                """SELECT s.*, u.role, u.deleted_at FROM sessions s
                   JOIN users u ON u.user_id=s.user_id
                   WHERE s.session_hash=?""",
                (session_hash,),
            ).fetchone()
            return dict(row) if row is not None else None

    def revoke_session(self, principal: Principal) -> None:
        if not principal.session_id:
            return
        with self.transaction() as conn:
            conn.execute(
                "UPDATE sessions SET revoked_at=? WHERE session_id=? AND tenant_id=? AND user_id=?",
                (self.now(), principal.session_id, principal.tenant_id, principal.user_id),
            )

    @staticmethod
    def _authorise(row: Mapping[str, Any], principal: Principal) -> None:
        if row["tenant_id"] != principal.tenant_id:
            raise PlatformError(ErrorCode.NOT_FOUND, "Resource not found.")
        if row["owner_id"] != principal.user_id and not principal.can_manage_tenant:
            raise PlatformError(ErrorCode.NOT_FOUND, "Resource not found.")

    def create_submission_and_job(
        self,
        principal: Principal,
        *,
        idempotency_hash: str,
        request_sha256: str,
        task_type: str,
        question: str,
        candidate_script: str,
        target_band: float | None,
        upload_id: str | None,
        max_attempts: int = 3,
    ) -> tuple[Mapping[str, Any], bool]:
        if not idempotency_hash or not request_sha256 or max_attempts < 1:
            raise PlatformError(ErrorCode.INVALID_REQUEST, "Invalid submission identity.")
        with self.transaction() as conn:
            existing = conn.execute(
                "SELECT * FROM submissions WHERE tenant_id=? AND owner_id=? AND idempotency_hash=?",
                (principal.tenant_id, principal.user_id, idempotency_hash),
            ).fetchone()
            if existing is not None:
                if existing["request_sha256"] != request_sha256:
                    raise PlatformError(
                        ErrorCode.IDEMPOTENCY_CONFLICT,
                        "The idempotency key is already bound to different input.",
                    )
                return dict(existing), False
            if upload_id:
                upload = conn.execute("SELECT * FROM uploads WHERE upload_id=?", (upload_id,)).fetchone()
                if upload is None:
                    raise PlatformError(ErrorCode.NOT_FOUND, "Upload not found.")
                self._authorise(upload, principal)
            submission_id = _public_id("submission")
            job_id = _public_id("job")
            now = self.now()
            conn.execute(
                """INSERT INTO submissions(
                    submission_id, tenant_id, owner_id, idempotency_hash, request_sha256,
                    task_type, question, candidate_script, target_band, upload_id,
                    state, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    submission_id,
                    principal.tenant_id,
                    principal.user_id,
                    idempotency_hash,
                    request_sha256,
                    task_type,
                    question,
                    candidate_script,
                    target_band,
                    upload_id,
                    SubmissionState.QUEUED.value,
                    now,
                    now,
                ),
            )
            conn.execute(
                """INSERT INTO jobs(
                    job_id, submission_id, tenant_id, owner_id, state,
                    max_attempts, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    job_id,
                    submission_id,
                    principal.tenant_id,
                    principal.user_id,
                    JobState.QUEUED.value,
                    max_attempts,
                    now,
                    now,
                ),
            )
            row = conn.execute("SELECT * FROM submissions WHERE submission_id=?", (submission_id,)).fetchone()
            return dict(row), True

    def submission(self, principal: Principal, submission_id: str, *, include_raw: bool = False) -> Mapping[str, Any]:
        with self._lock:
            row = self._conn.execute("SELECT * FROM submissions WHERE submission_id=?", (submission_id,)).fetchone()
            if row is None:
                raise PlatformError(ErrorCode.NOT_FOUND, "Submission not found.")
            self._authorise(row, principal)
            result = dict(row)
            if not include_raw:
                result.pop("question", None)
                result.pop("candidate_script", None)
                result.pop("idempotency_hash", None)
            return result

    def worker_submission(self, submission_id: str) -> Mapping[str, Any]:
        with self._lock:
            row = self._conn.execute("SELECT * FROM submissions WHERE submission_id=?", (submission_id,)).fetchone()
            if row is None:
                raise PlatformError(ErrorCode.NOT_FOUND, "Submission not found.")
            return dict(row)

    def history(self, principal: Principal, *, limit: int = 50) -> tuple[Mapping[str, Any], ...]:
        limit = max(1, min(int(limit), 100))
        with self._lock:
            rows = self._conn.execute(
                """SELECT submission_id, task_type, state, accepted_result_sha256,
                          version, created_at, updated_at
                   FROM submissions WHERE tenant_id=? AND owner_id=?
                   ORDER BY created_at DESC, submission_id DESC LIMIT ?""",
                (principal.tenant_id, principal.user_id, limit),
            ).fetchall()
            return tuple(dict(row) for row in rows)

    def report(self, principal: Principal, submission_id: str) -> Mapping[str, Any]:
        self.submission(principal, submission_id)
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM artifacts WHERE submission_id=? AND kind='REPORT'",
                (submission_id,),
            ).fetchone()
            if row is None:
                raise PlatformError(ErrorCode.NOT_FOUND, "Completed report not found.")
            self._authorise(row, principal)
            return {
                "artifactId": row["artifact_id"],
                "contentSha256": row["content_sha256"],
                "payload": json.loads(row["payload_json"]),
            }

    def criterion_scoring_diagnostic(self, principal: Principal, submission_id: str) -> Mapping[str, Any] | None:
        """Internal owner-scoped audit seam, deliberately absent from learner routes."""
        if not isinstance(principal, Principal):
            raise PlatformError(ErrorCode.UNAUTHENTICATED, "A complete principal is required.")
        with self._lock:
            submission = self.submission(principal, submission_id)
            if submission["owner_id"] != principal.user_id:
                raise PlatformError(ErrorCode.NOT_FOUND, "Resource not found.")
            row = self._conn.execute(
                "SELECT * FROM artifacts WHERE submission_id=? AND kind='CRITERION_SCORING_DIAGNOSTIC'",
                (submission_id,),
            ).fetchone()
            if row is None:
                return None  # Historical opaque executions stay opaque.
            self._authorise(row, principal)
            from .criterion_diagnostic import CriterionScoringDiagnostic
            value = CriterionScoringDiagnostic(row["payload_json"]).content()
            if digest(value) != row["content_sha256"] or value["submissionId"] != submission_id:
                raise PlatformError(ErrorCode.CONFLICT, "Diagnostic integrity check failed.")
            return {"artifactId": row["artifact_id"], "version": row["version"],
                    "contentSha256": row["content_sha256"], "payload": value}

    def job_for_submission(self, principal: Principal, submission_id: str) -> Mapping[str, Any]:
        self.submission(principal, submission_id)
        with self._lock:
            row = self._conn.execute("SELECT * FROM jobs WHERE submission_id=?", (submission_id,)).fetchone()
            if row is None:
                raise PlatformError(ErrorCode.NOT_FOUND, "Job not found.")
            result = dict(row)
            result.pop("lease_owner", None)
            return result

    def _recover_expired(self, conn: sqlite3.Connection, now: str) -> None:
        rows = conn.execute(
            "SELECT * FROM jobs WHERE state=? AND lease_expires_at<=?",
            (JobState.LEASED.value, now),
        ).fetchall()
        for row in rows:
            if row["attempts"] >= row["max_attempts"]:
                conn.execute(
                    "UPDATE jobs SET state=?, failure_code=?, lease_owner=NULL, lease_expires_at=NULL, version=version+1, updated_at=? WHERE job_id=?",
                    (JobState.FAILED.value, "LEASE_EXHAUSTED", now, row["job_id"]),
                )
                conn.execute(
                    "UPDATE submissions SET state=?, version=version+1, updated_at=? WHERE submission_id=? AND accepted_result_sha256 IS NULL",
                    (SubmissionState.FAILED.value, now, row["submission_id"]),
                )
            else:
                conn.execute(
                    "UPDATE jobs SET state=?, lease_owner=NULL, lease_expires_at=NULL, version=version+1, updated_at=? WHERE job_id=?",
                    (JobState.RETRY_WAIT.value, now, row["job_id"]),
                )

    def claim_next_job(self, worker_id: str, *, lease_seconds: int = 60) -> ClaimedJob | None:
        if not worker_id or not (1 <= lease_seconds <= 3600):
            raise PlatformError(ErrorCode.INVALID_REQUEST, "Invalid worker lease.")
        with self.transaction() as conn:
            now = self.now()
            self._recover_expired(conn, now)
            row = conn.execute(
                """SELECT * FROM jobs WHERE state IN (?, ?)
                   ORDER BY created_at, job_id LIMIT 1""",
                (JobState.QUEUED.value, JobState.RETRY_WAIT.value),
            ).fetchone()
            if row is None:
                return None
            if row["cancel_requested"]:
                conn.execute(
                    "UPDATE jobs SET state=?, updated_at=?, version=version+1 WHERE job_id=?",
                    (JobState.CANCELLED.value, now, row["job_id"]),
                )
                conn.execute(
                    "UPDATE submissions SET state=?, updated_at=?, version=version+1 WHERE submission_id=?",
                    (SubmissionState.CANCELLED.value, now, row["submission_id"]),
                )
                return None
            expires = _iso(self._clock() + timedelta(seconds=lease_seconds))
            attempt = int(row["attempts"]) + 1
            conn.execute(
                """UPDATE jobs SET state=?, attempts=?, lease_owner=?, lease_expires_at=?,
                       progress=MAX(progress, 1), version=version+1, updated_at=? WHERE job_id=?""",
                (JobState.LEASED.value, attempt, worker_id, expires, now, row["job_id"]),
            )
            conn.execute(
                "UPDATE submissions SET state=?, version=version+1, updated_at=? WHERE submission_id=? AND accepted_result_sha256 IS NULL",
                (SubmissionState.PROCESSING.value, now, row["submission_id"]),
            )
            return ClaimedJob(
                row["job_id"],
                row["submission_id"],
                row["tenant_id"],
                row["owner_id"],
                attempt,
                worker_id,
                expires,
            )

    def update_job_progress(self, job_id: str, worker_id: str, progress: int) -> None:
        if not (1 <= int(progress) <= 99):
            raise PlatformError(ErrorCode.INVALID_REQUEST, "Progress must be between 1 and 99.")
        with self.transaction() as conn:
            row = conn.execute("SELECT * FROM jobs WHERE job_id=?", (job_id,)).fetchone()
            if row is None or row["state"] != JobState.LEASED.value or row["lease_owner"] != worker_id:
                raise PlatformError(ErrorCode.CONFLICT, "The worker does not own this lease.")
            if int(progress) < int(row["progress"]):
                raise PlatformError(ErrorCode.CONFLICT, "Job progress is monotonic.")
            conn.execute(
                "UPDATE jobs SET progress=?, version=version+1, updated_at=? WHERE job_id=?",
                (int(progress), self.now(), job_id),
            )

    def complete_job(self, job_id: str, worker_id: str, outcome: ProcessingOutcome) -> str:
        cancelled = False
        result_sha = ""
        with self.transaction() as conn:
            job = conn.execute("SELECT * FROM jobs WHERE job_id=?", (job_id,)).fetchone()
            if job is None or job["state"] != JobState.LEASED.value or job["lease_owner"] != worker_id:
                raise PlatformError(ErrorCode.CONFLICT, "The worker does not own this lease.")
            now = self.now()
            if job["cancel_requested"]:
                conn.execute(
                    "UPDATE jobs SET state=?, lease_owner=NULL, lease_expires_at=NULL, progress=0, version=version+1, updated_at=? WHERE job_id=?",
                    (JobState.CANCELLED.value, now, job_id),
                )
                conn.execute(
                    "UPDATE submissions SET state=?, version=version+1, updated_at=? WHERE submission_id=? AND accepted_result_sha256 IS NULL",
                    (SubmissionState.CANCELLED.value, now, job["submission_id"]),
                )
                cancelled = True
            else:
                submission = conn.execute(
                    "SELECT * FROM submissions WHERE submission_id=?", (job["submission_id"],)
                ).fetchone()
                result_sha = outcome.result_sha256
                accepted = submission["accepted_result_sha256"]
                if accepted is not None and accepted != result_sha:
                    raise PlatformError(ErrorCode.CONFLICT, "A different semantic result was already accepted.")
                if outcome.criterion_diagnostic is not None:
                    value = outcome.criterion_diagnostic.content()
                    if value["submissionId"] != job["submission_id"] or value["outcome"] == "COMPLETE" or submission["task_type"] != "task2":
                        raise PlatformError(ErrorCode.CONFLICT, "Diagnostic submission or outcome mismatch.")
                    if not job["tenant_id"] or not job["owner_id"] or (job["tenant_id"], job["owner_id"]) != (submission["tenant_id"], submission["owner_id"]):
                        raise PlatformError(ErrorCode.FORBIDDEN, "Diagnostic ownership is required.")
                    existing = conn.execute("SELECT content_sha256, version FROM artifacts WHERE submission_id=? AND kind='CRITERION_SCORING_DIAGNOSTIC'", (job["submission_id"],)).fetchone()
                    if existing is not None and (existing["content_sha256"] != digest(value) or existing["version"] != 1):
                        raise PlatformError(ErrorCode.CONFLICT, "A different diagnostic version was already accepted.")
                    if existing is None:
                        if accepted is not None:
                            raise PlatformError(ErrorCode.CONFLICT, "Historical diagnostics cannot be fabricated.")
                        conn.execute("""INSERT INTO artifacts(artifact_id, submission_id, tenant_id, owner_id, kind,
                            payload_json, content_sha256, created_at) VALUES (?, ?, ?, ?, 'CRITERION_SCORING_DIAGNOSTIC', ?, ?, ?)""",
                            (_public_id("artifact"), job["submission_id"], job["tenant_id"], job["owner_id"], canonical_json(value), digest(value), now))
                if accepted is None:
                    if outcome.state is SubmissionState.COMPLETE:
                        payload_json = canonical_json(dict(outcome.payload or {}))
                        artifact_sha = digest(json.loads(payload_json))
                        conn.execute(
                            """INSERT INTO artifacts(
                                artifact_id, submission_id, tenant_id, owner_id, kind,
                                payload_json, content_sha256, created_at
                            ) VALUES (?, ?, ?, ?, 'REPORT', ?, ?, ?)""",
                            (
                                _public_id("artifact"),
                                job["submission_id"],
                                job["tenant_id"],
                                job["owner_id"],
                                payload_json,
                                artifact_sha,
                                now,
                            ),
                        )
                    conn.execute(
                        """UPDATE submissions SET state=?, accepted_result_sha256=?,
                               version=version+1, updated_at=? WHERE submission_id=?""",
                        (outcome.state.value, result_sha, now, job["submission_id"]),
                    )
                conn.execute(
                    """UPDATE jobs SET state=?, progress=100, lease_owner=NULL,
                           lease_expires_at=NULL, failure_code=?, version=version+1,
                           updated_at=? WHERE job_id=?""",
                    (JobState.SUCCEEDED.value, outcome.failure_code, now, job_id),
                )
        if cancelled:
            raise PlatformError(ErrorCode.CANCELLED, "The job was cancelled before completion.")
        return result_sha

    def fail_job(self, job_id: str, worker_id: str, *, failure_code: str, retriable: bool) -> JobState:
        safe_code = str(failure_code)[:80] or "WORKER_FAILURE"
        with self.transaction() as conn:
            row = conn.execute("SELECT * FROM jobs WHERE job_id=?", (job_id,)).fetchone()
            if row is None or row["state"] != JobState.LEASED.value or row["lease_owner"] != worker_id:
                raise PlatformError(ErrorCode.CONFLICT, "The worker does not own this lease.")
            can_retry = retriable and row["attempts"] < row["max_attempts"] and not row["cancel_requested"]
            state = JobState.RETRY_WAIT if can_retry else JobState.FAILED
            submission_state = SubmissionState.QUEUED if can_retry else SubmissionState.FAILED
            if row["cancel_requested"]:
                state = JobState.CANCELLED
                submission_state = SubmissionState.CANCELLED
            now = self.now()
            conn.execute(
                """UPDATE jobs SET state=?, failure_code=?, lease_owner=NULL,
                       lease_expires_at=NULL, version=version+1, updated_at=? WHERE job_id=?""",
                (state.value, safe_code, now, job_id),
            )
            conn.execute(
                "UPDATE submissions SET state=?, version=version+1, updated_at=? WHERE submission_id=? AND accepted_result_sha256 IS NULL",
                (submission_state.value, now, row["submission_id"]),
            )
            return state

    def request_cancel(self, principal: Principal, submission_id: str) -> JobState:
        self.submission(principal, submission_id)
        with self.transaction() as conn:
            row = conn.execute("SELECT * FROM jobs WHERE submission_id=?", (submission_id,)).fetchone()
            if row is None:
                raise PlatformError(ErrorCode.NOT_FOUND, "Job not found.")
            state = JobState(row["state"])
            if state in {JobState.SUCCEEDED, JobState.FAILED, JobState.CANCELLED}:
                return state
            now = self.now()
            if state in {JobState.QUEUED, JobState.RETRY_WAIT}:
                conn.execute(
                    "UPDATE jobs SET state=?, cancel_requested=1, version=version+1, updated_at=? WHERE job_id=?",
                    (JobState.CANCELLED.value, now, row["job_id"]),
                )
                conn.execute(
                    "UPDATE submissions SET state=?, version=version+1, updated_at=? WHERE submission_id=?",
                    (SubmissionState.CANCELLED.value, now, submission_id),
                )
                return JobState.CANCELLED
            conn.execute(
                "UPDATE jobs SET cancel_requested=1, version=version+1, updated_at=? WHERE job_id=?",
                (now, row["job_id"]),
            )
            return state

    def append_submission_record(
        self,
        principal: Principal,
        submission_id: str,
        *,
        table: str,
        id_column: str,
        id_prefix: str,
        payload: Mapping[str, Any],
    ) -> Mapping[str, Any]:
        if table not in {"revisions", "hint_events"}:
            raise ValueError("unsupported append table")
        self.submission(principal, submission_id)
        payload_json = canonical_json(dict(payload))
        record_id = _public_id(id_prefix)
        content_sha = digest(json.loads(payload_json))
        with self.transaction() as conn:
            conn.execute(
                f"INSERT INTO {table}({id_column}, submission_id, tenant_id, owner_id, payload_json, content_sha256, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    record_id,
                    submission_id,
                    principal.tenant_id,
                    principal.user_id,
                    payload_json,
                    content_sha,
                    self.now(),
                ),
            )
        return {id_column: record_id, "content_sha256": content_sha}

    def append_memory(self, principal: Principal, payload: Mapping[str, Any]) -> Mapping[str, Any]:
        payload_json = canonical_json(dict(payload))
        memory_id = _public_id("memory")
        content_sha = digest(json.loads(payload_json))
        with self.transaction() as conn:
            conn.execute(
                "INSERT INTO memory_records(memory_id, tenant_id, owner_id, payload_json, content_sha256, created_at) VALUES (?, ?, ?, ?, ?, ?)",
                (memory_id, principal.tenant_id, principal.user_id, payload_json, content_sha, self.now()),
            )
        return {"memory_id": memory_id, "content_sha256": content_sha}

    def learning_records(self, principal: Principal, submission_id: str, table: str) -> tuple[Mapping[str, Any], ...]:
        if table not in {"revisions", "hint_events"}:
            raise ValueError("Unsupported learning table.")
        self.submission(principal, submission_id)
        with self._lock:
            rows = self._conn.execute(f"SELECT payload_json, content_sha256 FROM {table} WHERE submission_id=? AND tenant_id=? AND owner_id=? ORDER BY created_at, rowid",
                (submission_id, principal.tenant_id, principal.user_id)).fetchall()
            records = []
            for row in rows:
                payload = json.loads(row['payload_json'])
                if digest(payload) != row['content_sha256']:
                    raise PlatformError(ErrorCode.CONFLICT, "Learning record integrity mismatch.")
                if payload.get('kind') == 'WEB_LEARNING_V1': records.append(payload)
            return tuple(records)

    def append_learning_record(self, principal, submission_id, *, table, stream_id, command_id, command_sha256, expected_version, content):
        if table not in {"revisions", "hint_events"} or not stream_id or not command_id:
            raise PlatformError(ErrorCode.INVALID_REQUEST, "Learning command identity required.")
        self.submission(principal, submission_id)
        id_column, prefix = ('revision_id', 'revision') if table == 'revisions' else ('hint_event_id', 'hint')
        with self.transaction() as conn:
            records = self.learning_records(principal, submission_id, table)
            existing = next((r for r in records if r['streamId'] == stream_id and r['commandId'] == command_id), None)
            if existing:
                if existing['commandSha256'] != command_sha256:
                    raise PlatformError(ErrorCode.IDEMPOTENCY_CONFLICT, "Learning command was reused with different input.")
                return existing
            version = max((r['version'] for r in records if r['streamId'] == stream_id), default=0)
            if type(expected_version) is not int or expected_version != version:
                raise PlatformError(ErrorCode.CONFLICT, "Learning state changed; reload required.")
            payload = {'kind': 'WEB_LEARNING_V1', 'streamId': stream_id, 'commandId': command_id,
                'commandSha256': command_sha256, 'version': version + 1, 'content': dict(content)}
            conn.execute(f"INSERT INTO {table}({id_column}, submission_id, tenant_id, owner_id, payload_json, content_sha256, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (_public_id(prefix), submission_id, principal.tenant_id, principal.user_id, canonical_json(payload), digest(payload), self.now()))
            return payload

    def pending_learning_work(self):
        """Trusted worker inventory; never exposed through learner APIs."""
        with self._lock:
            rows = self._conn.execute("SELECT submission_id, tenant_id, owner_id, payload_json, content_sha256 FROM revisions ORDER BY created_at, rowid").fetchall()
            latest = {}
            for row in rows:
                payload = json.loads(row['payload_json'])
                if payload.get('kind') != 'WEB_LEARNING_V1': continue
                if digest(payload) != row['content_sha256']:
                    raise PlatformError(ErrorCode.CONFLICT, "Learning record integrity mismatch.")
                latest[(row['submission_id'], payload['streamId'])] = {**dict(row), 'record': payload}
            return tuple(item for item in latest.values() if item['record']['content'].get('state') in {'QUEUED', 'RUNNING'})

    def learning_memory_inputs(self, principal):
        with self._lock:
            rows = self._conn.execute("SELECT submission_id, payload_json, content_sha256 FROM revisions WHERE tenant_id=? AND owner_id=? ORDER BY created_at, rowid",
                (principal.tenant_id, principal.user_id)).fetchall()
            latest = {}
            for row in rows:
                payload = json.loads(row['payload_json'])
                if digest(payload) != row['content_sha256']: raise PlatformError(ErrorCode.CONFLICT, 'Learning record integrity mismatch.')
                if payload.get('kind') == 'WEB_LEARNING_V1': latest[(row['submission_id'], payload['streamId'])] = payload['content']
            return tuple({'submissionId': sid, **event} for (sid, _), content in latest.items() if content.get('state') == 'COMPLETE' for event in content.get('memoryEvents', ()))

    def memory(self, principal: Principal) -> tuple[Mapping[str, Any], ...]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT memory_id, payload_json, content_sha256, created_at FROM memory_records WHERE tenant_id=? AND owner_id=? ORDER BY created_at, memory_id",
                (principal.tenant_id, principal.user_id),
            ).fetchall()
            return tuple({
                "memoryId": row["memory_id"],
                "payload": json.loads(row["payload_json"]),
                "contentSha256": row["content_sha256"],
                "createdAt": row["created_at"],
            } for row in rows)

    def create_support_case(
        self,
        principal: Principal,
        *,
        category: str,
        failure_code: str | None = None,
        submission_id: str | None = None,
    ) -> Mapping[str, Any]:
        allowed = {"BAD_CASE", "PRIVACY", "RELIABILITY", "ACCESS", "OTHER"}
        if category not in allowed:
            raise PlatformError(ErrorCode.INVALID_REQUEST, "Support category is invalid.")
        if failure_code is not None and (len(failure_code) > 80 or "\n" in failure_code):
            raise PlatformError(ErrorCode.INVALID_REQUEST, "Support failure code is invalid.")
        if submission_id is not None:
            self.submission(principal, submission_id)
        case_id = _public_id("case")
        with self.transaction() as conn:
            conn.execute(
                "INSERT INTO support_cases(case_id, tenant_id, owner_id, submission_id, category, failure_code, status, created_at) VALUES (?, ?, ?, ?, ?, ?, 'OPEN', ?)",
                (
                    case_id,
                    principal.tenant_id,
                    principal.user_id,
                    submission_id,
                    category,
                    failure_code,
                    self.now(),
                ),
            )
        return {
            "caseId": case_id,
            "category": category,
            "failureCode": failure_code,
            "submissionId": submission_id,
            "status": "OPEN",
        }

    def register_upload(
        self,
        principal: Principal,
        *,
        media_type: str,
        byte_size: int,
        content_sha256: str,
        storage_key: str,
    ) -> str:
        upload_id = _public_id("upload")
        with self.transaction() as conn:
            conn.execute(
                "INSERT INTO uploads(upload_id, tenant_id, owner_id, media_type, byte_size, content_sha256, storage_key, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    upload_id,
                    principal.tenant_id,
                    principal.user_id,
                    media_type,
                    int(byte_size),
                    content_sha256,
                    storage_key,
                    self.now(),
                ),
            )
        return upload_id

    def upload(self, principal: Principal, upload_id: str) -> Mapping[str, Any]:
        with self._lock:
            row = self._conn.execute("SELECT * FROM uploads WHERE upload_id=?", (upload_id,)).fetchone()
            if row is None:
                raise PlatformError(ErrorCode.NOT_FOUND, "Upload not found.")
            self._authorise(row, principal)
            return dict(row)

    def set_consent(
        self,
        principal: Principal,
        purpose: ConsentPurpose,
        *,
        granted: bool,
        policy_version: str,
    ) -> Mapping[str, Any]:
        if not isinstance(granted, bool) or not policy_version:
            raise PlatformError(ErrorCode.INVALID_REQUEST, "Consent requires a policy version.")
        with self.transaction() as conn:
            conn.execute(
                """INSERT INTO consents(tenant_id, owner_id, purpose, granted, policy_version, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?)
                   ON CONFLICT(tenant_id, owner_id, purpose) DO UPDATE SET
                       granted=excluded.granted,
                       policy_version=excluded.policy_version,
                       updated_at=excluded.updated_at""",
                (
                    principal.tenant_id,
                    principal.user_id,
                    purpose.value,
                    int(granted),
                    policy_version,
                    self.now(),
                ),
            )
        return self.consent(principal, purpose)

    def consent(self, principal: Principal, purpose: ConsentPurpose) -> Mapping[str, Any]:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM consents WHERE tenant_id=? AND owner_id=? AND purpose=?",
                (principal.tenant_id, principal.user_id, purpose.value),
            ).fetchone()
            if row is None:
                return {
                    "purpose": purpose.value,
                    "granted": False,
                    "policyVersion": None,
                    "state": "UNKNOWN_DENY",
                }
            return {
                "purpose": purpose.value,
                "granted": bool(row["granted"]),
                "policyVersion": row["policy_version"],
                "state": "GRANTED" if row["granted"] else "REVOKED",
                "updatedAt": row["updated_at"],
            }

    def audit_receipt(
        self,
        *,
        tenant_id: str,
        owner_id: str | None,
        action: str,
        detail: Mapping[str, Any],
    ) -> Mapping[str, Any]:
        created_at = self.now()
        subject_sha = digest({"tenantId": tenant_id, "ownerId": owner_id})
        content = {
            "tenantId": tenant_id,
            "subjectSha256": subject_sha,
            "action": action,
            "detail": dict(detail),
            "createdAt": created_at,
        }
        receipt_sha = digest(content)
        receipt_id = f"receipt_{receipt_sha[:32]}"
        with self.transaction() as conn:
            conn.execute(
                "INSERT OR IGNORE INTO audit_receipts(receipt_id, tenant_id, owner_id, subject_sha256, action, detail_json, receipt_sha256, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    receipt_id,
                    tenant_id,
                    owner_id,
                    subject_sha,
                    action,
                    canonical_json(dict(detail)),
                    receipt_sha,
                    created_at,
                ),
            )
        return {"receiptId": receipt_id, "receiptSha256": receipt_sha, **content}

    def export_subject(self, principal: Principal) -> Mapping[str, Any]:
        with self._lock:
            user = self._conn.execute(
                "SELECT user_id, tenant_id, role, created_at FROM users WHERE tenant_id=? AND user_id=? AND deleted_at IS NULL",
                (principal.tenant_id, principal.user_id),
            ).fetchone()
            if user is None:
                raise PlatformError(ErrorCode.NOT_FOUND, "Subject not found.")
            submissions = [dict(row) for row in self._conn.execute(
                "SELECT * FROM submissions WHERE tenant_id=? AND owner_id=? ORDER BY created_at, submission_id",
                (principal.tenant_id, principal.user_id),
            ).fetchall()]
            submission_ids = [row["submission_id"] for row in submissions]
            def owned(table: str) -> list[dict[str, Any]]:
                return [dict(row) for row in self._conn.execute(
                    f"SELECT * FROM {table} WHERE tenant_id=? AND owner_id=? ORDER BY created_at",
                    (principal.tenant_id, principal.user_id),
                ).fetchall()]
            artifacts: list[dict[str, Any]] = []
            jobs: list[dict[str, Any]] = []
            if submission_ids:
                placeholders = ",".join("?" for _ in submission_ids)
                artifacts = [dict(row) for row in self._conn.execute(
                    f"SELECT * FROM artifacts WHERE submission_id IN ({placeholders}) ORDER BY created_at",
                    submission_ids,
                ).fetchall()]
                jobs = [dict(row) for row in self._conn.execute(
                    f"SELECT * FROM jobs WHERE submission_id IN ({placeholders}) ORDER BY created_at",
                    submission_ids,
                ).fetchall()]
            consents = [dict(row) for row in self._conn.execute(
                "SELECT * FROM consents WHERE tenant_id=? AND owner_id=? ORDER BY purpose",
                (principal.tenant_id, principal.user_id),
            ).fetchall()]
            actor_sha = digest({"tenantId": principal.tenant_id, "ownerId": principal.user_id})
            metric_events = [dict(row) for row in self._conn.execute(
                "SELECT * FROM metric_events WHERE tenant_id=? AND actor_sha256=? ORDER BY created_at, event_id",
                (principal.tenant_id, actor_sha),
            ).fetchall()]
            receipts = [dict(row) for row in self._conn.execute(
                "SELECT * FROM audit_receipts WHERE tenant_id=? AND (owner_id=? OR subject_sha256=?) ORDER BY created_at, receipt_id",
                (principal.tenant_id, principal.user_id, actor_sha),
            ).fetchall()]
            quotas = [dict(row) for row in self._conn.execute(
                "SELECT * FROM quota_usage WHERE tenant_id=? AND owner_id=? ORDER BY quota_name, window_key",
                (principal.tenant_id, principal.user_id),
            ).fetchall()]
            return {
                "schemaVersion": "subject-export-v1",
                "subject": dict(user),
                "submissions": submissions,
                "artifacts": artifacts,
                "jobs": jobs,
                "revisions": owned("revisions"),
                "hintEvents": owned("hint_events"),
                "memoryRecords": owned("memory_records"),
                "supportCases": owned("support_cases"),
                "uploads": owned("uploads"),
                "consents": consents,
                "metricEvents": metric_events,
                "auditReceipts": receipts,
                "quotaUsage": quotas,
            }

    def request_deletion(self, principal: Principal) -> Mapping[str, Any]:
        subject_sha = digest({"tenantId": principal.tenant_id, "ownerId": principal.user_id})
        with self.transaction() as conn:
            existing = conn.execute(
                "SELECT * FROM deletion_requests WHERE tenant_id=? AND subject_sha256=?",
                (principal.tenant_id, subject_sha),
            ).fetchone()
            if existing is not None:
                return dict(existing)
            deletion_id = _public_id("deletion")
            conn.execute(
                "INSERT INTO deletion_requests(deletion_id, tenant_id, owner_id, subject_sha256, state, requested_at) VALUES (?, ?, ?, ?, 'REQUESTED', ?)",
                (deletion_id, principal.tenant_id, principal.user_id, subject_sha, self.now()),
            )
            row = conn.execute("SELECT * FROM deletion_requests WHERE deletion_id=?", (deletion_id,)).fetchone()
            return dict(row)

    def deletion_request(self, deletion_id: str) -> Mapping[str, Any]:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM deletion_requests WHERE deletion_id=?",
                (deletion_id,),
            ).fetchone()
            if row is None:
                raise PlatformError(ErrorCode.NOT_FOUND, "Deletion request not found.")
            return dict(row)

    def uploads_for_subject(self, tenant_id: str, owner_id: str) -> tuple[Mapping[str, Any], ...]:
        with self._lock:
            return tuple(dict(row) for row in self._conn.execute(
                "SELECT * FROM uploads WHERE tenant_id=? AND owner_id=?",
                (tenant_id, owner_id),
            ).fetchall())

    @staticmethod
    def _purge_subject_conn(conn: sqlite3.Connection, tenant_id: str, owner_id: str) -> None:
        submission_ids = [row[0] for row in conn.execute(
            "SELECT submission_id FROM submissions WHERE tenant_id=? AND owner_id=?",
            (tenant_id, owner_id),
        ).fetchall()]
        for table in ("metric_events", "quota_usage", "consents", "uploads", "memory_records", "support_cases"):
            owner_column = "actor_sha256" if table == "metric_events" else "owner_id"
            if table == "metric_events":
                conn.execute(
                    "DELETE FROM metric_events WHERE tenant_id=? AND actor_sha256=?",
                    (tenant_id, digest({"tenantId": tenant_id, "ownerId": owner_id})),
                )
            else:
                conn.execute(
                    f"DELETE FROM {table} WHERE tenant_id=? AND {owner_column}=?",
                    (tenant_id, owner_id),
                )
        if submission_ids:
            placeholders = ",".join("?" for _ in submission_ids)
            conn.execute(f"DELETE FROM submissions WHERE submission_id IN ({placeholders})", submission_ids)
        conn.execute("DELETE FROM sessions WHERE tenant_id=? AND user_id=?", (tenant_id, owner_id))
        conn.execute("DELETE FROM audit_receipts WHERE tenant_id=? AND owner_id=?", (tenant_id, owner_id))
        conn.execute("DELETE FROM users WHERE tenant_id=? AND user_id=?", (tenant_id, owner_id))

    def retention_candidates(self, cutoff: str) -> tuple[Mapping[str, Any], ...]:
        terminal = (
            SubmissionState.COMPLETE.value,
            SubmissionState.PARTIAL.value,
            SubmissionState.REVIEW_REQUIRED.value,
            SubmissionState.FAILED.value,
            SubmissionState.CANCELLED.value,
        )
        placeholders = ",".join("?" for _ in terminal)
        with self._lock:
            rows = self._conn.execute(
                f"""SELECT submission_id, tenant_id, owner_id, upload_id, state, updated_at
                    FROM submissions WHERE updated_at<? AND state IN ({placeholders})
                    ORDER BY updated_at, submission_id""",
                (cutoff, *terminal),
            ).fetchall()
            return tuple(dict(row) for row in rows)

    def execute_submission_retention(self, submission_id: str) -> Mapping[str, Any]:
        with self.transaction() as conn:
            row = conn.execute(
                "SELECT submission_id, tenant_id, owner_id, upload_id, state, updated_at FROM submissions WHERE submission_id=?",
                (submission_id,),
            ).fetchone()
            if row is None:
                return {"submissionId": submission_id, "state": "ALREADY_ABSENT", "storageKey": None}
            if row["state"] not in {
                SubmissionState.COMPLETE.value,
                SubmissionState.PARTIAL.value,
                SubmissionState.REVIEW_REQUIRED.value,
                SubmissionState.FAILED.value,
                SubmissionState.CANCELLED.value,
            }:
                raise PlatformError(ErrorCode.CONFLICT, "Active submissions cannot be removed by retention.")
            storage_key = None
            if row["upload_id"] is not None:
                references = conn.execute(
                    "SELECT COUNT(*) AS count FROM submissions WHERE upload_id=? AND submission_id<>?",
                    (row["upload_id"], submission_id),
                ).fetchone()["count"]
                if not references:
                    upload = conn.execute("SELECT storage_key FROM uploads WHERE upload_id=?", (row["upload_id"],)).fetchone()
                    storage_key = upload["storage_key"] if upload is not None else None
                    conn.execute("DELETE FROM uploads WHERE upload_id=?", (row["upload_id"],))
            conn.execute("DELETE FROM submissions WHERE submission_id=?", (submission_id,))
            return {
                "submissionId": submission_id,
                "tenantId": row["tenant_id"],
                "ownerId": row["owner_id"],
                "state": "DELETED",
                "storageKey": storage_key,
            }

    @classmethod
    def purge_submission_from_backup(cls, backup_path: str | Path, submission_id: str) -> None:
        path = Path(backup_path).resolve()
        if not path.is_file():
            raise PlatformError(ErrorCode.NOT_FOUND, "Registered backup is missing.")
        conn = sqlite3.connect(str(path), isolation_level=None)
        try:
            conn.execute("PRAGMA foreign_keys=ON")
            conn.execute("BEGIN IMMEDIATE")
            upload = conn.execute("SELECT upload_id FROM submissions WHERE submission_id=?", (submission_id,)).fetchone()
            conn.execute("DELETE FROM submissions WHERE submission_id=?", (submission_id,))
            if upload is not None and upload[0] is not None:
                remaining = conn.execute("SELECT COUNT(*) FROM submissions WHERE upload_id=?", (upload[0],)).fetchone()[0]
                if not remaining:
                    conn.execute("DELETE FROM uploads WHERE upload_id=?", (upload[0],))
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def execute_primary_deletion(self, deletion_id: str) -> Mapping[str, Any]:
        with self.transaction() as conn:
            request = conn.execute("SELECT * FROM deletion_requests WHERE deletion_id=?", (deletion_id,)).fetchone()
            if request is None:
                raise PlatformError(ErrorCode.NOT_FOUND, "Deletion request not found.")
            if request["state"] == "COMPLETE":
                return dict(request)
            tenant_id = request["tenant_id"]
            owner_id = request["owner_id"]
            if owner_id is None:
                raise PlatformError(ErrorCode.CONFLICT, "Deletion subject is unavailable.")
            self._purge_subject_conn(conn, tenant_id, owner_id)
            conn.execute(
                "UPDATE deletion_requests SET owner_id=NULL, state='PRIMARY_COMPLETE', completed_at=? WHERE deletion_id=?",
                (self.now(), deletion_id),
            )
            return {
                "deletion_id": deletion_id,
                "tenant_id": tenant_id,
                "subject_sha256": request["subject_sha256"],
                "state": "PRIMARY_COMPLETE",
            }

    def complete_deletion(self, deletion_id: str) -> Mapping[str, Any]:
        with self.transaction() as conn:
            row = conn.execute("SELECT * FROM deletion_requests WHERE deletion_id=?", (deletion_id,)).fetchone()
            if row is None:
                raise PlatformError(ErrorCode.NOT_FOUND, "Deletion request not found.")
            conn.execute(
                "UPDATE deletion_requests SET state='COMPLETE', completed_at=COALESCE(completed_at, ?) WHERE deletion_id=?",
                (self.now(), deletion_id),
            )
            updated = conn.execute("SELECT * FROM deletion_requests WHERE deletion_id=?", (deletion_id,)).fetchone()
            return dict(updated)

    def record_metric_event(
        self,
        *,
        tenant_id: str,
        actor_sha256: str,
        event_type: str,
        dimensions: Mapping[str, str],
        numeric: Mapping[str, float],
    ) -> str:
        event_id = _public_id("event")
        with self.transaction() as conn:
            conn.execute(
                "INSERT INTO metric_events(event_id, tenant_id, actor_sha256, event_type, dimensions_json, numeric_json, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    event_id,
                    tenant_id,
                    actor_sha256,
                    event_type,
                    canonical_json(dict(dimensions)),
                    canonical_json(dict(numeric)),
                    self.now(),
                ),
            )
        return event_id

    def metric_events(self, event_type: str) -> tuple[Mapping[str, Any], ...]:
        with self._lock:
            return tuple(dict(row) for row in self._conn.execute(
                "SELECT * FROM metric_events WHERE event_type=? ORDER BY created_at, event_id",
                (event_type,),
            ).fetchall())

    def consume_quota(
        self,
        principal: Principal,
        *,
        quota_name: str,
        window_key: str,
        limit: int,
        amount: int = 1,
    ) -> int:
        if limit < 1 or amount < 1:
            raise PlatformError(ErrorCode.INVALID_REQUEST, "Quota values must be positive.")
        with self.transaction() as conn:
            row = conn.execute(
                "SELECT amount FROM quota_usage WHERE tenant_id=? AND owner_id=? AND quota_name=? AND window_key=?",
                (principal.tenant_id, principal.user_id, quota_name, window_key),
            ).fetchone()
            used = int(row["amount"] if row is not None else 0)
            if used + amount > limit:
                raise PlatformError(ErrorCode.QUOTA_EXCEEDED, "Quota exceeded.")
            conn.execute(
                """INSERT INTO quota_usage(tenant_id, owner_id, quota_name, window_key, amount)
                   VALUES (?, ?, ?, ?, ?)
                   ON CONFLICT(tenant_id, owner_id, quota_name, window_key)
                   DO UPDATE SET amount=excluded.amount""",
                (principal.tenant_id, principal.user_id, quota_name, window_key, used + amount),
            )
            return used + amount

    def backup_to(self, destination: str | Path, *, register: bool = True) -> Path:
        target_path = Path(destination).resolve()
        target_path.parent.mkdir(parents=True, exist_ok=True)
        with self._lock:
            target = sqlite3.connect(str(target_path))
            try:
                self._conn.backup(target)
            finally:
                target.close()
        try:
            os.chmod(target_path, 0o600)
        except OSError:
            pass
        if register:
            with self.transaction() as conn:
                conn.execute(
                    "INSERT OR IGNORE INTO backup_catalog(backup_id, path, created_at) VALUES (?, ?, ?)",
                    (_public_id("backup"), str(target_path), self.now()),
                )
        return target_path

    def restore_from(self, source: str | Path) -> None:
        source_path = Path(source).resolve()
        if not source_path.is_file():
            raise PlatformError(ErrorCode.NOT_FOUND, "Backup not found.")
        with self._lock:
            origin = sqlite3.connect(str(source_path))
            try:
                origin.backup(self._conn)
            finally:
                origin.close()
            self._conn.execute("PRAGMA foreign_keys=ON")
        if self.schema_version() != SCHEMA_VERSION:
            raise PlatformError(ErrorCode.CONFLICT, "Restored backup schema is unsupported.")

    def registered_backups(self) -> tuple[Path, ...]:
        with self._lock:
            rows = self._conn.execute("SELECT path FROM backup_catalog ORDER BY created_at, backup_id").fetchall()
            return tuple(Path(row["path"]).resolve() for row in rows)

    @classmethod
    def purge_subject_from_backup(cls, backup_path: str | Path, tenant_id: str, owner_id: str) -> None:
        path = Path(backup_path).resolve()
        if not path.is_file():
            raise PlatformError(ErrorCode.NOT_FOUND, "Registered backup is missing.")
        conn = sqlite3.connect(str(path), isolation_level=None)
        try:
            conn.execute("PRAGMA foreign_keys=ON")
            conn.execute("BEGIN IMMEDIATE")
            cls._purge_subject_conn(conn, tenant_id, owner_id)
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()
