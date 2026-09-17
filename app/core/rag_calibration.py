"""Comparable paired calibration and target-specific RAG promotion decisions."""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any, Sequence

from .rag_evidence import EvidenceGateStatus
from .rag_package import (
    DiscoveryStatus,
    RagDiscoveryDecision,
    RuntimeValidationOutcome,
    RuntimeValidationStatus,
)
from .submission import digest


RAG_CALIBRATION_VERSION = "rag-paired-calibration-v1"
RAG_PROMOTION_POLICY_VERSION = "rag-promotion-policy-v1"


class RagCalibrationError(ValueError):
    """A paired run is incomplete, non-comparable, or leaks score authority."""


class UtilityAdjudication(str, Enum):
    IMPROVED = "IMPROVED"
    UNCHANGED = "UNCHANGED"
    WORSENED = "WORSENED"


class RagProductDecision(str, Enum):
    RAG_PROMOTED_FOR_PRODUCT = "RAG_PROMOTED_FOR_PRODUCT"
    RAG_AVAILABLE_BUT_NOT_PROMOTED = "RAG_AVAILABLE_BUT_NOT_PROMOTED"
    RAG_DISABLED = "RAG_DISABLED"


@dataclass(frozen=True)
class PairedCalibrationConfig:
    case_ids: tuple[str, ...]
    scoring_configuration_sha256: str
    rubric_runtime_sha256: str
    provider_routes_sha256: str
    prompt_set_sha256: str
    config_sha256: str
    version: str = RAG_CALIBRATION_VERSION

    @classmethod
    def create(
        cls,
        *,
        case_ids: Sequence[str],
        scoring_configuration_sha256: str,
        rubric_runtime_sha256: str,
        provider_routes_sha256: str,
        prompt_set_sha256: str,
    ) -> "PairedCalibrationConfig":
        ids = tuple(case_ids)
        if not ids or len(ids) != len(set(ids)) or any(not item for item in ids):
            raise RagCalibrationError("Calibration requires ordered unique case IDs.")
        fields = (
            scoring_configuration_sha256,
            rubric_runtime_sha256,
            provider_routes_sha256,
            prompt_set_sha256,
        )
        if any(not item for item in fields):
            raise RagCalibrationError("Calibration configuration lineage is incomplete.")
        payload = {
            "version": RAG_CALIBRATION_VERSION,
            "caseIds": list(ids),
            "scoringConfigurationSha256": fields[0],
            "rubricRuntimeSha256": fields[1],
            "providerRoutesSha256": fields[2],
            "promptSetSha256": fields[3],
        }
        return cls(ids, *fields, digest(payload))


@dataclass(frozen=True)
class PairedCalibrationCase:
    case_id: str
    task_type: str
    criterion: str
    rubric_only_locked_score_sha256: str
    rag_arm_locked_score_sha256: str
    gate_status: EvidenceGateStatus
    utility: UtilityAdjudication
    utility_evidence_ids: tuple[str, ...]
    rag_direct_score_authority: bool
    rubric_only_latency_ms: int
    rag_arm_latency_ms: int
    rag_cost_state: str = "UNKNOWN_NO_PRICE_POLICY"
    rag_failure: bool = False

    def __post_init__(self) -> None:
        if self.task_type not in ("task1", "task2") or not self.case_id or not self.criterion:
            raise RagCalibrationError("A paired calibration case is invalid.")
        if not self.rubric_only_locked_score_sha256 or not self.rag_arm_locked_score_sha256:
            raise RagCalibrationError("Both paired arms require locked score identities.")
        if self.rag_direct_score_authority is not False:
            raise RagCalibrationError("The RAG arm may not have direct score authority.")
        if min(self.rubric_only_latency_ms, self.rag_arm_latency_ms) < 0:
            raise RagCalibrationError("Calibration latency cannot be negative.")
        if not self.rag_cost_state:
            raise RagCalibrationError("Calibration cost state must be explicit.")
        if not self.utility_evidence_ids:
            raise RagCalibrationError("Utility adjudication requires evidence IDs.")

    def content(self) -> dict[str, Any]:
        return {
            "caseId": self.case_id,
            "taskType": self.task_type,
            "criterion": self.criterion,
            "rubricOnlyLockedScoreSha256": self.rubric_only_locked_score_sha256,
            "ragArmLockedScoreSha256": self.rag_arm_locked_score_sha256,
            "baseScoreChanged": self.rubric_only_locked_score_sha256 != self.rag_arm_locked_score_sha256,
            "gateStatus": self.gate_status.value,
            "utility": self.utility.value,
            "utilityEvidenceIds": list(self.utility_evidence_ids),
            "ragDirectScoreAuthority": False,
            "rubricOnlyLatencyMs": self.rubric_only_latency_ms,
            "ragArmLatencyMs": self.rag_arm_latency_ms,
            "ragCostState": self.rag_cost_state,
            "ragFailure": self.rag_failure,
        }


