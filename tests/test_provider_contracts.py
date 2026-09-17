from __future__ import annotations

import json
import unittest
from unittest.mock import Mock, patch
import warnings

import requests

from app.core.llm import LLMError, ProviderFailureError, _call_openai_compatible_chat, grade
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
from app.core.models import GradingRequest
from app.core.providers import (
    Capability,
    ModelRoute,
    OpenAICompatibleTransport,
    ProviderCallRequest,
    ProviderCallResult,
    ProviderFailureCode,
    provider_preflight,
    resolve_active_routes,
    resolve_route,
    route_for,
)
from app.core.settings import AppSettings
from app.core.workflow import WorkflowStatus


def _diagnosis() -> dict:
    return {
        "overall": 7.0,
        "targetBand": 7.5,
        "gapToTarget": 0.5,
        "criteria": [
            {"name": label, "score": 7.0}
            for label in ("TR", "CC", "LR", "GRA")
        ],
    }


class _StreamResponse:
    def __init__(self, lines: list[str], status_code: int = 200, text: str = "") -> None:
        self._lines = lines
        self.status_code = status_code
        self.text = text
        self.closed = False

    def iter_lines(self, decode_unicode: bool = False):
        yield from self._lines

    def close(self) -> None:
        self.closed = True


def _settings() -> AppSettings:
    return AppSettings(
        task1_qwen_api_key="primary-key",
        task1_deepseek_api_key="syntax-key",
        task2_qwen_api_key="primary-key",
        task2_deepseek_api_key="syntax-key",
    )


def _main_json() -> str:
    return json.dumps(
        {
            "taskType": "task2",
            "overallBand": 7.0,
            "summary": "Clear response.",
            "scores": [
                {"label": "TR", "score": 7.0, "rationale": ""},
                {"label": "CC", "score": 7.0, "rationale": ""},
                {"label": "LR", "score": 7.0, "rationale": ""},
                {"label": "GRA", "score": 7.0, "rationale": ""},
            ],
            "qwenExamReadyVersion": "Primary draft.",
        }
    )


def _syntax_json() -> str:
    return json.dumps({"deepseekSyntaxEnhancedVersion": "Syntax draft.", "syntaxUpgrades": []})


def _final_json() -> str:
    value = json.loads(_main_json())
    value["balancedFinalVersion"] = "Validated draft."
    value["scoreDiagnosis"] = _diagnosis()
    value["paragraphFeedback"] = []
    value["vocabularyUpgrades"] = []
    value["syntaxUpgrades"] = []
    value["memoriseWorthyExpressions"] = []
    value["nextPracticeSuggestions"] = []
    return json.dumps(value)


