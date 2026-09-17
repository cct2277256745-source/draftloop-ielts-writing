"""主批改、句法增强、最终验证三阶段 IELTS 批改流水线。"""
from __future__ import annotations

import copy
from contextvars import ContextVar
from dataclasses import asdict, dataclass, field, replace
import hashlib
import json
import math
import os
from pathlib import Path
import time
from typing import Any, Callable, TypeVar
import warnings

import requests

from .errors import LLMError
from .image_input import ImageInput, validate_image_input
from .models import GradingRequest, GradingResult
from .rubric import StructuredRubricSnapshot, is_approved_task2_snapshot
from .observability import (
    ArtifactLineage,
    CacheIdentity,
    PHASE0_RUBRIC_VERSION,
    PricingCatalog,
    ScoreCache,
    TraceLedger,
    TraceRecorder,
    VersionSnapshot,
    WORKFLOW_VERSION,
)
from .providers import (
    Capability,
    ModelCapability,
    ModelRoute,
    OpenAICompatibleTransport,
    ProviderCallRequest,
    ProviderCallResult,
    ProviderConfigurationSnapshot,
    ProviderContract,
    ProviderFailure,
    ProviderFailureCode,
    RouteResolution,
    endpoint_for,
    provider_preflight,
    resolve_route,
)
from .submission import SubmissionError, SubmissionSnapshot
from .task2_understanding import (
    Task2Understanding,
    Task2UnderstandingProviderError,
    Task2UnderstandingSchemaError,
    Task2UnderstandingService,
    UnderstandingStatus,
    UNDERSTANDING_SEMANTIC_VERSION,
    prompt_version as task2_understanding_prompt_version,
)
from .student_evidence import (
    StudentEvidence,
    StudentEvidenceProviderError,
    StudentEvidenceSchemaError,
    StudentEvidenceService,
    StudentEvidenceStatus,
    STUDENT_EVIDENCE_SEMANTIC_VERSION,
    prompt_version as student_evidence_prompt_version,
)
from .settings import AppSettings, QWEN_REVIEW_PROVIDER
from .workflow import (
    StageFailure,
    WorkflowFailureState,
    WorkflowResult,
    WorkflowStage,
)

CONNECT_TIMEOUT = 20
READ_TIMEOUT = int(os.getenv("IELTS_API_READ_TIMEOUT", "300"))
PROMPT_DIR = Path(__file__).resolve().parent.parent / "prompts"
_SCORE_CACHE_LIMIT = 32
_ORIGINAL_SCORE_CACHE: ScoreCache["ScoreSnapshot"] = ScoreCache(_SCORE_CACHE_LIMIT)
_LOCAL_TRACE_LEDGER = TraceLedger()
_LAST_COMPATIBILITY_RESULT: ContextVar[ProviderCallResult | None] = ContextVar(
    "last_compatibility_result", default=None
)

LEGACY_TASK2_CALIBRATION = (
    "Score conservatively when evidence is borderline.",
    "Treat Overall as the arithmetic outcome of four independently justified criterion scores, not an impressionistic label. A script consistently at Band 7 across all four criteria is Overall 7.0, not 7.5. Retain the existing half-band calculation behaviour; examples such as 8/8/7/7 or 8/7.5/7.5/7 may produce 7.5, but do not assign four generous 7.5 scores merely to reach a target.",
    "Choose the lower adjacent score unless higher-band qualities are clear, specific and sustained.",
    "Assess score-limiting weaknesses before strengths. A clear, readable essay may remain 6.5-7.0 when ideas are insufficiently extended, cohesion is mechanical, vocabulary is vague or repetitive, or complex grammar is not consistently accurate.",
    "Do not compensate across criteria: vocabulary cannot repair underdeveloped Task Response, organisation cannot raise weak GRA, and accurate simple sentences alone do not demonstrate higher grammatical range.",
    "Apply the legacy TR 6.5 ceiling when a clear position exists but body ideas, consequences, or support remain general, repetitive, asserted rather than explained, or unevenly covered.",
    "Apply the legacy CC 6.5-7.0 ceiling when progression is understandable but paragraph logic is formulaic, linking is conspicuous, referencing is weak, or sentences are listed rather than developed.",
    "Apply the legacy LR 6.5 ceiling when meaning is communicated but wording repeatedly relies on broad words, memorised phrases, imprecise paraphrase, awkward collocations, or inconsistent formality.",
    "Apply the legacy GRA 6.5 ceiling when complex forms are attempted but accuracy is unstable, errors recur, sentence boundaries are unreliable, or most accurate sentences use safe or repetitive patterns.",
    "Use caution at 7.5 and 8.5. Absence of lower-band weaknesses is not sufficient positive evidence; verify any quality above 7.0 across the response rather than in one sentence or paragraph.",
    "Identify each criterion's strongest score-limiting weakness, assign all four independently, and calculate Overall only afterwards rather than reverse-engineering criterion scores.",
    "Before returning scores, check whether a strict assessment could plausibly be 0.5 lower.",
)


class StageSchemaError(LLMError):
    """A redacted stage-boundary validation failure."""


class JSONResponseError(StageSchemaError):
    """Compatibility name for exhausted JSON/schema repair without raw bodies."""

    def __init__(self, message: str) -> None:
        super().__init__(message)


@dataclass(frozen=True)
class LockedBandScore:
    label: str
    score: float
    rationale: str = ""


@dataclass(frozen=True)
class ScoreSnapshot:
    overall_band: float
    criteria: tuple[LockedBandScore, ...]


@dataclass(frozen=True)
class MainStageData:
    task_type: str
    summary: str
    primary_draft: str = field(repr=False)
    scores: ScoreSnapshot
    chart_understanding: dict[str, Any] = field(repr=False, compare=False)
    data_accuracy_check: dict[str, Any] = field(repr=False, compare=False)
    overview_check: dict[str, Any] = field(repr=False, compare=False)
    score_diagnosis: dict[str, Any] = field(repr=False, compare=False)
    paragraph_feedback: tuple[dict[str, Any], ...] = field(repr=False, compare=False)
    vocabulary_upgrades: tuple[dict[str, Any], ...] = field(repr=False, compare=False)
    memorise_worthy: tuple[dict[str, Any], ...] = field(repr=False, compare=False)
    next_practice: tuple[str, ...] = field(repr=False, compare=False)


@dataclass(frozen=True)
class SyntaxStageData:
    enhanced_draft: str = field(repr=False)
    upgrades: tuple[dict[str, Any], ...] = field(repr=False, compare=False)


@dataclass(frozen=True)
class FinalStageData:
    values: dict[str, Any] = field(repr=False, compare=False)


