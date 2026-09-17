"""Evidence-constrained full coaching derived only after score locking."""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any, Mapping, Sequence

from .assessment_finalization import (
    FinalizationBundle,
    LockedScoreSnapshot,
    validate_finalization_bundle,
    validate_locked_score_snapshot,
)
from .core_diagnosis import CoreDiagnosis
from .rag_evidence import EvidenceGateDecision, EvidenceGateStatus
from .submission import digest


FULL_COACHING_VERSION = "full-coaching-v1"


class CoachingContractError(ValueError):
    """A coaching draft violates score, evidence, or authority boundaries."""


class CoachingStatus(str, Enum):
    COMPLETE = "COMPLETE"
    PARTIAL = "PARTIAL"


class CoachingSection(str, Enum):
    BOTTLENECK = "BOTTLENECK"
    KEEP = "KEEP"
    MIND_MAP = "MIND_MAP"
    PARAGRAPH_DIAGNOSIS = "PARAGRAPH_DIAGNOSIS"
    CORRECTION = "CORRECTION"
    MINIMAL_IMPROVED_PARAGRAPH = "MINIMAL_IMPROVED_PARAGRAPH"
    TOPIC_LEARNING = "TOPIC_LEARNING"
    NEXT_ACTION = "NEXT_ACTION"


class CoachingAuthority(str, Enum):
    CURRENT_STUDENT_EVIDENCE = "CURRENT_STUDENT_EVIDENCE"
    RAG_EVIDENCE = "RAG_EVIDENCE"
    TOPIC_KB = "TOPIC_KB"


@dataclass(frozen=True)
class LearningBudget:
    max_total_items: int = 24
    max_bottlenecks: int = 3
    max_keep_items: int = 3
    max_mind_map_items: int = 6
    max_paragraph_diagnoses: int = 6
    max_corrections: int = 8
    max_minimal_paragraphs: int = 2
    max_topic_items: int = 6
    max_next_actions: int = 3

    def __post_init__(self) -> None:
        values = tuple(self.content().values())
        if any(isinstance(value, bool) or not isinstance(value, int) or value < 0 for value in values):
            raise CoachingContractError("Learning budget values must be non-negative integers.")
        if self.max_total_items < 1:
            raise CoachingContractError("The learning budget must allow at least one item.")

    def content(self) -> dict[str, int]:
        return {
            "maxTotalItems": self.max_total_items,
            "maxBottlenecks": self.max_bottlenecks,
            "maxKeepItems": self.max_keep_items,
            "maxMindMapItems": self.max_mind_map_items,
            "maxParagraphDiagnoses": self.max_paragraph_diagnoses,
            "maxCorrections": self.max_corrections,
            "maxMinimalParagraphs": self.max_minimal_paragraphs,
            "maxTopicItems": self.max_topic_items,
            "maxNextActions": self.max_next_actions,
        }

    def section_limit(self, section: CoachingSection) -> int:
        return {
            CoachingSection.BOTTLENECK: self.max_bottlenecks,
            CoachingSection.KEEP: self.max_keep_items,
            CoachingSection.MIND_MAP: self.max_mind_map_items,
            CoachingSection.PARAGRAPH_DIAGNOSIS: self.max_paragraph_diagnoses,
            CoachingSection.CORRECTION: self.max_corrections,
            CoachingSection.MINIMAL_IMPROVED_PARAGRAPH: self.max_minimal_paragraphs,
            CoachingSection.TOPIC_LEARNING: self.max_topic_items,
            CoachingSection.NEXT_ACTION: self.max_next_actions,
        }[section]


@dataclass(frozen=True)
class CoachingItemDraft:
    section: CoachingSection
    criterion: str
    text_en: str
    text_zh: str
    finding_ids: tuple[str, ...] = ()
    observation_ids: tuple[str, ...] = ()
    claim_ids: tuple[str, ...] = ()
    locator_ids: tuple[str, ...] = ()
    source_authority: CoachingAuthority = CoachingAuthority.CURRENT_STUDENT_EVIDENCE
    source_ids: tuple[str, ...] = ()
    priority: int = 0


