"""Privacy-minimized, append-only execution telemetry for the Phase 0 pipeline.

The records in this module intentionally hold fingerprints and configuration
metadata only.  Candidate text, image bytes/data URLs, provider bodies, and
credentials must stay at their execution boundaries and never enter a trace.
"""
from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass, field
from decimal import Decimal
from enum import Enum
import hashlib
import json
import threading
import uuid
from typing import Generic, Mapping, TypeVar

from .providers import ProviderCallResult, ProviderContract, ProviderUsage


WORKFLOW_VERSION = "workflow:p0-05-v1"
PHASE0_RUBRIC_VERSION = "rubric:not-integrated-v1"


class CostState(str, Enum):
    KNOWN = "KNOWN"
    UNKNOWN = "UNKNOWN"


class CacheOutcome(str, Enum):
    HIT = "HIT"
    MISS = "MISS"
    BYPASSED = "BYPASSED"


@dataclass(frozen=True)
class VersionSnapshot:
    """Every code/configuration version that can affect a cached score."""

    route: str
    model: str
    model_version: str
    prompt_version: str
    rubric_version: str
    workflow_version: str
    rubric_id: str = "rubric:not-integrated"
    runtime_content_sha256: str = "not-integrated"
    preprocessing_version: str = ""
    understanding_prompt_version: str = ""
    understanding_semantic_version: str = ""
    understanding_sha256: str = ""
    student_evidence_prompt_version: str = ""
    student_evidence_semantic_version: str = ""
    student_evidence_sha256: str = ""

    @property
    def cacheable(self) -> bool:
        base = all(
            value.strip()
            for value in (
                self.route,
                self.model,
                self.model_version,
                self.prompt_version,
                self.rubric_version,
                self.rubric_id,
                self.runtime_content_sha256,
                self.workflow_version,
            )
        )
        understanding = (
            self.preprocessing_version,
            self.understanding_prompt_version,
            self.understanding_semantic_version,
            self.understanding_sha256,
        )
        evidence = (
            self.student_evidence_prompt_version,
            self.student_evidence_semantic_version,
            self.student_evidence_sha256,
        )
        return (
            base
            and (not any(understanding) or all(value.strip() for value in understanding))
            and (not any(evidence) or all(value.strip() for value in evidence))
        )


@dataclass(frozen=True)
class ArtifactLineage:
    """Opaque P2 artifact links. Raw question/essay data never belongs here."""

    submission_snapshot_id: str | None = None
    essay_version_id: str | None = None
    locator_manifest_sha256: str | None = None
    task2_understanding_sha256: str | None = None
    student_evidence_sha256: str | None = None

    def __post_init__(self) -> None:
        values = (
            self.submission_snapshot_id,
            self.essay_version_id,
            self.locator_manifest_sha256,
            self.task2_understanding_sha256,
        )
        if any(values) and not all(values):
            raise ValueError("P2 artifact lineage must be complete")
        if self.student_evidence_sha256 is not None and not all(values):
            raise ValueError("Student Evidence lineage requires P2-01 lineage")

    def as_dict(self) -> dict[str, str | None]:
        return {
            "submissionSnapshotId": self.submission_snapshot_id,
            "essayVersionId": self.essay_version_id,
            "locatorManifestSha256": self.locator_manifest_sha256,
            "task2UnderstandingSha256": self.task2_understanding_sha256,
            "studentEvidenceSha256": self.student_evidence_sha256,
        }


