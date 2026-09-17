"""Provenance-aware coaching knowledge, topic packs, and minimal edits."""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import re
from typing import Any, Iterable, Sequence

from .revision import AssistanceDepth
from .submission import EssayVersion, digest


TOPIC_KB_VERSION = "topic-language-kb-v1"
TOPIC_PACK_VERSION = "topic-learning-pack-v1"
MINIMAL_EDIT_VERSION = "minimal-edit-v1"


class TopicKnowledgeError(ValueError):
    """Knowledge provenance, isolation, or minimal-edit constraints failed."""


class KnowledgeKind(str, Enum):
    VOCABULARY = "VOCABULARY"
    COLLOCATION = "COLLOCATION"
    FRAME = "FRAME"
    ARGUMENT = "ARGUMENT"
    COUNTERARGUMENT = "COUNTERARGUMENT"
    EXAMPLE = "EXAMPLE"
    MISUSE = "MISUSE"
    ALTERNATIVE = "ALTERNATIVE"


class KnowledgeSource(str, Enum):
    STUDENT_OWNED = "STUDENT_OWNED"
    STUDENT_IMPROVED = "STUDENT_IMPROVED"
    INTERNAL_TOPIC = "INTERNAL_TOPIC"
    TEACHER_RECOMMENDED = "TEACHER_RECOMMENDED"
    INSTITUTION_RECOMMENDED = "INSTITUTION_RECOMMENDED"


class KnowledgeRights(str, Enum):
    OWNER_ONLY = "OWNER_ONLY"
    TENANT_COACHING = "TENANT_COACHING"
    PUBLIC_SAFE = "PUBLIC_SAFE"
    FORBIDDEN = "FORBIDDEN"


class Naturalness(str, Enum):
    NATURAL = "NATURAL"
    CAUTION = "CAUTION"
    UNNATURAL = "UNNATURAL"


def _normalise(value: str) -> str:
    return " ".join(re.findall(r"[^\W_]+", value.casefold(), re.UNICODE))


_MALICIOUS_MARKERS = (
    "ignore previous",
    "ignore all instructions",
    "system prompt",
    "direct score authority",
    "override rubric",
)


