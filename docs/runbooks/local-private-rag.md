# Local/private frozen RAG runtime

This is a separate opt-in capability, not a change to the default public target
policy or the historical C1 acceptance decision. Keep the release and generated
private user reports outside tracked/public artifacts. Do not run upstream build
or release-validation scripts: some write back into the frozen release.

## Local configuration

Install the optional query/runtime dependencies from `requirements-rag-local.txt`
into a local virtual environment. The model cache must already contain
Qwen/Qwen3-Embedding-0.6B revision
`97b0c614be4d77ee51c0cef4e5f07c00f9eb65b3`. Runtime loading is offline, does not
trust remote code and never regenerates corpus vectors. FAISS and the Torch query
encoder run in separate processes to avoid conflicting macOS OpenMP libraries.
Do not set `KMP_DUPLICATE_LIB_OK` to conceal a native runtime conflict.

Supply these variables using ignored local configuration:

- `EMPIRICAL_RAG_PACKAGE_PATH`: externally mounted, immutable package root.
- `IELTS_TARGET_ENVIRONMENT=LOCAL_PRIVATE_RESEARCH`.
- `EMPIRICAL_RAG_LOCAL_PRIVATE_AUTHORIZED=1`: explicit operator private-use consent.
- `EMPIRICAL_RAG_MODEL_CACHE`: local fixed-revision model cache, never the corpus root.

This workspace's operator-specific configuration is in the ignored
`.scratch/local-rag/runtime.env`; it is not a public configuration template.

## Product integration

The local browser uses `BrowserRuntime` and task-specific foundation adapters. The current assessment flow is Rubric initial assessment → independent RAG audit → absolute overall difference → conditional fresh retrieval and Rubric re-score → final lock → teaching report → authorized persistence and PDF. ADR 0020 supersedes the older post-lock-only timing for this flow. The initial assessment cannot receive RAG, target-band or learner-memory context. The independent audit cannot receive the initial bands or target. Threshold policy is tested in `tests/test_rag_calibration_audit.py`.

Task 2 uses the validated immutable dense/BM25/RRF package. Task 1 uses the separately pinned, attributed commentary sidecar described in `docs/audits/task1-calibration-coverage.md`; the original package contains no TA. Both preserve evidence and authority gates. Configured but incomplete audits return review required and never publish a normal score report. A missing configuration is recorded as unavailable, not as passed calibration.

Post-lock retrieval for teaching remains a separate operation. It cannot mutate the LockedScoreSnapshot. This distinction matters when interpreting historical C1/C3 tests, whose injected foundation already supplies a locked score and therefore verifies only post-lock integration.

Runtime enabled does not mean a particular query has enough evidence. Browser projections distinguish runtime availability and report-specific calibration, and disclose no package paths, corpus object IDs, raw retrieval scores or credentials.

Close `service.rag_runtime` at application shutdown. Changing the package path or private authorization revokes retrieval. Restart with valid configuration to reload.

## Reproducible validation

From the repository root:

```sh
source .scratch/local-rag/runtime.env
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python scripts/validate_local_private_rag.py
RUN_REAL_PRIVATE_RAG_TESTS=1 PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m unittest tests.test_local_private_rag -v
```

The first command prints only safe fixture metrics. The second tests the real
package using read interception for tampering; it never edits or copies frozen
files. Without explicit test opt-in, real-package tests skip rather than discover
a private default path. Corpus similarity is never an IELTS scoring authority;
the same LockedScoreSnapshot is preserved with RAG enabled/disabled or failing.

## Separate public gate

`LOCAL_PRIVATE_RESEARCH_AUTHORIZED` means only the declared private-use scope.
The package remains `LICENSE_UNCLEAR / PRIVATE_RESEARCH_ONLY` with commercial
authorization false. `PUBLIC_SAFE_AUTHORIZED` remains false. Technical validation
and upstream retrieval promotion are not public/commercial rights grants, nor
evidence of examiner-equivalent scoring or measured coaching utility.
