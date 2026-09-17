"""Synthetic structure-only rubric fixture for mocked consumer tests."""
from __future__ import annotations

from app.core.rubric import (
    BandAnchor,
    CriterionRubric,
    FrozenMapping,
    OfficialClaim,
    SourceCoordinate,
    SourceProvenance,
    StructuredRubricSnapshot,
    EXPECTED_RUNTIME_HASH,
    EXPECTED_SOURCE_HASHES,
    TASK1_EXPECTED_RUNTIME_HASH,
    TASK1_EXPECTED_SOURCE_HASHES,
)


TEST_RUNTIME_HASH = EXPECTED_RUNTIME_HASH


def task2_snapshot() -> StructuredRubricSnapshot:
    criteria = []
    for code in ("TR", "CC", "LR", "GRA"):
        anchors = tuple(
            BandAnchor(
                band=band,
                source=SourceCoordinate(
                    source_id="IELTS_WBD_2023_TASK2",
                    pdf_page=9 if band < 5 else (8 if band < 7 else 7),
                    band=band,
                    criterion="ALL" if band == 0 else code,
                ),
                official_claims=(OfficialClaim(f"{code}-{band}-TEST", f"Test-only canonical claim {code} {band}."),),
                derived_internal=FrozenMapping({"test_dimension": [f"{code}-{band}-TEST"]}),
            )
            for band in range(10)
        )
        criteria.append(CriterionRubric(
            code=code,
            name=code,
            official_definition=f"Test-only definition for {code}.",
            assessment_dimensions=("test_dimension",),
            anchors=anchors,
        ))
    return StructuredRubricSnapshot(
        rubric_id="ielts_academic_writing_task2",
        version="1.0.0",
        schema_version="1.0.0",
        task_type="task2",
        module="academic",
        runtime_content_sha256=TEST_RUNTIME_HASH,
        runtime_review_gate="RUBRIC_V1_READY",
        commercial_distribution_status="NOT_ASSESSED",
        public_repo_status="DO_NOT_PUBLISH_DESCRIPTOR_TEXT_WITHOUT_RIGHTS_REVIEW",
        approved_provenance=(
            SourceProvenance("IELTS_WBD_2023_TASK2", "IELTS", "OFFICIAL_RUBRIC_SOURCE", "PRIVATE_SOURCE_REFERENCE_AND_STRUCTURED_INTERNAL_RUBRIC", EXPECTED_SOURCE_HASHES["IELTS_WBD_2023_TASK2"]),
            SourceProvenance("IELTS_WKAC_TASK2", "IELTS", "OFFICIAL_RUBRIC_SOURCE", "PRIVATE_SOURCE_REFERENCE_AND_STRUCTURED_INTERNAL_RUBRIC", EXPECTED_SOURCE_HASHES["IELTS_WKAC_TASK2"]),
        ),
        criteria=tuple(criteria),
    )


def task1_snapshot() -> StructuredRubricSnapshot:
    criteria = []
    for code in ("TA", "CC", "LR", "GRA"):
        anchors = tuple(
            BandAnchor(
                band=band,
                source=SourceCoordinate(
                    source_id="IELTS_WBD_2023_TASK1",
                    pdf_page=6 if band < 5 else (5 if band < 7 else 4),
                    band=band,
                    criterion="ALL" if band == 0 else code,
                ),
                official_claims=(OfficialClaim(
                    f"{code}-{band}-TEST",
                    f"Test-only canonical Task 1 claim {code} {band}.",
                ),),
                derived_internal=FrozenMapping({
                    "test_dimension": [f"{code}-{band}-TEST"]
                }),
            )
            for band in range(10)
        )
        criteria.append(CriterionRubric(
            code=code,
            name=code,
            official_definition=f"Test-only Task 1 definition for {code}.",
            assessment_dimensions=("test_dimension",),
            anchors=anchors,
        ))
    return StructuredRubricSnapshot(
        rubric_id="ielts_academic_writing_task1",
        version="1.0.0",
        schema_version="1.0.0",
        task_type="task1",
        module="academic",
        runtime_content_sha256=TASK1_EXPECTED_RUNTIME_HASH,
        runtime_review_gate="TASK1_RUBRIC_V1_READY",
        commercial_distribution_status="NOT_ASSESSED",
        public_repo_status="DO_NOT_PUBLISH_DESCRIPTOR_TEXT_WITHOUT_RIGHTS_REVIEW",
        approved_provenance=(
            SourceProvenance(
                "IELTS_WBD_2023_TASK1", "IELTS", "OFFICIAL_RUBRIC_SOURCE",
                "PRIVATE_SOURCE_REFERENCE_AND_STRUCTURED_INTERNAL_RUBRIC",
                TASK1_EXPECTED_SOURCE_HASHES["IELTS_WBD_2023_TASK1"],
            ),
            SourceProvenance(
                "IELTS_WKAC_TASK1", "IELTS", "OFFICIAL_RUBRIC_SOURCE",
                "PRIVATE_SOURCE_REFERENCE_AND_STRUCTURED_INTERNAL_RUBRIC",
                TASK1_EXPECTED_SOURCE_HASHES["IELTS_WKAC_TASK1"],
            ),
        ),
        criteria=tuple(criteria),
    )
