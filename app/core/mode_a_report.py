"""Pure, versioned Mode A report-document composition."""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from types import MappingProxyType
from typing import Any, Mapping, Sequence

from .assessment_finalization import (
    LockedScoreSnapshot,
    assert_consumer_scores,
    validate_locked_score_snapshot,
)
from .coaching import (
    CoachingContractError,
    CoachingSection,
    CoachingStatus,
    FullCoaching,
    validate_full_coaching,
)
from .submission import digest


MODE_A_REPORT_VERSION = "mode-a-report-document-v1"


class ModeAReportError(ValueError):
    """The report cannot truthfully represent its source artifacts."""


class ModeAReportStatus(str, Enum):
    COMPLETE = "COMPLETE"
    PARTIAL = "PARTIAL"
    REVIEW_REQUIRED = "REVIEW_REQUIRED"


def _freeze(value: Any) -> Any:
    if isinstance(value, Mapping):
        return MappingProxyType({str(key): _freeze(child) for key, child in value.items()})
    if isinstance(value, (list, tuple)):
        return tuple(_freeze(child) for child in value)
    return value


def _thaw(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _thaw(child) for key, child in value.items()}
    if isinstance(value, tuple):
        return [_thaw(child) for child in value]
    return value


@dataclass(frozen=True)
class ReportSection:
    key: str
    order: int
    label_en: str
    label_zh: str
    payload: Mapping[str, Any]

    def __post_init__(self) -> None:
        object.__setattr__(self, "payload", _freeze(self.payload))

    def content(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "order": self.order,
            "labelEn": self.label_en,
            "labelZh": self.label_zh,
            "payload": _thaw(self.payload),
        }


@dataclass(frozen=True)
class ModeAReportDocument:
    task_type: str
    status: ModeAReportStatus
    score_projection: Mapping[str, Any] | None
    sections: tuple[ReportSection, ...]
    source_hashes: Mapping[str, str]
    review_reasons: tuple[str, ...]
    disclaimer_en: str
    disclaimer_zh: str
    document_sha256: str
    version: str = MODE_A_REPORT_VERSION

    def __post_init__(self) -> None:
        if self.score_projection is not None:
            object.__setattr__(self, "score_projection", _freeze(self.score_projection))
        object.__setattr__(self, "source_hashes", _freeze(self.source_hashes))

    @property
    def publishable(self) -> bool:
        return self.status in {ModeAReportStatus.COMPLETE, ModeAReportStatus.PARTIAL}

    def content(self, *, include_hash: bool = True) -> dict[str, Any]:
        value = {
            "version": self.version,
            "taskType": self.task_type,
            "status": self.status.value,
            "publishable": self.publishable,
            "scoreProjection": _thaw(self.score_projection),
            "sections": [section.content() for section in self.sections],
            "sourceHashes": dict(self.source_hashes),
            "reviewReasons": list(self.review_reasons),
            "disclaimerEn": self.disclaimer_en,
            "disclaimerZh": self.disclaimer_zh,
            "consumerMayRescore": False,
        }
        if include_hash:
            value["documentSha256"] = self.document_sha256
        return value


_SECTION_ORDER = (
    ("score_summary", "Estimated Band", "预估分数"),
    ("criteria", "Criterion Estimates", "单项预估"),
    ("strongest", "Strongest Criterion", "最强单项"),
    ("bottlenecks", "Main Bottlenecks", "主要瓶颈"),
    ("keep", "Keep", "保留项"),
    ("mind_map", "Essay Mind Map", "文章思维导图"),
    ("paragraphs", "Paragraph Diagnosis", "段落诊断"),
    ("corrections", "Corrections", "纠正项"),
    ("minimal_paragraph", "Minimal Improved Paragraph", "最小改进段落"),
    ("topic_learning", "Topic Learning", "话题学习"),
    ("next_actions", "Next Actions", "下一步行动"),
)
_SECTION_TO_COACHING = {
    "bottlenecks": CoachingSection.BOTTLENECK,
    "keep": CoachingSection.KEEP,
    "mind_map": CoachingSection.MIND_MAP,
    "paragraphs": CoachingSection.PARAGRAPH_DIAGNOSIS,
    "corrections": CoachingSection.CORRECTION,
    "minimal_paragraph": CoachingSection.MINIMAL_IMPROVED_PARAGRAPH,
    "topic_learning": CoachingSection.TOPIC_LEARNING,
    "next_actions": CoachingSection.NEXT_ACTION,
}
_DISCLAIMER_EN = (
    "This is an internal estimated-band coaching report, not an official IELTS result "
    "or an examiner decision."
)
_DISCLAIMER_ZH = "这是内部预估分数学习报告，不是 IELTS 官方成绩或考官决定。"


def _final_document(partial: ModeAReportDocument) -> ModeAReportDocument:
    return ModeAReportDocument(**{
        **partial.__dict__,
        "document_sha256": digest(partial.content(include_hash=False)),
    })


