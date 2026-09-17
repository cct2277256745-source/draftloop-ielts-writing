"""Deterministic read-only Dense + BM25 + RRF retrieval boundary."""
from __future__ import annotations

from dataclasses import dataclass, field
import math
import time
from types import MappingProxyType
from typing import Any, Callable, Mapping, Sequence

from .rag_package import FrozenEvidenceObject, ValidatedFrozenPackage
from .submission import digest


RETRIEVAL_PROVIDER_VERSION = "frozen-retrieval-provider-v1"
RRF_K = 60
SOURCE_LIMIT = 20
MAX_TOP_K = 5


class RetrievalProviderError(ValueError):
    """The frozen retrieval provider could not produce a trustworthy pack."""


@dataclass(frozen=True)
class RankedCandidate:
    object_id: str
    score: float

    def __post_init__(self) -> None:
        if not self.object_id or isinstance(self.score, bool) or not math.isfinite(self.score):
            raise RetrievalProviderError("A retrieval candidate is invalid.")


@dataclass(frozen=True)
class RetrievalEvidenceItem:
    object_id: str
    object_type: str
    criterion: str | None
    features: tuple[str, ...]
    text: str = field(repr=False)
    provenance: str = field(repr=False)
    source_artifact_id: str = field(repr=False)
    rights: str = field(repr=False)
    usage: str = field(repr=False)
    dense_rank: int | None
    bm25_rank: int | None
    dense_retrieval_score: float | None = field(repr=False)
    bm25_retrieval_score: float | None = field(repr=False)
    rrf_score: float = field(repr=False)
    direct_score_authority: bool = False

    def __post_init__(self) -> None:
        if self.direct_score_authority is not False:
            raise RetrievalProviderError("Retrieved evidence may not have direct score authority.")

    def content(self, *, include_text: bool = True) -> dict[str, Any]:
        value = {
            "id": self.object_id,
            "type": self.object_type,
            "criterion": self.criterion,
            "features": list(self.features),
            "provenance": self.provenance,
            "sourceArtifactId": self.source_artifact_id,
            "rights": self.rights,
            "usage": self.usage,
            "denseRank": self.dense_rank,
            "bm25Rank": self.bm25_rank,
            "denseRetrievalScore": self.dense_retrieval_score,
            "bm25RetrievalScore": self.bm25_retrieval_score,
            "rrfScore": self.rrf_score,
            "directScoreAuthority": False,
            "retrievalScoresAreScoringAuthority": False,
        }
        if include_text:
            value["text"] = self.text
        return value


@dataclass(frozen=True)
class RetrievalEvidencePack:
    items: tuple[RetrievalEvidenceItem, ...]
    query_sha256: str
    advisory_criterion: str | None
    package_content_sha256: str
    package_version: str
    dense_candidate_count: int
    bm25_candidate_count: int
    elapsed_ms: int
    pack_sha256: str
    version: str = RETRIEVAL_PROVIDER_VERSION

    def content(self, *, include_hash: bool = True, include_text: bool = True) -> dict[str, Any]:
        value = {
            "version": self.version,
            "querySha256": self.query_sha256,
            "advisoryCriterion": self.advisory_criterion,
            "criterionWasHardFilter": False,
            "packageContentSha256": self.package_content_sha256,
            "packageVersion": self.package_version,
            "denseCandidateCount": self.dense_candidate_count,
            "bm25CandidateCount": self.bm25_candidate_count,
            "rrfK": RRF_K,
            "rerankerInvoked": False,
            "elapsedMs": self.elapsed_ms,
            "items": [item.content(include_text=include_text) for item in self.items],
        }
        if include_hash:
            value["packSha256"] = self.pack_sha256
        return value


SearchFunction = Callable[[str, int], Sequence[RankedCandidate]]


def _validate_candidates(
    candidates: Sequence[RankedCandidate],
    *,
    object_by_id: Mapping[str, FrozenEvidenceObject],
) -> tuple[RankedCandidate, ...]:
    limited = tuple(candidates[:SOURCE_LIMIT])
    if any(not isinstance(item, RankedCandidate) for item in limited):
        raise RetrievalProviderError("A retrieval source returned an invalid candidate type.")
    ids = tuple(item.object_id for item in limited)
    if len(ids) != len(set(ids)) or any(item_id not in object_by_id for item_id in ids):
        raise RetrievalProviderError("A retrieval source returned duplicate or unmapped IDs.")
    return limited


