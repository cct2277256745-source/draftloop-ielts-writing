"""Synthetic controls plus opt-in real package tests. Never write package bytes."""
from __future__ import annotations

from dataclasses import FrozenInstanceError, replace
import hashlib
import io
import json
import os
from pathlib import Path
import pickle
import time
import unittest
from unittest.mock import patch

from app.core.rag_historical import (
    ARTIFACT_NAMES, LOCAL_PRIVATE_RESEARCH, LOCAL_PRIVATE_RESEARCH_AUTHORIZED,
    PRIVATE_AUTH_ENV, QWEN_PIN, RestrictedBM25Unpickler, discover_historical_controls,
    package_sentinel, verify_artifact_bytes,
)
from app.core.rag_package import (
    CONTROL_DOCUMENT_NAMES, PACKAGE_ENVIRONMENT_VARIABLE, TARGET_ENVIRONMENT_VARIABLE,
    RagDisabledReason, RagPackageError, RagUsagePolicy, discover_for_default_product,
    discover_frozen_package, validate_frozen_runtime,
)
from app.core.rag_local_runtime import LocalRagRuntime
from app.core.rag_retrieval import RetrievalProviderError
from app.application.rag_coaching import PostScoreRagCoachingService
from scripts.validate_local_private_rag import run_smoke, smoke_inputs


def synthetic_controls():
    # Independent control-only fixture, not a copy of any real package artifact.
    integration = "\n".join((
        "# Retrieval package integration contract v1", "ADVISORY_ONLY",
        "DIRECT_SCORE_AUTHORITY = false", "LICENSE_UNCLEAR / PRIVATE_RESEARCH_ONLY",
        "Search dense FAISS Top20 and BM25 Top20.", "Fuse with RRF k=60", "Return Top5.",
        "No reranker, score prediction, calibration, or scoring-pipeline integration"))
    boundary = "\n".join(("# Public and private boundary", "LICENSE_UNCLEAR", "PRIVATE_RESEARCH_ONLY",
        "does not grant commercial authorization", "outside the authorized private research environment"))
    raw = {"INTEGRATION_CONTRACT.md": integration.encode(), "PUBLIC_PRIVATE_BOUNDARY.md": boundary.encode()}
    artifacts = {name: {"bytes": len(raw.get(name, b"")),
        "sha256": hashlib.sha256(raw.get(name, b"")).hexdigest()} for name in ARTIFACT_NAMES}
    manifest = {"schema_version": "frozen_retrieval_package.v1", "package_id": "ielts-frozen-retrieval-v1",
        "package_version": "1.0.0", "artifacts": artifacts, "read_only": True,
        "rights_status": "LICENSE_UNCLEAR", "usage_scope": "PRIVATE_RESEARCH_ONLY", "commercial_authorization": False}
    raw[CONTROL_DOCUMENT_NAMES[0]] = json.dumps(manifest).encode()
    report = {"package_id": manifest["package_id"], "package_version": "1.0.0",
        "core_manifest_sha256": hashlib.sha256(raw[CONTROL_DOCUMENT_NAMES[0]]).hexdigest(),
        "status": "FROZEN_RETRIEVAL_PACKAGE_RELEASE_PASS", "checks": {"fixture": True},
        "artifact_actual_sha256": {name: info["sha256"] for name, info in artifacts.items()}}
    raw["release_validation_report.json"] = json.dumps(report).encode()
    return raw, manifest


