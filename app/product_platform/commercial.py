"""C3-only schemas for later consented commercial evidence."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Sequence

from .contracts import ErrorCode, PlatformError, digest


COMMERCIAL_CONTRACT_VERSION = "c3-commercial-contract-v1"


@dataclass(frozen=True)
class ExperimentProtocol:
    experiment_id: str
    hypothesis: str
    population_definition: str
    consent_required: bool
    primary_metric: str
    denominator: str
    stopping_rule: str
    evidence_state: str = "UNOBSERVED_C3_CONTRACT"

    def __post_init__(self) -> None:
        values = (
            self.experiment_id,
            self.hypothesis,
            self.population_definition,
            self.primary_metric,
            self.denominator,
            self.stopping_rule,
        )
        if not all(isinstance(value, str) and value.strip() for value in values):
            raise PlatformError(ErrorCode.INVALID_REQUEST, "Experiment protocol fields are required.")
        if not self.consent_required or self.evidence_state != "UNOBSERVED_C3_CONTRACT":
            raise PlatformError(
                ErrorCode.INVALID_REQUEST,
                "C3 protocols must require consent and remain unobserved.",
            )

    def content(self) -> Mapping[str, Any]:
        return {
            "contractVersion": COMMERCIAL_CONTRACT_VERSION,
            "experimentId": self.experiment_id,
            "hypothesis": self.hypothesis,
            "populationDefinition": self.population_definition,
            "consentRequired": self.consent_required,
            "primaryMetric": self.primary_metric,
            "denominator": self.denominator,
            "stoppingRule": self.stopping_rule,
            "evidenceState": self.evidence_state,
        }


@dataclass(frozen=True)
class UnitEconomicsMetric:
    name: str
    unit: str
    numerator: str
    denominator: str
    observed_value: float | None = None
    evidence_state: str = "UNOBSERVED_C3_CONTRACT"

    def __post_init__(self) -> None:
        if not self.name or not self.unit or not self.numerator or not self.denominator:
            raise PlatformError(ErrorCode.INVALID_REQUEST, "Unit-economics definitions must be complete.")
        if self.observed_value is not None or self.evidence_state != "UNOBSERVED_C3_CONTRACT":
            raise PlatformError(ErrorCode.INVALID_REQUEST, "C3 cannot carry observed commercial values.")


def commercial_contract(
    protocols: Sequence[ExperimentProtocol],
    metrics: Sequence[UnitEconomicsMetric],
) -> Mapping[str, Any]:
    if not protocols or not metrics:
        raise PlatformError(ErrorCode.INVALID_REQUEST, "Commercial contracts require protocols and metrics.")
    content = {
        "contractVersion": COMMERCIAL_CONTRACT_VERSION,
        "evidenceState": "UNOBSERVED_C3_CONTRACT",
        "protocols": [dict(item.content()) for item in protocols],
        "unitEconomics": [item.__dict__ for item in metrics],
        "claimLimits": [
            "NO_REAL_USER_EVIDENCE",
            "NO_WILLINGNESS_TO_PAY_RESULT",
            "NO_COMMERCIAL_DECISION",
        ],
    }
    return {**content, "contractSha256": digest(content)}