@dataclass(frozen=True)
class PairedCalibrationReport:
    config_sha256: str
    cases: tuple[PairedCalibrationCase, ...]
    improved_count: int
    unchanged_count: int
    worsened_count: int
    disabled_gate_count: int
    failure_count: int
    unknown_cost_count: int
    maximum_added_latency_ms: int
    report_sha256: str
    version: str = RAG_CALIBRATION_VERSION

    def content(self, *, include_hash: bool = True) -> dict[str, Any]:
        value = {
            "version": self.version,
            "configSha256": self.config_sha256,
            "cases": [item.content() for item in self.cases],
            "improvedCount": self.improved_count,
            "unchangedCount": self.unchanged_count,
            "worsenedCount": self.worsened_count,
            "disabledGateCount": self.disabled_gate_count,
            "failureCount": self.failure_count,
            "unknownCostCount": self.unknown_cost_count,
            "maximumAddedLatencyMs": self.maximum_added_latency_ms,
            "directScoreAuthority": False,
        }
        if include_hash:
            value["reportSha256"] = self.report_sha256
        return value


def build_paired_calibration_report(
    config: PairedCalibrationConfig,
    cases: Sequence[PairedCalibrationCase],
) -> PairedCalibrationReport:
    values = tuple(cases)
    if tuple(item.case_id for item in values) != config.case_ids:
        raise RagCalibrationError("Paired cases are missing, duplicated, or out of configured order.")
    if any(item.rubric_only_locked_score_sha256 != item.rag_arm_locked_score_sha256 for item in values):
        raise RagCalibrationError("RAG changed a code-owned locked base score.")
    counts = {item: sum(case.utility is item for case in values) for item in UtilityAdjudication}
    provisional = PairedCalibrationReport(
        config_sha256=config.config_sha256,
        cases=values,
        improved_count=counts[UtilityAdjudication.IMPROVED],
        unchanged_count=counts[UtilityAdjudication.UNCHANGED],
        worsened_count=counts[UtilityAdjudication.WORSENED],
        disabled_gate_count=sum(case.gate_status is not EvidenceGateStatus.SUFFICIENT for case in values),
        failure_count=sum(case.rag_failure for case in values),
        unknown_cost_count=sum(not case.rag_cost_state.startswith("KNOWN") for case in values),
        maximum_added_latency_ms=max(
            (max(0, case.rag_arm_latency_ms - case.rubric_only_latency_ms) for case in values),
            default=0,
        ),
        report_sha256="",
    )
    return PairedCalibrationReport(
        config_sha256=provisional.config_sha256,
        cases=provisional.cases,
        improved_count=provisional.improved_count,
        unchanged_count=provisional.unchanged_count,
        worsened_count=provisional.worsened_count,
        disabled_gate_count=provisional.disabled_gate_count,
        failure_count=provisional.failure_count,
        unknown_cost_count=provisional.unknown_cost_count,
        maximum_added_latency_ms=provisional.maximum_added_latency_ms,
        report_sha256=digest(provisional.content(include_hash=False)),
    )


@dataclass(frozen=True)
class RagPromotionPolicy:
    minimum_improved_fraction: float = 0.5
    maximum_added_latency_ms: int = 1000
    allow_worsened_cases: bool = False
    allow_failures: bool = False

    def __post_init__(self) -> None:
        if not 0 <= self.minimum_improved_fraction <= 1 or self.maximum_added_latency_ms < 0:
            raise RagCalibrationError("The RAG promotion policy is invalid.")