@dataclass(frozen=True)
class KnowledgeAsset:
    asset_id: str
    kind: KnowledgeKind
    source: KnowledgeSource
    rights: KnowledgeRights
    owner_id: str
    tenant_id: str
    text: str
    topic_tags: tuple[str, ...]
    naturalness: Naturalness
    misuse_warning: str | None
    source_artifact_id: str
    version: int
    semantic_key: str
    asset_sha256: str
    schema_version: str = TOPIC_KB_VERSION

    @classmethod
    def create(
        cls,
        *,
        kind: KnowledgeKind,
        source: KnowledgeSource,
        rights: KnowledgeRights,
        owner_id: str,
        tenant_id: str,
        text: str,
        topic_tags: Sequence[str],
        naturalness: Naturalness,
        source_artifact_id: str,
        misuse_warning: str | None = None,
        version: int = 1,
    ) -> "KnowledgeAsset":
        clean_text = " ".join(text.split())
        tags = tuple(sorted({_normalise(tag) for tag in topic_tags if _normalise(tag)}))
        if (
            not isinstance(kind, KnowledgeKind)
            or not isinstance(source, KnowledgeSource)
            or not isinstance(rights, KnowledgeRights)
            or not isinstance(naturalness, Naturalness)
            or not owner_id
            or not tenant_id
            or not clean_text
            or not tags
            or not source_artifact_id
            or isinstance(version, bool)
            or not isinstance(version, int)
            or version < 1
        ):
            raise TopicKnowledgeError("Knowledge assets require complete provenance and ownership.")
        if rights is KnowledgeRights.FORBIDDEN:
            raise TopicKnowledgeError("Forbidden-rights content cannot enter the coaching KB.")
        if source in {KnowledgeSource.STUDENT_OWNED, KnowledgeSource.STUDENT_IMPROVED} and rights is not KnowledgeRights.OWNER_ONLY:
            raise TopicKnowledgeError("Student knowledge cannot be promoted beyond owner-only rights automatically.")
        if source in {KnowledgeSource.TEACHER_RECOMMENDED, KnowledgeSource.INSTITUTION_RECOMMENDED} and rights is KnowledgeRights.PUBLIC_SAFE:
            raise TopicKnowledgeError("Teacher or institutional knowledge requires a separate public-rights decision.")
        if any(marker in clean_text.casefold() for marker in _MALICIOUS_MARKERS):
            raise TopicKnowledgeError("Knowledge asset contains an unsafe instruction marker.")
        if kind is KnowledgeKind.MISUSE and not misuse_warning:
            raise TopicKnowledgeError("Misuse assets require an explicit warning.")
        semantic_key = f"{kind.value}:{_normalise(clean_text)}"
        identity = {
            "schemaVersion": TOPIC_KB_VERSION,
            "kind": kind.value,
            "source": source.value,
            "rights": rights.value,
            "ownerId": owner_id,
            "tenantId": tenant_id,
            "text": clean_text,
            "topicTags": list(tags),
            "naturalness": naturalness.value,
            "misuseWarning": misuse_warning,
            "sourceArtifactId": source_artifact_id,
            "version": version,
        }
        asset_sha = digest(identity)
        return cls(
            "knowledge-asset:" + asset_sha,
            kind,
            source,
            rights,
            owner_id,
            tenant_id,
            clean_text,
            tags,
            naturalness,
            misuse_warning,
            source_artifact_id,
            version,
            semantic_key,
            asset_sha,
        )

    def content(self) -> dict[str, Any]:
        return {
            "schemaVersion": self.schema_version,
            "assetId": self.asset_id,
            "kind": self.kind.value,
            "source": self.source.value,
            "rights": self.rights.value,
            "ownerId": self.owner_id,
            "tenantId": self.tenant_id,
            "text": self.text,
            "topicTags": list(self.topic_tags),
            "naturalness": self.naturalness.value,
            "misuseWarning": self.misuse_warning,
            "sourceArtifactId": self.source_artifact_id,
            "version": self.version,
            "semanticKey": self.semantic_key,
            "assetSha256": self.asset_sha256,
            "directScoreAuthority": False,
        }

    def allowed_for(self, tenant_id: str, owner_id: str) -> bool:
        if self.rights is KnowledgeRights.PUBLIC_SAFE:
            return self.tenant_id in {tenant_id, "public"}
        if self.tenant_id != tenant_id:
            return False
        if self.rights is KnowledgeRights.OWNER_ONLY:
            return self.owner_id == owner_id
        return self.rights is KnowledgeRights.TENANT_COACHING


class TopicKnowledgeRepository:
    """An isolated repository boundary; persistence is intentionally out of C2."""

    def __init__(self, assets: Iterable[KnowledgeAsset] = ()) -> None:
        self._assets: dict[str, KnowledgeAsset] = {}
        self._semantic_by_tenant: dict[tuple[str, str], str] = {}
        for asset in assets:
            self.add(asset)

    def add(self, asset: KnowledgeAsset) -> None:
        if not isinstance(asset, KnowledgeAsset) or digest({
            key: value for key, value in asset.content().items()
            if key not in {"assetId", "semanticKey", "assetSha256", "directScoreAuthority"}
        }) != asset.asset_sha256:
            raise TopicKnowledgeError("Knowledge asset hash is invalid.")
        marker = (asset.tenant_id, asset.semantic_key)
        if asset.asset_id in self._assets or marker in self._semantic_by_tenant:
            raise TopicKnowledgeError("Duplicate knowledge asset is not allowed.")
        self._assets[asset.asset_id] = asset
        self._semantic_by_tenant[marker] = asset.asset_id

    def get(self, asset_id: str, *, tenant_id: str, owner_id: str) -> KnowledgeAsset:
        asset = self._assets.get(asset_id)
        if asset is None or not asset.allowed_for(tenant_id, owner_id):
            raise TopicKnowledgeError("Knowledge asset is unavailable in this tenant boundary.")
        return asset

    def search(self, *, tenant_id: str, owner_id: str, topic_tags: Sequence[str]) -> tuple[KnowledgeAsset, ...]:
        wanted = {_normalise(tag) for tag in topic_tags if _normalise(tag)}
        return tuple(sorted(
            (
                asset for asset in self._assets.values()
                if asset.allowed_for(tenant_id, owner_id)
                and wanted.intersection(asset.topic_tags)
            ),
            key=lambda asset: asset.asset_id,
        ))

    def delete(self, asset_id: str, *, tenant_id: str, owner_id: str) -> None:
        asset = self.get(asset_id, tenant_id=tenant_id, owner_id=owner_id)
        if asset.owner_id != owner_id:
            raise TopicKnowledgeError("Only the owner can delete a knowledge asset.")
        del self._assets[asset_id]
        del self._semantic_by_tenant[(asset.tenant_id, asset.semantic_key)]


