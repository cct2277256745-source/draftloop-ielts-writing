"""Rights-first discovery and read-only validation for a frozen retrieval release.

The public product never guesses a package location.  Control documents are the
only package files that discovery may read; runtime artifacts require an
``AuthorizedFrozenPackage`` capability produced by the rights gate.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
import hashlib
import json
import os
from pathlib import Path
import re
from types import MappingProxyType
from typing import Any, Callable, Iterable, Mapping, Sequence

from .submission import digest


PACKAGE_ENVIRONMENT_VARIABLE = "EMPIRICAL_RAG_PACKAGE_PATH"
TARGET_ENVIRONMENT_VARIABLE = "IELTS_TARGET_ENVIRONMENT"
RAG_DISCOVERY_VERSION = "rag-package-discovery-v1"
RAG_RUNTIME_VALIDATION_VERSION = "rag-runtime-validation-v1"
PACKAGE_SCHEMA_VERSION = "frozen-retrieval-package-v1"
INTEGRATION_CONTRACT_VERSION = "retrieval-integration-v1"
BOUNDARY_CONTRACT_VERSION = "public-private-boundary-v1"
RELEASE_VALIDATION_VERSION = "frozen-release-validation-v1"
CONTROL_DOCUMENT_NAMES = (
    "retrieval_package_manifest.json",
    "INTEGRATION_CONTRACT.md",
    "PUBLIC_PRIVATE_BOUNDARY.md",
    "release_validation_report.json",
)
RUNTIME_ROLES = ("OBJECTS", "DENSE_INDEX", "DENSE_METADATA", "BM25")
DEFAULT_RAG_TARGET_POLICY_PATH = (
    Path(__file__).resolve().parents[1] / "resources" / "policies" / "c1_rag_target_policy.json"
)


class RagPackageError(ValueError):
    """A package control or runtime invariant failed closed."""


class DiscoveryStatus(str, Enum):
    READY_FOR_RUNTIME_VALIDATION = "READY_FOR_RUNTIME_VALIDATION"
    RAG_DISABLED = "RAG_DISABLED"


class RuntimeValidationStatus(str, Enum):
    VALIDATED = "VALIDATED"
    RAG_DISABLED = "RAG_DISABLED"


class RagDisabledReason(str, Enum):
    PACKAGE_NOT_CONFIGURED = "PACKAGE_NOT_CONFIGURED"
    TARGET_ENVIRONMENT_UNDECLARED = "TARGET_ENVIRONMENT_UNDECLARED"
    PACKAGE_PATH_UNSAFE = "PACKAGE_PATH_UNSAFE"
    CONTROL_DOCUMENT_MISSING = "CONTROL_DOCUMENT_MISSING"
    CONTROL_DOCUMENT_INVALID = "CONTROL_DOCUMENT_INVALID"
    PACKAGE_CONTRACT_MISMATCH = "PACKAGE_CONTRACT_MISMATCH"
    RIGHTS_UNAUTHORIZED = "RIGHTS_UNAUTHORIZED"
    TARGET_ENVIRONMENT_UNAUTHORIZED = "TARGET_ENVIRONMENT_UNAUTHORIZED"
    INTENDED_USE_UNAUTHORIZED = "INTENDED_USE_UNAUTHORIZED"
    COMMERCIAL_USE_UNAUTHORIZED = "COMMERCIAL_USE_UNAUTHORIZED"
    RELEASE_VALIDATION_FAILED = "RELEASE_VALIDATION_FAILED"
    RUNTIME_ARTIFACT_INVALID = "RUNTIME_ARTIFACT_INVALID"
    EMBEDDING_RUNTIME_INCOMPATIBLE = "EMBEDDING_RUNTIME_INCOMPATIBLE"
    DENSE_RUNTIME_UNAVAILABLE = "DENSE_RUNTIME_UNAVAILABLE"
    PACKAGE_MUTATION_DETECTED = "PACKAGE_MUTATION_DETECTED"


def _freeze(value: Any) -> Any:
    if isinstance(value, Mapping):
        return MappingProxyType({str(key): _freeze(child) for key, child in value.items()})
    if isinstance(value, (list, tuple)):
        return tuple(_freeze(child) for child in value)
    return value


def _thaw(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _thaw(child) for key, child in value.items()}
    if isinstance(value, tuple):
        return [_thaw(child) for child in value]
    return value


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _strict_json_bytes(raw: bytes, *, document: str) -> Mapping[str, Any]:
    try:
        value = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RagPackageError(f"{document} is not valid UTF-8 JSON.") from exc
    if not isinstance(value, dict):
        raise RagPackageError(f"{document} must contain a JSON object.")
    return value


def _markdown_control_json(raw: bytes, *, document: str) -> Mapping[str, Any]:
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise RagPackageError(f"{document} is not valid UTF-8.") from exc
    stripped = text.strip()
    if stripped.startswith("{"):
        return _strict_json_bytes(raw, document=document)
    blocks = re.findall(r"```json\s*(\{.*?\})\s*```", text, flags=re.DOTALL | re.IGNORECASE)
    if len(blocks) != 1:
        raise RagPackageError(f"{document} must contain exactly one JSON control block.")
    return _strict_json_bytes(blocks[0].encode("utf-8"), document=document)


def _string_list(value: Any, *, field_name: str) -> tuple[str, ...]:
    if not isinstance(value, list) or not value or any(not isinstance(item, str) or not item for item in value):
        raise RagPackageError(f"{field_name} must be a non-empty string list.")
    return tuple(value)


def _safe_existing_directory(configured: str) -> Path:
    candidate = Path(configured).expanduser()
    if not candidate.is_absolute():
        raise RagPackageError("The frozen package path must be absolute.")
    try:
        if candidate.is_symlink():
            raise RagPackageError("The configured frozen package path may not be a symlink.")
    except OSError as exc:
        raise RagPackageError("The frozen package path cannot be inspected safely.") from exc
    if not candidate.is_dir():
        raise RagPackageError("The frozen package directory does not exist.")
    return candidate.resolve(strict=True)


def _safe_child(root: Path, relative: str, *, require_file: bool = True) -> Path:
    path = Path(relative)
    if path.is_absolute() or not path.parts or any(part in ("", ".", "..") for part in path.parts):
        raise RagPackageError("A package artifact path is unsafe.")
    candidate = root.joinpath(*path.parts)
    current = root
    for component in path.parts:
        current = current / component
        if current.is_symlink():
            raise RagPackageError("A package artifact path may not contain symlinks.")
    resolved = candidate.resolve(strict=True)
    try:
        resolved.relative_to(root)
    except ValueError as exc:
        raise RagPackageError("A package artifact escaped the package root.") from exc
    if require_file and not resolved.is_file():
        raise RagPackageError("A package artifact is not a regular file.")
    return resolved


@dataclass(frozen=True)
class RagUsagePolicy:
    target_environment: str
    intended_use: str
    commercial: bool = False

    def __post_init__(self) -> None:
        if not self.target_environment or not self.intended_use:
            raise RagPackageError("RAG usage requires an explicit target environment and intended use.")


@dataclass(frozen=True)
class RagDiscoveryDecision:
    status: DiscoveryStatus
    reason: RagDisabledReason | None
    target_environment: str | None
    intended_use: str | None
    package_id: str | None
    package_version: str | None
    control_versions: Mapping[str, str] = field(repr=False)
    control_documents_read: tuple[str, ...] = ()
    decision_sha256: str = ""
    version: str = RAG_DISCOVERY_VERSION

    def __post_init__(self) -> None:
        object.__setattr__(self, "control_versions", _freeze(self.control_versions))

    def content(self, *, include_hash: bool = True) -> dict[str, Any]:
        value = {
            "version": self.version,
            "status": self.status.value,
            "reason": self.reason.value if self.reason else None,
            "targetEnvironment": self.target_environment,
            "intendedUse": self.intended_use,
            "packageId": self.package_id,
            "packageVersion": self.package_version,
            "controlVersions": dict(self.control_versions),
            "controlDocumentsRead": list(self.control_documents_read),
            "privatePathDisclosed": False,
            "privateHashesDisclosed": False,
        }
        if include_hash:
            value["decisionSha256"] = self.decision_sha256
        return value


@dataclass(frozen=True)
class AuthorizedFrozenPackage:
    """Non-serializable capability proving rights checks preceded runtime access."""

    root: Path = field(repr=False)
    manifest: Mapping[str, Any] = field(repr=False)
    manifest_bytes: bytes = field(repr=False)
    usage_policy: RagUsagePolicy = field(repr=False)
    discovery_decision_sha256: str
    authorization_scope: str = "DECLARED_TARGET_AUTHORIZED"

    def __post_init__(self) -> None:
        object.__setattr__(self, "manifest", _freeze(self.manifest))


@dataclass(frozen=True)
class DiscoveryOutcome:
    decision: RagDiscoveryDecision
    authorized_package: AuthorizedFrozenPackage | None


def _decision(
    status: DiscoveryStatus,
    *,
    reason: RagDisabledReason | None,
    policy: RagUsagePolicy | None,
    package_id: str | None = None,
    package_version: str | None = None,
    control_versions: Mapping[str, str] | None = None,
    documents_read: Sequence[str] = (),
) -> RagDiscoveryDecision:
    provisional = RagDiscoveryDecision(
        status=status,
        reason=reason,
        target_environment=policy.target_environment if policy else None,
        intended_use=policy.intended_use if policy else None,
        package_id=package_id,
        package_version=package_version,
        control_versions=control_versions or {},
        control_documents_read=tuple(documents_read),
    )
    return RagDiscoveryDecision(
        status=provisional.status,
        reason=provisional.reason,
        target_environment=provisional.target_environment,
        intended_use=provisional.intended_use,
        package_id=provisional.package_id,
        package_version=provisional.package_version,
        control_versions=provisional.control_versions,
        control_documents_read=provisional.control_documents_read,
        decision_sha256=digest(provisional.content(include_hash=False)),
    )


def discover_frozen_package(
    *,
    configured_path: str | None,
    usage_policy: RagUsagePolicy | None,
) -> DiscoveryOutcome:
    """Read controls, decide rights, and return the sole runtime-access capability."""
    if not configured_path:
        return DiscoveryOutcome(
            _decision(
                DiscoveryStatus.RAG_DISABLED,
                reason=RagDisabledReason.PACKAGE_NOT_CONFIGURED,
                policy=usage_policy,
            ),
            None,
        )
    if usage_policy is None:
        return DiscoveryOutcome(
            _decision(
                DiscoveryStatus.RAG_DISABLED,
                reason=RagDisabledReason.TARGET_ENVIRONMENT_UNDECLARED,
                policy=None,
            ),
            None,
        )
    try:
        root = _safe_existing_directory(configured_path)
    except RagPackageError:
        return DiscoveryOutcome(
            _decision(
                DiscoveryStatus.RAG_DISABLED,
                reason=RagDisabledReason.PACKAGE_PATH_UNSAFE,
                policy=usage_policy,
            ),
            None,
        )

    raw: dict[str, bytes] = {}
    documents_read: list[str] = []
    try:
        for name in CONTROL_DOCUMENT_NAMES:
            path = _safe_child(root, name)
            raw[name] = path.read_bytes()
            documents_read.append(name)
    except (OSError, RagPackageError):
        return DiscoveryOutcome(
            _decision(
                DiscoveryStatus.RAG_DISABLED,
                reason=RagDisabledReason.CONTROL_DOCUMENT_MISSING,
                policy=usage_policy,
                documents_read=documents_read,
            ),
            None,
        )

    try:
        manifest = _strict_json_bytes(raw[CONTROL_DOCUMENT_NAMES[0]], document=CONTROL_DOCUMENT_NAMES[0])
        if manifest.get("schema_version") == "frozen_retrieval_package.v1":
            from .rag_historical import discover_historical_controls
            return discover_historical_controls(root, raw, manifest, usage_policy)
        integration = _markdown_control_json(raw[CONTROL_DOCUMENT_NAMES[1]], document=CONTROL_DOCUMENT_NAMES[1])
        boundary = _markdown_control_json(raw[CONTROL_DOCUMENT_NAMES[2]], document=CONTROL_DOCUMENT_NAMES[2])
        report = _strict_json_bytes(raw[CONTROL_DOCUMENT_NAMES[3]], document=CONTROL_DOCUMENT_NAMES[3])
        package_id = str(manifest["packageId"])
        package_version = str(manifest["packageVersion"])
        if not package_id or not package_version or manifest.get("schemaVersion") != PACKAGE_SCHEMA_VERSION:
            raise RagPackageError("The manifest package identity is invalid.")
        if (
            integration.get("contractVersion") != INTEGRATION_CONTRACT_VERSION
            or integration.get("packageVersion") != package_version
            or integration.get("readOnly") is not True
        ):
            raise RagPackageError("The integration contract does not match the package.")
        if (
            boundary.get("boundaryVersion") != BOUNDARY_CONTRACT_VERSION
            or boundary.get("packageVersion") != package_version
        ):
            raise RagPackageError("The public/private boundary does not match the package.")
        if (
            report.get("validationVersion") != RELEASE_VALIDATION_VERSION
            or report.get("packageVersion") != package_version
            or report.get("manifestSha256") != _sha256_bytes(raw[CONTROL_DOCUMENT_NAMES[0]])
        ):
            raise RagPackageError("The release validation report does not match the manifest.")
    except (KeyError, TypeError, RagPackageError):
        return DiscoveryOutcome(
            _decision(
                DiscoveryStatus.RAG_DISABLED,
                reason=RagDisabledReason.CONTROL_DOCUMENT_INVALID,
                policy=usage_policy,
                documents_read=documents_read,
            ),
            None,
        )

    versions = {
        "manifest": str(manifest["schemaVersion"]),
        "integration": str(integration["contractVersion"]),
        "boundary": str(boundary["boundaryVersion"]),
        "releaseValidation": str(report["validationVersion"]),
    }
    common = {
        "policy": usage_policy,
        "package_id": package_id,
        "package_version": package_version,
        "control_versions": versions,
        "documents_read": documents_read,
    }
    if report.get("status") != "PASS":
        return DiscoveryOutcome(_decision(DiscoveryStatus.RAG_DISABLED, reason=RagDisabledReason.RELEASE_VALIDATION_FAILED, **common), None)
    if boundary.get("rightsStatus") != "AUTHORIZED":
        return DiscoveryOutcome(_decision(DiscoveryStatus.RAG_DISABLED, reason=RagDisabledReason.RIGHTS_UNAUTHORIZED, **common), None)
    try:
        environments = _string_list(boundary.get("allowedTargetEnvironments"), field_name="allowedTargetEnvironments")
        uses = _string_list(boundary.get("allowedUses"), field_name="allowedUses")
    except RagPackageError:
        return DiscoveryOutcome(_decision(DiscoveryStatus.RAG_DISABLED, reason=RagDisabledReason.RIGHTS_UNAUTHORIZED, **common), None)
    if usage_policy.target_environment not in environments:
        return DiscoveryOutcome(_decision(DiscoveryStatus.RAG_DISABLED, reason=RagDisabledReason.TARGET_ENVIRONMENT_UNAUTHORIZED, **common), None)
    if usage_policy.intended_use not in uses:
        return DiscoveryOutcome(_decision(DiscoveryStatus.RAG_DISABLED, reason=RagDisabledReason.INTENDED_USE_UNAUTHORIZED, **common), None)
    if usage_policy.commercial and boundary.get("commercialUseAuthorized") is not True:
        return DiscoveryOutcome(_decision(DiscoveryStatus.RAG_DISABLED, reason=RagDisabledReason.COMMERCIAL_USE_UNAUTHORIZED, **common), None)

    decision = _decision(DiscoveryStatus.READY_FOR_RUNTIME_VALIDATION, reason=None, **common)
    return DiscoveryOutcome(
        decision,
        AuthorizedFrozenPackage(
            root=root,
            manifest=manifest,
            manifest_bytes=raw[CONTROL_DOCUMENT_NAMES[0]],
            usage_policy=usage_policy,
            discovery_decision_sha256=decision.decision_sha256,
        ),
    )


def discover_from_environment(
    environ: Mapping[str, str] | None = None,
    *,
    intended_use: str = "assessment-calibration",
    commercial: bool = False,
) -> DiscoveryOutcome:
    values = os.environ if environ is None else environ
    target = values.get(TARGET_ENVIRONMENT_VARIABLE)
    policy = RagUsagePolicy(target, intended_use, commercial) if target else None
    return discover_frozen_package(
        configured_path=values.get(PACKAGE_ENVIRONMENT_VARIABLE),
        usage_policy=policy,
    )


def load_default_rag_usage_policy(
    path: Path = DEFAULT_RAG_TARGET_POLICY_PATH,
) -> RagUsagePolicy:
    """Load the tracked, public-safe target declaration (never a package path)."""
    try:
        value = _strict_json_bytes(path.read_bytes(), document=path.name)
    except OSError as exc:
        raise RagPackageError("The default RAG target policy is unavailable.") from exc
    if value.get("policyVersion") != "c1-rag-target-policy-v1" or value.get("defaultEnabled") is not False:
        raise RagPackageError("The default RAG target policy must remain fail-closed.")
    commercial = value.get("commercial")
    if not isinstance(commercial, bool):
        raise RagPackageError("The default RAG target commercial-use policy is invalid.")
    return RagUsagePolicy(
        target_environment=str(value.get("targetEnvironment", "")),
        intended_use=str(value.get("intendedUse", "")),
        commercial=commercial,
    )


def discover_for_default_product(
    environ: Mapping[str, str] | None = None,
) -> DiscoveryOutcome:
    """Discover only the explicitly configured package for the tracked target."""
    values = os.environ if environ is None else environ
    return discover_frozen_package(
        configured_path=values.get(PACKAGE_ENVIRONMENT_VARIABLE),
        usage_policy=load_default_rag_usage_policy(),
    )


@dataclass(frozen=True)
class EmbeddingRuntimePin:
    model_id: str
    revision: str
    dimension: int
    normalized: bool
    query_prompt: str

    def content(self) -> dict[str, Any]:
        return {
            "modelId": self.model_id,
            "revision": self.revision,
            "dimension": self.dimension,
            "normalized": self.normalized,
            "queryPrompt": self.query_prompt,
        }


@dataclass(frozen=True)
class DenseIndexShape:
    index_type: str
    dimension: int
    vector_count: int


@dataclass(frozen=True)
class FrozenEvidenceObject:
    object_id: str
    object_type: str
    criterion: str | None
    features: tuple[str, ...]
    text: str = field(repr=False)
    provenance: str = field(repr=False)
    source_artifact_id: str = field(repr=False)
    rights: str = field(repr=False)
    usage: str = field(repr=False)
    direct_score_authority: bool = False

    def __post_init__(self) -> None:
        if (
            not self.object_id
            or not self.object_type
            or not self.text
            or not self.provenance
            or not self.source_artifact_id
            or not self.rights
            or not self.usage
            or self.direct_score_authority is not False
        ):
            raise RagPackageError("A frozen evidence object violates the authority/linkage contract.")


@dataclass(frozen=True)
class ValidatedFrozenPackage:
    package_id: str
    package_version: str
    package_content_sha256: str
    discovery_decision_sha256: str
    object_by_id: Mapping[str, FrozenEvidenceObject] = field(repr=False)
    object_order: tuple[str, ...] = field(repr=False)
    embedding_pin: EmbeddingRuntimePin = field(repr=False)
    validation_sha256: str
    version: str = RAG_RUNTIME_VALIDATION_VERSION

    def __post_init__(self) -> None:
        object.__setattr__(self, "object_by_id", _freeze(self.object_by_id))


@dataclass(frozen=True)
class RuntimeValidationOutcome:
    status: RuntimeValidationStatus
    reason: RagDisabledReason | None
    validated_package: ValidatedFrozenPackage | None
    checked_artifact_roles: tuple[str, ...]
    mutation_free: bool
    runtime_handle: Any = field(default=None, repr=False, compare=False)


def _runtime_failure(reason: RagDisabledReason, roles: Iterable[str] = ()) -> RuntimeValidationOutcome:
    return RuntimeValidationOutcome(RuntimeValidationStatus.RAG_DISABLED, reason, None, tuple(roles), False)


def _file_identity(path: Path) -> tuple[int, str]:
    raw = path.read_bytes()
    return len(raw), _sha256_bytes(raw)


def _artifact_declarations(manifest: Mapping[str, Any]) -> Mapping[str, Mapping[str, Any]]:
    raw = manifest.get("runtimeArtifacts")
    if not isinstance(raw, (list, tuple)) or len(raw) != len(RUNTIME_ROLES):
        raise RagPackageError("The manifest runtime-artifact set is incomplete.")
    result: dict[str, Mapping[str, Any]] = {}
    for item in raw:
        if not isinstance(item, Mapping) or item.get("role") not in RUNTIME_ROLES:
            raise RagPackageError("A runtime-artifact declaration is invalid.")
        role = str(item["role"])
        if role in result:
            raise RagPackageError("A runtime-artifact role is duplicated.")
        if (
            not isinstance(item.get("path"), str)
            or not isinstance(item.get("sha256"), str)
            or not re.fullmatch(r"[0-9a-f]{64}", str(item.get("sha256")))
            or not isinstance(item.get("bytes"), int)
            or isinstance(item.get("bytes"), bool)
            or int(item.get("bytes")) < 0
        ):
            raise RagPackageError("A runtime-artifact declaration is incomplete.")
        result[role] = item
    if tuple(sorted(result)) != tuple(sorted(RUNTIME_ROLES)):
        raise RagPackageError("The runtime-artifact role set is invalid.")
    return MappingProxyType(result)


def _parse_object(raw: Any) -> FrozenEvidenceObject:
    if not isinstance(raw, dict) or "sourceBand" in raw or "similarityScore" in raw:
        raise RagPackageError("A frozen object contains prohibited score-transfer fields.")
    features = raw.get("features")
    if not isinstance(features, list) or any(not isinstance(item, str) or not item for item in features):
        raise RagPackageError("A frozen object feature list is invalid.")
    criterion = raw.get("criterion")
    if criterion is not None and not isinstance(criterion, str):
        raise RagPackageError("A frozen object criterion is invalid.")
    return FrozenEvidenceObject(
        object_id=str(raw.get("id", "")),
        object_type=str(raw.get("type", "")),
        criterion=criterion,
        features=tuple(features),
        text=str(raw.get("text", "")),
        provenance=str(raw.get("provenance", "")),
        source_artifact_id=str(raw.get("sourceArtifactId", "")),
        rights=str(raw.get("rights", "")),
        usage=str(raw.get("usage", "")),
        direct_score_authority=raw.get("directScoreAuthority"),
    )


def validate_frozen_runtime(
    authorized: AuthorizedFrozenPackage,
    *,
    runtime_embedding: EmbeddingRuntimePin,
    dense_inspector: Callable[[Path], DenseIndexShape] | None,
) -> RuntimeValidationOutcome:
    """Validate exact bytes and mappings without writing, repairing, or rebuilding."""
    if not isinstance(authorized, AuthorizedFrozenPackage):
        raise RagPackageError("Runtime validation requires an authorized package capability.")
    if authorized.manifest.get("schema_version") == "frozen_retrieval_package.v1":
        from .rag_historical import validate_historical_runtime
        return validate_historical_runtime(authorized, runtime_embedding=runtime_embedding)
    checked: list[str] = []
    try:
        declarations = _artifact_declarations(authorized.manifest)
        paths: dict[str, Path] = {}
        before: dict[str, tuple[int, str]] = {}
        for role in RUNTIME_ROLES:
            declaration = declarations[role]
            path = _safe_child(authorized.root, str(declaration["path"]))
            identity = _file_identity(path)
            if identity != (int(declaration["bytes"]), str(declaration["sha256"])):
                raise RagPackageError("A runtime artifact does not match its declared bytes.")
            paths[role] = path
            before[role] = identity
            checked.append(role)

        manifest_pin = authorized.manifest.get("embedding")
        if not isinstance(manifest_pin, Mapping) or manifest_pin != runtime_embedding.content():
            return _runtime_failure(RagDisabledReason.EMBEDDING_RUNTIME_INCOMPATIBLE, checked)
        if authorized.manifest.get("objectCount") != 729:
            raise RagPackageError("The frozen package must declare exactly 729 objects.")

        objects: list[FrozenEvidenceObject] = []
        with paths["OBJECTS"].open("r", encoding="utf-8", newline="") as handle:
            for line in handle:
                if not line.strip():
                    raise RagPackageError("The object JSONL contains an empty record.")
                try:
                    objects.append(_parse_object(json.loads(line)))
                except json.JSONDecodeError as exc:
                    raise RagPackageError("The object JSONL is malformed.") from exc
        if len(objects) != 729 or len({item.object_id for item in objects}) != 729:
            raise RagPackageError("The frozen object set must contain 729 unique IDs.")
        object_ids = tuple(item.object_id for item in objects)

        dense_metadata = _strict_json_bytes(paths["DENSE_METADATA"].read_bytes(), document="DENSE_METADATA")
        if dense_inspector is None:
            return _runtime_failure(RagDisabledReason.DENSE_RUNTIME_UNAVAILABLE, checked)
        dense_shape = dense_inspector(paths["DENSE_INDEX"])
        if (
            dense_shape.index_type != dense_metadata.get("indexType")
            or dense_shape.dimension != dense_metadata.get("dimension")
            or dense_shape.vector_count != dense_metadata.get("vectorCount")
            or dense_shape.dimension != runtime_embedding.dimension
            or dense_shape.vector_count != 729
            or tuple(dense_metadata.get("objectIds", ())) != object_ids
        ):
            raise RagPackageError("The dense index shape or object order is invalid.")

        bm25 = _strict_json_bytes(paths["BM25"].read_bytes(), document="BM25")
        if (
            bm25.get("schemaVersion") != "frozen-bm25-v1"
            or bm25.get("documentCount") != 729
            or tuple(bm25.get("documentIds", ())) != object_ids
        ):
            raise RagPackageError("The BM25 document mapping is invalid.")

        after = {role: _file_identity(path) for role, path in paths.items()}
        if after != before:
            return _runtime_failure(RagDisabledReason.PACKAGE_MUTATION_DETECTED, checked)
    except (OSError, TypeError, ValueError):
        return _runtime_failure(RagDisabledReason.RUNTIME_ARTIFACT_INVALID, checked)

    package_content_sha = digest({
        "manifestSha256": _sha256_bytes(authorized.manifest_bytes),
        "runtimeArtifacts": {role: {"bytes": size, "sha256": sha} for role, (size, sha) in before.items()},
    })
    provisional = {
        "version": RAG_RUNTIME_VALIDATION_VERSION,
        "packageId": str(authorized.manifest["packageId"]),
        "packageVersion": str(authorized.manifest["packageVersion"]),
        "packageContentSha256": package_content_sha,
        "discoveryDecisionSha256": authorized.discovery_decision_sha256,
        "objectCount": len(objects),
        "objectOrderSha256": digest(list(object_ids)),
        "embeddingPin": runtime_embedding.content(),
        "mutationFree": True,
    }
    validated = ValidatedFrozenPackage(
        package_id=provisional["packageId"],
        package_version=provisional["packageVersion"],
        package_content_sha256=package_content_sha,
        discovery_decision_sha256=authorized.discovery_decision_sha256,
        object_by_id={item.object_id: item for item in objects},
        object_order=object_ids,
        embedding_pin=runtime_embedding,
        validation_sha256=digest(provisional),
    )
    return RuntimeValidationOutcome(
        RuntimeValidationStatus.VALIDATED,
        None,
        validated,
        tuple(checked),
        True,
    )
