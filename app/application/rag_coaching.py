"""C3 worker-side post-score composition; retrieval never enters scoring inputs."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from app.core.assessment_finalization import FinalizationBundle, LockedScoreSnapshot
from app.core.coaching import (
    CoachingAuthority, CoachingContractError, CoachingItemDraft, CoachingSection, FullCoaching,
    build_full_coaching, validate_full_coaching,
)
from app.core.core_diagnosis import CoreDiagnosis
from app.core.mode_a_report import compose_mode_a_report
from app.core.rag_evidence import EvidenceGateContext, EvidenceGateError, EvidenceGateStatus, gate_retrieval_evidence
from app.core.rag_local_runtime import LocalRagRuntime
from app.core.rag_sidecar import RagSidecar, ValidatedObservation
from app.core.submission import SubmissionSnapshot
from app.core.task1_claims import Task1SubmissionSnapshot
from app.product_platform.contracts import ProcessingOutcome, SubmissionState


@dataclass(frozen=True)
class PostScoreCoachingInputs:
    submission: SubmissionSnapshot | Task1SubmissionSnapshot
    bundle: FinalizationBundle
    locked_score: LockedScoreSnapshot
    diagnosis: CoreDiagnosis
    drafts: tuple[CoachingItemDraft, ...]
    observation: ValidatedObservation | None
    gate_context: EvidenceGateContext | None
    current_evidence: object | None = None
    calibration_audit: dict | None = None
    detailed_report: dict | None = None


@dataclass(frozen=True)
class PostScoreCoachingResult:
    coaching: FullCoaching
    rag_projection: dict
    smoke: dict


class PostScoreRagCoachingService:
    def __init__(self, runtime: LocalRagRuntime, *, timeout_seconds=10):
        self.runtime = runtime
        self.sidecar = RagSidecar(runtime.provider, enabled=runtime.provider is not None,
                                  timeout_seconds=timeout_seconds)

    def compose(self, value: PostScoreCoachingInputs) -> PostScoreCoachingResult:
        score = value.locked_score
        before = score.content()
        # First validate existing lineage and base coaching without giving RAG any score handle.
        base = build_full_coaching(score, value.bundle, value.diagnosis, value.drafts)
        observation = value.observation
        if observation is None:
            if score.task_type != "task1" or value.gate_context is not None:
                raise ValueError("Post-score observation lineage mismatch.")
            validate_full_coaching(base)
            projection = {"state": "RAG_DISABLED", "reason": "TASK1_QUERY_NOT_AVAILABLE",
                "evidenceState": "NOT_REACHED", "consumer": None, "directScoreAuthority": False}
            return PostScoreCoachingResult(base, projection, {
                "queryFingerprint": None, "denseCandidateCount": 0, "bm25CandidateCount": 0,
                "fusedResultCount": 0, "evidenceGate": "NOT_REACHED", "acceptedEvidenceCount": 0,
                "consumer": None, "consumerRagItemCount": 0, "lockedScoreIdentical": score.content() == before,
                "directScoreAuthority": False, "rerankerInvoked": False})
        assessment = next((a for a in value.bundle.assessments if a.criterion == observation.criterion), None)
        known_observations = {ref.get("observationId") for a in value.bundle.assessments
            if a.criterion == observation.criterion for f in a.findings for ref in f.get("evidenceRefs", ())}
        if (assessment is None or observation.observation_id not in known_observations
                or observation.task_type != score.task_type
                or observation.source_artifact_sha256 not in assessment.authoritative_lineage.values()
                or value.gate_context.criterion != observation.criterion):
            raise ValueError("Post-score observation lineage mismatch.")
        # This consumer is private-only. Caller cannot widen rights with a different gate context.
        if value.gate_context.allowed_rights != ("LICENSE_UNCLEAR",) \
                or value.gate_context.allowed_usage != ("PRIVATE_RESEARCH_ONLY",):
            raise ValueError("Private coaching evidence policy mismatch.")
        sidecar_result = self.sidecar.retrieve_after_scoring(observation, score)
        pack = sidecar_result.evidence_pack
        gate = None
        coaching = base
        consumer_reason = None
        if pack is not None:
            try:
                gate = gate_retrieval_evidence(pack, value.gate_context)
                references = ()
                if gate.status is EvidenceGateStatus.SUFFICIENT:
                    # Bounded, labelled empirical reference support; no new score or invented advice.
                    references = tuple(CoachingItemDraft(
                        section=CoachingSection.TOPIC_LEARNING,
                        criterion=observation.criterion,
                        text_en="Empirical reference, not a scoring rule: " + item.text,
                        text_zh="经验参考，不是评分规则：" + item.text,
                        observation_ids=(observation.observation_id,),
                        source_authority=CoachingAuthority.RAG_EVIDENCE,
                        source_ids=(item.object_id,),
                    ) for item in gate.accepted_items[:2])
                coaching = build_full_coaching(score, value.bundle, value.diagnosis,
                                              (*value.drafts, *references), rag_evidence=gate)
            except (EvidenceGateError, CoachingContractError):
                gate = None
                coaching = base
                consumer_reason = "EVIDENCE_CONSUMER_REJECTED"
        validate_full_coaching(coaching)
        if score.content() != before or coaching.locked_score_sha256 != score.snapshot_sha256:
            raise ValueError("Post-score consumer changed locked scores.")
        projection = self.runtime.projection()
        projection.update(evidenceState=gate.status.value if gate else sidecar_result.status.value,
                          evidenceReason=consumer_reason or (sidecar_result.reason.value if sidecar_result.reason else None),
                          consumer="build_full_coaching" if gate else None)
        smoke = {
            "queryFingerprint": sidecar_result.query_sha256,
            "denseCandidateCount": pack.dense_candidate_count if pack else 0,
            "bm25CandidateCount": pack.bm25_candidate_count if pack else 0,
            "fusedResultCount": len(pack.items) if pack else 0,
            "evidenceGate": gate.status.value if gate else "NOT_REACHED",
            "acceptedEvidenceCount": len(gate.accepted_items) if gate else 0,
            "consumer": "build_full_coaching" if gate else None,
            "consumerRagItemCount": sum(i.source_authority is CoachingAuthority.RAG_EVIDENCE for i in coaching.items),
            "lockedScoreIdentical": score.content() == before,
            "directScoreAuthority": False,
            "rerankerInvoked": False,
        }
        return PostScoreCoachingResult(coaching, projection, smoke)


class C3RagCoachingProcessor:
    """Inject into C3's leased worker, never into a presentation-to-engine shortcut.

    foundation is the accepted assessment adapter: it runs BEFORE retrieval and
    must return locked artifacts bound to the worker's exact current submission.
    Non-complete assessment outcomes pass through unchanged, without RAG access.
    """

    def __init__(self, foundation: Callable, coaching: PostScoreRagCoachingService):
        self.foundation, self.coaching = foundation, coaching

    def __call__(self, request):
        value = self.foundation(request)
        if isinstance(value, ProcessingOutcome):
            if value.state is SubmissionState.COMPLETE:
                raise ValueError("Complete local assessments require typed locked artifacts for post-score composition.")
            return value
        if not isinstance(value, PostScoreCoachingInputs):
            raise ValueError("Typed post-score artifacts required.")
        if (value.submission.task_type != request.task_type or value.submission.question != request.question
                or value.submission.essay_version.original_text != request.candidate_script):
            raise ValueError("Worker submission and assessment lineage mismatch.")
        result = self.coaching.compose(value)
        report = compose_mode_a_report(value.locked_score, result.coaching)
        payload = report.content()
        payload["rag"] = result.rag_projection
        if value.calibration_audit is not None:
            payload['calibrationAudit'] = value.calibration_audit
        if value.detailed_report is not None:
            payload['detailedReport'] = value.detailed_report
        from .learning_context import build_learning_context
        context = build_learning_context(value, result.coaching)
        if context is not None:
            payload["learningContext"] = context
        # Persistence, tenant access, idempotency, audit and deletion remain owned by C3.
        return ProcessingOutcome(SubmissionState(report.status.value), payload)