class ProviderContractTests(unittest.TestCase):
    def test_reasoning_effort_is_forwarded_only_when_requested(self):
        resolution = resolve_route(route_for('task1','chart_claim_extraction'),
            provider_id='fixture-provider',model_id='glm-5.3-flash',api_key='fixture-key',
            base_url='https://fixture.invalid/v1',declared_capabilities=frozenset(Capability))
        for effort in (None,'low'):
            post=Mock(side_effect=requests.ReadTimeout())
            transport=OpenAICompatibleTransport(post=post,sleep=lambda _:None)
            transport.call(resolution.contract,ProviderCallRequest(
                [{'role':'user','content':'Extract claims'}],'claims',reasoning_effort=effort))
            payload=post.call_args.kwargs['json']
            if effort is None:self.assertNotIn('reasoning_effort',payload)
            else:self.assertEqual(payload['reasoning_effort'],'low')

    def test_declared_route_resolves_to_a_log_safe_contract(self) -> None:
        route = route_for("task2", "main_review")
        assert route is not None
        resolution = resolve_route(
            route,
            provider_id="openai-compatible",
            model_id="configured-model",
            api_key="secret-key",
            base_url="https://example.test/v1",
        )
        self.assertTrue(resolution.ok)
        assert resolution.contract is not None
        self.assertEqual(
            resolution.contract.endpoint, "https://example.test/v1/chat/completions"
        )
        self.assertNotIn("secret-key", repr(resolution.contract))
        self.assertNotIn("secret-key", resolution.contract.snapshot.as_log_fields().values())
        self.assertTrue(resolution.contract.snapshot.has_credentials)

    def test_missing_credentials_and_malformed_endpoint_fail_before_transport(self) -> None:
        route = route_for("task2", "main_review")
        assert route is not None
        missing = resolve_route(
            route,
            provider_id="openai-compatible",
            model_id="configured-model",
            api_key="",
            base_url="https://example.test/v1",
        )
        malformed = resolve_route(
            route,
            provider_id="openai-compatible",
            model_id="configured-model",
            api_key="secret-key",
            base_url="not-a-url",
        )
        credential_url = resolve_route(
            route,
            provider_id="openai-compatible",
            model_id="configured-model",
            api_key="secret-key",
            base_url="https://embedded-secret@example.test/v1",
        )
        self.assertEqual(missing.failure.code, ProviderFailureCode.MISSING_CREDENTIALS)
        self.assertEqual(malformed.failure.code, ProviderFailureCode.MALFORMED_ENDPOINT)
        self.assertEqual(credential_url.failure.code, ProviderFailureCode.MALFORMED_ENDPOINT)

    def test_unknown_or_incompatible_capability_fails_deterministically(self) -> None:
        route = route_for("task1", "main_review")
        assert route is not None
        incompatible = resolve_route(
            route,
            provider_id="openai-compatible",
            model_id="text-only-model",
            api_key="secret-key",
            base_url="https://example.test/v1",
            declared_capabilities=frozenset({Capability.TEXT_INPUT}),
            requires_image=True,
        )
        unknown_route = ModelRoute(
            route_id="task2.unknown",
            task_type="task2",
            stage="unknown",
            configuration_key="primary",
            required_capabilities=frozenset({"not-a-capability"}),  # type: ignore[arg-type]
        )
        unknown = resolve_route(
            unknown_route,
            provider_id="openai-compatible",
            model_id="configured-model",
            api_key="secret-key",
            base_url="https://example.test/v1",
        )
        self.assertEqual(incompatible.failure.code, ProviderFailureCode.INCOMPATIBLE_ROUTE)
        self.assertEqual(unknown.failure.code, ProviderFailureCode.UNKNOWN_CAPABILITY)

    def test_preflight_does_not_call_network_and_keeps_optional_failure_typed(self) -> None:
        settings = _settings()
        settings.task2_deepseek_api_key = ""
        with patch("app.core.providers.requests.post") as post:
            routes = provider_preflight(settings, "task2", requires_image=False)
        self.assertTrue(routes["main_review"].ok)
        self.assertTrue(routes["final_validation"].ok)
        self.assertEqual(
            routes["syntax_enhancement"].failure.code,
            ProviderFailureCode.MISSING_CREDENTIALS,
        )
        post.assert_not_called()

    def test_all_active_routes_resolve_and_required_failure_stops_grade_before_transport(self) -> None:
        for task_type, requires_image in (("task1", True), ("task2", False)):
            routes = resolve_active_routes(_settings(), task_type, requires_image)
            self.assertTrue(all(resolution.ok for resolution in routes.values()))

        settings = _settings()
        settings.task2_qwen_api_key = ""
        request = GradingRequest(
            taskType="task2", question="Discuss both views.", essay="Candidate essay."
        )
        with patch("app.core.llm._call_openai_compatible_chat") as adapter:
            with self.assertRaises(ProviderFailureError) as raised:
                grade(request, settings=settings)
        self.assertEqual(raised.exception.failure.code, ProviderFailureCode.MISSING_CREDENTIALS)
        adapter.assert_not_called()

    def test_streaming_assembly_and_usage_are_normalized(self) -> None:
        route = route_for("task2", "main_review")
        assert route is not None
        contract = resolve_route(
            route,
            provider_id="openai-compatible",
            model_id="configured-model",
            api_key="secret-key",
            base_url="https://example.test/v1",
        ).contract
        assert contract is not None
        response = _StreamResponse(
            [
                'data: {"choices":[{"delta":{"content":"{\\"ok\\":"}}]}',
                'data: {"choices":[{"delta":{"content":"true}"}}],"usage":{"prompt_tokens":3,"completion_tokens":2,"total_tokens":5}}',
                "data: [DONE]",
            ]
        )
        post = Mock(return_value=response)
        result = OpenAICompatibleTransport(post=post, sleep=Mock()).call(
            contract,
            ProviderCallRequest(
                messages=[{"role": "user", "content": "test"}],
                display_name="contract test",
                require_json_object=True,
            ),
        )
        self.assertTrue(result.ok)
        self.assertEqual(json.loads(result.content), {"ok": True})
        self.assertEqual(result.usage.total_tokens, 5)
        self.assertTrue(response.closed)
        self.assertTrue(post.call_args.kwargs["stream"])

    def test_timeout_http_and_schema_failures_are_typed_and_redacted(self) -> None:
        route = route_for("task2", "main_review")
        assert route is not None
        contract = resolve_route(
            route,
            provider_id="openai-compatible",
            model_id="configured-model",
            api_key="secret-key",
            base_url="https://example.test/v1",
        ).contract
        assert contract is not None
        request = ProviderCallRequest(
            messages=[{"role": "user", "content": "candidate essay must not appear"}],
            display_name="contract test",
            require_json_object=True,
        )
        timeout = OpenAICompatibleTransport(
            post=Mock(side_effect=[requests.ReadTimeout("secret.example"), requests.ReadTimeout("secret.example")]),
            sleep=Mock(),
        ).call(contract, request)
        http = OpenAICompatibleTransport(
            post=Mock(return_value=_StreamResponse([], status_code=500, text="candidate essay")),
            sleep=Mock(),
        ).call(contract, request)
        schema = OpenAICompatibleTransport(
            post=Mock(return_value=_StreamResponse(['data: {"choices":[{"delta":{"content":"not-json"}}]}'])),
            sleep=Mock(),
        ).call(contract, request)
        self.assertEqual(timeout.failure.code, ProviderFailureCode.TIMEOUT)
        self.assertEqual(http.failure.code, ProviderFailureCode.HTTP_ERROR)
        self.assertEqual(schema.failure.code, ProviderFailureCode.SCHEMA_ERROR)
        self.assertNotIn("secret.example", timeout.failure.message)
        self.assertNotIn("candidate essay", http.failure.message)

    def test_legacy_adapter_is_deprecated_but_keeps_normalized_transport_behavior(self) -> None:
        response = _StreamResponse(['data: {"choices":[{"delta":{"content":"{}"}}]}'])
        with patch("app.core.llm.requests.post", return_value=response), warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            text = _call_openai_compatible_chat(
                "configured-model",
                "secret-key",
                [{"role": "user", "content": "test"}],
                "https://example.test/v1",
                "compatibility test",
            )
        self.assertEqual(text, "{}")
        self.assertTrue(any(item.category is DeprecationWarning for item in caught))
        self.assertTrue(response.closed)

    def test_scoring_pipeline_consumes_normalized_provider_results(self) -> None:
        request = GradingRequest(
            taskType="task2", question="Discuss both views.", essay="Candidate essay."
        )
        with patch(
            "app.core.llm._call_through_compatibility_adapter",
            side_effect=[
                ProviderCallResult(content=_main_json()),
                ProviderCallResult(content=_syntax_json()),
                ProviderCallResult(content=_final_json()),
            ],
        ) as adapter:
            result = grade(request, settings=_settings())
        self.assertIs(result.status, WorkflowStatus.COMPLETE)
        assert result.payload is not None
        self.assertEqual(result.payload.balancedFinalVersion, "Validated draft.")
        self.assertEqual(adapter.call_count, 3)
        self.assertTrue(all(call.args[0].snapshot.has_credentials for call in adapter.call_args_list))


if __name__ == "__main__":
    unittest.main()
