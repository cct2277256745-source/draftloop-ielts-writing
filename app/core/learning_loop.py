"""Pure orchestration for the complete post-score C2 headless learning loop."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Sequence

from .assessment_finalization import (
    FinalizationBundle,
    LockedScoreSnapshot,
    validate_locked_score_snapshot,
)
from .coaching import CoachingItemDraft, FullCoaching, build_full_coaching
from .core_diagnosis import CoreDiagnosis
from .learning_memory import (
    LearningEventStore,
    MasteryProjection,
    PracticeNeed,
    RecommendationPlan,
    build_memory_recommendations,
    replay_mastery,
)
from .mode_a_report import ModeAReportDocument, compose_mode_a_report
from .revision import (
    HintSession,
    RevisionClassification,
    RevisionIssue,
    RevisionJudgmentDraft,
    RevisionLedger,
    RevisionSummary,
    aggregate_revision_metrics,
    build_revision_ledger,
    classify_revision,
)
from .submission import EssayVersion, canonical_json, digest
from .topic_knowledge import MinimalEditPlan, TopicLearningPack


LEARNING_LOOP_VERSION = "headless-learning-loop-v1"


class LearningLoopError(ValueError):
    """The C2 headless artifacts do not form one score-immutable lineage."""


@dataclass(frozen=True)
class HeadlessLearningLoopResult:
    task_type: str
    locked_score_sha256: str
    coaching: FullCoaching
    report: ModeAReportDocument
    topic_pack: TopicLearningPack
    minimal_edit_plan: MinimalEditPlan
    hint_session: HintSession
    revision_ledger: RevisionLedger
    classifications: tuple[RevisionClassification, ...]
    revision_summary: RevisionSummary
    mastery_projections: tuple[MasteryProjection, ...]
    recommendations: RecommendationPlan
    score_equivalence_sha256: str
    result_sha256: str
    version: str = LEARNING_LOOP_VERSION

    def content(self, *, include_hash: bool = True) -> dict[str, Any]:
        value = {
            "version": self.version,
            "taskType": self.task_type,
            "lockedScoreSha256": self.locked_score_sha256,
            "coachingSha256": self.coaching.coaching_sha256,
            "reportDocumentSha256": self.report.document_sha256,
            "topicPackSha256": self.topic_pack.pack_sha256,
            "minimalEditPlanSha256": self.minimal_edit_plan.plan_sha256,
            "hintSessionSha256": self.hint_session.session_sha256,
            "revisionLedgerSha256": self.revision_ledger.ledger_sha256,
            "revisionClassificationIds": [item.classification_id for item in self.classifications],
            "revisionSummarySha256": self.revision_summary.summary_sha256,
            "masteryProjectionSha256s": [item.projection_sha256 for item in self.mastery_projections],
            "recommendationPlanSha256": self.recommendations.plan_sha256,
            "scoreEquivalenceSha256": self.score_equivalence_sha256,
        }
        if include_hash:
            value["resultSha256"] = self.result_sha256
        return value


def run_headless_learning_loop(
    snapshot: LockedScoreSnapshot,
    bundle: FinalizationBundle,
    diagnosis: CoreDiagnosis,
    *,
    coaching_drafts: Sequence[CoachingItemDraft],
    topic_pack: TopicLearningPack,
    original_essay: EssayVersion,
    revised_essay: EssayVersion,
    revision_issues: Sequence[RevisionIssue],
    hint_session: HintSession,
    revision_judgments: Sequence[RevisionJudgmentDraft],
    minimal_edit_plan: MinimalEditPlan,
    event_store: LearningEventStore,
    learner_id: str,
    tenant_id: str,
    practice_needs: Sequence[PracticeNeed],
) -> HeadlessLearningLoopResult:
    validate_locked_score_snapshot(snapshot)
    before = canonical_json(snapshot.content())
    if bundle.bundle_sha256 != snapshot.finalization_bundle_sha256:
        raise LearningLoopError("Learning loop bundle does not match the Locked Score.")
    if diagnosis.locked_score_sha256 != snapshot.snapshot_sha256:
        raise LearningLoopError("Learning loop diagnosis does not match the Locked Score.")
    lineage = dict(bundle.assessments[0].authoritative_lineage)
    if lineage.get("essayVersionId") != original_essay.essay_version_id:
        raise LearningLoopError("Revision V1 is not the scored Candidate Script version.")
    if hint_session.issue_id not in {issue.issue_id for issue in revision_issues}:
        raise LearningLoopError("Hint session is not bound to a revision issue.")
    if minimal_edit_plan.essay_version_id != original_essay.essay_version_id:
        raise LearningLoopError("Minimal edits are not bound to the scored Candidate Script.")
    if digest(minimal_edit_plan.content(include_hash=False)) != minimal_edit_plan.plan_sha256:
        raise LearningLoopError("Minimal Edit Plan hash is invalid.")
    if digest(topic_pack.content(include_hash=False)) != topic_pack.pack_sha256:
        raise LearningLoopError("Topic Learning Pack hash is invalid.")

    coaching = build_full_coaching(
        snapshot,
        bundle,
        diagnosis,
        coaching_drafts,
        topic_pack=topic_pack,
    )
    report = compose_mode_a_report(snapshot, coaching)
    ledger = build_revision_ledger(
        snapshot.snapshot_sha256,
        original_essay,
        revised_essay,
        revision_issues,
        assistance_depth=hint_session.assistance_depth,
    )
    classifications = classify_revision(ledger, revision_issues, revision_judgments)
    summary = aggregate_revision_metrics(ledger, revision_issues, classifications)
    mastery = replay_mastery(event_store, learner_id=learner_id, tenant_id=tenant_id)
    recommendations = build_memory_recommendations(snapshot, practice_needs, mastery)
    after = canonical_json(snapshot.content())
    if before != after or recommendations.locked_score_sha256 != snapshot.snapshot_sha256:
        raise LearningLoopError("A C2 consumer changed the Locked Score projection.")
    equivalence = digest({"before": before, "after": after})
    partial = HeadlessLearningLoopResult(
        snapshot.task_type,
        snapshot.snapshot_sha256,
        coaching,
        report,
        topic_pack,
        minimal_edit_plan,
        hint_session,
        ledger,
        classifications,
        summary,
        mastery,
        recommendations,
        equivalence,
        "",
    )
    return HeadlessLearningLoopResult(**{
        **partial.__dict__,
        "result_sha256": digest(partial.content(include_hash=False)),
    })