@dataclass(frozen=True)
class TopicNeed:
    need_id: str
    criterion: str
    topic_tags: tuple[str, ...]
    evidence_ids: tuple[str, ...]
    priority: int

    @classmethod
    def create(
        cls,
        criterion: str,
        topic_tags: Sequence[str],
        evidence_ids: Sequence[str],
        *,
        priority: int = 0,
    ) -> "TopicNeed":
        tags = tuple(sorted({_normalise(value) for value in topic_tags if _normalise(value)}))
        evidence = tuple(sorted({str(value) for value in evidence_ids if str(value)}))
        if not criterion or not tags or not evidence or isinstance(priority, bool) or priority < 0:
            raise TopicKnowledgeError("Topic needs require current evidence, tags, and a valid priority.")
        payload = {"criterion": criterion, "topicTags": list(tags), "evidenceIds": list(evidence)}
        return cls("topic-need:" + digest(payload), criterion, tags, evidence, priority)


class TopicPackStatus(str, Enum):
    COMPLETE = "COMPLETE"
    EMPTY = "EMPTY"


@dataclass(frozen=True)
class TopicPackItem:
    asset: KnowledgeAsset
    need_id: str
    evidence_ids: tuple[str, ...]

    def content(self) -> dict[str, Any]:
        return {
            "asset": self.asset.content(),
            "needId": self.need_id,
            "evidenceIds": list(self.evidence_ids),
        }


@dataclass(frozen=True)
class TopicLearningPack:
    tenant_id: str
    owner_id: str
    status: TopicPackStatus
    items: tuple[TopicPackItem, ...]
    pack_sha256: str
    version: str = TOPIC_PACK_VERSION

    def content(self, *, include_hash: bool = True) -> dict[str, Any]:
        value = {
            "version": self.version,
            "tenantId": self.tenant_id,
            "ownerId": self.owner_id,
            "status": self.status.value,
            "items": [item.content() for item in self.items],
            "directScoreAuthority": False,
        }
        if include_hash:
            value["packSha256"] = self.pack_sha256
        return value


_SOURCE_PRIORITY = {
    KnowledgeSource.STUDENT_OWNED: 0,
    KnowledgeSource.STUDENT_IMPROVED: 1,
    KnowledgeSource.TEACHER_RECOMMENDED: 2,
    KnowledgeSource.INSTITUTION_RECOMMENDED: 3,
    KnowledgeSource.INTERNAL_TOPIC: 4,
}