@dataclass(frozen=True)
class CacheIdentity:
    """Opaque exact-semantic identity for the locked original-score cache."""

    key: str
    submission_fingerprint: str
    versions: VersionSnapshot
    cacheable: bool

    @classmethod
    def from_submission(
        cls,
        task_type: str,
        question: str,
        essay: str,
        versions: VersionSnapshot,
    ) -> "CacheIdentity":
        normalized = {
            "task_type": task_type.strip().lower(),
            "question": question.strip(),
            "essay": essay.strip(),
        }
        submission_fingerprint = _digest(normalized)
        key = _digest({
            "submission": submission_fingerprint,
            "route": versions.route,
            "model": versions.model,
            "model_version": versions.model_version,
            "prompt_version": versions.prompt_version,
            "rubric_version": versions.rubric_version,
            "rubric_id": versions.rubric_id,
            "runtime_content_sha256": versions.runtime_content_sha256,
            "workflow_version": versions.workflow_version,
            "preprocessing_version": versions.preprocessing_version,
            "understanding_prompt_version": versions.understanding_prompt_version,
            "understanding_semantic_version": versions.understanding_semantic_version,
            "understanding_sha256": versions.understanding_sha256,
            "student_evidence_prompt_version": versions.student_evidence_prompt_version,
            "student_evidence_semantic_version": versions.student_evidence_semantic_version,
            "student_evidence_sha256": versions.student_evidence_sha256,
        })
        return cls(
            key=key,
            submission_fingerprint=submission_fingerprint,
            versions=versions,
            cacheable=versions.cacheable,
        )

    @classmethod
    def from_artifacts(
        cls, *, submission_snapshot_sha256: str, understanding_sha256: str,
        student_evidence_sha256: str,
        versions: VersionSnapshot,
    ) -> "CacheIdentity":
        if not submission_snapshot_sha256 or not understanding_sha256 or not student_evidence_sha256:
            raise ValueError("Task 2 cache identity requires versioned artifacts")
        submission_fingerprint = _digest({
            "submissionSnapshotSha256": submission_snapshot_sha256,
            "task2UnderstandingSha256": understanding_sha256,
            "studentEvidenceSha256": student_evidence_sha256,
        })
        key = _digest({
            "submission": submission_fingerprint,
            "route": versions.route,
            "model": versions.model,
            "model_version": versions.model_version,
            "prompt_version": versions.prompt_version,
            "rubric_version": versions.rubric_version,
            "rubric_id": versions.rubric_id,
            "runtime_content_sha256": versions.runtime_content_sha256,
            "workflow_version": versions.workflow_version,
            "preprocessing_version": versions.preprocessing_version,
            "understanding_prompt_version": versions.understanding_prompt_version,
            "understanding_semantic_version": versions.understanding_semantic_version,
            "understanding_sha256": versions.understanding_sha256,
            "student_evidence_prompt_version": versions.student_evidence_prompt_version,
            "student_evidence_semantic_version": versions.student_evidence_semantic_version,
            "student_evidence_sha256": versions.student_evidence_sha256,
        })
        return cls(key=key, submission_fingerprint=submission_fingerprint, versions=versions, cacheable=versions.cacheable)


@dataclass(frozen=True)
class ModelPricing:
    """Explicit local pricing metadata; absence means cost is unknown."""

    provider: str
    model: str
    model_version: str
    input_per_million: Decimal
    output_per_million: Decimal
    currency: str = "USD"


class PricingCatalog:
    """Explicitly supplied local pricing metadata; defaults deliberately to unknown."""

    def __init__(self, entries: Mapping[tuple[str, str, str], ModelPricing] | None = None) -> None:
        self._entries = dict(entries or {})

    def pricing_for(self, contract: ProviderContract, model_version: str) -> ModelPricing | None:
        return self._entries.get((
            contract.provider_id,
            contract.model.model_id,
            model_version,
        ))


