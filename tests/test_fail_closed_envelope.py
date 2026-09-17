from __future__ import annotations

import json
import copy
import hashlib
import os
import unittest
from dataclasses import FrozenInstanceError
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QFileDialog, QLabel

from app.core.llm import grade
from app.core.models import GradingRequest, GradingResult
from app.core.settings import AppSettings
from app.core.providers import ProviderCallResult, ProviderFailureCode
from app.ui.feedback_panel import FeedbackPanel
from app.ui.main_window import MainWindow
from app.worker import GradingWorker
from app.core.workflow import (
    StageFailure,
    WorkflowFailureState,
    WorkflowResult,
    WorkflowStage,
    WorkflowStatus,
)
from tests.rubric_test_support import task2_snapshot
from tests.task2_understanding_support import fixture_understanding_service
from tests.student_evidence_support import fixture_student_evidence_service


_grade_runtime = grade


def grade(request, *args, **kwargs):
    if request.taskType == "task2" and "rubric_snapshot" not in kwargs:
        kwargs["rubric_snapshot"] = task2_snapshot()
    if request.taskType == "task2" and "understanding_service" not in kwargs:
        kwargs["understanding_service"] = fixture_understanding_service()
    if request.taskType == "task2" and "student_evidence_service" not in kwargs:
        kwargs["student_evidence_service"] = fixture_student_evidence_service()
    return _grade_runtime(request, *args, **kwargs)


def _settings() -> AppSettings:
    return AppSettings(
        task1_qwen_api_key="primary-key",
        task1_deepseek_api_key="syntax-key",
        task2_qwen_api_key="primary-key",
        task2_deepseek_api_key="syntax-key",
    )


def _request(case: str = "base") -> GradingRequest:
    return GradingRequest(
        taskType="task2",
        question="Discuss both views.",
        essay=(
            "A private candidate script that must never become a fallback report. "
            + case
        ),
    )


def _main_response() -> str:
    return json.dumps({
        "taskType": "task2",
        "overallBand": 7.0,
        "summary": "The response is clear but needs fuller support.",
        "scores": [
            {"label": "TR", "score": 7.0, "rationale": "Relevant."},
            {"label": "CC", "score": 7.0, "rationale": "Logical."},
            {"label": "LR", "score": 6.5, "rationale": "Some repetition."},
            {"label": "GRA", "score": 7.0, "rationale": "Generally accurate."},
        ],
        "qwenExamReadyVersion": "Validated primary draft.",
    })


def _syntax_response() -> str:
    return json.dumps({
        "deepseekSyntaxEnhancedVersion": "Validated syntax draft.",
        "syntaxUpgrades": [],
    })


def _final_response() -> str:
    return json.dumps({
        "taskType": "task2",
        "overallBand": 9.0,
        "scores": [
            {"label": label, "score": 9.0, "rationale": "Must not replace main."}
            for label in ("TR", "CC", "LR", "GRA")
        ],
        "scoreDiagnosis": {
            "overall": 9.0,
            "targetBand": 7.5,
            "gapToTarget": 0.0,
            "criteria": [
                {"name": label, "score": 9.0}
                for label in ("TR", "CC", "LR", "GRA")
            ],
        },
        "paragraphFeedback": [],
        "vocabularyUpgrades": [],
        "syntaxUpgrades": [],
        "balancedFinalVersion": "Validated final draft.",
        "memoriseWorthyExpressions": [],
        "nextPracticeSuggestions": [],
    })


