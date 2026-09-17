"""Append-only learning events, deterministic mastery, and recommendations."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Iterable, Sequence

from .assessment_finalization import LockedScoreSnapshot, validate_locked_score_snapshot
from .revision import AssistanceDepth
from .submission import digest


LEARNING_EVENT_VERSION = "learning-event-v1"
MASTERY_POLICY_VERSION = "mastery-policy-v1"
RECOMMENDATION_POLICY_VERSION = "memory-recommendations-v1"


class LearningMemoryError(ValueError):
    """Learning-event provenance, isolation, or projection contract failed."""


class LearningEventKind(str, Enum):
    DEMONSTRATION = "DEMONSTRATION"
    CORRECTION = "CORRECTION"
    DELETE = "DELETE"


class LearningFamily(str, Enum):
    EXPRESSION = "EXPRESSION"
    GRAMMAR = "GRAMMAR"
    ARGUMENT = "ARGUMENT"
    TOPIC = "TOPIC"
    ERROR = "ERROR"
    SCORE_PROFILE = "SCORE_PROFILE"


def _iso_utc(value: datetime | str) -> str:
    try:
        parsed = value if isinstance(value, datetime) else datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (TypeError, ValueError) as exc:
        raise LearningMemoryError("Learning event timestamp is invalid.") from exc
    if parsed.tzinfo is None:
        raise LearningMemoryError("Learning event timestamp must include a timezone.")
    return parsed.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


@dataclass(frozen=True)
class LearningEvent:
    event_id: str
    kind: LearningEventKind
    learner_id: str
    tenant_id: str
    family: LearningFamily
    skill_key: str
    topic: str
    correct: bool | None
    demonstrated: bool
    evidence_ids: tuple[str, ...]
    essay_version_id: str
    revision_ledger_sha256: str | None
    assistance_depth: AssistanceDepth
    occurred_at: str
    target_event_id: str | None
    event_sha256: str
    version: str = LEARNING_EVENT_VERSION

    @classmethod
    def create(
        cls,
        *,
        learner_id: str,
        tenant_id: str,
        family: LearningFamily,
        skill_key: str,
        topic: str,
        correct: bool | None,
        demonstrated: bool,
        evidence_ids: Sequence[str],
        essay_version_id: str,
        assistance_depth: AssistanceDepth,
        occurred_at: datetime | str,
        revision_ledger_sha256: str | None = None,
        kind: LearningEventKind = LearningEventKind.DEMONSTRATION,
        target_event_id: str | None = None,
    ) -> "LearningEvent":
        evidence = tuple(sorted({str(value) for value in evidence_ids if str(value)}))
        timestamp = _iso_utc(occurred_at)
        if (
            not learner_id
            or not tenant_id
            or not isinstance(family, LearningFamily)
            or not skill_key
            or not topic
            or not isinstance(assistance_depth, AssistanceDepth)
            or not essay_version_id
            or not evidence
        ):
            raise LearningMemoryError("Learning events require complete provenance and evidence.")
        if kind is LearningEventKind.DEMONSTRATION and target_event_id is not None:
            raise LearningMemoryError("A demonstration cannot target another event.")
        if kind in {LearningEventKind.CORRECTION, LearningEventKind.DELETE} and not target_event_id:
            raise LearningMemoryError("Correction/delete events require a target event.")
        if kind is LearningEventKind.DELETE and (correct is not None or demonstrated):
            raise LearningMemoryError("Delete events cannot claim demonstrated behavior.")
        identity = {
            "version": LEARNING_EVENT_VERSION,
            "kind": kind.value,
            "learnerId": learner_id,
            "tenantId": tenant_id,
            "family": family.value,
            "skillKey": skill_key,
            "topic": topic,
            "correct": correct,
            "demonstrated": demonstrated,
            "evidenceIds": list(evidence),
            "essayVersionId": essay_version_id,
            "revisionLedgerSha256": revision_ledger_sha256,
            "assistanceDepth": assistance_depth.value,
            "occurredAt": timestamp,
            "targetEventId": target_event_id,
        }
        sha = digest(identity)
        return cls(
            "learning-event:" + sha,
            kind,
            learner_id,
            tenant_id,
            family,
            skill_key,
            topic,
            correct,
            demonstrated,
            evidence,
            essay_version_id,
            revision_ledger_sha256,
            assistance_depth,
            timestamp,
            target_event_id,
            sha,
        )

    @classmethod
    def delete(cls, target: "LearningEvent", *, occurred_at: datetime | str) -> "LearningEvent":
        return cls.create(
            learner_id=target.learner_id,
            tenant_id=target.tenant_id,
            family=target.family,
            skill_key=target.skill_key,
            topic=target.topic,
            correct=None,
            demonstrated=False,
            evidence_ids=target.evidence_ids,
            essay_version_id=target.essay_version_id,
            assistance_depth=AssistanceDepth.UNKNOWN,
            occurred_at=occurred_at,
            revision_ledger_sha256=target.revision_ledger_sha256,
            kind=LearningEventKind.DELETE,
            target_event_id=target.event_id,
        )

    def content(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "eventId": self.event_id,
            "kind": self.kind.value,
            "learnerId": self.learner_id,
            "tenantId": self.tenant_id,
            "family": self.family.value,
            "skillKey": self.skill_key,
            "topic": self.topic,
            "correct": self.correct,
            "demonstrated": self.demonstrated,
            "evidenceIds": list(self.evidence_ids),
            "essayVersionId": self.essay_version_id,
            "revisionLedgerSha256": self.revision_ledger_sha256,
            "assistanceDepth": self.assistance_depth.value,
            "occurredAt": self.occurred_at,
            "targetEventId": self.target_event_id,
            "eventSha256": self.event_sha256,
            "directScoreAuthority": False,
        }

    def demonstration_key(self) -> tuple[Any, ...]:
        return (
            self.family,
            self.skill_key,
            self.topic,
            self.essay_version_id,
            self.revision_ledger_sha256,
            self.evidence_ids,
        )


class LearningEventStore:
    """Append-only isolated raw events with rebuildable effective views."""

    def __init__(self, events: Iterable[LearningEvent] = ()) -> None:
        self._events: list[LearningEvent] = []
        self._by_id: dict[str, LearningEvent] = {}
        for event in events:
            self.append(event)

    def append(self, event: LearningEvent) -> bool:
        if not isinstance(event, LearningEvent) or digest({
            key: value for key, value in event.content().items()
            if key not in {"eventId", "eventSha256", "directScoreAuthority"}
        }) != event.event_sha256:
            raise LearningMemoryError("Learning event hash is invalid.")
        existing = self._by_id.get(event.event_id)
        if existing is not None:
            if existing != event:
                raise LearningMemoryError("Learning event identity collision.")
            return False
        if event.target_event_id:
            target = self._by_id.get(event.target_event_id)
            if target is None:
                raise LearningMemoryError("Learning event target does not exist.")
            if target.learner_id != event.learner_id or target.tenant_id != event.tenant_id:
                raise LearningMemoryError("Learning event target crosses learner or tenant boundaries.")
            if _parse_timestamp(event.occurred_at) < _parse_timestamp(target.occurred_at):
                raise LearningMemoryError("Correction/delete events cannot precede their target.")
        self._events.append(event)
        self._by_id[event.event_id] = event
        return True

    def export(self, *, learner_id: str, tenant_id: str) -> tuple[LearningEvent, ...]:
        return tuple(sorted(
            (
                event for event in self._events
                if event.learner_id == learner_id and event.tenant_id == tenant_id
            ),
            key=lambda event: (event.occurred_at, event.event_id),
        ))

    def effective_events(self, *, learner_id: str, tenant_id: str) -> tuple[LearningEvent, ...]:
        active: dict[str, LearningEvent] = {}
        for event in self.export(learner_id=learner_id, tenant_id=tenant_id):
            if event.kind is LearningEventKind.DELETE:
                active.pop(event.target_event_id or "", None)
            elif event.kind is LearningEventKind.CORRECTION:
                active.pop(event.target_event_id or "", None)
                active[event.event_id] = event
            else:
                active[event.event_id] = event
        return tuple(sorted(active.values(), key=lambda event: (event.occurred_at, event.event_id)))

    def append_delete(self, target_event_id: str, *, occurred_at: datetime | str) -> LearningEvent:
        target = self._by_id.get(target_event_id)
        if target is None:
            raise LearningMemoryError("Cannot delete an unknown learning event.")
        event = LearningEvent.delete(target, occurred_at=occurred_at)
        self.append(event)
        return event


class MasteryState(str, Enum):
    EXPOSURE = "EXPOSURE"
    GUIDED_USE = "GUIDED_USE"
    INDEPENDENT_USE = "INDEPENDENT_USE"
    CROSS_TOPIC_TRANSFER = "CROSS_TOPIC_TRANSFER"
    STABLE_MASTERY = "STABLE_MASTERY"


_MASTERY_ORDER = tuple(MasteryState)
_GUIDED_ASSISTANCE = frozenset({
    AssistanceDepth.LOCATION,
    AssistanceDepth.CATEGORY,
    AssistanceDepth.GUIDANCE,
})


@dataclass(frozen=True)
class MasteryProjection:
    learner_id: str
    tenant_id: str
    family: LearningFamily
    skill_key: str
    state: MasteryState
    state_reason: str
    evidence_event_ids: tuple[str, ...]
    independent_event_ids: tuple[str, ...]
    independent_topics: tuple[str, ...]
    regression: bool
    projection_sha256: str
    policy_version: str = MASTERY_POLICY_VERSION

    def content(self, *, include_hash: bool = True) -> dict[str, Any]:
        value = {
            "policyVersion": self.policy_version,
            "learnerId": self.learner_id,
            "tenantId": self.tenant_id,
            "family": self.family.value,
            "skillKey": self.skill_key,
            "state": self.state.value,
            "stateReason": self.state_reason,
            "evidenceEventIds": list(self.evidence_event_ids),
            "independentEventIds": list(self.independent_event_ids),
            "independentTopics": list(self.independent_topics),
            "regression": self.regression,
            "directScoreAuthority": False,
        }
        if include_hash:
            value["projectionSha256"] = self.projection_sha256
        return value


def _parse_timestamp(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def replay_mastery(
    store: LearningEventStore,
    *,
    learner_id: str,
    tenant_id: str,
) -> tuple[MasteryProjection, ...]:
    grouped: dict[tuple[LearningFamily, str], list[LearningEvent]] = {}
    for event in store.effective_events(learner_id=learner_id, tenant_id=tenant_id):
        grouped.setdefault((event.family, event.skill_key), []).append(event)
    projections: list[MasteryProjection] = []
    for (family, skill_key), events in sorted(grouped.items(), key=lambda item: (item[0][0].value, item[0][1])):
        ordered = sorted(events, key=lambda event: (event.occurred_at, event.event_id))
        unique: dict[tuple[Any, ...], LearningEvent] = {}
        for event in ordered:
            unique[event.demonstration_key()] = event
        demonstrations = sorted(unique.values(), key=lambda event: (event.occurred_at, event.event_id))
        guided = [
            event for event in demonstrations
            if event.demonstrated and event.correct is True and event.assistance_depth in _GUIDED_ASSISTANCE
            and event.family is not LearningFamily.SCORE_PROFILE
        ]
        independent = [
            event for event in demonstrations
            if event.demonstrated and event.correct is True and event.assistance_depth is AssistanceDepth.NONE
            and event.family is not LearningFamily.SCORE_PROFILE
        ]
        topics = {event.topic for event in independent}
        state = MasteryState.EXPOSURE
        reason = "Only exposure, uncertain provenance, examples, rewrites, or incorrect use is available."
        if guided:
            state = MasteryState.GUIDED_USE
            reason = "At least one correct student use with bounded guidance is demonstrated."
        if independent:
            state = MasteryState.INDEPENDENT_USE
            reason = "At least one distinct correct independent use is demonstrated."
        if len(independent) >= 2 and len(topics) >= 2:
            state = MasteryState.CROSS_TOPIC_TRANSFER
            reason = "Distinct correct independent uses are demonstrated across topics."
        if len(independent) >= 3 and len(topics) >= 2:
            span = _parse_timestamp(independent[-1].occurred_at) - _parse_timestamp(independent[0].occurred_at)
            if span.days >= 7:
                state = MasteryState.STABLE_MASTERY
                reason = "Repeated independent cross-topic use is demonstrated across the stability window."
        eligible_behavior = [
            event for event in demonstrations
            if event.demonstrated and event.assistance_depth is AssistanceDepth.NONE
            and event.family is not LearningFamily.SCORE_PROFILE
        ]
        regression = bool(eligible_behavior and eligible_behavior[-1].correct is False)
        if regression and state is not MasteryState.EXPOSURE:
            state = _MASTERY_ORDER[max(0, _MASTERY_ORDER.index(state) - 1)]
            reason = "A later independent incorrect use records regression and lowers the projection one state."
        identity = {
            "policyVersion": MASTERY_POLICY_VERSION,
            "learnerId": learner_id,
            "tenantId": tenant_id,
            "family": family.value,
            "skillKey": skill_key,
            "state": state.value,
            "stateReason": reason,
            "evidenceEventIds": [event.event_id for event in demonstrations],
            "independentEventIds": [event.event_id for event in independent],
            "independentTopics": sorted(topics),
            "regression": regression,
        }
        projections.append(MasteryProjection(
            learner_id,
            tenant_id,
            family,
            skill_key,
            state,
            reason,
            tuple(identity["evidenceEventIds"]),
            tuple(identity["independentEventIds"]),
            tuple(identity["independentTopics"]),
            regression,
            digest(identity),
        ))
    return tuple(projections)


@dataclass(frozen=True)
class PracticeNeed:
    need_id: str
    family: LearningFamily
    skill_key: str
    topic: str
    evidence_ids: tuple[str, ...]
    priority: int

    @classmethod
    def create(
        cls,
        family: LearningFamily,
        skill_key: str,
        topic: str,
        evidence_ids: Sequence[str],
        *,
        priority: int = 0,
    ) -> "PracticeNeed":
        evidence = tuple(sorted({str(value) for value in evidence_ids if str(value)}))
        if not skill_key or not topic or not evidence or isinstance(priority, bool) or priority < 0:
            raise LearningMemoryError("Practice needs require current evidence and a valid priority.")
        payload = {
            "family": family.value,
            "skillKey": skill_key,
            "topic": topic,
            "evidenceIds": list(evidence),
        }
        return cls("practice-need:" + digest(payload), family, skill_key, topic, evidence, priority)


class RecommendationKind(str, Enum):
    CURRENT_EVIDENCE = "CURRENT_EVIDENCE"
    RECURRENCE = "RECURRENCE"
    TRANSFER_GAP = "TRANSFER_GAP"


@dataclass(frozen=True)
class LearningRecommendation:
    recommendation_id: str
    kind: RecommendationKind
    family: LearningFamily
    skill_key: str
    topic: str
    priority: int
    reason: str
    current_evidence_ids: tuple[str, ...]
    mastery_projection_sha256: str | None

    def content(self) -> dict[str, Any]:
        return {
            "recommendationId": self.recommendation_id,
            "kind": self.kind.value,
            "family": self.family.value,
            "skillKey": self.skill_key,
            "topic": self.topic,
            "priority": self.priority,
            "reason": self.reason,
            "currentEvidenceIds": list(self.current_evidence_ids),
            "masteryProjectionSha256": self.mastery_projection_sha256,
        }


@dataclass(frozen=True)
class RecommendationPlan:
    locked_score_sha256: str
    recommendations: tuple[LearningRecommendation, ...]
    plan_sha256: str
    policy_version: str = RECOMMENDATION_POLICY_VERSION

    def content(self, *, include_hash: bool = True) -> dict[str, Any]:
        value = {
            "policyVersion": self.policy_version,
            "lockedScoreSha256": self.locked_score_sha256,
            "recommendations": [item.content() for item in self.recommendations],
            "scoringInput": False,
        }
        if include_hash:
            value["planSha256"] = self.plan_sha256
        return value


def build_memory_recommendations(
    snapshot: LockedScoreSnapshot,
    needs: Sequence[PracticeNeed],
    projections: Sequence[MasteryProjection],
) -> RecommendationPlan:
    validate_locked_score_snapshot(snapshot)
    before = snapshot.content()
    projection_by_key = {(item.family, item.skill_key): item for item in projections}
    recommendations: list[LearningRecommendation] = []
    current_keys: set[tuple[LearningFamily, str]] = set()
    for need in needs:
        key = (need.family, need.skill_key)
        current_keys.add(key)
        projection = projection_by_key.get(key)
        priority = 100 + need.priority
        reason = "Current validated Candidate Script evidence requires practice."
        if projection is not None:
            if projection.state in {MasteryState.EXPOSURE, MasteryState.GUIDED_USE}:
                priority += 20
                reason = "Current evidence confirms a recurring need; memory remains below independent use."
            elif projection.state is MasteryState.INDEPENDENT_USE:
                priority += 10
                reason = "Current evidence takes priority and shows a cross-topic transfer gap."
            elif projection.state is MasteryState.STABLE_MASTERY:
                reason = "Current evidence overrides the historical stable-mastery projection."
        identity = {
            "policyVersion": RECOMMENDATION_POLICY_VERSION,
            "lockedScoreSha256": snapshot.snapshot_sha256,
            "kind": RecommendationKind.CURRENT_EVIDENCE.value,
            "needId": need.need_id,
            "priority": priority,
            "reason": reason,
            "projectionSha256": projection.projection_sha256 if projection else None,
        }
        recommendations.append(LearningRecommendation(
            "learning-recommendation:" + digest(identity),
            RecommendationKind.CURRENT_EVIDENCE,
            need.family,
            need.skill_key,
            need.topic,
            priority,
            reason,
            need.evidence_ids,
            projection.projection_sha256 if projection else None,
        ))
    for projection in projections:
        key = (projection.family, projection.skill_key)
        if key in current_keys or projection.state not in {MasteryState.GUIDED_USE, MasteryState.INDEPENDENT_USE}:
            continue
        kind = RecommendationKind.RECURRENCE if projection.state is MasteryState.GUIDED_USE else RecommendationKind.TRANSFER_GAP
        reason = (
            "Historical guided use should be demonstrated independently."
            if kind is RecommendationKind.RECURRENCE
            else "Independent use should be demonstrated in a different topic."
        )
        identity = {
            "policyVersion": RECOMMENDATION_POLICY_VERSION,
            "lockedScoreSha256": snapshot.snapshot_sha256,
            "kind": kind.value,
            "projectionSha256": projection.projection_sha256,
        }
        recommendations.append(LearningRecommendation(
            "learning-recommendation:" + digest(identity),
            kind,
            projection.family,
            projection.skill_key,
            projection.independent_topics[-1] if projection.independent_topics else "general",
            40,
            reason,
            (),
            projection.projection_sha256,
        ))
    recommendations.sort(key=lambda item: (-item.priority, item.recommendation_id))
    if snapshot.content() != before:
        raise LearningMemoryError("Memory recommendations attempted to mutate the Locked Score.")
    partial = RecommendationPlan(snapshot.snapshot_sha256, tuple(recommendations), "")
    return RecommendationPlan(**{
        **partial.__dict__,
        "plan_sha256": digest(partial.content(include_hash=False)),
    })
