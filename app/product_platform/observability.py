"""Privacy-minimised metrics, disclosure budget, quotas, and alerts."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from .contracts import ErrorCode, PlatformError, Principal, digest
from .persistence import PlatformStore


METRIC_DICTIONARY_VERSION = "c3-metric-dictionary-v1"
ALLOWED_EVENT_TYPES = frozenset({
    "API_REQUEST",
    "SUBMISSION_CREATED",
    "SUBMISSION_TERMINAL",
    "JOB_RETRY",
    "JOB_FAILURE",
    "EXPORT_CREATED",
    "DELETION_COMPLETED",
    "COST_OBSERVED",
})
ALLOWED_DIMENSIONS = frozenset({
    "apiVersion",
    "route",
    "method",
    "statusClass",
    "taskType",
    "outcome",
    "failureCode",
    "providerRoute",
    "modelId",
    "currency",
})
ALLOWED_NUMERIC = frozenset({
    "count",
    "latencyMs",
    "inputTokens",
    "outputTokens",
    "costMinorUnits",
    "attempts",
})


@dataclass(frozen=True)
class MetricDefinition:
    name: str
    unit: str
    denominator: str
    aggregation: str
    missing_state: str = "UNKNOWN"


METRIC_DICTIONARY = (
    MetricDefinition("request_count", "request", "eligible API requests", "COUNT"),
    MetricDefinition("completion_rate", "ratio", "accepted submissions", "TERMINAL/ACCEPTED"),
    MetricDefinition("job_failure_rate", "ratio", "leased jobs", "FAILED/LEASED"),
    MetricDefinition("latency_ms", "millisecond", "timed operations", "MEAN"),
    MetricDefinition("cost_minor_units", "minor-currency-unit", "priced calls", "SUM"),
)


@dataclass(frozen=True)
class AggregateResult:
    event_type: str
    state: str
    cohort_size: int
    event_count: int | None
    numeric: Mapping[str, float]
    budget_remaining: int


@dataclass(frozen=True)
class AlertRule:
    name: str
    numeric_key: str
    threshold: float
    comparison: str = "GREATER_THAN"

    def evaluate(self, aggregate: AggregateResult) -> bool:
        if aggregate.state != "AVAILABLE" or self.numeric_key not in aggregate.numeric:
            return False
        value = float(aggregate.numeric[self.numeric_key])
        if self.comparison == "GREATER_THAN":
            return value > self.threshold
        if self.comparison == "GREATER_OR_EQUAL":
            return value >= self.threshold
        raise ValueError("unsupported alert comparison")


@dataclass(frozen=True)
class ReadinessSnapshot:
    version: str
    aggregates: Mapping[str, AggregateResult]
    triggered_alerts: tuple[str, ...]
    state: str


def build_readiness_snapshot(
    metrics: "PrivacySafeMetrics",
    event_types: tuple[str, ...],
    alert_rules: tuple[AlertRule, ...],
    *,
    min_cohort: int = 3,
) -> ReadinessSnapshot:
    aggregates = {
        event_type: metrics.aggregate(event_type, min_cohort=min_cohort)
        for event_type in event_types
    }
    triggered = tuple(sorted(
        rule.name
        for rule in alert_rules
        if any(rule.evaluate(aggregate) for aggregate in aggregates.values())
    ))
    state = "ALERT" if triggered else "READY_NO_ALERT"
    if any(value.state != "AVAILABLE" for value in aggregates.values()):
        state = "INSUFFICIENT_OR_SUPPRESSED_DATA"
    return ReadinessSnapshot(METRIC_DICTIONARY_VERSION, aggregates, triggered, state)


class PrivacySafeMetrics:
    """Small-cohort suppression plus a bounded disclosure-query budget.

    The budget is an operational disclosure control, not a differential-privacy
    epsilon claim; no statistical privacy guarantee is asserted.
    """

    def __init__(self, store: PlatformStore, *, query_budget: int = 20) -> None:
        if query_budget < 1:
            raise ValueError("query budget must be positive")
        self.store = store
        self._remaining = query_budget

    @staticmethod
    def actor_sha256(principal: Principal) -> str:
        return digest({"tenantId": principal.tenant_id, "ownerId": principal.user_id})

    def record(
        self,
        principal: Principal,
        event_type: str,
        *,
        dimensions: Mapping[str, str] | None = None,
        numeric: Mapping[str, float] | None = None,
    ) -> str:
        if event_type not in ALLOWED_EVENT_TYPES:
            raise PlatformError(ErrorCode.INVALID_REQUEST, "Metric event type is not allowed.")
        dimensions = dict(dimensions or {})
        numeric = dict(numeric or {"count": 1})
        if not set(dimensions).issubset(ALLOWED_DIMENSIONS):
            raise PlatformError(ErrorCode.INVALID_REQUEST, "Metric dimension is not allowed.")
        if not set(numeric).issubset(ALLOWED_NUMERIC):
            raise PlatformError(ErrorCode.INVALID_REQUEST, "Metric value is not allowed.")
        for key, value in dimensions.items():
            if not isinstance(value, str) or len(value) > 80 or "\n" in value:
                raise PlatformError(ErrorCode.INVALID_REQUEST, f"Metric dimension {key} is unsafe.")
        for value in numeric.values():
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise PlatformError(ErrorCode.INVALID_REQUEST, "Metric numeric values must be numbers.")
        return self.store.record_metric_event(
            tenant_id=principal.tenant_id,
            actor_sha256=self.actor_sha256(principal),
            event_type=event_type,
            dimensions=dimensions,
            numeric=numeric,
        )

    def aggregate(self, event_type: str, *, min_cohort: int = 3) -> AggregateResult:
        if event_type not in ALLOWED_EVENT_TYPES or min_cohort < 2:
            raise PlatformError(ErrorCode.INVALID_REQUEST, "Invalid aggregate query.")
        if self._remaining <= 0:
            raise PlatformError(
                ErrorCode.PRIVACY_BUDGET_EXHAUSTED,
                "Metric disclosure budget is exhausted.",
            )
        self._remaining -= 1
        rows = self.store.metric_events(event_type)
        cohort = len({row["actor_sha256"] for row in rows})
        if cohort < min_cohort:
            return AggregateResult(event_type, "SUPPRESSED_SMALL_COHORT", cohort, None, {}, self._remaining)
        totals: dict[str, float] = {}
        counts: dict[str, int] = {}
        for row in rows:
            import json
            for key, value in json.loads(row["numeric_json"]).items():
                totals[key] = totals.get(key, 0.0) + float(value)
                counts[key] = counts.get(key, 0) + 1
        numeric = {
            key: (total / counts[key] if key == "latencyMs" else total)
            for key, total in totals.items()
        }
        return AggregateResult(event_type, "AVAILABLE", cohort, len(rows), numeric, self._remaining)

    @property
    def budget_remaining(self) -> int:
        return self._remaining


def metric_dictionary_contract() -> Mapping[str, Any]:
    definitions = [definition.__dict__ for definition in METRIC_DICTIONARY]
    return {
        "version": METRIC_DICTIONARY_VERSION,
        "definitions": definitions,
        "dictionarySha256": digest(definitions),
        "privacyClaim": "SMALL_COHORT_AND_QUERY_BUDGET_ONLY_NOT_DIFFERENTIAL_PRIVACY",
    }
