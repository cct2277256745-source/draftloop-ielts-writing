"""Deterministic, evidence-linked diagnosis derived after score locking."""
from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, Mapping

from .assessment_finalization import (
    FinalizationBundle,
    LockedScoreSnapshot,
    validate_finalization_bundle,
    validate_locked_score_snapshot,
)
from .submission import digest


CORE_DIAGNOSIS_VERSION = "core-diagnosis-v1"
_CRITERION_ORDER = {"TA": 0, "TR": 0, "CC": 1, "LR": 2, "GRA": 3}


@dataclass(frozen=True)
class DiagnosisItem:
    item_id: str
    criterion: str
    kind: str
    priority: int
    official_claim_ids: tuple[str, ...]
    finding_ids: tuple[str, ...]
    observation_ids: tuple[str, ...]

    def content(self) -> dict[str, Any]:
        return {
            "itemId": self.item_id,
            "criterion": self.criterion,
            "kind": self.kind,
            "priority": self.priority,
            "officialClaimIds": list(self.official_claim_ids),
            "findingIds": list(self.finding_ids),
            "observationIds": list(self.observation_ids),
        }


@dataclass(frozen=True)
class CoreDiagnosis:
    locked_score_sha256: str
    strongest_criterion: str
    main_bottleneck: DiagnosisItem | None
    bottlenecks: tuple[DiagnosisItem, ...]
    keep_items: tuple[DiagnosisItem, ...]
    next_actions: tuple[DiagnosisItem, ...]
    target_band: float | None
    version: str
    diagnosis_sha256: str

    def content(self, *, include_hash: bool = True) -> dict[str, Any]:
        value = {
            "version": self.version,
            "lockedScoreSha256": self.locked_score_sha256,
            "strongestCriterion": self.strongest_criterion,
            "mainBottleneck": self.main_bottleneck.content() if self.main_bottleneck else None,
            "bottlenecks": [item.content() for item in self.bottlenecks],
            "keepItems": [item.content() for item in self.keep_items],
            "nextActions": [item.content() for item in self.next_actions],
            "targetBand": self.target_band,
        }
        if include_hash:
            value["diagnosisSha256"] = self.diagnosis_sha256
        return value


def _item(criterion: str, kind: str, priority: int, finding: Mapping[str, Any]) -> DiagnosisItem:
    claim_ids = tuple(sorted(str(value) for value in finding.get("officialClaimIds", ())))
    finding_id = str(finding.get("findingId", ""))
    observation_ids = tuple(sorted({
        str(ref.get("observationId", ""))
        for ref in finding.get("evidenceRefs", ())
        if isinstance(ref, Mapping) and ref.get("observationId")
    }))
    identity = {
        "criterion": criterion,
        "kind": kind,
        "officialClaimIds": list(claim_ids),
        "findingIds": [finding_id] if finding_id else [],
        "observationIds": list(observation_ids),
    }
    return DiagnosisItem(
        item_id="diagnosis-item:" + digest(identity),
        criterion=criterion,
        kind=kind,
        priority=priority,
        official_claim_ids=claim_ids,
        finding_ids=(finding_id,) if finding_id else (),
        observation_ids=observation_ids,
    )


def build_core_diagnosis(
    snapshot: LockedScoreSnapshot,
    bundle: FinalizationBundle,
    *,
    target_band: float | None = None,
) -> CoreDiagnosis:
    validate_locked_score_snapshot(snapshot)
    validate_finalization_bundle(bundle)
    if snapshot.finalization_bundle_sha256 != bundle.bundle_sha256:
        raise ValueError("Diagnosis inputs do not share the locked score lineage.")
    if target_band is not None and not 0.0 <= float(target_band) <= 9.0:
        raise ValueError("Diagnosis target band is invalid.")
    score_map = dict(snapshot.score_by_criterion())
    strongest = min(
        score_map,
        key=lambda criterion: (-score_map[criterion], _CRITERION_ORDER[criterion]),
    )
    bottlenecks: list[DiagnosisItem] = []
    keeps: list[DiagnosisItem] = []
    seen: set[str] = set()
    for assessment in bundle.assessments:
        for finding in assessment.findings:
            role = finding.get("role")
            fit = finding.get("claimFit")
            if role == "HIGHER_BAND_BOUNDARY" and fit in {"NOT_DEMONSTRATED", "CONTRADICTED"}:
                priority = 300 if fit == "CONTRADICTED" else 200
                priority += int((9.0 - score_map[assessment.criterion]) * 10)
                if target_band is not None:
                    priority += int(max(0.0, float(target_band) - score_map[assessment.criterion]) * 10)
                item = _item(assessment.criterion, "BOTTLENECK", priority, finding)
            elif role == "ANCHOR_SUPPORT" and fit in {"DEMONSTRATED", "PARTIALLY_DEMONSTRATED"}:
                item = _item(
                    assessment.criterion,
                    "KEEP_IT",
                    100 if fit == "DEMONSTRATED" else 50,
                    finding,
                )
            else:
                continue
            if item.item_id in seen:
                continue
            seen.add(item.item_id)
            (bottlenecks if item.kind == "BOTTLENECK" else keeps).append(item)
    bottlenecks.sort(key=lambda item: (-item.priority, _CRITERION_ORDER[item.criterion], item.item_id))
    keeps.sort(
        key=lambda item: (
            0 if item.criterion == strongest else 1,
            -item.priority,
            _CRITERION_ORDER[item.criterion],
            item.item_id,
        )
    )
    top_bottlenecks = tuple(bottlenecks[:3])
    next_actions = tuple(
        DiagnosisItem(
            item_id="next-action:" + item.item_id.split(":", 1)[-1],
            criterion=item.criterion,
            kind="NEXT_ACTION",
            priority=item.priority,
            official_claim_ids=item.official_claim_ids,
            finding_ids=item.finding_ids,
            observation_ids=item.observation_ids,
        )
        for item in top_bottlenecks
    )
    partial = CoreDiagnosis(
        locked_score_sha256=snapshot.snapshot_sha256,
        strongest_criterion=strongest,
        main_bottleneck=top_bottlenecks[0] if top_bottlenecks else None,
        bottlenecks=top_bottlenecks,
        keep_items=tuple(keeps[:3]),
        next_actions=next_actions,
        target_band=float(target_band) if target_band is not None else None,
        version=CORE_DIAGNOSIS_VERSION,
        diagnosis_sha256="",
    )
    return CoreDiagnosis(**{**partial.__dict__, "diagnosis_sha256": digest(partial.content(include_hash=False))})


def score_projection(snapshot: LockedScoreSnapshot) -> Mapping[str, Any]:
    """Return a read-only diagnosis/report projection without assessment mutation hooks."""
    validate_locked_score_snapshot(snapshot)
    return MappingProxyType({
        "overallBand": snapshot.overall_band,
        "criteria": snapshot.score_by_criterion(),
        "lockedScoreSha256": snapshot.snapshot_sha256,
    })