class WorkflowEnvelopeContractTests(unittest.TestCase):
    def test_only_complete_may_carry_a_final_report(self) -> None:
        report = GradingResult(taskType="task2", balancedFinalVersion="Final.")

        complete = WorkflowResult.complete(report)
        self.assertIs(complete.payload, report)
        self.assertIs(complete.status, WorkflowStatus.COMPLETE)
        self.assertTrue(complete.completeness)

        for status in (
            WorkflowStatus.PARTIAL,
            WorkflowStatus.REVIEW_REQUIRED,
            WorkflowStatus.FAILED,
        ):
            with self.subTest(status=status):
                with self.assertRaises(ValueError):
                    WorkflowResult(
                        status=status,
                        completeness=False,
                        payload=report,
                    )

    def test_failure_evidence_is_immutable_and_report_free(self) -> None:
        failure = StageFailure(
            stage=WorkflowStage.MAIN_REVIEW,
            state=WorkflowFailureState.SCHEMA_FAILURE,
            message="主批改返回结构不完整。",
        )
        result = WorkflowResult.failed(failure)

        self.assertIs(result.status, WorkflowStatus.FAILED)
        self.assertFalse(result.completeness)
        self.assertIsNone(result.payload)
        self.assertEqual(result.stage_failures, (failure,))
        with self.assertRaises(FrozenInstanceError):
            failure.message = "changed"  # type: ignore[misc]

        mutable_source = [failure]
        copied = WorkflowResult(
            status=WorkflowStatus.FAILED,
            completeness=False,
            stage_failures=mutable_source,  # type: ignore[arg-type]
        )
        mutable_source.clear()
        self.assertEqual(copied.stage_failures, (failure,))

    def test_review_required_carries_only_typed_reasons(self) -> None:
        result = WorkflowResult.review_required(
            WorkflowFailureState.EVIDENCE_CONFLICT,
            WorkflowFailureState.TASK1_FACT_CONFLICT,
        )

        self.assertIs(result.status, WorkflowStatus.REVIEW_REQUIRED)
        self.assertIsNone(result.payload)
        self.assertEqual(
            result.review_reasons,
            (
                WorkflowFailureState.EVIDENCE_CONFLICT,
                WorkflowFailureState.TASK1_FACT_CONFLICT,
            ),
        )

    def test_all_review_states_remain_report_free(self) -> None:
        for reason in (
            WorkflowFailureState.LOW_CONFIDENCE,
            WorkflowFailureState.DISAGREEMENT,
            WorkflowFailureState.EVIDENCE_CONFLICT,
            WorkflowFailureState.TASK1_FACT_CONFLICT,
            WorkflowFailureState.REVIEW_REQUIRED,
        ):
            with self.subTest(reason=reason):
                result = WorkflowResult.review_required(reason)
                self.assertIs(result.status, WorkflowStatus.REVIEW_REQUIRED)
                self.assertIsNone(result.payload)