def assemble_topic_pack(
    repository: TopicKnowledgeRepository,
    *,
    tenant_id: str,
    owner_id: str,
    needs: Sequence[TopicNeed],
    max_items: int = 6,
) -> TopicLearningPack:
    if not tenant_id or not owner_id or isinstance(max_items, bool) or not 0 <= max_items <= 12:
        raise TopicKnowledgeError("Topic pack boundary is invalid.")
    selected: list[TopicPackItem] = []
    seen_assets: set[str] = set()
    for need in sorted(needs, key=lambda item: (-item.priority, item.need_id)):
        candidates = repository.search(
            tenant_id=tenant_id,
            owner_id=owner_id,
            topic_tags=need.topic_tags,
        )
        candidates = tuple(sorted(candidates, key=lambda asset: (
            _SOURCE_PRIORITY[asset.source], asset.asset_id
        )))
        for asset in candidates:
            if asset.asset_id in seen_assets:
                continue
            if asset.naturalness is Naturalness.UNNATURAL and asset.kind is not KnowledgeKind.MISUSE:
                continue
            seen_assets.add(asset.asset_id)
            selected.append(TopicPackItem(asset, need.need_id, need.evidence_ids))
            if len(selected) >= max_items:
                break
        if len(selected) >= max_items:
            break
    status = TopicPackStatus.COMPLETE if selected else TopicPackStatus.EMPTY
    partial = TopicLearningPack(tenant_id, owner_id, status, tuple(selected), "")
    return TopicLearningPack(**{
        **partial.__dict__,
        "pack_sha256": digest(partial.content(include_hash=False)),
    })


class EditOperation(str, Enum):
    KEEP = "KEEP"
    FIX = "FIX"
    DEVELOP = "DEVELOP"
    DIVERSIFY = "DIVERSIFY"
    REMOVE = "REMOVE"
    RESTRUCTURE = "RESTRUCTURE"


@dataclass(frozen=True)
class MinimalEditDraft:
    operation: EditOperation
    start: int
    end: int
    original_text: str
    replacement_text: str
    rationale: str
    evidence_ids: tuple[str, ...]
    source_asset_ids: tuple[str, ...] = ()
    fact_preserved: bool = True
    position_preserved: bool = True
    style_preserved: bool = True
    naturalness: Naturalness = Naturalness.NATURAL
    assistance_depth: AssistanceDepth = AssistanceDepth.AI_REWRITE


@dataclass(frozen=True)
class MinimalEdit:
    edit_id: str
    operation: EditOperation
    essay_version_id: str
    start: int
    end: int
    original_text_sha256: str
    replacement_text: str
    rationale: str
    evidence_ids: tuple[str, ...]
    source_asset_ids: tuple[str, ...]
    assistance_depth: AssistanceDepth

    def content(self) -> dict[str, Any]:
        return {
            "editId": self.edit_id,
            "operation": self.operation.value,
            "essayVersionId": self.essay_version_id,
            "start": self.start,
            "end": self.end,
            "originalTextSha256": self.original_text_sha256,
            "replacementText": self.replacement_text,
            "rationale": self.rationale,
            "evidenceIds": list(self.evidence_ids),
            "sourceAssetIds": list(self.source_asset_ids),
            "assistanceDepth": self.assistance_depth.value,
            "directScoreAuthority": False,
        }


@dataclass(frozen=True)
class MinimalEditPlan:
    essay_version_id: str
    edits: tuple[MinimalEdit, ...]
    changed_character_count: int
    original_character_count: int
    plan_sha256: str
    version: str = MINIMAL_EDIT_VERSION

    def content(self, *, include_hash: bool = True) -> dict[str, Any]:
        value = {
            "version": self.version,
            "essayVersionId": self.essay_version_id,
            "edits": [edit.content() for edit in self.edits],
            "changedCharacterCount": self.changed_character_count,
            "originalCharacterCount": self.original_character_count,
            "preserveBeforeUpgrade": True,
        }
        if include_hash:
            value["planSha256"] = self.plan_sha256
        return value


_NUMBER_RE = re.compile(r"(?<!\w)\d+(?:[.,]\d+)*(?:%|\b)")
_POSITION_MARKERS = frozenset({"not", "no", "never", "disagree", "oppose", "must", "should"})


def _words(value: str) -> set[str]:
    return set(re.findall(r"[^\W_]+", value.casefold(), re.UNICODE))


