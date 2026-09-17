"""Immutable revision ledgers, guided hints, classifications, and metrics."""
from __future__ import annotations

from dataclasses import dataclass
from difflib import SequenceMatcher
from enum import Enum, IntEnum
import unicodedata
from typing import Any, Mapping, Sequence

from .submission import EssayVersion, digest


REVISION_LEDGER_VERSION = "revision-ledger-v1"
GUIDED_HINT_POLICY_VERSION = "guided-hints-v1"
REVISION_CLASSIFIER_VERSION = "revision-classifier-v1"
REVISION_METRIC_POLICY_VERSION = "revision-metrics-v1"


class RevisionContractError(ValueError):
    """Revision evidence, state, or metric input is invalid."""


class AssistanceDepth(str, Enum):
    NONE = "NONE"
    LOCATION = "LOCATION"
    CATEGORY = "CATEGORY"
    GUIDANCE = "GUIDANCE"
    REFERENCE = "REFERENCE"
    AI_REWRITE = "AI_REWRITE"
    PASTED = "PASTED"
    UNKNOWN = "UNKNOWN"

    @property
    def independent(self) -> bool:
        return self is AssistanceDepth.NONE


class ChangeKind(str, Enum):
    INSERT = "INSERT"
    DELETE = "DELETE"
    REPLACE = "REPLACE"


class AlignmentStatus(str, Enum):
    ALIGNED = "ALIGNED"
    REVIEW_REQUIRED = "REVIEW_REQUIRED"


@dataclass(frozen=True)
class ChangeRange:
    essay_version_id: str
    start: int
    end: int
    text_sha256: str

    def content(self) -> dict[str, Any]:
        return {
            "essayVersionId": self.essay_version_id,
            "start": self.start,
            "end": self.end,
            "textSha256": self.text_sha256,
        }


@dataclass(frozen=True)
class RevisionIssue:
    issue_id: str
    criterion: str
    essay_version_id: str
    start: int
    end: int
    evidence_ids: tuple[str, ...]

    @classmethod
    def create(
        cls,
        issue_id: str,
        criterion: str,
        essay: EssayVersion,
        start: int,
        end: int,
        evidence_ids: Sequence[str],
    ) -> "RevisionIssue":
        ids = tuple(sorted({str(value) for value in evidence_ids if str(value)}))
        if (
            not issue_id
            or not criterion
            or not ids
            or start < 0
            or end <= start
            or end > len(essay.original_text)
        ):
            raise RevisionContractError("Revision issue locator or evidence is invalid.")
        return cls(issue_id, criterion, essay.essay_version_id, start, end, ids)

    def content(self) -> dict[str, Any]:
        return {
            "issueId": self.issue_id,
            "criterion": self.criterion,
            "essayVersionId": self.essay_version_id,
            "start": self.start,
            "end": self.end,
            "evidenceIds": list(self.evidence_ids),
        }


@dataclass(frozen=True)
class ParagraphAlignment:
    original_paragraph_id: str | None
    revised_paragraph_id: str | None
    similarity: float
    status: str

    def content(self) -> dict[str, Any]:
        return {
            "originalParagraphId": self.original_paragraph_id,
            "revisedParagraphId": self.revised_paragraph_id,
            "similarity": self.similarity,
            "status": self.status,
        }


@dataclass(frozen=True)
class StudentChange:
    change_id: str
    kind: ChangeKind
    original_range: ChangeRange
    revised_range: ChangeRange
    original_text: str
    revised_text: str
    issue_ids: tuple[str, ...]
    assistance_depth: AssistanceDepth

    @property
    def punctuation_only(self) -> bool:
        def semantic(value: str) -> str:
            return "".join(
                char.casefold()
                for char in value
                if not unicodedata.category(char).startswith(("P", "Z"))
            )
        return semantic(self.original_text) == semantic(self.revised_text)

    def content(self) -> dict[str, Any]:
        return {
            "changeId": self.change_id,
            "kind": self.kind.value,
            "originalRange": self.original_range.content(),
            "revisedRange": self.revised_range.content(),
            "issueIds": list(self.issue_ids),
            "assistanceDepth": self.assistance_depth.value,
            "punctuationOnly": self.punctuation_only,
        }