class ProviderFailureError(LLMError):
    """Compatibility-facing exception retaining the normalized failure category."""

    def __init__(self, failure: ProviderFailure) -> None:
        super().__init__(failure.message)
        self.failure = failure


def grade(
    request: GradingRequest,
    provider: str = QWEN_REVIEW_PROVIDER,
    model: str = "",
    api_key: str = "",
    base_url: str = "",
    settings: AppSettings | None = None,
    trace_ledger: TraceLedger | None = None,
    pricing_catalog: PricingCatalog | None = None,
    trace_id: str | None = None,
    rubric_snapshot: StructuredRubricSnapshot | None = None,
    understanding_service: Task2UnderstandingService | None = None,
    student_evidence_service: StudentEvidenceService | None = None,
) -> WorkflowResult[GradingResult]:
    """执行三阶段流水线。

    旧的 provider/model/api_key 参数保留用于兼容；新 UI 直接传 settings。
    """
    if not request.question.strip():
        raise LLMError("请先输入雅思写作题目。")
    if not request.essay.strip():
        raise LLMError("请先粘贴考生作文。")

    if request.taskType == "task2":
        if not is_approved_task2_snapshot(rubric_snapshot):
            return WorkflowResult.failed(_rubric_failure())
    elif rubric_snapshot is not None:
        return WorkflowResult.failed(_rubric_failure())

    snapshot: SubmissionSnapshot | None = None
    if request.taskType == "task2":
        try:
            snapshot = SubmissionSnapshot.from_request(request)
        except SubmissionError as exc:
            raise LLMError("Task 2 submission cannot be versioned.") from exc

    image_input = validate_image_input(request.taskType, request.imagePath).require()

    cfg = settings or AppSettings.load()
    if api_key and not settings:
        qwen = cfg.config_for(request.taskType, "qwen")
        qwen.model = model or qwen.model
        qwen.api_key = api_key
        qwen.base_url = base_url or qwen.base_url

    routes = provider_preflight(cfg, request.taskType, image_input is not None)
    # Legacy direct parameters modify the primary route only, exactly as before.
    if api_key and not settings:
        routes = provider_preflight(cfg, request.taskType, image_input is not None)
        primary = routes["main_review"].route
        if primary is not None:
            routes["main_review"] = _legacy_primary_resolution(
                primary, qwen, image_input is not None
            )
        final = routes["final_validation"].route
        if final is not None:
            routes["final_validation"] = _legacy_primary_resolution(
                final, qwen, image_input is not None
            )
        understanding = routes.get("task2_understanding")
        if understanding is not None:
            routes["task2_understanding"] = _legacy_primary_resolution(
                understanding.route, qwen, image_input is not None
            )
        evidence = routes.get("student_evidence")
        if evidence is not None:
            routes["student_evidence"] = _legacy_primary_resolution(
                evidence.route, qwen, image_input is not None
            )

    main_contract = _require_contract(routes["main_review"])
    final_contract = _require_contract(routes["final_validation"])
    syntax_resolution = routes["syntax_enhancement"]
    understanding_contract = (
        _require_contract(routes["task2_understanding"])
        if request.taskType == "task2" else None
    )
    evidence_contract = (
        _require_contract(routes["student_evidence"])
        if request.taskType == "task2" else None
    )
    main_route = routes["main_review"].route
    if main_route is None:
        raise ProviderFailureError(ProviderFailure(
            ProviderFailureCode.PROVIDER_FAILURE,
            "主批改路由解析失败。",
        ))
    versions = VersionSnapshot(
        route=main_route.route_id,
        model=main_contract.model.model_id,
        model_version=f"configured:{main_contract.model.model_id}",
        prompt_version=_prompt_version(request.taskType, "qwen_main_review"),
        rubric_version=(rubric_snapshot.version if rubric_snapshot else PHASE0_RUBRIC_VERSION),
        workflow_version=WORKFLOW_VERSION,
        rubric_id=(rubric_snapshot.rubric_id if rubric_snapshot else "task1:not-integrated"),
        runtime_content_sha256=(
            rubric_snapshot.runtime_content_sha256 if rubric_snapshot else "not-integrated"
        ),
        preprocessing_version=(snapshot.essay_version.preprocessing_version if snapshot else ""),
        understanding_prompt_version=(task2_understanding_prompt_version() if snapshot else ""),
        understanding_semantic_version=(UNDERSTANDING_SEMANTIC_VERSION if snapshot else ""),
        student_evidence_prompt_version=(student_evidence_prompt_version() if snapshot else ""),
        student_evidence_semantic_version=(STUDENT_EVIDENCE_SEMANTIC_VERSION if snapshot else ""),
    )
    tracer = TraceRecorder(
        trace_ledger or _LOCAL_TRACE_LEDGER,
        CacheIdentity.from_submission(
            request.taskType, request.question, request.essay, versions
        ),
        trace_id=trace_id,
        pricing=pricing_catalog,
    )

    def finish(outcome: WorkflowResult[GradingResult]) -> WorkflowResult[GradingResult]:
        failure = (
            outcome.stage_failures[0].state.value
            if outcome.stage_failures
            else (outcome.review_reasons[0].value if outcome.review_reasons else None)
        )
        tracer.finish(outcome.status.value, failure)
        return outcome

    task2_understanding: Task2Understanding | None = None
    student_evidence: StudentEvidence | None = None
    cache_identity: CacheIdentity | None = None
    if snapshot is not None:
        assert understanding_contract is not None
        service = understanding_service or Task2UnderstandingService(OpenAICompatibleTransport())

        def record_understanding_attempt(
            attempt: int, latency_ms: int, schema_status: str, result: ProviderCallResult
        ) -> None:
            tracer.record_provider_call(
                stage=WorkflowStage.TASK2_UNDERSTANDING.value,
                contract=understanding_contract,
                prompt_version=versions.understanding_prompt_version,
                attempt=attempt,
                latency_ms=latency_ms,
                schema_status=schema_status,
                result=result,
            )

        try:
            outcome = service.run(snapshot, understanding_contract, on_attempt=record_understanding_attempt)
        except Task2UnderstandingSchemaError:
            return finish(WorkflowResult.failed(_schema_failure(WorkflowStage.TASK2_UNDERSTANDING)))
        except Task2UnderstandingProviderError:
            return finish(WorkflowResult.failed(_provider_failure(WorkflowStage.TASK2_UNDERSTANDING)))
        if outcome.review_required:
            tracer.bind_artifact_lineage(ArtifactLineage(
                submission_snapshot_id=snapshot.submission_snapshot_id,
                essay_version_id=snapshot.essay_version.essay_version_id,
                locator_manifest_sha256=snapshot.essay_version.locator_manifest_sha256,
                task2_understanding_sha256=outcome.artifact.artifact_sha256,
            ))
            return finish(WorkflowResult.review_required(
                WorkflowFailureState.TASK_DEFINITION_UNRESOLVED
            ))
        task2_understanding = outcome.artifact
        versions = replace(versions, understanding_sha256=task2_understanding.artifact_sha256)
        assert evidence_contract is not None
        evidence_service = student_evidence_service or StudentEvidenceService(OpenAICompatibleTransport())

        def record_evidence_attempt(
            attempt: int, latency_ms: int, schema_status: str, result: ProviderCallResult
        ) -> None:
            tracer.record_provider_call(
                stage=WorkflowStage.STUDENT_EVIDENCE.value,
                contract=evidence_contract,
                prompt_version=versions.student_evidence_prompt_version,
                attempt=attempt,
                latency_ms=latency_ms,
                schema_status=schema_status,
                result=result,
            )

        try:
            evidence_outcome = evidence_service.run(
                snapshot, task2_understanding, evidence_contract, on_attempt=record_evidence_attempt
            )
        except StudentEvidenceSchemaError:
            return finish(WorkflowResult.failed(_student_evidence_invalid_failure()))
        except StudentEvidenceProviderError:
            return finish(WorkflowResult.failed(_provider_failure(WorkflowStage.STUDENT_EVIDENCE)))
        if evidence_outcome.review_required:
            tracer.bind_artifact_lineage(ArtifactLineage(
                submission_snapshot_id=snapshot.submission_snapshot_id,
                essay_version_id=snapshot.essay_version.essay_version_id,
                locator_manifest_sha256=snapshot.essay_version.locator_manifest_sha256,
                task2_understanding_sha256=task2_understanding.artifact_sha256,
                student_evidence_sha256=evidence_outcome.artifact.artifact_sha256,
            ))
            return finish(WorkflowResult.review_required(
                WorkflowFailureState.STUDENT_EVIDENCE_INCOMPLETE
            ))
        student_evidence = evidence_outcome.artifact
        versions = replace(versions, student_evidence_sha256=student_evidence.artifact_sha256)
        cache_identity = CacheIdentity.from_artifacts(
            submission_snapshot_sha256=snapshot.snapshot_sha256,
            understanding_sha256=task2_understanding.artifact_sha256,
            student_evidence_sha256=student_evidence.artifact_sha256,
            versions=versions,
        )
        tracer.bind_cache_identity(cache_identity)
        tracer.bind_artifact_lineage(ArtifactLineage(
            submission_snapshot_id=snapshot.submission_snapshot_id,
            essay_version_id=snapshot.essay_version.essay_version_id,
            locator_manifest_sha256=snapshot.essay_version.locator_manifest_sha256,
            task2_understanding_sha256=task2_understanding.artifact_sha256,
            student_evidence_sha256=student_evidence.artifact_sha256,
        ))

    try:
        main_data = _stabilize_score_snapshot(
            call_main_review(
                request, main_contract, image_input, rubric_snapshot, tracer,
                task2_understanding, student_evidence,
            ),
            request, cache_identity,
            versions,
            tracer,
        )
    except StageSchemaError:
        return finish(WorkflowResult.failed(_schema_failure(WorkflowStage.MAIN_REVIEW)))
    except ProviderFailureError:
        return finish(WorkflowResult.failed(_provider_failure(WorkflowStage.MAIN_REVIEW)))
    except Exception:
        return finish(WorkflowResult.failed(_unexpected_failure(WorkflowStage.MAIN_REVIEW)))

    syntax_data: SyntaxStageData | None = None
    syntax_failure: StageFailure | None = None
    if syntax_resolution.ok:
        try:
            syntax_data = call_syntax_enhancement(
                request, _require_contract(syntax_resolution), main_data, tracer
            )
        except StageSchemaError:
            syntax_failure = _schema_failure(WorkflowStage.SYNTAX_ENHANCEMENT)
        except ProviderFailureError:
            syntax_failure = _provider_failure(WorkflowStage.SYNTAX_ENHANCEMENT)
        except Exception:
            return finish(WorkflowResult.failed(
                _unexpected_failure(WorkflowStage.SYNTAX_ENHANCEMENT)
            ))
    else:
        syntax_failure = _provider_failure(WorkflowStage.SYNTAX_ENHANCEMENT)

    try:
        final_data = call_final_validation(
            request,
            final_contract,
            image_input,
            main_data,
            syntax_data,
            rubric_snapshot,
            tracer,
        )
    except StageSchemaError:
        failures = tuple(
            failure for failure in (
                syntax_failure,
                _schema_failure(WorkflowStage.FINAL_VALIDATION),
            ) if failure is not None
        )
        return finish(WorkflowResult.partial(*failures))
    except ProviderFailureError:
        failures = tuple(
            failure for failure in (
                syntax_failure,
                _provider_failure(WorkflowStage.FINAL_VALIDATION),
            ) if failure is not None
        )
        return finish(WorkflowResult.partial(*failures))
    except Exception:
        return finish(WorkflowResult.failed(
            _unexpected_failure(WorkflowStage.FINAL_VALIDATION)
        ))

    if syntax_failure is not None:
        return finish(WorkflowResult.partial(syntax_failure))

    try:
        report = _compose_complete_report(
            request, main_data, syntax_data, final_data, versions
        )
    except Exception:
        return finish(WorkflowResult.failed(
            _unexpected_failure(WorkflowStage.FINAL_VALIDATION)
        ))
    return finish(WorkflowResult.complete(report))


