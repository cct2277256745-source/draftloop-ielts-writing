"""Rights-aware benchmark case registry for offline evaluation.

The registry owns evidence-role, provenance, rights, and fixture-safety
validation.  It never evaluates an IELTS response, grants scoring authority, or
loads ignored corpus material.  Executable entries are projected into P1-03's
provider-independent ``BenchmarkCase`` only after every registry gate passes.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
import hashlib
import json
from pathlib import Path
import re
from types import MappingProxyType
from typing import Any, Iterable, Mapping

from jsonschema import Draft202012Validator

from .evaluation import BenchmarkCase, TierLabel


REGISTRY_SCHEMA_VERSION = "benchmark-case-registry-v1"
DEFAULT_REGISTRY_ROOT = Path(__file__).resolve().parents[1] / "resources" / "benchmark_registry" / "v1"
PIN_ROOT = Path(__file__).resolve().parents[1] / "resources" / "rubric_pins"
_FIXTURE_ID = re.compile(r"^[a-z][a-z0-9-]{1,80}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_FORBIDDEN_KEYS = {
    "api_key", "apikey", "authorization", "candidate_script", "customer_id",
    "customer_text", "essay", "essay_text", "private_path", "raw_response",
    "secret", "token",
}
_FORBIDDEN_VALUE_MARKERS = (
    "synthetic_reference/", "ielts_band_evidence_corpus/", "ielts_dataset_discovery/",
    "app/resources/corpus/", "bearer ", "api_key=", "/users/", "\\users\\",
)


class RegistryErrorCode(str, Enum):
    INVALID_SCHEMA = "INVALID_SCHEMA"
    DUPLICATE_CASE = "DUPLICATE_CASE"
    UNKNOWN_SOURCE = "UNKNOWN_SOURCE"
    RIGHTS_INVALID = "RIGHTS_INVALID"
    PROVENANCE_INVALID = "PROVENANCE_INVALID"
    AUTHORITY_CONTAMINATION = "AUTHORITY_CONTAMINATION"
    PRIVATE_DATA_FORBIDDEN = "PRIVATE_DATA_FORBIDDEN"
    UNSAFE_REFERENCE = "UNSAFE_REFERENCE"
    HASH_MISMATCH = "HASH_MISMATCH"
    CHECK_INVALID = "CHECK_INVALID"
    TASK_SCOPE_INVALID = "TASK_SCOPE_INVALID"
    PROMOTION_BLOCKED = "PROMOTION_BLOCKED"


class RegistryValidationError(ValueError):
    def __init__(self, code: RegistryErrorCode, message: str = "") -> None:
        self.code = code
        super().__init__(message or code.value)


class RightsStatus(str, Enum):
    PROJECT_OWNED = "PROJECT_OWNED"
    METADATA_REFERENCE_ONLY = "METADATA_REFERENCE_ONLY"
    UNKNOWN = "UNKNOWN"
    PROHIBITED = "PROHIBITED"


class Eligibility(str, Enum):
    EXECUTABLE_OFFLINE = "EXECUTABLE_OFFLINE"
    METADATA_ONLY = "METADATA_ONLY"
    EXCLUDED = "EXCLUDED"


class AuthorityRole(str, Enum):
    OFFICIAL_SANITY_METADATA = "OFFICIAL_SANITY_METADATA"
    SYNTHETIC_REGRESSION = "SYNTHETIC_REGRESSION"
    BAD_CASE_REPRODUCTION = "BAD_CASE_REPRODUCTION"


class ExpectedCheckKind(str, Enum):
    DETERMINISTIC = "DETERMINISTIC"
    HUMAN_REVIEW = "HUMAN_REVIEW"


class BadCaseDecision(str, Enum):
    PENDING = "PENDING"
    REJECTED = "REJECTED"
    APPROVED_SYNTHETIC_REPRODUCTION = "APPROVED_SYNTHETIC_REPRODUCTION"


def _canonical_json(value: Any) -> str:
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise RegistryValidationError(RegistryErrorCode.INVALID_SCHEMA, "non-canonical JSON") from exc


def _digest(value: Any) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def _freeze(value: Any) -> Any:
    if isinstance(value, Mapping):
        return MappingProxyType({str(key): _freeze(child) for key, child in sorted(value.items())})
    if isinstance(value, (list, tuple)):
        return tuple(_freeze(item) for item in value)
    if value is None or isinstance(value, (str, int, float, bool)):
        _canonical_json(value)
        return value
    raise RegistryValidationError(RegistryErrorCode.INVALID_SCHEMA, "unsupported JSON value")


def _thaw(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {key: _thaw(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_thaw(item) for item in value]
    return value


def _identifier(value: Any, code: RegistryErrorCode = RegistryErrorCode.INVALID_SCHEMA) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > 200:
        raise RegistryValidationError(code, "invalid identifier")
    return value.strip()


def _sha(value: Any, code: RegistryErrorCode = RegistryErrorCode.HASH_MISMATCH) -> str:
    value = _identifier(value, code)
    if not _SHA256.fullmatch(value):
        raise RegistryValidationError(code, "invalid SHA-256")
    return value


def _assert_safe(value: Any) -> None:
    """Reject raw candidate data, credentials, local paths, and ignored roots."""
    if isinstance(value, Mapping):
        for key, child in value.items():
            normalized = str(key).strip().lower()
            if normalized in _FORBIDDEN_KEYS or "candidate" in normalized or "customer" in normalized:
                raise RegistryValidationError(RegistryErrorCode.PRIVATE_DATA_FORBIDDEN, "forbidden metadata key")
            _assert_safe(child)
    elif isinstance(value, (list, tuple)):
        for child in value:
            _assert_safe(child)
    elif isinstance(value, str):
        normalized = value.lower().replace("\\", "/")
        if any(marker in normalized for marker in _FORBIDDEN_VALUE_MARKERS):
            raise RegistryValidationError(RegistryErrorCode.PRIVATE_DATA_FORBIDDEN, "unsafe value")


def _read_json(path: Path, code: RegistryErrorCode = RegistryErrorCode.INVALID_SCHEMA) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise RegistryValidationError(code, "cannot read JSON") from exc


def _safe_child(root: Path, filename: str) -> Path:
    if not _FIXTURE_ID.fullmatch(filename):
        raise RegistryValidationError(RegistryErrorCode.UNSAFE_REFERENCE, "invalid fixture id")
    lexical_candidate = root / f"{filename}.json"
    if lexical_candidate.is_symlink():
        raise RegistryValidationError(RegistryErrorCode.UNSAFE_REFERENCE, "fixture symlink")
    try:
        root_real = root.resolve(strict=True)
        candidate = lexical_candidate.resolve(strict=True)
    except OSError as exc:
        raise RegistryValidationError(RegistryErrorCode.UNSAFE_REFERENCE, "fixture is unavailable") from exc
    try:
        candidate.relative_to(root_real)
    except ValueError as exc:
        raise RegistryValidationError(RegistryErrorCode.UNSAFE_REFERENCE, "fixture path escape") from exc
    if candidate.is_symlink() or not candidate.is_file():
        raise RegistryValidationError(RegistryErrorCode.UNSAFE_REFERENCE, "unsafe fixture")
    return candidate


def _pinned_sources() -> dict[str, dict[str, str]]:
    result: dict[str, dict[str, str]] = {}
    for task_type in ("task1", "task2"):
        pin = _read_json(PIN_ROOT / f"{task_type}-v1.0.0.json", RegistryErrorCode.PROVENANCE_INVALID)
        if not isinstance(pin, Mapping) or pin.get("task_type") != task_type:
            raise RegistryValidationError(RegistryErrorCode.PROVENANCE_INVALID, "invalid rubric pin")
        rubric_id = _identifier(pin.get("rubric_id"), RegistryErrorCode.PROVENANCE_INVALID)
        version = _identifier(pin.get("version"), RegistryErrorCode.PROVENANCE_INVALID)
        runtime_hash = _sha(pin.get("runtime_content_sha256"), RegistryErrorCode.PROVENANCE_INVALID)
        sources = pin.get("approved_provenance")
        if not isinstance(sources, list):
            raise RegistryValidationError(RegistryErrorCode.PROVENANCE_INVALID, "missing pinned provenance")
        for source in sources:
            if not isinstance(source, Mapping) or source.get("classification") != "OFFICIAL_RUBRIC_SOURCE":
                raise RegistryValidationError(RegistryErrorCode.PROVENANCE_INVALID, "invalid pinned source")
            source_id = _identifier(source.get("source_id"), RegistryErrorCode.PROVENANCE_INVALID)
            result[source_id] = {
                "taskType": task_type,
                "rubricId": rubric_id,
                "version": version,
                "runtimeContentSha256": runtime_hash,
            }
    return result


@dataclass(frozen=True)
class SourceRights:
    source_id: str
    status: RightsStatus | str
    allowed_uses: tuple[str, ...]
    provenance_kind: str
    exclusion_reason: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "source_id", _identifier(self.source_id))
        try:
            status = RightsStatus(self.status)
        except ValueError as exc:
            raise RegistryValidationError(RegistryErrorCode.RIGHTS_INVALID, "unknown rights status") from exc
        uses = tuple(sorted({_identifier(item, RegistryErrorCode.RIGHTS_INVALID) for item in self.allowed_uses}))
        if not uses:
            raise RegistryValidationError(RegistryErrorCode.RIGHTS_INVALID, "allowed use required")
        provenance_kind = _identifier(self.provenance_kind, RegistryErrorCode.RIGHTS_INVALID)
        reason = self.exclusion_reason.strip() if isinstance(self.exclusion_reason, str) and self.exclusion_reason.strip() else None
        if status in {RightsStatus.UNKNOWN, RightsStatus.PROHIBITED} and reason is None:
            raise RegistryValidationError(RegistryErrorCode.RIGHTS_INVALID, "exclusion reason required")
        object.__setattr__(self, "status", status)
        object.__setattr__(self, "allowed_uses", uses)
        object.__setattr__(self, "provenance_kind", provenance_kind)
        object.__setattr__(self, "exclusion_reason", reason)

    def to_dict(self) -> dict[str, Any]:
        return {
            "sourceId": self.source_id,
            "status": self.status.value,
            "allowedUses": list(self.allowed_uses),
            "provenanceKind": self.provenance_kind,
            "exclusionReason": self.exclusion_reason,
        }


@dataclass(frozen=True)
class ProvenanceRecord:
    source_id: str
    reference_sha256: str
    task_type: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "source_id", _identifier(self.source_id, RegistryErrorCode.PROVENANCE_INVALID))
        object.__setattr__(self, "reference_sha256", _sha(self.reference_sha256, RegistryErrorCode.PROVENANCE_INVALID))
        task_type = _identifier(self.task_type, RegistryErrorCode.PROVENANCE_INVALID)
        if task_type not in {"task1", "task2"}:
            raise RegistryValidationError(RegistryErrorCode.TASK_SCOPE_INVALID, "unknown task")
        object.__setattr__(self, "task_type", task_type)

    def to_dict(self) -> dict[str, str]:
        return {"sourceId": self.source_id, "referenceSha256": self.reference_sha256, "taskType": self.task_type}


@dataclass(frozen=True)
class ExpectedCheck:
    kind: ExpectedCheckKind | str
    check_id: str

    def __post_init__(self) -> None:
        try:
            kind = ExpectedCheckKind(self.kind)
        except ValueError as exc:
            raise RegistryValidationError(RegistryErrorCode.CHECK_INVALID, "unknown check kind") from exc
        object.__setattr__(self, "kind", kind)
        object.__setattr__(self, "check_id", _identifier(self.check_id, RegistryErrorCode.CHECK_INVALID))

    def to_dict(self) -> dict[str, str]:
        return {"kind": self.kind.value, "checkId": self.check_id}


@dataclass(frozen=True)
class RegistryCase:
    case_id: str
    tier: TierLabel | str
    task_type: str
    authority_role: AuthorityRole | str
    eligibility: Eligibility | str
    provenance: ProvenanceRecord
    synthetic: bool
    corpus_partition: str | None
    criterion: str | None
    coverage_tags: tuple[str, ...]
    expected_checks: tuple[ExpectedCheck, ...]
    fixture_id: str | None = None
    fixture_sha256: str | None = None
    bad_case_lineage: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "case_id", _identifier(self.case_id))
        try:
            tier = TierLabel(self.tier)
            role = AuthorityRole(self.authority_role)
            eligibility = Eligibility(self.eligibility)
        except ValueError as exc:
            raise RegistryValidationError(RegistryErrorCode.INVALID_SCHEMA, "invalid case enum") from exc
        task_type = _identifier(self.task_type)
        if task_type not in {"task1", "task2"} or self.provenance.task_type != task_type:
            raise RegistryValidationError(RegistryErrorCode.TASK_SCOPE_INVALID, "task/provenance mismatch")
        criterion = self.criterion.strip() if isinstance(self.criterion, str) and self.criterion.strip() else None
        permitted = {"TA", "CC", "LR", "GRA"} if task_type == "task1" else {"TR", "CC", "LR", "GRA"}
        if criterion is not None and criterion not in permitted:
            raise RegistryValidationError(RegistryErrorCode.TASK_SCOPE_INVALID, "criterion is not task-scoped")
        tags = tuple(sorted({_identifier(tag) for tag in self.coverage_tags}))
        checks = tuple(self.expected_checks)
        if not tags or not checks:
            raise RegistryValidationError(RegistryErrorCode.CHECK_INVALID, "coverage and checks required")
        fixture_id = self.fixture_id.strip() if isinstance(self.fixture_id, str) and self.fixture_id.strip() else None
        fixture_hash = _sha(self.fixture_sha256) if self.fixture_sha256 is not None else None
        lineage = self.bad_case_lineage.strip() if isinstance(self.bad_case_lineage, str) and self.bad_case_lineage.strip() else None
        if role is AuthorityRole.OFFICIAL_SANITY_METADATA:
            if tier is not TierLabel.A or eligibility is not Eligibility.METADATA_ONLY or self.synthetic or fixture_id or fixture_hash:
                raise RegistryValidationError(RegistryErrorCode.AUTHORITY_CONTAMINATION, "official metadata role")
            if any(check.kind is not ExpectedCheckKind.HUMAN_REVIEW for check in checks):
                raise RegistryValidationError(RegistryErrorCode.CHECK_INVALID, "official metadata needs human check")
        if role is AuthorityRole.SYNTHETIC_REGRESSION:
            if tier is not TierLabel.C or eligibility is not Eligibility.EXECUTABLE_OFFLINE or not self.synthetic:
                raise RegistryValidationError(RegistryErrorCode.AUTHORITY_CONTAMINATION, "synthetic regression role")
            if self.corpus_partition != "mutation" or not fixture_id or not fixture_hash:
                raise RegistryValidationError(RegistryErrorCode.INVALID_SCHEMA, "mutation fixture required")
            if any(check.kind is not ExpectedCheckKind.DETERMINISTIC for check in checks):
                raise RegistryValidationError(RegistryErrorCode.CHECK_INVALID, "synthetic fixture needs deterministic checks")
        if role is AuthorityRole.BAD_CASE_REPRODUCTION:
            if tier is not TierLabel.C or eligibility is not Eligibility.EXECUTABLE_OFFLINE or not self.synthetic or not lineage:
                raise RegistryValidationError(RegistryErrorCode.AUTHORITY_CONTAMINATION, "bad case requires approved synthetic reproduction")
        object.__setattr__(self, "tier", tier)
        object.__setattr__(self, "authority_role", role)
        object.__setattr__(self, "eligibility", eligibility)
        object.__setattr__(self, "task_type", task_type)
        object.__setattr__(self, "criterion", criterion)
        object.__setattr__(self, "coverage_tags", tags)
        object.__setattr__(self, "expected_checks", checks)
        object.__setattr__(self, "fixture_id", fixture_id)
        object.__setattr__(self, "fixture_sha256", fixture_hash)
        object.__setattr__(self, "bad_case_lineage", lineage)

    def identity_payload(self) -> dict[str, Any]:
        return {
            "caseId": self.case_id, "tier": self.tier.value, "taskType": self.task_type,
            "authorityRole": self.authority_role.value, "eligibility": self.eligibility.value,
            "provenance": self.provenance.to_dict(), "synthetic": self.synthetic,
            "corpusPartition": self.corpus_partition, "criterion": self.criterion,
            "coverageTags": list(self.coverage_tags),
            "expectedChecks": [check.to_dict() for check in self.expected_checks],
            "fixtureId": self.fixture_id, "fixtureSha256": self.fixture_sha256,
            "badCaseLineage": self.bad_case_lineage,
        }

    @property
    def content_sha256(self) -> str:
        return _digest(self.identity_payload())


@dataclass(frozen=True)
class BadCaseRecord:
    bad_case_id: str
    complaint_id: str
    reproduction_sha256: str
    affected_layer: str
    evidence_refs: tuple[str, ...]
    hypothesis: str
    changed_variable: str
    decision: BadCaseDecision | str
    redaction_status: str
    lineage: str | None = None

    def __post_init__(self) -> None:
        _assert_safe({
            "badCaseId": self.bad_case_id,
            "complaintId": self.complaint_id,
            "reproductionSha256": self.reproduction_sha256,
            "affectedLayer": self.affected_layer,
            "evidenceRefs": list(self.evidence_refs),
            "hypothesis": self.hypothesis,
            "changedVariable": self.changed_variable,
            "lineage": self.lineage,
        })
        object.__setattr__(self, "bad_case_id", _identifier(self.bad_case_id))
        object.__setattr__(self, "complaint_id", _identifier(self.complaint_id))
        object.__setattr__(self, "reproduction_sha256", _sha(self.reproduction_sha256))
        object.__setattr__(self, "affected_layer", _identifier(self.affected_layer))
        refs = tuple(sorted({_identifier(item) for item in self.evidence_refs}))
        if not refs:
            raise RegistryValidationError(RegistryErrorCode.INVALID_SCHEMA, "evidence references required")
        object.__setattr__(self, "evidence_refs", refs)
        object.__setattr__(self, "hypothesis", _identifier(self.hypothesis))
        object.__setattr__(self, "changed_variable", _identifier(self.changed_variable))
        try:
            decision = BadCaseDecision(self.decision)
        except ValueError as exc:
            raise RegistryValidationError(RegistryErrorCode.INVALID_SCHEMA, "bad-case decision") from exc
        redaction = _identifier(self.redaction_status)
        if redaction != "COMPLETE":
            raise RegistryValidationError(RegistryErrorCode.PRIVATE_DATA_FORBIDDEN, "redaction incomplete")
        lineage = self.lineage.strip() if isinstance(self.lineage, str) and self.lineage.strip() else None
        object.__setattr__(self, "decision", decision)
        object.__setattr__(self, "redaction_status", redaction)
        object.__setattr__(self, "lineage", lineage)

    @classmethod
    def from_intake(cls, value: Mapping[str, Any]) -> "BadCaseRecord":
        _assert_safe(value)
        required = {
            "badCaseId", "complaintId", "reproductionSha256", "affectedLayer", "evidenceRefs",
            "hypothesis", "changedVariable", "decision", "redactionStatus", "lineage",
        }
        if set(value) != required or not isinstance(value["evidenceRefs"], list):
            raise RegistryValidationError(RegistryErrorCode.INVALID_SCHEMA, "bad-case intake schema")
        return cls(
            bad_case_id=value["badCaseId"], complaint_id=value["complaintId"],
            reproduction_sha256=value["reproductionSha256"], affected_layer=value["affectedLayer"],
            evidence_refs=tuple(value["evidenceRefs"]), hypothesis=value["hypothesis"],
            changed_variable=value["changedVariable"], decision=value["decision"],
            redaction_status=value["redactionStatus"], lineage=value["lineage"],
        )


@dataclass(frozen=True)
class BenchmarkRegistry:
    sources: tuple[SourceRights, ...]
    cases: tuple[RegistryCase, ...]
    fixture_payloads: Mapping[str, Mapping[str, Any]] = field(default_factory=dict, repr=False)

    def __post_init__(self) -> None:
        sources = tuple(sorted(self.sources, key=lambda item: item.source_id))
        cases = tuple(sorted(self.cases, key=lambda item: item.case_id))
        if len({source.source_id for source in sources}) != len(sources):
            raise RegistryValidationError(RegistryErrorCode.UNKNOWN_SOURCE, "duplicate source")
        if len({case.case_id for case in cases}) != len(cases):
            raise RegistryValidationError(RegistryErrorCode.DUPLICATE_CASE, "duplicate case")
        payloads = {key: _freeze(value) for key, value in self.fixture_payloads.items()}
        object.__setattr__(self, "sources", sources)
        object.__setattr__(self, "cases", cases)
        object.__setattr__(self, "fixture_payloads", MappingProxyType(payloads))
        self._validate_relations()

    def _validate_relations(self) -> None:
        sources = {item.source_id: item for item in self.sources}
        pinned = _pinned_sources()
        for case in self.cases:
            source = sources.get(case.provenance.source_id)
            if source is None:
                raise RegistryValidationError(RegistryErrorCode.UNKNOWN_SOURCE, "case source missing")
            if case.authority_role is AuthorityRole.OFFICIAL_SANITY_METADATA:
                pin = pinned.get(source.source_id)
                if pin is None or pin["taskType"] != case.task_type or source.status is not RightsStatus.METADATA_REFERENCE_ONLY:
                    raise RegistryValidationError(RegistryErrorCode.PROVENANCE_INVALID, "official metadata is not pinned")
                if case.provenance.reference_sha256 != pin["runtimeContentSha256"]:
                    raise RegistryValidationError(RegistryErrorCode.PROVENANCE_INVALID, "official pin identity mismatch")
            elif case.authority_role in {AuthorityRole.SYNTHETIC_REGRESSION, AuthorityRole.BAD_CASE_REPRODUCTION}:
                if source.status is not RightsStatus.PROJECT_OWNED or "OFFLINE_REGRESSION" not in source.allowed_uses:
                    raise RegistryValidationError(RegistryErrorCode.RIGHTS_INVALID, "fixture source not executable")
                if case.fixture_id is None or case.fixture_sha256 is None:
                    raise RegistryValidationError(RegistryErrorCode.HASH_MISMATCH, "fixture reference missing")
                payload = self.fixture_payloads.get(case.fixture_id)
                if payload is None:
                    raise RegistryValidationError(RegistryErrorCode.UNSAFE_REFERENCE, "fixture payload missing")
                if _digest(_thaw(payload)) != case.fixture_sha256:
                    raise RegistryValidationError(RegistryErrorCode.HASH_MISMATCH, "fixture hash mismatch")
                _validate_fixture_payload(_thaw(payload), case)

    @property
    def content_sha256(self) -> str:
        return _digest({
            "schemaVersion": REGISTRY_SCHEMA_VERSION,
            "sources": [item.to_dict() for item in self.sources],
            "cases": [item.identity_payload() for item in self.cases],
            "fixtures": {key: _digest(_thaw(value)) for key, value in self.fixture_payloads.items()},
        })

    def executable_cases(self) -> tuple[BenchmarkCase, ...]:
        result: list[BenchmarkCase] = []
        for case in self.cases:
            if case.eligibility is not Eligibility.EXECUTABLE_OFFLINE:
                continue
            assert case.fixture_id is not None
            fixture = _thaw(self.fixture_payloads[case.fixture_id])
            payload = dict(fixture["payload"])
            payload["registryCaseId"] = case.case_id
            payload["registryCaseSha256"] = case.content_sha256
            payload["registryIdentity"] = self.content_sha256
            result.append(BenchmarkCase(case.case_id, case.tier, case.task_type, payload))
        return tuple(result)

    def fixture_hash_inventory(self) -> dict[str, str]:
        """Return canonical fixture content hashes without exposing payload text."""
        return {
            fixture_id: _digest(_thaw(payload))
            for fixture_id, payload in sorted(self.fixture_payloads.items())
        }

    def coverage_summary(self) -> dict[str, Any]:
        criteria = {
            "task1": {criterion: 0 for criterion in ("TA", "CC", "LR", "GRA")},
            "task2": {criterion: 0 for criterion in ("TR", "CC", "LR", "GRA")},
        }
        tags: dict[str, int] = {}
        tiers = {tier.value: 0 for tier in TierLabel}
        eligibility = {item.value: 0 for item in Eligibility}
        rights = {item.value: 0 for item in RightsStatus}
        for source in self.sources:
            rights[source.status.value] += 1
        for case in self.cases:
            tiers[case.tier.value] += 1
            eligibility[case.eligibility.value] += 1
            if case.criterion is not None:
                criteria[case.task_type][case.criterion] += 1
            for tag in case.coverage_tags:
                tags[tag] = tags.get(tag, 0) + 1
        return {
            "registryIdentity": self.content_sha256,
            "tiers": tiers,
            "eligibility": eligibility,
            "rights": rights,
            "criteria": criteria,
            "coverageTags": dict(sorted(tags.items())),
            "fixtureHashes": self.fixture_hash_inventory(),
            "tierBStatus": "NOT_COLLECTED", "tierDStatus": "NOT_COLLECTED",
            "executableCaseCount": len(self.executable_cases()),
        }


def _validate_fixture_payload(value: Any, case: RegistryCase) -> None:
    required = {
        "schemaVersion", "fixtureId", "synthetic", "corpusPartition", "sourceId", "taskType",
        "rubricIdentity", "criterionUnderTest", "scenario", "payload",
    }
    _assert_safe(value)
    if not isinstance(value, Mapping) or set(value) != required:
        raise RegistryValidationError(RegistryErrorCode.INVALID_SCHEMA, "fixture schema")
    if value["schemaVersion"] != REGISTRY_SCHEMA_VERSION or value["fixtureId"] != case.fixture_id:
        raise RegistryValidationError(RegistryErrorCode.INVALID_SCHEMA, "fixture identity")
    if value["synthetic"] is not True or value["corpusPartition"] != "mutation":
        raise RegistryValidationError(RegistryErrorCode.AUTHORITY_CONTAMINATION, "fixture is not synthetic mutation")
    if value["sourceId"] != case.provenance.source_id or value["taskType"] != case.task_type:
        raise RegistryValidationError(RegistryErrorCode.TASK_SCOPE_INVALID, "fixture scope")
    if value["criterionUnderTest"] != case.criterion or not isinstance(value["payload"], Mapping):
        raise RegistryValidationError(RegistryErrorCode.INVALID_SCHEMA, "fixture criterion/payload")
    identity = value["rubricIdentity"]
    if not isinstance(identity, Mapping) or set(identity) != {"rubricId", "version", "runtimeContentSha256"}:
        raise RegistryValidationError(RegistryErrorCode.PROVENANCE_INVALID, "fixture rubric identity")
    pinned = _pinned_sources()
    expected = next((item for item in pinned.values() if item["taskType"] == case.task_type), None)
    if expected is None or dict(identity) != {
        "rubricId": expected["rubricId"], "version": expected["version"], "runtimeContentSha256": expected["runtimeContentSha256"],
    }:
        raise RegistryValidationError(RegistryErrorCode.PROVENANCE_INVALID, "fixture rubric mismatch")
    forbidden_claims = {"targetOverallProfile", "criterionProfiles", "awardedBand", "officialRubric", "officialScore"}
    if forbidden_claims & set(value):
        raise RegistryValidationError(RegistryErrorCode.AUTHORITY_CONTAMINATION, "scoring claim in fixture")


def _parse_source(value: Any) -> SourceRights:
    required = {"sourceId", "status", "allowedUses", "provenanceKind", "exclusionReason"}
    if not isinstance(value, Mapping) or set(value) != required or not isinstance(value["allowedUses"], list):
        raise RegistryValidationError(RegistryErrorCode.INVALID_SCHEMA, "source schema")
    return SourceRights(value["sourceId"], value["status"], tuple(value["allowedUses"]), value["provenanceKind"], value["exclusionReason"])


def _parse_case(value: Any) -> RegistryCase:
    required = {
        "caseId", "tier", "taskType", "authorityRole", "eligibility", "provenance", "synthetic",
        "corpusPartition", "criterion", "coverageTags", "expectedChecks", "fixtureId", "fixtureSha256", "badCaseLineage",
    }
    if not isinstance(value, Mapping) or set(value) != required:
        raise RegistryValidationError(RegistryErrorCode.INVALID_SCHEMA, "case schema")
    provenance = value["provenance"]
    if not isinstance(provenance, Mapping) or set(provenance) != {"sourceId", "referenceSha256", "taskType"}:
        raise RegistryValidationError(RegistryErrorCode.INVALID_SCHEMA, "provenance schema")
    checks = value["expectedChecks"]
    if not isinstance(value["coverageTags"], list) or not isinstance(checks, list):
        raise RegistryValidationError(RegistryErrorCode.INVALID_SCHEMA, "case collections")
    parsed_checks = []
    for check in checks:
        if not isinstance(check, Mapping) or set(check) != {"kind", "checkId"}:
            raise RegistryValidationError(RegistryErrorCode.CHECK_INVALID, "check schema")
        parsed_checks.append(ExpectedCheck(check["kind"], check["checkId"]))
    return RegistryCase(
        case_id=value["caseId"], tier=value["tier"], task_type=value["taskType"],
        authority_role=value["authorityRole"], eligibility=value["eligibility"],
        provenance=ProvenanceRecord(provenance["sourceId"], provenance["referenceSha256"], provenance["taskType"]),
        synthetic=value["synthetic"], corpus_partition=value["corpusPartition"], criterion=value["criterion"],
        coverage_tags=tuple(value["coverageTags"]), expected_checks=tuple(parsed_checks), fixture_id=value["fixtureId"],
        fixture_sha256=value["fixtureSha256"], bad_case_lineage=value["badCaseLineage"],
    )


def load_registry(root: Path = DEFAULT_REGISTRY_ROOT) -> BenchmarkRegistry:
    """Load the fixed local package without consulting ignored corpus locations."""
    root = Path(root)
    schema = _read_json(root / "registry.schema.json")
    manifest = _read_json(root / "manifest.json")
    _assert_safe(manifest)
    try:
        Draft202012Validator.check_schema(schema)
        errors = list(Draft202012Validator(schema).iter_errors(manifest))
    except Exception as exc:
        raise RegistryValidationError(RegistryErrorCode.INVALID_SCHEMA, "registry JSON schema") from exc
    if errors:
        raise RegistryValidationError(RegistryErrorCode.INVALID_SCHEMA, "manifest validation")
    if set(manifest) != {"schemaVersion", "sources", "cases"} or manifest["schemaVersion"] != REGISTRY_SCHEMA_VERSION:
        raise RegistryValidationError(RegistryErrorCode.INVALID_SCHEMA, "manifest envelope")
    if not isinstance(manifest["sources"], list) or not isinstance(manifest["cases"], list):
        raise RegistryValidationError(RegistryErrorCode.INVALID_SCHEMA, "manifest collections")
    sources = tuple(_parse_source(item) for item in manifest["sources"])
    cases = tuple(_parse_case(item) for item in manifest["cases"])
    fixtures: dict[str, Mapping[str, Any]] = {}
    for case in cases:
        if case.fixture_id is None:
            continue
        fixture_path = _safe_child(root / "fixtures", case.fixture_id)
        fixture = _read_json(fixture_path)
        fixtures[case.fixture_id] = fixture
    return BenchmarkRegistry(sources, cases, fixtures)


def registry_gate(registry: BenchmarkRegistry) -> dict[str, Any]:
    """Return only non-scoring acceptance evidence for P1-04."""
    summary = registry.coverage_summary()
    required_criteria = {
        "task1": {"TA", "CC", "LR", "GRA"},
        "task2": {"TR", "CC", "LR", "GRA"},
    }
    criteria_ok = all(all(summary["criteria"][task][criterion] > 0 for criterion in wanted) for task, wanted in required_criteria.items())
    tags = summary["coverageTags"]
    required_tags = {"SCHEMA_FAILURE", "TYPED_FAILURE", "TASK_BOUNDARY", "AUTHORITY_BOUNDARY", "PRIVATE_DATA_BOUNDARY"}
    tier_a_ok = summary["tiers"]["A"] >= 2 and summary["eligibility"]["METADATA_ONLY"] >= 2
    passed = criteria_ok and tier_a_ok and all(tags.get(tag, 0) > 0 for tag in required_tags) and summary["executableCaseCount"] >= 8
    return {
        "status": "BENCHMARK_REGISTRY_PASS" if passed else "BENCHMARK_REGISTRY_BLOCKED",
        "registryIdentity": registry.content_sha256,
        "coverage": summary,
        "checks": {
            "tierAMetadataOnly": tier_a_ok,
            "taskScopedCriteria": criteria_ok,
            "failureAndBoundaryCoverage": all(tags.get(tag, 0) > 0 for tag in required_tags),
            "executableTierC": summary["executableCaseCount"] >= 8,
            "tierB": "NOT_COLLECTED",
            "tierD": "NOT_COLLECTED",
        },
    }


def promote_bad_case(record: BadCaseRecord, reproduction: RegistryCase) -> RegistryCase:
    """Allow promotion only after explicit approval and a new synthetic fixture."""
    if record.decision is not BadCaseDecision.APPROVED_SYNTHETIC_REPRODUCTION:
        raise RegistryValidationError(RegistryErrorCode.PROMOTION_BLOCKED, "human approval required")
    if reproduction.authority_role is not AuthorityRole.BAD_CASE_REPRODUCTION or reproduction.bad_case_lineage != record.bad_case_id:
        raise RegistryValidationError(RegistryErrorCode.PROMOTION_BLOCKED, "invalid reproduction lineage")
    if not reproduction.synthetic or reproduction.corpus_partition != "mutation":
        raise RegistryValidationError(RegistryErrorCode.PROMOTION_BLOCKED, "must be synthetic mutation")
    return reproduction
