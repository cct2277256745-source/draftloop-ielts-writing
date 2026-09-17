"""Provider-independent, fail-closed benchmark execution primitives.

This module deliberately stores only case/result fingerprints in persistent run
artifacts.  It is an evaluation execution boundary, not a scoring authority:
tiers remain labels and no result is converted into an architecture or quality
recommendation here.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from enum import Enum
import hashlib
import json
import os
from pathlib import Path
import tempfile
from time import perf_counter
from types import MappingProxyType
from typing import Any, Callable, Iterable, Mapping, Protocol

from .observability import CostState, ModelPricing, UsageCost
from .providers import ProviderCallResult, ProviderUsage


ARTIFACT_SCHEMA_VERSION = "benchmark-run-artifact-v3"
PREVIOUS_ARTIFACT_SCHEMA_VERSION = "benchmark-run-artifact-v2"
LEGACY_ARTIFACT_SCHEMA_VERSION = "benchmark-run-artifact-v1"
RUN_ID_SCHEMA_VERSION = LEGACY_ARTIFACT_SCHEMA_VERSION
RUNNER_SEMANTIC_VERSION = "p1-03-v1"


class EvaluationErrorCode(str, Enum):
    INVALID_CASE = "INVALID_CASE"
    DUPLICATE_CASE = "DUPLICATE_CASE"
    INVALID_CONFIG = "INVALID_CONFIG"
    ARTIFACT_INVALID = "ARTIFACT_INVALID"
    RESUME_DRIFT = "RESUME_DRIFT"


class EvaluationError(ValueError):
    def __init__(self, code: EvaluationErrorCode, message: str = "") -> None:
        self.code = code
        super().__init__(message or code.value)


class TierLabel(str, Enum):
    """Evidence-role labels.  They deliberately carry no scoring authority."""

    A = "A"
    B = "B"
    C = "C"
    D = "D"


class CaseStatus(str, Enum):
    PASSED = "PASSED"
    FAILED = "FAILED"


class RunState(str, Enum):
    INCOMPLETE = "INCOMPLETE"
    COMPLETE = "COMPLETE"


def _canonical_json(value: Any) -> str:
    """Return canonical JSON or reject values outside the artifact contract."""
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise EvaluationError(EvaluationErrorCode.ARTIFACT_INVALID, "not canonical JSON") from exc


def _digest(value: Any) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def _freeze(value: Any) -> Any:
    if isinstance(value, Mapping):
        return MappingProxyType({str(key): _freeze(child) for key, child in sorted(value.items())})
    if isinstance(value, (list, tuple)):
        return tuple(_freeze(child) for child in value)
    if value is None or isinstance(value, (str, int, float, bool)):
        # Validate floats and subclasses through canonical encoding.
        _canonical_json(value)
        return value
    raise EvaluationError(EvaluationErrorCode.ARTIFACT_INVALID, "unsupported JSON value")


def _thaw(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {key: _thaw(child) for key, child in value.items()}
    if isinstance(value, tuple):
        return [_thaw(child) for child in value]
    return value


def _safe_identifier(value: str, code: EvaluationErrorCode) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > 200:
        raise EvaluationError(code, "identifier is blank or invalid")
    return value.strip()


def _freeze_artifact_links(value: Mapping[str, str] | None) -> Mapping[str, str] | None:
    if value is None:
        return None
    p2_01_required = {
        "submissionSnapshotId", "essayVersionId", "locatorManifestSha256", "task2UnderstandingSha256",
    }
    p2_02_required = p2_01_required | {"studentEvidenceSha256"}
    if not isinstance(value, Mapping) or set(value) not in (p2_01_required, p2_02_required):
        raise EvaluationError(EvaluationErrorCode.ARTIFACT_INVALID, "invalid artifact links")
    links = {str(key): str(item).strip() for key, item in value.items()}
    if (
        not links["submissionSnapshotId"].startswith("submission:")
        or len(links["submissionSnapshotId"]) != len("submission:") + 64
        or not links["essayVersionId"].startswith("essay:")
        or len(links["essayVersionId"]) != len("essay:") + 64
        or any(char not in "0123456789abcdef" for char in links["submissionSnapshotId"][len("submission:"):])
        or any(char not in "0123456789abcdef" for char in links["essayVersionId"][len("essay:"):])
        or any(
            len(links[key]) != 64 or any(char not in "0123456789abcdef" for char in links[key])
            for key in ({"locatorManifestSha256", "task2UnderstandingSha256"} | ({"studentEvidenceSha256"} if "studentEvidenceSha256" in links else set()))
        )
    ):
        raise EvaluationError(EvaluationErrorCode.ARTIFACT_INVALID, "unsafe artifact links")
    return MappingProxyType(dict(sorted(links.items())))


def _artifact_version_for_links(links: Mapping[str, str] | None) -> str:
    if links is None:
        return LEGACY_ARTIFACT_SCHEMA_VERSION
    return ARTIFACT_SCHEMA_VERSION if "studentEvidenceSha256" in links else PREVIOUS_ARTIFACT_SCHEMA_VERSION


@dataclass(frozen=True)
class BenchmarkCase:
    """An immutable executable case.  Payload is never copied to the artifact."""

    case_id: str
    tier: TierLabel | str
    task_type: str
    payload: Mapping[str, Any] = field(repr=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "case_id", _safe_identifier(self.case_id, EvaluationErrorCode.INVALID_CASE))
        try:
            tier = TierLabel(self.tier)
        except ValueError as exc:
            raise EvaluationError(EvaluationErrorCode.INVALID_CASE, "unknown tier") from exc
        object.__setattr__(self, "tier", tier)
        object.__setattr__(self, "task_type", _safe_identifier(self.task_type, EvaluationErrorCode.INVALID_CASE))
        if not isinstance(self.payload, Mapping):
            raise EvaluationError(EvaluationErrorCode.INVALID_CASE, "payload must be an object")
        try:
            object.__setattr__(self, "payload", _freeze(self.payload))
        except EvaluationError as exc:
            raise EvaluationError(EvaluationErrorCode.INVALID_CASE, "payload is invalid") from exc

    def identity_payload(self) -> dict[str, Any]:
        return {
            "caseId": self.case_id,
            "tier": self.tier.value,
            "taskType": self.task_type,
            "payload": _thaw(self.payload),
        }

    @property
    def content_sha256(self) -> str:
        return _digest(self.identity_payload())


@dataclass(frozen=True)
class ExperimentConfig:
    """Explicit semantic configuration for one run plan."""

    semantic_version: str
    executor_id: str
    version_snapshot: Mapping[str, Any]
    selected_case_ids: tuple[str, ...] = ()
    tiers: tuple[TierLabel | str, ...] = ()
    limit: int | None = None
    options: Mapping[str, Any] = field(default_factory=dict, repr=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "semantic_version", _safe_identifier(self.semantic_version, EvaluationErrorCode.INVALID_CONFIG))
        object.__setattr__(self, "executor_id", _safe_identifier(self.executor_id, EvaluationErrorCode.INVALID_CONFIG))
        if not isinstance(self.version_snapshot, Mapping) or not self.version_snapshot:
            raise EvaluationError(EvaluationErrorCode.INVALID_CONFIG, "version snapshot is required")
        if not isinstance(self.options, Mapping):
            raise EvaluationError(EvaluationErrorCode.INVALID_CONFIG, "options must be an object")
        try:
            snapshot = _freeze(self.version_snapshot)
            options = _freeze(self.options)
        except EvaluationError as exc:
            raise EvaluationError(EvaluationErrorCode.INVALID_CONFIG, "config JSON is invalid") from exc
        selected = tuple(sorted(_safe_identifier(value, EvaluationErrorCode.INVALID_CONFIG) for value in self.selected_case_ids))
        if len(set(selected)) != len(selected):
            raise EvaluationError(EvaluationErrorCode.INVALID_CONFIG, "duplicate selected case")
        try:
            tiers = tuple(sorted({TierLabel(value) for value in self.tiers}, key=lambda item: item.value))
        except ValueError as exc:
            raise EvaluationError(EvaluationErrorCode.INVALID_CONFIG, "unknown tier filter") from exc
        if self.limit is not None and (not isinstance(self.limit, int) or self.limit <= 0):
            raise EvaluationError(EvaluationErrorCode.INVALID_CONFIG, "limit must be positive")
        object.__setattr__(self, "version_snapshot", snapshot)
        object.__setattr__(self, "options", options)
        object.__setattr__(self, "selected_case_ids", selected)
        object.__setattr__(self, "tiers", tiers)

    def identity_payload(self) -> dict[str, Any]:
        return {
            "semanticVersion": self.semantic_version,
            "executorId": self.executor_id,
            "versionSnapshot": _thaw(self.version_snapshot),
            "selectedCaseIds": list(self.selected_case_ids),
            "tiers": [tier.value for tier in self.tiers],
            "limit": self.limit,
            "options": _thaw(self.options),
        }

    @property
    def content_sha256(self) -> str:
        return _digest(self.identity_payload())


def deterministic_selection(cases: Iterable[BenchmarkCase], config: ExperimentConfig) -> tuple[BenchmarkCase, ...]:
    """Select cases in a stable order and reject missing/duplicate identity."""
    by_id: dict[str, BenchmarkCase] = {}
    for case in cases:
        if not isinstance(case, BenchmarkCase):
            raise EvaluationError(EvaluationErrorCode.INVALID_CASE, "case is not a BenchmarkCase")
        if case.case_id in by_id:
            raise EvaluationError(EvaluationErrorCode.DUPLICATE_CASE, case.case_id)
        by_id[case.case_id] = case
    selected_ids = set(config.selected_case_ids)
    missing = selected_ids - set(by_id)
    if missing:
        raise EvaluationError(EvaluationErrorCode.INVALID_CONFIG, "selected case is missing")
    selected = [
        case for case_id, case in sorted(by_id.items())
        if (not selected_ids or case_id in selected_ids)
        and (not config.tiers or case.tier in config.tiers)
    ]
    if config.limit is not None:
        selected = selected[:config.limit]
    if not selected:
        raise EvaluationError(EvaluationErrorCode.INVALID_CONFIG, "selection is empty")
    return tuple(selected)


@dataclass(frozen=True)
class BenchmarkRun:
    """Immutable plan; its ID is content-addressed by selected cases and config."""

    run_id: str
    config: ExperimentConfig
    cases: tuple[BenchmarkCase, ...]

    @classmethod
    def create(cls, cases: Iterable[BenchmarkCase], config: ExperimentConfig) -> "BenchmarkRun":
        selected = deterministic_selection(cases, config)
        run_id = _digest({
            "artifactSchemaVersion": RUN_ID_SCHEMA_VERSION,
            "config": config.identity_payload(),
            "cases": [case.identity_payload() for case in selected],
        })
        return cls(run_id=run_id, config=config, cases=selected)

    def identity_payload(self) -> dict[str, Any]:
        return {
            "runId": self.run_id,
            "config": self.config.identity_payload(),
            "caseContentSha256": [case.content_sha256 for case in self.cases],
        }


@dataclass(frozen=True)
class ExecutionOutcome:
    """Normalized provider-independent case outcome; raw result bodies are omitted."""

    schema_valid: bool
    usage: ProviderUsage | None = None
    provider_id: str = "offline-fixture"
    model_id: str = "fixture-model"
    model_version: str = "fixture-model@v1"
    latency_ms: int = 0
    provider_call_count: int = 1
    failure_code: str | None = None
    result: Mapping[str, Any] = field(default_factory=dict, repr=False)
    artifact_links: Mapping[str, str] | None = field(default=None, repr=False)

    def __post_init__(self) -> None:
        if not isinstance(self.schema_valid, bool) or not isinstance(self.result, Mapping):
            raise EvaluationError(EvaluationErrorCode.ARTIFACT_INVALID, "malformed execution outcome")
        if not isinstance(self.latency_ms, int) or self.latency_ms < 0 or not isinstance(self.provider_call_count, int) or self.provider_call_count < 0:
            raise EvaluationError(EvaluationErrorCode.ARTIFACT_INVALID, "invalid execution counts")
        for value in (self.provider_id, self.model_id, self.model_version):
            _safe_identifier(value, EvaluationErrorCode.ARTIFACT_INVALID)
        if self.usage is not None and not isinstance(self.usage, ProviderUsage):
            raise EvaluationError(EvaluationErrorCode.ARTIFACT_INVALID, "invalid usage")
        if self.failure_code is not None:
            _safe_identifier(self.failure_code, EvaluationErrorCode.ARTIFACT_INVALID)
        object.__setattr__(self, "result", _freeze(self.result))
        object.__setattr__(self, "artifact_links", _freeze_artifact_links(self.artifact_links))

    @property
    def result_sha256(self) -> str:
        if self.artifact_links is None:
            return _digest(_thaw(self.result))
        return _digest({"result": _thaw(self.result), "artifactLinks": _thaw(self.artifact_links)})


class BenchmarkExecutor(Protocol):
    def execute(self, case: BenchmarkCase, config: ExperimentConfig) -> ExecutionOutcome:
        """Execute one case without granting the runner scoring authority."""


class ProviderResultAdapter:
    """Optional adapter from the P0-02 normalized result contract to this runner.

    The callable is injected by a future provider-specific boundary.  This adapter
    never creates a transport, resolves settings, or retains request/response text
    in artifacts; it only normalizes usage, typed failures, and a result digest.
    """

    def __init__(
        self,
        invoke: Callable[[BenchmarkCase, ExperimentConfig], ProviderCallResult],
        *,
        provider_id: str,
        model_id: str,
        model_version: str,
        validate_schema: Callable[[str], bool],
    ) -> None:
        self._invoke = invoke
        self._provider_id = _safe_identifier(provider_id, EvaluationErrorCode.INVALID_CONFIG)
        self._model_id = _safe_identifier(model_id, EvaluationErrorCode.INVALID_CONFIG)
        self._model_version = _safe_identifier(model_version, EvaluationErrorCode.INVALID_CONFIG)
        self._validate_schema = validate_schema

    def execute(self, case: BenchmarkCase, config: ExperimentConfig) -> ExecutionOutcome:
        started = perf_counter()
        result = self._invoke(case, config)
        latency_ms = int((perf_counter() - started) * 1000)
        if not isinstance(result, ProviderCallResult):
            raise EvaluationError(EvaluationErrorCode.ARTIFACT_INVALID, "provider adapter result")
        if result.failure is not None:
            return ExecutionOutcome(
                schema_valid=False, usage=result.usage, provider_id=self._provider_id,
                model_id=self._model_id, model_version=self._model_version,
                latency_ms=latency_ms, provider_call_count=max(1, result.attempts),
                failure_code=result.failure.code.value, result={},
            )
        return ExecutionOutcome(
            schema_valid=bool(self._validate_schema(result.content)), usage=result.usage,
            provider_id=self._provider_id, model_id=self._model_id,
            model_version=self._model_version, latency_ms=latency_ms,
            provider_call_count=max(1, result.attempts),
            result={"providerContentSha256": _digest(result.content)},
        )


@dataclass(frozen=True)
class CaseResult:
    case_id: str
    case_content_sha256: str
    tier: TierLabel
    status: CaseStatus
    latency_ms: int
    provider_call_count: int
    schema_valid: bool
    provider_id: str | None
    model_id: str | None
    model_version: str | None
    prompt_tokens: int | None
    completion_tokens: int | None
    total_tokens: int | None
    cost_state: CostState
    cost_total: str | None
    cost_currency: str | None
    failure_code: str | None
    result_sha256: str | None
    artifact_links: Mapping[str, str] | None = field(default=None, repr=False, compare=False)

    @classmethod
    def failed(cls, case: BenchmarkCase, code: str, latency_ms: int = 0) -> "CaseResult":
        return cls(
            case_id=case.case_id, case_content_sha256=case.content_sha256, tier=case.tier,
            status=CaseStatus.FAILED, latency_ms=max(0, latency_ms), provider_call_count=0, schema_valid=False,
            provider_id=None, model_id=None, model_version=None,
            prompt_tokens=None, completion_tokens=None, total_tokens=None,
            cost_state=CostState.UNKNOWN, cost_total=None, cost_currency=None,
            failure_code=code, result_sha256=None,
            artifact_links=None,
        )

    @classmethod
    def from_outcome(cls, case: BenchmarkCase, outcome: ExecutionOutcome, cost: UsageCost) -> "CaseResult":
        status = CaseStatus.PASSED if outcome.schema_valid and not outcome.failure_code else CaseStatus.FAILED
        return cls(
            case_id=case.case_id, case_content_sha256=case.content_sha256, tier=case.tier,
            status=status, latency_ms=outcome.latency_ms, provider_call_count=outcome.provider_call_count, schema_valid=outcome.schema_valid,
            provider_id=outcome.provider_id, model_id=outcome.model_id, model_version=outcome.model_version,
            prompt_tokens=cost.prompt_tokens, completion_tokens=cost.completion_tokens,
            total_tokens=cost.total_tokens, cost_state=cost.state,
            cost_total=(str(cost.total) if cost.total is not None else None), cost_currency=cost.currency,
            failure_code=(outcome.failure_code or (None if outcome.schema_valid else "SCHEMA_FAILURE")),
            result_sha256=outcome.result_sha256,
            artifact_links=outcome.artifact_links,
        )

    def to_dict(self, artifact_schema_version: str = ARTIFACT_SCHEMA_VERSION) -> dict[str, Any]:
        result = {
            "caseId": self.case_id, "caseContentSha256": self.case_content_sha256,
            "tier": self.tier.value, "status": self.status.value, "latencyMs": self.latency_ms, "providerCallCount": self.provider_call_count,
            "schemaValid": self.schema_valid, "providerId": self.provider_id,
            "modelId": self.model_id, "modelVersion": self.model_version,
            "promptTokens": self.prompt_tokens, "completionTokens": self.completion_tokens,
            "totalTokens": self.total_tokens, "costState": self.cost_state.value,
            "costTotal": self.cost_total, "costCurrency": self.cost_currency,
            "failureCode": self.failure_code, "resultSha256": self.result_sha256,
        }
        if artifact_schema_version in {ARTIFACT_SCHEMA_VERSION, PREVIOUS_ARTIFACT_SCHEMA_VERSION}:
            result["artifactLinks"] = _thaw(self.artifact_links) if self.artifact_links else None
        return result

    @classmethod
    def from_dict(cls, value: Any, artifact_schema_version: str = ARTIFACT_SCHEMA_VERSION) -> "CaseResult":
        required = {
            "caseId", "caseContentSha256", "tier", "status", "latencyMs", "providerCallCount", "schemaValid",
            "providerId", "modelId", "modelVersion", "promptTokens", "completionTokens", "totalTokens",
            "costState", "costTotal", "costCurrency", "failureCode", "resultSha256",
        }
        if artifact_schema_version in {ARTIFACT_SCHEMA_VERSION, PREVIOUS_ARTIFACT_SCHEMA_VERSION}:
            required.add("artifactLinks")
        if not isinstance(value, Mapping) or set(value) != required:
            raise EvaluationError(EvaluationErrorCode.ARTIFACT_INVALID, "case result schema")
        try:
            result = cls(
                case_id=_safe_identifier(value["caseId"], EvaluationErrorCode.ARTIFACT_INVALID),
                case_content_sha256=_safe_identifier(value["caseContentSha256"], EvaluationErrorCode.ARTIFACT_INVALID),
                tier=TierLabel(value["tier"]), status=CaseStatus(value["status"]),
                latency_ms=value["latencyMs"], provider_call_count=value["providerCallCount"], schema_valid=value["schemaValid"],
                provider_id=value["providerId"], model_id=value["modelId"], model_version=value["modelVersion"],
                prompt_tokens=value["promptTokens"], completion_tokens=value["completionTokens"], total_tokens=value["totalTokens"],
                cost_state=CostState(value["costState"]), cost_total=value["costTotal"],
                cost_currency=value["costCurrency"], failure_code=value["failureCode"], result_sha256=value["resultSha256"],
                artifact_links=_freeze_artifact_links(value.get("artifactLinks")),
            )
        except (TypeError, ValueError, EvaluationError) as exc:
            raise EvaluationError(EvaluationErrorCode.ARTIFACT_INVALID, "case result values") from exc
        if not isinstance(result.latency_ms, int) or result.latency_ms < 0 or not isinstance(result.provider_call_count, int) or result.provider_call_count < 0 or not isinstance(result.schema_valid, bool):
            raise EvaluationError(EvaluationErrorCode.ARTIFACT_INVALID, "case result primitives")
        return result


@dataclass(frozen=True)
class RunArtifact:
    run: BenchmarkRun
    results: tuple[CaseResult, ...] = ()
    state: RunState = RunState.INCOMPLETE

    def __post_init__(self) -> None:
        object.__setattr__(self, "results", tuple(self.results))
        expected = {case.case_id: case for case in self.run.cases}
        seen: set[str] = set()
        for result in self.results:
            if result.case_id in seen or result.case_id not in expected:
                raise EvaluationError(EvaluationErrorCode.ARTIFACT_INVALID, "unexpected or duplicate result")
            if result.case_content_sha256 != expected[result.case_id].content_sha256:
                raise EvaluationError(EvaluationErrorCode.ARTIFACT_INVALID, "case identity drift")
            seen.add(result.case_id)
        if self.state is RunState.COMPLETE and seen != set(expected):
            raise EvaluationError(EvaluationErrorCode.ARTIFACT_INVALID, "complete artifact omits cases")

    @property
    def artifact_schema_version(self) -> str:
        versions = [_artifact_version_for_links(result.artifact_links) for result in self.results]
        if ARTIFACT_SCHEMA_VERSION in versions:
            return ARTIFACT_SCHEMA_VERSION
        if PREVIOUS_ARTIFACT_SCHEMA_VERSION in versions:
            return PREVIOUS_ARTIFACT_SCHEMA_VERSION
        return LEGACY_ARTIFACT_SCHEMA_VERSION

    def to_dict(self) -> dict[str, Any]:
        version = self.artifact_schema_version
        return {
            "artifactSchemaVersion": version,
            "run": self.run.identity_payload(),
            "state": self.state.value,
            "results": [result.to_dict(version) for result in self.results],
            "summary": self.summary(),
        }

    def summary(self) -> dict[str, Any]:
        total = len(self.run.cases)
        passed = sum(result.status is CaseStatus.PASSED for result in self.results)
        failed_results = sum(result.status is CaseStatus.FAILED for result in self.results)
        incomplete = total - len(self.results)
        # Missing cases remain visible as failures in the denominator until resume.
        failures_in_denominator = failed_results + incomplete
        known = [result for result in self.results if result.cost_state is CostState.KNOWN]
        unknown = total - len(known)
        currencies = {result.cost_currency for result in known}
        if unknown or len(currencies) != 1:
            total_cost_state = CostState.UNKNOWN
            total_cost = None
            currency = None
        else:
            total_cost_state = CostState.KNOWN
            total_cost = str(sum((Decimal(result.cost_total or "0") for result in known), Decimal("0")))
            currency = next(iter(currencies))
        return {
            "runId": self.run.run_id, "state": self.state.value,
            "totalCases": total, "passedCases": passed, "failedCases": failures_in_denominator,
            "completedFailureCases": failed_results, "incompleteCases": incomplete,
            "providerCallCount": sum(result.provider_call_count for result in self.results), "knownCostCases": len(known),
            "unknownCostCases": unknown, "totalCostState": total_cost_state.value,
            "totalCost": total_cost, "currency": currency,
            "tiers": {tier.value: sum(case.tier is tier for case in self.run.cases) for tier in TierLabel},
        }

    def human_summary(self) -> str:
        summary = self.summary()
        return "\n".join((
            "# Benchmark Run Summary", "",
            f"- Run ID: `{summary['runId']}`", f"- State: `{summary['state']}`",
            f"- Cases: {summary['totalCases']} total / {summary['passedCases']} passed / {summary['failedCases']} failures in denominator",
            f"- Incomplete cases: {summary['incompleteCases']}",
            f"- Provider calls: {summary['providerCallCount']}",
            f"- Cost: {summary['totalCostState']}" + (f" ({summary['totalCost']} {summary['currency']})" if summary["totalCost"] else ""),
            "- Tier B remains empirical evidence only and has no scoring authority.", "",
        ))


def _config_from_identity(value: Any) -> ExperimentConfig:
    required = {"semanticVersion", "executorId", "versionSnapshot", "selectedCaseIds", "tiers", "limit", "options"}
    if not isinstance(value, Mapping) or set(value) != required:
        raise EvaluationError(EvaluationErrorCode.ARTIFACT_INVALID, "config schema")
    try:
        return ExperimentConfig(
            semantic_version=value["semanticVersion"], executor_id=value["executorId"],
            version_snapshot=value["versionSnapshot"], selected_case_ids=tuple(value["selectedCaseIds"]),
            tiers=tuple(value["tiers"]), limit=value["limit"], options=value["options"],
        )
    except (TypeError, EvaluationError) as exc:
        raise EvaluationError(EvaluationErrorCode.ARTIFACT_INVALID, "config values") from exc


def _artifact_from_dict(value: Any, expected_run: BenchmarkRun) -> RunArtifact:
    required = {"artifactSchemaVersion", "run", "state", "results", "summary"}
    version = value.get("artifactSchemaVersion") if isinstance(value, Mapping) else None
    if not isinstance(value, Mapping) or set(value) != required or version not in {
        ARTIFACT_SCHEMA_VERSION, PREVIOUS_ARTIFACT_SCHEMA_VERSION, LEGACY_ARTIFACT_SCHEMA_VERSION,
    }:
        raise EvaluationError(EvaluationErrorCode.ARTIFACT_INVALID, "artifact schema")
    run_value = value["run"]
    if not isinstance(run_value, Mapping) or set(run_value) != {"runId", "config", "caseContentSha256"}:
        raise EvaluationError(EvaluationErrorCode.ARTIFACT_INVALID, "run schema")
    config = _config_from_identity(run_value["config"])
    if run_value["runId"] != expected_run.run_id or config.identity_payload() != expected_run.config.identity_payload() or run_value["caseContentSha256"] != [case.content_sha256 for case in expected_run.cases]:
        raise EvaluationError(EvaluationErrorCode.RESUME_DRIFT, "case or config drift")
    if not isinstance(value["results"], list):
        raise EvaluationError(EvaluationErrorCode.ARTIFACT_INVALID, "results must be a list")
    try:
        artifact = RunArtifact(expected_run, tuple(CaseResult.from_dict(item, version) for item in value["results"]), RunState(value["state"]))
    except (TypeError, ValueError, EvaluationError) as exc:
        if isinstance(exc, EvaluationError):
            raise
        raise EvaluationError(EvaluationErrorCode.ARTIFACT_INVALID, "artifact values") from exc
    if value["summary"] != artifact.summary():
        raise EvaluationError(EvaluationErrorCode.ARTIFACT_INVALID, "summary does not match results")
    return artifact


class ArtifactStore:
    """Atomic local artifact persistence.  Artifacts are always keyed by run ID."""

    def __init__(self, root: Path) -> None:
        self.root = Path(root)

    def json_path(self, run: BenchmarkRun) -> Path:
        return self.root / f"{run.run_id}.json"

    def summary_path(self, run: BenchmarkRun) -> Path:
        return self.root / f"{run.run_id}.md"

    def load(self, run: BenchmarkRun) -> RunArtifact | None:
        path = self.json_path(run)
        if not path.exists():
            return None
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise EvaluationError(EvaluationErrorCode.ARTIFACT_INVALID, "cannot read artifact") from exc
        return _artifact_from_dict(value, run)

    def save(self, artifact: RunArtifact) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        path = self.json_path(artifact.run)
        text = json.dumps(artifact.to_dict(), ensure_ascii=False, sort_keys=True, indent=2) + "\n"
        try:
            with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=self.root, delete=False) as handle:
                handle.write(text)
                temporary = Path(handle.name)
            os.replace(temporary, path)
            self.summary_path(artifact.run).write_text(artifact.human_summary(), encoding="utf-8")
        except OSError as exc:
            try:
                temporary.unlink(missing_ok=True)  # type: ignore[has-type]
            except (OSError, UnboundLocalError):
                pass
            raise EvaluationError(EvaluationErrorCode.ARTIFACT_INVALID, "cannot save artifact") from exc


class EvaluationRunner:
    """Execute or resume one immutable plan without contacting a provider itself."""

    def __init__(self, executor: BenchmarkExecutor, pricing: Mapping[tuple[str, str, str], ModelPricing] | None = None) -> None:
        self._executor = executor
        self._pricing = dict(pricing or {})

    def _case_result(self, case: BenchmarkCase, config: ExperimentConfig) -> CaseResult:
        started = perf_counter()
        try:
            outcome = self._executor.execute(case, config)
            if not isinstance(outcome, ExecutionOutcome):
                return CaseResult.failed(case, "MALFORMED_RESULT", int((perf_counter() - started) * 1000))
            pricing = self._pricing.get((outcome.provider_id, outcome.model_id, outcome.model_version))
            cost = UsageCost.from_usage(outcome.usage, pricing)
            return CaseResult.from_outcome(case, outcome, cost)
        except EvaluationError:
            return CaseResult.failed(case, "MALFORMED_RESULT", int((perf_counter() - started) * 1000))
        except Exception:
            return CaseResult.failed(case, "EXECUTOR_FAILURE", int((perf_counter() - started) * 1000))

    def execute(self, run: BenchmarkRun, store: ArtifactStore, max_new_cases: int | None = None) -> RunArtifact:
        if max_new_cases is not None and (not isinstance(max_new_cases, int) or max_new_cases <= 0):
            raise EvaluationError(EvaluationErrorCode.INVALID_CONFIG, "max_new_cases must be positive")
        artifact = store.load(run) or RunArtifact(run)
        existing = {result.case_id for result in artifact.results}
        results = list(artifact.results)
        processed = 0
        for case in run.cases:
            if case.case_id in existing:
                continue
            if max_new_cases is not None and processed >= max_new_cases:
                incomplete = RunArtifact(run, tuple(results), RunState.INCOMPLETE)
                store.save(incomplete)
                return incomplete
            result = self._case_result(case, run.config)
            results.append(result)
            processed += 1
            store.save(RunArtifact(run, tuple(results), RunState.INCOMPLETE))
        completed = RunArtifact(run, tuple(results), RunState.COMPLETE)
        store.save(completed)
        return completed


@dataclass(frozen=True)
class RunComparison:
    comparison_identity: str
    comparable: bool
    reason: str | None
    left_run_id: str
    right_run_id: str
    left_summary: Mapping[str, Any]
    right_summary: Mapping[str, Any]


def compare_runs(left: RunArtifact, right: RunArtifact) -> RunComparison:
    """Compare only equivalent run plans; no ranking or architecture recommendation."""
    left_plan = left.run.identity_payload()
    right_plan = right.run.identity_payload()
    left_identity = _digest({"config": left_plan["config"], "cases": left_plan["caseContentSha256"]})
    right_identity = _digest({"config": right_plan["config"], "cases": right_plan["caseContentSha256"]})
    comparable = left_identity == right_identity
    return RunComparison(
        comparison_identity=left_identity if comparable else _digest({"left": left_identity, "right": right_identity}),
        comparable=comparable,
        reason=None if comparable else "NON_COMPARABLE_RUN_PLANS",
        left_run_id=left.run.run_id, right_run_id=right.run.run_id,
        left_summary=MappingProxyType(left.summary()), right_summary=MappingProxyType(right.summary()),
    )


class FixtureExecutor:
    """Local-only deterministic executor used by tests and the CLI fixture mode."""

    def execute(self, case: BenchmarkCase, config: ExperimentConfig) -> ExecutionOutcome:
        fixture = case.payload.get("fixtureOutcome", "success")
        latency = case.payload.get("latencyMs", 1)
        if not isinstance(latency, int) or latency < 0:
            raise EvaluationError(EvaluationErrorCode.ARTIFACT_INVALID, "fixture latency")
        if fixture == "malformed":
            return None  # type: ignore[return-value]
        if fixture == "schema_failure":
            return ExecutionOutcome(False, latency_ms=latency, result={"fixture": fixture})
        if fixture == "provider_failure":
            return ExecutionOutcome(True, latency_ms=latency, failure_code="FIXTURE_PROVIDER_FAILURE", result={"fixture": fixture})
        usage: ProviderUsage | None
        if fixture == "missing_usage":
            usage = None
        elif fixture == "success":
            usage = ProviderUsage(prompt_tokens=11, completion_tokens=7, total_tokens=18)
        else:
            raise EvaluationError(EvaluationErrorCode.ARTIFACT_INVALID, "unknown fixture outcome")
        return ExecutionOutcome(
            True, usage=usage, latency_ms=latency,
            result={"fixture": fixture, "caseFingerprint": case.content_sha256},
        )