def call_main_review(
    request: GradingRequest,
    contract: ProviderContract,
    image_input: ImageInput | None,
    rubric_snapshot: StructuredRubricSnapshot | None = None,
    tracer: TraceRecorder | None = None,
    task2_understanding: Task2Understanding | None = None,
    student_evidence: StudentEvidence | None = None,
) -> MainStageData:
    content = _authorized_multimodal_content(
        _main_user_text(request, rubric_snapshot, task2_understanding, student_evidence), contract, image_input
    )
    return _call_json_with_retry(
        [{"role": "system", "content": _prompt(request.taskType, "qwen_main_review")},
         {"role": "user", "content": content}],
        contract,
        "主批改路由",
        lambda text: _validate_main_stage(text, request.taskType),
        stage=WorkflowStage.MAIN_REVIEW.value,
        prompt_version=_prompt_version(request.taskType, "qwen_main_review"),
        tracer=tracer,
    )


def call_syntax_enhancement(
    request: GradingRequest,
    contract: ProviderContract,
    main_data: MainStageData,
    tracer: TraceRecorder | None = None,
) -> SyntaxStageData:
    payload = {
        "taskType": request.taskType,
        "targetBand": request.targetBand,
        "targetBandStyle": _target_band_style(request.targetBand),
        "question": request.question,
        "chartSummary": copy.deepcopy(main_data.chart_understanding),
        "candidateEssay": request.essay,
        "qwenExamReadyVersion": main_data.primary_draft,
        "requiredOutputSchema": _syntax_schema(),
    }
    # The syntax route is deliberately text-only. It receives derived chart
    # understanding, never the original pixels.
    content = json.dumps(payload, ensure_ascii=False)
    return _call_json_with_retry(
        [{"role": "system", "content": _prompt(request.taskType, "deepseek_syntax_enhancer")},
         {"role": "user", "content": content}],
        contract,
        "句法增强路由",
        _validate_syntax_stage,
        stage=WorkflowStage.SYNTAX_ENHANCEMENT.value,
        prompt_version=_prompt_version(request.taskType, "deepseek_syntax_enhancer"),
        tracer=tracer,
    )