def build_minimal_edit_plan(
    essay: EssayVersion,
    drafts: Sequence[MinimalEditDraft],
    repository: TopicKnowledgeRepository,
    *,
    tenant_id: str,
    owner_id: str,
    max_changed_fraction: float = 0.35,
) -> MinimalEditPlan:
    if not 0.0 <= max_changed_fraction <= 0.5:
        raise TopicKnowledgeError("Minimal-edit budget is invalid.")
    edits: list[MinimalEdit] = []
    occupied: list[tuple[int, int]] = []
    changed = 0
    for draft in sorted(drafts, key=lambda item: (item.start, item.end, item.operation.value)):
        if (
            draft.start < 0
            or draft.end <= draft.start
            or draft.end > len(essay.original_text)
            or essay.original_text[draft.start:draft.end] != draft.original_text
            or not draft.rationale.strip()
            or not draft.evidence_ids
        ):
            raise TopicKnowledgeError("Minimal edit locator, rationale, or evidence is invalid.")
        if any(draft.start < end and start < draft.end for start, end in occupied):
            raise TopicKnowledgeError("Minimal edits cannot overlap.")
        if not all((draft.fact_preserved, draft.position_preserved, draft.style_preserved)):
            raise TopicKnowledgeError("Fact, position, and style preservation must pass.")
        if draft.naturalness is Naturalness.UNNATURAL:
            raise TopicKnowledgeError("Unnatural upgrades are rejected.")
        if draft.operation is EditOperation.KEEP and draft.replacement_text != draft.original_text:
            raise TopicKnowledgeError("KEEP cannot rewrite student language.")
        if draft.operation is EditOperation.REMOVE and draft.replacement_text:
            raise TopicKnowledgeError("REMOVE must have an empty replacement.")
        if draft.operation not in {EditOperation.KEEP, EditOperation.REMOVE} and not draft.replacement_text.strip():
            raise TopicKnowledgeError("This edit operation requires a replacement.")
        original_numbers = _NUMBER_RE.findall(draft.original_text)
        replacement_numbers = _NUMBER_RE.findall(draft.replacement_text)
        if original_numbers != replacement_numbers:
            raise TopicKnowledgeError("Minimal editing cannot change Task facts or numbers.")
        original_positions = _words(draft.original_text).intersection(_POSITION_MARKERS)
        replacement_positions = _words(draft.replacement_text).intersection(_POSITION_MARKERS)
        if original_positions != replacement_positions:
            raise TopicKnowledgeError("Minimal editing cannot reverse or add a position marker.")
        source_ids = tuple(sorted(set(draft.source_asset_ids)))
        for asset_id in source_ids:
            repository.get(asset_id, tenant_id=tenant_id, owner_id=owner_id)
        if len(draft.replacement_text) > max(40, len(draft.original_text) * 2):
            raise TopicKnowledgeError("Minimal edit replacement is excessively expansive.")
        evidence = tuple(sorted(set(draft.evidence_ids)))
        identity = {
            "version": MINIMAL_EDIT_VERSION,
            "operation": draft.operation.value,
            "essayVersionId": essay.essay_version_id,
            "start": draft.start,
            "end": draft.end,
            "originalTextSha256": digest(draft.original_text),
            "replacementText": draft.replacement_text,
            "rationale": " ".join(draft.rationale.split()),
            "evidenceIds": list(evidence),
            "sourceAssetIds": list(source_ids),
            "assistanceDepth": draft.assistance_depth.value,
        }
        edits.append(MinimalEdit(
            "minimal-edit:" + digest(identity),
            draft.operation,
            essay.essay_version_id,
            draft.start,
            draft.end,
            digest(draft.original_text),
            draft.replacement_text,
            identity["rationale"],
            evidence,
            source_ids,
            draft.assistance_depth,
        ))
        occupied.append((draft.start, draft.end))
        if draft.operation is not EditOperation.KEEP:
            changed += max(len(draft.original_text), len(draft.replacement_text))
    if changed / max(1, len(essay.original_text)) > max_changed_fraction:
        raise TopicKnowledgeError("Minimal edits exceed the whole-script change budget.")
    partial = MinimalEditPlan(essay.essay_version_id, tuple(edits), changed, len(essay.original_text), "")
    return MinimalEditPlan(**{
        **partial.__dict__,
        "plan_sha256": digest(partial.content(include_hash=False)),
    })
