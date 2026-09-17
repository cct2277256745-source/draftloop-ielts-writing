from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import requests
from PySide6.QtGui import QImage

from app.core.image_input import ImageInputError, ImageInputFailureCode
from app.core.llm import (
    LLMError,
    _call_openai_compatible_chat,
    _main_user_text,
    _prompt,
    grade,
)
from app.core.models import GradingRequest, GradingResult
from app.core.submission import SubmissionSnapshot
from app.core.task2_understanding import Task2UnderstandingService
from app.core.providers import resolve_route, route_for
from app.core.pdf_report import export_report
from app.core.settings import AppSettings
from app.core.workflow import WorkflowFailureState, WorkflowStatus
from tests.rubric_test_support import TEST_RUNTIME_HASH, task2_snapshot
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


def _complete_report(outcome):
    if outcome.status is not WorkflowStatus.COMPLETE or outcome.payload is None:
        raise AssertionError(f"Expected COMPLETE workflow, got {outcome!r}")
    return outcome.payload


def _diagnosis(task_type: str, overall: float, target: float = 7.5) -> dict:
    first = "TA" if task_type == "task1" else "TR"
    return {
        "overall": overall,
        "targetBand": target,
        "gapToTarget": max(0.0, round(target - overall, 1)),
        "criteria": [
            {"name": label, "score": overall}
            for label in (first, "CC", "LR", "GRA")
        ],
    }


def _main_json(task_type: str = "task1") -> str:
    first = "TA" if task_type == "task1" else "TR"
    return json.dumps({
        "taskType": task_type,
        "overallBand": 7.0,
        "summary": "Accurate overall, but comparisons need work.",
        "scores": [
            {"label": first, "score": 7, "rationale": ""},
            {"label": "CC", "score": 7, "rationale": ""},
            {"label": "LR", "score": 7, "rationale": ""},
            {"label": "GRA", "score": 7, "rationale": ""},
        ],
        "chartUnderstanding": {"chartType": "line graph", "mainTrends": ["rose"]},
        "dataAccuracyCheck": {"inaccurateDescriptions": []},
        "overviewCheck": {"hasOverview": True},
        "paragraphFeedback": [],
        "qwenExamReadyVersion": "Qwen draft with 20 units.",
        "balancedFinalVersion": "",
    })


def _syntax_json() -> str:
    return json.dumps({
        "deepseekSyntaxEnhancedVersion": "DeepSeek draft with 20 units.",
        "syntaxUpgrades": [{
            "originalSentence": "A rose.",
            "upgradedSentence": "While A rose, B fell.",
            "syntaxPattern": "while contrast",
            "whyItImprovesGRA": "Adds controlled complexity.",
            "examUsability": "high",
            "difficulty": "easy",
        }],
    })


def _final_json(task_type: str = "task1") -> str:
    obj = json.loads(_main_json(task_type))
    obj["deepseekSyntaxEnhancedVersion"] = "DeepSeek draft with 20 units."
    obj["balancedFinalVersion"] = "Final draft with 20 units."
    obj["deepseekChangesReview"] = [{"decision": "keep", "reason": "Accurate"}]
    obj["scoreDiagnosis"] = _diagnosis(task_type, 7.0)
    obj["vocabularyUpgrades"] = []
    obj["syntaxUpgrades"] = []
    obj["memoriseWorthyExpressions"] = []
    obj["nextPracticeSuggestions"] = []
    return json.dumps(obj)


def _settings() -> AppSettings:
    return AppSettings(
        task1_qwen_api_key="qwen-key",
        task1_deepseek_api_key="deepseek-key",
        task2_qwen_api_key="qwen-key",
        task2_deepseek_api_key="deepseek-key",
    )