def call_final_validation(
    request: GradingRequest,
    contract: ProviderContract,
    image_input: ImageInput | None,
    main_data: MainStageData,
    syntax_data: SyntaxStageData | None,
    rubric_snapshot: StructuredRubricSnapshot | None = None,
    tracer: TraceRecorder | None = None,
) -> FinalStageData:
    payload = {
        "taskType": request.taskType,
        "targetBand": request.targetBand,
        "targetBandStyle": _target_band_style(request.targetBand),
        "question": request.question,
        "candidateEssay": request.essay,
        "originalScores": {
            "overallBand": main_data.scores.overall_band,
            "criteria": [asdict(score) for score in main_data.scores.criteria],
        },
        "rubricIdentity": rubric_snapshot.identity() if rubric_snapshot else None,
        "qwenExamReadyVersion": main_data.primary_draft,
        "chartUnderstanding": copy.deepcopy(main_data.chart_understanding),
        "dataAccuracyCheck": copy.deepcopy(main_data.data_accuracy_check),
        "overviewCheck": copy.deepcopy(main_data.overview_check),
        "scoreDiagnosisDraft": copy.deepcopy(main_data.score_diagnosis),
        "paragraphFeedbackDraft": copy.deepcopy(list(main_data.paragraph_feedback)),
        "vocabularyUpgradesDraft": copy.deepcopy(list(main_data.vocabulary_upgrades)),
        "memoriseWorthyExpressionsDraft": copy.deepcopy(list(main_data.memorise_worthy)),
        "nextPracticeSuggestionsDraft": list(main_data.next_practice),
        "deepseekSyntaxEnhancedVersion": (
            syntax_data.enhanced_draft if syntax_data is not None else ""
        ),
        "syntaxUpgradesDraft": (
            copy.deepcopy(list(syntax_data.upgrades)) if syntax_data is not None else []
        ),
        "requiredOutputSchema": _final_validator_schema(request.taskType),
        "instruction": (
            "The original scores belong to the candidate essay and must remain exactly "
            "the same regardless of targetBand. targetBand controls only rewriting, "
            "gap analysis, upgrades and study recommendations. Revalidate and directly "
            "rewrite every student-facing draft section. Do not return a decision log. "
            "No expression rejected as stiff, specialist or over-academic may remain in "
            "vocabulary, syntax, the final essay or memorisation items. "
            "For Task 1, re-read the attached ORIGINAL chart image now. "
            "Do not rely only on chartSummary when validating data and trends."
        ),
    }
    content = _authorized_multimodal_content(
        json.dumps(payload, ensure_ascii=False), contract, image_input
    )
    return _call_json_with_retry(
        [{"role": "system", "content": _prompt(request.taskType, "qwen_final_validator")},
         {"role": "user", "content": content}],
        contract,
        "最终验证路由",
        lambda text: _validate_final_stage(text, request.taskType),
        stage=WorkflowStage.FINAL_VALIDATION.value,
        prompt_version=_prompt_version(request.taskType, "qwen_final_validator"),
        tracer=tracer,
    )


def build_main_review_payload(
    request: GradingRequest,
    rubric_snapshot: StructuredRubricSnapshot,
    task2_understanding: Task2Understanding | None = None,
    student_evidence: StudentEvidence | None = None,
) -> dict[str, Any]:
    """Build the Task 2 scoring input with strictly separated authority layers."""
    if request.taskType != "task2" or not is_approved_task2_snapshot(rubric_snapshot):
        raise LLMError("Task 2 rubric snapshot does not match the request.")
    if task2_understanding is None or task2_understanding.status is not UnderstandingStatus.VALID:
        raise LLMError("Task 2 understanding is required before scoring.")
    if student_evidence is None or student_evidence.status is not StudentEvidenceStatus.VALID:
        raise LLMError("Student Evidence is required before scoring.")
    payload = rubric_snapshot.to_prompt_payload()
    payload.update({
        "legacyInternalCalibration": {
            "authority": "LEGACY_INTERNAL_HEURISTIC",
            "isOfficialIeltsAuthority": False,
            "mayOverrideOfficialRubric": False,
            "rules": list(LEGACY_TASK2_CALIBRATION),
        },
        "productExecutionInvariants": {
            "scoreBeforeRewrite": True,
            "targetBandMayChangeOriginalScores": False,
        },
        "taskType": request.taskType,
        "task2Understanding": task2_understanding.main_review_projection(),
        "studentEvidence": student_evidence.main_review_projection(),
        "question": request.question.strip(),
        "candidateEssay": request.essay.strip(),
        "targetBand": request.targetBand,
        "targetBandPurpose": "rewrite_only",
        "targetBandStyle": _target_band_style(request.targetBand),
        "requiredOutputSchema": _main_review_schema(request.taskType),
    })
    return payload