@dataclass(frozen=True)
class RagPromotionDecision:
    decision: RagProductDecision
    target_environment: str | None
    default_enabled: bool
    reasons: tuple[str, ...]
    discovery_decision_sha256: str
    runtime_validation_sha256: str | None
    calibration_report_sha256: str | None
    rollback_state: str
    decision_sha256: str
    policy_version: str = RAG_PROMOTION_POLICY_VERSION

    def content(self, *, include_hash: bool = True) -> dict[str, Any]:
        value = {
            "policyVersion": self.policy_version,
            "decision": self.decision.value,
            "targetEnvironment": self.target_environment,
            "defaultEnabled": self.default_enabled,
            "reasons": list(self.reasons),
            "discoveryDecisionSha256": self.discovery_decision_sha256,
            "runtimeValidationSha256": self.runtime_validation_sha256,
            "calibrationReportSha256": self.calibration_report_sha256,
            "directScoreAuthority": False,
            "rollbackState": self.rollback_state,
        }
        if include_hash:
            value["decisionSha256"] = self.decision_sha256
        return value


def decide_rag_promotion(
    discovery: RagDiscoveryDecision,
    runtime: RuntimeValidationOutcome | None,
    calibration: PairedCalibrationReport | None,
    *,
    policy: RagPromotionPolicy = RagPromotionPolicy(),
) -> RagPromotionDecision:
    reasons: list[str] = []
    runtime_sha: str | None = None
    if discovery.status is DiscoveryStatus.RAG_DISABLED:
        decision = RagProductDecision.RAG_DISABLED
        reasons.append(discovery.reason.value if discovery.reason else "DISCOVERY_DISABLED")
    elif runtime is None or runtime.status is not RuntimeValidationStatus.VALIDATED:
        decision = RagProductDecision.RAG_DISABLED
        reasons.append(runtime.reason.value if runtime and runtime.reason else "RUNTIME_NOT_VALIDATED")
    else:
        assert runtime.validated_package is not None
        runtime_sha = runtime.validated_package.validation_sha256
        if calibration is None:
            decision = RagProductDecision.RAG_AVAILABLE_BUT_NOT_PROMOTED
            reasons.append("CALIBRATION_NOT_AVAILABLE")
        else:
            total = len(calibration.cases)
            improved_fraction = calibration.improved_count / total if total else 0.0
            if calibration.worsened_count and not policy.allow_worsened_cases:
                reasons.append("WORSENED_CASES_PRESENT")
            if calibration.failure_count and not policy.allow_failures:
                reasons.append("RAG_FAILURES_PRESENT")
            if calibration.disabled_gate_count:
                reasons.append("EVIDENCE_GATE_DISABLED_CASES_PRESENT")
            if calibration.unknown_cost_count:
                reasons.append("COST_NOT_ESTABLISHED")
            if calibration.maximum_added_latency_ms > policy.maximum_added_latency_ms:
                reasons.append("LATENCY_THRESHOLD_EXCEEDED")
            if improved_fraction < policy.minimum_improved_fraction:
                reasons.append("UTILITY_THRESHOLD_NOT_MET")
            decision = (
                RagProductDecision.RAG_PROMOTED_FOR_PRODUCT
                if not reasons
                else RagProductDecision.RAG_AVAILABLE_BUT_NOT_PROMOTED
            )
    provisional = RagPromotionDecision(
        decision=decision,
        target_environment=discovery.target_environment,
        default_enabled=decision is RagProductDecision.RAG_PROMOTED_FOR_PRODUCT,
        reasons=tuple(reasons),
        discovery_decision_sha256=discovery.decision_sha256,
        runtime_validation_sha256=runtime_sha,
        calibration_report_sha256=calibration.report_sha256 if calibration else None,
        rollback_state="RAG_DISABLED_RUBRIC_ONLY",
        decision_sha256="",
    )
    return RagPromotionDecision(
        decision=provisional.decision,
        target_environment=provisional.target_environment,
        default_enabled=provisional.default_enabled,
        reasons=provisional.reasons,
        discovery_decision_sha256=provisional.discovery_decision_sha256,
        runtime_validation_sha256=provisional.runtime_validation_sha256,
        calibration_report_sha256=provisional.calibration_report_sha256,
        rollback_state=provisional.rollback_state,
        decision_sha256=digest(provisional.content(include_hash=False)),
    )