def _target_main_json(target: float) -> str:
    return json.dumps({
        "taskType": "task2",
        "overallBand": 6.5,
        "summary": "The position is clear, but development and lexical precision limit the score.",
        "scores": [
            {"label": "TR", "score": 6.5, "rationale": "Ideas need fuller support."},
            {"label": "CC", "score": 6.5, "rationale": "Progression is generally clear."},
            {"label": "LR", "score": 6.0, "rationale": "Some wording is vague."},
            {"label": "GRA", "score": 6.5, "rationale": "Structures are mostly safe."},
        ],
        "qwenExamReadyVersion": f"Natural Qwen rewrite for Band {target:g}.",
        "vocabularyUpgrades": [{
            "originalExpression": "help the problem",
            "suggestedExpression": "institutionalised amelioration",
            "meaningZh": "机械学术化表达",
            "whyBetterEn": "This should be rejected by the validator.",
            "difficulty": "ambitious",
            "examUsability": "low",
            "naturalness": "stiff",
            "targetBandSuitability": "8.5",
            "recommendation": "avoid memorising",
        }],
    })


def _target_syntax_json(target: float) -> str:
    return json.dumps({
        "deepseekSyntaxEnhancedVersion": f"Controlled syntax version for Band {target:g}.",
        "syntaxUpgrades": [{
            "title": f"Upgrade {index}",
            "studentOriginalSentence": "This policy helps people.",
            "qwenExamReadySentence": "This policy can support households.",
            "deepseekSyntaxEnhancedSentence": "This policy can support households while limiting costs.",
            "syntaxPattern": "controlled contrast",
            "whyItImprovesGRAEn": "It adds controlled subordination.",
            "whyItImprovesGRAZh": "增加可控的从属结构。",
            "examUsability": "high",
            "difficulty": "easy" if target == 7.5 else "medium",
            "targetBandSuitability": f"{target:g}",
            "naturalness": "natural",
            "howToReuseZh": "用于对比两个结果。",
            "warningZh": "",
        } for index in range(1, 7)],
    })


def _target_final_json(target: float) -> str:
    obj = json.loads(_target_main_json(target))
    # 模拟终审错误地尝试按目标分数抬分，代码必须锁回主批改的 6.5。
    obj["overallBand"] = target
    obj["scores"] = [
        {"label": label, "score": target, "rationale": "Must be ignored."}
        for label in ("TR", "CC", "LR", "GRA")
    ]
    obj["deepseekSyntaxEnhancedVersion"] = f"Controlled syntax version for Band {target:g}."
    obj["balancedFinalVersion"] = f"Balanced, natural final version for Band {target:g}."
    obj["scoreDiagnosis"] = _diagnosis("task2", target, target)
    obj["paragraphFeedback"] = [
        {
            "paragraphNumber": index,
            "function": function,
            "strengthsEn": ["The paragraph has a clear role."],
            "issuesEn": ["Development can be more specific."],
            "howToImproveEn": ["Add one precise supporting detail."],
            "sentenceUpgrade": {"original": "Original.", "improved": "Improved."},
        }
        for index, function in ((1, "introduction"), (2, "body"), (3, "conclusion"))
    ]
    obj["syntaxUpgrades"] = json.loads(_target_syntax_json(target))["syntaxUpgrades"]
    obj["nextPracticeSuggestions"] = []
    obj["memoriseWorthyExpressions"] = [
        {
            "expression": "institutionalised amelioration",
            "meaningZh": "不应推荐",
            "reusability": "highly reusable",
            "targetBandSuitability": "8.5",
        },
        {
            "expression": f"a practical Band {target:g} expression",
            "meaningZh": "通用且自然的表达",
            "topic": "general",
            "exampleSentence": "This is a practical long-term response.",
            "whyUsefulZh": "适合迁移。",
            "reusability": "highly reusable",
            "targetBandSuitability": f"{target:g}",
            "warningZh": "",
        },
    ]
    obj["balancedVersionQualityCheck"] = {
        "natural": True,
        "targetBandAppropriate": True,
        "overAcademicStyle": False,
        "preservesOriginalArgument": True,
        "hasUsefulSentenceVariety": True,
        "briefNoteZh": "已删除机械学术化表达。",
    }
    obj["rubric_id"] = "forged-provider-rubric"
    obj["rubric_version"] = "9.9.9"
    obj["runtime_content_sha256"] = "f" * 64
    return json.dumps(obj)


def _image_count(messages) -> int:
    count = 0
    for message in messages:
        content = message.get("content")
        if isinstance(content, list):
            count += sum(1 for item in content if item.get("type") == "image_url")
    return count