class HistoricalControlTests(unittest.TestCase):
    def discover(self, *, target=LOCAL_PRIVATE_RESEARCH, commercial=False, use="assessment-calibration", mutate=None):
        raw, manifest = synthetic_controls()
        if mutate:
            mutate(raw, manifest)
        return discover_historical_controls(Path("/unused-synthetic-control-fixture"), raw, manifest,
            RagUsagePolicy(target, use, commercial))

    def test_adapter_retains_rights_and_returns_private_only_capability(self):
        outcome = self.discover()
        self.assertEqual(outcome.authorized_package.authorization_scope, LOCAL_PRIVATE_RESEARCH_AUTHORIZED)
        self.assertEqual(outcome.authorized_package.manifest["rights_status"], "LICENSE_UNCLEAR")
        self.assertEqual(outcome.authorized_package.manifest["usage_scope"], "PRIVATE_RESEARCH_ONLY")
        self.assertEqual(outcome.decision.control_documents_read, CONTROL_DOCUMENT_NAMES)
        self.assertNotIn("/unused", json.dumps(outcome.decision.content()))

    def test_private_authorization_never_authorizes_public_target(self):
        for target in ("local-desktop-public-safe", "PUBLIC_SAFE_AUTHORIZED", "production"):
            outcome = self.discover(target=target)
            self.assertIsNone(outcome.authorized_package)
            self.assertEqual(outcome.decision.reason, RagDisabledReason.TARGET_ENVIRONMENT_UNAUTHORIZED)

    def test_commercial_and_wrong_use_rejected(self):
        self.assertEqual(self.discover(commercial=True).decision.reason, RagDisabledReason.COMMERCIAL_USE_UNAUTHORIZED)
        self.assertEqual(self.discover(use="score-overwrite").decision.reason, RagDisabledReason.INTENDED_USE_UNAUTHORIZED)

    def test_malformed_controls_and_tampered_contract_fail_closed(self):
        for name in CONTROL_DOCUMENT_NAMES:
            def mutate(raw, manifest):
                raw[name] = b"malformed control"
            self.assertIsNone(self.discover(mutate=mutate).authorized_package)

    def test_invalid_release_report_rejected(self):
        def mutate(raw, manifest):
            value = json.loads(raw["release_validation_report.json"])
            value["checks"]["fixture"] = False
            raw["release_validation_report.json"] = json.dumps(value).encode()
        self.assertEqual(self.discover(mutate=mutate).decision.reason, RagDisabledReason.RELEASE_VALIDATION_FAILED)

    def test_environment_is_explicit_and_unsetting_never_loads_encoder(self):
        def forbidden(*args):
            raise AssertionError("Encoder must not load without authorization")
        missing = LocalRagRuntime({}, encoder_factory=forbidden)
        self.assertEqual(missing.projection()["reason"], "PACKAGE_NOT_CONFIGURED")
        unauthorized = LocalRagRuntime({PACKAGE_ENVIRONMENT_VARIABLE: "/unused",
            TARGET_ENVIRONMENT_VARIABLE: LOCAL_PRIVATE_RESEARCH}, encoder_factory=forbidden)
        self.assertEqual(unauthorized.projection()["reason"], "RIGHTS_UNAUTHORIZED")

    def test_pickle_globals_deny_arbitrary_code(self):
        with self.assertRaises(RagPackageError):
            RestrictedBM25Unpickler(io.BytesIO(pickle.dumps(os.system))).load()