@dataclass(frozen=True)
class RevisionLedger:
    locked_score_sha256: str
    original_essay_version_id: str
    revised_essay_version_id: str
    original_locator_manifest_sha256: str
    revised_locator_manifest_sha256: str
    alignment_status: AlignmentStatus
    alignment_flags: tuple[str, ...]
    paragraph_alignments: tuple[ParagraphAlignment, ...]
    changes: tuple[StudentChange, ...]
    issue_ids: tuple[str, ...]
    ledger_sha256: str
    version: str = REVISION_LEDGER_VERSION

    def content(self, *, include_hash: bool = True) -> dict[str, Any]:
        value = {
            "version": self.version,
            "lockedScoreSha256": self.locked_score_sha256,
            "originalEssayVersionId": self.original_essay_version_id,
            "revisedEssayVersionId": self.revised_essay_version_id,
            "originalLocatorManifestSha256": self.original_locator_manifest_sha256,
            "revisedLocatorManifestSha256": self.revised_locator_manifest_sha256,
            "alignmentStatus": self.alignment_status.value,
            "alignmentFlags": list(self.alignment_flags),
            "paragraphAlignments": [item.content() for item in self.paragraph_alignments],
            "changes": [item.content() for item in self.changes],
            "issueIds": list(self.issue_ids),
            "qualityJudgmentIncluded": False,
        }
        if include_hash:
            value["ledgerSha256"] = self.ledger_sha256
        return value


def _range(essay: EssayVersion, start: int, end: int) -> ChangeRange:
    if start < 0 or end < start or end > len(essay.original_text):
        raise RevisionContractError("Change range is invalid.")
    return ChangeRange(
        essay.essay_version_id,
        start,
        end,
        digest(essay.original_text[start:end]),
    )


def _overlaps(start: int, end: int, issue: RevisionIssue) -> bool:
    if start == end:
        # SequenceMatcher may attach the whitespace before an insertion to the
        # inserted text, placing the V1 insertion point one code point before
        # the issue start. Treat that exact adjacent boundary as linked while
        # keeping more distant insertions out of the issue.
        return issue.start - 1 <= start <= issue.end
    return start < issue.end and issue.start < end


def _paragraph_alignments(original: EssayVersion, revised: EssayVersion) -> tuple[ParagraphAlignment, ...]:
    old = [paragraph.locator.normalized_text for paragraph in original.paragraphs]
    new = [paragraph.locator.normalized_text for paragraph in revised.paragraphs]
    matcher = SequenceMatcher(a=old, b=new, autojunk=False)
    result: list[ParagraphAlignment] = []
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        width = max(i2 - i1, j2 - j1)
        for offset in range(width):
            left = original.paragraphs[i1 + offset] if i1 + offset < i2 else None
            right = revised.paragraphs[j1 + offset] if j1 + offset < j2 else None
            similarity = 0.0
            if left is not None and right is not None:
                similarity = SequenceMatcher(
                    a=left.locator.normalized_text,
                    b=right.locator.normalized_text,
                    autojunk=False,
                ).ratio()
            result.append(ParagraphAlignment(
                left.locator.locator_id if left else None,
                right.locator.locator_id if right else None,
                round(similarity, 6),
                tag.upper(),
            ))
    return tuple(result)