def _write_test_image(path: Path, image_format: str = "PNG") -> None:
    image = QImage(4, 3, QImage.Format_RGB32)
    image.fill(0xFFFFFFFF)
    if not image.save(str(path), image_format):
        raise AssertionError(f"Unable to write temporary {image_format} test image")


class _StreamResponse:
    def __init__(self, lines, status_code: int = 200, text: str = "") -> None:
        self._lines = lines
        self.status_code = status_code
        self.text = text
        self.closed = False

    def iter_lines(self, decode_unicode: bool = False):
        return iter(self._lines)

    def close(self) -> None:
        self.closed = True


class ThreeStagePipelineTest(unittest.TestCase):
    def test_qwen_task2_prompt_declares_separated_authority_layers(self) -> None:
        prompt = _prompt("task2", "qwen_main_review")
        self.assertIn("`officialRubric` is the only scoring authority", prompt)
        self.assertIn("`derivedInternal`", prompt)
        self.assertIn("`legacyInternalCalibration`", prompt)
        self.assertNotIn("former IELTS examiner", prompt)
        self.assertNotIn("owner of the original score", prompt)

    def test_main_prompt_separates_original_score_from_rewrite_target(self) -> None:
        request = GradingRequest(
            taskType="task2",
            question="Discuss both views.",
            essay="This essay presents two views and gives an opinion.",
            targetBand=8.5,
        )
        snapshot = SubmissionSnapshot.from_request(request)
        contract = resolve_route(
            route_for("task2", "task2_understanding"), provider_id="test", model_id="test",
            api_key="test", base_url="https://example.test/v1",
        ).contract
        assert contract is not None
        understanding = fixture_understanding_service().run(snapshot, contract).artifact
        evidence = fixture_student_evidence_service().run(snapshot, understanding, contract).artifact
        text = _main_user_text(request, task2_snapshot(), understanding, evidence)
        payload = json.loads(text)
        self.assertTrue(payload["productExecutionInvariants"]["scoreBeforeRewrite"])
        self.assertFalse(payload["productExecutionInvariants"]["targetBandMayChangeOriginalScores"])
        self.assertEqual(payload["targetBand"], 8.5)
        self.assertEqual(payload["targetBandPurpose"], "rewrite_only")

    def test_default_models_keep_task1_multimodal_model(self) -> None:
        settings = AppSettings()
        self.assertEqual(settings.task1_qwen_model, "qwen3.7plus")
        self.assertEqual(settings.task2_qwen_model, "qwen3.7max")
        self.assertEqual(settings.task1_deepseek_model, "deepseekv4pro")

    def test_generic_environment_variables_are_supported(self) -> None:
        with patch.dict("os.environ", {
            "QWEN_API_KEY": "env-qwen",
            "QWEN_MODEL": "env-qwen-model",
            "DEEPSEEK_API_KEY": "env-deepseek",
            "DEEPSEEK_MODEL": "env-deepseek-model",
        }, clear=False):
            settings = AppSettings.load()
        self.assertEqual(settings.task1_qwen_api_key, "env-qwen")
        self.assertEqual(settings.task2_qwen_model, "env-qwen-model")
        self.assertEqual(settings.task1_deepseek_api_key, "env-deepseek")
        self.assertEqual(settings.task2_deepseek_model, "env-deepseek-model")

    def test_network_timeout_retries_then_reads_streamed_json(self) -> None:
        chunks = ['{"overallBand":7.5,', '"scores":{}', "}"]
        response = _StreamResponse(
            [
                "data: " + json.dumps({
                    "choices": [{"delta": {"content": chunk}}],
                })
                for chunk in chunks
            ] + ["data: [DONE]"]
        )
        with patch(
            "app.core.llm.requests.post",
            side_effect=[requests.ReadTimeout("slow"), response],
        ) as post, patch("app.core.llm.time.sleep"):
            text = _call_openai_compatible_chat(
                "qwen-model",
                "key",
                [{"role": "user", "content": "test"}],
                "https://example.com/v1",
                "Qwen 主批改",
            )
        self.assertEqual(json.loads(text)["overallBand"], 7.5)
        self.assertEqual(post.call_count, 2)
        self.assertTrue(response.closed)
        self.assertTrue(post.call_args.kwargs["stream"])
        self.assertEqual(post.call_args.kwargs["timeout"], (20, 300))

    def test_repeated_timeout_uses_friendly_message(self) -> None:
        with patch(
            "app.core.llm.requests.post",
            side_effect=[
                requests.ReadTimeout("HTTPSConnectionPool(host='secret.example')"),
                requests.ReadTimeout("HTTPSConnectionPool(host='secret.example')"),
            ],
        ), patch("app.core.llm.time.sleep"):
            with self.assertRaises(LLMError) as context:
                _call_openai_compatible_chat(
                    "qwen-model",
                    "key",
                    [{"role": "user", "content": "test"}],
                    "https://example.com/v1",
                    "Qwen 主批改",
                )
        message = str(context.exception)
        self.assertIn("响应超时", message)
        self.assertIn("自动重试一次", message)
        self.assertNotIn("HTTPSConnectionPool", message)
        self.assertNotIn("secret.example", message)

    def test_task1_original_image_is_sent_only_to_authorized_stages(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            image = Path(tmp) / "chart.png"
            _write_test_image(image)
            request = GradingRequest(
                taskType="task1",
                question="Summarise the chart.",
                essay="The figure rose to 20 units.",
                imagePath=str(image),
            )
            with patch(
                "app.core.llm._call_openai_compatible_chat",
                side_effect=[_main_json(), _syntax_json(), _final_json()],
            ) as chat:
                result = _complete_report(grade(request, settings=_settings()))

        self.assertEqual(result.balancedFinalVersion, "Final draft with 20 units.")
        self.assertEqual(chat.call_count, 3)
        self.assertEqual(
            [call.args[0] for call in chat.call_args_list],
            ["qwen3.7plus", "deepseekv4pro", "qwen3.7plus"],
        )
        self.assertEqual(_image_count(chat.call_args_list[0].args[2]), 1)
        self.assertEqual(_image_count(chat.call_args_list[1].args[2]), 0)
        self.assertEqual(_image_count(chat.call_args_list[2].args[2]), 1)
        first_url = chat.call_args_list[0].args[2][1]["content"][1]["image_url"]["url"]
        final_url = chat.call_args_list[2].args[2][1]["content"][1]["image_url"]["url"]
        self.assertEqual(first_url, final_url)

    def test_syntax_route_failure_is_explicitly_partial(self) -> None:
        request = GradingRequest(taskType="task2", question="Discuss both views.", essay="Essay.")
        with patch(
            "app.core.llm._call_openai_compatible_chat",
            side_effect=[_main_json("task2"), LLMError("offline"), _final_json("task2")],
        ) as chat:
            result = grade(request, settings=_settings())
        self.assertEqual(chat.call_count, 3)
        self.assertEqual(chat.call_args_list[0].args[0], "qwen3.7max")
        self.assertEqual(chat.call_args_list[2].args[0], "qwen3.7max")
        self.assertIs(result.status, WorkflowStatus.PARTIAL)
        self.assertIsNone(result.payload)
        self.assertEqual(
            result.stage_failures[0].state,
            WorkflowFailureState.PROVIDER_FAILURE,
        )

    def test_final_validation_failure_is_partial_without_report(self) -> None:
        request = GradingRequest(taskType="task2", question="Discuss both views.", essay="Essay.")
        with patch(
            "app.core.llm._call_openai_compatible_chat",
            side_effect=[_main_json("task2"), _syntax_json(), LLMError("offline")],
        ):
            result = grade(request, settings=_settings())
        self.assertIs(result.status, WorkflowStatus.PARTIAL)
        self.assertIsNone(result.payload)
        self.assertEqual(
            result.stage_failures[-1].stage.value,
            "final_validation",
        )

    def test_task1_without_image_fails_before_provider_execution(self) -> None:
        request = GradingRequest(taskType="task1", question="Summarise the chart.", essay="Essay.")
        with patch(
            "app.core.llm._call_openai_compatible_chat",
            side_effect=[_main_json(), _syntax_json(), _final_json()],
        ) as chat:
            with self.assertRaises(ImageInputError) as raised:
                grade(request, settings=_settings())
        self.assertEqual(raised.exception.failure.code, ImageInputFailureCode.IMAGE_REQUIRED)
        chat.assert_not_called()

    def test_repeated_bad_json_is_failed_and_not_preserved(self) -> None:
        request = GradingRequest(taskType="task2", question="Discuss both views.", essay="Essay.")
        settings = _settings()
        settings.task2_deepseek_api_key = ""
        with patch(
            "app.core.llm._call_openai_compatible_chat",
            side_effect=[
                "not json",
                "still not json",
                "bad final",
                "still bad final",
            ],
        ):
            result = grade(request, settings=settings)
        self.assertIs(result.status, WorkflowStatus.FAILED)
        self.assertIsNone(result.payload)
        self.assertNotIn("not json", repr(result))
        self.assertNotIn("Essay.", repr(result))

    def test_valid_final_json_without_balanced_version_is_partial(self) -> None:
        final = json.loads(_final_json("task2"))
        final["balancedFinalVersion"] = ""
        final["qwenExamReadyVersion"] = ""
        request = GradingRequest(taskType="task2", question="Discuss both views.", essay="Essay.")
        with patch(
            "app.core.llm._call_openai_compatible_chat",
            side_effect=[
                _main_json("task2"),
                _syntax_json(),
                json.dumps(final),
                json.dumps(final),
            ],
        ):
            result = grade(request, settings=_settings())
        self.assertIs(result.status, WorkflowStatus.PARTIAL)
        self.assertIsNone(result.payload)

    def test_mixed_string_stage_schema_is_rejected(self) -> None:
        main = {
            "taskType": "task2",
            "scores": {
                "overall": "7.5", "tr_ta": "7.5", "cc": "7",
                "lr": "8", "gra": "7.5",
            },
            "summary": "Strong response.",
            "paragraphFeedback": "Paragraph 2 needs a more specific example.",
            "vocabularyUpgrades": "Replace vague wording with a precise term.",
            "chartUnderstanding": "",
            "qwenExamReadyVersion": "Qwen Band 8 revision.",
        }
        syntax = {
            "deepseekSyntaxEnhancedVersion": "DeepSeek Band 8 revision.",
            "syntaxUpgrades": "Use one controlled concession clause.",
        }
        final = {
            **main,
            "deepseekSyntaxEnhancedVersion": "DeepSeek Band 8 revision.",
            "balancedFinalVersion": "Balanced Band 8 revision.",
            "deepseekChangesReview": "simplify the over-formal nominalisation",
            "memoriseWorthyExpressions": "a practical long-term measure",
            "nextPracticeSuggestions": "Rewrite paragraph 2 with one concrete example.",
        }
        request = GradingRequest(
            taskType="task2",
            question="Discuss both views.",
            essay="Candidate essay.",
            targetBand=8.0,
        )
        with patch(
            "app.core.llm._call_openai_compatible_chat",
            side_effect=[json.dumps(main), json.dumps(syntax), json.dumps(final)],
        ):
            result = grade(request, settings=_settings())

        self.assertIs(result.status, WorkflowStatus.FAILED)
        self.assertIsNone(result.payload)

    def test_target_band_profiles_keep_original_scores_and_adjust_outputs(self) -> None:
        essay = (
            "Some people support longer school holidays because children need rest.\n\n"
            "However, long breaks can interrupt learning and create childcare problems.\n\n"
            "In my view, shorter and more frequent holidays are more practical."
        )
        results = {}
        for target in (7.5, 8.0, 8.5):
            request = GradingRequest(
                taskType="task2",
                question="Discuss the advantages and disadvantages of long school holidays.",
                essay=essay,
                targetBand=target,
            )
            with patch(
                "app.core.llm._call_openai_compatible_chat",
                side_effect=[
                    _target_main_json(target),
                    _target_syntax_json(target),
                    _target_final_json(target),
                ],
            ) as chat:
                results[target] = _complete_report(grade(request, settings=_settings()))

            main_text = chat.call_args_list[0].args[2][1]["content"]
            deepseek_payload = json.loads(chat.call_args_list[1].args[2][1]["content"])
            final_payload = json.loads(chat.call_args_list[2].args[2][1]["content"])
            self.assertEqual(json.loads(main_text)["targetBand"], target)
            self.assertEqual(deepseek_payload["targetBand"], target)
            self.assertEqual(final_payload["targetBand"], target)
            self.assertEqual(final_payload["rubricIdentity"]["runtime_content_sha256"], TEST_RUNTIME_HASH)
            self.assertNotIn("officialRubric", final_payload)
            self.assertNotIn("rubricIdentity", deepseek_payload)

        for target, result in results.items():
            self.assertEqual(result.overallBand, 6.5)
            self.assertEqual(result.rubric_id, "ielts_academic_writing_task2")
            self.assertEqual(result.rubric_version, "1.0.0")
            self.assertEqual(result.runtime_content_sha256, TEST_RUNTIME_HASH)
            self.assertEqual([score.score for score in result.scores], [6.5, 6.5, 6.0, 6.5])
            self.assertEqual(result.scoreDiagnosis.targetBand, target)
            self.assertEqual(result.balancedFinalVersion, f"Balanced, natural final version for Band {target:g}.")
            self.assertEqual(
                [item.expression for item in result.memoriseWorthyExpressions],
                [f"a practical Band {target:g} expression"],
            )
            self.assertEqual(len(result.paragraphFeedback), 3)
            self.assertTrue(all(item.issuesEn for item in result.paragraphFeedback))

        self.assertEqual(len(results[7.5].syntaxUpgrades), 5)
        self.assertEqual(len(results[8.0].syntaxUpgrades), 6)
        self.assertEqual(len(results[8.5].syntaxUpgrades), 6)

    def test_same_essay_reuses_first_score_snapshot_across_targets(self) -> None:
        question = "Should cities invest more in public parks?"
        essay = "A unique introduction.\n\nA unique body paragraph.\n\nA unique conclusion."

        first_main = json.loads(_target_main_json(7.5))
        first_main["overallBand"] = 6.5
        first_main["scores"][0]["score"] = 6.0
        first_final = json.loads(_target_final_json(7.5))

        second_main = json.loads(_target_main_json(8.5))
        second_main["overallBand"] = 8.0
        for score in second_main["scores"]:
            score["score"] = 8.0
        second_final = json.loads(_target_final_json(8.5))

        first_request = GradingRequest(
            taskType="task2",
            question=question,
            essay=essay,
            targetBand=7.5,
        )
        second_request = GradingRequest(
            taskType="task2",
            question=question,
            essay=essay,
            targetBand=8.5,
        )
        with patch(
            "app.core.llm._call_openai_compatible_chat",
            side_effect=[
                json.dumps(first_main),
                _target_syntax_json(7.5),
                json.dumps(first_final),
                json.dumps(second_main),
                _target_syntax_json(8.5),
                json.dumps(second_final),
            ],
        ):
            first = _complete_report(grade(first_request, settings=_settings()))
            second = _complete_report(grade(second_request, settings=_settings()))

        self.assertEqual(first.overallBand, 6.5)
        self.assertEqual(second.overallBand, 6.5)
        self.assertEqual(
            [score.score for score in second.scores],
            [score.score for score in first.scores],
        )
        self.assertEqual(second.scoreDiagnosis.targetBand, 8.5)

    def test_final_validator_replaces_over_academic_items_across_report(self) -> None:
        main = json.loads(_target_main_json(8.0))
        main["vocabularyUpgrades"] = [{
            "originalExpression": "be interested",
            "suggestedExpression": "cultivate intrinsic motivation",
            "whyBetterEn": "More academic.",
            "recommendation": "Use confidently",
        }]
        main["paragraphFeedback"] = [{
            "paragraphNumber": 1,
            "function": "Introduction",
            "strengthsEn": ["The position is clear."],
            "issuesEn": ["The wording is broad."],
            "howToImproveEn": ["Use a more precise but natural phrase."],
            "sentenceUpgrade": {
                "original": "Children become interested.",
                "improved": "Children cultivate intrinsic motivation.",
            },
        }]
        final = json.loads(_target_final_json(8.0))
        final["paragraphFeedback"] = [{
            "paragraphNumber": 1,
            "function": "Introduction",
            "strengthsEn": ["The position is clear."],
            "issuesEn": ["The wording is broad."],
            "howToImproveEn": ["Use a natural phrase."],
            "sentenceUpgrade": {
                "original": "Children become interested.",
                "improved": "Children develop a genuine interest.",
            },
        }]
        final["vocabularyUpgrades"] = [{
            "originalExpression": "be interested",
            "suggestedExpression": "develop a genuine interest",
            "whyBetterEn": "Natural, precise and easy to reuse.",
            "recommendation": "Use confidently",
        }]
        final["syntaxUpgrades"] = []
        final["balancedFinalVersion"] = (
            "Children can develop a genuine interest in language learning."
        )
        final["memoriseWorthyExpressions"] = []
        final["nextPracticeSuggestions"] = []
        request = GradingRequest(
            taskType="task2",
            question="When should children begin learning a foreign language?",
            essay="Children become interested when lessons are enjoyable.",
            targetBand=8.0,
        )
        with patch(
            "app.core.llm._call_openai_compatible_chat",
            side_effect=[
                json.dumps(main),
                _target_syntax_json(8.0),
                json.dumps(final),
            ],
        ):
            result = _complete_report(grade(request, settings=_settings()))

        rendered = json.dumps(
            {
                "paragraphFeedback": [
                    item.sentenceUpgrade for item in result.paragraphFeedback
                ],
                "vocabularyUpgrades": [
                    item.suggestedExpression for item in result.vocabularyUpgrades
                ],
                "syntaxUpgrades": [
                    item.upgradedSentence for item in result.syntaxUpgrades
                ],
                "balancedFinalVersion": result.balancedFinalVersion,
                "memoriseWorthyExpressions": [
                    item.expression for item in result.memoriseWorthyExpressions
                ],
            },
            ensure_ascii=False,
        ).lower()
        self.assertNotIn("cultivate intrinsic motivation", rendered)
        self.assertIn("develop a genuine interest", rendered)
        self.assertEqual(result.syntaxUpgrades, [])
        self.assertEqual(result.memoriseWorthyExpressions, [])
        self.assertEqual(result.nextPracticeSuggestions, [])

    def test_pdf_export_supports_rich_learning_cards(self) -> None:
        result = GradingResult.from_dict(json.loads(_target_final_json(8.0)))
        result.paragraphFeedback = []
        request = GradingRequest(
            taskType="task2",
            question="Discuss both views.",
            essay="Introduction.\n\nBody paragraph.\n\nConclusion.",
            targetBand=8.0,
        )
        # 用一次 normalize 路径补齐逐段反馈和诊断。
        with patch(
            "app.core.llm._call_openai_compatible_chat",
            side_effect=[
                _target_main_json(8.0),
                _target_syntax_json(8.0),
                _target_final_json(8.0),
            ],
        ):
            result = _complete_report(grade(request, settings=_settings()))
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "report.pdf"
            export_report(
                str(output),
                request.taskType,
                request.question,
                request.essay,
                result,
            )
            self.assertTrue(output.is_file())
            self.assertGreater(output.stat().st_size, 10_000)

    def test_pdf_export_splits_long_memorise_section_across_pages(self) -> None:
        data = json.loads(_target_final_json(8.0))
        long_note = (
            "这个表达适合在论证社会政策、教育和公共服务时使用，"
            "需要结合具体主语和结果，避免脱离语境机械套用。"
        ) * 8
        data["memoriseWorthyExpressions"] = [
            {
                "expression": f"transferable academic expression {index}",
                "meaningZh": long_note,
                "exampleSentence": (
                    "A carefully targeted policy can protect vulnerable groups "
                    "without weakening individual responsibility."
                ),
                "whyUsefulZh": long_note,
                "warningZh": "考场中只在语义准确时使用。",
            }
            for index in range(1, 9)
        ]
        data["nextPracticeSuggestions"] = [
            "完成一次限时写作并逐句检查论证展开。",
            "整理本次作文中重复出现的宽泛词汇。",
            "练习两种可控的复杂句结构。",
        ]
        result = GradingResult.from_dict(data)
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "long-learning-report.pdf"
            export_report(
                str(output),
                "task2",
                "Discuss both views and give your opinion.",
                "Introduction.\n\nBody paragraph.\n\nConclusion.",
                result,
            )
            self.assertTrue(output.is_file())
            self.assertGreater(output.stat().st_size, 10_000)


if __name__ == "__main__":
    unittest.main()
