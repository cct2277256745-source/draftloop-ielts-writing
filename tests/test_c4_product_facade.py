from __future__ import annotations

import unittest

from app.product_composition import (
    DraftLoopApplicationService,
    ExportArtifact,
    ExportState,
    PresentationState,
)
from app.product_platform.contracts import (
    ErrorCode,
    PlatformError,
    ProcessingOutcome,
    SubmissionState,
    SubmitCommand,
)
from tests.c3_support import C3Fixture


class DraftLoopApplicationServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.fx = C3Fixture()
        self.facade = DraftLoopApplicationService(self.fx.service)

    def tearDown(self) -> None:
        self.fx.close()

    def _complete_submission(self, idempotency_key: str = "c4-complete-contract-001") -> tuple[str, dict]:
        created = self.facade.submit(
            self.fx.learner,
            SubmitCommand(
                "task2",
                "Discuss a synthetic topic.",
                "This is isolated synthetic content.",
                idempotency_key,
            ),
        )
        submission_id = str(created["submissionId"])
        report_payload = {
            "version": "mode-a-report-document-v1",
            "taskType": "task2",
            "status": "COMPLETE",
            "publishable": True,
            "scoreProjection": {"likelyRange": [6.5, 7.0], "confidence": "MEDIUM"},
            "sections": [],
            "sourceHashes": {"lockedScoreSha256": "locked-fixture"},
            "reviewReasons": [],
            "consumerMayRescore": False,
        }
        self.fx.service.run_one_job(
            "c4-worker",
            lambda _value: ProcessingOutcome(SubmissionState.COMPLETE, report_payload),
        )
        return submission_id, report_payload

    def test_complete_semantic_result_produces_independent_normal_projection_but_not_a_fabricated_export(self) -> None:
        submission_id, report_payload = self._complete_submission()

        semantic = self.facade.semantic_result(self.fx.learner, submission_id)
        presentation = self.facade.presentation_projection(self.fx.learner, submission_id)
        export = self.facade.export_artifact(self.fx.learner, submission_id)

        self.assertIs(semantic.state, SubmissionState.COMPLETE)
        self.assertIs(presentation.state, PresentationState.NORMAL)
        self.assertEqual(presentation.content()["payload"], report_payload)
        self.assertEqual(presentation.semantic_result_sha256, semantic.semantic_result_sha256)
        self.assertIs(export.state, ExportState.BLOCKED)
        self.assertEqual(export.semantic_result_sha256, semantic.semantic_result_sha256)
        self.assertEqual(export.presentation_sha256, presentation.presentation_sha256)
        self.assertEqual(export.source_report_artifact_id, presentation.report_artifact_id)
        self.assertEqual(export.source_report_content_sha256, presentation.report_content_sha256)
        self.assertIsNone(export.artifact_id)
        self.assertIsNone(export.content_sha256)
        self.assertIsNone(export.media_type)
        self.assertEqual(export.failure_code, "EXPORT_ARTIFACT_NOT_MATERIALIZED")
        self.assertEqual(
            len({semantic.semantic_result_sha256, presentation.presentation_sha256, export.export_sha256}),
            3,
        )

    def test_ready_export_contract_requires_a_separately_addressed_materialized_artifact(self) -> None:
        submission_id, _report_payload = self._complete_submission("c4-materialized-export-001")
        semantic = self.facade.semantic_result(self.fx.learner, submission_id)
        presentation = self.facade.presentation_projection(self.fx.learner, submission_id)

        with self.assertRaises(ValueError):
            ExportArtifact(
                submission_id=submission_id,
                state=ExportState.READY,
                semantic_state=SubmissionState.COMPLETE,
                semantic_result_sha256=semantic.semantic_result_sha256,
                presentation_sha256=presentation.presentation_sha256,
                source_report_artifact_id=presentation.report_artifact_id,
                source_report_content_sha256=presentation.report_content_sha256,
                media_type="application/pdf",
            )

        with self.assertRaises(ValueError):
            ExportArtifact(
                submission_id=submission_id,
                state=ExportState.READY,
                semantic_state=SubmissionState.COMPLETE,
                semantic_result_sha256=semantic.semantic_result_sha256,
                presentation_sha256=presentation.presentation_sha256,
                source_report_artifact_id=presentation.report_artifact_id,
                source_report_content_sha256=presentation.report_content_sha256,
                artifact_id=presentation.report_artifact_id,
                content_sha256="a" * 64,
                media_type="application/pdf",
            )

        materialized = ExportArtifact(
            submission_id=submission_id,
            state=ExportState.READY,
            semantic_state=SubmissionState.COMPLETE,
            semantic_result_sha256=semantic.semantic_result_sha256,
            presentation_sha256=presentation.presentation_sha256,
            source_report_artifact_id=presentation.report_artifact_id,
            source_report_content_sha256=presentation.report_content_sha256,
            artifact_id="export:synthetic-pdf-001",
            content_sha256="a" * 64,
            media_type="application/pdf",
        )
        self.assertIs(materialized.state, ExportState.READY)
        self.assertEqual(materialized.content()["artifactId"], "export:synthetic-pdf-001")
        self.assertEqual(materialized.content()["contentSha256"], "a" * 64)

    def test_non_complete_semantic_results_have_no_normal_projection_or_export(self) -> None:
        expected_presentation_states = {
            SubmissionState.PARTIAL: PresentationState.PARTIAL,
            SubmissionState.REVIEW_REQUIRED: PresentationState.REVIEW_REQUIRED,
            SubmissionState.FAILED: PresentationState.FAILED,
        }
        for index, semantic_state in enumerate(expected_presentation_states, start=1):
            with self.subTest(state=semantic_state.value):
                created = self.facade.submit(
                    self.fx.learner,
                    SubmitCommand(
                        "task2",
                        "Discuss a synthetic topic.",
                        "This is isolated synthetic content.",
                        f"c4-blocked-contract-{index:03d}",
                    ),
                )
                submission_id = str(created["submissionId"])
                self.fx.service.run_one_job(
                    f"c4-worker-{index}",
                    lambda _value, state=semantic_state: ProcessingOutcome(
                        state,
                        failure_code=f"FIXTURE_{state.value}",
                    ),
                )

                presentation = self.facade.presentation_projection(self.fx.learner, submission_id)
                export = self.facade.export_artifact(self.fx.learner, submission_id)

                self.assertIs(presentation.state, expected_presentation_states[semantic_state])
                self.assertIsNone(presentation.payload)
                self.assertIsNone(presentation.report_artifact_id)
                self.assertIsNone(presentation.report_content_sha256)
                self.assertIs(export.state, ExportState.BLOCKED)
                self.assertIsNone(export.source_report_artifact_id)
                self.assertIsNone(export.source_report_content_sha256)
                self.assertIsNone(export.artifact_id)
                self.assertIsNone(export.content_sha256)
                self.assertIsNone(export.media_type)

    def test_missing_principal_and_cross_tenant_identifier_fail_closed(self) -> None:
        command = SubmitCommand(
            "task2",
            "Discuss a synthetic topic.",
            "This is isolated synthetic content.",
            "c4-tenant-contract-001",
        )
        created = self.facade.submit(self.fx.learner, command)
        submission_id = str(created["submissionId"])

        for operation in (
            lambda: self.facade.submit(None, command),  # type: ignore[arg-type]
            lambda: self.facade.semantic_result(None, submission_id),  # type: ignore[arg-type]
            lambda: self.facade.presentation_projection(None, submission_id),  # type: ignore[arg-type]
            lambda: self.facade.export_artifact(None, submission_id),  # type: ignore[arg-type]
        ):
            with self.assertRaises(PlatformError) as missing:
                operation()
            self.assertIs(missing.exception.code, ErrorCode.UNAUTHENTICATED)

        other = self.fx.identity.create_tenant_admin(
            "Other Tenant",
            "c4-other@example.test",
            "another correct horse password",
        )
        with self.assertRaises(PlatformError) as cross_tenant:
            self.facade.presentation_projection(other, submission_id)
        self.assertIs(cross_tenant.exception.code, ErrorCode.NOT_FOUND)

    def test_mismatched_report_state_or_hash_cannot_become_normal(self) -> None:
        created = self.facade.submit(
            self.fx.learner,
            SubmitCommand(
                "task2",
                "Discuss a synthetic topic.",
                "This is isolated synthetic content.",
                "c4-lineage-contract-001",
            ),
        )
        submission_id = str(created["submissionId"])
        self.fx.service.run_one_job(
            "c4-lineage-worker",
            lambda _value: ProcessingOutcome(
                SubmissionState.COMPLETE,
                {"version": "mode-a-report-document-v1", "status": "REVIEW_REQUIRED"},
            ),
        )

        mismatched_state = self.facade.presentation_projection(self.fx.learner, submission_id)
        self.assertIs(mismatched_state.state, PresentationState.FAILED)
        self.assertEqual(mismatched_state.failure_code, "PRESENTATION_LINEAGE_MISMATCH")
        self.assertIsNone(mismatched_state.payload)
        self.assertIs(
            self.facade.export_artifact(self.fx.learner, submission_id).state,
            ExportState.BLOCKED,
        )

        class CorruptReportHash:
            def __init__(self, platform):
                self._platform = platform

            def status(self, principal, requested_submission_id):
                return self._platform.status(principal, requested_submission_id)

            def report(self, principal, requested_submission_id):
                report = dict(self._platform.report(principal, requested_submission_id))
                report["contentSha256"] = "0" * 64
                return report

        corrupt_facade = DraftLoopApplicationService(CorruptReportHash(self.fx.service))  # type: ignore[arg-type]
        mismatched_hash = corrupt_facade.presentation_projection(self.fx.learner, submission_id)
        self.assertIs(mismatched_hash.state, PresentationState.FAILED)
        self.assertEqual(mismatched_hash.failure_code, "PRESENTATION_LINEAGE_MISMATCH")
        self.assertIs(corrupt_facade.export_artifact(self.fx.learner, submission_id).state, ExportState.BLOCKED)

    def test_malformed_complete_status_snapshots_are_rejected(self) -> None:
        submission_id, _report_payload = self._complete_submission("c4-malformed-status-001")

        class MutatedStatusPlatform:
            def __init__(self, platform, mutate):
                self._platform = platform
                self._mutate = mutate

            def status(self, principal, requested_submission_id):
                status = dict(self._platform.status(principal, requested_submission_id))
                status["job"] = dict(status["job"])
                self._mutate(status)
                return status

            def report(self, principal, requested_submission_id):
                return self._platform.report(principal, requested_submission_id)

        mutations = {
            "missing state": lambda status: status.pop("state"),
            "invalid state": lambda status: status.__setitem__("state", "NOT_A_STATE"),
            "missing complete hash": lambda status: status.__setitem__("resultSha256", None),
            "invalid complete hash": lambda status: status.__setitem__("resultSha256", "not-a-sha256"),
            "empty submission id": lambda status: status.__setitem__("submissionId", ""),
            "non-integer version": lambda status: status.__setitem__("submissionVersion", "1"),
            "inconsistent job state": lambda status: status["job"].__setitem__("state", "LEASED"),
            "inconsistent progress": lambda status: status["job"].__setitem__("progress", 90),
        }
        for label, mutation in mutations.items():
            with self.subTest(case=label):
                facade = DraftLoopApplicationService(
                    MutatedStatusPlatform(self.fx.service, mutation)  # type: ignore[arg-type]
                )
                with self.assertRaises(PlatformError) as invalid:
                    facade.semantic_result(self.fx.learner, submission_id)
                self.assertIs(invalid.exception.code, ErrorCode.CONFLICT)

    def test_malformed_complete_report_fields_and_payload_status_never_become_normal(self) -> None:
        submission_id, _report_payload = self._complete_submission("c4-malformed-report-001")

        class MutatedReportPlatform:
            def __init__(self, platform, mutate):
                self._platform = platform
                self._mutate = mutate

            def status(self, principal, requested_submission_id):
                return self._platform.status(principal, requested_submission_id)

            def report(self, principal, requested_submission_id):
                report = dict(self._platform.report(principal, requested_submission_id))
                report["payload"] = dict(report["payload"])
                self._mutate(report)
                return report

        mutations = {
            "missing payload status": lambda report: report["payload"].pop("status"),
            "invalid payload status": lambda report: report["payload"].__setitem__("status", "NOT_A_STATE"),
            "missing content hash": lambda report: report.pop("contentSha256"),
            "invalid content hash": lambda report: report.__setitem__("contentSha256", "not-a-sha256"),
            "empty artifact id": lambda report: report.__setitem__("artifactId", ""),
        }
        for label, mutation in mutations.items():
            with self.subTest(case=label):
                facade = DraftLoopApplicationService(
                    MutatedReportPlatform(self.fx.service, mutation)  # type: ignore[arg-type]
                )
                projection = facade.presentation_projection(self.fx.learner, submission_id)
                self.assertIs(projection.state, PresentationState.FAILED)
                self.assertIsNone(projection.payload)
                self.assertIn(
                    projection.failure_code,
                    {"PRESENTATION_CONTRACT_INVALID", "PRESENTATION_LINEAGE_MISMATCH"},
                )
                self.assertIs(
                    facade.export_artifact(self.fx.learner, submission_id).state,
                    ExportState.BLOCKED,
                )

    def test_status_change_while_reading_report_fails_closed_as_an_inconsistent_snapshot(self) -> None:
        submission_id, _report_payload = self._complete_submission("c4-snapshot-change-001")

        class ChangingSnapshotPlatform:
            def __init__(self, platform):
                self._platform = platform
                self._status_reads = 0

            def status(self, principal, requested_submission_id):
                self._status_reads += 1
                status = dict(self._platform.status(principal, requested_submission_id))
                if self._status_reads % 2 == 0:
                    status["submissionVersion"] = int(status["submissionVersion"]) + 1
                return status

            def report(self, principal, requested_submission_id):
                return self._platform.report(principal, requested_submission_id)

        facade = DraftLoopApplicationService(ChangingSnapshotPlatform(self.fx.service))  # type: ignore[arg-type]
        projection = facade.presentation_projection(self.fx.learner, submission_id)

        self.assertIs(projection.state, PresentationState.FAILED)
        self.assertEqual(projection.failure_code, "PRESENTATION_SNAPSHOT_CHANGED")
        self.assertIsNone(projection.payload)
        self.assertIs(facade.export_artifact(self.fx.learner, submission_id).state, ExportState.BLOCKED)


if __name__ == "__main__":
    unittest.main()
