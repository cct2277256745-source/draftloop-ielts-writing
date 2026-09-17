"""Offline, fail-closed metrics and Phase 1 evaluation-gate contracts.

This module evaluates benchmark evidence, not IELTS writing quality.  It stores
only identities, aggregates, and typed outcomes; Candidate Scripts, Provider
bodies, evidence text, and reviewer identity never enter a report or decision.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation, ROUND_CEILING
from enum import Enum
import hashlib
import json
import os
from pathlib import Path
import tempfile
from types import MappingProxyType
from typing import Any, Iterable, Mapping, Protocol

from jsonschema import Draft202012Validator

from .benchmark_registry import (
    BenchmarkRegistry,
    ExpectedCheckKind,
    RegistryValidationError,
    load_registry,
    registry_gate,
)
from .evaluation import (
    ArtifactStore,
    BenchmarkRun,
    CaseStatus,
    EvaluationError,
    EvaluationRunner,
    ExperimentConfig,
    FixtureExecutor,
    RunArtifact,
    RunState,
)
from .observability import CostState


GATE_SCHEMA_VERSION = "evaluation-gate-v1"
DEFAULT_GATE_ROOT = Path(__file__).resolve().parents[1] / "resources" / "evaluation_gate" / "v1"
_SHA256_LENGTH = 64
_FORBIDDEN_KEYS = {
    "candidate_script", "customer_text", "essay", "essay_text", "raw_response",
    "reviewer_name", "human_score", "awarded_band", "examiner_score", "qwk", "mae",
}
_FORBIDDEN_MARKERS = (
    "synthetic_reference/", "ielts_band_evidence_corpus/", "app/resources/corpus/",
    "/users/", "\\users\\", "bearer ", "api_key=",
)


class GateErrorCode(str, Enum):
    INVALID_SCHEMA = "INVALID_SCHEMA"
    INVALID_POLICY = "INVALID_POLICY"
    UNKNOWN_METRIC = "UNKNOWN_METRIC"
    DUPLICATE_METRIC = "DUPLICATE_METRIC"
    INVALID_UNIT = "INVALID_UNIT"
    PROVENANCE_INVALID = "PROVENANCE_INVALID"
    ARTIFACT_INVALID = "ARTIFACT_INVALID"
    HASH_MISMATCH = "HASH_MISMATCH"
    NON_COMPARABLE_RUNS = "NON_COMPARABLE_RUNS"
    RIGHTS_BLOCKED = "RIGHTS_BLOCKED"
    PRIVATE_DATA_FORBIDDEN = "PRIVATE_DATA_FORBIDDEN"
    DECISION_INVALID = "DECISION_INVALID"


class EvaluationGateError(ValueError):
    def __init__(self, code: GateErrorCode, message: str = "") -> None:
        self.code = code
        super().__init__(message or code.value)


class MetricId(str, Enum):
    SCORE_STABILITY = "SCORE_STABILITY"
    EVIDENCE_VALIDITY = "EVIDENCE_VALIDITY"
    SCHEMA_COMPLETION = "SCHEMA_COMPLETION"
    FAILURE_RATE = "FAILURE_RATE"
    LATENCY = "LATENCY"
    COST = "COST"
    RIGHTS_COMPLIANCE = "RIGHTS_COMPLIANCE"
    EXPECTED_CHECK_CONFORMANCE = "EXPECTED_CHECK_CONFORMANCE"


class MetricState(str, Enum):
    PASS = "PASS"
    FAIL = "FAIL"
    MISSING = "MISSING"
    UNKNOWN = "UNKNOWN"
    THRESHOLD_NOT_ESTABLISHED = "THRESHOLD_NOT_ESTABLISHED"
    NOT_EVALUATED = "NOT_EVALUATED"
    NOT_APPLICABLE = "NOT_APPLICABLE"


class PolicyRequirement(str, Enum):
    REQUIRED = "REQUIRED"
    DEFERRED = "DEFERRED"
    NOT_APPLICABLE = "NOT_APPLICABLE"


class Comparator(str, Enum):
    GTE = "GTE"
    LTE = "LTE"
    NONE = "NONE"


class GateDecision(str, Enum):
    ACCEPT = "ACCEPT"
    REJECT = "REJECT"
    NEEDS_REVISION = "NEEDS_REVISION"


def _canonical_json(value: Any) -> str:
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise EvaluationGateError(GateErrorCode.INVALID_SCHEMA, "canonical JSON required") from exc


def _digest(value: Any) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def _identifier(value: Any, code: GateErrorCode = GateErrorCode.INVALID_SCHEMA) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > 200:
        raise EvaluationGateError(code, "invalid identifier")
    return value.strip()


def _sha(value: Any, code: GateErrorCode = GateErrorCode.HASH_MISMATCH) -> str:
    value = _identifier(value, code)
    if len(value) != _SHA256_LENGTH or any(char not in "0123456789abcdef" for char in value):
        raise EvaluationGateError(code, "invalid SHA-256")
    return value


def _decimal(value: Any, code: GateErrorCode = GateErrorCode.INVALID_SCHEMA) -> Decimal:
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise EvaluationGateError(code, "invalid decimal") from exc
    if not result.is_finite():
        raise EvaluationGateError(code, "non-finite decimal")
    return result


def _decimal_text(value: Decimal) -> str:
    return format(value.normalize(), "f") if value != 0 else "0"


def _freeze(value: Any) -> Any:
    if isinstance(value, Mapping):
        return MappingProxyType({str(key): _freeze(child) for key, child in sorted(value.items())})
    if isinstance(value, (list, tuple)):
        return tuple(_freeze(child) for child in value)
    if value is None or isinstance(value, (str, int, float, bool)):
        _canonical_json(value)
        return value
    raise EvaluationGateError(GateErrorCode.INVALID_SCHEMA, "unsupported JSON value")


def _thaw(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {key: _thaw(child) for key, child in value.items()}
    if isinstance(value, tuple):
        return [_thaw(child) for child in value]
    return value


def _assert_safe(value: Any) -> None:
    if isinstance(value, Mapping):
        for key, child in value.items():
            normalized = str(key).lower().strip()
            if normalized in _FORBIDDEN_KEYS or "candidate" in normalized or "customer" in normalized:
                raise EvaluationGateError(GateErrorCode.PRIVATE_DATA_FORBIDDEN, "unsafe metadata key")
            _assert_safe(child)
    elif isinstance(value, (list, tuple)):
        for child in value:
            _assert_safe(child)
    elif isinstance(value, str):
        normalized = value.lower().replace("\\", "/")
        if any(marker in normalized for marker in _FORBIDDEN_MARKERS):
            raise EvaluationGateError(GateErrorCode.PRIVATE_DATA_FORBIDDEN, "unsafe metadata value")


def _read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise EvaluationGateError(GateErrorCode.INVALID_SCHEMA, "cannot read JSON") from exc


@dataclass(frozen=True)
class MetricDefinition:
    metric_id: MetricId | str
    unit: str
    primary_key: str
    description: str

    def __post_init__(self) -> None:
        try:
            metric_id = MetricId(self.metric_id)
        except ValueError as exc:
            raise EvaluationGateError(GateErrorCode.UNKNOWN_METRIC, "unknown metric") from exc
        allowed = {"ratio", "milliseconds", "currency", "decimal", "boolean"}
        unit = _identifier(self.unit, GateErrorCode.INVALID_UNIT)
        if unit not in allowed:
            raise EvaluationGateError(GateErrorCode.INVALID_UNIT, "unknown unit")
        object.__setattr__(self, "metric_id", metric_id)
        object.__setattr__(self, "unit", unit)
        object.__setattr__(self, "primary_key", _identifier(self.primary_key))
        object.__setattr__(self, "description", _identifier(self.description))

    def to_dict(self) -> dict[str, str]:
        return {"metricId": self.metric_id.value, "unit": self.unit, "primaryKey": self.primary_key, "description": self.description}


@dataclass(frozen=True)
class ThresholdRule:
    metric_id: MetricId | str
    requirement: PolicyRequirement | str
    comparator: Comparator | str
    threshold: str | None
    unit: str
    provenance_id: str

    def __post_init__(self) -> None:
        try:
            metric_id = MetricId(self.metric_id)
            requirement = PolicyRequirement(self.requirement)
            comparator = Comparator(self.comparator)
        except ValueError as exc:
            raise EvaluationGateError(GateErrorCode.INVALID_POLICY, "policy enum") from exc
        threshold = None if self.threshold is None else _decimal_text(_decimal(self.threshold, GateErrorCode.INVALID_POLICY))
        if comparator is Comparator.NONE and threshold is not None:
            raise EvaluationGateError(GateErrorCode.INVALID_POLICY, "none comparator cannot have threshold")
        if comparator is not Comparator.NONE and threshold is None and requirement is PolicyRequirement.REQUIRED:
            raise EvaluationGateError(GateErrorCode.INVALID_POLICY, "required threshold missing")
        if requirement is PolicyRequirement.NOT_APPLICABLE and (comparator is not Comparator.NONE or threshold is not None):
            raise EvaluationGateError(GateErrorCode.INVALID_POLICY, "not-applicable rule cannot compare")
        object.__setattr__(self, "metric_id", metric_id)
        object.__setattr__(self, "requirement", requirement)
        object.__setattr__(self, "comparator", comparator)
        object.__setattr__(self, "threshold", threshold)
        object.__setattr__(self, "unit", _identifier(self.unit, GateErrorCode.INVALID_UNIT))
        object.__setattr__(self, "provenance_id", _identifier(self.provenance_id, GateErrorCode.PROVENANCE_INVALID))

    def to_dict(self) -> dict[str, Any]:
        return {
            "metricId": self.metric_id.value, "requirement": self.requirement.value,
            "comparator": self.comparator.value, "threshold": self.threshold,
            "unit": self.unit, "provenanceId": self.provenance_id,
        }


@dataclass(frozen=True)
class ThresholdPolicy:
    policy_id: str
    semantic_version: str
    dictionary_sha256: str
    rules: tuple[ThresholdRule, ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "policy_id", _identifier(self.policy_id, GateErrorCode.INVALID_POLICY))
        object.__setattr__(self, "semantic_version", _identifier(self.semantic_version, GateErrorCode.INVALID_POLICY))
        object.__setattr__(self, "dictionary_sha256", _sha(self.dictionary_sha256, GateErrorCode.PROVENANCE_INVALID))
        rules = tuple(sorted(self.rules, key=lambda item: item.metric_id.value))
        if len(rules) != len(MetricId) or {item.metric_id for item in rules} != set(MetricId):
            raise EvaluationGateError(GateErrorCode.INVALID_POLICY, "complete metric policy required")
        if len({item.metric_id for item in rules}) != len(rules):
            raise EvaluationGateError(GateErrorCode.DUPLICATE_METRIC, "duplicate policy metric")
        object.__setattr__(self, "rules", rules)

    def identity_payload(self) -> dict[str, Any]:
        return {
            "schemaVersion": GATE_SCHEMA_VERSION, "policyId": self.policy_id,
            "semanticVersion": self.semantic_version, "dictionarySha256": self.dictionary_sha256,
            "rules": [item.to_dict() for item in self.rules],
        }

    @property
    def content_sha256(self) -> str:
        return _digest(self.identity_payload())

    def rule_for(self, metric_id: MetricId) -> ThresholdRule:
        return next(rule for rule in self.rules if rule.metric_id is metric_id)


@dataclass(frozen=True)
class ScoreObservation:
    run_id: str
    case_id: str
    repetition_id: str
    task_type: str
    criterion_scores: Mapping[str, str]
    overall_score: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "run_id", _identifier(self.run_id))
        object.__setattr__(self, "case_id", _identifier(self.case_id))
        object.__setattr__(self, "repetition_id", _identifier(self.repetition_id))
        task_type = _identifier(self.task_type)
        expected = {"task1": {"TA", "CC", "LR", "GRA"}, "task2": {"TR", "CC", "LR", "GRA"}}
        if task_type not in expected or not isinstance(self.criterion_scores, Mapping) or not self.criterion_scores:
            raise EvaluationGateError(GateErrorCode.INVALID_SCHEMA, "score observation shape")
        if not set(self.criterion_scores) <= expected[task_type]:
            raise EvaluationGateError(GateErrorCode.NON_COMPARABLE_RUNS, "task criterion contamination")
        scores = {key: _decimal_text(_decimal(value)) for key, value in self.criterion_scores.items()}
        overall = None if self.overall_score is None else _decimal_text(_decimal(self.overall_score))
        object.__setattr__(self, "task_type", task_type)
        object.__setattr__(self, "criterion_scores", MappingProxyType(dict(sorted(scores.items()))))
        object.__setattr__(self, "overall_score", overall)


class EvidenceLocatorValidator(Protocol):
    def validate(self, *, case_id: str, task_type: str, criterion: str, locator_id: str, text: str, start: int, end: int) -> "EvidenceLocatorCheck":
        """Validate transient text without persisting it."""


@dataclass(frozen=True)
class EvidenceLocatorCheck:
    run_id: str
    case_id: str
    task_type: str
    criterion: str
    locator_id: str
    required: bool
    valid: bool | None
    reason_code: str | None
    locator_sha256: str

    def __post_init__(self) -> None:
        for field_name in ("run_id", "case_id", "task_type", "criterion", "locator_id"):
            object.__setattr__(self, field_name, _identifier(getattr(self, field_name)))
        if not isinstance(self.required, bool) or self.valid is not None and not isinstance(self.valid, bool):
            raise EvaluationGateError(GateErrorCode.INVALID_SCHEMA, "locator state")
        if self.valid is True and self.reason_code is not None:
            raise EvaluationGateError(GateErrorCode.INVALID_SCHEMA, "valid locator cannot have failure")
        if self.valid is False and self.reason_code is None:
            raise EvaluationGateError(GateErrorCode.INVALID_SCHEMA, "invalid locator needs reason")
        if self.reason_code is not None:
            object.__setattr__(self, "reason_code", _identifier(self.reason_code))
        object.__setattr__(self, "locator_sha256", _sha(self.locator_sha256))

    @classmethod
    def from_transient_text(cls, *, run_id: str, case_id: str, task_type: str, criterion: str, locator_id: str, required: bool, text: str, start: int, end: int) -> "EvidenceLocatorCheck":
        _assert_safe({"textDigest": _digest(text)})
        if not isinstance(start, int) or not isinstance(end, int) or start < 0 or end < start or end > len(text):
            valid, reason = False, "LOCATOR_OUT_OF_BOUNDS"
            excerpt = ""
        else:
            valid, reason = True, None
            excerpt = text[start:end]
        return cls(run_id, case_id, task_type, criterion, locator_id, required, valid, reason, _digest({"locatorId": locator_id, "excerpt": excerpt}))


@dataclass(frozen=True)
class BlindReviewMetadata:
    review_id: str
    case_id: str
    blinded: bool
    reviewer_alias_sha256: str
    review_kind: str
    decision_code: str

    def __post_init__(self) -> None:
        if not self.blinded:
            raise EvaluationGateError(GateErrorCode.PRIVATE_DATA_FORBIDDEN, "review must remain blinded")
        for field_name in ("review_id", "case_id", "review_kind", "decision_code"):
            object.__setattr__(self, field_name, _identifier(getattr(self, field_name)))
        if self.review_kind not in {"EVIDENCE", "BAD_CASE"}:
            raise EvaluationGateError(GateErrorCode.INVALID_SCHEMA, "review kind")
        if any(marker in self.decision_code.upper() for marker in ("SCORE", "EXAMINER", "HUMAN_VERIFIED")):
            raise EvaluationGateError(GateErrorCode.DECISION_INVALID, "human scoring claim")
        object.__setattr__(self, "reviewer_alias_sha256", _sha(self.reviewer_alias_sha256))


@dataclass(frozen=True)
class MetricResult:
    metric_id: MetricId | str
    state: MetricState | str
    unit: str
    values: Mapping[str, Any]
    definition_sha256: str
    note_code: str | None = None

    def __post_init__(self) -> None:
        try:
            metric_id, state = MetricId(self.metric_id), MetricState(self.state)
        except ValueError as exc:
            raise EvaluationGateError(GateErrorCode.UNKNOWN_METRIC, "invalid metric result") from exc
        _assert_safe(self.values)
        object.__setattr__(self, "metric_id", metric_id)
        object.__setattr__(self, "state", state)
        object.__setattr__(self, "unit", _identifier(self.unit, GateErrorCode.INVALID_UNIT))
        object.__setattr__(self, "values", _freeze(self.values))
        object.__setattr__(self, "definition_sha256", _sha(self.definition_sha256, GateErrorCode.PROVENANCE_INVALID))
        if self.note_code is not None:
            object.__setattr__(self, "note_code", _identifier(self.note_code))

    def to_dict(self) -> dict[str, Any]:
        return {"metricId": self.metric_id.value, "state": self.state.value, "unit": self.unit, "values": _thaw(self.values), "definitionSha256": self.definition_sha256, "noteCode": self.note_code}


@dataclass(frozen=True)
class MetricReport:
    run_artifact_sha256: str
    registry_sha256: str
    policy_sha256: str
    metric_results: tuple[MetricResult, ...]
    run_id: str

    def __post_init__(self) -> None:
        for field_name in ("run_artifact_sha256", "registry_sha256", "policy_sha256"):
            object.__setattr__(self, field_name, _sha(getattr(self, field_name), GateErrorCode.PROVENANCE_INVALID))
        object.__setattr__(self, "run_id", _identifier(self.run_id))
        results = tuple(sorted(self.metric_results, key=lambda item: item.metric_id.value))
        if len(results) != len(MetricId) or {item.metric_id for item in results} != set(MetricId):
            raise EvaluationGateError(GateErrorCode.INVALID_SCHEMA, "complete metric report required")
        object.__setattr__(self, "metric_results", results)

    def identity_payload(self) -> dict[str, Any]:
        return {"schemaVersion": GATE_SCHEMA_VERSION, "runId": self.run_id, "runArtifactSha256": self.run_artifact_sha256, "registrySha256": self.registry_sha256, "policySha256": self.policy_sha256, "metrics": [item.to_dict() for item in self.metric_results]}

    @property
    def content_sha256(self) -> str:
        return _digest(self.identity_payload())

    def result_for(self, metric_id: MetricId) -> MetricResult:
        return next(result for result in self.metric_results if result.metric_id is metric_id)

    def to_dict(self) -> dict[str, Any]:
        result = self.identity_payload()
        result["reportSha256"] = self.content_sha256
        return result


@dataclass(frozen=True)
class GateDecisionArtifact:
    report_sha256: str
    policy_sha256: str
    decision: GateDecision | str
    claim_scope: str
    acceptance: str | None
    blockers: tuple[str, ...] = ()
    gaps: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "report_sha256", _sha(self.report_sha256, GateErrorCode.PROVENANCE_INVALID))
        object.__setattr__(self, "policy_sha256", _sha(self.policy_sha256, GateErrorCode.PROVENANCE_INVALID))
        try:
            decision = GateDecision(self.decision)
        except ValueError as exc:
            raise EvaluationGateError(GateErrorCode.DECISION_INVALID, "invalid gate decision") from exc
        claim_scope = _identifier(self.claim_scope, GateErrorCode.DECISION_INVALID)
        if claim_scope != "EVALUATION_INFRASTRUCTURE_ONLY":
            raise EvaluationGateError(GateErrorCode.DECISION_INVALID, "unsupported claim scope")
        acceptance = None if self.acceptance is None else _identifier(self.acceptance, GateErrorCode.DECISION_INVALID)
        if decision is GateDecision.ACCEPT and acceptance != "PHASE_1_ACCEPT":
            raise EvaluationGateError(GateErrorCode.DECISION_INVALID, "acceptance label required")
        if decision is not GateDecision.ACCEPT and acceptance is not None:
            raise EvaluationGateError(GateErrorCode.DECISION_INVALID, "non-accept cannot claim phase acceptance")
        object.__setattr__(self, "decision", decision)
        object.__setattr__(self, "claim_scope", claim_scope)
        object.__setattr__(self, "acceptance", acceptance)
        object.__setattr__(self, "blockers", tuple(sorted({_identifier(item) for item in self.blockers})))
        object.__setattr__(self, "gaps", tuple(sorted({_identifier(item) for item in self.gaps})))

    def identity_payload(self) -> dict[str, Any]:
        return {"schemaVersion": GATE_SCHEMA_VERSION, "reportSha256": self.report_sha256, "policySha256": self.policy_sha256, "decision": self.decision.value, "claimScope": self.claim_scope, "acceptance": self.acceptance, "blockers": list(self.blockers), "gaps": list(self.gaps)}

    @property
    def content_sha256(self) -> str:
        return _digest(self.identity_payload())

    def to_dict(self) -> dict[str, Any]:
        result = self.identity_payload()
        result["decisionSha256"] = self.content_sha256
        return result


def _definitions_from_value(value: Any) -> tuple[MetricDefinition, ...]:
    required = {"schemaVersion", "metrics"}
    if not isinstance(value, Mapping) or set(value) != required or value["schemaVersion"] != GATE_SCHEMA_VERSION or not isinstance(value["metrics"], list):
        raise EvaluationGateError(GateErrorCode.INVALID_SCHEMA, "metric dictionary schema")
    definitions = tuple(MetricDefinition(item.get("metricId"), item.get("unit"), item.get("primaryKey"), item.get("description")) for item in value["metrics"] if isinstance(item, Mapping))
    if len(definitions) != len(value["metrics"]) or len(definitions) != len(MetricId) or {item.metric_id for item in definitions} != set(MetricId):
        raise EvaluationGateError(GateErrorCode.DUPLICATE_METRIC, "complete unique metric dictionary required")
    return tuple(sorted(definitions, key=lambda item: item.metric_id.value))


def load_definitions(root: Path = DEFAULT_GATE_ROOT) -> tuple[MetricDefinition, ...]:
    root = Path(root)
    schema, value = _read_json(root / "metric_dictionary.schema.json"), _read_json(root / "metric_dictionary.json")
    try:
        Draft202012Validator.check_schema(schema)
        if list(Draft202012Validator(schema).iter_errors(value)):
            raise EvaluationGateError(GateErrorCode.INVALID_SCHEMA, "metric dictionary JSON schema")
    except EvaluationGateError:
        raise
    except Exception as exc:
        raise EvaluationGateError(GateErrorCode.INVALID_SCHEMA, "invalid metric dictionary schema") from exc
    return _definitions_from_value(value)


def definitions_sha256(definitions: Iterable[MetricDefinition]) -> str:
    return _digest({"schemaVersion": GATE_SCHEMA_VERSION, "metrics": [item.to_dict() for item in sorted(definitions, key=lambda item: item.metric_id.value)]})


def load_policy(root: Path = DEFAULT_GATE_ROOT) -> ThresholdPolicy:
    root = Path(root)
    schema, value = _read_json(root / "policy.schema.json"), _read_json(root / "phase1-foundation-v1.json")
    try:
        Draft202012Validator.check_schema(schema)
        if list(Draft202012Validator(schema).iter_errors(value)):
            raise EvaluationGateError(GateErrorCode.INVALID_POLICY, "policy JSON schema")
    except EvaluationGateError:
        raise
    except Exception as exc:
        raise EvaluationGateError(GateErrorCode.INVALID_POLICY, "invalid policy schema") from exc
    _assert_safe(value)
    required = {"schemaVersion", "policyId", "semanticVersion", "dictionarySha256", "rules"}
    if not isinstance(value, Mapping) or set(value) != required or value["schemaVersion"] != GATE_SCHEMA_VERSION or not isinstance(value["rules"], list):
        raise EvaluationGateError(GateErrorCode.INVALID_POLICY, "policy envelope")
    definitions = load_definitions(root)
    if value["dictionarySha256"] != definitions_sha256(definitions):
        raise EvaluationGateError(GateErrorCode.PROVENANCE_INVALID, "metric dictionary hash")
    rules = []
    for item in value["rules"]:
        if not isinstance(item, Mapping) or set(item) != {"metricId", "requirement", "comparator", "threshold", "unit", "provenanceId"}:
            raise EvaluationGateError(GateErrorCode.INVALID_POLICY, "policy rule schema")
        rules.append(ThresholdRule(item["metricId"], item["requirement"], item["comparator"], item["threshold"], item["unit"], item["provenanceId"]))
    return ThresholdPolicy(value["policyId"], value["semanticVersion"], value["dictionarySha256"], tuple(rules))


def _definition_map(definitions: Iterable[MetricDefinition]) -> dict[MetricId, MetricDefinition]:
    result = {item.metric_id: item for item in definitions}
    if set(result) != set(MetricId):
        raise EvaluationGateError(GateErrorCode.INVALID_SCHEMA, "incomplete metric definitions")
    return result


def _apply_rule(definition: MetricDefinition, rule: ThresholdRule, values: Mapping[str, Any], raw_state: MetricState, note: str | None = None) -> MetricResult:
    if definition.unit != rule.unit:
        raise EvaluationGateError(GateErrorCode.INVALID_POLICY, "metric unit differs from policy")
    if rule.requirement is PolicyRequirement.NOT_APPLICABLE:
        state = MetricState.NOT_APPLICABLE
    elif raw_state in {MetricState.NOT_EVALUATED, MetricState.UNKNOWN, MetricState.MISSING}:
        state = raw_state
    elif rule.comparator is Comparator.NONE or rule.threshold is None:
        state = MetricState.THRESHOLD_NOT_ESTABLISHED
    else:
        primary = values.get("primaryValue")
        if primary is None:
            state = MetricState.MISSING
        else:
            value, threshold = _decimal(primary), _decimal(rule.threshold)
            state = MetricState.PASS if (value >= threshold if rule.comparator is Comparator.GTE else value <= threshold) else MetricState.FAIL
    return MetricResult(definition.metric_id, state, definition.unit, values, _digest(definition.to_dict()), note)


def _rate(numerator: int, denominator: int) -> str:
    return _decimal_text(Decimal(numerator) / Decimal(denominator)) if denominator else "0"


def _latency_percentile(values: list[int], percentile: Decimal) -> int | None:
    if not values:
        return None
    ordered = sorted(values)
    rank = int((percentile * len(ordered)).to_integral_value(rounding=ROUND_CEILING))
    return ordered[rank - 1]


class EvaluationGate:
    """Compute versioned metric evidence and make only policy-supported decisions."""

    def __init__(self, definitions: Iterable[MetricDefinition], policy: ThresholdPolicy) -> None:
        self._definitions = _definition_map(definitions)
        if definitions_sha256(self._definitions.values()) != policy.dictionary_sha256:
            raise EvaluationGateError(GateErrorCode.PROVENANCE_INVALID, "policy dictionary provenance")
        self._policy = policy

    @property
    def policy(self) -> ThresholdPolicy:
        return self._policy

    def metric_report(self, artifact: RunArtifact, registry: BenchmarkRegistry, *, score_observations: Iterable[ScoreObservation] = (), locator_checks: Iterable[EvidenceLocatorCheck] = ()) -> MetricReport:
        if not isinstance(artifact, RunArtifact) or not isinstance(registry, BenchmarkRegistry):
            raise EvaluationGateError(GateErrorCode.ARTIFACT_INVALID, "invalid gate inputs")
        try:
            artifact_value = artifact.to_dict()
        except (EvaluationError, ValueError) as exc:
            raise EvaluationGateError(GateErrorCode.ARTIFACT_INVALID, "run artifact is invalid") from exc
        report_results = self._compute(artifact, registry, tuple(score_observations), tuple(locator_checks))
        return MetricReport(_digest(artifact_value), registry.content_sha256, self._policy.content_sha256, tuple(report_results), artifact.run.run_id)

    def _compute(self, artifact: RunArtifact, registry: BenchmarkRegistry, scores: tuple[ScoreObservation, ...], locators: tuple[EvidenceLocatorCheck, ...]) -> list[MetricResult]:
        total = len(artifact.run.cases)
        results = {item.case_id: item for item in artifact.results}
        definition = self._definitions
        rule = self._policy.rule_for
        schema_valid = sum(item.schema_valid for item in artifact.results)
        schema_values = {"numerator": schema_valid, "denominator": total, "rate": _rate(schema_valid, total), "primaryValue": _rate(schema_valid, total), "incomplete": total - len(artifact.results)}
        failure_count = sum(item.status is CaseStatus.FAILED for item in artifact.results) + (total - len(artifact.results))
        failure_values = {"numerator": failure_count, "denominator": total, "rate": _rate(failure_count, total), "primaryValue": _rate(failure_count, total), "completedFailures": sum(item.status is CaseStatus.FAILED for item in artifact.results), "incomplete": total - len(artifact.results)}
        latencies = [item.latency_ms for item in artifact.results]
        latency_values = {"observed": len(latencies), "missing": total - len(latencies), "p50Ms": _latency_percentile(latencies, Decimal("0.50")), "p95Ms": _latency_percentile(latencies, Decimal("0.95")), "maxMs": max(latencies) if latencies else None, "primaryValue": str(_latency_percentile(latencies, Decimal("0.95"))) if latencies else None}
        known = [item for item in artifact.results if item.cost_state is CostState.KNOWN]
        currencies = {item.cost_currency for item in known}
        costs_known = len(known) == total and total > 0 and len(currencies) == 1
        if costs_known:
            total_cost = sum((_decimal(item.cost_total or "0") for item in known), Decimal("0"))
            cost_values, cost_state = {"known": total, "unknown": 0, "currency": next(iter(currencies)), "total": _decimal_text(total_cost), "perPlannedCase": _decimal_text(total_cost / Decimal(total)), "primaryValue": _decimal_text(total_cost / Decimal(total))}, MetricState.THRESHOLD_NOT_ESTABLISHED
        else:
            cost_values, cost_state = {"known": len(known), "unknown": total - len(known), "currency": None, "total": None, "perPlannedCase": None, "primaryValue": None}, MetricState.UNKNOWN

        executable = {case.case_id: case for case in registry.executable_cases()}
        rights_ok = registry_gate(registry)["status"] == "BENCHMARK_REGISTRY_PASS" and set(case.case_id for case in artifact.run.cases) <= set(executable)
        for case in artifact.run.cases:
            expected = executable.get(case.case_id)
            if expected is None or case.payload.get("registryIdentity") != registry.content_sha256 or case.payload.get("registryCaseId") != case.case_id:
                rights_ok = False
        rights_values = {"numerator": total if rights_ok else 0, "denominator": total, "rate": _rate(total if rights_ok else 0, total), "primaryValue": _rate(total if rights_ok else 0, total), "tierAExecutable": False}

        deterministic = []
        for case in artifact.run.cases:
            registry_case = next((item for item in registry.cases if item.case_id == case.case_id), None)
            if registry_case is None:
                continue
            observed = results.get(case.case_id)
            for check in registry_case.expected_checks:
                if check.kind is not ExpectedCheckKind.DETERMINISTIC:
                    continue
                if check.check_id == "SCHEMA_FAILURE_REMAINS_VISIBLE":
                    okay = observed is not None and observed.status is CaseStatus.FAILED and observed.failure_code == "SCHEMA_FAILURE"
                elif check.check_id == "TYPED_FAILURE_REMAINS_VISIBLE":
                    okay = observed is not None and observed.status is CaseStatus.FAILED and observed.failure_code == "FIXTURE_PROVIDER_FAILURE"
                else:
                    okay = observed is not None and observed.status is CaseStatus.PASSED
                deterministic.append(okay)
        expected_values = {"numerator": sum(deterministic), "denominator": len(deterministic), "rate": _rate(sum(deterministic), len(deterministic)), "primaryValue": _rate(sum(deterministic), len(deterministic)), "missing": len(deterministic) - len(results)}

        score_values, score_state, score_note = self._score_values(artifact, scores)
        locator_values, locator_state, locator_note = self._locator_values(artifact, locators)
        raw = {
            MetricId.SCORE_STABILITY: (score_values, score_state, score_note),
            MetricId.EVIDENCE_VALIDITY: (locator_values, locator_state, locator_note),
            MetricId.SCHEMA_COMPLETION: (schema_values, MetricState.THRESHOLD_NOT_ESTABLISHED, None),
            MetricId.FAILURE_RATE: (failure_values, MetricState.THRESHOLD_NOT_ESTABLISHED, None),
            MetricId.LATENCY: (latency_values, MetricState.NOT_EVALUATED if not latencies else MetricState.THRESHOLD_NOT_ESTABLISHED, None),
            MetricId.COST: (cost_values, cost_state, "UNKNOWN_COST" if cost_state is MetricState.UNKNOWN else None),
            MetricId.RIGHTS_COMPLIANCE: (rights_values, MetricState.THRESHOLD_NOT_ESTABLISHED, "RIGHTS_BLOCKED" if not rights_ok else None),
            MetricId.EXPECTED_CHECK_CONFORMANCE: (expected_values, MetricState.THRESHOLD_NOT_ESTABLISHED, "EXPECTED_CHECK_MISMATCH" if not all(deterministic) else None),
        }
        return [_apply_rule(definition[metric], rule(metric), values, state, note) for metric, (values, state, note) in raw.items()]

    def _score_values(self, artifact: RunArtifact, observations: tuple[ScoreObservation, ...]) -> tuple[dict[str, Any], MetricState, str | None]:
        if not observations:
            return {"observed": 0, "primaryValue": None}, MetricState.NOT_EVALUATED, "SCORE_IMPLEMENTATION_UNAVAILABLE"
        known_cases = {case.case_id: case for case in artifact.run.cases}
        grouped: dict[str, list[ScoreObservation]] = {}
        seen_repetitions: set[tuple[str, str]] = set()
        for item in observations:
            if item.run_id != artifact.run.run_id or item.case_id not in known_cases or item.task_type != known_cases[item.case_id].task_type:
                raise EvaluationGateError(GateErrorCode.NON_COMPARABLE_RUNS, "score observation differs from run plan")
            key = (item.case_id, item.repetition_id)
            if key in seen_repetitions:
                raise EvaluationGateError(GateErrorCode.DUPLICATE_METRIC, "duplicate score repetition")
            seen_repetitions.add(key)
            grouped.setdefault(item.case_id, []).append(item)
        deltas: list[Decimal] = []
        for case_id, items in grouped.items():
            repetition_ids = {item.repetition_id for item in items}
            if len(repetition_ids) < 2:
                continue
            fields = sorted({key for item in items for key in item.criterion_scores} | ({"overall"} if any(item.overall_score is not None for item in items) else set()))
            for field_name in fields:
                values = [_decimal(item.overall_score) if field_name == "overall" and item.overall_score is not None else _decimal(item.criterion_scores[field_name]) for item in items if (field_name == "overall" and item.overall_score is not None) or field_name in item.criterion_scores]
                if len(values) >= 2:
                    deltas.append(max(values) - min(values))
        if not deltas:
            return {"observed": len(observations), "primaryValue": None}, MetricState.NOT_EVALUATED, "INSUFFICIENT_REPETITIONS"
        maximum = max(deltas)
        return {"observed": len(observations), "comparedDimensions": len(deltas), "maxDelta": _decimal_text(maximum), "primaryValue": _decimal_text(maximum)}, MetricState.THRESHOLD_NOT_ESTABLISHED, None

    def _locator_values(self, artifact: RunArtifact, checks: tuple[EvidenceLocatorCheck, ...]) -> tuple[dict[str, Any], MetricState, str | None]:
        if not checks:
            return {"required": 0, "primaryValue": None}, MetricState.NOT_EVALUATED, "EVIDENCE_IMPLEMENTATION_UNAVAILABLE"
        known_cases = {case.case_id: case for case in artifact.run.cases}
        required = valid = missing = 0
        seen_locators: set[tuple[str, str]] = set()
        for item in checks:
            if item.run_id != artifact.run.run_id or item.case_id not in known_cases or item.task_type != known_cases[item.case_id].task_type:
                raise EvaluationGateError(GateErrorCode.NON_COMPARABLE_RUNS, "locator differs from run plan")
            key = (item.case_id, item.locator_id)
            if key in seen_locators:
                raise EvaluationGateError(GateErrorCode.DUPLICATE_METRIC, "duplicate evidence locator")
            seen_locators.add(key)
            if item.required:
                required += 1
                if item.valid is True:
                    valid += 1
                elif item.valid is None:
                    missing += 1
        return {"numerator": valid, "denominator": required, "missing": missing, "rate": _rate(valid, required), "primaryValue": _rate(valid, required)}, MetricState.THRESHOLD_NOT_ESTABLISHED, None

    def decide(self, report: MetricReport) -> GateDecisionArtifact:
        if report.policy_sha256 != self._policy.content_sha256:
            raise EvaluationGateError(GateErrorCode.PROVENANCE_INVALID, "report policy differs")
        blockers: list[str] = []
        gaps: list[str] = []
        required_gaps: list[str] = []
        for result in report.metric_results:
            rule = self._policy.rule_for(result.metric_id)
            if rule.requirement is not PolicyRequirement.REQUIRED:
                if result.state in {MetricState.UNKNOWN, MetricState.NOT_EVALUATED, MetricState.THRESHOLD_NOT_ESTABLISHED}:
                    gaps.append(f"{result.metric_id.value}:{result.state.value}")
                continue
            if result.state is MetricState.FAIL:
                blockers.append(f"{result.metric_id.value}:THRESHOLD_FAILED")
            elif result.state in {MetricState.MISSING, MetricState.UNKNOWN, MetricState.NOT_EVALUATED, MetricState.THRESHOLD_NOT_ESTABLISHED}:
                gap = f"{result.metric_id.value}:{result.state.value}"
                gaps.append(gap)
                required_gaps.append(gap)
        if blockers:
            decision, acceptance = GateDecision.REJECT, None
        elif required_gaps:
            decision, acceptance = GateDecision.NEEDS_REVISION, None
        else:
            decision, acceptance = GateDecision.ACCEPT, "PHASE_1_ACCEPT"
        return GateDecisionArtifact(report.content_sha256, self._policy.content_sha256, decision, "EVALUATION_INFRASTRUCTURE_ONLY", acceptance, tuple(blockers), tuple(gaps))


class GateArtifactStore:
    """Atomically persist metric and decision artifacts without raw inputs."""

    def __init__(self, root: Path) -> None:
        self.root = Path(root)

    def _write(self, name: str, value: Mapping[str, Any]) -> Path:
        self.root.mkdir(parents=True, exist_ok=True)
        target = self.root / name
        text = json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
        try:
            with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=self.root, delete=False) as handle:
                handle.write(text)
                temporary = Path(handle.name)
            os.replace(temporary, target)
        except OSError as exc:
            try:
                temporary.unlink(missing_ok=True)  # type: ignore[has-type]
            except (OSError, UnboundLocalError):
                pass
            raise EvaluationGateError(GateErrorCode.ARTIFACT_INVALID, "cannot save gate artifact") from exc
        return target

    def save(self, report: MetricReport, decision: GateDecisionArtifact) -> tuple[Path, Path]:
        if decision.report_sha256 != report.content_sha256:
            raise EvaluationGateError(GateErrorCode.HASH_MISMATCH, "decision report hash")
        return self._write("metric-report.json", report.to_dict()), self._write("gate-decision.json", decision.to_dict())


def load_metric_report(value: Any) -> MetricReport:
    """Validate a persisted report before it can support a decision."""
    required = {"schemaVersion", "runId", "runArtifactSha256", "registrySha256", "policySha256", "metrics", "reportSha256"}
    if not isinstance(value, Mapping) or set(value) != required or value.get("schemaVersion") != GATE_SCHEMA_VERSION or not isinstance(value.get("metrics"), list):
        raise EvaluationGateError(GateErrorCode.ARTIFACT_INVALID, "metric report schema")
    parsed: list[MetricResult] = []
    for item in value["metrics"]:
        required_metric = {"metricId", "state", "unit", "values", "definitionSha256", "noteCode"}
        if not isinstance(item, Mapping) or set(item) != required_metric:
            raise EvaluationGateError(GateErrorCode.ARTIFACT_INVALID, "metric result schema")
        parsed.append(MetricResult(item["metricId"], item["state"], item["unit"], item["values"], item["definitionSha256"], item["noteCode"]))
    report = MetricReport(value["runArtifactSha256"], value["registrySha256"], value["policySha256"], tuple(parsed), value["runId"])
    if report.content_sha256 != value["reportSha256"]:
        raise EvaluationGateError(GateErrorCode.HASH_MISMATCH, "metric report hash")
    return report


def load_registry_artifact(path: Path, registry: BenchmarkRegistry) -> RunArtifact:
    """Load a P1-03 artifact only after reconstructing its registry-backed plan."""
    value = _read_json(Path(path))
    if not isinstance(value, Mapping) or not isinstance(value.get("run"), Mapping):
        raise EvaluationGateError(GateErrorCode.ARTIFACT_INVALID, "run artifact envelope")
    run_value = value["run"]
    config_value = run_value.get("config") if isinstance(run_value, Mapping) else None
    required = {"semanticVersion", "executorId", "versionSnapshot", "selectedCaseIds", "tiers", "limit", "options"}
    if not isinstance(config_value, Mapping) or set(config_value) != required:
        raise EvaluationGateError(GateErrorCode.ARTIFACT_INVALID, "run artifact config")
    try:
        config = ExperimentConfig(
            semantic_version=config_value["semanticVersion"], executor_id=config_value["executorId"],
            version_snapshot=config_value["versionSnapshot"], selected_case_ids=tuple(config_value["selectedCaseIds"]),
            tiers=tuple(config_value["tiers"]), limit=config_value["limit"], options=config_value["options"],
        )
        run = BenchmarkRun.create(registry.executable_cases(), config)
        if run_value.get("runId") != run.run_id:
            raise EvaluationGateError(GateErrorCode.HASH_MISMATCH, "run identity")
        artifact = ArtifactStore(Path(path).parent).load(run)
    except EvaluationGateError:
        raise
    except (EvaluationError, TypeError, ValueError) as exc:
        raise EvaluationGateError(GateErrorCode.ARTIFACT_INVALID, "cannot validate run artifact") from exc
    if artifact is None:
        raise EvaluationGateError(GateErrorCode.ARTIFACT_INVALID, "run artifact unavailable")
    return artifact


def foundation_run(output_root: Path, gate_root: Path = DEFAULT_GATE_ROOT) -> tuple[RunArtifact, MetricReport, GateDecisionArtifact]:
    """Execute the fixed P1-04 fixture set and make the infrastructure-only decision."""
    registry = load_registry()
    if registry_gate(registry)["status"] != "BENCHMARK_REGISTRY_PASS":
        raise EvaluationGateError(GateErrorCode.RIGHTS_BLOCKED, "benchmark registry gate")
    definitions, policy = load_definitions(gate_root), load_policy(gate_root)
    gate = EvaluationGate(definitions, policy)
    config = ExperimentConfig(
        semantic_version="p1-05-foundation-v1", executor_id="offline-fixture-v1",
        version_snapshot={"registry": registry.content_sha256, "policy": policy.content_sha256, "gate": GATE_SCHEMA_VERSION},
    )
    run = BenchmarkRun.create(registry.executable_cases(), config)
    artifact = EvaluationRunner(FixtureExecutor()).execute(run, ArtifactStore(Path(output_root) / "runs"))
    if artifact.state is not RunState.COMPLETE:
        raise EvaluationGateError(GateErrorCode.ARTIFACT_INVALID, "foundation run incomplete")
    report = gate.metric_report(artifact, registry)
    decision = gate.decide(report)
    GateArtifactStore(Path(output_root)).save(report, decision)
    return artifact, report, decision
