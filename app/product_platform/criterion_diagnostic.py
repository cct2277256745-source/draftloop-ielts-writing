"""Internal, content-addressed projection of the scoring execution record.

Not a report, score authority, or learner API contract. Raw inputs never enter it.
"""
from dataclasses import dataclass, field
import json

from jsonschema import Draft202012Validator

from app.core.criterion_scoring import FailureCategory, output_schema
from app.core.providers import ProviderFailureCode
from .contracts import canonical_json, digest


VERSION = "criterion-scoring-diagnostic-v1"
CRITERIA = ("TR", "CC", "LR", "GRA")


def _enum(*values):
    return {"enum": list(values)}


def _object(properties):
    return {"type": "object", "additionalProperties": False,
            "required": list(properties), "properties": properties}


def _schema():
    source = output_schema()
    assessed, unassessable = [s["allOf"][1]["properties"] for s in source["oneOf"]]
    failures = [e.value for e in FailureCategory]
    count = {"type": "integer", "minimum": 0}
    sha = {"type": ["string", "null"], "pattern": "^[0-9a-f]{64}$"}
    # Closed field vocabulary: a Provider-controlled property name cannot be a path.
    keys = set(source["$defs"]["common"]["properties"])
    keys.update(source["$defs"]["common"]["required"])
    for name in ("finding", "hook", "evidenceRef"):
        keys.update(source["$defs"][name]["properties"])
    from app.core.criterion_scoring import OUTPUT_DIAGNOSTICS
    row = _object({
        "criterion": _enum(*CRITERIA),
        "workerTerminalCategory": _enum("VALIDATED", "NOT_PRODUCED", *failures),
        "providerOutcomeCategory": _enum("SUCCESS", "PROVIDER_FAILURE", "UNEXPECTED", "NOT_CALLED", "UNKNOWN"),
        "providerFailureCode": _enum(None, *[e.value for e in ProviderFailureCode]),
        "repairAttemptCount": {"type": "integer", "minimum": 0, "maximum": 1},
        "semanticStatus": _enum("ASSESSED", "UNASSESSABLE", "FAILURE", "NOT_PRODUCED"),
        "candidateBandRange": {"anyOf": [{"type": "null"}, {"type": "array", "minItems": 2, "maxItems": 2, "items": {"type": "integer", "minimum": 0, "maximum": 9}}]},
        "estimatedBand": {"type": ["number", "null"], "minimum": 0, "maximum": 9},
        "confidence": _enum(None, "HIGH", "MEDIUM", "LOW"),
        "confidenceReasonCodes": {"type": "array", "items": assessed["confidenceReasons"]["items"]},
        "unassessableReasonCodes": {"type": "array", "items": unassessable["unassessableReasons"]["items"]},
        "validationCode": _enum(None, "OUTPUT_INVALID", *[v[0] for v in OUTPUT_DIAGNOSTICS.values()]),
        "validationPath": {"type": ["string", "null"], "pattern": r"^\$(?:\.(?:" + "|".join(sorted(keys)) + r")|\[[0-9]+\])*$"},
        "usedContradictionHooksCount": count, "anchorSupportCount": count,
        "higherBandBoundaryCount": count, "assessmentHash": sha,
    })
    return _object({
        "schemaVersion": _enum(VERSION),
        "submissionId": {"type": "string", "pattern": "^submission_[0-9a-f]{32}$"},
        "executionRecordHash": sha,
        "outcome": _enum("COMPLETE", "INCOMPLETE", "FAILED"),
        "sharedFailureCategories": {"type": "array", "items": _enum(*failures)},
        "semanticRecordCount": {"type": "integer", "minimum": 0, "maximum": 4},
        "assessedCount": {"type": "integer", "minimum": 0, "maximum": 4},
        "bundleCreated": {"type": "boolean"},
        "criteria": {"type": "array", "minItems": 4, "maxItems": 4, "items": row},
    })


@dataclass(frozen=True)
class CriterionScoringDiagnostic:
    payload_json: str = field(repr=False)

    def __post_init__(self):
        try:
            value = json.loads(self.payload_json)
            valid = Draft202012Validator(_schema()).is_valid(value)
            valid = valid and [r["criterion"] for r in value["criteria"]] == list(CRITERIA)
        except (ValueError, TypeError, KeyError):
            valid = False
        if not valid:
            raise ValueError("INVALID_CRITERION_DIAGNOSTIC")

    def content(self):
        return json.loads(self.payload_json)

    @classmethod
    def from_execution(cls, submission_id, outcome):
        record = outcome.execution_record
        assessments = {a.criterion: a for a in outcome.assessments}
        if {k: a.assessment_sha256 for k, a in assessments.items()} != dict(record.assessment_sha256_by_criterion):
            raise ValueError("DIAGNOSTIC_LINEAGE_MISMATCH")
        rows = []
        for criterion in CRITERIA:
            a = assessments.get(criterion)
            failure = next((f for f in record.failures if f.criterion == criterion), None)
            attempts = sorted((t for t in record.attempts if t.criterion == criterion), key=lambda t: t.attempt)
            last = attempts[-1] if attempts else None
            rows.append({
                "criterion": criterion,
                "workerTerminalCategory": "VALIDATED" if a else failure.category.value if failure else "NOT_PRODUCED",
                "providerOutcomeCategory": ("SUCCESS" if last.validation_state in ("VALID", "INVALID") else last.validation_state) if last else ("UNKNOWN" if failure else "NOT_CALLED"),
                "providerFailureCode": failure.provider_failure_code.value if failure and failure.provider_failure_code else last.provider_failure_code if last else None,
                "repairAttemptCount": max((t.attempt - 1 for t in attempts), default=0),
                "semanticStatus": a.status.value if a else "FAILURE" if failure else "NOT_PRODUCED",
                "candidateBandRange": [a.lower_band, a.upper_band] if a and a.lower_band is not None else None,
                "estimatedBand": a.estimated_band if a else None,
                "confidence": a.confidence if a else None,
                "confidenceReasonCodes": list(a.confidence_reasons) if a else [],
                "unassessableReasonCodes": list(a.unassessable_reasons) if a else [],
                "validationCode": last.validation_code if last else None,
                "validationPath": last.validation_path if last else None,
                "usedContradictionHooksCount": len(a.used_contradiction_hooks) if a else 0,
                "anchorSupportCount": sum(f["role"] == "ANCHOR_SUPPORT" for f in a.findings) if a else 0,
                "higherBandBoundaryCount": sum(f["role"] == "HIGHER_BAND_BOUNDARY" for f in a.findings) if a else 0,
                "assessmentHash": a.assessment_sha256 if a else None,
            })
        # Hash the existing safe execution record, not inputs or Provider content.
        execution_hash = digest({"status": record.status.value,
            "assessmentHashes": dict(record.assessment_sha256_by_criterion),
            "failures": [f.safe_content() for f in record.failures],
            "bundleHash": record.bundle_sha256,
            "attempts": [vars(t) for t in record.attempts]})
        return cls(canonical_json({"schemaVersion": VERSION, "submissionId": submission_id,
            "executionRecordHash": execution_hash, "outcome": record.status.value,
            "sharedFailureCategories": [f.category.value for f in record.failures if f.criterion is None],
            "semanticRecordCount": len(record.assessment_sha256_by_criterion),
            "assessedCount": sum(a.status.value == "ASSESSED" for a in assessments.values()),
            "bundleCreated": record.bundle_sha256 is not None, "criteria": rows}))
