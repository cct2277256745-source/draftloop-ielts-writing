"""Read-only, pinned authority runtime for Academic Writing rubrics."""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import Enum
import hashlib
import json
import os
from pathlib import Path
from typing import Any, Iterator

from jsonschema import Draft202012Validator, FormatChecker


CRITERIA = ("TR", "CC", "LR", "GRA")
WHOLE_BANDS = tuple(range(10))
EXPECTED_RUNTIME_HASH = "3e0a7322fbd4f8ebe90b88a6ec6183f12c02ec7189f1e32fff76d69f3e7bc9a2"
EXPECTED_SOURCE_HASHES = {
    "IELTS_WBD_2023_TASK2": "e3c88943ef92d98988ce4db454fd7fa8d8435f0b25e9ec667e3720a5c1168d1b",
    "IELTS_WKAC_TASK2": "5e46074fab056620861710ba77fe6e8d183a9403a70b5228e00b96ef7e94422e",
}
EXPECTED_DEFINITION_COORDINATES = {
    "TR": {"source_id": "IELTS_WKAC_TASK2", "pdf_pages": [2, 3]},
    "CC": {"source_id": "IELTS_WKAC_TASK2", "pdf_page": 3},
    "LR": {"source_id": "IELTS_WKAC_TASK2", "pdf_page": 4},
    "GRA": {"source_id": "IELTS_WKAC_TASK2", "pdf_page": 4},
}
EXPECTED_ASSET_KEYS = frozenset({
    "task2/current.json", "task2/v1.0.0/manifest.json",
    "task2/v1.0.0/TR.json", "task2/v1.0.0/CC.json",
    "task2/v1.0.0/LR.json", "task2/v1.0.0/GRA.json",
    "schema/task2-current-v1.schema.json",
    "schema/task2-manifest-v1.schema.json",
    "schema/task2-criterion-v1.schema.json",
})
DEFAULT_PIN = Path(__file__).resolve().parents[1] / "resources" / "rubric_pins" / "task2-v1.0.0.json"
DEFAULT_ROOT = Path(__file__).resolve().parents[2] / "rubrics" / "task2"
TASK1_CRITERIA = ("TA", "CC", "LR", "GRA")
TASK1_EXPECTED_RUNTIME_HASH = "8ff84b27fa0c1604255da5a513bddeef39f5e8f6903736b4dc78482fab899313"
TASK1_EXPECTED_SOURCE_HASHES = {
    "IELTS_WBD_2023_TASK1": "e3c88943ef92d98988ce4db454fd7fa8d8435f0b25e9ec667e3720a5c1168d1b",
    "IELTS_WKAC_TASK1": "5e46074fab056620861710ba77fe6e8d183a9403a70b5228e00b96ef7e94422e",
}
TASK1_EXPECTED_DEFINITION_COORDINATES = {
    "TA": {"source_id": "IELTS_WKAC_TASK1", "pdf_pages": [1, 2]},
    "CC": {"source_id": "IELTS_WKAC_TASK1", "pdf_page": 3},
    "LR": {"source_id": "IELTS_WKAC_TASK1", "pdf_page": 4},
    "GRA": {"source_id": "IELTS_WKAC_TASK1", "pdf_page": 4},
}
TASK1_EXPECTED_ASSET_KEYS = frozenset({
    "task1/current.json", "task1/v1.0.0/manifest.json",
    "task1/v1.0.0/TA.json", "task1/v1.0.0/CC.json",
    "task1/v1.0.0/LR.json", "task1/v1.0.0/GRA.json",
    "schema/task1-current-v1.schema.json",
    "schema/task1-manifest-v1.schema.json",
    "schema/task1-criterion-v1.schema.json",
})
TASK1_DEFAULT_PIN = (
    Path(__file__).resolve().parents[1]
    / "resources" / "rubric_pins" / "task1-v1.0.0.json"
)
TASK1_DEFAULT_ROOT = Path(__file__).resolve().parents[2] / "rubrics" / "task1"
FORBIDDEN_AUTHORITY_KEYS = frozenset({
    "research_feature_profiles", "research_data", "teacher_data", "student_data",
    "student_target_band", "target_band", "targetband", "synthetic_samples",
    "synthetic_data", "empirical_data", "sample_scripts", "model_essays",
    "model_essay_language", "corpus_references", "topic_vocabulary",
    "chart_facts", "chart_data", "vision_extraction", "vision_output",
    "image_model_knowledge", "image_model_output", "extracted_chart_facts",
    "unapproved_overview_rule", "unapproved_data_rule",
})


