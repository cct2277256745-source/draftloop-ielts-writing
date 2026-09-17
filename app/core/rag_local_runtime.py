"""Explicit local/private runtime assembly; never enabled by public target policy."""
from __future__ import annotations

import os
import json
from pathlib import Path
import select
import subprocess
import sys
import threading
from typing import Mapping

from .rag_historical import (
    LOCAL_PRIVATE_RESEARCH, LOCAL_PRIVATE_RESEARCH_AUTHORIZED, PRIVATE_AUTH_ENV,
    QWEN_PIN, bm25_tokenize,
)
from .rag_package import (
    PACKAGE_ENVIRONMENT_VARIABLE, TARGET_ENVIRONMENT_VARIABLE, RagUsagePolicy,
    RagDisabledReason, discover_frozen_package,
    validate_frozen_runtime,
)
from .rag_retrieval import RankedCandidate, RetrievalProvider, RetrievalProviderError

MODEL_CACHE_ENV = "EMPIRICAL_RAG_MODEL_CACHE"


class InProcessQwenEncoder:
    """CPU-only fixed model revision, offline loading, query encoding only."""

    pin = QWEN_PIN

    def __init__(self, cache_folder=None):
        from sentence_transformers import SentenceTransformer
        self.model = SentenceTransformer(
            self.pin.model_id, revision=self.pin.revision, device="cpu",
            cache_folder=cache_folder, local_files_only=True, trust_remote_code=False,
            token=False,
        )
        self.model.max_seq_length = 512
        if self.model.get_sentence_embedding_dimension() != self.pin.dimension:
            raise ValueError("Embedding model dimension mismatch.")
        # Loading is not sufficient: prove query runtime shape and normalization.
        self.encode("Explain a writing claim with relevant supporting evidence.")

    def encode(self, query):
        import numpy as np
        result = self.model.encode([query], prompt=self.pin.query_prompt, batch_size=1,
            normalize_embeddings=True, convert_to_numpy=True, show_progress_bar=False)
        result = np.ascontiguousarray(result, dtype=np.float32)
        if result.shape != (1, 1024) or not np.isfinite(result).all() \
                or not np.allclose(np.linalg.norm(result, axis=1), 1, atol=1e-4):
            raise RetrievalProviderError("Embedding query runtime incompatible.")
        return result


class PinnedQwenEncoder:
    """Isolate Torch's OpenMP from FAISS's native runtime; no unsafe OMP override."""

    pin = QWEN_PIN

    def __init__(self, cache_folder=None):
        env = dict(os.environ)
        env.update(HF_HUB_OFFLINE="1", TRANSFORMERS_OFFLINE="1", TOKENIZERS_PARALLELISM="false")
        env.pop("KMP_DUPLICATE_LIB_OK", None)
        self.lock = threading.Lock()
        self.process = subprocess.Popen(
            [sys.executable, "-m", "app.core.rag_query_worker", cache_folder or ""],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            text=True, bufsize=1, env=env, cwd=str(Path(__file__).resolve().parents[2]),
        )
        try:
            if self._response(60) != {"ready": True, "pin": self.pin.content()}:
                raise RuntimeError("Pinned query worker startup failed.")
        except Exception:
            self.close()
            raise

    def _response(self, timeout):
        if not select.select([self.process.stdout], [], [], timeout)[0]:
            self.close()
            raise RuntimeError("Pinned query worker timed out.")
        line = self.process.stdout.readline(100000)
        if not line.endswith("\n"):
            raise RuntimeError("Invalid query worker response.")
        return json.loads(line)

    def encode(self, query):
        import numpy as np
        with self.lock:
            if self.process.poll() is not None:
                raise RuntimeError("Pinned query worker unavailable.")
            self.process.stdin.write(json.dumps({"query": query}) + "\n")
            self.process.stdin.flush()
            response = self._response(15)
            if not isinstance(response, dict) or "vector" not in response:
                raise RetrievalProviderError("Isolated query encoding failed.")
            result = np.ascontiguousarray(response["vector"], dtype=np.float32)
            if result.shape != (1, 1024) or not np.isfinite(result).all() \
                    or not np.allclose(np.linalg.norm(result, axis=1), 1, atol=1e-4):
                raise RetrievalProviderError("Invalid isolated embedding result.")
            return result

    def close(self):
        if self.process.poll() is None:
            self.process.kill()
            self.process.wait(timeout=5)
        for stream in (self.process.stdin, self.process.stdout):
            if stream:
                stream.close()


class PrivateRetrievalProvider(RetrievalProvider):
    def __init__(self, package, handle, encoder, authorized_now):
        if encoder.pin != package.embedding_pin:
            raise RetrievalProviderError("Embedding pin mismatch.")
        self.handle, self.encoder, self.authorized_now = handle, encoder, authorized_now
        self.lock = threading.Lock()
        super().__init__(package, dense_search=self.dense, bm25_search=self.lexical)

    def dense(self, query, limit):
        scores, positions = self.handle.index.search(self.encoder.encode(query), limit)
        return [RankedCandidate(self._package.object_order[int(p)], float(s))
                for s, p in zip(scores[0], positions[0]) if p >= 0]

    def lexical(self, query, limit):
        import numpy as np
        scores = self.handle.bm25.get_scores(bm25_tokenize(query))
        positive = np.flatnonzero(scores > 0)
        positions = positive[np.argsort(-scores[positive], kind="stable")][:limit]
        return [RankedCandidate(self._package.object_order[int(p)], float(scores[p])) for p in positions]

    def retrieve(self, query, criterion=None, top_k=5):
        with self.lock:
            if not self.authorized_now():
                raise RetrievalProviderError("Private runtime authorization is no longer active.")
            self.handle.assert_unchanged()
            result = super().retrieve(query, criterion, top_k)
            self.handle.assert_unchanged()
            if not self.authorized_now():
                raise RetrievalProviderError("Private runtime authorization was revoked during retrieval.")
            return result