@dataclass(frozen=True)
class CoachingItem:
    item_id: str
    section: CoachingSection
    criterion: str
    text_en: str
    text_zh: str
    finding_ids: tuple[str, ...]
    observation_ids: tuple[str, ...]
    claim_ids: tuple[str, ...]
    locator_ids: tuple[str, ...]
    source_authority: CoachingAuthority
    source_ids: tuple[str, ...]
    priority: int

    def content(self) -> dict[str, Any]:
        return {
            "itemId": self.item_id,
            "section": self.section.value,
            "criterion": self.criterion,
            "textEn": self.text_en,
            "textZh": self.text_zh,
            "findingIds": list(self.finding_ids),
            "observationIds": list(self.observation_ids),
            "claimIds": list(self.claim_ids),
            "locatorIds": list(self.locator_ids),
            "sourceAuthority": self.source_authority.value,
            "sourceIds": list(self.source_ids),
            "directScoreAuthority": False,
            "priority": self.priority,
        }


@dataclass(frozen=True)
class FullCoaching:
    task_type: str
    locked_score_sha256: str
    finalization_bundle_sha256: str
    core_diagnosis_sha256: str
    strongest_criterion: str
    status: CoachingStatus
    omitted_sections: tuple[CoachingSection, ...]
    items: tuple[CoachingItem, ...]
    learning_budget: LearningBudget
    rag_evidence_gate_sha256: str | None
    topic_pack_sha256: str | None
    coaching_sha256: str
    version: str = FULL_COACHING_VERSION

    def content(self, *, include_hash: bool = True) -> dict[str, Any]:
        value = {
            "version": self.version,
            "taskType": self.task_type,
            "lockedScoreSha256": self.locked_score_sha256,
            "finalizationBundleSha256": self.finalization_bundle_sha256,
            "coreDiagnosisSha256": self.core_diagnosis_sha256,
            "strongestCriterion": self.strongest_criterion,
            "status": self.status.value,
            "omittedSections": [section.value for section in self.omitted_sections],
            "items": [item.content() for item in self.items],
            "learningBudget": self.learning_budget.content(),
            "ragEvidenceGateSha256": self.rag_evidence_gate_sha256,
            "topicPackSha256": self.topic_pack_sha256,
            "directScoreAuthority": False,
        }
        if include_hash:
            value["coachingSha256"] = self.coaching_sha256
        return value

    def items_for(self, section: CoachingSection) -> tuple[CoachingItem, ...]:
        return tuple(item for item in self.items if item.section is section)


_REQUIRED_COMPLETE_SECTIONS = frozenset({
    CoachingSection.BOTTLENECK,
    CoachingSection.KEEP,
    CoachingSection.MIND_MAP,
    CoachingSection.PARAGRAPH_DIAGNOSIS,
    CoachingSection.CORRECTION,
    CoachingSection.MINIMAL_IMPROVED_PARAGRAPH,
    CoachingSection.NEXT_ACTION,
})
_PROHIBITED_CLAIMS = (
    "official score",
    "official band",
    "examiner-certified",
    "former examiner",
    "官方成绩",
    "官方分数",
    "前考官",
)


def _known_evidence(bundle: FinalizationBundle) -> dict[str, dict[str, set[str]]]:
    known: dict[str, dict[str, set[str]]] = {}
    for assessment in bundle.assessments:
        criterion_known = known.setdefault(assessment.criterion, {
            "finding": set(),
            "observation": set(),
            "claim": set(),
            "locator": set(),
        })
        for finding in assessment.findings:
            finding_id = str(finding.get("findingId", ""))
            if finding_id:
                criterion_known["finding"].add(finding_id)
            for reference in finding.get("evidenceRefs", ()):
                if not isinstance(reference, Mapping):
                    continue
                observation_id = str(reference.get("observationId", ""))
                if observation_id:
                    criterion_known["observation"].add(observation_id)
                for value in reference.get("claimIds", ()):
                    criterion_known["claim"].add(str(value))
                for value in reference.get("locatorIds", ()):
                    criterion_known["locator"].add(str(value))
    return known


