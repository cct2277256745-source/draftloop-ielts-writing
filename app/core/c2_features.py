"""Versioned C2 feature and reverse-order rollback contract."""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any

from .submission import digest


C2_FEATURE_POLICY_VERSION = "c2-feature-rollback-v1"


class C2FeatureError(ValueError):
    """A feature configuration violates C2 dependency order."""


class C2RollbackLayer(str, Enum):
    MEMORY_RECOMMENDATIONS = "MEMORY_RECOMMENDATIONS"
    MEMORY_WRITES = "MEMORY_WRITES"
    TOPIC_PACKS_AND_MINIMAL_EDITS = "TOPIC_PACKS_AND_MINIMAL_EDITS"
    GUIDED_REVISION = "GUIDED_REVISION"
    FULL_COACHING = "FULL_COACHING"


@dataclass(frozen=True)
class C2FeatureFlags:
    full_coaching_enabled: bool = True
    guided_revision_enabled: bool = True
    topic_packs_and_minimal_edits_enabled: bool = True
    memory_writes_enabled: bool = True
    memory_recommendations_enabled: bool = True

    def __post_init__(self) -> None:
        values = (
            self.full_coaching_enabled,
            self.guided_revision_enabled,
            self.topic_packs_and_minimal_edits_enabled,
            self.memory_writes_enabled,
            self.memory_recommendations_enabled,
        )
        if any(not isinstance(value, bool) for value in values):
            raise C2FeatureError("C2 feature flags must be booleans.")
        # Every later layer depends on all earlier layers. Disabled lower layers
        # therefore require every dependent higher layer to be disabled too.
        seen_disabled = False
        for value in values:
            if seen_disabled and value:
                raise C2FeatureError("C2 feature flags violate dependency order.")
            seen_disabled = seen_disabled or not value

    @property
    def full_loop_enabled(self) -> bool:
        return all(self.content().values())

    def content(self) -> dict[str, bool]:
        return {
            "C2_FULL_COACHING_ENABLED": self.full_coaching_enabled,
            "C2_GUIDED_REVISION_ENABLED": self.guided_revision_enabled,
            "C2_TOPIC_PACK_MINIMAL_EDIT_ENABLED": self.topic_packs_and_minimal_edits_enabled,
            "C2_MEMORY_WRITES_ENABLED": self.memory_writes_enabled,
            "C2_MEMORY_RECOMMENDATIONS_ENABLED": self.memory_recommendations_enabled,
        }


@dataclass(frozen=True)
class C2RollbackDecision:
    disabled_from: C2RollbackLayer | None
    flags: C2FeatureFlags
    locked_assessment_preserved: bool
    decision_sha256: str
    policy_version: str = C2_FEATURE_POLICY_VERSION

    def content(self, *, include_hash: bool = True) -> dict[str, Any]:
        value = {
            "policyVersion": self.policy_version,
            "disabledFrom": self.disabled_from.value if self.disabled_from else None,
            "flags": self.flags.content(),
            "lockedAssessmentPreserved": self.locked_assessment_preserved,
        }
        if include_hash:
            value["decisionSha256"] = self.decision_sha256
        return value


def c2_rollback_decision(disabled_from: C2RollbackLayer | None = None) -> C2RollbackDecision:
    enabled_count = {
        None: 5,
        C2RollbackLayer.MEMORY_RECOMMENDATIONS: 4,
        C2RollbackLayer.MEMORY_WRITES: 3,
        C2RollbackLayer.TOPIC_PACKS_AND_MINIMAL_EDITS: 2,
        C2RollbackLayer.GUIDED_REVISION: 1,
        C2RollbackLayer.FULL_COACHING: 0,
    }[disabled_from]
    values = tuple(index < enabled_count for index in range(5))
    flags = C2FeatureFlags(*values)
    partial = C2RollbackDecision(disabled_from, flags, True, "")
    return C2RollbackDecision(
        disabled_from,
        flags,
        True,
        digest(partial.content(include_hash=False)),
    )