class RubricErrorCode(str, Enum):
    MISSING_FILE = "MISSING_FILE"
    INVALID_JSON = "INVALID_JSON"
    INVALID_SCHEMA = "INVALID_SCHEMA"
    PATH_ESCAPE = "PATH_ESCAPE"
    IDENTITY_MISMATCH = "IDENTITY_MISMATCH"
    INTEGRITY_MISMATCH = "INTEGRITY_MISMATCH"
    PROVENANCE_INVALID = "PROVENANCE_INVALID"
    PROVENANCE_MISMATCH = "PROVENANCE_MISMATCH"
    STATUS_INVALID = "STATUS_INVALID"
    COVERAGE_INVALID = "COVERAGE_INVALID"
    HALF_BAND_FORBIDDEN = "HALF_BAND_FORBIDDEN"
    AUTHORITY_CONTAMINATION = "AUTHORITY_CONTAMINATION"
    PROJECTION_DRIFT = "PROJECTION_DRIFT"


class RubricLoadError(Exception):
    """Log-safe rubric failure: no paths, descriptors, or local filenames."""

    def __init__(self, code: RubricErrorCode, component: str = "package") -> None:
        self.code = code
        self.component = component
        super().__init__(f"Rubric package rejected: {code.value} ({component}).")


class FrozenMapping(Mapping[str, Any]):
    """A recursively immutable JSON object."""

    __slots__ = ("_items",)

    def __init__(self, value: Mapping[str, Any]) -> None:
        object.__setattr__(
            self,
            "_items",
            tuple((str(key), _freeze(child)) for key, child in value.items()),
        )

    def __setattr__(self, name: str, value: Any) -> None:
        raise AttributeError("FrozenMapping is immutable")

    def __getitem__(self, key: str) -> Any:
        for current, value in self._items:
            if current == key:
                return value
        raise KeyError(key)

    def __iter__(self) -> Iterator[str]:
        return (key for key, _ in self._items)

    def __len__(self) -> int:
        return len(self._items)

    def __repr__(self) -> str:
        return f"FrozenMapping(keys={tuple(self)!r})"


def _freeze(value: Any) -> Any:
    if isinstance(value, Mapping):
        return FrozenMapping(value)
    if isinstance(value, list):
        return tuple(_freeze(item) for item in value)
    return value


