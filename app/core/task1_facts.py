"""Task 1 image identity, independent ChartFacts extraction, and reconciliation."""
from __future__ import annotations
import logging

from dataclasses import dataclass, field
from enum import Enum
import hashlib
import json
import math
from pathlib import Path
from types import MappingProxyType
from typing import Any, Mapping, Sequence

from .image_input import ImageInput
from .providers import (
    Capability,
    ProviderCallRequest,
    ProviderCallResult,
    ProviderContract,
    ProviderTransport,
    RouteResolution,
)
from .submission import digest


TASK1_IMAGE_SNAPSHOT_VERSION = "task1-image-snapshot-v1"
CHART_FACTS_SCHEMA_VERSION = "chart-facts-output-v1"
CHART_FACTS_SEMANTIC_VERSION = "chart-facts-v3"
CHART_FACTS_REPAIR_POLICY_VERSION = "chart-facts-repair-v1"
CHART_FACTS_RECONCILIATION_VERSION = "chart-facts-reconciliation-v1"
_PROMPT_DIR = Path(__file__).resolve().parents[1] / "prompts"


class Task1FactsError(ValueError):
    """Task 1 visual facts are unavailable, malformed, or conflicting."""

    def __init__(self, message, provider_failure_code=None):
        super().__init__(message)
        self.provider_failure_code = provider_failure_code


class ChartType(str, Enum):
    BAR = "BAR"
    LINE = "LINE"
    TABLE = "TABLE"
    PIE = "PIE"
    PROCESS = "PROCESS"
    MAP = "MAP"
    COMBINED = "COMBINED"


class ChartFactsStatus(str, Enum):
    COMPLETE = "COMPLETE"
    AMBIGUOUS = "AMBIGUOUS"
    UNSUPPORTED = "UNSUPPORTED"


class FactKind(str, Enum):
    TITLE_CONTEXT = "TITLE_CONTEXT"
    UNIT = "UNIT"
    CATEGORY = "CATEGORY"
    TIME_POINT = "TIME_POINT"
    VALUE = "VALUE"
    RANGE = "RANGE"
    TREND = "TREND"
    RANKING = "RANKING"
    COMPARISON = "COMPARISON"
    PROCESS_STEP = "PROCESS_STEP"
    MAP_FEATURE = "MAP_FEATURE"


class ReconciliationStatus(str, Enum):
    VERIFIED = "VERIFIED"
    CONFLICT = "TASK1_FACT_CONFLICT"
    UNAVAILABLE = "UNAVAILABLE"


@dataclass(frozen=True)
class Task1ImageSnapshot:
    question_content_sha256: str
    image_content_sha256: str
    media_type: str
    byte_size: int
    width: int
    height: int
    snapshot_sha256: str
    version: str = TASK1_IMAGE_SNAPSHOT_VERSION
    question: str = field(default="", repr=False, compare=False)
    image: ImageInput = field(default=None, repr=False, compare=False)  # type: ignore[assignment]

    @classmethod
    def create(cls, question: str, image: ImageInput) -> "Task1ImageSnapshot":
        if not isinstance(question, str) or not question.strip() or not isinstance(image, ImageInput):
            raise Task1FactsError("Task 1 question and validated image are required.")
        image_hash = hashlib.sha256(image.content).hexdigest()
        payload = {
            "version": TASK1_IMAGE_SNAPSHOT_VERSION,
            "questionContentSha256": digest(question),
            "imageContentSha256": image_hash,
            "mediaType": image.metadata.media_type,
            "byteSize": image.metadata.byte_size,
            "width": image.metadata.width,
            "height": image.metadata.height,
        }
        return cls(
            question_content_sha256=payload["questionContentSha256"],
            image_content_sha256=image_hash,
            media_type=image.metadata.media_type,
            byte_size=image.metadata.byte_size,
            width=image.metadata.width,
            height=image.metadata.height,
            snapshot_sha256=digest(payload),
            question=question,
            image=image,
        )

    def identity(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "questionContentSha256": self.question_content_sha256,
            "imageContentSha256": self.image_content_sha256,
            "mediaType": self.media_type,
            "byteSize": self.byte_size,
            "width": self.width,
            "height": self.height,
            "snapshotSha256": self.snapshot_sha256,
        }