def build_revision_ledger(
    locked_score_sha256: str,
    original: EssayVersion,
    revised: EssayVersion,
    issues: Sequence[RevisionIssue],
    *,
    assistance_depth: AssistanceDepth = AssistanceDepth.UNKNOWN,
) -> RevisionLedger:
    if not locked_score_sha256 or not isinstance(assistance_depth, AssistanceDepth):
        raise RevisionContractError("Revision lineage or assistance provenance is invalid.")
    issue_by_id: dict[str, RevisionIssue] = {}
    for issue in issues:
        if issue.essay_version_id != original.essay_version_id:
            raise RevisionContractError("Original issue evidence must remain bound to V1.")
        if issue.issue_id in issue_by_id:
            raise RevisionContractError("Revision issue IDs are duplicated.")
        issue_by_id[issue.issue_id] = issue
    matcher = SequenceMatcher(
        a=original.original_text,
        b=revised.original_text,
        autojunk=False,
    )
    changes: list[StudentChange] = []
    changed_old = changed_new = 0
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            continue
        kind = {
            "insert": ChangeKind.INSERT,
            "delete": ChangeKind.DELETE,
            "replace": ChangeKind.REPLACE,
        }[tag]
        old_text = original.original_text[i1:i2]
        new_text = revised.original_text[j1:j2]
        linked = tuple(sorted(
            issue.issue_id for issue in issues if _overlaps(i1, i2, issue)
        ))
        identity = {
            "kind": kind.value,
            "originalEssayVersionId": original.essay_version_id,
            "revisedEssayVersionId": revised.essay_version_id,
            "originalStart": i1,
            "originalEnd": i2,
            "revisedStart": j1,
            "revisedEnd": j2,
            "originalTextSha256": digest(old_text),
            "revisedTextSha256": digest(new_text),
            "issueIds": list(linked),
            "assistanceDepth": assistance_depth.value,
        }
        changes.append(StudentChange(
            "student-change:" + digest(identity),
            kind,
            _range(original, i1, i2),
            _range(revised, j1, j2),
            old_text,
            new_text,
            linked,
            assistance_depth,
        ))
        changed_old += i2 - i1
        changed_new += j2 - j1
    flags: set[str] = set()
    ratio = matcher.ratio()
    old_fraction = changed_old / max(1, len(original.original_text))
    new_fraction = changed_new / max(1, len(revised.original_text))
    if (
        ratio < 0.2
        or (old_fraction > 0.8 and new_fraction > 0.8)
        or (ratio < 0.5 and old_fraction > 0.6 and new_fraction > 0.6)
    ):
        flags.add("FULL_REPLACEMENT")
    elif old_fraction > 0.6 or new_fraction > 0.6:
        flags.add("LARGE_CHANGE")
    old_paragraphs = [item.locator.normalized_text for item in original.paragraphs]
    new_paragraphs = [item.locator.normalized_text for item in revised.paragraphs]
    if len(old_paragraphs) != len(set(old_paragraphs)) or len(new_paragraphs) != len(set(new_paragraphs)):
        flags.add("AMBIGUOUS_ALIGNMENT")
    status = AlignmentStatus.REVIEW_REQUIRED if {
        "FULL_REPLACEMENT", "AMBIGUOUS_ALIGNMENT"
    }.intersection(flags) else AlignmentStatus.ALIGNED
    alignments = _paragraph_alignments(original, revised)
    partial = RevisionLedger(
        locked_score_sha256=locked_score_sha256,
        original_essay_version_id=original.essay_version_id,
        revised_essay_version_id=revised.essay_version_id,
        original_locator_manifest_sha256=original.locator_manifest_sha256,
        revised_locator_manifest_sha256=revised.locator_manifest_sha256,
        alignment_status=status,
        alignment_flags=tuple(sorted(flags)),
        paragraph_alignments=alignments,
        changes=tuple(changes),
        issue_ids=tuple(sorted(issue_by_id)),
        ledger_sha256="",
    )
    return RevisionLedger(**{
        **partial.__dict__,
        "ledger_sha256": digest(partial.content(include_hash=False)),
    })


def validate_revision_ledger(ledger: RevisionLedger) -> None:
    if not isinstance(ledger, RevisionLedger):
        raise RevisionContractError("Revision ledger type is invalid.")
    if digest(ledger.content(include_hash=False)) != ledger.ledger_sha256:
        raise RevisionContractError("Revision ledger hash is invalid.")


class HintLevel(IntEnum):
    LOCATION = 1
    CATEGORY = 2
    GUIDANCE = 3
    REFERENCE = 4


class HintSessionStatus(str, Enum):
    ACTIVE = "ACTIVE"
    STOPPED = "STOPPED"
    MODE_A = "MODE_A"


