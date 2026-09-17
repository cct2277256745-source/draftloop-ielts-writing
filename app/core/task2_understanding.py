"""Validated, non-scoring Task 2 understanding before criterion review."""
from __future__ import annotations

from dataclasses import dataclass, field, replace
from enum import Enum
import hashlib
import json
from pathlib import Path
import re
from time import monotonic
from typing import Any, Callable, Mapping

from jsonschema import Draft202012Validator

from .providers import ProviderCallRequest, ProviderCallResult, ProviderContract, ProviderTransport
from .submission import SubmissionSnapshot, canonical_json, digest


UNDERSTANDING_SCHEMA_VERSION = "task2-understanding-output-v1"
UNDERSTANDING_SEMANTIC_VERSION = "task2-understanding-v1"
_ROOT = Path(__file__).resolve().parents[1] / "resources" / "task2_understanding" / "v1"
_SCHEMA_PATH = _ROOT / "task2-understanding-output.schema.json"
_PROMPT_PATH = Path(__file__).resolve().parents[1] / "prompts" / "task2_understanding.md"
_FORBIDDEN_TOKENS = frozenset({
    "score", "band", "rubric", "target", "rewrite", "history", "memory", "teacher", "retrieval", "rag",
})


class Task2UnderstandingError(ValueError):
    """A safe Task 2 understanding boundary error."""


class Task2UnderstandingSchemaError(Task2UnderstandingError):
    """Provider output failed schema, locator, lineage, or authority validation."""


class Task2UnderstandingProviderError(Task2UnderstandingError):
    """The configured Provider did not return a usable understanding response."""


class UnderstandingStatus(str, Enum):
    VALID = "VALID"
    REVIEW_REQUIRED = "REVIEW_REQUIRED"


class AnalyzerUncertainty(str, Enum):
    TASK_TYPE_UNRESOLVED = "TASK_TYPE_UNRESOLVED"
    REQUIREMENTS_UNRESOLVED = "REQUIREMENTS_UNRESOLVED"
    LOCATOR_UNRESOLVED = "LOCATOR_UNRESOLVED"
    LINEAGE_INVALID = "LINEAGE_INVALID"


_BLOCKING_UNCERTAINTIES = frozenset(AnalyzerUncertainty)


@dataclass(frozen=True)
class Task2Understanding:
    """A frozen structural artifact; learner state is deliberately not a score."""

    schema_version: str
    semantic_version: str
    prompt_version: str
    artifact_sha256: str
    status: UnderstandingStatus
    review_reasons: tuple[AnalyzerUncertainty, ...]
    payload: Mapping[str, Any] = field(repr=False, compare=False)
    provider_id: str = ""
    model_id: str = ""
    model_version: str = ""
    route_key: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(self, "review_reasons", tuple(sorted(set(self.review_reasons), key=lambda item: item.value)))

    @property
    def scoreable(self) -> bool:
        return self.status is UnderstandingStatus.VALID

    def main_review_projection(self) -> dict[str, Any]:
        """Safe structure only; raw Candidate Script stays on the request boundary."""
        payload = self.payload
        return {
            "understandingSha256": self.artifact_sha256,
            "schemaVersion": self.schema_version,
            "semanticVersion": self.semantic_version,
            "submissionSnapshotId": payload["submissionSnapshotId"],
            "essayVersionId": payload["essayVersionId"],
            "locatorManifestSha256": payload["locatorManifestSha256"],
            "questionType": payload["questionType"],
            "requirements": payload["requirements"],
            "position": payload["position"],
            "coverage": payload["coverage"],
            "argumentNodes": payload["argumentNodes"],
        }

    def log_safe_lineage(self) -> dict[str, str]:
        payload = self.payload
        return {
            "task2UnderstandingSha256": self.artifact_sha256,
            "submissionSnapshotId": payload["submissionSnapshotId"],
            "essayVersionId": payload["essayVersionId"],
            "locatorManifestSha256": payload["locatorManifestSha256"],
            "understandingPromptVersion": self.prompt_version,
            "understandingSemanticVersion": self.semantic_version,
            "providerId": self.provider_id,
            "modelId": self.model_id,
            "modelVersion": self.model_version,
            "routeKey": self.route_key,
        }


@dataclass(frozen=True)
class UnderstandingOutcome:
    artifact: Task2Understanding

    @property
    def review_required(self) -> bool:
        return not self.artifact.scoreable


def _read_schema() -> dict[str, Any]:
    try:
        value = json.loads(_SCHEMA_PATH.read_text(encoding="utf-8"))
        Draft202012Validator.check_schema(value)
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError) as exc:
        raise Task2UnderstandingSchemaError("Task 2 understanding schema is unavailable.") from exc
    return value


