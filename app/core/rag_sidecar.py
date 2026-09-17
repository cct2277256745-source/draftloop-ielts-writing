"""Versioned advisory query construction and failure-isolated RAG execution."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeoutError
from dataclasses import dataclass
from enum import Enum
import re
import threading
import time
from typing import Callable

from .assessment_finalization import LockedScoreSnapshot, validate_locked_score_snapshot
from .rag_retrieval import RetrievalEvidencePack, RetrievalProvider, RetrievalProviderError
from .submission import digest


RAG_QUERY_VERSION = "rag-advisory-query-v1"
RAG_SIDECAR_VERSION = "rag-sidecar-v1"


class RagSidecarError(ValueError):
    """A query or sidecar configuration violates the isolation contract."""


class SidecarStatus(str, Enum):
    EVIDENCE_AVAILABLE = "EVIDENCE_AVAILABLE"
    RAG_DISABLED = "RAG_DISABLED"


class SidecarDisabledReason(str, Enum):
    FEATURE_DISABLED = "FEATURE_DISABLED"
    PACKAGE_DISABLED = "PACKAGE_DISABLED"
    QUERY_REJECTED = "QUERY_REJECTED"
    TIMEOUT = "TIMEOUT"
    PROVIDER_FAILURE = "PROVIDER_FAILURE"
    CIRCUIT_BREAKER_OPEN = "CIRCUIT_BREAKER_OPEN"


@dataclass(frozen=True)
class ValidatedObservation:
    task_type: str
    criterion: str
    observation_id: str
    observation_text: str
    learner_need: str
    source_artifact_sha256: str

    def __post_init__(self) -> None:
        if self.task_type not in ("task1", "task2"):
            raise RagSidecarError("A RAG observation requires a supported task type.")
        if not all((self.criterion, self.observation_id, self.observation_text, self.learner_need, self.source_artifact_sha256)):
            raise RagSidecarError("A RAG observation must be validated and lineage-bound.")
        if len(self.observation_text) > 1200 or len(self.learner_need) > 400:
            raise RagSidecarError("A RAG observation exceeds the bounded query contract.")


@dataclass(frozen=True)
class RagQueryIntent:
    task_type: str
    advisory_criterion: str
    observation_id: str
    source_artifact_sha256: str
    search_text: str
    query_sha256: str
    version: str = RAG_QUERY_VERSION

    def content(self, *, include_search_text: bool = True, include_hash: bool = True) -> dict[str, str | bool]:
        value: dict[str, str | bool] = {
            "version": self.version,
            "taskType": self.task_type,
            "advisoryCriterion": self.advisory_criterion,
            "criterionIsHardFilter": False,
            "observationId": self.observation_id,
            "sourceArtifactSha256": self.source_artifact_sha256,
        }
        if include_search_text:
            value["searchText"] = self.search_text
        if include_hash:
            value["querySha256"] = self.query_sha256
        return value


_INJECTION_PATTERNS = (
    r"ignore\s+(all\s+)?previous",
    r"\bsystem\s*:",
    r"<\s*/?\s*(script|system|assistant)",
    r"\b(desired|source|target)\s+band\b",
    r"\bsimilarity\s+score\b",
    r"direct_score_authority",
)


def _plain_query_fragment(value: str) -> str:
    if any(re.search(pattern, value, flags=re.IGNORECASE) for pattern in _INJECTION_PATTERNS):
        raise RagSidecarError("The observation contains score-transfer or instruction-injection text.")
    if any(ord(character) < 32 and character not in "\t\n\r" for character in value):
        raise RagSidecarError("The observation contains unsafe control characters.")
    return " ".join(value.split())


def build_rag_query(observation: ValidatedObservation) -> RagQueryIntent:
    """Build a data-only query after a validated assessment observation exists."""
    if not isinstance(observation, ValidatedObservation):
        raise RagSidecarError("RAG query construction requires a validated observation.")
    observed = _plain_query_fragment(observation.observation_text)
    need = _plain_query_fragment(observation.learner_need)
    search_text = f"Task {observation.task_type[-1]} {observation.criterion}. Observed: {observed}. Need: {need}."
    provisional = RagQueryIntent(
        task_type=observation.task_type,
        advisory_criterion=observation.criterion,
        observation_id=observation.observation_id,
        source_artifact_sha256=observation.source_artifact_sha256,
        search_text=search_text,
        query_sha256="",
    )
    return RagQueryIntent(
        task_type=provisional.task_type,
        advisory_criterion=provisional.advisory_criterion,
        observation_id=provisional.observation_id,
        source_artifact_sha256=provisional.source_artifact_sha256,
        search_text=provisional.search_text,
        query_sha256=digest(provisional.content(include_hash=False)),
    )


@dataclass(frozen=True)
class RagSidecarResult:
    status: SidecarStatus
    reason: SidecarDisabledReason | None
    evidence_pack: RetrievalEvidencePack | None
    locked_score_sha256: str
    query_sha256: str | None
    elapsed_ms: int
    consecutive_failures: int
    trace_sha256: str
    version: str = RAG_SIDECAR_VERSION

    def content(self, *, include_hash: bool = True) -> dict[str, object]:
        value: dict[str, object] = {
            "version": self.version,
            "status": self.status.value,
            "reason": self.reason.value if self.reason else None,
            "lockedScoreSha256": self.locked_score_sha256,
            "baseScoreChanged": False,
            "querySha256": self.query_sha256,
            "queryTextLogged": False,
            "evidenceTextLogged": False,
            "evidencePackSha256": self.evidence_pack.pack_sha256 if self.evidence_pack else None,
            "elapsedMs": self.elapsed_ms,
            "consecutiveFailures": self.consecutive_failures,
        }
        if include_hash:
            value["traceSha256"] = self.trace_sha256
        return value


def _result(
    *,
    status: SidecarStatus,
    reason: SidecarDisabledReason | None,
    pack: RetrievalEvidencePack | None,
    locked_score_sha256: str,
    query_sha256: str | None,
    elapsed_ms: int,
    failures: int,
) -> RagSidecarResult:
    provisional = RagSidecarResult(
        status=status,
        reason=reason,
        evidence_pack=pack,
        locked_score_sha256=locked_score_sha256,
        query_sha256=query_sha256,
        elapsed_ms=elapsed_ms,
        consecutive_failures=failures,
        trace_sha256="",
    )
    return RagSidecarResult(
        status=provisional.status,
        reason=provisional.reason,
        evidence_pack=provisional.evidence_pack,
        locked_score_sha256=provisional.locked_score_sha256,
        query_sha256=provisional.query_sha256,
        elapsed_ms=provisional.elapsed_ms,
        consecutive_failures=provisional.consecutive_failures,
        trace_sha256=digest(provisional.content(include_hash=False)),
    )


class RagSidecar:
    """Optional post-score sidecar with timeout and circuit-breaker isolation."""

    def __init__(
        self,
        provider: RetrievalProvider | None,
        *,
        enabled: bool,
        timeout_seconds: float = 1.0,
        breaker_threshold: int = 3,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if timeout_seconds <= 0 or breaker_threshold < 1:
            raise RagSidecarError("The timeout and breaker policy are invalid.")
        self._provider = provider
        self._enabled = enabled
        self._timeout_seconds = timeout_seconds
        self._breaker_threshold = breaker_threshold
        self._clock = clock
        self._failures = 0
        self._lock = threading.Lock()

    def _disabled(
        self,
        score_hash: str,
        reason: SidecarDisabledReason,
        *,
        query_sha256: str | None = None,
        elapsed_ms: int = 0,
    ) -> RagSidecarResult:
        with self._lock:
            failures = self._failures
        return _result(
            status=SidecarStatus.RAG_DISABLED,
            reason=reason,
            pack=None,
            locked_score_sha256=score_hash,
            query_sha256=query_sha256,
            elapsed_ms=elapsed_ms,
            failures=failures,
        )

    def retrieve_after_scoring(
        self,
        observation: ValidatedObservation,
        locked_score: LockedScoreSnapshot,
    ) -> RagSidecarResult:
        validate_locked_score_snapshot(locked_score)
        score_hash = locked_score.snapshot_sha256
        if not self._enabled:
            return self._disabled(score_hash, SidecarDisabledReason.FEATURE_DISABLED)
        if self._provider is None:
            return self._disabled(score_hash, SidecarDisabledReason.PACKAGE_DISABLED)
        with self._lock:
            breaker_open = self._failures >= self._breaker_threshold
        if breaker_open:
            return self._disabled(score_hash, SidecarDisabledReason.CIRCUIT_BREAKER_OPEN)
        try:
            query = build_rag_query(observation)
        except RagSidecarError:
            return self._disabled(score_hash, SidecarDisabledReason.QUERY_REJECTED)

        started = self._clock()
        executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="rag-sidecar")
        future = executor.submit(
            self._provider.retrieve,
            query.search_text,
            query.advisory_criterion,
            5,
        )
        reason: SidecarDisabledReason | None = None
        pack: RetrievalEvidencePack | None = None
        try:
            pack = future.result(timeout=self._timeout_seconds)
        except FutureTimeoutError:
            future.cancel()
            reason = SidecarDisabledReason.TIMEOUT
        except (RetrievalProviderError, OSError, RuntimeError, ValueError):
            reason = SidecarDisabledReason.PROVIDER_FAILURE
        finally:
            executor.shutdown(wait=False)
        elapsed_ms = max(0, int(round((self._clock() - started) * 1000)))

        validate_locked_score_snapshot(locked_score)
        if locked_score.snapshot_sha256 != score_hash:
            raise RagSidecarError("The RAG sidecar changed the locked score identity.")
        if reason is not None:
            with self._lock:
                self._failures += 1
                failures = self._failures
            return _result(
                status=SidecarStatus.RAG_DISABLED,
                reason=reason,
                pack=None,
                locked_score_sha256=score_hash,
                query_sha256=query.query_sha256,
                elapsed_ms=elapsed_ms,
                failures=failures,
            )
        with self._lock:
            self._failures = 0
        return _result(
            status=SidecarStatus.EVIDENCE_AVAILABLE,
            reason=None,
            pack=pack,
            locked_score_sha256=score_hash,
            query_sha256=query.query_sha256,
            elapsed_ms=elapsed_ms,
            failures=0,
        )