class RetrievalProvider:
    """Adapter over already validated, immutable retrieval runtime handles."""

    def __init__(
        self,
        package: ValidatedFrozenPackage,
        *,
        dense_search: SearchFunction,
        bm25_search: SearchFunction,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if not isinstance(package, ValidatedFrozenPackage):
            raise RetrievalProviderError("Retrieval requires a validated frozen package.")
        self._package = package
        self._dense_search = dense_search
        self._bm25_search = bm25_search
        self._clock = clock

    def retrieve(
        self,
        query: str,
        criterion: str | None = None,
        top_k: int = MAX_TOP_K,
    ) -> RetrievalEvidencePack:
        if not isinstance(query, str) or not query.strip():
            raise RetrievalProviderError("A non-empty retrieval query is required.")
        if isinstance(top_k, bool) or not isinstance(top_k, int) or not 1 <= top_k <= MAX_TOP_K:
            raise RetrievalProviderError("top_k must be between 1 and 5.")
        if criterion is not None and (not isinstance(criterion, str) or not criterion):
            raise RetrievalProviderError("The advisory criterion is invalid.")

        started = self._clock()
        dense = _validate_candidates(
            self._dense_search(query, SOURCE_LIMIT),
            object_by_id=self._package.object_by_id,
        )
        lexical = _validate_candidates(
            self._bm25_search(query, SOURCE_LIMIT),
            object_by_id=self._package.object_by_id,
        )
        dense_by_id = {item.object_id: (rank, item.score) for rank, item in enumerate(dense, start=1)}
        bm25_by_id = {item.object_id: (rank, item.score) for rank, item in enumerate(lexical, start=1)}
        combined: list[tuple[str, float, int]] = []
        for object_id in set(dense_by_id) | set(bm25_by_id):
            dense_rank = dense_by_id.get(object_id, (None, None))[0]
            bm25_rank = bm25_by_id.get(object_id, (None, None))[0]
            rrf_score = sum(
                1.0 / (RRF_K + rank)
                for rank in (dense_rank, bm25_rank)
                if rank is not None
            )
            combined.append((object_id, rrf_score, min(rank for rank in (dense_rank, bm25_rank) if rank is not None)))
        combined.sort(key=lambda value: (-value[1], value[2], value[0]))

        items: list[RetrievalEvidenceItem] = []
        for object_id, rrf_score, _ in combined[:top_k]:
            source = self._package.object_by_id[object_id]
            dense_value = dense_by_id.get(object_id)
            bm25_value = bm25_by_id.get(object_id)
            items.append(RetrievalEvidenceItem(
                object_id=source.object_id,
                object_type=source.object_type,
                criterion=source.criterion,
                features=source.features,
                text=source.text,
                provenance=source.provenance,
                source_artifact_id=source.source_artifact_id,
                rights=source.rights,
                usage=source.usage,
                dense_rank=dense_value[0] if dense_value else None,
                bm25_rank=bm25_value[0] if bm25_value else None,
                dense_retrieval_score=dense_value[1] if dense_value else None,
                bm25_retrieval_score=bm25_value[1] if bm25_value else None,
                rrf_score=rrf_score,
                direct_score_authority=False,
            ))
        elapsed_ms = max(0, int(round((self._clock() - started) * 1000)))
        provisional = RetrievalEvidencePack(
            items=tuple(items),
            query_sha256=digest({"query": query}),
            advisory_criterion=criterion,
            package_content_sha256=self._package.package_content_sha256,
            package_version=self._package.package_version,
            dense_candidate_count=len(dense),
            bm25_candidate_count=len(lexical),
            elapsed_ms=elapsed_ms,
            pack_sha256="",
        )
        return RetrievalEvidencePack(
            items=provisional.items,
            query_sha256=provisional.query_sha256,
            advisory_criterion=provisional.advisory_criterion,
            package_content_sha256=provisional.package_content_sha256,
            package_version=provisional.package_version,
            dense_candidate_count=provisional.dense_candidate_count,
            bm25_candidate_count=provisional.bm25_candidate_count,
            elapsed_ms=provisional.elapsed_ms,
            pack_sha256=digest(provisional.content(include_hash=False)),
        )