def prompt_text() -> str:
    try:
        return _PROMPT_PATH.read_text(encoding="utf-8")
    except OSError as exc:
        raise Task2UnderstandingSchemaError("Task 2 understanding prompt is unavailable.") from exc


def prompt_version() -> str:
    return "sha256:" + hashlib.sha256(prompt_text().encode("utf-8")).hexdigest()


def _assert_no_forbidden_keys(value: Any) -> None:
    if isinstance(value, Mapping):
        for key, child in value.items():
            words = re.sub(r"([a-z])([A-Z])", r"\1 \2", str(key)).replace("_", " ").replace("-", " ").lower().split()
            if any(word in _FORBIDDEN_TOKENS for word in words):
                raise Task2UnderstandingSchemaError("Task 2 understanding contains a forbidden authority field.")
            _assert_no_forbidden_keys(child)
    elif isinstance(value, list):
        for child in value:
            _assert_no_forbidden_keys(child)


def _parse_object(text: str) -> dict[str, Any]:
    try:
        value = json.loads(text)
    except (TypeError, json.JSONDecodeError) as exc:
        raise Task2UnderstandingSchemaError("Task 2 understanding is not valid JSON.") from exc
    if not isinstance(value, dict):
        raise Task2UnderstandingSchemaError("Task 2 understanding must be a JSON object.")
    return value


def _validate_graph(nodes: list[dict[str, Any]]) -> None:
    identifiers = {node["nodeId"] for node in nodes}
    if len(identifiers) != len(nodes):
        raise Task2UnderstandingSchemaError("Task 2 argument nodes are duplicated.")
    parents = {node["nodeId"]: node["parentNodeId"] for node in nodes}
    if any(parent is not None and parent not in identifiers for parent in parents.values()):
        raise Task2UnderstandingSchemaError("Task 2 argument graph has an unknown parent.")
    for node_id in identifiers:
        seen: set[str] = set()
        current: str | None = node_id
        while current is not None:
            if current in seen:
                raise Task2UnderstandingSchemaError("Task 2 argument graph is cyclic.")
            seen.add(current)
            current = parents[current]


def validate_output(
    text: str,
    snapshot: SubmissionSnapshot,
    *,
    prompt_digest: str,
) -> Task2Understanding:
    value = _parse_object(text)
    _assert_no_forbidden_keys(value)
    schema = _read_schema()
    errors = list(Draft202012Validator(schema).iter_errors(value))
    if errors:
        raise Task2UnderstandingSchemaError("Task 2 understanding has an invalid schema.")
    if value["schemaVersion"] != UNDERSTANDING_SCHEMA_VERSION:
        raise Task2UnderstandingSchemaError("Task 2 understanding schema version is invalid.")
    if value["submissionSnapshotId"] != snapshot.submission_snapshot_id or value["questionContentSha256"] != snapshot.question_content_sha256:
        raise Task2UnderstandingSchemaError("Task 2 understanding submission lineage differs.")
    essay = snapshot.essay_version
    if value["essayVersionId"] != essay.essay_version_id or value["locatorManifestSha256"] != essay.locator_manifest_sha256:
        raise Task2UnderstandingSchemaError("Task 2 understanding essay lineage differs.")
    requirements = value["requirements"]
    requirement_ids = {item["requirementId"] for item in requirements}
    if len(requirement_ids) != len(requirements):
        raise Task2UnderstandingSchemaError("Task 2 requirements are duplicated.")
    for item in requirements:
        start, end = item["questionStart"], item["questionEnd"]
        if start < 0 or end <= start or end > len(snapshot.question) or not snapshot.question[start:end].strip():
            raise Task2UnderstandingSchemaError("Task 2 requirement locator is invalid.")
    coverage = value["coverage"]
    coverage_ids = {item["requirementId"] for item in coverage}
    if len(coverage_ids) != len(coverage) or coverage_ids != requirement_ids:
        raise Task2UnderstandingSchemaError("Task 2 requirement coverage is incomplete.")
    requires_position = any(item["kind"] == "STATE_POSITION" for item in requirements)
    if value["position"]["state"] == "NOT_REQUIRED" and requires_position:
        raise Task2UnderstandingSchemaError("Task 2 position is required by the prompt.")
    valid_sentences = essay.sentence_ids()
    for item in [value["position"], *coverage, *value["argumentNodes"]]:
        for sentence_id in item["sentenceIds"]:
            if sentence_id not in valid_sentences:
                raise Task2UnderstandingSchemaError("Task 2 understanding references a stale locator.")
    for node in value["argumentNodes"]:
        if not set(node["requirementIds"]).issubset(requirement_ids):
            raise Task2UnderstandingSchemaError("Task 2 argument node has an unknown requirement.")
    _validate_graph(value["argumentNodes"])
    try:
        uncertainties = tuple(AnalyzerUncertainty(item) for item in value["analyzerUncertainty"])
    except ValueError as exc:
        raise Task2UnderstandingSchemaError("Task 2 analyzer uncertainty is invalid.") from exc
    unresolved = set(uncertainties)
    if value["questionType"] == "AMBIGUOUS":
        unresolved.add(AnalyzerUncertainty.TASK_TYPE_UNRESOLVED)
    if not requirements:
        unresolved.add(AnalyzerUncertainty.REQUIREMENTS_UNRESOLVED)
    reasons = tuple(sorted(unresolved & _BLOCKING_UNCERTAINTIES, key=lambda item: item.value))
    status = UnderstandingStatus.REVIEW_REQUIRED if reasons else UnderstandingStatus.VALID
    payload = json.loads(canonical_json(value))
    identity = {
        "semanticVersion": UNDERSTANDING_SEMANTIC_VERSION,
        "promptVersion": prompt_digest,
        "output": payload,
        "status": status.value,
        "reviewReasons": [item.value for item in reasons],
    }
    return Task2Understanding(
        schema_version=UNDERSTANDING_SCHEMA_VERSION,
        semantic_version=UNDERSTANDING_SEMANTIC_VERSION,
        prompt_version=prompt_digest,
        artifact_sha256=digest(identity),
        status=status,
        review_reasons=reasons,
        payload=payload,
    )