def compose_mode_a_report(
    snapshot: LockedScoreSnapshot,
    coaching: FullCoaching,
) -> ModeAReportDocument:
    """Compose a stable report by copying, never deriving, Locked Score values."""
    validate_locked_score_snapshot(snapshot)
    try:
        validate_full_coaching(coaching)
    except CoachingContractError as exc:
        raise ModeAReportError("Full Coaching source is invalid.") from exc
    if coaching.locked_score_sha256 != snapshot.snapshot_sha256 or coaching.task_type != snapshot.task_type:
        raise ModeAReportError("Report sources do not share Locked Score lineage.")
    criterion_scores = dict(snapshot.score_by_criterion())
    assert_consumer_scores(
        snapshot,
        overall_band=snapshot.overall_band,
        criterion_scores=criterion_scores,
    )
    score = {
        "overallBand": snapshot.overall_band,
        "likelyRange": [snapshot.overall_lower_bound, snapshot.overall_upper_bound],
        "confidence": snapshot.confidence,
        "criteria": criterion_scores,
        "lockedScoreSha256": snapshot.snapshot_sha256,
        "calculationPolicyVersion": snapshot.calculation_policy_version,
        "estimatedBandLanguage": True,
    }
    sections: list[ReportSection] = []
    for order, (key, label_en, label_zh) in enumerate(_SECTION_ORDER, start=1):
        if key == "score_summary":
            payload: Mapping[str, Any] = {
                "overallBand": snapshot.overall_band,
                "likelyRange": [snapshot.overall_lower_bound, snapshot.overall_upper_bound],
                "confidence": snapshot.confidence,
            }
        elif key == "criteria":
            payload = {"criteria": criterion_scores}
        elif key == "strongest":
            payload = {"criterion": coaching.strongest_criterion}
        else:
            coaching_section = _SECTION_TO_COACHING[key]
            payload = {
                "itemIds": [item.item_id for item in coaching.items_for(coaching_section)],
                "items": [item.content() for item in coaching.items_for(coaching_section)],
            }
        sections.append(ReportSection(key, order, label_en, label_zh, payload))
    status = (
        ModeAReportStatus.COMPLETE
        if coaching.status is CoachingStatus.COMPLETE
        else ModeAReportStatus.PARTIAL
    )
    source_hashes = {
        "lockedScoreSha256": snapshot.snapshot_sha256,
        "finalizationBundleSha256": coaching.finalization_bundle_sha256,
        "coreDiagnosisSha256": coaching.core_diagnosis_sha256,
        "fullCoachingSha256": coaching.coaching_sha256,
    }
    if coaching.topic_pack_sha256:
        source_hashes["topicPackSha256"] = coaching.topic_pack_sha256
    if coaching.rag_evidence_gate_sha256:
        source_hashes["ragEvidenceGateSha256"] = coaching.rag_evidence_gate_sha256
    partial = ModeAReportDocument(
        task_type=snapshot.task_type,
        status=status,
        score_projection=score,
        sections=tuple(sections),
        source_hashes=source_hashes,
        review_reasons=(),
        disclaimer_en=_DISCLAIMER_EN,
        disclaimer_zh=_DISCLAIMER_ZH,
        document_sha256="",
    )
    return _final_document(partial)


def compose_review_mode_a_report(
    task_type: str,
    review_reasons: Sequence[str],
    *,
    source_hashes: Mapping[str, str],
) -> ModeAReportDocument:
    """Represent a critical upstream review state without publishing a score."""
    reasons = tuple(sorted({str(reason).strip() for reason in review_reasons if str(reason).strip()}))
    if task_type not in {"task1", "task2"} or not reasons or not source_hashes:
        raise ModeAReportError("A review report requires task, reasons, and source identity.")
    section = ReportSection(
        "review_required",
        1,
        "Review Required",
        "需要复核",
        {"reasons": list(reasons)},
    )
    partial = ModeAReportDocument(
        task_type=task_type,
        status=ModeAReportStatus.REVIEW_REQUIRED,
        score_projection=None,
        sections=(section,),
        source_hashes=dict(source_hashes),
        review_reasons=reasons,
        disclaimer_en=_DISCLAIMER_EN,
        disclaimer_zh=_DISCLAIMER_ZH,
        document_sha256="",
    )
    return _final_document(partial)


def validate_mode_a_report(document: ModeAReportDocument) -> None:
    if not isinstance(document, ModeAReportDocument):
        raise ModeAReportError("Mode A report type is invalid.")
    if digest(document.content(include_hash=False)) != document.document_sha256:
        raise ModeAReportError("Mode A report hash is invalid.")
    if document.status is ModeAReportStatus.REVIEW_REQUIRED and document.score_projection is not None:
        raise ModeAReportError("Review-required reports cannot publish score fields.")
    if document.publishable and document.score_projection is None:
        raise ModeAReportError("Publishable reports require a Locked Score projection.")
    orders = tuple(section.order for section in document.sections)
    if orders != tuple(range(1, len(orders) + 1)):
        raise ModeAReportError("Mode A report section order is invalid.")
