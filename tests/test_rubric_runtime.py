from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import shutil
import tempfile
import unittest

from app.core.rubric import (
    RubricErrorCode,
    RubricLoadError,
    load_task2_rubric,
    verify_source_pdfs,
)


RUBRIC_ROOT = Path(__file__).resolve().parents[1] / "rubrics" / "task2"
EXPECTED_RUNTIME_HASH = "3e0a7322fbd4f8ebe90b88a6ec6183f12c02ec7189f1e32fff76d69f3e7bc9a2"


@unittest.skipUnless(RUBRIC_ROOT.is_dir(), "private Task 2 rubric assets are absent")
class StructuredRubricRuntimeTests(unittest.TestCase):
    def setUp(self) -> None:
        self._temps: list[tempfile.TemporaryDirectory[str]] = []

    def tearDown(self) -> None:
        for temporary in self._temps:
            temporary.cleanup()

    def _copy_package(self) -> tuple[Path, Path]:
        temporary = tempfile.TemporaryDirectory()
        self._temps.append(temporary)
        base = Path(temporary.name)
        shutil.copytree(RUBRIC_ROOT.parent, base / "rubrics")
        pin = json.loads((Path(__file__).resolve().parents[1] / "app/resources/rubric_pins/task2-v1.0.0.json").read_text())
        pin_path = base / "pin.json"
        pin_path.write_text(json.dumps(pin), encoding="utf-8")
        return base / "rubrics/task2", pin_path

    def _rewrite_json(self, root: Path, pin_path: Path, relative: str, mutate) -> None:
        path = root.parent / relative
        value = json.loads(path.read_text(encoding="utf-8"))
        mutate(value)
        path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
        pin = json.loads(pin_path.read_text(encoding="utf-8"))
        pin["asset_file_sha256"][relative] = hashlib.sha256(path.read_bytes()).hexdigest()
        pin_path.write_text(json.dumps(pin), encoding="utf-8")

    def _assert_rejected(self, root: Path, pin_path: Path, *codes: RubricErrorCode) -> None:
        with self.assertRaises(RubricLoadError) as caught:
            load_task2_rubric(root, pin_path=pin_path)
        self.assertIn(caught.exception.code, codes)
        self.assertNotIn(str(root), str(caught.exception))

    def test_valid_package_loads_complete_immutable_snapshot(self) -> None:
        before = {
            path.relative_to(RUBRIC_ROOT): (path.read_bytes(), path.stat().st_mtime_ns)
            for path in RUBRIC_ROOT.rglob("*")
            if path.is_file()
        }

        snapshot = load_task2_rubric(RUBRIC_ROOT)

        self.assertEqual(snapshot.rubric_id, "ielts_academic_writing_task2")
        self.assertEqual(snapshot.version, "1.0.0")
        self.assertEqual(snapshot.runtime_content_sha256, EXPECTED_RUNTIME_HASH)
        self.assertEqual([criterion.code for criterion in snapshot.criteria], ["TR", "CC", "LR", "GRA"])
        self.assertTrue(all(tuple(anchor.band for anchor in criterion.anchors) == tuple(range(10)) for criterion in snapshot.criteria))
        with self.assertRaises((AttributeError, TypeError)):
            snapshot.version = "2.0.0"  # type: ignore[misc]
        with self.assertRaises((AttributeError, TypeError)):
            snapshot.criteria[0].anchors[0].derived_internal._items = ()  # type: ignore[misc]
        payload = snapshot.to_prompt_payload()
        payload["officialRubric"]["criteria"][0]["bands"][0]["officialClaims"][0]["text"] = "mutated"
        self.assertNotEqual(
            snapshot.to_prompt_payload()["officialRubric"]["criteria"][0]["bands"][0]["officialClaims"][0]["text"],
            "mutated",
        )
        after = {
            path.relative_to(RUBRIC_ROOT): (path.read_bytes(), path.stat().st_mtime_ns)
            for path in RUBRIC_ROOT.rglob("*")
            if path.is_file()
        }
        self.assertEqual(before, after)

    def test_runtime_identity_does_not_depend_on_root_path(self) -> None:
        original = os.environ.get("IELTS_TASK2_RUBRIC_ROOT")
        try:
            os.environ["IELTS_TASK2_RUBRIC_ROOT"] = str(RUBRIC_ROOT)
            snapshot = load_task2_rubric()
        finally:
            if original is None:
                os.environ.pop("IELTS_TASK2_RUBRIC_ROOT", None)
            else:
                os.environ["IELTS_TASK2_RUBRIC_ROOT"] = original
        self.assertEqual(snapshot.runtime_content_sha256, EXPECTED_RUNTIME_HASH)
        self.assertNotIn(str(RUBRIC_ROOT), repr(snapshot))
        self.assertNotIn("local_source_id", repr(snapshot))

    def test_hash_layers_and_provenance_paths_are_separate(self) -> None:
        snapshot = load_task2_rubric(RUBRIC_ROOT)
        payload = snapshot.to_prompt_payload()
        rendered = json.dumps(payload, ensure_ascii=False)
        self.assertEqual(payload["rubricIdentity"]["runtime_content_sha256"], EXPECTED_RUNTIME_HASH)
        self.assertNotIn("asset_file_sha256", rendered)
        self.assertNotIn("source_pdf_sha256", rendered)
        self.assertNotIn("local_source_id", rendered)
        self.assertNotIn("Downloads", rendered)

    def test_raw_asset_change_is_rejected_by_package_integrity(self) -> None:
        root, pin_path = self._copy_package()
        path = root / "v1.0.0/TR.json"
        path.write_bytes(path.read_bytes() + b"\n")
        self._assert_rejected(root, pin_path, RubricErrorCode.INTEGRITY_MISMATCH)

        root, pin_path = self._copy_package()
        schema = root.parent / "schema/task2-criterion-v1.schema.json"
        schema.write_bytes(schema.read_bytes() + b"\n")
        self._assert_rejected(root, pin_path, RubricErrorCode.INTEGRITY_MISMATCH)

    def test_missing_criterion_and_version_pointer_change_fail_closed(self) -> None:
        root, pin_path = self._copy_package()
        (root / "v1.0.0/LR.json").unlink()
        self._assert_rejected(root, pin_path, RubricErrorCode.MISSING_FILE)

        root, pin_path = self._copy_package()
        self._rewrite_json(
            root, pin_path, "task2/current.json",
            lambda value: value.update(
                rubric_version="1.0.1",
                canonical_manifest="v1.0.1/manifest.json",
            ),
        )
        self._assert_rejected(
            root, pin_path, RubricErrorCode.MISSING_FILE,
            RubricErrorCode.IDENTITY_MISMATCH,
        )

    def test_semantic_change_is_rejected_by_runtime_hash(self) -> None:
        root, pin_path = self._copy_package()
        self._rewrite_json(
            root, pin_path, "task2/v1.0.0/TR.json",
            lambda value: value["bands"]["9"]["official_descriptor"][0].update(text="changed"),
        )
        self._assert_rejected(root, pin_path, RubricErrorCode.INTEGRITY_MISMATCH)

    def test_missing_band_half_band_duplicate_claim_and_bad_coordinate_fail_closed(self) -> None:
        mutations = (
            lambda value: value["bands"].pop("4"),
            lambda value: value["bands"].update({"6.5": value["bands"]["6"]}),
            lambda value: value["bands"]["7"]["official_descriptor"].append(value["bands"]["7"]["official_descriptor"][0]),
            lambda value: value["bands"]["7"]["source_reference"].update(band=8),
        )
        for mutate in mutations:
            with self.subTest(mutate=mutate):
                root, pin_path = self._copy_package()
                self._rewrite_json(root, pin_path, "task2/v1.0.0/TR.json", mutate)
                self._assert_rejected(
                    root, pin_path, RubricErrorCode.INVALID_SCHEMA,
                    RubricErrorCode.COVERAGE_INVALID, RubricErrorCode.HALF_BAND_FORBIDDEN,
                    RubricErrorCode.PROVENANCE_INVALID,
                )

    def test_missing_status_and_invalid_provenance_fail_closed(self) -> None:
        root, pin_path = self._copy_package()
        self._rewrite_json(
            root, pin_path, "task2/v1.0.0/manifest.json",
            lambda value: value.pop("commercial_distribution_status"),
        )
        self._assert_rejected(root, pin_path, RubricErrorCode.INVALID_SCHEMA, RubricErrorCode.STATUS_INVALID)

        root, pin_path = self._copy_package()
        self._rewrite_json(
            root, pin_path, "task2/v1.0.0/manifest.json",
            lambda value: value["source_records"][0].update(source_id="UNKNOWN"),
        )
        self._assert_rejected(root, pin_path, RubricErrorCode.PROVENANCE_INVALID)

        root, pin_path = self._copy_package()
        pin = json.loads(pin_path.read_text(encoding="utf-8"))
        pin.pop("runtime_review_gate")
        pin_path.write_text(json.dumps(pin), encoding="utf-8")
        self._assert_rejected(root, pin_path, RubricErrorCode.INVALID_SCHEMA)

    def test_authority_contamination_and_projection_drift_fail_closed(self) -> None:
        for forbidden in (
            "research_data", "teacher_data", "student_data", "target_band",
            "synthetic_data", "empirical_data",
        ):
            with self.subTest(forbidden=forbidden):
                root, pin_path = self._copy_package()
                self._rewrite_json(
                    root, pin_path, "task2/v1.0.0/TR.json",
                    lambda value, key=forbidden: value.update({key: {"polluted": True}}),
                )
                self._assert_rejected(root, pin_path, RubricErrorCode.INVALID_SCHEMA, RubricErrorCode.AUTHORITY_CONTAMINATION)

        root, pin_path = self._copy_package()
        self._rewrite_json(
            root, pin_path, "task2/v1.0.0/TR.json",
            lambda value: value["bands"]["7"]["structured_interpretation"].update(
                {"external_evidence": "teacher calibration data"}
            ),
        )
        self._assert_rejected(root, pin_path, RubricErrorCode.AUTHORITY_CONTAMINATION)

        root, pin_path = self._copy_package()
        self._rewrite_json(
            root, pin_path, "task2/current.json",
            lambda value: value["criteria"]["TR"]["anchors"]["7"]["official_descriptor"].append("drift"),
        )
        self._assert_rejected(root, pin_path, RubricErrorCode.PROJECTION_DRIFT)

    def test_manifest_path_escape_and_symlink_fail_closed(self) -> None:
        root, pin_path = self._copy_package()
        self._rewrite_json(
            root, pin_path, "task2/current.json",
            lambda value: value.update(canonical_manifest="../manifest.json"),
        )
        self._assert_rejected(root, pin_path, RubricErrorCode.PATH_ESCAPE)

        root, pin_path = self._copy_package()
        self._rewrite_json(
            root, pin_path, "task2/v1.0.0/manifest.json",
            lambda value: value["criterion_files"].update(TR="../TR.json"),
        )
        self._assert_rejected(root, pin_path, RubricErrorCode.PATH_ESCAPE)

        root, pin_path = self._copy_package()
        tr_path = root / "v1.0.0/TR.json"
        target = root / "v1.0.0/TR-target.json"
        tr_path.rename(target)
        tr_path.symlink_to(target.name)
        self._assert_rejected(root, pin_path, RubricErrorCode.PATH_ESCAPE)

    def test_source_pdf_is_optional_and_explicit_verification_is_path_independent(self) -> None:
        snapshot = load_task2_rubric(RUBRIC_ROOT)
        self.assertEqual(snapshot.runtime_content_sha256, EXPECTED_RUNTIME_HASH)
        source_value = os.getenv("IELTS_TEST_WBD_SOURCE_PDF")
        if source_value:
            source = Path(source_value)
            self.assertEqual(
                verify_source_pdfs(snapshot, {"IELTS_WBD_2023_TASK2": source}),
                ("IELTS_WBD_2023_TASK2",),
            )
            root, _ = self._copy_package()
            wrong = root / "current.json"
            with self.assertRaises(RubricLoadError) as caught:
                verify_source_pdfs(snapshot, {"IELTS_WBD_2023_TASK2": wrong})
            self.assertEqual(caught.exception.code, RubricErrorCode.PROVENANCE_MISMATCH)
            self.assertNotIn(str(wrong), str(caught.exception))


if __name__ == "__main__":
    unittest.main()