class LocalRagRuntime:
    """Explicit environment opt-in; no hardcoded package or model-cache location."""

    def __init__(self, environ: Mapping[str, str] | None = None, *, encoder_factory=PinnedQwenEncoder):
        self.environ = os.environ if environ is None else environ
        self.provider = None
        self.validation = None
        self.reason = None
        self.discovery = None
        self._keys = (PACKAGE_ENVIRONMENT_VARIABLE, TARGET_ENVIRONMENT_VARIABLE, PRIVATE_AUTH_ENV, MODEL_CACHE_ENV)
        self._configuration = tuple(self.environ.get(k) for k in self._keys)
        if not self.environ.get(PACKAGE_ENVIRONMENT_VARIABLE):
            self.reason = RagDisabledReason.PACKAGE_NOT_CONFIGURED.value
            return
        if self.environ.get(TARGET_ENVIRONMENT_VARIABLE) != LOCAL_PRIVATE_RESEARCH:
            self.reason = RagDisabledReason.TARGET_ENVIRONMENT_UNAUTHORIZED.value
            return
        if self.environ.get(PRIVATE_AUTH_ENV) != "1":
            self.reason = RagDisabledReason.RIGHTS_UNAUTHORIZED.value
            return
        policy = RagUsagePolicy(LOCAL_PRIVATE_RESEARCH, "assessment-calibration", False)
        self.discovery = discover_frozen_package(
            configured_path=self.environ[PACKAGE_ENVIRONMENT_VARIABLE], usage_policy=policy)
        if self.discovery.authorized_package is None:
            self.reason = self.discovery.decision.reason.value
            return
        if self.discovery.authorized_package.authorization_scope != LOCAL_PRIVATE_RESEARCH_AUTHORIZED:
            self.reason = RagDisabledReason.RIGHTS_UNAUTHORIZED.value
            return
        self.validation = validate_frozen_runtime(self.discovery.authorized_package,
            runtime_embedding=QWEN_PIN, dense_inspector=None)
        if self.validation.validated_package is None:
            self.reason = self.validation.reason.value
            return
        try:
            encoder = encoder_factory(self.environ.get(MODEL_CACHE_ENV))
            self.provider = PrivateRetrievalProvider(self.validation.validated_package,
                self.validation.runtime_handle, encoder, self.authorized_now)
        except Exception:
            # Missing/offline model or native runtime errors are never public diagnostics with paths.
            self.reason = RagDisabledReason.EMBEDDING_RUNTIME_INCOMPATIBLE.value

    def authorized_now(self):
        return self.reason is None and tuple(self.environ.get(k) for k in self._keys) == self._configuration

    def retrieve_calibration(self, query, criterion, top_k=5, *, task_type='task2'):
        if self.provider is None or not self.authorized_now():
            raise RetrievalProviderError('Calibration reference authorization is not active.')
        if task_type=='task2':
            return self.provider.retrieve(query,criterion,top_k)
        if task_type!='task1':
            raise RetrievalProviderError('Unknown calibration task.')
        # The frozen package has no TA. Use a separately pinned Task 1 source
        # for all four criteria; never relabel Task Response or mix task evidence.
        from .task1_calibration_references import retrieve_task1_references
        self.validation.runtime_handle.assert_unchanged()
        result=retrieve_task1_references(query,criterion,top_k)
        if not self.authorized_now():
            raise RetrievalProviderError('Calibration reference authorization was revoked.')
        return result

    def close(self):
        if self.provider and hasattr(self.provider.encoder, "close"):
            self.provider.encoder.close()

    def projection(self):
        reason = self.reason
        if not self.environ.get(PACKAGE_ENVIRONMENT_VARIABLE):
            reason = RagDisabledReason.PACKAGE_NOT_CONFIGURED.value
        elif not self.authorized_now():
            reason = reason or "LOCAL_AUTHORIZATION_CHANGED"
        if reason is None and self.provider is not None:
            try:
                self.validation.runtime_handle.assert_unchanged()
            except Exception:
                reason = RagDisabledReason.PACKAGE_MUTATION_DETECTED.value
            process = getattr(self.provider.encoder, "process", None)
            if process is not None and process.poll() is not None:
                reason = "QUERY_RUNTIME_UNAVAILABLE"
        enabled = reason is None and self.provider is not None
        return {
            "state": "RAG_ENABLED" if enabled else "RAG_DISABLED",
            "package": self.validation.validated_package.package_id if enabled else None,
            "packageVersion": self.validation.validated_package.package_version if enabled else None,
            "mode": LOCAL_PRIVATE_RESEARCH,
            "capability": LOCAL_PRIVATE_RESEARCH_AUTHORIZED if enabled else None,
            "publicSafeAuthorized": False,
            "commercialAuthorized": False,
            "directScoreAuthority": False,
            "reason": None if enabled else reason or "RUNTIME_NOT_VALIDATED",
            "labelZh": "经验参考证据已启用 · 仅限本地私有研究" if enabled else "经验参考证据未启用",
        }