def _main_user_text(
    request: GradingRequest,
    rubric_snapshot: StructuredRubricSnapshot | None = None,
    task2_understanding: Task2Understanding | None = None,
    student_evidence: StudentEvidence | None = None,
) -> str:
    if request.taskType == "task2":
        if rubric_snapshot is None:
            raise LLMError("Task 2 rubric snapshot is required.")
        return json.dumps(
            build_main_review_payload(
                request, rubric_snapshot, task2_understanding, student_evidence
            ), ensure_ascii=False
        )
    schema = _main_review_schema(request.taskType)
    reference_block = ""
    if request.references:
        references = []
        for ref in request.references:
            references.append(
                f"Question: {ref.question}\n"
                f"Chart note: {ref.chartNote}\n"
                f"Band: {ref.band:g}\n"
                f"Model essay:\n{ref.modelEssay}"
            )
        reference_block = (
            "Internal high-score references for calibration only. "
            "Do not copy or mention them to the candidate:\n\n"
            + "\n\n---\n\n".join(references)
        )

    parts = [
        (
            "SCORING ORDER: First score the candidate essay independently and complete "
            "the strict band audit in the system instructions. Do not look for a score "
            "that resembles the rewrite target. Only after fixing the original Overall, "
            "TR/TA, CC, LR and GRA scores should you plan the rewrite."
        ),
        f"Question:\n{request.question.strip()}",
        f"Candidate essay:\n{request.essay.strip()}",
        (
            f"Target band: {request.targetBand:g} "
            "(rewrite target only; not the candidate's score)"
        ),
        "Target-band rewriting profile:\n" + _target_band_style(request.targetBand),
    ]
    if reference_block:
        parts.append(reference_block)
    parts.append("Required JSON shape:\n" + json.dumps(schema, ensure_ascii=False))
    return "\n\n".join(parts)


def _prompt(task_type: str, stage: str) -> str:
    task_specific = PROMPT_DIR / f"{stage}_{task_type}.md"
    path = task_specific if task_specific.is_file() else PROMPT_DIR / f"{stage}.md"
    try:
        return path.read_text(encoding="utf-8")
    except OSError as exc:
        raise LLMError(f"无法读取 Prompt 文件 {path.name}：{exc}") from exc


def _prompt_version(task_type: str, stage: str) -> str:
    """Return an opaque content fingerprint rather than storing prompt text."""
    digest = hashlib.sha256(_prompt(task_type, stage).encode("utf-8")).hexdigest()
    return f"sha256:{digest}"


def _authorized_multimodal_content(
    text: str,
    contract: ProviderContract,
    image_input: ImageInput | None,
) -> str | list[dict[str, Any]]:
    if image_input is None:
        return text
    if not contract.model.supports(Capability.IMAGE_INPUT):
        raise ProviderFailureError(ProviderFailure(
            ProviderFailureCode.INCOMPATIBLE_ROUTE,
            "当前路由不支持 Task 1 图像输入。",
        ))
    return [
        {"type": "text", "text": text},
        {"type": "image_url", "image_url": {"url": image_input.as_data_url()}},
    ]


def _require_contract(resolution: RouteResolution) -> ProviderContract:
    if resolution.contract is not None:
        return resolution.contract
    failure = resolution.failure or ProviderFailure(
        ProviderFailureCode.PROVIDER_FAILURE,
        "Provider 路由解析失败。",
    )
    raise ProviderFailureError(failure)


def _legacy_primary_resolution(
    route: ModelRoute, config, requires_image: bool
) -> RouteResolution:
    return resolve_route(
        route,
        provider_id="openai-compatible",
        model_id=config.model,
        api_key=config.api_key,
        base_url=config.base_url,
        declared_capabilities=config.declared_capabilities,
        requires_image=requires_image,
    )


V = TypeVar("V")


def _call_json_with_retry(
    messages: list[dict[str, Any]],
    contract: ProviderContract,
    display: str,
    validator: Callable[[str], V],
    *,
    stage: str,
    prompt_version: str,
    tracer: TraceRecorder | None,
) -> V:
    first, first_latency = _timed_provider_call(contract, messages, display)
    try:
        first_text = _require_provider_result(first)
    except ProviderFailureError:
        _record_provider_attempt(
            tracer, stage, contract, prompt_version, 1, first_latency,
            "NOT_EVALUATED", first,
        )
        raise
    try:
        value = validator(first_text)
    except StageSchemaError:
        _record_provider_attempt(
            tracer, stage, contract, prompt_version, 1, first_latency,
            "INVALID", first,
        )
    else:
        _record_provider_attempt(
            tracer, stage, contract, prompt_version, 1, first_latency,
            "VALID", first,
        )
        return value

    retry_messages = copy.deepcopy(messages) + [{
        "role": "user",
        "content": "Your previous response was not valid JSON. Return only one complete valid JSON object.",
    }]
    retry, retry_latency = _timed_provider_call(contract, retry_messages, display)
    try:
        retry_text = _require_provider_result(retry)
    except ProviderFailureError:
        _record_provider_attempt(
            tracer, stage, contract, prompt_version, 2, retry_latency,
            "NOT_EVALUATED", retry,
        )
        raise
    try:
        value = validator(retry_text)
    except StageSchemaError as exc:
        _record_provider_attempt(
            tracer, stage, contract, prompt_version, 2, retry_latency,
            "INVALID", retry,
        )
        raise JSONResponseError(
            f"{display} 连续两次返回无效结构。"
        ) from exc
    _record_provider_attempt(
        tracer, stage, contract, prompt_version, 2, retry_latency,
        "VALID", retry,
    )
    return value


def _timed_provider_call(
    contract: ProviderContract, messages: list[dict[str, Any]], display: str
) -> tuple[ProviderCallResult, int]:
    started = time.monotonic()
    result = _call_through_compatibility_adapter(contract, messages, display)
    return result, int((time.monotonic() - started) * 1000)


def _record_provider_attempt(
    tracer: TraceRecorder | None,
    stage: str,
    contract: ProviderContract,
    prompt_version: str,
    attempt: int,
    latency_ms: int,
    schema_status: str,
    result: ProviderCallResult,
) -> None:
    if tracer is not None:
        tracer.record_provider_call(
            stage=stage,
            contract=contract,
            prompt_version=prompt_version,
            attempt=attempt,
            latency_ms=latency_ms,
            schema_status=schema_status,
            result=result,
        )


def _require_provider_result(result: ProviderCallResult) -> str:
    if result.ok:
        return result.content
    failure = result.failure or ProviderFailure(
        ProviderFailureCode.PROVIDER_FAILURE,
        "Provider 调用失败。",
    )
    raise ProviderFailureError(failure)


def _call_through_compatibility_adapter(
    contract: ProviderContract,
    messages: list[dict[str, Any]],
    display: str,
) -> ProviderCallResult:
    """Temporary bridge for the existing call shape; scoring sees a normalized result."""
    token = _LAST_COMPATIBILITY_RESULT.set(None)
    try:
        content = _call_openai_compatible_chat(
            contract.model.model_id,
            contract.api_key,
            messages,
            contract.snapshot.base_url,
            display,
            _legacy_call=False,
        )
        captured = _LAST_COMPATIBILITY_RESULT.get()
        return captured or ProviderCallResult(content=content)
    except ProviderFailureError as exc:
        return _LAST_COMPATIBILITY_RESULT.get() or ProviderCallResult(failure=exc.failure)
    except LLMError as exc:
        return ProviderCallResult.failed(ProviderFailureCode.PROVIDER_FAILURE, str(exc))
    finally:
        _LAST_COMPATIBILITY_RESULT.reset(token)