@unittest.skipUnless(os.environ.get("RUN_REAL_PRIVATE_RAG_TESTS") == "1", "Explicit private real-package opt-in required")
class RealPrivateRuntimeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.env = dict(os.environ)
        cls.runtime = LocalRagRuntime(cls.env)
        if cls.runtime.provider is None:
            raise AssertionError(cls.runtime.projection())
        cls.authorized = cls.runtime.discovery.authorized_package
        cls.before = package_sentinel(cls.authorized.root)

    @classmethod
    def tearDownClass(cls):
        try:
            verify_artifact_bytes(cls.authorized)
            assert package_sentinel(cls.authorized.root) == cls.before
        finally:
            cls.runtime.close()

    def validate(self):
        return validate_frozen_runtime(self.authorized, runtime_embedding=QWEN_PIN, dense_inspector=None)

    def test_real_729_hashes_shapes_and_orders(self):
        outcome = self.validate()
        self.assertIsNotNone(outcome.validated_package)
        self.assertEqual(len(outcome.validated_package.object_order), 729)
        self.assertEqual(len(outcome.validated_package.object_by_id), 729)
        self.assertTrue(outcome.mutation_free)
        self.assertEqual(outcome.runtime_handle.index.d, 1024)
        self.assertEqual(outcome.runtime_handle.index.ntotal, 729)

    def test_real_query_c3_consumer_and_score_isolation(self):
        evidence = run_smoke(self.runtime)
        self.assertEqual(evidence["smoke"]["evidenceGate"], "SUFFICIENT")
        self.assertEqual(evidence["smoke"]["consumerRagItemCount"], 2)
        self.assertTrue(evidence["ragOnOffLockedScoreIdentical"])

    def test_default_public_discovery_still_rejects_real_private_package(self):
        outcome = discover_for_default_product(self.env)
        self.assertEqual(outcome.decision.reason, RagDisabledReason.TARGET_ENVIRONMENT_UNAUTHORIZED)
        self.assertIsNone(outcome.authorized_package)
        self.assertFalse(self.runtime.projection()["publicSafeAuthorized"])

    def test_tampered_read_fails_without_writing_package(self):
        original = Path.read_bytes
        def altered(path):
            data = original(path)
            return data[:-1] + bytes([data[-1] ^ 1]) if path.name == "dense.faiss" else data
        with patch.object(Path, "read_bytes", altered):
            self.assertEqual(self.validate().reason, RagDisabledReason.RUNTIME_ARTIFACT_INVALID)

    def test_missing_and_duplicate_object_reads_fail(self):
        original = Path.read_text
        for duplicate in (False, True):
            def altered(path, *args, **kwargs):
                text = original(path, *args, **kwargs)
                if path.name != "retrieval_objects.jsonl":
                    return text
                rows = text.splitlines()
                if duplicate:
                    rows[1] = rows[0]
                else:
                    rows.pop()
                return "\n".join(rows)
            with patch.object(Path, "read_text", altered):
                self.assertIsNone(self.validate().validated_package)

    def test_wrong_embedding_pin_fails(self):
        outcome = validate_frozen_runtime(self.authorized,
            runtime_embedding=replace(QWEN_PIN, revision="wrong"), dense_inspector=None)
        self.assertEqual(outcome.reason, RagDisabledReason.EMBEDDING_RUNTIME_INCOMPATIBLE)

    def test_bm25_order_failure_without_package_write(self):
        original = RestrictedBM25Unpickler.load
        def swapped(loader):
            value = original(loader)
            value["doc_ids"][0], value["doc_ids"][1] = value["doc_ids"][1], value["doc_ids"][0]
            return value
        with patch.object(RestrictedBM25Unpickler, "load", swapped):
            self.assertIsNone(self.validate().validated_package)

    def test_runtime_revocation_disables_projection_and_loaded_provider(self):
        original = self.env.pop(PACKAGE_ENVIRONMENT_VARIABLE)
        try:
            self.assertEqual(self.runtime.projection()["reason"], "PACKAGE_NOT_CONFIGURED")
            with self.assertRaises(RetrievalProviderError):
                self.runtime.provider.retrieve("safe query")
        finally:
            self.env[PACKAGE_ENVIRONMENT_VARIABLE] = original

    def test_timeout_and_failure_preserve_exact_score_and_base_coaching(self):
        inputs = smoke_inputs()
        before = inputs.locked_score.content()
        for failure in (False, True):
            def unavailable(*args):
                if failure:
                    raise RuntimeError("controlled failure")
                time.sleep(0.02)
                raise RuntimeError("controlled late result")
            with patch.object(self.runtime.provider, "retrieve", unavailable):
                result = PostScoreRagCoachingService(self.runtime, timeout_seconds=0.001).compose(inputs)
            self.assertEqual(result.smoke["consumerRagItemCount"], 0)
            self.assertEqual(inputs.locked_score.content(), before)
            self.assertEqual(result.coaching.locked_score_sha256, inputs.locked_score.snapshot_sha256)

    def test_conflicting_empirical_evidence_removed(self):
        inputs = smoke_inputs()
        service = PostScoreRagCoachingService(self.runtime)
        pack = service.sidecar.retrieve_after_scoring(inputs.observation, inputs.locked_score).evidence_pack
        context = replace(inputs.gate_context, official_conflict_object_ids=(pack.items[0].object_id,))
        result = service.compose(replace(inputs, gate_context=context))
        self.assertEqual(result.smoke["evidenceGate"], "CONFLICT")
        self.assertEqual(result.smoke["consumerRagItemCount"], 0)

    def test_locked_snapshot_is_immutable(self):
        inputs = smoke_inputs()
        with self.assertRaises(FrozenInstanceError):
            inputs.locked_score.overall_band = 9.0


if __name__ == "__main__":
    unittest.main()