def _validate_lineage(
    snapshot: LockedScoreSnapshot,
    bundle: FinalizationBundle,
    diagnosis: CoreDiagnosis,
) -> None:
    validate_locked_score_snapshot(snapshot)
    validate_finalization_bundle(bundle)
    if snapshot.finalization_bundle_sha256 != bundle.bundle_sha256:
        raise CoachingContractError("Coaching inputs do not share finalization lineage.")
    if diagnosis.locked_score_sha256 != snapshot.snapshot_sha256:
        raise CoachingContractError("Coaching diagnosis does not share Locked Score lineage.")
    if digest(diagnosis.content(include_hash=False)) != diagnosis.diagnosis_sha256:
        raise CoachingContractError("Core Diagnosis hash is invalid.")


def _validated_item(
    draft: CoachingItemDraft,
    known: Mapping[str, Mapping[str, set[str]]],
    accepted_rag_ids: set[str],
    accepted_topic_ids: set[str],
) -> CoachingItem:
    if not isinstance(draft.section, CoachingSection) or not isinstance(draft.source_authority, CoachingAuthority):
        raise CoachingContractError("Coaching item enum values are invalid.")
    if not draft.criterion or not draft.text_en.strip() or not draft.text_zh.strip():
        raise CoachingContractError("Coaching items require criterion and localized text.")
    criterion_known = known.get(draft.criterion)
    if criterion_known is None:
        raise CoachingContractError("Coaching item criterion is not part of the locked task.")
    combined_text = (draft.text_en + " " + draft.text_zh).casefold()
    if any(claim in combined_text for claim in _PROHIBITED_CLAIMS):
        raise CoachingContractError("Coaching cannot claim official or examiner authority.")
    finding_ids = tuple(sorted(set(draft.finding_ids)))
    observation_ids = tuple(sorted(set(draft.observation_ids)))
    claim_ids = tuple(sorted(set(draft.claim_ids)))
    locator_ids = tuple(sorted(set(draft.locator_ids)))
    if not any((finding_ids, observation_ids, claim_ids, locator_ids)):
        raise CoachingContractError("Every coaching item requires validated current evidence.")
    checks = (
        (finding_ids, criterion_known["finding"]),
        (observation_ids, criterion_known["observation"]),
        (claim_ids, criterion_known["claim"]),
        (locator_ids, criterion_known["locator"]),
    )
    if any(not set(values).issubset(allowed) for values, allowed in checks):
        raise CoachingContractError("A coaching item references unsupported evidence.")
    source_ids = tuple(sorted(set(draft.source_ids)))
    if draft.source_authority is CoachingAuthority.RAG_EVIDENCE:
        if not source_ids or not set(source_ids).issubset(accepted_rag_ids):
            raise CoachingContractError("RAG coaching requires accepted consumer-safe evidence IDs.")
    elif draft.source_authority is CoachingAuthority.TOPIC_KB:
        if not source_ids or not set(source_ids).issubset(accepted_topic_ids):
            raise CoachingContractError("Topic coaching requires assets from the validated Topic Learning Pack.")
    elif source_ids:
        raise CoachingContractError("Current-evidence coaching cannot claim external source IDs.")
    if isinstance(draft.priority, bool) or not isinstance(draft.priority, int) or draft.priority < 0:
        raise CoachingContractError("Coaching priority must be a non-negative integer.")
    identity = {
        "section": draft.section.value,
        "criterion": draft.criterion,
        "textEn": " ".join(draft.text_en.split()),
        "textZh": " ".join(draft.text_zh.split()),
        "findingIds": list(finding_ids),
        "observationIds": list(observation_ids),
        "claimIds": list(claim_ids),
        "locatorIds": list(locator_ids),
        "sourceAuthority": draft.source_authority.value,
        "sourceIds": list(source_ids),
    }
    return CoachingItem(
        item_id="coaching-item:" + digest(identity),
        section=draft.section,
        criterion=draft.criterion,
        text_en=identity["textEn"],
        text_zh=identity["textZh"],
        finding_ids=finding_ids,
        observation_ids=observation_ids,
        claim_ids=claim_ids,
        locator_ids=locator_ids,
        source_authority=draft.source_authority,
        source_ids=source_ids,
        priority=draft.priority,
    )