@dataclass(frozen=True)
class HintContent:
    issue_id: str
    level: HintLevel
    text_en: str
    text_zh: str
    evidence_ids: tuple[str, ...]
    contains_reference_answer: bool = False

    def __post_init__(self) -> None:
        if not self.issue_id or not self.text_en.strip() or not self.text_zh.strip() or not self.evidence_ids:
            raise RevisionContractError("Hint content requires issue, localization, and evidence.")
        if self.contains_reference_answer and self.level is not HintLevel.REFERENCE:
            raise RevisionContractError("Reference content is allowed only at hint level 4.")

    def content(self) -> dict[str, Any]:
        return {
            "issueId": self.issue_id,
            "level": int(self.level),
            "textEn": self.text_en,
            "textZh": self.text_zh,
            "evidenceIds": list(self.evidence_ids),
            "containsReferenceAnswer": self.contains_reference_answer,
        }


@dataclass(frozen=True)
class HintEvent:
    sequence: int
    action: str
    level: int
    event_sha256: str

    def content(self) -> dict[str, Any]:
        return {
            "sequence": self.sequence,
            "action": self.action,
            "level": self.level,
            "eventSha256": self.event_sha256,
        }


@dataclass(frozen=True)
class HintSession:
    issue_id: str
    evidence_ids: tuple[str, ...]
    status: HintSessionStatus
    revealed: tuple[HintContent, ...]
    events: tuple[HintEvent, ...]
    session_sha256: str
    policy_version: str = GUIDED_HINT_POLICY_VERSION

    @property
    def current_level(self) -> int:
        return int(self.revealed[-1].level) if self.revealed else 0

    @property
    def assistance_depth(self) -> AssistanceDepth:
        return {
            0: AssistanceDepth.NONE,
            1: AssistanceDepth.LOCATION,
            2: AssistanceDepth.CATEGORY,
            3: AssistanceDepth.GUIDANCE,
            4: AssistanceDepth.REFERENCE,
        }[self.current_level]

    def content(self, *, include_hash: bool = True) -> dict[str, Any]:
        value = {
            "policyVersion": self.policy_version,
            "issueId": self.issue_id,
            "evidenceIds": list(self.evidence_ids),
            "status": self.status.value,
            "revealed": [item.content() for item in self.revealed],
            "events": [event.content() for event in self.events],
            "assistanceDepth": self.assistance_depth.value,
        }
        if include_hash:
            value["sessionSha256"] = self.session_sha256
        return value


def _hint_session(
    issue_id: str,
    evidence_ids: tuple[str, ...],
    status: HintSessionStatus,
    revealed: tuple[HintContent, ...],
    events: tuple[HintEvent, ...],
) -> HintSession:
    partial = HintSession(issue_id, evidence_ids, status, revealed, events, "")
    return HintSession(**{
        **partial.__dict__,
        "session_sha256": digest(partial.content(include_hash=False)),
    })


def start_hint_session(issue: RevisionIssue) -> HintSession:
    return _hint_session(issue.issue_id, issue.evidence_ids, HintSessionStatus.ACTIVE, (), ())


def validate_hint_session(session: HintSession) -> None:
    if not isinstance(session, HintSession) or not session.issue_id or not session.evidence_ids:
        raise RevisionContractError("Hint session type or issue evidence is invalid.")
    if digest(session.content(include_hash=False)) != session.session_sha256:
        raise RevisionContractError("Hint session hash is invalid.")
    levels = tuple(int(item.level) for item in session.revealed)
    if levels != tuple(range(1, len(levels) + 1)):
        raise RevisionContractError("Hint session reveal history is not monotonic.")
    if any(
        item.issue_id != session.issue_id
        or not set(item.evidence_ids).issubset(session.evidence_ids)
        for item in session.revealed
    ):
        raise RevisionContractError("Hint session contains unsupported issue evidence.")
    if tuple(event.sequence for event in session.events) != tuple(range(1, len(session.events) + 1)):
        raise RevisionContractError("Hint event sequence is invalid.")


def _hint_event(session: HintSession, action: str, level: int) -> HintEvent:
    sequence = len(session.events) + 1
    payload = {
        "sessionSha256": session.session_sha256,
        "sequence": sequence,
        "action": action,
        "level": level,
    }
    return HintEvent(sequence, action, level, digest(payload))


