"""Task 1 composition through the accepted independent vision/claim/score gates."""
from dataclasses import dataclass

from app.core.assessment_finalization import FinalizationBundle
from app.core.coaching import build_full_coaching
from app.core.image_input import validate_image_bytes
from app.core.providers import ProviderFailureCode
from app.core.rubric import load_task1_rubric
from app.core.submission import digest
from app.core.task1_claims import Task1ClaimService, Task1SubmissionSnapshot
from app.core.task1_facts import ChartFactsService, Task1FactPipeline, Task1ImageSnapshot
from app.core.task1_scoring import Task1AssessmentFoundationService, Task1CriterionScoringService
from app.product_platform.contracts import ProcessingOutcome, SubmissionState
from .local_task2 import LocalTask2Foundation
from .rag_coaching import PostScoreCoachingInputs


@dataclass(frozen=True)
class Task1CoachingEvidence:
    claims: object
    facts: object
    bundle: FinalizationBundle

    def main_review_projection(self):
        return {"authority": "VERIFIED_TASK1_FACTS_CLAIMS_AND_ACCEPTED_FINDINGS",
            "claimValidation": self.claims.content(), "reconciledFacts": self.facts.content(),
            "acceptedFindings": [{"criterion": item.criterion,
                "findings": item.content()["findings"]} for item in self.bundle.assessments]}


class LocalTask1Foundation(LocalTask2Foundation):
    task_type = "task1"

    def __call__(self, request):
        stage = "PREFLIGHT"
        def checkpoint(event):
            nonlocal stage
            stage = event["stage"]
            self.checkpoint(event)
        try:
            if request.task_type != "task1":
                return ProcessingOutcome(SubmissionState.FAILED, None, "TASK_NOT_SUPPORTED")
            image = validate_image_bytes(request.upload_bytes).require()
            source = Task1ImageSnapshot.create(request.question, image)
            submission = Task1SubmissionSnapshot.create(source, request.candidate_script)
            rubric = load_task1_rubric()
            calibration_audit = None
            def calibrate(initial, facts, claims):
                nonlocal calibration_audit
                from .rag_audit import RagCalibrationService
                scoring_route = self.resolution('criterion_scoring')
                def rescore(review):
                    scoring = Task1CriterionScoringService(self.transport, on_criterion=lambda event:
                        checkpoint({'stage': 'RESCORING', **event})).run(
                            submission, facts, claims, rubric,
                            self.resolution('criterion_scoring', selection='rescore'), calibration_review=review)
                    return FinalizationBundle.create('task1', scoring.assessments,
                        source_bundle_sha256=scoring.bundle.bundle_sha256) if scoring.bundle else None
                calibration = RagCalibrationService(self.rag_runtime, self.transport,
                    self.resolution('criterion_scoring', selection='audit').contract, checkpoint=checkpoint).run(submission, rubric,
                        Task1CoachingEvidence(claims, facts, initial), initial, rescore)
                calibration_audit = calibration.audit
                return calibration.bundle
            foundation = Task1AssessmentFoundationService(
                Task1FactPipeline(ChartFactsService(self.transport, role="EXTRACTOR"),
                    ChartFactsService(self.transport, role="INDEPENDENT_VERIFIER")),
                Task1ClaimService(self.transport),
                Task1CriterionScoringService(self.transport, on_criterion=lambda event:
                    checkpoint({"stage": "CRITERION_SCORING", **event})),
                on_checkpoint=checkpoint, prelock_review=calibrate)
            outcome = foundation.run(source, submission, rubric,
                extraction_resolution=self.resolution("chart_facts_extraction"),
                verification_resolution=self.resolution("chart_facts_verification"),
                claim_resolution=self.resolution("chart_claim_extraction"),
                scoring_resolution=self.resolution("criterion_scoring"), target_band=request.target_band)
            self.checkpoint({"stage": stage, "facts": outcome.fact_pipeline.reconciled.status.value,
                "extraction": outcome.fact_pipeline.extraction.status.value if outcome.fact_pipeline.extraction else None,
                "verification": outcome.fact_pipeline.verification.status.value if outcome.fact_pipeline.verification else None,
                "factConflicts": [{"reason": item.reason,
                    "extractorPresent": item.extractor_fact_id is not None,
                    "verifierPresent": item.verifier_fact_id is not None,
                    "kinds": sorted({fact.kind.value for artifact in
                        (outcome.fact_pipeline.extraction, outcome.fact_pipeline.verification)
                        if artifact is not None for fact in artifact.facts if fact.fact_key == item.fact_key})}
                    for item in outcome.fact_pipeline.reconciled.conflicts]})
            if outcome.claims is not None:
                self.checkpoint({"stage": stage, "claimStatus": outcome.claims.status.value,
                    "claimChecks": [{"type": item.claim_type.value, "status": item.status.value,
                        "reasons": list(item.reasons)} for item in outcome.claims.claims]})
            if outcome.locked_score is None or outcome.diagnosis is None:
                state = SubmissionState.REVIEW_REQUIRED if outcome.status.value == "REVIEW_REQUIRED" else SubmissionState.FAILED
                codes = [code for code in ProviderFailureCode if any(item == code.value or item.endswith(':' + code.value) for item in outcome.failures)]
                if codes:
                    return ProcessingOutcome(SubmissionState.FAILED, None,
                        ProviderFailureCode.QUOTA_EXCEEDED.value if ProviderFailureCode.QUOTA_EXCEEDED in codes else codes[0].value)
                return ProcessingOutcome(state, None, "TASK1_" + stage + "_INCOMPLETE")
            locked = outcome.locked_score
            bundle = outcome.finalization_bundle or FinalizationBundle.create("task1", outcome.assessments,
                source_bundle_sha256=locked.source_bundle_sha256)
            checkpoint({"stage": "LOCKED_SCORE", "lockedScoreSha256": locked.snapshot_sha256})
            checkpoint({"stage": "COACHING"})
            evidence = Task1CoachingEvidence(outcome.claims, outcome.fact_pipeline.reconciled, bundle)
            drafts = self.coaching_drafts(submission, evidence, outcome.diagnosis, locked, bundle)
            if build_full_coaching(locked, bundle, outcome.diagnosis, drafts).status.value != "COMPLETE":
                return ProcessingOutcome(SubmissionState.PARTIAL, None, "COACHING_INCOMPLETE")
            checkpoint({'stage':'DEEP_COACHING'})
            detailed=self.detailed_report(submission,evidence,locked,bundle,request.target_band)
            checkpoint({"stage": "POST_SCORE_READY", "lockedScoreSha256": locked.snapshot_sha256})
            # Task 1 has validated claim/locator references, not the Task 2 query
            # observation contract. Do not invent an observation for retrieval.
            return PostScoreCoachingInputs(submission, bundle, locked, outcome.diagnosis, drafts, None, None,
                outcome.claims, calibration_audit=calibration_audit, detailed_report=detailed)
        except Exception as exc:
            checkpoint({"stage": stage, "outcome": "FAILED", "exceptionType": type(exc).__name__,
                "errorFingerprint": digest(str(exc))})
            return ProcessingOutcome(SubmissionState.FAILED, None, "TASK1_" + stage + "_FAILED")