@dataclass(frozen=True)
class UsageCost:
    state: CostState
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    total_tokens: int | None = None
    input_cost: Decimal | None = None
    output_cost: Decimal | None = None
    total: Decimal | None = None
    currency: str | None = None

    @classmethod
    def unknown(cls, usage: ProviderUsage | None = None) -> "UsageCost":
        return cls(
            state=CostState.UNKNOWN,
            prompt_tokens=usage.prompt_tokens if usage else None,
            completion_tokens=usage.completion_tokens if usage else None,
            total_tokens=usage.total_tokens if usage else None,
        )

    @classmethod
    def from_usage(
        cls, usage: ProviderUsage | None, pricing: ModelPricing | None
    ) -> "UsageCost":
        if (
            usage is None
            or pricing is None
            or usage.prompt_tokens is None
            or usage.completion_tokens is None
        ):
            return cls.unknown(usage)
        input_cost = Decimal(usage.prompt_tokens) * pricing.input_per_million / Decimal(1_000_000)
        output_cost = Decimal(usage.completion_tokens) * pricing.output_per_million / Decimal(1_000_000)
        return cls(
            state=CostState.KNOWN,
            prompt_tokens=usage.prompt_tokens,
            completion_tokens=usage.completion_tokens,
            total_tokens=(
                usage.total_tokens
                if usage.total_tokens is not None
                else usage.prompt_tokens + usage.completion_tokens
            ),
            input_cost=input_cost,
            output_cost=output_cost,
            total=input_cost + output_cost,
            currency=pricing.currency,
        )


@dataclass(frozen=True)
class ProviderCall:
    """One provider attempt, represented only by safe execution metadata."""

    trace_id: str
    stage: str
    provider: str
    model: str
    model_version: str
    route: str
    prompt_version: str
    attempt: int
    retry: bool
    transport_attempts: int
    latency_ms: int
    schema_status: str
    cost: UsageCost
    failure_category: str | None = None


@dataclass(frozen=True)
class Trace:
    """An immutable, append-only execution trace with no request/response bodies."""

    trace_id: str
    submission_fingerprint: str
    versions: VersionSnapshot
    provider_calls: tuple[ProviderCall, ...] = ()
    cache_outcome: CacheOutcome = CacheOutcome.BYPASSED
    cache_source_trace_id: str | None = None
    workflow_status: str | None = None
    failure_category: str | None = None
    artifact_lineage: ArtifactLineage = field(default_factory=ArtifactLineage)

    def __post_init__(self) -> None:
        object.__setattr__(self, "provider_calls", tuple(self.provider_calls))


@dataclass(frozen=True)
class TraceSummary:
    trace_count: int
    provider_call_count: int
    known_cost_count: int
    unknown_cost_count: int
    total_cost_state: CostState
    total_cost: Decimal | None
    currency: str | None