def reveal_hint(session: HintSession, content: HintContent) -> HintSession:
    validate_hint_session(session)
    if session.status is not HintSessionStatus.ACTIVE:
        raise RevisionContractError("Hints can be revealed only in an active session.")
    if content.issue_id != session.issue_id or not set(content.evidence_ids).issubset(session.evidence_ids):
        raise RevisionContractError("Hint content is not bound to the active verified issue.")
    requested = int(content.level)
    if requested == session.current_level:
        return session
    if requested != session.current_level + 1:
        raise RevisionContractError("Hint levels must be revealed monotonically without leakage.")
    event = _hint_event(session, "REVEAL", requested)
    return _hint_session(
        session.issue_id,
        session.evidence_ids,
        session.status,
        session.revealed + (content,),
        session.events + (event,),
    )


def stop_hints(session: HintSession) -> HintSession:
    validate_hint_session(session)
    if session.status is not HintSessionStatus.ACTIVE:
        raise RevisionContractError("Only an active hint session can stop.")
    event = _hint_event(session, "STOP", session.current_level)
    return _hint_session(session.issue_id, session.evidence_ids, HintSessionStatus.STOPPED, session.revealed, session.events + (event,))


def resume_hints(session: HintSession) -> HintSession:
    validate_hint_session(session)
    if session.status is not HintSessionStatus.STOPPED:
        raise RevisionContractError("Only a stopped hint session can resume.")
    event = _hint_event(session, "RESUME", session.current_level)
    return _hint_session(session.issue_id, session.evidence_ids, HintSessionStatus.ACTIVE, session.revealed, session.events + (event,))


def switch_to_mode_a(session: HintSession) -> HintSession:
    validate_hint_session(session)
    if session.status is HintSessionStatus.MODE_A:
        return session
    event = _hint_event(session, "SWITCH_MODE_A", session.current_level)
    return _hint_session(session.issue_id, session.evidence_ids, HintSessionStatus.MODE_A, session.revealed, session.events + (event,))


class RevisionLabel(str, Enum):
    REAL_IMPROVEMENT = "REAL_IMPROVEMENT"
    COSMETIC_CHANGE = "COSMETIC_CHANGE"
    UNCHANGED = "UNCHANGED"
    NEW_ERROR = "NEW_ERROR"
    OVEREDITED = "OVEREDITED"
    REGRESSION = "REGRESSION"


class ClassificationStatus(str, Enum):
    VALID = "VALID"
    REVIEW_REQUIRED = "REVIEW_REQUIRED"


@dataclass(frozen=True)
class RevisionJudgmentDraft:
    label: RevisionLabel
    confidence: str
    reason: str
    issue_id: str | None = None
    change_id: str | None = None


@dataclass(frozen=True)
class RevisionClassification:
    classification_id: str
    label: RevisionLabel
    status: ClassificationStatus
    confidence: str
    reason: str
    issue_id: str | None
    change_id: str | None
    evidence_ids: tuple[str, ...]
    assistance_depth: AssistanceDepth
    ledger_sha256: str

    def content(self) -> dict[str, Any]:
        return {
            "classificationId": self.classification_id,
            "label": self.label.value,
            "status": self.status.value,
            "confidence": self.confidence,
            "reason": self.reason,
            "issueId": self.issue_id,
            "changeId": self.change_id,
            "evidenceIds": list(self.evidence_ids),
            "assistanceDepth": self.assistance_depth.value,
            "ledgerSha256": self.ledger_sha256,
            "scoreMovementInferred": False,
        }