def _freeze(value: Any) -> Any:
    if isinstance(value, Mapping):
        return MappingProxyType({str(key): _freeze(child) for key, child in value.items()})
    if isinstance(value, (list, tuple)):
        return tuple(_freeze(child) for child in value)
    return value


def _normal(value: Any) -> str:
    return " ".join(str(value).strip().split())


def _number(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        number = float(value)
    else:
        try:
            number = float(str(value).replace(",", "").strip())
        except (TypeError, ValueError):
            return None
    return number if math.isfinite(number) else None


@dataclass(frozen=True)
class ChartFact:
    fact_id: str
    fact_key: str
    kind: FactKind
    subject: str
    value: str
    unit: str
    category: str
    time_point: str
    direction: str
    confidence: str
    critical: bool
    region: tuple[float, float, float, float] | None

    def content(self) -> dict[str, Any]:
        return {
            "factId": self.fact_id,
            "factKey": self.fact_key,
            "kind": self.kind.value,
            "subject": self.subject,
            "value": self.value,
            "unit": self.unit,
            "category": self.category,
            "timePoint": self.time_point,
            "direction": self.direction,
            "confidence": self.confidence,
            "critical": self.critical,
            "region": list(self.region) if self.region else None,
        }


@dataclass(frozen=True)
class ChartFactsArtifact:
    role: str
    source_snapshot_sha256: str
    chart_type: ChartType | None
    status: ChartFactsStatus
    title: str
    units: tuple[str, ...]
    categories: tuple[str, ...]
    time_points: tuple[str, ...]
    facts: tuple[ChartFact, ...]
    ambiguity_reasons: tuple[str, ...]
    provider_id: str
    model_id: str
    prompt_version: str
    schema_version: str
    semantic_version: str
    artifact_sha256: str

    def content(self, *, include_hash: bool = True) -> dict[str, Any]:
        value = {
            "role": self.role,
            "sourceSnapshotSha256": self.source_snapshot_sha256,
            "chartType": self.chart_type.value if self.chart_type else None,
            "status": self.status.value,
            "title": self.title,
            "units": list(self.units),
            "categories": list(self.categories),
            "timePoints": list(self.time_points),
            "facts": [item.content() for item in self.facts],
            "ambiguityReasons": list(self.ambiguity_reasons),
            "providerId": self.provider_id,
            "modelId": self.model_id,
            "promptVersion": self.prompt_version,
            "schemaVersion": self.schema_version,
            "semanticVersion": self.semantic_version,
        }
        if include_hash:
            value["artifactSha256"] = self.artifact_sha256
        return value


_ROOT_KEYS = frozenset({
    "chartType", "status", "title", "units", "categories", "timePoints",
    "facts", "ambiguityReasons",
})
_FACT_KEYS = frozenset({
    "kind", "subject", "value", "unit", "category", "timePoint", "direction",
    "confidence", "critical", "region",
})
_DIRECTIONS = frozenset({"INCREASE", "DECREASE", "STABLE", "MIXED", "NOT_APPLICABLE"})
_CONFIDENCE = frozenset({"HIGH", "MEDIUM", "LOW"})


def _validate_string_list(value: Any, name: str) -> tuple[str, ...]:
    if not isinstance(value, list) or any(not isinstance(item, str) or not item.strip() for item in value):
        raise Task1FactsError(f"ChartFacts {name} is invalid.")
    normalized = tuple(sorted({_normal(item) for item in value}))
    if len(normalized) != len(value):
        raise Task1FactsError(f"ChartFacts {name} contains duplicates.")
    return normalized


def _chart_fact(raw: Mapping[str, Any], index: int) -> ChartFact:
    if set(raw) != _FACT_KEYS:
        raise Task1FactsError("ChartFacts fact fields are invalid.")
    try:
        kind = FactKind(raw["kind"])
    except (TypeError, ValueError) as exc:
        raise Task1FactsError("ChartFacts fact kind is invalid.") from exc
    subject = _normal(raw["subject"])
    value = _normal(raw["value"])
    unit = _normal(raw["unit"])
    category = _normal(raw["category"])
    time_point = _normal(raw["timePoint"])
    direction = _normal(raw["direction"]).upper()
    confidence = _normal(raw["confidence"]).upper()
    critical = raw["critical"]
    if not subject or direction not in _DIRECTIONS or confidence not in _CONFIDENCE or not isinstance(critical, bool):
        raise Task1FactsError("ChartFacts fact semantics are invalid.")
    if kind in {FactKind.VALUE, FactKind.RANGE} and _number(value) is None:
        raise Task1FactsError("Numeric ChartFacts values must be finite numbers.")
    if kind is FactKind.TREND and unit:
        raise Task1FactsError("Qualitative TREND facts require an empty unit; axis units belong on VALUE facts.")
    numeric = _number(value)
    if numeric is not None and unit.casefold() in {"%", "percent", "percentage"} and not 0 <= numeric <= 100:
        raise Task1FactsError("Percentage ChartFacts value is impossible.")
    region_value = raw["region"]
    region: tuple[float, float, float, float] | None = None
    if region_value is not None:
        if (
            not isinstance(region_value, list)
            or len(region_value) != 4
            or any(_number(item) is None for item in region_value)
        ):
            raise Task1FactsError("ChartFacts region is invalid.")
        region = tuple(float(item) for item in region_value)  # type: ignore[assignment]
        x, y, width, height = region
        if min(region) < 0 or x + width > 1 or y + height > 1 or width <= 0 or height <= 0:
            raise Task1FactsError("ChartFacts region is outside the image.")
    semantic_key = {
        "kind": kind.value,
        "subject": subject.casefold(),
        "category": category.casefold(),
        "timePoint": time_point.casefold(),
    }
    fact_key = "chart-fact-key:" + digest(semantic_key)
    identity = {
        **semantic_key,
        "value": value,
        "unit": unit.casefold(),
        "direction": direction,
    }
    return ChartFact(
        fact_id=f"chart-fact:{index:04d}:" + digest(identity),
        fact_key=fact_key,
        kind=kind,
        subject=subject,
        value=value,
        unit=unit,
        category=category,
        time_point=time_point,
        direction=direction,
        confidence=confidence,
        critical=critical,
        region=region,
    )


def validate_chartfacts_output(
    value: Mapping[str, Any],
    source: Task1ImageSnapshot,
    contract: ProviderContract,
    *,
    role: str,
    prompt_version: str,
) -> ChartFactsArtifact:
    if not isinstance(value, Mapping) or set(value) != _ROOT_KEYS:
        raise Task1FactsError("ChartFacts output fields are invalid.")
    try:
        status = ChartFactsStatus(value["status"])
        chart_type = ChartType(value["chartType"]) if value["chartType"] is not None else None
    except (TypeError, ValueError) as exc:
        raise Task1FactsError("ChartFacts status or chart type is invalid.") from exc
    title = _normal(value["title"])
    if not isinstance(value["title"], str):
        raise Task1FactsError("ChartFacts title is invalid.")
    units = _validate_string_list(value["units"], "units")
    categories = _validate_string_list(value["categories"], "categories")
    time_points = _validate_string_list(value["timePoints"], "time points")
    raw_facts = value["facts"]
    if not isinstance(raw_facts, list) or any(not isinstance(item, Mapping) for item in raw_facts):
        raise Task1FactsError("ChartFacts facts are invalid.")
    facts = tuple(_chart_fact(item, index) for index, item in enumerate(raw_facts, start=1))
    reasons = _validate_string_list(value["ambiguityReasons"], "ambiguity reasons")
    if status is ChartFactsStatus.COMPLETE:
        if chart_type is None or not title or not facts or reasons:
            raise Task1FactsError("Complete ChartFacts lacks required evidence.")
        if any(item.confidence == "LOW" and item.critical for item in facts):
            raise Task1FactsError("Low-confidence critical ChartFacts cannot be complete.")
    elif status is ChartFactsStatus.AMBIGUOUS:
        if chart_type is None or not reasons:
            raise Task1FactsError("Ambiguous ChartFacts must explain the ambiguity.")
    elif facts or chart_type is not None or not reasons:
        raise Task1FactsError("Unsupported ChartFacts must not fabricate facts.")
    keys: dict[str, ChartFact] = {}
    for fact in facts:
        prior = keys.get(fact.fact_key)
        if prior is not None and prior.content() != fact.content():
            raise Task1FactsError("ChartFacts contains conflicting duplicate facts.")
        if fact.category and fact.category.casefold() not in {value.casefold() for value in categories}:
            raise Task1FactsError("ChartFacts fact category is undeclared.")
        if fact.time_point and fact.time_point.casefold() not in {value.casefold() for value in time_points}:
            raise Task1FactsError("ChartFacts fact time point is undeclared.")
        if fact.unit and fact.unit.casefold() not in {value.casefold() for value in units}:
            raise Task1FactsError("ChartFacts fact unit is undeclared.")
        keys[fact.fact_key] = fact
    partial = ChartFactsArtifact(
        role=role,
        source_snapshot_sha256=source.snapshot_sha256,
        chart_type=chart_type,
        status=status,
        title=title,
        units=units,
        categories=categories,
        time_points=time_points,
        facts=tuple(sorted(facts, key=lambda item: (item.fact_key, item.fact_id))),
        ambiguity_reasons=reasons,
        provider_id=contract.provider_id,
        model_id=contract.model.model_id,
        prompt_version=prompt_version,
        schema_version=CHART_FACTS_SCHEMA_VERSION,
        semantic_version=CHART_FACTS_SEMANTIC_VERSION,
        artifact_sha256="",
    )
    return ChartFactsArtifact(**{
        **partial.__dict__,
        "artifact_sha256": digest(partial.content(include_hash=False)),
    })


def _prompt(role: str) -> str:
    name = "task1_chart_facts_verification.md" if role == "INDEPENDENT_VERIFIER" else "task1_chart_facts_extraction.md"
    try:
        return "\n\n".join((
            (_PROMPT_DIR / name).read_text(encoding="utf-8"),
            (_PROMPT_DIR / "task1_chart_facts_contract.md").read_text(encoding="utf-8"),
        ))
    except OSError as exc:
        raise Task1FactsError("Task 1 ChartFacts prompt is unavailable.") from exc


def _prompt_version(role: str) -> str:
    return "sha256:" + hashlib.sha256(_prompt(role).encode("utf-8")).hexdigest()


def _required_output_shape() -> dict[str, Any]:
    return {
        "chartType": "BAR|LINE|TABLE|PIE|PROCESS|MAP|COMBINED|null",
        "status": "COMPLETE|AMBIGUOUS|UNSUPPORTED",
        "title": "",
        "units": [],
        "categories": [],
        "timePoints": [],
        "facts": [{
            "kind": "TITLE_CONTEXT|UNIT|CATEGORY|TIME_POINT|VALUE|RANGE|TREND|RANKING|COMPARISON|PROCESS_STEP|MAP_FEATURE",
            "subject": "",
            "value": "",
            "unit": "",
            "category": "",
            "timePoint": "",
            "direction": "INCREASE|DECREASE|STABLE|MIXED|NOT_APPLICABLE",
            "confidence": "HIGH|MEDIUM|LOW",
            "critical": True,
            "region": None,
        }],
        "ambiguityReasons": [],
    }


class ChartFactsService:
    def __init__(self, transport: ProviderTransport, *, role: str) -> None:
        if role not in {"EXTRACTOR", "INDEPENDENT_VERIFIER"}:
            raise ValueError("ChartFacts role is invalid.")
        self._transport = transport
        self._role = role

    def run(self, source: Task1ImageSnapshot, resolution: RouteResolution) -> ChartFactsArtifact:
        contract = resolution.contract if isinstance(resolution, RouteResolution) else None
        if (
            contract is None
            or not resolution.ok
            or resolution.route.task_type != "task1"
            or not contract.model.supports(Capability.IMAGE_INPUT)
        ):
            raise Task1FactsError("Task 1 ChartFacts route is unavailable or not vision capable.")
        if digest({
            "version": source.version,
            "questionContentSha256": source.question_content_sha256,
            "imageContentSha256": source.image_content_sha256,
            "mediaType": source.media_type,
            "byteSize": source.byte_size,
            "width": source.width,
            "height": source.height,
        }) != source.snapshot_sha256:
            raise Task1FactsError("Task 1 image snapshot identity mismatch.")
        prompt_version = _prompt_version(self._role)
        payload = {
            "role": self._role,
            "taskType": "task1",
            "question": source.question,
            "sourceIdentity": source.identity(),
            "mustWorkIndependently": self._role == "INDEPENDENT_VERIFIER",
            "requiredOutputShape": _required_output_shape(),
        }
        content = [
            {"type": "text", "text": json.dumps(payload, ensure_ascii=False)},
            {"type": "image_url", "image_url": {"url": source.image.as_data_url()}},
        ]
        messages = [
            {"role": "system", "content": _prompt(self._role)},
            {"role": "user", "content": content},
        ]
        last_error: Exception | None = None
        previous = None
        for attempt in (1, 2):
            request_messages = list(messages)
            if attempt == 2:
                request_messages.append({"role": "assistant", "content": previous})
                request_messages.append({
                    "role": "user",
                    "content": "Return one corrected JSON object with exactly the required fields; do not add facts not visible in the image. Validation rule: " + (str(last_error) if isinstance(last_error, Task1FactsError) else "Return valid JSON without trailing prose."),
                })
            result = self._transport.call(contract, ProviderCallRequest(
                request_messages,
                f"Task 1 {self._role.lower()}",
                require_json_object=True,
                validate_json_object=False,
                # Chart extraction is consumed atomically. Some compatible
                # gateways terminate long vision streams before the answer.
                stream=False,
            ))
            if not isinstance(result, ProviderCallResult) or not result.ok:
                logging.getLogger(__name__).warning("chart_facts_provider role=%s failure=%s", self._role,
                    result.failure.code.value if isinstance(result, ProviderCallResult) and result.failure else "INVALID_RESULT")
                raise Task1FactsError("Task 1 ChartFacts Provider failed.",
                    result.failure.code.value if isinstance(result, ProviderCallResult) and result.failure else None)
            try:
                raw = json.loads(result.content)
                if not isinstance(raw, Mapping):
                    raise Task1FactsError("ChartFacts output is not an object.")
                return validate_chartfacts_output(
                    raw, source, contract, role=self._role, prompt_version=prompt_version
                )
            except (json.JSONDecodeError, Task1FactsError) as exc:
                logging.getLogger(__name__).warning("chart_facts_validation role=%s attempt=%s rule=%s",
                    self._role, attempt, str(exc) if isinstance(exc, Task1FactsError) else "INVALID_JSON")
                last_error = exc
                previous = result.content
        raise Task1FactsError("Task 1 ChartFacts output remained invalid after repair.") from last_error


@dataclass(frozen=True)
class FactConflict:
    fact_key: str
    reason: str
    extractor_fact_id: str | None
    verifier_fact_id: str | None

    def content(self) -> dict[str, Any]:
        return {
            "factKey": self.fact_key,
            "reason": self.reason,
            "extractorFactId": self.extractor_fact_id,
            "verifierFactId": self.verifier_fact_id,
        }


@dataclass(frozen=True)
class ReconciledChartFacts:
    status: ReconciliationStatus
    source_snapshot_sha256: str
    extraction_sha256: str | None
    verification_sha256: str | None
    chart_type: ChartType | None
    facts: tuple[ChartFact, ...]
    conflicts: tuple[FactConflict, ...]
    version: str
    artifact_sha256: str

    def content(self, *, include_hash: bool = True) -> dict[str, Any]:
        value = {
            "status": self.status.value,
            "sourceSnapshotSha256": self.source_snapshot_sha256,
            "extractionSha256": self.extraction_sha256,
            "verificationSha256": self.verification_sha256,
            "chartType": self.chart_type.value if self.chart_type else None,
            "facts": [item.content() for item in self.facts],
            "conflicts": [item.content() for item in self.conflicts],
            "version": self.version,
        }
        if include_hash:
            value["artifactSha256"] = self.artifact_sha256
        return value


def _facts_agree(left: ChartFact, right: ChartFact) -> str | None:
    if left.unit.casefold() != right.unit.casefold():
        return "UNIT_MISMATCH"
    if left.direction != right.direction:
        return "DIRECTION_MISMATCH"
    left_number, right_number = _number(left.value), _number(right.value)
    if left_number is not None and right_number is not None:
        tolerance = max(0.5, 0.01 * max(abs(left_number), abs(right_number)))
        if abs(left_number - right_number) > tolerance:
            return "VALUE_MISMATCH"
    elif left.value.casefold() != right.value.casefold():
        return "VALUE_MISMATCH"
    return None


def reconcile_chart_facts(
    source: Task1ImageSnapshot,
    extraction: ChartFactsArtifact | None,
    verification: ChartFactsArtifact | None,
) -> ReconciledChartFacts:
    conflicts: list[FactConflict] = []
    agreed: list[ChartFact] = []
    status = ReconciliationStatus.UNAVAILABLE
    chart_type: ChartType | None = None
    if (
        extraction is not None
        and verification is not None
        and extraction.source_snapshot_sha256 == source.snapshot_sha256
        and verification.source_snapshot_sha256 == source.snapshot_sha256
        and extraction.status is ChartFactsStatus.COMPLETE
        and verification.status is ChartFactsStatus.COMPLETE
    ):
        chart_type = extraction.chart_type
        if extraction.chart_type != verification.chart_type:
            conflicts.append(FactConflict("chart-type", "CHART_TYPE_MISMATCH", None, None))
        left = {item.fact_key: item for item in extraction.facts}
        right = {item.fact_key: item for item in verification.facts}
        for key in sorted(set(left) | set(right)):
            first, second = left.get(key), right.get(key)
            critical = bool((first and first.critical) or (second and second.critical))
            if first is None or second is None:
                if critical:
                    conflicts.append(FactConflict(
                        key, "CRITICAL_FACT_MISSING",
                        first.fact_id if first else None,
                        second.fact_id if second else None,
                    ))
                continue
            mismatch = _facts_agree(first, second)
            if mismatch is not None:
                if critical:
                    conflicts.append(FactConflict(key, mismatch, first.fact_id, second.fact_id))
                continue
            agreed.append(first)
        status = ReconciliationStatus.CONFLICT if conflicts else ReconciliationStatus.VERIFIED
    elif extraction is not None or verification is not None:
        conflicts.append(FactConflict("artifact", "INCOMPLETE_INDEPENDENT_EVIDENCE", None, None))
    partial = ReconciledChartFacts(
        status=status,
        source_snapshot_sha256=source.snapshot_sha256,
        extraction_sha256=extraction.artifact_sha256 if extraction else None,
        verification_sha256=verification.artifact_sha256 if verification else None,
        chart_type=chart_type if status is ReconciliationStatus.VERIFIED else None,
        facts=tuple(sorted(agreed, key=lambda item: item.fact_key)) if status is ReconciliationStatus.VERIFIED else (),
        conflicts=tuple(conflicts),
        version=CHART_FACTS_RECONCILIATION_VERSION,
        artifact_sha256="",
    )
    return ReconciledChartFacts(**{
        **partial.__dict__,
        "artifact_sha256": digest(partial.content(include_hash=False)),
    })


@dataclass(frozen=True)
class Task1FactPipelineOutcome:
    extraction: ChartFactsArtifact | None
    verification: ChartFactsArtifact | None
    reconciled: ReconciledChartFacts
    provider_failures: tuple[str, ...] = ()


class Task1FactPipeline:
    def __init__(self, extraction: ChartFactsService, verification: ChartFactsService) -> None:
        self._extraction = extraction
        self._verification = verification

    def run(
        self,
        source: Task1ImageSnapshot,
        extraction_resolution: RouteResolution,
        verification_resolution: RouteResolution,
    ) -> Task1FactPipelineOutcome:
        extracted: ChartFactsArtifact | None = None
        verified: ChartFactsArtifact | None = None
        failures = []
        try:
            extracted = self._extraction.run(source, extraction_resolution)
        except Task1FactsError as exc:
            if exc.provider_failure_code: failures.append(exc.provider_failure_code)
        try:
            verified = self._verification.run(source, verification_resolution)
        except Task1FactsError as exc:
            if exc.provider_failure_code: failures.append(exc.provider_failure_code)
        return Task1FactPipelineOutcome(
            extracted,
            verified,
            reconcile_chart_facts(source, extracted, verified),
            tuple(failures),
        )