class FailClosedPipelineTests(unittest.TestCase):
    def test_declared_dictionary_score_shape_remains_supported(self) -> None:
        main = json.loads(_main_response())
        main.pop("overallBand")
        main["scores"] = {
            "overall": 7.0,
            "tr_ta": 7.0,
            "cc": 7.0,
            "lr": 6.5,
            "gra": 7.0,
        }
        with patch(
            "app.core.llm._call_openai_compatible_chat",
            side_effect=[json.dumps(main), _syntax_response(), _final_response()],
        ):
            result = grade(_request("dict-scores"), settings=_settings())

        self.assertIs(result.status, WorkflowStatus.COMPLETE)
        assert result.payload is not None
        self.assertEqual(result.payload.overallBand, 7.0)

    def test_only_all_valid_stages_construct_a_complete_report(self) -> None:
        with patch(
            "app.core.llm._call_openai_compatible_chat",
            side_effect=[_main_response(), _syntax_response(), _final_response()],
        ):
            result = grade(_request(), settings=_settings())

        self.assertIs(result.status, WorkflowStatus.COMPLETE)
        self.assertTrue(result.completeness)
        self.assertIsInstance(result.payload, GradingResult)
        assert result.payload is not None
        self.assertEqual(result.payload.balancedFinalVersion, "Validated final draft.")
        self.assertEqual(result.payload.overallBand, 7.0)
        self.assertEqual(
            [score.score for score in result.payload.scores],
            [7.0, 7.0, 6.5, 7.0],
        )
        self.assertEqual(
            [item.score for item in result.payload.scoreDiagnosis.criteria],
            [7.0, 7.0, 6.5, 7.0],
        )

    def test_malformed_json_and_wrong_criteria_fail_closed(self) -> None:
        wrong = json.loads(_main_response())
        wrong["scores"][0]["label"] = "TA"
        cases = (
            ("malformed", "not-json"),
            ("wrong-criteria", json.dumps(wrong)),
            (
                "wrong-count",
                json.dumps({
                    **json.loads(_main_response()),
                    "scores": json.loads(_main_response())["scores"][:3],
                }),
            ),
        )
        for name, response in cases:
            with self.subTest(case=name), patch(
                "app.core.llm._call_openai_compatible_chat",
                side_effect=[response, response],
            ):
                result = grade(_request(name), settings=_settings())
            self.assertIs(result.status, WorkflowStatus.FAILED)
            self.assertIsNone(result.payload)
            self.assertEqual(
                result.stage_failures[0].state,
                WorkflowFailureState.SCHEMA_FAILURE,
            )

    def test_optional_syntax_schema_failure_is_partial_without_report(self) -> None:
        responses = [_main_response(), "{}", "{}", _final_response()]
        captured: list[list[dict[str, object]]] = []

        def call(_model, _key, messages, _base, _display, **_kwargs):
            captured.append(copy.deepcopy(messages))
            return responses.pop(0)

        with patch("app.core.llm._call_openai_compatible_chat", side_effect=call):
            result = grade(_request("syntax-partial"), settings=_settings())

        self.assertIs(result.status, WorkflowStatus.PARTIAL)
        self.assertIsNone(result.payload)
        self.assertEqual(
            result.stage_failures[0].stage,
            WorkflowStage.SYNTAX_ENHANCEMENT,
        )
        final_payload = json.loads(captured[-1][1]["content"])
        self.assertEqual(final_payload["deepseekSyntaxEnhancedVersion"], "")
        self.assertEqual(final_payload["syntaxUpgradesDraft"], [])

    def test_final_schema_failure_is_partial_without_primary_draft_report(self) -> None:
        with patch(
            "app.core.llm._call_openai_compatible_chat",
            side_effect=[_main_response(), _syntax_response(), "{}", "{}"],
        ):
            result = grade(_request("final-partial"), settings=_settings())

        self.assertIs(result.status, WorkflowStatus.PARTIAL)
        self.assertIsNone(result.payload)
        self.assertEqual(
            result.stage_failures[-1].stage,
            WorkflowStage.FINAL_VALIDATION,
        )

    def test_wrong_final_criterion_set_is_partial_without_report(self) -> None:
        invalid = json.loads(_final_response())
        invalid["scoreDiagnosis"]["criteria"][0]["name"] = "TA"
        encoded = json.dumps(invalid)
        with patch(
            "app.core.llm._call_openai_compatible_chat",
            side_effect=[_main_response(), _syntax_response(), encoded, encoded],
        ):
            result = grade(_request("final-criteria"), settings=_settings())

        self.assertIs(result.status, WorkflowStatus.PARTIAL)
        self.assertIsNone(result.payload)

    def test_provider_timeout_is_typed_at_required_stage(self) -> None:
        timeout = ProviderCallResult.failed(
            ProviderFailureCode.TIMEOUT,
            "safe provider timeout",
            retryable=True,
        )
        with patch(
            "app.core.llm._call_through_compatibility_adapter",
            return_value=timeout,
        ):
            result = grade(_request("timeout"), settings=_settings())

        self.assertIs(result.status, WorkflowStatus.FAILED)
        self.assertIsNone(result.payload)
        self.assertEqual(
            result.stage_failures[0].state,
            WorkflowFailureState.PROVIDER_FAILURE,
        )

    def test_final_provider_failure_is_partial_without_report(self) -> None:
        with patch(
            "app.core.llm._call_through_compatibility_adapter",
            side_effect=[
                ProviderCallResult(content=_main_response()),
                ProviderCallResult(content=_syntax_response()),
                ProviderCallResult.failed(
                    ProviderFailureCode.PROVIDER_FAILURE,
                    "safe final failure",
                ),
            ],
        ):
            result = grade(_request("final-provider"), settings=_settings())

        self.assertIs(result.status, WorkflowStatus.PARTIAL)
        self.assertIsNone(result.payload)
        self.assertEqual(
            result.stage_failures[-1].state,
            WorkflowFailureState.PROVIDER_FAILURE,
        )

    def test_unexpected_exception_is_redacted_failed_outcome(self) -> None:
        secret = "private essay and provider response must not leak"
        with patch(
            "app.core.llm._call_openai_compatible_chat",
            side_effect=RuntimeError(secret),
        ):
            result = grade(_request("unexpected"), settings=_settings())

        self.assertIs(result.status, WorkflowStatus.FAILED)
        self.assertIsNone(result.payload)
        self.assertEqual(
            result.stage_failures[0].state,
            WorkflowFailureState.UNEXPECTED_FAILURE,
        )
        self.assertNotIn(secret, repr(result))

    def test_schema_retry_preserves_route_prompt_and_semantic_input(self) -> None:
        responses = ["{}", _main_response(), _syntax_response(), _final_response()]
        calls: list[tuple[object, list[dict[str, object]], str]] = []

        def call(contract, messages, display):
            calls.append((contract, copy.deepcopy(messages), display))
            return ProviderCallResult(content=responses.pop(0))

        with patch(
            "app.core.llm._call_through_compatibility_adapter", side_effect=call
        ):
            result = grade(_request("retry-invariants"), settings=_settings())

        self.assertIs(result.status, WorkflowStatus.COMPLETE)
        first, retry = calls[0], calls[1]
        self.assertIs(first[0], retry[0])
        self.assertEqual(first[0].snapshot.route_key, "primary")
        self.assertEqual(first[0].provider_id, retry[0].provider_id)
        self.assertEqual(first[0].model.model_id, retry[0].model.model_id)
        self.assertEqual(first[0].snapshot.base_url, retry[0].snapshot.base_url)
        self.assertEqual(first[2], retry[2])
        self.assertEqual(first[1][:2], retry[1][:2])
        first_prompt = first[1][0]["content"]
        retry_prompt = retry[1][0]["content"]
        self.assertEqual(
            hashlib.sha256(first_prompt.encode()).hexdigest(),
            hashlib.sha256(retry_prompt.encode()).hexdigest(),
        )
        self.assertEqual(len(first[1]), 2)
        self.assertEqual(len(retry[1]), 3)

    def test_main_response_missing_scores_fails_without_report(self) -> None:
        invalid = json.dumps({
            "taskType": "task2",
            "overallBand": 7.0,
            "summary": "Looks plausible.",
            "qwenExamReadyVersion": "A rewritten draft.",
        })

        with patch(
            "app.core.llm._call_openai_compatible_chat",
            side_effect=[invalid, invalid],
        ) as adapter:
            result = grade(_request("missing-scores"), settings=_settings())

        self.assertIs(result.status, WorkflowStatus.FAILED)
        self.assertIsNone(result.payload)
        self.assertEqual(
            result.stage_failures[0].state,
            WorkflowFailureState.SCHEMA_FAILURE,
        )
        self.assertEqual(adapter.call_count, 2)
        rendered = repr(result)
        self.assertNotIn(_request("missing-scores").essay, rendered)
        self.assertNotIn("Looks plausible", rendered)


class WorkflowDesktopBoundaryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._app = QApplication.instance() or QApplication([])

    def test_non_complete_outcomes_render_state_only_and_block_export(self) -> None:
        window = MainWindow()
        failure = StageFailure(
            WorkflowStage.FINAL_VALIDATION,
            WorkflowFailureState.SCHEMA_FAILURE,
            "阶段返回结构无效。",
        )
        outcome = WorkflowResult.partial(failure)

        window.feedback_panel.set_workflow_result(
            outcome,
            question="Private question must not be rendered.",
            essay="Private candidate script must not be rendered.",
        )

        self.assertFalse(window.feedback_panel.export_btn.isEnabled())
        self.assertIsNone(window.feedback_panel.result)
        rendered = " ".join(
            label.text() for label in window.feedback_panel.findChildren(QLabel)
        )
        self.assertIn("PARTIAL", rendered)
        self.assertNotIn("Private candidate script", rendered)
        with patch.object(QFileDialog, "getSaveFileName") as chooser:
            window._on_export()
        chooser.assert_not_called()
        window.close()

    def test_failed_and_review_required_render_no_report_sections(self) -> None:
        panel = FeedbackPanel()
        outcomes = (
            WorkflowResult.failed(StageFailure(
                WorkflowStage.MAIN_REVIEW,
                WorkflowFailureState.PROVIDER_FAILURE,
                "阶段调用失败。",
            )),
            WorkflowResult.review_required(WorkflowFailureState.LOW_CONFIDENCE),
        )
        for outcome in outcomes:
            with self.subTest(status=outcome.status):
                panel.set_workflow_result(
                    outcome,
                    essay="Private candidate script must stay hidden.",
                )
                self.assertIsNone(panel.result)
                self.assertFalse(panel.export_btn.isEnabled())
                rendered = " ".join(
                    label.text() for label in panel.findChildren(QLabel)
                )
                self.assertIn(outcome.status.value, rendered)
                self.assertNotIn("Private candidate script", rendered)
        panel.close()

    def test_complete_outcome_is_the_only_exportable_report(self) -> None:
        panel = FeedbackPanel()
        report = GradingResult(
            taskType="task2",
            overallBand=7.0,
            balancedFinalVersion="Validated final report.",
        )
        panel.set_workflow_result(WorkflowResult.complete(report))

        self.assertTrue(panel.export_btn.isEnabled())
        self.assertIs(panel.result, report)
        panel.close()

    def test_worker_redacts_unexpected_exception_into_failed_envelope(self) -> None:
        emitted: list[WorkflowResult[GradingResult]] = []
        worker = GradingWorker(_request("worker"), _settings())
        worker.finished.connect(emitted.append)
        secret = "raw provider body and candidate script"

        with patch("app.worker.grade", side_effect=RuntimeError(secret)):
            worker.run()

        self.assertEqual(len(emitted), 1)
        self.assertIs(emitted[0].status, WorkflowStatus.FAILED)
        self.assertIsNone(emitted[0].payload)
        self.assertNotIn(secret, repr(emitted[0]))


if __name__ == "__main__":
    unittest.main()