def classify_revision(
    ledger: RevisionLedger,
    issues: Sequence[RevisionIssue],
    drafts: Sequence[RevisionJudgmentDraft],
) -> tuple[RevisionClassification, ...]:
    validate_revision_ledger(ledger)
    issue_by_id = {issue.issue_id: issue for issue in issues}
    change_by_id = {change.change_id: change for change in ledger.changes}
    if len(issue_by_id) != len(issues):
        raise RevisionContractError("Classification issue IDs are duplicated.")
    result: list[RevisionClassification] = []
    seen: set[tuple[str | None, str | None, RevisionLabel]] = set()
    primary_label_by_change: dict[str, RevisionLabel] = {}
    label_by_issue: dict[str, RevisionLabel] = {}
    for draft in drafts:
        if draft.confidence not in {"HIGH", "MEDIUM", "LOW"} or not draft.reason.strip():
            raise RevisionContractError("Revision judgment confidence or reason is invalid.")
        issue = issue_by_id.get(draft.issue_id) if draft.issue_id else None
        change = change_by_id.get(draft.change_id) if draft.change_id else None
        if draft.issue_id and issue is None:
            raise RevisionContractError("Revision judgment references an unknown issue.")
        if draft.change_id and change is None:
            raise RevisionContractError("Revision judgment references an unknown change.")
        if draft.label is RevisionLabel.UNCHANGED:
            if issue is None or change is not None:
                raise RevisionContractError("UNCHANGED requires an issue and no StudentChange.")
        elif change is None:
            raise RevisionContractError("Changed classifications require a StudentChange.")
        if draft.label is RevisionLabel.REAL_IMPROVEMENT:
            if issue is None or issue.issue_id not in change.issue_ids:
                raise RevisionContractError("Real improvement must resolve a linked original issue.")
            if change.punctuation_only:
                raise RevisionContractError("Punctuation-only edits cannot be real improvements.")
        if draft.label is RevisionLabel.NEW_ERROR and issue is not None:
            raise RevisionContractError("A new error cannot reuse an original issue identity.")
        if issue is not None:
            prior_issue_label = label_by_issue.get(issue.issue_id)
            if prior_issue_label is not None and prior_issue_label is not draft.label:
                raise RevisionContractError("One original issue cannot receive contradictory classifications.")
            label_by_issue[issue.issue_id] = draft.label
        if change is not None and draft.label is not RevisionLabel.NEW_ERROR:
            prior_change_label = primary_label_by_change.get(change.change_id)
            if prior_change_label is not None and prior_change_label is not draft.label:
                raise RevisionContractError("One StudentChange cannot receive contradictory primary labels.")
            primary_label_by_change[change.change_id] = draft.label
        if draft.label is RevisionLabel.OVEREDITED and "FULL_REPLACEMENT" not in ledger.alignment_flags and "LARGE_CHANGE" not in ledger.alignment_flags:
            raise RevisionContractError("Over-editing requires large-change evidence.")
        marker = (draft.issue_id, draft.change_id, draft.label)
        if marker in seen:
            continue
        seen.add(marker)
        evidence_ids = issue.evidence_ids if issue else ()
        assistance = change.assistance_depth if change else AssistanceDepth.NONE
        status = (
            ClassificationStatus.REVIEW_REQUIRED
            if draft.confidence == "LOW" or ledger.alignment_status is AlignmentStatus.REVIEW_REQUIRED
            else ClassificationStatus.VALID
        )
        identity = {
            "classifierVersion": REVISION_CLASSIFIER_VERSION,
            "ledgerSha256": ledger.ledger_sha256,
            "label": draft.label.value,
            "status": status.value,
            "confidence": draft.confidence,
            "reason": " ".join(draft.reason.split()),
            "issueId": draft.issue_id,
            "changeId": draft.change_id,
            "evidenceIds": list(evidence_ids),
            "assistanceDepth": assistance.value,
        }
        result.append(RevisionClassification(
            "revision-classification:" + digest(identity),
            draft.label,
            status,
            draft.confidence,
            identity["reason"],
            draft.issue_id,
            draft.change_id,
            evidence_ids,
            assistance,
            ledger.ledger_sha256,
        ))
    return tuple(sorted(result, key=lambda item: item.classification_id))


class MetricStatus(str, Enum):
    VALUE = "VALUE"
    NOT_APPLICABLE = "NOT_APPLICABLE"


@dataclass(frozen=True)
class RevisionMetric:
    name: str
    numerator: int
    denominator: int
    value: float | None
    status: MetricStatus

    def content(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "numerator": self.numerator,
            "denominator": self.denominator,
            "value": self.value,
            "status": self.status.value,
        }


