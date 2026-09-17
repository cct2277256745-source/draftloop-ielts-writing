"""Deterministic sufficiency/conflict gate for advisory empirical evidence."""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Mapping, Sequence

from .rag_retrieval import RetrievalEvidenceItem, RetrievalEvidencePack
from .submission import digest


RAG_EVIDENCE_GATE_VERSION = "rag-evidence-gate-v1"


class EvidenceGateError(ValueError):
    """An evidence pack or gate context violates the authority contract."""


class EvidenceGateStatus(str, Enum):
    SUFFICIENT = "SUFFICIENT"
    INSUFFICIENT = "INSUFFICIENT"
    CONFLICT = "CONFLICT"


class EvidenceRejectionReason(str, Enum):
    AUTHORITY_INVALID = "AUTHORITY_INVALID"
    RIGHTS_INVALID = "RIGHTS_INVALID"
    PROVENANCE_MISSING = "PROVENANCE_MISSING"
    CRITERION_MISMATCH = "CRITERION_MISMATCH"
    FEATURE_MISMATCH = "FEATURE_MISMATCH"
    OFFICIAL_RUBRIC_CONFLICT = "OFFICIAL_RUBRIC_CONFLICT"
    CURRENT_STUDENT_EVIDENCE_CONFLICT = "CURRENT_STUDENT_EVIDENCE_CONFLICT"


@dataclass(frozen=True)
class EvidenceGateContext:
    criterion: str
    relevant_features: tuple[str, ...]
    allowed_rights: tuple[str, ...]
    allowed_usage: tuple[str, ...]
    official_conflict_object_ids: tuple[str, ...] = ()
    student_conflict_object_ids: tuple[str, ...] = ()
    minimum_items: int = 2

    def __post_init__(self) -> None:
        if (
            not self.criterion
            or not self.allowed_rights
            or not self.allowed_usage
            or isinstance(self.minimum_items, bool)
            or not 1 <= self.minimum_items <= 5
        ):
            raise EvidenceGateError("The evidence-gate context is invalid.")


@dataclass(frozen=True)
class ConsumerEvidenceItem:
    object_id: str
    object_type: str
    criterion: str | None
    features: tuple[str, ...]
    text: str = field(repr=False)
    provenance: str = field(repr=False)
    source_artifact_id: str = field(repr=False)
    rights: str = field(repr=False)
    usage: str = field(repr=False)
    direct_score_authority: bool = False

    def content(self) -> dict[str, Any]:
        return {
            "id": self.object_id,
            "type": self.object_type,
            "criterion": self.criterion,
            "features": list(self.features),
            "text": self.text,
            "provenance": self.provenance,
            "sourceArtifactId": self.source_artifact_id,
            "rights": self.rights,
            "usage": self.usage,
            "directScoreAuthority": False,
            "retrievalScoreExposedToScoring": False,
        }


@dataclass(frozen=True)
class EvidenceItemDecision:
    object_id: str
    accepted: bool
    reasons: tuple[EvidenceRejectionReason, ...]

    def content(self) -> dict[str, Any]:
        return {
            "objectId": self.object_id,
            "accepted": self.accepted,
            "reasons": [reason.value for reason in self.reasons],
        }


@dataclass(frozen=True)
class EvidenceGateDecision:
    status: EvidenceGateStatus
    accepted_items: tuple[ConsumerEvidenceItem, ...]
    item_decisions: tuple[EvidenceItemDecision, ...]
    source_pack_sha256: str
    decision_sha256: str
    version: str = RAG_EVIDENCE_GATE_VERSION

    def content(self, *, include_hash: bool = True) -> dict[str, Any]:
        value = {
            "version": self.version,
            "status": self.status.value,
            "sourcePackSha256": self.source_pack_sha256,
            "directScoreAuthority": False,
            "acceptedItems": [item.content() for item in self.accepted_items],
            "itemDecisions": [item.content() for item in self.item_decisions],
        }
        if include_hash:
            value["decisionSha256"] = self.decision_sha256
        return value


def _safe_item(item: RetrievalEvidenceItem) -> ConsumerEvidenceItem:
    return ConsumerEvidenceItem(
        object_id=item.object_id,
        object_type=item.object_type,
        criterion=item.criterion,
        features=item.features,
        text=item.text,
        provenance=item.provenance,
        source_artifact_id=item.source_artifact_id,
        rights=item.rights,
        usage=item.usage,
        direct_score_authority=False,
    )


def gate_retrieval_evidence(
    pack: RetrievalEvidencePack,
    context: EvidenceGateContext,
) -> EvidenceGateDecision:
    if not isinstance(pack, RetrievalEvidencePack) or not isinstance(context, EvidenceGateContext):
        raise EvidenceGateError("The evidence gate requires a typed pack and context.")
    if digest(pack.content(include_hash=False)) != pack.pack_sha256:
        raise EvidenceGateError("The retrieval evidence pack hash is invalid.")

    decisions: list[EvidenceItemDecision] = []
    accepted: list[ConsumerEvidenceItem] = []
    conflict = False
    official_conflicts = set(context.official_conflict_object_ids)
    student_conflicts = set(context.student_conflict_object_ids)
    relevant = set(context.relevant_features)
    for item in pack.items:
        reasons: list[EvidenceRejectionReason] = []
        if item.direct_score_authority is not False:
            reasons.append(EvidenceRejectionReason.AUTHORITY_INVALID)
        if item.rights not in context.allowed_rights or item.usage not in context.allowed_usage:
            reasons.append(EvidenceRejectionReason.RIGHTS_INVALID)
        if not item.provenance or not item.source_artifact_id:
            reasons.append(EvidenceRejectionReason.PROVENANCE_MISSING)
        if item.criterion is not None and item.criterion != context.criterion:
            reasons.append(EvidenceRejectionReason.CRITERION_MISMATCH)
        if relevant and not relevant.intersection(item.features):
            reasons.append(EvidenceRejectionReason.FEATURE_MISMATCH)
        if item.object_id in official_conflicts:
            reasons.append(EvidenceRejectionReason.OFFICIAL_RUBRIC_CONFLICT)
            conflict = True
        if item.object_id in student_conflicts:
            reasons.append(EvidenceRejectionReason.CURRENT_STUDENT_EVIDENCE_CONFLICT)
            conflict = True
        item_decision = EvidenceItemDecision(item.object_id, not reasons, tuple(reasons))
        decisions.append(item_decision)
        if not reasons:
            accepted.append(_safe_item(item))

    if conflict:
        status = EvidenceGateStatus.CONFLICT
        accepted = []
    elif len(accepted) < context.minimum_items:
        status = EvidenceGateStatus.INSUFFICIENT
        accepted = []
    else:
        status = EvidenceGateStatus.SUFFICIENT
    provisional = EvidenceGateDecision(
        status=status,
        accepted_items=tuple(accepted),
        item_decisions=tuple(decisions),
        source_pack_sha256=pack.pack_sha256,
        decision_sha256="",
    )
    return EvidenceGateDecision(
        status=provisional.status,
        accepted_items=provisional.accepted_items,
        item_decisions=provisional.item_decisions,
        source_pack_sha256=provisional.source_pack_sha256,
        decision_sha256=digest(provisional.content(include_hash=False)),
    )