def _call_openai_compatible_chat(
    model: str,
    api_key: str,
    messages: list[dict[str, Any]],
    base_url: str,
    display: str,
    *,
    _legacy_call: bool = True,
) -> str:
    if _legacy_call:
        warnings.warn(
            "_call_openai_compatible_chat is a temporary compatibility adapter; "
            "new code must use ProviderContract and ProviderTransport.",
            DeprecationWarning,
            stacklevel=2,
        )
    endpoint = endpoint_for(base_url)
    if endpoint is None:
        raise ProviderFailureError(
            ProviderFailure(
                ProviderFailureCode.MALFORMED_ENDPOINT,
                f"{display} 的 Base URL 无效。",
            )
        )
    contract = ProviderContract(
        provider_id="legacy-openai-compatible",
        endpoint=endpoint,
        model=ModelCapability(model.strip(), frozenset(Capability)),
        snapshot=ProviderConfigurationSnapshot(
            route_key="legacy_compatibility",
            provider_id="legacy-openai-compatible",
            model_id=model.strip(),
            base_url=base_url.strip(),
            has_credentials=bool(api_key.strip()),
        ),
        api_key=api_key,
    )
    request = ProviderCallRequest(
        messages=messages,
        display_name=display,
        stream=True,
        timeout=(CONNECT_TIMEOUT, READ_TIMEOUT),
    )
    # `requests` and `time` remain injected here only for legacy test compatibility.
    result = OpenAICompatibleTransport(post=requests.post, sleep=time.sleep).call(contract, request)
    _LAST_COMPATIBILITY_RESULT.set(result)
    return _require_provider_result(result)


def _validate_main_stage(text: str, task_type: str) -> MainStageData:
    obj = _validated_object(text)
    _require_task_type(obj, task_type)
    scores = _validated_scores(obj, task_type)
    summary = _required_text(obj, "summary")
    primary_draft = _required_text(obj, "qwenExamReadyVersion")
    _validate_optional_report_sections(obj)

    if task_type == "task1":
        for key in ("chartUnderstanding", "dataAccuracyCheck", "overviewCheck"):
            _required_mapping(obj, key)

    return MainStageData(
        task_type=task_type,
        summary=summary,
        primary_draft=primary_draft,
        scores=scores,
        chart_understanding=copy.deepcopy(obj.get("chartUnderstanding", {})),
        data_accuracy_check=copy.deepcopy(obj.get("dataAccuracyCheck", {})),
        overview_check=copy.deepcopy(obj.get("overviewCheck", {})),
        score_diagnosis=copy.deepcopy(obj.get("scoreDiagnosis", {})),
        paragraph_feedback=tuple(copy.deepcopy(obj.get("paragraphFeedback", []))),
        vocabulary_upgrades=tuple(copy.deepcopy(obj.get("vocabularyUpgrades", []))),
        memorise_worthy=tuple(
            copy.deepcopy(obj.get("memoriseWorthyExpressions", []))
        ),
        next_practice=tuple(copy.deepcopy(obj.get("nextPracticeSuggestions", []))),
    )


def _validate_syntax_stage(text: str) -> SyntaxStageData:
    obj = _validated_object(text)
    enhanced = _required_text(obj, "deepseekSyntaxEnhancedVersion")
    upgrades = _required_mapping_list(obj, "syntaxUpgrades")
    return SyntaxStageData(enhanced, tuple(copy.deepcopy(upgrades)))


def _validate_final_stage(text: str, task_type: str) -> FinalStageData:
    obj = _validated_object(text)
    _require_task_type(obj, task_type)
    _required_text(obj, "balancedFinalVersion")
    _validate_score_diagnosis(obj.get("scoreDiagnosis"), task_type)
    for key in (
        "paragraphFeedback",
        "vocabularyUpgrades",
        "syntaxUpgrades",
        "memoriseWorthyExpressions",
    ):
        _required_mapping_list(obj, key)
    suggestions = obj.get("nextPracticeSuggestions")
    if not isinstance(suggestions, list) or any(
        not isinstance(item, str) for item in suggestions
    ):
        raise StageSchemaError("最终验证缺少有效学习建议。")
    if task_type == "task1":
        for key in ("chartUnderstanding", "dataAccuracyCheck", "overviewCheck"):
            _required_mapping(obj, key)
    return FinalStageData(copy.deepcopy(obj))


def _validated_object(text: str) -> dict[str, Any]:
    try:
        return _parse_json_object(text)
    except LLMError as exc:
        raise StageSchemaError("Provider 返回的内容不是有效 JSON 对象。") from exc


def _require_task_type(obj: dict[str, Any], task_type: str) -> None:
    if obj.get("taskType") != task_type:
        raise StageSchemaError("Provider 返回的任务类型不匹配。")


def _required_text(obj: dict[str, Any], key: str) -> str:
    value = obj.get(key)
    if not isinstance(value, str) or not value.strip():
        raise StageSchemaError(f"Provider 返回缺少必要字段 {key}。")
    return value


def _required_mapping(obj: dict[str, Any], key: str) -> dict[str, Any]:
    value = obj.get(key)
    if not isinstance(value, dict):
        raise StageSchemaError(f"Provider 返回字段 {key} 结构无效。")
    return value


def _required_mapping_list(
    obj: dict[str, Any], key: str
) -> list[dict[str, Any]]:
    value = obj.get(key)
    if not isinstance(value, list) or any(not isinstance(item, dict) for item in value):
        raise StageSchemaError(f"Provider 返回字段 {key} 结构无效。")
    return value