class TraceLedger:
    """Thread-safe in-memory append-only telemetry store; it has no disk persistence."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._traces: list[Trace] = []

    def append(self, trace: Trace) -> None:
        with self._lock:
            self._traces.append(trace)

    def records(self) -> tuple[Trace, ...]:
        with self._lock:
            return tuple(self._traces)

    def summary(self) -> TraceSummary:
        records = self.records()
        calls = tuple(call for trace in records for call in trace.provider_calls)
        known = [call.cost for call in calls if call.cost.state is CostState.KNOWN]
        unknown = [call.cost for call in calls if call.cost.state is CostState.UNKNOWN]
        currencies = {item.currency for item in known}
        if unknown or len(currencies) != 1:
            return TraceSummary(
                trace_count=len(records),
                provider_call_count=len(calls),
                known_cost_count=len(known),
                unknown_cost_count=len(unknown),
                total_cost_state=CostState.UNKNOWN,
                total_cost=None,
                currency=None,
            )
        return TraceSummary(
            trace_count=len(records),
            provider_call_count=len(calls),
            known_cost_count=len(known),
            unknown_cost_count=0,
            total_cost_state=CostState.KNOWN,
            total_cost=sum((item.total or Decimal("0") for item in known), Decimal("0")),
            currency=next(iter(currencies), None),
        )


class TraceRecorder:
    """Collects one execution trace without retaining request or response bodies."""

    def __init__(
        self,
        ledger: TraceLedger,
        identity: CacheIdentity,
        trace_id: str | None = None,
        pricing: PricingCatalog | None = None,
        artifact_lineage: ArtifactLineage | None = None,
    ) -> None:
        self.trace_id = trace_id or uuid.uuid4().hex
        self._ledger = ledger
        self._identity = identity
        self._pricing = pricing or PricingCatalog()
        self._calls: list[ProviderCall] = []
        self._cache_outcome = CacheOutcome.BYPASSED
        self._cache_source_trace_id: str | None = None
        self._artifact_lineage = artifact_lineage or ArtifactLineage()

    def bind_artifact_lineage(self, lineage: ArtifactLineage) -> None:
        self._artifact_lineage = lineage

    def bind_cache_identity(self, identity: CacheIdentity) -> None:
        self._identity = identity

    def record_provider_call(
        self,
        *,
        stage: str,
        contract: ProviderContract,
        prompt_version: str,
        attempt: int,
        latency_ms: int,
        schema_status: str,
        result: ProviderCallResult,
    ) -> None:
        model_version = f"configured:{contract.model.model_id}"
        transport_attempts = max(1, result.attempts)
        self._calls.append(ProviderCall(
            trace_id=self.trace_id,
            stage=stage,
            provider=contract.provider_id,
            model=contract.model.model_id,
            model_version=model_version,
            route=contract.snapshot.route_key,
            prompt_version=prompt_version,
            attempt=attempt,
            retry=attempt > 1 or transport_attempts > 1,
            transport_attempts=transport_attempts,
            latency_ms=max(0, latency_ms),
            schema_status=schema_status,
            cost=UsageCost.from_usage(
                result.usage,
                self._pricing.pricing_for(contract, model_version),
            ),
            failure_category=(result.failure.code.value if result.failure else None),
        ))

    def record_cache(self, lookup: "CacheLookup[object]") -> None:
        self._cache_outcome = CacheOutcome.HIT if lookup.hit else (
            CacheOutcome.MISS if self._identity.cacheable else CacheOutcome.BYPASSED
        )
        self._cache_source_trace_id = lookup.source_trace_id

    def finish(self, workflow_status: str, failure_category: str | None = None) -> Trace:
        trace = Trace(
            trace_id=self.trace_id,
            submission_fingerprint=self._identity.submission_fingerprint,
            versions=self._identity.versions,
            provider_calls=tuple(self._calls),
            cache_outcome=self._cache_outcome,
            cache_source_trace_id=self._cache_source_trace_id,
            workflow_status=workflow_status,
            failure_category=failure_category,
            artifact_lineage=self._artifact_lineage,
        )
        self._ledger.append(trace)
        return trace


T = TypeVar("T")


@dataclass(frozen=True)
class CacheLookup(Generic[T]):
    value: T
    hit: bool
    source_trace_id: str | None

    def as_tuple(self) -> tuple[bool, str | None]:
        return self.hit, self.source_trace_id


class ScoreCache(Generic[T]):
    """Small exact-identity LRU cache with source-trace lineage for each entry."""

    def __init__(self, limit: int = 32) -> None:
        self._limit = limit
        self._lock = threading.Lock()
        self._items: OrderedDict[str, tuple[T, str]] = OrderedDict()

    def put_or_get(
        self, identity: CacheIdentity, value: T, source_trace_id: str
    ) -> CacheLookup[T]:
        if not identity.cacheable:
            return CacheLookup(value=value, hit=False, source_trace_id=None)
        with self._lock:
            cached = self._items.get(identity.key)
            if cached is not None:
                self._items.move_to_end(identity.key)
                return CacheLookup(value=cached[0], hit=True, source_trace_id=cached[1])
            self._items[identity.key] = (value, source_trace_id)
            while len(self._items) > self._limit:
                self._items.popitem(last=False)
            return CacheLookup(value=value, hit=False, source_trace_id=source_trace_id)


def _digest(value: object) -> str:
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()
