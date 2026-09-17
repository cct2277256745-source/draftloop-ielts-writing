"""Consent, subject export, retention, and complete deletion workflows."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Mapping

from .contracts import ErrorCode, PlatformError, Principal, digest
from .persistence import PlatformStore
from .uploads import UploadStorage


PRIVACY_POLICY_VERSION = "c3-privacy-policy-v1"
EXPORT_SCHEMA_VERSION = "subject-export-envelope-v1"


@dataclass(frozen=True)
class RetentionRule:
    data_class: str
    duration_days: int | None
    disposition: str
    reason: str


DEFAULT_RETENTION_MATRIX = (
    RetentionRule("active-submission", 365, "DELETE_OR_ANONYMISE", "Learner history window"),
    RetentionRule("upload-bytes", 30, "DELETE_AFTER_DERIVATION", "Minimise source media"),
    RetentionRule("privacy-receipt", None, "RETAIN_MINIMISED", "Rights accountability"),
    RetentionRule("ordinary-log", 30, "DELETE", "Operational troubleshooting only"),
    RetentionRule("backup", 30, "EXPIRE_AND_PURGE_SUBJECT", "Bound recovery exposure"),
)


class PrivacyService:
    def __init__(
        self,
        store: PlatformStore,
        *,
        uploads: UploadStorage | None = None,
        retention_matrix: tuple[RetentionRule, ...] = DEFAULT_RETENTION_MATRIX,
    ) -> None:
        self.store = store
        self.uploads = uploads
        self.retention_matrix = retention_matrix

    def export_subject(self, principal: Principal) -> Mapping[str, Any]:
        data = self.store.export_subject(principal)
        envelope = {
            "schemaVersion": EXPORT_SCHEMA_VERSION,
            "privacyPolicyVersion": PRIVACY_POLICY_VERSION,
            "subjectSha256": digest({
                "tenantId": principal.tenant_id,
                "ownerId": principal.user_id,
            }),
            "data": data,
        }
        envelope_sha = digest(envelope)
        receipt = self.store.audit_receipt(
            tenant_id=principal.tenant_id,
            owner_id=principal.user_id,
            action="SUBJECT_EXPORT_CREATED",
            detail={
                "schemaVersion": EXPORT_SCHEMA_VERSION,
                "exportSha256": envelope_sha,
            },
        )
        return {**envelope, "exportSha256": envelope_sha, "receipt": receipt}

    def request_deletion(self, principal: Principal) -> Mapping[str, Any]:
        request = self.store.request_deletion(principal)
        receipt = self.store.audit_receipt(
            tenant_id=principal.tenant_id,
            owner_id=principal.user_id,
            action="SUBJECT_DELETION_REQUESTED",
            detail={"deletionId": request["deletion_id"]},
        )
        return {
            "deletionId": request["deletion_id"],
            "state": request["state"],
            "requestedAt": request["requested_at"],
            "receipt": receipt,
        }

    def execute_deletion(self, deletion_id: str) -> Mapping[str, Any]:
        request = self.store.deletion_request(deletion_id)
        if request["state"] == "COMPLETE":
            return {
                "deletionId": deletion_id,
                "state": "COMPLETE",
                "subjectSha256": request["subject_sha256"],
                "completedAt": request["completed_at"],
            }
        owner_id = request["owner_id"]
        if not owner_id:
            raise PlatformError(ErrorCode.CONFLICT, "Deletion is in an incomplete terminal stage.")
        tenant_id = str(request["tenant_id"])
        owner_id = str(owner_id)
        backups = self.store.registered_backups()
        missing = [path for path in backups if not path.is_file()]
        if missing:
            raise PlatformError(ErrorCode.CONFLICT, "A registered backup is unavailable for deletion.")

        # Purge every registered recovery copy before making the primary subject
        # unreachable.  Retrying remains safe because deletes are idempotent.
        for backup in backups:
            PlatformStore.purge_subject_from_backup(backup, tenant_id, owner_id)
        removed_files = 0
        if self.uploads is not None:
            removed_files = self.uploads.delete_subject_files(tenant_id, owner_id)
        primary = self.store.execute_primary_deletion(deletion_id)
        receipt = self.store.audit_receipt(
            tenant_id=tenant_id,
            owner_id=None,
            action="SUBJECT_DELETION_COMPLETED",
            detail={
                "deletionId": deletion_id,
                "subjectSha256": primary["subject_sha256"],
                "registeredBackupsPurged": len(backups),
                "uploadFilesRemoved": removed_files,
            },
        )
        complete = self.store.complete_deletion(deletion_id)
        return {
            "deletionId": deletion_id,
            "state": complete["state"],
            "subjectSha256": complete["subject_sha256"],
            "completedAt": complete["completed_at"],
            "receipt": receipt,
        }

    def retention_contract(self) -> Mapping[str, Any]:
        rules = [
            {
                "dataClass": rule.data_class,
                "durationDays": rule.duration_days,
                "disposition": rule.disposition,
                "reason": rule.reason,
            }
            for rule in self.retention_matrix
        ]
        return {
            "policyVersion": PRIVACY_POLICY_VERSION,
            "rules": rules,
            "matrixSha256": digest(rules),
        }

    def enforce_submission_retention(self, cutoff: datetime, *, execute: bool = False) -> Mapping[str, Any]:
        if cutoff.tzinfo is None:
            raise PlatformError(ErrorCode.INVALID_REQUEST, "Retention cutoff requires a timezone.")
        cutoff_iso = cutoff.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
        candidates = self.store.retention_candidates(cutoff_iso)
        if not execute:
            return {
                "policyVersion": PRIVACY_POLICY_VERSION,
                "mode": "DRY_RUN",
                "cutoff": cutoff_iso,
                "submissionIds": [row["submission_id"] for row in candidates],
            }
        backups = self.store.registered_backups()
        if any(not path.is_file() for path in backups):
            raise PlatformError(ErrorCode.CONFLICT, "A registered backup is unavailable for retention.")
        deleted = []
        for row in candidates:
            for backup in backups:
                PlatformStore.purge_submission_from_backup(backup, str(row["submission_id"]))
            result = self.store.execute_submission_retention(str(row["submission_id"]))
            if self.uploads is not None:
                self.uploads.delete_storage_key(result.get("storageKey"))
            receipt = self.store.audit_receipt(
                tenant_id=str(result["tenantId"]),
                owner_id=str(result["ownerId"]),
                action="RETENTION_SUBMISSION_DELETED",
                detail={"submissionId": result["submissionId"], "cutoff": cutoff_iso},
            )
            deleted.append({"submissionId": result["submissionId"], "receipt": receipt})
        return {
            "policyVersion": PRIVACY_POLICY_VERSION,
            "mode": "EXECUTED",
            "cutoff": cutoff_iso,
            "deleted": deleted,
        }