def _thaw(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {key: _thaw(child) for key, child in value.items()}
    if isinstance(value, tuple):
        return [_thaw(item) for item in value]
    return value


@dataclass(frozen=True)
class SourceProvenance:
    source_id: str
    publisher: str
    classification: str
    usage_classification: str
    source_pdf_sha256: str = field(repr=False)


@dataclass(frozen=True)
class OfficialClaim:
    claim_id: str
    text: str = field(repr=False)


@dataclass(frozen=True)
class SourceCoordinate:
    source_id: str
    pdf_page: int
    band: int
    criterion: str


@dataclass(frozen=True)
class BandAnchor:
    band: int
    source: SourceCoordinate
    official_claims: tuple[OfficialClaim, ...]
    derived_internal: FrozenMapping = field(repr=False)


@dataclass(frozen=True)
class CriterionRubric:
    code: str
    name: str
    official_definition: str = field(repr=False)
    assessment_dimensions: tuple[str, ...] = field(repr=False)
    anchors: tuple[BandAnchor, ...] = field(repr=False)


@dataclass(frozen=True)
class RubricPin:
    rubric_id: str
    version: str
    schema_version: str
    task_type: str
    module: str
    runtime_content_sha256: str
    runtime_review_gate: str
    hash_disposition: FrozenMapping = field(repr=False)
    commercial_distribution_status: str
    public_repo_status: str
    asset_file_sha256: FrozenMapping = field(repr=False)
    approved_provenance: tuple[SourceProvenance, ...] = field(repr=False)


@dataclass(frozen=True)
class StructuredRubricSnapshot:
    rubric_id: str
    version: str
    schema_version: str
    task_type: str
    module: str
    runtime_content_sha256: str
    runtime_review_gate: str
    commercial_distribution_status: str
    public_repo_status: str
    approved_provenance: tuple[SourceProvenance, ...] = field(repr=False)
    criteria: tuple[CriterionRubric, ...] = field(repr=False)

    def identity(self) -> dict[str, str]:
        return {
            "rubric_id": self.rubric_id,
            "version": self.version,
            "runtime_content_sha256": self.runtime_content_sha256,
        }
    def to_prompt_payload(self) -> dict[str, Any]:
        official: list[dict[str, Any]] = []
        derived: list[dict[str, Any]] = []
        for criterion in self.criteria:
            official_bands = []
            derived_bands = []
            for anchor in criterion.anchors:
                official_bands.append({
                    "band": anchor.band,
                    "sourceReference": {
                        "sourceId": anchor.source.source_id,
                        "pdfPage": anchor.source.pdf_page,
                        "criterion": anchor.source.criterion,
                    },
                    "officialClaims": [
                        {"id": claim.claim_id, "text": claim.text}
                        for claim in anchor.official_claims
                    ],
                })
                derived_bands.append({
                    "band": anchor.band,
                    "interpretationStatus": "DERIVED_INTERNAL",
                    "structuredInterpretation": _thaw(anchor.derived_internal),
                })
            official.append({
                "criterion": criterion.code,
                "name": criterion.name,
                "officialDefinition": criterion.official_definition,
                "assessmentDimensions": list(criterion.assessment_dimensions),
                "bands": official_bands,
            })
            derived.append({"criterion": criterion.code, "bands": derived_bands})
        return {
            "rubricIdentity": self.identity(),
            "officialRubric": {"authority": "OFFICIAL_RUBRIC", "criteria": official},
            "derivedInternal": {
                "authority": "DERIVED_INTERNAL",
                "mayAddScoringRequirements": False,
                "criteria": derived,
            },
        }


def is_approved_task2_snapshot(snapshot: Any) -> bool:
    """Validate a consumer-supplied snapshot's complete public identity contract."""
    if not isinstance(snapshot, StructuredRubricSnapshot):
        return False
    if (
        snapshot.rubric_id != "ielts_academic_writing_task2"
        or snapshot.version != "1.0.0"
        or snapshot.schema_version != "1.0.0"
        or snapshot.task_type != "task2"
        or snapshot.module != "academic"
        or snapshot.runtime_content_sha256 != EXPECTED_RUNTIME_HASH
        or snapshot.runtime_review_gate != "RUBRIC_V1_READY"
        or snapshot.commercial_distribution_status != "NOT_ASSESSED"
        or snapshot.public_repo_status != "DO_NOT_PUBLISH_DESCRIPTOR_TEXT_WITHOUT_RIGHTS_REVIEW"
        or tuple(criterion.code for criterion in snapshot.criteria) != CRITERIA
        or any(
            tuple(anchor.band for anchor in criterion.anchors) != WHOLE_BANDS
            for criterion in snapshot.criteria
        )
    ):
        return False
    sources = {source.source_id: source for source in snapshot.approved_provenance}
    return set(sources) == set(EXPECTED_SOURCE_HASHES) and all(
        source.publisher == "IELTS"
        and source.classification == "OFFICIAL_RUBRIC_SOURCE"
        and source.usage_classification
        == "PRIVATE_SOURCE_REFERENCE_AND_STRUCTURED_INTERNAL_RUBRIC"
        and source.source_pdf_sha256 == EXPECTED_SOURCE_HASHES[source_id]
        for source_id, source in sources.items()
    )


def _safe_json(path: Path, component: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise RubricLoadError(RubricErrorCode.MISSING_FILE, component) from exc
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise RubricLoadError(RubricErrorCode.INVALID_JSON, component) from exc
    if not isinstance(value, dict):
        raise RubricLoadError(RubricErrorCode.INVALID_JSON, component)
    return value


def _safe_asset(base: Path, relative: Any, component: str) -> Path:
    if not isinstance(relative, str) or not relative or Path(relative).is_absolute() or ".." in Path(relative).parts:
        raise RubricLoadError(RubricErrorCode.PATH_ESCAPE, component)
    if base.is_symlink():
        raise RubricLoadError(RubricErrorCode.PATH_ESCAPE, component)
    candidate = base / relative
    try:
        base_real = base.resolve(strict=True)
        candidate_real = candidate.resolve(strict=True)
    except FileNotFoundError as exc:
        raise RubricLoadError(RubricErrorCode.MISSING_FILE, component) from exc
    try:
        candidate_real.relative_to(base_real)
    except ValueError as exc:
        raise RubricLoadError(RubricErrorCode.PATH_ESCAPE, component) from exc
    current = base
    for part in Path(relative).parts:
        current = current / part
        if current.is_symlink():
            raise RubricLoadError(RubricErrorCode.PATH_ESCAPE, component)
    if not candidate_real.is_file():
        raise RubricLoadError(RubricErrorCode.PATH_ESCAPE, component)
    return candidate_real


def _validate_schema(instance: dict[str, Any], schema: dict[str, Any], component: str) -> None:
    try:
        Draft202012Validator.check_schema(schema)
        errors = list(Draft202012Validator(schema, format_checker=FormatChecker()).iter_errors(instance))
    except Exception as exc:
        raise RubricLoadError(RubricErrorCode.INVALID_SCHEMA, component) from exc
    if errors:
        raise RubricLoadError(RubricErrorCode.INVALID_SCHEMA, component)


def _sha256(path: Path, component: str) -> str:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError as exc:
        raise RubricLoadError(RubricErrorCode.MISSING_FILE, component) from exc


def _load_pin(path: Path = DEFAULT_PIN) -> RubricPin:
    raw = _safe_json(path, "pin")
    required = {
        "rubric_id", "version", "schema_version", "task_type", "module",
        "runtime_content_sha256", "runtime_review_gate", "hash_disposition",
        "commercial_distribution_status", "public_repo_status",
        "asset_file_sha256", "approved_provenance",
    }
    if set(raw) != required or not isinstance(raw["asset_file_sha256"], dict):
        raise RubricLoadError(RubricErrorCode.INVALID_SCHEMA, "pin")
    sources = raw["approved_provenance"]
    if not isinstance(sources, list) or len(sources) != 2:
        raise RubricLoadError(RubricErrorCode.PROVENANCE_INVALID, "pin")
    try:
        provenance = tuple(SourceProvenance(**item) for item in sources)
        values = {
            key: raw[key]
            for key in required - {"asset_file_sha256", "approved_provenance", "hash_disposition"}
        }
        return RubricPin(
            **values,
            hash_disposition=FrozenMapping(raw["hash_disposition"]),
            asset_file_sha256=FrozenMapping(raw["asset_file_sha256"]),
            approved_provenance=provenance,
        )
    except (KeyError, TypeError) as exc:
        raise RubricLoadError(RubricErrorCode.INVALID_SCHEMA, "pin") from exc


def _walk_keys(value: Any) -> Iterator[str]:
    if isinstance(value, dict):
        for key, child in value.items():
            yield str(key).lower()
            yield from _walk_keys(child)


def _walk_strings(value: Any) -> Iterator[str]:
    if isinstance(value, dict):
        for key, child in value.items():
            yield str(key).casefold()
            yield from _walk_strings(child)
    elif isinstance(value, list):
        for child in value:
            yield from _walk_strings(child)
    elif isinstance(value, str):
        yield value.casefold()


def _reject_contamination(*documents: dict[str, Any]) -> None:
    for document in documents:
        for key in _walk_keys(document):
            if key in FORBIDDEN_AUTHORITY_KEYS:
                raise RubricLoadError(RubricErrorCode.AUTHORITY_CONTAMINATION, "authority")
            if key.endswith(".5"):
                raise RubricLoadError(RubricErrorCode.HALF_BAND_FORBIDDEN, "authority")


def _reject_criterion_contamination(*documents: dict[str, Any]) -> None:
    tokens = (
        "research", "teacher", "student", "target_band", "target-band",
        "target band", "synthetic", "empirical", "model essay",
        "chart facts", "chartfacts", "vision extraction", "image model",
        "image-model", "extracted number",
    )
    for document in documents:
        if any(token in value for value in _walk_strings(document) for token in tokens):
            raise RubricLoadError(RubricErrorCode.AUTHORITY_CONTAMINATION, "authority")


def _validate_derived_mapping(anchor: dict[str, Any]) -> None:
    """Keep internal mappings total and bounded by their exact official claims."""
    official_ids = {claim["id"] for claim in anchor["official_descriptor"]}
    interpretation = anchor["structured_interpretation"]
    mapped_ids: set[str] = set()
    performance_dimensions = interpretation.get("performance_dimensions")
    if isinstance(performance_dimensions, dict):
        for references in performance_dimensions.values():
            if isinstance(references, list):
                mapped_ids.update(references)
    for key in ("positive_characteristics", "limiting_characteristics"):
        references = interpretation.get(key)
        if isinstance(references, list):
            mapped_ids.update(references)
    if mapped_ids - official_ids:
        raise RubricLoadError(
            RubricErrorCode.AUTHORITY_CONTAMINATION, "derived_mapping"
        )
    if official_ids - mapped_ids:
        raise RubricLoadError(RubricErrorCode.COVERAGE_INVALID, "derived_mapping")


def runtime_semantic_document(
    manifest: dict[str, Any], criteria: dict[str, dict[str, Any]]
) -> dict[str, Any]:
    """Return the exact path-free semantic envelope used for runtime identity."""
    manifest_fields = (
        "rubric_id", "rubric_version", "schema_version", "task_type", "module",
        "criteria", "authority", "official_band_levels", "criterion_files",
        "commercial_distribution_status", "public_repo_status",
    )
    source_fields = (
        "source_id", "publisher", "source_version", "classification",
        "usage_classification",
    )
    return {
        "manifest": {key: manifest[key] for key in manifest_fields},
        "authority_sources": [
            {key: source[key] for key in source_fields if key in source}
            for source in manifest["source_records"]
        ],
        "criteria": {
            criterion: criteria[criterion]
            for criterion in manifest["criteria"]
        },
    }


def runtime_content_hash(
    manifest: dict[str, Any], criteria: dict[str, dict[str, Any]]
) -> str:
    """Hash the canonical semantic envelope with the runtime hash contract."""
    semantics = runtime_semantic_document(manifest, criteria)
    # This fixed semantic envelope is the domain separator. Filesystem identity,
    # package-byte hashes and PDF provenance hashes are deliberately absent.
    canonical = json.dumps(semantics, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _build_snapshot(
    pin: RubricPin,
    documents: dict[str, dict[str, Any]],
    criterion_order: tuple[str, ...],
) -> StructuredRubricSnapshot:
    criteria = []
    for code in criterion_order:
        document = documents[code]
        definition = document["criterion_definition"]
        anchors = []
        for band in WHOLE_BANDS:
            anchor = document["bands"][str(band)]
            source = anchor["source_reference"]
            anchors.append(BandAnchor(
                band=band,
                source=SourceCoordinate(
                    source_id=source["source_id"], pdf_page=source["pdf_page"],
                    band=source["band"], criterion=source["criterion"],
                ),
                official_claims=tuple(
                    OfficialClaim(claim_id=claim["id"], text=claim["text"])
                    for claim in anchor["official_descriptor"]
                ),
                derived_internal=FrozenMapping(anchor["structured_interpretation"]),
            ))
        criteria.append(CriterionRubric(
            code=code, name=document["criterion_name"],
            official_definition=definition["official_definition"],
            assessment_dimensions=tuple(definition["assessment_dimensions"]),
            anchors=tuple(anchors),
        ))
    return StructuredRubricSnapshot(
        rubric_id=pin.rubric_id, version=pin.version, schema_version=pin.schema_version,
        task_type=pin.task_type, module=pin.module,
        runtime_content_sha256=pin.runtime_content_sha256,
        runtime_review_gate=pin.runtime_review_gate,
        commercial_distribution_status=pin.commercial_distribution_status,
        public_repo_status=pin.public_repo_status,
        approved_provenance=pin.approved_provenance, criteria=tuple(criteria),
    )


@dataclass(frozen=True)
class _RuntimeContract:
    rubric_id: str
    task_type: str
    criteria: tuple[str, ...]
    expected_runtime_hash: str
    expected_source_hashes: Mapping[str, str]
    expected_definition_coordinates: Mapping[str, dict[str, Any]]
    expected_asset_keys: frozenset[str]
    review_gate: str
    band_source_id: str
    band_pages: tuple[int, ...]
    environment_variable: str
    default_root: Path
    expected_hash_disposition: Mapping[str, Any]


_CANONICAL_JSON_DISPOSITION = {
    "algorithm": "SHA-256",
    "encoding": "UTF-8",
    "canonical_json": "ensure_ascii=false; sort_keys=true; separators=(',', ':')",
    "semantic_envelope": ["manifest", "authority_sources", "criteria"],
}

_TASK2_CONTRACT = _RuntimeContract(
    rubric_id="ielts_academic_writing_task2",
    task_type="task2",
    criteria=CRITERIA,
    expected_runtime_hash=EXPECTED_RUNTIME_HASH,
    expected_source_hashes=EXPECTED_SOURCE_HASHES,
    expected_definition_coordinates=EXPECTED_DEFINITION_COORDINATES,
    expected_asset_keys=EXPECTED_ASSET_KEYS,
    review_gate="RUBRIC_V1_READY",
    band_source_id="IELTS_WBD_2023_TASK2",
    band_pages=(9, 9, 9, 9, 9, 8, 8, 7, 7, 7),
    environment_variable="IELTS_TASK2_RUBRIC_ROOT",
    default_root=DEFAULT_ROOT,
    expected_hash_disposition={
        **_CANONICAL_JSON_DISPOSITION,
        "superseded_planned_runtime_content_sha256":
            "9995dffede200486b9eb6cf29f087508045c77997f2ee30b814dc46a172447ad",
    },
)

_TASK1_CONTRACT = _RuntimeContract(
    rubric_id="ielts_academic_writing_task1",
    task_type="task1",
    criteria=TASK1_CRITERIA,
    expected_runtime_hash=TASK1_EXPECTED_RUNTIME_HASH,
    expected_source_hashes=TASK1_EXPECTED_SOURCE_HASHES,
    expected_definition_coordinates=TASK1_EXPECTED_DEFINITION_COORDINATES,
    expected_asset_keys=TASK1_EXPECTED_ASSET_KEYS,
    review_gate="TASK1_RUBRIC_V1_READY",
    band_source_id="IELTS_WBD_2023_TASK1",
    band_pages=(5, 5, 5, 5, 5, 4, 4, 3, 3, 3),
    environment_variable="IELTS_TASK1_RUBRIC_ROOT",
    default_root=TASK1_DEFAULT_ROOT,
    expected_hash_disposition={
        **_CANONICAL_JSON_DISPOSITION,
        "contract_version": "P1_01_RUNTIME_SEMANTIC_V1",
        "provenance_and_paths_excluded": True,
    },
)


def _load_rubric(
    contract: _RuntimeContract,
    rubric_root: Path | str | None,
    pin_path: Path | str,
) -> StructuredRubricSnapshot:
    pin = _load_pin(Path(pin_path))
    identity = (contract.rubric_id, "1.0.0", "1.0.0", contract.task_type, "academic")
    pin_identity = (pin.rubric_id, pin.version, pin.schema_version, pin.task_type, pin.module)
    if pin_identity != identity:
        raise RubricLoadError(RubricErrorCode.IDENTITY_MISMATCH, "pin")
    if (
        pin.runtime_content_sha256 != contract.expected_runtime_hash
        or set(pin.asset_file_sha256) != contract.expected_asset_keys
        or any(
            not isinstance(value, str)
            or len(value) != 64
            or any(character not in "0123456789abcdef" for character in value)
            for value in pin.asset_file_sha256.values()
        )
    ):
        raise RubricLoadError(RubricErrorCode.INTEGRITY_MISMATCH, "pin")
    disposition = _thaw(pin.hash_disposition)
    if any(
        disposition.get(key) != expected
        for key, expected in contract.expected_hash_disposition.items()
    ):
        raise RubricLoadError(RubricErrorCode.INTEGRITY_MISMATCH, "pin")
    if (
        pin.runtime_review_gate != contract.review_gate
        or pin.commercial_distribution_status != "NOT_ASSESSED"
        or pin.public_repo_status
        != "DO_NOT_PUBLISH_DESCRIPTOR_TEXT_WITHOUT_RIGHTS_REVIEW"
    ):
        raise RubricLoadError(RubricErrorCode.STATUS_INVALID, "pin")
    pin_sources = {source.source_id: source for source in pin.approved_provenance}
    if set(pin_sources) != set(contract.expected_source_hashes) or any(
        source.publisher != "IELTS"
        or source.classification != "OFFICIAL_RUBRIC_SOURCE"
        or source.usage_classification
        != "PRIVATE_SOURCE_REFERENCE_AND_STRUCTURED_INTERNAL_RUBRIC"
        or source.source_pdf_sha256 != contract.expected_source_hashes[source_id]
        for source_id, source in pin_sources.items()
    ):
        raise RubricLoadError(RubricErrorCode.PROVENANCE_INVALID, "pin")

    configured_root = rubric_root or os.getenv(
        contract.environment_variable, str(contract.default_root)
    )
    root = Path(configured_root)
    package_root = root.parent
    expected_assets = dict(pin.asset_file_sha256)
    asset_paths: dict[str, Path] = {}
    for relative, expected in expected_assets.items():
        path = _safe_asset(package_root, relative, "asset")
        asset_paths[relative] = path
        if _sha256(path, "asset") != expected:
            raise RubricLoadError(RubricErrorCode.INTEGRITY_MISMATCH, "asset")

    current_key = f"{contract.task_type}/current.json"
    current = _safe_json(asset_paths[current_key], "current")
    expected_manifest = f"v{pin.version}/manifest.json"
    _safe_asset(root, current.get("canonical_manifest"), "current_manifest")
    if current.get("canonical_manifest") != expected_manifest:
        raise RubricLoadError(RubricErrorCode.IDENTITY_MISMATCH, "current")
    manifest_path = _safe_asset(root, expected_manifest, "manifest")
    manifest_key = f"{contract.task_type}/v{pin.version}/manifest.json"
    if manifest_path != asset_paths[manifest_key]:
        raise RubricLoadError(RubricErrorCode.PATH_ESCAPE, "manifest")
    manifest = _safe_json(manifest_path, "manifest")

    schema_prefix = f"schema/{contract.task_type}"
    current_schema = _safe_json(
        asset_paths[f"{schema_prefix}-current-v1.schema.json"], "current_schema"
    )
    manifest_schema = _safe_json(
        asset_paths[f"{schema_prefix}-manifest-v1.schema.json"], "manifest_schema"
    )
    criterion_schema = _safe_json(
        asset_paths[f"{schema_prefix}-criterion-v1.schema.json"], "criterion_schema"
    )
    _validate_schema(current, current_schema, "current")
    _validate_schema(manifest, manifest_schema, "manifest")

    manifest_identity = tuple(
        manifest.get(key)
        for key in ("rubric_id", "rubric_version", "schema_version", "task_type", "module")
    )
    current_identity = tuple(
        current.get(key)
        for key in ("rubric_id", "rubric_version", "schema_version", "task_type", "module")
    )
    if identity != manifest_identity or identity != current_identity:
        raise RubricLoadError(RubricErrorCode.IDENTITY_MISMATCH, "identity")
    if (
        manifest.get("criteria") != list(contract.criteria)
        or manifest.get("official_band_levels") != list(WHOLE_BANDS)
    ):
        raise RubricLoadError(RubricErrorCode.COVERAGE_INVALID, "manifest")

    documents: dict[str, dict[str, Any]] = {}
    version_root = manifest_path.parent
    for code in contract.criteria:
        claim_ids: set[str] = set()
        relative = manifest.get("criterion_files", {}).get(code)
        path = _safe_asset(version_root, relative, code)
        criterion_key = f"{contract.task_type}/v{pin.version}/{code}.json"
        if path != asset_paths[criterion_key]:
            raise RubricLoadError(RubricErrorCode.PATH_ESCAPE, code)
        document = _safe_json(path, code)
        _validate_schema(document, criterion_schema, code)
        doc_identity = tuple(
            document.get(key)
            for key in ("rubric_id", "rubric_version", "schema_version", "task_type")
        )
        if doc_identity != identity[:4] or document.get("criterion") != code:
            raise RubricLoadError(RubricErrorCode.IDENTITY_MISMATCH, code)
        bands = document.get("bands", {})
        if set(bands) != {str(value) for value in WHOLE_BANDS}:
            raise RubricLoadError(RubricErrorCode.COVERAGE_INVALID, code)
        definition_source = document["criterion_definition"]["official_source_reference"]
        if definition_source != contract.expected_definition_coordinates[code]:
            raise RubricLoadError(RubricErrorCode.PROVENANCE_INVALID, code)
        for band in WHOLE_BANDS:
            anchor = bands[str(band)]
            source = anchor["source_reference"]
            expected_criterion = "ALL" if band == 0 else code
            if (
                source.get("source_id") != contract.band_source_id
                or source.get("pdf_page") != contract.band_pages[band]
                or source.get("band") != band
                or source.get("criterion") != expected_criterion
            ):
                raise RubricLoadError(RubricErrorCode.PROVENANCE_INVALID, code)
            if anchor.get("interpretation_status") != "DERIVED_INTERNAL":
                raise RubricLoadError(RubricErrorCode.AUTHORITY_CONTAMINATION, code)
            for claim in anchor["official_descriptor"]:
                if claim["id"] in claim_ids:
                    raise RubricLoadError(RubricErrorCode.COVERAGE_INVALID, "claim")
                claim_ids.add(claim["id"])
            _validate_derived_mapping(anchor)
        documents[code] = document

    approved = {item.source_id: item for item in pin.approved_provenance}
    records = manifest.get("source_records", [])
    if {record.get("source_id") for record in records} != set(approved):
        raise RubricLoadError(RubricErrorCode.PROVENANCE_INVALID, "manifest")
    for record in records:
        source = approved[record["source_id"]]
        if (
            record.get("publisher") != source.publisher
            or record.get("classification") != source.classification
            or record.get("usage_classification") != source.usage_classification
            or record.get("source_sha256") != source.source_pdf_sha256
        ):
            raise RubricLoadError(RubricErrorCode.PROVENANCE_MISMATCH, "manifest")
    if (
        manifest.get("commercial_distribution_status")
        != pin.commercial_distribution_status
        or manifest.get("public_repo_status") != pin.public_repo_status
    ):
        raise RubricLoadError(RubricErrorCode.STATUS_INVALID, "manifest")

    current_sources = {
        item.get("source_id"): item
        for item in current.get("source_provenance", [])
    }
    if set(current_sources) != set(approved):
        raise RubricLoadError(RubricErrorCode.PROVENANCE_INVALID, "current")
    for source_id, pinned in approved.items():
        projected = current_sources[source_id]
        if (
            projected.get("classification") != pinned.classification
            or projected.get("source_sha256") != pinned.source_pdf_sha256
        ):
            raise RubricLoadError(RubricErrorCode.PROVENANCE_MISMATCH, "current")
    for code in contract.criteria:
        current_criterion = current.get("criteria", {}).get(code, {})
        expected_criterion_path = f"v{pin.version}/{code}.json"
        _safe_asset(root, current_criterion.get("criterion_file"), code)
        if current_criterion.get("criterion_file") != expected_criterion_path:
            raise RubricLoadError(RubricErrorCode.IDENTITY_MISMATCH, code)
        projection = current_criterion.get("anchors", {})
        if set(projection) != {"5", "6", "7"}:
            raise RubricLoadError(RubricErrorCode.PROJECTION_DRIFT, code)
        for band in (5, 6, 7):
            canonical = documents[code]["bands"][str(band)]
            projected = projection[str(band)]
            if (
                projected.get("official_descriptor")
                != [claim["text"] for claim in canonical["official_descriptor"]]
                or projected.get("source_reference") != canonical["source_reference"]
                or projected.get("interpretation_status") != "DERIVED_INTERNAL"
                or projected.get("structured_interpretation_reference")
                != f"{expected_criterion_path}#/bands/{band}"
            ):
                raise RubricLoadError(RubricErrorCode.PROJECTION_DRIFT, code)

    _reject_contamination(current, manifest, *documents.values())
    _reject_criterion_contamination(*documents.values())
    digest = runtime_content_hash(manifest, documents)
    if digest != pin.runtime_content_sha256:
        raise RubricLoadError(RubricErrorCode.INTEGRITY_MISMATCH, "runtime")
    return _build_snapshot(pin, documents, contract.criteria)


def load_task2_rubric(
    rubric_root: Path | str | None = None,
    *,
    pin_path: Path | str = DEFAULT_PIN,
) -> StructuredRubricSnapshot:
    """Validate the pinned Academic Task 2 package."""
    return _load_rubric(_TASK2_CONTRACT, rubric_root, pin_path)


def load_task1_rubric(
    rubric_root: Path | str | None = None,
    *,
    pin_path: Path | str = TASK1_DEFAULT_PIN,
) -> StructuredRubricSnapshot:
    """Validate the pinned Academic Task 1 package."""
    return _load_rubric(_TASK1_CONTRACT, rubric_root, pin_path)


def verify_source_pdfs(
    snapshot: StructuredRubricSnapshot,
    inputs: Mapping[str, Path | str],
) -> tuple[str, ...]:
    """Optionally verify caller-supplied PDFs without retaining or exposing paths."""
    approved = {source.source_id: source for source in snapshot.approved_provenance}
    if set(inputs) - set(approved):
        raise RubricLoadError(RubricErrorCode.PROVENANCE_INVALID, "source")
    verified = []
    for source_id, path_value in inputs.items():
        if _sha256(Path(path_value), "source") != approved[source_id].source_pdf_sha256:
            raise RubricLoadError(RubricErrorCode.PROVENANCE_MISMATCH, "source")
        verified.append(source_id)
    return tuple(sorted(verified))