def build_input(snapshot: SubmissionSnapshot) -> dict[str, Any]:
    """The explicit allowlist mechanically excludes target, rubric, history, and RAG."""
    return {
        "taskType": "task2",
        "submissionSnapshotId": snapshot.submission_snapshot_id,
        "questionContentSha256": snapshot.question_content_sha256,
        "essayVersionId": snapshot.essay_version.essay_version_id,
        "locatorManifestSha256": snapshot.essay_version.locator_manifest_sha256,
        "question": snapshot.question,
        "candidateScript": snapshot.essay_version.original_text,
        "locatorManifest": snapshot.essay_version.locator_manifest(),
        "requiredOutputSchema": _read_schema(),
    }


AttemptRecorder = Callable[[int, int, str, ProviderCallResult], None]


class Task2UnderstandingService:
    """Provider adapter for the pre-scoring Task 2 understanding stage."""

    def __init__(self, transport: ProviderTransport) -> None:
        self._transport = transport

    def run(
        self,
        snapshot: SubmissionSnapshot,
        contract: ProviderContract,
        *,
        on_attempt: AttemptRecorder | None = None,
    ) -> UnderstandingOutcome:
        prompt = prompt_text()
        digest_value = "sha256:" + hashlib.sha256(prompt.encode("utf-8")).hexdigest()
        messages: list[dict[str, Any]] = [
            {"role": "system", "content": prompt},
            {"role": "user", "content": json.dumps(build_input(snapshot), ensure_ascii=False)},
        ]
        for attempt in (1, 2):
            started = monotonic()
            result = self._transport.call(contract, ProviderCallRequest(
                messages=messages,
                display_name="Task 2 understanding",
                require_json_object=True,
                validate_json_object=False,
                temperature=0.0,
            ))
            elapsed = int((monotonic() - started) * 1000)
            if not isinstance(result, ProviderCallResult) or not result.ok:
                if on_attempt is not None and isinstance(result, ProviderCallResult):
                    on_attempt(attempt, elapsed, "NOT_EVALUATED", result)
                raise Task2UnderstandingProviderError("Task 2 understanding Provider call failed.")
            try:
                artifact = validate_output(result.content, snapshot, prompt_digest=digest_value)
            except Task2UnderstandingSchemaError as exc:
                if on_attempt is not None:
                    on_attempt(attempt, elapsed, "INVALID", result)
                if attempt == 2:
                    raise
                messages = messages + [{"role": "assistant", "content": result.content}, {
                    "role": "user",
                    "content": "Return exactly one JSON object matching requiredOutputSchema. Do not add explanations. "
                               "Correct the validation failure using the original immutable input: " + str(exc),
                }]
                continue
            if on_attempt is not None:
                on_attempt(attempt, elapsed, "VALID", result)
            return UnderstandingOutcome(replace(
                artifact,
                provider_id=contract.provider_id,
                model_id=contract.model.model_id,
                model_version=f"configured:{contract.model.model_id}",
                route_key=contract.snapshot.route_key,
            ))
        raise Task2UnderstandingSchemaError("Task 2 understanding is invalid.")
