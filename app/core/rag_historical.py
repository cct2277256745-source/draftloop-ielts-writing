"""Read-only compatibility boundary for the historical private retrieval release.

No frozen bytes are repaired, reserialized or persisted. Private research consent
is deliberately not a public-rights grant. Pickle globals are deny-by-default.
"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
import io
import json
import pickle
import re
import unicodedata
from typing import Any, Mapping

from .rag_package import (
    AuthorizedFrozenPackage, CONTROL_DOCUMENT_NAMES, DiscoveryOutcome,
    DiscoveryStatus, EmbeddingRuntimePin, FrozenEvidenceObject, RagDisabledReason,
    RagPackageError, RagUsagePolicy, RuntimeValidationOutcome, RuntimeValidationStatus,
    ValidatedFrozenPackage, _decision, _safe_child, _sha256_bytes, _thaw,
)
from .submission import digest

LOCAL_PRIVATE_RESEARCH = "LOCAL_PRIVATE_RESEARCH"
LOCAL_PRIVATE_RESEARCH_AUTHORIZED = "LOCAL_PRIVATE_RESEARCH_AUTHORIZED"
PUBLIC_SAFE_AUTHORIZED = "PUBLIC_SAFE_AUTHORIZED"
PRIVATE_AUTH_ENV = "EMPIRICAL_RAG_LOCAL_PRIVATE_AUTHORIZED"
QWEN_PIN = EmbeddingRuntimePin(
    "Qwen/Qwen3-Embedding-0.6B", "97b0c614be4d77ee51c0cef4e5f07c00f9eb65b3",
    1024, True,
    "Instruct: Retrieve relevant IELTS writing evidence cards or empirical corpus patterns for the given coaching query.\nQuery:",
)
ARTIFACT_NAMES = frozenset({
    "INTEGRATION_CONTRACT.md", "PUBLIC_PRIVATE_BOUNDARY.md", "README.md",
    "benchmark_reference.json", "bm25_index.pkl", "dense.faiss",
    "embedding_index_manifest.json", "production_retrieval_manifest.json",
    "retrieval_config.json", "retrieval_objects.jsonl",
})


def require(condition, message):
    if not condition:
        raise RagPackageError(message)


def discover_historical_controls(root, raw, manifest, policy):
    common = dict(policy=policy, documents_read=CONTROL_DOCUMENT_NAMES)
    def denied(reason):
        return DiscoveryOutcome(_decision(DiscoveryStatus.RAG_DISABLED, reason=reason, **common), None)
    try:
        require(manifest.get("package_id") == "ielts-frozen-retrieval-v1"
                and manifest.get("package_version") == "1.0.0", "Historical identity invalid.")
        require(set(manifest["artifacts"]) == ARTIFACT_NAMES, "Historical artifact set invalid.")
        for name in ("INTEGRATION_CONTRACT.md", "PUBLIC_PRIVATE_BOUNDARY.md"):
            spec = manifest["artifacts"][name]
            require(len(raw[name]) == spec["bytes"] and _sha256_bytes(raw[name]) == spec["sha256"],
                    "Historical control hash mismatch.")
        integration = raw["INTEGRATION_CONTRACT.md"].decode("utf-8")
        boundary = raw["PUBLIC_PRIVATE_BOUNDARY.md"].decode("utf-8")
        # Recognize this legacy textual contract, never infer authorization from arbitrary Markdown.
        require(all(s in integration for s in (
            "# Retrieval package integration contract v1", "ADVISORY_ONLY",
            "DIRECT_SCORE_AUTHORITY = false", "LICENSE_UNCLEAR / PRIVATE_RESEARCH_ONLY",
            "Search dense FAISS Top20 and BM25 Top20.", "Fuse with RRF k=60", "Return Top5.",
            "No reranker, score prediction, calibration, or scoring-pipeline integration")),
            "Unrecognized historical integration contract.")
        require(all(s in boundary for s in ("# Public and private boundary", "LICENSE_UNCLEAR",
            "PRIVATE_RESEARCH_ONLY", "does not grant commercial authorization",
            "outside the authorized private research environment")), "Unrecognized private boundary.")
        report = json.loads(raw["release_validation_report.json"])
        require(report["package_id"] == manifest["package_id"]
                and report["package_version"] == manifest["package_version"]
                and report["core_manifest_sha256"] == _sha256_bytes(raw[CONTROL_DOCUMENT_NAMES[0]]),
                "Historical report identity mismatch.")
        common.update(package_id=manifest["package_id"], package_version=manifest["package_version"],
                      control_versions={"manifest": manifest["schema_version"],
                                        "adapter": "historical-private-controls-v1"})
        if report.get("status") != "FROZEN_RETRIEVAL_PACKAGE_RELEASE_PASS" or not report.get("checks") \
                or any(value is not True for value in report["checks"].values()):
            return denied(RagDisabledReason.RELEASE_VALIDATION_FAILED)
        for name, spec in manifest["artifacts"].items():
            require(report["artifact_actual_sha256"].get(name) == spec["sha256"],
                    "Historical report artifact mismatch.")
        require(report.get("upstream_actual_sha256", {}) == manifest.get("upstream_frozen_sha256", {}),
                "Historical source linkage report mismatch.")
    except (KeyError, ValueError, TypeError, UnicodeError):
        return denied(RagDisabledReason.CONTROL_DOCUMENT_INVALID)
    if policy.target_environment != LOCAL_PRIVATE_RESEARCH:
        return denied(RagDisabledReason.TARGET_ENVIRONMENT_UNAUTHORIZED)
    if policy.commercial:
        return denied(RagDisabledReason.COMMERCIAL_USE_UNAUTHORIZED)
    if policy.intended_use != "assessment-calibration":
        return denied(RagDisabledReason.INTENDED_USE_UNAUTHORIZED)
    if (manifest.get("rights_status") != "LICENSE_UNCLEAR"
            or manifest.get("usage_scope") != "PRIVATE_RESEARCH_ONLY"
            or manifest.get("commercial_authorization") is not False):
        return denied(RagDisabledReason.RIGHTS_UNAUTHORIZED)
    if manifest.get("read_only") is not True:
        return denied(RagDisabledReason.CONTROL_DOCUMENT_INVALID)
    decision = _decision(DiscoveryStatus.READY_FOR_RUNTIME_VALIDATION, reason=None, **common)
    # Control-byte hashes are private in-memory capability state, not public audit output.
    internal = dict(manifest)
    internal["controlByteHashes"] = {k: _sha256_bytes(v) for k, v in raw.items()}
    return DiscoveryOutcome(decision, AuthorizedFrozenPackage(
        root, internal, raw[CONTROL_DOCUMENT_NAMES[0]], policy,
        decision.decision_sha256, LOCAL_PRIVATE_RESEARCH_AUTHORIZED))


def package_sentinel(root):
    result = {}
    require({p.name for p in root.iterdir()} == ARTIFACT_NAMES | {
        "retrieval_package_manifest.json", "release_validation_report.json"}, "Unexpected package entries.")
    for path in (root, *root.iterdir()):
        stat = path.lstat()
        require(not path.is_symlink() and not stat.st_mode & 0o222, "Package is writable or linked.")
        result[path.name] = (stat.st_mode, stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns, stat.st_ino)
    return result


class RestrictedBM25Unpickler(pickle.Unpickler):
    def find_class(self, module, name):
        if (module, name) == ("rank_bm25", "BM25Okapi"):
            from rank_bm25 import BM25Okapi
            return BM25Okapi
        raise RagPackageError("Forbidden serialized runtime global.")


TOKEN_RE = re.compile(r"[a-z0-9]+(?:[-'][a-z0-9]+)*|[\u4e00-\u9fff]", re.I)


def bm25_tokenize(text):
    tokens = TOKEN_RE.findall(unicodedata.normalize("NFKC", text).lower())
    chars = [x for x in tokens if len(x) == 1 and "\u4e00" <= x <= "\u9fff"]
    return tokens + [a + b for a, b in zip(chars, chars[1:])]


@dataclass
class HistoricalRuntimeHandle:
    authorized: AuthorizedFrozenPackage
    sentinel: Mapping
    index: Any
    bm25: Any

    def assert_unchanged(self):
        require(package_sentinel(self.authorized.root) == self.sentinel, "Package mutation detected.")
        verify_artifact_bytes(self.authorized)


def verify_artifact_bytes(authorized):
    manifest = authorized.manifest
    for name, expected in manifest["controlByteHashes"].items():
        require(_sha256_bytes(_safe_child(authorized.root, name).read_bytes()) == expected,
                "Control bytes changed after authorization.")
    for name, spec in manifest["artifacts"].items():
        raw = _safe_child(authorized.root, name).read_bytes()
        require(len(raw) == spec["bytes"] and _sha256_bytes(raw) == spec["sha256"],
                "Declared artifact bytes invalid.")


def validate_historical_runtime(authorized, *, runtime_embedding):
    checked = []
    def failed(reason):
        return RuntimeValidationOutcome(RuntimeValidationStatus.RAG_DISABLED, reason, None, tuple(checked), False)
    try:
        require(authorized.authorization_scope == LOCAL_PRIVATE_RESEARCH_AUTHORIZED
                and authorized.usage_policy.target_environment == LOCAL_PRIVATE_RESEARCH
                and not authorized.usage_policy.commercial, "Private runtime capability required.")
        root, m = authorized.root, authorized.manifest
        before = package_sentinel(root)
        verify_artifact_bytes(authorized)
        checked.extend(sorted(ARTIFACT_NAMES))
        if runtime_embedding != QWEN_PIN:
            return failed(RagDisabledReason.EMBEDDING_RUNTIME_INCOMPATIBLE)
        import faiss
        import numpy as np
        config = json.loads(_safe_child(root, "retrieval_config.json").read_bytes())
        metadata = json.loads(_safe_child(root, "embedding_index_manifest.json").read_bytes())
        production = json.loads(_safe_child(root, "production_retrieval_manifest.json").read_bytes())
        require(config["schema_version"] == "retrieval_runtime_config.v1"
                and metadata["schema_version"] == "release_embedding_index_manifest.v1"
                and config["read_only"] is True
                and config["package_id"] == m["package_id"]
                and config["dense"]["index_path"] == "dense.faiss"
                and config["bm25"]["index_path"] == "bm25_index.pkl"
                and config["dense"]["index_type"] == "IndexFlatIP"
                and config["supported_optional_filters"] == [], "Runtime schema or mapping invalid.")
        require(m["retrieval_object_count"] == 729 and m["evidence_card_count"] == 723
                and m["cross_band_pattern_count"] == 6, "Object declaration invalid.")
        require(m["embedding_model"] == config["dense"]["model"] == metadata["embedding_model"] == QWEN_PIN.model_id
                and m["embedding_model_revision"] == config["dense"]["model_revision"] == metadata["embedding_model_revision"] == QWEN_PIN.revision
                and m["embedding_dimension"] == config["dense"]["dimension"] == metadata["embedding_dimension"] == 1024
                and m["normalization"] == config["dense"]["normalization"] == metadata["normalization"] == "L2",
                "Embedding declarations mismatch.")
        require(m["dense_top_k"] == config["dense"]["top_k"] == 20
                and m["bm25_top_k"] == config["bm25"]["top_k"] == 20
                and m["rrf_k"] == config["fusion"]["rrf_k"] == 60
                and m["final_top_k"] == config["fusion"]["final_top_k"] == 5
                and config["fusion"]["candidate_top_k"] == 20
                and config["fusion"]["method"] == "RRF"
                and m["reranker_enabled"] is config["reranker_enabled"] is False,
                "Frozen retrieval contract mismatch.")
        for field, name in (("corpus_hash", "retrieval_objects.jsonl"),
                            ("retrieval_objects_hash", "retrieval_objects.jsonl"),
                            ("config_hash", "retrieval_config.json"),
                            ("faiss_index_hash", "dense.faiss"), ("bm25_artifact_hash", "bm25_index.pkl")):
            require(m[field] == m["artifacts"][name]["sha256"], "Manifest linkage mismatch.")
        require(metadata["corpus_hash"] == m["corpus_hash"]
                and metadata["release_faiss"]["sha256"] == m["faiss_index_hash"]
                and metadata["source_embeddings"]["sha256"] == m["embedding_source_hash"]
                and metadata["retrieval_object_count"] == 729
                and metadata["faiss_position_mapping"] == "Zero-based FAISS position equals line order in retrieval_objects.jsonl.",
                "Dense lineage mismatch.")
        require(production["frozen_linkage"]["corpus_sha256"] == m["corpus_hash"]
                and production["frozen_linkage"]["retrieval_objects"] == 729
                and production["components"]["dense"]["model"] == QWEN_PIN.model_id
                and production["components"]["dense"]["revision"] == QWEN_PIN.revision
                and production["components"]["reranker"]["promoted"] is False
                and production["components"]["fusion"]["k"] == 60
                and production["rights"] == {"rights_status": "LICENSE_UNCLEAR", "usage_scope": "PRIVATE_RESEARCH_ONLY",
                                             "commercial_authorization": False}, "Production contract mismatch.")
        lines = _safe_child(root, "retrieval_objects.jsonl").read_text().splitlines()
        require(len(lines) == 729 and all(lines), "Object count invalid.")
        objects = [json.loads(line) for line in lines]
        ids = tuple(o["retrieval_id"] for o in objects)
        require(len(set(ids)) == 729 and all(isinstance(i, str) and i for i in ids), "Duplicate object identity.")
        require(Counter(o["object_type"] for o in objects) == {"EVIDENCE_CARD": 723, "CROSS_BAND_PATTERN": 6},
                "Object type counts invalid.")
        normalized = []
        source_links = {}
        for o in objects:
            require(o["rights_status"] == "LICENSE_UNCLEAR" and o["usage_scope"] == "PRIVATE_RESEARCH_ONLY"
                    and o.get("DIRECT_SCORE_AUTHORITY", False) is False
                    and isinstance(o["source_artifact_id"], str) and bool(o["source_artifact_id"])
                    and re.fullmatch(r"[0-9a-f]{64}", o["source_artifact_sha256"]), "Object rights/linkage invalid.")
            previous = source_links.setdefault(o["source_artifact_id"], o["source_artifact_sha256"])
            require(previous == o["source_artifact_sha256"], "Conflicting source identity linkage.")
            if o["object_type"] == "CROSS_BAND_PATTERN":
                require(o.get("EMPIRICAL_PATTERN_ONLY") is True and o.get("OFFICIAL_RUBRIC_AUTHORITY") is False
                        and o.get("DIRECT_SCORE_AUTHORITY") is False, "Pattern authority invalid.")
            features = o.get("claim_features", [])
            require(isinstance(features, list) and all(isinstance(f, str) for f in features), "Feature schema invalid.")
            if o.get("feature_type"):
                features = [o["feature_type"], *features]
            normalized.append(FrozenEvidenceObject(o["retrieval_id"], o["object_type"], o["criterion"],
                tuple(features), o["text"], o["source_artifact_sha256"], o["source_artifact_id"],
                o["rights_status"], o["usage_scope"], False))
        index = faiss.read_index(str(_safe_child(root, "dense.faiss")))
        require(type(index).__name__ == metadata["faiss_index_type"] == "IndexFlatIP"
                and index.d == 1024 and index.ntotal == 729 and index.metric_type == faiss.METRIC_INNER_PRODUCT,
                "Dense shape invalid.")
        # Reconstruct existing vectors, never re-embed/rebuild. Frozen line order is the ID mapping.
        vectors = index.reconstruct_n(0, 729)
        require(np.isfinite(vectors).all() and np.allclose(np.linalg.norm(vectors, axis=1), 1, atol=1e-4),
                "Dense normalization invalid.")
        distances, positions = index.search(vectors, 1)
        require(positions[:, 0].tolist() == list(range(729)) and np.allclose(distances[:, 0], 1, atol=1e-4),
                "Dense self-position linkage invalid.")
        lexical = RestrictedBM25Unpickler(io.BytesIO(_safe_child(root, "bm25_index.pkl").read_bytes())).load()
        require(lexical["version"] == 1 and tuple(lexical["doc_ids"]) == ids
                and len(lexical["tokenized_documents"]) == len(lexical["searchable_texts"]) == 729, "BM25 order invalid.")
        bm = lexical["bm25"]
        require(bm.corpus_size == 729 and bm.k1 == 1.5 and bm.b == 0.75 and bm.epsilon == 0.25,
                "BM25 runtime parameters invalid.")
        for i, o in enumerate(objects):
            terms = [o.get("criterion"), o.get("card_type"), o.get("control_profile"), *o.get("claim_features", [])]
            searchable = o["text"] + "\n" + " ".join(str(t) for t in terms if t and t != "None")
            tokens = bm25_tokenize(searchable)
            require(searchable == lexical["searchable_texts"][i] and tokens == lexical["tokenized_documents"][i]
                    and dict(Counter(tokens)) == bm.doc_freqs[i] and len(tokens) == bm.doc_len[i], "BM25 document linkage invalid.")
        handle = HistoricalRuntimeHandle(authorized, before, index, bm)
        handle.assert_unchanged()
        content_hash = digest({"manifest": _sha256_bytes(authorized.manifest_bytes),
                               "artifacts": _thaw(m["artifacts"])})
        validated = ValidatedFrozenPackage(m["package_id"], m["package_version"], content_hash,
            authorized.discovery_decision_sha256, {o.object_id: o for o in normalized}, ids, QWEN_PIN,
            digest({"content": content_hash, "order": ids, "adapter": "historical-private-runtime-v1"}))
        return RuntimeValidationOutcome(RuntimeValidationStatus.VALIDATED, None, validated, tuple(checked), True, handle)
    except ImportError:
        return failed(RagDisabledReason.DENSE_RUNTIME_UNAVAILABLE)
    except (OSError, ValueError, TypeError, KeyError, AttributeError, pickle.UnpicklingError):
        return failed(RagDisabledReason.RUNTIME_ARTIFACT_INVALID)