def _number(value: Any, field_name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise StageSchemaError(f"Provider 返回字段 {field_name} 必须是数字。")
    number = float(value)
    if not math.isfinite(number) or not 0.0 <= number <= 9.0:
        raise StageSchemaError(f"Provider 返回字段 {field_name} 超出有效范围。")
    if not math.isclose(number * 2, round(number * 2), abs_tol=1e-9):
        raise StageSchemaError(f"Provider 返回字段 {field_name} 不是有效半分档。")
    return number


def _validated_scores(obj: dict[str, Any], task_type: str) -> ScoreSnapshot:
    raw = obj.get("scores")
    expected = ("TA" if task_type == "task1" else "TR", "CC", "LR", "GRA")
    criteria: list[LockedBandScore] = []

    if isinstance(raw, list):
        if len(raw) != 4 or any(not isinstance(item, dict) for item in raw):
            raise StageSchemaError("Provider 必须返回四项评分。")
        seen: set[str] = set()
        for item in raw:
            label = item.get("label")
            if not isinstance(label, str):
                raise StageSchemaError("Provider 返回的评分标签无效。")
            normalized = label.strip().upper()
            if normalized not in expected or normalized in seen:
                raise StageSchemaError("Provider 返回的评分标签集合无效。")
            seen.add(normalized)
            criteria.append(LockedBandScore(
                label=normalized,
                score=_number(item.get("score"), f"scores.{normalized}"),
                rationale=(
                    item.get("rationale", "")
                    if isinstance(item.get("rationale", ""), str)
                    else ""
                ),
            ))
        if seen != set(expected):
            raise StageSchemaError("Provider 返回的评分标签集合无效。")
    elif isinstance(raw, dict):
        required = {"overall", "tr_ta", "cc", "lr", "gra"}
        if not required.issubset(raw):
            raise StageSchemaError("Provider 返回的评分字段不完整。")
        criteria = [
            LockedBandScore(expected[0], _number(raw["tr_ta"], "scores.tr_ta")),
            LockedBandScore("CC", _number(raw["cc"], "scores.cc")),
            LockedBandScore("LR", _number(raw["lr"], "scores.lr")),
            LockedBandScore("GRA", _number(raw["gra"], "scores.gra")),
        ]
    else:
        raise StageSchemaError("Provider 返回缺少四项评分。")

    overall_source = obj.get("overallBand")
    if overall_source is None and isinstance(raw, dict):
        overall_source = raw.get("overall")
    overall = _number(overall_source, "overallBand")
    if isinstance(raw, dict) and not math.isclose(
        overall, _number(raw.get("overall"), "scores.overall"), abs_tol=1e-9
    ):
        raise StageSchemaError("Provider 返回的总分字段相互冲突。")
    return ScoreSnapshot(overall, tuple(criteria))


def _validate_optional_report_sections(obj: dict[str, Any]) -> None:
    if "scoreDiagnosis" in obj and not isinstance(obj["scoreDiagnosis"], dict):
        raise StageSchemaError("主批改 scoreDiagnosis 结构无效。")
    for key in (
        "paragraphFeedback",
        "vocabularyUpgrades",
        "memoriseWorthyExpressions",
    ):
        if key in obj:
            _required_mapping_list(obj, key)
    if "nextPracticeSuggestions" in obj:
        value = obj["nextPracticeSuggestions"]
        if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
            raise StageSchemaError("主批改 nextPracticeSuggestions 结构无效。")


def _validate_score_diagnosis(value: Any, task_type: str) -> None:
    if not isinstance(value, dict):
        raise StageSchemaError("最终验证缺少 scoreDiagnosis。")
    for key in ("overall", "targetBand", "gapToTarget"):
        _number(value.get(key), f"scoreDiagnosis.{key}")
    criteria = value.get("criteria")
    if not isinstance(criteria, list) or len(criteria) != 4:
        raise StageSchemaError("最终验证 scoreDiagnosis 必须包含四项标准。")
    expected = {"TA" if task_type == "task1" else "TR", "CC", "LR", "GRA"}
    names: set[str] = set()
    for item in criteria:
        if not isinstance(item, dict) or not isinstance(item.get("name"), str):
            raise StageSchemaError("最终验证 scoreDiagnosis 标准结构无效。")
        name = item["name"].strip().upper()
        if name not in expected or name in names:
            raise StageSchemaError("最终验证 scoreDiagnosis 标签集合无效。")
        _number(item.get("score"), f"scoreDiagnosis.{name}")
        names.add(name)
    if names != expected:
        raise StageSchemaError("最终验证 scoreDiagnosis 标签集合无效。")


def _stabilize_score_snapshot(
    main_data: MainStageData,
    request: GradingRequest,
    cache_identity: CacheIdentity | None,
    versions: VersionSnapshot,
    tracer: TraceRecorder,
) -> MainStageData:
    identity = cache_identity or CacheIdentity.from_submission(
        request.taskType, request.question, request.essay, versions
    )
    lookup = _ORIGINAL_SCORE_CACHE.put_or_get(
        identity, main_data.scores, tracer.trace_id
    )
    tracer.record_cache(lookup)
    return replace(main_data, scores=lookup.value)


def local_trace_summary():
    """Return only aggregate, redacted in-memory telemetry for local diagnostics."""
    return _LOCAL_TRACE_LEDGER.summary()


def _compose_complete_report(
    request: GradingRequest,
    main_data: MainStageData,
    syntax_data: SyntaxStageData | None,
    final_data: FinalStageData,
    versions: VersionSnapshot,
) -> GradingResult:
    if syntax_data is None:
        raise ValueError("A complete report requires validated syntax-stage data.")
    values = copy.deepcopy(final_data.values)
    values.update({
        "taskType": request.taskType,
        "overallBand": main_data.scores.overall_band,
        "summary": main_data.summary,
        "scores": [asdict(item) for item in main_data.scores.criteria],
        "qwenExamReadyVersion": main_data.primary_draft,
        "deepseekSyntaxEnhancedVersion": syntax_data.enhanced_draft,
        "rawModelResponses": {},
        "rubric_id": versions.rubric_id,
        "rubric_version": versions.rubric_version,
        "runtime_content_sha256": versions.runtime_content_sha256,
    })
    diagnosis = copy.deepcopy(values["scoreDiagnosis"])
    diagnosis["overall"] = main_data.scores.overall_band
    diagnosis["targetBand"] = request.targetBand
    diagnosis["gapToTarget"] = max(
        0.0, round(request.targetBand - main_data.scores.overall_band, 1)
    )
    by_label = {item.label: item.score for item in main_data.scores.criteria}
    for criterion in diagnosis["criteria"]:
        criterion["score"] = by_label[criterion["name"].strip().upper()]
    values["scoreDiagnosis"] = diagnosis

    report = GradingResult.from_dict(values)
    blocked_expressions = {
        item.suggestedExpression.strip().casefold()
        for item in report.vocabularyUpgrades
        if item.suggestedExpression.strip()
        and (
            item.naturalness.strip().lower() == "stiff"
            or item.recommendation.strip().lower() == "avoid memorising"
        )
    }
    report.memoriseWorthyExpressions = [
        item for item in report.memoriseWorthyExpressions
        if item.expression.strip()
        and item.reusability.strip().lower() != "not recommended"
        and item.expression.strip().casefold() not in blocked_expressions
    ][:8]
    report.syntaxUpgrades = report.syntaxUpgrades[:(5 if request.targetBand < 7.75 else 6)]
    report.nextPracticeSuggestions = report.nextPracticeSuggestions[:3]
    return report


def _schema_failure(stage: WorkflowStage) -> StageFailure:
    return StageFailure(stage, WorkflowFailureState.SCHEMA_FAILURE, "阶段返回结构无效。")


def _student_evidence_invalid_failure() -> StageFailure:
    return StageFailure(
        WorkflowStage.STUDENT_EVIDENCE,
        WorkflowFailureState.STUDENT_EVIDENCE_INVALID,
        "Student Evidence is invalid or cannot be resolved to the current essay.",
    )


def _rubric_failure() -> StageFailure:
    return StageFailure(
        WorkflowStage.RUBRIC_PREFLIGHT,
        WorkflowFailureState.RUBRIC_FAILURE,
        "Task 2 rubric is unavailable or invalid.",
    )


def _provider_failure(stage: WorkflowStage) -> StageFailure:
    return StageFailure(stage, WorkflowFailureState.PROVIDER_FAILURE, "阶段调用失败。")


def _unexpected_failure(stage: WorkflowStage) -> StageFailure:
    return StageFailure(stage, WorkflowFailureState.UNEXPECTED_FAILURE, "阶段发生意外错误。")


def _target_band_style(target_band: float) -> str:
    if target_band < 7.75:
        return (
            "Band 7.5: exam-friendly, natural and accurate; preserve most of the "
            "candidate's wording; correct errors, awkward collocations, repetition and "
            "links; use only a few upgrades and at most one clearly advanced syntax "
            "upgrade per paragraph; prefer easy/medium reusable expressions."
        )
    if target_band < 8.25:
        return (
            "Band 8.0: natural but more mature; add precise collocations and controlled "
            "complex sentences, with moderate nominalisation, non-defining clauses, "
            "participle or with-structures; avoid stacking advanced vocabulary; allow "
            "medium and a small number of natural ambitious items."
        )
    return (
        "Band 8.5: polished, flexible and syntactically varied; allow more ambitious "
        "compression and organisation, but keep every expression natural, clear and "
        "learnable; do not sacrifice readability or turn the essay into an unfamiliar "
        "model answer; ambitious items must include usage-risk guidance."
    )


def _main_review_schema(task_type: str) -> dict[str, Any]:
    return {
        "taskType": task_type,
        "scores": {"overall": 0, "tr_ta": 0, "cc": 0, "lr": 0, "gra": 0},
        "summary": "",
        "scoreDiagnosis": {
            "overall": 0,
            "targetBand": 0,
            "gapToTarget": 0,
            "currentLevelSummaryEn": "",
            "currentLevelSummaryZh": "",
            "whyThisScoreEn": "",
            "whyThisScoreZh": "",
            "targetGapAnalysisEn": "",
            "targetGapAnalysisZh": "",
            "criteria": [{
                "name": "TR/TA|CC|LR|GRA",
                "score": 0,
                "commentEn": "",
                "commentZh": "",
                "mainProblemsEn": [],
                "mainProblemsZh": [],
                "nextStepEn": "",
                "nextStepZh": "",
            }],
        },
        "chartUnderstanding": {},
        "dataAccuracyCheck": {},
        "overviewCheck": {},
        "paragraphFeedback": [{
            "paragraphNumber": 1,
            "function": "",
            "strengthsEn": [],
            "issuesEn": [],
            "howToImproveEn": [],
            "sentenceUpgrade": {
                "original": "", "improved": "",
            },
        }],
        "vocabularyUpgrades": [{
            "originalExpression": "",
            "suggestedExpression": "",
            "whyBetterEn": "",
            "recommendation": "",
        }],
        "qwenExamReadyVersion": "",
    }


def _final_validator_schema(task_type: str) -> dict[str, Any]:
    schema: dict[str, Any] = {
        "taskType": task_type,
        "scoreDiagnosis": {
            "overall": 0,
            "targetBand": 0,
            "gapToTarget": 0,
            "currentLevelSummaryEn": "",
            "currentLevelSummaryZh": "",
            "whyThisScoreEn": "",
            "whyThisScoreZh": "",
            "targetGapAnalysisEn": "",
            "targetGapAnalysisZh": "",
            "criteria": [{
                "name": "TR/TA|CC|LR|GRA",
                "score": 0,
                "commentEn": "",
                "commentZh": "",
                "mainProblemsEn": [],
                "mainProblemsZh": [],
                "nextStepEn": "",
                "nextStepZh": "",
            }],
        },
        "paragraphFeedback": [{
            "paragraphNumber": 1,
            "function": "",
            "strengthsEn": [],
            "issuesEn": [],
            "howToImproveEn": [],
            "sentenceUpgrade": {
                "original": "",
                "improved": "",
            },
        }],
        "vocabularyUpgrades": [{
            "originalExpression": "",
            "suggestedExpression": "",
            "whyBetterEn": "",
            "recommendation": "",
        }],
        "balancedFinalVersion": "",
        "syntaxUpgrades": [_syntax_card_schema()],
        "memoriseWorthyExpressions": [{
            "expression": "",
            "meaningZh": "",
            "exampleSentence": "",
            "whyUsefulZh": "",
            "warningZh": "",
        }],
        "nextPracticeSuggestions": [],
    }
    if task_type == "task1":
        schema.update({
            "chartUnderstanding": {},
            "dataAccuracyCheck": {},
            "overviewCheck": {},
        })
    return schema


def _syntax_schema() -> dict[str, Any]:
    return {
        "deepseekSyntaxEnhancedVersion": "",
        "syntaxUpgrades": [_syntax_card_schema()],
    }


def _syntax_card_schema() -> dict[str, Any]:
    return {
        "syntaxTypeZh": "",
        "studentOriginalSentence": "",
        "finalUpgradedSentence": "",
        "whyWorthLearningZh": "",
        "howToReuseZh": "",
    }


def _parse_json_object(text: str) -> dict[str, Any]:
    raw = _extract_json(text)
    try:
        obj = json.loads(raw, strict=False)
    except json.JSONDecodeError as exc:
        raise LLMError(f"无法解析模型返回的 JSON：{exc}") from exc
    if not isinstance(obj, dict):
        raise LLMError("模型没有返回 JSON 对象。")
    return obj


def _extract_json(text: str) -> str:
    value = text.strip()
    if value.startswith("```"):
        value = value.split("```", 2)[1] if value.count("```") >= 2 else value
        if value.lstrip().lower().startswith("json"):
            value = value.lstrip()[4:]
    value = value.strip()
    first, last = value.find("{"), value.rfind("}")
    if first >= 0 and last > first:
        return value[first:last + 1]
    raise LLMError("模型没有返回 JSON 格式的结果。")