@dataclass(frozen=True)
class RevisionSummary:
    ledger_sha256: str
    metrics: tuple[RevisionMetric, ...]
    resolved_issue_ids: tuple[str, ...]
    unresolved_issue_ids: tuple[str, ...]
    new_error_classification_ids: tuple[str, ...]
    review_required_classification_ids: tuple[str, ...]
    unclassified_change_ids: tuple[str, ...]
    score_movement: None
    summary_sha256: str
    policy_version: str = REVISION_METRIC_POLICY_VERSION

    def content(self, *, include_hash: bool = True) -> dict[str, Any]:
        value = {
            "policyVersion": self.policy_version,
            "ledgerSha256": self.ledger_sha256,
            "metrics": [metric.content() for metric in self.metrics],
            "resolvedIssueIds": list(self.resolved_issue_ids),
            "unresolvedIssueIds": list(self.unresolved_issue_ids),
            "newErrorClassificationIds": list(self.new_error_classification_ids),
            "reviewRequiredClassificationIds": list(self.review_required_classification_ids),
            "unclassifiedChangeIds": list(self.unclassified_change_ids),
            "scoreMovement": None,
        }
        if include_hash:
            value["summarySha256"] = self.summary_sha256
        return value


def _metric(name: str, numerator: int, denominator: int) -> RevisionMetric:
    if denominator == 0:
        return RevisionMetric(name, numerator, denominator, None, MetricStatus.NOT_APPLICABLE)
    return RevisionMetric(name, numerator, denominator, numerator / denominator, MetricStatus.VALUE)


def aggregate_revision_metrics(
    ledger: RevisionLedger,
    issues: Sequence[RevisionIssue],
    classifications: Sequence[RevisionClassification],
) -> RevisionSummary:
    validate_revision_ledger(ledger)
    issue_ids = {issue.issue_id for issue in issues}
    if len(issue_ids) != len(issues):
        raise RevisionContractError("Metric issue IDs are duplicated.")
    valid = [item for item in classifications if item.status is ClassificationStatus.VALID]
    resolved = {
        item.issue_id for item in valid
        if item.label is RevisionLabel.REAL_IMPROVEMENT and item.issue_id
    }
    unresolved = issue_ids - resolved
    denominator = len(ledger.changes)
    counts = {
        label: len({
            item.change_id for item in valid
            if item.label is label and item.change_id is not None
        })
        for label in RevisionLabel
    }
    metrics = (
        _metric("ISSUE_RESOLUTION_RATE", len(resolved), len(issue_ids)),
        _metric("NEW_ERROR_RATE", counts[RevisionLabel.NEW_ERROR], denominator),
        _metric("VALUABLE_CHANGE_RATIO", counts[RevisionLabel.REAL_IMPROVEMENT], denominator),
        _metric("COSMETIC_CHANGE_RATE", counts[RevisionLabel.COSMETIC_CHANGE], denominator),
        _metric("REGRESSION_RATE", counts[RevisionLabel.REGRESSION], denominator),
        _metric("OVEREDITING_RATE", counts[RevisionLabel.OVEREDITED], denominator),
    )
    new_errors = tuple(sorted(
        item.classification_id for item in valid if item.label is RevisionLabel.NEW_ERROR
    ))
    reviews = tuple(sorted(
        item.classification_id for item in classifications
        if item.status is ClassificationStatus.REVIEW_REQUIRED
    ))
    classified_change_ids = {
        item.change_id for item in classifications if item.change_id is not None
    }
    unclassified = tuple(sorted(
        change.change_id for change in ledger.changes
        if change.change_id not in classified_change_ids
    ))
    partial = RevisionSummary(
        ledger.ledger_sha256,
        metrics,
        tuple(sorted(resolved)),
        tuple(sorted(unresolved)),
        new_errors,
        reviews,
        unclassified,
        None,
        "",
    )
    return RevisionSummary(**{
        **partial.__dict__,
        "summary_sha256": digest(partial.content(include_hash=False)),
    })