def build_full_coaching(
    snapshot: LockedScoreSnapshot,
    bundle: FinalizationBundle,
    diagnosis: CoreDiagnosis,
    draft_items: Sequence[CoachingItemDraft],
    *,
    learning_budget: LearningBudget | None = None,
    rag_evidence: EvidenceGateDecision | None = None,
    topic_pack: Any | None = None,
) -> FullCoaching:
    """Validate and freeze a semantic coaching draft without exposing score writes."""
    _validate_lineage(snapshot, bundle, diagnosis)
    budget = learning_budget or LearningBudget()
    accepted_rag_ids: set[str] = set()
    rag_sha: str | None = None
    rag_available = False
    if rag_evidence is not None:
        if digest(rag_evidence.content(include_hash=False)) != rag_evidence.decision_sha256:
            raise CoachingContractError("RAG evidence decision hash is invalid.")
        rag_sha = rag_evidence.decision_sha256
        rag_available = rag_evidence.status is EvidenceGateStatus.SUFFICIENT
        if rag_available:
            accepted_rag_ids = {item.object_id for item in rag_evidence.accepted_items}
    accepted_topic_ids: set[str] = set()
    topic_sha: str | None = None
    if topic_pack is not None:
        try:
            topic_sha = topic_pack.pack_sha256
            if digest(topic_pack.content(include_hash=False)) != topic_sha:
                raise CoachingContractError("Topic Learning Pack hash is invalid.")
            accepted_topic_ids = {item.asset.asset_id for item in topic_pack.items}
        except AttributeError as exc:
            raise CoachingContractError("Topic Learning Pack type is invalid.") from exc

    known = _known_evidence(bundle)
    items: list[CoachingItem] = []
    seen: set[str] = set()
    rag_omitted = False
    for draft in draft_items:
        if draft.source_authority is CoachingAuthority.RAG_EVIDENCE and not rag_available:
            rag_omitted = True
            continue
        item = _validated_item(draft, known, accepted_rag_ids, accepted_topic_ids)
        if item.item_id in seen:
            continue
        seen.add(item.item_id)
        items.append(item)
    items.sort(key=lambda item: (item.section.value, -item.priority, item.item_id))
    if len(items) > budget.max_total_items:
        raise CoachingContractError("Coaching exceeds the total learning budget.")
    for section in CoachingSection:
        count = sum(item.section is section for item in items)
        if count > budget.section_limit(section):
            raise CoachingContractError(f"Coaching exceeds the {section.value} learning budget.")

    present = {item.section for item in items}
    relevant_required = set(_REQUIRED_COMPLETE_SECTIONS)
    if not diagnosis.bottlenecks:
        relevant_required -= {CoachingSection.BOTTLENECK, CoachingSection.NEXT_ACTION}
    if not diagnosis.keep_items:
        relevant_required.remove(CoachingSection.KEEP)
    omitted = tuple(sorted(relevant_required - present, key=lambda item: item.value))
    status = CoachingStatus.PARTIAL if omitted or rag_omitted else CoachingStatus.COMPLETE
    partial = FullCoaching(
        task_type=snapshot.task_type,
        locked_score_sha256=snapshot.snapshot_sha256,
        finalization_bundle_sha256=bundle.bundle_sha256,
        core_diagnosis_sha256=diagnosis.diagnosis_sha256,
        strongest_criterion=diagnosis.strongest_criterion,
        status=status,
        omitted_sections=omitted,
        items=tuple(items),
        learning_budget=budget,
        rag_evidence_gate_sha256=rag_sha,
        topic_pack_sha256=topic_sha,
        coaching_sha256="",
    )
    return FullCoaching(**{
        **partial.__dict__,
        "coaching_sha256": digest(partial.content(include_hash=False)),
    })


def validate_full_coaching(coaching: FullCoaching) -> None:
    if not isinstance(coaching, FullCoaching):
        raise CoachingContractError("Full Coaching type is invalid.")
    if digest(coaching.content(include_hash=False)) != coaching.coaching_sha256:
        raise CoachingContractError("Full Coaching hash is invalid.")
    if len(coaching.items) > coaching.learning_budget.max_total_items:
        raise CoachingContractError("Full Coaching learning budget was mutated.")
