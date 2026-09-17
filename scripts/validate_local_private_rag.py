#!/usr/bin/env python3
"""Opt-in real retrieval smoke; synthetic learner, private data never printed/written."""
from __future__ import annotations

import json
from pathlib import Path
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.application.rag_coaching import PostScoreCoachingInputs, PostScoreRagCoachingService
from app.core.rag_evidence import EvidenceGateContext
from app.core.rag_historical import package_sentinel
from app.core.rag_local_runtime import LocalRagRuntime
from app.core.rag_sidecar import ValidatedObservation
from app.product_composition.service import DraftLoopApplicationService
from app.product_platform.contracts import ApiRequest, SubmitCommand
from tests.c2_support import complete_coaching_drafts, task2_foundation_fixture
from tests.c3_support import C3Fixture


def smoke_inputs():
    submission, bundle, score, diagnosis = task2_foundation_fixture()
    assessment = next(a for a in bundle.assessments if a.criterion == "GRA")
    observation = ValidatedObservation("task2", "GRA", "o0004", "GRA fixture observation",
        "Practise grammatical accuracy, complex sentence structures and error-free sentences.",
        assessment.authoritative_lineage["studentEvidenceSha256"])
    return PostScoreCoachingInputs(submission, bundle, score, diagnosis,
        complete_coaching_drafts(diagnosis), observation,
        EvidenceGateContext("GRA", (), ("LICENSE_UNCLEAR",), ("PRIVATE_RESEARCH_ONLY",)))


def run_smoke(runtime):
    assert runtime.projection()["state"] == "RAG_ENABLED", runtime.projection()["reason"]
    handle = runtime.validation.runtime_handle
    before = package_sentinel(handle.authorized.root)
    inputs = smoke_inputs()
    score_before = inputs.locked_score.content()
    service = PostScoreRagCoachingService(runtime)
    result = service.compose(inputs)
    assert result.smoke["denseCandidateCount"] == 20
    assert result.smoke["bm25CandidateCount"] == 20
    assert result.smoke["fusedResultCount"] == 5
    assert result.smoke["evidenceGate"] == "SUFFICIENT", result.smoke
    assert result.smoke["consumerRagItemCount"] >= 1
    assert result.coaching.locked_score_sha256 == inputs.locked_score.snapshot_sha256

    fx = C3Fixture()
    try:
        fx.service.rag_runtime = runtime
        facade = DraftLoopApplicationService(fx.service)
        command = SubmitCommand(inputs.submission.task_type, inputs.submission.question,
            inputs.submission.essay_version.original_text, "local-rag-safe-fixture-001")
        submission_id = facade.submit(fx.learner, command)["submissionId"]
        processor = fx.service.local_coaching_processor(lambda request: inputs)
        job = fx.service.run_one_job("local-rag-smoke-worker", processor)
        assert job["state"] == "SUCCEEDED", job
        projection = facade.presentation_projection(fx.learner, submission_id)
        assert projection.payload["rag"]["consumer"] == "build_full_coaching"
        assert projection.payload["rag"]["evidenceState"] == "SUFFICIENT"
        assert fx.router.handle(ApiRequest("GET", "/v1/runtime/rag", {}, {})).status == 401
        response = fx.router.handle(ApiRequest("GET", "/v1/runtime/rag", fx.learner_headers, {}))
        assert response.status == 200 and response.body["rag"]["state"] == "RAG_ENABLED"
        assert facade.submit(fx.learner, command)["created"] is False
        assert fx.service.run_one_job("duplicate-smoke", processor) is None
    finally:
        fx.close()
    disabled = LocalRagRuntime({})
    rubric_only = PostScoreRagCoachingService(disabled).compose(inputs)
    assert rubric_only.coaching.locked_score_sha256 == result.coaching.locked_score_sha256
    assert inputs.locked_score.content() == score_before
    handle.assert_unchanged()
    assert package_sentinel(handle.authorized.root) == before
    return {
        "fixtureId": "local-rag-safe-fixture-001",
        "claimScope": "REAL_FROZEN_RETRIEVAL_WITH_SYNTHETIC_LEARNER_NOT_BETA_OR_SCORE_ACCURACY",
        "runtime": runtime.projection(),
        "smoke": result.smoke,
        "objectCount": len(runtime.validation.validated_package.object_order),
        "declaredArtifactHashes": "PASS",
        "c3WorkerPersistenceAndFacade": "PASS",
        "authenticatedStatusProjection": "PASS",
        "idempotentDuplicate": "PASS",
        "ragOnOffLockedScoreIdentical": True,
        "frozenPackageBytesUnchanged": True,
        "packageObjectsCopiedOrCommitted": False,
    }


def main():
    runtime = LocalRagRuntime()
    try:
        if runtime.provider is None:
            print(json.dumps({"runtime": runtime.projection()}, ensure_ascii=False, indent=2))
            return 1
        print(json.dumps(run_smoke(runtime), ensure_ascii=False, indent=2))
        return 0
    finally:
        runtime.close()


if __name__ == "__main__":
    raise SystemExit(main())
